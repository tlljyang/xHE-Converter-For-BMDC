"""FFmpeg command construction (HandBrake-parity audio options).

Produces argument *arrays* only — never a shell string.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from xhe4bmdc.engine.models import (AUDIO_CODECS, CONTAINERS, HW_ENCODER_MATRIX,
                                    AudioOptions, ExportOptions, ProbeResult, VideoOptions)

# Flags the engine itself may generate — user passthrough of these is a conflict
_ENGINE_FLAGS = {"-c:a", "-codec:a", "-acodec", "-b:a", "-q:a", "-ar", "-ac",
                 "-c:v", "-codec:v", "-vcodec", "-i", "-map", "-f", "-y",
                 "-filter:a", "-af", "-sample_fmt", "-progress", "-movflags"}


@dataclass
class BuiltCommand:
    args: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)      # non-fatal warnings
    conflicts: list[str] = field(default_factory=list)  # passthrough conflicts


def available_encoders(ffmpeg: str) -> set[str]:
    """Parse `ffmpeg -encoders` into a set of encoder names (cached by caller).

    Line format: ' V....D libx264   description...' — name starts at column 8.
    """
    import subprocess
    cmd = [ffmpeg, "-hide_banner", "-encoders"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30,
                          creationflags=_cf())
    encs: set[str] = set()
    for line in proc.stdout.splitlines():
        if len(line) > 8 and line[0] == " " and line[7] == " " \
                and not line.startswith(" -----"):
            name = line[8:].strip().split(" ")[0]
            if name and not name.startswith("-") and all(c.isalnum() or c in "_-.=" for c in name):
                encs.add(name)
    return encs


def _cf() -> int:
    import sys
    return 0x08000000 if sys.platform == "win32" else 0


def check_audio_codec_available(codec_key: str, encoders: set[str]) -> Optional[str]:
    """Return a human reason when a codec is unavailable, else None."""
    info = AUDIO_CODECS.get(codec_key)
    if info is None:
        return f"未知音频编码 {codec_key}"
    enc = info.get("encoder")
    if enc == "copy":
        return None
    if enc not in encoders:
        alt = info.get("alt_encoder")
        if alt and alt in encoders and codec_key == "aac_he":
            # native aac encoder can emit HE-AAC profile on some builds
            return None
        return f"当前 FFmpeg 不含 {enc} 编码器"
    return None


def build_audio_args(opts: AudioOptions, probe: ProbeResult,
                     encoders: set[str]) -> tuple[list[str], list[str], list[str]]:
    """Audio encoder arguments + filter chain + notes. Returns (args, filters, notes)."""
    info = AUDIO_CODECS[opts.codec]
    args: list[str] = []
    filters: list[str] = []
    notes: list[str] = []

    src_ch = probe.audio.channels if probe.audio else 2

    if info.get("encoder") == "copy":
        args += ["-c:a", "copy"]
        return args, filters, notes

    encoder = info["encoder"]
    if encoder not in encoders:
        alt = info.get("alt_encoder")
        if opts.codec == "aac_he" and alt == "aac" and alt in encoders:
            encoder, args = alt, args + ["-profile:a", info["alt_profile"]]
            notes.append("libfdk_aac 不可用，回退 FFmpeg 原生 aac 的 HE-AAC profile")
        else:  # caller should have gated this in UI; engine-side hard fallback
            encoder, notes = "aac", notes + [f"{info['encoder']} 不可用，已回退 aac"]

    args += ["-c:a", encoder]

    # bit depth (lossless family)
    if info.get("bit_depth") == 24:
        args += ["-sample_fmt", "s32"]
    elif info.get("bit_depth") == 16 and encoder in {"flac", "alac"}:
        args += ["-sample_fmt", "s16"]

    # bitrate / quality
    if opts.quality is not None:
        if encoder in {"libmp3lame"}:
            args += ["-q:a", str(int(max(0, min(9, opts.quality))))]
        elif encoder in {"libvorbis", "libopus"}:
            args += ["-q:a", str(max(-1.0, min(10.0, float(opts.quality))))]
        elif encoder in {"libfdk_aac"}:
            args += ["-vbr", str(int(max(1, min(5, opts.quality))))]
        else:
            args += ["-b:a", f"{max(8, int(opts.bitrate_kb))}k"]
    else:
        args += ["-b:a", f"{max(8, int(opts.bitrate_kb))}k"]

    # channels / mixdown
    ac_map = {"mono": 1, "stereo": 2, "5.1": 6}
    if opts.channels in ac_map:
        want = ac_map[opts.channels]
        if want != src_ch:
            args += ["-ac", str(want)]
    # "auto" keeps source layout

    # gain
    if abs(opts.gain_db) > 0.001:
        filters.append(f"volume={opts.gain_db:+.2f}dB")

    # sample rate
    if opts.sample_rate:
        args += ["-ar", str(int(opts.sample_rate))]

    return args, filters, notes


def resolve_video_encoder(v: VideoOptions, encoders: set[str],
                          notes: list[str]) -> Optional[str]:
    """Return the video encoder name, or None for stream copy."""
    if v.mode == "copy":
        return None
    if v.hwaccel == "none":
        sw = {"h264": "libx264", "hevc": "libx265", "av1": "libsvtav1", "vp9": "libvpx-vp9"}
        enc = sw.get(v.mode)
        if enc and enc not in encoders:
            notes.append(f"{enc} 不可用，视频回退为 copy")
            return None
        return enc
    matrix = HW_ENCODER_MATRIX.get(v.hwaccel, {})
    candidates = matrix.get(v.mode, [])
    for cand in candidates:
        if cand in encoders:
            return cand
    notes.append(f"{v.hwaccel} 无 {v.mode} 硬件编码器，视频回退为 copy")
    return None


def build_ffmpeg_cmd(opts: ExportOptions, probe: ProbeResult,
                     encoders: set[str], inputs: list[str],
                     output: str, wav_audio_input: Optional[str] = None) -> BuiltCommand:
    """Assemble the final ffmpeg argument array.

    inputs: original media path(s). wav_audio_input: decoded WAV to use as the
    audio source instead of the original's audio stream (xHE pipeline).
    """
    bc = BuiltCommand()
    args: list[str] = ["-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats"]

    cont = CONTAINERS[opts.container]

    # --- inputs
    for i in inputs:
        args += ["-i", i]
    if wav_audio_input:
        args += ["-i", wav_audio_input]

    # --- maps
    if wav_audio_input:
        args += ["-map", "0:v:0", "-map", f"{len(inputs)}:a:0"]
        bc.notes.append("音频来自 xHE 解码中间 WAV")
    else:
        args += ["-map", "0:v:0?", "-map", "0:a:0?"]

    # --- video
    venc = resolve_video_encoder(opts.video, encoders, bc.notes)
    if venc is None:
        args += ["-c:v", "copy"]
    else:
        args += ["-c:v", venc]
        if opts.video.hwaccel == "none" and opts.video.preset:
            args += ["-preset", opts.video.preset]

    # --- audio
    a_args, a_filters, a_notes = build_audio_args(opts.audio, probe, encoders)
    bc.notes.extend(a_notes)
    args += a_args
    if a_filters:
        args += ["-filter:a", ",".join(a_filters)]

    # --- autopassthru resolution
    if opts.audio.codec == "autopassthru":
        src_codec = probe.audio.codec if probe.audio else ""
        allowed = cont["audio_ok"]
        passthru_ok = {"aac": "aac", "mp3": "mp3", "ac3": "ac3", "eac3": "eac3",
                       "opus": "opus", "vorbis": "vorbis", "flac": "flac", "alac": "alac"}
        key = passthru_ok.get(src_codec)
        if key and key in allowed:
            # strip generated -c:a ... args, use copy
            args = _replace_audio_codec_with_copy(args)
            bc.notes.append(f"源音频 {src_codec} 可直通容器，已 Passthru")
        else:
            fallback = AUDIO_CODECS["autopassthru"].get("fallback", "aac")
            bc.notes.append(f"源音频 {src_codec or '未知'} 不能直通 {opts.container}，回退 {fallback}")

    # --- container
    args += ["-f", cont["muxer"]]
    if opts.container in {"mp4", "m4v"}:
        args += ["-movflags", "+faststart"]

    # --- passthrough extras + conflict detection
    i = 0
    extra = list(opts.extra_ffmpeg_args)
    while i < len(extra):
        tok = extra[i]
        if tok in _ENGINE_FLAGS:
            bc.conflicts.append(tok)
        i += 1
    if extra:
        args += extra

    args += [output]
    bc.args = args
    return bc


def _audio_notes(_a: AudioOptions) -> list[str]:
    return []


def _replace_audio_codec_with_copy(args: list[str]) -> list[str]:
    out: list[str] = []
    skip = 0
    for i, tok in enumerate(args):
        if skip:
            skip -= 1
            continue
        if tok in {"-c:a", "-codec:a", "-acodec"}:
            out += ["-c:a", "copy"]
            skip = 1
            continue
        out.append(tok)
    return out


def split_arg_tokens(text: str) -> list[str]:
    """Split a user passthrough string into tokens (quote-aware)."""
    import shlex
    try:
        lexer = shlex.shlex(text, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return text.split()
