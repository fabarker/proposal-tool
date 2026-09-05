# Porting the Proposal Tool into `cyrus_pmg` — the playbook

**Written against commit `9f4dc50` (2026-09-03).** This document is the single porting guide.
`service/TRANSPLANT.md` is now a pointer to it; do not maintain two lists. The evidence behind
every statement here is in `PORTING_GUIDE_AUDIT.md`.

Paths are relative to `epsilon-phi-core/proposal-tool/` unless they start with `isg-cyrus-pmg/`.
"The host" means `isg-cyrus-pmg/src/cyrus_pmg` as described in `HOST_AUDIT.md`. Every claim
about the host carries one of three tags:

- **Verified** — checked against this repository's code, tests or a running instance.
- **Audit** — stated by `HOST_AUDIT.md` (2026-08-30) and not independently re-checked.
- **Unverifiable** — needs the live Cyrus codebase; listed again in §18.

---

## 1. Purpose, scope and non-goals

**Purpose.** Move the Proposal Tool — one page folder, one Python package, one block of router
endpoints — into the Cyrus PMG dashboard so that it runs behind the host's Flask gate and
FastAPI service, with no analytics library and no database on the request path.

**Scope.**

- Copy the page folder `proposalTool/` into `isg-cyrus-pmg/src/cyrus_pmg/dashboard/`.
- Copy the package `service/cyrus_pmg/pmgService/scenario/` into
  `isg-cyrus-pmg/src/cyrus_pmg/pmgService/`.
- Insert the endpoint block of `service/cyrus_pmg/pmgService/dashboardRouter.py` into the host's
  `pmgService/dashboardRouter.py`.
- Add three names to the host's `pmgService/core/accessControl.py`.
- Add one Flask route and one nav link.
- Configure twenty environment variables, deliver four data extracts and one baked store, and
  install two Python dependencies if the host lacks them.

**Non-goals.**

- Porting the analytics library (`epsilonPhi`). It is not on the request path (D67) and this
  guide keeps it off. Analytics are computed by an offline bake on a machine that has the library
  and delivered to the host as data (§11.5).
- Replacing placeholder data. The fee card, the product catalogue's figures, the sleeve contents
  and the advisor directory are stand-ins; the port carries them as they are and §17 says what
  must land before a client sees the tool.
- Retheming to OneGS. Deliberately a separate change (§10.5, §17).
- Tests in the host's dashboard package (it has none — Audit §10). The suite stays on the
  epsilon-phi side as the pre-port gate (§14).
- Deployment pipeline changes. The host zips `src/cyrus_pmg/**` (Audit §6); this guide tells
  you what must live outside that tree and nothing about how it is distributed.

---

## 2. What the tool is today

A single page for Private Wealth Advisors. Step 1 builds a strategic asset allocation: enter the
mandate, choose currency and hedging, resolve a base portfolio from the supplying database's
universe and compare it against up to three more. Step 2 implements it: choose an implementation
type (one of four books), attach one sleeve of products to each category, price the fees, and
download an Excel proposal. Every delivered proposal is recorded permanently.

Behind it, in the order a request travels:

1. **The page** — one generated HTML/CSS/JS folder, no framework, no build step at serve time.
2. **The Flask gate and proxy** — the host's own; `/api/<x>` becomes `/api/v1/<x>`.
3. **The router block** — 25 endpoints on the host's FastAPI `dashboardRouter`: 5 reads, 5 editor
   writes, 15 admin routes for the sleeve repository console.
4. **The scenario package** — 25 modules. A `ScenarioPort` protocol with three implementations
   (`fixtures`, `live`, `baked`), the rules, the wire payloads, four stores and the Excel writer.
5. **The data** — four delivered extracts (strategic portfolios, products, fee card, advisors), a
   baked analytics store produced offline, and four stores the tool owns (scenarios, sleeves, the
   proposal register, and the bake).

In production the service runs **`SCENARIO_ADAPTER=baked` with `SCENARIO_BAKED_FALLBACK=0`**: every
portfolio a user can select is precomputed, a resolve is a dictionary lookup (measured 6–9 ms), an
export is written by the tool in ~200 ms, and the analytics library is never imported (Verified —
§14.3).

---

## 3. Architecture and component boundaries

```text
browser ──► Flask :8001 (host: dashboardFrontend.py)
              ├─ /proposalTool/<file>     send_from_directory(dashboard/proposalTool)   ◄─ INSERT one route
              ├─ /static/js/accessGate.js, /static/js/globals.js   the host's own shared JS
              └─ /api/<x>  ─proxy─►  FastAPI :8002 (host: isgPMGService.py, PMG_SVC_PORT)
                                        └─ /api/v1/…  dashboardRouter  ◄─ INSERT the endpoint block
                                              │  Depends(requireAuth | requireEditor | requireAdmin)
                                              ▼
                                        cyrus_pmg.pmgService.scenario   ◄─ COPY verbatim
                                              ├─ registry.getScenarioPort()  → bakedAdapter (prod)
                                              ├─ rules · payloads · types · saaKeys · universe
                                              ├─ scenarioStore (JSON files)   sleeveRepo (SQLite)
                                              ├─ proposalRegister (SQLite)    products · fees · advisors
                                              ├─ workbook (openpyxl)          assetEstimates
                                              └─ engine.py ─lazy─► the analytics library  (bake only)
```

### 3.1 The page folder — `proposalTool/` (COPY)

