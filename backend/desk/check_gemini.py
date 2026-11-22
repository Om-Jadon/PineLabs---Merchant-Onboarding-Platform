"""Run explicitly with python -m desk.check_gemini to check the four bundled specimen images."""

import os
import sys

from .evidence import normalize
from .gemini import MODEL, ReaderUnavailable, read_gemini
from .settings import FIXTURES, IMAGE_APPLICATION, LEGAL_NAME, NEW_GST, PAN


def main():
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        sys.exit("Set GEMINI_API_KEY in the worker environment to run the live check.")
    for filename, kind, expected_fields in [
        ("pan-stat.png", "PAN", {"holder_name": LEGAL_NAME, "pan": PAN}),
        ("gst-stationary.png", "GST", {"legal_name": LEGAL_NAME, "gstin": NEW_GST}),
        ("pan.png", "PAN", {"holder_name": "SAMPLE KUMAR", "pan": IMAGE_APPLICATION["pan"]}),
        ("gst-cert.png", "GST", {
            "legal_name": IMAGE_APPLICATION["legal_name"], "gstin": IMAGE_APPLICATION["gstin"],
        }),
    ]:
        try:
            reading = read_gemini((FIXTURES / filename).read_bytes(), "image/png", key)
        except ReaderUnavailable as exc:
            sys.exit(f"{filename} · {MODEL} · {exc}")
        for field, expected in expected_fields.items():
            evidence = reading.fields.get(field, {})
            if (
                reading.kind != kind
                or reading.status != "READY"
                or evidence.get("ambiguous")
                or normalize(evidence.get("value") or "") != normalize(expected)
                or evidence.get("page") != 1
                or not evidence.get("quote")
            ):
                sys.exit(f"{filename} · {reading.method} · {field} did not match the fixture and source")
        print(
            f"{filename} · {reading.method} · {kind} identified, both printed fields and sources matched",
            flush=True,
        )


if __name__ == "__main__":
    main()
