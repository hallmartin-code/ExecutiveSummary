"""Content-prioritising layout engine.

The engine measures a full page composition at a given typographic scale. If the
content overflows, a fixed escalation ladder is applied: drop the lowest-priority
content first, tighten spacing next, and reduce type size only as a last resort —
never below the configured legibility floor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from reportlab.pdfbase import pdfmetrics

from ..analysis.reference_analyzer import TemplateSpec
from ..analysis.summary_generator import (
    ExecutiveSummary,
    FinancialTable,
    MetricCallout,
    last_sentence_end,
)
from ..utils.logging import get_logger

log = get_logger("layout")


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


@dataclass
class TextLine:
    text: str
    font: str
    size: float
    color: str
    x: float
    y: float          # baseline, PDF coordinates (origin bottom-left)
    tracking: float = 0.0
    width: float = 0.0


@dataclass
class RuleLine:
    x0: float
    y: float
    x1: float
    color: str
    width: float = 0.6


@dataclass
class FilledRect:
    x: float
    y: float
    width: float
    height: float
    color: str
    radius: float = 0.0


@dataclass
class Page:
    """Everything the renderer needs to draw, already positioned."""

    width: float
    height: float
    lines: list[TextLine] = field(default_factory=list)
    rules: list[RuleLine] = field(default_factory=list)
    rects: list[FilledRect] = field(default_factory=list)
    overflow_pt: float = 0.0
    used_scale: float = 1.0
    body_size: float = 9.2
    dropped_sections: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def fits(self) -> bool:
        return self.overflow_pt <= 0.01


# ---------------------------------------------------------------------------
# Text measurement
# ---------------------------------------------------------------------------


def text_width(text: str, font: str, size: float, tracking: float = 0.0) -> float:
    try:
        base = pdfmetrics.stringWidth(text, font, size)
    except Exception:  # pragma: no cover - unknown font
        base = pdfmetrics.stringWidth(text, "Helvetica", size)
    return base + tracking * max(len(text) - 1, 0)


def wrap_text(
    text: str, font: str, size: float, max_width: float, tracking: float = 0.0
) -> list[str]:
    """Greedy word wrap; over-long single words are broken by character."""
    if not text:
        return []
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if text_width(candidate, font, size, tracking) <= max_width or not current:
                if text_width(candidate, font, size, tracking) > max_width and not current:
                    # A single word wider than the column: break it.
                    chunk = ""
                    for ch in word:
                        if text_width(chunk + ch, font, size, tracking) > max_width and chunk:
                            lines.append(chunk)
                            chunk = ch
                        else:
                            chunk += ch
                    current = chunk
                    continue
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


@dataclass
class RichSegment:
    """A styled run within a line of mixed-weight text."""

    text: str
    font: str
    color: str


# "Lead-in: detail" — the label before the colon is set bold, matching the
# reference document's convention for bullets and body paragraphs alike.
_LEAD_IN_RE = re.compile(r"^([^:]{2,42}):\s+(?=\S)")


def split_lead_in(text: str) -> tuple[str | None, str]:
    """Separate a bold lead-in label from the rest of the sentence."""
    match = _LEAD_IN_RE.match(text or "")
    if not match:
        return None, text
    label = match.group(1).strip()
    # A colon inside a sentence is not a label; require a short, title-like run.
    if len(label.split()) > 7:
        return None, text
    return label, text[match.end():].strip()


def wrap_rich(
    segments: list[RichSegment], size: float, max_width: float, tracking: float = 0.0
) -> list[list[RichSegment]]:
    """Word-wrap styled runs, preserving each word's styling."""
    words: list[RichSegment] = []
    for seg in segments:
        for i, word in enumerate(seg.text.split()):
            words.append(RichSegment(word, seg.font, seg.color))

    lines: list[list[RichSegment]] = []
    current: list[RichSegment] = []
    current_width = 0.0
    space = text_width(" ", segments[0].font if segments else "Helvetica", size, tracking)

    for word in words:
        w = text_width(word.text, word.font, size, tracking)
        extra = w if not current else w + space
        if current and current_width + extra > max_width:
            lines.append(current)
            current, current_width = [word], w
        else:
            current.append(word)
            current_width += extra
    if current:
        lines.append(current)
    return lines


# ---------------------------------------------------------------------------
# Section priority (Step 9 content prioritisation)
# ---------------------------------------------------------------------------

# Higher survives longer. Order encodes what an investor needs most.
SECTION_PRIORITY: dict[str, int] = {
    "corporate_summary": 100,
    "key_facts": 92,
    "financing_opportunity": 90,
    "innovative_solution": 88,
    "commercial_validation": 86,
    "metrics": 85,
    "target_markets": 82,
    "products_platform": 78,
    "competitive_advantages": 74,
    "management_team": 68,
    "use_of_funds": 64,
    "projected_financials": 58,
    "investment_thesis": 52,
    "risks": 40,
    "missing_material_information": 30,
}


@dataclass
class SectionBlock:
    key: str
    heading: str
    body: str | None
    bullets: list[str]
    style: str
    column: int
    order: int
    priority: int
    table: Any | None = None


