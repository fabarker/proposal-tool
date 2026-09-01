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
  mandate: null,                    /* {topAccountSize, mandateSize, primaryPwa} */
  basis: { currency: 'USD', hedging: 'Hedged' },
  schema: null,
  schemaStatus: 'idle',             /* 'idle'|'loading'|'ready'|'error' */
  schemaError: null,
  step: 'aa',                       /* 'aa' | 'impl' */
  columns: [],                      /* index 0 is always the base; see makeColumn */
  basisChosen: false,               /* the PWA has answered currency + hedging */
  implSeen: false,                  /* step 2 has been opened at least once */
  variant: null,                    /* implementation variant; gates step 2 (D29) */
  /* On by default: the tilt is house practice wherever it can be funded.
     A portfolio that cannot fund it renders the toggle off and disabled,
     and tiltCategories no-ops, so this one default covers both (D50). */
  tacticalTilt: true,
  /* The strategic volatility premium (D53): a product a PWA adds, so off by
     default, and forbidden outside the currencies the schema names. */
  volPremium: false,
  /* Whether the proposal shows fees at all (D52). Off to begin with: a fee is
     a conversation a PWA opens deliberately, and until they do, the pricing
     controls and the three fee columns are not in the page. */
  includeFees: false,
  /* How the book is priced (D51). The schedule is a choice with no default,
     like the variant; the level has a prescribed one, which the server sets
     when it creates the scenario and the schema names (fees.defaultLevel). */
  feeSchedule: null,                /* 'CASP' | 'RDR' | null */
  feeLevel: null,                   /* e.g. 'PMG Target' */
  sleeves: {},                      /* category -> sleeve name (pickable categories only) */
  sleeveLib: {},                    /* category -> {status, sleeves, error} */
  exporting: { status: 'idle', error: null },
  basisDraft: null,                 /* pending basis change awaiting confirmation */
  /* excludeTAA is pinned true: tactical allocation is an implementation
     concept, so no strategic key carries it (D50). The field stays in the
     canonical key - fourth of four - so the bake is untouched. */
  baseDraft: { allocation: '', excludeRE: false, excludeTAA: true, riskLevel: '' },
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

