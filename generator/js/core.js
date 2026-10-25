var App = (function () {
'use strict';

/* =============================================================================
   Core layer: state, data flows over the HTTP surface, and the step-1 renders.

   Everything the server owns arrives through the schema and the resolve
   payloads - option lists, thresholds, the availability set, category and
   asset-class names and their order (spec 4.3). Nothing here hardcodes a
   category list; the palette is keyed by category NAME (spec 6.6).
   ========================================================================== */

/* ---- state (spec 5.3) --------------------------------------------------- */
var state = {
  phase: 'landing',                 /* 'landing' | 'workspace' */
  scenarioId: null,
  mandate: null,                    /* {topAccountSize, mandateSize, primaryPwa, entity} */
  /* the notices (D199): {noticeId: {confirmed: {by, at, revision, name,
     title, ticked, tickWords, category, sleeve} or null, declined: [{by, at,
     category, sleeve}]}} */
  notices: {},
  basis: { currency: 'USD', hedging: 'Hedged' },
  schema: null,
  schemaStatus: 'idle',             /* 'idle'|'loading'|'ready'|'error' */
  schemaError: null,
  /* Whether the scenario service is answering (D64). 'down' is set by
     apiFetch the moment the proxy reports the backend unreachable (502),
     timed out (504) or the fetch itself fails, and cleared by the next call
     that succeeds. It gates every write through canEdit, so one flag turns
     the whole page read only. */
  service: 'up',                    /* 'up' | 'down' */
  serviceSince: null,               /* when it went down, for the message */
  fromCache: false,                 /* this workspace was restored, not fetched */
  retryAt: null,                    /* epoch ms of the next automatic attempt */
  retryStep: 0,
  step: 'aa',                       /* 'aa' | 'impl' */
  columns: [],                      /* index 0 is always the base; see makeColumn */
  basisChosen: false,               /* the PWA has answered currency + hedging */
  basisAnswered: { currency: false, hedging: false },   /* each half, until both are (D93) */
  implSeen: false,                  /* step 2 has been opened at least once */
  variant: null,                    /* implementation type; gates step 2 (D29) */
  /* On by default: the tilt is house practice wherever it can be funded.
     A portfolio that cannot fund it renders the toggle off and disabled,
     and resolveOverlays skips it, so this one default covers both (D50). */
  tacticalTilt: true,
  /* The strategic volatility premium (D53): held by default wherever the
     currency allows it, like the tilt, and forbidden outside the currencies
     its rule names (D155) - App.volPremium() applies that gate on read. */
  volPremium: true,
  /* The base portfolio tier rolls up on the implementation step: those
     settings are answered by then, and the rail is long. Auto on every step
     change, so the chevron's override lasts as long as the step does;
     baseRoll is the one-shot that tells the renderer to animate rather than
     to paint the new state flat. */
  baseCollapsed: false,
  baseRoll: null,                   /* null | 'up' | 'down' */
  /* Whether the proposal shows fees at all (D52). Off to begin with: a fee is
     a conversation a PWA opens deliberately, and until they do, the pricing
     controls and the three fee columns are not in the page. */
  includeFees: false,
  /* How the book is priced (D51). The schedule is a choice with no default,
     like the variant; the level has a prescribed one, which the server sets
     when it creates the scenario and the schema names (fees.defaultLevel). */
  feeSchedule: null,                /* 'CASP' | 'RDR' | null */
  feeLevel: null,                   /* e.g. 'PMG Target', or the custom level */
  customFees: {},                   /* the PWA's rates by schedule, under it (D96) */
  otherCharges: {},                 /* the charges quoted beside the fees (D177) */
  sleeves: {},                      /* category -> sleeve name (pickable categories only) */
  sleeveLib: {},                    /* category -> {status, sleeves, error} */
  /* the proposal's own sleeves (D170): category -> {name, note, mode,
     products: [{productId, weightPct}]}, in force where no library name is
     chosen; and the products each category may hold, fetched on demand */
  customSleeves: {},
  customLib: {},                    /* category -> {status, products, mayBuild, error} */
  /* a proposal built product by product (D171): {active, profile, products:
     [{productId, weightPct, category}], builtBy, approval, notice} or null;
     what one is built from under the type and basis in force; and the
     rail's pending switch between the two kinds of proposal */
  customPortfolio: null,
  cpLib: { key: null, status: 'idle', data: null, error: null },
  cpConfirm: null,
  exporting: { status: 'idle', error: null },
  basisDraft: null,                 /* pending basis change awaiting confirmation */
  /* The base tier's unfinished answer. The key is built from these three the
     moment they are enough (D54): a risk level with no allocation types - an
     all-equity book - is enough on its own. */
  baseDraft: { allocationType: '', excludeRealAssets: false, riskLevel: '' },
  rebuilding: false
};
var draft = null;                   /* mandate dialog working copy */
var dlgOpener = null;
var seqCounter = 0;
var extras = [];
var picker = null;
/* In-flight variant PUT. A resolve waits on it, so the server always
   knows the variant before it is asked to validate a key against it. */
var variantPending = null;

/* ---- tiny utilities ----------------------------------------------------- */
function esc(t) {
  return String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
}
/* An amount, in dollars whatever the proposal's base currency: the PWA
   enters the mandate and the top account size in dollars, and every
   product's minimum is in dollars (D183). */
function money(n) {
  return (n === null || n === undefined || isNaN(n))
    ? '—' : '$' + Math.round(n).toLocaleString('en-US');
}
function parseMoney(v) { return Math.round(Number(String(v).replace(/[^0-9.]/g, '')) || 0); }

/* Finiteness guard (spec 15.8): a non-finite number must never render. */
function num(x, digits, suffix) {
  if (typeof x !== 'number' || !isFinite(x)) {
    console.error('[proposalTool] non-finite value reached a render:', x);
    return '';
  }
  return x.toFixed(digits) + (suffix || '');
}
function pct1(x) { /* weights: dash under 0.05, else 1dp percent */
  if (typeof x !== 'number' || !isFinite(x)) {
    console.error('[proposalTool] non-finite weight:', x); return '';
  }
  return (x < 0.05) ? '—' : x.toFixed(1) + '%';
}

/* currency | riskLevel | allocationType | excludeRealAssets - the supplying
   database's own key, read out of its portfolio names (D54). An all-equity
   book has no allocation type and both trailing fields read NA. */
function keyStr(k) {
  if (!k.allocationType) return [k.currency, k.riskLevel, 'NA', 'NA'].join('|');
  return [k.currency, k.riskLevel, k.allocationType, k.excludeRealAssets ? 1 : 0].join('|');
}
function keyEq(a, b) { return keyStr(a) === keyStr(b); }

/* ---- schema access ------------------------------------------------------ */
function schemaReady() { return state.schemaStatus === 'ready' && !!state.schema; }
function opt(path, fallback) {
  var node = state.schema, parts = path.split('.');
  for (var i = 0; i < parts.length; i++) {
    if (!node) return fallback;
    node = node[parts[i]];
  }
  return (node === undefined || node === null) ? fallback : node;
}
function availabilitySet() {
  if (!state._avail || state._availFrom !== state.schema) {
    var list = opt('availability', []);
    var set = {};
    for (var i = 0; i < list.length; i++) set[list[i]] = 1;
    state._avail = set;
    state._availFrom = state.schema;
  }
  return state._avail;
}
function available(k) { return !!availabilitySet()[keyStr(k)]; }
/* The private markets minimums (D200): what a portfolio that holds private
   markets needs - the lowest minimum any product has at it, or a gap where
   none has one - read from the schema for the basis currency. Null for one
   that holds none. */
function pmGate(k) {
  var gates = opt('rules.privateMarkets.gates', null);
  var ks = k ? keyStr(k) : '';
  if (!gates || !Object.prototype.hasOwnProperty.call(gates, ks)) return null;
  return gates[ks] == null ? { gap: true, minimum: null } : { gap: false, minimum: gates[ks] };
}
/* whether the mandate keeps this portfolio's allocation closed */
function pmClosed(k) {
  var need = pmGate(k), size = state.mandate ? state.mandate.mandateSize : null;
  if (!need || size === null || size === undefined) return false;
  return need.gap || size < need.minimum - 1e-6;
}
/* $26m, as the rail says it */
function pmMillions(n) {
  var m = n / 1e6;
  return '$' + (Math.round(m * 100) / 100).toLocaleString('en-US') + 'm';
}
function pmClosedWords(k) {
  var need = pmGate(k);
  if (!need) return '';
  return need.gap ? 'no private markets minimum set yet' : 'needs a ' + pmMillions(need.minimum) + ' mandate';
}
/* Why a level cannot be had (C6). "Unavailable" reads as an outage; what is
   actually true is narrower and usually fixable, and the availability set
   already knows it: the same level exists with the exclusion the other way,
   or under another allocation, or not at all on this basis and type. */
function whyUnavailable(allocationType, excludeRealAssets, risk) {
  var probe = buildKey(allocationType, excludeRealAssets, risk);
  if (!probe || available(probe)) return '';
  /* the mandate is below what its private markets need (D200) */
  if (pmClosed(probe)) return pmClosedWords(probe);
  if (reAllowed(allocationType) && available(buildKey(allocationType, !excludeRealAssets, risk))) {
    return excludeRealAssets ? 'not offered ex-RAs' : 'only offered ex-RAs';
  }
  var others = allocationChoices(risk).filter(function (t) {
    return t !== allocationType
      && (available(buildKey(t, false, risk)) || available(buildKey(t, true, risk)));
  });
  if (others.length) return 'not offered as ' + allocationType;
  return 'not offered for ' + (state.variant || 'this type') + ' in ' + state.basis.currency;
}
function used(k) {
  return state.columns.some(function (c) { return keyEq(c.key, k); });
}
function reAllowed(allocationType) {
  return opt('options.reAllowed', ['Full', 'ex-HFs']).indexOf(allocationType) >= 0;
}

/* The allocation types a risk level offers - the facet the schema derives
   from the universe. Empty for an all-equity level: it holds no alternatives,
   so there is nothing to choose, which is what greys the selector. Nothing
   here names a risk level (D54). */
function typesForRisk(risk) {
  var byRisk = opt('options.allocationTypesByRisk', null);
  if (!byRisk || !risk) return opt('options.allocations', []);
  return byRisk[risk] || [];
}
function isAllEquityRisk(risk) { return !!risk && typesForRisk(risk).length === 0; }

/* What the allocation select may offer: the variant's and mandate's list,
   narrowed to what the chosen risk level actually holds. */
function allocationChoices(risk) {
  var offered = opt('options.allocations', []);
  if (!risk) return offered;
  var types = typesForRisk(risk);
  return offered.filter(function (t) { return types.indexOf(t) >= 0; });
}

/* One place builds a key from the three controls. A level with no allocation
   types yields the all-equity key whatever the other two say; otherwise an
   allocation is needed, and the exclusion only counts where the type can hold
   real assets at all. Null means "not enough to build yet". */
function buildKey(allocationType, excludeRealAssets, riskLevel) {
  if (!riskLevel) return null;
  var currency = state.basis.currency;
  if (isAllEquityRisk(riskLevel)) {
    return { currency: currency, riskLevel: riskLevel,
             allocationType: null, excludeRealAssets: null };
  }
  if (!allocationType) return null;
  return { currency: currency, riskLevel: riskLevel, allocationType: allocationType,
           excludeRealAssets: reAllowed(allocationType) ? !!excludeRealAssets : false };
}
/* Read only while the service is down (D64). Every control in the rail, the
   pickers and the dialogs already asks this before enabling itself, so this
   one line is what makes degraded mode safe: nothing can be changed that
   could not be saved. */
function canEdit() { return state.service !== 'down' && opt('capabilities.canEdit', true); }
function canExport() { return state.service !== 'down' && opt('capabilities.canExport', true); }
function autoSleeveCategories() { return opt('rules.autoSleeveCategories', []); }

/* ---- overlay resolver (D155) ---------------------------------------------
   The overlay rules - the tilt, the premium and any the desk adds in the
   repository's Overlay Funding tab - resolved top to bottom, each reading the
   allocation as the rules above it left it. A rule takes size% of the
   portfolio split across its sources by their weights ('portfolio'), or
   size% of what each source holds at its step times its weight ('sources');
   a source's assets shrink pro rata; the rule's own category is placed after
   the category it names, or at the end. A rule is skipped when its switch is
   off, the currency is not one it names, or a source is not held or would go
   below zero.

   THE PYTHON MIRROR is rules.resolveOverlays(); the two must agree exactly or
   the screen and the workbook drift, and tests/test_overlay_rules.py runs
   this block, between these two markers, against it. Pure: it reads nothing
   but its arguments, so the repository console judges a draft with it too. */
var OVERLAY_TOLERANCE = 1e-9;      /* rules.OVERLAY_TOLERANCE */
function overlayWeightOf(c) { return +(c.weightPct || 0); }
function overlayTake(rule, source, before) {
  var size = +rule.size, weight = +source.weightPct / 100;
  return rule.basis === 'portfolio' ? size * weight : before * (size / 100) * weight;
}
function overlayRawTakes(rule, categories) {
  var byName = {};
  (categories || []).forEach(function (c) { byName[c.name] = c; });
  var out = [], sources = rule.sources || [];
  for (var i = 0; i < sources.length; i++) {
    var held = byName[sources[i].category];
    if (!held) return null;
    var before = overlayWeightOf(held);
    if (!(before > 0)) return null;               /* NaN as well as zero */
    out.push({ category: sources[i].category, before: before, take: overlayTake(rule, sources[i], before) });
  }
  return out.length ? out : null;
}
function overlayTakes(rule, categories) {
  var raw = overlayRawTakes(rule, categories);
  if (!raw || raw.some(function (t) { return t.before < t.take - OVERLAY_TOLERANCE; })) return null;
  return raw.map(function (t) { return { category: t.category, take: Math.min(t.take, t.before) }; });
}
/* what the repository refuses: every source held - in *strategic*, the
   portfolio's strategic allocation, when given - and one would go below zero */
function overlayShortfall(rule, categories, strategic) {
  var heldIn = {};
  (strategic || categories || []).forEach(function (c) { heldIn[c.name] = overlayWeightOf(c); });
  var sources = rule.sources || [];
  if (!sources.length || sources.some(function (s) { return !(heldIn[s.category] > 0); })) return [];
  var byName = {};
  (categories || []).forEach(function (c) { byName[c.name] = c; });
  var out = [];
  sources.forEach(function (s) {
    var held = byName[s.category];
    var before = held ? overlayWeightOf(held) : 0;
    if (before !== before) return;
    var take = overlayTake(rule, s, before);
    if (before < take - OVERLAY_TOLERANCE) out.push({ category: s.category, after: before - take });
  });
  return out;
}
function overlaySkip(rule, selections, currency) {
  if (rule.toggle && !(selections || {})[rule.toggle]) return 'off';
  var currencies = rule.currencies || [];
  if (currencies.length && currencies.indexOf(currency) < 0) return 'currency';
  return null;
}
function overlayCopy(o) {
  var copy = {};
  Object.keys(o).forEach(function (k) { copy[k] = o[k]; });
  return copy;
}
function resolveOverlays(categories, rules, selections, currency) {
  var result = (categories || []).map(function (c) {
    var copy = overlayCopy(c);
    copy.assets = (c.assets || []).map(overlayCopy);
    return copy;
  });
  var steps = [];
  (rules || []).forEach(function (rule) {
    var step = { id: rule.id, name: rule.name, into: rule.into, status: null,
                 amount: 0, takes: [] };
    steps.push(step);
    var reason = overlaySkip(rule, selections, currency);
    if (reason) { step.status = reason; return; }
    var takes = overlayTakes(rule, result);
    if (!takes) { step.status = 'unfundable'; return; }
    var byName = {};
    result.forEach(function (c) { byName[c.name] = c; });
    var amount = 0;
    takes.forEach(function (t) {
      var category = byName[t.category];
      var before = +category.weightPct, after = before - t.take;
      category.weightPct = after;
      var share = before ? after / before : 0;
      category.assets.forEach(function (a) { a.weightPct = +a.weightPct * share; });
      amount += t.take;
      step.takes.push({ category: t.category, before: before, take: t.take, after: after });
    });
    var entry = { name: rule.into, weightPct: amount,
                  assets: [{ reportingName: rule.row || rule.into, weightPct: amount }] };
    var place = rule.place || 'end', index = -1;
    if (place !== 'end') {
      for (var i = 0; i < result.length; i++) { if (result[i].name === place) { index = i; break; } }
    }
    if (index < 0) result.push(entry); else result.splice(index + 1, 0, entry);
    step.status = 'applied';
    step.amount = amount;
  });
  return { categories: result, steps: steps };
}
/* rules._pct: '{:g}'.format(round(value, 4)) */
function overlayPct(value) { return String(+(+value).toFixed(4)); }
/* the rule in words, as rules.overlayWords() writes it */
function overlayWords(rule) {
  var sources = rule.sources || [], size = overlayPct(rule.size);
  if (sources.length <= 1) {
    var named = sources.length ? sources[0].category : 'no source';
    return rule.basis === 'portfolio' ? size + '% of the portfolio from ' + named
      : size + '% of ' + named + ' as it stands';
  }
  var split = sources.map(function (s) { return s.category + ' ' + overlayPct(s.weightPct) + '%'; }).join(' and ');
  return rule.basis === 'portfolio' ? size + '% of the portfolio, split ' + split
    : size + '% of each source as it stands, times its weight: ' + split;
}
/* ---- end of the overlay resolver ---- */

/* The rules in force for the implementation type the schema was fetched for,
   in the order they resolve; and the one a proposal switch answers to. */
function overlayList() { var o = opt('rules.overlays', null); return (o && o.rules) || []; }
/* the rules the type has no sleeve for, and every rule's place (D191) */
function overlaysWithheld() { var o = opt('rules.overlays', null); return (o && o.withheld) || []; }
function overlayOrder() { var o = opt('rules.overlays', null); return (o && o.order) || []; }
function overlayRule(toggle) {
  return overlayList().filter(function (r) { return r.toggle === toggle; })[0] || null;
}

/* The display name for a risk level. The value stays what the schema sent -
   it is the fourth field of the portfolio key and keys the bake - so the UI
   shows the label and submits the value. Falls back to the value itself, so a
   level the map has not been told about still reads sensibly (D35). */
function riskLabel(value) {
  var labels = opt('options.riskLevelLabels', null);
  return (labels && labels[value]) || value;
}

/* Derived naming (the template is hardcodable; the values are not - spec 4.3).
   Order is risk level, then allocation type, then the exclusion, with the
   currency in front of the full form: "USD Moderate-Aggressive Core ex-RAs".
   The risk level prints through riskLabel, so the name says what the rail
   says - the key carries the database's own spelling (D54).

   Built from the KEY, never from the payload's own name or header fields.
   Those are frozen into the baked slices, so a naming change would show on
   some surfaces and not others until a re-bake; deriving from the key makes
   the screen consistent the moment the rule changes (D36). */
function headerName(k) {
  var risk = riskLabel(k.riskLevel);
  if (!k.allocationType) return risk;               /* an all-equity book is its risk level */
  return risk + ' ' + k.allocationType + (k.excludeRealAssets ? ' ex-RAs' : '');
}
function fullName(k) { return (k.currency || state.basis.currency) + ' ' + headerName(k); }

/* ---- live regions (spec 13.3): present from first paint -----------------
   Messages queue rather than replace: two columns dropped in the same tick
   must both be announced, and a cleared-then-set region only re-announces
   once settled. */
var liveQueue = { polite: [], assertive: [] };
var liveTimer = { polite: null, assertive: null };
function announce(politeness, message) {
  var channel = politeness === 'assertive' ? 'assertive' : 'polite';
  liveQueue[channel].push(message);
  if (liveTimer[channel]) return;
  liveTimer[channel] = window.setTimeout(function () {
    liveTimer[channel] = null;
    var node = document.getElementById('live-' + channel);
    if (!node) { liveQueue[channel] = []; return; }
    node.textContent = liveQueue[channel].join(' ');
    liveQueue[channel] = [];
  }, 60);
}

/* ---- focus preservation across re-render (spec 5.4, 15.6) --------------- */
function preserveFocus(fn) {
  var active = document.activeElement;
  var id = active && active.id;
  var selStart, selEnd;
  if (active && typeof active.selectionStart === 'number') {
    selStart = active.selectionStart; selEnd = active.selectionEnd;
  }
  fn();
  if (!id) return;
  var again = document.getElementById(id);
  if (again && again !== document.activeElement) {
    try {
      again.focus();
      if (selStart !== undefined && typeof again.setSelectionRange === 'function') {
        again.setSelectionRange(selStart, selEnd);
      }
    } catch (e) { /* focus is best-effort */ }
  }
}

/* =============================================================================
   Data flows
   ========================================================================== */

/* the implementation types that ask for a custodian: PMG ESG (D195) */
function custodyNeeded(variant) {
  return !!variant && (opt('options.custodyTypes', []) || []).indexOf(variant) !== -1;
}
/* the custodian in force: the one chosen, under a type that asks for one */
function custodian() {
  return custodyNeeded(state.variant) && state.mandate ? state.mandate.entity || '' : '';
}
/* the custodian as a query parameter (D193); nothing where none is in force */
function entityQuery() {
  return custodian() ? '&entity=' + encodeURIComponent(custodian()) : '';
}
function entityName(id) {
  var found = (opt('options.entities', []) || []).filter(function (e) { return e.id === id; })[0];
  return found ? found.name : (id || '');
}

function schemaQuery() {
  var q = '?currency=' + encodeURIComponent(state.basis.currency)
        + '&hedging=' + encodeURIComponent(state.basis.hedging);
  if (state.mandate && state.mandate.mandateSize) {
    q += '&mandateSize=' + encodeURIComponent(state.mandate.mandateSize);
  }
  /* The top account size sets a flat schedule's fee tier (D51); the mandate
     size, sent above, is what a marginal schedule blends across the ladder
     (D83). The schema carries the resulting rates either way. */
  if (state.mandate && state.mandate.topAccountSize) {
    q += '&topAccountSize=' + encodeURIComponent(state.mandate.topAccountSize);
  }
  /* the custodian, under PMG ESG: the overlays served are the ones with an
     edition for it (D193) */
  q += entityQuery();
  /* The variant narrows options.allocations and the availability set, so it
     belongs in the key of what the schema describes (D49). */
  if (state.variant) q += '&variant=' + encodeURIComponent(state.variant);
  return q;
}

/* The blended rate the chosen schedule prices at, or null when it is not a
   marginal schedule. Read straight off the schema - the server does the
   blending (D83) - and used only to notice that a mandate edit moved it. */
function feeBlendNow() {
  var table = (opt('fees.rates', null) || {})[state.feeSchedule];
  if (!table || !table.marginal) return null;
  var rate = (table.levels || {})[state.feeLevel || opt('fees.defaultLevel', null)];
  return (typeof rate === 'number') ? rate : null;
}

/* Whether the chosen variant mandates the real-estate exclusion. Read from the
   schema, never a list in this file - the same rule the categories and the
   risk labels follow (spec 4.3). */
function variantForcesExcludeRE(name) {
  if (!name) return false;
  return opt('options.variantsExcludingRealEstate', []).indexOf(name) !== -1;
}

async function fetchSchema() {
  state.schemaStatus = 'loading';
  refresh();
  var tierBefore = opt('fees.tier.id', null);
  var blendBefore = feeBlendNow();
  try {
    state.schema = await apiFetch('/scenario/schema' + schemaQuery());
    state.schemaStatus = 'ready';
    state.schemaError = null;
    pruneUnavailableColumns();
    /* A mandate edit can re-price every management fee on the sheet without
       any row visibly changing: it can move a flat schedule's account-size
       tier, or move the blend a marginal schedule pays across the ladder
       (D83). Either way the change is announced, because nothing on screen
       would otherwise say it happened. */
    var tierAfter = opt('fees.tier.id', null);
    var blendAfter = feeBlendNow();
    if (blendBefore !== null && blendAfter !== null && blendBefore !== blendAfter) {
      announce('polite', 'Effective fee rate is now ' + blendAfter.toFixed(4)
        + '%; management fees re-priced.');
    } else if (tierBefore && tierAfter && tierBefore !== tierAfter) {
      announce('polite', 'Account size tier is now ' + opt('fees.tier.label', tierAfter)
        + '; management fees re-priced.');
    }
  } catch (err) {
    state.schemaError = err.message || String(err);
    /* A schema already in hand is not thrown away because a later fetch
       failed (D64). The page keeps rendering what it has, read only, and the
       banner says why; only a page that never had a schema has nothing to
       show and falls through to the full-page state. */
    state.schemaStatus = state.schema ? 'ready' : 'error';
  }
  refresh();
}

/* Columns whose combination the (re-fetched) schema no longer offers are
   dropped and named (spec 11.2 step 4, 11.4 step 3). */
function pruneUnavailableColumns() {
  if (!schemaReady()) return;
  var survivors = [];
  state.columns.forEach(function (col, i) {
    if (available(col.key)) { survivors.push(col); return; }
    abortColumn(col);
    announce('assertive', fullName(col.key)
      + ' is not available under the current mandate and basis and has been removed.');
    /* never the base: the store refuses to remove it, and it has no need to
       be removed - the next base recorded takes its place (D102) */
    if (i > 0) deleteColumnOnServer(col.key);
  });
  state.columns = survivors;
}

/* ---- columns ------------------------------------------------------------ */
function makeColumn(key, role) {
  return {
    key: key,
    role: role,                       /* 'base' | 'comparison' */
    status: 'loading',                /* 'loading' | 'ready' | 'error' */
    requestedAt: Date.now(),
    skel: false,                      /* true once the 200ms threshold passes */
    skelTimer: null,
    abort: null,
    seq: 0,
    error: null,
    data: null
  };
}

function abortColumn(col) {
  if (col.skelTimer) { clearTimeout(col.skelTimer); col.skelTimer = null; }
  if (col.abort) { try { col.abort.abort(); } catch (e) {} col.abort = null; }
}

function startResolve(col) {
  abortColumn(col);
  col.status = 'loading';
  col.error = null;
  col.skel = false;
  col.requestedAt = Date.now();
  var seq = ++seqCounter;
  col.seq = seq;

  /* The 200ms rule (spec 10.1): a timer cancelled by the response, never a
     minimum display duration. */
  col.skelTimer = window.setTimeout(function () {
    if (col.seq === seq && col.status === 'loading') { col.skel = true; refresh(); }
  }, 200);

  var controller = ('AbortController' in window) ? new AbortController() : null;
  col.abort = controller;

  if (!state.scenarioId) { return; }
  Promise.resolve(variantPending).then(function () {
  return apiFetch('/scenario/' + encodeURIComponent(state.scenarioId) + '/portfolio', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: col.key, role: col.role }),
    signal: controller ? controller.signal : undefined
  }).then(function (body) {
    /* A response for a removed column is discarded silently; a stale one
       (the column was re-resolved since) likewise (spec 15.3). */
    if (state.columns.indexOf(col) < 0 || col.seq !== seq) return;
    if (col.skelTimer) { clearTimeout(col.skelTimer); col.skelTimer = null; }
    col.status = 'ready';
    col.data = body.portfolio;
    col.skel = false;
    settleRebuilding();
    if (col.role === 'base') { onBaseReady(); }
    announce('polite', headerName(col.key) + ' ready.');
    refresh();
  }).catch(function (err) {
    if (err && err.name === 'AbortError') return;
    if (state.columns.indexOf(col) < 0 || col.seq !== seq) return;
    if (col.skelTimer) { clearTimeout(col.skelTimer); col.skelTimer = null; }
    col.status = 'error';
    col.error = (err && err.message) || 'The portfolio could not be built.';
    settleRebuilding();
    announce('assertive', headerName(col.key) + ' could not be built. Retry available.');
    refresh();
  });
  });
}

function settleRebuilding() {
  if (state.rebuilding && !state.columns.some(function (c) { return c.status === 'loading'; })) {
    state.rebuilding = false;
  }
}

function slotsLeft() {
  var cap = opt('rules.maxPortfolios', 4) - 1;
  return cap - (state.columns.length - 1);
}

function addComparison(key) {
  if (!canEdit() || slotsLeft() <= 0 || !available(key) || used(key)) return false;
  var col = makeColumn(key, 'comparison');
  state.columns.push(col);
  App.lastAdded = state.columns.length - 1;
  startResolve(col);
  announce('polite', headerName(key) + ' added. '
    + (slotsLeft()) + ' slot' + (slotsLeft() === 1 ? '' : 's') + ' remaining.');
  refresh();
  document.querySelectorAll('.tblwrap').forEach(function (w) {
    w.scrollTo({ left: w.scrollWidth, behavior: 'smooth' });
  });
  window.setTimeout(function () { App.lastAdded = -1; refresh(); }, 1400);
  return true;
}

function removeComparison(index) {
  if (index <= 0 || index >= state.columns.length) return;
  var col = state.columns[index];
  abortColumn(col);                       /* works on a still-loading column */
  state.columns.splice(index, 1);
  deleteColumnOnServer(col.key);
  announce('polite', headerName(col.key) + ' removed. '
    + slotsLeft() + ' slot' + (slotsLeft() === 1 ? '' : 's') + ' remaining.');
  App.lastAdded = -1;
  refresh();
}

function deleteColumnOnServer(key) {
  if (!state.scenarioId) return Promise.resolve();
  return apiFetch('/scenario/' + encodeURIComponent(state.scenarioId)
    + '/portfolio/' + encodeURIComponent(keyStr(key)), { method: 'DELETE' })
    .catch(function (err) {
      if (err && err.message) showAlert('error', err.message);
    });
}

function retryColumn(index) {
  var col = state.columns[index];
  if (!col || col.status !== 'error') return;
  startResolve(col);
  refresh();
}

/* The same combination at the nearest risk level the schema does offer.
   Used when a base edit names something unavailable, and when a currency
   change leaves the base's own combination behind. */
function nearestAvailable(key) {
  var risks = opt('options.riskLevels', []);
  var holdsAllocation = !!key.allocationType;
  for (var i = 0; i < risks.length; i++) {
    /* A level that holds no allocation type is a different kind of book, not
       a nearer one: an all-equity level ignores the allocation entirely, so
       without this a withdrawn Core column fell all the way to All Equity -
       which holds no alternatives at all - and called it the nearest thing. */
    if (holdsAllocation === isAllEquityRisk(risks[i])) continue;
    var probe = buildKey(key.allocationType, key.excludeRealAssets, risks[i]);
    if (probe && available(probe)) return probe;
  }
  return null;
}

