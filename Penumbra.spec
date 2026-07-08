# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller build spec for Penumbra (Nautilus).
# Build with:  pyinstaller Penumbra.spec --noconfirm
#
# All asset paths in the code resolve relative to the folder ABOVE game/
# (config._HERE = dirname(dirname(__file__))). PyInstaller puts bundled data
# at that same bundle root, so every data folder below lands where the code
# already looks for it.

import os

# Folders shipped verbatim (src on disk  ->  dest inside the bundle root).
_DATA_DIRS = [
    "Textures",
    "vehicles",
    "levels",
    "images",
    "projectiles",
    "saves",
]
# Loose config/data files the code reads at runtime.
_DATA_FILES = [
    "base_upgrades.json",
    "capture_points.json",
    "unlocks.json",
    "waves.json",
    "icon.png",
]

datas = [(d, d) for d in _DATA_DIRS if os.path.isdir(d)]
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
    [],
    exclude_binaries=True,      # onedir build (saves persist next to the exe)
    name="Penumbra",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,              # windowed app, no console window
    disable_windowed_traceback=False,
    icon="icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Penumbra",
)
