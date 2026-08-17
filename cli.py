"""Command-line interface.

    python cli.py --reference input/reference_summary.pdf \\
                  --deck input/company_deck.pptx \\
                  --output output/executive_summary.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config import AVAILABLE_MODELS, DEFAULT_MODEL, INPUT_DIR, OUTPUT_DIR, PipelineConfig
from src.pipeline import generate_executive_summary
from src.utils.files import InputValidationError
from src.utils.logging import get_logger, set_verbose

log = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cli.py",
        description="Generate a one-page investor executive summary PDF from a pitch deck.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python cli.py --deck input/deck.pdf --reference input/reference_summary.pdf\n"
            "  python cli.py --deck input/deck.pptx --no-ai\n"
            "  python cli.py --deck input/deck.pdf --company \"Acme Inc\" --verbose\n"
        ),
    )
    p.add_argument("--deck", "-d", required=True,
                   help="Pitch deck to summarise (.pdf or .pptx).")
    p.add_argument("--reference", "-r", default=None,
                   help="Reference executive summary PDF used as the design template.")
    p.add_argument("--template", "-t", default=None,
                   help=(
                       "Document structure template JSON (a TemplateSpec). Overrides "
                       "--reference. Defaults to templates/executive_summary_template.json."
                   ))
    p.add_argument("--output", "-o", default=None,
                   help="Output PDF path. Defaults to output/<company>_executive_summary.pdf")
    p.add_argument("--output-dir", default=str(OUTPUT_DIR),
                   help="Directory for generated files (default: output/).")
    p.add_argument("--company", default=None,
                   help="Override the company name detected in the deck.")
    p.add_argument("--model", default=DEFAULT_MODEL, choices=AVAILABLE_MODELS,
                   help=f"Anthropic model to use (default: {DEFAULT_MODEL}).")
    p.add_argument("--no-ai", action="store_true",
                   help="Disable AI entirely; extract and compose deterministically.")
    p.add_argument("--no-vision", action="store_true",
                   help="Skip the multimodal pass over visually dense slides.")
    p.add_argument("--no-missing-notices", action="store_true",
                   help="Omit the 'information not provided' section from the page.")
    p.add_argument("--external-research", action="store_true",
                   help="Permit outside data (OFF by default; changes grounding guarantees).")
    p.add_argument("--vision-budget", type=int, default=14,
                   help="Maximum slides sent to the multimodal model (default: 14).")
    p.add_argument("--allow-multipage", action="store_true",
                   help="Permit more than one page instead of prioritising content.")
    p.add_argument("--min-font", type=float, default=8.5,
                   help="Minimum body font size in points (default: 8.5).")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose logging.")
    return p


def _resolve_deck(value: str) -> Path:
    path = Path(value)
    if not path.exists() and not path.is_absolute():
        candidate = INPUT_DIR / path.name
        if candidate.exists():
            return candidate
    return path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    set_verbose(args.verbose)

    config = PipelineConfig(
        reference_path=_resolve_deck(args.reference) if args.reference else None,
        template_path=Path(args.template) if args.template else None,
        deck_path=_resolve_deck(args.deck),
        output_dir=Path(args.output_dir),
        output_filename=args.output,
        company_name_override=args.company,
        include_missing_notices=not args.no_missing_notices,
        model=args.model,
        use_ai=not args.no_ai,
        use_vision=not args.no_vision,
        external_research=args.external_research,
        vision_slide_budget=max(0, args.vision_budget),
        verbose=args.verbose,
    )
    config.render.min_body_font_pt = args.min_font
    config.render.allow_multipage = args.allow_multipage
    if args.allow_multipage:
        config.render.max_pages = 0

    if args.output:
        out = Path(args.output)
        if out.parent != Path("."):
            config.output_dir = out.parent

    if config.use_ai and not config.api_key:
        log.warning(
            "ANTHROPIC_API_KEY is not set. Running in deterministic mode "
            "(pass --no-ai to silence this warning)."
        )
    if config.external_research:
        log.warning(
            "External research is ENABLED. Output may contain information not "
            "sourced from the pitch deck."
        )

    try:
        result = generate_executive_summary(config)
    except InputValidationError as exc:
        log.error("Input rejected: %s", exc)
        return 2
    except Exception as exc:
        log.error("Generation failed: %s", exc)
        if args.verbose:
            raise
        return 1

    print()
    print("=" * 66)
    print(f"  Company:        {result.company_name}")
    print(f"  PDF:            {result.pdf_path}")
    print(f"  Extracted JSON: {result.extracted_json_path}")
    print(f"  Analysis JSON:  {result.analysis_json_path}")
    if result.preview_png_path:
        print(f"  Preview PNG:    {result.preview_png_path}")
    print("-" * 66)
    if result.investor_data:
        print(f"  Facts extracted:   {result.investor_data.present_fact_count()}")
    print(f"  Derived metrics:   {len(result.derived_metrics)}")
    print(f"  Metric callouts:   {len(result.summary.metrics) if result.summary else 0}")
    print(f"  Not provided:      {len(result.missing_information)}")
    if result.validation:
        status = "PASSED" if result.validation.passed else "FAILED"
        print(f"  PDF validation:    {status} ({result.validation.page_count} page)")
        for issue in result.validation.issues:
            print(f"      [{issue.severity}] {issue.check}: {issue.detail}")
    if result.qa:
        print(f"  QA:                {'passed' if result.qa.passed else 'FAILED'}")
        for warning in result.qa.warnings[:5]:
            print(f"      [warning] {warning}")
    for warning in result.warnings:
        print(f"  ! {warning}")
    if result.ai_usage:
        print(f"  AI calls:          {result.ai_usage.get('calls', 0)}")
    print(f"  Elapsed:           {result.elapsed_seconds}s")
    print("=" * 66)

    return 0 if result.succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
