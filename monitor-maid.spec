# -*- mode: python ; coding: utf-8 -*-
"""Standalone app. PyInstaller embeds the interpreter and imported libraries only.

On macOS the bundle is Monitor Maid.app. On Windows and Linux the folder is
dist/monitor-maid and the program name is monitor-maid.
"""

import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("certifi")
hidden = collect_submodules("monitor_maid") + ["certifi"]

block_cipher = None

a = Analysis(
    ["monitor_maid/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "ensurepip",
        "lib2to3",
        "pip",
        "pydoc",
        "pytest",
        "setuptools",
        "tkinter",
        "unittest",
        "venv",
        "xmlrpc",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe_name = "Monitor Maid" if sys.platform == "darwin" else "monitor-maid"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=exe_name,
)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Monitor Maid.app",
        icon=None,
        bundle_identifier="local.monitor-maid.app",
        info_plist={
            "CFBundleName": "Monitor Maid",
            "CFBundleDisplayName": "Monitor Maid",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
        },
    )
