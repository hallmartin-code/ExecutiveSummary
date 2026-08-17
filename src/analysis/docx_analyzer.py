"""Infer a design template from a Word (.docx) reference document.

Word carries its structure explicitly — styles, run properties, paragraph
spacing, indentation, and tables — so this reads far more reliably than measuring
a PDF. As with the PDF path, only design is taken: geometry, typography, colour,
section order, list style, and density. No factual content survives.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from ..utils.logging import get_logger
from .reference_analyzer import (
    ColorSpec,
    FooterSpec,
    HeaderSpec,
    MastheadSpec,
    PageSpec,
    SectionSpec,
    SidebarField,
    SpacingSpec,
    TableSpec,
    TemplateSpec,
    TitleBarSpec,
    TypographySpec,
    _match_section_key,
    apply_canonical_contract,
)

log = get_logger("docx_analyzer")

# A leading dash or bullet glyph followed by whitespace marks a list item in
# documents that draw their own bullets rather than using Word list numbering.
_BULLET_PREFIX = re.compile(r"^\s*[–—\-•▪◦]\s+")
_NUMBER_PREFIX = re.compile(r"^\s*\d+[.)]\s+")

# Sidebar rows are "Label:  value" pairs.
_SIDEBAR_ROW = re.compile(r"^([A-Z][A-Za-z .&/]{2,24}):\s{1,4}(.*)$")


class DocxParseError(RuntimeError):
    """Raised when a .docx reference cannot be opened or is empty."""


def _hex(color: Any) -> str | None:
    try:
        if color is not None and color.rgb is not None:
            return f"#{str(color.rgb).upper()}"
    except Exception:
        pass
    return None


def _run_info(run: Any) -> dict[str, Any]:
    return {
        "text": run.text or "",
        "size": run.font.size.pt if run.font.size else None,
        "bold": bool(run.bold),
        "italic": bool(run.italic),
        "color": _hex(run.font.color),
        "name": run.font.name,
    }


def _iter_body(document: Any) -> Iterator[tuple[str, Any]]:
    """Yield ``("p", paragraph)`` / ``("tbl", table)`` in true document order."""
    paragraphs = {p._p: p for p in document.paragraphs}
    tables = {t._tbl: t for t in document.tables}
    for child in document.element.body.iterchildren():
        if child in paragraphs:
            yield "p", paragraphs[child]
        elif child in tables:
            yield "tbl", tables[child]


def _classify(paragraph: Any, runs: list[dict[str, Any]], accent: str | None) -> str:
    """Label a paragraph as heading, bullet, numbered, caption, or body."""
    text = paragraph.text.strip()
    if not text:
        return "blank"

    fmt = paragraph.paragraph_format
    indent = fmt.left_indent.pt if fmt.left_indent else 0.0
    visible = [r for r in runs if r["text"].strip()]
    if not visible:
        return "blank"

    if _BULLET_PREFIX.match(text) or indent >= 12:
        return "bullet"
    if _NUMBER_PREFIX.match(text):
        return "numbered"

    # A heading is short, entirely bold, and set in the accent colour.
    all_bold = all(r["bold"] for r in visible)
    accent_coloured = accent is not None and any(
        (r["color"] or "").upper() == accent.upper() for r in visible
    )
    if all_bold and accent_coloured and len(text) <= 60:
        return "heading"

    if all(r["italic"] for r in visible) and len(text) <= 260:
        return "caption"

    return "body"


def _dominant(values: list[Any], default: Any) -> Any:
    counted = Counter(v for v in values if v is not None)
    return counted.most_common(1)[0][0] if counted else default


def analyze_docx_reference(path: str | Path) -> TemplateSpec:
    """Read a .docx reference and return a reconstructable design spec."""
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - dependency guaranteed by requirements
        raise DocxParseError("python-docx is not installed.") from exc

    p = Path(path)
    try:
        document = Document(str(p))
    except Exception as exc:
        raise DocxParseError(f"Could not open '{p.name}': {exc}") from exc

    blocks = list(_iter_body(document))
    if not blocks:
        raise DocxParseError(f"'{p.name}' contains no content.")

    # -- page geometry -----------------------------------------------------
    section = document.sections[0]
    width = float(section.page_width.pt)
    height = float(section.page_height.pt)
    margins = {
        "left": round(float(section.left_margin.pt), 1),
        "right": round(float(section.right_margin.pt), 1),
        "top": round(float(section.top_margin.pt), 1),
        "bottom": round(float(section.bottom_margin.pt), 1),
    }

    # -- collect run statistics -------------------------------------------
    all_runs: list[dict[str, Any]] = []
    for kind, block in blocks:
        if kind == "p":
            all_runs.extend(_run_info(r) for r in block.runs)

    text_runs = [r for r in all_runs if r["text"].strip()]
    if not text_runs:
        raise DocxParseError(f"'{p.name}' contains no readable text.")

    # Accent colour, derived from headings rather than from bold runs generally.
    #
    # Counting every bold run conflates two different things: section headings and
    # the bold lead-in labels that open body sentences ("Business model: ..."). The
    # lead-ins are set in the body colour and can outnumber the headings, which
    # would elect the body colour as the accent and then match no heading at all.
    # A heading is instead identified structurally first — a short, non-list
    # paragraph whose runs are *entirely* bold — and the accent is the dominant
    # colour among those.
    heading_candidates: list[dict[str, Any]] = []
    for kind, block in blocks:
        if kind != "p":
            continue
        text = block.text.strip()
        if not text or len(text) > 60:
            continue
        if _BULLET_PREFIX.match(text) or _NUMBER_PREFIX.match(text):
            continue
        runs = [_run_info(r) for r in block.runs if r.text.strip()]
        if runs and all(r["bold"] for r in runs):
            heading_candidates.extend(runs)

    heading_colors = Counter(r["color"] for r in heading_candidates if r["color"])
    accent = heading_colors.most_common(1)[0][0] if heading_colors else "#1F4E79"

    # Body colour: most common colour weighted by characters set.
    weighted: Counter = Counter()
    for r in text_runs:
        if r["color"]:
            weighted[r["color"]] += len(r["text"])
    body_color = weighted.most_common(1)[0][0] if weighted else "#222222"

    # A lighter secondary accent is used for bullet glyphs and contact details.
    secondary = None
    for color, _ in Counter(r["color"] for r in text_runs if r["color"]).most_common():
        if color not in (accent, body_color) and color.upper() not in ("#FFFFFF", "#000000"):
            secondary = color
            break

    sizes = [r["size"] for r in text_runs if r["size"]]
    size_by_chars: Counter = Counter()
    for r in text_runs:
        if r["size"]:
            size_by_chars[r["size"]] += len(r["text"].strip())
    body_size = size_by_chars.most_common(1)[0][0] if size_by_chars else 9.0
    largest = max(sizes) if sizes else 15.0

    font_name = _dominant([r["name"] for r in text_runs], "Calibri")

    # -- masthead ----------------------------------------------------------
    masthead = MastheadSpec()
    masthead_sections: list[SectionSpec] = []
    title_bar = TitleBarSpec()

    first_paragraphs = [b for k, b in blocks[:2] if k == "p" and b.text.strip()]
    if first_paragraphs:
        runs = [_run_info(r) for r in first_paragraphs[0].runs if r.text.strip()]
        if len(runs) >= 2 and runs[0]["bold"]:
            title_bar = TitleBarSpec(
                enabled=True,
                document_label="Company Overview & Executive Summary",
                company_name_case="upper" if runs[0]["text"].isupper() else "as-is",
            )

    masthead_table = next(
        (b for k, b in blocks if k == "tbl" and len(b.rows) == 1 and len(b.columns) == 2),
        None,
    )
    if masthead_table is not None:
        left_cell, right_cell = masthead_table.rows[0].cells[:2]

        sidebar_fields: list[SidebarField] = []
        group = 0
        seen_blank = False
        for para in left_cell.paragraphs:
            text = para.text.strip()
            if not text:
                seen_blank = True
                continue
            match = _SIDEBAR_ROW.match(text)
            if match:
                label = match.group(1).strip()
                if seen_blank and sidebar_fields:
                    group += 1
                    seen_blank = False
                sidebar_fields.append(
                    SidebarField(
                        key=re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_"),
                        label=label,
                        group=group,
                    )
                )
        masthead = MastheadSpec(
            enabled=True,
            sidebar_width_ratio=0.34,
            sidebar_fields=sidebar_fields,
            show_contact=any("contact" in q.text.lower() for q in left_cell.paragraphs),
            show_not_stated=any(
                "not stated" in q.text.lower() for q in left_cell.paragraphs
            ),
        )

        order = 0
        for para in right_cell.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            runs = [_run_info(r) for r in para.runs if r.text.strip()]
            if _classify(para, runs, accent) == "heading":
                key = _match_section_key(text) or re.sub(
                    r"[^a-z0-9]+", "_", text.lower()
                ).strip("_")
                masthead_sections.append(
                    SectionSpec(
                        key=key,
                        heading=text,
                        column=2,          # 2 marks the masthead's main column
                        order=order,
                        approx_words=70,
                        style="paragraph",
                    )
                )
                order += 1

    # -- body sections -----------------------------------------------------
    body_sections: list[SectionSpec] = []
    current: SectionSpec | None = None
    word_counts: dict[str, int] = {}
    styles_seen: dict[str, Counter] = {}
    order = len(masthead_sections)

    for kind, block in blocks:
        if kind == "tbl":
            if block is masthead_table:
                continue
            if current is not None:
                styles_seen.setdefault(current.key, Counter())["table"] += 1
            continue

        text = block.text.strip()
        if not text:
            continue
        runs = [_run_info(r) for r in block.runs if r.text.strip()]
        kind_of = _classify(block, runs, accent)

        if kind_of == "heading":
            key = _match_section_key(text) or re.sub(
                r"[^a-z0-9]+", "_", text.lower()
            ).strip("_")
            current = SectionSpec(
                key=key, heading=text, column=0, order=order,
                approx_words=60, style="paragraph",
            )
            body_sections.append(current)
            word_counts[key] = 0
            styles_seen[key] = Counter()
            order += 1
        elif current is not None:
            word_counts[current.key] = word_counts.get(current.key, 0) + len(text.split())
            styles_seen.setdefault(current.key, Counter())[kind_of] += 1

    # Style and density per section, from what actually follows each heading.
    for spec in body_sections:
        counts = styles_seen.get(spec.key, Counter())
        if counts.get("table"):
            spec.style = "table"
        elif counts.get("bullet", 0) > counts.get("body", 0):
            spec.style = "bullets"
        elif counts.get("numbered", 0) > counts.get("body", 0):
            spec.style = "numbered"
        spec.approx_words = max(25, word_counts.get(spec.key, 60))

    for spec in masthead_sections:
        spec.approx_words = max(35, spec.approx_words)

    sections = masthead_sections + body_sections
    if not sections:
        raise DocxParseError(f"No section headings were found in '{p.name}'.")

    bullet_sizes = [
        r["size"] for r in text_runs if r["size"] and r["size"] < body_size
    ]
    bullet_size = min(bullet_sizes) if bullet_sizes else body_size - 0.5

    spec = TemplateSpec(
        page=PageSpec(
            size="letter" if abs(width - 612) < 6 else "custom",
            width_pt=round(width, 1),
            height_pt=round(height, 1),
            orientation="portrait" if height >= width else "landscape",
            margins=margins,
        ),
        header=HeaderSpec(show_tagline=True, show_meta_line=False, show_rule=False),
        title_bar=title_bar,
        masthead=masthead,
        sections=sections,
        typography=TypographySpec(
            body_font="Helvetica",
            heading_font="Helvetica-Bold",
            title_size=round(min(max(largest, 12.0), 24.0), 1),
            masthead_name_size=round(min(max(largest, 12.0), 22.0), 1),
            title_bar_size=round(min(max(largest - 2.0, 11.0), 20.0), 1),
            tagline_size=round(max(body_size - 1.0, 7.5), 1),
            heading_size=round(min(max(body_size + 1.0, 8.5), 12.0), 1),
            body_size=round(min(max(body_size, 8.5), 11.0), 1),
            bullet_size=round(min(max(bullet_size, 8.0), 10.5), 1),
            caption_size=round(max(body_size - 1.5, 6.5), 1),
            table_size=round(max(body_size - 1.5, 6.5), 1),
            note_size=round(max(body_size - 2.0, 6.0), 1),
            sidebar_label_size=round(max(body_size - 1.0, 7.0), 1),
            sidebar_value_size=round(max(body_size - 1.0, 7.0), 1),
            heading_case="as-is",
            heading_tracking=0.0,
            line_height=1.22,
        ),
        colors=ColorSpec(
            text=body_color,
            heading=accent,
            accent=accent,
            accent_secondary=secondary or accent,
            muted="#595959",
            sidebar_value="#333333",
            rule="#D6DEE6",
            metric_band_bg="#F4F7FA",
            metric_value=accent,
        ),
        spacing=SpacingSpec(
            section_gap=8.0, heading_gap=2.5, paragraph_gap=4.0,
            bullet_indent=12.0, column_gap=14.0,
        ),
        table=TableSpec(header_bg=accent, header_text="#FFFFFF"),
        footer=FooterSpec(enabled=True, show_rule=True),
        columns=1,
        metric_callout_count=0,
        metrics_style="sidebar",
        total_body_words=sum(word_counts.values()) or 600,
        source="inferred",
        notes=[
            f"Measured from {p.name}",
            f"{len(sections)} sections, {len(text_runs)} runs",
            f"masthead={'yes' if masthead.enabled else 'no'}, "
            f"font={font_name}, body={body_size}pt",
        ],
    )

    spec = apply_canonical_contract(spec)
    log.info(
        "Word reference: %d sections (%d in masthead), body %.1fpt, accent %s",
        len(spec.sections), len(masthead_sections), spec.typography.body_size, accent,
    )
    return spec
