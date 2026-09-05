# Porting-guide audit — the Proposal Tool against `PORTING.md`, `TRANSPLANT.md` and `HOST_AUDIT.md`

Audit date 2026-09-03, extended 2026-09-04. Repository state at the original pass:
`datasource-dev` at `9f4dc50`. This report is the evidence and reasoning behind the rewritten
`PORTING.md`; it does not repeat the guide. Paths are relative to `epsilon-phi-core/proposal-tool/`.

**Second pass, 2026-09-04.** A structural note on Cyrus was supplied (§4.4), and the concurrency
questions it raised were tested (§8.5). One defect was found in shipped code and fixed (§9.2) —
so, unlike the first pass, this audit did change application code.

**Third pass, 2026-09-05.** Six real Cyrus files arrived at `../cyrus-files/` (§4.5). They close
the auth blocker, correct two facts the screenshots had wrong, and expose **two incompatibilities
that would have failed at runtime** — both now fixed and covered by a test (§9.3).

---

## 1. Executive verdict

**Ready with specified changes.**

The implementation is portable in the way the brief demanded: the page is one folder in the host's
own layout, the back end is one package with intra-package imports only, the endpoints are one
block on the host's router with the host's own auth dependencies, and the analytics library is
provably off the request path. Nothing structural has to be rewritten.

Four things must nonetheless be done that the previous guide either omitted or understated, and
each is a change rather than a copy:

1. **Seven environment variables are mandatory, not optional.** The package's default data paths
   resolve to `proposal-tool/{saaSource,productSource,sleeveSource}` and `service/var/` by climbing
   out of the package directory; in the host those locations do not exist or fall inside the
   source tree. (`universe.py:28`, `products.py:36`, `sleeveRepo.py:75,77`, `proposalRegister.py:55`,
   `bake.py:57`, `scenarioStore.py:35` — resolved at runtime in §8.)
2. **Two dependencies must be present on the host**: `openpyxl` (the whole Excel export, D67) and
   `pandas` (imported at module level by `portfolio_weights.py:85`, which builds two frames at
   import, lines 1257–1258). Neither is named in `HOST_AUDIT.md`'s dependency list.
3. **The auth import splits across two host modules, and one function must be written.**
   `accessControl` holds `getKerberosFromFastApiRequest`; `pmgEntitlement` holds `requireAuth` and
   `requireEditor`; neither has `requireAdmin`/`isAdmin` (§4.5, §9.2). Two runtime
   incompatibilities behind that seam are now fixed on this side (§9.3).
4. **Four data deliveries** — three extracts and the baked store — travel as data, not as part of
   the package copy; the baked store is 688 payloads the host cannot regenerate.

## 2. Verdict explained — documentation versus implementation

**Documentation deficiencies** (the bulk of the findings): both guides predate the four commits
that removed the analytics library from the export (D67), added the proposal register (D69), added
the sleeve history/archive/activity routes (D65/D66) and reshaped the implementation table
(D70–D74). Their description of `engine.py`, the bake, the configuration list, the route count, the
test count and the theme cost is out of date, and their headline — "nothing structural, two copies
and two inserts" — hides the auth additions and the mandatory configuration.

**Implementation limits** (real, but none blocks the port):

- The out-of-package default paths (§1.1) are a design choice that makes the mirror self-contained
  at the cost of silent failure in a bare host. Configuration cures it; a code change is not
  required.
- `pandas` is on the import path only because `portfolio_weights.py` builds notebook-convenience
  frames at import; the request path uses one constant from that module (`ASSET_METADATA`). A
  five-line code change would remove the dependency; it is not needed to port.
- The advisor directory has no environment override (`advisors.py:19`); replacing the stub
  workbook with a table is a code change, contrary to the old TRANSPLANT §2 which lists it among
  "what the host swaps later … no caller changes".
- The minimum-investment hard block (D70) refuses every export on the packaged catalogue's
  placeholder minimums. It blocks **client use**, not the port, and clears with real data.

## 3. Materials examined

| Input | Path | State |
|---|---|---|
| Proposal tool | `proposal-tool/` — `proposalTool/` (page), `service/cyrus_pmg/pmgService/scenario/` (25 modules, 7,732 lines), `service/cyrus_pmg/pmgService/dashboardRouter.py` (696 lines), the stand-ins under `service/cyrus_pmg/`, `generator/`, `backend/`, `saaSource/`, `productSource/`, `sleeveSource/` | working tree at `9f4dc50` |
| Porting guide | `PORTING.md` (324 lines) and `service/TRANSPLANT.md` (204 lines) as at `9f4dc50` | rewritten by this audit |
| Cyrus audit | `HOST_AUDIT.md` (785 lines, committed 2026-08-30 in `4044464`) | read in full; not modified |
| Tests | `service/tests/` — 5 modules, `conftest.py`, `golden/` (3 files + README) | run (§8) |
| Configuration | `service/dashboard.env.defaults`, `service/start_dashboard.sh`, `service/stop_dashboard.sh`, `.gitignore` | read |
| Registers and studies | `service/DEVIATIONS.md` (D1–D74, adapter decisions, G1–G8), `service/PERFORMANCE.md`, `service/README.md`, `README.md`, `archive/BRIEF.md`, `archive/dataSources.html`, `archive/dataOperations.html`, `archive/exportPortingPlan.html`, `archive/exportTrace.html`, `spec.html` §3.4, §3.5, §16, the three `*Source/README.md` | read |
| Git history | all 19 commits touching `proposal-tool` (`6d12d53` 2026-08-30 … `9f4dc50` 2026-09-03); per-file history of the three documents; diffs of the last guide commits; shortstats since each guide's last update | §6 |
| Runtime | the mirror running from `service/start_dashboard.sh` (pids in `service/var/run/`), `SCENARIO_ADAPTER=baked`, fallback on; plus in-process probes with throwaway stores | §8 |
| Cyrus structural note (second pass) | supplied 2026-09-04: transcribed screenshots of the `src/` tree, the PMG service's routes, its project structure, config and design principles. Parts marked truncated in the source | §4.4, §7 |
| Real Cyrus files (third pass) | supplied 2026-09-05 at `../cyrus-files/`: `pmgEntitlement.py`, `dashboardRouter.py`, `dashboardFrontend.py`, `exceptionHandlers.py`, `config.py`, `dashboard.env.defaults`. Transcriptions with marked gaps; source rather than description | §4.5, §9.3 |

## 4. Current-state findings

### 4.1 Architecture and portability

- **One outward import seam.** Nothing inside `scenario/` imports `cyrus_pmg.*` or `..core`
  (grep, zero hits). `engine.py` is the only module naming the analytics library, through
  `os.getenv('SAA_ENGINE_PACKAGE', 'epsilonPhi')` and a five-entry `_SYMBOLS` table resolved lazily
  by PEP 562 `__getattr__` (`engine.py:36, 44–51, 62–86`).
