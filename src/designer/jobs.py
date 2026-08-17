"""Adapters between panel designs and the rest of the workbench."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Sequence

from ..models import PanelDesign, PanelEntry, PanelLabel
from . import catalogue, icons as icon_lib


def engraving_text(item: Dict[str, Any] | Any) -> str:
    """The label a laser operator would type for one button position."""
    if isinstance(item, dict):
        lines = item.get("lines", []) or []
        icon = item.get("icon") or ""
    else:
        lines = getattr(item, "lines", []) or []
        icon = getattr(item, "icon", "") or ""
    text = " ".join(str(line).strip() for line in lines if str(line).strip())
    if text:
        return text
    found = icon_lib.get(icon)
    return f"[{found['name']}]" if found else ""


def to_panel_entries(designs: Sequence[PanelDesign]) -> List[PanelEntry]:
    """Turn designs into panel-queue entries, one per physical panel.

    Quantities are expanded so the operator can tick off each panel as it is
    engraved, and identical configurations fall into the queue's existing
    matching-configuration grouping on their own.
    """
    entries: List[PanelEntry] = []
    for design in designs:
        slots = catalogue.button_slots(design.family, design.buttons)
        if slots <= 0:
            continue

        by_index = {int(e.index): e for e in design.engraving}
        rows: List[List[PanelLabel]] = []
        for index in range(slots):
            text = engraving_text(by_index.get(index, {}))
            if index % 2 == 0:
                rows.append([])
            if text:
                rows[-1].append(PanelLabel(text=text, include=True))
        rows = [row for row in rows if row]
        if not rows:
            continue

        code = catalogue.part_code(design.family, design.series, design.region,
                                   design.buttons, design.button_finish,
                                   design.rim_finish)
        total = max(1, int(design.quantity or 1))
        for copy in range(1, total + 1):
            name = design.name or code
            if total > 1:
                name = f"{name} ({copy} of {total})"
            entries.append(PanelEntry(
                panel_id=uuid.uuid4().hex[:12],
                name=name,
                rows=[[label.model_copy() for label in row] for row in rows],
                page=1,
                style="dynalite",
                source_file=code,
            ))
    return entries
