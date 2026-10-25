from __future__ import annotations

import io
import re
import textwrap
import zipfile
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from app.models import LectureContext
from app.utils import lecture_stem, safe_filename, sha256_text

AI_NOTE_FORMATS = {"markdown": ".md", "pdf": ".pdf", "docx": ".docx"}


def obsidian_note_markdown(
    context: LectureContext,
    body: str,
    *,
    parent_note: str | None,
    ai_provider: str,
    ai_model: str,
    prompt_version: str,
    transcript: str,
) -> str:
    up = f'"[[{parent_note}]]"' if parent_note else '""'
    return f"""---
up: {up}
course: {context.course_code}
lecture: "{context.lecture_number}"
source: {context.source}
ai_generated: true
ai_provider: "{ai_provider}"
ai_model: "{ai_model}"
prompt_version: "{prompt_version}"
transcript_hash: "{sha256_text(transcript)}"
generated_at: "{datetime.now(UTC).isoformat()}"
---

# {context.title}

{body.strip()}
"""


def save_obsidian_note(markdown: str, destination_dir: Path, context: LectureContext, overwrite: bool = False) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    path = destination_dir / f"{safe_filename(context.title)}.md"
    if path.exists() and not overwrite:
        return path
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(markdown, encoding="utf-8")
    tmp.replace(path)
    return path


def export_ai_note(
    markdown: str,
    destination_dir: Path,
    context: LectureContext,
    note_format: str,
    overwrite: bool = False,
    filename_stem: str | None = None,
) -> Path:
    suffix = AI_NOTE_FORMATS.get(note_format)
    if not suffix:
        raise ValueError(f"Unsupported AI note format: {note_format}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    filename = filename_stem or lecture_stem(
        context.course_code, context.topic_name, context.lecture_number, context.title
    )
    path = destination_dir / f"{filename}{suffix}"
    if path.exists() and not overwrite:
        return path
    if note_format == "markdown":
        payload = markdown.encode("utf-8")
    elif note_format == "docx":
        payload = _docx_bytes(markdown, context.title)
    else:
        payload = _pdf_bytes(markdown)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(payload)
    tmp.replace(path)
    return path


def _note_body(markdown: str) -> str:
    if not markdown.startswith("---\n"):
        return markdown.strip()
    end = markdown.find("\n---\n", 4)
    return markdown[end + 5 :].strip() if end >= 0 else markdown.strip()


def _plain_inline(text: str) -> str:
    text = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"(`{1,3}|\*\*|__|\*|_)", "", text)
    return text.strip()


def _docx_paragraph(text: str, style: str | None = None) -> str:
    style_xml = f'<w:pStyle w:val="{style}"/>' if style else ""
    paragraph_properties = f"<w:pPr>{style_xml}</w:pPr>" if style_xml else ""
    parts = re.split(r"(\*\*.*?\*\*)", text)
    runs = []
    for part in parts:
        if not part:
            continue
        bold = part.startswith("**") and part.endswith("**")
        value = _plain_inline(part[2:-2] if bold else part)
        if not value:
            continue
        properties = "<w:rPr><w:b/></w:rPr>" if bold else ""
        runs.append(
            f'<w:r>{properties}<w:t xml:space="preserve">{escape(value)}</w:t></w:r>'
        )
    return f"<w:p>{paragraph_properties}{''.join(runs)}</w:p>"


def _docx_bytes(markdown: str, title: str) -> bytes:
    paragraphs = []
    for raw_line in _note_body(markdown).splitlines():
        line = raw_line.strip()
        if not line:
            paragraphs.append("<w:p/>")
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            paragraphs.append(_docx_paragraph(heading.group(2), f"Heading{len(heading.group(1))}"))
            continue
        if re.match(r"^[-*]\s+", line):
            line = "• " + re.sub(r"^[-*]\s+", "", line)
        paragraphs.append(_docx_paragraph(line))

    document = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{''.join(paragraphs)}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr></w:body>
</w:document>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:sz w:val="22"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>
</w:styles>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>'''
    document_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    generated = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{escape(title)}</dc:title><dc:creator>Dental Lecture Manager</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">{generated}</dcterms:created>
</cp:coreProperties>'''
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/_rels/document.xml.rels", document_rels)
        archive.writestr("docProps/core.xml", core)
    return output.getvalue()


def _pdf_text(value: str) -> str:
    value = _plain_inline(value)
    value = value.replace("•", "-").replace("–", "-").replace("—", "-")
    return value.encode("ascii", "replace").decode("ascii").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_bytes(markdown: str) -> bytes:
    lines: list[tuple[str, str, int, int]] = []
    for raw_line in _note_body(markdown).splitlines():
        line = raw_line.strip()
        if not line:
            lines.append(("", "regular", 10, 0))
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            size = {1: 18, 2: 14, 3: 12}[len(heading.group(1))]
            lines.append((heading.group(2), "bold", size, 0))
            continue
        indent = 14 if re.match(r"^[-*]\s+", line) else 0
        if indent:
            line = "- " + re.sub(r"^[-*]\s+", "", line)
        lines.append((line, "regular", 10, indent))

    pages: list[list[tuple[str, str, int, int, float]]] = [[]]
    y = 788.0
    for text, font, size, indent in lines:
        width = max(35, int((487 - indent) / (size * 0.52)))
        wrapped = textwrap.wrap(_pdf_text(text), width=width, break_long_words=False) or [""]
        leading = size * 1.35
        for value in wrapped:
            if y - leading < 54:
                pages.append([])
                y = 788.0
            pages[-1].append((value, font, size, indent, y))
            y -= leading
        y -= 4 if font == "bold" else 1

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    }
    page_ids = []
    for index, page in enumerate(pages):
        page_id = 5 + index * 2
        content_id = page_id + 1
        page_ids.append(page_id)
        commands = []
        for value, font, size, indent, y_position in page:
            font_ref = "F2" if font == "bold" else "F1"
            commands.append(
                f"BT /{font_ref} {size} Tf 1 0 0 1 {54 + indent} {y_position:.1f} Tm ({value}) Tj ET"
            )
        stream = "\n".join(commands).encode("ascii")
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")
        objects[content_id] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id in range(1, max(objects) + 1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(output)
