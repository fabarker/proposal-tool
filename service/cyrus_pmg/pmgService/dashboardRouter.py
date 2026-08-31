"""Dashboard API router - the host's ``pmgService/dashboardRouter.py``.

In the host this file already exists and carries every dashboard endpoint; the
Proposal Tool TRANSPLANTS the scenario endpoint block below into it verbatim.
In this mirror the file carries only that block, written exactly as it will
appear in the host: mixedCase handler names, reads inheriting the router-level
``requireAuth``, writes taking ``Depends(requireEditor)``, non-2xx JSON
carrying top-level ``error`` (and ``field`` on validation failures) per the
host error contract (spec 3.5).

The HTTP surface is spec 3.4, plus one addition recorded as deviation D2:
PUT /scenario/{scenarioId} persists mandate, basis and sleeve updates, because
GET /scenario/{scenarioId} is specified to restore all three (spec 11.8) and
the listed surface has no write for them.

Handlers are sync ``def`` on purpose: FastAPI runs them in the threadpool, so
a slow resolve_portfolio never blocks the event loop.
"""

from fastapi import APIRouter, Body, Depends, Response
from fastapi.responses import JSONResponse

from cyrus_pmg.pmgService.core.accessControl import requireAuth, requireEditor
from cyrus_pmg.pmgService.scenario import scenarioStore
from cyrus_pmg.pmgService.scenario.registry import getScenarioPort
from cyrus_pmg.pmgService.scenario.rules import exportFilename, validateBasis
from cyrus_pmg.pmgService.scenario.sleeves import sleeveExists
from cyrus_pmg.pmgService.scenario.types import (
    AnalyticsError,
    BasisInput,
    MandateInput,
    PortfolioKey,
    ScenarioNotFound,
    ValidationError,
)

router = APIRouter(dependencies=[Depends(requireAuth)])

_XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Build the port at import rather than on the first request, so any warm-up it
# does (the baked adapter pre-builds the static weight tables on a daemon
# thread) overlaps service start instead of the first user. Construction is
# cheap for every adapter - epsilonPhi's own setup stays lazy - and a
# misconfigured adapter must not stop the router loading, so failures wait to
# surface on the request that needs it.
try:
    getScenarioPort()
except Exception:                                     # noqa: BLE001 - reported per request
    pass


def _validationError(exc: ValidationError):
    return JSONResponse(status_code=422,
                        content={'error': exc.message, 'field': exc.field})


def _notFound(scenarioId):
    return JSONResponse(status_code=404, content={
        'error': 'Scenario {} is no longer available - scenarios are kept for '
                 '24 hours. Start a new one.'.format(scenarioId)})


def _analyticsError(exc: AnalyticsError):
    return JSONResponse(status_code=502, content={'error': str(exc)})


def _scenarioPayload(state: dict) -> dict:
    """A stored scenario as the wire shape of GET /scenario/{id}."""
    return {
        'id': state['id'],
        'mandate': state['mandate'],
        'basis': state['basis'],
        'base': state['base'],
        'comparisons': state['comparisons'],
        'sleeves': state['sleeves'],
    }


# ---------------------------------------------------------------- reads -----
# Static paths are declared before /scenario/{scenarioId} so they never match
# as an id.

@router.get('/scenario/schema')
def getScenarioSchema(currency: str = 'USD', hedging: str = 'Hedged',
                      mandateSize: float = None):
    """Field options, rules and the availability set (spec 3.4)."""
    basis = BasisInput(currency=currency, hedging=hedging)
    try:
        validateBasis(basis)
        mandate = None
        if mandateSize is not None:
            mandate = MandateInput(topAccountSize=mandateSize,
                                   mandateSize=mandateSize, primaryPwa='')
        return getScenarioPort().get_schema(basis, mandate)
    except ValidationError as exc:
        return _validationError(exc)
    except AnalyticsError as exc:
        return _analyticsError(exc)


@router.get('/scenario/advisors')
def searchAdvisors(q: str = '', limit: int = 20):
    """Primary PWA typeahead (spec 3.4)."""
    return {'advisors': list(getScenarioPort().search_advisors(q, limit))}


@router.get('/scenario/sleeves')
def listSleeves(category: str, currency: str = 'USD', hedging: str = 'Hedged'):
    """The sleeve library for one category (spec 3.4)."""
    basis = BasisInput(currency=currency, hedging=hedging)
    try:
        return {'category': category,
                'sleeves': list(getScenarioPort().list_sleeves(category, basis))}
    except AnalyticsError as exc:
        return _analyticsError(exc)


@router.get('/scenario/{scenarioId}')
def getScenario(scenarioId: str):
    """Rehydrate after a refresh (spec 11.8)."""
    try:
        return _scenarioPayload(scenarioStore.getScenario(scenarioId))
    except ScenarioNotFound:
        return _notFound(scenarioId)


# --------------------------------------------------------------- writes -----

@router.post('/scenario')
def createScenario(payload: dict = Body(...), user: str = Depends(requireEditor)):
    """Create a scenario from mandate + basis; returns its id (spec 3.4)."""
    port = getScenarioPort()
    try:
        mandate = MandateInput.fromDict(payload.get('mandate') or {})
        basis = BasisInput.fromDict(payload.get('basis') or {})
        validateBasis(basis)
        port.validate_mandate(mandate)
        state = scenarioStore.createScenario(mandate, basis)
        return {'id': state['id'], 'scenario': _scenarioPayload(state)}
    except ValidationError as exc:
        return _validationError(exc)


