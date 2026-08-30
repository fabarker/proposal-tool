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
        try {
            var body = await resp.json();
            if (body && body.loginUrl) { window.location = body.loginUrl; return; }
            if (body && body.error) msg = body.error;
        } catch (e) { /* non-JSON error body */ }
        throw new Error(msg);
    }
    return resp.json();
}


var App = (function(){
'use strict';
var RISKS = [["LV","Low Vol",10],["C","Cons",25],["CM","Cons Mod",38],["M","Mod",45],
             ["MA","Mod Agg",58],["A","Agg",72],["AE","All Equity",98]];
var VARIANTS = [
  {id:"full-re", aa:"Full",    re:true,  label:"Full + RE"},
  {id:"full",    aa:"Full",    re:false, label:"Full"},
  {id:"exhf-re", aa:"Ex HFs",  re:true,  label:"Ex HFs + RE"},
  {id:"exhf",    aa:"Ex HFs",  re:false, label:"Ex HFs"},
  {id:"core",    aa:"Core",    re:false, label:"Core"},
  {id:"exalts",  aa:"Ex Alts", re:false, label:"Ex Alts"}
];
/* combinations with no stored portfolio: never selectable, so no-match cannot occur */
var MISSING = {"exalts|LV":1,"exalts|C":1,"full-re|AE":1,"exhf-re|AE":1,"full|LV":1,
               "exhf|LV":1,"full-re|LV":1,"exhf-re|LV":1};
var CAP = 3;
/* Allocation variants differ by which alternatives they admit. Every variant holds
   bonds and equities.
     Full     hedge funds + private equity  (+ real estate when the variant carries it)
     Ex HFs   private equity only           (+ real estate when the variant carries it)
     Core     hedge funds only
     Ex Alts  neither */
var CCY_FACTOR = {USD:1.00, CHF:0.88, GBP:1.04, EUR:0.94};
var HEDGE_VOL  = {"Hedged":1.00,"ISG Hedged":1.02,"Unhedged":1.12,"Equity Not Hedged":1.07};
var PWAS = ["M. Aldridge \u2014 Zurich","R. Considine \u2014 London","S. Ferreira \u2014 Geneva",
            "T. Okonjo \u2014 London","H. Lindqvist \u2014 Zurich","D. Baumgartner \u2014 Zug"];

/* phase: the page is its own landing screen. "landing" shows the front door and
   hides the rail; "workspace" is everything else. The mandate dialog sits over
   whichever is showing. */
var phase = "landing";
var mandate = {top:null, size:null, pwa:"", editing:false, err:null, field:null};
var draft = null;                    /* in-flight dialog values */
var basis   = {ccy:"USD", hedge:"Hedged"};
var BASE    = {v:"core", r:"M", taa:false};
var ALLOCATIONS = ["Full","Core","Ex HFs","Ex Alts"];
function reAllowed(aa){ return aa === "Full" || aa === "Ex HFs"; }
/* map an allocation + "exclude RE" tick back onto the six stored variants */
function variantFor(aa, exRE){
  for(var i=0;i<VARIANTS.length;i++){
    var v = VARIANTS[i];
    if(v.aa === aa && v.re === (reAllowed(aa) ? !exRE : false)) return v.id;
  }
  return VARIANTS[0].id;
}

/* The seven real categories. Real Estate is not one of them: it sits inside
   Other Private Assets, which is what Include RE governs. */
var CATS = [
  {name:"Cash, Deposits & Money Market Funds", key:"cash",
    split:[["Cash & Deposits",.58],["Money Market Funds",.42]]},
  {name:"Investment Grade Fixed Income", key:"igfi",
    split:[["Government Bonds",.42],["Investment Grade Credit",.38],["Municipals",.20]]},
  {name:"Other Fixed Income", key:"ofi",
    split:[["High Yield Credit",.62],["Emerging Market Debt",.38]]},
  {name:"Public Equity", key:"equity",
    split:[["US Equity",.49],["Europe Equity",.20],["Japan Equity",.09],["Emerging Market Equity",.22]]},
  {name:"Hedge Funds", key:"hf",
    split:[["Multi-Strategy",.56],["Equity Long/Short",.44]]},
  {name:"Private Equity", key:"pe",
    split:[["Buyout",.62],["Growth & Venture",.38]]},
  {name:"Other Private Assets", key:"opa",
    split:[["Real Estate",.46],["Infrastructure",.30],["Private Credit",.24]]}
];
var CRISES = ["Global Financial Crisis 2008","European Debt Crisis 2011",
              "COVID-19 Drawdown 2020","Rates Repricing 2022"];
var CF = [1.00,.41,.60,.52];
var PREMIA = [["Value at Risk · 99% · 1 Month",.62],["Value at Risk · 99% · 1 Year",1.55],
              ["Value at Risk · 99% · 3 Years",2.02],["Conditional VaR · 99% · 1 Year",1.92]];

function key(v,r){ return v+"|"+r; }
function risk(r){ for(var i=0;i<RISKS.length;i++){ if(RISKS[i][0]===r) return RISKS[i]; } }
function variant(v){ for(var i=0;i<VARIANTS.length;i++){ if(VARIANTS[i].id===v) return VARIANTS[i]; } }
function available(v,r){ return !MISSING[key(v,r)]; }

function model(vId,rId,taa){
  var v = variant(vId), eq = risk(rId)[2];
  taa = !!taa;                       /* taa === true means TACTICAL ASSET ALLOCATION EXCLUDED */
  var hf = (v.aa==="Full" || v.aa==="Core")   ? eq*0.135 : 0;
  var pe = (v.aa==="Full" || v.aa==="Ex HFs") ? eq*0.175 : 0;
  var re = v.re ? Math.max(4, eq*0.125) : 0;
  var equity = eq - (hf+pe)*0.55 - re*0.35;
  var cash = Math.max(1.2, 4.2 - eq*0.022);
  var fi = 100 - equity - hf - pe - re - cash;
  if(fi < 0.5){ equity += (fi-0.5); fi = 0.5; }
  var k = 100/(equity+hf+pe+re+cash+fi);
  var w = {cash:cash*k, igfi:fi*k*0.74, ofi:fi*k*0.26, equity:equity*k,
           hf:hf*k, pe:pe*k, opa:re*k};
  /* which categories the variant admits at all, independent of weight */
  var has = {cash:true, igfi:true, ofi:true, equity:true,
             hf:(v.aa==="Full"||v.aa==="Core"),
             pe:(v.aa==="Full"||v.aa==="Ex HFs"),
             opa:!!v.re};
  var wAlts = w.hf + w.pe, wFixed = w.igfi + w.ofi;
  var ret = 1.55 + w.equity*0.0555 + wAlts*0.036 + w.opa*0.020 + wFixed*0.012;
  var vol = 1.05 + w.equity*0.1665 + wAlts*0.062 + w.opa*0.055 + wFixed*0.016;
  /* "ex RE" applies only where real estate was possible but excluded, i.e. Full and
     Ex HFs. Core and Ex Alts can never hold it, so they never carry the suffix. */
  var suffix = (!v.re && (v.aa==="Full"||v.aa==="Ex HFs")) ? " ex RE" : "";
  if(taa) suffix += " ex TAA";
  /* the tactical overlay adds a little active return and a little active risk;
     excluding it steps both back. Placeholder — the real effect comes from the data. */
  if(taa){ ret -= 0.14; vol -= 0.22; }
  ret *= (CCY_FACTOR[basis.ccy] || 1);
  vol *= (HEDGE_VOL[basis.hedge] || 1);
  return {v:vId, r:rId, taa:taa, w:w, has:has, ret:ret, vol:vol,
          sharpe:(ret-2.0*(CCY_FACTOR[basis.ccy]||1))/vol,
          name:v.aa+" "+risk(rId)[1]+suffix,
          full:basis.ccy+" "+v.aa+" "+risk(rId)[1]+suffix};
}
function pct(x){ return (x < 0.05) ? "—" : x.toFixed(1)+"%"; }
function sgn(x){ return x.toFixed(2)+"%"; }

var portfolios = [model(BASE.v, BASE.r, BASE.taa)];
var picks = [];
var picker = null;
var extras = [];
var step = "aa";

function used(v,r,taa){ return portfolios.some(function(p){
  return p.v===v && p.r===r && p.taa===!!taa; }); }
function picked(v,r){ return picks.findIndex(function(p){ return p.v===v && p.r===r; }); }
function remaining(){ return CAP - (portfolios.length-1) - picks.length; }
function slotsLeft(){ return CAP - (portfolios.length-1); }
function selectable(v,r,taa){ return available(v,r) && !used(v,r,taa); }

function togglePick(v,r){
  var i = picked(v,r);
  if(i>=0){ picks.splice(i,1); }
  else { if(remaining()<=0 || !selectable(v,r)) return false; picks.push({v:v,r:r}); }
  refresh(); return true;
}
function addPick(v,r){
  if(remaining()<=0 || !selectable(v,r) || picked(v,r)>=0) return false;
  picks.push({v:v,r:r}); refresh(); return true;
}
function removePick(i){ picks.splice(i,1); refresh(); }
function setPicks(list){
  picks = [];
  list.forEach(function(p){
    if(p && p.v && p.r && selectable(p.v,p.r) && picked(p.v,p.r)<0 && picks.length<CAP)
      picks.push({v:p.v,r:p.r});
  });
  refresh();
}
function commit(){
  picks.forEach(function(p){ portfolios.push(model(p.v,p.r,p.taa)); });
  picks = []; refresh();
  document.querySelectorAll(".tblwrap").forEach(function(w){
    w.scrollTo({left:w.scrollWidth, behavior:"smooth"});
  });
}
function addPortfolio(v,r,taa){
  if(slotsLeft()<=0 || !selectable(v,r,taa)) return false;
  portfolios.push(model(v,r,taa));
  App.lastAdded = portfolios.length-1;
  refresh();
  document.querySelectorAll(".tblwrap").forEach(function(w){
    w.scrollTo({left:w.scrollWidth, behavior:"smooth"});
  });
  setTimeout(function(){ App.lastAdded = -1; refresh(); }, 1400);
  return true;
}
function removePortfolio(i){ if(i>0){ portfolios.splice(i,1); App.lastAdded=-1; refresh(); } }

/* Editing the scenario basis rebuilds every column against the new currency and
   hedging, keeping each column's allocation and risk level. */
function setBasis(ccy, hedge){
  basis.ccy = ccy; basis.hedge = hedge;
  portfolios = portfolios.map(function(p){ return model(p.v, p.r, p.taa); });
  App.lastAdded = -1; refresh();
}
/* Changing the base replaces column one. A comparison that now duplicates it is dropped. */
function setBase(v, r, taa){
  if(!available(v,r)) return false;
  taa = !!taa;
  BASE.v = v; BASE.r = r; BASE.taa = taa;
  portfolios = [model(v,r,taa)].concat(
    portfolios.slice(1).filter(function(p){
      return !(p.v===v && p.r===r && p.taa===taa); }));
  picks = []; App.lastAdded = -1; refresh(); return true;
}
function validateMandate(top, size){
  if(!(size >= 5000000)) return "Mandate size must be at least $5,000,000.";
  if(size > top) return "Mandate size cannot exceed the top account size.";
  return null;
}
function setMandate(top, size, pwa){
  var err = validateMandate(top, size);
  mandate.err = err;
  if(err) { refresh(); return false; }
  mandate.top = top; mandate.size = size; mandate.pwa = pwa; mandate.editing = false;
  refresh(); return true;
}

/* ── shared rail chrome ── */
function money(n){
  return (n === null || n === undefined || isNaN(n))
    ? "\u2014" : "$" + Math.round(n).toLocaleString("en-US");
}
function parseMoney(v){ return Math.round(Number(String(v).replace(/[^0-9.]/g,"")) || 0); }

function renderPhase(){
  var landing = document.getElementById("view-landing");
  var aa = document.getElementById("view-aa");
  var impl = document.getElementById("view-impl-wrap");
  var steps = document.getElementById("steps");
  if(landing) landing.hidden = (phase !== "landing");
  if(steps) steps.hidden = (phase === "landing");
  if(phase === "landing"){
    if(aa) aa.hidden = true;
    if(impl) impl.hidden = true;
  }
  document.body.classList.toggle("phase-landing", phase === "landing");
}

function renderMandate(){
  var el = document.getElementById("tier-mandate"); if(!el) return;
  /* no mandate yet: the rail is hidden on the landing phase, so render nothing
     rather than a summary full of dashes */
  if(mandate.top === null){ el.innerHTML = ""; return; }
  el.innerHTML = '<div class="tier-h"><h3>Mandate</h3>'
    + '<button type="button" class="tier-edit" id="mdedit">Edit</button></div>'
    + '<p class="summary">Top account '+money(mandate.top)
    + '<span>Mandate '+money(mandate.size)+'</span>'
    + '<span>'+mandate.pwa+'</span></p>';
}

/* ── mandate dialog ──────────────────────────────────────────────────────────
   Used for the first entry from the landing screen and for Edit afterwards. The
   only difference is the button label and whether Cancel returns to the landing. */
var dlgOpener = null;

function openMandateDialog(opener){
  dlgOpener = opener || null;
  draft = {top: mandate.top, size: mandate.size, pwa: mandate.pwa,
           q: mandate.pwa || "", open: false, idx: -1, err: null, field: null};
  renderDialog();
  var f = document.getElementById("mdtop");
  if(f) f.focus();
}
function closeMandateDialog(){
  draft = null; renderDialog();
  if(dlgOpener && dlgOpener.isConnected) dlgOpener.focus();
  else { var s = document.getElementById("startbtn"); if(s) s.focus(); }
}
function advisorMatches(q){
  q = (q || "").trim().toLowerCase();
  if(q.length < 2) return null;                       /* below the threshold */
  return PWAS.filter(function(p){ return p.toLowerCase().indexOf(q) >= 0; });
}
function validateDraft(){
  if(!(draft.top > 0)) return {field:"mdtop", msg:"Enter the top account size."};
  if(!(draft.size >= 5000000))
    return {field:"mdsize", msg:"Mandate size must be at least $5,000,000."};
  if(draft.size > draft.top)
    return {field:"mdsize", msg:"Mandate size cannot exceed the top account size."};
  if(PWAS.indexOf(draft.pwa) < 0)
    return {field:"mdpwa", msg:"Choose a Primary PWA from the list."};
  return null;
}

function renderDialog(){
  var host = document.getElementById("mandateDialog"); if(!host) return;
  if(!draft){ host.innerHTML = ""; host.hidden = true; return; }
  host.hidden = false;
  var first = (phase === "landing");
  var matches = advisorMatches(draft.q);
  var opts = "";
  if(draft.open && matches !== null){
    opts = matches.length
      ? matches.map(function(p, i){
          return '<li role="option" id="pwa-o'+i+'" class="combo-opt'
            + (i===draft.idx?" on":"")+'" aria-selected="'+(i===draft.idx)+'">'+p+'</li>';
        }).join("")
      : '<li class="combo-opt none" role="option" aria-disabled="true" aria-selected="false">'
        + 'No advisor matches “'+draft.q+'”.</li>';
  }
  var err = draft.err;
  function invalid(id){
    return (err && err.field === id) ? ' aria-invalid="true" aria-describedby="mderr"' : '';
  }
  host.innerHTML =
      '<div class="scrim" data-scrim></div>'
    + '<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dlgTitle">'
    +   '<button type="button" class="dlg-close" id="dlgclose" aria-label="Close">×</button>'
    +   '<h2 id="dlgTitle">'+(first ? "New scenario" : "Edit mandate")+'</h2>'
    +   '<p class="dlg-sub">Mandate details for this client.</p>'
    +   '<div class="field"><label for="mdtop">Top account size</label>'
    +     '<input type="text" id="mdtop" inputmode="numeric" autocomplete="off" value="'
    +       (draft.top ? money(draft.top) : '')+'"'+invalid("mdtop")+'></div>'
    +   '<div class="field"><label for="mdsize">Mandate size</label>'
    +     '<input type="text" id="mdsize" inputmode="numeric" autocomplete="off" value="'
    +       (draft.size ? money(draft.size) : '')+'"'+invalid("mdsize")+'>'
    +     '<span class="field-hint">At least $5,000,000, and no more than the top account.</span>'
    +   '</div>'
    +   '<div class="field combo"><label for="mdpwa">Primary PWA</label>'
    +     '<input type="text" id="mdpwa" role="combobox" autocomplete="off" spellcheck="false"'
    +       ' aria-expanded="'+(draft.open && matches !== null)+'" aria-controls="pwa-list"'
    +       ' aria-autocomplete="list"'
    +       (draft.idx >= 0 ? ' aria-activedescendant="pwa-o'+draft.idx+'"' : '')
    +       ' value="'+draft.q.replace(/"/g,"&quot;")+'"'+invalid("mdpwa")+'>'
    +     (matches !== null && matches.length
        ? '<span class="combo-count">'+matches.length+' found</span>' : '')
    +     '<ul id="pwa-list" role="listbox" aria-label="Matching advisors" class="combo-list"'
    +       (opts ? '' : ' hidden')+'>'+opts+'</ul>'
    +     (matches === null && draft.open
        ? '<span class="field-hint">Type at least two characters.</span>' : '')
    +   '</div>'
    +   (err ? '<p class="md-err" id="mderr" role="alert">'+err.msg+'</p>' : '')
    +   '<div class="dlg-actions">'
    +     '<button type="button" class="btn btn-ghost" id="dlgcancel">Cancel</button>'
    +     '<button type="button" class="btn btn-primary" id="dlgsave">'
    +       (first ? "Continue" : "Save mandate")+'</button>'
    +   '</div>'
    + '</div>';
}

function commitMandate(){
  var err = validateDraft();
  if(err){ draft.err = err; renderDialog();
    var f = document.getElementById(err.field); if(f) f.focus(); return false; }
  mandate.top = draft.top; mandate.size = draft.size; mandate.pwa = draft.pwa;
  mandate.err = null;
  var wasLanding = (phase === "landing");
  phase = "workspace";
  draft = null;
  refresh();
  if(wasLanding){
    var f = document.getElementById("bpa");     /* land on the allocation control */
    if(f) f.focus();
  }
  return true;
}

function renderBasis(){
  var el = document.getElementById("tier-basis"); if(!el) return;
  el.innerHTML = '<div class="tier-h"><h3>Scenario basis</h3></div>'
    + '<div class="basis">'
    +   '<div class="field"><label for="ccy">Currency</label><select id="ccy">'
    +     ["USD","CHF","GBP","EUR"].map(function(c){
            return '<option'+(c===basis.ccy?' selected':'')+'>'+c+'</option>'; }).join("")
    +   '</select></div>'
    +   '<div class="field"><label for="hedge">Hedging</label><select id="hedge">'
    +     ["Hedged","ISG Hedged","Unhedged","Equity Not Hedged"].map(function(c){
            return '<option'+(c===basis.hedge?' selected':'')+'>'+c+'</option>'; }).join("")
    +   '</select></div></div>'
    + '<p class="field-note" style="margin-top:8px">Fixed across every column. Changing either '
    + 'rebuilds all portfolios.</p>';
}
function renderBase(){
  var el = document.getElementById("tier-base"); if(!el) return;
  var cur = variant(BASE.v), aa = cur.aa;
  var canRE = reAllowed(aa);
  var exRE = canRE ? !cur.re : true;      /* Core and Ex Alts never hold real estate */
  el.innerHTML = '<div class="tier-h"><h3>Base portfolio</h3>'
    + '<span class="tier-count">Column 1</span></div>'
    + '<div class="basis" style="grid-template-columns:1fr">'
    +   '<div class="field"><label for="bpa">Allocation</label><select id="bpa">'
    +     ALLOCATIONS.map(function(a){
            return '<option'+(a===aa?' selected':'')+'>'+a+'</option>'; }).join("")
    +   '</select></div>'
    +   '<div class="chk"><input type="checkbox" id="bpre"'
    +     (exRE?' checked':'')+(canRE?'':' disabled')
    +     (canRE?'':' aria-describedby="bprenote"')+'>'
    +     '<label for="bpre">Exclude Real Estate</label></div>'
    +   (canRE ? '' : '<p class="chk-note" id="bprenote">Not available \u2014 '+aa
                    + ' holds no real estate.</p>')
    +   '<div class="chk"><input type="checkbox" id="bptaa"'+(BASE.taa?' checked':'')+'>'
    +     '<label for="bptaa">Exclude Tactical Asset Allocation</label></div>'
    +   '<p class="chk-note">TAA is included in every allocation by default.</p>'
    +   '<div class="field"><label for="bpr">Risk level</label><select id="bpr">'
    +     riskOptionsCore(BASE.v, BASE.r)+'</select></div></div>'
  + '<p class="field-note" style="margin-top:8px">Changing the base rebuilds column one. A '
  + 'comparison that duplicates it is dropped.</p>';
}
/* local option builders so the tiers do not depend on picker-supplied helpers */
function allocOptionsCore(sel){
  return VARIANTS.map(function(v){
    return '<option value="'+v.id+'"'+(v.id===sel?' selected':'')+'>'+v.label+'</option>';
  }).join("");
}
function riskOptionsCore(vId, sel){
  return RISKS.map(function(r){
    var av = available(vId, r[0]);
    return '<option value="'+r[0]+'"'+(r[0]===sel?' selected':'')+(av?'':' disabled')+'>'
      + r[1] + (av?'':' — unavailable') + '</option>';
  }).join("");
}
function renderPreview(){
  var pv = document.getElementById("preview"); if(!pv) return;
  if(!picks.length){
    pv.innerHTML = '<p class="none">Nothing queued. Up to '+slotsLeft()
      +' more portfolio'+(slotsLeft()===1?"":"s")+' can be added.</p>';
  } else {
    var h = "<p>Will add</p><ol>";
    picks.forEach(function(p,i){
      h += '<li><b>'+(i+1)+'</b>'+model(p.v,p.r).name
        +'<button type="button" class="rm" data-pick="'+i+'" aria-label="Remove '
        +model(p.v,p.r).name+' from the queue">×</button></li>';
    });
    pv.innerHTML = h+"</ol>";
  }
  var b = document.getElementById("addbtn");
  if(b){
    b.disabled = !picks.length;
    b.textContent = picks.length ? ("Add "+picks.length+" portfolio"+(picks.length>1?"s":""))
                                 : "Add portfolios";
  }
  var c = document.getElementById("count");
  if(c) c.textContent = (portfolios.length-1+picks.length)+" of "+CAP;
}
function renderBuilt(){
  var el = document.getElementById("built"); if(!el) return;
  el.innerHTML = portfolios.map(function(p,i){
    return '<div class="built-row"><span class="nm">'+p.name+'</span>'
      + (i===0 ? '<span class="tag">Base</span>'
               : '<button type="button" class="rm" data-i="'+i+'" aria-label="Remove '
                 +p.name+'">×</button>') + '</div>';
  }).join("");
}

/* ── tables ── */
function assetShare(p, cat, si){ return p.w[cat.key]*cat.split[si][1]; }
/* three states per cell: a value, an em dash (holds none of it), or blank
   (the allocation variant does not have this category at all) */
function cellFor(p, cat, si){
  if(!p.has[cat.key]) return "";
  return pct(si === null ? p.w[cat.key] : assetShare(p, cat, si));
}
var METRICS = [["Estimated Mean Return",function(p){return p.ret.toFixed(2)+"%";}],
               ["Sharpe Ratio",function(p){return p.sharpe.toFixed(2);}],
               ["Volatility",function(p){return p.vol.toFixed(2)+"%";}]];

function renderAlloc(){
  if(!document.getElementById("alloc")) return;
  var plus = App.plusColumn && portfolios.length < CAP+1;
  var h = '<caption class="sr-only">Portfolio allocation and metrics</caption><thead><tr>'
        + '<th scope="col" class="rowhead">Asset</th>';
  portfolios.forEach(function(p,i){
    h += '<th scope="col" class="num'+(i===App.lastAdded?' just-added':'')+'">'+p.name
      +(i===0?'<span class="base-tag">Base</span>':'')+'</th>';
  });
  if(plus) h += '<th scope="col" class="num addcol"><button type="button" id="plusbtn" '
              + 'class="plus" aria-label="Add a comparison portfolio">+</button></th>';
  h += "</tr></thead><tbody>";
  function row(cls, label, fn, pad){
    var r = '<tr class="'+cls+'"><th scope="row">'+label+'</th>'
          + portfolios.map(function(p){ return '<td class="num">'+fn(p)+'</td>'; }).join("");
    if(plus) r += '<td class="num addcol"></td>';
    return r+"</tr>";
  }
  CATS.forEach(function(c){
    if(!portfolios.some(function(p){ return p.has[c.key]; })) return;  /* nobody has it */
    h += row("cat", c.name, function(p){ return cellFor(p,c,null); });
    c.split.forEach(function(s,si){
      h += row("asset"+(si%2?" alt":""), s[0], function(p){ return cellFor(p,c,si); });
    });
  });
  h += row("total","Total",function(){ return "100.0%"; });
  METRICS.forEach(function(m){ h += row("metric", m[0], m[1]); });
  var el = document.getElementById("alloc");
  if(el) el.innerHTML = h+"</tbody>";
}
function renderRisk(){
  if(!document.getElementById("risk")) return;
  var n = portfolios.length, span = n*2+1;
  var h = '<caption class="sr-only">Risk dashboard</caption><thead><tr>'
        + '<th scope="col" rowspan="2" class="rowhead">Measure</th>';
  portfolios.forEach(function(p,i){
    h += '<th scope="colgroup" colspan="2" class="num'+(i===App.lastAdded?' just-added':'')+'">'
       + p.name + (i===0?'<span class="base-tag">Base</span>':'')+'</th>';
  });
  h += "</tr><tr>" + portfolios.map(function(){
    return '<th scope="col" class="num sub">Nominal</th><th scope="col" class="num sub">Real</th>';
  }).join("") + "</tr></thead><tbody>";
  function band(t){ return '<tr class="band"><th scope="rowgroup" colspan="'+span+'">'+t+'</th></tr>'; }

  h += band("Factor Based Risk Analytics");
  CATS.forEach(function(c){
    if(!portfolios.some(function(p){ return p.has[c.key]; })) return;
    h += '<tr class="cat"><th scope="row">'+c.name+'</th>'
       + portfolios.map(function(p){
           return '<td class="num span2" colspan="2">'+cellFor(p,c,null)+'</td>'; }).join("")+"</tr>";
  });
  METRICS.forEach(function(m){
    h += '<tr class="metric"><th scope="row">'+m[0]+'</th>'
       + portfolios.map(function(p){ return '<td class="num span2" colspan="2">'+m[1](p)+'</td>'; }).join("")+"</tr>";
  });
  h += band("Predicted Performance Over Stress Periods");
  CRISES.forEach(function(cn,ci){
    h += '<tr class="asset'+(ci%2?" alt":"")+'"><th scope="row">'+cn+'</th>'
       + portfolios.map(function(p){
           var b = -(p.w.equity*0.58 + (p.w.hf+p.w.pe)*0.50 + p.w.opa*0.47
                     + (p.w.igfi+p.w.ofi)*0.06)*CF[ci];
           return '<td class="num neg">'+sgn(b)+'</td><td class="num neg">'+sgn(b-2.4*CF[ci])+'</td>';
         }).join("")+"</tr>";
  });
  h += band("Portfolio Risk Premia");
  PREMIA.forEach(function(m,mi){
    h += '<tr class="asset'+(mi%2?" alt":"")+'"><th scope="row">'+m[0]+'</th>'
       + portfolios.map(function(p){
           var v = -(p.vol*m[1]);
           return '<td class="num neg">'+sgn(v)+'</td><td class="num neg">'+sgn(v-p.vol*m[1]*0.18)+'</td>';
         }).join("")+"</tr>";
  });
  [["Probability of Loss · 1 Year",1],["Probability of Loss · 3 Years",2]].forEach(function(m,mi){
    h += '<tr class="asset'+((mi+PREMIA.length)%2?" alt":"")+'"><th scope="row">'+m[0]+'</th>'
       + portfolios.map(function(p){
           var v = Math.max(1.5, 30 - p.ret*2.6 - m[1]*3.4);
           return '<td class="num">'+v.toFixed(1)+'%</td><td class="num">'+(v+6.2).toFixed(1)+'%</td>';
         }).join("")+"</tr>";
  });
  var el = document.getElementById("risk");
  if(el) el.innerHTML = h+"</tbody>";
}

var CAT_COLORS = ["var(--cat-1)","var(--cat-2)","var(--cat-3)","var(--cat-4)",
                  "var(--cat-5)","var(--cat-6)","var(--cat-7)"];

function esc(t){ return String(t).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;"); }

function renderComposition(){
  var el = document.getElementById("viz-comp"); if(!el) return;
  var W = 720, rowH = 26, gap = 16, padL = 148, padR = 16, top = 8;
  var n = portfolios.length, H = top + n*rowH + (n-1)*gap + 26;
  var barW = W - padL - padR;
  var svg = '<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Allocation by category for '
    + n + ' portfolio' + (n>1?'s':'') + '. The allocation table below carries the exact values.">';
  /* recessive gridlines at 0/25/50/75/100 */
  [0,25,50,75,100].forEach(function(t){
    var x = padL + barW*t/100;
    svg += '<line class="gl" x1="'+x+'" y1="'+top+'" x2="'+x+'" y2="'+(top+n*rowH+(n-1)*gap)+'"/>'
        +  '<text class="tick" x="'+x+'" y="'+(H-8)+'" text-anchor="middle">'+t+'%</text>';
  });
  portfolios.forEach(function(p,i){
    var y = top + i*(rowH+gap), x = padL;
    /* charts skip categories the variant does not have */
    svg += '<text class="plbl" x="'+(padL-12)+'" y="'+(y+rowH/2+4)+'" text-anchor="end">'
        +  esc(p.name.length>22 ? p.name.slice(0,21)+"\u2026" : p.name)+'</text>';
    CATS.forEach(function(c,ci){
      if(!p.has[c.key]) return;
      var v = p.w[c.key]; if(v < 0.05) return;
      var w = barW*v/100;
      var r = 0; /* rounded outer ends only */
      svg += '<rect class="seg" x="'+x+'" y="'+y+'" width="'+Math.max(0.5,w)+'" height="'+rowH+'"'
          +  ' rx="'+(w>6?3:0)+'" fill="'+CAT_COLORS[ci]+'"'
          +  ' data-tip="'+esc(p.name+" \u00b7 "+c.name)+'|'+v.toFixed(1)+'%"></rect>';
      if(w > 46) svg += '<text class="lbl" x="'+(x+w/2)+'" y="'+(y+rowH/2+4)+'"'
          +  ' text-anchor="middle">'+v.toFixed(0)+'%</text>';
      x += w;
    });
  });
  el.innerHTML = svg+'</svg>';
}

function renderScatter(){
  var el = document.getElementById("viz-rr"); if(!el) return;
  var W = 460, H = 300, padL = 46, padB = 38, padT = 14, padR = 18;
  var vols = portfolios.map(function(p){ return p.vol; });
  var rets = portfolios.map(function(p){ return p.ret; });
  var vlo = Math.min.apply(null,vols)-1.2, vhi = Math.max.apply(null,vols)+1.2;
  var rlo = Math.min.apply(null,rets)-0.5, rhi = Math.max.apply(null,rets)+0.5;
  if(vhi-vlo < 1){ vlo -= 1; vhi += 1; }
  if(rhi-rlo < 0.5){ rlo -= 0.5; rhi += 0.5; }
  var X = function(v){ return padL + (W-padL-padR)*(v-vlo)/(vhi-vlo); };
  var Y = function(r){ return H-padB - (H-padB-padT)*(r-rlo)/(rhi-rlo); };
  var svg = '<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Estimated mean return against '
    + 'volatility for '+portfolios.length+' portfolio'+(portfolios.length>1?'s':'')
    + '. Exact values are in the metrics band of the allocation table.">';
  var xt = 4, yt = 4, i;
  for(i=0;i<=xt;i++){
    var xv = vlo+(vhi-vlo)*i/xt, x = X(xv);
    svg += '<line class="gl" x1="'+x+'" y1="'+padT+'" x2="'+x+'" y2="'+(H-padB)+'"/>'
        +  '<text class="tick" x="'+x+'" y="'+(H-padB+15)+'" text-anchor="middle">'+xv.toFixed(1)+'</text>';
  }
  for(i=0;i<=yt;i++){
    var yv = rlo+(rhi-rlo)*i/yt, y = Y(yv);
    svg += '<line class="gl" x1="'+padL+'" y1="'+y+'" x2="'+(W-padR)+'" y2="'+y+'"/>'
        +  '<text class="tick" x="'+(padL-8)+'" y="'+(y+3.5)+'" text-anchor="end">'+yv.toFixed(1)+'</text>';
  }
  svg += '<line class="ax" x1="'+padL+'" y1="'+(H-padB)+'" x2="'+(W-padR)+'" y2="'+(H-padB)+'"/>'
      +  '<line class="ax" x1="'+padL+'" y1="'+padT+'" x2="'+padL+'" y2="'+(H-padB)+'"/>'
      +  '<text class="tick" x="'+((padL+W-padR)/2)+'" y="'+(H-6)+'" text-anchor="middle">'
      +  'Volatility %</text>'
      +  '<text class="tick" transform="translate(12,'+((padT+H-padB)/2)+') rotate(-90)"'
      +  ' text-anchor="middle">Estimated mean return %</text>';
  portfolios.forEach(function(p,i){
    var x = X(p.vol), y = Y(p.ret), base = (i===0);
    svg += '<circle class="dot" cx="'+x+'" cy="'+y+'" r="'+(base?7.5:6)+'"'
        +  ' fill="'+(base?"#16243A":"#1F5FBF")+'"'
        +  ' data-tip="'+esc(p.name)+'|vol '+p.vol.toFixed(2)+'% \u00b7 return '+p.ret.toFixed(2)+'%"></circle>';
    var above = (y > padT+28);
    svg += '<text class="plbl" x="'+x+'" y="'+(above ? y-13 : y+20)+'" text-anchor="middle">'
        +  esc(p.name.length>20 ? p.name.slice(0,19)+"\u2026" : p.name)+'</text>';
  });
  el.innerHTML = svg+'</svg>';
}

function renderLegend(){
  var el = document.getElementById("viz-key"); if(!el) return;
  el.innerHTML = CATS.map(function(c,i){
    return '<i><span class="sw" style="background:'+CAT_COLORS[i]+'"></span>'+c.name+'</i>';
  }).join("");
  var k2 = document.getElementById("viz-key2");
  if(k2) k2.innerHTML = '<i><span class="sw" style="background:#16243A;border-radius:50%"></span>'
    + 'Base</i><i><span class="sw" style="background:#1F5FBF;border-radius:50%"></span>Comparison</i>';
}

function refresh(){
  renderPhase(); renderDialog();
  renderMandate(); renderBasis(); renderBase();
  if(picker && picker.render) picker.render();
  renderPreview(); renderBuilt(); renderAlloc(); renderRisk();
  renderLegend(); renderComposition(); renderScatter();
  extras.forEach(function(f){ try { f(); } catch(e) { console.error(e); } });
  var cc = document.getElementById("colcount");
  if(cc) cc.textContent = portfolios.length+" portfolio"+(portfolios.length>1?"s":"")
    +" · "+(portfolios.length*2)+" columns in the risk dashboard";
}

/* shared tooltip for both charts */
var vizTip = document.createElement("div");
vizTip.className = "viz-tip";
document.body.appendChild(vizTip);
document.addEventListener("mouseover", function(e){
  var t = e.target && e.target.closest ? e.target.closest("[data-tip]") : null;
  if(!t){ vizTip.classList.remove("on"); return; }
  var parts = t.getAttribute("data-tip").split("|");
  vizTip.innerHTML = parts[0] + "<br><b>" + (parts[1]||"") + "</b>";
  vizTip.classList.add("on");
});
document.addEventListener("mousemove", function(e){
  if(!vizTip.classList.contains("on")) return;
  vizTip.style.left = Math.min(window.innerWidth-200, e.clientX+14) + "px";
  vizTip.style.top  = Math.max(8, e.clientY-42) + "px";
});
document.addEventListener("mouseleave", function(){ vizTip.classList.remove("on"); }, true);

document.addEventListener("click", function(e){
  if(e.target.id === "startbtn" || e.target.id === "mdedit"){
    openMandateDialog(e.target); return;
  }
  if(e.target.id === "dlgsave"){ commitMandate(); return; }
  if(e.target.id === "dlgcancel" || e.target.id === "dlgclose"
     || (e.target.dataset && e.target.dataset.scrim !== undefined)){
    closeMandateDialog(); return;
  }
  var opt = e.target.closest ? e.target.closest(".combo-opt") : null;
  if(opt && !opt.classList.contains("none") && draft){
    draft.pwa = opt.textContent; draft.q = opt.textContent;
    draft.open = false; draft.idx = -1; draft.err = null;
    renderDialog();
    var f = document.getElementById("mdpwa"); if(f) f.focus();
    return;
  }
  var rm = e.target.closest(".rm");
  if(rm){
    if(rm.dataset.pick !== undefined) removePick(+rm.dataset.pick);
    else if(rm.dataset.i !== undefined) removePortfolio(+rm.dataset.i);
    return;
  }
  if(e.target.id === "addbtn"){ commit(); return; }
});
document.addEventListener("input", function(e){
  if(!draft) return;
  if(e.target.id === "mdtop"){ draft.top = parseMoney(e.target.value); draft.err = null; return; }
  if(e.target.id === "mdsize"){ draft.size = parseMoney(e.target.value); draft.err = null; return; }
  if(e.target.id === "mdpwa"){
    draft.q = e.target.value; draft.open = true; draft.idx = -1;
    draft.pwa = (PWAS.indexOf(draft.q) >= 0) ? draft.q : "";
    draft.err = null;
    var pos = e.target.selectionStart;
    renderDialog();
    var f = document.getElementById("mdpwa");
    if(f){ f.focus(); try { f.setSelectionRange(pos, pos); } catch(_){} }
    return;
  }
});

document.addEventListener("keydown", function(e){
  if(!draft) return;
  if(e.key === "Escape"){ e.preventDefault(); closeMandateDialog(); return; }
  if(e.target.id !== "mdpwa") return;
  var m = advisorMatches(draft.q);
  if(m === null || !m.length) return;
  if(e.key === "ArrowDown" || e.key === "ArrowUp"){
    e.preventDefault();
    draft.open = true;
    draft.idx = (e.key === "ArrowDown")
      ? Math.min(m.length - 1, draft.idx + 1)
      : Math.max(0, draft.idx - 1);
  } else if(e.key === "Enter" && draft.idx >= 0){
    e.preventDefault();
    draft.pwa = m[draft.idx]; draft.q = m[draft.idx]; draft.open = false; draft.idx = -1;
  } else { return; }
  renderDialog();
  var f = document.getElementById("mdpwa"); if(f) f.focus();
});

document.addEventListener("change", function(e){
  if(e.target.id === "ccy" || e.target.id === "hedge"){
    setBasis(document.getElementById("ccy").value, document.getElementById("hedge").value);
    return;
  }
  if(e.target.id === "bpa" || e.target.id === "bpre"){
    var cur = variant(BASE.v);
    var aa  = (e.target.id === "bpa") ? e.target.value : cur.aa;
    /* an allocation with no real estate forces the exclusion on and greys the box */
    var exRE = !reAllowed(aa) ? true
             : (e.target.id === "bpre" ? e.target.checked : !cur.re);
    var v = variantFor(aa, exRE), r = BASE.r;
    if(!available(v,r)){
      for(var i=0;i<RISKS.length;i++){ if(available(v,RISKS[i][0])){ r = RISKS[i][0]; break; } }
    }
    setBase(v, r, BASE.taa); return;
  }
  if(e.target.id === "bptaa"){ setBase(BASE.v, BASE.r, e.target.checked); return; }
  if(e.target.id === "bpr"){ setBase(BASE.v, e.target.value, BASE.taa); return; }
});

return {RISKS:RISKS, VARIANTS:VARIANTS, MISSING:MISSING, BASE:BASE, CAP:CAP,
        model:model, variant:variant, risk:risk, available:available, used:used,
        picked:picked, picks:function(){return picks;}, portfolios:function(){return portfolios;},
        remaining:remaining, slotsLeft:slotsLeft, selectable:selectable,
        togglePick:togglePick, addPick:addPick, removePick:removePick, setPicks:setPicks,
        commit:commit, addPortfolio:addPortfolio, removePortfolio:removePortfolio, refresh:refresh,
        setBasis:setBasis, setBase:setBase, setMandate:setMandate,
        mandate:function(){return mandate;}, basis:function(){return basis;},
        phase:function(){return phase;}, openMandate:openMandateDialog,
        commitMandate:commitMandate, draft:function(){return draft;},
        setPicker:function(p){ picker = p; },
        addRenderer:function(f){ extras.push(f); },
        step:function(){ return step; },
        setStep:function(s){ step = s; refresh(); },
        CATS:CATS, mandateSize:function(){ return mandate.size; },
        plusColumn:false, lastAdded:-1};
})();


function allocOptions(sel){
  return App.VARIANTS.map(function(v){
    return '<option value="'+v.id+'"'+(v.id===sel?' selected':'')+'>'+v.label+'</option>';
  }).join("");
}
function riskOptions(vId, sel){
  return App.RISKS.map(function(r){
    var ok = vId ? App.selectable(vId, r[0]) : true;
    var why = !vId ? "" : (!App.available(vId,r[0]) ? " — unavailable"
              : (App.used(vId,r[0]) ? " — already added" : ""));
    return '<option value="'+r[0]+'"'+(r[0]===sel?' selected':'')+(ok?'':' disabled')+'>'
      +r[1]+why+'</option>';
  }).join("");
}


(function(){
App.plusColumn = true;
var pop = document.createElement("div");
pop.className = "pop";
pop.setAttribute("role","dialog");
pop.setAttribute("aria-modal","false");
pop.setAttribute("aria-label","Add a comparison portfolio");
document.body.appendChild(pop);
var cur = {v:App.VARIANTS[0].id, r:null}, opener = null, justAdded = "";

function paint(){
  var left = App.slotsLeft();
  if(left <= 0){
    pop.innerHTML = '<button type="button" class="pop-close" aria-label="Close">\u00d7</button>'
      + '<h4>Add comparison</h4>'
      + '<p class="pop-full">All three comparison slots are in use. Remove one from the rail to '
      + 'free a slot.</p>'
      + '<div class="pop-actions"><button type="button" class="btn btn-ghost" id="pdone">Close</button></div>';
    return;
  }
  var ok = cur.v && cur.r && App.selectable(cur.v,cur.r);
  pop.innerHTML = '<button type="button" class="pop-close" aria-label="Close">\u00d7</button>'
    + '<h4>Add comparison</h4>'
    + '<p class="slots">' + left + ' slot' + (left===1?"":"s") + ' remaining'
    + (justAdded ? ' \u00b7 <span style="color:#176A33">' + justAdded + ' added</span>' : '')
    + '</p>'
    + '<div class="field"><label for="pa">Allocation</label><select id="pa">'
    +   allocOptions(cur.v) + '</select></div>'
    + '<div class="field"><label for="pr">Risk level</label><select id="pr">'
    +   '<option value="">Select\u2026</option>' + riskOptions(cur.v, cur.r) + '</select></div>'
    + '<div class="pop-actions">'
    +   '<button type="button" class="btn btn-primary" id="padd"' + (ok?"":" disabled") + '>'
    +     'Add to table</button>'
    +   '<button type="button" class="btn btn-ghost" id="pdone">Done</button></div>';
}
function open(btn){
  opener = btn; justAdded = ""; paint();
  var r = btn.getBoundingClientRect();
  pop.classList.add("on");
  var h = pop.offsetHeight || 300;
  pop.style.top  = Math.max(12, Math.min(window.innerHeight - h - 12, r.bottom + 8)) + "px";
  pop.style.left = Math.max(12, Math.min(window.innerWidth - 292, r.right - 280)) + "px";
  var f = pop.querySelector("#pa"); if(f) f.focus();
}
function close(){
  pop.classList.remove("on");
  var t = document.getElementById("plusbtn");
  if(t) t.focus(); else if(opener && opener.isConnected) opener.focus();
}
document.addEventListener("click", function(e){
  if(e.target.id === "plusbtn"){ open(e.target); return; }
  if(e.target.closest(".pop")) return;
  if(pop.classList.contains("on")) close();
});
pop.addEventListener("change", function(e){
  if(e.target.id === "pa"){ cur.v = e.target.value; cur.r = null; justAdded = ""; paint(); }
  if(e.target.id === "pr"){ cur.r = e.target.value || null; paint(); }
});
pop.addEventListener("click", function(e){
  if(e.target.id === "padd"){
    var nm = App.model(cur.v, cur.r).name;
    if(App.addPortfolio(cur.v, cur.r)){
      justAdded = nm; cur.r = null; paint();
      var f = pop.querySelector("#pr"); if(f) f.focus();
    }
    return;
  }
  if(e.target.id === "pdone" || e.target.closest(".pop-close")) close();
});
document.addEventListener("keydown", function(e){
  if(e.key === "Escape" && pop.classList.contains("on")) close();
});
App.setPicker({render:function(){ if(pop.classList.contains("on")) paint(); }});
})();


var CATEGORIES = [
  "Cash, Deposits & Money Market Funds",
  "Investment Grade Fixed Income",
  "Other Fixed Income",
  "Public Equity",
  "Hedge Funds",
  "Private Equity",
  "Other Private Assets"
];

/* Every product carries: name, ticker, sub-asset class, style, vehicle, source,
   liquidity, exposure currency, product cost %, GS management fee %, weight in
   the sleeve. Sleeve weights sum to 1. */
function P(n,t,ac,st,v,src,liq,ccy,c,m,w){
  return {n:n,t:t,ac:ac,st:st,v:v,src:src,liq:liq,ccy:ccy,c:c,m:m,w:w};
}

var SLEEVES = {
"Cash, Deposits & Money Market Funds": [
  {name:"Government Money Market", products:[
    P("GS FS Government MMF","FGTXX","Government MMF","Passive","Mutual Fund","Internal","Daily","USD",0.18,0.15,0.65),
    P("GS FS Treasury Instruments","FTIXX","Treasury MMF","Passive","Mutual Fund","Internal","Daily","USD",0.17,0.15,0.35)]},
  {name:"Deposits & MMF Blend", products:[
    P("GS Bank Deposit Program","—","Bank Deposits","Passive","SMA","Internal","Daily","USD",0.05,0.15,0.50),
    P("GS FS Government MMF","FGTXX","Government MMF","Passive","Mutual Fund","Internal","Daily","USD",0.18,0.15,0.50)]},
  {name:"Prime & Enhanced Cash", products:[
    P("GS FS Prime Obligations","FPOXX","Prime MMF","Active","Mutual Fund","Internal","Daily","USD",0.23,0.15,0.55),
    P("GS Enhanced Income Fund","GEIRX","Enhanced Cash","Active","Mutual Fund","Internal","Daily","USD",0.31,0.15,0.45)]}
],
"Investment Grade Fixed Income": [
  {name:"GSAM Separately Managed Account", products:[
    P("GSAM Core Municipal SMA","—","Municipals","Active","SMA","Internal","Daily","USD",0.25,0.30,0.45),
    P("GSAM Intermediate Credit SMA","—","IG Corporate","Active","SMA","Internal","Daily","USD",0.28,0.30,0.35),
    P("GSAM Short Duration SMA","—","Short Duration","Active","SMA","Internal","Daily","USD",0.22,0.30,0.20)]},
  {name:"Funds Only", products:[
    P("GS US Corporate Bond Fund","GSUCX","IG Corporate","Active","Mutual Fund","Internal","Daily","USD",0.42,0.30,0.45),
    P("Ashfield Global Credit Fund","AGCIX","IG Corporate","Active","Mutual Fund","External","Daily","Local",0.55,0.30,0.30),
    P("GS Short Duration Income","GSSDX","Short Duration","Active","Mutual Fund","Internal","Daily","USD",0.38,0.30,0.25)]},
  {name:"ETF & Mutual Funds", products:[
    P("GS Access IG Corporate ETF","GIGB","IG Corporate","Passive","ETF","Internal","Daily","USD",0.14,0.30,0.40),
    P("GS Access Treasury 0-1 ETF","GBIL","Government","Passive","ETF","Internal","Daily","USD",0.12,0.30,0.30),
    P("GS Core Fixed Income Fund","GCFIX","Aggregate","Active","Mutual Fund","Internal","Daily","USD",0.46,0.30,0.30)]}
],
"Other Fixed Income": [
  {name:"High Yield & EM Funds", products:[
    P("GS High Yield Fund","GSHAX","High Yield","Active","Mutual Fund","Internal","Daily","USD",0.72,0.30,0.55),
    P("GS Emerging Markets Debt","GSDAX","EM Debt","Active","Mutual Fund","Internal","Daily","USD",0.85,0.30,0.45)]},
  {name:"Multi-Sector Funds", products:[
    P("GS Strategic Income Fund","GSZAX","Multi-Sector","Active","Mutual Fund","Internal","Daily","USD",0.79,0.30,0.60),
    P("Calderwood Local EM Debt","CLEDX","EM Debt","Active","Mutual Fund","External","Daily","Local",0.91,0.30,0.40)]},
  {name:"ETF Only", products:[
    P("GS Access High Yield ETF","GHYB","High Yield","Passive","ETF","Internal","Daily","USD",0.34,0.30,0.60),
    P("GS Access EM USD Bond ETF","GEMD","EM Debt","Passive","ETF","Internal","Daily","USD",0.39,0.30,0.40)]}
],
"Public Equity": [
  {name:"Active-Passive", products:[
    P("GS Access US Large Cap ETF","GSLC","US Large Cap","Passive","ETF","Internal","Daily","USD",0.09,0.30,0.34),
    P("GS US Equity Insights Fund","GCSAX","US All Cap","Active","Mutual Fund","Internal","Daily","USD",0.71,0.30,0.22),
    P("Northbrook Intl Equity","NBIEX","Intl Developed","Active","Mutual Fund","External","Daily","Local",0.68,0.30,0.24),
    P("GS Emerging Markets Equity","GEMAX","Emerging Markets","Active","Mutual Fund","Internal","Daily","Local",1.12,0.30,0.20)]},
  {name:"Passive", products:[
    P("GS Access US Large Cap ETF","GSLC","US Large Cap","Passive","ETF","Internal","Daily","USD",0.09,0.30,0.46),
    P("GS Access Intl Equity ETF","GSIE","Intl Developed","Passive","ETF","Internal","Daily","Local",0.25,0.30,0.32),
    P("GS Access EM Equity ETF","GEM","Emerging Markets","Passive","ETF","Internal","Daily","Local",0.37,0.30,0.22)]},
  {name:"Concentrated Active", products:[
    P("GS Concentrated Growth SMA","—","US Large Cap","Active","SMA","Internal","Daily","USD",0.55,0.30,0.40),
    P("GS US Focused Value SMA","—","US Large Cap","Active","SMA","Internal","Daily","USD",0.55,0.30,0.32),
    P("Northbrook Focused Intl","NBFIX","Intl Developed","Active","Mutual Fund","External","Daily","Local",0.94,0.30,0.28)]}
],
"Hedge Funds": [
  {name:"Multi-Strategy Fund of Funds", products:[
    P("GS HedgeWorks Multi-Strategy","—","Multi-Strategy","Active","SMA","Internal","Quarterly","USD",1.35,0.30,0.60),
    P("Calderwood Global Macro","—","Global Macro","Active","SMA","External","Quarterly","USD",1.48,0.30,0.40)]},
  {name:"Direct Single Manager", products:[
    P("Ashfield Select Equity L/S","—","Equity Long/Short","Active","SMA","External","Quarterly","USD",1.62,0.30,0.55),
    P("Northbrook Relative Value","—","Relative Value","Active","SMA","External","Monthly","USD",1.55,0.30,0.45)]},
  {name:"Liquid Alternatives", products:[
    P("GS Absolute Return Tracker","GARTX","Multi-Strategy","Active","Mutual Fund","Internal","Daily","USD",0.96,0.30,0.60),
    P("GS Managed Futures Strategy","GMFAX","Managed Futures","Active","Mutual Fund","Internal","Daily","USD",1.08,0.30,0.40)]}
],
"Private Equity": [
  {name:"Diversified Vintage Program", products:[
    P("GS Vintage Fund IX","—","Secondaries","Active","SMA","Internal","Drawdown","USD",1.25,0.30,0.40),
    P("GS Private Markets Buyout","—","Buyout","Active","SMA","Internal","Drawdown","USD",1.45,0.30,0.35),
    P("GS Growth Equity Partners","—","Growth","Active","SMA","Internal","Drawdown","USD",1.50,0.30,0.25)]},
  {name:"Buyout Focus", products:[
    P("GS Private Markets Buyout","—","Buyout","Active","SMA","Internal","Drawdown","USD",1.45,0.30,0.65),
    P("Ashfield Co-Investment","—","Co-Investment","Active","SMA","External","Drawdown","EUR",1.10,0.30,0.35)]},
  {name:"Growth & Venture", products:[
    P("GS Growth Equity Partners","—","Growth","Active","SMA","Internal","Drawdown","USD",1.50,0.30,0.55),
    P("GS Venture Access Fund","—","Venture","Active","SMA","Internal","Drawdown","USD",1.72,0.30,0.45)]}
],
"Other Private Assets": [
  {name:"Real Estate & Infrastructure", products:[
    P("GS Real Estate Partners","—","Real Estate","Active","SMA","Internal","Drawdown","USD",1.30,0.30,0.45),
    P("Northbrook Infrastructure","—","Infrastructure","Active","SMA","External","Drawdown","EUR",1.28,0.30,0.35),
    P("GS Global REIT Fund","GREAX","Listed Real Estate","Active","Mutual Fund","Internal","Daily","Local",0.98,0.30,0.20)]},
  {name:"Private Credit Focus", products:[
    P("GS Private Credit Partners","—","Private Credit","Active","SMA","Internal","Quarterly","USD",1.40,0.30,0.60),
    P("Ashfield Direct Lending","—","Direct Lending","Active","SMA","External","Quarterly","USD",1.35,0.30,0.40)]},
  {name:"Diversified Real Assets", products:[
    P("GS Real Assets Program","—","Real Assets","Active","SMA","Internal","Drawdown","USD",1.22,0.30,0.55),
    P("Calderwood Energy Transition","—","Energy","Active","SMA","External","Drawdown","EUR",1.38,0.30,0.45)]}
]
};

(function(){
'use strict';
var chosen = {};                       /* category name -> sleeve name */
CATEGORIES.forEach(function(c){ chosen[c] = null; });

function base(){ return App.portfolios()[0]; }
function catKey(name){
  for(var i=0;i<App.CATS.length;i++){ if(App.CATS[i].name === name) return App.CATS[i].key; }
  return null;
}
/* categories the base portfolio actually has */
function liveCategories(){
  var b = base(); if(!b) return [];
  return CATEGORIES.filter(function(c){
    var k = catKey(c); return k && b.has[k] && b.w[k] > 0.005;
  });
}
function sleeveFor(cat){
  var name = chosen[cat]; if(!name) return null;
  var list = SLEEVES[cat] || [];
  for(var i=0;i<list.length;i++){ if(list[i].name === name) return list[i]; }
  return null;
}
function filled(){ return liveCategories().filter(function(c){ return !!chosen[c]; }).length; }
function complete(){ var l = liveCategories(); return l.length > 0 && filled() === l.length; }

/* ── rows ──
   Every printed figure derives from the PRINTED weight, so the document
   hand-reconciles: weight x mandate really does equal the notional shown.

   1. round each weight to 2dp by largest remainder, so the column sums to
      exactly 100.00% rather than 99.99% or 100.01%
   2. derive notional from that rounded weight, then round to the nearest $100
   3. derive the weighted fee from that rounded weight too

   For a mandate that is a round million, step 2's rounding is a no-op: mandate/10000
   is already a multiple of 100, so notional lands on a hundred by construction. */
var ROUND_TO = 100;

function roundWeights(exact){
  var units = exact.map(function(w){ return Math.floor(w * 100); });     /* 0.01% units */
  var used  = units.reduce(function(a, b){ return a + b; }, 0);
  var short = 10000 - used;                                             /* 100.00% = 10000 units */
  var order = exact.map(function(w, i){ return {i:i, rem:w*100 - Math.floor(w*100)}; })
                   .sort(function(a, b){ return b.rem - a.rem; });
  for(var n = 0; n < short && n < order.length; n++) units[order[n].i] += 1;
  return units.map(function(u){ return u / 100; });
}

function rows(){
  var b = base(), out = [], all = [];
  if(!b) return out;
  liveCategories().forEach(function(cat){
    var k = catKey(cat), catW = b.w[k], sl = sleeveFor(cat);
    var items = [];
    if(sl) items = sl.products.map(function(p){
      var it = {name:p.n, ticker:p.t, ac:p.ac, style:p.st, vehicle:p.v, source:p.src,
                liq:p.liq, ccy:p.ccy, cost:p.c, mgmt:p.m,
                allIn:p.c + p.m, exact:catW * p.w, sleeve:sl.name};
      all.push(it);
      return it;
    });
    out.push({cat:cat, weight:catW, sleeve:sl ? sl.name : null, items:items});
  });

  if(all.length){
    var rounded = complete()
      ? roundWeights(all.map(function(i){ return i.exact; }))   /* sums to 100.00% */
      : all.map(function(i){ return Math.round(i.exact * 100) / 100; });
    all.forEach(function(it, ix){
      it.weight   = rounded[ix];
      it.wtdBp    = it.allIn * it.weight;
      it.notional = Math.round(App.mandateSize() * it.weight / 100 / ROUND_TO) * ROUND_TO;
    });
  }
  return out;
}
function totals(){
  var w = 0, bp = 0, n = 0;
  rows().forEach(function(g){ g.items.forEach(function(i){
    w += i.weight; bp += i.wtdBp; n += i.notional; }); });
  return {weight:w, bp:bp, notional:n};
}
function money(v){
  return (v === null || v === undefined || isNaN(v))
    ? "\u2014" : "$" + Math.round(v).toLocaleString("en-US");
}
function pillFor(v){
  var m = {Active:"p-act", Passive:"p-pas", SMA:"p-sma", ETF:"p-etf", "Mutual Fund":"p-mf",
           Internal:"p-int", External:"p-ext"};
  return '<span class="pill '+(m[v]||"p-pas")+'">'+v+'</span>';
}

/* ── rail tier ── */
function renderRail(){
  var el = document.getElementById("tier-sleeves"); if(!el) return;
  if(App.step() !== "impl"){ el.hidden = true; return; }
  el.hidden = false;
  var b = base(), live = liveCategories();
  if(!b){
    el.innerHTML = '<div class="tier-h"><h3>Sleeves</h3></div>'
      + '<p class="field-note">Build a base portfolio first.</p>';
    return;
  }
  var h = '<div class="tier-h"><h3>Sleeves</h3>'
        + '<span class="tier-count">'+filled()+' of '+live.length+'</span></div><div class="sl-list">';
  live.forEach(function(cat, i){
    var k = catKey(cat), opts = (SLEEVES[cat]||[]).map(function(s){
      return '<option value="'+s.name+'"'+(chosen[cat]===s.name?' selected':'')+'>'+s.name+'</option>';
    }).join("");
    h += '<div class="sl-row'+(chosen[cat]?' done':'')+'">'
      +   '<span class="cat"><b><label for="sl'+i+'">'+cat+'</label></b>'
      +     '<span>'+b.w[k].toFixed(1)+'%</span></span>'
      +   '<select id="sl'+i+'" data-cat="'+cat.replace(/"/g,'&quot;')+'">'
      +     '<option value="">Select a sleeve…</option>'+opts+'</select></div>';
  });
  h += '</div><p class="sl-progress">'+filled()+' of '+live.length
     + ' categories have a sleeve.<span class="sl-bar"><i style="width:'
     + (live.length ? Math.round(filled()/live.length*100) : 0)+'%"></i></span></p>';
  el.innerHTML = h;
}

/* ── document ── */
function renderView(){
  var el = document.getElementById("view-impl"); if(!el) return;
  var wrap = document.getElementById("view-impl-wrap");
  var nav = document.getElementById("steps");
  if(nav) nav.querySelectorAll(".step").forEach(function(b){
    b.setAttribute("aria-selected", String(b.dataset.step === App.step()));
  });
  var aa = document.getElementById("view-aa");
  /* the landing phase hides both steps; only inside the workspace does the step
     nav decide which one shows */
  var onLanding = (App.phase && App.phase() === "landing");
  if(aa) aa.hidden = onLanding || App.step() !== "aa";
  if(wrap) wrap.hidden = onLanding || App.step() !== "impl";
  el.hidden = onLanding || App.step() !== "impl";
  if(onLanding || App.step() !== "impl") return;

  var b = base();
  if(!b){
    el.innerHTML = '<div class="impl-empty"><h3>No base portfolio yet</h3>'
      + '<p>Choose an allocation and risk level in the rail to build the base portfolio, then '
      + 'attach a sleeve to each of its categories.</p></div>';
    return;
  }
  var live = liveCategories(), t = totals(), done = complete();
  var h = '<div class="impl-head"><div>'
    + '<p class="sec-note" style="margin:0 0 4px">Implementing <strong>'+b.name+'</strong> '
    + 'against a mandate of '+money(App.mandateSize())+'.</p>'
    + '<p class="sec-note" style="margin:0">'
    + (done ? '<span class="bdg b-ok">All '+live.length+' categories implemented</span>'
            : '<span class="bdg b-warn">'+(live.length-filled())+' categor'
              +((live.length-filled())===1?'y':'ies')+' still need a sleeve</span>')
    + '</p></div></div>';

  h += '<div class="tblwrap"><table class="tbl impl">'
    + '<caption class="sr-only">Implementation model by product</caption><thead><tr>'
    + '<th scope="col" class="rowhead txt">Categories &amp; Asset Classes</th>'
    + '<th scope="col" class="txt prodcol">Products</th>'
    + '<th scope="col" class="num">Allocation (%)</th>'
    + '<th scope="col" class="txt">Ticker</th>'
    + '<th scope="col" class="txt">Style</th>'
    + '<th scope="col" class="txt">Vehicle</th>'
    + '<th scope="col" class="txt">Source</th>'
    + '<th scope="col" class="txt">Liquidity</th>'
    + '<th scope="col" class="txt">Exposure ccy</th>'
    + '<th scope="col" class="num">Cost</th>'
    + '<th scope="col" class="num">Mgmt fee</th>'
    + '<th scope="col" class="num">Wtd fee</th>'
    + '<th scope="col" class="num">Notional</th>'
    + '</tr></thead><tbody>';

  rows().forEach(function(g){
    var gbp = 0, gn = 0, gw = 0;
    g.items.forEach(function(i){ gbp += i.wtdBp; gn += i.notional; gw += i.weight; });
    var gNotional = g.sleeve ? gn
      : Math.round(App.mandateSize()*g.weight/100/ROUND_TO)*ROUND_TO;
    h += '<tr class="cat"><th scope="row">'+g.cat+'</th>'
      +  '<td class="txt prodcol">'+(g.sleeve
           ? '<span class="pill p-sleeve">'+g.sleeve+'</span>'
           : '<span class="bdg b-warn">No sleeve attached</span>')+'</td>'
      +  '<td class="num">'+g.weight.toFixed(2)+'%</td>'
      +  '<td colspan="6"></td>'
      +  '<td class="num"></td><td class="num"></td>'
      +  '<td class="num">'+(g.sleeve ? gbp.toFixed(1)+'bp' : '')+'</td>'
      +  '<td class="num">'+money(gNotional)+'</td></tr>';
    g.items.forEach(function(i, ix){
      h += '<tr class="asset'+(ix%2?' alt':'')+'"><th scope="row">'+i.ac+'</th>'
        +  '<td class="txt prodcol">'+i.name+'</td>'
        +  '<td class="num">'+i.weight.toFixed(2)+'%</td>'
        +  '<td class="txt tick">'+i.ticker+'</td>'
        +  '<td class="txt">'+pillFor(i.style)+'</td>'
        +  '<td class="txt">'+pillFor(i.vehicle)+'</td>'
        +  '<td class="txt">'+pillFor(i.source)+'</td>'
        +  '<td class="txt">'+i.liq+'</td>'
        +  '<td class="txt tick">'+i.ccy+'</td>'
        +  '<td class="num">'+i.cost.toFixed(2)+'%</td>'
        +  '<td class="num">'+i.mgmt.toFixed(2)+'%</td>'
        +  '<td class="num">'+i.wtdBp.toFixed(1)+'bp</td>'
        +  '<td class="num">'+money(i.notional)+'</td></tr>';
    });
  });
  h += '<tr class="grand"><th scope="row">Total</th>'
    +  '<td class="prodcol"></td>'
    +  '<td class="num">'+t.weight.toFixed(2)+'%</td>'
    +  '<td colspan="8"></td>'
    +  '<td class="num">'+t.bp.toFixed(1)+'bp</td>'
    +  '<td class="num">'+money(t.notional)+'</td></tr>';
  h += '</tbody></table></div>'
    + '<div class="impl-foot">'
    +   '<button type="button" class="btn btn-primary" id="implexport"'
    +     (done ? '' : ' disabled aria-describedby="implgate"')+'>Download .xlsx</button>'
    +   (done ? '' : '<p class="impl-gate" id="implgate">Attach a sleeve to every category to '
                    + 'enable the download.</p>')
    + '</div>'
    + '<p class="impl-note">Weighted fee is (product cost + management fee) × weight, shown in '
    + 'basis points to one decimal. Weights are rounded to two decimals by largest remainder so the '
    + 'column sums to exactly 100.00%, and notional is derived from the printed weight against the '
    + 'mandate size — so weight × mandate reconciles to the notional shown.</p>';
  el.innerHTML = h;
}

document.addEventListener("change", function(e){
  if(e.target.dataset && e.target.dataset.cat !== undefined){
    chosen[e.target.dataset.cat] = e.target.value || null;
    App.refresh();
  }
});
document.addEventListener("click", function(e){
  var s = e.target.closest(".step");
  if(s){ App.setStep(s.dataset.step); return; }
  if(e.target.id === "implexport"){
    e.target.textContent = "Preparing…"; e.target.disabled = true;
    setTimeout(function(){
      e.target.textContent = "Download .xlsx"; e.target.disabled = false;
    }, 1200);
  }
});
App.addRenderer(function(){ renderRail(); renderView(); });
})();


// Bootstrap. The script tag sits at the end of <body>, so the DOM is parsed by
// the time this runs; the readyState guard covers a deferred load anyway.
(function () {
    function boot() { App.refresh(); }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
