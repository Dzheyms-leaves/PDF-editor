"""Antumbra panel designer: catalogue, validation and job export."""

from __future__ import annotations

import io
import json
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from .. import config, exporters
from ..designer import bom, catalogue, icons as icon_lib, jobs, render
from ..models import (
    DesignCheckResult,
    EngravingTemplate,
    DesignExportRequest,
    DesignToPanelsRequest,
    PanelDesign,
    PanelEntry,
    QuoteRequest,
)

router = APIRouter(prefix="/api/designer", tags=["designer"])


@router.get("/catalogue")
def get_catalogue() -> Dict[str, Any]:
    """Everything the browser needs to build its controls and preview."""
    return catalogue.as_dict()


@router.post("/check", response_model=DesignCheckResult)
def check(design: PanelDesign) -> DesignCheckResult:
    """Validate one configuration and return its code, summary and geometry."""
    try:
        catalogue.validate(design.family, design.series, design.region,
                           design.buttons, design.button_finish, design.rim_finish)
    except (ValueError, KeyError) as exc:
        return DesignCheckResult(ok=False, errors=[str(exc)])

    slots = catalogue.button_slots(design.family, design.buttons)
    warnings: List[str] = []

    limit = catalogue.MAX_CHARS_PER_LINE
    for item in design.engraving:
        if item.index >= slots:
            continue
        for line in item.lines:
            if len(line.strip()) > limit:
                warnings.append(
                    f"Position {item.index + 1}: \"{line.strip()}\" is longer than "
                    f"{limit} characters and will be engraved small"
                )
        if len(item.lines) > catalogue.MAX_LINES:
            warnings.append(
                f"Position {item.index + 1}: only {catalogue.MAX_LINES} lines fit "
                "on a button"
            )
        if item.icon and not icon_lib.get(item.icon):
            warnings.append(f"Position {item.index + 1}: unknown icon '{item.icon}'")

    return DesignCheckResult(
        ok=True,
        part_code=catalogue.part_code(design.family, design.series, design.region,
                                      design.buttons, design.button_finish,
                                      design.rim_finish),
        product=catalogue.product_name(design.family, design.series),
        slots=slots,
        warnings=warnings,
        summary=[list(pair) for pair in catalogue.describe(
            design.family, design.series, design.region, design.buttons,
            design.button_finish, design.rim_finish)],
        layout=catalogue.layout(design.family, design.series, design.region,
                                design.buttons),
    )


@router.post("/export")
def export(req: DesignExportRequest) -> Response:
    """Download a job as a spec sheet, an engraving schedule or a saved file."""
    if not req.designs:
        raise HTTPException(status_code=400, detail="There are no panels to export")

    stem = (req.job_name or "antumbra-job").replace("/", "-").strip() or "antumbra-job"
    try:
        if req.fmt == "pdf":
            payload = render.spec_sheet(
                [d.model_dump() for d in req.designs], req.job_name, req.project,
                req.client)
            media, name = "application/pdf", f"{stem}.pdf"
        elif req.fmt == "csv":
            payload = exporters.designs_csv(req.designs)
            media, name = "text/csv; charset=utf-8", f"{stem}.csv"
        elif req.fmt == "xlsx":
            payload = exporters.designs_xlsx(req.designs)
            media = ("application/vnd.openxmlformats-officedocument"
                     ".spreadsheetml.sheet")
            name = f"{stem}.xlsx"
        else:
            payload = json.dumps({
                "job_name": req.job_name,
                "project": req.project,
                "client": req.client,
                "designs": [d.model_dump() for d in req.designs],
            }, indent=2).encode("utf-8")
            media, name = "application/json", f"{stem}.json"
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return StreamingResponse(
        io.BytesIO(payload),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# -------------------------------------------------------- bill of materials

def _quote_settings(req: QuoteRequest) -> Dict[str, Any]:
    """Merge the stored commercial defaults with anything sent per request."""
    stored = config.load_settings()
    return {
        "price_book": stored.get("price_book") or {},
        "currency": req.currency or stored.get("quote_currency") or "AUD",
        "tax_rate": (req.tax_rate if req.tax_rate is not None
                     else stored.get("quote_tax_rate", 10.0)),
        "tax_label": req.tax_label or stored.get("quote_tax_label") or "GST",
        "terms": req.terms or stored.get("quote_terms") or "",
        "company": (stored.get("my_company_names") or [""])[0],
    }


def _build_bom(req: QuoteRequest):
    settings = _quote_settings(req)
    try:
        result = bom.build(
            req.designs,
            price_book=settings["price_book"],
            overrides=req.rates,
            extras=req.extras,
            include_engraving=req.include_engraving,
            currency=settings["currency"],
            tax_rate=float(settings["tax_rate"]),
            tax_label=settings["tax_label"],
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result, settings


@router.post("/bom")
def bill_of_materials(req: QuoteRequest) -> Dict[str, Any]:
    """Price the job on screen, grouping identical configurations."""
    result, _settings = _build_bom(req)
    return result.as_dict()


@router.post("/quote")
def quote(req: QuoteRequest) -> Response:
    """Download the job as a quotation or a bill of materials."""
    result, settings = _build_bom(req)
    stem = (req.job_name or "quote").replace("/", "-").strip() or "quote"

    if req.fmt == "pdf":
        payload = bom.quote_pdf(
            result, job_name=req.job_name, project=req.project, client=req.client,
            reference=req.reference, company=settings["company"],
            terms=settings["terms"])
        media, name = "application/pdf", f"{stem}.pdf"
    elif req.fmt == "csv":
        payload = exporters.bom_csv(result)
        media, name = "text/csv; charset=utf-8", f"{stem}.csv"
    else:
        payload = exporters.bom_xlsx(result)
        media = ("application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet")
        name = f"{stem}.xlsx"

    return StreamingResponse(
        io.BytesIO(payload), media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------- templates

@router.get("/templates", response_model=List[EngravingTemplate])
def list_templates() -> List[EngravingTemplate]:
    stored = config.get_setting("engraving_templates", []) or []
    return [EngravingTemplate(**item) for item in stored]


@router.post("/templates", response_model=List[EngravingTemplate])
def save_template(template: EngravingTemplate) -> List[EngravingTemplate]:
    """Save a label set, replacing any template of the same id."""
    if not template.name.strip():
        raise HTTPException(status_code=400, detail="Give the template a name")

    stored = list(config.get_setting("engraving_templates", []) or [])
    template.template_id = template.template_id or uuid.uuid4().hex[:10]
    kept = [item for item in stored if item.get("template_id") != template.template_id]
    kept.append(template.model_dump())
    config.save_settings({"engraving_templates": kept})
    return [EngravingTemplate(**item) for item in kept]


@router.delete("/templates/{template_id}", response_model=List[EngravingTemplate])
def delete_template(template_id: str) -> List[EngravingTemplate]:
    stored = list(config.get_setting("engraving_templates", []) or [])
    kept = [item for item in stored if item.get("template_id") != template_id]
    if len(kept) == len(stored):
        raise HTTPException(status_code=404, detail="No such template")
    config.save_settings({"engraving_templates": kept})
    return [EngravingTemplate(**item) for item in kept]


@router.post("/panels", response_model=List[PanelEntry])
def to_panels(req: DesignToPanelsRequest) -> List[PanelEntry]:
    """Hand a job to the panel queue so it can be engraved and exported."""
    entries = jobs.to_panel_entries(req.designs)
    if not entries:
        raise HTTPException(
            status_code=400,
            detail="None of these panels carry engraving, so there is nothing to queue",
        )
    return entries
