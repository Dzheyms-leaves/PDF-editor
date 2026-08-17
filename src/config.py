"""Runtime paths and user-tunable settings.

Everything the app writes lives under one workspace directory so a packaged
build (PyInstaller one-file) never tries to write next to the frozen binary.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def bundle_dir() -> Path:
    """Directory holding read-only bundled assets (static/, vendor/)."""
    if _is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def workspace_dir() -> Path:
    """Writable directory for uploads, jobs, templates and settings."""
    override = os.environ.get("PDF_WORKBENCH_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if _is_frozen() or os.environ.get("PDF_WORKBENCH_USER_HOME"):
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        return base / "PDFWorkbench"
    return Path(__file__).resolve().parent.parent / "workspace"


BUNDLE_DIR = bundle_dir()
STATIC_DIR = BUNDLE_DIR / "static"
WORKSPACE = workspace_dir()
UPLOAD_DIR = WORKSPACE / "uploads"
JOB_DIR = WORKSPACE / "jobs"
TEMPLATE_DIR = WORKSPACE / "templates"
MODEL_CACHE_DIR = WORKSPACE / "models"
SETTINGS_PATH = WORKSPACE / "settings.json"

for _d in (WORKSPACE, UPLOAD_DIR, JOB_DIR, TEMPLATE_DIR, MODEL_CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)


DEFAULT_SETTINGS: Dict[str, Any] = {
    # --- OCR ---
    # "auto" walks the registry in priority order and picks the best engine that
    # reports itself available on this machine.
    "ocr_engine": "auto",
    "ocr_render_dpi": 200,
    # DeepSeek-OCR resolution preset: tiny | small | base | large | gundam.
    "deepseek_mode": "gundam",
    "deepseek_model_id": "deepseek-ai/DeepSeek-OCR",
    # Leave empty to let transformers use its default cache.
    "deepseek_local_path": "",
    "deepseek_device": "auto",  # auto | cuda | mps | cpu
    "ocr_remote_url": "",
    "ocr_remote_api_key": "",
    # --- Extraction ---
    # Used to work out which party on a purchase order is *us*, so the other
    # one gets reported as the counterparty.
    "my_company_names": ["Automated Electrical Solutions", "AES"],
    # --- Viewer ---
    "preview_dpi": 110,
    "max_upload_mb": 300,
    # --- Editing ---
    "autosave_revisions": True,
    "max_revisions": 40,
}


def load_settings() -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            stored = json.loads(SETTINGS_PATH.read_text("utf-8"))
            if isinstance(stored, dict):
                settings.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
        except (json.JSONDecodeError, OSError):
            pass
    return settings


def save_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    current = load_settings()
    current.update({k: v for k, v in values.items() if k in DEFAULT_SETTINGS})
    SETTINGS_PATH.write_text(json.dumps(current, indent=2), "utf-8")
    return current


def get_setting(key: str, default: Any = None) -> Any:
    return load_settings().get(key, DEFAULT_SETTINGS.get(key, default))
