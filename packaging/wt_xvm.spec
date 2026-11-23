# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "data" / "locales.json"), "data"),
    (str(ROOT / "data" / "vehicles.json"), "data"),
    (str(ROOT / "data" / "ocr_models"), "data/ocr_models"),
    (str(ROOT / "RU_Log.md"), "."),
    (str(ROOT / "RU_Chat.md"), "."),
    (str(ROOT / "EN_Log.md"), "."),
    (str(ROOT / "EN_Chat.md"), "."),
]
binaries = []
hiddenimports = [
    "clr",
    "System",
    "webview.platforms.edgechromium",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

for pkg in (
    "webview",
    "pythonnet",
    "clr_loader",
    "rapidocr_onnxruntime",
    "onnxruntime",
    "pystray",
    "uvicorn",
    "fastapi",
    "rapidfuzz",
    "mss",
):
    pkg_datas, pkg_bins, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_bins
    hiddenimports += pkg_hidden

a = Analysis(
    [str(ROOT / "wt_xvm" / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
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
    a.binaries,
    a.datas,
    [],
    name="WT-XVM",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_window_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
