"""Structured purchase-order extraction.

The hard parts of a real PO are geometric, not textual:

* Extraction order is unreliable — item codes routinely appear detached from
  their row — so every decision here is made from coordinates.
* Column values are a mix of left- and right-aligned, so column boundaries are
  derived from *whitespace gaps in the body*, seeded by the header positions,
  rather than from header x-positions alone.
* Headers wrap ("DISC" / "%", "COD" / "E") and descriptions wrap across
  continuation lines that carry no numbers.
* Header fields appear either to the right of their label or directly beneath
  it, and neighbouring address blocks are easy to mistake for values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ..models import (
    ColumnMapping,
    POHeader,
    POLineItem,
    POTemplate,
    PurchaseOrderResult,
    Rect,
)
from .textgrid import Line, PageGrid, Word

# --------------------------------------------------------------------------
# Column vocabulary
# --------------------------------------------------------------------------

# Canonical field -> phrases that identify its column header. Longer phrases
# win, so "unit price" beats a bare "unit".
FIELD_KEYWORDS: Dict[str, List[str]] = {
    "quantity": ["qty ordered", "order qty", "quantity", "qty", "units ordered"],
    "part_code": [
        "my item no", "item no", "item number", "item code", "part no", "part number",
        "product code", "stock code", "catalogue no", "cat no", "sku", "model no",
        "our part", "your part", "item",
    ],
    "description": [
        "item description", "description", "particulars", "details", "goods",
        "product description",
    ],
    "unit_price": ["unit price", "unit cost", "price each", "price", "rate", "each", "cost"],
    "total_price": [
        "extended price", "extended", "line total", "amount aud", "amount", "net amount",
        "total price", "value", "total",
    ],
    "unit": ["uom", "unit of measure", "pack size", "unit", "pack"],
    "discount": ["disc %", "discount", "disc"],
    "tax": ["tax rate", "gst", "vat", "tax"],
    "due_date": ["date required", "delivery date", "due date", "required by", "due"],
    "line_no": ["line no", "line", "item #", "no."],
}

# Fields that carry a number and therefore signal "this line starts a new item".
NUMERIC_ANCHORS = ("quantity", "unit_price", "total_price")

_TOTALS_RE = re.compile(
    r"^\s*(sub\s*-?\s*total|total|grand\s+total|balance\s+due|amount\s+due|includes?\b|"
    r"gst\b|vat\b|tax\b|freight|shipping|delivery\s+charge|paid\s+today|sale\s+amt|"
    r"net\s+total|order\s+total|page\s+\d+\s+of\s+\d+)",
    re.IGNORECASE,
)

_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-/_.]{1,}$")
_NUMBERISH_RE = re.compile(r"\d")
_MONEY_RE = re.compile(r"^[\$€£]?\s*-?[\d,]+(?:\.\d+)?%?$")


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9%# ]+", " ", text.lower()).strip()


def match_field(header_text: str) -> Tuple[Optional[str], int]:
    """Map a header cell onto a canonical field, with a confidence score."""
    norm = _normalise(header_text)
    norm = re.sub(r"\s+", " ", norm)
    if not norm:
        return None, 0

    best_field: Optional[str] = None
    best_score = 0
    for field, keywords in FIELD_KEYWORDS.items():
        for keyword in keywords:
            if norm == keyword:
                score = 100 + len(keyword)
            elif re.search(rf"\b{re.escape(keyword)}\b", norm):
                score = 50 + len(keyword)
            elif keyword.startswith(norm) and len(norm) >= 3:
                score = 20 + len(norm)
            else:
                continue
            if score > best_score:
                best_score, best_field = score, field
    return best_field, best_score


# --------------------------------------------------------------------------
# Header row detection
# --------------------------------------------------------------------------

@dataclass
class HeaderCell:
    text: str
    x0: float
    x1: float
    field: Optional[str] = None
    score: int = 0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0


def split_cells(line: Line, median_height: float) -> List[HeaderCell]:
    """Group a line's words into cells, splitting on horizontal gaps."""
    if not line.words:
        return []
    threshold = max(4.0, median_height * 0.6)
    cells: List[HeaderCell] = []
    current: List[Word] = [line.words[0]]

    for prev, word in zip(line.words, line.words[1:]):
        if (word.x0 - prev.x1) > threshold:
            cells.append(_cell_from(current))
            current = [word]
        else:
            current.append(word)
    cells.append(_cell_from(current))
    return cells


