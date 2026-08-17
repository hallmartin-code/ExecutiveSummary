"""Rasterise slides to PNG for the multimodal pass.

PDFs render directly through PyMuPDF. PowerPoint files need LibreOffice to
convert to PDF first; when it is unavailable the pipeline degrades to
native-text-only extraction rather than guessing at chart contents.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

import pymupdf

from ..utils.logging import get_logger

log = get_logger("slide_renderer")

# Claude's vision input works best well under the 8000px edge limit; 1400px keeps
# chart axis labels readable while holding images to a sane payload size.
MAX_EDGE_PX = 1400
JPEG_LIKE_QUALITY_DPI = 150


class SlideImage:
    """A rendered slide held in memory (never written to a user-visible path)."""

    __slots__ = ("slide", "png_bytes", "width", "height")

    def __init__(self, slide: int, png_bytes: bytes, width: int, height: int) -> None:
        self.slide = slide
        self.png_bytes = png_bytes
        self.width = width
        self.height = height

    def to_base64(self) -> str:
        return base64.standard_b64encode(self.png_bytes).decode("ascii")

    @property
    def size_kb(self) -> float:
        return len(self.png_bytes) / 1024.0


def _find_soffice() -> str | None:
    """Locate a LibreOffice binary on PATH or in the usual Windows install dirs."""
    for name in ("soffice", "soffice.exe", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def render_pdf_slides(
    pdf_path: str | Path,
    slide_numbers: list[int],
    dpi: int = JPEG_LIKE_QUALITY_DPI,
) -> list[SlideImage]:
    """Render the requested 1-indexed pages of a PDF to PNG bytes."""
    images: list[SlideImage] = []
    if not slide_numbers:
        return images
    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:
        log.warning("Cannot render slides for vision pass: %s", exc)
        return images

    try:
        for n in slide_numbers:
            if not (1 <= n <= doc.page_count):
                continue
            page = doc[n - 1]
            scale = dpi / 72.0
            longest = max(page.rect.width, page.rect.height) * scale
            if longest > MAX_EDGE_PX:
                scale *= MAX_EDGE_PX / longest
            try:
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                images.append(SlideImage(n, pix.tobytes("png"), pix.width, pix.height))
            except Exception as exc:  # pragma: no cover - single-page failure
                log.debug("Failed to rasterise slide %s: %s", n, exc)
    finally:
        doc.close()
    return images


def pptx_to_pdf(pptx_path: str | Path, workdir: Path) -> Path | None:
    """Convert a .pptx to PDF using LibreOffice, if it is installed."""
    soffice = _find_soffice()
    if not soffice:
        log.warning(
            "LibreOffice not found; PowerPoint slides cannot be rasterised. "
            "Visual-only content on image-heavy slides will not be read."
        )
        return None

    workdir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                soffice,
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                str(workdir),
                str(pptx_path),
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        log.warning("LibreOffice conversion failed: %s", exc)
        return None

    produced = workdir / (Path(pptx_path).stem + ".pdf")
    return produced if produced.exists() else None


def render_slides(
    source_path: str | Path,
    source_format: str,
    slide_numbers: list[int],
    workdir: Path,
    dpi: int = JPEG_LIKE_QUALITY_DPI,
) -> list[SlideImage]:
    """Render slides regardless of source format."""
    if not slide_numbers:
        return []
    if source_format == "pdf":
        return render_pdf_slides(source_path, slide_numbers, dpi)
    if source_format == "pptx":
        converted = pptx_to_pdf(source_path, workdir)
        if converted is None:
            return []
        return render_pdf_slides(converted, slide_numbers, dpi)
    return []


def ocr_image(image: SlideImage) -> str | None:
    """Last-resort OCR. Returns ``None`` unless pytesseract is installed."""
    try:
        import io

        import pytesseract  # type: ignore[import-not-found]
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(image.png_bytes)) as im:
            text = pytesseract.image_to_string(im)
        return text.strip() or None
    except Exception as exc:  # pragma: no cover - tesseract binary missing
        log.debug("OCR failed on slide %s: %s", image.slide, exc)
        return None
