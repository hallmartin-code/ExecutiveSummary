"""Anthropic (Claude) provider.

The API key is read from ``ANTHROPIC_API_KEY`` and is never logged. All responses
are parsed defensively: a malformed model reply degrades to an empty result rather
than propagating garbage into the summary.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from ..utils.logging import get_logger
from .base import AIProvider
from .prompts import (
    QA_PROMPT,
    REFERENCE_PROMPT,
    SLIDE_VISION_PROMPT,
    STRUCTURE_PROMPT,
    SUMMARY_PROMPT,
)

log = get_logger("anthropic")

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0


def _repair_truncated_json(text: str) -> dict[str, Any]:
    """Recover as much as possible from a reply cut off by the token limit.

    A large structured extraction can exhaust ``max_tokens`` mid-object. Closing
    the open containers salvages every complete field instead of discarding the
    whole response — the incomplete trailing field is dropped, which is the safe
    outcome because partial values would fail the evidence gate anyway.
    """
    start = text.find("{")
    if start == -1:
        return {}
    body = text[start:]

    depth_stack: list[str] = []
    in_string = False
    escape = False
    last_safe = -1

    for i, ch in enumerate(body):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            depth_stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if depth_stack:
                depth_stack.pop()
        elif ch == "," and len(depth_stack) <= 2:
            # A comma at shallow depth is a clean truncation point.
            last_safe = i

    if in_string or last_safe == -1:
        # Fall back to the last complete top-level entry.
        last_safe = max(body.rfind("},"), body.rfind("],"))
        if last_safe == -1:
            return {}
        last_safe += 1

    candidate = body[:last_safe]
    # Re-derive the containers still open at the truncation point.
    depth_stack = []
    in_string = False
    escape = False
    for ch in candidate:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            depth_stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and depth_stack:
            depth_stack.pop()

    repaired = candidate.rstrip().rstrip(",") + "".join(reversed(depth_stack))
    try:
        parsed = json.loads(repaired)
        if isinstance(parsed, dict):
            log.warning(
                "Model reply was truncated; recovered %d top-level section(s) from it.",
                len(parsed),
            )
            return parsed
    except json.JSONDecodeError:
        pass
    return {}


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply."""
    if not text:
        return {}
    cleaned = text.strip()

    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    # Fall back to brace matching, which survives leading prose.
    start = cleaned.find("{")
    if start == -1:
        return {}
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(cleaned[start : i + 1])
                    return parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    break

    return _repair_truncated_json(cleaned)


