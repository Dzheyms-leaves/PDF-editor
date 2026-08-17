"""Document lifecycle: upload, inspect, render, download, undo/redo."""

from __future__ import annotations

import io
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse

from .. import config
from ..models import AssetInfo, DocumentInfo, OperationResult, PageTextLayer, WordBox
from ..pdfcompat import pymupdf
from ..session import STORE
from .common import describe_or_404, get_document_or_404

router = APIRouter(prefix="/api", tags=["documents"])


@router.post("/documents", response_model=List[DocumentInfo])
async def upload_documents(files: List[UploadFile] = File(...)) -> List[DocumentInfo]:
    max_bytes = int(config.get_setting("max_upload_mb", 300)) * 1024 * 1024
    uploaded: List[DocumentInfo] = []
    errors: List[str] = []

    for upload in files:
        name = upload.filename or "document.pdf"
        if not name.lower().strip().endswith(".pdf"):
            errors.append(f"{name}: not a PDF")
            continue
        data = await upload.read()
        if len(data) > max_bytes:
            errors.append(f"{name}: larger than {max_bytes // (1024 * 1024)} MB")
            continue
        try:
            uploaded.append(STORE.add_pdf_bytes(data, name))
        except ValueError as exc:
            errors.append(f"{name}: {exc}")

    if not uploaded and errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    return uploaded


@router.get("/documents", response_model=List[DocumentInfo])
def list_documents() -> List[DocumentInfo]:
    return STORE.list_documents()


@router.get("/documents/{doc_id}", response_model=DocumentInfo)
def get_document(doc_id: str) -> DocumentInfo:
    return describe_or_404(doc_id)


@router.delete("/documents/{doc_id}", response_model=OperationResult)
def delete_document(doc_id: str) -> OperationResult:
    get_document_or_404(doc_id)
    STORE.remove(doc_id)
    return OperationResult(status="success", message="Document closed")


@router.get("/documents/{doc_id}/pages/{page_no}/render")
def render_page(
    doc_id: str,
    page_no: int,
    dpi: int = Query(default=0, ge=0, le=600),
    width: int = Query(default=0, ge=0, le=4000),
) -> Response:
    """Render a page to PNG. Give either a dpi or a target pixel width."""
    get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        if not 1 <= page_no <= doc.page_count:
            raise HTTPException(status_code=404, detail="Page not found")
        page = doc[page_no - 1]
        if width:
            scale = width / page.rect.width if page.rect.width else 1.0
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        else:
            pix = page.get_pixmap(dpi=dpi or int(config.get_setting("preview_dpi", 110)),
                                  alpha=False)
        png = pix.tobytes("png")
    finally:
        doc.close()

    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/documents/{doc_id}/pages/{page_no}/text", response_model=PageTextLayer)
def page_text_layer(doc_id: str, page_no: int) -> PageTextLayer:
    """Word boxes for the selection overlay."""
    get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        if not 1 <= page_no <= doc.page_count:
            raise HTTPException(status_code=404, detail="Page not found")
        page = doc[page_no - 1]
        words = [
            WordBox(
                text=str(w[4]), x0=float(w[0]), y0=float(w[1]),
                x1=float(w[2]), y1=float(w[3]),
                block=int(w[5]), line=int(w[6]), word=int(w[7]),
            )
            for w in page.get_text("words")
        ]
        return PageTextLayer(
            page_number=page_no,
            width=float(page.rect.width),
            height=float(page.rect.height),
            words=words,
        )
    finally:
        doc.close()


@router.get("/documents/{doc_id}/download")
def download_document(doc_id: str, filename: Optional[str] = None) -> StreamingResponse:
    entry = get_document_or_404(doc_id)
    data = STORE.read_bytes(doc_id)
    name = filename or entry.filename
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/documents/{doc_id}/undo", response_model=OperationResult)
def undo(doc_id: str) -> OperationResult:
    get_document_or_404(doc_id)
    changed = STORE.undo(doc_id)
    return OperationResult(
        status="success" if changed else "noop",
        message="Undone" if changed else "Nothing to undo",
        document=describe_or_404(doc_id),
    )


@router.post("/documents/{doc_id}/redo", response_model=OperationResult)
def redo(doc_id: str) -> OperationResult:
    get_document_or_404(doc_id)
    changed = STORE.redo(doc_id)
    return OperationResult(
        status="success" if changed else "noop",
        message="Redone" if changed else "Nothing to redo",
        document=describe_or_404(doc_id),
    )


@router.post("/assets", response_model=AssetInfo)
async def upload_asset(file: UploadFile = File(...)) -> AssetInfo:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded image is empty")
    return STORE.add_asset(data, file.filename or "asset.png")


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str) -> Response:
    try:
        data = STORE.asset_bytes(asset_id)
        info = STORE.asset_info(asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    suffix = info.filename.rsplit(".", 1)[-1].lower()
    media = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "svg": "image/svg+xml", "gif": "image/gif",
    }.get(suffix, "application/octet-stream")
    return Response(content=data, media_type=media)


@router.post("/reset", response_model=OperationResult)
def reset_session() -> OperationResult:
    STORE.reset()
    return OperationResult(status="success", message="Session cleared")
