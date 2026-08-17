"""Page operations, annotations, editing, redaction and the stamper."""

from __future__ import annotations

import pytest

from src.models import (
    AnnotationSpec,
    LogoConfig,
    PageSelection,
    RedactionSpec,
    Rect,
    SplitRequest,
)
from src.pdfcompat import pymupdf
from src.pdfops import annots, detector, pageops, secure, stamper, textedit
from src.pdfops.common import hex_to_rgb, parse_page_ranges, resolve_pages


def open_bytes(data: bytes):
    return pymupdf.open(stream=data, filetype="pdf")


# ----------------------------------------------------------------- helpers

@pytest.mark.parametrize("spec,total,expected", [
    ("1,3", 5, {1, 3}),
    ("2-4", 5, {2, 3, 4}),
    ("4-2", 5, {2, 3, 4}),          # reversed ranges still work
    ("1, 9", 5, {1}),               # out-of-range entries dropped
    ("", 5, set()),
    ("nonsense", 5, set()),
])
def test_parse_page_ranges(spec, total, expected):
    assert parse_page_ranges(spec, total) == expected


def test_resolve_pages_modes():
    assert resolve_pages(PageSelection(mode="all"), 3) == [1, 2, 3]
    assert resolve_pages(PageSelection(mode="last"), 3) == [3]
    assert resolve_pages(PageSelection(mode="even"), 4) == [2, 4]
    assert resolve_pages(PageSelection(mode="all_except_first"), 3) == [2, 3]


def test_hex_to_rgb():
    assert hex_to_rgb("#ffffff") == (1.0, 1.0, 1.0)
    assert hex_to_rgb("#000") == (0.0, 0.0, 0.0)
    assert hex_to_rgb("nope", (0.5, 0.5, 0.5)) == (0.5, 0.5, 0.5)


# -------------------------------------------------------------- page ops

def test_rotate_and_delete(simple_pdf):
    doc = open_bytes(simple_pdf)
    assert pageops.rotate_pages(doc, [1, 2], 90) == 2
    assert doc[0].rotation == 90
    assert pageops.delete_pages(doc, [4]) == 1
    assert doc.page_count == 3
    doc.close()


def test_cannot_delete_every_page(simple_pdf):
    doc = open_bytes(simple_pdf)
    with pytest.raises(ValueError, match="at least one page"):
        pageops.delete_pages(doc, [1, 2, 3, 4])
    doc.close()


def test_reorder_requires_a_permutation(simple_pdf):
    doc = open_bytes(simple_pdf)
    pageops.reorder_pages(doc, [4, 3, 2, 1])
    assert "Hello page 4" in doc[0].get_text()
    with pytest.raises(ValueError, match="exactly once"):
        pageops.reorder_pages(doc, [1, 1, 2, 3])
    doc.close()


def test_move_page(simple_pdf):
    doc = open_bytes(simple_pdf)
    pageops.move_page(doc, 1, 4)
    assert "Hello page 1" in doc[3].get_text()
    doc.close()


def test_insert_and_duplicate(simple_pdf):
    doc = open_bytes(simple_pdf)
    pageops.insert_blank_pages(doc, after_page=0, count=2)
    assert doc.page_count == 6
    assert doc[0].get_text().strip() == ""
    pageops.duplicate_pages(doc, [3])
    assert doc.page_count == 7
    doc.close()


def test_split_and_extract(simple_pdf):
    doc = open_bytes(simple_pdf)
    parts = pageops.split_document(doc, SplitRequest(mode="every_n", every_n=2), "doc.pdf")
    assert len(parts) == 2
    for _name, data in parts:
        part = open_bytes(data)
        assert part.page_count == 2
        part.close()

    single = open_bytes(pageops.extract_pages(doc, [2]))
    assert single.page_count == 1
    assert "Hello page 2" in single[0].get_text()
    single.close()
    doc.close()


def test_merge(simple_pdf):
    doc = open_bytes(simple_pdf)
    added = pageops.merge_documents(doc, [("other.pdf", simple_pdf)])
    assert added == 4
    assert doc.page_count == 8
    doc.close()


