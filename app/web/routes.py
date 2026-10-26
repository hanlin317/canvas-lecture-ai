from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import plistlib
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from packaging.version import InvalidVersion, Version

from app import __version__
from app.ai.factory import (
    AI_PROVIDER_SPECS,
    api_key_status,
    credential_store_label,
    fetch_provider_models,
    get_ai_provider_spec,
    save_api_key,
)
from app.ai.prompts import NOTE_LENGTHS
from app.canvas.client import CanvasClient
from app.config import Settings, load_settings, save_web_settings
from app.database import Database
from app.models import JobRecord, PipelineOptions
from app.services.discovery import DiscoveryService, _default_course_group
from app.services.pipeline import PipelineService
from app.transcripts.whisper import local_whisper_available, local_whisper_engine
from app.utils import expand_path

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter()
logger = logging.getLogger(__name__)
ACTIVE_JOBS: dict[str, asyncio.Task] = {}
UPDATE_PUBLIC_KEY_B64 = "/u/PyTFPDYT02PQQRIsNDXMLzvcFWgutkZvPKm/p1UI="
UPDATE_STATE: dict[str, object] = {"status": "idle", "progress_percent": 0}
UPDATE_TASK: asyncio.Task | None = None
CANVAS_INSTITUTIONS = (
    {
        "id": "sydney",
        "name": "University of Sydney",
        "url": "https://canvas.sydney.edu.au",
    },
    {
        "id": "uts",
        "name": "University of Technology Sydney",
        "url": "https://canvas.uts.edu.au",
    },
    {
        "id": "melbourne",
        "name": "University of Melbourne",
        "url": "https://canvas.lms.unimelb.edu.au",
    },
    {"id": "monash", "name": "Monash University", "url": "https://lms.monash.edu"},
    {"id": "rmit", "name": "RMIT University", "url": "https://rmit.instructure.com"},
)


def get_settings() -> Settings:
    return load_settings()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def update_job_progress(
    db: Database, job_id: str, message: str, completed: int, total: int
) -> None:
    db.update_job(
        job_id,
        current_step=message,
        completed_steps=completed,
        total_steps=max(1, total),
        updated_at=utc_now(),
    )


def provider_models_for_display(provider_name: str, db: Database) -> tuple[list[dict], str | None]:
    spec = get_ai_provider_spec(provider_name)
    cached = db.list_provider_models(provider_name)
    if cached:
        return (
            [{"id": item["model_id"], "label": item["label"]} for item in cached],
            cached[0]["refreshed_at"],
        )
    return ([{"id": item.id, "label": item.label} for item in spec.models], None)


def validated_ai_model(provider_name: str, model: str, db: Database) -> str:
    spec = get_ai_provider_spec(provider_name)
    selected = model.strip() or spec.default_model
    models, _ = provider_models_for_display(provider_name, db)
    if selected not in {item["id"] for item in models}:
        raise ValueError(f"{selected} is not an available model for {spec.label}.")
    return selected


def selected_canvas_institution(canvas_url: str) -> str:
    normalized = canvas_url.strip().rstrip("/")
    return next(
        (item["id"] for item in CANVAS_INSTITUTIONS if item["url"] == normalized),
        "other",
    )


def platform_asset_key() -> str:
    return "windows-x64" if sys.platform == "win32" else "macos-arm64"


def platform_display_name() -> str:
    return "Windows" if sys.platform == "win32" else "macOS"


def whisper_display_name() -> str:
    return "Faster Whisper" if sys.platform == "win32" else "MLX Whisper"


def _updates_dir() -> Path:
    path = expand_path("local_data/updates")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _update_cache_path() -> Path:
    return _updates_dir() / "2026-10-25-update-status.json"


