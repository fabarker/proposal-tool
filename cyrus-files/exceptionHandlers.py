"""
Exception handlers for the PMG service.
"""
from http import HTTPStatus
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response


class PmgAppException(Exception):
    """Application-level exception carrying an HTTP status code and a reason message.

    Extras
    ------
    ``extra`` : optional dict merged into the JSON body (e.g. ``{"loginUrl": "..."}``).
    ``redirectUrl`` : if set, the exception handler returns a 302 ``RedirectResponse``
        instead of a JSON body. Use this for browser navigations that must be
        bounced through the GSSSO login flow.
    ``headers`` : optional response headers (e.g. ``{"Location": "..."}``).
    """

    def __init__(
        self,
        statusCode: int = 500,
        reason: str = "Internal Server Error",
        extra: Optional[Dict[str, Any]] = None,
        redirectUrl: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        super().__init__(reason)
        self.statusCode = statusCode
        self.reason = reason
        self.extra = extra or {}
        self.redirectUrl = redirectUrl
        self.headers = headers or {}


async def pmgExceptionHandler(request: Request, exc: PmgAppException) -> Response:
    """FastAPI exception handler registered on the app for PmgAppException."""
    # Browser-navigation redirect (typically to the GSSSO login URL)
    if exc.redirectUrl:
        return RedirectResponse(
            url=exc.redirectUrl,
            status_code=exc.statusCode if 300 <= exc.statusCode < 400 else 302,
            headers=exc.headers,
        )
    body: Dict[str, Any] = {
        "error": HTTPStatus(exc.statusCode).phrase,
        "detail": exc.reason,
        "path": str(request.url),
    }
    if exc.extra:
        body.update(exc.extra)
    return JSONResponse(
        status_code=exc.statusCode,
        content=body,
        headers=exc.headers,
    )
