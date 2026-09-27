"""
PMG entitlement / authentication.

The Permit policy (see the .gel snippet in the design doc) defines:

* three **roles**:     ``ISGAdmin``, ``PMGEditor``, ``PMGViewer``
* three **resources**: ``pmgui``, ``pmgapi``, ``optimizationapi``
* three **actions**:   ``view``, ``modify``, ``post``

with the following privileges (row = role, column = ``resource:action``):


| Role      | pmgui:view | pmgui:modify | pmgui:post | pmgapi:view | pmgapi:modify | pmgapi:post | optimizationapi:view | optimizationapi:modify | optimizationapi:post |
|-----------|------------|--------------|------------|-------------|---------------|-------------|----------------------|------------------------|----------------------|
| ISGAdmin  | yes        | yes          | -          | yes         | yes           | -           | yes                  | yes                    | -                    |
| PMGEditor | yes        | yes          | yes        | yes         | yes           | yes         | yes                  | yes                    | yes                  |
| PMGViewer | yes        | -            | -          | yes         | -             | -           | yes                  | -                      | -                    |

**Note** ``post`` is a PMGEditor-only privilege (ISGAdmin does NOT have
``post`` on any resource, per the policy).

Layering with the kerberos allowlist
-----------------------------------
Auth is applied in two layers:

1. **Kerberos allowlist gate** (``accessControl.requireAllowlistedUser``)
   - extracts the kerberos from the GSSSO cookie / REMOTE_USER header and
   rejects everything that isn't on ``ALLOWED_KERBEROS``.

2. **Permit / role-based check** - this module. Grants are the union of:

   * whatever every role the user holds in PERMIT grants them, and
   * the *env-default grant* from being on the allowlist:

   | Env                 | Allowlist grant                              |
   |---------------------|----------------------------------------------|
   | DEV / UAT and below | ``view + modify`` on ALL resources (Editor)   |
   | PROD                | ``view`` on ALL resources (Viewer)            |

   So in PROD the allowlist alone only lets you read; to modify or post
   the kerberos must also be in the ``PMGEditor`` / ``ISGAdmin`` PERMIT
   group.

Multi-role
----------
A user may hold multiple roles at once. ``UserData`` therefore carries
the union of every role's ``resource → {actions}`` map plus the
allowlist bonus; ``.role`` remains the single highest-priority role
string for backward compat.

Public API for endpoints
------------------------
Prefer the resource-specific dependency factories so each endpoint
declares exactly what it needs::

    from cyrus_pmg.pmgService.core.pmgEntitlement import (
        requirePmgApiView,     # any of the three roles
        requirePmgApiModify,   # ISGAdmin / PMGEditor
        requirePmgApiPost,     # PMGEditor only (per policy)
        requireOptApiView, requireOptApiModify, requireOptApiPost,
    )

    @router.post("/save", dependencies=[Depends(requirePmgApiModify)])
    def save(userData: UserData = Depends(requirePmgApiModify)):
        ...

Back-compat shims - ``requireAuth`` / ``requireEditor`` / ``requirePoster``
still exist and now map to (``pmgapi``, ``view`` / ``modify`` / ``post``).

RECONSTRUCTION NOTES (added during transcription)
------------------------------------------------
Transcribed from IMG_4226(1).jpeg through IMG_4230(1).jpeg and IMG_4231.jpeg
through IMG_4243.jpeg. All executable source after the folded imports is
visible across the overlapping screenshots, through original line 585.

Original lines 77-92 are collapsed. Standard-library/FastAPI imports and
logger initialization below are inferred from visible usage. Project imports
for Env, PermitUtil, GSSOCookie, isAllowed, requireAllowlistedUser and
PmgAppException remain unresolved: their module paths are not shown here.
The visible SPA UserData import is preserved exactly.

The first screenshot crops the left edge of the opening prose and both sides
of its permission table. Opening words/bullets are inferred; both docstring
tables have been reformatted. No executable permission logic was inferred or
refactored. Existing comments and behaviour, including inconsistencies, are
preserved. Original source line references differ from output line numbers.

An added reconstruction guard prevents execution until the missing imports
are restored. Syntax checking does not validate external integrations,
authentication behaviour, or the original policy's correctness.
"""

