"""Colour, geometry and page-selection helpers shared across the PDF services."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from ..models import PageSelection, Rect
from ..pdfcompat import pymupdf

RGB = Tuple[float, float, float]

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def hex_to_rgb(value: Optional[str], default: RGB = (0.0, 0.0, 0.0)) -> RGB:
    """Convert ``#rrggbb`` / ``#rgb`` to a 0-1 float triple."""
    if not value:
        return default
    match = _HEX_RE.match(value.strip())
    if not match:
        return default
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return (
        int(digits[0:2], 16) / 255.0,
        int(digits[2:4], 16) / 255.0,
        int(digits[4:6], 16) / 255.0,
    )


def rgb_to_hex(rgb: Optional[Sequence[float]]) -> str:
    if not rgb:
        return "#000000"
    vals = list(rgb)[:3]
    while len(vals) < 3:
        vals.append(0.0)
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in vals)


def srgb_int_to_hex(value: Optional[int]) -> str:
    """PyMuPDF reports span colours as packed 24-bit integers."""
    if value is None:
        return "#000000"
    value = int(value) & 0xFFFFFF
    return f"#{value:06x}"


def to_fitz_rect(rect: Rect) -> "pymupdf.Rect":
    r = rect.normalised()
    return pymupdf.Rect(r.x0, r.y0, r.x1, r.y1)


def from_fitz_rect(rect) -> Rect:
    return Rect(x0=float(rect.x0), y0=float(rect.y0), x1=float(rect.x1), y1=float(rect.y1))


def clamp_rect_to_page(rect: "pymupdf.Rect", page: "pymupdf.Page") -> "pymupdf.Rect":
    bounds = page.rect
    return pymupdf.Rect(
        max(bounds.x0, min(rect.x0, bounds.x1)),
        max(bounds.y0, min(rect.y0, bounds.y1)),
        max(bounds.x0, min(rect.x1, bounds.x1)),
        max(bounds.y0, min(rect.y1, bounds.y1)),
    )


def parse_page_ranges(spec: Optional[str], total: int) -> Set[int]:
    """Parse ``'1, 3-5, 9'`` into a set of 1-indexed page numbers."""
    if not spec:
        return set()
    selected: Set[int] = set()
    for chunk in spec.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk[1:]:  # allow a leading '-' to be a typo, not a range marker
            lo_s, _, hi_s = chunk.partition("-")
            try:
                lo, hi = int(lo_s.strip()), int(hi_s.strip())
            except ValueError:
                continue
            if lo > hi:
                lo, hi = hi, lo
            selected.update(p for p in range(max(1, lo), min(total, hi) + 1))
        else:
            try:
                page = int(chunk)
            except ValueError:
                continue
            if 1 <= page <= total:
                selected.add(page)
    return selected


def resolve_pages(selection: PageSelection, total: int) -> List[int]:
    """Turn a :class:`PageSelection` into a sorted list of 1-indexed pages."""
    if total <= 0:
        return []
    mode = selection.mode
    if mode == "all":
        pages = set(range(1, total + 1))
    elif mode == "first":
        pages = {1}
    elif mode == "last":
        pages = {total}
    elif mode == "all_except_first":
        pages = set(range(2, total + 1))
    elif mode == "even":
        pages = {p for p in range(1, total + 1) if p % 2 == 0}
    elif mode == "odd":
        pages = {p for p in range(1, total + 1) if p % 2 == 1}
    elif mode == "custom":
        pages = parse_page_ranges(selection.custom, total)
        if not pages:
            pages = set(range(1, total + 1))
    else:
        pages = set(range(1, total + 1))
    return sorted(pages)


def legacy_page_selection(kind: str, custom: Optional[str], total: int) -> List[int]:
    """Page selection using the stamper's original vocabulary."""
    return resolve_pages(PageSelection(mode=kind, custom=custom), total)  # type: ignore[arg-type]


def validate_pages(pages: Iterable[int], total: int) -> List[int]:
    out = sorted({int(p) for p in pages if 1 <= int(p) <= total})
    return out


