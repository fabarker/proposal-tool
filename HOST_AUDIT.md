## 1. What this dashboard is

A web UI for Goldman Sachs PWM/ISG's PMG (Portfolio Management Group) portfolio-optimization workflow. Users pick a PMG account, view its holdings, edit optimization configuration, run an ISG model portfolio, run product-level optimization, manage constraints/preferred products/asset-class mappings, and compare the optimized result to current holdings. Purpose and features are enumerated in the package README:

- `dashboard/README.md` lines 1–17 — “A web-based dashboard for PMG portfolio optimization. It allows users to view current holdings, configure optimization parameters, run ISG model portfolios, execute product-level optimization, and compare optimization results against current holdings.”

It sits inside the `cyrus_pmg` package as its user-facing surface. All business logic (DB access, optimizer calls, ISG model runs) has been moved out of the dashboard package into the FastAPI services in `cyrus_pmg.pmgService` and `cyrus_pmg.optimizationService`. The dashboard is now essentially:

1. A pile of static HTML/CSS/JS files (one folder per page), plus
2. A thin Flask process that serves those files and reverse-proxies `/api/*` to the FastAPI backend.

Confirmed by `dashboard/__init__.py` lines 1–15:

```python
"""
Dashboard module - Frontend only.

The backend service (DashboardHandler) lives in:
    cyrus_pmg.pmgService.handler.dashboardHandler

This package now only contains:
    - dashboardFrontend.py  - Flask server for HTML/JS/CSS + API proxy
    - dashboardConfig.py    - Port configuration
    - static/               - Frontend assets
"""
```

...and by `dashboardConfig.py` lines 1–13 which flag the old `FLASK_API_PORT` as deprecated in favour of the FastAPI backend port from `optimizationService/config.py` / `pmgService/config.py`.

---

## 2. The frontend stack

Vanilla static HTML + hand-written ES5/ES6 JavaScript, served by Flask. No SPA framework, no template engine, no build step.

Evidence:

- `dashboard/index.html` is a plain `<!DOCTYPE html>` document with inline markup, `<script src="/static/js/..."></script>` tags loading raw JS, and CSS via `<link rel="stylesheet">`. There is no Jinja `{{ }}` syntax anywhere; Flask serves it via `send_from_directory` (see §4), not `render_template`.
- `requirements.in` (isg-cyrus-pmg) lists `Flask==2.2.5`, `Flask-RESTful==0.3.6`, `Jinja2==3.1.2` (Flask dep, not used for templating), `requests` (for the proxy). No Streamlit / Dash / Panel / NiceGUI / Reflex / Gradio / React / Vue anywhere.
- The only external JS dependency is Chart.js pulled from a CDN by pages that need charts, e.g. `modelComparison/modelComparison.html` line 7: `<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>`.
- Backend framework for the API the frontend talks to: FastAPI (`pmgService/isgPMGService.py` line 20 `from fastapi import FastAPI`, uvicorn entry).

So the “framework” for the UI is: browser + Flask static file server + per-page JS files that call `fetch('/api/...')`. Concepts I'll use below (that are unusual if you've only done Python):

- **Route** — in Flask, a URL pattern bound to a Python function with an `@app.route(...)` decorator. In this codebase every route just serves a file or proxies a request.
- **Static file serving** — Flask function `send_from_directory(dir, name)` returns the file `name` from directory `dir` as an HTTP response. No templating.
- **Reverse proxy** — a route that receives an HTTP request and re-issues it against another server (the FastAPI backend), then returns that server's response to the browser. Used here so the browser only ever talks to one origin.
- **Page** — one HTML file plus its sibling JS/CSS files. There is no component / callback model; each page is a self-contained document.

---

## 3. Directory and module map

Top level of `isg-cyrus-pmg/src/cyrus_pmg/dashboard/`:

| Path | Kind | Responsibility |
|---|---|---|
| `__init__.py` | Python | Marker + docstring only; no exports. |
| `dashboardConfig.py` | Python | Reads `FRONTEND_PORT` env var (default 8001) and `DASHBOARD_HOST` (`0.0.0.0`). Contains a deprecation note about the old Flask backend port. |
| `dashboardFrontend.py` | Python | The only running Python code in this package. Flask app: access gate, static routes per page, `/api/*` reverse proxy to FastAPI. |
| `dashboard.env.defaults` | shell | Sourced by `start_dashboard.sh`; sets ports, log dirs, auth vars (`PMG_ALLOWED_KERBEROS`), OAuth, SAA toggle, Axioma paths. |
| `start_dashboard.sh` | shell | Launches (1) optimizationService, (2) isgPMGService, (3) Flask frontend; waits for `/health`; rotates logs. |
| `stop_dashboard.sh` | shell | Kills them all. |
| `index.html` | HTML | Main dashboard page (account selector + optimization config + holdings + ISG model + product-level optimization). |
| `README.md` | doc | User-facing dashboard doc (partially stale — describes the pre-migration Flask backend architecture). |
| `PMG_MODEL_CONSTRUCTION_PLAN.md` | doc | Design notes for the pmgModelConstruction page. |
| `static/css/` | CSS | `dashboard.css`, `onegsTheme.css` — the shared GS theme + main-page rules. |
| `static/js/` | JS | Shared JS modules loaded by many pages. See table below. |
| `<pageName>/` | folder | One folder per page: contains `<pageName>.html` + `static/js/<pageName>.js` + `static/css/<pageName>.css` (typical layout). |

