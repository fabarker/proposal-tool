# Proposal Tool service — the host mirror

This directory reproduces, inside epsilon-phi, the topology of
`isg-cyrus-pmg/src/cyrus_pmg`, so the Proposal Tool back end is developed in
its final shape. The package is literally named `cyrus_pmg` so every import in
the transplantable files is byte-identical to what it will be in the host.
`TRANSPLANT.md` is the file-by-file porting list; `DEVIATIONS.md` records
every departure from the hand-off package and every spec gap met on the way.

## Run it

```bash
cd proposal-tool/service
./start_dashboard.sh          # [1/2] isgPMGService (FastAPI)  [2/2] Flask frontend
open http://localhost:8001/proposalTool/proposalTool.html
./stop_dashboard.sh
```

`dashboard.env.defaults` carries every knob (sourced by the launcher; override
in the shell):

| Variable | Default | Meaning |
|---|---|---|
| `FRONTEND_PORT` / `PMG_SVC_PORT` | 8001 / 8002 | The host's ports |
| `PMG_ALLOWED_KERBEROS` | `fbarker` | Comma-separated allowlist — the same variable host dev uses. Empty allows nobody. |
| `SCENARIO_ADAPTER` | `fixtures` | `fixtures`, `epsilonphi` or `baked` (see below) |
| `SCENARIO_BAKED_DIR` | `service/var/baked` | Where the bake store lives |
| `SCENARIO_BAKED_FALLBACK` | `1` | `0` = never fall through to live analytics (no database needed at all) |
| `SCENARIO_STORE_DIR` | system temp | Where scenario state files live |
| `SCENARIO_RETENTION_HOURS` | 24 | Scenario expiry |
| `PMG_SVC_WORKERS` | 1 | The host runs 2; epsilonPhi caches are per-process |
| `SCENARIO_FIXTURES_*` | unset | Fixture failure/latency knobs — see `dashboard.env.defaults` |

Sign-in: the GSSSO stand-in lives at `/_dev_login` (the gate redirects there).
For curl, send the cookie: `curl -b kerberos=fbarker …`.

