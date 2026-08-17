"""Compose the executive summary content model.

This is the last stage before layout. Nothing reaches the renderer that has not
passed the evidence gate and the banned-language filter.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from ..config import PipelineConfig
from ..utils.logging import get_logger
from .evidence import Classification, EvidenceLedger
from .investor_schema import Fact, InvestorData
from .metric_engine import parse_money
from .reference_analyzer import TemplateSpec

log = get_logger("summary")


# ---------------------------------------------------------------------------
# Content model (Step 16)
# ---------------------------------------------------------------------------


class MetricCallout(BaseModel):
    label: str
    value: str
    source_slides: list[int] = Field(default_factory=list)
    classification: Classification = Classification.EXPLICIT


class SidebarEntry(BaseModel):
    """One labelled row in the masthead sidebar."""

    key: str
    label: str
    value: str
    source_slides: list[int] = Field(default_factory=list)


class ContactBlock(BaseModel):
    name: str | None = None
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    source_slides: list[int] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not any((self.name, self.role, self.email, self.phone))


class FinancialTable(BaseModel):
    """A compact projections table lifted verbatim from the deck."""

    columns: list[str] = Field(default_factory=list)   # e.g. year headers
    rows: list[list[str]] = Field(default_factory=list)  # first cell is the row label
    caption: str | None = None
    source_slides: list[int] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.rows or not self.columns


# Sections that hold plain prose.
PROSE_SECTIONS = (
    "corporate_summary",
    "innovative_solution",
    "products_platform",
    "financing_opportunity",
    "use_of_funds",
    "investment_thesis",
    "risks",
)

# Sections that hold a list of items. Items may carry a bold lead-in label,
# written as "Lead-in: detail" and rendered with the lead-in in bold.
LIST_SECTIONS = (
    "key_facts",
    "target_markets",
    "competitive_advantages",
    "commercial_validation",
    "management_team",
)

ALL_CONTENT_SECTIONS = PROSE_SECTIONS + LIST_SECTIONS + ("projected_financials",)


class ExecutiveSummary(BaseModel):
    """The finished page content, independent of how it will be laid out."""

    company_name: str
    tagline: str | None = None
    document_label: str | None = None
    meta_line: str | None = None

    # Masthead sidebar
    sidebar: list[SidebarEntry] = Field(default_factory=list)
    contact: ContactBlock = Field(default_factory=ContactBlock)
    not_stated: list[str] = Field(default_factory=list)

    # Masthead main column
    corporate_summary: str = ""
    key_facts: list[str] = Field(default_factory=list)
    innovative_solution: str | None = None

    # Body
    products_platform: str | None = None
    target_markets: list[str] = Field(default_factory=list)
    competitive_advantages: list[str] = Field(default_factory=list)
    commercial_validation: list[str] = Field(default_factory=list)
    projected_financials: FinancialTable = Field(default_factory=FinancialTable)
    financing_opportunity: str | None = None
    use_of_funds: str | None = None
    management_team: list[str] = Field(default_factory=list)
    investment_thesis: str | None = None
    risks: str | None = None

    metrics: list[MetricCallout] = Field(default_factory=list)
    source_slides: dict[str, list[int]] = Field(default_factory=dict)
    footer_note: str | None = None

    # -- accessors ----------------------------------------------------------

    def section_text(self, key: str) -> Any:
        return getattr(self, key, None)

    def section_as_text(self, key: str) -> str:
        """Flatten any section to a single string, for grounding checks."""
        value = getattr(self, key, None)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(str(v) for v in value)
        if isinstance(value, FinancialTable):
            parts = list(value.columns)
            for row in value.rows:
                parts.extend(row)
            if value.caption:
                parts.append(value.caption)
            return " ".join(parts)
        return ""

    def has_content(self, key: str) -> bool:
        value = getattr(self, key, None)
        if isinstance(value, FinancialTable):
            return not value.is_empty()
        if isinstance(value, list):
            return bool(value)
        return bool(value and str(value).strip())

    def word_count(self) -> int:
        return sum(
            len(self.section_as_text(key).split()) for key in ALL_CONTENT_SECTIONS
        )

    def sidebar_value(self, key: str) -> str | None:
        for entry in self.sidebar:
            if entry.key == key:
                return entry.value
        return None


# ---------------------------------------------------------------------------
# Language hygiene
# ---------------------------------------------------------------------------

BANNED_WORDS = [
    "revolutionary", "groundbreaking", "ground-breaking", "massive", "game-changing",
    "game changer", "world-class", "world class", "disruptive", "disrupting",
    "incredible", "unique", "cutting-edge", "cutting edge", "best-in-class",
    "transformative", "transforming", "paradigm", "unprecedented", "exciting",
    "amazing", "state-of-the-art", "seamless", "visionary", "innovative",
    "revolutionize", "revolutionise", "unparalleled", "phenomenal", "stellar",
]

# Neutral replacements keep the sentence grammatical after a banned word is removed.
_REPLACEMENTS = {
    "revolutionary": "", "groundbreaking": "", "ground-breaking": "",
    "game-changing": "", "game changer": "product", "world-class": "",
    "best-in-class": "", "cutting-edge": "", "cutting edge": "",
    "state-of-the-art": "", "unprecedented": "", "transformative": "",
    "disruptive": "", "incredible": "", "amazing": "", "exciting": "",
    "seamless": "", "visionary": "", "innovative": "", "unparalleled": "",
    "phenomenal": "", "stellar": "", "unique": "distinct", "massive": "large",
    "paradigm": "approach", "revolutionize": "change", "revolutionise": "change",
}


def strip_marketing_language(text: str | None) -> tuple[str | None, list[str]]:
    """Remove banned promotional words. Returns the cleaned text and what was removed."""
    if not text:
        return text, []
    cleaned = text
    removed: list[str] = []
    for word in BANNED_WORDS:
        pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
        if pattern.search(cleaned):
            removed.append(word)
            cleaned = pattern.sub(_REPLACEMENTS.get(word, ""), cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"\ba\s+(?=[aeiouAEIOU])", "an ", cleaned)
    cleaned = cleaned.replace("!", ".").strip()
    return (cleaned or None), removed


# Inline citations like "(slide 19)" or "(slides 2, 9)" belong in the audit JSON,
# not on an investor one-pager. They are captured before being stripped.
# Handles "(slide 19)", "(slides 2, 9)", "(slides 26-28)", and "(see slides 3 and 4)".
_CITATION_RE = re.compile(
    r"\s*\((?:see\s+)?slides?\.?\s*([\d,\s&\-â€“â€”]+(?:and\s*\d+\s*)?)\)", re.IGNORECASE
)
_RANGE_RE = re.compile(r"(\d+)\s*[\-â€“â€”]\s*(\d+)")


def strip_slide_citations(text: str | None) -> tuple[str | None, list[int]]:
    """Remove inline slide references, returning the slide numbers found."""
    if not text:
        return text, []
    slides: list[int] = []

    def _add(n: int) -> None:
        if n > 0 and n not in slides:
            slides.append(n)

    def _capture(match: re.Match[str]) -> str:
        body = match.group(1)
        # Expand ranges first, then take any remaining standalone numbers.
        for start, end in _RANGE_RE.findall(body):
            lo, hi = int(start), int(end)
            if lo <= hi and hi - lo <= 40:
                for n in range(lo, hi + 1):
                    _add(n)
        remainder = _RANGE_RE.sub(" ", body)
        for token in re.findall(r"\d+", remainder):
            _add(int(token))
        return ""

    cleaned = _CITATION_RE.sub(_capture, text)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return (cleaned or None), sorted(slides)


# Abbreviations whose full stop does not end a sentence. Without this, trimming
# "â€¦in the US. The U.S. Air Force usesâ€¦" can leave a paragraph ending "The U.S."
_ABBREVIATIONS = {
    "u.s", "u.k", "u.s.a", "e.g", "i.e", "etc", "vs", "inc", "corp", "co", "ltd",
    "llc", "plc", "dr", "mr", "mrs", "ms", "prof", "st", "jr", "sr", "no", "fig",
    "approx", "est", "dept", "gov", "univ", "ph.d", "m.d", "b.s", "m.s", "d.c",
}

_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")


def last_sentence_end(text: str) -> int:
    """Index of the last real sentence-ending punctuation mark, or -1.

    Skips full stops that belong to abbreviations and single-letter initials.
    """
    best = -1
    for match in _SENTENCE_END_RE.finditer(text):
        if match.group(0) == ".":
            preceding = text[: match.start()]
            token = re.split(r"[\s(\[\"']", preceding)[-1].lower().rstrip(".")
            # "U.S", "Ph.D", "A" (an initial) â€” all non-terminal.
            if token in _ABBREVIATIONS or re.fullmatch(r"(?:[a-z]\.)*[a-z]", token):
                continue
        best = match.start()
    return best


_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)


_LIST_MARKER_RE = re.compile(r"^\s*(?:\d{1,2}[.)]|[–—\-•▪◦*])\s+")


def strip_list_marker(text: str | None) -> str | None:
    """Remove a leading enumeration or bullet glyph from a list item.

    The renderer draws the marker itself, so an item that arrives already
    numbered would otherwise print as "1. 1. Acute lung injury burden".
    """
    if not text:
        return text
    return _LIST_MARKER_RE.sub("", text).strip() or None


def strip_markdown_emphasis(text: str | None) -> str | None:
    """Remove Markdown emphasis markers from generated prose.

    Bold is expressed structurally in this document ("Label: detail" is set with
    the label in bold), not with Markdown. A model that reaches for ``**bold**``
    anyway would otherwise print the asterisks literally on the page.
    """
    if not text:
        return text
    out = _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    out = re.sub(r"(?<!\w)\*{1,3}(?!\w)", "", out)   # stray markers
    out = re.sub(r"(?<!\w)_{2,}(?!\w)", "", out)
    # "**Label:** detail" collapses to "Label: detail"; tidy any doubled colon.
    out = re.sub(r":\s*:", ":", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def _trim_to_words(text: str | None, max_words: int) -> str | None:
    """Cut *text* to a word budget on a sentence boundary where possible."""
    if not text:
        return text
    words = text.split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    # Prefer ending on a complete sentence, even at the cost of a good deal of
    # length: a paragraph that stops mid-clause reads as a rendering defect,
    # whereas a shorter complete one reads as edited.
    last_stop = last_sentence_end(truncated)
    if last_stop > len(truncated) * 0.35:
        return truncated[: last_stop + 1].strip()
    return truncated.rstrip(",;:- ") + "."


# ---------------------------------------------------------------------------
# Metric selection
# ---------------------------------------------------------------------------

# (label, section, field, priority) â€” priority orders the callout band.
_METRIC_CANDIDATES: list[tuple[str, str, str, int]] = [
    ("ARR", "traction", "arr", 10),
    ("Revenue", "traction", "revenue", 10),
    ("Revenue growth", "growth_metrics", "revenue_growth", 9),
    ("Customers", "customers", "customer_count", 9),
    ("Gross margin", "business_model", "gross_margin", 8),
    ("TAM", "market", "tam", 8),
    ("SAM", "market", "sam", 6),
    ("SOM", "market", "som", 6),
    ("Raising", "fundraising", "raise_amount", 10),
    ("Valuation", "fundraising", "valuation", 7),
    ("Raised to date", "fundraising", "capital_raised_to_date", 7),
    ("Committed", "fundraising", "committed", 7),
    ("Pricing", "business_model", "pricing", 5),
    ("Pipeline", "customers", "pipeline", 5),
    ("Runway", "financial_metrics", "runway", 6),
    ("NRR", "financial_metrics", "nrr", 7),
    ("LTV/CAC", "financial_metrics", "ltv", 5),
    ("Retention", "traction", "retention", 6),
    ("Pilots", "traction", "pilots", 6),
    ("Contracts", "traction", "contracts", 6),
    ("CAGR", "growth_metrics", "cagr", 7),
]

_MAX_VALUE_CHARS = 14
_MAX_LABEL_CHARS = 22


# Labels whose callout is meaningless unless the value carries a currency or scale.
# "TAM 100" (from "100 million manual ventilators") is a unit count, not a market size.
_CURRENCY_LABELS = {
    "arr", "revenue", "tam", "sam", "som", "raising", "valuation",
    "raised to date", "committed", "pricing", "ltv/cac", "bookings",
}

_CURRENCY_RE = re.compile(r"[$â‚¬Â£]|\b\d+(?:\.\d+)?\s?(?:[KMB]|thousand|million|billion)\b", re.IGNORECASE)


def _metric_value_ok(value: str) -> bool:
    """A callout must be a short, punchy figure â€” not a sentence."""
    v = (value or "").strip()
    return bool(v) and len(v) <= _MAX_VALUE_CHARS and bool(re.search(r"\d", v))


def tidy_terminal_punctuation(text: str | None) -> str | None:
    """Ensure a section ends as a finished sentence.

    A paragraph that runs out of word budget mid-list can end on a semicolon or
    comma, which reads as truncation on the page.
    """
    if not text:
        return text
    cleaned = text.rstrip()
    if not cleaned:
        return None
    if cleaned[-1] in ".!?":
        return cleaned
    # Drop a dangling connector before closing the sentence.
    cleaned = re.sub(r"[,;:]\s*(?:and|or|with|including)?\s*$", "", cleaned).rstrip()
    cleaned = cleaned.rstrip(",;:- ")
    if not cleaned:
        return None
    return cleaned if cleaned[-1] in ".!?" else cleaned + "."


def _prefix_label(label: str, prefix: str) -> str:
    """Add a qualifier to a metric label without truncating away its meaning.

    Naively prefixing then slicing turns "2029-30 Revenue Growth" into
    "Proj. 2029-30 Revenue", which reads as a revenue figure rather than a growth
    rate. Leading qualifiers are dropped instead, because the trailing noun
    phrase is what names the metric.
    """
    words = label.split()
    while words and len(f"{prefix} {' '.join(words)}") > _MAX_LABEL_CHARS:
        if len(words) == 1:
            break
        words.pop(0)
    combined = f"{prefix} {' '.join(words)}".strip()
    return combined[:_MAX_LABEL_CHARS]


def _metric_value_ok_for_label(label: str, value: str) -> bool:
    """Additionally require a money-shaped value for money-shaped labels."""
    if not _metric_value_ok(value):
        return False
    if label.strip().lower() in _CURRENCY_LABELS:
        return bool(_CURRENCY_RE.search(value))
    return True


def select_metrics(data: InvestorData, limit: int) -> list[MetricCallout]:
    """Choose the strongest available figures. Never pads empty slots."""
    scored: list[tuple[int, MetricCallout]] = []
    seen_values: set[str] = set()

    for label, section_name, field_name, priority in _METRIC_CANDIDATES:
        section = getattr(data, section_name, None)
        if section is None:
            continue
        fact = getattr(section, field_name, None)
        if not isinstance(fact, Fact) or not fact.present:
            continue

        value = re.sub(r"\s+", " ", fact.value or "").strip()
        # Reduce a long phrase to its leading figure if one is present.
        if not _metric_value_ok_for_label(label, value):
            match = re.search(
                r"\$\s?\d[\d,.]*\s?(?:[KMB]|thousand|million|billion)?|"
                r"\d[\d,.]*\s?(?:[KMB]\b|%|x\b)",
                value, re.IGNORECASE,
            )
            if not match:
                continue
            value = re.sub(r"\s+", " ", match.group(0)).strip()
            # Normalise "$5 M" to "$5M" so callouts read cleanly.
            value = re.sub(r"(\d)\s+([KMB])\b", r"\1\2", value, flags=re.IGNORECASE)
            value = re.sub(r"(?i)(\d)\s?(thousand|million|billion)",
                           lambda m: m.group(1) + m.group(2)[0].upper(), value)
            if not _metric_value_ok_for_label(label, value):
                continue

        key = value.lower().replace(" ", "")
        if key in seen_values:
            continue
        seen_values.add(key)

        scored.append(
            (
                priority,
                MetricCallout(
                    label=label[:_MAX_LABEL_CHARS],
                    value=value,
                    source_slides=fact.source_slides,
                    classification=fact.classification,
                ),
            )
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    chosen = [m for _, m in scored[:limit]]

    # Avoid showing SAM/SOM without TAM, which reads as an arbitrary figure.
    labels = {m.label for m in chosen}
    if "TAM" not in labels:
        chosen = [m for m in chosen if m.label not in ("SAM", "SOM")]

    log.info(
        "Selected %d metric callout(s): %s",
        len(chosen), ", ".join(f"{m.label} {m.value}" for m in chosen) or "none",
    )
    return chosen


# ---------------------------------------------------------------------------
# Deterministic composition (no AI)
# ---------------------------------------------------------------------------


def _join_facts(facts: list[Fact], limit: int = 3, sep: str = "; ") -> str:
    values = [f.value for f in facts if f.present and f.value][:limit]
    return sep.join(values)


def _sentence(text: str | None) -> str:
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text).strip().rstrip(";,")
    if t and not t.endswith((".", "?", "!")):
        t += "."
    return t[0].upper() + t[1:] if t else ""


def compose_deterministic(
    data: InvestorData, template: TemplateSpec, config: PipelineConfig
) -> ExecutiveSummary:
    """Build the summary from schema values alone, with no generative step.

    Sentences are assembled from extracted values, so the output is necessarily
    grounded â€” it is drier than the AI path but never invents anything.
    """
    name = data.resolved_company_name(config.company_name_override)
    budgets = {s.key: s.approx_words for s in template.sections}
    summary = ExecutiveSummary(company_name=name)

    def budget(key: str, default: int) -> int:
        return budgets.get(key, default)

    def item(label: str | None, value: str | None) -> str | None:
        """Format a list item with an optional bold lead-in label."""
        if not value:
            return None
        body = _sentence(value)
        return f"{label}: {body}" if label else body

    # -- Masthead sidebar ---------------------------------------------------
    sidebar_sources: list[tuple[str, str, Fact]] = [
        ("stage", "Stage", data.company.stage),
        ("raise", "Raise", data.fundraising.raise_amount),
        ("terms", "Terms", data.fundraising.terms),
        ("industry", "Industry", data.company.industry),
        ("reg_path", "Reg. Path", data.technology.regulatory_pathway),
        ("market_size", "Market Size", data.market.tam),
        ("use_of_funds", "Use of Funds", data.use_of_funds.summary),
    ]
    for key, label, fact in sidebar_sources:
        if fact.present and fact.value:
            summary.sidebar.append(
                SidebarEntry(
                    key=key, label=label,
                    value=_trim_to_words(fact.value, 18) or fact.value,
                    source_slides=fact.source_slides,
                )
            )

    lead = next(
        (m for m in data.team.founders + data.team.executives
         if m.role and "ceo" in m.role.lower()),
        (data.team.founders or data.team.executives or [None])[0],
    )
    if lead is not None:
        summary.contact = ContactBlock(
            name=lead.name, role=lead.role, source_slides=lead.source_slides
        )
    if data.company.website.present:
        summary.contact.email = data.company.website.value

    # -- Corporate summary --------------------------------------------------
    parts: list[str] = []
    if data.company.description.present:
        parts.append(_sentence(data.company.description.value))
    elif data.solution.product_service.present:
        parts.append(_sentence(f"{name} {data.solution.product_service.value}"))
    if data.solution.value_proposition.present:
        parts.append(_sentence(data.solution.value_proposition.value))
    if data.market.tam.present:
        parts.append(_sentence(f"The deck sizes the core market at {data.market.tam.value}"))
    summary.corporate_summary = _trim_to_words(
        " ".join(p for p in parts if p), budget("corporate_summary", 80)
    ) or ""

    # -- Key facts ----------------------------------------------------------
    for label, fact in (
        ("Problem scale", data.problem.magnitude),
        ("Cost of the problem", data.problem.cost_of_problem),
        ("Status quo", data.problem.existing_alternatives),
        ("Urgency", data.problem.urgency),
    ):
        entry = item(label, fact.value if fact.present else None)
        if entry:
            summary.key_facts.append(entry)
    summary.key_facts = summary.key_facts[:4]

    # -- Innovative solution ------------------------------------------------
    solution_parts = [
        f.value for f in (data.solution.how_it_works, data.technology.core_technology)
        if f.present
    ]
    summary.innovative_solution = _trim_to_words(
        " ".join(_sentence(s) for s in solution_parts), budget("innovative_solution", 70)
    )

    # -- Products & platform ------------------------------------------------
    product_parts = []
    for label, fact in (
        (None, data.product.specification),
        (None, data.product.development_status),
        ("Business model", data.business_model.revenue_model),
        ("Pricing", data.business_model.pricing),
        ("Intellectual property", data.intellectual_property.patent_status),
    ):
        entry = item(label, fact.value if fact.present else None)
        if entry:
            product_parts.append(entry)
    summary.products_platform = _trim_to_words(
        " ".join(product_parts), budget("products_platform", 90)
    )

    # -- Target markets -----------------------------------------------------
    if data.market.tam.present:
        market = f"TAM {data.market.tam.value}"
        if data.market.sam.present:
            market += f" Â· SAM {data.market.sam.value}"
        if data.market.som.present:
            market += f" Â· SOM {data.market.som.value}"
        summary.target_markets.append(item("Core market", market) or market)
    for label, fact in (
        ("Target customer", data.market.target_customer),
        ("Geography", data.market.geography),
        ("Methodology", data.market.methodology),
    ):
        entry = item(label, fact.value if fact.present else None)
        if entry:
            summary.target_markets.append(entry)

    # -- Competitive advantages ---------------------------------------------
    for label, fact in (
        (None, data.competitive_advantage.differentiation),
        (None, data.competitive_advantage.defensibility),
        ("Switching costs", data.competitive_advantage.switching_costs),
        ("Regulatory path", data.technology.regulatory_pathway),
    ):
        entry = item(label, fact.value if fact.present else None)
        if entry:
            summary.competitive_advantages.append(entry)
    competitors = _join_facts(data.competitive_advantage.competitors, 5, " Â· ")
    if competitors:
        summary.competitive_advantages.append(f"Cost position vs. incumbents: {competitors}")

    # -- Commercial validation ----------------------------------------------
    for label, fact in (
        ("Revenue", data.traction.revenue),
        ("ARR", data.traction.arr),
        ("Customers", data.customers.customer_count),
        ("Pilots", data.traction.pilots),
        ("Contracts", data.traction.contracts),
        ("Retention", data.traction.retention),
    ):
        entry = item(label, fact.value if fact.present else None)
        if entry:
            summary.commercial_validation.append(entry)
    for group in (
        data.traction.validation_evidence,
        data.traction.regulatory_milestones,
        data.traction.partnerships,
    ):
        joined = _join_facts(group, 2)
        if joined:
            summary.commercial_validation.append(_sentence(joined))
    summary.commercial_validation = summary.commercial_validation[:6]

    # -- Projected financials ------------------------------------------------
    summary.projected_financials = _build_financial_table(data)

    # -- Financing opportunity -----------------------------------------------
    fin_parts = []
    if data.fundraising.raise_amount.present:
        f = f"{name} is raising {data.fundraising.raise_amount.value}"
        if data.fundraising.instrument.present:
            f += f" via {data.fundraising.instrument.value}"
        if data.fundraising.valuation.present:
            f += f" at {data.fundraising.valuation.value}"
        fin_parts.append(_sentence(f))
    for label, fact in (
        (None, data.fundraising.terms),
        ("Raised to date", data.fundraising.capital_raised_to_date),
        ("Committed", data.fundraising.committed),
        ("Lead investor", data.fundraising.lead_investor),
    ):
        entry = item(label, fact.value if fact.present else None)
        if entry:
            fin_parts.append(entry)
    summary.financing_opportunity = _trim_to_words(
        " ".join(fin_parts), budget("financing_opportunity", 80)
    )

    # -- Use of funds ---------------------------------------------------------
    use_parts = []
    if data.use_of_funds.summary.present:
        use_parts.append(_sentence(data.use_of_funds.summary.value))
    line_items = _join_facts(data.use_of_funds.line_items, 5, "; ")
    if line_items:
        use_parts.append(_sentence(line_items))
    upcoming = _join_facts(data.milestones.upcoming, 3)
    if upcoming:
        use_parts.append(_sentence(f"Milestones: {upcoming}"))
    summary.use_of_funds = _trim_to_words(" ".join(use_parts), budget("use_of_funds", 70))

    # -- Management team -------------------------------------------------------
    for member in (data.team.founders + data.team.executives)[:4]:
        bits = [member.name]
        if member.role:
            bits.append(f"â€” {member.role}.")
        detail = member.credentials or ""
        if member.commitment:
            detail = f"{detail} ({member.commitment})".strip()
        summary.management_team.append(
            f"{' '.join(bits)} {detail}".strip()
        )
    advisors = "; ".join(m.one_line() for m in data.team.advisors[:4])
    if advisors:
        summary.management_team.append(f"Advisors: {advisors}")

    # -- Investment thesis ------------------------------------------------------
    thesis_parts = []
    if data.market.tam.present and data.competitive_advantage.differentiation.present:
        thesis_parts.append(
            _sentence(
                f"A {data.market.tam.value} market addressed with "
                f"{data.competitive_advantage.differentiation.value}"
            )
        )
    if data.fundraising.raise_amount.present:
        thesis_parts.append(
            _sentence(
                f"{data.fundraising.raise_amount.value} is sought to reach the next milestone"
            )
        )
    summary.investment_thesis = _trim_to_words(
        " ".join(thesis_parts), budget("investment_thesis", 70)
    )

    tagline = data.company.tagline.value if data.company.tagline.present else None
    if not tagline and data.solution.product_service.present:
        tagline = _trim_to_words(data.solution.product_service.value, 14)
    summary.tagline = tagline

    return summary


def _build_financial_table(data: InvestorData) -> FinancialTable:
    """Assemble the projections table from explicitly stated deck figures.

    Only rows whose values appear in the deck are included; nothing is
    interpolated to fill a year with no stated figure.
    """
    facts = [f for f in data.financial_metrics.projections if f.present and f.value]
    if not facts:
        facts = [f for f in data.financial_metrics.revenue_history if f.present and f.value]
    if not facts:
        return FinancialTable()

    year_re = re.compile(r"\b(20\d{2})\b")
    metric_re = re.compile(
        r"\b(revenue|gross profit|ebitda|net income|cash flow|cogs|opex|payroll)\b",
        re.IGNORECASE,
    )
    value_re = re.compile(r"\(?-?\$?\s?[\d,]+(?:\.\d+)?\)?[KMB]?")

    grid: dict[str, dict[str, str]] = {}
    years: list[str] = []
    slides: set[int] = set()

    for fact in facts:
        text = fact.value or ""
        year = year_re.search(text)
        metric = metric_re.search(text)
        if not year or not metric:
            continue
        y = year.group(1)
        label = metric.group(1).title()
        remainder = value_re.findall(year_re.sub(" ", text))
        if not remainder:
            continue
        grid.setdefault(label, {})[y] = remainder[-1].strip()
        if y not in years:
            years.append(y)
        slides.update(fact.source_slides)

    if not grid or len(years) < 2:
        return FinancialTable()

    years = sorted(years)[:6]
    order = ["Revenue", "Gross Profit", "Ebitda", "Net Income", "Cash Flow"]
    rows: list[list[str]] = []
    for label in order + [k for k in grid if k not in order]:
        if label not in grid:
            continue
        row = [label.replace("Ebitda", "EBITDA")]
        row.extend(grid[label].get(y, "â€“") for y in years)
        rows.append(row)
        if len(rows) >= 6:
            break

    return FinancialTable(
        columns=[""] + years,
        rows=rows,
        caption=None,
        source_slides=sorted(slides),
    )


# ---------------------------------------------------------------------------
# AI composition
# ---------------------------------------------------------------------------


def _investor_payload(data: InvestorData) -> dict[str, Any]:
    """Compact, fact-only view handed to the model."""
    payload: dict[str, Any] = {}
    for section_name, section in data:
        if section_name in ("sources", "missing_information"):
            continue
        if not hasattr(section, "__iter__"):
            continue
        section_out: dict[str, Any] = {}
        for field_name, value in section:  # type: ignore[misc]
            if isinstance(value, Fact):
                if value.present:
                    section_out[field_name] = {
                        "value": value.value,
                        "slides": value.source_slides,
                        "classification": value.classification.value,
                    }
            elif isinstance(value, list) and value:
                items = []
                for item in value:
                    if isinstance(item, Fact) and item.present:
                        items.append({"value": item.value, "slides": item.source_slides,
                                      "classification": item.classification.value})
                    elif hasattr(item, "one_line"):
                        items.append({"value": item.one_line(), "slides": item.source_slides,
                                      "classification": "EXPLICIT"})
                if items:
                    section_out[field_name] = items
        if section_out:
            payload[section_name] = section_out
    payload["missing_information"] = data.missing_information.items
    return payload


def compose_with_ai(
    data: InvestorData,
    template: TemplateSpec,
    config: PipelineConfig,
    provider: Any,
    ledger: EvidenceLedger,
) -> ExecutiveSummary | None:
    """Draft the summary with Claude, then reject any ungrounded section."""
    sections_payload = []
    for s in sorted(template.sections, key=lambda s: s.order):
        if s.style not in ("paragraph", "bullets", "numbered"):
            continue
        entry: dict[str, Any] = {
            "key": s.key,
            "heading": s.heading,
            "word_budget": s.approx_words,
            # A list section expects an array of items; a paragraph expects a string.
            "output_type": "list" if s.style in ("bullets", "numbered") else "string",
            "style": s.style,
        }
        # The template's content contract travels with the request, so each
        # section is written against the same brief regardless of the reference.
        if s.guidance:
            entry["must_answer"] = s.guidance
        if s.source_fields:
            entry["use_these_fields"] = s.source_fields
        sections_payload.append(entry)

    metric_spec = next((s for s in template.sections if s.key == "metrics"), None)
    request = {
        "sections": sections_payload,
        "sidebar_fields": [
            {"key": f.key, "label": f.label, "guidance": f.guidance}
            for f in template.masthead.sidebar_fields
        ],
        "metric_callout_slots": template.metric_callout_count,
        "metric_guidance": metric_spec.guidance if metric_spec else None,
        "metric_candidate_fields": metric_spec.source_fields if metric_spec else [],
        "total_word_budget": template.total_body_words,
    }

    try:
        result = provider.generate_summary(_investor_payload(data), request)
    except Exception as exc:
        log.warning("AI summary generation failed (%s); using deterministic composition", exc)
        return None
    if not result:
        return None

    name = data.resolved_company_name(config.company_name_override)
    summary = ExecutiveSummary(company_name=name)
    rejected = 0

    def _budget_for(field: str, default: int) -> int:
        return next(
            (s.approx_words for s in template.sections if s.key == field), default
        )

    # -- Prose sections -----------------------------------------------------
    for field in PROSE_SECTIONS:
        raw = result.get(field)
        if not isinstance(raw, str) or not raw.strip():
            continue
        text = strip_markdown_emphasis(re.sub(r"\s+", " ", raw)) or ""

        check = ledger.verify_text(text)
        if not check.grounded:
            log.warning(
                "Rejected ungrounded %s (numbers not in deck: %s)",
                field, ", ".join(check.ungrounded_numbers),
            )
            rejected += 1
            continue

        setattr(summary, field, _trim_to_words(
            text, _budget_for(field, 110 if field == "corporate_summary" else 75)
        ))
        summary.source_slides[field] = check.matched_slides

    # -- List sections ------------------------------------------------------
    for field in LIST_SECTIONS:
        raw = result.get(field)
        if isinstance(raw, str) and raw.strip():
            raw = [raw]
        if not isinstance(raw, list):
            continue

        # Hold the section to its budget by keeping whole bullets and dropping
        # trailing ones, rather than truncating every bullet to an equal share.
        # An investor would rather read three complete points than five clipped
        # ones, and a mid-sentence cut reads as a defect.
        # The budget guides; the layout ladder enforces the page. Allowing a
        # modest overshoot keeps two or three complete bullets where a hard stop
        # would leave one, and the ladder trims later if the page really is full.
        section_budget = int(_budget_for(field, 90) * 1.35)
        used = 0

        kept: list[str] = []
        matched: list[int] = []
        for entry in raw:
            if not isinstance(entry, str) or not entry.strip():
                continue
            text = strip_list_marker(
                strip_markdown_emphasis(re.sub(r"\s+", " ", entry))
            ) or ""
            if not text:
                continue
            check = ledger.verify_text(text)
            if not check.grounded:
                log.warning(
                    "Rejected ungrounded %s item (numbers not in deck: %s)",
                    field, ", ".join(check.ungrounded_numbers),
                )
                rejected += 1
                continue
            # A single very long bullet is still capped, so one runaway item
            # cannot swallow the section on its own.
            item_text = _trim_to_words(text, 48) or text
            item_words = len(item_text.split())
            if kept and used + item_words > section_budget:
                break
            kept.append(item_text)
            used += item_words
            matched.extend(check.matched_slides)
        if kept:
            setattr(summary, field, kept[:6])
            summary.source_slides[field] = sorted(set(matched))

    # -- Sidebar ------------------------------------------------------------
    sidebar_raw = result.get("sidebar")
    if isinstance(sidebar_raw, dict):
        label_for = {f.key: f.label for f in template.masthead.sidebar_fields}
        for key, value in sidebar_raw.items():
            if not isinstance(value, str) or not value.strip():
                continue
            text = re.sub(r"\s+", " ", value).strip()
            if not ledger.verify_text(text).grounded:
                log.warning("Rejected ungrounded sidebar value for '%s'", key)
                rejected += 1
                continue
            summary.sidebar.append(
                SidebarEntry(
                    key=key,
                    label=label_for.get(key, key.replace("_", " ").title()),
                    value=_trim_to_words(text, 18) or text,
                )
            )

    contact_raw = result.get("contact")
    if isinstance(contact_raw, dict):
        summary.contact = ContactBlock(
            name=(contact_raw.get("name") or None),
            role=(contact_raw.get("role") or None),
            email=(contact_raw.get("email") or None),
            phone=(contact_raw.get("phone") or None),
        )

    # -- Financials table ---------------------------------------------------
    table_raw = result.get("projected_financials")
    if isinstance(table_raw, dict):
        columns = [str(c).strip() for c in (table_raw.get("columns") or [])]
        rows_raw = table_raw.get("rows") or []
        rows: list[list[str]] = []
        for row in rows_raw:
            if not isinstance(row, list) or not row:
                continue
            # A year with no stated figure is an en dash, never the string "None":
            # the absence is real and should read as such.
            cells = [
                "–" if str(c).strip().lower() in ("none", "null", "n/a", "na", "")
                else str(c).strip()
                for c in row
            ]
            if ledger.verify_text(" ".join(cells[1:])).grounded:
                rows.append(cells)
            else:
                rejected += 1

        # Column 0 is the row-label column and carries no heading. A model that
        # omits it and starts at the first year would shift every value one
        # column left, dropping the final year off the table.
        if columns and rows:
            widest = max(len(r) for r in rows)
            if len(columns) < widest:
                columns = [""] + columns
            columns = [""] + columns[1:]     # column 0 never carries a heading
            columns = columns[:widest]

        caption = table_raw.get("caption")
        if columns and rows:
            summary.projected_financials = FinancialTable(
                columns=columns[:6],
                rows=[r[:6] for r in rows[:6]],
                caption=(
                    re.sub(r"\s+", " ", caption).strip()
                    if isinstance(caption, str) and caption.strip()
                    and ledger.verify_text(caption).grounded else None
                ),
            )

    tagline = result.get("tagline")
    if isinstance(tagline, str) and tagline.strip():
        cleaned = re.sub(r"\s+", " ", tagline).strip()
        if ledger.verify_text(cleaned).grounded:
            summary.tagline = _trim_to_words(cleaned, 16)

    # Metric callouts from the model, validated against the ledger.
    for item in result.get("metrics") or []:
        if not isinstance(item, dict):
            continue
        label = re.sub(r"\s+", " ", str(item.get("label") or "")).strip()
        value = re.sub(r"\s+", " ", str(item.get("value") or "")).strip()
        if not label or not _metric_value_ok_for_label(label, value):
            continue
        check = ledger.verify_text(value)
        if not check.grounded:
            log.warning("Rejected ungrounded metric callout: %s = %s", label, value)
            rejected += 1
            continue
        slides = [int(s) for s in (item.get("source_slides") or []) if str(s).isdigit()]
        summary.metrics.append(
            MetricCallout(
                label=label[:_MAX_LABEL_CHARS],
                value=value,
                source_slides=slides or check.matched_slides[:2],
            )
        )

    missing = result.get("not_stated") or result.get("missing_material_information")
    if isinstance(missing, list):
        summary.not_stated = [str(m).strip() for m in missing if str(m).strip()][:6]

    if rejected:
        log.warning("Evidence gate rejected %d AI-generated element(s)", rejected)
    return summary


def merge_summaries(base: ExecutiveSummary, overlay: ExecutiveSummary | None) -> ExecutiveSummary:
    """Overlay the AI draft onto the deterministic base, section by section.

    A section the evidence gate rejected simply keeps its deterministic version,
    so one bad paragraph never costs the whole page its AI-written prose.
    """
    if overlay is None:
        return base

    merged = base.model_copy(deep=True)
    replaced: list[str] = []
    for field in _TEXT_SECTIONS:
        if not overlay.has_content(field):
            continue
        setattr(merged, field, getattr(overlay, field))
        replaced.append(field)

    if overlay.tagline:
        merged.tagline = overlay.tagline
    if overlay.metrics:
        merged.metrics = overlay.metrics
    if overlay.sidebar:
        merged.sidebar = overlay.sidebar
    if not overlay.contact.is_empty():
        merged.contact = overlay.contact
    if overlay.not_stated:
        merged.not_stated = overlay.not_stated
    if overlay.source_slides:
        merged.source_slides.update(overlay.source_slides)

    kept = [f for f in _TEXT_SECTIONS if merged.has_content(f) and f not in replaced]
    log.info(
        "Composed %d section(s) with AI%s",
        len(replaced),
        f"; {len(kept)} retained from deterministic composition ({', '.join(kept)})"
        if kept else "",
    )
    return merged


# ---------------------------------------------------------------------------
# Quality control (Step 18)
# ---------------------------------------------------------------------------


class QAResult(BaseModel):
    passed: bool = True
    issues: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    revisions_applied: list[str] = Field(default_factory=list)

    @property
    def critical_issues(self) -> list[dict[str, Any]]:
        return [i for i in self.issues if str(i.get("severity", "")).lower() == "critical"]


_TEXT_SECTIONS = ALL_CONTENT_SECTIONS

# Which investor-schema sections back each written section. Used to detect a
# section that claims the deck says nothing when the deck in fact does.
_SECTION_BACKING: dict[str, tuple[str, ...]] = {
    "corporate_summary": ("company", "solution"),
    "key_facts": ("problem", "customers"),
    "innovative_solution": ("solution", "technology"),
    "products_platform": ("product", "business_model", "intellectual_property"),
    "target_markets": ("market",),
    "competitive_advantages": ("competitive_advantage", "intellectual_property"),
    "commercial_validation": ("traction", "customers"),
    "projected_financials": ("financial_metrics",),
    "financing_opportunity": ("fundraising",),
    "use_of_funds": ("use_of_funds", "milestones"),
    "management_team": ("team",),
    "investment_thesis": ("company", "competitive_advantage"),
}

_ABSENCE_RE = re.compile(
    r"\b(?:does not (?:provide|disclose|include|specify|state|name)"
    r"|do not (?:provide|disclose|include|specify)"
    r"|is not (?:provided|disclosed|specified|stated)"
    r"|are not (?:provided|disclosed|specified|stated)"
    r"|no information(?: is)?(?: provided)?"
    r"|not provided in the deck"
    r"|the deck is silent)\b",
    re.IGNORECASE,
)


def _distinctive_tokens(section: object) -> set[str]:
    """Words from a schema section that should surface in prose written about it."""
    tokens: set[str] = set()

    def _harvest(text: str | None) -> None:
        for word in re.findall(r"[A-Za-z][\w.&/-]{3,}|\$[\d.,]+[KMB]?|\d[\d.,]*%?",
                              str(text or "")):
            token = word.strip(".,;:").lower()
            if len(token) >= 3 and token not in _COMMON_WORDS:
                tokens.add(token)

    for _, value in section:  # type: ignore[misc]
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, Fact) and item.present:
                _harvest(item.value)
            elif hasattr(item, "name"):
                _harvest(getattr(item, "name", None))
                _harvest(getattr(item, "credentials", None))
    return tokens


_COMMON_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "are",
    "was", "were", "not", "provided", "company", "deck", "their", "its", "per",
    "into", "over", "under", "than", "more", "less", "also", "each", "which",
    "used", "using", "based", "across", "including", "within", "years", "year",
}


def detect_invented_gaps(summary: ExecutiveSummary, data: InvestorData) -> list[str]:
    """Find sections that claim absence while the schema holds real data.

    This is the "invented gap" failure: asserting the deck says nothing about
    the team when the team slide was extracted successfully. The evidence ledger
    cannot catch it, because a false claim of absence contains no numbers.
    """
    invented: list[str] = []
    for field, backing_names in _SECTION_BACKING.items():
        text = summary.section_as_text(field)
        if not text.strip() or not _ABSENCE_RE.search(text):
            continue

        available: set[str] = set()
        for name in backing_names:
            section = getattr(data, name, None)
            if section is not None:
                available |= _distinctive_tokens(section)
        if not available:
            continue  # the absence claim is accurate

        lowered = text.lower()
        # If none of the extracted specifics made it into the prose, the section
        # is asserting absence rather than reporting what was found.
        if not any(token in lowered for token in available):
            invented.append(field)
    return invented


def run_quality_control(
    summary: ExecutiveSummary,
    data: InvestorData,
    ledger: EvidenceLedger,
    provider: Any,
    config: PipelineConfig,
    fallback: ExecutiveSummary | None = None,
) -> QAResult:
    """Deterministic checks first, then the optional AI reviewer.

    *fallback* is the deterministic composition, used to restore any section the
    checks reject, so a bad paragraph is replaced rather than simply deleted.
    """
    result = QAResult()

    # 0. Invented gaps: a section claiming the deck is silent when it is not.
    for field in detect_invented_gaps(summary, data):
        restored = getattr(fallback, field, None) if fallback else None
        if restored:
            setattr(summary, field, restored)
            result.revisions_applied.append(
                f"{field}: replaced a false 'not provided' claim with the extracted facts"
            )
            log.warning(
                "Section '%s' claimed the deck provides nothing, but facts were "
                "extracted for it; restored the deterministic version.", field,
            )
        else:
            setattr(summary, field, None)
            result.issues.append({
                "section": field,
                "severity": "warning",
                "type": "invented_gap",
                "detail": (
                    "The section asserted that the deck provides no information, "
                    "but data was extracted for it. The section was removed."
                ),
                "quote": str(getattr(summary, field, ""))[:160],
            })

    # 1. Marketing language and inline citations, applied to prose and to each
    #    item of a list section alike.
    def _clean_one(text: str | None, field: str) -> tuple[str | None, list[int], list[str]]:
        text = strip_markdown_emphasis(text)
        text, cited = strip_slide_citations(text)
        cleaned, removed = strip_marketing_language(text)
        return cleaned, cited, removed

    for field in _TEXT_SECTIONS:
        value = getattr(summary, field, None)
        all_cited: list[int] = []
        all_removed: list[str] = []

        if isinstance(value, str):
            cleaned, cited, removed = _clean_one(value, field)
            setattr(summary, field, cleaned)
            all_cited, all_removed = cited, removed
        elif isinstance(value, list):
            items: list[str] = []
            for item in value:
                cleaned, cited, removed = _clean_one(str(item), field)
                all_cited.extend(cited)
                all_removed.extend(removed)
                if cleaned:
                    items.append(cleaned)
            setattr(summary, field, items)
        elif isinstance(value, FinancialTable) and not value.is_empty():
            cleaned, cited, removed = _clean_one(value.caption, field)
            value.caption = cleaned
            all_cited, all_removed = cited, removed

        if all_cited:
            summary.source_slides[field] = sorted(
                set(summary.source_slides.get(field, []) + all_cited)
            )
        if all_removed:
            result.revisions_applied.append(
                f"{field}: removed promotional language "
                f"({', '.join(sorted(set(all_removed)))})"
            )
    if summary.tagline:
        cleaned, removed = strip_marketing_language(summary.tagline)
        if removed:
            summary.tagline = cleaned
            result.revisions_applied.append("tagline: removed promotional language")

    # 2. Factual grounding â€” the hard gate.
    for field in _TEXT_SECTIONS:
        text = summary.section_as_text(field)
        if not text.strip():
            continue
        check = ledger.verify_text(text)
        if not check.grounded:
            result.issues.append({
                "section": field,
                "severity": "critical",
                "type": "fabricated_facts",
                "detail": f"Numbers not present in the deck: {', '.join(check.ungrounded_numbers)}",
                "quote": text[:160],
            })
        else:
            summary.source_slides.setdefault(field, check.matched_slides)

    # Sidebar values carry figures too, and must clear the same gate.
    kept_sidebar: list[SidebarEntry] = []
    for entry in summary.sidebar:
        if ledger.verify_text(entry.value).grounded:
            kept_sidebar.append(entry)
        else:
            log.warning("Dropped ungrounded sidebar value: %s = %s", entry.label, entry.value)
            result.revisions_applied.append(f"sidebar: dropped ungrounded '{entry.label}'")
    summary.sidebar = kept_sidebar

    for metric in summary.metrics:
        if not ledger.verify_text(metric.value).grounded:
            result.issues.append({
                "section": "metrics",
                "severity": "critical",
                "type": "fabricated_facts",
                "detail": f"Metric callout '{metric.label}: {metric.value}' is not in the deck.",
                "quote": f"{metric.label}: {metric.value}",
            })

    # 3. Numeric consistency â€” the same label must not carry two values.
    by_label: dict[str, set[str]] = {}
    for metric in summary.metrics:
        by_label.setdefault(metric.label.lower(), set()).add(metric.value)
    for label, values in by_label.items():
        if len(values) > 1:
            result.issues.append({
                "section": "metrics",
                "severity": "warning",
                "type": "numeric_inconsistency",
                "detail": f"'{label}' appears with conflicting values: {', '.join(sorted(values))}",
                "quote": label,
            })

    # A raise larger than the stated TAM signals a misread, not a real deal.
    tam = parse_money(data.market.tam.value) if data.market.tam.present else None
    raise_amt = (
        parse_money(data.fundraising.raise_amount.value)
        if data.fundraising.raise_amount.present else None
    )
    if tam and raise_amt and raise_amt > tam:
        result.issues.append({
            "section": "financing",
            "severity": "warning",
            "type": "numeric_inconsistency",
            "detail": "Stated raise exceeds stated TAM; one of the two figures is likely misread.",
            "quote": f"raise {data.fundraising.raise_amount.value} vs TAM {data.market.tam.value}",
        })

    # 4. Duplication across sections.
    seen_sentences: dict[str, str] = {}
    for field in _TEXT_SECTIONS:
        text = summary.section_as_text(field)
        if not text.strip():
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            key = re.sub(r"[^a-z0-9 ]", "", sentence.lower()).strip()
            if len(key) < 30:
                continue
            if key in seen_sentences and seen_sentences[key] != field:
                result.warnings.append(
                    f"Duplicated content between {seen_sentences[key]} and {field}."
                )
            else:
                seen_sentences[key] = field

    # 5. Missing context â€” projections must be labelled as such.
    projection_values = {
        f.value for f in data.financial_metrics.projections if f.present and f.value
    }
    for field in ("corporate_summary", "commercial_validation"):
        text = summary.section_as_text(field)
        for value in projection_values:
            token = re.search(r"\$\s?\d[\d,.]*\s?[KMB]?", value or "")
            if not token:
                continue
            if token.group(0) in text and not re.search(
                r"project|forecast|plan|expect|plan to|by 20\d\d|target", text, re.IGNORECASE
            ):
                result.issues.append({
                    "section": field,
                    "severity": "warning",
                    "type": "missing_context",
                    "detail": (
                        f"{token.group(0)} appears in the deck as a projection but is not "
                        "labelled as forward-looking here."
                    ),
                    "quote": token.group(0),
                })
                break

    # 5a. Terminal punctuation, applied last so budget trimming cannot undo it.
    for field in _TEXT_SECTIONS:
        value = getattr(summary, field, None)
        if isinstance(value, str):
            tidied = tidy_terminal_punctuation(value)
            if tidied != value:
                setattr(summary, field, tidied)
        elif isinstance(value, list):
            setattr(
                summary, field,
                [t for t in (tidy_terminal_punctuation(str(i)) for i in value) if t],
            )

    # 5b. Forward-looking callouts must say so.
    #
    # A CAGR computed from the deck's own projections is a legitimate derived
    # metric, but presenting it beside achieved figures without a "Proj." prefix
    # would misrepresent it as realised performance.
    projected_values = {
        f.value for f in data.growth_metrics.derived
        if f.present and f.value and "projected" in (f.original_text or "").lower()
    }
    for metric in summary.metrics:
        if metric.value in projected_values and "proj" not in metric.label.lower():
            metric.label = _prefix_label(metric.label, "Proj.")
            result.revisions_applied.append(
                f"metrics: labelled '{metric.value}' as projected"
            )

    # 6. Investor relevance.
    if not summary.metrics:
        result.warnings.append("No metric callouts could be supported by the deck.")
    if not summary.financing_opportunity and data.fundraising.raise_amount.present:
        result.warnings.append("Deck states a raise but the financing section is empty.")

    # 7. AI reviewer.
    if config.ai_available and getattr(provider, "available", False):
        try:
            review = provider.review_summary(
                summary.model_dump(mode="json"),
                {
                    "records": [
                        {
                            "claim": r.claim, "value": r.value, "slides": r.source_slides,
                            "classification": r.classification.value,
                        }
                        for r in ledger.records
                    ],
                    "ledger_summary": ledger.summary(),
                },
            )
            for issue in review.get("issues") or []:
                if not isinstance(issue, dict):
                    continue
                issue = dict(issue)
                issue["source"] = "ai_reviewer"
                section = str(issue.get("section") or "")
                severity = str(issue.get("severity", "")).lower()

                # The ledger is the authority on whether a claim is grounded.
                # An AI reviewer's "critical" verdict is only allowed to block
                # rendering when the deterministic check agrees; otherwise it is
                # recorded as a warning so a good section is not silently deleted.
                if severity == "critical":
                    text = (
                        summary.section_as_text(section)
                        if section in _TEXT_SECTIONS else ""
                    )
                    ledger_agrees = bool(text.strip()) and not ledger.verify_text(text).grounded
                    if not ledger_agrees:
                        issue["severity"] = "warning"
                        issue["downgraded"] = (
                            "AI reviewer flagged this, but the evidence ledger confirms "
                            "every figure traces to the deck."
                        )
                result.issues.append(issue)
            for warning in review.get("warnings") or []:
                result.warnings.append(str(warning))

            # Apply revisions only if they too pass the evidence gate.
            for field, revised in (review.get("revisions") or {}).items():
                # Only prose sections are revised in place; a list or table would
                # need restructuring, which the reviewer is not asked to do.
                if field not in PROSE_SECTIONS or not isinstance(revised, str):
                    continue
                text = re.sub(r"\s+", " ", revised).strip()
                if not text:
                    continue
                if not ledger.verify_text(text).grounded:
                    log.warning("Discarded QA revision for %s: it introduced ungrounded numbers", field)
                    continue
                # A revision can reintroduce citations or banned words, so the
                # hygiene filters are applied again rather than only up front.
                text, cited = strip_slide_citations(text)
                if cited:
                    summary.source_slides[field] = sorted(
                        set(summary.source_slides.get(field, []) + cited)
                    )
                text, _ = strip_marketing_language(text)
                if not text:
                    continue
                budget = 110 if field == "corporate_summary" else 80
                setattr(summary, field, _trim_to_words(text, budget))
                result.revisions_applied.append(f"{field}: QA revision applied")
                # Re-check: a revision can clear a previously raised critical issue.
                result.issues = [
                    i for i in result.issues
                    if not (i.get("section") == field and i.get("type") == "fabricated_facts")
                ]
        except Exception as exc:
            log.warning("AI QA review failed: %s", exc)
            result.warnings.append("AI QA review unavailable.")

    result.passed = not result.critical_issues
    log.info(
        "QA: %s â€” %d issue(s), %d warning(s), %d revision(s)",
        "passed" if result.passed else "FAILED",
        len(result.issues), len(result.warnings), len(result.revisions_applied),
    )
    return result


def drop_failing_sections(summary: ExecutiveSummary, qa: QAResult) -> list[str]:
    """Remove sections with unresolved critical issues so rendering can proceed."""
    dropped: list[str] = []
    for issue in qa.critical_issues:
        section = str(issue.get("section") or "")
        if section in ("metrics", "corporate_summary"):
            continue  # the lead paragraph is restored, never removed
        if section in _TEXT_SECTIONS and summary.has_content(section):
            current = getattr(summary, section)
            if isinstance(current, list):
                setattr(summary, section, [])
            elif isinstance(current, FinancialTable):
                setattr(summary, section, FinancialTable())
            else:
                setattr(summary, section, None)
            dropped.append(section)
    if dropped:
        log.warning("Dropped section(s) failing factual QA: %s", ", ".join(sorted(set(dropped))))
    return dropped

