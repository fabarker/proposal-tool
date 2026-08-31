#!/usr/bin/env python3
"""
Generate 20 visual-direction explorations for the Portfolio Analytics UI.

Every file renders the SAME screen with the SAME data so the directions can be
compared fairly: the scenario parameter form (showing the cascade and a disabled
dependent field), the allocation table, and the three-section risk dashboard with
its mixed column span.

Run:  python3 build_styles.py
Then: open index.html
"""
import os
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────────────────────── mock content ──
PORTFOLIOS = ["USD Core Mod", "USD Full Mod Agg", "GBP Ex Alts Cons ex RE"]

ALLOCATION = [
    ("Cash & Equivalents", 4.0, [
        ("Cash", 2.5, 1.5, 6.0), ("Short Duration Credit", 1.5, 1.0, 4.0)]),
    ("Fixed Income", 31.0, [
        ("Government Bonds", 12.0, 8.0, 26.0), ("Investment Grade Credit", 11.0, 8.5, 18.0),
        ("High Yield Credit", 5.0, 4.5, 4.0), ("Emerging Market Debt", 3.0, 3.0, 2.0)]),
    ("Equities", 45.0, [
        ("US Equity", 22.0, 26.0, 24.0), ("Europe Equity", 9.0, 11.0, 10.0),
        ("Japan Equity", 4.0, 5.0, 4.0), ("Emerging Market Equity", 10.0, 12.0, 6.0)]),
    ("Alternatives", 14.0, [
        ("Hedge Funds", 6.0, 8.0, 0.0), ("Private Equity", 8.0, 11.0, 0.0)]),
    ("Real Estate", 6.0, [
        ("Listed Real Estate", 2.5, 3.0, 0.0), ("Direct Real Estate", 3.5, 5.0, 0.0)]),
]
CATEGORY_TOTALS = {"Cash & Equivalents": (4.0, 2.5, 10.0), "Fixed Income": (31.0, 24.0, 50.0),
                   "Equities": (45.0, 54.0, 44.0), "Alternatives": (14.0, 19.0, 0.0),
                   "Real Estate": (6.0, 8.0, 0.0)}
METRICS = [("Estimated Mean Return", "5.84%", "6.51%", "4.62%"),
           ("Sharpe Ratio", "0.42", "0.45", "0.38"),
           ("Volatility", "9.71%", "11.24%", "7.08%")]
CRISES = [("Global Financial Crisis 2008", ["-28.14%", "-31.02%", "-30.88%", "-33.41%", "-21.05%", "-24.60%"]),
          ("European Debt Crisis 2011", ["-11.42%", "-12.88%", "-13.20%", "-14.51%", "-8.31%", "-9.77%"]),
          ("COVID-19 Drawdown 2020", ["-16.73%", "-17.09%", "-18.94%", "-19.30%", "-12.44%", "-12.80%"]),
          ("Rates Repricing 2022", ["-14.08%", "-20.55%", "-15.31%", "-21.78%", "-13.02%", "-19.49%"])]
RISK_PREMIA = [("Value at Risk · 99% · 1 Month", ["-6.42%", "-6.71%", "-7.38%", "-7.67%", "-4.83%", "-5.12%"]),
               ("Value at Risk · 99% · 1 Year", ["-14.80%", "-17.22%", "-17.05%", "-19.47%", "-10.91%", "-13.33%"]),
               ("Value at Risk · 99% · 3 Years", ["-19.36%", "-25.94%", "-22.30%", "-28.88%", "-14.02%", "-20.60%"]),
               ("Conditional VaR · 99% · 1 Year", ["-18.55%", "-21.04%", "-21.36%", "-23.85%", "-13.70%", "-16.19%"]),
               ("Probability of Loss · 1 Year", ["21.4%", "27.8%", "23.9%", "30.1%", "18.2%", "24.6%"]),
               ("Probability of Loss · 3 Years", ["11.7%", "19.3%", "13.1%", "21.0%", "9.4%", "16.8%"])]

# ────────────────────────────────────────────────────────────────── markup ─────
def fmt(v):
    return "—" if v == 0.0 else "{:.1f}%".format(v)

def allocation_table():
    rows = []
    for cat, _, assets in ALLOCATION:
        t = CATEGORY_TOTALS[cat]
        rows.append('<tr class="cat"><th scope="row">{}</th>{}</tr>'.format(
            cat, "".join('<td class="num">{}</td>'.format(fmt(x)) for x in t)))
        for name, a, b, c in assets:
            alt = " alt" if len([r for r in rows if 'class="asset' in r]) % 2 else ""
            rows.append('<tr class="asset{}"><th scope="row">{}</th>{}</tr>'.format(
                alt, name, "".join('<td class="num">{}</td>'.format(fmt(x)) for x in (a, b, c))))
    rows.append('<tr class="total"><th scope="row">Total</th>'
                '<td class="num">100.0%</td><td class="num">100.0%</td><td class="num">100.0%</td></tr>')
    for label, *vals in METRICS:
        rows.append('<tr class="metric"><th scope="row">{}</th>{}</tr>'.format(
            label, "".join('<td class="num">{}</td>'.format(v) for v in vals)))
    heads = "".join('<th scope="col" class="num">{}{}</th>'.format(
        p, '<span class="base-tag">Base</span>' if i == 0 else "") for i, p in enumerate(PORTFOLIOS))
    return ('<table class="tbl alloc"><caption class="sr-only">Portfolio allocation and metrics</caption>'
            '<thead><tr><th scope="col" class="rowhead">Asset</th>{}</tr></thead>'
            '<tbody>{}</tbody></table>'.format(heads, "".join(rows)))

