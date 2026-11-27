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
    Name  ISIN  ShareClass  Income  Hedging  ISG  ClassCurrency
    ExposureCurrency  Domicile  UCITSCompliant  Replication  UKReporting
    TotalExpenseRatio  Yield  MinimumInvestment  Benchmark
    Exchange  ExchangeCountry  RIC  CUSIP  SEDOL  IssueCurrency  MandateCode

A product file has three levels (D218), one row per listing, in the
thirty-three columns of the desk's product data sheet (D225):

- **The product** - the first ten columns - is the same on every one of its
  rows. A `ProductId` is a fund in one vehicle, and `Strategy` its name. `Vehicle` is `ETF`, `Mutual Fund` or
  `SMA`, and picks the product's card.
- **The share class** - `Name` to `Benchmark` - is the same on every row of
  its ISIN. `Name` is the class's name as the fund names it, blank for the
  strategy's (D225). `ShareClass` is the class letter (I, A, P...), on a Mutual Fund
  only; `Income` is Distributing or Accumulating. `Hedging` is the
  proportion hedged, a percentage from 0 to 100 (D224): `100%` fully
  hedged, `0%` unhedged, `50%` half, blank for not said; it may be typed
  with or without the sign, and a cell Excel formats as a percentage is
  read as it shows. `ISG` is `Yes` for a class ISG hedges, else blank
  (D226): a foreign currency bond fund's class hedged 100% by ISG is
  `100%` and `Yes`. `ClassCurrency` is the class's currency, the one it is
  priced in; a sheet may head it `Pricing Currency` (D226).
  `ExposureCurrency` is the exposure after hedging: a
  class 100% hedged to GBP gives GBP. `Domicile` is the country's name,
  stored as `Luxembourg` whether the sheet says `LUXEMBOURG`, `Luxembourg`
  or `LU` (D227); a name that is no country is refused. `UCITSCompliant` and `UKReporting` are each
  Yes, No or blank. `TotalExpenseRatio` (the cost), `Yield` and the
  minimum belong to the class. A class with no ISIN yet is the set of a product's rows that agree
  on all of these.
- **The listing** - `Exchange` (free text) and `ExchangeCountry` (the
  country's name, read from a name or a code as `Domicile` is, D228), `RIC`, `CUSIP` and `SEDOL` (what the listing
  trades under there), `IssueCurrency` and `MandateCode` - is where and in
  what the class is bought, and the code an account holding it is opened
  under (D225). There is no ticker (D220). The CUSIP is the desk's own,
  letters and digits of any length (D225); a SEDOL is six letters or digits
  and a check digit; a RIC is one word, such as `CSPX.L`. An ISIN, a RIC,
  a CUSIP, a SEDOL or a mandate code belongs to one product. A class that is
  not exchange-listed has
  one listing per currency it is dealt in, with no exchange. Within a class
  two rows the same in exchange and issue currency are refused.
- **A separately managed account** keeps a row per mandate: each mandate is
  a variant of the strategy, with its `MandateCode`, its own `Name`
  (blank for the strategy's name, D222, D225), issue currency, hedging, cost,
  yield and minimum, and `ISG` - `Yes` for a mandate ISG hedges, else
  blank (D224); the card lists them under Mandates. The currency a mandate
  is run in is its `IssueCurrency`, or its `ClassCurrency` (`Pricing
  Currency`) where a sheet gives it there (D226). It has no ISIN,
  class letter, domicile, UCITS compliance, replication, UK reporting,
  exchange or its country, RIC, CUSIP or SEDOL.
  Its mandates may each give their own `AssetClass` - a strategy's EUR,
  USD and GBP mandates EUR, USD and GBP Fixed Income (D230); a fund's rows
  all give the product's. A proposal holds an SMA's first mandate in its
  base currency, else its first (D230).

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
in sterling: four rows, each with a SEDOL, a RIC and its exchange's
country, and each class named. **GS US Focused Value SMA** is
a Public Equity SMA in three mandates - 0% hedged in dollars, 100% hedged
and 50% hedged ticked ISG in sterling - each named. The ISINs, SEDOLs, codes and names are
illustrative.

**A cell that says nothing is blank** (D229): `NULL`, `N/A`, `#N/A`,
`None`, `nan`, `nil` or a lone dash, in any column; a needed column
given one is refused as missing.

**Older files are still read.** The headings before the data sheet
(D225) - `ProductCost`, `DistributionYield` and `MandateName` - are read as
`TotalExpenseRatio`, `Yield` and `Name`; a file without the new columns
keeps what the catalogue holds of them, and one without `MandateCode`
keeps each listing's code. A `Hedging` column of words (D224) reads
as the percentage: `Hedged` 100%, `Unhedged` 0%, and `ISG Hedged` 50%
ticked ISG on an SMA's mandate (a fund's class gives the percentage and
ticks ISG, D226); a file without an `ISG` column keeps each tick unless
it gives the hedging. The thirty-column layout without `ISG`, the twenty-nine-column layout without `MandateName` (each mandate keeps the name it has), the nineteen- and twenty-eight-column
listings layouts, the layout before listings (`products.csv`, or one with a
`BaseCurrency` column), a file with Rank columns and one with a
QuotationUnit column (D219: the column is not read) all import. A `Name`
column in a file with no `Strategy` column is the Strategy, as before D220. A `Ticker` shaped like an ISIN is read as the ISIN
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