# INFERRED from visible usage; original import block at 77-92 is folded.
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Callable, Dict, List, Optional, Set, Tuple

from fastapi import Depends, Request

logger = logging.getLogger(__name__)  # INFERRED initialization.

# -----------------------------------------------------------------------------
# Re-use SPA's UserData when possible for downstream compatibility.
# -----------------------------------------------------------------------------
try:
    from cyrus_spa.cyrus_spa_core.dataModel.userData import UserData
except ImportError:
    class UserData:  # type: ignore[no-redef]
        kerberos: str = "unknown"
        role: str = ""
        roles: list = []
        # ``permissions`` maps resource name -> set of action names.
        permissions: dict = {}
        # Legacy flat list of actions (union across every resource) kept
        # so old ``canModify(u) / canPost(u)`` helpers still work.
        actionsAllowed: list = []

        def __repr__(self):
            return (
                f"UserData(kerberos={self.kerberos!r}, role={self.role!r}, "
                f"roles={getattr(self, 'roles', [])!r}, "
                f"permissions={getattr(self, 'permissions', {})!r})"
            )


# -----------------------------------------------------------------------------
# Roles / resources / actions
# -----------------------------------------------------------------------------
ROLE_ADMIN = "ISGAdmin"
ROLE_EDITOR = "PMGEditor"
ROLE_VIEWER = "PMGViewer"

# Highest-priority first - used to derive the single ``role`` field on
# UserData and to sort ``roles`` for logging.
ROLE_PRIORITY = [ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER]

RESOURCE_PMGUI = "pmgui"
RESOURCE_PMGAPI = "pmgapi"
RESOURCE_OPTIMIZATIONAPI = "optimizationapi"
ALL_RESOURCES = (RESOURCE_PMGUI, RESOURCE_PMGAPI, RESOURCE_OPTIMIZATIONAPI)

ACTION_VIEW = "view"
ACTION_MODIFY = "modify"
ACTION_POST = "post"
ALL_ACTIONS = (ACTION_VIEW, ACTION_MODIFY, ACTION_POST)

# Role -> { resource: {actions} }. EXACT transcription of the .gel policy;
# do NOT infer permissions from one row to another - keep this table
# authoritative so any future policy change is a one-line edit.
ROLE_PERMISSIONS: Dict[str, Dict[str, Set[str]]] = {
    ROLE_ADMIN: {
        RESOURCE_PMGUI:           {ACTION_VIEW, ACTION_MODIFY},
        RESOURCE_PMGAPI:          {ACTION_VIEW, ACTION_MODIFY},
        RESOURCE_OPTIMIZATIONAPI: {ACTION_VIEW, ACTION_MODIFY},
    },
    ROLE_EDITOR: {
        RESOURCE_PMGUI:           {ACTION_VIEW, ACTION_MODIFY, ACTION_POST},
        RESOURCE_PMGAPI:          {ACTION_VIEW, ACTION_MODIFY, ACTION_POST},
        RESOURCE_OPTIMIZATIONAPI: {ACTION_VIEW, ACTION_MODIFY, ACTION_POST},
    },
    ROLE_VIEWER: {
        RESOURCE_PMGUI:           {ACTION_VIEW},
        RESOURCE_PMGAPI:          {ACTION_VIEW},
        RESOURCE_OPTIMIZATIONAPI: {ACTION_VIEW},
    },
}

# Env-default allowlist grants - see module docstring.
_ALLOWLIST_GRANT_NONPROD: Dict[str, Set[str]] = {
    res: {ACTION_VIEW, ACTION_MODIFY} for res in ALL_RESOURCES
}
_ALLOWLIST_GRANT_PROD: Dict[str, Set[str]] = {
    res: {ACTION_VIEW} for res in ALL_RESOURCES
}


