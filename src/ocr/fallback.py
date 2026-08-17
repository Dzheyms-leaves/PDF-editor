"""Lightweight OCR backends used when DeepSeek-OCR cannot run.

None of these understand document layout the way a VLM does, so their output is
reconstructed into reading order geometrically: words are clustered into lines
by baseline, and lines into blocks by vertical gaps.
"""

from __future__ import annotations

import io
import shutil
import time
from typing import Any, List, Optional, Sequence, Tuple

from ..models import OcrBlock, OcrPageResult, Rect
from .base import OcrEngine, OcrError, RenderedPage


def _lines_from_boxes(
    items: Sequence[Tuple[float, float, float, float, str, Optional[float]]],
    line_tol: float,
) -> List[List[Tuple[float, float, float, float, str, Optional[float]]]]:
    """Group (x0, y0, x1, y1, text, conf) items into visual lines."""
    lines: List[List[Any]] = []
    for item in sorted(items, key=lambda i: (i[1], i[0])):
        y_centre = (item[1] + item[3]) / 2.0
        placed = False
        for line in lines:
            ref = line[0]
            ref_centre = (ref[1] + ref[3]) / 2.0
            if abs(y_centre - ref_centre) <= line_tol:
                line.append(item)
                placed = True
                break
        if not placed:
            lines.append([item])
    for line in lines:
        line.sort(key=lambda i: i[0])
    lines.sort(key=lambda ln: min(i[1] for i in ln))
    return lines


def _assemble(
    page: RenderedPage,
    items: Sequence[Tuple[float, float, float, float, str, Optional[float]]],
    engine: str,
    started: float,
    in_pixels: bool = True,
) -> OcrPageResult:
    """Turn raw word boxes into text, markdown and located blocks."""
    if not items:
        return OcrPageResult(
            page_number=page.page_number, engine=engine,
            width=page.page_width, height=page.page_height,
            duration_ms=int((time.perf_counter() - started) * 1000),
            warning="No text was recognised on this page",
        )

    heights = [abs(i[3] - i[1]) for i in items if abs(i[3] - i[1]) > 0]
    median_h = sorted(heights)[len(heights) // 2] if heights else 10.0
    lines = _lines_from_boxes(items, line_tol=max(2.0, median_h * 0.6))

    text_lines: List[str] = []
    for line in lines:
        # Mark wide horizontal gaps so column structure survives in plain text.
        parts: List[str] = []
        prev_x1: Optional[float] = None
        for x0, y0, x1, y1, text, _conf in line:
            if prev_x1 is not None and (x0 - prev_x1) > median_h * 1.6:
                parts.append("\t")
            parts.append(text)
            prev_x1 = x1
        joined = "".join(
            (p if p == "\t" else p + " ") for p in parts
        ).replace(" \t ", "\t").strip()
        text_lines.append(joined)

    # One block per *detection*, not per line. Each engine detection carries an
    # accurate box for a short phrase; merging a whole line into a single block
    # would smear phrases from different columns into one span, and the table
    # column detector works entirely from those x-positions.
    blocks: List[OcrBlock] = []
    for x0, y0, x1, y1, text, conf in items:
        rect = page.px_to_pdf(x0, y0, x1, y1) if in_pixels else Rect(x0=x0, y0=y0, x1=x1, y1=y1)
        blocks.append(OcrBlock(text=text, rect=rect, confidence=conf))

    plain = "\n".join(text_lines)
    return OcrPageResult(
        page_number=page.page_number,
        engine=engine,
        text=plain,
        markdown=plain,
        blocks=blocks,
        width=page.page_width,
        height=page.page_height,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


class RapidOcrEngine(OcrEngine):
    """RapidOCR — ONNX PP-OCR. CPU-friendly, no system packages, good accuracy."""

    name = "rapidocr"
    label = "RapidOCR (CPU)"
    priority = 60
    supports_layout = True
    install_hint = "pip install rapidocr-onnxruntime"

    def __init__(self) -> None:
        super().__init__()
        self._reader: Any = None

    def _import(self):
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415
            return RapidOCR
        except ImportError:
            try:
                from rapidocr import RapidOCR  # noqa: PLC0415 - newer package name
                return RapidOCR
            except ImportError:
                return None

    def probe(self) -> bool:
        if self._import() is None:
            self._detail = "rapidocr-onnxruntime is not installed"
            return False
        self._device = "cpu"
        self._detail = "ONNX Runtime on CPU"
        return True

    def _ensure(self) -> None:
        if self._reader is None:
            factory = self._import()
            if factory is None:
                raise OcrError("RapidOCR is not installed")
            self._reader = factory()

    def recognise(
        self, page: RenderedPage, mode: str = "markdown", prompt: Optional[str] = None
    ) -> OcrPageResult:
        self._ensure()
        started = time.perf_counter()
        try:
            import numpy as np  # noqa: PLC0415
            from PIL import Image  # noqa: PLC0415

            with Image.open(io.BytesIO(page.png)) as img:
                array = np.array(img.convert("RGB"))
            result, _elapsed = self._reader(array)
        except Exception as exc:  # noqa: BLE001
            raise OcrError(f"RapidOCR failed on page {page.page_number}: {exc}") from exc

        items = []
        for entry in result or []:
            box, text, score = entry[0], entry[1], (entry[2] if len(entry) > 2 else None)
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            items.append((min(xs), min(ys), max(xs), max(ys), str(text),
                          float(score) if score is not None else None))
        return _assemble(page, items, self.name, started)


class TesseractEngine(OcrEngine):
    """Tesseract via pytesseract. Ubiquitous, weakest on dense tables."""

    name = "tesseract"
    label = "Tesseract (CPU)"
    priority = 40
    supports_layout = True
    install_hint = "Install the Tesseract binary, then: pip install pytesseract"

    def probe(self) -> bool:
        try:
            import pytesseract  # noqa: PLC0415
        except ImportError:
            self._detail = "pytesseract is not installed"
            return False
        binary = shutil.which("tesseract") or getattr(
            pytesseract.pytesseract, "tesseract_cmd", ""
        )
        if not binary or (binary != "tesseract" and not shutil.which(str(binary))):
            self._detail = "The tesseract binary was not found on PATH"
            return False
        try:
            version = str(pytesseract.get_tesseract_version())
        except Exception:  # noqa: BLE001
            self._detail = "The tesseract binary could not be run"
            return False
        self._device = "cpu"
        self._detail = f"Tesseract {version}"
        return True

    def recognise(
        self, page: RenderedPage, mode: str = "markdown", prompt: Optional[str] = None
    ) -> OcrPageResult:
        started = time.perf_counter()
        try:
            import pytesseract  # noqa: PLC0415
            from PIL import Image  # noqa: PLC0415

            with Image.open(io.BytesIO(page.png)) as img:
                data = pytesseract.image_to_data(
                    img.convert("RGB"), output_type=pytesseract.Output.DICT
                )
        except Exception as exc:  # noqa: BLE001
            raise OcrError(f"Tesseract failed on page {page.page_number}: {exc}") from exc

        items = []
        for idx, word in enumerate(data.get("text", [])):
            word = (word or "").strip()
            if not word:
                continue
            try:
                conf = float(data["conf"][idx])
            except (ValueError, KeyError, IndexError):
                conf = -1.0
            if conf < 0:
                conf = None  # type: ignore[assignment]
            x = float(data["left"][idx])
            y = float(data["top"][idx])
            w = float(data["width"][idx])
            h = float(data["height"][idx])
            items.append((x, y, x + w, y + h, word,
                          (conf / 100.0) if conf is not None else None))
        return _assemble(page, items, self.name, started)