def test_crop_shrinks_the_page(simple_pdf):
    doc = open_bytes(simple_pdf)
    before = doc[0].rect.width
    pageops.crop_pages(doc, [1], Rect(x0=0.1, y0=0.1, x1=0.9, y1=0.9), unit="ratio")
    assert doc[0].rect.width < before
    doc.close()


# ------------------------------------------------------------ annotations

@pytest.mark.parametrize("spec", [
    AnnotationSpec(kind="highlight", page=1, rect=Rect(x0=70, y0=100, x1=200, y1=125)),
    AnnotationSpec(kind="underline", page=1, rect=Rect(x0=70, y0=100, x1=200, y1=125)),
    AnnotationSpec(kind="strikeout", page=1, rect=Rect(x0=70, y0=100, x1=200, y1=125)),
    AnnotationSpec(kind="note", page=1, rect=Rect(x0=300, y0=100, x1=320, y1=120), text="hi"),
    AnnotationSpec(kind="rect", page=1, rect=Rect(x0=70, y0=200, x1=200, y1=260)),
    AnnotationSpec(kind="circle", page=1, rect=Rect(x0=70, y0=280, x1=200, y1=340)),
    AnnotationSpec(kind="arrow", page=1, rect=Rect(x0=70, y0=360, x1=200, y1=400)),
    AnnotationSpec(kind="freetext", page=1, rect=Rect(x0=70, y0=420, x1=300, y1=460), text="note"),
    AnnotationSpec(kind="ink", page=1, points=[[(70, 500), (120, 520), (170, 500)]]),
])
def test_every_annotation_kind_round_trips(simple_pdf, spec):
    doc = open_bytes(simple_pdf)
    ref = annots.add_annotation(doc, spec)
    assert ref.index >= 0
    assert len(annots.list_annotations(doc, 1)) == 1
    doc.close()


def test_delete_and_flatten_annotations(simple_pdf):
    doc = open_bytes(simple_pdf)
    for offset in range(3):
        annots.add_annotation(doc, AnnotationSpec(
            kind="rect", page=1,
            rect=Rect(x0=70, y0=200 + offset * 40, x1=200, y1=230 + offset * 40),
        ))
    assert len(annots.list_annotations(doc, 1)) == 3
    assert annots.delete_annotations(doc, 1, [0, 2]) == 2
    assert len(annots.list_annotations(doc, 1)) == 1
    assert annots.flatten_annotations(doc, [1]) == 1
    assert annots.list_annotations(doc, 1) == []
    doc.close()


def test_ink_needs_two_points(simple_pdf):
    doc = open_bytes(simple_pdf)
    with pytest.raises(ValueError):
        annots.add_annotation(doc, AnnotationSpec(kind="ink", page=1, points=[[(10, 10)]]))
    doc.close()


# --------------------------------------------------------------- editing

def test_edit_text_replaces_in_place(simple_pdf):
    doc = open_bytes(simple_pdf)
    result = textedit.edit_text(doc, 1, Rect(x0=68, y0=104, x1=260, y1=126), "Replaced text")
    assert "Hello page 1" in result["replaced"]
    text = doc[0].get_text()
    assert "Replaced text" in text
    assert "Hello page 1" not in text
    doc.close()


def test_find_and_replace_across_pages(simple_pdf):
    doc = open_bytes(simple_pdf)
    result = textedit.find_and_replace(doc, "Hello", "Goodbye", PageSelection(mode="all"))
    assert result["replaced"] == 4
    for index in range(4):
        assert "Goodbye" in doc[index].get_text()
    doc.close()


def test_delete_content_removes_text(simple_pdf):
    doc = open_bytes(simple_pdf)
    textedit.delete_content(doc, 1, Rect(x0=68, y0=104, x1=260, y1=126))
    assert "Hello page 1" not in doc[0].get_text()
    doc.close()


def test_add_image(simple_pdf, logo_png):
    doc = open_bytes(simple_pdf)
    textedit.add_image(doc, 1, Rect(x0=300, y0=500, x1=420, y1=540), logo_png)
    assert len(textedit.list_images(doc, 1)) == 1
    doc.close()


# -------------------------------------------------------------- security

