# sleeveSource — the sleeve library as a table

Sleeves live in the Proposal Tool's own database (`SCENARIO_SLEEVES_DB`, a
SQLite file; `scenario/sleeveRepo.py`), where an admin builds and maintains
them from inside the app. This folder is the tabular form of that library:

| File | What |
|---|---|
| `sleeves.csv` | One row per product-in-sleeve: `Variant, Category, Sleeve, ProductId, Weight`. 110 sleeves, 268 rows. |
| `sleeves.xlsx` | The same rows as a workbook. |
| `buildSleeveSource.py` | Validates `sleeves.csv` against the product catalogue (every product known, none listed twice, weights sum to 1) and writes `sleeves.xlsx`. |

It is used two ways:

* **Seeding.** A sleeve database that does not exist yet is created and
  filled from `SCENARIO_SLEEVES_SEED` (default: this `sleeves.csv`). Once the
  database exists the seed is never read again - the database is the library.
* **Interchange.** `python3 -m cyrus_pmg.pmgService.scenario.sleeveTools
  --export out.csv` writes the live library in this shape; `--import in.csv`
  loads one (add `--replace` to clear first). This is how a library moves
  between environments, or is reviewed as a table.

**The names are PMG's; the contents are not.** The sleeve names in every
category were supplied by the desk (D59). What each one *holds* &mdash; which
products, at what weights &mdash; is placeholder, chosen from the delivered
catalogue to suit the name and the book: an `SMA Only` sleeve holds SMAs, an
`ETFs Only` sleeve ETFs, and a sleeve offered under Irish Onshore reaches the
UCITS and ICAV feeders where one exists, which is the same sleeve name
resolving to different products under different implementation types (D29).
Replacing those holdings with PMG's own is the open item; the names are not.

Private Equity and Other Private Assets share one sleeve (D60), so their rows
carry the single category `Private Equity & Other Private Assets`.

A name that says which book it belongs to (`Funds Irish`, `US Onshore ETFs`,
anything marked ESG) is offered under that book only. The rest are offered
under all four, so every book can be completed in every category.