/* Changing the base replaces column one (spec 11.5). */
function setBase(key) {
  if (!schemaReady() || !canEdit()) return false;
  if (!available(key)) {
    var fallen = nearestAvailable(key);      /* the first risk level it offers */
    if (!fallen) return false;
    key = fallen;
  }
  var old = state.columns[0];
  if (old && keyEq(old.key, key)) return false;
  if (old) abortColumn(old);

  var col = makeColumn(key, 'base');
  /* Carry the outgoing base's figures onto the incoming one. A base edit
     replaces the column object rather than re-resolving it in place, so
     without this the new column has no data at all, drops out of
     shownColumns, and the table collapses to skeletons for the ~20ms the
     resolve takes - the whole section shrinking and springing back, which
     reads as the page flashing. Held only until the real figures land. */
  if (old && old.data) col.data = old.data;

  var rest = state.columns.slice(1).filter(function (c) {
    if (keyEq(c.key, key)) {
      abortColumn(c);
      /* No DELETE: recording a base drops a comparison that duplicates it, in
         the same write. Sending both raced - whichever arrived second lost,
         and if that was the DELETE the store refused it, because by then the
         key it named was the base. */
      announce('assertive', headerName(c.key)
        + ' was removed as a comparison because it is now the proposed portfolio.');
      return false;
    }
    return true;
  });
  state.columns = [col].concat(rest);
  App.lastAdded = -1;
  /* the library is resolved per base portfolio (D89): what a name offers
     under the new one is fetched again, and the choices re-checked against
     it once the base has resolved (onBaseReady) */
  state.sleeveLib = {};
  startResolve(col);
  refresh();
  return true;
}

/* Sleeves attach to the base's categories; anything that changes those
   categories invalidates them (spec 11.6). Runs when the base resolves. */
function onBaseReady() {
  var base = state.columns[0];
  if (!base || base.status !== 'ready') return;
  var have = {};
  var groups = opt('rules.sleeveGroups', []) || [];
  base.data.categories.forEach(function (c) {
    have[c.name] = 1;
    /* a grouped category's sleeve is chosen and kept under its group (D60):
       Private Equity and Other Private Assets hold one sleeve, stored under
       their group's name, which no base category carries - so without this
       every reload dropped the private-markets sleeve (D136) */
    groups.forEach(function (g) {
      if ((g.categories || []).indexOf(c.name) >= 0) have[g.name] = 1;
    });
  });
  var dropped = [];
  Object.keys(state.sleeves).forEach(function (category) {
    if (!have[category]) { dropped.push(category); delete state.sleeves[category]; }
  });
  if (dropped.length) {
    dropped.forEach(function (category) {
      announce('assertive', 'The ' + category + ' sleeve was removed because the '
        + 'base no longer holds ' + category + '.');
    });
    pushSleeves();
  }
  /* and a name the new portfolio has no edition of is dropped and named,
     the same as under a new basis (D89) */
  revalidateSleeves();
}

/* ---- scenario basis (spec 11.4): confirm before a full rebuild ---------- */
function requestBasisChange(field, value) {
  if (!canEdit()) return;
  var draftBasis = {
    currency: state.basisDraft ? state.basisDraft.currency : state.basis.currency,
    hedging: state.basisDraft ? state.basisDraft.hedging : state.basis.hedging
  };
  draftBasis[field] = value;
  if (draftBasis.currency === state.basis.currency
      && draftBasis.hedging === state.basis.hedging) {
    state.basisDraft = null; refresh(); return;
  }
  if (!state.columns.length) {
    /* nothing to rebuild: apply immediately */
    state.basis = draftBasis;
    state.basisDraft = null;
    persistBasis();
    fetchSchema();
    return;
  }
  state.basisDraft = draftBasis;   /* select updates optimistically; no request */
  refresh();
}

function cancelBasisChange() { state.basisDraft = null; refresh(); }

async function confirmBasisChange() {
  if (!state.basisDraft) return;
  state.basis = state.basisDraft;
  state.basisDraft = null;
  state.rebuilding = true;
  persistBasis();

  /* fetch the new schema first; unavailable combinations are dropped and
     named; survivors re-resolve keeping their combination (spec 11.4) */
  try {
    state.schema = await apiFetch('/scenario/schema' + schemaQuery());
    state.schemaStatus = 'ready';
  } catch (err) {
    state.schemaStatus = 'error';
    state.schemaError = err.message || String(err);
    state.rebuilding = false;
    refresh();
    return;
  }
  /* A key carries its currency, so every column has to be re-keyed into the
     new one before it can be asked whether it still exists. Asking with the
     OLD key - "USD|Moderate|Core|0" against the CHF availability set - never
     matched, so a currency change dropped every column, including the base;
     and removing the base is the one thing the store refuses, so the change
     ended in an error banner over an empty document. What it should do, and
     now does, is rebuild the same combinations in the new currency (11.4). */
  var moved = [];
  var lost = [];
  state.columns.forEach(function (col) {
    var key = buildKey(col.key.allocationType, col.key.excludeRealAssets, col.key.riskLevel);
    if (key && available(key)) moved.push({ col: col, key: key, was: col.key });
    else lost.push(col);
  });
  /* The base leads: if its combination is not offered in the new currency it
     falls to the nearest risk level that is, the way a base edit does (D54),
     rather than taking the comparisons down with it. */
  if (state.columns.length && (!moved.length || moved[0].col !== state.columns[0])) {
    var first = state.columns[0];
    var fallen = nearestAvailable(first.key);
    if (fallen) {
      moved.unshift({ col: first, key: fallen, was: first.key });
      lost = lost.filter(function (c) { return c !== first; });
      announce('assertive', headerName(first.key) + ' is not available in '
        + state.basis.currency + '; ' + headerName(fallen) + ' is proposed instead.');
    }
  }
  lost.forEach(function (col) {
    abortColumn(col);
    announce('assertive', headerName(col.key) + ' is not available in '
      + state.basis.currency + ' and has been removed.');
  });
  /* The stale keys go first, so recording the new ones cannot meet a full
     set of comparison slots - and never the base's, which the store replaces
     of its own accord when the new one is recorded. */
  var stale = state.columns.slice(1).map(function (col) { return col.key; });
  await Promise.all(stale.map(deleteColumnOnServer));
  moved.forEach(function (m) { m.col.key = m.key; });
  state.columns = moved.map(function (m) { return m.col; });
  state.columns.forEach(function (col) { startResolve(col); });
  settleRebuilding();                /* nothing survived: the spinner stops here */
  state.sleeveLib = {};              /* the library may differ under the new basis */
  state.customLib = {};              /* and a product's ticker and minimum (D180) */
  revalidateSleeves();
  refresh();
}

/* ---- the save barrier (D122, plan A1) -----------------------------------
   Several setters write the scenario and move on - the page stays live while
   the PUT lands. Exporting mid-flight would snapshot the store without the
   change the user just made, so every fire-and-forget write is tracked here
   and both exports wait for a quiet store before they POST. A failed save
   blocks the export with its own message, rather than delivering a file the
   screen does not show. */
var pendingWrites = [];
function trackWrite(promise) {
  pendingWrites.push(promise);
  var drop = function () {
    var at = pendingWrites.indexOf(promise);
    if (at !== -1) pendingWrites.splice(at, 1);
  };
  promise.then(drop, drop);
  return promise;
}
function writesSettled() {
  /* settle everything in flight NOW, then look again: a write queued while
     waiting is caught by the recursion, so the barrier closes only on a
     store with nothing left to land */
  if (!pendingWrites.length) return Promise.resolve();
  return Promise.allSettled(pendingWrites.slice()).then(function (states) {
    if (states.some(function (one) { return one.status === 'rejected'; })) {
      throw new Error('Your latest changes could not be saved.');
    }
    return writesSettled();
  });
}

function persistBasis() {
  if (!state.scenarioId) return;
  trackWrite(apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ basis: state.basis })
  })).catch(function (err) { showAlert('error', err.message || String(err)); });
}

/* Sleeves kept on a basis or base change, re-validated against the library
   for the portfolio now in force; any no longer offered are dropped and
   named (spec 11.6, D89). */
function revalidateSleeves() {
  Object.keys(state.sleeves).forEach(function (category) {
    ensureSleeveLib(category, function (lib) {
      var name = state.sleeves[category];
      if (!name) return;
      var stillOffered = (lib.sleeves || []).some(function (s) { return s.name === name; });
      if (!stillOffered) {
        delete state.sleeves[category];
        announce('assertive', 'The ' + category + ' sleeve "' + name
          + '" is not offered for this portfolio and has been removed.');
        pushSleeves();
        refresh();
      }
    });
  });
}

/* ---- sleeves state ------------------------------------------------------
   A failed library stays failed until the user retries - refetching it on
   every render cycle would loop forever and mask the error state. Callers
   render the freshly-set loading state themselves; the async settle calls
   refresh(). */
function ensureSleeveLib(category, onReady) {
  if (!state.variant) return;     /* nothing to list until a variant is chosen */
  var entry = state.sleeveLib[category];
  if (entry) {
    if (entry.status === 'ready' && onReady) onReady(entry);
    return;                       /* ready, loading OR error: nothing to start */
  }
  state.sleeveLib[category] = { status: 'loading', sleeves: [], error: null };
  /* The base portfolio goes with the request (D89): a sleeve name may hold
     several editions in the library and the server serves the one for this
     book. Nothing in the reply says which - the page lists names. */
  var base = state.columns[0] || null;
  apiFetch('/scenario/sleeves?category=' + encodeURIComponent(category)
      + '&variant=' + encodeURIComponent(state.variant)
      + '&currency=' + encodeURIComponent(state.basis.currency)
      + '&hedging=' + encodeURIComponent(state.basis.hedging)
      + (base ? '&key=' + encodeURIComponent(keyStr(base.key)) : '') + entityQuery())
    .then(function (body) {
      state.sleeveLib[category] = { status: 'ready', sleeves: body.sleeves || [], error: null };
      if (onReady) onReady(state.sleeveLib[category]);
      refresh();
    })
    .catch(function (err) {
      state.sleeveLib[category] = {
        status: 'error', sleeves: [],
        error: (err && err.message) || 'Could not load sleeves'
      };
      refresh();
    });
}

/* The variant decides which sleeves exist and what they contain, so changing
   it invalidates every cached library and every choice made from one. Both go
   in the same turn as the PUT, which clears the server's map too (D29). */
async function setVariant(name, entity) {
  if (!canEdit()) return;
  if (!name || name === state.variant) return;
  /* A type that asks for a custodian (D195) is chosen without one: its card
     opens on the way to the implementation step, where the custodian first
     matters. Chosen on that step, the type asks for it first, on the card;
     the card comes back here with the choice, and closing it keeps the type
     in force. */
  if (custodyNeeded(name) && entity === undefined) {
    if (!custodian() && state.step === 'impl') {
      openCustodyDialog(name, document.getElementById('bpv'));
      return;
    }
    entity = custodian();
  }
  state.mandate = Object.assign({}, state.mandate, { entity: custodyNeeded(name) ? entity || '' : '' });
  var had = Object.keys(state.sleeves).length + Object.keys(state.customSleeves).length;
  var hadBase = state.columns[0] || null;
  state.variant = name;
  state.sleeveLib = {};
  state.sleeves = {};
  /* a custom sleeve holds the old type's products: it goes with the rest (D170) */
  state.customSleeves = {};
  state.customLib = {};
  /* a custom portfolio is kept through a new type (D171); what it is built
     from is the new type's */
  state.cpLib = { key: null, status: 'idle', data: null, error: null };
  state.cpConfirm = null;
  /* The base draft was assembled against the previous variant's allocations,
     so a half-made selection is discarded rather than silently carried into a
     variant that may not offer it. */
  state.baseDraft = { allocationType: '', excludeRealAssets: false, riskLevel: '' };
  refresh();

  /* Persist FIRST, and wait for it. The server validates every resolve against
     the variant it has stored (D49), so anything issued while this is in
     flight is judged against a scenario that does not know the variant yet and
     comes back 422. Ordering this after the schema fetch left a whole extra
     round trip in which a fast selection could do exactly that. */
  if (state.scenarioId) {
    /* the custodian goes with a type that asks for one; the server clears
       it under any other */
    var write = custodyNeeded(name) ? { variant: name, mandate: state.mandate } : { variant: name };
    variantPending = trackWrite(apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(write)
    })).then(function (body) {
      /* an approval of a custom portfolio was of the old type (D171) */
      if (body && 'customPortfolio' in body) state.customPortfolio = body.customPortfolio;
    }).catch(function (err) { showAlert('error', err.message || String(err)); });
    await variantPending;
    variantPending = null;
  }

  /* The variant narrows options.allocations and the availability set, so the
     schema is re-fetched before anything is judged against it (D49). */
  try {
    state.schema = await apiFetch('/scenario/schema' + schemaQuery());
  } catch (err) {
    showAlert('error', err.message || String(err));
    return;
  }
  pruneUnavailableColumns();
  /* The base is column one and the comparisons are read against it. If the new
     variant cannot build the base, the whole set goes rather than leaving
     comparisons anchored to nothing - the same rule the store applies, so the
     two never diverge. */
  if (hadBase && state.columns.indexOf(hadBase) === -1 && state.columns.length) {
    state.columns.slice().forEach(function (col, i) {
      abortColumn(col);
      if (i > 0) deleteColumnOnServer(col.key);      /* never the base (D102) */
    });
    state.columns = [];
  }
  announce('assertive', had
    ? 'Implementation type set to ' + name + '. ' + had
      + ' sleeve choice' + (had === 1 ? '' : 's') + ' cleared - the library has changed.'
    : 'Implementation type set to ' + name + '.');
  refresh();
}

/* The tilt is an implementation choice: it moves weight between the
   implemented categories and never re-resolves the strategic portfolio, so
   nothing here touches the analytics or the availability set (D50). */
function setTacticalTilt(on) {
  if (!canEdit()) return;
  on = !!on;
  if (on === state.tacticalTilt) return;
  state.tacticalTilt = on;
  if (state.scenarioId) {
    trackWrite(apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tacticalTilt: on })
    })).catch(function (err) { showAlert('error', err.message || String(err)); });
  }
  var tiltRule = overlayRule('tacticalTilt');
  announce('polite', on
    ? (tiltRule ? tiltRule.name : 'Tactical tilt') + ' added: '
      + (tiltRule ? overlayWords(tiltRule) : '') + '.'
    : (tiltRule ? tiltRule.name : 'Tactical tilt') + ' removed.');
  refresh();
}

/* ---- pricing (D51) -------------------------------------------------------
   The schedule and the level are stored on the scenario and validated
   server-side against the same lists the schema serves. Neither touches the
   analytics: they re-price the implementation sheet and nothing else. */
function feeScheduleIds() {
  return opt('fees.schedules', []).map(function (s) { return s.id; });
}

function feeLevelIds() {
  var ids = opt('fees.levels', []).map(function (l) { return l.id; });
  return customLevel() ? ids.concat([customLevel()]) : ids;
}

/* The custom level (D96): the schema names it, the page never does. Under it
   the rates are the PWA's own, one per row of the schedule chosen, and they
   live in state.customFees as {schedule: rate | {feeGroup: rate}}. */
function customLevel() { return opt('fees.customLevel', null); }

function setCustomFees(map) {
  if (!canEdit()) return;
  state.customFees = map || {};
  pushFee({ customFees: state.customFees });
  announce('polite', 'Custom fee rates applied.');
  refresh();
}

/* The charges the Fees Summary slide quotes beside the fees (D177): the PWA's
   for this proposal, saved with the scenario like the custom rates. */
function setOtherCharges(map) {
  if (!canEdit()) return;
  state.otherCharges = map || {};
  pushFee({ otherCharges: state.otherCharges });
  announce('polite', 'Other charges applied.');
  refresh();
}

function feeLevel() {
  return state.feeLevel || opt('fees.defaultLevel', null);
}

/* A level a row of the card does not price is moved to the same point on
   Management (D185). The implementation layer, which knows the products
   held, says when; this keeps the scenario saying what priced it. No refresh: the page
   has already drawn the level in force. */
function adoptFeeLevel(level, why) {
  if (!canEdit() || !level || level === feeLevel()) return;
  if (feeLevelIds().indexOf(level) === -1) return;
  state.feeLevel = level;
  pushFee({ feeLevel: level });
  announce('polite', 'Fee level ' + level + (why ? ': ' + why : '.'));
}

function pushFee(patch) {
  if (!state.scenarioId) return;
  trackWrite(apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch)
  })).catch(function (err) { showAlert('error', err.message || String(err)); });
}

/* The strategic volatility premium (D53). Same shape as the tilt - an
   implementation overlay that moves weight and re-resolves nothing - with one
   extra gate: the product is forbidden outside the currencies the schema
   lists, so a book in any other currency cannot turn it on. The rule is
   applied server-side too; this is the half that keeps it off the screen. */
function volPremiumCurrencies() {
  var rule = overlayRule('volPremium');
  return rule ? (rule.currencies || []) : [];
}

/* the premium's rule names the currencies it may be held in (D155); a rule
   that names none may be held in any, and no rule means no premium */
function canHoldVolPremium() {
  var rule = overlayRule('volPremium');
  if (!rule) return false;
  var currencies = rule.currencies || [];
  return !currencies.length || currencies.indexOf(state.basis.currency) >= 0;
}

function setVolPremium(on) {
  if (!canEdit()) return;
  on = !!on;
  if (!canHoldVolPremium()) return;
  if (on === state.volPremium) return;
  state.volPremium = on;
  if (state.scenarioId) {
    trackWrite(apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ volPremium: on })
    })).catch(function (err) { showAlert('error', err.message || String(err)); });
  }
  var premiumRule = overlayRule('volPremium');
  var premiumName = premiumRule ? premiumRule.name : 'Strategic Volatility Premium';
  announce('polite', on
    ? premiumName + ' added: ' + (premiumRule ? overlayWords(premiumRule) : '') + '.'
    : premiumName + ' removed.');
  refresh();
}

/* The include-fees toggle (D52). It carries the whole pricing question: with
   it off the rail's schedule and level are not rendered and the table drops
   its three fee columns, so a proposal that does not discuss fees never shows
   an empty fee column or an unanswered control.

   Turning it OFF is animated by the caller BEFORE the state moves - the
   columns have to fade out of a DOM that still contains them - so this takes
   the commit as a continuation rather than assuming it can run immediately.
   Turning it on needs none of that: the new cells animate themselves in. */
function setIncludeFees(on) {
  if (!canEdit()) return;
  on = !!on;
  if (on === state.includeFees) return;
  state.includeFees = on;
  pushFee({ includeFees: on });
  announce('polite', on
    ? 'Fees included. ' + (state.feeSchedule
        ? 'Priced ' + state.feeSchedule + ', ' + feeLevel() + '.'
        : 'Choose a fee schedule to price the model.')
    : 'Fees excluded from the proposal.');
  refresh();
}

function setFeeSchedule(name) {
  if (!canEdit()) return;
  if (feeScheduleIds().indexOf(name) === -1) return;
  if (name === state.feeSchedule) return;
  state.feeSchedule = name;
  pushFee({ feeSchedule: name });
  var entry = opt('fees.schedules', []).filter(function (s) { return s.id === name; })[0];
  announce('polite', 'Fee schedule ' + name + (entry && entry.note ? ': ' + entry.note : '.'));
  refresh();
}

function setFeeLevel(level) {
  if (!canEdit()) return;
  if (feeLevelIds().indexOf(level) === -1) return;
  if (level === feeLevel()) return;
  state.feeLevel = level;
  pushFee({ feeLevel: level });
  announce('polite', 'Fee level ' + level + '.');
  refresh();
}

function chooseSleeve(category, name) {
  if (!canEdit()) return;
  /* a sleeve holding a hedge fund asks for the notice first (D198) */
  if (name) {
    var lib = state.sleeveLib[category];
    var picked = lib && lib.status === 'ready'
      ? lib.sleeves.filter(function (s) { return s.name === name; })[0] : null;
    if (noticeGate(category, name, picked ? picked.products : [], function () { attachSleeve(category, name); })) return;
  }
  attachSleeve(category, name);
}

function attachSleeve(category, name) {
  if (name) state.sleeves[category] = name; else delete state.sleeves[category];
  pushSleeves();
  refresh();
}

function pushSleeves() {
  if (!state.scenarioId) return;
  trackWrite(apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sleeves: state.sleeves })
  })).catch(function (err) { showAlert('error', err.message || String(err)); });
}

/* ---- custom sleeves (D170) -----------------------------------------------
   A custom sleeve is in force for a category when no library sleeve is
   chosen there. Choosing a library sleeve keeps the custom one, unused, so
   choosing it again brings it back as it was. */
function ensureCustomLib(category, onReady) {
  if (!state.variant) return;
  var entry = state.customLib[category];
  if (entry) {
    if (entry.status === 'ready' && onReady) onReady(entry);
    return;
  }
  state.customLib[category] = { status: 'loading', products: [], mayBuild: false, error: null };
  /* the currency goes too: a product's ticker and minimum are its own in
     each (D180) */
  apiFetch('/scenario/products?category=' + encodeURIComponent(category)
      + '&variant=' + encodeURIComponent(state.variant)
      + '&currency=' + encodeURIComponent(state.basis.currency) + entityQuery())
    .then(function (body) {
      state.customLib[category] = { status: 'ready', products: body.products || [],
                                    mayBuild: !!body.mayBuild, error: null };
      if (onReady) onReady(state.customLib[category]);
      refresh();
    })
    .catch(function (err) {
      state.customLib[category] = { status: 'error', products: [], mayBuild: false,
                                    error: (err && err.message) || 'Could not load the products' };
      refresh();
    });
}

/* Save the custom sleeves and the choices together, in one write; the
   server's answer - each sleeve as it checked and keeps it - replaces ours.
   Resolves to null, or to the server's refusal as a message. */
function pushCustomSleeves() {
  if (!state.scenarioId) return Promise.resolve(null);
  return trackWrite(apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sleeves: state.sleeves, customSleeves: state.customSleeves })
  })).then(function (body) {
    if (body && body.customSleeves) state.customSleeves = body.customSleeves;
    refresh();
    return null;
  }, function (err) { return (err && err.message) || String(err); });
}

/* *definition* null discards the category's custom sleeve. Given, it is put
   in force: the library choice there is cleared. On a refusal nothing
   changes here and the message comes back. */
async function setCustomSleeve(category, definition) {
  if (!canEdit()) return 'Your role cannot change this proposal.';
  /* a custom sleeve holding a hedge fund asks for the notice first (D198);
     declined, the builder stays open and says why */
  if (definition) {
    var asked = await new Promise(function (resolve) {
      var gated = noticeGate(category, customSleeveName(definition), customProductsOf(category, definition),
        function () { resolve(null); },
        function () { resolve('The notice was not confirmed, so this sleeve is not in force.'); });
      if (!gated) resolve(null);
    });
    if (asked) return asked;
  }
  return putCustomSleeve(category, definition);
}

async function putCustomSleeve(category, definition) {
  var before = { sleeves: Object.assign({}, state.sleeves),
                 custom: Object.assign({}, state.customSleeves) };
  if (definition) {
    state.customSleeves[category] = definition;
    delete state.sleeves[category];
  } else {
    delete state.customSleeves[category];
  }
  var refused = await pushCustomSleeves();
  if (refused) {
    state.sleeves = before.sleeves; state.customSleeves = before.custom;
    refresh();
  }
  return refused;
}

/* Put a held custom sleeve back in force, in place of the library's. */
function useCustomSleeve(category) {
  if (!canEdit() || !state.customSleeves[category]) return;
  var held = state.customSleeves[category];
  if (noticeGate(category, customSleeveName(held), customProductsOf(category, held), function () { putBackCustom(category); })) return;
  putBackCustom(category);
}

function putBackCustom(category) {
  delete state.sleeves[category];
  pushSleeves();
  refresh();
}

/* a custom sleeve's products as the library serves them, where the
   category's list is read; the ones it no longer has are left out */
function customProductsOf(category, definition) {
  var lib = state.customLib[category];
  if (!lib || lib.status !== 'ready') return [];
  var byId = {};
  (lib.products || []).forEach(function (p) { byId[p.productId] = p; });
  return ((definition && definition.products) || []).map(function (p) { return byId[p.productId]; }).filter(Boolean);
}
/* the name a custom sleeve goes by on the rail (D170) */
function customSleeveName(definition) {
  var name = String((definition && definition.name) || '').trim();
  return name ? 'Custom: ' + name : 'Custom';
}

/* ---- custom portfolios (D171) --------------------------------------------
   A proposal is strategic or custom. A custom one is chosen as "Custom" in
   the Allocation selector and built on step 2 from products and weights. The
   strategic base and its sleeves are kept while it is, unused, and the base
   follows the custom portfolio's risk profile: it becomes the profile's
   anchor, the book the limits are read from and the first comparison beside
   the custom column on step 1. */
function cpActive() { return !!(state.customPortfolio && state.customPortfolio.active); }

function mayBuildPortfolio() {
  return canEdit() && !!state.variant
    && (opt('customPortfolio.mayBuild', []) || []).indexOf(state.variant) !== -1;
}

function ensureCpLib() {
  if (!state.variant || !state.basisChosen) return;
  var key = [state.variant, state.basis.currency, state.basis.hedging,
             custodian()].join('|');
  if (state.cpLib.key === key && state.cpLib.status !== 'idle') return;
  state.cpLib = { key: key, status: 'loading', data: null, error: null };
  apiFetch('/scenario/custom-portfolio?variant=' + encodeURIComponent(state.variant)
      + '&currency=' + encodeURIComponent(state.basis.currency)
      + '&hedging=' + encodeURIComponent(state.basis.hedging) + entityQuery())
    .then(function (body) {
      if (state.cpLib.key !== key) return;
      state.cpLib = { key: key, status: 'ready', data: body, error: null };
      refresh();
    })
    .catch(function (err) {
      if (state.cpLib.key !== key) return;
      state.cpLib = { key: key, status: 'error', data: null,
                      error: (err && err.message) || 'Could not load the products' };
      refresh();
    });
}

/* Save the build - its kind, profile and products - in one write. The
   server's answer, as it checked and keeps it, replaces ours unless a later
   save has gone since. Resolves to null, or to the refusal as a message,
   having put back what was there. */
var cpSeq = 0;
function pushCustomPortfolio(change) {
  if (!state.scenarioId) return Promise.resolve(null);
  var before = state.customPortfolio;
  var next = Object.assign({ active: false, profile: null, products: [] }, before || {}, change || {});
  state.customPortfolio = next;
  var seq = ++cpSeq;
  refresh();
  return trackWrite(apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ customPortfolio: Object.assign({ active: !!next.active, profile: next.profile,
                                                            products: next.products || [] },
                                                          /* the benchmark only when it is chosen (D211) */
                                                          'benchmark' in (change || {}) ? { benchmark: next.benchmark || '' } : {}) })
  })).then(function (body) {
    if (seq === cpSeq && body && body.customPortfolio) state.customPortfolio = body.customPortfolio;
    refresh();
    return null;
  }, function (err) {
    if (seq === cpSeq) state.customPortfolio = before;
    refresh();
    return (err && err.message) || String(err);
  });
}

/* Submit, withdraw, approve or send back (D171), once every save has
   landed; the answer replaces ours. */
function cpApproval(action, note) {
  if (!state.scenarioId) return Promise.resolve(null);
  return writesSettled().then(function () {
    return trackWrite(apiFetch('/scenario/' + encodeURIComponent(state.scenarioId)
        + '/custom-portfolio/approval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: action, note: note || '' })
    }));
  }).then(function (body) {
    if (body && 'customPortfolio' in body) state.customPortfolio = body.customPortfolio;
    refresh();
    return null;
  }, function (err) { refresh(); return (err && err.message) || String(err); });
}

/* the risk level the rail stands on: the custom portfolio's profile, the
   built base's, or the draft's */
function railRisk() {
  if (cpActive() && state.customPortfolio.profile) return state.customPortfolio.profile;
  var base = state.columns[0] || null;
  var drafting = !!(state.baseDraft.allocationType || state.baseDraft.riskLevel);
  return (base && !drafting) ? base.key.riskLevel : state.baseDraft.riskLevel;
}

function keyFromStr(text) {
  var p = String(text || '').split('|');
  if (p.length !== 4) return null;
  return { currency: p[0], riskLevel: p[1], allocationType: p[2] === 'NA' ? null : p[2],
           excludeRealAssets: p[3] === 'NA' ? null : p[3] === '1' };
}

/* The base follows a custom portfolio's profile to its anchor. Tried once
   per anchor, so a book the availability set refuses is not chased. */
var anchorTried = null;
function syncAnchorBase() {
  if (!cpActive() || state.cpLib.status !== 'ready' || !canEdit()) return;
  var profile = ((state.cpLib.data || {}).profiles || {})[state.customPortfolio.profile];
  if (!profile || anchorTried === profile.key) return;
  anchorTried = profile.key;
  var key = keyFromStr(profile.key), base = state.columns[0] || null;
  if (key && !(base && keyEq(base.key, key))) setBase(key);
}

async function becomeCustom() {
  var risk = railRisk();
  state.cpConfirm = null;
  if (!risk || !mayBuildPortfolio()) { refresh(); return; }
  var held = state.customPortfolio || {};
  var refused = await pushCustomPortfolio({ active: true, profile: risk, products: held.products || [] });
  if (refused) { showAlert('error', refused); return; }
  state.baseDraft = { allocationType: '', excludeRealAssets: false, riskLevel: '' };
  anchorTried = null;
  ensureCpLib();
  announce('assertive', (held.products || []).length
    ? 'A custom portfolio: the build as it was left, on the Implementation step.'
    : 'A custom portfolio: Implementation opens on a blank slate.');
  App.setStep('impl');
}

async function becomeStrategic(allocationType) {
  var risk = railRisk();
  state.cpConfirm = null;
  var refused = await pushCustomPortfolio({ active: false });
  if (refused) { showAlert('error', refused); return; }
  var exRA = !!(allocationType && reAllowed(allocationType) && variantForcesExcludeRE(state.variant));
  var key = buildKey(allocationType, exRA, risk);
  var base = state.columns[0] || null;
  if (key && !(base && keyEq(base.key, key))) setBase(key);
  announce('assertive', 'A strategic proposal again. The custom build is kept: choosing Custom brings it back.');
  refresh();
}

function setCpProfile(risk) {
  if (!cpActive() || !risk || risk === state.customPortfolio.profile) return;
  anchorTried = null;
  pushCustomPortfolio({ profile: risk }).then(function (refused) {
    if (refused) showAlert('error', refused);
    else announce('polite', 'Risk profile ' + riskLabel(risk) + ': the limits changed; the build did not.');
  });
}

