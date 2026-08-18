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
ARTWORK_INK = (0.0, 0.0, 0.0)     # engraving artwork is cut, not printed


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


# -------------------------------------------------------- engraving layout

# Worked out in millimetres and kept clear of any page, so the spec sheet, the
# order form and the pre-flight check all place a label identically.

ALIGN_LEFT, ALIGN_CENTRE, ALIGN_RIGHT = 0, 1, 2


def engraved_lines(item: Dict[str, Any]) -> List[str]:
    """The non-blank rows of one position's label, in order."""
    return [str(line).strip() for line in item.get("lines", []) if str(line).strip()]


def text_align(button: Dict[str, Any], item: Dict[str, Any]) -> int:
    """A label reads away from its indicator, so the two columns mirror."""
    if item.get("icon") and item.get("icon_side") == "top":
        return ALIGN_CENTRE
    return (ALIGN_RIGHT if button["text"].get("align") == "right" else ALIGN_LEFT)


def engraving_boxes(button: Dict[str, Any], item: Dict[str, Any], has_icon: bool,
                    has_lines: bool) -> Tuple[Optional[Tuple[float, float, float]],
                                              Optional[Tuple[float, float, float, float]]]:
    """Where the icon and the text sit inside one button, in millimetres.

    Returns ``((icon_x, icon_y, icon_side), (text_x, text_y, text_w, text_h))``,
    either of which is ``None`` when that part is not engraved. On the right-hand
    column the icon swaps to the far side so it stays beside the indicator and
    the panel reads symmetrically.
    """
    area = button["text"]
    left, top, width, height = area["x"], area["y"], area["w"], area["h"]
    mirrored = area.get("align") == "right"

    if has_icon and has_lines and item.get("icon_side") != "top":
        side = min(height * 0.52, width * 0.34)
        gap = side * 0.28
        if mirrored:
            return ((left + width - side, top + (height - side) / 2, side),
                    (left, top, width - side - gap, height))
        return ((left, top + (height - side) / 2, side),
                (left + side + gap, top, width - side - gap, height))

    if has_icon and has_lines:                      # icon above the text
        side = min(height * 0.42, width * 0.5)
        return ((left + (width - side) / 2, top + height * 0.14, side),
                (left, top + height * 0.14 + side, width, height * 0.86 - side))

    if has_icon:
        side = min(height * 0.6, width * 0.7)
        return ((left + (width - side) / 2, top + (height - side) / 2, side), None)

    return (None, (left, top, width, height))


def label_size(lines: Sequence[str], width: float, height: float, font: str,
               requested: float = 0.0) -> float:
    """The size a label is engraved at, given the room it has.

    A requested size is honoured until it would overrun the button: an
    overrunning line is dropped in full by ``insert_textbox``, so it is always
    the size that gives way, never the text.
    """
    ceiling = height / (len(lines) + 1.1)
    ideal = min(requested or catalogue.AUTO_TEXT_MM * MM, ceiling)
    return min(_fit_size(line, width, ideal, font) for line in lines)


def fitted_sizes(design: Dict[str, Any]) -> Dict[int, float]:
    """The height, in millimetres, each engraved position is actually cut at."""
    layout = catalogue.layout(design["family"], design["series"], design["region"],
                              design["buttons"])
    font = catalogue.engraving_font(design.get("font"))
    requested = catalogue.text_size_mm(design.get("text_size_mm")) * MM
    by_index = {int(item.get("index", -1)): item
                for item in design.get("engraving", [])}

    sizes: Dict[int, float] = {}
    for button in layout["buttons"]:
        item = by_index.get(button["index"], {})
        lines = engraved_lines(item)
        if not lines:
            continue
        _icon_box, text_box = engraving_boxes(
            button, item, bool(icon_lib.get(item.get("icon") or "")), True)
        if not text_box:
            continue
        _x, _y, width, height = text_box
        sizes[button["index"]] = label_size(
            lines, width * MM, height * MM, font.pdf, requested) / MM
    return sizes


# ------------------------------------------------------------------- panel