def _read_update_cache(max_age_seconds: int = 86400) -> dict | None:
    path = _update_cache_path()
    if not path.exists() or time.time() - path.stat().st_mtime > max_age_seconds:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_update_cache(payload: dict) -> None:
    path = _update_cache_path()
    temporary = path.with_suffix(".json.part")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def _verify_update_manifest(manifest_bytes: bytes, signature_bytes: bytes) -> dict:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(UPDATE_PUBLIC_KEY_B64, validate=True)
        )
        public_key.verify(signature_bytes, manifest_bytes)
    except (InvalidSignature, ValueError) as exc:
        raise RuntimeError("The update manifest signature is invalid.") from exc
    try:
        manifest = json.loads(manifest_bytes)
        Version(str(manifest["version"]))
        platforms = manifest.get("platforms")
        legacy_macos = all(manifest.get(key) for key in ("dmg_url", "sha256", "app_name"))
        if platforms is not None and not isinstance(platforms, dict):
            raise ValueError("Update platforms must be an object.")
        if not platforms and not legacy_macos:
            raise ValueError("Update manifest is incomplete.")
    except (KeyError, InvalidVersion, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("The signed update manifest is malformed.") from exc
    return manifest


def _select_platform_update(manifest: dict) -> dict:
    key = platform_asset_key()
    platforms = manifest.get("platforms")
    if isinstance(platforms, dict):
        asset = platforms.get(key)
        if not isinstance(asset, dict) or not all(
            asset.get(field) for field in ("download_url", "file_name", "sha256", "size")
        ):
            raise RuntimeError(f"This release does not contain an update for {platform_display_name()}.")
        return {**manifest, **asset, "platform": key}
    if key == "macos-arm64":
        return {
            **manifest,
            "download_url": manifest["dmg_url"],
            "file_name": Path(urlparse(str(manifest["dmg_url"])).path).name,
            "installer_kind": "dmg",
            "platform": key,
        }
    raise RuntimeError("This release predates Windows update support.")


async def _fetch_signed_update_manifest(settings: Settings, *, force: bool = False) -> dict:
    if not force:
        cached = _read_update_cache()
        if cached:
            return cached
    repository = settings.updates.repository
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"Canvas-Lecture-AI/{__version__}",
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
        release_response = await client.get(
            f"https://api.github.com/repos/{repository}/releases/latest"
        )
        release_response.raise_for_status()
        release = release_response.json()
        assets = {asset.get("name"): asset for asset in release.get("assets", [])}
        manifest_asset = next(
            (asset for name, asset in assets.items() if str(name).endswith("-update.json")),
            None,
        )
        signature_asset = next(
            (asset for name, asset in assets.items() if str(name).endswith("-update.json.sig")),
            None,
        )
        if not manifest_asset or not signature_asset:
            raise RuntimeError("The latest release does not contain a signed update manifest.")
        manifest_response, signature_response = await asyncio.gather(
            client.get(manifest_asset["browser_download_url"]),
            client.get(signature_asset["browser_download_url"]),
        )
        manifest_response.raise_for_status()
        signature_response.raise_for_status()

    manifest = _select_platform_update(
        _verify_update_manifest(manifest_response.content, signature_response.content)
    )
    latest = Version(str(manifest["version"]))
    current = Version(__version__)
    payload = {
        **manifest,
        "current_version": __version__,
        "available": latest > current,
        "release_url": manifest.get("release_url") or release.get("html_url", ""),
        "checked_at": utc_now(),
    }
    _write_update_cache(payload)
    return payload


async def _download_update(manifest: dict) -> None:
    global UPDATE_STATE
    destination = _updates_dir() / str(manifest["file_name"])
    temporary = destination.with_name(destination.name + ".part")
    expected_size = int(manifest.get("size") or 0)
    downloaded = 0
    digest = hashlib.sha256()
    UPDATE_STATE = {
        **manifest,
        "status": "downloading",
        "progress_percent": 0,
    }
    try:
        async with (
            httpx.AsyncClient(timeout=None, follow_redirects=True) as client,
            client.stream("GET", str(manifest["download_url"])) as response,
        ):
            response.raise_for_status()
            total = expected_size or int(response.headers.get("content-length") or 0)
            with temporary.open("wb") as handle:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    handle.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    UPDATE_STATE["progress_percent"] = (
                        round(downloaded * 100 / total) if total else 0
                    )
        if digest.hexdigest().lower() != str(manifest["sha256"]).lower():
            raise RuntimeError("Downloaded update checksum did not match the signed manifest.")
        temporary.replace(destination)
        UPDATE_STATE = {
            **manifest,
            "status": "downloaded",
            "progress_percent": 100,
            "download_path": str(destination),
        }
    except (httpx.HTTPError, OSError, RuntimeError) as exc:
        logger.exception("Update download failed")
        UPDATE_STATE = {
            **manifest,
            "status": "failed",
            "progress_percent": 0,
            "error": str(exc),
        }


def _current_app_path() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    if sys.platform == "win32":
        return executable
    return executable.parents[2] if len(executable.parents) >= 3 else None


def _mount_and_validate_update(dmg_path: Path, manifest: dict) -> None:
    mount_dir = _updates_dir() / f"2026-10-25-update-mount-{os.getpid()}"
    mount_dir.mkdir(parents=True, exist_ok=False)
    attached = False
    try:
        subprocess.run(
            [
                "hdiutil",
                "attach",
                "-nobrowse",
                "-readonly",
                "-mountpoint",
                str(mount_dir),
                str(dmg_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        attached = True
        candidate = mount_dir / str(manifest["app_name"])
        if not candidate.is_dir():
            raise RuntimeError("The update DMG does not contain the expected application.")
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(candidate)],
            check=True,
            capture_output=True,
            text=True,
        )
        with (candidate / "Contents" / "Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
        if info.get("CFBundleIdentifier") != "com.hanlin317.dentallecturemanager":
            raise RuntimeError("The update has an unexpected application identity.")
        if Version(str(info.get("CFBundleShortVersionString"))) != Version(
            str(manifest["version"])
        ):
            raise RuntimeError("The application version does not match the signed manifest.")
    finally:
        if attached:
            subprocess.run(
                ["hdiutil", "detach", str(mount_dir)],
                check=False,
                capture_output=True,
            )


def _write_update_installer(dmg_path: Path, manifest: dict, current_app: Path) -> Path:
    version = str(manifest["version"])
    destination = current_app.parent / str(manifest["app_name"])
    backup = current_app.parent / f"{current_app.name}.previous-v{__version__}"
    script = _updates_dir() / f"2026-10-25-install-v{version}.sh"
    values = {
        "DMG": dmg_path,
        "CURRENT": current_app,
        "DESTINATION": destination,
        "BACKUP": backup,
        "APP_NAME": str(manifest["app_name"]),
        "PID_TO_STOP": os.getpid(),
    }
    assignments = "\n".join(f"{key}={shlex.quote(str(value))}" for key, value in values.items())
    script.write_text(
        f"""#!/bin/zsh
set -euo pipefail
{assignments}
sleep 1
kill -TERM "$PID_TO_STOP" 2>/dev/null || true
for attempt in {{1..80}}; do
  kill -0 "$PID_TO_STOP" 2>/dev/null || break
  sleep 0.25
done
if [[ -e "$BACKUP" ]]; then
  BACKUP="$BACKUP-$(date +%Y%m%d%H%M%S)"
fi
if [[ "$DESTINATION" != "$CURRENT" && -e "$DESTINATION" ]]; then
  exit 3
fi
MOUNT_DIR=$(mktemp -d /tmp/2026-10-25-canvas-lecture-ai-update.XXXXXX)
hdiutil attach -nobrowse -readonly -mountpoint "$MOUNT_DIR" "$DMG" >/dev/null
SOURCE="$MOUNT_DIR/$APP_NAME"
mv "$CURRENT" "$BACKUP"
if ditto "$SOURCE" "$DESTINATION" && codesign --verify --deep --strict "$DESTINATION"; then
  hdiutil detach "$MOUNT_DIR" >/dev/null || true
  open "$DESTINATION"
  exit 0
fi
if [[ ! -e "$CURRENT" ]]; then mv "$BACKUP" "$CURRENT"; fi
hdiutil detach "$MOUNT_DIR" >/dev/null || true
open "$CURRENT"
exit 4
""",
        encoding="utf-8",
    )
    script.chmod(0o700)
    return script


async def _launch_update_installer(script: Path, needs_authorization: bool) -> None:
    await asyncio.sleep(1)
    if needs_authorization:
        command = shlex.quote(str(script))
        await asyncio.to_thread(
            subprocess.Popen,
            [
                "osascript",
                "-e",
                f"do shell script {json.dumps(command)} with administrator privileges",
            ],
            start_new_session=True,
        )
    else:
        await asyncio.to_thread(subprocess.Popen, [str(script)], start_new_session=True)


async def _launch_windows_installer(installer: Path) -> None:
    await asyncio.sleep(1)
    await asyncio.to_thread(
        subprocess.Popen,
        [
            str(installer),
            "/SILENT",
            "/CLOSEAPPLICATIONS",
            "/RESTARTAPPLICATIONS",
        ],
        start_new_session=True,
    )


@router.get("/updates/status")
async def update_status(
    force: bool = False,
    settings: Settings = Depends(get_settings),
) -> dict:
    global UPDATE_TASK, UPDATE_STATE
    preferences = {
        "check_automatically": settings.updates.check_automatically,
        "download_automatically": settings.updates.download_automatically,
        "install_automatically": settings.updates.install_automatically,
    }
    if not force and not settings.updates.check_automatically:
        return {
            "status": "disabled",
            "current_version": __version__,
            **preferences,
        }
    if UPDATE_STATE.get("status") in {"downloading", "downloaded", "installing", "failed"}:
        return {**UPDATE_STATE, **preferences}
    try:
        manifest = await _fetch_signed_update_manifest(settings, force=force)
    except (httpx.HTTPError, OSError, RuntimeError) as exc:
        logger.warning("Update check failed: %s", exc)
        return {
            "status": "check_failed",
            "current_version": __version__,
            "error": str(exc),
            **preferences,
        }
    UPDATE_STATE = {
        **manifest,
        "status": "available" if manifest["available"] else "current",
        "progress_percent": 0,
    }
    if manifest["available"] and settings.updates.download_automatically:
        UPDATE_TASK = asyncio.create_task(_download_update(manifest))
        UPDATE_STATE["status"] = "downloading"
    return {**UPDATE_STATE, **preferences}


@router.post("/updates/download")
async def download_update(settings: Settings = Depends(get_settings)) -> dict:
    global UPDATE_TASK, UPDATE_STATE
    if UPDATE_TASK and not UPDATE_TASK.done():
        return UPDATE_STATE
    manifest = await _fetch_signed_update_manifest(settings, force=True)
    if not manifest["available"]:
        return {**manifest, "status": "current", "progress_percent": 100}
    UPDATE_STATE = {**manifest, "status": "downloading", "progress_percent": 0}
    UPDATE_TASK = asyncio.create_task(_download_update(manifest))
    return UPDATE_STATE


@router.post("/updates/install")
async def install_update() -> dict:
    if UPDATE_STATE.get("status") != "downloaded":
        raise HTTPException(status_code=409, detail="Download the update before installing it.")
    current_app = _current_app_path()
    if current_app is None:
        raise HTTPException(
            status_code=409,
            detail="Automatic installation is available only in the packaged application.",
        )
    download_path = Path(str(UPDATE_STATE.get("download_path") or ""))
    if not download_path.is_file():
        raise HTTPException(status_code=409, detail="The downloaded update could not be found.")
    if sys.platform == "win32":
        if download_path.suffix.lower() != ".exe":
            raise HTTPException(status_code=409, detail="The Windows update is not an installer.")
        UPDATE_STATE["status"] = "installing"
        asyncio.create_task(_launch_windows_installer(download_path))
        return {"status": "installing", "version": UPDATE_STATE["version"]}
    try:
        await asyncio.to_thread(_mount_and_validate_update, download_path, UPDATE_STATE)
        installer = _write_update_installer(download_path, UPDATE_STATE, current_app)
    except (OSError, RuntimeError, subprocess.SubprocessError, InvalidVersion) as exc:
        logger.exception("Could not prepare app update")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    UPDATE_STATE["status"] = "installing"
    needs_authorization = not os.access(current_app.parent, os.W_OK)
    asyncio.create_task(_launch_update_installer(installer, needs_authorization))
    return {"status": "installing", "version": UPDATE_STATE["version"]}


def mark_job_cancelled(db: Database, job_id: str) -> None:
    finished_at = utc_now()
    db.update_job(
        job_id,
        status="cancelled",
        current_step="Job stopped",
        message="Job stopped by user.",
        updated_at=finished_at,
        finished_at=finished_at,
    )


async def run_discovery_job(
    job_id: str, course_code: str, settings: Settings, db: Database
) -> None:
    task = asyncio.current_task()
    if task is not None:
        ACTIVE_JOBS[job_id] = task
    job = db.get_job(job_id)
    if job and job.status == "cancelled":
        ACTIVE_JOBS.pop(job_id, None)
        return
    started_at = utc_now()
    db.update_job(job_id, status="running", started_at=started_at, updated_at=started_at)
    try:
        result = await DiscoveryService(db, settings).discover_course(
            course_code,
            progress=lambda message, completed, total: update_job_progress(
                db, job_id, message, completed, total
            ),
        )
        discovered_course = result.get("course")
        display_code = discovered_course.code if discovered_course else course_code
        message = f"Discovery finished for {display_code}: {result['recordings']} recording(s)."
        finished_at = utc_now()
        db.update_job(
            job_id,
            status="completed",
            current_step="Discovery complete",
            completed_steps=6,
            total_steps=6,
            message=message,
            updated_at=finished_at,
            finished_at=finished_at,
        )
    except asyncio.CancelledError:
        mark_job_cancelled(db, job_id)
    except Exception as exc:
        logger.exception("Course discovery failed")
        finished_at = utc_now()
        db.update_job(
            job_id,
            status="failed",
            current_step="Discovery failed",
            error_message=str(exc),
            updated_at=finished_at,
            finished_at=finished_at,
        )
    finally:
        ACTIVE_JOBS.pop(job_id, None)


def pipeline_summary(lecture_count: int, results: list[dict]) -> str:
    stage_statuses = [
        value
        for result in results
        for key, value in result.items()
        if key in {"video", "slides", "transcript", "notebooklm", "ai_notes"}
    ]
    ready = sum(
        status in {"complete", "skipped"}
        or (isinstance(status, str) and status not in {"unavailable", "failed"})
        for status in stage_statuses
    )
    unavailable = stage_statuses.count("unavailable")
    failed = stage_statuses.count("failed")
    lecture_errors = sum(bool(result.get("error")) for result in results)
    return (
        f"Processed {lecture_count} lecture(s): {lecture_count - lecture_errors} completed, "
        f"{lecture_errors} stopped by errors; {ready} output(s) ready, "
        f"{unavailable} unavailable, {failed} failed."
    )


async def run_pipeline_job(
    job_id: str,
    lecture_ids: list[int],
    options: PipelineOptions,
    settings: Settings,
    db: Database,
) -> None:
    task = asyncio.current_task()
    if task is not None:
        ACTIVE_JOBS[job_id] = task
    job = db.get_job(job_id)
    if job and job.status == "cancelled":
        ACTIVE_JOBS.pop(job_id, None)
        return
    started_at = utc_now()
    db.update_job(job_id, status="running", started_at=started_at, updated_at=started_at)
    try:
        results = await PipelineService(db, settings).run_many(
            lecture_ids,
            options,
            progress=lambda message, completed, total: update_job_progress(
                db, job_id, message, completed, total
            ),
        )
        message = pipeline_summary(len(lecture_ids), results)
        finished_at = utc_now()
        total_steps = PipelineService.stage_count(options) * len(lecture_ids)
        db.update_job(
            job_id,
            status="completed",
            current_step="Pipeline complete",
            completed_steps=total_steps,
            total_steps=max(1, total_steps),
            message=message,
            updated_at=finished_at,
            finished_at=finished_at,
        )
    except asyncio.CancelledError:
        mark_job_cancelled(db, job_id)
    except Exception as exc:
        logger.exception("Pipeline run failed")
        finished_at = utc_now()
        db.update_job(
            job_id,
            status="failed",
            current_step="Pipeline failed",
            error_message=str(exc),
            updated_at=finished_at,
            finished_at=finished_at,
        )
    finally:
        ACTIVE_JOBS.pop(job_id, None)


def format_lecture_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%d/%m/%Y")
    except ValueError:
        return value


templates.env.filters["lecture_date"] = format_lecture_date


def get_db(settings: Settings = Depends(get_settings)) -> Database:
    return Database(settings.database_path)


def redirect_home(**params: str) -> RedirectResponse:
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"/{suffix}", status_code=303)


def redirect_setup(step: int, **params: str) -> RedirectResponse:
    params = {"step": str(max(1, min(7, step))), **params}
    return RedirectResponse(f"/setup?{urlencode(params)}", status_code=303)


def selected_course_from_request(courses, request: Request):
    requested = request.query_params.get("course")
    if requested:
        for course in courses:
            if (
                str(course.id) == requested
                or course.canvas_id == requested
                or course.code == requested
            ):
                return course
    return courses[0] if courses else None


def grouped_courses_for_display(courses) -> list[dict]:
    buckets: dict[tuple[bool, str], list] = {}
    for course in courses:
        group_name = course.group_name or _default_course_group(course.code, course.name)
        buckets.setdefault((course.is_active, group_name), []).append(course)
    groups = []
    for (is_active, group_name), grouped_courses in sorted(
        buckets.items(),
        key=lambda item: (0 if item[0][0] else 1, item[0][1].lower()),
    ):
        state = "Active" if is_active else "Saved"
        groups.append(
            {
                "label": f"{state} - {group_name}",
                "courses": sorted(grouped_courses, key=lambda course: (course.code, course.name)),
            }
        )
    return groups


def grouped_processing_for_display(records: list[dict]) -> list[dict]:
    lectures: dict[int, dict] = {}
    for record in records:
        lecture_id = int(record["lecture_id"])
        group = lectures.setdefault(
            lecture_id,
            {
                "title": record["custom_title"] or record["original_title"],
                "stages": [],
            },
        )
        group["stages"].append(record)
    return list(lectures.values())


def obsidian_vault_status(value: str) -> dict[str, str]:
    if not value.strip():
        return {"kind": "warn", "text": "Not configured"}
    path = Path(value).expanduser()
    if not path.is_dir():
        return {"kind": "error", "text": "Folder not found"}
    if not (path / ".obsidian").is_dir():
        return {"kind": "warn", "text": "Obsidian vault marker not found"}
    return {"kind": "ok", "text": "Vault detected"}


def validated_directory(
    value: str,
    label: str,
    *,
    obsidian: bool = False,
    create: bool = False,
) -> str:
    stripped = value.strip()
    if obsidian and not stripped:
        return ""
    if not stripped:
        raise ValueError(f"{label} is required.")
    path = Path(stripped).expanduser().resolve()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError(f"{label} folder does not exist: {path}")
    if obsidian and not (path / ".obsidian").is_dir():
        raise ValueError(f"The selected folder is not an Obsidian vault: {path}")
    return str(path)


def validated_canvas_url(value: str) -> str:
    stripped = value.strip().rstrip("/")
    parsed = urlparse(stripped)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Canvas URL must be a complete http:// or https:// address.")
    return stripped


async def choose_directory(prompt: str) -> str | None:
    if sys.platform == "darwin":
        command = ("osascript", "-e", f'POSIX path of (choose folder with prompt "{prompt}")')
    elif sys.platform == "win32":
        escaped_prompt = prompt.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
            f"$dialog.Description = '{escaped_prompt}'; "
            "if ($dialog.ShowDialog() -eq 'OK') { Write-Output $dialog.SelectedPath }"
        )
        command = ("powershell.exe", "-NoProfile", "-STA", "-Command", script)
    else:
        raise RuntimeError("Folder selection is supported on macOS and Windows.")
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        message = stderr.decode(errors="replace").strip()
        if "User canceled" in message:
            return None
        raise RuntimeError(message or "Could not open the folder picker.")
    selected = stdout.decode().strip()
    if not selected:
        return None
    return selected.rstrip("/") or "/"


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request, settings: Settings = Depends(get_settings), db: Database = Depends(get_db)
) -> HTMLResponse:
    if not settings.app.onboarding_complete:
        return RedirectResponse("/setup", status_code=303)
    courses = db.list_courses()
    selected_course = selected_course_from_request(courses, request)
    topics = db.list_topics(selected_course.id) if selected_course and selected_course.id else []
    lectures = (
        db.list_lectures(selected_course.id) if selected_course and selected_course.id else []
    )
    recent_processing = (
        db.list_recent_processing(selected_course.id)
        if selected_course and selected_course.id
        else []
    )
    processing_groups = grouped_processing_for_display(recent_processing)
    lectures_by_topic: dict[int | None, list] = {}
    for lecture in lectures:
        lectures_by_topic.setdefault(lecture.topic_id, []).append(lecture)
    lecture_groups = [
        {"topic": topic, "lectures": lectures_by_topic[topic.id]}
        for topic in topics
        if topic.id in lectures_by_topic
    ]
    if lectures_by_topic.get(None):
        lecture_groups.append({"topic": None, "lectures": lectures_by_topic[None]})
    canvas = CanvasClient(settings)
    canvas_session_status = "saved" if canvas.has_session() else "not saved"
    setup_complete = bool(
        settings.canvas.base_url and Path(settings.paths.download_root).expanduser().is_dir()
    )
    ai_providers = []
    for spec in AI_PROVIDER_SPECS:
        models, refreshed_at = provider_models_for_display(spec.name, db)
        ai_providers.append(
            {
                "name": spec.name,
                "label": spec.label,
                "default_model": (
                    spec.default_model
                    if spec.default_model in {item["id"] for item in models}
                    else models[0]["id"]
                ),
                "models": models,
                "models_refreshed_at": refreshed_at,
                "key_status": api_key_status(spec.name),
                "requires_key": spec.requires_key,
            }
        )
    try:
        requested_provider = request.query_params.get("model_provider") or settings.ai.provider
        configured_provider = get_ai_provider_spec(requested_provider)
    except ValueError:
        configured_provider = AI_PROVIDER_SPECS[0]
    configured_models = next(
        provider["models"]
        for provider in ai_providers
        if provider["name"] == configured_provider.name
    )
    default_ai_model = (
        settings.ai.model
        if settings.ai.model in {item["id"] for item in configured_models}
        else next(
            provider["default_model"]
            for provider in ai_providers
            if provider["name"] == configured_provider.name
        )
    )
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "settings": settings,
            "courses": courses,
            "course_groups": grouped_courses_for_display(courses),
            "selected_course": selected_course,
            "topics": topics,
            "lectures": lectures,
            "lecture_groups": lecture_groups,
            "recent_processing": recent_processing,
            "processing_groups": processing_groups,
            "canvas_session_status": canvas_session_status,
            "setup_complete": setup_complete,
            "ai_providers": ai_providers,
            "ai_provider_models": {
                provider["name"]: provider["models"] for provider in ai_providers
            },
            "ai_provider_refresh": {
                provider["name"]: provider["models_refreshed_at"] for provider in ai_providers
            },
            "default_ai_provider": configured_provider.name,
            "default_ai_model": default_ai_model,
            "obsidian_vault_status": obsidian_vault_status(settings.paths.obsidian_vault),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "job_id": request.query_params.get("job"),
            "current_version": __version__,
            "whisper_available": local_whisper_available(settings.transcripts.whisper_command),
            "whisper_engine": local_whisper_engine(settings.transcripts.whisper_command),
            "whisper_name": whisper_display_name(),
            "platform_name": platform_display_name(),
            "credential_store": credential_store_label(),
            "env": {
                "OPENAI_API_KEY": bool(__import__("os").environ.get("OPENAI_API_KEY")),
                "ANTHROPIC_API_KEY": bool(__import__("os").environ.get("ANTHROPIC_API_KEY")),
                "GEMINI_API_KEY": bool(__import__("os").environ.get("GEMINI_API_KEY")),
            },
        },
    )


