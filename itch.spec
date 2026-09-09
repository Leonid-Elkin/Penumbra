# -*- mode: python ; coding: utf-8 -*-
#
# One-FILE build for itch.io.  Everything — code, textures and every JSON — is
# packed INSIDE the single Penumbra.exe and extracted to a hidden temp folder at
# launch, so a player never sees (or can edit) the config JSONs.  Saves/settings
# are redirected to %APPDATA%\Penumbra at runtime (see game/config.py).
#
# Build with:  pyinstaller itch.spec --noconfirm --clean

import os

# Folders shipped verbatim (src on disk  ->  dest inside the bundle root).
_DATA_DIRS = [
    "Textures", "vehicles", "levels", "images", "projectiles",
]
# Loose config/data files the code reads at runtime.
_DATA_FILES = [
    "base_upgrades.json", "capture_points.json", "unlocks.json",
    "waves.json", "surge.json", "icon.png",
]

datas  = [(d, d) for d in _DATA_DIRS if os.path.isdir(d)]
datas += [(f, ".") for f in _DATA_FILES if os.path.isfile(f)]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Penumbra",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,              # windowed app, no console window
    disable_windowed_traceback=False,
    icon="icon.ico",
)
