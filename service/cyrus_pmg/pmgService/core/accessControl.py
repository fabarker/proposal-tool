"""Access control - STAND-IN for the host's ``cyrus_pmg.pmgService.core.accessControl``.

The host authenticates with GSSSO/Kerberos and keeps an allowlist. Neither
exists in epsilon-phi, so this module stubs the *implementation* while keeping
every *seam* exactly where the host has it:

    getKerberosFromFlaskRequest(request)   identity from a Flask request
    getKerberosFromFastApiRequest(request) identity from a FastAPI request
    isAllowed(kerberos)                    the allowlist check
    getAllowlist()                         the allowlist source
    buildLoginUrl(nextUrl)                 where an unauthenticated browser goes
    requireAuth / requireEditor            FastAPI dependencies -> UserData

**The dependencies return a UserData, not a kerberos string.** The host's own
do (``pmgEntitlement.requireCan`` builds one), and a mirror that handed back a
string would let the whole suite pass against a shape the host never presents -
which is exactly how three write paths came to crash on it. What the stores
persist is still the kerberos: the block reads ``.kerberos`` at the boundary,
and nothing in ``scenario/`` ever sees this type.

What is stubbed, and how:

* The allowlist source is the ``PMG_ALLOWED_KERBEROS`` environment variable
  (comma-separated), exactly as local host development already configures it.
  An empty allowlist allows nobody - the same behaviour the host warns about.
* The identity is read from a ``kerberos`` cookie, or an ``X-Kerberos`` header
  for command-line calls. The host reads the GSSSO session instead; that is the
  only line that changes at port time.
* ``buildLoginUrl`` points at the dev login page the Flask frontend serves at
  ``/_dev_login`` (a stand-in for the GSSSO redirect). The host builds the real
  GSSSO URL here.

This file is NOT transplanted - the host already has its own.
"""

import os
from urllib.parse import quote

from fastapi import HTTPException, Request

_KERBEROS_COOKIE = 'kerberos'
_KERBEROS_HEADER = 'X-Kerberos'

# ---- the host's entitlement model, reproduced -------------------------------
# cyrus_pmg.pmgService.core.pmgEntitlement defines three roles over three
# resources and three actions, and hands endpoints a UserData carrying the
# union. The real UserData is cyrus_spa's; the host falls back to an inline
# class of its own when that import fails, and only ever reads these five
# attributes - kerberos, role, roles, permissions, actionsAllowed - always
# through getattr with a default. Those five are what this reproduces.

ROLE_ADMIN = 'ISGAdmin'
ROLE_EDITOR = 'PMGEditor'
ROLE_VIEWER = 'PMGViewer'
ROLE_PRIORITY = [ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER]

RESOURCE_PMGUI = 'pmgui'
RESOURCE_PMGAPI = 'pmgapi'
RESOURCE_OPTIMIZATIONAPI = 'optimizationapi'
ALL_RESOURCES = (RESOURCE_PMGUI, RESOURCE_PMGAPI, RESOURCE_OPTIMIZATIONAPI)

ACTION_VIEW = 'view'
ACTION_MODIFY = 'modify'
ACTION_POST = 'post'
ALL_ACTIONS = (ACTION_VIEW, ACTION_MODIFY, ACTION_POST)

#: role -> {resource: {actions}}. The host's policy table verbatim - note that
#: ``post`` is PMGEditor's alone and ISGAdmin does not hold it.
ROLE_PERMISSIONS = {
    ROLE_ADMIN: {r: {ACTION_VIEW, ACTION_MODIFY} for r in ALL_RESOURCES},
    ROLE_EDITOR: {r: {ACTION_VIEW, ACTION_MODIFY, ACTION_POST} for r in ALL_RESOURCES},
    ROLE_VIEWER: {r: {ACTION_VIEW} for r in ALL_RESOURCES},
}


class UserData:
    """Who is calling, in the shape the host's dependencies hand out."""

    def __init__(self, kerberos='', roles=()):
        ordered = [r for r in ROLE_PRIORITY if r in roles]
        permissions = {}
        for role in ordered:
            for resource, actions in ROLE_PERMISSIONS.get(role, {}).items():
                permissions.setdefault(resource, set()).update(actions)
        self.kerberos = kerberos
        self.roles = ordered
        self.role = ordered[0] if ordered else ''
        self.permissions = permissions
        self.actionsAllowed = [a for a in ALL_ACTIONS
                               if any(a in acts for acts in permissions.values())]

    def __repr__(self):
        return 'UserData(kerberos={!r}, role={!r}, roles={!r})'.format(
            self.kerberos, self.role, self.roles)


def hasRole(userData, *roles) -> bool:
    """True when the caller holds any of *roles*. The host's helper."""
    held = set(getattr(userData, 'roles', None) or [])
    return any(r in held for r in roles)


def can(userData, resource: str, action: str) -> bool:
    """True when the caller may take *action* on *resource*. The host's helper."""
    return action in (getattr(userData, 'permissions', None) or {}).get(resource, set())


