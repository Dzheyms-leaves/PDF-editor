"""Orchestration: rasterise pages, route them to an engine, build results."""

from __future__ import annotations

from typing import List, Optional

from .. import config
from ..models import OcrPageResult, OcrRequest, PageSelection, Rect
from ..pdfcompat import pymupdf
from ..pdfops.common import resolve_pages, to_fitz_rect
from .base import OcrEngine, OcrError, RenderedPage
from .registry import get_engine

# A page needs at least this much embedded text before we trust its text layer
# and skip recognition. Kept deliberately low and in step with
# ``DocumentStore.describe``: re-OCRing a page that already has real text is
# both slower and less accurate than reading it.
NATIVE_TEXT_THRESHOLD = 12


def render_page(
    doc: "pymupdf.Document",
    page_no: int,
    dpi: int = 200,
    clip: Optional[Rect] = None,
) -> RenderedPage:
    """Rasterise one page (or a region of it) ready for recognition."""
    if not 1 <= page_no <= doc.page_count:
        raise ValueError(f"Page {page_no} is out of range")

    page = doc[page_no - 1]
    clip_rect = None
    if clip is not None:
        clip_rect = to_fitz_rect(clip) & page.rect
        if clip_rect.is_empty or clip_rect.width < 2 or clip_rect.height < 2:
            raise ValueError("The selected region is too small to read")

    pix = page.get_pixmap(dpi=dpi, alpha=False, clip=clip_rect)
    area = clip_rect if clip_rect is not None else page.rect

    return RenderedPage(
        png=pix.tobytes("png"),
        width_px=pix.width,
        height_px=pix.height,
        page_width=float(area.width),
        page_height=float(area.height),
        page_number=page_no,
        dpi=dpi,
        source_page=page,
        origin_x=float(area.x0),
        origin_y=float(area.y0),
    )


def page_has_text(doc: "pymupdf.Document", page_no: int) -> bool:
    if not 1 <= page_no <= doc.page_count:
        return False
    return len(doc[page_no - 1].get_text("text").strip()) >= NATIVE_TEXT_THRESHOLD


def _engine_for(
    doc: "pymupdf.Document", page_no: int, request: OcrRequest
) -> OcrEngine:
    """Use the embedded text layer when it exists, unless OCR was forced."""
    if not request.force and (request.engine in (None, "", "auto")) and page_has_text(doc, page_no):
        return get_engine("native", allow_native=True)
    if request.engine == "native":
        return get_engine("native", allow_native=True)
    return get_engine(request.engine)


def ocr_pages(doc: "pymupdf.Document", request: OcrRequest) -> List[OcrPageResult]:
    """Recognise every selected page, one engine call per page."""
    dpi = int(request.dpi or config.get_setting("ocr_render_dpi", 200))
    pages = resolve_pages(request.pages, doc.page_count)
    results: List[OcrPageResult] = []

    for page_no in pages:
        try:
            engine = _engine_for(doc, page_no, request)
        except OcrError as exc:
            results.append(
                OcrPageResult(page_number=page_no, engine="none", warning=str(exc))
            )
            continue

        try:
            # The native engine reads the page directly; no raster needed.
            render_dpi = 1 if engine.name == "native" else dpi
            rendered = render_page(doc, page_no, render_dpi)
            result = engine.recognise(rendered, mode=request.mode, prompt=request.prompt)

            # If the text layer turned out to be empty, retry with real OCR.
            if engine.name == "native" and not result.text.strip() and not request.force:
                try:
                    real = get_engine(request.engine)
                    rendered = render_page(doc, page_no, dpi)
                    result = real.recognise(rendered, mode=request.mode, prompt=request.prompt)
                except OcrError as exc:
                    result.warning = (
                        f"This page has no text layer and no OCR engine is available: {exc}"
                    )
            results.append(result)
        except OcrError as exc:
            results.append(
                OcrPageResult(page_number=page_no, engine=engine.name, warning=str(exc))
            )
        except Exception as exc:  # noqa: BLE001 - keep the batch alive
            results.append(
                OcrPageResult(
                    page_number=page_no, engine=engine.name,
                    warning=f"Unexpected failure: {exc}",
                )
            )
    return results


def ocr_region(
    doc: "pymupdf.Document",
    page_no: int,
    region: Rect,
    engine_name: Optional[str] = None,
    dpi: Optional[int] = None,
    mode: str = "plain",
    force: bool = False,
) -> OcrPageResult:
    """Recognise just one rectangle — the drag-a-box-and-copy path."""
    use_dpi = int(dpi or config.get_setting("ocr_render_dpi", 200))

    if not force and engine_name in (None, "", "auto"):
        page = doc[page_no - 1]
        clip = to_fitz_rect(region) & page.rect
        if not clip.is_empty and len(page.get_text("text", clip=clip).strip()) >= 3:
            engine = get_engine("native", allow_native=True)
            rendered = render_page(doc, page_no, 1, clip=region)
            return engine.recognise(rendered, mode=mode)

    engine = get_engine(engine_name)
    # Small regions benefit from extra resolution.
    rendered = render_page(doc, page_no, use_dpi, clip=region)
    if rendered.width_px < 400 or rendered.height_px < 120:
        rendered = render_page(doc, page_no, min(600, use_dpi * 2), clip=region)
    return engine.recognise(rendered, mode=mode)


def make_searchable(
    doc: "pymupdf.Document",
    selection: PageSelection,
    engine_name: Optional[str] = None,
    dpi: Optional[int] = None,
) -> dict:
    """Add an invisible text layer so scanned pages become searchable.

    Text is written with render mode 3 (invisible), positioned on each
    recognised line's own box, so selection lines up with the underlying image.
    """
    use_dpi = int(dpi or config.get_setting("ocr_render_dpi", 200))
    engine = get_engine(engine_name)
    pages = resolve_pages(selection, doc.page_count)

    updated = 0
    skipped: List[int] = []
    for page_no in pages:
        if page_has_text(doc, page_no):
            skipped.append(page_no)
            continue
        rendered = render_page(doc, page_no, use_dpi)
        try:
            result = engine.recognise(rendered, mode="grounded")
        except OcrError:
            continue

        page = doc[page_no - 1]
        wrote = False
        for block in result.blocks:
            text = block.text.replace("\t", " ").strip()
            if not text or block.rect is None:
                continue
            box = to_fitz_rect(block.rect) & page.rect
            if box.is_empty or box.height < 1:
                continue
            size = max(2.0, min(72.0, box.height * 0.82))
            try:
                page.insert_text(
                    pymupdf.Point(box.x0, box.y1 - box.height * 0.18),
                    text,
                    fontname="helv",
                    fontsize=size,
                    render_mode=3,  # invisible: selectable but not drawn
                    color=(0, 0, 0),
                )
                wrote = True
            except Exception:  # noqa: BLE001 - unmappable glyphs are skipped
                continue
        if wrote:
            updated += 1

    return {"pages_updated": updated, "pages_skipped": skipped, "engine": engine.name}
