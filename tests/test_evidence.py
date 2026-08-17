"""Evidence classification and the hallucination gate."""

from __future__ import annotations

from pathlib import Path

from src.analysis.deck_analyzer import (
    apply_ai_structure,
    build_ledger,
    detect_missing_information,
)
from src.analysis.evidence import (
    Classification,
    Evidence,
    EvidenceLedger,
    extract_numbers,
    normalise_number,
    token_magnitude,
)
from src.analysis.investor_schema import Fact, InvestorData
from src.analysis.summary_generator import (
    BANNED_WORDS,
    strip_marketing_language,
    strip_slide_citations,
)
from src.ingestion import load_deck


class TestNumberNormalisation:
    def test_equivalent_notations_collapse(self) -> None:
        assert normalise_number("$2.4M") == normalise_number("2.4 m") == "2.4m"
        assert normalise_number("$1,234,567") == "1234567"

    def test_scale_words_and_letters_agree(self) -> None:
        assert token_magnitude("2.4m") == token_magnitude("2.4million") == 2_400_000

    def test_magnitudes_ignore_percentages_and_multiples(self) -> None:
        """50% and 50 are different claims and must not be equated."""
        assert token_magnitude("50%") is None
        assert token_magnitude("4.0x") is None

    def test_scale_suffix_needs_a_word_boundary(self) -> None:
        """'$3 Margin' is three dollars, not three million."""
        assert "3m" not in extract_numbers("Cost of Goods Sold $3 Margin 87%")


class TestClassification:
    def test_explicit_evidence_is_grounded(self) -> None:
        ev = Evidence(claim="ARR", value="$3.2M", source_slides=[6],
                      classification=Classification.EXPLICIT)
        assert ev.is_grounded

    def test_not_provided_is_never_grounded(self) -> None:
        assert not Evidence(claim="CAC", classification=Classification.NOT_PROVIDED).is_grounded

    def test_missing_fact_is_falsy(self) -> None:
        fact = Fact.missing()
        assert not fact and not fact.present
        assert fact.display() == "Not provided"


class TestLedgerGate:
    def _ledger(self) -> EvidenceLedger:
        ledger = EvidenceLedger()
        ledger.index_source_text(6, "ARR $3.2M across 47 customers. Gross margin 74%.")
        ledger.index_source_text(9, "Raising $8M with $750,000 committed.")
        return ledger

    def test_deck_numbers_pass(self) -> None:
        result = self._ledger().verify_text("The company reports $3.2M ARR across 47 customers.")
        assert result.grounded
        assert 6 in result.matched_slides

    def test_invented_numbers_are_caught(self) -> None:
        result = self._ledger().verify_text("The company reports $9.9M ARR.")
        assert not result.grounded
        assert "9.9m" in result.ungrounded_numbers

    def test_same_quantity_at_a_different_scale_is_grounded(self) -> None:
        """'$750K' in prose must match '$750,000' in the deck."""
        assert self._ledger().verify_text("Tranche one is $750K.").grounded

    def test_derived_values_become_grounded_once_registered(self) -> None:
        ledger = self._ledger()
        assert not ledger.verify_text("Revenue grew 50%.").grounded
        ledger.add(Evidence(claim="Revenue growth", value="50%", source_slides=[6],
                            classification=Classification.DERIVED,
                            calculation="(3.0 - 2.0) / 2.0"))
        assert ledger.verify_text("Revenue grew 50%.").grounded

    def test_summary_counts_by_classification(self) -> None:
        ledger = self._ledger()
        ledger.add(Evidence(claim="a", value="$3.2M", source_slides=[6]))
        ledger.add(Evidence(claim="b", value="50%", source_slides=[6],
                            classification=Classification.DERIVED))
        summary = ledger.summary()
        assert summary["explicit"] == 1 and summary["derived"] == 1


class TestAIStructureGate:
    def test_ungrounded_ai_values_are_rejected(self) -> None:
        ledger = EvidenceLedger()
        ledger.index_source_text(6, "ARR $3.2M across 47 customers.")
        data = InvestorData()

        accepted = apply_ai_structure(
            data,
            {
                "traction": {
                    "arr": {"value": "$3.2M", "source_slides": [6]},
                    "revenue": {"value": "$18.7M", "source_slides": [6]},  # fabricated
                }
            },
            ledger, max_slide=9,
        )
        assert accepted == 1
        assert data.traction.arr.value == "$3.2M"
        assert not data.traction.revenue.present

    def test_out_of_range_slide_numbers_are_dropped(self) -> None:
        ledger = EvidenceLedger()
        ledger.index_source_text(2, "47 customers")
        data = InvestorData()
        apply_ai_structure(
            data,
            {"customers": {"customer_count": {"value": "47", "source_slides": [99]}}},
            ledger, max_slide=9,
        )
        assert 99 not in data.customers.customer_count.source_slides


