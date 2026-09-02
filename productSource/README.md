# productSource — the stand-in product catalogue

The *product database* the sleeve repository selects from, as the one-table
extract that database hands over: one row per product, one column per field.

| File | What |
|---|---|
| `products.csv` | The authored extract, 73 products, thirteen columns. **This is the one the service reads by default.** |
| `products.xlsx` | The same rows as a workbook, so the XLSX path of the reader is exercised too. |
| `buildProductSource.py` | Validates `products.csv` (unique ids, numeric cost, a fee group on every row) and writes `products.xlsx`. |

Point the service elsewhere with `SCENARIO_PRODUCTS_SOURCE` (CSV or XLSX). The
reader (`scenario/products.py`) rejects an extract with a duplicate id, an
empty name, a non-numeric cost or a fee group the fee card does not price.

`DistributionYield` (percent per annum) and `MinimumInvestment` (the product's currency, blank where there is none) are the catalogue's only figures beyond cost (D63); both are placeholders here.

`ProductId` is the key sleeves reference. Tickers cannot be: 38 of these
products - the SMAs and private-market programmes - have none.

**Stub data.** The products are the ones the authored sleeve library carried
(D9, D29). The ids are slugs of the names; a real catalogue brings its own.
