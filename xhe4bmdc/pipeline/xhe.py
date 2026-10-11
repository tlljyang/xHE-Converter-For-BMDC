"""xHE-AAC (USAC) decode stage using the AOSP libxaac `xaacdec` binary.

Mirrors the validated batch_convert.py approach:
  esds ASC  +  consecutive AUs  +  Ittiam metadata file  ->  xaacdec -mp4:1

Raw file layout is [ASC][AU0][AU1]... and the metadata stsz table lists ONLY
the AU sizes (the ASC is consumed via -dec_info_init). Files are accessed by
seek — never fully loaded into memory.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from xhe4bmdc.engine.probe import _creationflags

_CHUNK = 4 * 1024 * 1024
_OVERLAP = 512


class XheDecodeError(RuntimeError):
    pass


def _find_esds(path: Path) -> int:
    """Locate the `esds` box marker anywhere in the file (chunked scan)."""
    with open(path, "rb") as f:
        pos = 0
        tail = b""
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            data = tail + chunk
            idx = data.find(b"esds")
            if idx != -1:
                return pos - len(tail) + idx
            tail = data[-_OVERLAP:]
            pos += len(chunk)
    raise XheDecodeError("MP4 中未找到 esds box")


def _read_desc(data: bytes, p: int) -> tuple[int, int, int]:
    """One MPEG-4 descriptor header: (tag, payload_len, payload_start)."""
    tag = data[p]
    p += 1
    length = 0
    for _ in range(4):
        b = data[p]
        p += 1
        length = (length << 7) | (b & 0x7F)
        if not (b & 0x80):
            break
    return tag, length, p


def extract_asc(path: str | Path) -> bytes:
    """Extract the AudioSpecificConfig from the file's esds box."""
    pth = Path(path)
    off = _find_esds(pth)
    with open(pth, "rb") as f:
        f.seek(off)
        window = f.read(1024)
    p = 4 + 4  # skip 'esds' + version/flags
    tag, _ln, p = _read_desc(window, p)
    if tag != 0x03:
        raise XheDecodeError(f"ES_Descriptor 预期 0x03，得到 {tag:#x}")
    p += 3  # ES_ID (2) + flags (1)
    tag, _ln, p = _read_desc(window, p)
    if tag != 0x04:
        raise XheDecodeError(f"DecoderConfigDescriptor 预期 0x04，得到 {tag:#x}")
    if window[p] != 0x40:
        raise XheDecodeError(f"objectTypeIndication {window[p]:#x} 不是 AAC 系")
    p += 13
    tag, ln, p = _read_desc(window, p)
    if tag != 0x05 or ln == 0:
        raise XheDecodeError("esds 中无 DecoderSpecificInfo (ASC)")
    return window[p:p + ln]


def audio_entries(ffprobe: str, mp4_path: str,
                  timeout: float = 120.0) -> list[tuple[int, int]]:
    """Audio AU (position, size) entries in stream order via ffprobe.

    ffprobe csv fields print as `size,pos`; pos<0 (no byte position) is skipped.
    """
    cmd = [ffprobe, "-v", "error", "-select_streams", "a:0",
           "-show_packets", "-show_entries", "packet=pos,size",
           "-of", "csv=p=0", mp4_path]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=_creationflags())
    if proc.returncode != 0:
        raise XheDecodeError(f"ffprobe 包表失败: {proc.stderr.strip()[-200:]}")
    entries: list[tuple[int, int]] = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 2:
            continue
        try:
            size, pos = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if pos >= 0:
            entries.append((pos, size))
    entries.sort(key=lambda e: e[0])
    return entries


def build_raw_and_meta(mp4_path: str, workdir: Path,
                       ffprobe: str) -> tuple[Path, Path, int]:
    """Create <stem>.raw (ASC+AUs) + <stem>.meta (Ittiam metadata).

    Returns (raw_path, meta_path, au_count).
    """
    src = Path(mp4_path)
    asc = extract_asc(src)
    entries = audio_entries(ffprobe, mp4_path)
    if not entries:
        raise XheDecodeError("未找到音频包")

    workdir.mkdir(parents=True, exist_ok=True)
    raw_path = workdir / (src.stem + ".raw")
    meta_path = workdir / (src.stem + ".meta")

    with open(src, "rb") as f, open(raw_path, "wb") as out:
        out.write(asc)
        for pos, size in entries:
            f.seek(pos)
            out.write(f.read(size))

    with open(meta_path, "w", encoding="ascii") as m:
        m.write(f"-dec_info_init:{len(asc)}\n")
        m.write("-g_track_count:1\n")
        m.write(f"-ia_mp4_stsz_entries:{len(entries)}\n")
        m.write("-playTimeInSamples:0\n")
        m.write("-startOffsetInSamples:0\n")
        m.write("-useEditlist:0\n")
        for _, size in entries:
            m.write(f"-ia_mp4_stsz_size:{size}\n")
    return raw_path, meta_path, len(entries)


def run_xaacdec(xaacdec: str, raw: Path, meta: Path, wav_out: Path,
                timeout: float = 600.0) -> int:
    """Decode the raw AU stream to WAV. Returns decoded frame count."""
    cmd = [xaacdec,
           f"-ifile:{raw}", f"-imeta:{meta}", f"-ofile:{wav_out}",
           "-mp4:1", "-pcmsz:16", "-dmix:0", "-tostereo:1"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=_creationflags())
    tail = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 or not wav_out.exists() or wav_out.stat().st_size < 45:
        raise XheDecodeError(f"xaacdec 失败(rc={proc.returncode}): {tail[-300:]}")
    frames = 0
    for line in tail.splitlines():
        if "TOTAL FRAMES" in line:
            try:
                frames = int(line.split("[")[1].split("]")[0])
            except (IndexError, ValueError):
                pass
    return frames
