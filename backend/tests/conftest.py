import os
from io import BytesIO

import pytest
from PIL.PdfParser import PdfName, PdfParser

if "TEST_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]


@pytest.fixture
def text_pdf():
    """Two lines of selectable text for PDF parsing and source-grounding tests."""
    from desk.settings import LEGAL_NAME, NEW_GST

    buffer = BytesIO()
    with PdfParser(f=buffer) as pdf:
        pdf.start_writing()
        pdf.write_header()
        pdf.pages.append(pdf.next_object_id(0))
        pdf.write_catalog()
        font = pdf.write_obj(None, Type=PdfName("Font"), Subtype=PdfName("Type1"), BaseFont=PdfName("Helvetica"))
        text = f"BT /F1 12 Tf 24 100 Td (Legal name: {LEGAL_NAME}) Tj 0 -20 Td (GSTIN: {NEW_GST}) Tj ET"
        content = pdf.write_obj(None, stream=text.encode("ascii"))
        pdf.write_page(0, Resources={"Font": {"F1": font}}, MediaBox=[0, 0, 360, 144], Contents=content)
        pdf.write_xref_and_trailer()
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    from desk.settings import secret

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for key in ("SESSION_KEY_DIRECTORY", "SIMULATOR_KEY_DIRECTORY", "KEYS_READ_ONLY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KEY_DIRECTORY", str(tmp_path / "keys"))
    secret.cache_clear()
    yield
    secret.cache_clear()
