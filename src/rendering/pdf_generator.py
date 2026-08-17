"""Native PDF rendering with ReportLab.

Everything is drawn as vector primitives and real text runs, so the output has a
selectable text layer and stays crisp at any zoom. Nothing is rasterised.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

from ..analysis.reference_analyzer import TemplateSpec
from ..analysis.summary_generator import ExecutiveSummary
from ..config import RenderConfig
from ..utils.logging import get_logger
from .layout import LayoutEngine, Page

log = get_logger("pdf")


def _color(value: str):
    try:
        return HexColor(value)
    except Exception:  # pragma: no cover - malformed colour from inference
        return HexColor("#1A1A1A")


def draw_page(c: canvas.Canvas, page: Page) -> None:
    """Emit one composed page onto the canvas."""
    # Background fills first, then rules, then text.
    for rect in page.rects:
        c.setFillColor(_color(rect.color))
        if rect.radius > 0:
            c.roundRect(rect.x, rect.y, rect.width, rect.height, rect.radius,
                        stroke=0, fill=1)
        else:
            c.rect(rect.x, rect.y, rect.width, rect.height, stroke=0, fill=1)

    for rule in page.rules:
        c.setStrokeColor(_color(rule.color))
        c.setLineWidth(rule.width)
        # A "rule" with equal x values is a vertical divider.
        if abs(rule.x1 - rule.x0) < 0.01:
            c.line(rule.x0, rule.y, rule.x0, rule.y + 14)
        else:
            c.line(rule.x0, rule.y, rule.x1, rule.y)

    for line in page.lines:
        if not line.text:
            continue
        font = line.font
        try:
            c.setFont(font, line.size)
        except Exception:  # pragma: no cover - font not registered
            font = "Helvetica"
            c.setFont(font, line.size)

        # Every run goes through a text object with char spacing set explicitly.
        # PDF character spacing (Tc) is part of the persistent text state, so a
        # tracked heading would otherwise widen every run drawn after it.
        t = c.beginText(line.x, line.y)
        t.setFont(font, line.size)
        t.setFillColor(_color(line.color))
        t.setCharSpace(line.tracking or 0)
        t.textOut(line.text)
        c.drawText(t)


def render_pdf(
    summary: ExecutiveSummary,
    template: TemplateSpec,
    output_path: str | Path,
    render_config: RenderConfig | None = None,
) -> tuple[Path, Page]:
    """Lay out and write the executive summary PDF.

    Returns the written path and the composed :class:`Page` for validation.
    """
    cfg = render_config or RenderConfig()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    engine = LayoutEngine(template, min_body_pt=cfg.min_body_font_pt)
    if cfg.allow_multipage:
        pages = engine.fit_multipage(summary, max_pages=max(cfg.max_pages or 3, 2))
    else:
        pages = [engine.fit(summary, max_attempts=cfg.max_layout_attempts)]
    page = pages[0]

    c = canvas.Canvas(str(out), pagesize=(page.width, page.height))
    c.setTitle(f"{summary.company_name} — Investor Executive Summary")
    c.setAuthor("Investor Executive Summary Generator")
    c.setSubject("Investor executive summary generated from the company pitch deck")
    # Do not embed the deck path or any other source detail in metadata.
    c.setKeywords("executive summary, investor, confidential")

    for rendered in pages:
        draw_page(c, rendered)
        c.showPage()
    c.save()

    log.info(
        "Rendered PDF: %s (%d page(s), body %.1fpt, %d text lines)",
        out.name, len(pages), page.body_size, sum(len(p.lines) for p in pages),
    )
    return out, page


_META_VALUE_CHARS = 34


def _condense(value: str) -> str:
    """Reduce a field to the short form the header strip can carry.

    Extracted values are often full clauses ("$2M to reach FDA approval in 2
    years or less"); the meta strip needs the figure, not the sentence.
    """
    v = re.sub(r"\s+", " ", value).strip(" .;,")
    # Cut at the first clause boundary.
    v = re.split(r"\s+(?:to|for|with|in order to)\s+|[:;(]", v)[0].strip(" .;,-")
    if len(v) > _META_VALUE_CHARS:
        v = v[:_META_VALUE_CHARS].rsplit(" ", 1)[0].rstrip(" .,;-")
    return v


def build_meta_line(summary: ExecutiveSummary, data_bits: dict[str, str | None]) -> str | None:
    """Assemble the header's ``SECTOR | STAGE | RAISE`` strip from present values."""
    parts = []
    for key, value in data_bits.items():
        if not value:
            continue
        condensed = _condense(str(value))
        if condensed:
            parts.append(f"{key.upper()} {condensed}")
    return "   |   ".join(parts) if parts else None


def default_footer_note(company: str, include_date: bool = True) -> str:
    note = (
        f"Confidential. Prepared for accredited investors. All figures are as stated in the "
        f"{company} pitch deck and have not been independently verified."
    )
    if include_date:
        note += f" Compiled {date.today().strftime('%d %B %Y')}."
    return note
