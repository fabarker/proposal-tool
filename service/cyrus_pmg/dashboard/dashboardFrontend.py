"""Flask frontend - STAND-IN for the host's ``dashboardFrontend.py``.

Reproduces the host's serving stack so the Proposal Tool runs behind it in its
final shape:

* a ``before_request`` kerberos allowlist gate, in the same position, with the
  same outcomes (302 to login for an unauthenticated browser, 401 JSON with
  ``loginUrl`` for an unauthenticated /api call, a 403 card / 403 JSON for a
  caller not on the allowlist),
* one hand-written route per page folder served with ``send_from_directory``,
* the ``/api/<x>`` -> ``/api/v1/<x>`` reverse proxy with ``timeout=300`` and
  ``allow_redirects=False`` (deliberate - it lets the browser see the
  backend's 302), returning 502/504 JSON when the backend is down or slow.

This file is NOT transplanted. The host already has its own; the transplant
adds to it exactly one route (``serve_proposal_tool``) and one nav link -
see PORTING.md. ``/_dev_login`` is a dev-only stand-in for GSSSO.

Run:  python cyrus_pmg/dashboard/dashboardFrontend.py
"""

import os

import requests
from flask import Flask, Response, jsonify, redirect, request, send_from_directory

from cyrus_pmg.pmgService.core.accessControl import (
    buildLoginUrl,
    getKerberosFromFlaskRequest,
    isAllowed,
)

app = Flask(__name__)

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))

# Paths that bypass the gate, matching the host's list: health, favicon, the
# denied card, the whoami probe, stylesheets and accessGate.js itself (so the
# sign-in overlay can render), plus the dev login stand-in.
_PUBLIC_PATHS = ('/health', '/favicon.ico', '/_access_denied', '/_dev_login', '/api/whoami')


def _isPublicPath(path):
    if path in _PUBLIC_PATHS:
        return True
    if path.startswith('/static/css/'):
        return True
    if path == '/static/js/accessGate.js':
        return True
    return False


def _renderAccessDenied(kerberos):
    """The host's access-denied card: who you are, and who to ask."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<title>Access denied</title></head>'
        '<body style="font-family:system-ui,sans-serif;background:#f4f7fa;margin:0;'
        'display:flex;align-items:center;justify-content:center;min-height:100vh">'
        '<div style="background:#fff;border:1px solid #f1b8b2;border-radius:10px;'
        'padding:28px 34px;max-width:34rem;box-shadow:0 2px 8px rgba(0,0,0,.08)">'
        '<h1 style="font-size:20px;margin:0 0 10px;color:#9b1c1c">Access denied</h1>'
        '<p style="margin:0 0 8px;color:#1c2733">The user <b>{}</b> is not on the '
        'PMG dashboard access list.</p>'
        '<p style="margin:0;color:#5b6b7c">Ask the PMG team to add you to '
        '<code>PMG_ALLOWED_KERBEROS</code>.</p>'
        '</div></body></html>'.format(kerberos)
    )


@app.before_request
def _enforceAllowlist():
    """Kerberos allowlist gate, before every request, as the host does.

    No identity: browsers are redirected to the login URL, /api callers get a
    401 JSON carrying ``loginUrl``. An identity that is not allowlisted gets
    the denied card (403), or 403 JSON on /api paths.
    """
    if _isPublicPath(request.path):
        return None
    kerberos = getKerberosFromFlaskRequest(request)
    if not kerberos:
        loginUrl = buildLoginUrl(request.url)
        if request.path.startswith('/api/'):
            return jsonify({'loginUrl': loginUrl}), 401
        return redirect(loginUrl)
    if isAllowed(kerberos):
        return None
    if request.path.startswith('/api/'):
        return jsonify({'error': 'User {} is not on the access list.'.format(kerberos)}), 403
    return _renderAccessDenied(kerberos), 403


@app.route('/health')
def health():
    """Readiness probe for the launcher."""
    return jsonify({'status': 'ok'})


@app.route('/favicon.ico')
def favicon():
    """No favicon; keep the logs quiet."""
    return '', 204


@app.route('/_access_denied')
def access_denied():
    """Serve the denied card directly, mirroring the host's public path."""
    return _renderAccessDenied(getKerberosFromFlaskRequest(request) or 'unknown'), 403


