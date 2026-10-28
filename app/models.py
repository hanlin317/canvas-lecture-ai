from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class DownloadStatus(StrEnum):
    MISSING = "missing"
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    SKIPPED = "skipped"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class TranscriptMethod(StrEnum):
    OFFICIAL_TXT = "official_txt"
    OFFICIAL_VTT = "official_vtt"
    WHISPER = "whisper"
    MANUAL = "manual"
    UNKNOWN = "unknown"


class Course(BaseModel):
    id: int | None = None
    canvas_id: str
    code: str
    name: str
    canvas_url: str | None = None
    group_name: str | None = None
    is_active: bool = False


class Topic(BaseModel):
    id: int | None = None
    course_id: int
    name: str
    numbering_prefix: str = ""
    download_directory: str | None = None
    obsidian_directory: str | None = None
    parent_note: str | None = None
    position: int = 0


class Lecture(BaseModel):
    id: int | None = None
    source_id: str
    course_id: int
    topic_id: int | None = None
    original_title: str
    custom_title: str | None = None
    lecture_number: str | None = None
    recording_url: str | None = None
    date: str | None = None
    duration_seconds: int | None = None
    position: int = 0

    @property
    def display_title(self) -> str:
        return self.custom_title or self.original_title


class Asset(BaseModel):
    id: int | None = None
    lecture_id: int
    kind: str
    remote_url: str | None = None
    quality: str | None = None
    local_path: str | None = None
    size_bytes: int | None = None
    status: DownloadStatus = DownloadStatus.MISSING
    checksum: str | None = None
    transcript_source: TranscriptMethod | None = None
    notebooklm_path: str | None = None
    obsidian_note_path: str | None = None
    error_message: str | None = None


class ProcessingRecord(BaseModel):
    id: int | None = None
    lecture_id: int
    stage: str
    status: str
    ai_provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    transcript_hash: str | None = None
    transcript_file: str | None = None
    output_file: str | None = None
    error_message: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class JobRecord(BaseModel):
    id: str
    kind: str
    status: str = "queued"
    course_code: str | None = None
    current_step: str = "Queued"
    completed_steps: int = 0
    total_steps: int = 1
    message: str | None = None
    error_message: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    payload_json: str = "{}"


class JobItemRecord(BaseModel):
    id: int | None = None
    job_id: str
    lecture_id: int
    position: int
    status: str = "queued"
    current_stage: str = "Queued"
    completed_steps: int = 0
    total_steps: int = 1
    error_message: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class NoteDraft(BaseModel):
    processing_id: int
    lecture_id: int
    markdown: str
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class EchoVideoAsset(BaseModel):
    url: str
    quality: str = "unknown"
    size_bytes: int | None = None


class EchoTranscriptAsset(BaseModel):
    url: str
    format: str = "txt"
    language: str | None = None


class EchoPresentationAsset(BaseModel):
    media_id: str
    filename: str
    size_bytes: int | None = None


class EchoRecording(BaseModel):
    source_id: str
    title: str
    url: str
    date: str | None = None
    duration_seconds: int | None = None
    order: int = 0
    topic_hint: str | None = None
    videos: list[EchoVideoAsset] = Field(default_factory=list)
    transcripts: list[EchoTranscriptAsset] = Field(default_factory=list)
    presentations: list[EchoPresentationAsset] = Field(default_factory=list)


class PipelineOptions(BaseModel):
    download_video: bool = True
    download_slides: bool = False
    download_transcript: bool = True
    whisper_fallback: bool = False
    notebooklm: bool = True
    ai_notes: bool = False
    ai_note_format: Literal["markdown", "pdf", "docx"] = "markdown"
    ai_note_length: Literal["concise", "standard", "detailed", "comprehensive"] = "standard"
    ai_custom_instructions: str = ""
    save_obsidian: bool = False
    overwrite: bool = False
    skip_existing: bool = True
    conflict_policy: Literal["skip", "replace", "keep_both", "update_changed"] = "skip"
    prompt_template: Literal[
        "comprehensive",
        "exam_revision",
        "clinical_procedures",
        "flashcards",
        "learning_objectives",
        "concise_summary",
    ] = "comprehensive"
    privacy_mode: Literal["downloads_only", "local_only", "cloud_ai"] = "cloud_ai"


@dataclass(frozen=True)
class LectureContext:
    course_code: str
    topic_name: str
    lecture_number: str
    title: str
    source: str
    source_recording_id: str
    date: str | None = None
    transcript_method: str = "unknown"
