"""Streamlit interface, styled to the TEN Capital Network brand.

    streamlit run app.py
"""

from __future__ import annotations

import hmac
import sys
import traceback
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import (  # noqa: E402
    ALLOW_USER_API_KEY,
    APP_PASSWORD,
    NOTIFY_EMAIL_TO,
    AVAILABLE_MODELS,
    DEFAULT_MODEL,
    IS_MANAGED_DEPLOYMENT,
    MAX_UPLOAD_BYTES,
    OUTPUT_DIR,
    REQUIRE_USER_API_KEY,
    PipelineConfig,
    _env_flag,
    notifications_enabled,
)
from src.pipeline import generate_executive_summary  # noqa: E402
from src.ui import (  # noqa: E402
    brand_lockup,
    card_heading,
    card_marker,
    disclosure,
    eyebrow,
    footer,
    inject_brand_css,
    section_label,
)
from src.utils.files import InputValidationError, persist_upload, temp_workspace  # noqa: E402

# The favicon is the TEN Capital mark. Resolved from this file rather than the
# working directory so it loads however the app is launched, and falls back to an
# emoji if the asset is ever missing so the page still renders.
_FAVICON = Path(__file__).resolve().parent / "static" / "favicon.png"

st.set_page_config(
    page_title="TEN Capital — Deck to One-Pager",
    page_icon=str(_FAVICON) if _FAVICON.exists() else "📄",
    layout="centered",
    initial_sidebar_state="collapsed",
)

inject_brand_css()


# ---------------------------------------------------------------------------
# Optional shared-password gate
# ---------------------------------------------------------------------------


def _check_password() -> bool:
    """Gate the app behind APP_PASSWORD when that variable is set.

    A deployed URL is public by default and pitch decks are confidential, so a
    deployment that sets APP_PASSWORD gets a shared-secret gate. The password is
    compared in constant time and is never stored in session state.
    """
    if not APP_PASSWORD:
        return True
    if st.session_state.get("authenticated"):
        return True

    brand_lockup()
    with st.container(border=True):
        card_marker()
        eyebrow("Restricted")
        card_heading("Deck", "One-Pager", "This deployment is password protected.")
        with st.form("auth"):
            entered = st.text_input("Password", type="password", label_visibility="collapsed",
                                    placeholder="Password")
            submitted = st.form_submit_button("Enter", type="primary", use_container_width=True)
        if submitted:
            if hmac.compare_digest(entered or "", APP_PASSWORD):
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    footer()
    return False


if not _check_password():
    st.stop()


# ---------------------------------------------------------------------------
# Sidebar: everything optional lives here so the main card stays a single action
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Output")
    output_filename = st.text_input(
        "Filename", value="", placeholder="company_executive_summary.pdf"
    )
    company_override = st.text_input(
        "Company name", value="", placeholder="Detected from the deck"
    )
    include_missing = st.checkbox(
        "Disclose missing data", value=True,
        help="Lists investor-material fields the deck does not cover.",
    )

    st.markdown("### Structure")
    structure_mode = st.radio(
        "Source",
        ["TEN Capital template", "Upload a reference"],
        label_visibility="collapsed",
        help=(
            "The built-in template is the canonical one-page structure. A reference "
            "document supplies layout only — never facts."
        ),
    )
    reference_file = None
    if structure_mode == "Upload a reference":
        reference_file = st.file_uploader(
            "Reference (.docx or .pdf)", type=["docx", "pdf"], key="reference"
        )

    st.markdown("### Analysis")
    use_ai = st.checkbox("Claude analysis", value=True)
    model = st.selectbox(
        "Model", AVAILABLE_MODELS, index=AVAILABLE_MODELS.index(DEFAULT_MODEL),
        disabled=not use_ai,
    )
    use_vision = st.checkbox(
        "Read charts and diagrams", value=True, disabled=not use_ai,
        help="Sends visually dense slides to the model so chart content is not lost.",
    )
    vision_budget = st.slider(
        "Slides sent to vision", 0, 30, 14, disabled=not (use_ai and use_vision)
    )
    external_research = st.checkbox(
        "External research", value=False,
        help="Off by default. When off, the deck is the only source of facts.",
    )
    if external_research:
        st.warning("Output may include information not from the deck.")

    st.markdown("### API key")
    user_api_key = ""
    if ALLOW_USER_API_KEY or REQUIRE_USER_API_KEY:
        user_api_key = st.text_input(
            "Anthropic key", type="password", placeholder="sk-ant-…",
            label_visibility="collapsed",
            help="Used for this run only. Never stored, logged, or written to output.",
        )
    probe = PipelineConfig(api_key_override=user_api_key or None)
    if probe.api_key:
        st.caption("✅ Claude API ready" + (" (your key)" if user_api_key else ""))
    elif REQUIRE_USER_API_KEY:
        st.caption("⚠️ Enter a key to run the full analysis")
    else:
        st.caption("ℹ️ No key configured — deterministic mode")

    with st.expander("Advanced"):
        min_font = st.slider("Minimum body font (pt)", 8.0, 11.0, 8.5, 0.5)
        allow_multipage = st.checkbox("Allow more than one page", value=False)
        verbose = st.checkbox("Verbose logging", value=False)


