"""Offline bake: precompute every portfolio the tool can ever be asked for.

The Proposal Tool is a lookup, not a solve (DECISIONS Q2). Its input space is
closed and small: the 68 UI-available combinations per currency x 4 hedging
policies. Nothing a PWA selects is unknown in advance, so the analytics can be
computed once per data version and served from disk.

Layout under the store directory (SCENARIO_BAKED_DIR):

    manifest.json           provenance + coverage + failures
    USD_Hedged.json         {keyStr: PortfolioResult} for that slice
    USD_Unhedged.json
    ...

One file per (currency, hedging) so a cold process reads only the slice it
needs (~700 KB, ~10 ms) instead of the whole universe.

Usage::

    # one slice
    python3 -m cyrus_pmg.pmgService.scenario.bake --currency USD --hedging Hedged

    # everything this database can serve, four workers
    python3 -m cyrus_pmg.pmgService.scenario.bake --all --workers 4

    # a store for demos/tests, no database required
    python3 -m cyrus_pmg.pmgService.scenario.bake --all --adapter fixtures

Resumable by default: an existing slice is loaded and its keys skipped, and
the slice is flushed every ``--flush-every`` portfolios, so a bake that is
interrupted (or a resolve that fails) never loses completed work. A
combination that cannot be resolved is recorded in the manifest with its
error rather than aborting the run - this database, for instance, carries
currency configs for USD and GBP only, so CHF and EUR bake as failures.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import tempfile
import time

from . import rules
from .types import BasisInput, PortfolioKey

DEFAULT_STORE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')


def storeDir(path=None) -> str:
    """The bake store directory: argument, env, then the packaged default."""
    directory = path or os.getenv('SCENARIO_BAKED_DIR', '') or DEFAULT_STORE
    return os.path.abspath(directory)


def sliceName(currency: str, hedging: str) -> str:
    return '{}_{}.json'.format(currency, hedging.replace(' ', ''))


def slicePath(directory: str, currency: str, hedging: str) -> str:
    return os.path.join(directory, sliceName(currency, hedging))


def writeJsonAtomic(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, separators=(',', ':'))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def readJson(path: str, default=None):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def updateManifest(directory: str, currency: str, hedging: str, entry: dict) -> None:
    """Merge one slice's outcome into the store manifest."""
    path = os.path.join(directory, 'manifest.json')
    manifest = readJson(path, default=None) or {'slices': {}}
    manifest.setdefault('slices', {})[sliceName(currency, hedging)] = entry
    manifest['updatedAt'] = datetime.datetime.now().isoformat(timespec='seconds')
    covered = sum(s.get('baked', 0) for s in manifest['slices'].values())
    manifest['portfoliosBaked'] = covered
    manifest['currencies'] = sorted({
        name.split('_')[0] for name, s in manifest['slices'].items() if s.get('baked')})
    writeJsonAtomic(path, manifest)


