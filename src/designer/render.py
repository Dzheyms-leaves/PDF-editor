"""Spec-sheet rendering for Antumbra panel designs.

Produces the document the workshop actually needs: a 1:1 front elevation the
laser operator can lay the part against, the finish and product-code block, and
a numbered engraving schedule. Geometry comes from :mod:`catalogue`, so the
drawing and the browser preview can never drift apart.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..pdfcompat import Point, Rect, pymupdf
from . import catalogue, icons as icon_lib

MM = 72.0 / 25.4          # points per millimetre

PAGE_W, PAGE_H = 595.0, 842.0     # A4 portrait
MARGIN = 42.0

FONT = "helv"
FONT_BOLD = "hebo"
FONT_MONO = "cobo"

INK = (0.13, 0.13, 0.12)
MUTED = (0.42, 0.41, 0.38)
HAIRLINE = (0.72, 0.71, 0.68)
RULE = (0.88, 0.87, 0.85)
ACCENT = (0.62, 0.44, 0.18)


def _rgb(hex_colour: str) -> Tuple[float, float, float]:
    return catalogue.rgb_tuple(hex_colour)


def _shade(colour: Sequence[float], factor: float) -> Tuple[float, float, float]:
    return tuple(max(0.0, min(1.0, c * factor)) for c in colour)  # type: ignore[return-value]


def _text_width(text: str, size: float, font: str = FONT) -> float:
    return pymupdf.get_text_length(text, fontname=font, fontsize=size)


def _fit_size(text: str, width: float, ideal: float, font: str = FONT,
              floor: float = 3.2) -> float:
    """Shrink a font size until the string fits the available width.

    The target is deliberately a shade under the real width: a string that
    measures as an exact fit can still wrap inside ``insert_textbox``, and a
    wrapped line overflows its box and is dropped in full.
    """
    if not text:
        return ideal
    usable = max(1.0, width * 0.96)
    actual = _text_width(text, ideal, font)
    if actual <= usable:
        return ideal
    return max(floor, ideal * usable / actual)


def _line(page: Any, x: float, y: float, width: float, text: str, *,
          size: float = 8.0, font: str = FONT, color: Sequence[float] = INK,
          align: int = 0) -> float:
    """Draw one line of text and return the height it should be charged.

    PyMuPDF drops a text box wholesale when the rectangle is a fraction too
    short, so every box here is given plenty of vertical slack. Text is laid
    out from the top of the rectangle, which makes the extra room harmless.
    """
    page.insert_textbox(Rect(x, y, x + width, y + size * 2.8), text,
                        fontname=font, fontsize=size, color=color, align=align)
    return size * 1.34


# ------------------------------------------------------------------- panel

class PanelPainter:
    """Draws one panel elevation onto a page at a given scale."""

    def __init__(self, page: Any, design: Dict[str, Any], origin: Tuple[float, float],
                 scale: float = MM):
        self.page = page
        self.design = design
        self.ox, self.oy = origin
        self.scale = scale
        self.layout = catalogue.layout(
            design["family"], design["series"], design["region"], design["buttons"])
        self.button_finish = catalogue.button_finish(design["button_finish"])
        self.rim_finish = catalogue.rim_finish(design["rim_finish"])
        self.ink = _rgb(catalogue.ink_for(self.button_finish.hex))

    # -- coordinate helpers ------------------------------------------------

    def x(self, mm: float) -> float:
        return self.ox + mm * self.scale

    def y(self, mm: float) -> float:
        return self.oy + mm * self.scale

    def rect(self, box: Dict[str, float]) -> Rect:
        return Rect(self.x(box["x"]), self.y(box["y"]),
                    self.x(box["x"] + box["w"]), self.y(box["y"] + box["h"]))

    def _radius(self, box: Dict[str, float]) -> Optional[float]:
        corner = box.get("r") or 0
        if corner <= 0:
            return None
        shorter = min(box["w"], box["h"])
        return max(0.02, min(0.5, corner / shorter)) if shorter else None

    @property
    def size(self) -> Tuple[float, float]:
        return (self.layout["width_mm"] * self.scale,
                self.layout["height_mm"] * self.scale)

    # -- drawing -----------------------------------------------------------

    def draw(self) -> None:
        self._draw_plate()
        if self.layout["screen"]:
            self._draw_screen()
        for button in self.layout["buttons"]:
            self._draw_button(button)

    def _draw_plate(self) -> None:
        plate, face = self.layout["plate"], self.layout["face"]
        rim = _rgb(self.rim_finish.hex)

        shape = self.page.new_shape()
        shape.draw_rect(self.rect(plate), radius=self._radius(plate))
        shape.finish(color=_shade(rim, 0.55), fill=rim, width=0.6)
        shape.commit()

        # The aperture sits slightly darker so the rim reads as a raised bezel.
        shape = self.page.new_shape()
        shape.draw_rect(self.rect(face), radius=self._radius(face))
        shape.finish(color=_shade(rim, 0.7), fill=_shade(rim, 0.9), width=0.4)
        shape.commit()

    def _draw_screen(self) -> None:
        screen = self.layout["screen"]
        box = self.rect(screen)
        shape = self.page.new_shape()
        shape.draw_rect(box, radius=self._radius(screen))
        shape.finish(color=(0.2, 0.21, 0.22), fill=(0.11, 0.12, 0.13), width=0.5)
        shape.commit()
        _line(self.page, box.x0, box.y0 + box.height / 2 - 8, box.width,
              "Labelled in software", size=8, color=(0.62, 0.63, 0.64), align=1)

    def _draw_button(self, button: Dict[str, Any]) -> None:
        fill = _rgb(self.button_finish.hex)
        shape = self.page.new_shape()
        shape.draw_rect(self.rect(button), radius=self._radius(button))
        if button["zone"]:
            shape.finish(color=_shade(fill, 0.82), fill=fill, width=0.3,
                         dashes="[1 1] 0")
        else:
            shape.finish(color=_shade(fill, 0.72), fill=fill, width=0.5)
        shape.commit()

        led = button["led"]
        shape = self.page.new_shape()
        shape.draw_circle(Point(self.x(led["cx"]), self.y(led["cy"])),
                          led["r"] * self.scale)
        shape.finish(color=_shade(fill, 0.6), fill=_shade(fill, 0.86), width=0.3)
        shape.commit()

        self._draw_engraving(button)

    # -- engraving ---------------------------------------------------------

    def _engraving_for(self, index: int) -> Dict[str, Any]:
        for item in self.design.get("engraving", []):
            if int(item.get("index", -1)) == index:
                return item
        return {}

    def _draw_engraving(self, button: Dict[str, Any]) -> None:
        item = self._engraving_for(button["index"])
        lines = [str(line).strip() for line in item.get("lines", []) if str(line).strip()]
        icon = icon_lib.get(item.get("icon") or "")
        if not lines and not icon:
            return

        area = button["text"]
        left, top = self.x(area["x"]), self.y(area["y"])
        width, height = area["w"] * self.scale, area["h"] * self.scale
        side = item.get("icon_side", "left")

        if icon and lines and side == "left":
            icon_side = min(height * 0.52, width * 0.34)
            self._draw_icon(icon, (left, top + (height - icon_side) / 2, icon_side))
            gap = icon_side * 0.28
            self._draw_lines(lines, left + icon_side + gap, top,
                             width - icon_side - gap, height)
        elif icon and lines:                       # icon above the text
            icon_side = min(height * 0.42, width * 0.5)
            self._draw_icon(icon, (left + (width - icon_side) / 2,
                                   top + height * 0.14, icon_side))
            self._draw_lines(lines, left, top + height * 0.14 + icon_side,
                             width, height * 0.86 - icon_side, align=1)
        elif icon:
            icon_side = min(height * 0.6, width * 0.7)
            self._draw_icon(icon, (left + (width - icon_side) / 2,
                                   top + (height - icon_side) / 2, icon_side))
        else:
            self._draw_lines(lines, left, top, width, height)

    def _draw_lines(self, lines: List[str], left: float, top: float,
                    width: float, height: float, align: int = 0) -> None:
        ideal = min(height / (len(lines) + 1.1), 3.0 * self.scale)
        size = min(_fit_size(line, width, ideal) for line in lines)
        leading = size * 1.24
        cursor = top + (height - leading * len(lines)) / 2

        for line in lines:
            _line(self.page, left, cursor, width, line, size=size,
                  color=self.ink, align=align)
            cursor += leading

    def _draw_icon(self, icon: Dict[str, Any], box: Tuple[float, float, float]) -> None:
        """Render an icon from the shared library into a square box."""
        x0, y0, size = box
        stroke = max(0.35, size * 0.075)

        def px(value: float) -> float:
            return x0 + value / 100.0 * size

        def py(value: float) -> float:
            return y0 + value / 100.0 * size

        def points(flat: Sequence[float]) -> List[Point]:
            return [Point(px(flat[i]), py(flat[i + 1])) for i in range(0, len(flat), 2)]

        strokes = self.page.new_shape()
        drew_stroke = False
        fills = self.page.new_shape()
        drew_fill = False

        for item in icon["shapes"]:
            kind = item[0]
            if kind == "line":
                strokes.draw_line(Point(px(item[1]), py(item[2])),
                                  Point(px(item[3]), py(item[4])))
                drew_stroke = True
            elif kind == "poly":
                pts = points(item[1])
                if item[2] and len(pts) > 2:
                    pts = pts + [pts[0]]
                strokes.draw_polyline(pts)
                drew_stroke = True
            elif kind == "circle":
                strokes.draw_circle(Point(px(item[1]), py(item[2])),
                                    item[3] / 100.0 * size)
                drew_stroke = True
            elif kind == "fpoly":
                fills.draw_polyline(points(item[1]))
                drew_fill = True
            elif kind == "disc":
                fills.draw_circle(Point(px(item[1]), py(item[2])),
                                  item[3] / 100.0 * size)
                drew_fill = True

        if drew_stroke:
            strokes.finish(color=self.ink, width=stroke, lineCap=1, lineJoin=1,
                           closePath=False)
            strokes.commit()
        if drew_fill:
            fills.finish(color=self.ink, fill=self.ink, width=0.1, closePath=True)
            fills.commit()

    # -- annotation --------------------------------------------------------

    CALLOUT = 30.0     # how far position numbers sit outside the plate
    DIM_GAP = 22.0     # how far dimension lines sit outside the plate

    def annotate(self) -> None:
        """Position callouts and overall dimensions around the elevation."""
        width_mm = self.layout["width_mm"]
        height_mm = self.layout["height_mm"]

        shape = self.page.new_shape()
        for button in self.layout["buttons"]:
            middle = self.y(button["y"] + button["h"] / 2)
            if button["column"] == 0:
                shape.draw_line(Point(self.x(button["x"]), middle),
                                Point(self.x(0) - self.CALLOUT + 12, middle))
            else:
                shape.draw_line(Point(self.x(button["x"] + button["w"]), middle),
                                Point(self.x(width_mm) + self.CALLOUT - 12, middle))
        shape.finish(color=HAIRLINE, width=0.4, dashes="[1 1.6] 0")

        base = self.y(height_mm) + self.DIM_GAP
        right = self.x(width_mm) + self.DIM_GAP
        shape.draw_line(Point(self.x(0), base), Point(self.x(width_mm), base))
        shape.draw_line(Point(self.x(0), base - 3), Point(self.x(0), base + 3))
        shape.draw_line(Point(self.x(width_mm), base - 3),
                        Point(self.x(width_mm), base + 3))
        shape.draw_line(Point(right, self.y(0)), Point(right, self.y(height_mm)))
        shape.draw_line(Point(right - 3, self.y(0)), Point(right + 3, self.y(0)))
        shape.draw_line(Point(right - 3, self.y(height_mm)),
                        Point(right + 3, self.y(height_mm)))
        shape.finish(color=MUTED, width=0.5)
        shape.commit()

        for button in self.layout["buttons"]:
            middle = self.y(button["y"] + button["h"] / 2)
            if button["column"] == 0:
                x, align = self.x(0) - self.CALLOUT - 12, 2
            else:
                x, align = self.x(width_mm) + self.CALLOUT - 8, 0
            _line(self.page, x, middle - 4, 20, str(button["index"] + 1),
                  size=8, font=FONT_BOLD, color=ACCENT, align=align)

        # One caption carries both dimensions, which keeps the vertical
        # dimension line clear of whatever sits to the right of the drawing.
        _line(self.page, self.x(0), base + 5, width_mm * self.scale,
              f"{width_mm:g} x {height_mm:g} mm  ·  scale 1:1",
              size=7.5, color=MUTED, align=1)

    def bounds(self) -> Tuple[float, float]:
        """Right edge and bottom edge of the annotated drawing, in points."""
        width, height = self.size
        return (self.x(self.layout["width_mm"]) + self.DIM_GAP + 10,
                self.y(self.layout["height_mm"]) + self.DIM_GAP + 22)


# ------------------------------------------------------------- spec sheet

def _header(page: Any, job: str, project: str, client: str, index: int,
            total: int) -> float:
    _line(page, MARGIN, MARGIN, 380, "ANTUMBRA ENGRAVING SPECIFICATION",
          size=13, font=FONT_BOLD, color=INK)
    _line(page, PAGE_W - MARGIN - 120, MARGIN + 3, 120,
          f"Panel {index} of {total}", size=8, color=MUTED, align=2)

    meta = "   ".join(part for part in [
        f"Job: {job}" if job else "",
        f"Project: {project}" if project else "",
        f"Client: {client}" if client else "",
        _dt.date.today().isoformat(),
    ] if part)
    _line(page, MARGIN, MARGIN + 20, PAGE_W - 2 * MARGIN, meta, size=8, color=MUTED)

    top = MARGIN + 38
    shape = page.new_shape()
    shape.draw_line(Point(MARGIN, top), Point(PAGE_W - MARGIN, top))
    shape.finish(color=ACCENT, width=1.1)
    shape.commit()
    return top + 18


def _spec_block(page: Any, design: Dict[str, Any], left: float, top: float,
                width: float) -> float:
    rows = catalogue.describe(
        design["family"], design["series"], design["region"], design["buttons"],
        design["button_finish"], design["rim_finish"])

    cursor = top
    cursor += _line(page, left, cursor, width, design.get("name") or "Panel",
                    size=11, font=FONT_BOLD) + 3
    for label, value in (("Location", design.get("location", "")),
                         ("Reference", design.get("reference", ""))):
        if value:
            cursor += _line(page, left, cursor, width, f"{label}: {value}",
                            size=8, color=MUTED)
    cursor += 8

    label_w = min(74.0, width * 0.38)
    for label, value in rows:
        _line(page, left, cursor, label_w, label, size=8, color=MUTED)
        mono = label == "Product code"
        size = _fit_size(value, width - label_w, 9.0 if mono else 8.0,
                         FONT_MONO if mono else FONT_BOLD, floor=5.5)
        _line(page, left + label_w, cursor, width - label_w, value, size=size,
              font=FONT_MONO if mono else FONT_BOLD)
        cursor += 12.5

    # Finish swatches make the sheet checkable at a glance on the bench.
    cursor += 5
    swatch_w, swatch_h = 20.0, 13.0
    for label, finish in (("Buttons", catalogue.button_finish(design["button_finish"])),
                          ("Rim", catalogue.rim_finish(design["rim_finish"]))):
        shape = page.new_shape()
        shape.draw_rect(Rect(left, cursor, left + swatch_w, cursor + swatch_h),
                        radius=0.16)
        shape.finish(color=HAIRLINE, fill=_rgb(finish.hex), width=0.5)
        shape.commit()
        _line(page, left + swatch_w + 8, cursor + 2, width - swatch_w - 8,
              f"{label}: {finish.name}", size=8)
        cursor += swatch_h + 5

    cursor += 5
    cursor += _line(page, left, cursor, width,
                    f"Quantity: {int(design.get('quantity') or 1)}",
                    size=9, font=FONT_BOLD) + 2
    order = (design.get("order_12nc") or "").strip()
    cursor += _line(page, left, cursor, width,
                    f"Signify 12NC: {order or '________________'}",
                    size=8.5, color=INK if order else MUTED)
    return cursor


def _schedule(page: Any, design: Dict[str, Any], left: float, top: float,
              width: float) -> float:
    slots = catalogue.button_slots(design["family"], design["buttons"])
    if slots <= 0:
        _line(page, left, top, width,
              "This product is labelled in software; there is nothing to engrave.",
              size=8.5, color=MUTED)
        return top + 20

    cursor = top
    cursor += _line(page, left, cursor, width, "ENGRAVING SCHEDULE", size=9,
                    font=FONT_BOLD) + 2

    columns = (28.0, max(120.0, width - 28.0 - 96.0), 96.0)
    x = left
    for header, span in zip(("POS", "TEXT", "ICON"), columns):
        _line(page, x, cursor, span, header, size=6.8, color=MUTED)
        x += span
    cursor += 11

    shape = page.new_shape()
    shape.draw_line(Point(left, cursor), Point(left + width, cursor))
    shape.finish(color=HAIRLINE, width=0.6)
    shape.commit()
    cursor += 4

    by_index = {int(e.get("index", -1)): e for e in design.get("engraving", [])}
    for index in range(slots):
        item = by_index.get(index, {})
        lines = [str(line).strip() for line in item.get("lines", []) if str(line).strip()]
        icon = icon_lib.get(item.get("icon") or "")
        text = "  /  ".join(lines) if lines else "-"
        icon_name = icon["name"] if icon else "-"

        x = left
        _line(page, x, cursor, columns[0], str(index + 1), size=8,
              font=FONT_BOLD, color=ACCENT)
        x += columns[0]
        _line(page, x, cursor, columns[1], text,
              size=8.5, font=FONT_BOLD if lines else FONT,
              color=INK if lines else MUTED)
        x += columns[1]
        _line(page, x, cursor, columns[2], icon_name, size=8,
              color=INK if icon else MUTED)
        cursor += 13.5

        shape = page.new_shape()
        shape.draw_line(Point(left, cursor - 2.5), Point(left + width, cursor - 2.5))
        shape.finish(color=RULE, width=0.35)
        shape.commit()

    return cursor + 4


def _footer(page: Any, design: Dict[str, Any]) -> None:
    notes = (design.get("notes") or "").strip()
    top = PAGE_H - MARGIN - 44
    shape = page.new_shape()
    shape.draw_line(Point(MARGIN, top), Point(PAGE_W - MARGIN, top))
    shape.finish(color=HAIRLINE, width=0.5)
    shape.commit()

    cursor = top + 6
    if notes:
        cursor += _line(page, MARGIN, cursor, PAGE_W - 2 * MARGIN,
                        f"Notes: {notes}", size=8) + 2
    _line(page, MARGIN, cursor, PAGE_W - 2 * MARGIN,
          "Elevation is drawn 1:1 - print at 100% with no page scaling to check "
          "against the part.", size=7, color=MUTED)
    _line(page, MARGIN, cursor + 9, PAGE_W - 2 * MARGIN,
          "The 12NC is allocated by Signify: take it from your quote rather than "
          "from this sheet.", size=7, color=MUTED)


def design_page(doc: Any, design: Dict[str, Any], job: str, project: str,
                client: str, index: int, total: int) -> None:
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    top = _header(page, job, project, client, index, total)

    painter = PanelPainter(page, design, (MARGIN + 46, top + 10))
    painter.draw()
    painter.annotate()
    drawing_right, drawing_bottom = painter.bounds()

    column_left = drawing_right + 26
    column_width = PAGE_W - MARGIN - column_left
    after_spec = _spec_block(page, design, column_left, top + 10, column_width)

    # The schedule prefers the full page width under the drawing, and only
    # squeezes into the right-hand column when the drawing runs long.
    schedule_top = max(after_spec + 14, drawing_bottom + 18)
    floor = PAGE_H - MARGIN - 60
    slots = catalogue.button_slots(design["family"], design["buttons"])
    needed = 30 + 13.5 * max(slots, 1)
    if schedule_top + needed <= floor:
        _schedule(page, design, MARGIN, schedule_top, PAGE_W - 2 * MARGIN)
    else:
        _schedule(page, design, column_left, after_spec + 14, column_width)

    _footer(page, design)


def spec_sheet(designs: Sequence[Dict[str, Any]], job_name: str = "",
               project: str = "", client: str = "") -> bytes:
    """Build the complete PDF for a set of designs, one panel per page."""
    if not designs:
        raise ValueError("There are no panels to export")

    doc = pymupdf.open()
    try:
        for position, design in enumerate(designs, start=1):
            catalogue.validate(design["family"], design["series"], design["region"],
                               design["buttons"], design["button_finish"],
                               design["rim_finish"])
            design_page(doc, design, job_name, project, client, position, len(designs))
        doc.set_metadata({
            "title": f"Antumbra engraving specification - {job_name or 'job'}",
            "subject": "Antumbra panel configuration and engraving schedule",
            "creator": "PDF Workbench - Antumbra designer",
        })
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
