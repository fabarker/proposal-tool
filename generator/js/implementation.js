(function () {
'use strict';
/* =============================================================================
   Implementation layer (spec 8): the sleeve tier in the rail, the
   thirteen-column product table, and the Excel download.

   Categories come from the resolved base portfolio - never a list in this
   file (spec 2.2). Asset Allocation Strategies (or whatever the schema's
   autoSleeveCategories names) renders attached and locked, counts toward the
   gate, and can never fail it (spec 2.6).

   Every printed figure derives from the printed weight (spec 8.4):
   roundWeights below is the JavaScript mirror of the Python
   roundWeightsLargestRemainder - floor to 0.01% units, hand the shortfall to
   the largest remainders, ties broken by original row order. The two must
   agree exactly or the workbook and the screen drift.
   ========================================================================== */
var ROUND_TO = 100;

function roundWeights(exact) {
  var units = exact.map(function (w) { return Math.floor(w * 100 + 1e-9); });
  var used = units.reduce(function (a, b) { return a + b; }, 0);
  var short = 10000 - used;
  var order = exact.map(function (w, i) { return { i: i, rem: w * 100 - units[i] }; })
    .sort(function (a, b) { return (b.rem - a.rem) || (a.i - b.i); });
  for (var n = 0; n < short && order.length; n++) units[order[n % order.length].i] += 1;
  return units.map(function (u) { return u / 100; });
}

function money(v) {
  return (v === null || v === undefined || isNaN(v))
    ? '—' : '$' + Math.round(v).toLocaleString('en-US');
}

function pillFor(v) {
  var map = { Active: 'p-act', Passive: 'p-pas', SMA: 'p-sma', ETF: 'p-etf',
              'Mutual Fund': 'p-mf', Internal: 'p-int', External: 'p-ext' };
  return '<span class="pill ' + (map[v] || 'p-pas') + '">' + App.esc(v) + '</span>';
}

function isAuto(category) { return App.autoSleeveCategories().indexOf(category) >= 0; }

function baseCategories() {
  var base = App.base();
  if (!base || base.status !== 'ready') return [];
  return base.data.categories;
}

/* The chosen (or auto-attached) sleeve object for a category, or null. */
function sleeveFor(category) {
  var lib = App.sleeveLib()[category];
  if (!lib || lib.status !== 'ready') return null;
  if (isAuto(category)) return lib.sleeves[0] || null;
  var name = App.sleeves()[category];
  if (!name) return null;
  for (var i = 0; i < lib.sleeves.length; i++) {
    if (lib.sleeves[i].name === name) return lib.sleeves[i];
  }
  return null;
}

function ensureLibraries() {
  if (App.step() !== 'impl') return;
  baseCategories().forEach(function (category) {
    App.ensureSleeveLib(category.name);
  });
}

function filledCount() {
  var live = baseCategories();
  var filled = 0;
  live.forEach(function (category) { if (sleeveFor(category.name)) filled += 1; });
  return { filled: filled, total: live.length };
}

function complete() {
  var counts = filledCount();
  return counts.total > 0 && counts.filled === counts.total;
}

/* ---- rows: every printed figure from the printed weight (spec 8.4) ------ */
function rows() {
  var groups = [];
  var all = [];
  baseCategories().forEach(function (category) {
    var sleeve = sleeveFor(category.name);
    var items = [];
    if (sleeve) {
      sleeve.products.forEach(function (product) {
        var item = {
          name: product.name, ticker: product.ticker, assetClass: product.assetClass,
          style: product.style, vehicle: product.vehicle, source: product.source,
          liquidity: product.liquidity, exposureCurrency: product.exposureCurrency,
          cost: product.productCost, mgmt: product.managementFee,
          exact: category.weightPct * product.weight
        };
        items.push(item);
        all.push(item);
      });
    }
    groups.push({
      category: category.name,
      weightPct: category.weightPct,
      auto: isAuto(category.name),
      sleeve: sleeve ? sleeve.name : null,
      items: items
    });
  });
  if (all.length) {
    var printed = complete()
      ? roundWeights(all.map(function (i) { return i.exact; }))
      : all.map(function (i) { return Math.round(i.exact * 100) / 100; });
    all.forEach(function (item, ix) {
      item.weight = printed[ix];
      item.wtdBp = (item.cost + item.mgmt) * item.weight;
      item.notional = Math.round(App.mandateSize() * item.weight / 100 / ROUND_TO) * ROUND_TO;
    });
  }
  return groups;
}

function totals(groups) {
  var weight = 0, bp = 0, notional = 0;
  groups.forEach(function (group) {
    group.items.forEach(function (item) {
      weight += item.weight; bp += item.wtdBp; notional += item.notional;
    });
  });
  return { weight: weight, bp: bp, notional: notional };
}

/* ---- the rail tier (spec 9.4) ------------------------------------------- */
function renderRail() {
  var el = document.getElementById('tier-sleeves'); if (!el) return;
  if (App.step() !== 'impl' || App.phase() !== 'workspace') { el.hidden = true; return; }
  el.hidden = false;
  var base = App.base();
  if (!base) {
    el.innerHTML = '<div class="tier-h"><h3>Sleeves</h3></div>'
      + '<p class="field-note">Build a base portfolio first.</p>';
    return;
  }
  if (base.status !== 'ready') {
    el.innerHTML = '<div class="tier-h"><h3>Sleeves</h3></div>'
      + '<p class="field-note">' + (base.status === 'error'
          ? 'The base portfolio could not be built. Retry it from the allocation step.'
          : 'Resolving the base portfolio…') + '</p>';
    return;
  }
  var counts = filledCount();
  var html = '<div class="tier-h"><h3>Sleeves</h3>'
    + '<span class="tier-count">' + counts.filled + ' of ' + counts.total + '</span></div>'
    + '<div class="sl-list">';
  baseCategories().forEach(function (category, i) {
    var lib = App.sleeveLib()[category.name];
    var chosen = sleeveFor(category.name);
    if (isAuto(category.name)) {
      html += '<div class="sl-row done auto"><span class="cat"><b>' + App.esc(category.name)
        + '</b><span>' + App.num(category.weightPct, 1, '%') + '</span></span>'
        + '<p class="sl-auto" id="slauto' + i + '">'
        + (chosen ? App.esc(chosen.name) + ' 🔒' : 'Loading sleeve…')
        + '</p></div>';
      return;
    }
    var select;
    if (!lib || lib.status === 'loading') {
      select = '<select id="sl' + i + '" disabled><option>Loading sleeves…</option></select>';
    } else if (lib.status === 'error') {
      select = '<select id="sl' + i + '" disabled><option>Could not load sleeves</option></select>'
        + '<button type="button" class="sl-retry" data-slretry="'
        + App.esc(category.name) + '">Retry</button>';
    } else {
      var options = lib.sleeves.map(function (s) {
        return '<option value="' + App.esc(s.name) + '"'
          + (App.sleeves()[category.name] === s.name ? ' selected' : '') + '>'
          + App.esc(s.name) + '</option>';
      }).join('');
      select = '<select id="sl' + i + '" data-cat="' + App.esc(category.name) + '"'
        + (App.canEdit() ? '' : ' disabled') + '>'
        + '<option value="">Select a sleeve…</option>' + options + '</select>';
    }
    html += '<div class="sl-row' + (chosen ? ' done' : '') + '">'
      + '<span class="cat"><b><label for="sl' + i + '">' + App.esc(category.name) + '</label></b>'
      + '<span>' + App.num(category.weightPct, 1, '%') + '</span></span>' + select + '</div>';
  });
  var progressPct = counts.total ? Math.round(counts.filled / counts.total * 100) : 0;
  html += '</div><p class="sl-progress">' + counts.filled + ' of ' + counts.total
    + ' categories have a sleeve.'
    + '<span class="sl-bar"><i style="width:' + progressPct + '%"></i></span></p>';
  var hasAuto = baseCategories().some(function (c) { return isAuto(c.name); });
  if (hasAuto) {
    html += '<p class="field-note">' + App.esc(App.autoSleeveCategories()[0])
      + ' is attached automatically while tactical tilts are included.</p>';
  }
  el.innerHTML = html;
}

/* ---- the document (spec 9.3) -------------------------------------------- */
function renderView() {
  var el = document.getElementById('view-impl'); if (!el) return;
  var wrap = document.getElementById('view-impl-wrap');
  var nav = document.getElementById('steps');
  if (nav) {
    nav.querySelectorAll('.step').forEach(function (b) {
      b.setAttribute('aria-selected', String(b.dataset.step === App.step()));
    });
  }
  var aa = document.getElementById('view-aa');
  var onLanding = App.phase() !== 'workspace';
  if (aa) aa.hidden = onLanding || App.step() !== 'aa';
  if (wrap) wrap.hidden = onLanding || App.step() !== 'impl';
  el.hidden = onLanding || App.step() !== 'impl';
  if (onLanding || App.step() !== 'impl') return;

  var base = App.base();
  if (!base) {
    el.innerHTML = '<div class="impl-empty"><h3>No base portfolio yet</h3>'
      + '<p>Choose an allocation and risk level in the rail to build the base portfolio, '
      + 'then attach a sleeve to each of its categories.</p></div>';
    return;
  }
  if (base.status === 'loading') {
    el.innerHTML = '<div class="impl-empty"><h3>Resolving the base portfolio…</h3>'
      + '<p>The implementation model builds from the base portfolio’s categories.</p></div>';
    return;
  }
  if (base.status === 'error') {
    el.innerHTML = '<div class="impl-empty"><h3>The base portfolio could not be built</h3>'
      + '<p>Retry it from the allocation step; the implementation model needs its categories.</p></div>';
    return;
  }

  var groups = rows();
  var t = totals(groups);
  var counts = filledCount();
  var done = complete();
  var columnsBusy = App.columns().some(function (c) { return c.status !== 'ready'; });
  var exporting = App.exporting();

  var html = '<div class="impl-head"><div>'
    + '<p class="sec-note" style="margin:0 0 4px">Implementing <strong>'
    + App.esc(base.data.name) + '</strong> against a mandate of '
    + money(App.mandateSize()) + '.</p>'
    + '<p class="sec-note" style="margin:0">'
    + (done ? '<span class="bdg b-ok">All ' + counts.total + ' categories implemented</span>'
            : '<span class="bdg b-warn">' + (counts.total - counts.filled) + ' categor'
              + ((counts.total - counts.filled) === 1 ? 'y' : 'ies')
              + ' still need' + ((counts.total - counts.filled) === 1 ? 's' : '')
              + ' a sleeve</span>')
    + '</p></div></div>';

  html += '<div class="tblwrap" tabindex="0" aria-label="Implementation model, scrolls horizontally">'
    + '<table class="tbl impl">'
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

  groups.forEach(function (group) {
    var groupBp = 0, groupNotional = 0, groupWeight = 0;
    group.items.forEach(function (item) {
      groupBp += item.wtdBp; groupNotional += item.notional; groupWeight += item.weight;
    });
    var shownWeight = group.items.length ? groupWeight : group.weightPct;
    var shownNotional = group.items.length ? groupNotional
      : Math.round(App.mandateSize() * group.weightPct / 100 / ROUND_TO) * ROUND_TO;
    html += '<tr class="cat"><th scope="row">' + App.esc(group.category) + '</th>'
      + '<td class="txt prodcol">' + (group.sleeve
          ? '<span class="pill p-sleeve">' + App.esc(group.sleeve)
            + (group.auto ? ' 🔒' : '') + '</span>'
          : '<span class="bdg b-warn">No sleeve attached</span>') + '</td>'
      + '<td class="num">' + App.num(shownWeight, 2, '%') + '</td>'
      + '<td colspan="6"></td>'
      + '<td class="num"></td><td class="num"></td>'
      + '<td class="num">' + (group.items.length ? App.num(groupBp, 1, 'bp') : '') + '</td>'
      + '<td class="num">' + money(shownNotional) + '</td></tr>';
    group.items.forEach(function (item, ix) {
      html += '<tr class="asset' + (ix % 2 ? ' alt' : '') + '"><th scope="row">'
        + App.esc(item.assetClass) + '</th>'
        + '<td class="txt prodcol">' + App.esc(item.name) + '</td>'
        + '<td class="num">' + App.num(item.weight, 2, '%') + '</td>'
        + '<td class="txt tick">' + App.esc(item.ticker) + '</td>'
        + '<td class="txt">' + pillFor(item.style) + '</td>'
        + '<td class="txt">' + pillFor(item.vehicle) + '</td>'
        + '<td class="txt">' + pillFor(item.source) + '</td>'
        + '<td class="txt">' + App.esc(item.liquidity) + '</td>'
        + '<td class="txt tick">' + App.esc(item.exposureCurrency) + '</td>'
        + '<td class="num">' + App.num(item.cost, 2, '%') + '</td>'
        + '<td class="num">' + App.num(item.mgmt, 2, '%') + '</td>'
        + '<td class="num">' + App.num(item.wtdBp, 1, 'bp') + '</td>'
        + '<td class="num">' + money(item.notional) + '</td></tr>';
    });
  });
  html += '<tr class="grand"><th scope="row">Total</th>'
    + '<td class="prodcol"></td>'
    + '<td class="num">' + App.num(t.weight, 2, '%') + '</td>'
    + '<td colspan="8"></td>'
    + '<td class="num">' + App.num(t.bp, 1, 'bp') + '</td>'
    + '<td class="num">' + money(t.notional) + '</td></tr>';
  html += '</tbody></table></div>';

  var reason = null;
  if (!App.canExport()) reason = 'Export is not available for your role.';
  else if (!done) reason = 'Attach a sleeve to every category to enable the download.';
  else if (columnsBusy) reason = 'Wait for every portfolio column to finish resolving.';
  var disabled = !!reason || exporting.status === 'working';
  html += '<div class="impl-foot">'
    + '<button type="button" class="btn btn-primary" id="implexport"'
    + (disabled ? ' disabled' : '')
    + (reason ? ' aria-describedby="implgate" title="' + App.esc(reason) + '"' : '') + '>'
    + (exporting.status === 'working' ? 'Preparing…' : 'Download .xlsx') + '</button>'
    + (reason ? '<p class="impl-gate" id="implgate">' + App.esc(reason) + '</p>' : '')
    + (exporting.status === 'error'
        ? '<span class="bdg b-breach">' + App.esc(exporting.error || 'Export failed') + '</span>'
        : '')
    + '</div>'
    + '<p class="impl-note">Weighted fee is (product cost + management fee) × weight, shown in '
    + 'basis points to one decimal. Weights are rounded to two decimals by largest remainder so '
    + 'the column sums to exactly 100.00%, and notional is derived from the printed weight '
    + 'against the mandate size — so weight × mandate reconciles to the notional shown.</p>';
  el.innerHTML = html;
  publishPinnedColumnWidth(el);
}

/* Column 2 is pinned immediately to the right of column 1, and column 1 now
   sizes to its longest label, so that offset can no longer be a constant.
   Measure what column 1 actually rendered at and publish it as --impl-c1 for
   the stylesheet. One forced layout per render, on a table just rebuilt. */
function publishPinnedColumnWidth(root) {
  var head = root.querySelector('.tbl.impl thead .rowhead');
  if (!head) return;
  var width = Math.round(head.getBoundingClientRect().width);
  if (width > 0) head.closest('table').style.setProperty('--impl-c1', width + 'px');
}

/* ---- export (spec 14) --------------------------------------------------- */
async function exportWorkbook() {
  var exporting = App.exporting();
  if (exporting.status === 'working') return;
  exporting.status = 'working';
  exporting.error = null;
  App.refresh();
  try {
    var resp = await fetch(window.API_BASE + '/scenario/'
        + encodeURIComponent(App.scenarioId()) + '/export',
      { method: 'POST', credentials: 'same-origin' });
    if (!resp.ok) {
      var message = 'Export failed (' + resp.status + ')';
      try {
        var body = await resp.json();
        if (body && body.loginUrl) { window.location = body.loginUrl; return; }
        if (body && body.error) message = body.error;
      } catch (e) { /* non-JSON body */ }
      throw new Error(message);
    }
    var blob = await resp.blob();
    var name = 'EpsilonPhi_Scenario.xlsx';
    var disposition = resp.headers.get('Content-Disposition') || '';
    var match = disposition.match(/filename="?([^";]+)"?/);
    if (match) name = match[1];
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
    exporting.status = 'idle';
    App.announce('polite', 'Workbook downloaded.');
  } catch (err) {
    exporting.status = 'error';
    exporting.error = (err && err.message) || 'Export failed.';
  }
  App.refresh();
}

/* ---- events ------------------------------------------------------------- */
document.addEventListener('change', function (e) {
  if (e.target.dataset && e.target.dataset.cat !== undefined) {
    App.chooseSleeve(e.target.dataset.cat, e.target.value || null);
  }
});
document.addEventListener('click', function (e) {
  var step = e.target.closest ? e.target.closest('.step') : null;
  if (step) { App.setStep(step.dataset.step); return; }
  if (e.target.id === 'implexport') { exportWorkbook(); return; }
  var retry = e.target.closest ? e.target.closest('.sl-retry') : null;
  if (retry) {
    var category = retry.dataset.slretry;
    delete App.sleeveLib()[category];
    App.ensureSleeveLib(category);
    return;
  }
});

/* ensureLibraries runs first so a library kicked off this cycle already
   shows its loading state when the rail paints */
App.addRenderer(function () { ensureLibraries(); renderRail(); renderView(); });
})();