def _cell_from(words: Sequence[Word]) -> HeaderCell:
    return HeaderCell(
        text=" ".join(w.text for w in words).strip(),
        x0=min(w.x0 for w in words),
        x1=max(w.x1 for w in words),
    )


def merge_wrapped_header(cells: List[HeaderCell], follower: Line) -> List[HeaderCell]:
    """Fold a second header line ("%" under "DISC") into the cells above it."""
    for word in follower.words:
        target = None
        for cell in cells:
            if cell.x0 - 2.0 <= word.cx <= cell.x1 + 2.0:
                target = cell
                break
        if target is None:
            continue
        token = word.text.strip()
        if not token:
            continue
        # A short alphabetic fragment continues a broken word ("COD" + "E");
        # anything else is a separate token ("DISC" + "%").
        if token.isalpha() and len(token) <= 2 and target.text.isalpha():
            target.text += token
        else:
            target.text = f"{target.text} {token}".strip()
        target.x1 = max(target.x1, word.x1)
    return cells


def find_header_row(grid: PageGrid) -> Optional[Tuple[int, List[HeaderCell]]]:
    """Locate the line-items table header. Returns ``(line_index, cells)``."""
    median = grid.median_line_height
    best: Optional[Tuple[int, List[HeaderCell], int]] = None

    for idx, line in enumerate(grid.lines):
        cells = split_cells(line, median)
        if len(cells) < 2:
            continue

        # Try the line on its own, and merged with the following line.
        candidates = [cells]
        if idx + 1 < len(grid.lines):
            follower = grid.lines[idx + 1]
            if 0 < (follower.cy - line.cy) <= median * 2.2:
                merged = merge_wrapped_header(
                    [HeaderCell(c.text, c.x0, c.x1) for c in cells], follower
                )
                candidates.append(merged)

        for candidate in candidates:
            matched = 0
            score = 0
            seen: set[str] = set()
            for cell in candidate:
                field, cell_score = match_field(cell.text)
                cell.field, cell.score = field, cell_score
                if field and cell_score >= 20:
                    matched += 1
                    score += cell_score
                    seen.add(field)
            # A believable header names a description or code column *and*
            # at least one numeric column.
            has_identity = bool(seen & {"description", "part_code"})
            has_numeric = bool(seen & set(NUMERIC_ANCHORS))
            if matched >= 2 and has_identity and has_numeric:
                if best is None or score > best[2]:
                    best = (idx, candidate, score)

    if best is None:
        return None
    return best[0], _resolve_duplicates(best[1])


def _resolve_duplicates(cells: List[HeaderCell]) -> List[HeaderCell]:
    """Keep the best cell per field; demote the losers to extra columns."""
    winners: Dict[str, HeaderCell] = {}
    for cell in cells:
        if not cell.field:
            continue
        current = winners.get(cell.field)
        if current is None or cell.score > current.score:
            if current is not None:
                current.field = None
            winners[cell.field] = cell
        else:
            cell.field = None

    # If nothing claimed the part-code column, an unmapped "code"-ish column is
    # very likely it.
    if "part_code" not in winners:
        for cell in cells:
            if cell.field is None and re.search(r"\b(code|item|part)\b", cell.text, re.I):
                cell.field = "part_code"
                break
    return cells


# --------------------------------------------------------------------------
# Column boundaries
# --------------------------------------------------------------------------

def _coverage_gaps(lines: Sequence[Line], min_gap: float = 4.0) -> List[Tuple[float, float]]:
    """Whitespace columns: x-ranges no word in the body occupies."""
    spans: List[Tuple[float, float]] = []
    for line in lines:
        for word in line.words:
            spans.append((word.x0, word.x1))
    if not spans:
        return []
    spans.sort()

    merged: List[List[float]] = [list(spans[0])]
    for x0, x1 in spans[1:]:
        if x0 <= merged[-1][1] + 0.5:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])

    gaps: List[Tuple[float, float]] = []
    for left, right in zip(merged, merged[1:]):
        if (right[0] - left[1]) >= min_gap:
            gaps.append((left[1], right[0]))
    return gaps


