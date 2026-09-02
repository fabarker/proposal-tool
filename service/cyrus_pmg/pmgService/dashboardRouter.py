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

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse

from cyrus_pmg.pmgService.core.accessControl import (
    getKerberosFromFastApiRequest, isAdmin, requireAdmin, requireAuth, requireEditor)
from cyrus_pmg.pmgService.scenario import fees, products, scenarioStore, sleeveRepo
from cyrus_pmg.pmgService.scenario.registry import getScenarioPort
from cyrus_pmg.pmgService.scenario.rules import (
    exportFilename, validateBasis, validateFeeLevel, validateFeeSchedule,
    validateKey, validateVariant)
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
# cheap for every adapter - the engine's own setup stays lazy - and a
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


def _includeFees(state: dict) -> bool:
    """Whether the proposal shows fees at all (D52).

    Absent means a scenario stored before the toggle existed. One that had
    already chosen a schedule was showing fees, and keeps showing them; one
    that never chose is left as a new scenario starts, with fees off.
    """
    if 'includeFees' in state:
        return bool(state['includeFees'])
    return bool(state.get('feeSchedule'))


def _scenarioPayload(state: dict) -> dict:
    """A stored scenario as the wire shape of GET /scenario/{id}."""
    return {
        'id': state['id'],
        'mandate': state['mandate'],
        'basis': state['basis'],
        'base': state['base'],
        'comparisons': state['comparisons'],
        'variant': state.get('variant'),
        'tacticalTilt': bool(state.get('tacticalTilt', True)),
        'volPremium': bool(state.get('volPremium', True)),
        'sleeves': state['sleeves'],
        'includeFees': _includeFees(state),
        'feeSchedule': state.get('feeSchedule'),
        'feeLevel': state.get('feeLevel'),
    }


# ---------------------------------------------------------------- reads -----
# Static paths are declared before /scenario/{scenarioId} so they never match
# as an id.

@router.get('/scenario/schema')
def getScenarioSchema(request: Request, currency: str = 'USD', hedging: str = 'Hedged',
                      mandateSize: float = None, variant: str = None,
                      topAccountSize: float = None):
    """Field options, rules and the availability set (spec 3.4).

    *variant* narrows ``allocations`` and ``availability`` (D49). It is
    optional because the schema is fetched once before a variant is chosen;
    absent, no variant filter applies and the picker stays disabled
    client-side until one is set.

    *mandateSize* gates the $20m private-assets rule and *topAccountSize*
    sets the fee tier (D51). Each binds only when given: a schema fetched
    before the mandate exists carries every allocation and no fee rates.
    """
    basis = BasisInput(currency=currency, hedging=hedging)
    try:
        validateBasis(basis)
        mandate = None
        if mandateSize is not None or topAccountSize is not None:
            mandate = MandateInput(topAccountSize=topAccountSize,
                                   mandateSize=mandateSize, primaryPwa='')
        schema = dict(getScenarioPort().get_schema(basis, mandate, variant))
        # Who is asking decides one capability (D57). Copied before it is
        # stamped: the port may hand back a cached dict, and one caller's
        # admin flag must not be the next caller's.
        capabilities = dict(schema.get('capabilities') or {})
        capabilities['canAdmin'] = isAdmin(getKerberosFromFastApiRequest(request))
        schema['capabilities'] = capabilities
        return schema
    except ValidationError as exc:
        return _validationError(exc)
    except AnalyticsError as exc:
        return _analyticsError(exc)


# ---- the rate card (D55) ------------------------------------------------
# Read only: the card is what the delivering team sent, and it changes by
# delivery (feeTools --accept), never through the service. Static paths,
# declared before /scenario/{scenarioId} like the others.

@router.get('/scenario/fees')
def getFeeCard(tier: str = None, topAccountSize: float = None, whole: bool = False):
    """The card: every cell at every tier when *whole*, else one tier - by id,
    or the tier a top account size falls in. Each cell carries the delivered
    rate beside the rate in force and whether they differ. The panel takes the
    whole card and pivots it locally."""
    try:
        if whole:
            return fees.card()
        if not tier:
            if topAccountSize is None:
                raise ValidationError('tier', 'Give a tier id, a top account size, or whole=1.')
            tier = fees.tierFor(topAccountSize)['id']
        return fees.grid(tier)
    except (KeyError, ValueError) as exc:
        return JSONResponse(status_code=422, content={'error': str(exc), 'field': 'tier'})
    except ValidationError as exc:
        return _validationError(exc)


@router.get('/scenario/advisors')
def searchAdvisors(q: str = '', limit: int = 20):
    """Primary PWA typeahead (spec 3.4)."""
    return {'advisors': list(getScenarioPort().search_advisors(q, limit))}


