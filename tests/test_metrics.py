"""Deterministic metric calculation."""

from __future__ import annotations

import pytest

from src.analysis.evidence import Classification
from src.analysis.investor_schema import Fact, InvestorData
from src.analysis.metric_engine import (
    apply_derived_to_data,
    compute_derived_metrics,
    format_money,
    format_percent,
    parse_count,
    parse_money,
    parse_months,
    parse_percent,
    parse_year,
)


class TestParsing:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("$2.4M", 2_400_000),
            ("$2.4 million", 2_400_000),
            ("$750K", 750_000),
            ("$750,000", 750_000),
            ("$1.25B", 1_250_000_000),
            ("3,200", 3_200),
            ("$32", 32),
            ("($256k)", -256_000),
        ],
    )
    def test_parse_money(self, text: str, expected: float) -> None:
        assert parse_money(text) == pytest.approx(expected)

    def test_parse_money_returns_none_without_a_number(self) -> None:
        assert parse_money("not provided") is None
        assert parse_money(None) is None

    @pytest.mark.parametrize("text,expected", [("74%", 74.0), ("118 %", 118.0), ("-12%", -12.0)])
    def test_parse_percent(self, text: str, expected: float) -> None:
        assert parse_percent(text) == pytest.approx(expected)

    def test_parse_count_and_year_and_months(self) -> None:
        assert parse_count("47 customers") == 47
        assert parse_year("2025 Revenue $3.0M") == 2025
        assert parse_months("24 months of runway") == 24


class TestFormatting:
    @pytest.mark.parametrize(
        "value,expected",
        [(2_400_000, "$2.4M"), (1_250_000_000, "$1.25B"), (750_000, "$750K"), (320, "$320")],
    )
    def test_format_money(self, value: float, expected: str) -> None:
        assert format_money(value) == expected

    def test_format_percent(self) -> None:
        assert format_percent(50.0) == "50%"
        assert format_percent(4.25, 1) == "4.2%"


def _revenue_data() -> InvestorData:
    data = InvestorData()
    data.financial_metrics.revenue_history = [
        Fact(value="2024 Revenue $2,000,000", source_slides=[6]),
        Fact(value="2025 Revenue $3,000,000", source_slides=[6]),
    ]
    return data


class TestDerivedMetrics:
    def test_revenue_growth_is_calculated_in_python(self) -> None:
        """The spec's worked example: 2.0M -> 3.0M is 50% growth."""
        derived = compute_derived_metrics(_revenue_data())
        growth = next(d for d in derived if d.claim == "Revenue growth")
        assert growth.value == "50%"
        assert growth.classification is Classification.DERIVED
        assert growth.calculation is not None
        assert growth.source_slides == [6]

    def test_cagr_requires_a_multi_year_span(self) -> None:
        data = _revenue_data()
        # Two consecutive years is a growth rate, not a CAGR.
        assert not any("CAGR" in d.claim for d in compute_derived_metrics(data))

        data.financial_metrics.revenue_history.append(
            Fact(value="2027 Revenue $8,000,000", source_slides=[6])
        )
        derived = compute_derived_metrics(data)
        cagr = next(d for d in derived if "CAGR" in d.claim)
        # (8.0 / 2.0) ^ (1/3) - 1 = 58.7%
        assert cagr.value == "59%"

    def test_arr_to_mrr_conversion(self) -> None:
        data = InvestorData()
        data.traction.arr = Fact(value="$3.6M", source_slides=[6])
        derived = compute_derived_metrics(data)
        mrr = next(d for d in derived if d.claim == "MRR")
        assert mrr.value == "$300K"
        assert mrr.calculation == "3,600,000 / 12"

    def test_ltv_cac_ratio(self) -> None:
        data = InvestorData()
        data.financial_metrics.ltv = Fact(value="$24,000", source_slides=[9])
        data.financial_metrics.cac = Fact(value="$6,000", source_slides=[9])
        ratio = next(d for d in compute_derived_metrics(data) if d.claim == "LTV/CAC")
        assert ratio.value == "4.0x"

    def test_remaining_raise(self) -> None:
        data = InvestorData()
        data.fundraising.raise_amount = Fact(value="$8M", source_slides=[9])
        data.fundraising.committed = Fact(value="$3.5M", source_slides=[9])
        remaining = next(
            d for d in compute_derived_metrics(data) if d.claim == "Remaining to close"
        )
        assert remaining.value == "$4.5M"

    def test_revenue_per_customer(self) -> None:
        data = InvestorData()
        data.traction.revenue = Fact(value="$3,000,000", source_slides=[6])
        data.customers.customer_count = Fact(value="50 customers", source_slides=[6])
        per_customer = next(
            d for d in compute_derived_metrics(data) if d.claim == "Revenue per customer"
        )
        assert per_customer.value == "$60K"

    def test_nothing_is_derived_without_inputs(self) -> None:
        """No data must never produce a fabricated metric."""
        assert compute_derived_metrics(InvestorData()) == []

    def test_derived_metrics_are_written_back_as_derived(self) -> None:
        data = _revenue_data()
        derived = compute_derived_metrics(data)
        apply_derived_to_data(data, derived)
        assert data.growth_metrics.revenue_growth.value == "50%"
        assert data.growth_metrics.revenue_growth.classification is Classification.DERIVED
        assert data.growth_metrics.revenue_growth.calculation

    def test_projection_derived_metrics_are_labelled(self) -> None:
        data = InvestorData()
        data.financial_metrics.projections = [
            Fact(value="2026 Revenue $1,000,000", source_slides=[24]),
            Fact(value="2029 Revenue $8,000,000", source_slides=[24]),
        ]
        derived = compute_derived_metrics(data)
        assert any("Projected" in d.claim for d in derived)
        apply_derived_to_data(data, derived)
        assert any(
            "projected" in (f.original_text or "").lower()
            for f in data.growth_metrics.derived
        )
