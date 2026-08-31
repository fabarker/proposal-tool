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
| **`accessGate.js` and `globals.js` are included** with absolute `/static/js/...` paths. | Host convention, and the auth gate the brief requires. These are the host's own shared modules at the host's own paths; opened from `file://` they 404 harmlessly and the page's `typeof API_BASE` guard keeps working. |

The interface moved on from the specification in a number of places while it was being built
— the topbar is gone, the base column is headed "Proposed Portfolio", the risk dashboard's
premia are banded by measure, and the tables size their own columns.
**`spec.html` revision 6 describes what is there now**, and its §1.5 maps every changed
section to the entry in **`service/DEVIATIONS.md`** (D20–D46) that explains it. Two matter for
a port: **D29**, implementation variants — step 2 opens on a required choice of one of four
product universes, which changes the `list_sleeves` signature and adds `variant` to the
scenario state; and **D35/D36**, naming — the risk level is shown by a label served in the
schema while the key keeps its short value, so nothing about the availability set or the bake
changes.

## Backend

**The back end is built.** It is no longer a seam to fill: the page fetches everything through
`apiFetch()`, and the service behind it lives in `service/` as a mirror of this host's
topology, written to be copied in. Two verbatim copies and two documented inserts —
**`service/TRANSPLANT.md` is the file-by-file list**, and it contains configuration only.

| | |
|---|---|
| The page folder | copy as above — it needs no edits |
| `pmgService/scenario/` | copy verbatim: the port, both adapters, the rules, the store, the bake |
| `pmgService/dashboardRouter.py` | insert the scenario endpoint block, already written in host style |
| `dashboardFrontend.py`, `index.html` | the route and nav link above |

Endpoints sit under `/api/v1/` — the Flask proxy rewrites `/api/<x>`, so the page calls
`/api/scenario/...`. Writes take `Depends(requireEditor)`; reads inherit `requireAuth`.

Which data layer serves them is one environment variable, `SCENARIO_ADAPTER`:

| | Data | Cold resolve | Database |
|---|---|---:|---|
| `fixtures` | supplied weights, synthetic analytics | ~0ms | no |
| `epsilonphi` | live analytics | ~97s | yes |
| `baked` | precomputed epsilonPhi results | **~15ms** | no |

`baked` is the production setting: the analytics are computed once per data version by an
offline job and served from disk. See `service/PERFORMANCE.md` for why, and for the profile
that got a cold portfolio from 255s to milliseconds.

## Not addressed

- **No tests reach the host.** The dashboard package has none and this adds none to it; the
  suite lives on the epsilon-phi side, in `service/tests/`.
- **`dataversion`.** Every `epsilonPhi` config table is keyed on `currency` + `dataversion`. Which
  version the adapter reads is a host concern; no control is surfaced. It does decide when the
  bake is stale — re-bake when it changes.
- **Currency coverage.** On the development database only USD and GBP have currency configs,
  so CHF and EUR cannot resolve at all. The UI offers four because the supplied weights carry
  four. A host decision: restrict the list, or load the rows.

## Where the rest is written down

| File | What it carries |
|---|---|
| `service/README.md` | running it, the wire contract, the three adapters, baking |
| `service/TRANSPLANT.md` | the porting list, file by file |
| `service/DEVIATIONS.md` | every departure from the package, D1–D28, and the spec gaps found |
| `service/PERFORMANCE.md` | the analytics profile, its causes, and the measurements |
