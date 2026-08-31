"""File-backed scenario state.

Scenario state - mandate, basis, column keys, sleeves - must survive a refresh
(spec 11.8) and must be visible to every service worker: the host launches
pmgService with more than one uvicorn worker, so an in-memory store would 404
on whichever worker did not create the scenario. One JSON file per scenario,
written atomically, is worker-safe and survives restarts. Recorded as
deviation D8.

Resolved portfolio payloads are NOT stored here - they are a cache,
recomputable, and stay in-process in the adapters.

Retention: scenarios expire untouched after SCENARIO_RETENTION_HOURS
(default 24 - open item 8 is undecided, so the number only shapes the
"no longer available" message). Expired files are removed opportunistically.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
import time

from .types import BasisInput, MandateInput, PortfolioKey, ScenarioNotFound, ValidationError
from .rules import MAX_PORTFOLIOS

_lock = threading.Lock()


def _storeDir() -> str:
    configured = os.getenv('SCENARIO_STORE_DIR', '').strip()
    directory = configured or os.path.join(tempfile.gettempdir(), 'pmg_proposal_scenarios')
    os.makedirs(directory, exist_ok=True)
    return directory


def _retentionSeconds() -> float:
    return float(os.getenv('SCENARIO_RETENTION_HOURS', '24')) * 3600.0


def _path(scenarioId: str) -> str:
    if not scenarioId.startswith('sc_') or not scenarioId[3:].isalnum():
        raise ScenarioNotFound(scenarioId)
    return os.path.join(_storeDir(), scenarioId + '.json')


def _writeAtomic(path: str, state: dict) -> None:
    directory = os.path.dirname(path)
    handle, tmp = tempfile.mkstemp(dir=directory, suffix='.tmp')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as fh:
            json.dump(state, fh, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _sweepExpired() -> None:
    cutoff = time.time() - _retentionSeconds()
    try:
        for name in os.listdir(_storeDir()):
            if not name.endswith('.json'):
                continue
            path = os.path.join(_storeDir(), name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.unlink(path)
            except OSError:
                pass
    except OSError:
        pass


def createScenario(mandate: MandateInput, basis: BasisInput) -> dict:
    """Create and persist a new scenario; returns its state."""
    with _lock:
        _sweepExpired()
        scenarioId = 'sc_' + secrets.token_hex(6)
        now = time.time()
        state = {
            'id': scenarioId,
            'createdAt': now,
            'updatedAt': now,
            'mandate': mandate.toDict(),
            'basis': basis.toDict(),
            'base': None,
            'comparisons': [],
            'sleeves': {},
        }
        _writeAtomic(_path(scenarioId), state)
        return state


def getScenario(scenarioId: str) -> dict:
    """Load a scenario; expired or unknown ids raise ScenarioNotFound."""
    path = _path(scenarioId)
    try:
        if os.path.getmtime(path) < time.time() - _retentionSeconds():
            os.unlink(path)
            raise ScenarioNotFound(scenarioId)
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        raise ScenarioNotFound(scenarioId)


def _save(state: dict) -> dict:
    state['updatedAt'] = time.time()
    _writeAtomic(_path(state['id']), state)
    return state


def updateScenario(scenarioId: str, mandate: MandateInput = None,
                   basis: BasisInput = None, sleeves: dict = None) -> dict:
    """Apply a partial state update (the PUT endpoint - deviation D2)."""
    with _lock:
        state = getScenario(scenarioId)
        if mandate is not None:
            state['mandate'] = mandate.toDict()
        if basis is not None:
            state['basis'] = basis.toDict()
        if sleeves is not None:
            state['sleeves'] = dict(sleeves)
        return _save(state)


def recordColumn(scenarioId: str, key: PortfolioKey, role: str) -> dict:
    """Record a resolved column on the scenario.

    role 'base' replaces the base and drops a comparison that now duplicates
    it; 'comparison' appends, subject to the cap. Both idempotent.
    """
    keyStr = key.toStr()
    with _lock:
        state = getScenario(scenarioId)
        if role == 'base':
            state['base'] = keyStr
            state['comparisons'] = [c for c in state['comparisons'] if c != keyStr]
        else:
            if keyStr != state['base'] and keyStr not in state['comparisons']:
                if len(state['comparisons']) >= MAX_PORTFOLIOS - 1:
                    raise ValidationError(
                        'key', 'All {} comparison slots are in use.'.format(MAX_PORTFOLIOS - 1))
                state['comparisons'].append(keyStr)
        return _save(state)


def removeColumn(scenarioId: str, key: PortfolioKey) -> dict:
    """Remove a comparison column. The base cannot be removed (spec 2.7)."""
    keyStr = key.toStr()
    with _lock:
        state = getScenario(scenarioId)
        if keyStr == state['base']:
            raise ValidationError('key', 'The base portfolio cannot be removed.')
        state['comparisons'] = [c for c in state['comparisons'] if c != keyStr]
        return _save(state)
