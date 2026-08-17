"""Operations that run across many documents at once.

Every operation here is a pure ``bytes -> bytes`` transform on one PDF. The
router decides what to do with the results — zip them up for download, or write
them back into the session where they land on the undo stack like any other
edit — so nothing in this module needs to know about the store.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..models import PageSelection
from ..pdfcompat import pymupdf
from ..pdfops import annots, pageops, secure
from . import pack

OPERATIONS = ("stamp", "watermark", "rotate", "optimise", "flatten", "scrub")


def _open(data: bytes, label: str) -> Any:
    try:
        return pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{label}: not a readable PDF ({exc})") from exc


def _save(doc: Any) -> bytes:
    return doc.tobytes(garbage=3, deflate=True)


# ------------------------------------------------------------- operations

def stamp(data: bytes, *, footer: str = "", numbers: bool = True,
          number_format: str = "Page {page} of {total}",
          position: str = "bottom-right", start_number: int = 1,
          skip_first: bool = False, label: str = "document") -> bytes:
    """Add page numbers and a title-block footer to one document."""
    if position not in pack.POSITIONS:
        raise ValueError(f"Unknown number position '{position}'")
    doc = _open(data, label)
    with doc:
        pack.stamp_furniture(doc, 1 if skip_first else 0, footer, numbers,
                             number_format, position, start_number)
        return _save(doc)


def watermark(data: bytes, *, text: str, opacity: float = 0.15,
              rotation: float = 45.0, colour: str = "#b00020",
              font_size: float = 60.0, position: str = "center",
              label: str = "document") -> bytes:
    """Stamp a text watermark across every page."""
    if not text.strip():
        raise ValueError("A watermark needs some text")
    doc = _open(data, label)
    with doc:
        secure.add_watermark(doc, PageSelection(mode="all"), text=text,
                             opacity=opacity, rotation=rotation, colour=colour,
                             font_size=font_size, position=position)
        return _save(doc)


def rotate(data: bytes, *, degrees: int = 90, label: str = "document") -> bytes:
    if degrees % 90:
        raise ValueError("Rotation must be a multiple of 90 degrees")
    doc = _open(data, label)
    with doc:
        pageops.rotate_pages(doc, list(range(1, doc.page_count + 1)), degrees)
        return _save(doc)


def optimise(data: bytes, *, label: str = "document") -> bytes:
    """Garbage-collect and recompress; typically the cheapest size win."""
    doc = _open(data, label)
    with doc:
        return doc.tobytes(garbage=4, deflate=True, deflate_images=True,
                           deflate_fonts=True, clean=True)


def flatten(data: bytes, *, label: str = "document") -> bytes:
    """Bake annotations into the page so markup cannot be edited away."""
    doc = _open(data, label)
    with doc:
        annots.flatten_annotations(doc)
        return _save(doc)


def scrub(data: bytes, *, label: str = "document") -> bytes:
    """Strip metadata before a document leaves the office."""
    doc = _open(data, label)
    with doc:
        secure.scrub_document_metadata(doc)
        return _save(doc)


_HANDLERS: Dict[str, Callable[..., bytes]] = {
    "stamp": stamp,
    "watermark": watermark,
    "rotate": rotate,
    "optimise": optimise,
    "flatten": flatten,
    "scrub": scrub,
}


def apply(operation: str, data: bytes, params: Dict[str, Any], label: str) -> bytes:
    """Run one named operation over one document."""
    handler = _HANDLERS.get(operation)
    if handler is None:
        raise ValueError(f"Unknown batch operation '{operation}'")
    return handler(data, label=label, **params)


# ------------------------------------------------------------------ splits

def split(data: bytes, *, every: int = 0, ranges: str = "",
          label: str = "document") -> List[Tuple[str, bytes]]:
    """Break one document into several, named after the original.

    Either ``every`` N pages, or an explicit ``ranges`` spec like
    ``1-4, 5, 9-12``. Returns ``(filename, bytes)`` pairs.
    """
    doc = _open(data, label)
    stem = re.sub(r"\.pdf$", "", label, flags=re.I) or "document"
    out: List[Tuple[str, bytes]] = []
    with doc:
        total = doc.page_count
        groups: List[List[int]] = []

        if ranges.strip():
            for chunk in ranges.split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                if "-" in chunk:
                    start, _, end = chunk.partition("-")
                    try:
                        first, last = int(start), int(end)
                    except ValueError as exc:
                        raise ValueError(f"'{chunk}' is not a page range") from exc
                else:
                    try:
                        first = last = int(chunk)
                    except ValueError as exc:
                        raise ValueError(f"'{chunk}' is not a page number") from exc
                first, last = max(1, first), min(total, last)
                if first > last:
                    raise ValueError(f"'{chunk}' is outside this document")
                groups.append(list(range(first, last + 1)))
        else:
            step = max(1, int(every or 1))
            for start in range(1, total + 1, step):
                groups.append(list(range(start, min(start + step, total + 1))))

        if not groups:
            raise ValueError("That split would produce nothing")

        for index, pages in enumerate(groups, start=1):
            part = pymupdf.open()
            with part:
                for page_no in pages:
                    part.insert_pdf(doc, from_page=page_no - 1, to_page=page_no - 1)
                span = (f"{pages[0]}" if len(pages) == 1
                        else f"{pages[0]}-{pages[-1]}")
                out.append((f"{stem}_p{span}.pdf", _save(part)))
    return out


# ----------------------------------------------------------------- renaming

TOKENS = ("{name}", "{n}", "{nn}", "{pages}", "{date}", "{project}", "{rev}")


def rename(pattern: str, label: str, index: int, pages: int,
           project: str = "", revision: str = "") -> str:
    """Expand a naming pattern for one document.

    Tokens: ``{name}`` original stem, ``{n}`` / ``{nn}`` counter, ``{pages}``
    page count, ``{date}`` today, ``{project}``, ``{rev}``.
    """
    stem = re.sub(r"\.pdf$", "", label, flags=re.I) or "document"
    filled = (pattern or "{name}")
    for token, value in (
        ("{name}", stem),
        ("{nn}", f"{index:02d}"),
        ("{n}", str(index)),
        ("{pages}", str(pages)),
        ("{date}", _dt.date.today().isoformat()),
        ("{project}", project),
        ("{rev}", revision),
    ):
        filled = filled.replace(token, value)

    filled = re.sub(r'[<>:"/\\|?*]', "-", filled).strip()
    filled = re.sub(r"\s+", " ", filled)
    if not filled:
        filled = stem
    return filled if filled.lower().endswith(".pdf") else f"{filled}.pdf"
