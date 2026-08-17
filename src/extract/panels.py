"""Control-panel button-label extraction.

Ported from the original ``antumbrapdfextractor`` browser tool, keeping both
layout parsers and their tuning constants:

* **Dynalite spec sheets** — one panel per PDF; labels are rows of text split
  on wide horizontal gaps, with boilerplate filtered out by noise patterns.
* **Smart Home Works job packs** — several panels per page, each a header plus
  a two-column bullet grid, with a mangled duplicate list off to the right that
  must be excluded.

Unlike the browser original, a scanned image-only sheet is no longer a dead
end: the caller can hand us OCR-derived grids and the same parsers run on them.
"""

from __future__ import annotations

import re
import uuid
from typing import Dict, List, Optional, Sequence, Tuple

from ..models import PanelEntry, PanelLabel, Rect
from .textgrid import PageGrid, Word

# --------------------------------------------------------------------------
# Dynalite spec sheets
# --------------------------------------------------------------------------

NOISE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"^orientation\s*:", r"^creation date", r"^product code", r"^12\s*nc",
        r"^panel name\s*:", r"^style\s*:", r"philips", r"dynalite",
        r"button colou?r", r"^type\s*:", r"^\d{4}$", r"^\d{6,}$", r"^[|_\-\s]+$",
    )
]

BREAK_GAP = 15.0  # x-gap above which two runs are separate button labels
JOIN_SPACE_GAP = 1.5


def is_noise(text: str) -> bool:
    return any(rx.search(text) for rx in NOISE_PATTERNS)


def _tokens_from_line(words: Sequence[Word]) -> List[Tuple[str, Rect]]:
    """Split a line into label tokens on wide horizontal gaps."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: w.x0)
    tokens: List[Tuple[str, Rect]] = []
    current: List[Word] = [ordered[0]]

    for previous, word in zip(ordered, ordered[1:]):
        gap = word.x0 - previous.x1
        if gap > BREAK_GAP:
            tokens.append(_token(current))
            current = [word]
        else:
            current.append(word)
    tokens.append(_token(current))
    return [(text, rect) for text, rect in tokens if text.strip()]


def _token(words: Sequence[Word]) -> Tuple[str, Rect]:
    text = ""
    previous: Optional[Word] = None
    for word in words:
        if previous is None:
            text = word.text
        else:
            gap = word.x0 - previous.x1
            text += (" " if gap > JOIN_SPACE_GAP else "") + word.text
        previous = word
    rect = Rect(
        x0=min(w.x0 for w in words), y0=min(w.y0 for w in words),
        x1=max(w.x1 for w in words), y1=max(w.y1 for w in words),
    )
    return text.strip(), rect


def guess_panel_name(grids: Sequence[PageGrid], fallback: str) -> str:
    for grid in grids:
        for line in grid.lines:
            match = re.search(r"panel name\s*:\s*(.+)", line.text, re.IGNORECASE)
            if match and match.group(1).strip():
                return match.group(1).strip()
    return fallback


def parse_dynalite(grids: Sequence[PageGrid], fallback_name: str) -> List[PanelEntry]:
    """One queue entry per PDF, with every text row offered as labels."""
    rows: List[List[PanelLabel]] = []
    for grid in grids:
        for line in grid.lines:
            tokens = _tokens_from_line(line.words)
            if not tokens:
                continue
            rows.append([
                PanelLabel(text=text, include=not is_noise(text), rect=rect)
                for text, rect in tokens
            ])

    entry = PanelEntry(
        panel_id=f"p{uuid.uuid4().hex[:8]}",
        name=guess_panel_name(grids, fallback_name),
        rows=rows,
        page=grids[0].page_number if grids else 1,
        style="dynalite",
        source_file=fallback_name,
    )
    entry.config_key = config_key(entry)
    return [entry]


# --------------------------------------------------------------------------
# Smart Home Works job packs
# --------------------------------------------------------------------------

SH_FOOTER_Y = 760.0
SH_GRID_X_MAX = 260.0    # the mangled duplicate list starts around x 279
SH_COL_SPLIT = 130.0     # left column < 130 <= right column
SH_CELL_GAP = 40.0       # vertical gap that starts a new button cell
SH_LINE_TOL = 6.0
SH_WORD_JOIN_GAP = 25.0

_SH_SKIP = re.compile(r"^(Panel|Type|Antumbra|Finish|White|w/|Surround)$", re.IGNORECASE)
_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")


def _cluster_cells(words: Sequence[Word]) -> List[List[Word]]:
    """Split a column's words into button cells on vertical gaps."""
    cells: List[List[Word]] = []
    current: List[Word] = []
    previous_y: Optional[float] = None
    for word in sorted(words, key=lambda w: w.y0):
        if previous_y is not None and (word.y0 - previous_y) > SH_CELL_GAP and current:
            cells.append(current)
            current = []
        current.append(word)
        previous_y = word.y0
    if current:
        cells.append(current)
    return cells


def _cell_label(cell: Sequence[Word]) -> str:
    """Join a cell's words into one label, respecting lines and wide gaps."""
    lines: List[List[Word]] = []
    for word in sorted(cell, key=lambda w: (w.y0, w.x0)):
        target = None
        for line in lines:
            if abs(line[0].y0 - word.y0) < SH_LINE_TOL:
                target = line
                break
        if target is None:
            lines.append([word])
        else:
            target.append(word)

    lines.sort(key=lambda ln: min(w.y0 for w in ln))
    line_texts: List[str] = []
    for line in lines:
        line.sort(key=lambda w: w.x0)
        parts: List[str] = []
        current = ""
        previous: Optional[Word] = None
        for word in line:
            if previous is None:
                current = word.text
            elif (word.x0 - previous.x1) > SH_WORD_JOIN_GAP:
                parts.append(current)
                current = word.text
            else:
                current += " " + word.text
            previous = word
        if current:
            parts.append(current)
        line_texts.append("  ".join(parts))
    return " ".join(line_texts).strip()


