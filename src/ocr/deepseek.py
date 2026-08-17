"""DeepSeek-OCR backend.

DeepSeek-OCR is a vision-language document model: it reads a page image and
emits markdown, optionally with ``<|ref|>``/``<|det|>`` grounding boxes. It is
loaded through ``transformers`` with ``trust_remote_code`` because the repo
ships its own modelling code.

Realistically it wants a CUDA GPU with roughly 8 GB of VRAM. The registry only
offers this engine when a suitable device is present, so a laptop without one
silently falls through to a lighter backend instead of hanging.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional, Tuple

from .. import config
from ..models import OcrPageResult
from .base import OcrEngine, OcrError, RenderedPage, markdown_to_plain, parse_grounded_output

# Resolution presets from the DeepSeek-OCR model card.
# "gundam" is the dynamic tiling mode and is the right default for dense pages
# such as purchase orders.
MODES = {
    "tiny":   {"base_size": 512,  "image_size": 512, "crop_mode": False},
    "small":  {"base_size": 640,  "image_size": 640, "crop_mode": False},
    "base":   {"base_size": 1024, "image_size": 1024, "crop_mode": False},
    "large":  {"base_size": 1280, "image_size": 1280, "crop_mode": False},
    "gundam": {"base_size": 1024, "image_size": 640,  "crop_mode": True},
}

PROMPTS = {
    "markdown": "<image>\n<|grounding|>Convert the document to markdown.",
    "plain": "<image>\nFree OCR.",
    "grounded": "<image>\n<|grounding|>OCR this image.",
}


class DeepSeekOcrEngine(OcrEngine):
    name = "deepseek"
    label = "DeepSeek-OCR"
    priority = 100
    supports_layout = True
    supports_markdown = True
    install_hint = (
        "Needs an NVIDIA GPU plus: pip install torch --index-url "
        "https://download.pytorch.org/whl/cu121 && pip install transformers tokenizers einops "
        "addict easydict"
    )

    def __init__(self) -> None:
        super().__init__()
        self._model: Any = None
        self._tokenizer: Any = None
        self._load_lock = threading.Lock()
        self._load_error: Optional[str] = None

    # ------------------------------------------------------------ probing

    def _resolve_device(self) -> Tuple[Optional[str], str]:
        """Return ``(device, detail)``; device is None when unusable."""
        try:
            import torch  # noqa: PLC0415 - deliberately lazy
        except ImportError:
            return None, "PyTorch is not installed"

        preference = str(config.get_setting("deepseek_device", "auto")).lower()

        if preference in {"auto", "cuda"} and torch.cuda.is_available():
            try:
                name = torch.cuda.get_device_name(0)
                vram = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
                if vram < 6.0 and preference == "auto":
                    return None, f"{name} has only {vram:.1f} GB VRAM (needs ~8 GB)"
                return "cuda", f"{name} · {vram:.1f} GB VRAM"
            except Exception as exc:  # noqa: BLE001
                return None, f"CUDA present but unusable: {exc}"

        if preference == "cuda":
            return None, "CUDA requested but no CUDA device is available"

        if preference == "mps" and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps", "Apple Metal (experimental — the model targets CUDA)"

        if preference == "cpu":
            return "cpu", "CPU only — expect 30 s to several minutes per page"

        # preference == "auto" and no GPU: do not silently pick CPU, it is too
        # slow to be a sensible automatic choice.
        return None, "No CUDA GPU detected (set OCR device to 'cpu' to force it)"

    def probe(self) -> bool:
        if self._load_error:
            self._detail = self._load_error
            return False

        device, detail = self._resolve_device()
        self._device = device
        self._detail = detail
        if device is None:
            return False

        try:
            import transformers  # noqa: F401, PLC0415
        except ImportError:
            self._detail = "transformers is not installed"
            return False

        local = str(config.get_setting("deepseek_local_path", "") or "").strip()
        if local and not Path(local).exists():
            self._detail = f"Local model path not found: {local}"
            return False

        self._detail = f"{detail} · mode '{config.get_setting('deepseek_mode', 'gundam')}'"
        return True

    # ------------------------------------------------------------ loading

    def _model_source(self) -> str:
        local = str(config.get_setting("deepseek_local_path", "") or "").strip()
        if local:
            return local
        return str(config.get_setting("deepseek_model_id", "deepseek-ai/DeepSeek-OCR"))

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            device, detail = self._resolve_device()
            if device is None:
                raise OcrError(f"DeepSeek-OCR cannot run here: {detail}")

            try:
                import torch  # noqa: PLC0415
                from transformers import AutoModel, AutoTokenizer  # noqa: PLC0415
            except ImportError as exc:
                raise OcrError(f"DeepSeek-OCR needs transformers and torch: {exc}") from exc

            source = self._model_source()
            os.environ.setdefault("HF_HOME", str(config.MODEL_CACHE_DIR))

            try:
                tokenizer = AutoTokenizer.from_pretrained(source, trust_remote_code=True)

                load_kwargs: dict[str, Any] = {
                    "trust_remote_code": True,
                    "use_safetensors": True,
                }
                # flash-attention gives a large speedup but is an optional build;
                # fall back to eager attention when it is missing.
                try:
                    import flash_attn  # noqa: F401, PLC0415
                    load_kwargs["_attn_implementation"] = "flash_attention_2"
                except ImportError:
                    load_kwargs["_attn_implementation"] = "eager"

                model = AutoModel.from_pretrained(source, **load_kwargs)
                model = model.eval()
                if device == "cuda":
                    model = model.cuda().to(torch.bfloat16)
                elif device == "mps":
                    model = model.to("mps").to(torch.float16)
                else:
                    model = model.to(torch.float32)

                self._model = model
                self._tokenizer = tokenizer
                self._device = device
            except Exception as exc:  # noqa: BLE001
                self._load_error = str(exc)
                raise OcrError(f"Could not load DeepSeek-OCR from '{source}': {exc}") from exc

    def warmup(self) -> None:
        self._ensure_loaded()

    # --------------------------------------------------------------- work

    def recognise(
        self,
        page: RenderedPage,
        mode: str = "markdown",
        prompt: Optional[str] = None,
    ) -> OcrPageResult:
        self._ensure_loaded()

        preset = MODES.get(
            str(config.get_setting("deepseek_mode", "gundam")).lower(), MODES["gundam"]
        )
        text_prompt = prompt or PROMPTS.get(mode, PROMPTS["markdown"])
        started = time.perf_counter()

        # ``infer`` takes a path, so the rendered page goes to a scratch file.
        with tempfile.TemporaryDirectory(prefix="pdfwb_ocr_") as tmp:
            image_path = Path(tmp) / f"page_{page.page_number}.png"
            image_path.write_bytes(page.png)
            out_dir = Path(tmp) / "out"
            out_dir.mkdir(exist_ok=True)

            try:
                raw = self._model.infer(
                    self._tokenizer,
                    prompt=text_prompt,
                    image_file=str(image_path),
                    output_path=str(out_dir),
                    base_size=preset["base_size"],
                    image_size=preset["image_size"],
                    crop_mode=preset["crop_mode"],
                    save_results=False,
                    test_compress=False,
                )
            except TypeError:
                # Older/newer revisions of the remote code vary in signature.
                raw = self._model.infer(
                    self._tokenizer,
                    prompt=text_prompt,
                    image_file=str(image_path),
                    output_path=str(out_dir),
                )
            except Exception as exc:  # noqa: BLE001
                raise OcrError(f"DeepSeek-OCR failed on page {page.page_number}: {exc}") from exc

            text = self._coerce_output(raw, out_dir)

        markdown, blocks = parse_grounded_output(text, page)
        plain = markdown_to_plain(markdown)

        return OcrPageResult(
            page_number=page.page_number,
            engine=self.name,
            text=plain,
            markdown=markdown,
            blocks=blocks,
            width=page.page_width,
            height=page.page_height,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    @staticmethod
    def _coerce_output(raw: Any, out_dir: Path) -> str:
        """``infer`` may return the text, or write it to ``output_path``."""
        if isinstance(raw, str) and raw.strip():
            return raw
        if isinstance(raw, dict):
            for key in ("text", "result", "output"):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        if isinstance(raw, (list, tuple)) and raw:
            first = raw[0]
            if isinstance(first, str):
                return first
        for candidate in sorted(out_dir.glob("**/*")):
            if candidate.suffix.lower() in {".txt", ".md", ".mmd"}:
                try:
                    content = candidate.read_text("utf-8", errors="replace")
                    if content.strip():
                        return content
                except OSError:
                    continue
        return ""
