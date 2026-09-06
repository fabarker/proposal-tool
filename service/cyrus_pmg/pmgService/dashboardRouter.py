"""Dashboard API router - the host's ``pmgService/dashboardRouter.py``.

In the host this file already exists and carries every dashboard endpoint; the
Proposal Tool TRANSPLANTS the scenario endpoint block below into it verbatim.
In this mirror the file carries only that block, written exactly as it will
appear in the host: mixedCase handler names, the whole proposal flow inheriting
the router-level ``requireAuth`` - an allowlisted PWA creates, exports and
requests with no PERMIT role (D77) - the repository taking
``Depends(requireAdmin)``, non-2xx JSON
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
    isAdmin, requireAdmin, requireAuth)
from cyrus_pmg.pmgService.scenario import (accountRequests, fees, products, proposalRegister,
                                           scenarioStore, sleeveRepo)
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

# ===== TRANSPLANT BLOCK BEGIN (PORTING.md §9.1) ==============================
# Everything from here to TRANSPLANT BLOCK END is pasted into the host's
# dashboardRouter.py unchanged. The imports above it are merged into the
# host's import section; the `router =` line above is NOT copied - the
# host's router already exists and is already mounted under /api/v1.

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
                      topAccountSize: float = None,
                      caller=Depends(requireAuth)):
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
        capabilities['canAdmin'] = isAdmin(caller)
        schema['capabilities'] = capabilities
        # The account opening form's option lists ride the schema, so the
        # page renders what the server says and never lists of its own (D76).
        # Copied for the same reason as the capabilities.
        options = dict(schema.get('options') or {})
        options['accountRequest'] = accountRequests.options()
        schema['options'] = options
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
def getFeeCard(tier: str = None, topAccountSize: float = None, whole: bool = False,
               mandateSize: float = None, schedule: str = None):
    """The card: every cell at every tier when *whole*, else one tier - by id,
    or the tier a top account size falls in. Each cell carries the delivered
    rate beside the rate in force and whether they differ. The panel takes the
    whole card and pivots it locally.

    With *mandateSize* it answers instead with the marginal build-up behind a
    blended rate (D83): the bands that amount fills, the rate in each, and the
    blend at every level. *schedule* picks which marginally-priced schedule,
    defaulting to the only one there is.
    """
    try:
        if mandateSize is not None:
            marginal = [s for s in fees.SCHEDULES if fees.isMarginal(s)]
            if not marginal:
                raise ValidationError('schedule', 'No schedule is priced marginally.')
            chosen = schedule or marginal[0]
            if chosen not in marginal:
                raise ValidationError(
                    'schedule', '{} is not priced marginally; it reads one tier.'.format(chosen))
            return fees.marginalPayload(chosen, mandateSize)
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
def getRepository(caller=Depends(requireAdmin)):
    """Everything the console needs in one fetch: the types, the
    categories, every sleeve (broken ones included, with their problems
    and provenance), the sleeves that have been archived out of the library
    but kept on the record (D65), the whole catalogue, the products sleeves
    reference that the catalogue no longer carries (D58), and where both
    came from."""
    try:
        return {
            'variants': list(sleeveRepo.VARIANTS),
            'categories': sleeveRepo.categories(),
            'fixedCategories': sleeveRepo.fixedCategories(),
            'sleeves': sleeveRepo.listAll(),
            'archived': sleeveRepo.listArchived(),
            'orphans': sleeveRepo.orphanProducts(),
            'products': products.all(),
            'catalogue': products.describeSource(),
            'store': sleeveRepo.describe(),
            'register': proposalRegister.describe(),
            'user': caller.kerberos,
        }
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


@router.post('/scenario/repository/sleeves')
def createRepositorySleeve(payload: dict = Body(...), caller=Depends(requireAdmin)):
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
            payload.get('products') or [], note=payload.get('note', ''), user=caller.kerberos)
        return {'sleeves': made, 'sleeve': made[0]}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


@router.put('/scenario/repository/sleeves/{sleeveId}')
def updateRepositorySleeve(sleeveId: int, payload: dict = Body(...),
                           caller=Depends(requireAdmin)):
    """Rename, re-note or re-weight a sleeve. Its type and category are
    fixed at creation - a sleeve moved between them is a different sleeve."""
    try:
        sleeve = sleeveRepo.updateSleeve(
            sleeveId, payload.get('name'), payload.get('products') or [],
            note=payload.get('note', ''), user=caller.kerberos)
        return {'sleeve': sleeve}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


@router.delete('/scenario/repository/sleeves/{sleeveId}')
def deleteRepositorySleeve(sleeveId: int, caller=Depends(requireAdmin)):
    """Archive a sleeve out of the library. Refused for a fixed category,
    which always holds one. The sleeve is not destroyed (D65): it keeps its
    whole history, appears under the console's Archive, and can be restored.
    A scenario still naming it keeps the name and shows the picker's
    'no longer offered' state until a PWA re-picks - never a substitution."""
    try:
        return {'deleted': sleeveRepo.deleteSleeve(sleeveId, user=caller.kerberos)}
    except ValidationError as exc:
        return _validationError(exc)


@router.get('/scenario/repository/sleeves/{sleeveId}/history')
def getRepositorySleeveHistory(sleeveId: int, caller=Depends(requireAdmin)):
    """Every revision of one sleeve, newest first, each carrying the whole
    sleeve as it stood plus what moved since the one before it (D65).

    Answers for a removed sleeve exactly as for a live one - an empty list
    means no such sleeve, not a sleeve with nothing to show."""
    return {'sleeveId': sleeveId, 'history': sleeveRepo.history(sleeveId)}


@router.post('/scenario/repository/sleeves/restore')
def restoreRepositorySleeves(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Put several archived sleeves back at once: {ids: [...]}. All or
    nothing, and checked against itself as well as the library (D66)."""
    try:
        made = sleeveRepo.restoreSleeves(payload.get('ids') or [], user=caller.kerberos)
        return {'sleeves': made}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