Per-page folders present today (each is a “page” in the routing sense of §6):

```text
assetClassMappingBuilder/   batchOptimization/   constraintSetup/
factorAnalytics/            isgModelPlayground/  modelComparison/
modelPlayground/            modelPortfolios/     pmgModelConstruction/
preferredProducts/          productSetup/
```

Each contains `<pageName>.html` and `static/{css,js}/` with same-named CSS/JS. Example — `preferredProducts/`:

```text
preferredProducts/
  preferredProducts.html
  static/
    css/preferredProducts.css
    js/preferredProducts.js
```

`modelPlayground/` is the odd one out — it has only `static/` (its HTML is generated on the fly by rewriting `index.html`; see §7 gotchas).

Shared JS modules in `static/js/` (each is loaded by an explicit `<script src="/static/js/…">` tag in whichever pages need it):

| File | Role |
|---|---|
| `accessGate.js` | Front-end auth badge/overlay; calls `/api/whoami`. Included on every page. |
| `globals.js` | Sets `window.API_BASE` and page-wide vars. |
| `isgCodeSelector.js` | Floating “ISG1 / ISG2” schema switcher injected into any page that includes it. |
| `accountManager.js` | Main-page account combo box + `onAccountChange`. |
| `configManager.js`, `configAndTargetVol.js` | Main-page optimization-config form logic. |
| `assetClassMappings.js` | AC mapping editor. |
| `constraintAndAssetListEditors.js` | Shared structured constraint editor. |
| `copyConfig.js` | “Copy config from another account” panel. |
| `factorAnalyticsLauncher.js` | Opens factor-analytics popup. |
| `hierarchyGrouping.js`, `uiUtils.js` | Table grouping / small UI helpers. |
| `isgCodeSelector.js`, `modelPortfolio.js`, `modelRunner.js`, `productOptimization.js` | Main-page domain logic. |

There is no JS module system (no bundler, no `import`); files rely on global functions defined in each `<script>` tag being available to the next. Load order in HTML matters.

### Module dependency graph (Python side)

```text
start_dashboard.sh
  ├─ python -m uvicorn cyrus_pmg.optimizationService.optimizationService:app
  ├─ python -m uvicorn cyrus_pmg.pmgService.isgPMGService:app
  └─ python dashboardFrontend.py
      ├─ cyrus_pmg.dashboard.dashboardConfig       (ports)
      ├─ cyrus_pmg.pmgService.config.settings      (backend port for proxy)
      └─ cyrus_pmg.pmgService.core.accessControl   (kerberos allowlist)
```

`dashboardFrontend.py` imports only from the pmgService (for auth helpers and the backend port). No import of any handler / business logic.

---

## 4. How the app boots and serves

Entry point: `dashboardFrontend.py` at module level creates one Flask app, then `main()` calls `app.run()`.

`dashboardFrontend.py` lines 24, 314–329:

```python
app = Flask(__name__)

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
...
def main():
    from cyrus_pmg.dashboard.dashboardConfig import FLASK_FRONTEND_PORT, DASHBOARD_HOST
    from cyrus_pmg.pmgService.config import settings as pmgSettings

    host = DASHBOARD_HOST
    port = FLASK_FRONTEND_PORT
    ...
    app.run(host=host, port=port, debug=False)

if __name__ == '__main__':
    main()
```

Ports (`dashboardConfig.py` lines 17–18):

```python
FLASK_FRONTEND_PORT = os.getenv("FRONTEND_PORT", 8001)
DASHBOARD_HOST = '0.0.0.0'
```

FastAPI backend port comes from `pmgService/config.py` line 14: `port: int = os.getenv("PMG_SVC_PORT", 8002)`.

### Layout of a request

```text
Browser  ➞  Flask :8001  ──(if /api/*)──➞  FastAPI pmgService :8002
            │                                  │
            ├─ /            ➞ index.html       └─ /api/v1/<path>
            ├─ /static/...  ➞ static/...
            ├─ /<page>/...  ➞ <page>/...
            └─ /api/...     ➞ proxy
```

The proxy is one wildcard route, `dashboardFrontend.py` lines 274–319:

```python
@app.route('/api/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
def proxy_api(path):
    backend_url = f'{_get_backend_url()}/api/v1/{path}'
    ...
    resp = requests.request(method=request.method, url=backend_url, ...,
                            timeout=300, allow_redirects=False)
```

Note the URL rewrite: browser `/api/foo` → backend `/api/v1/foo`. The FastAPI service mounts the dashboard endpoints there — see `pmgService/dashboardRouter.py` which is `include_router`-ed by `isgPMGService.py` (lines 30–34).

