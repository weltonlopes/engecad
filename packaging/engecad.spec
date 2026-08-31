# -*- mode: python ; coding: utf-8 -*-
"""Build: pyinstaller packaging/engecad.spec (a partir da raiz do repo)."""

import os

from PyInstaller.utils.hooks import collect_all

block_cipher = None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))
ICON = os.path.join(ROOT, "packaging", "icon.ico")

datas = []
binaries = []
hiddenimports = []

# rasterio/pyproj carregam GDAL/PROJ via dados binários que o PyInstaller
# não detecta sozinho por análise estática; ezdxf/shapely tem dados próprios.
for pkg in ("rasterio", "pyproj", "shapely", "ezdxf"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    [os.path.join(ROOT, "packaging", "entrypoint.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EngeCAD",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=ICON if os.path.exists(ICON) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="EngeCAD",
)
