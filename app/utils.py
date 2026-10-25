from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

MACOS_FORBIDDEN = {":"}
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
WHITESPACE = re.compile(r"\s+")


def expand_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def safe_filename(value: str, max_length: int = 180) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = CONTROL_CHARS.sub("", normalized)
    normalized = "".join("-" if ch in MACOS_FORBIDDEN or ch in "/\\" else ch for ch in normalized)
    normalized = WHITESPACE.sub(" ", normalized).strip(" .")
    normalized = re.sub(r"[ _]{2,}", " ", normalized)
    if not normalized:
        normalized = "untitled"
    return normalized[:max_length].rstrip(" .")


def ensure_relative_child(root: Path, child: Path) -> Path:
    root = root.expanduser().resolve()
    target = child.expanduser().resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"path escapes configured root: {target}")
    return target


def lecture_stem(
    course_code: str,
    topic_name: str,
    lecture_number: str | None,
    title: str,
) -> str:
    parts = [course_code, topic_name]
    if lecture_number:
        parts.append(lecture_number)
    parts.append(title)
    return safe_filename(" ".join(part for part in parts if part))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def number_for_position(prefix: str, position: int) -> str:
    clean = prefix.strip().rstrip(".")
    if clean:
        return f"{clean}.{position}"
    return str(position)
