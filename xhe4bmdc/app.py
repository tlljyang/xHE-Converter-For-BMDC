"""xHE Converter for BMDC — Gradio UI (转码 / 日志 / 预设)."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path

import gradio as gr

from xhe4bmdc import APP_NAME, __version__
from xhe4bmdc import deps as depsmod
from xhe4bmdc.engine import command as cmdmod
from xhe4bmdc.engine.models import (AUDIO_CODECS, CHANNEL_MODES, CONTAINERS,
                                    HWACCEL_FORMS, SAMPLE_RATES,
                                    AudioOptions, ExportOptions, VideoOptions,
                                    job_row_headers)
from xhe4bmdc.engine.naming import apply_rename
from xhe4bmdc.engine.probe import audio_summary
from xhe4bmdc.engine.queue import Engine
from xhe4bmdc.engine.store import Store
from xhe4bmdc.hwprobe.detect import HwProbeResult, probe_hwaccels

CSS = """
#src-audio-info {background: rgba(59,130,246,.08); border-radius: 8px; padding: 10px 14px;}
.badge-xhe {color:#16a34a; font-weight:700;}
.badge-no {color:#94a3b8;}
footer {display:none !important;}
"""

_engine: Engine | None = None
_engine_lock = threading.Lock()
_hw_probe: HwProbeResult | None = None
_hw_probe_lock = threading.Lock()


def get_engine() -> Engine | None:
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            d = depsmod.resolve()
            if not d.ok:
                return None
            _engine = Engine(d.ffmpeg, d.ffprobe, d.xaacdec, Store())
        return _engine


def get_hw_probe() -> HwProbeResult:
    """Probe once (cached); runs in caller thread on first use."""
    global _hw_probe
    with _hw_probe_lock:
        if _hw_probe is None:
            eng = get_engine()
            if eng is None:
                return HwProbeResult()
            _hw_probe = probe_hwaccels(eng.ffmpeg, eng.encoders)
        return _hw_probe


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fmt_dur(sec: float) -> str:
    if sec <= 0:
        return "-"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _codec_labels() -> list[str]:
    return [v["label"] for v in AUDIO_CODECS.values()]


def _label_to_key(label: str) -> str:
    for k, v in AUDIO_CODECS.items():
        if v["label"] == label:
            return k
    return "aac"


def _container_labels() -> list[str]:
    return [v["label"] for v in CONTAINERS.values()]


def _container_label_to_key(label: str) -> str:
    for k, v in CONTAINERS.items():
        if v["label"] == label:
            return k
    return "mp4"


def _channel_labels() -> list[str]:
    return list(CHANNEL_MODES.values())


def _channel_label_to_key(label: str) -> str:
    for k, v in CHANNEL_MODES.items():
        if v == label:
            return k
    return "auto"


FILE_HEADERS = ["包含", "文件", "xHE", "原音频", "时长", "状态"]
_RENAME_HEADERS = ["原文件名", "输出文件名"]


# ---------------------------------------------------------------------------
# state helpers — file rows are kept in session state as list[dict]
# ---------------------------------------------------------------------------

def _files_table_rows(files: list[dict]) -> list[list]:
    rows = []
    for f in files:
        rows.append([bool(f["selected"]), f["display"],
                     "✅ xHE" if f["is_xhe"] else "—",
                     f["audio_summary"], _fmt_dur(f["duration"]), f["status"]])
    return rows


def _selected(files: list[dict]) -> list[dict]:
    return [f for f in files if f["selected"]]


def _probe_new(paths: list[tuple[str, str]], existing: list[dict]) -> list[dict]:
    eng = get_engine()
    out = list(existing)
    seen = {f["path"] for f in out}
    for path, display in paths:
        if path in seen:
            continue
        seen.add(path)
        if eng is None:
            out.append({"path": path, "display": display, "is_xhe": False,
                        "audio_summary": "依赖未配置，无法探测", "duration": 0,
                        "selected": False, "status": "未探测"})
            continue
        pr = eng.probe(path)
        if pr.error:
            out.append({"path": path, "display": display, "is_xhe": False,
                        "audio_summary": f"探测失败: {pr.error}", "duration": 0,
                        "selected": False, "status": "❌ 探测失败"})
        else:
            out.append({"path": path, "display": display, "is_xhe": pr.is_xhe,
                        "audio_summary": audio_summary(pr),
                        "duration": pr.duration,
                        "selected": pr.is_xhe,   # xHE 自动选中
                        "status": "🟢 xHE，已自动选中" if pr.is_xhe else "非 xHE（可手动勾选）"})
    return out


# ---------------------------------------------------------------------------
# UI construction
# ---------------------------------------------------------------------------

def build_app() -> gr.Blocks:
    eng = get_engine()

    with gr.Blocks(title=APP_NAME) as demo:
        gr.Markdown(f"## {APP_NAME} `v{__version__}`  \n批量转换 BMD Camera 的 xHE-AAC 音频为剪辑友好的格式")
        main_tabs = gr.Tabs()

        # ========================= 依赖缺失配置区 =========================
        dep_group_visible = eng is None
        with gr.Group(visible=dep_group_visible) as dep_group:
            gr.Markdown("### ⚠️ 未检测到有效依赖\n请填写本机 FFmpeg / xaacdec 路径（也可在程序目录的 dependencies.yaml 中配置）")
            with gr.Row():
                dep_ffmpeg = gr.Textbox(label="ffmpeg 路径", placeholder=r"C:\Tools\ffmpeg\bin\ffmpeg.exe", scale=2)
                dep_ffprobe = gr.Textbox(label="ffprobe 路径", placeholder=r"C:\Tools\ffmpeg\bin\ffprobe.exe", scale=2)
                dep_xaacdec = gr.Textbox(label="xaacdec 路径（处理 xHE 必需）", placeholder=r"C:\Tools\xaacdec\xaacdec.exe", scale=2)
            dep_browse_ffmpeg = gr.Button("浏览 ffmpeg…", size="sm")
            dep_save = gr.Button("保存并重载依赖", variant="primary")
            dep_status = gr.Textbox(label="状态", interactive=False)

        # ============================ 转码页 ============================
        with main_tabs:
            with gr.Tab("转码", id="transcode"):
                files_state = gr.State([])
                rename_state = gr.State({})     # display -> output name
                extra_state = gr.State([])      # passthrough tokens

                with gr.Row():
                    upload = gr.File(label="拖入或选择视频文件（可多选、可多次添加）",
                                     file_count="multiple", file_types=["video", ".mp4", ".mov", ".mkv", ".m4v"])
                    with gr.Column(scale=1):
                        path_input = gr.Textbox(label="或输入本地文件/文件夹路径（分号分隔，文件夹会展开）",
                                                placeholder=r"E:\desktop\B001_clips; E:\video\a.mp4")
                        path_add = gr.Button("添加路径", size="sm")
                files_table = gr.Dataframe(headers=FILE_HEADERS,
                                           datatype=["bool", "str", "str", "str", "str", "str"],
                                           interactive=True, wrap=True,
                                           column_count=(len(FILE_HEADERS), "fixed"))

                gr.Markdown("### 音频参数")
                with gr.Group():
                    gr.Markdown('<span style="opacity:.75">上半：原音频信息</span>')
                    src_audio_info = gr.HTML(value="<i>选择文件后显示</i>", elem_id="src-audio-info")
                    gr.Markdown('<span style="opacity:.75">下半：输出设置</span>')
                    with gr.Row():
                        a_codec = gr.Dropdown(choices=_codec_labels(), value=AUDIO_CODECS["aac"]["label"], label="格式")
                        a_rate = gr.Dropdown(choices=["自动（与源一致）"] + [f"{r} Hz" for r in SAMPLE_RATES],
                                             value="自动（与源一致）", label="采样率")
                        a_depth = gr.Dropdown(choices=["跟随编码器", "16-bit", "24-bit"],
                                              value="跟随编码器", label="采样深度")
                    with gr.Row():
                        a_bitrate = gr.Slider(32, 512, value=256, step=16, label="码率 (kbps, CBR)")
                        a_channels = gr.Dropdown(choices=_channel_labels(),
                                                  value=CHANNEL_MODES["auto"], label="声道")
                        a_gain = gr.Slider(-24, 24, value=0, step=0.5, label="增益 (dB)")

                with gr.Row():
                    container = gr.Dropdown(choices=_container_labels(),
                                            value=CONTAINERS["mp4"]["label"], label="视频封装格式")
                    v_mode = gr.Dropdown(choices=["copy（不重编码视频）", "H.264", "H.265/HEVC", "AV1", "VP9"],
                                         value="copy（不重编码视频）", label="视频编码")
                    v_preset = gr.Dropdown(choices=["ultrafast", "superfast", "veryfast", "faster",
                                                    "fast", "medium", "slow", "slower"],
                                           value="medium", label="软件编码 preset", visible=False)

                with gr.Row():
                    hw_enabled = gr.Checkbox(value=False, label="启用硬件加速")
                    hw_form = gr.Dropdown(choices=["软件编码"], value="软件编码", label="加速形式（启动时探测）")
                    hw_info = gr.Textbox(label="GPU 兼容性", interactive=False, scale=2)

                with gr.Row():
                    out_dir = gr.Textbox(label="输出目录（可手写）", scale=3)
                    out_browse = gr.Button("浏览…", size="sm")

                with gr.Row():
                    btn_rename = gr.Button("📋 重命名面板")
                    btn_extra = gr.Button("⚡ 透传 FFmpeg 参数")
                    btn_preset_save = gr.Button("💾 保存为预设")

                # ---- 重命名面板（弹出层） ----
                with gr.Group(visible=False) as rename_panel:
                    gr.Markdown("### 批量重命名（输出文件名）")
                    rename_df = gr.Dataframe(headers=_RENAME_HEADERS,
                                             datatype=["str", "str"], interactive=True,
                                             column_count=(2, "fixed"), wrap=True)
                    with gr.Row():
                        rename_save = gr.Button("保存退出", variant="primary")
                        rename_close = gr.Button("直接退出")

                # ---- 透传参数面板 ----
                with gr.Group(visible=False) as extra_panel:
                    gr.Markdown("### 透传 FFmpeg 参数（全局，追加在引擎参数之后、输出文件之前）")
                    extra_txt = gr.Textbox(label="参数", placeholder="-vf settb=AVTB -max_muxing_queue_size 1024")
                    gr.Markdown('<span style="opacity:.6">与引擎生成的参数冲突时以你填写的为准；已知冲突会在日志中标注</span>')
                    with gr.Row():
                        extra_save = gr.Button("保存退出", variant="primary")
                        extra_close = gr.Button("直接退出")

                # ---- 保存预设弹层 ----
                with gr.Group(visible=False) as preset_panel:
                    preset_name = gr.Textbox(label="预设名称")
                    with gr.Row():
                        preset_confirm = gr.Button("保存", variant="primary")
                        preset_cancel = gr.Button("取消")

                with gr.Row():
                    parallel = gr.Slider(1, 4, value=2, step=1, label="并行导出数")
                    btn_export = gr.Button("🚀 开始导出", variant="primary", scale=2)
                    btn_cancel_all = gr.Button("取消全部", variant="stop")
                export_status = gr.Textbox(label="导出状态", interactive=False, lines=3)

            # ============================ 日志页 ============================
            with gr.Tab("日志", id="logs"):
                jobs_table = gr.Dataframe(headers=job_row_headers(),
                                          datatype=["str"] * len(job_row_headers()),
                                          interactive=False, wrap=True)
                with gr.Row():
                    job_selector = gr.Dropdown(choices=[], label="选择任务查看日志")
                    btn_refresh_logs = gr.Button("刷新")
                    btn_retry = gr.Button("🔁 重试所选", variant="primary")
                    btn_cancel_job = gr.Button("取消所选")
                    btn_clear_done = gr.Button("清理已完成")
                log_view = gr.Textbox(label="任务日志（尾部）", lines=18, interactive=False)

            # ============================ 预设页 ============================
            with gr.Tab("预设", id="presets"):
                preset_list = gr.Radio(choices=[], label="已保存的预设（点击选中后加载）")
                with gr.Row():
                    btn_load_preset = gr.Button("加载预设并返回转码页", variant="primary")
                    btn_delete_preset = gr.Button("删除预设", variant="stop")
                    btn_refresh_presets = gr.Button("刷新")
                preset_status = gr.Textbox(label="状态", interactive=False)

        refresh_timer = gr.Timer(2.0)

        # ------------------------------------------------------------------
        # handlers
        # ------------------------------------------------------------------

        def _dep_browse() -> str:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
            p = filedialog.askopenfilename(title="选择 ffmpeg",
                                           filetypes=[("ffmpeg", "ffmpeg*"), ("所有文件", "*.*")])
            root.destroy()
            return p or ""

        dep_browse_ffmpeg.click(_dep_browse, outputs=dep_ffmpeg)

        def _dep_save(ff: str, fp: str, xa: str) -> str:
            global _engine
            d = depsmod.resolve(overrides={k: v for k, v in
                                           {"ffmpeg": ff, "ffprobe": fp, "xaacdec": xa}.items() if v})
            if not d.ok:
                return "❌ 仍有问题:\n" + "\n".join(d.problems)
            yml = depsmod.program_dir() / depsmod.DEPS_FILENAME
            try:
                depsmod.save_yaml_config(yml, {"ffmpeg": d.ffmpeg, "ffprobe": d.ffprobe,
                                               "xaacdec": d.xaacdec})
            except OSError:
                pass
            with _engine_lock:
                _engine = Engine(d.ffmpeg, d.ffprobe, d.xaacdec, Store())
            return f"✅ 依赖已配置 ffmpeg={d.ffmpeg}"

        dep_save.click(_dep_save, inputs=[dep_ffmpeg, dep_ffprobe, dep_xaacdec],
                       outputs=dep_status)

        # ---- 文件导入 ----
        def _expand_paths(text: str) -> list[tuple[str, str]]:
            pairs: list[tuple[str, str]] = []
            for chunk in text.replace(";", "\n").splitlines():
                chunk = chunk.strip().strip('"')
                if not chunk:
                    continue
                p = Path(chunk)
                if p.is_dir():
                    for ext in ("*.mp4", "*.mov", "*.m4v", "*.mkv", "*.avi", "*.webm", "*.mts", "*.m2ts"):
                        for f in sorted(p.glob(ext)):
                            pairs.append((str(f.resolve()), f.name))
                elif p.exists():
                    pairs.append((str(p.resolve()), p.name))
            return pairs

        def _add_uploads(files, files_st):
            pairs = []
            for f in files or []:
                name = getattr(f, "name", None) or str(f)
                orig = getattr(f, "orig_name", None) or Path(name).name
                pairs.append((name, Path(orig).name))
            return _after_import(pairs, files_st)

        def _add_paths(text, files_st):
            return _after_import(_expand_paths(text), files_st)

        def _after_import(pairs, files_st) -> tuple:
            files = _probe_new(pairs, files_st or [])
            return (_files_table_rows(files), files,
                    _first_selected_html(files))

        def _first_selected_html(files: list[dict]) -> str:
            sel = _selected(files)
            if not sel:
                return "<i>未选择文件</i>"
            f = sel[0]
            extra = ""
            if len(sel) > 1:
                extra = f'<div style="opacity:.7">（共选中 {len(sel)} 个文件，上方显示第 1 个）</div>'
            return f"<div>{f['audio_summary']}</div>{extra}"

        upload.upload(_add_uploads, inputs=[upload, files_state],
                      outputs=[files_table, files_state, src_audio_info])
        path_add.click(_add_paths, inputs=[path_input, files_state],
                       outputs=[files_table, files_state, src_audio_info],
                       api_name="add_paths")

        # ---- 选择联动：非 xHE 不可选中 ----
        def _table_changed(rows, files_st):
            files = list(files_st or [])
            if not files:
                return gr.update(), files, "<i>未选择文件</i>"
            clamped = False
            for row, f in zip(rows or [], files):
                want = bool(row[0])
                if want and not f["is_xhe"] and not f.get("manual_unlock"):
                    want = False      # 非 xHE 默认锁定
                    clamped = True
                f["selected"] = want
                f["manual_unlock"] = f.get("manual_unlock", False)
            out_rows = _files_table_rows(files)
            if clamped:
                out_rows = out_rows  # rows already reflect enforced False
            return out_rows, files, _first_selected_html(files)

        files_table.input(_table_changed, inputs=[files_table, files_state],
                          outputs=[files_table, files_state, src_audio_info])

        # ---- 选项收集 ----
        def _gather(files_st, codec, rate, depth, bitrate, channels, gain,
                    cont, vmode, vpreset, hw_on, hw_sel, extra_st) -> ExportOptions:
            opts = ExportOptions(
                audio=AudioOptions(
                    codec=_label_to_key(codec),
                    sample_rate=0 if rate.startswith("自动") else int(rate.replace(" Hz", "")),
                    bit_depth=0 if depth.startswith("跟随") else int(depth.split("-")[0]),
                    bitrate_kb=int(bitrate),
                    channels=_channel_label_to_key(channels),
                    gain_db=float(gain)),
                video=VideoOptions(
                    mode="copy" if vmode.startswith("copy") else
                         {"H.264": "h264", "H.265/HEVC": "hevc", "AV1": "av1", "VP9": "vp9"}.get(vmode, "copy"),
                    hwaccel="none",
                    preset=vpreset or "medium"),
                container=_container_label_to_key(cont),
                extra_ffmpeg_args=list(extra_st or []))
            if hw_on and hw_sel in HWACCEL_FORMS and hw_sel != "none":
                opts.video.hwaccel = hw_sel
            _ = files_st
            return opts

        # ---- 重命名面板 ----
        def _open_rename(files_st):
            sel = _selected(files_st or [])
            rows = [[f["display"], f["display"]] for f in sel]
            if not rows:
                rows = [["（未选择文件）", ""]]
            return gr.update(visible=True), rows

        btn_rename.click(_open_rename, inputs=files_state,
                         outputs=[rename_panel, rename_df])

        def _save_rename(rows, rename_st):
            m = dict(rename_st or {})
            for row in rows or []:
                if len(row) >= 2 and row[0] and not str(row[0]).startswith("（"):
                    m[str(row[0])] = apply_rename(str(row[0]), str(row[1] or row[0]))
            return gr.update(visible=False), m

        def _close_rename():
            return gr.update(visible=False)

        rename_save.click(_save_rename, inputs=[rename_df, rename_state],
                          outputs=[rename_panel, rename_state])
        rename_close.click(_close_rename, outputs=rename_panel)

        # ---- 透传参数面板 ----
        def _open_extra(extra_st):
            txt = " ".join(extra_st or [])
            return gr.update(visible=True), txt

        def _save_extra(txt):
            return gr.update(visible=False), cmdmod.split_arg_tokens(txt or "")

        btn_extra.click(_open_extra, inputs=extra_state, outputs=[extra_panel, extra_txt])
        extra_save.click(_save_extra, inputs=extra_txt, outputs=[extra_panel, extra_state])
        extra_close.click(lambda: gr.update(visible=False), outputs=extra_panel)

        # ---- 预设 ----
        def _open_preset_panel():
            return gr.update(visible=True)

        def _do_save_preset(name, files_st, codec, rate, depth, bitrate, channels, gain,
                            cont, vmode, vpreset, hw_on, hw_sel, extra_st, rename_st):
            if not (name or "").strip():
                return gr.update(visible=False), "❌ 请填写预设名称"
            opts = _gather(files_st, codec, rate, depth, bitrate, channels, gain,
                           cont, vmode, vpreset, hw_on, hw_sel, extra_st)
            eng = get_engine()
            if eng is None:
                return gr.update(visible=False), "❌ 依赖未配置"
            eng.store.save_preset(name.strip(), opts)
            return gr.update(visible=False), f"✅ 预设「{name.strip()}」已保存"

        btn_preset_save.click(_open_preset_panel, outputs=preset_panel)
        preset_confirm.click(_do_save_preset,
                             inputs=[preset_name, files_state, a_codec, a_rate, a_depth,
                                     a_bitrate, a_channels, a_gain, container, v_mode,
                                     v_preset, hw_enabled, hw_form, extra_state, rename_state],
                             outputs=[preset_panel, export_status])
        preset_cancel.click(lambda: gr.update(visible=False), outputs=preset_panel)

        # ---- 硬件加速 ----
        def _hw_toggle(on: bool):
            if not on:
                return gr.update(choices=["软件编码"], value="软件编码"), "已关闭硬件加速", gr.update(visible=False)
            res = get_hw_probe()
            labels = {form: f"{HWACCEL_FORMS[form]} ({'+'.join(res.available[form])})"
                      for form in res.forms()}
            if not labels:
                return (gr.update(choices=["软件编码"], value="软件编码"),
                        "❌ 未检测到可用硬件加速，将使用软件编码", gr.update(visible=False))
            first = next(iter(labels.values()))
            return (gr.update(choices=list(labels.values()), value=first),
                    res.label_summary(), gr.update(visible=False))

        hw_enabled.change(_hw_toggle, inputs=hw_enabled, outputs=[hw_form, hw_info, v_preset])

        def _vmode_changed(mode: str):
            return gr.update(visible=not mode.startswith("copy"))

        v_mode.change(_vmode_changed, inputs=v_mode, outputs=v_preset)

        # ---- 输出目录浏览 ----
        def _browse_dir():
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
            p = filedialog.askdirectory(title="选择输出目录")
            root.destroy()
            return p or ""

        out_browse.click(_browse_dir, outputs=out_dir)

        # ---- 导出 ----
        def _export(files_st, outdir, codec, rate, depth, bitrate, channels, gain,
                    cont, vmode, vpreset, hw_on, hw_sel, extra_st, rename_st, par):
            eng = get_engine()
            if eng is None:
                return "❌ 依赖未配置，请先在上方填写 ffmpeg 路径"
            sel = _selected(files_st or [])
            if not sel:
                return "❌ 请先选择至少一个文件"
            if not (outdir or "").strip():
                return "❌ 请指定输出目录"
            outp = Path(outdir.strip())
            if not outp.exists():
                try:
                    outp.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    return f"❌ 输出目录不可用: {exc}"
            opts = _gather(files_st, codec, rate, depth, bitrate, channels, gain,
                           cont, vmode, vpreset, hw_on, hw_sel, extra_st)
            project = uuid.uuid4().hex[:8]
            eng.parallelism = max(1, int(par))
            started = 0
            for f in sel:
                name = (rename_st or {}).get(f["display"]) or f["display"]
                job = eng.create_job(f["path"], project, opts, str(outp), output_name=name)
                eng.enqueue(job.id)
                started += 1
            return f"🚀 已开始导出 {started} 个文件 → {outp}（项目 {project}）"

        btn_export.click(_export,
                         inputs=[files_state, out_dir, a_codec, a_rate, a_depth, a_bitrate,
                                 a_channels, a_gain, container, v_mode, v_preset,
                                 hw_enabled, hw_form, extra_state, rename_state, parallel],
                         outputs=export_status, api_name="export")

        def _cancel_all():
            eng = get_engine()
            if eng:
                eng.cancel_all()
            return "已请求取消全部任务"

        btn_cancel_all.click(_cancel_all, outputs=export_status)

        # ---- 日志页 ----
        def _refresh_jobs():
            eng = get_engine()
            if eng is None:
                return [], gr.update(choices=[], value=None)
            jobs = eng.all_jobs()
            rows = [j.to_row() for j in jobs]
            choices = [f"{j.id} · {j.display_name} · {j.state}" for j in jobs]
            return rows, gr.update(choices=choices)

        def _refresh_log(sel):
            eng = get_engine()
            if eng is None or not sel:
                return ""
            jid = sel.split(" · ")[0]
            job = eng.get_job(jid)
            if job is None:
                return "任务不存在（可能已被清理）"
            text = eng.full_log(jid)
            head = (f"文件: {job.display_name}\n状态: {job.state}  进度: {job.progress*100:.0f}%"
                    f"  尝试: {job.attempts}\n输出: {job.output_path or '-'}\n"
                    + (f"错误: {job.error}\n" if job.error else "") + "—" * 40 + "\n")
            return head + text

        def _retry(sel):
            eng = get_engine()
            if eng is None or not sel:
                return "未选择任务"
            jid = sel.split(" · ")[0]
            job = eng.get_job(jid)
            if job and job.state == "running":
                return "任务运行中，无需重试"
            eng.retry(jid)
            return f"已重新排队 {jid}"

        def _cancel_one(sel):
            eng = get_engine()
            if eng is None or not sel:
                return "未选择任务"
            eng.cancel(sel.split(" · ")[0])
            return "已请求取消"

        def _clear_done():
            eng = get_engine()
            if eng:
                eng.store.clear_finished()
            return "已清理完成/取消的任务记录"

        refresh_timer.tick(_refresh_jobs, outputs=[jobs_table, job_selector])
        btn_refresh_logs.click(_refresh_jobs, outputs=[jobs_table, job_selector])
        job_selector.change(_refresh_log, inputs=job_selector, outputs=log_view)
        btn_retry.click(_retry, inputs=job_selector, outputs=export_status)
        btn_cancel_job.click(_cancel_one, inputs=job_selector, outputs=export_status)
        btn_clear_done.click(_clear_done, outputs=export_status)

        # ---- 预设页 ----
        def _refresh_presets():
            eng = get_engine()
            if eng is None:
                return gr.update(choices=[], value=None)
            names = [p["name"] for p in eng.store.list_presets()]
            return gr.update(choices=names, value=None)

        def _load_preset(sel):
            eng = get_engine()
            if eng is None or not sel:
                return [gr.update()] * 11 + ["未选择预设"]
            for p in eng.store.list_presets():
                if p["name"] == sel:
                    o: ExportOptions = p["options"]
                    codec_label = AUDIO_CODECS[o.audio.codec]["label"]
                    rate = "自动（与源一致）" if not o.audio.sample_rate else f"{o.audio.sample_rate} Hz"
                    depth = {0: "跟随编码器", 16: "16-bit", 24: "24-bit"}.get(o.audio.bit_depth, "跟随编码器")
                    chans = CHANNEL_MODES.get(o.audio.channels, CHANNEL_MODES["auto"])
                    cont = CONTAINERS[o.container]["label"]
                    vmodes = {"copy": "copy（不重编码视频）", "h264": "H.264",
                              "hevc": "H.265/HEVC", "av1": "AV1", "vp9": "VP9"}
                    hw_on = o.video.hwaccel != "none"
                    return (codec_label, rate, depth, o.audio.bitrate_kb, chans,
                            o.audio.gain_db, cont, vmodes.get(o.video.mode, "copy（不重编码视频）"),
                            o.video.preset or "medium", hw_on, " ".join(o.extra_ffmpeg_args),
                            f"✅ 已加载预设「{sel}」")
            return [gr.update()] * 11 + ["预设不存在"]

        def _delete_preset(sel):
            eng = get_engine()
            if eng is None or not sel:
                return gr.update(), "未选择预设"
            eng.store.delete_preset(sel)
            names = [p["name"] for p in eng.store.list_presets()]
            return gr.update(choices=names, value=None), f"已删除「{sel}」"

        # note: loading also switches back to the transcode tab
        def _load_preset_and_switch(sel):
            updates = _load_preset(sel)
            tabs_update = gr.update(selected="transcode")
            return updates + [tabs_update]

        btn_refresh_presets.click(_refresh_presets, outputs=preset_list)
        demo.load(_refresh_presets, outputs=preset_list)
        btn_load_preset.click(_load_preset_and_switch, inputs=preset_list,
                              outputs=[a_codec, a_rate, a_depth, a_bitrate, a_channels,
                                       a_gain, container, v_mode, v_preset, hw_enabled,
                                       extra_txt, preset_status, main_tabs])
        btn_delete_preset.click(_delete_preset, inputs=preset_list,
                                outputs=[preset_list, preset_status])
    return demo


def main() -> None:
    demo = build_app()
    demo.launch(server_name="127.0.0.1", server_port=7860,
                inbrowser=True, show_error=True, css=CSS)


if __name__ == "__main__":
    main()