---

## 5. Running the dashboard locally

The canonical launcher is `start_dashboard.sh` (bash — designed for Linux/WSL/mac; on native Windows PowerShell you must run it under WSL or replicate the three `python -m …` commands manually). Quoting the parts you need:

### 5.1 Prereqs the code assumes

- Linux/mac shell (bash). If you're on Windows, use WSL.
- A Python virtualenv with the packages in `isg-cyrus-pmg/requirements.txt` installed (Flask 2.2.5, requests, FastAPI, uvicorn, pydantic-settings, sqlalchemy, sybase driver, etc.). The script auto-detects `$VIRTUAL_ENV` — activate it first.
- `PYTHONPATH` includes all three sibling projects (script sets it): `start_dashboard.sh` line 43:

```bash
export PYTHONPATH="${BASE_DIR}/isg-cyrus-pmg/src:${BASE_DIR}/isg-cyrus-core/src..."
```

- Reachable SQL Anywhere / Sybase IQ database (the backend uses `sqlanydb`; see the `dbcapi.dll` error message you hit earlier). Client libs must be discoverable (Windows: `C:\SYBASE_IQ_16_0\IQ-16_0\Bin64\dbcapi.dll`).
- Optional but expected in prod: Axioma files under `AXIOMA_FILE_DIR`, GSSSO/kerberos identity in the browser.

### 5.2 The one-line start

```bash
cd isg-cyrus-pmg/src/cyrus_pmg/dashboard
source /path/to/venv/bin/activate   # start_dashboard.sh reads $VIRTUAL_ENV
./start_dashboard.sh
```

That script does, from lines 155–204:

```text
[1/4] Starting optimizationService backend...
    uvicorn cyrus_pmg.optimizationService.optimizationService:app
        --host 0.0.0.0 --port ${OPT_SVC_PORT:-8003} --workers 4

[2/4] Starting isgPMGService backend...
    uvicorn cyrus_pmg.pmgService.isgPMGService:app
        --host 0.0.0.0 --port ${PMG_SVC_PORT:-8002} --workers 2

[3/4] Starting Flask frontend (port $FRONTEND_PORT)...
    python dashboardFrontend.py
```

Each backend is polled at `http://127.0.0.1:${port}/health` for up to 60s before …

### 5.3 Manual start (matches the script; works cross-platform)

```bash
export PYTHONPATH="$PWD/isg-cyrus-pmg/src:$PWD/isg-cyrus-core/src/main/python:$PWD/..."
# terminal 1
python -m uvicorn cyrus_pmg.optimizationService.optimizationService:app --host 0.0.0.0 ...
# terminal 2
python -m uvicorn cyrus_pmg.pmgService.isgPMGService:app --host 0.0.0.0 ...
# terminal 3
python isg-cyrus-pmg/src/cyrus_pmg/dashboard/dashboardFrontend.py
```

The README's “Manual Start” (lines 92–100) still references the deprecated `dashboardApi.py`; ignore that — use the two `uvicorn` commands above.

### 5.4 URL to open

```text
http://localhost:8001/                                      <-- main dashboard
http://localhost:8001/preferredProducts/preferredProducts.html
```

(all page URLs listed in §6).

### 5.5 Environment variables

Defaults live in `dashboard/dashboard.env.defaults` (auto-loaded by the launcher). The ones that actually matter to boot:

| Var | Default | Purpose |
|---|---:|---|
| `FRONTEND_PORT` | `8001` | Flask frontend port. |
| `OPT_SVC_PORT` | `8003` | FastAPI optimization service port. |
| `PMG_SVC_PORT` | `8002` | FastAPI pmgService port (the proxy target). |
| `PMG_ALLOWED_KERBEROS` | *(empty)* | Comma-separated Kerberos allowlist used by access control. |

---

## 6. How it's deployed beyond localhost

Evidence in the repo (search: `.gitlab-ci.yml`, `.gs-project.yml`, scripts, Dockerfiles):

- No Dockerfile, no Kubernetes manifests, no cloud-provider IaC anywhere in `isg-cyrus-pmg/`.
- GitLab CI (`.gitlab-ci.yml` lines 10–28) — the `package` job just zips `src/cyrus_pmg/**`, `scripts/**` and `resources/**` into `cyruspmg.zip` and publishes it as an artifact. There is no `docker build` / `docker push`.
- GS “area/gns” deployment (`.gs-project.yml` lines 15–27) — the zip is handed to Goldman's internal `gns` / `area` deployment system with `GNSParent=/gns/software/pwmtech/isg/cyruspmg` and aliases `snapshot`, [obscured], `prod`. This is an internal GS package-management/deploy pipeline (not open-source infra). What actually runs the process on the target host isn't in this repo — presumably a wrapper script on the `gns`-deployed servers invokes `start_dashboard.sh`.
- Config for staging vs prod — no separate config files. Environment differences are all env-vars: e.g. `EXECUTION_USER_NAME` and comment about the “ayco deployment” (`dashboard.env.defaults` lines 44–48), the `PMG_ALLOWED_KERBEROS` allowlist, and `USE_SAA_SERVICE`.
- Secrets management — nothing in the repo. `OAUTH_CLIENT_SECRET` is expected to be exported into the environment by the runner (`dashboard.env.defaults` line 52 is just an empty default).
- CI test job runs pytest under a venv it builds itself, see §10.

