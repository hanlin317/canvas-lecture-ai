from __future__ import annotations

from pydantic import BaseModel


class CanvasModuleItem(BaseModel):
    id: str
    title: str
    type: str
    url: str | None = None
    html_url: str | None = None
    external_url: str | None = None


class CanvasModule(BaseModel):
    id: str
    name: str
    items: list[CanvasModuleItem] = []


class CanvasCourseSummary(BaseModel):
    canvas_id: str
    code: str
    name: str
    url: str | None = None
