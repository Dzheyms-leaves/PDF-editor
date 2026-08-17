"""Antumbra panel designer: catalogue, validation and job export."""

from __future__ import annotations

import io
import json
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from .. import exporters
from ..designer import catalogue, icons as icon_lib, jobs, render
from ..models import (
    DesignCheckResult,
    DesignExportRequest,
    DesignToPanelsRequest,
    PanelDesign,
    PanelEntry,
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
