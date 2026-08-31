"""End-to-end orchestration.

Deck -> extraction -> investor schema -> evidence validation -> metrics ->
content model -> layout -> PDF -> validation. Every intermediate stage is written
to JSON so an extraction problem can be diagnosed without re-running the pipeline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .ai import get_provider
from .ai.base import AIProvider, NullProvider
from .analysis.deck_analyzer import (
    analyze_deck,
    deck_debug_payload,
    detect_missing_information,
)
from .analysis.evidence import EvidenceLedger
from .analysis.investor_schema import InvestorData
from .analysis.metric_engine import apply_derived_to_data, compute_derived_metrics
from .analysis.docx_analyzer import DocxParseError, analyze_docx_reference
from .analysis.reference_analyzer import (
    TemplateSpec,
    analyze_reference,
    apply_canonical_contract,
    default_template,
    load_template,
)
from .analysis.summary_generator import (
    ExecutiveSummary,
    QAResult,
    compose_deterministic,
    compose_with_ai,
    drop_failing_sections,
    merge_summaries,
    run_quality_control,
    select_metrics,
)
from .config import PipelineConfig
from .ingestion import load_deck
from .notifications import send_run_notification
from .ingestion.document import DeckDocument
from .rendering.layout import fit_word_budgets_to_page, summarise_page
from .rendering.pdf_generator import build_meta_line, default_footer_note, render_pdf
from .rendering.pdf_validator import ValidationResult, render_preview_png, validate_one_page_pdf
from .utils.files import ensure_output_dir, safe_filename, validate_input_file, write_json
from .utils.logging import get_logger, set_verbose

log = get_logger("pipeline")

ProgressFn = Callable[[str, float], None]


@dataclass
class PipelineResult:
    """Everything a caller needs after a run."""

    company_name: str
    pdf_path: Path | None = None
    extracted_json_path: Path | None = None
    analysis_json_path: Path | None = None
    preview_png_path: Path | None = None

    deck: DeckDocument | None = None
    template: TemplateSpec | None = None
    investor_data: InvestorData | None = None
    summary: ExecutiveSummary | None = None
    qa: QAResult | None = None
    validation: ValidationResult | None = None
    layout_stats: dict[str, Any] = field(default_factory=dict)

    derived_metrics: list[dict[str, Any]] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    ai_usage: dict[str, int] = field(default_factory=dict)
    notification_id: str | None = None
    elapsed_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.pdf_path is not None and (
            self.validation is None or self.validation.passed
        )


def _noop(_message: str, _fraction: float) -> None:
    return None


def generate_executive_summary(
    config: PipelineConfig, progress: ProgressFn | None = None
) -> PipelineResult:
    """Run the full pipeline and return the result bundle."""
    started = time.monotonic()
    report = progress or _noop
    set_verbose(config.verbose)

    if config.deck_path is None:
        raise ValueError("A pitch deck path is required.")
    deck_path = validate_input_file(config.deck_path, kind="deck")
    reference_path = (
        validate_input_file(config.reference_path, kind="reference")
        if config.reference_path else None
    )
    output_dir = ensure_output_dir(config.output_dir)

    provider: AIProvider = (
        get_provider("anthropic", model=config.model, api_key=config.api_key)
        if config.ai_available else NullProvider()
    )
    if not getattr(provider, "available", False):
        log.warning(
            "Running without an AI provider: extraction is heuristic and prose is "
            "assembled deterministically from extracted values."
        )

    result = PipelineResult(company_name="Company")

    # -- 1. Reference template --------------------------------------------
    report("Analyzing reference layout", 0.05)
    if config.template_path is not None:
        # An explicit structure template wins: it is a specification, not a sample
        # to be inferred from.
        log.info("Loading document structure template: %s", Path(config.template_path).name)
        template = apply_canonical_contract(load_template(config.template_path))
    elif reference_path is not None:
        log.info("Loading reference executive summary: %s", reference_path.name)
        log.info("Analyzing reference layout")
        if reference_path.suffix.lower() == ".docx":
            try:
                template = analyze_docx_reference(reference_path)
            except DocxParseError as exc:
                log.warning(
                    "Could not read '%s' (%s); using the canonical structure.",
                    reference_path.name, exc,
                )
                template = default_template()
        else:
            template = analyze_reference(
                reference_path, ai_provider=provider if config.ai_available else None
            )
    else:
        log.info("No reference supplied; using the canonical document structure template")
        template = default_template()
    result.template = template

    # -- 2. Deck ingestion -------------------------------------------------
    report("Parsing pitch deck", 0.15)
    deck = load_deck(deck_path)
    result.deck = deck

    # -- 3. Extraction into the investor schema ---------------------------
    report("Extracting investor data", 0.30)
    investor_data, ledger = analyze_deck(deck, config, provider)
    result.investor_data = investor_data

    company_name = investor_data.resolved_company_name(config.company_name_override)
    result.company_name = company_name
    log.info("Identified company: %s", company_name)

    # -- 4. Deterministic metrics -----------------------------------------
    report("Calculating derived metrics", 0.45)
    derived = compute_derived_metrics(investor_data)
    apply_derived_to_data(investor_data, derived)
    ledger.extend(derived)
    result.derived_metrics = [
        {
            "metric": d.claim,
            "value": d.value,
            "calculation": d.calculation,
            "sources": d.source_slides,
            "classification": d.classification.value,
        }
        for d in derived
    ]

    missing = detect_missing_information(investor_data)
    result.missing_information = missing
    for item in missing:
        log.warning("%s not provided", item)

    # -- 5. Compose the summary -------------------------------------------
    report("Generating executive summary", 0.60)
    log.info("Generating executive summary")
    # Rescale the reference's relative section weights to what this page can
    # physically hold, so the output fills the page instead of inheriting the
    # reference document's absolute length.
    template = fit_word_budgets_to_page(template, template.metric_callout_count)
    result.template = template

    # The deterministic composition is always built first: it is the floor that
    # guarantees a usable page even if every AI section fails the evidence gate.
    baseline = compose_deterministic(investor_data, template, config)
    summary = baseline
    if config.ai_available and getattr(provider, "available", False):
        drafted = compose_with_ai(investor_data, template, config, provider, ledger)
        if drafted is not None:
            summary = merge_summaries(baseline, drafted)
        else:
            result.warnings.append("AI drafting unavailable; composed deterministically.")
    else:
        result.warnings.append("Summary composed deterministically from extracted values.")

    # Top up any slots the model left empty, but never invent a slot: the count
    # is capped by what the deck actually supports.
    slots = template.metric_callout_count
    if len(summary.metrics) < slots:
        taken_values = {m.value.lower().replace(" ", "") for m in summary.metrics}
        taken_labels = {m.label.lower() for m in summary.metrics}
        for candidate in select_metrics(investor_data, slots * 2):
            if len(summary.metrics) >= slots:
                break
            if candidate.value.lower().replace(" ", "") in taken_values:
                continue
            if candidate.label.lower() in taken_labels:
                continue
            summary.metrics.append(candidate)
            taken_values.add(candidate.value.lower().replace(" ", ""))
            taken_labels.add(candidate.label.lower())
    summary.metrics = summary.metrics[:slots]

    # Gaps are disclosed in the masthead sidebar under "Not stated in source deck".
    if config.include_missing_notices and not summary.not_stated:
        summary.not_stated = missing[:5]
    if not config.include_missing_notices:
        summary.not_stated = []

    summary.meta_line = build_meta_line(
        summary,
        {
            "sector": investor_data.company.industry.value
            if investor_data.company.industry.present else None,
            "stage": investor_data.company.stage.value
            if investor_data.company.stage.present else None,
            "raise": investor_data.fundraising.raise_amount.value
            if investor_data.fundraising.raise_amount.present else None,
            "instrument": investor_data.fundraising.instrument.value
            if investor_data.fundraising.instrument.present else None,
        },
    )
    summary.footer_note = default_footer_note(company_name)

    # -- 6. Quality control -----------------------------------------------
    report("Running quality control", 0.72)
    qa = run_quality_control(
        summary, investor_data, ledger, provider, config, fallback=baseline
    )
    if not qa.passed:
        dropped = drop_failing_sections(summary, qa)
        if dropped:
            qa.revisions_applied.append(
                f"Removed section(s) with unresolved factual issues: {', '.join(dropped)}"
            )
            # Re-check after removal; the page must not go out with known fabrications.
            qa = run_quality_control(
                summary, investor_data, ledger, NullProvider(), config, fallback=baseline
            )
    result.qa = qa

    # -- 7. Render and validate, with retries -----------------------------
    report("Rendering PDF", 0.82)
    base_name = safe_filename(
        Path(config.output_filename).stem if config.output_filename
        else f"{company_name}_executive_summary"
    )
    pdf_path = output_dir / f"{base_name}.pdf"

    log.info("Rendering PDF")
    validation: ValidationResult | None = None
    page = None
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        pdf_path, page = render_pdf(summary, template, pdf_path, config.render)

        log.info("Validating one-page constraint")
        validation = validate_one_page_pdf(
            pdf_path,
            template=template,
            min_font_pt=config.render.min_body_font_pt - 0.3,
            visual_check=True,
            dpi=config.render.render_dpi,
            max_pages=(
                max(config.render.max_pages or 3, 2)
                if config.render.allow_multipage else 1
            ),
        )
        if validation.passed:
            break

        if attempt == max_retries:
            log.error("PDF validation still failing after %d attempts", max_retries)
            break

        log.warning("Validation failed on attempt %d; revising layout", attempt)
        template, summary = _revise_for_validation(template, summary, validation, attempt)

    result.pdf_path = pdf_path
    result.validation = validation
    if page is not None:
        result.layout_stats = summarise_page(page)
        if page.dropped_sections:
            result.warnings.append(
                f"Sections omitted to hold one page: {', '.join(page.dropped_sections)}"
            )

    # -- 8. Preview + audit artefacts -------------------------------------
    report("Writing audit artefacts", 0.94)
    result.preview_png_path = render_preview_png(
        pdf_path, output_dir / f"{base_name}_preview.png"
    )

    result.extracted_json_path = write_json(
        output_dir / f"{base_name.replace('_executive_summary', '')}_extracted_data.json",
        deck_debug_payload(deck),
    )
    result.analysis_json_path = write_json(
        output_dir / f"{base_name.replace('_executive_summary', '')}_analysis.json",
        _analysis_payload(
            config, deck, template, investor_data, summary, qa, validation,
            ledger, result, provider,
        ),
    )

    if isinstance(provider, NullProvider):
        result.ai_usage = {}
    else:
        usage = getattr(provider, "usage_summary", None)
        result.ai_usage = usage() if callable(usage) else {}

    result.summary = summary
    result.elapsed_seconds = round(time.monotonic() - started, 2)

    # Notify last, once every artefact exists on disk. Failures are logged and
    # swallowed inside send_run_notification: a mail problem must not cost the
    # caller a document that generated successfully.
    report("Sending notification", 0.98)
    result.notification_id = send_run_notification(result, config)

    report("Done", 1.0)

    if validation and validation.passed:
        log.info("PDF validation passed")
    log.info("Completed in %.1fs", result.elapsed_seconds)
    return result


def _revise_for_validation(
    template: TemplateSpec,
    summary: ExecutiveSummary,
    validation: ValidationResult,
    attempt: int,
) -> tuple[TemplateSpec, ExecutiveSummary]:
    """Adjust the template/content in response to a failed validation."""
    t = template.model_copy(deep=True)
    s = summary.model_copy(deep=True)
    checks = {i.check for i in validation.critical}

    if "page_count" in checks or "bounds" in checks or "overlap" in checks:
        # Buy vertical room: tighter leading and slightly smaller headings.
        t.typography.line_height = max(1.14, t.typography.line_height - 0.05)
        t.spacing.section_gap = max(4.0, t.spacing.section_gap - 1.2)
        t.spacing.paragraph_gap = max(2.0, t.spacing.paragraph_gap - 0.5)
        log.info("Revision %d: tightened leading and section spacing", attempt)

    if "font_size" in checks or "font_size_minimum" in checks:
        t.typography.body_size = max(t.typography.body_size, 9.0)
        t.typography.footer_size = max(t.typography.footer_size, 6.4)
        t.typography.metric_label_size = max(t.typography.metric_label_size, 6.2)
        log.info("Revision %d: raised type sizes back above the legibility floor", attempt)

    if "visual_bleed" in checks:
        for edge in ("left", "right", "top", "bottom"):
            t.page.margins[edge] = max(t.page.margins[edge], 36.0)
        log.info("Revision %d: enforced minimum 36pt margins", attempt)

    if attempt >= 2:
        # Shed the lowest-value content rather than compressing further.
        if s.not_stated:
            s.not_stated = []
        elif s.risks:
            s.risks = None
        elif s.problem:
            s.problem = None
        log.info("Revision %d: dropped lowest-priority content", attempt)

    return t, s


def _analysis_payload(
    config: PipelineConfig,
    deck: DeckDocument,
    template: TemplateSpec,
    data: InvestorData,
    summary: ExecutiveSummary,
    qa: QAResult,
    validation: ValidationResult | None,
    ledger: EvidenceLedger,
    result: PipelineResult,
    provider: AIProvider,
) -> dict[str, Any]:
    """The auditable record of the whole run."""
    return {
        "generated_on": date.today().isoformat(),
        "company_name": result.company_name,
        "run": {
            "deck_file": Path(deck.source_path).name,
            "deck_format": deck.source_format,
            "slide_count": deck.slide_count,
            "reference_file": Path(config.reference_path).name if config.reference_path else None,
            "template_file": Path(config.template_path).name if config.template_path else None,
            "template_source": template.source,
            "ai_provider": getattr(provider, "name", "none"),
            "model": config.model if config.ai_available else None,
            "external_research_enabled": config.external_research,
            "include_missing_notices": config.include_missing_notices,
        },
        "reference_template": template.model_dump(mode="json"),
        "investor_data": data.model_dump(mode="json"),
        "executive_summary": summary.model_dump(mode="json"),
        "derived_metrics": result.derived_metrics,
        "missing_information": data.missing_information.items,
        "evidence": {
            "ledger_summary": ledger.summary(),
            "traceability": data.audit_records(),
            "section_source_slides": summary.source_slides,
            "metric_source_slides": {
                m.label: m.source_slides for m in summary.metrics
            },
        },
        "quality_control": {
            "passed": qa.passed,
            "issues": qa.issues,
            "warnings": qa.warnings,
            "revisions_applied": qa.revisions_applied,
        },
        "layout": result.layout_stats,
        "pdf_validation": validation.model_dump(mode="json") if validation else None,
        "sources": [s.model_dump(mode="json") for s in data.sources],
        "pipeline_warnings": result.warnings,
    }
