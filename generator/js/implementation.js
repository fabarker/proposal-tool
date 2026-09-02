(function () {
'use strict';
/* =============================================================================
   Implementation layer (spec 8): the sleeve tier in the rail, the product
   table (the thirteen columns of spec 9.3 plus the fee group, D51), and the
   Excel download.

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

/* ---- the management fee (D51) -------------------------------------------
   Not a property of the product. It is resolved from the fee schedule, the
   fee level and - under a schedule that prices by group - the product's fee
   group, against the rates the schema serves for the mandate's account-size
   tier. The tier is resolved server-side; the client only ever holds one
   tier's rates, so this is a lookup and nothing more.

   THE PYTHON MIRROR is fees.managementFee(), read through fees.ratesAtTier();
   the test suite checks the two agree across every schedule, level and group.
   Anything unknown resolves to null - rendered as a dash and blocking the
   export - never to a guessed rate. */
function resolveFee(rates, schedule, level, feeGroup) {
  if (!rates || !schedule || !level) return null;
  var table = rates[schedule];
  if (!table) return null;
  var levels = table.byGroup ? (table.groups || {})[feeGroup] : table.levels;
  if (!levels) return null;
  var rate = levels[level];
  return (typeof rate === 'number') ? rate : null;
}

function feeRates() { return App.opt('fees.rates', null); }
function feeSchedules() { return App.opt('fees.schedules', []); }

function feeScheduleEntry() {
  var chosen = App.feeSchedule();
  return feeSchedules().filter(function (s) { return s.id === chosen; })[0] || null;
}

/* Whether the chosen schedule reads the fee group at all. */
function feeByGroup() {
  var entry = feeScheduleEntry();
  return !!(entry && entry.byGroup);
}

function managementFee(feeGroup) {
  return resolveFee(feeRates(), App.feeSchedule(), App.feeLevel(), feeGroup);
}

function groupPill(name) {
  return '<span class="pill p-grp">' + App.esc(name || '—') + '</span>';
}

function feeText(pct) { return pct === null ? '—' : App.num(pct, 2, '%'); }
function bpText(bp) { return bp === null ? '—' : App.num(bp, 1, 'bp'); }

/* ---- the tactical tilt (D50) --------------------------------------------
   Tactical allocation is an implementation concept, so it is not in any
   strategic payload: the toggle introduces it here, funded out of the
   category the schema names, and the weight is moved rather than created.

   THE PYTHON MIRROR is rules.tiltedCategories(); the two must agree exactly
   or the screen and the workbook drift - the same discipline roundWeights
   follows. Everything downstream (products, fees, notionals, doughnuts) is
   derived from these category weights, so nothing else needs to know. */
function tiltPct() { return App.opt('rules.tacticalTiltPct', 8); }
function tiltFundedFrom() { return App.opt('rules.tacticalTiltFundedFrom', ''); }
function tiltCategory() { return App.opt('rules.tacticalTiltCategory', ''); }

function canFundTilt(categories) {
  var from = tiltFundedFrom();
  for (var i = 0; i < categories.length; i++) {
    if (categories[i].name === from) return categories[i].weightPct >= tiltPct();
  }
  return false;
}

function tiltCategories(categories, on) {
  var out = categories.map(function (c) {
    return { name: c.name, weightPct: c.weightPct, assets: (c.assets || []).slice() };
  });
  if (!on || !canFundTilt(out)) return out;
  var pct = tiltPct(), from = tiltFundedFrom();
  out.forEach(function (c) {
    if (c.name !== from) return;
    var before = c.weightPct, after = before - pct;
    c.weightPct = after;
    var share = before ? after / before : 0;
    c.assets = c.assets.map(function (a) {
      return { reportingName: a.reportingName, weightPct: a.weightPct * share };
    });
  });
  out.push({ name: tiltCategory(), weightPct: pct,
             assets: [{ reportingName: tiltCategory(), weightPct: pct }] });
  return out;
}

/* ---- the strategic volatility premium (D53) ------------------------------
   The second overlay, and the mirror of rules.volPremiumCategories(). It
   takes ALREADY-TILTED categories, because the share is of the funding
   category as implemented: what the tilt left behind. The new category is
   inserted directly after the one that funded it, which is where the sheet
   reads it - under Investment Grade Fixed Income, before Other Fixed Income.

   The currency gate is not here. App.volPremium() has already applied it, on
   the same list of currencies the schema serves to both sides. */
function volPremiumShare() { return App.opt('rules.volPremiumShare', 0.075); }
function volPremiumFundedFrom() { return App.opt('rules.volPremiumFundedFrom', ''); }
function volPremiumCategory() { return App.opt('rules.volPremiumCategory', ''); }

function volPremiumCategories(categories, on) {
  var out = categories.map(function (c) {
    return { name: c.name, weightPct: c.weightPct, assets: (c.assets || []).slice() };
  });
  if (!on) return out;
  var share = volPremiumShare(), from = volPremiumFundedFrom();
  for (var i = 0; i < out.length; i++) {
    if (out[i].name !== from) continue;
    var before = out[i].weightPct;
    if (before <= 0) break;
    var take = before * share, after = before - take;
    out[i].weightPct = after;
    var scale = after / before;
    out[i].assets = out[i].assets.map(function (a) {
      return { reportingName: a.reportingName, weightPct: a.weightPct * scale };
    });
    out.splice(i + 1, 0, {
      name: volPremiumCategory(), weightPct: take,
      assets: [{ reportingName: volPremiumCategory(), weightPct: take }]
    });
    break;
  }
  return out;
}

/* The categories AS IMPLEMENTED - what every row, fee and chart below is
   built from. Step 1 keeps showing the strategic allocation untouched.
   Tilt first, then the premium, the order rules.implementedCategories()
   fixes: the premium's share is of what the tilt leaves. */
function baseCategories() {
  var base = App.base();
  if (!base || base.status !== 'ready') return [];
  return volPremiumCategories(
    tiltCategories(base.data.categories, App.tacticalTilt()), App.volPremium());
}

/* The strategic categories, for the funding test the toggle is gated on. */
function strategicCategories() {
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

/* What the rail counts: the categories a PWA picks a sleeve for. The
   auto-attached ones are carried by their toggle and have no control in the
   list, so counting them would report progress against work nobody does.
   complete() keeps using filledCount - the gate is about the whole model,
   including a category whose library has not loaded yet. */
function pickedCount() {
  var live = baseCategories().filter(function (c) { return !isAuto(c.name); });
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
          cost: product.productCost, feeGroup: product.feeGroup,
          mgmt: managementFee(product.feeGroup),
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
      /* percent x percent = bp; null while the book is unpriced */
      item.wtdBp = item.mgmt === null ? null : (item.cost + item.mgmt) * item.weight;
      item.notional = Math.round(App.mandateSize() * item.weight / 100 / ROUND_TO) * ROUND_TO;
    });
  }
  return groups;
}

