"""Text and content editing.

PDFs have no notion of an editable text box: glyphs are painted at absolute
positions. Editing text in place therefore means *removing* the old glyphs with
a redaction and *repainting* replacement text with matched font, size and
colour. These helpers make that round-trip as faithful as PyMuPDF allows.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from ..models import PageSelection, Rect, TextSpanInfo
from ..pdfcompat import pymupdf
from .common import (
    clamp_rect_to_page,
    fit_font_size,
    from_fitz_rect,
    hex_to_rgb,
    resolve_font,
    resolve_pages,
    sample_background,
    srgb_int_to_hex,
    to_fitz_rect,
)

# Redaction constants moved around between PyMuPDF releases.
_REDACT_IMAGE_NONE = getattr(pymupdf, "PDF_REDACT_IMAGE_NONE", 0)
_REDACT_IMAGE_PIXELS = getattr(pymupdf, "PDF_REDACT_IMAGE_PIXELS", 2)
_REDACT_LINE_ART_NONE = getattr(pymupdf, "PDF_REDACT_LINE_ART_NONE", 0)


def _span_flags(flags: int) -> Tuple[bool, bool]:
    """PyMuPDF span flags: bit 1 = italic, bit 4 = bold (serif is bit 2)."""
    return bool(flags & 16), bool(flags & 2)


def spans_in_rect(page: "pymupdf.Page", rect: "pymupdf.Rect") -> List[Dict]:
    """Every text span whose box meaningfully overlaps ``rect``."""
    found: List[Dict] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                sbox = pymupdf.Rect(span["bbox"])
                if sbox.is_empty:
                    continue
                overlap = sbox & rect
                if overlap.is_empty:
                    continue
                area = sbox.width * sbox.height
                if area <= 0:
                    continue
                if (overlap.width * overlap.height) / area >= 0.35:
                    found.append(span)
    return found


def describe_spans(page: "pymupdf.Page", page_no: int, rect: Optional[Rect] = None) -> List[TextSpanInfo]:
    """Report the text spans on a page (optionally limited to a rect)."""
    target = to_fitz_rect(rect) if rect else page.rect
    out: List[TextSpanInfo] = []
    for span in spans_in_rect(page, target):
        bold, italic = _span_flags(int(span.get("flags", 0)))
        out.append(
            TextSpanInfo(
                page=page_no,
                rect=from_fitz_rect(pymupdf.Rect(span["bbox"])),
                text=span.get("text", ""),
                font=span.get("font", ""),
                size=round(float(span.get("size", 0)), 2),
                colour=srgb_int_to_hex(span.get("color")),
                flags=int(span.get("flags", 0)),
                bold=bold,
                italic=italic,
            )
        )
    return out


def _style_from_spans(spans: Sequence[Dict]) -> Dict:
    """Pick the dominant style of a group of spans, weighted by text length."""
    if not spans:
        return {"font": "helv", "size": 11.0, "colour": "#000000", "bold": False, "italic": False}
    best = max(spans, key=lambda s: len(s.get("text", "")))
    bold, italic = _span_flags(int(best.get("flags", 0)))
    return {
        "font": best.get("font", ""),
        "size": float(best.get("size", 11.0)) or 11.0,
        "colour": srgb_int_to_hex(best.get("color")),
        "bold": bold,
        "italic": italic,
    }


def _erase(page: "pymupdf.Page", box: "pymupdf.Rect", fill: Tuple[float, float, float]) -> None:
    """Remove all content inside ``box`` and repaint it with ``fill``."""
    page.add_redact_annot(box, fill=fill)
    page.apply_redactions(images=_REDACT_IMAGE_NONE, graphics=_REDACT_LINE_ART_NONE)


def edit_text(
    doc: "pymupdf.Document",
    page_no: int,
    rect: Rect,
    new_text: str,
    font: Optional[str] = None,
    size: Optional[float] = None,
    colour: Optional[str] = None,
    align: str = "left",
    background: Optional[str] = None,
) -> Dict:
    """Replace the text inside ``rect`` with ``new_text``, matching its style."""
    if not 1 <= page_no <= doc.page_count:
        raise ValueError(f"Page {page_no} is out of range")

    page = doc[page_no - 1]
    box = clamp_rect_to_page(to_fitz_rect(rect), page)
    if box.is_empty or box.width < 1 or box.height < 1:
        raise ValueError("The selected area is too small to edit")

    spans = spans_in_rect(page, box)
    style = _style_from_spans(spans)
    original = "".join(s.get("text", "") for s in spans)

    fontname = resolve_font(font or style["font"], style["bold"], style["italic"])
    fontsize = float(size or style["size"])
    text_colour = hex_to_rgb(colour or style["colour"])
    bg = hex_to_rgb(background) if background else sample_background(page, box)

    # Give the replacement a little vertical slack so descenders are not clipped.
    write_box = pymupdf.Rect(box.x0, box.y0 - 1, box.x1 + 2, box.y1 + 2) & page.rect

    _erase(page, box, bg)

    align_map = {
        "left": pymupdf.TEXT_ALIGN_LEFT,
        "center": pymupdf.TEXT_ALIGN_CENTER,
        "right": pymupdf.TEXT_ALIGN_RIGHT,
    }
    fitted = fit_font_size(new_text, write_box, fontsize, fontname)

    leftover = -1.0
    for attempt in range(6):
        trial = fitted * (0.9 ** attempt)
        leftover = page.insert_textbox(
            write_box,
            new_text,
            fontname=fontname,
            fontsize=trial,
            color=text_colour,
            align=align_map.get(align, pymupdf.TEXT_ALIGN_LEFT),
        )
        if leftover >= 0:
            fitted = trial
            break

    if leftover < 0:
        # Still overflowing: fall back to a single unwrapped baseline draw so
        # the user never silently loses their text.
        page.insert_text(
            pymupdf.Point(box.x0, box.y1 - max(1.0, fontsize * 0.2)),
            new_text,
            fontname=fontname,
            fontsize=max(4.0, fitted * 0.8),
            color=text_colour,
        )

    return {
        "replaced": original,
        "font": fontname,
        "size": round(fitted, 2),
        "colour": colour or style["colour"],
        "overflowed": leftover < 0,
    }


def delete_content(
    doc: "pymupdf.Document", page_no: int, rect: Rect, background: Optional[str] = None
) -> Dict:
    """Erase everything inside ``rect`` (text, drawings and images)."""
    if not 1 <= page_no <= doc.page_count:
        raise ValueError(f"Page {page_no} is out of range")
    page = doc[page_no - 1]
    box = clamp_rect_to_page(to_fitz_rect(rect), page)
    if box.is_empty:
        raise ValueError("Nothing selected to delete")
    removed = "".join(s.get("text", "") for s in spans_in_rect(page, box))
    bg = hex_to_rgb(background) if background else sample_background(page, box)
    page.add_redact_annot(box, fill=bg)
    page.apply_redactions(images=_REDACT_IMAGE_PIXELS)
    return {"removed_text": removed}


def move_content(
    doc: "pymupdf.Document",
    page_no: int,
    rect: Rect,
    dx: float,
    dy: float,
    background: Optional[str] = None,
) -> Dict:
    """Cut the pixels inside ``rect`` and paste them offset by ``(dx, dy)``.

    Rasterises the region, which is lossless enough for logos and stamps but
    will soften vector text — the UI warns about this before committing.
    """
    if not 1 <= page_no <= doc.page_count:
        raise ValueError(f"Page {page_no} is out of range")
    page = doc[page_no - 1]
    box = clamp_rect_to_page(to_fitz_rect(rect), page)
    if box.is_empty:
        raise ValueError("Nothing selected to move")

    pix = page.get_pixmap(clip=box, dpi=300, alpha=False)
    bg = hex_to_rgb(background) if background else sample_background(page, box)
    page.add_redact_annot(box, fill=bg)
    page.apply_redactions(images=_REDACT_IMAGE_PIXELS)

    target = pymupdf.Rect(box.x0 + dx, box.y0 + dy, box.x1 + dx, box.y1 + dy)
    target = clamp_rect_to_page(target, page)
    if target.is_empty:
        raise ValueError("The destination falls outside the page")
    page.insert_image(target, pixmap=pix, overlay=True)
    return {"moved_to": from_fitz_rect(target).model_dump()}


def add_image(
    doc: "pymupdf.Document",
    page_no: int,
    rect: Rect,
    image_bytes: bytes,
    opacity: float = 1.0,
    keep_aspect: bool = True,
) -> Dict:
    if not 1 <= page_no <= doc.page_count:
        raise ValueError(f"Page {page_no} is out of range")
    page = doc[page_no - 1]
    box = clamp_rect_to_page(to_fitz_rect(rect), page)
    if box.is_empty:
        raise ValueError("Image placement area is empty")
    page.insert_image(
        box,
        stream=image_bytes,
        keep_proportion=keep_aspect,
        overlay=True,
        alpha=-1,
    )
    return {"rect": from_fitz_rect(box).model_dump()}


def list_images(doc: "pymupdf.Document", page_no: int) -> List[Dict]:
    if not 1 <= page_no <= doc.page_count:
        raise ValueError(f"Page {page_no} is out of range")
    page = doc[page_no - 1]
    out: List[Dict] = []
    for info in page.get_images(full=True):
        xref = info[0]
        rects = page.get_image_rects(xref)
        for rect in rects:
            out.append(
                {
                    "xref": xref,
                    "width": info[2],
                    "height": info[3],
                    "rect": from_fitz_rect(rect).model_dump(),
                }
            )
    return out


def replace_image(
    doc: "pymupdf.Document",
    page_no: int,
    image_bytes: bytes,
    xref: Optional[int] = None,
    rect: Optional[Rect] = None,
) -> Dict:
    """Swap an existing image for a new one, keeping its footprint."""
    if not 1 <= page_no <= doc.page_count:
        raise ValueError(f"Page {page_no} is out of range")
    page = doc[page_no - 1]

    target_rect: Optional["pymupdf.Rect"] = None
    if xref is not None:
        rects = page.get_image_rects(xref)
        if rects:
            target_rect = rects[0]
    if target_rect is None and rect is not None:
        target_rect = to_fitz_rect(rect)
    if target_rect is None:
        raise ValueError("Could not locate the image to replace")

    bg = sample_background(page, target_rect)
    page.add_redact_annot(target_rect, fill=bg)
    page.apply_redactions(images=_REDACT_IMAGE_PIXELS)
    page.insert_image(target_rect, stream=image_bytes, keep_proportion=True, overlay=True)
    return {"rect": from_fitz_rect(target_rect).model_dump()}


def find_text(
    doc: "pymupdf.Document",
    needle: str,
    selection: PageSelection,
    match_case: bool = False,
    whole_word: bool = False,
    limit: int = 500,
) -> List[Dict]:
    """Locate every occurrence of ``needle``. Returns hit rects per page."""
    if not needle:
        return []
    hits: List[Dict] = []
    flags = 0
    if not match_case:
        flags |= getattr(pymupdf, "TEXT_IGNORECASE", 8)
    if whole_word:
        flags |= getattr(pymupdf, "TEXT_WHOLE_WORDS", 0)

    for page_no in resolve_pages(selection, doc.page_count):
        page = doc[page_no - 1]
        try:
            found = page.search_for(needle, flags=flags)
        except TypeError:  # older signature without flags
            found = page.search_for(needle)
        for box in found:
            hits.append({"page": page_no, "rect": from_fitz_rect(box).model_dump()})
            if len(hits) >= limit:
                return hits
    return hits


def find_and_replace(
    doc: "pymupdf.Document",
    find: str,
    replace: str,
    selection: PageSelection,
    match_case: bool = False,
    whole_word: bool = False,
    limit: int = 500,
) -> Dict:
    """Replace every hit, matching each occurrence's own font and colour."""
    if not find:
        raise ValueError("Nothing to find")

    replaced = 0
    pages_touched: List[int] = []
    flags = 0
    if not match_case:
        flags |= getattr(pymupdf, "TEXT_IGNORECASE", 8)
    if whole_word:
        flags |= getattr(pymupdf, "TEXT_WHOLE_WORDS", 0)

    for page_no in resolve_pages(selection, doc.page_count):
        page = doc[page_no - 1]
        try:
            boxes = page.search_for(find, flags=flags)
        except TypeError:
            boxes = page.search_for(find)
        if not boxes:
            continue

        # Capture styles before any redaction disturbs the page.
        jobs = []
        for box in boxes[: max(0, limit - replaced)]:
            style = _style_from_spans(spans_in_rect(page, box))
            jobs.append((box, style))
        if not jobs:
            continue

        for box, style in jobs:
            bg = sample_background(page, box)
            page.add_redact_annot(box, fill=bg)
        page.apply_redactions(images=_REDACT_IMAGE_NONE, graphics=_REDACT_LINE_ART_NONE)

        for box, style in jobs:
            if not replace:
                replaced += 1
                continue
            fontname = resolve_font(style["font"], style["bold"], style["italic"])
            size = float(style["size"])
            write_box = pymupdf.Rect(box.x0, box.y0 - 1, box.x1 + 40, box.y1 + 2) & page.rect
            size = fit_font_size(replace, write_box, size, fontname)
            page.insert_text(
                pymupdf.Point(box.x0, box.y1 - max(0.8, size * 0.18)),
                replace,
                fontname=fontname,
                fontsize=size,
                color=hex_to_rgb(style["colour"]),
            )
            replaced += 1

        pages_touched.append(page_no)
        if replaced >= limit:
            break

    return {"replaced": replaced, "pages": pages_touched}


def extract_page_text(doc: "pymupdf.Document", page_no: int, mode: str = "text") -> str:
    if not 1 <= page_no <= doc.page_count:
        raise ValueError(f"Page {page_no} is out of range")
    return doc[page_no - 1].get_text(mode)