class TestMissingInformation:
    def test_absent_fields_are_reported_not_invented(self) -> None:
        missing = detect_missing_information(InvestorData())
        assert "Customer acquisition cost (CAC)" in missing
        assert "Revenue or ARR" in missing

    def test_present_fields_are_not_reported_missing(self) -> None:
        data = InvestorData()
        data.traction.revenue = Fact(value="$3M", source_slides=[6])
        data.customers.customer_count = Fact(value="47", source_slides=[6])
        missing = detect_missing_information(data)
        assert "Customer count" not in missing


class TestLanguageHygiene:
    def test_banned_words_are_removed(self) -> None:
        cleaned, removed = strip_marketing_language(
            "A revolutionary platform addressing a massive market."
        )
        assert "revolutionary" not in cleaned.lower()
        assert "massive" not in cleaned.lower()
        assert removed

    def test_every_banned_word_is_actually_stripped(self) -> None:
        for word in BANNED_WORDS:
            cleaned, removed = strip_marketing_language(f"The company has a {word} product.")
            assert word.lower() not in cleaned.lower(), f"{word!r} survived stripping"
            assert removed

    def test_clean_text_is_untouched(self) -> None:
        text = "The company reports $3.2M ARR across 47 customers."
        cleaned, removed = strip_marketing_language(text)
        assert cleaned == text and removed == []

    def test_slide_citations_move_out_of_prose(self) -> None:
        cleaned, slides = strip_slide_citations(
            "Revenue reached $3.2M (slide 6) with 47 customers (slides 2, 9)."
        )
        assert "slide" not in cleaned.lower()
        assert slides == [2, 6, 9]
        assert "$3.2M" in cleaned

    def test_citation_ranges_are_expanded(self) -> None:
        cleaned, slides = strip_slide_citations("Three endorsements (slides 26-28).")
        assert "slide" not in cleaned.lower()
        assert slides == [26, 27, 28]
        assert cleaned == "Three endorsements."

    def test_citation_with_see_and_and(self) -> None:
        cleaned, slides = strip_slide_citations("Two patents (see slides 11 and 12).")
        assert slides == [11, 12]
        assert "slide" not in cleaned.lower()

    def test_parentheticals_that_are_not_citations_survive(self) -> None:
        text = "Devices cost $25 per use (AMBU and Air Medici)."
        cleaned, slides = strip_slide_citations(text)
        assert cleaned == text and slides == []


class TestListBudgeting:
    """A list section keeps whole items and drops the tail, never mid-sentence cuts."""

    def test_items_are_dropped_not_truncated(self) -> None:
        from src.analysis.reference_analyzer import default_template
        from src.analysis.summary_generator import compose_with_ai
        from src.analysis.investor_schema import InvestorData
        from src.config import PipelineConfig

        long_item = (
            "Cleveland EMS purchased ventilators for 540000 dollars under a general "
            "obligation bond covering all 25 frontline ambulances in the city fleet."
        )

        class FakeProvider:
            available = True

            def generate_summary(self, payload, request):
                return {
                    "corporate_summary": "The company sells a device.",
                    "commercial_validation": [long_item] * 6,
                }

        ledger = EvidenceLedger()
        ledger.index_source_text(1, long_item + " The company sells a device.")

        summary = compose_with_ai(
            InvestorData(), default_template(), PipelineConfig(use_ai=False),
            FakeProvider(), ledger,
        )
        assert summary is not None
        items = summary.commercial_validation
        assert items, "expected at least one bullet to survive"
        assert len(items) < 6, "expected the tail to be dropped"
        for item in items:
            assert item.rstrip()[-1] in ".!?", f"bullet was cut mid-sentence: {item!r}"