- **The analytics library is off the request path.** A fresh process with `SCENARIO_ADAPTER=baked`
  and `SCENARIO_BAKED_FALLBACK=0` completed schema → create → resolve → sleeves → export → register →
  console → non-admin refusal without `epsilonPhi` entering `sys.modules` (§8.3). Two tests lock
  it: `tests/test_scenario_backend.py::test_an_export_imports_no_part_of_the_analytics_library`
  (a subprocess asserting `LEAKED []`, line 1507) and
  `tests/test_baked_adapter.py::test_schema_and_library_need_no_analytics`.
- **The host shape is reproduced, not approximated.** Flask gate (`dashboardFrontend.py:71–91`)
  with the audit's public paths plus `/_dev_login`; per-page route (`165–168`); proxy rewriting
  `/api/<x>` → `/api/v1/<x>` with `timeout=300`, `allow_redirects=False`, 502/504 JSON
  (`183–210`); FastAPI app mounting the router at `/api/v1` (`isgPMGService.py:33`); router-level
  `Depends(requireAuth)` (`dashboardRouter.py:41`), `Depends(requireEditor)` on the five writes,
  `Depends(requireAdmin)` on the fifteen admin routes (route inventory, §8.1).
- **The page follows the audit's conventions** (`generator/build_styles.py:755–760, 982–1050`):
  `accessGate.js` then `globals.js` at absolute paths, page script last, `'use strict'`, the
  `API_BASE` guard, `apiFetch` with `credentials: 'same-origin'`, `#alertArea`, `loginUrl` follow,
  `DOMContentLoaded` with a `readyState` guard.
- **Stores are worker-safe by construction**: JSON files with atomic rename
  (`scenarioStore.py:51–57`), SQLite for the sleeve library (schema v2, `sleeveRepo.py:86`) and the
  register (schema v1, `proposalRegister.py:52`).

### 4.2 Already well isolated

