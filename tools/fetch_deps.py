#!/usr/bin/env python3
"""Resolve + download the latest stable FFmpeg / libxaac deps for a platform.

Writes deps-lock.yml (url, sha256, version per artifact) used by the weekly
release to decide whether anything changed. Platforms: windows-x64, macos-x64,
macos-arm64.

Usage: python tools/fetch_deps.py <platform> [--download DIR]
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

PROXY = None  # set via env HTTPS_PROXY by CI if needed


def http_get(url: str, as_text: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": "xHE4BMDC-ci"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    return data.decode("utf-8", "replace") if as_text else data


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha256_bytes(data)


# ---------------------------------------------------------------- windows

def resolve_windows(outdir: Path | None) -> dict:
    """Gyan 'release-essentials' tracks the latest stable branch build."""
    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    info = {"ffmpeg_url": url}
    if outdir:
        data = http_get(url)
        info["ffmpeg_sha256"] = save(outdir / "ffmpeg-win.zip", data)
        with zipfile.ZipFile(outdir / "ffmpeg-win.zip") as z:
            bins = [n for n in z.namelist()
                    if n.endswith(("bin/ffmpeg.exe", "bin/ffprobe.exe"))]
            z.extractall(outdir / "ffmpeg-win")
        exe = next(n for n in bins if n.endswith("ffmpeg.exe"))
        ver_out = http_get  # noqa: F841 — version comes from the zip dir name
        m = re.search(r"ffmpeg-(\d+\.\d+\.\d+)", exe)
        info["ffmpeg_version"] = m.group(1) if m else "unknown"
        info["ffmpeg_extracted"] = str((outdir / "ffmpeg-win").resolve())
    return info


# ---------------------------------------------------------------- macOS

_STABLE_RE = re.compile(r"ffmpeg-(\d+)\.(\d+)\.(\d+)\.zip")


def resolve_macos(outdir: Path | None, arch: str) -> dict:
    """macOS x64: evermeet.cx versioned zips (relative hrefs).
    macOS arm64: osxexperts.net static zips (ffmpeg<N>arm.zip / ffprobe<N>arm.zip)."""
    if arch == "x64":
        page = http_get("https://evermeet.cx/ffmpeg/", as_text=True)
        hrefs = re.findall(r'href="(ffmpeg-(?:\d+\.)+\d+\.zip)"', page)
        if not hrefs:
            raise RuntimeError("evermeet.cx 页面解析失败")
        ver = max(hrefs, key=lambda h: tuple(int(x) for x in _STABLE_RE.match(h).groups()))
        ffmpeg_url = f"https://evermeet.cx/pub/ffmpeg/{ver}"
        ffprobe_url = ffmpeg_url.replace("ffmpeg-", "ffprobe-")
    else:
        page = http_get("https://osxexperts.net/", as_text=True)
        ffs = re.findall(r'href="(https://www\.osxexperts\.net/ffmpeg\d*arm\.zip)"', page, re.I)
        fps = re.findall(r'href="(https://www\.osxexperts\.net/ffprobe\d*arm\.zip)"', page, re.I)
        if not ffs or not fps:
            raise RuntimeError("osxexperts.net 页面解析失败")
        ffmpeg_url, ffprobe_url = ffs[0], fps[0]

    info = {"ffmpeg_url": ffmpeg_url, "ffprobe_url": ffprobe_url, "arch": arch}
    m = re.search(r"ffmpeg-(\d+\.\d+\.\d+)\.zip|ffmpeg(\d+)arm", ffmpeg_url)
    info["ffmpeg_version"] = (m.group(1) or m.group(2)) if m else "unknown"
    if outdir:
        dest = outdir / f"ffmpeg-mac-{arch}"
        dest.mkdir(parents=True, exist_ok=True)
        for name, url in (("ffmpeg", ffmpeg_url), ("ffprobe", ffprobe_url)):
            data = http_get(url)
            info[f"{name}_sha256"] = save(outdir / f"{name}-mac-{arch}.zip", data)
            with zipfile.ZipFile(outdir / f"{name}-mac-{arch}.zip") as z:
                z.extractall(dest)
        info["ffmpeg_extracted"] = str(dest.resolve())
    return info


# ---------------------------------------------------------------- libxaac

LIBXAAC_REF = "55de89d2d004016292d1fcb4b42904ad3f5b54a82"  # v0.1.12 (pinned)


def resolve_libxaac(outdir: Path | None) -> dict:
    url = ("https://android.googlesource.com/platform/external/libxaac/+archive/"
           f"{LIBXAAC_REF}.tar.gz")
    info = {"libxaac_url": url, "libxaac_ref": LIBXAAC_REF}
    if outdir:
        data = http_get(url)
        info["libxaac_sha256"] = save(outdir / "libxaac.tar.gz", data)
        dest = outdir / "libxaac-src"
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(outdir / "libxaac.tar.gz") as t:
            t.extractall(dest)
        info["libxaac_extracted"] = str(dest.resolve())
    return info


def main() -> int:
    platform = sys.argv[1]
    download = "--download" in sys.argv
    outdir = Path(sys.argv[sys.argv.index("--download") + 1]) if download else None
    lock: dict = {"platform": platform}
    if platform == "windows-x64":
        lock.update(resolve_windows(outdir))
    elif platform in ("macos-x64", "macos-arm64"):
        lock.update(resolve_macos(outdir, "arm64" if "arm64" in platform else "x64"))
    else:
        print(f"unknown platform {platform}", file=sys.stderr)
        return 2
    lock.update(resolve_libxaac(outdir))
    print(json.dumps(lock, indent=2))
    if outdir:
        (outdir / "deps-lock.json").write_text(json.dumps(lock, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
