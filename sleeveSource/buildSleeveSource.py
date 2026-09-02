#!/usr/bin/env python3
"""Build the stand-in sleeve extract: the library as a table.

The Proposal Tool keeps sleeves in its own database (sleeveRepo.py), where an
admin builds and maintains them from inside the app. This extract is how that
database is SEEDED the first time it opens, and the shape sleeves travel in
between environments (sleeveTools --export / --import):

    Variant  Category  Sleeve  ProductId  Weight

One row per product in a sleeve. Variant is the implementation type the sleeve
is offered under, Category the category it implements, Sleeve its name;
together they identify it. ProductId references the product catalogue
(productSource/) and Weight is the product's share of the sleeve as a fraction
- a sleeve's rows sum to 1.

THE SLEEVES ARE STUB DATA - the authored library of deviations D9 and D29,
written out row by row. sleeves.csv is the authored file; this script
validates it and writes sleeves.xlsx beside it.
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, 'sleeves.csv')
XLSX_PATH = os.path.join(HERE, 'sleeves.xlsx')
PRODUCTS = os.path.join(HERE, '..', 'productSource', 'products.csv')

COLUMNS = ['Variant', 'Category', 'Sleeve', 'ProductId', 'Weight']


def readCsv(path, columns):
    with open(path, newline='', encoding='utf-8') as fh:
        reader = csv.reader(fh)
        head = next(reader)
        if head[:len(columns)] != columns:
            sys.exit('{} header is {!r}, expected {!r}'.format(
                os.path.basename(path), head, columns))
        return [row for row in reader if row and row[0]]


def validate(rows):
    known = {row[0] for row in readCsv(PRODUCTS, ['ProductId'])}
    totals, seen = {}, {}
    for row in rows:
        key = (row[0], row[1], row[2])
        if row[3] not in known:
            sys.exit('{}: product {!r} is not in the catalogue'.format(' / '.join(key), row[3]))
        if row[3] in seen.setdefault(key, set()):
            sys.exit('{}: product {!r} listed twice'.format(' / '.join(key), row[3]))
        seen[key].add(row[3])
        totals[key] = totals.get(key, 0.0) + float(row[4])
    for key, total in totals.items():
        if abs(total - 1.0) > 1e-9:
            sys.exit('{}: weights sum to {}, not 1'.format(' / '.join(key), total))
    return totals


def writeXlsx(rows):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = 'Sleeves'
    sheet.append(COLUMNS)
    for row in rows:
        sheet.append(row[:4] + [float(row[4])])
    book.save(XLSX_PATH)


def main():
    rows = readCsv(CSV_PATH, COLUMNS)
    totals = validate(rows)
    writeXlsx(rows)
    variants = []
    for row in rows:
        if row[0] not in variants:
            variants.append(row[0])
    print('sleeves.csv   {} rows, {} sleeves'.format(len(rows), len(totals)))
    print('sleeves.xlsx  written')
    for variant in variants:
        count = sum(1 for key in totals if key[0] == variant)
        print('  {:<28} {} sleeves'.format(variant, count))


if __name__ == '__main__':
    main()