@router.put('/scenario/{scenarioId}')
def updateScenario(scenarioId: str, payload: dict = Body(...),
                   user: str = Depends(requireEditor)):
    """Persist mandate, basis or sleeve updates (deviation D2).

    Accepts any subset of {mandate, basis, sleeves}. Mandate updates are
    validated server-side; sleeve maps are checked against the library, and
    auto-attached categories are refused so a client bug cannot store one.
    """
    port = getScenarioPort()
    try:
        current = scenarioStore.getScenario(scenarioId)
        mandate = basis = sleeves = None
        if 'mandate' in payload:
            mandate = MandateInput.fromDict(payload['mandate'] or {})
            port.validate_mandate(mandate)
        if 'basis' in payload:
            basis = BasisInput.fromDict(payload['basis'] or {})
            validateBasis(basis)
        if 'sleeves' in payload:
            from cyrus_pmg.pmgService.scenario.rules import AUTO_SLEEVE_CATEGORIES
            sleeves = {}
            for category, name in (payload['sleeves'] or {}).items():
                if category in AUTO_SLEEVE_CATEGORIES:
                    raise ValidationError(
                        'sleeves',
                        '{} carries its sleeve automatically.'.format(category))
                if name is not None and not sleeveExists(category, name):
                    raise ValidationError(
                        'sleeves',
                        'No sleeve named {!r} for {}.'.format(name, category))
                sleeves[category] = name
        state = scenarioStore.updateScenario(
            scenarioId, mandate=mandate, basis=basis, sleeves=sleeves)
        return _scenarioPayload(state)
    except ScenarioNotFound:
        return _notFound(scenarioId)
    except ValidationError as exc:
        return _validationError(exc)


@router.post('/scenario/{scenarioId}/portfolio')
def resolvePortfolio(scenarioId: str, payload: dict = Body(...),
                     user: str = Depends(requireEditor)):
    """Resolve one portfolio - THE EXPENSIVE CALL (spec 3.4).

    Body: {"key": {allocation, excludeRE, excludeTAA, riskLevel},
           "role": "base" | "comparison"}. The resolved column is recorded on
    the scenario so a refresh restores it.
    """
    port = getScenarioPort()
    try:
        state = scenarioStore.getScenario(scenarioId)
        key = PortfolioKey.fromDict(payload.get('key'))
        role = payload.get('role') or 'comparison'
        if role not in ('base', 'comparison'):
            raise ValidationError('role', 'role must be base or comparison.')
        basis = BasisInput.fromDict(state['basis'])
        try:
            result = port.resolve_portfolio(basis, key)
        except LookupError as exc:
            # The availability set should have prevented this selection.
            raise ValidationError('key', str(exc))
        scenarioStore.recordColumn(scenarioId, key, role)
        return {'portfolio': result}
    except ScenarioNotFound:
        return _notFound(scenarioId)
    except ValidationError as exc:
        return _validationError(exc)
    except AnalyticsError as exc:
        return _analyticsError(exc)


@router.delete('/scenario/{scenarioId}/portfolio/{portfolioKey:path}')
def removePortfolio(scenarioId: str, portfolioKey: str,
                    user: str = Depends(requireEditor)):
    """Remove a comparison column (spec 3.4). Idempotent."""
    try:
        key = PortfolioKey.fromStr(portfolioKey)
        scenarioStore.removeColumn(scenarioId, key)
        return {'removed': key.toStr()}
    except ScenarioNotFound:
        return _notFound(scenarioId)
    except ValidationError as exc:
        return _validationError(exc)


@router.post('/scenario/{scenarioId}/export')
def exportScenario(scenarioId: str, user: str = Depends(requireEditor)):
    """The Excel workbook (spec 14). Assembled from stored scenario state.

    Refuses (422) while a category lacks a sleeve - the UI disables the
    button, the server re-enforces. Column payloads are re-resolved through
    the port, which is cheap when its caches are warm and correct when not.
    """
    port = getScenarioPort()
    try:
        state = scenarioStore.getScenario(scenarioId)
        if not state['base']:
            raise ValidationError('base', 'No base portfolio to implement.')
        basis = BasisInput.fromDict(state['basis'])
        mandate = MandateInput.fromDict(state['mandate'])

        keys = [state['base']] + list(state['comparisons'])
        results = [port.resolve_portfolio(basis, PortfolioKey.fromStr(k))
                   for k in keys]

        from cyrus_pmg.pmgService.scenario.rules import AUTO_SLEEVE_CATEGORIES
        sleeves = state['sleeves'] or {}
        missing = [c['name'] for c in results[0]['categories']
                   if c['name'] not in AUTO_SLEEVE_CATEGORIES
                   and not sleeves.get(c['name'])]
        if missing:
            raise ValidationError(
                'sleeves',
                'Attach a sleeve to every category first - missing: {}.'.format(
                    ', '.join(missing)))

        content = port.build_export(basis, mandate, results,
                                    {'sleeves': sleeves})
        filename = exportFilename(basis)
        return Response(
            content=content,
            media_type=_XLSX,
            headers={'Content-Disposition':
                     'attachment; filename="{}"'.format(filename)},
        )
    except ScenarioNotFound:
        return _notFound(scenarioId)
    except ValidationError as exc:
        return _validationError(exc)
    except AnalyticsError as exc:
        return _analyticsError(exc)
