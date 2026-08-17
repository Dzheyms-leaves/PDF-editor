"""OCR endpoints and app settings."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException

from .. import config
from ..models import (
    OcrCapabilities,
    OcrPageResult,
    OcrRequest,
    OperationResult,
    SearchablePdfRequest,
)
from ..ocr import registry
from ..ocr.base import OcrError
from ..ocr.service import make_searchable, ocr_pages, ocr_region
from ..session import STORE
from .common import get_document_or_404, run_mutation

router = APIRouter(prefix="/api", tags=["ocr"])


@router.get("/ocr/capabilities", response_model=OcrCapabilities)
def ocr_capabilities() -> OcrCapabilities:
    return registry.capabilities()


@router.post("/ocr/refresh", response_model=OcrCapabilities)
def refresh_capabilities() -> OcrCapabilities:
    registry.refresh()
    return registry.capabilities()


@router.post("/ocr/warmup")
def warmup(engine: str = Body(default="", embed=True)) -> Dict[str, Any]:
    """Load model weights up front so the first real page isn't slow."""
    try:
        selected = registry.get_engine(engine or None)
        selected.warmup()
        return {"status": "ready", "engine": selected.name, "device": selected.device}
    except OcrError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/documents/{doc_id}/ocr", response_model=List[OcrPageResult])
def run_ocr(doc_id: str, req: OcrRequest) -> List[OcrPageResult]:
    get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        if req.region is not None and req.region_page is not None:
            return [
                ocr_region(
                    doc, req.region_page, req.region, req.engine, req.dpi,
                    mode=req.mode if req.mode != "markdown" else "plain",
                    force=req.force,
                )
            ]
        return ocr_pages(doc, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OcrError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        doc.close()


@router.post("/documents/{doc_id}/ocr/searchable", response_model=OperationResult)
def build_searchable(doc_id: str, req: SearchablePdfRequest) -> OperationResult:
    return run_mutation(
        doc_id,
        lambda d: make_searchable(d, req.pages, req.engine, req.dpi),
        "Invisible text layer added — the scan is now searchable",
    )


# ---------------------------------------------------------------- settings

@router.get("/settings")
def get_settings() -> Dict[str, Any]:
    values = config.load_settings()
    # Never echo a stored key back to the browser.
    if values.get("ocr_remote_api_key"):
        values["ocr_remote_api_key"] = "********"
    return values


@router.post("/settings")
def update_settings(values: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if values.get("ocr_remote_api_key") == "********":
        values.pop("ocr_remote_api_key")
    saved = config.save_settings(values)
    registry.refresh()
    if saved.get("ocr_remote_api_key"):
        saved = dict(saved)
        saved["ocr_remote_api_key"] = "********"
    return saved