function keyStr(k) {
  return [k.allocation, k.excludeRE ? 1 : 0, k.excludeTAA ? 1 : 0, k.riskLevel].join('|');
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
function used(k) {
  return state.columns.some(function (c) { return keyEq(c.key, k); });
}
function reAllowed(allocation) {
  return opt('options.reAllowed', ['Full', 'Ex HFs']).indexOf(allocation) >= 0;
}
function canEdit() { return opt('capabilities.canEdit', true); }
function canExport() { return opt('capabilities.canExport', true); }
function autoSleeveCategories() { return opt('rules.autoSleeveCategories', []); }

/* The display name for a risk level. The value stays what the schema sent -
   it is the fourth field of the portfolio key and keys the bake - so the UI
   shows the label and submits the value. Falls back to the value itself, so a
   level the map has not been told about still reads sensibly (D35). */
function riskLabel(value) {
  var labels = opt('options.riskLevelLabels', null);
  return (labels && labels[value]) || value;
}

/* Derived naming (the template is hardcodable; the values are not - spec 4.3).
   Order is risk level, then allocation, then exclusions, with the currency in
   front of the full form: "USD Moderate-Aggressive Core". The risk
   level prints through riskLabel, so the name says what the rail says.

   Built from the KEY, never from the payload's own name or header fields.
   Those are frozen into the baked slices, so a naming change would show on
   some surfaces and not others until a re-bake; deriving from the key makes
   the screen consistent the moment the rule changes (D36). */
function headerName(k) {
  var suffix = '';
  if (k.excludeRE && reAllowed(k.allocation)) suffix += ' ex RE';
  return riskLabel(k.riskLevel) + ' ' + k.allocation + suffix;
}
function fullName(k) { return state.basis.currency + ' ' + headerName(k); }

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

function schemaQuery() {
  var q = '?currency=' + encodeURIComponent(state.basis.currency)
        + '&hedging=' + encodeURIComponent(state.basis.hedging);
  if (state.mandate && state.mandate.mandateSize) {
    q += '&mandateSize=' + encodeURIComponent(state.mandate.mandateSize);
  }
  /* The top account size sets the fee tier, and the schema carries the
     rates at that tier (D51). */
  if (state.mandate && state.mandate.topAccountSize) {
    q += '&topAccountSize=' + encodeURIComponent(state.mandate.topAccountSize);
  }
  /* The variant narrows options.allocations and the availability set, so it
     belongs in the key of what the schema describes (D49). */
  if (state.variant) q += '&variant=' + encodeURIComponent(state.variant);
  return q;
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
  try {
    state.schema = await apiFetch('/scenario/schema' + schemaQuery());
    state.schemaStatus = 'ready';
    state.schemaError = null;
    pruneUnavailableColumns();
    /* A mandate edit can move the account-size tier, which re-prices every
       management fee on the sheet without any row visibly changing. */
    var tierAfter = opt('fees.tier.id', null);
    if (tierBefore && tierAfter && tierBefore !== tierAfter) {
      announce('polite', 'Account size tier is now ' + opt('fees.tier.label', tierAfter)
        + '; management fees re-priced.');
    }
  } catch (err) {
    state.schemaStatus = 'error';
    state.schemaError = err.message || String(err);
  }
  refresh();
}

/* Columns whose combination the (re-fetched) schema no longer offers are
   dropped and named (spec 11.2 step 4, 11.4 step 3). */
function pruneUnavailableColumns() {
  if (!schemaReady()) return;
  var survivors = [];
  state.columns.forEach(function (col) {
    if (available(col.key)) { survivors.push(col); return; }
    abortColumn(col);
    announce('assertive', fullName(col.key)
      + ' is not available under the current mandate and basis and has been removed.');
    deleteColumnOnServer(col.key);
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
  if (!state.scenarioId) return;
  apiFetch('/scenario/' + encodeURIComponent(state.scenarioId)
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

/* Changing the base replaces column one (spec 11.5). */
function setBase(key) {
  if (!schemaReady() || !canEdit()) return false;
  if (!available(key)) {
    /* fall to the first risk level the combination offers */
    var risks = opt('options.riskLevels', []);
    var fallen = null;
    for (var i = 0; i < risks.length; i++) {
      var probe = { allocation: key.allocation, excludeRE: key.excludeRE,
                    excludeTAA: key.excludeTAA, riskLevel: risks[i] };
      if (available(probe)) { fallen = probe; break; }
    }
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
      deleteColumnOnServer(c.key);
      announce('assertive', headerName(c.key)
        + ' was removed as a comparison because it is now the base.');
      return false;
    }
    return true;
  });
  state.columns = [col].concat(rest);
  App.lastAdded = -1;
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
  base.data.categories.forEach(function (c) { have[c.name] = 1; });
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
  var survivors = [];
  state.columns.forEach(function (col) {
    if (available(col.key)) { survivors.push(col); return; }
    abortColumn(col);
    announce('assertive', headerName(col.key) + ' is not available in '
      + state.basis.currency + ' and has been removed.');
    deleteColumnOnServer(col.key);
  });
  state.columns = survivors;
  state.columns.forEach(function (col) { startResolve(col); });
  state.sleeveLib = {};              /* the library may differ under the new basis */
  revalidateSleeves();
  refresh();
}

function persistBasis() {
  if (!state.scenarioId) return;
  apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ basis: state.basis })
  }).catch(function (err) { showAlert('error', err.message || String(err)); });
}

/* Sleeves kept on a basis change, re-validated against the new library;
   any no longer offered are dropped and named (spec 11.6). */
function revalidateSleeves() {
  Object.keys(state.sleeves).forEach(function (category) {
    ensureSleeveLib(category, function (lib) {
      var name = state.sleeves[category];
      if (!name) return;
      var stillOffered = (lib.sleeves || []).some(function (s) { return s.name === name; });
      if (!stillOffered) {
        delete state.sleeves[category];
        announce('assertive', 'The ' + category + ' sleeve "' + name
          + '" is not offered under the new basis and has been removed.');
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
  apiFetch('/scenario/sleeves?category=' + encodeURIComponent(category)
      + '&variant=' + encodeURIComponent(state.variant)
      + '&currency=' + encodeURIComponent(state.basis.currency)
      + '&hedging=' + encodeURIComponent(state.basis.hedging))
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
async function setVariant(name) {
  if (!canEdit()) return;
  if (!name || name === state.variant) return;
  var had = Object.keys(state.sleeves).length;
  var hadBase = state.columns[0] || null;
  state.variant = name;
  state.sleeveLib = {};
  state.sleeves = {};
  /* The base draft was assembled against the previous variant's allocations,
     so a half-made selection is discarded rather than silently carried into a
     variant that may not offer it. */
  state.baseDraft = { allocation: '', excludeRE: false, excludeTAA: true, riskLevel: '' };
  refresh();

  /* Persist FIRST, and wait for it. The server validates every resolve against
     the variant it has stored (D49), so anything issued while this is in
     flight is judged against a scenario that does not know the variant yet and
     comes back 422. Ordering this after the schema fetch left a whole extra
     round trip in which a fast selection could do exactly that. */
  if (state.scenarioId) {
    variantPending = apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ variant: name })
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
    state.columns.slice().forEach(function (col) {
      abortColumn(col);
      deleteColumnOnServer(col.key);
    });
    state.columns = [];
  }
  announce('assertive', had
    ? 'Implementation variant set to ' + name + '. ' + had
      + ' sleeve choice' + (had === 1 ? '' : 's') + ' cleared - the library has changed.'
    : 'Implementation variant set to ' + name + '.');
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
    apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tacticalTilt: on })
    }).catch(function (err) { showAlert('error', err.message || String(err)); });
  }
  announce('polite', on
    ? 'Tactical tilt added, funded from ' + opt('rules.tacticalTiltFundedFrom', '') + '.'
    : 'Tactical tilt removed.');
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
  return opt('fees.levels', []).map(function (l) { return l.id; });
}

