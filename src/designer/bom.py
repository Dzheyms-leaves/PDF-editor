"""Bill of materials and quoting for a designed panel job.

Turns the panels on screen into the two documents the commercial side needs: a
bill of materials that groups identical configurations, and a priced quote.

Rates come from a local price book kept in settings and can be overridden per
request. Nothing here invents a price: a part with no rate is carried at zero
and flagged, so an unpriced line is visible on the quote rather than quietly
costing nothing.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..pdfcompat import Point, Rect, pymupdf
from . import catalogue

A4 = (595.0, 842.0)
MARGIN = 48.0

FONT = "helv"
FONT_BOLD = "hebo"
FONT_MONO = "cobo"

INK = (0.11, 0.11, 0.10)
MUTED = (0.42, 0.41, 0.38)
RULE = (0.80, 0.79, 0.76)
ACCENT = (0.62, 0.44, 0.18)

ENGRAVING_CODE = "ENGRAVING"


@dataclass
class Line:
    part_code: str
    description: str
    quantity: float
    unit: str = "ea"
    rate: float = 0.0
    priced: bool = True
    panels: List[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(self.quantity * self.rate, 2)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "part_code": self.part_code, "description": self.description,
            "quantity": self.quantity, "unit": self.unit, "rate": self.rate,
            "total": self.total, "priced": self.priced, "panels": self.panels,
        }


@dataclass
class Bom:
    lines: List[Line]
    currency: str = "AUD"
    tax_rate: float = 10.0
    tax_label: str = "GST"

    @property
    def subtotal(self) -> float:
        return round(sum(line.total for line in self.lines), 2)

    @property
    def tax(self) -> float:
        return round(self.subtotal * self.tax_rate / 100.0, 2)

    @property
    def total(self) -> float:
        return round(self.subtotal + self.tax, 2)

    @property
    def unpriced(self) -> List[str]:
        return [line.part_code for line in self.lines if not line.priced]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "lines": [line.as_dict() for line in self.lines],
            "currency": self.currency,
            "tax_rate": self.tax_rate,
            "tax_label": self.tax_label,
            "subtotal": self.subtotal,
            "tax": self.tax,
            "total": self.total,
            "unpriced": self.unpriced,
        }


def describe_panel(design: Any) -> str:
    """A one-line description of a configuration, for an order line."""
    fam = catalogue.family(design.family)
    reg = catalogue.region(design.region)
    button = catalogue.button_finish(design.button_finish)
    rim = catalogue.rim_finish(design.rim_finish)

    head = catalogue.product_name(design.family, design.series)
    if fam.counts:
        head += f" {design.buttons}-button"
    return f"{head} {reg.short or reg.name} - {button.name} / {rim.name}"


def _rate_for(code: str, price_book: Dict[str, Any],
              overrides: Dict[str, Any]) -> Tuple[float, bool]:
    """Look up a rate; the second value says whether one was actually found."""
    for source in (overrides, price_book):
        if code in source:
            try:
                return round(float(source[code]), 2), True
            except (TypeError, ValueError):
                continue
    return 0.0, False


def build(designs: Sequence[Any], *, price_book: Optional[Dict[str, Any]] = None,
          overrides: Optional[Dict[str, Any]] = None,
          extras: Sequence[Any] = (), include_engraving: bool = True,
          currency: str = "AUD", tax_rate: float = 10.0,
          tax_label: str = "GST") -> Bom:
    """Group a job's panels into priced order lines."""
    book = price_book or {}
    over = overrides or {}

    grouped: Dict[str, Line] = {}
    engraved_panels = 0

    for design in designs:
        try:
            catalogue.validate(design.family, design.series, design.region,
                               design.buttons, design.button_finish,
                               design.rim_finish)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"{design.name or 'A panel'}: {exc}") from exc

        code = catalogue.part_code(design.family, design.series, design.region,
                                   design.buttons, design.button_finish,
                                   design.rim_finish)
        quantity = max(1, int(design.quantity or 1))

        line = grouped.get(code)
        if line is None:
            rate, priced = _rate_for(code, book, over)
            line = Line(part_code=code, description=describe_panel(design),
                        quantity=0, rate=rate, priced=priced)
            grouped[code] = line
        line.quantity += quantity
        if design.name:
            line.panels.append(design.name)

        if any((e.lines and any(t.strip() for t in e.lines)) or e.icon
               for e in design.engraving):
            engraved_panels += quantity

    lines = list(grouped.values())

    if include_engraving and engraved_panels:
        rate, priced = _rate_for(ENGRAVING_CODE, book, over)
        lines.append(Line(part_code=ENGRAVING_CODE,
                          description="Custom engraving, per panel",
                          quantity=engraved_panels, rate=rate, priced=priced))

    for extra in extras:
        quantity = float(getattr(extra, "quantity", 0) or 0)
        if not quantity:
            continue
        lines.append(Line(
            part_code=getattr(extra, "part_code", "") or "",
            description=getattr(extra, "description", "") or "Additional item",
            quantity=quantity,
            unit=getattr(extra, "unit", "ea") or "ea",
            rate=round(float(getattr(extra, "rate", 0) or 0), 2),
        ))

    if not lines:
        raise ValueError("There is nothing to quote")
    return Bom(lines=lines, currency=currency, tax_rate=tax_rate, tax_label=tax_label)


# ------------------------------------------------------------- PDF quote

def _width(text: str, size: float, font: str = FONT) -> float:
    return pymupdf.get_text_length(text, fontname=font, fontsize=size)


