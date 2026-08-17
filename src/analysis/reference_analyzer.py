"""Infer a design template from a reference executive summary.

The reference is treated strictly as a *design* source. Its geometry, typography,
colour palette, section ordering, and density are measured with PyMuPDF and
turned into a :class:`TemplateSpec` that the renderer reconstructs natively.
None of its factual content is carried forward.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf
from pydantic import BaseModel, Field, ValidationError

from ..config import MAX_REFERENCE_PAGES
from ..utils.logging import get_logger

log = get_logger("reference_analyzer")


# ---------------------------------------------------------------------------
# Template model
# ---------------------------------------------------------------------------


class PageSpec(BaseModel):
    size: str = "letter"
    width_pt: float = 612.0
    height_pt: float = 792.0
    orientation: str = "portrait"
    margins: dict[str, float] = Field(
        default_factory=lambda: {"top": 46.0, "bottom": 42.0, "left": 50.0, "right": 50.0}
    )


class TypographySpec(BaseModel):
    body_font: str = "Helvetica"
    heading_font: str = "Helvetica-Bold"
    title_size: float = 22.0
    tagline_size: float = 10.5
    heading_size: float = 8.2
    body_size: float = 9.2
    metric_value_size: float = 14.0
    metric_label_size: float = 6.6
    footer_size: float = 6.8
    heading_case: str = "upper"          # upper | title | as-is
    heading_tracking: float = 0.9        # extra letter spacing in points
    line_height: float = 1.28

    # Masthead and detail styles, used by document structures that carry a
    # sidebar, numbered facts, a data table, or captions.
    masthead_name_size: float = 17.0
    sidebar_label_size: float = 8.0
    sidebar_value_size: float = 8.0
    bullet_size: float = 8.5
    caption_size: float = 7.5
    table_size: float = 7.5
    note_size: float = 7.0
    title_bar_size: float = 15.0
    title_bar_label_size: float = 9.0


class SidebarField(BaseModel):
    """One labelled row in the masthead sidebar."""

    key: str
    label: str
    source_field: str | None = None
    guidance: str | None = None
    group: int = 0            # blank line inserted between groups
    optional: bool = True


class MastheadSpec(BaseModel):
    """The header block: a labelled sidebar beside an opening narrative column."""

    enabled: bool = False
    sidebar_width_ratio: float = 0.34
    sidebar_bg: str | None = None
    sidebar_fields: list[SidebarField] = Field(default_factory=list)
    show_contact: bool = True
    contact_heading: str = "Contact"
    show_not_stated: bool = True
    not_stated_label: str = "Not stated in source deck:"
    divider: bool = True


class TitleBarSpec(BaseModel):
    """The single line above the masthead: company name plus a document label."""

    enabled: bool = False
    document_label: str = "Company Overview & Executive Summary"
    company_name_case: str = "upper"


class TableSpec(BaseModel):
    """A compact financial table rendered inside a section."""

    header_bg: str = "#1F4E79"
    header_text: str = "#FFFFFF"
    row_alt_bg: str | None = "#F4F7FA"
    grid: str = "#D6DEE6"
    row_labels_bold: bool = True
    max_columns: int = 6
    max_rows: int = 6


class ColorSpec(BaseModel):
    text: str = "#1A1A1A"
    heading: str = "#0F2E4C"
    accent: str = "#0F2E4C"
    muted: str = "#5A6672"
    rule: str = "#C8D0D8"
    metric_band_bg: str = "#F2F5F8"
    metric_value: str = "#0F2E4C"

    # Secondary accent used for bullet glyphs, contact details, and links.
    accent_secondary: str = "#2E75B6"
    sidebar_value: str = "#333333"


class SpacingSpec(BaseModel):
    section_gap: float = 7.5
    heading_gap: float = 2.6
    paragraph_gap: float = 3.2
    bullet_indent: float = 9.0
    column_gap: float = 18.0


class SectionSpec(BaseModel):
    key: str
    heading: str
    column: int = 0          # 0 = left/full, 1 = right
    order: int = 0
    approx_words: int = 55
    style: str = "paragraph"  # paragraph | bullets | metrics | header | footer
    optional: bool = False

    # The content contract. These travel with the template so it defines not only
    # how the page looks but which investor questions each section must answer,
    # and which schema fields supply the evidence.
    guidance: str | None = Field(
        default=None,
        description="What this section must answer, in investor terms.",
    )
    source_fields: list[str] = Field(
        default_factory=list,
        description="Investor-schema paths that supply this section's evidence.",
    )


class FooterSpec(BaseModel):
    enabled: bool = True
    text: str = "Confidential — prepared for accredited investors."
    show_rule: bool = True
    show_generated_date: bool = True


class HeaderSpec(BaseModel):
    eyebrow: str | None = None
    company_name_case: str = "as-is"   # as-is | upper
    show_tagline: bool = True
    show_rule: bool = True
    show_meta_line: bool = True
    alignment: str = "left"


class TemplateSpec(BaseModel):
    """The complete inferred design of the reference document."""

    page: PageSpec = Field(default_factory=PageSpec)
    header: HeaderSpec = Field(default_factory=HeaderSpec)
    title_bar: TitleBarSpec = Field(default_factory=TitleBarSpec)
    masthead: MastheadSpec = Field(default_factory=MastheadSpec)
    sections: list[SectionSpec] = Field(default_factory=list)
    typography: TypographySpec = Field(default_factory=TypographySpec)
    colors: ColorSpec = Field(default_factory=ColorSpec)
    spacing: SpacingSpec = Field(default_factory=SpacingSpec)
    table: TableSpec = Field(default_factory=TableSpec)
    footer: FooterSpec = Field(default_factory=FooterSpec)
    columns: int = 2
    metric_callout_count: int = 4
    metrics_style: str = "band"        # band | boxes | inline
    total_body_words: int = 430
    source: str = "default"            # default | inferred | inferred+ai | canonical
    notes: list[str] = Field(default_factory=list)

    def section_keys(self) -> list[str]:
        return [s.key for s in sorted(self.sections, key=lambda s: s.order)]

    def masthead_section_keys(self) -> list[str]:
        return [s.key for s in sorted(self.sections, key=lambda s: s.order) if s.column == 2]

    def body_sections(self) -> list["SectionSpec"]:
        """Sections drawn below the masthead, in order."""
        return [s for s in sorted(self.sections, key=lambda s: s.order) if s.column != 2]


# ---------------------------------------------------------------------------
# Fallback template (Step 8 structure)
# ---------------------------------------------------------------------------

FALLBACK_SECTIONS = [
    SectionSpec(key="investment_opportunity", heading="INVESTMENT OPPORTUNITY",
                column=0, order=0, approx_words=100, style="paragraph"),
    SectionSpec(key="metrics", heading="DEAL BY THE NUMBERS",
                column=0, order=1, approx_words=0, style="metrics"),
    SectionSpec(key="market_business_model", heading="MARKET & BUSINESS MODEL",
                column=0, order=2, approx_words=70, style="paragraph"),
    SectionSpec(key="traction", heading="TRACTION & VALIDATION",
                column=1, order=3, approx_words=70, style="paragraph"),
    SectionSpec(key="competitive_position", heading="COMPETITIVE POSITION",
                column=0, order=4, approx_words=60, style="paragraph"),
    SectionSpec(key="team", heading="TEAM",
                column=1, order=5, approx_words=55, style="paragraph"),
    SectionSpec(key="financing", heading="FINANCING & USE OF FUNDS",
                column=1, order=6, approx_words=70, style="paragraph"),
    SectionSpec(key="missing_material_information", heading="INFORMATION NOT PROVIDED",
                column=1, order=7, approx_words=35, style="bullets", optional=True),
]


CANONICAL_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "templates" / "executive_summary_template.json"
)


def load_template(path: str | Path) -> TemplateSpec:
    """Load a :class:`TemplateSpec` from JSON.

    Keys prefixed with ``_`` are treated as file comments and ignored, so the
    canonical template can document itself inline.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = {k: v for k, v in raw.items() if not k.startswith("_")}
    return TemplateSpec.model_validate(raw)


