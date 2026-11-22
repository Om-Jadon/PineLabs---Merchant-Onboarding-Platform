import base64
import json
import shutil
from io import BytesIO

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from desk import gemini
from desk.contracts import Upload
from desk.db import Case
from desk.evidence import Problem, compare, local_read, make_document
from desk.settings import FIXTURES, IMAGE_APPLICATION, LEGAL_NAME, NEW_GST, PAN


def fields(name=LEGAL_NAME, gstin=NEW_GST):
    return [
        {"name": "legal_name", "value": name, "page": 1, "quote": name, "uncertain": False},
        {"name": "gstin", "value": gstin, "page": 1, "quote": gstin, "uncertain": False},
    ]


def response(kind="GST", readings=None, finish="STOP"):
    answer = {"document_type": kind, "fields": fields() if readings is None else readings}
    return httpx.Response(
        200,
        json={
            "modelVersion": "test-version",
            "candidates": [
                {
                    "finishReason": finish,
                    "content": {
                        "parts": [{"thought": True, "text": "ignored"}, {"text": json.dumps(answer)}]
                    },
                }
            ],
        },
    )


def upload(filename, kind="AUTO"):
    return Upload(
        kind=kind,
        filename=filename,
        content_base64=base64.b64encode((FIXTURES / filename).read_bytes()).decode(),
    )


def compare_reading(reading):
    doc = make_document(Upload(kind="GST", filename="gst.json", fields={}), "test", 1)
    doc.status, doc.fields = reading.status, reading.fields
    case = Case(legal_name=LEGAL_NAME, pan=PAN, gstin=NEW_GST, policy=["GST"])
    return {f["key"]: f["status"] for f in compare(case, {"GST": doc})}


@pytest.mark.parametrize("mime", ["image/png", "application/pdf"])
def test_schema_request_and_grounded_reading(mime, text_pdf):
    if mime == "image/png":
        data = (FIXTURES / "gst-cert.png").read_bytes()
        name, gstin = IMAGE_APPLICATION["legal_name"], IMAGE_APPLICATION["gstin"]
    else:
        data, name, gstin = text_pdf, LEGAL_NAME, NEW_GST

    def post(url, *, headers, json, timeout):
        assert (
            url
            == "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent"
        )
        assert headers == {"x-goog-api-key": "unit-test-credential"}
        parts = json["contents"][0]["parts"]
        assert name not in parts[0]["text"] and gstin not in parts[0]["text"]
        assert parts[1]["inlineData"]["mimeType"] == mime
        assert base64.b64decode(parts[1]["inlineData"]["data"]) == data
        assert json["generationConfig"]["responseJsonSchema"]["properties"]["document_type"]
        return response(readings=fields(name, gstin))

    reading = gemini.read_gemini(data, mime, "unit-test-credential", post)
    assert reading.method == "Gemini · test-version"
    assert reading.fields["gstin"]["page"] == 1 and reading.fields["gstin"]["quote"] == gstin
    assert reading.fields["gstin"]["value"] == gstin and reading.fields["legal_name"]["value"] == name


@pytest.mark.parametrize("kind", ["OTHER", "MIXED"])
def test_unknown_or_mixed_has_no_applicable_fields(kind):
    result = gemini.read_gemini(b"synthetic", "image/png", "test", lambda *a, **k: response(kind))
    assert (result.kind, result.status, result.fields) == (kind, "CONFLICT", {})


def test_pan_classification_uses_pan_specific_fields():
    evidence = [
        {"name": "holder_name", "value": LEGAL_NAME, "page": 1, "quote": LEGAL_NAME, "uncertain": False},
        {"name": "pan", "value": PAN, "page": 1, "quote": PAN, "uncertain": False},
    ]
    reading = gemini.read_gemini(
        b"synthetic", "image/png", "test", lambda *a, **k: response("PAN", evidence)
    )
    assert reading.kind == "PAN" and reading.fields["pan"]["value"] == PAN
    wrong = gemini.read_gemini(
        b"synthetic", "image/png", "test", lambda *a, **k: response("GST", evidence)
    )
    assert wrong.status == "CONFLICT" and not wrong.fields


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"uncertain": True, "value": None}, "unreadable"),
        ({"quote": "some other identifier"}, "conflicting"),
        ({"quote": "invented prefix " + NEW_GST}, "conflicting"),
        ({"page": 2}, "conflicting"),
    ],
)
def test_unsupported_field_cannot_satisfy_validation(change, expected, text_pdf):
    candidates = fields()
    candidates[1].update(change)
    reading = gemini.read_gemini(
        text_pdf,
        "application/pdf",
        "test",
        lambda *a, **k: response(readings=candidates),
    )
    assert compare_reading(reading)["gstin"] == expected


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(401),
        httpx.Response(429),
        httpx.Response(503),
        httpx.Response(200, json={"candidates": []}),
        response(finish="MAX_TOKENS"),
        response(readings=[{"field": "invalid"}]),
        httpx.Response(200, text="invalid"),
    ],
)
def test_provider_errors_use_sanitized_failure(reply):
    with pytest.raises(gemini.ReaderUnavailable) as err:
        gemini.read_gemini(b"x", "image/png", "unit-test-credential", lambda *a, **k: reply)
    assert "unit-test-credential" not in str(err.value)


