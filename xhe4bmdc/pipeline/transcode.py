"""Per-file transcode orchestration: decode → encode → verify → atomic commit."""
from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path

from xhe4bmdc.engine import command as cmdmod
from xhe4bmdc.engine.models import ExportOptions, Job, ProbeResult
from xhe4bmdc.engine.naming import sanitize_name, unique_output_path
from xhe4bmdc.engine.runner import CancelToken, RunResult, run_with_progress, verify_output
from xhe4bmdc.pipeline import xhe


class TranscodeError(RuntimeError):
    pass


class Transcoder:
    """Stateless per-file pipeline executor (one instance per job run)."""

    def __init__(self, ffmpeg: str, ffprobe: str, xaacdec: str,
                 encoders: set[str]) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.xaacdec = xaacdec
        self.encoders = encoders

    def run(self, job: Job, probe: ProbeResult, cancel: CancelToken,
            on_progress=None, on_stage=None, on_log=None) -> Path:
        """Execute the job; returns the final output path.

        on_progress(fraction), on_stage(str), on_log(line) are optional.
        Raises TranscodeError / XheDecodeError on failure, returns early on
        cancellation via CancelToken (raises TranscodeError('已取消')).
        """
        def stage(name: str) -> None:
            job.stage = name
            if on_stage:
                on_stage(name)

        def progress(frac: float) -> None:
            job.progress = max(0.0, min(1.0, frac))
            if on_progress:
                on_progress(job.progress)

        log_tail: deque[str] = deque(maxlen=4000)

        def log(line: str) -> None:
            log_tail.append(line)
            if on_log:
                on_log(line)

        out_dir = Path(job.output_dir)
        final_name = sanitize_name(job.output_name or Path(job.source_path).name)
        workdir = out_dir / ".xhe4bmdc-work" / job.id
        workdir.mkdir(parents=True, exist_ok=True)

        # Reserve unique final path (dedup _1/_2...) under engine reservation
        final_path = unique_output_path(out_dir, final_name)
        partial = workdir / ("partial" + final_path.suffix)
        job.output_path = str(final_path)

        # ---- stage 1: xHE decode (if needed)
        wav_path: Path | None = None
        if job.is_xhe:
            stage("decode")
            progress(0.01)
            if not self.xaacdec or not Path(self.xaacdec).exists():
                raise TranscodeError("未配置 xaacdec（xHE 解码器），无法处理 xHE 文件")
            raw, meta, au_count = xhe.build_raw_and_meta(
                job.source_path, workdir, self.ffprobe)
            log(f"[xhe] ASC+AUs 打包完成: {au_count} AUs")
            wav_path = workdir / "audio.wav"
            frames = xhe.run_xaacdec(self.xaacdec, raw, meta, wav_path)
            log(f"[xhe] 解码 {frames} 帧 -> {wav_path.name}")
            if cancel.cancelled:
                raise TranscodeError("已取消")
            progress(0.05)

        # ---- stage 2: ffmpeg encode/mux
        stage("encode")
        built = cmdmod.build_ffmpeg_cmd(
            job.options, probe, self.encoders,
            inputs=[job.source_path],
            output=str(partial),
            wav_audio_input=str(wav_path) if wav_path else None)
        for note in built.notes:
            log(f"[engine] {note}")
        if built.conflicts:
            log(f"[engine] 透传参数与引擎冲突: {', '.join(built.conflicts)}（引擎参数优先生效位置可能被覆盖）")
        log(f"[ffmpeg] {' '.join(built.args[:12])} ...")

        total = probe.duration or 0.0
        base_frac = 0.05 if wav_path else 0.0
        span = (0.95 - base_frac) if wav_path else 0.95

        def on_time(out_sec: float) -> None:
            if total > 0:
                progress(base_frac + span * min(1.0, out_sec / total))

        full_cmd = [self.ffmpeg] + built.args
        res = run_with_progress(full_cmd, total, cancel, log_tail,
                                on_time=on_time)
        if res.cancelled or cancel.cancelled:
            raise TranscodeError("已取消")
        if res.returncode != 0:
            tail = "\n".join(list(log_tail)[-25:])
            raise TranscodeError(f"ffmpeg 退出码 {res.returncode}\n{tail}")
        progress(0.95)

        # ---- stage 3: verify
        stage("verify")
        expect = probe.duration or 0.0
        ok, why = verify_output(self.ffprobe, partial, expect)
        if not ok:
            raise TranscodeError(f"输出验证失败: {why}")

        # ---- stage 4: atomic commit
        stage("commit")
        progress(0.99)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.exists():
            final_path = unique_output_path(out_dir, final_path.name)
            job.output_path = str(final_path)
        import shutil
        try:
            os.replace(partial, final_path)   # same volume: atomic
        except OSError:
            shutil.move(str(partial), str(final_path))  # cross-volume fallback
        shutil.rmtree(workdir, ignore_errors=True)
        progress(1.0)
        job.log_text = "\n".join(list(log_tail)[-60:])
        return final_path
