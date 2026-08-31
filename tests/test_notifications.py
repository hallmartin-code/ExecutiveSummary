"""Result-email behaviour.

Email is the only path by which deck-derived content leaves this server for
anywhere other than the AI provider, so the guarantees worth pinning are: it
never fires from a test, it never breaks a generation, it never leaks the key,
and the UI says truthfully where the data goes.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import config as app_config
from src.notifications import emailer
from src.notifications.emailer import (
    Attachment,
    ResendError,
    ResendMailer,
    build_summary,
    send_run_notification,
)
from src.utils.logging import redact

ROOT = Path(__file__).resolve().parent.parent


def _result(tmp_path: Path, **overrides):
    pdf = tmp_path / "Acme_executive_summary.pdf"
    pdf.write_bytes(b"%PDF-1.7\nfake\n")
    analysis = tmp_path / "Acme_analysis.json"
    analysis.write_text(json.dumps({"company_name": "Acme"}), encoding="utf-8")

    base = dict(
        company_name="Acme Devices",
        pdf_path=pdf,
        analysis_json_path=analysis,
        deck=SimpleNamespace(slide_count=18, source_path=str(tmp_path / "deck.pdf")),
        investor_data=SimpleNamespace(present_fact_count=lambda: 64),
        summary=SimpleNamespace(tagline="Portable ventilator for first responders"),
        qa=SimpleNamespace(passed=True),
        validation=SimpleNamespace(passed=True),
        derived_metrics=[{"metric": "Revenue growth"}],
        missing_information=["Revenue or ARR", "Runway"],
        layout_stats={"dropped_sections": ["investment_thesis"]},
        warnings=["Composed deterministically."],
        elapsed_seconds=12.3,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _config(**overrides):
    base = dict(model="claude-sonnet-4-5-20250929", ai_available=True,
                send_notification=None)
    base.update(overrides)
    return SimpleNamespace(**base)


class TestNeverSendsFromTests:
    def test_pytest_is_detected(self) -> None:
        """The guard that stops a test suite mailing a real inbox."""
        assert "PYTEST_CURRENT_TEST" in os.environ

    def test_send_is_suppressed_under_pytest(self, tmp_path: Path, monkeypatch) -> None:
        called = False

        def _boom(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("network call attempted from a test")

        monkeypatch.setattr(emailer.ResendMailer, "send", _boom)
        # Even with everything forced on, the pytest guard wins.
        assert send_run_notification(_result(tmp_path), _config(send_notification=True)) is None
        assert not called

    def test_conftest_disables_notifications(self) -> None:
        assert os.environ.get("EMAIL_NOTIFICATIONS") == "false"


class TestGating:
    """The gate is evaluated with the pytest guard removed, to test the logic."""

    @pytest.fixture(autouse=True)
    def _drop_pytest_marker(self, monkeypatch):
        monkeypatch.setattr(emailer, "_running_under_pytest", lambda: False)
        # Any send in these tests is intercepted, never dispatched.
        monkeypatch.setattr(
            emailer.ResendMailer, "send", lambda self, **kw: "test-message-id"
        )

    def test_disabled_when_no_key(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(app_config, "RESEND_API_KEY", None)
        assert send_run_notification(_result(tmp_path), _config()) is None

    def test_per_run_false_wins_over_enabled_deployment(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(app_config, "RESEND_API_KEY", "re_test")
        monkeypatch.setattr(app_config, "EMAIL_NOTIFICATIONS", True)
        assert send_run_notification(
            _result(tmp_path), _config(send_notification=False)
        ) is None

    def test_sends_when_enabled(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(app_config, "RESEND_API_KEY", "re_test")
        monkeypatch.setattr(app_config, "EMAIL_NOTIFICATIONS", True)
        monkeypatch.setattr(app_config, "NOTIFY_EMAIL_TO", "info@example.com")
        assert send_run_notification(_result(tmp_path), _config()) == "test-message-id"

    def test_no_recipient_means_no_send(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(app_config, "RESEND_API_KEY", "re_test")
        monkeypatch.setattr(app_config, "EMAIL_NOTIFICATIONS", True)
        monkeypatch.setattr(app_config, "NOTIFY_EMAIL_TO", "   ")
        assert send_run_notification(_result(tmp_path), _config()) is None

    def test_multiple_recipients_are_split(self, tmp_path: Path, monkeypatch) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            emailer.ResendMailer, "send",
            lambda self, **kw: captured.update(kw) or "id",
        )
        monkeypatch.setattr(app_config, "RESEND_API_KEY", "re_test")
        monkeypatch.setattr(app_config, "EMAIL_NOTIFICATIONS", True)
        monkeypatch.setattr(app_config, "NOTIFY_EMAIL_TO", "a@x.com, b@y.com")
        send_run_notification(_result(tmp_path), _config())
        assert captured["to"] == ["a@x.com", "b@y.com"]


class TestFailuresAreContained:
    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch):
        monkeypatch.setattr(emailer, "_running_under_pytest", lambda: False)
        monkeypatch.setattr(app_config, "RESEND_API_KEY", "re_test")
        monkeypatch.setattr(app_config, "EMAIL_NOTIFICATIONS", True)
        monkeypatch.setattr(app_config, "NOTIFY_EMAIL_TO", "info@example.com")

    def test_api_error_does_not_raise(self, tmp_path: Path, monkeypatch) -> None:
        def _fail(self, **kwargs):
            raise ResendError("HTTP 422: domain not verified")

        monkeypatch.setattr(emailer.ResendMailer, "send", _fail)
        assert send_run_notification(_result(tmp_path), _config()) is None

    def test_unexpected_error_does_not_raise(self, tmp_path: Path, monkeypatch) -> None:
        def _boom(self, **kwargs):
            raise ValueError("something odd")

        monkeypatch.setattr(emailer.ResendMailer, "send", _boom)
        assert send_run_notification(_result(tmp_path), _config()) is None

    def test_missing_attachment_file_is_skipped(self, tmp_path: Path, monkeypatch) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            emailer.ResendMailer, "send",
            lambda self, **kw: captured.update(kw) or "id",
        )
        result = _result(tmp_path)
        result.pdf_path = tmp_path / "gone.pdf"  # never written
        assert send_run_notification(result, _config()) == "id"
        names = [a.filename for a in captured["attachments"]]
        assert "gone.pdf" not in names

    def test_oversized_attachment_is_skipped(self, tmp_path: Path, monkeypatch) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            emailer.ResendMailer, "send",
            lambda self, **kw: captured.update(kw) or "id",
        )
        monkeypatch.setattr(emailer, "MAX_ATTACHMENT_BYTES", 10)
        assert send_run_notification(_result(tmp_path), _config()) == "id"
        assert captured["attachments"] == []


class TestMessageContent:
    def test_subject_flags_a_clean_run(self, tmp_path: Path) -> None:
        subject, _, _ = build_summary(_result(tmp_path), _config())
        assert subject.startswith("[OK]")
        assert "Acme Devices" in subject

    def test_subject_flags_a_run_needing_review(self, tmp_path: Path) -> None:
        result = _result(tmp_path, validation=SimpleNamespace(passed=False))
        subject, _, _ = build_summary(result, _config())
        assert subject.startswith("[REVIEW]")

    def test_body_reports_the_run(self, tmp_path: Path) -> None:
        _, html, text = build_summary(_result(tmp_path), _config())
        for token in ("Acme Devices", "18", "64", "Revenue or ARR", "investment_thesis"):
            assert token in html, f"{token} missing from the HTML body"
            assert token in text, f"{token} missing from the text body"

    def test_body_has_a_plain_text_alternative(self, tmp_path: Path) -> None:
        _, html, text = build_summary(_result(tmp_path), _config())
        assert "<" not in text, "the text part should not contain markup"
        assert html.lstrip().startswith("<!doctype html>")

    def test_html_escapes_company_names(self, tmp_path: Path) -> None:
        result = _result(tmp_path, company_name='Acme <script>alert(1)</script>')
        _, html, _ = build_summary(result, _config())
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_states_figures_are_unverified(self, tmp_path: Path) -> None:
        _, html, text = build_summary(_result(tmp_path), _config())
        assert "not been independently verified" in html
        assert "not been independently verified" in text

    def test_deterministic_run_is_labelled(self, tmp_path: Path) -> None:
        _, _, text = build_summary(_result(tmp_path), _config(ai_available=False))
        assert "deterministic (no AI)" in text


class TestAttachments:
    def test_encodes_content_as_base64(self) -> None:
        payload = Attachment("x.pdf", b"hello").to_payload()
        assert payload == {"filename": "x.pdf", "content": "aGVsbG8="}


class TestTransport:
    def test_requires_a_key(self, monkeypatch) -> None:
        monkeypatch.setattr(app_config, "RESEND_API_KEY", None)
        with pytest.raises(ResendError, match="not configured"):
            ResendMailer()

    def test_sets_a_user_agent(self) -> None:
        """Resend sits behind Cloudflare, which 403s urllib's default UA."""
        assert "urllib" not in emailer.USER_AGENT
        assert emailer.USER_AGENT

    def test_client_errors_are_not_retried(self, monkeypatch) -> None:
        attempts = {"n": 0}

        def _fake_urlopen(request, timeout=None):
            attempts["n"] += 1
            raise urllib.error.HTTPError(
                emailer.RESEND_ENDPOINT, 422, "Unprocessable", {}, None
            )

        monkeypatch.setattr(emailer.urllib.request, "urlopen", _fake_urlopen)
        monkeypatch.setattr(emailer.time, "sleep", lambda *_: None)
        with pytest.raises(ResendError):
            ResendMailer(api_key="re_test").send(
                sender="a@b.com", to=["c@d.com"], subject="s", html="<p>h</p>", text="t"
            )
        assert attempts["n"] == 1, "a 4xx will not succeed on retry"

    def test_server_errors_are_retried(self, monkeypatch) -> None:
        attempts = {"n": 0}

        def _fake_urlopen(request, timeout=None):
            attempts["n"] += 1
            raise urllib.error.HTTPError(
                emailer.RESEND_ENDPOINT, 503, "Unavailable", {}, None
            )

        monkeypatch.setattr(emailer.urllib.request, "urlopen", _fake_urlopen)
        monkeypatch.setattr(emailer.time, "sleep", lambda *_: None)
        with pytest.raises(ResendError):
            ResendMailer(api_key="re_test").send(
                sender="a@b.com", to=["c@d.com"], subject="s", html="<p>h</p>", text="t"
            )
        assert attempts["n"] == emailer.MAX_ATTEMPTS