| File | Size | Notes |
|---|---:|---|
| `proposalTool.html` | 11,136 B | Loads `/static/js/accessGate.js` then `/static/js/globals.js` (absolute, the host's files), then `static/js/proposalTool.js` at the end of `<body>`. Stylesheet by relative `href`. |
| `static/css/proposalTool.css` | 123,203 B | 83 tokens on `:root`; `@font-face` `src:url("../fonts/…")` (Verified). |
| `static/js/proposalTool.js` | 343,720 B | One file, `'use strict'`; four IIFEs concatenated in load-bearing order (core → picker → implementation → repository) plus a boot block. |
| `static/fonts/*.woff2` | 5 files, ~235 KB | goldman-sans-regular, gs-sans-variable, gs-sans-condensed-variable, roboto-regular, roboto-medium. |

The folder is **generated**: `python3 generator/build_styles.py`, run from `proposal-tool/`, writes it
and the identical mirror at `service/cyrus_pmg/dashboard/proposalTool/` (Verified byte-identical).
Never hand-edit either copy.

What the page assumes of its host (all Verified in the mirror):

- `window.API_BASE` is set by `globals.js`; the page guards with
  `if (typeof API_BASE === 'undefined') window.API_BASE = window.location.origin + '/api'`.
- Every call is `fetch(window.API_BASE + path, {credentials: 'same-origin'})` through one
  `apiFetch`; a non-2xx JSON body's `error` is shown, a `loginUrl` is followed.
- Errors render into `#alertArea`; a 502/504 from the proxy marks the service down and the page
  degrades read-only from a `localStorage` snapshot (D64).
- Browser state: `localStorage` keys `pmg.proposalTool.railCollapsed` and `pt.snapshot.<scenarioId>`;
  URL deep link `?scenario=<id>` via `history.replaceState`; console views on the hash
  (`#repository`, `#catalogue`, `#archive`, `#activity`, `#proposals`).
- No `onegsTheme.css`, no `dashboard.css`, no `isgCodeSelector.js`, no Chart.js, no host header
  block — all deliberate (§10.4).

### 3.2 The scenario package — `service/cyrus_pmg/pmgService/scenario/` (COPY)

25 Python modules (7,732 lines) and four data files. Only intra-package relative imports; no
`cyrus_pmg.*` and no `..core` import anywhere inside it (Verified). Its two outward seams are
`engine.py` (the analytics library, lazily) and the host's `accessControl`, which only the router
imports.

| Module | Role | Runtime on the baked path? |
|---|---|---|
| `scenarioPort.py` | The eight-method `ScenarioPort` protocol | contract |
| `types.py` | `BasisInput`, `MandateInput`, `PortfolioKey`, `ValidationError`, `ScenarioNotFound`, `AnalyticsError` | yes |
| `registry.py` | `getScenarioPort()` — reads `SCENARIO_ADAPTER`, `SCENARIO_BAKED_FALLBACK` | yes |
| `bakedAdapter.py` | `BakedScenarioPort` — dictionary lookup over slice files; export via `workbook` | **the production adapter** |
| `fixturesAdapter.py` | `FixturesScenarioPort` — synthetic analytics, failure knobs | demo/debug only |
| `liveAdapter.py` | `LiveScenarioPort` — real analytics through `engine` | bake, or `SCENARIO_BAKED_FALLBACK=1` |
| `engine.py` | The only module naming the analytics library: `_PACKAGE` from `SAA_ENGINE_PACKAGE`, five symbols in `_SYMBOLS` (`CAppConfig`, `DATAVERSION`, `ContextCreator`, `SAAPortfolio`, `AssetReturnEstimator`), PEP 562 lazy resolution | never resolved on the baked path |
| `rules.py` | Floors, caps, tilt, volatility premium, variants, sleeve groups, availability, validators, `schemaPayload` | yes |
| `saaKeys.py` | Portfolio name ⇄ key parser and the closed vocabularies | yes |
| `universe.py` | The strategic universe read from `SCENARIO_SAA_SOURCE` — keys, facets, weights, category rows | **yes — read at request time** for availability and category rows |
| `payloads.py` | `PortfolioResult` shaping, finiteness guard, largest-remainder rounding | yes |
| `portfolio_weights.py` | Asset metadata (`ASSET_METADATA`, 19 assets), hedge ratios, context window, the engine bridge (`get_context`, `get_portfolio`). Imports **pandas** and builds two frames at import | imported at start (§12) |
| `scenarioStore.py` | One JSON file per scenario under `SCENARIO_STORE_DIR`, atomic writes, `SCENARIO_RETENTION_HOURS` | yes |
| `sleeves.py` | Facade: `VARIANTS`, `listSleeves`, `sleeveExists`, `variantExists` | yes |
| `sleeveRepo.py` | The sleeve library: SQLite at `SCENARIO_SLEEVES_DB`, schema v2, seeded once from `SCENARIO_SLEEVES_SEED`, append-only history, soft delete, migration | yes |
| `sleeveTools.py` | CLI: census, export, import, history, archived, activity | operations |
| `products.py` | The delivered catalogue from `SCENARIO_PRODUCTS_SOURCE`, validated at load, reloaded on mtime | yes |
| `fees.py` | The rate card from `SCENARIO_FEES_SOURCE` + `fees.json`; tiers, groups, levels, `managementFee` | yes |
| `feeTools.py` | CLI: census, diff, accept | operations |
| `advisors.py` | Primary PWA directory from the packaged `advisors.xlsx` (no override) | yes |
| `assetEstimates.py` | Per-asset long-term estimates for the assumptions sheet: store copy, else packaged, `SCENARIO_ASSET_ESTIMATES` overrides | yes |
| `workbook.py` | The whole Excel proposal with openpyxl alone: `portfolios`, `risk_dashboard`, `assumptions`, `Implementation`, hidden `chartData`; `buildImplementationRows` | yes |
| `proposalRegister.py` | Every delivered proposal, SQLite at `SCENARIO_REGISTER_DB`, schema v1, append-only, workbook bytes + SHA-256 | yes |
| `bake.py` | Offline bake CLI and the store layout (`manifest.json`, `<CCY>_<Hedging>.json`, `assetEstimates.json`); `storeDir()` reads `SCENARIO_BAKED_DIR` | store layout at runtime; the CLI offline |
| `__init__.py` | Package docstring (its module list is stale — ignore it) | — |

Data files inside the package: `advisors.xlsx` (24 rows), `fees.json`, `feeRates.csv` (180 cells,
placeholder), `assetEstimates.json` (the packaged fallback for the assumptions sheet).

### 3.3 The endpoint block — `service/cyrus_pmg/pmgService/dashboardRouter.py` (INSERT)

696 lines, entirely the block. Anatomy (line numbers as at `9f4dc50`):

- lines 20–39: imports — `fastapi` (`APIRouter, Body, Depends, Request, Response`, `JSONResponse`),
  `cyrus_pmg.pmgService.core.accessControl` (`getKerberosFromFastApiRequest, isAdmin, requireAdmin,
  requireAuth, requireEditor`), and `cyrus_pmg.pmgService.scenario.*`;
- line 41: `router = APIRouter(dependencies=[Depends(requireAuth)])` — **the host already has this
  line; do not insert it**;
- line 43: `_XLSX` media type; lines 51–54: `getScenarioPort()` warm-up at import, exceptions
  swallowed and reported per request;
- helpers `_validationError`, `_notFound`, `_analyticsError`, `_includeFees`, `_scenarioPayload`,
  `_feedFilters`, `_csv`;
- 25 handlers (§11.1). All sync `def` on purpose: FastAPI runs them in the threadpool.

The router reads no environment and opens no file itself (Verified); everything goes through the
package.

### 3.4 The stand-ins — mirror the host, NOT shipped

| Mirror file | The host already has |
|---|---|
| `service/cyrus_pmg/dashboard/dashboardFrontend.py` | Its own gate, routes and proxy. Only the §10.2 route is new. The mirror's `/_dev_login` is a GSSSO stand-in and must not be ported. |
| `service/cyrus_pmg/dashboard/dashboardConfig.py`, `index.html` | Its own (plus the §10.3 nav link). |
| `service/cyrus_pmg/dashboard/static/js/accessGate.js`, `globals.js` | Its own shared modules (Audit §3). |
| `service/cyrus_pmg/pmgService/isgPMGService.py`, `config.py` | Its own app and settings. The mirror's exception handlers document the error contract the host must already meet (§13.3). |
| `service/cyrus_pmg/pmgService/core/accessControl.py` | Its own auth module. Three names must be added to it (§9.2). |
| `service/start_dashboard.sh`, `stop_dashboard.sh`, `dashboard.env.defaults` | Its own launcher trio; the env defaults are the reference for §11.4. |
| `service/tests/`, `service/var/`, `service/weo_2026_1.csv` | Not shipped (§14, §11.3; the CSV is an empty artefact of the IMF WEO download, git-ignored). |

### 3.5 Development-side material — NOT shipped

`generator/` (the page's source), `backend/` (the original hand-off `scenario_port.py` and
`portfolio_weights.py`), `saaSource/`, `productSource/`, `sleeveSource/` (stand-in extracts — the
**contents** are delivered as data, §11.5), `spec.html`, `DECISIONS.md`, `archive/BRIEF.md`, the design
studies (`archive/*.html`), `service/DEVIATIONS.md`, `service/PERFORMANCE.md`.

### 3.6 Where the package's defaults resolve — and why that matters

Every data path in the package is `os.getenv(...) or <default relative to the module>`. Resolved
against the package directory (Verified at runtime):

| Setting | Default resolves to | In the host that would be |
|---|---|---|
| `SCENARIO_SAA_SOURCE` | `../../../../saaSource/saaPortfolios.csv` | `isg-cyrus-pmg/saaSource/…` — **does not exist** |
| `SCENARIO_PRODUCTS_SOURCE` | `../../../../productSource/products.csv` | `isg-cyrus-pmg/productSource/…` — **does not exist** |
| `SCENARIO_SLEEVES_SEED` | `../../../../sleeveSource/sleeves.csv` | `isg-cyrus-pmg/sleeveSource/…` — **does not exist** |
| `SCENARIO_SLEEVES_DB` | `../../../var/sleeves.db` | `isg-cyrus-pmg/src/var/sleeves.db` — inside the source tree |
| `SCENARIO_REGISTER_DB` | `../../../var/proposals.db` | `isg-cyrus-pmg/src/var/proposals.db` — inside the source tree |
| `SCENARIO_BAKED_DIR` | `../../../var/baked` | `isg-cyrus-pmg/src/var/baked` — inside the source tree |
| `SCENARIO_STORE_DIR` | `$TMPDIR/pmg_proposal_scenarios` | the system temp directory — lossy |
| `SCENARIO_FEES_SOURCE` | `feeRates.csv` beside `fees.py` | travels with the package (placeholder) |
| `SCENARIO_ASSET_ESTIMATES` | `<bake store>/assetEstimates.json`, else the packaged copy | travels with the package |
| advisors | `advisors.xlsx` beside `advisors.py` — no override | travels with the package (stub) |

So in the host the first three variables are **mandatory** (the package fails at first read
without them — `universe.py` raises on a missing extract, `products.py` raises `BadCatalogue`,
`sleeveRepo` cannot seed) and the next four must be set to durable, writable locations outside
`src/`. This is the single largest correction to the previous guide.

---

## 4. The Cyrus integration point

### 4.0 What the real host files settle

Six actual Cyrus files are in the repository at `../cyrus-files/`, supplied 2026-09-05:
`pmgEntitlement.py`, `dashboardRouter.py`, `dashboardFrontend.py`, `exceptionHandlers.py`,
`config.py`, `dashboard.env.defaults`. They are transcriptions with marked gaps, but they are
source rather than description, and they **replace `HOST_AUDIT.md` wherever the two differ**.
Everything in this section is Verified against them unless it says otherwise.

**Confirmed compatible — the port's structural assumptions all hold:**

| Assumption | Evidence |
|---|---|
| `dashboardRouter.py` exposes a module-level `router` | `router = APIRouter(dependencies=[Depends(requireAuth)])` — byte-identical to this mirror's line 41 |
| The proxy rewrites `/api/<x>` → `/api/v1/<x>` | `dashboardFrontend.proxy_api`: `f'{_get_backend_url()}/api/v1/{path}'`, `timeout=300`, `allow_redirects=False`, 502/504 |
| The proxy forwards the identity | it copies every header except `host`/`connection`/`transfer-encoding`, so the GSSSO cookie travels |
| One hand-written route per page folder | eleven of them, all `send_from_directory(os.path.join(DASHBOARD_DIR, '<page>'), filename)` |
| `_PUBLIC_PATHS` | `/health`, `/favicon.ico`, `/_access_denied`, `/api/whoami`, `/static/css/`, `/static/js/accessGate.js` — the mirror's list exactly |
| `/api/whoami` exists and is public | on a separate `publicRouter`; returns `{success, kerberos, allowed, role, canView, canModify, canPost, env, …}` and adds `loginUrl` when there is no identity |
| `getKerberosFromFastApiRequest`, `isAllowed`, `getAllowlist`, `buildLoginUrl` | all called by the host's own `whoami` |
| `requireAuth` **and** `requireEditor` both exist | `pmgEntitlement` back-compat shims: `requireAuth = requirePmgApiView`, `requireEditor = requirePmgApiModify` |
| `PMG_SVC_` settings prefix | `config.py` `env_prefix`; no collision with `SCENARIO_*` or `PMG_ALLOWED_KERBEROS` |
| Ports 8001 / 8002 / 8003 | `dashboard.env.defaults` — the 8088 in an earlier screenshot was wrong |

**The auth module is split across two files, not one.** The block's single import line must become
two:

| Name | Lives in | Status |
|---|---|---|
| `getKerberosFromFastApiRequest`, `isAllowed`, `getAllowlist`, `buildLoginUrl`, `requireAllowlistedUser` | `pmgService/core/accessControl.py` | exists |
| `requireAuth`, `requireEditor`, `requirePoster`, `can`, `hasRole`, `PmgEntitlement` | `pmgService/core/pmgEntitlement.py` | exists |
| `requireAdmin`, `isAdmin` | **neither** | must be written — §9.2 |

**Cyrus has a three-role entitlement model**, not the one role the mirror stubs. Roles `ISGAdmin`,
`PMGEditor`, `PMGViewer`; resources `pmgui`, `pmgapi`, `optimizationapi`; actions `view`, `modify`,
`post`. `requireAuth` is `pmgapi:view` (all three roles), `requireEditor` is `pmgapi:modify`
(ISGAdmin and PMGEditor). Two consequences:

- **`post` is PMGEditor-only — ISGAdmin does not have it**, per the policy table. So there is no
  `resource:action` that isolates an administrator, and `requireAdmin` has to test role membership
  (`hasRole(userData, 'ISGAdmin')`) rather than a permission.
- **In PROD the allowlist alone grants only `view`.** A kerberos on `PMG_ALLOWED_KERBEROS` but in
  no PERMIT group can read the tool and cannot create a scenario, attach a sleeve or export. In
  DEV/UAT-and-below every caller is short-circuited to a synthetic `ISGAdmin`. Getting PWAs into
  `PMGEditor` is a go-live prerequisite, not a detail.

**Two incompatibilities were found and fixed on this side** (§9.2, §13.3): the host's auth
dependencies return a `UserData` object where this block bound a string, and the host's
`PmgAppException` puts the HTTP status phrase in `error` with the message in `detail`, the
opposite way round from this block's own errors.

**Still to confirm against the checkout** (§18): the router docstring says it is "mounted under
`/api/v1/dashboard` by the optimizationService application", which contradicts the live proxy line
building `/api/v1/{path}` — and the host's own pages would 404 if the docstring were current, so it
reads as stale. Confirm before Step 4, because if the mount really is `/api/v1/dashboard` every
path in §11.1 shifts.

### 4.1 What the audit establishes (unchanged by the above)

Audit, unless marked otherwise:

- The dashboard is static page folders under `isg-cyrus-pmg/src/cyrus_pmg/dashboard/`, one Flask
  `@app.route('/<page>/<path:filename>')` per folder served with `send_from_directory` (Audit §7.1), a
  hard-coded nav `<a>` in `index.html` (Audit §7.2), and a `before_request` kerberos allowlist gate (Audit §7.3)
  whose public paths are `/health`, `/favicon.ico`, `/_access_denied`, `/api/whoami`, `/static/css/*`
  and `/static/js/accessGate.js`.
- The proxy is one wildcard route: `/api/<path>` → `http://…:PMG_SVC_PORT/api/v1/<path>`,
  `timeout=300`, `allow_redirects=False`, 502 on `ConnectionError`, 504 on `Timeout` (§4, §13).
- The backend is FastAPI: `pmgService/isgPMGService.py` mounts `pmgService/dashboardRouter.py`
  under `/api/v1` (§4); handlers are `mixedCase`; the router carries `Depends(requireAuth)` and
  writes take `Depends(requireEditor)` (§9, §12 step 7).
- Shared JS: `accessGate.js` probes `/api/whoami` and paints a badge or overlay; `globals.js` sets
  `window.API_BASE` (§3, §9). No module system; script order matters (§11).
- Auth module: `pmgService/core/accessControl.py` with `getKerberosFromFlaskRequest`, `isAllowed`,
  `buildLoginUrl`, `requireAuth`, `requireEditor` (§9, §11) and the allowlist in
  `PMG_ALLOWED_KERBEROS` (Audit §5.5).
- Ports: `FRONTEND_PORT` 8001, `PMG_SVC_PORT` **8002**, `OPT_SVC_PORT` 8003 (Verified:
  `cyrus-files/dashboard.env.defaults`). `PMG_SVC_WORKERS` defaults to **2** in that file, though
  `config.py`'s own fallback is 4 — so at least two workers, four if the env file is not sourced.
- Dependencies named: Flask 2.2.5, requests, FastAPI, uvicorn, pydantic-settings, sqlalchemy, the
  Sybase driver (Audit §5.1). **openpyxl and pandas are not named** (this guide, §12).
- Deployment: GitLab CI zips `src/cyrus_pmg/**`, `scripts/**`, `resources/**`; distributed by the
  internal `gns` system; no containers; env vars are the only configuration (§6).
- The dashboard package has no tests (§10).

The Proposal Tool was built to this shape and runs in a mirror of it (`service/`), so the
integration point is: **one page folder, one route, one nav link, one router insert, one package,
three auth names, configuration.**

---

## 5. Prerequisites and pre-port checks

Run these on the epsilon-phi side, at the commit you are porting, before copying anything.

```bash
cd proposal-tool

# 1. The page is freshly generated and both copies agree
python3 generator/build_styles.py
diff -rq proposalTool service/cyrus_pmg/dashboard/proposalTool && echo IDENTICAL

# 2. The suite is green (253 passed, 4 skipped; the 4 need a live database)
cd service && PYTHONPATH=. python3 -m pytest tests -q

# 3. The extracts parse and the stores are consistent
PYTHONPATH=. python3 -m cyrus_pmg.pmgService.scenario.bake --census        # 0 unparsed, no unknown tickers
PYTHONPATH=. python3 -m cyrus_pmg.pmgService.scenario.sleeveTools --census # no broken sleeves, no orphans
PYTHONPATH=. python3 -m cyrus_pmg.pmgService.scenario.feeTools --census    # note: 0.0-placeholder

# 4. The bake you will deliver is complete
python3 -c "import json; m=json.load(open('var/baked/manifest.json')); print(m['portfoliosBaked'], m['currencies'], m['currencySubstitutions'])"
# expected at 9f4dc50: 688 ['CHF','EUR','GBP','USD'] {'GBP':'USD','CHF':'USD','EUR':'USD'}
```

Also confirm before starting (Unverifiable here — §18): the host's Python version and whether
`openpyxl` and `pandas` are installed; the exact names in the host's `accessControl.py`; that the
host backend answers `/api/v1/whoami`; where extracts and stores may live on the deployed box.

---

## 6. Inventory — copy, adapt, reimplement, exclude

### COPY (verbatim, no edits)

| What | To |
|---|---|
| `proposalTool/` (html, css, js, fonts) | `isg-cyrus-pmg/src/cyrus_pmg/dashboard/proposalTool/` |
| `service/cyrus_pmg/pmgService/scenario/` (25 modules + `advisors.xlsx`, `fees.json`, `feeRates.csv`, `assetEstimates.json`) | `isg-cyrus-pmg/src/cyrus_pmg/pmgService/scenario/` |
| The bake store: `service/var/baked/` (16 slices, `manifest.json`, `assetEstimates.json`, ~3.0 MB) | a durable directory named by `SCENARIO_BAKED_DIR` |
| The extracts: `saaSource/saaPortfolios.csv`, `productSource/products.csv`, `sleeveSource/sleeves.csv` | durable locations named by `SCENARIO_SAA_SOURCE`, `SCENARIO_PRODUCTS_SOURCE`, `SCENARIO_SLEEVES_SEED` (until the real deliveries replace them) |

### INSERT (a block added to an existing host file, in its final form)

| Block | Into |
|---|---|
| The endpoint block of `service/cyrus_pmg/pmgService/dashboardRouter.py` (§9.1) | the host's `pmgService/dashboardRouter.py` |
| `serve_proposal_tool` route (§10.2) | the host's `dashboard/dashboardFrontend.py` |
| The nav link (§10.3) | the host's `dashboard/index.html` |
| `getKerberosFromFastApiRequest`, `isAdmin`, `requireAdmin` (§9.2) | the host's `pmgService/core/accessControl.py` |
| The twenty settings of §11.4 | the host's `dashboard/dashboard.env.defaults` (or wherever it keeps them) |

### ADAPT (the host's own facility, exercised through an existing seam — no package edit)

| What | How |
|---|---|
| Identity | The host's GSSSO session replaces the mirror's `kerberos` cookie / `X-Kerberos` header, inside the host's own `accessControl` — the package never reads identity. |
| Admin list | `isAdmin` reads whatever source the host's allowlist reads (the mirror reads `PMG_ADMIN_KERBEROS`). |
| The analytics library, on the **bake** machine only | `SAA_ENGINE_PACKAGE` if the library is named differently; the `_SYMBOLS` table in `engine.py` if its layout differs. Irrelevant to the host service under §11.4's recommended configuration. |
| Log destination | The host's launcher redirects each process to its own log (Audit §9); the package logs nothing of its own (§13.4). |

### REIMPLEMENT later, behind an unchanged function (a code change — not part of the port)

| What | Where | Why it is code, not config |
|---|---|---|
| The advisor directory | `advisors.py`: `searchAdvisors(query, limit=20)`, `advisorExists(display)` | Reads the packaged `advisors.xlsx`; there is no environment override (finding F7 in `archive/dataSources.html`). |
| Hedge ratios, the context window, the policy constants | `portfolio_weights.py` (`HEDGE_RATIOS_BY_OPTION`, `CONTEXT_START_DATE`, `CONTEXT_END_DATE`), `rules.py` (`MANDATE_FLOOR`, `TACTICAL_TILT_PCT`, `VOL_PREMIUM_SHARE`, `VARIANT_ALLOCATIONS`, …) | Business decisions expressed as constants; they need sign-off, then a code change and — for the first group — a re-bake. |
| The import-time pandas frames in `portfolio_weights.py` | lines 1257–1258 | Optional: removing the import-time build would drop pandas from the request path (§12). Not required for the port. |

### EXCLUDE

Everything in §3.4 and §3.5; `service/var/log`, `service/var/run`, `service/var/*.bak*`;
`.DS_Store` files; `service/weo_2026_1.csv`; the mirror's `/_dev_login`.

---

## 7. Source → target mapping

| Source (this repo) | Target (host) | Action |
|---|---|---|
| `proposalTool/proposalTool.html` | `src/cyrus_pmg/dashboard/proposalTool/proposalTool.html` | COPY |
| `proposalTool/static/css/proposalTool.css` | `…/proposalTool/static/css/proposalTool.css` | COPY |
| `proposalTool/static/js/proposalTool.js` | `…/proposalTool/static/js/proposalTool.js` | COPY |
| `proposalTool/static/fonts/*.woff2` (5) | `…/proposalTool/static/fonts/` | COPY |
| `service/cyrus_pmg/pmgService/scenario/*.py` (25) | `src/cyrus_pmg/pmgService/scenario/` | COPY |
| `service/cyrus_pmg/pmgService/scenario/{advisors.xlsx,fees.json,feeRates.csv,assetEstimates.json}` | same | COPY |
| `service/cyrus_pmg/pmgService/dashboardRouter.py` lines 20–39 (imports, deduplicated), 43–54, 57–696 | appended to `src/cyrus_pmg/pmgService/dashboardRouter.py` | INSERT |
| `service/cyrus_pmg/pmgService/core/accessControl.py` lines 96–124 (`getAdminAllowlist`, `isAdmin`, `requireAdmin`) and 64–69 (`getKerberosFromFastApiRequest`) | `src/cyrus_pmg/pmgService/core/accessControl.py` | INSERT, adapted to the host's identity source |
| `service/cyrus_pmg/dashboard/dashboardFrontend.py` lines 165–168 | `src/cyrus_pmg/dashboard/dashboardFrontend.py` | INSERT |
| `service/cyrus_pmg/dashboard/index.html` lines 14–19 | `src/cyrus_pmg/dashboard/index.html` header block | INSERT |
| `service/dashboard.env.defaults` (the `SCENARIO_*`, `PMG_ADMIN_KERBEROS` lines) | the host's `dashboard.env.defaults` | CONFIG |
| `service/var/baked/` | `$SCENARIO_BAKED_DIR` | DATA delivery |
| `saaSource/saaPortfolios.csv` | `$SCENARIO_SAA_SOURCE` | DATA delivery (stand-in until the real extract) |
| `productSource/products.csv` | `$SCENARIO_PRODUCTS_SOURCE` | DATA delivery (stand-in) |
| `sleeveSource/sleeves.csv` | `$SCENARIO_SLEEVES_SEED` | DATA delivery (seed, read once) |
| `service/cyrus_pmg/dashboard/{dashboardFrontend,dashboardConfig}.py`, `index.html`, `static/js/*` | — | STAND-IN, not copied |
| `service/cyrus_pmg/pmgService/{isgPMGService,config}.py`, `core/accessControl.py` | — | STAND-IN, not copied |
| `service/tests/`, `generator/`, `backend/`, docs | — | dev side only |

---

## 8. Migration procedure

Do the steps in order; each has a check.

**Step 0 — Freeze the commit.** Note the epsilon-phi commit (`git rev-parse --short HEAD`), run §5,
and take the bake manifest's `updatedAt` and `source.modified` — they are what §15 checks the
running host against.

**Step 1 — Dependencies.** Confirm or add `openpyxl` and `pandas` to the host's requirements
(§12). Confirm the host's Python is ≥ 3.8 (the only version this code is proven on is 3.8.20).

**Step 2 — The auth module (do this before anything else touches the host).** Establish what
`pmgService/core/` actually contains, then add `getKerberosFromFastApiRequest`, `isAdmin` and
`requireAdmin` to it and point the block's import at the real module name (§9.2 — it may be
`pmgEntitlement.py`, not `accessControl.py`). Check, in the host's virtualenv:

