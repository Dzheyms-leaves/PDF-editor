"""Engine discovery and selection.

``probe()`` results are cached because some of them shell out or import torch.
Call :func:`refresh` after changing OCR settings.
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

from .. import config
from ..models import OcrCapabilities, OcrEngineInfo
from .base import OcrEngine, OcrError
from .deepseek import DeepSeekOcrEngine
from .fallback import RapidOcrEngine, TesseractEngine
from .native import NativeTextEngine
from .remote import RemoteOcrEngine

_LOCK = threading.RLock()
_ENGINES: Dict[str, OcrEngine] = {}
_PROBED: Optional[Dict[str, bool]] = None


def _engines() -> Dict[str, OcrEngine]:
    global _ENGINES
    if not _ENGINES:
        with _LOCK:
            if not _ENGINES:
                for cls in (
                    NativeTextEngine,
                    DeepSeekOcrEngine,
                    RemoteOcrEngine,
                    RapidOcrEngine,
                    TesseractEngine,
                ):
                    engine = cls()
                    _ENGINES[engine.name] = engine
    return _ENGINES


def refresh() -> None:
    """Drop cached probe results (after a settings change or an install)."""
    global _PROBED
    with _LOCK:
        _PROBED = None


def _probe_all() -> Dict[str, bool]:
    global _PROBED
    if _PROBED is None:
        with _LOCK:
            if _PROBED is None:
                results: Dict[str, bool] = {}
                for name, engine in _engines().items():
                    try:
                        results[name] = bool(engine.probe())
                    except Exception:  # noqa: BLE001 - a probe must never break startup
                        results[name] = False
                _PROBED = results
    return _PROBED


def list_engines(include_native: bool = True) -> List[OcrEngineInfo]:
    probed = _probe_all()
    out: List[OcrEngineInfo] = []
    for name, engine in _engines().items():
        if name == "native" and not include_native:
            continue
        out.append(
            OcrEngineInfo(
                name=name,
                label=engine.label,
                available=probed.get(name, False),
                priority=engine.priority,
                device=engine.device,
                detail=engine.detail,
                install_hint=engine.install_hint,
                supports_layout=engine.supports_layout,
                supports_markdown=engine.supports_markdown,
            )
        )
    out.sort(key=lambda e: (-e.priority, e.name))
    return out


def best_engine_name(exclude_native: bool = True) -> Optional[str]:
    """Highest-priority available engine, ignoring the native text reader."""
    probed = _probe_all()
    candidates = [
        (engine.priority, name)
        for name, engine in _engines().items()
        if probed.get(name) and not (exclude_native and name == "native")
    ]
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def get_engine(name: Optional[str] = None, *, allow_native: bool = False) -> OcrEngine:
    """Resolve an engine by name, or auto-select the best available one."""
    engines = _engines()
    requested = (name or config.get_setting("ocr_engine", "auto") or "auto").strip().lower()

    if requested in {"", "auto"}:
        chosen = best_engine_name(exclude_native=not allow_native)
        if chosen is None:
            raise OcrError(
                "No OCR engine is available. Install one of: "
                "rapidocr-onnxruntime (CPU, easiest), Tesseract, or DeepSeek-OCR with a CUDA GPU."
            )
        return engines[chosen]

    engine = engines.get(requested)
    if engine is None:
        raise OcrError(f"Unknown OCR engine '{requested}'")
    if not _probe_all().get(requested):
        raise OcrError(
            f"{engine.label} is not available on this machine: "
            f"{engine.detail or 'unknown reason'}. {engine.install_hint}".strip()
        )
    return engine


def capabilities() -> OcrCapabilities:
    """Everything the UI needs to explain the OCR situation to the user."""
    engines = list_engines()
    gpu_available = False
    gpu_name: Optional[str] = None
    torch_version: Optional[str] = None
    try:
        import torch  # noqa: PLC0415

        torch_version = str(torch.__version__)
        if torch.cuda.is_available():
            gpu_available = True
            gpu_name = torch.cuda.get_device_name(0)
    except Exception:  # noqa: BLE001
        pass

    return OcrCapabilities(
        engines=engines,
        selected=best_engine_name(),
        gpu_available=gpu_available,
        gpu_name=gpu_name,
        torch_version=torch_version,
    )
