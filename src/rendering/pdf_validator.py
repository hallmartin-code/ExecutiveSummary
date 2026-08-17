"""Technical and visual validation of the generated PDF.

The generated file is reopened and inspected as an independent artefact rather
than trusting the layout engine's own accounting: page count, text-layer presence,
bounds, overlap, font size, and a rendered-pixel check of the margins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf
from pydantic import BaseModel, Field

from ..analysis.reference_analyzer import TemplateSpec
from ..utils.logging import get_logger

log = get_logger("validator")


class ValidationIssue(BaseModel):
    check: str
    severity: str  # "critical" | "warning"
    detail: str


class ValidationResult(BaseModel):
    passed: bool = True
    page_count: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)

    @property
    def critical(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "critical"]

    def add(self, check: str, severity: str, detail: str) -> None:
        self.issues.append(ValidationIssue(check=check, severity=severity, detail=detail))
        if severity == "critical":
            self.passed = False


def _overlap_area(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    inter = a & b
    return abs(inter.get_area()) if not inter.is_empty else 0.0


def validate_one_page_pdf(
    pdf_path: str | Path,
    template: TemplateSpec | None = None,
    min_font_pt: float = 8.0,
    visual_check: bool = True,
    dpi: int = 150,
    max_pages: int = 1,
) -> ValidationResult:
    """Verify the deliverable: page count, no clipping, no overlap, legible type.

    *max_pages* is 1 for the default one-page contract; a caller that has
    explicitly opted into a longer document raises it.
    """
    result = ValidationResult()
    path = Path(pdf_path)

    if not path.exists():
        result.add("file_exists", "critical", f"Output not found: {path}")
        return result

    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:
        result.add("readable", "critical", f"Generated PDF cannot be opened: {exc}")
        return result

    try:
        result.page_count = doc.page_count

        # 1. Page count.
        if doc.page_count == 0:
            result.add("page_count", "critical", "The generated PDF has no pages.")
            return result
        if doc.page_count > max_pages:
            result.add(
                "page_count", "critical",
                f"Expected at most {max_pages} page(s), found {doc.page_count}.",
            )

        page = doc[0]
        page_rect = page.rect

        # 2. Selectable text layer.
        text = page.get_text().strip()
        if len(text) < 120:
            result.add(
                "text_layer", "critical",
                f"Only {len(text)} characters of selectable text; the page may be rasterised.",
            )

        spans: list[dict[str, Any]] = []
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if (span.get("text") or "").strip():
                        spans.append(span)

        if not spans:
            result.add("text_spans", "critical", "No text spans found on the page.")
            return result

        # 3. Nothing outside the page.
        tolerance = 1.5
        out_of_bounds = []
        for span in spans:
            r = pymupdf.Rect(span["bbox"])
            if (
                r.x0 < -tolerance or r.y0 < -tolerance
                or r.x1 > page_rect.width + tolerance
                or r.y1 > page_rect.height + tolerance
            ):
                out_of_bounds.append((span.get("text", "")[:40], tuple(round(v, 1) for v in r)))
        if out_of_bounds:
            result.add(
                "bounds", "critical",
                f"{len(out_of_bounds)} text span(s) fall outside the page: "
                f"{out_of_bounds[:3]}",
            )

        # 4. Margins respected (a soft check: inference can be slightly off).
        if template is not None:
            m = template.page.margins
            slack = 6.0
            violations = [
                (span.get("text", "")[:30], round(pymupdf.Rect(span["bbox"]).x0, 1))
                for span in spans
                if pymupdf.Rect(span["bbox"]).x0 < m["left"] - slack
                or pymupdf.Rect(span["bbox"]).x1 > page_rect.width - m["right"] + slack
            ]
            if violations:
                result.add(
                    "margins", "warning",
                    f"{len(violations)} span(s) encroach on the margins: {violations[:3]}",
                )

        # 5. Legible font sizes.
        #
        # Metric labels, the header meta strip, and the footer are deliberately
        # set smaller than body copy, so the floor is applied to the dominant
        # body size (weighted by characters set) rather than to the global
        # minimum. A separate absolute floor catches genuinely unreadable type.
        sizes = [round(float(s.get("size", 0)), 2) for s in spans]
        smallest = min(sizes) if sizes else 0.0

        chars_by_size: dict[float, int] = {}
        for span in spans:
            size = round(float(span.get("size", 0)), 2)
            chars_by_size[size] = chars_by_size.get(size, 0) + len(
                (span.get("text") or "").strip()
            )
        body_size = max(chars_by_size, key=lambda s: chars_by_size[s]) if chars_by_size else 0.0

        if body_size < min_font_pt:
            result.add(
                "font_size", "critical",
                f"Dominant body font is {body_size}pt, below the {min_font_pt}pt floor.",
            )

        ABSOLUTE_FLOOR = 5.5
        unreadable = sorted({s for s in sizes if s < ABSOLUTE_FLOOR})
        if unreadable:
            result.add(
                "font_size_minimum", "critical",
                f"Text set below {ABSOLUTE_FLOOR}pt is unreadable: {unreadable}pt.",
            )
        elif smallest < 6.2:
            result.add(
                "font_size_small", "warning",
                f"Smallest type on the page is {smallest}pt (labels and footer).",
            )
        smallest_body = body_size

        # 6. Overlapping text.
        # Compare only spans on distinct baselines; same-line spans legitimately abut.
        overlaps: list[tuple[str, str]] = []
        rects = [(pymupdf.Rect(s["bbox"]), (s.get("text") or "")[:24]) for s in spans]
        rects.sort(key=lambda pair: (pair[0].y0, pair[0].x0))
        for i in range(len(rects)):
            r1, t1 = rects[i]
            for j in range(i + 1, min(i + 14, len(rects))):
                r2, t2 = rects[j]
                if r2.y0 > r1.y1:
                    break
                if abs(r1.y0 - r2.y0) < 0.8:  # same baseline
                    continue
                area = _overlap_area(r1, r2)
                smaller = min(abs(r1.get_area()), abs(r2.get_area())) or 1.0
                if area / smaller > 0.32:
                    overlaps.append((t1, t2))
        if overlaps:
            result.add(
                "overlap", "critical",
                f"{len(overlaps)} overlapping text pair(s), e.g. {overlaps[:3]}",
            )

        # 7. Footer present and inside the page.
        footer_spans = [
            s for s in spans
            if pymupdf.Rect(s["bbox"]).y1 > page_rect.height * 0.90
        ]
        if template is not None and template.footer.enabled and not footer_spans:
            result.add("footer", "warning", "Footer text was not found on the rendered page.")

        # 8. Visual inspection of the rendered pixels.
        if visual_check:
            visual = _visual_inspection(page, template, dpi)
            result.stats.update(visual)
            if visual.get("content_touches_edge"):
                result.add(
                    "visual_bleed", "critical",
                    "Rendered content reaches the physical page edge (clipping risk).",
                )
            ink = visual.get("ink_coverage", 0.0)
            if ink > 0.62:
                result.add(
                    "density", "warning",
                    f"Page is visually dense (ink coverage {ink:.0%}); whitespace is tight.",
                )
            elif ink < 0.06:
                result.add(
                    "density", "warning",
                    f"Page is very sparse (ink coverage {ink:.0%}); layout may look unbalanced.",
                )

        result.stats.update(
            {
                "page_width_pt": round(page_rect.width, 1),
                "page_height_pt": round(page_rect.height, 1),
                "text_characters": len(text),
                "text_spans": len(spans),
                "smallest_font_pt": smallest,
                "smallest_body_font_pt": smallest_body,
                "largest_font_pt": max(sizes) if sizes else 0.0,
            }
        )
    finally:
        doc.close()

    if result.passed:
        log.info("PDF validation passed (%d page, %d spans)",
                 result.page_count, result.stats.get("text_spans", 0))
    else:
        for issue in result.critical:
            log.error("Validation failure [%s]: %s", issue.check, issue.detail)
    for issue in result.issues:
        if issue.severity == "warning":
            log.warning("Validation warning [%s]: %s", issue.check, issue.detail)
    return result


def _visual_inspection(
    page: pymupdf.Page, template: TemplateSpec | None, dpi: int
) -> dict[str, Any]:
    """Rasterise the page and measure the actual inked region."""
    stats: dict[str, Any] = {}
    try:
        scale = dpi / 72.0
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    except Exception as exc:  # pragma: no cover
        log.debug("Visual inspection skipped: %s", exc)
        return {"visual_check": "skipped"}

    width, height, n = pix.width, pix.height, pix.n
    samples = pix.samples

    # Scan on a grid; full per-pixel scanning is needlessly slow at 150 dpi.
    step = 2
    min_x, min_y, max_x, max_y = width, height, -1, -1
    inked = 0
    total = 0
    for y in range(0, height, step):
        row = y * pix.stride
        for x in range(0, width, step):
            idx = row + x * n
            # Treat anything meaningfully darker than white as ink.
            if samples[idx] < 232 or samples[idx + 1] < 232 or samples[idx + 2] < 232:
                inked += 1
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
            total += 1

    if max_x < 0:
        return {"visual_check": "blank_page", "ink_coverage": 0.0}

    stats["visual_check"] = "ok"
    stats["ink_coverage"] = round(inked / max(total, 1), 4)
    stats["content_bbox_px"] = [min_x, min_y, max_x, max_y]
    stats["render_size_px"] = [width, height]

    edge_margin_px = max(4, int(6 * dpi / 72.0))
    stats["content_touches_edge"] = bool(
        min_x < edge_margin_px
        or min_y < edge_margin_px
        or max_x > width - edge_margin_px
        or max_y > height - edge_margin_px
    )

    if template is not None:
        m = template.page.margins
        stats["margin_actual_pt"] = {
            "left": round(min_x / dpi * 72.0, 1),
            "top": round(min_y / dpi * 72.0, 1),
            "right": round((width - max_x) / dpi * 72.0, 1),
            "bottom": round((height - max_y) / dpi * 72.0, 1),
        }
        stats["margin_expected_pt"] = m
    return stats


def render_preview_png(pdf_path: str | Path, png_path: str | Path, dpi: int = 130) -> Path | None:
    """Write a PNG preview of page 1 (for the UI and for visual inspection)."""
    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:
        log.warning("Could not open PDF for preview: %s", exc)
        return None
    try:
        if doc.page_count == 0:
            return None
        scale = dpi / 72.0
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        out = Path(png_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out))
        return out
    except Exception as exc:  # pragma: no cover
        log.warning("Preview rendering failed: %s", exc)
        return None
    finally:
        doc.close()
