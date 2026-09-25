"""The sleeve repository console (D57): its stylesheet and its script.

Loaded after the implementation layer in build_styles.build_assets - the
console attaches to App like the picker and implementation layers do and
reuses their dialog, button and error idioms. Nothing here is reached by a
user who is not an admin: the entry links stay hidden and the endpoints
refuse, so the console is dead weight in a non-admin's page, deliberately
shipped anyway so the page is one artefact.
"""

import os

REPO_CSS = r"""
/* ── the sleeve repository console (D57) ──
   A three-pane overlay on the dialog base: categories, the sleeves in one,
   the editor for one. Sized to the viewport rather than to its content so
   the panes scroll independently and the footer stays put. */
.dialog.repo{width:min(1280px,calc(100vw - 32px));height:min(940px,calc(100vh - 32px));
  padding:0;display:flex;flex-direction:column;overflow:hidden}
/* A column of fixed bands around one band that takes the rest. Flex rather
   than a grid template because two of the bands - the unsaved-changes notice,
   the orphans notice - are only sometimes there, and a row template that
   counts children hands the wrong track to the wrong band when one is missing. */
.dialog.repo>.repo-h,.dialog.repo>.repo-f,.dialog.repo>.repo-notice{flex:0 0 auto}
/* The views sit inside the panel the tabs name (G1), so the band that takes
   the remaining height is that panel - and it passes the height on, because
   the panes inside it are what actually scroll. */
.dialog.repo>#repoPanel{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
#repoPanel>.cat-tools,#repoPanel>.repo-notice{flex:0 0 auto}
#repoPanel>.repo-b,#repoPanel>.cat-b{flex:1 1 auto;min-height:0}
.repo-h{display:flex;align-items:center;gap:12px 16px;flex-wrap:wrap;
  padding:12px 16px 12px 20px;border-bottom:1px solid var(--line-strong)}
/* The header carries a title, two segmented controls, the source line and the
   close button. Below full width there is not room for all of it: the source
   line goes first (the footer says the same thing), and wrapping is the
   backstop so a narrow window pushes a row down rather than clipping the last
   implementation type off the end. */
.dialog.repo .repo-h h2{margin:0;font-family:var(--f-num);font-size:13px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--ink)}
.repo-seg{display:flex;border:1px solid var(--line-strong);border-radius:4px;overflow:hidden}
.repo-seg button{appearance:none;border:0;border-left:1px solid var(--line-strong);
  background:var(--surface);color:var(--ink-2);font:inherit;font-size:13px;padding:6px 12px;
  cursor:pointer;white-space:nowrap}
.repo-seg button:first-child{border-left:0}
.repo-seg button:hover{background:var(--surface-2);color:var(--ink)}
.repo-seg button[aria-selected="true"]{background:#16243A;color:#fff;font-weight:600}
.repo-seg button:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.repo-src{font-family:var(--f-num);font-size:12.5px;color:var(--ink-3);white-space:nowrap}
.repo-h .repo-src{margin-left:auto}
.dialog.repo .dlg-close{position:static;margin-left:4px}
.repo-b{display:grid;grid-template-columns:232px 300px minmax(0,1fr);min-height:0}
/* The Sleeves view at the catalogue's width, so switching between the two
   does not jump, and with the extra room shared by all three panes - the
   editor, which holds the product table, taking most of it (D115). */
.dialog.repo.sleeves{width:min(1560px,calc(100vw - 32px))}
/* only where the card actually gets wider; below that the panes keep their
   widths, and the 900px rule still narrows them */
@media (min-width:1320px){.dialog.repo.sleeves .repo-b{grid-template-columns:268px 340px minmax(0,1fr)}}
.repo-pane{border-right:1px solid var(--line);min-height:0;overflow:auto;display:flex;flex-direction:column}
.repo-pane:last-child{border-right:0}
.repo-pane-h{font-family:var(--f-num);font-size:11.5px;font-weight:700;letter-spacing:.14em;
  text-transform:uppercase;color:var(--ink-3);padding:14px 16px 8px;display:flex;align-items:center;
  gap:8px;flex:0 0 auto;min-height:44px}
.repo-pane-h .btn{margin-left:auto;padding:5px 10px;font-size:11px}
.repo-cat{display:flex;align-items:center;gap:6px;width:100%;text-align:left;appearance:none;
  background:none;border:0;border-left:3px solid transparent;font:inherit;font-size:13.5px;
  color:var(--ink);padding:9px 16px 9px 13px;cursor:pointer;line-height:1.3}
.repo-cat:hover{background:var(--surface-2)}
.repo-cat .n{margin-left:auto;font-family:var(--f-num);font-size:12px;color:var(--ink-3)}
.repo-cat[aria-selected="true"]{background:#E3EBFA;border-left-color:var(--accent);color:#1E4FA3;font-weight:600}
.repo-cat[aria-selected="true"] .n{color:#1E4FA3}
.repo-fixed{display:inline-block;font-family:var(--f-num);font-size:10.5px;font-weight:700;
  letter-spacing:.08em;text-transform:uppercase;color:var(--ink-2);background:var(--surface-2);
  border-radius:8px;padding:0 6px;line-height:16px;vertical-align:middle}
.repo-sleeve{display:grid;gap:2px;width:100%;text-align:left;appearance:none;background:none;
  border:0;border-bottom:1px solid var(--line);font:inherit;padding:10px 16px;cursor:pointer;
  color:var(--ink);line-height:1.35}
.repo-sleeve:hover{background:var(--surface-2)}
.repo-sleeve b{font-size:13.5px;font-weight:600}
.repo-sleeve small{font-size:12px;color:var(--ink-2)}
.repo-sleeve[aria-selected="true"],.repo-sleeve.new{background:#F0F4FB;box-shadow:inset 3px 0 0 var(--accent)}
.repo-sleeve .warn,.repo-pick .warn{color:#B45309;font-weight:600}
.repo-none{margin:10px 16px;font-size:13px;color:var(--ink-3)}
/* ── the sleeve list's own controls (A1, C1) ──
   The book strip moved here from the console header, where it was a second
   segmented control identical to the view switch; and the search that the
   library of 101 sleeves had never had sits under it. */
.repo-list{display:flex;flex-direction:column}
.repo-books{display:flex;flex-wrap:wrap;gap:4px;padding:8px 12px 0}
.repo-book{appearance:none;border:1px solid var(--line-strong);background:var(--surface);
  border-radius:12px;padding:3px 9px;font-family:var(--f-num);font-size:11.5px;color:var(--ink-2);
  cursor:pointer;white-space:nowrap;line-height:1.5}
.repo-book:hover:not(:disabled){border-color:var(--ink-3);color:var(--ink)}
.repo-book[aria-pressed="true"]{background:#16243A;border-color:#16243A;color:#fff;font-weight:700}
.repo-book:disabled{opacity:.45;cursor:default}
.repo-book:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.repo-find{display:flex;align-items:center;gap:6px;margin:8px 12px 6px;padding:0 8px;
  border:1px solid var(--line-strong);border-radius:4px;background:var(--surface);color:var(--ink-3)}
.repo-find input{border:0;background:none;font:inherit;font-size:13px;color:var(--ink);
  padding:6px 0;width:100%;outline:none;min-width:0}
.repo-find input::-webkit-search-cancel-button{-webkit-appearance:none}
.repo-find kbd{font-family:var(--f-num);font-size:10.5px;border:1px solid var(--line-strong);
  border-radius:3px;padding:0 5px;color:var(--ink-3);background:var(--surface-2)}
.repo-find:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px rgba(31,95,191,.18)}
.repo-hits{margin:2px 16px 4px;font-family:var(--f-num);font-size:11.5px;color:var(--ink-3)}
/* ── the editor's own actions (A2) ──
   Archiving and adding an edition act on the sleeve being edited, so they sit
   where it is named rather than in the footer beside Save. */
.repo-ed-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 14px;
  padding-bottom:10px;border-bottom:1px solid var(--line)}
.repo-ed-where{flex:1 1 auto;min-width:0;font-family:var(--f-num);font-size:11.5px;
  letter-spacing:.06em;color:var(--ink-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.repo-ed-act{appearance:none;border:1px solid var(--line-strong);background:var(--surface);
  border-radius:4px;padding:4px 9px;font-family:var(--f-num);font-size:11.5px;color:var(--ink-2);cursor:pointer}
.repo-ed-act:hover:not(:disabled){border-color:var(--ink-3);color:var(--ink)}
.repo-ed-act.danger{color:#9B1C1C;border-color:#F1B8B2}
.repo-ed-act.danger:hover:not(:disabled){background:#FDE8E8;border-color:#9B1C1C;color:#9B1C1C}
.repo-ed-act:disabled{opacity:.5;cursor:default}
.repo-ed-act:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.repo-ed-top.is-confirm{background:#FDE8E8;border:1px solid #F1B8B2;border-radius:5px;
  padding:10px 12px;margin-bottom:14px}
.repo-ed-warn{flex:1 1 220px;font-size:13px;color:#7A1D1D}
.repo-ed-top.is-confirm .btn{padding:5px 11px;font-size:12px}
/* a draft that outlived a reload (F1) */
.repo-kept{background:#E3EBFA;border-color:#B9CDF0!important;color:#1E4FA3}
.repo-kept .btn{padding:4px 10px;font-size:12px}
/* what the draft's state is, in the footer (F2) */
.repo-state{font-family:var(--f-num);font-size:12px;white-space:nowrap;color:var(--ink-3)}
.repo-state.is-dirty{color:#B45309;font-weight:700}
.repo-state.is-saved{color:#176A33}
/* the register: when and who are one cell (D3) */
.reg-tbl td.reg-when{white-space:nowrap;line-height:1.3}
.reg-tbl td.reg-when small{display:block;font-family:var(--f-num);font-size:11px;color:var(--ink-3)}
.reg-tbl td.reg-pwa{max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.repo-ed{padding:14px 20px 12px;gap:12px}
.repo-frow{display:grid;grid-template-columns:minmax(0,1fr) 220px;gap:14px}
.repo-fld{display:grid;gap:5px}
.repo-fld label{font-family:var(--f-num);font-size:12px;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:var(--ink-2)}
.repo-fld input[type=text]{border:1px solid var(--line-strong);border-radius:4px;padding:7px 10px;
  font:inherit;font-size:13.5px;background:var(--surface);color:var(--ink);min-height:34px;width:100%}
.repo-fld input[aria-invalid="true"]{border-color:#B42318}
.repo-fld .ro{border:1px solid var(--line-strong);border-radius:4px;padding:7px 10px;font-size:13.5px;
  color:var(--ink-3);background:var(--surface-2);min-height:34px;display:flex;align-items:center;gap:6px}
.repo-prods{border:1px solid var(--line-strong);border-radius:6px}
.repo-prods .ph,.repo-prods .pr{display:grid;grid-template-columns:minmax(0,1fr) 96px 32px;
  align-items:center;gap:10px;padding:8px 12px}
.repo-prods .ph{font-family:var(--f-num);font-size:11px;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-3);border-bottom:1px solid var(--line);background:var(--surface-2)}
.repo-prods .pr{border-bottom:1px solid var(--line);position:relative}
.repo-pick{appearance:none;background:none;border:1px solid transparent;border-radius:4px;
  text-align:left;font:inherit;padding:4px 6px;display:grid;gap:1px;min-width:0;cursor:pointer;
  color:var(--ink);line-height:1.3}
.repo-pick:hover{border-color:var(--line-strong)}
.repo-pick b{font-weight:500;font-size:13.5px}
.repo-pick small{font-family:var(--f-num);font-size:12px;color:var(--ink-2)}
.repo-pick.empty b{color:var(--ink-3);font-weight:400}
.repo-w{font-family:var(--f-num);font-size:13.5px;text-align:right;border:1px solid var(--line-strong);
  border-radius:4px;padding:5px 8px;width:96px;background:var(--surface);color:var(--ink)}
.repo-rm{appearance:none;background:none;border:0;color:var(--ink-3);font-size:18px;line-height:1;
  cursor:pointer;padding:4px}
.repo-rm:hover{color:#9B1C1C}
.repo-search{width:100%;border:1px solid var(--accent);border-radius:4px;padding:6px 10px;
  font:inherit;font-size:13.5px;box-shadow:0 0 0 3px rgba(31,95,191,.18);background:var(--surface);color:var(--ink)}
.repo-menu{position:absolute;left:12px;right:12px;top:calc(100% - 2px);background:var(--surface);
  border:1px solid var(--line-strong);border-radius:6px;box-shadow:0 10px 30px rgba(12,24,44,.18);
  z-index:5;max-height:280px;overflow:auto;margin:0;padding:0;list-style:none}
.repo-menu li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;padding:8px 12px;
  border-bottom:1px solid var(--line);cursor:pointer;font-size:13px;line-height:1.3}
.repo-menu li:hover{background:var(--surface-2)}
.repo-menu li[aria-selected="true"]{background:#E3EBFA}
.repo-menu li b{font-weight:500}
.repo-menu li small{display:block;color:var(--ink-2);font-family:var(--f-num);font-size:12px}
.repo-menu .grp{font-family:var(--f-num);font-size:11.5px;color:#1E4FA3;background:#E3EBFA;
  border-radius:9px;padding:1px 8px;align-self:center;white-space:nowrap}
.repo-menu .hint{display:block;padding:6px 12px;font-family:var(--f-num);font-size:11.5px;
  color:var(--ink-3);background:var(--surface-2);cursor:default;border-bottom:0}
.repo-tot{display:grid;grid-template-columns:minmax(0,1fr) 96px 32px;gap:10px;padding:9px 12px;
  font-family:var(--f-num);font-size:13px;font-weight:700;background:var(--surface-2)}
.repo-tot .ok{color:#176A33;text-align:right}
.repo-tot .bad{color:#9B1C1C;text-align:right}
.repo-add{align-self:start;margin-top:8px}
.repo-problems{margin:0;font-size:12.5px;color:#7A4A0A}
.repo-prov{font-family:var(--f-num);font-size:12px;color:var(--ink-3);margin:auto 0 0;line-height:1.5}
.repo-empty,.repo-loading{margin:20px;color:var(--ink-3);font-size:14px}
.repo-notice{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:9px 20px;
  background:#FEF3C7;color:#7A4A0A;font-size:13px;border-bottom:1px solid #F3D48A}
.repo-notice .btn{padding:5px 10px;font-size:11px}
.repo-ed .repo-notice{border:1px solid #F3D48A;border-radius:4px;padding:9px 12px}
.repo-f{display:flex;align-items:center;gap:10px;padding:10px 20px;border-top:1px solid var(--line-strong);
  background:var(--surface-2)}
.repo-f .spacer{flex:1}
.repo-f .md-err{margin:0;padding:6px 10px}
.btn.btn-danger{color:#9B1C1C;border-color:#F1B8B2;background:var(--surface)}
.btn.btn-danger:hover{background:#FDE8E8}
/* Create sits at the far left of the footer, away from Save and Delete: it
   does not act on the sleeve being edited, it starts a different one (D61). */
.btn.btn-create{background:#176A33;border-color:#176A33;color:#fff}
.btn.btn-create:hover{background:#12572A;border-color:#12572A}
.btn.btn-create:disabled{opacity:.5}
/* the two answers only a new sleeve gives */
.repo-fld select{border:1px solid var(--line-strong);border-radius:4px;padding:7px 10px;
  font:inherit;font-size:13.5px;background:var(--surface);color:var(--ink);min-height:34px;width:100%}
.repo-vars{display:flex;flex-wrap:wrap;gap:6px 14px}
.repo-var{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--ink);cursor:pointer}
.repo-var.off{color:var(--ink-3);cursor:not-allowed}
/* ── editions (D89) ──
   A name's editions sit together under it in the list, indented, each
   saying which portfolios it is for. In the editor the edition is one
   field: the label, then the rules as rows of chips - one row per rule,
   rules being alternatives - and under them the count the server gives
   back, green when it is clean and red when it collides. */
.repo-grp{display:flex;align-items:baseline;gap:8px;padding:9px 16px 3px;border-bottom:1px solid var(--line);
  background:var(--surface-2)}
.repo-grp b{font-size:13.5px;font-weight:600;color:var(--ink)}
.repo-grp small{font-family:var(--f-num);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.repo-sleeve.ed{padding-left:28px}
.repo-sleeve.ed b small{font-weight:400;color:var(--ink-3);font-size:12px;margin-left:4px}
.repo-edn .repo-sub{font-size:12px;color:var(--ink-3);line-height:1.45}
.repo-rules{display:grid;gap:8px}
.repo-rule{border:1px solid var(--line-strong);border-radius:6px;padding:8px 10px 6px;display:grid;gap:6px;
  background:var(--surface)}
.repo-rule-h{display:flex;align-items:center;justify-content:space-between;font-family:var(--f-num);
  font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-2)}
.repo-rule-h small{font-weight:400;letter-spacing:.04em;color:var(--ink-3);margin-left:6px;text-transform:none}
.repo-rule-h .repo-rm{position:static;width:22px;height:22px}
.repo-rule-f{display:grid;grid-template-columns:78px minmax(0,1fr);gap:8px;align-items:start}
.repo-rule-f .lbl{font-size:12px;color:var(--ink-2);padding-top:3px}
.repo-rule-f .chips{display:flex;flex-wrap:wrap;gap:4px}
.repo-chip{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--ink);cursor:pointer;
  border:1px solid var(--line-strong);border-radius:999px;padding:2px 9px 2px 6px;background:var(--surface);
  line-height:18px;user-select:none}
.repo-chip:hover{border-color:var(--accent)}
.repo-chip.on{background:#E3EBFA;border-color:#1E4FA3;color:#1E4FA3;font-weight:600}
.repo-chip input{margin:0;width:12px;height:12px;accent-color:#1E4FA3}
.repo-applies{margin:2px 0 0;font-size:12.5px;color:var(--ink-2);line-height:1.45}
.repo-applies.ok{color:#176A33}
.repo-applies.bad{color:#9B1C1C}
.rev-rules{margin:-4px 0 6px;font-family:var(--f-num);font-size:11.5px;color:var(--ink-3);letter-spacing:.02em}
/* the context menu on a sleeve: inside the dialog, clamped off its edges */
.repo-ctx{position:absolute;z-index:8;min-width:238px;background:var(--surface);
  border:1px solid var(--line-strong);border-radius:6px;box-shadow:0 12px 34px rgba(12,24,44,.24);
  padding:6px 0;display:grid}
.repo-ctx .h{margin:0;padding:7px 12px 6px;font-size:13.5px;font-weight:600;color:var(--ink);
  border-bottom:1px solid var(--line)}
.repo-ctx .h small{display:block;font-family:var(--f-num);font-size:11.5px;font-weight:400;color:var(--ink-3)}
.repo-ctx .lbl{margin:0;padding:8px 12px 4px;font-family:var(--f-num);font-size:10.5px;font-weight:700;
  letter-spacing:.12em;text-transform:uppercase;color:var(--ink-3)}
.repo-ctx .none{margin:0;padding:2px 12px 8px;font-size:12.5px;color:var(--ink-3)}
.repo-ctx .sep{margin:5px 0;border-top:1px solid var(--line)}
.repo-mi{appearance:none;background:none;border:0;text-align:left;font:inherit;font-size:13px;
  color:var(--ink);padding:7px 12px;cursor:pointer;width:100%}
.repo-mi:hover:not(:disabled){background:#E3EBFA;color:#1E4FA3}
.repo-mi:disabled{color:var(--ink-3);cursor:not-allowed}
.repo-mi.danger{color:#9B1C1C}
.repo-mi.danger:hover:not(:disabled){background:#FDE8E8;color:#9B1C1C}
.repo-mi:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
/* ── the record: history and removed sleeves (D65) ──
   The history sits under the editor as a closed drawer, because most visits
   are to change a sleeve rather than to read what it was. Open, it is a
   ladder of revisions newest first: the change summary always visible, the
   whole composition one click further. The version in force is marked, and
   only the others offer to be put back - restoring what is already there is
   not an action, it is a no-op with a button. */
.repo-hist{margin:10px 0 0;border-top:1px solid var(--line)}
.repo-histh{display:flex;align-items:center;gap:8px;width:100%;appearance:none;background:none;
  border:0;font:inherit;font-family:var(--f-num);font-size:12px;font-weight:700;
  letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);
  padding:10px 0 9px;cursor:pointer;text-align:left}
.repo-histh:hover{color:var(--accent)}
.repo-histh .n{margin-left:auto;letter-spacing:0;text-transform:none;font-weight:400;
  font-size:12px;color:var(--ink-3)}
.repo-histh .rev-caret{transition:transform .15s ease}
.repo-hist.open .repo-histh .rev-caret{transform:rotate(90deg)}
.repo-hist.open .repo-histh{color:var(--ink-2)}
.repo-hist-b{max-height:260px;overflow-y:auto;margin:0 -20px;padding:0 20px 8px;
  border-top:1px solid var(--line)}
.rev{border-bottom:1px solid var(--line)}
.rev:last-child{border-bottom:0}
.rev-h{display:flex;align-items:center;gap:10px;width:100%;appearance:none;background:none;
  border:0;font:inherit;color:var(--ink);padding:9px 0;cursor:pointer;text-align:left}
.rev-h:hover{color:var(--accent)}
.rev-n{font-family:var(--f-num);font-size:11.5px;font-weight:700;color:var(--ink-3);
  background:var(--surface-2);border-radius:9px;padding:1px 7px;line-height:16px;flex:none}
.rev.now .rev-n{background:#E3EBFA;color:#1E4FA3}
.rev-what{display:grid;gap:1px;min-width:0}
.rev-what b{font-size:13px;font-weight:600}
.rev-what small{font-family:var(--f-num);font-size:11.5px;color:var(--ink-3)}
.rev-now{margin-left:auto;font-family:var(--f-num);font-size:10.5px;font-weight:700;
  letter-spacing:.08em;text-transform:uppercase;color:#176A33;background:#E7F4EC;
  border-radius:8px;padding:0 6px;line-height:16px;flex:none}
.rev-caret{margin-left:auto;color:var(--ink-3);font-size:15px;line-height:1;flex:none}
.rev-now + .rev-caret{margin-left:8px}
.rev.open .rev-caret{transform:rotate(90deg)}
.rev-changes{margin:0 0 8px;padding:0 0 0 32px;list-style:none;display:grid;gap:2px}
.rev-changes li{font-size:12px;color:var(--ink-2);line-height:1.4;position:relative}
.rev-changes li::before{content:"";position:absolute;left:-11px;top:7px;width:4px;height:4px;
  border-radius:50%;background:var(--line-strong)}
.rev-b{padding:0 0 10px 32px}
.rev-name{margin:0 0 6px;font-size:13px;font-weight:600}
.rev-name small{font-weight:400;color:var(--ink-3);font-size:12px}
.rev-products{margin:0;padding:0;list-style:none;display:grid;gap:3px}
.rev-products li{display:grid;grid-template-columns:56px minmax(0,1fr);gap:8px;
  font-size:12.5px;line-height:1.4}
.rev-products .w{font-family:var(--f-num);font-weight:700;text-align:right;color:var(--ink-2)}
.rev-products .warn{color:#B45309;font-weight:600}
.rev-products.big li{font-size:13.5px;padding:3px 0;border-bottom:1px solid var(--line)}
.rev-products.big li:last-child{border-bottom:0}
.rev-put{margin-top:10px;padding:5px 11px;font-size:11.5px}
/* ── the archive and the activity feed (D66) ──
   Two more views on the same dialog, both wearing the catalogue's chrome so
   that one set of habits serves all three tables. The archive is a filing
   cabinet: a searchable table of what left the library, a batch tick, and a
   drawer for one sleeve with its history and the way back. The feed is a
   story: the record newest first, a day at a time, a sentence per change. */
.repo-seg [role="tab"] .n{margin-left:6px;font-family:var(--f-num);font-size:11px;font-weight:700;
  background:var(--surface-2);color:var(--ink-2);border-radius:8px;padding:0 6px;line-height:16px;
  display:inline-block;vertical-align:middle}
.repo-seg [role="tab"][aria-selected="true"] .n{background:rgba(255,255,255,.22);color:#FFF}

.arc-tools .cat-search,.act-tools .cat-search{flex:0 1 300px;min-width:200px}
.arc-sel{display:inline-flex;align-items:center;gap:6px;font-family:var(--f-num);font-size:12px;
  color:var(--ink-2);background:var(--surface);border:1px solid var(--line-strong);border-radius:14px;
  padding:0 4px 0 10px;height:28px}
.arc-sel>span{white-space:nowrap}
.arc-sel.on{border-color:var(--accent);background:#E3EBFA;color:#1E4FA3}
.arc-sel select{appearance:none;-webkit-appearance:none;border:0;background:transparent;font:inherit;
  font-size:12px;font-weight:700;color:inherit;padding:0 18px 0 2px;height:26px;cursor:pointer;
  max-width:190px;text-overflow:ellipsis;
  background-image:linear-gradient(45deg,transparent 50%,currentColor 50%),linear-gradient(135deg,currentColor 50%,transparent 50%);
  background-position:calc(100% - 10px) 11px,calc(100% - 6px) 11px;background-size:4px 4px,4px 4px;background-repeat:no-repeat}
.arc-sel select:focus-visible{outline:2px solid var(--accent);outline-offset:-2px;border-radius:12px}
.arc-export{margin-left:4px;padding:5px 10px;font-size:11px;text-decoration:none}
.arc-b{display:flex;flex-direction:column;min-height:0;flex:1}
.arc-tblwrap{flex:1;min-height:180px}
.arc-tbl td b{font-weight:600}
.arc-tbl td .mut,.arc-tbl td.mut{color:var(--ink-3)}
.arc-tbl .pinc input{width:14px;height:14px;margin:0;vertical-align:middle;cursor:pointer;accent-color:var(--accent)}
.arc-tbl tr.pin td{background:#F0F4FB}
.arc-tbl tr.on td{background:#E3EBFA}
.arc-tbl tr[data-arcrow]{cursor:pointer}
.arc-status{white-space:nowrap}
.arc-badge{font-family:var(--f-num);font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  border-radius:8px;padding:1px 7px;line-height:16px;display:inline-block}
.arc-badge.gone{background:#FDE8E8;color:#9B1C1C}
.arc-badge.live{background:#E7F4EC;color:#176A33}
.arc-detail{border-top:1px solid var(--line-strong);background:var(--surface);padding:14px 20px 12px;
  max-height:44%;overflow-y:auto;flex:none}
.arc-dh{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap}
.arc-dh h3{margin:0;font-size:17px;font-weight:700}
.arc-dh p{margin:2px 0 0;font-size:13px;color:var(--ink-2)}
.arc-dh .repo-prov{margin:5px 0 0}
.arc-dact{margin-left:auto;display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end}
.arc-dact .repo-problems{max-width:34ch;text-align:right}
.arc-dclose{position:static;margin-left:4px}
.arc-db{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr);gap:0 28px;margin-top:12px}
.arc-db .repo-hist{margin-top:0;border-top:0}
.arc-db .repo-hist-b{margin:0;padding:0 0 4px;max-height:220px}
.arc-lbl{padding:0 0 6px;border-bottom:0}
.arc-f .repo-src + .spacer{flex:1}
/* the feed */
.act-chips{display:inline-flex;gap:4px;flex-wrap:wrap}
.act-chip{appearance:none;background:var(--surface);border:1px solid var(--line-strong);border-radius:14px;
  font:inherit;font-family:var(--f-num);font-size:12px;font-weight:700;color:var(--ink-2);padding:0 9px;
  height:28px;cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.act-chip .n{font-weight:400;color:var(--ink-3)}
.act-chip.on{background:#E3EBFA;border-color:var(--accent);color:#1E4FA3}
.act-chip.on .n{color:#1E4FA3}
.act-chip.off{opacity:.5}
.act-chip:hover:not(.off){border-color:var(--accent)}
.act-chip:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.act-b{flex:1;overflow-y:auto;min-height:200px;background:var(--surface)}
.act-day{position:sticky;top:0;z-index:1;margin:0;padding:11px 20px 6px;font-family:var(--f-num);font-size:11px;
  font-weight:700;letter-spacing:.11em;text-transform:uppercase;color:var(--ink-3);background:var(--surface-2);
  border-bottom:1px solid var(--line)}
.act-ev{display:grid;grid-template-columns:52px minmax(0,1fr) auto;gap:14px;padding:10px 20px;
  border-bottom:1px solid var(--line);align-items:start}
.act-ev .t{font-family:var(--f-num);font-size:12px;color:var(--ink-3);padding-top:2px}
.act-ev .w p{margin:0;font-size:13.5px}
.act-ev .w p b{font-weight:600}
.act-ev .w .mut{color:var(--ink-2)}
.act-ev .w small{display:block;font-family:var(--f-num);font-size:11.5px;color:var(--ink-3);margin-top:1px}
.act-changes{margin:5px 0 0;padding-left:14px}
.act-changes li::before{left:-10px}
.act-ev .a{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.act-btn{padding:4px 9px;font-size:11px}
.act-badge{font-family:var(--f-num);font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  border-radius:8px;padding:1px 7px;line-height:16px;display:inline-block;vertical-align:middle;margin:0 2px}
.act-badge.created,.act-badge.imported,.act-badge.seeded,.act-badge.baseline{background:var(--surface-2);color:var(--ink-2)}
.act-badge.updated{background:#E3EBFA;color:#1E4FA3}
.act-badge.reverted,.act-badge.restored{background:#FEF3C7;color:#7A4A0A}
.act-badge.deleted{background:#FDE8E8;color:#9B1C1C}
.act-none{padding:24px 20px;margin:0}
.act-more{padding:14px 20px 18px;text-align:center}
/* ── the proposal register (D69) ──
   A fifth table on the same dialog, in the catalogue's chrome. The list is
   dense and named-column; the open proposal sits below it with its two
   pictures behind a segmented toggle and the workbook one click away. The
   pictures are indented tables in their own right, so a category reads as
   a group head and its assets or products as the rows under it. */
/* One width for every view that holds a table, so moving between the tabs
   never resizes the card (D115, D117). */
.dialog.repo.register{width:min(1560px,calc(100vw - 32px))}
.reg-tools .cat-search{flex:0 1 280px;min-width:180px}
.reg-tbl td b{font-weight:600}
.reg-tbl td .mut,.reg-tbl .mut{color:var(--ink-3)}
.reg-tbl tr[data-regrow]{cursor:pointer}
.reg-tbl th.num,.reg-tbl td.num{padding-right:22px}
.arc-badge.acc{background:#E3EBFA;color:#1E4FA3}
.arc-badge.mute{background:var(--surface-2);color:var(--ink-2)}
.arc-badge.warn{background:#FEF3C7;color:#7A4A0A}
.arc-badge.ok{background:#EAF5EE;color:#176A33}

/* ── the register, laid out as a list and a pane (D116) ── */
/* The saved views come before the filters: one press for the question people
   actually arrive with, and the count is worth reading before it is pressed. */
.reg-views{display:flex;gap:6px;flex-wrap:wrap;padding:8px 16px 7px;border-bottom:1px solid var(--line);
  background:var(--surface)}
.reg-view{font-family:var(--f-body);font-size:12.5px;padding:6px 11px;border-radius:14px;cursor:pointer;
  border:1px solid var(--line-strong);background:var(--surface);color:var(--ink-2);white-space:nowrap;transition:.14s}
.reg-view:hover{border-color:var(--accent);color:var(--accent)}
.reg-view b{font-family:var(--f-num);font-weight:700;margin-left:6px;color:var(--ink-3)}
.reg-view.on{background:#16243A;border-color:#16243A;color:#fff;font-weight:600}
.reg-view.on b{color:#AFC3DE}
.reg-view:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.reg-filters{border-bottom:1px solid var(--line);background:var(--surface)}
/* what the rows in force come to, before a row is read */
.reg-stat{display:flex;gap:10px 26px;flex-wrap:wrap;margin:0;padding:9px 16px;
  border-bottom:1px solid var(--line);background:var(--surface)}
.reg-stat div{min-width:0}
.reg-stat dt{font-family:var(--f-num);font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--ink-3);margin:0}
.reg-stat dd{margin:1px 0 0;font-family:var(--f-num);font-size:15px;font-weight:600;color:var(--ink)}
/* the list and the record, side by side: reading one costs no rows */
.reg-b{display:grid;grid-template-columns:minmax(0,1fr);min-height:0}
.reg-b.open{grid-template-columns:minmax(0,1.1fr) minmax(0,1fr)}
.reg-b>.reg-list{min-height:0}
.reg-pane{min-width:0;min-height:0;overflow-y:auto;border-left:1px solid var(--line-strong);background:var(--bg)}
.reg-pane .reg-detail{max-height:none;border-top:0;background:transparent}
.reg-pane .arc-dh{flex-direction:column;gap:8px}
.reg-pane .arc-dact{margin-left:0;justify-content:flex-start}
.reg-pane .reg-pic-wrap{overflow-x:auto}
/* the id everything quotes, as a control rather than text to select */
.reg-uid{font-family:var(--f-num);font-size:12.5px;letter-spacing:.02em;color:var(--ink-2);
  background:none;border:0;border-bottom:1px dashed var(--line-mid);padding:1px 0;cursor:pointer}
.reg-uid:hover{color:var(--accent);border-bottom-color:var(--accent)}
.reg-uid:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.reg-uid.copied{color:#176A33;border-bottom-color:#176A33}
.reg-uid.copied::after{content:" copied";font-size:10px;letter-spacing:.08em;text-transform:uppercase}
/* The register has no tick column to borrow a gutter from, so its first
   cell sat 10px off the card's edge. 16px puts the first column on the same
   line as the search box, the view chips and the summary above it (D118). */
.reg-tbl th:first-child,.reg-tbl td:first-child{padding-left:16px}
.reg-tbl th:last-child,.reg-tbl td:last-child{padding-right:16px}
.reg-tbl td.reg-size{line-height:1.3}
.reg-tbl td.reg-size small{display:block;font-family:var(--f-num);font-size:10.5px;color:var(--ink-3);font-weight:400}
.reg-tbl td.reg-pwa small{display:block;font-family:var(--f-num);font-size:10.5px;color:var(--ink-3)}
@media (max-width:1100px){
  /* too narrow for two columns: the record goes back under the table, where
     it is the only thing that fits */
  .reg-b.open{grid-template-columns:minmax(0,1fr)}
  .reg-pane{border-left:0;border-top:1px solid var(--line-strong);max-height:52%}
}
.reg-detail{max-height:52%}
.reg-detail .arc-dact .repo-seg{margin-left:6px}
.reg-detail .arc-dact .btn-primary{text-decoration:none}
.reg-detail .repo-prov b.warn{color:#7A4A0A;font-weight:700}
.reg-pic-wrap{margin-top:12px;overflow-x:auto}
.reg-pic{width:100%}
.reg-pic th{position:static}
.reg-pic tr.grp td{background:var(--surface-2);font-weight:600}
.reg-pic td.sub{padding-left:26px;color:var(--ink-2)}
.reg-pic td.num{font-family:var(--f-num);text-align:right;white-space:nowrap}
.reg-pic th.num{text-align:right}
/* one portfolio, two columns: stretched to the drawer the weight sits a screen away
   from its label. Outranks .cat-tbl's own width rule by specificity. */
.reg-pic-wrap table.reg-pic-one{width:auto;min-width:480px;max-width:720px}
.reg-f .repo-src + .spacer{flex:1}
/* ── the admin entry points (D62) ──
   Two glyphs, three places, one job each: the rail's utility bar is where the
   tools LIVE, the icon on the sleeve tier is the shortcut from the thing that
   needs fixing, and the landing tiles are for an admin who came to maintain
   the library rather than write a proposal. All three hidden unless the
   schema says canAdmin. */
.rail-admin-bar{position:sticky;bottom:0;margin-top:auto;z-index:2;
  display:flex;align-items:center;gap:8px;padding:9px 12px;
  background:#0E1C33;border-top:1px solid var(--rail-line)}
.rail-admin-cap{margin-right:auto;font-family:var(--f-num);font-size:10.5px;font-weight:700;
  letter-spacing:.14em;text-transform:uppercase;color:var(--rail-ink-3);white-space:nowrap}
.rail-admin-btn{width:32px;height:32px;flex:0 0 auto;display:grid;place-items:center;
  border:1px solid var(--rail-input-border,#52739C);border-radius:6px;
  color:var(--rail-ink-2);text-decoration:none;background:transparent;
  transition:background 120ms linear,color 120ms linear,border-color 120ms linear}
.rail-admin-btn svg{width:17px;height:17px;display:block}
.rail-admin-btn:hover{background:var(--rail-input-bg,#1D2F4B);color:#fff;border-color:#5590E4}
.rail-admin-btn:focus-visible{outline:2px solid var(--rail-focus,#8FB4FF);outline-offset:2px}
/* Collapsed, the bar is the only thing in the rail with anything to say: the
   caption goes and the glyphs stack down the 48px strip. */
body.rail-collapsed .rail-admin-cap{display:none}
body.rail-collapsed .rail-admin-bar{flex-direction:column;gap:6px;padding:9px 0}
body.rail-collapsed .rail-admin-btn{width:30px;height:30px}
/* the shortcut on the sleeve tier's own heading */
.tier-admin{width:24px;height:24px;flex:0 0 auto;display:grid;place-items:center;padding:0;
  border:1px solid transparent;border-radius:5px;background:none;color:var(--rail-ink-3);cursor:pointer}
.tier-admin svg{width:15px;height:15px;display:block}
.tier-admin:hover{color:#fff;border-color:var(--rail-input-border,#52739C)}
.tier-admin:focus-visible{outline:2px solid var(--rail-focus,#8FB4FF);outline-offset:1px}
.tier-h-r{display:flex;align-items:center;gap:9px}
/* ── the landing page's way into the library (D106) ──
   Aligned to the hero's own measure rather than bled to the edges: it is a
   footnote to the page, not a second surface laid over it. The role comes
   first and the destinations follow, so the strip explains its own presence
   before it offers anything.

   Everything here is inside #lpadmin, which is hidden outright for a
   non-admin, so none of it can leave a gap behind. */
/* In the landing grid's foot row, so it lines up with the copy above but
   sits at the bottom of the page rather than 38px under the last line of it.
   The margin is a floor for a short viewport, not the spacing itself (D108). */
.lp-admin{display:flex;align-items:center;gap:10px 22px;flex-wrap:wrap;
  width:100%;max-width:1120px;margin:56px auto 0;padding:14px 0 0;
  border-top:1px solid var(--line-strong)}
.lp-admin[hidden]{display:none}
.lp-admin-who{display:flex;align-items:center;gap:9px;font-size:13.5px;color:var(--ink-2)}
.lp-admin-who b{color:var(--ink);font-weight:600}
.lp-admin-role{font-family:var(--f-num);font-size:9.5px;font-weight:700;letter-spacing:.13em;
  text-transform:uppercase;background:#16243A;color:#fff;border-radius:3px;padding:2px 7px;white-space:nowrap}
/* The destinations sit at the far end, so the eye reads role first and the
   two groups cannot be mistaken for one list. */
.lp-admin-links{display:flex;align-items:center;gap:7px 14px;flex-wrap:wrap;margin-left:auto}
.lp-admin-dot{color:var(--line-mid)}
.lp-admin-links a{font-size:13.5px;color:var(--ink-2);text-decoration:none;
  border-bottom:1px solid transparent;padding-bottom:1px;transition:color 120ms linear}
.lp-admin-links a:hover{color:var(--accent);border-bottom-color:rgba(31,95,191,.4)}
.lp-admin-links a:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:2px}
/* Narrow, the two groups stack and the destinations lead: on a tablet the
   role is reassurance and the links are the reason the strip is there. */
@media (max-width:640px){
  .lp-admin{gap:9px;margin:36px auto 0}
  .lp-admin-links{margin-left:0;order:-1;width:100%;gap:6px 16px}
  .lp-admin-links a{font-size:14.5px}
  /* The separators go once the row can wrap: a dot is bound to its
     neighbours by nothing, so a wrap leaves one stranded at the end of a
     line. The gap separates them well enough on its own. */
  .lp-admin-dot{display:none}
}
/* ── the catalogue view (D58), laid out as the terminal (D63) ──
   A facet rail, a dense table with the figures on the right, a tray of pins
   that opens into a comparison, a side panel on demand. Density is the
   point; colour is only ever a constraint (liquidity, a minimum above the
   mandate, a product no sleeve holds) or a selection. */
.dialog.repo.catalogue{width:min(1560px,calc(100vw - 32px))}
.cat-tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:7px 16px;
  border-bottom:1px solid var(--line);background:var(--surface-2)}
.cat-search{display:flex;align-items:center;gap:6px;border:1px solid var(--line-strong);border-radius:4px;
  background:var(--surface);padding:0 8px;width:270px;color:var(--ink-3)}
.cat-search input{border:0;background:none;font:inherit;font-size:13px;color:var(--ink);padding:6px 0;width:100%;outline:none}
.cat-search input::-webkit-search-cancel-button{-webkit-appearance:none}
.cat-search kbd{font-family:var(--f-num);font-size:10.5px;border:1px solid var(--line-strong);border-radius:3px;
  padding:0 5px;color:var(--ink-3);background:var(--surface-2)}
.cat-search:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px rgba(31,95,191,.18)}
.cat-density button{font-family:var(--f-num);font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding:5px 10px}
.cat-chipwrap{position:relative}
.cat-chip{appearance:none;display:inline-flex;align-items:center;gap:5px;border:1px solid var(--line-strong);
  border-radius:14px;background:var(--surface);padding:3px 10px;font-family:var(--f-num);font-size:12px;
  color:var(--ink-2);cursor:pointer;white-space:nowrap;line-height:1.4}
.cat-chip:hover{border-color:var(--ink-3)}
.cat-chip.on{background:#E3EBFA;border-color:#B9CDF0;color:#1E4FA3;font-weight:700}
.cat-chip b{color:var(--ink);font-weight:700}
.cat-chip .car{font-size:9px;color:var(--ink-3)}
.cat-menu{position:absolute;left:0;top:calc(100% + 4px);min-width:210px;background:var(--surface);
  border:1px solid var(--line-strong);border-radius:6px;box-shadow:0 10px 30px rgba(12,24,44,.18);
  z-index:6;padding:6px 0;display:grid}
.cat-opt{display:grid;grid-template-columns:auto 1fr;gap:8px;align-items:center;padding:4px 12px;
  font-size:13px;cursor:pointer;color:var(--ink)}
.cat-opt.fixed{color:var(--ink-3)}
.cat-opt:hover{background:var(--surface-2)}
.cat-clear{appearance:none;background:none;border:0;padding:0;font:inherit;font-family:var(--f-num);font-size:12px;
  color:#1E4FA3;cursor:pointer;text-decoration:underline}
.cat-menu .cat-clear{margin:6px 12px 2px;justify-self:start}
.cat-count{margin-left:auto;font-family:var(--f-num);font-size:12px;color:var(--ink-3);white-space:nowrap}
.cat-orphans{gap:6px 14px}
.cat-orphan{font-size:12.5px}
/* the body: rail, table (or comparison), and the panel over the right edge */
.cat-b{display:grid;grid-template-columns:208px minmax(0,1fr);min-height:0;position:relative}
.cat-facets{border-right:1px solid var(--line);overflow:auto;min-height:0;padding:6px 0 12px;background:var(--surface)}
.cat-fh{margin:0;padding:10px 14px 4px;font-family:var(--f-num);font-size:10.5px;font-weight:700;letter-spacing:.14em;
  text-transform:uppercase;color:var(--ink-3)}
.cat-fo{display:flex;align-items:center;gap:8px;padding:3px 14px;font-size:12.5px;color:var(--ink);cursor:pointer;line-height:1.35}
.cat-fo:hover{background:var(--surface-2)}
.cat-fo input{margin:0;width:12px;height:12px;flex:0 0 auto;accent-color:var(--accent)}
.cat-fo .v{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cat-fo .n{margin-left:auto;font-family:var(--f-num);font-size:11.5px;color:var(--ink-3)}
.cat-fo.on{color:#1E4FA3;font-weight:600}
.cat-fo.on .n{color:#1E4FA3}
.cat-fo.off{color:var(--ink-3);cursor:not-allowed}
.cat-facets-clear{margin:12px 14px 0}
.cat-tblwrap{overflow:auto;min-height:0}
.cat-tblwrap:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
/* the catalogue's own table (the archive and the register borrow the class):
   the header, and banded the band under it, stand over the rows, and a row
   the keyboard scrolls to has to come to rest clear of them */
.cat-tblwrap.cat-main{scroll-padding-top:30px}
.cat-tblwrap.cat-main.grouped{scroll-padding-top:62px}
/* a whole number of pixels, where the type alone comes to 29.6: the bands
   hold under it at a fixed offset, and rows that start between pixels blur
   their rules */
.cat-main .cat-tbl thead th{height:30px}
.cat-tbl{border-collapse:separate;border-spacing:0;width:100%;font-size:12.5px}
.cat-tbl.comfortable{font-size:13px}
.cat-tbl th{position:sticky;top:0;background:var(--surface-2);border-bottom:1px solid var(--line-strong);
  padding:0;white-space:nowrap;z-index:2;text-align:left}
.cat-tbl th.num,.cat-tbl td.num{text-align:right}
.cat-tbl th.num .cat-sort{justify-content:flex-end}
.cat-tbl th.pinc,.cat-tbl td.pinc{width:30px;padding:0 0 0 10px;position:sticky;left:0;z-index:3;background:var(--surface-2)}
.cat-tbl td.pinc{background:var(--surface);z-index:1}
.cat-sort{appearance:none;background:none;border:0;width:100%;display:flex;align-items:baseline;gap:4px;font-family:var(--f-num);
  font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);font-weight:700;padding:8px 10px;
  cursor:pointer;text-align:inherit;line-height:1.2}
.cat-sort small{font-size:9px;letter-spacing:.06em;color:var(--ink-3);font-weight:400}
.cat-sort:hover{color:var(--ink)}
.cat-sort.on{color:#1E4FA3}
.cat-sort.drv{font-style:italic;color:var(--ink-2)}
/* a header says it sorts before it has (D99): the arrows hang off the label,
   out of the flow, so a column is no wider for being pointed at */
.cat-sort .lb{position:relative;display:inline-flex;align-items:baseline;gap:4px}
.cat-sort:not(.on) .lb::after{content:"↕";position:absolute;left:calc(100% + 2px);top:0;opacity:0;font-size:10px;
  letter-spacing:0;font-weight:400;font-style:normal;transition:opacity 120ms linear}
.cat-tbl th:hover .cat-sort:not(.on) .lb::after,.cat-sort:not(.on):focus-visible .lb::after{opacity:.75}
.cat-sort:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.cat-tbl td{padding:0 10px;border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:middle;color:var(--ink);
  height:34px}
.cat-tbl.comfortable td{height:40px}
.cat-tbl td.num{font-family:var(--f-num);font-variant-numeric:tabular-nums;font-size:12.5px}
.cat-tbl.comfortable td.num{font-size:13px}
.cat-tbl td.tk{font-family:var(--f-num);font-weight:700;letter-spacing:.02em}
.cat-tbl td.nm{white-space:nowrap;min-width:200px;position:sticky;left:40px;background:var(--surface);z-index:1;
  font-weight:500;box-shadow:inset -1px 0 0 var(--line)}
.cat-tbl td.nm b{font-weight:500}
.cat-tbl td.mute{color:var(--ink-3)}
/* ── the table says what it can do (D99) ──
   Rows open a panel, boxes pin, headers sort and the keyboard does all of
   it; this is where that shows. One order of precedence, held throughout:
   the sorted column's tint is the weakest mark, then the cursor, then a pin,
   then the open row - and the pointer, while it is there, over all of them. */
/* the chevron at the row's end: this opens. Pinned to the right edge, as
   the pin box and the name are to the left. */
.cat-tbl th.go,.cat-tbl td.go{position:sticky;right:0;width:30px;min-width:30px;padding:0 9px 0 0;text-align:right}
.cat-tbl td.go{background:var(--surface);z-index:1}
.cat-tbl td.go span{opacity:0;color:var(--accent);font-size:17px;font-weight:700;line-height:1;transition:opacity 120ms linear}
.cat-tbl tr[data-catrow]:hover td.go span,.cat-tbl tr.cur td.go span,.cat-tbl tr.on td.go span{opacity:1}
/* the header over the bands, and its two pinned corners over the header */
.cat-tbl thead th{z-index:3}
.cat-tbl thead th.pinc,.cat-tbl thead th.go{z-index:4}
/* the sorted column, its whole height: found from anywhere in the table,
   not only from its header. Opaque, because the name column is pinned and
   rows pass beneath it. */
.cat-tbl td.srt{background-color:#F3F7FD}
.cat-tbl th.srt{box-shadow:inset 0 -2px 0 var(--accent)}
.cat-tbl tr[data-catrow]{cursor:pointer}
.cat-tbl tr[data-catrow]:hover td{background-color:#EAF1FB}
.cat-tbl tr[data-catrow]:not(.pin):not(.cur):hover td.pinc{box-shadow:inset 3px 0 0 var(--line-mid)}
.cat-tbl tr[data-catrow]:hover .pinbox{border-color:var(--accent)}
/* The cursor's two lines are strips of background, not inset shadows: the
   columns' widths are fractions of a pixel, and an inset shadow on a cell
   that starts between pixels leaks a hairline of its colour down the cell's
   side - a rule between every column of the one row the eye is on. The
   states below set a colour and nothing else, so the strips survive them. */
.cat-tbl tr.cur td{background-color:#F5F8FD;box-shadow:none;
  background-image:linear-gradient(var(--accent),var(--accent)),linear-gradient(var(--accent),var(--accent));
  background-size:100% 1px;background-position:0 0,0 100%;background-repeat:no-repeat}
.cat-tbl tr.cur td.pinc{box-shadow:inset 3px 0 0 var(--accent)}
.cat-tbl tr.pin td{background-color:#F0F4FB}
.cat-tbl tr.pin td.pinc{box-shadow:inset 3px 0 0 var(--accent)}
.cat-tbl tr.on td{background-color:#E3EBFA}
/* the open row stays the open row under the pointer: deeper, never paler */
.cat-tbl tr.on[data-catrow]:hover td{background-color:#DCE6F8}
/* the keyboard's row is the cursor's row, and the cursor's marks are its focus ring */
.cat-tbl tr[data-catrow]:focus{outline:none}
.cat-tbl tr.dim td:not(.pinc){color:var(--ink-3)}
.cat-tbl tr.dim td.nm b{color:var(--ink-2)}
.cat-tbl td.pos{color:#176A33}
.cat-tbl td.neg{color:#9B1C1C}
.cat-tbl .veh{display:inline-block;font-family:var(--f-num);font-size:10.5px;font-weight:700;color:var(--ink-2);
  background:var(--surface-2);border-radius:3px;padding:0 5px;line-height:16px;letter-spacing:.04em}
.liq{display:inline-block;font-family:var(--f-num);font-size:11px;font-weight:700;letter-spacing:.04em;color:var(--ink-2);
  background:var(--surface-2);border-radius:8px;padding:0 7px;line-height:16px}
.liq.slow{color:#92600A;background:#FEF3C7}
.liq.lock{color:#9B1C1C;background:#FDE8E8}
.flag{display:inline-block;font-family:var(--f-num);font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  color:#92600A;background:#FEF3C7;border-radius:8px;padding:0 6px;line-height:15px;margin-left:6px;vertical-align:1px}
.cat-tbl td.used.zero{color:#B45309;font-weight:700}
.cat-tbl td.cat-empty{white-space:normal;padding:22px 16px;color:var(--ink-2);font-size:13.5px;position:static}
.pinbox{appearance:none;width:14px;height:14px;border:1px solid var(--line-strong);border-radius:3px;background:var(--surface);
  cursor:pointer;padding:0;display:block;position:relative}
.pinbox.on{background:var(--accent);border-color:var(--accent)}
.pinbox.on::after{content:"";position:absolute;left:4px;top:1px;width:4px;height:8px;border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg)}
.pinbox:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
/* the shadow that says the table carries on to the right (D99): over the
   rows rather than behind them, so a pinned or pointed-at row does not break
   it, and placed by the page against the chevron column and any scrollbar */
.cat-more{position:absolute;top:0;bottom:0;right:30px;width:14px;pointer-events:none;z-index:3;opacity:0;
  background:radial-gradient(farthest-side at 100% 50%,rgba(12,24,44,.24),rgba(12,24,44,0));transition:opacity 150ms linear}
.cat-more.on{opacity:1}
/* ── rows banded by sleeve category (D100) ──
   A band holds under the header until the next arrives. It laps the header
   by a pixel, and sits beneath it, so no display scaling can open a gap for
   the rows to show through. */
.cat-tbl tr.cat-band{cursor:pointer}
.cat-tbl tr.cat-band th{top:29px;z-index:2;height:32px;padding:0;background:#E9EEF5;text-align:left;
  border-bottom:1px solid var(--line-strong);box-shadow:inset 0 1px 0 var(--line-strong)}
.cat-tbl tr.cat-band:hover th{background:#DFE6F0}
.cat-fold{appearance:none;border:0;background:none;font:inherit;color:var(--ink);cursor:pointer;
  position:sticky;left:0;display:inline-flex;align-items:baseline;gap:12px;padding:0 12px;height:31px;line-height:31px}
.cat-fold:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.cat-fold b{font-weight:600;font-size:13px}
.cat-fold .n,.cat-fold .rng{font-family:var(--f-num);font-size:12px;color:var(--ink-2)}
.cat-fold .cst{font-family:var(--f-num);font-size:11px;font-weight:700;color:#92600A;background:#FEF3C7;border-radius:8px;
  padding:0 7px;line-height:16px}
.cat-fold .car{display:inline-block;width:10px;font-size:11px;color:var(--ink-2);transition:transform 150ms linear}
.cat-band.shut .car{transform:rotate(-90deg)}
/* the keys, above the tray (D99) */
.cat-keys{flex:0 0 auto;display:flex;align-items:center;gap:3px 16px;flex-wrap:wrap;padding:5px 16px;
  border-top:1px solid var(--line-strong);background:var(--surface-2);font-size:12px;color:var(--ink-2)}
.cat-keys .ttl{font-family:var(--f-num);font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--ink)}
.cat-keys .k{white-space:nowrap}
.cat-keys kbd{font-family:var(--f-num);font-size:10.5px;border:1px solid var(--line-strong);border-radius:3px;padding:0 5px;
  color:var(--ink);background:var(--surface);margin-right:4px}
/* the tray, in the footer */
.repo-f.cat-f{padding:8px 16px;gap:12px}
.cat-keys + .repo-f.cat-f{border-top-color:var(--line)}
.cat-tray{display:flex;align-items:center;gap:10px;flex:1;min-width:0}
.cat-tray .ttl{font-family:var(--f-num);font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--ink)}
.cat-tray .pins{display:flex;gap:6px;flex-wrap:wrap}
.cat-tray .pn{appearance:none;border:1px solid #B9CDF0;background:#E3EBFA;color:#1E4FA3;border-radius:4px;padding:2px 8px;
  font-family:var(--f-num);font-size:11.5px;font-weight:700;cursor:pointer}
.cat-tray .pn:hover{background:#D3DFF7}
.cat-tray .none{font-size:12.5px;color:var(--ink-3)}
.cat-tray .none kbd{font-family:var(--f-num);font-size:10.5px;border:1px solid var(--line-strong);border-radius:3px;padding:0 4px}
.cat-tray .cnt{font-family:var(--f-num);font-size:12px;color:var(--ink-3);white-space:nowrap}
.cat-tray .spacer{flex:1}
.cat-src{white-space:nowrap}
/* the comparison, in the table's place */
.cat-cmpwrap{overflow:auto;min-height:0;padding:14px 18px}
.cat-cmp{border-collapse:collapse;font-size:12.5px;min-width:520px}
.cat-cmp th{font-family:var(--f-num);font-size:11px;letter-spacing:.04em;color:var(--ink);padding:8px 12px;text-align:right;
  border-bottom:1px solid var(--line-strong);vertical-align:bottom;position:relative;min-width:120px}
.cat-cmp th:first-child{text-align:left;min-width:150px}
.cat-cmp th .t{display:block;font-size:12.5px;font-weight:700}
.cat-cmp th small{display:block;font-weight:400;color:var(--ink-3);font-size:10.5px;white-space:normal;max-width:160px;margin-left:auto}
.cat-unpin{position:absolute;top:2px;right:2px;appearance:none;border:0;background:none;color:var(--ink-3);font-size:14px;cursor:pointer;line-height:1}
.cat-unpin:hover{color:#9B1C1C}
.cat-cmp td{padding:6px 12px;border-bottom:1px solid var(--line);text-align:right;font-family:var(--f-num);
  font-variant-numeric:tabular-nums;color:var(--ink)}
.cat-cmp td:first-child{text-align:left;font-family:var(--f-body);color:var(--ink-2);font-size:12px}
.cat-cmp td.best{font-weight:700;color:#1E4FA3;background:#F0F4FB}
.cat-cmp tr.sec td{background:var(--surface-2);font-family:var(--f-num);font-size:10px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--ink-3);padding-top:9px;text-align:left}
.cat-cmp-key{margin:12px 0 0;font-size:12px;color:var(--ink-3);max-width:70ch}
.cat-cmp-empty{padding:30px 20px;color:var(--ink-3);font-size:13.5px}
/* the panel: over the right edge, not in the layout */
.cat-detail{position:absolute;top:0;right:0;bottom:0;width:340px;background:var(--surface);border-left:1px solid var(--line-strong);
  box-shadow:-12px 0 30px rgba(12,24,44,.14);padding:16px 18px;display:flex;flex-direction:column;gap:12px;font-size:12.5px;
  overflow:auto;z-index:4}
.cat-detail .dlg-close{top:8px;right:10px}
.cat-detail .eyeb{font-family:var(--f-num);font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-3)}
.cat-detail h4{margin:0;font-size:16px;font-weight:600;line-height:1.25;color:var(--ink);padding-right:28px}
.cat-detail .tk{font-family:var(--f-num);font-size:12px;color:var(--ink-2);word-break:break-all}
.cat-mg{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
.cat-mg .m{border:1px solid var(--line);border-radius:5px;padding:7px 9px}
.cat-mg .m small{display:block;font-family:var(--f-num);font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.cat-mg .m b{font-family:var(--f-num);font-size:16px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--ink)}
.cat-dl{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;font-size:12.5px;margin:0}
.cat-dl dt{font-family:var(--f-num);font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);padding-top:1px}
.cat-dl dd{margin:0;color:var(--ink)}
.cat-usedin{border:1px solid var(--line);border-radius:6px;overflow:hidden}
.cat-usedin .h{font-family:var(--f-num);font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:var(--ink-3);padding:7px 10px;background:var(--surface-2);border-bottom:1px solid var(--line)}
.cat-usedin .r{display:grid;gap:2px;padding:8px 10px;border-bottom:1px solid var(--line);font-size:12.5px}
.cat-usedin .r:last-child{border-bottom:0}
.cat-usedin .r b{font-weight:500;color:var(--ink)}
.cat-usedin .r small{font-family:var(--f-num);font-size:11.5px;color:var(--ink-2)}
.cat-usedin .r.none{color:var(--ink-3)}
.cat-link{appearance:none;background:none;border:0;padding:0;font-family:var(--f-num);font-size:11px;letter-spacing:.06em;
  text-transform:uppercase;color:#1E4FA3;cursor:pointer;text-align:left;justify-self:start}
.cat-link:hover{text-decoration:underline}
.cat-detail .acts{display:flex;gap:8px;margin-top:auto;padding-top:6px;flex-wrap:wrap}
@media (max-width:1320px){
  .repo-h .repo-src{display:none}
}
@media (max-width:900px){
  .repo-b{grid-template-columns:180px 220px minmax(0,1fr)}
  .cat-b{grid-template-columns:180px minmax(0,1fr)}
}
"""


def _asset(name):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, 'js', name), encoding='utf-8') as fh:
        return fh.read()


REPO_JS = _asset('repository.js')