def test_fallback_when_quota_exhausted(monkeypatch, text_pdf):
    monkeypatch.setenv("GEMINI_API_KEY", "test")

    def unavailable(*args):
        raise gemini.ReaderUnavailable("Gemini returned HTTP 429")

    monkeypatch.setattr(gemini, "read_gemini", unavailable)
    reading = gemini.read_document(text_pdf, "application/pdf")
    assert reading.method == "PDF text" and "429" in reading.note
    assert reading.fields["gstin"]["value"] == NEW_GST
    blank = BytesIO()
    Image.new("RGB", (120, 80), "white").save(blank, format="PNG")
    unclear = gemini.read_document(blank.getvalue(), "image/png")
    assert unclear.status == "TECHNICAL" and "429" in unclear.note


def test_local_digital_pdf_without_key(monkeypatch, text_pdf):
    monkeypatch.setattr(gemini, "read_gemini", lambda *a: pytest.fail("Unexpected hosted call"))
    reading = gemini.read_document(text_pdf, "application/pdf")
    assert reading.kind == "GST" and reading.fields["gstin"]["value"] == NEW_GST


@pytest.mark.skipif(
    not shutil.which("tesseract"), reason="OCR executable is included in the Compose image"
)
def test_specimen_images_are_classified_and_cannot_approve_together():
    docs = {}
    for filename, kind in [("pan.png", "PAN"), ("gst-cert.png", "GST")]:
        doc = make_document(upload(filename), "test", 1)
        reading = local_read(doc.data, doc.mime)
        assert reading.kind == kind and reading.status == "READY"
        doc.status, doc.fields = reading.status, reading.fields
        docs[kind] = doc
    assert docs["PAN"].fields["holder_name"]["value"] == "SAMPLE KUMAR"
    assert docs["GST"].fields["gstin"]["value"] == IMAGE_APPLICATION["gstin"]
    case = Case(**IMAGE_APPLICATION, policy=["PAN", "GST"])
    rows = {row["key"]: row for row in compare(case, docs)}
    assert rows["holder_name"]["status"] == "conflicting"
    assert rows["legal_name"]["status"] == "matched"
    assert rows["gstin"]["status"] == "invalid"
    assert rows["pan"]["status"] in {"matched", "unreadable"}


def test_local_pan_reader_leaves_damaged_number_unreadable(monkeypatch):
    from desk import evidence

    damaged = b"INCOME TAX DEPARTMENT\nPermanent Account Number\nABCDE12?4F\nName\nOTHER HOLDER"
    monkeypatch.setattr(evidence, "document_tool", lambda *a, **k: damaged)
    reading = local_read(b"scan", "image/png")
    assert reading.kind == "PAN" and reading.fields["holder_name"]["value"] == "OTHER HOLDER"
    assert reading.fields["pan"]["unreadable"] and not reading.fields["pan"]["value"]


def test_file_signature_and_structured_source():
    item = upload("pan.png")
    item.filename = "document.jpg"
    assert make_document(item, "test", 1).mime == "image/png"
    structured = make_document(
        Upload(kind="PAN", filename="document.json", fields={"pan": PAN}), "test", 1
    )
    assert structured.fields["pan"]["source"] == "$.pan"
    with pytest.raises(Problem):
        make_document(Upload(filename="scan.png", content_base64="notbase64"), "test", 1)


def test_identifier_is_a_complete_token_in_source(monkeypatch):
    candidates = fields()
    candidates[1]["quote"] = NEW_GST
    monkeypatch.setattr(gemini, "pdf_text", lambda data: [LEGAL_NAME + " " + NEW_GST + "0"])
    reading = gemini.read_gemini(
        b"fixture", "application/pdf", "test", lambda *a, **k: response(readings=candidates)
    )
    assert compare_reading(reading)["gstin"] == "conflicting"


def test_unsupported_document_pages_and_multiframe_image_are_refused(monkeypatch, text_pdf):
    from desk import evidence

    pdf = Upload(filename="document.pdf", content_base64=base64.b64encode(text_pdf).decode())
    for summary in (b"Pages: 9\nEncrypted: no\n", b"Pages: 1\nEncrypted: yes\n"):
        monkeypatch.setattr(evidence, "document_tool", lambda *a, **k: summary)
        with pytest.raises(Problem, match="unlocked PDF"):
            make_document(pdf, "test", 1)
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        buffer, format="PNG", save_all=True, append_images=[Image.new("RGB", (2, 2), "blue")]
    )
    frames = Upload(
        filename="two-frames.png", content_base64=base64.b64encode(buffer.getvalue()).decode()
    )
    with pytest.raises(Problem, match="still image"):
        make_document(frames, "test", 1)


def test_invalid_database_text_fails_input_validation():
    with pytest.raises(ValidationError):
        Upload(kind="PAN", filename="pan.json", fields={"pan": PAN + chr(0)})


def test_parser_does_not_inherit_service_credentials(monkeypatch):
    from desk import evidence

    def run(command, **kwargs):
        assert command[0] == "prlimit" and not kwargs["env"].get("GEMINI_API_KEY")
        assert set(kwargs["env"]) == {"PATH", "LC_ALL", "OMP_THREAD_LIMIT"}
        kwargs["stdout"].write(b"fixture")

    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-only")
    monkeypatch.setattr(evidence.subprocess, "run", run)
    assert evidence.document_tool(["pdfinfo", "-"], b"", seconds=1, output_limit=8) == b"fixture"
