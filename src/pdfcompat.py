"""Single import point for PyMuPDF.

PyMuPDF renamed its module from ``fitz`` to ``pymupdf`` in 1.24 and now emits a
deprecation warning for the old name. Importing through here means the rest of
the codebase never has to care which one is installed.
"""

from __future__ import annotations

try:  # PyMuPDF >= 1.24
    import pymupdf  # type: ignore
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz as pymupdf  # type: ignore

# Re-exported so callers can write ``from .pdfcompat import fitz``.
fitz = pymupdf

Document = pymupdf.Document
Page = pymupdf.Page
Rect = pymupdf.Rect
Point = pymupdf.Point
Matrix = pymupdf.Matrix

VERSION = getattr(pymupdf, "__doc__", "") or ""


def version_tuple() -> tuple[int, ...]:
    """Return the PyMuPDF version as a comparable tuple, e.g. ``(1, 28, 2)``."""
    raw = getattr(pymupdf, "VersionBind", None) or getattr(pymupdf, "__version__", "0.0.0")
    parts: list[int] = []
    for chunk in str(raw).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


__all__ = [
    "pymupdf",
    "fitz",
    "Document",
    "Page",
    "Rect",
    "Point",
    "Matrix",
    "version_tuple",
]