def compute_boundaries(
    cells: Sequence[HeaderCell], body_lines: Sequence[Line]
) -> List[float]:
    """Boundaries between columns, snapped to real whitespace in the body.

    Header midpoints alone misplace wide left-aligned description columns —
    a long description overruns the midpoint and lands in the next column — so
    each proposed boundary is moved into the nearest genuine body gap.
    """
    gaps = _coverage_gaps(body_lines)
    boundaries: List[float] = []

    for left, right in zip(cells, cells[1:]):
        proposed = (left.x1 + right.x0) / 2.0
        inside = next((g for g in gaps if g[0] <= proposed <= g[1]), None)
        if inside is not None:
            boundaries.append(proposed)
            continue
        # Snap to the nearest gap that sits between the two header cells.
        between = [g for g in gaps if g[1] > left.x0 and g[0] < right.x1]
        if between:
            nearest = min(between, key=lambda g: abs(((g[0] + g[1]) / 2.0) - proposed))
            boundaries.append((nearest[0] + nearest[1]) / 2.0)
        else:
            boundaries.append(proposed)

    # Boundaries must stay strictly increasing.
    for i in range(1, len(boundaries)):
        if boundaries[i] <= boundaries[i - 1]:
            boundaries[i] = boundaries[i - 1] + 0.5
    return boundaries


def column_bands(
    cells: Sequence[HeaderCell], boundaries: Sequence[float]
) -> List[Tuple[float, float]]:
    edges = [-1e6, *boundaries, 1e6]
    return [(edges[i], edges[i + 1]) for i in range(len(cells))]


# --------------------------------------------------------------------------
# Body rows
# --------------------------------------------------------------------------

def _is_totals_line(line: Line, description_band: Optional[Tuple[float, float]]) -> bool:
    text = line.text.strip()
    if not _TOTALS_RE.match(text):
        return False
    # A totals row sits to the right of the description column; a product
    # genuinely called "Total station" starts at the left margin.
    if description_band is not None:
        left = min(w.x0 for w in line.words)
        if left <= description_band[1]:
            # Allow it anyway when the line has no other content.
            return len(line.words) <= 4
    return True


def collect_body_lines(
    grid: PageGrid, header_index: int, header_cells: Sequence[HeaderCell]
) -> List[Line]:
    """Lines belonging to the table body, stopping at totals or a big gap.

    ``header_index`` of -1 means "start at the top of the page" — used when a
    table continues onto a page that does not repeat its header.
    """
    if not grid.lines:
        return []
    median = grid.median_line_height
    header_line = grid.lines[header_index] if header_index >= 0 else grid.lines[0]
    description_band: Optional[Tuple[float, float]] = None
    for cell in header_cells:
        if cell.field == "description":
            description_band = (cell.x0, cell.x1)
            break

    body: List[Line] = []
    previous_cy = header_line.cy
    for line in grid.lines[header_index + 1:]:
        if not line.words:
            continue
        # A large vertical jump means the table has ended.
        if body and (line.cy - previous_cy) > median * 4.5:
            break
        if _is_totals_line(line, description_band):
            break
        body.append(line)
        previous_cy = line.cy
    return body


def _cell_values(
    line: Line, cells: Sequence[HeaderCell], bands: Sequence[Tuple[float, float]]
) -> Dict[int, str]:
    return {
        idx: line.text_between(band[0], band[1])
        for idx, band in enumerate(bands)
    }


def _looks_numeric(value: str) -> bool:
    value = value.strip()
    return bool(value) and bool(_NUMBERISH_RE.search(value))


def split_code_from_description(description: str) -> Tuple[Optional[str], str]:
    """Pull a part code out of a description cell.

    Handles the two shapes seen in the wild: a leading code token
    (``PD-PCN DyNet PC Node…``) and a trailing parenthesised code
    (``…Sensor - RECESSED (DUS360-CR)``).
    """
    text = description.strip()
    if not text:
        return None, text

    tokens = text.split()
    first = tokens[0].strip(",;")
    if _CODE_RE.match(first) and first.upper() == first:
        is_whole = len(tokens) == 1
        has_marker = bool(re.search(r"[-/_.]", first) or _NUMBERISH_RE.search(first))
        if is_whole or (has_marker and len(first) >= 3):
            remainder = " ".join(tokens[1:]).strip()
            return first, (remainder or text)

    trailing = re.search(r"\(([A-Z0-9][A-Z0-9\-/_.]{2,})\)\s*$", text)
    if trailing:
        return trailing.group(1), text

    return None, text


