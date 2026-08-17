"""Pluggable OCR backends.

Engines are discovered at runtime so the app runs on a bare laptop and gets
faster and more accurate as heavier dependencies (torch, DeepSeek-OCR) become
available. Nothing here is imported eagerly — importing torch costs seconds.
"""

from .base import OcrEngine, OcrError, RenderedPage  # noqa: F401
from .registry import get_engine, list_engines, capabilities  # noqa: F401
