# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

webauthn_datas, webauthn_binaries, webauthn_hiddenimports = collect_all("webauthn")

a = Analysis(
    ["desktop_app.py"],
    pathex=["."],
    binaries=webauthn_binaries,
    datas=webauthn_datas + [("vendor", "vendor"), ("index.html", "."), ("assets", "assets")],
    hiddenimports=webauthn_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VBCCPAS-Soft",
    icon="assets/society-logo.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