# -----------------------------------------------------------------------------
# Environment helpers
# -----------------------------------------------------------------------------
def _isProdEnv() -> bool:
    return "PROD" in (Env.get_env() or "").upper()


def _isAuthEnforcedEnv() -> bool:
    """PROD and UAT enforce the PERMIT lookup; everything else short-
    circuits to a dev-mode ISGAdmin."""
    env = (Env.get_env() or "").upper()
    return "PROD" in env or "UAT" in env


def _allowlistGrant() -> Dict[str, Set[str]]:
    """Env-appropriate ``resource -> actions`` grant a kerberos receives
    simply for being on ``ALLOWED_KERBEROS``."""
    return dict(_ALLOWLIST_GRANT_PROD if _isProdEnv() else _ALLOWLIST_GRANT_NONPROD)


# -----------------------------------------------------------------------------
# Internal builders
# -----------------------------------------------------------------------------
def _sortRoles(roles) -> List[str]:
    """Deduplicated, high -> low priority."""
    seen, ordered = set(), []
    for r in ROLE_PRIORITY:
        if r in roles and r not in seen:
            ordered.append(r); seen.add(r)
    # Preserve any unknown roles (defensive against future PERMIT roles).
    for r in roles:
        if r not in seen:
            ordered.append(r); seen.add(r)
    return ordered


def _permissionsFromRoles(roles) -> Dict[str, Set[str]]:
    """Union :data:`ROLE_PERMISSIONS` over the given roles."""
    merged: Dict[str, Set[str]] = defaultdict(set)
    for r in roles:
        for res, actions in ROLE_PERMISSIONS.get(r, {}).items():
            merged[res].update(actions)
    return dict(merged)


def _permissionsFromPermit(kerberos: str) -> Dict[str, Set[str]]:
    """Ask PERMIT directly what privileges (resource:action pairs) this
    kerberos is entitled to and return them as a ``{resource: {actions}}``
    map, filtered to the PMG-known resources/actions.

    This is the authoritative check - the role→permission mapping in
    :data:`ROLE_PERMISSIONS` is only a fallback for when the PERMIT
    ``allowedPrivileges`` call fails or returns nothing.
    """
    try:
        raw = PermitUtil.getAllowedPrivileges(kerberos) or []
    except Exception:
        logger.exception(
            "PMG auth: PermitUtil.getAllowedPrivileges failed for '%s'", kerberos,
        )
        return {}

    perms: Dict[str, Set[str]] = defaultdict(set)
    for entry in raw:
        res = entry.get("resource")
        act = entry.get("action")
        if res in ALL_RESOURCES and act in ALL_ACTIONS:
            perms[res].add(act)
    return dict(perms)


