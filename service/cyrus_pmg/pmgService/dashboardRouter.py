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

import io
import json
import zipfile

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse

from cyrus_pmg.pmgService.core.accessControl import (
    isAdmin, requireAdmin, requireAuth)
from cyrus_pmg.pmgService.scenario import (accountRequests, customAccess, customPortfolio,
                                           customSleeves, feeCard, fees,
                                           fundingSplit, overlayRules, products,
                                           otherCharges as _otherCharges,
                                           proposalRegister, repositoryFeed, scenarioStore,
                                           sleeveRepo, sleeveRules)
from cyrus_pmg.pmgService.scenario.registry import getScenarioPort
from cyrus_pmg.pmgService.scenario.rules import (
    exportFilename, validateBasis, validateCustomFees, validateFeeLevel, validateFeeSchedule,
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

def _baseKeyFrom(keyStr):
    """A base portfolio key string as a PortfolioKey, or None for none; a
    malformed one is a 422 naming the field, like every other bad input."""
    if not keyStr:
        return None
    try:
        return PortfolioKey.fromStr(keyStr)
    except (ValueError, TypeError) as exc:
        raise ValidationError('key', 'Not a portfolio key: {!r}.'.format(keyStr))


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
    # a sleeve, a product or the fee card saved by someone else since the
    # editor opened it (D156, D165, D166)
    if isinstance(exc, (sleeveRepo.StaleError, products.StaleError, feeCard.StaleError)):
        return JSONResponse(status_code=409, content={'error': exc.message, 'field': exc.field})
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
        'customFees': state.get('customFees') or {},
        # the charges the PWA quotes beside the fees (D177)
        'otherCharges': _otherCharges.withDefaults(state.get('otherCharges')),
        # the proposal's own sleeves, by the name they are chosen under (D170)
        'customSleeves': state.get('customSleeves') or {},
        # a proposal built product by product, and whether it is in force (D171)
        'customPortfolio': state.get('customPortfolio') or None,
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
        # ...and who they are, so the landing page can say whose session this
        # is beside what the role opens (D106). The repository payload already
        # carried it, but that is only fetched once the console is open.
        capabilities['user'] = caller.kerberos
        schema['capabilities'] = capabilities
        # The account opening form's option lists ride the schema, so the
        # page renders what the server says and never lists of its own (D76).
        # Copied for the same reason as the capabilities.
        options = dict(schema.get('options') or {})
        options['accountRequest'] = accountRequests.options()
        schema['options'] = options
        # Where this caller may build a custom sleeve (D170): every category
        # a sleeve is picked for, for an admin always and for a PWA while an
        # admin has opened the custom workflows to PWAs (D172); otherwise none.
        # The server checks again on every save.
        settings = customSleeves.describe()
        schema['customSleeves'] = {
            'mayBuild': (list(settings['pickable']) if customAccess.may(capabilities['canAdmin'])
                         else []),
            'nameMax': settings['nameMax'], 'noteMax': settings['noteMax']}
        # Under which types this caller may build a custom portfolio, and the
        # limits a build is checked against (D171). Checked again on save.
        schema['customPortfolio'] = customPortfolio.forCaller(capabilities['canAdmin'])
        return schema
    except ValidationError as exc:
        return _validationError(exc)
    except AnalyticsError as exc:
        return _analyticsError(exc)


@router.get('/scenario/products')
def listCategoryProducts(category: str, variant: str = None, currency: str = None,
                         caller=Depends(requireAuth)):
    """The products a custom sleeve in *category* may hold under *variant*
    (D170): those the category's library sleeves hold under the type, whole
    as the catalogue has them, with the ticker and minimum each has in
    *currency* (D180). Any signed-in user may read it; whether they may
    build is ``mayBuild``."""
    try:
        validateVariant(variant)
        if category not in sleeveRepo.categories():
            raise ValidationError('category', 'There is no category {!r}.'.format(category))
        return {'category': category, 'variant': variant,
                'products': [products.inCurrency(p, currency)
                             for p in customSleeves.categoryProducts(category, variant)],
                'mayBuild': customSleeves.mayBuild(category, isAdmin(caller))}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


# ---- the rate card (D55) ------------------------------------------------
# Read only here, for every PWA: the card is maintained in the repository's
# Fees tab (D166), whose admin-only calls are below with the repository's.
# Static paths, declared before /scenario/{scenarioId} like the others.

@router.get('/scenario/custom-portfolio')
def getCustomPortfolioCatalogue(variant: str = None, currency: str = 'USD',
                                hedging: str = 'Hedged', caller=Depends(requireAuth)):
    """What a custom portfolio is built from under *variant* (D171): the
    products it may hold, each with its category; every line; the type's
    library sleeves to insert; each risk profile's anchor portfolio and the
    lines' long-term estimates, for the limits and the figures; and whether
    this caller may build. Any signed-in user may read it."""
    try:
        validateVariant(variant)
        basis = BasisInput(currency=currency, hedging=hedging)
        validateBasis(basis)
        body = customPortfolio.catalogue(variant)
        # each product's ticker and minimum in the portfolio's currency (D180)
        body['products'] = [products.inCurrency(p, basis.currency) for p in body['products']]
        body.update(customPortfolio.profiles(getScenarioPort(), basis, variant))
        body['library'] = customPortfolio.library(variant)
        body['mayBuild'] = customPortfolio.mayBuild(variant, isAdmin(caller))
        return body
    except ValidationError as exc:
        return _validationError(exc)
    except AnalyticsError as exc:
        return _analyticsError(exc)


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
    except fees.BadRateCard as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})
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
                hedging: str = 'Hedged', key: str = None):
    """The sleeve library for one category, under one variant (spec 3.4, D29),
    resolved for the base portfolio *key* (D89).

    *variant* is required. It is declared optional only so that an absent one
    reaches validateVariant and comes back as a 422 naming the field, rather
    than as FastAPI's own unfielded "Malformed request." Serving a default
    library to a caller that has not chosen a variant is how the wrong
    products reach the wrong book, so it is never a fallback.

    *key* is the base column's key string. A name that has an edition for
    that portfolio is served as that edition, and nothing in the response
    says so - the PWA sees names. Without a key only fallback editions are
    offered. Each product carries the ticker and the minimum it has in
    *currency* (D180).
    """
    basis = BasisInput(currency=currency, hedging=hedging)
    try:
        validateVariant(variant)
        baseKey = _baseKeyFrom(key)
        served = list(getScenarioPort().list_sleeves(category, basis, variant, baseKey))
        for sleeve in served:
            sleeve['products'] = [products.inCurrency(p, basis.currency)
                                  for p in sleeve.get('products') or []]
        return {'category': category, 'variant': variant, 'sleeves': served}
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
            # the values an edition's rules may name, per field, from the
            # strategic universe as loaded (D89)
            'ruleVocabulary': sleeveRules.vocabulary(),
            'ruleFields': list(sleeveRules.FIELDS),
            # every strategic portfolio and the words for a rule's values, so
            # the New edition form can count, list and map them (D158)
            'strategicPortfolios': sleeveRules.portfolioKeys(),
            'ruleLabels': sleeveRules.valueLabels(),
            'sleeves': sleeveRepo.listAll(),
            'archived': sleeveRepo.listArchived(),
            'orphans': sleeveRepo.orphanProducts(),
            # the catalogue with each product's stamps, the products withdrawn
            # from it, the words its fields are described by and the fee
            # groups a product may name (D165)
            'products': products.entries(),
            'withdrawnProducts': products.entries(withdrawn=True),
            'productVocabulary': products.vocabulary(),
            'feeGroups': fees.feeGroups(),
            'catalogue': products.describeSource(),
            # whether PWAs may enter the custom workflows at all (D172)
            'customAccess': customAccess.describe(),
            'store': sleeveRepo.describe(),
            'register': proposalRegister.describe(),
            'user': caller.kerberos,
        }
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})
    except fees.BadRateCard as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


