"""TEN Capital Network brand system for the Streamlit app.

Streamlit's own theming covers only base colours, so the design is applied as a
single injected stylesheet that targets Streamlit's stable ``data-testid``
hooks. Everything here is presentation: no application logic lives in this
module, and nothing it renders affects what the generator produces.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

import streamlit as st

# The authoritative TEN Capital mark. Everything under static/ is generated from
# it by tools/make_icons.py.
MARK_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "ten_capital_mark.png"


@lru_cache(maxsize=1)
def mark_data_uri() -> str | None:
    """The mark as a base64 data URI, or None if the asset is missing.

    Inlining keeps the lockup working without static file serving and without a
    network round-trip, which matters because it renders before anything else on
    the page.
    """
    try:
        encoded = base64.b64encode(MARK_PATH.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:image/png;base64,{encoded}"

# ---------------------------------------------------------------------------
# Palette and type scale
# ---------------------------------------------------------------------------

BRAND = {
    "navy_950": "#0B1526",
    "navy_900": "#101E33",
    "navy_800": "#16283F",
    "navy_700": "#1E354F",
    "coral": "#EE5A4E",
    "coral_soft": "#F0776C",
    "amber": "#F3A22A",
    "teal": "#35BEBB",
    "ink_100": "#F3F6FA",
    "ink_300": "#C4D0E0",
    "ink_500": "#7E90A8",
    "ink_600": "#5C6E86",
}

# The three figures of the TEN Capital mark, inlined so the app has no external
# image dependency and renders identically offline.
BRAND_MARK_SVG = """
<svg class="tc-mark" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg"
     role="img" aria-label="TEN Capital Network">
  <path d="M50 6 C64 6 74 16 74 16" stroke="#F3A22A" stroke-width="11" stroke-linecap="round" fill="none"/>
  <path d="M76 66 C76 82 63 92 63 92" stroke="#35BEBB" stroke-width="11" stroke-linecap="round" fill="none"/>
  <path d="M24 66 C24 82 37 92 37 92" stroke="#EE5A4E" stroke-width="11" stroke-linecap="round"
        fill="none" transform="rotate(180 50 79)"/>
  <circle cx="50" cy="20" r="11" fill="#F3A22A"/>
  <circle cx="78" cy="68" r="11" fill="#35BEBB"/>
  <circle cx="22" cy="68" r="11" fill="#EE5A4E"/>
