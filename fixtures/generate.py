"""Rebuild the synthetic evidence with ReportLab and Poppler's pdftoppm."""

from pathlib import Path
import json
import subprocess
import tempfile

import reportlab
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A5
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas


DESTINATION = Path(__file__).resolve().parent
MERCHANT = "Riverbend Stationery"
PAN = "DEMFR1234K"
OLD_GSTIN = "29DEMFR1234K1ZL"
NEW_GSTIN = "29DEMFR1234K2ZK"
WIDTH, HEIGHT = A5
MARGIN = 34
INK = "#20342D"
MUTED = "#4F655A"
GREEN = "#245B44"
RULE = "#C2D1C8"


def certificate(path, gstin, *, alternate=False, obscure=False):
    document = Canvas(str(path), pagesize=A5, invariant=1, pageCompression=1)
    document.setAuthor("Merchant onboarding demo")
    document.setTitle("Synthetic GST registration certificate")

    def line(text, top, size, font="Evidence Sans", color=INK):
        document.setFont(font, size)
        document.setFillColor(HexColor(color))
        document.drawString(MARGIN, HEIGHT - top, text)

    def divider(top):
        document.setStrokeColor(HexColor(RULE))
        document.setLineWidth(0.65)
        document.line(MARGIN, HEIGHT - top, WIDTH - MARGIN, HEIGHT - top)

    is_replacement = gstin == NEW_GSTIN
    reference = "RB-2026-002" if is_replacement else "RB-2026-001"
    copy_date = "19 November 2026" if is_replacement else "14 August 2026"

    line("MERCHANT RECORDS", 42, 10, "Helvetica-Bold", GREEN)
    document.setFillColor(HexColor("#E4EDE5"))
    document.roundRect(WIDTH - MARGIN - 96, HEIGHT - 48, 96, 18, 4, fill=1, stroke=0)
    document.setFillColor(HexColor(GREEN))
    document.setFont("Helvetica-Bold", 8)
    document.drawCentredString(WIDTH - MARGIN - 48, HEIGHT - 42, "SYNTHETIC SAMPLE")
    divider(64)
    line("GST registration certificate", 119, 29, "Times-Roman")
    line("Business document supplied for onboarding", 145, 11, color=MUTED)

    field_top = 282 if alternate else 232
    if alternate:
        line("Copy reference  " + reference, 195, 11, color=MUTED)
        line("Date on file  " + copy_date, 216, 11, color=MUTED)
    line("Merchant particulars", field_top - 24, 13, "Helvetica-Bold", GREEN)
    divider(field_top - 11)
    name_top, id_top = (field_top + 82, field_top + 38) if alternate else (field_top + 38, field_top + 82)
    line("Legal name: " + MERCHANT, name_top, 14)
    line("GSTIN: " + gstin, id_top, 14)

    if obscure:
        digit = 9
        prefix_width = pdfmetrics.stringWidth("GSTIN: " + gstin[:digit], "Evidence Sans", 14)
        digit_width = pdfmetrics.stringWidth(gstin[digit], "Evidence Sans", 14)
        document.setFillColor(HexColor("#7E817F"))
        document.rect(MARGIN + prefix_width - 0.3, HEIGHT - id_top - 2,
                      digit_width + 0.6, 14, stroke=0, fill=1)

    divider(field_top + 106)
    if not alternate:
        line("Copy reference  " + reference, 390, 11, color=MUTED)
        line("Date on file  " + copy_date, 411, 11, color=MUTED)

    divider(HEIGHT - 64)
    line("Fictional merchant data for the onboarding demonstration.", HEIGHT - 42, 8.5, color=MUTED)
    document.drawRightString(WIDTH - MARGIN, 42, "1 / 1")
    document.showPage()
    document.save()


def registry_letter(path):
    document = Canvas(str(path), pagesize=A5, invariant=1, pageCompression=1)
    document.setTitle("Synthetic merchant correspondence")
    document.setAuthor(MERCHANT)
    document.setFillColor(HexColor("#253D55"))
    document.setFont("Times-Bold", 24)
    document.drawString(MARGIN, HEIGHT - 61, MERCHANT)
    document.setFont("Helvetica", 10)
    document.drawString(MARGIN, HEIGHT - 80, "Merchant administration")
    document.setStrokeColor(HexColor("#738A9D"))
    document.line(MARGIN, HEIGHT - 96, WIDTH - MARGIN, HEIGHT - 96)
    document.setFont("Evidence Sans", 10)
    document.drawRightString(WIDTH - MARGIN, HEIGHT - 128, "19 November 2026")

    document.setFillColor(HexColor("#202B35"))
    document.setFont("Helvetica-Bold", 16)
    document.drawString(MARGIN, HEIGHT - 187, "Updated GST registration details")
    body = document.beginText(MARGIN, HEIGHT - 231)
    body.setFont("Evidence Sans", 11.3)
    body.setLeading(19)
    for line in [
        "Dear onboarding team,",
        "",
        "We are sending the updated business details you requested.",
        f"Our registered entity is {MERCHANT}.",
        f"Our tax registration reference is {NEW_GSTIN}.",
        "",
        "Please use this reference when you review our application.",
        "",
        "Kind regards,",
        "Merchant administration",
    ]:
        body.textLine(line)
    document.drawText(body)
    document.setFont("Helvetica", 9)
    document.setFillColor(HexColor("#495E70"))
    document.drawString(MARGIN, 42, "Synthetic correspondence for the merchant onboarding demonstration.")
    document.showPage()
    document.save()


def rasterize(source, stem, *, image_format="png"):
    subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-singlefile", "-r", "200",
                    f"-{image_format}", str(source), str(DESTINATION / stem)], check=True)


def main():
    vera = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    pdfmetrics.registerFont(TTFont("Evidence Sans", str(vera)))
    pan = {"holder_name": MERCHANT, "pan": PAN}
    (DESTINATION / "pan.json").write_text(json.dumps(pan, indent=2) + "\n", encoding="utf-8")
    old = DESTINATION / "gst-inactive.pdf"
    certificate(old, OLD_GSTIN)
    certificate(DESTINATION / "gst-current.pdf", NEW_GSTIN)
    rasterize(old, "gst-scan")
    with tempfile.TemporaryDirectory() as directory:
        sheet = Path(directory) / "scan-source.pdf"
        certificate(sheet, NEW_GSTIN, alternate=True)
        rasterize(sheet, "gst-scan-alternate")
        certificate(sheet, NEW_GSTIN, obscure=True)
        rasterize(sheet, "gst-scan-obscured")
        registry_letter(sheet)
        rasterize(sheet, "registry-letter", image_format="jpeg")


if __name__ == "__main__":
    main()
