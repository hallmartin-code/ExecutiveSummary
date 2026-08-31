"""Email the result of a completed run via Resend.

This is the one place the application sends deck-derived content anywhere other
than the configured AI provider, so it is deliberately narrow:

* it runs only when a Resend key is configured and notifications are enabled;
* it never raises into the pipeline — a failed send loses an email, not the PDF;
* it refuses to run under pytest, so a future test cannot mail a real inbox.

The Resend REST API is called directly through ``urllib`` rather than adding an
SDK: it is a single JSON POST, and the dependency surface stays smaller.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Sequence

from .. import config as app_config
from ..utils.logging import get_logger

log = get_logger("email")

RESEND_ENDPOINT = "https://api.resend.com/emails"

# Resend sits behind Cloudflare, which rejects urllib's default User-Agent with a
# 403 (error 1010) before the request ever reaches the API.
USER_AGENT = "ExecutiveSummary/1.0 (+https://tencapital.group)"

REQUEST_TIMEOUT = 20.0
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0

# Resend caps a message at 40 MB; stay well under it after base64 inflation.
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024


class ResendError(RuntimeError):
    """Raised inside the mailer; never propagated to the pipeline."""


@dataclass
class Attachment:
    filename: str
    content: bytes

    def to_payload(self) -> dict[str, str]:
        return {
            "filename": self.filename,
            "content": base64.b64encode(self.content).decode("ascii"),
        }


class ResendMailer:
    """Minimal Resend client covering the one call this app makes."""

    def __init__(self, api_key: str | None = None, timeout: float = REQUEST_TIMEOUT) -> None:
        self.api_key = (api_key or app_config.RESEND_API_KEY or "").strip()
        if not self.api_key:
            raise ResendError("RESEND_API_KEY is not configured.")
        self.timeout = timeout

    def send(
        self,
        *,
        sender: str,
        to: Sequence[str],
        subject: str,
        html: str,
        text: str,
        attachments: Sequence[Attachment] = (),
    ) -> str:
        """Send one message. Returns the Resend message id."""
        payload: dict[str, Any] = {
            "from": sender,
            "to": list(to),
            "subject": subject,
            "html": html,
            "text": text,
        }
        if attachments:
            payload["attachments"] = [a.to_payload() for a in attachments]

        body = json.dumps(payload).encode("utf-8")
        last_error = "unknown error"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            request = urllib.request.Request(
                RESEND_ENDPOINT,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    parsed = json.loads(response.read() or b"{}")
                    return str(parsed.get("id", ""))
            except urllib.error.HTTPError as exc:
                detail = (exc.read() or b"")[:300].decode("utf-8", "replace")
                last_error = f"HTTP {exc.code}: {detail}"
                # A 4xx other than rate limiting will not succeed on retry.
                if exc.code != 429 and exc.code < 500:
                    break
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))

        raise ResendError(last_error)


# ---------------------------------------------------------------------------
# Message composition
# ---------------------------------------------------------------------------

_NAVY = "#0B1526"
_CARD = "#101E33"
_INK = "#F3F6FA"
_MUTED = "#7E90A8"
_TEAL = "#35BEBB"
_CORAL = "#EE5A4E"
_SANS = "-apple-system,Segoe UI,sans-serif"
_MONO = "ui-monospace,SFMono-Regular,Menlo,monospace"


def _rows(items: list[tuple[str, str]]) -> str:
    return "".join(
        f'<tr>'
        f'<td style="padding:4px 14px 4px 0;color:{_MUTED};'
        f'font:500 12px/1.5 {_SANS};white-space:nowrap">{escape(label)}</td>'
        f'<td style="padding:4px 0;color:{_INK};'
        f'font:600 13px/1.5 {_SANS}">{escape(value)}</td>'
        f'</tr>'
        for label, value in items
    )


def _bullets(title: str, items: list[str], colour: str) -> str:
    if not items:
        return ""
    entries = "".join(
        f'<li style="margin:2px 0;color:{_INK}">{escape(str(i))}</li>'
        for i in items[:10]
    )
    return (
        f'<div style="margin-top:18px">'
        f'<div style="font:600 11px/1.4 {_MONO};letter-spacing:.12em;'
        f'text-transform:uppercase;color:{colour};margin-bottom:6px">{escape(title)}</div>'
        f'<ul style="margin:0;padding-left:18px;font:400 13px/1.6 {_SANS}">{entries}</ul>'
        f'</div>'
    )


def build_summary(result: Any, config: Any) -> tuple[str, str, str]:
    """Return ``(subject, html, text)`` describing a completed run."""
    company = result.company_name or "Unknown company"
    validation_ok = bool(result.validation and result.validation.passed)
    qa_ok = bool(result.qa and result.qa.passed)
    status = "OK" if validation_ok and qa_ok else "REVIEW"

    subject = f"[{status}] One-pager - {company}"

    facts = result.investor_data.present_fact_count() if result.investor_data else 0
    slides = result.deck.slide_count if result.deck else 0
    deck_name = Path(result.deck.source_path).name if result.deck else "-"

    table = [
        ("Company", company),
        ("Deck", deck_name),
        ("Slides parsed", str(slides)),
        ("Investor facts", str(facts)),
        ("Derived metrics", str(len(result.derived_metrics))),
        ("PDF validation", "passed" if validation_ok else "FAILED"),
        ("Quality control", "passed" if qa_ok else "FAILED"),
        ("Model", config.model if config.ai_available else "deterministic (no AI)"),
        ("Elapsed", f"{result.elapsed_seconds}s"),
    ]

    missing = list(result.missing_information or [])
    warnings = list(result.warnings or [])
    dropped = list((result.layout_stats or {}).get("dropped_sections") or [])

    tagline = ""
    if result.summary and result.summary.tagline:
        tagline = (
            f'<div style="color:{_MUTED};font:400 14px/1.5 {_SANS};margin:2px 0 18px">'
            f'{escape(result.summary.tagline)}</div>'
        )

    html = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:{_NAVY}">
  <div style="max-width:600px;margin:0 auto;background:{_CARD};
              border:1px solid #1E354F;border-radius:16px;padding:28px 30px">
    <div style="height:2px;border-radius:2px;margin:-28px -30px 22px;
                background:linear-gradient(90deg,{_CORAL},#F3A22A,{_TEAL})"></div>
    <div style="font:600 11px/1.4 {_MONO};letter-spacing:.14em;text-transform:uppercase;
                color:{_TEAL};margin-bottom:10px">Deck Analyzer &middot; {escape(status)}</div>
    <div style="font:700 22px/1.3 {_SANS};color:{_INK}">{escape(company)}</div>
    {tagline}
    <table style="border-collapse:collapse;width:100%">{_rows(table)}</table>
    {_bullets("Not stated in the deck", missing, _MUTED)}
    {_bullets("Sections omitted to hold one page", dropped, _MUTED)}
    {_bullets("Run warnings", warnings, _CORAL)}
    <div style="margin-top:22px;padding-top:16px;border-top:1px solid #1E354F;
                font:400 12px/1.6 {_SANS};color:{_MUTED}">
      The one-pager PDF is attached. Every figure is drawn from the submitted deck
      and has not been independently verified.
    </div>
  </div>
</body></html>"""

    lines = [f"{status} - one-pager generated for {company}", ""]
    lines += [f"{label}: {value}" for label, value in table]
    if missing:
        lines += ["", "Not stated in the deck:"] + [f"  - {m}" for m in missing[:10]]
    if dropped:
        lines += ["", "Sections omitted to hold one page:"] + [f"  - {d}" for d in dropped]
    if warnings:
        lines += ["", "Run warnings:"] + [f"  - {w}" for w in warnings[:10]]
    lines += [
        "",
        "The one-pager PDF is attached. Figures are as stated in the deck and have "
        "not been independently verified.",
    ]

    return subject, html, "\n".join(lines)


