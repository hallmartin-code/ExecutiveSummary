"""Provider-agnostic AI interface.

Every provider returns plain dictionaries so the rest of the pipeline never
depends on a vendor SDK type. :class:`NullProvider` implements the same surface
with no network calls, which is what keeps the app fully functional (in
deterministic mode) without an API key.
"""

from __future__ import annotations

import abc
from typing import Any

from ..utils.logging import get_logger

log = get_logger("ai")


class AIProvider(abc.ABC):
    """Interface implemented by every AI backend."""

    name: str = "base"

    @abc.abstractmethod
    def analyze_reference(self, measured_spec: Any, reference_text: str) -> Any | None:
        """Refine a measured :class:`TemplateSpec`. Design only — never facts."""

    @abc.abstractmethod
    def extract_slide(self, slide_number: int, image_b64: str, native_text: str) -> dict[str, Any]:
        """Read a rendered slide image and return its visible text and metrics."""

    @abc.abstractmethod
    def structure_investor_data(self, deck_payload: dict[str, Any]) -> dict[str, Any]:
        """Map slide-level content into the investor schema shape."""

    @abc.abstractmethod
    def generate_summary(
        self, investor_payload: dict[str, Any], template_payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Write the executive-summary prose."""

    @abc.abstractmethod
    def review_summary(
        self, summary_payload: dict[str, Any], evidence_payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Quality-control pass over the drafted summary."""

    @property
    def available(self) -> bool:
        return True


class NullProvider(AIProvider):
    """No-op provider used when AI is disabled or no key is configured."""

    name = "none"

    def analyze_reference(self, measured_spec: Any, reference_text: str) -> Any | None:
        return None

    def extract_slide(self, slide_number: int, image_b64: str, native_text: str) -> dict[str, Any]:
        return {"slide": slide_number, "text": "", "metrics": [], "confidence": 0.0}

    def structure_investor_data(self, deck_payload: dict[str, Any]) -> dict[str, Any]:
        return {}

    def generate_summary(
        self, investor_payload: dict[str, Any], template_payload: dict[str, Any]
    ) -> dict[str, Any]:
        return {}

    def review_summary(
        self, summary_payload: dict[str, Any], evidence_payload: dict[str, Any]
    ) -> dict[str, Any]:
        return {"passed": True, "issues": [], "warnings": [], "revisions_applied": []}

    @property
    def available(self) -> bool:
        return False


def get_provider(
    provider: str = "anthropic", model: str | None = None, api_key: str | None = None
) -> AIProvider:
    """Instantiate a provider by name, degrading to :class:`NullProvider`.

    Add a new backend by implementing :class:`AIProvider` and registering it here.
    """
    key = (provider or "").strip().lower()
    if key in ("", "none", "null", "off"):
        return NullProvider()
    if key in ("anthropic", "claude"):
        from .anthropic_provider import AnthropicProvider

        try:
            return AnthropicProvider(model=model, api_key=api_key)
        except Exception as exc:
            log.warning("Anthropic provider unavailable (%s); running deterministically.", exc)
            return NullProvider()
    log.warning("Unknown AI provider '%s'; running deterministically.", provider)
    return NullProvider()