def parse_line_items(
    grid: PageGrid,
    cells: Sequence[HeaderCell],
    bands: Sequence[Tuple[float, float]],
    body: Sequence[Line],
) -> List[POLineItem]:
    """Fold body lines into items, attaching continuation lines to their row."""
    field_by_index = {idx: cell.field for idx, cell in enumerate(cells)}
    label_by_index = {idx: cell.text for idx, cell in enumerate(cells)}
    desc_index = next((i for i, f in field_by_index.items() if f == "description"), None)
    anchor_indices = [i for i, f in field_by_index.items() if f in NUMERIC_ANCHORS]
    has_code_column = any(f == "part_code" for f in field_by_index.values())

    items: List[POLineItem] = []
    for line in body:
        values = _cell_values(line, cells, bands)
        starts_item = any(_looks_numeric(values.get(i, "")) for i in anchor_indices)

        if not starts_item and items and desc_index is not None:
            # Continuation: append to the previous item's description.
            extra_text = values.get(desc_index, "").strip()
            if extra_text:
                previous = items[-1]
                previous.description = (
                    f"{previous.description} {extra_text}".strip()
                    if previous.description else extra_text
                )
            continue

        if not starts_item:
            continue

        item = POLineItem(page=grid.page_number, rect=Rect(
            x0=line.x0, y0=line.y0, x1=line.x1, y1=line.y1
        ))
        for idx, field in field_by_index.items():
            value = values.get(idx, "").strip()
            if not value:
                continue
            if field is None:
                key = _normalise(label_by_index.get(idx, f"column{idx}")) or f"column{idx}"
                item.extra[key] = value
            elif field == "line_no":
                digits = re.sub(r"\D", "", value)
                item.line_no = int(digits) if digits else None
            else:
                setattr(item, field, value)

        if not has_code_column and item.description:
            code, description = split_code_from_description(item.description)
            if code:
                item.part_code = code
                item.description = description

        items.append(item)

    return items


# --------------------------------------------------------------------------
# Header fields
# --------------------------------------------------------------------------

HEADER_PATTERNS: Dict[str, List[str]] = {
    "po_number": [
        r"purchase\s*order\s*(?:number|no\.?|#)", r"purchase\s*no\.?",
        r"\bp\.?\s?o\.?\s*(?:number|no\.?|#)", r"^order\s*(?:number|no\.?|#)",
        r"^order\s*#", r"requisition\s*(?:number|no\.?)",
    ],
    "order_date": [
        r"purchase\s*order\s*date", r"^order\s*date", r"date\s*of\s*order",
        r"^date$", r"^dated$", r"issue\s*date",
    ],
    "required_date": [
        r"delivery\s*date", r"date\s*required", r"required\s*(?:by|date)",
        r"due\s*date", r"ship\s*date", r"expected",
    ],
    "reference": [r"^reference$", r"^ref\.?$", r"your\s*ref", r"job\s*(?:no|number|ref)",
                  r"our\s*ref"],
    "buyer": [r"ordered\s*by", r"^buyer$", r"raised\s*by", r"contact"],
    "total": [r"total\s*am(?:oun)?t", r"^total$", r"order\s*total", r"grand\s*total",
              r"balance\s*due"],
    "subtotal": [r"sub\s*-?\s*total", r"sale\s*amt", r"net\s*total", r"goods\s*total"],
    "tax": [r"^gst$", r"^vat$", r"^tax$", r"gst\s*am(?:oun)?t", r"includes?\s*gst"],
}

_LABELISH = re.compile(
    "|".join(p for patterns in HEADER_PATTERNS.values() for p in patterns), re.IGNORECASE
)


MONEY_FIELDS = {"total", "subtotal", "tax"}


