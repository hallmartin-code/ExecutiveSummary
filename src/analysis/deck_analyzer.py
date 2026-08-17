"""Turn a parsed deck into structured, evidence-tagged investor data.

Two paths converge here: an AI structuring pass (when a key is configured) and a
deterministic heuristic pass. Both emit the same :class:`InvestorData` shape, and
both are validated against the evidence ledger before anything downstream sees them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..config import PipelineConfig
from ..ingestion.document import DeckDocument, SlideContent
from ..ingestion.slide_renderer import ocr_image, render_slides
from ..utils.files import temp_workspace
from ..utils.logging import get_logger
from .evidence import Classification, Evidence, EvidenceLedger
from .investor_schema import (
    Fact,
    InvestorData,
    SourceRecord,
    TeamMember,
)

log = get_logger("deck_analyzer")


# ---------------------------------------------------------------------------
# Vision pass
# ---------------------------------------------------------------------------


def run_visual_pass(
    deck: DeckDocument, config: PipelineConfig, provider: Any
) -> int:
    """Read visually dense slides with a multimodal model (or OCR fallback).

    Returns the number of slides successfully enriched.
    """
    targets = deck.slides_needing_vision(budget=config.vision_slide_budget)
    if not targets:
        log.info("No slides flagged for a visual pass")
        return 0

    slide_numbers = [s.slide for s in targets]
    log.info(
        "%d slide(s) flagged as visually dense: %s",
        len(slide_numbers),
        ", ".join(str(n) for n in slide_numbers),
    )

    enriched = 0
    with temp_workspace("execsummary_render_") as workdir:
        images = render_slides(
            deck.source_path, deck.source_format, slide_numbers, workdir,
            dpi=config.render.render_dpi,
        )
        if not images:
            log.warning("Slides could not be rasterised; visual content will not be read")
            return 0

        by_number = {s.slide: s for s in deck.slides}
        use_ai = config.ai_available and config.use_vision and getattr(provider, "available", False)

        for image in images:
            slide = by_number.get(image.slide)
            if slide is None:
                continue

            if use_ai:
                try:
                    result = provider.extract_slide(
                        image.slide, image.to_base64(), slide.native_only_text()
                    )
                except Exception as exc:
                    log.warning("Vision pass failed on slide %s: %s", image.slide, exc)
                    result = {}

                text_parts: list[str] = []
                if result.get("text"):
                    text_parts.append(str(result["text"]))
                if result.get("visual_description"):
                    text_parts.append(str(result["visual_description"]))

                # Only keep chart metrics the model was confident it could read.
                for m in result.get("metrics") or []:
                    if not isinstance(m, dict):
                        continue
                    if float(m.get("confidence", 0) or 0) < 0.6:
                        continue
                    name = str(m.get("name") or "").strip()
                    value = str(m.get("value") or "").strip()
                    if value:
                        text_parts.append(f"{name}: {value}" if name else value)

                if text_parts:
                    slide.vision_text = "\n".join(text_parts)
                    slide.vision_confidence = float(result.get("confidence", 0.7) or 0.7)
                    enriched += 1
                    continue

            # OCR is the fallback, and only when it is actually installed.
            ocr = ocr_image(image)
            if ocr:
                slide.vision_text = ocr
                slide.vision_confidence = 0.5
                enriched += 1
                log.debug("Slide %s enriched via OCR fallback", image.slide)

    log.info("Visual pass enriched %d slide(s)", enriched)
    return enriched


# ---------------------------------------------------------------------------
# Ledger construction
# ---------------------------------------------------------------------------


def build_ledger(deck: DeckDocument) -> EvidenceLedger:
    """Index every number the deck contains so generated prose can be checked."""
    ledger = EvidenceLedger()
    for slide in deck.slides:
        ledger.index_source_text(slide.slide, slide.all_text())
    return ledger


# ---------------------------------------------------------------------------
# AI structuring
# ---------------------------------------------------------------------------

_LIST_FIELDS = {
    ("customers", "named_customers"),
    ("traction", "partnerships"),
    ("traction", "regulatory_milestones"),
    ("traction", "validation_evidence"),
    ("financial_metrics", "revenue_history"),
    ("financial_metrics", "projections"),
    ("competitive_advantage", "competitors"),
    ("intellectual_property", "patents"),
    ("go_to_market", "channels"),
    ("go_to_market", "distribution_partners"),
    ("use_of_funds", "line_items"),
    ("milestones", "upcoming"),
    ("milestones", "achieved"),
    ("risks", "identified"),
    ("risks", "mitigations"),
    ("growth_metrics", "derived"),
}

_TEAM_FIELDS = {"founders", "executives", "advisors"}


def _fact_from_payload(payload: Any, ledger: EvidenceLedger, max_slide: int) -> Fact | None:
    """Build a validated :class:`Fact`, rejecting ungrounded numbers."""
    if payload is None:
        return None
    if isinstance(payload, (str, int, float)):
        payload = {"value": str(payload)}
    if not isinstance(payload, dict):
        return None

    value = payload.get("value")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()

    slides = []
    for s in payload.get("source_slides") or []:
        try:
            n = int(s)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= max_slide and n not in slides:
            slides.append(n)

    original = payload.get("original_text")
    original = re.sub(r"\s+", " ", str(original)).strip() if original else None

    confidence = payload.get("confidence", 0.85)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.85

    # Hallucination gate: any number in the value must exist in the deck.
    check = ledger.verify_text(value)
    if not check.grounded:
        log.warning(
            "Rejected ungrounded value %r (numbers not in deck: %s)",
            value[:70], ", ".join(check.ungrounded_numbers),
        )
        return None

    if not slides:
        slides = check.matched_slides[:3]
    if not slides:
        confidence = min(confidence, 0.6)

    return Fact(
        value=value,
        source_slides=sorted(slides),
        classification=Classification.EXPLICIT,
        confidence=max(0.0, min(1.0, confidence)),
        original_text=original,
    )


def _team_member(payload: Any, max_slide: int) -> TeamMember | None:
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 80:
        return None
    slides = []
    for s in payload.get("source_slides") or []:
        try:
            n = int(s)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= max_slide:
            slides.append(n)

    def _clean(key: str, limit: int) -> str | None:
        v = payload.get(key)
        if not v:
            return None
        return re.sub(r"\s+", " ", str(v)).strip()[:limit] or None

    return TeamMember(
        name=name,
        role=_clean("role", 60),
        credentials=_clean("credentials", 160),
        commitment=_clean("commitment", 40),
        source_slides=sorted(set(slides)),
    )


def apply_ai_structure(
    data: InvestorData, payload: dict[str, Any], ledger: EvidenceLedger, max_slide: int
) -> int:
    """Merge an AI structuring response into *data*. Returns facts accepted."""
    accepted = 0
    for section_name, section_payload in (payload or {}).items():
        section = getattr(data, section_name, None)
        if section is None or not isinstance(section_payload, dict):
            continue

        for field_name, field_payload in section_payload.items():
            if not hasattr(section, field_name):
                continue

            if section_name == "team" and field_name in _TEAM_FIELDS:
                members = [
                    m for m in (
                        _team_member(item, max_slide)
                        for item in (field_payload or [])
                    ) if m is not None
                ]
                if members:
                    setattr(section, field_name, members)
                    accepted += len(members)
                continue

            if (section_name, field_name) in _LIST_FIELDS:
                items = field_payload if isinstance(field_payload, list) else [field_payload]
                facts = [
                    f for f in (
                        _fact_from_payload(item, ledger, max_slide) for item in items
                    ) if f is not None
                ]
                if facts:
                    setattr(section, field_name, facts)
                    accepted += len(facts)
                continue

            fact = _fact_from_payload(field_payload, ledger, max_slide)
            if fact is not None:
                setattr(section, field_name, fact)
                accepted += 1

    return accepted


# ---------------------------------------------------------------------------
# Heuristic structuring (no AI)
# ---------------------------------------------------------------------------

_TOPIC_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("problem", ("problem", "challenge", "pain", "why now", "status quo", "today")),
    ("solution", ("solution", "how it works", "product", "our approach", "technology", "platform")),
    ("market", ("market", "tam", "sam", "som", "opportunity", "market size")),
    ("business_model", ("business model", "revenue model", "pricing", "unit economics", "monetiz")),
    ("traction", ("traction", "milestone", "validation", "progress", "customers", "pilot", "results")),
    ("competition", ("competition", "competitive", "landscape", "alternatives", "versus")),
    ("team", ("team", "leadership", "founders", "advisory", "management")),
    ("ip", ("intellectual property", "patent", " ip ", "ip -", "proprietary")),
    ("financials", ("financial", "projections", "p&l", "revenue forecast", "model")),
    ("fundraising", ("raise", "raising", "the ask", "deal terms", "investment", "funding", "use of funds")),
    ("gtm", ("go to market", "go-to-market", "gtm", "distribution", "sales strategy", "channel")),
    ("regulatory", ("regulatory", "fda", "510(k)", "ce mark", "clinical", "approval pathway")),
]


def _classify_slide(slide: SlideContent) -> list[str]:
    text = f"{slide.title or ''}\n{slide.content}".lower()
    return [topic for topic, keys in _TOPIC_PATTERNS if any(k in text for k in keys)]


def _strip_slide_furniture(text: str) -> str:
    """Drop slide numbers and other non-content lines from extracted slide text.

    Deck exports routinely leave a bare page number on its own line, which would
    otherwise be spliced into generated prose as if it were a figure.
    """
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"\d{1,3}[.)]?", line):        # bare slide number
            continue
        if re.fullmatch(r"[•▪◦o·\-–—*]+", line):       # orphaned bullet glyph
            continue
        lines.append(line)
    return "\n".join(lines)


def _looks_like_prose(text: str) -> bool:
    """Reject slide label soup — headings, axis labels, and metric captions.

    Deck slides are laid out visually, so joined text runs often read as
    ``"MSRP $30 Distributor $24 Cost of Goods Sold $3"``. That is data, not a
    sentence, and splicing it into a summary produces unreadable prose.
    """
    words = text.split()
    if len(words) < 7:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    # Mostly-uppercase text is a heading or a label row.
    if sum(1 for c in letters if c.isupper()) / len(letters) > 0.35:
        return False
    # Real sentences carry function words.
    functional = {"the", "a", "an", "is", "are", "of", "to", "in", "for", "with",
                  "that", "and", "on", "by", "from", "as", "can", "has", "have",
                  "because", "which", "their", "our", "its", "it"}
    if sum(1 for w in words if w.lower().strip(".,;:") in functional) < 2:
        return False
    # A dense run of numbers is a metric table, not prose.
    numeric = sum(1 for w in words if re.search(r"\d", w))
    return numeric / len(words) <= 0.28


def _first_sentence(text: str, max_chars: int = 220, require_prose: bool = True) -> str:
    """Take the first well-formed sentence, ignoring slide furniture."""
    cleaned = re.sub(r"\s+", " ", _strip_slide_furniture(text)).strip()
    if not cleaned:
        return ""
    # Strip a leading slide number that survived line joining ("15 RAISING $2M").
    cleaned = re.sub(r"^\d{1,3}\s+(?=[A-Za-z])", "", cleaned)

    candidates: list[str] = []
    match = re.search(r"^(.{20,%d}?[.!?])(?:\s|$)" % max_chars, cleaned)
    if match:
        candidates.append(match.group(1).strip())
    candidates.append(cleaned[:max_chars].rsplit(" ", 1)[0].strip())

    for candidate in candidates:
        if not require_prose or _looks_like_prose(candidate):
            return candidate
    return ""


_EMAIL_RE = re.compile(r"[\w.+-]+@([A-Za-z0-9-]{2,})\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(?:https?://)?(?:www\.)?([A-Za-z0-9-]{3,})\.(?:com|io|ai|co|net|org|health|bio)\b")

_GENERIC_DOMAINS = {
    "gmail", "outlook", "hotmail", "yahoo", "icloud", "proton", "protonmail",
    "me", "live", "aol", "msn", "example",
}


def _infer_company_name(deck: DeckDocument) -> tuple[str | None, int, float]:
    """Best-effort company name, preferring evidence over the title slide."""
    # 1. Non-generic email domain (contact slides are highly reliable).
    for slide in deck.slides:
        for domain in _EMAIL_RE.findall(slide.all_text()):
            if domain.lower() not in _GENERIC_DOMAINS and 2 < len(domain) <= 40:
                return domain, slide.slide, 0.9

    # 2. A website URL.
    for slide in deck.slides:
        for domain in _URL_RE.findall(slide.all_text()):
            if domain.lower() not in _GENERIC_DOMAINS and 2 < len(domain) <= 40:
                return domain, slide.slide, 0.8

    # 3. Document metadata, minus any trailing descriptor.
    candidate = (deck.metadata.get("title") or "").strip()
    candidate = re.split(r"\s+[-–|]\s+|:\s+", candidate)[0].strip()
    if 2 <= len(candidate) <= 45 and not candidate.lower().endswith((".pdf", ".pptx")):
        return candidate, 1, 0.55

    # 4. The title slide, as a last resort.
    if deck.slides and deck.slides[0].title:
        first = re.split(r"\s+[-–|]\s+|:\s+", deck.slides[0].title)[0].strip()
        if 2 <= len(first) <= 45:
            return first, 1, 0.45

    return None, 1, 0.0


def _mk_fact(value: str, slides: list[int], confidence: float = 0.75) -> Fact:
    return Fact(
        value=re.sub(r"\s+", " ", value).strip(),
        source_slides=sorted(set(slides)),
        classification=Classification.EXPLICIT,
        confidence=confidence,
        original_text=value.strip()[:300],
    )


def heuristic_structure(deck: DeckDocument, data: InvestorData) -> int:
    """Populate the schema without an LLM, using slide topics and metric context.

    Deliberately conservative: it fills only fields it can attribute to a topic-matched
    slide, so a missing key produces a thinner summary rather than a wrong one.
    """
    accepted = 0
    topics: dict[str, list[SlideContent]] = {}
    for slide in deck.slides:
        for topic in _classify_slide(slide):
            topics.setdefault(topic, []).append(slide)

    def fill(target_section: str, field: str, slides: list[SlideContent], max_chars: int = 240) -> None:
        nonlocal accepted
        section = getattr(data, target_section)
        if getattr(section, field).present or not slides:
            return
        for slide in slides:
            text = _first_sentence(slide.content or slide.all_text(), max_chars)
            if len(text) >= 25:
                setattr(section, field, _mk_fact(text, [slide.slide]))
                accepted += 1
                return

    # Company name: an email or web domain in the deck is far more reliable than
    # the title slide, which usually carries a marketing headline instead.
    if not data.company.company_name.present:
        name, slide_no, confidence = _infer_company_name(deck)
        if name:
            data.company.company_name = _mk_fact(name, [slide_no], confidence)
            accepted += 1

    if not data.company.tagline.present and deck.slides:
        first = deck.slides[0]
        # The title slide's headline is a legitimate tagline even though it is
        # not a sentence, so the prose filter is relaxed here.
        line = _first_sentence(first.title or first.content or "", 90, require_prose=False)
        if 10 <= len(line) <= 90:
            data.company.tagline = _mk_fact(line, [1], 0.6)
            accepted += 1

    fill("problem", "customer_problem", topics.get("problem", []), 260)
    fill("solution", "product_service", topics.get("solution", []), 260)
    fill("solution", "how_it_works", topics.get("solution", [])[1:], 220)
    fill("business_model", "revenue_model", topics.get("business_model", []), 220)
    fill("competitive_advantage", "differentiation", topics.get("competition", []), 220)
    fill("go_to_market", "strategy", topics.get("gtm", []), 200)
    fill("technology", "regulatory_pathway", topics.get("regulatory", []), 200)
    fill("intellectual_property", "patent_status", topics.get("ip", []), 200)
    fill("fundraising", "terms", topics.get("fundraising", []), 200)

    # Market sizes: pull the labelled TAM/SAM/SOM tokens.
    for slide in topics.get("market", []):
        text = slide.all_text()
        for label, field in (("tam", "tam"), ("sam", "sam"), ("som", "som")):
            if getattr(data.market, field).present:
                continue
            match = re.search(
                rf"\b{label}\b[^\n]{{0,60}}?(\$\s?\d[\d,.]*\s?[KMB]?)", text, re.IGNORECASE
            )
            if match:
                setattr(data.market, field, _mk_fact(match.group(1).strip(), [slide.slide], 0.8))
                accepted += 1

    # Fundraising amount from "raising $X" phrasing anywhere in the deck.
    if not data.fundraising.raise_amount.present:
        for slide in deck.slides:
            match = re.search(
                r"rais(?:e|ing)\s+(?:up to\s+)?(\$\s?\d[\d,.]*\s?[KMB]?)",
                slide.all_text(), re.IGNORECASE,
            )
            if match:
                data.fundraising.raise_amount = _mk_fact(
                    match.group(1).strip(), [slide.slide], 0.85
                )
                accepted += 1
                break

    log.info("Heuristic structuring populated %d fields", accepted)
    return accepted


# ---------------------------------------------------------------------------
# Missing-information detection
# ---------------------------------------------------------------------------

_MATERIAL_FIELDS: list[tuple[str, str, str]] = [
    ("traction", "revenue", "Revenue or ARR"),
    ("customers", "customer_count", "Customer count"),
    ("business_model", "gross_margin", "Gross margin"),
    ("financial_metrics", "cac", "Customer acquisition cost (CAC)"),
    ("financial_metrics", "ltv", "Lifetime value (LTV)"),
    ("financial_metrics", "churn", "Churn or retention"),
    ("financial_metrics", "runway", "Runway"),
    ("financial_metrics", "burn_rate", "Burn rate"),
    ("fundraising", "valuation", "Valuation or cap"),
    ("fundraising", "lead_investor", "Named lead investor"),
    ("fundraising", "capital_raised_to_date", "Capital raised to date"),
    ("market", "methodology", "Market-sizing methodology"),
    ("use_of_funds", "summary", "Use of proceeds"),
]


def detect_missing_information(data: InvestorData) -> list[str]:
    """List investor-material fields the deck does not cover."""
    missing: list[str] = []
    for section_name, field_name, label in _MATERIAL_FIELDS:
        section = getattr(data, section_name, None)
        if section is None:
            continue
        fact = getattr(section, field_name, None)
        if isinstance(fact, Fact) and not fact.present:
            missing.append(label)

    if not data.traction.arr.present and not data.traction.revenue.present:
        if "Revenue or ARR" not in missing:
            missing.append("Revenue or ARR")
    if not data.team.founders and not data.team.executives:
        missing.append("Named leadership team")
    if not data.use_of_funds.summary.present and not data.use_of_funds.line_items:
        if "Use of proceeds" not in missing:
            missing.append("Use of proceeds")

    for item in missing:
        data.missing_information.add(item)
    log.info("%d investor-material field(s) not provided by the deck", len(missing))
    return missing


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def analyze_deck(
    deck: DeckDocument, config: PipelineConfig, provider: Any
) -> tuple[InvestorData, EvidenceLedger]:
    """Full deck -> :class:`InvestorData` pipeline with evidence controls."""
    log.info("Parsing %d-slide pitch deck", deck.slide_count)

    run_visual_pass(deck, config, provider)
    ledger = build_ledger(deck)

    data = InvestorData()
    accepted = 0

    if config.ai_available and getattr(provider, "available", False):
        payload = {
            "text": deck.full_text()[:180000],
            "metrics": [
                {"value": m.value, "name": m.name, "slide": m.slide}
                for s in deck.slides for m in s.metrics
            ],
        }
        try:
            structured = provider.structure_investor_data(payload)
            accepted = apply_ai_structure(data, structured, ledger, deck.slide_count)
            log.info("AI structuring accepted %d grounded facts", accepted)
        except Exception as exc:
            log.warning("AI structuring failed (%s); falling back to heuristics", exc)

    if accepted < 8:
        if accepted:
            log.info("Supplementing sparse AI output with heuristic extraction")
        accepted += heuristic_structure(deck, data)

    if config.company_name_override:
        data.company.company_name = Fact(
            value=config.company_name_override.strip(),
            source_slides=[],
            classification=Classification.EXPLICIT,
            confidence=1.0,
            original_text="User-supplied company name override",
        )

    # Provenance record for every slide that contributed a fact.
    used: dict[int, set[str]] = {}
    for path, fact in data.iter_facts():
        if not fact.present:
            continue
        for slide_no in fact.source_slides:
            used.setdefault(slide_no, set()).add(path.split("[")[0])
    for member_group in (data.team.founders, data.team.executives, data.team.advisors):
        for member in member_group:
            for slide_no in member.source_slides:
                used.setdefault(slide_no, set()).add("team")

    titles = {s.slide: s.title for s in deck.slides}
    data.sources = [
        SourceRecord(slide=n, title=titles.get(n), used_for=sorted(fields))
        for n, fields in sorted(used.items())
    ]

    # Register every accepted fact in the ledger for the QA pass.
    for path, fact in data.iter_facts():
        if not fact.present:
            continue
        ledger.add(
            Evidence(
                claim=f"{path}: {fact.value}",
                value=fact.value,
                source_slides=fact.source_slides,
                classification=fact.classification,
                confidence=fact.confidence,
                original_text=fact.original_text,
                field_path=path,
            )
        )

    log.info(
        "Extracted %d investor-relevant facts across %d slides",
        data.present_fact_count(), len(data.sources),
    )
    log.info("Extracted %d quantitative metrics", deck.total_metrics())
    return data, ledger


def deck_debug_payload(deck: DeckDocument) -> dict[str, Any]:
    """Slide-level JSON written to *_extracted_data.json* for debugging."""
    return {
        "source_path": str(Path(deck.source_path).name),
        "source_format": deck.source_format,
        "slide_count": deck.slide_count,
        "metadata": deck.metadata,
        "slides": [
            {
                "slide": s.slide,
                "title": s.title,
                "content": s.content,
                "bullets": s.bullets,
                "tables": [t.rows for t in s.tables],
                "chart_labels": s.chart_labels,
                "image_captions": s.image_captions,
                "speaker_notes": s.speaker_notes,
                "metrics": [
                    {"name": m.name, "value": m.value, "context": m.context}
                    for m in s.metrics
                ],
                "image_count": s.image_count,
                "native_text_chars": s.native_text_chars,
                "visual_density": s.visual_density,
                "needs_visual_pass": s.needs_visual_pass,
                "vision_text": s.vision_text,
                "vision_confidence": s.vision_confidence,
            }
            for s in deck.slides
        ],
    }
