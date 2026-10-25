(function () {
'use strict';
/* =============================================================================
   Custom portfolios (D171): a proposal built product by product.

   Chosen as "Custom" in the rail's Allocation selector, built here
   on the implementation step from nothing: products and their weights of the
   portfolio. Its categories follow from its products, and its risk profile
   sets the limits - each category's weight in the profile's own strategic
   portfolio, give or take, plus a cap on any one product and on what does not
   deal daily. Outside the usual ranges an admin who did not build it approves
   it before it downloads. The model, the table, the fees and the gate are
   the implementation layer's, fed from here; this file lays out what is custom: the
   allocation step's column, the rail's tier, the blank slate, the editable
   cells and the dialogs that add to a build.
   ========================================================================== */

/* custom-portfolio-helpers-begin
   DOM-free, so the suite runs them through node beside the Python they
   mirror (customPortfolio.checks, limits, figures). The words are the
   server's, so the page and a refused export say the same thing. */
var CP_BALANCE = 0.005;                 /* customPortfolio.BALANCE_TOLERANCE */

function cpG(v) { return String(Number(Number(v).toPrecision(6))); }
function cpMoney(v) { return '$' + Math.round(v).toLocaleString('en-US'); }

/* "12.5", "12.50" or "12.5%": a weight to 2dp from 0 to 100; '' is none yet
   (null); anything else NaN */
function cpParse(text) {
  var t = String(text == null ? '' : text).trim().replace(/%$/, '').trim();
  if (t === '') return null;
  if (!/^(\d{1,3}(\.\d{0,2})?|\.\d{1,2})$/.test(t)) return NaN;
  var v = parseFloat(t);
  return v > 100 ? NaN : v;
}

function cpWeightsByLine(products) {
  var out = {};
  (products || []).forEach(function (p) {
    out[p.category] = (out[p.category] || 0) + (Number(p.weightPct) || 0);
  });
  return out;
}

/* the overlays' own lines counted in the line a strategic proposal funds
   them from (customPortfolio.countedIn) */
function cpLimited(weights, countedIn) {
  var out = {};
  Object.keys(weights).forEach(function (line) {
    var to = (countedIn || {})[line] || line;
    out[to] = (out[to] || 0) + weights[line];
  });
  return out;
}

function cpBands(anchor, names, limits) {
  var out = {};
  names.forEach(function (n) {
    var c = Number((anchor || {})[n] || 0);
    out[n] = { centre: c,
               soft: [Math.max(0, c - limits.soft), Math.min(100, c + limits.soft)],
               hard: [Math.max(0, c - limits.hard), Math.min(100, c + limits.hard)] };
  });
  return out;
}

/* *values* scaled to add to exactly *target*, to 2dp, by largest remainder */
function cpScaleTo(values, target) {
  var sum = values.reduce(function (a, b) { return a + b; }, 0);
  if (!(sum > 0)) return values.map(function () { return 0; });
  var exact = values.map(function (v) { return v / sum * target; });
  var units = exact.map(function (v) { return Math.floor(v * 100 + 1e-9); });
  var short = Math.round(target * 100) - units.reduce(function (a, b) { return a + b; }, 0);
  exact.map(function (v, i) { return { i: i, r: v * 100 - units[i] }; })
    .sort(function (a, b) { return (b.r - a.r) || (a.i - b.i); })
    .slice(0, Math.max(0, short)).forEach(function (o) { units[o.i] += 1; });
  return units.map(function (u) { return u / 100; });
}

/* The build against its limits: {status, problems, wide, lines, total,
   balanced, heavy, illiquid}. *ctx* carries what the server reads for
   itself: the lines in order, the anchor's weights by line, the limits,
   the lines counted in another, the products this build may hold and
   their liquidity and names, the mandate and the private-markets rule.
   Mirrors customPortfolio.checks; minimums are the model's to find. */
function cpCheck(products, ctx) {
  var label = function (pid) { return (ctx.label || {})[pid] || pid; };
  var problems = [];
  if (!products.length) {
    /* nothing built: every line the profile holds, at nothing, against its range */
    var none = (ctx.lines || []).filter(function (n) { return n in (ctx.anchor || {}); });
    Object.keys(ctx.anchor || {}).forEach(function (n) { if (none.indexOf(n) === -1) none.push(n); });
    var empty = cpBands(ctx.anchor, none, ctx.limits), at = {};
    none.forEach(function (n) {
      at[n] = { centre: empty[n].centre, soft: empty[n].soft, hard: empty[n].hard, weightPct: 0 };
    });
    return { status: 'bad', problems: ['Nothing is built yet.'], wide: [], lines: at, total: 0,
             balanced: false, heavy: [], illiquid: 0 };
  }
  var gone = products.filter(function (p) { return !(ctx.allowed || {})[p.productId]; })
    .map(function (p) { return label(p.productId); });
  if (gone.length) {
    problems.push(gone.join(', ') + ' ' + (gone.length === 1 ? 'is' : 'are')
      + ' no longer a product this portfolio may hold.');
  }
  var unweighted = products.filter(function (p) { return !p.weightPct; })
    .map(function (p) { return label(p.productId); });
  if (unweighted.length) {
    problems.push('Give ' + unweighted.join(', ') + ' a weight, or remove '
      + (unweighted.length === 1 ? 'it' : 'them') + '.');
  }
  var total = products.reduce(function (t, p) { return t + (Number(p.weightPct) || 0); }, 0);
  var balanced = Math.abs(total - 100) < CP_BALANCE;
  if (!balanced) problems.push('The portfolio adds to ' + total.toFixed(2) + '%, not 100%.');
  if (products.length > ctx.limits.maxProducts) {
    problems.push(products.length + ' products; at most ' + ctx.limits.maxProducts + '.');
  }
  var held = cpLimited(cpWeightsByLine(products), ctx.countedIn);
  var names = (ctx.lines || []).filter(function (n) { return n in held || n in (ctx.anchor || {}); });
  Object.keys(held).forEach(function (n) { if (names.indexOf(n) === -1) names.push(n); });
  var bands = cpBands(ctx.anchor, names, ctx.limits);
  var outside = [], wide = [];
  names.forEach(function (line) {
    var w = held[line] || 0, band = bands[line];
    /* over a limit is over it however much is still to place; under one
       only counts once the portfolio adds to 100% */
    if (w > band.hard[1] + 1e-9 || (balanced && w < band.hard[0] - 1e-9)) {
      outside.push(line + ' at ' + w.toFixed(2) + '% (' + cpG(band.hard[0]) + '-' + cpG(band.hard[1]) + '%)');
    } else if (w > band.soft[1] + 1e-9 || (balanced && w < band.soft[0] - 1e-9)) {
      wide.push(line);
    }
  });
  if (outside.length) problems.push('Outside what the profile allows: ' + outside.join('; ') + '.');
  var heavy = products.filter(function (p) { return (Number(p.weightPct) || 0) > ctx.limits.concentration + 1e-9; })
    .map(function (p) { return p.productId; });
  if (heavy.length) {
    problems.push(heavy.map(label).join(', ') + ': a single product holds at most '
      + cpG(ctx.limits.concentration) + '% of the portfolio.');
  }
  var illiquid = products.reduce(function (t, p) {
    return t + ((ctx.liquidity || {})[p.productId] !== 'Daily' ? (Number(p.weightPct) || 0) : 0);
  }, 0);
  if (illiquid > ctx.limits.illiquid + 1e-9) {
    problems.push(illiquid.toFixed(2) + '% is not daily-dealing; at most ' + cpG(ctx.limits.illiquid) + '%.');
  }
  var priv = cpWeightsByLine(products)[ctx.privateLine] || 0;
  /* the $20m rule, until there is a private markets minimums table: then
     each product is held to its own, as a sleeve's are (D200) */
  if (priv > 0 && ctx.mandateSize != null && ctx.privateMinimum != null && ctx.mandateSize < ctx.privateMinimum) {
    problems.push('Private markets need a mandate of ' + cpMoney(ctx.privateMinimum) + ' or more.');
  }
  var lines = {};
  names.forEach(function (n) {
    lines[n] = { centre: bands[n].centre, soft: bands[n].soft, hard: bands[n].hard, weightPct: held[n] || 0 };
  });
  return { status: problems.length ? 'bad' : wide.length ? 'warn' : 'ok', problems: problems,
           wide: wide, lines: lines, total: total, balanced: balanced, heavy: heavy, illiquid: illiquid };
}

function cpWideText(check, profileLabel) {
  return check.wide.map(function (n) { return n + ' at ' + check.lines[n].weightPct.toFixed(2) + '%'; }).join(', ')
    + ' ' + (check.wide.length === 1 ? 'is' : 'are') + ' outside the ' + profileLabel
    + ' profile’s usual range ('
    + check.wide.map(function (n) { return cpG(check.lines[n].soft[0]) + '-' + cpG(check.lines[n].soft[1]) + '%'; }).join(', ')
    + ').';
}

/* What can be said of a mix nobody analysed (customPortfolio.figures). */
function cpFigures(weights, stats, anchorLines, analysedVol) {
  var total = Object.keys(weights).reduce(function (t, k) { return t + weights[k]; }, 0);
  if (!(total > 0)) return { estimatedReturnPct: null, volatilityLimitPct: null, volatilityPct: null };
  var ret = 0, cap = 0, anchorCap = 0;
  Object.keys(weights).forEach(function (l) {
    ret += weights[l] / 100 * ((stats[l] || {}).ret || 0);
    cap += weights[l] / 100 * ((stats[l] || {}).vol || 0);
  });
  Object.keys(anchorLines || {}).forEach(function (l) {
    anchorCap += anchorLines[l] / 100 * ((stats[l] || {}).vol || 0);
  });
  return { estimatedReturnPct: ret, volatilityLimitPct: cap,
           volatilityPct: analysedVol != null && anchorCap > 0 ? analysedVol * cap / anchorCap : null };
}
/* custom-portfolio-helpers-end */

/* ---- what this file reads --------------------------------------------- */
function esc(v) { return App.esc(v); }
function pct(v, dp) { return App.num(v, dp == null ? 2 : dp, '%'); }
function cp() { return App.customPortfolio() || null; }
function lib() { var l = App.cpLib(); return l && l.status === 'ready' ? l.data : null; }
function settings() { return App.opt('customPortfolio', {}) || {}; }
/* the $20m rule before a private markets minimums table, or null (D200) */
function cpPrivateMinimum() {
  var s = settings();
  if (!Object.prototype.hasOwnProperty.call(s, 'privateMinimum')) return 20000000;
  return s.privateMinimum == null ? null : s.privateMinimum;
}
function limits() {
  return Object.assign({ soft: 8, hard: 15, concentration: 25, illiquid: 30, maxProducts: 40 },
                       settings().limits || {});
}
function editable() { return App.mayBuildPortfolio(); }
function products() { return ((cp() || {}).products) || []; }
function profileLabel() { return App.riskLabel((cp() || {}).profile); }

var byIdCache = { data: null, map: {} };
function productOf(pid) {
  var l = lib();
  if (!l) return null;
  if (byIdCache.data !== l) {
    byIdCache = { data: l, map: {} };
    (l.products || []).forEach(function (p) { byIdCache.map[p.productId] = p; });
  }
  return byIdCache.map[pid] || null;
}
function nameOf(pid) { var p = productOf(pid); return p ? p.name : pid; }

/* a product no sleeve holds is suggested the category most products of its
   asset class are in */
function suggestedLine(product) {
  var l = lib(), counts = {};
  (l ? l.products : []).forEach(function (p) {
    if (p.category && p.assetClass === product.assetClass) counts[p.category] = (counts[p.category] || 0) + 1;
  });
  var best = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; })[0];
  return best || (l && l.lines.indexOf('Public Equity') !== -1 ? 'Public Equity' : (l ? l.lines[0] : ''));
}

