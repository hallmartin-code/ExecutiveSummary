"""Deterministic metric calculation.

The LLM never performs financial arithmetic. It supplies explicit values; this
module parses them, computes derived metrics in Python, and records the exact
expression used so every DERIVED figure is auditable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..utils.logging import get_logger
from .evidence import Classification, Evidence
from .investor_schema import Fact, InvestorData

log = get_logger("metrics")

_SCALES = {
    "k": 1e3, "thousand": 1e3, "thousands": 1e3,
    "m": 1e6, "mm": 1e6, "million": 1e6, "millions": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9, "billions": 1e9,
    "t": 1e12, "trillion": 1e12,
}

_MONEY_RE = re.compile(
    r"(?P<neg>[-(])?\s*\$?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<scale>k|m|mm|bn|b|t|thousand|thousands|million|millions|billion|billions|trillion)?\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"(?P<num>-?\d[\d,]*(?:\.\d+)?)\s*%")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_MONTHS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:months?|mos?\b)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_money(text: str | None) -> float | None:
    """Parse a currency-like string into a float. ``"$2.4M"`` -> ``2400000.0``."""
    if not text:
        return None
    match = _MONEY_RE.search(str(text))
    if not match:
        return None
    try:
        value = float(match.group("num").replace(",", ""))
    except ValueError:
        return None
    scale = (match.group("scale") or "").lower()
    if scale:
        value *= _SCALES.get(scale, 1.0)
    if match.group("neg"):
        value = -value
    return value


def parse_percent(text: str | None) -> float | None:
    """Parse a percentage into a float (``"87%"`` -> ``87.0``)."""
    if not text:
        return None
    match = _PERCENT_RE.search(str(text))
    if not match:
        return None
    try:
        return float(match.group("num").replace(",", ""))
    except ValueError:
        return None


_COUNT_RE = re.compile(
    # A scale suffix counts only when it stands alone: "47 customers" is 47,
    # not 47 million, even though "customers" begins with a letter we use as a
    # scale marker elsewhere.
    r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<scale>k|m|mm|bn|b|thousand|million|billion)?(?![A-Za-z])",
    re.IGNORECASE,
)


def parse_count(text: str | None) -> float | None:
    """Parse a plain count, tolerating thousands separators and K/M suffixes."""
    if not text:
        return None
    match = _COUNT_RE.search(str(text))
    if not match:
        return None
    try:
        value = float(match.group("num").replace(",", ""))
    except ValueError:
        return None
    scale = (match.group("scale") or "").lower()
    return value * _SCALES.get(scale, 1.0) if scale else value


def parse_year(text: str | None) -> int | None:
    if not text:
        return None
    match = _YEAR_RE.search(str(text))
    return int(match.group(0)) if match else None


def parse_months(text: str | None) -> float | None:
    if not text:
        return None
    match = _MONTHS_RE.search(str(text))
    return float(match.group(1)) if match else None


def format_money(value: float) -> str:
    """Render a float back into deck-style shorthand."""
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1e9:
        return f"{sign}${v / 1e9:.2f}".rstrip("0").rstrip(".") + "B"
    if v >= 1e6:
        return f"{sign}${v / 1e6:.2f}".rstrip("0").rstrip(".") + "M"
    if v >= 1e3:
        return f"{sign}${v / 1e3:.0f}K"
    return f"{sign}${v:,.0f}"


def format_percent(value: float, decimals: int = 0) -> str:
    return f"{value:.{decimals}f}%" if decimals else f"{value:.0f}%"


def format_count(value: float) -> str:
    if value >= 1e6:
        return f"{value / 1e6:.1f}M".replace(".0M", "M")
    if value >= 1e3 and value == int(value):
        return f"{int(value):,}"
    return f"{value:,.0f}" if value == int(value) else f"{value:,.1f}"


# ---------------------------------------------------------------------------
# Time series
# ---------------------------------------------------------------------------


@dataclass
class SeriesPoint:
    label: str
    year: int | None
    value: float
    slides: list[int]


def _series_from_facts(facts: list[Fact]) -> list[SeriesPoint]:
    """Turn a list of ``"2024 Revenue: $2.0M"``-style facts into a sorted series."""
    points: list[SeriesPoint] = []
    for fact in facts:
        if not fact.present or not fact.value:
            continue
        amount = parse_money(fact.value)
        if amount is None:
            continue
        year = parse_year(fact.value) or parse_year(fact.original_text or "")
        # Reject the case where the year token *is* the amount ("2026" alone).
        if year is not None and abs(amount - year) < 0.5:
            stripped = re.sub(r"\b(19|20)\d{2}\b", " ", fact.value)
            amount = parse_money(stripped)
            if amount is None:
                continue
        points.append(
            SeriesPoint(label=fact.value, year=year, value=amount, slides=list(fact.source_slides))
        )
    points.sort(key=lambda p: (p.year if p.year is not None else 9999))
    return points


# ---------------------------------------------------------------------------
# Derived metrics
# ---------------------------------------------------------------------------


def _derived(
    name: str, value: str, calculation: str, slides: list[int], confidence: float = 0.95
) -> Evidence:
    return Evidence(
        claim=name,
        value=value,
        source_slides=sorted(set(slides)),
        classification=Classification.DERIVED,
        confidence=confidence,
        calculation=calculation,
        field_path=f"growth_metrics.{name.lower().replace(' ', '_')}",
    )


def compute_derived_metrics(data: InvestorData) -> list[Evidence]:
    """Compute every derived metric the deck's explicit values support."""
    derived: list[Evidence] = []

    revenue_series = _series_from_facts(data.financial_metrics.revenue_history)
    projection_series = _series_from_facts(data.financial_metrics.projections)

    # --- Revenue growth and CAGR ------------------------------------------
    for series, label in ((revenue_series, "Revenue"), (projection_series, "Projected revenue")):
        usable = [p for p in series if p.value > 0]
        if len(usable) < 2:
            continue
        first, last = usable[0], usable[-1]

        # Year-over-year between the two most recent consecutive points.
        prev, curr = usable[-2], usable[-1]
        if prev.value > 0:
            growth = (curr.value - prev.value) / prev.value * 100.0
            derived.append(
                _derived(
                    f"{label} growth",
                    format_percent(growth, 0 if abs(growth) >= 10 else 1),
                    f"({curr.value:,.0f} - {prev.value:,.0f}) / {prev.value:,.0f}",
                    prev.slides + curr.slides,
                )
            )

        # CAGR needs a real multi-year span.
        if first.year and last.year and last.year > first.year and first.value > 0:
            periods = last.year - first.year
            if periods >= 2:
                cagr = ((last.value / first.value) ** (1.0 / periods) - 1.0) * 100.0
                derived.append(
                    _derived(
                        f"{label} CAGR",
                        format_percent(cagr),
                        f"({last.value:,.0f} / {first.value:,.0f})^(1/{periods}) - 1",
                        first.slides + last.slides,
                    )
                )

    # --- ARR / MRR conversion ---------------------------------------------
    arr = parse_money(data.traction.arr.value) if data.traction.arr.present else None
    mrr = parse_money(data.traction.mrr.value) if data.traction.mrr.present else None
    if arr and not mrr:
        derived.append(
            _derived("MRR", format_money(arr / 12.0), f"{arr:,.0f} / 12",
                     data.traction.arr.source_slides)
        )
    elif mrr and not arr:
        derived.append(
            _derived("ARR", format_money(mrr * 12.0), f"{mrr:,.0f} * 12",
                     data.traction.mrr.source_slides)
        )

    # --- Gross margin -----------------------------------------------------
    if not data.business_model.gross_margin.present:
        price = parse_money(data.business_model.pricing.value)
        cogs = parse_money(data.business_model.average_contract_value.value)
        if price and cogs and price > cogs > 0:
            margin = (price - cogs) / price * 100.0
            derived.append(
                _derived(
                    "Gross margin",
                    format_percent(margin, 1),
                    f"({price:,.0f} - {cogs:,.0f}) / {price:,.0f}",
                    data.business_model.pricing.source_slides
                    + data.business_model.average_contract_value.source_slides,
                    confidence=0.75,
                )
            )

    # --- Revenue per customer ---------------------------------------------
    revenue = parse_money(data.traction.revenue.value) or arr
    customers = parse_count(data.customers.customer_count.value)
    if revenue and customers and customers > 0:
        derived.append(
            _derived(
                "Revenue per customer",
                format_money(revenue / customers),
                f"{revenue:,.0f} / {customers:,.0f}",
                data.traction.revenue.source_slides + data.customers.customer_count.source_slides,
            )
        )

    # --- LTV/CAC -----------------------------------------------------------
    ltv = parse_money(data.financial_metrics.ltv.value)
    cac = parse_money(data.financial_metrics.cac.value)
    if ltv and cac and cac > 0:
        derived.append(
            _derived(
                "LTV/CAC",
                f"{ltv / cac:.1f}x",
                f"{ltv:,.0f} / {cac:,.0f}",
                data.financial_metrics.ltv.source_slides + data.financial_metrics.cac.source_slides,
            )
        )

    # --- Runway ------------------------------------------------------------
    if not data.financial_metrics.runway.present:
        raise_amount = parse_money(data.fundraising.raise_amount.value)
        burn = parse_money(data.financial_metrics.burn_rate.value)
        if raise_amount and burn and burn > 0:
            months = raise_amount / burn
            if 0 < months < 240:
                derived.append(
                    _derived(
                        "Runway",
                        f"{months:.0f} months",
                        f"{raise_amount:,.0f} / {burn:,.0f} per month",
                        data.fundraising.raise_amount.source_slides
                        + data.financial_metrics.burn_rate.source_slides,
                        confidence=0.8,
                    )
                )

    # --- SOM as a share of TAM --------------------------------------------
    tam = parse_money(data.market.tam.value)
    som = parse_money(data.market.som.value)
    if tam and som and tam > 0 and som <= tam:
        share = som / tam * 100.0
        if share >= 0.05:
            derived.append(
                _derived(
                    "SOM as % of TAM",
                    format_percent(share, 1 if share < 10 else 0),
                    f"{som:,.0f} / {tam:,.0f}",
                    data.market.tam.source_slides + data.market.som.source_slides,
                )
            )

    # --- Remaining raise ---------------------------------------------------
    target = parse_money(data.fundraising.raise_amount.value)
    committed = parse_money(data.fundraising.committed.value)
    if target and committed and 0 < committed < target:
        derived.append(
            _derived(
                "Remaining to close",
                format_money(target - committed),
                f"{target:,.0f} - {committed:,.0f}",
                data.fundraising.raise_amount.source_slides
                + data.fundraising.committed.source_slides,
            )
        )

    # --- Customer growth ---------------------------------------------------
    customer_points = _series_from_facts(
        [f for f in data.customers.named_customers if f.present]
    )
    if len(customer_points) >= 2 and customer_points[0].value > 0:
        first, last = customer_points[0], customer_points[-1]
        growth = (last.value - first.value) / first.value * 100.0
        derived.append(
            _derived(
                "Customer growth",
                format_percent(growth),
                f"({last.value:,.0f} - {first.value:,.0f}) / {first.value:,.0f}",
                first.slides + last.slides,
                confidence=0.7,
            )
        )

    log.info("Calculated %d derived metrics", len(derived))
    for d in derived:
        log.debug("DERIVED %s = %s  [%s]", d.claim, d.value, d.calculation)
    return derived


def apply_derived_to_data(data: InvestorData, derived: list[Evidence]) -> None:
    """Write derived metrics back into the investor schema."""
    for ev in derived:
        fact = Fact(
            value=ev.value,
            source_slides=ev.source_slides,
            classification=Classification.DERIVED,
            confidence=ev.confidence,
            calculation=ev.calculation,
            # Carrying the metric's own name lets downstream code tell a metric
            # derived from projections apart from one derived from actuals.
            original_text=f"Derived metric: {ev.claim}",
        )
        name = ev.claim.lower()
        if name == "revenue growth":
            data.growth_metrics.revenue_growth = fact
        elif name.endswith("cagr"):
            data.growth_metrics.cagr = fact
        elif name == "customer growth":
            data.growth_metrics.customer_growth = fact
        elif name == "mrr" and not data.traction.mrr.present:
            data.traction.mrr = fact
        elif name == "arr" and not data.traction.arr.present:
            data.traction.arr = fact
        elif name == "gross margin" and not data.business_model.gross_margin.present:
            data.business_model.gross_margin = fact
        elif name == "runway" and not data.financial_metrics.runway.present:
            data.financial_metrics.runway = fact
        data.growth_metrics.derived.append(fact)