def default_template() -> TemplateSpec:
    """The canonical document structure used when no reference is supplied.

    Loaded from ``templates/executive_summary_template.json`` so the structure is
    editable without touching code. Falls back to the in-code section list only if
    that file is missing or unreadable.
    """
    try:
        spec = load_template(CANONICAL_TEMPLATE_PATH)
        if not spec.source or spec.source == "inferred":
            spec.source = "canonical"
        return spec
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        log.warning(
            "Canonical template at %s could not be loaded (%s); using the built-in structure.",
            CANONICAL_TEMPLATE_PATH.name, exc,
        )
        return TemplateSpec(
            sections=[s.model_copy() for s in FALLBACK_SECTIONS], source="default"
        )


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

# Ordered most specific first: "financing opportunity" must not be captured by the
# looser "opportunity" pattern belonging to investment_opportunity.
_SECTION_SYNONYMS: list[tuple[str, tuple[str, ...]]] = [
    ("corporate_summary",
     ("corporate summary", "company summary", "company overview", "business overview",
      "at a glance", "overview")),
    ("key_facts",
     ("key facts", "key points", "fast facts", "the facts", "key highlights",
      "highlights")),
    ("innovative_solution",
     ("innovative solution", "the solution", "our solution", "why now",
      "solution & why now", "technology & solution")),
    ("products_platform",
     ("products & platform", "products and platform", "product & platform",
      "product platform", "products", "platform", "product & technology")),
    ("target_markets",
     ("target markets", "target market", "markets", "market segments",
      "market opportunity", "addressable market")),
    ("competitive_advantages",
     ("competitive advantages", "competitive advantage", "advantages",
      "differentiation", "why we win", "moat", "competition", "competitive landscape")),
    ("commercial_validation",
     ("commercial validation", "validation", "traction & validation", "traction",
      "proof points", "evidence", "customer validation")),
    ("projected_financials",
     ("projected financials", "financial projections", "financials", "projections",
      "financial summary", "p&l")),
    ("financing_opportunity",
     ("financing opportunity", "the ask", "financing", "the raise", "raise",
      "deal terms", "terms", "investment terms", "offering")),
    ("use_of_funds",
     ("use of funds", "use of proceeds", "capital deployment", "allocation")),
    ("management_team",
     ("key management team", "management team", "leadership team", "team",
      "leadership", "management", "founders")),
    ("investment_thesis",
     ("investment thesis", "thesis", "why invest", "the opportunity",
      "investment rationale", "core investment belief")),
    ("investment_opportunity",
     ("investment opportunity", "executive summary", "description")),
    ("metrics",
     ("deal by the numbers", "by the numbers", "key metrics", "metrics",
      "key figures", "snapshot")),
    ("problem", ("problem", "the problem", "challenge", "pain point")),
    ("business_model",
     ("business model", "revenue model", "monetization", "monetisation", "pricing")),
    ("risks", ("risks", "key risks", "risk factors", "risks & mitigations")),
    ("missing_material_information",
     ("information not provided", "not provided", "missing information", "open questions",
      "diligence items", "gaps")),
]

