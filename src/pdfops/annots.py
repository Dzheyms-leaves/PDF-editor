"""Annotation and markup layer.

Everything here writes real PDF annotations, so markup stays selectable and
editable in Acrobat, Preview and every other reader — not flattened pixels.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from ..models import AnnotationRef, AnnotationSpec, Rect
from ..pdfcompat import pymupdf
from .common import (
    clamp_rect_to_page,
    fit_font_size,
    from_fitz_rect,
    hex_to_rgb,
    resolve_font,
    rgb_to_hex,
    to_fitz_rect,
)

_TEXT_MARKUP = {"highlight", "underline", "strikeout", "squiggly"}


def _quads_for(spec: AnnotationSpec) -> List["pymupdf.Quad"]:
    quads = [to_fitz_rect(q).quad for q in spec.quads]
    if not quads and spec.rect:
        quads = [to_fitz_rect(spec.rect).quad]
    return quads


def add_annotation(doc: "pymupdf.Document", spec: AnnotationSpec) -> AnnotationRef:
    if not 1 <= spec.page <= doc.page_count:
        raise ValueError(f"Page {spec.page} is out of range")

    page = doc[spec.page - 1]
    colour = hex_to_rgb(spec.colour, (1.0, 0.83, 0.0))
    annot = None

    if spec.kind in _TEXT_MARKUP:
        quads = _quads_for(spec)
        if not quads:
            raise ValueError(f"'{spec.kind}' needs a rect or quads to mark up")
        adder = {
            "highlight": page.add_highlight_annot,
            "underline": page.add_underline_annot,
            "strikeout": page.add_strikeout_annot,
            "squiggly": page.add_squiggly_annot,
        }[spec.kind]
        annot = adder(quads)
        annot.set_colors(stroke=colour)

    elif spec.kind == "note":
        if not spec.rect:
            raise ValueError("A sticky note needs a position")
        point = pymupdf.Point(spec.rect.x0, spec.rect.y0)
        annot = page.add_text_annot(point, spec.text or "", icon=spec.icon or "Note")
        annot.set_colors(stroke=colour)

    elif spec.kind == "ink":
        # PyMuPDF wants plain float pairs here; Point objects are rejected.
        strokes = [
            [(float(x), float(y)) for x, y in stroke]
            for stroke in spec.points
            if len(stroke) >= 2
        ]
        if not strokes:
            raise ValueError("Freehand ink needs at least one stroke of two or more points")
        annot = page.add_ink_annot(strokes)
        annot.set_colors(stroke=colour)
        annot.set_border(width=max(0.2, spec.stroke_width))

    elif spec.kind in {"rect", "circle"}:
        if not spec.rect:
            raise ValueError(f"'{spec.kind}' needs a rect")
        box = clamp_rect_to_page(to_fitz_rect(spec.rect), page)
        annot = (page.add_rect_annot if spec.kind == "rect" else page.add_circle_annot)(box)
        fill = hex_to_rgb(spec.fill) if spec.fill else None
        annot.set_colors(stroke=colour, fill=fill)
        annot.set_border(width=max(0.2, spec.stroke_width))

    elif spec.kind in {"line", "arrow"}:
        if not spec.rect:
            raise ValueError(f"'{spec.kind}' needs start and end points in its rect")
        start = pymupdf.Point(spec.rect.x0, spec.rect.y0)
        end = pymupdf.Point(spec.rect.x1, spec.rect.y1)
        annot = page.add_line_annot(start, end)
        annot.set_colors(stroke=colour)
        annot.set_border(width=max(0.2, spec.stroke_width))
        if spec.kind == "arrow":
            annot.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_CLOSED_ARROW)

    elif spec.kind in {"freetext", "stamp_text"}:
        if not spec.rect:
            raise ValueError("A text box needs a rect")
        box = clamp_rect_to_page(to_fitz_rect(spec.rect), page)
        font = resolve_font(spec.font)
        size = fit_font_size(spec.text or "", box, spec.font_size, font)
        annot = page.add_freetext_annot(
            box,
            spec.text or "",
            fontsize=size,
            fontname=font,
            text_color=colour,
            fill_color=hex_to_rgb(spec.fill) if spec.fill else None,
            border_width=spec.stroke_width if spec.fill else 0,
        )
    else:
        raise ValueError(f"Unsupported annotation kind '{spec.kind}'")

    if annot is None:
        raise ValueError(f"Could not create a '{spec.kind}' annotation")

    if spec.opacity < 1.0:
        annot.set_opacity(max(0.0, min(1.0, spec.opacity)))
    info = annot.info
    if spec.author:
        info["title"] = spec.author
    if spec.text and spec.kind not in {"freetext", "stamp_text", "note"}:
        info["content"] = spec.text
    annot.set_info(info)
    annot.update()

    index = _index_of(page, annot)
    return AnnotationRef(
        page=spec.page,
        index=index,
        kind=spec.kind,
        rect=from_fitz_rect(annot.rect),
        text=spec.text,
        author=spec.author,
        colour=spec.colour,
    )


def _index_of(page: "pymupdf.Page", target) -> int:
    for idx, annot in enumerate(page.annots()):
        if annot.xref == target.xref:
            return idx
    return -1


def list_annotations(doc: "pymupdf.Document", page_no: Optional[int] = None) -> List[AnnotationRef]:
    out: List[AnnotationRef] = []
    pages = [page_no] if page_no else range(1, doc.page_count + 1)
    for pno in pages:
        if not 1 <= pno <= doc.page_count:
            continue
        page = doc[pno - 1]
        for idx, annot in enumerate(page.annots()):
            info = annot.info
            colours = annot.colors or {}
            out.append(
                AnnotationRef(
                    page=pno,
                    index=idx,
                    kind=annot.type[1] if annot.type else "unknown",
                    rect=from_fitz_rect(annot.rect),
                    text=info.get("content") or None,
                    author=info.get("title") or None,
                    colour=rgb_to_hex(colours.get("stroke")),
                )
            )
    return out


def delete_annotations(doc: "pymupdf.Document", page_no: int, indices: Sequence[int]) -> int:
    if not 1 <= page_no <= doc.page_count:
        raise ValueError(f"Page {page_no} is out of range")
    page = doc[page_no - 1]
    annots = list(page.annots())
    removed = 0
    # Delete high indices first so the remaining positions stay valid.
    for idx in sorted({int(i) for i in indices}, reverse=True):
        if 0 <= idx < len(annots):
            page.delete_annot(annots[idx])
            removed += 1
    return removed


def clear_annotations(doc: "pymupdf.Document", pages: Optional[Sequence[int]] = None) -> int:
    targets = pages or range(1, doc.page_count + 1)
    removed = 0
    for pno in targets:
        if not 1 <= pno <= doc.page_count:
            continue
        page = doc[pno - 1]
        for annot in list(page.annots()):
            page.delete_annot(annot)
            removed += 1
    return removed


def flatten_annotations(doc: "pymupdf.Document", pages: Optional[Sequence[int]] = None) -> int:
    """Bake annotation appearances into the page content and drop the annots.

    Done by rendering each annotation's own appearance stream into the page, so
    the visual result matches what a reader would have drawn.
    """
    targets = list(pages) if pages else list(range(1, doc.page_count + 1))
    flattened = 0
    for pno in targets:
        if not 1 <= pno <= doc.page_count:
            continue
        page = doc[pno - 1]
        annots = list(page.annots())
        if not annots:
            continue
        for annot in annots:
            rect = annot.rect
            if rect.is_empty or rect.width <= 0 or rect.height <= 0:
                page.delete_annot(annot)
                continue
            try:
                pix = annot.get_pixmap(dpi=200, alpha=True)
                page.insert_image(rect, pixmap=pix, overlay=True)
                flattened += 1
            except Exception:  # noqa: BLE001 - drop annots we cannot rasterise
                pass
            page.delete_annot(annot)
    return flattened
