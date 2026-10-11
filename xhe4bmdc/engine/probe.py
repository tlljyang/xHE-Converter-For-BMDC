"""ffprobe wrapper: media probing and xHE-AAC detection."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from xhe4bmdc.engine.models import AudioStreamInfo, ProbeResult, VideoStreamInfo


def _run_ffprobe(ffprobe: str, path: str, timeout: float = 60.0) -> dict:
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=_creationflags())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-500:] or "ffprobe failed")
    return json.loads(proc.stdout)


def _creationflags() -> int:
    # Windows: don't open a console window for the child process
    import sys
    if sys.platform == "win32":
        return 0x08000000  # CREATE_NO_WINDOW
    return 0


def probe_media(ffprobe: str, path: str) -> ProbeResult:
    """Probe one media file. Never raises; errors land in ProbeResult.error."""
    p = Path(path)
    result = ProbeResult(path=str(p.resolve()))
    try:
        if not p.is_file():
            raise FileNotFoundError(str(p))
        result.size = p.stat().st_size
        data = _run_ffprobe(ffprobe, str(p))
        fmt = data.get("format", {})
        result.container = (fmt.get("format_name") or "").split(",")[0]
        try:
            result.duration = float(fmt.get("duration") or 0.0)
        except ValueError:
            result.duration = 0.0

        # ffprobe >= 8 nests streams under "stream_groups"; older builds use "streams"
        streams: list[dict] = list(data.get("streams") or [])
        for group in data.get("stream_groups") or []:
            streams.extend(group.get("streams") or [])

        for s in streams:
            if s.get("codec_type") == "audio" and result.audio is None:
                result.audio = AudioStreamInfo(
                    index=int(s.get("index", 0)),
                    codec=s.get("codec_name") or "",
                    profile=s.get("profile") or "",
                    sample_rate=int(s.get("sample_rate") or 0),
                    channels=int(s.get("channels") or 0),
                    channel_layout=s.get("channel_layout") or "",
                    bit_rate=int(s.get("bit_rate") or 0),
                    duration=float(s.get("duration") or 0.0) or result.duration,
                )
            elif s.get("codec_type") == "video" and result.video is None \
                    and not (s.get("codec_name") or "").startswith("mjpeg"):
                # skip attached cover art
                if s.get("disposition", {}).get("attached_pic"):
                    continue
                fps = 0.0
                raw = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
                try:
                    num, _, den = raw.partition("/")
                    den_i = int(den or 1)
                    fps = float(num) / den_i if den_i else 0.0
                except (ValueError, ZeroDivisionError):
                    fps = 0.0
                color = s.get("color_transfer") or ""
                hdr = ""
                if "smpte2084" in color:
                    hdr = "HDR10"
                elif "arib-std-b67" in color:
                    hdr = "HLG"
                result.video = VideoStreamInfo(
                    index=int(s.get("index", 0)),
                    codec=s.get("codec_name") or "",
                    width=int(s.get("width") or 0),
                    height=int(s.get("height") or 0),
                    fps=round(fps, 3),
                    bit_rate=int(s.get("bit_rate") or 0),
                    duration=float(s.get("duration") or 0.0) or result.duration,
                    hdr=hdr,
                )
    except Exception as exc:  # noqa: BLE001 — surfaced in UI
        result.error = str(exc)
    return result


def audio_summary(pr: ProbeResult) -> str:
    """One-line human summary of the source audio, for the panel's top half."""
    if pr.error:
        return f"探测失败: {pr.error}"
    if pr.audio is None:
        return "无音频流"
    a = pr.audio
    rate = f"{a.sample_rate} Hz" if a.sample_rate else "未知采样率"
    ch = a.channel_layout or (f"{a.channels}ch" if a.channels else "未知声道")
    br = f"{a.bit_rate / 1000:.0f} kbps" if a.bit_rate else "未知码率"
    dur = f"{pr.duration:.2f} s" if pr.duration else "未知时长"
    return f"{a.codec.upper()} {('· ' + a.profile) if a.profile else ''} | {rate} | {ch} | {br} | {dur}"