def bakeSlice(port, currency: str, hedging: str, directory: str,
              resume: bool = True, limit: int = None, flushEvery: int = 10,
              verbose: bool = True) -> dict:
    """Bake every available portfolio for one (currency, hedging)."""
    basis = BasisInput(currency=currency, hedging=hedging)
    # No mandate filter: the bake covers everything the schema can offer at
    # any mandate size, so the $20m rule stays a UI-time concern.
    keys = rules.availability(basis, None)
    if limit:
        keys = keys[:limit]

    path = slicePath(directory, currency, hedging)
    payloads = (readJson(path, default={}) or {}) if resume else {}
    already = len(payloads)
    failures = {}
    started = time.time()

    def _entry(complete):
        return {
            'currency': currency,
            'hedging': hedging,
            'available': len(keys),
            'baked': len(payloads),
            'resumedWith': already,
            'failures': failures,
            'complete': complete,
            'seconds': round(time.time() - started, 1),
            'describe': port.describe(),
            'bakedAt': datetime.datetime.now().isoformat(timespec='seconds'),
        }

    for index, keyStr in enumerate(keys, start=1):
        if keyStr in payloads:
            continue
        key = PortfolioKey.fromStr(keyStr)
        t0 = time.time()
        try:
            payloads[keyStr] = port.resolve_portfolio(basis, key)
            if verbose:
                print('  [{}/{}] {:<28} {:6.1f}s'.format(
                    index, len(keys), keyStr, time.time() - t0), flush=True)
        except Exception as exc:                      # noqa: BLE001 - recorded, not raised
            failures[keyStr] = '{}: {}'.format(type(exc).__name__, exc)
            if verbose:
                print('  [{}/{}] {:<28} FAILED {}'.format(
                    index, len(keys), keyStr, failures[keyStr][:90]), flush=True)
        if flushEvery and (len(payloads) % flushEvery == 0):
            # flush the manifest with the slice: a long bake stays observable,
            # and a service reading the store mid-bake describes itself honestly
            writeJsonAtomic(path, payloads)
            updateManifest(directory, currency, hedging, _entry(False))

    writeJsonAtomic(path, payloads)
    entry = _entry(True)
    updateManifest(directory, currency, hedging, entry)
    if verbose:
        print('slice {} {}: {}/{} baked in {:.0f}s ({} failures)'.format(
            currency, hedging, entry['baked'], entry['available'],
            entry['seconds'], len(failures)), flush=True)
    return entry


def _makePort(adapter: str):
    if adapter == 'fixtures':
        from .fixturesAdapter import FixturesScenarioPort
        return FixturesScenarioPort()
    from .epsilonPhiAdapter import EpsilonPhiScenarioPort
    return EpsilonPhiScenarioPort()


def _bakeOneInProcess(args_tuple):
    """Worker entry point: one process per slice keeps epsilonPhi's
    process-level caches (factor windows, betas) warm for the whole slice."""
    adapter, currency, hedging, directory, resume, limit, flushEvery = args_tuple
    port = _makePort(adapter)
    return bakeSlice(port, currency, hedging, directory, resume=resume,
                     limit=limit, flushEvery=flushEvery, verbose=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--currency', action='append', default=None,
                        help='currency to bake; repeatable (default: all)')
    parser.add_argument('--hedging', action='append', default=None,
                        help='hedging policy to bake; repeatable (default: all)')
    parser.add_argument('--all', action='store_true',
                        help='bake every currency x hedging slice')
    parser.add_argument('--adapter', default='epsilonphi',
                        choices=('epsilonphi', 'fixtures'))
    parser.add_argument('--out', default=None, help='store directory')
    parser.add_argument('--workers', type=int, default=1,
                        help='parallel slice workers (one process per slice)')
    parser.add_argument('--limit', type=int, default=None,
                        help='bake at most N portfolios per slice (smoke test)')
    parser.add_argument('--flush-every', type=int, default=10, dest='flushEvery')
    parser.add_argument('--no-resume', action='store_true')
    args = parser.parse_args(argv)

    currencies = args.currency or (rules.CURRENCIES if args.all else ['USD'])
    hedgings = args.hedging or (rules.HEDGING_POLICIES if args.all else ['Hedged'])
    directory = storeDir(args.out)
    os.makedirs(directory, exist_ok=True)

    slices = [(args.adapter, c, h, directory, not args.no_resume, args.limit,
               args.flushEvery)
              for c in currencies for h in hedgings]
    print('baking {} slice(s) into {} with the {} adapter'.format(
        len(slices), directory, args.adapter), flush=True)

    started = time.time()
    if args.workers > 1 and len(slices) > 1:
        import multiprocessing
        with multiprocessing.get_context('spawn').Pool(args.workers) as pool:
            entries = pool.map(_bakeOneInProcess, slices)
    else:
        entries = [_bakeOneInProcess(item) for item in slices]

    baked = sum(e['baked'] for e in entries)
    failed = sum(len(e['failures']) for e in entries)
    print('\nbaked {} portfolios across {} slices in {:.0f}s ({} failures)'.format(
        baked, len(entries), time.time() - started, failed), flush=True)
    print('store: {}'.format(directory), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
