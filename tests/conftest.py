"""Shared fixtures. All test decks are synthesised so tests need no external files."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reportlab.lib.pagesizes import landscape, letter  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

# Deck content used by both the PDF and PPTX fixtures, so parser tests can
# assert the same facts regardless of source format.
SAMPLE_SLIDES: list[tuple[str, list[str]]] = [
    ("Nimbus Freight", ["Freight brokerage automation for mid-market shippers"]),
    ("Problem", [
        "US shippers spend 14 hours per week booking freight manually.",
        "Manual brokerage costs the industry $8.4 billion annually.",
    ]),
    ("Solution", [
        "Nimbus automates load matching and rate negotiation.",
        "Integrates with existing TMS platforms in under two weeks.",
    ]),
    ("Market", ["TAM $12.5B", "SAM $3.2B", "SOM $320M", "Target customer: mid-market shippers"]),
    ("Business Model", [
        "SaaS subscription plus 2% of booked freight value.",
        "Average contract value $84,000 per year.",
        "Gross margin 74%",
    ]),
    ("Traction", [
        "2024 Revenue $2,000,000",
        "2025 Revenue $3,000,000",
        "ARR $3.2M",
        "47 customers",
        "Net revenue retention 118%",
    ]),
    ("Team", [
        "Dana Whitfield, CEO, 12 years at C.H. Robinson.",
        "Amir Haddad, CTO, previously staff engineer at Flexport.",
    ]),
    ("Competition", [
        "Competitors: Convoy, Uber Freight, Loadsmart.",
        "Two granted patents covering automated rate negotiation.",
    ]),
    ("The Ask", [
        "Raising $8M Series A",
        "$3.5M committed",
        "Pre-money valuation $32M",
        "Use of proceeds: engineering $4M, sales $3M, working capital $1M",
    ]),
]


def _write_pdf(path: Path, slides: list[tuple[str, list[str]]]) -> Path:
    c = canvas.Canvas(str(path), pagesize=landscape(letter))
    width, height = landscape(letter)
    for title, lines in slides:
        c.setFont("Helvetica-Bold", 28)
        c.drawString(60, height - 80, title)
        c.setFont("Helvetica", 16)
        y = height - 130
        for line in lines:
            c.drawString(60, y, line)
            y -= 26
        c.showPage()
    c.save()
    return path


@pytest.fixture(scope="session")
def sample_pdf_deck(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("decks") / "nimbus_deck.pdf"
    return _write_pdf(path, SAMPLE_SLIDES)


@pytest.fixture(scope="session")
def sample_pptx_deck(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    path = tmp_path_factory.mktemp("decks") / "nimbus_deck.pptx"
    prs = Presentation()
    blank = prs.slide_layouts[6]

    for index, (title, lines) in enumerate(SAMPLE_SLIDES):
        slide = prs.slides.add_slide(blank)

        title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.4), Inches(9), Inches(1))
        title_frame = title_box.text_frame
        title_frame.text = title
        title_frame.paragraphs[0].runs[0].font.size = Pt(32)

        body_box = slide.shapes.add_textbox(Inches(0.5), Inches(1.6), Inches(9), Inches(4.5))
        body_frame = body_box.text_frame
        body_frame.text = lines[0]
        for line in lines[1:]:
            body_frame.add_paragraph().text = line

        # One grouped shape, to exercise recursive shape traversal.
        if index == 5:
            group_a = slide.shapes.add_textbox(Inches(0.5), Inches(5.6), Inches(4), Inches(0.5))
            group_a.text_frame.text = "Pipeline 120 qualified shippers"

        # One table, on the market slide.
        if index == 3:
            table = slide.shapes.add_table(2, 2, Inches(5.2), Inches(5.2),
                                           Inches(4), Inches(1)).table
            table.cell(0, 0).text = "Segment"
            table.cell(0, 1).text = "Value"
            table.cell(1, 0).text = "TAM"
            table.cell(1, 1).text = "$12.5B"

        notes = slide.notes_slide
        notes.notes_text_frame.text = f"Speaker notes for {title}."

    prs.save(str(path))
    return path


@pytest.fixture(scope="session")
def sample_reference(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A two-column reference one-pager for template-inference tests."""
    from reportlab.lib.colors import HexColor

    path = tmp_path_factory.mktemp("reference") / "reference.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    navy = HexColor("#12395C")

    c.setFillColor(navy)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(52, height - 70, "Example Company")
    c.setFillColor(HexColor("#5A6672"))
    c.setFont("Helvetica", 10.5)
    c.drawString(52, height - 86, "A factual descriptor of the business")

    left_sections = [
        ("INVESTMENT OPPORTUNITY", 5),
        ("MARKET & BUSINESS MODEL", 4),
        ("COMPETITIVE POSITION", 4),
    ]
    right_sections = [
        ("TRACTION & VALIDATION", 5),
        ("TEAM", 3),
        ("FINANCING & USE OF FUNDS", 4),
    ]

    for x, sections in ((52, left_sections), (320, right_sections)):
        y = height - 130
        for heading, line_count in sections:
            c.setFillColor(navy)
            c.setFont("Helvetica-Bold", 8.0)
            c.drawString(x, y, heading)
            y -= 13
            c.setFillColor(HexColor("#1A1A1A"))
            c.setFont("Helvetica", 9.2)
            for _ in range(line_count):
                c.drawString(x, y, "Body copy line establishing the density of this section.")
                y -= 12
            y -= 9

    # Metric callouts, larger than body copy.
    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(navy)
    for i, value in enumerate(("$4.1M", "164%", "11", "$2.8B")):
        c.drawString(60 + i * 130, height - 108, value)

    c.setFillColor(HexColor("#5A6672"))
    c.setFont("Helvetica", 6.8)
    c.drawString(52, 30, "Confidential. Prepared for accredited investors.")
    c.showPage()
    c.save()
    return path