function groupsOf() { return App.opt('rules.sleeveGroups', []) || []; }
function lineOf(category) {
  var groups = groupsOf();
  for (var i = 0; i < groups.length; i++) {
    if ((groups[i].categories || []).indexOf(category) !== -1) return groups[i].name;
  }
  return category;
}
function lineColor(line) {
  var groups = groupsOf();
  for (var i = 0; i < groups.length; i++) {
    if (groups[i].name === line) return App.catColor(groups[i].categories[0]);
  }
  return App.catColor(line);
}

function anchorEntry() {
  var l = lib(), c = cp();
  return l && c ? (l.profiles || {})[c.profile] || null : null;
}

function checkCtx() {
  var l = lib(), allowed = {}, liquidity = {}, label = {};
  (l ? l.products : []).forEach(function (p) {
    allowed[p.productId] = true; liquidity[p.productId] = p.liquidity; label[p.productId] = p.name;
  });
  /* a product no longer offered keeps its name from the build's own read */
  products().forEach(function (p) { if (!label[p.productId]) label[p.productId] = p.productId; });
  var anchor = anchorEntry();
  return { lines: l ? l.lines : [], anchor: anchor ? anchor.lines : {}, limits: limits(),
           countedIn: settings().countedIn || {}, allowed: allowed, liquidity: liquidity, label: label,
           mandateSize: App.mandateSize(), privateMinimum: cpPrivateMinimum(),
           privateLine: settings().privateLine || '' };
}

function check() { return lib() ? cpCheck(products(), checkCtx()) : null; }

/* ---- what the implementation layer asks for --------------------------------------- */
/* the build's lines, in the repository's order, each with its weight */
function cpLines() {
  var l = lib();
  if (!l) return [];
  var weights = {}, order = [];
  products().forEach(function (p) {
    if (!(p.category in weights)) { weights[p.category] = 0; order.push(p.category); }
    weights[p.category] += Number(p.weightPct) || 0;
  });
  order.sort(function (a, b) {
    var ia = l.lines.indexOf(a), ib = l.lines.indexOf(b);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  });
  return order.map(function (n) { return { name: n, weightPct: weights[n] }; });
}

/* a line as a sleeve: its products, each with its share of the line */
function cpSleeveFor(line) {
  if (!lib()) return null;
  var mine = products().filter(function (p) { return p.category === line; });
  var total = mine.reduce(function (t, p) { return t + (Number(p.weightPct) || 0); }, 0);
  var out = [];
  for (var i = 0; i < mine.length; i++) {
    var found = productOf(mine[i].productId);
    if (!found) return null;
    out.push(Object.assign({}, found, { weight: total > 0 ? (Number(mine[i].weightPct) || 0) / total : 0,
                                        weightPct: mine[i].weightPct }));
  }
  return { name: 'Custom portfolio', custom: false, products: out };
}

function cpIsEmpty() { return !products().length; }

/* ---- the page's own state ------------------------------------------------ */
var ui = { text: {}, bad: {}, added: [], history: [], future: [], snap: null, dirty: false, dlg: null,
           returnTo: null };

function snapshot() { return JSON.stringify(products()); }
function remember(snap) {
  ui.history.push(snap === undefined ? snapshot() : snap);
  if (ui.history.length > 100) ui.history.shift();
  ui.future = [];
}

/* Save a new set of products; a refusal is said where the build is. */
function save(next, words) {
  ui.dirty = false;
  return App.pushCustomPortfolio({ products: next }).then(function (refused) {
    if (refused) { App.announce('assertive', refused); showRefusal(refused); }
    else if (words) App.announce('polite', words);
    return refused;
  });
}
var refusal = null;
function showRefusal(text) { refusal = text; App.refresh(); }

function fmtWeight(v) { return v == null || v === '' ? '' : (Math.round(Number(v) * 100) / 100).toFixed(2); }

function slug(s) { return String(s).replace(/[^A-Za-z0-9]+/g, '-'); }

/* ---- the rail: the custom portfolio's own tier ---------------------------- */
function lineWords(check, line) {
  var band = check.lines[line];
  return 'usual ' + cpG(band.soft[0]) + '–' + cpG(band.soft[1]) + '%';
}

