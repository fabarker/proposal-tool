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
/* ── step nav ──
   The numbered-circle treatment from proposalToolv2: a numeral in a ring, the
   stage name over a one-line description, the two joined by a connector and
   centred over the document. Ported to this page's own tokens rather than
   v2's, so the theme swap of spec 6.5 stays a token change (D32).

   The SEMANTICS are unchanged and deliberately not v2's. v2 marks these
   aria-current="step", which describes a linear wizard; spec 13.2 fixes this
   nav as role="tablist" with aria-selected and arrow-key movement, which is
   what it is - two panels of one workspace, reachable in either order. Only
   the appearance is borrowed.

   .steps keeps display:flex, so the [hidden] !important rule above is still
   what hides it on the landing (spec 15.1). */
.steps{display:flex;align-items:center;justify-content:center;
  margin:0 0 clamp(20px,3vw,28px);padding:12px 0 16px;
  border-bottom:1px solid var(--line-strong)}
.step{appearance:none;background:none;border:0;font:inherit;color:var(--ink-2);
  display:flex;align-items:center;gap:10px;text-align:left;
  padding:9px 13px;border-radius:var(--radius-sm);cursor:pointer}
.step:hover{background:var(--surface-2);color:var(--ink)}
.step:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.step:disabled{opacity:.4;cursor:not-allowed}
.step:disabled:hover{background:none}
/* Once step 2 is reachable it breathes until it has been opened, so the PWA
   is told the next stage has unlocked rather than having to notice. The pulse
   is on the numeral, not the whole tab: a moving block of text is harder to
   read than a moving disc beside it. */
.step-beckon .step-index{animation:stepbeckon 2.4s ease-in-out infinite;
  border-color:var(--accent);color:var(--accent)}
@keyframes stepbeckon{
  0%,100%{box-shadow:0 0 0 0 rgba(31,95,191,.34)}
  50%{box-shadow:0 0 0 6px rgba(31,95,191,0)}
}
@media (prefers-reduced-motion:reduce){
  .step-beckon .step-index{animation:none;box-shadow:0 0 0 3px rgba(31,95,191,.28)}
}
.step:disabled:hover{background:none}
.step-index{width:30px;height:30px;flex:0 0 auto;display:grid;place-items:center;
  border:1px solid var(--line-strong);border-radius:50%;background:var(--surface);
  font-family:var(--f-num);font-size:12px;font-weight:700;color:var(--ink-3);
  transition:background .15s ease,color .15s ease,border-color .15s ease}