The page folder (generated, byte-identical mirror — `diff -rq` clean); the scenario package; the
router block (reads no environment, opens no file — grep clean); the auth seam (`Depends` names
only); the `ScenarioPort` protocol (eight methods, `scenarioPort.py`); the offline tools (`bake`,
`sleeveTools`, `feeTools`, all `python -m`); the error contract (returned as `JSONResponse` by the
router for 422/404/502, so independent of the app's exception handlers).

### 4.3 Coupling, gaps and blockers

| Finding | Evidence | Kind |
|---|---|---|
| Default data paths climb out of the package | `universe.py:28`, `products.py:36`, `sleeveRepo.py:75,77`, `proposalRegister.py:55`, `bake.py:57`, `assetEstimates.py:23`; resolved at runtime to `../../../../saaSource/…`, `../../../var/…` (§8.3) | configuration must be mandatory |
| `pandas` on the import path | `portfolio_weights.py:85`, frames at `1257–1258`; imported by `universe.py:24`, `rules.py`, `workbook.py:48`; measured 0.73 s import | dependency |
| The SAA extract is read at request time, not only at bake time | `rules.py` uses `universe.keys/facets/has/realAssetTypes`; `payloads.py` uses `universe.categoryRows`; the extract was loaded during port construction (§8.3) | deliver the extract with the bake |
| Advisor directory has no override | `advisors.py:19` `_XLSX` fixed beside the module; `archive/dataSources.html` F7 | code change for real data |
| Three auth names absent from the audited host module | router imports at `dashboardRouter.py:23–24`; audit §9/§11 list only `requireAuth`, `requireEditor`, `getKerberosFromFlaskRequest`, `isAllowed`, `buildLoginUrl` | insert |
| Export refuses on placeholder minimums | `dashboardRouter.py` `exportScenario` → `model['breaches']` → 422 `minimumInvestment`; measured 8 of 23 positions at $50m | data, not code |
| Theme is not a pure token swap | built CSS: 83 tokens, 755 `var()` uses, but 304 literal hex (77 distinct) and 26 `rgb()` outside `:root` | cost of a later change |
| No logging in the package | no `logging` import in `scenario/` | acceptable; provenance surfaces instead |
| Stale in-repo docs | `service/README.md` still describes `sleeves.py` as tables and `SCENARIO_ADAPTER` default `fixtures`; `scenario/__init__.py` docstring lists a `..core.accessControl` seam and omits eight modules; `README.md` cites D1–D46; `archive/dataOperations.html` expects 193 tests; `service/cyrus_pmg/pmgService/config.py` docstring says the transplant adds `SCENARIO_ADAPTER` to the host's config (it does not — `registry.py:32` reads the environment) | not in this audit's edit scope; noted |

### 4.4 Second pass — what the Cyrus structural note changes

Newer than `HOST_AUDIT.md` (2026-08-30) and contradicting it in four places. Treated as the better
guess, not as proof: it is a transcription, parts are marked truncated, and its `core/` listing may
be curated to the optimize-session workflow rather than complete.

| | `HOST_AUDIT.md` | The note | Consequence |
|---|---|---|---|
| Auth module | `core/accessControl.py` (§9, §11) | `core/pmgEntitlement.py`, "Auth dependency (`requireAuth`)"; **no `accessControl.py` listed** | **Blocker.** The block's first import is `from …core.accessControl import …`, and it is appended to the host's own `dashboardRouter.py` — a failed import takes every existing dashboard endpoint with it, not just ours |
| Roles | `requireAuth` + `requireEditor` (§9) | only `requireAuth` | Free to absorb: the mirror's `requireEditor` is `return requireAuth(request)` |
| `PMG_SVC_PORT` | 8002 (§4) | 8088 | Cosmetic — the proxy reads the host's config |
| `PMG_SVC_WORKERS` | 2 (§5.2) | 4 | Drove the concurrency testing in §8.5, which found a defect |

New, and each now a §18 check in the guide: `core/exceptionHandlers.py` and `PmgAppException`
("structured JSON error responses") sit directly on the contract the page depends on; handlers in
`pmgService/handler/` are class-based, each owning its own `APIRouter`, though `dashboardRouter.py`
still exists as a module beside `optimizeSessionRouter.py`; config is pydantic-settings under a
`PMG_SVC_` prefix (no collision with `SCENARIO_*` or `PMG_ALLOWED_KERBEROS`); the service also
serves `/pmg/*` and `/api/v1/optimize-session*`, neither colliding with `/api/v1/scenario/*`.

Two signs the audit has aged beyond those points: `dashboard/` now lists `approvals/` and
`progressTracking/` and no `modelPlayground/`; `pmgService/handler/` lists no `dashboardHandler.py`,
which audit §8.6 put at the centre of the dashboard data flow. A `dashboard/DASHBOARD_AUDIT.md`
exists in-tree and is probably the current version of the document this port was designed against.

### 4.5 Third pass — the real host files

`../cyrus-files/` holds `pmgEntitlement.py`, `dashboardRouter.py`, `dashboardFrontend.py`,
`exceptionHandlers.py`, `config.py` and `dashboard.env.defaults`. Transcriptions with marked gaps,
but source rather than description; they supersede both `HOST_AUDIT.md` and the 2026-09-04 note.

**Confirmed — every structural assumption of the port holds.** `dashboardRouter.py` opens with
`router = APIRouter(dependencies=[Depends(requireAuth)])`, byte-identical to this mirror's line 41.
The proxy builds `/api/v1/{path}` with `timeout=300` and `allow_redirects=False` and forwards every
header but the hop-by-hop three, so the GSSSO cookie travels. `_PUBLIC_PATHS` matches the mirror's
list exactly. Eleven per-page routes use the exact `send_from_directory` pattern the Proposal Tool
route needs. `/api/whoami` exists on a separate `publicRouter` and returns `loginUrl` when there is
no identity. `getKerberosFromFastApiRequest`, `isAllowed`, `getAllowlist` and `buildLoginUrl` are
all called by the host's own `whoami`.

**Corrected.** `PMG_SVC_PORT` is **8002**, not the 8088 the screenshots showed
(`dashboard.env.defaults`, and `config.py`'s own fallback). `PMG_SVC_WORKERS` defaults to **2** in
the env file though `config.py` falls back to 4 — either way ≥ 2, so the cold-start fix of §8.5
stands.

**The blocker is resolved, and it was half right.** `accessControl` *does* exist — it holds
`requireAllowlistedUser`, `getKerberosFromFastApiRequest`, `isAllowed`, `getAllowlist`,
`buildLoginUrl`. But `requireAuth` and `requireEditor` live in `pmgEntitlement` as back-compat
shims over a three-role model. So the block's single import line splits in two, and neither module
has `requireAdmin`/`isAdmin` — those must be written. Because `post` is PMGEditor-only and ISGAdmin
deliberately lacks it, no `resource:action` isolates an administrator: the gate must test role
membership.

**One operational finding with go-live consequences.** `_allowlistGrant()` gives an allowlisted
kerberos `view + modify` in DEV/UAT-and-below but **`view` only in PROD**. A PWA on
`PMG_ALLOWED_KERBEROS` with no PERMIT role can read the tool in production and cannot create a
scenario, attach a sleeve or export. Getting PWAs into `PMGEditor` is a prerequisite, not a detail.

**Unresolved.** The router docstring says it is "mounted under `/api/v1/dashboard` by the
optimizationService application", which the proxy line contradicts — and the host's own pages
would 404 if the docstring were current, so it reads as stale. Confirm before the router insert.

## 5. Accuracy review of the old guide

Line numbers refer to the versions at `9f4dc50`.

### 5.1 Correct

- `PORTING.md` §1 (lines 29–51) and TRANSPLANT §1: the page folder, its four asset kinds, the two
  absolute shared includes, "never hand-edit — run `generator/build_styles.py`". Verified
  (`build_styles.py:1067, 1122–1140`; five fonts present; mirror identical).
- `PORTING.md` §3–§4 and TRANSPLANT §4: the Flask route and the nav link. Verified against
  `dashboardFrontend.py:165–168`, `index.html:14–19`, and `HOST_AUDIT.md` §7.1, §8.2, §12.
- `PORTING.md` §5 / TRANSPLANT §3: the endpoint block is "in its final form: mixedCase handlers,
  reads inheriting `requireAuth`, writes taking `Depends(requireEditor)`"; endpoints under
  `/api/v1/`; the proxy rewrite. Verified.
- `PORTING.md` §6 adapter table and "`SCENARIO_BAKED_FALLBACK=0` keeps the service entirely free of
  a database". Verified by the probe (§8.3).
- `PORTING.md` §8 verify commands. Still valid.
- The conventions table (lines 176–189). Every row verified in the built page.
- TRANSPLANT §2's note that the router needs `requireAdmin`, `isAdmin` and
  `getKerberosFromFastApiRequest` from the host's `accessControl` (lines 33–39). Correct and
  important; `PORTING.md` never carried it.
- TRANSPLANT §2's D54/D55/D56/D57 descriptions of the extracts, the census tools and the seed
  (lines 70–102, 107–122). Verified against `universe.py`, `products.py`, `sleeveRepo.py`,
  `fees.py`, `feeTools.py`, `sleeveTools.py` and the three `*Source/README.md`.
- TRANSPLANT §5 stand-ins table (lines 171–181). Verified: each named file carries a "NOT
  transplanted" docstring.
- TRANSPLANT's "one change outside the package", the Tier 0 optimisation in the library (lines
  60–65). Still true and still relevant only to the bake machine (`PERFORMANCE.md` Tier 0;
  `tests/test_tier0_beta_equivalence.py`).

### 5.2 Outdated

| Claim | Where | Reality | Evidence |
|---|---|---|---|
| `engine.py` has "six entries" in `_SYMBOLS`; `build_export` does `from .engine import Reporting` | `PORTING.md:64–77`; TRANSPLANT:41–45 | Five entries; `Reporting` removed by D67; the export never touches the library | `engine.py:40–51`; `workbook.py` docstring; `DEVIATIONS.md` D67 |
| "Data files travel inside the package (`advisors.xlsx`; `portfolio_weights.py` carries the supplied universe)" | `PORTING.md:88–90` | The universe is the extract at `SCENARIO_SAA_SOURCE` (D54); data files are `advisors.xlsx`, `fees.json`, `feeRates.csv`, `assetEstimates.json` | `universe.py`; `ls scenario/` |
| "What is baked here is USD only: 4 × 68 = 272 payloads … GBP has not been baked; CHF and EUR have no config" | `PORTING.md:158–163` | 16 slices × 43 = 688 payloads, four currencies, non-USD via `--analytics-currency USD` | `var/baked/manifest.json`: `portfoliosBaked 688`, `currencySubstitutions {GBP,CHF,EUR → USD}`, `updatedAt 2026-09-02T15:34:35` |
| The bake command `--all --workers 4` | `PORTING.md:152`; TRANSPLANT:55–57 | Needs `--analytics-currency USD` for the non-USD slices on this database; `--out` to bake beside the live store | `bake.py --help`; `archive/dataOperations.html` runbook B |
| "four admin-only routes under `/scenario/repository`" | `PORTING.md:234` | Fifteen | route inventory, §8.1 |
| "D20–D64 … Fourteen matter for a port"; "D1–D64" | `PORTING.md:207, 322` | D1–D74; D65–D74 all matter (new routes, a new store, a new env var, the export rewrite, the hard block) | `DEVIATIONS.md` rows D65–D74 |
| "The baked payloads carry the pre-D35 naming (`USD Core Agg`) … the exported workbook reads those fields" | `PORTING.md:293–296` | The store was rebuilt after D54: `name='USD Moderate-Aggressive Core'`, `header='Moderate-Aggressive Core'` | `var/baked/USD_Hedged.json` (§8.3) |
| "Sleeve libraries for three of the four variants are invented. Only PMG Multi-Asset is real." | `PORTING.md:297–298` | The names in all four books are PMG's (D59); the contents of all four are placeholders | `sleeveSource/README.md`; D59 |
| "89 passing, plus 4 gated" | `PORTING.md:306–308` | 252 passed, 4 skipped, five modules | §8.2 |
| Config table: `SCENARIO_ADAPTER` "`fixtures` default here" | TRANSPLANT:187 | `dashboard.env.defaults:21` sets `baked`; `registry.py:32` defaults to `fixtures` only when unset | both files |
| "`SCENARIO_BAKED_DIR` … `service/var/baked`"; "`SCENARIO_STORE_DIR` system temp" | TRANSPLANT:188, 194 | Still true — but the guide never says the same defaults land inside `src/` in the host | §8.3 resolution table |

### 5.3 Incorrect

| Claim | Where | Why it is wrong | Evidence |
|---|---|---|---|
| "Nothing in the list below is a code change" / "Nothing structural remains on the list … two verbatim copies and two documented inserts" | `PORTING.md:6–7`; TRANSPLANT:201–204 | The host's `accessControl.py` needs three new functions; two Python dependencies may need installing; seven variables are mandatory because the package's defaults are unreachable in the host; the advisor swap is code | §1, §4.3 |
| "Every colour resolves through `:root` custom properties, so retheming to `--gs-*` is a token change, not a hunt through component CSS" | `PORTING.md:196`; TRANSPLANT:199 | 304 literal hex colours (77 distinct) and 26 `rgb()` values sit outside `:root` in the built stylesheet; the generator's Python carries them | `proposalTool/static/css/proposalTool.css` (measured §8.4); `generator/themes.py` `_EXTRA`, `generator/implementation.py` |
| "The complete list of what else changes is TRANSPLANT.md §6" | `PORTING.md:145–147` | §6 omits `SCENARIO_SAA_SOURCE` (mandatory), `SCENARIO_FEES_SOURCE`, `SCENARIO_ASSET_ESTIMATES`, `SCENARIO_REGISTER_DB`, `PMG_SVC_HOST`/`DASHBOARD_HOST`, `PMG_SVC_WORKERS` — 20 variables are read, 13 listed | `grep os.getenv` inventory, §8.4 |
| "What the host swaps later, behind unchanged functions … all internal to the package, no caller changes" listing `advisors.py workbook read → the production advisor table` | TRANSPLANT:67–68, 135 | No override exists; it is a code change to `advisors.py` | `advisors.py:19`; F7 |
| "`bake.py` travels with it … Neither adds a host dependency" | `PORTING.md:89–90`; TRANSPLANT:55–58 | The package as a whole adds `openpyxl` and `pandas`; `bake.py` itself needs the library and database on the machine that runs it | §8.3 module list |
| "No tests reach the host … the suite lives in `service/tests/` — 89 passing" | `PORTING.md:306` | Count wrong (252); and the suite depends on the mirror's stand-ins and packaged defaults, which the guide never said | `tests/conftest.py`; grep for `X-Kerberos`, `saaSource` in tests |

### 5.4 Missing

Neither guide covered:

- D67's consequences for the port: the export is engine-free; the assumptions sheet exists;
  `assetEstimates.json` travels; the golden workbook and the leak test exist; the export takes
  ~0.2 s (§8.3) so the proxy's 300 s timeout is irrelevant to it.
- D69: the proposal register — a fourth store (`SCENARIO_REGISTER_DB`), five admin routes, a
  permanent, append-only, unrecoverable SQLite file that needs a backup schedule; `createdBy` on
  scenarios.
- D65/D66: the sleeve history and soft delete; the v1→v2 migration (`sleeveRepo.py:231–273`);
  restore/revert routes; archive and activity routes and CSV exports.
- D70: the minimum-investment hard block, enforced in `exportScenario`, and the fact that on the
  packaged catalogue it refuses every export.
- D73: the hidden `chartData` sheet and native charts (an `openpyxl.chart` dependency).
- The complete route table (25) and the three gates.
- The complete environment inventory (20) with defaults and their resolution in the host.
- The runtime read of the SAA extract (availability and category rows), which ties the extract
  delivery to the bake delivery.
- Dependencies and versions actually exercised (Python 3.8.20, FastAPI 0.122.0, Starlette 0.44.0,
  pydantic 2.5.3, openpyxl 3.0.10, pandas 2.0.3).
- Two-worker safety of the stores; where stores must live given the host zips `src/cyrus_pmg/**`.
- Browser state and deep links (`localStorage` keys `pmg.proposalTool.railCollapsed`,
  `pt.snapshot.<id>`; `?scenario=`; console hashes) — relevant because the audit says host pages use
  query deep links.
- The `/_dev_login` stand-in and the `kerberos` cookie / `X-Kerberos` header as things that must
  not reach the host.
- Rollout order, rollback per layer, acceptance criteria, an operating rhythm, and a "verify on
  the live host" list.

## 6. Changes since the guide was last updated

**When the guides were last materially updated.**

- `PORTING.md`: written in substance on 2026-09-01 (`6462895` "One seam for the analytics library,
  and PORTING.md made ready"); extended on 2026-09-02 (`070da20`, D54–D63); last touched on
  2026-09-03 in `4d2ba93` — a nine-line diff adding the D64 bullet and renumbering "D63" to "D64".
- `TRANSPLANT.md`: last updated 2026-09-02 in `070da20` (+64/−19 lines).
- `HOST_AUDIT.md`: unchanged since 2026-08-30 (`4044464`).

**Implementation commits after that**, with their effect on the porting approach:

| Commit | Date | What changed under `proposal-tool` | Effect on the port |
|---|---|---|---|
| `4d2ba93` Service-down handling, and the data estate written down | 2026-09-03 | `core.js` +219, `build_styles.py` +103, `archive/dataSources.html`, `archive/dataOperations.html`, `archive/serviceDownPages.html`; `PORTING.md` got its D64 bullet | The page now degrades on 502/504 and polls the schema; a host with its own health contract may want the retry pointed at it. Guide had this. |
| `b9863ba` The record, the archive, and the export cut loose from epsilonPhi | 2026-09-03 | `workbook.py` +740, `sleeveRepo.py` +657, `liveAdapter.py` +84, `bake.py` +66, `bakedAdapter.py` +39, `engine.py` −Reporting, new `assetEstimates.py`/`.json`, `dashboardRouter.py` +114 (history/restore/revert/activity/archive routes), golden files, `archive/exportPortingPlan.html`, `archive/exportTrace.html`, `archive/sleeveArchive.html`; tests +808 | **Invalidates `PORTING.md` §2** (the engine seam description) and the bake/export narrative; adds seven admin routes and a migration; makes the export engine-free and fast. Neither guide updated. |
| `2f08d6b` The landing page says what the tool does | 2026-09-03 | generator copy/CSS | none |
| `9f4dc50` The proposal register, and nine changes to the implementation table | 2026-09-03 | new `proposalRegister.py` (+420) and its tests (+293); `dashboardRouter.py` +97 (five register routes, the export records first and refuses on minimums); `workbook.py` +205 (columns, combined row, charts); `scenarioStore.py` `createdBy`; `dashboard.env.defaults` `SCENARIO_REGISTER_DB`; `core.js` −notices; `DEVIATIONS.md` D69–D74 | Adds a store, an env var, five routes, a backup obligation, an `openpyxl.chart` dependency and the export block. Neither guide updated. |

Totals since `4d2ba93` (the last `PORTING.md` touch): `scenario/` 13 files, +6,153/−327; the router
+200/−11; `generator/` +1,347/−122; tests +1,872/−15. Since `070da20` (the last `TRANSPLANT.md`
update) the `generator/` figure is +1,657/−134 and tests +1,951/−15.

## 7. Cyrus compatibility analysis

### 7.1 Confirmed matches (Verified in the mirror; Audit for the host side)

| Aspect | Host (Audit) | Tool (Verified) |
|---|---|---|
| Page folder layout and naming | `pageName/pageName.html`, `static/{css,js}/pageName.*`, lowerCamelCase (§11) | `proposalTool/` exactly so, plus `static/fonts/` |
| Route registration | one `@app.route('/<page>/<path:filename>')` with `send_from_directory` (§7.1) | `dashboardFrontend.py:165–168` in that form |
| Nav | inline-styled `<a>` in the `index.html` header block (§7.2, §8.2) | `index.html:14–19` identical markup |
| Script order | `accessGate.js` → `globals.js` → page script at end of body (§11) | `build_styles.py:759–760`, page script last |
| `API_BASE` | set by `globals.js`, guarded in page JS (§8.5) | guard at `build_styles.py:991–993` |
| Fetch and errors | `fetch(${API_BASE}/…)`, `credentials: 'same-origin'`, `body.error` to `#alertArea`, `body.loginUrl` redirect (§12 step 3) | `apiFetch` in `HOST_PRELUDE` |
| Proxy | `/api/<x>` → `/api/v1/<x>`, `timeout=300`, `allow_redirects=False`, 502/504 JSON (§4, §13) | `dashboardFrontend.py:183–210` |
| Backend | FastAPI, `dashboardRouter` mounted at `/api/v1`, mixedCase handlers, `Depends(requireAuth)`/`requireEditor` (§4, §9, §12 step 7) | `isgPMGService.py:33`; `dashboardRouter.py:41`; handler names |
| Auth gate | `before_request` allowlist; 302 / 401 `{loginUrl}` / 403 card or JSON (§7.3, §9) | `dashboardFrontend.py:71–91`; observed over HTTP (§8.3) |
| Env | `PMG_ALLOWED_KERBEROS`, `FRONTEND_PORT`, `PMG_SVC_PORT` (§5.5) | same names, same semantics |
| No hot reload; restart Flask for Python changes (§13) | — | guide says so |
| Query-string deep links are a host pattern (§8.5) | — | `?scenario=<id>` |

### 7.2 Conflicts or adaptations required

| Item | Detail | Resolution in the guide |
|---|---|---|
| `accessControl.py` lacks three names | `getKerberosFromFastApiRequest`, `isAdmin`, `requireAdmin` | §9.2 reference implementation to adapt |
| Admin list source | mirror reads `PMG_ADMIN_KERBEROS` | host chooses; semantics stated |
| Dependencies | `openpyxl`, `pandas` not in the audit's list | §12 |
| Default paths | unreachable in the host | §3.6, §11.4 mandatory set |
| Stores under `src/` | CI zips `src/cyrus_pmg/**` | §11.4 reference block places them outside |
| Theme | host pages load `onegsTheme.css` + `dashboard.css`; the tool is self-contained | deliberate; cost of retheme measured in §10.5 |
| Header shape | host pages carry `.header` + `.back-btn`; the tool has a rail | deliberate, agreed (D27/D28) |
| Chart.js CDN, `isgCodeSelector.js` | host pages use them; the tool does not | deliberate (self-contained; not schema-scoped) |
| `/_dev_login`, `kerberos` cookie, `X-Kerberos` header | mirror-only | excluded explicitly |
| Two uvicorn workers | host launcher | stores are file/SQLite; noted |
| Health/retry | the page polls the schema because the mirror's Flask `/health` never touches the backend | host may point the retry at its own contract (D64) |

### 7.3 Assumptions that remain unverifiable

Listed in full as the checklist in `PORTING.md` §18. The most consequential: the exact host
`accessControl` names and how identity is read from a FastAPI request; that the host proxy forwards
what `requireAuth` reads; that the host app maps auth `HTTPException`s to top-level
`{error}`/`{loginUrl}`; that `/api/v1/whoami` exists on the backend; the host's Python, FastAPI,
Starlette, pydantic, openpyxl and pandas versions; where durable writable storage lives and whether
`gns` preserves it; whether the host may run the bake or must receive it.

## 8. Validation results

### 8.1 Static inventories (from the code)

- Routes: 25 (`@router.*` decorators parsed with their `Depends`): 5 reads on the router gate, 5
  `requireEditor` writes, 15 `requireAdmin`.
- Environment variables read: 20 (`grep -rnE "os\.(getenv|environ)"` over `service/` and
  `generator/`).
- Outward imports from `scenario/`: none; the analytics library named only in `engine.py`.
- Third-party imports in the service: `fastapi`, `flask`, `openpyxl`, `pandas`, `requests`
  (stand-ins included); in tests additionally `numpy`, `pytest`, `starlette`.
- Dependency declarations in `proposal-tool/`: none (no `requirements*.txt`, `pyproject.toml`,
  `setup.*`).

### 8.2 Tests

```
cd proposal-tool/service && PYTHONPATH=. python3 -m pytest tests -q -rs
252 passed, 4 skipped in 40.43s
SKIPPED [4] tests/test_tier0_beta_equivalence.py — needs a live SAA analytics database; set SAA_ENGINE_LIVE=1
```

`node` was available, so the JavaScript-mirror tests ran rather than skipping. The four skipped
tests guard a change inside the analytics library (`PERFORMANCE.md` Tier 0) and need its database;
they are not part of the port.

### 8.3 Runtime

**The running mirror** (`SCENARIO_ADAPTER=baked`, `SCENARIO_BAKED_FALLBACK=1`, one worker, pids
from `service/var/run/`):

- `GET /health` on 8001 and 8002: `{"status":"ok"}`.
- Gate shapes unauthenticated: `GET /` → 302 to `/_dev_login?next=…`; `GET /api/scenario/schema`
  → 401 `{loginUrl}`; `GET /api/whoami` → 401 `{loginUrl}`; `/static/js/accessGate.js` → 200
  (public). Authenticated (`kerberos` cookie): `whoami` → `{kerberos, allowed: true}`; a
  non-allowlisted id → 403 `{error}`.
- Schema: keys `options, availability, categories, rules, fees, capabilities, dataInfo`;
  `capabilities {canExport, canEdit, canAdmin}`; `dataInfo.adapter "baked (live fallback)"`,
  `dataversion "… 688 portfolios baked · CHF, EUR, GBP, USD"`; `availability` 43.
- A full HTTP cycle with a directory advisor: create → `PUT` variant → `POST` portfolio 200 in
  9 ms (warm 8.6 ms) → export before sleeves 422 `field: sleeves` naming the five categories →
  `PUT` sleeves + fees → export at $50m 422 `field: minimumInvestment` naming the breaching
  positions → rehydrate with 12 keys → `DELETE` of the base 422 → `GET /scenario/fees?whole=1`
  (5 tiers, 6 groups, placeholder delivery) → unknown scenario 404 with the retention message →
  malformed body 422 `{error: 'Malformed request.'}`. The first attempt at creation was refused
  until `primaryPwa` was a name from the advisor directory — every adapter's `validate_mandate`
  runs `rules.validateMandate(mandate, advisors.advisorExists)` (`rules.py:519–535`).
- The bake manifest: `updatedAt 2026-09-02T15:34:35`, `portfoliosBaked 688`, four currencies,
  `source.modified 2026-09-02T10:59:41`, `unparsedNames []`, `currencySubstitutions {GBP, CHF, EUR
  → USD}`; every slice `complete: true`, 43 of 43; the store is 3,018,459 bytes over 18 files.

**In-process probe** (fresh process, `SCENARIO_BAKED_FALLBACK=0`, throwaway store/sleeve
db/register, `PMG_ALLOWED_KERBEROS=PMG_ADMIN_KERBEROS=fbarker`, `X-Kerberos` header): schema 200
(`adapter "baked"`) → create → variant → resolve 6.4 ms → cross-currency comparison 422
`field: currency` → comparison 200 → unavailable key 422 `field: key` → rehydrate 12 keys →
sleeve products carry 14 fields → sleeves + fees → **export at a $5bn mandate 200 in 194 ms,
21,624 bytes, sheets `portfolios, risk_dashboard, assumptions, Implementation, chartData`** →
register lists 1 row (`sequence 1`, `exportedBy`, `createdBy`, `workbookBytes 21624`) → the stored
workbook downloads byte-identical → mandate to $50m → export 422 `minimumInvestment` → console
payload keys `archived, catalogue, categories, fixedCategories, orphans, products, register,
sleeves, store, user, variants` → admin list emptied: 403 and `canAdmin: false`. Elapsed 2.78 s.
`sys.modules` afterwards: **no `epsilonPhi`**; `pandas`, `numpy`, `pyarrow` present (via
`portfolio_weights.py`); third-party loaded: `anyio, fastapi, httpx, openpyxl, pydantic, starlette`.

**Package import cost** (fresh process): pandas 0.73 s, then the package and port construction
0.37 s; the SAA extract is loaded during construction; first `get_schema` 5.1 ms.

**Offline tools**: `bake --census` (172 portfolios, 2,468 holdings, 0 unparsed, no unknown
tickers, the closed vocabularies printed); `sleeveTools --census` against a throwaway seed (102
sleeves — 22/31/26/23 by book; fixed categories Asset Allocation Strategies and Hybrid Fixed
Income); `feeTools --census` (180 cells, 5 tiers, 5 groups, `0.0-placeholder`).

**Default path resolution** (in-process, relative to the package): SAA extract
`../../../../saaSource/saaPortfolios.csv`; catalogue `../../../../productSource/products.csv`;
seed `../../../../sleeveSource/sleeves.csv`; sleeve db `../../../var/sleeves.db`; register
`../../../var/proposals.db`; bake store `../../../var/baked`; asset estimates
`../../../var/baked/assetEstimates.json`; rate card and `fees.json` and `advisors.xlsx` beside
their modules; scenario store `$TMPDIR/pmg_proposal_scenarios`.

**Baked payload naming**: `USD|ModAgg|Core|0` → `name 'USD Moderate-Aggressive Core'`; non-USD
payloads carry `analyticsCurrency 'USD'`.

### 8.4 Measurements used for corrections

- Built CSS: 83 `:root` tokens; 755 `var(--…)` references; 304 literal hex colours (77 distinct)
  and 26 `rgb()/rgba()` outside `:root`; `@font-face src:url("../fonts/*.woff2")` for all five.
- Page: html 11,136 B, css 123,203 B, js 343,720 B; fonts 235 KB; the service mirror
  byte-identical.
- Package: 25 modules, 7,732 lines, 856 KB with data files.

### 8.5 Concurrency, second pass (2026-09-04)

Prompted by the worker count in §4.4, and by the question of whether the two SQLite stores are safe
on a Linux host. Four processes released together on a shared wall-clock barrier, one fresh
interpreter each.

| Test | Before the fix | After |
|---|---|---|
| 100 concurrent register writes across 4 processes | 100/100, no `database is locked` | unchanged |
| 4 processes against an existing sleeve database | all 4 fine | unchanged |
| 4 processes cold-starting the register | all 4 fine | unchanged |
| 4 processes cold-starting an **empty** sleeve database | **3 of 4 failed**: `OperationalError: table sleeves already exists` | all 4 fine, ×5 runs |
| the same, with the check→seed window widened to 1 s | **1 failure + 306 history revisions where 102 are correct** | 102/260/102 exactly, ×3 runs |
| 200 warm `_connect()` after the fix | — | 0.65 ms each |

Two check-then-act races, both in `sleeveRepo`:

1. `_migrate` did `if not _tableExists(conn, 'sleeves'): conn.executescript(_SLEEVES_TABLE…)`, and
   `_SLEEVES_TABLE` was `CREATE TABLE {name}` with no `IF NOT EXISTS` — the only statement in the
   module without it.
2. `_connect` re-asked `count == 0 and seeded is None` outside any write transaction, so two
   workers could both seed. The live-name partial index refuses the duplicate *sleeves*, which is
   why the first race masked this one; `sleeveHistory` has no such index, so the record trebled.
   The second race did not fire at natural speed — the four processes stagger themselves by their
   own import time — which is precisely what would have let it reach the host.

Platform findings that need no code change: `sqlite3` is stdlib and the stores use only
`os.path.join` and `os.replace`, so nothing is macOS- or Linux-specific; the partial unique index
sets a **SQLite ≥ 3.8.0** floor (RHEL 7 ships 3.7.17); journal mode is the default `delete`, not
WAL, which is comfortable at this write volume; and SQLite locking is unreliable on NFS, which
matters because four workers share one file — recorded as R15/R16 in §10.

### 8.6 Failures, limitations and skipped checks

| Check | Result | Reason | Limitation |
|---|---|---|---|
| Backend-down 502/504 through the proxy | not exercised | would require stopping the developer's running service | the proxy's mapping was read in code (`dashboardFrontend.py:203–207`) and the page's reaction in `core.js:2471–2525`; not observed live in this audit |
| Live adapter, the bake, `SAA_ENGINE_LIVE=1` tests | not run | need the analytics library's database | the bake store was inspected, not regenerated; claims about bake timings come from `PERFORMANCE.md` and the manifest |
| A successful export over HTTP against the running service | deliberately not done | it would append a permanent row to the developer's own register (`var/proposals.db`, which already holds real rows) | the success path was proven in-process against a throwaway register instead |
| A real non-admin, allowlisted user | not possible | only one id is allowlisted in the dev configuration | the branch was exercised by emptying the admin list in-process (403; `canAdmin: false`) |
| The first two HTTP/in-process attempts | failed at creation | `primaryPwa` must be an advisor-directory name (`rules.validateMandate`, `rules.py:534`) | re-run with a directory name; the rule is recorded in the guide's §11.2 |
| `timeout` on macOS | absent | not a project matter | censuses re-run without it |
| The host itself | not available | — | everything host-side is Audit or Unverifiable |

Side effects of this audit: two throwaway scenarios were created in the running mirror's scenario
store (24-hour retention); no register row was written; no sleeve was changed; no file under
`proposal-tool/` other than the three documents was modified (§9).

## 9. Guide changes made

### 9.1 First pass — the rewrite

**`PORTING.md` — rewritten in full** (324 → 1,136 lines), restructured into the nineteen sections
the brief asks for. Substantive changes:

1. The headline changed from "two copies, two inserts, configuration" to the honest count: two
   copies, four inserts (router block, Flask route, nav link, three `accessControl` names),
   configuration with seven mandatory variables, and four data deliveries.
2. §3.6 added: every default data path and where it resolves in the host — the reason the
   variables are mandatory.
3. §3.2 lists all 25 modules with their runtime role on the baked path; `engine.py` is described
   as five symbols with `Reporting` gone (D67).
4. §9.1 gives the anatomy of the router insert by line number, including the one line that must
   **not** be inserted (`router = APIRouter(...)`), and §9.2 the reference implementation of the
   three auth additions.
5. §11.1 is the full 25-route table with gates; §11.2 the contracts the page depends on; §11.3 the
   four stores with schema versions, concurrency and backup; §11.4 all twenty variables with
   defaults and the host requirement, plus a reference `env.defaults` block; §11.5 the bake as a
   delivery with the current numbers and the `--analytics-currency` requirement.
6. §12 names `openpyxl` and `pandas` with the versions proven and explains why pandas is on the
   import path; states what a no-fallback service actually imports.
7. §13 sets out the three roles, the identity seam, the error contract the host must meet, and
   that the package logs nothing.
8. §14 describes the suite honestly (a pre-port gate written against the stand-ins), the golden
   workbook, and the portability probe to repeat on the host.
9. §15 has eighteen acceptance criteria; §16 rollout order, rollback per layer and an operating
   rhythm; §17 separates client-use blockers (placeholder data, the D70 block, USD-context
   analytics) from port risks; §18 is the verify-against-live-Cyrus checklist; §19 the completion
   checklist.
10. The theme claim was replaced with the measured cost (§10.5).
11. Obsolete guidance removed: the six-symbol `engine.py`, the `Reporting` example, the
    USD-only/272-payload bake, the pre-D35 naming caveat, the "only Multi-Asset is real" caveat,
    the 89-test count, "D1–D64".
12. Appendix A condenses D2–D74 to one line each; Appendix B points at the supporting documents
    and flags which are themselves stale.

**`service/TRANSPLANT.md` — reduced to a pointer** (204 → 22 lines). Two documents describing the
same list is how the drift happened (they disagreed on `SCENARIO_ADAPTER`'s default and on which
variables exist). The file remains so that references to it resolve, with a table of where each old
section now lives.

**`PORTING_GUIDE_AUDIT.md` — this report, new.**

On the first pass no other file was modified. The second pass changed application code — see §9.2.

### 9.2 Second pass — the code fix and the guide updates

**Application code changed** (the first pass changed none; this is the exception, and it is a
defect found by the audit's own testing rather than a porting convenience):

- `service/cyrus_pmg/pmgService/scenario/sleeveRepo.py` — `_SLEEVES_TABLE` gains `IF NOT EXISTS`,
  and the inline seed check in `_connect` becomes `_seedOnce`, which re-reads under
  `BEGIN IMMEDIATE` and takes the write lock only when the library looks empty. Behaviour is
  unchanged for every existing caller; what changes is that a concurrent cold start is now correct.
- `service/tests/test_sleeve_repository.py` — one test added,
  `test_four_workers_cold_starting_together_seed_the_library_once`, which forces the check→seed
  window and asserts one `seeded` revision per sleeve. Verified to fail against the pre-fix module
  (`table sleeves already exists`) and pass against the fix. Suite: **253 passed, 4 skipped**.

**Guide updates**: a new §4.0 setting out where the two host sources disagree and which to believe;
§9.2 rewritten to lead with the auth-module blocker and its blast radius; §11.3.1 (four workers,
the measured table, the fix, and the seed-before-start deploy step) and §11.3.2 (network
filesystems, the SQLite version floor, journal mode) added; §12 gains the `sqlite3` row; §13.3
gains the `exceptionHandlers.py` instruction; §8 Step 2 reordered to settle the auth module before
anything touches the host; the port (8088), worker count (4) and test count (253) corrected
throughout; §17 and §18 extended with the five new risks and seven new checks.

### 9.3 Third pass — two runtime incompatibilities, found and fixed

Both were invisible to inspection and would have failed on the host at runtime.

**1. The auth dependencies return an object where the block binds a string.** `requireCan` returns
a `UserData`; the mirror's `accessControl` returns the kerberos itself. The host never noticed
because its own routers use these purely as gates — `dependencies=[Depends(requireEditor)]` — and
never bind the value. This block binds it in twenty handlers and hands it to three stores that
cannot take an object. Demonstrated before fixing:

| Path | Failure |
|---|---|
| `scenarioStore.createScenario(createdBy=…)` → JSON | `TypeError: Object of type UserData is not JSON serializable` |
| `proposalRegister.record(…)` → `exportedBy TEXT` | `InterfaceError: Error binding parameter` |
| `sleeveRepo.*(user=…)` → `sleeveHistory.actor TEXT` | `InterfaceError: Error binding parameter` |

That is every scenario created, every export, and every sleeve save. Fixed by `_callerId()` in
`dashboardRouter.py`, normalising at the eight consumption points, so the block runs unchanged
against either host; the twenty `user: str = Depends(...)` annotations were dropped, `str` being
untrue on the host. Covered by
`test_the_caller_id_survives_either_shape_of_auth_dependency`, verified to fail against the
previous module.

**2. The error contract is inverted.** `PmgAppException` puts the HTTP status *phrase* in `error`
and the message in `detail`; this block puts the message in `error` and adds `field`. Reading
`error` alone showed a caller "Forbidden" rather than the reason. `apiFetch` now prefers a string
`detail` and falls back to `error` — checked against seven shapes, including FastAPI's list-shaped
`detail`, which falls through rather than rendering as `[object Object]`.

Suite after both: **254 passed, 4 skipped**.

## 10. Risk register

| # | Risk | Severity | Likelihood | Evidence | Mitigation | Needs live Cyrus? |
|---|---|---|---|---|---|---|
| R1 | Package defaults resolve outside the package; the service fails on first read in a bare host | High | Certain unless configured | §8.3 path resolution; `universe.py:28`, `products.py:36`, `sleeveRepo.py:75,77` | Seven mandatory variables (`PORTING.md` §11.4); Step 5 censuses | No |
| R2 | `pandas` / `openpyxl` absent or incompatible on the host | High | Unknown | `portfolio_weights.py:85`; `workbook.py:39–45`; not in `HOST_AUDIT.md` §5.1 | Verify before Step 3; pin 3.0.10 / 2.0.3 as floors | Yes |
| R3 | Host `accessControl` names/signatures differ from the mirror | High | Medium | router imports; audit lists five names | Step 2 import check; adapt `requireAdmin` to the host's `requireAuth` | Yes |
| R4 | Host app leaks `{"detail": …}` on auth failures; the page cannot redirect to login | Medium | Low (host pages read `body.error`) | `isgPMGService.py:36–55` documents the contract | §13.3 check with an unauthenticated request | Yes |
| R5 | Stores placed under `src/` are zipped, wiped or unwritable on deployment | Medium | Medium | audit §6 (CI zips `src/cyrus_pmg/**`); defaults land in `src/var` | §11.4 reference paths outside the tree | Yes |
| R6 | Extract and bake drift apart (no runtime staleness check) | Medium | Medium over time | `archive/dataSources.html` F3; availability read from the extract at request time (§4.3) | Deliver together; compare `manifest.source.modified` at rollout | No |
| R7 | The register is lost | High | Low | append-only, no regeneration (`proposalRegister.py` docstring; D69) | Daily off-host backup (§16.3) | Partly (where backups run) |
| R8 | Every export refused on placeholder minimums; the tool cannot deliver a proposal | High for client use | Certain until real data | D70; measured 8/23 breaches at $50m | Real catalogue minimums; no code change | No |
| R9 | Non-USD analytics are USD-context | Medium | Certain for GBP/CHF/EUR | manifest `currencySubstitutions`; `PERFORMANCE.md` findings | Decide currency coverage; load configs and re-bake | No |
| R10 | Proxy does not forward what `requireAuth` reads | High | Low (host pages already work through it) | audit elides the `requests.request` arguments; mirror passes `cookies=request.cookies` | §18 check | Yes |
| R11 | Python < 3.8 on the host | Low | Low | `from __future__ import annotations` everywhere; proven only on 3.8.20 | §18 check | Yes |
| R12 | Two workers, per-worker store paths | Low | Low | stores are files/SQLite by design (`scenarioStore.py` docstring) | Shared paths; §15 criterion 13 | Yes |
| R13 | A future retheme is under-budgeted | Low | High | 304 literal colours outside `:root` | §10.5 measured cost | No |
| R15 | Durable storage is an NFS mount; SQLite locking is unreliable there, with four workers on one file | High | Unknown — enterprise "durable shared storage" often is | §8.5; SQLite's own documented limitation | Establish local vs network (§18); register on local disk with backups shipped off | Yes |
| R16 | Host SQLite older than 3.8.0 — the partial unique index will not compile | High | Low | §8.5; RHEL 7 ships 3.7.17 | One-line check in §11.3.2 | Yes |
| R17 | Host auth module is `pmgEntitlement`, not `accessControl`; a failed import in the appended block breaks the host's whole dashboard router | High | Medium — the newer source says so | §4.4 | Settle in Step 2 before anything else; §9.2 | Yes |
| R18 | `PmgAppException` wraps non-2xx bodies in an envelope, breaking the 401 redirect and inline field errors | Medium | Unknown | §4.4; `exceptionHandlers.py` named but not seen | Read it before Step 4; §13.3 | Yes |
| ~~R19~~ | ~~`dashboardRouter.py` converted to class-based handlers~~ | **closed** | — | §4.5: module-level `router` confirmed, byte-identical to the mirror's | — | No |
| R20 | **In PROD an allowlisted PWA with no PERMIT role can only read** — no scenario, no sleeve, no export | High | Certain unless PWAs are enrolled | §4.5, `pmgEntitlement._allowlistGrant()` | Enrol PWAs in `PMGEditor` before go-live | Partly — the enrolment itself |
| R21 | Which role maintains the sleeve library is undecided; the policy gives PMGEditor strictly more than ISGAdmin | Medium | Certain | §4.5: `post` is PMGEditor-only | PMG decision, then `requireAdmin` (§9.2) | No |
| R22 | The router may be mounted at `/api/v1/dashboard`, not `/api/v1` | Medium | Low — the docstring reads as stale | §4.5: proxy builds `/api/v1/{path}` | Confirm before the insert; every §11.1 path shifts if true | Yes |
| R14 | Stale companion docs mislead a porter (`service/README.md`, `scenario/__init__.py`, `README.md`, `archive/dataOperations.html` counts, `config.py` docstring) | Low | Medium | §4.3 last row | `PORTING.md` Appendix B flags them; fix in a follow-up outside this audit's scope | No |

## 11. Outstanding decisions and recommended next steps

**Decisions for owners** (also in `PORTING.md` §17):

1. Currency coverage — restrict the offered list, or load GBP/CHF/EUR configs into the library so
   the bake can drop `--analytics-currency USD` (spec open item 17).
2. Scenario retention (open item 8) and the `dataversion` / re-bake cadence (open item 9).
3. Who holds the admin list, and from which source the host reads it.
4. Whether the advisor directory becomes a table before go-live (code change in `advisors.py`).
5. The OneGS retheme (open items 1 and 2), now with a measured cost.
6. Where extracts, stores and the bake live on the deployed host, and who delivers the bake.
7. Whether to remove the import-time pandas frames in `portfolio_weights.py` (would drop a
   dependency; a small code change with a re-run of the suite).

**Recommended next steps, in order:**

1. Work through `PORTING.md` §18 against the live `isg-cyrus-pmg` checkout; record each answer in
   that checklist.
2. Obtain the real product catalogue (with minimums) and the real fee card; run
   `feeTools --diff`, `sleeveTools --census` and `bake --census` against them here, before any
   host work — R8 and the fee placeholder are the client-facing blockers.
3. Fix the stale companion documents listed in §4.3 (a documentation-only change) so the next
   porter is not misled by `service/README.md` or the package docstring.
4. Optionally make R1 impossible rather than merely configured: have the package refuse to start
   when a default path does not exist, or drop the out-of-package defaults. That is a code change
   and was not made here.
5. Then execute `PORTING.md` §8 step by step, holding to §15 before the nav link goes in.