function cpRailHtml() {
  var c = cp(), l = lib(), aa = App.step() === 'aa';
  var count = products().length;
  var html = '<div class="tier-h"><h3>Custom portfolio</h3><span class="tier-h-r">'
    + '<span class="tier-count">' + count + ' product' + (count === 1 ? '' : 's') + '</span>'
    + (App.opt('capabilities.canAdmin', false)
        ? '<button type="button" class="tier-admin" data-openrepo aria-label="Open the repository" title="Repository">'
          + '<svg viewBox="0 0 20 20" aria-hidden="true"><use href="#i-sleeves"/></svg></button>' : '')
    + '</span></div>';
  if (!l) {
    var state = App.cpLib();
    return html + '<p class="field-note">' + (state && state.status === 'error'
      ? esc(state.error) : 'Reading what a custom portfolio may hold…') + '</p>';
  }
  var chk = check(), tone = !count ? '' : chk.status === 'bad' ? 'bad' : chk.status === 'warn' ? 'warn' : 'ok';
  var a = (c && c.approval) || null;
  var status;
  if (!count) status = 'Nothing built yet. ' + (aa ? 'It is built on Implementation.' : 'Add products, paste them from Excel or insert a sleeve.');
  else {
    status = (chk.balanced ? 'The portfolio adds to <b>100.00%</b>.'
      : 'The portfolio adds to <b>' + pct(chk.total) + '</b>: ' + pct(Math.abs(100 - chk.total))
        + (chk.total < 100 ? ' still to place.' : ' too much.'))
      + ' ' + (chk.status === 'bad' ? 'Not deliverable yet.'
        : chk.status === 'ok' ? 'Deliverable.'
        : a && a.state === 'approved' ? 'Approved by ' + esc(a.decidedBy) + ': deliverable.'
        : a && a.state === 'submitted' ? 'Awaiting an admin’s approval.'
        : 'Needs an admin’s approval.');
    if (a && a.state === 'approved' && chk.status === 'warn') tone = 'ok';
  }
  html += '<p class="cp-railstat ' + tone + '" role="status">' + status + '</p>';
  html += cpBenchmarkHtml();
  /* each line against its usual range; the overlays' lines counted in the
     line they are funded from */
  var counted = settings().countedIn || {}, weights = cpWeightsByLine(products());
  var order = l.lines.filter(function (n) { return n in chk.lines || n in weights; });
  html += '<div class="sl-list">';
  order.forEach(function (line) {
    var mine = products().filter(function (p) { return p.category === line; }).length;
    var band = chk.lines[line];
    if (!band) {
      html += '<div class="sl-row done cp-line"><span class="cat"><b>' + esc(line) + '</b><span>'
        + pct(weights[line] || 0) + '</span></span><span class="sl-shape"><span>counted in '
        + esc(counted[line] || '') + '</span><span>' + mine + ' product' + (mine === 1 ? '' : 's') + '</span></span></div>';
      return;
    }
    var out = count && (band.weightPct > band.hard[1] + 1e-9 || (chk.balanced && band.weightPct < band.hard[0] - 1e-9));
    var wideLine = !out && chk.wide.indexOf(line) !== -1;
    /* a range holds the line and what is counted in it */
    var withCounted = Math.abs(band.weightPct - (weights[line] || 0)) >= 0.005;
    html += '<div class="sl-row done cp-line' + (out ? ' is-out' : wideLine ? ' is-wide' : '') + '">'
      + '<span class="cat"><b>' + esc(line) + '</b><span>' + (count ? pct(weights[line] || 0) : '—') + '</span></span>'
      + '<span class="sl-shape"><span>' + lineWords(chk, line) + '</span>'
      + (withCounted ? '<span>' + pct(band.weightPct) + ' with what counts in it</span>' : '') + '<span>'
      + (mine ? mine + ' product' + (mine === 1 ? '' : 's') : 'no products') + '</span>'
      + (out ? '<span class="bad">outside ' + cpG(band.hard[0]) + '–' + cpG(band.hard[1]) + '%</span>'
         : wideLine ? '<span class="warn">' + (a && a.state === 'approved' ? 'approved' : 'needs approval') + '</span>' : '')
      + '</span></div>';
  });
  html += '</div>';
  if (aa) {
    html += '<div class="cp-rail-btns"><button type="button" class="btn btn-ghost fee-view" data-cpgo>'
      + (count ? 'Open it on Implementation ›' : 'Build it on Implementation ›') + '</button></div>';
  } else if (editable()) {
    html += '<div class="cp-rail-btns">'
      + '<button type="button" class="btn btn-ghost fee-view" data-cpact="add">+ Add products</button>'
      + '<button type="button" class="btn btn-ghost fee-view" data-cpact="paste">Paste</button>'
      + '<button type="button" class="btn btn-ghost fee-view" data-cpact="sleeve">Insert a sleeve</button></div>';
  } else {
    html += '<p class="field-note">Built by ' + esc((c && c.builtBy) || 'an admin')
      + '. Only an admin changes it.</p>';
  }
  if (!aa) {
    html += '<p class="chk-note cp-rail-note">No sleeves and no overlay switches here: Tactical Tilts and the '
      + 'Strategic Volatility Premium are products, added like any other.</p>';
  }
  return html;
}

/* The portfolio's benchmark (D211): a custom portfolio has no risk level,
   currency and hedging cell of its own, so whoever prepares it chooses one
   from the MAP benchmarks. The workbook, the deck and the account opening
   request name it. */
function cpBenchmarkHtml() {
  var l = lib(), held = (cp() || {}).benchmark || '';
  var options = ((l && l.benchmarks) || []).slice();
  if (held && options.indexOf(held) === -1) options.unshift(held);
  if (!options.length) {
    return '<div class="field cp-bench"><label>Benchmark</label><p class="field-note">None to choose yet: '
      + 'an admin adds them in Account Opening, MAP Benchmarks.</p></div>';
  }
  return '<div class="field cp-bench"><label for="cpBench">Benchmark</label>'
    + '<select id="cpBench" data-cpbench' + (App.canEdit() ? '' : ' disabled') + '>'
    + '<option value=""' + (held ? '' : ' selected') + '>Choose the benchmark…</option>'
    + options.map(function (b) {
        return '<option' + (b === held ? ' selected' : '') + '>' + esc(b) + '</option>';
      }).join('') + '</select>'
    + '<p class="field-note">' + (held ? 'Named in the workbook, the deck and the account opening request.'
      : 'Without one, the files name none and an account opening request cannot be submitted.') + '</p></div>';
}

/* ---- the implementation page ------------------------------------------- */
function cpSummaryHtml(gate) {
  var chk = check(), count = products().length;
  var placed = chk ? Math.min(100, chk.total) : 0;
  return '<div class="completion-summary"><div class="completion-line"><span>' + count + ' product'
    + (count === 1 ? '' : 's') + ' · ' + pct(chk ? chk.total : 0) + ' placed</span>'
    + '<strong class="cs-state ' + gate.tone + '">' + esc(gate.headline) + '</strong></div>'
    + '<div class="progress-track' + (chk && chk.balanced ? ' is-done' : '') + '" role="img" aria-label="'
    + pct(chk ? chk.total : 0) + ' of the portfolio placed"><i style="--progress:' + placed + '%"></i></div></div>';
}

function cpBlankHtml() {
  var l = lib();
  var html = '<div class="cp-blank" id="cpBlank"><h3>A blank slate</h3>'
    + '<p>Nothing is filled in. Add the products and their weights of the portfolio, and the categories follow '
    + 'from them. The <b>' + esc(profileLabel()) + '</b> risk profile only sets the limits the portfolio is checked against.</p>';
  if (!l) {
    var state = App.cpLib();
    return html + '<p class="field-note">' + (state && state.status === 'error' ? esc(state.error)
      : 'Reading what a custom portfolio may hold…') + '</p></div>';
  }
  function way(act, title, text) {
    return '<button type="button" class="cp-way" data-cpact="' + act + '"><b>' + title + '</b><span>' + text + '</span></button>';
  }
  if (editable()) {
    html += '<div class="cp-ways">'
      + way('add', '+ Add products', 'Search ' + esc(App.variant()) + '’s products and tick the ones you want; each lands in its category, ready for a weight.')
      + way('paste', 'Paste from Excel', 'Product names, tickers or ids and their weights of the portfolio, checked line by line.')
      + way('sleeve', 'Insert a sleeve', 'A library sleeve’s products at a weight of the portfolio you choose. After that they are ordinary rows.')
      + '</div>';
  } else {
    html += '<p>Only an admin builds a custom portfolio.</p>';
  }
  var chk = check(), lim = limits();
  html += '<p class="cp-lim-h">What the ' + esc(profileLabel()) + ' profile allows</p>'
    + '<table class="cp-lim"><thead><tr><th>Category</th><th class="n">Usual range</th><th class="n">At most</th></tr></thead><tbody>'
    + Object.keys(chk.lines).map(function (n) {
        var b = chk.lines[n];
        return '<tr><td>' + esc(n) + '</td><td class="n">' + cpG(b.soft[0]) + '–' + cpG(b.soft[1]) + '%</td>'
          + '<td class="n">' + cpG(b.hard[0]) + '–' + cpG(b.hard[1]) + '%</td></tr>';
      }).join('') + '</tbody></table>'
    + '<p class="cp-lim-more">Within the usual ranges it delivers; between them and the limits an admin approves it first; '
    + 'beyond, it cannot be delivered. Also: no product above ' + cpG(lim.concentration) + '% of the portfolio, at most '
    + cpG(lim.illiquid) + '% not daily-dealing, at most ' + lim.maxProducts + ' products, every position at its product’s '
    + 'minimum, and ' + (cpPrivateMinimum() == null
        ? 'each private markets product at its private markets minimum for the profile’s Full portfolio'
        : 'private markets from a ' + cpMoney(cpPrivateMinimum()) + ' mandate') + '. Tactical Tilts and '
    + 'the Strategic Volatility Premium count with the category they are funded from.</p></div>';
  return html;
}

function cpToolbarHtml() {
  if (!editable()) return '';
  var chk = check();
  return (refusal ? '<p class="impl-state error cp-refused" role="alert"><b>Not saved</b>' + esc(refusal) + '</p>' : '')
    + '<div class="cp-tools" role="toolbar" aria-label="Build the portfolio">'
    + '<button type="button" class="btn btn-primary" data-cpact="add">+ Add products</button>'
    + '<button type="button" class="btn btn-ghost" data-cpact="paste">Paste from Excel</button>'
    + '<button type="button" class="btn btn-ghost" data-cpact="sleeve">Insert a sleeve</button>'
    + '<button type="button" class="btn btn-ghost" data-cpact="scale100"'
    + (chk && chk.total > 0 && !chk.balanced ? '' : ' disabled')
    + ' title="Scale every weight in proportion so the portfolio adds to 100%">Scale to 100%</button>'
    + '<button type="button" class="btn btn-ghost" data-cpact="undo"' + (ui.history.length ? '' : ' disabled') + '>Undo</button>'
    + '<button type="button" class="btn btn-ghost" data-cpact="redo"' + (ui.future.length ? '' : ' disabled') + '>Redo</button>'
    + '<button type="button" class="btn btn-ghost" data-cpact="clear">Clear…</button></div>';
}