class AnthropicProvider(AIProvider):
    """Claude-backed implementation of :class:`AIProvider`."""

    name = "anthropic"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("The 'anthropic' package is not installed.") from exc

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key or not key.strip():
            raise RuntimeError("ANTHROPIC_API_KEY is not set in the environment.")

        self.model = model or DEFAULT_MODEL
        self._client = Anthropic(api_key=key.strip())
        self.call_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        log.info("AI provider ready: anthropic / %s", self.model)

    # -- transport ----------------------------------------------------------

    def _call(
        self,
        system: str,
        content: list[dict[str, Any]] | str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        """One message round-trip with retry on transient failures."""
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                    messages=[{"role": "user", "content": blocks}],
                )
                self.call_count += 1
                usage = getattr(response, "usage", None)
                if usage:
                    self.input_tokens += getattr(usage, "input_tokens", 0) or 0
                    self.output_tokens += getattr(usage, "output_tokens", 0) or 0
                return "".join(
                    block.text for block in response.content
                    if getattr(block, "type", "") == "text"
                )
            except Exception as exc:
                last_error = exc
                name = type(exc).__name__
                transient = any(
                    marker in name
                    for marker in ("RateLimit", "Overloaded", "APIConnection", "Timeout", "InternalServer")
                )
                if transient and attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning("%s from API; retrying in %.0fs", name, delay)
                    time.sleep(delay)
                    continue
                break

        # Message text is redacted by the logging filter before it is emitted.
        log.error("AI call failed after %d attempts: %s", MAX_RETRIES, last_error)
        raise RuntimeError(f"Anthropic API call failed: {type(last_error).__name__}")

    # -- AIProvider ---------------------------------------------------------

    def analyze_reference(self, measured_spec: Any, reference_text: str) -> Any | None:
        from ..analysis.reference_analyzer import TemplateSpec

        payload = (
            "MEASURED TEMPLATE JSON:\n"
            + json.dumps(measured_spec.model_dump(mode="json"), indent=2)
            + "\n\nREFERENCE PLAIN TEXT (design cues only, facts are irrelevant):\n"
            + reference_text[:6000]
        )
        raw = self._call(REFERENCE_PROMPT, payload, max_tokens=4096)
        data = _extract_json(raw)
        if not data:
            return None
        try:
            return TemplateSpec.model_validate(data)
        except Exception as exc:
            log.warning("AI returned an invalid template spec (%s); keeping measured spec.", exc)
            return None

    def extract_slide(self, slide_number: int, image_b64: str, native_text: str) -> dict[str, Any]:
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": image_b64},
            },
            {
                "type": "text",
                "text": (
                    f"Slide {slide_number}.\n\n"
                    f"Native text already extracted from this slide:\n"
                    f"{(native_text or '(none)')[:2000]}\n\n"
                    "Report what is visible in the image, prioritising content that the "
                    "native text layer missed (charts, diagrams, tables, graphics)."
                ),
            },
        ]
        try:
            raw = self._call(SLIDE_VISION_PROMPT, content, max_tokens=2000)
        except RuntimeError:
            return {"slide": slide_number, "text": "", "metrics": [], "confidence": 0.0}
        data = _extract_json(raw)
        data.setdefault("slide", slide_number)
        data.setdefault("text", "")
        data.setdefault("metrics", [])
        data.setdefault("confidence", 0.0)
        return data

    def structure_investor_data(self, deck_payload: dict[str, Any]) -> dict[str, Any]:
        payload = (
            "PITCH DECK CONTENT (the only source of truth):\n\n"
            + deck_payload.get("text", "")
            + "\n\nQUANTITATIVE TOKENS DETECTED (value, slide):\n"
            + json.dumps(deck_payload.get("metrics", [])[:220], indent=1)
        )
        # The investor schema is wide; a tight cap truncates the reply mid-object.
        raw = self._call(STRUCTURE_PROMPT, payload, max_tokens=16000)
        data = _extract_json(raw)
        if not data:
            log.warning(
                "Could not parse the structuring reply (%d characters); "
                "falling back to heuristic extraction.", len(raw or ""),
            )
        return data

    def generate_summary(
        self, investor_payload: dict[str, Any], template_payload: dict[str, Any]
    ) -> dict[str, Any]:
        payload = (
            "STRUCTURED INVESTOR DATA (the only facts you may use):\n"
            + json.dumps(investor_payload, indent=1, default=str)[:60000]
            + "\n\nSECTIONS TO WRITE, WITH WORD BUDGETS:\n"
            + json.dumps(template_payload, indent=1)
        )
        raw = self._call(SUMMARY_PROMPT, payload, max_tokens=4000, temperature=0.1)
        return _extract_json(raw)

    def review_summary(
        self, summary_payload: dict[str, Any], evidence_payload: dict[str, Any]
    ) -> dict[str, Any]:
        payload = (
            "DRAFTED EXECUTIVE SUMMARY:\n"
            + json.dumps(summary_payload, indent=1, default=str)
            + "\n\nEVIDENCE LEDGER FROM THE DECK:\n"
            + json.dumps(evidence_payload, indent=1, default=str)[:50000]
        )
        try:
            raw = self._call(QA_PROMPT, payload, max_tokens=3000)
        except RuntimeError:
            return {"passed": True, "issues": [], "warnings": ["QA pass unavailable."],
                    "revisions": {}}
        data = _extract_json(raw)
        data.setdefault("passed", True)
        data.setdefault("issues", [])
        data.setdefault("warnings", [])
        data.setdefault("revisions", {})
        return data

    def usage_summary(self) -> dict[str, int]:
        return {
            "calls": self.call_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
