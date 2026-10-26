from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from app.ai.base import AIRequest
from app.ai.factory import get_ai_provider
from app.ai.prompts import (
    STUDY_NOTE_SYSTEM_PROMPT,
    build_study_note_prompt,
    note_length_tokens,
)
from app.canvas.client import CanvasClient
from app.config import Settings
from app.database import Database
from app.downloads.manager import DownloadManager
from app.echo360.client import Echo360Client, ResolvedEchoAssets
from app.models import (
    Asset,
    DownloadStatus,
    Lecture,
    LectureContext,
    PipelineOptions,
    ProcessingRecord,
    TranscriptMethod,
)
from app.notebooklm.exporter import export_notebooklm
from app.obsidian.exporter import export_ai_note, obsidian_note_markdown, save_obsidian_note
from app.services.manifest import export_topic_manifest
from app.transcripts.cleaner import clean_plain_transcript, clean_vtt
from app.transcripts.whisper import transcribe_with_local_whisper
from app.utils import expand_path, lecture_stem, number_for_position, sha256_text

logger = logging.getLogger(__name__)


class PipelineService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.downloads = DownloadManager(settings.downloads.concurrency)
        self.canvas = CanvasClient(settings)
        self.echo = Echo360Client(settings)

    @staticmethod
    def stage_count(options: PipelineOptions) -> int:
        return (
            2
            + int(options.download_video or options.download_slides or options.download_transcript)
            + int(options.download_video)
            + int(options.download_slides)
            + int(options.download_transcript)
            + int(options.notebooklm)
            + int(options.ai_notes)
        )

    async def run_lecture(
        self,
        lecture_id: int,
        options: PipelineOptions,
        progress: Callable[[str, int, int], None] | None = None,
        start_step: int = 0,
        total_steps: int | None = None,
        lecture_position: int = 1,
        lecture_total: int = 1,
    ) -> dict:
        total = total_steps or self.stage_count(options)
        completed = start_step
        current_message = ""

        def begin(message: str) -> None:
            nonlocal current_message
            current_message = message
            if progress:
                progress(message, completed, total)

        def finish() -> None:
            nonlocal completed
            completed += 1
            if progress:
                progress(current_message, completed, total)

        lecture = self.db.get_lecture(lecture_id)
        if not lecture:
            raise ValueError(f"lecture not found: {lecture_id}")
        context, topic_dir = self._context_and_dir(lecture)
        result: dict[str, str] = {"lecture": context.title}

        def lecture_step(action: str) -> str:
            return f"Lecture {lecture_position} of {lecture_total}: {action} - {context.title}"

        begin(lecture_step("Preparing"))
        finish()

        resolved = ResolvedEchoAssets()
        resolution_error: str | None = None
        if options.download_video or options.download_slides or options.download_transcript:
            begin(lecture_step("Accessing Echo360 media"))
            try:
                resolved = await self._resolve_echo_assets(lecture)
            except Exception as exc:
                logger.exception("Could not resolve Echo360 assets for %s", context.title)
                resolution_error = str(exc)
            finish()

        if options.download_video:
            begin(lecture_step("Downloading video"))
            if resolution_error:
                self._record_resolution_failure(lecture, "download_video", resolution_error)
                result["video"] = "failed"
            else:
                result["video"] = await self._download_video(
                    lecture, context, topic_dir, options, resolved
                )
            finish()
        if options.download_slides:
            begin(lecture_step("Downloading slides"))
            if resolution_error:
                self._record_resolution_failure(lecture, "download_slides", resolution_error)
                result["slides"] = "failed"
            else:
                result["slides"] = await self._download_slides(
                    lecture, context, topic_dir, options, resolved
                )
            finish()
        if options.download_transcript:
            begin(lecture_step("Preparing transcript"))
            canonical = self._canonical_transcript_path(topic_dir, context)
            if resolution_error and not canonical.exists():
                self._record_resolution_failure(lecture, "canonical_transcript", resolution_error)
                result["transcript"] = "failed"
            else:
                result["transcript"] = await self._prepare_transcript(
                    lecture,
                    context,
                    topic_dir,
                    options,
                    resolved.headers_by_url,
                )
            finish()
        transcript_path = self._canonical_transcript_path(topic_dir, context)
        if options.notebooklm:
            begin(lecture_step("Exporting NotebookLM source"))
            if transcript_path.exists():
                result["notebooklm"] = self._export_notebooklm(lecture, context, transcript_path)
            else:
                result["notebooklm"] = "unavailable"
                self.db.upsert_processing(
                    ProcessingRecord(
                        lecture_id=lecture.id or 0,
                        stage="notebooklm",
                        status="unavailable",
                        error_message="A transcript is required before creating a NotebookLM source.",
                    )
                )
            finish()
        if options.ai_notes:
            destination = f"{options.ai_note_format.upper()} in the preview folder"
            if options.save_obsidian or self.settings.ai.auto_save_notes:
                destination += " and Markdown in Obsidian"
            begin(lecture_step(f"Generating AI notes and saving {destination}"))
            if transcript_path.exists():
                result["ai_notes"] = await self._generate_ai_note(
                    lecture, context, transcript_path, options
                )
            else:
                result["ai_notes"] = "unavailable"
                self.db.upsert_processing(
                    ProcessingRecord(
                        lecture_id=lecture.id or 0,
                        stage="ai_note",
                        status="unavailable",
                        error_message="A transcript is required before generating AI notes.",
                    )
                )
            finish()
        begin(lecture_step("Updating lecture manifest"))
        if lecture.topic_id:
            export_topic_manifest(self.db, lecture.topic_id, topic_dir)
        finish()
        return result

    async def run_many(
        self,
        lecture_ids: list[int],
        options: PipelineOptions,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> list[dict]:
        output = []
        steps_per_lecture = self.stage_count(options)
        total_steps = max(1, steps_per_lecture * len(lecture_ids))
        for index, lecture_id in enumerate(lecture_ids, start=1):
            try:
                output.append(
                    await self.run_lecture(
                        lecture_id,
                        options,
                        progress=progress,
                        start_step=(index - 1) * steps_per_lecture,
                        total_steps=total_steps,
                        lecture_position=index,
                        lecture_total=len(lecture_ids),
                    )
                )
            except Exception as exc:
                lecture = self.db.get_lecture(lecture_id)
                title = (
                    lecture.custom_title or lecture.original_title
                    if lecture
                    else f"Lecture {lecture_id}"
                )
                logger.exception("Pipeline failed for %s", title)
                self.db.upsert_processing(
                    ProcessingRecord(
                        lecture_id=lecture_id,
                        stage="pipeline",
                        status="failed",
                        error_message=str(exc),
                    )
                )
                output.append({"lecture": title, "error": str(exc)})
                if progress:
                    progress(
                        f"Lecture {index} of {len(lecture_ids)}: Failed - {title}",
                        index * steps_per_lecture,
                        total_steps,
                    )
        return output

    def _record_resolution_failure(self, lecture: Lecture, stage: str, error: str) -> None:
        self.db.upsert_processing(
            ProcessingRecord(
                lecture_id=lecture.id or 0,
                stage=stage,
                status="failed",
                error_message=f"Could not access Echo360 media: {error}",
            )
        )

    async def _resolve_echo_assets(self, lecture: Lecture) -> ResolvedEchoAssets:
        with self.db.connect() as conn:
            course = conn.execute(
                "SELECT * FROM courses WHERE id = ?", (lecture.course_id,)
            ).fetchone()
        if not course or not lecture.recording_url:
            return ResolvedEchoAssets()
        launch_urls = await self.canvas.echo360_navigation_links(str(course["canvas_id"]))
        if not launch_urls:
            raise RuntimeError("Echo360 course launch link was not found.")
        slide_asset = next(
            (
                asset
                for asset in self.db.list_assets(lecture.id or 0)
                if asset.kind == "slides"
                and (asset.remote_url or "").startswith("echo360-presentation://")
            ),
            None,
        )
        presentation_id = (
            (slide_asset.remote_url or "").removeprefix("echo360-presentation://")
            if slide_asset
            else None
        )
        resolved = await self.echo.resolve_recording_assets(
            launch_urls[0], lecture.recording_url, presentation_id=presentation_id
        )
        if resolved.video_url:
            self.db.upsert_asset(
                Asset(
                    lecture_id=lecture.id or 0,
                    kind="video",
                    remote_url=resolved.video_url,
                    quality="highest",
                    status=DownloadStatus.AVAILABLE,
                )
            )
        if resolved.transcript_url:
            self.db.upsert_asset(
                Asset(
                    lecture_id=lecture.id or 0,
                    kind="vtt",
                    remote_url=resolved.transcript_url,
                    status=DownloadStatus.AVAILABLE,
                )
            )
        return resolved

    @staticmethod
    def _output_stem(context: LectureContext) -> str:
        return lecture_stem(
            context.course_code,
            context.topic_name,
            context.lecture_number,
            context.title,
        )

    def _context_and_dir(self, lecture: Lecture) -> tuple[LectureContext, Path]:
        with self.db.connect() as conn:
            course = conn.execute(
                "SELECT * FROM courses WHERE id = ?", (lecture.course_id,)
            ).fetchone()
            topic = (
                conn.execute("SELECT * FROM topics WHERE id = ?", (lecture.topic_id,)).fetchone()
                if lecture.topic_id
                else None
            )
        if not course:
            raise ValueError("lecture has no course")
        topic_name = topic["name"] if topic else "Lectures"
        number = lecture.lecture_number or number_for_position(
            topic["numbering_prefix"] if topic else "", lecture.position or 1
        )
        title = lecture.custom_title or lecture.original_title
        base = (
            Path(topic["download_directory"])
            if topic and topic["download_directory"]
            else expand_path(self.settings.paths.download_root)
            / f"{course['code']} Recorded Lectures"
            / topic_name
        )
        context = LectureContext(
            course_code=course["code"],
            topic_name=topic_name,
            lecture_number=number,
            title=title,
            source="Echo360",
            source_recording_id=lecture.source_id,
            date=lecture.date,
        )
        return context, base

    async def _download_asset(
        self, lecture: Lecture, kind: str, topic_dir: Path, extension: str, options: PipelineOptions
    ) -> str:
        assets = [
            asset
            for asset in self.db.list_assets(lecture.id or 0)
            if asset.kind == kind and asset.remote_url
        ]
        if not assets:
            self.db.upsert_processing(
                ProcessingRecord(
                    lecture_id=lecture.id or 0, stage=f"download_{kind}", status="unavailable"
                )
            )
            return "unavailable"
        asset = assets[0]
        context, _ = self._context_and_dir(lecture)
        destination = topic_dir / f"{self._output_stem(context)}.{extension}"
        if destination.exists() and options.skip_existing and not options.overwrite:
            asset.local_path = str(destination)
            asset.status = DownloadStatus.COMPLETE
            self.db.upsert_asset(asset)
            self.db.upsert_processing(
                ProcessingRecord(
                    lecture_id=lecture.id or 0,
                    stage=f"download_{kind}",
                    status="skipped",
                    output_file=str(destination),
                )
            )
            return "skipped"
        try:
            status = await self.downloads.download(
                asset.remote_url or "", destination, overwrite=options.overwrite
            )
            asset.local_path = str(destination)
            asset.status = (
                DownloadStatus.COMPLETE
                if status in {"complete", "skipped"}
                else DownloadStatus(asset.status)
            )
            self.db.upsert_asset(asset)
            self.db.upsert_processing(
                ProcessingRecord(
                    lecture_id=lecture.id or 0,
                    stage=f"download_{kind}",
                    status=status,
                    output_file=str(destination),
                )
            )
            return status
        except Exception as exc:  # noqa: BLE001
            asset.status = DownloadStatus.FAILED
            asset.error_message = str(exc)
            self.db.upsert_asset(asset)
            self.db.upsert_processing(
                ProcessingRecord(
                    lecture_id=lecture.id or 0,
                    stage=f"download_{kind}",
                    status="failed",
                    error_message=str(exc),
                )
            )
            return "failed"

    async def _download_video(
        self,
        lecture: Lecture,
        context: LectureContext,
        topic_dir: Path,
        options: PipelineOptions,
        resolved: ResolvedEchoAssets,
    ) -> str:
        if not resolved.video_url or not resolved.audio_url:
            self.db.upsert_processing(
                ProcessingRecord(
                    lecture_id=lecture.id or 0, stage="download_video", status="unavailable"
                )
            )
            return "unavailable"
        destination = topic_dir / f"{self._output_stem(context)}.mp4"
        try:
            status = await self.downloads.download_and_mux(
                resolved.video_url,
                resolved.audio_url,
                destination,
                video_headers=resolved.headers_by_url.get(resolved.video_url),
                audio_headers=resolved.headers_by_url.get(resolved.audio_url),
                overwrite=options.overwrite,
            )
            asset = next(
                (item for item in self.db.list_assets(lecture.id or 0) if item.kind == "video"),
                None,
            )
            if asset:
                asset.local_path = str(destination)
                asset.status = DownloadStatus.COMPLETE
                self.db.upsert_asset(asset)
            self.db.upsert_processing(
                ProcessingRecord(
                    lecture_id=lecture.id or 0,
                    stage="download_video",
                    status=status,
                    output_file=str(destination),
                )
            )
            return status
        except Exception as exc:
            logger.exception("Video download failed for %s", context.title)
            self.db.upsert_processing(
                ProcessingRecord(
                    lecture_id=lecture.id or 0,
                    stage="download_video",
                    status="failed",
                    error_message=str(exc),
                )
            )
            return "failed"

    async def _download_slides(
        self,
        lecture: Lecture,
        context: LectureContext,
        topic_dir: Path,
        options: PipelineOptions,
        resolved: ResolvedEchoAssets,
    ) -> str:
        if not resolved.slides_url:
            self.db.upsert_processing(
                ProcessingRecord(
                    lecture_id=lecture.id or 0,
                    stage="download_slides",
                    status="unavailable",
                )
            )
            return "unavailable"
        extension = (resolved.slides_extension or "pdf").lower()
        if extension not in {"pdf", "ppt", "pptx"}:
            extension = "pdf"
        destination = topic_dir / f"{self._output_stem(context)}.{extension}"
        try:
            status = await self.downloads.download(
                resolved.slides_url,
                destination,
                overwrite=options.overwrite,
                headers=resolved.headers_by_url.get(resolved.slides_url),
            )
            asset = next(
                (item for item in self.db.list_assets(lecture.id or 0) if item.kind == "slides"),
                None,
            )
            if asset:
                asset.local_path = str(destination)
                asset.status = DownloadStatus.COMPLETE
                self.db.upsert_asset(asset)
            self.db.upsert_processing(
                ProcessingRecord(
                    lecture_id=lecture.id or 0,
                    stage="download_slides",
                    status=status,
                    output_file=str(destination),
                )
            )
            return status
        except Exception as exc:
            logger.exception("Slide download failed for %s", context.title)
            self.db.upsert_processing(
                ProcessingRecord(
                    lecture_id=lecture.id or 0,
                    stage="download_slides",
                    status="failed",
                    error_message=str(exc),
                )
            )
            return "failed"

    async def _prepare_transcript(
        self,
        lecture: Lecture,
        context: LectureContext,
        topic_dir: Path,
        options: PipelineOptions,
        headers_by_url: dict[str, dict[str, str]] | None = None,
    ) -> str:
        canonical = self._canonical_transcript_path(topic_dir, context)
        if canonical.exists() and options.skip_existing and not options.overwrite:
            self.db.upsert_processing(
                ProcessingRecord(
                    lecture_id=lecture.id or 0,
                    stage="canonical_transcript",
                    status="skipped",
                    output_file=str(canonical),
                )
            )
            return "skipped"

        transcript_assets = [
            asset
            for asset in self.db.list_assets(lecture.id or 0)
            if asset.kind in {"transcript", "vtt"} and asset.remote_url
        ]
        raw_text = ""
        method = TranscriptMethod.UNKNOWN
        if transcript_assets:
            asset = transcript_assets[0]
            suffix = (
                ".vtt"
                if asset.kind == "vtt" or (asset.remote_url or "").lower().endswith(".vtt")
                else ".txt"
            )
            raw_path = topic_dir / f"{self._output_stem(context)}.official{suffix}"
            try:
                await self.downloads.download(
                    asset.remote_url or "",
                    raw_path,
                    overwrite=options.overwrite,
                    headers=(headers_by_url or {}).get(asset.remote_url or ""),
                )
            except RuntimeError:
                if not raw_path.exists():
                    raise
                logger.warning(
                    "Using cached official transcript after Echo360 refresh failed: %s", raw_path
                )
            raw = raw_path.read_text(encoding="utf-8", errors="replace")
            if suffix == ".vtt":
                raw_text = clean_vtt(
                    raw, include_timestamps=self.settings.transcripts.include_timestamps
                )
                method = TranscriptMethod.OFFICIAL_VTT
            else:
                raw_text = clean_plain_transcript(raw)
                method = TranscriptMethod.OFFICIAL_TXT
        elif options.whisper_fallback and self.settings.transcripts.whisper_enabled:
            video = topic_dir / f"{self._output_stem(context)}.mp4"
            whisper_path = topic_dir / f"{self._output_stem(context)}.whisper.txt"
            transcribe_with_local_whisper(
                video,
                whisper_path,
                self.settings.transcripts.whisper_command,
                self.settings.transcripts.whisper_model,
                overwrite=options.overwrite,
            )
            raw_text = clean_plain_transcript(
                whisper_path.read_text(encoding="utf-8", errors="replace")
            )
            method = TranscriptMethod.WHISPER
        else:
            self.db.upsert_processing(
                ProcessingRecord(
                    lecture_id=lecture.id or 0, stage="canonical_transcript", status="unavailable"
                )
            )
            return "unavailable"

        canonical.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"course: {context.course_code}\n"
            f"topic: {context.topic_name}\n"
            f"lecture: {context.lecture_number}\n"
            f"title: {context.title}\n"
            f"source: {context.source}\n"
            f"source_recording_id: {context.source_recording_id}\n"
            f"transcript_method: {method.value}\n\n"
        )
        canonical.write_text(header + raw_text, encoding="utf-8")
        self.db.upsert_processing(
            ProcessingRecord(
                lecture_id=lecture.id or 0,
                stage="canonical_transcript",
                status="complete",
                transcript_hash=sha256_text(raw_text),
                transcript_file=str(canonical),
                output_file=str(canonical),
            )
        )
        return "complete"

    def _canonical_transcript_path(self, topic_dir: Path, context: LectureContext) -> Path:
        return topic_dir / f"{self._output_stem(context)}.txt"

    def _export_notebooklm(
        self, lecture: Lecture, context: LectureContext, transcript_path: Path
    ) -> str:
        transcript = transcript_path.read_text(encoding="utf-8")
        staging = expand_path(self.settings.paths.notebooklm_staging_dir)
        output_dir = staging / context.course_code / context.topic_name
        output = export_notebooklm(context, transcript, output_dir)
        self.db.upsert_processing(
            ProcessingRecord(
                lecture_id=lecture.id or 0,
                stage="notebooklm",
                status="complete",
                transcript_hash=sha256_text(transcript),
                transcript_file=str(transcript_path),
                output_file=str(output),
            )
        )
        return str(output)

    async def _generate_ai_note(
        self,
        lecture: Lecture,
        context: LectureContext,
        transcript_path: Path,
        options: PipelineOptions,
    ) -> str:
        transcript = transcript_path.read_text(encoding="utf-8")
        provider = get_ai_provider(self.settings.ai.provider)
        note_length = options.ai_note_length or self.settings.ai.note_length
        custom_instructions = (
            options.ai_custom_instructions.strip()
            if options.ai_custom_instructions.strip()
            else self.settings.ai.custom_instructions.strip()
        )
        prompt_id = (
            f"{self.settings.ai.prompt_version}:{note_length}:"
            f"{sha256_text(custom_instructions)[:10]}"
        )
        body = await provider.generate_note(
            AIRequest(
                system_prompt=STUDY_NOTE_SYSTEM_PROMPT,
                user_prompt=build_study_note_prompt(
                    context.course_code,
                    context.topic_name,
                    context.lecture_number,
                    context.title,
                    transcript,
                    note_length=note_length,
                    custom_instructions=custom_instructions,
                ),
                model=self.settings.ai.model,
                temperature=self.settings.ai.temperature,
                max_output_tokens=note_length_tokens(note_length),
            )
        )
        with self.db.connect() as conn:
            topic = (
                conn.execute("SELECT * FROM topics WHERE id = ?", (lecture.topic_id,)).fetchone()
                if lecture.topic_id
                else None
            )
        note = obsidian_note_markdown(
            context,
            body,
            parent_note=topic["parent_note"] if topic else None,
            ai_provider=provider.name,
            ai_model=self.settings.ai.model,
            prompt_version=prompt_id,
            transcript=transcript,
        )
        preview_dir = expand_path("local_data/previews")
        output = export_ai_note(
            note,
            preview_dir,
            context,
            options.ai_note_format,
            overwrite=options.overwrite,
        )
        if options.save_obsidian or self.settings.ai.auto_save_notes:
            vault = self.settings.paths.obsidian_vault
            if not vault:
                raise RuntimeError("Obsidian vault path is not configured.")
            destination = (
                Path(topic["obsidian_directory"])
                if topic and topic["obsidian_directory"]
                else expand_path(vault) / context.course_code / context.topic_name
            )
            markdown_output = save_obsidian_note(
                note, destination, context, overwrite=options.overwrite
            )
            output = markdown_output
            if options.ai_note_format != "markdown":
                output = export_ai_note(
                    note,
                    destination,
                    context,
                    options.ai_note_format,
                    overwrite=options.overwrite,
                    filename_stem=context.title,
                )
        self.db.upsert_processing(
            ProcessingRecord(
                lecture_id=lecture.id or 0,
                stage="ai_note",
                status="complete",
                ai_provider=provider.name,
                model=self.settings.ai.model,
                prompt_version=prompt_id,
                transcript_hash=sha256_text(transcript),
                transcript_file=str(transcript_path),
                output_file=str(output),
            )
        )
        return str(output)
