"""Deck ingestion: PDF and PowerPoint parsers plus slide rasterisation."""

from .document import DeckDocument, SlideContent, SlideMetric, SlideTable
from .loader import load_deck

__all__ = ["DeckDocument", "SlideContent", "SlideMetric", "SlideTable", "load_deck"]
