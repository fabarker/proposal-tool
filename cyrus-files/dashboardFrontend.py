"""
Frontend Server for Portfolio Optimization Dashboard
Serves static HTML/JS files and proxies /api/* requests to the backend API server.

Users should visit THIS server. It is the single entry point for the Flask-based UI.

Usage:
    python dashboardFrontend.py
"""

import json
import os

import requests
from flask import Flask, Response, redirect, request, send_from_directory

# MISSING project imports: getKerberosFromFlaskRequest, buildLoginUrl,
# isAllowed, getAllowlist.
app = Flask(__name__)

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))


# ==================== Access Control ====================
# Paths that bypass the allowlist check (so the 'access denied' page itself
# and the lightweight identity probe can still render / be called).
_PUBLIC_PATHS = {
    '/health',
    '/favicon.ico',
    '/_access_denied',
    '/api/whoami',          # forwarded to FastAPI public /whoami
}


def _isPublicPath(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    # allow CSS/JS used by the access-denied page itself
    if path.startswith('/static/css/') or path.startswith('/static/js/accessGate.js'):
        return True
    return False


def _wantsHtml() -> bool:
    """Heuristic: is this a top-level browser navigation (should 302) or an
    XHR/fetch call (should 401 with JSON)?"""
    accept = (request.headers.get('accept') or '').lower()
    if 'application/json' in accept:
        return False
    if request.headers.get('x-requested-with', '').lower() == 'xmlhttprequest':
        return False
    if 'text/html' in accept:
        return True
    return False


@app.before_request
def _enforceAllowlist():
    """Gate every non-public request.

    Two distinct failure modes:
      1. **No kerberos identity in the request** (no GSSSO / gsweb-kerberos
         cookie, no REMOTE_USER header) → *actively request a cookie* by
         redirecting browser navigations to the GSSSO sign-in URL (302), or
         returning a 401 JSON body containing ``loginUrl`` for API/XHR calls
         so the front-end can drive the redirect itself.
      2. **Kerberos present but not on allowlist** → render the static
         "Access denied" page (HTML) or return a 403 JSON body (API).
    """
    if _isPublicPath(request.path):
        return None

    kerberos = getKerberosFromFlaskRequest(request)

    # ---- Case 1: no identity at all → actively prompt for GSSSO cookie ----
    if not kerberos:
        # Build a sign-in URL that bounces the browser back to the originally
        # requested URL after the cookie is set.
        loginUrl = buildLoginUrl(request.url)

        if request.path.startswith('/api/'):
            # XHR / fetch - cannot transparently follow a 302 to an HTML
            # login page, so return JSON 401 with the loginUrl so the JS
            # layer can do ``window.location = loginUrl``.
            body = {
                'success': False,
                'error': (
                    'Unauthorized: no kerberos identity in the request '
                    '(GSSSO / gsweb-kerberos cookie missing).'
                ),
                'loginUrl': loginUrl,
            }
            return Response(
                json.dumps(body),
                status=401,
                content_type='application/json',
                headers={'WWW-Authenticate': 'Cookie realm="GSSSO"'},
            )

        # Browser navigation - redirect straight to GSSSO sign-in if we have
        # a usable URL; otherwise fall back to the access-denied page so the
        # user at least sees something.
        if loginUrl:
            return redirect(loginUrl, code=302)
        return _renderAccessDenied(kerberos), 403

    # ---- Case 2: identity present → enforce allowlist ----
    if isAllowed(kerberos):
        return None  # allowed → continue

    # API requests get a JSON 403; page requests get the HTML access-denied page.
    if request.path.startswith('/api/'):
        msg = (
            f"Forbidden: kerberos '{kerberos}' is not on the "
            f"PMG dashboard allowlist."
        )
        return Response(
            '{"success": false, "error": "' + msg.replace('"', '\\"') + '"}',
            status=403,
            content_type='application/json',
        )
    return _renderAccessDenied(kerberos), 403


def _renderAccessDenied(kerberos):
    who = kerberos or '— no kerberos identity detected —'
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Access denied</title>
<style>
  body {{ font-family:-apple-system,Segoe UI,Roboto,sans-serif;
         display:flex; align-items:center; justify-content:center;
         min-height:100vh; margin:0; background:#f5f7fa; color:#1c3144; }}
  .card {{ background:white; padding:36px 44px; border-radius:14px;
          box-shadow:0 14px 40px rgba(0,0,0,0.08); max-width:520px; }}
  h1 {{ margin:0 0 12px; font-size:22px; }}
  code {{ background:#f1f3f5; padding:2px 6px; border-radius:4px; }}
  .muted {{ color:#6c7a89; font-size:13px; margin-top:18px; }}
</style></head>
<body><div class="card">
  <h1>🚫 Access denied</h1>
  <p>You ({ '<code>' + who + '</code>' }) are not on the PMG dashboard
  allowlist.</p>
  <p>Please contact the PMG team and ask to be added.</p>
  <p class="muted">Allowlist currently has {len(getAllowlist())} user(s).</p>
</div></body></html>"""


@app.route('/_access_denied')
def _accessDeniedPage():
    return _renderAccessDenied(getKerberosFromFlaskRequest(request)), 403


def _get_backend_url():
    """Get the backend API base URL - now points to the FastAPI pmgService."""
    from cyrus_pmg.pmgService.config import settings
    return f'http://127.0.0.1:{settings.port}'


# -----------------------------------------------------------------------------
# Playground gating - the ISG Model Playground and Account Playground are
# non-prod tooling surfaces (edits are neutralised in memory, no writes to
# DB). Block direct URL hits on PROD *and UAT* as defense-in-depth; the nav
# dropdown entries are also hidden client-side via ``/api/whoami``'s
# ``isPlaygroundEnabled`` flag.
# -----------------------------------------------------------------------------
def _isPlaygroundBlockedEnv() -> bool:
    """True when the running env is PROD or UAT - the two envs where the
    playgrounds must never be reachable, regardless of user role.

    Fails closed: if we cannot determine the env we assume it *is*
    blocked so we never leak the playground into a sensitive env by
    accident.
    """
    try:
        from cyrus.core.lib.env.env import Env
        env = (Env.get_env() or "").upper()
        return "PROD" in env or "UAT" in env
    except Exception:
        return True


# Back-compat shim - legacy call sites (and any external importers) can
# keep using the old name; both point at the same broadened check.
_isProdEnv = _isPlaygroundBlockedEnv


def _playgroundBlockedResponse():
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<title>Playground disabled</title></head><body style="font-family:'
        '-apple-system,Segoe UI,Roboto,sans-serif;padding:60px;text-align:center;">'
        '<h1>🚫 Playground disabled in this environment</h1>'
        '<p>The ISG Model / Account Playground is a non-prod tooling '
        'surface and is disabled on <b>PROD</b> and <b>UAT</b>. '
        'Use a NON-PROD environment (DEV / QA / stress) to access it.</p>'
        '<p><a href="/">← Back to dashboard</a></p></body></html>'
    )


# ==================== Static File Serving ====================

@app.route('/')
def serve_index():
    """Serve the main dashboard HTML page"""
    return send_from_directory(DASHBOARD_DIR, 'index.html')


@app.route('/static/<path:filename>')
def serve_static(filename):
    """Serve static CSS and JS files"""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'static'), filename)


_GS_FAVICON_URL = 'https://cdn.gs.com/images/goldman-sachs/v3/gs-favicon.ico'


@app.route('/favicon.ico')
def serve_favicon():
    return redirect(_GS_FAVICON_URL, code=301)


@app.route('/constraintSetup/<path:filename>')
def serve_constraint_setup(filename):
    """Serve constraint setup static files"""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'constraintSetup'), filename)


@app.route('/productSetup/<path:filename>')
def serve_product_setup(filename):
    """Serve product setup static files"""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'productSetup'), filename)


@app.route('/modelPortfolios/<path:filename>')
def serve_model_portfolios(filename):
    """Serve model portfolios static files"""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'modelPortfolios'), filename)


@app.route('/modelComparison/<path:filename>')
def serve_model_comparison(filename):
    """Serve model comparison page static files"""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'modelComparison'), filename)


@app.route('/batchOptimization/<path:filename>')
def serve_batch_optimization(filename):
    """Serve batch optimization page static files"""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'batchOptimization'), filename)


@app.route('/approvals/<path:filename>')
def serve_approvals(filename):
    """Serve approvals page static files"""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'approvals'), filename)


@app.route('/progressTracking/<path:filename>')
def serve_progress_tracking(filename):
    """Serve progress tracking page static files"""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'progressTracking'), filename)


@app.route('/preferredProducts/<path:filename>')
def serve_preferred_products(filename):
    """Serve preferred products page static files"""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'preferredProducts'), filename)


@app.route('/pmgModelConstruction/<path:filename>')
def serve_pmg_model_construction(filename):
    """Serve PMG model portfolio construction page static files"""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'pmgModelConstruction'), filename)


@app.route('/assetClassMappingBuilder/<path:filename>')
def serve_asset_class_mapping_builder(filename):
    """Serve Asset Class Mapping Builder page static files"""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'assetClassMappingBuilder'), filename)


@app.route('/accountPlayground/')
@app.route('/accountPlayground/index.html')
def serve_model_playground_index():
    """Serve the Account Playground page (formerly /modelPlayground/).

    Rather than duplicate the main dashboard's UI (which drifts), this
    route serves the EXACT `index.html` of the main dashboard, but
    injects a small `playgroundGuard.js` right after `<body>`. The
    guard:
      • wraps `window.fetch` and short-circuits any DB-write endpoint
        (POST /config, POST/DELETE /asset-class-mappings, POST/DELETE
        /preferred-product-lists, POST/PUT/DELETE /tcost,
        POST/PUT/DELETE /noptimized, POST/PUT/PATCH/DELETE
        /product-parameters …) into a fake
        `{success:true, playground:true}` response so nothing persists;
      • renders a floating "Edit ISG Model" panel;
      • intercepts the product-optimization response and rescales it
        client-side by the user's edited AC weights.

    Result: identical UI/UX to the main page (holdings, ISG model,
    product-level config, AC mapping editor, preferred products,
    product-level result table - everything), with runtime-only edits.

    Blocked on PROD - playgrounds are a non-prod tooling surface only
    (see :func:`_isProdEnv`).
    """
    if _isProdEnv():
        return _playgroundBlockedResponse(), 403
    with open(os.path.join(DASHBOARD_DIR, 'index.html'), 'r') as f:
        html = f.read()
    inject = (
        '<div id="pgBanner" style="background:linear-gradient(90deg,#f59e0b,#f97316);'
        'color:#fff;padding:8px 16px;text-align:center;font-weight:600;'
        'font-size:13px;letter-spacing:0.3px;">'
        '🧪 ACCOUNT PLAYGROUND - every save is neutralised; edits live in memory only.'
        '</div>\n'
        '<script src="/accountPlayground/static/playgroundGuard.js"></script>\n'
    )
    # Inject right after <body ...>
    import re
    html = re.sub(r'(<body[^>]*>)', r'\1\n' + inject, html, count=1)
    return html


@app.route('/accountPlayground/<path:filename>')
def serve_model_playground(filename):
    """Serve Account Playground static assets (guard JS/CSS).

    Files now live under dashboard/isgModelPlayground/static/ on disk -
    the two playgrounds (account-level here and model-level fan-out
    under /isgModelPlayground/) share the same folder to keep related
    code co-located. The public URL is still /accountPlayground/ to
    reflect this variant's account-level scope. Blocked on PROD.
    """
    if _isProdEnv():
        return _playgroundBlockedResponse(), 403
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'isgModelPlayground'), filename)


# -----------------------------------------------------------------------------
# ISG Model Playground (model-first, fan-out)
# -----------------------------------------------------------------------------
# Sibling of /accountPlayground/ but flipped inside-out: instead of picking
# an account and editing its model, the user picks an ISG model from the
# DB (via GET /api/model-portfolios), edits its asset-class weights in
# memory, sees every account whose config matches that model on
# (portfolioType, currency, investorType) and fans a product-level
# optimization out across them - all with zero DB writes. The account-
# detail drawer is an iframe pointed at /accountPlayground/?account=… so
# it transparently inherits the existing write-guard. Both variants
# share the same dashboard/isgModelPlayground/ folder on disk and are
# blocked on PROD (non-prod tooling only).
@app.route('/isgModelPlayground/')
@app.route('/isgModelPlayground/index.html')
def serve_isg_model_playground_index():
    if _isProdEnv():
        return _playgroundBlockedResponse(), 403
    return send_from_directory(
        os.path.join(DASHBOARD_DIR, 'isgModelPlayground'),
        'isgModelPlayground.html',
    )


@app.route('/isgModelPlayground/<path:filename>')
def serve_isg_model_playground(filename):
    if _isProdEnv():
        return _playgroundBlockedResponse(), 403
    return send_from_directory(
        os.path.join(DASHBOARD_DIR, 'isgModelPlayground'), filename)


@app.route('/factorAnalytics/<path:filename>')
def serve_factor_analytics(filename):
    """Serve Factor Analytics popup page static files (HTML / CSS / JS)."""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'factorAnalytics'), filename)


# ==================== API Proxy ====================

@app.route('/api/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
def proxy_api(path):
    """Proxy all /api/* requests to the FastAPI pmgService backend."""
    backend_url = f'{_get_backend_url()}/api/v1/{path}'

    # Forward query string
    if request.query_string:
        backend_url += '?' + request.query_string.decode('utf-8')

    # Forward headers (skip hop-by-hop headers)
    # INFERRED ending of original line 395: screenshots cut off during
    # 'transfer-encoding'. Any additional excluded headers are not visible.
    headers = {k: v for k, v in request.headers if k.lower() not in ('host', 'connection', 'transfer-encoding')}

    try:
        resp = requests.request(
            method=request.method,
            url=backend_url,
            headers=headers,
            data=request.get_data(),
            timeout=300,  # 5 min timeout for long-running optimizations
            # Do NOT auto-follow 3xx responses - we want to pass redirects
            # (e.g. the GSSSO sign-in 302 from requireAllowlistedUser) straight
            # through to the browser so the user can authenticate.
            allow_redirects=False,
        )

        # Build response, forwarding status code and content
        response = Response(resp.content, status=resp.status_code)
        # Forward response headers (skip hop-by-hop)
        for k, v in resp.headers.items():
            # INFERRED ending of original line 414: final visible item begins
            # 'content-'; completed as 'content-length'. Further items unknown.
            if k.lower() not in ('connection', 'transfer-encoding', 'content-encoding', 'content-length'):
                response.headers[k] = v
        return response

    except requests.ConnectionError:
        return Response(
            # INFERRED end of original line 420, cropped after 'Is it run...'.
            '{"success": false, "error": "Backend API server is not reachable. Is it running?"}',
            status=502,
            content_type='application/json'
        )
    except requests.Timeout:
        return Response(
            '{"success": false, "error": "Backend API request timed out."}',
            status=504,
            content_type='application/json'
        )


def main():
    """Run the frontend Flask server"""
    from cyrus_pmg.dashboard.dashboardConfig import FLASK_FRONTEND_PORT, DASHBOARD_HOST
    from cyrus_pmg.pmgService.config import settings as pmgSettings

    host = DASHBOARD_HOST
    port = FLASK_FRONTEND_PORT

    print(f"Starting Portfolio Optimization Dashboard Frontend...")
    print(f"  Host          : {host}")
    print(f"  Frontend Port : {port}")
    print(f"  Backend API   : http://127.0.0.1:{pmgSettings.port}/api/v1/")
    print(f"  URL           : http://{host}:{port}")

    app.run(host=host, port=port, debug=False)


if __name__ == '__main__':
    main()