/* Weight and notional always add up; the fee adds up only once every row
   has one, and is null - not zero - until then. */
function totals(groups) {
  var weight = 0, bp = 0, notional = 0, unpriced = false;
  groups.forEach(function (group) {
    group.items.forEach(function (item) {
      weight += item.weight; notional += item.notional;
      if (item.wtdBp === null) unpriced = true; else bp += item.wtdBp;
    });
  });
  return { weight: weight, bp: unpriced ? null : bp, notional: notional };
}

/* ---- the variant, as read here (spec 8.1, D29, D49) ---------------------
   The choice itself moved to the base portfolio tier of step 1: the variant
   decides which allocations exist, so it has to be answered before anything
   is built, not after (D49). What remains here is the answer, shown because
   every sleeve below is scoped by it and a PWA arriving at step 2 needs to
   see which book they are implementing. */
/* ---- the tactical tilt toggle (D50) -------------------------------------
   Offered disabled, with the reason, where the strategic portfolio cannot
   fund it - every All Equity book holds no investment grade fixed income at
   all. Silently doing nothing, or quietly funding it from somewhere else,
   would both be worse than saying so. */
function tacticalTiltField() {
  var strategic = strategicCategories();
  if (!strategic.length) return '';
  var fundable = canFundTilt(strategic);
  var on = App.tacticalTilt() && fundable;
  var pct = tiltPct();
  return '<div class="tilt-field' + (on ? ' done' : '') + '">'
    + '<div class="chk"><input type="checkbox" id="impltilt" data-tilt="1"'
    + (on ? ' checked' : '')
    + (fundable && App.canEdit() ? '' : ' disabled')
    + ' aria-describedby="tiltnote">'
    + '<label for="impltilt">Tactical Tilts</label></div>'
    + '<p class="chk-note" id="tiltnote">' + (fundable
        ? App.esc(pct.toFixed(0)) + '% funded pro rata from '
          + App.esc(tiltFundedFrom()) + '.'
        : 'Needs ' + App.esc(pct.toFixed(0)) + '% of '
          + App.esc(tiltFundedFrom()) + ' to fund; this portfolio holds none.')
    + '</p></div>';
}

