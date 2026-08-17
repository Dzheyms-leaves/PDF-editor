"""Editing endpoints: page ops, annotations, text/content editing, forms, security."""

from __future__ import annotations

import io
from typing import List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from ..models import (
    AddImageRequest,
    AnnotationRef,
    AnnotationSpec,
    ApplyRedactionsRequest,
    BatesRequest,
    CropRequest,
    DeleteAnnotationRequest,
    DeleteContentRequest,
    DeletePagesRequest,
    DuplicatePagesRequest,
    EditTextRequest,
    ExtractRequest,
    FillFormRequest,
    FindReplaceRequest,
    FormField,
    InsertBlankRequest,
    MergeRequest,
    MovePageRequest,
    MoveContentRequest,
    OperationResult,
    PageSelection,
    RedactSearchRequest,
    RedactionSpec,
    ReorderRequest,
    ReplaceImageRequest,
    RotateRequest,
    SignatureRequest,
    SplitRequest,
    TextSpanInfo,
    WatermarkRequest,
)
from ..pdfops import annots, forms, pageops, secure, textedit
from ..session import STORE
from .common import describe_or_404, get_document_or_404, run_mutation, zip_bytes

router = APIRouter(prefix="/api/documents/{doc_id}", tags=["editing"])


# ------------------------------------------------------------------ pages

@router.post("/pages/rotate", response_model=OperationResult)
def rotate(doc_id: str, req: RotateRequest) -> OperationResult:
    return run_mutation(
        doc_id, lambda d: {"rotated": pageops.rotate_pages(d, req.pages, req.degrees)},
        "Pages rotated",
    )


@router.post("/pages/delete", response_model=OperationResult)
def delete_pages(doc_id: str, req: DeletePagesRequest) -> OperationResult:
    return run_mutation(
        doc_id, lambda d: {"deleted": pageops.delete_pages(d, req.pages)}, "Pages deleted"
    )


@router.post("/pages/reorder", response_model=OperationResult)
def reorder(doc_id: str, req: ReorderRequest) -> OperationResult:
    return run_mutation(
        doc_id, lambda d: pageops.reorder_pages(d, req.order), "Pages reordered"
    )


@router.post("/pages/move", response_model=OperationResult)
def move_page(doc_id: str, req: MovePageRequest) -> OperationResult:
    return run_mutation(
        doc_id, lambda d: pageops.move_page(d, req.page, req.to_index), "Page moved"
    )


@router.post("/pages/duplicate", response_model=OperationResult)
def duplicate(doc_id: str, req: DuplicatePagesRequest) -> OperationResult:
    return run_mutation(
        doc_id, lambda d: {"duplicated": pageops.duplicate_pages(d, req.pages)},
        "Pages duplicated",
    )


@router.post("/pages/insert-blank", response_model=OperationResult)
def insert_blank(doc_id: str, req: InsertBlankRequest) -> OperationResult:
    return run_mutation(
        doc_id,
        lambda d: {"inserted": pageops.insert_blank_pages(
            d, req.after_page, req.count, req.width, req.height
        )},
        "Blank pages inserted",
    )


@router.post("/pages/crop", response_model=OperationResult)
def crop(doc_id: str, req: CropRequest) -> OperationResult:
    return run_mutation(
        doc_id, lambda d: {"cropped": pageops.crop_pages(d, req.pages, req.rect, req.unit)},
        "Pages cropped",
    )


@router.post("/pages/reset-crop", response_model=OperationResult)
def reset_crop(doc_id: str, req: DeletePagesRequest) -> OperationResult:
    return run_mutation(
        doc_id, lambda d: {"reset": pageops.reset_crop(d, req.pages)}, "Crop reset"
    )


@router.post("/merge", response_model=OperationResult)
def merge(doc_id: str, req: MergeRequest) -> OperationResult:
    sources = []
    for other_id in req.doc_ids:
        entry = get_document_or_404(other_id)
        sources.append((entry.filename, STORE.read_bytes(other_id)))
    return run_mutation(
        doc_id,
        lambda d: {"pages_added": pageops.merge_documents(d, sources, req.insert_after)},
        "Documents merged",
    )


