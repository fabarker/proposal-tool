"""FastAPI service - STAND-IN for the host's ``pmgService/isgPMGService.py``.

Reproduces the parts of the host service the Proposal Tool depends on:

* the app, with ``dashboardRouter`` mounted under ``/api/v1``
  (so nothing needs to change in this file when endpoints are added),
* ``/health`` for the launcher's readiness poll,
* ``/api/v1/whoami`` for ``accessGate.js`` (public at the Flask gate,
  answered here),
* the host error contract: a non-2xx JSON body carries top-level ``error``
  (401 carries ``loginUrl``), never FastAPI's default ``{"detail": ...}``.

This file is NOT transplanted - the host already has its own service, which
already mounts its dashboardRouter. At port time, verify the host emits the
same top-level error shape for auth failures; its pages already read
``body.error``/``body.loginUrl``, so it should.

Run:  python -m uvicorn cyrus_pmg.pmgService.isgPMGService:app --port 8002
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from cyrus_pmg.pmgService.core.accessControl import (
    buildLoginUrl,
    getKerberosFromFastApiRequest,
    isAllowed,
)
from cyrus_pmg.pmgService.dashboardRouter import router as dashboardRouter

app = FastAPI(title='isgPMGService (epsilon-phi mirror)', docs_url=None, redoc_url=None)
app.include_router(dashboardRouter, prefix='/api/v1')


@app.exception_handler(HTTPException)
async def _httpErrorContract(request: Request, exc: HTTPException):
    """Map HTTPException onto the host contract: top-level error/loginUrl."""
    body = exc.detail if isinstance(exc.detail, dict) else {'error': str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def _requestShapeErrorContract(request: Request, exc: RequestValidationError):
    """A malformed request body or query still yields the contract shape."""
    return JSONResponse(status_code=422, content={'error': 'Malformed request.'})


@app.exception_handler(Exception)
async def _unhandledErrorContract(request: Request, exc: Exception):
    """Anything unhandled surfaces as a 500 with the contract shape."""
    return JSONResponse(
        status_code=500,
        content={'error': 'Internal error: {}'.format(type(exc).__name__)},
    )


@app.get('/health')
async def health():
    """Readiness probe for the launcher."""
    return {'status': 'ok'}


@app.get('/api/v1/whoami')
async def whoami(request: Request):
    """Identity probe for accessGate.js. Public at the Flask gate."""
    kerberos = getKerberosFromFastApiRequest(request)
    if not kerberos:
        return JSONResponse(status_code=401, content={'loginUrl': buildLoginUrl()})
    if not isAllowed(kerberos):
        return JSONResponse(
            status_code=403,
            content={'error': 'User {} is not on the access list.'.format(kerberos)},
        )
    return {'kerberos': kerberos, 'allowed': True}
