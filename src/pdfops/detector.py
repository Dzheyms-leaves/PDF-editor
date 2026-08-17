"""Smart whitespace detection for logo placement.

Ported from the original ``pdf_logo_stamper`` with three fixes:

* Raster verification used to run only when a page had *no* vector obstacles,
  so scanned pages with a single detected element skipped the pixel check
  entirely. It now verifies the winning candidate on every page.
* The logo's true aspect ratio is honoured when ``maintain_aspect_ratio`` is
  set, instead of stretching to the configured box.
* Obstacle extraction no longer discards a whole page's text when one block
  contains unencodable characters.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..models import LogoConfig, ObstacleBox, PagePlacement, Rect
from ..pdfcompat import pymupdf
from .common import legacy_page_selection

Box = Tuple[float, float, float, float]


def rects_overlap(a: Box, b: Box) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def sanitize_text(text: Optional[str], max_len: int = 40) -> str:
    """Strip surrogates and control characters that would break JSON encoding."""
    if not text:
        return ""
    try:
        cleaned = text.encode("utf-8", "replace").decode("utf-8", "ignore")
        cleaned = "".join(c for c in cleaned if c.isprintable() or c in " \t\n")
        return cleaned.strip()[:max_len]
    except Exception:  # noqa: BLE001
        return ""


def extract_page_obstacles(page: "pymupdf.Page", config: LogoConfig) -> List[ObstacleBox]:
    """Collect text blocks, vector drawings and images as keep-out boxes."""
    obstacles: List[ObstacleBox] = []
    page_w, page_h = page.rect.width, page.rect.height

    def _is_full_page(rect) -> bool:
        return rect.width > page_w * 0.92 and rect.height > page_h * 0.92

    try:
        for block in page.get_text("blocks"):
            x0, y0, x1, y1, text, _no, block_type = block[:7]
            if block_type != 0:
                continue
            label = sanitize_text(text)
            if not label:
                continue
            obstacles.append(
                ObstacleBox(
                    x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1),
                    obstacle_type="text", label=label,
                )
            )
    except Exception:  # noqa: BLE001 - a damaged text tree shouldn't stop placement
        pass

    try:
        for drawing in page.get_drawings():
            rect = drawing.get("rect")
            if not rect or _is_full_page(rect):
                continue
            if rect.width <= 0.1 or rect.height <= 0.1:
                continue
            obstacles.append(
                ObstacleBox(
                    x0=float(rect.x0), y0=float(rect.y0), x1=float(rect.x1), y1=float(rect.y1),
                    obstacle_type="drawing", label="Drawing",
                )
            )
    except Exception:  # noqa: BLE001
        pass

    try:
        for info in page.get_images(full=True):
            for rect in page.get_image_rects(info[0]):
                if _is_full_page(rect):
                    continue
                obstacles.append(
                    ObstacleBox(
                        x0=float(rect.x0), y0=float(rect.y0),
                        x1=float(rect.x1), y1=float(rect.y1),
                        obstacle_type="image", label="Image",
                    )
                )
    except Exception:  # noqa: BLE001
        pass

    return obstacles


def check_pixel_whitespace(
    page: "pymupdf.Page", candidate: Box, dark_pixel_threshold: float = 0.05
) -> bool:
    """Render the candidate area and report whether it is visually empty."""
    try:
        clip = pymupdf.Rect(*candidate) & page.rect
        if clip.is_empty or clip.width < 1 or clip.height < 1:
            return True
        pix = page.get_pixmap(clip=clip, dpi=72, alpha=False)
        if pix.width == 0 or pix.height == 0:
            return True
        data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n >= 3:
            gray = 0.299 * data[:, :, 0] + 0.587 * data[:, :, 1] + 0.114 * data[:, :, 2]
        else:
            gray = data[:, :, 0]
        dark_ratio = float(np.count_nonzero(gray < 235)) / float(pix.width * pix.height)
        return dark_ratio <= dark_pixel_threshold
    except Exception:  # noqa: BLE001 - never block placement on a render failure
        return True


def is_candidate_free(
    candidate: Box,
    obstacles: Sequence[ObstacleBox],
    content_padding: float,
    page_w: float,
    page_h: float,
    margin_side: float,
    margin_bottom: float,
) -> bool:
    """Vector-only collision and margin test."""
    cx0, cy0, cx1, cy1 = candidate
    if cx0 < margin_side or cx1 > (page_w - margin_side):
        return False
    if cy1 > (page_h - margin_bottom) or cy0 < 0:
        return False

    for obs in obstacles:
        padded = (
            obs.x0 - content_padding,
            obs.y0 - content_padding,
            obs.x1 + content_padding,
            obs.y1 + content_padding,
        )
        if rects_overlap(candidate, padded):
            return False
    return True


def _scale_steps(min_scale: float) -> List[float]:
    steps = [1.0]
    current = 0.9
    while current >= min_scale - 1e-9:
        steps.append(round(current, 2))
        current -= 0.1
    return steps


def find_best_placement(
    page: "pymupdf.Page",
    config: LogoConfig,
    obstacles: List[ObstacleBox],
    logo_aspect: Optional[float] = None,
    verify_pixels: bool = True,
) -> PagePlacement:
    """Choose the best logo rect for one page."""
    page_w = float(page.rect.width)
    page_h = float(page.rect.height)
    page_num = page.number + 1

    base_w = float(config.width_pt)
    base_h = float(config.height_pt)
    if config.maintain_aspect_ratio and logo_aspect and logo_aspect > 0:
        # Fit the logo inside the configured box without distorting it.
        if base_w / base_h > logo_aspect:
            base_w = base_h * logo_aspect
        else:
            base_h = base_w / logo_aspect

    search_top = page_h * (1.0 - config.search_band_ratio)
    band = [
        obs for obs in obstacles
        if obs.y1 >= (search_top - config.content_padding) and obs.y0 <= page_h
    ]

    def _accept(candidate: Box) -> bool:
        if not is_candidate_free(
            candidate, band, config.content_padding, page_w, page_h,
            config.margin_side, config.margin_bottom,
        ):
            return False
        if verify_pixels and not check_pixel_whitespace(page, candidate):
            return False
        return True

    for scale in _scale_steps(config.min_scale):
        w, h = base_w * scale, base_h * scale
        bottom = page_h - config.margin_bottom

        for strategy in config.strategy_priority:
            candidate: Optional[Box] = None

            if strategy == "bottom-right":
                x1 = page_w - config.margin_side
                candidate = (x1 - w, bottom - h, x1, bottom)
            elif strategy == "bottom-left":
                x0 = config.margin_side
                candidate = (x0, bottom - h, x0 + w, bottom)
            elif strategy == "bottom-center":
                x0 = (page_w - w) / 2.0
                candidate = (x0, bottom - h, x0 + w, bottom)
            elif strategy == "best-fit":
                candidate = _grid_search(page, config, band, w, h, page_w, page_h,
                                         search_top, verify_pixels)

            if candidate is not None and _accept(candidate):
                return PagePlacement(
                    page_number=page_num,
                    page_width=page_w,
                    page_height=page_h,
                    placed=True,
                    strategy_used=strategy,
                    rect=Rect(x0=candidate[0], y0=candidate[1], x1=candidate[2], y1=candidate[3]),
                    scale=scale,
                    opacity=config.opacity,
                    message=f"Clean spot via {strategy} at {int(scale * 100)}% scale",
                    obstacles=obstacles,
                )

    return _fallback_placement(config, page_num, page_w, page_h, base_w, base_h, obstacles)


def _grid_search(
    page: "pymupdf.Page",
    config: LogoConfig,
    band: Sequence[ObstacleBox],
    w: float,
    h: float,
    page_w: float,
    page_h: float,
    search_top: float,
    verify_pixels: bool,
) -> Optional[Box]:
    """Sweep the bottom band for the cleanest void, preferring low and outboard."""
    x_min = config.margin_side
    x_max = page_w - config.margin_side - w
    y_max = page_h - config.margin_bottom - h
    y_min = search_top
    if x_max < x_min or y_max < y_min:
        return None

    xs = np.linspace(x_min, x_max, num=max(2, int((x_max - x_min) / 15)))
    ys = np.linspace(y_max, y_min, num=max(2, int((y_max - y_min) / 10)))

    best: Optional[Box] = None
    best_score = -float("inf")
    for cy in ys:
        for cx in xs:
            cand = (float(cx), float(cy), float(cx) + w, float(cy) + h)
            if not is_candidate_free(
                cand, band, config.content_padding, page_w, page_h,
                config.margin_side, config.margin_bottom,
            ):
                continue
            dist_from_bottom = page_h - config.margin_bottom - (cy + h)
            side_preference = abs(cx + w / 2.0 - page_w / 2.0)
            score = (1000 - dist_from_bottom * 2) + (side_preference * 0.5)
            if score > best_score:
                if verify_pixels and not check_pixel_whitespace(page, cand):
                    continue
                best_score = score
                best = cand
    return best


def _fallback_placement(
    config: LogoConfig,
    page_num: int,
    page_w: float,
    page_h: float,
    base_w: float,
    base_h: float,
    obstacles: List[ObstacleBox],
) -> PagePlacement:
    bottom = page_h - config.margin_bottom
    right = page_w - config.margin_side

    if config.fallback_behavior == "subtle_overlay":
        return PagePlacement(
            page_number=page_num, page_width=page_w, page_height=page_h, placed=True,
            strategy_used="fallback-subtle-overlay",
            rect=Rect(x0=right - base_w, y0=bottom - base_h, x1=right, y1=bottom),
            scale=1.0,
            opacity=min(0.28, config.opacity),
            message="Placed as a subtle overlay — the page is crowded",
            obstacles=obstacles,
        )
    if config.fallback_behavior == "shrink_to_fit":
        scale = 0.35
        w, h = base_w * scale, base_h * scale
        return PagePlacement(
            page_number=page_num, page_width=page_w, page_height=page_h, placed=True,
            strategy_used="fallback-shrink",
            rect=Rect(x0=right - w, y0=bottom - h, x1=right, y1=bottom),
            scale=scale, opacity=config.opacity,
            message=f"Shrunk to {int(scale * 100)}% at the bottom-right margin",
            obstacles=obstacles,
        )
    return PagePlacement(
        page_number=page_num, page_width=page_w, page_height=page_h, placed=False,
        strategy_used="none", rect=None, scale=0.0, opacity=0.0,
        message="Skipped — no clear whitespace found in the search band",
        obstacles=obstacles,
    )


def analyze_document_placements(
    doc: "pymupdf.Document",
    config: LogoConfig,
    logo_aspect: Optional[float] = None,
    verify_pixels: bool = True,
) -> List[PagePlacement]:
    """Compute placements for every page, honouring the page-selection rules."""
    total = doc.page_count
    selected = set(legacy_page_selection(config.page_selection, config.custom_pages, total))

    placements: List[PagePlacement] = []
    for idx in range(total):
        page = doc[idx]
        page_num = idx + 1
        obstacles = extract_page_obstacles(page, config)
        if page_num in selected:
            placements.append(
                find_best_placement(page, config, obstacles, logo_aspect, verify_pixels)
            )
        else:
            placements.append(
                PagePlacement(
                    page_number=page_num,
                    page_width=float(page.rect.width),
                    page_height=float(page.rect.height),
                    placed=False, strategy_used="unselected", rect=None,
                    scale=0.0, opacity=0.0,
                    message="Excluded by the page selection rules",
                    obstacles=obstacles,
                )
            )
    return placements
