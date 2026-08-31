"""Access control - STAND-IN for the host's ``cyrus_pmg.pmgService.core.accessControl``.

The host authenticates with GSSSO/Kerberos and keeps an allowlist. Neither
exists in epsilon-phi, so this module stubs the *implementation* while keeping
every *seam* exactly where the host has it:

    getKerberosFromFlaskRequest(request)   identity from a Flask request
    getKerberosFromFastApiRequest(request) identity from a FastAPI request
    isAllowed(kerberos)                    the allowlist check
    getAllowlist()                         the allowlist source
    buildLoginUrl(nextUrl)                 where an unauthenticated browser goes
    requireAuth / requireEditor            FastAPI dependencies

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


def requireAuth(request: Request) -> str:
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
    return kerberos


def requireEditor(request: Request) -> str:
    """FastAPI dependency for writes. One role today, so this is requireAuth;
    it exists so every write endpoint already carries the host's seam."""
    return requireAuth(request)
