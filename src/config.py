"""Central configuration.

All secrets are read from the environment. Nothing sensitive is ever stored here
or written to logs.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# A project-local .env wins over an ambient shell variable.
#
# python-dotenv's default is the reverse, which is a quiet foot-gun here: a
# machine-wide ANTHROPIC_API_KEY left over from another tool silently overrides
# the key a developer just wrote into .env, and the only symptom is that calls
# bill to the wrong account. A .env file is an explicit, per-project statement of
# intent, so it takes precedence. Managed deployments ship no .env, so Railway's
# dashboard variables are unaffected.
_DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_DOTENV_PATH, override=True)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = PROJECT_ROOT / "input"
TEMPLATES_DIR = PROJECT_ROOT / "templates"

# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

# Railway sets RAILWAY_ENVIRONMENT on every deployment.
IS_MANAGED_DEPLOYMENT = bool(
    os.environ.get("RAILWAY_ENVIRONMENT")
    or os.environ.get("RAILWAY_PROJECT_ID")
    or _env_flag("MANAGED_DEPLOYMENT")
)

# A container filesystem is ephemeral and its repo directory should be treated as
# read-only, so generated files go to a temp directory and reach the user through
# the download buttons instead.
if os.environ.get("OUTPUT_DIR"):
    OUTPUT_DIR = Path(os.environ["OUTPUT_DIR"]).expanduser()
elif IS_MANAGED_DEPLOYMENT:
    OUTPUT_DIR = Path(tempfile.gettempdir()) / "execsummary_output"
else:
    OUTPUT_DIR = PROJECT_ROOT / "output"

# Optional shared-password gate for a public URL. Unset means no gate.
APP_PASSWORD = os.environ.get("APP_PASSWORD") or None

# Let a visitor supply their own Anthropic key instead of spending the deployment's.
ALLOW_USER_API_KEY = _env_flag("ALLOW_USER_API_KEY", default=False)

# Hide the server-side key entirely and require each visitor to bring one.
REQUIRE_USER_API_KEY = _env_flag("REQUIRE_USER_API_KEY", default=False)

# ---------------------------------------------------------------------------
# Result notifications (Resend)
# ---------------------------------------------------------------------------
#
# Every completed run can be emailed, with the one-pager and the audit JSON
# attached. This sends deck-derived content to a third party (Resend) and to the
# recipient's inbox, so it is only active when a key is configured and it is
# disclosed in the UI.

RESEND_API_KEY = (os.environ.get("RESEND_API_KEY") or "").strip() or None

# The sending domain must be verified in Resend or the API rejects the send.
NOTIFY_EMAIL_FROM = os.environ.get(
    "NOTIFY_EMAIL_FROM", "TEN Capital Deck Analyzer <noreply@tencapital.group>"
)
NOTIFY_EMAIL_TO = os.environ.get("NOTIFY_EMAIL_TO", "Info@tencapital.group")

# Attach the audit JSON as well as the PDF. Off would keep the mail small.
NOTIFY_ATTACH_ANALYSIS = _env_flag("NOTIFY_ATTACH_ANALYSIS", default=True)

EMAIL_NOTIFICATIONS = _env_flag("EMAIL_NOTIFICATIONS", default=bool(RESEND_API_KEY))


def notifications_enabled() -> bool:
    """Whether a completed run should be emailed.

    Reads the module globals at call time so a test or a reload can flip the
    behaviour without the pipeline caching a stale answer.
    """
    return bool(EMAIL_NOTIFICATIONS and RESEND_API_KEY and NOTIFY_EMAIL_TO)

# ---------------------------------------------------------------------------
# Security limits
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = _env_int("MAX_UPLOAD_MB", 80) * 1024 * 1024
MAX_DECK_PAGES = _env_int("MAX_DECK_PAGES", 120)
MAX_REFERENCE_PAGES = 6

ALLOWED_DECK_SUFFIXES = {".pdf", ".pptx"}
# Word references are read structurally by docx_analyzer, which is more reliable
# than measuring a PDF because Word states its styles explicitly.
ALLOWED_REFERENCE_SUFFIXES = {".pdf", ".docx"}

# Magic-byte prefixes used for content sniffing (see utils.files.validate_input_file).
_OOXML = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
MAGIC_PREFIXES = {
    ".pdf": (b"%PDF-",),
    ".pptx": _OOXML,
    ".docx": _OOXML,
}

# ---------------------------------------------------------------------------
# Model defaults
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"

AVAILABLE_MODELS = [
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-1-20250805",
    "claude-haiku-4-5-20251001",
]


@dataclass
class RenderConfig:
    """Knobs that govern the one-page constraint and typographic floors."""

    max_pages: int = 1
    allow_multipage: bool = False
    min_body_font_pt: float = 8.5
    preferred_body_font_pt: float = 10.0
    max_layout_attempts: int = 8
    render_dpi: int = 150


@dataclass
class PipelineConfig:
    """Runtime configuration for a single generation run."""

    reference_path: Path | None = None
    deck_path: Path | None = None
    # Explicit TemplateSpec JSON. Overrides both the reference PDF and the
    # canonical default when set.
    template_path: Path | None = None
    output_dir: Path = OUTPUT_DIR
    output_filename: str | None = None

    company_name_override: str | None = None
    include_missing_notices: bool = True

    model: str = DEFAULT_MODEL
    use_ai: bool = True
    use_vision: bool = True
    external_research: bool = False  # OFF by default; never silently enabled.

    vision_slide_budget: int = 14
    render: RenderConfig = field(default_factory=RenderConfig)

    verbose: bool = False

    # Per-run switch for the result email. None means "follow the deployment
    # setting"; True/False force it on or off for this run.
    send_notification: bool | None = None

    # Supplied per-request (for example typed into the web UI). Held only for the
    # lifetime of the run: never logged, never written to disk, never persisted.
    api_key_override: str | None = field(default=None, repr=False)

    @property
    def api_key(self) -> str | None:
        """Resolve the Anthropic key: per-request override, then environment.

        The key is never read from source and never written to any output file.
        """
        if self.api_key_override and self.api_key_override.strip():
            return self.api_key_override.strip()
        if REQUIRE_USER_API_KEY:
            # The deployment's own key is deliberately not offered to visitors.
            return None
        key = os.environ.get("ANTHROPIC_API_KEY")
        return key.strip() if key and key.strip() else None

    @property
    def ai_available(self) -> bool:
        return bool(self.use_ai and self.api_key)