@router.get('/scenario/sleeves')
def listSleeves(category: str, variant: str = None, currency: str = 'USD',
                hedging: str = 'Hedged'):
    """The sleeve library for one category, under one variant (spec 3.4, D29).

    *variant* is required. It is declared optional only so that an absent one
    reaches validateVariant and comes back as a 422 naming the field, rather
    than as FastAPI's own unfielded "Malformed request." Serving a default
    library to a caller that has not chosen a variant is how the wrong
    products reach the wrong book, so it is never a fallback.
    """
    basis = BasisInput(currency=currency, hedging=hedging)
    try:
        validateVariant(variant)
        return {'category': category, 'variant': variant,
                'sleeves': list(getScenarioPort().list_sleeves(
                    category, basis, variant))}
    except ValidationError as exc:
        return _validationError(exc)
    except AnalyticsError as exc:
        return _analyticsError(exc)
    except products.BadCatalogue as exc:
        # the delivered catalogue cannot be read: the column shows the
        # library error state with Retry, the same as an analytics failure
        return JSONResponse(status_code=502, content={'error': str(exc)})


# ---- the sleeve repository (D57) ------------------------------------------
# Admin only, reads included: the console is the repository's only client,
# and the whole catalogue is not something an editor has a use for on its
# own. Static paths, declared before /scenario/{scenarioId} like the others.
# Every write goes through the repository's own validation and comes back
# as the {error, field} body the page already knows how to show.

@router.get('/scenario/repository')
def getRepository(user: str = Depends(requireAdmin)):
    """Everything the console needs in one fetch: the types, the
    categories, every sleeve (broken ones included, with their problems
    and provenance), the whole catalogue, the products sleeves reference
    that the catalogue no longer carries (D58), and where both came from."""
    try:
        return {
            'variants': list(sleeveRepo.VARIANTS),
            'categories': sleeveRepo.categories(),
            'fixedCategories': sleeveRepo.fixedCategories(),
            'sleeves': sleeveRepo.listAll(),
            'orphans': sleeveRepo.orphanProducts(),
            'products': products.all(),
            'catalogue': products.describeSource(),
            'store': sleeveRepo.describe(),
            'user': user,
        }
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


@router.post('/scenario/repository/sleeves')
def createRepositorySleeve(payload: dict = Body(...), user: str = Depends(requireAdmin)):
    """Build a sleeve: {category, name, note, products: [{productId, weight}]},
    weights as fractions summing to 1, under `variants` (a list) or `variant`.

    Several types in one call is how the console both creates a sleeve for a
    set of books and copies an existing one into another (D61). All or
    nothing: a name that clashes in any of them writes none of them.
    """
    try:
        variants = payload.get('variants')
        if not variants:
            variants = [payload.get('variant')] if payload.get('variant') else []
        made = sleeveRepo.createSleeves(
            variants, payload.get('category'), payload.get('name'),
            payload.get('products') or [], note=payload.get('note', ''), user=user)
        return {'sleeves': made, 'sleeve': made[0]}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


@router.put('/scenario/repository/sleeves/{sleeveId}')
def updateRepositorySleeve(sleeveId: int, payload: dict = Body(...),
                           user: str = Depends(requireAdmin)):
    """Rename, re-note or re-weight a sleeve. Its type and category are
    fixed at creation - a sleeve moved between them is a different sleeve."""
    try:
        sleeve = sleeveRepo.updateSleeve(
            sleeveId, payload.get('name'), payload.get('products') or [],
            note=payload.get('note', ''), user=user)
        return {'sleeve': sleeve}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