/* the category band: what built it, its weight to set, and its range */
function cpBandHtml(group) {
  var chk = check(), held = group.items.length;
  var html = '<span class="pill p-sleeve is-built" title="Built product by product">Built<small class="pc" aria-label="'
    + held + ' product' + (held === 1 ? '' : 's') + '">' + held + '</small></span>';
  if (editable() && held) {
    var id = 'cps-' + slug(group.category);
    html += '<span class="cp-scale"><label for="' + id + '">Set to</label><input id="' + id + '" data-cpscale="'
      + esc(group.category) + '" value="' + fmtWeight(group.weightPct) + '" inputmode="decimal" autocomplete="off"'
      + ' aria-label="Scale ' + esc(group.category) + ' to a weight of the portfolio, its products in proportion"><span>%</span></span>';
  }
  var band = chk && chk.lines[group.category];
  if (band) {
    var w = band.weightPct;
    var out = w > band.hard[1] + 1e-9 || (chk.balanced && w < band.hard[0] - 1e-9);
    var wideLine = !out && chk.wide.indexOf(group.category) !== -1;
    var own = group.items.reduce(function (t, i) { return t + (Number(i.weight) || 0); }, 0);
    html += '<span class="cp-band' + (out ? ' out' : wideLine ? ' wide' : '') + '">usual '
      + cpG(band.soft[0]) + '–' + cpG(band.soft[1]) + '%' + (out ? ' · at most ' + cpG(band.hard[0]) + '–' + cpG(band.hard[1]) + '%' : '')
      + (Math.abs(w - own) >= 0.005 ? ' · ' + pct(w) + ' with what counts in it' : '') + '</span>';
  } else if ((settings().countedIn || {})[group.category]) {
    html += '<span class="cp-band">counted in ' + esc(settings().countedIn[group.category]) + '</span>';
  }
  return html;
}

function cpNameExtras(item, group) {
  var chk = check(), html = '';
  if (chk && chk.heavy.indexOf(item.productId) !== -1) {
    html += ' <span class="bdg b-breach sm" title="A single product holds at most ' + cpG(limits().concentration)
      + '% of the portfolio">above ' + cpG(limits().concentration) + '%</span>';
  }
  var product = productOf(item.productId);
  if (!product) html += ' <span class="bdg b-breach sm">no longer offered</span>';
  if (editable() && product && !product.category) {
    var l = lib();
    html += '<select class="cp-cat" data-cpcat="' + esc(item.productId) + '" aria-label="Category of ' + esc(item.name)
      + '" title="No sleeve holds this product: its category was suggested from its asset class">'
      + l.lines.map(function (n) { return '<option' + (n === group.category ? ' selected' : '') + '>' + esc(n) + '</option>'; }).join('')
      + '</select><span class="cp-sug">suggested</span>';
  }
  if (editable()) {
    html += '<button type="button" class="cp-x" data-cprm="' + esc(item.productId) + '" aria-label="Remove '
      + esc(item.name) + '" title="Remove">×</button>';
  }
  return html;
}

function cpWeightCell(item) {
  var row = products().filter(function (p) { return p.productId === item.productId; })[0] || {};
  var text = item.productId in ui.text ? ui.text[item.productId] : fmtWeight(row.weightPct);
  var zero = !row.weightPct;
  return '<input type="text" class="cp-w' + (zero ? ' zero' : '') + (ui.added.indexOf(item.productId) !== -1 ? ' new' : '')
    + '" id="cpw-' + slug(item.productId) + '" data-cpw="' + esc(item.productId) + '" value="' + esc(text)
    + '" inputmode="decimal" autocomplete="off" aria-label="Weight of ' + esc(item.name) + ', percent of the portfolio"'
    + (ui.bad[item.productId] ? ' aria-invalid="true"' : '') + (editable() ? '' : ' disabled') + '>';
}

function cpTotalNote(weight) {
  if (Math.abs(weight - 100) < CP_BALANCE) return '';
  return '<small class="cp-left">' + pct(Math.abs(100 - weight)) + (weight < 100 ? ' to place' : ' too much') + '</small>';
}

/* the gate's custom clauses: a problem that stops delivery, or the approval
   that is the way on (the implementation layer's export gate) */
function cpGate(groups) {
  if (!lib()) {
    var state = App.cpLib();
    return { problem: state && state.status === 'error' ? state.error : 'Reading what a custom portfolio may hold…' };
  }
  var chk = check();
  if (!products().length) return { problem: 'Nothing is built yet: add products, paste them from Excel or insert a sleeve.', empty: true };
  var bad = Object.keys(ui.bad).filter(function (k) { return ui.bad[k]; });
  if (bad.length) {
    return { problem: bad.length + ' weight' + (bad.length === 1 ? '' : 's')
      + ' to fix: a weight is a percentage of the portfolio, up to two places.' };
  }
  if (chk.status === 'bad') return { problem: chk.problems.join(' ') };
  if (chk.status === 'ok') return { problem: null };
  var c = cp(), a = c.approval || null, user = App.opt('capabilities.user', ''), admin = App.opt('capabilities.canAdmin', false);
  var wide = cpWideText(chk, profileLabel());
  if (a && a.state === 'approved') return { approved: 'Approved by ' + a.decidedBy + ' at ' + (a.decidedAt || '').slice(11, 16) + '.' };
  function btn(act, label, primary) {
    return '<button type="button" class="btn ' + (primary ? 'btn-primary' : 'btn-ghost') + '" data-cpappr="' + act + '">' + label + '</button>';
  }
  if (!a || a.state === 'returned') {
    return { approval: { headline: 'Needs approval',
      text: (a ? 'Sent back by ' + a.decidedBy + ': “' + (a.reason || '') + '” ' : '') + wide
        + ' An admin approves it before it downloads.',
      actions: editable() ? btn('submit', 'Submit for approval', true) : '' } };
  }
  var at = (a.at || '').slice(11, 16);
  if (admin && user !== c.builtBy && user !== a.by) {
    return { approval: { headline: 'Awaiting your approval',
      text: 'Submitted by ' + a.by + ' at ' + at + (a.note ? ': “' + a.note + '”' : '.') + ' ' + wide,
      actions: btn('return', 'Send back') + btn('approve', 'Approve', true) } };
  }
  return { approval: { headline: 'Awaiting approval',
    text: 'Submitted by ' + a.by + ' at ' + at + '. ' + (admin ? 'You built or submitted it, so another admin approves it.'
      : 'An admin approves it or sends it back.'),
    actions: editable() || user === a.by ? btn('withdraw', 'Withdraw') : '' } };
}

