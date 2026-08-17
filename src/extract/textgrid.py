"""A coordinate-aware view of a page's words.

Both the PO parser and the panel parser need the same thing: words with boxes,
grouped into lines, in reading order — regardless of whether they came from the
PDF's text layer or from an OCR engine. Raw extraction order is unreliable
(item codes routinely arrive detached from their row), so everything downstream
works from geometry, never from stream order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from ..models import OcrPageResult, Rect
from ..pdfcompat import pymupdf


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class Line:
    words: List[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words).strip()

    @property
    def y0(self) -> float:
        return min((w.y0 for w in self.words), default=0.0)

    @property
    def y1(self) -> float:
        return max((w.y1 for w in self.words), default=0.0)

    @property
    def x0(self) -> float:
        return min((w.x0 for w in self.words), default=0.0)

    @property
    def x1(self) -> float:
        return max((w.x1 for w in self.words), default=0.0)

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def words_between(self, x0: float, x1: float) -> List[Word]:
        """Words whose horizontal centre falls inside ``[x0, x1)``."""
        return [w for w in self.words if x0 <= w.cx < x1]

    def text_between(self, x0: float, x1: float) -> str:
        return " ".join(w.text for w in self.words_between(x0, x1)).strip()


class PageGrid:
    """Words and lines for one page, in PDF coordinates with y growing down."""

    def __init__(self, page_number: int, width: float, height: float, words: Sequence[Word],
                 source: str = "pdf") -> None:
        self.page_number = page_number
        self.width = width
        self.height = height
        self.source = source
        self.words: List[Word] = [w for w in words if w.text.strip()]
        self.lines: List[Line] = group_lines(self.words)

    # ------------------------------------------------------------ builders

    @classmethod
    def from_page(cls, page: "pymupdf.Page", page_number: int) -> "PageGrid":
        words = [
            Word(text=str(w[4]), x0=float(w[0]), y0=float(w[1]), x1=float(w[2]), y1=float(w[3]))
            for w in page.get_text("words")
        ]
        rect = page.rect
        return cls(page_number, float(rect.width), float(rect.height), words, "pdf")

    @classmethod
    def from_ocr(cls, result: OcrPageResult) -> "PageGrid":
        """Build a grid from OCR blocks, splitting block text back into words.

        Engines report a box per line, so word boxes are interpolated by
        character width. That is accurate enough for column assignment, which
        only needs each word's approximate centre.
        """
        words: List[Word] = []
        for block in result.blocks:
            text = block.text.strip()
            if not text:
                continue
            rect = block.rect
            if rect is None:
                continue
            parts = [p for p in re.split(r"[ \t]+", text) if p]
            if not parts:
                continue
            total_chars = sum(len(p) for p in parts) + max(0, len(parts) - 1)
            if total_chars <= 0:
                continue
            span = rect.x1 - rect.x0
            cursor = rect.x0
            for part in parts:
                share = (len(part) / total_chars) * span
                words.append(
                    Word(text=part, x0=cursor, y0=rect.y0, x1=cursor + share, y1=rect.y1)
                )
                cursor += share + (span / total_chars)
        return cls(result.page_number, result.width, result.height, words, "ocr")

    # ------------------------------------------------------------- helpers

    @property
    def median_line_height(self) -> float:
        heights = sorted(w.height for w in self.words if w.height > 0)
        return heights[len(heights) // 2] if heights else 10.0

    def lines_between(self, y0: float, y1: float) -> List[Line]:
        return [ln for ln in self.lines if y0 <= ln.cy < y1]

    def find_line(self, pattern: str, flags: int = re.IGNORECASE) -> Optional[Line]:
        rx = re.compile(pattern, flags)
        for line in self.lines:
            if rx.search(line.text):
                return line
        return None

    def find_word(self, pattern: str, flags: int = re.IGNORECASE) -> Optional[Word]:
        rx = re.compile(pattern, flags)
        for word in self.words:
            if rx.search(word.text):
                return word
        return None

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


def group_lines(words: Sequence[Word], tolerance: Optional[float] = None) -> List[Line]:
    """Cluster words into visual lines by vertical overlap."""
    if not words:
        return []
    if tolerance is None:
        heights = sorted(w.height for w in words if w.height > 0)
        median = heights[len(heights) // 2] if heights else 10.0
        tolerance = max(1.5, median * 0.55)

    lines: List[Line] = []
    for word in sorted(words, key=lambda w: (w.y0, w.x0)):
        target: Optional[Line] = None
        for line in reversed(lines):
            if abs(line.cy - word.cy) <= tolerance:
                target = line
                break
            if line.cy < word.cy - tolerance * 4:
                break
        if target is None:
            target = Line()
            lines.append(target)
        target.words.append(word)

    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    lines.sort(key=lambda ln: (ln.y0, ln.x0))
    return lines


def grids_for_document(
    doc: "pymupdf.Document", pages: Iterable[int]
) -> List[PageGrid]:
    return [PageGrid.from_page(doc[p - 1], p) for p in pages if 1 <= p <= doc.page_count]


def rect_of_line(line: Line) -> Rect:
    return Rect(x0=line.x0, y0=line.y0, x1=line.x1, y1=line.y1)


def join_with_gaps(words: Sequence[Word], gap_factor: float = 1.8) -> str:
    """Join words, inserting a tab where the horizontal gap looks like a column."""
    if not words:
        return ""
    ordered = sorted(words, key=lambda w: w.x0)
    heights = [w.height for w in ordered if w.height > 0]
    median = sorted(heights)[len(heights) // 2] if heights else 10.0
    out: List[str] = [ordered[0].text]
    for prev, cur in zip(ordered, ordered[1:]):
        gap = cur.x0 - prev.x1
        out.append("\t" if gap > median * gap_factor else " ")
        out.append(cur.text)
    return "".join(out).strip()
