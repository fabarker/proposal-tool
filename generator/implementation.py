# -*- coding: utf-8 -*-
"""Implementation step: attach PMG-authored sleeves of products to each category.

A sleeve is a fixed block whose product weights sum to 1. The category's weight in
the asset allocation is distributed proportionally across those products. Only the
BASE portfolio is implemented.

    all-in per product  = product cost + GS management fee   (additive)
    weighted fee (bp)   = all-in % x weight %
    portfolio total fee = sum of weighted fees
"""

IMPL_CSS = r"""
/* ── step nav ── */
.steps{display:flex;gap:2px;margin:0 0 clamp(20px,3vw,28px);border-bottom:1px solid var(--line-strong)}
.step{appearance:none;background:none;border:0;border-bottom:2px solid transparent;
  font:inherit;font-family:var(--f-num);font-size:14.5px;font-weight:600;color:var(--ink-2);
  padding:9px 16px;cursor:pointer;margin-bottom:-1px;display:flex;align-items:center;gap:8px}
.step:hover{color:var(--ink)}
.step[aria-selected="true"]{color:#16243A;border-bottom-color:var(--accent)}
.step:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.step .sn{font-family:var(--f-num);font-size:11.5px;color:var(--ink-3);font-weight:700}
.step[aria-selected="true"] .sn{color:var(--accent)}
.step:disabled{opacity:.45;cursor:not-allowed}

/* ── sleeve pickers in the rail ── */
.sl-list{display:grid;gap:9px}
.sl-row{display:grid;gap:5px}
.sl-row .cat{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.sl-row .cat b{font-size:13px;font-weight:600;color:var(--rail-ink-2);line-height:1.3}
.sl-row .cat span{font-family:var(--f-num);font-size:12px;color:var(--rail-ink-3);
  font-variant-numeric:tabular-nums;flex-shrink:0}
.sl-row.done .cat b{color:var(--rail-ink)}
.sl-progress{margin:12px 0 0;font-size:13px;color:var(--rail-ink-2)}
.sl-bar{height:4px;border-radius:2px;background:#22334E;overflow:hidden;margin:6px 0 0}
.sl-bar i{display:block;height:100%;background:var(--rail-accent);transition:width .2s}
@media (prefers-reduced-motion:reduce){.sl-bar i{transition:none}}

/* ── implementation table ── */
.impl-head{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;
  flex-wrap:wrap;margin:0 0 14px}
.impl-foot{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:16px 0 0}
.impl-gate{margin:0;font-size:14px;color:var(--ink-2)}
.tbl.impl{min-width:1690px}
.tbl.impl td.txt{white-space:nowrap}
/* both identity columns stay pinned while the attribute and money columns scroll */
/* Column 1 sizes to its longest label rather than a fixed 248px. Column 2 is
   pinned immediately after it, so its sticky offset has to follow: the
   implementation renderer measures column 1 after each paint and publishes
   the width as --impl-c1. The 248px fallback is the spec 9.3 value, used
   until the first measurement lands. */
.tbl.impl .rowhead{width:1%}
.tbl.impl .prodcol{position:sticky;left:var(--impl-c1,248px);z-index:1;width:1%;
  border-right:1px solid var(--line-strong)}
.tbl.impl thead .prodcol{z-index:4;background:var(--head-bg)}
.tbl.impl tr.asset .prodcol{background:var(--surface);color:var(--ink)}
.tbl.impl tr.asset.alt .prodcol{background:#E8F2E6}
.tbl.impl tr.cat .prodcol{background:#E9EDF2}
.tbl.impl tr.grand .prodcol{background:#16243A}
.tbl.impl tbody tr:hover .prodcol{background:var(--row-hover)}
.tbl.impl td.txt,.tbl.impl th.txt{text-align:left}
.tbl.impl .tick{font-family:var(--f-num);color:var(--ink-2);letter-spacing:.02em}
.tbl.impl tr.cat td.num{font-weight:700}
.tbl.impl tr.grand th,.tbl.impl tr.grand td{background:#16243A;color:#fff;font-weight:700;
  border-top:2px solid #16243A}
.tbl.impl tr.grand th{background:#16243A}
.pill{display:inline-block;font-family:var(--f-num);font-size:11px;font-weight:700;
  letter-spacing:.04em;padding:1px 7px;border-radius:9px;white-space:nowrap}
.p-act{background:#E3EBFA;color:#1E4FA3;border:1px solid #B9CDF0}
.p-pas{background:#EEF1F4;color:#5B6B7C;border:1px solid #D8DEE6}
.p-sma{background:#E7F4EC;color:#176A33;border:1px solid #B7E0C5}
.p-etf{background:#FEF3C7;color:#B45309;border:1px solid #F3D48A}
.p-mf{background:#F3EDF9;color:#5B4380;border:1px solid #D9CCEA}
.p-int{background:#E8EEF5;color:#1E4FA3;border:1px solid #C4D5E8}
.p-ext{background:#F5F1E8;color:#7A5C2E;border:1px solid #E0D3BC}
.p-sleeve{background:#EEF1F4;color:#5B6B7C;border:1px solid #D8DEE6}
.impl-empty{background:var(--surface);border:1px dashed var(--line-strong);border-radius:var(--radius);
  padding:clamp(28px,5vw,52px);text-align:center}
.impl-empty h3{margin:0 0 7px;font-size:18.5px;color:#16243A;font-family:var(--f-display)}
.impl-empty p{margin:0 auto;max-width:44ch;font-size:15px;color:var(--ink-2)}
.impl-note{font-size:13px;color:var(--ink-3);margin:11px 0 0;max-width:76ch}
"""

# ── sleeve library ─────────────────────────────────────────────────────────────
# name, [(product, ticker, style, type, cost%, mgmt fee%, weight in sleeve)]
import os

def _asset(name):
    """Read a JS payload from generator/js/ (see pickers._asset)."""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, 'js', name), encoding='utf-8') as fh:
        return fh.read()

IMPL_JS = _asset('implementation.js')
