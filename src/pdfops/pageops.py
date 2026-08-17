"""Page-level document surgery: reorder, rotate, delete, merge, split, crop."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..models import Rect, SplitRequest
from ..pdfcompat import pymupdf
from .common import parse_page_ranges, to_fitz_rect, validate_pages


def rotate_pages(doc: "pymupdf.Document", pages: Sequence[int], degrees: int) -> int:
    """Rotate pages by a relative amount. ``degrees`` is rounded to a quarter turn."""
    step = int(round(degrees / 90.0)) * 90
    targets = validate_pages(pages, doc.page_count)
    for page_no in targets:
        page = doc[page_no - 1]
        page.set_rotation((page.rotation + step) % 360)
    return len(targets)


def delete_pages(doc: "pymupdf.Document", pages: Sequence[int]) -> int:
    targets = validate_pages(pages, doc.page_count)
    if not targets:
        return 0
    if len(targets) >= doc.page_count:
        raise ValueError("Cannot delete every page — a PDF needs at least one page")
    doc.delete_pages([p - 1 for p in targets])
    return len(targets)


def reorder_pages(doc: "pymupdf.Document", order: Sequence[int]) -> None:
    """Apply a new page order. ``order`` must be a permutation of 1..page_count."""
    total = doc.page_count
    wanted = [int(p) for p in order]
    if sorted(wanted) != list(range(1, total + 1)):
        raise ValueError(
            f"Page order must list each of the {total} pages exactly once "
            f"(got {len(wanted)} entries)"
        )
    doc.select([p - 1 for p in wanted])


def move_page(doc: "pymupdf.Document", page: int, to_index: int) -> None:
    total = doc.page_count
    if not 1 <= page <= total:
        raise ValueError(f"Page {page} is out of range")
    to_index = max(1, min(total, int(to_index)))
    order = [p for p in range(1, total + 1) if p != page]
    order.insert(to_index - 1, page)
    reorder_pages(doc, order)


def duplicate_pages(doc: "pymupdf.Document", pages: Sequence[int]) -> int:
    targets = validate_pages(pages, doc.page_count)
    # Work back-to-front so earlier insertions don't shift later indices.
    for page_no in sorted(targets, reverse=True):
        doc.fullcopy_page(page_no - 1, page_no)
    return len(targets)


def insert_blank_pages(
    doc: "pymupdf.Document",
    after_page: int = 0,
    count: int = 1,
    width: Optional[float] = None,
    height: Optional[float] = None,
) -> int:
    count = max(1, int(count))
    if width is None or height is None:
        ref = doc[min(max(after_page, 1), doc.page_count) - 1].rect
        width = width or ref.width
        height = height or ref.height
    index = max(0, min(doc.page_count, int(after_page)))
    for offset in range(count):
        doc.new_page(pno=index + offset, width=width, height=height)
    return count


def merge_documents(
    doc: "pymupdf.Document",
    others: Sequence[Tuple[str, bytes]],
    insert_after: Optional[int] = None,
) -> int:
    """Append other PDFs into ``doc``. Returns the number of pages added."""
    added = 0
    start_at = doc.page_count if insert_after is None else max(0, min(doc.page_count, insert_after))
    for _name, data in others:
        src = pymupdf.open(stream=data, filetype="pdf")
        try:
            if src.is_encrypted and not src.authenticate(""):
                raise ValueError("One of the documents to merge is password protected")
            doc.insert_pdf(src, start_at=start_at)
            start_at += src.page_count
            added += src.page_count
        finally:
            src.close()
    return added


def crop_pages(
    doc: "pymupdf.Document", pages: Sequence[int], rect: Rect, unit: str = "pdf"
) -> int:
    targets = validate_pages(pages, doc.page_count)
    for page_no in targets:
        page = doc[page_no - 1]
        bounds = page.rect
        if unit == "ratio":
            box = pymupdf.Rect(
                bounds.x0 + rect.x0 * bounds.width,
                bounds.y0 + rect.y0 * bounds.height,
                bounds.x0 + rect.x1 * bounds.width,
                bounds.y0 + rect.y1 * bounds.height,
            )
        else:
            box = to_fitz_rect(rect)
        box = box & bounds
        if box.is_empty or box.width < 2 or box.height < 2:
            raise ValueError(f"Crop box is empty on page {page_no}")
        # set_cropbox works in unrotated coordinates.
        page.set_cropbox(box)
    return len(targets)


def reset_crop(doc: "pymupdf.Document", pages: Sequence[int]) -> int:
    targets = validate_pages(pages, doc.page_count)
    for page_no in targets:
        page = doc[page_no - 1]
        page.set_cropbox(page.mediabox)
    return len(targets)


def extract_pages(doc: "pymupdf.Document", pages: Sequence[int]) -> bytes:
    """Return a new PDF containing only ``pages``, in the order given."""
    targets = [p for p in pages if 1 <= p <= doc.page_count]
    if not targets:
        raise ValueError("No valid pages selected")
    out = pymupdf.open()
    try:
        for page_no in targets:
            out.insert_pdf(doc, from_page=page_no - 1, to_page=page_no - 1)
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()


def split_document(
    doc: "pymupdf.Document", req: SplitRequest, base_name: str
) -> List[Tuple[str, bytes]]:
    """Split into parts. Returns ``[(filename, pdf_bytes), ...]``."""
    total = doc.page_count
    stem = Path(base_name).stem or "document"
    groups: List[List[int]] = []

    if req.mode == "every_n":
        size = max(1, int(req.every_n))
        for start in range(1, total + 1, size):
            groups.append(list(range(start, min(start + size, total + 1))))
    elif req.mode == "at_pages":
        cuts = sorted({p for p in req.at_pages if 1 < p <= total})
        start = 1
        for cut in cuts:
            groups.append(list(range(start, cut)))
            start = cut
        groups.append(list(range(start, total + 1)))
    else:  # ranges
        for spec in req.ranges:
            pages = sorted(parse_page_ranges(spec, total))
            if pages:
                groups.append(pages)

    groups = [g for g in groups if g]
    if not groups:
        raise ValueError("Split produced no output — check the page ranges")

    results: List[Tuple[str, bytes]] = []
    width = len(str(len(groups)))
    for idx, pages in enumerate(groups, start=1):
        label = f"{pages[0]}" if len(pages) == 1 else f"{pages[0]}-{pages[-1]}"
        name = f"{stem}_part{idx:0{width}d}_p{label}.pdf"
        results.append((name, extract_pages(doc, pages)))
    return results


def page_thumbnails(doc: "pymupdf.Document", width_px: int = 120) -> List[Dict]:
    """Small preview data for the page rail."""
    out: List[Dict] = []
    for idx in range(doc.page_count):
        page = doc[idx]
        rect = page.rect
        scale = width_px / rect.width if rect.width else 1.0
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        out.append(
            {
                "page_number": idx + 1,
                "width": pix.width,
                "height": pix.height,
                "png": pix.tobytes("png"),
            }
        )
    return out