/* ---- step 1: the custom column, the strategic ones beside it ---------------- */
function cpAllocHtml() {
  var l = lib(), c = cp();
  var columns = App.columns();
  var mine = cpWeightsByLine(products());
  var built = products().length > 0 && !!l;
  var dash = '—';
  function grouped(col) {
    var out = {};
    ((col.data && col.data.categories) || []).forEach(function (cat) {
      var line = lineOf(cat.name);
      out[line] = (out[line] || 0) + cat.weightPct;
    });
    return out;
  }
  var others = columns.map(grouped);
  var order = (l ? l.lines : Object.keys(mine)).filter(function (n) {
    return (mine[n] || 0) > 0 || others.some(function (o) { return (o[n] || 0) > 0; });
  });
  function mix(weights) {
    return '<span class="col-mix" aria-hidden="true">' + order.filter(function (n) { return (weights[n] || 0) > 0; })
      .map(function (n) { return '<span style="flex:' + weights[n] + ' 0 0;background:' + lineColor(n) + '"></span>'; }).join('')
      + '</span>';
  }
  var plus = App.plusColumn && editable() && columns.length < App.opt('rules.maxPortfolios', 4);
  var html = '<caption class="sr-only">The custom portfolio and the strategic portfolios beside it</caption><thead><tr>'
    + '<th scope="col" class="rowhead">Asset Class</th>'
    + '<th scope="col" class="num" title="The custom portfolio"><span class="col-head"><span class="col-mark is-prop" aria-hidden="true">P</span>'
    + '<span class="col-nm"><small class="col-role">Proposed</small>Custom' + (built ? '' : '<small class="cp-unbuilt">not built yet</small>')
    + '</span></span>' + (built ? mix(mine) : '') + '</th>'
    + columns.map(function (col, i) {
        var name = (col.data && col.data.header) || '';
        return '<th scope="col" class="num' + (i ? ' has-rm' : '') + '" title="' + esc((col.data && col.data.name) || name) + '">'
          + '<span class="col-head"><span class="col-mark" aria-hidden="true">' + (i + 1) + '</span><span class="col-nm">'
          + (i === 0 ? '<small class="col-role cp-role">Its profile</small>' : '') + esc(name) + '</span>'
          + (i && editable() ? '<button type="button" class="col-rm" data-remove="' + i + '" title="Remove ' + esc(name)
              + '" aria-label="Remove ' + esc(name) + '">×</button>' : '')
          + '</span>' + (col.data ? mix(others[i]) : '') + '</th>';
      }).join('')
    + (plus ? '<th scope="col" class="num addcol"><button type="button" id="plusbtn" class="plus" aria-label="Add a comparison portfolio">+</button></th>' : '')
    + '</tr></thead><tbody>';
  function cells(fn) {
    return columns.map(function (col, i) {
      if (!col.data) return '<td class="num"><span class="cw">' + (col.status === 'error' ? dash : '<span class="skel"></span>') + '</span></td>';
      return '<td class="num"><span class="cw">' + fn(col, i) + '</span></td>';
    }).join('') + (plus ? '<td class="num addcol"></td>' : '');
  }
  order.forEach(function (n) {
    html += '<tr class="cat"><th scope="row"><span class="cw"><span class="cat-sw" aria-hidden="true" style="background:'
      + lineColor(n) + '"></span>' + esc(n) + '</span></th>'
      + '<td class="num"><span class="cw">' + (built ? pct(mine[n] || 0, 1) : dash) + '</span></td>'
      + cells(function (col, i) { return pct(others[i][n] || 0, 1); }) + '</tr>';
    if (!built) return;
    var classes = {}, seen = [];
    products().forEach(function (p) {
      if (p.category !== n) return;
      var product = productOf(p.productId), name = product ? product.assetClass : p.productId;
      if (!(name in classes)) { classes[name] = 0; seen.push(name); }
      classes[name] += Number(p.weightPct) || 0;
    });
    seen.forEach(function (name, ix) {
      html += '<tr class="asset cp-ac' + (ix % 2 ? ' alt' : '') + '"><th scope="row"><span class="cw">' + esc(name) + '</span></th>'
        + '<td class="num"><span class="cw">' + pct(classes[name], 1) + '</span></td>'
        + cells(function () { return '<span class="cp-dash">' + dash + '</span>'; }) + '</tr>';
    });
  });
  var total = Object.keys(mine).reduce(function (t, k) { return t + mine[k]; }, 0);
  var anchor = anchorEntry();
  var figures = l && built ? cpFigures(mine, l.lineStats || {}, anchor ? anchor.lines : {},
                                       anchor ? (anchor.metrics || {}).volatilityPct : null) : null;
  html += '<tr class="total"><th scope="row"><span class="cw">Total</span></th><td class="num"><span class="cw">'
    + (built ? pct(total, 1) : dash) + '</span></td>' + cells(function () { return '100.0%'; }) + '</tr>'
    + '<tr class="metric"><th scope="row"><span class="cw">Estimated Mean Return</span></th><td class="num"><span class="cw">'
    + (figures ? pct(figures.estimatedReturnPct) + '<span class="cp-est">est.</span>' : dash) + '</span></td>'
    + cells(function (col) { return pct(col.data.metrics.estimatedReturnPct); }) + '</tr>'
    + '<tr class="metric"><th scope="row"><span class="cw">Sharpe Ratio</span></th><td class="num"><span class="cw">' + dash + '</span></td>'
    + cells(function (col) { return Number(col.data.metrics.sharpe).toFixed(2); }) + '</tr>'
    + '<tr class="metric"><th scope="row"><span class="cw">Volatility</span></th><td class="num"><span class="cw">'
    + (figures && figures.volatilityPct != null ? '~' + pct(figures.volatilityPct, 1) + '<span class="cp-est">ind.</span>' : dash)
    + '</span></td>' + cells(function (col) { return pct(col.data.metrics.volatilityPct); }) + '</tr>';
  return html + '</tbody>';
}

/* ---- the dialogs ----------------------------------------------------------- */
function openDlg(d) {
  ui.dlg = d; ui.returnTo = document.activeElement;
  App.setBackgroundInert(true);
  renderDlg();
  var first = document.querySelector('#cpDialog [data-first]');
  if (first) first.focus();
}
function closeDlg() {
  ui.dlg = null;
  renderDlg();
  App.setBackgroundInert(false);
  var back = ui.returnTo; ui.returnTo = null;
  window.setTimeout(function () { if (back && document.contains(back)) back.focus(); }, 0);
}

function renderDlg() {
  var host = document.getElementById('cpDialog'); if (!host) return;
  var d = ui.dlg;
  if (!d) { host.hidden = true; host.innerHTML = ''; return; }
  var inner = { add: addDlg, paste: pasteDlg, sleeve: sleeveDlg, clear: clearDlg, note: noteDlg }[d.kind](d);
  host.hidden = false;
  host.innerHTML = '<div class="scrim" data-cpclose></div><div class="dialog wide rc cp-dlg' + (d.kind === 'add' || d.kind === 'sleeve' ? ' cp-wide' : '')
    + '" role="dialog" aria-modal="true" aria-labelledby="cpDlgTitle"><button type="button" class="dlg-close" data-cpclose aria-label="Close">×</button>'
    + inner + '</div>';
  var on = host.querySelector('.cp-sl input:checked');
  if (on) {
    var list = host.querySelector('.cp-sl-list'), row = on.closest('.cp-sl');
    list.scrollTop = Math.max(0, row.offsetTop - list.offsetTop - 40);
  }
}

var ADD_FILTERS = [['assetClass', 'Asset class'], ['vehicle', 'Vehicle'], ['style', 'Style'],
                   ['source', 'Source'], ['liquidity', 'Liquidity'], ['category', 'Category']];
function lineFor(product) { return product.category || suggestedLine(product); }

function addDlg(d) {
  var l = lib(), held = products().map(function (p) { return p.productId; });
  var q = (d.q || '').trim().toLowerCase();
  var list = l.products.filter(function (p) {
    if (q && [p.name, p.ticker, p.productId, p.assetClass, p.vehicle].join(' ').toLowerCase().indexOf(q) === -1) return false;
    return ADD_FILTERS.every(function (f) {
      var want = d.f[f[0]];
      return !want || (f[0] === 'category' ? lineFor(p) : p[f[0]]) === want;
    });
  });
  var k = d.sort.k, dir = d.sort.dir;
  list.sort(function (a, b) {
    var x = k === 'category' ? lineFor(a) : a[k], y = k === 'category' ? lineFor(b) : b[k];
    if (k === 'productCost' || k === 'minimumInvestment') {
      x = x == null ? Infinity : x; y = y == null ? Infinity : y; return (x - y) * dir;
    }
    return String(x).localeCompare(String(y)) * dir;
  });
  function options(f) {
    var vals = {};
    l.products.forEach(function (p) { vals[f === 'category' ? lineFor(p) : p[f]] = 1; });
    return Object.keys(vals).sort().map(function (v) {
      return '<option' + (d.f[f] === v ? ' selected' : '') + '>' + esc(v) + '</option>';
    }).join('');
  }
  function th(key, label, num) {
    var on = d.sort.k === key;
    return '<th class="' + (num ? 'n' : '') + '" aria-sort="' + (on ? (dir > 0 ? 'ascending' : 'descending') : 'none')
      + '"><button type="button" data-cpsort="' + key + '">' + label + (on ? (dir > 0 ? ' ↑' : ' ↓') : '') + '</button></th>';
  }
  var room = limits().maxProducts - held.length, n = d.ticked.length;
  return '<div class="rc-head"><h2 id="cpDlgTitle">Add products</h2><p class="rc-meta"><span>'
    + esc(App.variant()) + '’s products'
    + '</span><span><b>' + l.products.length + '</b> products</span><span>' + held.length + ' in the portfolio</span></p></div>'
    + '<div class="cp-pick-h"><input type="search" id="cpAddQ" data-first placeholder="Search names, tickers, asset classes…" value="'
    + esc(d.q || '') + '" aria-label="Search the products">'
    + ADD_FILTERS.map(function (f) {
        return '<select data-cpfilter="' + f[0] + '" aria-label="' + f[1] + '"><option value="">' + f[1] + ': any</option>' + options(f[0]) + '</select>';
      }).join('')
    + '</div><div class="cp-pick-wrap"><table class="cp-pick"><thead><tr><th><span class="sr-only">Tick</span></th>'
    + th('name', 'Product') + th('category', 'Category') + th('assetClass', 'Asset class') + th('vehicle', 'Vehicle')
    + th('liquidity', 'Liquidity') + th('productCost', 'Cost', true) + th('minimumInvestment', 'Minimum', true) + '</tr></thead><tbody>'
    + (list.length ? list.map(function (p) {
        var isHeld = held.indexOf(p.productId) !== -1, on = d.ticked.indexOf(p.productId) !== -1;
        return '<tr' + (isHeld ? ' class="held"' : '') + '><td>' + (isHeld ? ''
            : '<input type="checkbox" data-cptick="' + esc(p.productId) + '"' + (on ? ' checked' : '')
              + (!on && n >= room ? ' disabled' : '') + ' aria-label="Add ' + esc(p.name) + '">') + '</td>'
          + '<td><b>' + esc(p.name) + '</b><small>' + esc(p.ticker && p.ticker !== '—' ? p.ticker + ' · ' : '')
          + esc(p.style) + ' · ' + esc(p.source) + '</small>' + (isHeld ? '<span class="cp-held">In the portfolio</span>' : '')
          + '</td>'
          + '<td>' + esc(lineFor(p)) + (p.category ? '' : '<span class="cp-sug">suggested</span>') + '</td>'
          + '<td>' + esc(p.assetClass) + '</td><td>' + esc(p.vehicle) + '</td><td>' + esc(p.liquidity) + '</td>'
          + '<td class="n">' + pct(p.productCost) + '</td><td class="n">'
          + (p.minimumInvestment ? App.money(p.minimumInvestment) : '—') + '</td></tr>';
      }).join('') : '<tr><td colspan="8" class="cp-none">No product matches. Clear the search or a filter.</td></tr>')
    + '</tbody></table></div><div class="rc-actions"><span class="rc-note">'
    + (n ? n + ' ticked · each lands in its category with no weight, ready to type' : room > 0
        ? 'Tick the products to add' : 'The portfolio holds the most products it may: ' + limits().maxProducts)
    + '</span><span class="cf-actions"><button type="button" class="btn btn-ghost" data-cpclose>Cancel</button>'
    + '<button type="button" class="btn btn-primary" data-cpaddgo' + (n ? '' : ' disabled') + '>Add '
    + (n ? n + ' product' + (n === 1 ? '' : 's') : 'products') + '</button></span></div>';
}

