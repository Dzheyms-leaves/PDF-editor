"""Per-supplier PO layout templates, stored as JSON in the workspace.

A template records the column bands and header regexes that worked for one
supplier, so the next PO from them parses correctly without re-guessing.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from .. import config
from ..models import POTemplate


def _path(template_id: str):
    return config.TEMPLATE_DIR / f"{template_id}.json"


def list_templates() -> List[POTemplate]:
    out: List[POTemplate] = []
    for path in sorted(config.TEMPLATE_DIR.glob("*.json")):
        try:
            out.append(POTemplate.model_validate_json(path.read_text("utf-8")))
        except Exception:  # noqa: BLE001 - skip corrupt files rather than crash
            continue
    return out


def get_template(template_id: str) -> Optional[POTemplate]:
    path = _path(template_id)
    if not path.exists():
        return None
    try:
        return POTemplate.model_validate_json(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def save_template(template: POTemplate) -> POTemplate:
    if not template.template_id:
        template.template_id = f"tpl_{uuid.uuid4().hex[:8]}"
    if not template.created_at:
        template.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _path(template.template_id).write_text(
        template.model_dump_json(indent=2), "utf-8"
    )
    return template


def delete_template(template_id: str) -> bool:
    path = _path(template_id)
    if path.exists():
        path.unlink()
        return True
    return False


def match_template(text: str) -> Optional[POTemplate]:
    """Pick the template whose supplier markers appear in the document text."""
    if not text:
        return None
    lowered = text.lower()
    best: Optional[POTemplate] = None
    best_score = 0
    for template in list_templates():
        score = 0
        for marker in template.supplier_match:
            marker = marker.strip().lower()
            if not marker:
                continue
            if marker.startswith("/") and marker.endswith("/"):
                try:
                    if re.search(marker[1:-1], lowered, re.IGNORECASE):
                        score += 2
                except re.error:
                    continue
            elif marker in lowered:
                score += len(marker)
        if score > best_score:
            best, best_score = template, score
    return best
