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
  bootChecked: false
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
function hashFor(view) { return view === 'catalogue' ? catHash() : '#repository'; }
function syncHash() {
  if (!repo.open) return;
  try { window.history.replaceState(null, '', hashFor(repo.view)); } catch (e) { /* file: */ }
}

function openRepository(trigger, view, at) {
  if (!canAdmin()) return;
  repo.open = true; repo.error = null; repo.trigger = trigger || document.activeElement;
  repo.view = view === 'catalogue' ? 'catalogue' : 'sleeves';
  /* where to land, when the caller knows: the sleeve tier's shortcut opens on
     the implementation type the proposal is already using (D62) */
  repo.pending = at || null;
  render();
  loadRepository();
}

function switchView(view) {
  if (view === repo.view) return;
  repo.view = view; cat.openChip = null; cat.detail = null;
  render();
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
  if (window.location.hash.indexOf('#repository') === 0 || window.location.hash.indexOf('#catalogue') === 0) {
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
  if (t.variant) repo.variant = t.variant;
  if (t.category) repo.category = t.category;
  repo.view = 'sleeves';
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
      /* land on the one in the book being looked at, if it is among them */
      var here = madeAll.filter(function (m) { return m.variant === repo.variant; })[0] || madeAll[0];
      repo.category = here.category; repo.variant = here.variant;
      loadDraft(here.id);
      forgetLibrary(last.category);
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
      repo.data.sleeves = repo.data.sleeves.filter(function (s) { return s.id !== gone.id; });
      catRowCache = null;
      var left = sleevesIn(repo.variant, repo.category);
      loadDraft(left[0] ? left[0].id : null);
      forgetLibrary(gone.category);
      App.announce('polite', 'Deleted ' + gone.name + '.');
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
    + '<p class="repo-prov">' + prov + '</p>';
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
    + '>Remove from ' + esc(entry.category) + '</button>'
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

  var header = '<div class="repo-h"><h2 id="repoTitle" class="dlg-shout">Sleeve Repository</h2>'
    + '<div class="repo-seg" role="tablist" aria-label="View">'
    + '<button type="button" role="tab" data-repoview="sleeves" aria-selected="' + (!onCatalogue ? 'true' : 'false') + '">Sleeves</button>'
    + '<button type="button" role="tab" data-repoview="catalogue" aria-selected="' + (onCatalogue ? 'true' : 'false') + '">Catalogue</button>'
    + '</div>';
  if (d && !onCatalogue) {
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
  if (onCatalogue && d) {
    footer = '<div class="repo-f cat-f">' + catTrayHtml()
      + (repo.error ? '<span class="md-err" role="alert">' + esc(repo.error) + '</span>' : '')
      + '<span class="repo-src cat-src">' + esc(d.catalogue.path.replace(/^.*\/(productSource\/)/, '$1')) + ' · '
      + esc(shortDate(d.catalogue.modified)) + ' · read only</span>'
      + '</div>';
  } else {
    var canSave = !!repo.draft && repo.dirty && !draftProblems().length && !repo.saving;
    footer = '<div class="repo-f">'
      + (d ? '<button type="button" class="btn btn-create" data-repocreate'
          + (repo.saving ? ' disabled' : '') + '>+ Create sleeve</button>' : '')
      + (d ? '<span class="repo-src">Library · ' + esc(d.store.path.split('/').pop()) + ' · ' + d.store.sleeves
          + ' sleeves' + (d.store.seededAt ? ' · seeded ' + esc(shortDate(d.store.seededAt)) : '') + '</span>' : '')
      + '<span class="spacer"></span>'
      + (repo.error ? '<span class="md-err" role="alert">' + esc(repo.error) + '</span>' : '')
      + (repo.draft && repo.draft.id && !isFixed(repo.category)
          ? (repo.confirmDelete
              ? '<button type="button" class="btn" data-repocanceldelete>Keep</button>'
                + '<button type="button" class="btn btn-danger" data-repodelete>Confirm delete</button>'
              : '<button type="button" class="btn btn-danger" data-repodelete' + (repo.saving ? ' disabled' : '') + '>Delete sleeve</button>')
          : '')
      + '<button type="button" class="btn btn-primary" data-reposave' + (canSave ? '' : ' disabled') + '>'
      + (repo.saving ? 'Saving…' : (repo.draft && repo.draft.create ? 'Create sleeve' : 'Save sleeve')) + '</button>'
      + '</div>';
  }

  host.innerHTML = '<div class="scrim" data-reposcrim></div>'
    + '<div class="dialog repo' + (onCatalogue ? ' catalogue' : '') + '" role="dialog" aria-modal="true" aria-labelledby="repoTitle">'
    + header + leaving + body + footer + '</div>';
  syncHash();

  if (focused) {
    var again = document.getElementById(focused);
    if (again && again.focus) {
      again.focus();
      if ((again.id === 'repoSearch' || again.id === 'catSearch') && again.setSelectionRange && again.type !== 'search') {
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
    + '[data-repocreate],[data-repocopy],[data-reporemove]') : null;
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
