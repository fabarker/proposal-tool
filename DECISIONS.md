# Portfolio Analytics UI — Design Q&A Log

Running record of the collaborative design session for the new front-end UI package.
Every question asked and answer given is appended here in order, along with findings
derived from reading the existing codebase.

- **Project:** `epsilon-phi-core` (placeholder host; UI must be transplantable to a different Python project)
- **Started:** 2026-08-28
- **Status:** In progress — design discovery

---

## Table of contents

- [Brief & constraints](#brief--constraints)
- [Codebase recon findings](#codebase-recon-findings)
- [Q&A log](#qa-log)
- [Decisions locked](#decisions-locked)
- [Open items](#open-items)

---

## Brief & constraints

Fixed requirements stated up front by the product owner:

| # | Constraint |
|---|---|
| 1 | Delivered as a **new package inside an existing Python project** |
| 2 | **Portability is first-class** — the package will later be lifted into a *different* Python project. All project-specific naming, config, schemas and endpoint paths must sit behind an adapter boundary |
| 3 | **Plain HTML + JavaScript, no build step** |
| 4 | Interactive, reactive, dynamic front end |
| 5 | Starts as a **single page**, but architecture must scale to multiple pages without rework |
| 6 | **Responsive** across screen sizes |
| 7 | **Full production spec** — every screen documents responsive behaviour, accessibility, and all UI states (loading, empty, error, permissions) |
| 8 | Structural decisions to be reasoned through with tradeoffs, not assumed |

Deliverable: a single self-contained HTML specification document, detailed enough for a
web designer and a software engineer to build from directly.

---

## Codebase recon findings

Established by reading the repo, not by asking:

### Repository layout

- `epsilon-phi-core` — the analytics library. `epsilonPhi/core/{simulation, optimizer, portfolio,
  reporting, dataModel, schema, factor, estimator, timeSeries, lib}` plus `ep_saa`, `ep_strategies`, `research`.
- SQLAlchemy / MySQL backed (`SessionMgr`, `Configs`), config-driven per `currency` + `dataversion`.
- **No web layer of any kind.** No Flask, FastAPI, Dash, Streamlit. No `api`/`server`/`app.py` module
  anywhere in the package. Functionality is exposed purely as library calls.

### The lookup backend does not exist yet

- `configUtil.get_model_portfolio_assets()` — bare `pass` stub.
- `configUtil.get_reference_portfolio_assets()` — bare `pass` stub.
- Config tables (`currency_config`, `simulation_config`, `crises_config`, …) are keyed only on
  `currency` + `dataversion`. There is **no risk-level, allocation-type or model-portfolio store**.

**Consequence:** the parameter set and lookup query shape are a *design decision made in this session*,
and become part of the contract the host project must satisfy. This strengthens the adapter approach —
the UI defines the query shape, the host implements it.

### The output tables already exist in the reporting layer

`core/reporting/Reporting.py` already builds the exact artifacts the UI needs:

**`get_portfolios_table()`** — the allocation table.
- Rows: for each asset category in order, a category row carrying the category total weight,
  then its assets indented beneath (`"  " + reporting_name`), weights as %.
  Then `TOTAL`, blank spacer, `Estimated Mean Return`, `Sharpe Ratio`, blank spacer, `Volatility`.
- Columns: one per portfolio, concatenated side by side.
- Row sets are **unioned across portfolios and `fillna(0)`**.

**`get_risk_dashboard()`** — a three-section composite:

| Section header (literal row) | Source | Contents |
|---|---|---|
| **Factor Based Risk Analytics** | `get_portfolio_tbl()` | Category weights + Estimated Mean Return, Sharpe Ratio, Volatility (`TOTAL` dropped) |
| **Predicted Performance Over Stress Periods** | `get_crisis_tbl()` | One row per crisis period, from `get_factor_stress_tests()` |
| **Portfolio Risk Premia** | `get_var_pol_tbl()` | VaR 99%, CVaR 99%, Probability of Loss — each × {1 month, 1 year, 3 years} |

- Stress-period rows are **data-driven**, resolved via `get_crises_config()` filtered by date range.
  The table must render an arbitrary row set, not a fixed list.

### Available portfolio metrics

From `SAAPortfolio` / `Portfolio`:

- **Return** — `get_total_return()`, `get_current_env_total_return()`, `get_medium_return()`,
  `get_risk_premia()`, `get_current_env_risk_premia()`, `get_alpha()`
- **Risk** — `get_risk()`, `get_systematic_risk()`, `get_idio_variance()`, `get_risk_betas()`,
  `get_return_betas()`, `get_risk_decomposition_factor()`, `get_fx_risk_decomposition()`
- **Ratios** — `get_sharpe_ratio()`, `get_curr_env_sharpe_ratio()`, `get_implied_risk_aversion()`
- **Historical** — `get_historical_volatility()`, `get_historical_beta()`, `get_historical_max_drawdown()`,
  `get_historical_worst_peak_to_trough_loss()`, `get_worst_periodic_real_return()`

### Prior UI work — explicitly out of scope

- `model-management/mpo-ui` — a 15-screen dependency-free HTML/CSS/JS prototype for Model Portfolio
  Optimisation, with a `build.py` byte-concatenator.
- `epsilon-phi-core/simulation-dashboard.html` — a separate static mock on a warm/paper palette.

**Both ruled out by the product owner. Starting from scratch.**

---

## Q&A log

### Q1 — Which part of the codebase needs a UI first?

> Which part of `epsilon-phi-core` needs a UI first? What is the one workflow a user would come
> to this UI to do?

**A:** Portfolio construction, risk and return modelling, and reporting. **Simulation is not needed.**

The workflow: use a set of parameters to define and build a portfolio, view risk and return metrics,
and add comparison portfolios.

Expanded description of the flow:

> User lands on the page. Initiates a new scenario analysis. They have a set of fields they can select
> which collectively define a model portfolio — currency, risk level, asset allocation type,
> include/exclude real estate etc. Once all selected and submitted, the user sees a table on screen:
> first column is all the asset names, next column is the portfolio they just defined showing the
> weights, and at the bottom of the table return, volatility, Sharpe ratio, equity risk beta and a few
> other metrics. The user can then click "add comparison portfolio" which opens a dialogue/card with
> the same set of parameters to define another portfolio.

---

### Q2 — What produces the weights on submit?

> When the user hits submit, does a **lookup** return a pre-built model portfolio (milliseconds),
> or does a **solve** run the optimiser on demand (seconds to minutes, needs a job model,
> can fail to converge)?

**A:** A lookup. The parameter combination selects an existing model portfolio / stored allocation
that has already been built. Returns in milliseconds. Submit is effectively a filtered query.

**Consequence:** no job model, no progress/cancellation UI, no infeasibility state.
Synchronous request/response throughout.

---

### Q3 — What is the complete selector field set?

> For each field: label, control type, option values, whether required, and whether any are
> dependent on each other.

**A:** Cascading logic is definitely needed — some fields are disabled until others are selected,
and some are disabled under certain parameter sets.

- **Currency:** CHF, USD, GBP, EUR
- **Risk Level:** Low Vol, Cons, Cons Mod, Mod, Mod Agg, Agg, All Equity
- **AA Type:** Full, Core, Ex HFs, Ex Alts
- **Include RE:** True only when AA Type is Full or Ex HFs; otherwise False

---

### Q4 — Is that the complete field set, and what is the full enablement chain?

**A:** Additional fields:

- **Hedging Policy:** Hedged, ISG Hedged, Unhedged, Equity Not Hedged
- **Top Account Size:** float, dollars
- **Mandate Size:** float, dollars
- **Primary PWA:** string

Anything below $5 million is rejected and a pop-up appears.
These are mandatory and all other fields are greyed out until they are filled in.

---

### Q5 — Gate semantics, the $5m rule, and Primary PWA control type

> Is Hedging Policy part of the gate or the model definition? Which amount does the $5m floor apply
> to, and is it a hard block? Is Primary PWA free text or a lookup? Is there a relationship between
> Top Account Size and Mandate Size?

**A:**
- **Hedging Policy is model-related**, not part of the gate.
- The **$5m floor is on Mandate Size**, and it is a **hard block**.
- **Primary PWA is a lookup typeahead search.**
- **Mandate Size is always equal to or less than Top Account Size.**

---

### Q6 — What is the complete metrics band, and is Nominal/Real in scope?

**A:** The CVaR / Probability of Loss table (`get_var_pol_tbl()`) should also be on screen as a table.

Category-grouped rows with subtotals, and union-with-zero-fill, are both confirmed as wanted.

---

### Q7 — Which metrics make up the band, and where does Nominal/Real apply?

**A:** Park equity risk beta for now. The **Nominal/Real split is confined to the risk table**,
which is called the **risk dashboard**.

---

### Q8 — Is the on-screen risk dashboard the full three-section composite?

> `get_var_pol_tbl()` is only the VaR block; `get_risk_dashboard()` is the superset with three
> sections. Which is on screen?

**A:** The full three-section composite.

---

### Q9 — How do comparison portfolios behave?

> How many, can they be edited/removed/reordered, is the first privileged, how are columns labelled?

**A:**
- **3 comparison models max** — 4 portfolios total.
- The **first portfolio is a base/privileged portfolio**, always in the first column;
  others are compared against it.
- **No diffs or deltas required.**
- **Portfolio names are not editable.** The full name is built from the fields selected:
  `{currency} {aa_type} {risk_level}` plus `"ex RE"` when real estate is excluded.

**Consequence:** the risk dashboard tops out at **8 data columns** (4 portfolios × Nominal/Real).

---

### Q10 — Transport: does the environment allow a long-running Python process?

> Where does this run, is a server process acceptable, and are users concurrent?
> Proposed a `Gateway` module with swappable adapters (HTTP / fixtures / embedded shell),
> and a server-provided `getFormSchema()` so cascade rules are data, not JavaScript.

**A:** Eventually a long-running Python process will serve this front end. In the current testing
build phase it is connected to a quant's laptop, but it will be on a running server process.
**Concurrent users are important — separate session/thread per user.**

**Also, a correction to the risk dashboard structure:**

> Only the crisis periods and the value-at-risk numbers have Nominal and Real. The first block
> with the category weights, return, vol, Sharpe are **single columns and must be merged across
> the two following columns below it**.

So the risk dashboard has a **mixed column span** — per portfolio it is a 2-column group,
and section 1 rows span both:

```
                          |    USD Core Mod (base)    |     USD Full Agg      |
                          |   Nominal   |    Real     |  Nominal  |   Real    |
 Factor Based Risk Analytics
   Equity                 |     32.0%  (colspan 2)    |   28.0%  (colspan 2)  |
   Estimated Mean Return  |      6.2%  (colspan 2)    |    5.8%  (colspan 2)  |
 Predicted Performance Over Stress Periods
   GFC 2008               |   -28.1%    |   -30.4%    |  -25.2%   |  -27.1%   |
 Portfolio Risk Premia
   VaR 99% · 1 Month      |    -8.2%    |    -8.9%    |   -7.4%   |   -8.0%   |
```

---

### Q11 — How are users identified, and do they have different capabilities?

> Authentication, roles, and whether scenarios survive refresh / can be shared.

**A:** Single user type for now. Session states are persistent — should be able to send colleagues
a link, refresh and get it back if wanted.

---

### Q12 — What does the user see when they arrive with no scenario?

> Empty form or a list of saved scenarios? What identifies a scenario? Is persistence automatic
> or explicit? What does a colleague opening a link get?

**A:** The landing state is a parameter form. **No list of saved scenarios.**
**Link sharing is deferred** — ignore for now.

Also instructed: all questions and answers are to be recorded in a markdown file, appended as
the session continues. (This document.)

### Q13 — What is the visual direction?

> Is there an existing brand or design system? Who sees this screen — internal only or client-facing?
> Density: compact or comfortable? Light, dark, or both? Any references?

**A:** No existing design framework. It needs to be **exceptionally high quality and professional,
for client-facing use**. Rather than specifying a direction, produce **20 example HTML files** of
differing brands/visuals/styling in a new directory, to review and pick a specific direction from.

**Delivered:** `epsilon-phi-core/ui-style-explorations/` — 20 self-contained directions plus an
`index.html` gallery and a `build_styles.py` generator. Every direction renders the *same screen with
the same data* (parameter form with the cascade visible, allocation table, three-section risk
dashboard with mixed column span, three portfolios) so the comparison is purely aesthetic.

Also clarified: the landing state is the parameter form and it **should** be aesthetically pleasing
— read as a typo in the original answer, pending confirmation.

**Status:** awaiting a direction choice. Answers may combine directions
(e.g. "14 but denser, with the numerals from 04").

---

### Q13a — Round one review

**A:** Directions **14 (Signal)** and **19 (Aurora)** are the only two with promise. Delete the rest.
Create 20 more iterating on those two. In addition, read
`epsilonPhi.core.reporting.Reporting.generate_report()`, learn the styles and formatting of the
Excel output, and create an HTML style example matching it.

*(Instruction said "iterate on 14 and 20"; read as 14 and 19, the two named as promising.
Flagged for confirmation.)*

**Delivered:** round two in `ui-style-explorations/` — 23 directions.
01–02 retained baselines · 03–12 Signal lineage · 13–18 Aurora lineage · 19–22 hybrids ·
23 derived from the Excel report.

#### Excel report formatting — extracted specification

From `core/reporting/formatting_portfolios.yaml` and `formatting_risk.yaml`, which drive
`write_portfolios()` and `write_risk_dashboard()`. **This is a real house style and should inform
the final table specification regardless of which direction is chosen.**

| | Portfolios sheet | Risk sheet |
|---|---|---|
| Typeface | Calibri Light 12.5 | Aptos Narrow 12.0 |
| Header row | white fill, dotted bottom border, height 37 | `#092532` fill, white text, height 20 |
| Category row | fill `#D3DDEA`, height 17 | bold, colour `#092539`, dotted top border `#A9A9A9` |
| Asset row | white fill, number format `0.0` | white fill, indent 1, `0.0%` |
| Total row | white, dotted top border, height 23, `0.0%` | — |
| Metrics band | fill `#0F243E`, white text, height 23, `0.0%` | — |
| Number alignment | right, indent 4 | centred |
| Spacer rows | 2–3px between metric rows | — |
| Column widths | A 40, B 18 | A 40, B 15 |

Palette: `#0F243E` deep navy · `#092532` near-black navy · `#D3DDEA` pale blue-grey ·
`#A9A9A9` dotted rules · white ground.

Note the asset rows use number format `0.0` while category and total rows use `0.0%` — consistent
with `get_portfolios_table()`, which multiplies asset weights by 100 but leaves category totals as
fractions.

**Status:** awaiting direction choice from round two.

---

### Q13b — Learn the MPO UI branding

**A:** Look through `model-management/mpo-ui`, learn the visual branding and styling, and build a new
`ui-style-explorations` mock based on it.

*(Note: `mpo-ui` was ruled out as a design source at Q1. This reinstates it as a branding reference
only — its screens and IA remain out of scope.)*

**Delivered:** direction **24 · Console**.

#### MPO UI design system — extracted specification

From `model-management/mpo-ui/assets/app.css`. **This is the closest thing the organisation has to an
existing design system and should be treated as a real constraint on the final spec.**

| Token | Value | Role |
|---|---|---|
| `--navy` | `#16243a` | appbar, section bands, segmented-control active |
| `--navy2` | `#1d2f4b` | active nav item |
| `--canvas` | `#eceef1` | page ground |
| `--panel` | `#ffffff` | cards |
| `--line` | `#d4dae2` | structural rule |
| `--line2` | `#eef1f4` | per-row rule |
| `--ink` / `--mut` | `#1c2733` / `#5b6b7c` | text / muted text |
| `--blu` / `--blubg` | `#1f5fbf` / `#e3ebfa` | primary action / info tint |
| `--grn` / `--grnbg` | `#1a7f37` / `#e7f4ec` | positive |
| `--red` / `--redbg` | `#b42318` / `#fde8e8` | negative |
| `--amb` / `--ambbg` | `#b45309` / `#fef3c7` | warning |

Structural conventions:

- **Typeface:** system stack only, no webfont. 14px base, 12.5px chrome, 12px data.
- **Panels:** 6px radius, `1px solid #c9d1da`, `0 1px 3px rgba(16,24,40,.08)`, `overflow:hidden`.
- **Appbar:** navy, white bold title, status chips (`.chip.attr/draft/pub/batch/ext/int`).
- **Toolbar:** `#f7f8fa` strip beneath the appbar with a bottom border.
- **Vitals strip:** `#101b2d`, white tabular figures, `#2a3c57` dividers, green/amber status words.
- **Tables:** `th` 10.5px uppercase `.06em` muted on `#f0f2f5`; `td` `5px 10px` with `--line2`
  bottom rule; **`tr.grp` `#e9edf2` bold** — maps directly onto our category subtotal rows;
  hover `#f7f8fa`; `tabular-nums` throughout; sticky first column with a right border.
- **Badges:** pill, 10.5px/700, tinted background with a matching darker border
  (`.b-ok`, `.b-warn`, `.b-breach`, `.b-bind`, `.b-slack`, `.b-skip`, `.b-run`).
- **Buttons:** 4px radius, 12.5px/600, `.pri` blue, `.dng` white-on-red-border, `.dis` 45% opacity.
- **Segmented control:** `.seg`, navy active segment.

**Two existing house styles now conflict** and this needs a decision: the Excel report is
navy `#0F243E` / pale blue `#D3DDEA` / Calibri Light, while the MPO UI is navy `#16243A` /
`#E9EDF2` / system stack. They are close but not identical. Directions 23 and 24 each follow one
faithfully.

**Status:** awaiting direction choice.

---

### Q13c — Porcelain with a left parameter rail

**A:** Take the styling of Porcelain (21) but put the parameter block in a sidebar on the left, in
the same way as model-management.

**Delivered:** direction **25 · Porcelain Rail**.

Layout pattern taken from `mpo-ui` `#shellnav`: fixed to the viewport, full height, own scroll,
brand block at top, uppercase group label, main content offset by the rail width.

Two deliberate departures, flagged for confirmation:

- **324px rather than the original 236px** — the mpo-ui rail carries nav links, this one carries
  form controls, which are not legible at 236.
- **Pale rather than navy** — a navy rail contradicts Porcelain's thesis (contrast removed from
  the chrome, kept in the data). A navy variant is a two-token change if wanted.

Rail collapses to a stacked block below **1040px**.

**Implication for the spec if this layout is chosen:** the parameter form becomes persistent rather
than a step the user passes through, which changes the interaction model — parameters stay
visible and editable alongside the results, and "Add comparison portfolio" could reuse the rail
rather than opening a dialogue. That is a genuine product decision, not just a layout one, and needs
a separate answer.

**Status:** awaiting direction choice.

---

### Q13d — Use the model-management blues and navys

**A:** The rail layout is a step in the right direction, but use the blues and navys from
model-management.

**Delivered:** direction **26 · Porcelain Navy** — Porcelain Rail repainted in the `mpo-ui` palette,
with the rail becoming the `#shellnav` navy.

| Element | Value | Source in app.css |
|---|---|---|
| Rail ground | `#16243a` | `--navy` |
| Rail ink | `#ffffff` / `#b6c2d2` / `#7e90a8` | shellnav brand / links / sub |
| Rail dividers | `#233754` | shellnav borders |
| Rail input fill | `#1d2f4b` | `--navy2` |
| Document accent | `#1f5fbf` | `--blu` |
| Category rows | `#e9edf2` | `tr.grp` |
| Base chip | `#e3ebfa` / `#b9cdf0` / `#1e4fa3` | `.chip.attr` |
| Rules | `#d4dae2` structural, `#eef1f4` per row | `--line` / `--line2` |

#### Two house values had to be adjusted for accessibility

A full WCAG audit of the palette found two failures on the dark rail. **These adjustments should
carry into the final spec.**

| | House value | Used | Reason |
|---|---|---|---|
| Rail input border | `#2e4468` | `#52739c` | 1.59:1 on the navy — fails 1.4.11 Non-text Contrast (3.0 required). Now 3.18:1. |
| Rail primary button | `#1f5fbf` | `#2a6ad0` | `#1f5fbf` is 2.56:1 against the navy ground. `#2a6ad0` is the only candidate clearing both white-text 4.5 (5.16:1) and 3.0 against the rail (3.02:1). |

`#1f5fbf` is retained as the document accent, where it measures 6.09:1 on white.
All 14 audited pairs pass.

**Status:** awaiting confirmation that this is the direction.

---

### Q13e — Use the full mpo-ui `:root` token set

**A:** Use these stylings if not already being used — the complete `:root` block from
`mpo-ui/assets/app.css` (18 tokens).

**Audit before the change:** 10 of 18 were in use. Missing: `--canvas`, `--grn`, `--grnbg`,
`--redbg`, `--amb`, `--ambbg`, `--spec`, `--specbg`.

**Delivered:** direction 26 updated so all 18 carry real work rather than sitting declared:

| Token | Now used for |
|---|---|
| `--canvas` `#eceef1` | page ground (was `#f4f6f8`) |
| `--grn` / `--grnbg` | positive figures, `.b-ok` badge "Lookup matched" |
| `--red` / `--redbg` | negative figures, `.b-breach` badge "Below minimum" |
| `--amb` / `--ambbg` | `.b-warn` badge "Real estate excluded — AA Type is Core" |
| `--spec` / `--specbg` | annotation layer, exactly as mpo-ui uses it |

Two structural additions carry them: a **notices strip** beneath the header, and a
**semantic-palette block** at the foot. Both show truthful content for the parameters displayed.

This also imports mpo-ui's full badge vocabulary — `.b-ok`, `.b-warn`, `.b-breach`, `.b-bind`,
`.b-slack` — which **should be adopted wholesale in the final spec** as the status system, since it
already covers the states the brief requires.

#### Accessibility

**22 of 22 audited pairs pass**, including all five badges, the annotation block and the dark rail.

One accepted exception: the `.spec` dashed border (`#d4a017` on `#fffbeb`) is 2.29:1, below the 3.0
for non-text contrast. Retained at the house value because `.spec` is the *annotation* layer —
mpo-ui toggles it off with "Hide annotations" and it never ships in the product. If any of it
becomes product chrome the border must be darkened.

**Status:** awaiting confirmation that this is the direction.

---

### Q13f — Select fields showing a pattern fill

**A:** The dropdown selects (Hedging Policy, Currency, etc.) are rendering a strange repeating
pattern.

**Cause:** a CSS shorthand bug in the rail layout, not a rendering artefact. The rail rule used the
`background` **shorthand**:

```css
.rail input[type=text],.rail select{background:var(--rail-input-bg);…}
.rail select{background-image:var(--rail-caret);}
```

The `background` shorthand resets `background-repeat` to `repeat` and `background-position` to
`0% 0%`. The base `select` rule sets both explicitly, but it appears *earlier* in the sheet, so the
reset won — leaving the 11×7 chevron SVG tiling across the whole control.

**Fix:** use `background-color` in the rail rule instead of the shorthand, and restate
`background-repeat` / `background-position` on `.rail select`. Also added `background-size:11px 7px`
to the base rule so a future theme override cannot reintroduce it.

**Affected:** directions 25 and 26 only (the two rail layouts). Verified across all 26 by checking
that the rule restoring `no-repeat` always appears after any rule applying a `background`
shorthand to a select — 0 files tile.

**For the spec:** never set `background` shorthand on a control that carries an indicator image.
Worth an explicit note in the implementation section, since it is a silent failure — the control
still works, it just looks broken.

---

### Q14 — Add comparison portfolio, up to 3 in one go

**A:** Currency and Hedging Policy are **fixed** across all portfolios. Risk Level, AA Type and
Include RE all vary — all combinations of those three are available.

**Consequence:** a portfolio varies on three fields, not eight. Because Include RE is valid only for
Full and Ex HFs, there are exactly six allocation variants:

`Full + RE` · `Full` · `Ex HFs + RE` · `Ex HFs` · `Core` · `Ex Alts`

Six variants × seven risk levels = **42 combinations per scenario**. Small enough to show entirely.

**Delivered:** direction **27 · Porcelain Navy · Matrix** — a working prototype, built as a new file
so 26 can be reverted to.

#### Why a matrix rather than a form

1. **It removes the cascade.** Include RE becomes part of the row label rather than a dependent
   toggle that disables and forces itself False. No disabled state, no re-validation, nothing for an
   implementer to get subtly wrong. This retires the complexity introduced at Q3.
2. **It retires the no-match error state.** The available set is fetched up front and unavailable
   combinations render as `×`. The user cannot submit a query that returns nothing. One request
   removes an entire failure mode from the spec.
3. **Pick order is numbered**, so column order is predictable rather than emergent.

#### Rail information architecture, revised

| Tier | Fields | Scope |
|---|---|---|
| Mandate | Top Account Size, Mandate Size, Primary PWA | identifies the client |
| Scenario basis | Currency, Hedging Policy | fixed across all columns |
| Portfolios | the 6×7 matrix | base + up to 3 |

#### Verification

- All 34 available combinations: weights sum to 100.0, no negative weights, RE appears only in RE
  variants, return and volatility monotonic in risk level.
- Risk-dashboard colspan arithmetic correct at 1, 2, 3 and 4 portfolios and after removal
  (`n*2+1` columns per row, `n*2` for the Nominal/Real strip).
- Cells blocked by the cap are styled distinctly from cells that do not exist.

#### Two things this surfaced

1. **Naming ambiguity.** *Resolved at Q14c: `ex RE` only for Full and Ex HFs.*
2. **Are Core and Ex Alts actually different?** *Resolved at Q14d: Core holds hedge funds, Ex Alts
   holds none.*

**Also still open:** editing the base after comparisons exist (rebuild all / drop all / block), and
whether the base can be swapped by promoting a comparison.

---

### Q14a — Not sure about the matrix; build all the alternatives

**A:** Build all five alternatives as new directions from 26.

**Delivered:** directions **27–32**, six pickers on identical styling and **one shared engine**
(`pickers.py`) — same data model, table renderer, cap and removal logic. Only the picker differs.

| # | Picker | Character | Rail space | Availability visible |
|---|---|---|---|---|
| 27 | Matrix | 6×7 grid of every combination | high | full |
| 28 | Slots | Three collapsible slots, two selects each | high | in the select |
| 29 | Queue | One pair of selects, queue and repeat | low | in the select |
| 30 | List | All 42, grouped and type-to-filter | medium | full |
| 31 | Axis | Vary one axis, tick up to three | low | full |
| 32 | Table | `+` column opens a popover | none | in the popover |

#### The portable idea

Whichever picker wins, **AA Type and Include RE collapse into a single six-option Allocation axis**:

`Full + RE` · `Full` · `Ex HFs + RE` · `Ex HFs` · `Core` · `Ex Alts`

This removes the cascade entirely — no dependent toggle, no forced-False, no re-validation on
change. **This should go into the spec regardless of the picker chosen.** It retires the complexity
introduced at Q3.

#### Verification

All six execute under a DOM shim without throwing and render populated tables. State machine driven
end to end on the shared core:

- cap enforced at 3 queued; a fourth pick is refused
- unavailable combinations refused
- duplicates of the base and of existing portfolios refused
- commit moves the queue into columns and clears it
- removal frees a slot; the base cannot be removed
- risk-dashboard colspans correct at 1, 3 and 4 portfolios and after removal
  (`n*2+1` per row, `n*2` for the Nominal/Real strip)

**Status:** awaiting a pick.

---

### Q14b — Picker chosen: Table, with adding done in the popover

**A:** Go with the **Table** picker (32), but the act of adding happens on the popover — instead of
the blue Queue button it should be "Add to table" or similar.

**Delivered:** 32 rewritten for **direct add**. No queue step.

| Before | After |
|---|---|
| Popover "Queue" → rail preview → blue "Add portfolios" | Popover **"Add to table"** → column appears at once |
| Rail held preview + Add button | Rail holds mandate, scenario basis and built list only |

Behaviour:

- **Add to table** inserts the column immediately, scrolls both tables to reveal it, and highlights
  the new column header for 1.4s (suppressed under `prefers-reduced-motion`).
- The popover **stays open** with the risk select cleared, so three comparisons are three presses
  without leaving it. This is how "up to 3 in one go" is satisfied without a queue.
- At the cap the popover replaces its controls with an explanation and points at the rail to free a
  slot. The `+` column disappears and returns when one opens.
- Focus returns to the `+` button on close; Escape closes.

#### Verification

All six pickers still run. Direct-add flow driven end to end:

- three sequential adds succeed; the fourth is refused
- unavailable combinations, duplicates and base duplicates refused
- `+` column present below the cap, absent at it, and returns after a removal
- allocation table width accounts for the `+` column (`n+2` with it, `n+1` without)
- risk-dashboard colspans correct at every count

**Still open:** the `ex RE` naming rule for Core / Ex Alts, whether Core and Ex Alts genuinely differ
in the real data, and what happens when the base or scenario basis is edited after comparisons exist.

---

### Q14c — Naming rule, Core vs Ex Alts, rebuild, and the missing mandate controls

**A:**
1. `ex RE` applies **only to Full and Ex HFs** — Core and Ex Alts never carry it.
2. Core and Ex Alts **do** differ in the real data.
3. Editing the base or scenario basis **rebuilds**.
4. In 32 there is nowhere to set the mandate or the allocation — compare against 26.

**Point 4 was a regression I introduced.** Moving to the picker template turned the mandate into a
dead summary with a non-functional Edit button, and hardcoded the base portfolio to Core / Mod. The
rail now has four tiers, all live:

| Tier | Controls | On change |
|---|---|---|
| **Mandate** | Top Account Size, Mandate Size, Primary PWA (typeahead) — summary that expands to an editable form | Validated on save |
| **Scenario basis** | Currency, Hedging Policy | **Rebuilds every column**, keeping each column's allocation and risk |
| **Base portfolio** | Allocation, Risk level | Replaces column one; a comparison that now duplicates it is dropped |
| **Comparisons** | count, built list with remove | — |

**Mandate validation** is now live and demonstrates the error state the brief requires: mandate size
below $5,000,000 is a hard block with an inline `.b-breach`-styled message and `aria-invalid` on the
field; mandate size above top account size is also blocked. Primary PWA is a typeahead backed by a
`datalist`.

#### Core vs Ex Alts

Implemented as: **Core is a reduced set of building blocks** — it drops High Yield Credit, Emerging
Market Debt and Japan Equity, with the remaining sleeves renormalised inside their category — while
**Ex Alts keeps full breadth minus alternatives and real estate**. Core therefore gives up a little
return for a little less risk.

**This is an assumption and needs confirming against the real stored allocations.** What is certain
from your answer is only that they differ; the specific way they differ is my invention.

A useful side effect: this is the first time the prototype actually shows the
**union-with-zero-fill** behaviour — Core columns render dashes where they hold nothing, which is
the rule agreed at Q6 and which had never been visible until now.

#### Verification

- `ex RE` suffix correct for all six variants
- mandate: below $5m rejected with the error surfaced; mandate > top account rejected; valid input
  clears the error
- basis change preserves column count and combinations while rebuilding the numbers, and the
  currency propagates into the full name
- base change drops a comparison that duplicates the new base
- Core and Ex Alts differ in return and volatility at every risk level, and Core stays monotonic
- all six pickers still run

---

### Q14d — How Core and Ex Alts actually differ

**A:** Core includes bonds, equities **and hedge funds**. Ex Alts is just bonds and equities — the
difference is the hedge funds. Settle on the **32 picker-table** design and branding as the base for
now (may change later) and return to the Q&A.

**My earlier guess was wrong** and is corrected. I had invented a reduced sleeve set for Core
(dropping high yield, EM debt and Japan). The real structure is which alternatives each variant
admits:

| Variant | Hedge Funds | Private Equity | Real Estate |
|---|---|---|---|
| Full | ✓ | ✓ | optional |
| Ex HFs | ✗ | ✓ | optional |
| Core | **✓** | ✗ | ✗ |
| Ex Alts | ✗ | ✗ | ✗ |

Every variant holds bonds and equities. Note this makes **Ex HFs and Core near-mirrors** — one drops
hedge funds and keeps private equity, the other does the reverse.

Verified at Mod: composition correct for all six, weights sum to 100.0, Core and Ex Alts differ on
volatility (9.15% vs 9.37%), every variant holds bonds and equities, monotonicity preserved.

The union-with-zero-fill dashes are now correct and meaningful — Ex Alts shows dashes against Hedge
Funds, Private Equity and Real Estate; Core against Private Equity and Real Estate; Ex HFs against
Hedge Funds.

---

### Q15 — The future host project

> Does the host already run a web stack? Does it own auth/session? Does the analytics travel with
> the UI or does the host have its own?

**A:** The host will call **epsilonPhi under the hood** — parameters submitted, epsilonPhi
constructs the portfolios, generates the analytics and returns them to the application.
**FastAPI** is the intended stack.

**Package shape agreed:**

```
epsilonphi_ui/
├── ports.py            # Protocol: the ~5 methods the host must provide
├── router.py           # APIRouter, depends only on the Protocol
├── adapters/
│   └── epsilonphi.py   # reference adapter for this project
├── app.py              # standalone FastAPI app, development only
└── static/             # index.html + app.css + app.js, no build step
```

Host adoption is two lines:

```python
from epsilonphi_ui import make_router
app.include_router(make_router(MyAdapter()), prefix="/portfolio-analytics")
```

**Ship an `APIRouter`, not an app.** A router mounts into the host's existing FastAPI instance and
inherits its middleware, auth and session. A standalone app would have to run alongside and be
proxied. `app.py` is for local development only.

---

### Q16 / Q17 — How long the analytics take

> Where are the 5 seconds spent, and does a batch call beat four sequential ones?

**A:** It could be **5 seconds**, so loading states are required. Running the full analytics is
expensive per portfolio, but it may all be cached so as not to recompute. Not worth pinning down
further.

**Design decision taken, no further input needed:** every column requests its data immediately and
shows a skeleton only after a **~200ms threshold**. A cached response lands before the threshold and
the skeleton never appears, so it reads as instant; an uncached one shows the skeleton and fills on
arrival. One code path, correct either way.

**Consequences that must be specified:**

- Columns are ordered by **insertion, not completion** — concurrent requests can return out of order
- The remove control must work on a still-loading column
- Each column needs its own **error state with retry**
- The popover must not block while a column resolves
- **The rebuild rule from Q14c needs a confirmation step.** At 5s per portfolio, changing currency
  could take 20 seconds. It cannot fire silently on a select change, which is what the prototype
  currently does.

---

### Q18 — Reporting

> What does "reporting" mean — which artifact, what is in it, how does it reach the user?

**A:** Reporting uses `generate_report()` to create and download the on-screen tables as an Excel
file. **For now, use the styling in the existing `generate_report()` function.**

**Assumptions flagged for confirmation:**

- `include_wealth_simulations=False`, since simulation was ruled out of scope at Q1
- The workbook contains the **portfolios sheet and risk dashboard** matching what is on screen
- Export is **disabled while any column is still loading** — an incomplete workbook is worse than no
  workbook
- Delivered as a browser download via `Content-Disposition: attachment`
- Filename convention to be specified in the spec, e.g.
  `EpsilonPhi_Scenario_USD_Hedged_2026-08-29.xlsx`

---

### Q19 — Charts

> None, one, both, or something else?

**A:** Add some charts, at my discretion.

**Chosen: two, placed between the allocation table and the risk dashboard, in an "At a glance"
section.** Both summarise; neither replaces a table.

| Chart | Form | Why |
|---|---|---|
| **Allocation by category** | Horizontal stacked bars, one per portfolio | Part-to-whole across a small number of entities. Makes four allocations comparable at a glance in a way 24 rows of percentages cannot. |
| **Return against volatility** | Scatter, all four points direct-labelled | The classic test of whether a comparison is buying anything. Base marked distinctly. |

Deliberately **not** added: a stress-period grouped bar (4 crises × 4 portfolios is too busy) and a
VaR chart (redundant with volatility).

#### Colour, computed rather than chosen

Categorical slots are the validated default theme, assigned in **fixed order**, never cycled:

`#2a78d6` · `#eb6834` · `#1baf7a` · `#eda100` · `#e87ba4`

Validator output on the `#FFFFFF` panel:

```
[PASS] Lightness band          all 5 inside L 0.43–0.77
[PASS] Chroma floor            all 5 >= 0.1
[PASS] CVD separation          worst adjacent ΔE 9.1 (protan)
[PASS] Normal-vision floor     worst adjacent ΔE 19.6
[WARN] Contrast vs surface     3 slots below 3:1 — relief required
```

The contrast WARN is **discharged, not dismissed**: the allocation table sits directly beneath with
exact values, and wide segments carry direct labels.

**The house green, amber and red are deliberately excluded from the series palette.** They are
reserved as status colours in this UI (`.b-ok`, `.b-warn`, `.b-breach`, positive/negative figures),
and a status hue must never impersonate a series.

#### Implementation

Inline SVG, no library, no build step. Responsive via `viewBox`. Per-mark hover tooltips.
Both charts carry `role="img"` with an `aria-label` that names the measure and points at the table
for exact values — identity is never colour-alone, since the legend is always present and the table
is the accessible source of truth.

Verified: no negative or overflowing geometry, rows sum to the full bar width, markers ≥ 12px,
labels inside the viewBox, one tooltip per mark, five legend entries.

---

## Part two — implementation models

Context added after the SAA specification was written.

**Organisation.** The service is for the **Portfolio Management Group (PMG) at Goldman Sachs**.
**Users are Private Wealth Advisors (PWAs)**, who use the tool to build portfolio implementation
models and submit them to be invested in.

**Two stages.** Everything specified in Q1–Q21 is stage one: choosing the **asset allocation**.
Stage two is **implementation** — attaching sleeves of investible products to the asset allocation.

---

### Q22 / Q23 — Attachment level and the real category list

**A:** Sleeves attach at the **category** level. The authoritative categories are:

1. Cash, Deposits & Money Market Funds
2. Investment Grade Fixed Income
3. Other Fixed Income
4. Public Equity
5. Hedge Funds
6. Private Equity
7. Other Private Assets

**Corrections to the SAA spec this forces:**

- The mock's five categories (Cash & Equivalents, Fixed Income, Equities, Alternatives, Real Estate)
  are **wrong**. The allocation table grouping and the risk dashboard's first section must use the
  seven above.
- **Real Estate is not a category** — it sits inside *Other Private Assets*. `Include RE` is a rule
  about the contents of that category.
- A category absent from an allocation variant **disappears from that column**, rather than showing
  a dash. A dash means "holds none of this asset class"; an absent category means the variant does
  not have that concept.

| Allocation | Cash/MMF | IGFI | Other FI | Public Eq | HF | PE | Other Priv |
|---|---|---|---|---|---|---|---|
| Full + RE | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Full | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| Ex HFs + RE | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ |
| Ex HFs | ✓ | ✓ | ✓ | ✓ | — | ✓ | — |
| Core | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| Ex Alts | ✓ | ✓ | ✓ | ✓ | — | — | — |

#### Terminology, fixed

| Term | Means |
|---|---|
| **Category** | One of the seven. What sleeves attach to. |
| **Asset class** | A row beneath a category in the allocation table |
| **Sleeve** | A set of investible products with weights summing to 1 |
| **Product** | A line item within a sleeve |

---

### Q24 — Which portfolio is implemented

**A:** **Only the base portfolio.**

The comparison columns exist to choose an allocation; once chosen, the base is the thing implemented
and submitted. Implementation is a **third route**, `#/scenario/{id}/implementation`, carrying each
category's AA weight forward as a reference column.

**Parked:** changing the base after sleeves are attached invalidates the implementation. To be
resolved once the module is defined.

---

### Q25 — What a sleeve is

**A:**

| | |
|---|---|
| **Source** | A library of pre-built sleeves authored by PMG |
| **Assembly** | **No one can assemble sleeves.** PWAs select only. |
| **Choice** | ~5 sleeves per category |
| **Mutability** | **A sleeve is a fixed block.** Weights cannot be edited, products cannot be added or dropped. |
| **Completeness** | **Every category must have an attached sleeve before submission.** |

**Consequence:** the implementation screen is a **picker with a completeness gate**, not an editable
grid. This is the single largest simplification in the module.

**Mechanic:** a sleeve's weights sum to 1. The category's weight in the asset allocation is
distributed proportionally across the sleeve's products.

> IG Fixed Income at 17.2% of the portfolio, sleeve of A 45% / B 30% / C 25%
> → A 7.74%, B 5.16%, C 4.30%, summing back to 17.20%.

---

### Q26 — The $20m private assets rule

**A:** Private assets are blocked for mandates under **$20,000,000**. This gates **which allocation
variants are available in the SAA step**. At implementation there is no further gating.
**Hedge funds are not private assets.** Dropping now-invalid columns after a downward mandate edit
is acceptable.

**Retroactive contract change.** The availability set is no longer a function of
`(currency, hedging)` alone — it depends on **mandate size**. `get_schema()` must take the mandate.
Sections 3.3 and 4.1 of the specification change accordingly.

| Allocation | Private assets | Under $20m |
|---|---|---|
| Full + RE | PE + Other Private Assets | **blocked** |
| Full | PE | **blocked** |
| Ex HFs + RE | PE + Other Private Assets | **blocked** |
| Ex HFs | PE | **blocked** |
| Core | Hedge Funds only — not private | available |
| Ex Alts | none | available |

**A sub-$20m mandate has two allocations available out of six.** Editing the mandate downward drops
any column that is no longer valid, with the same announcement treatment as the currency rebuild
(§10.4).

---

### Q27 / Q28 — The sleeve picker and the implementation table

**A:** The PWA sees **names only**. A library of names per category, selected from. Names are unique
and describe the implementation vehicle:

| Category | Sleeves |
|---|---|
| Investment Grade Fixed Income | GSAM Separately Managed Account · Funds Only · ETF & Mutual Funds |
| Public Equity | Active-Passive · Passive · Concentrated Active |

Since the name carries the entire differentiation, a **plain select per category** is the honest
control. A card list would invent hierarchy that does not exist.

**Table columns:** Product · Ticker · Style (Active / Passive) · Type (SMA / ETF / Mutual Fund) ·
Product cost · Management fee · Weighted fee · Weight · **Notional off mandate size**.

Grouped by category with subtotals reconciling to the AA weight, mirroring the allocation table.

---

### Q29 — Fee model

**A:** Management fee is the **GS advisory fee layered on top of** the product cost — additive, not
nested.

```
all-in per product  = product cost + management fee
weighted fee        = (product cost + management fee) x weight
portfolio total fee = sum of weighted fees
```

Units: **2-decimal percentage**.

**Deviation taken, flagged:** the *weighted fee* column is shown in **basis points to one decimal**.
At 2dp a thinly-weighted product renders a real fee as `0.00%` — e.g. 0.33% all-in on a 1.40% weight
is 0.0046%. A fee of zero on a client-facing document generates a phone call. Everything else stays
2dp percent; the portfolio total is shown both ways, `0.84% (84.4bp)`.

---

### Q30 — Submission

**A:** Submission is **not in scope for this UI**. The terminal action downloads the tables to Excel.

Gated on completeness — all categories carrying a sleeve — and adds an implementation sheet to the
existing workbook rather than producing a second file, so the proposal stays one document.

---

### Build — implementation step in direction 32

The prototype now carries **both steps** behind a step nav, exercising the multi-page architecture
for real rather than hypothetically.

**Core corrections made at the same time:** the allocation table, risk dashboard and charts now use
the **seven real categories**. The chart palette extended to seven slots and was re-validated —
all checks still pass. Categories a variant does not have render **blank**, distinct from the em
dash that means "holds none of this asset class".

**Verified end to end:**

- The base (Core) offers five categories; Private Equity and Other Private Assets are correctly absent
- Total weight 100.00%; total notional exactly $26,000,000; blended fee 84.4bp
- Weighted fee reconciles to (cost + mgmt) × weight per product
- Export disabled while any category lacks a sleeve; badge names the shortfall; the table flags the gap

#### One finding worth a decision

**The printed weight and the printed notional do not hand-reconcile.** Notional is computed at full
precision while weight displays at 2dp, so multiplying the printed weight by the mandate can be out
by up to **mandate × 0.005%** — about **$1,300 on a $26m mandate**. Observed drift on a real row:
$4,247,290 shown against $4,248,400 hand-computed.

**Resolved — round notional to the nearest $100.**

Implementing that literally did **not** fix it. Rounding the notional makes the figures look
deliberate, but the drift comes from the **weight's** 2dp rounding, not the notional's precision:
printed `7.02%` × $26m is $1,825,200 while the true weight is 7.0150%. Drift stayed at $1,300.

**The rule as built** derives every printed figure from the printed weight:

1. Round each weight to 2dp by **largest remainder**, so the column sums to exactly `100.00%`
   rather than 99.99% or 100.01%.
2. Derive notional from that rounded weight, then round to the nearest $100.
3. Derive the weighted fee from that rounded weight too.

For a mandate that is a round million, step 2 is a no-op — `mandate / 10000` is already a multiple
of 100, so notional lands on a hundred by construction.

**Result, all four invariants holding at once:**

| Invariant | Result |
|---|---|
| Every line a multiple of $100 | yes |
| Lines sum to the mandate exactly | $26,000,000 |
| Category subtotals sum to the mandate | $26,000,000 |
| Weights sum to exactly 100.00% | 100.0000% |
| **Printed weight × mandate = printed notional** | **$0 drift** |
| Printed (cost + mgmt) × printed weight = printed weighted fee | 0 mismatches |

**For the spec:** a client-facing table must derive its figures from what it prints, not from what it
computed. Rounding the output is not the same as rounding the input.

---

### Q31 — Implementation table columns

**A:** Rename Type to **Vehicle**. Add a **sub-asset class** column — every product carries an
associated asset class as well as a category. Add **Source** (Internal / External), **Liquidity**
(Daily / Quarterly / …) and **Exposure Currency**. **Weight** becomes the first column after Product.

**Column order as built** — thirteen columns:

| # | Column | Notes |
|---|---|---|
| 1 | Product | sticky, row header |
| 2 | **Weight** | moved forward, per instruction |
| 3 | **Asset class** | the sub-asset class beneath the category |
| 4 | Ticker | em dash where the vehicle has none, e.g. an SMA |
| 5 | Style | Active / Passive |
| 6 | **Vehicle** | was Type — SMA / ETF / Mutual Fund |
| 7 | **Source** | Internal / External |
| 8 | **Liquidity** | Daily / Monthly / Quarterly / Drawdown |
| 9 | **Exposure ccy** | USD / EUR / Local |
| 10 | Cost | product cost, 2dp % |
| 11 | Mgmt fee | GS advisory fee, 2dp % |
| 12 | Wtd fee | (cost + mgmt) × weight, bp to 1dp |
| 13 | Notional | derived from the printed weight |

**Consequences:**

- Minimum table width rises to **1460px**, so the table scrolls horizontally on anything but a wide
  desktop. The sticky Product column is now doing real work rather than being a nicety.
- The product record gains four fields the port must supply: sub-asset class, source, liquidity and
  exposure currency. `resolve_sleeve()` returns them per product.
- Category rows span columns 3–9 to carry the sleeve name; the grand total row spans 3–11.
  Verified: every row occupies exactly 13 grid columns.

**Also changed:** the total portfolio fee card was dropped and **Download .xlsx moved beneath the
table**, with the reason shown inline while it is disabled. No information lost — the blended fee
was duplicating the grand total row, which still carries it in basis points.

---

### Q32 — Exclude RE and Exclude TAA as tick boxes

**A:** Exclude RE becomes a **tick box, inactive unless Full or Ex HFs is selected**. Add a second
tick box, **Exclude Tactical Asset Allocation**. TAA is in every allocation by default.

**This partly reverses Q22's compound Allocation axis, and correctly so.** Folding TAA into the
compound select would give twelve options — worse than four plus two checkboxes. The RE cascade
returns, but as one dependent control with a single clear rule.

**Base portfolio controls as built:**

| Control | Type | Behaviour |
|---|---|---|
| Allocation | select | `Full` · `Core` · `Ex HFs` · `Ex Alts` |
| Exclude Real Estate | checkbox | Enabled only for Full and Ex HFs. For Core and Ex Alts it is **checked and disabled**, with the reason stated — those allocations hold no real estate, so the exclusion is true rather than not-applicable |
| Exclude Tactical Asset Allocation | checkbox | Always enabled. Unticked by default, with a note that TAA is standard |
| Risk level | select | Unchanged |

**Naming:** suffixes append in order — `USD Full Mod ex RE ex TAA`.

**Combination space:** 6 allocation variants × 2 TAA states × 7 risk levels = **84 per scenario**,
up from 42. The availability set grows accordingly.

**Assumption flagged:** excluding TAA is modelled as a small reduction in both return and volatility
(the tactical overlay adds active return and active risk). The real effect must come from the data.
It is also assumed TAA does **not** add or remove a category.

#### A regression this exposed

Threading the TAA flag surfaced that **return, volatility and Sharpe had been `NaN` since the
seven-category rewrite (Q23)**. The metric formulas still referenced the old weight keys —
`w.alts`, `w.re`, `w.fi` — which no longer exist. The crisis calculation had the same fault.

Every existing test passed throughout, because they checked *structure and reconciliation* — column
counts, weights summing to 100%, notionals totalling the mandate — and never that the numbers were
finite. A `NaN` assertion in the TAA test even passed spuriously, since `NaN !== NaN` is true.

**Added to the suite: a finiteness guard** over the whole combination space (68 available
combinations × 2 TAA states) plus a check that no rendered surface contains `NaN` or `undefined`.
**This belongs in the production test suite**, not just the prototype's — reconciliation tests pass
happily on nonsense.

---

### Q33 — House typefaces

**A:** Use the fonts in `ui-style-explorations/fonts/`.

Five files supplied: `goldman-sans-regular`, `gs-sans-variable`, `gs-sans-condensed-variable`,
`roboto-regular`, `roboto-medium` — all valid WOFF2.

**Assignment:**

| Role | Face | Why |
|---|---|---|
| Wordmark | Goldman Sans | The brand face. **Ships regular only** — so it is used at 400 and never asked for a bold, which would be synthesised and look wrong |
| Headings & body | GS Sans | Variable, `wght 250–700`, covers every weight the design uses |
| Tables & data | GS Sans Condensed | Variable, `wght 300–900`. Narrow enough for the thirteen-column implementation table, and it echoes Aptos Narrow in the Excel risk sheet |
| Fallback | Roboto | 1250 glyphs against GS Sans's 500 |

**Checks run before wiring them in:**

- **All five carry `tnum`.** The whole comparison design depends on
  `font-variant-numeric: tabular-nums`; had the house face lacked it, the numeric role would have
  had to go to Roboto.
- **Glyph coverage against the characters the pages actually render** — `·` `×` `—` `…` — is
  complete. The only gap is `─` (U+2500), which appears 97 times but exclusively inside comment
  banners, so it never renders.
- Name records in the GS files are stripped (family reads `.`), normal for licensed corporate
  webfonts. `@font-face` supplies the family name, so it does not matter.

**Consequence:** the Google Fonts request is gone. The pages are now **fully offline** — which also
removes the failure mode that made direction 01 look flat when the webfont did not load.

**For the spec:** §6.3 currently mandates a system stack, inherited from `mpo-ui`. That is now
superseded — the house faces ship with the package, and `fonts/` becomes an asset the receiving
project must carry across.

---

## Part three — the receiving project

`DASHBOARD_AUDIT_extracted.md` documents the host codebase: the PMG portfolio-optimization
dashboard in `isg-cyrus-pmg/src/cyrus_pmg/dashboard/`. This section records what it is and what it
changes.

---

### Q34 — Host structure, and what it invalidates

#### What the host is

**Flask serves static files per page; FastAPI serves the API; Flask reverse-proxies
`/api/*` → `/api/v1/*`.** Vanilla HTML and hand-written JS. No framework, no bundler, **no module
system**, no build step, no template engine.

| Concern | How the host does it |
|---|---|
| Serving | Flask (`dashboardFrontend.py`, port 8001) via `send_from_directory` |
| API | FastAPI `pmgService` (8002) and `optimizationService` (8003); endpoints on `dashboardRouter.py` |
| Proxy | One wildcard route rewrites `/api/<x>` to `/api/v1/<x>`, `timeout=300`, `allow_redirects=False` |
| Pages | One folder per page: `pageName/pageName.html` + `static/{css,js}/pageName.{css,js}` |
| Routing | One hand-written `@app.route('/pageName/<path:filename>')` per folder. **No auto-discovery** |
| Navigation | Hard-coded `<a>` tags in `index.html`. No registry |
| JS | Globals on `window`; `<script src>` order is load-bearing. `if (typeof X === 'undefined')` guards |
| State | Module-level `let`. **No shared store.** `localStorage` for the ISG code only |
| Rendering | Direct DOM writes — `innerHTML` / `replaceChildren`. No re-render model |
| Theme | `static/css/onegsTheme.css` (`--gs-*` variables) + `dashboard.css`, then page CSS |
| Charts | Chart.js from CDN, per page |
| Auth | Kerberos allowlist via `before_request`; `accessGate.js` probes `/api/whoami`; FastAPI re-enforces with `Depends(requireAuth)` / `requireEditor` |
| Errors | Non-2xx JSON carries `error`; 401 carries `loginUrl`. Pages render into `#alertArea` |
| Deploy | GitLab CI zips the package; GS internal `gns` distributes. No Docker, no K8s |
| Tests | **The dashboard package is untested.** No safety net for a new page |

#### What this invalidates in the specification

| § | Spec said | Correction |
|---|---|---|
| 3.1–3.2 | Ship a FastAPI `APIRouter` that also serves static | Static is Flask's job. Our endpoints go on the existing `dashboardRouter.py` under `/api/v1/` |
| 5.1 | ES modules with `import` / `export` | **No module system.** Globals, documented `<script>` order |
| 5.2 | Hash router, three routes | Multi-page host. We ship **one page folder**; the two steps are tabs inside it |
| 5.3 | Base path from `document.baseURI` | Host convention: `window.API_BASE = window.location.origin + '/api'` behind a `typeof` guard |
| 8.3 | Inline SVG charts | Host uses Chart.js from CDN. Ours is self-contained — a deliberate deviation, and safer on a locked-down network |

**A validation:** merging the two prototypes into one page was right for this host. The steps share
mandate, basis and portfolio state, and the host has **no shared JS store** — two page folders could
not have shared it.

#### Design questions answered

| Question | Answer |
|---|---|
| Does the mandate come from a selected PMG account? | **No.** There is no concept of PMG accounts on this page, and the workflow does not open with an account. The mandate dialog stays as designed. |
| OneGS theme or the mpo-ui palette? | **Shift themes at porting time.** Keep the current palette; the token layer stays swappable. |
| Keep the fixed left rail? | **Yes**, and **remove the back-to-dashboard link**. This page is independent of the others. |

#### Settled for the port

- One page folder, `scenarioAnalysis/`, following the naming convention exactly
- Split the monolith into `scenarioAnalysis.html` + `static/css/scenarioAnalysis.css` +
  `static/js/scenarioAnalysis.js`
- Globals not modules; `'use strict'`; bootstrap on `DOMContentLoaded`; documented script order
- Host error convention: `error` field into `#alertArea`; `if (body.loginUrl) window.location = body.loginUrl`
- Endpoints under `/api/v1/`; the 300s proxy timeout covers the ~5s analytics comfortably
- Fonts to `scenarioAnalysis/static/fonts/` with **absolute** URLs — relative ones resolve against
  the CSS file's location, not the page's
- The Python generator becomes a **dev tool**; what ships is generated static files
- **No back link, no host header shape.** The rail is the page's own chrome.

---

### Q35 — ISG schema scoping

> ISG1 and ISG2 are two parallel database schemas. A schema-scoped page includes
> `isgCodeSelector.js`, sends `?schemaCode=` on every call, and re-fetches on `isgCodeChanged`.
> Does our data differ between them?

**A:** Skip the selector — it does not apply.

**Consequences:** no `isgCodeSelector.js`, no `schemaCode` parameter, no `isgCodeChanged` listener.
The availability set stays keyed on currency, hedging and mandate size only, and the sticky global
`pmg.globalIsgCode` is irrelevant to this page.

Note this leaves `epsilonPhi`'s own `dataversion` axis unaddressed — every config table is keyed on
`currency` + `dataversion`. Which data version the adapter reads is a host concern, not a UI one,
so no control is surfaced.

---

### Build — restructured to the host page-folder convention

The prototype now ships as `ui-style-explorations/scenarioAnalysis/`, laid out exactly as the host
serves pages. `PORTING.md` beside it carries the drop-in steps.

```
scenarioAnalysis/
  scenarioAnalysis.html
  static/css/scenarioAnalysis.css
  static/js/scenarioAnalysis.js
  static/fonts/*.woff2
```

**Adoption is three steps:** copy the folder, add one Flask route, add one nav link.

#### Host conventions adopted

`'use strict'` · `API_BASE` guard · `DOMContentLoaded` bootstrap with a `readyState` guard ·
`#alertArea` with `showAlert(kind, message)` · non-2xx `error` field surfaced ·
`if (body.loginUrl) window.location = body.loginUrl` on 401 · `credentials: 'same-origin'` ·
globals not modules, load order documented · direct DOM writes · script at the end of `<body>`.

`apiFetch()` is the seam the adapter plugs into. Verified against the host's error contract:
401 redirects to `loginUrl`, 502 surfaces the `error` field, 200 returns the body.

#### Deliberate deviations, each with a reason

| Deviation | Reason |
|---|---|
| No back link, no host header shape | Agreed at Q34 — the page is independent |
| No `onegsTheme.css` / `dashboard.css` | Theme shift happens at port time; every colour resolves through `:root`, so retheming is a token change |
| No `isgCodeSelector.js` | Confirmed not schema-scoped at Q35 |
| Inline SVG charts, not Chart.js from CDN | Self-contained; nothing breaks on a network that blocks `cdn.jsdelivr.net` |
| Local woff2 rather than the system stack | The house faces. `@font-face` uses `../fonts/*`, relative to the stylesheet, which resolves identically under the Flask route and from `file://` |
| Relative `href`/`src` rather than absolute | Same URL under the Flask route, and the page stays openable from disk for review |

#### One correction found while wiring it

`apiFetch` originally read the bare global `API_BASE`, matching `preferredProducts.js`. That only
works because `window === globalThis` in a browser. It now reads `window.API_BASE` — identical at
runtime, and testable outside one.

---

### Q36 — Landing state

**A:** Add a landing state on the same page: a title, *Portfolio Management Group Implementation
Tool*, and a **Start Here** button that opens the mandate card. Once the mandate is entered, show
the allocation page with the rail in place.

**Delivered.** The page now has three phases on one document:

| Phase | Shows | Rail |
|---|---|---|
| `landing` | Title, one-paragraph description, **Start Here**, and a *What you will need* list that states the $5m floor before the user meets it as an error | Hidden |
| dialog | Modal over whichever phase is showing | &mdash; |
| `workspace` | Step nav, allocation tables, charts, risk dashboard, implementation | Visible, mandate summary in place |

**The rail is hidden on the landing phase**, because every control in it depends on a mandate
existing. Showing it empty would be showing a form the user cannot use.

#### The mandate card is now a real modal

Previously the mandate was edited inline in the rail. It is now a dialog, used for both first entry
and later edits &mdash; the only differences are the title (*New scenario* / *Edit mandate*) and the
primary button label (*Continue* / *Save mandate*).

`role="dialog"`, `aria-modal="true"`, `aria-labelledby`, focus to the first field on open, focus
returned to the invoker on close, Escape closes, scrim closes.

**Primary PWA is now a proper combobox**, replacing the `datalist`: `role="combobox"` with
`aria-expanded`, `aria-controls`, `aria-autocomplete`, a `role="listbox"` of `role="option"` rows,
`aria-activedescendant` tracking the highlight, arrow keys moving it **without moving focus**, Enter
to select, and a two-character threshold with an explicit hint below it. Free text that matches no
advisor is invalid.

On Continue, focus lands on the **Allocation** control &mdash; the next thing the user has to do.

#### Two bugs this surfaced

1. **`renderMandate` called `money(null)` and threw on first load.** The mandate tier rendered even
   on the landing phase, where no mandate exists yet. `money()` is now null-safe in both modules and
   the tier renders nothing until a mandate exists. This would have thrown in the browser on the
   very first paint.
2. **The implementation renderer un-hid the workspace regardless of phase.** `refresh()` calls
   `renderPhase()` first and the registered extras afterwards, so `renderView` was overriding the
   phase decision. It now checks the phase before deciding which step to show.

**For the spec:** &sect;7.1 says *&ldquo;the page opens with the rail populated and the document
empty. There is no landing route: the page is the landing.&rdquo;* That is now superseded &mdash;
there is a landing phase before the rail appears.

---

### Q37 — Make the landing page aesthetically pleasing

**A:** The landing was a heading, a paragraph and a bullet list &mdash; a default, not a design.

**The move:** the landing is the one expressive surface in a tool that is otherwise dense data
furniture, so the boldness is spent there and nowhere else.

| Element | Decision |
|---|---|
| **Ground** | A deep navy field with two off-axis radial washes &mdash; the only dark surface in the product, so crossing it reads as a threshold you pass once |
| **Hero** | **A real output of the tool**, not an illustration: three allocations (Full Mod Agg, Core Mod, Ex Alts Cons) drawn as stacked composition bands from the same model and the same category colours the allocation table uses, with their real return and volatility |
| **Sheet** | The bands sit on a **white card** floating on the navy, echoing the product's own white-document-on-navy-chrome structure |
| **Type** | Goldman Sans at `clamp(34px, 4.6vw, 58px)`, weight 400 &mdash; the face has no bold, so the scale carries the emphasis rather than the weight |
| **Motion** | One orchestrated moment: the bands grow from zero width on entry. Suppressed under `prefers-reduced-motion`, landing on identical geometry |
| **What you will need** | A definition list with small-caps labels rather than bullets. **Not numbered** &mdash; it is a checklist, not a sequence |

#### Why the artefact sits on white

The category palette was validated against `#FFFFFF`. Re-validated against the navy ground it
**fails the dark-mode lightness band**, and a lifted dark variant failed too. Rather than carry a
second colour system for one screen, the hero sits on a white sheet &mdash; so the colours are
identical to the allocation table's, and consistency comes for free.

#### Contrast on the dark field

Audited at the darkest point of the gradient (`#101B2C`). Three values failed:

| | Was | Now | Note |
|---|---|---|---|
| Call to action | `#1f5fbf` &mdash; 2.84:1 | **`#2a6ad0`** &mdash; 3.35:1 vs ground, 5.16:1 with white text | The same step the rail button uses, so no new colour enters the system |
| Section rule | `#26405f` &mdash; 1.63:1 | `#2c4a6b` | Decorative separator, exempt from 1.4.11, but lifted because it was invisible |
| Eyebrow dash | `#3e5f8c` &mdash; 2.65:1 | `#4a6e9c` &mdash; 3.29:1 | Same |

All text pairs pass: title 17.28:1, accent 8.34:1, lede 9.25:1, eyebrow 6.04:1, labels 4.66:1.

---

## Decisions locked

| Area | Decision |
|---|---|
| **Scope** | Portfolio construction, risk/return modelling, reporting. Simulation excluded. |
| **Prior prototypes** | `mpo-ui` and `simulation-dashboard.html` both discarded. Greenfield. |
| **Submit behaviour** | Lookup, not solve. Synchronous, sub-second. No job model. |
| **Gate fields** | Top Account Size, Mandate Size, Primary PWA — mandatory, gate the rest of the form |
| **Model fields** | Hedging Policy, Currency, Risk Level, AA Type, Include RE |
| **Validation** | Mandate Size ≥ $5m hard block; Mandate Size ≤ Top Account Size |
| **Primary PWA** | Typeahead lookup control |
| **Include RE** | Enabled only when AA Type ∈ {Full, Ex HFs}; otherwise forced False |
| **Allocation table** | Category-grouped rows with subtotals, indented assets, union across portfolios with zero-fill |
| **Metrics band** | Estimated Mean Return, Sharpe Ratio, Volatility (equity risk beta parked) |
| **Risk dashboard** | Full three-section composite; mixed column span (section 1 colspan 2, sections 2–3 split Nominal/Real) |
| **Portfolio count** | 4 max — 1 base (pinned first) + 3 comparisons. No deltas. |
| **Fixed vs varying** | Currency + Hedging Policy fixed per scenario; Risk Level, AA Type, Include RE vary |
| **Comparison picker** | **Table (32)** — `+` column opens a popover; "Add to table" inserts the column directly. No queue |
| **Edit behaviour** | Editing the scenario basis or the base portfolio **rebuilds**; a comparison duplicating the new base is dropped |
| **Categories** | Cash/Deposits/MMF · IGFI · Other FI · Public Equity · Hedge Funds · Private Equity · Other Private Assets. Real Estate sits inside Other Private Assets |
| **Implementation columns** | Product · Weight · Asset class · Ticker · Style · Vehicle · Source · Liquidity · Exposure ccy · Cost · Mgmt fee · Wtd fee · Notional |
| **Fee model** | Management fee = GS advisory fee, additive on product cost. Weighted fee = (cost + mgmt) × weight, shown in bp to 1dp. Total = sum of weighted fees |
| **Submission** | Out of scope. Terminal action downloads the tables to Excel, gated on completeness |
| **Sleeves** | PMG-authored library, ~5 per category, fixed blocks, attach at category level. Every category must carry one before submission |
| **Implementation scope** | Base portfolio only, on route `#/scenario/{id}/implementation` |
| **$20m rule** | Private assets (PE + Other Private Assets) blocked under $20m mandate. Gates SAA variants to Core and Ex Alts. Hedge funds are not private assets |
| **Typography** | Goldman Sans (wordmark, 400 only) · GS Sans variable (headings, body) · GS Sans Condensed variable (tables) · Roboto (fallback). Served locally, no network |
| **Base controls** | Allocation select (4) + Exclude Real Estate checkbox (gated on Full / Ex HFs) + Exclude TAA checkbox (always available, off by default) + Risk level |
| **Allocation variants** | Full = HF + PE; Ex HFs = PE only; Core = HF only; Ex Alts = neither. All hold bonds + equities. RE optional on Full and Ex HFs only |
| **Allocation axis** | AA Type + Include RE collapse into one 6-option select. Removes the cascade. Adopt regardless of picker |
| **Portfolio naming** | Derived, non-editable. `" ex RE"` **only** for Full and Ex HFs; Core and Ex Alts never carry it |
| **Host** | `isg-cyrus-pmg` PMG dashboard. Flask static + FastAPI API + `/api/*` proxy. Vanilla JS, no module system, one folder per page |
| **ISG scoping** | Not applicable. No selector, no `schemaCode`, no re-fetch listener |
| **Port shape** | Single page folder `scenarioAnalysis/`; two steps as tabs; globals not modules; endpoints on `dashboardRouter.py` |
| **Transport** | **FastAPI `APIRouter`** mounted into the host app. Host implements a ~5-method Protocol; reference adapter calls epsilonPhi |
| **Latency** | Analytics up to ~5s per portfolio, possibly cached. Skeletons after a 200ms threshold; columns independently stateful |
| **Reporting** | Download the on-screen tables as Excel via `generate_report()`, existing styling |
| **Charts** | Two: stacked allocation bars + return/volatility scatter. Validated categorical palette; status hues excluded |
| **Concurrency** | Concurrent users required — separate session/thread per user |
| **Roles** | Single role for now; spec a documented permission extension point, do not invent a hierarchy |
| **Landing state** | Landing phase on the same page: title, Start Here, and a what-you-need list. Rail hidden until a mandate exists |
| **Link sharing** | Deferred |
| **Visual direction** | **Settled (provisionally): direction 32** — Porcelain Navy on the mpo-ui `:root` palette, navy rail, table `+` picker. May change later |
| **Status system** | Adopt mpo-ui's badge vocabulary wholesale: `.b-ok`, `.b-warn`, `.b-breach`, `.b-bind`, `.b-slack` |

---

## Open items

Carried forward, not yet answered:

1. **Equity risk beta** — parked. `get_risk_betas()` vs `get_return_betas()` unresolved.
2. **Add/remove/re-run** — can a user remove or re-run an already-added comparison portfolio,
   or is it add-only until reset?
3. **Enablement chain detail** — is there a required order among Currency / Risk Level / AA Type,
   or are they all live once the gate is complete? Are any individual *option values* ever
   unavailable under certain combinations?
4. **Primary PWA lookup source** — where does the advisor list come from? *(still open)*
5. **"No portfolio matches"** — what happens when a valid parameter combination has no stored portfolio?
6. **Reporting / export** — *Resolved at Q18: Excel download via `generate_report()`.*
7. **Charts** — *Resolved at Q19: two, specified and built.*
8. **Visual / brand direction** — round two: 26 directions in `ui-style-explorations/`;
    converging on 26 (Porcelain Navy). Awaiting confirmation.
12. **Rail vs stacked layout** — if the rail is chosen, does the parameter form stay
    persistent, and does “Add comparison portfolio” reuse the rail instead of a dialogue?
    (see Q13c)
11. **Two conflicting house styles** — the Excel report (Q13a) and the MPO UI (Q13b) use different
    navies and greys. Decide which is canonical, or reconcile them.
9. **Accessibility specifics** — target conformance level, any assistive-tech requirements.
10. **Future host project** — *Resolved at Q15: FastAPI host calling epsilonPhi; ship a mountable APIRouter.*

---

## Q38 — Landing page catalogue, and two visual bugs

> **You:** create a 20 example catalogue i can browse through to find the right landing
> page, be careful to not show at the bottom of the page the *1 Asset allocation
> 2 Implementation* and also what looks like a white header at the top containing
> *enario analysis  1 portfolio · 2 columns in the risk dashboard  Lookup matched*.

### The two bugs, and why they happened

Both were the same class of mistake: a state was being *marked* hidden but not
*rendered* hidden.

**1. Step nav and workspace visible under the landing page.** `renderPhase()` was
setting `steps.hidden = true`, `view-aa.hidden = true` and `view-impl-wrap.hidden = true`
correctly — the DOM property was right. But the user-agent rule `[hidden]{display:none}`
carries no weight against an author rule, and `.steps{display:flex}` and
`#view-aa{display:block}` are author rules. The author rule wins every time, so the
elements stayed laid out. Fixed by promoting the rule in `BASE_CSS`:

```css
[hidden]{display:none!important}
```

This is worth carrying into the port: any page that toggles `.hidden` and also sets
`display` on the same element has this bug latent in it.

**2. The white bar across the top.** The topbar (scenario name, "1 portfolio · 2 columns",
the lookup status line) is chrome for the workspace, not for the landing page, and nothing
was hiding it. Fixed alongside the rail:

```css
body.phase-landing .rail{display:none}
body.phase-landing .topbar{display:none}
```

### Two further defects found while checking the fix

**3. The composition bands and legend swatches painted transparent.** `CAT_COLORS` holds
`var(--cat-1)`…`var(--cat-7)`, and those custom properties were declared on `.viz` — the
chart container. The landing sheet is not inside a `.viz`, so the variables did not
resolve, `background: var(--cat-1)` was an invalid declaration, and every band and swatch
came out transparent over the grey track. The categorical slots are **palette, not
chart-local state**, so they now sit on `:root`; `.viz` keeps only the grid/axis/ink tokens.

**4. A strip of canvas grey below the landing field.** `#view-landing` is `min-height:100vh`
but sits below `#alertArea`, so the document runs past the viewport and the body's grey
showed underneath. Fixed with `body.phase-landing{background:#101B2C}`.

### The catalogue

`ui-style-explorations/landing-options/` — `index.html` plus 20 pages. Static HTML and CSS,
no JavaScript. Every option carries **the same copy and the same real numbers** so the
comparison is only about design: three genuine allocations from the model
(Full Mod Agg, Core Mod, Ex Alts Cons), the seven validated category colours in the same
fixed order as the allocation table, and the 34-point risk/return cloud.

| | | |
|---|---|---|
| 01 **Sheet** — navy field, allocations on a floating white sheet (the current build) | 08 **Swiss** — no hero; hard grid, hairlines, one band | 15 **Card** — everything in one white card on navy |
| 02 **Plane** — the risk/return plane as the hero | 09 **Editorial** — bands set inline as a figure in a report | 16 **Nocturne** — near-black, one accent, plane on the ground |
| 03 **Column** — one allocation as a single tall column | 10 **Grid** — small multiples, five allocations at a glance | 17 **Spine** — full-height colour spine down the left edge |
| 04 **Ledger** — the allocation table itself as the hero | 11 **Halves** — hard split: navy holds words, white holds evidence | 18 **Monogram** — formal, centred, spectrum as the only colour |
| 05 **Numbers** — four figures that describe the problem | 12 **Banner** — spectrum full-bleed across the top as a masthead | 19 **Gradient** — deep field, bands lifted straight onto it |
| 06 **Daylight** — same structure on the product's canvas grey | 13 **Stack** — copy centred, evidence beneath | 20 **Console** — dense and technical, numbers doing the talking |
| 07 **Paper** — warm stock, rule above the title | 14 **Offset** — asymmetric; artefact high right, words bottom left | |

Generated by `build_landings.py` from `/tmp/landing-data.json`, which is exported from the
model rather than hand-written — so if the allocations change, the catalogue follows.

**Decision needed:** which direction, or which two to hybridise.

---

## Q39 — Landing catalogue, round two

> **You:** have a look at landing-options-spectacular. In this folder are really extravagant
> landing pages, which are too much for this app, but i want you to re-do your landing-options
> upping the creativity and dynamism slightly.

### What the spectacular set does, and what was taken from it

The spectacular pages reach for 100px+ gradient-clipped headlines, orbiting rings, conic
donuts, 3D card decks, marquee tickers, mint glows, a brand glyph and a fake "live" dot. The
useful lesson underneath all that is narrower: **the page should arrive rather than appear,
the artefact should build itself from the data, and things that carry a number should respond
to the cursor.** That is what round two takes. What it leaves behind: invented copy
("Model the move"), gradient text, decorative orbits, the live dot, anything looping except two
ambient glows.

### What changed across all 20

- **Orchestrated arrival.** The copy column enters top to bottom (eyebrow → title → lede →
  button → checklist, 50–600 ms), then the artefact fades in and draws itself.
- **Artefacts that build from the data.** Bands grow left to right, staggered by row; the plane
  pops its 34 points in volatility order and the base lands last with a ring that pulses twice;
  the column stacks from the base and labels each category as it settles; the ring turns into
  place; the ledger's rows arrive in order with a bar growing beside each figure; the spine and
  masthead draw from the top / left.
- **Hover response.** Rows slide; the segment under the cursor holds its colour while its
  neighbours step back; the deck fans; the tilted sheet squares up; the small multiples lift;
  the button's arrow travels.
- **Type a notch larger** — `clamp(36px, 4.5vw, 60px)` with tighter tracking — chosen so the
  accent phrase still holds one line in a split column.
- **Reduced motion** kills every animation and transition; the finished layout is what those
  users see.

### The 20

| | | |
|---|---|---|
| 01 **Sheet** — the current build, choreographed, with a soft halo | 08 **Swiss** — type on a faint grid, the rule draws beneath | 15 **Spine** — full-height spine proportioned to the base allocation |
| 02 **Plane** — 34 points pop in volatility order, base rings | 09 **Editorial** — numbered figure, rules draw in | 16 **Nocturne** — near-black, drifting glow, plane on the ground |
| 03 **Column** — one allocation stacks from the base, self-labelling | 10 **Grid** — small multiples arrive in sequence, lift on hover | 17 **Gradient** — two glows drift, bands on the ground, glass button |
| 04 **Ring** — base allocation as a ring turning into place *(new)* | 11 **Halves** — navy words, white evidence, seam draws down | 18 **Emblem** — formal, centred, the ring as a small emblem *(new)* |
| 05 **Deck** — three sheets stacked, hover fans them *(new)* | 12 **Banner** — base allocation as a full-bleed masthead | 19 **Ledger** — table rows arrive with bars growing beside each figure |
| 06 **Daylight** — same choreography on canvas grey | 13 **Threshold** — one card, allocation spine down its edge | 20 **Console** — title bar with the facts, bands, figures, checklist |
| 07 **Bento** — hero tile plus two evidence tiles *(new)* | 14 **Offset** — tilted sheet top-right squares up on hover | |

Generator: `ui-style-explorations/build_landings.py`, reading `landing-data.json` (now inside
the repo rather than `/tmp`). Round one is preserved outside the repo in the session
scratchpad only; it is superseded.

**Decision needed:** which direction, or which two to hybridise.

---

## Q40 — Landing catalogue, round three: sustained motion

> **You:** push them up slightly more in terms of dynamism on screen

Round two moved on arrival and on hover, then held still. Round three keeps every page alive
after it has arrived — always slowly, always tied to something on the page, never decorative
objects orbiting for their own sake.

### Added to every page

- **Sheen.** A soft band of light passes over every bar, spine, masthead rule and ledger bar
  every 6–8 s, rows offset so they never sweep together.
- **Button halo.** A thin ring leaves *Start Here* every 4.5 s and fades — the one loop that
  exists purely to say "this is where you begin".
- **Accent shine.** A single sweep of light crosses *Implementation Tool* once, 1.1 s after it
  lands. Its resting state is the flat accent colour, which is also what reduced-motion users see.
- **Glow.** Every navy page now has two slow radial glows drifting on 22 s / 26 s cycles,
  blurred so they read as light, not shapes.

### Added where the artefact allows it

| Page | Ongoing motion |
|---|---|
| 02 Plane, 07 Bento, 16 Nocturne | Each of the 34 points breathes on its own clock (4.6–6.6 s, desynchronised); the base's ring pulses every 4 s |
| 03 Column | A vertical sheen climbs the column |
| 04 Ring, 18 Emblem | The ring turns once every 120 s / 150 s — the gaps between categories are what you see move |
| 05 Deck | The three sheets bob on offset 7 s cycles; hover still fans them |
| 08 Swiss | The background grid pans diagonally, one cell every 40 s |
| 10 Grid | The five small multiples bob on offset clocks; each bar takes a sheen |
| 11 Halves, 13 Threshold, 15 Spine | A vertical sheen runs down the coloured seam / spine |
| 14 Offset | The tilted sheet floats on an 8 s cycle |

### Two things deliberately not done

- **No counters or ticking numbers** — no JavaScript, and the figures are facts, not a show.
- **No gradient text at rest** — the accent shine is a one-time pass over a flat colour, not a
  permanently rainbowed headline.

Everything still collapses to the finished layout under `prefers-reduced-motion`. Generator and
data unchanged in location: `ui-style-explorations/build_landings.py`, `landing-data.json`.

**Decision needed:** which direction, or which two to hybridise.

---

## Q41 — Landing page decided: Swiss

> **You:** my final decision on landing page is
> `.../landing-options/08-swiss.html` — delete everything else

### Decision

**08 Swiss** is the landing page. Kept at its existing path so the URL you have keeps working:

```
ui-style-explorations/landing-options/08-swiss.html
```

### Deleted

| | |
|---|---|
| `landing-options/` | 42 files — the 19 other directions, your 20 `08-swiss-bg-*` background studies, `08-swiss-backgrounds.html`, `18-emblem-liquid.html`, and the gallery `index.html` |
| `landing-options-spectacular/` | whole directory, 23 files |
| `build_landings.py` | the catalogue generator |
| `landing-data.json` | data the generator read |

Deleting the generator matters for a reason beyond tidiness: it would have **overwritten** your
hand-tuned `08-swiss.html` the next time anyone ran it. Your edits were not in it.

A full copy of everything deleted is in this session's scratchpad
(`scratchpad/backup-before-delete/`), which is outside the repo and will not survive the session.

### Your edits, preserved exactly

The file you picked had been hand-edited after my last build, so it was treated as authoritative
and copied, not regenerated:

- ground `#FBFCFE` (the grid and its pan removed)
- title changed to **"Proposal Tool"**, set as two blocks, accent `#446cce`
- Goldman Sans at `clamp(53px,6vw,99px)`, tracking `-.045em`, plus a `max-width:560px` step-down
- square CTA with your own `softPulse` / `softHalo` pair

### Two changes I did make, both consequences of the deletion

1. **Removed the back-link** (`← All 20 options` markup + its CSS) — it pointed at the gallery
   `index.html`, which no longer exists.
2. **`<title>`** `Swiss — landing option` → `Portfolio Management Group Proposal Tool` — the old
   one framed the page as one of a set that no longer exists.

Nothing else differs from your file, byte for byte.

### One inconsistency to settle

The page is now titled **Proposal Tool**, but its own lede still ends *"to produce the
implementation model"*, and `ui-spec/spec.html` calls the product the **Implementation Tool**
throughout. Left alone — the wording is yours to decide.

### Not done, and not in scope here

The Swiss direction has not been folded into the working prototype
(`scenarioAnalysis/`), which still carries the round-two navy landing. §7.1 of the spec also
still says "there is no landing route: the page is the landing", superseded back at Q36.

---

## Q42 — Swiss folded into the prototype

> **You:** now fold the swiss landing into the prototype

The landing phase in `scenarioAnalysis/` was the round-two navy design. It is now the Swiss
direction you chose, generated from source rather than copied as a static file, so the
prototype stays a single build.

### What changed

**`build_styles.py` — markup.** The `#view-landing` section was rebuilt to the Swiss structure:
rule, then a copy column (eyebrow, two-block title, seven-colour spectrum), then a two-column
band holding the lede + CTA on the left and the checklist on the right. The old
`figure.lp-art` / `#lp-sheet` artefact is gone.

The spectrum draws its seven segments from `var(--cat-1..7)` — the same validated palette the
allocation table uses — rather than hard-coded hexes, so the landing and the table can never
drift apart.

**`pickers.py` — CSS.** The landing block was replaced wholesale. Ground `#FBFCFE`, Goldman Sans
at `clamp(53px,6vw,99px)` with `-.045em` tracking, accent `#446cce`, square CTA carrying your
`softPulse` / `softHalo` pair (renamed `lp-pulse` / `lp-halo` to sit in the page's namespace).
Colours that exist as tokens now use them (`--ink-2`, `--line-strong`, `--f-num`); the ones that
are specific to this page stay literal.

**`pickers.py` — JS.** `renderLandingArt()` and its `LP_SHOWN` guard were deleted — 2,205
characters. Swiss has no data-driven artefact, so nothing needs painting; `renderPhase()` now
only toggles visibility and `body.phase-landing`.

### One deliberate change from the standalone file

The prototype's `.btn` forces `text-transform:uppercase`, which turned the CTA into "START HERE".
The base rule exposes a `--btn-case` token, so `.lp-cta` sets `--btn-case:none` rather than
overriding the property — the landing CTA is sentence case, app buttons stay uppercase.

### One difference left alone

The CTA is 58px tall in the prototype against 54px standalone. Cause: `.btn{font:inherit}` picks
up the app's 25.5px body line-height, where the standalone page has none. That is the product's
own typographic rhythm asserting itself and is the right outcome — not corrected.

### Verified

- All five suites pass — `finite`, `taa`, `i33`, `cols`, `landing`.
- Start Here opens the mandate dialog; committing hands off to the workspace with the navy rail,
  the mandate populated and the allocation table rendered.
- Every `.lp-*` class is both defined and used; no orphans either way.
- No stale `lp-sheet` / `lp-art` / `lp-seg` / `renderLandingArt` / `LP_SHOWN` anywhere.

`landing-options/08-swiss.html` is kept as the design reference. A pre-existing dead `.landing`
/ `.landing-need` CSS block (10 rules, from a design two rounds back) is still in the stylesheet;
it was already unused before this change and was left alone.

### Now out of date

`ui-spec/spec.html` §7.1 still says *"There is no landing route: the page is the landing"*,
which was superseded at Q36 and is now plainly contradicted by the build. The spec also calls the
product the **Implementation Tool** throughout, while the landing you chose says **Proposal
Tool**, and the landing's own lede still ends *"to produce the implementation model"*.

---

## Q43 — Spec updated for the landing

> **You:** update the spec

### First, a correction to Q42

Q42 said the spec *"calls the product the Implementation Tool throughout"*. That was wrong —
the phrase appears **zero** times in `spec.html`. The document is titled **Epsilon Phi Scenario
Analysis** and calls the page that everywhere. The naming tension is real but it is between
*Scenario Analysis* and *Proposal Tool*, not the pair I named. Apologies for the noise.

### What changed, and why each

| § | Change |
|---|---|
| **7.1** | Retitled *Empty state and the mandate dialog* → **The landing, and the mandate dialog**. The claim *"There is no landing route: the page is the landing"* is gone. Replaced with the landing as built: a wireframe, the ground and type values, and why the seven-colour rule reads from `var(--cat-1…7)` rather than hard-coded hexes. |
| **5.3** | `phase: "landing" \| "workspace"` added to the state shape. The spec previously had no notion of a phase at all — `phase` appeared 0 times. |
| **10.2** | New first row: the whole page with no mandate. Marked explicitly as *a distinct phase, not an empty state waiting to be filled* — the distinction the old §7.1 got wrong. |
| **6.3** | The Goldman Sans row now covers the landing display size, `clamp(53px,6vw,99px)` at `-.045em`, noting it is the only place the face is set above 21px. |
| **12** | New **The landing** block. It has no rail, so the 1180/1040 breakpoints do not apply; its own are 900px (side-by-side → stacked) and 560px (title step-down, label column 96px → 84px). |
| **13.1** | The seven-colour rule is `aria-hidden="true"` — ornament repeating the palette, carrying nothing the copy does not. §13.2 already specified dialog focus return, so that needed nothing. |
| **§16** | New open item **12: what the product is called** — three names for one page. Filed rather than resolved; §16 is where the spec's own convention puts open decisions. |
| header | Revision 2 → **3**. |

### A callout worth keeping

§7.1 now carries the reason the landing is a phase and not a route: the host registers **one Flask
route per page folder** (§3.2), so a landing with its own URL would break the porting contract.
That constraint was implicit before and is now written down where someone about to add a router
will read it.

### Checked

Well-formed after every edit (16 sections, 37 tables, 269 rows all balanced); subsection numbering
intact in §6, §7, §10, §13; the Contents list needed no change because it indexes top-level
sections only. The new wireframe is 88 characters at its widest, inside the existing 81–98 range,
so it introduces no new horizontal scroll.

---

## Q44 — Naming settled: Proposal Tool

> **You:** i want Proposal Tool for the naming. make it consistent everywhere

### The rule applied

**The product is the Epsilon Phi Proposal Tool**, shortened to **Proposal Tool** in the interface.

**A &ldquo;scenario&rdquo; is what a PWA builds; the Proposal Tool is what builds it.** The domain
noun is untouched. Renaming it would have churned the entire HTTP contract for a cosmetic change,
so everything below stayed exactly as it was:

`scenarioId` &middot; `/api/scenario/…` (13 endpoints) &middot; `ScenarioPort` &middot;
the *Scenario basis* rail tier &middot; the *New scenario* dialog &middot;
`aria-label="Scenario"` &middot; `EpsilonPhi_Scenario_{CCY}_{HEDGING}_{DATE}.xlsx` &middot;
open item 8, *Scenario retention*

That rule is now written into the spec as **§1.3 What it is called**, so the next person does not
have to re-derive it.

### Renamed

| Where | From | To |
|---|---|---|
| Spec title, masthead, footer | Epsilon Phi Scenario Analysis | Epsilon Phi Proposal Tool |
| Workspace topbar | Scenario analysis | Proposal Tool |
| Page folder, html, css, js | `scenarioAnalysis` | `proposalTool` |
| Flask route | `/scenarioAnalysis/…` | `/proposalTool/…` |
| Route handler | `serve_scenario_analysis` | `serve_proposal_tool` |
| Theme label (`themes.py`) | Scenario Analysis | Proposal Tool |

The folder rename was one constant &mdash; `SLUG` in `build_styles.py` &mdash; plus the references
in `PORTING.md` (14), `README.md` (4), the spec (9) and the landing reference copy's font paths.
The old folder was deleted and regenerated rather than moved, so nothing stale survives.

### Also fixed

The footer still read **revision 2** while the header read 3 &mdash; I bumped only the header in
Q43. Both now read revision 3.

Open item **12** is gone from §16. It was *Decision needed*, and §16's own lede says
&ldquo;everything unresolved&rdquo;, so a settled item does not belong there; the decision lives in
§1.3 instead. Items 1&ndash;11 are unchanged and there is no gap. The former §1.3 *Out of scope*
is now §1.4 &mdash; nothing cross-referenced it.

### Verified

- All five suites pass.
- Spec well-formed; 37 tables and 268 rows balanced; zero occurrences of
  `Scenario Analysis`, `Scenario analysis` or `scenarioAnalysis` remain.
- All three house faces load in both the prototype and the landing reference copy after the path
  change.
- Landing &rarr; mandate dialog &rarr; workspace still hands off; topbar reads **Proposal Tool**
  while the rail still reads **Scenario basis**, which is the distinction working.

### Left alone deliberately

Earlier entries in this log still say `scenarioAnalysis` and *Scenario Analysis*. It is an
append-only record of what was true when written, and rewriting it would destroy the history of
the decision.

---

## Q45 — Consolidated into a hand-off package

> **You:** bring it all together into a single directory that can be handed off to a software
> engineer … for them to fully implement the entire system front to back in line with the guidance

Everything now lives in **`epsilon-phi-core/proposal-tool/`**. The work was *moved*, not copied —
`ui-spec/` and `ui-style-explorations/` no longer exist. Two copies of a spec drift; one does not.

### Structure, and why

```
proposal-tool/
  README.md            the entry point: what to read, in what order
  spec.html            the build reference, revision 3
  PORTING.md           dropping it into cyrus_pmg.dashboard
  DECISIONS.md         this log
  proposalTool/        THE DELIVERABLE — copied into the host verbatim
  backend/             the adapter contract
  generator/           development tool, does not ship
  reference/           the chosen landing, standalone
```

`proposalTool/` sits at the top level rather than nested, so PORTING.md's *"copy the folder"* stays
literal. The generator moved into `generator/` and now writes to its **sibling**, which took one
line: `root = os.path.join(os.path.dirname(ROOT), SLUG)`.

### Two files created

**`backend/scenario_port.py`** — the eight-method `ScenarioPort` Protocol, extracted from spec §4.2
into an importable module. The referenced types (`BasisInput`, `Schema`, `PortfolioResult` …) are
left as forward references under `TYPE_CHECKING`, with a comment pointing at the section that
specifies each one's shape. Binding them to concrete dataclasses would have invented contract
detail the spec does not fix, and the receiving project's own types are the right home.

**`README.md`** — reading order, the directory map, the backend in one paragraph with the endpoint
table and its latency profile, the four implementation traps that cost the most time, and the
naming rule. It is the only file that assumes no prior context.

### Deleted

The old `ui-style-explorations/README.md`. It still described the round-two navy landing — *"a dark
navy front door … three allocations drawn as composition bands … on a white sheet floating over the
navy"* — which Swiss replaced two rounds ago. A stale orientation document is worse than none, and
the new README supersedes it.

### Verified

Clean rebuild from the generator into the new location; all five suites pass against the
regenerated JS; both the prototype and the landing reference render with all three house faces
loading and no horizontal overflow; every path named in the README resolves; no reference to
`ui-style-explorations`, `ui-spec/` or `ui-design-qa.md` survives anywhere except the historical
entries in this log.

### Left alone

Earlier entries here still name the old paths. This is an append-only record of what was true when
written; rewriting it would destroy the history the spec's §16.1 points readers at.
