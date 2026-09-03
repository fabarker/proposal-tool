# Porting into `cyrus_pmg.dashboard`

`proposalTool/` is laid out to the host's page-folder convention. The governing rule, and the
thing to hold the port to: **what changes must be configuration, never structure.**

Four actions — two verbatim copies and two documented inserts — then configuration. Nothing
in the list below is a code change.

| | Action |
|---|---|
| 1. The page folder | copy verbatim |
| 2. `pmgService/scenario/` | copy verbatim |
| 3. `pmgService/dashboardRouter.py` | insert the scenario endpoint block |
| 4. `dashboardFrontend.py`, `index.html` | insert the route and the nav link |
| 5. Environment | set the values in §6 |
| 6. The bake | run the offline job |

`service/TRANSPLANT.md` is the file-by-file rehearsal of the same thing, classed
COPY / INSERT / STAND-IN / CONFIG. Read it beside this.

## Preflight

- **The analytics library.** The package is imported by name in exactly one module,
  `scenario/engine.py`. Confirm what the host's SAA library is called and how it is laid
  out before you start — §2 below is where that lands.
- **A writable store path** for scenarios and one for the bake output.
- **The database and `dataversion`** the host's analytics library should read (open item 9).

## 1. Copy the folder

```bash
cp -r proposalTool \
   isg-cyrus-pmg/src/cyrus_pmg/dashboard/
```

```text
proposalTool/
  proposalTool.html
  static/
    css/proposalTool.css
    js/proposalTool.js
    fonts/*.woff2
```

Folder, HTML, CSS and JS all share the name, per the convention in `preferredProducts/`.
No edits: URLs are relative, and the two shared-JS includes (`/static/js/accessGate.js`,
`/static/js/globals.js`) are the host's own files at the host's own paths.

> These files are generated. Never hand-edit `proposalTool/` — change `generator/` and run
> `python3 generator/build_styles.py`, which writes this folder and the service mirror
> together.

## 2. Copy the scenario package — and point it at the analytics library

```bash
cp -r service/cyrus_pmg/pmgService/scenario \
      isg-cyrus-pmg/src/cyrus_pmg/pmgService/
```

Verbatim. The package uses only intra-package relative imports and has exactly two outward
seams: `cyrus_pmg.pmgService.core.accessControl` (imported by the router, not by the package),
and the analytics library.

**`engine.py` is that second seam, and the only module in the package that names the analytics
library.** The adapter, the loader, the bake and the tests all import their symbols from it:

```python
def build_export(self, ...):
    from .engine import Reporting        # not from the library directly
```

Making the port fit the host's library is therefore one of two edits, in one file:

| The host's library is… | Do this |
|---|---|
| Named differently, same layout | Set `SAA_ENGINE_PACKAGE`. No code change. |
| Laid out differently too | Edit the `_SYMBOLS` table in `engine.py` — six entries, each a module path and an attribute. |

Two properties of that module are load-bearing; preserve them if you touch it:

- **Resolution is lazy.** Symbols resolve through PEP 562 `__getattr__` on first access, and
  call sites import them *inside* the function that needs them. The engine's `setup()` costs
  12-14s (database plus factor models), and both a fixtures-configured service and the baked
  adapter's constructed-but-never-connected fallback delegate depend on never triggering it.
  A module-level `from .engine import X` anywhere would connect the database at process start.
- **The lookup is memoised**, so the indirection costs one dict hit per call after the first.

Data files travel inside the package (`advisors.xlsx`; `portfolio_weights.py` carries the
supplied universe). `bake.py` travels with it as an offline job; `bakedAdapter.py` serves its
output. Neither adds a host dependency.

## 3. Register the route

In `dashboardFrontend.py`, beside the other per-page routes:

```python
@app.route('/proposalTool/<path:filename>')
def serve_proposal_tool(filename):
    """Serve Proposal Tool static files."""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'proposalTool'), filename)
```

Restart the Flask process — there is no hot reload for Python changes.

## 4. Add the nav link

In `index.html`, inside the header flex block, copying the surrounding inline styles verbatim:

```html
<a href="/proposalTool/proposalTool.html"
   style="color:white; text-decoration:none; font-size:14px; padding:8px 16px;
          border:1px solid rgba(255,255,255,0.4); border-radius:8px;
          transition:background 0.2s;"
   onmouseover="this.style.background='rgba(255, 255, 255, 0.15)'"
   onmouseout="this.style.background='transparent'">📐 Proposal Tool</a>
```

