from __future__ import annotations

from pathlib import Path

from app.models import LectureContext
from app.utils import lecture_stem


def notebooklm_markdown(context: LectureContext, transcript: str) -> str:
    return f"""---
course: {context.course_code}
topic: {context.topic_name}
lecture: "{context.lecture_number}"
title: "{context.title}"
source: {context.source}
source_recording_id: "{context.source_recording_id}"
transcript_method: {context.transcript_method}
---

# {context.course_code} - {context.topic_name}

## Lecture {context.lecture_number} - {context.title}

## Transcript

{transcript.strip()}
"""


def export_notebooklm(context: LectureContext, transcript: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = (
        output_dir
        / f"{lecture_stem(context.course_code, context.topic_name, context.lecture_number, context.title)}.md"
    )
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(notebooklm_markdown(context, transcript), encoding="utf-8")
    tmp.replace(path)
    return path
