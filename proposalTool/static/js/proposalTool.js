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
    var resp;
    try {
        resp = await fetch(window.API_BASE + path,
                           Object.assign({credentials: 'same-origin'}, opts || {}));
    } catch (transport) {
        // the frontend itself is unreachable - no response at all (D64)
        if (window.App && App.noteService) App.noteService(false, 0);
        var dead = new Error('The Proposal Tool could not reach its service.');
        dead.status = 0;
        throw dead;
    }
    // One place learns whether the service is answering: the proxy turns an
    // unreachable backend into 502 and a slow one into 504 (D64).
    if (window.App && App.noteService) App.noteService(resp.status !== 502 && resp.status !== 504, resp.status);
    if (!resp.ok) {
        var msg = 'Request failed (' + resp.status + ')';
        var body = null;
        try {
            body = await resp.json();
            if (body && body.loginUrl) { window.location = body.loginUrl; return; }
            if (body && body.error) msg = body.error;
        } catch (e) { /* non-JSON error body */ }
        var err = new Error(msg);
        err.status = resp.status;   // callers map 422 field errors and 404s
        err.body = body;
        throw err;
    }
    return resp.json();
}

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


(function () {
'use strict';
/* =============================================================================
   Picker layer: the + column's add-comparison popover (spec 7.3).

   Controls mirror the base tier - Allocation select, the two exclusion tick
   boxes, Risk level - because the spec never enumerates the popover's own
   controls and Q32 settled that four options plus two tick boxes beat a
   compound select (recorded as gap G2). Adding happens here: "Add to table"
   inserts the column at once and the popover stays open with the risk select
   cleared, so three comparisons are three presses. Below 1040px it becomes a
   bottom sheet (spec 12).
   ========================================================================== */
App.plusColumn = true;

var pop = document.createElement('div');
pop.className = 'pop';
pop.setAttribute('role', 'dialog');
pop.setAttribute('aria-modal', 'false');
pop.setAttribute('aria-label', 'Add a comparison portfolio');
function mountPop() { document.body.appendChild(pop); }
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', mountPop);
} else { mountPop(); }

/* The popover mirrors the base tier (D12): allocation type, the real-assets
   exclusion, risk level - and App.buildKey decides when they are enough, so
   an all-equity level needs no allocation at all (D54). */
var cur = { allocationType: '', excludeRealAssets: false, riskLevel: '' };
var opener = null;
var justAdded = '';

function currentKey() {
  return App.buildKey(cur.allocationType, cur.excludeRealAssets, cur.riskLevel);
}

function paint() {
  var left = App.slotsLeft();
  if (left <= 0) {
    pop.innerHTML = '<button type="button" class="pop-close" aria-label="Close">×</button>'
      + '<h4>Add comparison</h4>'
      + '<p class="pop-full">All three comparison slots are in use. Remove one from the rail '
      + 'to free a slot.</p>'
      + '<div class="pop-actions"><button type="button" class="btn btn-ghost" id="pdone">'
      + 'Done</button></div>';
    return;
  }
  var allEquity = App.isAllEquityRisk(cur.riskLevel);
  var canRA = cur.allocationType ? App.reAllowed(cur.allocationType) : false;
  var key = currentKey();
  var ok = key && App.available(key) && !App.used(key);
  var allocationOptions = allEquity
    ? '<option value="" selected>—</option>'
    : (cur.allocationType ? '' : '<option value="" selected>Select…</option>')
      + App.allocationChoices(cur.riskLevel).map(function (a) {
          return '<option' + (a === cur.allocationType ? ' selected' : '') + '>' + App.esc(a) + '</option>';
        }).join('');
  var riskOptions = '<option value=""' + (cur.riskLevel ? '' : ' selected') + '>Select…</option>'
    + App.opt('options.riskLevels', []).map(function (r) {
        var probe = App.buildKey(cur.allocationType, cur.excludeRealAssets, r);
        var why = '';
        var enabled = true;
        if (probe) {
          if (!App.available(probe)) { enabled = false; why = ' — unavailable'; }
          else if (App.used(probe)) { enabled = false; why = ' — already added'; }
        }
        return '<option value="' + App.esc(r) + '"' + (r === cur.riskLevel ? ' selected' : '')
          + (enabled ? '' : ' disabled') + '>' + App.esc(App.riskLabel(r)) + why + '</option>';
      }).join('');

  pop.innerHTML = '<button type="button" class="pop-close" aria-label="Close">×</button>'
    + '<h4>Add comparison</h4>'
    + '<p class="slots">' + left + ' slot' + (left === 1 ? '' : 's') + ' remaining'
    + (justAdded ? ' · <span class="pop-added">' + App.esc(justAdded) + ' added</span>' : '')
    + '</p>'
    + '<div class="field"><label for="pa">Allocation</label><select id="pa"'
    + (allEquity ? ' disabled' : '') + '>' + allocationOptions + '</select></div>'
    + '<div class="chk"><input type="checkbox" id="pre"'
    + (cur.excludeRealAssets && canRA && !allEquity ? ' checked' : '')
    + ((cur.allocationType && canRA && !allEquity) ? '' : ' disabled')
    + ((allEquity || (cur.allocationType && !canRA)) ? ' aria-describedby="prenote"' : '') + '>'
    + '<label for="pre">Exclude Real Assets</label></div>'
    + (allEquity
        ? '<p class="chk-note" id="prenote">An all-equity book holds no alternatives, so '
          + 'no allocation type or exclusion applies.</p>'
        : (cur.allocationType && !canRA)
        ? '<p class="chk-note" id="prenote">Not available — ' + App.esc(cur.allocationType)
          + ' holds no real assets.</p>' : '')
    + '<div class="field"><label for="pr">Risk Level</label><select id="pr">'
    + riskOptions + '</select></div>'
    + '<div class="pop-actions">'
    + '<button type="button" class="btn btn-primary" id="padd"' + (ok ? '' : ' disabled') + '>'
    + 'Add to table</button>'
    + '<button type="button" class="btn btn-ghost" id="pdone">Done</button></div>';
}

function isSheet() {
  return window.matchMedia && window.matchMedia('(max-width: 1039px)').matches;
}

function open(button) {
  opener = button;
  justAdded = '';
  paint();
  pop.classList.add('on');
  pop.classList.toggle('sheet', isSheet());
  if (!isSheet()) {
    var rect = button.getBoundingClientRect();
    var height = pop.offsetHeight || 320;
    pop.style.top = Math.max(12, Math.min(window.innerHeight - height - 12, rect.bottom + 8)) + 'px';
    pop.style.left = Math.max(12, Math.min(window.innerWidth - 292, rect.right - 280)) + 'px';
  } else {
    pop.style.top = ''; pop.style.left = '';
  }
  var field = pop.querySelector('#pa');
  if (field) field.focus();
}

function close() {
  pop.classList.remove('on');
  var plus = document.getElementById('plusbtn');
  if (plus) plus.focus();
  else if (opener && opener.isConnected) opener.focus();
}

document.addEventListener('click', function (e) {
  if (e.target.id === 'plusbtn') { open(e.target); return; }
  /* a click whose target was re-rendered away mid-bubble is not an outside
     click - without this, "Add to table" would close the popover it just
     repainted */
  if (!e.target.isConnected) return;
  if (e.target.closest && e.target.closest('.pop')) return;
  if (pop.classList.contains('on')) close();
});

pop.addEventListener('change', function (e) {
  if (e.target.id === 'pa') {
    cur.allocationType = e.target.value;
    cur.excludeRealAssets = false;
    justAdded = '';
    paint();
    var risk = pop.querySelector('#pr');
    if (risk && cur.allocationType && !cur.riskLevel) risk.focus();
    return;
  }
  if (e.target.id === 'pre') { cur.excludeRealAssets = e.target.checked; paint(); return; }
  if (e.target.id === 'pr') { cur.riskLevel = e.target.value || ''; paint(); return; }
});

pop.addEventListener('click', function (e) {
  if (e.target.id === 'padd') {
    var key = currentKey();
    if (key && App.addComparison(key)) {
      justAdded = App.headerName(key);
      cur.riskLevel = '';
      paint();
      var risk = pop.querySelector('#pr');
      if (risk) risk.focus();
    }
    return;
  }
  if (e.target.id === 'pdone' || (e.target.closest && e.target.closest('.pop-close'))) close();
});

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && pop.classList.contains('on')) close();
});

App.setPicker({
  render: function () { if (pop.classList.contains('on')) paint(); }
});
})();

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

/* Sleeve groups (D60): categories implemented together, which therefore carry
   one sleeve chosen once. The grouping rides the schema, so this file names no
   category - THE PYTHON MIRROR is rules.sleeveCategory / rules.sleeveCategories
   and the two must agree, or the rail would offer a choice the export gate
   does not ask for. */
function sleeveGroups() { return App.opt('rules.sleeveGroups', []) || []; }

function sleeveCategory(category) {
  var groups = sleeveGroups();
  for (var i = 0; i < groups.length; i += 1) {
    if ((groups[i].categories || []).indexOf(category) !== -1) return groups[i].name;
  }
  return category;
}

/* The categories reduced to the things a sleeve is chosen FOR, each carrying
   the summed weight of what it stands for. */
function sleeveCategories(categories) {
  var out = [], byName = {};
  categories.forEach(function (entry) {
    var name = sleeveCategory(entry.name);
    if (byName[name]) {
      byName[name].weightPct += entry.weightPct;
      byName[name].members.push(entry.name);
      return;
    }
    byName[name] = { name: name, weightPct: entry.weightPct, members: [entry.name] };
    out.push(byName[name]);
  });
  return out;
}

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

function managementFee(feeGroup) {
  return resolveFee(feeRates(), App.feeSchedule(), App.feeLevel(), feeGroup);
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
  var under = sleeveCategory(category);
  var lib = App.sleeveLib()[under];
  if (!lib || lib.status !== 'ready') return null;
  if (isAuto(category)) return lib.sleeves[0] || null;
  var name = App.sleeves()[under];
  if (!name) return null;
  for (var i = 0; i < lib.sleeves.length; i++) {
    if (lib.sleeves[i].name === name) return lib.sleeves[i];
  }
  return null;
}

function ensureLibraries() {
  if (App.step() !== 'impl' || !App.variant()) return;
  sleeveCategories(baseCategories()).forEach(function (category) {
    App.ensureSleeveLib(category.name);
  });
}

