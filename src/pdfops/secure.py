"""True redaction, watermarking, Bates numbering and metadata scrubbing."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from ..models import PageSelection, RedactionSpec, Rect
from ..pdfcompat import pymupdf
from .common import (
    clamp_rect_to_page,
    fit_font_size,
    from_fitz_rect,
    hex_to_rgb,
    resolve_pages,
    to_fitz_rect,
)

_REDACT_IMAGE_REMOVE = getattr(pymupdf, "PDF_REDACT_IMAGE_REMOVE", 1)
_REDACT_IMAGE_PIXELS = getattr(pymupdf, "PDF_REDACT_IMAGE_PIXELS", 2)
_REDACT_IMAGE_NONE = getattr(pymupdf, "PDF_REDACT_IMAGE_NONE", 0)


def apply_redactions(
    doc: "pymupdf.Document",
    redactions: Sequence[RedactionSpec],
    remove_images: bool = True,
    scrub_metadata: bool = True,
) -> Dict:
    """Permanently remove content under each rect.

    This is real redaction: the glyphs and image pixels are deleted from the
    content stream, not merely covered with a black rectangle.
    """
    if not redactions:
        raise ValueError("No redaction areas supplied")

    by_page: Dict[int, List[RedactionSpec]] = {}
    for spec in redactions:
        by_page.setdefault(spec.page, []).append(spec)

    applied = 0
    for page_no, specs in sorted(by_page.items()):
        if not 1 <= page_no <= doc.page_count:
            continue
        page = doc[page_no - 1]
        for spec in specs:
            box = clamp_rect_to_page(to_fitz_rect(spec.rect), page)
            if box.is_empty or box.width < 0.5 or box.height < 0.5:
                continue
            fill = hex_to_rgb(spec.fill, (0.0, 0.0, 0.0))
            kwargs = {"fill": fill}
            if spec.overlay_text:
                kwargs.update(
                    text=spec.overlay_text,
                    text_color=hex_to_rgb(spec.text_colour, (1.0, 1.0, 1.0)),
                    fontsize=max(5.0, min(11.0, box.height * 0.6)),
                    align=pymupdf.TEXT_ALIGN_CENTER,
                )
            page.add_redact_annot(box, **kwargs)
            applied += 1
        page.apply_redactions(
            images=_REDACT_IMAGE_REMOVE if remove_images else _REDACT_IMAGE_NONE
        )

    if scrub_metadata:
        scrub_document_metadata(doc)

    return {"redactions_applied": applied, "pages": sorted(by_page.keys())}


def scrub_document_metadata(doc: "pymupdf.Document") -> None:
    """Clear document info and XMP so redacted text cannot leak via metadata."""
    doc.set_metadata({})
    try:
        doc.del_xml_metadata()
    except Exception:  # noqa: BLE001 - not every PDF carries XMP
        pass


def find_redaction_targets(
    doc: "pymupdf.Document",
    patterns: Sequence[str],
    selection: PageSelection,
    match_case: bool = False,
) -> List[RedactionSpec]:
    """Locate text to redact. ``/.../`` entries are treated as regexes."""
    targets: List[RedactionSpec] = []
    pages = resolve_pages(selection, doc.page_count)

    literals = [p for p in patterns if not (p.startswith("/") and p.rstrip("i").endswith("/"))]
    regexes: List[re.Pattern] = []
    for raw in patterns:
        if raw.startswith("/") and raw.rstrip("i").endswith("/"):
            body = raw[1:].rstrip("i").rstrip("/")
            flags = 0 if match_case else re.IGNORECASE
            try:
                regexes.append(re.compile(body, flags))
            except re.error:
                continue

    search_flags = 0 if match_case else getattr(pymupdf, "TEXT_IGNORECASE", 8)

    for page_no in pages:
        page = doc[page_no - 1]
        for needle in literals:
            if not needle:
                continue
            try:
                boxes = page.search_for(needle, flags=search_flags)
            except TypeError:
                boxes = page.search_for(needle)
            for box in boxes:
                targets.append(RedactionSpec(page=page_no, rect=from_fitz_rect(box)))

        if regexes:
            words = page.get_text("words")
            for x0, y0, x1, y1, word, *_ in words:
                if any(rx.search(word) for rx in regexes):
                    targets.append(
                        RedactionSpec(
                            page=page_no,
                            rect=Rect(x0=x0, y0=y0, x1=x1, y1=y1),
                        )
                    )
    return targets


def add_watermark(
    doc: "pymupdf.Document",
    selection: PageSelection,
    text: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    opacity: float = 0.15,
    rotation: float = 45.0,
    font_size: float = 60.0,
    colour: str = "#b00020",
    scale: float = 0.6,
    position: str = "center",
) -> int:
    if not text and not image_bytes:
        raise ValueError("A watermark needs either text or an image")

    pages = resolve_pages(selection, doc.page_count)
    rgb = hex_to_rgb(colour, (0.69, 0.0, 0.13))
    stamped = 0

    for page_no in pages:
        page = doc[page_no - 1]
        bounds = page.rect

        if image_bytes:
            width = bounds.width * max(0.05, min(1.0, scale))
            height = width  # insert_image keeps proportion inside the box
            spots = _watermark_spots(bounds, width, height, position)
            for spot in spots:
                page.insert_image(
                    spot, stream=image_bytes, overlay=True, keep_proportion=True, alpha=-1
                )
            stamped += 1
            continue

        size = font_size
        box_width = bounds.width * 0.9
        font = pymupdf.Font("hebo")
        text_width = font.text_length(text or "", fontsize=size)
        if text_width > box_width and text_width > 0:
            size = size * (box_width / text_width)

        spots = _watermark_spots(bounds, box_width, size * 1.6, position)
        for spot in spots:
            page.insert_textbox(
                spot,
                text or "",
                fontname="hebo",
                fontsize=size,
                color=rgb,
                align=pymupdf.TEXT_ALIGN_CENTER,
                rotate=0,
                fill_opacity=opacity,
                stroke_opacity=opacity,
                morph=(
                    pymupdf.Point(spot.x0 + spot.width / 2, spot.y0 + spot.height / 2),
                    pymupdf.Matrix(rotation),
                ),
            )
        stamped += 1
    return stamped


def _watermark_spots(bounds, width: float, height: float, position: str) -> List["pymupdf.Rect"]:
    cx, cy = bounds.x0 + bounds.width / 2, bounds.y0 + bounds.height / 2
    half_w, half_h = width / 2, height / 2

    if position == "tile":
        spots = []
        step_y = bounds.height / 3
        for row in range(3):
            y = bounds.y0 + step_y * row + step_y / 2
            spots.append(pymupdf.Rect(cx - half_w, y - half_h, cx + half_w, y + half_h))
        return spots
    if position == "top":
        cy = bounds.y0 + bounds.height * 0.18
    elif position == "bottom":
        cy = bounds.y0 + bounds.height * 0.82
    return [pymupdf.Rect(cx - half_w, cy - half_h, cx + half_w, cy + half_h)]


def add_bates_numbers(
    doc: "pymupdf.Document",
    selection: PageSelection,
    prefix: str = "",
    suffix: str = "",
    start: int = 1,
    digits: int = 6,
    position: str = "bottom-right",
    font_size: float = 9.0,
    colour: str = "#333333",
    margin: float = 24.0,
) -> Dict:
    pages = resolve_pages(selection, doc.page_count)
    rgb = hex_to_rgb(colour, (0.2, 0.2, 0.2))
    counter = int(start)
    stamped: List[str] = []

    for page_no in pages:
        page = doc[page_no - 1]
        bounds = page.rect
        label = f"{prefix}{counter:0{max(1, digits)}d}{suffix}"
        font = pymupdf.Font("helv")
        width = font.text_length(label, fontsize=font_size)
        # insert_textbox needs real slack: a box sized to the glyphs alone is
        # rejected outright and silently draws nothing.
        height = font_size * 2.2

        vertical, _, horizontal = position.partition("-")
        if vertical == "top":
            y0 = bounds.y0 + margin
        else:
            y0 = bounds.y1 - margin - height
        if horizontal == "left":
            x0 = bounds.x0 + margin
        elif horizontal == "center":
            x0 = bounds.x0 + (bounds.width - width) / 2
        else:
            x0 = bounds.x1 - margin - width

        box = pymupdf.Rect(x0 - 3, y0, x0 + width + 8, y0 + height) & bounds
        fitted = page.insert_textbox(
            box, label, fontname="helv", fontsize=font_size, color=rgb,
            align=pymupdf.TEXT_ALIGN_LEFT,
        )
        if fitted < 0:
            # Last resort: draw on a baseline so a number always lands.
            page.insert_text(
                pymupdf.Point(max(bounds.x0 + 2, x0), min(bounds.y1 - 2, y0 + font_size)),
                label, fontname="helv", fontsize=font_size, color=rgb,
            )
        stamped.append(label)
        counter += 1

    return {"stamped": len(stamped), "first": stamped[0] if stamped else None,
            "last": stamped[-1] if stamped else None}


def set_password(data: bytes, user_pw: str = "", owner_pw: str = "") -> bytes:
    """Return an encrypted copy of the document."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        perm = int(
            getattr(pymupdf, "PDF_PERM_ACCESSIBILITY", 0)
            | getattr(pymupdf, "PDF_PERM_PRINT", 0)
            | getattr(pymupdf, "PDF_PERM_COPY", 0)
        )
        return doc.tobytes(
            encryption=getattr(pymupdf, "PDF_ENCRYPT_AES_256", 6),
            owner_pw=owner_pw or user_pw,
            user_pw=user_pw,
            permissions=perm,
            garbage=3,
            deflate=True,
        )
    finally:
        doc.close()


def remove_password(data: bytes, password: str = "") -> bytes:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        if doc.is_encrypted and not doc.authenticate(password):
            raise ValueError("Incorrect password")
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
