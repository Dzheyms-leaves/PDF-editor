"""Job pack assembly: turn a pile of PDFs into one issued document.

Builds the deliverable an as-built or O&M pack actually needs — a cover sheet
carrying the project and revision, a linked table of contents, PDF bookmarks
matching it, continuous page numbering and a title-block footer on every sheet.

The page numbering is the fiddly part: the contents page can only be written
once the front matter's own length is known, so sources are measured first, the
front matter is sized from that, and every reference is then offset by it.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..pdfcompat import Point, Rect, pymupdf

A4 = (595.0, 842.0)
MARGIN = 56.0

FONT = "helv"
FONT_BOLD = "hebo"

INK = (0.11, 0.11, 0.10)
MUTED = (0.42, 0.41, 0.38)
RULE = (0.78, 0.77, 0.74)
ACCENT = (0.62, 0.44, 0.18)

TOC_FIRST_PAGE = 24        # entries that fit on the first contents page
TOC_LATER_PAGE = 32        # entries on any continuation page

POSITIONS = ("bottom-left", "bottom-centre", "bottom-right",
             "top-left", "top-centre", "top-right")


@dataclass
class Section:
    """One source document placed into the pack."""

    title: str
    data: bytes
    start: int = 0         # 0-based page index within the content body
    pages: int = 0
    outline: List[Tuple[int, str, int]] = field(default_factory=list)


def _text_width(text: str, size: float, font: str = FONT) -> float:
    return pymupdf.get_text_length(text, fontname=font, fontsize=size)


def _fit(text: str, width: float, size: float, font: str = FONT) -> str:
    """Truncate with an ellipsis so a long title cannot overrun its column."""
    if _text_width(text, size, font) <= width:
        return text
    trimmed = text
    while trimmed and _text_width(f"{trimmed}...", size, font) > width:
        trimmed = trimmed[:-1]
    return f"{trimmed}..." if trimmed else ""


def _line(page: Any, x: float, y: float, text: str, *, size: float = 10.0,
          font: str = FONT, color: Sequence[float] = INK, align: str = "left",
          width: float = 0.0) -> None:
    """Draw a single line on its baseline.

    ``insert_text`` is used rather than ``insert_textbox`` because it places a
    baseline directly and cannot silently drop the string when a box turns out
    to be a fraction too short.
    """
    if not text:
        return
    if align == "right":
        x = x + width - _text_width(text, size, font)
    elif align == "centre":
        x = x + (width - _text_width(text, size, font)) / 2
    page.insert_text(Point(x, y), text, fontname=font, fontsize=size, color=color)


# ------------------------------------------------------------------- cover

def _draw_cover(doc: Any, cover: Dict[str, Any], logo: Optional[bytes],
                total_sections: int, total_pages: int) -> None:
    page = doc.new_page(width=A4[0], height=A4[1])
    right = A4[0] - MARGIN
    width = right - MARGIN

    top = MARGIN + 30
    if logo:
        try:
            image = pymupdf.Pixmap(logo)
            ratio = image.height / image.width if image.width else 0.3
            box_w = min(190.0, width)
            box = Rect(MARGIN, top, MARGIN + box_w, top + box_w * ratio)
            page.insert_image(box, stream=logo, keep_proportion=True)
            top = box.y1 + 34
        except Exception:      # noqa: BLE001 - a bad logo must not lose the pack
            pass

    shape = page.new_shape()
    shape.draw_line(Point(MARGIN, top), Point(right, top))
    shape.finish(color=ACCENT, width=2.0)
    shape.commit()

    cursor = top + 42
    title = cover.get("title") or "Job pack"
    size = 26.0
    while _text_width(title, size, FONT_BOLD) > width and size > 12:
        size -= 1
    _line(page, MARGIN, cursor, title, size=size, font=FONT_BOLD)
    cursor += size + 14

    subtitle = cover.get("project") or ""
    if subtitle:
        _line(page, MARGIN, cursor, subtitle, size=13, color=MUTED)
        cursor += 26

    cursor += 26
    rows = [
        ("Client", cover.get("client", "")),
        ("Reference", cover.get("reference", "")),
        ("Revision", cover.get("revision", "")),
        ("Date", cover.get("date") or _dt.date.today().isoformat()),
        ("Prepared by", cover.get("prepared_by", "")),
        ("Contents", f"{total_sections} document{'' if total_sections == 1 else 's'}, "
                     f"{total_pages} page{'' if total_pages == 1 else 's'}"),
    ]
    for label, value in rows:
        if not value:
            continue
        _line(page, MARGIN, cursor, label.upper(), size=8, color=MUTED)
        _line(page, MARGIN + 110, cursor, str(value), size=11, font=FONT_BOLD)
        cursor += 22

    notes = (cover.get("notes") or "").strip()
    if notes:
        cursor += 18
        page.insert_textbox(Rect(MARGIN, cursor, right, cursor + 190), notes,
                            fontname=FONT, fontsize=10, color=INK, lineheight=1.5)

    shape = page.new_shape()
    shape.draw_line(Point(MARGIN, A4[1] - MARGIN), Point(right, A4[1] - MARGIN))
    shape.finish(color=RULE, width=0.6)
    shape.commit()


# ---------------------------------------------------------------- contents

def toc_page_count(entries: int) -> int:
    if entries <= TOC_FIRST_PAGE:
        return 1
    return 1 + -(-(entries - TOC_FIRST_PAGE) // TOC_LATER_PAGE)


def _draw_contents(doc: Any, sections: Sequence[Section], offset: int,
                   first_page: int) -> None:
    """Write the contents onto pages already reserved at ``first_page``.

    The pages have to exist, and so do their targets, before the rows can be
    linked — a GOTO link to a page the document has not got yet is rejected.
    """
    right = A4[0] - MARGIN
    width = right - MARGIN
    index = 0
    sheet = 0

    while index < len(sections):
        page = doc[first_page + sheet]
        sheet += 1
        first = index == 0
        cursor = MARGIN + 26

        if first:
            _line(page, MARGIN, cursor, "CONTENTS", size=15, font=FONT_BOLD)
            cursor += 12
            shape = page.new_shape()
            shape.draw_line(Point(MARGIN, cursor), Point(right, cursor))
            shape.finish(color=ACCENT, width=1.4)
            shape.commit()
            cursor += 28
        else:
            _line(page, MARGIN, cursor, "CONTENTS (continued)", size=11,
                  font=FONT_BOLD, color=MUTED)
            cursor += 24

        capacity = TOC_FIRST_PAGE if first else TOC_LATER_PAGE
        for _ in range(capacity):
            if index >= len(sections):
                break
            section = sections[index]
            printed = section.start + offset + 1
            number = str(printed)
            number_w = _text_width(number, 10, FONT_BOLD)

            title = _fit(section.title, width - number_w - 34, 10.5)
            _line(page, MARGIN, cursor, title, size=10.5)
            _line(page, right - number_w, cursor, number, size=10, font=FONT_BOLD,
                  color=ACCENT)

            # A dotted leader, drawn only across the gap between the two.
            gap_start = MARGIN + _text_width(title, 10.5) + 6
            gap_end = right - number_w - 6
            if gap_end > gap_start:
                shape = page.new_shape()
                shape.draw_line(Point(gap_start, cursor - 3), Point(gap_end, cursor - 3))
                shape.finish(color=RULE, width=0.5, dashes="[0.6 2.4] 0")
                shape.commit()

            page.insert_link({
                "kind": pymupdf.LINK_GOTO,
                "from": Rect(MARGIN, cursor - 11, right, cursor + 4),
                "page": section.start + offset,
            })
            cursor += 21
            index += 1


# ----------------------------------------------------------------- furniture

def stamp_furniture(doc: Any, first_page: int, footer: str, numbers: bool,
                     fmt: str, position: str, start_number: int) -> None:
    """Add the page numbers and title-block footer to the body pages."""
    total = doc.page_count - first_page
    for offset in range(first_page, doc.page_count):
        page = doc[offset]
        bounds = page.rect
        number = start_number + (offset - first_page)

        top_edge = position.startswith("top")
        y = bounds.y0 + 34 if top_edge else bounds.y1 - 28
        left = bounds.x0 + 40
        span = bounds.width - 80
        if span <= 0:
            continue

        shape = page.new_shape()
        rule_y = y - 12 if top_edge else y - 14
        shape.draw_line(Point(left, rule_y), Point(left + span, rule_y))
        shape.finish(color=RULE, width=0.5)
        shape.commit()

        if footer:
            _line(page, left, y, _fit(footer, span * 0.66, 8), size=8, color=MUTED)
        if numbers:
            label = (fmt or "Page {page} of {total}").format(
                page=number, total=total + start_number - 1, n=number)
            align = "left" if position.endswith("left") else (
                "centre" if position.endswith("centre") else "right")
            _line(page, left, y, label, size=8, color=MUTED, align=align, width=span)


# -------------------------------------------------------------------- build

def build_pack(sources: Sequence[Tuple[str, bytes]], *, cover: Optional[Dict[str, Any]] = None,
               logo: Optional[bytes] = None, contents: bool = True,
               bookmarks: bool = True, page_numbers: bool = True,
               number_format: str = "Page {page} of {total}",
               number_position: str = "bottom-right", footer: str = "",
               start_number: int = 1) -> bytes:
    """Assemble sources into one issued pack.

    ``sources`` is a sequence of ``(title, pdf_bytes)``. Returns the finished
    PDF; the caller owns naming and delivery.
    """
    if not sources:
        raise ValueError("There are no documents to assemble")
    if number_position not in POSITIONS:
        raise ValueError(f"Unknown number position '{number_position}'")

    sections: List[Section] = []
    body = pymupdf.open()
    try:
        for title, data in sources:
            try:
                source = pymupdf.open(stream=data, filetype="pdf")
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"{title}: not a readable PDF ({exc})") from exc
            with source:
                if not source.page_count:
                    continue
                section = Section(title=title or "Untitled", data=data,
                                  start=body.page_count, pages=source.page_count)
                # Keep each source's own bookmarks, rebased onto the pack.
                for level, name, page_no in (source.get_toc() or []):
                    if page_no >= 1:
                        section.outline.append((level + 1, name, section.start + page_no - 1))
                sections.append(section)
                body.insert_pdf(source)

        if not sections:
            raise ValueError("Every document in the pack was empty")

        front = (1 if cover else 0) + (toc_page_count(len(sections)) if contents else 0)

        out = pymupdf.open()
        try:
            if cover:
                _draw_cover(out, cover, logo, len(sections), body.page_count)

            # Reserve the contents sheets, then fill them once the body they
            # point at is actually in the document.
            toc_start = out.page_count
            toc_pages = toc_page_count(len(sections)) if contents else 0
            for _ in range(toc_pages):
                out.new_page(width=A4[0], height=A4[1])
            out.insert_pdf(body)
            if contents:
                _draw_contents(out, sections, front, toc_start)

            if bookmarks:
                toc: List[List[Any]] = []
                if contents:
                    toc.append([1, "Contents", (1 if cover else 0) + 1])
                for section in sections:
                    toc.append([1, section.title, section.start + front + 1])
                    for level, name, page_no in section.outline:
                        toc.append([level, name, page_no + front + 1])
                out.set_toc(toc)

            if page_numbers or footer:
                stamp_furniture(out, front, footer, page_numbers, number_format,
                                 number_position, start_number)

            out.set_metadata({
                "title": (cover or {}).get("title") or "Job pack",
                "subject": (cover or {}).get("project", ""),
                "author": (cover or {}).get("prepared_by", ""),
                "creator": "PDF Workbench",
            })
            return out.tobytes(garbage=3, deflate=True)
        finally:
            out.close()
    finally:
        body.close()


def pack_outline(sources: Sequence[Tuple[str, int]], *, cover: bool = True,
                 contents: bool = True) -> List[Dict[str, Any]]:
    """Preview where each document will land, without building the pack."""
    front = (1 if cover else 0) + (toc_page_count(len(sources)) if contents else 0)
    rows: List[Dict[str, Any]] = []
    cursor = 0
    for title, pages in sources:
        rows.append({"title": title, "pages": pages, "start": cursor + front + 1})
        cursor += pages
    return rows