**Bottom line:** the codebase itself contains no container/K8s/cloud deployment. Beyond localhost, deployment is via GitLab CI producing a zip that GS's internal `gns` system distributes to servers; how those servers actually start the process is out-of-band from this repo.

---

## 7. The page/routing model

There are two orthogonal registries you have to touch when adding a page:

### 7.1 Backend routing — one Flask `@app.route` per page folder

Every page folder is served by a hand-written route in `dashboardFrontend.py`. There is no auto-discovery. Example lines 174–200:

```python
@app.route('/constraintSetup/<path:filename>')
def serve_constraint_setup(filename):
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'constraintSetup'), filename)

@app.route('/preferredProducts/<path:filename>')
def serve_preferred_products(filename):
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'preferredProducts'), filename)

@app.route('/pmgModelConstruction/<path:filename>')
def serve_pmg_model_construction(filename):
    ...
```

Pattern: `<path:filename>` is a Flask URL-converter that captures a multi-segment path. So one route per folder serves both the page's HTML (`/preferredProducts/preferredProducts.html`) and its nested static assets (`/preferredProducts/static/css/preferredProducts.css`, etc.).

Two folders use a slightly different pattern:

- `isgModelPlayground` (lines 253–265) also has a `/isgModelPlayground/` root that returns `isgModelPlayground.html` directly, so bare `/isgModelPlayground/` works.
- `accountPlayground` (lines 210–248) is a virtual page: no folder of that name on disk. The route re-reads `index.html`, regex-injects a `<script>` tag for `playgroundGuard.js`, and serves it. Assets are served from the `modelPlayground/` folder.

So the routing model is: decorator-based, one route per page folder, manually registered, no dynamic discovery. All URL prefixes are string-literals in `dashboardFrontend.py`.

### 7.2 Navigation — hard-coded `<a>` tags in `index.html`

The “nav bar” is the header block in `index.html` lines 21–39. Every nav entry is a literal `<a href="/<pageFolder>/<pageFolder>.html">…</a>`. No central registry, no dict/list, no plugin system. To add a link to a new page, you edit that block.

### 7.3 Auth — a `before_request` gate

Every request goes through `_enforceAllowlist()` (`dashboardFrontend.py` lines 68–121):

```python
@app.before_request
def _enforceAllowlist():
    if _isPublicPath(request.path):
        return None
    kerberos = getKerberosFromFlaskRequest(request)
    if not kerberos:
        loginUrl = buildLoginUrl(request.url)
        ...  # 401 JSON for /api/*, 302 to GSSSO for browser
    if isAllowed(kerberos):
        return None
    ...  # 403
```

`_PUBLIC_PATHS` (lines 27–33) lists paths that bypass the gate: `/health`, `/favicon.ico`, `/_access_denied`, `/api/whoami`, plus anything under `/static/css/` or `/static/js/accessGate.js`. New pages don't need to touch this — the gate lets any authenticated allowlisted user hit any route.

---

## 8. Dissection of one existing page — `preferredProducts`

Chosen because it's a typical “list + modal” page that follows the standard convention exactly. Follow this end-to-end and you have the template.

### 8.1 Route registration

`dashboardFrontend.py` lines 195–197:

```python
@app.route('/preferredProducts/<path:filename>')
def serve_preferred_products(filename):
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'preferredProducts'), filename)
```

### 8.2 Nav entry

`index.html` line 26:

```html
<a href="/preferredProducts/preferredProducts.html"
   style="color:white; text-decoration:none; font-size:14px; padding:8px 16px;
          border:1px solid rgba(255,255,255,0.4); border-radius:8px;
          transition:background 0.2s;"
   onmouseover="this.style.background='rgba(255, 255, 255, 0.15)'"
   onmouseout="this.style.background='transparent'">⭐ Preferred Products</a>
```

### 8.3 Files on disk

```text
preferredProducts/
  preferredProducts.html          # markup + script tags
  static/
    css/preferredProducts.css     # page-specific styles
    js/preferredProducts.js       # all page logic
```

### 8.4 HTML shell (page skeleton)

`preferredProducts/preferredProducts.html` lines 1–25:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Preferred Products</title>
    <link rel="stylesheet" href="/static/css/onegsTheme.css">
    <link rel="stylesheet" href="/preferredProducts/static/css/preferredProducts.css">
    <link rel="stylesheet" href="/static/css/dashboard.css">
</head>
<body>
<script src="/static/js/accessGate.js"></script>
<script src="/static/js/globals.js"></script>
<script src="/static/js/isgCodeSelector.js"></script>
    <div class="header">
        <a href="/" class="back-btn">← Back to Dashboard</a>
        <h1>⭐ Preferred Products</h1>
        <span></span>
    </div>

    <div class="container">
        <div id="alertArea"></div>
        ...
