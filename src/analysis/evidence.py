"""Evidence classification and grounding controls.

Every factual claim carried through the pipeline is wrapped in an :class:`Evidence`
record tagged EXPLICIT, DERIVED, or NOT_PROVIDED. The :class:`EvidenceLedger`
then acts as the single gate that any generated sentence must pass before it can
reach the PDF.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Iterable, Sequence

from pydantic import BaseModel, Field, field_validator


class Classification(str, Enum):
    """How a claim relates to the source deck."""

    EXPLICIT = "EXPLICIT"        # stated verbatim in the deck
    DERIVED = "DERIVED"          # computed in Python from explicit deck values
    NOT_PROVIDED = "NOT_PROVIDED"  # absent from the deck


class Evidence(BaseModel):
    """A single traceable claim."""

    claim: str
    value: str | None = None
    source_slides: list[int] = Field(default_factory=list)
    classification: Classification = Classification.EXPLICIT
    confidence: float = 0.9
    original_text: str | None = Field(
        default=None, description="Verbatim deck wording that supports the claim."
    )
    calculation: str | None = Field(
        default=None, description="Arithmetic used, for DERIVED claims only."
    )
    field_path: str | None = Field(
        default=None, description="Investor-schema field this evidence populates."
    )

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @field_validator("source_slides")
    @classmethod
    def _clean_slides(cls, v: list[int]) -> list[int]:
        out: list[int] = []
        for s in v or []:
            try:
                n = int(s)
            except (TypeError, ValueError):
                continue
            if n > 0 and n not in out:
                out.append(n)
        return sorted(out)

    @property
    def is_grounded(self) -> bool:
        return (
            self.classification in (Classification.EXPLICIT, Classification.DERIVED)
            and bool(self.source_slides)
        )


# ---------------------------------------------------------------------------
# Number grounding
# ---------------------------------------------------------------------------

# Matches $2.4M, 87%, 190,600, 12.5x, $20.4 billion, 510(k) etc.
_NUMBER_RE = re.compile(
    r"""
    (?<![\w/])                         # not mid-identifier
    \$?\s?
    \d{1,3}(?:,\d{3})+(?:\.\d+)?       # 1,234,567
    | \$?\s?\d+(?:\.\d+)?\s?%          # 12.5%
    # A scale suffix must not be the first letter of the next word: "$3 Margin"
    # is three dollars, not three million.
    | \$\s?\d+(?:\.\d+)?\s?(?:[KMB](?![A-Za-z])|thousand|million|billion)?(?![A-Za-z])
    | \b\d+(?:\.\d+)?\s?(?:[KMB](?![A-Za-z])|[xX](?![A-Za-z]))
    | \b\d{4}\b                        # years
    | \b\d+(?:\.\d+)?\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

_SCALE_FACTORS = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "t": 1e12, "trillion": 1e12,
}

_MAGNITUDE_RE = re.compile(
    r"^(\d+(?:\.\d+)?)(k|m|mm|b|bn|t|thousand|million|billion|trillion)?$"
)

# Tokens that are structural rather than factual claims.
_NUMERIC_STOPWORDS = {
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
    "one", "two", "three", "first", "second",
}


def normalise_number(token: str) -> str:
    """Reduce a numeric token to a comparable canonical form.

    ``"$2.4M"``, ``"2.4 M"``, and ``"$ 2.4m"`` all collapse to ``"2.4m"``.
    """
    t = token.lower().strip()
    t = t.replace("$", "").replace(",", "").replace(" ", "")
    t = t.replace("thousand", "k").replace("million", "m").replace("billion", "b")
    t = t.rstrip(".")
    # Drop a trailing ".0" so "50" and "50.0" match.
    m = re.match(r"^(\d+)\.0+([a-z%x]*)$", t)
    if m:
        t = m.group(1) + m.group(2)
    return t


def token_magnitude(token: str) -> float | None:
    """Numeric magnitude of a normalised token, so ``750k`` == ``750,000``.

    Percentages and multiples are excluded: ``50%`` and ``50`` are different
    claims and must not be treated as the same evidence.
    """
    t = normalise_number(token)
    if not t or t.endswith("%") or t.endswith("x"):
        return None
    match = _MAGNITUDE_RE.match(t)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    scale = match.group(2)
    return value * _SCALE_FACTORS.get(scale, 1.0) if scale else value


def extract_numbers(text: str) -> list[str]:
    """Return the normalised numeric tokens present in *text*."""
    found: list[str] = []
    for match in _NUMBER_RE.finditer(text or ""):
        norm = normalise_number(match.group(0))
        if norm and norm not in _NUMERIC_STOPWORDS and norm not in found:
            found.append(norm)
    return found


