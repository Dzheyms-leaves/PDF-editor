import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLES = ROOT / "tests" / "sample_data"


@pytest.fixture(scope="session")
def samples() -> Path:
    return SAMPLES


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from src.server import app
    from src.session import STORE

    with TestClient(app) as test_client:
        yield test_client
    STORE.reset()


@pytest.fixture()
def simple_pdf() -> bytes:
    """A four-page PDF with predictable text on each page."""
    from src.pdfcompat import pymupdf

    doc = pymupdf.open()
    for index in range(4):
        page = doc.new_page()
        page.insert_text((72, 120), f"Hello page {index + 1}", fontsize=18)
        page.insert_text((72, 160), f"SECRET-{index:03d}", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture()
def logo_png() -> bytes:
    import io

    from PIL import Image

    image = Image.new("RGBA", (300, 100), (193, 137, 63, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
