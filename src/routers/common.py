"""Shared helpers for the route modules."""

from __future__ import annotations

import io
import zipfile
from typing import Any, Callable, Dict, List, Sequence, Tuple

from fastapi import HTTPException

from ..models import DocumentInfo, OperationResult
from ..session import STORE


def get_document_or_404(doc_id: str):
    try:
        return STORE.get(doc_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def describe_or_404(doc_id: str) -> DocumentInfo:
    try:
        return STORE.describe(doc_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def run_mutation(doc_id: str, fn: Callable, message: str = "Done") -> OperationResult:
    """Apply an edit, translating domain errors into clean HTTP responses."""
    get_document_or_404(doc_id)
    try:
        data = STORE.mutate(doc_id, fn)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    payload: Dict[str, Any] | None
    if data is None:
        payload = None
    elif isinstance(data, dict):
        payload = data
    else:
        payload = {"result": data}

    return OperationResult(
        status="success",
        message=message,
        document=describe_or_404(doc_id),
        data=payload,
    )


def zip_bytes(files: Sequence[Tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        used: Dict[str, int] = {}
        for name, data in files:
            safe = name
            if safe in used:
                used[safe] += 1
                stem, _, ext = safe.rpartition(".")
                safe = f"{stem}_{used[name]}.{ext}" if stem else f"{safe}_{used[name]}"
            else:
                used[safe] = 0
            archive.writestr(safe, data)
    return buffer.getvalue()
