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
              'Mutual Fund': 'p-mf', UCITS: 'p-ucits', ICAV: 'p-icav',
              Internal: 'p-int', External: 'p-ext' };
  return '<span class="pill ' + (map[v] || 'p-pas') + '">' + App.esc(v) + '</span>';
}

function isAuto(category) { return App.autoSleeveCategories().indexOf(category) >= 0; }

function baseCategories() {
  var base = App.base();
  if (!base || base.status !== 'ready') return [];
  return base.data.categories;
}

/* The chosen (or auto-attached) sleeve object for a category, or null.
   Nothing resolves before a variant is chosen: the auto-attached sleeve is
   variant-specific too, so it cannot be attached earlier either (D29). */
function sleeveFor(category) {
  if (!App.variant()) return null;
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
  if (App.step() !== 'impl' || !App.variant()) return;
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

/* ---- the variant control (spec 8.1, D29) --------------------------------
   The names are schema data, never a list in this file - the same rule the
   categories follow (spec 2.2, 4.3). Rendered as the first field of the
   sleeve tier because it is the first decision of step 2 and every other one
   depends on it. */
function variantField() {
  var variants = App.implementationVariants();
  var chosen = App.variant();
  if (!variants.length) {
    return '<p class="field-note">The implementation variants could not be '
      + 'loaded, so no sleeve can be attached.</p>';
  }
  var options = variants.map(function (name) {
    return '<option value="' + App.esc(name) + '"'
      + (chosen === name ? ' selected' : '') + '>' + App.esc(name) + '</option>';
  }).join('');
  return '<div class="vr-field' + (chosen ? ' done' : '') + '">'
    + '<label for="implvariant">Implementation variant</label>'
    + '<select id="implvariant" data-variant="1"'
    + (App.canEdit() ? '' : ' disabled') + '>'
    + '<option value="">Select a variant…</option>' + options + '</select>'
    + (chosen
        ? '<p class="vr-note">Sleeves below are those ' + App.esc(chosen)
          + ' can hold.</p>'
        : '') + '</div>';
}

/* ---- the rail tier (spec 9.4) ------------------------------------------- */
function renderRail() {
  var el = document.getElementById('tier-sleeves'); if (!el) return;
  if (App.step() !== 'impl' || App.phase() !== 'workspace') { el.hidden = true; return; }
  el.hidden = false;
  var base = App.base();
  var counts = filledCount();
  var chosenVariant = App.variant();

  /* The variant does not depend on the base portfolio - it is a property of
     the book being implemented, not of the allocation - so its control is
     rendered before the base-status returns and can be answered while the
     base is still resolving. */
  var head = '<div class="tier-h"><h3>Sleeves</h3>'
    + (chosenVariant && base && base.status === 'ready'
        ? '<span class="tier-count">' + counts.filled + ' of ' + counts.total + '</span>'
        : '') + '</div>' + variantField();

  if (!base) {
    el.innerHTML = head + '<p class="field-note">Build a base portfolio first.</p>';
    return;
  }
  if (base.status !== 'ready') {
    el.innerHTML = head + '<p class="field-note">' + (base.status === 'error'
        ? 'The base portfolio could not be built. Retry it from the allocation step.'
        : 'Resolving the base portfolio…') + '</p>';
    return;
  }
  var html = head;

  /* The gate: no variant, no sleeves. Rendering the pickers disabled would
     invite clicking them; there is nothing behind them to pick yet. */
  if (!chosenVariant) {
    el.innerHTML = html + '<p class="field-note">Choose an implementation variant '
      + 'to see the sleeves available to it. It decides which sleeves each '
      + 'category offers and what they hold.</p>';
    return;
  }

  html += '<div class="sl-list">';
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


/* ---- composition doughnuts (spec 8.5) -----------------------------------
   Five small multiples under the table: how the implemented money splits
   across each product attribute. The weight summed is the PRINTED weight, the
   same figure the table shows, so a reader can add the rows up and arrive at
   the arc (spec 8.4).

   Colour is taken from the categorical palette and keyed to the value's NAME,
   not to the slice's position - the rule spec 6.6 sets for the category
   charts, and for the same reason: slices are ordered by weight, so a colour
   tied to position would have Passive change hue the moment it overtook
   Active. Names are ranked alphabetically to pick the slot, which is stable
   for a given set of values and independent of what happens to be attached.
   With more distinct values than slots the palette repeats, which the legend
   disambiguates. */
var DONUT_DIMENSIONS = [
  { key: 'style',            label: 'Style' },
  { key: 'vehicle',          label: 'Vehicle' },
  { key: 'source',           label: 'Source' },
  { key: 'liquidity',        label: 'Liquidity' },
  { key: 'exposureCurrency', label: 'Exposure currency' }
];
var DONUT_SLOTS = 7;

/* The legend prints one decimal, and rounding each share independently lets
   the column sum to 100.1. Same defect spec 8.4 exists to prevent, so the same
   remedy: largest remainder, at 0.1 rather than 0.01.

   Deliberately a separate function rather than a parameter on roundWeights.
   That one is called as cases.map(roundWeights) by the Python/JS mirror test,
   and Array.map passes the index as the second argument - a `perPercent`
   parameter would silently receive 0, 1, 2 and rescale every case. */
function roundSharesOneDp(exact) {
  var units = exact.map(function (w) { return Math.floor(w * 10 + 1e-9); });
  var used = units.reduce(function (a, b) { return a + b; }, 0);
  var short = 1000 - used;
  var order = exact.map(function (w, i) { return { i: i, rem: w * 10 - units[i] }; })
    .sort(function (a, b) { return (b.rem - a.rem) || (a.i - b.i); });
  for (var n = 0; n < short && order.length; n++) units[order[n % order.length].i] += 1;
  return units.map(function (u) { return u / 10; });
}

function breakdown(items, key) {
  var byValue = {};
  var total = 0;
  items.forEach(function (item) {
    var value = item[key];
    if (value === null || value === undefined || value === '') value = '—';
    byValue[value] = (byValue[value] || 0) + item.weight;
    total += item.weight;
  });
  var names = Object.keys(byValue).sort();          /* the colour ranking */
  var slotOf = {};
  names.forEach(function (name, i) { slotOf[name] = (i % DONUT_SLOTS) + 1; });
  var slices = names.map(function (name) {
    return { name: name, weight: byValue[name], slot: slotOf[name],
             share: total > 0 ? byValue[name] / total : 0 };
  });
  slices.sort(function (a, b) {                     /* biggest first to read */
    return (b.weight - a.weight) || (a.name < b.name ? -1 : 1);
  });
  /* printed shares close on 100.0 exactly; the arcs keep the exact fractions */
  var printed = total > 0
    ? roundSharesOneDp(slices.map(function (slice) { return slice.share * 100; }))
    : slices.map(function () { return 0; });
  slices.forEach(function (slice, i) { slice.pct = printed[i]; });
  return { slices: slices, total: total };
}

/* Arcs as a dashed circle rather than path arithmetic: one circumference, one
   dasharray per slice, offset by what came before. Rotated so the first slice
   starts at twelve o'clock. */
function donutSvg(slices, label) {
  var SIZE = 132, R = 52, STROKE = 22;
  var C = 2 * Math.PI * R;
  var offset = 0;
  var arcs = '';
  slices.forEach(function (slice) {
    var length = slice.share * C;
    if (length <= 0) return;
    arcs += '<circle class="dn-arc" cx="' + (SIZE / 2) + '" cy="' + (SIZE / 2)
      + '" r="' + R + '" fill="none" stroke="var(--cat-' + slice.slot + ')"'
      + ' stroke-width="' + STROKE + '"'
      + ' stroke-dasharray="' + length.toFixed(3) + ' ' + (C - length).toFixed(3) + '"'
      + ' stroke-dashoffset="' + (-offset).toFixed(3) + '"><title>'
      + App.esc(slice.name) + ' ' + App.num(slice.pct, 1, '%')
      + '</title></circle>';
    offset += length;
  });
  if (!arcs) {
    arcs = '<circle cx="' + (SIZE / 2) + '" cy="' + (SIZE / 2) + '" r="' + R
      + '" fill="none" stroke="var(--line-strong)" stroke-width="' + STROKE + '"/>';
  }
  return '<svg class="dn" viewBox="0 0 ' + SIZE + ' ' + SIZE + '" role="img" aria-label="'
    + App.esc(label) + '">' + arcs + '</svg>';
}

function renderDonuts(groups, done) {
  var items = [];
  groups.forEach(function (group) {
    group.items.forEach(function (item) { items.push(item); });
  });
  if (!items.length) return '';

  var attached = items.reduce(function (sum, item) { return sum + item.weight; }, 0);
  var cards = DONUT_DIMENSIONS.map(function (dimension) {
    var data = breakdown(items, dimension.key);
    var reading = data.slices.map(function (slice) {
      return slice.name + ' ' + App.num(slice.pct, 1, '%');
    }).join(', ');
    var legend = data.slices.map(function (slice) {
      return '<li><span class="dn-sw" style="background:var(--cat-' + slice.slot + ')"></span>'
        + '<span class="dn-nm">' + App.esc(slice.name) + '</span>'
        + '<span class="dn-pc">' + App.num(slice.pct, 1, '%') + '</span></li>';
    }).join('');
    return '<div class="dn-card"><h4>' + App.esc(dimension.label) + '</h4>'
      + donutSvg(data.slices, dimension.label + ': ' + reading)
      + '<ul class="dn-key">' + legend + '</ul></div>';
  }).join('');

  /* Shares are of what is attached, not of the mandate, so the ring always
     closes. While categories are still unfilled that is a different number
     from 100% of the portfolio, and the note says so rather than letting the
     chart imply the model is finished. */
  return '<div class="impl-viz"><div class="impl-viz-head">'
    + '<h3>Composition of the implemented model</h3>'
    + '<p class="sec-note">Share of allocation by product attribute'
    + (done ? '.' : ', across the ' + App.num(attached, 2, '%')
        + ' attached so far &mdash; not of the whole portfolio.')
    + '</p></div><div class="dn-row">' + cards + '</div></div>';
}

/* ---- the document (spec 9.3) -------------------------------------------- */
function renderView() {
  var el = document.getElementById('view-impl'); if (!el) return;
  var wrap = document.getElementById('view-impl-wrap');
  var nav = document.getElementById('steps');
  if (nav) {
    /* Step 2 is unreachable until there is something to implement, and once
       there is, it asks for attention until it has been opened. A tab that
       looks available but does nothing is worse than one that plainly is
       not. */
    var base = App.base();
    var ready = !!(base && base.status === 'ready');
    nav.querySelectorAll('.step').forEach(function (b) {
      b.setAttribute('aria-selected', String(b.dataset.step === App.step()));
      if (b.dataset.step !== 'impl') return;
      b.disabled = !ready;
      b.classList.toggle('step-beckon', ready && !App.implSeen());
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
  if (!App.variant()) {
    el.innerHTML = '<div class="impl-empty"><h3>Choose an implementation variant</h3>'
      + '<p>The variant decides which sleeves each category of <strong>'
      + App.esc(base.data.name) + '</strong> can hold, and what those sleeves '
      + 'contain. Nothing can be attached until it is set. Choose one at the '
      + 'top of the rail.</p></div>';
    return;
  }

  var groups = rows();
  var t = totals(groups);
  var counts = filledCount();
  var done = complete();
  var columnsBusy = App.columns().some(function (c) { return c.status !== 'ready'; });
  var exporting = App.exporting();

  /* The same stage block step 1 carries: eyebrow, title, standfirst. The
     "Implementing X against a mandate of Y" line becomes the standfirst
     rather than sitting as a second note, so the two steps open identically. */
  var html = '<div class="impl-head"><div>'
    + '<p class="eyebrow">Step 2 of 2</p>'
    + '<h2 class="stage-title">Portfolio Implementation</h2>'
    + '<p class="stage-sub">Implementing <strong>'
    + App.esc(base.data.name) + '</strong> against a mandate of '
    + money(App.mandateSize()) + '.</p>'
    + '</div>'
    /* v2's completion summary in place of the pill: it reported only two
       states, where this shows how far along the model is at a glance. */
    + '<div class="completion-summary">'
    + '<div class="completion-line"><span>Implementation progress</span>'
    + '<strong>' + counts.filled + ' of ' + counts.total + '</strong></div>'
    + '<div class="progress-track' + (done ? ' is-done' : '') + '" role="img" aria-label="'
    + counts.filled + ' of ' + counts.total + ' categories implemented"><i style="--progress:'
    + (counts.total ? Math.round(counts.filled / counts.total * 100) : 0) + '%"></i></div>'
    + '</div></div>';

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
            /* The auto category keeps its padlock and gets no remove control:
               its sleeve is attached by rule, not by choice (spec 2.6). */
            + (group.auto ? ' 🔒'
                : (App.canEdit()
                    ? '<button type="button" class="pill-x" data-rmsleeve="'
                      + App.esc(group.category) + '" aria-label="Remove the '
                      + App.esc(group.sleeve) + ' sleeve from '
                      + App.esc(group.category) + '">&#215;</button>'
                    : ''))
            + '</span>'
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
  html += renderDonuts(groups, done);

  var reason = null;
  if (!App.canExport()) reason = 'Export is not available for your role.';
  else if (!App.variant()) reason = 'Choose an implementation variant first.';
  else if (!done) reason = 'Attach a sleeve to every category to enable the download.';
  else if (columnsBusy) reason = 'Wait for every portfolio column to finish resolving.';
  var disabled = !!reason || exporting.status === 'working';
  /* v2's export card in place of the bare button. The gate keeps its three
     voices - working, blocked, failed - and stays wired to the button through
     aria-describedby, so the reason still reaches assistive tech (spec 8.2). */
  var gateClass = 'export-gate';
  var gateText = '';
  if (exporting.status === 'working') {
    gateText = 'The server is generating the workbook from the persisted scenario.';
  } else if (exporting.status === 'error') {
    gateClass += ' error';
    gateText = exporting.error || 'Export failed.';
  } else if (reason) {
    gateClass += ' blocked';
    gateText = reason;
  } else {
    gateClass += ' ready';
    gateText = 'Ready — every category is implemented and the scenario is saved.';
  }
  html += '<section class="export-card" aria-labelledby="exporttitle">'
    + '<div class="export-icon" aria-hidden="true"><span>X</span></div>'
    + '<div class="export-copy">'
    + '<p class="eyebrow">Final deliverable</p>'
    + '<h3 id="exporttitle">Download the proposal workbook</h3>'
    + '<p>Generates Portfolios, Risk Dashboard and Implementation sheets from the '
    + 'persisted scenario and the current epsilonPhi analytics.</p>'
    + '<p class="' + gateClass + '" id="implgate">' + App.esc(gateText) + '</p>'
    + '</div>'
    + '<button type="button" class="btn btn-export" id="implexport"'
    + (disabled ? ' disabled' : '') + ' aria-describedby="implgate"'
    + (reason ? ' title="' + App.esc(reason) + '"' : '') + '>'
    + (exporting.status === 'working' ? 'Preparing…' : 'Download Excel') + '</button>'
    + '</section>';
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
  if (!e.target.dataset) return;
  if (e.target.dataset.variant !== undefined) {
    App.setVariant(e.target.value || null);
    return;
  }
  if (e.target.dataset.cat !== undefined) {
    App.chooseSleeve(e.target.dataset.cat, e.target.value || null);
  }
});
document.addEventListener('click', function (e) {
  var step = e.target.closest ? e.target.closest('.step') : null;
  if (step) { App.setStep(step.dataset.step); return; }
  if (e.target.id === 'implexport') { exportWorkbook(); return; }
  var rm = e.target.closest ? e.target.closest('[data-rmsleeve]') : null;
  if (rm) { App.chooseSleeve(rm.dataset.rmsleeve, null); return; }
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