function feeLevel() {
  return state.feeLevel || opt('fees.defaultLevel', null);
}

function pushFee(patch) {
  if (!state.scenarioId) return;
  apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch)
  }).catch(function (err) { showAlert('error', err.message || String(err)); });
}

/* The strategic volatility premium (D53). Same shape as the tilt - an
   implementation overlay that moves weight and re-resolves nothing - with one
   extra gate: the product is forbidden outside the currencies the schema
   lists, so a book in any other currency cannot turn it on. The rule is
   applied server-side too; this is the half that keeps it off the screen. */
function volPremiumCurrencies() { return opt('rules.volPremiumCurrencies', []); }

function canHoldVolPremium() {
  return volPremiumCurrencies().indexOf(state.basis.currency) >= 0;
}

function setVolPremium(on) {
  if (!canEdit()) return;
  on = !!on;
  if (!canHoldVolPremium()) return;
  if (on === state.volPremium) return;
  state.volPremium = on;
  if (state.scenarioId) {
    apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ volPremium: on })
    }).catch(function (err) { showAlert('error', err.message || String(err)); });
  }
  announce('polite', on
    ? 'Strategic Volatility Premium added, funded pro rata from '
      + opt('rules.volPremiumFundedFrom', '') + '.'
    : 'Strategic Volatility Premium removed.');
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
  if (name) state.sleeves[category] = name; else delete state.sleeves[category];
  pushSleeves();
  refresh();
}

