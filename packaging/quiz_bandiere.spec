# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone macOS app (Intel x86_64, macOS 11+).

Build:  ../backend/.venv/bin/pyinstaller quiz_bandiere.spec --noconfirm --distpath dist --workpath build
Output: dist/QuiZ Bandiere.app  (double-clickable)
"""
from PyInstaller.utils.hooks import collect_submodules

APP_NAME = "QuiZ Bandiere"
BUNDLE_ID = "it.quizbandiere.desktop"
VERSION = "0.2.0"

# read-only seed shipped with the app (data/cache/baseline/review + the built
# web frontend). Destination is relative to sys._MEIPASS (Contents/Resources/_internal).
datas = [
    ("../frontend/dist", "frontend/dist"),
    ("../data", "data"),
]

hiddenimports = (
    collect_submodules("app")
    + collect_submodules("uvicorn")
    + collect_submodules("fastapi")
)

a = Analysis(
    ["launcher.py"],
    pathex=["../backend", ".."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=APP_NAME + ".app",
    icon="icon.icns",
    bundle_identifier=BUNDLE_ID,
    info_plist={
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "CFBundleName": APP_NAME,
    },
)
