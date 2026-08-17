"""Batch logo stamping with smart whitespace detection."""

from __future__ import annotations

import io
import json
from typing import Dict, List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from ..models import (
    BatchStampRequest,
    BatchStampResult,
    DocumentAnalysisResult,
    LogoConfig,
    OperationResult,
)
from ..pdfops.detector import analyze_document_placements
from ..pdfops.stamper import logo_aspect_ratio, prepare_logo_bytes, stamp_document
from ..session import STORE
from .common import describe_or_404, get_document_or_404, zip_bytes

router = APIRouter(prefix="/api/stamp", tags=["stamping"])


@router.post("/analyze", response_model=List[DocumentAnalysisResult])
def analyze(req: BatchStampRequest) -> List[DocumentAnalysisResult]:
    """Work out where the logo should go on every page, without writing."""
    aspect = None
    if req.logo_id:
        try:
            aspect = logo_aspect_ratio(STORE.asset_bytes(req.logo_id))
        except KeyError:
            aspect = None

    results: List[DocumentAnalysisResult] = []
    for doc_id in req.doc_ids:
        entry = get_document_or_404(doc_id)
        doc = STORE.open_doc(doc_id)
        try:
            placements = analyze_document_placements(doc, req.config, aspect)
        finally:
            doc.close()

        overrides = req.manual_overrides.get(doc_id, {})
        for placement in placements:
            if placement.page_number in overrides:
                placement.rect = overrides[placement.page_number]
                placement.placed = True
                placement.is_manual_override = True
                placement.message = "Manually positioned"

        results.append(
            DocumentAnalysisResult(
                doc_id=doc_id,
                filename=entry.filename,
                total_pages=len(placements),
                page_placements=placements,
            )
        )
    return results


@router.get("/preview/{doc_id}/{page_no}")
def preview(
    doc_id: str,
    page_no: int,
    logo_id: str = Query(...),
    x0: float = Query(...),
    y0: float = Query(...),
    x1: float = Query(...),
    y1: float = Query(...),
    opacity: float = Query(default=1.0, ge=0.0, le=1.0),
    dpi: int = Query(default=120, ge=36, le=400),
) -> Response:
    """Render a page with the logo composited in, without saving anything."""
    get_document_or_404(doc_id)
    try:
        logo = STORE.asset_bytes(logo_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    from ..pdfcompat import pymupdf

    doc = STORE.open_doc(doc_id)
    try:
        if not 1 <= page_no <= doc.page_count:
            raise HTTPException(status_code=404, detail="Page not found")
        page = doc[page_no - 1]
        page.insert_image(
            pymupdf.Rect(x0, y0, x1, y1),
            stream=prepare_logo_bytes(logo, opacity),
            keep_proportion=True,
            overlay=True,
        )
        png = page.get_pixmap(dpi=dpi, alpha=False).tobytes("png")
    finally:
        doc.close()
    return Response(content=png, media_type="image/png")


@router.post("/apply")
def apply_stamps(req: BatchStampRequest):
    """Stamp every selected document.

    With ``apply_in_place`` the open documents are edited (and stay undoable);
    otherwise the results come back as a ZIP and the originals are untouched.
    """
    try:
        logo = STORE.asset_bytes(req.logo_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Logo not found: {exc}") from exc

    aspect = logo_aspect_ratio(logo)
    outputs: List[tuple[str, bytes]] = []
    doc_results: List[DocumentAnalysisResult] = []
    total_stamped = 0

    for doc_id in req.doc_ids:
        entry = get_document_or_404(doc_id)
        overrides = req.manual_overrides.get(doc_id, {})

        def _apply(doc, _overrides=overrides):
            placements = analyze_document_placements(doc, req.config, aspect)
            return stamp_document(doc, logo, placements, _overrides)

        if req.apply_in_place:
            stamped, placements = STORE.mutate(doc_id, _apply)
        else:
            doc = STORE.open_doc(doc_id)
            try:
                stamped, placements = _apply(doc)
                outputs.append((f"stamped_{entry.filename}", doc.tobytes(garbage=3, deflate=True)))
            finally:
                doc.close()

        total_stamped += stamped
        doc_results.append(
            DocumentAnalysisResult(
                doc_id=doc_id, filename=entry.filename,
                total_pages=len(placements), page_placements=placements,
            )
        )

    if req.apply_in_place:
        return BatchStampResult(
            job_id="inplace",
            total_documents=len(doc_results),
            total_pages_stamped=total_stamped,
            documents=doc_results,
            download_url=None,
        )

    job_id, _job_dir = STORE.new_job_dir()
    summary = {
        "job_id": job_id,
        "total_documents": len(outputs),
        "total_pages_stamped": total_stamped,
        "config": req.config.model_dump(),
        "results": [d.model_dump() for d in doc_results],
    }
    archive = zip_bytes(
        [*outputs, ("batch_stamping_report.json", json.dumps(summary, indent=2).encode())]
    )
    STORE.register_job(job_id, zip_bytes=archive)

    return BatchStampResult(
        job_id=job_id,
        total_documents=len(outputs),
        total_pages_stamped=total_stamped,
        documents=doc_results,
        download_url=f"/api/stamp/download/{job_id}",
    )


@router.get("/download/{job_id}")
def download_job(job_id: str) -> StreamingResponse:
    job = STORE.jobs.get(job_id)
    if not job or "zip_bytes" not in job:
        raise HTTPException(status_code=404, detail="That batch has expired — run it again")
    return StreamingResponse(
        io.BytesIO(job["zip_bytes"]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="stamped_{job_id}.zip"'},
    )