def _left_aligned_blocks(
    grid: PageGrid,
    near_y: Optional[float] = None,
    window: float = 120.0,
    min_words: int = 3,
) -> List[float]:
    """x-positions that start a stacked text block (an address, a label column).

    Computed from *words*, not lines: in two-column layouts the two columns sit
    at slightly different baselines, so line grouping fuses them and the line's
    x0 no longer reveals either column. Restricting to a vertical window around
    the label keeps a value that happens to share an x with a distant totals
    figure from being mistaken for a block.
    """
    counts: Dict[int, int] = {}
    for word in grid.words:
        if near_y is not None and abs(word.cy - near_y) > window:
            continue
        key = int(round(word.x0))
        counts[key] = counts.get(key, 0) + 1
    return [float(x) for x, count in counts.items() if count >= min_words]


def _money_right(label_words: Sequence[Word], line: Line) -> Optional[str]:
    """Last money-looking token to the right of the label on the same line."""
    label_end = max(w.x1 for w in label_words)
    tokens = [
        w.text for w in line.words
        if w.x0 > label_end + 0.5 and _MONEY_RE.match(w.text.strip())
    ]
    return tokens[-1] if tokens else None


def _value_below(
    grid: PageGrid, label_line: Line, label_x0: float, median: float
) -> Optional[str]:
    for line in grid.lines:
        if line.cy <= label_line.cy + median * 0.4:
            continue
        if line.cy > label_line.cy + median * 2.6:
            break
        candidate_words = [w for w in line.words if abs(w.x0 - label_x0) <= 6.0]
        if not candidate_words:
            continue
        # Take the whole run starting at that x, stopping at a wide gap.
        start = min(candidate_words, key=lambda w: w.x0)
        run: List[Word] = []
        previous: Optional[Word] = None
        for word in line.words:
            if word.x0 < start.x0 - 0.5:
                continue
            if previous is not None and (word.x0 - previous.x1) > median * 3.0:
                break
            run.append(word)
            previous = word
        text = " ".join(w.text for w in run).strip()
        if text and not _LABELISH.search(text):
            return text
        return None
    return None


def _value_right(
    label_words: Sequence[Word],
    label_line: Line,
    median: float,
    block_xs: Sequence[float],
) -> Optional[str]:
    label_end = max(w.x1 for w in label_words)
    label_start = min(w.x0 for w in label_words)
    label_cy = sum(w.cy for w in label_words) / len(label_words)
    label_block = min(
        (bx for bx in block_xs if abs(bx - label_start) <= 6.0), default=None
    )

    run: List[Word] = []
    previous: Optional[Word] = None
    for word in label_line.words:
        if word.x0 <= label_end + 0.5:
            continue
        # Words from a neighbouring column share a *line* but not a baseline.
        if abs(word.cy - label_cy) > median * 0.4:
            break
        # A word that begins a different stacked block is another column.
        if any(abs(word.x0 - bx) <= 3.0 for bx in block_xs if bx != label_block):
            break
        if previous is None:
            if (word.x0 - label_end) > 140:
                break
        elif (word.x0 - previous.x1) > median * 4.0:
            break
        run.append(word)
        previous = word

    text = " ".join(w.text for w in run).strip()
    if not text or _LABELISH.search(text):
        return None
    return text


def extract_header_fields(
    grid: PageGrid, exclude_lines: Optional[set[int]] = None
) -> Dict[str, str]:
    """Find label/value pairs, handling both value-right and value-below.

    ``exclude_lines`` holds the indices of the line-item table, so a column
    header such as "GST" is never mistaken for a document-level label.
    """
    median = grid.median_line_height
    skip = exclude_lines or set()
    found: Dict[str, str] = {}

    for field, patterns in HEADER_PATTERNS.items():
        for pattern in patterns:
            if field in found:
                break
            regex = re.compile(pattern, re.IGNORECASE)
            for line_index, line in enumerate(grid.lines):
                if line_index in skip:
                    continue
                # Match progressively longer word runs so multi-word labels
                # ("Purchase Order Number") are recognised as one unit.
                for start in range(len(line.words)):
                    matched = False
                    # Shortest match first: a longer run would swallow the
                    # value into the label ("Purchase No:" + "00111797").
                    for end in range(start + 1, min(start + 5, len(line.words)) + 1):
                        chunk = line.words[start:end]
                        text = " ".join(w.text for w in chunk).strip()
                        # Anchored: the label must *begin* the chunk, otherwise
                        # chunk[0] is some neighbouring column's word and the
                        # value lookup anchors to the wrong x-position.
                        if not regex.match(text):
                            continue
                        matched = True
                        has_colon = text.rstrip().endswith(":")
                        label_cy = sum(w.cy for w in chunk) / len(chunk)
                        block_xs = _left_aligned_blocks(grid, near_y=label_cy)

                        if field in MONEY_FIELDS:
                            value = _money_right(chunk, line)
                            if not value:
                                below = _value_below(grid, line, chunk[0].x0, median)
                                value = below if below and _MONEY_RE.match(below) else None
                        else:
                            below = _value_below(grid, line, chunk[0].x0, median)
                            right = _value_right(chunk, line, median, block_xs)
                            value = (right or below) if has_colon else (below or right)

                        if value:
                            found[field] = value.strip(" :\t")
                        break
                    if matched or field in found:
                        break
                if field in found:
                    break
    return found


