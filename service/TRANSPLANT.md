# Transplant rehearsal — copying this into `isg-cyrus-pmg/src/cyrus_pmg`

The governing test: **what has to change must be configuration, never
structure.** Every file below is classed as one of:

* **COPY** — copied verbatim, no edits.
* **INSERT** — a block added to an existing host file, written here in its
  final form.
* **STAND-IN** — exists only to mirror the host in epsilon-phi; the host
  already has its own. Not copied.
* **CONFIG** — an environment value the host sets.

## 1. The page folder — COPY

```
cp -r proposal-tool/proposalTool  isg-cyrus-pmg/src/cyrus_pmg/dashboard/
```

Verbatim, per PORTING.md. Fonts travel inside it. No edits: URLs are relative,
the two shared-JS includes (`/static/js/accessGate.js`, `/static/js/globals.js`)
are the host's own files at the host's own paths.

## 2. The scenario package — COPY

```
cp -r proposal-tool/service/cyrus_pmg/pmgService/scenario \
      isg-cyrus-pmg/src/cyrus_pmg/pmgService/
```

Verbatim. The package uses only intra-package relative imports, and has exactly
two outward seams:

* `cyrus_pmg.pmgService.core.accessControl`, imported by the router (below),
  not by the package itself. Since D57 the router takes a third name from it,
  `requireAdmin`, beside `requireAuth` and `requireEditor` &mdash; the host's
  module needs that one addition (a dependency reading `PMG_ADMIN_KERBEROS`,
  or whatever source its other allowlist reads), plus `isAdmin` and
  `getKerberosFromFastApiRequest`, which the schema route uses to tell the
  page whether the caller may open the sleeve repository.
* **`engine.py` — the analytics library.** It is the only module that names
  that library, and it resolves its six symbols lazily against
  `SAA_ENGINE_PACKAGE` (default `epsilonPhi`). If the host's SAA library is
  named differently, set that variable; if its internal layout also differs,
  edit the `_SYMBOLS` table in that one file. Nothing else in the package —
  adapter, loader, bake, router — names it at all (D47).

Data files travel inside it (`advisors.xlsx`, `fees.json`, `feeRates.csv`).
The strategic universe does not: it is read from `SCENARIO_SAA_SOURCE`, the
supplying database's extract, which the host points at (D54, below). Nor do
the products or the sleeves: the catalogue is read from
`SCENARIO_PRODUCTS_SOURCE` (D56) and the sleeve library lives in a database
the service writes at `SCENARIO_SLEEVES_DB`, seeded once from
`SCENARIO_SLEEVES_SEED` (D57, below).

`bake.py` travels with the package as an offline job (`python3 -m
cyrus_pmg.pmgService.scenario.bake --all --workers 4`), run on a data refresh
rather than at request time; `bakedAdapter.py` serves its output. Neither adds
a host dependency.

**One change outside the package:** the Tier 0 optimisation of
the analytics library's `core/estimator/assetReturnEstimator.py` (deviation
D16) lives in that library, not here. It is bit-identical and independently
useful, so it should be raised with its owners as its own change rather than
carried as part of this transplant. Without it the Proposal Tool still works —
live resolves and bakes simply cost ~2.6× more.

What the host swaps later, behind unchanged functions, when real sources
arrive — all internal to the package, no caller changes:

* **`SCENARIO_SAA_SOURCE` → the supplying database's extract** (D54). The
  strategic universe is read from it: `PortfolioName, AssetTicker, Weight`,
  CSV or XLSX, one row per holding. Every currency, risk level and allocation
  type the UI offers is derived from the names in it. The packaged default
  is the fictitious stand-in under `proposal-tool/saaSource`; run
  `python3 -m cyrus_pmg.pmgService.scenario.bake --census` against the real
  one first and fix any name it rejects before baking. `saaKeys.RISK_LEVELS`
  and `ALLOCATION_TYPES` are the closed vocabularies the parser matches
  against, in selector order - confirm them against the database's own.
* **`SCENARIO_PRODUCTS_SOURCE` → the product database's extract** (D56):
  one row per product, `ProductId, Name, Ticker, AssetClass, Style, Vehicle,
  Source, Liquidity, ExposureCurrency, ProductCost, FeeGroup`, CSV or XLSX.
  `ProductId` is the key sleeves reference, so it must be stable across
  deliveries; `FeeGroup` must be one the fee card prices or the extract is
  refused at load. `DistributionYield` and `MinimumInvestment` are optional
  columns (D63) the catalogue view compares on; absent, they serve as blanks. The packaged one is the stand-in under
  `proposal-tool/productSource`.