```

Note the conventions embedded here:

- Always load `onegsTheme.css` first, then the page-specific CSS, then reuse `dashboard.css` if you need its shared widget rules.
- `accessGate.js` is loaded first so it can render the sign-in overlay if the `/api/whoami` probe fails.
- Load `globals.js` before `isgCodeSelector.js` when the page uses the ISG schema switcher. Page's own JS is loaded at the bottom of `<body>`.
- The header has a `back-btn` linking to `/` and an `<h1>` with an emoji.

### 8.5 Page JS entry point

`preferredProducts/static/js/preferredProducts.js` lines 1–30:

```javascript
// API_BASE may already be declared by globals.js. Runtime guard so the
// page still works if loaded standalone.
if (typeof API_BASE === 'undefined') {
    window.API_BASE = window.location.origin + '/api';
}

function _currentSchemaCode() {
    return (typeof getCurrentISGCode === 'function') ? getCurrentISGCode() : 'ISG2';
}

let allLists = [];
let filteredLists = [];
let listDetailsCache = {};

document.addEventListener('DOMContentLoaded', async () => {
    ...
    await loadAllLists();
});
```

Pattern:

1. Guard `API_BASE` in case `globals.js` wasn't loaded.
2. Define a small helper to read the current ISG Code from the shared selector, with a hardcoded fallback (`'ISG2'`).
3. Module-level `let` variables hold page state.
4. Bootstrap on `DOMContentLoaded`, which reads any query-string deep-link params (this page supports `?editList=<name>&schemaCode=…`) and then fetches from the API.

### 8.6 Data flow

Frontend fetch → Flask proxy → FastAPI router → handler → DB:

```text
preferredProducts.js
  fetch(`${API_BASE}/preferred-product-lists?schemaCode=${_currentSchemaCode()}`)
    │
    ▼
Flask  /api/preferred-product-lists                (proxy_api, line 274)
    │  rewrites to
    ▼
FastAPI /api/v1/preferred-product-lists            (dashboardRouter.py)
    │
    ▼
DashboardHandler in cyrus_pmg.pmgService.handler.dashboardHandler
    │
    ▼
Sybase / SQL Anywhere via SQLAlchemy
```

### 8.7 Rendering & state

There is no framework re-render. The JS holds the fetched list in `allLists`, filters it into `filteredLists`, and rewrites the DOM by setting `document.getElementById('listsContainer').innerHTML = htmlString`. State is in-module `let` variables plus `localStorage` for the persistent `pmg.globalIsgCode` key set by `isgCodeSelector.js`. There is no shared JS store.

### 8.8 CSS

`preferredProducts/static/css/preferredProducts.css` lines 1–20 show the convention: it starts by resetting margins, uses the OneGS theme CSS variables (`var(--gs-text-primary)`, `var(--gs-border)`, `var(--gs-shadow-sm)`), and re-defines `.header` / `.back-btn` / `.container` per page. The gradient header colour is chosen per page (`#2c5282` → `#4a7bb7` here); different pages use different gradients — there is no shared header component.

---

## 9. Shared infrastructure

- **Layout / shell.** Each page owns its own `<header>` and `<div class="container">`. The only truly shared UI element is the access-gate badge/overlay injected by `accessGate.js`. Nav is duplicated: the main `index.html` header lists everything; sub-pages just have a `← Back to Dashboard` (or `← Back to Model Portfolios`) link.
- **Navigation registry.** Hard-coded `<a>` tags in `index.html` lines 21–39. No dictionary, no discovery.
- **Theming.** Two shared CSS files:
  - `static/css/onegsTheme.css` — GS OneGS theme (CSS variables like `--gs-text-primary`, `--gs-border`, `--gs-danger`, `--gs-surface-secondary`).
  - `static/css/dashboard.css` — shared widget rules (`.constraint-list`, `.cstr-*`, `.ac-tag-container`, buttons, forms). Page-specific CSS lives under `<page>/static/css/<page>.css`.
- **Reusable JS.** The shared modules in `static/js/` (see §3). Include them by `<script src>`; they attach globals (`window.API_BASE`, `getCurrentISGCode`, `getCurrentAccount`, ...). There is no `import` /module system; order of `<script>` tags matters.
- **Auth.** Two layers.
  - **Server.** `_enforceAllowlist()` before request handler (`dashboardFrontend.py` lines 68–121). Reads kerberos via `pmgService.core.accessControl.getKerberosFromFlaskRequest`; either 302s the browser to `buildLoginUrl(request.url)` (GSSSO), returns 401 JSON for XHR, renders `_renderAccessDenied` HTML for a non-allowlisted user, or 403 JSON for `/api/*`. FastAPI backend re-enforces via `Depends(requireAuth)` on router and `Depends(requireEditor)` on write endpoints (`pmgService/dashboardRouter.py` lines 75–76).
  - **Client.** `accessGate.js` calls `/api/whoami` and paints a badge or a blocking overlay.