def build_blocks(summary: ExecutiveSummary, template: TemplateSpec) -> list[SectionBlock]:
    """Assemble the renderable sections, skipping anything with no content."""
    blocks: list[SectionBlock] = []
    for spec in sorted(template.sections, key=lambda s: s.order):
        priority = SECTION_PRIORITY.get(spec.key, 50)

        if spec.key == "metrics":
            if summary.metrics:
                blocks.append(SectionBlock(
                    key="metrics", heading=spec.heading, body=None, bullets=[],
                    style="metrics", column=spec.column, order=spec.order,
                    priority=priority,
                ))
            continue

        value = summary.section_text(spec.key)

        if isinstance(value, FinancialTable):
            if not value.is_empty():
                blocks.append(SectionBlock(
                    key=spec.key, heading=spec.heading, body=value.caption, bullets=[],
                    style="table", column=spec.column, order=spec.order,
                    priority=priority, table=value,
                ))
            continue

        if isinstance(value, list):
            items = [str(v) for v in value if str(v).strip()]
            if items:
                style = spec.style if spec.style in ("bullets", "numbered") else "bullets"
                blocks.append(SectionBlock(
                    key=spec.key, heading=spec.heading, body=None, bullets=items,
                    style=style, column=spec.column, order=spec.order,
                    priority=priority,
                ))
            continue

        if isinstance(value, str) and value.strip():
            blocks.append(SectionBlock(
                key=spec.key, heading=spec.heading, body=value.strip(), bullets=[],
                style="paragraph", column=spec.column, order=spec.order,
                priority=priority,
            ))
    return blocks


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@dataclass
class ScaleState:
    """One rung of the fitting ladder."""

    font_scale: float = 1.0
    spacing_scale: float = 1.0
    trim_ratio: float = 1.0     # fraction of each paragraph's words retained
    drop_below_priority: int = 0


