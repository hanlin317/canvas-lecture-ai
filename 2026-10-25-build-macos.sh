#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
MACHINE="$(uname -m)"
ARCH="arm64"
if [[ "$MACHINE" == "x86_64" ]]; then
  ARCH="x64"
fi
BUILD_VENV="$ROOT/build/packaging-venv-$ARCH"
BROWSER_DIR="$ROOT/build/playwright-browsers"
DIST_DIR="$ROOT/dist"
WORK_DIR="$ROOT/build/pyinstaller-work-$ARCH"
STAGE_DIR="$ROOT/build/2026-10-25-dmg-stage-$$"
VERSION="$($PYTHON_BIN -c 'import pathlib,tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')"
APP_NAME="Canvas Lecture AI"
DMG_PATH="$DIST_DIR/Canvas-Lecture-AI-v$VERSION-$ARCH.dmg"

mkdir -p "$ROOT/build" "$DIST_DIR"

if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$BUILD_VENV"
fi

"$BUILD_VENV/bin/python" -m pip install --upgrade pip
"$BUILD_VENV/bin/python" -m pip install -e '.[ai,dev]'

PLAYWRIGHT_BROWSERS_PATH="$BROWSER_DIR" \
  "$BUILD_VENV/bin/python" -m playwright install chromium

"$BUILD_VENV/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --distpath "$DIST_DIR" \
  --workpath "$WORK_DIR" \
  "$ROOT/2026-10-25-dental-lecture-manager.spec"

APP_PATH="$DIST_DIR/$APP_NAME.app"
mkdir -p "$APP_PATH/Contents/Resources"
ditto "$BROWSER_DIR" "$APP_PATH/Contents/Resources/playwright-browsers"
if [[ ! -e "$APP_PATH/Contents/Frameworks/playwright-browsers" ]]; then
  ln -s ../Resources/playwright-browsers "$APP_PATH/Contents/Frameworks/playwright-browsers"
fi
SIGNING_IDENTITY="${MACOS_SIGNING_IDENTITY:--}"
codesign --force --deep --options runtime --sign "$SIGNING_IDENTITY" "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

mkdir -p "$STAGE_DIR"
ditto "$APP_PATH" "$STAGE_DIR/$APP_NAME.app"
if [[ ! -e "$STAGE_DIR/Applications" ]]; then
  ln -s /Applications "$STAGE_DIR/Applications"
fi

hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$STAGE_DIR" \
  -format UDZO \
  -ov \
  "$DMG_PATH"

if [[ "$SIGNING_IDENTITY" != "-" ]]; then
  codesign --force --sign "$SIGNING_IDENTITY" "$DMG_PATH"
fi

if [[ -n "${MACOS_NOTARY_APPLE_ID:-}" && -n "${MACOS_NOTARY_TEAM_ID:-}" && -n "${MACOS_NOTARY_PASSWORD:-}" ]]; then
  xcrun notarytool submit "$DMG_PATH" \
    --apple-id "$MACOS_NOTARY_APPLE_ID" \
    --team-id "$MACOS_NOTARY_TEAM_ID" \
    --password "$MACOS_NOTARY_PASSWORD" \
    --wait
  xcrun stapler staple "$DMG_PATH"
fi

(cd "$DIST_DIR" && shasum -a 256 "$(basename "$DMG_PATH")" > "$(basename "$DMG_PATH").sha256")
printf 'Built %s\n' "$DMG_PATH"
