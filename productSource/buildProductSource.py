#!/usr/bin/env python3
"""Build the stand-in product catalogue: every product a sleeve may hold.

This is the *product database* the Proposal Tool's sleeve repository selects
from, expressed as the one-table extract that database would hand over - one
row per product, one column per field:

    ProductId  Name  Ticker  AssetClass  Category  Style  Vehicle  ShareClass
    Source  Liquidity  ExposureCurrency  ProductCost  FeeGroup
    DistributionYield  MinimumInvestment

Category is the sleeve category the catalogue bands the product under (D178),
one of the library's categories or blank.

The last two are the catalogue's only figures beyond cost (D63): the
distribution yield, percent per annum as delivered, and the minimum
investment in the product's currency, blank where there is none (an
exchange-traded product). Both are placeholders here, keyed off the asset
class and the vehicle.

ProductId is the key. It is a column of its own because a ticker cannot be:
separately managed accounts and private-market programmes have none, and 38
of the 73 products here are in that position. The ids in this file are slugs
of the product names; the real catalogue will carry its own, and nothing
downstream cares what they look like as long as they are unique and stable -
sleeves reference products by them.

FeeGroup must be one of the groups the fee card prices (fees.py); the reader
rejects the extract otherwise, because a product with an unpriced group would
fail only at export, under RDR, long after it was placed in a sleeve.

THE PRODUCTS ARE STUB DATA. They are the ones the authored sleeve library
carried (deviation D9, D29): real-looking, not real. products.csv is the
authored file; this script validates it and writes products.xlsx beside it,
so both shapes of extract exist for the reader to be tried against.

products-listings.csv is the same products in the layout a product file
takes since the three levels (D218, D219, D220, D222, D224, D225): thirty-three
columns, the product's (its name headed Strategy), then the share class's
(its Name - an SMA mandate's name; UCITSCompliant Yes or No; Hedging the
proportion hedged, a percentage, with an SMA mandate's ISG tick beside it;
the cost and yield headed TotalExpenseRatio and Yield), then the listing's
(its exchange and the exchange's country, RIC, CUSIP, SEDOL and issue
currency), then its mandate code; a row for each listing,
a product's rows together in its classes' and listings' order - what the catalogue's Download the product file writes.
Two products carry the plan's worked examples: GS Global Credit Portfolio,
a Mutual Fund in four share classes dealt in five currencies, and GS Access
Treasury 0-1 ETF, two share classes, each with its name, listed four
times, each listing with its RIC; and one SMA, GS US
Focused Value SMA, in three mandates, each with a name of its own. This script
validates it too - a share class's values the same on every row of its
ISIN - and writes products-listings.xlsx beside it, for an admin who starts
a product file in Excel.
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, 'products.csv')
XLSX_PATH = os.path.join(HERE, 'products.xlsx')

COLUMNS = ['ProductId', 'Name', 'Ticker', 'AssetClass', 'Category', 'Style', 'Vehicle',
           'ShareClass', 'Source', 'Liquidity', 'ExposureCurrency', 'ProductCost', 'FeeGroup',
           'DistributionYield', 'MinimumInvestment']
COST, GROUP, YIELD, MINIMUM = (COLUMNS.index(c) for c in
                               ('ProductCost', 'FeeGroup', 'DistributionYield', 'MinimumInvestment'))

LISTINGS_CSV = os.path.join(HERE, 'products-listings.csv')
LISTINGS_XLSX = os.path.join(HERE, 'products-listings.xlsx')
PRODUCT_COLUMNS = ['ProductId', 'Strategy', 'Provider', 'Category', 'AssetClass', 'Style', 'Vehicle',
                   'Source', 'Liquidity', 'FeeGroup']
CLASS_COLUMNS = ['Name', 'ISIN', 'ShareClass', 'Income', 'Hedging', 'ClassCurrency', 'ExposureCurrency',
                 'Domicile', 'UCITSCompliant', 'Replication', 'UKReporting', 'TotalExpenseRatio',
                 'Yield', 'MinimumInvestment', 'Benchmark']
_AFTER_HEDGING = CLASS_COLUMNS.index('Hedging') + 1
#: ISG, an SMA mandate's tick, beside the hedging it qualifies (D224)
LISTINGS_COLUMNS = (PRODUCT_COLUMNS + CLASS_COLUMNS[:_AFTER_HEDGING] + ['ISG'] + CLASS_COLUMNS[_AFTER_HEDGING:]
                    + ['Exchange', 'ExchangeCountry', 'RIC', 'CUSIP', 'SEDOL', 'IssueCurrency', 'MandateCode'])
#: the cost, the yield and the minimum, as each layout heads them (D225)
FIGURES = {'ProductCost': ('ProductCost', 'DistributionYield', 'MinimumInvestment'),
           'TotalExpenseRatio': ('TotalExpenseRatio', 'Yield', 'MinimumInvestment')}


def _figures(columns):
    return FIGURES['ProductCost' if 'ProductCost' in columns else 'TotalExpenseRatio']


def readCsv(path=CSV_PATH, columns=COLUMNS):
    with open(path, newline='', encoding='utf-8') as fh:
        reader = csv.reader(fh)
        head = next(reader)
        if head != columns:
            sys.exit('{} header is {!r}, expected {!r}'.format(os.path.basename(path), head, columns))
        return [row for row in reader if row and row[0]]


def validate(rows, columns=COLUMNS, listings=False):
    """The checks a row must pass. In the listings layout a product's id
    repeats, a row for each listing, but its rows must be together."""
    costName, yieldName, _ = _figures(columns)
    cost, group, yield_, minimum = (columns.index(c) for c in
                                    (costName, 'FeeGroup', yieldName, 'MinimumInvestment'))
    ids = [row[0] for row in rows]
    if listings:
        seen = [i for n, i in enumerate(ids) if n == 0 or ids[n - 1] != i]
        dup = sorted({i for i in seen if seen.count(i) > 1})
        if dup:
            sys.exit('listings not together: {}'.format(', '.join(dup)))
        agree(rows, columns, PRODUCT_COLUMNS, 0, 'product')
        agree([row for row in rows if row[columns.index('ISIN')].strip()], columns, CLASS_COLUMNS,
              columns.index('ISIN'), 'share class')
    else:
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            sys.exit('duplicate ProductId: {}'.format(', '.join(dup)))
    for row in rows:
        if not row[1].strip():
            sys.exit('{}: empty {}'.format(row[0], columns[1]))
        try:
            float(row[cost])
        except ValueError:
            sys.exit('{}: {} {!r} is not a number'.format(row[0], costName, row[cost]))
        if not row[group].strip():
            sys.exit('{}: empty FeeGroup'.format(row[0]))
        for i, label in ((yield_, yieldName), (minimum, 'MinimumInvestment')):
            if row[i].strip():
                try:
                    if float(row[i]) < 0:
                        sys.exit('{}: {} is negative'.format(row[0], label))
                except ValueError:
                    sys.exit('{}: {} {!r} is not a number'.format(row[0], label, row[i]))


def agree(rows, columns, which, key, level):
    """The values of one level the same on every row that shares its key:
    a product's on each of its rows, a share class's on each row of its
    ISIN."""
    first = {}
    for n, row in enumerate(rows):
        values = [row[columns.index(c)] for c in which]
        was = first.setdefault(row[key], (n, values))
        for c, a, b in zip(which, was[1], values):
            if a != b:
                sys.exit('{} {}: {} is {!r} on one row and {!r} on another'.format(level, row[key], c, a, b))


def writeXlsx(rows, path=XLSX_PATH, columns=COLUMNS):
    """The rows as a workbook, one sheet named Products, the figures as
    numbers and every other cell as text; the hedging (D224) a number shown
    as a percentage, as Excel keeps one: 50% is 0.5."""
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = 'Products'
    sheet.append(columns)
    numbers = [columns.index(c) for c in _figures(columns)]
    hedging = columns.index('Hedging') if 'Hedging' in columns else None
    for row in rows:
        out = list(row)
        for i in numbers:
            out[i] = float(out[i]) if out[i].strip() else None
        if hedging is not None:
            out[hedging] = float(out[hedging].rstrip('%')) / 100 if out[hedging].strip() else None
        sheet.append(out)
        if hedging is not None and out[hedging] is not None:
            sheet.cell(row=sheet.max_row, column=hedging + 1).number_format = '0%'
    book.save(path)


def main():
    rows = readCsv()
    validate(rows)
    writeXlsx(rows)
    groups = sorted({row[GROUP] for row in rows})
    print('products.csv  {} products'.format(len(rows)))
    print('products.xlsx written')
    print('fee groups    {}'.format(', '.join(groups)))
    print('no ticker     {}'.format(sum(1 for row in rows if not row[2].strip())))

    listings = readCsv(LISTINGS_CSV, LISTINGS_COLUMNS)
    validate(listings, LISTINGS_COLUMNS, listings=True)
    writeXlsx(listings, LISTINGS_XLSX, LISTINGS_COLUMNS)
    print('products-listings.csv  {} listings of {} products'.format(
        len(listings), len({row[0] for row in listings})))
    print('products-listings.xlsx written')


if __name__ == '__main__':
    main()