class LayoutEngine:
    """Composes an :class:`ExecutiveSummary` into a positioned :class:`Page`."""

    def __init__(self, template: TemplateSpec, min_body_pt: float = 8.5) -> None:
        self.t = template
        self.min_body_pt = min_body_pt

    # -- helpers ------------------------------------------------------------

    def _columns(self, state: ScaleState) -> tuple[float, float, list[float]]:
        m = self.t.page.margins
        left = m["left"]
        right = self.t.page.width_pt - m["right"]
        content_width = right - left
        n = max(1, self.t.columns)
        gap = self.t.spacing.column_gap * state.spacing_scale
        col_width = (content_width - gap * (n - 1)) / n
        xs = [left + i * (col_width + gap) for i in range(n)]
        return content_width, col_width, xs

    def _trim(self, text: str, state: ScaleState) -> str:
        """Shorten a paragraph, always leaving it ending as a finished sentence."""
        if state.trim_ratio >= 0.999 or not text:
            return text
        words = text.split()
        keep = max(12, int(len(words) * state.trim_ratio))
        if keep >= len(words):
            return text

        out = " ".join(words[:keep])
        # Prefer a real sentence boundary; a clause boundary still needs a stop
        # added, otherwise the paragraph ends on a dangling semicolon.
        sentence_cut = last_sentence_end(out)
        if sentence_cut > len(out) * 0.5:
            return out[: sentence_cut + 1].strip()

        clause_cut = out.rfind("; ")
        if clause_cut > len(out) * 0.5:
            return out[:clause_cut].rstrip(",;: ") + "."

        return out.rstrip(",;:- ") + "."

    # -- header -------------------------------------------------------------

    def _draw_header(
        self, page: Page, summary: ExecutiveSummary, state: ScaleState, y: float
    ) -> float:
        typ, col, sp = self.t.typography, self.t.colors, self.t.spacing
        m = self.t.page.margins
        left = m["left"]
        right = self.t.page.width_pt - m["right"]

        if self.t.header.eyebrow:
            size = max(5.8, typ.footer_size * state.font_scale)
            y -= size
            page.lines.append(TextLine(
                self.t.header.eyebrow.upper(), typ.heading_font, size,
                col.muted, left, y, tracking=1.4,
            ))
            y -= sp.paragraph_gap * state.spacing_scale

        name = summary.company_name
        if self.t.header.company_name_case == "upper":
            name = name.upper()
        title_size = typ.title_size * state.font_scale
        # Shrink an over-long company name rather than letting it clip.
        while text_width(name, typ.heading_font, title_size) > (right - left) and title_size > 11:
            title_size -= 0.5
        y -= title_size
        page.lines.append(TextLine(name, typ.heading_font, title_size, col.heading, left, y))

        if summary.tagline and self.t.header.show_tagline:
            tag_size = max(8.0, typ.tagline_size * state.font_scale)
            y -= sp.paragraph_gap * state.spacing_scale + tag_size
            for line in wrap_text(summary.tagline, typ.body_font, tag_size, right - left)[:2]:
                page.lines.append(TextLine(line, typ.body_font, tag_size, col.muted, left, y))
                y -= tag_size * self.t.typography.line_height

        if summary.meta_line and self.t.header.show_meta_line:
            meta_size = max(6.2, typ.footer_size * state.font_scale + 0.4)
            y -= sp.paragraph_gap * state.spacing_scale + meta_size
            for line in wrap_text(summary.meta_line, typ.body_font, meta_size, right - left)[:2]:
                page.lines.append(TextLine(line, typ.body_font, meta_size, col.muted, left, y))
                y -= meta_size * 1.2

        if self.t.header.show_rule:
            y -= sp.section_gap * state.spacing_scale * 0.7
            page.rules.append(RuleLine(left, y, right, col.rule, 0.9))
            y -= sp.section_gap * state.spacing_scale * 0.5

        return y

    # -- metric band --------------------------------------------------------

    def _metric_band_height(self, state: ScaleState) -> float:
        typ = self.t.typography
        value_size = typ.metric_value_size * state.font_scale
        label_size = max(5.6, typ.metric_label_size * state.font_scale)
        return value_size + label_size + 12.0 * state.spacing_scale

    def _draw_metric_band(
        self, page: Page, metrics: list[MetricCallout], state: ScaleState, y: float
    ) -> float:
        if not metrics:
            return y
        typ, col, sp = self.t.typography, self.t.colors, self.t.spacing
        m = self.t.page.margins
        left = m["left"]
        right = self.t.page.width_pt - m["right"]
        width = right - left

        value_size = typ.metric_value_size * state.font_scale
        label_size = max(5.6, typ.metric_label_size * state.font_scale)
        pad = 6.0 * state.spacing_scale
        band_h = self._metric_band_height(state)

        y -= band_h
        page.rects.append(FilledRect(left, y, width, band_h, col.metric_band_bg, radius=2.0))

        n = len(metrics)
        cell_w = width / n
        for i, metric in enumerate(metrics):
            cx = left + cell_w * i + cell_w / 2.0

            value = metric.value
            v_size = value_size
            while text_width(value, typ.heading_font, v_size) > cell_w - 8 and v_size > 7.5:
                v_size -= 0.4
            vy = y + band_h - pad - v_size * 0.86
            page.lines.append(TextLine(
                value, typ.heading_font, v_size, col.metric_value,
                cx - text_width(value, typ.heading_font, v_size) / 2.0, vy,
            ))

            label = metric.label.upper()
            l_size = label_size
            while text_width(label, typ.body_font, l_size, 0.5) > cell_w - 6 and l_size > 4.8:
                l_size -= 0.3
            ly = y + pad * 0.8
            page.lines.append(TextLine(
                label, typ.body_font, l_size, col.muted,
                cx - text_width(label, typ.body_font, l_size, 0.5) / 2.0, ly, tracking=0.5,
            ))

            if i < n - 1:
                divider_x = left + cell_w * (i + 1)
                page.rules.append(RuleLine(
                    divider_x, y + pad, divider_x, col.rule, 0.5
                ))

        return y - sp.section_gap * state.spacing_scale

    # -- section measurement / drawing --------------------------------------

    def _section_height(self, block: SectionBlock, col_width: float, state: ScaleState) -> float:
        typ, sp = self.t.typography, self.t.spacing
        heading_size = max(6.5, typ.heading_size * state.font_scale)
        body_size = max(self.min_body_pt, typ.body_size * state.font_scale)
        # Bullets are set smaller than body copy by design, but the legibility
        # floor applies to every text class: at the floor they converge rather
        # than the smaller class dropping underneath it.
        bullet_size = max(self.min_body_pt, typ.bullet_size * state.font_scale)
        leading = body_size * typ.line_height
        bullet_leading = bullet_size * typ.line_height

        h = heading_size + sp.heading_gap * state.spacing_scale

        if block.style == "table" and block.table is not None:
            size = max(5.8, typ.table_size * state.font_scale)
            row_h = size + 5.0 * state.spacing_scale
            h += row_h * (1 + len(block.table.rows[: self.t.table.max_rows]))
            if block.body:
                caption_size = max(5.8, typ.caption_size * state.font_scale)
                h += 2.0 * state.spacing_scale
                h += len(
                    wrap_text(block.body, typ.body_font, caption_size, col_width)
                ) * caption_size * 1.2
        elif block.style in ("bullets", "numbered"):
            indent = sp.bullet_indent
            for item in block.bullets:
                segments = self._rich_for(item, state)
                h += len(wrap_rich(segments, bullet_size, col_width - indent)) * bullet_leading
                h += 1.2 * state.spacing_scale
        else:
            body = self._trim(block.body or "", state)
            h += len(
                wrap_rich(self._rich_for(body, state), body_size, col_width)
            ) * leading

        return h + sp.section_gap * state.spacing_scale

    # -- title bar and masthead --------------------------------------------

    def _draw_title_bar(
        self, page: Page, summary: ExecutiveSummary, state: ScaleState, y: float
    ) -> float:
        """Company name plus a document label on one line, above a rule."""
        typ, col, sp = self.t.typography, self.t.colors, self.t.spacing
        m = self.t.page.margins
        left = m["left"]
        right = self.t.page.width_pt - m["right"]

        name_size = typ.title_bar_size * state.font_scale
        label_size = max(6.5, typ.title_bar_label_size * state.font_scale)
        name = summary.company_name
        if self.t.title_bar.company_name_case == "upper":
            name = name.upper()
        while text_width(name, typ.heading_font, name_size) > (right - left) * 0.55 and name_size > 10:
            name_size -= 0.5

        y -= name_size
        page.lines.append(TextLine(name, typ.heading_font, name_size, col.heading, left, y))

        label = summary.document_label or self.t.title_bar.document_label
        if label:
            page.lines.append(TextLine(
                label, typ.body_font, label_size, col.muted,
                left + text_width(name, typ.heading_font, name_size) + 10.0,
                y + (name_size - label_size) * 0.15,
            ))

        y -= sp.paragraph_gap * state.spacing_scale
        page.rules.append(RuleLine(left, y, right, col.rule, 0.8))
        return y - sp.section_gap * state.spacing_scale * 0.7

    def _draw_masthead(
        self, page: Page, summary: ExecutiveSummary, state: ScaleState, y: float
    ) -> float:
        """Sidebar of labelled facts beside the opening narrative column."""
        typ, col, sp = self.t.typography, self.t.colors, self.t.spacing
        m = self.t.page.margins
        left = m["left"]
        right = self.t.page.width_pt - m["right"]
        total_w = right - left

        gap = sp.column_gap * state.spacing_scale
        side_w = total_w * self.t.masthead.sidebar_width_ratio
        main_w = total_w - side_w - gap
        main_x = left + side_w + gap
        top = y

        # -- sidebar --------------------------------------------------------
        sy = y
        name_size = typ.masthead_name_size * state.font_scale
        name = summary.company_name
        while text_width(name, typ.heading_font, name_size) > side_w and name_size > 9:
            name_size -= 0.5
        sy -= name_size
        page.lines.append(TextLine(name, typ.heading_font, name_size, col.heading, left, sy))

        if summary.tagline:
            tag_size = max(6.5, typ.tagline_size * state.font_scale)
            for line in wrap_text(summary.tagline, typ.body_font, tag_size, side_w)[:3]:
                sy -= tag_size * 1.2
                page.lines.append(TextLine(line, typ.body_font, tag_size, col.muted, left, sy))

        label_size = max(6.2, typ.sidebar_label_size * state.font_scale)
        value_size = max(6.2, typ.sidebar_value_size * state.font_scale)
        previous_group = None
        for entry in summary.sidebar:
            spec = next(
                (f for f in self.t.masthead.sidebar_fields if f.key == entry.key), None
            )
            group = spec.group if spec else 0
            sy -= (6.0 if previous_group is None or group != previous_group else 2.5) * state.spacing_scale
            previous_group = group

            segments = [
                RichSegment(f"{entry.label}:", typ.heading_font, col.heading),
                RichSegment(entry.value, typ.body_font, col.sidebar_value),
            ]
            sy = self._draw_rich(
                page, segments, left, sy, side_w, value_size, value_size * 1.18
            )

        if self.t.masthead.show_contact and not summary.contact.is_empty():
            sy -= 7.0 * state.spacing_scale
            sy -= label_size
            page.lines.append(TextLine(
                self.t.masthead.contact_heading, typ.heading_font, label_size,
                col.accent_secondary, left, sy,
            ))
            for text, size, colour in (
                (summary.contact.name, value_size, col.text),
                (summary.contact.role, value_size - 0.5, col.muted),
                (summary.contact.email, value_size - 0.5, col.accent_secondary),
                (summary.contact.phone, value_size - 0.5, col.sidebar_value),
            ):
                if not text:
                    continue
                sy -= size * 1.2
                font = typ.heading_font if colour == col.text else typ.body_font
                page.lines.append(TextLine(text, font, size, colour, left, sy))

        if self.t.masthead.show_not_stated and summary.not_stated:
            note_size = max(5.6, typ.note_size * state.font_scale)
            sy -= 7.0 * state.spacing_scale
            sy -= note_size
            page.lines.append(TextLine(
                self.t.masthead.not_stated_label, typ.heading_font, note_size,
                col.muted, left, sy,
            ))
            joined = "; ".join(summary.not_stated) + "."
            for line in wrap_text(joined, typ.body_font, note_size, side_w)[:5]:
                sy -= note_size * 1.18
                page.lines.append(TextLine(line, typ.body_font, note_size, col.muted, left, sy))

        # -- main column ----------------------------------------------------
        my = top
        blocks = [b for b in build_blocks(summary, self.t) if b.column == 2]
        for block in blocks:
            if block.priority < state.drop_below_priority:
                page.dropped_sections.append(block.key)
                continue
            my = self._draw_section(page, block, main_x, my, main_w, state)

        if self.t.masthead.divider:
            bottom = min(sy, my) - sp.paragraph_gap * state.spacing_scale
            page.rules.append(RuleLine(left, bottom, right, col.rule, 0.6))
            return bottom - sp.section_gap * state.spacing_scale * 0.8

        return min(sy, my) - sp.section_gap * state.spacing_scale

    def _rich_for(self, text: str, state: ScaleState) -> list[RichSegment]:
        """Build styled runs for a sentence that may open with a bold lead-in."""
        typ, col = self.t.typography, self.t.colors
        label, rest = split_lead_in(text)
        if label is None:
            return [RichSegment(text, typ.body_font, col.text)]
        return [
            RichSegment(f"{label}:", typ.heading_font, col.text),
            RichSegment(rest, typ.body_font, col.text),
        ]

    def _draw_rich(
        self, page: Page, segments: list[RichSegment], x: float, y: float,
        width: float, size: float, leading: float,
    ) -> float:
        """Draw wrapped styled runs; returns the new cursor position."""
        for line in wrap_rich(segments, size, width):
            y -= leading
            cursor = x
            for i, seg in enumerate(line):
                page.lines.append(TextLine(seg.text, seg.font, size, seg.color, cursor, y))
                cursor += text_width(seg.text, seg.font, size)
                if i < len(line) - 1:
                    cursor += text_width(" ", seg.font, size)
        return y

    def _draw_table(
        self, page: Page, table: Any, x: float, y: float,
        width: float, state: ScaleState,
    ) -> float:
        """Draw the projections table: header band, then one row per metric."""
        typ, col, tbl = self.t.typography, self.t.colors, self.t.table
        size = max(5.8, typ.table_size * state.font_scale)
        row_h = size + 5.0 * state.spacing_scale
        columns = table.columns[: tbl.max_columns]
        if not columns:
            return y

        # The label column is wider than the value columns.
        label_w = width * 0.28
        value_w = (width - label_w) / max(len(columns) - 1, 1)

        def cell_x(index: int) -> float:
            return x if index == 0 else x + label_w + value_w * (index - 1)

        # Header band.
        y -= row_h
        page.rects.append(FilledRect(x, y, width, row_h, tbl.header_bg, radius=1.0))
        for i, heading in enumerate(columns):
            if not heading:
                continue
            tx = cell_x(i) + (4.0 if i == 0 else value_w - 4.0
                              - text_width(heading, typ.heading_font, size))
            page.lines.append(TextLine(
                heading, typ.heading_font, size, tbl.header_text, tx, y + 4.0 * state.spacing_scale
            ))

        # Data rows.
        for r, row in enumerate(table.rows[: tbl.max_rows]):
            y -= row_h
            if tbl.row_alt_bg and r % 2 == 1:
                page.rects.append(FilledRect(x, y, width, row_h, tbl.row_alt_bg))
            for i, cell in enumerate(row[: len(columns)]):
                if not cell:
                    continue
                font = typ.heading_font if (i == 0 and tbl.row_labels_bold) else typ.body_font
                if i == 0:
                    tx = cell_x(0) + 4.0
                else:
                    tx = cell_x(i) + value_w - 4.0 - text_width(cell, font, size)
                page.lines.append(TextLine(
                    cell, font, size, col.text, tx, y + 4.0 * state.spacing_scale
                ))

        return y

    def _draw_section(
        self, page: Page, block: SectionBlock, x: float, y: float,
        col_width: float, state: ScaleState, heading: bool = True,
    ) -> float:
        typ, col, sp = self.t.typography, self.t.colors, self.t.spacing
        heading_size = max(6.5, typ.heading_size * state.font_scale)
        body_size = max(self.min_body_pt, typ.body_size * state.font_scale)
        # Bullets are set smaller than body copy by design, but the legibility
        # floor applies to every text class: at the floor they converge rather
        # than the smaller class dropping underneath it.
        bullet_size = max(self.min_body_pt, typ.bullet_size * state.font_scale)
        leading = body_size * typ.line_height
        bullet_leading = bullet_size * typ.line_height

        if heading:
            label = block.heading.upper() if typ.heading_case == "upper" else block.heading
            y -= heading_size
            page.lines.append(TextLine(
                label, typ.heading_font, heading_size, col.heading, x, y,
                tracking=typ.heading_tracking,
            ))
            y -= sp.heading_gap * state.spacing_scale

        if block.style == "table" and block.table is not None:
            y = self._draw_table(page, block.table, x, y, col_width, state)
            if block.body:
                caption_size = max(5.8, typ.caption_size * state.font_scale)
                y -= 2.0 * state.spacing_scale
                for line in wrap_text(block.body, typ.body_font, caption_size, col_width):
                    y -= caption_size * 1.2
                    page.lines.append(
                        TextLine(line, typ.body_font, caption_size, col.muted, x, y)
                    )

        elif block.style in ("bullets", "numbered"):
            indent = sp.bullet_indent
            for index, item in enumerate(block.bullets, start=1):
                marker = f"{index}." if block.style == "numbered" else "–"
                marker_color = col.text if block.style == "numbered" else col.accent_secondary
                segments = self._rich_for(item, state)
                lines = wrap_rich(segments, bullet_size, col_width - indent)
                for i, line in enumerate(lines):
                    y -= bullet_leading
                    if i == 0:
                        page.lines.append(TextLine(
                            marker, typ.body_font, bullet_size, marker_color, x, y
                        ))
                    cursor = x + indent
                    for j, seg in enumerate(line):
                        page.lines.append(
                            TextLine(seg.text, seg.font, bullet_size, seg.color, cursor, y)
                        )
                        cursor += text_width(seg.text, seg.font, bullet_size)
                        if j < len(line) - 1:
                            cursor += text_width(" ", seg.font, bullet_size)
                y -= 1.2 * state.spacing_scale

        else:
            body = self._trim(block.body or "", state)
            y = self._draw_rich(
                page, self._rich_for(body, state), x, y, col_width, body_size, leading
            )

        return y - sp.section_gap * state.spacing_scale

    # -- footer -------------------------------------------------------------

    def _footer_height(self, state: ScaleState) -> float:
        if not self.t.footer.enabled:
            return 0.0
        return max(5.8, self.t.typography.footer_size * state.font_scale) + 10.0

    def _draw_footer(self, page: Page, summary: ExecutiveSummary, state: ScaleState) -> None:
        if not self.t.footer.enabled:
            return
        typ, col = self.t.typography, self.t.colors
        m = self.t.page.margins
        left = m["left"]
        right = self.t.page.width_pt - m["right"]
        size = max(5.8, typ.footer_size * state.font_scale)
        y = m["bottom"] * 0.62

        if self.t.footer.show_rule:
            page.rules.append(RuleLine(left, y + size + 4.5, right, col.rule, 0.5))

        text = summary.footer_note or self.t.footer.text
        for line in wrap_text(text, typ.body_font, size, right - left)[:2]:
            page.lines.append(TextLine(line, typ.body_font, size, col.muted, left, y))
            y -= size * 1.2

    # -- composition --------------------------------------------------------

    def compose(self, summary: ExecutiveSummary, state: ScaleState) -> Page:
        """Lay the whole page out at one rung of the ladder."""
        page = Page(
            width=self.t.page.width_pt,
            height=self.t.page.height_pt,
            used_scale=state.font_scale,
            body_size=max(self.min_body_pt, self.t.typography.body_size * state.font_scale),
        )
        m = self.t.page.margins
        _, col_width, xs = self._columns(state)
        bottom_limit = m["bottom"] + self._footer_height(state)

        blocks = build_blocks(summary, self.t)
        kept: list[SectionBlock] = []
        for b in blocks:
            # Masthead sections are drawn by _draw_masthead, not in the body flow.
            if b.column == 2 and self.t.masthead.enabled:
                continue
            if b.priority < state.drop_below_priority:
                page.dropped_sections.append(b.key)
            else:
                kept.append(b)

        y = self.t.page.height_pt - m["top"]

        if self.t.title_bar.enabled:
            y = self._draw_title_bar(page, summary, state, y)

        if self.t.masthead.enabled:
            y = self._draw_masthead(page, summary, state, y)
        else:
            y = self._draw_header(page, summary, state, y)

        # A full-width metric band reads better than a column-bound one.
        metric_block = next((b for b in kept if b.key == "metrics"), None)
        if metric_block is not None:
            y = self._draw_metric_band(page, summary.metrics, state, y)
            kept = [b for b in kept if b.key != "metrics"]

        body_top = y
        available = body_top - bottom_limit

        if self.t.columns <= 1:
            cursor = body_top
            for block in kept:
                cursor = self._draw_section(page, block, xs[0], cursor, col_width, state)
            page.overflow_pt = max(0.0, bottom_limit - cursor)
        else:
            assignment = self._balance(kept, col_width, state, available)
            cursors = [body_top] * len(xs)
            for block, col_index in assignment:
                idx = min(col_index, len(xs) - 1)
                cursors[idx] = self._draw_section(
                    page, block, xs[idx], cursors[idx], col_width, state
                )
            page.overflow_pt = max(0.0, bottom_limit - min(cursors))

        self._draw_footer(page, summary, state)
        return page

    def _balance(
        self, blocks: list[SectionBlock], col_width: float,
        state: ScaleState, available: float,
    ) -> list[tuple[SectionBlock, int]]:
        """Assign sections to columns, honouring the template then rebalancing.

        The reference's column hints are used first; if that leaves one column
        badly over-full, sections spill into the shorter column in order.
        """
        heights = {b.key: self._section_height(b, col_width, state) for b in blocks}
        totals = [0.0, 0.0]
        assignment: list[tuple[SectionBlock, int]] = []

        for block in blocks:
            preferred = 1 if block.column >= 1 else 0
            other = 1 - preferred
            h = heights[block.key]
            if totals[preferred] + h > available and totals[other] + h <= available:
                chosen = other
            elif totals[preferred] + h > available and totals[other] < totals[preferred]:
                chosen = other
            else:
                chosen = preferred
            totals[chosen] += h
            assignment.append((block, chosen))
        return assignment

    # -- the fitting ladder -------------------------------------------------

    def fit(self, summary: ExecutiveSummary, max_attempts: int = 8) -> Page:
        """Find the highest-quality composition that fits on one page.

        Escalation order (Step 9): shorten prose, drop low-priority sections,
        tighten spacing, and only then reduce type size.
        """
        ladder: list[ScaleState] = [
            ScaleState(1.00, 1.00, 1.00, 0),    # ideal
            ScaleState(1.00, 0.94, 0.92, 0),    # light trim
            ScaleState(1.00, 0.88, 0.85, 35),   # drop "information not provided"
            ScaleState(0.97, 0.84, 0.80, 45),   # drop risks
            ScaleState(0.95, 0.80, 0.74, 55),   # drop problem
            ScaleState(0.93, 0.76, 0.68, 60),   # drop solution detail
            ScaleState(0.90, 0.72, 0.62, 68),   # drop team narrative
            ScaleState(0.88, 0.68, 0.56, 75),   # keep only the core case
        ]
        ladder = ladder[: max(1, max_attempts)]

        best: Page | None = None
        for attempt, state in enumerate(ladder, start=1):
            # Never go below the legibility floor. The binding constraint is the
            # smallest body-class size in the template, which is usually the
            # bullet size rather than the paragraph size.
            smallest_class = min(
                self.t.typography.body_size, self.t.typography.bullet_size
            )
            if smallest_class * state.font_scale < self.min_body_pt:
                state = ScaleState(
                    font_scale=self.min_body_pt / smallest_class,
                    spacing_scale=state.spacing_scale,
                    trim_ratio=state.trim_ratio,
                    drop_below_priority=state.drop_below_priority,
                )

            page = self.compose(summary, state)
            if best is None or page.overflow_pt < best.overflow_pt:
                best = page
            if page.fits:
                if attempt > 1:
                    log.info(
                        "Content fitted on attempt %d (body %.1fpt, %.0f%% prose retained)",
                        attempt, page.body_size, state.trim_ratio * 100,
                    )
                page.notes.append(f"fitted_on_attempt={attempt}")
                return page
            log.debug(
                "Attempt %d overflows by %.1fpt; escalating", attempt, page.overflow_pt
            )

        assert best is not None
        log.warning(
            "Content still overflows by %.1fpt after %d attempts; "
            "trailing content will be dropped rather than clipped.",
            best.overflow_pt, len(ladder),
        )
        return self._hard_fit(summary, ladder[-1])

    def fit_multipage(
        self, summary: ExecutiveSummary, max_pages: int = 3
    ) -> list[Page]:
        """Flow the content across several pages instead of prioritising it.

        Used only when the caller explicitly permits more than one page. Sections
        are never truncated here — whatever does not fit continues on the next
        page, which is the whole point of relaxing the constraint.
        """
        state = ScaleState()
        first = self.compose(summary, state)
        if first.fits:
            return [first]

        remaining = summary.model_copy(deep=True)
        blocks = build_blocks(summary, self.t)
        pages: list[Page] = []
        placed: set[str] = set()

        for page_index in range(max_pages):
            # Keep only the sections not yet placed.
            working = summary.model_copy(deep=True)
            for block in blocks:
                if block.key in placed:
                    if block.key == "metrics":
                        working.metrics = []
                    elif block.key == "missing_material_information":
                        working.missing_material_information = []
                    else:
                        setattr(working, block.key, None)

            # Suppress the header on continuation pages.
            if page_index > 0:
                working.tagline = None
                working.meta_line = None

            page = self.compose(working, state)
            pages.append(page)

            newly_placed = [
                b.key for b in blocks
                if b.key not in placed and self._fits_on(b, working, page)
            ]
            if not newly_placed:
                break
            placed.update(newly_placed)
            if len(placed) >= len(blocks):
                break

        log.info("Multi-page layout produced %d page(s)", len(pages))
        return pages or [first]

    def _fits_on(self, block: SectionBlock, summary: ExecutiveSummary, page: Page) -> bool:
        """Whether a section's heading actually made it onto the composed page."""
        heading = block.heading.upper()
        return any(line.text.upper().startswith(heading[:18]) for line in page.lines)

    def _hard_fit(self, summary: ExecutiveSummary, state: ScaleState) -> Page:
        """Last resort: remove whole sections until the page fits.

        Content is never allowed to flow invisibly past the page edge; it is
        dropped explicitly and reported.
        """
        working = summary.model_copy(deep=True)
        removable = sorted(
            (k for k in SECTION_PRIORITY if k not in ("corporate_summary", "metrics")),
            key=lambda k: SECTION_PRIORITY[k],
        )
        dropped: list[str] = []
        page = self.compose(working, state)

        for key in removable:
            if page.fits:
                break
            if not hasattr(working, key) or not working.has_content(key):
                continue
            current = getattr(working, key, None)
            if isinstance(current, list):
                setattr(working, key, [])
            elif isinstance(current, FinancialTable):
                setattr(working, key, FinancialTable())
            else:
                setattr(working, key, None)
            dropped.append(key)
            page = self.compose(working, state)

        # If even that is not enough, truncate the lead paragraph.
        guard = 0
        while not page.fits and guard < 12:
            words = (working.corporate_summary or "").split()
            if len(words) <= 30:
                break
            working.corporate_summary = (
                " ".join(words[: int(len(words) * 0.85)]).rstrip(",;: ") + "."
            )
            page = self.compose(working, state)
            guard += 1

        page.dropped_sections = sorted(set(page.dropped_sections + dropped))
        page.notes.append("hard_fit_applied")
        if page.dropped_sections:
            log.warning(
                "Dropped to fit one page: %s", ", ".join(page.dropped_sections)
            )
        return page


