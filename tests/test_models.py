"""Unit tests for model logic (xHE detection, options round-trip)."""
from xhe4bmdc.engine.models import (AudioOptions, AudioStreamInfo, ExportOptions,
                                    ProbeResult, VideoOptions)


def _probe(profile: str, codec: str = "aac") -> ProbeResult:
    return ProbeResult(path="x.mp4",
                       audio=AudioStreamInfo(index=1, codec=codec, profile=profile))


def test_xhe_detected_by_profile():
    assert _probe("xHE-AAC").is_xhe


def test_xhe_detected_by_usac_profile():
    assert _probe("USAC").is_xhe


def test_xhe_detected_by_codec_name():
    assert _probe("", codec="xheaac").is_xhe


def test_not_xhe_for_plain_aac():
    assert not _probe("LC").is_xhe


def test_not_xhe_without_audio():
    assert not ProbeResult(path="x.mp4").is_xhe


def test_options_roundtrip():
    o = ExportOptions(
        audio=AudioOptions(codec="flac24", sample_rate=96000, bit_depth=24,
                           bitrate_kb=0, quality=None, channels="stereo", gain_db=1.5),
        video=VideoOptions(mode="hevc", hwaccel="nvenc", preset="slow"),
        container="mkv",
        extra_ffmpeg_args=["-x", "y"])
    d = o.to_dict()
    o2 = ExportOptions.from_dict(d)
    assert o2.audio.codec == "flac24"
    assert o2.audio.gain_db == 1.5
    assert o2.video.hwaccel == "nvenc"
    assert o2.container == "mkv"
    assert o2.extra_ffmpeg_args == ["-x", "y"]