class PanelPainter:
    """Draws one panel elevation onto a page at a given scale.

    In ``artwork`` mode only the engraving is drawn, in black on nothing: that
    is what the laser cuts, and it is the form the Dynalite order form takes.
    """

    def __init__(self, page: Any, design: Dict[str, Any], origin: Tuple[float, float],
                 scale: float = MM, artwork: bool = False):
        self.page = page
        self.design = design
        self.ox, self.oy = origin
        self.scale = scale
        self.layout = catalogue.layout(
            design["family"], design["series"], design["region"], design["buttons"])
        self.button_finish = catalogue.button_finish(design["button_finish"])
        self.rim_finish = catalogue.rim_finish(design["rim_finish"])
        self.ink = ARTWORK_INK if artwork else _rgb(
            catalogue.ink_for(self.button_finish.hex))
        self.font = catalogue.engraving_font(design.get("font"))
        self.text_mm = catalogue.text_size_mm(design.get("text_size_mm"))

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

    def draw_artwork(self) -> None:
        """Only the engraving, positioned exactly where it lands on the part."""
        for button in self.layout["buttons"]:
            self._draw_engraving(button)

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
        lines = engraved_lines(item)
        icon = icon_lib.get(item.get("icon") or "")
        if not lines and not icon:
            return

        icon_box, text_box = engraving_boxes(button, item, bool(icon), bool(lines))
        if icon and icon_box:
            x0, y0, side = icon_box
            self._draw_icon(icon, (self.x(x0), self.y(y0), side * self.scale))
        if lines and text_box:
            x0, y0, width, height = text_box
            self._draw_lines(lines, self.x(x0), self.y(y0), width * self.scale,
                             height * self.scale, align=text_align(button, item))

    def _draw_lines(self, lines: List[str], left: float, top: float,
                    width: float, height: float, align: int = 0) -> None:
        size = label_size(lines, width, height, self.font.pdf,
                          self.text_mm * self.scale)
        leading = size * 1.24
        cursor = top + (height - leading * len(lines)) / 2

        for line in lines:
            _line(self.page, left, cursor, width, line, size=size,
                  font=self.font.pdf, color=self.ink, align=align)
            cursor += leading

    def _draw_icon(self, icon: Dict[str, Any], box: Tuple[float, float, float]) -> None:
        _icon_shapes(self.page, icon, box, self.ink)

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


def _icon_shapes(page: Any, icon: Dict[str, Any], box: Tuple[float, float, float],
                 ink: Sequence[float]) -> None:
    """Render an icon from the shared library into a square box."""
    x0, y0, size = box
    stroke = max(0.35, size * 0.075)

    def px(value: float) -> float:
        return x0 + value / 100.0 * size

    def py(value: float) -> float:
        return y0 + value / 100.0 * size

    def points(flat: Sequence[float]) -> List[Point]:
        return [Point(px(flat[i]), py(flat[i + 1])) for i in range(0, len(flat), 2)]

    strokes = page.new_shape()
    drew_stroke = False
    fills = page.new_shape()
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
        strokes.finish(color=ink, width=stroke, lineCap=1, lineJoin=1,
                       closePath=False)
        strokes.commit()
    if drew_fill:
        fills.finish(color=ink, fill=ink, width=0.1, closePath=True)
        fills.commit()


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
    font = catalogue.engraving_font(design.get("font"))
    asked = catalogue.text_size_mm(design.get("text_size_mm"))
    cursor += _line(page, left, cursor, width,
                    f"Engraving: {font.name}, "
                    + (f"{asked:g} mm" if asked else "fitted to each button"),
                    size=8, color=MUTED) + 3
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


# -------------------------------------------------------------- order form

# A landscape sheet in the shape of the Dynalite panel order form: engraving
# artwork at 1:1 inside registration marks, and a title block along the foot
# carrying the product code, the 12NC and the panel's identity. The reference
# form prints a manufacturer's logo bottom-left; that space is given here to
# the words and icons themselves, so the sheet says what is being cut without
# anyone measuring the artwork.

ORDER_W, ORDER_H = 841.89, 595.28      # A4 landscape
ORDER_MARGIN = 14.0
BLOCK_H = 85.0                         # title block along the foot
CELL_H = 26.0                          # one metadata row
CELL_W = 130.0
CELL_GAP = 2.0
CELL_PAD = 3.0
TICK_LEN = 21.0                        # registration-mark arm
TICK_GAP = 7.0                         # its clearance from the corner
ORDER_FONT_SIZE = 8.0
FRAME = (0.75, 0.75, 0.75)


