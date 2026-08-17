"""The PDF's own text layer, exposed as an OCR engine.

Most purchase orders and spec sheets are born-digital, so reading the embedded
text is both instant and perfectly accurate. Treating it as an engine means the
rest of the app has exactly one code path for "get me the text of this page".
"""

from __future__ import annotations

import time
from typing import List, Optional

from ..models import OcrBlock, OcrPageResult, Rect
from .base import OcrEngine, OcrError, RenderedPage


class NativeTextEngine(OcrEngine):
    name = "native"
    label = "Embedded PDF text"
    priority = 200  # always preferred when the page actually has text
    supports_layout = True
    supports_markdown = False
    install_hint = ""

    def probe(self) -> bool:
        self._device = "cpu"
        self._detail = "Reads the text layer already inside the PDF — instant and exact"
        return True

    def recognise(
        self, page: RenderedPage, mode: str = "markdown", prompt: Optional[str] = None
    ) -> OcrPageResult:
        source = page.source_page
        if source is None:
            raise OcrError("The native engine needs an open PDF page")

        started = time.perf_counter()
        clip = None
        if page.origin_x or page.origin_y or page.page_width or page.page_height:
            try:
                from ..pdfcompat import pymupdf  # noqa: PLC0415

                if page.origin_x or page.origin_y:
                    clip = pymupdf.Rect(
                        page.origin_x,
                        page.origin_y,
                        page.origin_x + page.page_width,
                        page.origin_y + page.page_height,
                    )
            except Exception:  # noqa: BLE001
                clip = None

        blocks: List[OcrBlock] = []
        lines: List[str] = []
        data = source.get_text("dict", clip=clip) if clip else source.get_text("dict")

        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(s.get("text", "") for s in spans).strip()
                if not text:
                    continue
                bbox = line.get("bbox")
                rect = Rect(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3]) if bbox else None
                blocks.append(OcrBlock(text=text, rect=rect, confidence=1.0))
                lines.append(text)

        plain = "\n".join(lines)
        return OcrPageResult(
            page_number=page.page_number,
            engine=self.name,
            text=plain,
            markdown=plain,
            blocks=blocks,
            width=page.page_width,
            height=page.page_height,
            duration_ms=int((time.perf_counter() - started) * 1000),
            warning=None if plain else "This page has no embedded text — it needs real OCR",
        )