/* The volatility premium toggle, beneath the tilt and reading the same way.
   Two reasons it can be offered disabled, and each says which: a currency
   that may not hold the product at all, and a portfolio with nothing to fund
   it from. The currencies come from the schema - the page never names one. */
function volPremiumField() {
  var strategic = strategicCategories();
  if (!strategic.length) return '';
  var from = volPremiumFundedFrom();
  var allowed = App.canHoldVolPremium();
  var fundable = strategic.some(function (c) {
    return c.name === from && c.weightPct > 0;
  });
  var on = App.volPremium() && fundable;
  var currencies = App.opt('rules.volPremiumCurrencies', []);
  var note;
  if (!allowed) {
    note = 'Available in ' + App.esc(currencies.join(' and '))
      + ' only; this book is in ' + App.esc(App.basis().currency) + '.';
  } else if (!fundable) {
    note = 'Funded from ' + App.esc(from) + '; this portfolio holds none.';
  } else {
    note = App.esc((volPremiumShare() * 100).toFixed(1))
      + '% of ' + App.esc(from) + ' after tilts, funded pro rata from it.';
  }
  return '<div class="tilt-field' + (on ? ' done' : '') + '">'
    + '<div class="chk"><input type="checkbox" id="implvolprem" data-volprem="1"'
    + (on ? ' checked' : '')
    + (allowed && fundable && App.canEdit() ? '' : ' disabled')
    + ' aria-describedby="volpremnote">'
    + '<label for="implvolprem">Strategic Volatility Premium</label></div>'
    + '<p class="chk-note" id="volpremnote">' + note + '</p></div>';
}

/* ---- revealing and hiding the fee layer (D52) ----------------------------
   Two mechanisms, because the two directions are not symmetrical. Turning
   fees ON re-renders first: the rail block and the fee cells are brand new
   nodes, and a CSS transition does not run on a node that was born in its
   final state, so they carry a one-shot class and a keyframe animation plays
   itself in. Turning them OFF has to animate what is about to be destroyed,
   so the caller runs the leave animation on the live DOM and only then lets
   the state move and the render take the cells away.

   feeReveal is read by both renderRail and renderView in one pass of the
   renderer and cleared at the end of it, so a single toggle animates the
   rail and the table together. */
var FEE_MOTION = 360;                 /* ms; the fee reveal, in and out */
var feeReveal = false;
var feeHiding = false;