- **Data access.** Not touched by the frontend. Everything goes through `/api/...` → FastAPI `DashboardHandler`.
- **Config / env vars.** `dashboardConfig.py` for ports (Flask side), `pmgService/config.py` for the FastAPI port. Runtime env from `dashboard.env.defaults`.
- **Logging.** Frontend uses Flask's default (stdout); `start_dashboard.sh` redirects each service to `${LOG_DIR}/dashboard_frontend.<pid>.log`, `pmg_service.<pid>.log`, `optimization_service.<pid>.log`, plus a separate `pmg_audit.<pid>.log` for the DB-change audit trail (`isgPMGService.py` lines 56–82).
- **Caching.** None in the Flask frontend. Some pages cache in memory …

---

## 10. Tests

- `isg-cyrus-pmg/test/unit/` contains pytest suites for handlers, data models, constraints, optimization service — none targeting the dashboard package (no `test/unit/dashboard/`). The Flask frontend has no dedicated tests.
- `.gitlab-ci.yml` `test:` job (lines 30–58) runs `pytest --cov=src` against `test/` under a fresh venv, using `test/resources/logging/log.conf.console.yaml`. It reports JUnit XML for Cloud Etch upload.
- `pytest.ini` and `conftest.py` live at package root but don't reference Flask/`dashboardFrontend`.

Effectively: the dashboard package is untested. The nearest thing to a smoke test is `curl http://127.0.0.1:${port}/health` in `start_dashboard.sh`.

---

## 11. Conventions to match

Discovered by cross-reading several pages:

- **Folder naming:** `lowerCamelCase` (`preferredProducts`, `assetClassMappingBuilder`). Same string is reused for URL prefix, HTML filename, JS filename, CSS filename.
- **File layout:** `pageName/pageName.html`, `pageName/static/js/pageName.js`, `pageName/static/css/pageName.css`.
- **HTML:** `<!DOCTYPE html>`, `lang="en"`, viewport meta, page title = human name. Always `onegsTheme.css` + page CSS + `dashboard.css` when reusing shared widgets.
- **Script order:** `accessGate.js` → `globals.js` (if needed) → `isgCodeSelector.js` (if the page is schema-scoped) → your page JS at end of `<body>`.
- **JS style:** raw browser JS, no bundler, no TypeScript. Guard globals with `if (typeof X === 'undefined')`. Bootstrap on `DOMContentLoaded`. Fetch via `fetch(${API_BASE}/...)`. Show errors in a `#alertArea` div. State in module-level `let` variables.
- **Header shape:** `<div class="header">` with `.back-btn` on the left, `<h1>` with a leading emoji in the middle, and either a spacer or a controls block on the right. Gradient background chosen per page.
- **Python style (frontend server only):** `snake_case` for Flask view functions, `_leadingUnderscore` for internal helpers, docstrings on every route explaining what it serves. See `dashboardFrontend.py` lines 168–207 — every route has a one-line docstring like `"""Serve X static files."""`.
- **Python (backend routers):** `mixedCase` names for endpoints/handlers (see `dashboardRouter.py`). Doesn't affect the frontend page, but if you're adding an endpoint too, match this style.
- **Docstring style:** triple-quoted, first-line summary, optional blank line, then reST/plain prose. See `dashboardFrontend.py` module docstring and `_enforceAllowlist` docstring lines 68–79 for the pattern.
- **Error handling in the proxy:** the `/api/*` route catches `requests.ConnectionError` → 502 and `requests.Timeout` → 504, both with a small JSON body (`dashboardFrontend.py` lines 320–330). Pages should render any non-2xx JSON's `error` field into `#alertArea`.
- **Ambiguities / inconsistencies I saw (call these out honestly):**
  - The README still describes the deprecated `dashboardApi.py` Flask backend. Reality is FastAPI in `pmgService`.
  - Auth constants: `dashboardFrontend.py` calls `getAllowlist()` but the imported name is fine — check that `PMG_ALLOWED_KERBEROS` is set on your box or you'll get the “access denied” card on every request.
  - `modelPlayground` on disk is served under the URL prefix `/accountPlayground/`. Don't rename folders casually.
  - `factorAnalytics` is not linked from the main nav; it's opened as a popup by `factorAnalyticsLauncher.js`. Not every folder needs a nav entry.

---

## 12. Recipe — add a new page

Let's add a page called `myNewPage`. Concrete, ordered steps.

### Step 1 — create the folder & files

```text
isg-cyrus-pmg/src/cyrus_pmg/dashboard/myNewPage/
  myNewPage.html
  static/
    css/myNewPage.css
    js/myNewPage.js
```

### Step 2 — write `myNewPage.html`

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>My New Page</title>
    <link rel="stylesheet" href="/static/css/onegsTheme.css">
    <link rel="stylesheet" href="/myNewPage/static/css/myNewPage.css">
    <link rel="stylesheet" href="/static/css/dashboard.css">
