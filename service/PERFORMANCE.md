# Analytics performance — profile, causes, and what was done

The brief asked for the number ("`resolve_portfolio` is slow. Nobody has said
how slow is unacceptable. Measure it and record the number"). This is that
record: what a portfolio cost, why, and what it costs now.

## Where it stood

A cold single portfolio: **~255s**. Profiled per phase with `cProfile`
(`CAppConfig.setup()` excluded from the 255s; it adds ~9–14s once per process).

| Phase | Cold, 1st portfolio | Warm process, 2nd portfolio |
|---|---:|---:|
| `get_context('USD')` | 1.0s | — (cached) |
| Portfolio build | 20.4s | 0.01s |
| `get_total_return` | 66.0s | 2.6s |
| `get_risk` + `get_sharpe_ratio` | 8.9s | 1.6s |
| `get_factor_stress_tests` | 132.1s | 13.0s |
| `get_portfolio_var_pol` | 27.5s | 12.0s |
| **Total** | **~255s** | **~29s** |

**168s of the 255s was one function**, `AssetReturnEstimator.calc_return_betas`
(62s through returns, 106s through the extended-window stress panel). It runs a
rolling 60-observation OLS — about 360 windows per asset — and inside every
iteration re-orthogonalised the factor window (6,810 `sklearn` fits in the
returns phase alone). The rest was database I/O: ~20s loading time series on
the build, ~14s of interest-rate curves inside the VaR path.

## The four causes

1. **Asset-independent work inside a per-asset loop.** `orthogonalize_columns`
   depends only on the factor window and the model — not the asset — yet ran
   for all 19 assets, both window sets, every currency and every hedging
   policy.
2. **Results computed and discarded.** `orth_X`'s only consumer was
   `np.std(orth_X, ddof=1)`; the regression used the *un*-orthogonalised
   `X_prime / stdev`. And `asset.convert_asset_to_currency(...)` — a real FX
   computation — was used only to reach `.schema`, which is the *same object*
   as `asset.schema` (verified by identity).
3. **Cache lifetime and cache key.** `AssetReturnEstimator._cache` is
   process-level and in memory, so every process start paid the full bill;
   and its key carried `hedging_ratio`, which the computation never used
   (so each hedging policy recomputed identical betas) while omitting the
   schema, whose currency and window the inputs do depend on.
4. **A per-portfolio floor.** Even fully warm, each portfolio costs ~27s:
   the stressed returns panel is rebuilt per portfolio (~10s), `get_sigma`
   (~3s), bootstrap paths (~6s), simulation. Asset-level caching cannot
   touch this — only precomputation can.

## Tier 0 — analytics-library fixes (bit-identical)

In the analytics library's `core/estimator/assetReturnEstimator.py`: memoise the
per-window normalisation statistic in a shared `_window_cache`; drop the
discarded conversion; key the beta cache on what the computation reads
(`name, currency, start, end, normalized, model hash`) instead of on
`hedging_ratio`.

Guarded by `tests/test_tier0_beta_equivalence.py`, which re-implements the
original function verbatim and asserts equality with `array_equal`, plus the
two premises (betas invariant to `hedging_ratio`; the conversion preserves the
schema object). Live output is unchanged to the digit: USD Core Mod
`ret=5.4881% vol=6.5348%` before and after.

| | Before | After Tier 0 |
|---|---:|---:|
| Cold first portfolio | 255s | **96.6s** |
| Second portfolio, same basis | 29s | 26.7s |
| Switching hedging policy | 66s | **27.8s** |
| Beta computation per asset | 2.61s | **0.74s** (0.67s once windows are warm) |

## Tier 1 — bake and serve

The tool is a lookup over a closed space: 68 UI-available combinations per
currency × 4 hedging policies. `bake.py` computes them once per data version
into one JSON slice per (currency, hedging); `bakedAdapter.py` serves them.

**Baked and measured:** all 272 USD portfolios (4 hedging slices × 68), four
worker processes, **2,148s wall (~36 min), zero failures**, 240KB per slice —
about 1MB for the entire USD universe.

Served from a *fresh process* with `SCENARIO_BAKED_FALLBACK=0`, so nothing is
warm and no database exists — measured end to end over the Flask proxy and
FastAPI, not in-process:

| | Live analytics | Baked |
|---|---:|---:|
| First portfolio in a cold process | 96,600ms | **14.8ms** |
| Remaining 67 in the slice | ~27,000ms each | **7.9ms median** (max 9.3) |
| First portfolio of another hedging slice | ~27,800ms | **~14ms** |
| First `get_schema` | 465ms | **30.6ms** |
| Database needed to serve | yes | **no** |

The numbers are identical to the live path, digit for digit: USD Core Mod
`ret=5.4881% vol=6.5348% sharpe=0.4573` either way.

Two smaller fixes fell out of measuring this:

* `rules.availability` rebuilt its pandas filter on every `get_schema`
  (~10ms a call, and the schema is refetched on every basis or mandate
  change). Now memoised per currency.
* The 465ms first `get_schema` was the supplied weight universe (6,384 rows)
  building on first touch. The baked adapter now warms the static tables on a
  daemon thread, and `dashboardRouter` constructs the port at import, so that
  cost overlaps service start instead of landing on the first user.

A visible consequence in the UI: the 200ms skeleton threshold is never
reached, so skeletons no longer appear at all — `columns[].skel` stays
`false` through a four-portfolio build. Spec §10.1's design holds; the case
it was written for has simply stopped happening.

Re-bake when `dataversion` changes:

```bash
PYTHONPATH=".:../../src/python" \
  python3 -m cyrus_pmg.pmgService.scenario.bake --all --workers 4
```

Bakes are resumable, flush every 10 portfolios, and record per-combination
failures in `manifest.json` rather than aborting.

## Two findings for the owners

* **`hedging_ratio` has never affected return betas** (verified identical at
  0.0, 0.5 and 1.0) because the converted asset was discarded. If betas are
  meant to vary with the hedge, that is a model change — and the cache key
  must regain the ratio in the same commit. Hedging does still reach risk and
  VaR through the risk estimator, so hedged/unhedged volatility differs
  (6.5348% vs 6.7232% on USD Core Mod).
* **This database carries currency configs for USD and GBP only** at
  dataversion 1. CHF and EUR raise
  `'odict_values' object has no attribute 'risk_free_rate'` — `get_config`
  returns *all* values as its default when a key is missing, so an absent row
  becomes an `AttributeError` deep in the call stack rather than a clear
  "no config for CHF". The UI offers all four currencies because the supplied
  weights carry all four; on this database two of them cannot resolve. Product
  decision: restrict the currency list, or load the missing config rows.
