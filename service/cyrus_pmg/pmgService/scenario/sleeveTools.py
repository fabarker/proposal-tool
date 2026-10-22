"""Manage the sleeve repository from the command line (D57).

The console inside the app is how an admin builds and maintains sleeves day
to day. This is for everything around it - what the library holds, moving it
between environments, and reviewing it as a table:

    python3 -m cyrus_pmg.pmgService.scenario.sleeveTools --census
        What the store and the catalogue hold; every broken sleeve and why;
        every product the sleeves reference that the catalogue no longer
        carries; any fixed category holding two sleeves under one type; and
        the overlays a type is not given for want of a sleeve (D191).

    python3 -m cyrus_pmg.pmgService.scenario.sleeveTools --export out.csv
        The whole library in the interchange shape, one row per product:
        Variant, Category, Sleeve, Edition, ProductId, Weight, BelowMinimum
        (the product held instead below the row's minimum, D192) - and beside
        it, as out.rules.csv, every edition's rules (D89). A sleeve shared
        by several types names them all in Variant, separated by |, or All
        for every type (D179).

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
        The last N changes across the whole repository, newest first (D66,
        D167): sleeves, products, the fee card, the overlay rules and the
        funding split - who did what, and what moved. Default 40.

    python3 -m cyrus_pmg.pmgService.scenario.sleeveTools --products-export out.csv
        The product catalogue as a product file (D165): the columns the file
        has always had, then BaseCurrency - one All row per live product,
        and a row for each base currency it has values of its own in, or
        only the currencies it is offered in (D182). Edit it and import it.

    python3 -m cyrus_pmg.pmgService.scenario.sleeveTools --products-import in.csv [--withdraw-missing] [--note TEXT]
        Load a product file (CSV or XLSX) through the same checks the console
        gives a product, after printing what it would do. New products are
        added and changed ones updated; ones the file leaves out are kept,
        or withdrawn with --withdraw-missing - except any a sleeve holds,
        which refuses the whole load. A single bad row refuses it too.

    python3 -m cyrus_pmg.pmgService.scenario.sleeveTools --product-history ID
        Every version of one product, oldest first: who changed it, when,
        why, and what moved.

The store is SCENARIO_SLEEVES_DB. The catalogue and the fee card are kept in
it (D165, D166). A store that does not exist yet is created and seeded on
the first command that opens it: the fee card from SCENARIO_FEES_SOURCE, the
catalogue from SCENARIO_PRODUCTS_SOURCE, the sleeves from
SCENARIO_SLEEVES_SEED - each once, and never read again.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import os
import sys

from . import products, repositoryFeed, sleeveRepo
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
        print('           {} labelled edition(s) with rules'.format(c['editions']))
    if c.get('shared'):
        print('           {} shared by several types, counted under each'.format(c['shared']))
    print('catalogue  in the store · version {} · {} products{}'.format(
        cat['revision'], cat['products'],
        ' · {} withdrawn'.format(cat['withdrawn']) if cat['withdrawn'] else ''))
    print('           last edited {} by {} · loaded {} from {}'.format(
        (cat['lastEditedAt'] or '').replace('T', ' '), cat['lastEditedBy'] or 'unknown',
        (cat['seededAt'] or 'never')[:10], os.path.basename(cat['seededFrom'] or '') or 'nothing'))
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
    if c.get('overlaysWithheld'):
        # not a problem either: a type with no sleeve in an overlay's
        # category does not offer that overlay
        print('\nOVERLAYS NOT APPLIED:')
        for line in c['overlaysWithheld']:
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
    page = repositoryFeed.activity(limit=limit)
    if not page['entries']:
        print('nothing on record')
        return 0
    print('{} of {} change(s), newest first\n'.format(len(page['entries']), page['total']))
    day = None
    for e in page['entries']:
        if e['at'][:10] != day:
            day = e['at'][:10]
            print('  {}'.format(day))
        if e['area'] == 'sleeves':
            what = '{} / {} / {}  r{}'.format(e['variant'], e['category'], e['name'], e['revision'])
        else:
            what = '{}: {}{}'.format(repositoryFeed.AREA_LABELS[e['area']], e['name'],
                                     '  r{}'.format(e['revision']) if e.get('revision') else '')
        print('    {}  {:<9} {:<14} {}'.format(
            e['at'][11:16], e['action'], (e['actor'] or 'unknown')[:14], what))
        for change in e['changes']:
            print('             - {}'.format(change))
        if e.get('note') and e['area'] != 'sleeves':
            print('             "{}"'.format(e['note']))
    if page['next']:
        print('\n(more: raise the limit)')
    return 0


def _productsExport(path: str) -> int:
    rows = products.exportRows()
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        csv.writer(fh).writerows(rows)
    print('wrote {} product(s) to {}'.format(len(rows) - 1, path))
    return 0


def _productsImport(path: str, withdrawMissing: bool, note: str) -> int:
    import base64
    try:
        with open(path, 'rb') as fh:
            content = base64.b64encode(fh.read()).decode('ascii')
        preview = products.importPreview(path, content)
    except (OSError, ValidationError, products.BadCatalogue) as exc:
        print('REFUSED: {}'.format(getattr(exc, 'message', exc)))
        print('nothing was written')
        return 1
    print('{}: {} product(s)'.format(preview['file'], preview['rows']))
    print('  {} new, {} changed, {} back from withdrawn, {} unchanged, {} not in the file'.format(
        len(preview['added']), len(preview['changed']), len(preview['restored']),
        preview['unchanged'], len(preview['missing'])))
    for item in preview['changed'] + preview['restored']:
        print('  {}  {}'.format(item['productId'], '; '.join(item['changeWords']) or 'back'))
    withdraw = [m['productId'] for m in preview['missing']] if withdrawMissing else []
    try:
        done = products.importFile(path, content, user=getpass.getuser(), withdraw=withdraw,
                                   note=note or '', base=preview['revision'])
    except ValidationError as exc:
        print('REFUSED ({}): {}'.format(exc.field, exc.message))
        print('nothing was written')
        return 1
    print('imported: {added} added, {changed} changed, {restored} restored, {withdrawn} withdrawn '
          '- the catalogue is now version {revision}'.format(**done))
    return 0


def _productHistory(productId: str) -> int:
    trail = products.history(productId)
    if not trail:
        print('no product {} on record'.format(productId))
        return 2
    print('product {}  {}'.format(productId, trail[0]['product']['name']))
    print('{} version(s), oldest first\n'.format(len(trail)))
    for entry in reversed(trail):
        print('  v{:<3} {:<9} {:<19} {}{}'.format(
            entry['revision'], entry['action'], entry['at'], entry['actor'] or 'unknown',
            '   <- in force' if entry['current'] else ''))
        for line in entry['changeWords']:
            print('       - {}'.format(line))
        if entry['note']:
            print('       "{}"'.format(entry['note']))
        if entry['source']:
            print('       from {}'.format(entry['source']))
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
        writer.writerow(sleeveRepo.SEED_COLUMNS_EDITIONS + [sleeveRepo.ALTERNATIVE_COLUMN])
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
            writer.writerow(sleeveRepo.RULE_COLUMNS + [sleeveRepo.ENTITY_COLUMN])
            writer.writerows(ruleRows)
        print('wrote {} rule(s) to {}'.format(len(ruleRows), rulesFile))
    return 0


def _import(path: str, replace: bool) -> int:
    try:
        rows = list(sleeveRepo.readSeedRows(path))
        rulesFile = path if sleeveRepo.workbookRules(path) else _rulesFileFor(path)
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
    ap.add_argument('--products-export', metavar='CSV', dest='productsExport')
    ap.add_argument('--products-import', metavar='FILE', dest='productsImport')
    ap.add_argument('--withdraw-missing', action='store_true', dest='withdrawMissing')
    ap.add_argument('--note', default='')
    ap.add_argument('--product-history', metavar='PRODUCTID', dest='productHistory')
    args = ap.parse_args(argv)
    if args.productsExport:
        return _productsExport(args.productsExport)
    if args.productsImport:
        return _productsImport(args.productsImport, args.withdrawMissing, args.note)
    if args.productHistory:
        return _productHistory(args.productHistory)
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
