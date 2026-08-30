# Epsilon Phi Proposal Tool — engineering hand-off

Everything needed to build this service, front to back. Nothing here is left for you to
invent: where a decision is still open it is listed in **spec §16** and marked as such.

---

## What you are building

A client-facing page for the **Portfolio Management Group (PMG)** at Goldman Sachs. Its users are
**Private Wealth Advisors (PWAs)**.

It is built and run in **`epsilon-phi`**, but written to be **transplanted into
`isg-cyrus-pmg/src/cyrus_pmg`** — so the back end follows that codebase's design rather than a
convenient local one. `BRIEF.md` and `HOST_AUDIT.md` cover what that means in practice.

| Step | What the PWA does | Output |
|---|---|---|
| **1 — Asset allocation** | Enters the client mandate, sets the scenario basis, defines a base portfolio, compares it against up to three alternatives | Allocation table, two summary charts, three-section risk dashboard |
| **2 — Implementation** | Attaches one PMG-authored sleeve of investible products to each category of the base portfolio | Product-level model with weights, fees and notional, exported to Excel |

Both steps live in **one page**. It ships as a page folder inside the existing PMG dashboard:
plain HTML and JavaScript, **no build step, no framework**.

---

## Read in this order

| # | File | Why |
|---|---|---|
| — | **`PROMPT.md`** | The kickoff instruction, for whoever hands this to a developer or an agent. Not part of the build. |
| 0 | **`BRIEF.md`** | What you are being asked to do, in what order, and what is non-negotiable. Read it before anything else. |
| 0.5 | **`HOST_AUDIT.md`** | How `cyrus_pmg.dashboard` is built. The back end must follow its design, because this gets transplanted there. §11, §12 and §13 are the working sections. |
| 1 | **`spec.html`** | The complete build reference. Open in a browser. 16 sections; start with §1 Purpose, §2 Domain model, §5 Front-end architecture. |
| 2 | **`proposalTool/proposalTool.html`** | The working prototype. Open it directly — no server, no network. Click through the whole flow before writing anything. |
| 3 | **`PORTING.md`** | Drop-in steps for `cyrus_pmg.dashboard`: copy the folder, add one Flask route, add one nav link. |
| 4 | **`backend/scenario_port.py`** | The eight-method adapter you implement. This is the entire backend contract. |
| 4.5 | **`backend/portfolio_weights.py`** | The supplied model allocations — 272 portfolios — and the loader that selects one. It is what sits behind `resolve_portfolio`. Spec §4.5. |
| 5 | **`DECISIONS.md`** | The decision log, Q1–Q45. Read when you want to know *why* — every rule in the spec traces back to a numbered exchange here. |

---

## What is in this directory

```
proposal-tool/
  README.md                  you are here
  PROMPT.md                  the kickoff instruction to hand a developer
  BRIEF.md                   the engineering brief: scope, phasing, constraints
  HOST_AUDIT.md              how the target host is built; the back end must match it
  spec.html                  the build reference, revision 3
  PORTING.md                 dropping it into the host dashboard
  DECISIONS.md               decision log, Q1-Q45

  proposalTool/              THE DELIVERABLE — copy this folder into the host verbatim
    proposalTool.html
    static/css/proposalTool.css
    static/js/proposalTool.js
    static/fonts/*.woff2     five house faces, served locally

  backend/
    scenario_port.py         the adapter contract, extracted from spec §4.2
    portfolio_weights.py     the supplied weight universe + loader (spec §4.5)
    portfolio_weights.xlsx   the same data as a workbook

  generator/                 development tool, does NOT ship
    build_styles.py          rewrites ../proposalTool/ ; run: python3 build_styles.py
    pickers.py               shared core: state, model, tables, charts, rail, dialog
    implementation.py        step 2: sleeve library, product table, fee arithmetic
    themes.py                design tokens
    fonts/                   font sources, copied into the page folder on build

  reference/
    landing-swiss.html       the chosen landing as a standalone page (design reference for §7.1)
```

**Do not hand-edit anything under `proposalTool/`** — it is overwritten every time the generator
runs. Change `generator/*.py` and rebuild.

---

## The backend, in one paragraph

The page currently generates its data client-side; **`apiFetch()` in the JS is the seam**. Implement
`ScenarioPort` (eight methods) against your data layer, expose it over the HTTP surface in
**spec §3.4**, and the front end needs no changes. Endpoints go on
`pmgService/dashboardRouter.py` under `/api/v1/`; the Flask proxy rewrites `/api/<x>` to
`/api/v1/<x>`, so the page calls `/api/scenario/...`. The proxy's `timeout=300` comfortably covers
the ~5s analytics call. Writes need `Depends(requireEditor)`; reads inherit `requireAuth`.

| Endpoint (as the page calls it) | Latency |
|---|---|
| `GET /api/scenario/schema` | fast, called on open and on every basis/mandate change |
| `GET /api/scenario/advisors?q=` | fast |
| `POST /api/scenario` | fast |
| `GET /api/scenario/{id}` | fast |
| `POST /api/scenario/{id}/portfolio` | **expensive — seconds** |
| `DELETE /api/scenario/{id}/portfolio/{key}` | fast |
| `GET /api/scenario/sleeves?category=` | fast |
| `POST /api/scenario/{id}/export` | moderate |

Error contract is **spec §3.5**: non-2xx JSON carries `error`; 401 carries `loginUrl`.

---

## Things that will bite you

These are in **spec §15 Implementation traps** in full. The four that cost the most time:

- **`[hidden]` loses to author `display` rules.** The landing toggles visibility with `.hidden`,
  and `.steps{display:flex}` silently beats the UA rule. The stylesheet uses
  `[hidden]{display:none!important}` for exactly this reason.
- **Every column carries its own status.** There is no global loading flag. Three columns can be
  resolving while a fourth errors and the base is ready — all four render at once.
- **The 200ms skeleton rule.** Implement as a timer cancelled by the response, *not* a minimum
  display duration.
- **Preserve focus across re-render.** Record `activeElement.id` and the selection range before
  replacing markup, or the typeahead loses the caret every time a column resolves.

---

## Naming

The product is the **Epsilon Phi Proposal Tool**, shortened to **Proposal Tool** in the interface.

A **"scenario" is what a PWA builds; the Proposal Tool is what builds it.** The domain noun is
unchanged throughout — `scenarioId`, the `/api/scenario/…` surface, `ScenarioPort`, the
*Scenario basis* rail tier, the *New scenario* dialog, and the export filename. Renaming the
product does not rename the thing it produces. The rule is **spec §1.3**.

---

## Conformance

**WCAG 2.2 AA** is the target, and **spec §13** is the standard rather than a checklist for someone
else. No formal audit is scheduled. Reduced-motion users get the finished layout with no animation.
