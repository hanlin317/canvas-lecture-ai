from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from app.ai.base import AIRequest
from app.ai.factory import get_ai_provider
from app.ai.prompts import COURSE_GROUP_SYSTEM_PROMPT, build_course_group_prompt
from app.canvas.client import CanvasClient
from app.config import Settings
from app.database import Database
from app.echo360.client import Echo360Client
from app.models import Asset, Course, DownloadStatus, Lecture, Topic


class DiscoveryService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.canvas = CanvasClient(settings)
        self.echo = Echo360Client(settings)

    async def sync_canvas_courses(self) -> list[Course]:
        canvas_courses = await self.canvas.list_courses(active_only=False)
        self.db.mark_all_courses_inactive()
        courses = []
        for item in canvas_courses:
            code = _normalize_course_code(item.code, item.name)
            courses.append(
                self.db.upsert_course(
                    Course(
                        canvas_id=item.canvas_id,
                        code=code,
                        name=item.name,
                        canvas_url=item.url,
                        group_name=_default_course_group(code, item.name),
                        is_active=True,
                    )
                )
            )
        return courses

    async def group_courses_with_ai(self, provider_name: str, model: str) -> int:
        courses = self.db.list_courses()
        if not courses:
            return 0
        provider = get_ai_provider(provider_name)
        payload = [
            {
                "canvas_id": course.canvas_id,
                "code": course.code,
                "name": course.name,
                "active": course.is_active,
            }
            for course in courses
        ]
        response = await provider.generate_note(
            AIRequest(
                system_prompt=COURSE_GROUP_SYSTEM_PROMPT,
                user_prompt=build_course_group_prompt(payload),
                model=model,
                temperature=0.1,
            )
        )
        groups = _parse_course_groups(response, {course.canvas_id for course in courses})
        return self.db.update_course_groups(groups)

    async def discover_course(
        self,
        course_code: str,
        diagnostic_dir: Path = Path("local_data/diagnostics"),
        progress: Callable[[str, int, int], None] | None = None,
    ) -> dict:
        total_steps = 6

        def report(message: str, completed: int) -> None:
            if progress:
                progress(message, completed, total_steps)

        report("Locating the selected Canvas course", 0)
        course = self.db.get_course(course_code)
        if not course:
            await self.sync_canvas_courses()
            course = self.db.get_course(course_code)
        if not course:
            raise ValueError(f"Course not found: {course_code}")
        report("Loading Canvas modules", 1)
        modules = await self.canvas.list_modules(course.canvas_id)
        report("Finding the Echo360 course link", 2)
        module_echo_links = self.canvas.echo360_links_from_modules(modules)
        navigation_echo_links = await self.canvas.echo360_navigation_links(course.canvas_id)
        echo_links = list(dict.fromkeys(navigation_echo_links + module_echo_links))
        report("Reading Echo360 folders and recordings", 3)
        recordings = []
        if echo_links:
            recordings = await self.echo.discover_course(
                echo_links[0],
                diagnostic_output=diagnostic_dir / f"{course.code}_echo360.json",
            )
        report("Saving lecture folders", 4)
        topics: dict[str, Topic] = {}
        for module_position, module in enumerate(modules, start=1):
            topic = self.db.upsert_topic(
                Topic(
                    course_id=course.id or 0,
                    name=module.name,
                    numbering_prefix=_infer_prefix(module.name),
                    position=module_position,
                )
            )
            topics[module.name] = topic
        folder_positions: dict[str, int] = {}
        for recording in recordings:
            topic_name = (recording.topic_hint or "").strip()
            if topic_name and topic_name not in topics:
                folder_positions.setdefault(topic_name, len(folder_positions) + 1)
                topics[topic_name] = self.db.upsert_topic(
                    Topic(
                        course_id=course.id or 0,
                        name=topic_name,
                        numbering_prefix=_infer_prefix(topic_name),
                        position=folder_positions[topic_name],
                    )
                )
        default_topic = next(iter(topics.values()), None) or self.db.upsert_topic(
            Topic(course_id=course.id or 0, name="Lectures", numbering_prefix="")
        )
        report("Saving discovered recordings", 5)
        topic_lecture_counts: dict[int, int] = {}
        for recording in recordings:
            topic = topics.get(recording.topic_hint or "") or default_topic
            topic_id = topic.id or 0
            topic_lecture_counts[topic_id] = topic_lecture_counts.get(topic_id, 0) + 1
            topic_lecture_number = topic_lecture_counts[topic_id]
            lecture = self.db.upsert_lecture(
                Lecture(
                    source_id=recording.source_id,
                    course_id=course.id or 0,
                    topic_id=topic.id,
                    original_title=recording.title,
                    lecture_number=str(topic_lecture_number),
                    recording_url=recording.url,
                    date=recording.date,
                    duration_seconds=recording.duration_seconds,
                    position=topic_lecture_number,
                )
            )
            for video in recording.videos:
                self.db.upsert_asset(
                    Asset(
                        lecture_id=lecture.id or 0,
                        kind="video",
                        remote_url=video.url,
                        quality=video.quality,
                        size_bytes=video.size_bytes,
                        status=DownloadStatus.AVAILABLE,
                    )
                )
            for transcript in recording.transcripts:
                self.db.upsert_asset(
                    Asset(
                        lecture_id=lecture.id or 0,
                        kind="vtt" if transcript.format == "vtt" else "transcript",
                        remote_url=transcript.url,
                        status=DownloadStatus.AVAILABLE,
                    )
                )
            for presentation in recording.presentations:
                extension = Path(presentation.filename).suffix.lower().lstrip(".") or "pdf"
                self.db.upsert_asset(
                    Asset(
                        lecture_id=lecture.id or 0,
                        kind="slides",
                        remote_url=f"echo360-presentation://{presentation.media_id}",
                        quality=extension,
                        size_bytes=presentation.size_bytes,
                        status=DownloadStatus.AVAILABLE,
                    )
                )
        report("Discovery complete", 6)
        return {"course": course, "echo_links": echo_links, "recordings": len(recordings)}


