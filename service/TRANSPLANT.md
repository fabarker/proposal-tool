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
  not by the package itself.
* **`engine.py` — the analytics library.** It is the only module that names
  that library, and it resolves its six symbols lazily against
  `SAA_ENGINE_PACKAGE` (default `epsilonPhi`). If the host's SAA library is
  named differently, set that variable; if its internal layout also differs,
  edit the `_SYMBOLS` table in that one file. Nothing else in the package —
  adapter, loader, bake, router — names it at all (D47).

Data files travel inside it (`advisors.xlsx`; `portfolio_weights.py` carries
the supplied universe).

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

* `sleeves.py` tables → the PMG-maintained sleeve library, **for all four
  implementation variants**; data ownership is the flagged open PMG question.
  `BASELINE` plus `_VARIANT_OFFERS` compose into `SLEEVE_LIBRARY`; the only
  contract the rest of the package depends on is `VARIANTS`, `listSleeves(
  category, variant)` and `sleeveExists(category, name, variant)`. The three
  non-Multi-Asset libraries are invented — spec open item 18, and the one
  thing here that must not reach a client as it stands (D29).
* `rules.TACTICAL_TILT_PCT` / `_FUNDED_FROM` / `_CATEGORY` → PMG's own tilt
  size and funding source (D50). Changing the percentage is a constant;
  changing the funding category is a constant plus a re-check that every
  offered portfolio can fund it, which `canFundTacticalTilt` already gates.
* `fees.json` → the published CASP and RDR schedules (D51). **Every rate in
  the file is a placeholder** and the file says so (`"placeholder": true`);
  the rail shows a flag until it is cleared. The tiers, the six levels and
  both grids are read from the file, so the published schedule's own tier
  edges and rates drop in without touching `fees.py`; `_assertWellFormed`
  rejects a file with gaps, overlapping tiers or a floor above its ceiling
  at import. The products' `feeGroup` values in `sleeves.py` are assigned by
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
* `portfolio_weights.py` synthetic anchors → approved stored allocations (the
  module's own docstring: "replace the constants before production use").
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
