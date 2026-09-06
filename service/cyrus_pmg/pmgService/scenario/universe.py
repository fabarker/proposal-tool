"""The strategic universe: every portfolio the supplying database offers (D54).

The database is the authority. This module reads its extract - one row per
holding, ``PortfolioName, AssetTicker, Weight`` - parses each name into a key,
and from the key set derives what the UI may offer. Nothing about which
currencies, risk levels or allocation types exist is written down here: the
facets are a projection of the keys, so a portfolio the database does not hold
cannot be offered and one it gains appears on the next bake.

The extract's location is configuration: ``SCENARIO_SAA_SOURCE`` names a CSV
or XLSX. The packaged default is the fictitious stand-in under
``proposal-tool/saaSource``, which the host points at its real extract.

Everything is memoised for the life of the process; the source is static
between bakes. ``reload()`` exists for tests.
"""

from __future__ import annotations

import csv
import os
import threading

from . import portfolio_weights as pw
from . import saaKeys
from .types import PortfolioKey

_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', '..', '..', '..', 'saaSource', 'saaPortfolios.csv')


def sourcePath() -> str:
    return os.path.abspath(os.getenv('SCENARIO_SAA_SOURCE', '') or _DEFAULT)


# ticker -> (reporting name, category), in the supplied universe's order
_ASSETS = {code: (name, category) for code, name, category in pw.ASSET_METADATA}
_ASSET_ORDER = {code: index for index, (code, _, _) in enumerate(pw.ASSET_METADATA)}

_lock = threading.Lock()
#: bumped by reload(); see generation()
_generation = 0
_loaded = None


class _Universe:
    __slots__ = ('path', 'keys', 'byKeyStr', 'names', 'weights', 'failures',
                 'unknownTickers')

    def __init__(self, path):
        self.path = path
        self.keys = []             # PortfolioKey, vocabulary order
        self.byKeyStr = {}         # keyStr -> PortfolioKey
        self.names = {}            # keyStr -> the name as the database spells it
        self.weights = {}          # keyStr -> [(ticker, weight)] in universe order
        self.failures = []         # (name, reason) for names that did not parse
        self.unknownTickers = {}   # ticker -> count, for tickers not in the universe


def _readRows(path: str):
    if path.lower().endswith('.csv'):
        with open(path, newline='', encoding='utf-8') as fh:
            reader = csv.reader(fh)
            next(reader, None)
            for row in reader:
                if row and row[0]:
                    yield row[0], row[1], row[2]
        return
    from openpyxl import load_workbook
    sheet = load_workbook(path, read_only=True).active
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if row and row[0]:
            yield row[0], row[1], row[2]


def _load(path: str) -> _Universe:
    u = _Universe(path)
    seen = {}
    rejected = set()
    for name, ticker, weight in _readRows(path):
        name = str(name).strip()
        if name in rejected:
            continue
        key = seen.get(name)
        if key is None:
            try:
                key = saaKeys.parseName(name)
            except saaKeys.UnparseableName as exc:
                u.failures.append((name, str(exc)))
                rejected.add(name)
                continue
            keyStr = key.toStr()
            if keyStr in u.byKeyStr:
                u.failures.append((name, 'duplicates {!r}'.format(u.names[keyStr])))
                rejected.add(name)
                continue
            seen[name] = key
            u.keys.append(key)
            u.byKeyStr[keyStr] = key
            u.names[keyStr] = name
            u.weights[keyStr] = []
        ticker = str(ticker).strip()
        if ticker not in _ASSETS:
            u.unknownTickers[ticker] = u.unknownTickers.get(ticker, 0) + 1
            continue
        u.weights[key.toStr()].append((ticker, float(weight)))
    for rows in u.weights.values():
        rows.sort(key=lambda r: _ASSET_ORDER[r[0]])
    u.keys.sort(key=saaKeys.sortKey)
    return u


def _get() -> _Universe:
    global _loaded
    with _lock:
        if _loaded is None:
            _loaded = _load(sourcePath())
        return _loaded


def reload() -> None:
    """Forget the loaded source (tests, or a bake that re-reads)."""
    global _loaded, _generation
    with _lock:
        _loaded = None
        _generation += 1


def generation() -> int:
    """How many times the source has been forgotten. Anything caching a
    PROJECTION of the universe - rules' option lists above all - holds this
    beside its cache and rebuilds when it moves, so a reload cannot leave a
    stale list of currencies behind (D86)."""
    return _generation