function cpConfirmHtml() {
  var pend = state.cpConfirm;
  if (!pend) return '';
  if (pend.to === 'custom') {
    var n = ((state.customPortfolio || {}).products || []).length;
    return '<div class="cp-confirm" role="group" aria-labelledby="cpconfirmh">'
      + '<b id="cpconfirmh">A custom portfolio</b>'
      + '<p>The proposal becomes a custom one, built product by product, with '
      + esc(riskLabel(railRisk())) + ' as its risk profile. The sleeves chosen are kept, unused: '
      + 'choosing a strategic allocation again brings them back. '
      + (n ? 'The custom build of ' + n + ' product' + (n === 1 ? '' : 's') + ' comes back as it was left.'
           : 'Implementation opens on a blank slate.') + '</p>'
      + '<div class="cp-confirm-btns"><button type="button" class="btn btn-ghost" data-cpconfirm="no">Cancel</button>'
      + '<button type="button" class="btn btn-primary" data-cpconfirm="yes" id="cpconfirmyes">'
      + (n ? 'Make it custom' : 'Start building') + '</button></div></div>';
  }
  return '<div class="cp-confirm" role="group" aria-labelledby="cpconfirmh">'
    + '<b id="cpconfirmh">Back to a strategic allocation</b>'
    + '<p>The proposal becomes ' + esc(riskLabel(railRisk())) + ' ' + esc(pend.allocationType || '')
    + ' again, with the sleeves chosen for it. The custom build is kept: choosing Custom brings it back as it is.</p>'
    + '<div class="cp-confirm-btns"><button type="button" class="btn btn-ghost" data-cpconfirm="no">Keep it custom</button>'
    + '<button type="button" class="btn btn-primary" data-cpconfirm="yes" id="cpconfirmyes">Use '
    + esc(pend.allocationType || 'it') + '</button></div></div>';
}

/* =============================================================================
   Mandate dialog (spec 7.1). Validation on blur and submit, never per key.
   ========================================================================== */
function openMandateDialog(opener) {
  if (!canEdit()) return;
  dlgOpener = opener || null;
  draft = {
    top: state.mandate ? state.mandate.topAccountSize : null,
    size: state.mandate ? state.mandate.mandateSize : null,
    pwa: state.mandate ? state.mandate.primaryPwa : '',
    q: state.mandate ? state.mandate.primaryPwa : '',
    open: false, idx: -1, err: null, dirty: false,
    matches: null,                    /* null = below threshold */
    searching: false, searchTimer: null, searchAbort: null,
    saving: false
  };
  renderDialog();
  var first = document.getElementById('mdtop');
  if (first) first.focus();
}

function closeMandateDialog() {
  if (draft && draft.searchTimer) clearTimeout(draft.searchTimer);
  if (draft && draft.searchAbort) { try { draft.searchAbort.abort(); } catch (e) {} }
  draft = null;
  renderDialog();
  if (dlgOpener && dlgOpener.isConnected) { dlgOpener.focus(); }
  else {
    var start = document.getElementById('startbtn');
    if (start && !start.closest('[hidden]')) start.focus();
  }
}

function scheduleAdvisorSearch() {
  if (draft.searchTimer) clearTimeout(draft.searchTimer);
  var query = (draft.q || '').trim();
  if (query.length < 2) {
    draft.matches = null; draft.searching = false;
    preserveFocus(renderDialog);
    return;
  }
  draft.searching = true;
  preserveFocus(renderDialog);        /* keep the caret while re-rendering */
  draft.searchTimer = window.setTimeout(function () {   /* debounce 250ms */
    if (!draft) return;
    if (draft.searchAbort) { try { draft.searchAbort.abort(); } catch (e) {} }
    var controller = ('AbortController' in window) ? new AbortController() : null;
    draft.searchAbort = controller;
    apiFetch('/scenario/advisors?q=' + encodeURIComponent(query),
             { signal: controller ? controller.signal : undefined })
      .then(function (body) {
        if (!draft) return;
        draft.matches = (body.advisors || []).map(function (a) { return a.display; });
        draft.searching = false;
        preserveFocus(renderDialog);
      })
      .catch(function (err) {
        if (!draft || (err && err.name === 'AbortError')) return;
        draft.matches = [];
        draft.searching = false;
        preserveFocus(renderDialog);
      });
  }, 250);
}

function refocusCombo() {
  var field = document.getElementById('mdpwa');
  if (field && document.activeElement !== field) {
    var pos = field.value.length;
    field.focus();
    try { field.setSelectionRange(pos, pos); } catch (e) {}
  }
}

function validateDraft() {
  var floor = opt('rules.mandateFloor', 5000000);
  if (!(draft.top > 0)) return { field: 'mdtop', msg: 'Enter the top account size.' };
  if (!(draft.size >= floor)) {
    return { field: 'mdsize',
             msg: 'Mandate size must be at least ' + money(floor) + '.' };
  }
  if (draft.size > draft.top) {
    return { field: 'mdsize', msg: 'Mandate size cannot exceed the top account size.' };
  }
  if (!draft.pwa) return { field: 'mdpwa', msg: 'Choose a Primary PWA from the list.' };
  return null;
}

async function commitMandate() {
  if (!draft || draft.saving) return;
  var problem = validateDraft();
  if (problem) {
    draft.err = problem;
    renderDialog();
    var bad = document.getElementById(problem.field);
    if (bad) bad.focus();
    return;
  }
  draft.saving = true;
  renderDialog();
  /* the custodian is not the first card's (D195): it is asked for with a
     type that needs one, and a write without it keeps the one held */
  var mandate = { topAccountSize: draft.top, mandateSize: draft.size, primaryPwa: draft.pwa };
  try {
    if (!state.scenarioId) {
      var created = await apiFetch('/scenario', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mandate: mandate, basis: state.basis })
      });
      state.scenarioId = created.id;
      /* the server sets the prescribed fee level on a new scenario, and
         leaves fees excluded until they are asked for (D52) */
      state.includeFees = !!(created.scenario && created.scenario.includeFees);
      state.volPremium = !(created.scenario && created.scenario.volPremium === false);
      state.feeSchedule = (created.scenario && created.scenario.feeSchedule) || null;
      state.feeLevel = (created.scenario && created.scenario.feeLevel) || null;
      state.customFees = (created.scenario && created.scenario.customFees) || {};
      state.otherCharges = (created.scenario && created.scenario.otherCharges) || {};
      try {
        history.replaceState(null, '', '?scenario=' + encodeURIComponent(created.id));
      } catch (e) { /* file:// review mode */ }
    } else {
      await apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mandate: mandate })
      });
    }
  } catch (err) {
    draft.saving = false;
    var field = err && err.body && err.body.field;
    var map = { topAccountSize: 'mdtop', mandateSize: 'mdsize', primaryPwa: 'mdpwa' };
    draft.err = { field: map[field] || 'mdsize', msg: (err && err.message) || 'Could not save.' };
    renderDialog();
    var focusTo = document.getElementById(draft.err.field);
    if (focusTo) focusTo.focus();
    return;
  }
  state.mandate = Object.assign(mandate, { entity: state.mandate ? state.mandate.entity || '' : '' });
  var wasLanding = (state.phase === 'landing');
  state.phase = 'workspace';
  draft = null;
  /* mandate size gates the $20m rule: refetch the schema; any column now
     invalid is dropped with an announcement (spec 11.2) */
  await fetchSchema();
  if (wasLanding) guideFocus = true;
  refresh();
}

/* =============================================================================
   Boot and rehydrate (spec 11.1, 11.8)
   ========================================================================== */
async function boot() {
  var params = null;
  try { params = new URLSearchParams(window.location.search); } catch (e) {}
  var wanted = params ? params.get('scenario') : null;

  refresh();                              /* landing paints immediately */
  state.schemaStatus = 'loading';
  try {
    state.schema = await apiFetch('/scenario/schema' + schemaQuery());
    state.schemaStatus = 'ready';
  } catch (err) {
    state.schemaError = err.message || String(err);
    /* The service is not answering. If the last good render of the proposal
       being asked for is cached, show it read only rather than showing
       nothing (D64, option F); with nothing cached there is no proposal to
       fall back to and the page says so instead (option G). */
    var snap = (state.service === 'down') ? loadSnapshot(wanted) : null;
    if (snap) {
      restoreSnapshot(snap);
      announce('polite', 'Showing the last version loaded. The page is read only '
        + 'until the scenario service is back.');
    } else {
      state.schemaStatus = 'error';
    }
    refresh();
    return;
  }

  if (wanted) {
    try {
      var stored = await apiFetch('/scenario/' + encodeURIComponent(wanted));
      state.scenarioId = stored.id;
      state.mandate = stored.mandate;
      state.basis = stored.basis;
      state.variant = stored.variant || null;
      /* absent means a scenario stored before the field existed, which
         takes the default; only an explicit false turns it off */
      state.tacticalTilt = stored.tacticalTilt !== false;
      /* absent means a scenario stored before the field existed, which takes
         the default; only an explicit false turns it off. Never carried into
         a currency that cannot hold it - App.volPremium() sees to that. */
      state.volPremium = stored.volPremium !== false;
      state.feeSchedule = stored.feeSchedule || null;
      state.feeLevel = stored.feeLevel || null;
      state.customFees = stored.customFees || {};
      state.otherCharges = stored.otherCharges || {};
      /* A scenario stored before the toggle existed has no includeFees, and
         the server fills it from whether a schedule was ever chosen: a
         proposal already priced keeps showing its fees (D52). */
      state.includeFees = !!stored.includeFees;
      /* a scenario with columns has already been through the basis step */
      state.basisChosen = !!(stored.base || (stored.comparisons || []).length);
      state.sleeves = stored.sleeves || {};
      state.customSleeves = stored.customSleeves || {};
      state.customPortfolio = stored.customPortfolio || null;
      state.notices = stored.notices || {};
      state.phase = 'workspace';
      await fetchSchema();                /* now with the mandate size */
      var keys = [];
      if (stored.base) keys.push({ str: stored.base, role: 'base' });
      (stored.comparisons || []).forEach(function (k) {
        keys.push({ str: k, role: 'comparison' });
      });
      keys.forEach(function (entry) {
        var parts = entry.str.split('|');
        var na = parts[2] === 'NA';
        var key = { currency: parts[0], riskLevel: parts[1],
                    allocationType: na ? null : parts[2],
                    excludeRealAssets: na ? null : parts[3] === '1' };
        var col = makeColumn(key, entry.role);
        state.columns.push(col);
        startResolve(col);                /* columns come back loading (spec 11.8) */
      });
    } catch (err) {
      /* scenario gone: back to the landing with an explanation (spec 3.5) */
      showAlert('info', (err && err.message)
        || 'That scenario is no longer available. Start a new one.');
      try { history.replaceState(null, '', window.location.pathname); } catch (e) {}
    }
  }
  refresh();
}

/* =============================================================================
   Renders. Direct DOM writes, focus preserved by the refresh wrapper.
   ========================================================================== */
function renderPhase() {
  var landing = document.getElementById('view-landing');
  var schemaError = document.getElementById('view-schema-error');
  var aa = document.getElementById('view-aa');
  var impl = document.getElementById('view-impl-wrap');
  var steps = document.getElementById('steps');
  /* Nothing to show at all - not merely a failed call. With a schema in hand
     the workspace or the landing stays up and the degraded banner carries the
     message instead (D64). */
  var broken = (state.schemaStatus === 'error') && !state.schema;
  if (schemaError) {
    schemaError.hidden = !broken;
    var reason = document.getElementById('schema-error-reason');
    if (reason) reason.textContent = state.schemaError || '';
  }
  if (landing) landing.hidden = broken || (state.phase !== 'landing');
  if (steps) steps.hidden = broken || (state.phase === 'landing');
  if (broken || state.phase === 'landing') {
    if (aa) aa.hidden = true;
    if (impl) impl.hidden = true;
  }
  /* the rail is not drawn when the schema failed - its options come from it */
  document.body.classList.toggle('phase-landing', broken || state.phase === 'landing');
}

function renderMandate() {
  var el = document.getElementById('tier-mandate'); if (!el) return;
  if (!state.mandate) { el.innerHTML = ''; return; }
  el.innerHTML = '<div class="tier-h"><h3>Mandate</h3>'
    + '<button type="button" class="tier-edit" id="mdedit"' + (canEdit() ? '' : ' disabled') + '>Edit</button></div>'
    + '<dl class="summary md-kv">'
    + '<dt>Top Account:</dt><dd>' + money(state.mandate.topAccountSize) + '</dd>'
    + '<dt>Mandate Size:</dt><dd>' + money(state.mandate.mandateSize) + '</dd>'
    + '<dt>Primary PWA:</dt><dd>' + esc(state.mandate.primaryPwa) + '</dd>'
    + custodyRowHtml()
    + '</dl>';
}

function renderBasis() {
  var el = document.getElementById('tier-basis'); if (!el) return;
  var showing = state.basisDraft || state.basis;
  var disabled = !schemaReady() || !canEdit() ? ' disabled' : '';
  /* Until each is answered its select shows a placeholder rather than a
     default, and the guide marks whichever is still open as next (D91, D93). The scenario does hold a basis - it has
     to, the schema is fetched against one - but presenting that as a chosen
     answer invites the PWA to skip a decision the whole comparison rests on. */
  var pendingCcy = !state.basisChosen && !state.basisAnswered.currency;
  var pendingHedge = !state.basisChosen && !state.basisAnswered.hedging;
  el.className = 'tier';
  var placeholder = '<option value="" selected>Select…</option>';
  var html = '<div class="tier-h"><h3>Scenario basis</h3></div>'
    + '<div class="basis">'
    + '<div class="field"><label for="ccy">Base Currency</label><select id="ccy"' + disabled + '>'
    + (pendingCcy ? placeholder : '')
    + opt('options.currencies', []).map(function (c) {
        return '<option' + (!pendingCcy && c === showing.currency ? ' selected' : '') + '>'
          + esc(c) + '</option>';
      }).join('')
    + '</select></div>'
    + '<div class="field"><label for="hedge">Currency Hedging</label><select id="hedge"' + disabled + '>'
    + (pendingHedge ? placeholder : '')
    + opt('options.hedgingPolicies', []).map(function (c) {
        return '<option' + (!pendingHedge && c === showing.hedging ? ' selected' : '') + '>'
          + esc(c) + '</option>';
      }).join('')
    + '</select></div></div>';
  if (state.basisDraft) {
    var count = state.columns.length;
    var what = (state.basisDraft.currency !== state.basis.currency)
      ? 'in ' + esc(state.basisDraft.currency)
      : 'with ' + esc(state.basisDraft.hedging) + ' hedging';
    html += '<div class="basis-confirm" role="group" aria-label="Confirm basis change">'
      + '<p>Rebuild ' + count + ' portfolio' + (count === 1 ? '' : 's') + ' ' + what + '?</p>'
      + '<div><button type="button" class="btn btn-primary" id="basis-apply">Rebuild</button>'
      + '<button type="button" class="btn btn-ghost" id="basis-cancel">Cancel</button></div></div>';
  }
  el.innerHTML = html;
}

/* Collapsing the base tier. *quiet* is a step change, which already refreshes;
   a chevron click refreshes too, so both arrive at applyBaseRoll below. */
function setBaseCollapsed(on, quiet) {
  on = !!on;
  if (on === state.baseCollapsed) return;
  state.baseCollapsed = on;
  state.baseRoll = on ? 'up' : 'down';
  if (!quiet) {
    announce('polite', on ? 'Proposed portfolio settings hidden.'
                          : 'Proposed portfolio settings shown.');
  }
}

/* The roll itself. renderBase replaces the tier's innerHTML, so the body is a
   brand-new node in its final state and a CSS transition would have nothing to
   move from. So paint the OLD state first, force the layout, then flip on the
   next frame - the transition then runs from a real starting height. Without a
   pending roll the state is simply painted, which is what a re-render for any
   other reason should do. */
function applyBaseRoll() {
  var el = document.getElementById('tier-base'); if (!el) return;
  var body = el.querySelector('.tier-body');
  var roll = state.baseRoll;
  state.baseRoll = null;
  if (!body || !roll) {
    el.classList.toggle('is-rolled', state.baseCollapsed);
    return;
  }
  body.classList.add('no-roll');                 /* suppress the transition */
  el.classList.toggle('is-rolled', roll === 'down');
  void body.offsetHeight;                        /* commit that as the start */
  body.classList.remove('no-roll');
  window.requestAnimationFrame(function () {
    el.classList.toggle('is-rolled', roll === 'up');
  });
}

function renderBase() {
  var el = document.getElementById('tier-base'); if (!el) return;
  var base = state.columns[0] || null;
  /* before a base exists, the tier renders the pending draft so a partial
     selection survives the re-render */
  var pending = state.baseDraft;
  /* A draft in progress wins over the built key: moving an all-equity base to
     a level that needs an allocation leaves the tier half-answered, and the
     controls must show that half rather than snap back to the old book. */
  var drafting = !!(pending.allocationType || pending.riskLevel);
  var key = (base && !drafting) ? base.key : null;
  var allocationType = key ? (key.allocationType || '') : pending.allocationType;
  var risk = key ? key.riskLevel : pending.riskLevel;
  /* An all-equity level holds no alternatives: the allocation select and the
     exclusion have nothing to say, and grey out. Derived from the facet, not
     from the words "All Equity" (D54). */
  var allEquity = isAllEquityRisk(risk);
  var canRA = allocationType ? reAllowed(allocationType) : false;
  var exRA = key ? !!key.excludeRealAssets : !!pending.excludeRealAssets;
  /* The basis comes first. Until it is answered these controls are inert, and
     the guide's highlight stays on the basis (D91). */
  var locked = !state.basisChosen;
  var disabled = (!schemaReady() || !canEdit() || locked) ? ' disabled' : '';

  /* Then the variant, which decides which allocations exist at all (D49).
     Until it is answered the allocation and risk selects are inert for the
     same reason the whole tier is inert before the basis: their options are
     not merely unknown, they are undecided. */
  var variant = state.variant;
  var variantLocked = locked || !variant;
  var afterVariant = (!schemaReady() || !canEdit() || variantLocked) ? ' disabled' : '';
  var forceExRA = variantForcesExcludeRE(variant);
  if (forceExRA && canRA) exRA = true;

  var variants = opt('options.implementationVariants', []);
  var variantOptions = (variant ? '' : '<option value="" selected>Select…</option>')
    + variants.map(function (v) {
        return '<option' + (v === variant ? ' selected' : '') + '>' + esc(v) + '</option>';
      }).join('');

  /* A custom portfolio (D171) is an allocation choice: "Custom", under the
     strategic ones, for whoever may build one under this type. While one is
     in force the risk level is its profile, the exclusion is not asked, and
     a pending switch either way shows its choice until it is confirmed. */
  var custom = cpActive(), pend = state.cpConfirm;
  var showCustom = custom ? !(pend && pend.to === 'strategic') : !!(pend && pend.to === 'custom');
  var offerCustom = custom || mayBuildPortfolio();
  if (custom) risk = state.customPortfolio.profile || risk;
  var shownAllocation = pend && pend.to === 'strategic' ? pend.allocationType : allocationType;
  var allocationOptions = (allEquity
    ? '<option value=""' + (showCustom ? '' : ' selected') + '>—</option>'
    : (shownAllocation || showCustom ? '' : '<option value="" selected>Select…</option>')
      + allocationChoices(risk).map(function (a) {
          /* greyed out, with the mandate it needs, where the private
             markets minimums keep it closed at this risk level (D200) */
          var probe = risk ? buildKey(a, reAllowed(a) && (exRA || forceExRA), risk) : null;
          var shut = probe && pmClosed(probe) && !(a === shownAllocation && !showCustom);
          return '<option value="' + esc(a) + '"' + (a === shownAllocation && !showCustom ? ' selected' : '')
            + (shut ? ' disabled' : '') + '>' + esc(a) + (shut ? ' — ' + esc(pmClosedWords(probe)) : '') + '</option>';
        }).join(''))
    + (offerCustom ? '<optgroup label="Build it yourself"><option value="__custom__"'
        + (showCustom ? ' selected' : '') + (risk ? '' : ' disabled') + '>Custom'
        + (risk ? '' : ' — choose a risk level first') + '</option></optgroup>' : '');
  /* Every level is offered; a level the current allocation cannot reach is
     shown disabled, and an all-equity level is reachable with no allocation
     at all - so the risk select is live as soon as the variant is. */
  var riskOptions = (risk ? '' : '<option value="" selected>Select…</option>')
    + opt('options.riskLevels', []).map(function (r) {
        var probe = buildKey(allocationType, exRA, r);
        /* a custom portfolio's profile is any level the type anchors (D171) */
        var ok = custom ? true : (probe ? available(probe) : true);
        return '<option value="' + esc(r) + '"'
          + (r === risk ? ' selected' : '') + (ok ? '' : ' disabled') + '>'
          + esc(riskLabel(r)) + (ok ? '' : ' — ' + esc(whyUnavailable(allocationType, exRA, r))) + '</option>';
      }).join('');

  var raNote = '';
  if (allEquity) {
    raNote = 'An all-equity portfolio holds no alternatives, so no allocation type '
      + 'or exclusion applies.';
  } else if (allocationType && !canRA) {
    raNote = 'Not available — ' + esc(allocationType) + ' holds no real assets.';
  } else if (allocationType && forceExRA) {
    raNote = 'Required by ' + esc(variant) + '.';
  }
  var raEnabled = !allEquity && allocationType && canRA && canEdit() && !forceExRA;
  /* the other side of the exclusion may need a larger mandate (D200) */
  var raFlip = raEnabled && risk ? buildKey(allocationType, !exRA, risk) : null;
  if (raFlip && pmClosed(raFlip)) {
    raEnabled = false;
    raNote = esc(allocationType) + (exRA ? '' : ' ex-RAs') + ' ' + esc(pmClosedWords(raFlip)) + '.';
  }

  el.className = 'tier';
  /* Allocation, then risk level, then the exclusion: the selects are the
     choice, the tick box narrows what it produced. */
  /* The heading carries the chevron, so the tier can be reopened wherever it
     was rolled up; the controls go in .tier-body, which is the thing that
     rolls. aria-expanded and aria-controls carry the state to a reader. */
  el.innerHTML = '<div class="tier-h"><h3 id="basetitle">Proposed portfolio</h3>'
    + '<button type="button" class="tier-roll" id="baseroll"'
    + ' aria-expanded="' + (state.baseCollapsed ? 'false' : 'true') + '"'
    + ' aria-controls="basebody" aria-label="'
    + (state.baseCollapsed ? 'Show' : 'Hide') + ' the proposed portfolio settings"'
    + ' title="' + (state.baseCollapsed ? 'Show' : 'Hide')
    + ' the proposed portfolio settings"><span aria-hidden="true">&#8250;</span>'
    + '</button></div>'
    + '<div class="tier-body" id="basebody">'
    + '<div class="basis" style="grid-template-columns:1fr">'
    /* The type gates everything, so it leads and its note follows it. Then
       risk, then allocation: a risk level that is entirely public equity has
       no allocation type at all, so the field below is a consequence of the
       one above it (D54). */
    + '<div class="field"><label for="bpv">Implementation Type</label>'
    + '<select id="bpv"' + disabled + '>' + variantOptions + '</select></div>'
    + (variantLocked && !locked
        ? '<p class="chk-note" style="margin-left:0">Choose an implementation '
          + 'type first — it decides which portfolios are available.</p>' : '')
    + '<div class="field"><label for="bpr">' + (showCustom ? 'Risk profile' : 'Risk Level')
    + '</label><select id="bpr"'
    + (afterVariant || (custom && !mayBuildPortfolio() ? ' disabled' : '')) + '>' + riskOptions + '</select></div>'
    + '<div class="field"><label for="bpa">Allocation</label><select id="bpa"'
    + (afterVariant || (allEquity && !offerCustom ? ' disabled' : '')
       || (custom && !mayBuildPortfolio() ? ' disabled' : '')) + '>' + allocationOptions + '</select></div>'
    + (showCustom ? '' : '<div class="chk"><input type="checkbox" id="bpre"'
      + (exRA && canRA && !allEquity ? ' checked' : '')
      + (raEnabled ? '' : ' disabled')
      + (raNote ? ' aria-describedby="bprenote"' : '') + '>'
      + '<label for="bpre">Exclude Real Assets</label></div>'
      + (raNote ? '<p class="chk-note" id="bprenote">' + raNote + '</p>' : ''))
    + (custom && !(pend && pend.to === 'strategic')
        ? '<p class="chk-note" style="margin-left:0">' + (mayBuildPortfolio()
            ? 'Built product by product. The risk profile sets the limits; nothing is filled in from it.'
            : 'Built product by product' + (state.customPortfolio.builtBy ? ' by ' + esc(state.customPortfolio.builtBy) : '')
              + '. Only an admin changes a custom portfolio.')
          + '</p>' : '')
    + cpConfirmHtml()
    + '</div></div>';
  applyBaseRoll();
}

/* Nothing renders above the allocation table. The strip that used to carry
   the "No real assets" tablet and the column-failure aggregate is gone: a
   failed column already says so on the column itself, and the allocation the
   user chose is the allocation the rail shows. */

/* ---- table scaffolding -------------------------------------------------- */
function readyColumns() {
  return state.columns.filter(function (c) { return c.status === 'ready'; });
}

/* What the document may DRAW, as against what has finished resolving.

   A column that is re-resolving still holds the figures from last time, and
   showing them until the new ones land is what keeps a change like ticking
   Exclude TAA from tearing the page down. Judging by status alone emptied the
   union, so the table fell back to blank skeleton rows and the charts section
   hid itself - which shunted everything below it up and back, and is what made
   the whole page appear to flash on a change that touches two rows.

   A column that has never resolved has nothing to show and is still excluded,
   so a genuinely new column skeletons exactly as spec 10.1 says. */
function shownColumns() {
  return state.columns.filter(function (c) {
    return c.status === 'ready' || (c.status === 'loading' && c.data);
  });
}

function holdsPreviousData(col) {
  return col.status === 'loading' && !!col.data;
}

/* Union of categories over ready columns, in the schema's universe order,
   then any stragglers in first-seen order. Assets union per category keeps
   first-seen order (spec 2.2: rows come from the weight source). */
function unionRows() {
  var order = opt('categories', []);
  var ready = shownColumns();
  var categories = [];
  var index = {};
  function categoryEntry(name) {
    if (index[name]) return index[name];
    var entry = { name: name, assets: [], assetIndex: {} };
    index[name] = entry;
    categories.push(entry);
    return entry;
  }
  order.forEach(function (name) {
    var held = ready.some(function (col) {
      return col.data.categories.some(function (c) { return c.name === name; });
    });
    if (held) categoryEntry(name);
  });
  ready.forEach(function (col) {
    col.data.categories.forEach(function (c) {
      var entry = categoryEntry(c.name);
      c.assets.forEach(function (a) {
        if (!entry.assetIndex[a.reportingName]) {
          entry.assetIndex[a.reportingName] = 1;
          entry.assets.push(a.reportingName);
        }
      });
    });
  });
  return categories;
}

function categoryOf(col, name) {
  if (col.status !== 'ready') return null;
  for (var i = 0; i < col.data.categories.length; i++) {
    if (col.data.categories[i].name === name) return col.data.categories[i];
  }
  return null;
}

/* Three cell states (spec 9.1): a value; an em dash - holds none of this
   asset class; blank - the variant does not have the category at all.
   Loading columns take a shimmer cell after 200ms; error columns a dash. */
function cellFor(col, categoryName, assetName) {
  if (col.status === 'loading' && !holdsPreviousData(col)) {
    return col.skel ? '<span class="skel"></span>' : '';
  }
  if (col.status === 'error') return '—';
  var category = categoryOf(col, categoryName);
  if (!category) return '';
  if (assetName === null) return pct1(category.weightPct);
  for (var i = 0; i < category.assets.length; i++) {
    if (category.assets[i].reportingName === assetName) return pct1(category.assets[i].weightPct);
  }
  return '—';
}

function metricCell(col, kind) {
  if (col.status === 'loading' && !holdsPreviousData(col)) {
    return col.skel ? '<span class="skel"></span>' : '';
  }
  if (col.status === 'error') return '—';
  var metrics = col.data.metrics;
  if (kind === 'ret') return num(metrics.estimatedReturnPct, 2, '%');
  if (kind === 'vol') return num(metrics.volatilityPct, 2, '%');
  return num(metrics.sharpe, 2);
}
var METRIC_ROWS = [['Estimated Mean Return', 'ret'], ['Sharpe Ratio', 'sharpe'],
                   ['Volatility', 'vol']];

/* The first column is the portfolio being proposed to the client, and the
   comparisons are what it is proposed against. It says so in a line over its
   name, and it carries its name as every other column does (A2): a name that
   lived only in a tooltip could not be read on touch, nor in a screenshot.

   Every column also has a mark - P, then 1, 2, 3 - and the same mark is the
   portfolio's dot on the risk and return chart (D1), so the chart can be read
   against the table without a legend of names. */
var BASE_COLUMN_TITLE = 'Proposed';
function columnMark(i) { return i === 0 ? 'P' : String(i); }

/* The allocation table's heads carry each portfolio's category mix as a bar
   under the name (D151), in the category key's colours - the same colour as
   the swatch on that category's row, so a band in the bar is read against
   its row. Drawn only once the column has figures: a loading column without
   earlier ones, or a failed one, keeps an empty head rather than a guess. */
function mixBar(col) {
  if (col.status === 'error' || !col.data || (col.status === 'loading' && !holdsPreviousData(col))) return '';
  var bands = col.data.categories.filter(function (c) { return isFinite(c.weightPct) && c.weightPct >= 0.05; })
    .map(function (c) {
      return '<span style="flex:' + c.weightPct + ' 0 0;background:' + catColor(c.name) + '"></span>';
    }).join('');
  return bands ? '<span class="col-mix" aria-hidden="true">' + bands + '</span>' : '';
}

function columnHeadCell(col, i, scope, below) {
  var isBase = (i === 0);
  var label = headerName(col.key);
  var extra = '';
  if (col.status === 'loading' && col.skel) extra = '<span class="col-ellipsis" aria-hidden="true">…</span>';
  if (col.status === 'error') {
    extra = '<span class="bdg b-breach">Failed</span>'
      + '<button type="button" class="col-retry" data-retry="' + i + '">Retry</button>';
  }
  /* A comparison can be removed from its own column, in any state - a
     still-loading one aborts (spec 11.8). The base has no control: it cannot
     be removed, only changed. */
  var remove = (!isBase && canEdit())
    ? '<button type="button" class="col-rm" data-remove="' + i + '"'
      + ' title="Remove ' + esc(headerName(col.key)) + '"'
      + ' aria-label="Remove ' + esc(headerName(col.key)) + '">×</button>'
    : '';
  return '<th scope="' + scope + '"' + (scope === 'colgroup' ? ' colspan="2"' : '')
    + ' class="num' + (i === App.lastAdded ? ' just-added' : '') + (remove ? ' has-rm' : '') + '"'
    + (col.status === 'loading' ? ' aria-busy="true"' : '')
    + ' title="' + esc(fullName(col.key)) + '"'
    + ' aria-label="' + esc(fullName(col.key)) + '">'
    + '<span class="col-head"><span class="col-mark' + (isBase ? ' is-prop' : '') + '" aria-hidden="true">'
    + columnMark(i) + '</span><span class="col-nm">'
    + (isBase ? '<small class="col-role">' + BASE_COLUMN_TITLE + '</small>' : '')
    + esc(label) + '</span>' + extra + remove + '</span>' + (below || '') + '</th>';
}

/* ---- guiding the first build (D91) ---------------------------------------
   Until the base portfolio exists, exactly one rail control is "next", and
   everything that guides reads it from setupNext(): the field's highlight in
   the rail, the card in the document, the line between them, and where focus
   goes after an answer. The basis is one step made of two answers, both
   required (D93): the card names the pair until one is given, then points at
   the half still open.

   Wide, the rail sits beside the document and a line runs from the card to
   the field. Narrow, the rail stacks above the document and a line would have
   nowhere to go, so it is not drawn and focus does the work alone. */