@router.post("/split")
def split(doc_id: str, req: SplitRequest) -> StreamingResponse:
    entry = get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        parts = pageops.split_document(doc, req, entry.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        doc.close()

    archive = zip_bytes(parts)
    stem = entry.filename.rsplit(".", 1)[0]
    return StreamingResponse(
        io.BytesIO(archive),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{stem}_split.zip"'},
    )


@router.post("/extract")
def extract(doc_id: str, req: ExtractRequest) -> StreamingResponse:
    entry = get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        data = pageops.extract_pages(doc, req.pages)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        doc.close()

    stem = entry.filename.rsplit(".", 1)[0]
    name = req.filename or f"{stem}_extract.pdf"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ------------------------------------------------------------ annotations

@router.get("/annotations", response_model=List[AnnotationRef])
def list_annotations(doc_id: str, page: Optional[int] = None) -> List[AnnotationRef]:
    get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        return annots.list_annotations(doc, page)
    finally:
        doc.close()


@router.post("/annotations", response_model=OperationResult)
def add_annotation(doc_id: str, spec: AnnotationSpec) -> OperationResult:
    return run_mutation(
        doc_id, lambda d: annots.add_annotation(d, spec).model_dump(), "Annotation added"
    )


@router.post("/annotations/batch", response_model=OperationResult)
def add_annotations(doc_id: str, specs: List[AnnotationSpec]) -> OperationResult:
    def _apply(doc):
        return {"added": [annots.add_annotation(doc, s).model_dump() for s in specs]}

    return run_mutation(doc_id, _apply, f"{len(specs)} annotations added")


@router.post("/annotations/delete", response_model=OperationResult)
def delete_annotations(doc_id: str, req: DeleteAnnotationRequest) -> OperationResult:
    return run_mutation(
        doc_id,
        lambda d: {"removed": annots.delete_annotations(d, req.page, req.indices)},
        "Annotations deleted",
    )


@router.post("/annotations/flatten", response_model=OperationResult)
def flatten_annotations(doc_id: str, pages: Optional[List[int]] = Body(default=None)) -> OperationResult:
    return run_mutation(
        doc_id, lambda d: {"flattened": annots.flatten_annotations(d, pages)},
        "Annotations flattened",
    )


# ------------------------------------------------------------ text/content

@router.get("/pages/{page_no}/spans", response_model=List[TextSpanInfo])
def page_spans(doc_id: str, page_no: int) -> List[TextSpanInfo]:
    get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        if not 1 <= page_no <= doc.page_count:
            raise HTTPException(status_code=404, detail="Page not found")
        return textedit.describe_spans(doc[page_no - 1], page_no)
    finally:
        doc.close()


@router.post("/text/edit", response_model=OperationResult)
def edit_text(doc_id: str, req: EditTextRequest) -> OperationResult:
    return run_mutation(
        doc_id,
        lambda d: textedit.edit_text(
            d, req.page, req.rect, req.new_text, req.font, req.size,
            req.colour, req.align, req.background,
        ),
        "Text replaced",
    )


@router.post("/content/delete", response_model=OperationResult)
def delete_content(doc_id: str, req: DeleteContentRequest) -> OperationResult:
    return run_mutation(
        doc_id,
        lambda d: textedit.delete_content(d, req.page, req.rect, req.background),
        "Content erased",
    )


@router.post("/content/move", response_model=OperationResult)
def move_content(doc_id: str, req: MoveContentRequest) -> OperationResult:
    return run_mutation(
        doc_id,
        lambda d: textedit.move_content(d, req.page, req.rect, req.dx, req.dy, req.background),
        "Content moved",
    )


@router.get("/pages/{page_no}/images")
def list_images(doc_id: str, page_no: int):
    get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        return {"images": textedit.list_images(doc, page_no)}
    finally:
        doc.close()


@router.post("/images/add", response_model=OperationResult)
def add_image(doc_id: str, req: AddImageRequest) -> OperationResult:
    try:
        data = STORE.asset_bytes(req.asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return run_mutation(
        doc_id,
        lambda d: textedit.add_image(d, req.page, req.rect, data, req.opacity, req.keep_aspect),
        "Image placed",
    )


@router.post("/images/replace", response_model=OperationResult)
def replace_image(doc_id: str, req: ReplaceImageRequest) -> OperationResult:
    try:
        data = STORE.asset_bytes(req.asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return run_mutation(
        doc_id,
        lambda d: textedit.replace_image(d, req.page, data, req.xref, req.rect),
        "Image replaced",
    )


@router.post("/text/find")
def find_text(doc_id: str, req: FindReplaceRequest):
    get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        hits = textedit.find_text(
            doc, req.find, req.pages, req.match_case, req.whole_word, req.limit
        )
        return {"hits": hits, "count": len(hits)}
    finally:
        doc.close()


@router.post("/text/replace", response_model=OperationResult)
def replace_text(doc_id: str, req: FindReplaceRequest) -> OperationResult:
    return run_mutation(
        doc_id,
        lambda d: textedit.find_and_replace(
            d, req.find, req.replace, req.pages, req.match_case, req.whole_word, req.limit
        ),
        "Text replaced",
    )


# ------------------------------------------------------------------ forms

@router.get("/form/fields", response_model=List[FormField])
def form_fields(doc_id: str) -> List[FormField]:
    get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        return forms.list_fields(doc)
    finally:
        doc.close()


@router.post("/form/fill", response_model=OperationResult)
def fill_form(doc_id: str, req: FillFormRequest) -> OperationResult:
    def _apply(doc):
        result = forms.fill_fields(doc, req.values)
        if req.flatten:
            result["flattened"] = forms.flatten_form(doc)
        return result

    return run_mutation(doc_id, _apply, "Form updated")


@router.post("/form/flatten", response_model=OperationResult)
def flatten_form(doc_id: str) -> OperationResult:
    return run_mutation(
        doc_id, lambda d: {"flattened": forms.flatten_form(d)}, "Form flattened"
    )


@router.post("/signature", response_model=OperationResult)
def place_signature(doc_id: str, req: SignatureRequest) -> OperationResult:
    data = None
    if req.asset_id:
        try:
            data = STORE.asset_bytes(req.asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return run_mutation(
        doc_id,
        lambda d: forms.place_signature(
            d, req.page, req.rect, data, req.strokes, req.colour, req.stroke_width, req.flatten
        ),
        "Signature placed",
    )


# --------------------------------------------------------------- security

@router.post("/redact/find")
def find_redactions(doc_id: str, req: RedactSearchRequest):
    get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        targets = secure.find_redaction_targets(doc, req.patterns, req.pages, req.match_case)
    finally:
        doc.close()

    if req.apply_now and targets:
        run_mutation(
            doc_id, lambda d: secure.apply_redactions(d, targets), "Redactions applied"
        )
    return {"targets": [t.model_dump() for t in targets], "count": len(targets),
            "applied": bool(req.apply_now and targets)}


@router.post("/redact/apply", response_model=OperationResult)
def apply_redactions(doc_id: str, req: ApplyRedactionsRequest) -> OperationResult:
    return run_mutation(
        doc_id,
        lambda d: secure.apply_redactions(
            d, req.redactions, req.remove_images, req.scrub_metadata
        ),
        "Redactions applied — content permanently removed",
    )


@router.post("/watermark", response_model=OperationResult)
def watermark(doc_id: str, req: WatermarkRequest) -> OperationResult:
    data = None
    if req.asset_id:
        try:
            data = STORE.asset_bytes(req.asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return run_mutation(
        doc_id,
        lambda d: {"pages": secure.add_watermark(
            d, req.pages, req.text, data, req.opacity, req.rotation,
            req.font_size, req.colour, req.scale, req.position,
        )},
        "Watermark applied",
    )


@router.post("/bates", response_model=OperationResult)
def bates(doc_id: str, req: BatesRequest) -> OperationResult:
    return run_mutation(
        doc_id,
        lambda d: secure.add_bates_numbers(
            d, req.pages, req.prefix, req.suffix, req.start, req.digits,
            req.position, req.font_size, req.colour, req.margin,
        ),
        "Bates numbering applied",
    )


@router.post("/encrypt")
def encrypt(doc_id: str, user_pw: str = Body(""), owner_pw: str = Body("")) -> StreamingResponse:
    entry = get_document_or_404(doc_id)
    data = secure.set_password(STORE.read_bytes(doc_id), user_pw, owner_pw)
    stem = entry.filename.rsplit(".", 1)[0]
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{stem}_protected.pdf"'},
    )