* **`SCENARIO_SLEEVES_DB` → a host-writable path** (D57). The sleeve library
  is a SQLite file the service creates on first open and seeds from
  `SCENARIO_SLEEVES_SEED` (the packaged `proposal-tool/sleeveSource/sleeves.csv`
  &mdash; PMG's own sleeve names, 110 across the four books, with placeholder
  products and weights, D59); after that the database is the library and admins
  maintain it in the app. Back it up like any small
  database; move it between environments with `python3 -m
  cyrus_pmg.pmgService.scenario.sleeveTools --export` / `--import`. The only
  contract the rest of the package depends on is unchanged: `VARIANTS`,
  `listSleeves(category, variant)` and `sleeveExists(category, name,
  variant)` in `sleeves.py`. The seeded sleeves for the three non-Multi-Asset
  types are invented — spec open item 18, and the one thing here that must
  not reach a client as it stands (D29); the repository is how PMG replaces
  them. The same console shows admins the catalogue itself, view only, with
  every product's sleeves and the products a delivery dropped (D58) - run
  `sleeveTools --census` after a delivery for the same list at the shell.
* `rules.TACTICAL_TILT_PCT` / `_FUNDED_FROM` / `_CATEGORY` → PMG's own tilt
  size and funding source (D50). Changing the percentage is a constant;
  changing the funding category is a constant plus a re-check that every
  offered portfolio can fund it, which `canFundTacticalTilt` already gates.
* **`SCENARIO_FEES_SOURCE` → the delivered rate card** (D51, D55): a long
  CSV, one row per cell (`schedule, feeGroup, tier, tierMin, tierMax, source,
  point, rate`), from which the tiers and fee groups are derived. **The
  packaged `feeRates.csv` is a placeholder** and `fees.json` says so
  (`"placeholder": true`); the rail and the workbook flag it until a real
  card is accepted. The service cannot change the card; review a delivery with
  `python3 -m cyrus_pmg.pmgService.scenario.feeTools --diff <csv>` - it lists
  every cell that moved and any local adjustment sitting on one - then
  `--accept <csv> --version V --source S`, which stamps the provenance into
  `fees.json`. The reader rejects a card with a gap, disagreeing tier edges
  or a floor above its ceiling. The UI shows the card through a read-only
  viewer and has no way to edit it. The products' `feeGroup` values in `sleeves.py` are assigned by
  a rule of thumb (passive, core active, specialist active, alternatives,
  asset allocation) and want confirming against PMG's own grouping when the
  library is swapped. None of it is reached at all until a PWA ticks
  **Include fees** (D52), which a new scenario starts without.
* The **Strategic Volatility Premium** product and its *Hybrid Fixed Income*
  category (D53). Everything about it is stub data on the same footing as the
  rest of the library — the ticker, the 0.65% product cost and the
  *Specialist Active* fee group are placeholders — and it is offered under all
  four variants from one definition. `rules.VOL_PREMIUM_SHARE` (0.075),
  `VOL_PREMIUM_FUNDED_FROM` and `VOL_PREMIUM_CURRENCIES` are the constants to
  confirm with PMG; changing any of them is a one-line change that both the
  server and the page pick up, since the page reads all three from the schema.
* `rules.VARIANT_ALLOCATIONS` and `rules.VARIANTS_EXCLUDING_RE` → the same
  question for the *allocations* each variant may hold, which is a stricter
  rule than the sleeves: it decides what can be built at all (D49). Confirm
  this table with PMG alongside the libraries above.
* `advisors.py` workbook read → the production advisor table (open item 7).
* `portfolio_weights.py` keeps only the engine bridge (`get_portfolio`, the
  hedge ratios of D3, `ASSET_METADATA`). Its synthetic anchor tables and the
  generated frame are no longer read for weights or availability (D54).
* `HEDGE_RATIOS_BY_OPTION` → the host's hedging service or house ratios (D3).

## 3. The router endpoints — INSERT