function pushSleeves() {
  if (!state.scenarioId) return;
  apiFetch('/scenario/' + encodeURIComponent(state.scenarioId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sleeves: state.sleeves })
  }).catch(function (err) { showAlert('error', err.message || String(err)); });
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
      state.volPremium = !!(created.scenario && created.scenario.volPremium);
      state.feeSchedule = (created.scenario && created.scenario.feeSchedule) || null;
      state.feeLevel = (created.scenario && created.scenario.feeLevel) || null;
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
  state.mandate = mandate;
  var wasLanding = (state.phase === 'landing');
  state.phase = 'workspace';
  draft = null;
  /* mandate size gates the $20m rule: refetch the schema; any column now
     invalid is dropped with an announcement (spec 11.2) */
  await fetchSchema();
  refresh();
  if (wasLanding) {
    var allocationSelect = document.getElementById('bpa');
    if (allocationSelect) allocationSelect.focus();
  }
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
    state.schemaStatus = 'error';
    state.schemaError = err.message || String(err);
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
      /* off unless it was explicitly turned on, and never carried into a
         currency that cannot hold it */
      state.volPremium = !!stored.volPremium;
      state.feeSchedule = stored.feeSchedule || null;
      state.feeLevel = stored.feeLevel || null;
      /* A scenario stored before the toggle existed has no includeFees, and
         the server fills it from whether a schedule was ever chosen: a
         proposal already priced keeps showing its fees (D52). */
      state.includeFees = !!stored.includeFees;
      /* a scenario with columns has already been through the basis step */
      state.basisChosen = !!(stored.base || (stored.comparisons || []).length);
      state.sleeves = stored.sleeves || {};
      state.phase = 'workspace';
      await fetchSchema();                /* now with the mandate size */
      var keys = [];
      if (stored.base) keys.push({ str: stored.base, role: 'base' });
      (stored.comparisons || []).forEach(function (k) {
        keys.push({ str: k, role: 'comparison' });
      });
      keys.forEach(function (entry) {
        var parts = entry.str.split('|');
        var key = { allocation: parts[0], excludeRE: parts[1] === '1',
                    excludeTAA: parts[2] === '1', riskLevel: parts[3] };
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
  var broken = (state.schemaStatus === 'error');
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
    + '<p class="summary">Top account ' + money(state.mandate.topAccountSize)
    + '<span>Mandate ' + money(state.mandate.mandateSize) + '</span>'
    + '<span>' + esc(state.mandate.primaryPwa) + '</span></p>';
}

function renderBasis() {
  var el = document.getElementById('tier-basis'); if (!el) return;
  var showing = state.basisDraft || state.basis;
  var disabled = !schemaReady() || !canEdit() ? ' disabled' : '';
  /* Until both are answered the tier carries the ring, and the selects show a
     placeholder rather than a default. The scenario does hold a basis - it has
     to, the schema is fetched against one - but presenting that as a chosen
     answer invites the PWA to skip a decision the whole comparison rests on. */
  var pending = !state.basisChosen;
  el.className = 'tier' + (pending && state.phase === 'workspace' ? ' tier-ring' : '');
  var placeholder = '<option value="" selected>Select…</option>';
  var html = '<div class="tier-h"><h3>Scenario basis</h3></div>'
    + '<div class="basis">'
    + '<div class="field"><label for="ccy">Currency</label><select id="ccy"' + disabled + '>'
    + (pending ? placeholder : '')
    + opt('options.currencies', []).map(function (c) {
        return '<option' + (!pending && c === showing.currency ? ' selected' : '') + '>'
          + esc(c) + '</option>';
      }).join('')
    + '</select></div>'
    + '<div class="field"><label for="hedge">Hedging</label><select id="hedge"' + disabled + '>'
    + (pending ? placeholder : '')
    + opt('options.hedgingPolicies', []).map(function (c) {
        return '<option' + (!pending && c === showing.hedging ? ' selected' : '') + '>'
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
  } else {
    html += '<p class="field-note" style="margin-top:8px">Fixed across every column. Changing '
      + 'either rebuilds all portfolios.</p>';
  }
  el.innerHTML = html;
}

function renderBase() {
  var el = document.getElementById('tier-base'); if (!el) return;
  var base = state.columns[0] || null;
  /* before a base exists, the tier renders the pending draft so a partial
     selection (allocation chosen, risk not yet) survives the re-render */
  var key = base ? base.key : null;
  var pending = state.baseDraft;
  var allocation = key ? key.allocation : pending.allocation;
  var canRE = allocation ? reAllowed(allocation) : false;
  var exRE = key ? key.excludeRE : (allocation ? (canRE ? pending.excludeRE : true) : false);
  var exTAA = true;                 /* never strategic any more (D50) */
  var risk = key ? key.riskLevel : pending.riskLevel;
  /* The basis comes first. Until it is answered these controls are inert and
     the ring stays on the tier above - two tiers competing for attention tells
     the PWA nothing about which to answer first. */
  var locked = !state.basisChosen;
  var disabled = (!schemaReady() || !canEdit() || locked) ? ' disabled' : '';
  var ring = (state.phase === 'workspace' && !base && !locked) ? ' tier-ring' : '';

  /* Then the variant, which decides which allocations exist at all (D49).
     Until it is answered the allocation and risk selects are inert for the
     same reason the whole tier is inert before the basis: their options are
     not merely unknown, they are undecided. */
  var variant = state.variant;
  var variantLocked = locked || !variant;
  var afterVariant = (!schemaReady() || !canEdit() || variantLocked) ? ' disabled' : '';
  var forceExRE = variantForcesExcludeRE(variant);
  if (forceExRE && canRE) exRE = true;

  var variants = opt('options.implementationVariants', []);
  var variantOptions = (variant ? '' : '<option value="" selected>Select…</option>')
    + variants.map(function (v) {
        return '<option' + (v === variant ? ' selected' : '') + '>' + esc(v) + '</option>';
      }).join('');

  var allocationOptions = (allocation ? '' :
      '<option value="" selected>Select…</option>')
    + opt('options.allocations', []).map(function (a) {
        return '<option' + (a === allocation ? ' selected' : '') + '>' + esc(a) + '</option>';
      }).join('');
  var riskOptions = (risk ? '' : '<option value="" selected>Select…</option>')
    + opt('options.riskLevels', []).map(function (r) {
        var probe = allocation
          ? { allocation: allocation, excludeRE: canRE ? exRE : true,
              excludeTAA: exTAA, riskLevel: r }
          : null;
        var ok = probe ? available(probe) : true;
        return '<option value="' + esc(r) + '"'
          + (r === risk ? ' selected' : '') + (ok ? '' : ' disabled') + '>'
          + esc(riskLabel(r)) + (ok ? '' : ' — unavailable') + '</option>';
      }).join('');

  el.className = 'tier' + ring;
  /* Allocation, then risk level, then the two exclusions: the selects are the
     choice, the tick boxes narrow what it produced. */
  el.innerHTML = '<div class="tier-h"><h3>Base portfolio</h3></div>'
    + '<div class="basis" style="grid-template-columns:1fr">'
    + '<div class="field"><label for="bpv">Implementation Variant</label>'
    + '<select id="bpv"' + disabled + '>' + variantOptions + '</select></div>'
    + (variantLocked && !locked
        ? '<p class="chk-note">Choose a variant first — it decides which '
          + 'allocations are available.</p>' : '')
    + '<div class="field"><label for="bpa">Allocation</label><select id="bpa"'
    + afterVariant + '>' + allocationOptions + '</select></div>'
    + '<div class="field"><label for="bpr">Risk level</label><select id="bpr"'
    + (allocation && !variantLocked && canEdit() && schemaReady() ? '' : ' disabled') + '>'
    + riskOptions + '</select></div>'
    + '<div class="chk"><input type="checkbox" id="bpre"'
    + ((allocation && !canRE) || exRE ? ' checked' : '')
    + ((allocation && canRE && canEdit() && !forceExRE) ? '' : ' disabled')
    + ((allocation && (!canRE || forceExRE)) ? ' aria-describedby="bprenote"' : '') + '>'
    + '<label for="bpre">Exclude Real Estate</label></div>'
    + ((allocation && !canRE)
        ? '<p class="chk-note" id="bprenote">Not available — ' + esc(allocation)
          + ' holds no real estate.</p>'
        : (allocation && forceExRE)
        ? '<p class="chk-note" id="bprenote">Required by ' + esc(variant) + '.</p>' : '')
    + '</div>';
}

function renderBuilt() {
  var el = document.getElementById('built'); if (!el) return;
  el.innerHTML = state.columns.map(function (col, i) {
    var status = col.status === 'loading' ? ' <span class="built-status">building…</span>'
      : col.status === 'error' ? ' <span class="built-status err">failed</span>' : '';
    return '<div class="built-row"><span class="nm">' + esc(headerName(col.key)) + status + '</span>'
      + (i === 0 ? '<span class="tag">Base</span>'
                 : '<button type="button" class="rm" data-i="' + i + '" aria-label="Remove '
                   + esc(headerName(col.key)) + '">×</button>')
      + '</div>';
  }).join('');
  var count = document.getElementById('count');
  if (count) {
    count.textContent = (state.columns.length ? state.columns.length - 1 : 0)
      + ' of ' + (opt('rules.maxPortfolios', 4) - 1);
  }
}

/* The strip is exception-only: it says something when something is wrong or
   in flight, and nothing at all when the answer is simply "fine" (D34). The
   settled "Lookup matched" chip is gone - a table full of resolved figures
   already says the lookup matched - as is the portfolio count, which the
   rail's Comparisons tier carries. What remains is what has no other surface:
   a failure aggregate, and the whole-page rebuild of spec 10.1. */
function lookupStatus() {
  if (!state.columns.length) return null;
  var failed = state.columns.filter(function (c) { return c.status === 'error'; }).length;
  var loading = state.columns.filter(function (c) { return c.status === 'loading'; }).length;
  if (failed) return { cls: 'b-breach', text: failed + ' column' + (failed === 1 ? '' : 's') + ' failed' };
  /* Waiting is the spinner's job now, not a pill. A pill for a transient state
     grew the notices strip and shifted the document under the reader; a
     failure is persistent and worth the space, so it stays. */
  return null;
}

function renderNotices() {
  var el = document.getElementById('notices'); if (!el) return;
  if (state.phase !== 'workspace' || !state.columns.length) {
    el.hidden = true; el.innerHTML = ''; return;
  }
  var pieces = [];
  var status = lookupStatus();
  if (status) pieces.push('<span class="bdg ' + status.cls + '">' + esc(status.text) + '</span>');
  var base = state.columns[0];
  if (base && !reAllowed(base.key.allocation)) {
    pieces.push('<span class="bdg b-warn">Real estate excluded — Allocation is '
      + esc(base.key.allocation) + '</span>');
  }
  /* Nothing to say: the strip takes no room rather than sitting there empty. */
  if (!pieces.length) { el.hidden = true; el.innerHTML = ''; return; }
  el.hidden = false;
  el.innerHTML = pieces.join('\n');
}

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

/* The base column is headed "Proposed Portfolio" rather than its derived name
   plus a Base chip: it is the portfolio being proposed to the client, and the
   comparisons are what it is proposed against. The derived name is still
   available - as the hover tooltip, and as the aria-label, so it reaches
   assistive tech without a hover. */
var BASE_COLUMN_TITLE = 'Proposed Portfolio';

function columnHeadCell(col, i, scope) {
  var isBase = (i === 0);
  var label = isBase ? BASE_COLUMN_TITLE : headerName(col.key);
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
    + ' class="num' + (i === App.lastAdded ? ' just-added' : '') + '"'
    + (col.status === 'loading' ? ' aria-busy="true"' : '')
    + ' title="' + esc(fullName(col.key)) + '"'
    + ' aria-label="' + esc(fullName(col.key)) + '">'
    + '<span class="col-head">' + esc(label) + extra + remove + '</span></th>';
}

function renderAlloc() {
  var el = document.getElementById('alloc'); if (!el) return;
  var wrap = document.getElementById('doc-empty');
  if (state.phase === 'workspace' && !state.columns.length) {
    if (wrap) wrap.hidden = false;
    el.innerHTML = '';
    return;
  }
  if (wrap) wrap.hidden = true;

  var plus = App.plusColumn && canEdit()
    && state.columns.length < opt('rules.maxPortfolios', 4) && state.columns.length > 0;
  var html = '<caption class="sr-only">Portfolio allocation and metrics</caption><thead><tr>'
    + '<th scope="col" class="rowhead">Asset Class</th>';
  state.columns.forEach(function (col, i) { html += columnHeadCell(col, i, 'col'); });
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
  function row(cls, label, cellFn, key) {
    var r = '<tr class="' + cls + '"' + (key ? ' data-rk="' + esc(key) + '"' : '')
      + '><th scope="row"><span class="cw">' + esc(label) + '</span></th>'
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
      html += row('cat', category.name, function (col) {
        return cellFor(col, category.name, null);
      }, 'c:' + category.name);
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
  var reduced = window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* One change renders twice: optimistically while the column is loading, and
     again when the resolve lands. The snapshot is taken on the first of those
     and HELD, so the animation runs once, against the true before and after.
     Animating on both instead wiped the in-flight rows halfway through and
     restarted them, which is why the exit never finished.

     Ghosts are excluded: they carry data-rk because they are clones of real
     rows, and counting them would make the next render try to remove them
     again. */
  if (!reduced && !rowSnapshots[slot]) {
    rowSnapshots[slot] = { keys: {}, order: [] };
    var old = table.querySelectorAll('tbody tr[data-rk]:not([data-ghost])');
    for (var i = 0; i < old.length; i++) {
      var key = old[i].getAttribute('data-rk');
      rowSnapshots[slot].keys[key] = { node: old[i].cloneNode(true), height: old[i].offsetHeight };
      rowSnapshots[slot].order.push(key);
    }
  }

  mutate();
  if (reduced) { rowSnapshots[slot] = null; return; }

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
  wrap._fadeTimer = window.setTimeout(function () {
    wrap.style.transition = '';
    wrap.style.opacity = '';
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
  table.style.width = 'auto';
  table.style.minWidth = '0';

  var headerWidth = heads[0].getBoundingClientRect().width;
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
   The preference outlives the page, so a refresh - which this tool does on
   every rehydrate - comes back the way the user left it. */
var RAIL_PREF = 'pmg.proposalTool.railCollapsed';
var railCollapsed = false;
try { railCollapsed = window.localStorage.getItem(RAIL_PREF) === '1'; } catch (e) {}

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
  try { window.localStorage.setItem(RAIL_PREF, railCollapsed ? '1' : '0'); } catch (e) {}
  renderRailToggle();
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
  var reduced = window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!rail || reduced) { sizeComparisonTables(); return; }

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
  table.style.width = 'auto';
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
  function pairRow(cls, label, pairFn) {
    return '<tr class="' + cls + '"><th scope="row"><span class="cw">'
      + esc(label) + '</span></th>'
      + state.columns.map(function (col) {
          if (col.status === 'loading' && !holdsPreviousData(col)) {
            var skel = col.skel ? '<span class="skel"></span>' : '';
            return '<td class="num">' + skel + '</td><td class="num">' + skel + '</td>';
          }
          if (col.status === 'error') {
            return '<td class="num">—</td><td class="num">—</td>';
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
    if (!entry) return '<td class="num">\u2014</td><td class="num">\u2014</td>';
    var digits = probability ? 1 : 2;
    function cls(value) {
      if (!signed) return 'num';        /* only the stress rows are coloured */
      if (value < 0) return 'num neg';
      return value > 0 ? 'num pos' : 'num';
    }
    return '<td class="' + cls(entry.nominalPct) + '">' + num(entry.nominalPct, digits, '%') + '</td>'
         + '<td class="' + cls(entry.realPct) + '">' + num(entry.realPct, digits, '%') + '</td>';
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
    });
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
      });
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
      + 'Base</i><i><span class="sw" style="background:#1F5FBF;border-radius:50%"></span>Comparison</i>';
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
      + '. The allocation table below carries the exact values.">';
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
    ready.forEach(function (col) {
      var isBase = (state.columns.indexOf(col) === 0);
      var x = X(col.data.metrics.volatilityPct), y = Y(col.data.metrics.estimatedReturnPct);
      svg2 += '<circle class="dot" cx="' + x + '" cy="' + y + '" r="' + (isBase ? 7.5 : 6) + '"'
        + ' fill="' + (isBase ? '#16243A' : '#1F5FBF') + '"'
        + ' data-tip="' + esc(headerName(col.key)) + '|vol '
        + col.data.metrics.volatilityPct.toFixed(2) + '% · return '
        + col.data.metrics.estimatedReturnPct.toFixed(2) + '%"></circle>';
      var above = (y > pT + 28);
      var label = headerName(col.key);
      svg2 += '<text class="plbl" x="' + x + '" y="' + (above ? y - 13 : y + 20)
        + '" text-anchor="middle">' + esc(label.length > 20 ? label.slice(0, 19) + '…' : label) + '</text>';
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
    var reduced = window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!reduced) {
      view.classList.remove('doc-reveal');
      void view.offsetWidth;                 /* restart if it is still running */
      view.classList.add('doc-reveal');
      window.setTimeout(function () { view.classList.remove('doc-reveal'); }, 1400);
    }
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
  if (!draft) { host.innerHTML = ''; host.hidden = true; setBackgroundInert(false); return; }
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
    + '<div class="dlg-actions">'
    + '<button type="button" class="btn btn-ghost" id="dlgcancel"'
    + (draft.saving ? ' disabled' : '') + '>Cancel</button>'
    + '<button type="button" class="btn btn-primary" id="dlgsave"'
    + (draft.saving ? ' disabled' : '') + '>'
    + (draft.saving ? 'Working…' : (first ? 'Continue' : 'Save mandate')) + '</button>'
    + '</div></div>';
}

function setBackgroundInert(on) {
  ['.rail', 'main.shell'].forEach(function (selector) {
    var node = document.querySelector(selector);
    if (node && 'inert' in node) node.inert = on;
  });
}

/* ---- refresh ------------------------------------------------------------ */
function refresh() {
  preserveFocus(function () {
    renderRailToggle();
    renderPhase();
    renderDialog();
    renderMandate();
    renderBasis();
    renderBase();
    if (picker && picker.render) picker.render();
    renderBuilt();
    renderNotices();
    renderAlloc();
    renderRisk();
    renderCharts();
    renderStageChrome();
    renderResolving();
    renderFooter();
    extras.forEach(function (fn) { try { fn(); } catch (e) { console.error(e); } });
    sizeComparisonTables();
  });
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
  if (e.target.id === 'startbtn' || e.target.id === 'mdedit') {
    openMandateDialog(e.target); return;
  }
  if (e.target.id === 'schema-retry') { boot(); return; }
  if (e.target.id === 'dlgsave') { commitMandate(); return; }
  if (e.target.id === 'dlgcancel' || e.target.id === 'dlgclose') {
    closeMandateDialog(); return;
  }
  if (e.target.dataset && e.target.dataset.scrim !== undefined) {
    if (draft && !draft.dirty) closeMandateDialog();   /* spec 7.1 dialog rules */
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
  var columnRemove = e.target.closest ? e.target.closest('.col-rm') : null;
  if (columnRemove) { removeComparison(+columnRemove.dataset.remove); return; }
  var retry = e.target.closest ? e.target.closest('.col-retry') : null;
  if (retry) { retryColumn(+retry.dataset.retry); return; }
  var remove = e.target.closest ? e.target.closest('.rm') : null;
  if (remove && remove.dataset.i !== undefined) { removeComparison(+remove.dataset.i); return; }
  if (e.target.id === 'basis-apply') { confirmBasisChange(); return; }
  if (e.target.id === 'basis-cancel') { cancelBasisChange(); return; }
});

document.addEventListener('input', function (e) {
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

document.addEventListener('keydown', function (e) {
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
      /* First answer on a fresh scenario: record it against the basis the
         scenario already carries, and treat the pair as settled - the other
         half keeps whatever the mandate was created with, which is the value
         the schema was fetched against. No rebuild confirmation, because
         there is nothing built yet to rebuild. */
      if (!e.target.value) return;
      state.basis[field] = e.target.value;
      state.basisChosen = true;
      persistBasis();
      fetchSchema();
      refresh();
      return;
    }
    requestBasisChange(field, e.target.value);
    return;
  }

  if (e.target.id === 'bpv') {
    setVariant(e.target.value);
    return;
  }

  if (e.target.id === 'bpa' || e.target.id === 'bpre' || e.target.id === 'bpr') {
    var forced = variantForcesExcludeRE(state.variant);
    var base = state.columns[0] || null;
    if (!base) {
      /* no base yet: accumulate the draft; build once allocation + risk exist */
      var pending = state.baseDraft;
      if (e.target.id === 'bpa') { pending.allocation = e.target.value; pending.excludeRE = false; }
      if (e.target.id === 'bpre') pending.excludeRE = e.target.checked;
      if (e.target.id === 'bpr') pending.riskLevel = e.target.value;
      if (pending.allocation && pending.riskLevel) {
        var pendingCanRE = reAllowed(pending.allocation);
        var built = setBase({
          allocation: pending.allocation,
          excludeRE: pendingCanRE ? (forced || !!pending.excludeRE) : true,
          excludeTAA: true,
          riskLevel: pending.riskLevel
        });
        if (built) {
          state.baseDraft = { allocation: '', excludeRE: false, excludeTAA: true, riskLevel: '' };
        }
      } else {
        refresh();
      }
      return;
    }
    var current = base.key;
    var allocation = (e.target.id === 'bpa') ? e.target.value : current.allocation;
    if (!allocation) return;
    var canRE = reAllowed(allocation);
    var exRE;
    if (!canRE) exRE = true;                       /* forced on, box disabled */
    else if (forced) exRE = true;                  /* the variant mandates it */
    else if (e.target.id === 'bpre') exRE = e.target.checked;
    else if (e.target.id === 'bpa') exRE = false;  /* new allocation: RE held by default */
    else exRE = current.excludeRE;
    var exTAA = true;
    var risk = (e.target.id === 'bpr') ? e.target.value : current.riskLevel;
    if (!risk) { refresh(); return; }
    setBase({ allocation: allocation, excludeRE: exRE, excludeTAA: exTAA, riskLevel: risk });
    return;
  }
});

/* ---- public surface ----------------------------------------------------- */
return {
  /* state accessors */
  state: function () { return state; },
  phase: function () { return state.phase; },
  basis: function () { return state.basis; },
  mandate: function () { return state.mandate; },
  mandateSize: function () { return state.mandate ? state.mandate.mandateSize : null; },
  columns: function () { return state.columns; },
  base: function () { return state.columns[0] || null; },
  step: function () { return state.step; },
  implSeen: function () { return state.implSeen; },
  setStep: function (s) {
    state.step = s;
    if (s === 'impl') state.implSeen = true;   /* stop beckoning once opened */
    announce('polite', s === 'impl' ? 'Implementation step.' : 'Asset allocation step.');
    refresh();
  },
  schema: function () { return state.schema; },
  schemaReady: schemaReady,
  opt: opt,
  canEdit: canEdit,
  canExport: canExport,
  autoSleeveCategories: autoSleeveCategories,
  riskLabel: riskLabel,

  /* keys, names, availability */
  keyStr: keyStr,
  headerName: headerName,
  fullName: fullName,
  available: available,
  used: used,
  slotsLeft: slotsLeft,
  reAllowed: reAllowed,

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
  setFeeSchedule: setFeeSchedule,
  setFeeLevel: setFeeLevel,
  setVariant: setVariant,
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
  boot: boot,
  setPicker: function (p) { picker = p; },
  addRenderer: function (fn) { extras.push(fn); },
  plusColumn: false,
  lastAdded: -1
};
})();
