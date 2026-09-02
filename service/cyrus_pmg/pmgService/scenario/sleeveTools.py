"""Manage the sleeve repository from the command line (D57).

The console inside the app is how an admin builds and maintains sleeves day
to day. This is for everything around it - what the library holds, moving it
between environments, and reviewing it as a table:

    python3 -m cyrus_pmg.pmgService.scenario.sleeveTools --census
        What the store and the catalogue hold; every broken sleeve and why;
        every product the sleeves reference that the catalogue no longer
        carries; any fixed category not holding exactly one sleeve.

    python3 -m cyrus_pmg.pmgService.scenario.sleeveTools --export out.csv
        The whole library in the interchange shape, one row per product:
        Variant, Category, Sleeve, ProductId, Weight.

    python3 -m cyrus_pmg.pmgService.scenario.sleeveTools --import in.csv [--replace]
        Load a library through the same validation a save gets. Sleeves of
        the same name are overwritten; --replace clears the store first.
        A single failing sleeve aborts the whole load.

The store is SCENARIO_SLEEVES_DB, the catalogue SCENARIO_PRODUCTS_SOURCE;
a store that does not exist yet is created and seeded from
SCENARIO_SLEEVES_SEED on the first command that opens it.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import sys

from . import products, sleeveRepo
from .types import ValidationError


def _census() -> int:
    c = sleeveRepo.census()
    store, cat = c['store'], c['catalogue']
    print('store      {}'.format(store['path']))
    print('           {} sleeves, seeded {} from {}'.format(
        store['sleeves'], store['seededAt'] or 'never', store['seededFrom'] or 'nothing'))
    print('catalogue  {}'.format(cat['path']))
    print('           {} products, modified {}'.format(cat['products'], cat['modified']))
    print('types')
    for variant in sleeveRepo.VARIANTS:
        entry = c['byVariant'].get(variant, {'sleeves': 0, 'broken': 0})
        print('  {:<28} {:>3} sleeves{}'.format(
            variant, entry['sleeves'],
            '   {} broken'.format(entry['broken']) if entry['broken'] else ''))
    print('categories {}'.format(', '.join(sleeveRepo.categories())))
    print('fixed      {}'.format(', '.join(sleeveRepo.fixedCategories())))
    if c['broken']:
        print('\nBROKEN ({}):'.format(len(c['broken'])))
        for e in c['broken']:
            print('  {} / {} / {}'.format(e['variant'], e['category'], e['name']))
            for problem in e['problems']:
                print('      {}'.format(problem))
    if c['orphans']:
        print('\nNOT IN THE CATALOGUE ({}):'.format(len(c['orphans'])))
        for o in c['orphans']:
            print('  {}  referenced by {}'.format(
                o['productId'], ', '.join('{} / {} / {}'.format(
                    x['variant'], x['category'], x['name']) for x in o['sleeves'])))
    if c['fixedCategoryProblems']:
        print('\nFIXED CATEGORIES:')
        for line in c['fixedCategoryProblems']:
            print('  {}'.format(line))
    return 1 if (c['broken'] or c['fixedCategoryProblems']) else 0


def _export(path: str) -> int:
    rows = sleeveRepo.exportRows()
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(sleeveRepo.SEED_COLUMNS)
        writer.writerows(rows)
    print('wrote {} rows ({} sleeves) to {}'.format(
        len(rows), len({r[:3] for r in rows}), path))
    return 0


def _import(path: str, replace: bool) -> int:
    try:
        rows = list(sleeveRepo.readSeedRows(path))
    except (ValueError, OSError) as exc:
        print('cannot read {}: {}'.format(path, exc))
        return 2
    try:
        count = sleeveRepo.importRows(rows, replace=replace, user=getpass.getuser())
    except ValidationError as exc:
        print('REFUSED ({}): {}'.format(exc.field, exc.message))
        print('nothing was written')
        return 1
    print('{} {} sleeves from {}'.format('replaced the library with' if replace else 'loaded',
                                        count, path))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--census', action='store_true')
    ap.add_argument('--export', metavar='CSV')
    ap.add_argument('--import', metavar='CSV', dest='importPath')
    ap.add_argument('--replace', action='store_true')
    args = ap.parse_args(argv)
    if args.export:
        return _export(args.export)
    if args.importPath:
        return _import(args.importPath, args.replace)
    if args.census:
        return _census()
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