def _order_frame(page: Any) -> Tuple[Rect, Rect]:
    """Draw the sheet border and the title block, returning both rectangles."""
    frame = Rect(ORDER_MARGIN, ORDER_MARGIN, ORDER_W - ORDER_MARGIN,
                 ORDER_H - ORDER_MARGIN)
    block = Rect(frame.x0, frame.y1 - BLOCK_H, frame.x1, frame.y1 - 1)

    shape = page.new_shape()
    shape.draw_rect(frame)
    shape.draw_rect(block)
    shape.finish(color=FRAME, width=0.5)
    shape.commit()
    return frame, block


def _order_cell(page: Any, rect: Rect, text: str = "") -> None:
    shape = page.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=INK, width=0.5)
    shape.commit()
    if text:
        _line(page, rect.x0 + CELL_PAD, rect.y0 + 2, rect.width - 2 * CELL_PAD,
              text, size=ORDER_FONT_SIZE)


def _registration_marks(page: Any, box: Rect) -> None:
    """Corner ticks standing off the panel outline, as the reference form has.

    The outline itself is never drawn: everything inside these marks is cut, so
    a printed rectangle would be one more thing for the operator to ignore.
    """
    shape = page.new_shape()
    for x, direction in ((box.x0, -1), (box.x1, 1)):
        for y in (box.y0, box.y1):
            shape.draw_line(Point(x + direction * TICK_GAP, y),
                            Point(x + direction * (TICK_GAP + TICK_LEN), y))
    for y, direction in ((box.y0, -1), (box.y1, 1)):
        for x in (box.x0, box.x1):
            shape.draw_line(Point(x, y + direction * TICK_GAP),
                            Point(x, y + direction * (TICK_GAP + TICK_LEN)))
    shape.finish(color=INK, width=0.5)
    shape.commit()


def _order_thumbnail(page: Any, design: Dict[str, Any], box: Rect) -> None:
    """A hairline elevation, small enough to check the panel at a glance."""
    layout = catalogue.layout(design["family"], design["series"], design["region"],
                              design["buttons"])
    scale = min(box.width / layout["width_mm"], box.height / layout["height_mm"])
    ox = box.x0 + (box.width - layout["width_mm"] * scale) / 2
    oy = box.y0 + (box.height - layout["height_mm"] * scale) / 2

    def place(item: Dict[str, float]) -> Rect:
        return Rect(ox + item["x"] * scale, oy + item["y"] * scale,
                    ox + (item["x"] + item["w"]) * scale,
                    oy + (item["y"] + item["h"]) * scale)

    shape = page.new_shape()
    shape.draw_rect(place(layout["plate"]))
    for button in layout["buttons"]:
        shape.draw_rect(place(button))
    if layout["screen"]:
        shape.draw_rect(place(layout["screen"]))
    shape.finish(color=MUTED, width=0.5)
    shape.commit()


