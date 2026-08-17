"""AcroForm field reading/filling, flattening and signature placement."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..models import FormField, Rect
from ..pdfcompat import pymupdf
from .common import clamp_rect_to_page, from_fitz_rect, hex_to_rgb, to_fitz_rect

_FIELD_TYPES = {
    getattr(pymupdf, "PDF_WIDGET_TYPE_UNKNOWN", -1): "unknown",
    getattr(pymupdf, "PDF_WIDGET_TYPE_BUTTON", 1): "button",
    getattr(pymupdf, "PDF_WIDGET_TYPE_CHECKBOX", 2): "checkbox",
    getattr(pymupdf, "PDF_WIDGET_TYPE_COMBOBOX", 3): "combobox",
    getattr(pymupdf, "PDF_WIDGET_TYPE_LISTBOX", 4): "listbox",
    getattr(pymupdf, "PDF_WIDGET_TYPE_RADIOBUTTON", 5): "radio",
    getattr(pymupdf, "PDF_WIDGET_TYPE_SIGNATURE", 6): "signature",
    getattr(pymupdf, "PDF_WIDGET_TYPE_TEXT", 7): "text",
}


def _type_name(widget) -> str:
    return _FIELD_TYPES.get(widget.field_type, str(widget.field_type_string or "unknown"))


def list_fields(doc: "pymupdf.Document") -> List[FormField]:
    fields: List[FormField] = []
    for pno in range(doc.page_count):
        page = doc[pno]
        for widget in page.widgets():
            flags = int(widget.field_flags or 0)
            fields.append(
                FormField(
                    page=pno + 1,
                    name=widget.field_name or "",
                    field_type=_type_name(widget),
                    value=widget.field_value,
                    options=list(widget.choice_values or []),
                    rect=from_fitz_rect(widget.rect),
                    read_only=bool(flags & 1),
                    required=bool(flags & 2),
                    max_len=widget.text_maxlen or None,
                )
            )
    return fields


def _coerce(widget, value: Any) -> Any:
    kind = _type_name(widget)
    if kind in {"checkbox", "radio"}:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "x", "checked"}
    if value is None:
        return ""
    return str(value)


def fill_fields(doc: "pymupdf.Document", values: Dict[str, Any]) -> Dict[str, Any]:
    """Set field values by name. Unknown names are reported back, not fatal."""
    applied: List[str] = []
    skipped: List[str] = []
    remaining = dict(values)

    for pno in range(doc.page_count):
        page = doc[pno]
        for widget in page.widgets():
            name = widget.field_name
            if name is None or name not in remaining:
                continue
            raw = remaining[name]
            if int(widget.field_flags or 0) & 1:
                skipped.append(name)
                continue
            try:
                widget.field_value = _coerce(widget, raw)
                widget.update()
                applied.append(name)
            except Exception:  # noqa: BLE001 - a bad value shouldn't kill the batch
                skipped.append(name)

    unknown = [k for k in values if k not in applied and k not in skipped]
    return {"applied": applied, "skipped": skipped, "unknown": unknown}


def flatten_form(doc: "pymupdf.Document") -> int:
    """Bake field appearances into the page and remove the widgets.

    After this the values are permanent page content — no longer editable, and
    no longer stripped by readers that ignore AcroForms.
    """
    flattened = 0
    for pno in range(doc.page_count):
        page = doc[pno]
        widgets = list(page.widgets())
        if not widgets:
            continue
        for widget in widgets:
            rect = widget.rect
            if rect.is_empty or rect.width <= 0 or rect.height <= 0:
                continue
            try:
                pix = widget._annot.get_pixmap(dpi=200, alpha=True)
                page.insert_image(rect, pixmap=pix, overlay=True)
                flattened += 1
            except Exception:  # noqa: BLE001
                value = widget.field_value
                if value not in (None, "", False):
                    page.insert_textbox(
                        rect,
                        str(value),
                        fontname="helv",
                        fontsize=max(6.0, min(11.0, rect.height * 0.7)),
                        color=(0, 0, 0),
                    )
                    flattened += 1

        for annot in list(page.annots()):
            if annot.type[0] == getattr(pymupdf, "PDF_ANNOT_WIDGET", 19):
                page.delete_annot(annot)

    # Drop the AcroForm dictionary so readers stop advertising a form.
    try:
        root = doc.pdf_catalog()
        doc.xref_set_key(root, "AcroForm", "null")
    except Exception:  # noqa: BLE001
        pass
    return flattened


def place_signature(
    doc: "pymupdf.Document",
    page_no: int,
    rect: Rect,
    image_bytes: Optional[bytes] = None,
    strokes: Optional[Sequence[Sequence[Sequence[float]]]] = None,
    colour: str = "#101010",
    stroke_width: float = 1.8,
    flatten: bool = True,
) -> Dict[str, Any]:
    """Place a signature as an image or as drawn vector strokes.

    ``strokes`` are polylines in PDF page coordinates. With ``flatten`` they are
    painted straight into the page content; otherwise they become an ink annot
    the signer can still move or delete.
    """
    if not 1 <= page_no <= doc.page_count:
        raise ValueError(f"Page {page_no} is out of range")

    page = doc[page_no - 1]
    box = clamp_rect_to_page(to_fitz_rect(rect), page)
    if box.is_empty or box.width < 2 or box.height < 2:
        raise ValueError("Signature area is too small")

    if image_bytes:
        page.insert_image(box, stream=image_bytes, keep_proportion=True, overlay=True)
        return {"kind": "image", "rect": from_fitz_rect(box).model_dump()}

    polylines = [
        [(float(px), float(py)) for px, py in stroke]
        for stroke in (strokes or [])
        if len(stroke) >= 2
    ]
    if not polylines:
        raise ValueError("A signature needs either an image or drawn strokes")

    ink_colour = hex_to_rgb(colour, (0.06, 0.06, 0.06))
    if flatten:
        shape = page.new_shape()
        for line in polylines:
            for start, end in zip(line, line[1:]):
                shape.draw_line(pymupdf.Point(*start), pymupdf.Point(*end))
        shape.finish(color=ink_colour, width=max(0.3, stroke_width), closePath=False)
        shape.commit()
        return {"kind": "vector", "strokes": len(polylines)}

    annot = page.add_ink_annot([[(float(x), float(y)) for x, y in line] for line in polylines])
    annot.set_colors(stroke=ink_colour)
    annot.set_border(width=max(0.3, stroke_width))
    annot.update()
    return {"kind": "ink_annot", "strokes": len(polylines)}