```bash
ls src/cyrus_pmg/pmgService/core/
python -c "from cyrus_pmg.pmgService.core.<module> import (
    getKerberosFromFastApiRequest, isAdmin, requireAdmin, requireAuth, requireEditor)"
```

**Step 3 — Copy the package.**

```bash
cp -r proposal-tool/service/cyrus_pmg/pmgService/scenario  isg-cyrus-pmg/src/cyrus_pmg/pmgService/
```

Check: `python -c "import cyrus_pmg.pmgService.scenario.registry"` succeeds with `PYTHONPATH`
including `isg-cyrus-pmg/src`. (Nothing is read yet: the extracts are read on first use.)

**Step 4 — Insert the endpoint block** (§9.1). Check: `python -c "from cyrus_pmg.pmgService.dashboardRouter
import router; print(len([r for r in router.routes if r.path.startswith('/scenario')]))"` prints
`25`.

**Step 5 — Deliver the data.** Place the three extracts and the bake store on durable storage
outside `src/`; create the directories for the sleeve database, the register and the scenario
store. Set the §11.4 variables. Check (with the variables exported):

```bash
python -m cyrus_pmg.pmgService.scenario.bake --census            # 172 portfolios, 0 unparsed
python -m cyrus_pmg.pmgService.scenario.sleeveTools --census     # seeds the sleeve db on first run
python -m cyrus_pmg.pmgService.scenario.feeTools --census
```

