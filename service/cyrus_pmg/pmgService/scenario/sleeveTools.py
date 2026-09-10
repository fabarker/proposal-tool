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
        Variant, Category, Sleeve, Edition, ProductId, Weight - and beside
        it, as out.rules.csv, every edition's rules (D89).

    python3 -m cyrus_pmg.pmgService.scenario.sleeveTools --import in.csv [--replace]
        Load a library through the same validation a save gets. Editions of
        the same name and label are overwritten; --replace retires the ones
        already there rather than erasing them. A single failing sleeve
        aborts the whole load. Rules are read from in.rules.csv, or from
        sleeveRules.csv beside the file, when either exists.

    python3 -m cyrus_pmg.pmgService.scenario.sleeveTools --history 42
        Every revision of one sleeve, oldest first: what it was called, what
        it held, who changed it and what moved. Works for a sleeve that has
        been removed from the library - that is the point of keeping it.

    python3 -m cyrus_pmg.pmgService.scenario.sleeveTools --archived
        The sleeves archived out of the library, with who did it and when.

    python3 -m cyrus_pmg.pmgService.scenario.sleeveTools --activity [N]
        The last N changes across the whole library, newest first (D66):
        who did what to which sleeve, and what moved. Default 40.

The store is SCENARIO_SLEEVES_DB, the catalogue SCENARIO_PRODUCTS_SOURCE;
a store that does not exist yet is created and seeded from
SCENARIO_SLEEVES_SEED on the first command that opens it.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import os
import sys

from . import products, sleeveRepo
from .types import ValidationError


def _census() -> int:
    c = sleeveRepo.census()
    store, cat = c['store'], c['catalogue']
    print('store      {}'.format(store['path']))
    print('           {} sleeves, seeded {} from {}'.format(
        store['sleeves'], store['seededAt'] or 'never', store['seededFrom'] or 'nothing'))
    print('           schema v{} · {} revisions on record · {} sleeve(s) archived'.format(
        store.get('schemaVersion', 1), store.get('revisions', 0), store.get('archived', 0)))
    if c.get('editions'):
        print('           {} labelled edition(s) with rules (D89)'.format(c['editions']))
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
    if c.get('archived'):
        # not a problem, so not counted as one - an archived sleeve is a normal
        # thing for a library to hold, and the census is where you see it
        print('\nARCHIVED ({}):'.format(len(c['archived'])))
        for e in c['archived']:
            print('  [{}] {} / {} / {}   by {} on {}'.format(
                e['id'], e['variant'], e['category'], e['name'],
                e['archivedBy'] or 'unknown', (e['archivedAt'] or '')[:10]))
    return 1 if (c['broken'] or c['fixedCategoryProblems']) else 0


def _history(sleeveId: int) -> int:
    trail = sleeveRepo.history(sleeveId)
    if not trail:
        print('no sleeve {} on record'.format(sleeveId))
        return 2
    top = trail[0]
    print('sleeve {}  {} / {} / {}'.format(
        sleeveId, top['variant'], top['category'], top['name']))
    print('{} revision(s), oldest first\n'.format(len(trail)))
    for entry in reversed(trail):
        print('  r{:<3} {:<9} {:<19} {}{}'.format(
            entry['revision'], entry['action'], entry['at'],
            entry['actor'] or 'unknown', '   <- in force' if entry['current'] else ''))
        print('       {}'.format(entry['name']))
        for row in entry['products']:
            label = row['product']['name'] if row['product'] else row['productId'] + '  (not in the catalogue)'
            print('         {:>6.2f}%  {}'.format(row['weight'] * 100.0, label))
        for change in entry['changes']:
            print('       - {}'.format(change))
        print('')
    return 0