def parse_smart_blocks(grid: PageGrid, source_file: str) -> List[PanelEntry]:
    """Split one page into its panel blocks."""
    headers = sorted(
        (w for w in grid.words
         if re.sub(r"[^A-Za-z]", "", w.text) == "Panel"
         and w.x0 < 90 and w.y0 < SH_FOOTER_Y),
        key=lambda w: w.y0,
    )
    if not headers:
        return []

    entries: List[PanelEntry] = []
    for index, header in enumerate(headers):
        header_y = header.y0
        next_y = headers[index + 1].y0 if index + 1 < len(headers) else SH_FOOTER_Y

        in_block = [
            w for w in grid.words
            if header_y - 3 <= w.y0 < next_y - 3
            and w.x0 < SH_GRID_X_MAX
            and not _DATE_RE.match(w.text)
        ]

        header_words = sorted(
            (w for w in in_block if abs(w.y0 - header_y) < SH_LINE_TOL),
            key=lambda w: w.x0,
        )
        number = next(
            (w.text for w in header_words
             if w.text.isdigit() and 60 < w.x0 < 150),
            "",
        )
        name = " ".join(w.text for w in header_words if w.x0 > 150).strip()

        grid_words: List[Word] = []
        for word in in_block:
            if word.y0 <= header_y + 20:
                continue
            cleaned = word.text.replace("•", "").replace("•", "").strip()
            if not cleaned or _SH_SKIP.match(cleaned):
                continue
            grid_words.append(
                Word(text=cleaned, x0=word.x0, y0=word.y0, x1=word.x1, y1=word.y1)
            )

        left = [w for w in grid_words if w.x0 < SH_COL_SPLIT]
        right = [w for w in grid_words if w.x0 >= SH_COL_SPLIT]
        left_cells = [c for c in (_cell_label(c) for c in _cluster_cells(left)) if c]
        right_cells = [c for c in (_cell_label(c) for c in _cluster_cells(right)) if c]

        rows: List[List[PanelLabel]] = []
        for position in range(max(len(left_cells), len(right_cells))):
            row: List[PanelLabel] = []
            if position < len(left_cells):
                row.append(PanelLabel(text=left_cells[position]))
            if position < len(right_cells):
                row.append(PanelLabel(text=right_cells[position]))
            if row:
                rows.append(row)

        entry = PanelEntry(
            panel_id=f"p{uuid.uuid4().hex[:8]}",
            name=(f"P{number} — " if number else "") + (name or "Panel"),
            rows=rows,
            page=grid.page_number,
            style="smart",
            source_file=source_file,
        )
        entry.config_key = config_key(entry)
        entries.append(entry)
    return entries


# --------------------------------------------------------------------------
# Style sniffing and top-level entry point
# --------------------------------------------------------------------------

def sniff_style(grids: Sequence[PageGrid]) -> str:
    """Dynalite sheets announce themselves; job packs are found by geometry."""
    for grid in grids:
        for line in grid.lines:
            if re.search(r"panel name\s*:", line.text, re.IGNORECASE):
                return "dynalite"
    for grid in grids:
        if any(
            re.sub(r"[^A-Za-z]", "", w.text) == "Panel" and w.x0 < 90 and w.y0 < SH_FOOTER_Y
            for w in grid.words
        ):
            return "smart"
    return "dynalite"


def extract_panels(
    grids: Sequence[PageGrid], source_file: str, style: str = "auto"
) -> Tuple[str, List[PanelEntry]]:
    """Return ``(style_used, entries)``."""
    if not grids:
        return "dynalite", []
    chosen = sniff_style(grids) if style in (None, "auto") else style

    if chosen == "smart":
        entries: List[PanelEntry] = []
        for grid in grids:
            entries.extend(parse_smart_blocks(grid, source_file))
        if entries:
            return "smart", entries
        # Fall through to the Dynalite reading rather than returning nothing.
        chosen = "dynalite"

    return "dynalite", parse_dynalite(grids, source_file)


# --------------------------------------------------------------------------
# Identical-configuration grouping
# --------------------------------------------------------------------------

def included_labels(entry: PanelEntry) -> List[str]:
    return [
        label.text.strip()
        for row in entry.rows for label in row
        if label.include and label.text.strip()
    ]


def config_key(entry: PanelEntry) -> str:
    """Panels with the same key have an identical label layout."""
    lines = [
        " | ".join(label.text.strip() for label in row if label.include)
        for row in entry.rows
    ]
    return "\n".join(line for line in lines if line).lower()


def group_by_config(entries: Sequence[PanelEntry]) -> List[Dict]:
    """Group panels sharing an identical configuration (2 or more members)."""
    groups: Dict[str, List[PanelEntry]] = {}
    for entry in entries:
        if entry.error or not entry.rows:
            continue
        key = entry.config_key or config_key(entry)
        if not key:
            continue
        groups.setdefault(key, []).append(entry)

    out: List[Dict] = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        out.append({
            "config_key": key,
            "count": len(members),
            "done_count": sum(1 for m in members if m.done),
            "labels": included_labels(members[0]),
            "panel_ids": [m.panel_id for m in members],
            "names": [m.name for m in members],
        })
    out.sort(key=lambda g: -g["count"])
    return out
