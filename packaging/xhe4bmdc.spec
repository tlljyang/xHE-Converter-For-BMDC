# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: xHE4BMDC desktop launcher (Gradio UI).

Build (from repo root):
  pyinstaller packaging/xhe4bmdc.spec --noconfirm
Output: dist-app/xhe4bmdc/  (self-contained app with embedded Python)

The Lite / Onekey difference is only which extras sit next to the app dir:
  Lite  -> user provides dependencies.yaml
  Onekey-> ffmpeg/bin/* and bin/xaacdec placed alongside by the installer
"""
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ["tkinter", "tkinter.filedialog"]

# Gradio drags a large dynamic-import graph; collect everything it ships.
for pkg in ("gradio", "gradio_client", "safehttpx", "groovy"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["../packaging/launcher.py"],
    pathex=[".."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="xHE4BMDC",
    debug=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="xHE4BMDC",
)

import sys  # noqa: E402

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="xHE Converter for BMDC.app",
        bundle_identifier="io.github.tlljyang.xhe4bmdc",
        version="0.1.0",
        info_plist={
            "CFBundleDisplayName": "xHE4BMDC",
            "CFBundleShortVersionString": "0.1.0",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
        },
    )
