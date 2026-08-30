# -*- coding: utf-8 -*-
"""Theme definitions for the Portfolio Analytics UI.

Two directions remain. The exploration set that produced them (33 directions across
two rounds) has been retired now that the design is settled.

  index.html — both steps behind a step nav

It carries the same tokens: Porcelain restraint on the mpo-ui :root palette, with a
navy shellnav rail. Two house values are adjusted for contrast and documented in the
specification (rail input border, rail primary button).
"""

DEFAULTS = {'radius': '4px', 'radius-sm': '3px', 'shadow': 'none', 'cell-pad': '9px 14px', 'surface-2': 'var(--surface)', 'input-bg': 'var(--surface-2)', 'ink-2': 'var(--ink)', 'ink-3': 'var(--ink-2)', 'line-strong': 'var(--line)', 'cat-bg': 'transparent', 'metric-bg': 'transparent', 'row-hover': 'transparent', 'head-bg': 'var(--surface)', 'head-ink': 'var(--ink)', 'band-bg': 'var(--ink)', 'band-ink': 'var(--surface)', 'accent-ink': '#ffffff', 'neg': '#B3261E', 'panel-border': '1px solid var(--line)', 'rail-w': '320px', 'rail-bg': 'var(--surface)', 'rail-ink': 'var(--ink)', 'rail-ink-2': 'var(--ink-2)', 'rail-ink-3': 'var(--ink-3)', 'rail-line': 'var(--line)', 'rail-line-strong': 'var(--line-strong)'}

SANS = "system-ui,-apple-system,'Segoe UI',sans-serif"

def T(id, name, blurb, mood, gf, v, extra=""):
    return dict(id=id, name=name, blurb=blurb, mood=mood, gf=gf, v=v, extra=extra)