COMPANY_RE = re.compile(
    r"\b(pty|ltd|limited|inc\.?|llc|gmbh|corp|corporation|company|co\.|systems|"
    r"solutions|services|electrical|controls?|group|holdings|enterprises)\b",
    re.IGNORECASE,
)


DOC_TITLE_RE = re.compile(
    r"^\s*(purchase\s*order|order\s*form|tax\s*invoice|invoice|quotation|quote|"
    r"delivery\s*docket|remittance|statement|credit\s*note|packing\s*slip|order)\s*$",
    re.IGNORECASE,
)

LEGAL_SUFFIX_RE = re.compile(
    r"^\(?(pty|ltd|limited|inc\.?|llc|gmbh|corp\.?|corporation|p/l|pty\.?\s*ltd\.?)\b",
    re.IGNORECASE,
)


@dataclass
class TextBlock:
    """A stack of words sharing a left edge — an address or a label column."""

    x0: float
    rows: List[Tuple[float, str]]  # (y, text) in reading order

    @property
    def top(self) -> float:
        return self.rows[0][0] if self.rows else 0.0

    def texts(self) -> List[str]:
        return [text for _y, text in self.rows]


def _word_blocks(grid: PageGrid, min_rows: int = 2) -> List[TextBlock]:
    """Group words into left-aligned stacks, then into rows within each stack.

    Works on words rather than lines because interleaved two-column layouts
    defeat line grouping.
    """
    by_x: Dict[int, List[Word]] = {}
    for word in grid.words:
        by_x.setdefault(int(round(word.x0)), []).append(word)

    median = grid.median_line_height
    blocks: List[TextBlock] = []
    for x0, starters in by_x.items():
        if len(starters) < min_rows:
            continue
        rows: List[Tuple[float, str]] = []
        for starter in sorted(starters, key=lambda w: w.y0):
            # Collect the rest of that row, stopping at a wide gap.
            run: List[Word] = [starter]
            previous = starter
            for word in grid.words:
                if word is starter or abs(word.cy - starter.cy) > median * 0.45:
                    continue
                if word.x0 <= previous.x1 + 0.5:
                    continue
                if (word.x0 - previous.x1) > median * 3.0:
                    continue
                run.append(word)
                previous = word
            run.sort(key=lambda w: w.x0)
            text = " ".join(w.text for w in run).strip()
            if text:
                rows.append((starter.y0, text))

        # Split on vertical gaps: words sharing an x at opposite ends of the
        # page belong to different blocks, not one tall stack.
        for group in _split_on_gaps(rows, max_gap=median * 3.0):
            if len(group) >= min_rows:
                blocks.append(TextBlock(x0=float(x0), rows=group))

    blocks.sort(key=lambda b: (b.top, b.x0))
    return blocks


def _split_on_gaps(
    rows: Sequence[Tuple[float, str]], max_gap: float
) -> List[List[Tuple[float, str]]]:
    groups: List[List[Tuple[float, str]]] = []
    current: List[Tuple[float, str]] = []
    previous_y: Optional[float] = None
    for y, text in rows:
        if previous_y is not None and (y - previous_y) > max_gap and current:
            groups.append(current)
            current = []
        current.append((y, text))
        previous_y = y
    if current:
        groups.append(current)
    return groups