def risk_table():
    top = "".join('<th scope="colgroup" colspan="2" class="num">{}{}</th>'.format(
        p, '<span class="base-tag">Base</span>' if i == 0 else "") for i, p in enumerate(PORTFOLIOS))
    sub = "".join('<th scope="col" class="num sub">Nominal</th><th scope="col" class="num sub">Real</th>'
                  for _ in PORTFOLIOS)
    rows = ['<tr class="band"><th scope="rowgroup" colspan="7">Factor Based Risk Analytics</th></tr>']
    for cat, _, _ in ALLOCATION:
        t = CATEGORY_TOTALS[cat]
        rows.append('<tr class="cat"><th scope="row">{}</th>{}</tr>'.format(
            cat, "".join('<td class="num span2" colspan="2">{}</td>'.format(fmt(x)) for x in t)))
    for label, *vals in METRICS:
        rows.append('<tr class="metric"><th scope="row">{}</th>{}</tr>'.format(
            label, "".join('<td class="num span2" colspan="2">{}</td>'.format(v) for v in vals)))
    rows.append('<tr class="band"><th scope="rowgroup" colspan="7">Predicted Performance Over Stress Periods</th></tr>')
    for i, (label, vals) in enumerate(CRISES):
        rows.append('<tr class="asset{}"><th scope="row">{}</th>{}</tr>'.format(
            " alt" if i % 2 else "", label,
            "".join('<td class="num neg">{}</td>'.format(v) for v in vals)))
    rows.append('<tr class="band"><th scope="rowgroup" colspan="7">Portfolio Risk Premia</th></tr>')
    for i, (label, vals) in enumerate(RISK_PREMIA):
        cls = "num" if label.startswith("Prob") else "num neg"
        rows.append('<tr class="asset{}"><th scope="row">{}</th>{}</tr>'.format(
            " alt" if i % 2 else "", label,
            "".join('<td class="{}">{}</td>'.format(cls, v) for v in vals)))
    return ('<table class="tbl risk"><caption class="sr-only">Risk dashboard</caption><thead>'
            '<tr><th scope="col" rowspan="2" class="rowhead">Measure</th>{}</tr><tr>{}</tr></thead>'
            '<tbody>{}</tbody></table>'.format(top, sub, "".join(rows)))

FIELDS_GATE = [("Top Account Size", "input", "$48,500,000"), ("Mandate Size", "input", "$26,000,000"),
               ("Primary PWA", "typeahead", "M. Aldridge — Zurich")]
FIELDS_MODEL = [("Hedging Policy", "select", ["Hedged", "ISG Hedged", "Unhedged", "Equity Not Hedged"]),
                ("Currency", "select", ["USD", "CHF", "GBP", "EUR"]),
                ("Risk Level", "select", ["Mod", "Low Vol", "Cons", "Cons Mod", "Mod Agg", "Agg", "All Equity"]),
                ("AA Type", "select", ["Core", "Full", "Ex HFs", "Ex Alts"])]

def form_markup():
    gate = []
    for label, kind, val in FIELDS_GATE:
        fid = label.lower().replace(" ", "-")
        extra = ('<span class="ta-hint">3 matches</span>' if kind == "typeahead" else "")
        gate.append('<div class="field"><label for="{0}">{1}</label>'
                    '<input id="{0}" type="text" value="{2}" spellcheck="false">{3}</div>'
                    .format(fid, label, val, extra))
    model = []
    for label, _, opts in FIELDS_MODEL:
        fid = label.lower().replace(" ", "-")
        o = "".join('<option>{}</option>'.format(x) for x in opts)
        model.append('<div class="field"><label for="{0}">{1}</label>'
                     '<select id="{0}">{2}</select></div>'.format(fid, label, o))
    model.append('<div class="field is-disabled"><label for="include-re">Include Real Estate</label>'
                 '<div class="toggle" role="switch" aria-checked="false" aria-disabled="true" '
                 'aria-labelledby="include-re"><span class="knob"></span></div>'
                 '<span class="field-note">Unavailable for AA Type “Core”</span></div>')
    return ('<form class="scenario" novalidate>'
            '<fieldset class="grp grp-gate"><legend><span class="grp-idx">Mandate</span>'
            '<span class="grp-hint">Required before the model can be defined</span></legend>'
            '<div class="fieldgrid">{}</div></fieldset>'
            '<fieldset class="grp grp-model"><legend><span class="grp-idx">Model</span>'
            '<span class="grp-hint">Defines the portfolio to look up</span></legend>'
            '<div class="fieldgrid">{}</div></fieldset>'
            '<div class="formactions"><button type="button" class="btn btn-primary">Build portfolio</button>'
            '<button type="button" class="btn btn-ghost">Reset</button></div>'
            '</form>'.format("".join(gate), "".join(model)))

# ──────────────────────────────────────────────────────────────── base css ─────
def caret(c):
    c = c.replace("#", "%23")
    return ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='7'%3E"
            "%3Cpath d='M1 1l4.5 4.5L10 1' stroke='" + c + "' stroke-width='1.6' fill='none' "
            "stroke-linecap='round'/%3E%3C/svg%3E\")")

