"""Apply logo placements to pages."""

from __future__ import annotations

import io
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image

from ..models import PagePlacement, Rect
from ..pdfcompat import pymupdf
from .common import to_fitz_rect


def prepare_logo_bytes(logo_data: bytes, opacity: float = 1.0) -> bytes:
    """Normalise a logo to PNG and pre-multiply opacity into its alpha channel."""
    try:
        with Image.open(io.BytesIO(logo_data)) as img:
            rgba = img.convert("RGBA")
            if opacity < 0.99:
                r, g, b, a = rgba.split()
                a = a.point(lambda p: int(p * max(0.0, min(1.0, opacity))))
                rgba = Image.merge("RGBA", (r, g, b, a))
            buf = io.BytesIO()
            rgba.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    except Exception:  # noqa: BLE001 - hand the original bytes to PyMuPDF instead
        return logo_data


def logo_aspect_ratio(logo_data: bytes) -> Optional[float]:
    try:
        with Image.open(io.BytesIO(logo_data)) as img:
            w, h = img.size
            return (w / h) if h else None
    except Exception:  # noqa: BLE001
        return None


def stamp_document(
    doc: "pymupdf.Document",
    logo_bytes: bytes,
    placements: Sequence[PagePlacement],
    manual_overrides: Optional[Dict[int, Rect]] = None,
) -> Tuple[int, List[PagePlacement]]:
    """Stamp the logo onto each placed page. Returns (count, final placements)."""
    overrides = manual_overrides or {}
    stamped = 0
    final: List[PagePlacement] = []

    # Cache processed logo bytes per opacity — most jobs use a single value.
    cache: Dict[int, bytes] = {}

    def _logo_for(opacity: float) -> bytes:
        key = int(round(max(0.0, min(1.0, opacity)) * 100))
        if key not in cache:
            cache[key] = prepare_logo_bytes(logo_bytes, key / 100.0)
        return cache[key]

    for placement in placements:
        page_num = placement.page_number
        if not 1 <= page_num <= doc.page_count:
            final.append(placement)
            continue

        page = doc[page_num - 1]
        target: Optional[Rect] = None
        opacity = placement.opacity

        if page_num in overrides:
            target = overrides[page_num]
            placement.is_manual_override = True
            placement.placed = True
            placement.rect = target
            placement.message = "Manually positioned"
            if opacity <= 0:
                opacity = 1.0
        elif placement.placed and placement.rect is not None:
            target = placement.rect

        if target is None:
            final.append(placement)
            continue

        rect = to_fitz_rect(target) & page.rect
        if rect.is_empty or rect.width < 1 or rect.height < 1:
            placement.placed = False
            placement.message = "Placement fell outside the page and was skipped"
            final.append(placement)
            continue

        try:
            page.insert_image(
                rect,
                stream=_logo_for(opacity),
                keep_proportion=True,
                overlay=True,
                alpha=-1,
            )
            stamped += 1
            placement.placed = True
        except Exception as exc:  # noqa: BLE001 - one bad page shouldn't kill the batch
            placement.placed = False
            placement.message = f"Could not stamp this page: {exc}"

        final.append(placement)

    return stamped, final
