"""Static preview routes for JOAO Workbench v2 design options."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["workbench-preview"])

_PREVIEW_DIR = Path(__file__).resolve().parent.parent / "static" / "workbench-preview"


def _serve(name: str) -> FileResponse:
    path = _PREVIEW_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Preview missing: {name}")
    return FileResponse(path, media_type="text/html")


@router.get("/workbench-preview", include_in_schema=False)
async def workbench_preview_index() -> FileResponse:
    return _serve("index.html")


@router.get("/workbench-preview/option-a", include_in_schema=False)
async def workbench_preview_option_a() -> FileResponse:
    return _serve("option-a.html")


@router.get("/workbench-preview/option-b", include_in_schema=False)
async def workbench_preview_option_b() -> FileResponse:
    return _serve("option-b.html")


@router.get("/workbench-preview/option-c", include_in_schema=False)
async def workbench_preview_option_c() -> FileResponse:
    return _serve("option-c.html")


@router.get("/workbench-preview/option-d", include_in_schema=False)
async def workbench_preview_option_d() -> FileResponse:
    return _serve("option-d.html")


@router.get("/workbench-preview/option-e", include_in_schema=False)
async def workbench_preview_option_e() -> FileResponse:
    return _serve("option-e.html")
