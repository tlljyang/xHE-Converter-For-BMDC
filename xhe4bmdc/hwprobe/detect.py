"""Startup GPU/encoder compatibility probing.

Strategy: `ffmpeg -encoders` tells us which encoder *modules* the build has;
a 6-frame test encode tells us whether the *device* actually works. Both must
pass before an accelerator is offered in the UI. All versions/generations of
NVENC / AMF / QSV / VideoToolbox are therefore supported dynamically — the
probe result reflects whatever combination of build + driver + GPU is present.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from xhe4bmdc.engine.models import HWACCEL_FORMS, HW_ENCODER_MATRIX
from xhe4bmdc.engine.probe import _creationflags

_TEST_DEFS = {
    "h264":  {"lavfi": "color=c=black:s=320x240:d=0.1:r=30", "args": []},
    "hevc":  {"lavfi": "color=c=black:s=320x240:d=0.1:r=30", "args": []},
    "av1":   {"lavfi": "color=c=black:s=320x240:d=0.1:r=30", "args": []},
    "vp9":   {"lavfi": "color=c=black:s=320x240:d=0.1:r=30", "args": []},
    "prores": {"lavfi": "color=c=black:s=320x240:d=0.1:r=30", "args": []},
}


@dataclass
class HwProbeResult:
    available: dict[str, list[str]] = field(default_factory=dict)  # form -> codec list
    details: list[str] = field(default_factory=list)

    def forms(self) -> list[str]:
        return [f for f in self.available if self.available[f]]

    def label_summary(self) -> str:
        if not self.forms():
            return "未检测到可用硬件加速"
        parts = []
        for form in self.forms():
            label = HWACCEL_FORMS.get(form, form)
            codecs = "+".join(self.available[form])
            parts.append(f"{label} ({codecs})")
        return "可用: " + "；".join(parts)


def probe_hwaccels(ffmpeg: str, encoders: set[str],
                   timeout_per_test: float = 20.0) -> HwProbeResult:
    result = HwProbeResult()
    for form in ("nvenc", "amf", "qsv", "videotoolbox"):
        result.available[form] = []
        for codec, encoder_names in HW_ENCODER_MATRIX.get(form, {}).items():
            encoder = next((e for e in encoder_names if e in encoders), None)
            if encoder is None:
                continue
            ok, why = _test_encode(ffmpeg, encoder, codec, timeout_per_test)
            if ok:
                result.available[form].append(codec)
            else:
                result.details.append(f"{encoder}: {why}")
    return result


def _test_encode(ffmpeg: str, encoder: str, codec: str,
                 timeout: float) -> tuple[bool, str]:
    d = _TEST_DEFS.get(codec, _TEST_DEFS["h264"])
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", d["lavfi"],
           "-frames:v", "6", "-c:v", encoder, *d["args"],
           "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=_creationflags())
    except subprocess.TimeoutExpired:
        return False, "测试编码超时"
    if proc.returncode == 0:
        return True, ""
    err = (proc.stderr or "").strip().splitlines()
    return False, err[-1][:160] if err else f"退出码 {proc.returncode}"
