# productSource — the stand-in product catalogue

The *product database* the sleeve repository selects from, as the one-table
extract that database hands over: one row per listing, one column per field.

**The catalogue itself now lives in the repository** (D165). These files are
what it is loaded from, once, the first time the repository opens after the
upgrade - and the shape a product file takes whenever an admin imports one
from the Catalogue's *Import a file*, or exports one with *Download the
product file*. Changing them on disk changes nothing by itself.

| File | What |
|---|---|
| `products-listings.csv` | **The layout to start a product file from.** The same 73 products in nineteen columns, a row for each listing, no Rank columns - what *Download the product file* writes. |
| `products-listings.xlsx` | The same rows as a workbook (one sheet, *Products*), for a product file started in Excel. The catalogue reads it exactly as it reads the CSV. |
| `products.csv` | The authored extract in the layout before listings, 73 products, fifteen columns. **The one the catalogue is first loaded from by default.** |
| `products.xlsx` | `products.csv`'s rows as a workbook, so the XLSX path of the reader and an extract without the optional columns are both exercised. |
| `buildProductSource.py` | Validates `products.csv` and `products-listings.csv` (an id, a name, a numeric cost and a fee group on every row; a product's listings together) and writes the two workbooks. |
| `map-benchmarks-placeholders.csv` | Placeholder rows for MAP Benchmarks (Account Opening): Benchmark, Currency, Risk, Hedging, Mnemonic. |

Point the first load elsewhere with `SCENARIO_PRODUCTS_SOURCE` (CSV or XLSX).

## The listings layout

    ProductId  Name  Ticker  AssetClass  Category  Style  Vehicle  ShareClass
    Source  Liquidity  ExposureCurrency  IssueCurrency  ProductCost  FeeGroup
    DistributionYield  MinimumInvestment  Benchmark  MandateCode  Hedging

A product has **listings** (D207): the ways it can be bought - a share class
of a fund, a mandate code of a separate account. A product with several
gives a row to each under the same `ProductId`, together and in their order.
The catalogue holds facts only (D214): a proposal holds a product's **first**
listing, and the order is set afterwards by dragging the listings on the
product's card. There are no rankings by base currency any more; a file that
still has Rank columns is read without them, and the import's preview says
so.

What the product is - `ProductId`, `Name`, `Category`, `Style`, `Vehicle`,
`Source`, `Liquidity`, `FeeGroup` - is the same on each of its rows. The rest
belong to the listing: `Ticker`, `IssueCurrency`, `Hedging`, `ShareClass`,
`ExposureCurrency`, `AssetClass`, `ProductCost`, `DistributionYield`,
`MinimumInvestment`, `Benchmark`, and, for a separately managed account, its
`MandateCode`.

A file in the layout before listings (`products.csv`, or one with a
`BaseCurrency` column) is still read.

## The checks

The reader (`scenario/products.py`) rejects a file with a duplicate id, an
empty name, a non-numeric cost or a fee group the fee card does not price,
naming the row. An import also needs the six words a product is described by
- asset class, style, vehicle, source, liquidity, exposure currency.

`DistributionYield` (percent per annum) and `MinimumInvestment` (in dollars,
blank where there is none) are the catalogue's only figures beyond cost
(D63, D183); both are placeholders here.

`ProductId` is the key sleeves reference. Tickers cannot be: 38 of these
products - the SMAs and private-market programmes - have none.

**Stub data.** The products are the ones the authored sleeve library carried
(D9, D29). The ids are slugs of the names; a real catalogue brings its own.
In the listings files each product has one listing, and IssueCurrency,
Benchmark, MandateCode and Hedging are blank.
