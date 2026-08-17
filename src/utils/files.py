"""File-safety helpers: validation, safe naming, temp-file lifecycle."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterator

from ..config import (
    ALLOWED_DECK_SUFFIXES,
    ALLOWED_REFERENCE_SUFFIXES,
    MAGIC_PREFIXES,
    MAX_UPLOAD_BYTES,
)
from .logging import get_logger

log = get_logger("files")

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class InputValidationError(ValueError):
    """Raised when a supplied file fails extension, size, or content checks."""


def safe_filename(name: str, default: str = "document", max_len: int = 96) -> str:
    """Collapse *name* into a filesystem-safe slug.

    Strips directory separators, control characters, and Windows reserved names
    so a company name pulled out of a deck can never escape the output folder.
    """
    stem = Path(str(name or "")).name
    stem = _UNSAFE.sub("_", stem).strip("._-")
    stem = re.sub(r"_{2,}", "_", stem)
    if not stem:
        stem = default
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if stem.upper() in reserved:
        stem = f"_{stem}"
    return stem[:max_len]


def validate_input_file(path: str | Path, kind: str = "deck") -> Path:
    """Validate extension, size, and magic bytes for an input document.

    *kind* is ``"deck"`` (PDF or PPTX) or ``"reference"`` (PDF only).
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise InputValidationError(f"File not found: {p}")

    allowed = ALLOWED_DECK_SUFFIXES if kind == "deck" else ALLOWED_REFERENCE_SUFFIXES
    suffix = p.suffix.lower()
    if suffix not in allowed:
        raise InputValidationError(
            f"Unsupported {kind} format '{suffix or '(none)'}'. Allowed: {sorted(allowed)}"
        )

    size = p.stat().st_size
    if size == 0:
        raise InputValidationError(f"File is empty: {p.name}")
    if size > MAX_UPLOAD_BYTES:
        raise InputValidationError(
            f"File too large: {p.name} is {size / 1e6:.1f} MB "
            f"(limit {MAX_UPLOAD_BYTES / 1e6:.0f} MB)"
        )

    expected = MAGIC_PREFIXES.get(suffix)
    if expected:
        with p.open("rb") as fh:
            head = fh.read(8)
        if not any(head.startswith(sig) for sig in expected):
            raise InputValidationError(
                f"{p.name} does not look like a real {suffix.lstrip('.').upper()} file "
                "(content does not match its extension)."
            )
    return p


@contextlib.contextmanager
def temp_workspace(prefix: str = "execsummary_") -> Iterator[Path]:
    """A temp directory that is always removed, even on error."""
    d = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def persist_upload(data: bytes, filename: str, dest_dir: Path) -> Path:
    """Write uploaded bytes to *dest_dir* under a sanitised name."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise InputValidationError(
            f"Upload too large: {len(data) / 1e6:.1f} MB (limit {MAX_UPLOAD_BYTES / 1e6:.0f} MB)"
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower()
    target = dest_dir / safe_filename(Path(filename).stem or "upload")
    target = target.with_suffix(suffix)
    target.write_bytes(data)
    return target


def write_json(path: Path, payload: Any) -> Path:
    """Serialise *payload* to pretty JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def _default(obj: Any) -> Any:
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, set):
            return sorted(obj)
        return str(obj)

    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_default),
        encoding="utf-8",
    )
    return path


def ensure_output_dir(path: str | Path) -> Path:
    d = Path(path).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def cleanup_paths(*paths: str | Path | None) -> None:
    """Best-effort removal of temporary artefacts."""
    for p in paths:
        if not p:
            continue
        try:
            target = Path(p)
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                os.unlink(target)
        except OSError as exc:  # pragma: no cover - defensive
            log.debug("Could not clean up %s: %s", p, exc)