var GUIDE_WIDE = '(min-width:1040px)';     /* the rail's stacking breakpoint */
var guideFocus = false;                     /* move focus on after the next render */

function setupNext() {
  if (state.phase !== 'workspace' || state.columns.length || !canEdit() || state.step !== 'aa') {
    return null;
  }
  var p = state.baseDraft;
  var total = isAllEquityRisk(p.riskLevel) ? 3 : 4;
  if (!state.basisChosen) {
    var a = state.basisAnswered;
    if (a.currency && !a.hedging) {
      return { id: 'hedge', n: 1, total: total, title: 'Scenario basis',
               text: 'Now the currency hedging, to complete the basis every figure is priced on.' };
    }
    if (a.hedging && !a.currency) {
      return { id: 'ccy', n: 1, total: total, title: 'Scenario basis',
               text: 'Now the base currency, to complete the basis every figure is priced on.' };
    }
    return { id: 'ccy', also: 'hedge', n: 1, total: total, title: 'Scenario basis',
             text: 'Choose a base currency and currency hedging. Every figure in the proposal is priced on this basis.' };
  }
  if (!state.variant) {
    return { id: 'bpv', n: 2, total: total, title: 'Implementation type',
             text: 'It decides which portfolios, and later which sleeves, are available.' };
  }
  if (!p.riskLevel) {
    return { id: 'bpr', n: 3, total: total, title: 'Risk level',
             text: 'The proposed portfolio’s level of risk. Every comparison is measured against it.' };
  }
  if (!p.allocationType && total === 4) {
    return { id: 'bpa', n: 4, total: total, title: 'Allocation',
             text: 'The proposed portfolio builds as soon as this is chosen.' };
  }
  return null;            /* answered, and either building or unavailable */
}

function renderGuide() {
  var next = setupNext();
  document.querySelectorAll('.rail .field.is-next').forEach(function (f) {
    f.classList.remove('is-next');
  });
  [next && next.id, next && next.also].forEach(function (id) {
    var control = id && document.getElementById(id);
    var field = control && control.closest('.field');
    if (field) field.classList.add('is-next');
  });

  var card = document.getElementById('doc-empty');
  if (card && next) {
    card.innerHTML = '<p class="guide-step">Step ' + next.n + ' of ' + next.total + '</p>'
      + '<h3>' + esc(next.title) + '</h3><p>' + esc(next.text) + '</p>'
      + '<p class="guide-where">'
      + (railCollapsed
          ? '<span class="guide-wide">Expand the scenario panel on the left to continue.</span>'
          : '<span class="guide-wide">In the scenario panel, on the left.</span>')
      + '<span class="guide-narrow">In the scenario panel above.</span></p>';
  } else if (card) {
    card.innerHTML = '<h3>Choose the portfolio to propose</h3>'
      + '<p>Choose a risk level and allocation in the scenario panel to build the first column.</p>';
  }
  if (!next) placeGuide(false);           /* take the line down now, not next frame */
  window.requestAnimationFrame(function () { placeGuide(true); });
}

/* The line is fixed to the viewport, so it is redrawn on every scroll, the
   rail's included. The card is only re-aligned with its field on a render or
   a resize: moving it while the page scrolls would chase the reader. */
function guideWire() {
  var wire = document.getElementById('guide-wire');
  if (wire || !document.body) return wire;
  /* on body, so no ancestor's stacking context can put it under the rail */
  var ns = 'http://www.w3.org/2000/svg';
  wire = document.createElementNS(ns, 'svg');
  wire.setAttribute('id', 'guide-wire');
  wire.setAttribute('class', 'guide-wire');
  wire.setAttribute('aria-hidden', 'true');
  wire.setAttribute('hidden', '');
  wire.appendChild(document.createElementNS(ns, 'path'));
  var dot = document.createElementNS(ns, 'circle');
  dot.setAttribute('r', '3.5');
  wire.appendChild(dot);
  document.body.appendChild(wire);
  return wire;
}

function placeGuide(realign) {
  var wire = guideWire();
  var card = document.getElementById('doc-empty');
  var next = setupNext();
  var wide = window.matchMedia && window.matchMedia(GUIDE_WIDE).matches;
  var target = next && document.getElementById(railCollapsed ? 'railtoggle' : (next.also || next.id));
  var show = !!(wire && card && next && wide && target && !card.hidden && !draft);
  if (card && (!show || !wide)) card.style.marginTop = '';
  if (!show) { if (wire) wire.setAttribute('hidden', ''); return; }

  var tr = target.getBoundingClientRect();
  var ty = tr.top + tr.height / 2;
  if (realign) {
    /* centre the card on the field, never above where it sits naturally and
       never so low that it runs off the bottom of the window */
    /* measured against the margin it has NOW - mid-slide that is not the
       one last asked for, and using the target walks the card off course */
    var current = parseFloat(window.getComputedStyle(card).marginTop) || 0;
    var natural = card.getBoundingClientRect().top - current;
    var room = window.innerHeight - natural - card.offsetHeight - 24;
    var want = Math.round(Math.max(0, Math.min(ty - natural - card.offsetHeight / 2, room)));
    if (Math.abs((parseFloat(card.style.marginTop) || 0) - want) > 1) card.style.marginTop = want + 'px';
    /* the card slides there, and the line follows it rather than jumping */
    trackGuide(500);
    return;
  }
  var cr = card.getBoundingClientRect();
  var rail = document.querySelector('.rail');
  var rr = rail ? rail.getBoundingClientRect() : null;
  var visible = cr.bottom > 0 && cr.top < window.innerHeight
    && (!rr || (ty > rr.top && ty < rr.bottom));
  if (!visible) { wire.setAttribute('hidden', ''); return; }

  var x1 = cr.left - 2, y1 = cr.top + cr.height / 2;
  var x2 = tr.right + 7, y2 = ty;
  var dx = Math.max(28, (x1 - x2) / 2);
  wire.removeAttribute('hidden');
  wire.setAttribute('viewBox', '0 0 ' + window.innerWidth + ' ' + window.innerHeight);
  wire.querySelector('path').setAttribute('d', 'M' + x1 + ' ' + y1 + ' C' + (x1 - dx) + ' ' + y1
    + ' ' + (x2 + dx) + ' ' + y2 + ' ' + x2 + ' ' + y2);
  var dot = wire.querySelector('circle');
  dot.setAttribute('cx', x2);
  dot.setAttribute('cy', y2);
}

/* Redraw every frame for a while: the card sliding, or the rail folding. */
var guideTrackUntil = 0;
function trackGuide(ms) {
  var running = guideTrackUntil > Date.now();
  guideTrackUntil = Math.max(guideTrackUntil, Date.now() + ms);
  if (running) return;
  (function frame() {
    placeGuide(false);
    if (Date.now() < guideTrackUntil) window.requestAnimationFrame(frame);
  })();
}

var guideScrollFrame = 0;
document.addEventListener('scroll', function () {
  if (guideScrollFrame) return;
  guideScrollFrame = window.requestAnimationFrame(function () {
    guideScrollFrame = 0;
    placeGuide(false);
  });
}, true);
window.addEventListener('resize', function () { placeGuide(true); });
/* and once more when the card lands, however long the slide really took */
document.addEventListener('transitionend', function (e) {
  if (e.target && e.target.id === 'doc-empty') placeGuide(false);
});

/* After an answer, focus moves to the next control - at every width, and
   only while the base is still to be built. It waits out a control that is
   disabled while the schema re-fetches, and gives up rather than pulling
   focus back if the user has gone somewhere else in the meantime. */
function focusGuide() {
  if (!guideFocus) return;
  var next = setupNext();
  if (!next) { guideFocus = false; return; }
  var el = document.getElementById(next.id);
  if (!el || el.disabled || el.offsetParent === null) return;
  var active = document.activeElement;
  var free = !active || active === document.body || !!(active.closest && active.closest('.rail'));
  guideFocus = false;
  if (free) el.focus();
}

function renderAlloc() {
  var el = document.getElementById('alloc'); if (!el) return;
  var wrap = document.getElementById('doc-empty');
  /* The wrapper carries the accent rule, so an empty table must take it
     with it - otherwise the rule sits alone above the empty state. */
  var frame = el.closest('.tblwrap');
  /* a custom portfolio's own column first, the strategic ones beside it
     (D171) - customPortfolio.js lays that table out */
  if (state.phase === 'workspace' && cpActive() && App.cpAllocHtml) {
    if (wrap) wrap.hidden = true;
    if (frame) frame.hidden = false;
    el.innerHTML = App.cpAllocHtml();
    return;
  }
  if (state.phase === 'workspace' && !state.columns.length) {
    if (wrap) wrap.hidden = false;
    if (frame) frame.hidden = true;
    el.innerHTML = '';
    return;
  }
  if (wrap) wrap.hidden = true;
  if (frame) frame.hidden = false;

  var plus = App.plusColumn && canEdit()
    && state.columns.length < opt('rules.maxPortfolios', 4) && state.columns.length > 0;
  var html = '<caption class="sr-only">Portfolio allocation and metrics</caption><thead><tr>'
    + '<th scope="col" class="rowhead">Asset Class</th>';
  state.columns.forEach(function (col, i) { html += columnHeadCell(col, i, 'col', mixBar(col)); });
  if (plus) {
    html += '<th scope="col" class="num addcol"><button type="button" id="plusbtn" '
      + 'class="plus" aria-label="Add a comparison portfolio">+</button></th>';
  }
  html += '</tr></thead><tbody>';

  /* Every cell's content sits in an inline-block wrapper so a row can be
     collapsed to nothing and back (animateRowChanges). inline-block, not
     block: sizeFixedColumns measures column widths with the table in auto
     layout, where a block child would claim the full column and the
     measurement would be meaningless. */
  function row(cls, label, cellFn, key, lead) {
    var r = '<tr class="' + cls + '"' + (key ? ' data-rk="' + esc(key) + '"' : '')
      + '><th scope="row"><span class="cw">' + (lead || '') + esc(label) + '</span></th>'
      + state.columns.map(function (col) {
          return '<td class="num"><span class="cw">' + cellFn(col) + '</span></td>';
        }).join('');
    if (plus) r += '<td class="num addcol"></td>';
    return r + '</tr>';
  }

  var union = unionRows();
  if (!union.length) {
    /* nothing resolved yet: a generic skeleton keeps the surface honest */
    for (var s = 0; s < 6; s++) {
      html += row(s === 0 ? 'cat' : 'asset' + (s % 2 ? ' alt' : ''), ' ',
        function (col) { return col.skel ? '<span class="skel"></span>' : (col.status === 'error' ? '—' : ''); });
    }
  } else {
    union.forEach(function (category) {
      /* the category's key colour, the one its band in the heads' bars takes */
      html += row('cat', category.name, function (col) {
        return cellFor(col, category.name, null);
      }, 'c:' + category.name,
      '<span class="cat-sw" aria-hidden="true" style="background:' + catColor(category.name) + '"></span>');
      category.assets.forEach(function (assetName, si) {
        html += row('asset' + (si % 2 ? ' alt' : ''), assetName, function (col) {
          return cellFor(col, category.name, assetName);
        }, 'a:' + category.name + ':' + assetName);
      });
    });
  }
  /* The total and metric rows are keyed as well. They never come or go, but a
     leaving row is placed before the next KEYED row, and a category at the
     foot of the union has none after it - so without these its ghost fell
     through to appendChild and faded out below Volatility instead of in
     place. */
  html += row('total', 'Total', function (col) {
    if (col.status === 'loading') return col.skel ? '<span class="skel"></span>' : '';
    if (col.status === 'error') return '—';
    return '100.0%';
  }, 't:total');
  METRIC_ROWS.forEach(function (m) {
    html += row('metric', m[0], function (col) { return metricCell(col, m[1]); },
      'm:' + m[0]);
  });
  /* Rows that come and go are animated individually; the table's height then
     follows its own content, which is what makes the movement read as the row
     arriving rather than as a gap closing after it has already gone. */
  animateRowChanges(el, function () {
    el.innerHTML = html + '</tbody>';
  });
}

/* Animate the rows a re-render adds or drops.

   The earlier version of this animated the WRAPPER's height instead. That was
   wrong in a way that showed: innerHTML replaced the rows instantly, so on a
   removal the row vanished and you watched an empty gap close behind it. Here
   the leaving row is put back into the new table and collapsed, the entering
   row starts collapsed and opens, and the table's height changes because its
   content does - no box animation at all.

   Rows are matched by data-rk. A row cannot be transitioned itself
   (display:table-row does not interpolate) so what animates is each cell's
   block padding and the max-height of the .cw wrapper inside it, which
   together account for the whole row height. */
var rowSnapshots = {};                  /* per table, held across a render pair */

function animateRowChanges(table, mutate) {
  var slot = table.id || 'table';

  /* One change renders twice: optimistically while the column is loading, and
     again when the resolve lands. The snapshot is taken on the first of those
     and HELD, so the animation runs once, against the true before and after.
     Animating on both instead wiped the in-flight rows halfway through and
     restarted them, which is why the exit never finished.

     Ghosts are excluded: they carry data-rk because they are clones of real
     rows, and counting them would make the next render try to remove them
     again. */
  if (!rowSnapshots[slot]) {
    rowSnapshots[slot] = { keys: {}, order: [] };
    var old = table.querySelectorAll('tbody tr[data-rk]:not([data-ghost])');
    for (var i = 0; i < old.length; i++) {
      var key = old[i].getAttribute('data-rk');
      rowSnapshots[slot].keys[key] = { node: old[i].cloneNode(true), height: old[i].offsetHeight };
      rowSnapshots[slot].order.push(key);
    }
  }

  mutate();

  /* Still settling: keep the snapshot and animate on the render that lands. */
  if (state.columns.some(function (c) { return c.status === 'loading'; })) return;

  var snap = rowSnapshots[slot];
  var before = snap ? snap.keys : {};
  var order = snap ? snap.order : [];
  rowSnapshots[slot] = null;

  var body = table.querySelector('tbody');
  if (!body) return;

  /* Nothing was there before: this is the table being populated, not rows
     coming and going within it. Opening every row from collapsed would make
     the table grow under the sections beneath it and shove them down the page
     - which is exactly what the first resolve looked like. The whole document
     fades in together instead (renderStageChrome). */
  if (!order.length) return;

  var now = body.querySelectorAll('tr[data-rk]:not([data-ghost])');
  var present = {};
  var entering = [];
  for (var n = 0; n < now.length; n++) {
    var k = now[n].getAttribute('data-rk');
    present[k] = now[n];
    if (!(k in before)) entering.push(now[n]);
  }

  /* Leaving rows go back where they were, then collapse. Reinstated in their
     original order so a dropped category does not reappear at the bottom. */
  for (var o = 0; o < order.length; o++) {
    var goneKey = order[o];
    if (present[goneKey]) continue;
    var ghost = before[goneKey].node;
    ghost.setAttribute('data-ghost', '1');
    var after = null;
    for (var f = o + 1; f < order.length; f++) {
      if (present[order[f]]) { after = present[order[f]]; break; }
    }
    if (after) body.insertBefore(ghost, after); else body.appendChild(ghost);
    openRow(ghost, before[goneKey].height);
    void ghost.offsetHeight;                  /* commit the open state first */
    collapseRow(ghost, true);
  }

  /* Rows that stay put but whose figures changed: fade the new value in.
     Every weight shifts when a category is added or dropped, and swapping
     them under the reader is the same abruptness as swapping a whole row.
     Only cells whose text actually differs are touched, so a re-render that
     changes nothing stays completely still. */
  for (var p = 0; p < order.length; p++) {
    var stayKey = order[p];
    var live = present[stayKey];
    if (!live || !before[stayKey]) continue;
    var wasCells = before[stayKey].node.querySelectorAll('.cw');
    var nowCells = live.querySelectorAll('.cw');
    if (wasCells.length !== nowCells.length) continue;
    for (var q = 0; q < nowCells.length; q++) {
      if (wasCells[q].textContent === nowCells[q].textContent) continue;
      fadeValue(nowCells[q]);
    }
  }

  /* Entering rows start collapsed and open on the next frame. */
  /* A forced reflow rather than requestAnimationFrame: the start state has to
     be committed before the end state is set or no transition begins, and rAF
     does not fire at all in a background tab - which would leave rows stuck
     collapsed rather than merely un-animated. */
  entering.forEach(function (tr) {
    var target = tr.offsetHeight;
    collapseRow(tr, false, true);             /* start closed, no animation */
    void tr.offsetHeight;                     /* commit that */
    openRow(tr, target);                      /* then open, with one */
  });
}

/* Fade a changed figure in. The new text is already in the DOM, so this
   starts it transparent with no transition, commits that, then eases it up -
   the same commit-the-start-state dance the row animations need. */
function fadeValue(wrap) {
  wrap.style.transition = 'none';
  wrap.style.opacity = '0';
  void wrap.offsetWidth;
  wrap.style.transition = 'opacity var(--value-motion,var(--row-motion))';
  wrap.style.opacity = '1';
  window.clearTimeout(wrap._fadeTimer);
  /* the cleanup has to outlast the fade, or it strips the transition
     mid-flight and the value snaps to full opacity */
  /* and it is marked, for a moment (F1): the commonest change of all is the
     same rows with different numbers - hedging toggled, real assets excluded
     - and a handful of figures out of a hundred moved with nothing to say
     which. The class is dropped and re-added so a second change restarts it. */
  wrap.classList.remove('chg');
  void wrap.offsetWidth;
  wrap.classList.add('chg');
  wrap._fadeTimer = window.setTimeout(function () {
    wrap.style.transition = '';
    wrap.style.opacity = '';
    wrap.classList.remove('chg');
  }, 2000);
}

function rowCells(tr) {
  return tr.querySelectorAll(':scope > th, :scope > td');
}

/* Collapsed: no block padding, no content height. `remove` schedules the row
   for deletion once it has finished closing. */
var ROW_CELL_TRANSITION =
  'padding var(--row-motion),line-height var(--row-motion),border-width var(--row-motion)';

/* `instant` applies the collapsed state with no transition. An entering row
   needs that: given a transition, the collapse itself animates and the open
   that follows simply reverses it, so the row never leaves full height. */
function collapseRow(tr, remove, instant) {
  var cells = rowCells(tr);
  for (var i = 0; i < cells.length; i++) {
    var cell = cells[i];
    cell.style.transition = instant ? 'none' : ROW_CELL_TRANSITION;
    cell.style.paddingTop = '0px';
    cell.style.paddingBottom = '0px';
    /* The wrapper's max-height empties the cell's CONTENT, but a table cell
       still reserves a line box for its strut and keeps its border - together
       about 23 of the row's 29 pixels. Both have to collapse or the row stops
       shrinking most of the way through. */
    cell.style.lineHeight = '0';
    cell.style.borderTopWidth = '0';
    cell.style.borderBottomWidth = '0';
    var wrap = cell.querySelector('.cw');
    if (!wrap) continue;
    wrap.style.transition = instant
      ? 'none' : 'max-height var(--row-motion),opacity var(--row-motion)';
    wrap.style.maxHeight = '0px';
    wrap.style.opacity = '0';
  }
  if (!remove) return;
  var settle = function () {
    window.clearTimeout(tr._rowTimer);
    if (tr.parentNode) tr.parentNode.removeChild(tr);
  };
  tr.addEventListener('transitionend', function (e) {
    if (e.propertyName === 'max-height') settle();
  });
  tr._rowTimer = window.setTimeout(settle, 1600);
}

/* Open to a measured height, then hand the row back to the stylesheet so it
   is not left pinned to a pixel value. */
function openRow(tr, height) {
  var cells = rowCells(tr);
  for (var i = 0; i < cells.length; i++) {
    var cell = cells[i];
    cell.style.transition = ROW_CELL_TRANSITION;
    cell.style.paddingTop = '';
    cell.style.paddingBottom = '';
    cell.style.lineHeight = '';
    cell.style.borderTopWidth = '';
    cell.style.borderBottomWidth = '';
    var wrap = cell.querySelector('.cw');
    if (!wrap) continue;
    wrap.style.transition = 'max-height var(--row-motion),opacity var(--row-motion)';
    wrap.style.maxHeight = (height || 40) + 'px';
    wrap.style.opacity = '1';
  }
  window.clearTimeout(tr._openTimer);
  tr._openTimer = window.setTimeout(function () {
    if (tr.getAttribute('data-ghost')) return;      /* on its way out */
    for (var j = 0; j < cells.length; j++) {
      cells[j].style.transition = '';
      cells[j].style.lineHeight = '';
      cells[j].style.borderTopWidth = '';
      cells[j].style.borderBottomWidth = '';
      var w = cells[j].querySelector('.cw');
      if (w) { w.style.transition = ''; w.style.maxHeight = ''; w.style.opacity = ''; }
    }
  }, 1000);
}

/* Both comparison tables are laid out fixed so every data column is exactly
   the same width - a table whose columns differ in width reads as though the
   numbers differ in kind. Fixed layout needs the widths measured first,
   because they depend on the text the resolved portfolios happen to carry:
   the row header's content width, and the widest data column.

   Columns are capped at MAX_DATA_COLUMN so one portfolio does not stretch
   across the whole document, and floored at the widest natural column so
   nothing is ever clipped. Between those the columns share the available
   width equally. Whatever the total comes to is the table's width; below it
   the wrapper scrolls, which is what spec 12 asks for anyway. */
var MAX_DATA_COLUMN = 260;
var MIN_DATA_COLUMN = 120;

function sizeFixedColumns(table, dataCells, skip) {
  var heads = table.querySelectorAll('thead tr:first-child th');
  var wrap = table.parentNode;
  if (!heads.length || !wrap) return;
  if (sizingCanWait(table)) return;

  /* The wrapper shrink-wraps the table, so asking it how much room there is
     would just measure the table we are about to size. Stretch it for the
     measurement, then hand it back to the stylesheet. */
  var wrapWidth = wrap.style.width;
  wrap.style.width = '100%';
  var available = wrap.clientWidth;
  wrap.style.width = wrapWidth;
  if (available <= 0) return;                    /* hidden: size on the next pass */

  /* The measurement below runs the table through width:auto, and a transition
     cannot interpolate out of auto - the box would jump while the columns
     eased. So the transition is suppressed across the measurement, the old
     pixel width is restored, layout is forced, and only then is the
     transition put back. The final assignment is px to px, which animates. */
  var previousWidth = table.style.width;
  var previousC1 = table.style.getPropertyValue('--fixed-c1');
  var previousCol = table.style.getPropertyValue('--fixed-col');
  var previousTransition = table.style.transition;
  table.style.transition = 'none';

  /* Measure with the table free to shrink, or every column reports the
     stretched width it was given rather than the width its content needs.
     The widths this function published last time must be neutralised too, or
     each pass measures the previous pass's answer and the column can only
     ever grow. */
  table.style.setProperty('--fixed-c1', 'auto');
  table.style.setProperty('--fixed-col', 'auto');
  table.style.tableLayout = 'auto';
  /* max-content, not auto: an auto table is shrink-to-fit, and in a narrow
     wrapper it took the shortfall out of the one column that can give - the
     labels, whose wrapper is max-width:100% - so the measure came back short
     and the labels were clipped ("Investment Grade Fixe"). What is wanted is
     what the labels need; whether it fits is decided below (H3). */
  table.style.width = 'max-content';
  table.style.minWidth = '0';

  var headerWidth = heads[0].getBoundingClientRect().width;
  /* Narrow, a long label - "Private Equity & Other Private Assets" - pinned and
     unwrapped took most of the window and left a sliver for the figures. It
     is capped, and the stylesheet lets it wrap there (H3). */
  if (window.matchMedia && window.matchMedia('(max-width:700px)').matches) {
    headerWidth = Math.min(headerWidth, Math.floor(available * 0.44));
  }
  var skipped = skip ? table.querySelector(skip) : null;
  /* the + column's own declared width, not what auto layout hands it */
  var skippedWidth = skipped
    ? (parseFloat(getComputedStyle(skipped).minWidth) || skipped.getBoundingClientRect().width)
    : 0;
  var counted = 0;
  for (var i = 1; i < heads.length; i++) {
    if (heads[i] !== skipped) counted += dataCells;
  }
  if (!counted) {
    table.style.tableLayout = ''; table.style.width = '';
    table.style.transition = previousTransition;
    return;
  }

  var share = (available - headerWidth - skippedWidth) / counted;
  var columnWidth = Math.min(MAX_DATA_COLUMN, Math.max(MIN_DATA_COLUMN, share));

  /* The table's width has to be stated: a fixed-layout table with width:auto
     is still at least as wide as its container (CSS 2.1 17.5.2.1), and any
     surplus would be shared back out across the columns, undoing the cap. So
     pin it to exactly the sum of the specified columns. Narrower than the
     document is the point; wider and the wrapper scrolls. */
  var c1 = Math.ceil(headerWidth);
  var each = Math.floor(columnWidth);
  var total = c1 + each * counted + Math.ceil(skippedWidth);

  /* Put every measured value back where it was, with the transition still
     off, before re-enabling it - so what the browser animates is one pixel
     value to another.

     The custom properties matter as much as the width here. They are
     registered as <length>, so the 'auto' the measurement writes is invalid
     for them and falls back to the 0px initial value; re-enabling the
     transition from that state made the columns sweep up from nothing on
     EVERY render, including renders where the widths had not changed at all.
     Restoring the previous pixels first means an unchanged width animates
     from itself - which is to say, it does not move. */
  table.style.tableLayout = 'fixed';
  if (previousWidth) table.style.width = previousWidth;
  if (previousC1) table.style.setProperty('--fixed-c1', previousC1);
  if (previousCol) table.style.setProperty('--fixed-col', previousCol);
  void table.offsetWidth;
  table.style.transition = previousTransition;

  table.style.setProperty('--fixed-c1', c1 + 'px');
  table.style.setProperty('--fixed-col', each + 'px');
  table.style.width = total + 'px';
  table.style.minWidth = '0';
}

/* ---- the rail collapses, and the document takes the width ---------------
   Every load opens with the rail open. It closes only when someone presses
   the button, and that lasts for the page, not beyond it: the rail is where
   the work starts, so it is never remembered shut (D92). The preference used
   to persist; the key it left behind is cleared so it can never be read. */
var railCollapsed = false;
try { window.localStorage.removeItem('pmg.proposalTool.railCollapsed'); } catch (e) {}

function renderRailToggle() {
  document.body.classList.toggle('rail-collapsed', railCollapsed);
  var button = document.getElementById('railtoggle');
  if (!button) return;
  var label = railCollapsed ? 'Expand the scenario panel' : 'Collapse the scenario panel';
  button.setAttribute('aria-expanded', String(!railCollapsed));
  button.setAttribute('aria-label', label);
  button.setAttribute('title', label);
  button.innerHTML = '<span aria-hidden="true">' + (railCollapsed ? '»' : '«') + '</span>';
}

function toggleRail() {
  railCollapsed = !railCollapsed;
  renderRailToggle();
  renderGuide();
  trackGuide(450);                      /* the line follows the fold */
  /* the document just changed width, and the columns share what it has */
  trackRailMotion();
}

/* The fold is animated, so the document's width now arrives over ~280ms
   rather than all at once, and sizeComparisonTables has to run against the
   width the document ends up with. It runs once, when the rail says it has
   stopped.

   It does NOT run per frame. One pass costs ~18ms measured on the four-column
   allocation and risk tables together - past the 16.7ms frame budget on its
   own - so re-measuring during the slide would halve the frame rate to smooth
   a change that usually is not there at all: the data columns sit at
   MAX_DATA_COLUMN whenever the document has room, so widening the document by
   folding the rail leaves the table exactly as it was. Where the columns are
   share-constrained and the width does move, it moves once, as the slide
   settles, which reads as the table coming to rest rather than as a jump. */
function trackRailMotion() {
  var rail = document.querySelector('.rail');
  if (!rail) { sizeComparisonTables(); return; }

  var pending = true;
  function settle(e) {
    if (e && e.propertyName && e.propertyName !== 'width') return;
    if (!pending) return;
    pending = false;
    rail.removeEventListener('transitionend', settle);
    sizeComparisonTables();
  }
  rail.addEventListener('transitionend', settle);
  /* transitionend does not arrive if the transition never starts - a hidden
     rail, a zero duration, a fold interrupted by another click, a background
     tab throttling its frames - so the pass is bounded rather than left
     waiting on an event that may not come. */
  window.setTimeout(settle, 700);
}

/* Sizing runs after the renderers, because a table inside a step that is
   still hidden measures zero. Re-runs on resize, since the equal share
   depends on how much room the document has. */
/* Sizing is deferred while any column is still resolving.

   A change like ticking Exclude TAA renders twice: once optimistically, while
   the column is loading and the row set is momentarily different, and again
   when the resolve lands. Measuring both times sent column one out to one
   width and straight back - the visible "narrows, then returns" - for a change
   that ends where it started. Measuring only settled columns means the widths
   move once, or not at all when nothing about them changed.

   The exception is a table that has never been sized: it has no widths to
   hold, so the first pass runs whatever the column states are. */
function sizingCanWait(table) {
  if (!table.style.getPropertyValue('--fixed-col')
      && !table.style.getPropertyValue('--risk-col')) return false;
  return state.columns.some(function (c) { return c.status === 'loading'; });
}

function sizeComparisonTables() {
  var alloc = document.getElementById('alloc');
  var risk = document.getElementById('risk');
  if (alloc && alloc.rows.length) sizeFixedColumns(alloc, 1, 'thead th.addcol');
  if (risk && risk.rows.length) sizeRiskColumns(risk);
}

/* The risk dashboard sizes on its own rule, not the allocation table's.

   Its first column holds the crisis names - "US Inflation / Iranian
   Revolution" is the longest - and those must never be clipped or run under
   the first portfolio's Nominal column. So column one is LOCKED to what its
   widest cell needs plus a gutter, the data columns take what is left down to
   a floor, and below that floor the table stops shrinking and the wrapper
   scrolls. That is what lets four portfolios x two sub-columns survive a
   narrow document.

   AUTO layout, not fixed, and the widths go on the cells as min/max rather
   than on the <col> elements. Fixed layout would not take direction here:
   with the header row carrying colspan=2 pairs, neither a width on <col> nor
   one on the first cell moved the column - both computed correctly and the
   browser laid it out at the data width regardless. Auto layout honours a
   min-width/max-width pair on a cell exactly, which is all this needs. */
var MIN_RISK_DATA_COLUMN = 92;      /* a Nominal or Real cell: "-100.00%" */
var RISK_HEAD_GUTTER = 20;          /* breathing room after the longest name */

