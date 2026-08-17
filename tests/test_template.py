"""The canonical document structure template.

These tests protect the two properties that matter: the template stays a valid,
complete specification, and it never acquires company-specific content.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf
import pytest

from src.analysis.reference_analyzer import (
    CANONICAL_TEMPLATE_PATH,
    TemplateSpec,
    analyze_reference,
    apply_canonical_contract,
    default_template,
    load_template,
)
from src.analysis.summary_generator import ExecutiveSummary
from src.config import PipelineConfig
from src.pipeline import generate_executive_summary
from src.rendering.layout import SECTION_PRIORITY, build_blocks

TEMPLATE_PDF = CANONICAL_TEMPLATE_PATH.parent / "executive_summary_template.pdf"

from src.analysis.reference_analyzer import RENDERABLE_SECTION_KEYS

VALID_KEYS = set(RENDERABLE_SECTION_KEYS)

EXPECTED_ORDER = [
    "corporate_summary",
    "key_facts",
    "innovative_solution",
    "products_platform",
    "target_markets",
    "competitive_advantages",
    "commercial_validation",
    "projected_financials",
    "financing_opportunity",
    "use_of_funds",
    "management_team",
    "investment_thesis",
]


class TestTemplateFile:
    def test_exists_and_loads(self) -> None:
        assert CANONICAL_TEMPLATE_PATH.exists(), "canonical template JSON is missing"
        assert isinstance(load_template(CANONICAL_TEMPLATE_PATH), TemplateSpec)

    def test_is_the_default_template(self) -> None:
        spec = default_template()
        assert spec.source == "canonical"
        assert spec.section_keys() == load_template(CANONICAL_TEMPLATE_PATH).section_keys()

    def test_comment_keys_are_ignored(self) -> None:
        raw = json.loads(CANONICAL_TEMPLATE_PATH.read_text(encoding="utf-8"))
        assert "_comment" in raw, "the template should document itself inline"
        assert not hasattr(load_template(CANONICAL_TEMPLATE_PATH), "_comment")

    def test_geometry_is_a_single_letter_page(self) -> None:
        page = default_template().page
        assert (page.width_pt, page.height_pt) == (612.0, 792.0)
        assert page.orientation == "portrait"
        assert all(v >= 32 for v in page.margins.values())

    def test_masthead_and_title_bar_are_enabled(self) -> None:
        spec = default_template()
        assert spec.title_bar.enabled
        assert spec.masthead.enabled
        assert 0.2 < spec.masthead.sidebar_width_ratio < 0.5

    def test_sidebar_fields_are_defined_and_grouped(self) -> None:
        fields = default_template().masthead.sidebar_fields
        keys = [f.key for f in fields]
        for expected in ("stage", "raise", "terms", "industry", "market_size"):
            assert expected in keys
        assert len({f.group for f in fields}) > 1, "sidebar rows should be grouped"

    def test_body_is_a_single_full_width_column(self) -> None:
        spec = default_template()
        assert spec.columns == 1
        assert {s.column for s in spec.body_sections()} == {0}

    def test_body_type_respects_the_legibility_floor(self) -> None:
        typ = default_template().typography
        assert typ.body_size >= 8.5
        assert typ.heading_size >= typ.body_size
        assert typ.title_size > typ.heading_size


class TestSectionStructure:
    def test_section_keys_are_all_renderable(self) -> None:
        for key in default_template().section_keys():
            assert key in VALID_KEYS, f"'{key}' is not a key the renderer understands"

    def test_expected_sections_are_present_and_ordered(self) -> None:
        assert default_template().section_keys() == EXPECTED_ORDER

    def test_masthead_carries_the_opening_narrative(self) -> None:
        assert default_template().masthead_section_keys() == [
            "corporate_summary", "key_facts", "innovative_solution",
        ]

    def test_section_styles_match_the_training_structure(self) -> None:
        styles = {s.key: s.style for s in default_template().sections}
        assert styles["key_facts"] == "numbered"
        assert styles["projected_financials"] == "table"
        for key in ("target_markets", "competitive_advantages",
                    "commercial_validation", "management_team"):
            assert styles[key] == "bullets"
        for key in ("corporate_summary", "products_platform",
                    "financing_opportunity", "investment_thesis"):
            assert styles[key] == "paragraph"

    def test_orders_are_unique_and_sequential(self) -> None:
        orders = [s.order for s in default_template().sections]
        assert orders == sorted(orders)
        assert len(set(orders)) == len(orders)

    def test_every_section_has_a_priority(self) -> None:
        for key in default_template().section_keys():
            assert key in SECTION_PRIORITY, f"'{key}' has no content-prioritisation rank"

    def test_optional_sections_are_marked(self) -> None:
        spec = default_template()
        optional = {s.key for s in spec.sections if s.optional}
        assert "projected_financials" in optional
        assert "investment_thesis" in optional

    def test_corporate_summary_has_the_highest_priority(self) -> None:
        assert SECTION_PRIORITY["corporate_summary"] == max(SECTION_PRIORITY.values())


class TestContentContract:
    def test_every_body_section_states_what_it_must_answer(self) -> None:
        for section in default_template().sections:
            assert section.guidance, f"'{section.key}' has no guidance"
            assert len(section.guidance) > 40

    def test_every_section_names_its_source_fields(self) -> None:
        for section in default_template().sections:
            assert section.source_fields, f"'{section.key}' names no source fields"

    def test_source_fields_resolve_against_the_investor_schema(self) -> None:
        """A field path that does not exist would silently starve a section."""
        from src.analysis.investor_schema import InvestorData

        data = InvestorData()
        for section in default_template().sections:
            for path in section.source_fields:
                if path == "missing_information":
                    continue
                head, _, tail = path.partition(".")
                container = getattr(data, head, None)
                assert container is not None, f"unknown schema section '{head}' in {path}"
                if tail:
                    assert hasattr(container, tail), f"unknown field '{path}'"

    def test_contract_is_applied_to_an_inferred_reference(
        self, sample_reference: Path
    ) -> None:
        """Layout may come from a reference, but the contract never does."""
        spec = analyze_reference(sample_reference)
        canonical = {s.key: s for s in default_template().sections}
        for section in spec.sections:
            if section.key in canonical:
                assert section.guidance, f"'{section.key}' lost its guidance"
                assert section.source_fields

    def test_apply_contract_does_not_overwrite_explicit_guidance(self) -> None:
        spec = default_template()
        target = next(s for s in spec.sections if s.key == "management_team")
        target.guidance = "Custom brief for this deployment."
        apply_canonical_contract(spec)
        assert target.guidance == "Custom brief for this deployment."


class TestNoCompanyData:
    """The template must be pure structure."""

    # Companies that appeared in decks and reference documents during development.
    FORBIDDEN = [
        "accubreath", "madsen", "fogarty", "ventilat", "bag-valve",
        "meridian", "kowalski", "argyrodite", "electrolyte",
        "nimbus", "whitfield", "haddad", "freight", "flexport",
        "halcyon", "verdant",
    ]

    def _text_of(self, path: Path) -> str:
        if path.suffix == ".pdf":
            with pymupdf.open(path) as doc:
                return " ".join(page.get_text() for page in doc).lower()
        return path.read_text(encoding="utf-8").lower()

    @pytest.mark.parametrize(
        "filename",
        ["executive_summary_template.json", "executive_summary_template.pdf",
         "TEMPLATE_STRUCTURE.md"],
    )
    def test_no_company_names_appear(self, filename: str) -> None:
        path = CANONICAL_TEMPLATE_PATH.parent / filename
        if not path.exists():
            pytest.skip(f"{filename} not generated")
        text = self._text_of(path)
        found = [token for token in self.FORBIDDEN if token in text]
        assert not found, f"{filename} contains company-specific content: {found}"

    def test_template_carries_no_concrete_figures(self) -> None:
        """No currency amounts, percentages, or multiples anywhere in the spec."""
        spec = default_template()
        blob = " ".join(
            filter(None, [
                spec.footer.text, spec.header.eyebrow,
                *(s.heading for s in spec.sections),
                *(s.guidance or "" for s in spec.sections),
                *(" ".join(s.source_fields) for s in spec.sections),
            ])
        )
        assert not re.search(r"[$€£]\s?\d", blob), "template contains a currency figure"
        assert not re.search(r"\b\d+(?:\.\d+)?\s?%", blob), "template contains a percentage"
        assert not re.search(r"\b\d+(?:\.\d+)?x\b", blob), "template contains a multiple"

    def test_pdf_placeholders_are_bracketed(self) -> None:
        if not TEMPLATE_PDF.exists():
            pytest.skip("template PDF not generated")
        with pymupdf.open(TEMPLATE_PDF) as doc:
            text = doc[0].get_text()
        for placeholder in ("[COMPANY NAME]", "[Year]", "[value]", "[Contact name]"):
            assert placeholder in text, f"{placeholder} missing from the template PDF"


class TestTemplatePdf:
    def test_is_one_page_with_selectable_text(self) -> None:
        if not TEMPLATE_PDF.exists():
            pytest.skip("template PDF not generated")
        with pymupdf.open(TEMPLATE_PDF) as doc:
            assert doc.page_count == 1
            assert len(doc[0].get_text().strip()) > 500

    def test_every_section_heading_is_drawn(self) -> None:
        if not TEMPLATE_PDF.exists():
            pytest.skip("template PDF not generated")
        with pymupdf.open(TEMPLATE_PDF) as doc:
            text = doc[0].get_text().upper()
        for section in default_template().sections:
            if section.style == "metrics":
                continue
            assert section.heading.upper() in text, f"{section.heading} missing"

    def test_is_usable_as_a_reference_input(self) -> None:
        """The template PDF must round-trip through inference."""
        if not TEMPLATE_PDF.exists():
            pytest.skip("template PDF not generated")
        inferred = analyze_reference(TEMPLATE_PDF)
        assert inferred.page.size == "letter"
        recovered = set(inferred.section_keys())
        assert len(recovered & set(EXPECTED_ORDER)) >= 6, (
            f"too few sections recovered from the template PDF: {sorted(recovered)}"
        )


class TestTemplateDrivesGeneration:
    def test_blocks_are_built_for_every_populated_section(self) -> None:
        from src.analysis.summary_generator import FinancialTable

        spec = default_template()
        summary = ExecutiveSummary(
            company_name="Placeholder Co",
            corporate_summary="Body copy.",
            key_facts=["A fact."],
            innovative_solution="Body copy.",
            products_platform="Body copy.",
            target_markets=["A market."],
            competitive_advantages=["An advantage."],
            commercial_validation=["A proof point."],
            projected_financials=FinancialTable(
                columns=["", "2026"], rows=[["Revenue", "100"]]
            ),
            financing_opportunity="Body copy.",
            use_of_funds="Body copy.",
            management_team=["A person."],
            investment_thesis="Body copy.",
        )
        keys = {b.key for b in build_blocks(summary, spec)}
        assert keys == set(EXPECTED_ORDER)

    def test_empty_sections_produce_no_blocks(self) -> None:
        spec = default_template()
        summary = ExecutiveSummary(company_name="Empty Co", corporate_summary="Only this.")
        assert {b.key for b in build_blocks(summary, spec)} == {"corporate_summary"}

    def test_pipeline_uses_the_template_when_given_one(
        self, tmp_path: Path, sample_pdf_deck: Path
    ) -> None:
        config = PipelineConfig(
            deck_path=sample_pdf_deck,
            template_path=CANONICAL_TEMPLATE_PATH,
            output_dir=tmp_path / "output",
            use_ai=False, use_vision=False,
        )
        result = generate_executive_summary(config)
        assert result.template.source == "canonical"
        assert result.validation and result.validation.passed
        with pymupdf.open(result.pdf_path) as doc:
            assert doc.page_count == 1
            text = doc[0].get_text().upper()
        assert "CORPORATE SUMMARY" in text

    def test_template_overrides_a_supplied_reference(
        self, tmp_path: Path, sample_pdf_deck: Path, sample_reference: Path
    ) -> None:
        config = PipelineConfig(
            deck_path=sample_pdf_deck,
            reference_path=sample_reference,
            template_path=CANONICAL_TEMPLATE_PATH,
            output_dir=tmp_path / "output",
            use_ai=False, use_vision=False,
        )
        result = generate_executive_summary(config)
        assert result.template.source == "canonical"