**Step 6 — Copy the page folder and register it** (§10). Check: the four `curl`s in §10.6.

**Step 7 — Start, then walk the flow** (§15). Run the acceptance list as an allowlisted user and
as an admin.

**Step 8 — Operations.** Schedule the register backup (§16.3), the weekly sleeve export and
census, and decide who holds `PMG_ADMIN_KERBEROS` (§11.4).

---

## 9. Backend integration

### 9.1 The endpoint block

Append to the host's `pmgService/dashboardRouter.py`:

1. The import lines of the mirror file (lines 20–39), **merging** with the host's existing
   `fastapi` imports and **omitting** any name the host already imports from `accessControl`
   (`requireAuth`, `requireEditor`). The new imports are:

   ```python
   from cyrus_pmg.pmgService.core.accessControl import (
       getKerberosFromFastApiRequest, isAdmin, requireAdmin, requireAuth, requireEditor)
   from cyrus_pmg.pmgService.scenario import (fees, products, proposalRegister, scenarioStore,
                                              sleeveRepo)
   from cyrus_pmg.pmgService.scenario.registry import getScenarioPort
   from cyrus_pmg.pmgService.scenario.rules import (
       exportFilename, validateBasis, validateFeeLevel, validateFeeSchedule,
       validateKey, validateVariant)
   from cyrus_pmg.pmgService.scenario.sleeves import sleeveExists
   from cyrus_pmg.pmgService.scenario.types import (
       AnalyticsError, BasisInput, MandateInput, PortfolioKey, ScenarioNotFound, ValidationError)
   ```

2. **Not** line 41 (`router = APIRouter(...)`) — the host's router object already exists and is
   already `include_router`-ed under `/api/v1` (Audit §4, spec §3.4). The block's decorators bind
   to the host's `router` by name (Unverifiable: that the host's variable is called `router`; Audit
   §12 step 7 shows `@router.get`).

3. Lines 43–54 (`_XLSX`, the warm-up `try: getScenarioPort() except Exception: pass`).

4. Lines 57 to the end: the helpers and the 25 handlers, unchanged.

Route-level gates are already correct: reads inherit the router's `requireAuth`, writes carry
`Depends(requireEditor)`, the fifteen `/scenario/repository…` routes carry `Depends(requireAdmin)`
(§11.1; `tests/test_sleeve_repository.py::test_every_repository_route_requires_the_admin_role`
asserts the admin set).

`getScenarioSchema` takes `request: Request` and stamps `capabilities.canAdmin =
isAdmin(getKerberosFromFastApiRequest(request))` onto the schema; that is the only place the
router reads identity directly.

### 9.2 The auth module — two imports, and one function to write

Settled by `../cyrus-files/pmgEntitlement.py` and the host's own router. The block's single import
line becomes two, because the names live in two modules:

```python
from cyrus_pmg.pmgService.core.accessControl import getKerberosFromFastApiRequest
from cyrus_pmg.pmgService.core.pmgEntitlement import requireAuth, requireEditor
```

`requireAuth` (= `requirePmgApiView`) and `requireEditor` (= `requirePmgApiModify`) already exist
as back-compat shims, so nothing needs aliasing. **Do not insert the mirror's
`accessControl.py`** — the host's is richer and authoritative.

**`requireAdmin` and `isAdmin` do not exist and must be written.** Because `post` is a
PMGEditor-only privilege in the policy (ISGAdmin deliberately lacks it), no `resource:action`
isolates an administrator; the test has to be role membership. Add to the host's
`pmgEntitlement.py`, beside the other shims:

```python
def isAdmin(userData) -> bool:
    """Whether this caller may maintain the sleeve library."""
    return hasRole(userData, ROLE_ADMIN)


def requireAdmin(userData: UserData = Depends(requireAuth)) -> UserData:
    """Repository console gate. Mirrors requireCan's refusal shape."""
    if not isAdmin(userData):
        kerberos = getattr(userData, 'kerberos', 'unknown')
        raise PmgAppException(
            statusCode=HTTPStatus.FORBIDDEN.value,
            reason="Forbidden: user '{}' is not a sleeve repository admin.".format(kerberos))
    return userData
```

Then `from cyrus_pmg.pmgService.core.pmgEntitlement import isAdmin, requireAdmin` joins the second
import line. **Which role maintains the library is a PMG decision** — `ISGAdmin` is the natural
reading, but the policy gives `PMGEditor` strictly more, so confirm it (§17).

The schema route also calls `isAdmin(getKerberosFromFastApiRequest(request))` with a *string*. The
host's `isAdmin` above takes a `UserData`. Either overload it, or change that one call site to
`isAdmin(PmgEntitlement.isValidUser(kerberos)[1])`.

#### The `UserData`-versus-string boundary — fixed on this side

The host's dependencies return a `UserData`; this block was written against a mirror whose
dependencies return the kerberos itself, and it *binds* the value rather than only gating on it —
which the host never does, so nothing there had noticed.

Measured before the fix: a `UserData` reaching `scenarioStore` raised
`TypeError: Object of type UserData is not JSON serializable` on **every scenario created**, and
reaching either SQLite store raised `InterfaceError: Error binding parameter` on **every export and
every sleeve save**. Three write paths, all hard failures.

`_callerId()` now normalises at the eight points the value is consumed, so the same block runs
against either host unchanged:

```python
def _callerId(user) -> str:
    return (getattr(user, 'kerberos', None)
            or (user if isinstance(user, str) else '')
            or 'unknown')
```

`tests/test_sleeve_repository.py::test_the_caller_id_survives_either_shape_of_auth_dependency`
asserts both shapes and that what lands in the stores is a string. The twenty `user: str =
Depends(...)` annotations were dropped, since `str` is not what arrives on the host.

### 9.3 Nothing else changes on the backend

`isgPMGService.py` is untouched (the router is already mounted). `config.py` is untouched:
`SCENARIO_ADAPTER` and the other settings are read with `os.getenv` inside the package, not through
the host's settings object. The mirror's `config.py` docstring says otherwise; it is wrong.

---

## 10. Frontend integration

### 10.1 The folder

```bash
cp -r proposal-tool/proposalTool  isg-cyrus-pmg/src/cyrus_pmg/dashboard/
```

Folder, HTML, CSS and JS all share the name `proposalTool`, the host's page-folder convention
(Audit §11). No edits: the stylesheet and script `href`/`src` are relative; the two shared
includes are absolute at the host's own paths.

### 10.2 The Flask route

In the host's `dashboardFrontend.py`, beside the other per-page routes (Audit §7.1):

```python
@app.route('/proposalTool/<path:filename>')
def serve_proposal_tool(filename):
    """Serve Proposal Tool static files."""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'proposalTool'), filename)
```

Restart the Flask process — no hot reload for Python (Audit §13).

### 10.3 The nav link

In `index.html`, inside the header flex block, copying the surrounding inline styles verbatim
(Audit §7.2, §8.2):

```html
<a href="/proposalTool/proposalTool.html"
   style="color:white; text-decoration:none; font-size:14px; padding:8px 16px;
          border:1px solid rgba(255,255,255,0.4); border-radius:8px;
          transition:background 0.2s;"
   onmouseover="this.style.background='rgba(255, 255, 255, 0.15)'"
   onmouseout="this.style.background='transparent'">📐 Proposal Tool</a>
```

### 10.4 Conventions followed, and deliberate deviations

Followed (Verified in the built page): `lowerCamelCase` naming throughout; `accessGate.js` first,
then `globals.js`, page script at the end of `<body>`; `'use strict'`; the `API_BASE` guard;
bootstrap on `DOMContentLoaded` with a `readyState` guard; `#alertArea` + `showAlert(kind,
message)`; `body.loginUrl` followed on 401; `credentials: 'same-origin'`; no module system; direct
DOM writes.

Deliberate deviations (agreed in the build; recorded in `service/DEVIATIONS.md`):

| Deviation | Why |
|---|---|
| No `← Back to Dashboard`, no host header block; a fixed left rail | The page is independent of the others. |
| No `onegsTheme.css` / `dashboard.css`; one self-contained stylesheet | Retheming is a separate change (§10.5). |
| No `isgCodeSelector.js` | The page is not schema-scoped. |
| Inline SVG charts, no Chart.js CDN | Self-contained; nothing breaks on a network that blocks `cdn.jsdelivr.net`. |
| Local woff2 fonts via `../fonts/*` | The house faces; resolves identically under the Flask route. |
| Relative `href`/`src` for its own assets | Same URL under the route; the page also opens from disk for review. |

### 10.5 Theme — what a OneGS retheme actually costs

The previous guide said retheming is "a token change, not a hunt through component CSS". Measured
on the built stylesheet: **83 tokens on `:root` and 755 `var(--…)` references, but also 304 literal
hex colours (77 distinct) and 26 `rgb()/rgba()` values outside `:root`** (Verified). A retheme is
therefore a token swap in `generator/themes.py` (`_TOKENS`) **plus** a sweep of the literals in
`generator/{themes,implementation,repository,pickers,build_styles}.py`, then `python3
generator/build_styles.py`, then a contrast re-audit (spec §6.2, §13). Budget it as a change, not a
substitution. It is not part of this port.

### 10.6 Verify

```bash
curl -s  http://localhost:8001/proposalTool/proposalTool.html | head -3
curl -s  http://localhost:8001/proposalTool/static/js/proposalTool.js | head -1      # 'use strict';
curl -sI http://localhost:8001/proposalTool/static/fonts/gs-sans-variable.woff2 | head -1
curl -s  http://localhost:8002/health        # the host's PMG_SVC_PORT
```

Unauthenticated, the first two return the host's 302 to login (the mirror: `302 /_dev_login?next=…`).

---

## 11. API, models, persistence and configuration

### 11.1 The HTTP surface (25 routes under `/api/v1`; the page calls `/api/…`)

