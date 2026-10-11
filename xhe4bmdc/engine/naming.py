"""Output naming: sanitisation, conflict checks and _1/_2 dedup suffixes."""
from __future__ import annotations

import re
from pathlib import Path

_WIN_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)}, *{f"LPT{i}" for i in range(1, 10)}}


def sanitize_name(name: str) -> str:
    """Make a filename safe on both Windows and macOS (case-insensitive FS)."""
    name = name.strip()
    name = _WIN_ILLEGAL.sub("_", name)
    name = re.sub(r"[ .]+$", "", name)          # no trailing dots/spaces (Windows)
    stem = name.rsplit(".", 1)[0] or "video"
    if stem.upper() in _WIN_RESERVED:
        name = f"_{name}"
    if not name:
        name = "video"
    return name


def unique_output_path(directory: Path, filename: str) -> Path:
    """Reserve a non-existing output path, appending _1, _2... on conflicts.

    Note: callers that race on the same directory should hold the engine's
    reservation lock; this function only inspects the filesystem.
    """
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    i = 1
    while True:
        candidate = directory / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def apply_rename(original: str, new: str, fallback_ext: str = ".mp4") -> str:
    """User-typed output name -> safe filename with an extension.

    Keeps the original extension when the new name has none; replaces it when
    the user typed a different one (e.g. renaming to .mkv).
    """
    original = original.strip()
    new = new.strip()
    if not new:
        new = original
    new = sanitize_name(new)
    orig_ext = Path(original).suffix if "." in original else fallback_ext
    if "." not in new:
        new = new + orig_ext
    return new
