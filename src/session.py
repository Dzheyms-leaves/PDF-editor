"""In-process document session: open PDFs, image assets, jobs and undo history.

Documents are held on disk (not as long-lived ``Document`` handles) so that a
crash or reload never leaves a half-written file, and so undo/redo is just a
stack of byte snapshots.
"""

from __future__ import annotations

import io
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image

from . import config
from .models import AssetInfo, DocumentInfo, PageInfo
from .pdfcompat import pymupdf


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def safe_filename(name: str, fallback: str = "document.pdf") -> str:
    base = Path(name or fallback).name.strip()
    cleaned = "".join(c for c in base if c.isalnum() or c in " ._-()[]+&#@,")
    cleaned = cleaned.strip(" .") or fallback
    return cleaned[:180]


@dataclass
class DocEntry:
    doc_id: str
    filename: str
    path: Path
    total_pages: int = 0
    undo_stack: List[bytes] = field(default_factory=list)
    redo_stack: List[bytes] = field(default_factory=list)
    revision: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)
    meta: Dict[str, Any] = field(default_factory=dict)


class DocumentStore:
    """Owns every open document, asset and job for the running app."""

    def __init__(self) -> None:
        self.documents: Dict[str, DocEntry] = {}
        self.assets: Dict[str, Dict[str, Any]] = {}
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- docs

    def add_pdf_bytes(self, data: bytes, filename: str) -> DocumentInfo:
        if not data:
            raise ValueError("Uploaded file is empty (0 bytes)")

        doc_id = _new_id("d")
        clean = safe_filename(filename, "document.pdf")
        if not clean.lower().endswith(".pdf"):
            clean += ".pdf"
        path = config.UPLOAD_DIR / f"{doc_id}_{clean}"

        # Validate before we commit it to the store.
        try:
            probe = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            raise ValueError(f"Could not read PDF: {exc}") from exc

        try:
            if probe.is_encrypted and not probe.authenticate(""):
                raise ValueError(
                    "This PDF is password protected. Remove the password or supply it first."
                )
            if probe.page_count == 0:
                raise ValueError("PDF contains no pages")
            pages = probe.page_count
        finally:
            probe.close()

        path.write_bytes(data)
        entry = DocEntry(doc_id=doc_id, filename=clean, path=path, total_pages=pages)
        with self._lock:
            self.documents[doc_id] = entry
        return self.describe(doc_id)

    def get(self, doc_id: str) -> DocEntry:
        entry = self.documents.get(doc_id)
        if entry is None:
            raise KeyError(f"Unknown document '{doc_id}'")
        return entry

    def open_doc(self, doc_id: str) -> "pymupdf.Document":
        """Open a fresh handle. Caller closes it."""
        entry = self.get(doc_id)
        return pymupdf.open(entry.path)

    def read_bytes(self, doc_id: str) -> bytes:
        return self.get(doc_id).path.read_bytes()

    def describe(self, doc_id: str) -> DocumentInfo:
        entry = self.get(doc_id)
        doc = pymupdf.open(entry.path)
        try:
            pages: List[PageInfo] = []
            text_pages = 0
            for idx in range(doc.page_count):
                page = doc[idx]
                raw = page.get_text("text").strip()
                has_text = len(raw) >= 12
                if has_text:
                    text_pages += 1
                rect = page.rect
                pages.append(
                    PageInfo(
                        page_number=idx + 1,
                        width=round(rect.width, 2),
                        height=round(rect.height, 2),
                        rotation=page.rotation,
                        has_text=has_text,
                    )
                )
            has_form = bool(doc.is_form_pdf)
            coverage = (text_pages / doc.page_count) if doc.page_count else 0.0
            entry.total_pages = doc.page_count
        finally:
            doc.close()

        return DocumentInfo(
            doc_id=entry.doc_id,
            filename=entry.filename,
            total_pages=entry.total_pages,
            pages=pages,
            has_form_fields=has_form,
            text_coverage=round(coverage, 3),
            revision=entry.revision,
            can_undo=bool(entry.undo_stack),
            can_redo=bool(entry.redo_stack),
        )

    def remove(self, doc_id: str) -> None:
        with self._lock:
            entry = self.documents.pop(doc_id, None)
        if entry and entry.path.exists():
            entry.path.unlink(missing_ok=True)

    def list_documents(self) -> List[DocumentInfo]:
        return [self.describe(d) for d in list(self.documents.keys())]

    # ------------------------------------------------------------- editing

    def mutate(
        self,
        doc_id: str,
        fn: Callable[["pymupdf.Document"], Any],
        *,
        record_undo: bool = True,
    ) -> Any:
        """Open the document, apply ``fn``, and atomically persist the result.

        The original bytes are pushed onto the undo stack first, so any failure
        inside ``fn`` leaves the stored file untouched.
        """
        entry = self.get(doc_id)
        with entry.lock:
            original = entry.path.read_bytes()
            doc = pymupdf.open(stream=original, filetype="pdf")
            try:
                result = fn(doc)
                if doc.page_count == 0:
                    raise ValueError("Operation would leave the document with no pages")
                out = doc.tobytes(garbage=3, deflate=True)
            finally:
                doc.close()

            if record_undo:
                entry.undo_stack.append(original)
                max_rev = int(config.get_setting("max_revisions", 40))
                if len(entry.undo_stack) > max_rev:
                    entry.undo_stack.pop(0)
                entry.redo_stack.clear()

            self._write_atomic(entry.path, out)
            entry.revision += 1
            entry.total_pages = self._page_count(out)
            return result

    def replace_bytes(self, doc_id: str, data: bytes, *, record_undo: bool = True) -> None:
        entry = self.get(doc_id)
        with entry.lock:
            if record_undo:
                entry.undo_stack.append(entry.path.read_bytes())
                entry.redo_stack.clear()
            self._write_atomic(entry.path, data)
            entry.revision += 1
            entry.total_pages = self._page_count(data)

    def undo(self, doc_id: str) -> bool:
        entry = self.get(doc_id)
        with entry.lock:
            if not entry.undo_stack:
                return False
            entry.redo_stack.append(entry.path.read_bytes())
            data = entry.undo_stack.pop()
            self._write_atomic(entry.path, data)
            entry.revision += 1
            entry.total_pages = self._page_count(data)
            return True

    def redo(self, doc_id: str) -> bool:
        entry = self.get(doc_id)
        with entry.lock:
            if not entry.redo_stack:
                return False
            entry.undo_stack.append(entry.path.read_bytes())
            data = entry.redo_stack.pop()
            self._write_atomic(entry.path, data)
            entry.revision += 1
            entry.total_pages = self._page_count(data)
            return True

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    @staticmethod
    def _page_count(data: bytes) -> int:
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            return doc.page_count
        finally:
            doc.close()

    # -------------------------------------------------------------- assets

    def add_asset(self, data: bytes, filename: str, kind: str = "image") -> AssetInfo:
        asset_id = _new_id("a")
        clean = safe_filename(filename, "asset.png")
        path = config.UPLOAD_DIR / f"{asset_id}_{clean}"
        path.write_bytes(data)

        width, height = 100, 100
        try:
            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
        except Exception:  # noqa: BLE001 - SVG and friends have no PIL size
            pass

        info = AssetInfo(
            asset_id=asset_id,
            filename=clean,
            width=width,
            height=height,
            aspect_ratio=(width / height) if height else 1.0,
            kind=kind,
        )
        self.assets[asset_id] = {"info": info, "path": path, "bytes": data}
        return info

    def asset_bytes(self, asset_id: str) -> bytes:
        rec = self.assets.get(asset_id)
        if not rec:
            raise KeyError(f"Unknown asset '{asset_id}'")
        return rec["bytes"]

    def asset_info(self, asset_id: str) -> AssetInfo:
        rec = self.assets.get(asset_id)
        if not rec:
            raise KeyError(f"Unknown asset '{asset_id}'")
        return rec["info"]

    # ---------------------------------------------------------------- jobs

    def new_job_dir(self) -> Tuple[str, Path]:
        job_id = _new_id("j")
        job_dir = config.JOB_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_id, job_dir

    def register_job(self, job_id: str, **data: Any) -> None:
        self.jobs[job_id] = data

    # --------------------------------------------------------------- reset

    def reset(self) -> None:
        with self._lock:
            self.documents.clear()
            self.assets.clear()
            self.jobs.clear()
        for directory in (config.UPLOAD_DIR, config.JOB_DIR):
            for item in directory.glob("*"):
                try:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                except OSError:
                    pass


STORE = DocumentStore()