| Method | Path | Handler | Gate |
|---|---|---|---|
| GET | `/scenario/schema?currency&hedging&mandateSize&variant&topAccountSize` | `getScenarioSchema` | requireAuth |
| GET | `/scenario/fees?tier&topAccountSize&whole` | `getFeeCard` | requireAuth |
| GET | `/scenario/advisors?q&limit` | `searchAdvisors` | requireAuth |
| GET | `/scenario/sleeves?category&variant&currency&hedging` | `listSleeves` | requireAuth |
| GET | `/scenario/{scenarioId}` | `getScenario` | requireAuth |
| POST | `/scenario` | `createScenario` | requireEditor |
| PUT | `/scenario/{scenarioId}` | `updateScenario` | requireEditor |
| POST | `/scenario/{scenarioId}/portfolio` | `resolvePortfolio` | requireEditor |
| DELETE | `/scenario/{scenarioId}/portfolio/{portfolioKey:path}` | `removePortfolio` | requireEditor |
| POST | `/scenario/{scenarioId}/export` | `exportScenario` | requireEditor |
| GET | `/scenario/repository` | `getRepository` | requireAdmin |
| POST | `/scenario/repository/sleeves` | `createRepositorySleeve` | requireAdmin |
| PUT | `/scenario/repository/sleeves/{sleeveId}` | `updateRepositorySleeve` | requireAdmin |
| DELETE | `/scenario/repository/sleeves/{sleeveId}` | `deleteRepositorySleeve` | requireAdmin |
| GET | `/scenario/repository/sleeves/{sleeveId}/history` | `getRepositorySleeveHistory` | requireAdmin |
| POST | `/scenario/repository/sleeves/{sleeveId}/restore` | `restoreRepositorySleeve` | requireAdmin |
| POST | `/scenario/repository/sleeves/{sleeveId}/revert` | `revertRepositorySleeve` | requireAdmin |
| POST | `/scenario/repository/sleeves/restore` | `restoreRepositorySleeves` | requireAdmin |
| GET | `/scenario/repository/activity` | `getRepositoryActivity` | requireAdmin |
| GET | `/scenario/repository/archive.csv` | `exportRepositoryArchive` | requireAdmin |
| GET | `/scenario/repository/activity.csv` | `exportRepositoryActivity` | requireAdmin |
| GET | `/scenario/repository/proposals` | `listRegisterProposals` | requireAdmin |
| GET | `/scenario/repository/proposals.csv` | `exportRegisterProposals` | requireAdmin |
| GET | `/scenario/repository/proposals/{proposalId}` | `getRegisterProposal` | requireAdmin |
| GET | `/scenario/repository/proposals/{proposalId}/workbook` | `downloadRegisterWorkbook` | requireAdmin |

