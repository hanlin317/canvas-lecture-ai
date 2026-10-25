from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.ai.factory import AIModelSpec
from app.config import load_settings
from app.database import Database
from app.main import create_app
from app.models import Course, JobRecord, Lecture, ProcessingRecord, Topic
from app.web import routes as web_routes


def test_index_page_renders(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{tmp_path / 'test.sqlite3'}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert "Dental Lecture Manager" in response.text
    assert "Canvas session: not saved" in response.text
    assert "Anthropic Claude" in response.text
    assert "Google Gemini" in response.text
    assert "OpenRouter" in response.text
    assert 'type="password"' in response.text
    assert response.text.count('id="course-selector"') == 1
    assert "AI course grouping" in response.text
    assert 'id="job-progress"' in response.text
    assert "Estimating time remaining" in response.text
    assert 'name="download_slides"' in response.text
    assert 'action="/settings/app"' in response.text
    assert 'id="canvas-base-url"' in response.text
    assert 'data-folder-field="obsidian_vault"' in response.text
    assert 'id="config-ai-model" name="ai_model"' in response.text
    assert 'id="job-cancel"' in response.text
    assert 'id="ai-note-format"' in response.text
    assert "Word (.docx)" in response.text
    assert "DeepSeek V4.1 Flash" in response.text
    assert "Refresh models" in response.text
    assert "Not configured" in response.text
    assert "Open NotebookLM" in response.text
    assert "Show source files" in response.text
    assert "Configured" in response.text


def test_first_run_requires_canvas_setup_before_login(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{tmp_path / 'test.sqlite3'}'\n"
        "[canvas]\nbase_url = ''\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))

    response = TestClient(create_app()).get("/")

    assert response.status_code == 200
    assert "Setup required" in response.text
    assert '<form method="post" action="/login"><button disabled>' in response.text


def test_app_settings_route_saves_validated_configuration(tmp_path, monkeypatch) -> None:
    download = tmp_path / "downloads"
    notebook = tmp_path / "notebook"
    vault = tmp_path / "My Vault"
    for path in (download, notebook, vault / ".obsidian"):
        path.mkdir(parents=True)
    config = tmp_path / "config.toml"
    config.write_text(
        f"# retained\n[app]\ndatabase_path = '{tmp_path / 'test.sqlite3'}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n"
        "[downloads]\nconcurrency = 5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    client = TestClient(create_app())

    response = client.post(
        "/settings/app",
        data={
            "canvas_base_url": "https://canvas.new.example.edu/",
            "download_root": str(download),
            "notebooklm_staging_dir": str(notebook),
            "obsidian_vault": str(vault),
            "ai_provider": "anthropic",
            "ai_model": "claude-sonnet-5",
            "whisper_enabled": "on",
            "whisper_model": "whisper-test",
            "course_code": "SDDM6321",
        },
        follow_redirects=False,
    )

    settings = load_settings(config)
    assert response.status_code == 303
    assert "App+configuration+saved" in response.headers["location"]
    assert settings.paths.obsidian_vault == str(vault)
    assert settings.canvas.base_url == "https://canvas.new.example.edu"
    assert settings.ai.provider == "anthropic"
    assert settings.ai.model == "claude-sonnet-5"
    assert settings.transcripts.whisper_enabled is True
    assert "# retained" in config.read_text(encoding="utf-8")
    assert settings.downloads.concurrency == 5


def test_app_settings_route_creates_output_folders(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{tmp_path / 'test.sqlite3'}'\n"
        "[canvas]\nbase_url = 'https://canvas.example'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    download = tmp_path / "new" / "downloads"
    notebook = tmp_path / "new" / "notebooklm"
    client = TestClient(create_app())

    response = client.post(
        "/settings/app",
        data={
            "canvas_base_url": "https://canvas.example",
            "download_root": str(download),
            "notebooklm_staging_dir": str(notebook),
            "obsidian_vault": "",
            "ai_provider": "openai",
            "ai_model": "gpt-6.1-sol",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert download.is_dir()
    assert notebook.is_dir()


def test_pipeline_summary_includes_generated_outputs_and_lecture_errors() -> None:
    summary = web_routes.pipeline_summary(
        2,
        [
            {
                "lecture": "One",
                "transcript": "complete",
                "notebooklm": "/tmp/one.md",
                "ai_notes": "/tmp/one.pdf",
            },
            {"lecture": "Two", "error": "provider failed"},
        ],
    )

    assert summary == (
        "Processed 2 lecture(s): 1 completed, 1 stopped by errors; "
        "3 output(s) ready, 0 unavailable, 0 failed."
    )


@pytest.mark.asyncio
async def test_reveal_notebooklm_sources_opens_configured_folder(tmp_path, monkeypatch) -> None:
    sources = tmp_path / "NotebookLM Sources"
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{tmp_path / 'test.sqlite3'}'\n"
        f"[paths]\nnotebooklm_staging_dir = '{sources}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    calls = []

    class FakeProcess:
        async def wait(self):
            return 0

    async def fake_subprocess(*args):
        calls.append(args)
        return FakeProcess()

    monkeypatch.setattr(web_routes.sys, "platform", "darwin")
    monkeypatch.setattr(web_routes.asyncio, "create_subprocess_exec", fake_subprocess)
    client = TestClient(create_app())

    response = client.post(
        "/notebooklm/reveal",
        data={"course_code": "DENT1000"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert sources.is_dir()
    assert calls == [("open", str(sources))]


def test_app_settings_route_rejects_non_vault_folder(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{tmp_path / 'test.sqlite3'}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n",
        encoding="utf-8",
    )
    original = config.read_text(encoding="utf-8")
    monkeypatch.setenv("DLM_CONFIG", str(config))
    client = TestClient(create_app())

    response = client.post(
        "/settings/app",
        data={
            "canvas_base_url": "https://canvas.example",
            "download_root": str(folder),
            "notebooklm_staging_dir": str(folder),
            "obsidian_vault": str(folder),
            "ai_provider": "openai",
            "ai_model": "gpt-6.1-sol",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "not+an+Obsidian+vault" in response.headers["location"]
    assert config.read_text(encoding="utf-8") == original


def test_folder_picker_route_returns_selected_path(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{tmp_path / 'test.sqlite3'}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    picker = AsyncMock(return_value="/Users/test/My Vault")
    monkeypatch.setattr("app.web.routes.choose_directory", picker)
    client = TestClient(create_app())

    response = client.post("/settings/choose-folder", data={"field": "obsidian_vault"})

    assert response.status_code == 200
    assert response.json() == {"path": "/Users/test/My Vault", "cancelled": False}
    picker.assert_awaited_once_with("Choose your Obsidian vault")


def test_api_key_route_uses_secure_store(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{tmp_path / 'test.sqlite3'}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    saved = {}
    monkeypatch.setattr("app.web.routes.save_api_key", lambda provider, key: saved.update({provider: key}))
    client = TestClient(create_app())

    response = client.post(
        "/settings/api-key",
        data={"provider": "anthropic", "api_key": "test-secret", "course_code": "SDDM6321"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert saved == {"anthropic": "test-secret"}
    assert "test-secret" not in response.headers["location"]


def test_model_refresh_route_caches_provider_results(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "test.sqlite3"
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{db_path}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    refresh = AsyncMock(
        return_value=(
            AIModelSpec(id="deepseek-flash", label="DeepSeek V4.1 Flash (live)"),
            AIModelSpec(id="deepseek-v4-pro", label="DeepSeek V4 Pro (live)"),
        )
    )
    monkeypatch.setattr("app.web.routes.fetch_provider_models", refresh)
    client = TestClient(create_app())

    response = client.post(
        "/settings/models/refresh",
        data={"ai_provider": "deepseek", "course_code": "SDDM6321"},
        follow_redirects=False,
    )
    page = client.get("/?course=SDDM6321&model_provider=deepseek")
    cached = Database(db_path).list_provider_models("deepseek")

    assert response.status_code == 303
    assert "Refreshed+2+compatible+DeepSeek+model" in response.headers["location"]
    assert [item["model_id"] for item in cached] == ["deepseek-flash", "deepseek-v4-pro"]
    assert "DeepSeek V4.1 Flash (live)" in page.text


def test_ai_course_grouping_route(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{tmp_path / 'test.sqlite3'}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    grouping = AsyncMock(return_value=3)
    monkeypatch.setattr("app.web.routes.DiscoveryService.group_courses_with_ai", grouping)
    client = TestClient(create_app())

    response = client.post(
        "/courses/group",
        data={"provider": "openai", "model": "gpt-6.1-sol", "course": "SDDM6321"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "AI+grouping+updated+3+course" in response.headers["location"]
    grouping.assert_awaited_once_with("openai", "gpt-6.1-sol")


def test_sync_courses_redirects_with_error_instead_of_500(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{tmp_path / 'test.sqlite3'}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    monkeypatch.delenv("CANVAS_ACCESS_TOKEN", raising=False)
    client = TestClient(create_app())
    response = client.post("/courses/sync", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/?error=")


def test_discover_without_course_redirects_with_error(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{tmp_path / 'test.sqlite3'}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    client = TestClient(create_app())
    response = client.post("/discover", data={}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/?error=")


def test_discovery_runs_as_tracked_background_job(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "test.sqlite3"
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{db_path}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))

    async def discover(_service, course_code, diagnostic_dir=None, progress=None):
        progress("Reading Echo360 folders and recordings", 3, 6)
        return {"recordings": 9}

    monkeypatch.setattr("app.web.routes.DiscoveryService.discover_course", discover)
    client = TestClient(create_app())

    response = client.post("/discover", data={"course": "SDDM6321"}, follow_redirects=False)
    job_id = parse_qs(urlparse(response.headers["location"]).query)["job"][0]
    status = client.get(f"/jobs/{job_id}")

    assert response.status_code == 303
    assert status.json()["status"] == "completed"
    assert status.json()["progress_percent"] == 100
    assert "9 recording" in status.json()["message"]


def test_pipeline_route_passes_selected_ai_note_format(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{tmp_path / 'test.sqlite3'}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    captured = {}

    async def run_job(job_id, lecture_ids, options, settings, db):
        captured["format"] = options.ai_note_format

    monkeypatch.setattr("app.web.routes.run_pipeline_job", run_job)
    client = TestClient(create_app())

    response = client.post(
        "/pipeline",
        data={
            "lecture_ids": "1",
            "ai_notes": "on",
            "ai_note_format": "docx",
            "ai_provider": "openai",
            "ai_model": "gpt-6.1-sol",
            "course_code": "SDDM6321",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert captured["format"] == "docx"


def test_running_job_status_includes_eta(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "test.sqlite3"
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{db_path}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    db = Database(db_path)
    started = (datetime.now(UTC) - timedelta(seconds=12)).isoformat()
    db.create_job(
        JobRecord(
            id="job-eta",
            kind="pipeline",
            status="running",
            current_step="Preparing transcript",
            completed_steps=2,
            total_steps=6,
            started_at=started,
        )
    )
    client = TestClient(create_app())

    payload = client.get("/jobs/job-eta").json()

    assert payload["progress_percent"] == 33
    assert 23 <= payload["eta_seconds"] <= 25


@pytest.mark.asyncio
async def test_running_discovery_job_can_be_cancelled(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "test.sqlite3"
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{db_path}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    db = Database(db_path)
    db.create_job(
        JobRecord(
            id="job-stop",
            kind="discovery",
            course_code="SDDM6321",
            current_step="Waiting to start course discovery",
            total_steps=6,
        )
    )
    started = asyncio.Event()

    async def discover(_service, course_code, diagnostic_dir=None, progress=None):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("app.web.routes.DiscoveryService.discover_course", discover)
    task = asyncio.create_task(
        web_routes.run_discovery_job("job-stop", "SDDM6321", load_settings(config), db)
    )
    await started.wait()

    response = await web_routes.cancel_job("job-stop", db)
    await task

    job = db.get_job("job-stop")
    assert response["status"] == "cancelling"
    assert job is not None
    assert job.status == "cancelled"
    assert job.current_step == "Job stopped"
    assert "job-stop" not in web_routes.ACTIVE_JOBS


def test_index_shows_selected_course_lectures(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "test.sqlite3"
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{db_path}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    db = Database(db_path)
    db.upsert_course(Course(canvas_id="1", code="FIRST", name="First Course"))
    second = db.upsert_course(Course(canvas_id="2", code="SECOND", name="Second Course"))
    topic = db.upsert_topic(Topic(course_id=second.id or 0, name="Endodontics: Instructional Videos"))
    db.upsert_lecture(
        Lecture(
            source_id="rec-second",
            course_id=second.id or 0,
            topic_id=topic.id,
            original_title="Real Lecture",
            lecture_number="3.1",
            date="2026-04-02T17:00:00.000",
        )
    )
    client = TestClient(create_app())
    response = client.get("/?course=SECOND")
    assert response.status_code == 200
    assert "Lectures - SECOND" in response.text
    assert "Real Lecture" in response.text
    assert "Endodontics: Instructional Videos" in response.text
    assert 'data-topic-select' in response.text
    assert "Select all" in response.text
    assert "02/04/2026" in response.text
    assert "2026-04-02T17:00:00.000" not in response.text
    assert "Lectures - FIRST" not in response.text


def test_course_selector_distinguishes_duplicate_course_codes(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "test.sqlite3"
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{db_path}'\n"
        "[canvas]\nbase_url = 'https://canvas.example'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    db = Database(db_path)
    older = db.upsert_course(
        Course(canvas_id="2025", code="DENT1000", name="Dentistry 2025")
    )
    newer = db.upsert_course(
        Course(canvas_id="2026", code="DENT1000", name="Dentistry 2026")
    )
    topic = db.upsert_topic(Topic(course_id=newer.id or 0, name="New lectures"))
    db.upsert_lecture(
        Lecture(
            source_id="new-lecture",
            course_id=newer.id or 0,
            topic_id=topic.id,
            original_title="Only in 2026",
        )
    )

    response = TestClient(create_app()).get(f"/?course={newer.id}")

    assert response.status_code == 200
    assert "Only in 2026" in response.text
    assert f'<option value="{older.id}"' in response.text
    assert f'<option value="{newer.id}"' in response.text


def test_recent_pipeline_output_is_visible_and_downloadable(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "test.sqlite3"
    config = tmp_path / "config.toml"
    config.write_text(
        f"[app]\ndatabase_path = '{db_path}'\n"
        f"[canvas]\nbase_url = 'https://canvas.example'\nstate_path = '{tmp_path / 'state.json'}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DLM_CONFIG", str(config))
    db = Database(db_path)
    course = db.upsert_course(Course(canvas_id="1", code="DENT1000", name="Dental"))
    topic = db.upsert_topic(Topic(course_id=course.id or 0, name="Endodontics"))
    lecture = db.upsert_lecture(
        Lecture(source_id="rec-1", course_id=course.id or 0, topic_id=topic.id, original_title="Lecture One")
    )
    output = tmp_path / "lecture.txt"
    output.write_text("transcript output", encoding="utf-8")
    processing = db.upsert_processing(
        ProcessingRecord(
            lecture_id=lecture.id or 0,
            stage="canonical_transcript",
            status="complete",
            output_file=str(output),
        )
    )
    client = TestClient(create_app())

    page = client.get("/?course=DENT1000")
    downloaded = client.get(f"/outputs/{processing.id}")

    assert "Processing History" in page.text
    assert '<details class="pipeline-results"' in page.text
    assert str(output) not in page.text
    assert "Open output" in page.text
    assert downloaded.status_code == 200
    assert downloaded.text == "transcript output"