def _order_legend(page: Any, design: Dict[str, Any], box: Rect, job: str) -> None:
    """The engraving itself, listed where the reference form prints its logo."""
    heading = "ENGRAVING"
    if job:
        heading += f"   {job}"
    if design.get("location"):
        heading += f"   {design['location']}"
    _line(page, box.x0 + CELL_PAD, box.y0 + 2, box.width - 2 * CELL_PAD, heading,
          size=7, font=FONT_BOLD, color=MUTED)

    slots = catalogue.button_slots(design["family"], design["buttons"])
    if slots <= 0:
        _line(page, box.x0 + CELL_PAD, box.y0 + 16, box.width - 2 * CELL_PAD,
              "Labelled in software - nothing is engraved on this panel.",
              size=ORDER_FONT_SIZE, color=MUTED)
        return

    by_index = {int(item.get("index", -1)): item
                for item in design.get("engraving", [])}
    top = box.y0 + 15
    rows = max(1, (slots + 1) // 2)
    row_h = min(18.0, (box.y1 - top - 3) / rows)
    column_w = (box.width - 2 * CELL_PAD) / 2

    painter_ink = ARTWORK_INK
    for index in range(slots):
        row, column = divmod(index, 2)
        x = box.x0 + CELL_PAD + column * column_w
        y = top + row * row_h

        _line(page, x, y + 1, 12, str(index + 1), size=7.5, font=FONT_BOLD,
              color=ACCENT)
        item = by_index.get(index, {})
        icon = icon_lib.get(item.get("icon") or "")
        cursor = x + 13
        if icon:
            side = min(row_h - 4, 11.0)
            _icon_shapes(page, icon, (cursor, y + (row_h - side) / 2 - 1, side),
                         painter_ink)
            cursor += side + 4
        words = "  ".join(engraved_lines(item))
        width = x + column_w - cursor - 6
        _line(page, cursor, y + 1, width,
              words or ("icon only" if icon else "blank"),
              size=_fit_size(words, width, ORDER_FONT_SIZE, FONT, floor=5.0),
              color=INK if words else MUTED)


def order_page(doc: Any, design: Dict[str, Any], job: str, project: str,
               client: str) -> None:
    page = doc.new_page(width=ORDER_W, height=ORDER_H)
    frame, block = _order_frame(page)

    # -- artwork, centred in the space above the title block ---------------
    layout = catalogue.layout(design["family"], design["series"], design["region"],
                              design["buttons"])
    plate_w = layout["width_mm"] * MM
    plate_h = layout["height_mm"] * MM
    origin = ((frame.x0 + frame.x1 - plate_w) / 2,
              (frame.y0 + block.y0 - plate_h) / 2)
    _registration_marks(page, Rect(origin[0], origin[1],
                                   origin[0] + plate_w, origin[1] + plate_h))
    PanelPainter(page, design, origin, artwork=True).draw_artwork()

    # -- title block -------------------------------------------------------
    right = frame.x1
    top = block.y0 + 2
    rows = [top + n * (CELL_H + CELL_GAP) for n in range(3)]

    code = catalogue.part_code(design["family"], design["series"], design["region"],
                               design["buttons"], design["button_finish"],
                               design["rim_finish"])
    today = _dt.date.today()
    order_12nc = (design.get("order_12nc") or "").strip()
    region = catalogue.region(design["region"])
    family = catalogue.family(design["family"])
    button = catalogue.button_finish(design["button_finish"])

    _order_cell(page, Rect(right - 2 * CELL_W - CELL_GAP, rows[0],
                           right - CELL_W - CELL_GAP, rows[0] + CELL_H),
                f"Creation date: {today:%B} {today.day}, {today.year}")
    _order_cell(page, Rect(right - CELL_W, rows[0], right, rows[0] + CELL_H),
                f"Product code: {code}")
    _order_cell(page, Rect(right - 2 * CELL_W - CELL_GAP, rows[1],
                           right - CELL_W - CELL_GAP, rows[1] + CELL_H),
                f"12NC: {order_12nc or '____________'}")
    _order_cell(page, Rect(right - CELL_W, rows[1], right, rows[1] + CELL_H),
                f"Panel Name: {design.get('name') or 'Panel'}")
    _order_cell(page, Rect(right - 2 * CELL_W - CELL_GAP, rows[2], right,
                           rows[2] + CELL_H),
                f"Style: {region.style}; Type: {family.name.replace('Antumbra', '')}; "
                f"Button Colour: {button.name}")

    # Orientation cell, with the elevation the reference form draws beside it.
    orient_right = right - 2 * CELL_W - 2 * CELL_GAP - 1
    orient = Rect(orient_right - CELL_W, top, orient_right, block.y1 - 1)
    shape = page.new_shape()
    shape.draw_rect(orient)
    shape.finish(color=INK, width=0.5)
    shape.commit()
    plate_shape = ("Portrait" if layout["height_mm"] > layout["width_mm"]
                   else "Landscape" if layout["width_mm"] > layout["height_mm"]
                   else "Square")
    _line(page, orient.x0 + CELL_PAD, orient.y0 + 2, orient.width - 2 * CELL_PAD,
          f"Orientation: {plate_shape}", size=ORDER_FONT_SIZE)
    _order_thumbnail(page, design, Rect(orient.x0 + 8, orient.y0 + 18,
                                        orient.x1 - 8, orient.y1 - 6))

    legend = Rect(frame.x0 + 1, top, orient.x0 - CELL_GAP - 1, block.y1 - 1)
    _order_cell(page, legend)
    _order_legend(page, design, legend, job or project or client)


def order_form(designs: Sequence[Dict[str, Any]], job_name: str = "",
               project: str = "", client: str = "") -> bytes:
    """Order forms for a job, one landscape sheet per panel."""
    if not designs:
        raise ValueError("There are no panels to export")

    doc = pymupdf.open()
    try:
        for design in designs:
            catalogue.validate(design["family"], design["series"], design["region"],
                               design["buttons"], design["button_finish"],
                               design["rim_finish"])
            order_page(doc, design, job_name, project, client)
        doc.set_metadata({
            "title": f"Antumbra order form - {job_name or 'job'}",
            "subject": "Antumbra panel order form and engraving artwork",
            "creator": "PDF Workbench - Antumbra designer",
        })
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
