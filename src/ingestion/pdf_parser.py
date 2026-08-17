"""PDF pitch-deck parsing via PyMuPDF.

Slide titles are inferred from font size rather than position, because deck
layouts vary too much for a fixed top-of-page rule to be reliable.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from ..config import MAX_DECK_PAGES
from ..utils.logging import get_logger
from .document import (
    DeckDocument,
    SlideContent,
    SlideTable,
    clean_text,
    find_metrics,
    split_bullets,
)

log = get_logger("pdf_parser")


class DeckParseError(RuntimeError):
    """Raised when a deck cannot be opened or contains no usable pages."""


def _spans(page: pymupdf.Page) -> list[dict]:
    """Flatten the text dict into a list of spans with size and position."""
    out: list[dict] = []
    try:
        data = page.get_text("dict")
    except Exception as exc:  # pragma: no cover - corrupt page
        log.debug("Span extraction failed on page %s: %s", page.number, exc)
        return out
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = clean_text(span.get("text", ""))
                if not text:
                    continue
                out.append(
                    {
                        "text": text,
                        "size": round(float(span.get("size", 0)), 2),
                        "font": span.get("font", ""),
                        "bbox": span.get("bbox", (0, 0, 0, 0)),
                        "flags": span.get("flags", 0),
                    }
                )
    return out


def _infer_title(spans: list[dict], page_height: float) -> str | None:
    """The largest-font span in the upper 45% of the page, if it reads like a title."""
    if not spans:
        return None
    upper = [s for s in spans if s["bbox"][1] < page_height * 0.45]
    pool = upper or spans
    max_size = max(s["size"] for s in pool)
    if max_size <= 0:
        return None
    candidates = [s for s in pool if s["size"] >= max_size - 0.6]
    candidates.sort(key=lambda s: (s["bbox"][1], s["bbox"][0]))
    title = " ".join(s["text"] for s in candidates[:3]).strip()
    # Reject slide numbers and other noise.
    if not title or len(title) > 120 or title.replace(".", "").isdigit():
        return None
    return title


def _extract_tables(page: pymupdf.Page, slide_no: int) -> list[SlideTable]:
    """Use PyMuPDF's table finder when available; failures are non-fatal."""
    tables: list[SlideTable] = []
    try:
        finder = page.find_tables()
    except Exception as exc:  # pragma: no cover - version differences
        log.debug("Table detection unavailable on page %s: %s", slide_no, exc)
        return tables
    for table in getattr(finder, "tables", []) or []:
        try:
            rows = table.extract()
        except Exception:
            continue
        cleaned = [
            [clean_text(str(cell)) if cell is not None else "" for cell in row]
            for row in rows
        ]
        cleaned = [r for r in cleaned if any(c for c in r)]
        if len(cleaned) >= 2:
            tables.append(SlideTable(rows=cleaned, slide=slide_no))
    return tables


def _visual_density(page: pymupdf.Page, text_chars: int, image_count: int) -> float:
    """Fraction of the page plausibly occupied by non-text content.

    A slide that is mostly a chart yields a high score and gets routed to the
    vision pass; a text-heavy slide does not.
    """
    page_area = max(page.rect.get_area(), 1.0)
    image_area = 0.0
    try:
        for info in page.get_image_info():
            bbox = info.get("bbox")
            if bbox:
                rect = pymupdf.Rect(bbox)
                image_area += abs(rect.get_area())
    except Exception:  # pragma: no cover
        image_area = image_count * page_area * 0.1

    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    vector_area = 0.0
    for d in drawings:
        rect = d.get("rect")
        if rect is not None:
            vector_area += abs(pymupdf.Rect(rect).get_area())

    covered = min(image_area + vector_area * 0.5, page_area * 3)
    coverage = covered / page_area

    # Sparse text on a busy page is the strongest signal of a visual slide.
    text_sparsity = 1.0 - min(text_chars / 700.0, 1.0)
    return round(min(coverage * 0.55 + text_sparsity * 0.65, 2.0), 3)


def parse_pdf_deck(path: str | Path) -> DeckDocument:
    """Parse a PDF pitch deck into slide-level content."""
    p = Path(path)
    try:
        doc = pymupdf.open(p)
    except Exception as exc:
        raise DeckParseError(f"Could not open PDF '{p.name}': {exc}") from exc

    if doc.page_count == 0:
        doc.close()
        raise DeckParseError(f"'{p.name}' contains no pages.")
    if doc.page_count > MAX_DECK_PAGES:
        doc.close()
        raise DeckParseError(
            f"'{p.name}' has {doc.page_count} pages, above the {MAX_DECK_PAGES}-page limit."
        )

    slides: list[SlideContent] = []
    try:
        for index in range(doc.page_count):
            page = doc[index]
            slide_no = index + 1
            spans = _spans(page)
            raw_text = clean_text(page.get_text())
            title = _infer_title(spans, page.rect.height)

            body = raw_text
            if title:
                # Remove the title line from the body so it is not duplicated.
                for line in title.split("\n"):
                    body = body.replace(line, "", 1)
                body = clean_text(body)

            image_count = len(page.get_images(full=True))
            density = _visual_density(page, len(raw_text), image_count)
            tables = _extract_tables(page, slide_no)

            # Short spans that are not part of a sentence are usually chart labels.
            chart_labels = [
                s["text"]
                for s in spans
                if len(s["text"]) <= 28 and not s["text"].endswith((".", ":"))
            ][:40]

            slides.append(
                SlideContent(
                    slide=slide_no,
                    title=title,
                    content=body,
                    bullets=split_bullets(raw_text),
                    tables=tables,
                    chart_labels=chart_labels,
                    metrics=find_metrics(raw_text, slide_no),
                    image_count=image_count,
                    native_text_chars=len(raw_text),
                    visual_density=density,
                    needs_visual_pass=(len(raw_text) < 420 and image_count > 0)
                    or density > 0.85,
                )
            )
        metadata = {k: v for k, v in (doc.metadata or {}).items() if v}
    finally:
        doc.close()

    log.info("Parsed %d-slide PDF deck: %s", len(slides), p.name)
    return DeckDocument(
        source_path=str(p),
        source_format="pdf",
        slide_count=len(slides),
        slides=slides,
        metadata=metadata,
    )
