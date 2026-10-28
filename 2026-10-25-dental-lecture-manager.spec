# -*- mode: python ; coding: utf-8 -*-

import importlib.util
import platform
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPEC).resolve().parent
PLAYWRIGHT_BROWSERS = ROOT / "build" / "playwright-browsers"
MACHINE = platform.machine().lower()
IS_ARM = MACHINE in {"arm64", "aarch64"}
TARGET_ARCH = "arm64" if IS_ARM else "x86_64"

if not PLAYWRIGHT_BROWSERS.is_dir():
    raise SystemExit(
        "Playwright Chromium is missing. Run 2026-10-25-build-macos.sh instead of PyInstaller directly."
    )

datas = [
    (str(ROOT / "app" / "web" / "templates"), "app/web/templates"),
    (str(ROOT / "app" / "web" / "static"), "app/web/static"),
    (str(ROOT / "config.example.toml"), "."),
]
if importlib.util.find_spec("mlx_whisper") is not None:
    datas += collect_data_files("mlx_whisper")
if importlib.util.find_spec("mlx") is not None:
    datas += collect_data_files("mlx", includes=["lib/mlx.metallib"])
datas += collect_data_files("docx")
datas += collect_data_files("reportlab", includes=["fonts/*.ttf"])

binaries = [(get_ffmpeg_exe(), "ffmpeg")]
faster_hiddenimports = []
if importlib.util.find_spec("faster_whisper") is not None:
    faster_datas, faster_binaries, faster_hiddenimports = collect_all("faster_whisper")
    ctranslate_datas, ctranslate_binaries, ctranslate_hiddenimports = collect_all("ctranslate2")
    datas += faster_datas + ctranslate_datas
    binaries += faster_binaries + ctranslate_binaries
    faster_hiddenimports += ctranslate_hiddenimports

mlx_hiddenimports = (
    collect_submodules("mlx_whisper", filter=lambda name: name != "mlx_whisper.torch_whisper")
    if importlib.util.find_spec("mlx_whisper") is not None
    else []
)
hiddenimports = collect_submodules("openai") + mlx_hiddenimports + faster_hiddenimports + [
    "app.main",
    "mlx._os_warning",
    "mlx._reprlib_fix",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

a = Analysis(
    [str(ROOT / "app" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "torch", "mlx_whisper.torch_whisper"],
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
    target_arch=TARGET_ARCH,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Canvas Lecture AI",
)

app = BUNDLE(
    coll,
    name="Canvas Lecture AI.app",
    bundle_identifier="com.hanlin317.dentallecturemanager",
    info_plist={
        "CFBundleDisplayName": "Canvas Lecture AI",
        "CFBundleShortVersionString": "0.6.0",
        "CFBundleVersion": "6",
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
    },
)
