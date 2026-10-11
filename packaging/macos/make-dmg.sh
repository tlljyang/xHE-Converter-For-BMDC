#!/usr/bin/env bash
# Build a simple .dmg from the PyInstaller .app bundle (no external deps).
# Usage: ./make-dmg.sh <path-to-app> <output.dmg> <volume-name>
set -euo pipefail

APP="${1:?usage: make-dmg.sh <app> <dmg> <volname>}"
DMG="${2:?}"
VOL="${3:-xHE4BMDC}"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

hdiutil create -volname "$VOL" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
echo "dmg: $DMG"