Error contract (Verified over HTTP): 422 `{error, field}`; 404 `{error}` ("Scenario … is no
longer available - scenarios are kept for 24 hours"); 502 `{error}` on analytics failure; 401
`{loginUrl}`; 403 `{error}`; a malformed body is 422 `{error: 'Malformed request.'}`. The export
refuses with 422 `field: sleeves` while a category lacks a sleeve, `field: feeSchedule` while fees
are included with no schedule, and `field: minimumInvestment` while any position is below its
product's minimum (D70).

### 11.2 Contracts the page depends on

- **Portfolio key**: `currency|riskLevel|allocationType|excludeRealAssets` (`USD|Moderate|Full|0`;
  an all-equity book `USD|All Equity|NA|NA`); hedging is not in the key. URL-encoded in the DELETE
  path. `types.PortfolioKey`.
- **Mandate validation** (`rules.validateMandate`, `rules.py:519–535`, applied by every adapter's
  `validate_mandate`): top account size > 0; mandate size ≥ `MANDATE_FLOOR` ($5m) and ≤ the top
  account size; **`primaryPwa` must be a display string the advisor directory returns**
  (`advisors.advisorExists`) — a `POST /scenario` with any other name is 422 `field: primaryPwa`.
- **Rehydrate** (`GET /scenario/{id}`): `id, mandate, basis, base, comparisons, variant,
  tacticalTilt, volPremium, sleeves, includeFees, feeSchedule, feeLevel` (12 keys, Verified).
- **`PortfolioResult`**: `key, keyStr, name, header, categories[{name, weightPct, assets[{reportingName,
  weightPct}]}], metrics{estimatedReturnPct, volatilityPct, sharpe}, stress[{period, nominalPct,
  realPct}], premia[{group, horizon, label, nominalPct, realPct, kind}]`; non-USD payloads also
  carry `analyticsCurrency`.
- **A served sleeve product**: `productId, name, ticker, assetClass, style, vehicle, source,
  liquidity, exposureCurrency, productCost, feeGroup, distributionYield, minimumInvestment, weight`.
- **Schema**: `options, availability, categories, rules, fees, capabilities{canExport, canEdit,
  canAdmin}, dataInfo{adapter, source, dataversion, asOf}`.
- **Export**: `Content-Disposition: attachment; filename="PMG_Scenario_<CCY>_<Hedging>_<date>.xlsx"`;
  sheets `portfolios, risk_dashboard, assumptions, Implementation` plus a hidden `chartData` sheet
  that feeds five native doughnut charts (D73).

### 11.3 Persistence — the four stores the tool owns

| Store | Module | Format | Location | Concurrency | Backup / rollback |
|---|---|---|---|---:|---|
| Scenario state | `scenarioStore.py` | one JSON file per scenario, atomic rename | `SCENARIO_STORE_DIR` | worker-safe (files) | none needed — expires after `SCENARIO_RETENTION_HOURS` (24) |
| Sleeve library | `sleeveRepo.py` | SQLite, schema v2: `sleeves`, `sleeveProducts`, `sleeveHistory`, `meta`; partial unique index on live names; `_migrate` rebuilds v1→v2 and back-fills a `baseline` revision | `SCENARIO_SLEEVES_DB` | SQLite, `PRAGMA foreign_keys=ON` | dated `sleeveTools --export`; every revision kept; soft delete + restore (D65/D66) |
| Proposal register | `proposalRegister.py` | SQLite, schema v1: `proposals` (with the workbook blob and SHA-256), `proposalSleeves`, `meta`; seven indexes; **append-only by construction** | `SCENARIO_REGISTER_DB` | SQLite | **daily file backup off the host — the only store whose loss is unrecoverable** |
| Baked analytics | `bake.py` / `bakedAdapter.py` | `manifest.json` + one `<CCY>_<Hedging>.json` per slice + `assetEstimates.json` | `SCENARIO_BAKED_DIR` (read-only at runtime) | read-only | keep the previous store directory; swap by rename |

Seeding: the sleeve database is created and seeded from `SCENARIO_SLEEVES_SEED` **once**, the first
time it opens; after that the seed is never read (Verified: `sleeveRepo._seed`, `meta.seededAt`).
Both SQLite stores create their schema on first open; no migration tooling is needed for a fresh
host.

### 11.3.1 Four workers on one set of files

The host launches `isgPMGService` with **four** uvicorn workers (§4.0). All four stores are file-
or SQLite-backed precisely so that is safe; the adapters' in-memory caches are per worker and
recomputable (the baked adapter holds at most the 16 slices it has been asked for, ~3 MB).

Measured on this code with four processes released together (see
`tests/test_sleeve_repository.py::test_four_workers_cold_starting_together_seed_the_library_once`):

| | Result |
|---|---|
| 100 concurrent register writes across 4 processes | 100/100, no `database is locked` |
| 4 processes against an existing sleeve database | all fine |
| 4 processes cold-starting the register | all fine |
| 4 processes cold-starting an **empty** sleeve database | **was broken; fixed at `7fe6463`+** |

That last case was two check-then-act races in `sleeveRepo`, both closed: the `sleeves` table was
created without `IF NOT EXISTS` behind an existence check (three workers in four failed with
`table sleeves already exists`), and the seed itself re-asked "is the library empty?" outside a
write transaction, so two workers could both fill it — the live-name index refused the duplicate
sleeves, but `sleeveHistory` has no such index and ended up with one `seeded` revision per worker.
`_seedOnce` now re-reads under `BEGIN IMMEDIATE`, taking the write lock only when the library looks
empty, which is once in the life of a store.

**Still do this on the host**: create and seed the sleeve database as a deploy step, *before* the
service starts —

```bash
python -m cyrus_pmg.pmgService.scenario.sleeveTools --census
```

It is in §8 Step 5 already; treat it as load-bearing rather than a check. The fix makes a
concurrent cold start correct, but seeding first means the race is never run at all.

### 11.3.2 The filesystem under the SQLite files

Nothing in either store is platform-specific — `sqlite3` is stdlib, paths go through
`os.path.join`, and the scenario store's atomic write is `os.replace`, which is POSIX-native. Two
properties of the *storage* matter more than the operating system:

- **Network filesystems.** SQLite's locking is only as reliable as the filesystem's, and on NFS
  POSIX advisory locking is not reliable — spurious `database is locked`, and corruption in the bad
  case. Four workers sharing one file makes this live. If the durable path is an NFS mount, put the
  proposal register on local disk and ship backups off it, rather than running it from the share.
  If workers could ever be spread across hosts sharing that path, the same applies with more force.
- **The directory must be writable, not just the file** — SQLite creates `-journal` files beside
  the database. Journal mode is the default `delete`, not WAL; at this write volume (one insert per
  export, occasional admin saves) that is comfortable, and WAL would not help on NFS anyway.

**SQLite version floor: 3.8.0**, for the partial unique index (`… WHERE deletedAt = ''`). The
v1→v2 migration also uses `PRAGMA legacy_alter_table`, which needs 3.25.0 — but a fresh host never
runs that path. RHEL 7's system SQLite is 3.7.17 and would fail outright, so check it:

```bash
python -c "import sqlite3; print(sqlite3.sqlite_version)"   # >= 3.8.0
```

### 11.4 Configuration — every variable the package reads

Twenty variables (Verified by grep of `os.getenv` across `service/` and `generator/`). "Required"
means the host must set it; "own" means the host already has its own value.

| Variable | Read in | Default | Host |
|---|---|---|---|
| `SCENARIO_ADAPTER` | `registry.py` | `fixtures` when unset (`dashboard.env.defaults` sets `baked`) | **Required: `baked`** |
| `SCENARIO_BAKED_FALLBACK` | `registry.py` | `1` | **Required: `0`** — no database, no library on the request path |
| `SCENARIO_BAKED_DIR` | `bake.py` (`storeDir`, used by the adapter and `assetEstimates`) | `<pkg>/../../../var/baked` | **Required** — the delivered store |
| `SCENARIO_SAA_SOURCE` | `universe.py` | `<pkg>/../../../../saaSource/saaPortfolios.csv` | **Required** — read at request time |
| `SCENARIO_PRODUCTS_SOURCE` | `products.py` | `<pkg>/../../../../productSource/products.csv` | **Required** |
| `SCENARIO_SLEEVES_SEED` | `sleeveRepo.py` | `<pkg>/../../../../sleeveSource/sleeves.csv` | **Required** for the first start (read once) |
| `SCENARIO_SLEEVES_DB` | `sleeveRepo.py` | `<pkg>/../../../var/sleeves.db` | **Required** — durable, writable |
| `SCENARIO_REGISTER_DB` | `proposalRegister.py` | `<pkg>/../../../var/proposals.db` | **Required** — durable, writable, backed up |
| `SCENARIO_STORE_DIR` | `scenarioStore.py` | `$TMPDIR/pmg_proposal_scenarios` | **Required** — durable, shared by both workers |
| `SCENARIO_RETENTION_HOURS` | `scenarioStore.py` | `24` | optional (spec open item 8) |
| `SCENARIO_FEES_SOURCE` | `fees.py` | `<pkg>/feeRates.csv` (placeholder) | set when the real card is delivered; change it through `feeTools --accept` |
| `SCENARIO_ASSET_ESTIMATES` | `assetEstimates.py` | the bake store's copy, else `<pkg>/assetEstimates.json` | optional |
| `PMG_ALLOWED_KERBEROS` | `accessControl.py` | `fbarker` in the mirror | own (Audit §5.5) |
| `PMG_ADMIN_KERBEROS` | `accessControl.py` (mirror) | `fbarker` | **Required** — or the host's equivalent source in `isAdmin` |
| `SAA_ENGINE_PACKAGE` | `engine.py` | `epsilonPhi` | bake machine only |
| `PMG_SVC_PORT`, `PMG_SVC_HOST` | `config.py` (mirror) | 8002 / 127.0.0.1 | own — the host's are **8002 / 0.0.0.0**, under its `PMG_SVC_` pydantic-settings prefix (Verified: `cyrus-files/config.py`, `dashboard.env.defaults`) |
| `FRONTEND_PORT`, `DASHBOARD_HOST` | `dashboardConfig.py` (mirror) | 8001 / 127.0.0.1 | own |
| `PMG_SVC_WORKERS` | `start_dashboard.sh` (mirror) | 1 | own (the host runs 2) |
| `SCENARIO_FIXTURES_LATENCY_MS`, `SCENARIO_FIXTURES_FAIL`, `SCENARIO_FIXTURES_FAIL_SCHEMA`, `SCENARIO_FIXTURES_FAIL_SLEEVES`, `SCENARIO_FIXTURES_FAIL_EXPORT` | `fixturesAdapter.py` | unset | never in production |
| `SAA_ENGINE_LIVE` | tests only | unset | — |

Reference block for the host's `dashboard.env.defaults` (adjust the paths):

```bash
export SCENARIO_ADAPTER=baked
export SCENARIO_BAKED_FALLBACK=0
export SCENARIO_BAKED_DIR=/data/pmg/proposalTool/baked
export SCENARIO_SAA_SOURCE=/data/pmg/proposalTool/extracts/saaPortfolios.csv
export SCENARIO_PRODUCTS_SOURCE=/data/pmg/proposalTool/extracts/products.csv
export SCENARIO_SLEEVES_SEED=/data/pmg/proposalTool/extracts/sleeves.csv
export SCENARIO_SLEEVES_DB=/data/pmg/proposalTool/sleeves.db
export SCENARIO_REGISTER_DB=/data/pmg/proposalTool/proposals.db
export SCENARIO_STORE_DIR=/data/pmg/proposalTool/scenarios
export SCENARIO_RETENTION_HOURS=24
export PMG_ADMIN_KERBEROS=<comma-separated admins, a subset of PMG_ALLOWED_KERBEROS>
```

(`/data/pmg/proposalTool` is illustrative — where writable, durable storage lives on the deployed
host is Unverifiable, §18.)

### 11.5 The bake is a delivery, not a cache

The store is built where the analytics library and its database exist — today the epsilon-phi
side — and shipped to the host as data. At `9f4dc50`: 16 slices × 43 portfolios = **688
payloads**, four currencies, ~3.0 MB including `assetEstimates.json` and `manifest.json`. GBP, CHF
and EUR were analysed in a USD context (`--analytics-currency USD`) because the development
database carries currency configs for USD and GBP only; the manifest records
`currencySubstitutions` and every non-USD payload is stamped `analyticsCurrency: "USD"` (Verified).

```bash
# on a machine with the analytics library and its database, PYTHONPATH including the library
cd proposal-tool/service
PYTHONPATH=".:../../src/python" python3 -m cyrus_pmg.pmgService.scenario.bake --census
PYTHONPATH=".:../../src/python" python3 -m cyrus_pmg.pmgService.scenario.bake \
    --all --workers 4 --out var/baked.new --analytics-currency USD
# verify manifest.json: portfoliosBaked, currencies, source.path/modified, unparsedNames == []
# then deliver var/baked.new to the host as the new SCENARIO_BAKED_DIR
```

Re-bake when: the SAA extract changes (`bake --census` first), the library's `dataversion` moves,
the hedge ratios or the context window change, or the payload shape changes. The host never bakes.
`bake.py`, `liveAdapter.py`, `engine.py` and the engine half of `portfolio_weights.py` still travel
in the package (verbatim copy) and stay inert under `SCENARIO_BAKED_FALLBACK=0` (Verified by the
§14.3 probe).

Coverage is exact: an unbaked key with the fallback off is a 422 from the availability check when
the universe does not offer it, and an `AnalyticsError` → 502 with Retry if it is offered but
missing from the store (`test_miss_without_delegate_raises_analytics_error`). Because availability
is derived from the extract at request time (§3.6), **ship the extract and the bake that was built
from it together**; the manifest's `source.modified` is the tie.

---

## 12. Dependencies

Measured on the epsilon-phi side (Python 3.8.20). The host's versions are Unverifiable.

| Package | Here | Needed by | Host status (Audit §5.1) |
|---|---|---|---|
| Python | 3.8.20 | everything (`from __future__ import annotations` throughout) | not stated |
| `fastapi` / `starlette` / `pydantic` | 0.122.0 / 0.44.0 / 2.5.3 | the router: `APIRouter, Body, Depends, Request, Response, JSONResponse` only | present, versions not stated |
| `openpyxl` | 3.0.10 | `workbook.py` — `Workbook`, styles, `get_column_letter`, `chart.DoughnutChart`, `chart.Reference`, `chart.series.DataPoint`, `formatting.rule.CellIsRule`; `advisors.py`; the tests | **not named — verify/install** |
| `pandas` | 2.0.3 | `portfolio_weights.py` imports it at module level and builds two frames at import (lines 1257–1258); `universe.py`, `rules.py` and `workbook.py` import that module for `ASSET_METADATA`. Measured: pandas import 0.73 s, then the package 0.37 s | **not named — verify/install** |
| `requests`, `flask` | 2.32.3 / 2.2.5 | the host's own proxy and gate, not the package | present |
| `sqlite3` | stdlib, **library ≥ 3.8.0** | the sleeve repository and the proposal register; the partial unique index needs 3.8.0 (§11.3.2) | verify — a stripped Python build without `_sqlite3`, or RHEL 7's 3.7.17, would fail |
| `csv`, `json`, `secrets`, `hashlib`, `tempfile`, `threading` | stdlib | the stores and readers | — |
| `epsilonPhi` (the analytics library), `numpy`, `sklearn`, the database driver | — | **only the bake** and `SCENARIO_BAKED_FALLBACK=1` | not needed on the host service |

What a fully baked, no-fallback service actually imports across a complete cycle (schema, create,
resolve, sleeves, export, register, console): `fastapi, starlette, pydantic, openpyxl, anyio, httpx`
plus `pandas, numpy, pyarrow` via `portfolio_weights.py` — and **never `epsilonPhi`** (Verified,
§14.3). There is no requirements file in `proposal-tool/`; the two names above are what to add to
the host's `requirements.in`.

`node` is needed only to run the JavaScript-mirror tests on the epsilon-phi side (§14).

---

## 13. Authentication, authorisation, error handling, logging

### 13.1 Three roles, two lists

| Role | Gate | Source | What it unlocks |
|---|---|---|---|
| Reader | router-level `requireAuth` | the access list (`PMG_ALLOWED_KERBEROS` or the host's) | schema, fees, advisors, sleeves, rehydrate |
| Editor | `Depends(requireEditor)` — today identical to `requireAuth` | same | create/update scenarios, resolve, remove columns, export |
| Admin | `Depends(requireAdmin)` | the admin list, a subset of the access list | the sleeve repository console, archive, activity, the proposal register |

`capabilities.canAdmin` on the schema tells the page whether to draw the admin entry points; the
server re-enforces on every admin route (Verified: a non-admin gets 403 and `canAdmin: false`).
The proxy forwards the browser's cookies to FastAPI in the mirror (`cookies=request.cookies`);
the host's proxy must carry whatever its `requireAuth` reads (Unverifiable — Audit's snippet
elides the arguments).

### 13.2 Identity

The package never reads identity. The router gets it from `accessControl` (`Depends`, and
`getKerberosFromFastApiRequest(request)` in `getScenarioSchema`) and passes the user's id to
`scenarioStore.createScenario(createdBy=user)` and `proposalRegister.record(scenarioId, user, …)`.
Whatever string the host's `accessControl` returns is what the register stores.

### 13.3 The error contract — two shapes, both handled

Settled by `../cyrus-files/exceptionHandlers.py`. The host wraps its own failures as
`PmgAppException`, whose handler emits:

```python
{"error": HTTPStatus(exc.statusCode).phrase,   # "Forbidden" - the STATUS PHRASE
 "detail": exc.reason,                          # the actual message
 "path":   str(request.url)}                    # plus anything in exc.extra
```

That is the opposite way round from this block's own errors, which put the message in `error` and
add `field` (spec 3.5). Reading `error` alone would have shown a caller **"Forbidden"** instead of
the reason. `apiFetch` now prefers a string `detail` and falls back to `error`, which resolves
every shape it can meet — this block's 422s and 404s, `PmgAppException`, the Flask gate's 401/403,
the host's `_errorResponse`, and FastAPI's list-shaped `detail`, which correctly falls through
rather than rendering as `[object Object]`.

Two things that already work and need no change: `PmgAppException.extra` can carry `loginUrl`, and
the Flask gate's 401 body carries it directly — which is what drives the page's GSSSO redirect. And
`redirectUrl` on the exception produces a 302 that the proxy passes through untouched, because
`allow_redirects=False`.

One difference left deliberately alone: the host's `_errorResponse` returns **HTTP 200** with
`{success: false, error}`. This block returns real status codes with its own `JSONResponse`, per
spec 3.5, and does not route through that helper. The page needs the status codes, so this stays as
it is.

### 13.4 Logging and observability

The package writes no log lines and has no metrics hooks (Verified — no `logging` import anywhere
in `scenario/`). Provenance is surfaced instead: `dataInfo` on the schema (adapter, source,
dataversion, bake date), `sleeveRepo.describe()`, `proposalRegister.describe()`,
`products.describeSource()`, `universe.describeSource()`, `fees.deliveryInfo()` on the console
payload, and the CLIs' `--census`. Stdout/stderr go wherever the host's launcher sends each
process (Audit §9). If the host wants request logging for these routes, add it at the app or
router level — not in the package.

### 13.5 Security notes

- `send_from_directory` blocks traversal for the page folder (Audit §13).
- Every write re-validates server-side; the export re-resolves every column and rebuilds the
  implemented model rather than trusting the page (Verified in `exportScenario`).
- The register stores the delivered workbook bytes and their SHA-256; it has no update or delete
  path (`test_the_register_is_append_only_by_construction`).
- The fee card and the product catalogue are read-only in the service; they change only through
  `feeTools --accept` / a file delivery.
- The `/_dev_login` route, the `kerberos` cookie and the `X-Kerberos` header exist only in the
  mirror's stand-ins and must not reach the host.

---

## 14. Tests and validation

### 14.1 The suite (epsilon-phi side)

```bash
cd proposal-tool/service && PYTHONPATH=. python3 -m pytest tests -q
# 253 passed, 4 skipped in ~43s
```

| File | Tests | Covers |
|---|---:|---|
| `tests/test_scenario_backend.py` | 97 defs (parametrised) | rules, availability, rounding, the workbook against the golden files, the JS mirrors (needs `node`), the implementation model, fees, the export path, the library-leak guard |
| `tests/test_sleeve_repository.py` | 50 | the sleeve store, migration, history, archive/activity, the console routes, the admin gate on every `/scenario/repository…` route, `accessControl` semantics, and the four-worker cold start (§11.3.1) |
| `tests/test_proposal_register.py` | 18 | the register end to end, byte-identity of the stored workbook, append-only, the panel's endpoints |
| `tests/test_baked_adapter.py` | 10 | slice coverage, manifest provenance, no analytics on the read path, export without a delegate, resumable bake |
| `tests/test_tier0_beta_equivalence.py` | 4 | the library-side Tier 0 optimisation; **skipped** unless `SAA_ENGINE_LIVE=1` and a database |

`tests/conftest.py` gives every session a throwaway sleeve database and register. The suite is
written against the mirror's stand-ins (the `X-Kerberos` header, `_dev_login`, the packaged
extracts at their default paths), so it is the **pre-port gate**, run at the commit being copied.
It is not shipped to the host and is not expected to run there unmodified.