class TestTrimPrefersCompleteSentences:
    def test_trim_stops_at_a_sentence_boundary(self) -> None:
        from src.analysis.summary_generator import _trim_to_words

        text = (
            "The company raises $2 million in convertible debt. The round is "
            "structured in two tranches of $750,000 and $1.25 million with a $5 "
            "million pre-money valuation cap and a twenty per cent discount."
        )
        trimmed = _trim_to_words(text, 22)
        assert trimmed == "The company raises $2 million in convertible debt."

    def test_a_single_long_sentence_still_gets_a_stop(self) -> None:
        from src.analysis.summary_generator import _trim_to_words

        text = " ".join(["word"] * 60)
        trimmed = _trim_to_words(text, 20)
        assert trimmed.endswith(".")
        assert len(trimmed.split()) <= 21


class TestListMarkers:
    """The renderer draws bullet and number markers; items must not carry their own."""

    def test_leading_number_is_removed(self) -> None:
        from src.analysis.summary_generator import strip_list_marker

        assert strip_list_marker("1. Acute lung injury burden: 190,600 patients.") == (
            "Acute lung injury burden: 190,600 patients."
        )

    def test_leading_dash_is_removed(self) -> None:
        from src.analysis.summary_generator import strip_list_marker

        assert strip_list_marker("– Keiretsu Forum diligence completed.") == (
            "Keiretsu Forum diligence completed."
        )

    def test_a_figure_at_the_start_survives(self) -> None:
        """'47 customers…' must not lose its leading number."""
        from src.analysis.summary_generator import strip_list_marker

        assert strip_list_marker("47 customers generating $3.2M ARR.") == (
            "47 customers generating $3.2M ARR."
        )

    def test_empty_table_cells_render_as_a_dash(self) -> None:
        from src.analysis.reference_analyzer import default_template
        from src.analysis.investor_schema import InvestorData
        from src.analysis.summary_generator import compose_with_ai
        from src.config import PipelineConfig

        class FakeProvider:
            available = True

            def generate_summary(self, payload, request):
                return {
                    "corporate_summary": "The company sells a device.",
                    "projected_financials": {
                        "columns": ["", "2027", "2028"],
                        "rows": [["Revenue", "2430", "None"]],
                    },
                }

        ledger = EvidenceLedger()
        ledger.index_source_text(1, "The company sells a device. Revenue 2430 in 2027.")
        summary = compose_with_ai(
            InvestorData(), default_template(), PipelineConfig(use_ai=False),
            FakeProvider(), ledger,
        )
        assert summary is not None
        cells = summary.projected_financials.rows[0]
        assert "None" not in cells
        assert "–" in cells


class TestMarkdownStripping:
    """Bold is structural in this document; Markdown markers must never print."""

    def test_bold_markers_are_removed(self) -> None:
        from src.analysis.summary_generator import strip_markdown_emphasis

        assert strip_markdown_emphasis(
            "**Product:** BPAP mode with adjustable pressure."
        ) == "Product: BPAP mode with adjustable pressure."

    def test_underscore_emphasis_is_removed(self) -> None:
        from src.analysis.summary_generator import strip_markdown_emphasis

        assert strip_markdown_emphasis("__Regulatory:__ 510(k) Class II.") == (
            "Regulatory: 510(k) Class II."
        )

    def test_stray_markers_are_removed(self) -> None:
        from src.analysis.summary_generator import strip_markdown_emphasis

        assert "*" not in strip_markdown_emphasis("Revenue ** grew 50% * this year")

    def test_plain_text_is_untouched(self) -> None:
        from src.analysis.summary_generator import strip_markdown_emphasis

        text = "Product: BPAP mode with 4.0 cmH2O baseline pressure."
        assert strip_markdown_emphasis(text) == text

    def test_multiplication_in_prose_survives(self) -> None:
        from src.analysis.summary_generator import strip_markdown_emphasis

        assert strip_markdown_emphasis("3,000 utilities x $250K") == "3,000 utilities x $250K"

    def test_qa_strips_markdown_from_sections(self) -> None:
        from src.ai.base import NullProvider
        from src.analysis.investor_schema import InvestorData
        from src.analysis.summary_generator import ExecutiveSummary, run_quality_control
        from src.config import PipelineConfig

        summary = ExecutiveSummary(
            company_name="Test Co",
            corporate_summary="**Overview:** the company sells software.",
            target_markets=["**Core market:** enterprise buyers."],
        )
        ledger = EvidenceLedger()
        ledger.index_source_text(1, "the company sells software to enterprise buyers")
        run_quality_control(
            summary, InvestorData(), ledger, NullProvider(), PipelineConfig(use_ai=False)
        )
        assert "**" not in summary.corporate_summary
        assert all("**" not in item for item in summary.target_markets)


