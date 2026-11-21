"""Run explicitly with python -m desk.check_gemini. Sends two bundled synthetic documents."""

import os
import sys

from .evidence import normalize
from .gemini import MODEL, ReaderUnavailable, read_gemini
from .settings import FIXTURES, LEGAL_NAME, NEW_GST


def main():
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        sys.exit("Set GEMINI_API_KEY in the worker environment to run the live check.")
    for filename, mime in [
        ("registry-letter.jpg", "image/jpeg"),
        ("gst-current.pdf", "application/pdf"),
    ]:
        try:
            reading = read_gemini((FIXTURES / filename).read_bytes(), mime, key)
        except ReaderUnavailable as exc:
            sys.exit(f"{filename} · {MODEL} · {exc}")
        for field, expected in [("legal_name", LEGAL_NAME), ("gstin", NEW_GST)]:
            evidence = reading.fields.get(field, {})
            if (
                reading.kind != "GST"
                or reading.status != "READY"
                or evidence.get("ambiguous")
                or normalize(evidence.get("value") or "") != normalize(expected)
                or evidence.get("page") != 1
                or not evidence.get("quote")
            ):
                sys.exit(f"{filename} · {reading.method} · {field} did not match the fixture and source")
        print(
            f"{filename} · {reading.method} · GST identified, both fields and source references matched",
            flush=True,
        )


if __name__ == "__main__":
    main()
