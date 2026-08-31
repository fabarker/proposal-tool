# Engineering brief — Epsilon Phi Proposal Tool

> **Status: built (31 Aug 2026).** This brief is kept as issued — it records what was asked
> and in what order, and the phasing below is what was followed. For what now exists, read
> `service/README.md`; for how it departs from this package, `service/DEVIATIONS.md`; for the
> analytics work the brief could only ask for ("measure it and record the number"),
> `service/PERFORMANCE.md`.

**To:** the engineer building this, working independently
**With:** this directory. Everything you need is in it. Start at `README.md`, then read this.

---

## The job

Build the Proposal Tool **fully working inside `epsilon-phi`** — real `epsilonPhi` analytics, real
data, a PWA can complete the whole flow and download a workbook.

But build the back end **exactly as it would have to be built inside
`isg-cyrus-pmg/src/cyrus_pmg`**, following that codebase's design, because **this is going to be
transplanted into Cyrus.** `epsilon-phi` is where you develop it. Cyrus is what you are writing it
for.

---

## The one test that governs every decision

> **When this is copied into `cyrus_pmg.dashboard`, what has to change?**
>
> The answer must be **configuration, never structure.**

Ports, hostnames, the allowlist source, the database connection — those change. Route shapes,
handler names, the proxy contract, the auth seams, the file layout, the naming conventions — those
must already be right.

If you catch yourself writing something that "we'll restructure when it moves", stop. That is the
failure mode this brief exists to prevent. A transplant that needs a rewrite is not a transplant.

---

## Study the package before you write anything

You are working alone and there is no author to ask. That is deliberate; it also means the reading
is not optional. Budget real time for it.

| Order | Read | What you are extracting |
|---|---|---|
| 1 | **`HOST_AUDIT.md`** §11 Conventions, §12 Recipe, §13 Gotchas | How Cyrus does it. §12 is a literal add-a-page recipe — your work is that recipe, at scale. |
| 2 | **`spec.html`** §1–§5 | What you are building and the architecture it must have. §4.3 is the sharpest page in the document: it draws the line between what must be server-supplied and what may be hardcoded. |
| 3 | **`proposalTool/proposalTool.html`** | Open it. Click every control, resize it, tab through it. It is the behavioural reference — faster than reading §7–§9 cold. |
| 4 | **`spec.html`** §6–§14 | The detail. §10 is exhaustive on states; §13 is the accessibility standard. |
| 5 | **`backend/scenario_port.py`** and **`portfolio_weights.py`** | The eight methods, and the supplied weights behind `resolve_portfolio` (spec §4.5). Read §16 items 3 and 12–14 before building the allocation table — the supplied weights contradict the spec in three places, left unreconciled on purpose. |
| 6 | **`DECISIONS.md`** | Q1–Q50. Read when you disagree with something — nearly every rule traces to a numbered exchange with the reasoning. |

---

## Mirror the host topology in `epsilon-phi`

Do not invent a development architecture. Reproduce Cyrus's, so the code is already in its final
shape. From `HOST_AUDIT.md` §4 and spec §3.1, that is:

**A Flask static server** in the shape of `dashboardFrontend.py` — one hand-written `@app.route`
per page folder, served with `send_from_directory`, view functions in `snake_case` each carrying a
one-line docstring. No templating. No auto-discovery.

**A wildcard proxy route** rewriting `/api/<x>` to `/api/v1/<x>`, with `timeout=300` and
`allow_redirects=False`. Catch `requests.ConnectionError` → 502 and `requests.Timeout` → 504, both
with a small JSON body carrying `error`. `allow_redirects=False` is deliberate — it lets the
browser see the backend's 302. Do not change it.

**A FastAPI service** in the shape of `pmgService`, with a router in the shape of
`dashboardRouter.py` mounted under `/api/v1/`. Handler names in `mixedCase` — note this differs
from the Flask side, and matching both is part of transplanting cleanly. Reads inherit
`requireAuth`; writes take `Depends(requireEditor)`.

**An auth gate** in the shape of the `before_request` allowlist, with `accessGate.js` probing
`/api/whoami`. Stub the allowlist source; keep the gate, its position, and its 403 response shape.

**The page itself** stays exactly as delivered: one folder, `lowerCamelCase`, the same string for
URL prefix, HTML, CSS and JS filenames. No bundler, no TypeScript, no module system. Globals
guarded with `if (typeof X === 'undefined')`, bootstrap on `DOMContentLoaded`, errors rendered into
`#alertArea`, state in module-level `let`.

---

## What to stub, and how to stub it

Some things cannot exist in `epsilon-phi`. Stub the *implementation*; keep the *seam* exactly where
Cyrus needs it.

| Thing | Stub it as | Keep exactly |
|---|---|---|
| Kerberos allowlist | An env-var list, as local Cyrus dev already does (`PMG_ALLOWED_KERBEROS`) | The `before_request` gate, its position in the stack, the 403 card, the `/api/whoami` probe |
| GitLab CI / `gns` distribution | Nothing. Out of scope | — |
| Stored model allocations | **Nothing — these are supplied.** `backend/portfolio_weights.py`, 272 portfolios, spec §4.5 | Drive `get_schema`'s availability from its `ui_available` column, not a second list |
| PMG sleeve library | Same | Same |
| Advisor directory | Excel file, as the reference adapter does | `search_advisors` signature and its 2-character threshold behaviour |