def _rolesFor(kerberos: str):
    """The roles this stand-in grants. The host reads PERMIT membership; here
    the two environment lists stand in for it - the admin list is ISGAdmin, the
    access list on its own is PMGViewer.

    PMGViewer, not PMGEditor, because that is what the host's allowlist grants
    in PROD (``pmgEntitlement``: view on every resource; modify needs a PERMIT
    role). The proposal flow is gated on view alone (D77), so the mirror
    hands out the strictest grant the host ever will and the suite proves the
    flow on that footing - a PWA on the access list runs it end to end, and
    only the admin list reaches the repository.

    A kerberos on neither list holds nothing, as the host's isValidUser returns
    an empty UserData for one; and an admin must also be on the access list, so
    the role and the older string check agree about every caller."""
    if not isAllowed(kerberos):
        return []
    if kerberos in getAdminAllowlist():
        return [ROLE_ADMIN]
    return [ROLE_VIEWER]


def getAllowlist():
    """Return the set of allowed kerberos ids from PMG_ALLOWED_KERBEROS."""
    raw = os.getenv('PMG_ALLOWED_KERBEROS', '')
    return {item.strip() for item in raw.split(',') if item.strip()}


def isAllowed(kerberos):
    """True when *kerberos* is on the allowlist. An empty list allows nobody."""
    return bool(kerberos) and kerberos in getAllowlist()


def buildLoginUrl(nextUrl=None):
    """Return the login URL. GSSSO in the host; the /_dev_login stub here."""
    if nextUrl:
        return '/_dev_login?next={}'.format(quote(str(nextUrl), safe=''))
    return '/_dev_login'


def getKerberosFromFlaskRequest(request):
    """Extract the caller's kerberos id from a Flask request, or None."""
    kerberos = request.cookies.get(_KERBEROS_COOKIE)
    if not kerberos:
        kerberos = request.headers.get(_KERBEROS_HEADER)
    return kerberos or None


def getKerberosFromFastApiRequest(request):
    """Extract the caller's kerberos id from a FastAPI request, or None."""
    kerberos = request.cookies.get(_KERBEROS_COOKIE)
    if not kerberos:
        kerberos = request.headers.get(_KERBEROS_HEADER)
    return kerberos or None


def requireAuth(request: Request) -> UserData:
    """FastAPI dependency: an authenticated, allowlisted caller.

    Raises HTTPException with a dict detail; ``isgPMGService`` maps that onto
    the host error contract - a 401 body carrying ``loginUrl``, a 403 body
    carrying ``error`` - so callers see top-level fields, never ``detail``.
    """
    kerberos = getKerberosFromFastApiRequest(request)
    if not kerberos:
        raise HTTPException(status_code=401, detail={'loginUrl': buildLoginUrl()})
    if not isAllowed(kerberos):
        raise HTTPException(
            status_code=403,
            detail={'error': 'User {} is not on the access list.'.format(kerberos)},
        )
    return UserData(kerberos, _rolesFor(kerberos))


def requireEditor(request: Request) -> UserData:
    """FastAPI dependency: the caller may modify. The host's equivalent is
    ``pmgapi:modify``, which ISGAdmin and PMGEditor hold and the PROD allowlist
    does not. Kept because the host has it; the endpoint block no longer uses
    it (D77) - the proposal flow is a PWA's own work and needs only view."""
    userData = requireAuth(request)
    if not can(userData, RESOURCE_PMGAPI, ACTION_MODIFY):
        raise HTTPException(
            status_code=403,
            detail={'error': 'User {} may not modify.'.format(userData.kerberos)},
        )
    return userData


# ---- the admin role (D57) ----------------------------------------------------
# A third list beside the access list. Admins maintain the sleeve repository -
# the library every PWA picks from - so the list is expected to be a handful
# of names, and an empty list means nobody: the console simply does not
# appear. In the host this reads whatever source the other allowlist reads.

def getAdminAllowlist():
    """Return the set of admin kerberos ids from PMG_ADMIN_KERBEROS."""
    raw = os.getenv('PMG_ADMIN_KERBEROS', '')
    return {item.strip() for item in raw.split(',') if item.strip()}


def isAdmin(user) -> bool:
    """True when the caller may maintain the sleeve repository.

    Takes a UserData or a bare kerberos, because the schema route has only the
    latter to hand. The host's equivalent tests role membership: ``post`` is
    PMGEditor's alone and ISGAdmin does not hold it, so no single
    resource:action isolates an administrator."""
    if isinstance(user, str):
        return isAllowed(user) and user in getAdminAllowlist()
    return hasRole(user, ROLE_ADMIN)


def requireAdmin(request: Request) -> UserData:
    """FastAPI dependency for the repository console: an authenticated,
    allowlisted caller who is also an admin. 403 otherwise, in the same body
    shape as the access-list refusal."""
    userData = requireAuth(request)
    if not isAdmin(userData):
        raise HTTPException(
            status_code=403,
            detail={'error': 'User {} is not a sleeve repository admin.'.format(
                userData.kerberos)},
        )
    return userData
