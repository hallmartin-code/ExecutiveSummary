"""Format dispatch for deck loading."""

from __future__ import annotations

from pathlib import Path

from ..utils.files import validate_input_file
from .document import DeckDocument
from .pdf_parser import DeckParseError, parse_pdf_deck
from .pptx_parser import parse_pptx_deck


def load_deck(path: str | Path) -> DeckDocument:
    """Validate and parse a pitch deck in either supported format."""
    validated = validate_input_file(path, kind="deck")
    suffix = validated.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf_deck(validated)
    if suffix == ".pptx":
        return parse_pptx_deck(validated)
    raise DeckParseError(f"Unsupported deck format: {suffix}")
