# Investor Executive Summary — Document Structure Template

The canonical one-page structure the app generates, derived from the supplied Word
training document. This defines **layout and fields only**: no figure, name, or claim in
any template file may ever appear in a generated summary.

| Artefact | Role |
|---|---|
| `executive_summary_template.json` | The machine-readable spec the app loads (`TemplateSpec`) |
| `executive_summary_template.pdf` | The same structure drawn with placeholders; also usable as `--reference` |
| `executive_summary_template_preview.png` | Visual reference |
| `../tools/make_template.py` | Regenerates the PDF **through the real layout engine** |

Regenerate the PDF after editing the JSON:

```bash
python tools/make_template.py
```

Because the PDF is produced by the same engine as a real run, it is a faithful
specification rather than a hand-drawn approximation — change the JSON and the picture
changes with it.

---

## Page geometry

| Property | Value |
|---|---|
| Page size | US Letter, portrait (612 × 792 pt) |
| Margins | left 54, right 54, top 48, bottom 44 pt |
| Body columns | 1 (full width) |
| Page count | Exactly 1 (unless `--allow-multipage`) |

---

## Vertical order

```
┌────────────────────────────────────────────────────────────────┐
│ [COMPANY NAME]  Company Overview & Executive Summary   title bar│
│ ──────────────────────────────────────────────────────── rule   │
├──────────────────────┬─────────────────────────────────────────┤
│ [COMPANY NAME]  16pt │ Corporate Summary                       │
│ [tagline]        8pt │ Key Facts        (1. 2. 3. numbered)    │
│ Stage:   …           │ Innovative Solution                     │
│ Raise:   …           │                                         │
│ Terms:   …           │                                         │
│ Industry: …          │            ← masthead: sidebar + column  │
│ Reg. Path: …         │                                         │
│ Market Size: …       │                                         │
│ Use of Funds: …      │                                         │
│ Contact              │                                         │
│ Not stated in deck:  │                                         │
├──────────────────────┴─────────────────────────────────────────┤
│ ──────────────────────────────────────────────────────── rule   │
│ Products & Platform            paragraph                        │
│ Target Markets                 – bullets                        │
│ Competitive Advantages         – bullets                        │
│ Commercial Validation          – bullets                        │
│ Projected Financials           table + italic caption           │
│ Financing Opportunity          paragraph                        │
│ Use of Funds                   paragraph                        │
│ Key Management Team            – bullets                        │
│ Investment Thesis              paragraph                        │
├────────────────────────────────────────────────────────────────┤
│ ──────────────────────────────────────────────────────── rule   │
│ Confidentiality and non-verification notice                     │
└────────────────────────────────────────────────────────────────┘
```

The masthead answers "what is this deal" at a glance; the body carries the argument in
the order an investor evaluates it — product, market, advantage, evidence, numbers, deal,
team, thesis.

---

## Typography

| Element | Font | Size | Colour |
|---|---|---|---|
| Title-bar company name | Helvetica-Bold | 14 pt | `#1F4E79` |
| Title-bar document label | Helvetica | 9 pt | `#595959` |
| Masthead company name | Helvetica-Bold | 16 pt | `#1F4E79` |
| Tagline | Helvetica | 8 pt | `#595959` |
| Sidebar label | Helvetica-Bold | 8 pt | `#1F4E79` |
| Sidebar value | Helvetica | 8 pt | `#333333` |
| Section heading | Helvetica-Bold | 10 pt, sentence case | `#1F4E79` |
| Body copy | Helvetica | 9 pt, 1.22 line height | `#222222` |
| Bullet / numbered item | Helvetica | 8.5 pt | `#222222` |
| Bullet marker (en dash) | Helvetica | 8.5 pt | `#2E75B6` |
| Table header | Helvetica-Bold | 7.5 pt on `#1F4E79` | `#FFFFFF` |
| Table body | Helvetica | 7.5 pt | `#222222` |
| Caption | Helvetica | 7.5 pt | `#595959` |
| "Not stated" note | Helvetica | 7 pt | `#595959` |
| Footer | Helvetica | 6.8 pt | `#595959` |

No text class renders below **8.5 pt** for body copy; bullets converge to that floor
rather than dropping under it.

### Bold lead-in labels

Bullets and paragraphs may open with a bold label, written in the content model as
`"Label: detail"`. The renderer sets everything up to the colon in bold. A colon deeper in
a sentence is not treated as a label — the run must be short (≤ 7 words) to qualify.

---

## Masthead sidebar fields

Rows are grouped, with a blank line between groups. Absent values are omitted entirely;
nothing is shown as "N/A".

| Row | Group | Source | Content |
|---|---|---|---|
| Stage | 0 | `company.stage` | Financing stage as the deck describes it |
| Raise | 1 | `fundraising.raise_amount` | Amount and tranche structure |
| Terms | 1 | `fundraising.terms` | Cap, discount, interest, investor rights |
| Industry | 2 | `company.industry` | Sector and sub-sector |
| Reg. Path | 2 | `technology.regulatory_pathway` | Route and class; omitted if not applicable |
| Market Size | 2 | `market.tam` | Core market, plus adjacent verticals |
| Use of Funds | 2 | `use_of_funds.summary` | One phrase naming what the capital buys |
| **Contact** | — | `team.founders` / `company.website` | Name, title, email, phone |
| **Not stated in source deck** | — | `missing_information` | Investor-material gaps, semicolon separated |

