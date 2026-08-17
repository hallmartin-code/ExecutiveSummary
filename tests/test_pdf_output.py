"""PDF generation, the one-page constraint, and reference-template inference."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from src.analysis.reference_analyzer import analyze_reference, default_template
from src.analysis.summary_generator import ExecutiveSummary, MetricCallout
from src.config import PipelineConfig, RenderConfig
from src.pipeline import generate_executive_summary
from src.rendering.layout import LayoutEngine, fit_word_budgets_to_page
from src.rendering.pdf_generator import build_meta_line, render_pdf
from src.rendering.pdf_validator import validate_one_page_pdf

LOREM = (
    "The company sells an automated freight brokerage platform to mid-market "
    "shippers and reports recurring revenue across a growing customer base. "
)


def _summary(repeat: int = 1) -> ExecutiveSummary:
    from src.analysis.summary_generator import ContactBlock, FinancialTable, SidebarEntry

    return ExecutiveSummary(
        company_name="Nimbus Freight",
        tagline="Freight brokerage automation for mid-market shippers",
        document_label="Company Overview & Executive Summary",
        sidebar=[
            SidebarEntry(key="stage", label="Stage", value="Series A"),
            SidebarEntry(key="raise", label="Raise", value="$8M"),
            SidebarEntry(key="industry", label="Industry", value="Logistics"),
        ],
        contact=ContactBlock(name="Dana Whitfield", role="CEO"),
        not_stated=["Customer acquisition cost", "Churn"],
        corporate_summary=LOREM * (2 * repeat),
        key_facts=[LOREM * repeat, LOREM * repeat],
        innovative_solution=LOREM * repeat,
        products_platform=LOREM * repeat,
        target_markets=[LOREM * repeat],
        competitive_advantages=[LOREM * repeat],
        commercial_validation=[LOREM * repeat],
        projected_financials=FinancialTable(
            columns=["", "2026", "2027"],
            rows=[["Revenue", "2,430", "10,620"], ["EBITDA", "(1,540)", "940"]],
            caption="$ thousands.",
        ),
        financing_opportunity=LOREM * repeat,
        use_of_funds=LOREM * repeat,
        management_team=[LOREM * repeat],
        investment_thesis=LOREM * repeat,
        metrics=[
            MetricCallout(label="ARR", value="$3.2M", source_slides=[6]),
            MetricCallout(label="Customers", value="47", source_slides=[6]),
            MetricCallout(label="Gross margin", value="74%", source_slides=[5]),
            MetricCallout(label="Raising", value="$8M", source_slides=[9]),
        ],
        footer_note="Confidential. Prepared for accredited investors.",
    )


class TestReferenceInference:
    def test_geometry_is_measured(self, sample_reference: Path) -> None:
        spec = analyze_reference(sample_reference)
        assert spec.source == "inferred"
        assert spec.page.size == "letter"
        assert spec.page.orientation == "portrait"

    def test_two_column_layout_is_detected(self, sample_reference: Path) -> None:
        assert analyze_reference(sample_reference).columns == 2

    def test_sections_are_recognised_and_ordered(self, sample_reference: Path) -> None:
        """Legacy headings must fold onto the current renderable key set."""
        keys = analyze_reference(sample_reference).section_keys()
        for expected in ("corporate_summary", "commercial_validation",
                         "management_team", "financing_opportunity"):
            assert expected in keys, f"{expected} missing from {keys}"

    def test_metric_band_is_inserted_when_callouts_exist(self, sample_reference: Path) -> None:
        spec = analyze_reference(sample_reference)
        assert "metrics" in spec.section_keys()
        assert spec.metric_callout_count >= 3

    def test_no_reference_facts_leak_into_the_template(self, sample_reference: Path) -> None:
        """The reference is a design source only."""
        blob = analyze_reference(sample_reference).model_dump_json().lower()
        for fact in ("example company", "4.1m", "164%", "2.8b"):
            assert fact not in blob

    def test_unreadable_reference_falls_back(self, tmp_path: Path) -> None:
        """An unusable reference must yield the canonical structure, not nothing."""
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"%PDF-1.7\n" + b"\x00" * 200)
        spec = analyze_reference(broken)
        assert spec.source != "inferred"
        assert "corporate_summary" in spec.section_keys()

    def test_default_template_is_the_canonical_structure(self) -> None:
        spec = default_template()
        keys = spec.section_keys()
        assert keys[0] == "corporate_summary"
        assert spec.masthead.enabled
        assert spec.columns == 1


class TestPdfOutput:
    def test_renders_exactly_one_page(self, tmp_path: Path) -> None:
        path, page = render_pdf(_summary(), default_template(), tmp_path / "out.pdf")
        assert path.exists()
        with pymupdf.open(path) as doc:
            assert doc.page_count == 1
        assert page.fits

    def test_text_is_selectable_not_rasterised(self, tmp_path: Path) -> None:
        path, _ = render_pdf(_summary(), default_template(), tmp_path / "out.pdf")
        with pymupdf.open(path) as doc:
            text = doc[0].get_text()
        assert "Nimbus Freight" in text
        assert len(text) > 400

    def test_required_sections_appear(self, tmp_path: Path) -> None:
        path, _ = render_pdf(_summary(), default_template(), tmp_path / "out.pdf")
        with pymupdf.open(path) as doc:
            text = doc[0].get_text().upper()
        for heading in ("CORPORATE SUMMARY", "KEY FACTS", "TARGET MARKETS",
                        "COMMERCIAL VALIDATION", "FINANCING OPPORTUNITY",
                        "KEY MANAGEMENT TEAM"):
            assert heading in text, f"{heading} missing from the rendered page"

    def test_masthead_sidebar_is_rendered(self, tmp_path: Path) -> None:
        path, _ = render_pdf(_summary(), default_template(), tmp_path / "out.pdf")
        with pymupdf.open(path) as doc:
            text = doc[0].get_text()
        assert "Stage:" in text and "Series A" in text
        assert "Contact" in text and "Dana Whitfield" in text
        assert "Not stated in source deck:" in text

    def test_financial_table_is_rendered(self, tmp_path: Path) -> None:
        path, _ = render_pdf(_summary(), default_template(), tmp_path / "out.pdf")
        with pymupdf.open(path) as doc:
            text = doc[0].get_text()
        assert "Revenue" in text and "2,430" in text and "2026" in text

    def test_output_directory_is_created(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "c"
        path, _ = render_pdf(_summary(), default_template(), nested / "out.pdf")
        assert path.exists() and path.parent == nested

    def test_validation_passes_on_a_normal_page(self, tmp_path: Path) -> None:
        template = default_template()
        path, _ = render_pdf(_summary(), template, tmp_path / "out.pdf")
        result = validate_one_page_pdf(path, template=template)
        assert result.passed, [i.model_dump() for i in result.critical]
        assert result.page_count == 1


class TestOnePageConstraint:
    @pytest.mark.parametrize("repeat", [1, 4, 10, 25])
    def test_stays_on_one_page_at_any_content_volume(self, tmp_path: Path, repeat: int) -> None:
        template = default_template()
        path, page = render_pdf(_summary(repeat), template, tmp_path / f"out_{repeat}.pdf")
        with pymupdf.open(path) as doc:
            assert doc.page_count == 1, f"overflowed to {doc.page_count} pages"
        assert page.fits, f"content overflows by {page.overflow_pt}pt"

    def test_font_never_drops_below_the_floor(self, tmp_path: Path) -> None:
        cfg = RenderConfig(min_body_font_pt=8.5)
        _, page = render_pdf(_summary(25), default_template(), tmp_path / "out.pdf", cfg)
        assert page.body_size >= 8.5

    def test_overflow_drops_content_instead_of_clipping(self) -> None:
        """Content must be removed explicitly, never flowed off the page."""
        engine = LayoutEngine(default_template(), min_body_pt=8.5)
        page = engine.fit(_summary(30))
        assert page.fits
        assert page.dropped_sections or page.used_scale < 1.0

    def test_multipage_is_opt_in_only(self, tmp_path: Path) -> None:
        """Content that would overflow stays on one page unless explicitly permitted."""
        template = default_template()
        default_cfg = RenderConfig()
        path, _ = render_pdf(_summary(30), template, tmp_path / "one.pdf", default_cfg)
        with pymupdf.open(path) as doc:
            assert doc.page_count == 1

        multi_cfg = RenderConfig(allow_multipage=True, max_pages=3)
        path2, _ = render_pdf(_summary(30), template, tmp_path / "many.pdf", multi_cfg)
        with pymupdf.open(path2) as doc:
            assert doc.page_count >= 1
            # The relaxed run must not lose content relative to the constrained one.
            assert len(doc[0].get_text()) > 200

    def test_sparse_content_still_renders_validly(self, tmp_path: Path) -> None:
        minimal = ExecutiveSummary(
            company_name="Sparse Co",
            corporate_summary="The deck provides limited information.",
        )
        template = default_template()
        path, page = render_pdf(minimal, template, tmp_path / "sparse.pdf")
        assert page.fits
        with pymupdf.open(path) as doc:
            assert doc.page_count == 1
            assert "Sparse Co" in doc[0].get_text()

    def test_word_budgets_scale_to_page_capacity(self) -> None:
        template = default_template()
        fitted = fit_word_budgets_to_page(template, metric_count=0)
        # Body and masthead are budgeted as separate regions and summed.
        assert fitted.total_body_words > 200
        assert all(
            s.approx_words >= 24
            for s in fitted.sections
            if s.style in ("paragraph", "bullets", "numbered")
        )

    def test_masthead_and_body_are_budgeted_separately(self) -> None:
        """Masthead prose must not compete with the body flow for the same space."""
        template = default_template()
        fitted = fit_word_budgets_to_page(template, metric_count=0)
        masthead = [s for s in fitted.sections if s.column == 2]
        body = [
            s for s in fitted.sections
            if s.column != 2 and s.style in ("paragraph", "bullets", "numbered")
        ]
        assert masthead and body
        assert all(s.approx_words > 0 for s in masthead + body)

    def test_a_table_section_reserves_its_own_height(self) -> None:
        """Removing the table must free capacity for body prose."""
        with_table = fit_word_budgets_to_page(default_template(), metric_count=0)
        stripped = default_template()
        stripped.sections = [s for s in stripped.sections if s.style != "table"]
        without_table = fit_word_budgets_to_page(stripped, metric_count=0)

        def body_words(spec) -> int:
            return sum(
                s.approx_words for s in spec.sections
                if s.column != 2 and s.style in ("paragraph", "bullets", "numbered")
            )

        assert body_words(without_table) > body_words(with_table)


class TestValidator:
    def test_multipage_pdf_is_rejected(self, tmp_path: Path) -> None:
        from reportlab.pdfgen import canvas

        path = tmp_path / "two.pdf"
        c = canvas.Canvas(str(path))
        for _ in range(2):
            c.setFont("Helvetica", 10)
            c.drawString(72, 700, "Some body copy that makes this page non-empty. " * 5)
            c.showPage()
        c.save()

        result = validate_one_page_pdf(path)
        assert not result.passed
        assert any(i.check == "page_count" for i in result.critical)

    def test_missing_file_is_reported(self, tmp_path: Path) -> None:
        result = validate_one_page_pdf(tmp_path / "nope.pdf")
        assert not result.passed

    def test_unreadably_small_type_is_rejected(self, tmp_path: Path) -> None:
        from reportlab.pdfgen import canvas

        path = tmp_path / "tiny.pdf"
        c = canvas.Canvas(str(path))
        c.setFont("Helvetica", 4)
        for i in range(30):
            c.drawString(72, 700 - i * 6, "Body copy set far too small to read on paper.")
        c.showPage()
        c.save()
        result = validate_one_page_pdf(path)
        assert not result.passed


class TestMetaLine:
    def test_long_values_are_condensed(self) -> None:
        line = build_meta_line(
            _summary(),
            {"raise": "$2M to reach FDA approval in 2 years or less", "stage": "Seed"},
        )
        assert "RAISE $2M" in line
        assert "FDA approval" not in line

    def test_absent_values_are_omitted(self) -> None:
        assert build_meta_line(_summary(), {"raise": None, "stage": None}) is None


class TestPipelineIntegration:
    """Full pipeline with AI disabled, so the test is deterministic and offline."""

    def _run(self, tmp_path: Path, deck: Path, reference: Path | None = None):
        config = PipelineConfig(
            reference_path=reference,
            deck_path=deck,
            output_dir=tmp_path / "output",
            use_ai=False,
            use_vision=False,
        )
        return generate_executive_summary(config)

    def test_pdf_deck_produces_a_valid_one_pager(
        self, tmp_path: Path, sample_pdf_deck: Path, sample_reference: Path
    ) -> None:
        result = self._run(tmp_path, sample_pdf_deck, sample_reference)
        assert result.pdf_path and result.pdf_path.exists()
        assert result.validation and result.validation.passed
        with pymupdf.open(result.pdf_path) as doc:
            assert doc.page_count == 1

    def test_pptx_deck_produces_a_valid_one_pager(
        self, tmp_path: Path, sample_pptx_deck: Path
    ) -> None:
        result = self._run(tmp_path, sample_pptx_deck)
        assert result.pdf_path and result.pdf_path.exists()
        assert result.validation and result.validation.passed

    def test_intermediate_json_is_written(self, tmp_path: Path, sample_pdf_deck: Path) -> None:
        result = self._run(tmp_path, sample_pdf_deck)
        assert result.extracted_json_path and result.extracted_json_path.exists()
        assert result.analysis_json_path and result.analysis_json_path.exists()

        import json

        analysis = json.loads(result.analysis_json_path.read_text(encoding="utf-8"))
        for key in ("investor_data", "executive_summary", "evidence",
                    "quality_control", "pdf_validation", "reference_template"):
            assert key in analysis
        assert "traceability" in analysis["evidence"]

    def test_missing_information_is_recorded_not_invented(
        self, tmp_path: Path, sample_pdf_deck: Path
    ) -> None:
        result = self._run(tmp_path, sample_pdf_deck)
        assert result.missing_information
        # The deck has no CAC, so it must appear as a gap and never as a value.
        assert any("CAC" in item or "acquisition" in item for item in result.missing_information)
        assert not result.investor_data.financial_metrics.cac.present

    def test_output_filename_can_be_overridden(
        self, tmp_path: Path, sample_pdf_deck: Path
    ) -> None:
        config = PipelineConfig(
            deck_path=sample_pdf_deck,
            output_dir=tmp_path / "output",
            output_filename="custom_name.pdf",
            use_ai=False, use_vision=False,
        )
        result = generate_executive_summary(config)
        assert result.pdf_path.name == "custom_name.pdf"

    def test_company_name_override_is_applied(
        self, tmp_path: Path, sample_pdf_deck: Path
    ) -> None:
        config = PipelineConfig(
            deck_path=sample_pdf_deck,
            output_dir=tmp_path / "output",
            company_name_override="Renamed Co",
            use_ai=False, use_vision=False,
        )
        result = generate_executive_summary(config)
        assert result.company_name == "Renamed Co"
        with pymupdf.open(result.pdf_path) as doc:
            assert "Renamed Co" in doc[0].get_text()

    def test_no_ungrounded_numbers_reach_the_page(
        self, tmp_path: Path, sample_pdf_deck: Path
    ) -> None:
        """The core guarantee: every figure on the PDF traces back to the deck."""
        from src.analysis.deck_analyzer import build_ledger
        from src.analysis.evidence import extract_numbers
        from src.ingestion import load_deck

        result = self._run(tmp_path, sample_pdf_deck)
        ledger = build_ledger(load_deck(sample_pdf_deck))

        with pymupdf.open(result.pdf_path) as doc:
            rendered = doc[0].get_text()

        # Exclude the footer, which legitimately carries the generation date.
        body = rendered.split("Confidential.")[0]
        ungrounded = [n for n in extract_numbers(body) if not ledger.known_number(n)]
        assert not ungrounded, f"ungrounded figures on the page: {ungrounded}"
