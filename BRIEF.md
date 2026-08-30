# Engineering brief — Epsilon Phi Proposal Tool

**To:** the engineer building this
**With:** everything in this directory. Start at `README.md`.

---

## The job

Build and ship the Proposal Tool inside `cyrus_pmg.dashboard`: a PWA enters a client mandate,
compares up to four model portfolios, attaches a sleeve of investible products to each category of
the base, and exports the result to Excel.

**Done means:** a PWA can complete that flow end to end against real `epsilonPhi` analytics, on the
host, behind the host's auth, and download a workbook they would put in front of a client.

---

## Read this first, because it changes how you work

The two halves of this system are specified to **deliberately different depths**.

**The front end is fully specified.** `spec.html` is a build reference, not a sketch. Every screen,
every state — loading, empty, error, permissions — is in §10. Every breakpoint is in §12, every
ARIA obligation in §13. If you find yourself inventing UI behaviour, you have found a gap in the
spec: tell me, and I will fill it. Do not fill it yourself and move on.

**The back end is specified as a contract only.** `backend/scenario_port.py` is eight methods.
*What* they return is pinned down (§3.4, §3.5). *How* you implement them is entirely yours —
caching, session handling, connection pooling, error mapping, all your call. I have no opinion and
no requirements beyond the contract.

So: **push back on the front end, exercise judgement on the back end.** Reversing that is the most
expensive mistake available here.

---

## Sequence it in three phases

The fixtures adapter (§4.4) is what makes this safe to sequence. The entire UI was built,
demonstrated and handed over with **no Python analytics running at all**. Use that.

### Phase 1 — Port the page, no backend

Copy `proposalTool/` into the dashboard, add one Flask route and one nav link (`PORTING.md`), and
keep the client-side data generation as the fixtures adapter.

**Done when:** the page is reachable on the host behind Kerberos auth, and every state in §10 —
including the error and timeout states — is reachable. This is demoable to PMG. It is worth
demoing before you write any backend, because it is where design feedback is cheapest.

### Phase 2 — Implement the port

Write a class implementing `ScenarioPort` against `epsilonPhi` and stand the endpoints up on
`dashboardRouter.py` under `/api/v1/`.

Take `resolve_portfolio` seriously and leave it until you have the others working. It is the
expensive call — seconds, not milliseconds — and it is called once per column, up to four times per
scenario, again on every basis change. The spec says implementations should cache; how is yours.
The 200ms skeleton rule in §10.1 exists because of this call.

**Done when:** every method is backed by real data and the fixtures adapter still passes the same
§10 states, because you kept it. Do not delete it — it is how the next person debugs this without
a database.

### Phase 3 — Swap and harden

Flip `apiFetch()` to the real endpoints. Then the things that are nobody's favourite and always
matter: error mapping onto the §3.5 contract, the export path end to end, and behaviour when the
analytics call is slow rather than fast.

**Done when:** a PWA completes the flow on real data and the workbook opens clean.

---

## Non-negotiable, and why

These are constraints, not preferences. Each one has a reason; if a reason is wrong, tell me and
we change it deliberately.

| Constraint | Why |
|---|---|
| **No framework, no bundler, no module system** | The host has none. Globals on `window`, `<script src>` order is load-bearing. Introducing a build step makes this page the odd one out in a codebase nobody has time to modernise. |
| **One page folder, one Flask route** | The host's convention (§3.2). The landing is a *phase*, not a route (§7.1) — a landing with its own URL breaks this. |
| **`ScenarioPort` is eight methods** | Every method you add is a coupling the next transplant has to satisfy. If you need a ninth, that is a real conversation, not a quiet addition. |
| **The schema is data, not code** | §4.3 draws the line precisely. Option values, thresholds, the sleeve library and the availability set come *from the port*. Hardcode them and the next transplant means editing screen code. |
| **WCAG 2.2 AA** | §13. No audit is scheduled, which makes it your standard rather than someone else's checklist. |
| **Fonts served locally** | Five woff2 files. No network request from this page, ever. |

**On deviating:** §3.3 lists the deviations from host convention I made *deliberately*, with
reasons. That is the format. If you deviate, do it the same way — write down what and why — rather
than silently.

---

## Decide before you need them, not before you start

Almost nothing in §16 blocks you. Start now. But three want an answer before a specific moment:

| # | Question | Needed before |
|---|---|---|
| 6 | **Export contents** — assumed to be the three on-screen tables and nothing else | you write the export adapter (phase 2) |
| 3 | **TAA modelling** — assumed to be a small reduction in return and volatility, not its own line item | you build the allocation table, *if* the assumption is wrong |
| 1, 2 | **Theme** — palette is `mpo-ui`, host is OneGS; and the Excel/UI navies differ | port time. A token change by construction (§6.5) — but confirm that before phase 3 |

The rest — equity risk beta, exposure currency, advisor scope, retention, `dataversion` — block
nothing. Raise them when you hit them.

---

## What this package does *not* give you

Be clear-eyed about the gaps:

- **No tests.** §16 item 10. The host's dashboard package has none and this adds none. The suites
  used during development ran under a DOM shim outside the repo. **If you want a safety net, you
  are building it** — and I think you should, at least around the fee and rounding arithmetic in
  §8.4, which is where a silent error costs the most.
- **No deployment or release process.** Host concern.
- **No data migration.** The stored model allocations and the sleeve library must exist; who owns
  and maintains them is a PMG question, not an engineering one.
- **No performance budget.** I know `resolve_portfolio` is slow. I have not told you how slow is
  too slow, because I do not know what PMG will accept. Measure it in phase 2 and tell me.

---

## How to work with me

- **Read `DECISIONS.md` when you disagree with something.** Q1–Q45. Nearly every rule in the spec
  traces to a numbered exchange there, with the reasoning. If the reasoning does not survive
  contact with the host codebase, that is worth knowing.
- **Show me phase 1 before you start phase 2.** Design feedback is cheapest against a running page
  with fake data.
- **Flag a spec gap as a gap.** Do not paper over it. The document claims nothing is left for the
  implementer to invent; hold it to that.
