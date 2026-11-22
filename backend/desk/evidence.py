import base64
import binascii
import json
import re
import subprocess
from tempfile import TemporaryFile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
from threading import BoundedSemaphore

from PIL import Image, UnidentifiedImageError

from .contracts import MAX_DOCUMENT_BYTES, MAX_DOCUMENT_MIB, Upload
from .db import Case, Document

FIELDS = {"PAN": ("holder_name", "pan"), "GST": ("legal_name", "gstin")}
LABELS = {"holder_name": "Holder name", "legal_name": "Legal name", "pan": "PAN", "gstin": "GSTIN"}
PATTERNS = {
    "pan": re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]"),
    "gstin": re.compile(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]"),
}
parser_slots = BoundedSemaphore(2)


class Problem(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def normalize(value: str):
    return " ".join(value.split()).upper()


def read_labels(pages: list[str], kind: str):
    result = {}

    def record(key, value, page, quote):
        previous = result.get(key)
        if previous and (not value or normalize(previous["value"]) == normalize(value)):
            return
        result[key] = {
            "value": value[:250],
            "source": f"Page {page} · {LABELS[key]}",
            "page": page,
            "quote": quote[:400],
            "unreadable": not value,
            "ambiguous": bool(previous and previous["value"]),
        }

    for page, text in enumerate(pages, 1):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            for key in FIELDS[kind]:
                match = re.match(r"^\s*" + LABELS[key] + r"\s*[:\-]\s*(.+?)\s*$", line, re.I)
                if match:
                    record(key, match[1], page, line)

        if kind == "GST" and re.search(r"\bGST\s+REG[\s-]*06\b", text, re.I):
            for line in lines:
                row = re.sub(r"^[\d.| )]+", "", line)
                name = re.match(r"Legal Name\b[\s:|–-]+(.+)", row, re.I)
                number = re.fullmatch(r"Registration Number\s*[:|-]?\s*([A-Z0-9]+)", row, re.I)
                if name:
                    record("legal_name", name[1].strip(" |"), page, line)
                if number:
                    record("gstin", number[1], page, line)

        if kind == "PAN" and re.search(r"\bIncome Tax Department\b", text, re.I):
            heading = next((line for line in lines if re.search(r"\bPermanent Account\b", line, re.I)), None)
            if heading:
                # A damaged number still belongs to this PAN card. Leave it unreadable.
                record("pan", "", page, heading)
                for index, line in enumerate(lines):
                    for number in re.finditer(r"\b" + PATTERNS["pan"].pattern + r"\b", line):
                        record("pan", number[0], page, line)
                    if re.search(r"(?:^|/)\s*Name\b", line, re.I) and index + 1 < len(lines):
                        printed = lines[index + 1]
                        name = re.split(r"\.{2,}|[|_\[\]©%]", printed, maxsplit=1)[0].strip(" .")
                        if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,139}", name):
                            record("holder_name", name, page, printed)
    return result


@dataclass
class Reading:
    kind: str
    fields: dict
    method: str
    status: str = "READY"
    note: str = ""


def document_tool(command, data, *, seconds, output_limit):
    # Callers supply the executable and flags. The document goes to stdin; prlimit execs the parser.
    limits = ["prlimit", "--as=536870912", f"--cpu={seconds}", f"--fsize={output_limit + 1}", "--"]
    if not parser_slots.acquire(timeout=1):
        raise OSError("Document readers are busy")
    try:
        with TemporaryFile() as output:
            subprocess.run(
                [*limits, *command],
                input=data,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=seconds,
                check=True,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "OMP_THREAD_LIMIT": "1"},
            )
            output.seek(0)
            result = output.read(output_limit + 1)
    finally:
        parser_slots.release()
    if len(result) > output_limit:
        raise ValueError("Document output exceeded its limit")
    return result


def pdf_text(data):
    result = document_tool(
        ["pdftotext", "-layout", "-enc", "UTF-8", "-f", "1", "-l", "8", "-", "-"],
        data,
        seconds=8,
        output_limit=3_200_000,
    )
    pages = result.decode("utf-8", errors="replace").split("\f")
    if pages[-1] == "":
        pages.pop()
    return pages


def preview_pdf(data, page):
    try:
        return document_tool(
            [
                "pdftoppm",
                "-f",
                str(page),
                "-l",
                str(page),
                "-singlefile",
                "-scale-to",
                "1000",
                "-png",
                "-",
            ],
            data,
            seconds=8,
            output_limit=4_194_304,
        )
    except OSError:
        raise Problem(503, "The preview service is unavailable. Try again.") from None
    except (subprocess.SubprocessError, ValueError):
        raise Problem(422, "Preview unavailable. Download the original document.") from None


def local_read(data, mime):
    if mime == "application/pdf":
        method = "PDF text"
        try:
            pages = pdf_text(data)
        except (OSError, subprocess.SubprocessError, ValueError):
            return Reading("OTHER", {}, method, "TECHNICAL", "Local PDF reader unavailable")
    else:
        method = "Local OCR"
        try:
            result = document_tool(
                ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "6"],
                data,
                seconds=12,
                output_limit=400_000,
            )
            pages = [result.decode("utf-8", errors="replace")]
        except (OSError, subprocess.SubprocessError, ValueError):
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
    if name in {"", ".", ".."} or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise Problem(422, "Supply a valid document filename.")
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
        if not 0 < len(data) <= MAX_DOCUMENT_BYTES:
            raise Problem(422, f"Upload a document between 1 byte and {MAX_DOCUMENT_MIB} MiB.")
        status, method = "PENDING", "Queued"
        if data.startswith(b"%PDF-"):
            mime = "application/pdf"
            try:
                info = document_tool(["pdfinfo", "-"], data, seconds=4, output_limit=65_536)
                pages = re.search(rb"^Pages:\s*(\d+)\s*$", info, re.M)
                if (
                    not pages
                    or not 1 <= int(pages[1]) <= 8
                    or re.search(rb"^Encrypted:\s+yes", info, re.M)
                ):
                    raise Problem(422, "Use an unlocked PDF with at most eight pages.")
            except OSError:
                raise Problem(503, "The PDF reader is unavailable.") from None
            except (subprocess.SubprocessError, ValueError):
                raise Problem(422, "Use a readable, unlocked PDF within the document limits.") from None
        else:
            try:
                with Image.open(BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as img:
                    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
                        img.format
                    )
                    if not mime:
                        raise Problem(422, "Supported image formats are PNG, JPEG and WebP.")
                    if img.width * img.height > 10_000_000:
                        raise Problem(422, "Use a scan under ten megapixels.")
                    if getattr(img, "is_animated", False):
                        raise Problem(422, "Upload a single-page still image.")
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