function sizeRiskColumns(table) {
  var wrap = table.parentNode;
  if (!wrap) return;
  if (sizingCanWait(table)) return;

  var wrapWidth = wrap.style.width;
  wrap.style.width = '100%';
  var available = wrap.clientWidth;
  wrap.style.width = wrapWidth;
  if (available <= 0) return;                  /* hidden: size on a later pass */

  /* Measure with nothing pinned, or each cell reports the width it was last
     given rather than the width its content needs. */
  table.style.removeProperty('--risk-c1');
  table.style.removeProperty('--risk-col');
  table.style.tableLayout = 'auto';
  table.style.width = 'max-content';        /* as sizeFixedColumns, and for its reason */
  table.style.minWidth = '0';

  /* The widest thing column one has to hold. Section bands span the whole
     table, so they are skipped - they are not what column one must fit. */
  var widest = 0;
  var firstCells = table.querySelectorAll('thead tr:first-child > th:first-child, '
    + 'tbody tr > th:first-child');
  for (var i = 0; i < firstCells.length; i++) {
    var cell = firstCells[i];
    if (cell.colSpan > 1) continue;            /* a band, not a row header */
    widest = Math.max(widest, cell.getBoundingClientRect().width);
  }
  if (widest <= 0) { table.style.tableLayout = ''; table.style.width = ''; return; }
  var headWidth = Math.ceil(widest) + RISK_HEAD_GUTTER;
  if (window.matchMedia && window.matchMedia('(max-width:700px)').matches) {
    headWidth = Math.min(headWidth, Math.floor(available * 0.44));      /* the label cap, narrow (H3) */
  }

  var dataCols = 0;
  var headRow = table.querySelector('thead tr:first-child');
  if (headRow) {
    for (var h = 1; h < headRow.children.length; h++) {
      dataCols += headRow.children[h].colSpan || 1;
    }
  }
  if (!dataCols) { table.style.tableLayout = ''; table.style.width = ''; return; }

  var share = Math.floor((available - headWidth) / dataCols);
  var dataWidth = Math.max(MIN_RISK_DATA_COLUMN, share);

  table.style.setProperty('--risk-c1', headWidth + 'px');
  table.style.setProperty('--risk-col', dataWidth + 'px');
  table.style.tableLayout = 'auto';
  table.style.width = (headWidth + dataWidth * dataCols) + 'px';
  table.style.minWidth = '0';
}

var resizeTimer = null;
window.addEventListener('resize', function () {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(sizeComparisonTables, 120);
});

function renderRisk() {
  var el = document.getElementById('risk'); if (!el) return;
  if (!state.columns.length) { el.innerHTML = ''; return; }
  var n = state.columns.length, span = n * 2 + 1;
  /* The Nominal/Real strip is NOT in the header: the first section spans both
     sub-columns, so labelling them at the top would claim a split that does
     not apply until the stress periods. It is emitted once, inside the body,
     where the split actually begins. */
  /* A colgroup, because every header cell spans its Nominal/Real pair: fixed
     layout takes column widths from <col> when present, which is the only way
     to guarantee the two halves of a pair are exactly equal. */
  var html = '<caption class="sr-only">Risk dashboard</caption>'
    + '<colgroup><col class="col-head">'
    + new Array(n * 2 + 1).join('<col class="col-data">')
    + '</colgroup><thead><tr>'
    /* The column heads the measure names, which speak for themselves; the
       label is kept for screen readers, where an unnamed column header is a
       real loss rather than a tidy one (spec 13). */
    + '<th scope="col" class="rowhead"><span class="sr-only">Measure</span></th>';
  state.columns.forEach(function (col, i) { html += columnHeadCell(col, i, 'colgroup'); });
  html += '</tr></thead><tbody>';

  function nominalRealStrip() {
    return '<tr class="subhead"><td></td>' + state.columns.map(function () {
      return '<th scope="col" class="num sub">Nominal</th>'
           + '<th scope="col" class="num sub">Real</th>';
    }).join('') + '</tr>';
  }

  function band(title) {
    return '<tr class="band"><th scope="rowgroup" colspan="' + span + '">' + title + '</th></tr>';
  }
  function spanRow(cls, label, cellFn, key) {
    return '<tr class="' + cls + '"' + (key ? ' data-rk="' + esc(key) + '"' : '')
      + '><th scope="row"><span class="cw">' + esc(label) + '</span></th>'
      + state.columns.map(function (col) {
          return '<td class="num span2" colspan="2"><span class="cw">'
            + cellFn(col) + '</span></td>';
        }).join('') + '</tr>';
  }
  /* keyed and wrapped like every other row, so a figure that changes under
     the reader is faded in and marked here too (F1) */
  function pairRow(cls, label, pairFn, key) {
    return '<tr class="' + cls + '"' + (key ? ' data-rk="' + esc(key) + '"' : '') + '><th scope="row"><span class="cw">'
      + esc(label) + '</span></th>'
      + state.columns.map(function (col) {
          if (col.status === 'loading' && !holdsPreviousData(col)) {
            var skel = '<span class="cw">' + (col.skel ? '<span class="skel"></span>' : '') + '</span>';
            return '<td class="num">' + skel + '</td><td class="num">' + skel + '</td>';
          }
          if (col.status === 'error') {
            return '<td class="num"><span class="cw">—</span></td><td class="num"><span class="cw">—</span></td>';
          }
          return pairFn(col);
        }).join('') + '</tr>';
  }

  html += band('Factor Based Risk Analytics');
  unionRows().forEach(function (category) {
    html += spanRow('cat', category.name, function (col) {
      return cellFor(col, category.name, null);
    }, 'rc:' + category.name);
  });
  METRIC_ROWS.forEach(function (m) {
    html += spanRow('metric', m[0], function (col) { return metricCell(col, m[1]); },
      'rm:' + m[0]);
  });

  /* stress and premia rows are data-driven: the union of labels over ready
     columns, in first-seen order (spec 9.2) */
  function labelUnion(field) {
    var labels = [], seen = {};
    shownColumns().forEach(function (col) {
      (col.data[field] || []).forEach(function (entry) {
        var label = entry.period || entry.label;
        if (!seen[label]) { seen[label] = 1; labels.push(label); }
      });
    });
    return labels;
  }
  function findEntry(col, field, label) {
    var rows = col.data[field] || [];
    for (var i = 0; i < rows.length; i++) {
      if ((rows[i].period || rows[i].label) === label) return rows[i];
    }
    return null;
  }
  /* signed: a loss is red and a gain green, both keeping their sign, so
     colour is never the only channel (spec 13.4). Probabilities take neither
     - a high probability of loss is not a "positive" number. */
  function pairCells(entry, probability, signed) {
    if (!entry) return '<td class="num"><span class="cw">\u2014</span></td><td class="num"><span class="cw">\u2014</span></td>';
    var digits = probability ? 1 : 2;
    function cls(value) {
      if (!signed) return 'num';        /* only the stress rows are coloured */
      if (value < 0) return 'num neg';
      return value > 0 ? 'num pos' : 'num';
    }
    return '<td class="' + cls(entry.nominalPct) + '"><span class="cw">' + num(entry.nominalPct, digits, '%') + '</span></td>'
         + '<td class="' + cls(entry.realPct) + '"><span class="cw">' + num(entry.realPct, digits, '%') + '</span></td>';
  }

  html += band('Predicted Performance Over Stress Periods');
  html += nominalRealStrip();
  var stressLabels = labelUnion('stress');
  if (!stressLabels.length) {
    html += pairRow('asset', ' ', function () { return ''; });
  }
  stressLabels.forEach(function (label, i) {
    html += pairRow('asset' + (i % 2 ? ' alt' : ''), label, function (col) {
      return pairCells(findEntry(col, 'stress', label), false, true);
    }, 'rs:' + label);
  });

  /* Risk premia are grouped by measure - Value at Risk, Conditional Value at
     Risk, Probability of Loss - each a heading row over its horizons, the
     shape the existing report uses. The grouping comes from the payload: an
     entry's `group` where the adapter supplies one, otherwise everything in
     the label before its final separator, so a store baked before the field
     existed still groups correctly. */
  function premiaGroupOf(entry) {
    if (entry.group) return entry.group;
    var parts = String(entry.label).split(' \u00b7 ');
    return parts.length > 1 ? parts.slice(0, -1).join(' \u00b7 ') : entry.label;
  }
  function premiaHorizonOf(entry) {
    if (entry.horizon) return entry.horizon;
    var parts = String(entry.label).split(' \u00b7 ');
    return parts.length > 1 ? parts[parts.length - 1] : entry.label;
  }

  var groups = [], horizonsByGroup = {};
  shownColumns().forEach(function (col) {
    (col.data.premia || []).forEach(function (entry) {
      var group = premiaGroupOf(entry);
      if (!horizonsByGroup[group]) {
        horizonsByGroup[group] = [];
        groups.push(group);
      }
      var horizon = premiaHorizonOf(entry);
      if (horizonsByGroup[group].indexOf(horizon) < 0) horizonsByGroup[group].push(horizon);
    });
  });
  if (!groups.length) {
    html += band('Portfolio Risk Premia');
    html += pairRow('asset', ' ', function () { return ''; });
  }
  groups.forEach(function (group) {
    html += band(group);
    horizonsByGroup[group].forEach(function (horizon, i) {
      html += pairRow('asset' + (i % 2 ? ' alt' : ''), horizon, function (col) {
        var found = null;
        (col.data.premia || []).forEach(function (candidate) {
          if (premiaGroupOf(candidate) === group
              && premiaHorizonOf(candidate) === horizon) found = candidate;
        });
        return pairCells(found, found && found.kind === 'probability', false);
      }, 'rp:' + group + ':' + horizon);
    });
  });
  /* Same treatment as the allocation table: the category rows here come and
     go with the same exclusions, so they animate rather than being swapped
     under the reader. */
  animateRowChanges(el, function () {
    el.innerHTML = html + '</tbody>';
  });
}

/* ---- charts (spec 9.5): loading columns omitted, never drawn at zero ---- */

/* A palette slot belongs to a category by NAME, never by position
   (spec 6.6). The seven fixed assignments; an unexpected name falls back to
   the first unused slot with a console warning. */
var PALETTE_SLOT = {
  'Asset Allocation Strategies': 1,
  'Investment Grade Fixed Income': 2,
  'Other Fixed Income': 3,
  'Public Equity': 4,
  'Hedge Funds': 5,
  'Private Equity': 6,
  'Other Private Assets': 7
};
function slotFor(name) {
  if (PALETTE_SLOT[name]) return PALETTE_SLOT[name];
  console.warn('[proposalTool] no palette slot for category:', name);
  var taken = {};
  Object.keys(PALETTE_SLOT).forEach(function (k) { taken[PALETTE_SLOT[k]] = 1; });
  for (var s = 1; s <= 7; s++) { if (!taken[s]) { PALETTE_SLOT[name] = s; return s; } }
  PALETTE_SLOT[name] = ((Object.keys(PALETTE_SLOT).length - 1) % 7) + 1;
  return PALETTE_SLOT[name];
}
function catColor(name) { return 'var(--cat-' + slotFor(name) + ')'; }
/* a category's colour when it has a slot of its own, else null - for the
   console, which draws overlay categories that have none and must not have
   the fallback warn about them (D155) */
function knownCatColor(name) { return PALETTE_SLOT[name] ? catColor(name) : null; }

function renderCharts() {
  var section = document.querySelector('.sec.viz');
  var ready = shownColumns();
  if (section) section.hidden = !ready.length;   /* spec 10.2 */
  if (!ready.length) return;

  var key = document.getElementById('viz-key');
  if (key) {
    var legendNames = [];
    var seen = {};
    ready.forEach(function (col) {
      col.data.categories.forEach(function (c) {
        if (!seen[c.name]) { seen[c.name] = 1; legendNames.push(c.name); }
      });
    });
    var order = opt('categories', []);
    legendNames.sort(function (a, b) { return order.indexOf(a) - order.indexOf(b); });
    key.innerHTML = legendNames.map(function (name) {
      return '<i><span class="sw" style="background:' + catColor(name) + '"></span>'
        + esc(name) + '</i>';
    }).join('');
  }
  var key2 = document.getElementById('viz-key2');
  if (key2) {
    key2.innerHTML = '<i><span class="sw" style="background:#16243A;border-radius:50%"></span>'
      + 'Proposed</i><i><span class="sw" style="background:#1F5FBF;border-radius:50%"></span>Comparison</i>'
      + '<i class="viz-note">Marks match the table’s columns</i>';
  }

  var comp = document.getElementById('viz-comp');
  if (comp) {
    /* Vertical: one column per portfolio, categories stacked bottom-up. The
       axis moves to the left and the portfolio names run along the bottom, so
       each name gets a full column of width instead of a fixed left-hand
       gutter - which is what began truncating them once the risk level moved
       into the name. */
    /* The viewBox is CONSTANT. The svg is width:100% with height:auto, so its
       height is the card's width times H/W - a viewBox that narrowed as
       portfolios were removed made the card taller in exact proportion, and a
       single portfolio blew it up to several times its height. The frame stays
       500x302 whatever n is; the columns are laid out inside it. */
    var n = ready.length;
    var W = 500, padL = 46, padR = 14, top = 10, padB = 42, plotH = 250;
    var H = top + plotH + padB;
    var slot = (W - padL - padR) / n;
    /* Capped, so one portfolio is a column and not a slab, and floored so four
       stay legible. Each column is centred in its own slot. */
    var colW = Math.max(30, Math.min(96, slot - 28));
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Allocation by '
      + 'category for ' + n + ' portfolio' + (n > 1 ? 's' : '')
      + '. The allocation table carries the exact values.">';
    [0, 25, 50, 75, 100].forEach(function (t) {
      var y = top + plotH - plotH * t / 100;
      svg += '<line class="gl" x1="' + padL + '" y1="' + y + '" x2="' + (W - padR)
        + '" y2="' + y + '"/>'
        + '<text class="tick" x="' + (padL - 8) + '" y="' + (y + 4)
        + '" text-anchor="end">' + t + '%</text>';
    });
    ready.forEach(function (col, i) {
      var x = padL + i * slot + (slot - colW) / 2;
      var y = top + plotH;                       /* stack upward from the axis */
      col.data.categories.forEach(function (category) {
        var v = category.weightPct;
        if (!isFinite(v) || v < 0.05) return;
        var h = plotH * v / 100;
        y -= h;
        svg += '<rect class="seg" x="' + x + '" y="' + y + '" width="' + colW
          + '" height="' + Math.max(0.5, h) + '" rx="' + (h > 6 ? 3 : 0)
          + '" fill="' + catColor(category.name) + '"'
          + ' data-tip="' + esc(headerName(col.key) + ' \u00b7 ' + category.name) + '|'
          + v.toFixed(1) + '%"></rect>';
        if (h > 15) {
          svg += '<text class="lbl" x="' + (x + colW / 2) + '" y="' + (y + h / 2 + 4)
            + '" text-anchor="middle">' + v.toFixed(0) + '%</text>';
        }
      });
      /* The name sits under its own column, wrapped onto a second line rather
         than truncated - the long risk-level labels need the room. */
      var label = headerName(col.key);
      var words = label.split(' ');
      var line1 = words.shift();
      while (words.length && (line1 + ' ' + words[0]).length <= 14) line1 += ' ' + words.shift();
      var line2 = words.join(' ');
      if (line2.length > 16) line2 = line2.slice(0, 15) + '\u2026';
      svg += '<text class="plbl" x="' + (x + colW / 2) + '" y="' + (top + plotH + 18)
        + '" text-anchor="middle">' + esc(line1) + '</text>';
      if (line2) {
        svg += '<text class="plbl" x="' + (x + colW / 2) + '" y="' + (top + plotH + 31)
          + '" text-anchor="middle">' + esc(line2) + '</text>';
      }
    });
    comp.innerHTML = svg + '</svg>';
  }

  var rr = document.getElementById('viz-rr');
  if (rr) {
    var W2 = 460, H2 = 300, pL = 46, pB = 38, pT = 14, pR = 18;
    var vols = ready.map(function (c) { return c.data.metrics.volatilityPct; });
    var rets = ready.map(function (c) { return c.data.metrics.estimatedReturnPct; });
    var vlo = Math.min.apply(null, vols) - 1.2, vhi = Math.max.apply(null, vols) + 1.2;
    var rlo = Math.min.apply(null, rets) - 0.5, rhi = Math.max.apply(null, rets) + 0.5;
    if (vhi - vlo < 1) { vlo -= 1; vhi += 1; }
    if (rhi - rlo < 0.5) { rlo -= 0.5; rhi += 0.5; }
    var X = function (v) { return pL + (W2 - pL - pR) * (v - vlo) / (vhi - vlo); };
    var Y = function (r) { return H2 - pB - (H2 - pB - pT) * (r - rlo) / (rhi - rlo); };
    var svg2 = '<svg viewBox="0 0 ' + W2 + ' ' + H2 + '" role="img" aria-label="Estimated mean '
      + 'return against volatility for ' + ready.length + ' portfolio'
      + (ready.length > 1 ? 's' : '') + '. Exact values are in the metrics band of the '
      + 'allocation table.">';
    var i;
    for (i = 0; i <= 4; i++) {
      var xv = vlo + (vhi - vlo) * i / 4, gx = X(xv);
      svg2 += '<line class="gl" x1="' + gx + '" y1="' + pT + '" x2="' + gx + '" y2="' + (H2 - pB) + '"/>'
        + '<text class="tick" x="' + gx + '" y="' + (H2 - pB + 15) + '" text-anchor="middle">'
        + xv.toFixed(1) + '</text>';
    }
    for (i = 0; i <= 4; i++) {
      var yv = rlo + (rhi - rlo) * i / 4, gy = Y(yv);
      svg2 += '<line class="gl" x1="' + pL + '" y1="' + gy + '" x2="' + (W2 - pR) + '" y2="' + gy + '"/>'
        + '<text class="tick" x="' + (pL - 8) + '" y="' + (gy + 3.5) + '" text-anchor="end">'
        + yv.toFixed(1) + '</text>';
    }
    svg2 += '<line class="ax" x1="' + pL + '" y1="' + (H2 - pB) + '" x2="' + (W2 - pR) + '" y2="' + (H2 - pB) + '"/>'
      + '<line class="ax" x1="' + pL + '" y1="' + pT + '" x2="' + pL + '" y2="' + (H2 - pB) + '"/>'
      + '<text class="tick" x="' + ((pL + W2 - pR) / 2) + '" y="' + (H2 - 6) + '" text-anchor="middle">'
      + 'Volatility %</text>'
      + '<text class="tick" transform="translate(12,' + ((pT + H2 - pB) / 2) + ') rotate(-90)"'
      + ' text-anchor="middle">Estimated mean return %</text>';
    /* Each portfolio is its column's mark in a disc (D1): P, then 1, 2, 3. A
       name floated over each dot collided as soon as two portfolios sat close -
       and the commonest comparison, a book against its own ex-RAs, sits very
       close. A disc that would cover another is moved aside on a short leader
       instead, its true point left where it belongs, so nothing is misplaced
       to be made legible. A line from the proposed portfolio to each
       comparison draws the direction of the trade. */
    var marks = ready.map(function (col) {
      var at = state.columns.indexOf(col);
      return { col: col, at: at, x: X(col.data.metrics.volatilityPct), y: Y(col.data.metrics.estimatedReturnPct) };
    });
    var prop = marks.filter(function (m) { return m.at === 0; })[0] || null;
    var placed = [];
    var R = 9, GAP = 2 * R + 2;
    var tries = [[0, 0], [22, 0], [-22, 0], [0, -22], [0, 22], [16, -16], [-16, -16], [16, 16], [-16, 16]];
    /* the proposed portfolio takes its own point first; the others move round it */
    marks.slice().sort(function (a, b) { return a.at - b.at; }).forEach(function (m) {
      for (var t = 0; t < tries.length; t++) {
        var bx = m.x + tries[t][0], by = m.y + tries[t][1];
        var clash = placed.some(function (q) { return Math.abs(q.bx - bx) < GAP && Math.abs(q.by - by) < GAP; });
        var inside = bx > pL + R && bx < W2 - pR - R && by > pT + R && by < H2 - pB - R;
        if (!clash && (inside || t === 0)) { m.bx = bx; m.by = by; break; }
      }
      if (m.bx === undefined) { m.bx = m.x; m.by = m.y; }
      placed.push(m);
    });
    if (prop) {
      marks.forEach(function (m) {
        if (m === prop) return;
        svg2 += '<line class="rr-link" x1="' + prop.x + '" y1="' + prop.y + '" x2="' + m.x + '" y2="' + m.y + '"/>';
      });
    }
    marks.forEach(function (m) {
      var isBase = (m.at === 0), fill = isBase ? '#16243A' : '#1F5FBF';
      if (m.bx !== m.x || m.by !== m.y) {
        svg2 += '<line class="rr-lead" x1="' + m.x + '" y1="' + m.y + '" x2="' + m.bx + '" y2="' + m.by + '"/>'
          + '<circle cx="' + m.x + '" cy="' + m.y + '" r="2.5" fill="' + fill + '"/>';
      }
      svg2 += '<circle class="dot" cx="' + m.bx + '" cy="' + m.by + '" r="' + R + '" fill="' + fill + '"'
        + ' data-tip="' + esc(headerName(m.col.key)) + '|vol '
        + m.col.data.metrics.volatilityPct.toFixed(2) + '% · return '
        + m.col.data.metrics.estimatedReturnPct.toFixed(2) + '%"></circle>'
        + '<text class="rr-mark" x="' + m.bx + '" y="' + (m.by + 3.6) + '" text-anchor="middle">'
        + columnMark(m.at) + '</text>';
    });
    rr.innerHTML = svg2 + '</svg>';
  }
}

/* The provenance line is no longer printed. dataInfo still rides the schema
   (D4) and still tells the bake apart from the live path when something has to
   be diagnosed - it is just not on screen. */
/* Shown while anything is resolving or rebuilding. Purely visual - the live
   regions already announce readiness and failure, so an assistive-tech user
   is told what happened rather than that a spinner span exists. */
/* The stage headings and the risk dashboard stay out of the way until there
   is something under them. On arrival the document is one panel saying what to
   do; a title and a standfirst above an empty page are just furniture. */
var stageShown = false;

function renderStageChrome() {
  var base = state.columns[0] || null;
  /* Whether there is something to SHOW, not whether it has finished
     resolving. A re-resolve leaves the previous figures in place, so judging
     on status alone hid the headings and the risk dashboard mid-change - the
     page collapsing and springing back - and then re-armed the reveal, so the
     whole document faded in again on every exclusion toggle. */
  var show = state.phase === 'workspace'
    && !!(base && (base.status === 'ready' || base.data));
  var heads = document.querySelectorAll('#view-aa .sec-head.stage-head');
  for (var i = 0; i < heads.length; i++) heads[i].hidden = !show;
  var risk = document.getElementById('risk');
  if (risk) {
    var section = risk.closest('.sec');
    if (section) section.hidden = !show;
  }

  /* The first time there is something to show, the whole document arrives as
     one movement rather than section by section. Every part of it is already
     at its final size when the fade starts, so nothing pushes anything else
     down the page. */
  var view = document.getElementById('view-aa');
  if (view && show && !stageShown) {
    stageShown = true;
    view.classList.remove('doc-reveal');
    void view.offsetWidth;                   /* restart if it is still running */
    view.classList.add('doc-reveal');
    window.setTimeout(function () { view.classList.remove('doc-reveal'); }, 1400);
  }
  /* Re-arm only for a genuinely new scenario - no columns at all, or back on
     the landing. A column that is merely re-resolving must not reveal twice. */
  if (state.phase !== 'workspace' || !state.columns.length) stageShown = false;
}

function renderResolving() {
  var el = document.getElementById('resolving'); if (!el) return;
  var busy = state.phase === 'workspace'
    && (state.rebuilding
        || state.columns.some(function (c) { return c.status === 'loading'; }));
  el.hidden = !busy;
}

function renderFooter() {
  var el = document.getElementById('doc-foot'); if (!el) return;
  el.hidden = true;
  el.textContent = '';
}

/* ---- mandate dialog render ---------------------------------------------- */

function renderDialog() {
  var host = document.getElementById('mandateDialog'); if (!host) return;
  if (!draft) { host.innerHTML = ''; host.hidden = true; if (!account) setBackgroundInert(false); return; }
  host.hidden = false;
  setBackgroundInert(true);
  var first = (state.phase === 'landing');
  var options = '';
  var listOpen = draft.open && ((draft.q || '').trim().length >= 2);
  if (listOpen) {
    if (draft.searching) {
      options = '<li class="combo-opt none" role="option" aria-disabled="true" '
        + 'aria-selected="false">Searching…</li>';
    } else if (draft.matches && draft.matches.length) {
      options = draft.matches.map(function (name, i) {
        return '<li role="option" id="pwa-o' + i + '" class="combo-opt'
          + (i === draft.idx ? ' on' : '') + '" aria-selected="' + (i === draft.idx) + '">'
          + esc(name) + '</li>';
      }).join('');
    } else if (draft.matches) {
      options = '<li class="combo-opt none" role="option" aria-disabled="true" '
        + 'aria-selected="false">No advisor matches “' + esc(draft.q) + '”.</li>';
    }
  }
  var err = draft.err;
  function invalid(id) {
    return (err && err.field === id) ? ' aria-invalid="true" aria-describedby="mderr"' : '';
  }
  var dis = draft.saving ? ' disabled' : '';
  host.innerHTML =
      '<div class="scrim" data-scrim></div>'
    + '<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dlgTitle">'
    + '<button type="button" class="dlg-close" id="dlgclose" aria-label="Close">×</button>'
    + '<h2 id="dlgTitle"' + (first ? ' class="dlg-shout"' : '') + '>'
    + (first ? 'Start a proposal' : 'Edit mandate') + '</h2>'
    + '<div class="field"><label for="mdtop">Top Account Size ($AUS)</label>'
    + '<input type="text" id="mdtop" inputmode="numeric" autocomplete="off" value="'
    + (draft.top ? esc(money(draft.top)) : '') + '"' + invalid('mdtop')
    + (draft.saving ? ' disabled' : '') + '></div>'
    + '<div class="field"><label for="mdsize">Mandate size</label>'
    + '<input type="text" id="mdsize" inputmode="numeric" autocomplete="off" value="'
    + (draft.size ? esc(money(draft.size)) : '') + '"' + invalid('mdsize')
    + (draft.saving ? ' disabled' : '') + '>'
    + '<span class="field-hint">Minimum '
    + money(opt('rules.mandateFloor', 5000000)) + '</span></div>'
    + '<div class="field combo"><label for="mdpwa">Primary PWA</label>'
    + '<input type="text" id="mdpwa" role="combobox" autocomplete="off" spellcheck="false"'
    + ' aria-expanded="' + listOpen + '" aria-controls="pwa-list" aria-autocomplete="list"'
    + (draft.idx >= 0 ? ' aria-activedescendant="pwa-o' + draft.idx + '"' : '')
    + ' value="' + esc(draft.q) + '"' + invalid('mdpwa') + (draft.saving ? ' disabled' : '') + '>'
    + (listOpen && !draft.searching && draft.matches && draft.matches.length
        ? '<span class="combo-count">' + draft.matches.length + ' found</span>' : '')
    + '<ul id="pwa-list" role="listbox" aria-label="Matching advisors" class="combo-list"'
    + (options ? '' : ' hidden') + '>' + options + '</ul>'
    + (draft.open && (draft.q || '').trim().length < 2
        ? '<span class="field-hint">Type at least two characters.</span>' : '')
    + '</div>'
    + (err ? '<p class="md-err" id="mderr" role="alert">' + esc(err.msg) + '</p>' : '')
    /* Both cards carry the same pair, split to the card's two edges: the way
       on at the left, where the fields above it start, and Cancel apart from
       it at the right, so the two are never read as one control. The account
       opening request used to be a third button on this line, where its only
       neighbours were Continue and Cancel - a terminal action on a delivered
       proposal sitting among the controls for starting one (D107). */
    + '<div class="dlg-actions split">'
    + '<button type="button" class="btn btn-primary" id="dlgsave"' + dis + '>'
    + (draft.saving ? 'Working…' : (first ? 'Continue' : 'Save mandate')) + '</button>'
    + '<button type="button" class="btn btn-ghost" id="dlgcancel"' + dis + '>Cancel</button></div>'
    /* Below the rule, and only on the landing card: the other job someone may
       have come for, named by its precondition so it cannot be read as a
       second way to start. The landing page carries the same door (D107). */
    + (first
        ? '<p class="dlg-aside">Proposal already delivered? '
          + '<button type="button" class="btn-inline" id="dlgaccount"' + dis + '>'
          + 'Open an account against it</button></p>'
        : '')
    + '</div>';
}

function setBackgroundInert(on) {
  ['.rail', 'main.shell'].forEach(function (selector) {
    var node = document.querySelector(selector);
    if (node && 'inert' in node) node.inert = on;
  });
}

/* the cards that hold the page while they are open */
var DIALOG_HOSTS = ['mandateDialog', 'accountDialog', 'feeDialog', 'priceDialog', 'customDialog',
                    'chargesDialog', 'cslDialog', 'cpDialog', 'greetDialog', 'repoDialog',
                    'custodyDialog', 'noticeDialog'];
function anyDialogOpen(except) {
  return DIALOG_HOSTS.some(function (id) {
    var host = id !== except && document.getElementById(id);
    return !!(host && !host.hidden);
  });
}

/* =============================================================================
   The custodian (D195). Asked for only under PMG ESG (options.custodyTypes):
   what ESG means differs by jurisdiction, so the funds a client may own -
   the sleeve editions served - follow the custodian, one of GSI, GSBZ, GSLU
   and GSCO (options.entities). The type is chosen without one; its card
   opens once the PWA has moved on to the implementation step, before
   anything is picked from that type's library. Continue stays on the step;
   Cancel goes back to the allocation step. The rail's Mandate tier shows the
   custodian, with Change. Rendered into #custodyDialog.
   ========================================================================== */
var custody = null;           /* { variant, chosen, err, saving, opener, then, cancel } */

/* *then*, when given, runs once the custodian is saved, and *cancel* once
   the card is closed without one: the implementation step it was asked on
   stays, or is left */
function openCustodyDialog(variant, opener, then, cancel) {
  if (!canEdit()) return;
  custody = { variant: variant, chosen: custodian(), err: null, saving: false,
              opener: opener || document.activeElement, then: then || null, cancel: cancel || null };
  renderCustodyDialog();
  var first = document.querySelector('#custodyDialog input[name="cust"]:checked')
    || document.querySelector('#custodyDialog input[name="cust"]');
  if (first) first.focus();
}

function closeCustodyDialog() {
  var opener = custody && custody.opener, cancel = custody && custody.cancel;
  custody = null;
  renderCustodyDialog();
  /* the type select shows the type in force again */
  if (cancel) { cancel(); return; }
  refresh();
  var back = opener && opener.isConnected && !opener.id && opener !== document.body ? opener
    : document.getElementById(opener && opener.id ? opener.id : 'bpv');
  if (back && back.focus) back.focus();
}

