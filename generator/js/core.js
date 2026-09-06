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
  implSeen: false,                  /* step 2 has been opened at least once */
  variant: null,                    /* implementation type; gates step 2 (D29) */
  /* On by default: the tilt is house practice wherever it can be funded.
     A portfolio that cannot fund it renders the toggle off and disabled,
     and tiltCategories no-ops, so this one default covers both (D50). */
  tacticalTilt: true,
  /* The strategic volatility premium (D53): held by default wherever the
     currency allows it, like the tilt, and forbidden outside the currencies
     the schema names - App.volPremium() applies that gate on read. */
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
  feeLevel: null,                   /* e.g. 'PMG Target' */
  sleeves: {},                      /* category -> sleeve name (pickable categories only) */
  sleeveLib: {},                    /* category -> {status, sleeves, error} */
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
      var probe = buildKey(key.allocationType, key.excludeRealAssets, risks[i]);
      if (probe && available(probe)) { fallen = probe; break; }
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
  state.baseDraft = { allocationType: '', excludeRealAssets: false, riskLevel: '' };
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
      state.volPremium = !(created.scenario && created.scenario.volPremium === false);
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
    + '</dl>';
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
    + '<div class="field"><label for="ccy">Base Currency</label><select id="ccy"' + disabled + '>'
    + (pending ? placeholder : '')
    + opt('options.currencies', []).map(function (c) {
        return '<option' + (!pending && c === showing.currency ? ' selected' : '') + '>'
          + esc(c) + '</option>';
      }).join('')
    + '</select></div>'
    + '<div class="field"><label for="hedge">Currency Hedging</label><select id="hedge"' + disabled + '>'
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
    announce('polite', on ? 'Base portfolio settings hidden.'
                          : 'Base portfolio settings shown.');
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
  if (!body || !roll || prefersReducedMotion()) {
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

function prefersReducedMotion() {
  return !!(window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
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
  var forceExRA = variantForcesExcludeRE(variant);
  if (forceExRA && canRA) exRA = true;

  var variants = opt('options.implementationVariants', []);
  var variantOptions = (variant ? '' : '<option value="" selected>Select…</option>')
    + variants.map(function (v) {
        return '<option' + (v === variant ? ' selected' : '') + '>' + esc(v) + '</option>';
      }).join('');

  var allocationOptions = allEquity
    ? '<option value="" selected>—</option>'
    : (allocationType ? '' : '<option value="" selected>Select…</option>')
      + allocationChoices(risk).map(function (a) {
          return '<option' + (a === allocationType ? ' selected' : '') + '>' + esc(a) + '</option>';
        }).join('');
  /* Every level is offered; a level the current allocation cannot reach is
     shown disabled, and an all-equity level is reachable with no allocation
     at all - so the risk select is live as soon as the variant is. */
  var riskOptions = (risk ? '' : '<option value="" selected>Select…</option>')
    + opt('options.riskLevels', []).map(function (r) {
        var probe = buildKey(allocationType, exRA, r);
        var ok = probe ? available(probe) : true;
        return '<option value="' + esc(r) + '"'
          + (r === risk ? ' selected' : '') + (ok ? '' : ' disabled') + '>'
          + esc(riskLabel(r)) + (ok ? '' : ' — unavailable') + '</option>';
      }).join('');

  var raNote = '';
  if (allEquity) {
    raNote = 'An all-equity book holds no alternatives, so no allocation type '
      + 'or exclusion applies.';
  } else if (allocationType && !canRA) {
    raNote = 'Not available — ' + esc(allocationType) + ' holds no real assets.';
  } else if (allocationType && forceExRA) {
    raNote = 'Required by ' + esc(variant) + '.';
  }
  var raEnabled = !allEquity && allocationType && canRA && canEdit() && !forceExRA;

  el.className = 'tier' + ring;
  /* Allocation, then risk level, then the exclusion: the selects are the
     choice, the tick box narrows what it produced. */
  /* The heading carries the chevron, so the tier can be reopened wherever it
     was rolled up; the controls go in .tier-body, which is the thing that
     rolls. aria-expanded and aria-controls carry the state to a reader. */
  el.innerHTML = '<div class="tier-h"><h3 id="basetitle">Base portfolio</h3>'
    + '<button type="button" class="tier-roll" id="baseroll"'
    + ' aria-expanded="' + (state.baseCollapsed ? 'false' : 'true') + '"'
    + ' aria-controls="basebody" aria-label="'
    + (state.baseCollapsed ? 'Show' : 'Hide') + ' the base portfolio settings"'
    + ' title="' + (state.baseCollapsed ? 'Show' : 'Hide')
    + ' the base portfolio settings"><span aria-hidden="true">&#8250;</span>'
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
    + '<div class="field"><label for="bpr">Risk Level</label><select id="bpr"'
    + afterVariant + '>' + riskOptions + '</select></div>'
    + '<div class="field"><label for="bpa">Allocation</label><select id="bpa"'
    + (afterVariant || (allEquity ? ' disabled' : '')) + '>' + allocationOptions + '</select></div>'
    + '<div class="chk"><input type="checkbox" id="bpre"'
    + (exRA && canRA && !allEquity ? ' checked' : '')
    + (raEnabled ? '' : ' disabled')
    + (raNote ? ' aria-describedby="bprenote"' : '') + '>'
    + '<label for="bpre">Exclude Real Assets</label></div>'
    + (raNote ? '<p class="chk-note" id="bprenote">' + raNote + '</p>' : '')
    + '</div></div>';
  applyBaseRoll();
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
    + '<div class="dialog' + (first ? ' start' : '') + '" role="dialog" aria-modal="true" aria-labelledby="dlgTitle">'
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
    /* The landing card seats three actions: Continue then Cancel, far left as
       a group, and Create Account Opening Request right (D76). Edit mandate
       keeps its right-aligned pair. */
    + (first
        ? '<div class="dlg-actions split"><div class="dlg-grp">'
          + '<button type="button" class="btn btn-primary" id="dlgsave"' + dis + '>'
          + (draft.saving ? 'Working…' : 'Continue') + '</button>'
          + '<button type="button" class="btn btn-ghost" id="dlgcancel"' + dis + '>Cancel</button>'
          + '</div>'
          + '<button type="button" class="btn btn-ghost" id="dlgaccount"' + dis + '>'
          + 'Create Account Opening Request</button></div>'
        : '<div class="dlg-actions">'
          + '<button type="button" class="btn btn-ghost" id="dlgcancel"' + dis + '>Cancel</button>'
          + '<button type="button" class="btn btn-primary" id="dlgsave"' + dis + '>'
          + (draft.saving ? 'Working…' : 'Save mandate') + '</button></div>')
    + '</div>';
}

function setBackgroundInert(on) {
  ['.rail', 'main.shell'].forEach(function (selector) {
    var node = document.querySelector(selector);
    if (node && 'inert' in node) node.inert = on;
  });
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
var UID_PATTERN = /^pr_[0-9a-f]{12}$/;
var UID_SHAPE = 'A Proposal UID is pr_ followed by twelve letters or digits.';

/* The fields the user supplies. Option lists come from the schema
   (options.accountRequest), so the page never carries a list of its own. */
var AC_FIELDS = [
  { id: 'acclient', key: 'clientName',    label: 'Client / account name', kind: 'text',   required: true },
  { id: 'actype',   key: 'accountType',   label: 'Account type',          kind: 'select', required: true, options: 'accountTypes' },
  { id: 'acbook',   key: 'bookingCentre', label: 'Booking centre',        kind: 'select', required: true, options: 'bookingCentres' },
  { id: 'actax',    key: 'taxResidency',  label: 'Tax residency',         kind: 'text',   required: true, placeholder: 'Country' },
  { id: 'acfund',   key: 'fundingAmount', label: 'Initial funding',       kind: 'money',  required: true,
    hint: 'From the mandate size. Change it if the first tranche differs.' },
  { id: 'acsrc',    key: 'fundingSource', label: 'Funding source',        kind: 'select', required: true, options: 'fundingSources' },
  { id: 'acdate',   key: 'fundingDate',   label: 'Expected funding date', kind: 'date',   required: true },
  { id: 'ackyc',    key: 'kycReference',  label: 'KYC / onboarding reference', kind: 'text', required: false },
  { id: 'acnotes',  key: 'notes',         label: 'Notes for Onboarding',  kind: 'textarea', required: false, wide: true,
    placeholder: 'Anything Onboarding should know that the proposal does not say.' }
];

function acFieldId(key) {
  if (key === 'proposalId') return 'acuid';
  var spec = AC_FIELDS.filter(function (s) { return s.key === key; })[0];
  return spec ? spec.id : null;
}

function openAccountDialog() {
  if (!canEdit()) return;
  acOpener = dlgOpener;               /* the landing button that opened the card */
  if (draft) { draft = null; renderDialog(); }    /* the card gives way, no focus bounce */
  account = {
    uid: '', status: 'idle',          /* 'idle'|'looking'|'found'|'notfound'|'malformed'|'error' */
    message: '', proposal: null, requests: [],
    fields: { clientName: '', accountType: '', bookingCentre: '', taxResidency: '',
              fundingAmount: null, fundingSource: '', fundingDate: '', kycReference: '', notes: '' },
    fundingTouched: false,            /* the default (mandate size) never overwrites a typed amount */
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
  renderAccountDialog();
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
  try {
    var found = await apiFetch('/scenario/proposals/' + encodeURIComponent(uid));
    if (!account || account.seq !== seq) return;
    account.status = 'found'; account.message = '';
    account.proposal = found.proposal; account.requests = found.requests || [];
    if (!account.fundingTouched && !(account.fields.fundingAmount > 0)) {
      account.fields.fundingAmount = found.proposal.mandateSize || null;
    }
    announce('polite', 'Proposal found: ' + found.proposal.primaryPwa + ', '
      + money(found.proposal.mandateSize) + '.');
  } catch (err) {
    if (!account || account.seq !== seq) return;
    account.status = (err && err.status === 404) ? 'notfound'
      : (err && err.status === 422) ? 'malformed' : 'error';
    account.message = (err && err.message) || 'The lookup failed.';
  }
  preserveFocus(renderAccountDialog);
}

function accountMissing() {
  var f = account.fields;
  return AC_FIELDS.filter(function (spec) {
    if (!spec.required) return false;
    var v = f[spec.key];
    return (spec.kind === 'money') ? !(v > 0) : !(v && String(v).trim());
  });
}

function accountReady() {
  return !!(account && account.status === 'found' && !account.requests.length
            && !accountMissing().length);
}

function accountStatusText() {
  var a = account;
  if (a.status !== 'found') return 'Enter a Proposal UID to begin.';
  if (a.requests.length) {
    var r = a.requests[0];
    return 'An account opening request already exists for this proposal: ' + r.requestId
      + ' by ' + r.submittedBy + ' on ' + whenText(r.submittedAt) + '.';
  }
  var missing = accountMissing();
  if (missing.length) {
    return missing.length + ' required field' + (missing.length === 1 ? '' : 's') + ' remaining: '
      + missing.map(function (m) { return m.label; }).join(', ') + '.';
  }
  return 'Ready. Submitting records the request against ' + a.proposal.proposalId + '.';
}

function whenText(iso) {
  if (!iso) return '';
  var d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  var date = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  var time = String(iso).slice(11, 16);
  return /^\d\d:\d\d$/.test(time) ? date + ' ' + time : date;
}

function riskAllocationText(p) {
  var risk = riskLabel(p.riskLevel);
  if (!p.allocationType) return risk;
  return risk + ' · ' + p.allocationType + (p.excludeRealAssets ? ' · ex Real Assets' : '');
}

function switchesText(p) {
  var on = [];
  if (p.tacticalTilt) on.push('Tactical tilt');
  if (p.volPremium) on.push('Vol premium');
  return on.length ? on.join(' · ') : 'None';
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
  host.innerHTML =
      '<div class="scrim" data-scrim></div>'
    + '<div class="dialog account" role="dialog" aria-modal="true" aria-labelledby="acTitle">'
    + '<button type="button" class="dlg-close" id="acclose" aria-label="Close">×</button>'
    + (account.receipt ? accountReceiptMarkup() : accountFormMarkup())
    + '</div>';
}

function accountFormMarkup() {
  var a = account, p = a.proposal;
  var found = (a.status === 'found');
  var busy = a.submitting;
  /* the account fields open only for a proposal that can still be requested */
  var editable = found && !a.requests.length;
  var errField = a.err && a.err.field;
  function invalid(id) {
    return (errField === id) ? ' aria-invalid="true" aria-describedby="acerr"' : '';
  }

  var uidHint;
  if (a.status === 'looking') {
    uidHint = '<span class="field-hint busy" id="achint" role="status">Looking up…</span>';
  } else if (found) {
    uidHint = '<span class="field-hint ok" id="achint" role="status">✓ Proposal found · '
      + esc(p.currency + ' ' + p.hedging) + ' · exported ' + esc(whenText(p.exportedAt))
      + ' by ' + esc(p.exportedBy)
      + (p.sequence > 1 ? ' · proposal #' + p.sequence + ' of its scenario' : '') + '</span>';
  } else if (a.status === 'idle') {
    uidHint = '<span class="field-hint" id="achint">The last part of the workbook\'s name, '
      + 'and the first row of its Implementation sheet.</span>';
  } else {
    uidHint = '<p class="md-err dlg-uiderr" id="achint" role="alert">' + esc(a.message || UID_SHAPE) + '</p>';
  }

  /* Filled from the proposal: greyed, visibly filled, not editable. */
  var ro = [
    ['Primary PWA', p ? p.primaryPwa : ''],
    ['Mandate size', p ? money(p.mandateSize) : ''],
    ['Top account size', p ? money(p.topAccountSize) : ''],
    ['Basis', p ? p.currency + ' · ' + p.hedging : ''],
    ['Implementation type', p ? p.variant : ''],
    ['Risk · allocation', p ? riskAllocationText(p) : ''],
    ['Fees', p ? (p.includeFees ? (p.feeSchedule || '') + ' · ' + (p.feeLevel || '') : 'No fees') : ''],
    ['Switches', p ? switchesText(p) : '']
  ];
  var roMarkup = ro.map(function (pair, i) {
    return '<div class="field"><label for="acro' + i + '">' + esc(pair[0]) + '</label>'
      + '<input type="text" id="acro' + i + '" class="ro" readonly value="' + esc(pair[1]) + '"></div>';
  }).join('');
  var sleeves = (p && p.sleeves && p.sleeves.length)
    ? p.sleeves.map(function (s) {
        return '<div><span>' + esc(s.category) + '</span><span>' + esc(s.sleeve || '—')
          + (s.revision ? '<em>r' + esc(String(s.revision)) + '</em>' : '') + '</span></div>';
      }).join('')
    : '<div class="none">' + (p ? 'No sleeves recorded.' : '—') + '</div>';

  var fieldsMarkup = AC_FIELDS.map(function (spec) {
    var v = a.fields[spec.key];
    var label = '<label for="' + spec.id + '">' + esc(spec.label)
      + (spec.required ? '<span class="req" aria-hidden="true">•</span>' : '') + '</label>';
    var common = ' id="' + spec.id + '" data-acf="' + spec.key + '"'
      + (spec.required ? ' aria-required="true"' : '') + invalid(spec.id)
      + ((editable && !busy) ? '' : ' disabled');
    var control;
    if (spec.kind === 'select') {
      var choices = opt('options.accountRequest.' + spec.options, []);
      control = '<select' + common + '><option value=""' + (v ? '' : ' selected') + '>Select…</option>'
        + choices.map(function (o) {
            return '<option value="' + esc(o) + '"' + (o === v ? ' selected' : '') + '>' + esc(o) + '</option>';
          }).join('') + '</select>';
    } else if (spec.kind === 'textarea') {
      control = '<textarea' + common + ' rows="2" placeholder="' + esc(spec.placeholder || '') + '">'
        + esc(v || '') + '</textarea>';
    } else if (spec.kind === 'date') {
      control = '<input type="date"' + common + ' value="' + esc(v || '') + '">';
    } else if (spec.kind === 'money') {
      control = '<input type="text" inputmode="numeric" autocomplete="off"' + common
        + ' value="' + (v > 0 ? esc(money(v)) : '') + '">';
    } else {
      control = '<input type="text" autocomplete="off"' + common
        + (spec.placeholder ? ' placeholder="' + esc(spec.placeholder) + '"' : '')
        + ' value="' + esc(v || '') + '">';
    }
    return '<div class="field' + (spec.wide ? ' wide' : '') + '">' + label + control
      + (spec.hint ? '<span class="field-hint">' + esc(spec.hint) + '</span>' : '') + '</div>';
  }).join('');

  var ready = accountReady() && !busy;
  var uidInvalid = (errField === 'acuid' || a.status === 'notfound' || a.status === 'malformed'
                    || a.status === 'error');
  return '<h2 id="acTitle" class="dlg-shout">Account opening request</h2>'
    + '<p class="dlg-sub">Open an account on the terms of a delivered proposal.</p>'
    + '<div class="field"><label for="acuid">Proposal UID<span class="req" aria-hidden="true">•</span></label>'
    + '<input type="text" id="acuid" autocomplete="off" spellcheck="false" autocapitalize="off"'
    + ' aria-describedby="achint" aria-required="true" value="' + esc(a.uid) + '"'
    + (uidInvalid ? ' aria-invalid="true"' : '') + (busy ? ' disabled' : '') + '>'
    + uidHint + '</div>'
    + '<div class="dlg-sect">From the proposal'
    + (found ? '<span class="dlg-tag">filled · read only</span>' : '') + '</div>'
    + '<div class="' + (found ? '' : 'dlg-muted') + '"><div class="dlg-grid">' + roMarkup + '</div>'
    + '<div class="field"><label>Sleeves</label><div class="dlg-sleeves">' + sleeves + '</div></div></div>'
    + '<div class="dlg-sect">Account details</div>'
    + '<div class="' + (editable ? '' : 'dlg-muted') + '"><div class="dlg-grid">' + fieldsMarkup + '</div></div>'
    + (a.err ? '<p class="md-err" id="acerr" role="alert">' + esc(a.err.msg) + '</p>' : '')
    + '<div class="dlg-actions split">'
    + '<span class="dlg-status' + (ready ? ' ok' : '') + '" id="acstatus" role="status">'
    + esc(accountStatusText()) + '</span>'
    + '<div class="dlg-grp">'
    + '<button type="button" class="btn btn-ghost" id="accancel"' + (busy ? ' disabled' : '') + '>Cancel</button>'
    + '<button type="button" class="btn btn-primary" id="acsubmit"' + (ready ? '' : ' disabled') + '>'
    + (busy ? 'Working…' : 'Submit request') + '</button>'
    + '</div></div>';
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

/* The footer alone re-renders while the user types: a full re-render would
   fight the caret in a textarea and drop a select mid-choice. */
function patchAccountFooter() {
  var status = document.getElementById('acstatus');
  var submit = document.getElementById('acsubmit');
  if (!status || !submit || !account) return;
  var ready = accountReady() && !account.submitting;
  status.textContent = accountStatusText();
  status.classList.toggle('ok', ready);
  submit.disabled = !ready;
  var bad = document.querySelector('#accountDialog [data-acf][aria-invalid="true"]');
  if (bad) { bad.removeAttribute('aria-invalid'); bad.removeAttribute('aria-describedby'); }
  var err = document.getElementById('acerr');
  if (err) err.remove();
}

function onAccountInput(e) {
  var t = e.target;
  if (!t || !account || account.receipt) return false;
  if (t.id === 'acuid') { onUidInput(t.value); return true; }
  var key = t.dataset && t.dataset.acf;
  if (!key) return false;
  var spec = AC_FIELDS.filter(function (s) { return s.key === key; })[0];
  if (!spec) return false;
  if (spec.kind === 'money') {
    account.fields[key] = parseMoney(t.value) || null;
    account.fundingTouched = true;
  } else {
    account.fields[key] = t.value;
  }
  account.err = null; account.dirty = true;
  patchAccountFooter();
  return true;
}

async function submitAccountRequest() {
  if (!account || account.submitting || !accountReady()) return;
  account.submitting = true; account.err = null;
  renderAccountDialog();
  var body = { proposalId: account.proposal.proposalId };
  AC_FIELDS.forEach(function (spec) { body[spec.key] = account.fields[spec.key]; });
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
    /* the proposal has changed under us - gone, or now requested - so ask again */
    if (field === 'proposalId' && (err.status === 404 || err.status === 422)) {
      lookupProposal(account.proposal.proposalId, ++account.seq);
    }
  }
}

function copyRequestReference(button) {
  if (!account || !account.receipt) return;
  var text = account.receipt.requestId;
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
      sleeves: state.sleeves,
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
  state.sleeves = snap.sleeves || {};
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
    renderBuilt();
    renderAlloc();
    renderRisk();
    renderCharts();
    renderStageChrome();
    renderService();
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
  if (e.target.id === 'dlgsave') { commitMandate(); return; }
  if (e.target.id === 'dlgcancel' || e.target.id === 'dlgclose') {
    closeMandateDialog(); return;
  }
  if (e.target.id === 'dlgaccount') { openAccountDialog(); return; }
  if (e.target.id === 'accancel' || e.target.id === 'acclose' || e.target.id === 'acdone') {
    if (!(account && account.submitting)) closeAccountDialog();
    return;
  }
  if (e.target.id === 'acsubmit') { submitAccountRequest(); return; }
  if (e.target.id === 'accopy') { copyRequestReference(e.target); return; }
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
  if (account && e.target.id === 'acfund') {          /* the amount re-formats on blur */
    var amount = parseMoney(e.target.value);
    account.fields.fundingAmount = amount || null;
    e.target.value = amount ? money(amount) : '';
    patchAccountFooter();
    return;
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

/* selects and the date control report through change rather than input */
document.addEventListener('change', function (e) {
  if (account && e.target && e.target.dataset && e.target.dataset.acf) onAccountInput(e);
});

document.addEventListener('keydown', function (e) {
  if (account && !draft) {
    if (e.key === 'Escape') {
      e.preventDefault();
      if (!account.submitting) closeAccountDialog();
      return;
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
    var wasStep = state.step;
    state.step = s;
    if (s === 'impl') state.implSeen = true;   /* stop beckoning once opened */
    /* The base tier follows the step: rolled up while the implementation is
       being built, open again on the allocation step where it is the work. */
    if (s !== wasStep) setBaseCollapsed(s === 'impl', true);
    announce('polite', s === 'impl' ? 'Implementation step.' : 'Asset allocation step.');
    refresh();
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

  /* keys, names, availability */
  keyStr: keyStr,
  headerName: headerName,
  fullName: fullName,
  available: available,
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