# Legacy keys from earlier template revisions, folded onto the current set so a
# reference written against the old structure still resolves to a renderable
# section instead of being silently dropped.
SECTION_KEY_ALIASES: dict[str, str] = {
    "investment_opportunity": "corporate_summary",
    "solution": "innovative_solution",
    "product": "products_platform",
    "market_business_model": "target_markets",
    "market": "target_markets",
    "competitive_position": "competitive_advantages",
    "traction": "commercial_validation",
    "team": "management_team",
    "financing": "financing_opportunity",
    "problem": "key_facts",
    "business_model": "products_platform",
}

# Every key the renderer knows how to draw.
RENDERABLE_SECTION_KEYS = (
    "corporate_summary",
    "key_facts",
    "innovative_solution",
    "products_platform",
    "target_markets",
    "competitive_advantages",
    "commercial_validation",
    "projected_financials",
    "financing_opportunity",
    "use_of_funds",
    "management_team",
    "investment_thesis",
    "risks",
    "missing_material_information",
    "metrics",
)


def normalise_section_key(key: str | None) -> str | None:
    """Fold a legacy or inferred key onto the current renderable set."""
    if not key:
        return None
    return SECTION_KEY_ALIASES.get(key, key)


def _match_section_key(heading: str) -> str | None:
    h = re.sub(r"[^a-z& ]", " ", heading.lower())
    h = re.sub(r"\s+", " ", h).strip()
    if not h:
        return None
    # Exact matches first, so a heading that *is* a synonym never loses to a
    # longer entry that merely contains it.
    for key, synonyms in _SECTION_SYNONYMS:
        if h in synonyms:
            return normalise_section_key(key)
    for key, synonyms in _SECTION_SYNONYMS:
        for syn in synonyms:
            if h.startswith(syn) or syn in h:
                return normalise_section_key(key)
    return None


def _srgb(value: int) -> str:
    return f"#{value & 0xFFFFFF:06X}"