Tests (dev-side; nothing ships to the host's untested dashboard package):

```bash
cd proposal-tool/service && PYTHONPATH=. python3 -m pytest tests -q
# the bit-identity guard for the epsilonPhi optimisation needs a database:
EPSILONPHI_LIVE=1 PYTHONPATH=".:../../src/python" \
    python3 -m pytest tests/test_tier0_beta_equivalence.py -q
```

## The three adapters

| `SCENARIO_ADAPTER` | Data | Cold resolve | Database |
|---|---|---:|---|
| `fixtures` | Real supplied weights, synthetic analytics | ~0ms | no |
| `epsilonphi` | Live analytics | ~97s | yes |
| `baked` | Precomputed epsilonPhi results | **~15ms** | no (unless a miss falls through) |

`baked` is the production setting. The analytics are computed once per data
version by an offline bake and served from disk — the tool is a lookup over a
closed space (68 combinations per currency × 4 hedging policies), so nothing a
PWA selects is unknown in advance. `PERFORMANCE.md` carries the profile, the
causes and the measurements.

```bash
# bake one slice (about 30 minutes: 68 portfolios at the ~27s warm cost)
PYTHONPATH=".:../../src/python" \
  python3 -m cyrus_pmg.pmgService.scenario.bake --currency USD --hedging Hedged

# everything, one process per slice
PYTHONPATH=".:../../src/python" \
  python3 -m cyrus_pmg.pmgService.scenario.bake --all --workers 4

# a store for demos and tests, no database
PYTHONPATH=. python3 -m cyrus_pmg.pmgService.scenario.bake --all --adapter fixtures

# then serve from it
SCENARIO_ADAPTER=baked ./start_dashboard.sh
```

Bakes are resumable (existing keys are skipped, slices flush every 10
portfolios) and a combination that cannot be resolved is recorded in
`manifest.json` with its error rather than aborting the run. Re-bake when
`dataversion` changes; the manifest records which slices came from where.

Re-bake too when the **payload shape** changes — a new field on a resolve
result reaches the live and fixtures adapters immediately but not the store.
Where a change touches only labels and no figure, migrating the slices in place
is quicker than an hour of recompute, provided the script asserts that every
number comes through untouched: that is how the D25 premia regrouping and the
"Over …" horizons were applied to the 272 baked payloads.

## The wire contract

The page calls `/api/scenario/...`; the Flask proxy rewrites to
`/api/v1/scenario/...`. Reads inherit `requireAuth` from the router; writes
take `Depends(requireEditor)`. Non-2xx JSON carries top-level `error` (401
carries `loginUrl`; validation adds `field`), per spec §3.5.

| Endpoint | Notes |
|---|---|
| `GET /api/scenario/schema?currency&hedging&mandateSize` | Options, availability set (canonical key strings `Allocation\|RE\|TAA\|Risk Level`), rules (thresholds, cap, `autoSleeveCategories`), plus `capabilities` and `dataInfo` (D4). `mandateSize` applies the $20m rule; omitted (pre-mandate) applies no filter. |
| `GET /api/scenario/advisors?q&limit` | `{advisors: [{name, office, display}]}`; under 2 characters returns none. |
| `GET /api/scenario/sleeves?category&currency&hedging` | `{sleeves: [{name, products: [11-field records]}]}` |
| `POST /api/scenario` | Body `{mandate, basis}` → `{id, scenario}`. 422 `{error, field}`. |
| `GET /api/scenario/{id}` | `{id, mandate, basis, base, comparisons, sleeves}` — the rehydrate shape. 404 after expiry. |
| `PUT /api/scenario/{id}` | Any subset of `{mandate, basis, sleeves}` (deviation D2). Sleeve maps are validated; auto categories refused. |
| `POST /api/scenario/{id}/portfolio` | Body `{key, role: "base"\|"comparison"}` → `{portfolio}`; records the column. THE EXPENSIVE CALL. 422 unavailable key, 502 analytics failure. |
| `DELETE /api/scenario/{id}/portfolio/{key}` | Key URL-encoded canonical string. Idempotent; the base refuses with 422. |
| `POST /api/scenario/{id}/export` | The workbook; `Content-Disposition: attachment`. 422 while a category lacks a sleeve. |

`PortfolioResult` (the resolve payload): `key`, `keyStr`, `name`, `header`,
`categories: [{name, weightPct, assets: [{reportingName, weightPct}]}]`
(held rows only — the §2.2 blank/dash semantics are the UI's to apply),
`metrics {estimatedReturnPct, volatilityPct, sharpe}`,
`stress [{period, nominalPct, realPct}]`,
`premia [{group, horizon, label, nominalPct, realPct, kind: loss|probability}]`
— `group` and `horizon` drive the dashboard's measure blocks (D25), `label`
stays the flat form for any consumer without them. Every number passes a
finiteness guard before it leaves the adapter (spec §15.8).

## Layout

```
cyrus_pmg/
  dashboard/                    stand-ins for host files + the served page
    dashboardFrontend.py        gate · per-page routes · /api proxy (302/401/403/502/504 shapes)
    dashboardConfig.py, index.html, static/js/{accessGate,globals}.js
    proposalTool/               written by generator/build_styles.py — never edit by hand
  pmgService/
    isgPMGService.py, config.py     stand-ins (app, error contract, /health, whoami)
    dashboardRouter.py              THE TRANSPLANTED ENDPOINT BLOCK
    core/accessControl.py           stand-in for the host's auth module (same names)
    scenario/                       THE TRANSPLANTED PACKAGE — port, types, rules,
                                    payloads, store, adapters, sleeves, advisors, workbook,
                                    portfolio_weights.py (runtime copy of ../backend's)
tests/                          the safety net (rounding invariants, finiteness, JS mirror)
```