</head>
<body>
<!-- Access gate first so the sign-in overlay renders before anything else. -->
<script src="/static/js/accessGate.js"></script>
<!-- globals.js sets window.API_BASE; load it before any script that uses it. -->
<script src="/static/js/globals.js"></script>
<!-- Include only if this page should be scoped by ISG1/ISG2 schema. -->
<script src="/static/js/isgCodeSelector.js"></script>

    <div class="header">
        <a href="/" class="back-btn">← Back to Dashboard</a>
        <h1>✨ My New Page</h1>
        <span></span>
    </div>

    <div class="container">
        <div id="alertArea"></div>

        <div class="toolbar">
            <button class="btn-secondary" onclick="reloadItems()">🔄 Refresh</button>
        </div>

        <div id="itemsContainer">
            <div class="empty-state">
                <div class="spinner"></div>
                <div style="margin-top:12px;">Loading...</div>
            </div>
        </div>
    </div>

    <!-- Page JS at the bottom so all referenced DOM nodes exist. -->
    <script src="/myNewPage/static/js/myNewPage.js"></script>
</body>
</html>
```

### Step 3 — write `myNewPage/static/js/myNewPage.js`

Production-quality skeleton that follows every convention seen above:

```javascript
// =============================================================
// My New Page
// =============================================================
// Guards against being loaded without globals.js (matches the pattern in
// preferredProducts.js). Every fetch on this page goes through API_BASE so
// the Flask frontend's /api/* proxy forwards it to the FastAPI backend.
'use strict';

if (typeof API_BASE === 'undefined') {
    window.API_BASE = window.location.origin + '/api';
}

/** Read the current ISG schema code from the shared floating selector,
 *  falling back to ISG2 if isgCodeSelector.js isn't loaded on this page. */
function _currentSchemaCode() {
    return (typeof getCurrentISGCode === 'function') ? getCurrentISGCode() : 'ISG2';
}

// -------------------------------------------------------------
// Page state (module-level; there is no shared JS store).
// -------------------------------------------------------------
let items = [];

// -------------------------------------------------------------
// Bootstrap
// -------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
    // Re-fetch when the user flips the global ISG code selector.
    window.addEventListener('isgCodeChanged', reloadItems);
    reloadItems();
});

// -------------------------------------------------------------
// Data fetching
// -------------------------------------------------------------
async function reloadItems() {
    setLoading(true);
    try {
        const url = `${API_BASE}/my-new-items?schemaCode=${encodeURIComponent(_currentSchemaCode())}`;
        const resp = await fetch(url, { credentials: 'same-origin' });

        if (!resp.ok) {
            // Try to surface the JSON error the FastAPI/Flask layer returns.
            let msg = `Request failed (${resp.status})`;
            try {
                const body = await resp.json();
                if (body && body.error) msg = body.error;
                if (body && body.loginUrl) window.location = body.loginUrl;  // 401
            } catch (_) { /* non-JSON error body */ }
            throw new Error(msg);
        }

        const payload = await resp.json();
        items = Array.isArray(payload) ? payload : (payload.items || []);
        renderItems();
    } catch (err) {
        showAlert('error', err.message || String(err));
    } finally {
        setLoading(false);
    }
}

// -------------------------------------------------------------
// Rendering (direct DOM writes - this codebase has no framework)
// -------------------------------------------------------------
function renderItems() {
    const container = document.getElementById('itemsContainer');
    if (!items.length) {
        container.innerHTML = '<div class="empty-state">No items.</div>';
        return;
    }
    // Simple, safe render. Use textContent for anything user-supplied to
    // avoid injecting HTML from the backend into the page.
    const frag = document.createDocumentFragment();
    for (const it of items) {
        const row = document.createElement('div');
        row.className = 'item-row';
        row.textContent = `${it.name} - ${it.value}`;
        frag.appendChild(row);
    }
    container.replaceChildren(frag);
}

// -------------------------------------------------------------
// UI helpers
// -------------------------------------------------------------
function setLoading(loading) {
    const c = document.getElementById('itemsContainer');
    if (loading) {
        c.innerHTML = '<div class="empty-state"><div class="spinner"></div>' +
                      '<div style="margin-top:12px;">Loading...</div></div>';
    }
}