## 5. The router endpoints

Append the scenario endpoint block of `service/cyrus_pmg/pmgService/dashboardRouter.py`
(everything from the imports it needs through `exportScenario`) into the host's existing
`pmgService/dashboardRouter.py`. It is written in its final form: mixedCase handlers, reads
inheriting the router-level `requireAuth`, writes taking `Depends(requireEditor)`, imports
addressed to `cyrus_pmg.pmgService.scenario.*`.

Endpoints sit under `/api/v1/`; the Flask proxy rewrites `/api/<x>`, so the page calls
`/api/scenario/...`.

## 6. Configure

Which data layer serves the endpoints is one environment variable, `SCENARIO_ADAPTER`:

| | Data | Cold resolve | Database |
|---|---|---:|---|
| `fixtures` | supplied weights, synthetic analytics | ~0ms | no |
| `live` | live analytics through `engine.py` | ~97s | yes |
| `baked` | precomputed results read from disk | **~15ms** | no |

`baked` is the production setting: the analytics are computed once per data version by an
offline job and served from disk. It falls through to the live adapter for anything unbaked
unless `SCENARIO_BAKED_FALLBACK=0`, which keeps the service entirely free of a database.
See `service/PERFORMANCE.md` for why, and for the profile that took a cold portfolio from
255s to milliseconds.

The complete list of what else changes is TRANSPLANT.md §6: `SAA_ENGINE_PACKAGE`,
`SCENARIO_BAKED_DIR`, `SCENARIO_STORE_DIR`, `SCENARIO_RETENTION_HOURS`,
`PMG_ALLOWED_KERBEROS`, ports, the database config, and the theme token swap.

## 7. Run the bake

```bash
python3 -m cyrus_pmg.pmgService.scenario.bake --all --workers 4
```

Run on a data refresh, not at request time — re-run it when `dataversion` changes, since that
is what makes a bake stale.

**What is baked here is USD only**: 4 hedging policies × 68 portfolios = 272 payloads. Half of
those are now unreachable — since D50 every key is `excludeTAA=1`, so 34 per slice are served and
the rest are dead weight the next bake will drop. GBP has
a currency config on the development database and has not been baked; CHF and EUR have no
config there at all. On the host, bake every currency you intend to offer, or the uncovered
ones fall through to the live adapter at ~97s a portfolio.

## 8. Verify

```bash
curl -s http://localhost:8001/proposalTool/proposalTool.html | head
curl -s http://localhost:8001/proposalTool/static/js/proposalTool.js | head
curl -sI http://localhost:8001/proposalTool/static/fonts/gs-sans-variable.woff2
curl -s http://localhost:8002/api/v1/health
```

---

## Conventions followed

| Convention | How |
|---|---|
| Folder / file naming | `proposalTool` throughout, lowerCamelCase |
| Script at end of `<body>` | Single file; internal order is load-bearing (core → picker → implementation → boot) |
| `'use strict'` | At the top of the JS |
| `API_BASE` guard | `if (typeof API_BASE === 'undefined') window.API_BASE = window.location.origin + '/api'` |
| Bootstrap | `DOMContentLoaded`, with a `readyState` guard |
| Errors | `#alertArea` div; `showAlert(kind, message)`; non-2xx `error` field surfaced |
| 401 | `if (body.loginUrl) window.location = body.loginUrl` |
| Credentials | `credentials: 'same-origin'` so the kerberos cookie travels |
| No module system | Globals only; no `import`, no bundler, no build step at serve time |
| Direct DOM writes | `innerHTML` / `replaceChildren`; no framework |

## Deliberate deviations

| Deviation | Why |
|---|---|
| **No `← Back to Dashboard` link, no host header shape.** Fixed left rail instead. | Agreed: this page is independent of the others. |
| **No `onegsTheme.css` / `dashboard.css`.** Self-contained stylesheet. | Theme shift happens at port time. Every colour resolves through `:root` custom properties, so retheming to `--gs-*` is a token change, not a hunt through component CSS. |
| **No `isgCodeSelector.js`.** | Confirmed not schema-scoped. |
| **Inline SVG charts, not Chart.js from CDN.** | Self-contained, so nothing breaks on a network that blocks `cdn.jsdelivr.net`. |
| **Local woff2 fonts** rather than the system stack. | The house faces. `@font-face` URLs are `../fonts/*`, relative to the stylesheet, which resolves identically under the Flask route and from `file://`. |
| **Relative `href`/`src` in the HTML** rather than absolute `/proposalTool/...`. | Resolves to the same URL under the Flask route, and keeps the page openable from disk for review. |
| **`accessGate.js` and `globals.js` are included** with absolute `/static/js/...` paths. | Host convention, and the auth gate the brief requires. These are the host's own shared modules at the host's own paths; opened from `file://` they 404 harmlessly and the page's `typeof API_BASE` guard keeps working. |

