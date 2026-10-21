"""
Dashboard API Router - migrated from Flask dashboardApi.py to FastAPI.

All 50+ dashboard backend endpoints are registered here as a single FastAPI
APIRouter. The router is mounted under ``/api/v1/dashboard`` by the
optimizationService application, so every route path below maps 1-to-1 with
the old Flask ``/api/<path>`` pattern.

The Flask frontend (dashboardFrontend.py) continues to serve static files
and proxies ``/api/*`` requests to this FastAPI backend.

Business logic lives entirely in ``DashboardService`` (unchanged).

Author: jsanska (migrated from dashboardApi.py)
Date: 2026-04-29

Missing or partially shown source spans:
* 18-60: collapsed imports.
* 467-796: missing, except the sticky signature at 792.
* 836-889: missing, except the sticky signature at 875.
* 929-931: missing section heading.
* 996-1498: missing, except the sticky signature at 1477.
* 1537-1551: missing asset-class-mapping endpoint body.
* 1590-1592: missing exception tail.
* 1662 onward: remaining getGuidelines body and any subsequent code missing.

Incomplete fragments are preserved as comments. Recoverable right-cropped
endings are labelled INFERRED; unseen executable logic is not invented.
Duplicate constraint routes and an unreachable duplicate return are present
in the screenshots and retained. Syntax checking does not validate imports,
route registration, authentication, service contracts, or runtime behaviour.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

# Original source resumes at line 62.
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Router & shared service instance
#
# Auth chain (single, unified across the PMG service):
#   accessControl.requireAllowlistedUser -> kerberos + allowlist gate
#   pmgEntitlement.requireAuth           -> resolves kerberos -> UserData/role
#                                          (ALLOWED_KERBEROS members are
#                                           treated as ISGAdmin)
#
# Individual DB-modifying endpoints should ADD ``Depends(requireEditor)`` to
# reject PMGViewer users with HTTP 403 while still allowing ISGAdmin /
# PMGEditor / anyone on the static allowlist.
#
# ``publicRouter`` remains unauthenticated for the ``/whoami`` probe used by
# the front-end access gate.
# -----------------------------------------------------------------------------
router = APIRouter(dependencies=[Depends(requireAuth)])
publicRouter = APIRouter()
service = DashboardHandler()


@publicRouter.get("/whoami", tags=["Dashboard - Auth"],
                  summary="Return the caller's kerberos and allowlist status")
def whoami(
    request: Request,
    returnUrl: Optional[str] = Query(
        default=None,
        # INFERRED end of first description line, cropped in IMG_4206.
        description="URL the GSSSO sign-in flow should bounce the browser back to. "
                    "Used to build the loginUrl returned in the response. "
                    "Defaults to the Referer header or '/'.",
    ),
):
    """Identity probe for the front-end gate. Never blocks.

    When the caller has no kerberos identity the response includes a
    ``loginUrl`` field - navigate to it (e.g. ``window.location = loginUrl``)
    to trigger the GSSSO sign-in flow and obtain a cookie.
    """
    kerberos = getKerberosFromFastApiRequest(request)
    role = ""
    modify = False
    post = False
    view = False
    # Env is used by the front-end to hide non-prod-only features
    # (e.g. the ISG Model / Account Playground nav entries) on PROD.
    try:
        from cyrus.core.lib.env.env import Env  # local import to avoid startup [comment cropped]
        envName = (Env.get_env() or "")
    except Exception:
        envName = ""
    isProd = "PROD" in envName.upper()
    # Playground surfaces (ISG Model / Account) are disabled in both
    # PROD and UAT - mirrors the server-side gate in
    # dashboardFrontend._isPlaygroundBlockedEnv() so the nav dropdown
    # and the underlying HTML routes stay in lockstep.
    _envUpper = envName.upper()
    isPlaygroundEnabled = ("PROD" not in _envUpper) and ("UAT" not in _envUpper)
    if kerberos:
        try:
            from cyrus_pmg.pmgService.core.pmgEntitlement import (
                PmgEntitlement,
                canModify as _canModify,
                canPost as _canPost,
                canView as _canView,
            )
            _, ud = PmgEntitlement.isValidUser(kerberos)
            role = getattr(ud, "role", "") or ""
            modify = _canModify(ud)
            post = _canPost(ud)
            view = _canView(ud)
        except Exception as e:  # noqa: BLE001
            logger.debug("whoami: role lookup failed - %s", e)
    body = {
        "success": True,
        "kerberos": kerberos,
        "allowed": isAllowed(kerberos),
        "role": role,
        "canView": view,
        "canModify": modify,
        "canPost": post,
        "allowlistSize": len(getAllowlist()),
        "env": envName,
        "isProdEnv": isProd,
        "isPlaygroundEnabled": isPlaygroundEnabled,
    }
    if not kerberos:
        target = returnUrl or request.headers.get("referer") or "/"
        body["loginUrl"] = buildLoginUrl(target)
    return body


# INFERRED summary ending, cropped in IMG_4207 and IMG_4208.
@router.get("/allowlist", tags=["Dashboard - Auth"],
            summary="Return the full list of allowlisted kerberos IDs with their entitlements")
def allowlist():
    """Protected endpoint - only accessible by users already on the allowlist.

    For every kerberos on the static/env allowlist, resolve the user's
    effective entitlement via :meth:`PmgEntitlement.isValidUser` (which
    unions PERMIT's ``getAllowedPrivileges`` with role membership and
    the env-appropriate allowlist bonus) so the caller can see, at a
    glance, who can view / modify / post today.
    """
    from cyrus_pmg.pmgService.core.pmgEntitlement import (
        PmgEntitlement,
        canModify as _canModify,
        canPost as _canPost,
        canView as _canView,
    )

    users = sorted(getAllowlist())
    entitlements = []
    for kerb in users:
        role = ""
        view = modify = post = False
        try:
            _, ud = PmgEntitlement.isValidUser(kerb)
            role = getattr(ud, "role", "") or ""
            view = _canView(ud)
            modify = _canModify(ud)
            post = _canPost(ud)
        except Exception as e:  # noqa: BLE001
            # INFERRED cropped arguments at original line 185.
            logger.debug("allowlist: entitlement lookup failed for %s - %s", kerb, e)
        entitlements.append({
            "kerberos": kerb,
            "role": role,
            "canView": view,
            "canModify": modify,
            "canPost": post,
        })

    return {
        "success": True,
        "count": len(users),
        "allowedUsers": users,       # kept for backwards compatibility
        "entitlements": entitlements,  # new, richer per-user breakdown
    }


# =============================================================================
# Helper - consistent error envelope
# =============================================================================
def _errorResponse(e: Exception, status: int = 500) -> dict:
    # INFERRED docstring ending, cropped in IMG_4209.
    """Return the same ``{success: false, error: ...}`` shape the Flask API used."""
    logger.error(f"Dashboard API error: {e}", exc_info=True)
    return {"success": False, "error": str(e)}


# =============================================================================
# Helper - suppress HTTP caching for endpoints backed by mutable files
# =============================================================================
def _applyNoCacheHeaders(response: Response) -> None:
    """Attach a paranoia-level "do not cache this response" header set.

    Why this exists
    ---------------
    Several dashboard read endpoints are backed by JSON files on disk
    (``isg_model_from_db*.json``, ``isg_model_from_db*_updates.json``,
    ...) that get rewritten by:
      * this app itself   - create / delete / apply-update / save actions,
      * external batch    - nightly ISG-model refresh jobs, ad-hoc
                            regenerations by the modelling team.

    FastAPI's default JSON responses ship **no** ``Cache-Control`` header,
    so browsers apply heuristic freshness caching (RFC 9111 §4.2.2) and
    happily serve a stale response for minutes-to-hours after the
    underlying file has changed on disk. That looked exactly like the
    2026-07 "updates JSON was regenerated but the UI doesn't show the new
    updates" bug users reported.

    ``no-store`` alone is the modern-browser answer. ``Pragma: no-cache``
    and ``Expires: 0`` are belt-and-braces for HTTP/1.0 caches and
    corporate proxies that ignore ``Cache-Control``. Together they
    guarantee every reload does a fresh round-trip to
    :meth:`DashboardHandler.getAllModelPortfolios` /
    :meth:`DashboardHandler.getModelPortfolioUpdates`, both of which
    re-read the JSON file on every call.
    """
    # INFERRED trailing max-age directive; original line 241 is right-cropped.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


# =============================================================================
# Account Endpoints
# =============================================================================
@router.get("/accounts", tags=["Dashboard - Accounts"],
            summary="Get all PMG account numbers")
def getAccounts():
    # Plain ``def`` (not ``async def``) so FastAPI runs this in its
    # threadpool. The body does synchronous DB work with no awaits -
    # if it were ``async def``, the sync work would run on the event
    # loop and block every other request across the service (including
    # the main dashboard's own follow-up calls) while a long-running
    # /product-optimization request is in flight.
    try:
        accounts = service.getAllAccountNumbers()
        return {"success": True, "accounts": accounts}
    except Exception as e:
        return _errorResponse(e)


# -----------------------------------------------------------------------------
# New-client discovery
#
# Two endpoints so the dashboard can load fast (list first, holdings
# only when the user asks). The list call reuses the process-wide
# Concert directory cache - subsequent reloads are DB-only.
# -----------------------------------------------------------------------------
@router.get("/new-clients", tags=["Dashboard - Accounts"],
            summary="List Concert accounts not yet onboarded to PMG")
def getNewClients():
    try:
        rows = service.listNewClientAccounts()
        return {"success": True, "count": len(rows), "newClients": rows}
    except Exception as e:
        return _errorResponse(e)


@router.post("/new-clients/holdings", tags=["Dashboard - Accounts"],
             summary="Bulk-fetch live Concert holdings for new-client accounts")
async def getNewClientHoldings(request: Request):
    """Body: ``{accounts: ["acc1", "acc2", ...]}``. Returns
    ``{holdingsByAccount: {acc: [holding, ...]}}`` sourced live from
    Concert PM (batched 100 per API call)."""
    try:
        body = await request.json()
        accounts = body.get("accounts") or []
        if not isinstance(accounts, list):
            raise HTTPException(status_code=400, detail="'accounts' must be a list")
        # Concert API call is blocking (multiple network round-trips).
        # Offload so the event loop stays free.
        from starlette.concurrency import run_in_threadpool
        # INFERRED cropped call ending at original line 297.
        holdingsByAccount = await run_in_threadpool(service.getNewClientHoldings, accounts)
        return {"success": True, "holdingsByAccount": holdingsByAccount}
    except HTTPException:
        raise
    except Exception as e:
        return _errorResponse(e)


# INFERRED closing decorator text cropped in IMG_4212.
@router.post("/new-clients/{accountNumber}/save", tags=["Dashboard - Accounts"],
             summary="Persist live Concert holdings for one new-client account")
def saveNewClientHoldings(accountNumber: str):
    """Fetches the account's live Concert holdings and writes them into
    PMGPortfolioContent (same code path as the batch loader). After
    this succeeds the account will show up in the main Systematic
    Update Portfolios list and can be edited from the main dashboard."""
    try:
        result = service.saveNewClientHoldings(accountNumber)
        return result
    except Exception as e:
        return _errorResponse(e)


# -----------------------------------------------------------------------------
# Combined Concert sweep - powers "Load new clients" AND the stale-account
# guardrail preview in a SINGLE round-trip pass to Concert. The old design
# fired the directory + optimization-parameters calls twice (once per feature);
# this endpoint batches both classifications into one sweep so we save
# round-trips whenever either bucket would round up its own batch.
# Read-only: no DB writes. Apply is a separate explicit step.
# -----------------------------------------------------------------------------
# INFERRED summary ending at original line 328.
@router.get("/concert-sweep", tags=["Dashboard - Accounts"],
            summary="Single Concert sweep: new-client candidates + stale DB preview")
def getConcertSweep():
    try:
        return service.sweepConcertPortfolios()
    except Exception as e:
        return _errorResponse(e)


# -----------------------------------------------------------------------------
# Progress Tracking page - workflow-oriented status stepper.
# Currently drives ``/progressTracking/progressTracking.html`` for the
# ISG-model-change workflow. Additional workflows can be added by
# introducing sibling handler methods and dispatching here on
# ``workflow``.
# -----------------------------------------------------------------------------
@router.get("/progress/isg-model-change",
            tags=["Dashboard - Progress Tracking"],
            summary="Per-step status envelope for the ISG-model-change workflow")
def getIsgModelChangeProgress(
    schemaCode: str = 'ISG2',
    holdingsStaleDays: int = 7,
    force: bool = False,
):
    """Returns ``{success, steps: [...], doneCount, totalSteps, ...}``.
    Each step carries ``id``, ``title``, ``status``
    (``pending`` | ``ready`` | ``in-progress`` | ``done`` | ``error``),
    ``summary``, ``action?`` and ``details?``. Never raises - a
    per-step fetch failure surfaces as ``status='error'`` on that
    step so the rest of the page still renders.

    Server-side cached for 1 hour keyed on ``schemaCode``; pass
    ``force=true`` (Refresh button) to bypass the cache."""
    try:
        return service.getIsgModelChangeProgress(
            schemaCode=schemaCode,
            holdingsStaleDays=holdingsStaleDays,
            force=force,
        )
    except Exception as e:
        return _errorResponse(e)


# INFERRED summary ending at original line 373.
@router.get("/progress/concert-request",
            tags=["Dashboard - Progress Tracking"],
            summary="Per-step status envelope for the Concert-PM external-request workflow")
def getConcertRequestProgress(
    force: bool = False,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
):
    """Returns the same envelope shape as ``/progress/isg-model-change``
    but scoped to external-API sessions (sessions received from
    Concert-PM). Steps: requestsReceived → validation → optimization →
    approval → sendToConcert. Server-cached for 1 hour; pass
    ``force=true`` (Refresh button) to bypass.

    ``fromDate`` / ``toDate`` (ISO-8601) restrict the counts to sessions
    whose ``requestTime`` falls in the range. Omit both to include the
    entire history returned by the underlying listing helper."""
    try:
        return service.getConcertRequestProgress(
            force=force, fromDate=fromDate, toDate=toDate,
        )
    except Exception as e:
        return _errorResponse(e)


# INFERRED summary ending at original line 399.
@router.post("/stale-accounts/deactivate",
             dependencies=[Depends(requireEditor)],
             tags=["Dashboard - Accounts"],
             summary="Mark the supplied accounts' PMGPortfolioContent LIVE rows as inactive")
async def deactivateStaleAccounts(request: Request):
    """Body: ``{accounts: ["acc1", ...]}``. Guardrailed write: the
    caller supplies the exact list to demote (sourced from a prior
    ``/concert-sweep``) so the user has already reviewed the count
    before this fires. No fresh Concert call - the applied set
    matches exactly what the preview showed."""
    try:
        body = await request.json()
        accounts = body.get("accounts") or []
        if not isinstance(accounts, list):
            raise HTTPException(status_code=400, detail="'accounts' must be a list")
        from starlette.concurrency import run_in_threadpool
        # INFERRED cropped call ending at original line 412.
        return await run_in_threadpool(service.deactivateStaleAccounts, accounts)
    except HTTPException:
        raise
    except Exception as e:
        return _errorResponse(e)


# -----------------------------------------------------------------------------
# "Sync with Concert" - dashboard menu (batch-optimization page header).
# Backends live on :class:`DashboardHandler` (``syncRefreshHoldings`` /
# ``syncConcertModels``). Both use ``run_in_threadpool`` because they
# do synchronous DB + Concert HTTP work; running them on the event loop
# would stall every other request while a full-library refresh (~20
# batches of 100 accounts) is in flight.
# -----------------------------------------------------------------------------
@router.post("/sync/refresh-holdings-batch",
             dependencies=[Depends(requireEditor)],
             tags=["Dashboard - Accounts"],
             summary="Refresh Concert PM holdings for one UI-chunked batch of "
                     "accounts. Body: ``{accounts: ['ACC1', ...]}``. Frontend "
                     "sends ~100 accounts per call and paints its own progress "
                     "bar based on the per-batch response.")
async def syncRefreshHoldingsBatch(request: Request):
    """UI-driven batching: the browser chunks the DB account list into
    ~100-item slices and calls this endpoint once per slice. Each call
    completes in a few seconds - well under the API-gateway HTTP
    timeout that used to fire on the old "one giant server-side
    sweep" endpoint.

    Never raises - errors surface as ``{success: False, error: '…'}``.
    """
    try:
        body = await request.json()
        accounts = body.get('accounts') or []
        if not isinstance(accounts, list):
            raise HTTPException(status_code=400, detail="'accounts' must be a list")
        from starlette.concurrency import run_in_threadpool
        # INFERRED cropped call ending at original line 449.
        return await run_in_threadpool(service.refreshHoldingsForAccounts, accounts)
    except HTTPException:
        raise
    except Exception as e:
        return _errorResponse(e)


# INFERRED summary ending at original line 459.
@router.post("/sync/concert-models",
             dependencies=[Depends(requireEditor)],
             tags=["Dashboard - Accounts"],
             summary="One-click sync of Concert models with PMGConcertModels (accept all)")
async def syncConcertModelsWithDb():
    """Runs the drift validation and auto-accepts every ``new`` /
    ``changed`` / ``deleted`` item. Equivalent to opening the drift
    panel and clicking Accept-all."""
    try:
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(service.syncConcertModels)
    # INFERRED exception tail: original lines 467-468 are not supplied.
    except Exception as e:
        return _errorResponse(e)


# MISSING original lines 469-796. Includes unknown endpoints, helper/default
# definitions and the beginning of getEquityModelFactors. Visible fragments:
# [792] def getEquityModelFactors(riskModelId: str = _DEFAULT_RISK_MODEL_ID):
# [797]         return {
# [798]             "success":     True,
# [799]             "riskModelId": riskModelId,
# [800]             "factors":     factors,
# [801]         }
# [802]     except Exception as e:
# [803]         return _errorResponse(e)


# Original source resumes at line 806.
@router.get("/factor-analytics/combinations",
            tags=["Dashboard - Analytics"],
            summary="List the caller's saved combination names for a "
                    "risk model. DEFAULT is always seeded by the loader.")
def listFactorCombinations(request: Request,
                           riskModelId: str = _DEFAULT_RISK_MODEL_ID):
    try:
        userName = _resolveUserName(request)
        # Read-through fallback: if the caller has never saved anything
        # of their own, fall back to the SYSTEM-owned DEFAULT seeded by
        # the loader so the UI has something to show.
        names = PMGFactorGroupsDBUtil.listCombinations(
            riskModelId=riskModelId, userName=userName,
        )
        if not names:
            names = PMGFactorGroupsDBUtil.listCombinations(
                riskModelId=riskModelId, userName='SYSTEM',
            )
        return {
            "success": True,
            "riskModelId": riskModelId,
            "combinationNames": names,
        }
    except Exception as e:
        return _errorResponse(e)


@router.delete("/factor-analytics/combinations/{combinationName}",
               dependencies=[Depends(requireEditor)],
               tags=["Dashboard - Analytics"],
               summary="Soft-delete a combination for the caller. "
                       "DEFAULT can be deleted per-user; the SYSTEM "
                       "seed remains available as a fallback.")
def deleteFactorCombination(request: Request, combinationName: str,
                             riskModelId: str = _DEFAULT_RISK_MODEL_ID):
    try:
        userName = _resolveUserName(request)
        rows = PMGFactorGroupsDBUtil.deleteCombination(
            riskModelId=riskModelId,
            combinationName=combinationName,
            userName=userName,
        )
        return {"success": True, "rowsDeactivated": rows}
    except Exception as e:
        return _errorResponse(e)


@router.get("/health", tags=["Dashboard - Health"],
            summary="Dashboard health check")
def healthCheck():
    return {"status": "healthy", "service": "Portfolio Optimization Dashboard API"}


# MISSING original section heading at lines 929-931.
# =============================================================================
# INFERRED cropped summary endings at lines 935, 947 and 959 below.
@router.post("/generate-constraints", tags=["Dashboard - Constraints"],
             summary="Generate ISG constraints and asset list from model parameters")
async def generateConstraints(request: Request):
    try:
        data = await request.json()
        from starlette.concurrency import run_in_threadpool
        # INFERRED cropped call/return endings at original lines 940-941.
        result = await run_in_threadpool(service.generateConstraintsAndAssetList, data)
        return {"success": True, "constraints": result.get("constraints", ""),
                "assetList": result.get("assetList", "")}
    except Exception as e:
        return _errorResponse(e)


@router.post("/default-target-vol", tags=["Dashboard - Constraints"],
             summary="Get default ISG target volatility for given config parameters")
async def getDefaultTargetVol(request: Request):
    try:
        data = await request.json()
        from starlette.concurrency import run_in_threadpool
        vol = await run_in_threadpool(service.getDefaultTargetVol, data)
        return {"success": True, "targetVol": vol}
    except Exception as e:
        return _errorResponse(e)


@router.post("/generate-constraints-vol", tags=["Dashboard - Constraints"],
             summary="Generate constraints and asset list for a given target volatility")
async def generateConstraintsFromVolatility(request: Request):
    try:
        data = await request.json()
        volatility = data.pop("targetVol", None)
        if volatility is None:
            raise HTTPException(status_code=400, detail="targetVol is required")
        from starlette.concurrency import run_in_threadpool
        # INFERRED argument order and return suffix at original lines 967-968.
        result = await run_in_threadpool(service.generateConstraintsFromVolatility, data, volatility)
        return {"success": True, "constraints": result.get("constraints", ""),
                "assetList": result.get("assetList", "")}
    except HTTPException:
        raise
    except Exception as e:
        return _errorResponse(e)
        return _errorResponse(e)  # Duplicate is visible at original line 973.


# Duplicate route/function exists in IMG_4221 at original lines 976-985.
@router.post("/default-target-vol", tags=["Dashboard - Constraints"],
             summary="Get default ISG target volatility for given config parameters")
async def getDefaultTargetVol(request: Request):
    try:
        from starlette.concurrency import run_in_threadpool
        data = await request.json()
        vol = await run_in_threadpool(service.getDefaultTargetVol, data)
        return {"success": True, "targetVol": vol}
    except Exception as e:
        return _errorResponse(e)

@router.post("/asset-class-mappings/preview/{accountNumber}",
             tags=["Dashboard - Asset Class Mapping"],
             summary="Preview holdings and ISG model after applying asset class mappings")
async def previewAssetClassMappings(accountNumber: str, request: Request):
    try:
        from starlette.concurrency import run_in_threadpool
        body = await request.body()
        configData = json.loads(body) if body else None
        # INFERRED cropped call ending at original line 1528.
        return await run_in_threadpool(service.previewAssetClassMappings, accountNumber, configData)
    except Exception as e:
        return _errorResponse(e)


# =============================================================================
# Client Constraint Setup
# =============================================================================
@router.get("/constraint-enums", tags=["Dashboard - Constraint Setup"],
            summary="Return all ConstraintFields and their associated value-type enums")
def getConstraintEnums():
    try:
        return service.getConstraintEnums()
    except Exception as e:
        return _errorResponse(e)


@router.get("/product-columns", tags=["Dashboard - Constraint Setup"],
            summary="Return PMGProduct column names for product property constraints")
def getProductColumns():
    try:
        return service.getProductColumnNames()
    except Exception as e:
        return _errorResponse(e)


@router.get("/product-column-values/{columnName}", tags=["Dashboard - Constraint Setup"],
            summary="Return distinct values for a specific PMGProduct column")
def getProductColumnValues(columnName: str):
    try:
        return service.getProductColumnValues(columnName)
    except Exception as e:
        return _errorResponse(e)


@router.get("/restrictions/{accountNumber}", tags=["Dashboard - Constraint Setup"],
            summary="Get raw constraint data from Account_Restrictions for account")
def getAccountRestrictions(accountNumber: str):
    try:
        restrictions = service.getAccountRestrictions(accountNumber)
        return {"success": True, "restrictions": restrictions, "count": len(restrictions)}
    # INFERRED exception tail, original lines 1590-1592 not supplied.
    except Exception as e:
        return _errorResponse(e)


@router.get("/restriction-accounts", tags=["Dashboard - Constraint Setup"],
            summary="Get all unique account numbers from the restrictions file")
def getRestrictionAccounts():
    try:
        accounts = service.getAllRestrictionAccounts()
        return {"success": True, "accounts": accounts}
    except Exception as e:
        return _errorResponse(e)


@router.get("/restriction-templates", tags=["Dashboard - Constraint Setup"],
            summary="Get all unique template names from the restrictions file")
def getRestrictionTemplates():
    try:
        templates = service.getRestrictionTemplateNames()
        return {"success": True, "templates": templates}
    except Exception as e:
        return _errorResponse(e)


# INFERRED cropped tags at original line 1614.
@router.post("/restrictions/{accountNumber}/save", dependencies=[Depends(requireEditor)],
             tags=["Dashboard - Constraint Setup"],
             summary="Save configured constraints for an account (placeholder)")
async def saveAccountConstraints(accountNumber: str, request: Request):
    try:
        data = await request.json()
        # TODO: implement saving logic
        # Original line 1620 ends off-screen after "Constraint da"; its
        # complete return contract cannot be recovered. Visible prefix:
        # return {"success": True, "message": "Save function not yet implemented. Constraint da[ cropped ]
        # Added explicit gap marker instead of inventing the response suffix.
        raise NotImplementedError("Original return at source line 1620 is cropped")
    except Exception as e:
        return _errorResponse(e)


# =============================================================================
# Guideline DB
# =============================================================================
@router.get("/guideline-accounts", tags=["Dashboard - Guidelines"],
            summary="Get all account numbers that have guidelines")
def getGuidelineAccounts():
    try:
        accounts = service.getGuidelineAccountNumbers()
        return {"success": True, "accounts": accounts}
    except Exception as e:
        return _errorResponse(e)


@router.get("/guidelines-all", tags=["Dashboard - Guidelines"],
            summary="Load all guidelines across every account the user owns")
def getAllGuidelines():
    """Single-shot fetch used by the constraint-setup page so it can
    show every account's guidelines together (with account-number /
    constraint-name filters applied client-side)."""
    try:
        payload = service.getAllGuidelinesFromDB()
        return {
            "success": True,
            "guidelines": payload.get("guidelines", []),
            "accounts": payload.get("accounts", []),
            "count": payload.get("count", 0),
        }
    except Exception as e:
        return _errorResponse(e)
