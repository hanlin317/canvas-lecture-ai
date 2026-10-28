from __future__ import annotations

import re

TIMESTAMP = re.compile(r"(\d{2}:)?\d{2}:\d{2}\.\d{3}\s+-->\s+(\d{2}:)?\d{2}:\d{2}\.\d{3}.*")
WEBVTT_HEADER = re.compile(r"^\s*WEBVTT.*$", re.IGNORECASE)
HTML_TAG = re.compile(r"<[^>]+>")
LEADING_FILLER = re.compile(
    r"^(?:(?:okay|um+|uh+|so|right|you know)[,.:;!?\s]+)+",
    re.IGNORECASE,
)


def clean_vtt(vtt: str, include_timestamps: bool = False) -> str:
    lines: list[str] = []
    current_timestamp: str | None = None
    ignored_block = False
    for raw in vtt.splitlines():
        line = raw.strip()
        if not line:
            ignored_block = False
            continue
        if ignored_block:
            continue
        if re.match(r"^(NOTE|STYLE|REGION)(?:\s|$)", line, re.IGNORECASE):
            ignored_block = True
            continue
        if WEBVTT_HEADER.match(line) or line.isdigit():
            continue
        if TIMESTAMP.match(line):
            current_timestamp = line.split("-->")[0].strip()
            if include_timestamps:
                lines.append(f"[{current_timestamp}]")
            continue
        line = HTML_TAG.sub("", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return dedupe_caption_fragments(lines)


def clean_plain_transcript(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        line = HTML_TAG.sub("", raw).strip()
        line = re.sub(r"\s+", " ", line)
        if line:
            lines.append(line)
    return dedupe_caption_fragments(lines)


def dedupe_caption_fragments(lines: list[str]) -> str:
    output: list[str] = []
    previous = ""
    for line in lines:
        comparison = LEADING_FILLER.sub("", line).casefold()
        previous_comparison = LEADING_FILLER.sub("", previous).casefold()
        if comparison == previous_comparison:
            if output and len(line) < len(output[-1]):
                output[-1] = line
                previous = line
            continue
        if previous and line.startswith(previous):
            output[-1] = line
        elif previous and previous.endswith(line):
            pass
        else:
            output.append(line)
        previous = output[-1] if output else ""
    paragraphs: list[str] = []
    buffer: list[str] = []
    for line in output:
        buffer.append(line)
        if len(" ".join(buffer)) > 500 or line.endswith((".", "?", "!")):
            paragraphs.append(" ".join(buffer))
            buffer = []
    if buffer:
        paragraphs.append(" ".join(buffer))
    return "\n\n".join(paragraphs).strip() + ("\n" if paragraphs else "")
