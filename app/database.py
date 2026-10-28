from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models import (
    Asset,
    Course,
    JobItemRecord,
    JobRecord,
    Lecture,
    NoteDraft,
    ProcessingRecord,
    Topic,
)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS courses (
  id INTEGER PRIMARY KEY,
  canvas_id TEXT NOT NULL UNIQUE,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  canvas_url TEXT,
  group_name TEXT,
  is_active INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS topics (
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  numbering_prefix TEXT DEFAULT '',
  download_directory TEXT,
  obsidian_directory TEXT,
  parent_note TEXT,
  position INTEGER DEFAULT 0,
  UNIQUE(course_id, name)
);

CREATE TABLE IF NOT EXISTS lectures (
  id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL UNIQUE,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
  original_title TEXT NOT NULL,
  custom_title TEXT,
  lecture_number TEXT,
  recording_url TEXT,
  date TEXT,
  duration_seconds INTEGER,
  position INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY,
  lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  remote_url TEXT,
  quality TEXT,
  local_path TEXT,
  size_bytes INTEGER,
  status TEXT NOT NULL DEFAULT 'missing',
  checksum TEXT,
  transcript_source TEXT,
  notebooklm_path TEXT,
  obsidian_note_path TEXT,
  error_message TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_identity
ON assets(lecture_id, kind, COALESCE(quality, ''));

CREATE TABLE IF NOT EXISTS processing (
  id INTEGER PRIMARY KEY,
  lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  ai_provider TEXT,
  model TEXT,
  prompt_version TEXT,
  transcript_hash TEXT,
  transcript_file TEXT,
  output_file TEXT,
  error_message TEXT,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_processing_identity
ON processing(lecture_id, stage, COALESCE(prompt_version, ''));

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  course_code TEXT,
  current_step TEXT NOT NULL,
  completed_steps INTEGER NOT NULL DEFAULT 0,
  total_steps INTEGER NOT NULL DEFAULT 1,
  message TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  updated_at TEXT NOT NULL,
  finished_at TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS job_items (
  id INTEGER PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  current_stage TEXT NOT NULL DEFAULT 'Queued',
  completed_steps INTEGER NOT NULL DEFAULT 0,
  total_steps INTEGER NOT NULL DEFAULT 1,
  error_message TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(job_id, lecture_id)
);

CREATE TABLE IF NOT EXISTS profiles (
  scope TEXT PRIMARY KEY,
  data_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_drafts (
  processing_id INTEGER PRIMARY KEY REFERENCES processing(id) ON DELETE CASCADE,
  lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
  markdown TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_models (
  provider TEXT NOT NULL,
  model_id TEXT NOT NULL,
  label TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  refreshed_at TEXT NOT NULL,
  PRIMARY KEY(provider, model_id)
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterable[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.executescript(SCHEMA)
            topic_columns = {row[1] for row in conn.execute("PRAGMA table_info(topics)")}
            if "position" not in topic_columns:
                conn.execute("ALTER TABLE topics ADD COLUMN position INTEGER DEFAULT 0")
            course_columns = {row[1] for row in conn.execute("PRAGMA table_info(courses)")}
            if "group_name" not in course_columns:
                conn.execute("ALTER TABLE courses ADD COLUMN group_name TEXT")
            if "is_active" not in course_columns:
                conn.execute("ALTER TABLE courses ADD COLUMN is_active INTEGER NOT NULL DEFAULT 0")
            job_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            if "payload_json" not in job_columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")

    def reconcile_interrupted_jobs(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'paused',
                    current_step = 'Paused after application restart'
                WHERE kind = 'pipeline' AND status IN ('queued', 'running', 'pausing', 'cancelling')
                """
            )
            conn.execute(
                """
                UPDATE job_items
                SET status = 'paused', current_stage = 'Paused after application restart'
                WHERE status = 'running'
                """
            )

    def upsert_course(self, course: Course) -> Course:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO courses(canvas_id, code, name, canvas_url, group_name, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(canvas_id) DO UPDATE SET
                  code=excluded.code,
                  name=excluded.name,
                  canvas_url=excluded.canvas_url,
                  group_name=COALESCE(courses.group_name, excluded.group_name),
                  is_active=excluded.is_active
                """,
                (
                    course.canvas_id,
                    course.code,
                    course.name,
                    course.canvas_url,
                    course.group_name,
                    course.is_active,
                ),
            )
            row = conn.execute(
                "SELECT * FROM courses WHERE canvas_id = ?", (course.canvas_id,)
            ).fetchone()
            return Course(**dict(row))

    def list_courses(self) -> list[Course]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM courses ORDER BY code, name").fetchall()
            return [Course(**dict(row)) for row in rows]

    def mark_all_courses_inactive(self) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE courses SET is_active = 0")

    def update_course_groups(self, groups_by_canvas_id: dict[str, str]) -> int:
        updated = 0
        with self.connect() as conn:
            for canvas_id, group_name in groups_by_canvas_id.items():
                cursor = conn.execute(
                    "UPDATE courses SET group_name = ? WHERE canvas_id = ?",
                    (group_name, canvas_id),
                )
                updated += cursor.rowcount
        return updated

    def replace_provider_models(
        self,
        provider: str,
        models: list[tuple[str, str]],
        refreshed_at: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM provider_models WHERE provider = ?", (provider,))
            conn.executemany(
                """
                INSERT INTO provider_models(provider, model_id, label, position, refreshed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (provider, model_id, label, position, refreshed_at)
                    for position, (model_id, label) in enumerate(models)
                ],
            )

    def list_provider_models(self, provider: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT model_id, label, refreshed_at
                FROM provider_models
                WHERE provider = ?
                ORDER BY position, label
                """,
                (provider,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_course_by_code(self, code: str) -> Course | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM courses WHERE code = ?", (code,)).fetchone()
            return Course(**dict(row)) if row else None

    def get_course(self, identifier: str | int) -> Course | None:
        value = str(identifier)
        with self.connect() as conn:
            if value.isdigit():
                row = conn.execute("SELECT * FROM courses WHERE id = ?", (int(value),)).fetchone()
                if row:
                    return Course(**dict(row))
            row = conn.execute("SELECT * FROM courses WHERE canvas_id = ?", (value,)).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT * FROM courses WHERE code = ? ORDER BY is_active DESC, id DESC",
                    (value,),
                ).fetchone()
            return Course(**dict(row)) if row else None

    def upsert_topic(self, topic: Topic) -> Topic:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO topics(
                  course_id, name, numbering_prefix, download_directory, obsidian_directory, parent_note, position
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(course_id, name) DO UPDATE SET
                  numbering_prefix=COALESCE(NULLIF(excluded.numbering_prefix, ''), topics.numbering_prefix),
                  download_directory=COALESCE(excluded.download_directory, topics.download_directory),
                  obsidian_directory=COALESCE(excluded.obsidian_directory, topics.obsidian_directory),
                  parent_note=COALESCE(excluded.parent_note, topics.parent_note),
                  position=excluded.position
                """,
                (
                    topic.course_id,
                    topic.name,
                    topic.numbering_prefix,
                    topic.download_directory,
                    topic.obsidian_directory,
                    topic.parent_note,
                    topic.position,
                ),
            )
            row = conn.execute(
                "SELECT * FROM topics WHERE course_id = ? AND name = ?",
                (topic.course_id, topic.name),
            ).fetchone()
            return Topic(**dict(row))

    def list_topics(self, course_id: int) -> list[Topic]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM topics WHERE course_id = ? ORDER BY position, name",
                (course_id,),
            ).fetchall()
            return [Topic(**dict(row)) for row in rows]

    def upsert_lecture(self, lecture: Lecture) -> Lecture:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO lectures(source_id, course_id, topic_id, original_title, custom_title,
                  lecture_number, recording_url, date, duration_seconds, position)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                  course_id=excluded.course_id,
                  topic_id=excluded.topic_id,
                  original_title=excluded.original_title,
                  custom_title=COALESCE(excluded.custom_title, lectures.custom_title),
                  lecture_number=excluded.lecture_number,
                  recording_url=excluded.recording_url,
                  date=excluded.date,
                  duration_seconds=excluded.duration_seconds,
                  position=excluded.position
                """,
                (
                    lecture.source_id,
                    lecture.course_id,
                    lecture.topic_id,
                    lecture.original_title,
                    lecture.custom_title,
                    lecture.lecture_number,
                    lecture.recording_url,
                    lecture.date,
                    lecture.duration_seconds,
                    lecture.position,
                ),
            )
            row = conn.execute(
                "SELECT * FROM lectures WHERE source_id = ?", (lecture.source_id,)
            ).fetchone()
            return Lecture(**dict(row))

    def list_lectures(self, course_id: int, topic_id: int | None = None) -> list[Lecture]:
        sql = "SELECT * FROM lectures WHERE course_id = ?"
        args: list[Any] = [course_id]
        if topic_id is not None:
            sql += " AND topic_id = ?"
            args.append(topic_id)
        sql += " ORDER BY COALESCE(topic_id, 0), position, date, original_title"
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
            return [Lecture(**dict(row)) for row in rows]

    def get_lecture(self, lecture_id: int) -> Lecture | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM lectures WHERE id = ?", (lecture_id,)).fetchone()
            return Lecture(**dict(row)) if row else None

    def list_recent_processing(self, course_id: int, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*, l.original_title, l.custom_title
                FROM processing p
                JOIN lectures l ON l.id = p.lecture_id
                WHERE l.course_id = ?
                ORDER BY p.updated_at DESC
                LIMIT ?
                """,
                (course_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_processing(self, processing_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM processing WHERE id = ?", (processing_id,)).fetchone()
            return dict(row) if row else None

    def get_latest_processing(self, lecture_id: int, stage: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM processing
                WHERE lecture_id = ? AND stage = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (lecture_id, stage),
            ).fetchone()
            return dict(row) if row else None

    def update_processing_output(self, processing_id: int, output_file: str) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE processing SET output_file = ?, updated_at = ? WHERE id = ?",
                (output_file, datetime.now(UTC).isoformat(), processing_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("processing record not found")

    def create_job(self, job: JobRecord) -> JobRecord:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs(id, kind, status, course_code, current_step, completed_steps,
                  total_steps, message, error_message, created_at, started_at, updated_at, finished_at,
                  payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.kind,
                    job.status,
                    job.course_code,
                    job.current_step,
                    job.completed_steps,
                    job.total_steps,
                    job.message,
                    job.error_message,
                    job.created_at,
                    job.started_at,
                    job.updated_at,
                    job.finished_at,
                    job.payload_json,
                ),
            )
        return job

    def get_job(self, job_id: str) -> JobRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return JobRecord(**dict(row)) if row else None

    def list_jobs(self, limit: int = 20) -> list[JobRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [JobRecord(**dict(row)) for row in rows]

    def update_job(self, job_id: str, **changes: Any) -> JobRecord:
        allowed = {
            "status",
            "course_code",
            "current_step",
            "completed_steps",
            "total_steps",
            "message",
            "error_message",
            "started_at",
            "updated_at",
            "finished_at",
            "payload_json",
        }
        invalid = set(changes) - allowed
        if invalid:
            raise ValueError(f"Unsupported job field(s): {', '.join(sorted(invalid))}")
        if not changes:
            job = self.get_job(job_id)
            if not job:
                raise ValueError(f"job not found: {job_id}")
            return job
        assignments = ", ".join(f"{column} = ?" for column in changes)
        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",
                (*changes.values(), job_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"job not found: {job_id}")
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return JobRecord(**dict(row))

    def create_job_items(self, items: list[JobItemRecord]) -> None:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO job_items(job_id, lecture_id, position, status, current_stage,
                  completed_steps, total_steps, error_message, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, lecture_id) DO UPDATE SET
                  position=excluded.position,
                  status=excluded.status,
                  current_stage=excluded.current_stage,
                  completed_steps=excluded.completed_steps,
                  total_steps=excluded.total_steps,
                  error_message=excluded.error_message,
                  updated_at=excluded.updated_at
                """,
                [
                    (
                        item.job_id,
                        item.lecture_id,
                        item.position,
                        item.status,
                        item.current_stage,
                        item.completed_steps,
                        item.total_steps,
                        item.error_message,
                        item.updated_at,
                    )
                    for item in items
                ],
            )

    def list_job_items(self, job_id: str) -> list[JobItemRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM job_items WHERE job_id = ? ORDER BY position, id", (job_id,)
            ).fetchall()
            return [JobItemRecord(**dict(row)) for row in rows]

    def update_job_item(self, job_id: str, lecture_id: int, **changes: Any) -> JobItemRecord:
        allowed = {
            "position",
            "status",
            "current_stage",
            "completed_steps",
            "total_steps",
            "error_message",
            "updated_at",
        }
        invalid = set(changes) - allowed
        if invalid:
            raise ValueError(f"Unsupported job item field(s): {', '.join(sorted(invalid))}")
        assignments = ", ".join(f"{column} = ?" for column in changes)
        with self.connect() as conn:
            if changes:
                conn.execute(
                    f"UPDATE job_items SET {assignments} WHERE job_id = ? AND lecture_id = ?",
                    (*changes.values(), job_id, lecture_id),
                )
            row = conn.execute(
                "SELECT * FROM job_items WHERE job_id = ? AND lecture_id = ?",
                (job_id, lecture_id),
            ).fetchone()
            if not row:
                raise ValueError("job item not found")
            return JobItemRecord(**dict(row))

    def reorder_job_items(self, job_id: str, lecture_ids: list[int]) -> None:
        with self.connect() as conn:
            existing = {
                int(row["lecture_id"])
                for row in conn.execute(
                    "SELECT lecture_id FROM job_items WHERE job_id = ?", (job_id,)
                )
            }
            if set(lecture_ids) != existing or len(lecture_ids) != len(existing):
                raise ValueError("Reorder must include every lecture exactly once.")
            for position, lecture_id in enumerate(lecture_ids, start=1):
                conn.execute(
                    "UPDATE job_items SET position = ? WHERE job_id = ? AND lecture_id = ?",
                    (position, job_id, lecture_id),
                )

    def save_profile(self, scope: str, data: dict[str, Any], updated_at: str) -> None:
        import json

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO profiles(scope, data_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(scope) DO UPDATE SET
                  data_json=excluded.data_json,
                  updated_at=excluded.updated_at
                """,
                (scope, json.dumps(data, separators=(",", ":")), updated_at),
            )

    def get_profile(self, scope: str) -> dict[str, Any] | None:
        import json

        with self.connect() as conn:
            row = conn.execute(
                "SELECT data_json FROM profiles WHERE scope = ?", (scope,)
            ).fetchone()
            return json.loads(row["data_json"]) if row else None

    def upsert_note_draft(self, draft: NoteDraft) -> NoteDraft:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO note_drafts(processing_id, lecture_id, markdown, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(processing_id) DO UPDATE SET
                  markdown=excluded.markdown,
                  updated_at=excluded.updated_at
                """,
                (draft.processing_id, draft.lecture_id, draft.markdown, draft.updated_at),
            )
        return draft

    def get_note_draft(self, processing_id: int) -> NoteDraft | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM note_drafts WHERE processing_id = ?", (processing_id,)
            ).fetchone()
            return NoteDraft(**dict(row)) if row else None

    def upsert_asset(self, asset: Asset) -> Asset:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO assets(lecture_id, kind, remote_url, quality, local_path, size_bytes, status,
                  checksum, transcript_source, notebooklm_path, obsidian_note_path, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lecture_id, kind, COALESCE(quality, '')) DO UPDATE SET
                  remote_url=COALESCE(excluded.remote_url, assets.remote_url),
                  local_path=COALESCE(excluded.local_path, assets.local_path),
                  size_bytes=COALESCE(excluded.size_bytes, assets.size_bytes),
                  status=CASE
                    WHEN assets.remote_url = excluded.remote_url AND assets.status = 'complete'
                    THEN assets.status
                    ELSE excluded.status
                  END,
                  checksum=COALESCE(excluded.checksum, assets.checksum),
                  transcript_source=COALESCE(excluded.transcript_source, assets.transcript_source),
                  notebooklm_path=COALESCE(excluded.notebooklm_path, assets.notebooklm_path),
                  obsidian_note_path=COALESCE(excluded.obsidian_note_path, assets.obsidian_note_path),
                  error_message=excluded.error_message
                """,
                (
                    asset.lecture_id,
                    asset.kind,
                    asset.remote_url,
                    asset.quality,
                    asset.local_path,
                    asset.size_bytes,
                    asset.status.value,
                    asset.checksum,
                    asset.transcript_source.value if asset.transcript_source else None,
                    asset.notebooklm_path,
                    asset.obsidian_note_path,
                    asset.error_message,
                ),
            )
            row = conn.execute(
                "SELECT * FROM assets WHERE lecture_id = ? AND kind = ? AND COALESCE(quality, '') = COALESCE(?, '')",
                (asset.lecture_id, asset.kind, asset.quality),
            ).fetchone()
            data = dict(row)
            return Asset(**data)

    def list_assets(self, lecture_id: int) -> list[Asset]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM assets WHERE lecture_id = ?", (lecture_id,)
            ).fetchall()
            return [Asset(**dict(row)) for row in rows]

    def upsert_processing(self, record: ProcessingRecord) -> ProcessingRecord:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO processing(lecture_id, stage, status, ai_provider, model, prompt_version,
                  transcript_hash, transcript_file, output_file, error_message, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lecture_id, stage, COALESCE(prompt_version, '')) DO UPDATE SET
                  status=excluded.status,
                  ai_provider=excluded.ai_provider,
                  model=excluded.model,
                  transcript_hash=excluded.transcript_hash,
                  transcript_file=excluded.transcript_file,
                  output_file=excluded.output_file,
                  error_message=excluded.error_message,
                  updated_at=excluded.updated_at
                """,
                (
                    record.lecture_id,
                    record.stage,
                    record.status,
                    record.ai_provider,
                    record.model,
                    record.prompt_version,
                    record.transcript_hash,
                    record.transcript_file,
                    record.output_file,
                    record.error_message,
                    record.updated_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM processing WHERE lecture_id = ? AND stage = ? AND COALESCE(prompt_version, '') = COALESCE(?, '')",
                (record.lecture_id, record.stage, record.prompt_version),
            ).fetchone()
            return ProcessingRecord(**dict(row))

    def manifest_for_topic(self, topic_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            topic = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
            if not topic:
                raise ValueError(f"topic not found: {topic_id}")
            course = conn.execute(
                "SELECT * FROM courses WHERE id = ?", (topic["course_id"],)
            ).fetchone()
            lectures = conn.execute(
                "SELECT * FROM lectures WHERE topic_id = ? ORDER BY position, original_title",
                (topic_id,),
            ).fetchall()
            lecture_data = []
            for lecture in lectures:
                assets = conn.execute(
                    "SELECT * FROM assets WHERE lecture_id = ?", (lecture["id"],)
                ).fetchall()
                processing = conn.execute(
                    "SELECT * FROM processing WHERE lecture_id = ? ORDER BY updated_at DESC",
                    (lecture["id"],),
                ).fetchall()
                lecture_data.append(
                    {
                        "lecture": dict(lecture),
                        "assets": [dict(row) for row in assets],
                        "processing": [dict(row) for row in processing],
                    }
                )
            return {"course": dict(course), "topic": dict(topic), "lectures": lecture_data}