class GroundingResult(BaseModel):
    """Outcome of checking one generated sentence against the ledger."""

    text: str
    grounded: bool
    ungrounded_numbers: list[str] = Field(default_factory=list)
    matched_slides: list[int] = Field(default_factory=list)


class EvidenceLedger:
    """The authoritative record of what the deck actually says.

    ``verify_text`` is the hallucination gate: any number appearing in generated
    prose must also appear somewhere in the source corpus, otherwise the sentence
    is rejected before it can be rendered.
    """

    def __init__(self) -> None:
        self._records: list[Evidence] = []
        self._corpus_numbers: set[str] = set()
        self._number_slides: dict[str, list[int]] = {}
        # Magnitudes let "$750K" in prose match "$750,000" in the deck.
        self._magnitudes: dict[float, list[int]] = {}
        self._corpus_text: str = ""

    # -- population ---------------------------------------------------------

    def _register(self, token: str, slide_number: int) -> None:
        self._corpus_numbers.add(token)
        slides = self._number_slides.setdefault(token, [])
        if slide_number and slide_number not in slides:
            slides.append(slide_number)
        magnitude = token_magnitude(token)
        if magnitude is not None:
            mag_slides = self._magnitudes.setdefault(magnitude, [])
            if slide_number and slide_number not in mag_slides:
                mag_slides.append(slide_number)

    def index_source_text(self, slide_number: int, text: str) -> None:
        """Register raw deck text so its numbers count as grounded."""
        if not text:
            return
        self._corpus_text += "\n" + text
        for num in extract_numbers(text):
            self._register(num, slide_number)

    def add(self, evidence: Evidence) -> Evidence:
        self._records.append(evidence)
        # DERIVED values are legitimate even though they never appear in the deck.
        if evidence.classification is Classification.DERIVED and evidence.value:
            for num in extract_numbers(evidence.value):
                for slide in evidence.source_slides or [0]:
                    self._register(num, slide)
        return evidence

    def _magnitude_match(self, token: str) -> list[int] | None:
        """Find a deck magnitude equal to *token* within rounding tolerance."""
        magnitude = token_magnitude(token)
        if magnitude is None:
            return None
        if magnitude in self._magnitudes:
            return self._magnitudes[magnitude]
        # Allow for rounding in the deck's own presentation ($2.43M vs $2,430K).
        tolerance = max(abs(magnitude) * 0.005, 0.001)
        for known, slides in self._magnitudes.items():
            if abs(known - magnitude) <= tolerance:
                return slides
        return None

    def extend(self, records: Iterable[Evidence]) -> None:
        for r in records:
            self.add(r)

    # -- inspection ---------------------------------------------------------

    @property
    def records(self) -> list[Evidence]:
        return list(self._records)

    def by_classification(self, c: Classification) -> list[Evidence]:
        return [r for r in self._records if r.classification is c]

    def slides_for_number(self, token: str) -> list[int]:
        return sorted(self._number_slides.get(normalise_number(token), []))

    def known_number(self, token: str) -> bool:
        return (
            normalise_number(token) in self._corpus_numbers
            or self._magnitude_match(normalise_number(token)) is not None
        )

    # -- the gate -----------------------------------------------------------

    def verify_text(self, text: str, extra_allowed: Sequence[str] = ()) -> GroundingResult:
        """Check that every number in *text* traces back to the deck."""
        allowed = {normalise_number(t) for t in extra_allowed}
        ungrounded: list[str] = []
        matched: list[int] = []
        for num in extract_numbers(text or ""):
            if num in self._corpus_numbers or num in allowed:
                for s in self._number_slides.get(num, []):
                    if s not in matched:
                        matched.append(s)
                continue

            # The same quantity written at a different scale is still grounded.
            mag_slides = self._magnitude_match(num)
            if mag_slides is not None:
                for s in mag_slides:
                    if s not in matched:
                        matched.append(s)
                continue

            ungrounded.append(num)
        return GroundingResult(
            text=text,
            grounded=not ungrounded,
            ungrounded_numbers=ungrounded,
            matched_slides=sorted(matched),
        )

    def source_slides_for_text(self, text: str) -> list[int]:
        """Best-effort provenance for a generated sentence."""
        return self.verify_text(text).matched_slides

    def summary(self) -> dict[str, int]:
        return {
            "total_claims": len(self._records),
            "explicit": len(self.by_classification(Classification.EXPLICIT)),
            "derived": len(self.by_classification(Classification.DERIVED)),
            "not_provided": len(self.by_classification(Classification.NOT_PROVIDED)),
            "indexed_numbers": len(self._corpus_numbers),
        }
