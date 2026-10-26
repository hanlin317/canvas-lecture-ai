# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPEC).resolve().parent
PLAYWRIGHT_BROWSERS = ROOT / "build" / "playwright-browsers"

if not PLAYWRIGHT_BROWSERS.is_dir():
    raise SystemExit(
        "Playwright Chromium is missing. Run 2026-10-26-build-windows.ps1 instead of "
        "PyInstaller directly."
    )

faster_datas, faster_binaries, faster_hiddenimports = collect_all("faster_whisper")
ctranslate_datas, ctranslate_binaries, ctranslate_hiddenimports = collect_all("ctranslate2")

datas = [
    (str(ROOT / "app" / "web" / "templates"), "app/web/templates"),
    (str(ROOT / "app" / "web" / "static"), "app/web/static"),
    (str(ROOT / "config.example.toml"), "."),
    (str(PLAYWRIGHT_BROWSERS), "playwright-browsers"),
]
datas += collect_data_files("docx")
datas += collect_data_files("reportlab", includes=["fonts/*.ttf"])
datas += faster_datas + ctranslate_datas

binaries = [(get_ffmpeg_exe(), "ffmpeg")]
binaries += faster_binaries + ctranslate_binaries

hiddenimports = (
    collect_submodules("openai")
    + collect_submodules("keyring")
    + faster_hiddenimports
    + ctranslate_hiddenimports
    + [
        "app.main",
        "keyring.backends.Windows",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
    ]
)

a = Analysis(
    [str(ROOT / "app" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "mlx", "mlx_whisper", "torch"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Canvas Lecture AI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Canvas Lecture AI",
)
