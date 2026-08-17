"""Call a DeepSeek-OCR (or compatible) HTTP endpoint.

Useful when the model runs on a workshop server or another machine with a GPU.
Only enabled when a URL has been configured, since it sends page images off the
local machine.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Dict, Optional

from .. import config
from ..models import OcrPageResult
from .base import OcrEngine, OcrError, RenderedPage, markdown_to_plain, parse_grounded_output


class RemoteOcrEngine(OcrEngine):
    name = "remote"
    label = "Remote OCR endpoint"
    priority = 80
    supports_layout = True
    supports_markdown = True
    install_hint = "Set an endpoint URL in Settings → OCR"

    def probe(self) -> bool:
        url = str(config.get_setting("ocr_remote_url", "") or "").strip()
        if not url:
            self._detail = "No endpoint configured"
            return False
        try:
            import httpx  # noqa: F401, PLC0415
        except ImportError:
            self._detail = "httpx is not installed"
            return False
        self._device = "remote"
        self._detail = f"POSTs page images to {url}"
        return True

    def recognise(
        self, page: RenderedPage, mode: str = "markdown", prompt: Optional[str] = None
    ) -> OcrPageResult:
        import httpx  # noqa: PLC0415

        url = str(config.get_setting("ocr_remote_url", "") or "").strip()
        if not url:
            raise OcrError("No remote OCR endpoint is configured")
        api_key = str(config.get_setting("ocr_remote_api_key", "") or "").strip()

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: Dict[str, Any] = {
            "image_base64": base64.b64encode(page.png).decode("ascii"),
            "mode": mode,
            "page_number": page.page_number,
        }
        if prompt:
            payload["prompt"] = prompt

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=180.0) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
        except Exception as exc:  # noqa: BLE001
            raise OcrError(f"Remote OCR request failed: {exc}") from exc

        raw = ""
        if isinstance(body, str):
            raw = body
        elif isinstance(body, dict):
            for key in ("markdown", "text", "result", "output", "content"):
                value = body.get(key)
                if isinstance(value, str) and value.strip():
                    raw = value
                    break

        markdown, blocks = parse_grounded_output(raw, page)
        return OcrPageResult(
            page_number=page.page_number,
            engine=self.name,
            text=markdown_to_plain(markdown),
            markdown=markdown,
            blocks=blocks,
            width=page.page_width,
            height=page.page_height,
            duration_ms=int((time.perf_counter() - started) * 1000),
            warning=None if raw else "The endpoint returned no text",
        )