def detect_parties(grid: PageGrid, my_company: Sequence[str]) -> Dict[str, str]:
    """Split the address blocks into 'us' and 'the counterparty'."""
    mine = [c.lower().strip() for c in my_company if c.strip()]
    result: Dict[str, str] = {}
    blocks = _word_blocks(grid)
    limit = grid.height * 0.5

    def _is_mine(text: str) -> bool:
        lowered = text.lower()
        return any(name in lowered for name in mine)

    def _company_name(block: TextBlock) -> Optional[str]:
        texts = block.texts()
        for index, text in enumerate(texts[:2]):
            if len(text) < 4 or len(text) > 70 or _LABELISH.search(text):
                continue
            if DOC_TITLE_RE.match(text):
                continue
            if not (COMPANY_RE.search(text) or (text.isupper() and len(text.split()) <= 5)):
                continue
            # Fold a trailing "PTY LTD" line into the name above it.
            if index + 1 < len(texts) and LEGAL_SUFFIX_RE.match(texts[index + 1]):
                return f"{text} {texts[index + 1]}".strip()
            return text
        return None

    for block in blocks:
        if block.top > limit:
            continue
        name = _company_name(block)
        if not name:
            continue
        if _is_mine(name):
            result.setdefault("vendor_is_us", name)
        else:
            result.setdefault("supplier", name)

    # "Ship To" / "Delivery Address" names the block that follows the label.
    label = None
    for block in blocks:
        for y, text in block.rows:
            if re.match(r"^(ship\s*to|deliver(?:y)?\s*to|delivery\s*address)\b", text, re.I):
                label = (block, y, text)
                break
        if label:
            break

    if label is not None:
        block, y, text = label
        remainder = text.split(":", 1)[1].strip() if ":" in text else ""
        if remainder:
            result["ship_to"] = remainder
        else:
            following = [t for row_y, t in block.rows if row_y > y]
            if following:
                result["ship_to"] = following[0]

    result.pop("vendor_is_us", None)
    return result


_CURRENCY_RE = re.compile(r"\b(AUD|NZD|USD|GBP|EUR|CAD|SGD|HKD|JPY)\b")


def detect_currency(grid: PageGrid) -> Optional[str]:
    match = _CURRENCY_RE.search(grid.text)
    if match:
        return match.group(1)
    if "$" in grid.text:
        return "AUD" if re.search(r"\bABN\b", grid.text, re.I) else "USD"
    if "£" in grid.text:
        return "GBP"
    if "€" in grid.text:
        return "EUR"
    return None


# --------------------------------------------------------------------------
# Top-level parse
# --------------------------------------------------------------------------

