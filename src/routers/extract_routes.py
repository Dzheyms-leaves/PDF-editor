"""Purchase-order and panel extraction endpoints, plus their exports."""

from __future__ import annotations

import io
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from .. import config, exporters
from ..extract import panels as panels_mod
from ..extract import templates as template_store
from ..extract.purchase_order import parse_purchase_order, template_from_result
from ..extract.textgrid import PageGrid
from ..models import (
    PanelExportRequest,
    PanelExtractRequest,
    PanelExtractResult,
    POExtractRequest,
    POTemplate,
    PurchaseOrderResult,
)
from ..ocr.base import OcrError
from ..ocr.service import ocr_pages, page_has_text
from ..models import OcrRequest, PageSelection
from ..pdfops.common import resolve_pages
from ..session import STORE
from .common import get_document_or_404

router = APIRouter(prefix="/api", tags=["extraction"])

DEFAULT_MY_COMPANY = ["Automated Electrical Solutions", "AES"]


def _grids_for(
    doc, pages: List[int], force_ocr: bool, engine: Optional[str]
) -> tuple[List[PageGrid], str, Optional[str], List[str]]:
    """Build page grids from the text layer, falling back to OCR when needed."""
    warnings: List[str] = []
    needs_ocr = force_ocr or any(not page_has_text(doc, p) for p in pages)

    if not needs_ocr:
        return [PageGrid.from_page(doc[p - 1], p) for p in pages], "text", None, warnings

    request = OcrRequest(
        pages=PageSelection(mode="custom", custom=",".join(str(p) for p in pages)),
        engine=engine,
        mode="grounded",
        force=force_ocr,
    )
    try:
        results = ocr_pages(doc, request)
    except OcrError as exc:
        warnings.append(f"OCR unavailable: {exc}")
        return [PageGrid.from_page(doc[p - 1], p) for p in pages], "text", None, warnings

    grids: List[PageGrid] = []
    engine_used: Optional[str] = None
    for result in results:
        if result.warning:
            warnings.append(f"Page {result.page_number}: {result.warning}")
        if result.engine == "native":
            grids.append(PageGrid.from_page(doc[result.page_number - 1], result.page_number))
        else:
            engine_used = engine_used or result.engine
            grids.append(PageGrid.from_ocr(result))
    return grids, ("ocr" if engine_used else "text"), engine_used, warnings


# ------------------------------------------------------------ purchase orders

@router.post("/documents/{doc_id}/purchase-order", response_model=PurchaseOrderResult)
def extract_purchase_order(doc_id: str, req: POExtractRequest) -> PurchaseOrderResult:
    entry = get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        pages = resolve_pages(req.pages, doc.page_count)
        grids, source, engine, warnings = _grids_for(doc, pages, req.force_ocr, req.engine)

        template = None
        if req.template_id:
            template = template_store.get_template(req.template_id)
            if template is None:
                warnings.append(f"Template '{req.template_id}' not found — auto-detecting instead")
        if template is None:
            template = template_store.match_template("\n".join(g.text for g in grids))

        result = parse_purchase_order(
            grids,
            doc_id=doc_id,
            filename=entry.filename,
            template=template,
            my_company=config.get_setting("my_company_names", DEFAULT_MY_COMPANY),
            source=source,
            engine=engine,
        )
        result.warnings = warnings + result.warnings
        return result
    finally:
        doc.close()


@router.post("/purchase-orders/batch", response_model=List[PurchaseOrderResult])
def extract_purchase_orders(
    doc_ids: List[str] = Body(..., embed=True),
    force_ocr: bool = Body(default=False, embed=True),
    engine: Optional[str] = Body(default=None, embed=True),
) -> List[PurchaseOrderResult]:
    """Read several purchase orders in one call.

    A failure on one document is reported in that document's warnings rather
    than aborting the batch.
    """
    results: List[PurchaseOrderResult] = []
    for doc_id in doc_ids:
        try:
            results.append(
                extract_purchase_order(
                    doc_id,
                    POExtractRequest(force_ocr=force_ocr, engine=engine),
                )
            )
        except HTTPException as exc:
            results.append(
                PurchaseOrderResult(
                    doc_id=doc_id, filename=doc_id,
                    warnings=[f"Could not read this document: {exc.detail}"],
                )
            )
    return results