def _luminance(hex_color: str) -> float:
    v = int(hex_color.lstrip("#"), 16)
    r, g, b = (v >> 16) & 255, (v >> 8) & 255, v & 255
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _collect_spans(page: pymupdf.Page) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                if not text:
                    continue
                spans.append(
                    {
                        "text": text,
                        "size": round(float(span.get("size", 0)), 2),
                        "font": span.get("font", "") or "",
                        "color": _srgb(int(span.get("color", 0))),
                        "bbox": tuple(span.get("bbox", (0, 0, 0, 0))),
                        "bold": "bold" in (span.get("font") or "").lower()
                        or bool(int(span.get("flags", 0)) & 2 ** 4),
                    }
                )
    return spans


def _infer_margins(spans: list[dict], page: pymupdf.Page) -> dict[str, float]:
    if not spans:
        return PageSpec().margins
    left = min(s["bbox"][0] for s in spans)
    right = max(s["bbox"][2] for s in spans)
    top = min(s["bbox"][1] for s in spans)
    bottom = max(s["bbox"][3] for s in spans)
    # Floors keep an unusual reference (or a stray span) from producing margins
    # so tight that the generated page reads as clipped.
    return {
        "left": round(max(32.0, min(left, 108.0)), 1),
        "right": round(max(32.0, min(page.rect.width - right, 108.0)), 1),
        "top": round(max(30.0, min(top, 108.0)), 1),
        "bottom": round(max(34.0, min(page.rect.height - bottom, 108.0)), 1),
    }


def _infer_columns(spans: list[dict], page: pymupdf.Page, margins: dict[str, float]) -> int:
    """Two columns if a vertical band near the page centre stays empty."""
    content_left = margins["left"]
    content_right = page.rect.width - margins["right"]
    mid = (content_left + content_right) / 2.0
    band = (content_right - content_left) * 0.04

    crossing = sum(
        1 for s in spans if s["bbox"][0] < mid - band and s["bbox"][2] > mid + band
    )
    right_side = sum(1 for s in spans if s["bbox"][0] > mid)
    if not spans:
        return 1
    # Plenty of right-hand content and few spans straddling the gutter => 2 columns.
    if right_side >= max(4, len(spans) * 0.18) and crossing < len(spans) * 0.25:
        return 2
    return 1


def _base_font(fonts: Counter) -> tuple[str, str]:
    """Map the dominant embedded font to a ReportLab core family."""
    name = (fonts.most_common(1)[0][0] if fonts else "").lower()
    serif_markers = ("times", "georgia", "garamond", "serif", "minion", "cambria", "book")
    if any(m in name for m in serif_markers) and "sans" not in name:
        return "Times-Roman", "Times-Bold"
    return "Helvetica", "Helvetica-Bold"


