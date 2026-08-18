"""Antumbra product catalogue: families, finishes, geometry and part codes.

The whole configurator is driven by the tables in this module. The browser
preview, the PDF elevation drawing and the ordering code all read the same
geometry, so a corrected dimension or a new finish is a one-line change that
lands everywhere at once.

Part codes follow the published Antumbra structure::

    PA 6 B P A - W A
    │  │ │ │ │   │ └── rim finish
    │  │ │ │ │   └──── button finish
    │  │ │ │ └──────── region     A = Australian/American, E = European
    │  │ │ └────────── series     P = Antumbra, L = AntumbraLite
    │  │ └──────────── family     B = Button, T = Touch, D = Display
    │  └────────────── button count (button families only)
    └───────────────── Antumbra range

so ``PA6BPA-WA`` is a six-button Australian/American panel with white buttons
and an aluminium rim, and ``PA4BLE-WW`` is a four-button European AntumbraLite
in white on white.

The Signify 12NC ordering number is deliberately *not* generated here. It is a
catalogue number allocated by Signify, not something derivable from the
configuration, so inventing one would put a wrong number on a purchase order.
The designer carries it as a field the user fills in from their quote instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import icons as icon_lib


# --------------------------------------------------------------- data model

@dataclass(frozen=True)
class Finish:
    code: str
    name: str
    hex: str
    kind: str          # polycarbonate | metallic | plated
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code, "name": self.name, "hex": self.hex,
            "kind": self.kind, "note": self.note, "ink": ink_for(self.hex),
        }


@dataclass(frozen=True)
class Family:
    code: str          # B | T | D
    name: str
    counts: Tuple[int, ...]
    engravable: bool
    series: Tuple[str, ...]
    blurb: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code, "name": self.name, "counts": list(self.counts),
            "engravable": self.engravable, "series": list(self.series),
            "blurb": self.blurb,
        }


@dataclass(frozen=True)
class Region:
    code: str          # A | E
    name: str
    width_mm: float
    height_mm: float
    mounting: str
    short: str = ""    # compact form, for order lines
    style: str = ""    # the word Dynalite prints on an order form

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code, "name": self.name, "width_mm": self.width_mm,
            "height_mm": self.height_mm, "mounting": self.mounting,
            "short": self.short, "style": self.style,
        }


@dataclass(frozen=True)
class EngravingFont:
    """A typeface the laser can cut, in both the browser and the PDF.

    ``pdf`` is a base-14 name so the spec sheet needs no embedded font file and
    the packaged executable stays a single binary.
    """

    id: str
    name: str
    pdf: str
    css: str
    weight: str = "normal"

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "css": self.css,
                "weight": self.weight}


@dataclass(frozen=True)
class Series:
    code: str          # P | L
    name: str
    rim_mm: float
    blurb: str

    def as_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "name": self.name, "rim_mm": self.rim_mm,
                "blurb": self.blurb}


# ------------------------------------------------------------------ tables

BUTTON_FINISHES: Tuple[Finish, ...] = (
    Finish("W", "White", "#EDECE8", "polycarbonate"),
    Finish("S", "Silver", "#C6C8CB", "polycarbonate"),
    Finish("M", "Magnesium", "#9C9A92", "polycarbonate"),
    Finish("A", "Aluminium", "#B6BABE", "metallic"),
    Finish("G", "Gold", "#C8A63E", "metallic"),
    Finish("N", "Noir", "#262620", "metallic", "Black"),
    Finish("V", "Vintage", "#7B5B3C", "metallic", "Bronze"),
    Finish("P", "Prestige", "#C79184", "metallic", "Rose gold"),
)

RIM_FINISHES: Tuple[Finish, ...] = (
    Finish("W", "White", "#EDECE8", "polycarbonate"),
    Finish("M", "Magnesium", "#9C9A92", "polycarbonate"),
    Finish("C", "Chrome", "#D9DFE5", "plated"),
    Finish("A", "Aluminium", "#B6BABE", "metallic"),
)

FAMILIES: Tuple[Family, ...] = (
    Family("B", "AntumbraButton", (2, 4, 6), True, ("P", "L"),
           "Mechanical buttons with backlit engraving over a six-position grid."),
    Family("T", "AntumbraTouch", (6,), True, ("P",),
           "Capacitive touch surface with proximity wake and backlit engraving."),
    Family("D", "AntumbraDisplay", (), False, ("P",),
           "Graphical display with soft keys; labelling is set in software."),
)

REGIONS: Tuple[Region, ...] = (
    Region("A", "Australian / American", 75.0, 116.0,
           "US 1-gang / AU vertical wall box", "AU/US", "American"),
    Region("E", "European", 86.0, 86.0, "EU 60 mm flush box", "EU", "European"),
)

SERIES: Tuple[Series, ...] = (
    Series("P", "Antumbra", 4.0, "Full metal rim, mix-and-match finishes."),
    Series("L", "AntumbraLite", 2.6, "Slim rim, cost-optimised for volume."),
)

BACKLIGHTS: Tuple[Tuple[str, str, str], ...] = (
    ("white", "White", "#F2F4F6"),
    ("amber", "Amber", "#F0B45A"),
    ("blue", "Blue", "#6FA8DC"),
    ("green", "Green", "#8CC98C"),
    ("red", "Red", "#DE7A6E"),
)

ENGRAVING_FONTS: Tuple[EngravingFont, ...] = (
    EngravingFont("sans", "Helvetica", "helv", "Helvetica, Arial, sans-serif"),
    EngravingFont("sans-bold", "Helvetica Bold", "hebo",
                  "Helvetica, Arial, sans-serif", "bold"),
    EngravingFont("serif", "Times", "tiro", "'Times New Roman', Times, serif"),
    EngravingFont("serif-bold", "Times Bold", "tibo",
                  "'Times New Roman', Times, serif", "bold"),
    EngravingFont("mono", "Courier", "cour", "'Courier New', Courier, monospace"),
)

MAX_LINES = 2
MAX_CHARS_PER_LINE = 14

DEFAULT_FONT = "sans"
AUTO_TEXT_MM = 3.0        # the cap an automatically fitted label works down from
MAX_TEXT_MM = 6.0
# Offered in the designer; a label still shrinks below its chosen size rather
# than overrun the button, because an overrunning line is dropped in full.
TEXT_SIZES_MM: Tuple[float, ...] = (1.8, 2.0, 2.2, 2.5, 2.8, 3.0, 3.5, 4.0)

_BUTTON_BY_CODE = {f.code: f for f in BUTTON_FINISHES}
_RIM_BY_CODE = {f.code: f for f in RIM_FINISHES}
_FAMILY_BY_CODE = {f.code: f for f in FAMILIES}
_REGION_BY_CODE = {r.code: r for r in REGIONS}
_SERIES_BY_CODE = {s.code: s for s in SERIES}
_FONT_BY_ID = {f.id: f for f in ENGRAVING_FONTS}


# ------------------------------------------------------------------ colours

def _channels(hex_colour: str) -> Tuple[float, float, float]:
    value = hex_colour.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def luminance(hex_colour: str) -> float:
    """Perceived brightness, 0 (black) to 1 (white)."""
    red, green, blue = _channels(hex_colour)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def ink_for(hex_colour: str) -> str:
    """Engraving colour that stays readable on a given fascia."""
    return "#2A2A26" if luminance(hex_colour) > 0.45 else "#F0EFEA"


def rgb_tuple(hex_colour: str) -> Tuple[float, float, float]:
    """0-1 RGB triple, the form PyMuPDF wants."""
    return _channels(hex_colour)


# ------------------------------------------------------------- part numbers

def part_code(family: str, series: str, region: str, buttons: int,
              button_finish: str, rim_finish: str) -> str:
    """Build the Antumbra product code for a configuration."""
    fam = _FAMILY_BY_CODE[family]
    count = str(buttons) if fam.counts else ""
    return f"PA{count}{family}{series}{region}-{button_finish}{rim_finish}"


def base_code(family: str, series: str, region: str, buttons: int) -> str:
    """The product code without its finish suffix, as used on drawings."""
    fam = _FAMILY_BY_CODE[family]
    count = str(buttons) if fam.counts else ""
    return f"PA{count}{family}{series}{region}"


def validate(family: str, series: str, region: str, buttons: int,
             button_finish: str, rim_finish: str) -> None:
    """Raise ValueError describing the first thing that cannot be built."""
    fam = _FAMILY_BY_CODE.get(family)
    if fam is None:
        raise ValueError(f"Unknown product family '{family}'")
    if series not in _SERIES_BY_CODE:
        raise ValueError(f"Unknown series '{series}'")
    if series not in fam.series:
        allowed = ", ".join(_SERIES_BY_CODE[s].name for s in fam.series)
        raise ValueError(f"{fam.name} is only made in: {allowed}")
    if region not in _REGION_BY_CODE:
        raise ValueError(f"Unknown region '{region}'")
    if fam.counts and buttons not in fam.counts:
        allowed = ", ".join(str(c) for c in fam.counts)
        raise ValueError(f"{fam.name} comes in {allowed} buttons, not {buttons}")
    if button_finish not in _BUTTON_BY_CODE:
        raise ValueError(f"Unknown button finish '{button_finish}'")
    if rim_finish not in _RIM_BY_CODE:
        raise ValueError(f"Unknown rim finish '{rim_finish}'")


def button_finish(code: str) -> Finish:
    return _BUTTON_BY_CODE[code]


def rim_finish(code: str) -> Finish:
    return _RIM_BY_CODE[code]


def family(code: str) -> Family:
    return _FAMILY_BY_CODE[code]


def region(code: str) -> Region:
    return _REGION_BY_CODE[code]


def series(code: str) -> Series:
    return _SERIES_BY_CODE[code]


def engraving_font(code: Optional[str] = None) -> EngravingFont:
    """The typeface a design engraves in; blank means the house default."""
    found = _FONT_BY_ID.get((code or DEFAULT_FONT).strip() or DEFAULT_FONT)
    if found is None:
        raise ValueError(f"Unknown engraving font '{code}'")
    return found


def text_size_mm(value: Optional[float] = None) -> float:
    """A requested label height in millimetres, or 0 to fit each button."""
    size = float(value or 0.0)
    if size <= 0:
        return 0.0
    if size > MAX_TEXT_MM:
        raise ValueError(f"An engraved label cannot be taller than {MAX_TEXT_MM:g} mm")
    return size


def button_slots(family_code: str, buttons: int) -> int:
    """How many engravable positions a configuration has."""
    fam = _FAMILY_BY_CODE[family_code]
    if not fam.engravable:
        return 0
    return buttons if fam.counts else 6


# ----------------------------------------------------------------- geometry

# Millimetre constants for the button field. These are drawing conventions for
# the elevation, not manufacturing tolerances.
_FIELD_PAD = 3.5      # gap between the rim aperture and the button block
_GAP = 2.2            # gap between adjacent buttons
_LED_INSET = 5.0      # indicator centre, in from the button's outer edge
_LED_R = 1.35
_TEXT_OUTER = 8.4     # engraving area starts here, clear of the indicator
_TEXT_INNER = 2.6     # clear margin at the button's inner edge


def layout(family_code: str, series_code: str, region_code: str,
           buttons: int) -> Dict[str, Any]:
    """Full millimetre geometry for one panel, shared by every renderer.

    Returns the plate, the rim aperture and one rect per engravable position,
    each with the indicator dot and the engraving area already worked out.
    """
    fam = _FAMILY_BY_CODE[family_code]
    reg = _REGION_BY_CODE[region_code]
    ser = _SERIES_BY_CODE[series_code]

    plate = {"x": 0.0, "y": 0.0, "w": reg.width_mm, "h": reg.height_mm,
             "r": 3.0}
    rim = ser.rim_mm
    face = {"x": rim, "y": rim, "w": reg.width_mm - 2 * rim,
            "h": reg.height_mm - 2 * rim, "r": 2.0}

    result: Dict[str, Any] = {
        "plate": plate, "face": face, "rim_mm": rim,
        "buttons": [], "screen": None,
        "width_mm": reg.width_mm, "height_mm": reg.height_mm,
    }

    if fam.code == "D":
        # Display: a screen occupying the face, with no engravable buttons.
        result["screen"] = {
            "x": round(face["x"] + _FIELD_PAD, 2),
            "y": round(face["y"] + _FIELD_PAD, 2),
            "w": round(face["w"] - 2 * _FIELD_PAD, 2),
            "h": round(face["h"] - 2 * _FIELD_PAD, 2),
            "r": 1.5,
        }
        return result

    slots = button_slots(family_code, buttons)
    if slots <= 0:
        return result

    columns = 2
    rows = max(1, slots // columns)

    grid_x = face["x"] + _FIELD_PAD
    grid_y = face["y"] + _FIELD_PAD
    grid_w = face["w"] - 2 * _FIELD_PAD
    grid_h = face["h"] - 2 * _FIELD_PAD

    button_w = (grid_w - _GAP * (columns - 1)) / columns
    button_h = (grid_h - _GAP * (rows - 1)) / rows

    # Touch panels are one continuous surface: the "buttons" are zones, drawn
    # with hairlines rather than moulded gaps.
    zone = fam.code == "T"

    # The two columns are mirror images: each indicator sits at the outer edge
    # of its own column, so the right-hand dots land on the right of the panel
    # and the label reads away from them, which is how the part is made.
    for index in range(slots):
        row, column = divmod(index, columns)
        x = grid_x + column * (button_w + _GAP)
        y = grid_y + row * (button_h + _GAP)
        mirrored = column == columns - 1
        if mirrored:
            led_cx = x + button_w - _LED_INSET
            text_left = x + _TEXT_INNER
        else:
            led_cx = x + _LED_INSET
            text_left = x + _TEXT_OUTER
        result["buttons"].append({
            "index": index,
            "row": row,
            "column": column,
            "x": round(x, 2), "y": round(y, 2),
            "w": round(button_w, 2), "h": round(button_h, 2),
            "r": 1.6 if not zone else 0.8,
            "zone": zone,
            "side": "right" if mirrored else "left",
            "led": {"cx": round(led_cx, 2),
                    "cy": round(y + button_h / 2, 2), "r": _LED_R},
            "text": {"x": round(text_left, 2),
                     "y": round(y, 2),
                     "w": round(button_w - _TEXT_OUTER - _TEXT_INNER, 2),
                     "h": round(button_h, 2),
                     "align": "right" if mirrored else "left"},
        })
    return result


# ------------------------------------------------------------------ summary

def product_name(family_code: str, series_code: str) -> str:
    """Marketing name, e.g. 'AntumbraButton' or 'AntumbraLite Button'."""
    fam = _FAMILY_BY_CODE[family_code]
    if series_code == "P":
        return fam.name
    return f"AntumbraLite {fam.name.replace('Antumbra', '')}".strip()


def describe(family_code: str, series_code: str, region_code: str,
             buttons: int, button_code: str, rim_code: str) -> List[Tuple[str, str]]:
    """Label/value pairs for the spec sheet and the sidebar summary."""
    fam = _FAMILY_BY_CODE[family_code]
    reg = _REGION_BY_CODE[region_code]
    btn = _BUTTON_BY_CODE[button_code]
    rim = _RIM_BY_CODE[rim_code]

    rows = [
        ("Product", product_name(family_code, series_code)),
        ("Product code", part_code(family_code, series_code, region_code,
                                   buttons, button_code, rim_code)),
        ("Region", reg.name),
        ("Mounting", reg.mounting),
        ("Plate size", f"{reg.width_mm:g} x {reg.height_mm:g} mm"),
    ]
    if fam.counts:
        rows.insert(1, ("Buttons", str(buttons)))
    rows += [
        # Kept to characters the PDF base-14 fonts can encode.
        ("Button finish", f"{btn.name} ({btn.code}), {btn.kind}"
                          + (f", {btn.note.lower()}" if btn.note else "")),
        ("Rim finish", f"{rim.name} ({rim.code})"),
    ]
    return rows


def as_dict() -> Dict[str, Any]:
    """The whole catalogue, ready for the browser to build its controls from."""
    return {
        "families": [f.as_dict() for f in FAMILIES],
        "series": [s.as_dict() for s in SERIES],
        "regions": [r.as_dict() for r in REGIONS],
        "button_finishes": [f.as_dict() for f in BUTTON_FINISHES],
        "rim_finishes": [f.as_dict() for f in RIM_FINISHES],
        "backlights": [{"id": i, "name": n, "hex": h} for i, n, h in BACKLIGHTS],
        "icons": icon_lib.catalogue(),
        "icon_groups": icon_lib.GROUPS,
        "fonts": [f.as_dict() for f in ENGRAVING_FONTS],
        "text_sizes_mm": list(TEXT_SIZES_MM),
        "limits": {"max_lines": MAX_LINES, "max_chars": MAX_CHARS_PER_LINE,
                   "auto_text_mm": AUTO_TEXT_MM, "max_text_mm": MAX_TEXT_MM},
    }
