"""End-to-end API tests, plus the OCR registry and panel extraction."""

from __future__ import annotations

import pytest

from src.pdfcompat import pymupdf


def upload(client, data: bytes, name: str = "test.pdf") -> str:
    response = client.post(
        "/api/documents", files={"files": (name, data, "application/pdf")}
    )
    assert response.status_code == 200, response.text
    return response.json()[0]["doc_id"]


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["app"] == "PDF Workbench"


def test_upload_rejects_non_pdf(client):
    response = client.post(
        "/api/documents", files={"files": ("notes.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 400


def test_upload_rejects_corrupt_pdf(client):
    response = client.post(
        "/api/documents", files={"files": ("bad.pdf", b"%PDF-1.4 broken", "application/pdf")}
    )
    assert response.status_code == 400


def test_document_lifecycle(client, simple_pdf):
    doc_id = upload(client, simple_pdf)

    info = client.get(f"/api/documents/{doc_id}").json()
    assert info["total_pages"] == 4
    assert info["text_coverage"] == 1.0

    render = client.get(f"/api/documents/{doc_id}/pages/1/render?width=300")
    assert render.status_code == 200
    assert render.content[:8] == b"\x89PNG\r\n\x1a\n"

    layer = client.get(f"/api/documents/{doc_id}/pages/1/text").json()
    assert any("Hello" in w["text"] for w in layer["words"])

    assert client.delete(f"/api/documents/{doc_id}").status_code == 200
    assert client.get(f"/api/documents/{doc_id}").status_code == 404


def test_clear_all_closes_every_document_but_keeps_the_assets(client, simple_pdf,
                                                              logo_png):
    """Clearing the workspace is about documents. A stamp logo is not one.

    Losing the saved logos to a "close all" would be a nasty surprise, so this
    pins that only the PDFs go.
    """
    upload(client, simple_pdf, "one.pdf")
    upload(client, simple_pdf, "two.pdf")
    asset = client.post("/api/assets",
                        files={"file": ("logo.png", logo_png, "image/png")}).json()
    assert len(client.get("/api/documents").json()) == 2

    response = client.delete("/api/documents")
    assert response.status_code == 200
    assert "2 documents" in response.json()["message"]

    assert client.get("/api/documents").json() == []
    assert client.get(f"/api/assets/{asset['asset_id']}").status_code == 200

    # Clearing an empty workspace is a no-op, not an error.
    assert client.delete("/api/documents").status_code == 200


def test_missing_document_is_404(client):
    assert client.get("/api/documents/nope").status_code == 404
    assert client.post("/api/documents/nope/pages/rotate",
                       json={"pages": [1], "degrees": 90}).status_code == 404


def test_undo_redo_round_trip(client, simple_pdf):
    doc_id = upload(client, simple_pdf)
    client.post(f"/api/documents/{doc_id}/pages/delete", json={"pages": [1]})
    assert client.get(f"/api/documents/{doc_id}").json()["total_pages"] == 3

    client.post(f"/api/documents/{doc_id}/undo")
    assert client.get(f"/api/documents/{doc_id}").json()["total_pages"] == 4

    client.post(f"/api/documents/{doc_id}/redo")
    assert client.get(f"/api/documents/{doc_id}").json()["total_pages"] == 3


def test_failed_edit_leaves_the_document_untouched(client, simple_pdf):
    doc_id = upload(client, simple_pdf)
    response = client.post(f"/api/documents/{doc_id}/pages/reorder", json={"order": [1, 1, 2, 3]})
    assert response.status_code == 400
    # The document must survive a rejected operation intact.
    assert client.get(f"/api/documents/{doc_id}").json()["total_pages"] == 4
    render = client.get(f"/api/documents/{doc_id}/pages/1/render?width=200")
    assert render.status_code == 200


def test_split_returns_a_zip(client, simple_pdf):
    doc_id = upload(client, simple_pdf)
    response = client.post(f"/api/documents/{doc_id}/split",
                           json={"mode": "every_n", "every_n": 2})
    assert response.status_code == 200
    assert response.content[:2] == b"PK"


def test_download_returns_a_pdf(client, simple_pdf):
    doc_id = upload(client, simple_pdf)
    response = client.get(f"/api/documents/{doc_id}/download")
    assert response.status_code == 200
    assert response.content[:5] == b"%PDF-"


def test_stamp_pipeline(client, simple_pdf, logo_png):
    doc_id = upload(client, simple_pdf)
    asset = client.post("/api/assets", files={"file": ("logo.png", logo_png, "image/png")}).json()

    analysis = client.post("/api/stamp/analyze", json={
        "doc_ids": [doc_id], "logo_id": asset["asset_id"], "config": {},
    }).json()
    assert len(analysis[0]["page_placements"]) == 4

    result = client.post("/api/stamp/apply", json={
        "doc_ids": [doc_id], "logo_id": asset["asset_id"], "config": {},
        "apply_in_place": True,
    }).json()
    assert result["total_pages_stamped"] == 4

    zipped = client.post("/api/stamp/apply", json={
        "doc_ids": [doc_id], "logo_id": asset["asset_id"], "config": {},
        "apply_in_place": False,
    }).json()
    archive = client.get(zipped["download_url"])
    assert archive.status_code == 200
    assert archive.content[:2] == b"PK"


def test_purchase_order_endpoint(client, samples):
    data = (samples / "po_sample_a.pdf").read_bytes()
    doc_id = upload(client, data, "po.pdf")
    result = client.post(f"/api/documents/{doc_id}/purchase-order",
                         json={"pages": {"mode": "all"}}).json()
    assert result["header"]["po_number"] == "PO-10297"
    assert len(result["line_items"]) == 3
    assert result["source"] == "text"

    for fmt in ("csv", "tsv", "xlsx", "txt"):
        export = client.post(
            f"/api/documents/{doc_id}/purchase-order/export?fmt={fmt}", json=result
        )
        assert export.status_code == 200, fmt
        assert len(export.content) > 50


def test_settings_never_echo_the_api_key(client):
    client.post("/api/settings", json={"ocr_remote_api_key": "super-secret"})
    body = client.get("/api/settings").json()
    assert body["ocr_remote_api_key"] == "********"
    # Saving the masked value back must not overwrite the stored key.
    client.post("/api/settings", json={"ocr_remote_api_key": "********"})
    from src import config
    assert config.load_settings()["ocr_remote_api_key"] == "super-secret"
    client.post("/api/settings", json={"ocr_remote_api_key": ""})


# ------------------------------------------------------------------- OCR

def test_ocr_capabilities_lists_engines(client):
    caps = client.get("/api/ocr/capabilities").json()
    names = {e["name"] for e in caps["engines"]}
    assert {"native", "deepseek", "rapidocr", "tesseract", "remote"} <= names
    native = next(e for e in caps["engines"] if e["name"] == "native")
    assert native["available"] is True


def test_deepseek_reports_why_it_is_unavailable(client):
    caps = client.get("/api/ocr/capabilities").json()
    deepseek = next(e for e in caps["engines"] if e["name"] == "deepseek")
    if not deepseek["available"]:
        # The reason must be actionable, never a bare False.
        assert deepseek["detail"]
        assert deepseek["install_hint"]


def test_native_engine_reads_the_text_layer(client, simple_pdf):
    doc_id = upload(client, simple_pdf)
    results = client.post(f"/api/documents/{doc_id}/ocr", json={
        "pages": {"mode": "custom", "custom": "1"}, "mode": "plain",
    }).json()
    assert results[0]["engine"] == "native"
    assert "Hello page 1" in results[0]["text"]


def test_region_ocr_uses_the_text_layer_when_present(client, simple_pdf):
    doc_id = upload(client, simple_pdf)
    results = client.post(f"/api/documents/{doc_id}/ocr", json={
        "region": {"x0": 60, "y0": 100, "x1": 300, "y1": 130},
        "region_page": 1, "mode": "plain",
    }).json()
    assert "Hello page 1" in results[0]["text"]


def test_grounded_output_parser():
    """DeepSeek emits <|ref|>/<|det|> pairs with 0-999 normalised boxes."""
    from src.ocr.base import RenderedPage, parse_grounded_output

    page = RenderedPage(png=b"", width_px=1000, height_px=1000,
                        page_width=500.0, page_height=1000.0, page_number=1, dpi=200)
    raw = "<|ref|>Invoice total<|/ref|><|det|>[[100, 200, 500, 260]]<|/det|>\nplain tail"
    markdown, blocks = parse_grounded_output(raw, page)

    assert len(blocks) == 1
    assert blocks[0].text == "Invoice total"
    assert blocks[0].rect.x0 == pytest.approx(100 / 999 * 500, rel=1e-3)
    assert blocks[0].rect.y1 == pytest.approx(260 / 999 * 1000, rel=1e-3)
    assert "<|ref|>" not in markdown
    assert "Invoice total" in markdown
    assert "plain tail" in markdown


def test_markdown_to_plain_keeps_table_cells():
    from src.ocr.base import markdown_to_plain

    plain = markdown_to_plain("# Title\n\n| A | B |\n|---|---|\n| 1 | 2 |")
    assert "Title" in plain
    assert "1" in plain and "2" in plain
    assert "---" not in plain


# ---------------------------------------------------------------- panels

def _dynalite_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 60), "Panel Name: LEVEL 2 LOUNGE", fontsize=11)
    page.insert_text((40, 90), "Philips Dynalite", fontsize=9)
    for index, label in enumerate(["Downlights", "Feature", "Pendants", "All Off"]):
        page.insert_text((40, 130 + index * 30), label, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def test_panel_extraction_dynalite(client):
    doc_id = upload(client, _dynalite_pdf(), "panel.pdf")
    result = client.post(f"/api/documents/{doc_id}/panels", json={"style": "auto"}).json()
    assert result["style"] == "dynalite"
    entry = result["entries"][0]
    assert entry["name"] == "LEVEL 2 LOUNGE"

    labels = [l["text"] for row in entry["rows"] for l in row if l["include"]]
    assert "Downlights" in labels
    assert "All Off" in labels
    # Boilerplate must arrive pre-unticked.
    excluded = [l["text"] for row in entry["rows"] for l in row if not l["include"]]
    assert any("Dynalite" in text for text in excluded)


def test_panel_export_csv_shape(client):
    from src.exporters import panels_csv
    from src.models import PanelEntry, PanelLabel

    entries = [
        PanelEntry(panel_id="a", name="P1", rows=[[PanelLabel(text="One"), PanelLabel(text="Two")]]),
        PanelEntry(panel_id="b", name="P2", rows=[[PanelLabel(text="Three")]]),
    ]
    text = panels_csv(entries).decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0] == "Panel,Label1,Label2"
    assert lines[1] == "P1,One,Two"
    assert lines[2] == "P2,Three"


def test_config_grouping_finds_identical_panels():
    from src.extract.panels import config_key, group_by_config
    from src.models import PanelEntry, PanelLabel

    def make(panel_id, name, labels):
        entry = PanelEntry(panel_id=panel_id, name=name,
                           rows=[[PanelLabel(text=t) for t in labels]])
        entry.config_key = config_key(entry)
        return entry

    entries = [
        make("a", "P1", ["Lights", "Off"]),
        make("b", "P2", ["Lights", "Off"]),
        make("c", "P3", ["Different"]),
    ]
    groups = group_by_config(entries)
    assert len(groups) == 1
    assert groups[0]["count"] == 2
    assert set(groups[0]["panel_ids"]) == {"a", "b"}