def _archived() -> int:
    rows = sleeveRepo.listArchived()
    if not rows:
        print('nothing is archived')
        return 0
    print('{} sleeve(s) archived, still on the record:\n'.format(len(rows)))
    for e in rows:
        print('  [{}] {:<28} {:<32} {}'.format(e['id'], e['variant'], e['category'], e['name']))
        print('       archived by {} on {} · {} revision(s)'.format(
            e['archivedBy'] or 'unknown', (e['archivedAt'] or '')[:19], e['revisions']))
    print('\nrestore from the console\'s Archive, or with --import after an --export edit')
    return 0


def _activity(limit: int) -> int:
    page = sleeveRepo.activity(limit=limit)
    if not page['entries']:
        print('nothing on record')
        return 0
    print('{} of {} change(s), newest first\n'.format(len(page['entries']), page['total']))
    day = None
    for e in page['entries']:
        if e['at'][:10] != day:
            day = e['at'][:10]
            print('  {}'.format(day))
        print('    {}  {:<9} {:<14} {} / {} / {}  r{}'.format(
            e['at'][11:16], e['action'], (e['actor'] or 'unknown')[:14],
            e['variant'], e['category'], e['name'], e['revision']))
        for change in e['changes']:
            print('             - {}'.format(change))
    if page['next']:
        print('\n(more: raise the limit)')
    return 0


def _rulesFileFor(path: str) -> str:
    """in.csv -> in.rules.csv; falling back to sleeveRules.csv beside it."""
    base, ext = os.path.splitext(path)
    sibling = base + '.rules' + (ext or '.csv')
    if os.path.exists(sibling):
        return sibling
    return os.path.join(os.path.dirname(os.path.abspath(path)), 'sleeveRules.csv')


def _export(path: str) -> int:
    rows = sleeveRepo.exportRows()
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(sleeveRepo.SEED_COLUMNS_EDITIONS)
        writer.writerows(rows)
    print('wrote {} rows ({} editions) to {}'.format(
        len(rows), len({r[:4] for r in rows}), path))
    ruleRows = sleeveRepo.exportRuleRows()
    # always the sibling name on export; the fallback to sleeveRules.csv
    # beside the file is for reading a delivery, not for writing one
    base, ext = os.path.splitext(path)
    rulesFile = base + '.rules' + (ext or '.csv')
    if ruleRows or os.path.exists(rulesFile):
        with open(rulesFile, 'w', newline='', encoding='utf-8') as fh:
            writer = csv.writer(fh)
            writer.writerow(sleeveRepo.RULE_COLUMNS)
            writer.writerows(ruleRows)
        print('wrote {} rule(s) to {}'.format(len(ruleRows), rulesFile))
    return 0


def _import(path: str, replace: bool) -> int:
    try:
        rows = list(sleeveRepo.readSeedRows(path))
        rulesFile = _rulesFileFor(path)
        ruleRows = list(sleeveRepo.readRuleRows(rulesFile)) if os.path.exists(rulesFile) else []
    except (ValueError, OSError) as exc:
        print('cannot read {}: {}'.format(path, exc))
        return 2
    try:
        count = sleeveRepo.importRows(rows, replace=replace, user=getpass.getuser(),
                                      ruleRows=ruleRows)
    except ValidationError as exc:
        print('REFUSED ({}): {}'.format(exc.field, exc.message))
        print('nothing was written')
        return 1
    print('{} {} editions from {}{}'.format(
        'replaced the library with' if replace else 'loaded', count, path,
        ' (with {} rule(s))'.format(len(ruleRows)) if ruleRows else ''))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--census', action='store_true')
    ap.add_argument('--export', metavar='CSV')
    ap.add_argument('--import', metavar='CSV', dest='importPath')
    ap.add_argument('--replace', action='store_true')
    ap.add_argument('--history', metavar='SLEEVEID', type=int)
    ap.add_argument('--archived', '--deleted', action='store_true', dest='archived')
    ap.add_argument('--activity', metavar='N', type=int, nargs='?', const=40)
    args = ap.parse_args(argv)
    if args.history is not None:
        return _history(args.history)
    if args.archived:
        return _archived()
    if args.activity is not None:
        return _activity(args.activity)
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
