"""Core data model for xHE4BMDC.

All dataclasses are JSON-serialisable (via asdict) so they can be stored in
SQLite, embedded in presets, and round-tripped through the UI layer.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Probe results
# ---------------------------------------------------------------------------

@dataclass
class AudioStreamInfo:
    index: int
    codec: str                      # e.g. "aac"
    profile: str = ""               # e.g. "xHE-AAC"
    sample_rate: int = 0            # Hz
    channels: int = 0
    channel_layout: str = ""
    bit_rate: int = 0               # bits/s, 0 = unknown
    duration: float = 0.0           # seconds

    @property
    def is_xhe(self) -> bool:
        p = (self.profile or "").lower()
        c = (self.codec or "").lower()
        return "xhe" in p or "usac" in p or c in {"xheaac", "usac"}


@dataclass
class VideoStreamInfo:
    index: int
    codec: str
    width: int = 0
    height: int = 0
    fps: float = 0.0
    bit_rate: int = 0
    duration: float = 0.0
    hdr: str = ""                   # "" | "HDR10" | "HLG" | "Dolby Vision" ...


@dataclass
class ProbeResult:
    path: str
    container: str = ""             # mp4 / mov / matroska ...
    duration: float = 0.0
    size: int = 0
    audio: Optional[AudioStreamInfo] = None
    video: Optional[VideoStreamInfo] = None
    error: Optional[str] = None

    @property
    def is_xhe(self) -> bool:
        return self.audio is not None and self.audio.is_xhe


# ---------------------------------------------------------------------------
# Export options (everything preset-able lives here)
# ---------------------------------------------------------------------------

AUDIO_CODECS: dict[str, dict[str, Any]] = {
    # key: {label, ffmpeg encoder, kind, requires encoders, notes}
    "aac":        {"label": "AAC (LC)",            "encoder": "aac",          "kind": "lossy",  "lossless": False},
    "aac_he":     {"label": "HE-AAC (SBR)",        "encoder": "libfdk_aac",   "kind": "lossy",  "lossless": False,
                   "alt_encoder": "aac", "alt_profile": "aac_he"},
    "mp3":        {"label": "MP3 (lame)",          "encoder": "libmp3lame",   "kind": "lossy",  "lossless": False},
    "vorbis":     {"label": "Vorbis",              "encoder": "libvorbis",    "kind": "lossy",  "lossless": False},
    "opus":       {"label": "Opus",                "encoder": "libopus",      "kind": "lossy",  "lossless": False},
    "ac3":        {"label": "AC-3 (Dolby Digital)","encoder": "ac3",          "kind": "lossy",  "lossless": False},
    "eac3":       {"label": "E-AC-3 (DD+)",        "encoder": "eac3",         "kind": "lossy",  "lossless": False},
    "flac":       {"label": "FLAC (16-bit)",       "encoder": "flac",         "kind": "lossless", "lossless": True,  "bit_depth": 16},
    "flac24":     {"label": "FLAC (24-bit)",       "encoder": "flac",         "kind": "lossless", "lossless": True,  "bit_depth": 24},
    "alac":       {"label": "ALAC (16-bit)",       "encoder": "alac",         "kind": "lossless", "lossless": True,  "bit_depth": 16},
    "alac24":     {"label": "ALAC (24-bit)",       "encoder": "alac",         "kind": "lossless", "lossless": True,  "bit_depth": 24},
    "copy":       {"label": "Passthru (原样复制)",  "encoder": "copy",         "kind": "copy",   "lossless": True},
    "autopassthru": {"label": "Auto Passthru (可复制则复制，否则 AAC)",
                     "encoder": "copy",             "kind": "copy",           "lossless": True, "fallback": "aac"},
}

SAMPLE_RATES = [8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 88200, 96000, 176400, 192000]

CHANNEL_MODES = {
    "auto":    "自动（与源一致）",
    "mono":    "单声道",
    "stereo":  "立体声",
    "5.1":     "5.1 环绕",
}

CONTAINERS: dict[str, dict[str, Any]] = {
    "mp4":   {"label": "MP4 (.mp4)",  "muxer": "mp4",   "ext": ".mp4",
              "audio_ok": {"aac", "aac_he", "mp3", "ac3", "eac3", "opus", "flac", "flac24", "alac", "alac24", "copy", "autopassthru"},
              "video_ok": {"h264", "hevc", "mpeg4", "mpeg2", "vp9", "av1"}},
    "m4v":   {"label": "M4V (.m4v)",  "muxer": "mp4",   "ext": ".m4v",
              "audio_ok": {"aac", "aac_he", "mp3", "ac3", "eac3", "opus", "flac", "flac24", "alac", "alac24", "copy", "autopassthru"},
              "video_ok": {"h264", "hevc", "mpeg4", "mpeg2", "vp9", "av1"}},
    "mkv":   {"label": "MKV (.mkv)",  "muxer": "matroska", "ext": ".mkv",
              "audio_ok": set(AUDIO_CODECS.keys()),
              "video_ok": {"h264", "hevc", "mpeg4", "mpeg2", "vp8", "vp9", "av1", "theora"}},
    "webm":  {"label": "WebM (.webm)","muxer": "webm",  "ext": ".webm",
              "audio_ok": {"vorbis", "opus", "copy", "autopassthru"},
              "video_ok": {"vp8", "vp9", "av1"}},
}

HWACCEL_FORMS = {
    "none":        "软件编码（不加速）",
    "nvenc":       "NVIDIA NVENC",
    "amf":         "AMD AMF",
    "qsv":         "Intel QSV",
    "videotoolbox": "Apple VideoToolbox",
}

# encoder suffix per (hwaccel, codec) — resolved against runtime capabilities
HW_ENCODER_MATRIX: dict[str, dict[str, list[str]]] = {
    "nvenc":        {"h264": ["h264_nvenc"],          "hevc": ["hevc_nvenc"],          "av1": ["av1_nvenc"]},
    "amf":          {"h264": ["h264_amf"],            "hevc": ["hevc_amf"],            "av1": ["av1_amf"]},
    "qsv":          {"h264": ["h264_qsv"],            "hevc": ["hevc_qsv"],            "av1": ["av1_qsv"], "vp9": ["vp9_qsv"]},
    "videotoolbox": {"h264": ["h264_videotoolbox"],   "hevc": ["hevc_videotoolbox"],   "av1": ["av1_videotoolbox"], "prores": ["prores_videotoolbox"]},
}


@dataclass
class AudioOptions:
    codec: str = "aac"              # key of AUDIO_CODECS
    sample_rate: int = 0            # 0 = keep source
    bit_depth: int = 0              # 0 = codec default (16/24 only for lossless)
    bitrate_kb: int = 256           # used in CBR mode when quality is None
    quality: Optional[float] = None # encoder-specific VBR quality; None = use bitrate
    channels: str = "auto"          # key of CHANNEL_MODES
    gain_db: float = 0.0


@dataclass
class VideoOptions:
    mode: str = "copy"              # copy | h264 | hevc | av1 | vp9
    hwaccel: str = "none"           # key of HWACCEL_FORMS
    preset: str = "medium"          # software x264/x265 preset (ignored for copy/hw)


@dataclass
class ExportOptions:
    audio: AudioOptions = field(default_factory=AudioOptions)
    video: VideoOptions = field(default_factory=VideoOptions)
    container: str = "mp4"          # key of CONTAINERS
    extra_ffmpeg_args: list[str] = field(default_factory=list)   # passthrough tokens

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExportOptions":
        audio = AudioOptions(**d.get("audio", {}))
        video = VideoOptions(**d.get("video", {}))
        extra = list(d.get("extra_ffmpeg_args", []))
        return cls(audio=audio, video=video, container=d.get("container", "mp4"),
                   extra_ffmpeg_args=extra)


# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------

JOB_STATES = ("queued", "running", "done", "failed", "canceled", "skipped")


@dataclass
class Job:
    id: str
    project_id: str
    source_path: str
    output_name: str = ""           # final filename (no dir), may be renamed via panel
    display_name: str = ""          # original basename for UI
    is_xhe: bool = False
    state: str = "queued"
    progress: float = 0.0           # 0..1
    stage: str = ""                 # probe/decode/encode/verify/commit
    error: Optional[str] = None
    options: ExportOptions = field(default_factory=ExportOptions)
    output_dir: str = ""
    output_path: str = ""           # reserved final path (with dedup suffix)
    log_text: str = ""              # tail cache; full log lives in log file
    attempts: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_row(self) -> list[Any]:
        return [self.id, self.display_name, "xHE" if self.is_xhe else "-",
                self.state, f"{self.progress * 100:.0f}%", self.stage, self.error or ""]


def job_row_headers() -> list[str]:
    return ["ID", "文件", "xHE", "状态", "进度", "阶段", "错误"]