def _infer_prefix(name: str) -> str:
    match = re.search(r"\b(\d+)\b", name)
    return match.group(1) if match else ""


def _normalize_course_code(canvas_code: str, course_name: str) -> str:
    """Prefer the unit code users recognize over Canvas year/session prefixes."""
    matches: list[str] = []
    for value in (canvas_code, course_name):
        for match in re.findall(r"(?<![A-Z0-9])(SDDM\d{4})(?!\d)", value, re.IGNORECASE):
            normalized = match.upper()
            if normalized not in matches:
                matches.append(normalized)
    return "_".join(matches) if matches else canvas_code


def _default_course_group(code: str, name: str) -> str:
    value = f"{code} {name}".upper()
    if re.search(r"\b(SDDM|SDDP|DMD|BOH|DENT)", value):
        return "Dentistry"
    if re.search(r"\b(DATA|COMP|STAT|MATH|SCIE)", value):
        return "Data, Computing & Maths"
    if re.search(r"\b(ANAT|BIOL|PHSI|CHEM|MEDH)", value):
        return "Biomedical & Health"
    if re.search(r"\b(EXAM|FINAL|IN-SEMESTER|BYOD)", value):
        return "Exams & Assessments"
    if re.search(r"\b(OLE|OLET|OLES|OLEO)", value):
        return "Open Learning Environment"
    if re.search(r"RESOURCE|TRAINING|ORIENTATION|ACADEMIC|TRANSITION|PLACEMENT", value):
        return "University Resources"
    return "Other Courses"


def _parse_course_groups(response: str, valid_canvas_ids: set[str]) -> dict[str, str]:
    start = response.find("{")
    end = response.rfind("}")
    if start < 0 or end < start:
        raise ValueError("AI course grouping did not return a JSON object.")
    try:
        data = json.loads(response[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("AI course grouping returned invalid JSON.") from exc
    raw_groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(raw_groups, list) or not raw_groups or len(raw_groups) > 8:
        raise ValueError("AI course grouping must return between one and eight groups.")
    result: dict[str, str] = {}
    for group in raw_groups:
        if not isinstance(group, dict):
            raise TypeError("AI course grouping contains an invalid group.")
        name = str(group.get("name") or "").strip()
        canvas_ids = group.get("canvas_ids")
        if not name or len(name) > 36 or not isinstance(canvas_ids, list):
            raise ValueError("AI course grouping contains an invalid group name or ID list.")
        for canvas_id in canvas_ids:
            value = str(canvas_id)
            if value not in valid_canvas_ids or value in result:
                raise ValueError(
                    "AI course grouping contains an unknown or duplicate Canvas course ID."
                )
            result[value] = name
    if set(result) != valid_canvas_ids:
        raise ValueError("AI course grouping did not assign every course.")
    return result