function parsePaste(text) {
  var l = lib(), rows = [], errors = [], seen = {};
  String(text || '').split(/\r\n|\r|\n/).forEach(function (line, i) {
    if (!line.trim()) return;
    var cells = line.split('\t').map(function (c) { return c.trim(); });
    if (cells.length < 2) cells = line.split(/\s{2,}|,(?=\s*\d)/).map(function (c) { return c.trim(); });
    var name = cells[0], last = (cells[cells.length - 1] || '');
    var w = cpParse(last);
    if (cells.length < 2 || w === null || isNaN(w)) {
      if (i === 0) return;                      /* a heading row */
      errors.push('Line ' + (i + 1) + ': “' + last + '” is not a weight to two places.');
      rows.push({ line: i + 1, name: name, bad: 'not a weight' });
      return;
    }
    var key = name.toLowerCase();
    var p = l.products.filter(function (x) {
      return x.name.toLowerCase() === key || x.productId.toLowerCase() === key
        || (x.ticker && x.ticker !== '—' && x.ticker.toLowerCase() === key);
    })[0];
    if (!p) {
      errors.push('Line ' + (i + 1) + ': no product a custom portfolio here may hold is called “' + name + '”.');
      rows.push({ line: i + 1, name: name, w: w, bad: 'not offered' });
      return;
    }
    if (seen[p.productId]) {
      errors.push('Line ' + (i + 1) + ': ' + p.name + ' is in the block twice.');
      rows.push({ line: i + 1, name: name, w: w, bad: 'twice' });
      return;
    }
    seen[p.productId] = 1;
    rows.push({ line: i + 1, p: p, w: w });
  });
  var total = rows.reduce(function (t, r) { return t + (r.p ? r.w : 0); }, 0);
  return { rows: rows, errors: errors, total: Math.round(total * 100) / 100 };
}

function pasteDlg(d) {
  var r = parsePaste(d.text), good = r.rows.filter(function (x) { return x.p; });
  var any = products().length > 0, ok = good.length && !r.errors.length;
  return '<div class="rc-head"><h2 id="cpDlgTitle">Paste from Excel</h2><p class="rc-meta"><span>Two columns: product and weight of the portfolio</span></p></div>'
    + '<p class="cp-sub">Copy the block in Excel and paste it here. Products are matched by name, ticker or id; headings and % signs are fine. '
    + 'A block that does not read cleanly is not taken in.</p>'
    + '<textarea class="cp-paste" id="cpPaste" data-first aria-label="The pasted block">' + esc(d.text) + '</textarea>'
    + (r.rows.length ? '<table class="cp-prev"><thead><tr><th>Line</th><th>Product</th><th>Category</th><th class="n">Weight</th><th></th></tr></thead><tbody>'
      + r.rows.map(function (x) {
          return '<tr' + (x.bad ? ' class="bad"' : '') + '><td>' + x.line + '</td><td>' + esc(x.p ? x.p.name : x.name) + '</td><td>'
            + (x.p ? esc(lineFor(x.p)) : '') + '</td><td class="n">' + (x.w == null ? '' : fmtWeight(x.w) + '%') + '</td><td>'
            + (x.bad ? esc(x.bad) : '✓') + '</td></tr>';
        }).join('') + '</tbody></table><p class="rc-note cp-paste-note">' + good.length + ' product' + (good.length === 1 ? '' : 's')
        + ' read · they add to ' + fmtWeight(r.total) + '%' + (Math.abs(r.total - 100) < CP_BALANCE ? '' : ' (the table will say what is left to place)') + '</p>' : '')
    + (r.errors.length ? '<p class="md-err" role="alert">' + esc(r.errors[0]) + (r.errors.length > 1 ? ' And ' + (r.errors.length - 1) + ' more.' : '')
        + ' Nothing is taken in until every line reads.</p>' : '')
    + '<div class="rc-actions"><span class="rc-note"></span><span class="cf-actions"><button type="button" class="btn btn-ghost" data-cpclose>Cancel</button>'
    + (any ? '<button type="button" class="btn btn-ghost" data-cppaste="add"' + (ok ? '' : ' disabled') + '>Add to the portfolio</button>' : '')
    + '<button type="button" class="btn btn-primary" data-cppaste="replace"' + (ok ? '' : ' disabled') + '>' + (any ? 'Replace the portfolio' : 'Take it in')
    + '</button></span></div>';
}

function sleeveDefault(sleeve) {
  if (products().some(function (p) { return p.category === sleeve.category; })) return '';
  var anchor = anchorEntry(), w = anchor ? anchor.lines[sleeve.category] || 0 : 0;
  return w > 0 ? fmtWeight(w) : '';
}

function sleeveDlg(d) {
  var l = lib(), s = l.library[d.ix];
  var chk = check(), band = chk && chk.lines[(settings().countedIn || {})[s.category] || s.category];
  var w = cpParse(d.w), ok = w !== null && !isNaN(w) && w > 0;
  var shares = ok ? cpScaleTo(s.products.map(function (p) { return p.weight; }), Math.round(w * 100) / 100) : null;
  var held = {}; products().forEach(function (p) { held[p.productId] = p; });
  var cats = []; l.library.forEach(function (x) { if (cats.indexOf(x.category) === -1) cats.push(x.category); });
  var list = cats.map(function (cat) {
    return '<p class="cp-sl-cat">' + esc(cat) + '</p>' + l.library.map(function (x, i) {
      if (x.category !== cat) return '';
      return '<label class="cp-sl"><input type="radio" name="cpSleeve" value="' + i + '"' + (i === d.ix ? ' checked' : '')
        + '><span><b>' + esc(x.name) + '</b><small>' + x.products.length + ' product' + (x.products.length === 1 ? '' : 's') + '</small></span></label>';
    }).join('');
  }).join('');
  var rows = s.products.map(function (p, i) {
    var at = held[p.productId], was = at ? Number(at.weightPct) || 0 : 0;
    return '<tr><td>' + esc(nameOf(p.productId)) + (at ? '<small>already in the portfolio at ' + pct(was) + '</small>' : '') + '</td>'
      + '<td class="n">' + pct(p.weight * 100) + '</td><td class="n">' + (ok ? pct(was + shares[i]) : '—') + '</td></tr>';
  }).join('');
  return '<div class="rc-head"><h2 id="cpDlgTitle">Insert a sleeve</h2><p class="rc-meta"><span>' + esc(App.variant())
    + '’s library</span><span><b>' + l.library.length + '</b> sleeves</span></p></div>'
    + '<p class="cp-sub">A sleeve’s products go in at a weight of the portfolio you choose, in the sleeve’s proportions. '
    + 'After that they are ordinary rows: the sleeve is not kept as a sleeve.</p>'
    + '<div class="cp-sl-grid"><div class="cp-sl-list" role="radiogroup" aria-label="Sleeve">' + list + '</div>'
    + '<div class="cp-sl-prev"><h3>' + esc(s.name) + '</h3><p class="rc-note">' + esc(s.category)
    + (band ? ' · the ' + esc(profileLabel()) + ' profile’s usual range is ' + cpG(band.soft[0]) + '–' + cpG(band.soft[1])
        + '%, at most ' + cpG(band.hard[0]) + '–' + cpG(band.hard[1]) + '%' : '') + '.</p>'
    + '<div class="cp-sl-w"><label for="cpSlW">Its weight of the portfolio</label><input id="cpSlW" data-first value="' + esc(d.w)
    + '" inputmode="decimal" autocomplete="off"' + (d.w !== '' && !ok ? ' aria-invalid="true"' : '') + '><span>%</span></div>'
    + '<table class="cp-prev"><thead><tr><th>Product</th><th class="n">Of the sleeve</th><th class="n">Of the portfolio</th></tr></thead><tbody>'
    + rows + '</tbody></table></div></div>'
    + '<div class="rc-actions"><span class="rc-note">' + (ok ? s.products.length + ' product' + (s.products.length === 1 ? '' : 's')
        + ' at ' + pct(w) + ' of the portfolio' : 'Give it a weight of the portfolio') + '</span>'
    + '<span class="cf-actions"><button type="button" class="btn btn-ghost" data-cpclose>Cancel</button>'
    + '<button type="button" class="btn btn-primary" data-cpslgo' + (ok ? '' : ' disabled') + '>Insert the sleeve</button></span></div>';
}

