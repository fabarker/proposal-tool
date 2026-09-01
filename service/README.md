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
| `SCENARIO_ADAPTER` | `fixtures` | `fixtures`, `live` or `baked` (see below) |
| `SCENARIO_BAKED_DIR` | `service/var/baked` | Where the bake store lives |
| `SCENARIO_BAKED_FALLBACK` | `1` | `0` = never fall through to live analytics (no database needed at all) |
| `SCENARIO_STORE_DIR` | system temp | Where scenario state files live |
| `SCENARIO_RETENTION_HOURS` | 24 | Scenario expiry |
| `PMG_SVC_WORKERS` | 1 | The host runs 2; engine caches are per-process |
| `SCENARIO_FIXTURES_*` | unset | Fixture failure/latency knobs — see `dashboard.env.defaults` |

Sign-in: the GSSSO stand-in lives at `/_dev_login` (the gate redirects there).
For curl, send the cookie: `curl -b kerberos=fbarker …`.

Tests (dev-side; nothing ships to the host's untested dashboard package):

```bash
cd proposal-tool/service && PYTHONPATH=. python3 -m pytest tests -q
# the bit-identity guard for the Tier 0 optimisation needs a database:
SAA_ENGINE_LIVE=1 PYTHONPATH=".:../../src/python" \
    python3 -m pytest tests/test_tier0_beta_equivalence.py -q
```

## The three adapters

| `SCENARIO_ADAPTER` | Data | Cold resolve | Database |
|---|---|---:|---|
| `fixtures` | Real supplied weights, synthetic analytics | ~0ms | no |
| `live` | Live analytics | ~97s | yes |
| `baked` | Precomputed analytics results | **~15ms** | no (unless a miss falls through) |

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
| `GET /api/scenario/schema?currency&hedging&mandateSize` | Options (including `implementationVariants`, D29, and `riskLevelLabels`, D35), availability set (canonical key strings `Allocation\|RE\|TAA\|Risk Level`), rules (thresholds, cap, `autoSleeveCategories`), plus `capabilities` and `dataInfo` (D4). `mandateSize` applies the $20m rule; omitted (pre-mandate) applies no filter. |
| `GET /api/scenario/advisors?q&limit` | `{advisors: [{name, office, display}]}`; under 2 characters returns none. |
| `GET /api/scenario/sleeves?category&variant&currency&hedging` | `{category, variant, sleeves: [{name, products: [11-field records]}]}`. `variant` is required — an absent or unknown one is 422 `{error, field}`, never a default library (D29). |
| `POST /api/scenario` | Body `{mandate, basis}` → `{id, scenario}`. 422 `{error, field}`. |
| `GET /api/scenario/{id}` | `{id, mandate, basis, base, comparisons, variant, sleeves}` — the rehydrate shape. 404 after expiry. |
| `PUT /api/scenario/{id}` | Any subset of `{mandate, basis, variant, sleeves}` (deviations D2, D29). Sleeve maps are validated against the variant in force *after* the update; auto categories refused. A variant change alone clears the sleeve map. |
| `POST /api/scenario/{id}/portfolio` | Body `{key, role: "base"\|"comparison"}` → `{portfolio}`; records the column. THE EXPENSIVE CALL. 422 unavailable key, 502 analytics failure. |
| `DELETE /api/scenario/{id}/portfolio/{key}` | Key URL-encoded canonical string. Idempotent; the base refuses with 422. |
| `POST /api/scenario/{id}/export` | The workbook; `Content-Disposition: attachment`. 422 while no variant is chosen or a category lacks a sleeve. The variant is written above the implementation sheet's header. |

## Implementation variants (D29)

Step 2 opens on a required choice of one of four variants, and no sleeve can be
attached until it is made. The variant decides which sleeves each category
offers *and* what those sleeves hold, so the same category resolves to
different products under different variants — a US Onshore book reaches US
mutual funds and SMAs, an Irish Onshore book reaches UCITS.

| Variant | Public Equity offers | Vehicles |
|---|---|---|
| PMG Multi-Asset Portfolio | Active-Passive · Passive · Concentrated Active | the full house library |
| PMG ESG | ESG Core Equity · Climate Transition | ESG-screened funds and ETFs |
| US Onshore | Active-Passive · Passive · Concentrated Active | US mutual funds, ETFs, SMAs |
| Irish Onshore | UCITS Core Equity · UCITS Passive | UCITS and ICAV feeders |

`sleeves.py` holds `BASELINE` (the Multi-Asset library, unchanged from before
variants existed) and `_VARIANT_OFFERS`, which expresses the other three as
deltas: a bare string reuses a BASELINE sleeve, a dict is a sleeve that variant
alone offers. `_assertWellFormed()` proves at import that every sleeve's
weights sum to 1 and no variant offers a name twice — a sleeve that does not
sum to 1 corrupts every printed weight in its category *without* breaking the
screen-to-workbook reconciliation, so both would agree while both were wrong.

**It is stub data.** The per-variant product mixes are illustrative, not
authored by PMG; spec open item 18 records that they must be replaced before
anyone outside sees them. Replacing `sleeves.py` behind the same three
functions is the whole of that change.

Changing variant mid-work clears every sleeve choice, client-side and in the
store, and says so in a live region. The store does it in the same write as the
variant change, so the two can never be persisted out of step.

`PortfolioResult` (the resolve payload): `key`, `keyStr`, `name`, `header`,
`categories: [{name, weightPct, assets: [{reportingName, weightPct}]}]`
(held rows only — the §2.2 blank/dash semantics are the UI's to apply),
`metrics {estimatedReturnPct, volatilityPct, sharpe}`,
`stress [{period, nominalPct, realPct}]`,
`premia [{group, horizon, label, nominalPct, realPct, kind: loss|probability}]`
— `group` and `horizon` drive the dashboard's measure blocks (D25), `label`
stays the flat form for any consumer without them. Every number passes a
finiteness guard before it leaves the adapter (spec §15.8).

## Naming, and why the key is not the label (D35, D36)

A portfolio reads **currency, risk level, allocation, exclusions** — *USD
Moderate-Aggressive Core ex TAA* — and the risk level prints as a display name.

The values behind those names could not be renamed. `Mod Agg` is the fourth
field of the canonical key, so it keys the availability set, every stored
scenario and all 272 baked payloads; changing it in the data invalidates the
bake. So `rules.RISK_LEVEL_LABELS` rides the schema as
`options.riskLevelLabels`, and the UI renders the label while submitting the
value. `portfolioHeader` prints the label too, so the server and the screen
agree.

One consequence worth knowing before an export is reviewed: **the baked
payloads still carry the old `name` and `header` strings**, and the workbook
builds its sheets from those fields rather than from the key. The screen is
correct because the page derives every name from the key, but a workbook
generated from a baked slice will show the previous form until the slices are
re-baked or migrated in place. The premia regrouping was migrated the same way
in minutes rather than an hour of recompute.

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
