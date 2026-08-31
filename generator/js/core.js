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
  sleeves: {},                      /* category -> sleeve name (pickable categories only) */
  sleeveLib: {},                    /* category -> {status, sleeves, error} */
  exporting: { status: 'idle', error: null },
  basisDraft: null,                 /* pending basis change awaiting confirmation */
  baseDraft: { allocation: '', excludeRE: false, excludeTAA: false, riskLevel: '' },
  rebuilding: false
};
var draft = null;                   /* mandate dialog working copy */
var dlgOpener = null;
var seqCounter = 0;
var extras = [];
var picker = null;

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

/* Derived naming (the template is hardcodable; the values are not - spec 4.3). */
function headerName(k) {
  var suffix = '';
  if (k.excludeRE && reAllowed(k.allocation)) suffix += ' ex RE';
  if (k.excludeTAA) suffix += ' ex TAA';
  return k.allocation + ' ' + k.riskLevel + suffix;
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
  return q;
}

async function fetchSchema() {
  state.schemaStatus = 'loading';
  refresh();
  try {
    state.schema = await apiFetch('/scenario/schema' + schemaQuery());
    state.schemaStatus = 'ready';
    state.schemaError = null;
    pruneUnavailableColumns();
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
  apiFetch('/scenario/' + encodeURIComponent(state.scenarioId) + '/portfolio', {
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
    announce('polite', (col.data.header || headerName(col.key)) + ' ready.');
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
  var entry = state.sleeveLib[category];
  if (entry) {
    if (entry.status === 'ready' && onReady) onReady(entry);
    return;                       /* ready, loading OR error: nothing to start */
  }
  state.sleeveLib[category] = { status: 'loading', sleeves: [], error: null };
  apiFetch('/scenario/sleeves?category=' + encodeURIComponent(category)
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
  var html = '<div class="tier-h"><h3>Scenario basis</h3></div>'
    + '<div class="basis">'
    + '<div class="field"><label for="ccy">Currency</label><select id="ccy"' + disabled + '>'
    + opt('options.currencies', []).map(function (c) {
        return '<option' + (c === showing.currency ? ' selected' : '') + '>' + esc(c) + '</option>';
      }).join('')
    + '</select></div>'
    + '<div class="field"><label for="hedge">Hedging</label><select id="hedge"' + disabled + '>'
    + opt('options.hedgingPolicies', []).map(function (c) {
        return '<option' + (c === showing.hedging ? ' selected' : '') + '>' + esc(c) + '</option>';
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
  var exTAA = key ? key.excludeTAA : pending.excludeTAA;
  var risk = key ? key.riskLevel : pending.riskLevel;
  var disabled = !schemaReady() || !canEdit() ? ' disabled' : '';
  var ring = (state.phase === 'workspace' && !base) ? ' tier-ring' : '';

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
          + esc(r) + (ok ? '' : ' — unavailable') + '</option>';
      }).join('');

  el.className = 'tier' + ring;
  el.innerHTML = '<div class="tier-h"><h3>Base portfolio</h3>'
    + '<span class="tier-count">Column 1</span></div>'
    + '<div class="basis" style="grid-template-columns:1fr">'
    + '<div class="field"><label for="bpa">Allocation</label><select id="bpa"' + disabled + '>'
    + allocationOptions + '</select></div>'
    + '<div class="chk"><input type="checkbox" id="bpre"'
    + ((allocation && !canRE) || exRE ? ' checked' : '')
    + ((allocation && canRE && canEdit()) ? '' : ' disabled')
    + ((allocation && !canRE) ? ' aria-describedby="bprenote"' : '') + '>'
    + '<label for="bpre">Exclude Real Estate</label></div>'
    + ((allocation && !canRE)
        ? '<p class="chk-note" id="bprenote">Not available — ' + esc(allocation)
          + ' holds no real estate.</p>' : '')
    + '<div class="chk"><input type="checkbox" id="bptaa"'
    + (exTAA ? ' checked' : '') + (allocation && canEdit() ? '' : ' disabled') + '>'
    + '<label for="bptaa">Exclude Tactical Asset Allocation</label></div>'
    + '<p class="chk-note">TAA is included in every allocation by default.</p>'
    + '<div class="field"><label for="bpr">Risk level</label><select id="bpr"'
    + (allocation && canEdit() && schemaReady() ? '' : ' disabled') + '>'
    + riskOptions + '</select></div></div>'
    + '<p class="field-note" style="margin-top:8px">Changing the base rebuilds column one. '
    + 'A comparison that duplicates it is dropped.</p>';
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

function lookupStatus() {
  if (!state.columns.length) return null;
  var failed = state.columns.filter(function (c) { return c.status === 'error'; }).length;
  var loading = state.columns.filter(function (c) { return c.status === 'loading'; }).length;
  if (failed) return { cls: 'b-breach', text: failed + ' column' + (failed === 1 ? '' : 's') + ' failed' };
  if (state.rebuilding) return { cls: 'b-bind', text: 'Rebuilding ' + state.columns.length + ' portfolios…' };
  if (loading) return { cls: 'b-bind', text: 'Resolving…' };
  return { cls: 'b-ok', text: 'Lookup matched' };
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
  pieces.push('<span class="bdg b-bind">' + state.columns.length + ' of '
    + opt('rules.maxPortfolios', 4) + ' portfolios</span>');
  el.hidden = false;
  el.innerHTML = pieces.join('\n');
}

/* ---- table scaffolding -------------------------------------------------- */
function readyColumns() {
  return state.columns.filter(function (c) { return c.status === 'ready'; });
}

/* Union of categories over ready columns, in the schema's universe order,
   then any stragglers in first-seen order. Assets union per category keeps
   first-seen order (spec 2.2: rows come from the weight source). */
function unionRows() {
  var order = opt('categories', []);
  var ready = readyColumns();
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
  if (col.status === 'loading') return col.skel ? '<span class="skel"></span>' : '';
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
  if (col.status === 'loading') return col.skel ? '<span class="skel"></span>' : '';
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
    + esc(label) + extra + remove + '</th>';
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
    + '<th scope="col" class="rowhead">Asset</th>';
  state.columns.forEach(function (col, i) { html += columnHeadCell(col, i, 'col'); });
  if (plus) {
    html += '<th scope="col" class="num addcol"><button type="button" id="plusbtn" '
      + 'class="plus" aria-label="Add a comparison portfolio">+</button></th>';
  }
  html += '</tr></thead><tbody>';

  function row(cls, label, cellFn) {
    var r = '<tr class="' + cls + '"><th scope="row">' + esc(label) + '</th>'
      + state.columns.map(function (col) {
          return '<td class="num">' + cellFn(col) + '</td>';
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
      });
      category.assets.forEach(function (assetName, si) {
        html += row('asset' + (si % 2 ? ' alt' : ''), assetName, function (col) {
          return cellFor(col, category.name, assetName);
        });
      });
    });
  }
  html += row('total', 'Total', function (col) {
    if (col.status === 'loading') return col.skel ? '<span class="skel"></span>' : '';
    if (col.status === 'error') return '—';
    return '100.0%';
  });
  METRIC_ROWS.forEach(function (m) {
    html += row('metric', m[0], function (col) { return metricCell(col, m[1]); });
  });
  el.innerHTML = html + '</tbody>';
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

  /* The wrapper shrink-wraps the table, so asking it how much room there is
     would just measure the table we are about to size. Stretch it for the
     measurement, then hand it back to the stylesheet. */
  var wrapWidth = wrap.style.width;
  wrap.style.width = '100%';
  var available = wrap.clientWidth;
  wrap.style.width = wrapWidth;
  if (available <= 0) return;                    /* hidden: size on the next pass */

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
  if (!counted) { table.style.tableLayout = ''; table.style.width = ''; return; }

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

  table.style.setProperty('--fixed-c1', c1 + 'px');
  table.style.setProperty('--fixed-col', each + 'px');
  table.style.tableLayout = 'fixed';
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
  sizeComparisonTables();
}

/* Sizing runs after the renderers, because a table inside a step that is
   still hidden measures zero. Re-runs on resize, since the equal share
   depends on how much room the document has. */
function sizeComparisonTables() {
  var alloc = document.getElementById('alloc');
  var risk = document.getElementById('risk');
  if (alloc && alloc.rows.length) sizeFixedColumns(alloc, 1, 'thead th.addcol');
  if (risk && risk.rows.length) sizeFixedColumns(risk, 2, null);
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
    + '<th scope="col" class="rowhead">Measure</th>';
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
  function spanRow(cls, label, cellFn) {
    return '<tr class="' + cls + '"><th scope="row">' + esc(label) + '</th>'
      + state.columns.map(function (col) {
          return '<td class="num span2" colspan="2">' + cellFn(col) + '</td>';
        }).join('') + '</tr>';
  }
  function pairRow(cls, label, pairFn) {
    return '<tr class="' + cls + '"><th scope="row">' + esc(label) + '</th>'
      + state.columns.map(function (col) {
          if (col.status === 'loading') {
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
    });
  });
  METRIC_ROWS.forEach(function (m) {
    html += spanRow('metric', m[0], function (col) { return metricCell(col, m[1]); });
  });

  /* stress and premia rows are data-driven: the union of labels over ready
     columns, in first-seen order (spec 9.2) */
  function labelUnion(field) {
    var labels = [], seen = {};
    readyColumns().forEach(function (col) {
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
  readyColumns().forEach(function (col) {
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
  el.innerHTML = html + '</tbody>';
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
  var ready = readyColumns();
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
    var W = 720, rowH = 26, gap = 16, padL = 148, padR = 16, top = 8;
    var n = ready.length, H = top + n * rowH + (n - 1) * gap + 26;
    var barW = W - padL - padR;
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Allocation by '
      + 'category for ' + n + ' portfolio' + (n > 1 ? 's' : '')
      + '. The allocation table below carries the exact values.">';
    [0, 25, 50, 75, 100].forEach(function (t) {
      var x = padL + barW * t / 100;
      svg += '<line class="gl" x1="' + x + '" y1="' + top + '" x2="' + x + '" y2="'
        + (top + n * rowH + (n - 1) * gap) + '"/>'
        + '<text class="tick" x="' + x + '" y="' + (H - 8) + '" text-anchor="middle">' + t + '%</text>';
    });
    ready.forEach(function (col, i) {
      var y = top + i * (rowH + gap), x = padL;
      var label = col.data.header;
      svg += '<text class="plbl" x="' + (padL - 12) + '" y="' + (y + rowH / 2 + 4)
        + '" text-anchor="end">' + esc(label.length > 22 ? label.slice(0, 21) + '…' : label) + '</text>';
      col.data.categories.forEach(function (category) {
        var v = category.weightPct;
        if (!isFinite(v) || v < 0.05) return;
        var w = barW * v / 100;
        svg += '<rect class="seg" x="' + x + '" y="' + y + '" width="' + Math.max(0.5, w)
          + '" height="' + rowH + '" rx="' + (w > 6 ? 3 : 0) + '" fill="' + catColor(category.name) + '"'
          + ' data-tip="' + esc(col.data.header + ' · ' + category.name) + '|'
          + v.toFixed(1) + '%"></rect>';
        if (w > 46) {
          svg += '<text class="lbl" x="' + (x + w / 2) + '" y="' + (y + rowH / 2 + 4)
            + '" text-anchor="middle">' + v.toFixed(0) + '%</text>';
        }
        x += w;
      });
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
        + ' data-tip="' + esc(col.data.header) + '|vol '
        + col.data.metrics.volatilityPct.toFixed(2) + '% · return '
        + col.data.metrics.estimatedReturnPct.toFixed(2) + '%"></circle>';
      var above = (y > pT + 28);
      var label = col.data.header;
      svg2 += '<text class="plbl" x="' + x + '" y="' + (above ? y - 13 : y + 20)
        + '" text-anchor="middle">' + esc(label.length > 20 ? label.slice(0, 19) + '…' : label) + '</text>';
    });
    rr.innerHTML = svg2 + '</svg>';
  }
}

function renderFooter() {
  var el = document.getElementById('doc-foot'); if (!el) return;
  var info = opt('dataInfo', null);
  if (!info || state.phase !== 'workspace') { el.hidden = true; return; }
  el.hidden = false;
  el.textContent = 'Data: ' + (info.source || '')
    + (info.dataversion ? ' · ' + info.dataversion : '')
    + (info.asOf ? ' · as of ' + info.asOf : '')
    + (info.adapter ? ' · adapter: ' + info.adapter : '');
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
    + '<h2 id="dlgTitle">' + (first ? 'New scenario' : 'Edit mandate') + '</h2>'
    + '<p class="dlg-sub">Mandate details for this client.</p>'
    + '<div class="field"><label for="mdtop">Top account size</label>'
    + '<input type="text" id="mdtop" inputmode="numeric" autocomplete="off" value="'
    + (draft.top ? esc(money(draft.top)) : '') + '"' + invalid('mdtop')
    + (draft.saving ? ' disabled' : '') + '></div>'
    + '<div class="field"><label for="mdsize">Mandate size</label>'
    + '<input type="text" id="mdsize" inputmode="numeric" autocomplete="off" value="'
    + (draft.size ? esc(money(draft.size)) : '') + '"' + invalid('mdsize')
    + (draft.saving ? ' disabled' : '') + '>'
    + '<span class="field-hint">At least ' + money(opt('rules.mandateFloor', 5000000))
    + ', and no more than the top account.</span></div>'
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
  if (e.target.id === 'ccy') { requestBasisChange('currency', e.target.value); return; }
  if (e.target.id === 'hedge') { requestBasisChange('hedging', e.target.value); return; }

  if (e.target.id === 'bpa' || e.target.id === 'bpre' || e.target.id === 'bptaa'
      || e.target.id === 'bpr') {
    var base = state.columns[0] || null;
    if (!base) {
      /* no base yet: accumulate the draft; build once allocation + risk exist */
      var pending = state.baseDraft;
      if (e.target.id === 'bpa') { pending.allocation = e.target.value; pending.excludeRE = false; }
      if (e.target.id === 'bpre') pending.excludeRE = e.target.checked;
      if (e.target.id === 'bptaa') pending.excludeTAA = e.target.checked;
      if (e.target.id === 'bpr') pending.riskLevel = e.target.value;
      if (pending.allocation && pending.riskLevel) {
        var pendingCanRE = reAllowed(pending.allocation);
        var built = setBase({
          allocation: pending.allocation,
          excludeRE: pendingCanRE ? !!pending.excludeRE : true,
          excludeTAA: !!pending.excludeTAA,
          riskLevel: pending.riskLevel
        });
        if (built) {
          state.baseDraft = { allocation: '', excludeRE: false, excludeTAA: false, riskLevel: '' };
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
    else if (e.target.id === 'bpre') exRE = e.target.checked;
    else if (e.target.id === 'bpa') exRE = false;  /* new allocation: RE held by default */
    else exRE = current.excludeRE;
    var exTAA = (e.target.id === 'bptaa') ? e.target.checked : current.excludeTAA;
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
  setStep: function (s) {
    state.step = s;
    announce('polite', s === 'impl' ? 'Implementation step.' : 'Asset allocation step.');
    refresh();
  },
  schema: function () { return state.schema; },
  schemaReady: schemaReady,
  opt: opt,
  canEdit: canEdit,
  canExport: canExport,
  autoSleeveCategories: autoSleeveCategories,

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
