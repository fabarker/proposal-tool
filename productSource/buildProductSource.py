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
takes since listings (D207) and the catalogue holding facts only (D214):
nineteen columns, a row for each listing, a product's rows in its listings'
order, no Rank columns - what the catalogue's Download the product file
writes. This script validates it too and writes products-listings.xlsx
beside it, for an admin who starts a product file in Excel.
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
LISTINGS_COLUMNS = ['ProductId', 'Name', 'Ticker', 'AssetClass', 'Category', 'Style', 'Vehicle',
                    'ShareClass', 'Source', 'Liquidity', 'ExposureCurrency', 'IssueCurrency',
                    'ProductCost', 'FeeGroup', 'DistributionYield', 'MinimumInvestment',
                    'Benchmark', 'MandateCode', 'Hedging']


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
    cost, group, yield_, minimum = (columns.index(c) for c in
                                    ('ProductCost', 'FeeGroup', 'DistributionYield', 'MinimumInvestment'))
    ids = [row[0] for row in rows]
    if listings:
        seen = [i for n, i in enumerate(ids) if n == 0 or ids[n - 1] != i]
        dup = sorted({i for i in seen if seen.count(i) > 1})
        if dup:
            sys.exit('listings not together: {}'.format(', '.join(dup)))
    else:
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            sys.exit('duplicate ProductId: {}'.format(', '.join(dup)))
    for row in rows:
        if not row[1].strip():
            sys.exit('{}: empty Name'.format(row[0]))
        try:
            float(row[cost])
        except ValueError:
            sys.exit('{}: ProductCost {!r} is not a number'.format(row[0], row[cost]))
        if not row[group].strip():
            sys.exit('{}: empty FeeGroup'.format(row[0]))
        for i, label in ((yield_, 'DistributionYield'), (minimum, 'MinimumInvestment')):
            if row[i].strip():
                try:
                    if float(row[i]) < 0:
                        sys.exit('{}: {} is negative'.format(row[0], label))
                except ValueError:
                    sys.exit('{}: {} {!r} is not a number'.format(row[0], label, row[i]))


def writeXlsx(rows, path=XLSX_PATH, columns=COLUMNS):
    """The rows as a workbook, one sheet named Products, the figures as
    numbers and every other cell as text."""
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = 'Products'
    sheet.append(columns)
    numbers = [columns.index(c) for c in ('ProductCost', 'DistributionYield', 'MinimumInvestment')]
    for row in rows:
        out = list(row)
        for i in numbers:
            out[i] = float(out[i]) if out[i].strip() else None
        sheet.append(out)
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