@router.post('/scenario/repository/sleeves')
def createRepositorySleeve(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Build a sleeve: {category, name, note, products: [{productId, weight}]},
    weights as fractions summing to 1, under `variants` (a list) or `variant`.
    Optionally an edition (D89): {label, rules: [{currency, riskLevel,
    allocationType}]}, each field a list of values or absent for any.

    Several types make one sleeve shared by them all (D179): an edit under
    any of them is what each serves. All or nothing: a name that clashes in
    any of them writes nothing. `sleeves` is a list of the one made.
    """
    try:
        variants = payload.get('variants')
        if not variants:
            variants = [payload.get('variant')] if payload.get('variant') else []
        made = sleeveRepo.createSleeves(
            variants, payload.get('category'), payload.get('name'),
            payload.get('products') or [], note=payload.get('note', ''), user=caller.kerberos,
            label=payload.get('label', ''), rules=payload.get('rules'))
        return {'sleeves': made, 'sleeve': made[0]}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


@router.put('/scenario/repository/sleeves/{sleeveId}')
def updateRepositorySleeve(sleeveId: int, payload: dict = Body(...),
                           caller=Depends(requireAdmin)):
    """Rename, re-note, re-weight or re-scope a sleeve. Its category is fixed
    at creation - a sleeve moved between them is a different sleeve - and its
    types move by /types, not here (D179).
    `label` and `rules` absent from the payload leave the edition as it is.
    `base`, the revision count the edit started from, makes a save over
    someone else's newer one a 409 rather than a silent overwrite (D156)."""
    try:
        sleeve = sleeveRepo.updateSleeve(
            sleeveId, payload.get('name'), payload.get('products') or [],
            note=payload.get('note', ''), user=caller.kerberos,
            label=payload.get('label') if 'label' in payload else None,
            rules=payload.get('rules') if 'rules' in payload else None,
            base=payload.get('base'))
        return {'sleeve': sleeve}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


@router.post('/scenario/repository/sleeves/{sleeveId}/editions')
def addRepositoryEdition(sleeveId: int, payload: dict = Body(...),
                         caller=Depends(requireAdmin)):
    """Another edition of an existing sleeve's name, in its book and
    category (D89): {label, rules, note, products}. Meets every rule a
    created sleeve meets, plus the edition's own: a label the name already
    carries, or rules that claim a portfolio a sibling claims, are refused
    with the portfolios named."""
    try:
        sleeve = sleeveRepo.addEdition(
            sleeveId, payload.get('label', ''), payload.get('rules'),
            payload.get('products') or [], note=payload.get('note', ''), user=caller.kerberos)
        return {'sleeve': sleeve}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


@router.post('/scenario/repository/applicability')
def previewApplicability(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """What a rule set would mean, before it is saved (D89): {variant,
    category, name, label, rules, sleeveId?} -> {applies, appliesTo,
    universe, overlaps}. The console's live count and clash report; the
    same validation a save runs, so the words match."""
    try:
        return sleeveRepo.applicabilityPreview(
            payload.get('variants') or payload.get('variant'), payload.get('category'),
            payload.get('name'),
            payload.get('label', ''), payload.get('rules'), sleeveId=payload.get('sleeveId'))
    except ValidationError as exc:
        return _validationError(exc)


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


@router.post('/scenario/repository/sleeves/{sleeveId}/offered')
def offerRepositorySleeve(sleeveId: int, payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Disable a sleeve, or enable it again: {offered: bool} (D169). A
    disabled sleeve stays in the library and the console, and is not offered
    to PWAs: the implementation page's picker leaves it out, and a scenario
    still naming it shows the picker's 'no longer offered' state until a PWA
    re-picks, as for an archived one. Not a new version of the sleeve."""
    offered = payload.get('offered')
    if not isinstance(offered, bool):
        return _validationError(ValidationError('offered', 'Say whether the sleeve is offered: true or false.'))
    try:
        return {'sleeve': sleeveRepo.setOffered(sleeveId, offered, user=caller.kerberos)}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


@router.post('/scenario/repository/sleeves/{sleeveId}/types')
def shareRepositorySleeve(sleeveId: int, payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Where a sleeve is offered (D179): {action, variant}. `share` offers
    it under one more type - the same sleeve, so an edit under either is
    what both serve; `unshare` stops offering it under one of its types;
    `split` gives one of its types a copy of its own, separate from then on.
    Each acts on the sleeve's editions under the same types too, and none is
    a new version. Returns every sleeve touched, copies last."""
    actions = {'share': sleeveRepo.offerUnder, 'unshare': sleeveRepo.withdrawFrom,
               'split': sleeveRepo.splitOff}
    action = actions.get(payload.get('action'))
    if action is None:
        return _validationError(ValidationError('action', 'Say what to do: share, unshare or split.'))
    try:
        return {'sleeves': action(sleeveId, payload.get('variant'), user=caller.kerberos)}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


@router.post('/scenario/repository/custom-access')
def setCustomAccess(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Open the custom workflows - custom sleeves and custom portfolios - to
    PWAs, or close them, {open} (D172). Admins enter them either way. Each
    change is a revision on the record, shown in the Activity tab."""
    try:
        customAccess.setPwas(payload.get('open'), user=caller.kerberos)
        return {'customAccess': customAccess.describe()}
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
                 since: str = '', until: str = '', q: str = '', areas: str = '') -> dict:
    """The feed's query string, as the feed's keyword arguments. Actions and
    areas arrive comma-joined because a chip set is one parameter, not a
    list."""
    return {
        'areas': [a for a in (areas or '').split(',') if a],
        'actions': [a for a in (actions or '').split(',') if a],
        'variant': variant or None, 'category': category or None, 'actor': actor or None,
        'since': since or None, 'until': until or None, 'query': q or '',
    }


@router.get('/scenario/repository/activity')
def getRepositoryActivity(actions: str = '', variant: str = '', category: str = '',
                          actor: str = '', since: str = '', until: str = '', q: str = '',
                          areas: str = '', limit: int = 100, before: str = '',
                          caller=Depends(requireAdmin)):
    """A page of the record as a feed (D66, D167): every change to the
    sleeves, the products, the fee card, the overlay rules and the funding
    split, newest first, with the counts that frame it. `before` is the
    cursor a previous page handed back as `next`."""
    filters = _feedFilters(actions, variant, category, actor, since, until, q, areas)
    return repositoryFeed.activity(limit=limit, before=before or None, **filters)


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
                             areas: str = '', caller=Depends(requireAdmin)):
    """The feed under the same filters, every page of it, as a table."""
    filters = _feedFilters(actions, variant, category, actor, since, until, q, areas)
    return _csv(repositoryFeed.ACTIVITY_COLUMNS, repositoryFeed.activityRows(**filters),
                'repository-activity.csv')


# ---- the product catalogue (D165) -----------------------------------------
# Admin only, like the rest of the repository. A product is named in the
# body rather than the path, because an id is whatever the catalogue was
# delivered with and may hold a character a path cannot. Every write carries
# the signed-in admin as its author and comes back as the {error, field}
# body the page already shows; `base`, the version the editor opened, makes
# a save over someone else's newer one a 409.

def _catalogueError(exc):
    return JSONResponse(status_code=502, content={'error': str(exc)})


def _productAnswer(product):
    return {'product': product, 'catalogue': products.describeSource()}


@router.post('/scenario/repository/products')
def createRepositoryProduct(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Add a product: {product: {productId, name, ...}, note?}."""
    try:
        return _productAnswer(products.create(payload.get('product') or {}, user=caller.kerberos,
                                              note=payload.get('note', '')))
    except ValidationError as exc:
        return _validationError(exc)
    except (products.BadCatalogue, fees.BadRateCard) as exc:
        return _catalogueError(exc)


@router.put('/scenario/repository/products')
def updateRepositoryProduct(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Change a product's values: {productId, product, note?, base}. The id
    itself never changes."""
    try:
        return _productAnswer(products.update(
            str(payload.get('productId') or ''), payload.get('product') or {},
            user=caller.kerberos, note=payload.get('note', ''), base=payload.get('base')))
    except ValidationError as exc:
        return _validationError(exc)
    except (products.BadCatalogue, fees.BadRateCard) as exc:
        return _catalogueError(exc)


@router.post('/scenario/repository/products/withdraw')
def withdrawRepositoryProduct(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Take a product out of the catalogue: {productId, note?, base}. Refused
    while a live sleeve holds it; kept, with its history, and restorable."""
    try:
        return _productAnswer(products.withdraw(
            str(payload.get('productId') or ''), user=caller.kerberos,
            note=payload.get('note', ''), base=payload.get('base')))
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return _catalogueError(exc)


@router.post('/scenario/repository/products/restore')
def restoreRepositoryProduct(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Put a withdrawn product back: {productId, note?}."""
    try:
        return _productAnswer(products.restore(
            str(payload.get('productId') or ''), user=caller.kerberos, note=payload.get('note', '')))
    except ValidationError as exc:
        return _validationError(exc)
    except (products.BadCatalogue, fees.BadRateCard) as exc:
        return _catalogueError(exc)


@router.post('/scenario/repository/products/revert')
def revertRepositoryProduct(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Put an earlier version's values back: {productId, revision, note?,
    base}. It is a new version; nothing is rewound."""
    number = payload.get('revision')
    if isinstance(number, str) and number.strip().isdigit():
        number = int(number)
    try:
        return _productAnswer(products.revert(
            str(payload.get('productId') or ''), number, user=caller.kerberos,
            note=payload.get('note', ''), base=payload.get('base')))
    except ValidationError as exc:
        return _validationError(exc)
    except (products.BadCatalogue, fees.BadRateCard) as exc:
        return _catalogueError(exc)


@router.get('/scenario/repository/products/history')
def getRepositoryProductHistory(productId: str = '', caller=Depends(requireAdmin)):
    """Every version of one product, newest first, with what moved."""
    return {'productId': productId, 'history': products.history(productId)}


@router.post('/scenario/repository/products/import/preview')
def previewProductImport(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """What a product file would do, before anything moves: {name, content}
    with the file as base64."""
    try:
        return products.importPreview(payload.get('name'), payload.get('content'))
    except ValidationError as exc:
        return _validationError(exc)
    except (products.BadCatalogue, fees.BadRateCard) as exc:
        return JSONResponse(status_code=422, content={'error': str(exc), 'field': 'file'})


@router.post('/scenario/repository/products/import')
def importProducts(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Apply a product file: {name, content, withdraw: [ids], note?, base} -
    base being the catalogue version the preview showed."""
    try:
        done = products.importFile(payload.get('name'), payload.get('content'),
                                   user=caller.kerberos, withdraw=payload.get('withdraw') or [],
                                   note=payload.get('note', ''), base=payload.get('base'))
        return {'import': done, 'catalogue': products.describeSource()}
    except ValidationError as exc:
        return _validationError(exc)
    except (products.BadCatalogue, fees.BadRateCard) as exc:
        return JSONResponse(status_code=422, content={'error': str(exc), 'field': 'file'})


@router.get('/scenario/repository/products.csv')
def exportProducts(caller=Depends(requireAdmin)):
    """The live catalogue as a product file, ready to edit and import."""
    rows = products.exportRows()
    return _csv(rows[0], rows[1:], 'products.csv')


# ---- the fee card (D166) ---------------------------------------------------
# The card every proposal is priced from, kept in the repository: admin only,
# every change carrying a note and a new version, a write made against an
# out-of-date card refused with 409.

@router.get('/scenario/repository/fees')
def getRepositoryFeeCard(caller=Depends(requireAdmin)):
    """The card in force as the Fees tab edits it, with its version and the
    products each fee group prices."""
    try:
        return feeCard.describe()
    except (fees.BadRateCard, products.BadCatalogue) as exc:
        return _catalogueError(exc)


def _feeAnswer(saved):
    return {'saved': saved, 'fees': feeCard.describe()}


@router.put('/scenario/repository/fees')
def saveRepositoryFeeCard(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Put a new card in force: {card: {tiers, feeGroups, rates}, renames:
    [[old, new]], note, base}."""
    try:
        return _feeAnswer(feeCard.save(payload.get('card'), payload.get('note'),
                                       user=caller.kerberos, base=payload.get('base'),
                                       renames=payload.get('renames')))
    except ValidationError as exc:
        return _validationError(exc)
    except (fees.BadRateCard, products.BadCatalogue) as exc:
        return _catalogueError(exc)


@router.get('/scenario/repository/fees/history')
def getRepositoryFeeHistory(caller=Depends(requireAdmin)):
    """Every version of the card, newest first, with what moved."""
    return {'history': feeCard.history()}


@router.post('/scenario/repository/fees/revert')
def revertRepositoryFeeCard(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Put an earlier version back in force: {revision, note, base}."""
    number = payload.get('revision')
    if isinstance(number, str) and number.strip().isdigit():
        number = int(number)
    try:
        return _feeAnswer(feeCard.revert(number, payload.get('note'), user=caller.kerberos,
                                         base=payload.get('base')))
    except ValidationError as exc:
        return _validationError(exc)
    except (fees.BadRateCard, products.BadCatalogue) as exc:
        return _catalogueError(exc)


@router.post('/scenario/repository/fees/import/preview')
def previewFeeImport(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """What a fee card file would change: {name, content} as base64."""
    try:
        return feeCard.importPreview(payload.get('name'), payload.get('content'))
    except ValidationError as exc:
        return _validationError(exc)
    except (fees.BadRateCard, products.BadCatalogue) as exc:
        return JSONResponse(status_code=422, content={'error': str(exc), 'field': 'file'})


@router.post('/scenario/repository/fees/import')
def importFeeCard(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Put a fee card file in force: {name, content, note, base}."""
    try:
        return _feeAnswer(feeCard.importFile(payload.get('name'), payload.get('content'),
                                             payload.get('note'), user=caller.kerberos,
                                             base=payload.get('base')))
    except ValidationError as exc:
        return _validationError(exc)
    except (fees.BadRateCard, products.BadCatalogue) as exc:
        return _catalogueError(exc)


@router.get('/scenario/repository/fees.csv')
def exportFeeCard(caller=Depends(requireAdmin)):
    """The card in force as a fee card file, one row per rate."""
    rows = feeCard.exportRows()
    return _csv(rows[0], rows[1:], 'fee-card.csv')


def _registerFilters(exportedBy: str = '', primaryPwa: str = '', currency: str = '',
                     variant: str = '', since: str = '', until: str = '', q: str = '') -> dict:
    return {'exportedBy': exportedBy or None, 'primaryPwa': primaryPwa or None,
            'currency': currency or None, 'variant': variant or None,
            'since': since or None, 'until': until or None, 'query': q or ''}


@router.get('/scenario/repository/proposals')
def listRegisterProposals(exportedBy: str = '', primaryPwa: str = '', currency: str = '',
                          variant: str = '', since: str = '', until: str = '', q: str = '',
                          limit: int = 100, before: str = '', moved: int = 0, noAccount: int = 0,
                          custom: int = 0, portfolios: int = 0,
                          caller=Depends(requireAdmin)):
    """A page of the proposal register (D69): every delivered proposal, newest
    first, with the counts that frame it. `before` is the cursor a previous
    page handed back as `next`. No blob travels with a list."""
    filters = _registerFilters(exportedBy, primaryPwa, currency, variant, since, until, q)
    return proposalRegister.listProposals(limit=limit, before=before or None,
                                          movedOnly=bool(moved), noAccountOnly=bool(noAccount),
                                          customOnly=bool(custom), portfolioOnly=bool(portfolios), **filters)


@router.get('/scenario/repository/proposals.csv')
def exportRegisterProposals(exportedBy: str = '', primaryPwa: str = '', currency: str = '',
                            variant: str = '', since: str = '', until: str = '', q: str = '',
                            moved: int = 0, noAccount: int = 0, custom: int = 0,
                            portfolios: int = 0, caller=Depends(requireAdmin)):
    """The register as a table under the same filters and the same saved view,
    every page of it - so the file is what the screen says it is (D116)."""
    filters = _registerFilters(exportedBy, primaryPwa, currency, variant, since, until, q)
    filters.update(movedOnly=bool(moved), noAccountOnly=bool(noAccount), customOnly=bool(custom),
                   portfolioOnly=bool(portfolios))
    return _csv(proposalRegister.EXPORT_COLUMNS, proposalRegister.exportRows(**filters),
                'proposal-register.csv')


@router.get('/scenario/repository/proposals/views')
def registerViewCounts(caller=Depends(requireAdmin)):
    """How many proposals each saved view holds (D116). Declared above the
    {proposalId} route on purpose: a path parameter would swallow `views`."""
    return {'views': proposalRegister.viewCounts(caller.kerberos)}


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


@router.get('/scenario/repository/proposals/{proposalId}/deck')
def downloadRegisterDeck(proposalId: str, caller=Depends(requireAdmin)):
    """The delivered deck, byte for byte, hash in a header - the workbook's
    companion (D123). 404 for a proposal delivered before decks were: it
    never had one, and none is made up after the fact."""
    found = proposalRegister.deck(proposalId)
    if found is None:
        return JSONResponse(status_code=404,
                            content={'error': 'No deck was delivered with {}.'.format(proposalId)})
    return Response(content=found['bytes'], media_type=_PPTX,
                    headers={'Content-Disposition': 'attachment; filename="{}"'.format(found['name']),
                             'X-Deck-SHA256': found['sha']})


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
        return {'sleeve': sleeveRepo.revertSleeve(sleeveId, number, user=caller.kerberos,
                                                  base=payload.get('base'))}
    except ValidationError as exc:
        return _validationError(exc)
    except products.BadCatalogue as exc:
        return JSONResponse(status_code=502, content={'error': str(exc)})


# ---- the private-markets funding split (D148) ----------------------------
# How a private-markets commitment is held until capital is called: a default
# split for every implementation type and, optionally, a type's own override.
# Admin only, like every repository write; every change carries a note and
# adds a revision (fundingSplit.py).

@router.get('/scenario/repository/funding')
def getFundingSplit(caller=Depends(requireAdmin)):
    """The default split, every override, and which categories may hold the
    money - with how many private-markets books hold each."""
    return fundingSplit.describe()


@router.put('/scenario/repository/funding')
def saveFundingSplit(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Put a split in force: {scope: '*' or a type, destinations: [{category,
    weightPct}], note}. The weights are percentages adding up to exactly 100."""
    try:
        entry = fundingSplit.save(payload.get('scope'), payload.get('destinations'),
                                  payload.get('note'), user=caller.kerberos)
        return {'split': entry, 'funding': fundingSplit.describe()}
    except ValidationError as exc:
        return _validationError(exc)


@router.post('/scenario/repository/funding/remove')
def removeFundingOverride(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Put a type back on the default split: {scope, note}."""
    try:
        fundingSplit.removeOverride(payload.get('scope'), payload.get('note'),
                                    user=caller.kerberos)
        return {'funding': fundingSplit.describe()}
    except ValidationError as exc:
        return _validationError(exc)


@router.get('/scenario/repository/funding/history')
def getFundingHistory(scope: str = '*', caller=Depends(requireAdmin)):
    """Every revision of one scope's split, newest first."""
    try:
        return {'scope': scope, 'history': fundingSplit.history(scope)}
    except ValidationError as exc:
        return _validationError(exc)


@router.post('/scenario/repository/funding/revert')
def revertFundingSplit(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Put an earlier revision back in force: {scope, revision, note}. It is
    a new revision; nothing is rewound."""
    try:
        number = int(payload.get('revision', 0))
    except (TypeError, ValueError):
        return _validationError(ValidationError('revision', 'Choose a revision to restore.'))
    try:
        entry = fundingSplit.revert(payload.get('scope'), number, payload.get('note'),
                                    user=caller.kerberos)
        return {'split': entry, 'funding': fundingSplit.describe()}
    except ValidationError as exc:
        return _validationError(exc)


# ---- the overlay rules (D155) ----------------------------------------------
# The ordered list of overlays - the tilt, the premium and any the desk adds -
# that move weight between categories as a portfolio is implemented, resolved
# top to bottom: a default list for every implementation type and, optionally, a
# type's own. Admin only, like every repository write; every change carries a
# note and adds a revision (overlayRules.py). A write may name the list it was
# made against ({base: {scope, revision}}); when that is no longer the list in
# force it is refused with 409, and the console reloads.

def _overlayError(exc: ValidationError):
    if isinstance(exc, overlayRules.StaleError):
        return JSONResponse(status_code=409, content={'error': exc.message, 'field': exc.field})
    return _validationError(exc)


@router.get('/scenario/repository/overlays')
def getOverlayRules(caller=Depends(requireAdmin)):
    """The default list, every type's own list, and what the console needs to
    judge a draft as the server will: the strategic categories, every
    portfolio's category weights, the portfolios each type offers and a
    sample, which categories each type has a sleeve in, and the warnings in
    force."""
    return overlayRules.describe()


@router.put('/scenario/repository/overlays')
def saveOverlayRules(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Put a list in force: {scope: '*' or a type, rules: [...], note, base?}.
    The rules resolve in the order given. Refused (422) when a portfolio the
    list applies to holds a rule's sources but could not fund it; 409 when
    *base* is no longer the list in force."""
    try:
        saved = overlayRules.save(payload.get('scope'), payload.get('rules'),
                                  payload.get('note'), user=caller.kerberos,
                                  base=payload.get('base'))
        return {'list': saved['entry'], 'warnings': saved['warnings'],
                'overlays': overlayRules.describe()}
    except ValidationError as exc:
        return _overlayError(exc)


@router.post('/scenario/repository/overlays/remove')
def removeOverlayOverride(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Put a type back on the default list: {scope, note, base?}."""
    try:
        overlayRules.removeOverride(payload.get('scope'), payload.get('note'),
                                    user=caller.kerberos, base=payload.get('base'))
        return {'overlays': overlayRules.describe()}
    except ValidationError as exc:
        return _overlayError(exc)


@router.get('/scenario/repository/overlays/history')
def getOverlayHistory(scope: str = '*', caller=Depends(requireAdmin)):
    """Every revision of one scope's list, newest first, with what changed."""
    try:
        return {'scope': scope, 'history': overlayRules.history(scope)}
    except ValidationError as exc:
        return _overlayError(exc)


@router.post('/scenario/repository/overlays/revert')
def revertOverlayRules(payload: dict = Body(...), caller=Depends(requireAdmin)):
    """Put an earlier revision back in force: {scope, revision, note, base?}.
    It is a new revision, re-checked against today's portfolios."""
    number = payload.get('revision')
    if isinstance(number, str) and number.strip().isdigit():
        number = int(number)
    if isinstance(number, bool) or not isinstance(number, int):
        return _validationError(ValidationError('revision', 'Choose a revision to restore.'))
    try:
        saved = overlayRules.revert(payload.get('scope'), number, payload.get('note'),
                                    user=caller.kerberos, base=payload.get('base'))
        return {'list': saved['entry'], 'warnings': saved['warnings'],
                'overlays': overlayRules.describe()}
    except ValidationError as exc:
        return _overlayError(exc)


# ---- account opening requests (D76) --------------------------------------
# The landing card's third button opens a form that fills itself from a
# Proposal UID (D75) and records a request against that proposal. Static
# paths, declared before /scenario/{scenarioId} like the others. Lookup and
# Submit both inherit the router-level requireAuth: they are a PWA's own
# actions (D77).

@router.get('/scenario/repository/custom-portfolios/awaiting')
def listAwaitingCustomPortfolios(caller=Depends(requireAdmin)):
    """The custom portfolios waiting for an admin's approval (D171), each
    with its scenario, so an admin can open it."""
    return {'awaiting': scenarioStore.awaitingApproval(), 'user': caller.kerberos}


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
    sleeves, includeFees, feeSchedule, feeLevel, customFees, otherCharges}. Mandate updates are validated server-side; sleeve
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
        volPremium = customFees = customs = built = charges = None
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
        if 'customFees' in payload:
            # bounded against the mandate in force after this write (D96)
            customFees = payload['customFees'] or {}
            validateCustomFees(customFees, mandate or MandateInput.fromDict(current['mandate']))
        if 'otherCharges' in payload:
            charges = _otherCharges.clean(payload['otherCharges'])
        if 'sleeves' in payload:
            from cyrus_pmg.pmgService.scenario.rules import autoSleeveCategories
            automatic = autoSleeveCategories()
            against = variant or current.get('variant')
            if not against:
                raise ValidationError(
                    'variant',
                    'Choose an implementation type before attaching sleeves.')
            # a name resolves for the base portfolio in force (D89): with no
            # base yet only fallbacks can answer, which is the honest state
            baseKey = _baseKeyFrom(current.get('base'))
            sleeves = {}
            for category, name in (payload['sleeves'] or {}).items():
                if category in automatic:
                    raise ValidationError(
                        'sleeves',
                        '{} carries its sleeve automatically.'.format(category))
                if name is not None and not sleeveExists(category, name, against, baseKey):
                    # *category* is already the group name where there is one:
                    # the page sends what it picked under (D60)
                    raise ValidationError(
                        'sleeves',
                        'No sleeve named {!r} for {} under {}.'.format(
                            name, category, against))
                sleeves[category] = name
        if 'customSleeves' in payload:
            customs = _customSleevesFrom(payload['customSleeves'], current, variant, caller)
        if 'customPortfolio' in payload:
            built = _customPortfolioFrom(payload['customPortfolio'], current, variant, caller)
        elif variant and variant != current.get('variant') and current.get('customPortfolio'):
            # the build survives a new type; an approval of it under the old
            # one does not (D171)
            built = _voided(dict(current['customPortfolio']), 'implementation type')
        state = scenarioStore.updateScenario(
            scenarioId, mandate=mandate, basis=basis, sleeves=sleeves,
            variant=variant, tacticalTilt=tacticalTilt,
            feeSchedule=feeSchedule, feeLevel=feeLevel,
            includeFees=includeFees, volPremium=volPremium,
            customFees=customFees, customFeesBy=caller.kerberos,
            customSleeves=customs, customPortfolio=built,
            otherCharges=charges, otherChargesBy=caller.kerberos)
        return _scenarioPayload(state)
    except ScenarioNotFound:
        return _notFound(scenarioId)
    except ValidationError as exc:
        return _validationError(exc)


_CUSTOM_FIELDS = ('name', 'note', 'mode', 'products')


def _customSleevesFrom(raw, current, variant, caller) -> dict:
    """The custom sleeves a write sends, checked (D170): the whole map, by
    the name each is chosen under, None or absent for none. One the caller
    did not change is kept as stored - a PWA may keep and deliver a sleeve
    an admin built, or one built before its category was closed - and every
    other must be one this caller may build, holding products the
    category's sleeves hold under the type in force after this write."""
    against = variant or current.get('variant')
    if raw and not against:
        raise ValidationError('variant', 'Choose an implementation type before building a '
                                         'custom sleeve.')
    # a new type clears what was built under the old one, as the store does
    held = ({} if variant and variant != current.get('variant')
            else (current.get('customSleeves') or {}))
    admin = isAdmin(caller)
    out = {}
    for under, definition in (raw or {}).items():
        if definition is None:
            continue
        stored = held.get(under)
        if stored is not None and isinstance(definition, dict) and all(
                definition.get(f) == stored.get(f) for f in _CUSTOM_FIELDS):
            out[under] = stored
            continue
        if not customSleeves.mayBuild(under, admin):
            raise ValidationError(
                'customSleeves',
                'Custom sleeves are built by an admin.'
                if not customSleeves.isFixed(under)
                else '{} carries its sleeve automatically.'.format(under))
        out[under] = dict(customSleeves.clean(under, definition, against),
                          builtBy=caller.kerberos)
    return out


def _now():
    import datetime
    return datetime.datetime.now().isoformat(timespec='seconds')


def _voided(entry, what) -> dict:
    """*entry* with an approval in progress or given withdrawn, and the
    notice the page shows saying why (D171). A build sent back keeps the
    reason it was sent back for until it is submitted again."""
    approval = entry.get('approval') or {}
    if approval.get('state') in ('submitted', 'approved'):
        entry['notice'] = 'The {} changed after it was {}, so {}: submit it again.'.format(
            what, 'approved' if approval['state'] == 'approved' else 'submitted',
            'the approval no longer applies' if approval['state'] == 'approved'
            else 'the request was withdrawn')
        entry['approval'] = None
    return entry


def _customPortfolioFrom(raw, current, variant, caller) -> dict:
    """The custom portfolio a write sends, checked (D171): ``{active,
    profile, products}``. Making the proposal custom, changing its build or
    making it strategic again are for those who may build under the type in
    force after this write; a product the stored build already holds is kept
    even where it may no longer be held, so it can be swapped out one row at
    a time, and the export refuses until it is."""
    if not isinstance(raw, dict):
        raise ValidationError('customPortfolio', 'A custom portfolio is a risk profile, products '
                                                 'and weights.')
    against = variant or current.get('variant')
    if not against:
        raise ValidationError('variant', 'Choose an implementation type before building a custom '
                                         'portfolio.')
    held = dict(current.get('customPortfolio') or {})
    build = customPortfolio.clean(
        {'profile': raw.get('profile'), 'products': raw.get('products') or []}, against,
        keep={p['productId']: p.get('category') for p in held.get('products') or []})
    active = bool(raw.get('active'))
    changed = (not held or build['profile'] != held.get('profile')
               or build['products'] != held.get('products'))
    if (changed or active != bool(held.get('active'))) and not customPortfolio.mayBuild(
            against, isAdmin(caller)):
        raise ValidationError('customPortfolio', 'Custom portfolios are built by an admin.')
    out = dict(held, active=active, profile=build['profile'], products=build['products'])
    out.setdefault('approval', None)
    out.setdefault('notice', None)
    if changed:
        out['builtBy'] = caller.kerberos
        out['builtAt'] = _now()
    if changed or (variant and variant != current.get('variant')):
        what = ('implementation type' if not changed
                else 'risk profile' if build['products'] == held.get('products') else 'portfolio')
        _voided(out, what)
    return out


def _customCheck(state, port):
    """The stored build against its limits, and its profile's anchor
    portfolio resolved (D171)."""
    basis = BasisInput.fromDict(state['basis'])
    variant = state.get('variant')
    built = state.get('customPortfolio') or {}
    anchorResult = customPortfolio.anchorFor(port, basis, variant, built.get('profile'))
    anchor = customPortfolio.anchorLines(anchorResult) if anchorResult else {}
    mandateSize = (state.get('mandate') or {}).get('mandateSize')
    return customPortfolio.checks(built, variant, mandateSize, anchor), anchorResult


@router.post('/scenario/{scenarioId}/custom-portfolio/approval')
def customPortfolioApproval(scenarioId: str, payload: dict = Body(...),
                            caller=Depends(requireAuth)):
    """Approval of a custom portfolio outside its profile's usual range
    (D171): {action: submit | withdraw | approve | return, note}. Anyone who
    may build it submits or withdraws; an admin who neither built nor
    submitted it approves it or sends it back with a reason. An approval is
    of this build, profile and type: change any and it no longer applies."""
    port = getScenarioPort()
    try:
        state = scenarioStore.getScenario(scenarioId)
        built = dict(state.get('customPortfolio') or {})
        if not built.get('active'):
            raise ValidationError('customPortfolio', 'This proposal is not a custom portfolio.')
        variant = state.get('variant')
        action = payload.get('action')
        if action not in customPortfolio.APPROVAL_ACTIONS:
            raise ValidationError('action', 'Submit, withdraw, approve or send back.')
        note = ' '.join(str(payload.get('note') or '').split())
        if len(note) > customPortfolio.NOTE_MAX:
            raise ValidationError('note', 'The note is {} characters; at most {}.'.format(
                len(note), customPortfolio.NOTE_MAX))
        admin = isAdmin(caller)
        approval = dict(built.get('approval') or {})
        print_ = customPortfolio.fingerprint(built, variant)
        if action == 'submit':
            if not customPortfolio.mayBuild(variant, admin):
                raise ValidationError('customPortfolio', 'Custom portfolios are built by an admin.')
            check, _ = _customCheck(state, port)
            mandate = MandateInput.fromDict(state['mandate'])
            # its products as its currency reads them, the minimums included
            model = customPortfolio.buildModel(built, mandate.mandateSize, variant,
                                               currency=BasisInput.fromDict(state['basis']).currency)
            if check['status'] == 'bad' or model['breaches'] or model.get('unavailable'):
                raise ValidationError('approval', 'It cannot be delivered yet, so there is nothing to '
                                                  'approve: {}'.format(' '.join(check['problems'])
                                                                       or ('a product is not offered '
                                                                           'in its currency.'
                                                                           if model.get('unavailable')
                                                                           else 'a position is below its '
                                                                                'product\'s minimum.')))
            if check['status'] == 'ok':
                raise ValidationError('approval', 'Every category is within its usual range: it needs '
                                                  'no approval.')
            approval = {'state': 'submitted', 'by': caller.kerberos, 'note': note or '',
                        'at': _now(), 'fingerprint': print_}
            built['notice'] = None
        elif action == 'withdraw':
            if approval.get('state') != 'submitted':
                raise ValidationError('approval', 'Nothing is waiting for approval.')
            if not (customPortfolio.mayBuild(variant, admin) or caller.kerberos == approval.get('by')):
                raise ValidationError('approval', 'Only someone who may build it withdraws it.')
            approval = None
        else:
            if not admin:
                raise ValidationError('approval', 'An admin approves a custom portfolio.')
            if approval.get('state') != 'submitted':
                raise ValidationError('approval', 'Nothing is waiting for approval.')
            if caller.kerberos in (built.get('builtBy'), approval.get('by')):
                raise ValidationError('approval', 'You built or submitted it, so another admin '
                                                  'approves it.')
            if approval.get('fingerprint') != print_:
                raise ValidationError('approval', 'The portfolio changed after it was submitted.')
            if action == 'return' and not note:
                raise ValidationError('note', 'Say what needs to change.')
            approval.update(state='approved' if action == 'approve' else 'returned',
                            decidedBy=caller.kerberos, decidedAt=_now(),
                            reason=note if action == 'return' else '')
        built['approval'] = approval or None
        state = scenarioStore.updateScenario(scenarioId, customPortfolio=built)
        return _scenarioPayload(state)
    except ScenarioNotFound:
        return _notFound(scenarioId)
    except ValidationError as exc:
        return _validationError(exc)
    except AnalyticsError as exc:
        return _analyticsError(exc)


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


def _assembleExport(scenarioId: str, port):
    """Everything both export formats share (D122): read the stored state,
    validate it exactly as the workbook export always has, resolve every
    column through the port, and build the implementation model ONCE (D69) -
    refusing on a missing variant or sleeve, an unpriced custom row and a
    position below its product's minimum, so neither format can be a way
    around the other's gate. Raises what the endpoints map: ScenarioNotFound,
    ValidationError, AnalyticsError."""
    state = scenarioStore.getScenario(scenarioId)
    if (state.get('customPortfolio') or {}).get('active'):
        return _assembleCustomExport(state, port)
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

    from cyrus_pmg.pmgService.scenario.rules import AUTO_SLEEVE_CATEGORIES, sleeveCategory
    # The overlay rules in force for this type, read ONCE: the categories
    # they attach automatically and the model below come from the same list,
    # so a save landing mid-export cannot split them (D155).
    overlayEntry = overlayRules.current(variant)
    automatic = list(AUTO_SLEEVE_CATEGORIES) + [
        r['into'] for r in overlayEntry['rules'] if r['into'] not in AUTO_SLEEVE_CATEGORIES]
    from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows
    sleeves = state['sleeves'] or {}
    # the proposal's own sleeves (D170): one is in force where no library
    # sleeve is chosen
    customs = state.get('customSleeves') or {}
    # one choice per sleeve category: grouped categories share theirs (D60)
    missing, unresolved, customProblems = [], [], []
    baseKey = _baseKeyFrom(state['base'])
    for c in results[0]['categories']:
        if c['name'] in automatic:
            continue
        under = sleeveCategory(c['name'])
        if not sleeves.get(under) and customs.get(under):
            for problem in customSleeves.problems(under, customs[under], variant):
                if problem not in customProblems:
                    customProblems.append(problem)
        elif not sleeves.get(under):
            if under not in missing:
                missing.append(under)
        elif under not in unresolved and not sleeveExists(under, sleeves[under], variant, baseKey):
            # chosen under a different base, or the library moved: the
            # name has no edition for this portfolio and cannot be built
            unresolved.append(under)
    if missing:
        raise ValidationError(
            'sleeves',
            'Attach a sleeve to every category first - missing: {}.'.format(
                ', '.join(missing)))
    if unresolved:
        raise ValidationError(
            'sleeves',
            'The sleeve chosen for {} is not offered for this portfolio. '
            'Choose another.'.format(', '.join(unresolved)))
    if customProblems:
        raise ValidationError(
            'customSleeves',
            'A custom sleeve holds a product it no longer may: {}. Edit it before '
            'exporting.'.format('; '.join(customProblems)))

    implementation = {'sleeves': sleeves, 'customSleeves': customs, 'variant': variant,
                      'tacticalTilt': bool(state.get('tacticalTilt', True)),
                      'volPremium': bool(state.get('volPremium', True)),
                      'includeFees': includeFees,
                      'feeSchedule': feeSchedule, 'feeLevel': feeLevel,
                      'customFees': state.get('customFees') or {},
                      'customFeesBy': state.get('customFeesBy') or '',
                      'customFeesAt': state.get('customFeesAt'),
                      'otherCharges': _otherCharges.withDefaults(state.get('otherCharges'))}
    # The implemented model is built ONCE here and handed to both the
    # writer and the register, so the sheet a client receives and the
    # record kept of it are the same model rather than two builds (D69).
    # An unpriced proposal is built unpriced whatever schedule the
    # scenario happens to remember (D52) - the writer does the same.
    model = buildImplementationRows(
        results[0], sleeves, automatic, mandate.mandateSize, variant,
        implementation['tacticalTilt'], feeSchedule if includeFees else None, feeLevel,
        mandate.topAccountSize, implementation['volPremium'], basis.currency,
        customFees=implementation['customFees'], overlayEntry=overlayEntry,
        customSleeves=customs)
    # A custom sleeve weighted against the portfolio sets its category's
    # weight and nothing else moves (D170): the person decides where the
    # difference goes, and a book that does not add to 100% is not delivered.
    if not model['balanced']:
        raise ValidationError(
            'customSleeves',
            'The implemented portfolio adds to {:.2f}%, not 100%. Change the weights of a custom '
            'sleeve set as a percentage of the portfolio until it adds to 100%.'.format(
                model['implementedPct']))
    # An overlay's category carries its sleeve automatically; a rule whose
    # category has no sleeve yet for this type would put weight in the book
    # with no product, and a table that cannot be priced or rounded closed is
    # not delivered (D155)
    bare = [g['category'] for g in model['groups']
            if g.get('auto') and not g.get('sleeve') and g['weightPct'] > 0]
    if bare:
        raise ValidationError(
            'sleeves',
            'No sleeve in {} yet for {}: create one in the repository\'s Sleeves view before '
            'exporting.'.format(' or '.join(bare), variant))
    # Under the custom level a row without a rate leaves its products
    # unpriced, and an unpriced product cannot go on a priced sheet (D96).
    if model.get('unpricedGroups'):
        raise ValidationError(
            'customFees',
            'Set a custom rate for every fee group in the model - missing: {}.'.format(
                ', '.join(model['unpricedGroups'])))
    # A custom rate is held between the card's floor and ceiling for this
    # mandate (D96), and the card now changes in the repository (D166): a
    # rate entered before a change may sit outside the bounds it gives
    # today, so it is checked again here as it was when it was entered.
    if includeFees and fees.isCustom(feeLevel):
        validateCustomFees(implementation['customFees'], mandate)
    # A product not offered in the proposal's currency cannot be bought in
    # it (D182), and a position smaller than the product will accept is not
    # a position: the export refuses while either survives, so the UI's
    # block cannot be walked past by calling the endpoint directly (item 3).
    if model.get('unavailable'):
        raise ValidationError('offered', _unavailableText(model['unavailable'], basis.currency,
                                                          'change the sleeve or drop the product'))
    if model['breaches']:
        raise ValidationError('minimumInvestment',
                              'Below mandate minimum: {}. Raise the mandate, change the '
                              'sleeve, or drop the product before exporting.'.format(
                                  ', '.join('{} in {} (${:,.0f} against a ${:,.0f} minimum)'.format(
                                      b['name'], b['category'], b['notional'],
                                      b['minimumInvestment']) for b in model['breaches'][:4])
                                  + ('' if len(model['breaches']) <= 4
                                     else ', and {} more'.format(len(model['breaches']) - 4))))
    # every other check first: the charges are what the page asks for last,
    # as Download opens their card (D177)
    _otherCharges.required(implementation['otherCharges'], includeFees)
    return state, basis, mandate, results, implementation, model


def _unavailableText(unavailable, currency, fix) -> str:
    return 'Not offered in {}: {}. {} before exporting.'.format(
        currency, ', '.join('{} in {}'.format(u['name'], u['category']) for u in unavailable[:4])
        + ('' if len(unavailable) <= 4 else ', and {} more'.format(len(unavailable) - 4)),
        fix[0].upper() + fix[1:])


def _breachText(breaches) -> str:
    return 'Below mandate minimum: {}. Raise the mandate or change the weights before exporting.'.format(
        ', '.join('{} in {} (${:,.0f} against a ${:,.0f} minimum)'.format(
            b['name'], b['category'], b['notional'], b['minimumInvestment']) for b in breaches[:4])
        + ('' if len(breaches) <= 4 else ', and {} more'.format(len(breaches) - 4)))


def _assembleCustomExport(state, port):
    """``_assembleExport`` for a custom portfolio (D171): the model is built
    from the products, through the same rounding, pricing and minimums, and
    the export refuses while the build is unfinished, outside its limits,
    or outside its usual ranges without an approval that still applies. The
    strategic portfolios beside it on the allocation step - its profile's
    own first - go into the files as comparisons, never as the proposal."""
    variant = state.get('variant')
    validateVariant(variant)
    includeFees = _includeFees(state)
    feeSchedule = state.get('feeSchedule')
    feeLevel = state.get('feeLevel')
    if includeFees:
        validateFeeSchedule(feeSchedule)
        validateFeeLevel(feeLevel)
    basis = BasisInput.fromDict(state['basis'])
    mandate = MandateInput.fromDict(state['mandate'])
    built = state['customPortfolio']
    check, anchorResult = _customCheck(state, port)
    if check['status'] == 'bad':
        raise ValidationError('customPortfolio', ' '.join(check['problems']))
    customFees = state.get('customFees') or {}
    model = customPortfolio.buildModel(
        built, mandate.mandateSize, variant, feeSchedule if includeFees else None, feeLevel,
        mandate.topAccountSize, basis.currency, customFees=customFees)
    if model.get('unpricedGroups'):
        raise ValidationError(
            'customFees',
            'Set a custom rate for every fee group in the model - missing: {}.'.format(
                ', '.join(model['unpricedGroups'])))
    if includeFees and fees.isCustom(feeLevel):
        validateCustomFees(customFees, mandate)
    if model.get('unavailable'):
        raise ValidationError('offered', _unavailableText(model['unavailable'], basis.currency,
                                                          'take the product out'))
    if model['breaches']:
        raise ValidationError('minimumInvestment', _breachText(model['breaches']))
    profileLabel = customPortfolio.profileLabel(built['profile'])
    approval = built.get('approval') or {}
    if check['status'] == 'warn' and not customPortfolio.approved(built, variant):
        raise ValidationError('approval', '{} An admin approves it before it is delivered.'.format(
            customPortfolio.wideText(check, profileLabel)))
    keys = ([state['base']] if state['base'] else []) + list(state['comparisons'])
    results = [port.resolve_portfolio(basis, PortfolioKey.fromStr(k)) for k in keys]
    stats = customPortfolio.estimates(port, basis, variant)
    weights = customPortfolio.weightsByLine(built)
    model['customPortfolio'] = {
        'profile': built['profile'], 'profileLabel': profileLabel,
        'anchorKey': anchorResult['keyStr'] if anchorResult else None,
        'anchorName': anchorResult['header'] if anchorResult else None,
        'builtBy': built.get('builtBy') or '', 'builtAt': built.get('builtAt') or '',
        'approvedBy': approval.get('decidedBy') if approval.get('state') == 'approved' else None,
        'approvedAt': approval.get('decidedAt') if approval.get('state') == 'approved' else None,
        'approvalNote': approval.get('note') if approval.get('state') == 'approved' else None,
        'lines': check['lines'],
        'limits': {k: customPortfolio.LIMITS[k] for k in ('soft', 'hard', 'concentration',
                                                           'illiquid')},
        'figures': customPortfolio.figures(weights, stats, anchorResult),
        'comparisons': [r['keyStr'] for r in results],
        # what the files lay the allocation out by: every line in order, and
        # each comparison's weights by line
        'order': customPortfolio.lines(),
        'compared': [customPortfolio.anchorLines(r) for r in results]}
    implementation = {'sleeves': {}, 'customSleeves': {}, 'variant': variant,
                      'tacticalTilt': False, 'volPremium': False,
                      'includeFees': includeFees,
                      'feeSchedule': feeSchedule, 'feeLevel': feeLevel,
                      'customFees': customFees,
                      'customFeesBy': state.get('customFeesBy') or '',
                      'customFeesAt': state.get('customFeesAt'),
                      'otherCharges': _otherCharges.withDefaults(state.get('otherCharges')),
                      'customPortfolio': model['customPortfolio']}
    # every other check first: the charges are what the page asks for last,
    # as Download opens their card (D177)
    _otherCharges.required(implementation['otherCharges'], includeFees)
    return state, basis, mandate, results, implementation, model


_PPTX = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
_ZIP = 'application/zip'


@router.post('/scenario/{scenarioId}/export')
def exportScenario(scenarioId: str, caller=Depends(requireAuth)):
    """The proposal (spec 14): the Excel workbook AND the PowerPoint deck,
    delivered together and never apart (D123). Assembled from stored state.

    Refuses (422) while no variant is chosen, a category lacks a sleeve, the
    proposal includes fees and no schedule is chosen, a custom row is
    unpriced or a position sits below its product's minimum - the UI
    disables the button, the server re-enforces (``_assembleExport``).

    One UID is minted and stamped into both files; both are written from the
    one model (D69); both are recorded in one register row before either is
    returned (a proposal that could not be written down is not delivered);
    and both travel in one zip, because a browser can refuse a second
    download from one click and "both or neither" must not depend on it.
    """
    port = getScenarioPort()
    try:
        state, basis, mandate, results, implementation, model = _assembleExport(
            scenarioId, port)

        # The Proposal UID is minted here, before either file exists, so the
        # one id is written into both files, their names and the register
        # row. The register refuses the row if any of them differ (D75).
        proposalId = proposalRegister.newProposalId()
        delivery = dict(implementation, model=model, proposalId=proposalId)
        content = port.build_export(basis, mandate, results, delivery)
        deck = port.build_export_deck(basis, mandate, results, delivery)
        filename = exportFilename(basis, proposalId, custom=bool(model.get('customPortfolio')))
        stem = filename[:-len('.xlsx')]
        deckName = stem + '.pptx'
        # Record first, deliver second - both files, one transaction.
        proposalRegister.record(proposalId, scenarioId, caller.kerberos, state.get('createdBy', ''),
                                basis, mandate, results, implementation, model, content, filename,
                                deck=deck, deckName=deckName)
        bundle = io.BytesIO()
        # stored, not deflated: both members are already zips of their own
        with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_STORED) as archive:
            archive.writestr(filename, content)
            archive.writestr(deckName, deck)
        return Response(
            content=bundle.getvalue(),
            media_type=_ZIP,
            headers={'Content-Disposition':
                     'attachment; filename="{}.zip"'.format(stem),
                     'X-Proposal-Id': proposalId},
        )
    except ScenarioNotFound:
        return _notFound(scenarioId)
    except ValidationError as exc:
        return _validationError(exc)
    except AnalyticsError as exc:
        return _analyticsError(exc)


# ===== TRANSPLANT BLOCK END ==================================================
