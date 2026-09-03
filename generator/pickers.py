# -*- coding: utf-8 -*-
"""Six comparison-picker variants over one shared core.

Currency and Hedging are scenario-level. A portfolio varies on Risk Level and
Allocation, where Allocation is the compound of AA Type + Include RE:

    Full + RE | Full | Ex HFs + RE | Ex HFs | Core | Ex Alts

Collapsing those two fields into one 6-option axis removes the cascade entirely,
whichever picker sits on top. Every variant here assumes it.
"""

SHARED_CSS = r"""
.rail-tiers{flex:1}
.tier{padding:14px 20px;border-bottom:1px solid var(--rail-line)}
.tier:last-of-type{border-bottom:none}
.tier-h{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin:0 0 9px}
.tier-h h3{margin:0;font-family:var(--f-num);font-size:11px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--rail-ink-3);font-weight:700}
.tier-count{font-family:var(--f-num);font-size:11.5px;letter-spacing:.08em;color:var(--rail-ink-2);
  font-weight:700}
/* ── rolling a tier up ──
   The base portfolio's settings are answered by the time step 2 opens, so the
   tier rolls up there and the chevron reopens it. max-height is the animatable
   stand-in for auto height; 520px clears the tier at its tallest (variant,
   allocation, risk, the exclusion and its note). visibility follows on a delay
   so the controls leave the tab order once they are out of sight, and arrives
   immediately on the way back in. */
.tier-roll{appearance:none;background:none;border:0;padding:0;margin:0 0 0 auto;
  width:22px;height:22px;display:grid;place-items:center;cursor:pointer;align-self:center;
  color:var(--rail-ink-3);font-size:15px;line-height:1;border-radius:4px;
  transform:rotate(90deg);transition:transform 320ms cubic-bezier(.4,0,.2,1),
  color 150ms linear}
.tier-roll:hover{color:var(--rail-ink)}
.tier-roll:focus-visible{outline:2px solid var(--rail-focus);outline-offset:2px}
.tier.is-rolled .tier-roll{transform:rotate(0deg)}
.tier-body{overflow:hidden;max-height:520px;opacity:1;visibility:visible;
  transition:max-height 420ms cubic-bezier(.4,0,.2,1),opacity 240ms linear 60ms,
  visibility 0s linear 0s}
.tier.is-rolled .tier-body{max-height:0;opacity:0;visibility:hidden;
  transition:max-height 420ms cubic-bezier(.4,0,.2,1),opacity 200ms linear,
  visibility 0s linear 420ms}
/* the frame in which the renderer paints the outgoing state, before it flips */
.tier-body.no-roll{transition:none}
@media (prefers-reduced-motion:reduce){
  .tier-roll,.tier-body,.tier.is-rolled .tier-body{transition:none}}
.tier-edit{background:none;border:0;padding:0;font:inherit;font-size:12px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--rail-accent);cursor:pointer;font-weight:700}
.tier-edit:hover{text-decoration:underline}
.tier-edit:focus-visible{outline:2px solid var(--rail-focus);outline-offset:2px}
.summary{font-size:14.5px;color:var(--rail-ink);line-height:1.5;margin:0}
.summary span{display:block;color:var(--rail-ink-2);font-size:13px}
.basis{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.basis .field label{font-size:12px}

.preview{margin:12px 0 0;font-size:14px;color:var(--rail-ink-2)}
.preview p{margin:0}
.preview ol{margin:6px 0 0;padding:0;list-style:none}
.preview li{display:flex;gap:8px;align-items:center;padding:4px 0;color:var(--rail-ink)}
.preview li b{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;
  border-radius:3px;background:var(--rail-accent);color:#fff;font-size:11px;flex-shrink:0}
.preview .none{color:var(--rail-ink-3);font-size:13px;line-height:1.45}
.pk-actions{margin:14px 0 0;display:grid;gap:8px}
.built{margin:12px 0 0;display:grid;gap:5px}
.built-row{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:4px;
  background:var(--rail-input-bg);border:1px solid var(--rail-input-border);font-size:13px;
  color:var(--rail-ink)}
.built-row .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.built-row .tag{font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--rail-ink-3);
  font-weight:700}
.rm{background:none;border:0;color:var(--rail-ink-3);cursor:pointer;font:inherit;font-size:17px;
  line-height:1;padding:0 2px}
.rm:hover{color:#FF8A80}
.rm:focus-visible{outline:2px solid var(--rail-focus);outline-offset:2px}
.colcount{font-family:var(--f-num);font-size:12.5px;color:var(--ink-2)}

/* ── charts ───────────────────────────────────────────────────────────────
   Categorical slots are the validated default theme, assigned in fixed order.
   The house green/amber/red are NOT used here: they are reserved as status
   colours, and a status hue must never impersonate a series. */
:root{--cat-1:#2a78d6;--cat-2:#eb6834;--cat-3:#1baf7a;--cat-4:#eda100;--cat-5:#e87ba4;
  --cat-6:#4a3aa7;--cat-7:#e34948}
.viz{--viz-grid:#E4EAF0;--viz-axis:#C3CDD8;--viz-ink:var(--ink);--viz-mut:var(--ink-2)}
.viz-grid{display:grid;grid-template-columns:1fr;gap:18px}
@media (min-width:1180px){.viz-grid{grid-template-columns:1fr 1fr}}
.viz-card{background:var(--surface);border:1px solid var(--line-strong);
  border-left:3px solid var(--accent);
  border-radius:var(--radius);padding:16px 18px 14px;min-width:0}
.viz-card h3{margin:0 0 2px;font-size:15px;font-weight:700;color:#16243A;
  font-family:var(--f-display)}
.viz-card .sub{margin:0 0 12px;font-size:13px;color:var(--ink-3)}
.viz-legend{display:flex;flex-wrap:wrap;gap:10px 14px;margin:0 0 12px;font-size:12.5px;
  color:var(--ink-2)}
.viz-legend i{font-style:normal;display:inline-flex;align-items:center;gap:6px}
.viz-legend .sw{width:11px;height:11px;border-radius:2px;display:inline-block}
.viz svg{width:100%;height:auto;display:block;overflow:visible}
.viz text{font-family:var(--f-num);fill:var(--viz-mut)}
.viz .ax{stroke:var(--viz-axis);stroke-width:1}
.viz .gl{stroke:var(--viz-grid);stroke-width:1}
.viz .seg{stroke:var(--surface);stroke-width:2}
.viz .seg:hover{filter:brightness(1.08)}
.viz .dot{stroke:var(--surface);stroke-width:2}
.viz .lbl{font-size:12px;fill:#fff;font-weight:700}
.viz .plbl{font-size:12.5px;fill:var(--ink);font-weight:600}
.viz .tick{font-size:11.5px;fill:var(--ink-3)}
.viz-tip{position:fixed;z-index:90;pointer-events:none;background:#16243A;color:#fff;
  border-radius:4px;padding:6px 9px;font-family:var(--f-num);font-size:13px;
  box-shadow:0 6px 18px -4px rgba(16,24,40,.4);display:none;white-space:nowrap}
.viz-tip.on{display:block}
.viz-tip b{color:#8FB4FF;font-weight:700}
.viz-empty{padding:26px 0;font-size:14px;color:var(--ink-3);text-align:center}
@media (prefers-reduced-motion:reduce){.viz .seg:hover{filter:none}}
.tbl thead th.just-added{animation:landed 1.4s ease-out}
@keyframes landed{0%{background:#E3EBFA}60%{background:#E3EBFA}100%{background:var(--head-bg)}}
@media (prefers-reduced-motion:reduce){.tbl thead th.just-added{animation:none}}
.pk-note{font-size:12.5px;color:var(--rail-ink-3);line-height:1.45;margin:10px 0 0}
/* ── landing ─────────────────────────────────────────────────────────────────
   The Swiss direction, chosen 30 Aug 2026. There is no hero artefact: the type
   is the design, and the only colour is the seven-category rule, drawn from the
   same validated palette the allocation table uses. The ground is a near-white
   the same ground as the workspace: both read --bg, so the two surfaces
   cannot drift apart on a retheme. */
body.phase-landing{background:var(--bg)}
body.phase-landing .rail{display:none}
body.phase-landing .shell{margin-left:0;max-width:none;padding:0}
body.phase-landing #alertArea{padding:0 clamp(20px,6vw,80px)}
#view-landing{min-height:100vh;display:grid;grid-template-columns:minmax(0,1fr);
  align-content:center;padding:clamp(32px,5vh,72px) clamp(20px,6vw,80px);background:var(--bg)}
.lp-inner{width:100%;max-width:1120px;margin:0 auto}
.lp-rule{height:2px;background:#16243A;margin:0 0 18px;transform-origin:left;
  animation:lp-grow .7s both}

/* the copy column arrives top to bottom, then holds still */
.lp-copy>*{animation:lp-rise .7s cubic-bezier(.2,.8,.2,1) both}
.lp-copy>:nth-child(1){animation-delay:.05s}
.lp-copy>:nth-child(2){animation-delay:.14s}
.lp-copy>:nth-child(3){animation-delay:.24s}

.lp-eyebrow{font-family:var(--f-num);font-size:11.5px;letter-spacing:.24em;
  text-transform:uppercase;color:#7C8B9C;margin:0 0 26px;font-weight:600}
/* The host gets its name in the accent rather than the eyebrow's grey. It is
   a credit, and a credit nobody reads is not one - this is the only word on
   the line that has to carry. */
.lp-eyebrow b{color:var(--accent);font-weight:700}
.lp-title{font-family:'Goldman Sans','GS Sans','Roboto',system-ui,sans-serif;font-weight:400;
  font-size:clamp(53px,6vw,99px);line-height:.96;letter-spacing:-.045em;color:#152135;
  max-width:15ch;margin:0}
.lp-title span{display:block}
.lp-title .lp-accent{color:#446cce}

.lp-spec{display:flex;gap:2px;height:16px;border-radius:2px;overflow:hidden;
  position:relative;margin:26px 0 0}
.lp-spec span{flex:1;transform-origin:left;animation:lp-grow .55s cubic-bezier(.2,.8,.2,1) both}
.lp-spec span:nth-child(2){animation-delay:.06s}
.lp-spec span:nth-child(3){animation-delay:.12s}
.lp-spec span:nth-child(4){animation-delay:.18s}
.lp-spec span:nth-child(5){animation-delay:.24s}
.lp-spec span:nth-child(6){animation-delay:.30s}
.lp-spec span:nth-child(7){animation-delay:.36s}
.lp-spec::after{content:"";position:absolute;inset:0;pointer-events:none;
  background:linear-gradient(100deg,transparent 40%,rgba(255,255,255,.5) 50%,transparent 60%);
  transform:translateX(-100%);animation:lp-sheen 7s 2.6s infinite}

.lp-grid{display:grid;gap:36px;grid-template-columns:1fr;margin-top:26px;padding-top:22px;
  border-top:1px solid var(--line-strong)}
.lp-lede{font-size:16.5px;line-height:1.62;color:var(--ink-2);max-width:46ch;margin:0 0 34px}
/* What the tool is for, in two lines. The marker is a square in the accent,
   which is the same rectilinear language as the colour bars above it - a
   round bullet would be the only curve on the page. */
.lp-points{list-style:none;margin:0 0 34px;padding:0;display:grid;gap:15px;max-width:88ch}
.lp-points li{position:relative;padding-left:24px;font-size:16.5px;line-height:1.5;
  color:var(--ink-2)}
.lp-points li::before{content:"";position:absolute;left:0;top:.55em;width:9px;height:9px;
  background:var(--accent)}

.lp-cta{font-size:16px;padding:16px 30px 16px 36px;border-radius:0;letter-spacing:.02em;
  position:relative;display:inline-flex;align-items:center;gap:14px;
  --btn-case:none;                       /* the landing CTA is sentence case, unlike app buttons */
  animation:lp-pulse 2.8s .9s ease-in-out infinite}
.lp-cta::after{content:"\2192";font-family:'GS Sans','Roboto',sans-serif;font-weight:400;
  transition:transform .25s}
.lp-cta:hover::after{transform:translateX(5px)}
.lp-cta::before{content:"";position:absolute;inset:-1px;border:1.5px solid currentColor;
  pointer-events:none;opacity:0;animation:lp-halo 2.8s .9s ease-out infinite}
.lp-cta:focus-visible{outline:2px solid var(--accent);outline-offset:3px}

@media (max-width:560px){
  .lp-title{font-size:clamp(40px,11vw,55px);line-height:.98;max-width:100%}
}
@keyframes lp-grow{from{transform:scaleX(0)}}
@keyframes lp-rise{from{opacity:0;transform:translateY(14px)}}
@keyframes lp-sheen{0%{transform:translateX(-100%)}22%,100%{transform:translateX(100%)}}
@keyframes lp-pulse{
  0%,100%{box-shadow:0 12px 30px -10px rgba(31,95,191,.55),0 0 0 0 rgba(31,95,191,0)}
  50%{box-shadow:0 18px 40px -8px rgba(31,95,191,.72),0 0 0 10px rgba(31,95,191,.12)}}
@keyframes lp-halo{0%{transform:scale(1);opacity:.34}72%,100%{transform:scale(1.13,1.3);opacity:0}}
.landing{max-width:660px;margin:0 auto;padding:clamp(48px,11vh,120px) 0 80px}
.landing .eyebrow{font-family:var(--f-num);font-size:12px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--ink-3);margin:0 0 16px;font-weight:700}
.landing h1{font-family:var(--f-display);font-size:clamp(30px,4.6vw,46px);line-height:1.1;
  letter-spacing:-.02em;color:#16243A;margin:0 0 18px;font-weight:400;text-wrap:balance}
.landing .lede{font-size:17px;line-height:1.6;color:var(--ink-2);max-width:56ch;margin:0 0 32px}
.landing .btn{font-size:16px;padding:15px 34px}
.landing-need{margin:52px 0 0;padding:22px 0 0;border-top:1px solid var(--line-strong)}
.landing-need h2{font-family:var(--f-num);font-size:11px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--ink-3);margin:0 0 12px;font-weight:700}
.landing-need ul{margin:0;padding:0;list-style:none;display:grid;gap:8px}
.landing-need li{font-size:14.5px;color:var(--ink-2);padding-left:20px;position:relative}
.landing-need li::before{content:"";position:absolute;left:0;top:8px;width:7px;height:7px;
  border-radius:50%;background:var(--accent)}

/* ── mandate dialog ── */
#mandateDialog[hidden]{display:none}
.scrim{position:fixed;inset:0;background:rgba(16,24,40,.42);z-index:90}
.dialog{position:fixed;z-index:91;top:50%;left:50%;transform:translate(-50%,-50%);
  width:min(460px,calc(100vw - 32px));max-height:calc(100vh - 32px);overflow-y:auto;
  background:var(--surface);border:1px solid var(--line-strong);border-radius:var(--radius);
  box-shadow:0 24px 60px -12px rgba(16,24,40,.4);padding:24px 26px 22px}
/* 20px below, the gap the dropped standfirst used to carry. */
.dialog h2{margin:0 0 20px;font-size:19px;color:#16243A;font-family:var(--f-display)}
/* Capitals through text-transform rather than in the markup, so the
   accessible name stays "Start a proposal" - some screen readers spell out an
   all-capitals string as an acronym. */
.dialog h2.dlg-shout{text-transform:uppercase;letter-spacing:.07em;font-size:17px;
  font-family:var(--f-num);font-weight:700}
.dialog .field{margin:0 0 16px}
.dialog .field label{font-size:13px;color:var(--ink-2);font-weight:600}
.dialog input[type=text]{background:var(--surface);border-color:var(--line-strong);
  color:var(--ink);font-size:15.5px;padding:10px 12px}
.dialog input[aria-invalid="true"]{border-color:#B42318}
.field-hint{display:block;margin-top:5px;font-size:12px;color:var(--ink-3);line-height:1.4}
.dlg-close{position:absolute;top:14px;right:16px;background:none;border:0;font-size:22px;
  line-height:1;color:var(--ink-3);cursor:pointer;padding:2px 6px;border-radius:4px}
.dlg-close:hover{color:var(--ink)}
.dlg-close:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.dlg-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:22px}
.dialog .md-err{margin:0;padding:9px 12px;border-radius:4px;background:#FDE8E8;
  border:1px solid #F1B8B2;color:#9B1C1C;font-size:13px;line-height:1.45;font-weight:600}

/* ── combobox ── */
.combo{position:relative}
.combo-count{position:absolute;right:12px;top:36px;font-family:var(--f-num);font-size:11.5px;
  color:var(--ink-3);pointer-events:none}
.combo-list{position:absolute;left:0;right:0;top:100%;margin:4px 0 0;padding:4px;list-style:none;
  background:var(--surface);border:1px solid var(--line-strong);border-radius:var(--radius-sm);
  box-shadow:0 12px 28px -10px rgba(16,24,40,.28);max-height:196px;overflow-y:auto;z-index:2}
.combo-list[hidden]{display:none}
.combo-opt{padding:8px 10px;border-radius:4px;font-size:14px;color:var(--ink);cursor:pointer}
.combo-opt:hover,.combo-opt.on{background:var(--accent-soft,#E3EBFA);color:#1E4FA3}
.combo-opt.none{color:var(--ink-3);cursor:default;font-size:13px}
.combo-opt.none:hover{background:none;color:var(--ink-3)}

.md-form{display:grid;gap:10px}
.md-actions{display:grid;gap:8px;margin-top:2px}
.md-actions .btn{width:100%}
.md-err{margin:0;padding:7px 9px;border-radius:4px;background:#FDE8E8;border:1px solid #F1B8B2;
  color:#9B1C1C;font-size:13px;line-height:1.4;font-weight:600}
.chk{display:flex;align-items:flex-start;gap:9px;padding:1px 0}
/* The exclusions stand off the selects above them: the selects are the choice
   and the tick boxes narrow what it produced, so they read as a second group
   rather than as a fourth field. On top of the .basis grid gap. */
.basis>.chk{margin-top:8px}
.chk input{appearance:none;-webkit-appearance:none;width:16px;height:16px;border-radius:3px;
  border:1px solid var(--rail-input-border);background:var(--rail-input-bg);flex-shrink:0;
  margin:1px 0 0;cursor:pointer;position:relative}
.chk input:checked{background:var(--rail-accent);border-color:var(--rail-accent)}
.chk input:checked::after{content:"";position:absolute;left:4.5px;top:1px;width:4px;height:9px;
  border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg)}
.chk input:focus-visible{outline:2px solid var(--rail-focus);outline-offset:2px}
.chk input:disabled{cursor:not-allowed;opacity:.5}
.chk label{font-size:14px;color:var(--rail-ink-2);cursor:pointer;line-height:1.35}
.chk input:disabled + label{opacity:.55;cursor:not-allowed}
/* Flush with the tick box's own left edge, not indented under its label:
   every line in the rail starts on the same rule, and a note that steps
   in reads as a second column. The popover keeps its indent (below). */
.chk-note{font-size:12.5px;color:var(--rail-ink-3);margin:1px 0 4px;line-height:1.4}
.rail input[aria-invalid="true"]{border-color:#E0796D}
"""