function renderCustodyDialog() {
  var host = document.getElementById('custodyDialog'); if (!host) return;
  if (!custody) {
    host.innerHTML = ''; host.hidden = true;
    if (!anyDialogOpen('custodyDialog')) setBackgroundInert(false);
    return;
  }
  host.hidden = false;
  setBackgroundInert(true);
  var list = opt('options.entities', []) || [];
  var dis = custody.saving ? ' disabled' : '';
  host.innerHTML = '<div class="scrim" data-custscrim></div>'
    + '<div class="dialog cust" role="dialog" aria-modal="true" aria-labelledby="custTitle" aria-describedby="custLede">'
    + '<button type="button" class="dlg-close" id="custclose" aria-label="Close"' + dis + '>×</button>'
    + '<p class="cust-eyebrow">' + esc(custody.variant) + '</p>'
    + '<h2 id="custTitle">Choose the custodian</h2>'
    + '<p class="cust-lede" id="custLede">The funds a client may own under ' + esc(custody.variant)
    + ' depend on where the account is held: what counts as ESG differs by jurisdiction. The sleeves '
    + 'and products offered in the implementation follow the custodian you choose.</p>'
    + '<fieldset class="cust-list"><legend>Custodian</legend>'
    + list.map(function (e) {
        return '<label class="cust-opt"><input type="radio" name="cust" value="' + esc(e.id) + '"'
          + (e.id === custody.chosen ? ' checked' : '') + dis
          + (custody.err ? ' aria-describedby="custerr"' : '') + '><span>' + esc(e.name) + '</span></label>';
      }).join('')
    + '</fieldset>'
    + (custody.err ? '<p class="md-err" id="custerr" role="alert">' + esc(custody.err) + '</p>' : '')
    + '<div class="dlg-actions split">'
    + '<button type="button" class="btn btn-primary" id="custsave"' + dis + '>'
    + (custody.saving ? 'Working…' : 'Continue') + '</button>'
    + '<button type="button" class="btn btn-ghost" id="custcancel"' + dis + '>Cancel</button></div>'
    + '</div>';
}

async function commitCustody() {
  if (!custody || custody.saving) return;
  if (!custody.chosen) {
    custody.err = 'Choose the custodian.';
    renderCustodyDialog();
    var first = document.querySelector('#custodyDialog input[name="cust"]'); if (first) first.focus();
    return;
  }
  var variant = custody.variant, entity = custody.chosen;
  if (variant !== state.variant) {
    /* the type and its custodian go to the server in one write */
    custody = null;
    renderCustodyDialog();
    await setVariant(variant, entity);
    var sel = document.getElementById('bpv'); if (sel) sel.focus();
    return;
  }
  var moved = custodian() !== entity;
  var mandate = Object.assign({}, state.mandate, { entity: entity });
  custody.saving = true; custody.err = null;
  renderCustodyDialog();
  try {
    await trackWrite(apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mandate: mandate })
    }));
  } catch (err) {
    custody.saving = false;
    custody.err = (err && err.message) || 'Could not save the custodian.';
    renderCustodyDialog();
    return;
  }
  state.mandate = mandate;
  var opener = custody.opener, then = custody.then;
  custody = null;
  renderCustodyDialog();
  /* another custodian may hold other editions and exclude other products
     (D193): every library is read again at it */
  if (moved) {
    state.sleeveLib = {}; state.customLib = {};
    if (state.cpLib) state.cpLib = { key: '', status: 'idle', data: null, error: null };
  }
  await fetchSchema();
  announce('polite', 'Custodian set to ' + entityName(entity) + '.');
  if (then) {
    then();
    /* back to what was pressed to move on, unless the step has put focus
       somewhere itself (its greeting) */
    var from = opener && (opener.isConnected ? opener : opener.id && document.getElementById(opener.id));
    var free = !document.activeElement || document.activeElement === document.body;
    if (free && from && from.focus) from.focus();
    return;
  }
  refresh();
  var back = document.getElementById(opener && opener.id ? opener.id : 'custchange');
  if (back && back.focus) back.focus();
}

/* The console has closed: what an admin may have changed there - the
   sleeves and their editions, the notices - is read again, so
   the page behind it is not left showing the lists it had when the console
   opened. */
async function afterRepository() {
  if (state.phase !== 'workspace' || !state.scenarioId) return;
  state.sleeveLib = {}; state.customLib = {};
  if (state.cpLib) state.cpLib = { key: '', status: 'idle', data: null, error: null };
  await fetchSchema();
  refresh();
}

/* =============================================================================
   The notices (D199). The desk's words about what a proposal may hold, kept
   on the console's Notices tab and served as options.notices: each about
   sleeve categories, product categories (a product's own, served as
   ownCategory), product asset classes and particular products. When a
   sleeve or a product one matches is attached - a library sleeve, a custom
   sleeve, a product added to a custom portfolio - a card shows the notice
   before it goes in, one notice after another where several are raised.
   A notice may ask for its tick box to be ticked before Confirm. Confirm
   records who, when and the words ticked; Cancel records the decline and
   leaves what raised it off. Once per proposal: a notice confirmed in this
   proposal does not ask again. The download needs every notice the
   proposal raises confirmed, so one raised some other way - saved before,
   or a notice made since - says so in the rail, with Read.
   ========================================================================== */
var noticeCard = null;   /* { notice, queue, category, sleeve, held, proceed, cancel, opener, ticked, saving, err } */

function noticeRecord(id) { return (state.notices || {})[id] || null; }
function noticeConfirmed(id) { var r = noticeRecord(id); return !!(r && r.confirmed); }

/* whether *notice* is raised by a sleeve attached under *category* holding
   *products* (as served: ownCategory, assetClass, and the product each
   names below a minimum) */
function noticeHits(notice, category, products) {
  var a = notice.about || {};
  if (category && (a.sleeveCategories || []).indexOf(category) !== -1) return true;
  return (products || []).some(function (p) {
    return [p, p && p.alternative].some(function (x) {
      if (!x) return false;
      return (a.products || []).indexOf(x.productId) !== -1
        || (!!x.ownCategory && (a.productCategories || []).indexOf(x.ownCategory) !== -1)
        || (!!x.assetClass && (a.assetClasses || []).indexOf(x.assetClass) !== -1);
    });
  });
}
function noticesRaised(category, products) {
  return (opt('options.notices', []) || []).filter(function (n) { return noticeHits(n, category, products); });
}
/* the products among *products* a notice is raised by, by name */
function noticeHeld(notice, category, products) {
  var out = [];
  (products || []).forEach(function (p) {
    if (noticeHits(notice, '', [p]) && out.indexOf(p.name || p.productId) === -1) out.push(p.name || p.productId);
  });
  return out;
}

/* true when a card stands in the way: *proceed* runs once every notice
   raised is confirmed, and *cancel* on Cancel. False when nothing asks -
   nothing raised, all confirmed already, or nothing that may be recorded -
   and the caller goes on. */
function noticeGate(category, sleeve, products, proceed, cancel) {
  if (!canEdit() || !state.scenarioId) return false;
  var queue = noticesRaised(category, products).filter(function (n) { return !noticeConfirmed(n.id); });
  if (!queue.length) return false;
  openNoticeCard({ queue: queue, category: category, sleeve: sleeve, products: products || [],
                   proceed: proceed, cancel: cancel || null });
  return true;
}

/* the same for products added one by one, each under its own line - a
   custom portfolio's (D171): *items* is [{category, product}] */
function noticeGateFor(items, sleeve, proceed, cancel) {
  if (!canEdit() || !state.scenarioId) return false;
  var seen = {}, queue = [];
  (items || []).forEach(function (it) {
    noticesRaised(it.category, [it.product]).forEach(function (n) {
      if (!seen[n.id] && !noticeConfirmed(n.id)) { seen[n.id] = true; queue.push(n); }
    });
  });
  if (!queue.length) return false;
  openNoticeCard({ queue: queue, category: items.length === 1 ? items[0].category : '', sleeve: sleeve,
                   products: items.map(function (it) { return it.product; }), proceed: proceed, cancel: cancel || null });
  return true;
}

function openNoticeCard(card) {
  var queue = card.queue.slice(), first = queue.shift();
  noticeCard = Object.assign({ opener: document.activeElement }, card,
    { notice: first, queue: queue, ticked: false, saving: false, err: null });
  noticeCard.held = noticeHeld(first, card.category, card.products);
  announce('assertive', 'Notice: ' + first.name + '. ' + (first.tick ? 'Tick the box and confirm' : 'Confirm') + ' it to go on.');
  renderNoticeDialog();
  var body = document.getElementById('ntBody'); if (body) body.scrollTop = 0;
  var to = document.getElementById(first.tick ? 'nttick' : 'ntok'); if (to) to.focus();
}

/* the tick box moved: Confirm and its hint follow, without a redraw, so
   focus stays on the box */
function tickNotice(on) {
  if (!noticeCard || noticeCard.saving) return;
  noticeCard.ticked = !!on;
  var ok = document.getElementById('ntok'); if (ok) ok.disabled = !noticeCard.ticked;
  var why = document.getElementById('ntwhy'); if (why) why.textContent = noticeCard.ticked ? '' : 'Tick the box to confirm.';
}

/* one line of a notice: its words as typed, and **words** in bold */
function noticeLineHtml(line) {
  return esc(line).replace(/\*\*([^*]+?)\*\*/g, '<b>$1</b>');
}

/* a notice's body as the admin worded it: a blank line parts paragraphs,
   a block of lines numbered 1. 2. is a list, and **words** are bold */
function noticeBodyHtml(text) {
  return String(text || '').split(/\n\s*\n/).map(function (block) {
    var lines = block.split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
    if (!lines.length) return '';
    if (lines.every(function (l) { return /^\d+[.)]\s+/.test(l); })) {
      return '<ol>' + lines.map(function (l) { return '<li>' + noticeLineHtml(l.replace(/^\d+[.)]\s+/, '')) + '</li>'; }).join('') + '</ol>';
    }
    return '<p>' + lines.map(noticeLineHtml).join('<br>') + '</p>';
  }).join('');
}

function renderNoticeDialog() {
  var host = document.getElementById('noticeDialog'); if (!host) return;
  if (!noticeCard) {
    host.innerHTML = ''; host.hidden = true;
    if (!anyDialogOpen('noticeDialog')) setBackgroundInert(false);
    return;
  }
  host.hidden = false;
  setBackgroundInert(true);
  var n = noticeCard.notice, dis = noticeCard.saving ? ' disabled' : '';
  var waiting = !!n.tick && !noticeCard.ticked;
  var more = noticeCard.queue.length;
  host.innerHTML = '<div class="scrim" data-ntscrim></div>'
    + '<div class="dialog mkt hfn" role="alertdialog" aria-modal="true" aria-labelledby="ntTitle" aria-describedby="ntBody">'
    + '<p class="mkt-eyebrow">Notice' + (more ? ' · ' + (more + 1) + ' to confirm' : '') + '</p>'
    + '<h2 id="ntTitle">' + esc(n.title) + '</h2>'
    + ((noticeCard.held || []).length || noticeCard.sleeve
        ? '<p class="mkt-held hf-held">' + esc((noticeCard.sleeve ? noticeCard.sleeve : 'The proposal')
            + ((noticeCard.held || []).length ? ' holds ' + noticeCard.held.join(', ') : '')) + '</p>' : '')
    + '<div class="mkt-text hf-text" id="ntBody">' + noticeBodyHtml(n.body) + '</div>'
    /* the tick box, where the notice asks for one: Confirm waits for it */
    + (n.tick ? '<label class="hf-tick" for="nttick"><input type="checkbox" id="nttick"' + (noticeCard.ticked ? ' checked' : '') + dis + '>'
        + '<span>' + esc(n.tickWords) + '</span></label>' : '')
    + (noticeCard.err ? '<p class="md-err" role="alert">' + esc(noticeCard.err) + '</p>' : '')
    + '<div class="dlg-actions split hf-acts">'
    + '<button type="button" class="btn btn-primary" id="ntok"' + (noticeCard.saving || waiting ? ' disabled' : '')
    + (n.tick ? ' aria-describedby="ntwhy"' : '') + '>' + (noticeCard.saving ? 'Working…' : 'Confirm') + '</button>'
    + (n.tick ? '<p class="hf-why" id="ntwhy" aria-live="polite">' + (waiting ? 'Tick the box to confirm.' : '') + '</p>' : '')
    + '<button type="button" class="btn btn-ghost" id="ntcancel"' + dis + '>'
    + (noticeCard.proceed ? 'Cancel' : 'Not now') + '</button></div>'
    + '</div>';
}

/* back where the card was opened from, unless what it led to put focus
   somewhere itself */
function noticeFocusBack(card) {
  var free = !document.activeElement || document.activeElement === document.body;
  if (!free) return;
  var from = card.opener && card.opener !== document.body ? card.opener : null;
  var back = from && from.isConnected ? from : document.getElementById(from && from.id ? from.id : 'ntread');
  if (back && back.focus) back.focus();
}

function noticeWrite(action, card) {
  return trackWrite(apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ noticeAction: { notice: card.notice.id, action: action, ticked: !!card.ticked,
                                           category: card.category || '', sleeve: card.sleeve || '' } })
  }));
}

async function confirmNotice() {
  var card = noticeCard;
  if (!card || card.saving || (card.notice.tick && !card.ticked)) return;
  card.saving = true; card.err = null;
  renderNoticeDialog();
  try {
    var body = await noticeWrite('confirm', card);
    state.notices = (body && body.notices) || state.notices;
  } catch (err) {
    card.saving = false;
    card.err = 'The confirmation was not saved, so nothing is attached: ' + ((err && err.message) || String(err));
    renderNoticeDialog();
    var again = document.getElementById(card.notice.tick && !card.ticked ? 'nttick' : 'ntok'); if (again) again.focus();
    return;
  }
  announce('polite', 'Notice confirmed: ' + card.notice.name + '.');
  /* the next notice raised, if any, then what was asked for */
  var left = card.queue.filter(function (n) { return !noticeConfirmed(n.id); });
  if (left.length) {
    openNoticeCard(Object.assign({}, card, { queue: left, opener: card.opener }));
    return;
  }
  noticeCard = null;
  renderNoticeDialog();
  if (card.proceed) card.proceed(); else refresh();
  noticeFocusBack(card);
}

async function declineNotice() {
  var card = noticeCard;
  if (!card || card.saving) return;
  noticeCard = null;
  renderNoticeDialog();
  if (card.cancel) card.cancel();
  refresh();
  if (card.proceed) announce('polite', 'Not attached: the notice ' + card.notice.name + ' was not confirmed.');
  noticeFocusBack(card);
  /* the decline is recorded too, where an attach was asked for */
  if (!card.proceed) return;
  try {
    var body = await noticeWrite('decline', card);
    if (body && body.notices) state.notices = body.notices;
  } catch (err) {
    showAlert('error', 'The decline was not recorded: ' + ((err && err.message) || String(err)));
  }
}

/* the rail's line for them on the implementation step: the notices the
   proposal raises, confirmed by whom, or - not yet confirmed - Read.
   *raised* is [{notice, held}] */
function noticesNoteHtml(raised) {
  if (!(raised || []).length) return '';
  var open = raised.filter(function (r) { return !noticeConfirmed(r.notice.id); });
  var done = raised.filter(function (r) { return noticeConfirmed(r.notice.id); });
  return done.map(function (r) {
      var c = noticeRecord(r.notice.id).confirmed;
      return '<p class="hf-note is-done" role="note">' + esc(r.notice.name) + ': confirmed by ' + esc(c.by || 'someone')
        + (c.at ? ', ' + esc(String(c.at).replace('T', ' ').slice(0, 16)) : '') + '.</p>';
    }).join('')
    + (open.length ? '<div class="mkt-note hf-note" role="note"><b>' + (open.length === 1 ? 'Notice' : open.length + ' notices') + ' not confirmed</b>'
        + '<span>' + esc(open.map(function (r) { return r.notice.name; }).join('; ')) + '. The download needs '
        + (open.length === 1 ? 'it' : 'them') + '.</span>'
        + '<button type="button" class="btn-inline" id="ntread"' + (canEdit() ? '' : ' disabled') + '>Read</button></div>' : '');
}

/* the rail's line for it, in the Mandate tier, under the PWA: what is
   chosen, and the way to change it. On the allocation step it shows once
   one is chosen: until the PWA moves on, nothing asks for it. */
function custodyRowHtml() {
  if (!custodyNeeded(state.variant)) return '';
  var now = custodian();
  if (!now && state.step !== 'impl') return '';
  return '<dt>Custodian:</dt><dd class="cust-row' + (now ? '' : ' is-open') + '">'
    + (now ? esc(entityName(now)) : '<span class="cust-none">not chosen</span>')
    + ' <button type="button" class="btn-inline" id="custchange"' + (canEdit() ? '' : ' disabled') + '>'
    + (now ? 'Change' : 'Choose') + '</button></dd>';
}

/* =============================================================================
   Account opening request (D76). A form that fills itself from a Proposal
   UID (D75): the register answers the lookup, the proposal's parameters land
   in read-only fields, the user adds what the proposal cannot know, and
   Submit records one request against that proposal. Reached from the landing
   card's third button; rendered into #accountDialog.
   ========================================================================== */
var account = null;                 /* the account dialog's working copy */
var acOpener = null;
var acDraftBack = null;             /* the mandate the card held when it stood down */
var UID_PATTERN = /^pr_[0-9a-f]{12}$/;
var UID_SHAPE = 'A Proposal UID is pr_ followed by twelve letters or digits.';

/* The fields the user supplies. Option lists come from the schema
   (options.accountRequest), so the page never carries a list of its own; so
   does the one default, and so does a field's note. Every field is required;
   the custodian's name is asked for, and required, only for external
   custody. */
var AC_FIELDS = [
  { id: 'acname',   key: 'mandateName',        label: 'Mandate Name',         kind: 'text',   required: true },
  { id: 'acrel',    key: 'clientRelationship', label: 'Client Relationship',  kind: 'select', required: true, options: 'clientRelationships' },
  { id: 'actype',   key: 'accountType',        label: 'Account Type',         kind: 'select', required: true, options: 'accountTypes' },
  { id: 'acmifid',  key: 'mifidClass',         label: 'MiFID Class',          kind: 'select', required: true, options: 'mifidClasses' },
  { id: 'actax',    key: 'taxDomicile',        label: 'Tax Domicile',         kind: 'text',   required: true, placeholder: 'Country' },
  { id: 'acuknd',   key: 'ukRndClient',        label: 'UK RND Client',        kind: 'select', required: true, options: 'yesNo' },
  { id: 'acgs',     key: 'gsAffiliate',        label: 'GS Employee or Affiliate', kind: 'select', required: true, options: 'yesNo' },
  { id: 'acentity', key: 'offeringEntity',     label: 'Offering Entity',      kind: 'select', required: true, options: 'offeringEntities' },
  { id: 'acphasing', key: 'phasingTimeline',   label: 'Phasing Timeline',     kind: 'select', required: true, options: 'phasingTimelines', note: 'phasingNote' },
  { id: 'accustody', key: 'custodyPlatform',   label: 'Custody Platform',     kind: 'select', required: true, options: 'custodyPlatforms' },
  { id: 'accustodian', key: 'custodianName',   label: 'Custodian Name',       kind: 'text',   required: true, external: true }
];

/* The custodian's name belongs to external custody alone: hidden, it is
   neither asked for nor sent. */
function acAsked(spec) {
  return !spec.external
    || account.fields.custodyPlatform === opt('options.accountRequest.externalCustody', 'External COD');
}

/* A fresh set of answers: empty, but for the defaults the schema names. */
function acBlankFields() {
  var defaults = opt('options.accountRequest.defaults', {}) || {};
  var fields = {};
  AC_FIELDS.forEach(function (spec) { fields[spec.key] = defaults[spec.key] || ''; });
  return fields;
}

function acFieldId(key) {
  if (key === 'proposalId') return 'acuid';
  var spec = AC_FIELDS.filter(function (s) { return s.key === key; })[0];
  return spec ? spec.id : null;
}

function openAccountDialog(opener) {
  if (!canEdit()) return;
  acOpener = opener || dlgOpener;     /* the control to hand focus back to */
  /* One modal at a time, so the card stands down - but what the user typed
     into it is kept and put back when this dialog closes. Discarding it cost
     three filled fields for one mis-click (D107). */
  if (draft) {
    if (draft.searchTimer) clearTimeout(draft.searchTimer);
    if (draft.searchAbort) { try { draft.searchAbort.abort(); } catch (e) {} }
    draft.searchTimer = null; draft.searchAbort = null;
    draft.open = false; draft.idx = -1; draft.searching = false;
    acDraftBack = draft;
    draft = null;
    renderDialog();
  }
  account = {
    uid: '', status: 'idle',          /* 'idle'|'looking'|'found'|'notfound'|'malformed'|'error' */
    message: '', proposal: null, requests: [],
    fields: acBlankFields(),
    restrictions: [], chosen: [], rq: '',   /* the list on offer, the ids picked, the search (D203) */
    fees: {},                               /* a fee typed a row, where the proposal had none (D206) */
    err: null, dirty: false, submitting: false, receipt: null,
    seq: 0, timer: null
  };
  renderAccountDialog();
  var uid = document.getElementById('acuid');
  if (uid) uid.focus();
}

function closeAccountDialog() {
  if (!account) return;
  if (account.timer) clearTimeout(account.timer);
  account = null;
  /* Restore before rendering, so the background never comes out of inert for
     the one frame between the two dialogs. */
  if (acDraftBack) { draft = acDraftBack; acDraftBack = null; }
  renderAccountDialog();
  if (draft) {
    renderDialog();
    /* The button that opened this was destroyed with the card; the re-rendered
       card carries a fresh one, and that is where focus belongs. */
    var again = document.getElementById('dlgaccount');
    if (again) { again.focus(); return; }
  }
  var back = (acOpener && acOpener.isConnected) ? acOpener : document.getElementById('startbtn');
  if (back && !back.closest('[hidden]')) back.focus();
}

/* ---- the lookup: as soon as the box holds a well-formed UID, no button ---- */
function onUidInput(value) {
  account.uid = value.trim();
  account.err = null;
  if (account.timer) clearTimeout(account.timer);
  account.proposal = null; account.requests = [];
  var uid = account.uid.toLowerCase();
  if (UID_PATTERN.test(uid)) {
    account.status = 'looking'; account.message = '';
    var seq = ++account.seq;
    account.timer = window.setTimeout(function () { lookupProposal(uid, seq); }, 150);
  } else {
    ++account.seq;                    /* a reply to an earlier UID is stale now */
    account.status = (uid.length >= 15) ? 'malformed' : 'idle';
    account.message = (uid.length >= 15) ? UID_SHAPE : '';
  }
  preserveFocus(renderAccountDialog);
}

async function lookupProposal(uid, seq) {
  /* No abort: a lookup is a primary-key read, and a stale reply is simply
     ignored by the sequence check - cheaper than teaching apiFetch that an
     abort is not the service going down (D64). */
  var wasOpen = accountExpanded();
  try {
    var found = await apiFetch('/scenario/proposals/' + encodeURIComponent(uid));
    if (!account || account.seq !== seq) return;
    account.status = 'found'; account.message = '';
    account.proposal = found.proposal; account.requests = found.requests || [];
    /* the list on offer now; a pick no longer on it goes (D203) */
    account.restrictions = found.restrictions || [];
    account.chosen = account.chosen.filter(function (id) {
      return account.restrictions.some(function (r) { return r.id === id; });
    });
    if (account.requests.length) {
      announce('polite', 'This proposal already has an account opening request, '
        + account.requests[0].requestId + '.');
    } else {
      announce('polite', 'Proposal found: ' + found.proposal.primaryPwa + ', '
        + money(found.proposal.mandateSize) + '.');
    }
  } catch (err) {
    if (!account || account.seq !== seq) return;
    account.status = (err && err.status === 404) ? 'notfound'
      : (err && err.status === 422) ? 'malformed' : 'error';
    account.message = (err && err.message) || 'The lookup failed.';
  }
  /* The UID box is gone once the card grows, so focus moves on to the first
     thing left to do rather than falling back to the page. */
  if (!wasOpen && accountExpanded()) {
    renderAccountDialog();
    var first = document.getElementById(AC_FIELDS[0].id);
    if (first) first.focus();
  } else {
    preserveFocus(renderAccountDialog);
  }
}

function accountMissing() {
  var f = account.fields;
  return AC_FIELDS.filter(function (spec) {
    if (!spec.required || !acAsked(spec)) return false;
    var v = f[spec.key];
    return !(v && String(v).trim());
  }).concat(accountFeesMissing());
}

/* The SMA Details' rows (D206): each fee a proposal never priced is asked
   for in basis points, and a row without its code or benchmark stops the
   request until an admin gives it one. */
function accountSmaRows() {
  var d = account.proposal && account.proposal.smaDetails;
  return d ? d.rows || [] : [];
}
function accountSmaName(r) {
  return r.kind === 'mandate' ? (String(account.fields.mandateName || '').trim() || 'the mandate') : r.name;
}
function acFeeOk(text) {
  var t = String(text == null ? '' : text).trim().replace(/,/g, '').replace(/\s*bp$/i, '');
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return false;
  return parseFloat(t) <= 500;
}
function accountFeesMissing() {
  var d = account.proposal && account.proposal.smaDetails;
  if (!d || d.fees !== 'none') return [];
  return d.rows.filter(function (r) { return !acFeeOk((account.fees || {})[r.key]); })
    .map(function (r) { return { label: 'Management fee of ' + accountSmaName(r) }; });
}
function accountBlocked() {
  return accountSmaRows().filter(function (r) { return r.problem; });
}

function accountReady() {
  return !!(account && account.status === 'found' && !account.requests.length
            && !accountMissing().length && !accountBlocked().length);
}

function accountStatusText() {
  var a = account;
  if (a.status === 'idle') return 'Paste the UID to continue.';
  /* looking, refused or already requested: the line under the UID says it */
  if (!accountExpanded()) return '';
  var missing = accountMissing(), blocked = accountBlocked();
  /* one row named, with the first of its words; several counted - each
     says in red, in the table, what it lacks and who gives it */
  var off = blocked.length === 1
    ? 'Submit is off: ' + accountSmaName(blocked[0]) + ': ' + blocked[0].problem.split('. ')[0].replace(/\.?$/, '.')
    : blocked.length ? 'Submit is off: ' + blocked.length + ' rows of the SMA Details lack a code or a benchmark, marked in red.' : '';
  if (missing.length) {
    return missing.length + ' required field' + (missing.length === 1 ? '' : 's') + ' remaining: '
      + missing.map(function (m) { return m.label; }).join(', ') + '.' + (off ? ' ' + off : '');
  }
  if (off) return off;
  /* The PWA and mandate beside the UID: the last thing read before the
     proposal's one request is spent is the thing the user would check (D111). */
  var p = a.proposal;
  return 'Ready. Submitting records the request against ' + p.proposalId
    + ' (' + p.primaryPwa + ', ' + money(p.mandateSize) + ').';
}

/* The card opens on the UID alone and grows into the request only when the
   UID names a proposal that can still take one. Every other outcome - looking,
   malformed, not found, already requested - is answered in the small card,
   because there is nothing yet for the user to fill in (D111). */
function accountExpanded() {
  return !!(account && !account.receipt && account.status === 'found'
            && !account.requests.length);
}

/* Anything the user has given about the account, so a changed UID can say it
   was kept. A default does not count: the card wrote that. */
function accountHasDetails() {
  var f = account.fields, blank = acBlankFields();
  if (account.chosen.length) return true;
  return AC_FIELDS.some(function (spec) {
    return !!(f[spec.key] && String(f[spec.key]).trim()) && f[spec.key] !== blank[spec.key];
  });
}

/* Enter looks up at once rather than waiting out the debounce - and looks up
   again when the box already holds the UID, which is how someone who pressed
   Change gets back to the proposal they had. */
function lookupNow() {
  if (!account || account.submitting) return;
  var uid = (account.uid || '').toLowerCase();
  if (!UID_PATTERN.test(uid)) {
    if (uid) { account.status = 'malformed'; account.message = UID_SHAPE; preserveFocus(renderAccountDialog); }
    return;
  }
  if (account.timer) { clearTimeout(account.timer); account.timer = null; }
  account.status = 'looking'; account.message = '';
  preserveFocus(renderAccountDialog);
  lookupProposal(uid, ++account.seq);
}

/* Change: back to the small card with the UID selected for editing. What the
   user typed about the account is kept - the usual reason to change a UID is
   a typo or the wrong re-export, and the client is the same client (D111). */
function changeProposal() {
  if (!account || account.submitting) return;
  ++account.seq;                      /* a reply still in flight is stale now */
  if (account.timer) { clearTimeout(account.timer); account.timer = null; }
  account.status = 'idle'; account.message = ''; account.err = null;
  account.proposal = null; account.requests = [];
  renderAccountDialog();
  var box = document.getElementById('acuid');
  if (box) { box.focus(); box.select(); }
}

function whenText(iso) {
  if (!iso) return '';
  var d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  var date = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  var time = String(iso).slice(11, 16);
  return /^\d\d:\d\d$/.test(time) ? date + ' ' + time : date;
}

function renderAccountDialog() {
  var host = document.getElementById('accountDialog'); if (!host) return;
  if (!account) {
    host.innerHTML = ''; host.hidden = true;
    if (!draft) setBackgroundInert(false);
    return;
  }
  host.hidden = false;
  setBackgroundInert(true);
  /* The dialog element survives re-renders - only its contents are replaced -
     so the change between the small card and the grown one can be one
     movement, width and height together, rather than a jump (D111). */
  var box = host.querySelector('.dialog.account');
  var before = box ? box.getBoundingClientRect() : null;
  var wasOpen = box ? box.classList.contains('open') : false;
  if (!box) {
    host.innerHTML = '<div class="scrim" data-scrim></div>'
      + '<div class="dialog account" role="dialog" aria-modal="true" aria-labelledby="acTitle"></div>';
    box = host.querySelector('.dialog.account');
  }
  var open = accountExpanded();
  box.classList.toggle('open', open);
  box.innerHTML = '<button type="button" class="dlg-close" id="acclose" aria-label="Close">×</button>'
    + (account.receipt ? accountReceiptMarkup() : accountFormMarkup());
  if (before && wasOpen !== open && box.animate) {
    var after = box.getBoundingClientRect();
    box.style.overflow = 'hidden';
    var grow = box.animate([
      { width: before.width + 'px', height: before.height + 'px' },
      { width: after.width + 'px', height: after.height + 'px' }
    ], { duration: 260, easing: 'cubic-bezier(.4,0,.2,1)' });
    grow.onfinish = grow.oncancel = function () { box.style.overflow = ''; };
    var fresh = open && box.querySelector('.ac-grow');
    if (fresh && fresh.animate) {
      fresh.animate([{ opacity: 0 }, { opacity: 1 }],
                    { duration: 220, delay: 90, easing: 'ease-out', fill: 'backwards' });
    }
  }
}