function clearDlg() {
  var n = products().length;
  return '<div class="rc-head"><h2 id="cpDlgTitle">Clear the portfolio</h2><p class="rc-meta"><span>' + n + ' product' + (n === 1 ? '' : 's') + '</span></p></div>'
    + '<p class="cp-sub">Every product goes, and Implementation is a blank slate again. Undo brings them back.</p>'
    + '<div class="rc-actions"><span class="rc-note"></span><span class="cf-actions"><button type="button" class="btn btn-ghost" data-cpclose data-first>Cancel</button>'
    + '<button type="button" class="btn btn-primary" data-cpclearyes>Clear it</button></span></div>';
}

/* a note to the approver, or the reason it is sent back */
function noteDlg(d) {
  var back = d.action === 'return';
  var max = settings().noteMax || 300;
  return '<div class="rc-head"><h2 id="cpDlgTitle">' + (back ? 'Send it back' : 'Submit for approval') + '</h2>'
    + '<p class="rc-meta"><span>' + esc(profileLabel()) + ' risk profile</span><span>' + products().length + ' products</span></p></div>'
    + '<p class="cp-sub">' + (back ? 'Say what needs to change. Whoever built it sees it on the download card.'
        : esc(cpWideText(check(), profileLabel())) + ' An admin who did not build it sees the portfolio, the ranges and your note, '
          + 'and approves it or sends it back. Changing the portfolio or its risk profile after you submit withdraws the request.') + '</p>'
    + '<label for="cpNote" class="cp-note-l">' + (back ? 'What needs to change' : 'Note to the approver') + '</label>'
    + '<textarea id="cpNote" data-first class="cp-paste cp-note" maxlength="' + max + '">' + esc(d.note || '') + '</textarea>'
    + (d.error ? '<p class="md-err" role="alert">' + esc(d.error) + '</p>' : '')
    + '<div class="rc-actions"><span class="rc-note"></span><span class="cf-actions"><button type="button" class="btn btn-ghost" data-cpclose>Cancel</button>'
    + '<button type="button" class="btn btn-primary" data-cpnotego' + (back && !(d.note || '').trim() ? ' disabled' : '') + '>'
    + (back ? 'Send back' : 'Submit') + '</button></span></div>';
}

/* ---- the actions ------------------------------------------------------------ */
function rowsNow() { return products().map(function (p) { return Object.assign({}, p); }); }

function addProducts(ids) {
  /* a product a notice is about asks for it first (D199); declined, none
     of them is added */
  var fresh = ids.filter(function (id) { return !rowsNow().some(function (r) { return r.productId === id; }); });
  var items = fresh.map(function (id) { var p = productOf(id); return { category: lineFor(p), product: p }; })
    .filter(function (it) { return it.product; });
  if (App.noticeGateFor(items, 'Custom portfolio', function () { putProducts(ids); }, function () {})) return;
  putProducts(ids);
}

function putProducts(ids) {
  remember();
  var next = rowsNow();
  ids.forEach(function (id) {
    if (next.some(function (r) { return r.productId === id; })) return;
    var product = productOf(id);
    next.push({ productId: id, weightPct: null, category: lineFor(product) });
  });
  ui.added = ids.slice();
  closeDlg();
  save(next, ids.length + ' product' + (ids.length === 1 ? '' : 's') + ' added. Give each a weight.').then(function () {
    var first = document.getElementById('cpw-' + slug(ids[0]));
    if (first) { first.focus(); first.scrollIntoView({ block: 'center' }); }
  });
}

function applyPaste(mode) {
  var r = parsePaste(ui.dlg.text);
  if (r.errors.length) return;
  var items = r.rows.map(function (x) { return { category: lineFor(x.p), product: x.p }; });
  if (App.noticeGateFor(items, 'Custom portfolio', function () { takePaste(mode, r); }, function () {})) return;
  takePaste(mode, r);
}

function takePaste(mode, r) {
  remember();
  var next = mode === 'replace' ? [] : rowsNow();
  r.rows.forEach(function (x) {
    var at = next.filter(function (y) { return y.productId === x.p.productId; })[0];
    if (at) at.weightPct = x.w; else next.push({ productId: x.p.productId, weightPct: x.w, category: lineFor(x.p) });
  });
  ui.added = r.rows.map(function (x) { return x.p.productId; });
  ui.text = {}; ui.bad = {};
  closeDlg();
  save(next, r.rows.length + ' product' + (r.rows.length === 1 ? '' : 's') + ' taken in from the paste.');
}

function insertSleeve() {
  var d = ui.dlg, s = lib().library[d.ix], w = cpParse(d.w);
  if (w === null || isNaN(w) || !(w > 0)) return;
  remember();
  var next = rowsNow();
  var shares = cpScaleTo(s.products.map(function (p) { return p.weight; }), Math.round(w * 100) / 100);
  s.products.forEach(function (p, i) {
    var at = next.filter(function (r) { return r.productId === p.productId; })[0];
    if (at) at.weightPct = Math.round(((Number(at.weightPct) || 0) + shares[i]) * 100) / 100;
    else next.push({ productId: p.productId, weightPct: shares[i], category: s.category });
    delete ui.text[p.productId];
  });
  ui.added = s.products.map(function (p) { return p.productId; });
  closeDlg();
  save(next, s.name + ' inserted at ' + pct(w) + ': ' + s.products.length + ' products, now ordinary rows.');
}

function scaleLine(line, text) {
  var target = cpParse(text), next = rowsNow();
  var mine = next.filter(function (r) { return r.category === line; });
  if (target === null || isNaN(target) || !mine.length) { App.refresh(); return; }
  var values = mine.map(function (r) { return Number(r.weightPct) || 0; });
  if (!values.some(function (v) { return v > 0; })) values = values.map(function () { return 1; });
  remember();
  var out = cpScaleTo(values, Math.round(target * 100) / 100);
  mine.forEach(function (r, i) { r.weightPct = out[i]; delete ui.text[r.productId]; ui.bad[r.productId] = false; });
  save(next, line + ' set to ' + pct(target) + ', its products in proportion.');
}

function scale100() {
  var next = rowsNow(), values = next.map(function (r) { return Number(r.weightPct) || 0; });
  if (!values.some(function (v) { return v > 0; })) return;
  remember();
  var out = cpScaleTo(values, 100);
  next.forEach(function (r, i) { r.weightPct = out[i]; });
  ui.text = {}; ui.bad = {};
  save(next, 'Every weight scaled in proportion: the portfolio adds to 100%.');
}

function restore(rows) {
  ui.text = {}; ui.bad = {};
  save(rows);
}

/* ---- events -------------------------------------------------------------------- */
document.addEventListener('click', function (e) {
  var t = e.target.closest ? e.target.closest('button,[data-cpclose]') : null;
  if (!t) return;
  var ds = t.dataset;
  if (ds.cpgo !== undefined) { App.setStep('impl'); return; }
  if (ds.cpclose !== undefined) { closeDlg(); return; }
  if (ds.cpact) {
    var act = ds.cpact;
    if (!lib() || !editable()) return;
    refusal = null;
    if (act === 'add') { openDlg({ kind: 'add', q: '', f: {}, ticked: [], sort: { k: 'name', dir: 1 } }); return; }
    if (act === 'paste') { openDlg({ kind: 'paste', text: '' }); return; }
    if (act === 'sleeve') {
      var cats = products().map(function (p) { return p.category; });
      var lib_ = lib().library, ix = 0;
      for (var i = 0; i < lib_.length; i++) { if (cats.indexOf(lib_[i].category) === -1) { ix = i; break; } }
      if (!lib_.length) return;
      openDlg({ kind: 'sleeve', ix: ix, w: sleeveDefault(lib_[ix]), touched: false });
      return;
    }
    if (act === 'clear') { openDlg({ kind: 'clear' }); return; }
    if (act === 'scale100') { scale100(); return; }
    if (act === 'undo' && ui.history.length) { ui.future.push(snapshot()); restore(JSON.parse(ui.history.pop())); return; }
    if (act === 'redo' && ui.future.length) { ui.history.push(snapshot()); restore(JSON.parse(ui.future.pop())); return; }
    return;
  }
  if (ds.cprm) {
    if (!editable()) return;
    remember();
    var gone = nameOf(ds.cprm);
    delete ui.text[ds.cprm]; delete ui.bad[ds.cprm];
    save(rowsNow().filter(function (r) { return r.productId !== ds.cprm; }), gone + ' removed. Undo brings it back.');
    return;
  }
  if (ds.cpsort) {
    var d = ui.dlg;
    d.sort = { k: ds.cpsort, dir: d.sort.k === ds.cpsort ? -d.sort.dir : 1 };
    renderDlg();
    var sb = document.querySelector('[data-cpsort="' + ds.cpsort + '"]'); if (sb) sb.focus();
    return;
  }
  if (ds.cpaddgo !== undefined) { addProducts(ui.dlg.ticked); return; }
  if (ds.cppaste) { applyPaste(ds.cppaste); return; }
  if (ds.cpslgo !== undefined) { insertSleeve(); return; }
  if (ds.cpclearyes !== undefined) {
    remember();
    ui.text = {}; ui.bad = {}; ui.added = [];
    closeDlg();
    save([], 'The portfolio is cleared. Undo brings it back.').then(function () {
      var way = document.querySelector('.cp-way'); if (way) way.focus();
    });
    return;
  }
  if (ds.cpappr) {
    var action = ds.cpappr;
    if (action === 'submit' || action === 'return') { openDlg({ kind: 'note', action: action, note: '' }); return; }
    App.cpApproval(action).then(function (refused) {
      if (refused) { App.announce('assertive', refused); showRefusal(refused); }
      else App.announce('polite', action === 'approve' ? 'Approved. It can be downloaded.' : 'The request is withdrawn.');
    });
    return;
  }
  if (ds.cpnotego !== undefined) {
    var dlg = ui.dlg;
    App.cpApproval(dlg.action, dlg.note).then(function (refused) {
      if (refused) { dlg.error = refused; renderDlg(); return; }
      closeDlg();
      App.announce('polite', dlg.action === 'submit' ? 'Submitted for approval.' : 'Sent back.');
    });
    return;
  }
});

