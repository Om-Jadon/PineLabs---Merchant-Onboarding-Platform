import base64
import binascii
import json
import re
import subprocess
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .contracts import Upload
from .db import Case, Document

FIELDS = {"PAN": ("holder_name", "pan"), "GST": ("legal_name", "gstin")}
LABELS = {"holder_name": "Holder name", "legal_name": "Legal name", "pan": "PAN", "gstin": "GSTIN"}
PATTERNS = {
    "pan": re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]"),
    "gstin": re.compile(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]"),
}


class Problem(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def normalize(value: str):
    return " ".join(value.split()).upper()


def read_labels(pages: list[str], kind: str):
    result = {}
    for page, text in enumerate(pages, 1):
        for line in text.splitlines():
            for key in FIELDS[kind]:
                match = re.match(r"^\s*" + LABELS[key] + r"\s*[:\-]\s*(.+?)\s*$", line, re.I)
                if match:
                    candidate = {
                        "value": match[1][:250],
                        "source": f"Page {page} · {LABELS[key]}",
                        "page": page,
                    }
                    candidate["quote"] = line.strip()[:400]
                    if key in result and result[key]["value"] != candidate["value"]:
                        candidate["ambiguous"] = True
                    if key not in result or candidate.get("ambiguous"):
                        result[key] = candidate
    return result


@dataclass
class Reading:
    kind: str
    fields: dict
    method: str
    status: str = "READY"
    note: str = ""


def pdf_text(data):
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted or len(reader.pages) > 8:
        raise Problem(422, "Use an unlocked PDF with at most eight pages.")
    return [(page.extract_text() or "")[:100_000] for page in reader.pages]


def local_read(data, mime):
    if mime == "application/pdf":
        method = "PDF text"
        try:
            pages = pdf_text(data)
        except (PyPdfError, ValueError, KeyError, TypeError, IndexError):
            pages = []
    else:
        method = "Local OCR"
        try:
            with Image.open(BytesIO(data)) as img:
                stream = BytesIO()
                img.convert("RGB").save(stream, "PNG")
            run = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "6"],
                input=stream.getvalue(),
                capture_output=True,
                timeout=12,
                check=True,
            )
            pages = [run.stdout.decode("utf-8", errors="replace")]
        except (OSError, subprocess.SubprocessError):
            return Reading("OTHER", {}, method, "TECHNICAL", "Local image reader unavailable")
    found = {kind: read_labels(pages, kind) for kind in FIELDS}
    likely = [kind for kind, values in found.items() if ("gstin" if kind == "GST" else "pan") in values]
    if len(likely) == 1:
        kind = likely[0]
        return Reading(kind, found[kind], method)
    status = "CONFLICT" if any(page.strip() for page in pages) else "UNREADABLE"
    return Reading(
        "MIXED" if len(likely) > 1 else "OTHER", {}, method, status, "Document type needs review"
    )


def make_document(upload: Upload, case_id: str, revision: int):
    name = PurePath(upload.filename.replace("\\", "/")).name
    fields = {}
    status = "READY"
    if upload.fields is not None:
        data = json.dumps(upload.fields, ensure_ascii=False, indent=2).encode()
        fields = {
            key: {"value": upload.fields[key], "source": "$." + key}
            for key in FIELDS[upload.kind]
            if key in upload.fields
        }
        mime, method = "application/json", "Structured JSON"
    else:
        try:
            data = base64.b64decode(upload.content_base64, validate=True)
        except (ValueError, binascii.Error):
            raise Problem(422, "The document could not be decoded.")
        if not 0 < len(data) <= 3 * 1024 * 1024:
            raise Problem(422, "Upload a document between 1 byte and 3 MiB.")
        status, method = "PENDING", "Queued"
        if data.startswith(b"%PDF-"):
            mime = "application/pdf"
            try:
                reader = PdfReader(BytesIO(data))
                if reader.is_encrypted or len(reader.pages) > 8:
                    raise Problem(422, "Use an unlocked PDF with at most eight pages.")
            except (PyPdfError, ValueError, KeyError, TypeError, IndexError):
                status = "UNREADABLE"
        else:
            try:
                with Image.open(BytesIO(data)) as img:
                    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
                        img.format
                    )
                    if not mime:
                        raise Problem(422, "Supported image formats are PNG, JPEG and WebP.")
                    if img.width * img.height > 10_000_000:
                        raise Problem(422, "Use a scan under ten megapixels.")
                    img.verify()
            except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
                raise Problem(422, "Use a readable PDF, PNG, JPEG, WebP or structured JSON document.")
    return Document(
        case_id=case_id,
        kind=upload.kind,
        revision=revision,
        filename=name,
        data=data,
        mime=mime,
        method=method,
        status=status,
        fields=fields,
        detected_kind=upload.kind if upload.fields is not None else None,
    )


def compare(case: Case, documents: dict[str, Document]):
    rows = []
    for kind in case.policy:
        doc = documents.get(kind)
        for key in FIELDS[kind]:
            declared = getattr(case, "legal_name" if key == "holder_name" else key)
            field = doc.fields.get(key, {}) if doc else {}
            value = field.get("value", "")
            status = "matched"
            if doc and doc.status == "CONFLICT":
                status = "conflicting"
            elif doc and doc.status in {"PENDING", "TECHNICAL", "UNREADABLE"}:
                status = doc.status.lower()
            elif field.get("unreadable"):
                status = "unreadable"
            elif field.get("ambiguous"):
                status = "conflicting"
            elif not declared or not value:
                status = "missing"
            elif key in PATTERNS and not all(
                PATTERNS[key].fullmatch(normalize(v)) for v in (value, declared)
            ):
                status = "invalid"
            elif normalize(value) != normalize(declared):
                status = "conflicting"
            elif key == "gstin" and normalize(value)[2:12] != normalize(case.pan):
                status = "conflicting"
            rows.append(
                {
                    "kind": kind,
                    "key": key,
                    "label": LABELS[key],
                    "declared": declared,
                    "extracted": value,
                    "status": status,
                    "source": field.get("source"),
                    "page": field.get("page"),
                    "quote": field.get("quote"),
                }
            )
    return rows
