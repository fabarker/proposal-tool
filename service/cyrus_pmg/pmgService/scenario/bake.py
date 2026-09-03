"""Offline bake: precompute every portfolio the tool can ever be asked for.

The Proposal Tool is a lookup, not a solve (DECISIONS Q2). Its input space is
closed: every portfolio the supplying database offers (the strategic universe,
D54) x 4 hedging policies. Nothing a PWA selects is unknown in advance, so the
analytics can be computed once per data version and served from disk.

Three steps, in cost order. ENUMERATE reads the extract and parses every name
into a key - cheap, and alone enough to populate the selectors; a name that
does not parse is recorded, not fatal. WEIGHTS is a read of the same extract.
ANALYTICS is the expensive step, one resolve per key x hedging, resumable and
recorded per key. The manifest carries the derived facets and the source's
provenance, so a service can answer "what may I offer?" from one file.

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

from . import fees, rules, saaKeys, universe
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
    # what the bake was enumerated from, and what the selectors may offer -
    # derived from the key set, so a service reads them here rather than
    # recomputing them from a weights table (D54)
    manifest['source'] = universe.describeSource()
    manifest['vocabulary'] = {
        'currencies': list(saaKeys.CURRENCIES),
        'riskLevels': list(saaKeys.RISK_LEVELS),
        'allocationTypes': list(saaKeys.ALLOCATION_TYPES),
    }
    manifest['facets'] = universe.facets()
    manifest['unparsedNames'] = [{'name': n, 'reason': r} for n, r in universe.failures()]
    manifest['feeCard'] = fees.deliveryInfo()      # the other input a store depends on (D55)
    substituted = {name.split('_')[0]: s['analyticsCurrency']
                   for name, s in manifest['slices'].items() if s.get('currencySubstituted')}
    if substituted:
        manifest['currencySubstitutions'] = substituted
    writeJsonAtomic(path, manifest)


def bakeSlice(port, currency: str, hedging: str, directory: str,
              resume: bool = True, limit: int = None, flushEvery: int = 10,
              verbose: bool = True, analyticsCurrency: str = None) -> dict:
    """Bake every available portfolio for one (currency, hedging).

    *analyticsCurrency* runs the analytics in a currency other than the
    portfolio's own - the database carries a config for USD and GBP only, so
    a CHF or EUR book otherwise fails outright. The portfolio keeps its own
    currency everywhere it is identified: the key, the slice, the name, the
    column header. Only the context the numbers were computed in changes, and
    that is recorded on every payload it touched (``analyticsCurrency``) and
    in the manifest, because a GBP book priced in a USD context is not GBP
    analytics and nothing downstream should be able to forget it.
    """
    basis = BasisInput(currency=currency, hedging=hedging)
    context = BasisInput(currency=analyticsCurrency or currency, hedging=hedging)
    substituted = context.currency != currency
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
            'analyticsCurrency': context.currency,
            'currencySubstituted': substituted,
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
            payload = port.resolve_portfolio(context, key)
            if substituted:
                # the caveat travels with the numbers, not beside them
                payload = dict(payload, analyticsCurrency=context.currency)
            payloads[keyStr] = payload
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


def census() -> int:
    """The enumeration step on its own: what the extract holds, before any
    analytics are run against it. This is the dry run to trust a parser by."""
    source = universe.describeSource()
    facets = universe.facets()
    print('source     {}'.format(source['path']))
    print('modified   {}'.format(source['modified']))
    print('portfolios {}   holdings {}   unknown tickers {}'.format(
        source['portfolios'], source['holdings'], source['unknownTickers'] or 'none'))
    print('currencies {}'.format(', '.join(facets['currencies'])))
    print('risk       {}'.format(', '.join(facets['riskLevels'])))
    for risk in facets['riskLevels']:
        offered = facets['allocationTypes'].get(risk) or []
        print('  {:<13} {}'.format(risk, ', '.join(offered) or 'no allocation type'))
    print('ex-RAs     {}'.format(', '.join(
        t for t, offered in facets['exclusionOffered'].items() if offered) or 'none'))
    failures = universe.failures()
    print('unparsed   {}'.format(len(failures)))
    for name, reason in failures:
        print('  REJECTED {!r}: {}'.format(name, reason))
    return 1 if failures else 0


def _makePort(adapter: str):
    if adapter == 'fixtures':
        from .fixturesAdapter import FixturesScenarioPort
        return FixturesScenarioPort()
    from .liveAdapter import LiveScenarioPort
    return LiveScenarioPort()


def _bakeOneInProcess(args_tuple):
    """Worker entry point: one process per slice keeps the analytics
    library's process-level caches (factor windows, betas) warm for the
    whole slice."""
    (adapter, currency, hedging, directory, resume, limit, flushEvery,
     analyticsCurrency) = args_tuple
    port = _makePort(adapter)
    return bakeSlice(port, currency, hedging, directory, resume=resume,
                     limit=limit, flushEvery=flushEvery, verbose=True,
                     analyticsCurrency=analyticsCurrency)


def assetBlock(currency: str, hedging: str, analyticsCurrency: str = None) -> list:
    """The per-asset long-term estimates for one slice, read off a portfolio
    built in that basis. Every portfolio in a slice carries the whole padded
    universe, so any one of them answers for all of them."""
    from . import portfolio_weights as pw
    keys = [k for k in universe.keyStrs() if k.startswith(currency + '|')]
    if not keys:
        return []
    key = PortfolioKey.fromStr(keys[0])
    portfolio = pw.get_portfolio(analyticsCurrency or currency,
                                 universe.weightMap(key), hedging_option=hedging)
    rows = []
    for asset in portfolio.get_assets():
        premia = float(asset.get_risk_premia())
        uncertainty = float(asset.get_uncertainty())
        rows.append({
            'reportingName': asset.reporting_name,
            'category': asset.category,
            'lower': premia - uncertainty,
            'mean': premia,
            'upper': premia + uncertainty,
            'volatility': float(asset.get_volatility()),
            'sharpe': float(asset.get_sharpe_ratio()),
            'totalReturn': float(asset.get_total_return()),
            'hedgingRatio': float(asset.hedging_ratio),
            'from': str(asset.index.min())[:10],
            'to': str(asset.index.max())[:10],
        })
    return rows


def writeAssetEstimates(directory: str, entries) -> str:
    """Write ``assetEstimates.json`` beside the slices, one block per basis.

    A block records the currency its analytics actually ran in, because a
    substituted slice carries the substitute's estimates and the sheet should
    not pretend otherwise. A slice that could not be built is left out rather
    than filled with something plausible."""
    path = os.path.join(directory, 'assetEstimates.json')
    out = {'_note': ('Per-asset long-term estimates behind the export\'s assumptions '
                     'sheet, recorded once per (currency, hedging) slice.'),
           'updatedAt': datetime.datetime.now().isoformat(timespec='seconds'),
           'slices': {}}
    for entry in entries:
        currency, hedging = entry['currency'], entry['hedging']
        analytics = entry.get('analyticsCurrency') or currency
        try:
            rows = assetBlock(currency, hedging, analytics)
        except Exception as exc:                       # noqa: BLE001 - reported, not raised
            print('  asset estimates for {} {} unavailable: {}: {}'.format(
                currency, hedging, type(exc).__name__, exc), flush=True)
            continue
        if rows:
            out['slices']['{}|{}'.format(currency, hedging)] = {
                'analyticsCurrency': analytics, 'assets': rows}
    writeJsonAtomic(path, out)
    print('asset estimates: {} slice(s) -> {}'.format(len(out['slices']), path), flush=True)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--currency', action='append', default=None,
                        help='currency to bake; repeatable (default: all)')
    parser.add_argument('--hedging', action='append', default=None,
                        help='hedging policy to bake; repeatable (default: all)')
    parser.add_argument('--all', action='store_true',
                        help='bake every currency x hedging slice')
    parser.add_argument('--adapter', default='live',
                        choices=('live', 'fixtures'))
    parser.add_argument('--out', default=None, help='store directory')
    parser.add_argument('--workers', type=int, default=1,
                        help='parallel slice workers (one process per slice)')
    parser.add_argument('--limit', type=int, default=None,
                        help='bake at most N portfolios per slice (smoke test)')
    parser.add_argument('--flush-every', type=int, default=10, dest='flushEvery')
    parser.add_argument('--no-resume', action='store_true')
    parser.add_argument('--analytics-currency', dest='analyticsCurrency', default=None,
                        help='run the analytics in this currency whatever the '
                             'portfolio says (the database carries USD and GBP '
                             'configs only). The portfolio keeps its own currency '
                             'in its key, slice and name; the substitution is '
                             'recorded on every payload and in the manifest.')
    parser.add_argument('--census', action='store_true',
                        help='enumerate only: report the source, the facets and '
                             'every name that did not parse, then exit')
    args = parser.parse_args(argv)

    if args.census:
        return census()

    currencies = args.currency or (rules.CURRENCIES if args.all else ['USD'])
    hedgings = args.hedging or (rules.HEDGING_POLICIES if args.all else ['Hedged'])
    directory = storeDir(args.out)
    os.makedirs(directory, exist_ok=True)

    slices = [(args.adapter, c, h, directory, not args.no_resume, args.limit,
               args.flushEvery, args.analyticsCurrency)
              for c in currencies for h in hedgings]
    print('baking {} slice(s) into {} with the {} adapter'.format(
        len(slices), directory, args.adapter), flush=True)
    if args.analyticsCurrency:
        substituted = [c for c in currencies if c != args.analyticsCurrency]
        print('analytics run in {} for {} - those payloads are marked, and are '
              'NOT {} analytics'.format(args.analyticsCurrency,
                                        ', '.join(substituted) or 'nothing',
                                        '/'.join(substituted) or '-'), flush=True)

    started = time.time()
    if args.workers > 1 and len(slices) > 1:
        import multiprocessing
        with multiprocessing.get_context('spawn').Pool(args.workers) as pool:
            entries = pool.map(_bakeOneInProcess, slices)
    else:
        entries = [_bakeOneInProcess(item) for item in slices]

    # The workers' own manifest writes are progress reporting, and with several
    # of them a read-modify-write can drop a sibling's slice. The parent's pass
    # here is the authoritative one: every entry merged in, in order.
    for entry in entries:
        updateManifest(directory, entry['currency'], entry['hedging'], entry)

    # The assumptions sheet reports what the library assumes about each ASSET
    # rather than about any portfolio, so it is recorded once per slice here
    # rather than on all 43 payloads of one. Without this the export's fourth
    # sheet has nothing to say (D67).
    writeAssetEstimates(directory, entries)

    baked = sum(e['baked'] for e in entries)
    failed = sum(len(e['failures']) for e in entries)
    print('\nbaked {} portfolios across {} slices in {:.0f}s ({} failures)'.format(
        baked, len(entries), time.time() - started, failed), flush=True)
    print('store: {}'.format(directory), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