def parse_purchase_order(
    grids: Sequence[PageGrid],
    doc_id: str = "",
    filename: str = "",
    template: Optional[POTemplate] = None,
    my_company: Sequence[str] = (),
    source: str = "text",
    engine: Optional[str] = None,
) -> PurchaseOrderResult:
    """Parse a purchase order spanning one or more page grids."""
    result = PurchaseOrderResult(
        doc_id=doc_id, filename=filename, source=source, engine=engine,
        template_used=template.template_id if template else None,
    )
    if not grids:
        result.warnings.append("No pages to read")
        return result

    header_fields: Dict[str, str] = {}
    all_items: List[POLineItem] = []
    columns: List[str] = []
    # Carried across pages so a table that continues without repeating its
    # header still parses against the right columns.
    last_cells: Optional[List[HeaderCell]] = None
    last_bands: Optional[List[Tuple[float, float]]] = None

    for grid in grids:
        if not grid.words:
            result.warnings.append(f"Page {grid.page_number} has no readable text")
            continue

        found = find_header_row(grid)

        # Exclude the table itself before hunting for document-level labels,
        # so a "GST" column header is never read as the document's GST total.
        excluded: set[int] = set()
        if found is not None:
            table_start = found[0]
            table_body = collect_body_lines(grid, table_start, found[1])
            last_cy = table_body[-1].cy if table_body else grid.lines[table_start].cy
            excluded = {
                idx for idx, line in enumerate(grid.lines)
                if table_start <= idx and line.cy <= last_cy
            }

        for key, value in extract_header_fields(grid, excluded).items():
            header_fields.setdefault(key, value)
        for key, value in detect_parties(grid, my_company).items():
            header_fields.setdefault(key, value)

        if template and template.columns:
            cells = [
                HeaderCell(text=c.header_text or c.field, x0=c.x0, x1=c.x1, field=c.field,
                           score=100)
                for c in template.columns
            ]
            header_index = found[0] if found else _guess_header_index(grid)
            body = collect_body_lines(grid, header_index, cells) if header_index is not None else []
            bands = column_bands(cells, compute_boundaries(cells, body))
        elif found is not None:
            header_index, cells = found
            body = collect_body_lines(grid, header_index, cells)
            bands = column_bands(cells, compute_boundaries(cells, body))
        elif last_cells is not None:
            # A long order continues onto the next page without repeating its
            # header. Reuse the previous page's columns, but only when the page
            # actually looks like table rows — otherwise a terms-and-conditions
            # page would be mined for junk line items.
            candidate_body = collect_body_lines(grid, -1, last_cells)
            if not _looks_like_table_continuation(candidate_body, last_cells, last_bands):
                continue
            cells, bands, body = last_cells, last_bands, candidate_body
        else:
            result.warnings.append(
                f"Page {grid.page_number}: no line-item table header recognised"
            )
            continue

        if not columns:
            columns = [c.text for c in cells]
        items = parse_line_items(grid, cells, bands, body)
        all_items.extend(items)
        last_cells, last_bands = list(cells), list(bands)

    # Number the items once across the whole order, so a table continuing onto
    # a second page keeps counting instead of restarting at 1.
    for position, item in enumerate(all_items, start=1):
        if item.line_no is None:
            item.line_no = position

    result.columns = columns
    result.line_items = all_items
    result.raw_text = "\n\n".join(g.text for g in grids)

    header = POHeader()
    for field in (
        "po_number", "order_date", "required_date", "supplier", "ship_to",
        "buyer", "reference", "total", "subtotal", "tax",
    ):
        if field in header_fields:
            if field == "subtotal":
                header.subtotal = header_fields[field]
            else:
                setattr(header, field, header_fields[field])
    header.currency = detect_currency(grids[0])
    result.header = header

    if not all_items:
        result.warnings.append(
            "No line items were found — try Region OCR, or save a column template for this supplier"
        )
    return result


def _looks_like_table_continuation(
    body: Sequence[Line],
    cells: Sequence[HeaderCell],
    bands: Sequence[Tuple[float, float]],
) -> bool:
    """Does this headerless page really carry rows of the previous table?

    Requires at least one line with numbers in two different numeric columns.
    Prose happens to put a number in one band often enough; landing in two
    distinct ones by accident is rare.
    """
    anchor_indices = [
        idx for idx, cell in enumerate(cells) if cell.field in NUMERIC_ANCHORS
    ]
    if len(anchor_indices) < 2:
        return False
    for line in body:
        hits = sum(
            1 for idx in anchor_indices
            if _looks_numeric(line.text_between(bands[idx][0], bands[idx][1]))
        )
        if hits >= 2:
            return True
    return False


def _guess_header_index(grid: PageGrid) -> Optional[int]:
    for idx, line in enumerate(grid.lines):
        if re.search(r"description|qty|quantity|item", line.text, re.IGNORECASE):
            return idx
    return None


def template_from_result(
    name: str,
    grid: PageGrid,
    supplier_hint: Optional[str] = None,
) -> Optional[POTemplate]:
    """Build a reusable template from a page the parser already understands."""
    found = find_header_row(grid)
    if found is None:
        return None
    header_index, cells = found
    body = collect_body_lines(grid, header_index, cells)
    boundaries = compute_boundaries(cells, body)
    bands = column_bands(cells, boundaries)

    return POTemplate(
        template_id="",
        name=name,
        supplier_match=[supplier_hint] if supplier_hint else [],
        columns=[
            ColumnMapping(
                field=cell.field or f"extra_{idx}",
                header_text=cell.text,
                x0=band[0] if band[0] > -1e5 else cell.x0,
                x1=band[1] if band[1] < 1e5 else cell.x1,
            )
            for idx, (cell, band) in enumerate(zip(cells, bands))
        ],
    )
