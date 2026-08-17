"""Structured investor data model.

Every leaf fact is a :class:`Fact` so provenance and EXPLICIT/DERIVED/NOT_PROVIDED
classification survive all the way from ingestion to the audit JSON.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .evidence import Classification


class Fact(BaseModel):
    """A single investor-relevant value with provenance."""

    model_config = ConfigDict(populate_by_name=True)

    value: str | None = None
    source_slides: list[int] = Field(default_factory=list)
    classification: Classification = Classification.EXPLICIT
    confidence: float = 0.9
    original_text: str | None = None
    calculation: str | None = None

    def __bool__(self) -> bool:
        return bool(self.value) and self.classification is not Classification.NOT_PROVIDED

    @property
    def present(self) -> bool:
        return bool(self)

    def display(self, fallback: str = "Not provided") -> str:
        return self.value if self.present else fallback

    @classmethod
    def missing(cls) -> "Fact":
        return cls(value=None, classification=Classification.NOT_PROVIDED, confidence=1.0)


class _Section(BaseModel):
    """Base for all investor sections; unknown keys are rejected loudly."""

    model_config = ConfigDict(extra="ignore")

    def present_facts(self) -> dict[str, Fact]:
        out: dict[str, Fact] = {}
        for name, value in self:
            if isinstance(value, Fact) and value.present:
                out[name] = value
            elif isinstance(value, list):
                kept = [v for v in value if isinstance(v, Fact) and v.present]
                if kept:
                    out[name] = kept  # type: ignore[assignment]
        return out

    def all_slides(self) -> list[int]:
        slides: set[int] = set()
        for _, value in self:
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, Fact) and item.present:
                    slides.update(item.source_slides)
        return sorted(slides)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


class CompanyProfile(_Section):
    company_name: Fact = Field(default_factory=Fact.missing)
    tagline: Fact = Field(default_factory=Fact.missing)
    description: Fact = Field(default_factory=Fact.missing)
    headquarters: Fact = Field(default_factory=Fact.missing)
    year_founded: Fact = Field(default_factory=Fact.missing)
    industry: Fact = Field(default_factory=Fact.missing)
    website: Fact = Field(default_factory=Fact.missing)
    stage: Fact = Field(default_factory=Fact.missing)


class Problem(_Section):
    customer_problem: Fact = Field(default_factory=Fact.missing)
    magnitude: Fact = Field(default_factory=Fact.missing)
    existing_alternatives: Fact = Field(default_factory=Fact.missing)
    cost_of_problem: Fact = Field(default_factory=Fact.missing)
    urgency: Fact = Field(default_factory=Fact.missing)


class Solution(_Section):
    product_service: Fact = Field(default_factory=Fact.missing)
    how_it_works: Fact = Field(default_factory=Fact.missing)
    value_proposition: Fact = Field(default_factory=Fact.missing)


class Product(_Section):
    specification: Fact = Field(default_factory=Fact.missing)
    development_status: Fact = Field(default_factory=Fact.missing)
    form_factor: Fact = Field(default_factory=Fact.missing)


class Market(_Section):
    tam: Fact = Field(default_factory=Fact.missing)
    sam: Fact = Field(default_factory=Fact.missing)
    som: Fact = Field(default_factory=Fact.missing)
    market_growth: Fact = Field(default_factory=Fact.missing)
    target_customer: Fact = Field(default_factory=Fact.missing)
    geography: Fact = Field(default_factory=Fact.missing)
    methodology: Fact = Field(default_factory=Fact.missing)


class BusinessModel(_Section):
    revenue_model: Fact = Field(default_factory=Fact.missing)
    pricing: Fact = Field(default_factory=Fact.missing)
    average_contract_value: Fact = Field(default_factory=Fact.missing)
    recurring_revenue: Fact = Field(default_factory=Fact.missing)
    gross_margin: Fact = Field(default_factory=Fact.missing)


class Customers(_Section):
    customer_count: Fact = Field(default_factory=Fact.missing)
    named_customers: list[Fact] = Field(default_factory=list)
    buyer_persona: Fact = Field(default_factory=Fact.missing)
    pipeline: Fact = Field(default_factory=Fact.missing)


class Traction(_Section):
    revenue: Fact = Field(default_factory=Fact.missing)
    arr: Fact = Field(default_factory=Fact.missing)
    mrr: Fact = Field(default_factory=Fact.missing)
    bookings: Fact = Field(default_factory=Fact.missing)
    pilots: Fact = Field(default_factory=Fact.missing)
    contracts: Fact = Field(default_factory=Fact.missing)
    partnerships: list[Fact] = Field(default_factory=list)
    growth_rate: Fact = Field(default_factory=Fact.missing)
    retention: Fact = Field(default_factory=Fact.missing)
    usage: Fact = Field(default_factory=Fact.missing)
    regulatory_milestones: list[Fact] = Field(default_factory=list)
    validation_evidence: list[Fact] = Field(default_factory=list)


class FinancialMetrics(_Section):
    revenue_history: list[Fact] = Field(default_factory=list)
    projections: list[Fact] = Field(default_factory=list)
    gross_margin: Fact = Field(default_factory=Fact.missing)
    burn_rate: Fact = Field(default_factory=Fact.missing)
    runway: Fact = Field(default_factory=Fact.missing)
    cac: Fact = Field(default_factory=Fact.missing)
    ltv: Fact = Field(default_factory=Fact.missing)
    cac_payback: Fact = Field(default_factory=Fact.missing)
    churn: Fact = Field(default_factory=Fact.missing)
    nrr: Fact = Field(default_factory=Fact.missing)


class GrowthMetrics(_Section):
    revenue_growth: Fact = Field(default_factory=Fact.missing)
    cagr: Fact = Field(default_factory=Fact.missing)
    customer_growth: Fact = Field(default_factory=Fact.missing)
    derived: list[Fact] = Field(default_factory=list)


class CompetitiveAdvantage(_Section):
    competitors: list[Fact] = Field(default_factory=list)
    alternatives: Fact = Field(default_factory=Fact.missing)
    differentiation: Fact = Field(default_factory=Fact.missing)
    defensibility: Fact = Field(default_factory=Fact.missing)
    switching_costs: Fact = Field(default_factory=Fact.missing)


class Technology(_Section):
    core_technology: Fact = Field(default_factory=Fact.missing)
    technical_validation: Fact = Field(default_factory=Fact.missing)
    regulatory_pathway: Fact = Field(default_factory=Fact.missing)
    manufacturing: Fact = Field(default_factory=Fact.missing)


class IntellectualProperty(_Section):
    patents: list[Fact] = Field(default_factory=list)
    patent_status: Fact = Field(default_factory=Fact.missing)
    trade_secrets: Fact = Field(default_factory=Fact.missing)
    exclusivity: Fact = Field(default_factory=Fact.missing)


class GoToMarket(_Section):
    strategy: Fact = Field(default_factory=Fact.missing)
    channels: list[Fact] = Field(default_factory=list)
    sales_motion: Fact = Field(default_factory=Fact.missing)
    distribution_partners: list[Fact] = Field(default_factory=list)


class TeamMember(BaseModel):
    name: str
    role: str | None = None
    credentials: str | None = None
    commitment: str | None = None
    source_slides: list[int] = Field(default_factory=list)
    classification: Classification = Classification.EXPLICIT

    def one_line(self) -> str:
        bits = [self.name]
        if self.role:
            bits.append(f"({self.role})")
        if self.credentials:
            bits.append(f"— {self.credentials}")
        return " ".join(bits)


class Team(_Section):
    founders: list[TeamMember] = Field(default_factory=list)
    executives: list[TeamMember] = Field(default_factory=list)
    advisors: list[TeamMember] = Field(default_factory=list)
    headcount: Fact = Field(default_factory=Fact.missing)

    def all_slides(self) -> list[int]:
        slides: set[int] = set()
        for group in (self.founders, self.executives, self.advisors):
            for m in group:
                slides.update(m.source_slides)
        if self.headcount.present:
            slides.update(self.headcount.source_slides)
        return sorted(slides)


class Fundraising(_Section):
    raise_amount: Fact = Field(default_factory=Fact.missing)
    instrument: Fact = Field(default_factory=Fact.missing)
    round_stage: Fact = Field(default_factory=Fact.missing)
    valuation: Fact = Field(default_factory=Fact.missing)
    terms: Fact = Field(default_factory=Fact.missing)
    capital_raised_to_date: Fact = Field(default_factory=Fact.missing)
    committed: Fact = Field(default_factory=Fact.missing)
    lead_investor: Fact = Field(default_factory=Fact.missing)
    runway: Fact = Field(default_factory=Fact.missing)


class UseOfFunds(_Section):
    summary: Fact = Field(default_factory=Fact.missing)
    line_items: list[Fact] = Field(default_factory=list)


class Milestones(_Section):
    upcoming: list[Fact] = Field(default_factory=list)
    achieved: list[Fact] = Field(default_factory=list)
    timeline: Fact = Field(default_factory=Fact.missing)


class Risks(_Section):
    identified: list[Fact] = Field(default_factory=list)
    mitigations: list[Fact] = Field(default_factory=list)


class MissingInformation(BaseModel):
    """Investor-material fields the deck does not cover."""

    items: list[str] = Field(default_factory=list)

    def add(self, item: str) -> None:
        if item and item not in self.items:
            self.items.append(item)


class SourceRecord(BaseModel):
    slide: int
    title: str | None = None
    used_for: list[str] = Field(default_factory=list)


class InvestorData(BaseModel):
    """The complete normalised view of a pitch deck."""

    model_config = ConfigDict(extra="ignore")

    company: CompanyProfile = Field(default_factory=CompanyProfile)
    problem: Problem = Field(default_factory=Problem)
    solution: Solution = Field(default_factory=Solution)
    product: Product = Field(default_factory=Product)
    market: Market = Field(default_factory=Market)
    business_model: BusinessModel = Field(default_factory=BusinessModel)
    customers: Customers = Field(default_factory=Customers)
    traction: Traction = Field(default_factory=Traction)
    financial_metrics: FinancialMetrics = Field(default_factory=FinancialMetrics)
    growth_metrics: GrowthMetrics = Field(default_factory=GrowthMetrics)
    competitive_advantage: CompetitiveAdvantage = Field(default_factory=CompetitiveAdvantage)
    technology: Technology = Field(default_factory=Technology)
    intellectual_property: IntellectualProperty = Field(default_factory=IntellectualProperty)
    go_to_market: GoToMarket = Field(default_factory=GoToMarket)
    team: Team = Field(default_factory=Team)
    fundraising: Fundraising = Field(default_factory=Fundraising)
    use_of_funds: UseOfFunds = Field(default_factory=UseOfFunds)
    milestones: Milestones = Field(default_factory=Milestones)
    risks: Risks = Field(default_factory=Risks)
    missing_information: MissingInformation = Field(default_factory=MissingInformation)
    sources: list[SourceRecord] = Field(default_factory=list)

    # -- helpers ------------------------------------------------------------

    def iter_facts(self) -> list[tuple[str, Fact]]:
        """Flatten every :class:`Fact` with a dotted path."""
        out: list[tuple[str, Fact]] = []
        for section_name, section in self:
            if not isinstance(section, _Section):
                continue
            for field_name, value in section:
                items = value if isinstance(value, list) else [value]
                for idx, item in enumerate(items):
                    if isinstance(item, Fact):
                        suffix = f"[{idx}]" if isinstance(value, list) else ""
                        out.append((f"{section_name}.{field_name}{suffix}", item))
        return out

    def present_fact_count(self) -> int:
        return sum(1 for _, f in self.iter_facts() if f.present)

    def resolved_company_name(self, override: str | None = None) -> str:
        if override and override.strip():
            return override.strip()
        if self.company.company_name.present:
            return self.company.company_name.value  # type: ignore[return-value]
        return "Company"

    def audit_records(self) -> list[dict[str, Any]]:
        """Flat list suitable for the *_analysis.json* traceability block."""
        records: list[dict[str, Any]] = []
        for path, fact in self.iter_facts():
            if fact.classification is Classification.NOT_PROVIDED:
                continue
            records.append(
                {
                    "field": path,
                    "claim": fact.value,
                    "source_slides": fact.source_slides,
                    "classification": fact.classification.value,
                    "confidence": fact.confidence,
                    "original_text": fact.original_text,
                    "calculation": fact.calculation,
                }
            )
        return records
