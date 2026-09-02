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
  margin:0 0 clamp(25px,3.75vw,35px);padding:15px 0 20px;
  border-bottom:1px solid var(--line-strong)}
.step{appearance:none;background:none;border:0;font:inherit;color:var(--ink-2);
  display:flex;align-items:center;gap:12.5px;text-align:left;
  padding:11px 16px;border-radius:var(--radius-sm);cursor:pointer}
.step:hover{background:var(--surface-2);color:var(--ink)}
.step:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.step:disabled{opacity:.4;cursor:not-allowed}
.step:disabled:hover{background:none}
/* Once step 2 is reachable it breathes until it has been opened, so the PWA
   is told the next stage has unlocked rather than having to notice. The pulse
   is on the numeral, not the whole tab: a moving block of text is harder to
   read than a moving disc beside it - that much is unchanged, and it is why
   none of what follows touches the label.

   It is loud on purpose. The first version was a single faint ring on a slow
   cycle, which read as a rendering artefact rather than an invitation; this
   one carries a tinted disc, two rings leaving at different rates, and a small
   swell of the disc itself, on a cycle short enough to catch the eye without
   nagging. Reduced motion keeps the tint and a static double ring, so the
   affordance survives with the movement removed. */
.step-beckon .step-index{animation:stepbeckon 1.6s ease-in-out infinite;
  border-color:var(--accent);color:var(--accent);background:#EAF1FC}
@keyframes stepbeckon{
  0%,100%{box-shadow:0 0 0 0 rgba(31,95,191,.55),0 0 0 0 rgba(31,95,191,.3);
    transform:scale(1)}
  50%{box-shadow:0 0 0 10px rgba(31,95,191,0),0 0 0 17px rgba(31,95,191,0);
    transform:scale(1.09)}
}
@media (prefers-reduced-motion:reduce){
  .step-beckon .step-index{animation:none;transform:none;
    box-shadow:0 0 0 4px rgba(31,95,191,.34),0 0 0 8px rgba(31,95,191,.16)}
}
.step:disabled:hover{background:none}
.step-index{width:37.5px;height:37.5px;flex:0 0 auto;display:grid;place-items:center;
  border:1px solid var(--line-strong);border-radius:50%;background:var(--surface);
  font-family:var(--f-num);font-size:15px;font-weight:700;color:var(--ink-3);
  transition:background .15s ease,color .15s ease,border-color .15s ease}
.step-label{display:grid}
.step-label strong{font-family:var(--f-num);font-size:17px;font-weight:600;line-height:1.25}
.step-label small{font-size:13.5px;color:var(--ink-3);margin-top:1px;line-height:1.25}
.step[aria-selected="true"]{color:#16243A}
.step[aria-selected="true"] .step-index{background:#16243A;color:#fff;border-color:#16243A}
.step[aria-selected="true"] .step-label strong{font-weight:700}
.step-connector{width:100px;height:1px;flex:0 0 auto;background:var(--line-strong);margin:0 12.5px}
@media (prefers-reduced-motion:reduce){.step-index{transition:none}}
/* Narrow: the descriptions go before the stage names do, and the connector
   shrinks rather than pushing the second stage off the edge. */
@media (max-width:719px){
  .step-connector{width:30px;margin:0 5px}
  .step-label small{display:none}
}

/* The note under a segmented control or a level grid. It outlived the
   read-only variant summary this file used to open with: the variant is
   chosen with the base portfolio and step 2 no longer restates it. */
.vr-note{margin:1px 0 0;font-size:12.5px;color:var(--rail-ink-3);line-height:1.45}
/* the tactical tilt toggle: an implementation choice, so it sits with the
   sleeves rather than in the strategic tier (D50) */
.tilt-field{display:grid;gap:4px;margin:0 0 14px;padding:0 0 14px;
  border-bottom:1px solid var(--rail-line)}
.tilt-field .chk{margin:0}
.tilt-field.done label{color:var(--rail-ink)}
.tilt-field .chk-note{margin:0}
/* ── pricing: fee schedule and fee level (D51) ──
   The schedule is a two-way segmented control with nothing pressed until a
   PWA presses it; the level is a two-by-three grid, one row per source, so
   the six levels read as the band they are rather than as a list. Both sit
   with the sleeves because they price the sheet the sleeves build.

   They close the tier, below the pickers, and the group carries its own label
   because the tier heading says Sleeves and a fee schedule is not one. The
   rule is on TOP of the group here - between the sleeve list and pricing -
   and the last field drops its own, so the group's last line is not a rule
   sitting a few pixels above the tier's. */
.fee-group{margin:16px 0 0;padding:15px 0 0;border-top:1px solid var(--rail-line)}
/* PRICING is a section heading like the tier headings above it, and reads the
   same - it is only a separate rule because it sits inside a tier. */
.fee-group-h{margin:0 0 11px;font-family:var(--f-num);font-size:11px;font-weight:700;
  letter-spacing:.16em;text-transform:uppercase;color:var(--rail-ink-3);line-height:1}
/* ── the include-fees toggle (D52) ──
   Off, it is the only thing under the Pricing label and the table has no fee
   column; on, the schedule and the level unravel beneath it. The tick box
   matches the tactical tilt's, which is the rail's other section switch. */
.fee-toggle{display:grid;gap:4px}
.fee-toggle .chk{margin:0}
.fee-toggle.done label{color:var(--rail-ink)}
.fee-toggle .chk-note{margin:0}
.fee-body{display:block;margin:13px 0 0}
/* Reveal and hide. The reveal plays on nodes that were just rendered, where a
   transition would have nothing to move from, so both directions are keyframe
   animations; the leave fills forwards because it has to hold the collapsed
   state until the render that removes the block. max-height is the animatable
   stand-in for auto height - 420px clears the block at every rate grid the
   framework can serve, and the overflow only applies while it plays. */
@keyframes feeunravel{from{opacity:0;max-height:0;transform:translateY(-6px)}
  to{opacity:1;max-height:420px;transform:none}}
@keyframes feeravel{from{opacity:1;max-height:420px}
  to{opacity:0;max-height:0;transform:translateY(-6px)}}
.fee-body.unravel{animation:feeunravel 360ms cubic-bezier(.4,0,.2,1);overflow:hidden}
.fee-body.ravel{animation:feeravel 360ms cubic-bezier(.4,0,.2,1) both;overflow:hidden}
@media (prefers-reduced-motion:reduce){
  .fee-body.unravel,.fee-body.ravel{animation:none}}
.fee-field{display:grid;gap:6px;margin:0 0 14px;padding:0 0 14px;
  border-bottom:1px solid var(--rail-line)}
.fee-group .fee-field:last-child{margin-bottom:0;padding-bottom:0;border-bottom:none}
.fee-field .fee-label{font-size:13px;font-weight:600;color:var(--rail-ink-2);line-height:1.3}
.fee-field.done .fee-label{color:var(--rail-ink)}
/* Two columns by default; the level's two controls set their own from the
   schema's lists, so a framework with four points would still lay out. */
.fee-seg{display:grid;grid-template-columns:1fr 1fr;
  border:1px solid var(--rail-input-border,#52739C);border-radius:4px;overflow:hidden}
.fee-seg button+button{border-left:1px solid var(--rail-input-border,#52739C)}
.fee-seg button{font:inherit;font-size:13px;font-weight:600;padding:7px 6px;cursor:pointer;
  background:var(--rail-input-bg,#1D2F4B);color:var(--rail-ink-2);border:0;line-height:1.2}
.fee-seg button+button{border-left:1px solid var(--rail-input-border,#52739C)}
.fee-seg button:hover{color:var(--rail-ink)}
.fee-seg button[aria-pressed="true"]{background:var(--rail-accent,var(--accent));
  color:var(--rail-accent-ink,#fff)}
.fee-seg button:disabled{cursor:default;opacity:.6}
.fee-seg button:focus-visible{outline:2px solid var(--rail-focus,#8FB4FF);outline-offset:-2px}
/* the card's own line: delivery version, adjustments, and the way in (D55) */
.fee-card{margin:6px 0 0;font-size:12px;color:var(--rail-ink-3);line-height:1.5}
/* the way into the card, under the toggle that turns fees on */
.fee-view{width:100%;margin:9px 0 0;font-size:12.5px;padding:6px 10px;
  background:var(--rail-input-bg,#1D2F4B);border:1px solid var(--rail-input-border,#52739C);
  color:var(--rail-ink);border-radius:4px;cursor:pointer}
.fee-view:hover{border-color:var(--rail-accent,var(--accent))}
.fee-view:focus-visible{outline:2px solid var(--rail-focus,#8FB4FF);outline-offset:2px}
/* ── the rate card panel (D55) ──
   The mandate dialog's chrome, widened for a six-column grid. A cell shows
   the rate in force; amber means an adjustment is saved on top of the
   delivered rate, blue means changed here and not yet saved. */
.dialog.wide{width:min(940px,calc(100vw - 32px))}
/* The card can be opened from inside the repository console (D58), so it
   stacks above that dialog and its scrim rather than under them. */
#feeDialog .scrim{z-index:94}
#feeDialog .dialog{z-index:95}
/* ── the fee card (D55), laid out to be read at a glance ──
   A short head (what delivery this is), one row of controls (which way the
   card is turned, and which slice), then the grid. Three things carry the
   emphasis and nothing else does: the two source groups are visibly two
   groups; the column of the level this proposal prices at is heavier and
   tinted; and, pivoted by fee group, the one cell that is THIS mandate's rate
   - its tier crossed with that level - wears a ring. Everything else is
   quiet, so those three read first. */
.dialog.rc{padding:22px 26px 20px}
.rc-head{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 14px;margin:0 0 16px;padding-right:36px}
.dialog.rc .rc-head h2{margin:0}
.rc-meta{display:flex;flex-wrap:wrap;gap:2px 14px;margin:0;font-family:var(--f-num);font-size:12.5px;
  color:var(--ink-3)}
.rc-meta b{color:var(--ink-2);font-weight:600}
.rc-flag{font-family:var(--f-num);font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  color:#92600A;background:#FEF3C7;border:1px solid #F3D48A;border-radius:9px;padding:1px 8px;line-height:16px}
.rc-controls{display:flex;align-items:center;flex-wrap:wrap;gap:10px 12px;margin:0 0 12px}
.rc-seg{display:flex;border:1px solid var(--line-strong);border-radius:4px;overflow:hidden}
.rc-seg button{appearance:none;border:0;border-left:1px solid var(--line-strong);background:var(--surface);
  color:var(--ink-2);font:inherit;font-size:13px;padding:6px 12px;cursor:pointer;white-space:nowrap}
.rc-seg button:first-child{border-left:0}
.rc-seg button:hover{background:var(--surface-2);color:var(--ink)}
.rc-seg button[aria-selected="true"]{background:#16243A;color:#fff;font-weight:600}
.rc-seg button:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.rc-axis{width:auto;min-width:240px;font-size:13px}
.rc-loading{color:var(--ink-3);font-size:13px;margin:12px 0}
.rc-wrap{overflow-x:auto;border:1px solid var(--line-strong);border-radius:var(--radius)}
.rate-grid{border-collapse:collapse;width:100%;font-size:13px}
.rate-grid th,.rate-grid td{padding:0 12px;border-bottom:1px solid var(--line);white-space:nowrap;
  height:38px;vertical-align:middle}
.rate-grid thead th{font-family:var(--f-num);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--ink-3);background:var(--surface-2);font-weight:700;text-align:right;height:30px}
/* the two source groups: a spanning head with its own rule, and a stronger
   line plus a little air where one group becomes the next */
.rate-grid th.src{text-align:center;border-bottom:1px solid var(--line-strong);height:32px;
  font-size:11.5px;letter-spacing:.14em;color:var(--ink-2)}
.rate-grid th.src span{display:inline-block;padding:0 6px 3px;border-bottom:2px solid var(--ink-3)}
/* its own name, not .grp: the page has a .grp + .grp rule for stacked
   groups that would otherwise pad the second source head down */
.rate-grid .rc-grp{border-left:2px solid var(--line-strong);padding-left:20px}
.rate-grid thead th.rowhead{text-align:left;vertical-align:bottom;padding-bottom:8px}
/* the row head: an id set as a small pill, the band beside it, a tag when it
   is this mandate's own row */
.rate-grid tbody th.rowhead{text-align:left;font-weight:500;color:var(--ink);min-width:220px;
  display:flex;align-items:center;gap:9px;height:38px}
.rc-id{font-family:var(--f-num);font-size:11.5px;font-weight:700;letter-spacing:.04em;color:var(--ink-2);
  background:var(--surface-2);border:1px solid var(--line-strong);border-radius:4px;padding:1px 7px;line-height:17px;
  min-width:34px;text-align:center}
.rc-lbl{font-size:13px;color:var(--ink)}
.rc-tag{margin-left:auto;font-family:var(--f-num);font-size:10.5px;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:#1E4FA3;background:#E3EBFA;border-radius:8px;padding:0 7px;line-height:16px}
/* the level this proposal prices at: heavier, tinted, named in its head */
.rate-grid th.lvl{color:#1E4FA3;background:#E9F0FB}
.rate-grid th.lvl small{display:block;font-size:9px;letter-spacing:.08em;font-weight:700;color:#1E4FA3;
  opacity:.85;margin-top:1px}
.rate-grid td.lvl{background:#F3F7FD;font-weight:700;color:#16243A}
/* this mandate's own row */
.rate-grid tr.mark td,.rate-grid tr.mark th.rowhead{background:#F0F4FB}
.rate-grid tr.mark td.lvl{background:#E3EBFA}
.rate-grid tr.mark th.rowhead{box-shadow:inset 3px 0 0 var(--accent)}
.rate-grid tr.mark .rc-id{background:#fff;border-color:#B9CDF0;color:#1E4FA3}
/* the one cell that is this mandate's rate */
.rate-grid td.ring{box-shadow:inset 0 0 0 2px var(--accent);border-radius:3px;color:#1E4FA3}
.rate-grid td.rate{font-family:var(--f-num);font-variant-numeric:tabular-nums;text-align:right;
  color:var(--ink-2);min-width:74px;font-size:13.5px}
.rate-grid tbody tr:last-child td,.rate-grid tbody tr:last-child th{border-bottom:none}
.rate-grid tbody tr:hover td:not(.lvl),.rate-grid tbody tr:hover th.rowhead{background:var(--row-hover,var(--surface-2))}
.rate-grid tbody tr.mark:hover td:not(.lvl),.rate-grid tbody tr.mark:hover th.rowhead{background:#E9EFF9}
/* the key: one swatch per emphasis, in the order they appear */
.rc-legend{display:flex;flex-wrap:wrap;gap:6px 18px;margin:10px 0 0;font-size:12px;color:var(--ink-2)}
.rc-legend i{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-2px;margin-right:6px;
  border:1px solid var(--line-strong);background:var(--surface)}
.rc-legend i.k-unit{border-style:dashed}
.rc-legend i.k-lvl{background:#E9F0FB;border-color:#B9CDF0}
.rc-legend i.k-mark{background:#F0F4FB;border-color:#B9CDF0;box-shadow:inset 3px 0 0 var(--accent)}
.rc-legend i.k-ring{box-shadow:inset 0 0 0 2px var(--accent);background:#E3EBFA;border-color:transparent}
.rc-actions{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:16px;flex-wrap:wrap}
.rc-note{font-family:var(--f-num);font-size:12px;color:var(--ink-3)}
.dialog .md-err{margin-top:12px}
/* placeholder pricing is flagged in the rail as long as fees.json says so */
.fee-flag{margin:2px 0 0;font-size:12px;font-weight:600;color:#F3C46B;line-height:1.4}

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
/* Narrower by the three fee columns when the proposal excludes them (D52).
   The width is transitioned so the table closes up as the columns collapse
   rather than snapping when the render lands. */
/* Smaller type than the step 1 tables carry: this one is 23 columns wide and
   read across, where those are read down. .tbl sets 15px for both. */
.tbl.impl{min-width:1690px;transition:min-width var(--col-motion);font-size:13px}
.tbl.impl.no-fees,.tbl.impl.fees-out{min-width:1340px}
@media (prefers-reduced-motion:reduce){.tbl.impl{transition:none}}
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
/* the fee group (D51): struck through, not removed, under a schedule that
   does not read it - the column still says what the product is */
.p-grp{background:#EEF1F4;color:#3E4E60;border:1px solid #D8DEE6}
.tbl.impl .fee-dead .p-grp{text-decoration:line-through;opacity:.55}
.tbl.impl th.fee-dead{text-decoration:line-through;opacity:.7}
/* ── the fee columns arriving and leaving (D52) ──
   A column has no width of its own: it takes it from the widest cell. So the
   animation runs on two things at once - the cell's side padding, and a span
   around the content whose max-width is what the column measures. Together
   they open the column from nothing and close it back to nothing, with the
   contents fading over the top. The leave fills forwards, because it has to
   hold the collapsed state until the render that removes the cells. */
.tbl.impl .fcw{display:inline-block;max-width:220px;overflow:hidden;
  white-space:nowrap;vertical-align:middle}
@keyframes feecolin{from{opacity:0;padding-left:0;padding-right:0}}
@keyframes feecolout{to{opacity:0;padding-left:0;padding-right:0}}
@keyframes feespanin{from{max-width:0;opacity:0}}
@keyframes feespanout{to{max-width:0;opacity:0}}
.tbl.impl.fees-in th.fee-col,.tbl.impl.fees-in td.fee-col{
  animation:feecolin 360ms ease-out}
.tbl.impl.fees-in .fcw{animation:feespanin 360ms cubic-bezier(.4,0,.2,1)}
.tbl.impl.fees-out th.fee-col,.tbl.impl.fees-out td.fee-col{
  animation:feecolout 360ms ease-in both}
.tbl.impl.fees-out .fcw{animation:feespanout 360ms cubic-bezier(.4,0,.2,1) both}
@media (prefers-reduced-motion:reduce){
  .tbl.impl.fees-in th.fee-col,.tbl.impl.fees-in td.fee-col,.tbl.impl.fees-in .fcw,
  .tbl.impl.fees-out th.fee-col,.tbl.impl.fees-out td.fee-col,
  .tbl.impl.fees-out .fcw{animation:none}}
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