**Keep the fixtures adapter permanently.** Spec §4.4 is emphatic and it is right: a static-data
adapter is how this UI was built and demonstrated with no analytics running at all. Every state in
§10 — including error and timeout — must stay reachable through it. It is how the next person
debugs this without a database, and it is how you will demo before the analytics work.

---

## Sequence

### Phase 1 — Stand up the host shape

Flask static server, proxy route, FastAPI service, router, auth gate. Page served from its folder.
Data still generated client-side.

**Done when:** the page loads through the Flask server at a Cyrus-shaped URL, the auth gate rejects
an unlisted user with the right card, and `/api/…` returns a well-formed 502 when the FastAPI
service is down. That last one matters — it proves the proxy is right before anything depends on it.

### Phase 2 — Move the data behind HTTP

Implement `ScenarioPort` with a **fixtures** implementation and serve it over the real endpoints
(spec §3.4). Flip the page from client-side generation to `apiFetch()`.

**Done when:** the UI is talking real HTTP for every one of the eight methods, every §10 state is
still reachable, and no analytics have been written yet.

### Phase 3 — Implement against `epsilonPhi`

A second `ScenarioPort` implementation, real analytics. Leave `resolve_portfolio` until the others
work: it is the expensive call — seconds — hit once per column, up to four per scenario, again on
every basis change. Spec §10.1's 200ms skeleton rule exists because of it. Caching is yours to
design; the spec says only that you should.

**Done when:** a PWA completes the flow on real data and the exported workbook opens clean.

### Phase 4 — Transplant rehearsal

Write down, file by file, what a copy into `cyrus_pmg` would touch, and what would have to change.
Fix anything on that list that is structural rather than configuration.

**Done when:** the list contains only config.

---

## Where your judgement is invited — and where it is not

You are expected to make calls. Here is the map, so you spend your judgement in the right places.

**Yours, decide and move on:** everything behind the port — caching strategy, session handling,
connection pooling, query shape, how `epsilonPhi` is called, error mapping onto the §3.5 contract,
file and module organisation on the Python side, and how you stub what cannot exist locally.

**Follow the package; deviate only with a written reason:** the HTTP surface (§3.4), the error
contract (§3.5), the eight-method port, the server-supplied/hardcodable split (§4.3), the host
conventions in `HOST_AUDIT.md` §11.

**Do not deviate without stopping to think hard:** no framework, no bundler, no module system; one
page folder and one route; the landing as a *phase* not a route (§7.1); WCAG 2.2 AA (§13); fonts
served locally. Each of these is load-bearing for either the transplant or the host's constraints,
and §3.3 explains the ones that are already deliberate deviations.

**When you do deviate, use the §3.3 format:** what you changed, and why. A deviation that is
written down is a decision. One that is not is a defect the next person inherits.

**If the spec has a gap** — you are inventing UI behaviour rather than reading it — that is a
defect in the document, not a licence. Record it, choose the option most consistent with the
surrounding spec, and note what you chose and why. The document claims nothing is left for the
implementer to invent; where it fails that, the record is what protects the next person.

---

## Decide these before you need them

Almost nothing in spec §16 blocks you. Start now. Three want an answer before a specific moment:

| # | Question | Needed before |
|---|---|---|
| 6 | Export contents — assumed to be the three on-screen tables and nothing else | you write the export adapter (phase 3) |
| 3 | TAA modelling — assumed to be a small reduction in return and volatility, not its own line item | the allocation table, *if* the assumption is wrong |
| 1, 2 | Theme — palette is `mpo-ui`, host is OneGS; and the Excel and UI navies differ | phase 4. A token change by construction (§6.5) |

The rest — equity risk beta, exposure currency, advisor scope, retention, `dataversion` — block
nothing.

---

## What this package does not give you

- **No tests.** Spec §16 item 10: the host's dashboard package has none and this adds none. The
  suites used in development ran under a DOM shim outside the repo. If you want a safety net you
  are building it — and I would, around the fee and rounding arithmetic in §8.4, where a silent
  error is both most likely and most costly.
- **No performance budget.** `resolve_portfolio` is slow. Nobody has said how slow is unacceptable.
  Measure it and record the number.
- **No deployment process.** Host concern, and out of scope here.
- **No data ownership answer.** The stored allocations and the sleeve library have to come from
  somewhere and be maintained by someone. That is a PMG question; flag it early, because phase 3
  stalls without it.

---

## Definition of done

1. A PWA completes mandate → comparison → implementation → Excel export, on real `epsilonPhi`
   analytics, through the Flask/FastAPI stack, behind the auth gate.
2. Every state in spec §10 is reachable, through both the fixtures and the real implementation.
3. §13 accessibility obligations hold: keyboard path, focus management, reduced motion, contrast.
4. The phase 4 transplant list contains configuration only.
5. Every deviation from the package is written down in the §3.3 format.
