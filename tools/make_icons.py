"""Generate the app's icon set from the TEN Capital mark.

Source of truth is ``assets/ten_capital_mark.png``. Everything under ``static/``
is derived, so re-run this after replacing the source rather than editing the
outputs by hand.

    python tools/make_icons.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "ten_capital_mark.png"
STATIC = ROOT / "static"

# Browser tab, PWA/Android, and iOS home-screen sizes.
PNG_SIZES = {
    "favicon-16.png": 16,
    "favicon-32.png": 32,
    "favicon-192.png": 192,
    "favicon.png": 256,
    "apple-touch-icon.png": 180,
}
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64)]

# The mark ships with ~9% transparent padding. Favicons render at 16px, where
# that padding costs a fifth of the width, so it is trimmed and re-applied at a
# smaller, controlled amount.
TARGET_PADDING = 0.04


def load_trimmed() -> Image.Image:
    """Return the mark cropped to its opaque pixels, on a square canvas."""
    image = Image.open(SOURCE).convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if bbox:
        image = image.crop(bbox)

    # Square it off so no axis is stretched when resizing.
    side = max(image.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    return square


def render(mark: Image.Image, size: int) -> Image.Image:
    inner = max(1, round(size * (1 - 2 * TARGET_PADDING)))
    resized = mark.resize((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(resized, ((size - inner) // 2, (size - inner) // 2), resized)
    return canvas


def build() -> list[Path]:
    if not SOURCE.exists():
        raise SystemExit(f"source icon missing: {SOURCE}")

    STATIC.mkdir(parents=True, exist_ok=True)
    mark = load_trimmed()
    written: list[Path] = []

    for name, size in PNG_SIZES.items():
        out = STATIC / name
        render(mark, size).save(out, "PNG", optimize=True)
        written.append(out)

    ico = STATIC / "favicon.ico"
    render(mark, 256).save(ico, format="ICO", sizes=ICO_SIZES)
    written.append(ico)
    return written


if __name__ == "__main__":
    for path in build():
        with Image.open(path) as im:
            print(f"  {path.relative_to(ROOT).as_posix():34s} {im.size} {im.format}")
    print("icon set written to static/")