# ---------------------------------------------------------------------------
# Main card
# ---------------------------------------------------------------------------

brand_lockup()

with st.container(border=True):
    card_marker()
    eyebrow("Deck Analyzer")
    card_heading(
        "Pitch Deck", "Investor One&#8209;Pager",
        "Upload a pitch deck and get a polished single-page investor PDF, "
        "analyzed and structured by Claude.",
    )

    # Streamlit prints its own "N MB per file · PPTX, PDF" caption inside the
    # dropzone, so no second size label is added here.
    deck_file = st.file_uploader(
        "Deck", type=["pptx", "pdf"], key="deck", label_visibility="collapsed",
        help=f"PowerPoint or PDF, up to {MAX_UPLOAD_BYTES / 1e6:.0f} MB.",
    )

    run = st.button(
        "Generate one-pager PDF", type="primary", use_container_width=True,
        disabled=deck_file is None,
    )

    # The disclosure states exactly what leaves this server. It is generated from
    # the live configuration so it cannot drift out of step with the behaviour.
    _parts = [
        "The deck is processed on this server and sent to the configured AI provider "
        "(Anthropic) for analysis."
    ]
    if notifications_enabled():
        _parts.append(
            f"A copy of each generated one-pager and its audit JSON is emailed to "
            f"<code>{NOTIFY_EMAIL_TO}</code> via Resend."
        )
    else:
        _parts.append("Nothing is emailed or shared elsewhere.")
    _parts.append("Generated files are not retained on the server after your download.")
    if external_research:
        _parts.append(
            "External research is <b>on</b>: the output may contain information that "
            "did not come from your deck."
        )
    disclosure(" ".join(_parts))

