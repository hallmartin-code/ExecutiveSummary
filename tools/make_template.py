"""Render the canonical document structure template to PDF.

Reads ``templates/executive_summary_template.json`` and renders it through the
real layout engine with placeholder content, producing
``templates/executive_summary_template.pdf``.

Because it goes through the same engine as a real run, the output is a faithful
specification rather than a hand-drawn approximation: if the template changes,
this changes with it.

The output contains NO company data. Every field is a bracketed placeholder
describing what belongs there, so the file doubles as a visual specification and
a valid ``--reference`` input.

    python tools/make_template.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.analysis.reference_analyzer import (  # noqa: E402
    CANONICAL_TEMPLATE_PATH,
    load_template,
)
from src.analysis.summary_generator import (  # noqa: E402
    ContactBlock,
    ExecutiveSummary,
    FinancialTable,
    SidebarEntry,
)
from src.rendering.pdf_generator import render_pdf  # noqa: E402
from src.rendering.pdf_validator import render_preview_png  # noqa: E402

OUT = ROOT / "templates" / "executive_summary_template.pdf"
PREVIEW = ROOT / "templates" / "executive_summary_template_preview.png"

# Placeholder copy per section. Length approximates each section's share of the
# page so the density measured from this file matches a real summary.
PLACEHOLDERS: dict[str, object] = {
    "corporate_summary": (
        "[What the company does.] [How it works.] [Who buys it.] [The milestone it targets.]"
    ),
    "key_facts": [
        "[Problem scale]: [Quantified magnitude, as stated in the deck.]",
        "[Status quo failure]: [Why existing alternatives do not work.]",
        "[Cost of the problem]: [What it costs today, and who bears it.]",
    ],
    "innovative_solution": (
        "[Why now]: [What changed to make this possible.] [The mechanism delivered.]"
    ),
    "products_platform": (
        "[Product specification and status.] [Business model]: [How revenue is earned, "
        "with pricing and margins.] [Intellectual property]: [Patents and status.]"
    ),
    "target_markets": [
        "[Core market]: [TAM / SAM / SOM with the deck's methodology.]",
        "[Adjacent verticals]: [Other segments the deck sizes.]",
    ],
    "competitive_advantages": [
        "[Operational advantage versus the status quo.]",
        "[Economic advantage, with named incumbents' cost position.]",
        "[Regulatory or IP advantage]: [Pathway or protection claimed.]",
    ],
    "commercial_validation": [
        "[Completed diligence, named backers and partners.]",
        "[Category proof point]: [Adoption evidence with figures and date.]",
        "[Distribution or customer interest, and its stage.]",
    ],
    "financing_opportunity": (
        "[Amount and purpose]: [Instrument, cap, discount, interest.] "
        "[Exit]: [Route and acquirers.]"
    ),
    "use_of_funds": (
        "[What the capital buys, in milestone order with the deck's dates.]"
    ),
    "management_team": [
        "[Name, credential — Role.] [Experience and commitment.]",
        "[Name, credential — Role.] [Experience and commitment.]",
        "[Advisors]: [Name (domain); name (domain).]",
    ],
    "investment_thesis": (
        "[Closing argument: market condition, defensible advantage, evidence, and exit.]"
    ),
}

SIDEBAR_PLACEHOLDERS = {
    "stage": "[Financing stage]",
    "raise": "[Amount and tranche structure]",
    "terms": "[Cap · discount · interest · rights]",
    "industry": "[Sector — sub-sector]",
    "reg_path": "[Regulatory route and class]",
    "market_size": "[Core market size; adjacent verticals]",
    "use_of_funds": "[What the capital buys]",
}

FINANCIALS = FinancialTable(
    columns=["", "[Year]", "[Year]", "[Year]", "[Year]", "[Year]"],
    rows=[
        ["Revenue", "[value]", "[value]", "[value]", "[value]", "[value]"],
        ["Gross Profit", "[value]", "[value]", "[value]", "[value]", "[value]"],
        ["EBITDA", "[value]", "[value]", "[value]", "[value]", "[value]"],
        ["Net Income", "[value]", "[value]", "[value]", "[value]", "[value]"],
        ["Cash Flow", "[value]", "[value]", "[value]", "[value]", "[value]"],
    ],
    caption="[Units. The inflection points: when EBITDA and cash flow turn positive.]",
)


def build() -> Path:
    template = load_template(CANONICAL_TEMPLATE_PATH)

    summary = ExecutiveSummary(
        company_name="[COMPANY NAME]",
        tagline="[Factual one-line descriptor of the business, 14 words maximum]",
        document_label=template.title_bar.document_label,
        contact=ContactBlock(
            name="[Contact name]",
            role="[Title]",
            email="[email address]",
            phone="[phone number]",
        ),
        not_stated=[
            "[Investor-material field absent from the deck]",
            "[Investor-material field absent from the deck]",
            "[Investor-material field absent from the deck]",
        ],
        projected_financials=FINANCIALS,
        footer_note=(
            "Confidential. Prepared for accredited investors. All figures are as stated "
            "in the [company] pitch deck and have not been independently verified. "
            "Compiled [date]."
        ),
    )

    for field in template.masthead.sidebar_fields:
        summary.sidebar.append(
            SidebarEntry(
                key=field.key,
                label=field.label,
                value=SIDEBAR_PLACEHOLDERS.get(field.key, "[value]"),
            )
        )

    for key, value in PLACEHOLDERS.items():
        if hasattr(summary, key):
            setattr(summary, key, value)

    path, page = render_pdf(summary, template, OUT)
    render_preview_png(path, PREVIEW)
    if not page.fits:
        print(f"WARNING: placeholder content overflows by {page.overflow_pt:.1f}pt")
    return path


if __name__ == "__main__":
    written = build()
    print(f"Wrote document structure template: {written}")
