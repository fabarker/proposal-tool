"""The fee card from the command line (D55, D166): census, diff, accept, export.

The card is kept in the repository and maintained from the console's Fees
tab, where every change is reviewed and recorded. This is for everything
around it. The tool that earns its keep is the DIFF: a file a pricing team
sends is reviewed as "these eleven rates moved", rate by rate, before it is
put in force. The census says what the card in force holds; accept puts a
file in force as a new version of the card, with a note, exactly as the Fees
tab's import does; export writes the card in force as the file it can be
loaded from again.

    python3 -m cyrus_pmg.pmgService.scenario.feeTools --census
    python3 -m cyrus_pmg.pmgService.scenario.feeTools --diff new.csv
    python3 -m cyrus_pmg.pmgService.scenario.feeTools --accept new.csv \\
        --note "2027 pricing round" [--version 2026.3 --source "PMG pricing desk" --as-of 2026-09-01]
    python3 -m cyrus_pmg.pmgService.scenario.feeTools --export card.csv

Accept refuses a card the framework cannot price from - a gap, disagreeing
tier edges, a crossed band - and one that would take away a fee group a
product is priced by. The service reads the new version at once.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import sys

from . import feeCard, fees
from .types import ValidationError


def census() -> int:
    held = feeCard.current()
    info = fees.deliveryInfo(held)
    print('card       in the repository · {}'.format(info['version']))
    print('           last edited {} by {}{}'.format(
        (info['lastEditedAt'] or '').replace('T', ' '), info['lastEditedBy'] or 'unknown',
        ' · placeholder' if info['placeholder'] else ''))
    if held.get('note'):
        print('           {}'.format(held['note']))
    print('rates      {}   tiers {}   fee groups {}'.format(
        len(held['cells']), len(held['tiers']), len(held['feeGroups'])))
    for t in held['tiers']:
        print('  {:<3} {:<16} [{:,.0f}, {})'.format(
            t['id'], t['label'], t['min'], '∞' if t['max'] is None else '{:,.0f}'.format(t['max'])))
    print('groups     {}'.format(', '.join(held['feeGroups'])))
    for schedule in fees.SCHEDULES:
        groups = [None] if not fees.byGroup(schedule) else held['feeGroups']
        for g in groups:
            rates = [v for (s, gg, _, _, _), v in held['cells'].items() if s == schedule and gg == g]
            if rates:
                print('  {:<4} {:<18} {:.2f}% – {:.2f}%'.format(schedule, g or '-', min(rates), max(rates)))
    return 0


def _read(path: str):
    with open(path, 'rb') as fh:
        return base64.b64encode(fh.read()).decode('ascii')


def diff(path: str) -> int:
    """Rate by rate: the file against the card in force."""
    try:
        preview = feeCard.importPreview(path, _read(path))
    except ValidationError as exc:
        print('REFUSED: {}'.format(exc.message))
        return 1
    for line in preview['changes']:
        print('  {}'.format(line))
    if preview['blocked']:
        for b in preview['blocked']:
            print('  CANNOT REMOVE {} - {} product(s) are priced by it'.format(b['feeGroup'], b['products']))
    print('{} rate(s) moved{}'.format(preview['rates'], ' · the card is the same' if preview['same'] else ''))
    return 0


def accept(path: str, note: str, version: str = None, source: str = None, asOf: str = None) -> int:
    words = ' · '.join(x for x in (note, source and 'from ' + source, version and 'version ' + version,
                                    asOf and 'as of ' + asOf) if x)
    try:
        saved = feeCard.importFile(path, _read(path), words, user=getpass.getuser())
    except ValidationError as exc:
        print('REFUSED ({}): {}'.format(exc.field, exc.message))
        print('nothing was written')
        return 1
    print('the fee card is now version {}'.format(saved['revision']))
    for line in saved['changes']:
        print('  {}'.format(line))
    return 0


def export(path: str) -> int:
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        fh.write(feeCard.exportCsv())
    print('wrote the fee card ({}) to {}'.format(fees.deliveryInfo()['version'], path))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--census', action='store_true')
    ap.add_argument('--diff', metavar='CSV')
    ap.add_argument('--accept', metavar='CSV')
    ap.add_argument('--export', metavar='CSV')
    ap.add_argument('--note')
    ap.add_argument('--version')
    ap.add_argument('--source')
    ap.add_argument('--as-of', dest='asOf')
    args = ap.parse_args(argv)
    if args.diff:
        return diff(args.diff)
    if args.accept:
        if not args.note:
            ap.error('--accept needs --note: every change to the fee card says why')
        return accept(args.accept, args.note, args.version, args.source, args.asOf)
    if args.export:
        return export(args.export)
    return census()


if __name__ == '__main__':
    sys.exit(main())
