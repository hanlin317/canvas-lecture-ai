from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.utils import expand_path

APP_SUPPORT_NAME = "Dental Lecture Manager"


class AppConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8017
    database_path: str = "local_data/dental_lecture_manager.sqlite3"
    log_path: str = "local_data/dental_lecture_manager.log"


class CanvasConfig(BaseModel):
    base_url: str = ""
    state_path: str = "local_data/playwright_state/canvas_state.json"
    prefer_api: bool = True
    login_browser_channel: str = "chrome"
    login_profile_dir: str = "local_data/2026-10-24-canvas-browser-profile"
    login_timeout_seconds: int = Field(default=300, ge=30, le=900)


class PathsConfig(BaseModel):
    download_root: str = "~/Documents"
    notebooklm_staging_dir: str = "~/Documents/NotebookLM Staging"
    obsidian_vault: str = ""


class DownloadsConfig(BaseModel):
    preferred_video_quality: str = "highest"
    concurrency: int = Field(default=3, ge=1, le=8)
    overwrite: bool = False
    skip_existing: bool = True


class TranscriptsConfig(BaseModel):
    prefer_official: bool = True
    keep_vtt: bool = True
    include_timestamps: bool = False
    whisper_enabled: bool = False
    whisper_command: str = "mlx_whisper"
    whisper_model: str = "whisper-large-v3-turbo"


class AIConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4.1"
    temperature: float = 0.2
    auto_save_notes: bool = False
    prompt_version: str = "1.0"


class Settings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    canvas: CanvasConfig = Field(default_factory=CanvasConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    downloads: DownloadsConfig = Field(default_factory=DownloadsConfig)
    transcripts: TranscriptsConfig = Field(default_factory=TranscriptsConfig)
    ai: AIConfig = Field(default_factory=AIConfig)

    @property
    def database_path(self) -> Path:
        return expand_path(self.app.database_path)

    @property
    def canvas_state_path(self) -> Path:
        return expand_path(self.canvas.state_path)


def load_settings(path: str | Path | None = None) -> Settings:
    config_path = settings_path(path)
    data = {}
    if config_path.exists():
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    return Settings.model_validate(data)


def settings_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    configured = os.environ.get("DLM_CONFIG")
    if configured:
        return Path(configured)
    if is_packaged():
        return application_support_dir() / "config.toml"
    return Path("config.toml")


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def application_support_dir() -> Path:
    override = os.environ.get("DLM_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / APP_SUPPORT_NAME


def bundled_resource(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root / name


def prepare_runtime_environment() -> Path | None:
    """Prepare writable per-user paths and bundled tools for the macOS app."""
    if not is_packaged():
        return None

    data_dir = application_support_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DLM_CONFIG", str(data_dir / "config.toml"))
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", str(bundled_resource("playwright-browsers"))
    )

    config_path = settings_path()
    if not config_path.exists():
        example = bundled_resource("config.example.toml")
        if not example.exists():
            raise RuntimeError("The packaged default configuration is missing.")
        shutil.copy2(example, config_path)

    if "DLM_FFMPEG" not in os.environ:
        for candidate in bundled_resource("ffmpeg").glob("ffmpeg*"):
            if candidate.is_file():
                os.environ["DLM_FFMPEG"] = str(candidate)
                break

    os.chdir(data_dir)
    return data_dir


def save_web_settings(values: dict[str, Any], path: str | Path | None = None) -> Settings:
    allowed = {
        "canvas.base_url",
        "paths.download_root",
        "paths.notebooklm_staging_dir",
        "paths.obsidian_vault",
        "ai.provider",
        "ai.model",
        "transcripts.whisper_enabled",
        "transcripts.whisper_model",
    }
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"Unsupported setting(s): {', '.join(sorted(unknown))}")

    config_path = settings_path(path)
    source = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    data = tomllib.loads(source) if source.strip() else {}
    for dotted_key, value in values.items():
        section, key = dotted_key.split(".", 1)
        data.setdefault(section, {})[key] = value
    validated = Settings.model_validate(data)

    updated = _replace_toml_values(source, values)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_path.with_suffix(config_path.suffix + ".part")
    temporary.write_text(updated, encoding="utf-8")
    temporary.replace(config_path)
    return validated


def _replace_toml_values(source: str, values: dict[str, Any]) -> str:
    lines = source.splitlines()
    grouped: dict[str, dict[str, Any]] = {}
    for dotted_key, value in values.items():
        section, key = dotted_key.split(".", 1)
        grouped.setdefault(section, {})[key] = value

    for section, section_values in grouped.items():
        header_pattern = re.compile(rf"^\s*\[{re.escape(section)}\]\s*(?:#.*)?$")
        header_index = next(
            (index for index, line in enumerate(lines) if header_pattern.match(line)),
            None,
        )
        if header_index is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"[{section}]")
            lines.extend(f"{key} = {_toml_literal(value)}" for key, value in section_values.items())
            continue

        section_end = next(
            (
                index
                for index in range(header_index + 1, len(lines))
                if re.match(r"^\s*\[[^]]+\]", lines[index])
            ),
            len(lines),
        )
        for key, value in section_values.items():
            key_pattern = re.compile(
                rf"^(?P<prefix>\s*{re.escape(key)}\s*=\s*)(?P<value>.*?)(?P<comment>\s+#.*)?$"
            )
            key_index = next(
                (
                    index
                    for index in range(header_index + 1, section_end)
                    if key_pattern.match(lines[index])
                ),
                None,
            )
            if key_index is None:
                lines.insert(section_end, f"{key} = {_toml_literal(value)}")
                section_end += 1
                continue
            match = key_pattern.match(lines[key_index])
            if match:
                lines[key_index] = (
                    f"{match.group('prefix')}{_toml_literal(value)}{match.group('comment') or ''}"
                )
    return "\n".join(lines).rstrip() + "\n"


def _toml_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    raise TypeError(f"Unsupported TOML value: {type(value).__name__}")
