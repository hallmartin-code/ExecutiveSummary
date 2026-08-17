"""PDF deck ingestion."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ingestion import load_deck
from src.ingestion.pdf_parser import DeckParseError, parse_pdf_deck
from src.utils.files import InputValidationError


def test_parses_every_page(sample_pdf_deck: Path) -> None:
    deck = parse_pdf_deck(sample_pdf_deck)
    assert deck.source_format == "pdf"
    assert deck.slide_count == 9
    assert len(deck.slides) == 9
    assert [s.slide for s in deck.slides] == list(range(1, 10))


def test_titles_are_inferred_from_font_size(sample_pdf_deck: Path) -> None:
    deck = parse_pdf_deck(sample_pdf_deck)
    titles = [s.title for s in deck.slides]
    assert "Problem" in titles
    assert "Traction" in titles


def test_slide_provenance_is_preserved(sample_pdf_deck: Path) -> None:
    deck = parse_pdf_deck(sample_pdf_deck)
    traction = next(s for s in deck.slides if s.title == "Traction")
    assert "ARR $3.2M" in traction.all_text()
    # Every metric carries the slide it came from.
    assert all(m.slide == traction.slide for m in traction.metrics)


def test_quantitative_metrics_are_detected(sample_pdf_deck: Path) -> None:
    deck = parse_pdf_deck(sample_pdf_deck)
    values = {m.value.replace(" ", "") for s in deck.slides for m in s.metrics}
    assert any("3.2M" in v for v in values)
    assert any("74%" in v for v in values)
    assert deck.total_metrics() > 5


def test_visual_density_flags_are_computed(sample_pdf_deck: Path) -> None:
    deck = parse_pdf_deck(sample_pdf_deck)
    assert all(s.visual_density >= 0 for s in deck.slides)
    assert all(isinstance(s.needs_visual_pass, bool) for s in deck.slides)


def test_loader_dispatches_by_extension(sample_pdf_deck: Path) -> None:
    assert load_deck(sample_pdf_deck).source_format == "pdf"


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InputValidationError):
        load_deck(tmp_path / "nope.pdf")


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    with pytest.raises(InputValidationError):
        load_deck(empty)


def test_file_with_wrong_content_is_rejected(tmp_path: Path) -> None:
    """Extension checks alone are not enough; content is sniffed too."""
    fake = tmp_path / "not_really.pdf"
    fake.write_bytes(b"this is plain text, not a PDF")
    with pytest.raises(InputValidationError, match="does not look like"):
        load_deck(fake)


def test_unsupported_extension_is_rejected(tmp_path: Path) -> None:
    other = tmp_path / "deck.docx"
    other.write_bytes(b"PK\x03\x04whatever")
    with pytest.raises(InputValidationError, match="Unsupported"):
        load_deck(other)


def test_malformed_pdf_raises_parse_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    # Valid magic bytes but a corrupt body.
    broken.write_bytes(b"%PDF-1.7\n" + b"\x00" * 400)
    with pytest.raises(DeckParseError):
        parse_pdf_deck(broken)