def _fit(text: str, width: float, size: float, font: str = FONT) -> str:
    if _width(text, size, font) <= width:
        return text
    trimmed = text
    while trimmed and _width(f"{trimmed}...", size, font) > width:
        trimmed = trimmed[:-1]
    return f"{trimmed}..." if trimmed else ""


def _put(page: Any, x: float, y: float, text: str, *, size: float = 9.0,
         font: str = FONT, color: Sequence[float] = INK, align: str = "left",
         width: float = 0.0) -> None:
    if not text:
        return
    if align == "right":
        x = x + width - _width(text, size, font)
    elif align == "centre":
        x = x + (width - _width(text, size, font)) / 2
    page.insert_text(Point(x, y), text, fontname=font, fontsize=size, color=color)


def _money(value: float, currency: str) -> str:
    return f"{currency} {value:,.2f}"


def quote_pdf(bom: Bom, *, job_name: str = "", project: str = "", client: str = "",
              reference: str = "", company: str = "", terms: str = "",
              valid_days: int = 30) -> bytes:
    """Render the bill of materials as an issued quotation."""
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=A4[0], height=A4[1])
        right = A4[0] - MARGIN
        width = right - MARGIN

        _put(page, MARGIN, MARGIN + 14, company or "Quotation", size=15, font=FONT_BOLD)
        _put(page, MARGIN, MARGIN + 30, "QUOTATION", size=9, color=ACCENT)

        today = _dt.date.today()
        meta = [("Date", today.isoformat()),
                ("Valid until", (today + _dt.timedelta(days=valid_days)).isoformat())]
        if reference:
            meta.insert(0, ("Reference", reference))
        cursor = MARGIN + 14
        for label, value in meta:
            _put(page, right - 190, cursor, label, size=8, color=MUTED)
            _put(page, right - 110, cursor, value, size=9, font=FONT_BOLD,
                 align="right", width=110)
            cursor += 13

        top = MARGIN + 48
        shape = page.new_shape()
        shape.draw_line(Point(MARGIN, top), Point(right, top))
        shape.finish(color=ACCENT, width=1.4)
        shape.commit()

        cursor = top + 20
        for label, value in (("Client", client), ("Project", project),
                             ("Job", job_name)):
            if not value:
                continue
            _put(page, MARGIN, cursor, label.upper(), size=8, color=MUTED)
            _put(page, MARGIN + 62, cursor, value, size=10, font=FONT_BOLD)
            cursor += 15

        # ---- line table -------------------------------------------------
        cursor += 16
        columns = (92.0, width - 92.0 - 44 - 70 - 78, 44.0, 70.0, 78.0)
        headers = ("PART CODE", "DESCRIPTION", "QTY", "RATE", "AMOUNT")
        aligns = ("left", "left", "right", "right", "right")

        x = MARGIN
        for header, span, align in zip(headers, columns, aligns):
            _put(page, x, cursor, header, size=7, color=MUTED, align=align, width=span)
            x += span
        cursor += 7
        shape = page.new_shape()
        shape.draw_line(Point(MARGIN, cursor), Point(right, cursor))
        shape.finish(color=RULE, width=0.7)
        shape.commit()
        cursor += 15

        for line in bom.lines:
            values = (
                line.part_code or "-",
                _fit(line.description, columns[1] - 8, 8.2),
                f"{line.quantity:g} {line.unit}".strip(),
                _money(line.rate, "") if line.priced else "not priced",
                _money(line.total, ""),
            )
            fonts = (FONT_MONO, FONT, FONT, FONT, FONT_BOLD)
            x = MARGIN
            for value, span, align, font in zip(values, columns, aligns, fonts):
                colour = MUTED if (value == "not priced") else INK
                _put(page, x, cursor, value, size=8.2, font=font, color=colour,
                     align=align, width=span)
                x += span
            cursor += 15
            shape = page.new_shape()
            shape.draw_line(Point(MARGIN, cursor - 4), Point(right, cursor - 4))
            shape.finish(color=(0.90, 0.89, 0.87), width=0.35)
            shape.commit()

        # ---- totals -----------------------------------------------------
        cursor += 10
        label_x = right - 240
        for label, value, bold in (
            ("Subtotal", bom.subtotal, False),
            (f"{bom.tax_label} @ {bom.tax_rate:g}%", bom.tax, False),
            ("Total", bom.total, True),
        ):
            if bold:
                shape = page.new_shape()
                shape.draw_line(Point(label_x, cursor - 11), Point(right, cursor - 11))
                shape.finish(color=RULE, width=0.7)
                shape.commit()
            _put(page, label_x, cursor, label, size=9 if not bold else 10,
                 font=FONT if not bold else FONT_BOLD)
            _put(page, right - 130, cursor, _money(value, bom.currency),
                 size=9 if not bold else 11, font=FONT_BOLD, align="right", width=130)
            cursor += 17

        if bom.unpriced:
            cursor += 10
            _put(page, MARGIN, cursor,
                 f"Not priced: {', '.join(bom.unpriced)} - add a rate before issuing.",
                 size=8, color=ACCENT)
            cursor += 14

        if terms:
            cursor += 12
            page.insert_textbox(Rect(MARGIN, cursor, right, cursor + 150), terms,
                                fontname=FONT, fontsize=8, color=MUTED, lineheight=1.5)

        _put(page, MARGIN, A4[1] - MARGIN,
             "Prices exclude delivery unless stated. Panel finishes and engraving "
             "are made to order and cannot be returned.", size=7, color=MUTED)

        doc.set_metadata({"title": f"Quotation - {job_name or project or 'panel job'}",
                          "creator": "PDF Workbench - Antumbra designer"})
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