document.addEventListener('input', function (e) {
  var el = e.target;
  if (el.dataset && el.dataset.cpw !== undefined) {
    var pid = el.dataset.cpw;
    /* the undo point is the build before the first keystroke */
    if (ui.snap === null) ui.snap = snapshot();
    ui.text[pid] = el.value;
    var w = cpParse(el.value);
    ui.bad[pid] = typeof w === 'number' && isNaN(w);
    if (!ui.bad[pid]) {
      var row = products().filter(function (p) { return p.productId === pid; })[0];
      if (row) { row.weightPct = w; ui.dirty = true; }
    }
    App.refresh();
    return;
  }
  if (!ui.dlg) return;
  if (el.id === 'cpAddQ') {
    ui.dlg.q = el.value; renderDlg();
    var q = document.getElementById('cpAddQ'); q.focus(); q.setSelectionRange(q.value.length, q.value.length);
    return;
  }
  if (el.id === 'cpPaste') {
    ui.dlg.text = el.value;
    var pos = el.selectionStart; renderDlg();
    var p2 = document.getElementById('cpPaste'); p2.focus(); p2.setSelectionRange(pos, pos);
    return;
  }
  if (el.id === 'cpSlW') {
    ui.dlg.w = el.value; ui.dlg.touched = true;
    var at = el.selectionStart; renderDlg();
    var sw = document.getElementById('cpSlW'); sw.focus(); sw.setSelectionRange(at, at);
    return;
  }
  if (el.id === 'cpNote') {
    ui.dlg.note = el.value;
    var go = document.querySelector('[data-cpnotego]');
    if (go && ui.dlg.action === 'return') go.disabled = !el.value.trim();
  }
});

document.addEventListener('change', function (e) {
  var el = e.target;
  if (el.dataset && el.dataset.cpw !== undefined) {
    var pid = el.dataset.cpw;
    if (ui.bad[pid]) return;
    var row = products().filter(function (p) { return p.productId === pid; })[0];
    if (row && row.weightPct != null) ui.text[pid] = fmtWeight(row.weightPct); else ui.text[pid] = '';
    if (ui.dirty) {
      if (ui.snap !== null && ui.snap !== snapshot()) remember(ui.snap);
      ui.snap = null;
      delete ui.text[pid];
      save(rowsNow());
    }
    return;
  }
  if (el.dataset && el.dataset.cpscale !== undefined) { scaleLine(el.dataset.cpscale, el.value); return; }
  if (el.dataset && el.dataset.cpbench !== undefined) {
    var chosen = el.value;
    App.pushCustomPortfolio({ benchmark: chosen }).then(function (refused) {
      if (refused) showRefusal(refused);
      else App.announce('polite', chosen ? 'Benchmark ' + chosen + '.' : 'No benchmark chosen.');
    });
    return;
  }
  if (el.dataset && el.dataset.cpcat !== undefined) {
    remember();
    var next = rowsNow();
    next.forEach(function (r) { if (r.productId === el.dataset.cpcat) r.category = el.value; });
    save(next, nameOf(el.dataset.cpcat) + ' counted in ' + el.value + '.');
    return;
  }
  if (!ui.dlg) return;
  if (el.dataset && el.dataset.cpfilter) {
    ui.dlg.f[el.dataset.cpfilter] = el.value; renderDlg();
    var fs = document.querySelector('[data-cpfilter="' + el.dataset.cpfilter + '"]'); if (fs) fs.focus();
    return;
  }
  if (el.dataset && el.dataset.cptick) {
    var t = ui.dlg.ticked, at = t.indexOf(el.dataset.cptick);
    if (el.checked && at === -1) t.push(el.dataset.cptick); else if (!el.checked && at !== -1) t.splice(at, 1);
    var keep = el.dataset.cptick; renderDlg();
    var tb = document.querySelector('[data-cptick="' + keep + '"]'); if (tb) tb.focus();
    return;
  }
  if (el.name === 'cpSleeve') {
    ui.dlg.ix = Number(el.value);
    if (!ui.dlg.touched) ui.dlg.w = sleeveDefault(lib().library[ui.dlg.ix]);
    renderDlg();
    var r2 = document.querySelector('input[name="cpSleeve"][value="' + ui.dlg.ix + '"]'); if (r2) r2.focus();
  }
});

document.addEventListener('focusin', function (e) {
  if (e.target.dataset && e.target.dataset.cpw !== undefined && ui.snap === null) ui.snap = snapshot();
});

document.addEventListener('keydown', function (e) {
  if (ui.dlg) {
    if (e.key === 'Escape') { e.preventDefault(); closeDlg(); return; }
    if (e.target.id === 'cpSlW' && e.key === 'Enter') { e.preventDefault(); insertSleeve(); return; }
    return;
  }
  var el = e.target;
  if (el.dataset && el.dataset.cpscale !== undefined && e.key === 'Enter') { e.preventDefault(); el.blur(); return; }
  if (el.dataset && el.dataset.cpw !== undefined && (e.key === 'Enter' || e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
    e.preventDefault();
    var all = Array.prototype.slice.call(document.querySelectorAll('input.cp-w[data-cpw]'));
    var next = all[all.indexOf(el) + (e.key === 'ArrowUp' ? -1 : 1)];
    el.blur();
    if (next) { var id = next.id; window.setTimeout(function () { var n = document.getElementById(id); if (n) { n.focus(); n.select(); } }, 0); }
    return;
  }
  var inField = el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT';
  if (!inField && App.cpActive() && App.step() === 'impl' && editable() && (e.metaKey || e.ctrlKey)
      && (e.key === 'z' || e.key === 'Z')) {
    var btn = document.querySelector('[data-cpact="' + (e.shiftKey ? 'redo' : 'undo') + '"]');
    if (btn && !btn.disabled) { e.preventDefault(); btn.click(); }
  }
});

/* ---- every render: step 1's heading, and what a custom proposal hides there - */
function renderStage() {
  var on = App.cpActive() && App.phase() === 'workspace';
  document.body.classList.toggle('cp-on', on);
  var tab = document.querySelector('#tab-impl .step-label small');
  if (tab) tab.textContent = on ? 'Products & export' : 'Sleeves & export';
  var title = document.querySelector('#view-aa .sec .stage-title');
  if (title) {
    if (title.dataset.strategic === undefined) title.dataset.strategic = title.innerHTML;
    title.innerHTML = on ? 'Custom Allocation <span class="stage-sub">Built product by product · '
      + esc(profileLabel()) + ' risk profile</span>' : title.dataset.strategic;
    var head = title.closest('.sec-head');
    if (on && head) head.hidden = false;
  }
  if (on) {
    var risk = document.getElementById('risk');
    var section = risk && risk.closest('.sec');
    if (section) section.hidden = true;
    App.ensureCpLib();
    App.syncAnchorBase();
  } else {
    ui.history = []; ui.future = []; ui.text = {}; ui.bad = {};
  }
}

App.addRenderer(renderStage);
Object.assign(App, {
  cpLines: cpLines, cpSleeveFor: cpSleeveFor, cpIsEmpty: cpIsEmpty, cpRailHtml: cpRailHtml,
  cpSummaryHtml: cpSummaryHtml, cpBlankHtml: cpBlankHtml, cpToolbarHtml: cpToolbarHtml,
  cpBandHtml: cpBandHtml, cpNameExtras: cpNameExtras, cpWeightCell: cpWeightCell,
  cpTotalNote: cpTotalNote, cpGate: cpGate, cpAllocHtml: cpAllocHtml
});
})();
