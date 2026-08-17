# Investor Executive Summary Generator

Converts a company pitch deck (PDF or PowerPoint) into a polished **one-page investor
executive summary PDF**, laid out to match a reference document you supply.

The reference summary supplies the **design**: page geometry, typography, colour, section
order, column structure, and text density. The pitch deck supplies **all of the facts**.
Nothing crosses between the two.

---

## Contents

- [What it does](#what-it-does)
- [Document structure template](#document-structure-template)
- [Architecture](#architecture)
- [Installation](#installation)
- [Environment variables](#environment-variables)
- [Running it](#running-it)
- [Deploying as a web app](#deploying-as-a-web-app)
- [Folder structure](#folder-structure)
- [Supported file formats](#supported-file-formats)
- [Hallucination controls](#hallucination-controls)
- [Output files](#output-files)
- [Testing](#testing)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)

---

## What it does

```
Reference summary ──► measured design template (geometry, type, colour, sections)
                                     │
Pitch deck ──► slide extraction ──► investor schema ──► evidence gate ──► Python metrics
                                     │
                       executive summary content model
                                     │
                       layout engine ──► ReportLab PDF ──► validation (technical + visual)
```

Each stage writes structured JSON, so an extraction problem can be diagnosed without
re-running the pipeline.

**Key properties**

- Every factual claim is tagged `EXPLICIT`, `DERIVED`, or `NOT_PROVIDED` and carries the
  slide numbers it came from.
- Financial arithmetic is done in Python, never by the language model.
- The output is exactly one page, with a selectable text layer and no clipped or
  overlapping content.
- Missing data is reported as missing. It is never estimated or filled from industry
  averages.

---

## Document structure template

The canonical one-page structure lives in [`templates/`](templates/) and is what the app
uses when no reference document is supplied.

| File | Role |
|---|---|
| `executive_summary_template.json` | The spec the app loads — geometry, typography, colour, section order, **and the content contract** |
| `executive_summary_template.pdf` | The same structure drawn with placeholders; also valid as `--reference` |
| `executive_summary_template_preview.png` | Visual reference |
| `TEMPLATE_STRUCTURE.md` | Field-by-field documentation |

It contains **no company data** — only placeholders and field definitions. A test suite
asserts this, failing if any company name or concrete figure appears in any template file.

### It defines fields, not just layout

Each section carries two contract fields beyond its geometry:

```json
{
  "key": "traction",
  "heading": "TRACTION & VALIDATION",
  "column": 1,
  "guidance": "The evidence that the thesis is working. Revenue, customers, pilots, …
               Distinguish committed revenue from interest, and label projections.",
  "source_fields": ["traction.revenue", "traction.arr", "customers.customer_count", …]
}
```

`guidance` is passed into the generation prompt as the section's brief; `source_fields`
names the investor-schema paths that supply its evidence. A test resolves every path
against the schema, so a typo cannot silently starve a section.

This contract is applied **even when a reference PDF is supplied** — the reference governs
layout, never meaning, so inference cannot quietly change what the app analyses.

### Structure

Derived from the supplied Word training document:

```
Title bar    [COMPANY NAME]  Company Overview & Executive Summary · rule
Masthead     ┌ sidebar ──────────────┬ narrative column ──────────────┐
             │ [COMPANY NAME] 16pt   │ Corporate Summary              │
             │ [tagline]             │ Key Facts      (1. 2. 3.)      │
             │ Stage / Raise / Terms │ Innovative Solution            │
             │ Industry / Reg. Path  │                                │
             │ Market Size / Use     │                                │
             │ Contact               │                                │
             │ Not stated in deck:   │                                │
             └───────────────────────┴────────────────────────────────┘
Body         Products & Platform            paragraph
             Target Markets                 – bullets
             Competitive Advantages         – bullets
             Commercial Validation          – bullets
             Projected Financials           table + italic caption
             Financing Opportunity          paragraph
             Use of Funds                   paragraph
             Key Management Team            – bullets
             Investment Thesis              paragraph
Footer       Confidentiality and non-verification notice
```

The masthead answers "what is this deal" at a glance; the body carries the argument in the
order an investor evaluates it. Gaps are disclosed in the sidebar under **Not stated in
source deck** — context for reading the page, not a section of the argument.

Bullets and paragraphs may open with a **bold lead-in label**, written in the content model
as `"Label: detail"`; the renderer sets everything before the colon in bold. Markdown is
stripped, so a model that reaches for `**bold**` cannot print asterisks on the page.

`approx_words` per section is a **relative weight**, not an absolute budget. The masthead
column and the body flow are budgeted as **separate regions** from measured page capacity,
so masthead prose never competes with the body for the same space.

### Using and editing it

```bash
# Default: no reference needed
python cli.py --deck input/company_deck.pdf

# Explicit template (overrides --reference)
python cli.py --deck input/company_deck.pdf --template templates/executive_summary_template.json

# Infer the layout from your own Word or PDF reference
python cli.py --deck input/company_deck.pdf --reference input/my_summary.docx
```

Word references are read structurally by `docx_analyzer` — styles, run properties, paragraph
spacing, indentation and tables are all stated explicitly in the file, so inference is far
more reliable than measuring a PDF. Whichever reference is supplied, it governs **layout
only**: the content contract (what each section must answer, and which schema fields feed
it) always comes from the canonical template, so inference cannot quietly change what the
app analyses.

To change the structure, edit the JSON, then regenerate the PDF and run the tests:

```bash
python tools/make_template.py
pytest tests/test_template.py -q
```

Section `key` values must come from: `investment_opportunity`, `metrics`, `problem`,
`solution`, `market_business_model`, `traction`, `competitive_position`, `team`,
`financing`, `risks`, `missing_material_information`.

---

## Architecture

```
ExecutiveSummary/
├── app.py                      Streamlit interface
├── cli.py                      Command-line interface
├── requirements.txt
├── .env.example
│
├── src/
│   ├── config.py               Runtime config, security limits, model list
│   ├── pipeline.py             End-to-end orchestration
│   │
│   ├── ingestion/
│   │   ├── document.py         Slide-level data model shared by both parsers
│   │   ├── pdf_parser.py       PyMuPDF extraction, font-size title inference
│   │   ├── pptx_parser.py      python-pptx extraction, recursive group traversal
│   │   ├── slide_renderer.py   Rasterises slides for the multimodal pass
│   │   └── loader.py           Format dispatch
│   │
│   ├── analysis/
│   │   ├── reference_analyzer.py  Measures a PDF reference into a TemplateSpec
│   │   ├── docx_analyzer.py       Reads a Word reference structurally
│   │   ├── deck_analyzer.py       Deck → investor schema (AI + heuristic paths)
│   │   ├── investor_schema.py     Pydantic investor data model
│   │   ├── metric_engine.py       Deterministic derived-metric calculation
│   │   ├── evidence.py            Classification and the hallucination gate
│   │   └── summary_generator.py   Content model, composition, QA agent
│   │
│   ├── ai/
│   │   ├── base.py             AIProvider interface + NullProvider
│   │   ├── anthropic_provider.py  Claude implementation
│   │   └── prompts.py          System prompts with grounding rules
│   │
│   ├── rendering/
│   │   ├── layout.py           Measurement, column balancing, fitting ladder
│   │   ├── pdf_generator.py    ReportLab vector/text rendering
│   │   └── pdf_validator.py    One-page, bounds, overlap, and visual checks
│   │
│   └── utils/
│       ├── files.py            Validation, safe naming, temp-file lifecycle
│       └── logging.py          Logging with credential redaction
│
├── templates/
│   ├── executive_summary_template.json   Canonical structure + content contract
│   ├── executive_summary_template.pdf    Placeholder rendering of that structure
│   └── TEMPLATE_STRUCTURE.md             Field-by-field documentation
│
├── tools/
│   ├── make_template.py           Renders the template PDF from the JSON
│   └── make_reference_sample.py   Generates a sample reference (see note below)
│
├── input/    output/    tests/
```

### Separation of concerns

| Stage | Owner | Never does |
|---|---|---|
| Ingestion | `src/ingestion` | Interpret meaning |
| AI analysis | `src/ai` | Arithmetic, or invent facts |
| Metrics | `src/analysis/metric_engine.py` | Call a model |
| Evidence | `src/analysis/evidence.py` | Produce prose |
| Writing | `src/analysis/summary_generator.py` | Position anything |
| Layout | `src/rendering/layout.py` | Change wording semantics |
| Validation | `src/rendering/pdf_validator.py` | Trust the layout engine's own numbers |

---

## Installation

**Python 3.11 or newer** (developed and tested on 3.14).

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Dependencies

| Package | Purpose |
|---|---|
| `pymupdf` | PDF parsing, slide rasterisation, output validation |
| `python-pptx` | PowerPoint parsing |
| `python-docx` | Word reference-document parsing |
| `reportlab` | Native PDF generation |
| `pydantic` | Schema validation across every stage |
| `anthropic` | Claude API client |
| `streamlit` | Web interface |
| `python-dotenv` | Loads `.env` |
| `Pillow` | Image handling for the optional OCR path |
| `pytest` | Tests |

**Optional extras**

- **LibreOffice** — required only to rasterise `.pptx` slides for the multimodal pass.
  Without it, PowerPoint decks are still fully parsed from their native text, tables,
  charts, and notes; only chart *images* go unread.
- **pytesseract + Tesseract** — an OCR fallback used only when a slide cannot be read by
  the model. Not installed by default.

---

## Environment variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | No* | Enables AI extraction, chart reading, writing, and QA review |
| `APP_PASSWORD` | No | Shared-password gate for a public deployment |
| `ALLOW_USER_API_KEY` | No | Show an API-key field so visitors can use their own credit |
| `REQUIRE_USER_API_KEY` | No | Hide the server key; every visitor must bring one |
| `MAX_UPLOAD_MB` | No | Per-file upload limit (default 80) |
| `MAX_DECK_PAGES` | No | Maximum slides accepted (default 120) |
| `OUTPUT_DIR` | No | Where generated files are written |
| `SHOW_TRACEBACKS` | No | Show tracebacks in the UI (off by default when deployed) |

\* Without a key the application still runs end to end, using heuristic extraction and
deterministic composition. The output is drier and thinner, but it is still one page and
still fully grounded.

### Anthropic API setup

1. Create a key at <https://console.anthropic.com/>.
2. Put it in `.env` as `ANTHROPIC_API_KEY=sk-ant-...`, or export it in your shell.
3. The key is read from the environment only. It never appears in source, in generated
   files, or in logs — the logger redacts anything credential-shaped before it is emitted.

Available models are listed in `src/config.py` (`AVAILABLE_MODELS`), defaulting to
`claude-sonnet-4-5-20250929`.

---

## Running it

### Streamlit

```bash
streamlit run app.py
```

Upload the reference summary and the pitch deck, set options in the sidebar, and press
**Generate Executive Summary**. The interface shows extraction progress, the company
identified, extracted metrics, missing information, a page preview, and download buttons
for the PDF and both JSON files.

### CLI

```bash
python cli.py \
  --reference input/reference_summary.pdf \
  --deck input/company_deck.pptx \
  --output output/executive_summary.pdf
```

| Flag | Default | Purpose |
|---|---|---|
| `--deck`, `-d` | required | Pitch deck (`.pdf` or `.pptx`) |
| `--reference`, `-r` | none | Reference summary PDF to infer layout from |
| `--template`, `-t` | canonical | Structure template JSON; overrides `--reference` |
| `--output`, `-o` | auto | Output path; defaults to `<company>_executive_summary.pdf` |
| `--output-dir` | `output/` | Directory for generated files |
| `--company` | detected | Override the detected company name |
| `--model` | `claude-sonnet-4-5` | Model to use |
| `--no-ai` | off | Fully deterministic: no network calls |
| `--no-vision` | off | Skip the multimodal pass over chart-heavy slides |
| `--no-missing-notices` | off | Omit the "information not provided" section |
| `--external-research` | off | Permit non-deck data (relaxes the grounding guarantee) |
| `--vision-budget` | 14 | Maximum slides sent to the multimodal model |
| `--min-font` | 8.5 | Body-font floor in points |
| `--allow-multipage` | off | Permit more than one page |
| `--verbose`, `-v` | off | Debug logging |

Exit codes: `0` success, `1` generation or validation failure, `2` input rejected.

---

## Deploying as a web app

Full instructions are in **[DEPLOYMENT.md](DEPLOYMENT.md)**. In short, for Railway:

1. Push the repo to GitHub (`.env`, `input/`, and `output/` are already git-ignored).
2. Railway → **New Project → Deploy from GitHub repo**. It reads `railway.json` and
   `nixpacks.toml` and builds automatically.
3. Set `ANTHROPIC_API_KEY` in the **Variables** tab. Do not set `PORT` — Railway injects it.
4. **Settings → Networking → Generate Domain**.

Committed deployment configuration:

| File | Purpose |
|---|---|
| `railway.json` | Start command, health check on `/_stcore/health`, restart policy |
| `nixpacks.toml` | Python 3.12, dependency install, optional LibreOffice |
| `Procfile` | Same start command, for any Procfile-based host |
| `.python-version` | Pins Python 3.12 |
| `.streamlit/config.toml` | Upload limits, XSRF protection, theme, no usage stats |

Two things to decide before exposing the URL:

- **Access.** A Railway URL is public, and every run spends *your* Anthropic credit. Set
  `APP_PASSWORD` for a shared-secret gate, or `REQUIRE_USER_API_KEY=true` so each visitor
  supplies their own key.
- **Storage.** Container filesystems are ephemeral. The app detects a managed deployment and
  writes to a temp directory, delivering files through the download buttons; nothing is
  retained server-side. That is also the safer default for confidential decks.

---

## Folder structure

```
input/
    reference_summary.pdf
    company_deck.pdf      (or .pptx)

output/
    company_executive_summary.pdf
    company_extracted_data.json
    company_analysis.json
    company_executive_summary_preview.png
```

Filenames are not hard-coded anywhere; both interfaces let you choose the files.

### A note on the sample reference

No reference document was supplied with this project, so
`tools/make_reference_sample.py` generates one:

```bash
python tools/make_reference_sample.py
```

It writes `input/reference_summary.pdf` for a **fictional** company ("Meridian Materials").
It exists only to establish visual design and information hierarchy. Replace it with your
own reference summary whenever you have one — nothing in the pipeline depends on it.

---

## Supported file formats

| Input | Formats |
|---|---|
| Reference summary | DOCX, PDF |
| Pitch deck | PDF, PPTX |

Extracted per slide: page/slide number, title, body text, bullets, tables, chart labels,
image captions, speaker notes (PPTX), quantitative metrics, and text inside grouped
PowerPoint shapes.

### Visual content

Decks put critical information in charts and diagrams, so text extraction alone is not
trusted. The pipeline:

1. extracts native text first;
2. scores each slide for visual density (image and vector coverage against text volume);
3. rasterises the densest slides;
4. sends them to the multimodal model, which reports only values it can read;
5. falls back to OCR only if that is unavailable and Tesseract is installed.

A chart value that cannot be read confidently is omitted rather than estimated.

---

## Hallucination controls

Seven independent layers:

1. **Classification.** Every value is `EXPLICIT` (stated in the deck), `DERIVED`
   (calculated in Python from explicit values), or `NOT_PROVIDED`.

2. **The evidence ledger.** Every number in the deck is indexed with its slide numbers.
   Any number appearing in generated prose must trace back to that index, or the sentence
   is rejected before it can be rendered. Scale-equivalent forms match, so `$750K` in the
   summary matches `$750,000` in the deck, while `50%` and `50` stay distinct.

3. **Python-only arithmetic.** Growth, CAGR, ARR/MRR conversion, margins, runway, LTV/CAC,
   market share, and remaining raise are computed in `metric_engine.py`. Each result stores
   its expression and source slides:

   ```json
   {
     "metric": "Revenue growth",
     "value": "50%",
     "calculation": "(3,000,000 - 2,000,000) / 2,000,000",
     "sources": [6],
     "classification": "DERIVED"
   }
   ```

4. **Quality-control agent.** Before rendering, the draft is checked for factual support,
   numeric consistency, misleading context (a projection presented as achieved revenue),
   promotional language, duplication, and investor relevance. Critical factual issues block
   rendering. Where the AI reviewer and the deterministic ledger disagree about grounding,
   **the ledger wins** — so a correct section is never silently deleted on the reviewer's
   say-so, and a fabricated one is never let through on it either.

5. **Invented-gap detection.** The mirror image of a fabricated fact is a fabricated
   *absence* — a section asserting "the deck does not provide information on the team"
   when the team slide was extracted successfully. The ledger cannot catch this, because
   a false absence claim contains no numbers. Each written section is therefore checked
   against the schema data backing it: if it claims silence while facts exist and none of
   those facts appear in the prose, the section is replaced with the deterministic version
   built directly from the extracted values.

6. **Language filter.** A banned-word list (revolutionary, massive, game-changing,
   world-class, disruptive, …) is stripped after generation *and* again after any QA
   revision, so a rewrite cannot reintroduce marketing copy.

7. **External research off by default.** With it off, the deck is the only source of facts.
   Turning it on is explicit, logged as a warning, and recorded in the analysis JSON.

**What happens to missing data:** it is listed under "Information not provided" (or omitted
entirely with `--no-missing-notices`). It is never estimated, benchmarked, or papered over
with vague phrasing.

---

## Output files

| File | Contents |
|---|---|
| `<company>_executive_summary.pdf` | The deliverable: one page, selectable text |
| `<company>_extracted_data.json` | Raw slide-level extraction, including vision results and density scores |
| `<company>_analysis.json` | Investor schema, content model, derived metrics, full traceability, QA result, layout stats, PDF validation |
| `<company>_executive_summary_preview.png` | Rendered page, used for visual validation and the UI preview |

Slide citations do not clutter the printed page — they are stripped from the prose and
preserved in the analysis JSON:

```json
{
  "field": "traction.arr",
  "claim": "$3.2M",
  "source_slides": [6],
  "classification": "EXPLICIT",
  "confidence": 0.95,
  "original_text": "ARR $3.2M across 47 customers"
}
```

---

## The one-page constraint

Overflow is never solved by shrinking type into illegibility. The layout engine escalates
in a fixed order:

1. shorten prose,
2. drop the lowest-priority sections (in investor-priority order: the investment case and
   metrics survive longest, "information not provided" goes first),
3. tighten spacing,
4. reduce type size — never below `--min-font` (default 8.5 pt, preferred 9–10.5 pt).

Word budgets are scaled to the page's measured capacity, using the reference for the
*relative* weight of each section, so a short reference does not leave the page
two-thirds empty.

After rendering, the PDF is reopened and independently checked for: exactly one page, a
real text layer, nothing outside the page bounds, no overlapping text, legible type,
margin compliance, a visible footer, and — from the rendered pixels — ink coverage and
whether content reaches the physical page edge. Failures trigger up to three automatic
layout revisions.

---

## Testing

```bash
pytest tests -q
```

178 tests, no network access and no external fixture files — sample PDF and PPTX decks are
synthesised at test time. Coverage includes PDF and PPTX parsing, malformed and empty
input, schema validation, metric accuracy, EXPLICIT vs. DERIVED classification,
missing-information handling, the hallucination gate, invented-gap detection, banned-word
stripping, citation stripping, template inference, PDF creation, the exact one-page
guarantee at four content volumes, required sections, output-directory behaviour, the
structure template (validity, schema-path resolution, and that it stays free of company
data), deployment configuration (start command binds `0.0.0.0:$PORT`, ephemeral output
paths, and that **an API key never reaches a log, a repr, or an output file**), and an
end-to-end assertion that **no ungrounded figure reaches the rendered page**.

Two production bugs were found by these tests during development and fixed: `parse_count`
read "47 customers" as 47,000,000 (the `m` in "customers" was being treated as a scale
suffix), and a case-sensitive number pattern meant `$5 Million` in a deck never matched
`$5M` in the draft, causing the evidence gate to reject true facts.

---

## Limitations

- **Company-name detection** prefers an email or web domain in the deck, then document
  metadata, then the title slide. Decks whose title slide carries only a marketing headline
  may need `--company`.
- **PPTX rasterisation needs LibreOffice.** Without it, text-based extraction still works
  but chart *images* in PowerPoint decks are not read.
- **Scanned decks** need Tesseract installed for the OCR fallback.
- **Deterministic mode** (`--no-ai`) produces a noticeably thinner page. It is a safety
  floor, not a substitute for the AI path.
- **Unit-based market sizes** ("TAM = 100 million devices") are deliberately excluded from
  currency metric callouts, so such a deck shows fewer callouts rather than a misleading one.
- **No independent verification.** Deck figures are reproduced as the company's claims,
  attributed accordingly, and flagged as unverified in the footer.
- **Reference inference is structural.** Multi-column magazine layouts, background images,
  and unusual typographic systems are approximated, not reproduced exactly.
- Reference documents are read as a design source from **page 1 only**.

---

## Troubleshooting

**`ANTHROPIC_API_KEY is not set`**
The app falls back to deterministic mode. Set the key in `.env`, or pass `--no-ai` to
silence the warning.

**Company identified incorrectly**
Pass `--company "Real Name"`, or set the override field in the Streamlit sidebar.

**"Rejected ungrounded value …" warnings**
Working as intended: the model produced a figure that is not in the deck, and it was
discarded. Check `<company>_analysis.json` if you believe the figure really is in the deck
— it may live in an unrasterised chart.

**Page looks sparse**
The deck did not yield much extractable content. Check `<company>_extracted_data.json`:
if `native_text_chars` is low across many slides, the deck is image-based and needs the
vision pass (ensure `ANTHROPIC_API_KEY` is set and `--no-vision` is not passed).

**Sections missing from the output**
They were dropped to hold one page, or removed by QA. `layout.dropped_sections` and
`quality_control` in the analysis JSON say which, and why.

**`LibreOffice not found` when processing a PPTX**
Only affects rasterising slides for the vision pass. Install LibreOffice, or convert the
deck to PDF first.

**Generation is slow**
The multimodal pass dominates runtime (roughly one call per dense slide). Lower
`--vision-budget`, or use `--no-vision` for a faster, text-only run.

**Validation fails after three attempts**
The layout could not satisfy every constraint. The PDF is still written; read
`pdf_validation.issues` in the analysis JSON, then try `--min-font 8.5` or
`--no-missing-notices` to free up space.
