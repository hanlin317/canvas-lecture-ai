# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPEC).resolve().parent
PLAYWRIGHT_BROWSERS = ROOT / "build" / "playwright-browsers"

if not PLAYWRIGHT_BROWSERS.is_dir():
    raise SystemExit(
        "Playwright Chromium is missing. Run 2026-10-25-build-macos.sh instead of PyInstaller directly."
    )

datas = [
    (str(ROOT / "app" / "web" / "templates"), "app/web/templates"),
    (str(ROOT / "app" / "web" / "static"), "app/web/static"),
    (str(ROOT / "config.example.toml"), "."),
]
datas += collect_data_files("mlx_whisper")
datas += collect_data_files("mlx", includes=["lib/mlx.metallib"])

hiddenimports = collect_submodules("openai") + collect_submodules(
    "mlx_whisper", filter=lambda name: name != "mlx_whisper.torch_whisper"
) + [
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
    binaries=[(get_ffmpeg_exe(), "ffmpeg")],
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
    name="Dental Lecture Manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch="arm64",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Dental Lecture Manager",
)

app = BUNDLE(
    coll,
    name="Dental Lecture Manager.app",
    bundle_identifier="com.hanlin317.dentallecturemanager",
    info_plist={
        "CFBundleDisplayName": "Dental Lecture Manager",
        "CFBundleShortVersionString": "0.2.0",
        "CFBundleVersion": "2",
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
    },
)