function reducedMotion() {
  return !!(window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

/* Play the leave animation over the live DOM, then hand back. Reduced motion
   and a page with nothing to animate both commit straight away. */
function hideFeesThen(commit) {
  if (feeHiding) return;
  var body = document.querySelector('#tier-sleeves .fee-body');
  var table = document.querySelector('.tbl.impl');
  if (reducedMotion() || (!body && !table)) { commit(); return; }
  feeHiding = true;
  if (body) body.classList.add('ravel');
  if (table) table.classList.add('fees-out');
  window.setTimeout(function () { feeHiding = false; commit(); }, FEE_MOTION);
}

/* Bring the pricing group into the rail's own view when it opens.
   The group closes the tier, so on a rail already scrolled to the sleeve
   pickers it unravels below the fold and a PWA is left looking at the
   controls they did not just ask for. The rail scrolls, never the document:
   scrollIntoView would move both. The target is the group's TOP - it does not
   move while the body grows downwards, so this can run before the animation
   finishes - offset by the sticky brand block, which would otherwise cover it. */
function scrollFeeGroupIntoView(smooth) {
  var rail = document.querySelector('.rail');
  var group = document.querySelector('#tier-sleeves .fee-group');
  if (!rail || !group || rail.scrollHeight <= rail.clientHeight) return;
  var railBox = rail.getBoundingClientRect();
  var groupBox = group.getBoundingClientRect();
  if (groupBox.top >= railBox.top && groupBox.bottom <= railBox.bottom) return;
  var brand = rail.querySelector('.rail-brand');
  var pad = (brand ? brand.offsetHeight : 0) + 10;
  var top = rail.scrollTop + (groupBox.top - railBox.top) - pad;
  if (rail.scrollTo) {
    rail.scrollTo({ top: top, behavior: (smooth && !reducedMotion()) ? 'smooth' : 'auto' });
  } else {
    rail.scrollTop = top;
  }
}

/* Strip the one-shot classes once they have played, so nothing is left
   holding an overflow or an animation the next render would replay - and
   correct the scroll once the block has its full height, since until then
   the rail had less to scroll through than the target asked for. That
   correction is smooth too: an instant one on top of the smooth scroll that
   is still running is exactly the jump it exists to avoid, and where the
   first scroll already arrived it is a no-op. */
function settleFeeReveal() {
  var played = document.querySelectorAll('.fee-body.unravel, .tbl.impl.fees-in');
  Array.prototype.forEach.call(played, function (node) {
    node.addEventListener('animationend', function handler() {
      node.removeEventListener('animationend', handler);
      node.classList.remove('unravel');
      node.classList.remove('fees-in');
      if (node.classList.contains('fee-body')) scrollFeeGroupIntoView(true);
    });
  });
}

/* ---- pricing: the fee schedule and the fee level (D51) ------------------
   The schedule is a segmented control with no default, the level a grid of
   the six points the framework prices, and under both a line saying which
   account-size tier the mandate fell in - the one input a PWA cannot see
   otherwise, since it comes from the top account size and not from anything
   in this tier. Every list here is the schema's, not this file's (spec 4.3).

   Rendered at the FOOT of the sleeve tier, below the pickers, behind its own
   Pricing label. It sat between the tilt toggle and the pickers until the rail
   grew too long to read; pricing is answered once and the pickers are answered
   five times, so the thing that repeats comes first and the thing that does not
   settles under it. The label is there because a fee schedule is not a sleeve
   and the tier heading says Sleeves. */
function feeFields() {
  var schedules = feeSchedules();
  if (!schedules.length) return '';
  var chosen = App.feeSchedule();
  var level = App.feeLevel();
  var tier = App.opt('fees.tier', null);
  var editable = App.canEdit();
  var entry = feeScheduleEntry();
  var on = App.includeFees();

  /* The toggle is the whole question: off, there is nothing below it and no
     fee column in the table (D52). It is a tick box rather than a segmented
     control because it has a default - no - and because the thing it turns on
     is a section, not a value. */
  var head = '<div class="fee-group">'
    + '<p class="fee-group-h">Pricing</p>'
    + '<div class="fee-toggle' + (on ? ' done' : '') + '">'
    + '<div class="chk"><input type="checkbox" id="implincfees" data-incfees'
    + (on ? ' checked' : '') + (editable ? '' : ' disabled')
    + ' aria-describedby="incfeesnote">'
    + '<label for="implincfees">Include fees</label></div>'
    + '<p class="chk-note" id="incfeesnote">' + (on
        ? 'Fee columns are shown in the table and written to the workbook.'
        : 'The proposal shows no fees. Tick to price the model.')
    + '</p></div>';
  if (!on) return head + '</div>';

  var seg = schedules.map(function (s) {
    return '<button type="button" data-feesched="' + App.esc(s.id) + '"'
      + ' aria-pressed="' + (s.id === chosen ? 'true' : 'false') + '"'
      + (editable ? '' : ' disabled') + '>' + App.esc(s.id) + '</button>';
  }).join('');
  /* .fee-body is one element so the reveal has one thing to animate; the
     class is a one-shot the renderer strips when the animation ends. */
  var html = head
    + '<div class="fee-body' + (feeReveal ? ' unravel' : '') + '">'
    + '<div class="fee-field' + (chosen ? ' done' : '') + '">'
    + '<span class="fee-label" id="feeschedlabel">Fee schedule</span>'
    + '<div class="fee-seg" role="group" aria-labelledby="feeschedlabel">' + seg + '</div>'
    + '<p class="vr-note">' + (entry
        ? App.esc(entry.note || '')
        : 'Choose how the book is priced. Nothing is priced until it is chosen.')
    + '</p></div>';

  var sources = App.opt('fees.sources', []);
  var points = App.opt('fees.points', []);
  var levels = App.opt('fees.levels', []);
  function idFor(source, point) {
    for (var i = 0; i < levels.length; i++) {
      if (levels[i].source === source && levels[i].point === point) return levels[i].id;
    }
    return null;
  }
  var grid = '';
  sources.forEach(function (source) {
    grid += '<span class="rh">' + App.esc(source) + '</span>';
    points.forEach(function (point) {
      var id = idFor(source, point);
      grid += '<button type="button" data-feelevel="' + App.esc(id || '') + '"'
        + ' aria-pressed="' + (id && id === level ? 'true' : 'false') + '"'
        + ' aria-label="' + App.esc(id || point) + '"'
        + (editable && id ? '' : ' disabled') + '>' + App.esc(point) + '</button>';
    });
  });
  html += '<div class="fee-field done">'
    + '<span class="fee-label" id="feelevellabel">Fee level</span>'
    + '<div class="fee-levels" role="group" aria-labelledby="feelevellabel">' + grid + '</div>'
    + '<p class="vr-note">' + (tier
        ? 'Tier ' + App.esc(tier.id) + ', ' + App.esc(tier.label) + ', from the top account size.'
        : 'No account-size tier: the mandate has no top account size.')
    + '</p>'
    + (App.opt('fees.placeholder', false)
        ? '<p class="fee-flag">Placeholder rates, not the published schedule.</p>' : '')
    + '</div></div></div>';
  return html;
}

/* ---- the rail tier (spec 9.4) ------------------------------------------- */
function renderRail() {
  var el = document.getElementById('tier-sleeves'); if (!el) return;
  if (App.step() !== 'impl' || App.phase() !== 'workspace') { el.hidden = true; return; }
  el.hidden = false;
  var base = App.base();
  var counts = pickedCount();
  var chosenVariant = App.variant();

  /* The variant is already answered by the time this tier renders - it is
     chosen with the base portfolio, and nothing resolves without it (D49) -
     so the summary sits above the base-status returns and reads the same
     whether the base is ready, resolving or failed. */
  var head = '<div class="tier-h"><h3>Sleeves</h3>'
    + (chosenVariant && base && base.status === 'ready'
        ? '<span class="tier-count">' + counts.filled + ' of ' + counts.total + '</span>'
        : '') + '</div>' + tacticalTiltField() + volPremiumField();

  /* Pricing closes the tier on every path, including the ones that never draw
     a picker: the schedule and the level are scenario state, answerable while
     the base is still resolving, and taking them away when the base fails
     would lose an answer the PWA had already given. */
  if (!base) {
    el.innerHTML = head + '<p class="field-note">Build a base portfolio first.</p>' + feeFields();
    return;
  }
  if (base.status !== 'ready') {
    el.innerHTML = head + '<p class="field-note">' + (base.status === 'error'
        ? 'The base portfolio could not be built. Retry it from the allocation step.'
        : 'Resolving the base portfolio…') + '</p>' + feeFields();
    return;
  }
  var html = head;

  /* The gate: no variant, no sleeves. Rendering the pickers disabled would
     invite clicking them; there is nothing behind them to pick yet. */
  if (!chosenVariant) {
    /* Not reachable by the normal route - a portfolio cannot be built without
       a variant, and step 2 is gated on a portfolio - but a hand-edited or
       part-migrated scenario could arrive here, so it says where to go. */
    el.innerHTML = html + '<p class="field-note">No implementation variant is '
      + 'set. Choose one with the base portfolio on the allocation step; it '
      + 'decides which sleeves each category offers and what they hold.</p>'
      + feeFields();
    return;
  }

  /* Only the categories a PWA actually picks for. An auto-attached category
     carries one sleeve by rule and is put there by the toggle above, so a
     control for it would be a control that can never be used; the count and
     the note below say it is in the model (D53). */
  html += '<div class="sl-list">';
  baseCategories().filter(function (category) {
    return !isAuto(category.name);
  }).forEach(function (category, i) {
    var lib = App.sleeveLib()[category.name];
    var chosen = sleeveFor(category.name);
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
  html += feeFields();
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
  /* The same count the rail shows, so the two never disagree about how far
     along the implementation is (D53). */
  var counts = pickedCount();
  var done = complete();
  var columnsBusy = App.columns().some(function (c) { return c.status !== 'ready'; });
  var exporting = App.exporting();
  /* the fee group is struck through, not hidden, under a schedule that
     ignores it: the column still says what the product is */
  var schedule = App.feeSchedule();
  var groupDead = !!schedule && !feeByGroup();
  var groupCell = groupDead
    ? '<td class="txt fee-col fee-dead" title="Not read under ' + App.esc(schedule) + '">'
    : '<td class="txt fee-col">';
  /* The three fee columns are in the table only while the proposal includes
     fees (D52). Each fee cell wraps its content in a span, because a column
     takes its width from its content: animating the span is what makes the
     column itself open and close rather than appearing at full width. */
  var fees = App.includeFees();
  function feeCell(cls, inner) {
    return fees ? '<td class="' + cls + ' fee-col"><span class="fcw">'
      + inner + '</span></td>' : '';
  }

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
    + '<table class="tbl impl' + (fees ? '' : ' no-fees')
    + (fees && feeReveal ? ' fees-in' : '') + '">'
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
    + (fees
        ? '<th scope="col" class="txt fee-col' + (groupDead ? ' fee-dead' : '')
          + '"><span class="fcw">Fee group</span></th>'
          + '<th scope="col" class="num fee-col"><span class="fcw">Mgmt fee</span></th>'
          + '<th scope="col" class="num fee-col"><span class="fcw">Wtd fee</span></th>'
        : '')
    + '<th scope="col" class="num">Notional</th>'
    + '</tr></thead><tbody>';

  groups.forEach(function (group) {
    var groupBp = 0, groupNotional = 0, groupWeight = 0;
    group.items.forEach(function (item) {
      groupNotional += item.notional; groupWeight += item.weight;
      if (groupBp !== null) groupBp = item.wtdBp === null ? null : groupBp + item.wtdBp;
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
      + '<td class="num"></td>'
      + feeCell('txt', '') + feeCell('num', '')
      + feeCell('num', group.items.length ? bpText(groupBp) : '')
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
        + (fees
            ? groupCell + '<span class="fcw">' + groupPill(item.feeGroup) + '</span></td>'
              + feeCell('num', feeText(item.mgmt))
              + feeCell('num', bpText(item.wtdBp))
            : '')
        + '<td class="num">' + money(item.notional) + '</td></tr>';
    });
  });
  /* The filler spans Ticker through Mgmt fee, so it is two columns shorter
     when the fee columns are not there. */
  html += '<tr class="grand"><th scope="row">Total</th>'
    + '<td class="prodcol"></td>'
    + '<td class="num">' + App.num(t.weight, 2, '%') + '</td>'
    + '<td colspan="' + (fees ? 9 : 7) + '"></td>'
    + feeCell('num', bpText(t.bp))
    + '<td class="num">' + money(t.notional) + '</td></tr>';
  html += '</tbody></table></div>';
  html += renderDonuts(groups, done);

  var reason = null;
  if (!App.canExport()) reason = 'Export is not available for your role.';
  else if (!App.variant()) reason = 'Choose an implementation variant first.';
  /* Only a proposal that includes fees needs a schedule: one that does not
     exports a sheet with no fee column to price (D52). */
  else if (fees && !schedule) reason = 'Choose a fee schedule in the rail first.';
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
    + 'persisted scenario and the current SAA analytics.</p>'
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
    var name = 'PMG_Scenario.xlsx';
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
  if (e.target.dataset.tilt !== undefined) {
    App.setTacticalTilt(e.target.checked);
    return;
  }
  if (e.target.dataset.volprem !== undefined) {
    App.setVolPremium(e.target.checked);
    return;
  }
  /* Revealing renders first and lets the new nodes animate themselves in;
     hiding animates what is on screen and commits when it has played (D52). */
  if (e.target.dataset.incfees !== undefined) {
    if (e.target.checked) {
      feeReveal = true;
      App.setIncludeFees(true);
    } else {
      hideFeesThen(function () { App.setIncludeFees(false); });
    }
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
  var sched = e.target.closest ? e.target.closest('[data-feesched]') : null;
  if (sched) { App.setFeeSchedule(sched.dataset.feesched); return; }
  var level = e.target.closest ? e.target.closest('[data-feelevel]') : null;
  if (level) { App.setFeeLevel(level.dataset.feelevel); return; }
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
App.addRenderer(function () {
  ensureLibraries();
  renderRail();
  renderView();
  /* Both painted from the same one-shot, so it is cleared once, here, and
     the classes it wrote are stripped when they have finished playing. The
     rail follows the block it just opened. */
  if (feeReveal) {
    feeReveal = false;
    settleFeeReveal();
    scrollFeeGroupIntoView(true);
  }
});
})();
