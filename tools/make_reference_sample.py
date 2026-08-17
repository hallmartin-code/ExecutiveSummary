"""Generate a sample reference executive summary.

No reference document was supplied with this project, so this script produces a
realistic one for the pipeline to infer its design from. The company and every
figure in it are FICTIONAL and exist purely to establish visual design,
information hierarchy, and density.

Nothing in this file is ever used as a source of facts: `reference_analyzer`
measures geometry, typography, colour, and section ordering only.

    python tools/make_reference_sample.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "input" / "reference_summary.pdf"

NAVY = HexColor("#12395C")
INK = HexColor("#1A1A1A")
MUTED = HexColor("#5A6672")
RULE = HexColor("#C3CCD5")
BAND = HexColor("#F1F4F7")

MARGIN_L = MARGIN_R = 52.0
MARGIN_T = 46.0
MARGIN_B = 44.0

BODY_SIZE = 9.2
HEADING_SIZE = 8.0
LINE_HEIGHT = 1.28


def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if pdfmetrics.stringWidth(candidate, font, size) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def tracked(c: canvas.Canvas, text: str, x: float, y: float, font: str,
            size: float, color, spacing: float) -> None:
    """Draw letter-spaced text, then reset the spacing.

    Character spacing (Tc) persists in the PDF text state, so without the reset
    every subsequent run would inherit this heading's tracking.
    """
    t = c.beginText(x, y)
    t.setFont(font, size)
    t.setFillColor(color)
    t.setCharSpace(spacing)
    t.textOut(text)
    c.drawText(t)

    reset = c.beginText(0, 0)
    reset.setCharSpace(0)
    c.drawText(reset)


def heading(c: canvas.Canvas, text: str, x: float, y: float) -> float:
    tracked(c, text.upper(), x, y - HEADING_SIZE, "Helvetica-Bold", HEADING_SIZE, NAVY, 0.9)
    return y - HEADING_SIZE - 3.0


def paragraph(c: canvas.Canvas, text: str, x: float, y: float, width: float) -> float:
    c.setFillColor(INK)
    c.setFont("Helvetica", BODY_SIZE)
    for line in wrap(text, "Helvetica", BODY_SIZE, width):
        y -= BODY_SIZE * LINE_HEIGHT
        c.drawString(x, y, line)
    return y - 7.5


def build() -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    width, height = letter
    c = canvas.Canvas(str(OUT), pagesize=letter)
    c.setTitle("Reference Executive Summary (sample design template)")

    content_w = width - MARGIN_L - MARGIN_R
    gap = 18.0
    col_w = (content_w - gap) / 2
    col_x = [MARGIN_L, MARGIN_L + col_w + gap]

    # -- Header -----------------------------------------------------------
    y = height - MARGIN_T
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 22)
    y -= 22
    c.drawString(MARGIN_L, y, "Meridian Materials")

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 10.5)
    y -= 14
    c.drawString(MARGIN_L, y, "Solid-state electrolyte manufacturing for grid-scale storage")

    c.setFont("Helvetica", 7.2)
    y -= 12
    c.drawString(
        MARGIN_L, y,
        "SECTOR Advanced materials   |   STAGE Series A   |   RAISE $12M   |   HQ Columbus, OH",
    )

    y -= 10
    c.setStrokeColor(RULE)
    c.setLineWidth(0.9)
    c.line(MARGIN_L, y, width - MARGIN_R, y)
    y -= 12

    # -- Metric band ------------------------------------------------------
    band_h = 30.0
    y -= band_h
    c.setFillColor(BAND)
    c.roundRect(MARGIN_L, y, content_w, band_h, 2, stroke=0, fill=1)

    metrics = [("$4.1M", "ARR"), ("164%", "REVENUE GROWTH"), ("11", "CUSTOMERS"), ("$2.8B", "TAM")]
    cell = content_w / len(metrics)
    for i, (value, label) in enumerate(metrics):
        cx = MARGIN_L + cell * i + cell / 2
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(cx - pdfmetrics.stringWidth(value, "Helvetica-Bold", 14) / 2,
                     y + band_h - 6 - 12, value)
        label_w = pdfmetrics.stringWidth(label, "Helvetica", 6.6) + 0.5 * (len(label) - 1)
        tracked(c, label, cx - label_w / 2, y + 5, "Helvetica", 6.6, MUTED, 0.5)
        if i < len(metrics) - 1:
            c.setStrokeColor(RULE)
            c.setLineWidth(0.5)
            c.line(MARGIN_L + cell * (i + 1), y + 5, MARGIN_L + cell * (i + 1), y + band_h - 5)

    y -= 12
    body_top = y

    # -- Left column ------------------------------------------------------
    ly = body_top
    ly = heading(c, "Investment Opportunity", col_x[0], ly)
    ly = paragraph(c, (
        "Meridian Materials produces sulfide-based solid electrolyte powder for "
        "grid-scale battery manufacturers. Lithium-ion systems degrade 20% within four "
        "years in daily-cycling storage duty, and utilities absorb the replacement cost. "
        "Meridian supplies the electrolyte layer rather than the cell, selling into "
        "existing cell lines under multi-year supply agreements. The company reports "
        "$4.1M ARR across 11 customers and 164% year-over-year revenue growth."
    ), col_x[0], ly, col_w)

    ly = heading(c, "Market & Business Model", col_x[0], ly)
    ly = paragraph(c, (
        "TAM of $2.8B by 2030, built bottom-up from 40 GWh of announced North American "
        "grid storage capacity at $70/kWh of electrolyte content. Revenue is a per-kilogram "
        "supply contract averaging $370K annually, with three-year take-or-pay terms and "
        "58% gross margin at current volumes."
    ), col_x[0], ly, col_w)

    ly = heading(c, "Competitive Position", col_x[0], ly)
    ly = paragraph(c, (
        "Two granted composition-of-matter patents cover the argyrodite doping process, "
        "expiring 2041. Qualification into a cell line takes 14 months, creating switching "
        "costs once a customer is in production. Competing suppliers operate at gram scale; "
        "Meridian ships at 40 kg per week."
    ), col_x[0], ly, col_w)

    # -- Right column -----------------------------------------------------
    ry = body_top
    ry = heading(c, "Traction & Validation", col_x[1], ry)
    ry = paragraph(c, (
        "Revenue grew from $1.55M to $4.1M over twelve months. Eleven customers, four on "
        "multi-year supply agreements. Two customers qualified the material into production "
        "cell lines in 2025. A $3.2M Department of Energy award funds the pilot line. Net "
        "revenue retention of 141% with no customer losses to date."
    ), col_x[1], ry, col_w)

    ry = heading(c, "Team", col_x[1], ry)
    ry = paragraph(c, (
        "Dr. Anne Kowalski, CEO, previously led electrolyte R&D at Solid Power for nine "
        "years. Ravi Menon, CTO, holds 14 patents in sulfide processing. Elena Ford, VP "
        "Operations, scaled two specialty chemical plants from pilot to commercial volume."
    ), col_x[1], ry, col_w)

    ry = heading(c, "Financing & Use of Funds", col_x[1], ry)
    ry = paragraph(c, (
        "Raising $12M Series A at a $58M pre-money valuation; $7.5M committed with "
        "Ardent Climate leading. $9.2M raised previously. Proceeds fund the 400 kg/week "
        "production line ($7M), qualification engineering ($3M), and working capital ($2M), "
        "giving 26 months of runway to a targeted $14M revenue run-rate."
    ), col_x[1], ry, col_w)

    ry = heading(c, "Information Not Provided", col_x[1], ry)
    c.setFillColor(INK)
    c.setFont("Helvetica", BODY_SIZE)
    for item in ("Customer acquisition cost", "Detailed cost-per-kilogram breakdown",
                 "Named customer references"):
        for i, line in enumerate(wrap(item, "Helvetica", BODY_SIZE, col_w - 9)):
            ry -= BODY_SIZE * LINE_HEIGHT
            if i == 0:
                c.setFillColor(MUTED)
                c.drawString(col_x[1], ry, "•")
                c.setFillColor(INK)
            c.drawString(col_x[1] + 9, ry, line)

    # -- Footer -----------------------------------------------------------
    fy = MARGIN_B * 0.62
    c.setStrokeColor(RULE)
    c.setLineWidth(0.5)
    c.line(MARGIN_L, fy + 11, width - MARGIN_R, fy + 11)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.8)
    c.drawString(MARGIN_L, fy, (
        "Confidential. Prepared for accredited investors. All figures are as stated in the "
        "company's materials and have not been independently verified."
    ))

    c.showPage()
    c.save()
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"Wrote sample reference executive summary: {path}")