def sample_background(page: "pymupdf.Page", rect: "pymupdf.Rect") -> RGB:
    """Estimate the page background just outside ``rect``.

    Used when covering deleted or replaced content so the patch blends in
    rather than leaving a white box on a tinted page.
    """
    probe = pymupdf.Rect(
        max(page.rect.x0, rect.x0 - 6),
        max(page.rect.y0, rect.y0 - 6),
        min(page.rect.x1, rect.x1 + 6),
        min(page.rect.y1, rect.y1 + 6),
    )
    if probe.is_empty or probe.width < 1 or probe.height < 1:
        return (1.0, 1.0, 1.0)
    try:
        pix = page.get_pixmap(clip=probe, dpi=36, alpha=False)
    except Exception:  # noqa: BLE001 - fall back to white on any render issue
        return (1.0, 1.0, 1.0)
    if pix.width == 0 or pix.height == 0:
        return (1.0, 1.0, 1.0)

    # The modal colour of the border ring approximates the page background.
    counts: dict[tuple[int, int, int], int] = {}
    for x in range(pix.width):
        for y in (0, pix.height - 1):
            counts[pix.pixel(x, y)] = counts.get(pix.pixel(x, y), 0) + 1
    for y in range(pix.height):
        for x in (0, pix.width - 1):
            counts[pix.pixel(x, y)] = counts.get(pix.pixel(x, y), 0) + 1
    if not counts:
        return (1.0, 1.0, 1.0)
    best = max(counts.items(), key=lambda kv: kv[1])[0]
    return (best[0] / 255.0, best[1] / 255.0, best[2] / 255.0)


# Base-14 fonts are always embeddable without shipping font files, which keeps
# the packaged build self-contained.
BASE14 = {
    "helv": "helv", "helvetica": "helv",
    "helv-bold": "hebo", "hebo": "hebo",
    "helv-oblique": "heit", "heit": "heit",
    "helv-boldoblique": "hebi", "hebi": "hebi",
    "times": "tiro", "tiro": "tiro",
    "times-bold": "tibo", "tibo": "tibo",
    "times-italic": "tiit", "tiit": "tiit",
    "times-bolditalic": "tibi", "tibi": "tibi",
    "courier": "cour", "cour": "cour",
    "courier-bold": "cobo", "cobo": "cobo",
    "symbol": "symb", "symb": "symb",
    "zapf": "zadb", "zadb": "zadb",
}


def resolve_font(name: Optional[str], bold: bool = False, italic: bool = False) -> str:
    """Map an arbitrary PDF font name onto the closest base-14 font."""
    if name:
        key = name.strip().lower()
        if key in BASE14:
            return BASE14[key]
        lowered = key
        bold = bold or "bold" in lowered or "black" in lowered or "heavy" in lowered
        italic = italic or "italic" in lowered or "oblique" in lowered
        if "courier" in lowered or "mono" in lowered:
            family = "co"
        elif "times" in lowered or "serif" in lowered and "sans" not in lowered:
            family = "ti"
        else:
            family = "he"
    else:
        family = "he"

    if family == "he":
        if bold and italic:
            return "hebi"
        if bold:
            return "hebo"
        if italic:
            return "heit"
        return "helv"
    if family == "ti":
        if bold and italic:
            return "tibi"
        if bold:
            return "tibo"
        if italic:
            return "tiit"
        return "tiro"
    if bold and italic:
        return "cobi"
    if bold:
        return "cobo"
    if italic:
        return "coit"
    return "cour"


def fit_font_size(
    text: str, rect: "pymupdf.Rect", size: float, fontname: str = "helv", min_size: float = 4.0
) -> float:
    """Shrink ``size`` until ``text`` fits the width of ``rect``."""
    if not text:
        return size
    font = pymupdf.Font(fontname)
    longest = max(text.split("\n"), key=len) if "\n" in text else text
    width = font.text_length(longest, fontsize=size)
    if width <= rect.width or width <= 0:
        return size
    scaled = size * (rect.width / width) * 0.98
    return max(min_size, scaled)
