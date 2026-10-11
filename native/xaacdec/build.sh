#!/usr/bin/env bash
# Build the AOSP libxaac `xaacdec` test-decoder binary for the current platform.
# Used by CI (Windows MSYS2/MinGW, macOS clang). Produces: out/bin/xaacdec[.exe]
#
# Env:
#   XAAC_SOURCE_TAR  optional path/url to a pinned source tarball
#   LIBXAAC_REF      commit-ish to build (default: pinned below)
set -euo pipefail

LIBXAAC_REF="${LIBXAAC_REF:-55de89d2d004016292d1fcb4b42904ad3f5b54a82}"  # libxaac v0.1.12
BASE="$(cd "$(dirname "$0")" && pwd)"
SRC="$BASE/src"
OUT="$BASE/out"
JOBS="${JOBS:-$(nproc 2>/dev/null || sysctl -n hw.ncpu)}"

mkdir -p "$SRC" "$OUT"

if [ ! -d "$SRC/libxaac" ]; then
  if [ -n "${XAAC_SOURCE_TAR:-}" ] && [ -f "$XAAC_SOURCE_TAR" ]; then
    tar -xf "$XAAC_SOURCE_TAR" -C "$SRC"
    mv "$SRC"/libxaac-* "$SRC/libxaac"
  else
    git clone https://android.googlesource.com/platform/external/libxaac "$SRC/libxaac"
    git -C "$SRC/libxaac" checkout "$LIBXAAC_REF"
  fi
fi

cmake -S "$SRC/libxaac" -B "$OUT/build" -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX="$OUT" > /dev/null
cmake --build "$OUT/build" --target xaacdec -j "$JOBS" > /dev/null

BIN="$(find "$OUT/build" -name 'xaacdec*' -type f | head -1)"
mkdir -p "$OUT/bin"
cp "$BIN" "$OUT/bin/"
echo "built: $OUT/bin/$(basename "$BIN")"
sha256sum "$OUT/bin/$(basename "$BIN")" || shasum -a 256 "$OUT/bin/$(basename "$BIN")"