class TestSentenceBoundaries:
    """Trimming must not treat an abbreviation's full stop as a sentence end."""

    def test_abbreviations_are_not_sentence_ends(self) -> None:
        from src.analysis.summary_generator import last_sentence_end

        text = "Patients suffer injury in the US. The U.S. Air Force uses the device"
        idx = last_sentence_end(text)
        assert text[: idx + 1] == "Patients suffer injury in the US."

    def test_initials_are_not_sentence_ends(self) -> None:
        from src.analysis.summary_generator import last_sentence_end

        text = "Dana Whitfield led sales. Amir A. Haddad joined as CTO"
        assert text[: last_sentence_end(text) + 1] == "Dana Whitfield led sales."

    def test_no_boundary_returns_negative(self) -> None:
        from src.analysis.summary_generator import last_sentence_end

        assert last_sentence_end("a clause with no terminator") == -1

    def test_trim_does_not_leave_a_dangling_abbreviation(self) -> None:
        from src.analysis.summary_generator import _trim_to_words

        text = (
            "The company closed $350,000 in SAFE notes and completed due diligence "
            "with Keiretsu Forum. Medical Dynamics has expressed distribution interest "
            "across 13 western states once FDA approved. The U.S. Air Force uses "
            "portable ventilators of the same type in field operations today."
        )
        for budget in range(20, 55, 3):
            trimmed = _trim_to_words(text, budget)
            assert not trimmed.rstrip().endswith("The U.S."), f"budget {budget}"
            assert trimmed.rstrip()[-1] in ".!?"


class TestInventedGaps:
    """A section must not claim the deck is silent when facts were extracted."""

    def _data_with_team(self):
        from src.analysis.investor_schema import InvestorData, TeamMember

        data = InvestorData()
        data.team.founders = [
            TeamMember(name="Dana Whitfield", role="CEO",
                       credentials="12 years at C.H. Robinson", source_slides=[7]),
        ]
        return data

    def test_false_absence_claim_is_detected(self) -> None:
        from src.analysis.summary_generator import ExecutiveSummary, detect_invented_gaps

        summary = ExecutiveSummary(
            company_name="Nimbus",
            management_team=["The deck does not provide information on the founding team."],
        )
        assert "management_team" in detect_invented_gaps(summary, self._data_with_team())

    def test_accurate_absence_claim_is_allowed(self) -> None:
        from src.analysis.investor_schema import InvestorData
        from src.analysis.summary_generator import ExecutiveSummary, detect_invented_gaps

        summary = ExecutiveSummary(
            company_name="Nimbus",
            management_team=["The deck does not provide information on the founding team."],
        )
        # Nothing was extracted, so the statement is true.
        assert detect_invented_gaps(summary, InvestorData()) == []

    def test_section_reporting_real_facts_is_not_flagged(self) -> None:
        from src.analysis.summary_generator import ExecutiveSummary, detect_invented_gaps

        summary = ExecutiveSummary(
            company_name="Nimbus",
            management_team=[
                "Dana Whitfield, CEO, spent 12 years at C.H. Robinson.",
                "The deck does not disclose employee count.",
            ],
        )
        assert detect_invented_gaps(summary, self._data_with_team()) == []

    def test_qa_restores_the_deterministic_section(self) -> None:
        from src.ai.base import NullProvider
        from src.analysis.summary_generator import ExecutiveSummary, run_quality_control
        from src.config import PipelineConfig

        data = self._data_with_team()
        summary = ExecutiveSummary(
            company_name="Nimbus",
            corporate_summary="Nimbus automates freight brokerage.",
            management_team=["The deck does not provide information on the founding team."],
        )
        fallback = ExecutiveSummary(
            company_name="Nimbus",
            corporate_summary="Nimbus automates freight brokerage.",
            management_team=["Dana Whitfield (CEO) - 12 years at C.H. Robinson."],
        )
        ledger = EvidenceLedger()
        ledger.index_source_text(7, "Dana Whitfield, CEO, 12 years at C.H. Robinson.")

        result = run_quality_control(
            summary, data, ledger, NullProvider(), PipelineConfig(use_ai=False),
            fallback=fallback,
        )
        assert "Dana Whitfield" in " ".join(summary.management_team)
        assert any("false 'not provided'" in r for r in result.revisions_applied)


