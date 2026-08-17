"""Brand system and Streamlit page structure.

The visual layer is applied as a stylesheet keyed to Streamlit's DOM, so it can
break silently on a Streamlit upgrade. These tests pin the parts that would fail
quietly: the palette, the selectors the design depends on, and the app's own
sentinel hooks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.ui import branding

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
CSS = branding._CSS


class TestPalette:
    def test_matches_the_supplied_design(self) -> None:
        """Colours are taken verbatim from the design; drift would be a redesign."""
        assert branding.BRAND["navy_950"] == "#0B1526"
        assert branding.BRAND["coral"] == "#EE5A4E"
        assert branding.BRAND["amber"] == "#F3A22A"
        assert branding.BRAND["teal"] == "#35BEBB"
        assert branding.BRAND["ink_100"] == "#F3F6FA"

    def test_every_palette_entry_is_a_hex_colour(self) -> None:
        for name, value in branding.BRAND.items():
            assert re.fullmatch(r"#[0-9A-F]{6}", value), f"{name} is not a hex colour"

    def test_css_declares_the_custom_properties(self) -> None:
        for token in ("--navy-950", "--coral", "--amber", "--teal", "--ink-100"):
            assert f"{token}:" in CSS, f"{token} missing from the stylesheet"

    def test_streamlit_theme_matches_the_brand(self) -> None:
        """Streamlit's own chrome must not flash a different palette."""
        config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
        assert 'base = "dark"' in config
        assert branding.BRAND["navy_950"] in config
        assert branding.BRAND["coral"] in config


class TestStylesheet:
    def test_fonts_are_imported(self) -> None:
        assert "fonts.googleapis.com" in CSS
        for family in ("Sora", "Inter", "JetBrains+Mono"):
            assert family in CSS

    def test_import_is_the_first_rule(self) -> None:
        """A CSS @import after any other rule is ignored by the browser."""
        body = CSS.split("<style>", 1)[1].strip()
        assert body.startswith("@import")

    def test_material_icons_are_exempt_from_the_font_override(self) -> None:
        """Forcing Inter onto Streamlit's icon font prints ligature names as text."""
        assert '[data-testid="stIconMaterial"]' in CSS
        assert "Material Symbols Rounded" in CSS

    def test_card_is_keyed_to_an_app_owned_sentinel(self) -> None:
        """Keying off a Streamlit test ID alone would break on upgrade."""
        assert ".tc-card-marker" in CSS
        assert ":has(" in CSS

    def test_primary_button_covers_every_streamlit_variant(self) -> None:
        for variant in ("primaryFormSubmit", "primaryDownloadButton"):
            assert variant in CSS, f"{variant} buttons would miss the gradient"

    def test_dropzone_is_styled(self) -> None:
        assert '[data-testid="stFileUploaderDropzone"]' in CSS

    def test_main_container_clears_the_fixed_header(self) -> None:
        """Too little top padding crops the brand lockup behind Streamlit's header."""
        match = re.search(r"padding-top:\s*([\d.]+)rem", CSS)
        assert match, "no padding-top set on the main container"
        assert float(match.group(1)) >= 4.0

    def test_stylesheet_is_self_contained_apart_from_the_font_cdn(self) -> None:
        urls = re.findall(r"url\(([^)]+)\)", CSS)
        for url in urls:
            assert "fonts.googleapis.com" in url, f"unexpected external asset: {url}"


class TestBrandComponents:
    def test_mark_is_inline_svg_with_the_three_figures(self) -> None:
        svg = branding.BRAND_MARK_SVG
        assert svg.strip().startswith("<svg")
        assert svg.count("<circle") == 3
        for colour in ("#F3A22A", "#35BEBB", "#EE5A4E"):
            assert colour in svg

    def test_mark_carries_an_accessible_label(self) -> None:
        assert 'role="img"' in branding.BRAND_MARK_SVG
        assert "aria-label" in branding.BRAND_MARK_SVG

    def test_no_external_image_dependency(self) -> None:
        assert "<img" not in branding.BRAND_MARK_SVG

    @pytest.mark.parametrize(
        "name",
        ["inject_brand_css", "brand_lockup", "card_marker", "card_heading",
         "eyebrow", "section_label", "disclosure", "footer"],
    )
    def test_component_is_exported(self, name: str) -> None:
        assert hasattr(branding, name)
        import src.ui as ui

        assert hasattr(ui, name) or name == "disclosure"


class TestAppWiring:
    def _source(self) -> str:
        return APP.read_text(encoding="utf-8")

    def test_css_is_injected_before_any_content(self) -> None:
        source = self._source()
        assert source.index("inject_brand_css()") < source.index("brand_lockup()")

    def test_every_bordered_container_is_marked_as_a_card(self) -> None:
        """An unmarked container renders unstyled against the dark canvas."""
        source = self._source()
        containers = source.count("st.container(border=True)")
        markers = source.count("card_marker()")
        assert containers > 0
        assert markers >= containers, (
            f"{containers} bordered containers but only {markers} card markers"
        )

    def test_password_gate_still_precedes_the_app(self) -> None:
        source = self._source()
        assert source.index("_check_password()") < source.index("st.file_uploader")

    def test_upload_limit_is_shown_from_the_enforced_value(self) -> None:
        assert "MAX_UPLOAD_BYTES" in self._source()

    def test_no_email_or_external_delivery(self) -> None:
        """The design's mock disclosure mentioned emailing every generated file."""
        source = self._source().lower()
        for token in ("smtplib", "sendgrid", "@gmail.com", "send_email"):
            assert token not in source, f"unexpected delivery mechanism: {token}"

    def test_disclosure_states_where_the_deck_goes(self) -> None:
        assert "Anthropic" in self._source()