function accountFormMarkup() {
  var a = account;
  var open = accountExpanded();
  var busy = a.submitting;
  var held = a.status === 'found' && a.requests.length;
  var ready = accountReady() && !busy;
  var actions;
  if (open) {
    actions = '<button type="button" class="btn btn-ghost" id="accancel"' + (busy ? ' disabled' : '') + '>Cancel</button>'
      + '<button type="button" class="btn btn-primary" id="acsubmit"' + (ready ? '' : ' disabled') + '>'
      + (busy ? 'Working…' : 'Submit request') + '</button>';
  } else if (held) {
    actions = '<button type="button" class="btn btn-link" id="accopy">Copy reference</button>'
      + '<button type="button" class="btn btn-ghost" id="accancel">Close</button>';
  } else {
    /* No disabled Submit waiting below one empty box: there is nothing to
       submit yet, so the small card offers only the way out. */
    actions = '<button type="button" class="btn btn-ghost" id="accancel">Cancel</button>';
  }
  return '<h2 id="acTitle" class="dlg-shout">Open an Account</h2>'
    + '<p class="dlg-sub">Start from the proposal the account is for.</p>'
    + (open ? '<div class="ac-grow">' + accountProposalMarkup(busy) + accountFieldsMarkup(busy)
              + accountRestrictionsMarkup(busy) + accountSmaMarkup(busy) + '</div>'
            : accountUidMarkup())
    + (open && a.err ? '<p class="md-err" id="acerr" role="alert">' + esc(a.err.msg) + '</p>' : '')
    + '<div class="dlg-actions split' + (open ? ' ac-foot' : '') + '">'
    + '<span class="dlg-status' + (ready ? ' ok' : '') + (open && accountBlocked().length ? ' off' : '') + '" id="acstatus" role="status">'
    + esc(accountStatusText()) + '</span>'
    + '<div class="dlg-grp">' + actions + '</div></div>';
}

/* The small card: the UID box and one line under it, which carries every
   answer the lookup can give short of a proposal to fill in. */
function accountUidMarkup() {
  var a = account;
  var looking = a.status === 'looking';
  var bad = a.status === 'notfound' || a.status === 'malformed' || a.status === 'error';
  var hint;
  if (looking) {
    hint = '<span class="field-hint busy" id="achint" role="status">Finding the proposal…</span>';
  } else if (a.status === 'found' && a.requests.length) {
    var r = a.requests[0];
    hint = '<p class="dlg-held" id="achint" role="status"><b>This proposal already has an account '
      + 'opening request.</b> <code>' + esc(r.requestId) + '</code>, raised by ' + esc(r.submittedBy)
      + ' on ' + esc(whenText(r.submittedAt)) + '. Each proposal takes one request; quote this '
      + 'reference to follow it up.</p>';
  } else if (bad) {
    hint = '<p class="md-err dlg-uiderr" id="achint" role="alert">' + esc(a.message || UID_SHAPE) + '</p>';
  } else {
    hint = '<span class="field-hint" id="achint">The end of the workbook\'s file name, or cell B1 '
      + 'of its Implementation sheet.'
      + (accountHasDetails() ? ' <b>Your account details are kept.</b>' : '') + '</span>';
  }
  return '<div class="field"><label for="acuid">Proposal UID<span class="req" aria-hidden="true">•</span></label>'
    + '<div class="ac-uidbox">'
    + '<input type="text" id="acuid" autocomplete="off" spellcheck="false" autocapitalize="off"'
    + ' placeholder="pr_…" aria-describedby="achint" aria-required="true" value="' + esc(a.uid) + '"'
    + (bad ? ' aria-invalid="true"' : '') + '>'
    + (looking ? '<span class="ac-spin" aria-hidden="true"></span>' : '')
    + '</div>' + hint + '</div>';
}

/* The proposal, once found: the UID locked into one line, then the terms as
   plain text rather than greyed boxes - information to check, not controls
   switched off (D111). Then what the proposal implements, which the server
   works out and the request records as shown: fixed, set apart, with no
   control in it (D202). */
function accountDigestMarkup(rows, cls, label) {
  return '<dl class="ac-digest' + (cls ? ' ' + cls : '') + '"' + (label ? ' aria-label="' + esc(label) + '"' : '')
    + '>' + rows.map(function (t) {
      return '<div' + (t[2] ? ' class="wide"' : '') + '><dt>' + esc(t[0]) + '</dt><dd>'
        + esc(t[1] || '—') + '</dd></div>';
    }).join('') + '</dl>';
}

function accountProposalMarkup(busy) {
  var p = account.proposal;
  var terms = [
    ['Primary PWA', p.primaryPwa],
    ['Base currency', p.currency],
    ['Mandate size', money(p.mandateSize)],
    ['Top account size', money(p.topAccountSize)],
    ['Implementation', p.variant],
    ['Fees', p.includeFees ? (p.feeSchedule || '') + ' · ' + (p.feeLevel || '') : 'No fees']
  ];
  var info = p.allocationInfo;
  return '<div class="ac-lock">'
    + '<span class="ac-tick" aria-hidden="true">✓</span>'
    + '<span class="ac-uid">' + esc(p.proposalId) + '</span>'
    + '<span class="ac-when">Exported ' + esc(whenText(p.exportedAt)) + ' by ' + esc(p.exportedBy)
    + (p.sequence > 1 ? ' · proposal #' + p.sequence + ' of its scenario' : '') + '</span>'
    + '<button type="button" class="btn-inline" id="acchange"' + (busy ? ' disabled' : '')
    + ' aria-label="Change the Proposal UID">Change</button></div>'
    + '<div class="dlg-sect">The proposal<span class="dlg-tag">from the register</span></div>'
    + accountDigestMarkup(terms)
    + (info
        ? '<div class="dlg-sect">Allocation info<span class="dlg-tag ac-fixed-tag">inferred · cannot be edited</span></div>'
          + accountDigestMarkup([
              ['Allocation Type', info.allocationType],
              ['Risk Profile', info.riskProfile],
              ['Currency Hedging Policy', info.hedgingPolicy],
              ['Tactical Tilt Implementation', info.tacticalTilt ? 'True' : 'False'],
              ['Equity Allocation Implementation', info.equityImplementation, true]
            ], 'ac-fixed', 'Allocation info, inferred from the proposal')
        : '');
}

function accountFieldsMarkup(busy) {
  var a = account;
  var errField = a.err && a.err.field;
  return '<div class="dlg-sect">The account</div><div class="dlg-grid">' + AC_FIELDS.filter(acAsked).map(function (spec) {
    var v = a.fields[spec.key];
    var label = '<label for="' + spec.id + '">' + esc(spec.label)
      + (spec.required ? '<span class="req" aria-hidden="true">•</span>' : '') + '</label>';
    /* A note sits behind an i beside the label: shown on hover, on focus or
       when pressed, and read with the control it describes (D202). */
    var note = spec.note && opt('options.accountRequest.' + spec.note, '');
    var noteId = spec.id + 'note';
    if (note) {
      label = '<div class="ac-lab">' + label
        + '<button type="button" class="ac-info" data-acnote="' + noteId + '" aria-label="About '
        + esc(spec.label) + '" aria-expanded="false" aria-controls="' + noteId + '">i</button>'
        + '<p class="ac-note" id="' + noteId + '" role="tooltip">' + esc(note) + '</p></div>';
    }
    var described = [errField === spec.id ? 'acerr' : '', note ? noteId : '']
      .filter(Boolean).join(' ');
    var common = ' id="' + spec.id + '" data-acf="' + spec.key + '"'
      + (spec.required ? ' aria-required="true"' : '')
      + (errField === spec.id ? ' aria-invalid="true"' : '')
      + (described ? ' aria-describedby="' + described + '"' : '')
      + (busy ? ' disabled' : '');
    var control;
    if (spec.kind === 'select') {
      var choices = opt('options.accountRequest.' + spec.options, []);
      control = '<select' + common + '><option value=""' + (v ? '' : ' selected') + '>Select…</option>'
        + choices.map(function (o) {
            return '<option value="' + esc(o) + '"' + (o === v ? ' selected' : '') + '>' + esc(o) + '</option>';
          }).join('') + '</select>';
    } else {
      control = '<input type="text" autocomplete="off"' + common
        + (spec.placeholder ? ' placeholder="' + esc(spec.placeholder) + '"' : '')
        + ' value="' + esc(v || '') + '">';
    }
    return '<div class="field' + (spec.wide ? ' wide' : '') + '">' + label + control
      + (spec.hint ? '<span class="field-hint">' + esc(spec.hint) + '</span>' : '') + '</div>';
  }).join('') + '</div>';
}

/* Investment restrictions (D203): any number of the desk's list, or none.
   What is picked shows as chips beneath the list, each with its way out -
   beneath, so a tick never moves the rows under the pointer; the list
   filters once it is long. The chips and the list redraw on their own, so
   the search box keeps its caret. */
var AC_SEARCH_FROM = 9;
function accountRestrictionsMarkup(busy) {
  var list = account.restrictions || [];
  var head = '<div class="dlg-sect">Investment Restrictions<span class="dlg-tag ac-fixed-tag">optional · pick any that apply</span></div>';
  if (!list.length) return head + '<p class="ac-rnone">No restrictions are on offer yet.</p>';
  return head + '<div class="ac-restr">'
    + (list.length >= AC_SEARCH_FROM
        ? '<input type="search" id="acrsearch" class="ac-rsearch" autocomplete="off" placeholder="Search the restrictions…"'
          + ' aria-label="Search the restrictions" aria-controls="acrlist" value="' + esc(account.rq) + '"' + (busy ? ' disabled' : '') + '>'
        : '')
    + '<div class="ac-rlist" id="acrlist" role="group" aria-label="Investment restrictions">' + accountRestrictionListMarkup(busy) + '</div>'
    + '<div class="ac-chips" id="acrchips">' + accountChipsMarkup(busy) + '</div></div>';
}

function accountChosen() {
  return (account.restrictions || []).filter(function (r) { return account.chosen.indexOf(r.id) !== -1; });
}

function accountChipsMarkup(busy) {
  var chosen = accountChosen();
  if (!chosen.length) return '<span class="ac-chosen">None chosen.</span>';
  return '<span class="ac-chosen">' + chosen.length + ' chosen:</span>' + chosen.map(function (r) {
    return '<span class="ac-chip">' + esc(r.name)
      + '<button type="button" class="ac-chipx" data-acrx="' + esc(r.id) + '" aria-label="Remove ' + esc(r.name) + '"'
      + (busy ? ' disabled' : '') + '>×</button></span>';
  }).join('');
}

function accountRestrictionListMarkup(busy) {
  var q = (account.rq || '').trim().toLowerCase();
  var shown = (account.restrictions || []).filter(function (r) {
    return !q || (r.name + ' ' + (r.description || '')).toLowerCase().indexOf(q) !== -1;
  });
  if (!shown.length) return '<p class="ac-rnone">Nothing on the list matches “' + esc(account.rq.trim()) + '”.</p>';
  return shown.map(function (r) {
    var id = 'acr-' + r.id, on = account.chosen.indexOf(r.id) !== -1;
    return '<label class="ac-ropt" for="' + esc(id) + '"><input type="checkbox" id="' + esc(id) + '" data-acr="' + esc(r.id) + '"'
      + (on ? ' checked' : '') + (busy ? ' disabled' : '') + '>'
      + '<span><b>' + esc(r.name) + '</b>' + (r.description ? '<small>' + esc(r.description) + '</small>' : '') + '</span></label>';
  }).join('');
}

/* SMA Details (D206): the mandate, then each separate account the
   implementation holds, every value worked out by the server - code,
   benchmark, fee and funding - but a fee the proposal never priced, typed
   here in basis points. A row missing its code or benchmark says why, and
   who can give it one. */
function accountSmaMarkup(busy) {
  var d = account.proposal && account.proposal.smaDetails;
  if (!d) return '';
  return '<div class="dlg-sect">SMA Details<span class="dlg-tag ac-fixed-tag">the mandate, then each SMA in the implementation · inferred</span></div>'
    + '<div class="ac-sma-wrap"><table class="ac-sma"><thead><tr><th scope="col">SMA · mandate code</th><th scope="col">Benchmark</th>'
    + '<th scope="col" class="n">Management fee</th><th scope="col" class="n">Funding size</th></tr></thead><tbody>'
    + d.rows.map(function (r, i) { return accountSmaRowMarkup(r, i, d.fees, busy); }).join('')
    + '</tbody></table></div>';
}

function acBp(v) { return (Math.round(v * 100) / 100).toString() + ' bp'; }

function accountSmaRowMarkup(r, i, kind, busy) {
  var mandate = r.kind === 'mandate';
  var typed = String(account.fields.mandateName || '').trim();
  var name = mandate ? (typed || 'The mandate') : r.name;
  var from = { listing: 'its listing', mandate: 'its SMA mandate', product: 'the product’s', proposal: 'the delivered proposal’s', table: 'MAP Benchmarks' }[r.benchmarkFrom] || '';
  var fee;
  if (kind === 'none') {
    var v = (account.fees || {})[r.key] || '';
    fee = '<span class="ac-bp"><input type="text" id="acfee' + i + '" data-acfee="' + esc(r.key) + '" inputmode="decimal" autocomplete="off"'
      + ' value="' + esc(v) + '" placeholder="0" aria-required="true" aria-label="' + esc('Management fee of ' + name + ', in basis points') + '"'
      + (v && !acFeeOk(v) ? ' aria-invalid="true"' : '') + (busy ? ' disabled' : '') + '><span>bp</span></span>'
      + '<small>not priced: 0 to 500</small>';
  } else if (r.feeFrom === 'n/a') {
    fee = 'N/A<small>one rate on the whole mandate</small>';
  } else {
    fee = (r.feeBp === null || r.feeBp === undefined ? '—' : esc(acBp(r.feeBp)))
      + '<small>' + (mandate ? 'the proposal’s, over the portfolio' : 'from the proposal') + '</small>';
  }
  return '<tr class="' + (r.problem ? 'blocked' : mandate ? 'mand' : '') + '">'
    + '<td><span class="ac-sma-nm' + (mandate && !typed ? ' unnamed' : '') + '"' + (mandate ? ' id="acsmaname"' : '') + '>' + esc(name) + '</span>'
    + '<small>' + (mandate ? (typed ? 'The mandate' : 'Named once the Mandate Name is given') : esc(r.category))
    + (r.code ? ' · <code>' + esc(r.code) + '</code>' : '') + '</small>'
    + (r.problem ? '<span class="ac-sma-gap">' + esc(r.problem) + '</span>' : '') + '</td>'
    + '<td>' + (r.benchmark ? esc(r.benchmark) + (r.benchmarkMnemonic ? ' · <code>' + esc(r.benchmarkMnemonic) + '</code>' : '')
        + (from ? '<small>' + esc(from) + '</small>' : '') : '<span class="ac-sma-none">—</span>') + '</td>'
    + '<td class="n">' + fee + '</td>'
    + '<td class="n">' + esc(money(r.funding))
    + (mandate && r.inSmas ? '<small>of which in SMAs: ' + esc(money(r.inSmas)) + '</small>' : '')
    + (r.initialFunding !== null && r.initialFunding !== undefined ? '<small>initially ' + esc(money(r.initialFunding)) + '</small>' : '')
    + '</td></tr>';
}

function patchAccountRestrictions(list) {
  var chips = document.getElementById('acrchips');
  if (chips) chips.innerHTML = accountChipsMarkup(account.submitting);
  var host = list && document.getElementById('acrlist');
  if (host) host.innerHTML = accountRestrictionListMarkup(account.submitting);
}

function chooseRestriction(id, on) {
  if (!account || account.submitting) return;
  account.chosen = account.chosen.filter(function (x) { return x !== id; });
  if (on) account.chosen.push(id);
  account.dirty = true;
  var r = (account.restrictions || []).filter(function (x) { return x.id === id; })[0];
  patchAccountRestrictions(false);
  var box = document.getElementById('acr-' + id);
  if (box) box.checked = on;
  if (r) announce('polite', r.name + (on ? ' chosen.' : ' taken off.'));
}

function accountReceiptMarkup() {
  var r = account.receipt;
  return '<div class="dlg-tick" aria-hidden="true">✓</div>'
    + '<h2 id="acTitle">Request submitted</h2>'
    + '<p class="dlg-receipt">Account opening request <code>' + esc(r.requestId) + '</code> for proposal '
    + '<code>' + esc(r.proposalId) + '</code> was recorded at ' + esc(whenText(r.submittedAt))
    + ' by ' + esc(r.submittedBy) + '. <b>Quote the request reference</b> in any follow-up; '
    + 'the Proposal UID stays with the proposal.</p>'
    + '<div class="dlg-actions split">'
    + '<button type="button" class="btn btn-link" id="accopy">Copy reference</button>'
    + '<button type="button" class="btn btn-primary" id="acdone">Done</button></div>';
}

/* A field's note stays up once pressed - a tap has no hover - until it is
   pressed again or Escape closes it, before Escape closes the card. A note
   Escape closed stays down while its i keeps the focus or the pointer, which
   would otherwise show it again at once. */
function toggleAccountNote(button) {
  var open = button.getAttribute('aria-expanded') !== 'true';
  closeAccountNote();
  var lab = button.closest('.ac-lab');
  lab.classList.remove('is-shut');
  if (!open) { lab.classList.add('is-shut'); return; }
  button.setAttribute('aria-expanded', 'true');
  lab.classList.add('is-open');
}

function closeAccountNote() {
  var shown = [].filter.call(document.querySelectorAll('#accountDialog .ac-lab:not(.is-shut)'),
    function (lab) {
      var note = lab.querySelector('.ac-note');
      return note && getComputedStyle(note).display !== 'none';
    })[0];
  if (!shown) return false;
  shown.classList.remove('is-open');
  shown.classList.add('is-shut');
  var button = shown.querySelector('[data-acnote]');
  if (button) button.setAttribute('aria-expanded', 'false');
  return true;
}

/* The footer alone re-renders while the user types: a full re-render would
   fight the caret in a textarea and drop a select mid-choice. */
function patchAccountFooter() {
  var status = document.getElementById('acstatus');
  var submit = document.getElementById('acsubmit');
  if (!status || !submit || !account) return;
  var ready = accountReady() && !account.submitting;
  status.textContent = accountStatusText();
  status.classList.toggle('ok', ready);
  status.classList.toggle('off', accountExpanded() && accountBlocked().length > 0);
  submit.disabled = !ready;
  var bad = document.querySelector('#accountDialog [data-acf][aria-invalid="true"]');
  if (bad) {
    bad.removeAttribute('aria-invalid');
    var kept = (bad.getAttribute('aria-describedby') || '').split(' ')
      .filter(function (id) { return id && id !== 'acerr'; }).join(' ');
    if (kept) bad.setAttribute('aria-describedby', kept); else bad.removeAttribute('aria-describedby');
  }
  var err = document.getElementById('acerr');
  if (err) err.remove();
}

function onAccountInput(e) {
  var t = e.target;
  if (!t || !account || account.receipt) return false;
  if (t.id === 'acuid') { onUidInput(t.value); return true; }
  if (t.id === 'acrsearch') { account.rq = t.value; patchAccountRestrictions(true); return true; }
  if (t.dataset && t.dataset.acfee) {
    account.fees[t.dataset.acfee] = t.value; account.dirty = true;
    if (acFeeOk(t.value) || !t.value) t.removeAttribute('aria-invalid'); else t.setAttribute('aria-invalid', 'true');
    patchAccountFooter();
    return true;
  }
  var key = t.dataset && t.dataset.acf;
  if (!key) return false;
  var spec = AC_FIELDS.filter(function (s) { return s.key === key; })[0];
  if (!spec) return false;
  var was = acAsked({ external: true });
  account.fields[key] = t.value;
  account.err = null; account.dirty = true;
  /* the mandate's row of the SMA Details is named as the mandate is (D206) */
  if (key === 'mandateName') {
    var row = document.getElementById('acsmaname');
    if (row) {
      row.textContent = String(t.value || '').trim() || 'The mandate';
      row.classList.toggle('unnamed', !String(t.value || '').trim());
      var small = row.nextElementSibling, sub = small && small.firstChild;
      if (sub && sub.nodeType === 3) {
        sub.nodeValue = (String(t.value || '').trim() ? 'The mandate' : 'Named once the Mandate Name is given')
          + (small.querySelector('code') ? ' · ' : '');
      }
    }
  }
  /* the custody platform decides whether the custodian is asked for, so a
     change to it that adds or takes away that field redraws the card */
  if (acAsked({ external: true }) !== was) preserveFocus(renderAccountDialog);
  else patchAccountFooter();
  return true;
}

async function submitAccountRequest() {
  if (!account || account.submitting || !accountReady()) return;
  account.submitting = true; account.err = null;
  renderAccountDialog();
  var body = { proposalId: account.proposal.proposalId };
  AC_FIELDS.forEach(function (spec) { body[spec.key] = acAsked(spec) ? account.fields[spec.key] : ''; });
  body.restrictions = accountChosen().map(function (r) { return r.id; });
  body.smaFees = account.fees;
  try {
    var made = await apiFetch('/scenario/account-requests', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (!account) return;
    account.submitting = false; account.dirty = false; account.receipt = made.request;
    renderAccountDialog();
    var done = document.getElementById('acdone');
    if (done) done.focus();
    announce('polite', 'Account opening request ' + made.request.requestId + ' recorded.');
  } catch (err) {
    if (!account) return;
    account.submitting = false;
    var field = err && err.body && err.body.field;
    var id = acFieldId(field);
    account.err = { field: id, msg: (err && err.message) || 'Could not submit the request.' };
    renderAccountDialog();
    var node = id ? document.getElementById(id) : null;
    if (node && !node.disabled) node.focus();
    /* the proposal has changed under us - gone, or now requested - or the
       SMA Details have since the lookup, so ask again */
    if ((field === 'proposalId' && (err.status === 404 || err.status === 422)) || field === 'smaDetails') {
      lookupProposal(account.proposal.proposalId, ++account.seq);
    }
  }
}

function copyRequestReference(button) {
  if (!account) return;
  /* the receipt's reference, or the one already on the proposal (D111) */
  var text = account.receipt ? account.receipt.requestId
    : (account.requests.length ? account.requests[0].requestId : '');
  if (!text) return;
  function done() {
    button.textContent = 'Copied';
    announce('polite', 'Reference ' + text + ' copied.');
    window.setTimeout(function () { if (button.isConnected) button.textContent = 'Copy reference'; }, 1600);
  }
  function fallback() {
    var area = document.createElement('textarea');
    area.value = text; area.setAttribute('readonly', '');
    area.style.position = 'fixed'; area.style.opacity = '0';
    document.body.appendChild(area); area.select();
    try { document.execCommand('copy'); } catch (e) { /* nothing to do */ }
    area.remove();
    done();
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, fallback);
  } else {
    fallback();
  }
}

function trapFocus(e, host) {
  if (!host || host.hidden) return;
  var focusables = host.querySelectorAll(
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), '
    + 'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])');
  if (!focusables.length) return;
  var firstNode = focusables[0], lastNode = focusables[focusables.length - 1];
  if (e.shiftKey && document.activeElement === firstNode) { e.preventDefault(); lastNode.focus(); }
  else if (!e.shiftKey && document.activeElement === lastNode) { e.preventDefault(); firstNode.focus(); }
}

/* ---- the service, and surviving it (D64) ---------------------------------
   apiFetch calls noteService on every request. Going down starts a backing-off
   poll of the schema endpoint - the frontend's own /health cannot be used: it
   returns {status:'ok'} unconditionally and never touches the backend, so it
   answers happily throughout an outage. Coming back re-boots in place. */
var RETRY_STEPS = [3, 5, 8, 13, 21, 30];      /* seconds, then the last repeats */
var retryTimer = null;

function retryDelay(step) {
  return RETRY_STEPS[Math.min(step, RETRY_STEPS.length - 1)] * 1000;
}

function noteService(ok, status) {
  if (ok) {
    if (state.service === 'down') {
      /* it answered: stop polling, and reload what the outage may have missed */
      state.service = 'up'; state.serviceSince = null; state.retryAt = null; state.retryStep = 0;
      stopRetry();
      announce('polite', 'The scenario service is back.');
      recover();
    }
    return;
  }
  if (state.service === 'down') return;        /* already known, keep the timer */
  state.service = 'down';
  state.serviceSince = Date.now();
  state.retryStep = 0;
  scheduleRetry();
  announce('assertive', 'The scenario service is not responding. The page is read only.');
  refresh();
}

function scheduleRetry() {
  stopRetry();
  var wait = retryDelay(state.retryStep);
  state.retryAt = Date.now() + wait;
  retryTimer = window.setTimeout(function () {
    state.retryStep += 1;
    attemptRecovery();
  }, wait);
  tickRetry();
}
function stopRetry() {
  if (retryTimer) { window.clearTimeout(retryTimer); retryTimer = null; }
  if (tickTimer) { window.clearInterval(tickTimer); tickTimer = null; }
}
var tickTimer = null;
function tickRetry() {
  if (tickTimer) window.clearInterval(tickTimer);
  tickTimer = window.setInterval(renderService, 1000);
}

/* One real request, not a health probe. Success flows back through apiFetch
   into noteService, which is what actually clears the state. */
async function attemptRecovery() {
  try { await apiFetch('/scenario/schema' + schemaQuery()); }
  catch (err) { if (state.service === 'down') scheduleRetry(); }
}

/* The service answered again. Re-fetch what the page is showing so a cached
   render is replaced by a live one; boot() covers both the workspace and the
   landing, and reads the scenario id back out of the URL. */
async function recover() {
  /* boot() rebuilds the workspace from the server and appends the columns it
     finds, so the restored ones have to go first or every column arrives
     twice - the cached copy beside its own refetch (D64). */
  state.fromCache = false;
  state.columns = [];
  state.schemaStatus = 'idle';
  try { await boot(); } catch (e) { /* boot reports its own failure */ }
  refresh();
}

/* ---- the cache that makes read-only possible ----------------------------
   The last good workspace, kept per scenario so a service that dies while a
   proposal is open leaves the proposal on screen. It holds only what the page
   needs to render: no more than the API already sent it. */
function snapshotKey(id) { return 'pt.snapshot.' + id; }

function saveSnapshot() {
  if (!state.scenarioId || state.phase !== 'workspace' || state.service === 'down') return;
  if (!state.columns.length || !state.columns.some(function (c) { return c.status === 'ready'; })) return;
  try {
    window.localStorage.setItem(snapshotKey(state.scenarioId), JSON.stringify({
      at: Date.now(),
      schema: state.schema,
      scenarioId: state.scenarioId,
      mandate: state.mandate, basis: state.basis, variant: state.variant,
      step: state.step, basisChosen: state.basisChosen, implSeen: state.implSeen,
      tacticalTilt: state.tacticalTilt, volPremium: state.volPremium,
      includeFees: state.includeFees, feeSchedule: state.feeSchedule, feeLevel: state.feeLevel,
      customFees: state.customFees,
      otherCharges: state.otherCharges,
      sleeves: state.sleeves,
      customSleeves: state.customSleeves,
      customPortfolio: state.customPortfolio,
      notices: state.notices,
      columns: state.columns.filter(function (c) { return c.status === 'ready'; })
    }));
  } catch (e) { /* private mode, or full: the page simply has no fallback */ }
}

function loadSnapshot(id) {
  if (!id) return null;
  try {
    var raw = window.localStorage.getItem(snapshotKey(id));
    if (!raw) return null;
    var snap = JSON.parse(raw);
    return (snap && snap.schema && (snap.columns || []).length) ? snap : null;
  } catch (e) { return null; }
}

/* Put a snapshot on screen and mark everything it implies: the workspace is
   real but read only, and says so. */
function restoreSnapshot(snap) {
  state.schema = snap.schema; state.schemaStatus = 'ready';
  state.scenarioId = snap.scenarioId;
  state.mandate = snap.mandate; state.basis = snap.basis; state.variant = snap.variant;
  state.step = snap.step || 'aa'; state.basisChosen = !!snap.basisChosen;
  state.implSeen = !!snap.implSeen;
  state.tacticalTilt = snap.tacticalTilt !== false;
  state.volPremium = snap.volPremium !== false;
  state.includeFees = !!snap.includeFees;
  state.feeSchedule = snap.feeSchedule || null; state.feeLevel = snap.feeLevel || null;
  state.customFees = snap.customFees || {};
  state.otherCharges = snap.otherCharges || {};
  state.sleeves = snap.sleeves || {};
  state.customSleeves = snap.customSleeves || {};
  state.customPortfolio = snap.customPortfolio || null;
  state.notices = snap.notices || {};
  state.columns = snap.columns || [];
  state.phase = 'workspace';
  state.fromCache = true;
}

/* what the two surfaces say about the wait */
function retrySeconds() {
  if (!state.retryAt) return null;
  return Math.max(0, Math.round((state.retryAt - Date.now()) / 1000));
}

function renderService() {
  var down = state.service === 'down';
  var secs = retrySeconds();
  var wait = secs === null ? '' : (secs > 0 ? 'Trying again in ' + secs + 's' : 'Trying again…');

  var banner = document.getElementById('degraded');
  if (banner) {
    /* the banner is for a page that survived - workspace or landing; with
       nothing at all to show the full page takes over instead */
    banner.hidden = !(down && !!state.schema);
    var r = document.getElementById('degraded-retry');
    if (r) r.textContent = wait;
  }
  var retry = document.getElementById('down-retry');
  if (retry) retry.textContent = secs === null ? 'Trying again automatically…' : wait;
  var safe = document.getElementById('down-safe');
  if (safe) {
    var id = state.scenarioId;
    safe.hidden = !id;
    if (id) safe.textContent = 'Your proposal is saved. It is kept for 24 hours '
      + 'and will be exactly as you left it.';
  }
  var reason = document.getElementById('schema-error-reason');
  if (reason) reason.textContent = state.schemaError || '';
}

/* ---- refresh ------------------------------------------------------------ */
/* ---- the way on (B4) ------------------------------------------------------
   The document used to end under the risk dashboard, with the only door to
   step 2 back at the top of it. The foot says what is being proposed and
   carries the way forward - through the step nav's own button, so whatever
   that does (the first visit's greeting, D97) happens here too. */
function renderNext() {
  var el = document.getElementById('aa-next'); if (!el) return;
  var base = state.columns[0] || null;
  if (state.phase === 'workspace' && cpActive()) {
    var held = (state.customPortfolio.products || []).length, others = state.columns.length;
    el.hidden = false;
    el.innerHTML = '<p class="aa-next-txt"><span>Proposing</span> <b>a custom portfolio, '
      + esc(riskLabel(state.customPortfolio.profile)) + ' risk profile</b>'
      + '<span> · ' + (held ? held + ' product' + (held === 1 ? '' : 's') : 'nothing built yet')
      + ' · ' + (others ? others + ' comparison' + (others === 1 ? '' : 's') : 'no comparisons') + '</span></p>'
      + '<button type="button" class="btn btn-primary" id="aa-go">'
      + (held ? 'Continue to Implementation →' : 'Build it on Implementation →') + '</button>';
    return;
  }
  if (state.phase !== 'workspace' || !base) { el.hidden = true; el.innerHTML = ''; return; }
  var busy = state.columns.some(function (c) { return c.status === 'loading'; });
  var ready = base.status === 'ready';
  var n = state.columns.length - 1;
  el.hidden = false;
  el.innerHTML = '<p class="aa-next-txt"><span>Proposing</span> <b>' + esc(fullName(base.key)) + '</b>'
    + '<span> · ' + esc(state.basis.hedging) + ' · ' + (n ? n + ' comparison' + (n === 1 ? '' : 's') : 'no comparisons yet') + '</span></p>'
    + '<button type="button" class="btn btn-primary" id="aa-go"' + (ready && !busy ? '' : ' disabled') + '>'
    + (busy ? 'Building…' : (ready ? 'Continue to Implementation →' : 'The proposed portfolio did not build')) + '</button>';
}