.step-label{display:grid}
.step-label strong{font-family:var(--f-num);font-size:13.5px;font-weight:600;line-height:1.25}
.step-label small{font-size:11px;color:var(--ink-3);margin-top:1px;line-height:1.25}
.step[aria-selected="true"]{color:#16243A}
.step[aria-selected="true"] .step-index{background:#16243A;color:#fff;border-color:#16243A}
.step[aria-selected="true"] .step-label strong{font-weight:700}
.step-connector{width:80px;height:1px;flex:0 0 auto;background:var(--line-strong);margin:0 10px}
@media (prefers-reduced-motion:reduce){.step-index{transition:none}}
/* Narrow: the descriptions go before the stage names do, and the connector
   shrinks rather than pushing the second stage off the edge. */
@media (max-width:719px){
  .step-connector{width:24px;margin:0 4px}
  .step-label small{display:none}
}

/* ── implementation variant: the first field of step 2 (D29) ──
   Separated from the sleeve list by a rule rather than a heading: it gates
   the list below it, so it reads as its precondition rather than as another
   item in it. */
.vr-field{display:grid;gap:5px;margin:0 0 14px;padding:0 0 14px;
  border-bottom:1px solid var(--rail-line)}
.vr-field label{font-size:13px;font-weight:600;color:var(--rail-ink-2);line-height:1.3}
.vr-field.done label{color:var(--rail-ink)}
.vr-field select{width:100%}
.vr-note{margin:1px 0 0;font-size:12.5px;color:var(--rail-ink-3);line-height:1.45}

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
/* 30px below, matching .sec-head.stage-head - both steps open on the same
   block and must stand off their tables identically. */
.impl-head{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;
  flex-wrap:wrap;margin:0 0 30px}
.impl-gate{margin:0;font-size:14px;color:var(--ink-2)}

/* ── completion progress, from proposalToolv2's .completion-summary ──
   Replaces the pill that only ever said one of two things. Sits opposite the
   stage block, which .impl-head's space-between already positions. */
.completion-summary{min-width:230px}
.completion-line{display:flex;justify-content:space-between;gap:15px;
  font-family:var(--f-num);color:var(--ink-2);font-size:12px;margin-bottom:5px}
.completion-line strong{color:var(--ink);font-variant-numeric:tabular-nums}
.progress-track{height:6px;background:var(--surface-2);border-radius:999px;overflow:hidden}
.progress-track i{display:block;height:100%;width:var(--progress);background:#176A33;
  transition:width .2s ease}
.progress-track.is-done i{background:#176A33}
@media (prefers-reduced-motion:reduce){.progress-track i{transition:none}}

/* ── export card, from proposalToolv2 ──
   Its structure verbatim; its greens, ambers and reds swapped for the ones
   this page's badges already use, so the card reads as part of the document
   rather than as a transplant. */
.impl-viz{padding-bottom:44px;border-bottom:1px solid var(--line-strong)}
.export-card{margin:44px 0 0;padding:24px 26px;display:grid;
  grid-template-columns:60px 1fr auto;gap:20px;align-items:center;
  background:var(--surface);border:1px solid var(--line-strong);
  border-left:4px solid #176A33;border-radius:7px;box-shadow:var(--shadow)}
.export-icon{width:50px;height:58px;display:grid;place-items:center;position:relative;
  background:#E7F4EC;border:1px solid #B7E0C5;border-radius:4px;color:#176A33;
  font-family:var(--f-num);font-size:20px;font-weight:750}
.export-icon:after{content:"";position:absolute;right:0;top:0;border-style:solid;
  border-width:0 0 12px 12px;border-color:transparent transparent #B7E0C5 transparent}
.export-copy h3{margin:0;font-family:var(--f-num);font-size:23px;font-weight:520;
  color:#16243A}
.export-copy>p:not(.eyebrow){margin:4px 0;color:var(--ink-2);font-size:13px}
/* Scoped through .export-copy rather than carrying v2's !important: the
   generic paragraph rule above is (0,2,1) because of its :not(), so a bare
   .export-gate.blocked at (0,2,0) loses the colour it is there to set. */
.export-copy>p.export-gate{font-weight:650}
.export-copy>p.export-gate.ready{color:#176A33}
.export-copy>p.export-gate.blocked{color:#B45309}
.export-copy>p.export-gate.error{color:#9B1C1C}
.btn-export{background:#176A33;color:#fff;border-color:#176A33;min-width:180px}
.btn-export:hover:not(:disabled){background:#12572a;border-color:#12572a}
@media (max-width:719px){
  .export-card{grid-template-columns:1fr;gap:14px}
  .export-icon{display:none}
  .btn-export{width:100%}
}
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
/* The products column bands with the row it is in, not against it - the green
   was the only place on the page using that tint. */
.tbl.impl tr.asset.alt .prodcol{background:var(--surface)}
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
.p-ucits{background:#E4F1F4;color:#1C5A69;border:1px solid #BCDCE4}
.p-icav{background:#EDEBF6;color:#453A7A;border:1px solid #CFC9E6}
.p-sleeve{background:#EEF1F4;color:#5B6B7C;border:1px solid #D8DEE6}
/* Remove control inside the sleeve pill. Sized to stay inside an 11px pill
   without stretching it, and given a real hit area by the negative margin
   rather than by growing the pill. */
.pill-x{appearance:none;border:0;background:none;cursor:pointer;padding:0 0 0 5px;
  margin:0 -2px 0 1px;font:inherit;font-size:13px;line-height:1;color:inherit;
  opacity:.65;vertical-align:-1px}
.pill-x:hover{opacity:1;color:var(--neg,#B42318)}
.pill-x:focus-visible{outline:2px solid var(--accent);outline-offset:1px;border-radius:2px;
  opacity:1}
/* ── composition doughnuts, between the table and the download ── */
.impl-viz{margin:46px 0 0}
.impl-viz-head{margin:0 0 14px}
.impl-viz-head h3{margin:0 0 3px;font-family:var(--f-display);font-size:17px;color:#16243A;
  font-weight:600}
.impl-viz-head .sec-note{margin:0}
/* auto-fit rather than five fixed tracks: the row holds five across on a wide
   document and folds to three, then two, without a breakpoint per step - and
   it keeps working if a sixth attribute is ever added. */
.dn-row{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(196px,1fr))}
.dn-card{background:var(--surface);border:1px solid var(--line-strong);
  border-left:3px solid var(--accent);
  border-radius:var(--radius);padding:14px 15px 12px;min-width:0;
  display:flex;flex-direction:column;align-items:center}
.dn-card h4{margin:0 0 10px;font-family:var(--f-num);font-size:11.5px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink-2);font-weight:700;align-self:flex-start}
.dn{width:118px;height:118px;display:block;transform:rotate(-90deg)}
.dn-arc{transition:stroke-dasharray .25s ease,stroke-dashoffset .25s ease}
@media (prefers-reduced-motion:reduce){.dn-arc{transition:none}}
.dn-key{list-style:none;margin:11px 0 0;padding:0;width:100%;display:grid;gap:3px}
.dn-key li{display:flex;align-items:center;gap:7px;font-size:12.5px;color:var(--ink-2);
  line-height:1.35}
.dn-sw{width:9px;height:9px;border-radius:2px;flex:0 0 auto}
.dn-nm{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dn-pc{font-family:var(--f-num);font-variant-numeric:tabular-nums;color:var(--ink);
  flex:0 0 auto}
.impl-empty{background:var(--surface);border:1px dashed var(--line-strong);border-radius:var(--radius);
  padding:clamp(28px,5vw,52px);text-align:center}
.impl-empty h3{margin:0 0 7px;font-size:18.5px;color:#16243A;font-family:var(--f-display)}
.impl-empty p{margin:0 auto;max-width:44ch;font-size:15px;color:var(--ink-2)}
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