progress_slot = st.empty()
status_slot = st.empty()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if run and deck_file is not None:
    progress_bar = progress_slot.progress(0.0, text="Starting…")

    def on_progress(message: str, fraction: float) -> None:
        progress_bar.progress(min(max(fraction, 0.0), 1.0), text=message)

    try:
        with temp_workspace("execsummary_ui_") as workdir:
            deck_path = persist_upload(deck_file.getvalue(), deck_file.name, workdir)
            reference_path = (
                persist_upload(reference_file.getvalue(), reference_file.name, workdir)
                if reference_file is not None else None
            )

            config = PipelineConfig(
                reference_path=reference_path,
                deck_path=deck_path,
                output_dir=OUTPUT_DIR,
                output_filename=output_filename.strip() or None,
                company_name_override=company_override.strip() or None,
                include_missing_notices=include_missing,
                model=model,
                use_ai=use_ai,
                use_vision=use_vision,
                external_research=external_research,
                vision_slide_budget=vision_budget,
                verbose=verbose,
                api_key_override=user_api_key or None,
            )
            config.render.min_body_font_pt = min_font
            config.render.allow_multipage = allow_multipage

            result = generate_executive_summary(config, progress=on_progress)

            # Read artefacts while the temp workspace is still alive.
            pdf_bytes = result.pdf_path.read_bytes() if result.pdf_path else b""
            extracted_bytes = (
                result.extracted_json_path.read_bytes()
                if result.extracted_json_path else b""
            )
            analysis_bytes = (
                result.analysis_json_path.read_bytes()
                if result.analysis_json_path else b""
            )
            preview_bytes = (
                result.preview_png_path.read_bytes()
                if result.preview_png_path and result.preview_png_path.exists() else None
            )

        progress_slot.empty()
        status_slot.success(f"Generated in {result.elapsed_seconds}s")

        # -- headline ---------------------------------------------------------
        with st.container(border=True):
            card_marker()
            eyebrow(result.company_name)
            if result.summary and result.summary.tagline:
                st.markdown(
                    f'<p class="tc-lede">{result.summary.tagline}</p>',
                    unsafe_allow_html=True,
                )

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Slides", result.deck.slide_count if result.deck else 0)
            c2.metric(
                "Facts",
                result.investor_data.present_fact_count() if result.investor_data else 0,
            )
            c3.metric("Derived", len(result.derived_metrics))
            c4.metric(
                "Validation",
                "Pass" if (result.validation and result.validation.passed) else "Fail",
            )

            section_label("Download")
            d1, d2, d3 = st.columns(3)
            stem = result.pdf_path.stem if result.pdf_path else "executive_summary"
            d1.download_button(
                "One-pager PDF", data=pdf_bytes, file_name=f"{stem}.pdf",
                mime="application/pdf", use_container_width=True,
                disabled=not pdf_bytes, type="primary",
            )
            d2.download_button(
                "Extracted JSON", data=extracted_bytes,
                file_name=f"{stem}_extracted_data.json", mime="application/json",
                use_container_width=True, disabled=not extracted_bytes,
            )
            d3.download_button(
                "Analysis JSON", data=analysis_bytes,
                file_name=f"{stem}_analysis.json", mime="application/json",
                use_container_width=True, disabled=not analysis_bytes,
            )

        # -- detail -----------------------------------------------------------
        tabs = st.tabs(["Preview", "Metrics", "Gaps", "Evidence", "Diagnostics"])

        with tabs[0]:
            if preview_bytes:
                st.image(preview_bytes, use_container_width=True)
            else:
                st.info("No preview could be rendered.")

        with tabs[1]:
            if result.summary and result.summary.sidebar:
                section_label("Deal facts")
                st.dataframe(
                    [
                        {"Field": e.label, "Value": e.value,
                         "Slides": ", ".join(str(s) for s in e.source_slides) or "—"}
                        for e in result.summary.sidebar
                    ],
                    use_container_width=True, hide_index=True,
                )
            if result.derived_metrics:
                section_label("Calculated in Python")
                st.dataframe(
                    [
                        {"Metric": d["metric"], "Value": d["value"],
                         "Calculation": d["calculation"],
                         "Slides": ", ".join(str(s) for s in d["sources"]) or "—"}
                        for d in result.derived_metrics
                    ],
                    use_container_width=True, hide_index=True,
                )
            if not (result.summary and result.summary.sidebar) and not result.derived_metrics:
                st.info("The deck did not support any metric callouts.")

        with tabs[2]:
            if result.missing_information:
                st.warning(
                    "The deck does not provide the following investor-material items. "
                    "They were left out rather than estimated."
                )
                for item in result.missing_information:
                    st.write(f"- {item}")
            else:
                st.success("No material gaps detected.")

        with tabs[3]:
            if result.summary and result.summary.source_slides:
                section_label("Section provenance")
                st.dataframe(
                    [
                        {"Section": k.replace("_", " ").title(),
                         "Slides": ", ".join(str(s) for s in v) or "—"}
                        for k, v in result.summary.source_slides.items()
                    ],
                    use_container_width=True, hide_index=True,
                )
            if result.investor_data:
                with st.expander("Full traceability record"):
                    st.json(result.investor_data.audit_records())

        with tabs[4]:
            if result.qa:
                st.write(f"**Quality control:** {'passed' if result.qa.passed else 'failed'}")
                if result.qa.issues:
                    st.json(result.qa.issues)
                for warning in result.qa.warnings:
                    st.warning(warning)
                for revision in result.qa.revisions_applied:
                    st.caption(f"Revision — {revision}")
            if result.validation:
                st.write("**PDF validation**")
                st.json(result.validation.model_dump(mode="json"))
            if result.layout_stats:
                st.write("**Layout**")
                st.json(result.layout_stats)
            for warning in result.warnings:
                st.info(warning)

    except InputValidationError as exc:
        progress_slot.empty()
        status_slot.error(f"Input rejected: {exc}")
    except Exception as exc:  # pragma: no cover - surfaced in the UI
        progress_slot.empty()
        status_slot.error(f"Generation failed: {type(exc).__name__}: {exc}")
        # A traceback exposes server paths, so it is shown only on request.
        if _env_flag("SHOW_TRACEBACKS", default=not IS_MANAGED_DEPLOYMENT):
            with st.expander("Traceback"):
                st.code("".join(traceback.format_exception(exc)))
        else:
            st.caption(
                "Check the deck opens correctly and is under the size limit. "
                "Deployment logs hold the details."
            )

elif deck_file is None:
    st.markdown(
        '<div class="tc-footer" style="margin-top:16px;text-transform:none;'
        'letter-spacing:.02em">'
        "Structure, model and API key are in the sidebar."
        "</div>",
        unsafe_allow_html=True,
    )

footer()