@router.post("/documents/{doc_id}/purchase-order/export")
def export_purchase_order(
    doc_id: str,
    result: PurchaseOrderResult,
    fmt: str = Query(default="csv", pattern="^(csv|tsv|xlsx|txt)$"),
) -> Response:
    entry = get_document_or_404(doc_id)
    stem = (result.header.po_number or entry.filename.rsplit(".", 1)[0]).replace("/", "-")

    if fmt == "xlsx":
        data = exporters.purchase_order_xlsx(result)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        name = f"{stem}.xlsx"
    elif fmt == "txt":
        data = exporters.purchase_order_text(result).encode("utf-8")
        media, name = "text/plain; charset=utf-8", f"{stem}.txt"
    else:
        delimiter = "\t" if fmt == "tsv" else ","
        data = exporters.purchase_order_csv(result, delimiter=delimiter)
        media = "text/csv; charset=utf-8"
        name = f"{stem}.{'tsv' if fmt == 'tsv' else 'csv'}"

    return StreamingResponse(
        io.BytesIO(data),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ---------------------------------------------------------------- templates

@router.get("/po-templates", response_model=List[POTemplate])
def list_templates() -> List[POTemplate]:
    return template_store.list_templates()


@router.post("/po-templates", response_model=POTemplate)
def save_template(template: POTemplate) -> POTemplate:
    return template_store.save_template(template)


@router.delete("/po-templates/{template_id}")
def delete_template(template_id: str) -> Dict[str, Any]:
    return {"deleted": template_store.delete_template(template_id)}


@router.post("/documents/{doc_id}/po-templates/learn", response_model=POTemplate)
def learn_template(
    doc_id: str,
    name: str = Body(..., embed=True),
    supplier_hint: Optional[str] = Body(default=None, embed=True),
    page: int = Body(default=1, embed=True),
) -> POTemplate:
    """Capture the column layout of a page the parser already reads correctly."""
    get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        if not 1 <= page <= doc.page_count:
            raise HTTPException(status_code=400, detail="Page out of range")
        grid = PageGrid.from_page(doc[page - 1], page)
        template = template_from_result(name, grid, supplier_hint)
        if template is None:
            raise HTTPException(
                status_code=400,
                detail="No line-item table was recognised on that page, so there is nothing to save",
            )
        return template_store.save_template(template)
    finally:
        doc.close()


# ------------------------------------------------------------------- panels

@router.post("/documents/{doc_id}/panels", response_model=PanelExtractResult)
def extract_panels(doc_id: str, req: PanelExtractRequest) -> PanelExtractResult:
    entry = get_document_or_404(doc_id)
    doc = STORE.open_doc(doc_id)
    try:
        pages = list(range(1, doc.page_count + 1))
        grids, source, engine, warnings = _grids_for(doc, pages, req.force_ocr, req.engine)
        stem = entry.filename.rsplit(".", 1)[0]
        style, entries = panels_mod.extract_panels(grids, stem, req.style or "auto")

        if not entries:
            warnings.append("No panels were recognised in this PDF")
        return PanelExtractResult(
            doc_id=doc_id,
            filename=entry.filename,
            style=style,
            entries=entries,
            used_ocr=(source == "ocr"),
            engine=engine,
            warnings=warnings,
        )
    finally:
        doc.close()


@router.post("/panels/groups")
def panel_groups(entries: List[Dict[str, Any]] = Body(...)) -> Dict[str, Any]:
    from ..models import PanelEntry

    parsed = [PanelEntry.model_validate(e) for e in entries]
    for entry in parsed:
        entry.config_key = panels_mod.config_key(entry)
    return {"groups": panels_mod.group_by_config(parsed)}


@router.post("/panels/export")
def export_panels(req: PanelExportRequest) -> Response:
    stem = (req.job_name or "panel-job").replace("/", "-")
    if req.fmt == "xlsx":
        data = exporters.panels_xlsx(req.entries)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        name = f"{stem}.xlsx"
    elif req.fmt == "txt":
        data = exporters.panels_text(req.entries).encode("utf-8")
        media, name = "text/plain; charset=utf-8", f"{stem}.txt"
    else:
        data = exporters.panels_csv(req.entries)
        media, name = "text/csv; charset=utf-8", f"{stem}.csv"

    return StreamingResponse(
        io.BytesIO(data),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