class TestSecrets:
    # A synthetic key of the same shape as a real one. The live key is never
    # written into a tracked file — this repository is public.
    FAKE_KEY = "re_AbCdEfGh_1234567890abcdefGHIJKLmn"

    def test_resend_key_shape_is_redacted_from_logs(self) -> None:
        assert "AbCdEfGh" not in redact(f"Authorization: Bearer {self.FAKE_KEY}")

    def test_live_key_is_redacted_from_logs(self) -> None:
        """Redaction of the configured key, without naming it in source."""
        key = app_config.RESEND_API_KEY
        if not key:
            pytest.skip("no Resend key configured")
        assert key not in redact(f"Authorization: Bearer {key}")

    def test_no_resend_key_is_hardcoded_anywhere_tracked(self) -> None:
        """A key literal in a tracked file would be published with the repo."""
        targets = (
            list((ROOT / "src").rglob("*.py"))
            + list((ROOT / "tests").rglob("*.py"))
            + [ROOT / "app.py", ROOT / "cli.py"]
        )
        # Shaped like a real Resend key (re_<id>_<secret>) and anchored at a
        # token boundary, so identifiers such as "..._are_dropped_not_x" do
        # not register as credentials.
        pattern = re.compile(r"(?<![A-Za-z0-9_])re_[A-Za-z0-9]{6,}_[A-Za-z0-9]{16,}")
        for path in targets:
            for match in pattern.findall(path.read_text(encoding="utf-8")):
                assert match == self.FAKE_KEY, f"real-looking key in {path.name}"

    def test_env_example_documents_the_variables(self) -> None:
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for name in ("RESEND_API_KEY", "NOTIFY_EMAIL_TO", "NOTIFY_EMAIL_FROM",
                     "EMAIL_NOTIFICATIONS"):
            assert name in text, f"{name} undocumented in .env.example"


class TestDisclosure:
    def test_ui_states_the_recipient_when_enabled(self) -> None:
        """The page must not claim nothing is emailed once emailing is on."""
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "notifications_enabled()" in source
        assert "NOTIFY_EMAIL_TO" in source
        assert "Resend" in source

    def test_pipeline_records_the_message_id(self) -> None:
        source = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")
        assert "notification_id" in source
        assert "send_run_notification(result, config)" in source
