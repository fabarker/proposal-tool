# Data delivery templates

`ProposalTool_DataTemplates.xlsx` is the workbook to hand to whoever supplies the
Proposal Tool's data on the Cyrus side. One sheet per file the service reads,
each a literal table to fill in:

| Sheet | Becomes | Pointed at by |
|---|---|---|
| `saaPortfolios` | `saaPortfolios.csv` | `SCENARIO_SAA_SOURCE` |
| `products` | `products.csv` | `SCENARIO_PRODUCTS_SOURCE` |
| `sleeves` | `sleeves.csv` | `SCENARIO_SLEEVES_SEED` |
| `sleeveRules` | `sleeveRules.csv` | `SCENARIO_SLEEVES_RULES` (optional; else beside the seed) |
| `feeRates` | `feeRates.csv` | `SCENARIO_FEES_SOURCE` |
| `advisors` | `advisors.xlsx` | replaces the packaged file (no override) |
| `assetEstimates` | `assetEstimates.json` | `SCENARIO_ASSET_ESTIMATES` |

Three sheets carry no data to fill in: **Read me** (what goes where, and the
three census commands that check it), **Column reference** (every column with
its type and rule), and **Valid values** (the closed vocabularies, which the
fill-in sheets use as dropdowns). **portfolioAnalytics** documents the bake -
688 payloads across sixteen JSON files, produced by `bake.py`, not typed.

Each header cell carries a note with that column's rule, and rows tinted amber
are examples from the packaged stand-ins, to be deleted before delivery.

## Rebuilding it

```bash
cd proposal-tool/service
PYTHONPATH=. python3 ../dataTemplates/build_templates.py
```

The builder reads the columns, the vocabularies and the examples out of the
scenario package, so **rebuild it whenever a reader changes** rather than
editing the workbook by hand. The same contract is written out in prose in
`PORTING.md` Appendix C; keep the two together.

Units are the easiest thing to get wrong and differ by file: weights are
fractions, product costs and fee rates are percents, minimum investments and
tier edges are currency units, and the asset estimates are fractions again.
Every one is stated on the header note and in the column reference.
