#!/usr/bin/env python3
"""Build the stand-in product catalogue: every product a sleeve may hold.

This is the *product database* the Proposal Tool's sleeve repository selects
from, expressed as the one-table extract that database would hand over - one
row per product, one column per field:

    ProductId  Name  Ticker  AssetClass  Style  Vehicle  Source
    Liquidity  ExposureCurrency  ProductCost  FeeGroup
    DistributionYield  MinimumInvestment

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
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, 'products.csv')
XLSX_PATH = os.path.join(HERE, 'products.xlsx')

COLUMNS = ['ProductId', 'Name', 'Ticker', 'AssetClass', 'Style', 'Vehicle', 'Source',
           'Liquidity', 'ExposureCurrency', 'ProductCost', 'FeeGroup',
           'DistributionYield', 'MinimumInvestment']


def readCsv():
    with open(CSV_PATH, newline='', encoding='utf-8') as fh:
        reader = csv.reader(fh)
        head = next(reader)
        if head != COLUMNS:
            sys.exit('products.csv header is {!r}, expected {!r}'.format(head, COLUMNS))
        return [row for row in reader if row and row[0]]


def validate(rows):
    ids = [row[0] for row in rows]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        sys.exit('duplicate ProductId: {}'.format(', '.join(dup)))
    for row in rows:
        if not row[1].strip():
            sys.exit('{}: empty Name'.format(row[0]))
        try:
            float(row[9])
        except ValueError:
            sys.exit('{}: ProductCost {!r} is not a number'.format(row[0], row[9]))
        if not row[10].strip():
            sys.exit('{}: empty FeeGroup'.format(row[0]))
        for i, label in ((11, 'DistributionYield'), (12, 'MinimumInvestment')):
            if row[i].strip():
                try:
                    if float(row[i]) < 0:
                        sys.exit('{}: {} is negative'.format(row[0], label))
                except ValueError:
                    sys.exit('{}: {} {!r} is not a number'.format(row[0], label, row[i]))


def writeXlsx(rows):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = 'Products'
    sheet.append(COLUMNS)
    for row in rows:
        out = list(row)
        out[9] = float(out[9])
        out[11] = float(out[11]) if out[11].strip() else None
        out[12] = float(out[12]) if out[12].strip() else None
        sheet.append(out)
    book.save(XLSX_PATH)


def main():
    rows = readCsv()
    validate(rows)
    writeXlsx(rows)
    groups = sorted({row[10] for row in rows})
    print('products.csv  {} products'.format(len(rows)))
    print('products.xlsx written')
    print('fee groups    {}'.format(', '.join(groups)))
    print('no ticker     {}'.format(sum(1 for row in rows if not row[2].strip())))


if __name__ == '__main__':
    main()
