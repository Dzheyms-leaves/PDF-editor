"""Job packs and operations that span every open document."""

from __future__ import annotations

import io
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..assemble import batch, pack
from ..models import (
    BatchMergeRequest,
    BatchOutcome,
    BatchRenameRequest,
    BatchRequest,
    BatchResult,
    BatchSplitRequest,
    PackRequest,
)
from ..session import STORE
from .common import zip_bytes

router = APIRouter(prefix="/api/batch", tags=["batch"])


def _resolve(doc_ids: List[str]) -> List[Tuple[str, str, bytes]]:
    """Look up (doc_id, filename, bytes), failing on the first unknown id."""
    if not doc_ids:
        raise HTTPException(status_code=400, detail="No documents were selected")
    out: List[Tuple[str, str, bytes]] = []
    for doc_id in doc_ids:
        try:
            entry = STORE.get(doc_id)
            out.append((doc_id, entry.filename, STORE.read_bytes(doc_id)))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return out


def _download(payload: bytes, name: str, media: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(payload),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _safe_name(name: str, fallback: str, suffix: str) -> str:
    clean = (name or "").replace("/", "-").replace("\\", "-").strip() or fallback
    return clean if clean.lower().endswith(suffix) else f"{clean}{suffix}"


# --------------------------------------------------------------------- pack

@router.post("/pack")
def build_pack(req: PackRequest) -> StreamingResponse:
    """Assemble the selected documents into one issued job pack."""
    resolved = _resolve([s.doc_id for s in req.sources])
    titles = {s.doc_id: s.title for s in req.sources}
    sources = [(titles.get(doc_id) or filename.rsplit(".", 1)[0], data)
               for doc_id, filename, data in resolved]

    logo = None
    if req.cover.enabled and req.cover.logo_asset:
        try:
            logo = STORE.asset_bytes(req.cover.logo_asset)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        payload = pack.build_pack(
            sources,
            cover=req.cover.model_dump() if req.cover.enabled else None,
            logo=logo,
            contents=req.contents,
            bookmarks=req.bookmarks,
            page_numbers=req.page_numbers,
            number_format=req.number_format,
            number_position=req.number_position,
            footer=req.footer,
            start_number=req.start_number,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _download(payload, _safe_name(req.filename, "job-pack", ".pdf"),
                     "application/pdf")


@router.post("/pack/preview")
def preview_pack(req: PackRequest) -> Dict[str, Any]:
    """Where each document will land, without building anything."""
    resolved = _resolve([s.doc_id for s in req.sources])
    titles = {s.doc_id: s.title for s in req.sources}
    rows = []
    for doc_id, filename, _data in resolved:
        info = STORE.describe(doc_id)
        rows.append((titles.get(doc_id) or filename.rsplit(".", 1)[0], info.total_pages))

    outline = pack.pack_outline(rows, cover=req.cover.enabled, contents=req.contents)
    front = (1 if req.cover.enabled else 0) + (
        pack.toc_page_count(len(rows)) if req.contents else 0)
    return {"sections": outline, "front_matter": front,
            "total_pages": front + sum(pages for _t, pages in rows)}


@router.post("/merge")
def merge(req: BatchMergeRequest) -> StreamingResponse:
    """Straight concatenation, with one bookmark per source."""
    resolved = _resolve(req.doc_ids)
    sources = [(filename.rsplit(".", 1)[0], data) for _id, filename, data in resolved]
    try:
        payload = pack.build_pack(sources, cover=None, contents=False,
                                  bookmarks=req.bookmarks, page_numbers=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _download(payload, _safe_name(req.filename, "merged", ".pdf"),
                     "application/pdf")


# -------------------------------------------------------------- operations

@router.post("/run")
def run(req: BatchRequest):
    """Apply one operation to every selected document.

    With ``in_place`` the results are written back into the session, where each
    lands on that document's undo stack; otherwise they come back as a ZIP and
    the originals are left alone.
    """
    resolved = _resolve(req.doc_ids)
    files: List[Tuple[str, bytes]] = []
    outcomes: List[BatchOutcome] = []
    failures = 0

    for doc_id, filename, data in resolved:
        try:
            payload = batch.apply(req.operation, data, dict(req.params), filename)
        except (ValueError, KeyError, TypeError) as exc:
            failures += 1
            outcomes.append(BatchOutcome(doc_id=doc_id, filename=filename,
                                         ok=False, detail=str(exc)))
            continue
        files.append((filename, payload))
        if req.in_place:
            STORE.replace_bytes(doc_id, payload)
        outcomes.append(BatchOutcome(doc_id=doc_id, filename=filename,
                                     pages=STORE.describe(doc_id).total_pages))

    if not files:
        detail = outcomes[0].detail if outcomes else "Nothing to do"
        raise HTTPException(status_code=400, detail=detail)

    if req.in_place:
        done = len(files)
        message = f"{req.operation} applied to {done} document{'' if done == 1 else 's'}"
        if failures:
            message += f", {failures} failed"
        return BatchResult(status="success", message=message, outcomes=outcomes)

    if len(files) == 1:
        return _download(files[0][1], files[0][0], "application/pdf")
    return _download(zip_bytes(files), f"{req.operation}.zip", "application/zip")


@router.post("/split")
def split(req: BatchSplitRequest) -> StreamingResponse:
    """Break each selected document up, returning every part in one ZIP."""
    resolved = _resolve(req.doc_ids)
    files: List[Tuple[str, bytes]] = []
    for _doc_id, filename, data in resolved:
        try:
            files.extend(batch.split(data, every=req.every, ranges=req.ranges,
                                     label=filename))
        except ValueError as exc:
            raise HTTPException(status_code=400,
                                detail=f"{filename}: {exc}") from exc
    if not files:
        raise HTTPException(status_code=400, detail="That split produced nothing")
    return _download(zip_bytes(files), "split.zip", "application/zip")


@router.post("/rename/preview")
def preview_rename(req: BatchRenameRequest) -> List[Dict[str, str]]:
    resolved = _resolve(req.doc_ids)
    rows = []
    for index, (doc_id, filename, _data) in enumerate(resolved, start=1):
        pages = STORE.describe(doc_id).total_pages
        rows.append({
            "doc_id": doc_id,
            "from": filename,
            "to": batch.rename(req.pattern, filename, index, pages,
                               req.project, req.revision),
        })
    return rows


@router.post("/rename")
def rename(req: BatchRenameRequest) -> StreamingResponse:
    """Download every selected document under its new name."""
    resolved = _resolve(req.doc_ids)
    files: List[Tuple[str, bytes]] = []
    for index, (doc_id, filename, data) in enumerate(resolved, start=1):
        pages = STORE.describe(doc_id).total_pages
        files.append((batch.rename(req.pattern, filename, index, pages,
                                   req.project, req.revision), data))
    return _download(zip_bytes(files), "renamed.zip", "application/zip")
