from __future__ import annotations

import argparse
import asyncio
import json
import socket
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from app.canvas.client import CanvasClient
from app.config import is_packaged, load_settings, prepare_runtime_environment
from app.database import Database
from app.models import PipelineOptions
from app.services.discovery import DiscoveryService
from app.services.pipeline import PipelineService


def main() -> None:
    prepare_runtime_environment()
    parser = argparse.ArgumentParser(prog="python -m app")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    sub.add_parser("login")
    sub.add_parser("courses")
    discover = sub.add_parser("discover")
    discover.add_argument("course")
    pipeline = sub.add_parser("pipeline")
    pipeline.add_argument("--course", required=True)
    pipeline.add_argument("--topic")
    pipeline.add_argument("--ai-notes", action="store_true")
    pipeline.add_argument("--save-obsidian", action="store_true")
    pipeline.add_argument("--overwrite", action="store_true")
    diag = sub.add_parser("diagnose-canvas")
    diag.add_argument("--output", default="local_data/diagnostics/canvas_links.json")
    args = parser.parse_args()

    if args.cmd is None:
        if is_packaged():
            args.cmd = "serve"
        else:
            parser.error("a command is required")

    settings = load_settings()
    db = Database(settings.database_path)

    if args.cmd == "serve":
        if is_packaged():
            if _server_is_ready(settings.app.host, settings.app.port):
                webbrowser.open(_local_url(settings.app.host, settings.app.port))
                return
            threading.Thread(
                target=_open_browser_when_ready,
                args=(settings.app.host, settings.app.port),
                daemon=True,
            ).start()
        uvicorn.run(
            "app.main:app",
            host=settings.app.host,
            port=settings.app.port,
            reload=not is_packaged(),
        )
    elif args.cmd == "login":
        asyncio.run(CanvasClient(settings).login())
    elif args.cmd == "courses":
        courses = asyncio.run(DiscoveryService(db, settings).sync_canvas_courses())
        print(json.dumps([course.model_dump() for course in courses], indent=2))
    elif args.cmd == "discover":
        result = asyncio.run(DiscoveryService(db, settings).discover_course(args.course))
        print(json.dumps(_jsonable(result), indent=2))
    elif args.cmd == "pipeline":
        course = db.get_course_by_code(args.course)
        if not course:
            raise SystemExit(f"Unknown course: {args.course}")
        topic_id = None
        if args.topic:
            topics = [topic for topic in db.list_topics(course.id or 0) if topic.name == args.topic]
            if not topics:
                raise SystemExit(f"Unknown topic: {args.topic}")
            topic_id = topics[0].id
        lectures = db.list_lectures(course.id or 0, topic_id=topic_id)
        options = PipelineOptions(ai_notes=args.ai_notes, save_obsidian=args.save_obsidian, overwrite=args.overwrite, skip_existing=not args.overwrite)
        result = asyncio.run(PipelineService(db, settings).run_many([lecture.id or 0 for lecture in lectures], options))
        print(json.dumps(result, indent=2))
    elif args.cmd == "diagnose-canvas":
        path = asyncio.run(CanvasClient(settings).capture_browser_diagnostics(Path(args.output)))
        print(path)


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _local_url(host: str, port: int) -> str:
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{browser_host}:{port}"


def _server_is_ready(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    try:
        with socket.create_connection((probe_host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _open_browser_when_ready(host: str, port: int) -> None:
    for _ in range(120):
        if _server_is_ready(host, port):
            webbrowser.open(_local_url(host, port))
            return
        time.sleep(0.1)


if __name__ == "__main__":
    main()
