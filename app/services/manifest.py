from __future__ import annotations

from pathlib import Path

from app.database import Database
from app.utils import write_json


def export_topic_manifest(db: Database, topic_id: int, directory: Path) -> Path:
    path = directory / "_manifest.json"
    write_json(path, db.manifest_for_topic(topic_id))
    return path