</svg>
"""

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700;800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');

:root{
  --navy-950:#0B1526; --navy-900:#101E33; --navy-800:#16283F; --navy-700:#1E354F;
  --coral:#EE5A4E; --coral-soft:#F0776C; --amber:#F3A22A; --teal:#35BEBB;
  --ink-100:#F3F6FA; --ink-300:#C4D0E0; --ink-500:#7E90A8; --ink-600:#5C6E86;
}

/* ---------- canvas ---------------------------------------------------- */

[data-testid="stAppViewContainer"],
[data-testid="stHeader"]{
  background: var(--navy-950);
}

/* Ambient tri-colour glow echoing the three figures in the mark. */
[data-testid="stAppViewContainer"]::before{
  content:"";
  position:fixed; inset:0; pointer-events:none; z-index:0;
  background:
    radial-gradient(480px 380px at 14% 8%,  rgba(238,90,78,0.16), transparent 60%),
    radial-gradient(480px 380px at 86% 6%,  rgba(243,162,42,0.13), transparent 60%),
    radial-gradient(560px 420px at 50% 100%, rgba(53,190,187,0.14), transparent 60%);
}

[data-testid="stHeader"]{ border-bottom: none; }
[data-testid="stToolbar"]{ right: 8px; }

html, body, [data-testid="stAppViewContainer"] *{
  font-family:'Inter', system-ui, sans-serif;
}

/* Streamlit draws its chrome with an icon font whose glyphs are ligatures. The
   rule above would otherwise force Inter onto them and print the ligature name
   ("upload", "keyboard_double_arrow_right") as literal text. */
[data-testid="stIconMaterial"],
span[class*="material-symbols"],
.material-symbols-rounded, .material-icons{
  font-family:'Material Symbols Rounded', 'Material Icons' !important;
}

[data-testid="stMainBlockContainer"], .block-container{
  position:relative; z-index:1;
  /* Streamlit's fixed header sits above the flow; clear it or the brand lockup
     is cropped by it. */
  padding-top: 4.6rem;
  padding-bottom: 3rem;
  max-width: 760px;
}

h1, h2, h3, h4{ font-family:'Sora', sans-serif !important; color: var(--ink-100); }
p, li, label, span, div{ color: var(--ink-300); }

/* ---------- brand lockup ---------------------------------------------- */

.tc-brand{
  display:flex; align-items:center; gap:12px;
  margin: 0 0 24px 2px;
}
.tc-mark{ width:34px; height:34px; flex-shrink:0; }
.tc-word{
  font-family:'Sora', sans-serif; font-weight:800; font-size:15px;
  letter-spacing:.04em; line-height:1.15; color:var(--ink-100);
  text-transform:uppercase;
}
.tc-word span{
  display:block; font-weight:600; font-size:10px; letter-spacing:.22em;
  color:var(--ink-500); margin-top:2px;
}

/* ---------- card ------------------------------------------------------- */
/*
 * Streamlit renames its layout test IDs between releases, so the card is
 * identified by a sentinel this app renders inside it (see card_marker) rather
 * than by a Streamlit-owned hook that could disappear on upgrade.
 */

[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .tc-card-marker){
  background: linear-gradient(180deg, var(--navy-900) 0%, var(--navy-800) 100%);
  border: 1px solid var(--navy-700) !important;
  border-radius: 20px !important;
  padding: 30px 34px 26px !important;
  box-shadow: 0 30px 60px -20px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.03);
  position:relative; overflow:hidden;
}

/* Tri-colour hairline across the top of every card. */
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .tc-card-marker)::after{
  content:""; position:absolute; top:0; left:34px; right:34px; height:2px;
  background: linear-gradient(90deg, var(--coral), var(--amber), var(--teal));
  border-radius:2px;
}

/* The sentinel itself never occupies space. */
.tc-card-marker{ display:none; }
[data-testid="stElementContainer"]:has(> .stMarkdown .tc-card-marker){
  display:none !important;
}

/* ---------- headings --------------------------------------------------- */

.tc-eyebrow{
  display:flex; align-items:center; gap:8px;
  font-family:'JetBrains Mono', monospace; font-size:11px;
  letter-spacing:.14em; text-transform:uppercase; color:var(--teal);
  margin: 0 0 12px;
}
.tc-eyebrow::before{
  content:""; width:6px; height:6px; border-radius:50%;
  background:var(--teal); box-shadow:0 0 0 3px rgba(53,190,187,.18);
}

.tc-h1{
  font-family:'Sora', sans-serif; font-size:28px; font-weight:700;
  line-height:1.25; letter-spacing:-.01em; color:var(--ink-100);
  margin:0 0 10px;
}
.tc-h1 .arrow{ color:var(--ink-500); font-weight:400; margin:0 4px; }
.tc-h1 .to{
  background:linear-gradient(90deg, var(--coral-soft), var(--amber));
  -webkit-background-clip:text; background-clip:text; color:transparent;
}
.tc-lede{
  color:var(--ink-300); font-size:15px; line-height:1.6;
  margin:0 0 24px; max-width:52ch;
}

.tc-label{
  font-family:'JetBrains Mono', monospace; font-size:11px;
  letter-spacing:.14em; text-transform:uppercase; color:var(--ink-500);
  margin: 4px 0 10px;
}

/* ---------- file uploader as the dropzone ------------------------------ */

[data-testid="stFileUploaderDropzone"]{
  border:1.5px dashed var(--navy-700) !important;
  border-radius:14px !important;
  background: rgba(255,255,255,.015) !important;
  padding: 26px 20px !important;
  transition: border-color .18s ease, background .18s ease;
}
[data-testid="stFileUploaderDropzone"]:hover{
  border-color: var(--teal) !important;
  background: rgba(53,190,187,.05) !important;
}
[data-testid="stFileUploaderDropzone"] small{
  font-family:'JetBrains Mono', monospace; color:var(--ink-500) !important;
  font-size:11.5px !important;
}
[data-testid="stFileUploaderDropzone"] span,
[data-testid="stFileUploaderDropzone"] div{ color: var(--ink-300); }
[data-testid="stFileUploaderDropzone"] button{
  background: transparent !important;
  border:1px solid var(--navy-700) !important;
  color: var(--ink-100) !important;
  border-radius:8px !important;
  font-weight:600 !important;
}
[data-testid="stFileUploaderDropzone"] button:hover{
  border-color: var(--teal) !important; color: var(--teal) !important;
}
[data-testid="stFileUploaderFile"]{ color: var(--ink-300); }

/* ---------- buttons ---------------------------------------------------- */

.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button{
  border-radius:12px !important;
  border:1px solid var(--navy-700) !important;
  background: rgba(255,255,255,.03) !important;
  color: var(--ink-100) !important;
  font-family:'Sora', sans-serif !important; font-weight:600 !important;
  transition: filter .15s ease, transform .15s ease, border-color .15s ease;
}
.stButton > button:hover, .stDownloadButton > button:hover{
  border-color: var(--teal) !important; color: var(--ink-100) !important;
}

/* The call to action carries the brand gradient, whichever widget renders it.
   Streamlit gives each button variant its own `kind`, so all the primary
   variants are matched rather than just the plain button. */
.stButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"],
.stDownloadButton > button[kind="primaryDownloadButton"],
.stFormSubmitButton > button[kind="primaryFormSubmit"],
button[data-testid="stBaseButton-primary"],
button[data-testid="stBaseButton-primaryDownloadButton"],
button[data-testid="stBaseButton-primaryFormSubmit"]{
  background: linear-gradient(90deg, var(--coral) 0%, var(--coral-soft) 45%, var(--amber) 100%) !important;
  color:#17130E !important;
  border:none !important;
  font-weight:700 !important;
  font-size:15px !important;
  padding: 14px 20px !important;
  box-shadow: 0 10px 24px -10px rgba(238,90,78,.45);
}
.stButton > button[kind="primary"]:hover,
.stDownloadButton > button[kind="primary"]:hover,
.stDownloadButton > button[kind="primaryDownloadButton"]:hover,
.stFormSubmitButton > button[kind="primaryFormSubmit"]:hover,
button[data-testid="stBaseButton-primary"]:hover,
button[data-testid="stBaseButton-primaryDownloadButton"]:hover{
  filter:brightness(1.06); transform:translateY(-1px); color:#17130E !important;
}
.stButton > button[kind="primary"]:disabled,
button[data-testid="stBaseButton-primary"]:disabled{
  filter: grayscale(.65) brightness(.75); box-shadow:none; transform:none;
}

/* ---------- disclosure, footer ----------------------------------------- */

.tc-disclosure{
  margin-top:18px; padding-top:16px;
  border-top:1px solid var(--navy-700);
  font-size:12px; line-height:1.6; color:var(--ink-500);
}
.tc-disclosure code{
  font-family:'JetBrains Mono', monospace; background:var(--navy-950);
  border:1px solid var(--navy-700); color:var(--ink-300);
  padding:2px 6px; border-radius:5px; font-size:11.5px;
}
.tc-footer{
  text-align:center; margin:22px 0 4px;
  font-family:'JetBrains Mono', monospace; font-size:11px;
  letter-spacing:.08em; color:var(--ink-600); text-transform:uppercase;
}

/* ---------- metrics ----------------------------------------------------- */

[data-testid="stMetric"]{
  background: rgba(255,255,255,.022);
  border:1px solid var(--navy-700);
  border-radius:12px; padding:12px 14px;
}
[data-testid="stMetricLabel"] *{
  font-family:'JetBrains Mono', monospace !important;
  font-size:10.5px !important; letter-spacing:.12em; text-transform:uppercase;
  color:var(--ink-500) !important;
}
[data-testid="stMetricValue"]{
  font-family:'Sora', sans-serif !important; color:var(--ink-100) !important;
}

/* ---------- sidebar ----------------------------------------------------- */

[data-testid="stSidebar"]{
  background: var(--navy-900);
  border-right:1px solid var(--navy-700);
}
[data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3{
  font-size:13px !important; letter-spacing:.1em; text-transform:uppercase;
  color:var(--ink-500) !important; font-family:'JetBrains Mono', monospace !important;
}

/* ---------- inputs ------------------------------------------------------ */

[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"]{
  background: var(--navy-950) !important;
  border-color: var(--navy-700) !important;
  border-radius:10px !important;
}
input, textarea{ color: var(--ink-100) !important; }

/* ---------- tabs -------------------------------------------------------- */

[data-testid="stTabs"] button{
  font-family:'Sora', sans-serif !important; color: var(--ink-500) !important;
}
[data-testid="stTabs"] button[aria-selected="true"]{
  color: var(--ink-100) !important;
}
[data-testid="stTabs"] [data-baseweb="tab-highlight"]{
  background: linear-gradient(90deg, var(--coral), var(--amber)) !important;
}

/* ---------- feedback ---------------------------------------------------- */

[data-testid="stAlert"]{
  border-radius:12px; border:1px solid var(--navy-700);
  background: rgba(255,255,255,.025);
}
[data-testid="stProgressBar"] > div > div > div{
  background: linear-gradient(90deg, var(--coral), var(--amber), var(--teal)) !important;
}
[data-testid="stExpander"]{
  border:1px solid var(--navy-700) !important; border-radius:12px !important;
  background: rgba(255,255,255,.02) !important;
}
hr{ border-color: var(--navy-700) !important; }
code{ color: var(--teal) !important; }

/* Tables and dataframes */
[data-testid="stDataFrame"]{ border:1px solid var(--navy-700); border-radius:12px; }

@media (max-width: 640px){
  [data-testid="stVerticalBlockBorderWrapper"]{ padding: 26px 20px 22px !important; }
  .tc-h1{ font-size:23px; }
}
</style>
"""


