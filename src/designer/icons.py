"""Vector icon library for Antumbra button engraving.

Every icon is a plain list of primitive shapes inside a 100x100 box, so one
definition drives both the browser SVG preview and the PyMuPDF drawing in the
exported spec sheet. Curves are tessellated into polylines here rather than
emitted as arcs, which keeps both renderers down to four primitives:

``["line", x1, y1, x2, y2]``      a stroked segment
``["poly", [x, y, ...], closed]`` a stroked polyline
``["fpoly", [x, y, ...]]``        a filled polygon
``["circle", cx, cy, r]``         a stroked circle
``["disc", cx, cy, r]``           a filled circle

The laser engraver only ever cuts a single stroke weight, so nothing here
carries per-shape styling — the renderers pick one width and colour.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

Shape = List[Any]


# --------------------------------------------------------------- primitives

def _pt(x: float, y: float) -> Tuple[float, float]:
    return (round(x, 2), round(y, 2))


def _arc_points(cx: float, cy: float, r: float, a0: float, a1: float,
                steps: int = 22) -> List[float]:
    """Tessellate an arc. Angles are degrees, y grows downward as in SVG."""
    out: List[float] = []
    for i in range(steps + 1):
        angle = math.radians(a0 + (a1 - a0) * i / steps)
        out.extend(_pt(cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return out


def _arc(cx: float, cy: float, r: float, a0: float, a1: float,
         steps: int = 22, closed: bool = False) -> Shape:
    return ["poly", _arc_points(cx, cy, r, a0, a1, steps), closed]


def _rect(x: float, y: float, w: float, h: float) -> Shape:
    return ["poly", [x, y, x + w, y, x + w, y + h, x, y + h], True]


def _arrow(cx: float, cy: float, size: float, direction: str) -> List[Shape]:
    """A stem with a solid head, pointing one of four ways."""
    half = size / 2
    head = size * 0.42
    if direction in ("up", "down"):
        sign = -1 if direction == "up" else 1
        tip = cy + sign * half
        base = tip - sign * head
        return [
            ["line", cx, cy - sign * half, cx, base],
            ["fpoly", [cx, tip, cx - head * 0.62, base, cx + head * 0.62, base]],
        ]
    sign = -1 if direction == "left" else 1
    tip = cx + sign * half
    base = tip - sign * head
    return [
        ["line", cx - sign * half, cy, base, cy],
        ["fpoly", [tip, cy, base, cy - head * 0.62, base, cy + head * 0.62]],
    ]


def _rays(cx: float, cy: float, inner: float, outer: float, count: int = 8,
          offset: float = 0.0) -> List[Shape]:
    out: List[Shape] = []
    for i in range(count):
        angle = math.radians(offset + i * 360 / count)
        dx, dy = math.cos(angle), math.sin(angle)
        out.append(["line", *_pt(cx + inner * dx, cy + inner * dy),
                    *_pt(cx + outer * dx, cy + outer * dy)])
    return out


def _star(cx: float, cy: float, outer: float, inner: float,
          points: int = 5) -> Shape:
    coords: List[float] = []
    for i in range(points * 2):
        radius = outer if i % 2 == 0 else inner
        angle = math.radians(-90 + i * 180 / points)
        coords.extend(_pt(cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return ["fpoly", coords]


def _blade(cx: float, cy: float, angle_deg: float, length: float,
           width: float) -> Shape:
    """One fan blade: a rounded lobe pointing away from the hub."""
    coords: List[float] = []
    base = math.radians(angle_deg)
    for i in range(15):
        t = i / 14
        along = length * t
        span = width * math.sin(math.pi * t) * 0.5
        px = cx + along * math.cos(base) - span * math.sin(base)
        py = cy + along * math.sin(base) + span * math.cos(base)
        coords.extend(_pt(px, py))
    for i in range(14, -1, -1):
        t = i / 14
        along = length * t
        span = width * math.sin(math.pi * t) * 0.5
        px = cx + along * math.cos(base) + span * math.sin(base)
        py = cy + along * math.sin(base) - span * math.cos(base)
        coords.extend(_pt(px, py))
    return ["fpoly", coords]


# -------------------------------------------------------------- definitions
# Keys are stable — they end up in saved designs and exported schedules.

_DEFS: List[Tuple[str, str, str, List[Shape]]] = [
    # ---- lighting -------------------------------------------------------
    ("power", "Power", "lighting", [
        _arc(50, 52, 30, -60, 240),
        ["line", 50, 16, 50, 48],
    ]),
    ("all-off", "All off", "lighting", [
        _arc(50, 52, 30, -60, 240),
        ["line", 50, 16, 50, 48],
        ["line", 22, 24, 78, 80],
    ]),
    ("bulb", "Light", "lighting", [
        _arc(50, 44, 24, 150, 390),
        ["line", 33, 61, 40, 72],
        ["line", 67, 61, 60, 72],
        ["line", 40, 72, 60, 72],
        ["line", 41, 80, 59, 80],
        ["line", 44, 88, 56, 88],
    ]),
    ("downlight", "Downlight", "lighting", [
        ["fpoly", [34, 22, 66, 22, 74, 40, 26, 40]],
        ["line", 50, 48, 50, 62],
        ["line", 32, 50, 26, 64],
        ["line", 68, 50, 74, 64],
        ["line", 40, 72, 60, 72],
    ]),
    ("pendant", "Pendant", "lighting", [
        ["line", 50, 12, 50, 34],
        ["fpoly", [50, 34, 76, 66, 24, 66]],
        ["line", 30, 78, 70, 78],
    ]),
    ("wall-light", "Wall light", "lighting", [
        ["line", 20, 14, 20, 86],
        ["fpoly", [26, 36, 26, 64, 62, 76, 62, 24]],
        _arc(70, 50, 22, -70, 70),
    ]),
    ("lamp", "Table lamp", "lighting", [
        ["fpoly", [36, 20, 64, 20, 74, 48, 26, 48]],
        ["line", 50, 48, 50, 76],
        ["line", 32, 82, 68, 82],
        ["line", 32, 82, 38, 76],
        ["line", 68, 82, 62, 76],
    ]),
    ("bright-up", "Brighter", "lighting", [
        ["disc", 50, 50, 15],
        *_rays(50, 50, 22, 34),
        *_arrow(50, 50, 0, "up"),
    ]),
    ("bright-down", "Dimmer", "lighting", [
        ["circle", 50, 50, 15],
        *_rays(50, 50, 22, 32, count=8),
    ]),
    ("sun", "Day", "lighting", [
        ["circle", 50, 50, 18],
        *_rays(50, 50, 26, 38),
    ]),
    ("moon", "Night", "lighting", [
        _arc(50, 50, 30, 55, 305),
        _arc(66, 50, 30, 128, 232),
    ]),

    # ---- scenes ---------------------------------------------------------
    ("scene", "Scene", "scenes", [_star(50, 52, 34, 14)]),
    ("scene-2", "Scene 2", "scenes", [
        _star(38, 42, 24, 10),
        _star(68, 70, 16, 7),
    ]),
    ("welcome", "Welcome", "scenes", [
        _rect(28, 18, 44, 68),
        ["disc", 62, 54, 4],
        ["line", 22, 86, 78, 86],
    ]),
    ("dine", "Dining", "scenes", [
        ["line", 32, 16, 32, 84],
        ["line", 24, 16, 24, 40],
        ["line", 40, 16, 40, 40],
        _arc(32, 40, 8, 0, 180),
        _arc(70, 34, 14, 120, 420),
        ["line", 70, 48, 70, 84],
    ]),
    ("relax", "Relax", "scenes", [
        _arc(50, 62, 26, 180, 360),
        ["line", 24, 62, 76, 62],
        ["line", 36, 30, 40, 20],
        ["line", 50, 30, 54, 20],
        ["line", 64, 30, 68, 20],
    ]),

    # ---- shades ---------------------------------------------------------
    ("blind-up", "Blind up", "shades", [
        _rect(22, 18, 56, 64),
        ["line", 22, 34, 78, 34],
        ["line", 22, 46, 78, 46],
        *_arrow(50, 64, 26, "up"),
    ]),
    ("blind-down", "Blind down", "shades", [
        _rect(22, 18, 56, 64),
        ["line", 22, 34, 78, 34],
        *_arrow(50, 60, 26, "down"),
    ]),
    ("blind-stop", "Stop", "shades", [
        _rect(22, 18, 56, 64),
        ["line", 22, 34, 78, 34],
        ["fpoly", [40, 54, 60, 54, 60, 74, 40, 74]],
    ]),
    ("curtain-open", "Open", "shades", [
        ["line", 18, 16, 82, 16],
        ["fpoly", [22, 20, 38, 20, 34, 84, 22, 84]],
        ["fpoly", [78, 20, 62, 20, 66, 84, 78, 84]],
        *_arrow(43, 52, 16, "left"),
        *_arrow(57, 52, 16, "right"),
    ]),
    ("curtain-close", "Close", "shades", [
        ["line", 18, 16, 82, 16],
        ["fpoly", [22, 20, 46, 20, 44, 84, 22, 84]],
        ["fpoly", [78, 20, 54, 20, 56, 84, 78, 84]],
    ]),

    # ---- climate --------------------------------------------------------
    ("fan", "Fan", "climate", [
        _blade(50, 50, -90, 34, 26),
        _blade(50, 50, 30, 34, 26),
        _blade(50, 50, 150, 34, 26),
        ["disc", 50, 50, 7],
    ]),
    ("snowflake", "Cooling", "climate", [
        *_rays(50, 50, 0, 34, count=6, offset=-90),
        ["line", 50, 24, 43, 32], ["line", 50, 24, 57, 32],
        ["line", 50, 76, 43, 68], ["line", 50, 76, 57, 68],
        ["line", 27, 37, 27, 46], ["line", 73, 63, 73, 54],
        ["line", 27, 63, 27, 54], ["line", 73, 37, 73, 46],
    ]),
    ("heat", "Heating", "climate", [
        ["poly", [36, 84, 36, 30], False],
        _arc(36, 26, 10, 90, 450),
        ["disc", 36, 78, 10],
        ["line", 62, 22, 62, 44],
        ["line", 74, 22, 74, 44],
    ]),

    # ---- hotel ----------------------------------------------------------
    ("dnd", "Do not disturb", "hotel", [
        ["circle", 50, 50, 32],
        ["line", 28, 28, 72, 72],
    ]),
    ("mur", "Make up room", "hotel", [
        ["fpoly", [30, 62, 52, 40, 62, 50, 40, 72]],
        ["line", 40, 72, 30, 62],
        ["line", 58, 34, 74, 18],
        ["line", 24, 84, 56, 84],
    ]),
    ("bed", "Bedroom", "hotel", [
        ["line", 18, 34, 18, 78],
        ["line", 18, 68, 82, 68],
        ["line", 82, 68, 82, 50],
        _arc(30, 50, 9, 180, 360),
        ["fpoly", [42, 50, 82, 50, 82, 58, 42, 58]],
        ["line", 18, 78, 18, 84],
        ["line", 82, 78, 82, 84],
    ]),
    ("bell", "Service", "hotel", [
        _arc(50, 66, 30, 180, 360),
        ["line", 18, 66, 82, 66],
        ["line", 50, 36, 50, 26],
        ["disc", 50, 22, 6],
        ["line", 20, 78, 80, 78],
    ]),
    ("door", "Door", "hotel", [
        _rect(30, 14, 40, 72),
        ["disc", 62, 52, 4],
        ["line", 20, 86, 80, 86],
    ]),

    # ---- media ----------------------------------------------------------
    ("tv", "Screen", "media", [
        _rect(18, 22, 64, 44),
        ["line", 38, 78, 62, 78],
        ["line", 50, 66, 50, 78],
    ]),
    ("audio", "Audio", "media", [
        ["fpoly", [22, 40, 36, 40, 52, 24, 52, 76, 36, 60, 22, 60]],
        _arc(58, 50, 14, -55, 55),
        _arc(58, 50, 24, -55, 55),
    ]),
    ("mute", "Mute", "media", [
        ["fpoly", [22, 40, 36, 40, 52, 24, 52, 76, 36, 60, 22, 60]],
        ["line", 62, 38, 84, 62],
        ["line", 84, 38, 62, 62],
    ]),

    # ---- arrows and marks -----------------------------------------------
    ("arrow-up", "Up", "arrows", _arrow(50, 50, 56, "up")),
    ("arrow-down", "Down", "arrows", _arrow(50, 50, 56, "down")),
    ("arrow-left", "Left", "arrows", _arrow(50, 50, 56, "left")),
    ("arrow-right", "Right", "arrows", _arrow(50, 50, 56, "right")),
    ("plus", "Plus", "arrows", [
        ["line", 50, 24, 50, 76],
        ["line", 24, 50, 76, 50],
    ]),
    ("minus", "Minus", "arrows", [["line", 24, 50, 76, 50]]),
    ("dot", "Dot", "arrows", [["disc", 50, 50, 12]]),
    ("lock", "Lock", "arrows", [
        _rect(28, 46, 44, 36),
        _arc(50, 46, 15, 180, 360),
    ]),
]

ICONS: Dict[str, Dict[str, Any]] = {
    key: {"id": key, "name": name, "group": group, "shapes": shapes}
    for key, name, group, shapes in _DEFS
}

GROUPS: List[str] = []
for _key, _name, _group, _shapes in _DEFS:
    if _group not in GROUPS:
        GROUPS.append(_group)


def get(icon_id: str) -> Dict[str, Any] | None:
    return ICONS.get(icon_id)


def catalogue() -> List[Dict[str, Any]]:
    """Icons in definition order, ready to ship to the browser."""
    return [ICONS[key] for key, _n, _g, _s in _DEFS]