### 14.2 The golden workbook

`tests/golden/engine_USD_Hedged_2col.{xlsx,results.json,impl.json}` is the reference captured from
the analytics library's own reporting object on 3 September 2026. `test_scenario_backend.py`
rebuilds the three engine-laid sheets from the payloads and compares every cell's value, number
format, font, fill, border, alignment, merge, row height and column width, plus the conditional
rule. **Do not regenerate these files**; they cannot be reproduced once the library is gone.

### 14.3 The portability probe (repeat on the host after Step 5)

Run in a fresh process with the production configuration and throwaway stores. At `9f4dc50` this
completed a full cycle in 2.8 s — schema, create, resolve (6 ms), a cross-currency refusal (422), an
unavailable key (422), rehydrate, sleeves, export at a $5bn mandate (194 ms, 21,624 bytes, five
sheets), a register row whose stored workbook is byte-identical, the $50m export refused on
`minimumInvestment`, the console payload, a non-admin refused — and `epsilonPhi` never entered
`sys.modules`.

```bash
TMP=$(mktemp -d)
SCENARIO_ADAPTER=baked SCENARIO_BAKED_FALLBACK=0 \
SCENARIO_STORE_DIR=$TMP/store SCENARIO_SLEEVES_DB=$TMP/sleeves.db SCENARIO_REGISTER_DB=$TMP/proposals.db \
python3 - <<'EOF'
import sys
from fastapi.testclient import TestClient
from cyrus_pmg.pmgService.isgPMGService import app     # the host's app once ported
c = TestClient(app)
# ... authenticate the way the host's accessControl expects, then:
#   GET /api/v1/scenario/schema, POST /api/v1/scenario, PUT variant, POST portfolio,
#   GET sleeves, PUT sleeves, POST export, GET repository/proposals
assert not any(m.split('.')[0] == 'epsilonPhi' for m in sys.modules)
EOF
```

The two tests that lock this property on the epsilon-phi side:
`test_an_export_imports_no_part_of_the_analytics_library` (a subprocess, asserts `LEAKED []`) and
`test_schema_and_library_need_no_analytics`.

### 14.4 Validating the page

There is no JavaScript test harness. The JS mirrors (`rows()`, `volPremiumCategories`,
`roundWeights`, `roundSharesOneDp`, `resolveFee`, `sleeveCategory`) are proved against their Python
originals by extracting them from `generator/js/*.js` and running them through `node` inside
pytest. Behavioural checks are manual — §15.

---

## 15. Acceptance criteria

All must hold on the host, with the production configuration, before the page is linked from the
nav for users.

**Serving and gate**

1. `GET /proposalTool/proposalTool.html`, `…/static/js/proposalTool.js`, `…/static/css/proposalTool.css`
   and one font return 200 to an allowlisted user; unauthenticated returns the host's login redirect.
2. `GET /api/whoami` returns the identity; the page shows no sign-in overlay for an allowlisted user.
3. A user not on the access list gets the host's access-denied card; `/api/…` gets 403 `{error}`.

**Data and provenance**

