"""Antumbra designer: part codes, geometry, spec sheet and job handoff."""

from __future__ import annotations

import json

import pytest

from src.designer import catalogue, icons, jobs, render
from src.models import PanelDesign


def design(**overrides) -> dict:
    base = {
        "design_id": "d1",
        "name": "Lobby keypad",
        "family": "B",
        "series": "P",
        "region": "A",
        "buttons": 6,
        "button_finish": "W",
        "rim_finish": "A",
        "backlight": "white",
        "quantity": 1,
        "engraving": [
            {"index": 0, "lines": ["WELCOME"], "icon": "welcome", "icon_side": "left"},
            {"index": 5, "lines": ["ALL", "OFF"], "icon": "all-off", "icon_side": "left"},
        ],
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------- part codes

@pytest.mark.parametrize("family,series,region,buttons,button,rim,expected", [
    ("B", "P", "A", 6, "W", "A", "PA6BPA-WA"),
    ("B", "P", "A", 4, "W", "C", "PA4BPA-WC"),
    ("B", "P", "A", 6, "M", "A", "PA6BPA-MA"),
    ("B", "L", "E", 4, "W", "W", "PA4BLE-WW"),
    ("B", "P", "E", 2, "N", "C", "PA2BPE-NC"),
    ("T", "P", "A", 6, "G", "M", "PA6TPA-GM"),
    ("D", "P", "A", 0, "S", "W", "PADPA-SW"),      # display carries no count
])
def test_part_codes(family, series, region, buttons, button, rim, expected):
    assert catalogue.part_code(family, series, region, buttons, button, rim) == expected


def test_validate_rejects_impossible_combinations():
    with pytest.raises(ValueError, match="only made in"):
        catalogue.validate("D", "L", "A", 0, "W", "A")      # no Lite display
    with pytest.raises(ValueError, match="buttons"):
        catalogue.validate("B", "P", "A", 3, "W", "A")      # odd button count
    with pytest.raises(ValueError, match="button finish"):
        catalogue.validate("B", "P", "A", 6, "Z", "A")
    with pytest.raises(ValueError, match="rim finish"):
        catalogue.validate("B", "P", "A", 6, "W", "Z")


def test_chrome_is_a_rim_only_finish():
    assert "C" in {f.code for f in catalogue.RIM_FINISHES}
    assert "C" not in {f.code for f in catalogue.BUTTON_FINISHES}


# ---------------------------------------------------------------- geometry

def test_layout_fills_the_plate_without_overlap():
    layout = catalogue.layout("B", "P", "A", 6)
    assert (layout["width_mm"], layout["height_mm"]) == (75.0, 116.0)
    assert len(layout["buttons"]) == 6

    for button in layout["buttons"]:
        assert button["x"] >= layout["face"]["x"]
        assert button["y"] >= layout["face"]["y"]
        assert button["x"] + button["w"] <= layout["face"]["x"] + layout["face"]["w"] + 0.01
        assert button["y"] + button["h"] <= layout["face"]["y"] + layout["face"]["h"] + 0.01
        # The engraving area must sit clear of the indicator LED.
        assert button["text"]["x"] > button["led"]["cx"] + button["led"]["r"]
        assert button["text"]["w"] > 0

    # Two columns, three rows, no two buttons sharing a slot.
    positions = {(b["row"], b["column"]) for b in layout["buttons"]}
    assert len(positions) == 6


def test_fewer_buttons_means_taller_buttons():
    six = catalogue.layout("B", "P", "A", 6)["buttons"][0]["h"]
    two = catalogue.layout("B", "P", "A", 2)["buttons"][0]["h"]
    assert two > six


def test_european_plate_is_square_and_display_has_a_screen():
    layout = catalogue.layout("B", "P", "E", 4)
    assert layout["width_mm"] == layout["height_mm"] == 86.0

    display = catalogue.layout("D", "P", "A", 0)
    assert display["buttons"] == []
    assert display["screen"]["w"] > 0


def test_ink_flips_on_dark_finishes():
    assert catalogue.ink_for(catalogue.button_finish("W").hex) == "#2A2A26"
    assert catalogue.ink_for(catalogue.button_finish("N").hex) == "#F0EFEA"


# ------------------------------------------------------------------- icons

def test_every_icon_uses_known_primitives_inside_its_box():
    allowed = {"line", "poly", "fpoly", "circle", "disc"}
    assert icons.ICONS, "the icon library should not be empty"
    for icon in icons.catalogue():
        assert icon["group"] in icons.GROUPS
        for shape in icon["shapes"]:
            assert shape[0] in allowed
            if shape[0] == "line":
                numbers = shape[1:5]
            elif shape[0] in ("poly", "fpoly"):
                numbers = shape[1]
            else:
                numbers = [shape[1], shape[2]]
            assert all(-15 <= value <= 115 for value in numbers), icon["id"]


# -------------------------------------------------------------- spec sheet

def test_spec_sheet_reads_back_its_own_content():
    from src.pdfcompat import pymupdf

    data = render.spec_sheet([design()], "Riverside", "Stage 2", "Acme")
    assert data.startswith(b"%PDF")

    doc = pymupdf.open(stream=data)
    try:
        assert doc.page_count == 1
        text = doc[0].get_text()
    finally:
        doc.close()

    for expected in ("ANTUMBRA ENGRAVING SPECIFICATION", "PA6BPA-WA", "Lobby keypad",
                     "Riverside", "WELCOME", "ENGRAVING SCHEDULE", "75 x 116 mm"):
        assert expected in text, f"{expected!r} missing from the spec sheet"


def test_spec_sheet_is_one_page_per_panel():
    from src.pdfcompat import pymupdf

    data = render.spec_sheet([design(), design(design_id="d2", region="E", buttons=4)])
    doc = pymupdf.open(stream=data)
    try:
        assert doc.page_count == 2
    finally:
        doc.close()


def test_spec_sheet_rejects_an_empty_job():
    with pytest.raises(ValueError):
        render.spec_sheet([])


def test_every_catalogue_combination_renders():
    """A finish or family that cannot be drawn would be a broken order."""
    from src.pdfcompat import pymupdf

    designs = []
    for family in catalogue.FAMILIES:
        for series_code in family.series:
            for region in catalogue.REGIONS:
                counts = family.counts or (0,)
                designs.append(design(
                    design_id=f"{family.code}{series_code}{region.code}",
                    family=family.code, series=series_code, region=region.code,
                    buttons=counts[-1],
                ))
    data = render.spec_sheet(designs)
    doc = pymupdf.open(stream=data)
    try:
        assert doc.page_count == len(designs)
    finally:
        doc.close()


# ------------------------------------------------------------ panel queue

def test_designs_expand_into_one_queue_entry_per_physical_panel():
    model = PanelDesign(**design(quantity=3))
    entries = jobs.to_panel_entries([model])
    assert [e.name for e in entries] == [
        "Lobby keypad (1 of 3)", "Lobby keypad (2 of 3)", "Lobby keypad (3 of 3)"]

    labels = [label.text for row in entries[0].rows for label in row]
    assert labels == ["WELCOME", "ALL OFF"]
    assert all(entry.source_file == "PA6BPA-WA" for entry in entries)


def test_icon_only_buttons_still_carry_a_label():
    model = PanelDesign(**design(engraving=[
        {"index": 0, "lines": [], "icon": "fan"},
    ]))
    entries = jobs.to_panel_entries([model])
    assert entries[0].rows[0][0].text == "[Fan]"


def test_panels_without_engraving_are_not_queued():
    assert jobs.to_panel_entries([PanelDesign(**design(engraving=[]))]) == []


# -------------------------------------------------------------------- API

def test_catalogue_endpoint_describes_the_whole_range(client):
    body = client.get("/api/designer/catalogue").json()
    assert {f["code"] for f in body["families"]} == {"B", "T", "D"}
    assert {f["code"] for f in body["rim_finishes"]} == {"W", "M", "C", "A"}
    assert body["icons"] and body["icon_groups"]
    assert body["limits"]["max_lines"] == 2


def test_check_returns_code_geometry_and_warnings(client):
    body = client.post("/api/designer/check", json=design(engraving=[
        {"index": 0, "lines": ["A LABEL FAR TOO LONG FOR A BUTTON"], "icon": "nope"},
    ])).json()
    assert body["ok"] is True
    assert body["part_code"] == "PA6BPA-WA"
    assert body["product"] == "AntumbraButton"
    assert body["slots"] == 6
    assert len(body["layout"]["buttons"]) == 6
    assert any("longer than" in w for w in body["warnings"])
    assert any("unknown icon" in w for w in body["warnings"])


def test_check_reports_an_impossible_build_without_raising(client):
    body = client.post("/api/designer/check", json=design(family="D", series="L")).json()
    assert body["ok"] is False
    assert body["errors"]


@pytest.mark.parametrize("fmt,head", [
    ("pdf", b"%PDF"),
    ("csv", b"\xef\xbb\xbfPanel,"),
])
def test_export_formats(client, fmt, head):
    response = client.post("/api/designer/export",
                           json={"designs": [design()], "job_name": "riverside", "fmt": fmt})
    assert response.status_code == 200
    assert response.content.startswith(head)
    assert f"riverside.{fmt}" in response.headers["content-disposition"]


def test_export_json_round_trips_through_check(client):
    response = client.post("/api/designer/export",
                           json={"designs": [design()], "job_name": "riverside", "fmt": "json"})
    saved = json.loads(response.content)
    assert saved["job_name"] == "riverside"

    again = client.post("/api/designer/check", json=saved["designs"][0]).json()
    assert again["ok"] and again["part_code"] == "PA6BPA-WA"


def test_export_rejects_an_empty_job(client):
    response = client.post("/api/designer/export", json={"designs": [], "fmt": "pdf"})
    assert response.status_code == 400


def test_panels_endpoint_feeds_the_engraving_queue(client):
    response = client.post("/api/designer/panels", json={"designs": [design(quantity=2)]})
    assert response.status_code == 200
    assert len(response.json()) == 2

    empty = client.post("/api/designer/panels",
                        json={"designs": [design(engraving=[])]})
    assert empty.status_code == 400


# ------------------------------------------------------ bill of materials

def test_bom_groups_identical_configurations():
    from src.designer import bom
    from src.models import PanelDesign

    designs = [
        PanelDesign(**design(design_id="a", name="Suite A", buttons=4, region="E",
                             button_finish="N", rim_finish="C", quantity=12)),
        PanelDesign(**design(design_id="b", name="Suite B", buttons=4, region="E",
                             button_finish="N", rim_finish="C", quantity=8)),
        PanelDesign(**design(design_id="c", name="Lobby", quantity=4)),
    ]
    result = bom.build(designs, price_book={"PA4BPE-NC": 498.0, "PA6BPA-WA": 412.5,
                                            "ENGRAVING": 18.0})

    by_code = {line.part_code: line for line in result.lines}
    assert by_code["PA4BPE-NC"].quantity == 20          # the two suites merge
    assert by_code["PA4BPE-NC"].panels == ["Suite A", "Suite B"]
    assert by_code["PA6BPA-WA"].quantity == 4
    # Every panel here carries engraving, so the labour line counts all of them.
    assert by_code["ENGRAVING"].quantity == 24

    assert result.subtotal == round(20 * 498.0 + 4 * 412.5 + 24 * 18.0, 2)
    assert result.tax == round(result.subtotal * 0.10, 2)
    assert result.total == round(result.subtotal + result.tax, 2)


def test_bom_flags_a_part_with_no_rate_instead_of_pricing_it_free():
    from src.designer import bom
    from src.models import PanelDesign

    result = bom.build([PanelDesign(**design(quantity=3))], price_book={})
    line = result.lines[0]
    assert line.priced is False
    assert line.rate == 0.0
    assert result.unpriced == [line.part_code, "ENGRAVING"]


def test_bom_request_rates_beat_the_price_book():
    from src.designer import bom
    from src.models import PanelDesign

    result = bom.build([PanelDesign(**design())],
                       price_book={"PA6BPA-WA": 400.0},
                       overrides={"PA6BPA-WA": 350.0})
    assert result.lines[0].rate == 350.0


def test_bom_skips_the_engraving_line_for_a_blank_panel():
    from src.designer import bom
    from src.models import PanelDesign

    result = bom.build([PanelDesign(**design(engraving=[]))])
    assert [line.part_code for line in result.lines] == ["PA6BPA-WA"]


def test_bom_extras_join_the_order():
    from src.designer import bom
    from src.models import PanelDesign, QuoteLine

    result = bom.build([PanelDesign(**design())],
                       extras=[QuoteLine(description="Freight", quantity=1, rate=95),
                               QuoteLine(description="Ignored", quantity=0, rate=50)])
    assert [line.description for line in result.lines][-1] == "Freight"
    assert len(result.lines) == 3          # panel, engraving, freight


def test_quote_pdf_states_its_numbers(client):
    from src.pdfcompat import pymupdf

    response = client.post("/api/designer/quote", json={
        "designs": [design(quantity=2)], "job_name": "riverside",
        "client": "Acme Electrical", "reference": "Q-2481",
        "rates": {"PA6BPA-WA": 400.0, "ENGRAVING": 20.0}, "fmt": "pdf"})
    assert response.status_code == 200

    doc = pymupdf.open(stream=response.content)
    try:
        text = doc[0].get_text()
    finally:
        doc.close()
    for expected in ("QUOTATION", "PA6BPA-WA", "Acme Electrical", "Q-2481",
                     "840.00", "GST", "924.00"):
        assert expected in text, f"{expected!r} missing from the quote"


def test_quote_formats(client):
    for fmt, head in (("csv", b"\xef\xbb\xbfPart code,"), ("xlsx", b"PK")):
        response = client.post("/api/designer/quote", json={
            "designs": [design()], "job_name": "riverside", "fmt": fmt})
        assert response.status_code == 200
        assert response.content.startswith(head)


def test_bom_rejects_an_impossible_panel(client):
    response = client.post("/api/designer/bom",
                           json={"designs": [design(family="D", series="L")]})
    assert response.status_code == 400


# ------------------------------------------------------------- templates

def test_engraving_templates_round_trip(client):
    body = {"name": "Hotel suite", "slots": 4, "engraving": [
        {"index": 0, "lines": ["MASTER"], "icon": "bulb", "icon_side": "left"},
        {"index": 1, "lines": ["DO NOT", "DISTURB"], "icon": "dnd", "icon_side": "left"},
    ]}
    saved = client.post("/api/designer/templates", json=body).json()
    assert len(saved) == 1
    template_id = saved[0]["template_id"]
    assert template_id
    assert saved[0]["engraving"][1]["lines"] == ["DO NOT", "DISTURB"]

    assert client.get("/api/designer/templates").json()[0]["template_id"] == template_id

    # Saving with the same id replaces rather than duplicates.
    again = client.post("/api/designer/templates",
                        json={**body, "template_id": template_id, "name": "Suite v2"}).json()
    assert len(again) == 1
    assert again[0]["name"] == "Suite v2"

    assert client.delete(f"/api/designer/templates/{template_id}").json() == []
    assert client.delete(f"/api/designer/templates/{template_id}").status_code == 404


def test_template_needs_a_name(client):
    assert client.post("/api/designer/templates",
                       json={"name": "  ", "slots": 6}).status_code == 400