@app.route('/_dev_login', methods=['GET', 'POST'])
def dev_login():
    """DEV-ONLY stand-in for GSSSO: set the kerberos cookie and bounce back.

    GET without a kerberos shows a one-field form; GET with ?kerberos= or a
    POST sets the cookie and redirects to ?next= (default /). The host replaces
    this whole flow with its real login redirect.
    """
    kerberos = request.values.get('kerberos', '').strip()
    next_url = request.values.get('next', '/') or '/'
    # The gate passes request.url, which is absolute; accept it when it points
    # back at this host, otherwise fall back to the index.
    if not next_url.startswith('/'):
        if not next_url.startswith(request.host_url):
            next_url = '/'
    if kerberos:
        resp = redirect(next_url)
        resp.set_cookie('kerberos', kerberos, httponly=False, samesite='Lax')
        return resp
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<title>Dev sign-in</title></head>'
        '<body style="font-family:system-ui,sans-serif;background:#f4f7fa;margin:0;'
        'display:flex;align-items:center;justify-content:center;min-height:100vh">'
        '<form method="post" style="background:#fff;border:1px solid #d4dae2;'
        'border-radius:10px;padding:28px 34px;max-width:30rem;'
        'box-shadow:0 2px 8px rgba(0,0,0,.08)">'
        '<h1 style="font-size:20px;margin:0 0 6px;color:#16243a">Development sign-in</h1>'
        '<p style="margin:0 0 14px;color:#5b6b7c">Stand-in for GSSSO. Enter a kerberos id; '
        'it must be listed in <code>PMG_ALLOWED_KERBEROS</code> to pass the gate.</p>'
        '<input name="kerberos" autofocus required '
        'style="font:inherit;padding:8px 10px;border:1px solid #d4dae2;border-radius:6px;'
        'width:100%;box-sizing:border-box" placeholder="kerberos id">'
        '<input type="hidden" name="next" value="{}">'
        '<button type="submit" style="font:inherit;margin-top:12px;padding:9px 22px;'
        'border:0;border-radius:6px;background:#1f5fbf;color:#fff;font-weight:600;'
        'cursor:pointer">Sign in</button>'
        '</form></body></html>'.format(next_url.replace('"', '&quot;'))
    )


@app.route('/')
def serve_index():
    """Serve the dashboard index page."""
    return send_from_directory(DASHBOARD_DIR, 'index.html')


@app.route('/static/<path:filename>')
def serve_static(filename):
    """Serve shared static assets (accessGate.js, globals.js)."""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'static'), filename)


@app.route('/proposalTool/<path:filename>')
def serve_proposal_tool(filename):
    """Serve Proposal Tool static files."""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'proposalTool'), filename)


def _get_backend_url():
    """The FastAPI backend base URL, from the pmgService config."""
    from cyrus_pmg.pmgService.config import settings
    return 'http://127.0.0.1:{}'.format(settings.port)


def _forward_headers(req):
    """Headers to pass through to the backend, minus hop-by-hop fields."""
    dropped = {'host', 'content-length', 'connection'}
    return {k: v for k, v in req.headers if k.lower() not in dropped}


@app.route('/api/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
def proxy_api(path):
    """Proxy /api/<x> to the FastAPI backend as /api/v1/<x>.

    ``allow_redirects=False`` is deliberate: it lets the browser see the
    backend's 302. ConnectionError becomes a 502 and Timeout a 504, both with
    a small JSON body carrying ``error``, per the host contract.
    """
    backend_url = '{}/api/v1/{}'.format(_get_backend_url(), path)
    try:
        resp = requests.request(
            method=request.method,
            url=backend_url,
            params=list(request.args.items(multi=True)),
            data=request.get_data(),
            headers=_forward_headers(request),
            cookies=request.cookies,
            timeout=300,
            allow_redirects=False,
        )
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'The scenario service is not reachable. '
                                 'Start it and try again.'}), 502
    except requests.exceptions.Timeout:
        return jsonify({'error': 'The scenario service timed out.'}), 504
    excluded = {'content-encoding', 'content-length', 'transfer-encoding', 'connection'}
    headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in excluded]
    return Response(resp.content, resp.status_code, headers)


def main():
    """Start the Flask frontend on the configured host and port."""
    from cyrus_pmg.dashboard.dashboardConfig import DASHBOARD_HOST, FLASK_FRONTEND_PORT
    app.run(host=DASHBOARD_HOST, port=FLASK_FRONTEND_PORT, debug=False)


if __name__ == '__main__':
    main()