4. The schema's `dataInfo` reads `adapter: "baked"` (not "baked (live fallback)"), `688 portfolios
   baked` (or the delivered count), the four currencies and the bake date you delivered.
5. `availability` is 43 per currency at the current extract; `bake --census` on the host shows the
   delivered extract with 0 unparsed names.
6. `sleeveTools --census` shows the seeded library (102 sleeves at the packaged seed), no broken
   sleeves, no orphans, each fixed category holding exactly one sleeve.
7. The fee card's `delivery.version` is what you accepted (the packaged card reads
   `0.0-placeholder` and the rail says so).

**The flow**

8. Create a scenario, resolve a base and up to three comparisons: each column appears in well
   under a second; a fourth is refused (cap of four).
9. Change variant: sleeve choices clear, on screen and in the store.
10. Attach a sleeve to every category (Private Equity and Other Private Assets take one choice);
    the export card enables; with **Include fees** ticked and no schedule it says so.
11. Export refuses with 422 `field: minimumInvestment` when any position is below its product's
    minimum — at the packaged catalogue this happens at every mandate the tool offers (§17); raise
    the mandate in the test to prove the success path.
12. A successful export downloads `PMG_Scenario_<CCY>_<Hedging>_<date>.xlsx` with sheets
    `portfolios, risk_dashboard, assumptions, Implementation` (+ hidden `chartData`); the
    Implementation total is exactly 100.00%; the doughnut charts render in Excel.
13. Refresh the page: the scenario rehydrates from `?scenario=<id>` with mandate, basis, columns,
    variant, sleeves and fee settings intact. Both uvicorn workers answer for the same id.
14. Stop the FastAPI process: the page shows the degraded banner within seconds, disables editing
    and export, and recovers on its own when the process returns (D64).

**Admin**

15. As an admin, the rail shows the console links; the console opens with Sleeves, Catalogue,
    Archive, Activity and Proposals; a save appends a revision; a delete archives and restore
    returns it; the Proposals tab lists the export from step 12 and its workbook downloads
    byte-identical.
16. As a non-admin, no console links render and every `/api/scenario/repository…` call is 403.

**Parity**

17. For one scenario, the on-screen implementation table and the workbook's Implementation sheet
    agree line for line (weights, notional, fee bp); the five on-screen doughnuts and the five
    charts agree slice for slice.
18. `sys.modules` contains no `epsilonPhi` after the §14.3 cycle on the host.

---

## 16. Rollout and rollback

### 16.1 Rollout

Ship in this order, verifying each: dependencies → auth names → package → router block → data and
configuration → page folder and route → nav link last. Until the nav link is added the page is
reachable only by URL, which is a useful soak.

### 16.2 Rollback

| Layer | Rollback |
|---|---|
| The page | Remove the nav link; then the route; then the folder. No state is involved. |
| The router block | Remove the block; `isgPMGService` is untouched. |
| The package | Delete `pmgService/scenario/`; nothing else imports it. |
| The bake | Rename the previous store directory back into place (`SCENARIO_BAKED_DIR`); restart. Seconds. |
| Extracts | Restore the previous file; products reload on mtime, the SAA extract and the fee card are read at import — restart. |
| The sleeve library | Nothing is destroyed: restore from the console's Archive/History, or `sleeveTools --import <dated export> --replace`. |
| The register | **No rollback** — restore the SQLite file from backup. Nothing else can recreate it. |
| Scenario state | None needed; 24-hour retention; users re-enter. |

### 16.3 Operating rhythm (from `archive/dataOperations.html`, adapted)

- Daily: back up `$SCENARIO_REGISTER_DB` off the host (`sqlite3 … ".backup …"` or a copy while quiet).
- Weekly: `sleeveTools --census`; a dated `sleeveTools --export`; glance at the console's Activity tab.
- On delivery: fee card → `feeTools --diff` then `--accept --version --source --as-of`, restart;
  products → validate with `SCENARIO_PRODUCTS_SOURCE=<new> sleeveTools --census` first, then swap
  (no restart); SAA extract → `bake --census`, bake to a new directory, verify the manifest, swap,
  restart. The dependency order is fee card → products → sleeves; and extract → bake → store.
- On a library `dataversion` change: re-bake.

---

## 17. Known risks, blockers and unresolved decisions

**Blockers to client use (not to the port):**

- **Placeholder minimums block every export.** The packaged catalogue carries a flat $5,000,000
  `MinimumInvestment` on every SMA and private-market product; a product receives a fraction of its
  category's weight, so at a $50m mandate 8 of 23 positions breach and no proposal exports; even at
  $250m, 10 do (D70, measured). Real minimums in the delivered catalogue clear it with no code
  change. Until then the tool cannot deliver a proposal.
- **Placeholder fee card** (`0.0-placeholder`, flagged on the rail, in the workbook header and in
  the manifest); **placeholder sleeve contents** in all four books (the names are PMG's, D59; the
  products and weights are not); **stand-in SAA weights** (the tickers are real, the allocations
  invented); **stub advisor directory** (24 rows); **placeholder `DistributionYield` and
  `MinimumInvestment`**. Spec open items 17 and 18 stand.
- **Non-USD analytics are USD-context.** GBP, CHF and EUR were baked with `--analytics-currency
  USD`; honest and stamped, but not GBP-context numbers (finding F4).

**Risks in the port:**

| Risk | Severity | Handling |
|---|---|---|
| The package's default paths resolve outside itself (§3.6) | high — the service fails on first read | Set the seven required variables; the §8 checks catch it before start. |
| `pandas`/`openpyxl` absent or at incompatible versions on the host | high | Verify before Step 3; pin the versions proven here as a floor. |
| The host's `accessControl` names or signatures differ | high | Step 2 import check; adapt `requireAdmin` to call the host's `requireAuth`. |
| The host's error contract passes `{"detail": …}` through | medium — the page shows "Request failed (401)" instead of redirecting | §13.3 check; a test request without identity must return `{loginUrl}`. |
| The stores land inside `src/` and get zipped or wiped by deployment | medium | Durable paths outside the tree; the reference block in §11.4. |
| The extract and the bake drift apart (no staleness check at request time, finding F3) | medium | Deliver them together; compare `manifest.source.modified` to the extract on disk at each rollout. |
| Four workers, one set of file stores | low | Designed and now tested for it (§11.3.1); confirm the store paths are shared, not per-worker. |
| The durable path is an NFS mount; SQLite locking is unreliable there | high if it happens | Establish local disk vs network mount (§11.3.2, §18). Register on local disk, backups shipped off. |
| Host SQLite older than 3.8.0 | high | One-line check (§11.3.2); the partial unique index will not compile below it. |
| ~~The host's auth module is `pmgEntitlement`~~ | **closed** | Both modules confirmed; the import splits in two and `requireAdmin` is written (§9.2). |
| ~~`PmgAppException` wraps errors in its own envelope~~ | **closed** | It does; `apiFetch` now reads both shapes (§13.3). |
| **In PROD, an allowlisted PWA with no PERMIT role can only read** | high | Get PWAs into `PMGEditor` before go-live — without it, creating a scenario, attaching a sleeve and exporting are all 403. |
| Which role maintains the sleeve library is undecided | medium | `ISGAdmin` is the natural reading, but the policy gives `PMGEditor` strictly more (it alone has `post`). Confirm with PMG (§9.2). |
| The router's docstring claims a `/api/v1/dashboard` mount | medium | Contradicted by the live proxy line and by the host's own pages working; confirm before Step 4 (§4.0). |
| Python version older than 3.8 on the host | low | `from __future__ import annotations` needs 3.7+; f-strings and `secrets` need 3.6+; proven only on 3.8.20. |
| Theme mismatch with OneGS | cosmetic | A separate change with a measured cost (§10.5). |
| Retention default of 24 h lands scenarios in `$TMPDIR` | low | `SCENARIO_STORE_DIR` is required. |
| The register grows unbounded (~20–30 KB per export) | low | It is the record; back it up daily; budget storage. |

**Unresolved decisions (owner needed):**

- Which currencies to offer, and whether to load GBP/CHF/EUR configs into the library so the bake
  can drop `--analytics-currency` (spec open item 17).
- Scenario retention (open item 8); `dataversion` policy and the re-bake cadence (open item 9).
- Who holds the admin list, and where the host reads it from.
- Whether the advisor directory is swapped for a table before go-live (a code change, §6).
- The OneGS retheme (open items 1 and 2).
- Where extracts, stores and the bake live on the deployed host, and who delivers the bake.

---

## 18. Verify against the live Cyrus codebase

Everything below is asserted by `HOST_AUDIT.md` or assumed here, and cannot be proved from this
repository. Tick each before Step 1.

**Files, names, mounts**

- [ ] `isg-cyrus-pmg/src/cyrus_pmg/pmgService/dashboardRouter.py` exists, defines a module-level
      `router = APIRouter(dependencies=[Depends(requireAuth)])`, and is `include_router`-ed with
      `prefix='/api/v1'` in `pmgService/isgPMGService.py`.
- [ ] `pmgService/core/accessControl.py` exports `requireAuth` and `requireEditor` as FastAPI
      dependencies with the signature `(request: Request) -> str`, plus `isAllowed`,
      `buildLoginUrl`, `getKerberosFromFlaskRequest`. Note the exact way it reads identity from a
      FastAPI request — that is what `getKerberosFromFastApiRequest` must wrap.
- [ ] `dashboard/dashboardFrontend.py` has the per-page route pattern, `DASHBOARD_DIR`, the
      `/api/<path:path>` proxy to `/api/v1/`, and `_PUBLIC_PATHS` including `/api/whoami`,
      `/static/css/` and `/static/js/accessGate.js`.
- [ ] The proxy forwards the session cookie/headers that `requireAuth` reads (the Audit elides
      the `requests.request` arguments).
- [ ] `dashboard/static/js/globals.js` sets `window.API_BASE` to `<origin>/api`;
      `dashboard/static/js/accessGate.js` probes `/api/whoami` and tolerates a page with no
      `.header` block.
- [x] ~~The backend answers `/api/v1/whoami`~~ — **confirmed**, on a public router, with `loginUrl` when there is no identity.
- [ ] `isgPMGService.py` maps auth `HTTPException`s onto top-level `{error}` / `{loginUrl}` and
      malformed bodies onto a JSON `{error}` — never `{"detail": …}`.

**Runtime and dependencies**

- [ ] Python version of the host's virtualenv (≥ 3.8 required; 3.8.20 proven).
- [ ] `openpyxl` installed (3.0.10 proven; needs `openpyxl.chart.DoughnutChart` and
      `openpyxl.formatting.rule.CellIsRule`).
- [ ] `pandas` installed (2.0.3 proven).
- [ ] FastAPI/Starlette/pydantic versions accept sync `def` handlers with `Body(...)`, `Depends`,
      path parameters typed `int`, and `{portfolioKey:path}` (0.122 / 0.44 / 2.5.3 proven).
- [ ] The launcher's worker count for `isgPMGService` (env default 2, code fallback 4); the four store
      paths are shared by every worker and writable by the service user.
- [ ] `python -c "import sqlite3; print(sqlite3.sqlite_version)"` is ≥ 3.8.0 (§11.3.2).
- [ ] **Whether the durable storage path is local disk or a network mount** (§11.3.2). If NFS,
      decide where the proposal register lives before go-live.
- [x] ~~`pmgService/core/` — the real auth module~~ — **answered** by `cyrus-files/pmgEntitlement.py`:
      two modules, `requireAuth`/`requireEditor` present, `requireAdmin`/`isAdmin` to be written (§9.2).
- [x] ~~`exceptionHandlers.py` / `PmgAppException` body shape~~ — **answered**; handled in `apiFetch` (§13.3).
- [x] ~~`dashboardRouter.py` exposes a module-level `router`~~ — **confirmed**, byte-identical to this mirror's.
- [ ] **Whether the router is mounted at `/api/v1` or `/api/v1/dashboard`.** Its docstring says the
      latter; the live proxy builds the former and the host's own pages depend on it. Settle before Step 4 (§4.0).
- [ ] `dashboard/DASHBOARD_AUDIT.md` — read it; it is probably the current version of the document
      §4 is built on, and the dashboard's page folders have already moved on.
- [ ] Durable, writable, backed-up storage exists outside `src/cyrus_pmg/**` for the register,
      the sleeve database, the scenario store, the bake and the extracts; the `gns` deployment does
      not wipe it.
- [ ] Whether the deployed host may run a scheduled job, or whether the bake is always delivered
      from elsewhere; who holds the library and database credentials for it.
- [ ] If `SCENARIO_BAKED_FALLBACK=1` is ever wanted: the analytics library's package name
      (`SAA_ENGINE_PACKAGE`) and layout (`engine._SYMBOLS`), its `dataversion`, and currency
      configs for GBP/CHF/EUR.

**Conventions**

- [ ] The header flex block in `index.html` still carries the inline-style link pattern of §10.3.
- [ ] Page folders are still served with one hand-written route each (no auto-discovery has been
      added since the audit).
- [ ] `PMG_ALLOWED_KERBEROS` is still the allowlist source for local development, and where the
      admin list should be read from in production.

---

## 19. Completion checklist

- [ ] §5 pre-port checks green at the frozen commit; commit hash and bake `updatedAt` recorded.
- [ ] `openpyxl` and `pandas` present in the host environment.
- [ ] Three names added to `accessControl.py`; the Step 2 import check passes.
- [ ] `pmgService/scenario/` copied verbatim (25 modules, 4 data files); importable.
- [ ] Endpoint block appended; 25 `/scenario` routes registered; no duplicate `router` definition.
- [ ] Extracts and bake delivered to durable paths; the seven required variables plus
      `PMG_ADMIN_KERBEROS` set; the three censuses pass on the host.
- [ ] Page folder copied; route and nav link added; §10.6 curls pass.
- [ ] §14.3 probe on the host: full cycle, no `epsilonPhi` in `sys.modules`.
- [ ] §15 acceptance criteria 1–18 pass as a user and as an admin.
- [ ] Register backup scheduled; sleeve export and census scheduled; owners named for the
      §17 decisions.
- [ ] Nothing from §3.4/§3.5 was copied; `/_dev_login` does not exist on the host.

---

## Appendix A — deviations that shape the port (`service/DEVIATIONS.md`, D1–D74)

One line each; the register carries the reasoning.

- **D2** `PUT /scenario/{id}` added so mandate, basis, variant and sleeves survive a refresh.
- **D8** File-backed scenario store; ids `sc_` + 12 hex; `?scenario=` deep link.
- **D16** Tier 0 optimisation inside the analytics library's `assetReturnEstimator.py` — a change to
  that library, relevant to the bake machine only.
- **D17/D47** The bake, and `engine.py` as the single seam.
- **D29/D49** Implementation types (four books) chosen with the base portfolio; `variant` in state.
- **D35/D36** Risk-level labels ride the schema; keys keep the database's spelling.
- **D50** Tactical tilt is an implementation toggle (8% from Investment Grade Fixed Income), not a
  strategic key dimension.
- **D51/D52/D55** Fees resolved from a delivered rate card (CASP/RDR × tier × level × group);
  optional per proposal; `feeTools` is the only way the card changes.
- **D53** Strategic volatility premium overlay (Hybrid Fixed Income; USD and GBP only).
- **D54** The strategic universe is the supplying database's extract; keys are parsed from names;
  every selector option is derived.
- **D56/D58/D63** Delivered product catalogue (`ProductId` the key; two optional columns); a
  view-only catalogue for admins.
- **D57/D59/D60** The sleeve repository (SQLite, in-app admin console, `sleeveTools`); PMG's sleeve
  names; Private Equity + Other Private Assets share one sleeve (`rules.SLEEVE_GROUPS`).
- **D64** Service-down handling: 502/504 mark the service down; read-only from a snapshot; retry
  polls the schema.
- **D65/D66** Immutable sleeve history, soft delete, restore/revert; Archive and Activity views and
  routes; a one-way v1→v2 migration.
- **D67** The workbook is written by the tool; `Reporting` left `engine._SYMBOLS`; the assumptions
  sheet is kept; the golden workbook locks it.
- **D68** No all-zero rows on any sheet.
- **D69** The proposal register: every export recorded permanently with its workbook; five admin
  routes; `createdBy` on scenarios.
- **D70** Fee-group column removed from the table and sheet; "Product Cost"; Minimum Investment with
  a hard export block enforced server-side.
- **D71** Private Equity & Other Private Assets as one implementation line.
- **D72** Surface corrections (total-row hover, sticky header stacking, no notices strip, rail copy).
- **D73** The five composition doughnuts in the workbook as native charts on a hidden `chartData`
  sheet; shares rounded as the page rounds them.
- **D74** Three abbreviated on-screen headers that the sheet spells out.

## Appendix B — where the rest is written down

| File | What it carries |
|---|---|
| `PORTING_GUIDE_AUDIT.md` | The evidence behind this guide: what the previous guide got wrong, what changed, what was verified and how. |
| `service/README.md` | Running the mirror, the wire contract, the adapters, baking. Partly stale (it still describes `sleeves.py` as holding tables). |
| `service/DEVIATIONS.md` | D1–D74, the adapter-side decisions, the spec gaps G1–G8. |
| `service/PERFORMANCE.md` | The analytics profile and the Tier 0 / bake measurements. |
| `archive/dataSources.html`, `archive/dataOperations.html` | The data estate and its operating model (classes A–D, runbooks, rollback). Their test counts predate D69. |
| `archive/exportPortingPlan.html`, `archive/exportTrace.html` | Why and how the export was cut loose from the library (D67). |
| `spec.html` | Revision 6 of the build specification; §16 lists the open items. |
| `HOST_AUDIT.md` | The host as audited on 2026-08-30. |