@router.get("/setup", response_class=HTMLResponse)
async def onboarding(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> HTMLResponse:
    requested_step = request.query_params.get("step")
    try:
        step = int(requested_step) if requested_step else settings.app.onboarding_step
    except ValueError:
        step = settings.app.onboarding_step
    step = max(1, min(7, step))
    canvas = CanvasClient(settings)
    providers = []
    for spec in AI_PROVIDER_SPECS:
        models, _ = provider_models_for_display(spec.name, db)
        providers.append(
            {
                "name": spec.name,
                "label": spec.label,
                "default_model": spec.default_model,
                "models": models,
                "requires_key": spec.requires_key,
                "key_status": api_key_status(spec.name),
            }
        )
    selected_provider = next(
        (item for item in providers if item["name"] == settings.ai.provider), providers[0]
    )
    selected_model = settings.ai.model
    if selected_model not in {item["id"] for item in selected_provider["models"]}:
        selected_model = selected_provider["default_model"]
    return templates.TemplateResponse(
        request=request,
        name="2026-10-26-onboarding.html",
        context={
            "request": request,
            "settings": settings,
            "step": step,
            "providers": providers,
            "canvas_institutions": CANVAS_INSTITUTIONS,
            "selected_canvas_institution": selected_canvas_institution(
                settings.canvas.base_url
            ),
            "provider_models": {item["name"]: item["models"] for item in providers},
            "selected_provider": selected_provider["name"],
            "selected_model": selected_model,
            "canvas_session_saved": canvas.has_session(),
            "courses": db.list_courses(),
            "whisper_available": local_whisper_available(settings.transcripts.whisper_command),
            "whisper_engine": local_whisper_engine(settings.transcripts.whisper_command),
            "whisper_name": whisper_display_name(),
            "platform_name": platform_display_name(),
            "credential_store": credential_store_label(),
            "obsidian_vault_status": obsidian_vault_status(settings.paths.obsidian_vault),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/setup/advance")
async def advance_onboarding(next_step: int = Form(default=1)) -> RedirectResponse:
    step = max(1, min(7, next_step))
    save_web_settings({"app.onboarding_complete": False, "app.onboarding_step": step})
    return redirect_setup(step)


@router.post("/setup/storage")
async def save_onboarding_storage(
    canvas_base_url: str = Form(default=""),
    download_root: str = Form(default=""),
    notebooklm_staging_dir: str = Form(default=""),
    obsidian_vault: str = Form(default=""),
) -> RedirectResponse:
    try:
        save_web_settings(
            {
                "canvas.base_url": validated_canvas_url(canvas_base_url),
                "paths.download_root": validated_directory(
                    download_root, "Download folder", create=True
                ),
                "paths.notebooklm_staging_dir": validated_directory(
                    notebooklm_staging_dir, "NotebookLM sources folder", create=True
                ),
                "paths.obsidian_vault": validated_directory(
                    obsidian_vault, "Obsidian vault", obsidian=True
                ),
                "app.onboarding_complete": False,
                "app.onboarding_step": 3,
            }
        )
    except Exception as exc:
        logger.exception("Could not save onboarding storage settings")
        return redirect_setup(2, error=str(exc))
    return redirect_setup(3, message="Storage settings saved.")


@router.post("/setup/ai")
async def save_onboarding_ai(
    ai_enabled: bool = Form(default=False),
    ai_provider: str = Form(default="openai"),
    ai_model: str = Form(default=""),
    ai_note_length: str = Form(default="standard"),
    ai_custom_instructions: str = Form(default=""),
    api_key: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> RedirectResponse:
    try:
        provider_name = settings.ai.provider
        model = settings.ai.model
        if ai_enabled:
            provider = get_ai_provider_spec(ai_provider)
            provider_name = provider.name
            model = validated_ai_model(provider.name, ai_model, db)
            if api_key.strip():
                save_api_key(provider.name, api_key)
        instructions = settings.ai.custom_instructions
        if ai_enabled:
            if ai_note_length not in NOTE_LENGTHS:
                raise ValueError("Unknown default AI note length.")
            instructions = ai_custom_instructions.strip()
            if len(instructions) > 6000:
                raise ValueError("Custom AI instructions must be 6,000 characters or fewer.")
        else:
            ai_note_length = settings.ai.note_length
        save_web_settings(
            {
                "ai.enabled": ai_enabled,
                "ai.provider": provider_name,
                "ai.model": model,
                "ai.note_length": ai_note_length,
                "ai.custom_instructions": instructions,
                "app.onboarding_complete": False,
                "app.onboarding_step": 4,
            }
        )
    except Exception as exc:
        logger.exception("Could not save onboarding AI settings")
        return redirect_setup(3, error=str(exc))
    return redirect_setup(4, message="AI note settings saved.")


@router.post("/setup/transcription")
async def save_onboarding_transcription(
    whisper_enabled: bool = Form(default=False),
    whisper_model: str = Form(default=""),
) -> RedirectResponse:
    model = whisper_model.strip()
    if whisper_enabled and not model:
        return redirect_setup(4, error="Choose a Whisper model or turn local transcription off.")
    save_web_settings(
        {
            "transcripts.whisper_enabled": whisper_enabled,
            "transcripts.whisper_model": model or "large-v3-turbo",
            "app.onboarding_complete": False,
            "app.onboarding_step": 5,
        }
    )
    return redirect_setup(5, message="Transcription settings saved.")


@router.post("/setup/login")
async def onboarding_login(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    try:
        await CanvasClient(settings).login()
        save_web_settings({"app.onboarding_complete": False, "app.onboarding_step": 6})
    except Exception as exc:
        logger.exception("Canvas onboarding login failed")
        return redirect_setup(5, error=str(exc))
    return redirect_setup(6, message="Canvas login saved.")


@router.post("/setup/sync")
async def onboarding_sync_courses(
    settings: Settings = Depends(get_settings), db: Database = Depends(get_db)
) -> RedirectResponse:
    try:
        courses = await DiscoveryService(db, settings).sync_canvas_courses()
        save_web_settings({"app.onboarding_complete": False, "app.onboarding_step": 7})
    except Exception as exc:
        logger.exception("Canvas onboarding course sync failed")
        return redirect_setup(6, error=str(exc))
    return redirect_setup(7, message=f"Found {len(courses)} Canvas course(s).")


@router.post("/setup/finish")
async def finish_onboarding() -> RedirectResponse:
    save_web_settings({"app.onboarding_complete": True, "app.onboarding_step": 7})
    return redirect_home(message="Setup complete. Canvas Lecture AI is ready.")


@router.post("/setup/restart")
async def restart_onboarding() -> RedirectResponse:
    save_web_settings({"app.onboarding_complete": False, "app.onboarding_step": 1})
    return redirect_setup(1)


@router.post("/login")
async def login(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    try:
        await CanvasClient(settings).login()
    except Exception as exc:
        logger.exception("Canvas login failed")
        return redirect_home(error=str(exc))
    return redirect_home(message="Canvas browser session saved.")


@router.post("/settings/api-key")
async def update_api_key(
    provider: str = Form(default=""),
    api_key: str = Form(default=""),
    course_code: str = Form(default=""),
) -> RedirectResponse:
    try:
        spec = get_ai_provider_spec(provider)
        save_api_key(provider, api_key)
    except Exception as exc:
        logger.exception("Could not save AI provider key")
        return redirect_home(error=str(exc), course=course_code)
    return redirect_home(
        message=f"{spec.label} API key saved in {credential_store_label()}.", course=course_code
    )


@router.post("/settings/app")
async def update_app_settings(
    canvas_base_url: str = Form(default=""),
    download_root: str = Form(default=""),
    notebooklm_staging_dir: str = Form(default=""),
    obsidian_vault: str = Form(default=""),
    ai_enabled: bool = Form(default=False),
    ai_provider: str = Form(default="openai"),
    ai_model: str = Form(default=""),
    ai_note_length: str = Form(default="standard"),
    ai_custom_instructions: str = Form(default=""),
    updates_check_automatically: bool = Form(default=False),
    updates_download_automatically: bool = Form(default=False),
    updates_install_automatically: bool = Form(default=False),
    whisper_enabled: bool = Form(default=False),
    whisper_model: str = Form(default=""),
    course_code: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> RedirectResponse:
    try:
        provider_name = settings.ai.provider
        model = settings.ai.model
        if ai_enabled:
            provider = get_ai_provider_spec(ai_provider)
            provider_name = provider.name
            model = validated_ai_model(provider.name, ai_model, db)
        whisper = whisper_model.strip()
        if whisper_enabled and not whisper:
            raise ValueError("Whisper model is required when Whisper is enabled.")
        custom_instructions = settings.ai.custom_instructions
        if ai_enabled:
            if ai_note_length not in NOTE_LENGTHS:
                raise ValueError("Unknown default AI note length.")
            custom_instructions = ai_custom_instructions.strip()
            if len(custom_instructions) > 6000:
                raise ValueError("Custom AI instructions must be 6,000 characters or fewer.")
        else:
            ai_note_length = settings.ai.note_length
        save_web_settings(
            {
                "canvas.base_url": validated_canvas_url(canvas_base_url),
                "paths.download_root": validated_directory(
                    download_root, "Download folder", create=True
                ),
                "paths.notebooklm_staging_dir": validated_directory(
                    notebooklm_staging_dir, "NotebookLM sources folder", create=True
                ),
                "paths.obsidian_vault": validated_directory(
                    obsidian_vault, "Obsidian vault", obsidian=True
                ),
                "ai.enabled": ai_enabled,
                "ai.provider": provider_name,
                "ai.model": model,
                "ai.note_length": ai_note_length,
                "ai.custom_instructions": custom_instructions,
                "updates.check_automatically": updates_check_automatically,
                "updates.download_automatically": updates_download_automatically,
                "updates.install_automatically": updates_install_automatically,
                "transcripts.whisper_enabled": whisper_enabled,
                "transcripts.whisper_model": (whisper or "large-v3-turbo"),
            }
        )
    except Exception as exc:
        logger.exception("Could not save app configuration")
        return redirect_home(error=str(exc), course=course_code)
    return redirect_home(message="App configuration saved.", course=course_code)


@router.post("/settings/models/refresh")
async def refresh_provider_models(
    ai_provider: str = Form(default="openai"),
    course_code: str = Form(default=""),
    db: Database = Depends(get_db),
) -> RedirectResponse:
    try:
        spec = get_ai_provider_spec(ai_provider)
        models = await fetch_provider_models(spec.name)
        refreshed_at = utc_now()
        db.replace_provider_models(
            spec.name,
            [(item.id, item.label) for item in models],
            refreshed_at,
        )
    except Exception as exc:
        logger.exception("Could not refresh AI provider models")
        return redirect_home(
            error=str(exc),
            course=course_code,
            model_provider=ai_provider,
        )
    return redirect_home(
        message=f"Refreshed {len(models)} compatible {spec.label} model(s).",
        course=course_code,
        model_provider=spec.name,
    )


@router.post("/settings/choose-folder")
async def choose_settings_folder(field: str = Form(default="")) -> JSONResponse:
    prompts = {
        "download_root": "Choose the lecture download folder",
        "notebooklm_staging_dir": "Choose the NotebookLM staging folder",
        "obsidian_vault": "Choose your Obsidian vault",
    }
    if field not in prompts:
        raise HTTPException(status_code=400, detail="Unknown folder field.")
    try:
        selected = await choose_directory(prompts[field])
    except Exception as exc:
        logger.exception("Folder selection failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse({"path": selected, "cancelled": selected is None})


@router.post("/notebooklm/reveal")
async def reveal_notebooklm_sources(
    course_code: str = Form(default=""),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    path = expand_path(settings.paths.notebooklm_staging_dir)
    path.mkdir(parents=True, exist_ok=True)
    command = ("explorer.exe", str(path)) if sys.platform == "win32" else ("open", str(path))
    process = await asyncio.create_subprocess_exec(*command)
    if await process.wait() != 0:
        return redirect_home(
            error="Could not open the NotebookLM sources folder.", course=course_code
        )
    return redirect_home(message="Opened the NotebookLM sources folder.", course=course_code)


@router.post("/courses/sync")
async def sync_courses(
    settings: Settings = Depends(get_settings), db: Database = Depends(get_db)
) -> RedirectResponse:
    try:
        courses = await DiscoveryService(db, settings).sync_canvas_courses()
    except Exception as exc:
        logger.exception("Canvas course sync failed")
        return redirect_home(error=str(exc))
    params = {"message": f"Synced {len(courses)} Canvas course(s)."}
    if courses:
        params["course"] = str(courses[0].id)
    return redirect_home(**params)


@router.post("/courses/group")
async def group_courses(
    provider: str = Form(default="openai"),
    model: str = Form(default=""),
    course: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> RedirectResponse:
    try:
        spec = get_ai_provider_spec(provider)
        selected_model = validated_ai_model(spec.name, model, db)
        grouped = await DiscoveryService(db, settings).group_courses_with_ai(
            spec.name, selected_model
        )
    except Exception as exc:
        logger.exception("AI course grouping failed")
        return redirect_home(error=str(exc), course=course)
    return redirect_home(
        message=f"AI grouping updated {grouped} course(s) with {spec.label}.",
        course=course,
    )


@router.post("/discover")
async def discover(
    background_tasks: BackgroundTasks,
    course: str = Form(default=""),
    course_code: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> RedirectResponse:
    selected_code = (course or course_code).strip()
    if not selected_code:
        return redirect_home(error="Sync Canvas courses first, then choose a course to discover.")
    job_id = str(uuid4())
    db.create_job(
        JobRecord(
            id=job_id,
            kind="discovery",
            course_code=selected_code,
            current_step="Waiting to start course discovery",
            total_steps=6,
        )
    )
    background_tasks.add_task(run_discovery_job, job_id, selected_code, settings, db)
    return redirect_home(course=selected_code, job=job_id)


@router.post("/pipeline")
async def pipeline(
    background_tasks: BackgroundTasks,
    lecture_ids: list[int] = Form(default=[]),
    download_video: bool = Form(default=False),
    download_slides: bool = Form(default=False),
    download_transcript: bool = Form(default=False),
    notebooklm: bool = Form(default=False),
    ai_notes: bool = Form(default=False),
    ai_note_format: str = Form(default="markdown"),
    ai_note_length: str = Form(default="standard"),
    ai_custom_instructions: str = Form(default=""),
    save_obsidian: bool = Form(default=False),
    whisper_fallback: bool = Form(default=False),
    overwrite: bool = Form(default=False),
    ai_provider: str = Form(default="openai"),
    ai_model: str = Form(default=""),
    course_code: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> RedirectResponse:
    if not lecture_ids:
        return redirect_home(
            error="Select at least one lecture before running the pipeline.", course=course_code
        )
    try:
        if ai_notes and not settings.ai.enabled:
            raise ValueError("AI notes are disabled. Enable them in Settings before continuing.")
        provider_spec = get_ai_provider_spec(settings.ai.provider)
        selected_model = settings.ai.model
        if ai_notes:
            provider_spec = get_ai_provider_spec(ai_provider)
            selected_model = validated_ai_model(provider_spec.name, ai_model, db)
            if ai_note_format not in {"markdown", "pdf", "docx"}:
                raise ValueError(f"Unknown AI note format: {ai_note_format}")
            if ai_note_length not in NOTE_LENGTHS:
                raise ValueError(f"Unknown AI note length: {ai_note_length}")
        custom_instructions = ai_custom_instructions.strip()
        if len(custom_instructions) > 6000:
            raise ValueError("Custom AI instructions must be 6,000 characters or fewer.")
    except ValueError as exc:
        return redirect_home(error=str(exc), course=course_code)
    if ai_notes:
        settings.ai.provider = provider_spec.name
        settings.ai.model = selected_model
    options = PipelineOptions(
        download_video=download_video,
        download_slides=download_slides,
        download_transcript=download_transcript,
        notebooklm=notebooklm,
        ai_notes=ai_notes,
        ai_note_format=ai_note_format,
        ai_note_length=ai_note_length,
        ai_custom_instructions=custom_instructions,
        save_obsidian=save_obsidian,
        whisper_fallback=whisper_fallback,
        overwrite=overwrite,
        skip_existing=not overwrite,
    )
    total_steps = PipelineService.stage_count(options) * len(lecture_ids)
    job_id = str(uuid4())
    db.create_job(
        JobRecord(
            id=job_id,
            kind="pipeline",
            course_code=course_code,
            current_step="Waiting to start pipeline",
            total_steps=max(1, total_steps),
        )
    )
    background_tasks.add_task(run_pipeline_job, job_id, lecture_ids, options, settings, db)
    return redirect_home(course=course_code, job=job_id)


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, db: Database = Depends(get_db)) -> dict:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job was not found.")
    payload = job.model_dump()
    payload["progress_percent"] = round(100 * job.completed_steps / max(1, job.total_steps))
    payload["eta_seconds"] = None
    if job.status == "running" and job.started_at and 0 < job.completed_steps < job.total_steps:
        elapsed = max(
            0.0, (datetime.now(UTC) - datetime.fromisoformat(job.started_at)).total_seconds()
        )
        payload["eta_seconds"] = round(
            elapsed * (job.total_steps - job.completed_steps) / job.completed_steps
        )
    return payload


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, db: Database = Depends(get_db)) -> dict:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job was not found.")
    if job.status in {"completed", "failed", "cancelled"}:
        return {"status": job.status}

    db.update_job(
        job_id,
        status="cancelling",
        current_step="Stopping job",
        updated_at=utc_now(),
    )
    task = ACTIVE_JOBS.get(job_id)
    if task is None or not task.cancel():
        latest = db.get_job(job_id)
        if latest and latest.status not in {"completed", "failed", "cancelled"}:
            mark_job_cancelled(db, job_id)
    return {"status": db.get_job(job_id).status}


@router.get("/outputs/{processing_id}")
async def processing_output(processing_id: int, db: Database = Depends(get_db)) -> FileResponse:
    record = db.get_processing(processing_id)
    if not record or not record.get("output_file"):
        raise HTTPException(status_code=404, detail="Output file was not recorded.")
    path = Path(record["output_file"]).expanduser()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Output file no longer exists.")
    return FileResponse(path, filename=path.name)
