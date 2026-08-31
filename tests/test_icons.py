"""Favicon and icon-set wiring.

The favicon is easy to break silently: a missing file falls back to an emoji, a
wrong path is only visible in a browser tab. These tests pin the asset, the
generated sizes, and the app's reference to them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "ten_capital_mark.png"
STATIC = ROOT / "static"

EXPECTED = {
    "favicon.png": 256,
    "favicon-16.png": 16,
    "favicon-32.png": 32,
    "favicon-192.png": 192,
    "apple-touch-icon.png": 180,
}


class TestSourceAsset:
    def test_source_mark_exists(self) -> None:
        assert SOURCE.exists(), "assets/ten_capital_mark.png is the source of truth"

    def test_source_is_square_with_alpha(self) -> None:
        with Image.open(SOURCE) as im:
            assert im.mode == "RGBA", "the mark needs transparency"
            assert im.width == im.height, "a non-square mark distorts when resized"


class TestGeneratedIcons:
    @pytest.mark.parametrize("name,size", EXPECTED.items())
    def test_icon_exists_at_the_right_size(self, name: str, size: int) -> None:
        path = STATIC / name
        assert path.exists(), f"static/{name} missing — run tools/make_icons.py"
        with Image.open(path) as im:
            assert im.size == (size, size)

    def test_ico_is_present_and_multi_size(self) -> None:
        ico = STATIC / "favicon.ico"
        assert ico.exists(), "static/favicon.ico missing"
        with Image.open(ico) as im:
            assert im.format == "ICO"
            # A single entry looks poor on high-DPI tabs and pinned shortcuts,
            # so the .ico packs the classic Windows ladder.
            packed = sorted(im.ico.sizes())
            assert (16, 16) in packed and (32, 32) in packed
            assert len(packed) >= 3, f"only {len(packed)} size(s) packed: {packed}"

    def test_icons_keep_transparency(self) -> None:
        with Image.open(STATIC / "favicon.png") as im:
            assert im.mode == "RGBA"
            assert im.getchannel("A").getextrema()[0] == 0, "corners should be transparent"

    def test_padding_is_trimmed_for_small_sizes(self) -> None:
        """The source has ~9% padding, which wastes a fifth of a 16px favicon."""
        with Image.open(STATIC / "favicon-32.png") as im:
            bbox = im.getchannel("A").getbbox()
        content = bbox[2] - bbox[0]
        assert content >= 32 * 0.85, f"icon content only {content}px of 32px"


class TestAppWiring:
    def _app(self) -> str:
        return (ROOT / "app.py").read_text(encoding="utf-8")

    def test_page_icon_points_at_the_asset(self) -> None:
        source = self._app()
        assert "_FAVICON" in source
        assert "page_icon=" in source
        assert '"static"' in source and '"favicon.png"' in source

    def test_favicon_path_is_resolved_from_the_file_not_the_cwd(self) -> None:
        """A relative path breaks when the app is launched from another directory."""
        assert "Path(__file__).resolve().parent" in self._app()

    def test_missing_asset_falls_back_rather_than_crashing(self) -> None:
        assert "_FAVICON.exists()" in self._app()

    def test_static_serving_is_enabled(self) -> None:
        config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
        assert "enableStaticServing = true" in config


class TestBrandLockup:
    def test_lockup_uses_the_supplied_mark(self) -> None:
        from src.ui.branding import mark_data_uri

        uri = mark_data_uri()
        assert uri and uri.startswith("data:image/png;base64,")

    def test_lockup_is_self_contained(self) -> None:
        """Inlined so the header does not wait on a network request."""
        from src.ui import branding

        html_uri = branding.mark_data_uri()
        assert html_uri is not None
        assert "http" not in html_uri.split(",", 1)[0]

    def test_falls_back_to_the_drawn_svg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.ui import branding

        branding.mark_data_uri.cache_clear()
        monkeypatch.setattr(branding, "MARK_PATH", ROOT / "assets" / "does-not-exist.png")
        assert branding.mark_data_uri() is None
        branding.mark_data_uri.cache_clear()