def inject_brand_css() -> None:
    """Apply the brand stylesheet. Safe to call once per script run."""
    st.markdown(_CSS, unsafe_allow_html=True)


def card_marker() -> None:
    """Mark the enclosing container as a brand card.

    Call this first inside ``st.container(border=True)``. The stylesheet keys the
    card treatment off this sentinel rather than a Streamlit test ID, so a
    Streamlit upgrade that renames its layout hooks cannot silently un-style the
    page.
    """
    st.markdown('<div class="tc-card-marker"></div>', unsafe_allow_html=True)


def brand_lockup() -> None:
    """The mark plus wordmark, top-left of the page.

    Uses the supplied logo asset when present and falls back to the drawn SVG, so
    the header still renders if assets/ is ever missing.
    """
    uri = mark_data_uri()
    mark = (
        f'<img class="tc-mark" src="{uri}" alt="TEN Capital Network">'
        if uri else BRAND_MARK_SVG
    )
    st.markdown(
        f'<div class="tc-brand">{mark}'
        f'<div class="tc-word">Ten Capital<span>Network</span></div></div>',
        unsafe_allow_html=True,
    )


def eyebrow(text: str) -> None:
    st.markdown(f'<div class="tc-eyebrow">{text}</div>', unsafe_allow_html=True)


def card_heading(lead: str, accent: str, lede: str | None = None) -> None:
    """Headline in the design's two-tone treatment, with an optional lede."""
    st.markdown(
        f'<div class="tc-h1">{lead}<span class="arrow">&rarr;</span>'
        f'<span class="to">{accent}</span></div>',
        unsafe_allow_html=True,
    )
    if lede:
        st.markdown(f'<p class="tc-lede">{lede}</p>', unsafe_allow_html=True)


def section_label(text: str) -> None:
    st.markdown(f'<div class="tc-label">{text}</div>', unsafe_allow_html=True)


def disclosure(html: str) -> None:
    st.markdown(f'<div class="tc-disclosure">{html}</div>', unsafe_allow_html=True)


def footer(text: str = "Powered by TEN Capital Network") -> None:
    st.markdown(f'<div class="tc-footer">{text}</div>', unsafe_allow_html=True)


def stat_row(items: list[tuple[str, str]]) -> None:
    """A compact row of label/value stats, styled like the metric callouts."""
    columns = st.columns(len(items))
    for column, (label, value) in zip(columns, items):
        column.metric(label, value)
