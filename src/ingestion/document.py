"""Slide-level document model shared by the PDF and PPTX parsers."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


class SlideMetric(BaseModel):
    """A quantitative token found on a slide, with its immediate context."""

    name: str | None = None
    value: str
    context: str | None = None
    slide: int


class SlideTable(BaseModel):
    rows: list[list[str]] = Field(default_factory=list)
    slide: int

    def to_text(self) -> str:
        return "\n".join(" | ".join(c for c in row if c) for row in self.rows if any(row))


class SlideContent(BaseModel):
    """Everything extracted from a single slide or PDF page."""

    slide: int
    title: str | None = None
    content: str = ""
    bullets: list[str] = Field(default_factory=list)
    tables: list[SlideTable] = Field(default_factory=list)
    chart_labels: list[str] = Field(default_factory=list)
    image_captions: list[str] = Field(default_factory=list)
    speaker_notes: str | None = None
    metrics: list[SlideMetric] = Field(default_factory=list)

    # Visual-density signals used to decide whether to send the slide to vision.
    image_count: int = 0
    native_text_chars: int = 0
    visual_density: float = 0.0
    needs_visual_pass: bool = False

    # Filled in later by the vision pass, kept separate from native text.
    vision_text: str | None = None
    vision_confidence: float = 0.0

    def all_text(self) -> str:
        """Every piece of text associated with the slide, native and visual."""
        parts: list[str] = []
        if self.title:
            parts.append(self.title)
        if self.content:
            parts.append(self.content)
        parts.extend(self.bullets)
        parts.extend(self.chart_labels)
        parts.extend(self.image_captions)
        for t in self.tables:
            parts.append(t.to_text())
        if self.speaker_notes:
            parts.append(self.speaker_notes)
        if self.vision_text:
            parts.append(self.vision_text)
        return "\n".join(p for p in parts if p)

    def native_only_text(self) -> str:
        """Text excluding the vision pass — used for confidence weighting."""
        parts = [self.title or "", self.content, *self.bullets, *self.chart_labels]
        for t in self.tables:
            parts.append(t.to_text())
        if self.speaker_notes:
            parts.append(self.speaker_notes)
        return "\n".join(p for p in parts if p)


class DeckDocument(BaseModel):
    """A parsed pitch deck."""

    source_path: str
    source_format: str  # "pdf" | "pptx"
    slide_count: int
    slides: list[SlideContent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def full_text(self) -> str:
        return "\n\n".join(
            f"--- SLIDE {s.slide} ---\n{s.all_text()}" for s in self.slides
        )

    def slides_needing_vision(self, budget: int = 12) -> list[SlideContent]:
        """Pick the most visually dense slides, up to *budget*."""
        candidates = [s for s in self.slides if s.needs_visual_pass]
        candidates.sort(key=lambda s: s.visual_density, reverse=True)
        chosen = candidates[:budget]
        return sorted(chosen, key=lambda s: s.slide)

    def total_metrics(self) -> int:
        return sum(len(s.metrics) for s in self.slides)


# ---------------------------------------------------------------------------
# Shared text utilities
# ---------------------------------------------------------------------------

_METRIC_RE = re.compile(
    r"""
    (?:\$\s?\d[\d,]*(?:\.\d+)?\s?(?:[KMB]\b|thousand|million|billion)?)   # money
    | (?:\d[\d,]*(?:\.\d+)?\s?%)                                          # percent
    | (?:\d[\d,]*(?:\.\d+)?\s?[xX]\b)                                     # multiples
    | (?:\d{1,3}(?:,\d{3})+)                                              # big counts
    """,
    re.VERBOSE,
)

_BULLET_PREFIX = re.compile(r"^\s*(?:[•▪◦o·\-–—*]|\d+[.)]|[a-z][.)])\s+", re.IGNORECASE)


def clean_text(text: str) -> str:
    """Normalise whitespace and unicode punctuation from extracted text."""
    if not text:
        return ""
    t = text.replace(" ", " ").replace("﻿", "")
    t = t.replace("’", "'").replace("‘", "'")
    t = t.replace("“", '"').replace("”", '"')
    t = t.replace("–", "-").replace("—", "-")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def strip_bullet(line: str) -> str:
    return _BULLET_PREFIX.sub("", line).strip()


def split_bullets(text: str) -> list[str]:
    """Pull bullet-like lines out of a text block."""
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _BULLET_PREFIX.match(line):
            cleaned = strip_bullet(line)
            if len(cleaned) > 2:
                out.append(cleaned)
    return out


def find_metrics(text: str, slide: int, max_metrics: int = 40) -> list[SlideMetric]:
    """Locate quantitative tokens and capture the words around them."""
    metrics: list[SlideMetric] = []
    seen: set[tuple[str, str]] = set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        for match in _METRIC_RE.finditer(line):
            value = match.group(0).strip()
            # Context = the label nearest the number on the same line.
            before = line[: match.start()].strip()
            after = line[match.end():].strip()
            label = before[-60:] if before else after[:60]
            key = (value, label)
            if key in seen:
                continue
            seen.add(key)
            metrics.append(
                SlideMetric(
                    name=label.strip(" :-–—|") or None,
                    value=value,
                    context=line[:200],
                    slide=slide,
                )
            )
            if len(metrics) >= max_metrics:
                return metrics
    return metrics


def looks_like_title(text: str) -> bool:
    """Heuristic: short, mostly-uppercase or title-cased line."""
    t = text.strip()
    if not t or len(t) > 90 or "\n" in t:
        return False
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    return upper_ratio > 0.6 or t.istitle()
