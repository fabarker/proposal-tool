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
| `products-listings.csv` | **The layout to start a product file from.** The same 73 products in twenty-nine columns, a row for each listing, with the two worked examples - what *Download the product file* writes. |
| `products-listings.xlsx` | The same rows as a workbook (one sheet, *Products*), for a product file started in Excel. The catalogue reads it exactly as it reads the CSV. |
| `products.csv` | The authored extract in the layout before listings, 73 products, fifteen columns. **The one the catalogue is first loaded from by default.** |
| `products.xlsx` | `products.csv`'s rows as a workbook, so the XLSX path of the reader and an extract without the optional columns are both exercised. |
| `buildProductSource.py` | Validates `products.csv` and `products-listings.csv` (an id, a name, a numeric cost and a fee group on every row; a product's listings together; a product's values the same on each of its rows, a share class's on each row of its ISIN) and writes the two workbooks. |
| `map-benchmarks-placeholders.csv` | Placeholder rows for MAP Benchmarks (Account Opening): Benchmark, Currency, Risk, Hedging, Mnemonic. |

Point the first load elsewhere with `SCENARIO_PRODUCTS_SOURCE` (CSV or XLSX).

## The layout: product, share class, listing

    ProductId  Strategy  Provider  Category  AssetClass  Style  Vehicle  Source
    Liquidity  FeeGroup
    ISIN  ShareClass  Income  Hedging  ClassCurrency  ExposureCurrency
    Domicile  UCITSCompliant  Replication  UKReporting  ProductCost
    DistributionYield  MinimumInvestment  Benchmark
    Exchange  CUSIP  SEDOL  IssueCurrency
    MandateCode

A product file has three levels (D218), one row per listing:

- **The product** - the first ten columns - is the same on every one of its
  rows. A `ProductId` is a fund in one vehicle, and `Strategy` its name (a
  `Name` column is read as it, D220). `Vehicle` is `ETF`, `Mutual Fund` or
  `SMA`, and picks the product's card.
- **The share class** - `ISIN` to `Benchmark` - is the same on every row of
  its ISIN. `ShareClass` is the class letter (I, A, P...), on a Mutual Fund
  only; `Income` is Distributing or Accumulating. `ExposureCurrency` is the
  exposure after hedging: a class hedged to GBP gives GBP. `Domicile` is a
  two-letter country code, and `UCITSCompliant` and `UKReporting` are each
  Yes, No or blank. Cost, yield and minimum belong to the
  class. A class with no ISIN yet is the set of a product's rows that agree
  on all of these.
- **The listing** - `Exchange` (free text), `CUSIP` and `SEDOL` (what the
  listing trades under there) and `IssueCurrency` - is where and in what
  the class is bought. There is no ticker (D220). A CUSIP is eight letters
  or digits and a check digit, a SEDOL six letters or digits and a check
  digit; an ISIN, a CUSIP or a SEDOL belongs to one product. A class that is
  not exchange-listed has
  one listing per currency it is dealt in, with no exchange. Within a class
  two rows the same in exchange and issue currency are refused.
- **A separately managed account** keeps a row per `MandateCode`, each with
  its own issue currency, hedging, cost, yield and minimum; it has no ISIN,
  class letter, domicile, UCITS compliance, replication, UK reporting,
  exchange, CUSIP or SEDOL.

A product's rows sit together, its classes in order and each class's
listings in order. A proposal holds the **first listing of the first
share class**; the order is set afterwards by dragging the rows on the
product's card. Nothing beyond the old checks is required: the new columns
may be blank.

Two products carry the worked examples. **GS Global Credit Portfolio** is a
Mutual Fund in four share classes - I Acc, I Dist, I Acc hedged to GBP, A
Acc - with the first dealt in EUR and USD: five rows. **GS Access Treasury
0-1 ETF** is two classes - unhedged USD, hedged GBP - listed on the LSE in
dollars and in sterling, on Xetra in euros, and the hedged one on the LSE
in sterling: four rows, each with a SEDOL. The ISINs and SEDOLs are
illustrative.

**Older files are still read.** The nineteen- and twenty-eight-column
listings layouts, the layout before listings (`products.csv`, or one with a
`BaseCurrency` column), a file with Rank columns and one with a
QuotationUnit column (D219: the column is not read) all import. A `Name`
column is the Strategy. A `Ticker` shaped like an ISIN is read as the ISIN
where the file has no ISIN column; any other ticker is not kept (D220). A
`Regulation` column is read as `UCITSCompliant`: UCITS is Yes, AIF No,
anything else not stated. A `ShareClass` of Distributing or Accumulating
(or Dis, Dist, Acc) is read as `Income`; a `Vehicle` of UCITS or ICAV
becomes an ETF if the name says ETF, otherwise a Mutual Fund, a UCITS one's
classes UCITS compliant; Separate Account reads as SMA. The import's
preview says what it moved.

## The checks

The reader (`scenario/products.py`) rejects a file with a duplicate id, an
empty strategy, a non-numeric cost or a fee group the fee card does not price,
naming the row. An import also needs the six words a product is described by
- asset class, style, vehicle, source, liquidity, exposure currency.

`DistributionYield` (percent per annum) and `MinimumInvestment` (in dollars,
blank where there is none) are the catalogue's only figures beyond cost
(D63, D183); both are placeholders here.

`ProductId` is the key sleeves reference. An ISIN or SEDOL cannot be: the
SMAs and private-market programmes have none.

**Stub data.** The products are the ones the authored sleeve library carried
(D9, D29). The ids are slugs of the names; a real catalogue brings its own.
In the listings files every product but the two worked examples has one
listing; Provider is GSAM on the internal products and blank on the rest,
UCITSCompliant is Yes where the stub said UCITS, and the other new columns
are blank - the stub's tickers are not carried.