def analyze_reference(path: str | Path, ai_provider: Any | None = None) -> TemplateSpec:
    """Measure the reference PDF and return a reconstructable design spec."""
    p = Path(path)
    try:
        doc = pymupdf.open(str(p))
    except Exception as exc:
        log.warning("Could not open reference '%s' (%s); using default template.", p.name, exc)
        return default_template()

    try:
        if doc.page_count == 0:
            log.warning("Reference '%s' has no pages; using default template.", p.name)
            return default_template()
        if doc.page_count > MAX_REFERENCE_PAGES:
            log.warning(
                "Reference has %d pages; only the first will be used as the design source.",
                doc.page_count,
            )

        page = doc[0]
        spans = _collect_spans(page)
        if len(spans) < 6:
            log.warning(
                "Reference '%s' has little extractable text (%d spans); using default template.",
                p.name, len(spans),
            )
            return default_template()

        width, height = page.rect.width, page.rect.height
        orientation = "portrait" if height >= width else "landscape"
        size_name = (
            "letter" if abs(width - 612) < 6 and abs(height - 792) < 6
            else "a4" if abs(width - 595) < 8 and abs(height - 842) < 8
            else "custom"
        )
        margins = _infer_margins(spans, page)
        columns = _infer_columns(spans, page, margins)

        sizes = Counter(s["size"] for s in spans)
        # Weight by character count so the body size wins over stray large text.
        weighted: Counter = Counter()
        for s in spans:
            weighted[s["size"]] += len(s["text"])
        body_size = weighted.most_common(1)[0][0] if weighted else 9.2
        title_size = max(sizes) if sizes else 22.0

        fonts = Counter(s["font"] for s in spans)
        body_font, heading_font = _base_font(fonts)

        # Headings: bold or coloured spans above body size, short, on their own line.
        heading_spans = [
            s for s in spans
            if (s["bold"] or s["size"] > body_size + 0.4)
            and s["size"] < title_size - 1.0
            and len(s["text"]) <= 60
        ]
        heading_size = (
            round(sum(s["size"] for s in heading_spans) / len(heading_spans), 1)
            if heading_spans else round(body_size - 0.8, 1)
        )

        # Colours: darkest frequent colour = text; most saturated non-grey = accent.
        color_counts = Counter(s["color"] for s in spans)
        text_color = min(color_counts, key=_luminance) if color_counts else "#1A1A1A"
        accent = text_color
        for color, _ in color_counts.most_common():
            v = int(color.lstrip("#"), 16)
            r, g, b = (v >> 16) & 255, (v >> 8) & 255, v & 255
            if max(r, g, b) - min(r, g, b) > 24 and _luminance(color) < 0.72:
                accent = color
                break
        heading_colors = Counter(s["color"] for s in heading_spans)
        heading_color = heading_colors.most_common(1)[0][0] if heading_colors else accent

        # Section order from headings that match known synonyms.
        mid_x = (margins["left"] + (width - margins["right"])) / 2.0
        seen: dict[str, SectionSpec] = {}
        order = 0
        for s in sorted(heading_spans, key=lambda s: (round(s["bbox"][1], 1), s["bbox"][0])):
            key = _match_section_key(s["text"])
            if not key or key in seen:
                continue
            seen[key] = SectionSpec(
                key=key,
                heading=re.sub(r"\s{2,}", " ", s["text"]).strip(" :·|").upper(),
                column=1 if (columns == 2 and s["bbox"][0] > mid_x) else 0,
                order=order,
                style="metrics" if key == "metrics"
                else "bullets" if key == "missing_material_information"
                else "paragraph",
            )
            order += 1

        sections = list(seen.values())
        if len(sections) < 3:
            log.info(
                "Only %d recognisable sections in the reference; merging with the "
                "fallback structure.", len(sections)
            )
            existing = {s.key for s in sections}
            next_order = len(sections)
            for fb in FALLBACK_SECTIONS:
                if fb.key not in existing:
                    added = fb.model_copy()
                    added.order = next_order
                    next_order += 1
                    sections.append(added)

        # Density: total words on the reference page drives per-section budgets.
        total_words = sum(len(s["text"].split()) for s in spans)
        body_words = max(180, min(total_words, 700))
        paragraph_sections = [s for s in sections if s.style == "paragraph"] or sections
        per_section = max(35, int(body_words / max(len(paragraph_sections), 1)))
        for s in sections:
            if s.style == "paragraph":
                s.approx_words = per_section if s.key != "investment_opportunity" else min(
                    120, max(75, int(per_section * 1.5))
                )
            elif s.style == "bullets":
                s.approx_words = 40

        # Metric callouts: distinct large non-heading numeric spans.
        metric_like = [
            s for s in spans
            if s["size"] >= body_size + 1.5
            and re.search(r"\d", s["text"])
            and len(s["text"]) <= 18
            and s["size"] < title_size - 1.0
        ]
        metric_count = 4
        if metric_like:
            metric_count = max(3, min(6, len(metric_like)))

        # A metric band usually carries no heading of its own, so heading matching
        # never finds it. Insert one explicitly when the callouts are visible,
        # positioned where they sit on the reference page.
        if metric_like and not any(s.key == "metrics" for s in sections):
            band_y = min(s["bbox"][1] for s in metric_like)
            insert_at = sum(
                1 for s in sections
                if any(
                    hs["bbox"][1] < band_y
                    for hs in heading_spans
                    if _match_section_key(hs["text"]) == s.key
                )
            )
            for s in sections:
                if s.order >= insert_at:
                    s.order += 1
            sections.append(SectionSpec(
                key="metrics", heading="KEY METRICS", column=0,
                order=insert_at, approx_words=0, style="metrics",
            ))
            sections.sort(key=lambda s: s.order)
            log.debug("Inserted metric band at position %d (%d callouts)",
                      insert_at, len(metric_like))

        # Rules / dividers.
        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []
        horizontal_rules = [
            d for d in drawings
            if d.get("rect") is not None
            and pymupdf.Rect(d["rect"]).height < 3
            and pymupdf.Rect(d["rect"]).width > width * 0.2
        ]
        rule_color = "#C8D0D8"
        for d in horizontal_rules:
            stroke = d.get("color") or d.get("fill")
            if stroke and isinstance(stroke, (tuple, list)) and len(stroke) >= 3:
                rule_color = "#{:02X}{:02X}{:02X}".format(
                    int(stroke[0] * 255), int(stroke[1] * 255), int(stroke[2] * 255)
                )
                break

        # Footer: text in the bottom 8% of the page.
        footer_spans = [s for s in spans if s["bbox"][3] > height * 0.92]
        footer_size = (
            round(min(s["size"] for s in footer_spans), 1) if footer_spans else 6.8
        )

        spec = TemplateSpec(
            page=PageSpec(
                size=size_name,
                width_pt=round(width, 1),
                height_pt=round(height, 1),
                orientation=orientation,
                margins=margins,
            ),
            header=HeaderSpec(
                company_name_case="upper"
                if any(
                    s["size"] >= title_size - 0.5 and s["text"].isupper() for s in spans
                )
                else "as-is",
                show_rule=bool(horizontal_rules),
                show_tagline=True,
                show_meta_line=True,
            ),
            sections=sorted(sections, key=lambda s: s.order),
            typography=TypographySpec(
                body_font=body_font,
                heading_font=heading_font,
                title_size=round(min(max(title_size, 15.0), 30.0), 1),
                tagline_size=round(min(max(body_size + 1.2, 9.0), 13.0), 1),
                heading_size=round(min(max(heading_size, 7.0), 11.0), 1),
                body_size=round(min(max(body_size, 8.5), 11.0), 1),
                metric_value_size=round(min(max(body_size + 4.5, 12.0), 18.0), 1),
                footer_size=round(min(max(footer_size, 5.8), 8.0), 1),
            ),
            colors=ColorSpec(
                text=text_color,
                heading=heading_color,
                accent=accent,
                rule=rule_color,
                muted="#5A6672",
                metric_value=accent,
            ),
            footer=FooterSpec(enabled=bool(footer_spans), show_rule=bool(horizontal_rules)),
            columns=columns,
            metric_callout_count=metric_count,
            total_body_words=body_words,
            source="inferred",
            notes=[
                f"Measured from {p.name}",
                f"{len(spans)} text spans, {len(horizontal_rules)} horizontal rules",
                f"{columns}-column layout inferred",
            ],
        )
    finally:
        doc.close()

    # Optional AI refinement of the structural read (design only, never facts).
    if ai_provider is not None:
        try:
            refined = ai_provider.analyze_reference(spec, _reference_text_dump(p))
            if refined is not None:
                refined.source = "inferred+ai"
                spec = refined
        except Exception as exc:
            log.warning("AI reference refinement failed, keeping measured spec: %s", exc)

    # Layout comes from the reference; the content contract always comes from the
    # canonical template.
    spec = apply_canonical_contract(spec)

    log.info(
        "Reference template: %s %s, %d columns, %d sections, body %.1fpt",
        spec.page.size, spec.page.orientation, spec.columns,
        len(spec.sections), spec.typography.body_size,
    )
    return spec


def apply_canonical_contract(spec: TemplateSpec) -> TemplateSpec:
    """Attach the canonical guidance and source fields to a spec's sections.

    A reference PDF supplies layout, not meaning. Whatever the reference looks
    like, each recognised section keeps the same content contract — what it must
    answer and which schema fields feed it — so inference cannot quietly change
    what the app analyses.
    """
    try:
        canonical = load_template(CANONICAL_TEMPLATE_PATH)
    except (OSError, json.JSONDecodeError, ValidationError):
        return spec

    contract = {s.key: s for s in canonical.sections}
    for section in spec.sections:
        source = contract.get(section.key)
        if source is None:
            continue
        if not section.guidance:
            section.guidance = source.guidance
        if not section.source_fields:
            section.source_fields = list(source.source_fields)
        if not section.optional:
            section.optional = source.optional
    return spec


def _reference_text_dump(path: Path, max_chars: int = 6000) -> str:
    """Plain-text rendering of the reference, used only for structure inference."""
    try:
        doc = pymupdf.open(str(path))
    except Exception:
        return ""
    try:
        return "\n".join(doc[i].get_text() for i in range(min(doc.page_count, 2)))[:max_chars]
    finally:
        doc.close()
