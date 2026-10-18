# productSource — the stand-in product catalogue

The *product database* the sleeve repository selects from, as the one-table
extract that database hands over: one row per product, one column per field.

**The catalogue itself now lives in the repository** (D165). This file is
what it is loaded from, once, the first time the repository opens after the
upgrade - and the shape a product file takes whenever an admin imports one
from the Catalogue's *Import a file*, or exports one with *Download the
product file*. Changing it on disk changes nothing by itself.

| File | What |
|---|---|
| `products.csv` | The authored extract, 73 products, fourteen columns. **The one the catalogue is first loaded from by default.** |
| `products.xlsx` | An older copy of the rows as a workbook, without the ShareClass column, so the XLSX path of the reader and an extract without the optional columns are both exercised. |
| `buildProductSource.py` | Validates `products.csv` (unique ids, numeric cost, a fee group on every row) and writes `products.xlsx`. |

Point the first load elsewhere with `SCENARIO_PRODUCTS_SOURCE` (CSV or XLSX).
The reader (`scenario/products.py`) rejects a file with a duplicate id, an
empty name, a non-numeric cost or a fee group the fee card does not price,
naming the row. An import also needs the six words a product is described by
- asset class, style, vehicle, source, liquidity, exposure currency.

`DistributionYield` (percent per annum) and `MinimumInvestment` (the product's currency, blank where there is none) are the catalogue's only figures beyond cost (D63); both are placeholders here.

`ProductId` is the key sleeves reference. Tickers cannot be: 38 of these
products - the SMAs and private-market programmes - have none.

**Stub data.** The products are the ones the authored sleeve library carried
(D9, D29). The ids are slugs of the names; a real catalogue brings its own.
