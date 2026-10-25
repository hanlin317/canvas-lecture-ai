from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.ai.factory import (
    AI_PROVIDER_SPECS,
    api_key_status,
    fetch_provider_models,
    get_ai_provider_spec,
    save_api_key,
)
from app.canvas.client import CanvasClient
from app.config import Settings, load_settings, save_web_settings
from app.database import Database
from app.models import JobRecord, PipelineOptions
from app.services.discovery import DiscoveryService, _default_course_group
from app.services.pipeline import PipelineService
from app.transcripts.whisper import mlx_whisper_available

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter()
logger = logging.getLogger(__name__)
ACTIVE_JOBS: dict[str, asyncio.Task] = {}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def update_job_progress(db: Database, job_id: str, message: str, completed: int, total: int) -> None:
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


async def run_discovery_job(job_id: str, course_code: str, settings: Settings, db: Database) -> None:
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
        message = f"Discovery finished for {course_code}: {result['recordings']} recording(s)."
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
        if key in {"video", "slides", "transcript"}
    ]
    ready = sum(status in {"complete", "skipped"} for status in stage_statuses)
    unavailable = stage_statuses.count("unavailable")
    failed = stage_statuses.count("failed")
    return (
        f"Pipeline finished for {lecture_count} lecture(s): "
        f"{ready} output stage(s) ready, {unavailable} unavailable, {failed} failed."
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


def get_settings() -> Settings:
    return load_settings()


def get_db(settings: Settings = Depends(get_settings)) -> Database:
    return Database(settings.database_path)


def redirect_home(**params: str) -> RedirectResponse:
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"/{suffix}", status_code=303)


def selected_course_from_request(courses, request: Request):
    requested = request.query_params.get("course")
    if requested:
        for course in courses:
            if course.code == requested or str(course.id) == requested or course.canvas_id == requested:
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


def validated_directory(value: str, label: str, *, obsidian: bool = False) -> str:
    stripped = value.strip()
    if obsidian and not stripped:
        return ""
    if not stripped:
        raise ValueError(f"{label} is required.")
    path = Path(stripped).expanduser().resolve()
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
    if sys.platform != "darwin":
        raise RuntimeError("Folder selection is currently available on macOS only.")
    script = f'POSIX path of (choose folder with prompt "{prompt}")'
    process = await asyncio.create_subprocess_exec(
        "osascript",
        "-e",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        message = stderr.decode(errors="replace").strip()
        if "User canceled" in message:
            return None
        raise RuntimeError(message or "Could not open the folder picker.")
    return stdout.decode().strip().rstrip("/") or "/"


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, settings: Settings = Depends(get_settings), db: Database = Depends(get_db)) -> HTMLResponse:
    courses = db.list_courses()
    selected_course = selected_course_from_request(courses, request)
    topics = db.list_topics(selected_course.id) if selected_course and selected_course.id else []
    lectures = db.list_lectures(selected_course.id) if selected_course and selected_course.id else []
    recent_processing = db.list_recent_processing(selected_course.id) if selected_course and selected_course.id else []
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
    if not canvas.has_session():
        canvas_session_status = "not saved"
    else:
        canvas_session_status = "valid" if await canvas.session_is_valid() else "expired"
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
        provider["models"] for provider in ai_providers if provider["name"] == configured_provider.name
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
            "whisper_available": mlx_whisper_available(settings.transcripts.whisper_command),
            "env": {
                "OPENAI_API_KEY": bool(__import__("os").environ.get("OPENAI_API_KEY")),
                "ANTHROPIC_API_KEY": bool(__import__("os").environ.get("ANTHROPIC_API_KEY")),
                "GEMINI_API_KEY": bool(__import__("os").environ.get("GEMINI_API_KEY")),
            },
        },
    )


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
    return redirect_home(message=f"{spec.label} API key saved in macOS Keychain.", course=course_code)


@router.post("/settings/app")
async def update_app_settings(
    canvas_base_url: str = Form(default=""),
    download_root: str = Form(default=""),
    notebooklm_staging_dir: str = Form(default=""),
    obsidian_vault: str = Form(default=""),
    ai_provider: str = Form(default="openai"),
    ai_model: str = Form(default=""),
    whisper_enabled: bool = Form(default=False),
    whisper_model: str = Form(default=""),
    course_code: str = Form(default=""),
    db: Database = Depends(get_db),
) -> RedirectResponse:
    try:
        provider = get_ai_provider_spec(ai_provider)
        model = validated_ai_model(provider.name, ai_model, db)
        whisper = whisper_model.strip()
        if whisper_enabled and not whisper:
            raise ValueError("Whisper model is required when Whisper is enabled.")
        save_web_settings(
            {
                "canvas.base_url": validated_canvas_url(canvas_base_url),
                "paths.download_root": validated_directory(download_root, "Download folder"),
                "paths.notebooklm_staging_dir": validated_directory(
                    notebooklm_staging_dir, "NotebookLM staging folder"
                ),
                "paths.obsidian_vault": validated_directory(
                    obsidian_vault, "Obsidian vault", obsidian=True
                ),
                "ai.provider": provider.name,
                "ai.model": model,
                "transcripts.whisper_enabled": whisper_enabled,
                "transcripts.whisper_model": whisper or "whisper-large-v3-turbo",
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


@router.post("/courses/sync")
async def sync_courses(settings: Settings = Depends(get_settings), db: Database = Depends(get_db)) -> RedirectResponse:
    try:
        courses = await DiscoveryService(db, settings).sync_canvas_courses()
    except Exception as exc:
        logger.exception("Canvas course sync failed")
        return redirect_home(error=str(exc))
    params = {"message": f"Synced {len(courses)} Canvas course(s)."}
    if courses:
        params["course"] = courses[0].code
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
        grouped = await DiscoveryService(db, settings).group_courses_with_ai(spec.name, selected_model)
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
        return redirect_home(error="Select at least one lecture before running the pipeline.", course=course_code)
    try:
        provider_spec = get_ai_provider_spec(ai_provider)
        selected_model = validated_ai_model(provider_spec.name, ai_model, db)
        if ai_note_format not in {"markdown", "pdf", "docx"}:
            raise ValueError(f"Unknown AI note format: {ai_note_format}")
    except ValueError as exc:
        return redirect_home(error=str(exc), course=course_code)
    settings.ai.provider = provider_spec.name
    settings.ai.model = selected_model
    options = PipelineOptions(
        download_video=download_video,
        download_slides=download_slides,
        download_transcript=download_transcript,
        notebooklm=notebooklm,
        ai_notes=ai_notes,
        ai_note_format=ai_note_format,
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
        elapsed = max(0.0, (datetime.now(UTC) - datetime.fromisoformat(job.started_at)).total_seconds())
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