# ------------------------------------------------------------------ reads ---

def keys() -> list:
    """Every portfolio the database offers, in vocabulary order."""
    return list(_get().keys)


def keyStrs() -> list:
    return [k.toStr() for k in _get().keys]


def has(key: PortfolioKey) -> bool:
    return key.toStr() in _get().byKeyStr


def nameOf(key: PortfolioKey) -> str:
    """The portfolio's name as the database spells it."""
    return _get().names[key.toStr()]


def failures() -> list:
    """(name, reason) for every name in the extract that did not parse."""
    return list(_get().failures)


def unknownTickers() -> dict:
    return dict(_get().unknownTickers)


def holdings(key: PortfolioKey) -> list:
    """[(ticker, weight)] in universe order; LookupError when not offered."""
    rows = _get().weights.get(key.toStr())
    if rows is None:
        raise LookupError('{} is not in the strategic universe'.format(key.toStr()))
    return list(rows)


def weightMap(key: PortfolioKey, includeZeros: bool = True) -> dict:
    """ticker -> decimal weight, zero-padded to the full universe by default.

    The analytics portfolio is built zero-padded so every portfolio carries an
    identical asset set (D13); the screen payload comes from held rows only.
    """
    held = dict(holdings(key))
    if not includeZeros:
        return held
    return {code: held.get(code, 0.0) for code, _, _ in pw.ASSET_METADATA}


def categoryRows(key: PortfolioKey) -> list:
    """The payload's category/asset structure for one portfolio (spec 2.2).

    Order is the universe's own, which the UI renders as-is. A category the
    portfolio does not hold is simply absent - the three-cell-state rule
    (value / dash / blank) is the UI's to apply from presence.
    """
    categories, byName = [], {}
    for ticker, weight in holdings(key):
        name, category = _ASSETS[ticker]
        entry = byName.get(category)
        if entry is None:
            entry = {'name': category, 'weightPct': 0.0, 'assets': []}
            byName[category] = entry
            categories.append(entry)
        entry['weightPct'] += weight * 100.0
        entry['assets'].append({'reportingName': name, 'weightPct': weight * 100.0})
    return categories


# ----------------------------------------------------------------- facets ---

def facets(keyList=None) -> dict:
    """What the selectors may offer, derived from a key set and nothing else.

    A selector is empty because the data holds nothing for it, which is what
    greys it out. Nothing here names a risk level or an allocation type.

    Returns:
      currencies            in vocabulary order
      riskLevels            in risk order, least to most risky
      allocationTypes       {riskLevel: [type, ...]} - empty for an all-equity level
      exclusionOffered      {allocationType: bool} - whether an ex-RAs variant exists
    """
    keyList = list(_get().keys if keyList is None else keyList)
    byRisk, byType = {}, {}
    for key in keyList:
        byRisk.setdefault(key.riskLevel, set())
        if not key.isAllEquity:
            byRisk[key.riskLevel].add(key.allocationType)
            byType.setdefault(key.allocationType, set()).add(bool(key.excludeRealAssets))
    return {
        'currencies': sorted({k.currency for k in keyList},
                             key=saaKeys.CURRENCIES.index),
        'riskLevels': sorted(byRisk, key=saaKeys.RISK_LEVELS.index),
        'allocationTypes': {risk: sorted(types, key=saaKeys.ALLOCATION_TYPES.index)
                            for risk, types in byRisk.items()},
        'exclusionOffered': {t: (values == {False, True}) for t, values in byType.items()},
    }


def realAssetTypes(keyList=None) -> list:
    """Allocation types for which an ex-RAs variant exists - the ones that
    hold real assets to exclude. Derived, so it cannot drift from the data."""
    offered = facets(keyList)['exclusionOffered']
    return [t for t in saaKeys.ALLOCATION_TYPES if offered.get(t)]


def describeSource() -> dict:
    """Provenance for the manifest and describe(): where, how big, how clean."""
    u = _get()
    try:
        import datetime
        modified = datetime.datetime.fromtimestamp(
            os.path.getmtime(u.path)).isoformat(timespec='seconds')
    except OSError:
        modified = None
    return {
        'path': u.path,
        'modified': modified,
        'portfolios': len(u.keys),
        'holdings': sum(len(v) for v in u.weights.values()),
        'unparsed': len(u.failures),
        'unknownTickers': dict(u.unknownTickers),
    }