def _mergePermissions(*maps: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    merged: Dict[str, Set[str]] = defaultdict(set)
    for m in maps:
        for res, actions in (m or {}).items():
            merged[res].update(actions)
    return dict(merged)


def _flatActions(perms: Dict[str, Set[str]]) -> List[str]:
    """Legacy flat action list (union across every resource) in canonical
    view/modify/post order."""
    acc: Set[str] = set()
    for actions in perms.values():
        acc.update(actions)
    return [a for a in ALL_ACTIONS if a in acc]


def _buildUser(
    kerberos: str,
    roles,
    extraPermissions: Optional[Dict[str, Set[str]]] = None,
) -> UserData:
    """Assemble a :class:`UserData` from a role list + optional allowlist
    bonus grant. The bonus is unioned on top of the role permissions."""
    sortedRoles = _sortRoles(list(roles))
    perms = _mergePermissions(
        _permissionsFromRoles(sortedRoles),
        extraPermissions or {},
    )
    u = UserData()
    u.kerberos = kerberos
    u.roles = sortedRoles
    u.role = sortedRoles[0] if sortedRoles else ""
    u.permissions = perms
    u.actionsAllowed = _flatActions(perms)
    return u


# -----------------------------------------------------------------------------
# Entitlement class
# -----------------------------------------------------------------------------
class PmgEntitlement:
    """In-process user/role cache backed by PERMIT."""

    roles = ROLE_PRIORITY

    userDataCache: dict = {}
    lastCacheUpdateTimestamp: Optional[datetime] = None
    cacheRefreshIntervalHours: int = 1
    cacheRefreshInterval: timedelta = timedelta(hours=cacheRefreshIntervalHours)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------
    @staticmethod
    def checkValidUser(token) -> Tuple[bool, UserData]:
        if token is None:
            return False, UserData()
        try:
            gssoToken = token.value if hasattr(token, "value") else token
            kerberos = None
            try:
                cookie = GSSOCookie(GSSOCookieStr=gssoToken)
                kerberos = cookie.getKerberos()
            except Exception as e:
                logger.error("PMG auth: error extracting kerberos from GSSSO token - %s", e)

            if kerberos:
                return PmgEntitlement.isValidUser(kerberos)
            return False, UserData()
        except Exception as e:
            logger.error("PMG auth: unexpected error in checkValidUser - %s", e)
            return False, UserData()

    @staticmethod
    def checkValidKerberos(kerberos: str) -> Tuple[bool, UserData]:
        if not kerberos:
            return False, UserData()
        try:
            return PmgEntitlement.isValidUser(kerberos)
        except Exception as e:
            logger.error("PMG auth: unexpected error in checkValidKerberos - %s", e)
            return False, UserData()

    @staticmethod
    def isValidUser(kerberos: str) -> Tuple[bool, UserData]:
        """Resolve a kerberos to a fully-populated :class:`UserData`.

        Permissions are unioned from two sources:

          * every PERMIT role membership found in the cache;
          * the env-appropriate allowlist bonus (if the kerberos is on
            ``ALLOWED_KERBEROS``).

        In DEV/lower the cache is a synthetic ISGAdmin superuser, so the
        allowlist bonus is redundant there.
        """
        onAllowlist = isAllowed(kerberos)
        PmgEntitlement.refreshCache()

        cachedRoles: List[str] = []
        cached = PmgEntitlement.userDataCache.get(kerberos)
        if cached is not None:
            cachedRoles = list(
                getattr(cached, "roles", None)
                or ([cached.role] if getattr(cached, "role", None) else [])
            )

        # Authoritative entitlement lookup: ask PERMIT what
        # resource:action grants this specific kerberos has. Only
        # performed in enforced envs; in DEV we already synthesised an
        # ISGAdmin superuser in the cache.
        permitPerms: Dict[str, Set[str]] = {}
        if _isAuthEnforcedEnv():
            permitPerms = _permissionsFromPermit(kerberos)

        # Allowlist bonus (env-dependent).
        allowlistPerms = _allowlistGrant() if onAllowlist else {}

        # Merge every source; PERMIT's answer takes precedence in spirit
        # but union is safe because it can only add, not remove.
        extra = _mergePermissions(permitPerms, allowlistPerms)

        if cachedRoles or permitPerms or onAllowlist:
            return True, _buildUser(kerberos, cachedRoles, extraPermissions=extra)

        return False, UserData()

    @staticmethod
    def getUsersWithRole(role: str) -> list:
        PmgEntitlement.refreshCache()
        return [
            k for k, u in PmgEntitlement.userDataCache.items()
            if role in (getattr(u, "roles", None) or [getattr(u, "role", None)])
        ]

    # -------------------------------------------------------------------------
    # Cache management
    # -------------------------------------------------------------------------
    @staticmethod
    def refreshCache() -> None:
        cacheIsEmpty = len(PmgEntitlement.userDataCache) == 0
        cacheIsStale = (
            PmgEntitlement.lastCacheUpdateTimestamp is None
            or (datetime.now() - PmgEntitlement.lastCacheUpdateTimestamp)
            > PmgEntitlement.cacheRefreshInterval
        )
        if not (cacheIsEmpty or cacheIsStale):
            return

        if not _isAuthEnforcedEnv():
            # DEV / local: synthetic ISGAdmin superuser (all resources,
            # all actions granted to ISGAdmin per ROLE_PERMISSIONS).
            u = _buildUser("unknown", [ROLE_ADMIN])
            PmgEntitlement.userDataCache = defaultdict(lambda: u)
            PmgEntitlement.lastCacheUpdateTimestamp = datetime.now()
            return

        # PROD / UAT - pull real memberships from PERMIT.
        readFailure = False
        oldCache = PmgEntitlement.userDataCache.copy()
        rolesByUser: Dict[str, Set[str]] = {}

        for role in PmgEntitlement.roles:
            try:
                members = PermitUtil.getMembers(role)
                for m in members:
                    rolesByUser.setdefault(m, set()).add(role)
            except Exception:
                logger.exception("PMG auth: failed to read PERMIT members for role '%s'", role)
                readFailure = True

        newCache = {
            kerberos: _buildUser(kerberos, roles)
            for kerberos, roles in rolesByUser.items()
        }

        if newCache:
            PmgEntitlement.userDataCache = newCache
            PmgEntitlement.lastCacheUpdateTimestamp = datetime.now()
        else:
            PmgEntitlement.userDataCache = oldCache
            if readFailure:
                logger.error("PMG auth: PERMIT cache refresh failed - retaining old cache.")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _userRoles(userData: UserData) -> List[str]:
    roles = getattr(userData, "roles", None)
    if roles:
        return list(roles)
    single = getattr(userData, "role", None)
    return [single] if single else []


def _userPerms(userData: UserData) -> Dict[str, Set[str]]:
    """Return the user's ``{resource: {actions}}`` map. Falls back to
    deriving from ``roles`` for legacy UserData instances that don't
    carry ``permissions``."""
    perms = getattr(userData, "permissions", None)
    if perms:
        return {res: set(actions) for res, actions in perms.items()}
    return _permissionsFromRoles(_userRoles(userData))


def hasRole(userData: UserData, *roles: str) -> bool:
    ur = set(_userRoles(userData))
    return any(r in ur for r in roles)


def hasAllRoles(userData: UserData, *roles: str) -> bool:
    ur = set(_userRoles(userData))
    return all(r in ur for r in roles)


def can(userData: UserData, resource: str, action: str) -> bool:
    """Primary permission check - ``True`` iff the user's merged
    permissions grant ``action`` on ``resource``."""
    return action in _userPerms(userData).get(resource, set())


def canView(userData: UserData) -> bool:
    return ACTION_VIEW in (getattr(userData, "actionsAllowed", None) or [])


def canModify(userData: UserData) -> bool:
    """True iff the user can ``modify`` on *any* resource. Prefer
    :func:`can` for a resource-specific check."""
    return ACTION_MODIFY in (getattr(userData, "actionsAllowed", None) or [])


def canPost(userData: UserData) -> bool:
    """True iff the user can ``post`` on *any* resource.

    Per the policy this is a **PMGEditor-only** privilege - ``ISGAdmin``
    does NOT have ``post``. Prefer :func:`can` for a resource-specific
    check.
    """
    return ACTION_POST in (getattr(userData, "actionsAllowed", None) or [])


# -----------------------------------------------------------------------------
# FastAPI dependencies
# -----------------------------------------------------------------------------
def _resolveUserData(kerberos: str) -> UserData:
    """Turn a kerberos into a :class:`UserData` (allowlist → PERMIT).
    Callers past this point already passed ``requireAllowlistedUser`` so
    we default to a viewer if PERMIT has nothing for them (they still
    got past the allowlist)."""
    _, userData = PmgEntitlement.isValidUser(kerberos)
    if not getattr(userData, "kerberos", None):
        userData = _buildUser(kerberos, [ROLE_VIEWER])
    return userData


def _authDependency(
    request: Request,
    kerberos: str = Depends(requireAllowlistedUser),
) -> UserData:
    """Shared entry-point dependency: resolve UserData + log."""
    userData = _resolveUserData(kerberos)
    logger.info(
        "PMG auth: user='%s' role='%s' roles=%s env='%s' path='%s' "
        "permissions=%s",
        getattr(userData, "kerberos", "unknown"),
        getattr(userData, "role", ""),
        getattr(userData, "roles", []),
        Env.get_env(),
        request.url.path,
        {res: sorted(actions) for res, actions in _userPerms(userData).items()},
    )
    return userData


def requireCan(resource: str, action: str) -> Callable[..., UserData]:
    """Build a FastAPI dependency that authorises ``resource:action``.

    Usage::

        @router.post("/save",
                     dependencies=[Depends(requireCan("pmgapi", "modify"))])
    """
    if resource not in ALL_RESOURCES:
        raise ValueError(f"Unknown resource {resource!r}")
    if action not in ALL_ACTIONS:
        raise ValueError(f"Unknown action {action!r}")

    def _dep(
        request: Request,
        userData: UserData = Depends(_authDependency),
    ) -> UserData:
        if can(userData, resource, action):
            return userData

        kerberos = getattr(userData, "kerberos", "unknown")
        roles = getattr(userData, "roles", []) or [getattr(userData, "role", "")]
        logger.error(
            "PMG auth: forbidden %s:%s attempt by '%s' roles=%s on %s",
            resource, action, kerberos, roles, request.url.path,
        )
        raise PmgAppException(
            statusCode=HTTPStatus.FORBIDDEN.value,
            reason=(
                f"Forbidden: user '{kerberos}' with roles {roles} is not "
                f"permitted to '{action}' on resource '{resource}'."
            ),
        )

    _dep.__name__ = f"require_{resource}_{action}"
    return _dep


# ----- Ready-made per-resource dependencies (import these from routers) ----
requirePmgUiView   = requireCan(RESOURCE_PMGUI,           ACTION_VIEW)
requirePmgUiModify = requireCan(RESOURCE_PMGUI,           ACTION_MODIFY)
requirePmgUiPost   = requireCan(RESOURCE_PMGUI,           ACTION_POST)

requirePmgApiView   = requireCan(RESOURCE_PMGAPI,          ACTION_VIEW)
requirePmgApiModify = requireCan(RESOURCE_PMGAPI,          ACTION_MODIFY)
requirePmgApiPost   = requireCan(RESOURCE_PMGAPI,          ACTION_POST)

requireOptApiView   = requireCan(RESOURCE_OPTIMIZATIONAPI, ACTION_VIEW)
requireOptApiModify = requireCan(RESOURCE_OPTIMIZATIONAPI, ACTION_MODIFY)
requireOptApiPost   = requireCan(RESOURCE_OPTIMIZATIONAPI, ACTION_POST)


# ----- Back-compat shims (kept so existing routers keep compiling) --------
# ``requireAuth`` used to accept "any of the three PMG roles". The closest
# resource-aware equivalent for the pmgService endpoints it protected is
# ``pmgapi:view`` - every role has that action.
requireAuth   = requirePmgApiView
requireEditor = requirePmgApiModify
requirePoster = requirePmgApiPost


if __name__ == "__main__":
    print("Env:", Env.get_env())
    PmgEntitlement.refreshCache()
    print("Cache keys:", list(PmgEntitlement.userDataCache.keys())[:5])
    _, u = PmgEntitlement.isValidUser("shhang")
    print("shhang →", u)
    print("  can pmgapi:modify?", can(u, RESOURCE_PMGAPI, ACTION_MODIFY))
    print("  can pmgapi:post?  ", can(u, RESOURCE_PMGAPI, ACTION_POST))
    print("  can opt:view?     ", can(u, RESOURCE_OPTIMIZATIONAPI, ACTION_VIEW))
