from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import load_settings
from app.database import Database
from app.logging_config import configure_logging
from app.web.routes import router


def create_app() -> FastAPI:
    settings = load_settings()
    configure_logging(Path(settings.app.log_path).expanduser())
    Database(settings.database_path)
    app = FastAPI(title="Dental Lecture Manager")
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static")
    return app


app = create_app()