/* ---- the rail, narrow, once the work in it is done (H2) --------------------
   Below the stacking breakpoint the whole rail sits above the document, so a
   finished decision - basis, type, level, allocation - filled the first
   screen and the comparison was a scroll away. Once the proposed portfolio
   exists, step 1 folds the rail to one line that says what was chosen, and
   Edit opens it again. Step 2 keeps the whole rail: its pickers are the work
   there. Wide, none of this applies and the line is not shown. */
var railEditing = false;
function renderRailSummary() {
  var el = document.getElementById('railsum'); if (!el) return;
  var base = state.columns[0] || null;
  var on = state.phase === 'workspace' && state.step === 'aa' && !!base;
  document.body.classList.toggle('rail-summary', on);
  document.body.classList.toggle('rail-editing', on && railEditing);
  if (!on) { el.innerHTML = ''; railEditing = false; return; }
  el.innerHTML = '<span class="rail-sum-txt"><b>' + (cpActive()
      ? 'Custom · ' + esc(riskLabel(state.customPortfolio.profile)) + ' risk profile'
      : esc(fullName(base.key))) + '</b>'
    + '<span> · ' + esc(state.basis.hedging) + (state.variant ? ' · ' + esc(state.variant) : '') + '</span></span>'
    + '<button type="button" class="tier-edit" id="railsum-edit" aria-expanded="' + railEditing + '"'
    + ' aria-controls="rail-tiers">' + (railEditing ? 'Done' : 'Edit') + '</button>';
}

/* ---- what stands over the tables as they scroll (B1, H3) -------------------
   The column heads were sticky in the stylesheet and never stuck: a table's
   wrapper scrolls sideways, which makes it the box a sticky cell sticks to,
   and that box never scrolls down. Halfway through the risk dashboard its
   eight columns of figures had no names.

   So the heads are docked instead: one bar, fixed under whatever is already
   held at the top of the window, shown while a table's own head is above it
   and the table is still passing. It is drawn from the live head cells as
   measured, so it follows the table sideways, the pinned first column stays
   pinned, and both tables share it - they are the same comparison. It is
   aria-hidden: the real heads are in the tables, and would be heard twice.

   The same pass places the shadow at a table's right edge while there are
   columns beyond it (H3) - the catalogue's device (D99), for the same reason:
   narrow, nothing said the table carried on. */
/* Step 1 compares two tables that share their columns, so one docked bar
   serves both; step 2 has one table of its own, whose header is sticky in the
   stylesheet and, for the same reason theirs were, never sticks (A3). */
function dockTables() {
  return state.step === 'impl' ? ['implTbl'] : ['alloc', 'risk'];
}
function heldAtTop() {
  /* the bottom of anything already stuck to the top of the window */
  var held = 0;
  ['degraded', 'railsum'].forEach(function (id) {
    var el = document.getElementById(id);
    if (!el || el.hidden || !el.offsetParent) return;
    var box = (id === 'railsum' ? el.closest('.rail') : el).getBoundingClientRect();
    var style = getComputedStyle(id === 'railsum' ? el.closest('.rail') : el).position;
    if ((style === 'sticky' || style === 'fixed') && box.top <= 1 && box.bottom > held) held = box.bottom;
  });
  return held;
}
function placeChrome() {
  var dock = document.getElementById('coldock');
  var view = document.getElementById(state.step === 'impl' ? 'view-impl-wrap' : 'view-aa');
  var live = !!view && !view.hidden && state.phase === 'workspace';
  var top = live ? heldAtTop() : 0;
  var shown = null;
  dockTables().forEach(function (id) {
    var table = document.getElementById(id), wrap = table && table.parentNode;
    var edge = document.getElementById(id + '-more');
    if (!table || !wrap || !live || wrap.hidden) { if (edge) edge.classList.remove('on'); return; }
    if (edge) {
      var sec = edge.parentNode.getBoundingClientRect(), w = wrap.getBoundingClientRect();
      edge.style.top = (w.top - sec.top) + 'px';
      edge.style.height = wrap.clientHeight + 'px';
      edge.style.left = (w.left - sec.left + wrap.clientWidth - edge.offsetWidth) + 'px';
      edge.classList.toggle('on', wrap.scrollWidth - wrap.clientWidth - wrap.scrollLeft > 1);
    }
    var head = table.querySelector('thead tr');
    if (!head || shown) return;
    var h = head.getBoundingClientRect(), t = table.getBoundingClientRect();
    if (h.bottom < top + 1 && t.bottom > top + h.height + 40) shown = { wrap: wrap, head: head, height: h.height };
  });
  if (!dock) return;
  if (!shown) { dock.hidden = true; return; }
  var box = shown.wrap.getBoundingClientRect();
  dock.hidden = false;
  dock.style.top = top + 'px';
  dock.style.left = box.left + 'px';
  dock.style.width = shown.wrap.clientWidth + 'px';
  dock.style.height = shown.height + 'px';
  var cells = [].slice.call(shown.head.children), html = '';
  /* A column pinned in the table has to be pinned in the dock as well, and
     painted last, so the scrolling ones pass beneath it rather than over it.
     Which those are is read off the table - step 1 pins one, step 2 pins the
     category and the product (A3) - rather than being named here. */
  var loose = [], fixed = [];
  cells.forEach(function (th) {
    if (th.classList.contains('addcol')) return;
    (th.classList.contains('rowhead') || th.classList.contains('prodcol') ? fixed : loose).push(th);
  });
  loose.concat(fixed).forEach(function (th) {
    var r = th.getBoundingClientRect();
    var mark = th.querySelector('.col-mark'), name = th.querySelector('.col-nm');
    html += '<span class="dock-cell' + (fixed.indexOf(th) !== -1 ? ' is-fixed' : '')
      + (th.classList.contains('txt') || th.classList.contains('rowhead') ? ' is-txt' : '')
      + '" style="left:' + (r.left - box.left) + 'px;width:' + r.width + 'px">'
      + (mark ? mark.outerHTML : '') + (name ? name.outerHTML : (th.querySelector('.sr-only') ? '' : esc(th.textContent))) + '</span>';
  });
  if (dock._html !== html) { dock.innerHTML = html; dock._html = html; }
}
var chromeQueued = false;
function queueChrome() {
  if (chromeQueued) return;
  chromeQueued = true;
  /* a timer, not an animation frame: a frame never fires in a background tab */
  window.setTimeout(function () { chromeQueued = false; placeChrome(); }, 16);
}
document.addEventListener('scroll', queueChrome, true);      /* the window's, and a wrapper's own */
window.addEventListener('resize', queueChrome);

function refresh() {
  saveSnapshot();                        /* the last good render is the fallback */
  preserveFocus(function () {
    renderRailToggle();
    renderPhase();
    renderDialog();
    renderMandate();
    renderBasis();
    renderBase();
    if (picker && picker.render) picker.render();
    renderAlloc();
    renderGuide();
    renderRisk();
    renderCharts();
    renderStageChrome();
    renderService();
    renderResolving();
    renderFooter();
    renderNext();
    renderRailSummary();
    extras.forEach(function (fn) { try { fn(); } catch (e) { console.error(e); } });
    sizeComparisonTables();
  });
  focusGuide();
  placeChrome();
  /* and again once the columns have finished moving to their new widths */
  window.setTimeout(placeChrome, 480);
}

/* ---- shared tooltip for both charts ------------------------------------- */
var vizTip = document.createElement('div');
vizTip.className = 'viz-tip';
document.addEventListener('DOMContentLoaded', function () { document.body.appendChild(vizTip); });
if (document.readyState !== 'loading') document.body.appendChild(vizTip);
document.addEventListener('mouseover', function (e) {
  var target = e.target && e.target.closest ? e.target.closest('[data-tip]') : null;
  if (!target) { vizTip.classList.remove('on'); return; }
  var parts = target.getAttribute('data-tip').split('|');
  vizTip.innerHTML = esc(parts[0]) + '<br><b>' + esc(parts[1] || '') + '</b>';
  vizTip.classList.add('on');
});
document.addEventListener('mousemove', function (e) {
  if (!vizTip.classList.contains('on')) return;
  vizTip.style.left = Math.min(window.innerWidth - 200, e.clientX + 14) + 'px';
  vizTip.style.top = Math.max(8, e.clientY - 42) + 'px';
});
document.addEventListener('mouseleave', function () { vizTip.classList.remove('on'); }, true);

/* =============================================================================
   Events - delegated at the document, never bound to re-rendered nodes.
   ========================================================================== */
document.addEventListener('click', function (e) {
  if (e.target.id === 'aa-go') {
    /* the step nav's own button, so the first visit is greeted as it is from there (D97) */
    var tab = document.getElementById('tab-impl');
    if (tab && !tab.disabled) { tab.click(); window.scrollTo(0, 0); tab.focus({ preventScroll: true }); }
    return;
  }
  if (e.target.id === 'railsum-edit') {
    railEditing = !railEditing;
    renderRailSummary(); queueChrome();
    var back = document.getElementById('railsum-edit');
    if (back) back.focus();
    return;
  }
  if (e.target.id === 'startbtn' || e.target.id === 'mdedit') {
    openMandateDialog(e.target); return;
  }
  if (e.target.id === 'schema-retry' || e.target.id === 'degraded-now') {
    state.retryStep = 0;
    stopRetry();
    attemptRecovery();
    renderService();
    return;
  }
  if (e.target.id === 'down-more') {
    var reason = document.getElementById('schema-error-reason');
    if (reason) reason.hidden = !reason.hidden;
    return;
  }
  /* the custodian's card (D195) */
  if (e.target.id === 'custchange') { openCustodyDialog(state.variant, e.target); return; }
  if (e.target.id === 'custsave') { commitCustody(); return; }
  if (e.target.id === 'custcancel' || e.target.id === 'custclose') {
    if (custody && !custody.saving) closeCustodyDialog();
    return;
  }
  if (e.target.dataset && e.target.dataset.custscrim !== undefined) {
    if (custody && !custody.saving) closeCustodyDialog();
    return;
  }
  /* the notices (D199) */
  if (e.target.id === 'ntok') { confirmNotice(); return; }
  if (e.target.id === 'ntcancel') { declineNotice(); return; }
  if (e.target.id === 'ntread' && !noticeCard) {
    /* the ones the proposal raises and has not confirmed, one after another */
    var raised = App.raisedNotices ? App.raisedNotices() : [];
    var open = raised.filter(function (r) { return !noticeConfirmed(r.notice.id); });
    if (open.length) {
      openNoticeCard({ queue: open.map(function (r) { return r.notice; }), category: '', sleeve: '',
                       products: [].concat.apply([], open.map(function (r) { return r.products || []; })),
                       proceed: null, cancel: null, opener: e.target });
    }
    return;
  }
  if (e.target.id === 'dlgsave') { commitMandate(); return; }
  if (e.target.id === 'dlgcancel' || e.target.id === 'dlgclose') {
    closeMandateDialog(); return;
  }
  if (e.target.id === 'dlgaccount' || e.target.id === 'lpaccount') {
    openAccountDialog(e.target); return;
  }
  if (e.target.id === 'accancel' || e.target.id === 'acclose' || e.target.id === 'acdone') {
    if (!(account && account.submitting)) closeAccountDialog();
    return;
  }
  if (e.target.id === 'acsubmit') { submitAccountRequest(); return; }
  if (e.target.id === 'accopy') { copyRequestReference(e.target); return; }
  if (e.target.id === 'acchange') { changeProposal(); return; }
  if (e.target.dataset && e.target.dataset.acnote) { toggleAccountNote(e.target); return; }
  /* a restriction ticked, or a chip's way out (D203) */
  if (account && e.target.dataset && e.target.dataset.acr) { chooseRestriction(e.target.dataset.acr, e.target.checked); return; }
  if (account && e.target.dataset && e.target.dataset.acrx) {
    var gone = e.target.dataset.acrx;
    chooseRestriction(gone, false);
    var back = document.getElementById('acr-' + gone) || document.getElementById('acrsearch');
    if (back) back.focus();
    return;
  }
  if (e.target.dataset && e.target.dataset.scrim !== undefined) {
    if (draft && !draft.dirty) closeMandateDialog();   /* spec 7.1 dialog rules */
    else if (account && !account.dirty && !account.submitting) closeAccountDialog();
    return;
  }
  var option = e.target.closest ? e.target.closest('.combo-opt') : null;
  if (option && !option.classList.contains('none') && draft) {
    draft.pwa = option.textContent;
    draft.q = option.textContent;
    draft.open = false; draft.idx = -1; draft.err = null; draft.dirty = true;
    renderDialog();
    refocusCombo();
    return;
  }
  var toggle = e.target.closest ? e.target.closest('#railtoggle') : null;
  if (toggle) { toggleRail(); return; }
  var roll = e.target.closest ? e.target.closest('#baseroll') : null;
  if (roll) { setBaseCollapsed(!state.baseCollapsed, false); refresh(); return; }
  var cpButton = e.target.closest ? e.target.closest('[data-cpconfirm]') : null;
  if (cpButton) {
    var pendingSwitch = state.cpConfirm;
    if (cpButton.dataset.cpconfirm !== 'yes' || !pendingSwitch) {
      state.cpConfirm = null; refresh();
      var select = document.getElementById('bpa'); if (select) select.focus();
      return;
    }
    if (pendingSwitch.to === 'custom') becomeCustom(); else becomeStrategic(pendingSwitch.allocationType);
    return;
  }
  var columnRemove = e.target.closest ? e.target.closest('.col-rm') : null;
  if (columnRemove) { removeComparison(+columnRemove.dataset.remove); return; }
  var retry = e.target.closest ? e.target.closest('.col-retry') : null;
  if (retry) { retryColumn(+retry.dataset.retry); return; }
  var remove = e.target.closest ? e.target.closest('.rm') : null;
  if (remove && remove.dataset.i !== undefined) { removeComparison(+remove.dataset.i); return; }
  if (e.target.id === 'basis-apply') { confirmBasisChange(); return; }
  if (e.target.id === 'basis-cancel') { cancelBasisChange(); return; }
});

document.addEventListener('change', function (e) {
  if (!custody || !e.target || e.target.name !== 'cust') return;
  custody.chosen = e.target.value; custody.err = null;
});

document.addEventListener('input', function (e) {
  if (account && onAccountInput(e)) return;
  if (!draft) return;
  if (e.target.id === 'mdtop') {
    draft.top = parseMoney(e.target.value); draft.err = null; draft.dirty = true; return;
  }
  if (e.target.id === 'mdsize') {
    draft.size = parseMoney(e.target.value); draft.err = null; draft.dirty = true; return;
  }
  if (e.target.id === 'mdpwa') {
    draft.q = e.target.value;
    draft.open = true; draft.idx = -1; draft.err = null; draft.dirty = true;
    draft.pwa = (draft.matches && draft.matches.indexOf(draft.q) >= 0) ? draft.q : '';
    scheduleAdvisorSearch();
    return;
  }
});

/* Amounts re-format on blur; validation on blur, not per keystroke
   (spec 7.1). The re-render is deferred a tick so the focus transition the
   blur belongs to completes first - re-rendering synchronously inside
   focusout destroys the element the user just clicked. An error is surfaced
   only for a field that has a value: a user who has not reached the mandate
   field yet must not be told it is wrong. */
document.addEventListener('focusout', function (e) {
  /* a note Escape closed may show again once its i is left (D202) */
  if (e.target.dataset && e.target.dataset.acnote) {
    e.target.closest('.ac-lab').classList.remove('is-shut');
  }
  if (!draft) return;
  if (e.target.id === 'mdtop' || e.target.id === 'mdsize') {
    var value = parseMoney(e.target.value);
    if (e.target.id === 'mdtop') draft.top = value || null;
    else draft.size = value || null;
    e.target.value = value ? money(value) : '';
    window.setTimeout(function () {
      if (!draft || draft.saving) return;
      var problem = validateDraft();
      var relevant = problem
        && ((problem.field === 'mdtop' && draft.top !== null)
            || (problem.field === 'mdsize' && draft.size !== null));
      var next = relevant ? problem : null;
      var was = draft.err ? draft.err.field + draft.err.msg : '';
      var now = next ? next.field + next.msg : '';
      if (was !== now) { draft.err = next; preserveFocus(renderDialog); }
    }, 0);
  }
});

/* a notice's tick box (D199) */
document.addEventListener('change', function (e) {
  if (noticeCard && e.target && e.target.id === 'nttick') tickNotice(e.target.checked);
});

/* selects report through change rather than input */
document.addEventListener('change', function (e) {
  if (account && e.target && e.target.dataset && e.target.dataset.acf) onAccountInput(e);
});

document.addEventListener('keydown', function (e) {
  /* a notice closes by a button; Escape is Cancel (D199) */
  if (noticeCard) {
    /* it may stand over a builder: the keys are its alone while it is up */
    e.stopImmediatePropagation();
    if (e.key === 'Escape') { e.preventDefault(); declineNotice(); return; }
    if (e.key === 'Tab') trapFocus(e, document.getElementById('noticeDialog'));
    return;
  }
  if (custody) {
    if (e.key === 'Escape') { e.preventDefault(); if (!custody.saving) closeCustodyDialog(); return; }
    if (e.key === 'Enter' && e.target && e.target.name === 'cust') { e.preventDefault(); commitCustody(); return; }
    if (e.key === 'Tab') trapFocus(e, document.getElementById('custodyDialog'));
    return;
  }
  if (account && !draft) {
    if (e.key === 'Escape') {
      e.preventDefault();
      if (closeAccountNote()) return;
      if (!account.submitting) closeAccountDialog();
      return;
    }
    if (e.key === 'Enter' && e.target && e.target.id === 'acuid') {
      e.preventDefault(); lookupNow(); return;
    }
    if (e.key === 'Tab') trapFocus(e, document.getElementById('accountDialog'));
    return;
  }
  if (draft) {
    var host = document.getElementById('mandateDialog');
    if (e.key === 'Escape') {
      e.preventDefault();
      if (draft.open && (draft.q || '').trim().length >= 2) {
        draft.open = false; draft.idx = -1; renderDialog(); refocusCombo();
      } else {
        closeMandateDialog();
      }
      return;
    }
    if (e.key === 'Tab' && host && !host.hidden) {          /* focus trap */
      var focusables = host.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])');
      if (focusables.length) {
        var firstNode = focusables[0], lastNode = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === firstNode) {
          e.preventDefault(); lastNode.focus();
        } else if (!e.shiftKey && document.activeElement === lastNode) {
          e.preventDefault(); firstNode.focus();
        }
      }
    }
    if (e.target.id === 'mdpwa' && draft.matches && draft.matches.length && draft.open) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        draft.idx = (e.key === 'ArrowDown')
          ? Math.min(draft.matches.length - 1, draft.idx + 1)
          : Math.max(0, draft.idx - 1);
        renderDialog(); refocusCombo();
      } else if (e.key === 'Enter' && draft.idx >= 0) {
        e.preventDefault();
        draft.pwa = draft.matches[draft.idx];
        draft.q = draft.pwa;
        draft.open = false; draft.idx = -1; draft.dirty = true;
        renderDialog(); refocusCombo();
      }
    }
    return;
  }
});

/* step nav: arrow keys move between tabs (spec 13.2); Enter/Space activate
   natively since the tabs are real buttons */
document.addEventListener('keydown', function (e) {
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
  var tab = e.target.closest ? e.target.closest('#steps .step') : null;
  if (!tab) return;
  var tabs = Array.prototype.slice.call(document.querySelectorAll('#steps .step'));
  var index = tabs.indexOf(tab);
  if (index < 0) return;
  e.preventDefault();
  var next = tabs[(index + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
  next.focus();
});

document.addEventListener('change', function (e) {
  if (e.target.id === 'ccy' || e.target.id === 'hedge') {
    var field = e.target.id === 'ccy' ? 'currency' : 'hedging';
    if (!state.basisChosen) {
      /* A fresh scenario: each half is recorded as it is answered, and the
         basis counts as chosen only when BOTH are (D93). Filling the other
         half from whatever the scenario was created with passed a default
         off as the PWA's answer, on the one decision every figure is priced
         in. Only then is it persisted and the schema fetched against it. No
         rebuild confirmation, because there is nothing built yet to rebuild. */
      if (!e.target.value) return;
      state.basis[field] = e.target.value;
      state.basisAnswered[field] = true;
      guideFocus = true;
      if (state.basisAnswered.currency && state.basisAnswered.hedging) {
        state.basisChosen = true;
        persistBasis();
        fetchSchema();
      }
      refresh();
      return;
    }
    requestBasisChange(field, e.target.value);
    return;
  }

  if (e.target.id === 'bpv') {
    if (!state.columns.length) guideFocus = true;
    setVariant(e.target.value);
    return;
  }

  /* Custom (D171): choosing it, or a strategic allocation while it is in
     force, asks first in the rail; the risk level while it is in force is
     its profile, and moves the limits rather than the build. */
  if (e.target.id === 'bpa' && e.target.value === '__custom__') {
    state.cpConfirm = cpActive() ? null : { to: 'custom' };
    refresh();
    var yes = document.getElementById('cpconfirmyes'); if (yes) yes.focus();
    return;
  }
  if (cpActive() && e.target.id === 'bpa') {
    state.cpConfirm = e.target.value ? { to: 'strategic', allocationType: e.target.value } : null;
    refresh();
    var back = document.getElementById('cpconfirmyes'); if (back) back.focus();
    return;
  }
  if (cpActive() && e.target.id === 'bpr') { setCpProfile(e.target.value); return; }
  if (e.target.id === 'bpa' || e.target.id === 'bpre' || e.target.id === 'bpr') {
    state.cpConfirm = null;
    var forced = variantForcesExcludeRE(state.variant);
    var base = state.columns[0] || null;
    var pending = state.baseDraft;
    var drafting = !!(pending.allocationType || pending.riskLevel);
    var current = (base && !drafting) ? base.key : null;
    /* what the three controls say now: the built key, or the draft in
       progress, then the one control that just changed on top */
    var allocationType = current ? (current.allocationType || '') : pending.allocationType;
    var exRA = current ? !!current.excludeRealAssets : !!pending.excludeRealAssets;
    var risk = current ? current.riskLevel : pending.riskLevel;
    if (e.target.id === 'bpa') { allocationType = e.target.value; exRA = false; }
    if (e.target.id === 'bpre') exRA = e.target.checked;
    if (e.target.id === 'bpr') risk = e.target.value;
    if (allocationType && reAllowed(allocationType) && forced) exRA = true;
    /* buildKey decides whether that is enough - an all-equity level needs
       nothing more - and setBase replaces column one or falls to the nearest
       risk level the combination offers (D54) */
    var key = buildKey(allocationType, exRA, risk);
    /* a portfolio the private markets minimums keep closed is not built,
       and nothing falls to another risk level in its place (D200) */
    if (key && pmClosed(key)) {
      state.baseDraft = { allocationType: allocationType, excludeRealAssets: exRA, riskLevel: risk };
      refresh();
      return;
    }
    if (!base) guideFocus = true;
    var built = key ? setBase(key) : false;
    if (built || (key && base && keyEq(key, base.key))) {
      /* built, or the answer is the book already there: nothing pending */
      state.baseDraft = { allocationType: '', excludeRealAssets: false, riskLevel: '' };
      if (!built) refresh();
    } else {
      /* not enough to build yet, or nothing the combination offers: keep
         what was answered so the controls show it, base or no base */
      state.baseDraft = { allocationType: allocationType, excludeRealAssets: exRA, riskLevel: risk };
      refresh();
    }
    return;
  }
});

/* Escape in the rail puts back a pending switch between the two kinds (D171). */
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape' || !state.cpConfirm) return;
  if (!e.target.closest || !e.target.closest('#tier-base')) return;
  e.preventDefault();
  state.cpConfirm = null;
  refresh();
  var select = document.getElementById('bpa'); if (select) select.focus();
});

/* ---- public surface ----------------------------------------------------- */
return {
  /* state accessors */
  state: function () { return state; },
  phase: function () { return state.phase; },
  basis: function () { return state.basis; },
  mandate: function () { return state.mandate; },
  mandateSize: function () { return state.mandate ? state.mandate.mandateSize : null; },
  /* the hedge fund notice (D198): the rail's line, and whether its card is up */
  /* the notices (D199): which a sleeve raises, the rail's line, the gate
     a custom portfolio's products go through, and whether a card is up */
  noticesRaised: noticesRaised,
  noticeHeld: noticeHeld,
  noticesNoteHtml: noticesNoteHtml,
  noticeGate: noticeGate,
  noticeGateFor: noticeGateFor,
  noticeBodyHtml: noticeBodyHtml,
  noticeOpen: function () { return !!noticeCard; },
  /* the custodian in force, and its name (D193, D195); whether its card is up */
  entity: function () { return custodian(); },
  custodyOpen: function () { return !!custody; },
  entityName: entityName,
  columns: function () { return state.columns; },
  base: function () { return state.columns[0] || null; },
  step: function () { return state.step; },
  implSeen: function () { return state.implSeen; },
  setStep: function (s) {
    var wasStep = state.step;
    var opener = document.activeElement;
    state.step = s;
    if (s === 'impl') state.implSeen = true;   /* stop beckoning once opened */
    /* The base tier follows the step: rolled up while the implementation is
       being built, open again on the allocation step where it is the work. */
    if (s !== wasStep) setBaseCollapsed(s === 'impl', true);
    announce('polite', s === 'impl' ? 'Implementation step.' : 'Asset allocation step.');
    refresh();
    /* under a type that asks for a custodian (D193, D195), the implementation
       step asks for it once it is on screen: Continue stays on the step,
       Cancel goes back to the allocation step, since nothing here can be
       picked without one */
    if (s === 'impl' && state.mandate && custodyNeeded(state.variant) && !custodian()) {
      announce('assertive', 'Choose the custodian for the implementation step.');
      openCustodyDialog(state.variant, opener, function () { refresh(); }, function () {
        App.setStep('aa');
        /* back to what moved on, or the step it moved on to */
        var back = opener && opener.isConnected && opener !== document.body ? opener
          : document.querySelector('.step[data-step="impl"]');
        if (back && back.focus) back.focus();
      });
    }
  },
  baseCollapsed: function () { return state.baseCollapsed; },
  toggleBaseCollapsed: function () {
    setBaseCollapsed(!state.baseCollapsed, false);
    refresh();
  },
  schema: function () { return state.schema; },
  schemaReady: schemaReady,
  /* apiFetch reports transport health here; the rest is read by the page */
  noteService: noteService,
  serviceUp: function () { return state.service !== 'down'; },
  fromCache: function () { return state.fromCache; },
  retryDelay: retryDelay,
  opt: opt,
  canEdit: canEdit,
  canExport: canExport,
  autoSleeveCategories: autoSleeveCategories,
  riskLabel: riskLabel,
  /* the overlay rules (D155): the engine, and the list in force */
  resolveOverlays: resolveOverlays,
  overlayTakes: overlayTakes,
  overlayShortfall: overlayShortfall,
  overlayWords: overlayWords,
  overlayPct: overlayPct,
  overlayList: overlayList,
  overlaysWithheld: overlaysWithheld,
  overlayOrder: overlayOrder,
  overlayRule: overlayRule,
  categoryColour: knownCatColor,

  /* keys, names, availability */
  keyStr: keyStr,
  headerName: headerName,
  fullName: fullName,
  available: available,
  whyUnavailable: whyUnavailable,
  used: used,
  slotsLeft: slotsLeft,
  reAllowed: reAllowed,
  /* for the rate-card panel (D55): re-serve the schema after an edit so the
     rates re-price, and hold the page inert behind a dialog */
  refetchSchema: fetchSchema,
  setBackgroundInert: setBackgroundInert,
  buildKey: buildKey,
  typesForRisk: typesForRisk,
  allocationChoices: allocationChoices,
  isAllEquityRisk: isAllEquityRisk,

  /* actions */
  addComparison: addComparison,
  removeComparison: removeComparison,
  retryColumn: retryColumn,
  setBase: setBase,

  /* sleeves */
  sleeves: function () { return state.sleeves; },
  sleeveLib: function () { return state.sleeveLib; },
  ensureSleeveLib: ensureSleeveLib,
  chooseSleeve: chooseSleeve,
  /* custom sleeves (D170) */
  customSleeves: function () { return state.customSleeves; },
  customLib: function () { return state.customLib; },
  ensureCustomLib: ensureCustomLib,
  setCustomSleeve: setCustomSleeve,
  useCustomSleeve: useCustomSleeve,
  mayBuildCustom: function (category) {
    return canEdit() && (opt('customSleeves.mayBuild', []) || []).indexOf(category) !== -1;
  },
  /* custom portfolios (D171) */
  customPortfolio: function () { return state.customPortfolio; },
  cpActive: cpActive,
  cpLib: function () { return state.cpLib; },
  ensureCpLib: ensureCpLib,
  pushCustomPortfolio: pushCustomPortfolio,
  cpApproval: cpApproval,
  mayBuildPortfolio: mayBuildPortfolio,
  syncAnchorBase: syncAnchorBase,
  catColor: catColor,
  riskLabel: riskLabel,
  removeComparison: removeComparison,
  variant: function () { return state.variant; },
  tacticalTilt: function () { return state.tacticalTilt; },
  volPremium: function () { return state.volPremium && canHoldVolPremium(); },
  canHoldVolPremium: canHoldVolPremium,
  setVolPremium: setVolPremium,
  setTacticalTilt: setTacticalTilt,
  includeFees: function () { return state.includeFees; },
  setIncludeFees: setIncludeFees,
  feeSchedule: function () { return state.feeSchedule; },
  feeLevel: feeLevel,
  adoptFeeLevel: adoptFeeLevel,
  customLevel: customLevel,
  customFees: function () { return state.customFees || {}; },
  setCustomFees: setCustomFees,
  otherCharges: function () { return state.otherCharges || {}; },
  setOtherCharges: setOtherCharges,
  setFeeSchedule: setFeeSchedule,
  setFeeLevel: setFeeLevel,
  setVariant: setVariant,
  afterRepository: afterRepository,
  implementationVariants: function () {
    return opt('options.implementationVariants', []);
  },

  /* misc */
  scenarioId: function () { return state.scenarioId; },
  exporting: function () { return state.exporting; },
  announce: announce,
  money: money,
  esc: esc,
  num: num,
  refresh: refresh,
  writesSettled: writesSettled,
  boot: boot,
  setPicker: function (p) { picker = p; },
  addRenderer: function (fn) { extras.push(fn); },
  plusColumn: false,
  lastAdded: -1
};
})();