@router.delete('/scenario/repository/sleeves/{sleeveId}')
def deleteRepositorySleeve(sleeveId: int, user: str = Depends(requireAdmin)):
    """Retire a sleeve. Refused for a fixed category, which always holds one.
    A scenario still naming it keeps the name and shows the picker's
    'no longer offered' state until a PWA re-picks - never a substitution."""
    try:
        return {'deleted': sleeveRepo.deleteSleeve(sleeveId, user=user)}
    except ValidationError as exc:
        return _validationError(exc)


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
    """Persist mandate, basis, variant or sleeve updates (deviations D2, D29).

    Accepts any subset of {mandate, basis, variant, tacticalTilt, volPremium,
    sleeves, includeFees, feeSchedule, feeLevel}. Mandate updates are validated server-side; sleeve
    maps are checked against the library, and auto-attached categories are
    refused so a client bug cannot store one. The fee schedule and level are
    checked against the framework's lists (D51).

    Sleeve names are checked against the variant in force *after* this update,
    not the stored one, so a client may change variant and choose sleeves from
    the new library in a single write. Where only the variant moves, the store
    clears the sleeve map: the old names were chosen from a library this
    variant may not offer.
    """
    port = getScenarioPort()
    try:
        current = scenarioStore.getScenario(scenarioId)
        mandate = basis = sleeves = variant = None
        tacticalTilt = feeSchedule = feeLevel = includeFees = None
        volPremium = None
        if 'mandate' in payload:
            mandate = MandateInput.fromDict(payload['mandate'] or {})
            port.validate_mandate(mandate)
        if 'basis' in payload:
            basis = BasisInput.fromDict(payload['basis'] or {})
            validateBasis(basis)
        if 'variant' in payload:
            variant = payload['variant']
            validateVariant(variant)
        if 'tacticalTilt' in payload:
            tacticalTilt = bool(payload['tacticalTilt'])
        if 'volPremium' in payload:
            volPremium = bool(payload['volPremium'])
        if 'includeFees' in payload:
            includeFees = bool(payload['includeFees'])
        if 'feeSchedule' in payload:
            feeSchedule = payload['feeSchedule']
            validateFeeSchedule(feeSchedule)
        if 'feeLevel' in payload:
            feeLevel = payload['feeLevel']
            validateFeeLevel(feeLevel)
        if 'sleeves' in payload:
            from cyrus_pmg.pmgService.scenario.rules import AUTO_SLEEVE_CATEGORIES
            against = variant or current.get('variant')
            if not against:
                raise ValidationError(
                    'variant',
                    'Choose an implementation type before attaching sleeves.')
            sleeves = {}
            for category, name in (payload['sleeves'] or {}).items():
                if category in AUTO_SLEEVE_CATEGORIES:
                    raise ValidationError(
                        'sleeves',
                        '{} carries its sleeve automatically.'.format(category))
                if name is not None and not sleeveExists(category, name, against):
                    # *category* is already the group name where there is one:
                    # the page sends what it picked under (D60)
                    raise ValidationError(
                        'sleeves',
                        'No sleeve named {!r} for {} under {}.'.format(
                            name, category, against))
                sleeves[category] = name
        state = scenarioStore.updateScenario(
            scenarioId, mandate=mandate, basis=basis, sleeves=sleeves,
            variant=variant, tacticalTilt=tacticalTilt,
            feeSchedule=feeSchedule, feeLevel=feeLevel,
            includeFees=includeFees, volPremium=volPremium)
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
        # the key carries its currency (D54); a column can only be built in the
        # scenario's own, and the picker never offers another
        if key.currency != basis.currency:
            raise ValidationError(
                'currency', 'A {} portfolio cannot be added to a {} scenario.'.format(
                    key.currency, basis.currency))
        # The variant governs what may be built at all, so it is checked
        # before the expensive call rather than after it (D49).
        mandateSize = (state.get('mandate') or {}).get('mandateSize')
        validateKey(key, state.get('variant'), mandateSize)
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

    Refuses (422) while no variant is chosen, a category lacks a sleeve, or
    the proposal includes fees and no schedule is chosen - the UI disables the
    button, the server re-enforces. The fee level always has a value (the
    store defaults it) but is validated all the same, since it is read back
    from a file. Column payloads are
    re-resolved through the port, which is cheap when its caches are warm and
    correct when not.
    """
    port = getScenarioPort()
    try:
        state = scenarioStore.getScenario(scenarioId)
        if not state['base']:
            raise ValidationError('base', 'No base portfolio to implement.')
        variant = state.get('variant')
        validateVariant(variant)
        # A proposal that excludes fees needs no schedule to export, and the
        # sheet it produces carries no fee column to price (D52). One that
        # includes them must have chosen one: there is no default.
        includeFees = _includeFees(state)
        feeSchedule = state.get('feeSchedule')
        feeLevel = state.get('feeLevel')
        if includeFees:
            validateFeeSchedule(feeSchedule)
            validateFeeLevel(feeLevel)
        basis = BasisInput.fromDict(state['basis'])
        mandate = MandateInput.fromDict(state['mandate'])

        keys = [state['base']] + list(state['comparisons'])
        results = [port.resolve_portfolio(basis, PortfolioKey.fromStr(k))
                   for k in keys]

        from cyrus_pmg.pmgService.scenario.rules import (
            AUTO_SLEEVE_CATEGORIES, sleeveCategory)
        sleeves = state['sleeves'] or {}
        # one choice per sleeve category: grouped categories share theirs (D60)
        missing = []
        for c in results[0]['categories']:
            if c['name'] in AUTO_SLEEVE_CATEGORIES:
                continue
            under = sleeveCategory(c['name'])
            if not sleeves.get(under) and under not in missing:
                missing.append(under)
        if missing:
            raise ValidationError(
                'sleeves',
                'Attach a sleeve to every category first - missing: {}.'.format(
                    ', '.join(missing)))

        content = port.build_export(basis, mandate, results,
                                    {'sleeves': sleeves, 'variant': variant,
                                     'tacticalTilt': bool(state.get('tacticalTilt', True)),
                                     'volPremium': bool(state.get('volPremium', True)),
                                     'includeFees': includeFees,
                                     'feeSchedule': feeSchedule, 'feeLevel': feeLevel})
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