function filledCount() {
  var live = sleeveCategories(baseCategories());
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
  var live = sleeveCategories(baseCategories()).filter(function (c) { return !isAuto(c.name); });
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
  /* Categories that share one sleeve share one LINE: Private Equity and Other
     Private Assets are one choice in the rail (D60) and one sleeve in the
     repository, so they are one row whose weight is the sum of theirs.
     Mirrors buildImplementationRows. */
  var combined = [], seen = {};
  baseCategories().forEach(function (category) {
    var key = sleeveCategory(category.name);
    if (seen[key] === undefined) {
      seen[key] = combined.length;
      combined.push({ name: key, weightPct: 0 });
    }
    combined[seen[key]].weightPct += category.weightPct;
  });
  combined.forEach(function (category) {
    var sleeve = sleeveFor(category.name);
    var items = [];
    if (sleeve) {
      sleeve.products.forEach(function (product) {
        var item = {
          name: product.name, ticker: product.ticker, assetClass: product.assetClass,
          style: product.style, vehicle: product.vehicle, source: product.source,
          liquidity: product.liquidity, exposureCurrency: product.exposureCurrency,
          cost: product.productCost, feeGroup: product.feeGroup,
          minimumInvestment: product.minimumInvestment,
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
      /* A position smaller than the product will accept is not a position
         (item 3). Mirrors buildImplementationRows. */
      item.belowMinimum = !!item.minimumInvestment && item.notional < item.minimumInvestment;
    });
  }
  /* Nothing that prints as zero earns a line (D68). Dropped after the
     rounding, so the column still closes on 100.00 exactly - a row worth
     0.00 adds nothing to that sum. A category keeps its row while it has
     weight: an unimplemented one with an allocation is precisely what this
     page is asking a PWA to fix. Mirrors buildImplementationRows. */
  groups.forEach(function (group) {
    group.items = group.items.filter(function (item) { return item.weight !== 0; });
  });
  return groups.filter(function (group) { return group.weightPct !== 0; });
}

/* Every position that falls below its product's minimum (item 3). The export
   is refused while this is non-empty, on the page and on the server. */
function breaches(groups) {
  var out = [];
  groups.forEach(function (group) {
    group.items.forEach(function (item) {
      if (item.belowMinimum) {
        out.push({ category: group.category, name: item.name,
                   notional: item.notional, minimum: item.minimumInvestment });
      }
    });
  });
  return out;
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
      + '% of ' + App.esc(from) + ' after tilts';
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
var railPadWas = null;                /* the rail's own padding, while borrowed */

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
  /* Judge the guard on the height the body is growing INTO, not the height it
     has this frame. The unravel clips it with max-height, so for the first
     frames the group is short enough to look as though it already fits, the
     guard returns, and the only scroll left is the one settleFeeReveal fires
     on animationend - which is what made the rail appear to wait for the
     table. scrollHeight sees past the clip, so the decision is the same at
     the start of the animation as at the end and the two move together. */
  var body = group.querySelector('.fee-body');
  var pending = body
    ? Math.max(0, body.scrollHeight - body.getBoundingClientRect().height)
    : 0;
  if (groupBox.top >= railBox.top
      && groupBox.bottom + pending <= railBox.bottom) return;
  var brand = rail.querySelector('.rail-brand');
  var pad = (brand ? brand.offsetHeight : 0) + 10;
  var top = rail.scrollTop + (groupBox.top - railBox.top) - pad;
  /* The clip shortens the rail's own scroll range as well, so the browser
     clamps this scroll to whatever range exists mid-animation and the rest is
     made up only once the block has grown - which is the staging this exists
     to remove, one layer down. Borrow exactly the shortfall as padding so the
     range the scroll needs is there when it is issued, and hand it back when
     the block has grown into it: by then the real content occupies that space,
     so the scroll position does not move when the padding goes.

     Never borrow more than the block will actually add. The target can sit
     past the end of the rail - a group near the foot cannot be brought to the
     top of a viewport that has nothing left beneath it - and padding past that
     point buys a scroll that snaps back the moment it is handed in, which is a
     worse jump than the one being fixed. Capped here, the scroll goes as far
     as it can while the block grows, and settleFeeReveal takes up any few
     pixels left when the rest of the rail settles. */
  var shortfall = Math.min(
    Math.round(top - (rail.scrollHeight - rail.clientHeight)),
    Math.round(pending));
  if (shortfall > 0 && railPadWas === null) {
    railPadWas = rail.style.paddingBottom;
    var own = parseFloat(window.getComputedStyle(rail).paddingBottom) || 0;
    rail.style.paddingBottom = (own + shortfall) + 'px';
    /* animationend is the normal release; this is the backstop for a render
       that replaces the node mid-play, where it never fires. */
    window.setTimeout(releaseRailPad, FEE_MOTION + 80);
  }
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
function releaseRailPad() {
  if (railPadWas === null) return;
  var rail = document.querySelector('.rail');
  if (rail) rail.style.paddingBottom = railPadWas;
  railPadWas = null;
}

function settleFeeReveal() {
  var played = document.querySelectorAll('.fee-body.unravel, .tbl.impl.fees-in');
  Array.prototype.forEach.call(played, function (node) {
    node.addEventListener('animationend', function handler() {
      node.removeEventListener('animationend', handler);
      node.classList.remove('unravel');
      node.classList.remove('fees-in');
      if (node.classList.contains('fee-body')) {
        releaseRailPad();
        scrollFeeGroupIntoView(true);
      }
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
    + '<label for="implincfees">Include Fees</label></div>'
    + '<p class="chk-note" id="incfeesnote">' + (on
        ? 'Fee columns are shown in the table and written to the workbook.'
        : 'The proposal shows no fees. Tick to price the model.')
    + '</p>'
    /* the card is read only: this opens it, nothing here changes it (D55) */
    + '<button type="button" class="btn btn-ghost fee-view" data-feeview>'
    + 'View fee card</button>'
    + '</div>';
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
    + '<span class="fee-label" id="feeschedlabel">Fee Schedule</span>'
    + '<div class="fee-seg" role="group" aria-labelledby="feeschedlabel">' + seg + '</div>'
    + '<p class="vr-note">' + (entry
        ? App.esc(entry.note || '')
        : 'Choose how the book is priced. Nothing is priced until it is chosen.')
    + '</p></div>';

  /* The level is one value with two halves - a source and a point on that
     source's band - so it is chosen as two segmented controls stacked, the
     same control the schedule uses above. A combination the framework does
     not price is offered disabled rather than composed and refused. */
  var sources = App.opt('fees.sources', []);
  var points = App.opt('fees.points', []);
  var levels = App.opt('fees.levels', []);
  function idFor(source, point) {
    for (var i = 0; i < levels.length; i++) {
      if (levels[i].source === source && levels[i].point === point) return levels[i].id;
    }
    return null;
  }
  var chosenLevel = levels.filter(function (l) { return l.id === level; })[0] || null;
  var curSource = chosenLevel ? chosenLevel.source : (sources[0] || null);
  var curPoint = chosenLevel ? chosenLevel.point : (points[0] || null);

  /* Both controls are sized from the schema's own lists, not from a count
     written here: a framework that priced four points would still lay out. */
  function segment(values, chosen, other, attribute, resolve, label) {
    var buttons = values.map(function (value) {
      var id = resolve(value, other);
      return '<button type="button" ' + attribute + '="' + App.esc(value) + '"'
        + ' aria-pressed="' + (value === chosen ? 'true' : 'false') + '"'
        + ' aria-label="' + App.esc(id || value) + '"'
        + (editable && id ? '' : ' disabled') + '>' + App.esc(value) + '</button>';
    }).join('');
    return '<div class="fee-seg" role="group" aria-label="' + App.esc(label) + '"'
      + ' style="grid-template-columns:repeat(' + values.length + ',1fr)">'
      + buttons + '</div>';
  }

  html += '<div class="fee-field done">'
    + '<span class="fee-label" id="feelevellabel">Fee Level</span>'
    + segment(sources, curSource, curPoint, 'data-feesource',
              function (source, point) { return idFor(source, point); }, 'Fee source')
    + segment(points, curPoint, curSource, 'data-feepoint',
              function (point, source) { return idFor(source, point); }, 'Fee Level point')
    + '<p class="vr-note">' + (tier
        ? 'Tier ' + App.esc(tier.id) + ', ' + App.esc(tier.label) + ', from the top account size.'
        : 'No account-size tier: the mandate has no top account size.')
    + '</p>'
    + (App.opt('fees.placeholder', false)
        ? '<p class="fee-flag">Placeholder rates, not the published schedule.</p>' : '')
    + feeCardLine()
    + '</div></div></div>';
  return html;
}

/* The card's own line under the level: which delivery priced this book. */
function feeCardLine() {
  var delivery = App.opt('fees.delivery', null) || {};
  return '<p class="fee-card">Card ' + App.esc(delivery.version || 'unversioned') + '</p>';
}

/* ---- the rate card viewer (D55) -------------------------------------------
   Every cell at one tier or one fee group, read only. The card is what the
   delivering team sent; it changes by delivery (feeTools --accept), never
   here, so there is nothing to type into and nothing to save. The whole card
   is fetched once and pivoted in the client, so flipping the axis or the
   selection is instant. */
var feePanel = { open: false, card: null, pivot: 'group', tier: null, group: 0,
                 error: null, busy: false };

function feeCellKey(schedule, group, tier, source, point) {
  return [schedule, group || '', tier, source, point].join('|');
}

async function loadFeeCard() {
  feePanel.busy = true; renderFeePanel();
  try {
    var resp = await fetch(window.API_BASE + '/scenario/fees?whole=1',
                           { credentials: 'same-origin' });
    var data = null;
    try { data = await resp.json(); } catch (e) { /* no body */ }
    if (!resp.ok) {
      if (data && data.loginUrl) { window.location = data.loginUrl; return; }
      throw new Error((data && data.error) || ('Could not load the card (' + resp.status + ')'));
    }
    feePanel.card = data;
    feePanel.error = null;
  } catch (err) { feePanel.error = err.message; }
  feePanel.busy = false;
  renderFeePanel();
}

async function openFeePanel(groupName) {
  var tier = App.opt('fees.tier', null);
  feePanel.open = true;
  feePanel.tier = tier ? tier.id : null;    /* the by-tier chooser starts here... */
  feePanel.mandateTier = tier ? tier.id : null;   /* ...and this one never moves */
  feePanel.error = null;
  feePanel.returnTo = document.activeElement;
  renderFeePanel();
  await loadFeeCard();
  /* Opened AT a fee group - from the catalogue's drawer (D58) - the card
     arrives pivoted to that group's ladder, which is the one thing about a
     product the catalogue cannot show. */
  if (groupName && feePanel.card) {
    var at = feePanel.card.groups.findIndex(function (g) { return g.feeGroup === groupName; });
    if (at !== -1) { feePanel.pivot = 'group'; feePanel.group = at; renderFeePanel(); }
  }
  var sel = document.getElementById('feeaxis');
  if (sel) sel.focus();
}

function closeFeePanel() {
  feePanel.open = false; feePanel.card = null; feePanel.error = null;
  renderFeePanel();
  var trigger = feePanel.returnTo && document.contains(feePanel.returnTo)
    ? feePanel.returnTo : document.querySelector('[data-feeview]');
  feePanel.returnTo = null;
  if (trigger && trigger.focus) trigger.focus();
}

function feeGroupLabel(entry) {
  return entry.feeGroup ? entry.schedule + ' · ' + entry.feeGroup
    : entry.schedule + ' · one rate for every product';
}

/* The card pivots on which axis is a whole table and which is one choice.
   By fee group: a group's ladder down the tiers - the shape a rate card is
   published in. By tier: everything that prices at one account size. */
function feePivot() {
  var card = feePanel.card;
  if (feePanel.pivot === 'group') {
    var group = card.groups[Math.min(feePanel.group, card.groups.length - 1)];
    return {
      rowHead: 'Account-size tier',
      rows: card.tiers.map(function (t) {
        return { label: t.id, sub: t.label, mark: t.id === feePanel.mandateTier,
                 key: function (src, pt) {
                   return feeCellKey(group.schedule, group.feeGroup, t.id, src, pt);
                 } };
      })
    };
  }
  var tierId = feePanel.tier || card.tiers[0].id;
  return {
    rowHead: 'Schedule / fee group',
    rows: card.groups.map(function (g) {
      return { label: g.schedule, sub: g.feeGroup || 'one rate for every product',
               mark: !!(App.feeSchedule && App.feeSchedule() === g.schedule),
               key: function (src, pt) {
                 return feeCellKey(g.schedule, g.feeGroup, tierId, src, pt);
               } };
    })
  };
}

function renderFeePanel() {
  var host = document.getElementById('feeDialog'); if (!host) return;
  if (!feePanel.open) {
    host.innerHTML = ''; host.hidden = true; App.setBackgroundInert(false); return;
  }
  host.hidden = false;
  App.setBackgroundInert(true);
  var card = feePanel.card;
  var delivery = (card && card.delivery) || App.opt('fees.delivery', {}) || {};
  var placeholder = App.opt('fees.placeholder', false);

  /* The level this mandate prices at - the scenario's own, or the framework's
     default when none is chosen yet. It is one column of the grid, and the
     one a reader is looking for. */
  var levelId = (App.feeLevel && App.feeLevel()) || null;
  var byGroupPivot = feePanel.pivot === 'group';
  var mandateTier = feePanel.mandateTier;
  var tierLabel = function (id) {
    var t = card && card.tiers.filter(function (x) { return x.id === id; })[0];
    return t ? t.label : id;
  };

  var controls = '', table = '', legend = '';
  if (card) {
    var options = byGroupPivot
      ? card.groups.map(function (g, i) {
          return '<option value="' + i + '"' + (i === feePanel.group ? ' selected' : '') + '>'
            + App.esc(feeGroupLabel(g)) + '</option>';
        }).join('')
      : card.tiers.map(function (t) {
          return '<option value="' + App.esc(t.id) + '"'
            + (t.id === (feePanel.tier || card.tiers[0].id) ? ' selected' : '') + '>'
            + App.esc(t.id + ' · ' + t.label + (t.id === mandateTier ? ' · this mandate' : '')) + '</option>';
        }).join('');
    controls = '<div class="rc-controls">'
      + '<div class="rc-seg" role="tablist" aria-label="Pivot">'
      + '<button type="button" role="tab" data-feepivot="group" aria-selected="' + (byGroupPivot ? 'true' : 'false') + '">By fee group</button>'
      + '<button type="button" role="tab" data-feepivot="tier" aria-selected="' + (byGroupPivot ? 'false' : 'true') + '">By tier</button>'
      + '</div>'
      + '<label class="sr-only" for="feeaxis">' + (byGroupPivot ? 'Schedule and fee group' : 'Account-size tier') + '</label>'
      + '<select id="feeaxis" class="rc-axis">' + options + '</select>'
      + '</div>';

    var pivot = feePivot();
    var isLevel = function (src, pt) { return levelId === src + ' ' + pt; };
    var head1 = '<tr><th rowspan="2" class="rowhead">' + App.esc(pivot.rowHead) + '</th>'
      + card.sources.map(function (src) {
          return '<th colspan="' + card.points.length + '" class="src rc-grp"><span>' + App.esc(src) + '</span></th>';
        }).join('') + '</tr>';
    var head2 = '<tr>' + card.sources.map(function (src) {
        return card.points.map(function (pt, i) {
          var lvl = isLevel(src, pt);
          return '<th class="pt' + (i === 0 ? ' rc-grp' : '') + (lvl ? ' lvl' : '') + '">'
            + App.esc(pt) + (lvl ? '<small>fee level</small>' : '') + '</th>';
        }).join('');
      }).join('') + '</tr>';
    var body = pivot.rows.map(function (row) {
      var head = '<th scope="row" class="rowhead">'
        + '<span class="rc-id">' + App.esc(row.label) + '</span>'
        + '<span class="rc-lbl">' + App.esc(row.sub) + '</span>'
        /* by tier, the marked rows are a whole schedule and the key names it;
           a tag on each of five rows would say the same thing five times */
        + (row.mark && byGroupPivot ? '<span class="rc-tag">this mandate</span>' : '')
        + '</th>';
      var cells = card.sources.map(function (src) {
        return card.points.map(function (pt, i) {
          var rate = card.cells[row.key(src, pt)];
          var lvl = isLevel(src, pt);
          /* the one cell that is this mandate's rate: its tier, its level */
          var ring = byGroupPivot && row.mark && lvl;
          return '<td class="rate' + (i === 0 ? ' rc-grp' : '') + (lvl ? ' lvl' : '') + (ring ? ' ring' : '') + '">'
            + (typeof rate === 'number' ? rate.toFixed(2) : '—') + '</td>';
        }).join('');
      }).join('');
      return '<tr' + (row.mark ? ' class="mark"' : '') + '>' + head + cells + '</tr>';
    }).join('');
    table = '<div class="rc-wrap"><table class="rate-grid"><thead>' + head1 + head2
      + '</thead><tbody>' + body + '</tbody></table></div>';

    var keys = ['<span><i class="k-unit"></i>Annual management fee, percent</span>'];
    if (levelId) keys.push('<span><i class="k-lvl"></i>' + App.esc(levelId) + ' — the level this proposal prices at</span>');
    if (byGroupPivot && mandateTier) {
      keys.push('<span><i class="k-mark"></i>' + App.esc(mandateTier + ' · ' + tierLabel(mandateTier)) + ' — this mandate’s tier</span>');
      if (levelId) keys.push('<span><i class="k-ring"></i>This mandate’s rate on the schedule shown</span>');
    } else if (!byGroupPivot && App.feeSchedule && App.feeSchedule()) {
      keys.push('<span><i class="k-mark"></i>' + App.esc(App.feeSchedule()) + ' — this proposal’s schedule</span>');
    }
    legend = '<p class="rc-legend">' + keys.join('') + '</p>';
  } else if (!feePanel.error) {
    table = '<p class="rc-loading">Loading the card…</p>';
  }

  host.innerHTML =
      '<div class="scrim" data-feescrim></div>'
    + '<div class="dialog wide rc" role="dialog" aria-modal="true" aria-labelledby="feeTitle">'
    + '<button type="button" class="dlg-close" id="feeclose" aria-label="Close">×</button>'
    + '<div class="rc-head">'
    + '<h2 id="feeTitle">Fee card</h2>'
    + '<p class="rc-meta">'
    + '<span>Delivery <b>' + App.esc(delivery.version || 'unversioned') + '</b></span>'
    + (delivery.asOf ? '<span>as of <b>' + App.esc(delivery.asOf) + '</b></span>' : '')
    + (delivery.source ? '<span>from <b>' + App.esc(delivery.source) + '</b></span>' : '')
    + '</p>'
    + (placeholder ? '<span class="rc-flag">Placeholder rates</span>' : '')
    + '</div>'
    + controls
    + table
    + legend
    + (feePanel.error ? '<p class="md-err" role="alert">' + App.esc(feePanel.error) + '</p>' : '')
    + '<div class="rc-actions">'
    + '<span class="rc-note">The card is delivered and read only — it changes by delivery, not here.</span>'
    + '<button type="button" class="btn btn-primary" id="feecancel">Close</button>'
    + '</div></div>';
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
  /* The admin shortcut sits on the heading rather than on every picker row:
     one control for the tier, opening the repository on the implementation
     type already chosen, rather than five that each say the same thing (D62). */
  var head = '<div class="tier-h"><h3>Sleeves</h3><span class="tier-h-r">'
    + (chosenVariant && base && base.status === 'ready'
        ? '<span class="tier-count">' + counts.filled + ' of ' + counts.total + '</span>'
        : '')
    + (App.opt('capabilities.canAdmin', false)
        ? '<button type="button" class="tier-admin" data-openrepo'
          + ' aria-label="Open the sleeve repository" title="Sleeve repository">'
          + '<svg viewBox="0 0 20 20" aria-hidden="true"><use href="#i-sleeves"/></svg></button>'
        : '')
    + '</span></div>' + tacticalTiltField() + volPremiumField();

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
  sleeveCategories(baseCategories()).filter(function (category) {
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
    + '<h3>Composition of the Implemented Model</h3>'
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
  var schedule = App.feeSchedule();
  /* The two fee columns are in the table only while the proposal includes
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
    + '<th scope="col" class="txt">Exp ccy</th>'
    + '<th scope="col" class="num">Prod cost</th>'
    + (fees
        ? '<th scope="col" class="num fee-col"><span class="fcw">Mgmt fee</span></th>'
          + '<th scope="col" class="num fee-col"><span class="fcw">Wtd fee</span></th>'
        : '')
    + '<th scope="col" class="num">Min Investment</th>'
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
      + feeCell('num', '')
      + feeCell('num', group.items.length ? bpText(groupBp) : '')
      + '<td class="num"></td>'
      + '<td class="num">' + money(shownNotional) + '</td></tr>';
    group.items.forEach(function (item, ix) {
      html += '<tr class="asset' + (ix % 2 ? ' alt' : '')
        + (item.belowMinimum ? ' below-min' : '') + '"><th scope="row">'
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
            ? feeCell('num', feeText(item.mgmt)) + feeCell('num', bpText(item.wtdBp))
            : '')
        + '<td class="num">' + (typeof item.minimumInvestment === 'number'
            ? money(item.minimumInvestment) : '<span class="mut">&mdash;</span>') + '</td>'
        + '<td class="num">' + money(item.notional)
        + (item.belowMinimum
            ? ' <span class="bdg b-breach" title="' + App.esc(item.name)
              + ' is below its ' + App.esc(money(item.minimumInvestment))
              + ' minimum">below mandate minimum</span>' : '')
        + '</td></tr>';
    });
  });
  /* The filler spans Ticker through Mgmt fee, so it is one column shorter
     when the fee columns are not there. */
  html += '<tr class="grand"><th scope="row">Total</th>'
    + '<td class="prodcol"></td>'
    + '<td class="num">' + App.num(t.weight, 2, '%') + '</td>'
    + '<td colspan="' + (fees ? 8 : 7) + '"></td>'
    + feeCell('num', bpText(t.bp))
    + '<td class="num"></td>'
    + '<td class="num">' + money(t.notional) + '</td></tr>';
  html += '</tbody></table></div>';
  html += renderDonuts(groups, done);

  var breached = breaches(groups);
  var breachBlocked = false;
  var reason = null;
  if (!App.canExport()) reason = 'Export is not available for your role.';
  else if (!App.variant()) reason = 'Choose an implementation variant first.';
  /* Only a proposal that includes fees needs a schedule: one that does not
     exports a sheet with no fee column to price (D52). */
  else if (fees && !schedule) reason = 'Choose a fee schedule in the rail first.';
  else if (!done) reason = 'Attach a sleeve to every category to enable the download.';
  else if (columnsBusy) reason = 'Wait for every portfolio column to finish resolving.';
  /* A hard block: a position below the product's minimum cannot be bought,
     so the materials cannot be produced. The server refuses it too. */
  else if (breached.length) {
    breachBlocked = true;
    reason = breached.length === 1
      ? breached[0].name + ' in ' + breached[0].category + ' is below mandate minimum ('
        + money(breached[0].notional) + ' against a ' + money(breached[0].minimum)
        + ' minimum). Raise the mandate, change the sleeve, or drop the product.'
      : breached.length + ' positions are below mandate minimum: '
        + breached.slice(0, 3).map(function (b) { return b.name; }).join(', ')
        + (breached.length > 3 ? ' and ' + (breached.length - 3) + ' more' : '')
        + '. Raise the mandate, change the sleeve, or drop the products.';
  }
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
    gateClass += breachBlocked ? ' error' : ' blocked';
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
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && feePanel.open) closeFeePanel();
});
document.addEventListener('change', function (e) {
  if (!e.target.dataset) return;
  if (e.target.id === 'feeaxis') {
    if (feePanel.pivot === 'group') feePanel.group = parseInt(e.target.value, 10) || 0;
    else feePanel.tier = e.target.value;
    renderFeePanel();                    /* the card is already here: no fetch */
    return;
  }
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
  /* the rate card panel (D55) */
  if (e.target.closest && e.target.closest('[data-openrepo]')) {
    var at = e.target.closest('[data-openrepo]');
    if (App.openRepository) App.openRepository(at, 'sleeves', { variant: App.variant() });
    return;
  }
  if (e.target.closest && e.target.closest('[data-feeview]')) { openFeePanel(); return; }
  if (e.target.id === 'feeclose' || e.target.id === 'feecancel'
      || (e.target.dataset && e.target.dataset.feescrim !== undefined)) { closeFeePanel(); return; }
  var pivotTab = e.target.closest && e.target.closest('[data-feepivot]');
  if (pivotTab) {
    if (feePanel.pivot === pivotTab.dataset.feepivot) return;
    feePanel.pivot = pivotTab.dataset.feepivot;
    renderFeePanel();
    var axis = document.getElementById('feeaxis'); if (axis) axis.focus();
    return;
  }
  if (e.target.id === 'implexport') { exportWorkbook(); return; }
  var sched = e.target.closest ? e.target.closest('[data-feesched]') : null;
  if (sched) { App.setFeeSchedule(sched.dataset.feesched); return; }
  /* Either half of the level composes the whole: the other half is read from
     the level in force, so pressing Ceiling keeps the source it was on. */
  var half = e.target.closest
    ? e.target.closest('[data-feesource],[data-feepoint]') : null;
  if (half) {
    var levels = App.opt('fees.levels', []);
    var inForce = levels.filter(function (l) { return l.id === App.feeLevel(); })[0];
    var source = half.dataset.feesource
      || (inForce && inForce.source) || App.opt('fees.sources', [])[0];
    var point = half.dataset.feepoint
      || (inForce && inForce.point) || App.opt('fees.points', [])[0];
    var chosen = levels.filter(function (l) {
      return l.source === source && l.point === point;
    })[0];
    if (chosen) App.setFeeLevel(chosen.id);
    return;
  }
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
App.openFeePanel = openFeePanel;      /* the catalogue's drawer opens it at a group (D58) */
App.managementFee = managementFee;    /* the catalogue's Mgmt column prices from the same mirror (D63) */

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

/* ---- the sleeve repository console (D57) and the product catalogue (D58) --
   Where an admin builds and maintains the sleeve library, and reads the
   catalogue it is built from: one overlay on the same footing as the fee card
   viewer - the page behind it goes inert, Escape closes it, focus returns to
   whatever opened it - with two views behind a switch in its header.

   SLEEVES: three panes - the categories under one implementation type, the
   sleeves in one category, and the editor for one sleeve. Products are picked
   from the delivered catalogue (D56), never typed.

   CATALOGUE: the whole delivered table, view only - searched, filtered by
   vocabularies derived from the data, sorted by any column, with a drawer for
   one product that says where it is used and links to the sleeve that uses
   it. Nothing on this view writes.

   Everything both views show comes from one fetch of /scenario/repository;
   every save goes through the repository's own validation and the messages
   shown by a field are the server's. The client judges a draft as it is
   edited too - the total, an empty row - so Save is disabled before the
   server would refuse, but the server is the authority.

   Rendering: the whole dialog is one innerHTML, re-rendered on structural
   changes (a sleeve chosen, a row added, a view switched). Typing does not
   re-render - the draft or the query is updated in place and only the total,
   the Save state, the picker's results or the catalogue's rows are redrawn -
   so the caret never moves. */
(function () {
'use strict';

var repo = {
  open: false, view: 'sleeves', busy: false, saving: false, error: null, data: null,
  variant: null, category: null, sleeveId: null,
  draft: null, dirty: false, fieldError: null,
  picker: null,                /* { row, query, index } while a row's picker is open */
  confirmDelete: false,
  leaving: null,               /* { to, variant, category, fresh } | { close } while unsaved changes block a move */
  menu: null,                  /* { id, x, y } while a sleeve's context menu is open */
  trigger: null,
  bootChecked: false,
  /* the record (D65): the open sleeve's revisions, fetched on demand because
     most visits never ask for them */
  history: null,               /* { sleeveId, entries } */
  historyOpen: false,
  historyBusy: false,
  openRevision: null           /* the revision whose composition is expanded */
};

/* The catalogue view's own state (D63). It survives a switch to the other
   view, and every part of it encodes into the URL, so a view is a link. */
var cat = {
  query: '', filters: {},            /* facet field -> [values] */
  sort: { key: 'allIn', dir: 'asc' }, /* null = the delivery's own order */
  hidden: [],                        /* column keys taken away by the picker */
  density: 'dense',                  /* 'dense' | 'comfortable' */
  pins: [],                          /* productIds in the tray */
  cursor: null,                      /* the row the keyboard is on */
  detail: null,                      /* the product open in the side panel */
  compare: false,                    /* the pinned products, lined up */
  openChip: null                     /* 'cols' while the column picker is open */
};

/* The archive (D66, option A): the sleeves taken out of the library, as a
   searchable table. Its own state, URL-encoded like the catalogue's. */
var arc = {
  query: '', filters: {},                   /* variant | category | archivedBy -> value */
  sort: { key: 'archivedAt', dir: 'desc' },
  selected: [],                             /* sleeve ids ticked for a batch restore */
  detail: null                              /* the archived sleeve open below the table */
};
var ARC_COLUMNS = [
  { key: 'name', label: 'Sleeve' },
  { key: 'category', label: 'Category' },
  { key: 'variant', label: 'Book' },
  { key: 'held', label: 'Held', num: true },
  { key: 'archivedAt', label: 'Archived' },
  { key: 'revisions', label: 'Versions', num: true }
];

/* The activity feed (D66, option C): the record as a story. The page holds
   only what the server handed back; every filter change is a fresh read. */
var act = {
  query: '', actions: [], actor: '', variant: '', range: '30d',
  entries: [], next: null, total: 0, facets: null, earliest: '',
  busy: false, loaded: false, error: null
};
var ACT_ACTIONS = ['created', 'updated', 'reverted', 'deleted', 'restored', 'imported'];

/* The proposal register (D69): every delivered proposal, for ever. The page
   holds what the server handed back; a filter change is a fresh read. The
   open record is fetched on its own, and the workbook only on click. */
var reg = {
  query: '', exportedBy: '', primaryPwa: '', currency: '', variant: '', range: '90d',
  entries: [], next: null, total: 0, facets: null,
  busy: false, loaded: false, error: null,
  detail: null,                /* proposalId open below the table */
  record: null,                /* that proposal, fetched */
  recordBusy: false,
  picture: 'implemented'       /* 'allocation' | 'implemented' */
};
var REG_RANGES = [['30d', 'Last 30 days'], ['90d', 'Last 90 days'], ['365d', 'Last year'], ['all', 'All time']];
var regTimer = null;
var ACT_RANGES = [['7d', 'Last 7 days'], ['30d', 'Last 30 days'], ['90d', 'Last 90 days'], ['all', 'All time']];
var actTimer = null;

/* The facet rail, in order. A facet is a field of the product, or one of the
   two joins the repository already gives: which categories' sleeves hold it,
   and which books offer it. Both of those are many-valued. */
var CAT_FACETS = [
  { key: 'category', label: 'Category' },
  { key: 'vehicle', label: 'Vehicle' },
  { key: 'liquidity', label: 'Liquidity', order: ['Daily', 'Weekly', 'Monthly', 'Quarterly', 'Drawdown'] },
  { key: 'style', label: 'Style' },
  { key: 'exposureCurrency', label: 'Exposure' },
  { key: 'feeGroup', label: 'Fee group' },
  { key: 'book', label: 'Book' }
];
/* Every column, in order. The product column cannot be taken away; the
   rest can, and the picker remembers. num: right-aligned condensed figures.
   derived: net yield, set apart in its head because it is computed here. */
var CAT_COLUMNS = [
  { key: 'ticker', label: 'Ticker' },
  { key: 'name', label: 'Product', fixed: true },
  { key: 'assetClass', label: 'Class' },
  { key: 'vehicle', label: 'Veh' },
  { key: 'style', label: 'Style' },
  { key: 'source', label: 'Src' },
  { key: 'exposureCurrency', label: 'Ccy' },
  { key: 'liquidity', label: 'Liq' },
  { key: 'productCost', label: 'Cost', num: true },
  { key: 'mgmt', label: 'Mgmt', num: true, tier: true },
  { key: 'allIn', label: 'All-in', num: true },
  { key: 'distributionYield', label: 'Yield', num: true },
  { key: 'net', label: 'Net', num: true, derived: true },
  { key: 'minimumInvestment', label: 'Min', num: true },
  { key: 'used', label: 'Used', num: true }
];
var CAT_NUMERIC = { productCost: 1, mgmt: 1, allIn: 1, distributionYield: 1, net: 1, minimumInvestment: 1, used: 1 };
var UNPLACED = 'Not yet placed';

function canAdmin() { return !!App.opt('capabilities.canAdmin', false); }

/* ---- data helpers ------------------------------------------------------- */
function sleevesIn(variant, category) {
  return (repo.data && repo.data.sleeves || []).filter(function (s) {
    return s.variant === variant && s.category === category;
  });
}
function archivedSleeves() { return (repo.data && repo.data.archived) || []; }
function archivedById(id) {
  return archivedSleeves().filter(function (s) { return s.id === id; })[0] || null;
}
function anySleeveById(id) { return sleeveById(id) || archivedById(id); }
function sleeveById(id) {
  return (repo.data && repo.data.sleeves || []).filter(function (s) { return s.id === id; })[0] || null;
}
function isFixed(category) {
  return !!repo.data && repo.data.fixedCategories.indexOf(category) !== -1;
}
var productIndex = null;
function productById(id) {
  if (!productIndex) {
    productIndex = {};
    (repo.data.products || []).forEach(function (p) { productIndex[p.productId] = p; });
  }
  return productIndex[id] || null;
}
function productMeta(p) {
  return [p.ticker, p.assetClass, p.vehicle, p.source].filter(Boolean).join(' · ');
}
function money2(n) { return (Math.round(n * 100) / 100).toFixed(2); }
function shortDate(iso) {
  if (!iso) return '';
  var d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}
function esc(s) { return App.esc(s == null ? '' : String(s)); }

/* catalogue-helpers-begin
   Pure functions over the fetched data, DOM-free so the suite can run them
   through node against fixture data the way it runs the rounding and tilt
   mirrors. A "row" here is a product enriched with its joins and its
   figures: { p, used, categories, books, mgmt, allIn, net }. */
function catJoin(sleeves) {
  /* productId -> { used, categories, books } from the sleeves that hold it */
  var map = {};
  (sleeves || []).forEach(function (s) {
    (s.products || []).forEach(function (r) {
      var j = map[r.productId] = map[r.productId] || { used: 0, categories: [], books: [] };
      j.used += 1;
      if (j.categories.indexOf(s.category) === -1) j.categories.push(s.category);
      if (j.books.indexOf(s.variant) === -1) j.books.push(s.variant);
    });
  });
  return map;
}
function catEnrich(products, sleeves, mgmtOf, mandateSize) {
  var join = catJoin(sleeves);
  return (products || []).map(function (p) {
    var j = join[p.productId] || { used: 0, categories: [], books: [] };
    var mgmt = mgmtOf ? mgmtOf(p.feeGroup) : null;
    if (typeof mgmt !== 'number' || !isFinite(mgmt)) mgmt = null;
    var allIn = p.productCost + (mgmt === null ? 0 : mgmt);
    var y = p.distributionYield;
    var min = p.minimumInvestment;
    return {
      p: p, used: j.used, categories: j.categories, books: j.books,
      mgmt: mgmt, allIn: allIn,
      net: (typeof y === 'number') ? y - allIn : null,
      tooBig: typeof min === 'number' && typeof mandateSize === 'number' && mandateSize > 0 && min > mandateSize
    };
  });
}
function catValuesOf(row, field) {
  if (field === 'category') return row.categories.length ? row.categories : ['Not yet placed'];
  if (field === 'book') return row.books.length ? row.books : ['Not yet placed'];
  return [row.p[field] || ''];
}
function catMatches(p, q) {
  if (!q) return true;
  return [p.name, p.ticker, p.assetClass, p.vehicle, p.source, p.feeGroup]
    .join(' ').toLowerCase().indexOf(q) !== -1;
}
function catPasses(row, state, except) {
  var q = (state.query || '').trim().toLowerCase();
  if (!catMatches(row.p, q)) return false;
  for (var f in state.filters) {
    if (f === except) continue;
    var chosen = state.filters[f];
    if (!chosen || !chosen.length) continue;
    var values = catValuesOf(row, f), hit = false;
    for (var i = 0; i < values.length; i += 1) if (chosen.indexOf(values[i]) !== -1) { hit = true; break; }
    if (!hit) return false;
  }
  return true;
}
function catFilter(rows, state) {
  return (rows || []).filter(function (row) { return catPasses(row, state, null); });
}
function catFacets(rows, facets, state, orders) {
  /* counts for every value of every facet, each taken over the rows that
     pass every OTHER filter - so a value's count is what choosing it would
     leave, and a zero is a value that cannot be chosen from here */
  var out = {};
  facets.forEach(function (f) {
    var counts = {};
    (rows || []).forEach(function (row) {
      if (!catPasses(row, state, f.key)) return;
      catValuesOf(row, f.key).forEach(function (v) { counts[v] = (counts[v] || 0) + 1; });
    });
    /* every value that exists at all, so an excluded one still shows, at 0 */
    (rows || []).forEach(function (row) {
      catValuesOf(row, f.key).forEach(function (v) { if (!(v in counts)) counts[v] = 0; });
    });
    var order = (orders && orders[f.key]) || f.order || null;
    var values = Object.keys(counts).sort(function (a, b) {
      if (order) {
        var ia = order.indexOf(a), ib = order.indexOf(b);
        if (ia !== -1 || ib !== -1) return (ia === -1 ? 1e9 : ia) - (ib === -1 ? 1e9 : ib);
      }
      return a < b ? -1 : a > b ? 1 : 0;
    });
    out[f.key] = values.map(function (v) { return { value: v, count: counts[v] }; });
  });
  return out;
}
function catSortValue(row, key) {
  if (key === 'used' || key === 'mgmt' || key === 'allIn' || key === 'net') return row[key];
  if (key === 'productCost' || key === 'distributionYield' || key === 'minimumInvestment') return row.p[key];
  return String(row.p[key] || '').toLowerCase();
}
function catSort(rows, sort) {
  if (!sort) return rows.slice();                      /* the delivery's own order */
  var key = sort.key, dir = sort.dir === 'desc' ? -1 : 1;
  return rows.slice().sort(function (a, b) {
    var x = catSortValue(a, key), y = catSortValue(b, key);
    var xn = (x === null || x === undefined), yn = (y === null || y === undefined);
    if (xn && yn) return 0;
    if (xn) return 1;                                    /* a blank sorts last either way */
    if (yn) return -1;
    if (x < y) return -dir;
    if (x > y) return dir;
    return a.p.name < b.p.name ? -1 : a.p.name > b.p.name ? 1 : 0;
  });
}
function catBest(pinned) {
  /* per figure, which pinned product is best: lowest cost, highest yield */
  var rules = { productCost: -1, mgmt: -1, allIn: -1, distributionYield: 1, net: 1 };
  var best = {};
  Object.keys(rules).forEach(function (key) {
    var winner = null, value = null;
    pinned.forEach(function (row) {
      var v = catSortValue(row, key);
      if (v === null || v === undefined) return;
      if (value === null || (rules[key] > 0 ? v > value : v < value)) { value = v; winner = row.p.productId; }
    });
    if (winner !== null) best[key] = winner;
  });
  return best;
}
/* catalogue-helpers-end */

var catRowCache = null;
function catRows() {
  if (!catRowCache) {
    var priced = App.feeSchedule && App.feeSchedule() && App.managementFee;
    var mandate = (typeof App.mandateSize === 'function') ? App.mandateSize() : null;
    catRowCache = catEnrich(repo.data.products, repo.data.sleeves,
                            priced ? App.managementFee : null,
                            typeof mandate === 'number' ? mandate : null);
  }
  return catRowCache;
}
function catRow(productId) {
  return catRows().filter(function (r) { return r.p.productId === productId; })[0] || null;
}
function usedIn(productId) {
  /* the sleeves holding a product, for the side panel */
  var out = [];
  (repo.data.sleeves || []).forEach(function (s) {
    (s.products || []).forEach(function (r) {
      if (r.productId === productId) out.push({ id: s.id, variant: s.variant, category: s.category, name: s.name, weight: r.weight });
    });
  });
  return out;
}
function forgetJoins() { productIndex = null; catRowCache = null; }

/* ---- the draft ---------------------------------------------------------- */
function draftFrom(entry) {
  return {
    id: entry.id, name: entry.name, note: entry.note || '',
    products: entry.products.map(function (row) {
      return { productId: row.productId, weightPct: Math.round(row.weight * 10000) / 100 };
    })
  };
}
function newDraft(create) {
  /* A creation draft carries the two things an existing sleeve already knows:
     which category it implements, and which books offer it. Both are fixed
     once it exists - a sleeve moved between them is a different sleeve - so
     they are only editable here (D61). */
  var draft = { id: null, name: '', note: '', products: [] };
  if (create) {
    draft.create = true;
    draft.category = repo.category;
    draft.variants = [repo.variant];
  }
  return draft;
}
function total() {
  return repo.draft.products.reduce(function (sum, r) {
    return sum + (isFinite(r.weightPct) ? r.weightPct : 0);
  }, 0);
}
/* The client's reading of the server's rules, so Save waits until the draft
   could be accepted. The server still decides. */
function draftProblems() {
  var d = repo.draft, out = [];
  if (!d.name.trim()) out.push('Give the sleeve a name.');
  if (d.create && !(d.variants || []).length) out.push('Choose at least one implementation type.');
  if (d.create && !d.category) out.push('Choose a category.');
  if (!d.products.length) out.push('Add at least one product.');
  if (d.products.some(function (r) { return !r.productId; })) out.push('Every row needs a product.');
  if (d.products.some(function (r) { return !(r.weightPct > 0); })) out.push('Every product needs a weight above zero.');
  if (d.products.length && Math.abs(total() - 100) > 0.005) {
    out.push('Weights sum to ' + money2(total()) + '%; a sleeve must sum to 100%.');
  }
  return out;
}

/* ---- opening, loading, closing ----------------------------------------- */
function catHash() {
  var q = new URLSearchParams();
  if (cat.query.trim()) q.set('q', cat.query.trim());
  Object.keys(cat.filters).forEach(function (f) {
    if (cat.filters[f] && cat.filters[f].length) q.set('f.' + f, cat.filters[f].join('|'));
  });
  if (!cat.sort) q.set('sort', 'none');
  else if (!(cat.sort.key === 'allIn' && cat.sort.dir === 'asc')) q.set('sort', cat.sort.key + ':' + cat.sort.dir);
  if (cat.hidden.length) q.set('hide', cat.hidden.join(','));
  if (cat.density !== 'dense') q.set('d', cat.density);
  if (cat.pins.length) q.set('pins', cat.pins.join(','));
  if (cat.compare) q.set('cmp', '1');
  var str = q.toString();
  return '#catalogue' + (str ? '?' + str : '');
}
function catFromHash(hash) {
  /* the reverse: a shared or bookmarked link restores the view it named */
  var at = hash.indexOf('?'); if (at === -1) return;
  var q = new URLSearchParams(hash.slice(at + 1));
  cat.query = q.get('q') || '';
  cat.filters = {};
  q.forEach(function (v, k) { if (k.indexOf('f.') === 0 && v) cat.filters[k.slice(2)] = v.split('|'); });
  var sort = q.get('sort');
  if (sort === 'none') cat.sort = null;
  else if (sort && sort.indexOf(':') !== -1) cat.sort = { key: sort.split(':')[0], dir: sort.split(':')[1] === 'desc' ? 'desc' : 'asc' };
  cat.hidden = (q.get('hide') || '').split(',').filter(Boolean);
  cat.density = q.get('d') === 'comfortable' ? 'comfortable' : 'dense';
  cat.pins = (q.get('pins') || '').split(',').filter(Boolean);
  cat.compare = q.get('cmp') === '1';
}
function arcHash() {
  var q = new URLSearchParams();
  if (arc.query.trim()) q.set('q', arc.query.trim());
  Object.keys(arc.filters).forEach(function (f) { if (arc.filters[f]) q.set('f.' + f, arc.filters[f]); });
  if (!(arc.sort.key === 'archivedAt' && arc.sort.dir === 'desc')) q.set('sort', arc.sort.key + ':' + arc.sort.dir);
  if (arc.detail != null) q.set('open', String(arc.detail));
  var str = q.toString();
  return '#archive' + (str ? '?' + str : '');
}
function arcFromHash(hash) {
  var at = hash.indexOf('?'); if (at === -1) return;
  var q = new URLSearchParams(hash.slice(at + 1));
  arc.query = q.get('q') || '';
  arc.filters = {};
  q.forEach(function (v, k) { if (k.indexOf('f.') === 0 && v) arc.filters[k.slice(2)] = v; });
  var sort = q.get('sort');
  if (sort && sort.indexOf(':') !== -1) arc.sort = { key: sort.split(':')[0], dir: sort.split(':')[1] === 'desc' ? 'desc' : 'asc' };
  var open = parseInt(q.get('open') || '', 10);
  arc.detail = isFinite(open) ? open : null;
}
function actHash() {
  var q = new URLSearchParams();
  if (act.query.trim()) q.set('q', act.query.trim());
  if (act.actions.length) q.set('a', act.actions.join(','));
  if (act.actor) q.set('who', act.actor);
  if (act.variant) q.set('book', act.variant);
  if (act.range !== '30d') q.set('range', act.range);
  var str = q.toString();
  return '#activity' + (str ? '?' + str : '');
}
function actFromHash(hash) {
  var at = hash.indexOf('?'); if (at === -1) return;
  var q = new URLSearchParams(hash.slice(at + 1));
  act.query = q.get('q') || '';
  act.actions = (q.get('a') || '').split(',').filter(function (a) { return ACT_ACTIONS.indexOf(a) !== -1; });
  act.actor = q.get('who') || '';
  act.variant = q.get('book') || '';
  var range = q.get('range');
  act.range = ACT_RANGES.some(function (r) { return r[0] === range; }) ? range : '30d';
}
function regHash() {
  var q = new URLSearchParams();
  if (reg.query.trim()) q.set('q', reg.query.trim());
  if (reg.exportedBy) q.set('who', reg.exportedBy);
  if (reg.primaryPwa) q.set('pwa', reg.primaryPwa);
  if (reg.currency) q.set('ccy', reg.currency);
  if (reg.variant) q.set('book', reg.variant);
  if (reg.range !== '90d') q.set('range', reg.range);
  if (reg.detail) { q.set('open', reg.detail); if (reg.picture !== 'implemented') q.set('pic', reg.picture); }
  var str = q.toString();
  return '#proposals' + (str ? '?' + str : '');
}
function regFromHash(hash) {
  var at = hash.indexOf('?'); if (at === -1) return;
  var q = new URLSearchParams(hash.slice(at + 1));
  reg.query = q.get('q') || '';
  reg.exportedBy = q.get('who') || '';
  reg.primaryPwa = q.get('pwa') || '';
  reg.currency = q.get('ccy') || '';
  reg.variant = q.get('book') || '';
  var range = q.get('range');
  reg.range = REG_RANGES.some(function (r) { return r[0] === range; }) ? range : '90d';
  reg.detail = q.get('open') || null;
  reg.picture = q.get('pic') === 'allocation' ? 'allocation' : 'implemented';
}
function hashFor(view) {
  if (view === 'catalogue') return catHash();
  if (view === 'archive') return arcHash();
  if (view === 'activity') return actHash();
  if (view === 'proposals') return regHash();
  return '#repository';
}
function syncHash() {
  if (!repo.open) return;
  try { window.history.replaceState(null, '', hashFor(repo.view)); } catch (e) { /* file: */ }
}

function openRepository(trigger, view, at) {
  if (!canAdmin()) return;
  repo.open = true; repo.error = null; repo.trigger = trigger || document.activeElement;
  repo.view = ['catalogue', 'archive', 'activity', 'proposals'].indexOf(view) !== -1 ? view : 'sleeves';
  /* where to land, when the caller knows: the sleeve tier's shortcut opens on
     the implementation type the proposal is already using (D62) */
  repo.pending = at || null;
  render();
  loadRepository();
}

function switchView(view) {
  if (view === repo.view) return;
  if (repo.dirty && repo.view === 'sleeves') { repo.leaving = { view: view }; render(); return; }
  repo.view = view; cat.openChip = null; cat.detail = null;
  repo.historyOpen = false; repo.history = null; repo.openRevision = null;
  render();
  if (view === 'activity' && !act.loaded) loadActivity(true);
  if (view === 'proposals') {
    if (!reg.loaded) loadRegister(true);
    if (reg.detail && !(reg.record && reg.record.proposalId === reg.detail)) loadProposal(reg.detail);
  }
}

async function api(method, path, body) {
  var opts = { method: method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  var resp = await fetch(window.API_BASE + path, opts);
  var data = null;
  try { data = await resp.json(); } catch (e) { /* no body */ }
  if (resp.status === 401 && data && data.loginUrl) { window.location = data.loginUrl; return null; }
  return { ok: resp.ok, status: resp.status, body: data || {} };
}

async function loadRepository() {
  repo.busy = true; render();
  try {
    var r = await api('GET', '/scenario/repository');
    if (!r) return;
    if (!r.ok) throw new Error(r.body.error || ('Could not load the repository (' + r.status + ')'));
    repo.data = r.body; forgetJoins();
    chooseDefaults();
    repo.error = null;
    if (repo.view === 'activity' && !act.loaded) loadActivity(true);
    if (repo.view === 'proposals') {
      if (!reg.loaded) loadRegister(true);
      if (reg.detail) loadProposal(reg.detail);
    }
  } catch (err) { repo.error = err.message; }
  repo.busy = false;
  render();
  var first = document.querySelector('#repoDialog [data-repoview][aria-selected="true"]')
           || document.getElementById('repoclose');
  if (first) first.focus();
}

function chooseDefaults() {
  var d = repo.data;
  var want = repo.pending || {};
  repo.pending = null;
  var current = want.variant || (App.variant && App.variant());
  if (want.variant && d.variants.indexOf(want.variant) !== -1) {
    repo.variant = want.variant;
  } else if (d.variants.indexOf(repo.variant) === -1) {
    repo.variant = d.variants.indexOf(current) !== -1 ? current : d.variants[0];
  }
  if (want.category && d.categories.indexOf(want.category) !== -1) repo.category = want.category;
  if (d.categories.indexOf(repo.category) === -1) repo.category = d.categories[0];
  var offered = sleevesIn(repo.variant, repo.category);
  var keep = repo.sleeveId && offered.some(function (s) { return s.id === repo.sleeveId; });
  loadDraft(keep ? repo.sleeveId : (offered[0] ? offered[0].id : null));
}

function loadDraft(sleeveId, create) {
  var entry = sleeveId ? sleeveById(sleeveId) : null;
  repo.sleeveId = entry ? entry.id : null;
  repo.draft = entry ? draftFrom(entry) : newDraft(create);
  repo.dirty = false; repo.fieldError = null; repo.picker = null;
  repo.confirmDelete = false; repo.leaving = null; repo.menu = null;
}

function closeRepository(force) {
  if (repo.dirty && !force) { repo.leaving = { close: true }; repo.view = 'sleeves'; render(); return; }
  repo.open = false; repo.data = null; repo.draft = null; repo.dirty = false;
  repo.picker = null; repo.leaving = null; repo.confirmDelete = false; forgetJoins();
  cat.openChip = null; cat.detail = null; cat.compare = false;
  if (/^#(repository|catalogue|archive|activity|proposals)/.test(window.location.hash)) {
    try { window.history.replaceState(null, '', window.location.pathname + window.location.search); } catch (e) { /* file: */ }
  }
  render();
  if (repo.trigger && repo.trigger.focus) repo.trigger.focus();
  repo.trigger = null;
}

/* ---- moving between sleeves with unsaved changes ------------------------ */
function goTo(target) {
  /* target: { to, variant, category, fresh } - the destination, applied at
     once when there is nothing unsaved, otherwise held until the admin says */
  if (repo.dirty) { repo.leaving = target; repo.view = 'sleeves'; render(); return; }
  applyTarget(target);
}
function applyTarget(t) {
  if (t.view) {
    /* a move to another view, held back by an unsaved draft and now released */
    repo.dirty = false;
    if (t.detail != null) arc.detail = t.detail;
    switchView(t.view);
    if (t.then) t.then();
    return;
  }
  if (t.variant) repo.variant = t.variant;
  if (t.category) repo.category = t.category;
  repo.view = 'sleeves';
  /* the history belongs to the sleeve that was open, not to the next one */
  if (t.to == null || (repo.history && repo.history.sleeveId !== t.to)) {
    repo.historyOpen = false; repo.history = null; repo.openRevision = null;
  }
  try { window.history.replaceState(null, '', hashFor('sleeves')); } catch (e) { /* file: */ }
  if (t.fresh) { loadDraft(null, t.create); }
  else if (t.to != null) { loadDraft(t.to); }
  else { repo.sleeveId = null; repo.dirty = false; chooseDefaults(); }
  render();
  if (t.fresh) { var name = document.getElementById('repoName'); if (name) name.focus(); }
  if (t.then) t.then();
}
function selectSleeve(id) {
  if (id === repo.sleeveId && repo.draft && repo.draft.id === id) return;
  goTo({ to: id });
}

/* From the feed to the thing it names: a live sleeve opens in the editor with
   that revision unfolded; an archived one opens in the Archive the same way.
   Either way the history drawer is open on arrival, because arriving from a
   revision and then hunting for it would be absurd. */
function openRevisionFrom(sleeveId, revisionNumber, archived) {
  var land = function () {
    repo.historyOpen = true; repo.openRevision = revisionNumber;
    if (!(repo.history && repo.history.sleeveId === sleeveId)) loadHistory(sleeveId);
    else render();
  };
  if (archived) {
    if (repo.dirty) { repo.leaving = { view: 'archive', detail: sleeveId, then: land }; render(); return; }
    arc.detail = sleeveId; switchView('archive'); land(); return;
  }
  var entry = sleeveById(sleeveId); if (!entry) return;
  goTo({ variant: entry.variant, category: entry.category, to: sleeveId, then: land });
}

function resolveLeaving(discard) {
  var leaving = repo.leaving; repo.leaving = null;
  if (!discard || !leaving) { render(); return; }
  if (leaving.close) { closeRepository(true); return; }
  repo.dirty = false;
  applyTarget(leaving);
}

/* ---- saving and deleting ----------------------------------------------- */
async function saveDraft() {
  if (draftProblems().length || repo.saving) return;
  var d = repo.draft;
  var payload = {
    name: d.name.trim(), note: d.note.trim(),
    products: d.products.map(function (r) {
      return { productId: r.productId, weight: Math.round(r.weightPct * 10000) / 1000000 };
    })
  };
  repo.saving = true; repo.fieldError = null; repo.error = null; render();
  try {
    var r;
    if (d.id) {
      r = await api('PUT', '/scenario/repository/sleeves/' + d.id, payload);
    } else {
      payload.category = d.create ? d.category : repo.category;
      payload.variants = d.create ? d.variants : [repo.variant];
      r = await api('POST', '/scenario/repository/sleeves', payload);
    }
    if (!r) return;
    if (!r.ok) {
      if (r.body.field) repo.fieldError = { field: r.body.field, message: r.body.error };
      else repo.error = r.body.error || ('Could not save (' + r.status + ')');
    } else {
      var madeAll = r.body.sleeves || [r.body.sleeve];
      var list = repo.data.sleeves;
      madeAll.forEach(function (saved) {
        var at = list.findIndex(function (s) { return s.id === saved.id; });
        if (at === -1) list.push(saved); else list[at] = saved;
      });
      var last = madeAll[madeAll.length - 1];
      list.forEach(function (s) {           /* offeredUnder is shared by name; refresh it */
        if (s.category === last.category && s.name === last.name) s.offeredUnder = last.offeredUnder;
      });
      catRowCache = null;                   /* the catalogue's joins read the same list */
      if (repo.data.store) {
        /* every sleeve written appended one revision (D65) */
        repo.data.store.revisions = (repo.data.store.revisions || 0) + madeAll.length;
        if (!d.id) repo.data.store.sleeves = (repo.data.store.sleeves || 0) + madeAll.length;
      }
      /* land on the one in the book being looked at, if it is among them */
      var here = madeAll.filter(function (m) { return m.variant === repo.variant; })[0] || madeAll[0];
      repo.category = here.category; repo.variant = here.variant;
      var trailOpen = repo.historyOpen && repo.history && repo.history.sleeveId === here.id;
      loadDraft(here.id);
      forgetLibrary(last.category);
      /* a trail left open across a save would be one revision behind, which is
         the one revision the person looking at it just made */
      if (trailOpen) { repo.historyOpen = true; repo.openRevision = null; loadHistory(here.id); }
      if (act.loaded) act.loaded = false;
      App.announce('polite', madeAll.length > 1
        ? 'Saved ' + last.name + ' under ' + madeAll.length + ' implementation types.'
        : 'Saved ' + last.name + '.');
    }
  } catch (err) { repo.error = err.message; }
  repo.saving = false; render();
}

async function deleteCurrent() {
  var d = repo.draft; if (!d || !d.id) return;
  if (!repo.confirmDelete) { repo.confirmDelete = true; render(); return; }
  repo.saving = true; render();
  try {
    var r = await api('DELETE', '/scenario/repository/sleeves/' + d.id);
    if (!r) return;
    if (!r.ok) {
      repo.error = r.body.error || ('Could not delete (' + r.status + ')');
    } else {
      var gone = r.body.deleted;
      var kept = repo.data.sleeves.filter(function (s) { return s.id === gone.id; })[0];
      repo.data.sleeves = repo.data.sleeves.filter(function (s) { return s.id !== gone.id; });
      if (kept) {
        /* out of the library, into the archive - the console shows what the
           store did rather than pretending the sleeve stopped existing */
        kept.archived = true;
        kept.archivedAt = gone.archivedAt; kept.archivedBy = gone.archivedBy;
        kept.revisions = (kept.revisions || 0) + 1;
        repo.data.archived = [kept].concat(archivedSleeves());
      }
      if (repo.data.store) {
        repo.data.store.sleeves = Math.max(0, (repo.data.store.sleeves || 1) - 1);
        repo.data.store.archived = (repo.data.store.archived || 0) + 1;
        repo.data.store.revisions = (repo.data.store.revisions || 0) + 1;
      }
      catRowCache = null;
      repo.historyOpen = false; repo.history = null;
      if (act.loaded) act.loaded = false;        /* the feed is a page behind now */
      var left = sleevesIn(repo.variant, repo.category);
      loadDraft(left[0] ? left[0].id : null);
      forgetLibrary(gone.category);
      App.announce('polite', gone.name + ' archived. It keeps its history and can be restored from the Archive.');
    }
  } catch (err) { repo.error = err.message; }
  repo.saving = false; repo.confirmDelete = false; render();
}

/* Take a sleeve as defined in one book and offer it in another: the same
   name, the same products, created there. It is the create call with the
   source's own contents, so it meets exactly the rules a hand-built sleeve
   meets - the name must be free in the target, and a fixed category that
   already holds its one sleeve refuses (D61). */
async function copyToVariant(sleeveId, variant) {
  var entry = sleeveById(sleeveId); if (!entry) return;
  repo.menu = null; repo.saving = true; repo.error = null; render();
  try {
    var r = await api('POST', '/scenario/repository/sleeves', {
      category: entry.category, name: entry.name, note: entry.note,
      variants: [variant],
      products: entry.products.map(function (p) {
        return { productId: p.productId, weight: p.weight };
      })
    });
    if (!r) return;
    if (!r.ok) {
      repo.error = r.body.error || ('Could not add it to ' + variant + ' (' + r.status + ')');
    } else {
      var made = (r.body.sleeves || [r.body.sleeve])[0];
      repo.data.sleeves.push(made);
      repo.data.sleeves.forEach(function (x) {
        if (x.category === made.category && x.name === made.name) x.offeredUnder = made.offeredUnder;
      });
      catRowCache = null;
      forgetLibrary(made.category);
      App.announce('polite', made.name + ' added to ' + variant + '.');
    }
  } catch (err) { repo.error = err.message; }
  repo.saving = false; render();
}

/* Remove a sleeve from the category it implements, in this book only. The
   other books that offer the same name keep it - which is why the menu says
   the category and the book rather than just "delete". */
async function removeFromCategory(sleeveId) {
  repo.menu = null;
  var entry = sleeveById(sleeveId); if (!entry) return;
  if (entry.fixed) {
    repo.error = entry.category + ' always holds one sleeve - it cannot be removed.';
    render(); return;
  }
  repo.saving = true; repo.error = null; render();
  try {
    var r = await api('DELETE', '/scenario/repository/sleeves/' + sleeveId);
    if (!r) return;
    if (!r.ok) {
      repo.error = r.body.error || ('Could not remove it (' + r.status + ')');
    } else {
      var gone = r.body.deleted;
      repo.data.sleeves = repo.data.sleeves.filter(function (x) { return x.id !== gone.id; });
      catRowCache = null;
      if (repo.sleeveId === gone.id) {
        var left = sleevesIn(repo.variant, repo.category);
        loadDraft(left[0] ? left[0].id : null);
      }
      forgetLibrary(gone.category);
      App.announce('polite', gone.name + ' removed from ' + gone.category + '.');
    }
  } catch (err) { repo.error = err.message; }
  repo.saving = false; render();
}

/* A save reaches open scenarios through the page's own cache: forgetting the
   category makes the rail fetch it again on its next render, so a PWA with
   the tool open sees the change without a reload. */
function forgetLibrary(category) {
  var lib = App.sleeveLib && App.sleeveLib();
  if (lib && lib[category]) delete lib[category];
  App.refresh();
}

/* ---- the product picker ------------------------------------------------- */
function openPicker(row) {
  repo.picker = { row: row, query: '', index: 0 }; repo.confirmDelete = false;
  render();
  var box = document.getElementById('repoSearch'); if (box) box.focus();
}
function closePicker(rerender) {
  repo.picker = null;
  if (rerender !== false) render();
}
function pickerMatches() {
  var q = repo.picker.query.trim().toLowerCase();
  var taken = {};
  repo.draft.products.forEach(function (r, i) { if (r.productId && i !== repo.picker.row) taken[r.productId] = true; });
  return (repo.data.products || []).filter(function (p) {
    if (taken[p.productId]) return false;
    return catMatches(p, q);
  });
}
function choose(productId) {
  var row = repo.picker.row;
  repo.draft.products[row].productId = productId;
  repo.dirty = true; repo.fieldError = null;
  closePicker(false); render();
  var w = document.querySelector('[data-repoweight="' + row + '"]'); if (w) { w.focus(); w.select(); }
}
function addRow() {
  repo.draft.products.push({ productId: null, weightPct: NaN });
  repo.dirty = true;
  openPicker(repo.draft.products.length - 1);
}
function removeRow(i) {
  repo.draft.products.splice(i, 1);
  repo.dirty = true; repo.picker = null; render();
}

/* ---- rendering: the sleeve view ---------------------------------------- */
function pickerListHtml() {
  var hits = pickerMatches();
  var idx = Math.min(repo.picker.index, Math.max(0, hits.length - 1));
  repo.picker.index = idx;
  var items = hits.slice(0, 40).map(function (p, i) {
    return '<li role="option" id="repoOpt' + i + '" data-repochoose="' + esc(p.productId) + '"'
      + ' aria-selected="' + (i === idx ? 'true' : 'false') + '">'
      + '<div><b>' + esc(p.name) + '</b><small>' + esc(productMeta(p))
      + (p.liquidity ? ' · ' + esc(p.liquidity) : '') + ' · ' + money2(p.productCost) + '%</small></div>'
      + '<span class="grp">' + esc(p.feeGroup) + '</span></li>';
  }).join('');
  var hint = hits.length
    ? hits.length + ' of ' + repo.data.products.length + ' match · ↑↓ to move, Enter to choose'
      + (hits.length > 40 ? ' · showing 40, keep typing' : '')
    : 'Nothing in the catalogue matches.';
  return items + '<li class="hint" aria-hidden="true">' + esc(hint) + '</li>';
}

function productsHtml() {
  var d = repo.draft;
  var rows = d.products.map(function (r, i) {
    var p = r.productId ? productById(r.productId) : null;
    var picking = repo.picker && repo.picker.row === i;
    var cell;
    if (picking) {
      cell = '<input type="text" class="repo-search" id="repoSearch" role="combobox" autocomplete="off"'
        + ' aria-expanded="true" aria-controls="repoPickerList" aria-autocomplete="list"'
        + ' aria-activedescendant="repoOpt' + repo.picker.index + '"'
        + ' placeholder="Search the catalogue…" value="' + esc(repo.picker.query) + '">'
        + '<ul class="repo-menu" id="repoPickerList" role="listbox">' + pickerListHtml() + '</ul>';
    } else if (p) {
      cell = '<button type="button" class="repo-pick" data-repopick="' + i + '" title="Change product">'
        + '<b>' + esc(p.name) + '</b><small>' + esc(productMeta(p)) + ' · ' + esc(p.feeGroup) + '</small></button>';
    } else if (r.productId) {
      cell = '<button type="button" class="repo-pick" data-repopick="' + i + '">'
        + '<b>' + esc(r.productId) + '</b><small class="warn">Not in the catalogue - choose another</small></button>';
    } else {
      cell = '<button type="button" class="repo-pick empty" data-repopick="' + i + '"><b>Choose a product…</b></button>';
    }
    return '<div class="pr">' + cell
      + '<input type="text" inputmode="decimal" class="repo-w" data-repoweight="' + i + '" aria-label="Weight, percent"'
      + ' value="' + (isFinite(r.weightPct) ? esc(money2(r.weightPct)) : '') + '">'
      + '<button type="button" class="repo-rm" data-reporm="' + i + '" aria-label="Remove product">×</button>'
      + '</div>';
  }).join('');
  return '<div class="repo-prods">'
    + '<div class="ph"><span>Product · from the catalogue</span><span style="text-align:right">Weight %</span><span></span></div>'
    + rows
    + '<div class="repo-tot" id="repoTotal">' + totalHtml() + '</div>'
    + '</div>';
}

function totalHtml() {
  var n = repo.draft.products.length;
  var t = total();
  var ok = n > 0 && Math.abs(t - 100) <= 0.005;
  return '<span>Total' + (n ? '' : ' · no products yet') + '</span>'
    + '<span class="' + (ok ? 'ok' : 'bad') + '">' + (n ? money2(t) + (ok ? ' ✓' : ' ✗') : '—') + '</span><span></span>';
}

function fieldErr(field) {
  var fe = repo.fieldError;
  return fe && fe.field === field ? '<p class="md-err" role="alert">' + esc(fe.message) + '</p>' : '';
}

function editorHtml() {
  var d = repo.draft;
  if (!d) return '<p class="repo-empty">Choose a sleeve, or start a new one.</p>';
  var entry = d.id ? sleeveById(d.id) : null;
  var fixed = isFixed(repo.category);
  var problems = draftProblems();
  var prov = '';
  if (entry) {
    prov = 'Created ' + esc(shortDate(entry.createdAt)) + (entry.createdBy ? ' by ' + esc(entry.createdBy) : '')
      + ' · last saved ' + esc(shortDate(entry.updatedAt)) + (entry.updatedBy ? ' by ' + esc(entry.updatedBy) : '');
    var elsewhere = (entry.offeredUnder || []).filter(function (v) { return v !== entry.variant; });
    if (elsewhere.length) prov += ' · the same name is offered under ' + esc(elsewhere.join(', '));
  } else if (d.create) {
    var picked = (d.variants || []);
    prov = 'New sleeve · ' + esc(d.category) + ' · '
      + (picked.length ? 'will be created under ' + esc(picked.join(', ')) : 'no implementation type chosen');
  } else {
    prov = 'New sleeve · ' + esc(repo.category) + ' under ' + esc(repo.variant);
  }
  var serverProblems = entry && entry.problems && entry.problems.length
    ? '<div class="repo-notice" role="status">' + entry.problems.map(esc).join(' ') + ' The sleeve is withheld from the pickers until this is fixed.</div>'
    : '';
  var categoryField;
  if (d.create) {
    /* Only a new sleeve chooses these. Which category it implements and which
       books offer it are its identity; changing them afterwards would be a
       different sleeve, so an existing one shows them read only. */
    categoryField = '<div class="repo-fld"><label for="repoCategory">Category</label>'
      + '<select id="repoCategory">'
      + repo.data.categories.map(function (c) {
          return '<option value="' + esc(c) + '"' + (c === d.category ? ' selected' : '') + '>'
            + esc(c) + (isFixed(c) ? ' (fixed - one sleeve per type)' : '') + '</option>';
        }).join('')
      + '</select>' + fieldErr('category') + '</div>';
  } else {
    categoryField = '<div class="repo-fld"><label>Category</label><div class="ro">' + esc(repo.category)
      + (fixed ? ' <span class="repo-fixed">fixed</span>' : '') + '</div></div>';
  }
  var variantField = '';
  if (d.create) {
    variantField = '<div class="repo-fld"><label>Implementation types</label>'
      + '<div class="repo-vars">'
      + repo.data.variants.map(function (v) {
          var taken = sleevesIn(v, d.category).some(function (x) {
            return x.name.trim().toLowerCase() === d.name.trim().toLowerCase();
          });
          var full = isFixed(d.category) && sleevesIn(v, d.category).length >= 1;
          var off = taken || full;
          return '<label class="repo-var' + (off ? ' off' : '') + '"'
            + (off ? ' title="' + esc(taken ? 'A sleeve of that name is already offered here'
                                            : 'This category already holds its one sleeve here') + '"' : '')
            + '><input type="checkbox" data-repovar="' + esc(v) + '"'
            + ((d.variants || []).indexOf(v) !== -1 ? ' checked' : '') + (off ? ' disabled' : '')
            + '> <span>' + esc(v) + '</span></label>';
        }).join('')
      + '</div>' + fieldErr('variants') + '</div>';
  }
  return ''
    + serverProblems
    + '<div class="repo-frow">'
    + '<div class="repo-fld"><label for="repoName">Sleeve name</label>'
    + '<input type="text" id="repoName" maxlength="80" value="' + esc(d.name) + '"'
    + (repo.fieldError && repo.fieldError.field === 'name' ? ' aria-invalid="true"' : '') + '>' + fieldErr('name') + '</div>'
    + categoryField
    + '</div>'
    + variantField
    + '<div class="repo-fld"><label for="repoNote">Note</label>'
    + '<input type="text" id="repoNote" maxlength="240" placeholder="Optional. Shown to PWAs in the picker hint." value="' + esc(d.note) + '"></div>'
    + '<div class="repo-fld"><label>Products</label>' + productsHtml()
    + fieldErr('products') + fieldErr('weights')
    + '<button type="button" class="btn repo-add" data-repoadd' + (repo.picker ? ' disabled' : '') + '>+ Add product</button></div>'
    + (problems.length && repo.dirty
        ? '<p class="repo-problems">' + problems.map(esc).join(' ') + '</p>' : '')
    + '<p class="repo-prov">' + prov + '</p>'
    + (entry ? historyPanelHtml(entry.id, entry.revisions) : '');
}

/* ---- the record: history and the removed sleeves (D65) ------------------
   A sleeve's earlier versions are kept for ever, and a delete takes a sleeve
   out of the library without taking it off the record. Both are read here.
   The history is fetched only when someone asks for it: most visits to a
   sleeve are to edit it, and a trail that grows for the life of the library
   has no business riding along in the console's first payload. */

var ACTION_WORDS = {
  baseline: 'history begins', created: 'created', updated: 'edited',
  reverted: 'put an earlier version back', deleted: 'archived',
  restored: 'restored to the library', imported: 'loaded from a file',
  seeded: 'loaded with the library'
};
var ACTION_LABELS = {
  created: 'Created', updated: 'Edited', reverted: 'Reverted', deleted: 'Archived',
  restored: 'Restored', imported: 'Imported', seeded: 'Seeded', baseline: 'Baseline'
};

function revisionHtml(entry) {
  var open = repo.openRevision === entry.revision;
  var products = entry.products.map(function (row) {
    var label = row.product ? esc(row.product.name)
      : esc(row.productId) + ' <span class="warn">not in the catalogue</span>';
    return '<li><span class="w">' + money2(row.weight * 100) + '%</span><span>' + label + '</span></li>';
  }).join('');
  var changes = entry.changes.length
    ? '<ul class="rev-changes">' + entry.changes.map(function (c) {
        return '<li>' + esc(c) + '</li>'; }).join('') + '</ul>'
    : '';
  return '<div class="rev' + (entry.current ? ' now' : '') + (open ? ' open' : '') + '">'
    + '<button type="button" class="rev-h" data-reporev="' + entry.revision + '"'
    + ' aria-expanded="' + (open ? 'true' : 'false') + '">'
    + '<span class="rev-n">r' + entry.revision + '</span>'
    + '<span class="rev-what"><b>' + esc(ACTION_WORDS[entry.action] || entry.action) + '</b>'
    + '<small>' + esc(shortDate(entry.at)) + (entry.actor ? ' · ' + esc(entry.actor) : '') + '</small></span>'
    + (entry.current ? '<span class="rev-now">in force</span>' : '')
    + '<span class="rev-caret" aria-hidden="true">›</span>'
    + '</button>'
    + changes
    + (open ? '<div class="rev-b"><p class="rev-name">' + esc(entry.name)
        + (entry.note ? ' <small>' + esc(entry.note) + '</small>' : '') + '</p>'
        + '<ul class="rev-products">' + products + '</ul>'
        + (entry.current ? ''
            : '<button type="button" class="btn rev-put" data-reporevert="' + entry.revision + '"'
              + (repo.saving ? ' disabled' : '') + '>Put this version back</button>')
        + '</div>' : '')
    + '</div>';
}

function historyPanelHtml(sleeveId, count) {
  var head = '<button type="button" class="repo-histh" data-repohistory="' + sleeveId + '"'
    + ' aria-expanded="' + (repo.historyOpen ? 'true' : 'false') + '">'
    + '<span>History</span><span class="n">' + (count || 0) + ' version'
    + (count === 1 ? '' : 's') + '</span>'
    + '<span class="rev-caret" aria-hidden="true">›</span></button>';
  if (!repo.historyOpen) return '<div class="repo-hist">' + head + '</div>';
  var body;
  if (repo.historyBusy) {
    body = '<p class="repo-none">Reading the record…</p>';
  } else if (!repo.history || repo.history.sleeveId !== sleeveId) {
    body = '<p class="repo-none">No record for this sleeve.</p>';
  } else if (!repo.history.entries.length) {
    body = '<p class="repo-none">Nothing recorded yet.</p>';
  } else {
    body = repo.history.entries.map(revisionHtml).join('');
  }
  return '<div class="repo-hist open">' + head + '<div class="repo-hist-b">' + body + '</div></div>';
}

async function loadHistory(sleeveId) {
  repo.historyBusy = true; render();
  try {
    var r = await api('GET', '/scenario/repository/sleeves/' + sleeveId + '/history');
    if (!r) return;
    if (!r.ok) throw new Error(r.body.error || ('Could not read the history (' + r.status + ')'));
    repo.history = { sleeveId: sleeveId, entries: r.body.history || [] };
    repo.error = null;
  } catch (err) { repo.error = err.message; }
  repo.historyBusy = false; render();
}

function toggleHistory(sleeveId) {
  repo.historyOpen = !repo.historyOpen;
  repo.openRevision = null;
  if (!repo.historyOpen) { render(); return; }
  if (repo.history && repo.history.sleeveId === sleeveId) { render(); return; }
  loadHistory(sleeveId);
}

/* Both of these change the library, so both re-read it rather than patching
   the copy in hand: a restore can change what a fixed category holds and a
   revert can change what the pickers offer, and guessing at either from the
   response is how the console and the store drift apart. */
async function restoreArchived(ids) {
  ids = (ids || []).filter(function (id) { return archivedById(id); });
  if (!ids.length || repo.saving) return;
  repo.saving = true; repo.error = null; render();
  try {
    var r = await api('POST', '/scenario/repository/sleeves/restore', { ids: ids });
    if (!r) return;
    if (!r.ok) {
      repo.error = r.body.error || ('Could not restore (' + r.status + ')');
    } else {
      var back = r.body.sleeves || [];
      App.announce('polite', back.length === 1
        ? back[0].name + ' is back in the library.'
        : back.length + ' sleeves are back in the library.');
      repo.saving = false;
      repo.historyOpen = false; repo.history = null;
      arc.selected = arc.selected.filter(function (id) { return ids.indexOf(id) === -1; });
      if (arc.detail != null && ids.indexOf(arc.detail) !== -1) arc.detail = null;
      back.forEach(function (b) { forgetLibrary(b.category); });
      await loadRepository();
      if (act.loaded) loadActivity(true);
      if (back.length === 1 && repo.view !== 'activity') {
        applyTarget({ variant: back[0].variant, category: back[0].category, to: back[0].id });
      }
      return;
    }
  } catch (err) { repo.error = err.message; }
  repo.saving = false; render();
}

async function revertTo(revisionNumber) {
  var id = repo.history && repo.history.sleeveId; if (!id) return;
  repo.saving = true; repo.error = null; render();
  try {
    var r = await api('POST', '/scenario/repository/sleeves/' + id + '/revert',
                      { revision: revisionNumber });
    if (!r) return;
    if (!r.ok) {
      repo.error = r.body.error || ('Could not put that version back (' + r.status + ')');
    } else {
      var sleeve = r.body.sleeve;
      App.announce('polite', 'r' + revisionNumber + ' is in force again for ' + sleeve.name + '.');
      repo.saving = false;
      forgetLibrary(sleeve.category);
      await loadRepository();
      await loadHistory(id);
      if (act.loaded) loadActivity(true);
      applyTarget({ variant: sleeve.variant, category: sleeve.category, to: sleeve.id });
      return;
    }
  } catch (err) { repo.error = err.message; }
  repo.saving = false; render();
}

/* The context menu on a sleeve: the two things worth doing to one from the
   list rather than the editor - offer it in another book, or take it out of
   this one. Both act on one click; there is no submenu and nothing to
   confirm beyond the menu itself, because both are reversible by the other. */
function sleeveMenuHtml() {
  if (!repo.menu) return '';
  var entry = sleeveById(repo.menu.id);
  if (!entry) return '';
  var elsewhere = repo.data.variants.filter(function (v) {
    return (entry.offeredUnder || []).indexOf(v) === -1;
  });
  var full = entry.fixed;
  var adds = elsewhere.map(function (v) {
    var blocked = full && sleevesIn(v, entry.category).length >= 1;
    return '<button type="button" class="repo-mi" data-repocopy="' + v + '"'
      + (blocked ? ' disabled title="' + esc(entry.category + ' already holds its one sleeve under ' + v) + '"' : '')
      + '>' + esc(v) + '</button>';
  }).join('');
  return '<div class="repo-ctx" style="left:' + repo.menu.x + 'px;top:' + repo.menu.y + 'px"'
    + ' role="menu" aria-label="' + esc(entry.name) + '">'
    + '<p class="h">' + esc(entry.name) + '<small>' + esc(entry.category) + ' · ' + esc(entry.variant) + '</small></p>'
    + '<p class="lbl">Add to implementation type</p>'
    + (adds || '<p class="none">Offered under every implementation type.</p>')
    + '<p class="sep"></p>'
    + '<button type="button" class="repo-mi danger" data-reporemove="' + entry.id + '"'
    + (entry.fixed ? ' disabled title="A fixed category always holds one sleeve"' : '')
    + '>Archive from ' + esc(entry.category) + '</button>'
    + '</div>';
}

function sleevesViewHtml() {
  var d = repo.data;
  var cats = d.categories.map(function (c) {
    var n = sleevesIn(repo.variant, c).length;
    return '<button type="button" class="repo-cat" role="tab" data-repocat="' + esc(c) + '"'
      + ' aria-selected="' + (c === repo.category ? 'true' : 'false') + '">' + esc(c)
      + (isFixed(c) ? ' <span class="repo-fixed">fixed</span>' : '')
      + '<span class="n">' + n + '</span></button>';
  }).join('');
  var offered = sleevesIn(repo.variant, repo.category);
  var newDisabled = isFixed(repo.category) && offered.length >= 1;
  var list = offered.map(function (s) {
    var vehicles = [];
    s.products.forEach(function (r) { if (r.product && vehicles.indexOf(r.product.vehicle) === -1) vehicles.push(r.product.vehicle); });
    var sub = s.products.length + ' product' + (s.products.length === 1 ? '' : 's')
      + (vehicles.length ? ' · ' + vehicles.join(', ') : '')
      + ' · saved ' + shortDate(s.updatedAt) + (s.updatedBy ? ' by ' + s.updatedBy : '');
    return '<button type="button" class="repo-sleeve" data-reposleeve="' + s.id + '"'
      + ' aria-selected="' + (s.id === repo.sleeveId && !(repo.draft && repo.draft.id === null) ? 'true' : 'false') + '">'
      + '<b>' + esc(s.name) + (s.problems.length ? ' <span class="warn">' + s.problems.length + ' problem' + (s.problems.length === 1 ? '' : 's') + '</span>' : '') + '</b>'
      + '<small>' + esc(sub) + '</small></button>';
  }).join('') || '<p class="repo-none">No sleeve under ' + esc(repo.variant) + ' yet.</p>';
  if (repo.draft && repo.draft.id === null) {
    list += '<div class="repo-sleeve new" aria-current="true"><b>' + (esc(repo.draft.name) || 'New sleeve') + '</b><small>unsaved</small></div>';
  }
  return '<div class="repo-b">'
    + '<div class="repo-pane"><div class="repo-pane-h">Categories</div>' + cats + '</div>'
    + '<div class="repo-pane"><div class="repo-pane-h">' + esc(repo.category)
    + '<button type="button" class="btn" data-reponew' + (newDisabled ? ' disabled title="A fixed category holds exactly one sleeve"' : '') + '>+ New sleeve</button></div>'
    + list + '</div>'
    + '<div class="repo-pane repo-ed">' + editorHtml() + '</div>'
    + '</div>' + sleeveMenuHtml();
}

/* ---- rendering: the archive (D66, option A) -----------------------------
   The sleeves taken out of the library, as one searchable table across every
   book. Built on the catalogue's chrome - the same search, chips, sortable
   head and dense rows - because an admin who has learned one should not have
   to learn the other. Ticking rows collects a batch; opening one shows what
   it held, its history, and the way back. */
function arcRows() {
  return archivedSleeves().map(function (s) {
    var live = (s.offeredUnder || []).filter(function (v) { return v !== s.variant; });
    return { s: s, held: s.products.length, liveElsewhere: live,
             hay: (s.name + ' ' + s.category + ' ' + s.variant + ' ' + (s.archivedBy || '') + ' '
               + s.products.map(function (r) { return r.product ? r.product.name : r.productId; }).join(' ')).toLowerCase() };
  });
}
function arcPasses(row, except) {
  var q = arc.query.trim().toLowerCase();
  if (q && row.hay.indexOf(q) === -1) return false;
  for (var f in arc.filters) {
    if (f === except || !arc.filters[f]) continue;
    if (String(row.s[f] || '') !== arc.filters[f]) return false;
  }
  return true;
}
function arcVisible() {
  var rows = arcRows().filter(function (r) { return arcPasses(r); });
  var key = arc.sort.key, dir = arc.sort.dir === 'desc' ? -1 : 1;
  rows.sort(function (a, b) {
    var x = key === 'held' ? a.held : (key === 'revisions' ? a.s.revisions : String(a.s[key] || '').toLowerCase());
    var y = key === 'held' ? b.held : (key === 'revisions' ? b.s.revisions : String(b.s[key] || '').toLowerCase());
    if (x < y) return -dir; if (x > y) return dir;
    return a.s.id - b.s.id;
  });
  return rows;
}
function arcFacet(field, order) {
  var counts = {};
  arcRows().forEach(function (r) { if (arcPasses(r, field)) { var v = r.s[field] || ''; counts[v] = (counts[v] || 0) + 1; } });
  var values = Object.keys(counts);
  if (order) values.sort(function (a, b) { return order.indexOf(a) - order.indexOf(b); });
  else values.sort();
  return values.map(function (v) { return { value: v, count: counts[v] }; });
}
function arcSelect(field, label, order) {
  var chosen = arc.filters[field] || '';
  var values = arcFacet(field, order);
  return '<label class="arc-sel' + (chosen ? ' on' : '') + '"><span>' + esc(label) + '</span>'
    + '<select data-arcfilter="' + field + '">'
    + '<option value="">' + (chosen ? 'Any' : 'Any') + '</option>'
    + values.map(function (v) {
        return '<option value="' + esc(v.value) + '"' + (v.value === chosen ? ' selected' : '') + '>'
          + esc(v.value) + ' (' + v.count + ')</option>';
      }).join('')
    + '</select></label>';
}
function arcFiltersInForce() {
  var n = arc.query.trim() ? 1 : 0;
  for (var f in arc.filters) if (arc.filters[f]) n += 1;
  return n;
}
function arcToolbarHtml() {
  return '<div class="cat-tools arc-tools">'
    + '<label class="cat-search"><span aria-hidden="true">⌕</span>'
    + '<input type="search" id="arcSearch" placeholder="Search sleeve, product, who…" value="' + esc(arc.query) + '"'
    + ' aria-label="Search the archive"><kbd aria-hidden="true">/</kbd></label>'
    + arcSelect('variant', 'Book', repo.data.variants)
    + arcSelect('category', 'Category', repo.data.categories)
    + arcSelect('archivedBy', 'Archived by')
    + (arcFiltersInForce() ? '<button type="button" class="cat-clear" data-arcclear>Clear</button>' : '')
    + '<span class="cat-count" id="arcCount">' + arcCountText() + '</span>'
    + '<a class="btn arc-export" href="' + esc(window.API_BASE + '/scenario/repository/archive.csv') + '" download>Export CSV</a>'
    + '</div>';
}
function arcCountText() {
  var all = archivedSleeves().length, shown = arcVisible().length;
  if (!all) return 'Nothing archived';
  return (shown === all ? all : shown + ' of ' + all) + ' archived sleeve' + (all === 1 ? '' : 's');
}
function arcHeadHtml() {
  var visible = arcVisible();
  var allOn = visible.length > 0 && visible.every(function (r) { return arc.selected.indexOf(r.s.id) !== -1; });
  return '<tr><th class="pinc"><input type="checkbox" data-arcselall aria-label="Select every shown sleeve"' + (allOn ? ' checked' : '') + (visible.length ? '' : ' disabled') + '></th>'
    + ARC_COLUMNS.map(function (c) {
        var sorted = arc.sort.key === c.key;
        return '<th' + (c.num ? ' class="num"' : '') + ' aria-sort="' + (sorted ? (arc.sort.dir === 'desc' ? 'descending' : 'ascending') : 'none') + '">'
          + '<button type="button" class="cat-sort' + (sorted ? ' on' : '') + '" data-arcsort="' + c.key + '">' + esc(c.label)
          + (sorted ? (arc.sort.dir === 'desc' ? ' ▼' : ' ▲') : '') + '</button></th>';
      }).join('') + '<th></th></tr>';
}
function arcBodyHtml() {
  var rows = arcVisible();
  if (!rows.length) {
    return '<tr><td colspan="' + (ARC_COLUMNS.length + 2) + '" class="cat-empty">'
      + (archivedSleeves().length
          ? 'No archived sleeve matches' + (arc.query.trim() ? ' <b>“' + esc(arc.query.trim()) + '”</b>' : '') + ' with the filters in force. '
            + '<button type="button" class="cat-clear" data-arcclear>Clear the filters</button>'
          : 'Nothing has been archived. A sleeve archived from the editor keeps its history and appears here.')
      + '</td></tr>';
  }
  return rows.map(function (r) {
    var s = r.s, on = arc.selected.indexOf(s.id) !== -1, open = arc.detail === s.id;
    return '<tr data-arcrow="' + s.id + '" class="' + (on ? 'pin' : '') + (open ? ' on' : '') + '" aria-selected="' + open + '">'
      + '<td class="pinc"><input type="checkbox" data-arcsel="' + s.id + '" aria-label="Select ' + esc(s.name) + '"' + (on ? ' checked' : '') + '></td>'
      + '<td><b>' + esc(s.name) + '</b>' + (s.problems.length ? ' <span class="warn">' + s.problems.length + ' problem' + (s.problems.length === 1 ? '' : 's') + '</span>' : '') + '</td>'
      + '<td>' + esc(s.category) + '</td>'
      + '<td>' + esc(s.variant) + '</td>'
      + '<td class="num">' + r.held + '</td>'
      + '<td>' + esc(shortDate(s.archivedAt)) + (s.archivedBy ? ' <span class="mut">· ' + esc(s.archivedBy) + '</span>' : '') + '</td>'
      + '<td class="num">' + s.revisions + '</td>'
      + '<td class="arc-status">' + (r.liveElsewhere.length
          ? '<span class="arc-badge live" title="A sleeve of this name is in the library under ' + esc(r.liveElsewhere.join(', ')) + '">live under ' + esc(r.liveElsewhere.join(', ')) + '</span>'
          : '<span class="arc-badge gone">archived</span>') + '</td>'
      + '</tr>';
  }).join('');
}
function arcDetailHtml() {
  var entry = arc.detail != null ? archivedById(arc.detail) : null;
  if (!entry) return '';
  var blocked = sleevesIn(entry.variant, entry.category).some(function (x) {
    return x.name.trim().toLowerCase() === entry.name.trim().toLowerCase();
  });
  var full = entry.fixed && sleevesIn(entry.variant, entry.category).length >= 1;
  var why = blocked ? 'A sleeve of that name is in the library again — rename or archive it first.'
    : (full ? entry.category + ' already holds its one sleeve here.' : '');
  var products = entry.products.map(function (row) {
    var label = row.product ? esc(row.product.name) + ' <span class="mut">' + esc(productMeta(row.product)) + '</span>'
      : esc(row.productId) + ' <span class="warn">not in the catalogue</span>';
    return '<li><span class="w">' + money2(row.weight * 100) + '%</span><span>' + label + '</span></li>';
  }).join('');
  return '<div class="arc-detail" id="arcDetail">'
    + '<div class="arc-dh"><div><h3>' + esc(entry.name) + '</h3>'
    + '<p>' + esc(entry.category) + ' · ' + esc(entry.variant) + '</p>'
    + '<p class="repo-prov">Archived ' + esc(shortDate(entry.archivedAt)) + (entry.archivedBy ? ' by ' + esc(entry.archivedBy) : '')
    + ' · created ' + esc(shortDate(entry.createdAt)) + (entry.createdBy ? ' by ' + esc(entry.createdBy) : '')
    + (entry.note ? ' · “' + esc(entry.note) + '”' : '') + '</p></div>'
    + '<div class="arc-dact">'
    + (why ? '<span class="repo-problems">' + esc(why) + '</span>' : '')
    + '<button type="button" class="btn btn-primary" data-arcrestore="' + entry.id + '"' + (why || repo.saving ? ' disabled' : '') + '>Restore to the library</button>'
    + '<button type="button" class="dlg-close arc-dclose" data-arcdetailclose aria-label="Close">×</button>'
    + '</div></div>'
    + '<div class="arc-db"><div><p class="m-lbl repo-pane-h arc-lbl">What it held when it was archived</p>'
    + '<ul class="rev-products big">' + products + '</ul></div>'
    + '<div>' + historyPanelHtml(entry.id, entry.revisions) + '</div></div>'
    + '</div>';
}
function archiveViewHtml() {
  return arcToolbarHtml()
    + '<div class="arc-b">'
    + '<div class="cat-tblwrap arc-tblwrap" tabindex="0" aria-label="Archived sleeves, scrolls">'
    + '<table class="cat-tbl dense arc-tbl"><thead id="arcHead">' + arcHeadHtml() + '</thead>'
    + '<tbody id="arcBody">' + arcBodyHtml() + '</tbody></table></div>'
    + arcDetailHtml()
    + '</div>';
}
function updateArchive() {
  var head = document.getElementById('arcHead'); if (head) head.innerHTML = arcHeadHtml();
  var body = document.getElementById('arcBody'); if (body) body.innerHTML = arcBodyHtml();
  var count = document.getElementById('arcCount'); if (count) count.innerHTML = arcCountText();
  var foot = document.getElementById('arcFoot'); if (foot) foot.outerHTML = arcFooterHtml();
  syncHash();
}
function arcFooterHtml() {
  var n = arc.selected.length;
  var d = repo.data;
  return '<div class="repo-f arc-f" id="arcFoot">'
    + '<span class="repo-src">Archive · ' + archivedSleeves().length + ' sleeve' + (archivedSleeves().length === 1 ? '' : 's')
    + ' · ' + (d.store.revisions || 0) + ' versions on record</span>'
    + '<span class="spacer"></span>'
    + (repo.error ? '<span class="md-err" role="alert">' + esc(repo.error) + '</span>' : '')
    + (n ? '<span class="repo-src">' + n + ' selected</span>'
         + '<button type="button" class="btn" data-arcclearsel>Clear</button>'
         + '<button type="button" class="btn btn-primary" data-arcrestoresel' + (repo.saving ? ' disabled' : '') + '>'
         + (repo.saving ? 'Restoring…' : 'Restore ' + n + ' sleeve' + (n === 1 ? '' : 's')) + '</button>'
       : '<span class="repo-src">Tick sleeves to restore several at once</span>')
    + '</div>';
}

/* ---- rendering: the activity feed (D66, option C) ------------------------
   The record read as a story: every revision across every sleeve, newest
   first, grouped by day. What happened is a sentence, what moved is the line
   under it, and the version it produced is one click away. The page is what
   the server handed back - a filter change is a fresh read, not a sift. */
function actParams(before) {
  var q = new URLSearchParams();
  if (act.actions.length) q.set('actions', act.actions.join(','));
  if (act.actor) q.set('actor', act.actor);
  if (act.variant) q.set('variant', act.variant);
  if (act.query.trim()) q.set('q', act.query.trim());
  if (act.range !== 'all') {
    var days = parseInt(act.range, 10) || 30;
    var since = new Date(Date.now() - days * 86400000);
    q.set('since', since.toISOString().slice(0, 10));
  }
  q.set('limit', '60');
  if (before) q.set('before', before);
  return q.toString();
}
async function loadActivity(reset) {
  if (act.busy) return;
  act.busy = true; act.error = null;
  if (reset) { act.entries = []; act.next = null; }
  render();
  try {
    var r = await api('GET', '/scenario/repository/activity?' + actParams(reset ? null : act.next));
    if (!r) return;
    if (!r.ok) throw new Error(r.body.error || ('Could not read the record (' + r.status + ')'));
    act.entries = reset ? r.body.entries : act.entries.concat(r.body.entries);
    act.next = r.body.next; act.total = r.body.total; act.facets = r.body.facets;
    act.earliest = r.body.earliest || '';
    act.loaded = true;
  } catch (err) { act.error = err.message; }
  act.busy = false; render();
}
function actRefresh() {
  /* the search box is live; the rest is one read per change */
  if (actTimer) clearTimeout(actTimer);
  actTimer = setTimeout(function () { actTimer = null; loadActivity(true); }, 220);
}
function actSelect(name, attr, label, values, chosen) {
  return '<label class="arc-sel' + (chosen ? ' on' : '') + '"><span>' + esc(label) + '</span>'
    + '<select data-' + attr + '>' + values.map(function (v) {
        return '<option value="' + esc(v[0]) + '"' + (v[0] === chosen ? ' selected' : '') + '>' + esc(v[1]) + '</option>';
      }).join('') + '</select></label>';
}
function actToolbarHtml() {
  var facets = act.facets || { action: {}, actor: {}, variant: {} };
  var chips = ACT_ACTIONS.map(function (a) {
    var on = act.actions.indexOf(a) !== -1, n = facets.action[a] || 0;
    return '<button type="button" class="act-chip' + (on ? ' on' : '') + (!on && !n ? ' off' : '') + '" data-acttoggle="' + a + '" aria-pressed="' + on + '">'
      + esc(ACTION_LABELS[a]) + '<span class="n">' + n + '</span></button>';
  }).join('');
  var people = [['', 'Anyone']].concat(Object.keys(facets.actor).map(function (k) { return [k, k + ' (' + facets.actor[k] + ')']; }));
  if (act.actor && !facets.actor[act.actor]) people.push([act.actor, act.actor + ' (0)']);
  var books = [['', 'All books']].concat((repo.data.variants || []).map(function (v) { return [v, v + ' (' + (facets.variant[v] || 0) + ')']; }));
  return '<div class="cat-tools act-tools">'
    + '<label class="cat-search"><span aria-hidden="true">⌕</span>'
    + '<input type="search" id="actSearch" placeholder="Search sleeve, product, who, what changed…" value="' + esc(act.query) + '"'
    + ' aria-label="Search the record"><kbd aria-hidden="true">/</kbd></label>'
    + '<span class="act-chips" role="group" aria-label="Actions">' + chips + '</span>'
    + actSelect('who', 'actwho', 'Who', people, act.actor)
    + actSelect('book', 'actbook', 'Book', books, act.variant)
    + actSelect('range', 'actrange', 'When', ACT_RANGES, act.range)
    + ((act.query.trim() || act.actions.length || act.actor || act.variant || act.range !== '30d')
        ? '<button type="button" class="cat-clear" data-actclear>Clear</button>' : '')
    + '<a class="btn arc-export" href="' + esc(window.API_BASE + '/scenario/repository/activity.csv?' + actParams(null).replace(/&?limit=\d+/, '')) + '" download>Export CSV</a>'
    + '</div>';
}
function actSentence(e) {
  var what = ACTION_WORDS[e.action] || e.action;
  return '<b>' + esc(e.name) + '</b> <span class="act-badge ' + esc(e.action) + '">' + esc(ACTION_LABELS[e.action] || e.action) + '</span>'
    + ' <span class="mut">' + esc(what) + (e.actor ? ' by ' + esc(e.actor) : '') + '</span>';
}
function actFeedHtml() {
  if (!act.loaded && act.busy) return '<p class="repo-loading">Reading the record…</p>';
  if (act.error && !act.entries.length) return '<p class="repo-loading md-err">' + esc(act.error) + '</p>';
  if (!act.entries.length) {
    return '<p class="repo-none act-none">Nothing on record'
      + (act.range !== 'all' ? ' in the last ' + esc(act.range.replace('d', ' days')) : '')
      + ' with the filters in force.'
      + (act.range !== 'all' ? ' <button type="button" class="cat-clear" data-actrange="all">Show all time</button>' : '') + '</p>';
  }
  var out = [], day = null;
  act.entries.forEach(function (e) {
    var d = (e.at || '').slice(0, 10);
    if (d !== day) {
      day = d;
      out.push('<p class="act-day">' + esc(longDate(e.at)) + '</p>');
    }
    var sub = esc(e.category) + ' · ' + esc(e.variant) + ' · r' + e.revision
      + (e.action === 'baseline' ? ' · history begins here' : '')
      + (e.products.length ? ' · ' + e.products.length + ' product' + (e.products.length === 1 ? '' : 's') : '');
    var changes = e.changes.length ? '<ul class="rev-changes act-changes">' + e.changes.map(function (c) { return '<li>' + esc(c) + '</li>'; }).join('') + '</ul>' : '';
    var action;
    if (e.sleeveArchived) {
      action = '<button type="button" class="btn act-btn" data-actview="' + e.sleeveId + '" data-actrev="' + e.revision + '" data-actarchived="1">Open in Archive</button>'
        + (e.action === 'deleted' && e.current ? '<button type="button" class="btn act-btn" data-actrestore="' + e.sleeveId + '"' + (repo.saving ? ' disabled' : '') + '>Restore</button>' : '');
    } else {
      action = '<button type="button" class="btn act-btn" data-actview="' + e.sleeveId + '" data-actrev="' + e.revision + '">'
        + (e.current ? 'Open sleeve' : 'View r' + e.revision) + '</button>';
    }
    out.push('<div class="act-ev' + (e.current ? ' now' : '') + '">'
      + '<span class="t">' + esc((e.at || '').slice(11, 16)) + '</span>'
      + '<div class="w"><p>' + actSentence(e) + '</p><small>' + sub + '</small>' + changes + '</div>'
      + '<span class="a">' + action + '</span></div>');
  });
  if (act.next) {
    out.push('<div class="act-more"><button type="button" class="btn" data-actmore' + (act.busy ? ' disabled' : '') + '>'
      + (act.busy ? 'Reading…' : 'Earlier changes') + '</button></div>');
  }
  return out.join('');
}
function longDate(iso) {
  if (!iso) return '';
  var d = new Date(iso); if (isNaN(d)) return iso.slice(0, 10);
  var today = new Date(); today.setHours(0, 0, 0, 0);
  var that = new Date(d); that.setHours(0, 0, 0, 0);
  var diff = Math.round((today - that) / 86400000);
  var label = d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  if (diff === 0) return 'Today · ' + label;
  if (diff === 1) return 'Yesterday · ' + label;
  return label;
}
function activityViewHtml() {
  return actToolbarHtml() + '<div class="act-b" id="actBody">' + actFeedHtml() + '</div>';
}
function actFooterHtml() {
  var shown = act.entries.length;
  return '<div class="repo-f act-f">'
    + '<span class="repo-src">' + (act.loaded
        ? (shown === act.total ? shown : shown + ' of ' + act.total) + ' change' + (act.total === 1 ? '' : 's')
          + (act.earliest ? ' · on record since ' + esc(shortDate(act.earliest)) : '')
        : 'Reading the record…') + '</span>'
    + '<span class="spacer"></span>'
    + (repo.error ? '<span class="md-err" role="alert">' + esc(repo.error) + '</span>' : '')
    + '</div>';
}

/* ---- rendering: the proposal register (D69) -----------------------------
   Every delivered proposal, newest first, as a table the admin can filter
   and search; one open below it with its two pictures - the allocation as
   proposed, and the same allocation with its sleeves opened into products -
   and the delivered workbook one click away. The one thing the register
   computes rather than stores is the drift badge on a sleeve: the revision
   the proposal pinned, against where the library is now. */
function regParams(before) {
  var q = new URLSearchParams();
  if (reg.exportedBy) q.set('exportedBy', reg.exportedBy);
  if (reg.primaryPwa) q.set('primaryPwa', reg.primaryPwa);
  if (reg.currency) q.set('currency', reg.currency);
  if (reg.variant) q.set('variant', reg.variant);
  if (reg.query.trim()) q.set('q', reg.query.trim());
  if (reg.range !== 'all') {
    var days = parseInt(reg.range, 10) || 90;
    q.set('since', new Date(Date.now() - days * 86400000).toISOString().slice(0, 10));
  }
  q.set('limit', '60');
  if (before) q.set('before', before);
  return q.toString();
}
async function loadRegister(reset) {
  if (reg.busy) return;
  reg.busy = true; reg.error = null;
  if (reset) { reg.entries = []; reg.next = null; }
  render();
  try {
    var r = await api('GET', '/scenario/repository/proposals?' + regParams(reset ? null : reg.next));
    if (!r) return;
    if (!r.ok) throw new Error(r.body.error || ('Could not read the register (' + r.status + ')'));
    reg.entries = reset ? r.body.entries : reg.entries.concat(r.body.entries);
    reg.next = r.body.next; reg.total = r.body.total; reg.facets = r.body.facets;
    reg.loaded = true;
  } catch (err) { reg.error = err.message; }
  reg.busy = false; render();
}
async function loadProposal(proposalId) {
  reg.recordBusy = true; render();
  try {
    var r = await api('GET', '/scenario/repository/proposals/' + encodeURIComponent(proposalId));
    if (!r) return;
    if (!r.ok) throw new Error(r.body.error || ('Could not read the proposal (' + r.status + ')'));
    reg.record = r.body.proposal;
  } catch (err) { reg.error = err.message; reg.record = null; }
  reg.recordBusy = false; render();
}
function regRefresh() {
  if (regTimer) clearTimeout(regTimer);
  regTimer = setTimeout(function () { regTimer = null; loadRegister(true); }, 220);
}
function regSelect(attr, label, values, chosen) {
  return '<label class="arc-sel' + (chosen ? ' on' : '') + '"><span>' + esc(label) + '</span>'
    + '<select data-' + attr + '>' + values.map(function (v) {
        return '<option value="' + esc(v[0]) + '"' + (v[0] === chosen ? ' selected' : '') + '>' + esc(v[1]) + '</option>';
      }).join('') + '</select></label>';
}
function regFacetOptions(facet, chosen, any) {
  var counts = (reg.facets && reg.facets[facet]) || {};
  var out = [['', any]].concat(Object.keys(counts).map(function (k) { return [k, k + ' (' + counts[k] + ')']; }));
  if (chosen && !counts[chosen]) out.push([chosen, chosen + ' (0)']);
  return out;
}
function regFiltersInForce() {
  return !!(reg.query.trim() || reg.exportedBy || reg.primaryPwa || reg.currency || reg.variant || reg.range !== '90d');
}
function money(n) {
  if (typeof n !== 'number') return '—';
  if (n >= 1e6) return '$' + (Math.round(n / 1e5) / 10).toFixed(1) + 'm';
  if (n >= 1e3) return '$' + Math.round(n / 1e3) + 'k';
  return '$' + Math.round(n);
}
function regToolbarHtml() {
  return '<div class="cat-tools reg-tools">'
    + '<label class="cat-search"><span aria-hidden="true">⌕</span>'
    + '<input type="search" id="regSearch" placeholder="Search PWA, person, sleeve or portfolio…" value="' + esc(reg.query) + '"'
    + ' aria-label="Search the register"><kbd aria-hidden="true">/</kbd></label>'
    + regSelect('regwho', 'By', regFacetOptions('exportedBy', reg.exportedBy, 'Anyone'), reg.exportedBy)
    + regSelect('regpwa', 'PWA', regFacetOptions('primaryPwa', reg.primaryPwa, 'Any PWA'), reg.primaryPwa)
    + regSelect('regccy', 'Currency', regFacetOptions('currency', reg.currency, 'Any'), reg.currency)
    + regSelect('regbook', 'Book', regFacetOptions('variant', reg.variant, 'All books'), reg.variant)
    + regSelect('regrange', 'When', REG_RANGES, reg.range)
    + (regFiltersInForce() ? '<button type="button" class="cat-clear" data-regclear>Clear</button>' : '')
    + '<a class="btn arc-export" href="' + esc(window.API_BASE + '/scenario/repository/proposals.csv?' + regParams(null).replace(/&?limit=\d+/, '')) + '" download>Export CSV</a>'
    + '</div>';
}
function regHeadHtml() {
  return '<tr><th>Exported</th><th>By</th><th>Primary PWA</th><th class="num">Mandate</th>'
    + '<th>Basis</th><th>Book</th><th>Proposal portfolio</th><th>Pricing</th><th></th></tr>';
}
function regBodyHtml() {
  if (!reg.loaded && reg.busy) return '<tr><td colspan="9" class="cat-empty">Reading the register…</td></tr>';
  if (reg.error && !reg.entries.length) return '<tr><td colspan="9" class="cat-empty md-err">' + esc(reg.error) + '</td></tr>';
  if (!reg.entries.length) {
    return '<tr><td colspan="9" class="cat-empty">No proposal on record'
      + (reg.range !== 'all' ? ' in the ' + esc(REG_RANGES.filter(function (r) { return r[0] === reg.range; })[0][1].toLowerCase()) : '')
      + ' with the filters in force.'
      + (regFiltersInForce() ? ' <button type="button" class="cat-clear" data-regclear>Clear the filters</button>' : '')
      + '</td></tr>';
  }
  return reg.entries.map(function (e) {
    var when = e.exportedAt ? shortDate(e.exportedAt) + ' ' + e.exportedAt.slice(11, 16) : '';
    var pricing = e.includeFees ? esc((e.feeSchedule || '') + (e.feeLevel ? ' · ' + e.feeLevel.replace(/^PMG |^Management /, '') : '')) : '<span class="mut">no fees</span>';
    var flags = [];
    if (e.sequence > 1) flags.push('<span class="arc-badge acc">#' + e.sequence + '</span>');
    if (e.tacticalTilt) flags.push('<span class="arc-badge mute">tilt</span>');
    return '<tr data-regrow="' + esc(e.proposalId) + '" class="' + (reg.detail === e.proposalId ? 'on' : '') + '" aria-selected="' + (reg.detail === e.proposalId) + '">'
      + '<td>' + esc(when) + '</td>'
      + '<td>' + esc(e.exportedBy) + '</td>'
      + '<td><b>' + esc(e.primaryPwa) + '</b></td>'
      + '<td class="num">' + money(e.mandateSize) + '</td>'
      + '<td>' + esc(e.currency) + ' · ' + esc(e.hedging) + '</td>'
      + '<td>' + esc(e.variant) + '</td>'
      + '<td>' + esc((e.baseKey || '').split('|').slice(1, 3).join(' ')) + '</td>'
      + '<td>' + pricing + '</td>'
      + '<td class="arc-status">' + flags.join(' ') + '</td>'
      + '</tr>';
  }).join('');
}
function regPinBadge(pin) {
  if (pin.sleeveId == null || pin.revision == null) return '';
  if (pin.archived) return ' <span class="arc-badge gone" title="This sleeve has since been archived">r' + pin.revision + ' · archived</span>';
  if (pin.moved) return ' <span class="arc-badge warn" title="The library has moved on since this proposal">r' + pin.revision + ' · r' + pin.nowRevision + ' now</span>';
  return ' <span class="arc-badge mute">r' + pin.revision + '</span>';
}
function regAllocationHtml(record) {
  /* the proposal portfolio alone - the base column, the one the implemented
     model is built on. The comparisons were analysis, and are not kept. */
  var a = record.allocation || {};
  if (!a.categories || !a.categories.length) return '<p class="repo-none">No allocation recorded.</p>';
  var rows = '';
  a.categories.forEach(function (cat) {
    rows += '<tr class="grp"><td>' + esc(cat.name) + '</td><td class="num">' + money2(cat.weightPct) + '%</td></tr>';
    (cat.assets || []).forEach(function (asset) {
      rows += '<tr><td class="sub">' + esc(asset.reportingName) + '</td><td class="num">' + money2(asset.weightPct) + '%</td></tr>';
    });
  });
  return '<table class="cat-tbl dense reg-pic reg-pic-one"><thead><tr><th>Category / asset</th>'
    + '<th class="num">' + esc(a.name || a.keyStr || 'Proposal portfolio') + '</th></tr></thead>'
    + '<tbody>' + rows + '</tbody></table>';
}
function regImplementedHtml(record) {
  var groups = record.implemented || [];
  if (!groups.length) return '<p class="repo-none">No implemented model recorded.</p>';
  var pins = {};
  (record.sleeves || []).forEach(function (p) { pins[p.category] = p; });
  var rows = '';
  groups.forEach(function (g) {
    var pin = pins[g.category] || {sleeveId: g.sleeveId, revision: g.revision};
    rows += '<tr class="grp"><td>' + esc(g.category) + '</td>'
      + '<td>' + (g.sleeve ? esc(g.sleeve) + regPinBadge(pin) : '<span class="mut">no sleeve</span>') + '</td>'
      + '<td></td><td class="num">' + money2(g.weightPct) + '%</td><td class="num"></td></tr>';
    g.items.forEach(function (it) {
      rows += '<tr><td class="sub">' + esc(it.name || it.productId) + '</td><td></td>'
        + '<td class="mut">' + esc([it.ticker, it.vehicle].filter(Boolean).join(' · ') || '—') + '</td>'
        + '<td class="num">' + money2(it.printedPct) + '%</td>'
        + '<td class="num">' + money(it.notional) + '</td></tr>';
    });
  });
  return '<table class="cat-tbl dense reg-pic"><thead><tr><th>Category / product</th><th>Sleeve</th>'
    + '<th>Ticker · vehicle</th><th class="num">Weight</th><th class="num">Notional</th></tr></thead>'
    + '<tbody>' + rows + '</tbody></table>';
}
function regDetailHtml() {
  if (!reg.detail) return '';
  var r = reg.record;
  if (reg.recordBusy || !r || r.proposalId !== reg.detail) {
    return '<div class="arc-detail reg-detail"><p class="repo-none">Reading the proposal…</p></div>';
  }
  var pins = r.sleeves || [];
  var moved = pins.filter(function (p) { return p.moved || p.archived; }).length;
  return '<div class="arc-detail reg-detail" id="regDetail">'
    + '<div class="arc-dh"><div>'
    + '<h3>' + esc(r.primaryPwa) + ' · ' + money(r.mandateSize) + '</h3>'
    + '<p>' + esc(r.currency) + ' · ' + esc(r.hedging) + ' · ' + esc(r.variant)
    + ' · ' + esc((r.baseKey || '').split('|').slice(1, 3).join(' '))
    + (r.tacticalTilt ? ' · tactical tilt' : '') + (r.volPremium ? ' · vol premium' : '')
    + (r.includeFees ? ' · ' + esc((r.feeSchedule || '') + ' ' + (r.feeLevel || '')) : ' · no fees') + '</p>'
    + '<p class="repo-prov">' + esc(r.proposalId) + ' · exported ' + esc(shortDate(r.exportedAt)) + ' ' + esc((r.exportedAt || '').slice(11, 16))
    + ' by ' + esc(r.exportedBy) + (r.createdBy && r.createdBy !== r.exportedBy ? ' · started by ' + esc(r.createdBy) : '')
    + ' · workbook ' + Math.round(r.workbookBytes / 1024) + ' KB · sha ' + esc((r.workbookSha || '').slice(0, 8)) + '…'
    + (moved ? ' · <b class="warn">' + moved + ' sleeve' + (moved === 1 ? '' : 's') + ' moved since</b>' : '') + '</p>'
    + '</div><div class="arc-dact">'
    + '<a class="btn btn-primary" href="' + esc(window.API_BASE + '/scenario/repository/proposals/' + encodeURIComponent(r.proposalId) + '/workbook') + '" download>Download the workbook</a>'
    + '<div class="repo-seg" role="tablist" aria-label="Picture">'
    + '<button type="button" role="tab" data-regpic="allocation" aria-selected="' + (reg.picture === 'allocation') + '">Allocation</button>'
    + '<button type="button" role="tab" data-regpic="implemented" aria-selected="' + (reg.picture === 'implemented') + '">Implemented</button>'
    + '</div>'
    + '<button type="button" class="dlg-close arc-dclose" data-regdetailclose aria-label="Close">×</button>'
    + '</div></div>'
    + '<div class="reg-pic-wrap">' + (reg.picture === 'allocation' ? regAllocationHtml(r) : regImplementedHtml(r)) + '</div>'
    + '</div>';
}
function registerViewHtml() {
  return regToolbarHtml()
    + '<div class="arc-b">'
    + '<div class="cat-tblwrap arc-tblwrap" tabindex="0" aria-label="Proposal register, scrolls">'
    + '<table class="cat-tbl dense arc-tbl reg-tbl"><thead>' + regHeadHtml() + '</thead>'
    + '<tbody id="regBody">' + regBodyHtml()
    + (reg.next ? '<tr><td colspan="9" class="act-more"><button type="button" class="btn" data-regmore' + (reg.busy ? ' disabled' : '') + '>' + (reg.busy ? 'Reading…' : 'Earlier proposals') + '</button></td></tr>' : '')
    + '</tbody></table></div>'
    + regDetailHtml()
    + '</div>';
}
function regFooterHtml() {
  var d = repo.data;
  var shown = reg.entries.length;
  var total = (d && d.register && d.register.proposals) || 0;
  var bytes = (d && d.register && d.register.workbookBytes) || 0;
  var size = bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + ' MB' : Math.round(bytes / 1024) + ' KB';
  return '<div class="repo-f reg-f">'
    + '<span class="repo-src">Register · ' + total + ' proposal' + (total === 1 ? '' : 's') + ' · ' + size + ' of workbooks'
    + (d && d.register && d.register.earliest ? ' · since ' + esc(shortDate(d.register.earliest)) : '') + '</span>'
    + '<span class="spacer"></span>'
    + (repo.error ? '<span class="md-err" role="alert">' + esc(repo.error) + '</span>' : '')
    + '<span class="repo-src">' + (reg.loaded ? (shown === reg.total ? shown : shown + ' of ' + reg.total) + ' shown' : '') + ' · append-only</span>'
    + '</div>';
}

/* ---- rendering: the catalogue view (D58; laid out as the terminal, D63) ----
   A facet rail of every constraint with counts, a dense table of every field
   with the four figures on the right, a tray of pinned products that opens
   into a comparison, and a side panel for one product on demand. Built for
   someone who knows the products and is narrowing to a shortlist in a hurry:
   nothing explains itself, everything is one click or one key away, and the
   whole view is a link. */
function catVisible() { return catSort(catFilter(catRows(), cat), cat.sort); }
function catColumns() {
  return CAT_COLUMNS.filter(function (c) { return c.fixed || cat.hidden.indexOf(c.key) === -1; });
}
function catFiltersInForce() {
  var n = cat.query.trim() ? 1 : 0;
  for (var f in cat.filters) if (cat.filters[f] && cat.filters[f].length) n += 1;
  return n;
}
function catMoney(n) {
  if (typeof n !== 'number') return '—';
  if (n >= 1e6) return '$' + (Math.round(n / 1e5) / 10).toString().replace(/\.0$/, '') + 'm';
  if (n >= 1e3) return '$' + (Math.round(n / 100) / 10).toString().replace(/\.0$/, '') + 'k';
  return '$' + Math.round(n);
}
function catFigure(v, dp) { return (typeof v === 'number' && isFinite(v)) ? v.toFixed(dp) : '—'; }
function catTierId() { var t = App.opt('fees.tier', null); return t ? t.id : null; }
function catPricedLabel() {
  var tier = catTierId(), schedule = App.feeSchedule && App.feeSchedule();
  if (!tier || !schedule) return null;
  return schedule + ' · ' + (App.feeLevel ? App.feeLevel() : '') + ' · ' + tier;
}
function liqClass(v) {
  if (!v) return '';
  var s = String(v).toLowerCase();
  if (s === 'daily' || s === 'weekly') return '';
  if (s === 'drawdown' || s === 'closed' || s === 'illiquid') return ' lock';
  return ' slow';
}

function catToolbarHtml() {
  return '<div class="cat-tools">'
    + '<label class="cat-search"><span aria-hidden="true">⌕</span>'
    + '<input type="search" id="catSearch" placeholder="Search name, ticker, class…" value="' + esc(cat.query) + '"'
    + ' aria-label="Search the catalogue"><kbd aria-hidden="true">/</kbd></label>'
    + '<div class="repo-seg cat-density" role="tablist" aria-label="Density">'
    + '<button type="button" role="tab" data-catdensity="dense" aria-selected="' + (cat.density === 'dense') + '">Dense</button>'
    + '<button type="button" role="tab" data-catdensity="comfortable" aria-selected="' + (cat.density !== 'dense') + '">Comfortable</button>'
    + '</div>'
    + '<span class="cat-chipwrap">'
    + '<button type="button" class="cat-chip' + (cat.hidden.length ? ' on' : '') + '" data-catcols aria-expanded="' + (cat.openChip === 'cols') + '">Columns'
    + (cat.hidden.length ? ' <b>' + (CAT_COLUMNS.length - cat.hidden.length) + ' of ' + CAT_COLUMNS.length + '</b>' : '') + ' <span class="car">▾</span></button>'
    + (cat.openChip === 'cols'
        ? '<div class="cat-menu" role="group" aria-label="Columns">'
          + CAT_COLUMNS.map(function (c) {
              return '<label class="cat-opt' + (c.fixed ? ' fixed' : '') + '"><input type="checkbox" data-catcol="' + c.key + '"'
                + (cat.hidden.indexOf(c.key) === -1 ? ' checked' : '') + (c.fixed ? ' disabled' : '') + '> <span>' + esc(c.label)
                + (c.tier ? ' @ tier' : '') + (c.derived ? ' (derived)' : '') + '</span></label>';
            }).join('')
          + (cat.hidden.length ? '<button type="button" class="cat-clear" data-catshowall>Show all</button>' : '')
          + '</div>' : '')
    + '</span>'
    + '<span class="cat-count" id="catCount">' + catCountText() + '</span>'
    + '</div>';
}

function catCountText() {
  var n = catVisible().length, all = repo.data.products.length;
  var priced = catPricedLabel();
  return (n === all ? all + ' products' : 'Showing ' + n + ' of ' + all)
    + (cat.sort ? ' · sorted by ' + esc(catColumnLabel(cat.sort.key)) + (cat.sort.dir === 'desc' ? ' ↓' : ' ↑') : '')
    + (priced ? ' · priced ' + esc(priced) : ' · unpriced — no mandate open')
    + (catFiltersInForce() ? ' · <button type="button" class="cat-clear" data-catclearall>Clear</button>' : '');
}
function catColumnLabel(key) {
  var c = CAT_COLUMNS.filter(function (x) { return x.key === key; })[0];
  return c ? c.label : key;
}

function catFacetsHtml() {
  var orders = { category: repo.data.categories.concat([UNPLACED]), book: repo.data.variants.concat([UNPLACED]) };
  var facets = catFacets(catRows(), CAT_FACETS, cat, orders);
  return '<div class="cat-facets" id="catFacets">'
    + CAT_FACETS.map(function (f) {
        var chosen = cat.filters[f.key] || [];
        var values = facets[f.key] || [];
        if (!values.length) return '';
        return '<p class="cat-fh">' + esc(f.label) + '</p>'
          + values.map(function (v) {
              var on = chosen.indexOf(v.value) !== -1;
              var off = !on && v.count === 0;
              return '<label class="cat-fo' + (on ? ' on' : '') + (off ? ' off' : '') + '">'
                + '<input type="checkbox" data-catfacet="' + esc(f.key) + '" data-catval="' + esc(v.value) + '"'
                + (on ? ' checked' : '') + (off ? ' disabled' : '') + '>'
                + '<span class="v">' + esc(v.value || '(blank)') + '</span><span class="n">' + v.count + '</span></label>';
            }).join('');
      }).join('')
    + (catFiltersInForce() ? '<button type="button" class="cat-clear cat-facets-clear" data-catclearall>Clear all filters</button>' : '')
    + '</div>';
}

function catHeadHtml() {
  var tier = catTierId();
  return '<tr><th class="pinc"></th>' + catColumns().map(function (c) {
    var sorted = cat.sort && cat.sort.key === c.key;
    return '<th' + (c.num ? ' class="num"' : '') + ' aria-sort="' + (sorted ? (cat.sort.dir === 'desc' ? 'descending' : 'ascending') : 'none') + '">'
      + '<button type="button" class="cat-sort' + (sorted ? ' on' : '') + (c.derived ? ' drv' : '') + '" data-catsort="' + c.key + '"'
      + (c.derived ? ' title="Derived: distribution yield less all-in cost"' : '') + '>' + esc(c.label)
      + (c.tier ? '<small>' + (tier ? '@' + esc(tier) : 'no tier') + '</small>' : '')
      + (sorted ? (cat.sort.dir === 'desc' ? ' ▼' : ' ▲') : '') + '</button></th>';
  }).join('') + '</tr>';
}

function catCellHtml(row, c) {
  var p = row.p;
  switch (c.key) {
    case 'ticker': return '<td class="tk">' + esc(p.ticker) + '</td>';
    case 'name': return '<td class="nm"><b>' + esc(p.name) + (row.tooBig ? ' <span class="flag" title="Minimum above the open mandate">min ' + esc(catMoney(p.minimumInvestment)) + '</span>' : '') + '</b></td>';
    case 'vehicle': return '<td><span class="veh">' + esc(p.vehicle) + '</span></td>';
    case 'liquidity': return '<td><span class="liq' + liqClass(p.liquidity) + '">' + esc(p.liquidity) + '</span></td>';
    case 'productCost': return '<td class="num">' + catFigure(p.productCost, 2) + '</td>';
    case 'mgmt': return '<td class="num mute">' + catFigure(row.mgmt, 2) + '</td>';
    case 'allIn': return '<td class="num"><b>' + catFigure(row.allIn, 2) + '</b></td>';
    case 'distributionYield': return '<td class="num">' + catFigure(p.distributionYield, 2) + '</td>';
    case 'net': return '<td class="num' + (row.net === null ? ' mute' : (row.net < 0 ? ' neg' : ' pos')) + '">'
      + (row.net === null ? '—' : (row.net >= 0 ? '+' : '−') + Math.abs(row.net).toFixed(2)) + '</td>';
    case 'minimumInvestment': return '<td class="num' + (typeof p.minimumInvestment === 'number' ? '' : ' mute') + '">' + esc(catMoney(p.minimumInvestment)) + '</td>';
    case 'used': return '<td class="num used' + (row.used ? '' : ' zero') + '">' + row.used + '</td>';
    default: return '<td class="mute">' + esc(p[c.key]) + '</td>';
  }
}

function catBodyHtml() {
  var rows = catVisible();
  if (!rows.length) {
    return '<tr><td colspan="' + (catColumns().length + 1) + '" class="cat-empty">No product matches'
      + (cat.query.trim() ? ' <b>“' + esc(cat.query.trim()) + '”</b>' : '') + ' with the filters in force. '
      + '<button type="button" class="cat-clear" data-catclearall>Clear the filters</button> · '
      + repo.data.products.length + ' in the catalogue.</td></tr>';
  }
  var cols = catColumns();
  return rows.map(function (row) {
    var id = row.p.productId, pinned = cat.pins.indexOf(id) !== -1;
    return '<tr data-catrow="' + esc(id) + '" class="' + (pinned ? 'pin' : '') + (id === cat.cursor ? ' cur' : '')
      + (id === cat.detail ? ' on' : '') + (row.tooBig ? ' dim' : '') + '" tabindex="-1" aria-selected="' + (id === cat.detail) + '">'
      + '<td class="pinc"><button type="button" class="pinbox' + (pinned ? ' on' : '') + '" data-catpin="' + esc(id) + '"'
      + ' aria-label="' + (pinned ? 'Unpin' : 'Pin') + ' ' + esc(row.p.name) + '" aria-pressed="' + pinned + '"></button></td>'
      + cols.map(function (c) { return catCellHtml(row, c); }).join('') + '</tr>';
  }).join('');
}

function catTableHtml() {
  return '<div class="cat-tblwrap" id="catTblWrap" tabindex="0" aria-label="Product catalogue, scrolls">'
    + '<table class="cat-tbl ' + esc(cat.density) + '"><thead>' + catHeadHtml() + '</thead>'
    + '<tbody id="catBody">' + catBodyHtml() + '</tbody></table></div>';
}

function catCompareHtml() {
  var pinned = cat.pins.map(catRow).filter(Boolean);
  if (!pinned.length) return '<div class="cat-cmp-empty"><p>Pin two or more products to compare them.</p></div>';
  var best = catBest(pinned);
  var tier = catTierId();
  var cell = function (row, key, text, cls) {
    return '<td class="' + (cls || '') + (best[key] === row.p.productId ? ' best' : '') + '">' + text + '</td>';
  };
  var section = function (label) { return '<tr class="sec"><td colspan="' + (pinned.length + 1) + '">' + label + '</td></tr>'; };
  var line = function (label, fn, key) {
    return '<tr><td>' + label + '</td>' + pinned.map(function (row) { return cell(row, key, fn(row)); }).join('') + '</tr>';
  };
  return '<div class="cat-cmpwrap"><table class="cat-cmp"><thead><tr><th></th>'
    + pinned.map(function (row) {
        return '<th><span class="t">' + esc(row.p.ticker !== '—' ? row.p.ticker : row.p.name) + '</span><small>' + esc(row.p.name) + '</small>'
          + '<button type="button" class="cat-unpin" data-catunpin="' + esc(row.p.productId) + '" aria-label="Unpin ' + esc(row.p.name) + '">×</button></th>';
      }).join('')
    + '</tr></thead><tbody>'
    + section('Terms')
    + line('Asset class', function (r) { return esc(r.p.assetClass); })
    + line('Vehicle · style', function (r) { return esc(r.p.vehicle) + ' · ' + esc(r.p.style); })
    + line('Source', function (r) { return esc(r.p.source); })
    + line('Exposure', function (r) { return esc(r.p.exposureCurrency); })
    + line('Liquidity', function (r) { return '<span class="liq' + liqClass(r.p.liquidity) + '">' + esc(r.p.liquidity) + '</span>'; })
    + line('Minimum investment', function (r) { return esc(catMoney(r.p.minimumInvestment)); })
    + section('Cost · % p.a.')
    + line('Product cost', function (r) { return catFigure(r.p.productCost, 2); }, 'productCost')
    + line('Fee group', function (r) { return esc(r.p.feeGroup); })
    + line('Management' + (tier ? ' @ ' + esc(tier) : ''), function (r) { return catFigure(r.mgmt, 2); }, 'mgmt')
    + line('All-in', function (r) { return '<b>' + catFigure(r.allIn, 2) + '</b>'; }, 'allIn')
    + section('Yield · % p.a.')
    + line('Distribution yield', function (r) { return catFigure(r.p.distributionYield, 2); }, 'distributionYield')
    + line('<em>Net of all-in</em>', function (r) { return r.net === null ? '—' : (r.net >= 0 ? '+' : '−') + Math.abs(r.net).toFixed(2); }, 'net')
    + section('Placement')
    + line('Offered in', function (r) { return r.books.length ? r.books.length + ' book' + (r.books.length === 1 ? '' : 's') : '—'; })
    + line('Used in sleeves', function (r) { return String(r.used); })
    + '</tbody></table>'
    + '<p class="cat-cmp-key">Lowest cost and highest yield marked per row. Management and all-in price at the open proposal’s schedule, tier and level; without one they read as product cost.</p>'
    + '</div>';
}

function catDetailHtml() {
  var row = cat.detail ? catRow(cat.detail) : null;
  if (!row) return '';
  var p = row.p, uses = usedIn(p.productId);
  var list = uses.map(function (u) {
    return '<div class="r"><b>' + esc(u.name) + ' · ' + money2(u.weight * 100) + '%</b>'
      + '<small>' + esc(u.variant) + ' · ' + esc(u.category) + '</small>'
      + '<button type="button" class="cat-link" data-catopen="' + u.id + '">Open in repository →</button></div>';
  }).join('') || '<div class="r none">Not in any sleeve yet.</div>';
  return '<div class="cat-detail" role="region" aria-label="' + esc(p.name) + '">'
    + '<button type="button" class="dlg-close" data-catdetailclose aria-label="Close">×</button>'
    + '<span class="eyeb">Product</span>'
    + '<h4>' + esc(p.name) + '</h4>'
    + '<span class="tk">' + esc(p.ticker) + ' · ' + esc(p.assetClass) + ' · ' + esc(p.productId) + '</span>'
    + '<div class="cat-mg">'
    + '<div class="m"><small>Cost</small><b>' + catFigure(p.productCost, 2) + '</b></div>'
    + '<div class="m"><small>All-in' + (catTierId() ? ' @ ' + esc(catTierId()) : '') + '</small><b>' + catFigure(row.allIn, 2) + '</b></div>'
    + '<div class="m"><small>Yield</small><b>' + catFigure(p.distributionYield, 2) + '</b></div>'
    + '</div>'
    + '<dl class="cat-dl">'
    + '<dt>Vehicle</dt><dd>' + esc(p.vehicle) + ' · ' + esc(p.style) + '</dd><dt>Source</dt><dd>' + esc(p.source) + '</dd>'
    + '<dt>Exposure</dt><dd>' + esc(p.exposureCurrency) + '</dd><dt>Liquidity</dt><dd><span class="liq' + liqClass(p.liquidity) + '">' + esc(p.liquidity) + '</span></dd>'
    + '<dt>Minimum</dt><dd>' + esc(catMoney(p.minimumInvestment)) + (row.tooBig ? ' <span class="flag">above mandate</span>' : '') + '</dd>'
    + '<dt>Fee group</dt><dd>' + esc(p.feeGroup) + '</dd>'
    + '<dt>Offered in</dt><dd>' + (row.books.length ? esc(row.books.join(', ')) : '—') + '</dd></dl>'
    + '<div class="cat-usedin"><div class="h">Used in · ' + uses.length + ' sleeve' + (uses.length === 1 ? '' : 's') + '</div>' + list + '</div>'
    + '<div class="acts"><button type="button" class="btn" data-catpin="' + esc(p.productId) + '">' + (cat.pins.indexOf(p.productId) !== -1 ? 'Unpin' : 'Pin to compare') + '</button>'
    + '<button type="button" class="btn" data-catfee="' + esc(p.feeGroup) + '">Fee card · ' + esc(p.feeGroup) + '</button></div>'
    + '</div>';
}

function catOrphansHtml() {
  var orphans = repo.data.orphans || [];
  if (!orphans.length) return '';
  return '<div class="repo-notice cat-orphans" role="status">'
    + '<span>' + orphans.length + ' product' + (orphans.length === 1 ? '' : 's') + ' referenced by sleeves '
    + (orphans.length === 1 ? 'is' : 'are') + ' not in this catalogue; those sleeves are withheld from the pickers.</span>'
    + orphans.map(function (o) {
        return '<span class="cat-orphan"><b>' + esc(o.productId) + '</b> in '
          + o.sleeves.map(function (s) {
              return '<button type="button" class="cat-link" data-catopen="' + s.id + '">' + esc(s.name) + ' (' + esc(s.variant) + ')</button>';
            }).join(', ') + '</span>';
      }).join('')
    + '</div>';
}

function catTrayHtml() {
  var n = cat.pins.length;
  return '<div class="cat-tray" id="catTray">'
    + '<span class="ttl">Pinned</span>'
    + (n ? '<span class="pins">' + cat.pins.map(function (id) {
        var row = catRow(id); if (!row) return '';
        return '<button type="button" class="pn" data-catunpin="' + esc(id) + '" title="Unpin ' + esc(row.p.name) + '">'
          + esc(row.p.ticker !== '—' ? row.p.ticker : row.p.name.slice(0, 22)) + ' ×</button>';
      }).join('') + '</span>' : '<span class="none">Pin rows with the box, or <kbd>space</kbd> on a row</span>')
    + '<span class="cnt">' + n + ' of ' + catVisible().length + '</span>'
    + '<span class="spacer"></span>'
    + (n ? '<button type="button" class="btn btn-ghost" data-catclearpins>Clear</button>' : '')
    + '<button type="button" class="btn' + (cat.compare ? '' : ' btn-primary') + '" data-catcompare' + (n || cat.compare ? '' : ' disabled') + '>'
    + (cat.compare ? '← Back to list' : 'Compare · c') + '</button>'
    + '</div>';
}

function catalogueViewHtml() {
  return catToolbarHtml() + catOrphansHtml()
    + '<div class="cat-b">' + catFacetsHtml()
    + (cat.compare ? catCompareHtml() : catTableHtml())
    + catDetailHtml()
    + '</div>';
}

function catTogglePin(id) {
  var at = cat.pins.indexOf(id);
  if (at === -1) cat.pins.push(id); else cat.pins.splice(at, 1);
  if (!cat.pins.length) cat.compare = false;
}
function catFocusCursor() {
  var row = cat.cursor && document.querySelector('[data-catrow="' + cat.cursor.replace(/"/g, '\\"') + '"]');
  if (row && row.focus) row.focus();
}
function catMoveCursor(step) {
  var ids = catVisible().map(function (r) { return r.p.productId; });
  if (!ids.length) return;
  var at = ids.indexOf(cat.cursor);
  var next = at === -1 ? (step > 0 ? 0 : ids.length - 1) : Math.max(0, Math.min(ids.length - 1, at + step));
  cat.cursor = ids[next];
  if (cat.detail) cat.detail = cat.cursor;      /* an open panel follows the cursor */
  render(); catScrollCursorIntoView(); catFocusCursor();
}

/* ---- rendering: the dialog --------------------------------------------- */
function render() {
  var host = document.getElementById('repoDialog'); if (!host) return;
  if (!repo.open) {
    host.innerHTML = ''; host.hidden = true; App.setBackgroundInert(false); return;
  }
  host.hidden = false;
  App.setBackgroundInert(true);
  var focused = document.activeElement && document.activeElement.id;
  var d = repo.data;
  var onCatalogue = repo.view === 'catalogue';
  var onArchive = repo.view === 'archive', onActivity = repo.view === 'activity';
  var onRegister = repo.view === 'proposals';
  var onSleeves = !onCatalogue && !onArchive && !onActivity && !onRegister;

  var header = '<div class="repo-h"><h2 id="repoTitle" class="dlg-shout">Sleeve Repository</h2>'
    + '<div class="repo-seg" role="tablist" aria-label="View">'
    + '<button type="button" role="tab" data-repoview="sleeves" aria-selected="' + onSleeves + '">Sleeves</button>'
    + '<button type="button" role="tab" data-repoview="catalogue" aria-selected="' + onCatalogue + '">Catalogue</button>'
    + '<button type="button" role="tab" data-repoview="archive" aria-selected="' + onArchive + '">Archive'
    + (d && archivedSleeves().length ? '<span class="n">' + archivedSleeves().length + '</span>' : '') + '</button>'
    + '<button type="button" role="tab" data-repoview="activity" aria-selected="' + onActivity + '">Activity</button>'
    + '<button type="button" role="tab" data-repoview="proposals" aria-selected="' + onRegister + '">Proposals'
    + (d && d.register && d.register.proposals ? '<span class="n">' + d.register.proposals + '</span>' : '') + '</button>'
    + '</div>';
  if (d && onSleeves) {
    header += '<div class="repo-seg" role="tablist" aria-label="Implementation type">'
      + d.variants.map(function (v) {
          return '<button type="button" role="tab" data-repovariant="' + esc(v) + '"'
            + ' aria-selected="' + (v === repo.variant ? 'true' : 'false') + '">' + esc(v) + '</button>';
        }).join('') + '</div>';
  }
  if (d) {
    header += '<span class="repo-src">Catalogue · ' + d.catalogue.products + ' products · '
      + esc(d.catalogue.path.split('/').pop()) + ' · ' + esc(shortDate(d.catalogue.modified)) + '</span>';
  }
  header += '<button type="button" class="dlg-close" id="repoclose" data-repoclose aria-label="Close">×</button></div>';

  var body;
  if (!d) {
    body = '<div class="repo-b"><p class="repo-loading">' + (repo.error ? '' : 'Loading the repository…') + '</p></div>';
  } else if (onCatalogue) {
    body = catalogueViewHtml();
  } else if (onArchive) {
    body = archiveViewHtml();
  } else if (onActivity) {
    body = activityViewHtml();
  } else if (onRegister) {
    body = registerViewHtml();
  } else {
    body = sleevesViewHtml();
  }

  var leaving = '';
  if (repo.leaving) {
    leaving = '<div class="repo-notice" role="alertdialog" aria-live="assertive">'
      + '<span>You have unsaved changes to ' + esc(repo.draft && repo.draft.name || 'this sleeve') + '.</span>'
      + '<button type="button" class="btn" data-repokeep>Keep editing</button>'
      + '<button type="button" class="btn btn-danger" data-repodiscard>Discard changes</button></div>';
  }

  var footer;
  if (onArchive && d) {
    footer = arcFooterHtml();
  } else if (onActivity && d) {
    footer = actFooterHtml();
  } else if (onRegister && d) {
    footer = regFooterHtml();
  } else if (onCatalogue && d) {
    footer = '<div class="repo-f cat-f">' + catTrayHtml()
      + (repo.error ? '<span class="md-err" role="alert">' + esc(repo.error) + '</span>' : '')
      + '<span class="repo-src cat-src">' + esc(d.catalogue.path.replace(/^.*\/(productSource\/)/, '$1')) + ' · '
      + esc(shortDate(d.catalogue.modified)) + ' · read only</span>'
      + '</div>';
  } else {
    var onRemoved = false;
    var canSave = !onRemoved && !!repo.draft && repo.dirty && !draftProblems().length && !repo.saving;
    footer = '<div class="repo-f">'
      + (d ? '<button type="button" class="btn btn-create" data-repocreate'
          + (repo.saving ? ' disabled' : '') + '>+ Create sleeve</button>' : '')
      + (d ? '<span class="repo-src">Library · ' + esc(d.store.path.split('/').pop()) + ' · ' + d.store.sleeves
          + ' sleeves · ' + (d.store.revisions || 0) + ' versions on record'
          + (d.store.archived ? ' · ' + d.store.archived + ' archived' : '') + '</span>' : '')
      + '<span class="spacer"></span>'
      + (repo.error ? '<span class="md-err" role="alert">' + esc(repo.error) + '</span>' : '')
      + (!onRemoved && repo.draft && repo.draft.id && !isFixed(repo.category)
          ? (repo.confirmDelete
              ? '<span class="repo-src">It keeps its history and can be restored from the Archive.</span>'
                + '<button type="button" class="btn" data-repocanceldelete>Keep</button>'
                + '<button type="button" class="btn btn-danger" data-repodelete>Confirm archive</button>'
              : '<button type="button" class="btn btn-danger" data-repodelete' + (repo.saving ? ' disabled' : '') + '>Archive sleeve</button>')
          : '')
      + (onRemoved ? ''
          : '<button type="button" class="btn btn-primary" data-reposave' + (canSave ? '' : ' disabled') + '>'
            + (repo.saving ? 'Saving…' : (repo.draft && repo.draft.create ? 'Create sleeve' : 'Save sleeve')) + '</button>')
      + '</div>';
  }

  host.innerHTML = '<div class="scrim" data-reposcrim></div>'
    + '<div class="dialog repo' + (onCatalogue ? ' catalogue' : '') + (onArchive ? ' archive' : '') + (onActivity ? ' activity' : '') + (onRegister ? ' register' : '') + '" role="dialog" aria-modal="true" aria-labelledby="repoTitle">'
    + header + leaving + body + footer + '</div>';
  syncHash();

  if (focused) {
    var again = document.getElementById(focused);
    if (again && again.focus) {
      again.focus();
      if ((again.id === 'repoSearch' || again.id === 'catSearch' || again.id === 'arcSearch' || again.id === 'actSearch' || again.id === 'regSearch') && again.setSelectionRange && again.type !== 'search') {
        var end = again.value.length; again.setSelectionRange(end, end);
      }
    }
  }
}

function updateTotals() {
  var tot = document.getElementById('repoTotal'); if (tot) tot.innerHTML = totalHtml();
  var save = document.querySelector('[data-reposave]');
  if (save) save.disabled = !(repo.dirty && !draftProblems().length && !repo.saving);
  var probs = document.querySelector('.repo-problems');
  if (probs) probs.remove();
}
function updatePickerList() {
  var list = document.getElementById('repoPickerList'); if (!list) return;
  list.innerHTML = pickerListHtml();
  var box = document.getElementById('repoSearch');
  if (box) box.setAttribute('aria-activedescendant', 'repoOpt' + repo.picker.index);
}
/* typing in the catalogue's search redraws the rows, the facet counts, the
   count line and the tray - never the search box, so the caret stays put */
function updateCatalogue() {
  var body = document.getElementById('catBody'); if (body) body.innerHTML = catBodyHtml();
  var facets = document.getElementById('catFacets'); if (facets) facets.outerHTML = catFacetsHtml();
  var count = document.getElementById('catCount'); if (count) count.innerHTML = catCountText();
  var tray = document.getElementById('catTray'); if (tray) tray.outerHTML = catTrayHtml();
  syncHash();
}
function catScrollCursorIntoView() {
  var row = cat.cursor && document.querySelector('[data-catrow="' + cat.cursor.replace(/"/g, '\\"') + '"]');
  if (row && row.scrollIntoView) row.scrollIntoView({ block: 'nearest' });
}

/* ---- the entry points --------------------------------------------------- */
function renderEntryLinks() {
  var show = canAdmin();
  /* the whole bar, not the individual buttons: an empty bordered strip would
     be worse than no strip (D62) */
  var bar = document.getElementById('railadmin');
  if (bar) bar.hidden = !show;
  if (!repo.bootChecked && (typeof App.schemaReady !== 'function' || App.schemaReady())) {
    repo.bootChecked = true;
    if (show && window.location.hash.indexOf('#repository') === 0) openRepository(null, 'sleeves');
    if (show && window.location.hash.indexOf('#catalogue') === 0) {
      catFromHash(window.location.hash);
      openRepository(null, 'catalogue');
    }
    if (show && window.location.hash.indexOf('#archive') === 0) {
      arcFromHash(window.location.hash);
      openRepository(null, 'archive');
    }
    if (show && window.location.hash.indexOf('#activity') === 0) {
      actFromHash(window.location.hash);
      openRepository(null, 'activity');
    }
    if (show && window.location.hash.indexOf('#proposals') === 0) {
      regFromHash(window.location.hash);
      openRepository(null, 'proposals');
    }
  }
}

/* The fee card can be opened from the catalogue's drawer, above this dialog.
   Closing it releases the page's inert state, which this dialog still needs;
   watching the card's host puts it back. */
(function watchFeeDialog() {
  var feeHost = document.getElementById('feeDialog');
  if (!feeHost || typeof MutationObserver === 'undefined') return;
  new MutationObserver(function () {
    if (feeHost.hidden && repo.open) {
      App.setBackgroundInert(true);
      var back = document.querySelector('[data-catfee]'); if (back) back.focus();
    }
  }).observe(feeHost, { attributes: true, attributeFilter: ['hidden'] });
})();

/* ---- events ------------------------------------------------------------- */
document.addEventListener('click', function (e) {
  var t = e.target.closest ? e.target.closest('#repolink, #catlink') : null;
  if (t) { e.preventDefault(); openRepository(t, t.id.indexOf('cat') !== -1 ? 'catalogue' : 'sleeves'); return; }
  var host = document.getElementById('repoDialog');
  if (!host || !repo.open || !host.contains(e.target)) return;
  var el = e.target.closest ? e.target.closest(
    '[data-repoclose],[data-reposcrim],[data-repoview],[data-repovariant],[data-repocat],[data-reposleeve],'
    + '[data-reponew],[data-repoadd],[data-reporm],[data-repopick],[data-repochoose],[data-reposave],'
    + '[data-repodelete],[data-repocanceldelete],[data-repokeep],[data-repodiscard],'
    + '[data-catcols],[data-catshowall],[data-catdensity],[data-catclearall],[data-catsort],'
    + '[data-catpin],[data-catunpin],[data-catclearpins],[data-catcompare],[data-catdetailclose],'
    + '[data-catrow],[data-catopen],[data-catfee],'
    + '[data-repocreate],[data-repocopy],[data-reporemove],'
    + '[data-repohistory],[data-reporev],[data-reporevert],'
    + '[data-arcsort],[data-arcrow],[data-arcclear],[data-arcclearsel],[data-arcrestore],[data-arcrestoresel],[data-arcdetailclose],'
    + '[data-acttoggle],[data-actclear],[data-actmore],[data-actview],[data-actrestore],[data-actrange],'
    + '[data-regrow],[data-regclear],[data-regmore],[data-regpic],[data-regdetailclose]') : null;
  if (!el) {
    /* a click anywhere else closes an open picker, chip menu or context menu */
    if (repo.picker && !e.target.closest('.repo-menu, #repoSearch')) closePicker();
    if (cat.openChip && !e.target.closest('.cat-chipwrap')) { cat.openChip = null; render(); }
    if (repo.menu && !e.target.closest('.repo-ctx')) { repo.menu = null; render(); }
    /* a click on the table's empty space, or anywhere outside the panel, closes the panel */
    if (cat.detail && repo.view === 'catalogue' && !e.target.closest('.cat-detail')) { cat.detail = null; render(); }
    return;
  }
  var ds = el.dataset;
  if (ds.repoclose !== undefined || ds.reposcrim !== undefined) { closeRepository(false); return; }
  if (ds.repoview !== undefined) { switchView(ds.repoview); return; }
  if (ds.repovariant !== undefined) { goTo({ variant: ds.repovariant }); return; }
  if (ds.repocat !== undefined) { goTo({ category: ds.repocat }); return; }
  if (ds.reposleeve !== undefined) { selectSleeve(parseInt(ds.reposleeve, 10)); return; }
  if (ds.reponew !== undefined) { goTo({ fresh: true }); return; }
  if (ds.repocreate !== undefined) { goTo({ fresh: true, create: true }); return; }
  if (ds.repocopy !== undefined) { copyToVariant(repo.menu && repo.menu.id, ds.repocopy); return; }
  if (ds.reporemove !== undefined) { removeFromCategory(parseInt(ds.reporemove, 10)); return; }
  /* the record (D65) */
  if (ds.repohistory !== undefined) { toggleHistory(parseInt(ds.repohistory, 10)); return; }
  if (ds.reporev !== undefined) {
    var n = parseInt(ds.reporev, 10);
    repo.openRevision = repo.openRevision === n ? null : n;
    render(); return;
  }
  if (ds.reporevert !== undefined) { revertTo(parseInt(ds.reporevert, 10)); return; }
  /* the archive (D66) */
  if (ds.arcsort !== undefined) {
    if (arc.sort.key === ds.arcsort) arc.sort.dir = arc.sort.dir === 'asc' ? 'desc' : 'asc';
    else arc.sort = { key: ds.arcsort, dir: ds.arcsort === 'archivedAt' || ds.arcsort === 'revisions' ? 'desc' : 'asc' };
    updateArchive(); return;
  }
  if (ds.arcrow !== undefined) {
    if (e.target.closest('input')) return;                 /* the tick box has its own handler */
    var id = parseInt(ds.arcrow, 10);
    var same = arc.detail === id;
    arc.detail = same ? null : id;
    repo.historyOpen = false; repo.history = null; repo.openRevision = null;
    render(); return;
  }
  if (ds.arcdetailclose !== undefined) { arc.detail = null; repo.historyOpen = false; render(); return; }
  if (ds.arcclear !== undefined) { arc.query = ''; arc.filters = {}; render(); return; }
  if (ds.arcclearsel !== undefined) { arc.selected = []; updateArchive(); return; }
  if (ds.arcrestore !== undefined) { restoreArchived([parseInt(ds.arcrestore, 10)]); return; }
  if (ds.arcrestoresel !== undefined) { restoreArchived(arc.selected.slice()); return; }
  /* the feed (D66) */
  if (ds.acttoggle !== undefined) {
    var at = act.actions.indexOf(ds.acttoggle);
    if (at === -1) act.actions.push(ds.acttoggle); else act.actions.splice(at, 1);
    loadActivity(true); return;
  }
  if (ds.actrange !== undefined) { act.range = ds.actrange; loadActivity(true); return; }
  if (ds.actclear !== undefined) { act.query = ''; act.actions = []; act.actor = ''; act.variant = ''; act.range = '30d'; loadActivity(true); return; }
  if (ds.actmore !== undefined) { loadActivity(false); return; }
  if (ds.actview !== undefined) { openRevisionFrom(parseInt(ds.actview, 10), parseInt(ds.actrev, 10), ds.actarchived === '1'); return; }
  if (ds.actrestore !== undefined) { restoreArchived([parseInt(ds.actrestore, 10)]); return; }
  /* the register (D69) */
  if (ds.regrow !== undefined) {
    if (e.target.closest('a')) return;
    var same = reg.detail === ds.regrow;
    reg.detail = same ? null : ds.regrow;
    if (!same) loadProposal(ds.regrow); else render();
    return;
  }
  if (ds.regdetailclose !== undefined) { reg.detail = null; render(); return; }
  if (ds.regpic !== undefined) { reg.picture = ds.regpic === 'allocation' ? 'allocation' : 'implemented'; render(); return; }
  if (ds.regclear !== undefined) { reg.query = ''; reg.exportedBy = ''; reg.primaryPwa = ''; reg.currency = ''; reg.variant = ''; reg.range = '90d'; loadRegister(true); return; }
  if (ds.regmore !== undefined) { loadRegister(false); return; }
  if (ds.repoadd !== undefined) { addRow(); return; }
  if (ds.reporm !== undefined) { removeRow(parseInt(ds.reporm, 10)); return; }
  if (ds.repopick !== undefined) { openPicker(parseInt(ds.repopick, 10)); return; }
  if (ds.repochoose !== undefined) { choose(ds.repochoose); return; }
  if (ds.reposave !== undefined) { saveDraft(); return; }
  if (ds.repodelete !== undefined) { deleteCurrent(); return; }
  if (ds.repocanceldelete !== undefined) { repo.confirmDelete = false; render(); return; }
  if (ds.repokeep !== undefined) { resolveLeaving(false); return; }
  if (ds.repodiscard !== undefined) { resolveLeaving(true); return; }
  /* the catalogue (D63) */
  if (ds.catcols !== undefined) { cat.openChip = cat.openChip === 'cols' ? null : 'cols'; render(); return; }
  if (ds.catshowall !== undefined) { cat.hidden = []; render(); return; }
  if (ds.catdensity !== undefined) { cat.density = ds.catdensity === 'comfortable' ? 'comfortable' : 'dense'; render(); return; }
  if (ds.catclearall !== undefined) { cat.query = ''; cat.filters = {}; cat.openChip = null; render(); return; }
  if (ds.catsort !== undefined) {
    /* ascending, then descending, then back to the delivery's own order */
    if (!cat.sort || cat.sort.key !== ds.catsort) cat.sort = { key: ds.catsort, dir: 'asc' };
    else if (cat.sort.dir === 'asc') cat.sort.dir = 'desc';
    else cat.sort = null;
    render(); return;
  }
  if (ds.catpin !== undefined) { catTogglePin(ds.catpin); render(); return; }
  if (ds.catunpin !== undefined) {
    cat.pins = cat.pins.filter(function (id) { return id !== ds.catunpin; });
    if (!cat.pins.length) cat.compare = false;
    render(); return;
  }
  if (ds.catclearpins !== undefined) { cat.pins = []; cat.compare = false; render(); return; }
  if (ds.catcompare !== undefined) { if (cat.pins.length || cat.compare) { cat.compare = !cat.compare; cat.detail = null; render(); } return; }
  if (ds.catdetailclose !== undefined) { cat.detail = null; render(); catFocusCursor(); return; }
  if (ds.catrow !== undefined) {
    cat.cursor = ds.catrow;
    cat.detail = cat.detail === ds.catrow ? null : ds.catrow;
    render(); return;
  }
  if (ds.catfee !== undefined) { if (App.openFeePanel) App.openFeePanel(ds.catfee); return; }
});

/* a chip's boxes: change, not click, so the state read is the state the
   box is in after the browser has toggled it, however the click arrived */
document.addEventListener('change', function (e) {
  if (!repo.open || !e.target.dataset) return;
  var ds = e.target.dataset;
  if (ds.catfacet !== undefined && ds.catval !== undefined) {
    var chosen = (cat.filters[ds.catfacet] || []).slice();
    var at = chosen.indexOf(ds.catval);
    if (e.target.checked && at === -1) chosen.push(ds.catval);
    if (!e.target.checked && at !== -1) chosen.splice(at, 1);
    cat.filters[ds.catfacet] = chosen;
    updateCatalogue(); return;
  }
  if (ds.catcol !== undefined) {
    var hidden = cat.hidden.filter(function (k) { return k !== ds.catcol; });
    if (!e.target.checked) hidden.push(ds.catcol);
    cat.hidden = hidden; render(); return;
  }
  /* the archive's filters and tick boxes (D66) */
  if (ds.arcfilter !== undefined) { arc.filters[ds.arcfilter] = e.target.value; render(); return; }
  if (ds.arcsel !== undefined) {
    var sid = parseInt(ds.arcsel, 10);
    arc.selected = arc.selected.filter(function (x) { return x !== sid; });
    if (e.target.checked) arc.selected.push(sid);
    updateArchive(); return;
  }
  if (ds.arcselall !== undefined) {
    var shown = arcVisible().map(function (r) { return r.s.id; });
    arc.selected = e.target.checked
      ? arc.selected.concat(shown.filter(function (id) { return arc.selected.indexOf(id) === -1; }))
      : arc.selected.filter(function (id) { return shown.indexOf(id) === -1; });
    updateArchive(); return;
  }
  /* the feed's selects (D66) */
  if (ds.actwho !== undefined) { act.actor = e.target.value; loadActivity(true); return; }
  if (ds.actbook !== undefined) { act.variant = e.target.value; loadActivity(true); return; }
  if (ds.actrange !== undefined) { act.range = e.target.value; loadActivity(true); return; }
  /* the register's selects (D69) */
  if (ds.regwho !== undefined) { reg.exportedBy = e.target.value; loadRegister(true); return; }
  if (ds.regpwa !== undefined) { reg.primaryPwa = e.target.value; loadRegister(true); return; }
  if (ds.regccy !== undefined) { reg.currency = e.target.value; loadRegister(true); return; }
  if (ds.regbook !== undefined) { reg.variant = e.target.value; loadRegister(true); return; }
  if (ds.regrange !== undefined) { reg.range = e.target.value; loadRegister(true); return; }
});

document.addEventListener('contextmenu', function (e) {
  if (!repo.open || repo.view !== 'sleeves') return;
  var row = e.target.closest ? e.target.closest('[data-reposleeve]') : null;
  if (!row) return;
  e.preventDefault();
  var id = parseInt(row.dataset.reposleeve, 10);
  var host = document.getElementById('repoDialog');
  var box = host.querySelector('.dialog.repo').getBoundingClientRect();
  /* positioned inside the dialog, and kept off its edges so the menu is
     never half outside the thing it belongs to */
  repo.menu = { id: id,
                x: Math.min(e.clientX - box.left, box.width - 250),
                y: Math.min(e.clientY - box.top, box.height - 210) };
  if (repo.dirty) { repo.menu = null; goTo({ to: id }); return; }
  loadDraftKeepingMenu(id);
  render();
});

function loadDraftKeepingMenu(id) {
  var menu = repo.menu;
  loadDraft(id);
  repo.menu = menu;
}

document.addEventListener('change', function (e) {
  if (!repo.open || !repo.draft) return;
  var el = e.target;
  if (el.id === 'repoCategory') {
    repo.draft.category = el.value;
    repo.category = el.value;          /* the list beside it follows the choice */
    repo.draft.variants = (repo.draft.variants || []).filter(function (v) {
      return !(isFixed(el.value) && sleevesIn(v, el.value).length >= 1);
    });
    repo.dirty = true; render(); return;
  }
  if (el.dataset && el.dataset.repovar !== undefined) {
    var picked = (repo.draft.variants || []).slice();
    var at = picked.indexOf(el.dataset.repovar);
    if (el.checked && at === -1) picked.push(el.dataset.repovar);
    if (!el.checked && at !== -1) picked.splice(at, 1);
    repo.draft.variants = picked; repo.dirty = true; render();
  }
});

document.addEventListener('input', function (e) {
  if (!repo.open) return;
  var el = e.target;
  if (el.id === 'catSearch') { cat.query = el.value; updateCatalogue(); return; }
  if (el.id === 'arcSearch') { arc.query = el.value; updateArchive(); return; }
  if (el.id === 'actSearch') { act.query = el.value; syncHash(); actRefresh(); return; }
  if (el.id === 'regSearch') { reg.query = el.value; syncHash(); regRefresh(); return; }
  if (!repo.draft) return;
  if (el.id === 'repoName') { repo.draft.name = el.value; repo.dirty = true; updateTotals(); return; }
  if (el.id === 'repoNote') { repo.draft.note = el.value; repo.dirty = true; updateTotals(); return; }
  if (el.dataset && el.dataset.repoweight !== undefined) {
    var i = parseInt(el.dataset.repoweight, 10);
    var v = parseFloat(el.value.replace(/[%,\s]/g, ''));
    repo.draft.products[i].weightPct = isFinite(v) ? v : NaN;
    repo.dirty = true; updateTotals(); return;
  }
  if (el.id === 'repoSearch' && repo.picker) {
    repo.picker.query = el.value; repo.picker.index = 0; updatePickerList();
  }
});

/* Capture phase, on purpose: the fee card's own Escape handler was
   registered first and hides the card synchronously, and a bubbling handler
   here would then find no card open and close this dialog as well. Seen
   first, the card's presence is real and the key is left to it. */
document.addEventListener('keydown', function (e) {
  if (!repo.open) return;
  if (repo.picker && e.target.id === 'repoSearch') {
    var hits = pickerMatches();
    if (e.key === 'ArrowDown') { e.preventDefault(); repo.picker.index = Math.min(repo.picker.index + 1, Math.min(hits.length, 40) - 1); updatePickerList(); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); repo.picker.index = Math.max(repo.picker.index - 1, 0); updatePickerList(); return; }
    if (e.key === 'Enter') { e.preventDefault(); if (hits[repo.picker.index]) choose(hits[repo.picker.index].productId); return; }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closePicker(); return; }
    return;
  }
  if (repo.view === 'archive' || repo.view === 'activity' || repo.view === 'proposals') {
    var box2 = document.getElementById(repo.view === 'archive' ? 'arcSearch' : repo.view === 'activity' ? 'actSearch' : 'regSearch');
    var inField2 = e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA');
    if (e.key === '/' && !inField2) { e.preventDefault(); if (box2) { box2.focus(); box2.select(); } return; }
    if (e.key === 'Escape') {
      e.preventDefault();
      if (box2 && e.target === box2) {
        if (box2.value) {
          box2.value = '';
          if (repo.view === 'archive') { arc.query = ''; updateArchive(); }
          else if (repo.view === 'activity') { act.query = ''; loadActivity(true); }
          else { reg.query = ''; loadRegister(true); }
        }
        else box2.blur();
        return;
      }
      if (repo.view === 'archive' && arc.detail != null) { arc.detail = null; repo.historyOpen = false; render(); return; }
      if (repo.view === 'proposals' && reg.detail) { reg.detail = null; render(); return; }
      closeRepository(false); return;
    }
    return;
  }
  if (repo.view === 'catalogue') {
    /* the fee card, if open above this dialog, takes every key first */
    var feeUp = document.getElementById('feeDialog');
    if (feeUp && !feeUp.hidden) return;
    var inField = e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA');
    if (e.key === '/' && !inField) {
      e.preventDefault(); var box = document.getElementById('catSearch'); if (box) { box.focus(); box.select(); } return;
    }
    if (e.key === 'Escape' && e.target.id === 'catSearch') {
      e.preventDefault(); e.stopPropagation();
      if (cat.query) { cat.query = ''; e.target.value = ''; updateCatalogue(); } else { e.target.blur(); catFocusCursor(); }
      return;
    }
    if (!inField) {
      if (e.key === 'ArrowDown') { e.preventDefault(); catMoveCursor(1); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); catMoveCursor(-1); return; }
      if (e.key === ' ' && cat.cursor) { e.preventDefault(); catTogglePin(cat.cursor); render(); catFocusCursor(); return; }
      if (e.key === 'Enter' && cat.cursor && !cat.compare) { e.preventDefault(); cat.detail = cat.detail === cat.cursor ? null : cat.cursor; render(); catFocusCursor(); return; }
      if ((e.key === 'c' || e.key === 'C') && (cat.pins.length || cat.compare)) { e.preventDefault(); cat.compare = !cat.compare; cat.detail = null; render(); return; }
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      if (cat.detail) { cat.detail = null; render(); catFocusCursor(); return; }
      if (cat.compare) { cat.compare = false; render(); return; }
      if (cat.openChip) { cat.openChip = null; render(); return; }
      closeRepository(false); return;
    }
    return;
  }
  if (e.key === 'Escape') {
    /* the fee card, if open above this dialog, takes the key first */
    var fee = document.getElementById('feeDialog');
    if (fee && !fee.hidden) return;
    e.preventDefault();
    if (repo.menu) { repo.menu = null; render(); return; }
    if (cat.openChip) { cat.openChip = null; render(); return; }
    if (repo.leaving) { resolveLeaving(false); return; }
    if (repo.confirmDelete) { repo.confirmDelete = false; render(); return; }
    closeRepository(false);
    return;
  }
  if (e.key === 'Enter' && e.target.id === 'repoName') { e.preventDefault(); saveDraft(); }
}, true);

App.addRenderer(renderEntryLinks);
App.openRepository = openRepository;
})();


// Bootstrap. The script tag sits at the end of <body>, so the DOM is parsed by
// the time this runs; the readyState guard covers a deferred load anyway.
// App.boot() paints the landing at once, fetches the schema, and rehydrates
// from a ?scenario= id when one is present (spec 11.1, 11.8).
(function () {
    function boot() { App.boot(); }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
