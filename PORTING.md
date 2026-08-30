# Porting into `cyrus_pmg.dashboard`

`proposalTool/` is laid out to the host's page-folder convention. Copy it in, add one Flask
route, add one nav link. Nothing else changes.

## 1. Copy the folder

```bash
cp -r proposalTool \
   isg-cyrus-pmg/src/cyrus_pmg/dashboard/
```

```text
proposalTool/
  proposalTool.html
  static/
    css/proposalTool.css
    js/proposalTool.js
    fonts/*.woff2
```

Folder, HTML, CSS and JS all share the name, per the convention in `preferredProducts/`.

## 2. Register the route

In `dashboardFrontend.py`, beside the other per-page routes:

```python
@app.route('/proposalTool/<path:filename>')
def serve_scenario_analysis(filename):
    """Serve Proposal Tool static files."""
    return send_from_directory(os.path.join(DASHBOARD_DIR, 'proposalTool'), filename)
```

Restart the Flask process — there is no hot reload for Python changes.

## 3. Add the nav link

In `index.html`, inside the header flex block, copying the surrounding inline styles verbatim:

```html
<a href="/proposalTool/proposalTool.html"
   style="color:white; text-decoration:none; font-size:14px; padding:8px 16px;
          border:1px solid rgba(255,255,255,0.4); border-radius:8px;
          transition:background 0.2s;"
   onmouseover="this.style.background='rgba(255, 255, 255, 0.15)'"
   onmouseout="this.style.background='transparent'">📐 Proposal Tool</a>
```

## 4. Verify

```bash
curl -s http://localhost:8001/proposalTool/proposalTool.html | head
curl -s http://localhost:8001/proposalTool/static/js/proposalTool.js | head
curl -sI http://localhost:8001/proposalTool/static/fonts/gs-sans-variable.woff2
```

---

## Conventions followed

| Convention | How |
|---|---|
| Folder / file naming | `proposalTool` throughout, lowerCamelCase |
| Script at end of `<body>` | Single file; internal order is load-bearing (core → picker → implementation → boot) |
| `'use strict'` | At the top of the JS |
| `API_BASE` guard | `if (typeof API_BASE === 'undefined') window.API_BASE = window.location.origin + '/api'` |
| Bootstrap | `DOMContentLoaded`, with a `readyState` guard |
| Errors | `#alertArea` div; `showAlert(kind, message)`; non-2xx `error` field surfaced |
| 401 | `if (body.loginUrl) window.location = body.loginUrl` |
| Credentials | `credentials: 'same-origin'` so the kerberos cookie travels |
| No module system | Globals only; no `import`, no bundler, no build step at serve time |
| Direct DOM writes | `innerHTML` / `replaceChildren`; no framework |

## Deliberate deviations

| Deviation | Why |
|---|---|
| **No `← Back to Dashboard` link, no host header shape.** Fixed left rail instead. | Agreed: this page is independent of the others. |
| **No `onegsTheme.css` / `dashboard.css`.** Self-contained stylesheet. | Theme shift happens at port time. Every colour resolves through `:root` custom properties, so retheming to `--gs-*` is a token change, not a hunt through component CSS. |
| **No `isgCodeSelector.js`.** | Confirmed not schema-scoped. |
| **Inline SVG charts, not Chart.js from CDN.** | Self-contained, so nothing breaks on a network that blocks `cdn.jsdelivr.net`. |
| **Local woff2 fonts** rather than the system stack. | The house faces. `@font-face` URLs are `../fonts/*`, relative to the stylesheet, which resolves identically under the Flask route and from `file://`. |
| **Relative `href`/`src` in the HTML** rather than absolute `/proposalTool/...`. | Resolves to the same URL under the Flask route, and keeps the page openable from disk for review. |

## Backend, when the data is wired

The page currently generates its data client-side. `apiFetch()` in the JS is the seam.

Endpoints go on `pmgService/dashboardRouter.py` under `/api/v1/` — the Flask proxy rewrites
`/api/<x>` to `/api/v1/<x>`, so the page calls `/api/scenario/...` and the router serves
`/api/v1/scenario/...`. The proxy's `timeout=300` comfortably covers the ~5s analytics call.

Writes need `Depends(requireEditor)`; reads inherit `requireAuth` from the router.

## Not addressed

- **No tests.** The dashboard package has none, and this page adds none to the host. The four
  suites used during development run under a DOM shim outside the repo.
- **`dataversion`.** Every `epsilonPhi` config table is keyed on `currency` + `dataversion`. Which
  version the adapter reads is a host concern; no control is surfaced.
