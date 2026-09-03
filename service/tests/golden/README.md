# Golden workbooks — the reference the export is held to

`engine_USD_Hedged_2col.xlsx` is a workbook the **analytics library's own
reporting object produced**, captured on 3 September 2026 while `epsilonPhi`
was still on the path, with its `assumptions` sheet intact (the export used to
throw that sheet away).

It exists because the library cannot be ported into the host, and once it is
gone this file can never be regenerated. It is the definition of what a
proposal workbook looks like.

| File | What |
|---|---|
| `engine_USD_Hedged_2col.xlsx` | the reference: `portfolios`, `risk_dashboard`, `assumptions` |
| `engine_USD_Hedged_2col.results.json` | the resolved payloads that produced it |
| `engine_USD_Hedged_2col.impl.json` | the implementation state alongside |

`test_scenario_backend.py` rebuilds those three sheets from the payloads with
no analytics library present and compares **every cell's value, number format,
font, fill, border, alignment, merge, row height and column width** plus the
conditional-formatting ranges. The comparison must come out at zero.

Do not regenerate these files. If a deliberate change to the workbook is made,
the diff the test prints is the review.

## One deliberate departure

The reference carries two rows the production export drops: *Asset Allocation
Strategies* and its *Tactical Tilt Fund*, at 0.0% against every column. The
library padded each portfolio to the whole asset universe before reporting on
it, so its workbook shows assets nobody holds; no strategic portfolio holds
the tilt fund and the supplied extract names it nowhere (D68).

The comparison therefore runs with `engineParity=True`, which restores those
rows. That keeps this file a strict, meaningful lock on every other cell —
and keeps it true that the writer can still reproduce the library's output
exactly, should anyone need to check.
