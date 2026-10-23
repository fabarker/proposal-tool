"""File-backed scenario state.

Scenario state - mandate, basis, column keys, sleeves - must survive a refresh
(spec 11.8) and must be visible to every service worker: the dashboard
launches pmgService with more than one uvicorn worker, so an in-memory store would 404
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
from .fees import DEFAULT_LEVEL as DEFAULT_FEE_LEVEL

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


def createScenario(mandate: MandateInput, basis: BasisInput, createdBy: str = '') -> dict:
    """Create and persist a new scenario; returns its state.

    *createdBy* is the caller's kerberos. It was never written down before
    the register needed it (D69): a scenario expires in a day, but the
    proposal it produces is kept for ever and should say who began it."""
    with _lock:
        _sweepExpired()
        scenarioId = 'sc_' + secrets.token_hex(6)
        now = time.time()
        state = {
            'id': scenarioId,
            'createdBy': createdBy or '',
            'createdAt': now,
            'updatedAt': now,
            'mandate': mandate.toDict(),
            'basis': basis.toDict(),
            'base': None,
            'comparisons': [],
            'variant': None,
            # on by default where it can be funded (D50); the client shows
            # it off, and tiltedCategories no-ops, on a portfolio that
            # cannot fund it, so one default serves both cases
            'tacticalTilt': True,
            # On by default wherever the book may hold it, the same shape as
            # the tilt above: canHoldVolPremium no-ops it in a currency that
            # may not, and the client renders the toggle off and disabled
            # there, so one default serves every currency (D53).
            'volPremium': True,
            'sleeves': {},
            # the proposal's own sleeves, by the name they are chosen under:
            # products and weights built on the implementation page (D170)
            'customSleeves': {},
            # a proposal shows no fees until a PWA asks for them (D52); the
            # schedule is then a choice with no default, like the variant,
            # and the level has a prescribed one, the PMG target (D51)
            'includeFees': False,
            'feeSchedule': None,
            'feeLevel': DEFAULT_FEE_LEVEL,
            # the PWA's own rates, by schedule, for the custom level (D96)
            'customFees': {},
            # the charges the PWA quotes beside the fees (D177)
            'otherCharges': {},
            # a proposal built product by product (D171): None until one is
            # begun; its ``active`` says which kind the proposal is
            'customPortfolio': None,
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
                   basis: BasisInput = None, sleeves: dict = None,
                   variant: str = None, tacticalTilt: bool = None,
                   feeSchedule: str = None, feeLevel: str = None,
                   includeFees: bool = None, volPremium: bool = None,
                   customFees: dict = None, customFeesBy: str = None,
                   customSleeves: dict = None, customPortfolio: dict = None,
                   otherCharges: dict = None, otherChargesBy: str = None,
                   marketing: dict = None, hedgeFundNotice: dict = None) -> dict:
    """Apply a partial state update (the PUT endpoint - deviation D2).

    *customFees* replaces the whole map (D96) and records who wrote it and
    when, for the workbook's header: a custom rate has no delivery behind it,
    so the sheet says whose it is. *otherCharges* replaces the charges the
    PWA quotes beside the fees (D177), and records whose they are the same
    way. *marketing* replaces the marketing warnings the PWA has
    acknowledged and the allocation they were shown for (D196).
    *hedgeFundNotice* replaces the record of the hedge fund notice: who
    confirmed it and when, and who declined it (D198).

    A variant change clears the sleeve map in the same write unless the caller
    supplied one, because sleeve names are only meaningful under the variant
    they were chosen from (D29). The custom sleeves go with it unless the
    caller supplied them: their products are the ones the old type's
    library holds (D170). It also drops any column the new variant
    cannot build, since the variant now decides the available allocations and
    not merely the implementation (D49). Doing both here rather than in the
    router keeps them from ever being persisted out of step.

    A custom portfolio (D171) is kept through a variant change: a build is
    too much work to throw away, and a product the new type may not hold is
    flagged, not dropped. Its approval was of the old type and goes with the
    fingerprint the router checks. *customPortfolio* replaces the whole entry.

    Columns the new variant still offers are kept, so moving between two
    variants that share an allocation does not throw away resolved work. The
    base is column one and the comparisons are read against it, so if the base
    itself is dropped the whole set goes rather than leaving comparisons with
    no anchor.
    """
    with _lock:
        state = getScenario(scenarioId)
        if mandate is not None:
            state['mandate'] = mandate.toDict()
        if basis is not None:
            state['basis'] = basis.toDict()
        if variant is not None and variant != state.get('variant'):
            state['variant'] = variant
            if sleeves is None:
                state['sleeves'] = {}
            if customSleeves is None:
                state['customSleeves'] = {}
            _pruneColumnsForVariant(state, variant)
        if sleeves is not None:
            state['sleeves'] = dict(sleeves)
        if customSleeves is not None:
            state['customSleeves'] = dict(customSleeves)
        if customPortfolio is not None:
            state['customPortfolio'] = dict(customPortfolio)
        if tacticalTilt is not None:
            state['tacticalTilt'] = bool(tacticalTilt)
        if volPremium is not None:
            state['volPremium'] = bool(volPremium)
        if includeFees is not None:
            state['includeFees'] = bool(includeFees)
        if feeSchedule is not None:
            state['feeSchedule'] = feeSchedule
        if feeLevel is not None:
            state['feeLevel'] = feeLevel
        if customFees is not None:
            state['customFees'] = dict(customFees)
            state['customFeesBy'] = customFeesBy or ''
            state['customFeesAt'] = time.time()
        if otherCharges is not None:
            state['otherCharges'] = dict(otherCharges)
            state['otherChargesBy'] = otherChargesBy or customFeesBy or ''
            state['otherChargesAt'] = time.time()
        if marketing is not None:
            state['marketing'] = dict(marketing)
        if hedgeFundNotice is not None:
            state['hedgeFundNotice'] = dict(hedgeFundNotice)
        return _save(state)


def _pruneColumnsForVariant(state: dict, variant: str) -> None:
    """Drop columns the new *variant* does not offer (D49). Mutates *state*."""
    from . import rules
    held = ([state['base']] if state['base'] else []) + list(state['comparisons'])
    if not held:
        return
    mandateSize = (state.get('mandate') or {}).get('mandateSize')
    dropped = set(rules.keysInvalidForVariant(held, variant, mandateSize))
    if not dropped:
        return
    if state['base'] in dropped:
        state['base'] = None
        state['comparisons'] = []
    else:
        state['comparisons'] = [c for c in state['comparisons'] if c not in dropped]


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
    """Remove a comparison column. The proposed portfolio cannot be removed (spec 2.7).

    A page never asks: it replaces the proposed portfolio by recording a new
    one, which this module does in a single write (D102).
    """
    keyStr = key.toStr()
    with _lock:
        state = getScenario(scenarioId)
        if keyStr == state['base']:
            raise ValidationError('key', 'The proposed portfolio cannot be removed.')
        state['comparisons'] = [c for c in state['comparisons'] if c != keyStr]
        return _save(state)


def awaitingApproval() -> list:
    """Every live scenario whose custom portfolio is waiting for an admin's
    approval (D171), newest first: what the console lists so an admin can
    open it. Read straight from the files; an expired one is not listed."""
    cutoff = time.time() - _retentionSeconds()
    out = []
    try:
        names = os.listdir(_storeDir())
    except OSError:
        return out
    for name in names:
        if not (name.startswith('sc_') and name.endswith('.json')):
            continue
        path = os.path.join(_storeDir(), name)
        try:
            if os.path.getmtime(path) < cutoff:
                continue
            with open(path, encoding='utf-8') as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            continue
        custom = state.get('customPortfolio') or {}
        approval = custom.get('approval') or {}
        if not custom.get('active') or approval.get('state') != 'submitted':
            continue
        mandate = state.get('mandate') or {}
        out.append({'scenarioId': state.get('id'), 'variant': state.get('variant'),
                    'profile': custom.get('profile'), 'products': len(custom.get('products') or []),
                    'builtBy': custom.get('builtBy') or '', 'submittedBy': approval.get('by') or '',
                    'note': approval.get('note') or '', 'at': approval.get('at') or '',
                    'primaryPwa': mandate.get('primaryPwa') or '',
                    'mandateSize': mandate.get('mandateSize'),
                    'currency': (state.get('basis') or {}).get('currency')})
    out.sort(key=lambda e: e['at'], reverse=True)
    return out