Append the scenario endpoint block of
`proposal-tool/service/cyrus_pmg/pmgService/dashboardRouter.py` (everything
from the imports it needs through `exportScenario`) into the host's existing
`pmgService/dashboardRouter.py`. The block is written in its final form:
mixedCase handlers, reads inheriting the router-level `requireAuth`, writes
taking `Depends(requireEditor)`, imports addressed to
`cyrus_pmg.pmgService.scenario.*` and `cyrus_pmg.pmgService.core.accessControl`.

One thing to verify on the host (behaviour, not structure): its service must
emit auth failures as top-level `{error}` / `{loginUrl}` JSON, which its pages
already rely on (`body.error` in `preferredProducts.js`). The mirror's
stand-in service maps them explicitly; if the host's doesn't, that is a host
bug its own pages share.

## 4. The Flask route + nav link — INSERT (the PORTING.md recipe)

In the host's `dashboardFrontend.py`, beside the other per-page routes:

```python
@app.route('/proposalTool/<path:filename>')
def serve_proposal_tool(filename):
    """Serve Proposal Tool static files."""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'proposalTool'), filename)
```

In the host's `index.html` header block, the nav link from PORTING.md §3,
copying the surrounding inline styles verbatim.

## 5. Stand-ins — NOT COPIED

| Mirror file | Host already has |
|---|---|
| `dashboard/dashboardFrontend.py` | Its own (gate, proxy, routes). Only the §4 insert is new. |
| `dashboard/dashboardConfig.py`, `dashboard/index.html` | Its own (plus the nav insert). |
| `dashboard/static/js/accessGate.js`, `globals.js` | Its own shared modules — the reason the page references them at `/static/js/…`. |
| `pmgService/isgPMGService.py`, `pmgService/config.py` | Its own service and settings; `dashboardRouter` is already `include_router`-ed, so no change there (spec §3.4). |
| `pmgService/core/accessControl.py` | Its own auth module with the same names (`requireAuth`, `requireEditor`, `getKerberosFromFlaskRequest`, `isAllowed`, `buildLoginUrl`) — the stub exists so the mirror runs without GSSSO. |
| `start_dashboard.sh`, `stop_dashboard.sh`, `dashboard.env.defaults` | Its own launcher trio. |
| `tests/` | Deliberately not shipped — the host dashboard package is untested (spec §16 item 10) and the suite runs on the epsilon-phi side. |

## 6. Configuration — the complete list of what changes

| Setting | Here | There |
|---|---|---|
| `SCENARIO_ADAPTER` | `fixtures` default | `baked` (recommended) or `live`, in `dashboard.env.defaults` |
| `SCENARIO_BAKED_DIR` / `SCENARIO_BAKED_FALLBACK` | `service/var/baked` / `1` | A host-writable store path; the bake is a scheduled job re-run when `dataversion` changes |
| `PMG_ALLOWED_KERBEROS` | dev default | The real allowlist source the host already manages |
| `PMG_ADMIN_KERBEROS` | dev default | Who may maintain the sleeve repository (D57): a subset of the access list, from the same source |
| `SCENARIO_PRODUCTS_SOURCE` | `productSource/products.csv` | The product database's extract (D56) |
| `SCENARIO_SLEEVES_DB` / `SCENARIO_SLEEVES_SEED` | `service/var/sleeves.db` / `sleeveSource/sleeves.csv` | A host-writable database path; the seed is read once, when it is first created (D57) |
| `SAA_ENGINE_PACKAGE` | unset (`epsilonPhi`) | The host's analytics package, if it is named differently — the only edit `engine.py` needs |
| `SCENARIO_STORE_DIR` | system temp | A host-writable spool directory |
| `SCENARIO_RETENTION_HOURS` | 24 | PMG's answer to open item 8 |
| Ports / hosts | 8001/8002 on 127.0.0.1 | The host's `FRONTEND_PORT` / `PMG_SVC_PORT` on 0.0.0.0 |
| Database / `env.ini`, `dataversion` | epsilon-phi's DEV config | The host's analytics-library configuration (open item 9) |
| Analytics window (`CONTEXT_START_DATE` / `_END_DATE` in `portfolio_weights.py`) | 30-Nov-1983 → 31-Dec-2022 | Whatever window the host's model set prescribes |
| Theme | mpo-ui tokens | OneGS `--gs-*` token block at port time — a token swap by construction (§6.5), plus the §6.2 contrast re-audit |

**Nothing structural remains on the list.** Route shapes, handler names, the
proxy contract, the auth seams, file layout and naming all already match the
host; the four actions above are two verbatim copies and two documented
inserts.