import os

def _asset(name):
    """Read a JS payload from generator/js/. The JS moved out of Python
    strings when the page went data-driven - editing JavaScript as JavaScript
    beats editing it inside a string literal. build_styles.py concatenates the
    payloads in the load-bearing order (core -> picker -> implementation)."""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, 'js', name), encoding='utf-8') as fh:
        return fh.read()

CORE_JS = _asset('core.js')

# Option helpers now live inside core.js (they read the server schema);
# OPT_JS remains for build_styles' concatenation order.
OPT_JS = ""


PICKERS = {}

# ── the table picker: a "+" column in the allocation table opens a popover ───
PICKERS["table"] = dict(
name="Table",
blurb="A plus column in the allocation table opens a popover, and adding happens there \u2014 the column lands immediately. The rail carries no picker at all.",
html=r"""      <p class="pk-note">Add a comparison from the <strong>+</strong> column at the right of
        the allocation table.</p>""",
css=r"""
.addcol{width:46px;min-width:46px}
.plus{width:28px;height:28px;border-radius:4px;border:1px dashed var(--line-strong);
  background:var(--surface);color:var(--accent);font:inherit;font-size:19.5px;line-height:1;
  cursor:pointer;font-weight:700}
.plus:hover{border-style:solid;border-color:var(--accent);background:#EEF3FB}
.plus:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.pop{position:fixed;z-index:80;width:280px;background:var(--surface);
  border:1px solid var(--line-strong);border-radius:var(--radius);
  box-shadow:0 14px 38px -8px rgba(16,24,40,.32);padding:15px 16px 16px;display:none}
.pop.on{display:block}
.pop h4{margin:0 0 3px;font-family:var(--f-num);font-size:11px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--ink-2);font-weight:700}
.pop .slots{margin:0 0 12px;font-size:13px;color:var(--ink-3)}
.pop .field{margin:0 0 11px}
.pop .field label{color:var(--ink-2)}
.pop-actions{display:grid;gap:8px}
.pop-actions .btn{width:100%;padding:9px 12px}
.pop-close{position:absolute;top:9px;right:10px;background:none;border:0;color:var(--ink-3);
  font-size:19.5px;line-height:1;cursor:pointer;padding:0}
.pop-close:hover{color:var(--ink)}
.pop-close:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.pop-done{margin:0;font-size:13px;color:#176A33;display:none}
.pop.added .pop-done{display:block}
.pop-full{margin:0 0 11px;font-size:14px;color:var(--ink-2);line-height:1.45}
""",
js=_asset('picker.js'),
actions="")
