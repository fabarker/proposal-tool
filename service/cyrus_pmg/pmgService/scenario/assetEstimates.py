"""The per-asset long-term estimates behind the assumptions sheet (D67).

The fourth sheet of the export reports what the analytics library assumes
about each asset: its risk premia with an estimated range, volatility, Sharpe
ratio, total return, the hedging ratio in force, and the window each was
estimated over. None of that is a property of a portfolio - it belongs to the
asset universe under one (currency, hedging) basis - so it is recorded once
per slice rather than on all 43 payloads of one.

The packaged file is the default, exactly as the fee card's is; a bake writes
its own copy beside the slices and ``SCENARIO_ASSET_ESTIMATES`` overrides
both. Non-USD books were analysed in a USD context (the bake manifest records
the substitution), so their blocks carry the USD estimates and say so in
``analyticsCurrency``.
"""

from __future__ import annotations

import json
import os
import threading

_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'assetEstimates.json')

_lock = threading.Lock()
_loaded = None                      # (path, mtime, {sliceKey: block})


def sourcePath() -> str:
    """Where the estimates come from, most specific first.

    ``SCENARIO_ASSET_ESTIMATES`` wins outright. Otherwise a bake's own copy,
    written beside its slices, is preferred over the packaged one - a store
    and the estimates that describe it should travel together. The packaged
    file is the floor, so an export always has a fourth sheet."""
    override = os.getenv('SCENARIO_ASSET_ESTIMATES', '').strip()
    if override:
        return os.path.abspath(override)
    from .bake import storeDir
    beside = os.path.join(storeDir(None), 'assetEstimates.json')
    if os.path.exists(beside):
        return os.path.abspath(beside)
    return os.path.abspath(_DEFAULT)


def sliceKey(currency: str, hedging: str) -> str:
    return '{}|{}'.format(currency, hedging)


def _load(path):
    with open(path, encoding='utf-8') as handle:
        data = json.load(handle)
    slices = data.get('slices') or {}
    return {key: value for key, value in slices.items()}


def _get():
    """Reloaded when the file changes, like the product catalogue - an
    estimate set is a delivery, and a delivery gets replaced."""
    global _loaded
    path = sourcePath()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return path, {}
    with _lock:
        if _loaded is None or _loaded[0] != path or _loaded[1] != mtime:
            _loaded = (path, mtime, _load(path))
        return path, _loaded[2]


def forSlice(currency: str, hedging: str):
    """The asset rows for one basis, in the universe's order, or [] when the
    slice was never recorded. The caller decides what an empty block means;
    the sheet says so rather than disappearing."""
    _, slices = _get()
    block = slices.get(sliceKey(currency, hedging)) or {}
    return list(block.get('assets') or [])


def analyticsCurrencyFor(currency: str, hedging: str):
    _, slices = _get()
    block = slices.get(sliceKey(currency, hedging)) or {}
    return block.get('analyticsCurrency')


def describe() -> dict:
    path, slices = _get()
    return {'path': path, 'slices': len(slices),
            'assets': len(list(slices.values())[0]['assets']) if slices else 0}


def reload() -> None:
    global _loaded
    with _lock:
        _loaded = None
