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
.tbl.impl .rowhead{width:248px;min-width:248px;max-width:248px}
.tbl.impl .prodcol{position:sticky;left:248px;z-index:1;min-width:276px;
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
SLEEVE_JS = r"""
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
"""

IMPL_JS = SLEEVE_JS + r"""
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
"""
