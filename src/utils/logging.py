"""Application logging with secret redaction.

Any value that looks like an API key is masked before it reaches a handler, so a
stray f-string in a debug message can never leak a credential.
"""

from __future__ import annotations

import logging
import os
import re
import sys

_LOGGER_NAME = "execsummary"

# sk-ant-..., generic long tokens, and Bearer headers.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"re_[A-Za-z0-9_\-]{16,}"),   # Resend
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{12,}"),
    re.compile(r"(?i)(api[_\-]?key\"?\s*[:=]\s*\"?)([A-Za-z0-9_\-]{8,})"),
]

_SENSITIVE_ENV_KEYS = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "RESEND_API_KEY",
    "AWS_SECRET_ACCESS_KEY", "APP_PASSWORD",
)


def redact(text: str) -> str:
    """Mask anything credential-shaped in *text*."""
    out = str(text)
    for key in _SENSITIVE_ENV_KEYS:
        value = os.environ.get(key)
        if value and len(value) > 6 and value in out:
            out = out.replace(value, "***REDACTED***")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 2:
            out = pattern.sub(r"\1***REDACTED***", out)
        else:
            out = pattern.sub("***REDACTED***", out)
    return out


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # never let logging break the pipeline
            pass
        return True


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the shared application logger (configured on first call)."""
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        handler.addFilter(_RedactingFilter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger.getChild(name) if name else logger


def set_verbose(verbose: bool) -> None:
    logging.getLogger(_LOGGER_NAME).setLevel(logging.DEBUG if verbose else logging.INFO)