def _feedFilters(actions: str = '', variant: str = '', category: str = '', actor: str = '',
                 since: str = '', until: str = '', q: str = '') -> dict:
    """The feed's query string, as the repository's keyword arguments. Actions
    arrive comma-joined because a chip set is one parameter, not a list."""
    return {
        'actions': [a for a in (actions or '').split(',') if a],
        'variant': variant or None, 'category': category or None, 'actor': actor or None,
        'since': since or None, 'until': until or None, 'query': q or '',
    }


@router.get('/scenario/repository/activity')
def getRepositoryActivity(actions: str = '', variant: str = '', category: str = '',
                          actor: str = '', since: str = '', until: str = '', q: str = '',
                          limit: int = 100, before: str = '',
                          caller=Depends(requireAdmin)):
    """A page of the record as a feed (D66): every revision across every
    sleeve, newest first, with the counts that frame it. `before` is the
    cursor a previous page handed back as `next`."""
    filters = _feedFilters(actions, variant, category, actor, since, until, q)
    return sleeveRepo.activity(limit=limit, before=before or None, **filters)


def _csv(columns, rows, filename: str) -> Response:
    import csv
    import io as _io
    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return Response(content=buf.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="{}"'.format(filename)})


@router.get('/scenario/repository/archive.csv')
def exportRepositoryArchive(caller=Depends(requireAdmin)):
    """The archive as a table: one row per archived sleeve, what it held."""
    return _csv(sleeveRepo.ARCHIVE_COLUMNS, sleeveRepo.archiveRows(), 'sleeve-archive.csv')


@router.get('/scenario/repository/activity.csv')
def exportRepositoryActivity(actions: str = '', variant: str = '', category: str = '',
                             actor: str = '', since: str = '', until: str = '', q: str = '',
                             caller=Depends(requireAdmin)):
    """The feed under the same filters, every page of it, as a table."""
    filters = _feedFilters(actions, variant, category, actor, since, until, q)
    return _csv(sleeveRepo.ACTIVITY_COLUMNS, sleeveRepo.activityRows(**filters),
                'sleeve-activity.csv')


def _registerFilters(exportedBy: str = '', primaryPwa: str = '', currency: str = '',
                     variant: str = '', since: str = '', until: str = '', q: str = '') -> dict:
    return {'exportedBy': exportedBy or None, 'primaryPwa': primaryPwa or None,
            'currency': currency or None, 'variant': variant or None,
            'since': since or None, 'until': until or None, 'query': q or ''}


@router.get('/scenario/repository/proposals')
def listRegisterProposals(exportedBy: str = '', primaryPwa: str = '', currency: str = '',
                          variant: str = '', since: str = '', until: str = '', q: str = '',
                          limit: int = 100, before: str = '',
                          caller=Depends(requireAdmin)):
    """A page of the proposal register (D69): every delivered proposal, newest
    first, with the counts that frame it. `before` is the cursor a previous
    page handed back as `next`. No blob travels with a list."""
    filters = _registerFilters(exportedBy, primaryPwa, currency, variant, since, until, q)
    return proposalRegister.listProposals(limit=limit, before=before or None, **filters)


@router.get('/scenario/repository/proposals.csv')
def exportRegisterProposals(exportedBy: str = '', primaryPwa: str = '', currency: str = '',
                            variant: str = '', since: str = '', until: str = '', q: str = '',
                            caller=Depends(requireAdmin)):
    """The register as a table under the same filters, every page of it."""
    filters = _registerFilters(exportedBy, primaryPwa, currency, variant, since, until, q)
    return _csv(proposalRegister.EXPORT_COLUMNS, proposalRegister.exportRows(**filters),
                'proposal-register.csv')


@router.get('/scenario/repository/proposals/{proposalId}')
def getRegisterProposal(proposalId: str, caller=Depends(requireAdmin)):
    """One proposal: both pictures, the sleeve pins with where the library is
    now, and the workbook's name, size and hash - but not its bytes."""
    entry = proposalRegister.getProposal(proposalId)
    if entry is None:
        return JSONResponse(status_code=404, content={'error': 'No proposal {}.'.format(proposalId)})
    return {'proposal': entry}


@router.get('/scenario/repository/proposals/{proposalId}/workbook')
def downloadRegisterWorkbook(proposalId: str, caller=Depends(requireAdmin)):
    """The delivered workbook, byte for byte, with its hash in a header so
    'this is the file they received' is checkable rather than asserted."""
    found = proposalRegister.workbook(proposalId)
    if found is None:
        return JSONResponse(status_code=404, content={'error': 'No proposal {}.'.format(proposalId)})
    return Response(content=found['bytes'], media_type=_XLSX,
                    headers={'Content-Disposition': 'attachment; filename="{}"'.format(found['name']),
                             'X-Workbook-SHA256': found['sha']})


@router.post('/scenario/repository/sleeves/{sleeveId}/restore')
def restoreRepositorySleeve(sleeveId: int, caller=Depends(requireAdmin)):
    """Put a removed sleeve back. Re-validated on the way in: the name may
    have been taken since, and a product it holds may have left the
    catalogue."""
    try:
        return {'sleeve': sleeveRepo.restoreSleeve(sleeveId, user=caller.kerberos)}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


@router.post('/scenario/repository/sleeves/{sleeveId}/revert')
def revertRepositorySleeve(sleeveId: int, payload: dict = Body(...),
                           caller=Depends(requireAdmin)):
    """Put an earlier revision back in force: {revision}. Appends a new
    revision rather than rewinding - the revert is itself on the record."""
    try:
        number = int(payload.get('revision', 0))
    except (TypeError, ValueError):
        return _validationError(ValidationError('revision', 'Choose a revision to restore.'))
    try:
        return {'sleeve': sleeveRepo.revertSleeve(sleeveId, number, user=caller.kerberos)}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


# ---- account opening requests (D76) --------------------------------------
# The landing card's third button opens a form that fills itself from a
# Proposal UID (D75) and records a request against that proposal. Static
# paths, declared before /scenario/{scenarioId} like the others. Lookup and
# Submit both inherit the router-level requireAuth: they are a PWA's own
# actions (D77).

_UID_HINT = ('A Proposal UID is pr_ followed by twelve letters or digits: the last part of '
             "the workbook's name, and cell B1 of its Implementation sheet.")


@router.get('/scenario/proposals/{proposalId}')
def lookupProposal(proposalId: str):
    """The proposal behind a UID, as the account opening form shows it, and
    any request already recorded against it. 422 for something that is not a
    UID and 404 for a UID the register does not hold, both naming the field,
    so the form can put the message on the box it belongs to."""
    uid = (proposalId or '').strip().lower()
    if not proposalRegister.isProposalId(uid):
        return _validationError(ValidationError('proposalId', _UID_HINT))
    try:
        return accountRequests.lookup(uid)
    except accountRequests.ProposalNotFound:
        return JSONResponse(status_code=404, content={
            'error': "No proposal has the UID {}. Check the end of the workbook's name or cell "
                     'B1 of its Implementation sheet.'.format(uid),
            'field': 'proposalId'})


@router.post('/scenario/account-requests')
def createAccountRequest(payload: dict = Body(...), caller=Depends(requireAuth)):
    """Record an account opening request on the terms of a delivered proposal.
    One per proposal: a second is refused naming the first (D76)."""
    try:
        return {'request': accountRequests.record(caller.kerberos, payload)}
    except accountRequests.ProposalNotFound as exc:
        return JSONResponse(status_code=404, content={
            'error': 'No proposal has the UID {}.'.format(exc), 'field': 'proposalId'})
    except ValidationError as exc:
        return _validationError(exc)


@router.get('/scenario/account-requests/{requestId}')
def getAccountRequest(requestId: str):
    found = accountRequests.get(requestId)
    if found is None:
        return JSONResponse(status_code=404, content={
            'error': 'No account opening request {}.'.format(requestId)})
    return {'request': found}


@router.get('/scenario/{scenarioId}')
def getScenario(scenarioId: str):
    """Rehydrate after a refresh (spec 11.8)."""
    try:
        return _scenarioPayload(scenarioStore.getScenario(scenarioId))
    except ScenarioNotFound:
        return _notFound(scenarioId)


# --------------------------------------------------------------- writes -----

@router.post('/scenario')
def createScenario(payload: dict = Body(...), caller=Depends(requireAuth)):
    """Create a scenario from mandate + basis; returns its id (spec 3.4)."""
    port = getScenarioPort()
    try:
        mandate = MandateInput.fromDict(payload.get('mandate') or {})
        basis = BasisInput.fromDict(payload.get('basis') or {})
        validateBasis(basis)
        port.validate_mandate(mandate)
        state = scenarioStore.createScenario(mandate, basis, createdBy=caller.kerberos)
        return {'id': state['id'], 'scenario': _scenarioPayload(state)}
    except ValidationError as exc:
        return _validationError(exc)


@router.put('/scenario/{scenarioId}')
def updateScenario(scenarioId: str, payload: dict = Body(...),
                   caller=Depends(requireAuth)):
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
                     caller=Depends(requireAuth)):
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
                    caller=Depends(requireAuth)):
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
def exportScenario(scenarioId: str, caller=Depends(requireAuth)):
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
        from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows
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

        implementation = {'sleeves': sleeves, 'variant': variant,
                          'tacticalTilt': bool(state.get('tacticalTilt', True)),
                          'volPremium': bool(state.get('volPremium', True)),
                          'includeFees': includeFees,
                          'feeSchedule': feeSchedule, 'feeLevel': feeLevel}
        # The implemented model is built ONCE here and handed to both the
        # writer and the register, so the sheet a client receives and the
        # record kept of it are the same model rather than two builds (D69).
        # An unpriced proposal is built unpriced whatever schedule the
        # scenario happens to remember (D52) - the writer does the same.
        model = buildImplementationRows(
            results[0], sleeves, AUTO_SLEEVE_CATEGORIES, mandate.mandateSize, variant,
            implementation['tacticalTilt'], feeSchedule if includeFees else None, feeLevel,
            mandate.topAccountSize, implementation['volPremium'], basis.currency)
        # A position smaller than the product will accept is not a position:
        # the export refuses while any survives, so the UI's block cannot be
        # walked past by calling the endpoint directly (item 3).
        if model['breaches']:
            raise ValidationError('minimumInvestment',
                                  'Below mandate minimum: {}. Raise the mandate, change the '
                                  'sleeve, or drop the product before exporting.'.format(
                                      ', '.join('{} in {} (${:,.0f} against a ${:,.0f} minimum)'.format(
                                          b['name'], b['category'], b['notional'],
                                          b['minimumInvestment']) for b in model['breaches'][:4])
                                      + ('' if len(model['breaches']) <= 4
                                         else ', and {} more'.format(len(model['breaches']) - 4))))

        # The Proposal UID is minted here, before the workbook exists, so the
        # one id is written into the file, into its name and into the register
        # row. The register refuses the row if any of the three differ (D75).
        proposalId = proposalRegister.newProposalId()
        content = port.build_export(basis, mandate, results,
                                    dict(implementation, model=model, proposalId=proposalId))
        filename = exportFilename(basis, proposalId)
        # Record first, deliver second. A proposal that could not be written
        # down is not delivered - the register is worth nothing with holes in
        # it, and this write is one insert of ~30 KB into a local file.
        proposalRegister.record(proposalId, scenarioId, caller.kerberos, state.get('createdBy', ''),
                                basis, mandate, results, implementation, model, content, filename)
        return Response(
            content=content,
            media_type=_XLSX,
            headers={'Content-Disposition':
                     'attachment; filename="{}"'.format(filename),
                     'X-Proposal-Id': proposalId},
        )
    except ScenarioNotFound:
        return _notFound(scenarioId)
    except ValidationError as exc:
        return _validationError(exc)
    except AnalyticsError as exc:
        return _analyticsError(exc)

# ===== TRANSPLANT BLOCK END ==================================================