def fit_word_budgets_to_page(
    template: TemplateSpec, metric_count: int, fill_target: float = 0.98
) -> TemplateSpec:
    """Scale per-section word budgets to the page's actual capacity.

    The reference supplies the *relative* weight of each section; the page
    geometry supplies the absolute amount of prose that will physically fit. Using
    the reference's raw word count instead would leave a short reference's layout
    two-thirds empty and force a dense one into the shrink ladder immediately.
    """
    t = template.model_copy(deep=True)
    typ, sp = t.typography, t.spacing
    m = t.page.margins

    content_w = t.page.width_pt - m["left"] - m["right"]
    columns = max(1, t.columns)
    col_w = (content_w - sp.column_gap * (columns - 1)) / columns

    # Average glyph width for the body font, measured rather than assumed.
    sample = "the company reports revenue growth across customers and markets"
    avg_char_w = text_width(sample, typ.body_font, typ.body_size) / len(sample)

    def words_that_fit(width: float, height: float, count: int) -> int:
        """Words of body copy that fit in a region, net of section headings."""
        leading = typ.body_size * typ.line_height
        overhead = (typ.heading_size + sp.heading_gap + sp.section_gap) * count
        usable = height - overhead
        if usable <= leading:
            return 0
        words_per_line = (width / max(avg_char_w, 0.1)) / 6.2  # ~5.2-char words + space
        return max(0, int((usable / leading) * words_per_line * fill_target))

    def distribute(specs: list, capacity: int) -> None:
        if not specs or capacity <= 0:
            return
        weights = {s.key: max(s.approx_words, 1) for s in specs}
        total = sum(weights.values())
        for spec in specs:
            spec.approx_words = max(24, int(capacity * weights[spec.key] / total))

    # Vertical space consumed by furniture rather than prose.
    header_h = typ.title_size + typ.tagline_size * 2 + typ.footer_size + sp.section_gap * 2 + 16
    metrics_h = (typ.metric_value_size + typ.metric_label_size + 12 + sp.section_gap) if metric_count else 0
    footer_h = typ.footer_size + 12

    flowing = [s for s in t.sections if s.style in ("paragraph", "bullets", "numbered")]
    if not flowing:
        return t

    masthead_sections = [s for s in flowing if s.column == 2]
    body_sections = [s for s in flowing if s.column != 2]

    # -- masthead region ----------------------------------------------------
    #
    # The sidebar is fixed content, so its height sets the height of the
    # narrative column beside it. That column is budgeted on its own; it does
    # not compete with the body flow below.
    masthead_h = 0.0
    if t.masthead.enabled:
        # Sidebar rows are mostly single-line; contact runs to about four lines
        # and the gaps note to about three. Over-estimating here silently starves
        # the body budget and leaves the lower third of the page empty.
        rows = len(t.masthead.sidebar_fields)
        masthead_h = (
            typ.masthead_name_size + typ.tagline_size * 2.4
            + rows * typ.sidebar_value_size * 1.45
            + (4.0 * typ.sidebar_value_size * 1.2 if t.masthead.show_contact else 0)
            + (3.5 * typ.note_size * 1.2 if t.masthead.show_not_stated else 0)
            + sp.section_gap * 2
        )
        side_w = content_w * t.masthead.sidebar_width_ratio
        main_w = content_w - side_w - sp.column_gap
        distribute(
            masthead_sections,
            words_that_fit(main_w, masthead_h, len(masthead_sections)),
        )

    # -- table regions ------------------------------------------------------
    # A table's height comes from its row count, not from a word budget.
    # Reserve for a typical table (header plus four metric rows) rather than the
    # configured maximum: decks rarely state every row, and over-reserving here
    # shrinks the body budget for a table that never materialises.
    table_h = 0.0
    typical_rows = min(t.table.max_rows, 4) + 1
    for spec in t.sections:
        if spec.style == "table":
            table_h += (
                typical_rows * (typ.table_size + 5.0)
                + typ.caption_size * 2.4
                + typ.heading_size + sp.heading_gap + sp.section_gap
            )

    # -- body region --------------------------------------------------------
    top_block = masthead_h if t.masthead.enabled else header_h
    if t.title_bar.enabled:
        top_block += typ.title_bar_size + sp.section_gap

    body_h = (
        t.page.height_pt - m["top"] - m["bottom"]
        - top_block - metrics_h - footer_h - table_h
    )
    if body_h <= 0:
        return t

    capacity = words_that_fit(col_w, body_h, len(body_sections) / columns) * columns
    distribute(body_sections, capacity)

    t.total_body_words = capacity + sum(s.approx_words for s in masthead_sections)
    log.info(
        "Page capacity: ~%d words of body copy across %d column(s)%s",
        capacity, columns,
        f", plus ~{sum(s.approx_words for s in masthead_sections)} in the masthead"
        if masthead_sections else "",
    )
    return t


def summarise_page(page: Page) -> dict[str, object]:
    """Compact layout stats for the analysis JSON."""
    return {
        "fits_one_page": page.fits,
        "overflow_pt": round(page.overflow_pt, 2),
        "body_font_pt": round(page.body_size, 2),
        "font_scale": round(page.used_scale, 3),
        "text_lines": len(page.lines),
        "dropped_sections": page.dropped_sections,
        "notes": page.notes,
    }