# ── shared tokens and chrome ─────────────────────────────────────────────────
_GF     = ''
_TOKENS = {'bg': '#ECEEF1', 'surface': '#FFFFFF', 'surface-2': '#F0F2F5', 'ink': '#1C2733', 'ink-2': '#5B6B7C', 'ink-3': '#8494A4', 'line': '#EEF1F4', 'line-strong': '#D4DAE2', 'accent': '#1F5FBF', 'accent-ink': '#FFFFFF', 'neg': '#B42318', 'radius': '6px', 'radius-sm': '4px', 'shadow': '0 1px 2px rgba(16,24,40,.05)', 'cell-pad': '10px 15px', 'head-bg': '#FFFFFF', 'head-ink': '#5B6B7C', 'band-bg': '#FFFFFF', 'band-ink': '#1F5FBF', 'cat-bg': '#E9EDF2', 'metric-bg': '#F0F2F5', 'row-hover': '#F7F9FB', 'brand-weight': '400', 'f-display': "'GS Sans','Roboto',system-ui,-apple-system,sans-serif", 'f-body': "'GS Sans','Roboto',system-ui,-apple-system,sans-serif", 'f-num': "'GS Sans Condensed','GS Sans','Roboto',system-ui,-apple-system,sans-serif", 'rail-w': '352px', 'rail-bg': '#16243A', 'rail-ink': '#FFFFFF', 'rail-ink-2': '#B6C2D2', 'rail-ink-3': '#7E90A8', 'rail-line': '#233754', 'rail-line-strong': '#233754', 'rail-input-bg': '#1D2F4B', 'rail-input-border': '#52739C', 'rail-accent': '#2A6AD0', 'rail-accent-ink': '#FFFFFF', 'rail-btn-border': '#5590E4', 'rail-focus': '#8FB4FF', 'rail-caret-color': '#8FA2BC'}
_EXTRA  = '.tbl thead th{border-bottom:1px solid var(--ink)}tr.band th{border-top:1px solid var(--accent);letter-spacing:.2em}.tblwrap{background:var(--surface);border:1px solid var(--line-strong)}tr.cat th,tr.cat td{border-top:none;border-bottom:1px solid var(--line-strong)}tr.total th,tr.total td{background:#F0F2F5}.topbar{padding-top:clamp(18px,2.4vw,26px)}.topbar .brand-mark{font-size:22px;color:#16243A}.topbar .brand-sub{color:var(--ink-2);text-transform:none;letter-spacing:.02em;font-size:14px}.topbar .topbar-meta{background:#E3EBFA;color:#1E4FA3;border:1px solid #B9CDF0;border-radius:9px;padding:2px 9px;font-weight:700;text-transform:none;letter-spacing:.03em}h2{color:#16243A}.base-tag{background:#E3EBFA;color:#1E4FA3;border:1px solid #B9CDF0;border-radius:9px;font-size:11.5px;font-weight:700;padding:1px 7px;letter-spacing:.03em}.rail-brand{border-bottom:1px solid #233754}.brand-mark,.rail-brand b{font-family:\"Goldman Sans\",\"GS Sans\",\"Roboto\",system-ui,sans-serif;font-weight:400;letter-spacing:.005em}.rail .grp-idx{letter-spacing:.18em}.rail .ta-hint{color:#7E90A8}.rail .is-disabled{opacity:.42}.bdg{display:inline-block;font-size:12px;font-weight:700;padding:1px 8px;border-radius:9px;letter-spacing:.03em;white-space:nowrap}.b-ok{background:#E7F4EC;color:#176A33;border:1px solid #B7E0C5}.b-warn{background:#FEF3C7;color:#B45309;border:1px solid #F3D48A}.b-breach{background:#FDE8E8;color:#9B1C1C;border:1px solid #F1B8B2}.b-bind{background:#E3EBFA;color:#1E4FA3;border:1px solid #B9CDF0}.b-slack{background:#EEF1F4;color:#5B6B7C;border:1px solid #D8DEE6}.notices{display:flex;gap:9px;flex-wrap:wrap;align-items:center;margin:0 0 clamp(20px,3vw,30px)}.spec{margin:8px 0 0;background:#FFFBEB;border:1.5px dashed #D4A017;border-radius:6px;padding:13px 18px 15px;font-size:15px;color:#4A3B10}.spec h4{margin:0 0 8px;font-size:12.5px;letter-spacing:.09em;text-transform:uppercase;color:#92600A;font-family:var(--f-num);font-weight:700}.spec p{margin:0 0 9px;line-height:1.5}.spec p:last-child{margin-bottom:0}.spec .legend{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin:10px 0 0}.spec .legend span.lbl{font-size:13px;color:#6B5716}.pos{color:#1A7F37}.neg{color:#B42318}.rail .is-disabled .field-note{color:#E0B25C}'
_CHROME = '  <div class="notices">\n    <span class="bdg b-ok">Lookup matched</span>\n    <span class="bdg b-warn">Real estate excluded &mdash; AA Type is Core</span>\n    <span class="bdg b-bind">3 of 4 portfolios</span>\n  </div>'
_POST   = '  <aside class="spec">\n    <h4>Semantic palette</h4>\n    <p>Status colour is carried by the badge system from <code>app.css</code>, never by text colour\n      alone. Figures use <span class="pos">green</span> for positive and\n      <span class="neg">red</span> for negative, always alongside the sign.</p>\n    <div class="legend">\n      <span class="lbl">States</span>\n      <span class="bdg b-ok">Matched</span>\n      <span class="bdg b-warn">Constrained</span>\n      <span class="bdg b-breach">Below minimum</span>\n      <span class="bdg b-bind">Binding</span>\n      <span class="bdg b-slack">Not applicable</span>\n    </div>\n    <p style="margin-top:11px">Below minimum is the mandate-size hard block: under $5m the\n      scenario cannot be built and the field takes the breach treatment.</p>\n  </aside>'

THEMES = [
 T("index", "Proposal Tool",
   "Both steps in one page. Choose an asset allocation and compare up to four portfolios, then "
   "switch to Implementation and attach a sleeve of products to each category of the base.",
   "Working prototype \u00b7 Light",
   _GF, dict(_TOKENS), _EXTRA),
]

THEMES[0].update(layout="picker", picker="table", chrome=_CHROME, postamble=_POST,
                 implementation=True)