The interface moved on from the specification in a number of places while it was being built
— the topbar is gone, the base column is headed "Proposed Portfolio", the risk dashboard's
premia are banded by measure, and the tables size their own columns.
**`spec.html` revision 6 describes what is there now**, and its §1.5 maps every changed
section to the entry in **`service/DEVIATIONS.md`** (D20–D64) that explains it. Fourteen matter
for a port:

- **D29/D49**, implementation types — one of four product universes, chosen in the base
  portfolio tier of step 1 *before* the allocation, because it restricts which allocations
  exist (onshore books hold no alternatives; ESG forces the real-estate exclusion). It changes
  the `list_sleeves` signature, adds `variant` to scenario state, and is taken by
  `get_schema`, `allocationsFor` and `availability`.
- **D35/D36**, naming — the risk level is shown by a label served in the schema while the key
  keeps its short value, so nothing about the availability set or the bake changes.
- **D50**, tactical allocation — no longer strategic. Every key is `excludeTAA=1` (which is why
  availability is 34 per currency, not 68) and the tilt is an implementation toggle: 8% funded from
  Investment Grade Fixed Income, applied in `rules.tiltedCategories` and its JavaScript mirror. It
  never re-resolves, so it adds no analytics cost and no bake dimension.
- **D51**, management fees — resolved, not carried. A product has a fee group; the fee comes
  from the schedule (CASP or RDR), the top account size's tier and one of six fee levels, all
  read from `service/cyrus_pmg/pmgService/scenario/fees.json` by `fees.py`. **The rates in
  that file are invented placeholders** — swapping in the published schedule is a data change.
  It adds `feeSchedule`/`feeLevel` to scenario state, a `fees` block to `get_schema` (the
  tier's rates only, so the page never sees the whole table) and both to `build_export`; the
  workbook gains a *Fee group* column and three header rows. No bake dimension: fees are
  priced from the schema on the page and from the file in the workbook.
- **D57**, the sleeve repository — the library is a SQLite database the service writes
  (`SCENARIO_SLEEVES_DB`), seeded once from a tabular extract (`SCENARIO_SLEEVES_SEED`) and
  maintained by admins in an in-app console. `sleeves.py` keeps its three-function contract,
  so nothing that reads sleeves changes. Adds a third role (`requireAdmin`,
  `PMG_ADMIN_KERBEROS`) to `accessControl`, `capabilities.canAdmin` to `get_schema`, four
  admin-only routes under `/scenario/repository`, and `sleeveTools` (census / export /
  import). The host points the database at a writable path and backs it up like any other.
  The same console carries a view-only product catalogue for admins (D58): no new route,
  one more field (`orphans`) on the repository payload, a second entry link. The catalogue's
  delivery gains two optional columns, `DistributionYield` and `MinimumInvestment` (D63);
  a delivery without them still loads.
- **D60**, sleeve groups — Private Equity and Other Private Assets are one choice.
  `rules.SLEEVE_GROUPS` and `rules.sleeveCategory` decide what a sleeve is picked and
  stored under; the rail, the scenario store, the export gate, the workbook and the
  repository all key by it, and the page reads the grouping from the schema. Adding or
  removing a group is one entry in that list.
- **D64**, surviving the service &mdash; `apiFetch` marks the service down on 502/504 or a
  failed fetch, `canEdit`/`canExport` gate on it, and the page either stays up read-only
  from a `localStorage` snapshot or shows the outage page. The retry re-requests the
  schema because the frontend's `/health` never touches the backend. A host with its own
  health contract should point the retry at that instead.
- **D56**, the product catalogue — products are a delivered one-table extract
  (`SCENARIO_PRODUCTS_SOURCE`, one row per product, `ProductId` the key) read by
  `products.py` and joined into sleeves when they are served. Read only; a bad extract is
  refused at load. Run `sleeveTools --census` against the real one before anything else:
  it lists every sleeve a missing product would break.
- **D55**, the rate card — delivered as a long CSV at `SCENARIO_FEES_SOURCE` and read only in
  the service: it changes by delivery through `feeTools` (census / diff / accept), never
  through the app. Adds `GET /scenario/fees`, a read-only fee card viewer in the UI, a
  `Fee Card` row in the workbook header naming the delivered version, and `feeCard` in the
  bake manifest.
- **D54**, the strategic universe — the supplying database is the authority. Portfolios are
  read from its extract (`SCENARIO_SAA_SOURCE`), each name parsed into the key
  `currency|riskLevel|allocationType|excludeRealAssets`, and every selector option is derived
  from the key set. This changes the key format (currency in, tactical allocation out), the
  availability set, the bake (which now enumerates the extract and records facets, provenance
  and unparsed names in the manifest), and what `get_schema` serves. The parse is offline and
  strict; run the bake's `--census` against the real extract before anything else.
- **D53**, the strategic volatility premium — a second implementation overlay beside the tilt.
  A toggle introduces a *Hybrid Fixed Income* category holding one product, weighted at
  `rules.VOL_PREMIUM_SHARE` of Investment Grade Fixed Income after the tilt has been funded
  out of it and funded pro rata from the same products, inserted directly after its funding
  category. Enabled in USD and GBP only, and that rule is applied in `volPremiumCategories`
  as well as on the toggle. It adds `volPremium` to scenario state and to `implementation`,
  a second entry to `AUTO_SLEEVE_CATEGORIES`, four keys to the schema's `rules` block, and a
  `currency` argument to `buildImplementationRows`. No bake dimension: like the tilt it moves
  weight between implemented categories and never re-resolves a portfolio.
- **D52**, fees are optional — a proposal shows none until an **Include fees** tick box asks
  for them, and a new scenario starts with it off. It adds `includeFees` to scenario state and
  to `implementation`, and `workbook.implColumns(includeFees)` decides whether the sheet is
  fourteen columns or eleven. Two consequences to carry across: the export needs no fee
  schedule when fees are excluded, and a scenario stored without the field takes it from
  whether a schedule was ever chosen.
- **D47**, the `engine.py` seam described in §2.
- **D48**, the renames that came with it — see *Carried over* below.

## Carried over

Things a porter will meet that are not defects in the port itself:

- **`spec.html` §4.4 and §14.2 are superseded.** They print the old adapter name
  (`epsilonphi`, now `live`) and the old export filename (`EpsilonPhi_Scenario_*.xlsx`, now
  `PMG_Scenario_*.xlsx`). The spec was kept as the historical record; DEVIATIONS D48 carries
  the current values.
- **The baked payloads carry the pre-D35 naming.** Their stored `name` / `header` read
  `USD Core Agg`, not `USD Aggressive Core`. The screen is unaffected — it derives every name
  from the key (D36) — but **the exported workbook reads those fields**, so a workbook built
  from baked data shows the old form. Fixed by a re-bake, or by migrating the stored fields.
- **Sleeve libraries for three of the four variants are invented.** Only PMG Multi-Asset is
  real. Spec open item 18, and the one thing here that must not reach a client as it stands.
- **The per-variant allocation table is authored here**, in `rules.VARIANT_ALLOCATIONS` and
  `rules.VARIANTS_EXCLUDING_RE`. It encodes what was specified — Multi-Asset all four, ESG
  `Ex HFs`/`Ex Alts` with real estate off, US and Irish Onshore `Ex Alts` — and is the table
  to confirm with PMG alongside the sleeve libraries.

## Not addressed

- **No tests reach the host.** The dashboard package has none and this adds none to it; the
  suite lives on the epsilon-phi side in `service/tests/` — 89 passing, plus 4 that need a
  live database and are gated behind `SAA_ENGINE_LIVE=1`.
- **`dataversion`.** Every analytics config table is keyed on `currency` + `dataversion`.
  Which version the adapter reads is a host concern; no control is surfaced. It does decide
  when the bake is stale — re-bake when it changes.
- **Currency coverage.** On the development database only USD and GBP have currency configs,
  so CHF and EUR cannot resolve at all. The UI offers four because the supplied weights carry
  four. A host decision: restrict the list, or load the rows.

## Where the rest is written down

| File | What it carries |
|---|---|
| `service/README.md` | running it, the wire contract, the three adapters, baking |
| `service/TRANSPLANT.md` | the porting list, file by file |
| `service/DEVIATIONS.md` | every departure from the package, D1–D64, the adapter-side decisions, and the spec gaps found (G1–G8) |
| `service/PERFORMANCE.md` | the analytics profile, its causes, and the measurements |
| `spec.html` | revision 6 — what the tool does, §1.5 mapping the changes |
