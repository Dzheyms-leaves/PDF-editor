"""Engine interface and the shared plumbing every backend needs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from ..models import OcrBlock, OcrPageResult, Rect


class OcrError(RuntimeError):
    """Raised when an engine is unavailable or a recognition call fails."""


@dataclass
class RenderedPage:
    """One page rasterised for recognition, plus the mapping back to PDF space."""

    png: bytes
    width_px: int
    height_px: int
    page_width: float   # PDF points
    page_height: float  # PDF points
    page_number: int
    dpi: int
    # Live PyMuPDF page, when the caller has one open. Lets the native engine
    # read the existing text layer instead of re-recognising pixels.
    source_page: Any = None
    # Offset of the rendered clip within the page, for region OCR.
    origin_x: float = 0.0
    origin_y: float = 0.0

    @property
    def scale_x(self) -> float:
        return self.page_width / self.width_px if self.width_px else 1.0

    @property
    def scale_y(self) -> float:
        return self.page_height / self.height_px if self.height_px else 1.0

    def px_to_pdf(self, x0: float, y0: float, x1: float, y1: float) -> Rect:
        return Rect(
            x0=self.origin_x + x0 * self.scale_x,
            y0=self.origin_y + y0 * self.scale_y,
            x1=self.origin_x + x1 * self.scale_x,
            y1=self.origin_y + y1 * self.scale_y,
        )

    def norm_to_pdf(self, x0: float, y0: float, x1: float, y1: float, span: float = 999.0) -> Rect:
        """Map normalised (0..span) coordinates onto PDF points."""
        if span <= 0:
            span = 999.0
        return Rect(
            x0=self.origin_x + (x0 / span) * self.page_width,
            y0=self.origin_y + (y0 / span) * self.page_height,
            x1=self.origin_x + (x1 / span) * self.page_width,
            y1=self.origin_y + (y1 / span) * self.page_height,
        )


class OcrEngine:
    """Base class for every recognition backend.

    Subclasses implement :meth:`recognise` and :meth:`probe`. ``probe`` must be
    cheap and must never raise — it reports whether the engine can run here.
    """

    name: str = "base"
    label: str = "Base engine"
    priority: int = 0            # higher wins during auto-selection
    supports_layout: bool = False
    supports_markdown: bool = False
    install_hint: str = ""

    def __init__(self) -> None:
        self._detail: str = ""
        self._device: Optional[str] = None

    # -- discovery -------------------------------------------------------

    def probe(self) -> bool:
        raise NotImplementedError

    @property
    def detail(self) -> str:
        return self._detail

    @property
    def device(self) -> Optional[str]:
        return self._device

    # -- work ------------------------------------------------------------

    def recognise(
        self,
        page: RenderedPage,
        mode: str = "markdown",
        prompt: Optional[str] = None,
    ) -> OcrPageResult:
        raise NotImplementedError

    def warmup(self) -> None:
        """Optional: load weights ahead of the first real call."""

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _blank(page: RenderedPage, engine: str, warning: str = "") -> OcrPageResult:
        return OcrPageResult(
            page_number=page.page_number,
            engine=engine,
            width=page.page_width,
            height=page.page_height,
            warning=warning or None,
        )


# --------------------------------------------------------------------------
# Grounding / markdown post-processing shared by the VLM backends
# --------------------------------------------------------------------------

_REF_DET_RE = re.compile(
    r"<\|ref\|>(?P<text>.*?)<\|/ref\|>\s*<\|det\|>(?P<boxes>\[\[.*?\]\])<\|/det\|>",
    re.DOTALL,
)
_BOX_RE = re.compile(r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,"
                     r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]")
_TAG_RE = re.compile(r"<\|(?:ref|/ref|det|/det|grounding)\|>")


def parse_grounded_output(raw: str, page: RenderedPage) -> Tuple[str, List[OcrBlock]]:
    """Split DeepSeek-style grounded output into clean text and located blocks.

    The model emits ``<|ref|>label<|/ref|><|det|>[[x0,y0,x1,y1]]<|/det|>`` with
    coordinates normalised to 0-999 against the input image.
    """
    blocks: List[OcrBlock] = []
    for match in _REF_DET_RE.finditer(raw or ""):
        text = _TAG_RE.sub("", match.group("text")).strip()
        if not text:
            continue
        coords = _BOX_RE.findall(match.group("boxes"))
        if not coords:
            blocks.append(OcrBlock(text=text))
            continue
        for x0, y0, x1, y1 in coords:
            blocks.append(
                OcrBlock(
                    text=text,
                    rect=page.norm_to_pdf(float(x0), float(y0), float(x1), float(y1)),
                    kind=classify_block(text),
                )
            )

    cleaned = _REF_DET_RE.sub(lambda m: _TAG_RE.sub("", m.group("text")), raw or "")
    cleaned = _TAG_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, blocks


def classify_block(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("#"):
        return "title"
    if "|" in stripped and stripped.count("|") >= 3:
        return "table"
    if stripped.startswith("![") or stripped.lower().startswith("<figure"):
        return "figure"
    return "text"


def markdown_to_plain(markdown: str) -> str:
    """Flatten markdown to readable plain text (keeps table cells separated)."""
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", markdown or "", flags=re.MULTILINE)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"^\s*\|?[\s:\-|]+\|?\s*$", "", text, flags=re.MULTILINE)  # table rules
    text = text.replace("|", "\t")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