def test_redaction_actually_removes_text(simple_pdf):
    doc = open_bytes(simple_pdf)
    targets = secure.find_redaction_targets(
        doc, ["/SECRET-\\d+/"], PageSelection(mode="all")
    )
    assert len(targets) == 4
    secure.apply_redactions(doc, targets)
    for index in range(4):
        assert "SECRET" not in doc[index].get_text()
    doc.close()


def test_redaction_scrubs_metadata(simple_pdf):
    doc = open_bytes(simple_pdf)
    doc.set_metadata({"title": "SECRET-000 report"})
    secure.apply_redactions(
        doc, [RedactionSpec(page=1, rect=Rect(x0=68, y0=150, x1=200, y1=172))]
    )
    assert not (doc.metadata or {}).get("title")
    doc.close()


def test_watermark_and_bates(simple_pdf):
    doc = open_bytes(simple_pdf)
    assert secure.add_watermark(doc, PageSelection(mode="all"), text="DRAFT") == 4
    result = secure.add_bates_numbers(
        doc, PageSelection(mode="all"), prefix="AES-", start=1, digits=5
    )
    assert result["first"] == "AES-00001"
    assert result["last"] == "AES-00004"
    assert "AES-00001" in doc[0].get_text()
    doc.close()


def test_password_round_trip(simple_pdf):
    protected = secure.set_password(simple_pdf, user_pw="hunter2")
    locked = open_bytes(protected)
    assert locked.is_encrypted
    locked.close()
    cleared = secure.remove_password(protected, "hunter2")
    opened = open_bytes(cleared)
    assert opened.page_count == 4
    opened.close()
    with pytest.raises(ValueError, match="Incorrect password"):
        secure.remove_password(protected, "wrong")


# --------------------------------------------------------------- stamping

def test_detector_finds_a_clean_spot(simple_pdf, logo_png):
    doc = open_bytes(simple_pdf)
    placements = detector.analyze_document_placements(doc, LogoConfig(), logo_aspect=3.0)
    assert len(placements) == 4
    assert all(p.placed for p in placements)
    # A mostly empty page should get the top-priority strategy.
    assert placements[0].strategy_used == "bottom-right"
    doc.close()


def test_detector_respects_aspect_ratio(simple_pdf):
    doc = open_bytes(simple_pdf)
    config = LogoConfig(width_pt=200, height_pt=200, maintain_aspect_ratio=True)
    placement = detector.analyze_document_placements(doc, config, logo_aspect=3.0)[0]
    ratio = placement.rect.width / placement.rect.height
    assert ratio == pytest.approx(3.0, rel=0.02)
    doc.close()


def test_detector_avoids_content(logo_png):
    """A page with a full-width block at the bottom must not be stamped over it."""
    doc = pymupdf.open()
    page = doc.new_page()
    block = pymupdf.Rect(20, page.rect.height - 90, page.rect.width - 20, page.rect.height - 20)
    page.draw_rect(block, fill=(0.2, 0.2, 0.2))
    placement = detector.analyze_document_placements(doc, LogoConfig(), 3.0)[0]
    if placement.placed:
        overlap = detector.rects_overlap(
            (placement.rect.x0, placement.rect.y0, placement.rect.x1, placement.rect.y1),
            (block.x0, block.y0, block.x1, block.y1),
        )
        assert not overlap
    doc.close()


def test_stamp_writes_the_logo(simple_pdf, logo_png):
    doc = open_bytes(simple_pdf)
    placements = detector.analyze_document_placements(doc, LogoConfig(), 3.0)
    count, final = stamper.stamp_document(doc, logo_png, placements)
    assert count == 4
    assert all(p.placed for p in final)
    assert len(doc[0].get_images()) == 1
    doc.close()


def test_manual_override_wins(simple_pdf, logo_png):
    doc = open_bytes(simple_pdf)
    placements = detector.analyze_document_placements(doc, LogoConfig(), 3.0)
    override = Rect(x0=50, y0=50, x1=170, y1=90)
    count, final = stamper.stamp_document(doc, logo_png, placements, {1: override})
    assert count == 4
    assert final[0].is_manual_override
    assert final[0].rect.x0 == 50
    doc.close()