BASE_CSS = """
/* ── house typefaces, served locally: no network, no fallback surprise ──
   GS Sans and GS Sans Condensed are variable; the weight ranges below let the
   browser interpolate rather than synthesising. Goldman Sans ships regular only,
   so it is used at 400 for the wordmark and nowhere a bold is asked for.
   Roboto is the fallback: 1250 glyphs against GS Sans's 500. */
@font-face{font-family:"GS Sans";src:url("../fonts/gs-sans-variable.woff2") format("woff2");
  font-weight:250 700;font-style:normal;font-display:swap}
@font-face{font-family:"GS Sans Condensed";
  src:url("../fonts/gs-sans-condensed-variable.woff2") format("woff2");
  font-weight:300 900;font-style:normal;font-display:swap}
@font-face{font-family:"Goldman Sans";src:url("../fonts/goldman-sans-regular.woff2") format("woff2");
  font-weight:400;font-style:normal;font-display:swap}
@font-face{font-family:"Roboto";src:url("../fonts/roboto-regular.woff2") format("woff2");
  font-weight:400;font-style:normal;font-display:swap}
@font-face{font-family:"Roboto";src:url("../fonts/roboto-medium.woff2") format("woff2");
  font-weight:500;font-style:normal;font-display:swap}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--f-body);
  font-size:17px;line-height:1.5;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
/* The UA rule for [hidden] is display:none at low precedence, so any author rule
   that sets display - .steps{display:flex}, #view-landing{display:flex} - silently
   beats it and the element stays visible. */
[hidden]{display:none!important}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0 0 0 0);white-space:nowrap;border:0}
/* No topbar: the workspace opens straight onto the step nav. The top padding
   is what the topbar used to contribute - enough that the first control is
   not flush against the viewport edge. */
.shell{max-width:1500px;margin:0 auto;padding:clamp(14px,2vw,24px) clamp(16px,3vw,44px) 96px}

.sec{margin-bottom:clamp(26px,4vw,50px)}
.sec-head{display:flex;align-items:baseline;justify-content:space-between;gap:16px;
  margin-bottom:14px;flex-wrap:wrap}
h1,h2{margin:0;font-family:var(--f-display);font-weight:var(--head-weight,600);color:var(--ink)}
h1{font-size:clamp(28.5px,3.5vw,41.5px);letter-spacing:var(--head-ls,-.015em);line-height:1.15}
h2{font-size:clamp(18.5px,1.9vw,23px);letter-spacing:var(--head-ls,-.01em)}
.sec-note{font-size:14.5px;color:var(--ink-3);max-width:52ch}
.lede{font-size:16.5px;color:var(--ink-2);max-width:62ch;margin:10px 0 0}

.panel{background:var(--surface);border:var(--panel-border,1px solid var(--line));
  border-radius:var(--radius);box-shadow:var(--shadow);padding:clamp(18px,2.6vw,30px)}

.scenario{display:grid;gap:clamp(18px,2.4vw,26px)}
.grp{border:0;padding:0;margin:0;min-width:0}
.grp+.grp{border-top:1px dashed var(--line-strong);padding-top:clamp(18px,2.4vw,26px)}
legend{padding:0;margin-bottom:13px;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;width:100%}
.grp-idx{font-family:var(--f-num);font-size:12px;letter-spacing:.18em;text-transform:uppercase;
  color:var(--accent);font-weight:700}
.grp-hint{font-size:14px;color:var(--ink-3)}
.fieldgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(205px,1fr));gap:14px 18px}
.field{display:flex;flex-direction:column;gap:6px;position:relative;min-width:0}
.field label{font-size:13px;letter-spacing:.03em;color:var(--ink-2);font-weight:600}
input[type=text],select{font:inherit;font-family:var(--f-num);font-size:15.5px;color:var(--ink);
  background:var(--input-bg);border:1px solid var(--line-strong);border-radius:var(--radius-sm);
  padding:9px 11px;width:100%;appearance:none;transition:border-color .15s,box-shadow .15s}
select{background-image:var(--caret);background-repeat:no-repeat;
  background-position:right 11px center;background-size:11px 7px;padding-right:30px}
input[type=text]:hover,select:hover{border-color:var(--accent)}
input:focus-visible,select:focus-visible,.btn:focus-visible,.toggle:focus-visible{
  outline:2px solid var(--accent);outline-offset:2px}
.ta-hint{position:absolute;right:11px;top:32px;font-family:var(--f-num);font-size:11.5px;
  letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);pointer-events:none}
.field-note{font-size:12.5px;color:var(--ink-3);line-height:1.35}
.is-disabled{opacity:.48}
.toggle{width:42px;height:23px;border-radius:99px;background:var(--surface-2);
  border:1px solid var(--line-strong);position:relative}
.knob{position:absolute;top:2px;left:2px;width:17px;height:17px;border-radius:50%;background:var(--ink-3)}
.formactions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.btn{font:inherit;font-family:var(--f-num);font-size:14px;letter-spacing:.07em;
  text-transform:var(--btn-case,uppercase);font-weight:700;padding:11px 24px;
  border-radius:var(--radius-sm);border:1px solid transparent;cursor:pointer;transition:.15s}
.btn-primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.btn-primary:hover{filter:brightness(1.1)}
.btn-ghost{background:transparent;color:var(--ink-2);border-color:var(--line-strong)}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent)}

/* The frame follows the table. A capped table is narrower than the document,
   and a full-width wrapper would leave its surface and border stranded out to
   the right of the last column. fit-content shrinks to the table and still
   stops at the document edge, where overflow-x takes over and it scrolls. */
.tblwrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:var(--radius);
  width:fit-content;max-width:100%}
.tbl{width:100%;border-collapse:collapse;font-family:var(--f-num);font-size:15px;min-width:850px}
.tbl th,.tbl td{text-align:left;padding:var(--cell-pad);white-space:nowrap}
.tbl .num{text-align:right;font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1}
.tbl thead th{background:var(--head-bg);color:var(--head-ink);font-weight:700;font-size:12.5px;
  letter-spacing:.09em;text-transform:uppercase;border-bottom:2px solid var(--line-strong);
  position:sticky;top:0;z-index:2}
.tbl thead th.sub{font-size:12px;letter-spacing:.11em;opacity:.78;border-bottom-width:1px;font-weight:600}
.rowhead{text-align:left!important;position:sticky;left:0;z-index:3;background:var(--head-bg)}
/* The row-header column sizes to its longest label - category names and
   indented reporting names - and the numeric columns share the rest. In an
   auto-layout table a small percentage width resolves to the content's
   minimum, and cells are already nowrap. The implementation table is excluded:
   its first two columns are pinned at their specified widths (spec 9.3). */
.tbl:not(.impl) .rowhead,.tbl:not(.impl) tbody th{width:1%}
/* Every portfolio column exactly the same width, whatever the column count
   and however long the derived names are - a comparison table whose columns
   differ in width reads as though the numbers differ in kind. Auto layout
   cannot promise that: it honours each column's own content minimum, so the
   columns come out within a few pixels of each other but never equal. Fixed
   layout divides the remaining space equally by definition, so the table
   takes it, with the row header pinned to the measured content width the
   renderer publishes as --alloc-c1 and a min-width that keeps the equal
   columns wide enough for the longest header (see sizeAllocColumns). */
.tbl.alloc,.tbl.risk{table-layout:fixed}
.tbl.alloc thead th.rowhead{width:var(--fixed-c1,25%)}
.tbl.alloc thead th.num:not(.addcol){width:var(--fixed-col,auto)}
/* At the column cap a long derived name wraps rather than forcing the column
   wider - the cap is the point. Data cells stay nowrap. */
.tbl.alloc thead th.num,.tbl.risk thead th.num{white-space:normal}
/* The risk table declares its columns: its header cells span Nominal/Real
   pairs, and <col> widths are the only way to split a pair exactly evenly
   under fixed layout. */
.tbl.risk col.col-head{width:var(--fixed-c1,25%)}
.tbl.risk col.col-data{width:var(--fixed-col,auto)}
/* Section bands wrap instead of forcing the first column wide enough to hold
   "Conditional Value at Risk with 99% Confidence". */
tr.band th{white-space:normal}
/* the Nominal/Real strip sits in the body, where the split begins */
tr.subhead th{background:var(--head-bg);color:var(--head-ink);font-weight:600;
  font-size:12px;letter-spacing:.11em;text-transform:uppercase;opacity:.78;
  border-bottom:1px solid var(--line-strong)}
tr.subhead td{background:var(--surface);position:sticky;left:0;z-index:1}
.tbl tbody th{position:sticky;left:0;background:var(--surface);z-index:1;font-weight:400;
  color:var(--ink-2);text-align:left}
.base-tag{display:inline-block;margin-left:7px;font-size:11.5px;letter-spacing:.11em;padding:2px 5px;
  border-radius:var(--radius-sm);background:var(--accent);color:var(--accent-ink);vertical-align:middle}
tr.cat th,tr.cat td{font-weight:700;color:var(--ink);background:var(--cat-bg);
  border-top:1px solid var(--line-strong)}
tr.asset th{padding-left:32px}
tr.asset td,tr.asset th{color:var(--ink-2)}
tr.total th,tr.total td{font-weight:800;color:var(--ink);background:var(--cat-bg);
  border-top:2px solid var(--line-strong);border-bottom:2px solid var(--line-strong)}
tr.metric th,tr.metric td{font-weight:700;color:var(--ink);background:var(--metric-bg)}
tr.band th{background:var(--band-bg);color:var(--band-ink);font-size:11.5px;letter-spacing:.18em;
  text-transform:uppercase;font-weight:800;padding:12px 14px;position:sticky;left:0}
.tbl tbody tr:not(.band):hover td,.tbl tbody tr:not(.band):hover th{background:var(--row-hover)}
.neg{color:var(--neg)}
.span2{border-left:1px solid var(--line)}
.legend-note{margin-top:12px;font-size:13px;color:var(--ink-3);display:flex;gap:18px;flex-wrap:wrap}
/* host convention: #alertArea carries API errors */
#alertArea:empty{display:none}
.alert{padding:11px 15px;border-radius:var(--radius-sm);margin:0 0 16px;font-size:14px;border:1px solid}
.alert-error{background:#FDE8E8;border-color:#F1B8B2;color:#9B1C1C}
.alert-warning{background:#FEF3C7;border-color:#F3D48A;color:#B45309}
.alert-success{background:#E7F4EC;border-color:#B7E0C5;color:#176A33}

@media (max-width:900px){.tbl{font-size:14px;min-width:740px}}
@media (max-width:560px){
  .shell{padding-bottom:60px}
  .fieldgrid{grid-template-columns:1fr}
  .btn{width:100%;text-align:center}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""

from themes import THEMES, DEFAULTS
from pickers import SHARED_CSS, CORE_JS, OPT_JS, PICKERS
from implementation import IMPL_CSS, IMPL_JS

# ── state chrome: loading, empty, error and responsive additions ─────────────
# The surfaces the HTTP flip introduced (spec 10): skeleton cells, the failed
# column, the choose-a-base panel, the basis rebuild confirmation, the schema
# failure card, the describe() footer, and the popover's bottom-sheet form.
STATE_CSS = r"""
main{display:block}
.skip-link{position:absolute;left:-9999px;top:0;z-index:200;background:var(--accent);
  color:#fff;padding:9px 16px;border-radius:0 0 6px 0;font-family:var(--f-num);font-size:13px}
.skip-link:focus{left:0}

/* skeleton cells (spec 10.1): a 60%-width shimmer bar; static tint under
   reduced motion */
.skel{display:inline-block;width:60%;min-width:42px;height:12px;border-radius:3px;
  background:linear-gradient(90deg,var(--line) 25%,var(--surface-2) 42%,var(--line) 60%);
  background-size:200% 100%;animation:skel-sweep 1.1s linear infinite}
@keyframes skel-sweep{from{background-position:200% 0}to{background-position:-200% 0}}
@media (prefers-reduced-motion:reduce){.skel{animation:none;background:var(--line)}}
.col-ellipsis{display:inline-block;margin-left:5px;animation:skel-blink 1.2s steps(4,end) infinite}
@keyframes skel-blink{0%{opacity:.2}50%{opacity:1}100%{opacity:.2}}
@media (prefers-reduced-motion:reduce){.col-ellipsis{animation:none}}

/* the failed column (spec 10.3): Failed chip in the header, Retry beneath it */
.tbl thead th .bdg{margin-left:7px;vertical-align:middle}
.col-retry{display:block;margin:4px 0 0 auto;font:inherit;font-family:var(--f-num);
  font-size:11.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
  color:var(--accent);background:none;border:1px solid var(--line-strong);border-radius:3px;
  padding:2px 8px;cursor:pointer}
.col-retry:hover{border-color:var(--accent)}

/* choose-a-base / cannot-render panels (spec 10.2, 10.3) */
.doc-empty{background:var(--surface);border:1px dashed var(--line-strong);
  border-radius:var(--radius);padding:44px 30px;text-align:center;
  margin:0 0 clamp(26px,4vw,50px)}
.doc-empty h3{margin:0 0 8px;font-family:var(--f-display);font-size:19px;color:#16243A}
.doc-empty p{margin:0 auto;max-width:46ch;color:var(--ink-2);font-size:14.5px}
.doc-empty .btn{margin-top:16px}
.doc-empty-detail{margin-top:8px;color:var(--ink-3);font-size:13px}

/* the Base tier's ring while no base exists (spec 10.2). The spec names the
   accent for it; on the navy rail the accessible focus blue is the same
   adjustment section 6.2 makes for the rail button. */
.tier-ring{outline:2px solid var(--rail-focus,#8FB4FF);outline-offset:-2px}

/* basis rebuild confirmation (spec 11.4) */
.basis-confirm{margin-top:10px;background:var(--rail-input-bg,#1D2F4B);
  border:1px solid var(--rail-input-border,#52739C);border-radius:5px;padding:10px 12px}
.basis-confirm p{margin:0 0 9px;font-size:13.5px;color:var(--rail-ink,#fff)}
.basis-confirm div{display:flex;gap:8px}
.basis-confirm .btn{padding:7px 12px;font-size:12.5px;flex:1}

/* built-list status notes */
.built-status{font-size:11.5px;color:var(--rail-ink-3);margin-left:6px}
.built-status.err{color:#F3A9A2}

/* describe() footer */
.doc-foot{margin:26px 0 0;font-family:var(--f-num);font-size:12px;letter-spacing:.04em;
  color:var(--ink-3)}

/* popover checkboxes sit on a light ground; the .chk defaults are rail-scoped */
.pop .chk{margin:0 0 9px}
.pop .chk label{color:var(--ink-2)}
.pop .chk input{background:var(--surface);border:1px solid var(--line-strong)}
.pop .chk input:checked{background:var(--accent);border-color:var(--accent)}
.pop .chk input:focus-visible{outline-color:var(--accent)}
.pop .chk-note{color:var(--ink-3);margin-left:25px}
.pop-added{color:#176A33}

/* below 1040px the popover becomes a bottom sheet, and the small controls
   grow to touch size (spec 12) */
@media (max-width:1039px){
  .pop.sheet{left:0!important;right:0;bottom:0;top:auto!important;width:auto;
    border-radius:12px 12px 0 0;box-shadow:0 -8px 30px rgba(16,24,40,.25)}
  .plus{width:44px;height:44px}
  .pop-close{width:44px;height:44px;display:flex;align-items:center;justify-content:center}
}

/* keyboard users can scroll the wide tables (spec 13.2) */
.tblwrap:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

/* sleeve tier: the auto-attached row and the per-select retry */
.sl-auto{margin:0;font-size:13.5px;color:var(--rail-ink);
  background:var(--rail-input-bg,#1D2F4B);border:1px solid var(--rail-input-border,#52739C);
  border-radius:4px;padding:8px 11px}
.sl-retry{font:inherit;font-size:12px;color:var(--rail-focus,#8FB4FF);background:none;
  border:1px solid var(--rail-input-border,#52739C);border-radius:4px;padding:4px 8px;
  cursor:pointer;justify-self:start}

/* Signed figures. These need the extra class to outrank `tr.asset td`, which
   sets the muted row colour and is more specific than a bare .neg - which is
   why spec 9.2's "negative figures take --red" had never actually rendered.
   Only the stress rows carry these classes: a Value at Risk block is losses
   by definition, so painting all of it red says nothing. */
.tbl td.neg{color:var(--neg,#B42318)}
.tbl td.pos{color:#1A7F37}

/* The measure blocks - stress periods, VaR, CVaR, probability of loss - are
   long runs of single figures and read better tight. The factor analytics
   rows above them keep the standard height. */
.tbl.risk tbody tr.asset th,.tbl.risk tbody tr.asset td{padding-top:1px;padding-bottom:1px}
.tbl.risk tbody tr.subhead th{padding-top:2px;padding-bottom:2px}

/* Remove a comparison from its own column header. The base has none: it
   cannot be removed, only changed (spec 2.7). */
.tbl thead th.num{position:relative;padding-right:26px}
.col-rm{position:absolute;top:2px;right:3px;width:22px;height:22px;padding:0;
  display:flex;align-items:center;justify-content:center;border:0;border-radius:3px;
  background:none;color:var(--ink-3);font-family:var(--f-num);font-size:15px;
  line-height:1;cursor:pointer}
.col-rm:hover{color:var(--neg,#B42318);background:var(--surface-2)}
.col-rm:focus-visible{outline:2px solid var(--accent);outline-offset:1px}

/* Collapsing the rail. It folds to a strip that still carries the control, so
   the way back is always in view, and the document reclaims the width. */
.rail-brand{position:relative;padding-right:44px}
/* --rail-input-border is the value spec 6.2 substituted for the house one
   precisely because it clears 3:1 against the navy; the rail's own divider
   colour would leave this control invisible. */
.rail-toggle{position:absolute;top:14px;right:8px;width:28px;height:28px;padding:0;
  display:flex;align-items:center;justify-content:center;
  border:1px solid var(--rail-input-border,#52739C);
  border-radius:4px;background:none;color:var(--rail-ink-2);font-size:14px;line-height:1;
  cursor:pointer}
.rail-toggle:hover{color:var(--rail-ink);background:var(--rail-input-bg,#1D2F4B)}
.rail-toggle:focus-visible{outline:2px solid var(--rail-focus,#8FB4FF);outline-offset:2px}
body.rail-collapsed .rail{width:var(--rail-w-collapsed,48px)}
body.rail-collapsed .rail-tiers{display:none}
body.rail-collapsed .rail-brand{padding:14px 0 12px;border-bottom:none}
body.rail-collapsed .rail-brand b,body.rail-collapsed .rail-brand span{display:none}
body.rail-collapsed .rail-toggle{position:static;margin:0 auto}
body.has-rail.rail-collapsed .shell{margin-left:var(--rail-w-collapsed,48px);max-width:none}
@media (max-width:1039px){
  .col-rm{width:44px;height:44px;top:0;right:0}
  .tbl thead th.num{padding-right:46px}
  body.rail-collapsed .rail{width:auto}
  body.has-rail.rail-collapsed .shell{margin-left:0}
}

.alert-info{background:#E3EBFA;border-color:#B9CDF0;color:#1E4FA3}
.impl-foot .bdg{margin-left:10px}
"""


# ---------------------------------------------------------------- assembly --
def vars_css(v):
    rows = ["  --{}:{};".format(k, val) for k, val in DEFAULTS.items()]
    rows += ["  --{}:{};".format(k, val) for k, val in v.items()]
    rows.append("  --caret:{};".format(caret(v.get("ink-3", v.get("ink", "#555")))))
    if v.get("rail-caret-color"):
        rows.append("  --rail-caret:{};".format(caret(v["rail-caret-color"])))
    return ":root{\n" + "\n".join(rows) + "\n}"

RAIL_CSS = """
.rail{position:fixed;top:0;left:0;bottom:0;width:var(--rail-w);background:var(--rail-bg);
  border-right:1px solid var(--rail-line-strong);overflow-y:auto;z-index:50;
  display:flex;flex-direction:column}
.rail-brand{padding:18px 20px 14px;border-bottom:1px solid var(--rail-line);position:sticky;top:0;
  background:var(--rail-bg);z-index:2}
.rail-brand b{display:block;font-family:var(--f-display);font-size:17px;color:var(--rail-ink);
  font-weight:var(--brand-weight,700);letter-spacing:-.01em}
.rail-brand span{display:block;margin-top:4px;font-size:11.5px;letter-spacing:.15em;
  text-transform:uppercase;color:var(--rail-ink-3);font-family:var(--f-num)}
.rail-body{padding:16px 20px 30px;flex:1}
.rail-grp{font-family:var(--f-num);font-size:11px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--rail-ink-3);font-weight:700;margin:0 0 4px}
.rail-note{font-size:13px;color:var(--rail-ink-2);margin:0 0 16px;line-height:1.45}
.rail .field label{color:var(--rail-ink-2)}
.rail .grp-hint,.rail .field-note,.rail .ta-hint{color:var(--rail-ink-3)}
.rail .grp-idx{color:var(--rail-accent,var(--accent))}
.rail .grp+.grp{border-top-color:var(--rail-line)}
.rail input[type=text],.rail select{background-color:var(--rail-input-bg,var(--input-bg));
  border-color:var(--rail-input-border,var(--line-strong));color:var(--rail-ink)}
.rail select{background-image:var(--rail-caret,var(--caret));background-repeat:no-repeat;
  background-position:right 11px center}
.rail input[type=text]:hover,.rail select:hover{border-color:var(--rail-accent,var(--accent))}
.rail input:focus-visible,.rail select:focus-visible,.rail .btn:focus-visible{
  outline-color:var(--rail-focus,var(--accent))}
.rail .toggle{background-color:var(--rail-input-bg,var(--surface-2));
  border-color:var(--rail-input-border,var(--line-strong))}
.rail .knob{background:var(--rail-ink-3)}
.rail .btn-primary{background:var(--rail-accent,var(--accent));
  border-color:var(--rail-btn-border,var(--rail-accent,var(--accent)));
  color:var(--rail-accent-ink,var(--accent-ink))}
.rail .btn-ghost{color:var(--rail-ink-2);border-color:var(--rail-input-border,var(--line-strong))}
.rail .btn-ghost:hover{color:var(--rail-ink);border-color:var(--rail-accent,var(--accent))}
.rail .fieldgrid{grid-template-columns:1fr;gap:12px}
.rail .scenario{gap:18px}
.rail legend{margin-bottom:10px}
.rail .formactions{margin-top:2px}
.rail .formactions .btn{width:100%;text-align:center}
.rail-foot{padding:14px 20px;border-top:1px solid var(--rail-line);font-family:var(--f-num);
  font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--rail-ink-3)}
body.has-rail .shell{margin-left:var(--rail-w);max-width:1320px}
@media (max-width:1040px){
  .rail{position:static;width:auto;border-right:none;border-bottom:1px solid var(--rail-line-strong)}
  .rail-brand{position:static}
  .rail .fieldgrid{grid-template-columns:repeat(auto-fit,minmax(205px,1fr))}
  .rail .formactions .btn{width:auto}
  body.has-rail .shell{margin-left:0}
}
"""

PAGE_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{blurb}">
<link rel="stylesheet" href="static/css/{slug}.css">
</head>
<body class="has-rail">
<!-- Host convention: accessGate.js first so the sign-in overlay can render,
     then globals.js which sets API_BASE. Absolute paths, served by the host's
     Flask process; opened from file:// they 404 harmlessly and the page's own
     typeof guard keeps API_BASE working. -->
<script src="/static/js/accessGate.js"></script>
<script src="/static/js/globals.js"></script>
<a class="skip-link" href="#main">Skip to content</a>
<!-- Live regions: present from first paint, or they are never announced. -->
<div id="live-polite" class="sr-only" aria-live="polite"></div>
<div id="live-assertive" class="sr-only" aria-live="assertive"></div>
<aside class="rail" aria-label="Scenario">
  <div class="rail-brand">
    <b>Portfolio Management Group</b>
    <span>Proposal Tool</span>
    <button type="button" class="rail-toggle" id="railtoggle" aria-expanded="true"
            aria-controls="rail-tiers" aria-label="Collapse the scenario panel"
            title="Collapse the scenario panel"><span aria-hidden="true">&#171;</span></button>
  </div>
  <div class="rail-tiers" id="rail-tiers">
    <div class="tier" id="tier-mandate"></div>
    <div class="tier" id="tier-basis"></div>
    <div class="tier" id="tier-base"></div>

    <div class="tier" id="tier-sleeves" hidden></div>

    <div class="tier" id="tier-comparisons">
      <div class="tier-h"><h3>Comparisons</h3>
        <span class="tier-count" id="count">0 of 3</span></div>
{pickerhtml}
{actions}
      <div class="built" id="built"></div>
    </div>
  </div>
</aside>

<main class="shell" id="main">
  <!-- Host convention: every page surfaces API errors here. -->
  <div id="alertArea"></div>

  <!-- Schema failure: the page cannot render without field options, so this
       replaces everything until Retry succeeds (spec 10.3). -->
  <section id="view-schema-error" hidden>
    <div class="doc-empty">
      <h3>The Proposal Tool could not load</h3>
      <p>The scenario schema is unavailable, and every control depends on it.</p>
      <p class="doc-empty-detail" id="schema-error-reason"></p>
      <button type="button" class="btn btn-primary" id="schema-retry">Retry</button>
    </div>
  </section>

  <!-- Landing. The page is its own front door. The hero is not decoration: it is a
       real output of the tool - three allocations compared, drawn with the same
       category colours the allocation table uses. -->
  <section id="view-landing">
    <div class="lp-inner">
      <div class="lp-rule"></div>
      <div class="lp-copy">
        <p class="lp-eyebrow">Goldman Sachs &middot; Private Wealth Management</p>
        <h1 class="lp-title"><span>Portfolio Management Group</span><span class="lp-accent">Proposal Tool</span></h1>
        <div class="lp-spec" aria-hidden="true">
          <span style="background:var(--cat-1)"></span>
          <span style="background:var(--cat-2)"></span>
          <span style="background:var(--cat-3)"></span>
          <span style="background:var(--cat-4)"></span>
          <span style="background:var(--cat-5)"></span>
          <span style="background:var(--cat-6)"></span>
          <span style="background:var(--cat-7)"></span>
        </div>
      </div>
      <div class="lp-grid">
        <div>
          <p class="lp-lede">Build a model portfolio from a client mandate. Compare it against up to
            three alternatives, then attach sleeves of investible products to produce the
            implementation model.</p>
          <button type="button" class="btn btn-primary lp-cta" id="startbtn">Start Here</button>
        </div>
        <ul class="lp-need">
          <li><b>Mandate</b>Top account size and mandate size</li>
          <li><b>Adviser</b>The Primary PWA for the mandate</li>
          <li><b>Minimum</b>A mandate of at least $5,000,000</li>
        </ul>
      </div>
    </div>
  </section>

  <div class="steps" id="steps" role="tablist" aria-label="Scenario steps">
    <button type="button" class="step" data-step="aa" role="tab" aria-selected="true">
      <span class="sn">1</span>Asset allocation</button>
    <button type="button" class="step" data-step="impl" role="tab" aria-selected="false">
      <span class="sn">2</span>Implementation</button>
  </div>

<div id="view-aa">
{chrome}

  <!-- Mandate set, no base chosen yet: the rail's Base tier carries a ring
       and this panel says what to do (spec 10.2). -->
  <div class="doc-empty" id="doc-empty" hidden>
    <h3>Choose a base portfolio</h3>
    <p>Pick an allocation and risk level in the rail to build the first column.</p>
  </div>

  <section class="sec">
    <div class="sec-head">
      <h2>Allocation</h2>
    </div>
    <div class="tblwrap" tabindex="0" aria-label="Allocation table, scrolls horizontally">
      <table class="tbl alloc" id="alloc"></table></div>
    <p class="legend-note"><span>Base portfolio pinned to the first column</span>
      <span>Maximum of four portfolios</span></p>
  </section>

  <section class="sec viz">
    <div class="sec-head">
      <h2>At a glance</h2>
      <span class="sec-note">Summarises the table above. Exact values stay in the tables \u2014
        these are for shape and position, not for reading numbers off.</span>
    </div>
    <div class="viz-grid">
      <div class="viz-card">
        <h3>Allocation by category</h3>
        <p class="sub">Share of each portfolio, 0 to 100%.</p>
        <div class="viz-legend" id="viz-key"></div>
        <div id="viz-comp"></div>
      </div>
      <div class="viz-card">
        <h3>Return against volatility</h3>
        <p class="sub">Where each portfolio sits on the risk and return plane.</p>
        <div class="viz-legend" id="viz-key2"></div>
        <div id="viz-rr"></div>
      </div>
    </div>
  </section>

  <section class="sec">
    <div class="sec-head">
      <h2>Risk dashboard</h2>
      <span class="sec-note">Three sections. Factor analytics span both sub-columns; stress periods
        and risk premia split into Nominal and Real.</span>
    </div>
    <div class="tblwrap" tabindex="0" aria-label="Risk dashboard, scrolls horizontally">
      <table class="tbl risk" id="risk"></table></div>
  </section>
{postamble}
</div>

<div id="view-impl-wrap" hidden>
  <section class="sec" id="view-impl"></section>
</div>

  <!-- describe(): data version, source, as-of - for support and
       reproducibility (spec 4, the port's describe method). -->
  <p class="doc-foot" id="doc-foot" hidden></p>
</main>
<!-- Page JS last so every node it touches already exists. No module system in the
     host, so load order is load-bearing: this file defines App, then the picker and
     implementation layers attach to it. -->
<!-- Mandate dialog, rendered on demand. -->
<div id="mandateDialog" hidden></div>

<script src="static/js/{slug}.js"></script>
</body>
</html>
"""

HOST_PRELUDE = """\
'use strict';
// ---------------------------------------------------------------------------
// Host integration seam.
//
// The Flask frontend reverse-proxies /api/* to the FastAPI backend, rewriting
// /api/<x> to /api/v1/<x>. On a host page globals.js has already set API_BASE;
// this guard keeps the page working when opened standalone.
// ---------------------------------------------------------------------------
if (typeof API_BASE === 'undefined') {
    window.API_BASE = window.location.origin + '/api';
}

/** Render an API error into the page's #alertArea. Host convention. */
function showAlert(kind, message) {
    var area = document.getElementById('alertArea');
    if (!area) return;
    var div = document.createElement('div');
    div.className = 'alert alert-' + kind;
    div.textContent = message;
    area.replaceChildren(div);
}

function clearAlert() {
    var area = document.getElementById('alertArea');
    if (area) area.replaceChildren();
}

/**
 * Single entry point for every backend call.
 *
 * Follows the host's error contract: a non-2xx body carries `error`, and a 401
 * carries `loginUrl` which we follow so GSSSO can re-authenticate the browser.
 * Nothing in the prototype calls this yet - the data is generated client-side -
 * but this is the seam the adapter plugs into.
 */
async function apiFetch(path, opts) {
    // read off window rather than the bare global: identical in a browser,
    // and it does not depend on window === globalThis
    var resp = await fetch(window.API_BASE + path,
                           Object.assign({credentials: 'same-origin'}, opts || {}));
    if (!resp.ok) {
        var msg = 'Request failed (' + resp.status + ')';
        var body = null;
        try {
            body = await resp.json();
            if (body && body.loginUrl) { window.location = body.loginUrl; return; }
            if (body && body.error) msg = body.error;
        } catch (e) { /* non-JSON error body */ }
        var err = new Error(msg);
        err.status = resp.status;   // callers map 422 field errors and 404s
        err.body = body;
        throw err;
    }
    return resp.json();
}
"""

BOOT_JS = """
// Bootstrap. The script tag sits at the end of <body>, so the DOM is parsed by
// the time this runs; the readyState guard covers a deferred load anyway.
// App.boot() paints the landing at once, fetches the schema, and rehydrates
// from a ?scenario= id when one is present (spec 11.1, 11.8).
(function () {
    function boot() { App.boot(); }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
"""

SLUG = "proposalTool"              # folder, html, css and js all share this name


def build_assets(th):
    """Return (html, css, js) for the page, as three separate payloads.

    The host has no bundler and no module system, so the JS is one concatenated
    file whose internal order is load-bearing: the core defines App, then the
    picker and implementation layers attach to it.
    """
    picker = PICKERS.get(th.get("picker", "table"), {})
    css = "\n".join([
        vars_css(th["v"]), BASE_CSS, RAIL_CSS, SHARED_CSS, STATE_CSS,
        picker.get("css", ""),
        IMPL_CSS if th.get("implementation") else "",
        th["extra"],
    ])
    js = "\n".join([
        HOST_PRELUDE, CORE_JS, OPT_JS,
        picker.get("js", ""),
        IMPL_JS if th.get("implementation") else "",
        BOOT_JS,
    ])
    html = PAGE_SHELL.format(
        title=th["name"], blurb=th["blurb"], slug=SLUG,
        chrome=th.get("chrome", ""), postamble=th.get("postamble", ""),
        pickerhtml=picker.get("html", ""),
        actions=picker.get("actions", ""),
    )
    return html, css, js


def _write_page(root, html, css, js):
    """Write one complete page folder (html, css, js, fonts) under *root*."""
    for sub in ("static/css", "static/js", "static/fonts"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)

    with open(os.path.join(root, SLUG + ".html"), "w", encoding="utf-8") as fh:
        fh.write(html)
    with open(os.path.join(root, "static", "css", SLUG + ".css"), "w", encoding="utf-8") as fh:
        fh.write(css)
    with open(os.path.join(root, "static", "js", SLUG + ".js"), "w", encoding="utf-8") as fh:
        fh.write(js)

    # fonts travel with the page: the CSS references them as ../fonts/*
    src = os.path.join(ROOT, "fonts")
    if os.path.isdir(src):
        for name in sorted(os.listdir(src)):
            if name.endswith(".woff2"):
                shutil.copyfile(os.path.join(src, name),
                                os.path.join(root, "static", "fonts", name))


def main():
    th = THEMES[0]
    html, css, js = build_assets(th)

    # the generator sits in generator/; the page folder is its sibling, so that
    # PORTING.md's "copy the folder" is literal
    root = os.path.join(os.path.dirname(ROOT), SLUG)
    _write_page(root, html, css, js)

    # the service mirror serves the same folder from its dashboard directory,
    # exactly where the host will; writing it too makes every build a rehearsal
    # of PORTING.md's "copy the folder" step
    mirror = os.path.join(os.path.dirname(ROOT), "service", "cyrus_pmg",
                          "dashboard", SLUG)
    if os.path.isdir(os.path.dirname(mirror)):
        _write_page(mirror, html, css, js)

    print("Wrote {}/ (+ service mirror) - html {:,}  css {:,}  js {:,} bytes".format(
        SLUG, len(html), len(css), len(js)))


if __name__ == "__main__":
    main()
