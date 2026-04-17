"""Static preview routes for JOAO Workbench design options and mixed platform."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["workbench-preview"])

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_PREVIEW_DIR = _STATIC_DIR / "workbench-preview"
_REPORT_DIR = _STATIC_DIR / "reports"
_WORKBENCH_FILE = _STATIC_DIR / "workbench.html"


def _serve_preview(name: str) -> FileResponse:
    path = _PREVIEW_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Preview missing: {name}")
    return FileResponse(path, media_type="text/html")


@router.get("/workbench", include_in_schema=False)
async def workbench_mixed_platform() -> FileResponse:
    if not _WORKBENCH_FILE.exists():
        raise HTTPException(status_code=404, detail="Workbench mixed platform missing")
    return FileResponse(_WORKBENCH_FILE, media_type="text/html")


@router.get("/workbench-preview", include_in_schema=False)
async def workbench_preview_index() -> FileResponse:
    return _serve_preview("index.html")


@router.get("/workbench-preview/option-a", include_in_schema=False)
async def workbench_preview_option_a() -> FileResponse:
    return _serve_preview("option-a.html")


@router.get("/workbench-preview/option-b", include_in_schema=False)
async def workbench_preview_option_b() -> FileResponse:
    return _serve_preview("option-b.html")


@router.get("/workbench-preview/option-c", include_in_schema=False)
async def workbench_preview_option_c() -> FileResponse:
    return _serve_preview("option-c.html")


@router.get("/workbench-preview/option-d", include_in_schema=False)
async def workbench_preview_option_d() -> FileResponse:
    return _serve_preview("option-d.html")


@router.get("/workbench-preview/option-e", include_in_schema=False)
async def workbench_preview_option_e() -> FileResponse:
    return _serve_preview("option-e.html")


@router.get("/reports/e2e-test3", include_in_schema=False)
async def report_e2e_test3() -> FileResponse:
    path = _REPORT_DIR / "e2e-test3.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report missing")
    return FileResponse(path, media_type="text/html")