function showAlert(kind, message) {
    const area = document.getElementById('alertArea');
    const div = document.createElement('div');
    div.className = `alert alert-${kind}`;
    div.textContent = message;
    area.replaceChildren(div);
}
```

### Step 4 — write `myNewPage/static/css/myNewPage.css`

Follow the `preferredProducts.css` convention (pick your own gradient):

```css
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f4f7fa;
    color: var(--gs-text-primary);
    min-height: 100vh;
}
.header {
    background: linear-gradient(135deg, #3b3f7a 0%, #6c72c4 100%);
    color: white;
    padding: 18px 28px;
    display: flex; align-items: center; justify-content: space-between;
    box-shadow: 0 2px 8px rgba(0,0,0,0.12);
}
.header h1 { font-size: 22px; font-weight: 600; }
.back-btn {
    color: white; text-decoration: none; font-size: 14px;
    padding: 8px 16px; border: 1px solid rgba(255,255,255,0.4);
    border-radius: 8px; transition: background 0.2s;
}
.back-btn:hover { background: rgba(255, 255, 255, 0.15); }
.container { max-width: 1500px; margin: 24px auto; padding: 0 24px; }
.toolbar {
    display: flex; gap: 10px; align-items: center;
    background: white; padding: 14px 18px; border-radius: 10px;
    box-shadow: 0 2px 6px var(--gs-shadow-sm); margin-bottom: 16px;
}
.item-row {
    background: white; padding: 12px 16px; border-radius: 8px;
    box-shadow: 0 1px 3px var(--gs-shadow-sm); margin-bottom: 8px;
}
```

### Step 5 — register the route in `dashboardFrontend.py`

Add one block alongside the existing per-page routes (e.g. after the `preferred_products` block near line 197):

```python
@app.route('/myNewPage/<path:filename>')
def serve_my_new_page(filename):
    """Serve My New Page static files."""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'myNewPage'), filename)
```

That's it — no `__init__.py` edits, no registry, no config. Restart the Flask process (`stop_dashboard.sh && start_dashboard.sh`, or Ctrl-C the manual `python dashboardFrontend.py` and restart).

### Step 6 — add the nav link

In `index.html`, inside the header `<div style="display:flex; gap:12px; …">` around lines 21–39, add:

```html
<a href="/myNewPage/myNewPage.html"
   style="color:white; text-decoration:none; font-size:14px; padding:8px 16px;
          border:1px solid rgba(255,255,255,0.4); border-radius:8px;
          transition:background 0.2s;"
   onmouseover="this.style.background='rgba(255, 255, 255, 0.15)'"
   onmouseout="this.style.background='transparent'">✨ My New Page</a>
```

Copy the surrounding inline styles verbatim so the button matches.

### Step 7 — (optional) add the backend endpoint

If your page needs data, add the endpoint on the FastAPI router: `isg-cyrus-pmg/src/cyrus_pmg/pmgService/dashboardRouter.py`. Add a `@router.get("/my-new-items")` (auto-guarded by `requireAuth`; add `Depends(requireEditor)` for writes). No changes to `isgPMGService.py` needed — the router is already `include_router`-ed.

### Step 8 — verify

1. `curl -s http://localhost:8001/myNewPage/myNewPage.html | head` — HTML.
2. `curl -s http://localhost:8001/myNewPage/static/js/myNewPage.js | head` — JS.
3. Open `http://localhost:8001/` in a browser with a valid kerberos cookie; the nav button should appear. Click it.

---

## 13. Gotchas & constraints

- **Every page is manually registered.** If you skip step 5, you get 404 on the page URL. If you skip step 6, the page exists but isn't linked.
- **URL prefix must match folder name** for the standard route pattern to work. If you deviate (like `/accountPlayground/` → `modelPlayground/`), you have to know it and its assets will 404 unless the route knows.
- `accountPlayground` regex-injects into `index.html`. If you rewrite `index.html` heavily, verify the `re.sub(r'(<body[^>]*>)', …)` in `dashboardFrontend.py` lines 240–245 still matches — else the playground banner and guard silently break.
- **Script load order matters** (no module system). `accessGate.js` must be before your code; `globals.js` must be before anything using `API_BASE` or `getCurrentISGCode`.
- **Auth gate is process-wide.** With an empty `PMG_ALLOWED_KERBEROS` your requests get 403. Local dev usually needs `export PMG_ALLOWED_KERBEROS=<your kerberos>`.
- **The FastAPI backend must be up before the frontend is useful.** The Flask proxy returns 502 JSON for every `/api/*` call when the backend isn't listening on `PMG_SVC_PORT`.
- **Backend URL rewrite** — the proxy converts `/api/<x>` to `/api/v1/<x>`. Any endpoint you add on the backend must live under `/api/v1/...` (which is what `dashboardRouter` does).
- **Long-running endpoints** — the proxy uses `timeout=300`. Product-level optimization and full ISG runs approach that limit; if you add something slower, raise the timeout on the proxy.
- `allow_redirects=False` on the proxy is deliberate: it lets the browser see FastAPI's 302 to GSSSO. Do not change to `True`.
- `send_from_directory` blocks directory traversal, so `<path:filename>` is safe against `../` — but only files that exist on disk are served, meaning symlinks/aliases don't work.
- **README is partially stale.** `dashboardApi.py` no longer exists in this package; the port table in the README doesn't match `dashboardConfig.py`. Trust `dashboardFrontend.py` and `start_dashboard.sh`, not the README.
- **No hot reload.** `debug=False` in `main()`; you must restart the Flask process for Python changes. HTML/CSS/JS edits are picked up on the next browser refresh (they're read from disk on each request).
- **No tests for the dashboard** — you can add a page without breaking any CI job, but you also have no safety net.
- **Windows dev caveat:** `start_dashboard.sh` is bash-only and uses `nohup` / `kill -0` / `stat -c` — run under WSL or invoke the three Python commands manually (§5.3).
