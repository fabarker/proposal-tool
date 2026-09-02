"""Manage the delivered rate card (D55): census, diff, accept.

The card comes from another team as a long CSV - one row per cell. The tool
that earns its keep is the DIFF: a re-delivery is reviewed as "these eleven
cells moved", cell by cell, before anyone points the service at it. The
census says what a file holds; accept copies it into place and stamps the
delivery's provenance into fees.json.

    python3 -m cyrus_pmg.pmgService.scenario.feeTools --census
    python3 -m cyrus_pmg.pmgService.scenario.feeTools --diff new.csv
    python3 -m cyrus_pmg.pmgService.scenario.feeTools --accept new.csv \\
        --version 2026.3 --source "PMG pricing desk" [--as-of 2026-09-01]

Accept refuses a card the framework cannot price from - a gap, disagreeing
tier edges, a crossed band - so a bad delivery fails here rather than at the
next service start. The card is read only in the service: this is the only way
it changes.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys

from . import fees


def _cellStr(cell) -> str:
    schedule, group, tier, source, point = cell
    return '{:<4} {:<18} {:<3} {:<10} {}'.format(schedule, group or '-', tier, source, point)


def census(path=None) -> int:
    card = fees._readDelivery(path or fees.ratesPath())
    info = fees.deliveryInfo()
    print('card       {}'.format(path or info['path']))
    print('version    {}   as of {}   from {}'.format(
        info.get('version'), info.get('asOf'), info.get('source')))
    print('cells      {}   tiers {}   fee groups {}'.format(
        card['rows'], len(card['tiers']), len(card['feeGroups'])))
    for t in card['tiers']:
        print('  {:<3} {:<16} [{:,.0f}, {})'.format(
            t['id'], t['label'], t['min'], '∞' if t['max'] is None else '{:,.0f}'.format(t['max'])))
    print('groups     {}'.format(', '.join(card['feeGroups'])))
    for schedule in fees.SCHEDULES:
        groups = [None] if not fees.byGroup(schedule) else card['feeGroups']
        for g in groups:
            rates = [v for (s, gg, _, _, _), v in card['cells'].items() if s == schedule and gg == g]
            if rates:
                print('  {:<4} {:<18} {:.2f}% – {:.2f}%'.format(schedule, g or '-', min(rates), max(rates)))
    return 0


def diff(path: str) -> int:
    """Cell-by-cell: the candidate against the delivery in force."""
    new = fees._readDelivery(path)['cells']
    old = fees._DELIVERED['cells']
    changed = [(c, old[c], new[c]) for c in old if c in new and old[c] != new[c]]
    added = [c for c in new if c not in old]
    removed = [c for c in old if c not in new]
    for cell, a, b in sorted(changed):
        print('  {}   {:>6.2f} → {:<6.2f}  ({:+.2f})'.format(_cellStr(cell), a, b, b - a))
    for cell in sorted(added):
        print('  {}   added  {:.2f}'.format(_cellStr(cell), new[cell]))
    for cell in sorted(removed):
        print('  {}   REMOVED (was {:.2f})'.format(_cellStr(cell), old[cell]))
    print('{} of {} rates changed, {} added, {} removed'.format(
        len(changed), len(old), len(added), len(removed)))
    return 0


def accept(path: str, version: str, source: str, asOf: str = None) -> int:
    card = fees._readDelivery(path)                # raises BadRateCard on a bad file
    target = fees.ratesPath()
    shutil.copyfile(path, target)
    cfg = json.load(open(fees._CONFIG_PATH, encoding='utf-8'))
    cfg['delivery'] = {'source': source, 'version': version,
                       'asOf': asOf or datetime.date.today().isoformat()}
    cfg['placeholder'] = False
    with open(fees._CONFIG_PATH, 'w', encoding='utf-8') as fh:
        json.dump(cfg, fh, indent=2)
    print('accepted {} cells as version {} into {}'.format(card['rows'], version, target))
    print('restart the service: the delivery is read at import')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--census', action='store_true')
    ap.add_argument('--diff', metavar='CSV')
    ap.add_argument('--accept', metavar='CSV')
    ap.add_argument('--version')
    ap.add_argument('--source')
    ap.add_argument('--as-of', dest='asOf')
    args = ap.parse_args(argv)
    if args.diff:
        return diff(args.diff)
    if args.accept:
        if not (args.version and args.source):
            ap.error('--accept needs --version and --source')
        return accept(args.accept, args.version, args.source, args.asOf)
    return census()


if __name__ == '__main__':
    sys.exit(main())