class TestTerminalPunctuation:
    def test_dangling_semicolon_is_closed(self) -> None:
        from src.analysis.summary_generator import tidy_terminal_punctuation

        assert tidy_terminal_punctuation(
            "Advisors include Spencer Walker, MSc (20+ years medtech);"
        ) == "Advisors include Spencer Walker, MSc (20+ years medtech)."

    def test_dangling_connector_is_removed(self) -> None:
        from src.analysis.summary_generator import tidy_terminal_punctuation

        assert tidy_terminal_punctuation(
            "Proceeds fund engineering, sales, and"
        ) == "Proceeds fund engineering, sales."

    def test_finished_sentences_are_untouched(self) -> None:
        from src.analysis.summary_generator import tidy_terminal_punctuation

        text = "The company reports $3.2M ARR."
        assert tidy_terminal_punctuation(text) == text

    def test_layout_trim_always_ends_a_sentence(self) -> None:
        from src.analysis.reference_analyzer import default_template
        from src.rendering.layout import LayoutEngine, ScaleState

        engine = LayoutEngine(default_template())
        source = (
            "Tranche 1 is $750k for design freeze; Tranche 2 is $1.25M for FDA work; "
            "milestones run from Q1 2026 through Q3 2027; approval is targeted for 2028."
        )
        for ratio in (0.9, 0.75, 0.6, 0.45, 0.3):
            trimmed = engine._trim(source, ScaleState(trim_ratio=ratio))
            assert trimmed.rstrip()[-1] in ".!?", f"ratio {ratio} produced {trimmed!r}"


class TestMetricLabels:
    def test_projected_prefix_preserves_the_metric_noun(self) -> None:
        """Truncation must not turn a growth rate into a revenue figure."""
        from src.analysis.summary_generator import _prefix_label

        assert _prefix_label("2029-30 Revenue Growth", "Proj.") == "Proj. Revenue Growth"

    def test_short_labels_are_prefixed_intact(self) -> None:
        from src.analysis.summary_generator import _prefix_label

        assert _prefix_label("CAGR", "Proj.") == "Proj. CAGR"

    def test_projection_metrics_are_labelled_in_qa(self) -> None:
        from src.analysis.investor_schema import Fact, InvestorData
        from src.analysis.metric_engine import apply_derived_to_data, compute_derived_metrics
        from src.analysis.summary_generator import (
            ExecutiveSummary,
            MetricCallout,
            run_quality_control,
        )
        from src.ai.base import NullProvider
        from src.config import PipelineConfig

        data = InvestorData()
        data.financial_metrics.projections = [
            Fact(value="2026 Revenue $1,000,000", source_slides=[24]),
            Fact(value="2029 Revenue $8,000,000", source_slides=[24]),
        ]
        derived = compute_derived_metrics(data)
        apply_derived_to_data(data, derived)
        growth = next(d for d in derived if "growth" in d.claim.lower())

        summary = ExecutiveSummary(
            company_name="Test Co",
            investment_opportunity="The deck projects revenue growth.",
            metrics=[MetricCallout(label="Revenue growth", value=growth.value,
                                   source_slides=[24])],
        )
        ledger = EvidenceLedger()
        ledger.index_source_text(24, "2026 Revenue $1,000,000 2029 Revenue $8,000,000")
        ledger.extend(derived)

        run_quality_control(summary, data, ledger, NullProvider(),
                            PipelineConfig(use_ai=False))
        assert summary.metrics[0].label.lower().startswith("proj")


class TestEndToEndGrounding:
    def test_every_ledger_number_traces_to_a_slide(self, sample_pdf_deck: Path) -> None:
        deck = load_deck(sample_pdf_deck)
        ledger = build_ledger(deck)
        for token in ("$3.2M", "47", "74%", "$8M"):
            assert ledger.known_number(token), f"{token} should be indexed from the deck"
            assert ledger.slides_for_number(token), f"{token} has no slide provenance"

    def test_a_figure_absent_from_the_deck_is_not_known(self, sample_pdf_deck: Path) -> None:
        ledger = build_ledger(load_deck(sample_pdf_deck))
        assert not ledger.known_number("$41.7M")