The gaps disclosure lives here rather than in the body: it is context for reading the
page, not a section of the argument. Suppress it with `--no-missing-notices`.

---

## Sections

`approx_words` in the JSON is a **relative weight**. Absolute budgets are computed at
render time from measured page capacity, with the masthead column and the body flow
budgeted as separate regions so they do not compete for the same space.

### Masthead column

**Corporate Summary** — paragraph, weight 62. What the company does, the mechanism, the
customer, the milestone it targets, and the core market size. *Never dropped.*
`company.description` · `solution.*` · `market.tam` · `customers.buyer_persona` ·
`technology.regulatory_pathway`

**Key Facts** — numbered, weight 90. Two to four facts establishing that the problem is
real and large, each with a bold lead-in and quantified evidence.
`problem.*`

**Innovative Solution** — paragraph, weight 70. Why it works and why now.
`solution.*` · `technology.core_technology` · `technology.technical_validation` ·
`problem.urgency`

### Body

**Products & Platform** — paragraph, weight 128. Specification and status, then bold
lead-ins for business model, pricing and margins, and intellectual property.
`product.*` · `business_model.*` · `intellectual_property.*`

**Target Markets** — bullets, weight 60. Each bullet a segment with TAM/SAM/SOM and
methodology. Core market separated from adjacent verticals.
`market.*`

**Competitive Advantages** — bullets, weight 90. Operational, economic (with named
incumbents' cost position), and regulatory or IP advantages.
`competitive_advantage.*` · `technology.regulatory_pathway` ·
`intellectual_property.exclusivity`

**Commercial Validation** — bullets, weight 90. Diligence, named backers, category proof
points, distributor interest, letters of support, pilots and contracts. Committed revenue
distinguished from expressions of interest.
`traction.*` · `customers.*`

**Projected Financials** — table, weight 30, optional. Years as columns, metrics as rows;
only figures the deck states, never interpolated. Followed by an italic caption giving
units and inflection points. Column 0 is the row-label column and carries no heading.
`financial_metrics.projections` · `financial_metrics.revenue_history`

**Financing Opportunity** — paragraph, weight 96. Amount and purpose as a bold lead-in,
then instrument, tranches, cap, discount, interest, rights, capital closed, and a bold
`Exit:` lead-in.
`fundraising.*`

**Use of Funds** — paragraph, weight 72. What the capital buys in milestone order. If the
deck gives milestones rather than a line-item budget, say so rather than inventing an
allocation.
`use_of_funds.*` · `milestones.*` · `financial_metrics.runway`

**Key Management Team** — bullets, weight 96. One bullet per principal with a bold
name/credential/role lead-in, then experience and commitment. Closes with a bold
`Advisors:` bullet. *Must never claim the deck is silent on the team when members were
extracted* — enforced by the invented-gap check.
`team.*`

**Investment Thesis** — paragraph, weight 82, optional. The closing argument. Synthesis
only; introduces no fact not already stated above.
`company.description` · `market.tam` · `competitive_advantage.*` · `fundraising.raise_amount`

---

## Content prioritisation

When content exceeds one page, the engine shortens prose, then drops low-priority
sections, then tightens spacing, then reduces type size (never below 8.5 pt).

| Priority | Section |
|---:|---|
| 100 | Corporate Summary |
| 92 | Key Facts |
| 90 | Financing Opportunity |
| 88 | Innovative Solution |
| 86 | Commercial Validation |
| 82 | Target Markets |
| 78 | Products & Platform |
| 74 | Competitive Advantages |
| 68 | Key Management Team |
| 64 | Use of Funds |
| 58 | Projected Financials |
| 52 | Investment Thesis |

Note the tension this structure creates: a 7-row sidebar and a 6-row table consume a large
fixed share of the page, so the body sections are deliberately terse. A deck rich enough
to fill every section will see the lowest-priority ones trimmed or dropped, and the run
reports exactly which in `layout.dropped_sections`.

---

## Editing this template

1. Edit `executive_summary_template.json`.
2. Run `python tools/make_template.py` to refresh the PDF and preview.
3. Run `pytest tests/test_template.py -q`.

Section `key` values must come from `RENDERABLE_SECTION_KEYS` in
`src/analysis/reference_analyzer.py`:

`corporate_summary` · `key_facts` · `innovative_solution` · `products_platform` ·
`target_markets` · `competitive_advantages` · `commercial_validation` ·
`projected_financials` · `financing_opportunity` · `use_of_funds` · `management_team` ·
`investment_thesis` · `risks` · `missing_material_information` · `metrics`

Headings from older template revisions (`Traction`, `Team`, `Financing`, …) are folded
onto these keys by `SECTION_KEY_ALIASES`, so an existing reference document still
resolves rather than being silently dropped.

Section `style` values: `paragraph`, `bullets`, `numbered`, `table`, `metrics`.
Set `column: 2` to place a section in the masthead's narrative column.