def _collect_attachments(result: Any) -> list[Attachment]:
    """Attach the PDF, and the audit JSON when configured, within size limits."""
    candidates: list[Path] = []
    if result.pdf_path:
        candidates.append(Path(result.pdf_path))
    if app_config.NOTIFY_ATTACH_ANALYSIS and result.analysis_json_path:
        candidates.append(Path(result.analysis_json_path))

    attachments: list[Attachment] = []
    total = 0
    for path in candidates:
        try:
            data = path.read_bytes()
        except OSError as exc:
            log.warning("Could not attach %s: %s", path.name, exc)
            continue
        if len(data) > MAX_ATTACHMENT_BYTES:
            log.warning("Skipping oversized attachment %s (%.1f MB)", path.name, len(data) / 1e6)
            continue
        if total + len(data) > MAX_TOTAL_ATTACHMENT_BYTES:
            log.warning("Attachment budget reached; skipping %s", path.name)
            continue
        attachments.append(Attachment(path.name, data))
        total += len(data)
    return attachments


def _running_under_pytest() -> bool:
    """Whether this process is a test run.

    Exposed as a function so the notification tests can exercise the real
    dispatch logic by patching it; the env check alone is not patchable from a
    fixture, because pytest re-sets PYTEST_CURRENT_TEST for each test phase.
    """
    return "PYTEST_CURRENT_TEST" in os.environ


def send_run_notification(result: Any, config: Any) -> str | None:
    """Email a completed run. Returns the message id, or None if not sent.

    Never raises: notification is a side effect of a successful generation, and a
    mail failure must not cost the caller their document.
    """
    # A test run must never reach a real inbox, whatever the environment says.
    if _running_under_pytest():
        log.debug("Email notification suppressed under pytest")
        return None

    wanted = getattr(config, "send_notification", None)
    if wanted is False:
        return None
    if wanted is None and not app_config.notifications_enabled():
        return None
    if wanted is True and not app_config.RESEND_API_KEY:
        log.warning("Email requested but RESEND_API_KEY is not configured")
        return None

    recipients = [
        address.strip()
        for address in str(app_config.NOTIFY_EMAIL_TO).split(",")
        if address.strip()
    ]
    if not recipients:
        log.warning("Email enabled but no recipient configured")
        return None

    try:
        subject, html, text = build_summary(result, config)
        attachments = _collect_attachments(result)
        message_id = ResendMailer().send(
            sender=app_config.NOTIFY_EMAIL_FROM,
            to=recipients,
            subject=subject,
            html=html,
            text=text,
            attachments=attachments,
        )
        log.info(
            "Emailed result to %s (%d attachment(s), id %s)",
            ", ".join(recipients), len(attachments), message_id or "n/a",
        )
        return message_id
    except ResendError as exc:
        log.warning("Result email not sent: %s", exc)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Result email failed unexpectedly: %s", type(exc).__name__)
    return None
