/* ---- the sleeve repository console (D57) and the product catalogue (D58) --
   Where an admin builds and maintains the sleeve library, and reads the
   catalogue it is built from: one overlay on the same footing as the fee card
   viewer - the page behind it goes inert, Escape closes it, focus returns to
   whatever opened it - with two views behind a switch in its header.

   SLEEVES: the library two ways behind a switch (D156) - Cards (the
   implementation type, the categories as tiles, one category's sleeves as
   cards, one sleeve's page) and Table (every sleeve, filtered and sorted,
   opening in a drawer). A sleeve reads first and edits on request. Products
   are picked from the delivered catalogue (D56), never typed.

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
  draftStart: '',              /* what the draft said when it was loaded: dirty means changed (D156) */
  stale: null,                 /* someone else saved the open sleeve since it was loaded (409, D156) */
  savedAt: null,               /* when the open draft was last written, for the footer (F2) */
  kept: null,                  /* an unsaved draft that outlived a reload (F1) */
  query: '',                   /* searching the sleeve list, across every book (C1) */
  edMenu: false,               /* the editor's own action menu is open */
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
  openRevision: null,          /* the revision whose composition is expanded */
  /* the applicability of the draft's rules (D89): how many strategic
     portfolios they name and which sibling edition they collide with,
     asked of the server as the desk ticks, a beat behind the last tick */
  preview: null,               /* { busy } | { applies, universe, overlaps } | { error } */
  previewStamp: 0
};

/* The Uncalled Capital Allocation view (D148, named by D149): how a private-
   markets commitment is held until capital is called. Its own state,
   kept across a switch to another view, so a half-typed split survives a
   look at the catalogue. The draft is the rows as typed - a weight is the
   text in its box, so '33.3333' stays exactly what the desk wrote. */
var fund = {
  loaded: false, busy: false, saving: false, error: null,
  data: null,                  /* describe(): house, overrides, eligible, variants */
  scope: '*',                  /* '*' for the house split, or a type */
  draft: null,                 /* [{ category, weight }] while the desk is editing */
  note: '',
  history: null,               /* { scope, entries } */
  historyBusy: false
};

/* the three fields an edition's rules may constrain (D89), in the order
   the builder shows them, and the words it shows them under */
var RULE_FIELDS = ['currency', 'riskLevel', 'allocationType'];
var RULE_LABELS = { currency: 'Currency', riskLevel: 'Risk level', allocationType: 'Allocation' };

/* The catalogue view's own state (D63). It survives a switch to the other
   view, and every part of it encodes into the URL, so a view is a link. */
var cat = {
  query: '', filters: {},            /* facet field -> [values] */
  sort: { key: 'productCost', dir: 'asc' }, /* null = the delivery's own order */
  hidden: [],                        /* column keys taken away by the picker */
  density: 'dense',                  /* 'dense' | 'comfortable' */
  group: true,                       /* banded by sleeve category (D100); the default since D114 */
  folded: [],                        /* the categories whose bands are shut */
  pins: [],                          /* productIds in the tray */
  cursor: null,                      /* the row the keyboard is on */
  cursorBand: null,                  /* and, banded, which band's copy of it */
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
  { key: 'variant', label: 'Implementation type' },
  { key: 'held', label: 'Held', num: true },
  { key: 'createdAt', label: 'Created' },
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
  view: 'recent',              /* the saved view in force (D116) */
  openFilters: false,          /* the five selects, behind Filter */
  entries: [], next: null, total: 0, facets: null, summary: null, views: null, viewsBusy: false,
  busy: false, loaded: false, error: null,
  detail: null,                /* the proposal open in the right-hand pane */
  record: null,                /* that proposal, fetched */
  recordBusy: false,
  picture: 'implemented'       /* 'allocation' | 'implemented' */
};
/* The saved views, in the order they are shown. Each answers a question people
   arrive with, and carries its own count - 3 with sleeves moved is worth
   reading before it is pressed. Pressing one replaces the filters rather than
   adding to them, which is what keeps the counts honest (D116). */
var REG_VIEWS = [
  ['recent', 'Last 90 days'], ['week', 'This week'], ['mine', 'Mine'],
  ['moved', 'Sleeves moved'], ['noAccount', 'No account yet'], ['all', 'All time']
];
function regApplyView(key) {
  reg.view = key;
  reg.exportedBy = ''; reg.primaryPwa = ''; reg.currency = ''; reg.variant = '';
  reg.range = (key === 'week') ? '7d' : (key === 'recent') ? '90d' : 'all';
  if (key === 'mine') reg.exportedBy = App.opt('capabilities.user', '');
}
var REG_RANGES = [['7d', 'Last 7 days'], ['30d', 'Last 30 days'], ['90d', 'Last 90 days'],
                  ['365d', 'Last year'], ['all', 'All time']];
var regTimer = null;
var ACT_RANGES = [['7d', 'Last 7 days'], ['30d', 'Last 30 days'], ['90d', 'Last 90 days'], ['all', 'All time']];
var actTimer = null;

/* The facet rail, in order. A facet is a field of the product, or one of the
   two joins the repository already gives: which categories' sleeves hold it,
   and which books offer it. Both of those are many-valued. */
var CAT_FACETS = [
  { key: 'category', label: 'Category' },
  { key: 'vehicle', label: 'Vehicle' },
  { key: 'shareClass', label: 'Share class', order: ['Dis', 'Acc'] },
  { key: 'liquidity', label: 'Liquidity', order: ['Daily', 'Weekly', 'Monthly', 'Quarterly', 'Drawdown'] },
  { key: 'style', label: 'Style' },
  { key: 'exposureCurrency', label: 'Exposure' },
  { key: 'book', label: 'Implementation type' }
];
/* Every column, in order. The product column cannot be taken away; the
   rest can, and the picker remembers. num: right-aligned condensed figures.
   derived: net yield, set apart in its head because it is computed here.
   Product fees only (D114): a management fee depends on a proposal's
   schedule, tier and level, and the catalogue describes products, not a
   proposal - so no management, all-in or fee-group figure appears here. */
var CAT_COLUMNS = [
  { key: 'ticker', label: 'Ticker' },
  { key: 'name', label: 'Product', fixed: true },
  { key: 'assetClass', label: 'Class' },
  { key: 'vehicle', label: 'Veh' },
  { key: 'shareClass', label: 'Share' },
  { key: 'style', label: 'Style' },
  { key: 'source', label: 'Src' },
  { key: 'exposureCurrency', label: 'Ccy' },
  { key: 'liquidity', label: 'Liq' },
  { key: 'productCost', label: 'Cost', num: true },
  { key: 'distributionYield', label: 'Yield', num: true },
  { key: 'net', label: 'Net', num: true, derived: true },
  { key: 'minimumInvestment', label: 'Min', num: true },
  { key: 'used', label: 'Used', num: true }
];
var CAT_NUMERIC = { productCost: 1, distributionYield: 1, net: 1, minimumInvestment: 1, used: 1 };
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
/* A version's created stamp (D152): the date and the time, since two
   versions of one sleeve are often saved the same day. */
function shortDateTime(iso) {
  if (!iso) return '';
  var d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return shortDate(iso) + ', ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}
function esc(s) { return App.esc(s == null ? '' : String(s)); }

/* catalogue-helpers-begin
   Pure functions over the fetched data, DOM-free so the suite can run them
   through node against fixture data the way it runs the rounding and tilt
   mirrors. A "row" here is a product enriched with its joins and its
   figures: { p, used, categories, books, net, tooBig }. */
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
function catEnrich(products, sleeves, mandateSize) {
  var join = catJoin(sleeves);
  return (products || []).map(function (p) {
    var j = join[p.productId] || { used: 0, categories: [], books: [] };
    var y = p.distributionYield;
    var cost = p.productCost;
    var min = p.minimumInvestment;
    return {
      p: p, used: j.used, categories: j.categories, books: j.books,
      /* the yield less the product's own cost - never a management fee (D114) */
      net: (typeof y === 'number' && typeof cost === 'number') ? y - cost : null,
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
  return [p.name, p.ticker, p.assetClass, p.vehicle, p.source]
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
  if (key === 'used' || key === 'net') return row[key];
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
  var rules = { productCost: -1, distributionYield: 1, net: 1 };
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
function catGroups(rows, order, chosen) {
  /* the rows banded by sleeve category (D100), the bands in the order given
     and each saying what it holds: how many, the product-cost range, how many are
     not dealt daily. The rows keep the order they came in, so a sort applies
     within a band. Category is a join: a product two categories hold stands
     in both bands, and with a category filter in force only the chosen
     categories make bands - a band the filter did not ask for would be noise. */
  var at = function (key) { var i = (order || []).indexOf(key); return i === -1 ? 1e9 : i; };
  var index = {}, out = [];
  (rows || []).forEach(function (row) {
    var keys = catValuesOf(row, 'category');
    if (chosen && chosen.length) keys = keys.filter(function (k) { return chosen.indexOf(k) !== -1; });
    keys.forEach(function (key) {
      var g = index[key];
      if (!g) { g = index[key] = { key: key, rows: [], lo: null, hi: null, notDaily: 0 }; out.push(g); }
      g.rows.push(row);
      var cost = row.p.productCost;
      if (typeof cost === 'number' && isFinite(cost)) {
        if (g.lo === null || cost < g.lo) g.lo = cost;
        if (g.hi === null || cost > g.hi) g.hi = cost;
      }
      var liquidity = String(row.p.liquidity || '').toLowerCase();
      if (liquidity && liquidity !== 'daily') g.notDaily += 1;
    });
  });
  return out.sort(function (a, b) {
    return (at(a.key) - at(b.key)) || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
  });
}
/* catalogue-helpers-end */

var catRowCache = null;
function catRows() {
  if (!catRowCache) {
    var mandate = (typeof App.mandateSize === 'function') ? App.mandateSize() : null;
    catRowCache = catEnrich(repo.data.products, repo.data.sleeves,
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
function copyRules(rules) {
  return (rules || []).map(function (r) {
    var out = {};
    RULE_FIELDS.forEach(function (f) { out[f] = (r[f] || []).slice(); });
    return out;
  });
}
function draftFrom(entry) {
  return {
    id: entry.id, name: entry.name, note: entry.note || '',
    label: entry.label || '', rules: copyRules(entry.rules),
    products: entry.products.map(function (row) {
      return { productId: row.productId, weightPct: Math.round(row.weight * 10000) / 100 };
    })
  };
}
function newDraft(create, edition) {
  /* A creation draft carries the two things an existing sleeve already knows:
     which category it implements, and which books offer it. Both are fixed
     once it exists - a sleeve moved between them is a different sleeve - so
     they are only editable here (D61).

     An EDITION draft (D89) is the third kind: another edition of a name the
     library already has, in the same book and category. It starts as a copy
     of the edition it was taken from - the desk edits rather than retypes -
     with the name locked and the label and rules its own to fill in. */
  var draft = { id: null, name: '', note: '', label: '', rules: [], products: [] };
  if (edition) {
    var from = sleeveById(edition.ofId);
    draft.edition = { ofId: edition.ofId, variant: from ? from.variant : repo.variant,
                      category: from ? from.category : repo.category };
    draft.name = from ? from.name : edition.name;
    draft.note = from ? (from.note || '') : '';
    draft.products = from ? from.products.map(function (row) {
      return { productId: row.productId, weightPct: Math.round(row.weight * 10000) / 100 };
    }) : [];
    return draft;
  }
  if (create) {
    /* the category in hand, or none: with none in hand the desk chooses it,
       on the form, rather than finding the first with room chosen for them;
       and only a type whose fixed category is not already full (D157) */
    draft.create = true;
    draft.category = create.category || null;
    var v = create.variant || null;
    draft.variants = v && (!draft.category || svRoomIn(v, draft.category)) ? [v] : [];
    if (!draft.variants.length && draft.category) {
      var room = repo.data.variants.filter(function (x) { return svRoomIn(x, draft.category); });
      if (room.length === 1) draft.variants = room;
    }
    /* blank, or a copy of a sleeve already in the library (D157) */
    draft.startFrom = ncStartDefault();
    draft.sourceId = null;
  }
  return draft;
}
/* Where a draft lives - its own type and category, read from the draft,
   never from whatever the view happens to be showing (D156): the editor's
   checks for a fixed category, sibling editions and the applicability count
   all depend on it. */
function draftVariant(d) {
  if (!d) return repo.variant;
  if (d.edition) return d.edition.variant;
  if (d.create) return (d.variants || [])[0] || repo.variant;
  var e = d.id ? sleeveById(d.id) : null;
  return e ? e.variant : repo.variant;
}
function draftCategory(d) {
  if (!d) return repo.category;
  if (d.edition) return d.edition.category || repo.category;
  if (d.create) return d.category;
  var e = d.id ? sleeveById(d.id) : null;
  return e ? e.category : repo.category;
}
/* dirty means the draft says something it did not when it was loaded */
function markDirty() {
  repo.dirty = !!repo.draft && svDraftSig(repo.draft) !== repo.draftStart
    /* a new sleeve is worth asking about once it has a name, a note or a
       product - choosing where it goes is not yet work to lose (D157) */
    && (!repo.draft.create || !!(repo.draft.name.trim() || repo.draft.note.trim() || repo.draft.products.length));
}
/* Renaming a sleeve renames it under this implementation type only; the
   sleeves of the old name under the others keep it, and are no longer the
   same sleeve (D156 QA 15). Said before the save, not discovered after. */
function renameWarning() {
  var d = repo.draft; if (!d || !d.id) return '';
  var e = sleeveById(d.id); if (!e || d.name.trim() === e.name) return '';
  var others = (e.offeredUnder || []).filter(function (v) { return v !== e.variant; });
  if (!others.length) return '';
  return 'Renaming changes the name under ' + e.variant + ' only. The sleeves called ' + e.name + ' under '
    + others.join(', ') + ' keep their name and stop being offered as the same sleeve.';
}
/* the rules as the server takes them: only the fields with something ticked */
function cleanRules(rules) {
  return (rules || []).map(function (r) {
    var out = {};
    RULE_FIELDS.forEach(function (f) { if ((r[f] || []).length) out[f] = r[f].slice(); });
    return out;
  });
}
function ruleIsEmpty(r) {
  return !RULE_FIELDS.some(function (f) { return (r[f] || []).length; });
}
function describeRules(rules) {
  return (rules || []).map(function (r) {
    return RULE_FIELDS.filter(function (f) { return (r[f] || []).length; })
      .map(function (f) { return RULE_LABELS[f].toLowerCase() + ' ' + r[f].join(' | '); }).join('; ');
  }).join('  OR  ');
}
/* the live editions of one name in one book and category, the draft's own
   siblings: what its label and rules must not collide with */
function siblingsOf(d) {
  var variant = draftVariant(d);
  var category = draftCategory(d);
  return sleevesIn(variant, category).filter(function (s) {
    return s.name.trim().toLowerCase() === (d.name || '').trim().toLowerCase() && s.id !== d.id;
  });
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
  /* a new sleeve is judged by its own form, field by field (D157) */
  if (d.create) return ncProblems(d).map(function (p) { return p.m; });
  if (!d.name.trim()) out.push('Give the sleeve a name.');
  if (d.create && !(d.variants || []).length) out.push('Choose at least one implementation type.');
  if (d.create && !d.category) out.push('Choose a category.');
  /* the edition (D89): a label goes with rules and rules with a label; a
     rule ticks something; one fallback per name; one label per name */
  var rules = d.rules || [];
  if (rules.length && !d.label.trim()) out.push('Give the edition a label, such as "GBP" or "GBP ex-Alts".');
  if (d.label.trim() && !rules.length) out.push('Add at least one rule, or clear the label to make this the fallback.');
  if (rules.some(ruleIsEmpty)) out.push('Every rule needs at least one value ticked.');
  var sibs = siblingsOf(d);
  if (!rules.length && sibs.some(function (s) { return s.fallback; })) {
    out.push((d.edition ? 'This name' : d.name.trim()) + ' already has a fallback edition; give this one rules and a label.');
  }
  if (d.label.trim() && sibs.some(function (s) { return (s.label || '').toLowerCase() === d.label.trim().toLowerCase(); })) {
    out.push('An edition labelled ' + d.label.trim() + ' already exists for this name.');
  }
  if (!d.products.length) out.push('Add at least one product.');
  if (d.products.some(function (r) { return !r.productId; })) out.push('Every row needs a product.');
  var badWeight = d.products.filter(function (r) { return r.weightText != null && String(r.weightText).trim() !== '' && !isFinite(r.weightPct); })[0];
  if (badWeight) out.push('“' + String(badWeight.weightText).trim() + '” is not a plain number: write a weight like 12.5.');
  if (d.products.some(function (r) { return !(r.weightPct > 0); })) out.push('Every product needs a weight above zero.');
  if (d.products.length && !svWeightsOk(total())) {
    out.push('Weights sum to ' + svTotalText(total()) + '%; a sleeve must sum to exactly 100%.');
  }
  /* the server counts the rules' overlap with the name's other editions;
     a draft it says collides cannot be saved, so Save says why (D156 QA 11) */
  var pv = repo.preview;
  if (pv && pv.overlaps && pv.overlaps.length && (d.rules || []).some(function (r) { return !ruleIsEmpty(r); })) {
    out.push('Its rules overlap the ' + pv.overlaps.map(function (o) { return o.label; }).join(' and ')
      + ' edition' + (pv.overlaps.length === 1 ? '' : 's') + '; narrow them so each portfolio gets exactly one edition.');
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
  else if (!(cat.sort.key === 'productCost' && cat.sort.dir === 'asc')) q.set('sort', cat.sort.key + ':' + cat.sort.dir);
  if (cat.hidden.length) q.set('hide', cat.hidden.join(','));
  if (cat.density !== 'dense') q.set('d', cat.density);
  if (!cat.group) q.set('group', 'flat');
  else if (cat.folded.length) q.set('fold', cat.folded.join('|'));
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
  var known = function (key) { return CAT_COLUMNS.some(function (c) { return c.key === key; }); };
  cat.sort = { key: 'productCost', dir: 'asc' };
  if (sort === 'none') cat.sort = null;
  /* a link saved before D114 may sort by a column that is gone (all-in,
     management): it falls back to the default rather than to nothing */
  else if (sort && sort.indexOf(':') !== -1 && known(sort.split(':')[0])) {
    cat.sort = { key: sort.split(':')[0], dir: sort.split(':')[1] === 'desc' ? 'desc' : 'asc' };
  }
  cat.hidden = (q.get('hide') || '').split(',').filter(known);
  cat.density = q.get('d') === 'comfortable' ? 'comfortable' : 'dense';
  cat.group = q.get('group') !== 'flat';          /* banded unless the link says flat (D114) */
  cat.folded = cat.group ? (q.get('fold') || '').split('|').filter(Boolean) : [];
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
  if (reg.view !== 'recent') q.set('view', reg.view);
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
  var view = q.get('view');
  reg.view = REG_VIEWS.some(function (v) { return v[0] === view; }) ? view : 'recent';
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
  if (view === 'uncalled') return '#uncalled';
  if (view === 'overlays') return '#overlays';
  return '#repository';
}
function syncHash() {
  if (!repo.open) return;
  try { window.history.replaceState(null, '', hashFor(repo.view)); } catch (e) { /* file: */ }
}

function openRepository(trigger, view, at) {
  if (!canAdmin()) return;
  repo.open = true; repo.error = null; repo.trigger = trigger || document.activeElement;
  repo.view = ['catalogue', 'archive', 'activity', 'proposals', 'uncalled', 'overlays'].indexOf(view) !== -1 ? view : 'sleeves';
  /* where to land, when the caller knows: the sleeve tier's shortcut opens on
     the implementation type the proposal is already using (D62) */
  repo.pending = at || null;
  sv.level = 0; sv.drawer = false; sv.compare = false; sv.sel = []; sv.editing = false; sv.focus = null; sv.lastPlace = null;
  sv.fv = at && at.variant ? at.variant : ''; sv.fc = '';
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
  if (view === 'uncalled' && !fund.loaded) loadFunding();
  if (view === 'overlays' && !ovl.loaded) loadOverlays();
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
    sv.keptOffer = svReadKeptOffer();
    repo.error = null;
    if (repo.view === 'activity' && !act.loaded) loadActivity(true);
    if (repo.view === 'proposals') {
      if (!reg.loaded) loadRegister(true);
      if (reg.detail) loadProposal(reg.detail);
    }
    if (repo.view === 'uncalled') loadFunding();
    if (repo.view === 'overlays') loadOverlays();
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

function loadDraft(sleeveId, create, edition) {
  var entry = sleeveId ? sleeveById(sleeveId) : null;
  repo.sleeveId = entry ? entry.id : null;
  repo.draft = entry ? draftFrom(entry) : newDraft(create, edition);
  /* the version this edit starts from, sent with the save (D156) */
  if (entry) repo.draft.base = entry.revisions;
  repo.draftStart = svDraftSig(repo.draft); repo.stale = null;
  repo.dirty = false; repo.fieldError = null; repo.picker = null;
  repo.savedAt = null; repo.edMenu = false;
  repo.kept = keptDraftFor(repo.draft);        /* a copy that outlived a reload (F1) */
  repo.confirmDelete = false; repo.leaving = null; repo.menu = null;
  sv.editing = false;                          /* a sleeve opens to be read (D156) */
  ncReset();                                   /* the New sleeve form's own state goes with any draft (D157) */
  if (sv.createdFrom && sv.createdFrom.ids.indexOf(repo.sleeveId) === -1) sv.createdFrom = null;
  /* an existing edition already knows how many portfolios it names */
  repo.preview = entry && entry.rules && entry.rules.length ? { applies: entry.applies } : null;
  repo.previewStamp += 1;
}

function closeRepository(force) {
  if (repo.dirty && !force) { repo.leaving = { close: true }; repo.view = 'sleeves'; render(); return; }
  /* an unsaved overlay list asks first too (D155) */
  if (!force && ovlDirty()) {
    ovl.leaving = { close: true }; repo.view = 'overlays'; render(); ovlFocus('[data-ovl="leavekeep"]'); return;
  }
  ncReset();
  repo.open = false; repo.data = null; repo.draft = null; repo.dirty = false;
  repo.picker = null; repo.leaving = null; repo.confirmDelete = false; forgetJoins();
  cat.openChip = null; cat.detail = null; cat.compare = false;
  if (/^#(repository|catalogue|archive|activity|proposals|uncalled|overlays)/.test(window.location.hash)) {
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
    /* a move to another view, held back by an unsaved draft and now released:
       the changes were discarded, so the draft goes back to what is stored
       rather than travelling on with them (D156) */
    var gone = repo.draft;
    repo.dirty = false;
    if (gone) {
      if (gone.id && sleeveById(gone.id)) loadDraft(gone.id);
      else { repo.draft = null; if (sv.level === 2) sv.level = 1; sv.drawer = false; }
    }
    sv.editing = false;
    if (t.detail != null) arc.detail = t.detail;
    switchView(t.view);
    if (t.then) t.then();
    return;
  }
  /* the Sleeves view's own place (D156): what the move asked for, or - for
     a move that lands on a sleeve from elsewhere (a save, a revert, a
     restore, the feed, the catalogue) - that sleeve, open, with the table's
     filters following it and a search that would hide it cleared */
  var arriving = !t.sv && (t.to != null || t.fresh);
  if (t.sv) Object.keys(t.sv).forEach(function (k) { sv[k] = t.sv[k]; });
  else if (arriving) { if (sv.mode === 'table') sv.drawer = true; else sv.level = 2; }
  if (sv.drawer) sv.compare = false;
  if (t.to != null) {
    var landing = sleeveById(t.to);
    if (landing && arriving) {
      if (!svMatches(landing, repo.query)) repo.query = '';
      if (sv.mode === 'table') { var fol = svFollow({ fv: sv.fv, fc: sv.fc }, landing); sv.fv = fol.fv; sv.fc = fol.fc; sv.returnTo = landing.id; }
      else repo.query = '';
    }
  }
  if (!sv.focus) {
    if (t.fresh) sv.focus = '#repoName||#repoLabel||#repoNote';
    else if (t.to != null) sv.focus = sv.mode === 'table' ? '#svDrawerTitle' : '#svTitle';
  }
  if (t.variant) repo.variant = t.variant;
  if (t.category) repo.category = t.category;
  repo.view = 'sleeves';
  /* the history belongs to the sleeve that was open, not to the next one */
  if (t.to == null || (repo.history && repo.history.sleeveId !== t.to)) {
    repo.historyOpen = false; repo.history = null; repo.openRevision = null;
  }
  try { window.history.replaceState(null, '', hashFor('sleeves')); } catch (e) { /* file: */ }
  if (t.fresh) { loadDraft(null, t.create ? { variant: t.noVariant ? null : (t.variant || repo.variant), category: t.category || null } : null, t.edition); }
  else if (t.to != null) { loadDraft(t.to); }
  else { repo.sleeveId = null; repo.dirty = false; chooseDefaults(); }
  if (t.fresh && t.edition) sv.focus = '#repoLabel';
  render();
  if (t.then) t.then();
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
  /* where the held move meant to put focus goes with it; keeping the edit
     puts focus back on the editing controls (D156) */
  sv.focus = discard ? sv.pending
    : (sv.editFocus ? sv.editFocus + '||' : '') + '[data-reposave]||#repoName||#repoNote';
  sv.pending = null;
  if (!discard || !leaving) { render(); return; }
  /* discarding means discarding: the copy kept against a reload goes with the
     changes, or reopening the sleeve would offer back what was just refused (F1) */
  repo.kept = null; forgetKeptDraft();
  if (leaving.close) { closeRepository(true); return; }
  repo.dirty = false;
  applyTarget(leaving);
}

/* ---- saving and deleting ----------------------------------------------- */
async function saveDraft() {
  if (draftProblems().length || repo.saving) return;
  var d = repo.draft;
  var payload = {
    name: svNormName(d.name), note: d.note.trim(),
    label: d.label.trim(), rules: cleanRules(d.rules),
    products: d.products.map(function (r) {
      return { productId: r.productId, weight: Math.round(r.weightPct * 10000) / 1000000 };
    })
  };
  repo.saving = true; repo.fieldError = null; repo.error = null; render();
  try {
    var r;
    if (d.id) {
      if (typeof d.base === 'number') payload.base = d.base;
      r = await api('PUT', '/scenario/repository/sleeves/' + d.id, payload);
    } else if (d.edition) {
      r = await api('POST', '/scenario/repository/sleeves/' + d.edition.ofId + '/editions', payload);
    } else {
      payload.category = d.create ? d.category : repo.category;
      payload.variants = d.create ? d.variants : [repo.variant];
      r = await api('POST', '/scenario/repository/sleeves', payload);
    }
    if (!r) return;
    if (!r.ok) {
      /* someone else saved it since it was opened: say so, keep the edits on
         screen, and offer the reload rather than overwrite theirs (D156) */
      if (r.status === 409) repo.stale = r.body.error || 'Someone else saved this sleeve since you opened it.';
      else if (r.body.field) repo.fieldError = { field: r.body.field, message: r.body.error };
      else repo.error = r.body.error || ('Could not save (' + r.status + ')');
      var fieldIds = { name: 'repoName', label: 'repoLabel', note: 'repoNote', category: 'ncCats', variants: 'ncTypes', products: 'ncRows', weights: 'ncRows' };
      sv.focus = (repo.fieldError && fieldIds[repo.fieldError.field] ? '#' + fieldIds[repo.fieldError.field] + '||' : '')
        + (repo.stale ? '[data-svreload]||' : '') + '[data-svstate]||[data-svcancel]||[data-nccreate]';
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
      /* a save that changed a sleeve archived the version it replaced (D152) */
      madeAll.forEach(function (saved) {
        if (!saved.archivedVersion) return;
        repo.data.archived = [saved.archivedVersion].concat(archivedSleeves());
        if (repo.data.store) repo.data.store.archived = (repo.data.store.archived || 0) + 1;
        delete saved.archivedVersion;
      });
      if (repo.data.store) {
        /* every sleeve written appended one revision (D65) */
        repo.data.store.revisions = (repo.data.store.revisions || 0) + madeAll.length;
        if (!d.id) repo.data.store.sleeves = (repo.data.store.sleeves || 0) + madeAll.length;
      }
      /* land on the one in the book being looked at, if it is among them */
      var here = madeAll.filter(function (m) { return m.variant === repo.variant; })[0] || madeAll[0];
      /* a new sleeve copied from another says so on its page, for this visit:
         it is a separate sleeve from now on (D157) */
      var from = d.create ? ncSource(d) : null;
      repo.category = here.category; repo.variant = here.variant;
      var trailOpen = repo.historyOpen && repo.history && repo.history.sleeveId === here.id;
      loadDraft(here.id);
      if (d.create) sv.createdFrom = { ids: madeAll.map(function (m) { return m.id; }), made: last.name,
                                       under: madeAll.map(function (m) { return m.variant; }),
                                       name: from ? from.name : (d.sourceName || null), variant: from ? from.variant : null };
      forgetLibrary(last.category);
      /* a trail left open across a save would be one revision behind, which is
         the one revision the person looking at it just made */
      if (trailOpen) { repo.historyOpen = true; repo.openRevision = null; loadHistory(here.id); }
      if (act.loaded) act.loaded = false;
      repo.savedAt = new Date().toISOString();
      repo.kept = null; forgetKeptDraft();     /* it is in the store now (F1) */
      if (sv.mode === 'table') {
        sv.drawer = true; sv.compare = false;
        /* the table's filters follow what was saved, and closing the drawer
           goes back to its row (D157) */
        var fol = svFollow({ fv: sv.fv, fc: sv.fc }, here); sv.fv = fol.fv; sv.fc = fol.fc; sv.returnTo = here.id;
        if (repo.query.trim() && !svMatches(here, repo.query)) repo.query = '';
      } else sv.level = 2;
      sv.focus = sv.mode === 'table' ? '#svDrawerTitle' : '#svTitle';   /* back on the page, read (D156) */
      /* the outcome, not just the redraw (G2): the revision it became is the
         part an admin checks, and it is the part a screen reader could not see */
      App.announce('polite', d.create
        ? 'Created ' + last.name + ' under ' + svJoinAnd(madeAll.map(function (m) { return m.variant; }))
          + (from ? ', from a copy of ' + from.name : '') + '. You are on its page.'
        : madeAll.length > 1
        ? 'Saved ' + last.name + ' under ' + madeAll.length + ' implementation types, revision '
          + (last.revisions || 1) + '.'
        : 'Saved ' + last.name + (last.label ? ' (' + last.label + ' edition)' : '')
          + ' · revision ' + (last.revisions || 1) + '.');
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
      /* its page and its drawer go with it (D156) */
      if (sv.level === 2) sv.level = 1;
      sv.drawer = false; sv.sel = sv.sel.filter(function (x) { return x !== gone.id; });
      if (sv.sel.length < 2) sv.compare = false;
      sv.returnTo = null;
      sv.focus = sv.mode === 'table' ? svRowFocus() : '#svCatTitle||.sv-tile';
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
      label: entry.label || '', rules: cleanRules(entry.rules),
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
      if (repo.data.store) {
        repo.data.store.sleeves = (repo.data.store.sleeves || 0) + 1;
        repo.data.store.revisions = (repo.data.store.revisions || 0) + 1;
      }
      repo.data.sleeves.forEach(function (x) {
        if (x.category === made.category && x.name === made.name) x.offeredUnder = made.offeredUnder;
      });
      catRowCache = null;
      forgetLibrary(made.category);
      sv.focus = '.sv-offer:not([disabled])||#svOffers';
      App.announce('polite', made.name + ' is now offered under ' + variant + '.');
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
  if (repo.dirty && repo.draft && repo.draft.id === sleeveId) {
    repo.error = 'Save or discard your changes to ' + entry.name + ' before archiving it.';
    sv.focus = '[data-svcancel]||[data-reposave]'; render(); return;
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
        if (sv.level === 2) sv.level = 1;
        sv.drawer = false;
      }
      sv.sel = sv.sel.filter(function (x) { return x !== gone.id; });
      if (sv.sel.length < 2) sv.compare = false;
      sv.focus = sv.mode === 'table' ? svRowFocus() : '#svCatTitle||.sv-card||.sv-tile';
      forgetLibrary(gone.category);
      App.announce('polite', gone.name + ' archived from ' + gone.category
        + '. It keeps its history and can be restored from the Archive.');
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

/* ---- the applicability preview (D89) --------------------------------------
   The server counts what a rule set names and what it collides with, the
   same way a save will. Asked a beat after the last tick, and an answer
   that arrives after a later question is thrown away. */
var previewTimer = null;
function schedulePreview() {
  if (previewTimer) clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, 250);
}
async function runPreview() {
  previewTimer = null;
  var d = repo.draft; if (!d || !repo.open) return;
  var rules = cleanRules(d.rules).filter(function (r) { return !ruleIsEmpty(r); });
  if (!rules.length) { repo.preview = null; updateApplies(); return; }
  var stamp = (repo.previewStamp += 1);
  repo.preview = { busy: true }; updateApplies();
  try {
    var r = await api('POST', '/scenario/repository/applicability', {
      variant: draftVariant(d),
      category: draftCategory(d),
      name: d.name.trim(), label: d.label.trim(), rules: rules, sleeveId: d.id || null
    });
    if (stamp !== repo.previewStamp || !r) return;
    repo.preview = r.ok
      ? { applies: r.body.applies, universe: r.body.universe, overlaps: r.body.overlaps || [] }
      : { error: r.body.error || ('Could not check the rules (' + r.status + ')') };
  } catch (err) { repo.preview = { error: err.message }; }
  updateApplies();
}
function appliesHtml() {
  var d = repo.draft; if (!d) return '';
  var rules = (d.rules || []).filter(function (r) { return !ruleIsEmpty(r); });
  if (!rules.length) {
    return '<p class="repo-applies">' + (d.label.trim()
      ? 'No rules yet: tick the portfolios the ' + esc(d.label.trim()) + ' edition is for.'
      : 'The fallback: applies wherever no other edition of this name does.') + '</p>';
  }
  var p = repo.preview;
  if (!p) return '<p class="repo-applies"></p>';
  if (p.busy) return '<p class="repo-applies">Counting…</p>';
  if (p.error) return '<p class="repo-applies bad">' + esc(p.error) + '</p>';
  var line = 'Applies to ' + p.applies + (p.universe ? ' of ' + p.universe : '') + ' strategic portfolio'
    + (p.applies === 1 ? '' : 's') + '.';
  var clash = (p.overlaps || []).map(function (o) {
    return 'Overlaps the ' + esc(o.label) + ' edition on ' + o.count + ' portfolio' + (o.count === 1 ? '' : 's')
      + ': ' + esc(o.keys.slice(0, 4).join(', ')) + (o.keys.length > 4 ? ', …' : '') + '.';
  });
  return '<p class="repo-applies' + (clash.length ? ' bad' : ' ok') + '">' + esc(line)
    + (clash.length ? ' ' + clash.join(' ') + ' Narrow one of them; exactly one edition may apply to a portfolio.' : '')
    + '</p>';
}
function updateApplies() {
  var el = document.getElementById('repoApplies');
  if (el) el.innerHTML = appliesHtml(); else render();
  updateTotals();
}
function rulesHtml() {
  var d = repo.draft;
  var vocab = repo.data.ruleVocabulary || {};
  var rows = (d.rules || []).map(function (rule, i) {
    var fields = RULE_FIELDS.map(function (f) {
      var chips = (vocab[f] || []).map(function (v) {
        var on = (rule[f] || []).indexOf(v) !== -1;
        return '<label class="repo-chip' + (on ? ' on' : '') + '"><input type="checkbox" data-reporule="' + i
          + '" data-repofld="' + esc(f) + '" data-repoval="' + esc(v) + '"' + (on ? ' checked' : '') + '> '
          + esc(v) + '</label>';
      }).join('');
      return '<div class="repo-rule-f"><span class="lbl">' + esc(RULE_LABELS[f]) + '</span><span class="chips">' + chips + '</span></div>';
    }).join('');
    return '<div class="repo-rule"><div class="repo-rule-h"><span>Rule ' + (i + 1)
      + (i ? ' <small>or</small>' : '') + '</span>'
      + '<button type="button" class="repo-rm" data-reporulerm="' + i + '" aria-label="Remove rule ' + (i + 1) + '">×</button></div>'
      + fields + '</div>';
  }).join('');
  return '<div class="repo-rules">' + rows
    + '<button type="button" class="btn repo-add" data-reporuleadd>+ ' + ((d.rules || []).length ? 'Add another rule' : 'Add a rule') + '</button>'
    + '</div>';
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
  markDirty(); repo.fieldError = null;
  closePicker(false); render();
  var w = document.querySelector('[data-repoweight="' + row + '"]'); if (w) { w.focus(); w.select(); }
}
function addRow() {
  repo.draft.products.push({ productId: null, weightPct: NaN });
  markDirty();
  openPicker(repo.draft.products.length - 1);
}
function removeRow(i) {
  var gone = repo.draft.products.splice(i, 1)[0];
  var p = gone && productById(gone.productId);
  markDirty(); repo.picker = null;
  /* focus stays where the row was: the next weight, else the one before,
     else the way to add another (D157 review) */
  sv.focus = '[data-repoweight="' + i + '"]||[data-repoweight="' + (i - 1) + '"]||[data-ncadd]||[data-repoadd]';
  render();
  App.announce('polite', 'Removed ' + (p ? p.name : (gone && gone.productId) || 'the product') + '.');
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
      + (r.weightText != null && String(r.weightText).trim() !== '' && !isFinite(r.weightPct) ? ' aria-invalid="true"' : '')
      + ' value="' + (r.weightText != null ? esc(r.weightText) : isFinite(r.weightPct) ? esc(money2(r.weightPct)) : '') + '">'
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
  var ok = n > 0 && svWeightsOk(t);
  return '<span>Total' + (n ? '' : ' · no products yet') + '</span>'
    + '<span class="' + (ok ? 'ok' : 'bad') + '">' + (n ? svTotalText(t) + (ok ? ' ✓' : ' ✗') : '—') + '</span><span></span>';
}

function fieldErr(field) {
  var fe = repo.fieldError;
  return fe && fe.field === field ? '<p class="md-err" role="alert">' + esc(fe.message) + '</p>' : '';
}

/* The actions that act on THIS sleeve, in the editor where it is named (A2).
   They used to sit in the footer, where Archive shared an edge with Save -
   the one control pressed constantly beside the one that cannot be undone
   from the keyboard. The confirmation stays where the trigger is, rather
   than appearing at the other end of the dialog. */
/* A draft that outlived a reload, offered back rather than restored behind
   the admin's back: what is on screen is what the store holds, and taking it
   away without asking would be its own kind of loss (F1). */
function keptNoticeHtml() {
  if (!repo.kept || repo.dirty) return '';
  return '<div class="repo-notice repo-kept" role="status">'
    + '<span>Unsaved changes to this sleeve were kept from ' + esc(clockTime(repo.kept.at)) + '.</span>'
    + '<button type="button" class="btn" data-repokeptrestore>Restore them</button>'
    + '<button type="button" class="btn btn-ghost" data-repokeptdrop>Discard</button></div>';
}

function editorTopHtml(entry, fixed) {
  if (!entry) return '';
  var where = '<span class="repo-ed-where">' + esc(entry.category) + ' \u00b7 ' + esc(entry.variant) + '</span>';
  if (repo.confirmDelete) {
    return '<div class="repo-ed-top is-confirm">'
      + '<span class="repo-ed-warn">Archive <b>' + esc(entry.name) + '</b>? It keeps its history and can be '
      + 'restored from the Archive.</span>'
      + '<button type="button" class="btn" data-repocanceldelete>Keep</button>'
      + '<button type="button" class="btn btn-danger" data-repodelete' + (repo.saving ? ' disabled' : '') + '>'
      + 'Confirm archive</button></div>';
  }
  return '<div class="repo-ed-top">' + where
    + '<button type="button" class="repo-ed-act" data-repoedition="' + entry.id + '"'
    + (repo.saving ? ' disabled' : '')
    + ' title="Another edition of ' + esc(entry.name) + ', for particular portfolios">+ Add edition</button>'
    + (fixed ? ''
        : '<button type="button" class="repo-ed-act danger" data-repodelete' + (repo.saving ? ' disabled' : '')
          + '>Archive this sleeve\u2026</button>')
    + '</div>';
}

function editorHtml() {
  var d = repo.draft;
  if (!d) return '<p class="repo-empty">Choose a sleeve, or start a new one.</p>';
  var entry = d.id ? sleeveById(d.id) : null;
  var fixed = isFixed(draftCategory(d));
  var problems = draftProblems();
  var prov = '';
  if (entry) {
    /* this version's stamp; saving a change archives it and starts another (D152) */
    prov = 'This version created ' + esc(shortDateTime(entry.createdAt)) + (entry.createdBy ? ' by ' + esc(entry.createdBy) : '')
      + (entry.firstCreatedAt && entry.firstCreatedAt !== entry.createdAt
          ? ' · sleeve first created ' + esc(shortDate(entry.firstCreatedAt)) : '')
      + ' · saving a change puts this version in the Archive';
    var elsewhere = (entry.offeredUnder || []).filter(function (v) { return v !== entry.variant; });
    if (elsewhere.length) prov += ' · the same name is offered under ' + esc(elsewhere.join(', '));
  } else {
    prov = 'New sleeve · ' + esc(draftCategory(d)) + ' under ' + esc(draftVariant(d));
  }
  if (d.edition) {
    var of = sleeveById(d.edition.ofId);
    prov = 'New edition of ' + esc(d.name) + ' · ' + esc(draftCategory(d)) + ' under ' + esc(d.edition.variant)
      + (of ? ' · starts as a copy of the ' + (of.label ? esc(of.label) + ' edition' : 'fallback') : '');
  }
  var serverProblems = entry && entry.problems && entry.problems.length
    ? '<div class="repo-notice" role="status">' + entry.problems.map(esc).join(' ') + ' The sleeve is withheld from the pickers until this is fixed.</div>'
    : '';
  /* which category it implements and which types offer it are a sleeve's
     identity, chosen once on the New sleeve form (D157) and read only here */
  var categoryField = '<div class="repo-fld"><label>Category</label><div class="ro">' + esc(draftCategory(d))
    + (fixed ? ' <span class="repo-fixed">fixed</span>' : '') + '</div></div>';
  /* the name is the sleeve's identity across its editions (D89): an
     edition draft shows it read only, and an existing edition that has
     siblings cannot be renamed on its own */
  var sibs = entry ? siblingsOf(d) : [];
  var nameField;
  if (d.edition || sibs.length) {
    nameField = '<div class="repo-fld"><label>Sleeve name</label><div class="ro">' + esc(d.name)
      + (sibs.length ? ' <span class="repo-fixed" title="Shared by ' + (sibs.length + 1) + ' editions">' + (sibs.length + 1) + ' editions</span>' : '')
      + '</div></div>';
  } else {
    nameField = '<div class="repo-fld"><label for="repoName">Sleeve name</label>'
      + '<input type="text" id="repoName" maxlength="80" value="' + esc(d.name) + '"'
      + (repo.fieldError && repo.fieldError.field === 'name' ? ' aria-invalid="true"' : '') + '>' + fieldErr('name')
      + '<p class="sv-rename" id="repoRenameNote" role="status">' + esc(renameWarning()) + '</p></div>';
  }
  var editionField = '<div class="repo-fld repo-edn"><label for="repoLabel">Edition</label>'
    + '<input type="text" id="repoLabel" maxlength="80" placeholder="Leave blank for the fallback, or label it: GBP, GBP ex-Alts, EUR Moderate…"'
    + ' value="' + esc(d.label) + '"'
    + (repo.fieldError && repo.fieldError.field === 'label' ? ' aria-invalid="true"' : '') + '>' + fieldErr('label')
    + '<span class="repo-sub">Which strategic portfolios this edition is for. Tick values within a rule to narrow it; add a rule for an alternative. PWAs never see the label - they pick the name, and get the edition for their portfolio.</span>'
    + rulesHtml()
    + '<div id="repoApplies">' + appliesHtml() + '</div>'
    + fieldErr('rules') + '</div>';
  return ''
    + editorTopHtml(entry, fixed)
    + keptNoticeHtml()
    + serverProblems
    + '<div class="repo-frow">'
    + nameField
    + categoryField
    + '</div>'
    + '<div class="repo-fld"><label for="repoNote">Note</label>'
    + '<input type="text" id="repoNote" maxlength="240" placeholder="Optional. Shown to PWAs in the picker hint." value="' + esc(d.note) + '"></div>'
    + editionField
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
        + (entry.label ? ' <span class="repo-fixed">' + esc(entry.label) + '</span>' : '')
        + (entry.note ? ' <small>' + esc(entry.note) + '</small>' : '') + '</p>'
        + (entry.rules && entry.rules.length ? '<p class="rev-rules">' + esc(describeRules(entry.rules)) + '</p>' : '')
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
  if (repo.historyOpen) showHistory();          /* the rows have landed (B1) */
}

function toggleHistory(sleeveId) {
  repo.historyOpen = !repo.historyOpen;
  repo.openRevision = null;
  if (!repo.historyOpen) { render(); return; }
  if (repo.history && repo.history.sleeveId === sleeveId) { render(); showHistory(); return; }
  loadHistory(sleeveId);
}

/* The record sits at the foot of the editor, which scrolls on its own: on a
   saved sleeve the panel opened 740px down a 654px pane, so the button
   flipped, the fetch ran, the rows rendered, and nothing moved (B1). A
   control that looks broken is worse than one that is missing, because it
   gets pressed again. */
function showHistory() {
  var pane = document.querySelector('#repoDialog .sv-scroll') || document.querySelector('.repo-ed');
  var panel = document.querySelector('#repoDialog .repo-hist');
  if (!pane || !panel) return;
  var top = panel.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop;
  /* Set directly rather than smoothly: a smooth scroll is driven by animation
     frames, which do not run in a background tab, so the panel would open and
     the pane would silently stay where it was - the exact failure this is
     here to fix. The panel is a disclosure; arriving at it is the point. */
  pane.scrollTop = Math.max(0, top - 8);
  var head = panel.querySelector('.repo-hist-h, h4, button');
  if (head && head.focus) head.focus({ preventScroll: true });
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
    var live = sleeveById(id);
    var r = await api('POST', '/scenario/repository/sleeves/' + id + '/revert',
                      live ? { revision: revisionNumber, base: live.revisions } : { revision: revisionNumber });
    if (!r) return;
    if (!r.ok) {
      repo.error = r.status === 409
        ? (r.body.error || 'Someone else saved this sleeve since you opened it.') + ' Reload it before putting a version back.'
        : (r.body.error || ('Could not put that version back (' + r.status + ')'));
      if (r.status === 409) repo.stale = repo.error;
      sv.focus = '[data-svreload]||[data-reporevert]';
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
    var blocked = full && sleevesIn(v, entry.category).some(function (x) { return x.name !== entry.name; });
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
    + '<button type="button" class="repo-mi" data-repoedition="' + entry.id + '">Add an edition for particular portfolios…</button>'
    + '<p class="sep"></p>'
    + (repo.menu.confirm
        ? '<div class="repo-mconf" role="group" aria-label="Confirm archive"><p>Archive <b>' + esc(entry.name) + '</b> from '
          + esc(entry.category) + '? It keeps its history and can be restored from the Archive.</p>'
          + '<button type="button" class="btn" data-repomenukeep>Keep</button>'
          + '<button type="button" class="btn btn-danger" data-reporemoveyes="' + entry.id + '">Archive it</button></div>'
        : '<button type="button" class="repo-mi danger" data-reporemove="' + entry.id + '"'
          + (entry.fixed ? ' disabled title="A fixed category always holds one sleeve"' : '')
          + '>Archive from ' + esc(entry.category) + '\u2026</button>')
    + '</div>';
}

/* ---- searching the library (C1) -----------------------------------------
   101 sleeves live behind 7 categories x 4 books, and the only way to reach
   one whose book you had forgotten was to click until it appeared. The
   catalogue next door answers the same shape of question with a search box,
   so this is that: name, edition label and PRODUCT name, across everything.

   Product matters as much as name - "which sleeves hold GSAM Short Duration"
   is the question asked when a product is being retired, and the console
   could not answer it at all. */
function sleeveHits() {
  var q = repo.query.trim().toLowerCase();
  if (!q) return [];
  var out = [];
  (repo.data.sleeves || []).forEach(function (s) {
    var why = null;
    if ((s.name || '').toLowerCase().indexOf(q) !== -1) why = '';
    else if ((s.label || '').toLowerCase().indexOf(q) !== -1) why = 'edition ' + s.label;
    else {
      var hit = (s.products || []).filter(function (r) {
        return r.product && (r.product.name || '').toLowerCase().indexOf(q) !== -1;
      })[0];
      if (hit) why = 'holds ' + hit.product.name;
    }
    if (why !== null) out.push({ s: s, why: why });
  });
  /* the book in hand first, then by category, so a hit where you already are
     does not sit below twenty that are not */
  var order = repo.data.categories;
  return out.sort(function (a, b) {
    var av = a.s.variant === repo.variant ? 0 : 1, bv = b.s.variant === repo.variant ? 0 : 1;
    if (av !== bv) return av - bv;
    var ac = order.indexOf(a.s.category), bc = order.indexOf(b.s.category);
    if (ac !== bc) return ac - bc;
    return a.s.name < b.s.name ? -1 : a.s.name > b.s.name ? 1 : 0;
  });
}

/* ---- the Sleeves view: cards and a table (D156) --------------------------
   Proposal 11 of proposals/sleeves-screen-redesign.html. One view, two ways
   of looking at the library behind a switch:

   CARDS is the guided path - the implementation type, then the categories
   as tiles, then the sleeves of one category as cards that show what each
   holds, then one sleeve's page - with a breadcrumb back up.
   TABLE is the whole library as one sortable, filterable list; a row opens
   the same sleeve page in a drawer, and up to three ticked rows line up
   in a comparison above the table.

   The sleeve page reads first and edits on request: Edit sleeve turns it
   into the editor the console always had (editorHtml), under a bar that
   says so and holds Save. Nothing about saving, editions, history or the
   archive changed - only where they live.

   The state below is the VIEW's. What is open, the draft and whether it is
   dirty stay in repo, and every move that would leave a dirty draft still
   goes through goTo, so the Keep editing / Discard notice guards both
   views the way it guarded the three panes. Switching views is not a move:
   your place and an unsaved edit carry across. */
var SV_VIEW_KEY = 'pmg.repository.sleevesView';
var sv = {
  mode: 'cards',            /* 'cards' | 'table', remembered per browser */
  level: 0,                 /* cards: 0 the categories, 1 one category's sleeves, 2 a sleeve */
  editing: false,           /* the sleeve page is in edit mode */
  fv: '', fc: '',           /* table: implementation type and category, '' for every one */
  sort: null,               /* table: { key, dir }, or null for the library's own order */
  sel: [],                  /* table: sleeve ids ticked to compare, at most SV_COMPARE_MAX */
  compare: false,
  drawer: false,            /* table: the open sleeve is in the drawer */
  returnTo: null,           /* the row a closed drawer gives focus back to */
  focus: null,              /* selectors, '||'-separated, to focus once the next render lands */
  pending: null,            /* and the ones a move held by unsaved changes would have used */
  leavingSeen: null,
  editFocus: null,          /* the editor field last focused, for Keep editing */
  lastPlace: null,          /* where the last redraw left the view, for its scroll */
  lastTable: null,          /* the table as it was when you went to the cards (D156 QA 8) */
  keptOffer: null           /* an unsaved draft kept from an earlier visit, offered back */
};
var SV_COMPARE_MAX = 3;
try { if (window.localStorage.getItem(SV_VIEW_KEY) === 'table') sv.mode = 'table'; } catch (e) { /* private window */ }

/* sleeve-view-helpers-begin
   Pure functions over the fetched sleeves, DOM-free so the suite runs them
   in node against fixture data (D156). A sleeve is the repository's own
   record: { id, variant, category, name, label, products: [{ weight,
   product: { name, vehicle, productCost } }], problems, createdAt }. */
function svPlural(n, word, many) { return n + ' ' + (n === 1 ? word : (many || word + 's')); }
function svCost(s) {
  /* the weighted product cost, in percent, of the products the catalogue
     prices; svUnpriced says how many it could not - a sleeve none of whose
     products is priced has no figure at all */
  var t = 0, any = false;
  (s.products || []).forEach(function (r) {
    var c = r.product && r.product.productCost;
    if (typeof c === 'number' && isFinite(c)) { t += (Number(r.weight) || 0) * c; any = true; }
  });
  return any ? t : null;
}
function svUnpriced(s) {
  return (s.products || []).filter(function (r) {
    var c = r.product && r.product.productCost;
    return !(typeof c === 'number' && isFinite(c));
  }).length;
}
function svCostText(s) {
  var c = svCost(s), n = svUnpriced(s);
  var figure = c === null ? '—' : (Math.round(c * 100) / 100).toFixed(2) + '%';
  return n ? figure + ' \u00b7 ' + svPlural(n, 'product') + ' unpriced' : figure;
}
function svVehicles(s) {
  var out = [];
  (s.products || []).forEach(function (r) {
    var v = r.product && r.product.vehicle;
    if (v && out.indexOf(v) === -1) out.push(v);
  });
  return out;
}
function svStatus(s) {
  /* a sleeve the server finds a problem with is withheld from the pickers */
  var n = (s.problems || []).length;
  return { ok: !n, label: n ? 'Withheld \u00b7 ' + svPlural(n, 'problem') : 'Ready' };
}
function svMatches(s, query) {
  var q = (query || '').trim().toLowerCase();
  if (!q) return true;
  if ((s.name || '').toLowerCase().indexOf(q) !== -1) return true;
  if ((s.label || '').toLowerCase().indexOf(q) !== -1) return true;
  return (s.products || []).some(function (r) {
    return r.product && (r.product.name || '').toLowerCase().indexOf(q) !== -1;
  });
}
function svFilter(sleeves, f) {
  /* f: { variant, category, query } - an empty variant or category is every one */
  return (sleeves || []).filter(function (s) {
    if (f.variant && s.variant !== f.variant) return false;
    if (f.category && s.category !== f.category) return false;
    return svMatches(s, f.query);
  });
}
function svSortValue(s, key) {
  if (key === 'name') return ((s.name || '') + ' ' + (s.label || '')).toLowerCase();
  if (key === 'category') return (s.category || '').toLowerCase() || null;
  if (key === 'variant') return (s.variant || '').toLowerCase() || null;
  if (key === 'products') return (s.products || []).length;
  if (key === 'vehicles') return svVehicles(s).join(', ').toLowerCase() || null;
  if (key === 'cost') return svCost(s);
  if (key === 'status') return (s.problems || []).length;
  if (key === 'created') return s.createdAt || null;
  return null;
}
function svSort(rows, sort, order) {
  /* by the key asked for, A-Z or Z-A, a blank always last; no key is the
     library's own order - the cards' order: category, type, then name */
  order = order || {};
  var at = function (list, v) { var i = (list || []).indexOf(v); return i === -1 ? 1e9 : i; };
  var key = sort && sort.key, dir = sort && sort.dir === 'desc' ? -1 : 1;
  var library = function (a, b) {
    var an = svSortValue(a, 'name'), bn = svSortValue(b, 'name');
    return (at(order.categories, a.category) - at(order.categories, b.category))
      || (at(order.variants, a.variant) - at(order.variants, b.variant))
      || (an < bn ? -1 : an > bn ? 1 : 0) || ((a.id || 0) - (b.id || 0));
  };
  return (rows || []).slice().sort(function (a, b) {
    if (!key) return library(a, b);
    var x = svSortValue(a, key), y = svSortValue(b, key);
    var xn = x === null || x === undefined, yn = y === null || y === undefined;
    if (xn && !yn) return 1;
    if (yn && !xn) return -1;
    if (!xn && !yn) {
      if (x < y) return -dir;
      if (x > y) return dir;
    }
    return library(a, b);
  });
}
function svTick(sel, id, on, max) {
  /* the compare selection: ticking a fourth does nothing, unticking always works */
  var out = (sel || []).filter(function (x) { return x !== id; });
  if (on && out.length < (max || 3)) out.push(id);
  return out;
}
function svEditing(draft, editing, dirty) {
  /* the page edits when asked to, and always for a draft that is new or
     unsaved - a read view of changes nobody can see would hide them */
  return !!draft && (!!editing || draft.id == null || !!dirty);
}
function svToTable(c) {
  /* the cards' place as the table's: the type and category become its
     filters, and a sleeve open on its page opens in the drawer. A search
     spans every type and category, so it keeps every one. */
  if ((c.query || '').trim()) return { fv: '', fc: '', drawer: c.level === 2 };
  return { fv: c.variant || '', fc: c.level >= 1 ? (c.category || '') : '', drawer: c.level === 2 };
}
function svToCards(t, open) {
  /* the table's place as the cards': the sleeve in the drawer opens on its
     page; otherwise the filters choose the type and the category */
  if (t.drawer && open) return { variant: open.variant, category: open.category, level: 2 };
  var out = { level: t.fc ? 1 : 0 };
  if (t.fv) out.variant = t.fv;
  if (t.fc) out.category = t.fc;
  return out;
}
function svFollow(f, s) {
  /* the table's filters, moved to include a sleeve arriving from elsewhere,
     so its row is there and the line above it names the right place */
  return { fv: f.fv && f.fv !== s.variant ? s.variant : f.fv,
           fc: f.fc && f.fc !== s.category ? s.category : f.fc };
}
var SV_NUMBER = /^\s*(\d+(\.\d*)?|\.\d+)\s*%?\s*$/;
function svWeight(text) {
  /* a weight as typed: a plain number, or NaN - '1,5' and '5abc' are not
     read as 15 and 5 (D156) */
  var t = String(text == null ? '' : text);
  return SV_NUMBER.test(t) ? parseFloat(t) : NaN;
}
function svDraftSig(d) {
  /* what a draft says, as one string, so dirty means changed: retyping 60
     over 60.00 is not a change (D156) */
  if (!d) return '';
  var rules = (d.rules || []).map(function (r) {
    var out = {};
    ['currency', 'riskLevel', 'allocationType'].forEach(function (f) { if ((r[f] || []).length) out[f] = r[f].slice(); });
    return out;
  });
  return JSON.stringify({
    name: (d.name || '').trim(), note: (d.note || '').trim(), label: (d.label || '').trim(), rules: rules,
    products: (d.products || []).map(function (r) {
      return [r.productId || null, isFinite(r.weightPct) ? Math.round(r.weightPct * 10000) / 10000 : String(r.weightText || '')];
    }),
    category: d.category || null, variants: d.variants || null
  });
}

/* The New sleeve form (D157). A "lib" is the repository's sleeve list; a
   row is the form's own { productId, weightPct, weightText }. */
function svNormName(s) {
  /* a name as stored: trimmed, inner runs of spaces collapsed (D157) */
  return String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
}
function svSameName(a, b) { return svNormName(a).toLowerCase() === svNormName(b).toLowerCase(); }
var SV_WEIGHT_TOLERANCE = 1e-4;     /* percent: the server's 1e-6 as a fraction (sleeveRepo.WEIGHT_TOLERANCE) */
function svWeightsOk(total) { return Math.abs(total - 100) <= SV_WEIGHT_TOLERANCE; }
function svTotalText(t) {
  /* a total in as many places as it takes to see why it is refused:
     99.999, not 100.00; an accepted total reads 100.00 */
  if (!isFinite(t)) return '—';
  if (svWeightsOk(t)) return '100.00';
  for (var places = 2; places <= 4; places += 1) {
    var text = t.toFixed(places);
    if (Math.abs(parseFloat(text) - t) < 1e-9 || ['100.00', '100.000', '100.0000'].indexOf(text) === -1) return text;
  }
  return String(Math.round(t * 1e6) / 1e6);
}
function svPossessive(name) { var n = String(name || ''); return /s$/i.test(n) ? n + '\u2019' : n + '\u2019s'; }
function svJoinAnd(list) {
  var l = (list || []).slice();
  if (l.length < 2) return l.join('');
  return l.slice(0, -1).join(', ') + ' and ' + l[l.length - 1];
}
function svWeightNote(text) {
  /* what is wrong with a weight as typed, or '' when it is a weight */
  var t = String(text == null ? '' : text);
  if (!t.trim()) return '';
  if (/^\s*-\s*(\d+(\.\d*)?|\.\d+)\s*%?\s*$/.test(t)) return 'Must be above zero';
  return isFinite(svWeight(t)) ? (svWeight(t) > 0 ? '' : 'Must be above zero') : 'Not a plain number';
}
function svRowsMatch(rows, s) {
  /* the form's products are exactly a sleeve's, weights included */
  var a = (rows || []), b = (s && s.products) || [];
  if (a.length !== b.length) return false;
  return b.every(function (r) {
    var x = a.filter(function (y) { return y.productId === r.productId; })[0];
    return !!x && isFinite(x.weightPct) && Math.abs(x.weightPct - svPctOf(r.weight)) < 1e-9;
  });
}
function svNameElsewhere(lib, variants, chosen, category, name) {
  /* the sleeves of this name under the types not ticked: 'same' spelling
     (one sleeve offered more widely) or 'case' (differs in capitals only,
     which the library keeps as separate sleeves) */
  if (!category || !svNormName(name)) return [];
  var out = [];
  (variants || []).forEach(function (v) {
    if ((chosen || []).indexOf(v) !== -1) return;
    var hit = (lib || []).filter(function (s) { return s.variant === v && s.category === category && svSameName(s.name, name); })[0];
    if (hit) out.push({ variant: v, name: hit.name, kind: hit.name === svNormName(name) ? 'same' : 'case' });
  });
  return out;
}
function svAvail(lib, fixed, variant, category, name) {
  /* whether a new sleeve can be made under one type: a fixed category
     already holding its sleeve cannot take another, and a name the type
     already has there is that sleeve - never quietly a new edition of it */
  if (!category) return { ok: true };
  var here = (lib || []).filter(function (s) { return s.variant === variant && s.category === category; });
  if ((fixed || []).indexOf(category) !== -1 && here.length) {
    return { ok: false, kind: 'full', reason: category + ' holds one sleeve per implementation type, and '
      + variant + ' already has ' + here[0].name + '.', short: 'Full: already holds ' + here[0].name };
  }
  var nm = String(name || '').trim();
  var clash = nm ? here.filter(function (s) { return svSameName(s.name, nm); })[0] : null;
  if (clash) {
    return { ok: false, kind: 'taken', reason: variant + ' already has a sleeve called ' + clash.name + ' in ' + category
      + '. To give some portfolios a different mix, open ' + clash.name + ' and add an edition.',
      short: 'Already has a sleeve called ' + clash.name + ' here' };
  }
  return { ok: true };
}
function svCategoryFull(lib, fixed, variants, category) {
  return (variants || []).every(function (v) { return !svAvail(lib, fixed, v, category, '').ok; });
}
function svOutcomes(lib, fixed, variants, chosen, category, name) {
  /* per implementation type: what Create will make there, or why not */
  return (variants || []).map(function (v) {
    var a = svAvail(lib, fixed, v, category, name), on = (chosen || []).indexOf(v) !== -1;
    return { variant: v, chosen: on, ok: a.ok, kind: a.kind || null, reason: a.reason || '', short: a.short || '' };
  });
}
function svSourceKey(s) {
  /* what a sleeve holds, in a form that ignores the order its rows were saved in */
  return [s.name, s.label || ''].concat((s.products || []).map(function (r) { return r.productId + ':' + r.weight; }).sort()).join('|');
}
function svSources(lib, f) {
  /* the sleeves a new one can start from: this category, under the types
     chosen (or every type), an identical copy under several types listed
     once with every type it is under */
  var q = String(f.query || '').trim().toLowerCase(), at = {}, out = [];
  var order = f.order || [];
  var pos = function (v) { var i = order.indexOf(v); return i === -1 ? 1e9 : i; };
  (lib || []).filter(function (s) {
    return s.category === f.category && (f.allV || !(f.variants || []).length || f.variants.indexOf(s.variant) !== -1);
  }).sort(function (a, b) {
    return (pos(a.variant) - pos(b.variant)) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0) || ((a.id || 0) - (b.id || 0));
  }).forEach(function (s) {
    var k = svSourceKey(s);
    if (at[k]) { at[k].types.push(s.variant); at[k].ids.push(s.id); return; }
    at[k] = { s: s, types: [s.variant], ids: [s.id] }; out.push(at[k]);
  });
  return out.filter(function (e) {
    if (!q) return true;
    if (e.s.name.toLowerCase().indexOf(q) !== -1 || String(e.s.label || '').toLowerCase().indexOf(q) !== -1) return true;
    return (e.s.products || []).some(function (r) { return r.product && String(r.product.name || '').toLowerCase().indexOf(q) !== -1; });
  });
}
function svPctOf(weight) { return Math.round(Number(weight) * 10000) / 100; }
function svSourceMark(src, row) {
  /* a row against the sleeve it was copied from: '' unchanged, 'added', or
     'was 40.00%' for a weight that moved */
  if (!src) return '';
  var o = (src.products || []).filter(function (r) { return r.productId === row.productId; })[0];
  if (!o) return 'added';
  var was = svPctOf(o.weight);
  return isFinite(row.weightPct) && Math.abs(was - row.weightPct) < 1e-9 ? '' : 'was ' + was.toFixed(2) + '%';
}
function svSourceRemoved(src, rows) {
  if (!src) return [];
  return (src.products || []).filter(function (r) { return !(rows || []).some(function (x) { return x.productId === r.productId; }); });
}
function svSourceChanges(src, rows) {
  if (!src) return 0;
  return (rows || []).filter(function (r) { return svSourceMark(src, r); }).length + svSourceRemoved(src, rows).length;
}
function svPickAction(rowCount, unchanged, pickedId, currentId) {
  /* picking a sleeve to start from: the same one is nothing; with no
     products yet, or only the source's own, it copies; over products the
     desk has entered it asks first */
  if (pickedId === currentId && currentId != null) return 'same';
  return !rowCount || unchanged ? 'copy' : 'ask';
}
function svRowsCost(rows, products) {
  /* the weighted product cost of the form's rows, in percent, and how many
     products the catalogue does not price; no figure until a priced
     product has a weight */
  var c = 0, any = false, unpriced = 0;
  (rows || []).forEach(function (r) {
    var p = products[r.productId], k = p && p.productCost;
    if (typeof k === 'number' && isFinite(k)) { if (isFinite(r.weightPct) && r.weightPct > 0) { any = true; c += r.weightPct / 100 * k; } }
    else unpriced += 1;
  });
  return { cost: any ? c : null, unpriced: unpriced };
}
function svRowsCostText(rows, products) {
  var w = svRowsCost(rows, products);
  var figure = w.cost === null ? '—' : (Math.round(w.cost * 100) / 100).toFixed(2) + '%';
  return w.unpriced ? figure + ' · ' + svPlural(w.unpriced, 'product') + ' unpriced' : figure;
}
function svSpread(n) {
  /* n weights at 2dp that make exactly 100, the remainder on the last */
  if (!(n > 0)) return [];
  var base = Math.floor(10000 / n) / 100, out = [];
  for (var i = 0; i < n; i += 1) out.push(i === n - 1 ? Math.round((100 - base * (n - 1)) * 100) / 100 : base);
  return out;
}
function svCategoryProducts(lib, category) {
  /* the products this category's sleeves hold, under any type */
  var seen = {}, out = [];
  (lib || []).forEach(function (s) {
    if (s.category !== category) return;
    (s.products || []).forEach(function (r) { if (!seen[r.productId]) { seen[r.productId] = 1; out.push(r.productId); } });
  });
  return out;
}
function svAddHits(products, f) {
  /* the Add a product dialog's rows: the pool (null for the whole
     catalogue), then the search and the vehicle, style and source filters,
     sorted by name, asset class, cost or minimum with a blank last */
  var q = String(f.query || '').trim().toLowerCase();
  var pool = f.pool ? f.pool.reduce(function (m, id) { m[id] = 1; return m; }, {}) : null;
  var hits = (products || []).filter(function (p) {
    if (pool && !pool[p.productId]) return false;
    if (f.vehicle && p.vehicle !== f.vehicle) return false;
    if (f.style && p.style !== f.style) return false;
    if (f.source && p.source !== f.source) return false;
    if (!q) return true;
    return [p.name, p.assetClass, p.vehicle, p.productId, p.ticker].join(' ').toLowerCase().indexOf(q) !== -1;
  });
  var key = f.sort || 'cost', dir = f.dir === 'desc' ? -1 : 1;
  var val = function (p) {
    if (key === 'name') return String(p.name || '').toLowerCase();
    if (key === 'class') return String(p.assetClass || '').toLowerCase() || null;
    if (key === 'min') return typeof p.minimumInvestment === 'number' ? p.minimumInvestment : null;
    return typeof p.productCost === 'number' ? p.productCost : null;
  };
  return hits.sort(function (a, b) {
    var x = val(a), y = val(b), xn = x === null, yn = y === null;
    if (xn && !yn) return 1;
    if (yn && !xn) return -1;
    if (!xn && !yn && x !== y) return x < y ? -dir : dir;
    return String(a.name) < String(b.name) ? -1 : String(a.name) > String(b.name) ? 1 : 0;
  });
}
function svAddRows(rows, ids) {
  /* the products ticked in the dialog, appended without a weight; one the
     sleeve already holds is never added twice */
  var have = {}; (rows || []).forEach(function (r) { have[r.productId] = 1; });
  var out = (rows || []).slice();
  (ids || []).forEach(function (id) { if (!have[id]) { have[id] = 1; out.push({ productId: id, weightPct: NaN, weightText: '' }); } });
  return out;
}
/* sleeve-view-helpers-end */

/* the category's colour from the key the allocation chart uses; the two
   categories the key has no slot for take the colours the console already
   gives them elsewhere */
var SV_FALLBACK_COLOUR = { 'Hybrid Fixed Income': '#5E7690', 'Private Equity & Other Private Assets': 'var(--cat-6)' };
function svColour(category) {
  return (App.categoryColour && App.categoryColour(category)) || SV_FALLBACK_COLOUR[category] || '#9AA7B5';
}
function svSw(category) { return '<i class="sv-sw" aria-hidden="true" style="background:' + svColour(category) + '"></i>'; }
function svPct(n) { return (typeof n === 'number' && isFinite(n)) ? money2(n) + '%' : '—'; }
function svOrder() { return { categories: repo.data.categories, variants: repo.data.variants }; }
function svSiblings(s) {
  return sleevesIn(s.variant, s.category).filter(function (x) {
    return x.id !== s.id && x.name.trim().toLowerCase() === s.name.trim().toLowerCase();
  });
}
function svStatusChip(s) {
  var st = svStatus(s);
  return '<span class="sv-chip ' + (st.ok ? 'ok' : 'bad') + '">' + (st.ok ? '\u2713 ' : '! ') + esc(st.label) + '</span>';
}
function svEditionChip(s) {
  if (s.label) return '<span class="sv-chip info">Edition: ' + esc(s.label) + '</span>';
  return svSiblings(s).length ? '<span class="sv-chip mute">Fallback edition</span>' : '';
}
function svFixedChip(category) {
  return isFixed(category) ? '<span class="sv-chip mute">Fixed \u00b7 one per type</span>' : '';
}
function svWhere(variant, category) {
  return '<span class="sv-cell">' + svSw(category) + esc(category) + '</span><span class="sv-dot" aria-hidden="true">\u00b7</span><span>' + esc(variant) + '</span>';
}
/* whether a category under a type can take another sleeve: a fixed one
   already holding its sleeve cannot */
function svRoomIn(variant, category) {
  return !(isFixed(category) && sleevesIn(variant, category).length >= 1);
}
/* where a new sleeve would go from here: the cards' type and category, or
   the table's filters; with no category in hand, none - the form asks (D157) */
function svNewContext() {
  /* from inside the New sleeve form, where the form was opened from (D157 review) */
  if (ncOpen() && (sv.mode === 'table' ? sv.drawer : sv.level === 2) && nc.back) {
    var b = nc.back;
    return { variant: b.variant || null, category: b.category || null };
  }
  /* the table with every type shown names no type: none is ticked for you */
  var variant = sv.mode === 'table' ? (sv.fv || null) : repo.variant;
  var category = sv.mode === 'table' ? sv.fc : (sv.level >= 1 ? repo.category : '');
  return { variant: variant, category: category || null };
}
function svOpenEntry() { return repo.draft && repo.draft.id ? sleeveById(repo.draft.id) : null; }
function svDraftName() {
  var d = repo.draft; if (!d) return '';
  if (d.create) return d.name.trim() || 'New sleeve';
  if (d.edition) return 'New edition of ' + d.name;
  var e = svOpenEntry();
  return e ? e.name + (e.label ? ' (' + e.label + ')' : '') : d.name;
}
/* the draft in words, for the notice that guards it: what is actually being
   edited, a new edition by the label it has been given */
function svDraftWords() {
  var d = repo.draft; if (!d) return 'this sleeve';
  if (d.edition) return d.label.trim() ? 'the new ' + d.label.trim() + ' edition of ' + d.name : 'a new edition of ' + d.name;
  if (d.create) return d.name.trim() ? 'the new sleeve ' + d.name.trim() : 'a new sleeve';
  return svDraftName();
}

/* ---- the bar: the switch, where you are, search and New sleeve --------- */
var SV_ICON_CARDS = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="1" y="1" width="6" height="6" rx="1.2"/><rect x="9" y="1" width="6" height="6" rx="1.2"/><rect x="1" y="9" width="6" height="6" rx="1.2"/><rect x="9" y="9" width="6" height="6" rx="1.2"/></svg>';
var SV_ICON_TABLE = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="1" y="2" width="14" height="2.4" rx="1"/><rect x="1" y="6.8" width="14" height="2.4" rx="1"/><rect x="1" y="11.6" width="14" height="2.4" rx="1"/></svg>';
/* The context line is the table's: the cards have their breadcrumb, and a
   second copy of the path above it was noise (D156 QA). */
function svContextHtml() {
  if (sv.mode !== 'table') return '';
  if (repo.query.trim()) return 'Searching every implementation type and category';
  return 'Showing ' + esc(sv.fv || 'every implementation type') + ' \u00b7 ' + esc(sv.fc || 'every category');
}
function svBarHtml() {
  var cards = sv.mode !== 'table';
  /* a full fixed category does not switch New sleeve off: the form opens on
     it, says it is full, and the desk picks another (D157 review) */
  var full = false;
  /* the page in Cards has no list behind it to search: the box stays, so the
     bar never changes shape, but waits */
  var waiting = cards && sv.level === 2;
  /* nothing to search while a new sleeve is being made: the box steps aside,
     keeping its place so the bar keeps its shape (D157 review) */
  var creating = ncOpen() && (cards ? sv.level === 2 : sv.drawer);
  return '<div class="sv-bar' + (creating ? ' is-creating' : '') + '">'
    + '<div class="sv-mode" role="group" aria-label="View">'
    + '<button type="button" data-svmode="cards" aria-pressed="' + cards + '">' + SV_ICON_CARDS + 'Cards</button>'
    + '<button type="button" data-svmode="table" aria-pressed="' + !cards + '">' + SV_ICON_TABLE + 'Table</button></div>'
    + '<span class="sv-ctx" id="svCtx">' + svContextHtml() + '</span>'
    + '<label class="repo-find sv-find' + (waiting ? ' is-off' : '') + (creating ? ' is-gone' : '') + '"' + (creating ? ' aria-hidden="true"' : '') + '><span aria-hidden="true">\u2315</span>'
    + '<input type="search" id="repoFind" placeholder="' + (waiting ? 'Go back to search' : 'Search names and products\u2026') + '" autocomplete="off"'
    + ' aria-label="Search sleeves by name, edition or product, across every category and implementation type"'
    + (waiting || creating ? ' disabled tabindex="-1"' : '') + ' value="' + esc(waiting || creating ? '' : repo.query) + '"><kbd aria-hidden="true">/</kbd></label>'
    + '<span class="sv-newwrap">'
    + (full ? '<span class="sv-newwhy" id="svNewWhy">' + esc(at.category) + ' holds one sleeve per type</span>' : '')
    + '<button type="button" class="btn btn-primary sv-new" data-reponew'
    + (full ? ' disabled aria-describedby="svNewWhy"' : '') + (repo.saving && !full ? ' disabled' : '') + '>+ New sleeve</button></span>'
    + '</div>';
}
/* an unsaved draft kept from an earlier visit (F1), offered where it can be
   seen rather than only on the page of the sleeve it belongs to (D156 QA 13) */
function svKeptOfferHtml() {
  var k = sv.keptOffer;
  if (!k || (repo.draft && draftKeyFor(repo.draft) === k.key)) return '';
  var what = k.key.indexOf('sleeve:') === 0 ? (k.draft.name || 'a sleeve')
    : k.key.indexOf('edition:') === 0 ? 'a new edition of ' + (k.draft.name || 'a sleeve')
    : (k.draft.name ? 'the new sleeve ' + k.draft.name : 'a new sleeve');
  return '<div class="sv-kept" role="status"><span>You have unsaved changes to <b>' + esc(what) + '</b>, kept from '
    + esc(clockTime(k.at)) + '.</span><button type="button" class="btn btn-primary" data-svkeptopen>Open them</button>'
    + '<button type="button" class="btn" data-svkeptdrop>Discard</button></div>';
}

/* ---- cards ---------------------------------------------------------------- */
function svTypesHtml() {
  return '<div class="sv-types" role="group" aria-label="Implementation type">'
    + repo.data.variants.map(function (v) {
        return '<button type="button" data-repovariant="' + esc(v) + '" aria-pressed="' + (v === repo.variant) + '">' + esc(v) + '</button>';
      }).join('') + '</div>';
}
function svTilesHtml() {
  return '<div class="sv-tiles">' + repo.data.categories.map(function (c) {
    var list = sleevesIn(repo.variant, c), names = [];
    list.forEach(function (s) { if (names.indexOf(s.name) === -1) names.push(s.name); });
    var withheld = list.filter(function (s) { return !svStatus(s).ok; }).length;
    return '<button type="button" class="sv-tile" data-svtile="' + esc(c) + '" style="--cc:' + svColour(c) + '">'
      + '<span class="sv-tile-h">' + esc(c) + '</span>'
      + '<span class="sv-tile-n"><b>' + list.length + '</b> ' + (list.length === 1 ? 'sleeve' : 'sleeves') + '</span>'
      + '<span class="sv-tile-names">' + (names.length
          ? esc(names.slice(0, 4).join(' \u00b7 ')) + (names.length > 4 ? ' \u00b7 +' + (names.length - 4) + ' more' : '')
          : 'No sleeve yet') + '</span>'
      + ((isFixed(c) || withheld) ? '<span class="sv-tile-c">' + svFixedChip(c)
          + (withheld ? '<span class="sv-chip bad">' + withheld + ' withheld</span>' : '') + '</span>' : '')
      + '</button>';
  }).join('') + '</div>';
}
function svCardHtml(s, why) {
  var rows = (s.products || []).map(function (r) {
    return '<span class="sv-card-r"><span>' + esc(r.product ? r.product.name : r.productId) + '</span><b>' + money2(r.weight * 100) + '%</b></span>';
  }).join('');
  return '<button type="button" class="sv-card" data-svcard="' + s.id + '" data-svmenu="' + s.id + '">'
    + '<span class="sv-card-h"><b>' + esc(s.name) + '</b>' + svEditionChip(s) + '</span>'
    + (why !== undefined ? '<span class="sv-card-w">' + svWhere(s.variant, s.category) + '</span>'
        + (why ? '<span class="sv-card-why">' + esc(why.charAt(0).toUpperCase() + why.slice(1)) + '</span>' : '') : '')
    + '<span class="sv-card-p">' + rows + '</span>'
    + '<span class="sv-card-f">' + svStatusChip(s) + '<small>' + svPlural(s.products.length, 'product')
    + ' \u00b7 weighted cost ' + esc(svCostText(s)) + '</small></span></button>';
}
function svCrumbsHtml() {
  var h = '<nav class="sv-crumbs" aria-label="Where you are">';
  if (sv.level === 0) return h + '<b>Choose a category</b><span class="sv-hint">then a sleeve</span></nav>';
  h += '<button type="button" data-svcrumb="0">All categories \u00b7 ' + esc(repo.variant) + '</button>';
  var creating = repo.draft && repo.draft.create && sv.level === 2;
  var category = creating ? repo.draft.category : repo.category;
  /* a new sleeve whose category is not chosen yet has no category to go up to */
  if (category) {
    h += '<span class="sv-sep" aria-hidden="true">\u203a</span>';
    h += sv.level === 1 ? '<b aria-current="page">' + esc(category) + '</b>'
                        : '<button type="button" data-svcrumb="1">' + esc(category) + '</button>';
  }
  if (sv.level === 2) h += '<span class="sv-sep" aria-hidden="true">\u203a</span><b aria-current="page">' + esc(svDraftName()) + '</b>';
  return h + '</nav>';
}
function svHitsHtml() {
  var hits = sleeveHits();
  if (!hits.length) {
    return '<p class="sv-none">Nothing matches \u201c' + esc(repo.query.trim()) + '\u201d in any category or implementation type.</p>';
  }
  return '<h3 class="sv-h3">' + svPlural(hits.length, 'sleeve') + ' match \u201c' + esc(repo.query.trim()) + '\u201d</h3>'
    + '<p class="sv-hint">Across every implementation type and category. Clear the search to go back to the categories.</p>'
    + '<div class="sv-cards">' + hits.map(function (h) { return svCardHtml(h.s, h.why); }).join('') + '</div>';
}
function svCardsBodyHtml() {
  if (repo.query.trim() && sv.level < 2) return svHitsHtml();
  if (sv.level === 0) {
    return svCrumbsHtml() + '<div class="sv-row">' + svTypesHtml() + '</div>' + svTilesHtml();
  }
  if (sv.level === 1) {
    var list = sleevesIn(repo.variant, repo.category);
    return svCrumbsHtml()
      + '<div class="sv-cathead"><h3 class="sv-h3" id="svCatTitle" tabindex="-1">' + svSw(repo.category) + esc(repo.category) + '</h3>'
      + '<span class="sv-hint">' + svPlural(list.length, 'sleeve') + ' under ' + esc(repo.variant) + '</span>'
      + svFixedChip(repo.category) + '<span class="spacer"></span>' + svTypesHtml() + '</div>'
      + (list.length ? '<div class="sv-cards">' + list.map(function (s) { return svCardHtml(s); }).join('') + '</div>'
                     : '<p class="sv-none">No sleeve in ' + esc(repo.category) + ' under ' + esc(repo.variant) + ' yet. Use + New sleeve to make one.</p>');
  }
  return svCrumbsHtml() + svPageHtml(false);
}

/* ---- the sleeve page: read first, edit on request ----------------------- */
function svSection(title, sub, body, cls) {
  return '<section class="sv-sect' + (cls ? ' ' + cls : '') + '"><h4 class="sv-sect-h">' + esc(title)
    + (sub ? ' <small>' + esc(sub) + '</small>' : '') + '</h4><div class="sv-sect-b">' + body + '</div></section>';
}
function svProductsHtml(entry) {
  var rows = entry.products.map(function (r) {
    var p = r.product, row = p ? catRow(p.productId) : null;
    var facts = p ? [p.assetClass, p.vehicle, p.style, p.source, p.liquidity].filter(Boolean).join(' \u00b7 ') : 'Not in the catalogue';
    var w = r.weight * 100;
    return '<tr><td><b>' + esc(p ? p.name : r.productId) + '</b><small' + (p ? '' : ' class="warn"') + '>' + esc(facts) + '</small></td>'
      + '<td class="num">' + (p ? svPct(p.productCost) : '—') + '</td>'
      + '<td class="num">' + (p ? esc(catMoney(p.minimumInvestment)) : '—')
      + (row && row.tooBig ? ' <span class="sv-chip warn" title="Above the mandate open in the tool">above mandate</span>' : '') + '</td>'
      + '<td class="num"><span class="sv-wbar" aria-hidden="true"><i style="width:' + Math.max(0, Math.min(100, w)) + '%"></i></span>' + money2(w) + '%</td></tr>';
  }).join('');
  var t = entry.products.reduce(function (a, r) { return a + r.weight * 100; }, 0);
  var ok = entry.products.length && Math.abs(t - 100) <= 0.005;
  return '<div class="sv-ptwrap"><table class="sv-pt"><thead><tr><th scope="col">Product</th><th scope="col" class="num">Cost</th>'
    + '<th scope="col" class="num">Minimum</th><th scope="col" class="num">Weight</th></tr></thead><tbody>' + rows + '</tbody>'
    + '<tfoot><tr><td>Total <span class="sv-hint">\u00b7 weighted product cost ' + esc(svCostText(entry)) + '</span></td><td></td><td></td>'
    + '<td class="num ' + (ok ? 'ok' : 'bad') + '">' + money2(t) + '% ' + (ok ? '\u2713' : '\u2717') + '</td></tr></tfoot></table></div>';
}
function svWhoHtml(entry) {
  var sibs = svSiblings(entry), text;
  if (entry.label) {
    text = 'The <b>' + esc(entry.label) + '</b> edition applies to <b>' + (entry.applies == null ? 'some strategic portfolios'
        : svPlural(entry.applies, 'strategic portfolio')) + '</b>'
      + (entry.rules && entry.rules.length ? ': ' + esc(describeRules(entry.rules)) : '')
      + '. Every other portfolio gets the fallback edition of ' + esc(entry.name) + '.';
  } else if (sibs.length) {
    text = 'This is the <b>fallback</b>: it applies wherever no other edition of ' + esc(entry.name) + ' does. Other editions: '
      + sibs.map(function (x) {
          return '<b>' + esc(x.label || 'fallback') + '</b>' + (x.applies != null ? ' (' + svPlural(x.applies, 'portfolio') + ')' : '');
        }).join(', ') + '.';
  } else {
    text = 'One version for every portfolio under ' + esc(entry.variant) + '. An <b>edition</b> is a variant of this sleeve for '
      + 'particular portfolios, such as GBP portfolios; PWAs pick the sleeve by name and get the edition for their portfolio.';
  }
  return '<p class="sv-p">' + text + '</p>'
    + '<button type="button" class="btn" data-repoedition="' + entry.id + '"' + (repo.saving ? ' disabled' : '') + '>+ Add an edition for particular portfolios</button>';
}
function svDetailsHtml(entry) {
  var fixed = isFixed(entry.category);
  var offered = entry.offeredUnder || [entry.variant];
  var more = repo.data.variants.filter(function (v) { return offered.indexOf(v) === -1; });
  var offer = offered.map(function (v) {
    return '<span class="sv-chip ' + (v === entry.variant ? 'info' : 'mute') + '">' + esc(v) + '</span>';
  }).join(' ') + more.map(function (v) {
    var blocked = fixed && sleevesIn(v, entry.category).some(function (x) { return x.name !== entry.name; });
    return ' <button type="button" class="btn sv-offer" data-svcopy="' + esc(v) + '"'
      + (blocked || repo.saving ? ' disabled' : '')
      + (blocked ? ' title="' + esc(entry.category + ' already holds its one sleeve under ' + v) + '"' : '')
      + '>+ Offer under ' + esc(v) + '</button>';
  }).join('');
  var archive;
  if (fixed) {
    archive = '<p class="sv-hint">' + esc(entry.category) + ' is fixed: it always holds exactly one sleeve per implementation type, so this one cannot be archived.</p>';
  } else if (repo.confirmDelete) {
    archive = editorTopHtml(entry, fixed);
  } else {
    archive = '<button type="button" class="btn btn-danger" data-repodelete' + (repo.saving ? ' disabled' : '') + '>Archive this sleeve\u2026</button>'
      + '<span class="sv-hint">It keeps its history and can be restored from the Archive.</span>';
  }
  return '<dl class="sv-kv">'
    + '<dt>Note to PWAs</dt><dd>' + (entry.note ? esc(entry.note) : '<span class="sv-hint">None</span>') + '</dd>'
    + '<dt>Offered under</dt><dd class="sv-offers" id="svOffers" tabindex="-1">' + offer + '</dd>'
    + '<dt>This version</dt><dd>Created ' + esc(shortDateTime(entry.createdAt)) + (entry.createdBy ? ' by ' + esc(entry.createdBy) : '')
    + ' <span class="sv-hint">\u00b7 saving a change puts this version in the Archive</span></dd>'
    + (entry.firstCreatedAt && entry.firstCreatedAt !== entry.createdAt
        ? '<dt>First created</dt><dd>' + esc(shortDateTime(entry.firstCreatedAt)) + '</dd>' : '')
    + '<dt>Vehicles</dt><dd>' + esc(svVehicles(entry).join(', ') || '—') + '</dd>'
    + '</dl><div class="sv-archive">' + archive + '</div>';
}
function svReadHtml(entry, inDrawer) {
  var serverProblems = entry.problems && entry.problems.length
    ? '<div class="repo-notice sv-notice" role="status">' + entry.problems.map(esc).join(' ')
      + ' The sleeve is withheld from the pickers until this is fixed.</div>' : '';
  /* in the drawer its header names the sleeve, so the page does not again */
  return '<div class="sv-head"><div class="sv-head-t">'
    + (inDrawer ? '' : '<h3 id="svTitle" tabindex="-1">' + esc(entry.name) + '</h3>')
    + '<p class="sv-where">' + svWhere(entry.variant, entry.category) + '</p>'
    + '<p class="sv-chips">' + svStatusChip(entry) + svEditionChip(entry) + svFixedChip(entry.category) + '</p></div>'
    + '<button type="button" class="btn btn-primary sv-edit-btn" data-svedit' + (repo.saving ? ' disabled' : '') + '>Edit sleeve</button></div>'
    + keptNoticeHtml() + serverProblems
    + (sv.createdFrom && sv.createdFrom.ids.indexOf(entry.id) !== -1
        ? '<div class="nc-done" role="status"><b>\u2713 Created ' + esc(sv.createdFrom.made) + ' under ' + esc(svJoinAnd(sv.createdFrom.under)) + '.</b>'
          + (sv.createdFrom.name ? ' Started from ' + esc(sv.createdFrom.name) + (sv.createdFrom.variant ? ' (' + esc(sv.createdFrom.variant) + ')' : '')
              + '; it is a separate sleeve, so changes to ' + esc(sv.createdFrom.name) + ' will not change it.' : '')
          + ' <span class="sv-hint">This note shows until you leave this sleeve.</span></div>' : '')
    + svSection('What it holds', svPlural(entry.products.length, 'product'), svProductsHtml(entry))
    + svSection('Who gets it', 'editions', svWhoHtml(entry))
    + svSection('Details', '', svDetailsHtml(entry))
    + historyPanelHtml(entry.id, entry.revisions);
}
function svSaveLabel() {
  var d = repo.draft;
  if (repo.saving) return 'Saving\u2026';
  return d && d.create ? 'Create sleeve' : (d && d.edition ? 'Create edition' : 'Save sleeve');
}
/* what the editing controls say, most urgent first: a save in flight, what
   the server refused, someone else's newer save, the first problem with the
   draft, then a warning that does not block, then the state */
function svStateHtml() {
  if (repo.saving) return '<span class="repo-state">Saving\u2026</span>';
  if (repo.stale) return '<span class="repo-state is-bad">' + esc(repo.stale) + '</span>';
  if (repo.fieldError) return '<span class="repo-state is-bad">' + esc(repo.fieldError.message) + '</span>';
  if (repo.error && repo.draft) return '<span class="repo-state is-bad">' + esc(repo.error) + '</span>';
  var p = repo.dirty ? draftProblems() : [];
  if (p.length) return '<span class="repo-state is-bad">' + esc(p[0]) + '</span>';
  var warn = repo.dirty ? renameWarning() : '';
  if (warn) return '<span class="repo-state is-warn">' + esc(warn) + '</span>';
  return saveStateHtml() || '<span class="repo-state">No changes yet</span>';
}
function svCancelLabel() {
  if (repo.dirty) return 'Discard changes';
  return repo.draft && repo.draft.id ? 'Done' : 'Cancel';
}
function svCanSave() {
  return !!repo.draft && repo.dirty && !draftProblems().length && !repo.saving && !repo.stale;
}
/* the editing controls: a bar over the page in Cards, the drawer's footer in
   Table. The message takes the room and wraps; the actions stay right. While
   the unsaved-changes notice is up its Discard is the only one on screen. */
function svEditControlsHtml() {
  return '<span class="sv-state" data-svstate>' + svStateHtml() + '</span>'
    + '<span class="sv-actions">'
    + (repo.stale ? '<button type="button" class="btn" data-svreload>Reload the sleeve</button>' : '')
    + (repo.leaving ? '' : '<button type="button" class="btn" data-svcancel>' + svCancelLabel() + '</button>')
    + '<button type="button" class="btn btn-primary" data-reposave' + (svCanSave() ? '' : ' disabled') + '>' + svSaveLabel() + '</button>'
    + '</span>';
}
function svPageHtml(inDrawer) {
  var d = repo.draft;
  if (!d) return '<p class="sv-none">Choose a sleeve.</p>';
  if (d.create) {
    /* the New sleeve form carries its own Create and Cancel, in its summary (D157) */
    return (inDrawer ? '' : '<div class="sv-head nc-head"><div class="sv-head-t"><h3 id="svTitle" tabindex="-1">New sleeve</h3>'
        + '<p class="sv-where">' + (d.category ? svWhere((d.variants || []).join(', ') || repo.variant, d.category) : 'Choose where it goes, then what it holds') + '</p></div></div>')
      + svCreateHtml();
  }
  if (svEditing(d, sv.editing, repo.dirty)) {
    var title = d.edition ? 'New edition of ' + d.name : 'Editing this sleeve';
    return (inDrawer ? '' : '<div class="sv-editbar" role="region" aria-label="Editing"><b class="sv-editbar-t">' + esc(title) + '</b>'
        + svEditControlsHtml() + '</div>')
      + '<div class="sv-edit repo-ed">' + editorHtml() + '</div>';
  }
  var entry = svOpenEntry();
  return entry ? svReadHtml(entry, inDrawer) : '<p class="sv-none">Choose a sleeve.</p>';
}

/* ---- the New sleeve form (D157) -------------------------------------------
   Option 11 of proposals/new-sleeve-card.html: one form, a live summary
   pinned beside it, and a switch at the top - start blank, or from a copy of
   a sleeve already in the library. Where it goes (category and types) comes
   first and is chosen in plain sight, each type saying whether a sleeve can
   be made there and why not; products come from an Add a product dialog
   that searches or browses the catalogue; and the summary says exactly what
   Create will make, type by type, before it is pressed.

   The draft is repo.draft as for every sleeve (create: true, with its
   category, variants, startFrom and sourceId), so the unsaved-changes guard,
   the kept draft and the save path are the ones the console already has.
   What only the form needs while it is open - the source search, a question
   it is asking, the dialog - is here. */
var NC_START_KEY = 'pmg.repository.newSleeveStart';
var nc = {
  srcq: '', allV: false,         /* the source list's search, and every type rather than the chosen ones */
  ask: null,                     /* a sleeve id: "replace your products with its?" is on screen */
  lost: '', keptNote: '',        /* why a comparison with a source stopped */
  tried: false,                  /* Create was pressed: every problem shows */
  dlg: null,                     /* the Add a product dialog, open */
  back: null                     /* where Cancel returns to */
};
var NC_SEG = ['#16243A', '#1F5FBF', '#5E8FD6', '#2E7D6B', '#B3741C', '#7A4DA8', '#5E7690', '#A33A3A'];
function ncStartDefault() {
  try { return window.localStorage.getItem(NC_START_KEY) === 'existing' ? 'existing' : 'blank'; } catch (e) { return 'blank'; }
}
function ncReset() {
  nc.srcq = ''; nc.allV = false; nc.ask = null; nc.lost = ''; nc.keptNote = ''; nc.addedNote = false;
  nc.tried = false; nc.dlg = null; nc.touched = false;
}
function ncOpen() { return !!(repo.draft && repo.draft.create); }
function ncProducts() {
  var map = {}; (repo.data.products || []).forEach(function (p) { map[p.productId] = p; });
  return map;
}
function ncSource(d) {
  if (!d || d.sourceId == null) return null;
  var s = sleeveById(d.sourceId); if (s) return s;
  /* the copy picked was archived; an identical copy under another type is the same source */
  var others = (d.sourceIds || []).map(sleeveById).filter(Boolean);
  return others[0] || null;
}
/* the ids of every identical copy of a sleeve in its category, so archiving one keeps the comparison */
function ncCopyIds(s) {
  var key = svSourceKey(s);
  return repo.data.sleeves.filter(function (x) { return x.category === s.category && svSourceKey(x) === key; }).map(function (x) { return x.id; });
}
/* a kept draft read back: weights stored as '' are blank, not zero; a source
   no longer in the library stops being compared, and says so */
function ncRevive(d) {
  if (!d) return d;
  (d.products || []).forEach(function (r) {
    if (typeof r.weightPct !== 'number') r.weightPct = (r.weightPct === '' || r.weightPct == null) ? NaN : svWeight(r.weightPct);
  });
  if (d.create && d.sourceId != null && !ncSource(d)) {
    nc.lost = (d.sourceName || 'The sleeve this started from') + ' is no longer in the library, so your products are no longer compared with it.';
    d.sourceId = null; d.sourceIds = null;
  } else if (d.create && ncSource(d)) {
    d.sourceId = ncSource(d).id;
  }
  return d;
}
function ncAvail(v, d) { return svAvail(repo.data.sleeves, repo.data.fixedCategories, v, d.category, d.name); }
function ncTotal(d) { return d.products.reduce(function (a, r) { return a + (isFinite(r.weightPct) ? r.weightPct : 0); }, 0); }
/* what stands between the form and Create, by field, in the order the form asks */
function ncProblems(d) {
  var out = [], lib = repo.data.sleeves, fixed = repo.data.fixedCategories;
  if (!d.category) out.push({ f: 'cat', m: 'Choose a category.' });
  else if (svCategoryFull(lib, fixed, repo.data.variants, d.category)) {
    out.push({ f: 'cat', loud: true, m: d.category + ' holds its one sleeve under every implementation type. Choose another category.' });
  }
  if (!(d.variants || []).length) out.push({ f: 'vars', m: 'Choose at least one implementation type.' });
  (d.variants || []).forEach(function (v) {
    var a = ncAvail(v, d);
    if (!a.ok) out.push({ f: 'vars', clash: true, m: a.reason + ' Untick ' + v + (a.kind === 'taken' ? ' or change the name.' : '.') });
  });
  if (!svNormName(d.name)) out.push({ f: 'name', m: 'Give the sleeve a name.' });
  if (!d.products.length) out.push({ f: 'rows', m: 'Add at least one product from the catalogue.' });
  var products = ncProducts();
  var gone = d.products.filter(function (r) { return !products[r.productId]; })[0];
  if (gone) out.push({ f: 'rows', m: gone.productId + ' is no longer in the catalogue. Remove it, or choose another product.' });
  var bad = d.products.filter(function (r) { return svWeightNote(r.weightText); })[0];
  if (bad) {
    var why = svWeightNote(bad.weightText);
    out.push({ f: 'rows', m: why === 'Must be above zero'
      ? '“' + String(bad.weightText).trim() + '”: a weight must be above zero.'
      : '“' + String(bad.weightText).trim() + '” is not a plain number. Write a weight like 12.5.' });
  } else if (d.products.some(function (r) { return !(r.weightPct > 0); })) {
    /* a product with no weight yet is the next step, not a mistake: said
       quietly until Create is pressed or a weight box is left */
    var typedZero = d.products.some(function (r) { return isFinite(r.weightPct) && !(r.weightPct > 0); });
    out.push({ f: 'rows', soft: !typedZero, m: typedZero ? 'Every product needs a weight above zero.' : 'Give each product a weight.' });
  } else if (d.products.length && !svWeightsOk(ncTotal(d))) {
    out.push({ f: 'rows', m: 'Weights add up to ' + svTotalText(ncTotal(d)) + '%. They must make exactly 100%.' });
  }
  return out;
}
/* a field's problems, shown once it has something in it or Create was pressed */
function ncShown(d, f) {
  return ncProblems(d).filter(function (p) {
    if (p.f !== f) return false;
    if (nc.tried) return true;
    if (p.loud) return true;
    if (f === 'vars') return !!p.clash;
    if (f === 'rows') return d.products.length > 0 && (!p.soft || nc.touched);
    return false;
  });
}
/* what the summary and the narrow bar say is left */
function ncLeft(d) {
  var n = ncProblems(d).length;
  return n ? svPlural(n, 'thing') + ' still to do' : 'Ready to create';
}
function ncErrs(d, f) {
  var fe = repo.fieldError && ({ cat: ['category'], vars: ['variants', 'variant'], name: ['name'], rows: ['products', 'weights'] })[f];
  var server = fe && fe.indexOf(repo.fieldError.field) !== -1 ? '<p class="nc-err" role="alert">' + esc(repo.fieldError.message) + '</p>' : '';
  return ncShown(d, f).map(function (p) { return '<p class="nc-err" role="alert">' + esc(p.m) + '</p>'; }).join('') + server;
}
function ncCreateLabel(d) {
  var n = (d.variants || []).length;
  if (repo.saving) return 'Creating…';
  return n > 1 ? 'Create in ' + n + ' implementation types' : n === 1 ? 'Create sleeve in ' + d.variants[0] : 'Create sleeve';
}

function ncStartHtml(d) {
  var existing = d.startFrom === 'existing';
  return '<div class="nc-start"><span class="nc-start-l" id="ncStartL">Start from</span>'
    + '<span class="nc-seg" role="group" aria-labelledby="ncStartL">'
    + '<button type="button" data-ncstart="blank" aria-pressed="' + !existing + '">Blank</button>'
    + '<button type="button" data-ncstart="existing" aria-pressed="' + existing + '">An existing sleeve</button></span>'
    + '<span class="nc-start-h">' + (existing ? 'Copy the products and weights of a sleeve already in the library, then change what you need. Editions are never copied.'
                                              : 'Choose every product yourself.') + '</span></div>';
}
function ncCatsHtml(d) {
  var lib = repo.data.sleeves, fixed = repo.data.fixedCategories, under = (d.variants || [])[0] || repo.variant;
  return '<div class="nc-cats" id="ncCats" tabindex="-1" role="group" aria-label="Category">' + repo.data.categories.map(function (c) {
    var full = svCategoryFull(lib, fixed, repo.data.variants, c);
    var line = full ? 'Full: holds its one sleeve under every implementation type'
      : isFixed(c) ? 'Fixed: one sleeve per implementation type'
      : svPlural(sleevesIn(under, c).length, 'sleeve') + ' under ' + under;
    return '<button type="button" class="nc-cat" data-nccat="' + esc(c) + '" style="--cc:' + svColour(c) + '" aria-pressed="' + (d.category === c) + '"'
      + (full ? ' disabled' : '') + '><b>' + esc(c) + '</b><small>' + esc(line) + '</small></button>';
  }).join('') + '</div>';
}
function ncTypesHtml(d) {
  return '<div class="nc-types" id="ncTypes" tabindex="-1" role="group" aria-label="Implementation types">' + repo.data.variants.map(function (v) {
    var a = ncAvail(v, d), on = (d.variants || []).indexOf(v) !== -1;
    var here = d.category ? sleevesIn(v, d.category).length : 0;
    /* the short reason on the box; the whole sentence, with what to do, goes
       under the boxes and in the summary when a ticked type is the one */
    var line = !a.ok ? a.short : !d.category ? 'Choose a category first' : svPlural(here, 'sleeve') + ' in ' + d.category + ' today';
    return '<label class="nc-type' + (!a.ok ? (on ? ' clash' : ' off') : '') + (on ? ' on' : '') + '">'
      + '<input type="checkbox" data-ncvar="' + esc(v) + '"' + (on ? ' checked' : '') + (!a.ok && !on ? ' disabled' : '') + '>'
      + '<span><b>' + esc(v) + '</b><small>' + esc(line) + '</small></span></label>';
  }).join('') + '</div>';
}
function ncSourcesHtml(d) {
  if (!d.category) return '<p class="nc-none">Choose a category first to see the sleeves it holds.</p>';
  var list = svSources(repo.data.sleeves, { category: d.category, variants: d.variants, allV: nc.allV, query: nc.srcq, order: repo.data.variants });
  if (!list.length) {
    return '<p class="nc-none">No sleeve in ' + esc(d.category) + (nc.srcq.trim() ? ' matches “' + esc(nc.srcq.trim()) + '”' : '')
      + (nc.allV ? '.' : ' under the types chosen. Tick “Every implementation type” to look wider.') + '</p>';
  }
  return list.map(function (e) {
    var s = e.s, on = d.sourceId != null && e.ids.indexOf(d.sourceId) !== -1;
    return '<button type="button" class="nc-src" data-ncsrc="' + s.id + '" aria-pressed="' + on + '">'
      + '<b>' + esc(s.name) + (s.label ? ' <span class="sv-chip info">Edition: ' + esc(s.label) + '</span>' : '') + '</b>'
      + '<small>' + esc(e.types.join(', ')) + '</small>'
      + '<span class="nc-src-p">' + esc((s.products || []).map(function (r) {
          return (r.product ? r.product.name : r.productId) + ' ' + money2(r.weight * 100) + '%';
        }).join(' · ')) + '</span></button>';
  }).join('');
}
function ncSourceCount(d) {
  if (!d.category) return '';
  var n = svSources(repo.data.sleeves, { category: d.category, variants: d.variants, allV: nc.allV, query: nc.srcq, order: repo.data.variants }).length;
  return svPlural(n, 'sleeve') + ' in ' + d.category + (nc.allV || !(d.variants || []).length ? '' : ' under ' + d.variants.join(', '));
}
function ncPickerHtml(d) {
  if (d.startFrom !== 'existing') return '';
  var ask = '';
  if (nc.ask != null) {
    var a = sleeveById(nc.ask);
    if (a) {
      ask = '<div class="nc-ask" role="alertdialog" aria-labelledby="ncAskT"><b id="ncAskT">Replace your ' + svPlural(d.products.length, 'product')
        + ' with ' + esc(svPossessive(a.name)) + ' ' + a.products.length + '?</b><span>Your weights are replaced too.</span>'
        + '<span class="spacer"></span><button type="button" class="btn" data-nckeepmine>Keep mine</button>'
        + '<button type="button" class="btn btn-primary" data-ncreplace>Replace</button></div>';
    }
  }
  return '<section class="sv-sect nc-pick"><h4 class="sv-sect-h">Start from which sleeve?</h4><div class="sv-sect-b">'
    + '<div class="nc-pick-t"><label class="repo-find sv-find nc-find"><span aria-hidden="true">⌕</span>'
    + '<input type="search" id="ncSrcQ" placeholder="Search names and products…" autocomplete="off" aria-label="Search the sleeves to start from" value="' + esc(nc.srcq) + '"></label>'
    + '<label class="nc-chk"><input type="checkbox" data-ncallv' + (nc.allV ? ' checked' : '') + '> Every implementation type</label>'
    + '<span class="sv-hint" id="ncSrcCount" role="status">' + esc(ncSourceCount(d)) + '</span></div>'
    + ask + '<div class="nc-srcs" id="ncSrcList">' + ncSourcesHtml(d) + '</div></div></section>';
}
function ncNameNote(d) {
  /* the same name under types not ticked. Spelled the same, it is one
     sleeve offered more widely; differing in capitals only, the library keeps
     them apart - so say so, and offer the spelling in use. Not shown while a
     ticked type already has the name: that is the clash, said above. */
  if (!d.category || !svNormName(d.name) || !(d.variants || []).length) return '';
  if ((d.variants || []).some(function (v) { return ncAvail(v, d).kind === 'taken'; })) return '';
  var hits = svNameElsewhere(repo.data.sleeves, repo.data.variants, d.variants, d.category, d.name);
  if (!hits.length) return '';
  var same = hits.filter(function (h) { return h.kind === 'same'; });
  var cased = hits.filter(function (h) { return h.kind === 'case'; });
  var out = '';
  if (same.length) {
    out += esc(svJoinAnd(same.map(function (h) { return h.variant; }))) + (same.length === 1 ? ' already has' : ' already have')
      + ' a sleeve called ' + esc(same[0].name) + ' in ' + esc(d.category)
      + '. PWAs see these as one sleeve, offered under every implementation type that has it; each keeps its own products.';
  }
  if (cased.length) {
    out += (out ? ' ' : '') + esc(svJoinAnd(cased.map(function (h) { return h.variant; }))) + (cased.length === 1 ? ' has' : ' have')
      + ' a sleeve called ' + esc(cased[0].name) + ' in ' + esc(d.category) + '. Spelled differently, this one will be a separate sleeve;'
      + ' to offer them as one, use the same spelling. <button type="button" class="nc-link" data-ncusename="' + esc(cased[0].name) + '">Use \u201c'
      + esc(cased[0].name) + '\u201d</button>';
  }
  return out;
}
function ncRowHtml(d, r, i, src, products) {
  var p = products[r.productId], row = p ? catRow(p.productId) : null;
  var facts = p ? [p.assetClass, p.vehicle, p.style, p.liquidity].filter(Boolean).join(' · ') : 'Not in the catalogue';
  var mark = svSourceMark(src, r);
  var why = svWeightNote(r.weightText), bad = !!why;
  var val = r.weightText != null ? r.weightText : (isFinite(r.weightPct) ? money2(r.weightPct) : '');
  return '<tr><td><b>' + esc(p ? p.name : r.productId) + '</b><span class="nc-mark" id="ncMark' + i + '">' + ncMarkHtml(mark) + '</span>'
    + '<small' + (p ? '' : ' class="warn"') + '>' + esc(facts) + '</small></td>'
    + '<td class="num">' + (p ? svPct(p.productCost) : '—') + '</td>'
    + '<td class="num">' + (p ? esc(catMoney(p.minimumInvestment)) : '—')
    + (row && row.tooBig ? ' <span class="sv-chip warn" title="Above the mandate open in the tool">above mandate</span>' : '') + '</td>'
    + '<td class="num"><input type="text" inputmode="decimal" class="nc-w" data-repoweight="' + i + '" aria-label="Weight of ' + esc(p ? p.name : r.productId) + ', percent"'
    + (bad ? ' aria-invalid="true"' : '') + ' value="' + esc(val) + '"><span class="nc-rowerr" id="ncRowErr' + i + '">' + esc(why) + '</span></td>'
    + '<td class="num"><button type="button" class="btn nc-rm" data-reporm="' + i + '" aria-label="Remove ' + esc(p ? p.name : r.productId) + '">Remove</button></td></tr>';
}
function ncMarkHtml(mark) {
  if (!mark) return '';
  return '<span class="nc-diff' + (mark === 'added' ? ' add' : '') + '">' + esc(mark) + '</span>';
}
function ncFootHtml(d) {
  var products = ncProducts(), t = ncTotal(d), ok = d.products.length && svWeightsOk(t);
  return '<tr><td>Total <span class="sv-hint">· weighted product cost ' + esc(svRowsCostText(d.products, products)) + '</span>'
    + (d.products.length > 1 ? ' <button type="button" class="nc-link" data-ncspread>Spread evenly</button>' : '') + '</td><td></td><td></td>'
    + '<td class="num ' + (ok ? 'ok' : 'bad') + '">' + svTotalText(t) + '% ' + (ok ? '✓' : '✗') + '</td><td></td></tr>';
}
function ncRemovedHtml(d) {
  var src = ncSource(d), gone = svSourceRemoved(src, d.products);
  return gone.length ? 'Removed from ' + esc(src.name) + ': ' + esc(gone.map(function (r) {
    return (r.product ? r.product.name : r.productId) + ' (' + money2(r.weight * 100) + '%)';
  }).join(', ')) : '';
}
function ncNotesHtml() {
  /* "give each a weight" has done its job once every product has one */
  var d = repo.draft;
  if (nc.addedNote && d && d.products.length && d.products.every(function (r) { return r.weightPct > 0; })) { nc.addedNote = false; nc.keptNote = ''; }
  return (nc.keptNote ? '<p class="nc-info" role="status">' + esc(nc.keptNote) + '</p>' : '')
    + (nc.lost ? '<p class="nc-lost" role="status">' + esc(nc.lost) + '</p>' : '');
}
function ncHoldsHtml(d) {
  var src = ncSource(d), products = ncProducts();
  var notes = '<div class="nc-holds-notes" id="ncNotes">' + ncNotesHtml() + '</div>';
  var add = '<button type="button" class="btn btn-primary nc-addbtn" data-ncadd>+ Add product</button>';
  if (!d.products.length) {
    return notes + '<div class="nc-empty" id="ncRows" tabindex="-1">No products yet. '
      + (d.startFrom === 'existing' ? 'Pick a sleeve above to copy its products, or ' : '') + add + '</div>' + ncErrs(d, 'rows');
  }
  return notes + '<div class="sv-ptwrap nc-ptwrap" id="ncRows" tabindex="-1"><table class="sv-pt nc-pt"><thead><tr><th scope="col">Product</th>'
    + '<th scope="col" class="num">Cost</th><th scope="col" class="num">Minimum</th><th scope="col" class="num">Weight %</th><th scope="col"><span class="sr-only">Remove</span></th></tr></thead>'
    + '<tbody>' + d.products.map(function (r, i) { return ncRowHtml(d, r, i, src, products); }).join('') + '</tbody>'
    + '<tfoot id="ncFoot">' + ncFootHtml(d) + '</tfoot></table></div>'
    + '<p class="nc-removed" id="ncRemoved">' + ncRemovedHtml(d) + '</p>'
    + '<div class="nc-addrow"><button type="button" class="btn" data-ncadd>+ Add product</button>'
    + '<span class="sv-hint">Search or browse the catalogue of ' + (repo.data.products || []).length + ' products</span></div>'
    + '<div id="ncRowsErr">' + ncErrs(d, 'rows') + '</div>';
}
/* the summary pinned beside the form: what Create will make, and what is left */
function ncSideHtml(d) {
  var outcomes = svOutcomes(repo.data.sleeves, repo.data.fixedCategories, repo.data.variants, d.variants, d.category, d.name);
  var made = '<ul class="nc-made">' + outcomes.map(function (o) {
    if (!o.chosen) {
      return '<li class="no"><span class="ic" aria-hidden="true">–</span><span>' + esc(o.variant)
        + '<small>' + esc(o.ok ? 'Not chosen' : o.short) + '</small></span></li>';
    }
    if (!o.ok) {
      return '<li class="bad"><span class="ic" aria-hidden="true">✗</span><span><b>' + esc(o.variant) + '</b><small>' + esc(o.reason) + '</small></span></li>';
    }
    return '<li class="yes"><span class="ic" aria-hidden="true">✓</span><span><b>' + esc(o.variant) + '</b><small>'
      + esc(d.name.trim() || 'The new sleeve') + ' in ' + esc(d.category || 'the category you choose') + '</small></span></li>';
  }).join('') + '</ul>';
  var src = ncSource(d), products = ncProducts();
  var changes = svSourceChanges(src, d.products);
  var based = src ? '<p class="nc-based">Based on <b>' + esc(src.name) + '</b> (' + esc(src.variant) + ') · '
    + (changes ? svPlural(changes, 'change') : 'no changes yet') + '. Once created it is a separate sleeve: later changes to '
    + esc(src.name) + ' will not change it.</p>' : '';
  var t = ncTotal(d), ok = d.products.length && svWeightsOk(t);
  var bar = d.products.length ? '<div class="nc-bar" aria-hidden="true">' + d.products.map(function (r, i) {
    return '<i style="width:' + Math.max(0, Math.min(100, isFinite(r.weightPct) ? r.weightPct : 0)) + '%;background:' + NC_SEG[i % NC_SEG.length] + '"></i>';
  }).join('') + '</div>' : '';
  var mix = d.products.length
    ? bar + '<p class="nc-mix">' + svPlural(d.products.length, 'product') + ' · total <b class="' + (ok ? 'ok' : 'bad') + '">' + svTotalText(t)
      + '%</b><br>Weighted product cost ' + esc(svRowsCostText(d.products, products)) + '</p>'
    : '<p class="nc-mix sv-hint">No products yet.</p>';
  var probs = ncProblems(d);
  var todo = [
    ['cat', 'Where: ' + (d.category || 'choose a category')],
    ['vars', 'For: ' + ((d.variants || []).length ? d.variants.join(', ') : 'choose implementation types')],
    ['name', 'Name: ' + (d.name.trim() || 'not given yet')],
    ['rows', 'Products: ' + (d.products.length ? svPlural(d.products.length, 'product') + ' · total ' + svTotalText(t) + '%' : 'none yet')]
  ].map(function (it) {
    var bad = probs.filter(function (p) { return p.f === it[0]; });
    var loud = bad.length && (nc.tried || bad[0].loud || (it[0] === 'vars' && bad[0].clash)
      || (it[0] === 'rows' && d.products.length && (!bad[0].soft || nc.touched)));
    /* an open item says what it needs in words: a product without a weight yet is "Give each product a weight" */
    var text = loud ? bad[0].m : (bad.length && bad[0].soft ? bad[0].m : it[1]);
    return '<li class="' + (bad.length ? (loud ? 'bad' : 'open') : 'done') + '"><span class="ic" aria-hidden="true">'
      + (bad.length ? (loud ? '!' : '○') : '✓') + '</span><span>' + esc(text) + '</span></li>';
  }).join('');
  var error = repo.error ? '<p class="nc-err" role="alert">' + esc(repo.error) + '</p>' : '';
  return '<div class="nc-side-s" id="ncSideS"><h4>What will be created</h4>' + made + based
    + '<h4>Mix</h4>' + mix
    + '<h4>Still to do</h4><ul class="nc-todo" id="ncTodo">' + todo + '</ul>' + error + '</div>'
    + '<div class="nc-go">' + ncGoHtml(d, probs) + '</div>';
}
function ncGoHtml(d, probs) {
  return '<button type="button" class="btn btn-primary nc-create" data-nccreate' + (probs.length ? ' aria-disabled="true" aria-describedby="ncTodo"' : '')
    + (repo.saving ? ' disabled' : '') + '>' + esc(ncCreateLabel(d)) + '</button>'
    + '<button type="button" class="btn" data-nccancel' + (repo.saving ? ' disabled' : '') + '>Cancel</button>';
}
/* narrow, the summary follows the form; this bar keeps Create in reach */
function ncBarHtml(d) {
  var probs = ncProblems(d);
  return '<span class="nc-mbar-s ' + (probs.length ? 'open' : 'ok') + '">' + esc(ncLeft(d)) + '</span>'
    + '<button type="button" class="btn btn-primary" data-nccreate' + (probs.length ? ' aria-disabled="true"' : '') + (repo.saving ? ' disabled' : '') + '>'
    + esc(ncCreateLabel(d)) + '</button>';
}
function svCreateHtml() {
  var d = repo.draft;
  return keptNoticeHtml()
    + '<div class="nc"><div class="nc-form">'
    + ncStartHtml(d)
    + svSection('Where it goes', 'one sleeve is created under each implementation type you tick',
        '<p class="nc-lab">Category</p>' + ncCatsHtml(d) + '<div id="ncCatsErr">' + ncErrs(d, 'cat') + '</div>'
        + '<p class="nc-lab">Implementation types</p><div id="ncTypesWrap">' + ncTypesHtml(d) + '</div><div id="ncTypesErr">' + ncErrs(d, 'vars') + '</div>')
    + ncPickerHtml(d)
    + svSection('Name and note', '', '<div class="repo-fld"><label for="repoName">Sleeve name <small>what PWAs pick in the implementation step</small></label>'
        + '<input type="text" id="repoName" maxlength="80" placeholder="For example: Passive Core" value="' + esc(d.name) + '"'
        + (repo.fieldError && repo.fieldError.field === 'name' ? ' aria-invalid="true"' : '') + '>'
        + '<span class="nc-count" id="ncNameCount" aria-live="polite">' + ncCountText(d) + '</span>'
        + '<div id="ncNameErr">' + ncErrs(d, 'name') + '</div><p class="nc-info nc-namenote" id="ncNameNote">' + ncNameNote(d) + '</p></div>'
        + '<div class="repo-fld"><label for="repoNote">Note to PWAs <small>optional · shown beside the sleeve in the picker</small></label>'
        + '<input type="text" id="repoNote" maxlength="240" placeholder="For example: SMA Only will be managed by GSAM" value="' + esc(d.note) + '"></div>')
    + svSection('What it holds', ncSource(d) ? 'marked against ' + ncSource(d).name + ' (' + ncSource(d).variant + ')' : '', '<div id="ncHolds">' + ncHoldsHtml(d) + '</div>')
    + '<p class="nc-later"><b>Editions come after.</b> Every portfolio gets this one version. To give some portfolios, such as GBP ones, a different mix, '
    + 'create the sleeve, then use “+ Add an edition” on its page.</p>'
    + '</div><aside class="nc-side" id="ncSide" aria-label="What will be created">' + ncSideHtml(d) + '</aside></div>'
    + '<div class="nc-mbar" id="ncBar">' + ncBarHtml(d) + '</div>';
}
function ncCountText(d) {
  var n = svNormName(d.name).length;
  return n >= 60 ? n + ' of 80 characters' : '';
}
/* typing redraws what depends on it, never the field being typed in */
function ncRefresh() {
  var d = repo.draft; if (!d || !d.create || !document.getElementById('ncSide')) return;
  /* only what changed is rewritten: an alert re-inserted on every keystroke
     is read out on every keystroke (D157 review) */
  var probe = document.createElement('div');
  var set = function (id, html) {
    var el = document.getElementById(id); if (!el) return;
    probe.innerHTML = html;
    if (probe.innerHTML !== el.innerHTML) el.innerHTML = html;
  };
  /* the summary's scroll and the focus inside it survive */
  var sideS = document.getElementById('ncSideS'), top = sideS ? sideS.scrollTop : 0;
  var probs = ncProblems(d);
  set('ncSideS', (function () { probe.innerHTML = ncSideHtml(d); var x = probe.querySelector('#ncSideS'); return x ? x.innerHTML : ''; })());
  var go = document.querySelector('#ncSide .nc-go'); if (go) {
    var b = go.querySelector('[data-nccreate]');
    if (b) { b.textContent = ncCreateLabel(d); if (probs.length) b.setAttribute('aria-disabled', 'true'); else b.removeAttribute('aria-disabled'); }
  }
  if (sideS) sideS.scrollTop = top;
  var bar = document.getElementById('ncBar');
  if (bar) {
    var st = bar.querySelector('.nc-mbar-s'); if (st) { st.textContent = ncLeft(d); st.className = 'nc-mbar-s ' + (probs.length ? 'open' : 'ok'); }
    var bb = bar.querySelector('[data-nccreate]'); if (bb) { bb.textContent = ncCreateLabel(d); if (probs.length) bb.setAttribute('aria-disabled', 'true'); else bb.removeAttribute('aria-disabled'); }
  }
  set('ncTypesWrap', ncTypesHtml(d));
  set('ncTypesErr', ncErrs(d, 'vars'));
  set('ncNameErr', ncErrs(d, 'name'));
  set('ncNameNote', ncNameNote(d));
  var count = document.getElementById('ncNameCount'); if (count && count.textContent !== ncCountText(d)) count.textContent = ncCountText(d);
  set('ncFoot', d.products.length ? ncFootHtml(d) : '');
  set('ncRowsErr', ncErrs(d, 'rows'));
  set('ncRemoved', ncRemovedHtml(d));
  if (nc.addedNote) set('ncNotes', ncNotesHtml());
  var src = ncSource(d);
  d.products.forEach(function (r, i) {
    set('ncMark' + i, ncMarkHtml(svSourceMark(src, r)));
    var e = document.getElementById('ncRowErr' + i), why = svWeightNote(r.weightText);
    if (e && e.textContent !== why) e.textContent = why;
  });
}
/* starting from a sleeve: its products and weights, never its name, note or editions */
function ncUseSource(id) {
  var d = repo.draft, s = sleeveById(id); if (!d || !s) return;
  d.sourceId = id; d.sourceIds = ncCopyIds(s); d.sourceName = s.name;
  d.products = s.products.map(function (r) { return { productId: r.productId, weightPct: svPctOf(r.weight) }; });
  nc.ask = null; nc.lost = ''; nc.keptNote = ''; nc.touched = false; repo.fieldError = null;
  markDirty();
  /* to the name, unless it has one already */
  var named = !!svNormName(d.name);
  sv.focus = named ? '[data-ncsrc="' + id + '"]' : '#repoName';
  render();
  App.announce('polite', 'Copied ' + svPlural(d.products.length, 'product') + ' from ' + s.name + '.' + (named ? '' : ' Give the new sleeve a name.'));
}
function ncPick(id) {
  var d = repo.draft; if (!d) return;
  var src = ncSource(d);
  /* products and weights exactly the picked sleeve's are nothing to lose */
  var unchanged = svRowsMatch(d.products, sleeveById(id)) || (src ? svSourceChanges(src, d.products) === 0 : !d.products.length);
  var action = svPickAction(d.products.length, unchanged, id, d.sourceId);
  if (action === 'same') return;
  if (action === 'copy') { ncUseSource(id); return; }
  nc.ask = id; sv.focus = '[data-nckeepmine]'; render();
}
function ncSetStart(to) {
  var d = repo.draft; if (!d || d.startFrom === to) return;
  var src = ncSource(d);
  if (to === 'blank' && src) {
    /* the products stay - nothing is thrown away unless asked - and the
       comparison stops, saying so */
    nc.keptNote = d.products.length ? 'Kept your ' + svPlural(d.products.length, 'product') + '. They are no longer compared with ' + src.name + '.' : '';
    d.sourceId = null; d.sourceIds = null;
  }
  if (to === 'existing') nc.keptNote = '';
  nc.ask = null; nc.lost = '';
  d.startFrom = to;
  try { window.localStorage.setItem(NC_START_KEY, to); } catch (e) { /* private window */ }
  sv.focus = '[data-ncstart="' + to + '"]';
  render();
}
function ncSetCategory(c) {
  var d = repo.draft; if (!d || d.category === c) return;
  var src = ncSource(d);
  if (src && src.category !== c) {
    nc.lost = src.name + ' is a ' + src.category + ' sleeve, and this one is now in ' + c + '. Your '
      + svPlural(d.products.length, 'product') + ' stay in the form but are no longer compared with it.';
    nc.keptNote = ''; d.sourceId = null; d.sourceIds = null;
  }
  nc.ask = null;
  d.category = c;
  /* the breadcrumb's category is the form's (D157 review) */
  repo.category = c;
  var lib = repo.data.sleeves, fixed = repo.data.fixedCategories;
  /* the types stay as the desk set them, less any the category cannot take */
  d.variants = (d.variants || []).filter(function (v) { return svAvail(lib, fixed, v, c, '').ok; });
  markDirty(); repo.fieldError = null;
  sv.focus = '[data-nccat="' + svCssEscape(c) + '"]';
  render();
}
function ncToggleType(v, on) {
  var d = repo.draft; if (!d) return;
  var picked = (d.variants || []).filter(function (x) { return x !== v; });
  if (on && ncAvail(v, d).ok) picked.push(v);
  d.variants = repo.data.variants.filter(function (x) { return picked.indexOf(x) !== -1; });
  /* a source the ticked types no longer show stops being compared, the way
     moving category does; a copy under a type still ticked takes its place */
  var src = ncSource(d);
  if (src && !nc.allV && d.variants.length && d.variants.indexOf(src.variant) === -1) {
    var still = (d.sourceIds || [src.id]).map(sleeveById).filter(function (x) { return x && d.variants.indexOf(x.variant) !== -1; })[0];
    if (still) d.sourceId = still.id;
    else {
      nc.lost = src.name + ' is offered under ' + src.variant + ', which is no longer ticked. Your ' + svPlural(d.products.length, 'product')
        + ' stay in the form but are no longer compared with it.';
      nc.keptNote = ''; d.sourceId = null; d.sourceIds = null;
    }
  }
  markDirty(); repo.fieldError = null;
  sv.focus = '[data-ncvar="' + svCssEscape(v) + '"]';
  render();
}
function ncCreate() {
  var d = repo.draft; if (!d || repo.saving) return;
  var probs = ncProblems(d);
  if (probs.length) {
    nc.tried = true; nc.touched = true;
    var at = { cat: '#ncCats', vars: '#ncTypes', name: '#repoName', rows: '#ncRows' }[probs[0].f];
    sv.focus = at + '||[data-nccreate]';
    render();
    App.announce('assertive', 'Not created yet. ' + probs[0].m);
    return;
  }
  saveDraft();
}
/* where Cancel returns: where New sleeve was pressed */
function ncBack() {
  var b = nc.back || {};
  var open = b.sleeveId != null ? sleeveById(b.sleeveId) : null;
  if (open) {
    return { to: open.id, variant: open.variant, category: open.category,
             sv: sv.mode === 'table' ? { drawer: true } : { level: 2 } };
  }
  if (sv.mode === 'table') return { sv: { drawer: false } };
  return { sv: { level: b.level === 1 ? 1 : 0 }, category: b.level === 1 ? b.category : null };
}
function ncCancel() {
  if (repo.saving) return;                   /* a Create in flight lands first */
  var back = ncBack();
  sv.focus = back.to != null ? (sv.mode === 'table' ? '#svDrawerTitle' : '#svTitle')
    : sv.mode === 'table' ? '[data-reponew]' : (back.sv.level === 1 ? '#svCatTitle' : '[data-reponew]');
  if (repo.dirty) { goTo(back); return; }
  applyTarget(back);
  App.announce('polite', 'Cancelled. Nothing was created.');
}

/* the Add a product dialog: search, or browse this category's products or
   the whole catalogue; tick one or several and add them in one go */
function ncOpenDialog(from) {
  var d = repo.draft; if (!d) return;
  var used = d.category ? ncCatalogued(svCategoryProducts(repo.data.sleeves, d.category)) : [];
  nc.dlg = { q: '', scope: used.length ? 'cat' : 'all', vehicle: '', style: '', source: '', sort: 'cost', dir: 'asc', sel: [],
             ret: from || '[data-ncadd]', nudge: false };
  sv.focus = '#ncDlgQ';
  render();
}
function ncCloseDialog() {
  if (!nc.dlg) return;
  var ret = nc.dlg.ret; nc.dlg = null;
  sv.focus = ret + '||[data-ncadd]';
  render();
}
function ncCatalogued(ids) { var p = ncProducts(); return (ids || []).filter(function (id) { return !!p[id]; }); }
function ncDlgPool(d) { return nc.dlg.scope === 'cat' && d.category ? ncCatalogued(svCategoryProducts(repo.data.sleeves, d.category)) : null; }
function ncDlgHits(d) {
  var g = nc.dlg;
  return svAddHits(repo.data.products, { query: g.q, pool: ncDlgPool(d), vehicle: g.vehicle, style: g.style, source: g.source, sort: g.sort, dir: g.dir });
}
function ncDlgCountText(d, n) {
  var pool = ncDlgPool(d);
  return pool ? n + ' of ' + svPlural(pool.length, 'product') + ' ' + d.category + ' uses'
              : n + ' of ' + svPlural((repo.data.products || []).length, 'product');
}
function ncDlgRowsHtml(d) {
  var hits = ncDlgHits(d), had = {}, g = nc.dlg;
  d.products.forEach(function (r) { had[r.productId] = 1; });
  var pool = ncDlgPool(d);
  if (pool && !pool.length) {
    return '<div class="nc-dlg-empty"><p>No sleeve in ' + esc(d.category) + ' holds a product yet, so there is nothing to show from it.</p><p>'
      + '<button type="button" class="btn" data-ncdscope="all">Look in the whole catalogue</button></p></div>';
  }
  if (!hits.length) {
    var filtered = g.q.trim() || g.vehicle || g.style || g.source;
    return '<div class="nc-dlg-empty"><p>No product matches' + (g.q.trim() ? ' “' + esc(g.q.trim()) + '”' : ' these filters')
      + (ncDlgPool(d) ? ' among the products ' + esc(d.category) + ' uses' : '') + '.</p><p>'
      + (filtered ? '<button type="button" class="btn" data-ncdclear>Clear the search and filters</button>' : '')
      + (ncDlgPool(d) ? '<button type="button" class="btn" data-ncdscope="all">Look in the whole catalogue</button>' : '') + '</p></div>';
  }
  var th = function (k, label, num) {
    var on = g.sort === k;
    return '<th scope="col"' + (num ? ' class="num"' : '') + ' aria-sort="' + (on ? (g.dir === 'desc' ? 'descending' : 'ascending') : 'none') + '">'
      + '<button type="button" data-ncdsort="' + k + '">' + label + '<span class="sv-arrow" aria-hidden="true">' + (on ? (g.dir === 'desc' ? '▼' : '▲') : '') + '</span></button></th>';
  };
  return '<table class="nc-at"><caption class="sr-only">Products in the catalogue</caption><thead><tr><th scope="col" class="tick"><span class="sr-only">Choose</span></th>'
    + th('name', 'Product') + th('class', 'Asset class') + '<th scope="col">Vehicle</th><th scope="col">Style</th>'
    + '<th scope="col" class="nc-hn">Source</th><th scope="col" class="nc-hn">Liquidity</th><th scope="col" class="nc-hn">Currency</th>'
    + th('cost', 'Cost', true) + th('min', 'Minimum', true) + '</tr></thead><tbody>'
    + hits.map(function (p) {
        var isHad = !!had[p.productId], on = g.sel.indexOf(p.productId) !== -1;
        return '<tr class="' + (isHad ? 'had' : on ? 'on' : '') + '"' + (isHad ? '' : ' data-ncdrow="' + esc(p.productId) + '"') + '>'
          + '<td class="tick">' + (isHad ? '<span class="nc-had">Added</span>'
              : '<input type="checkbox" data-ncdtick="' + esc(p.productId) + '"' + (on ? ' checked' : '') + ' aria-label="Choose ' + esc(p.name) + '">') + '</td>'
          + '<td><b>' + esc(p.name) + '</b>' + (p.ticker && !/^[\s\u2014\u2013-]*$/.test(p.ticker) ? '<small>' + esc(p.ticker) + '</small>' : '') + '</td>'
          + '<td>' + esc(p.assetClass || '') + '</td><td>' + esc(p.vehicle || '') + '</td><td>' + esc(p.style || '') + '</td>'
          + '<td class="nc-hn">' + esc(p.source || '') + '</td><td class="nc-hn">' + esc(p.liquidity || '') + '</td><td class="nc-hn">' + esc(p.exposureCurrency || '') + '</td>'
          + '<td class="num">' + svPct(p.productCost) + '</td><td class="num">' + esc(catMoney(p.minimumInvestment)) + '</td></tr>';
      }).join('') + '</tbody></table>';
}
function ncDlgFootHtml() {
  var n = nc.dlg.sel.length;
  /* a click outside with products ticked does not throw the ticks away: it says how to finish */
  var said = nc.dlg.nudge && n ? '<b>' + svPlural(n, 'product') + ' ticked.</b> Add ' + (n === 1 ? 'it' : 'them') + ', or press Cancel to close without adding.'
    : n ? '<b>' + svPlural(n, 'product') + ' chosen</b>' : 'Tick the products to add, or click a row.';
  return '<span class="nc-dlg-n' + (nc.dlg.nudge && n ? ' nudge' : '') + '" role="status">' + said + '</span>'
    + '<span class="spacer"></span><button type="button" class="btn" data-ncdlgclose>Cancel</button>'
    + '<button type="button" class="btn btn-primary" data-ncdadd' + (n ? '' : ' disabled') + '>' + (n ? 'Add ' + svPlural(n, 'product') : 'Add products') + '</button>';
}
function ncDialogHtml() {
  var d = repo.draft; if (!nc.dlg || !d || !d.create) return '';
  var g = nc.dlg, products = repo.data.products || [];
  var values = function (field) {
    var out = []; products.forEach(function (p) { var v = p[field]; if (v && out.indexOf(v) === -1) out.push(v); });
    return out.sort();
  };
  var sel = function (key, field, label, cur) {
    return '<label class="nc-f">' + esc(label) + ' <select data-ncdf="' + key + '"><option value="">Any</option>'
      + values(field).map(function (v) { return '<option value="' + esc(v) + '"' + (v === cur ? ' selected' : '') + '>' + esc(v) + '</option>'; }).join('')
      + '</select></label>';
  };
  var scope = d.category ? '<span class="nc-seg" role="group" aria-label="Which products">'
    + '<button type="button" data-ncdscope="cat" aria-pressed="' + (g.scope === 'cat') + '">Only products ' + esc(d.category) + ' uses</button>'
    + '<button type="button" data-ncdscope="all" aria-pressed="' + (g.scope !== 'cat') + '">Whole catalogue</button></span>' : '';
  var hits = ncDlgHits(d).length;
  return '<div class="nc-dlg-scrim" data-ncdlgscrim></div>'
    + '<div class="nc-dlg" role="dialog" aria-modal="true" aria-labelledby="ncDlgT">'
    + '<div class="nc-dlg-h"><h3 id="ncDlgT">Add a product</h3><span class="sv-hint">to ' + esc(d.name.trim() || 'the new sleeve') + '</span>'
    + '<span class="spacer"></span><button type="button" class="btn" data-ncdlgclose>Close</button></div>'
    + '<div class="nc-dlg-f"><div class="nc-dlg-r"><label class="repo-find sv-find nc-find"><span aria-hidden="true">⌕</span>'
    + '<input type="search" id="ncDlgQ" autocomplete="off" placeholder="Search by name, asset class, vehicle or product id…" aria-label="Search the catalogue" value="' + esc(g.q) + '"></label>'
    + scope + '</div><div class="nc-dlg-r">' + sel('vehicle', 'vehicle', 'Vehicle', g.vehicle) + sel('style', 'style', 'Style', g.style) + sel('source', 'source', 'Source', g.source)
    + '<span class="spacer"></span><span class="sv-hint" id="ncDlgCount" role="status">' + esc(ncDlgCountText(d, hits)) + '</span></div></div>'
    + '<div class="nc-dlg-b" id="ncDlgBody">' + ncDlgRowsHtml(d) + '</div>'
    + '<div class="nc-dlg-ft" id="ncDlgFoot">' + ncDlgFootHtml() + '</div></div>';
}
function ncDlgRefresh() {
  var d = repo.draft; if (!nc.dlg || !d) return;
  var body = document.getElementById('ncDlgBody'); if (body) body.innerHTML = ncDlgRowsHtml(d);
  var count = document.getElementById('ncDlgCount'); if (count) count.textContent = ncDlgCountText(d, ncDlgHits(d).length);
  var foot = document.getElementById('ncDlgFoot'); if (foot) foot.innerHTML = ncDlgFootHtml();
}
function ncDlgTick(id, on) {
  var g = nc.dlg; if (!g) return;
  g.sel = g.sel.filter(function (x) { return x !== id; });
  if (on) g.sel.push(id);
  ncDlgRefresh();
  var box = document.querySelector('#repoDialog [data-ncdtick="' + svCssEscape(id) + '"]'); if (box) box.focus();
}
function ncDlgAdd() {
  var d = repo.draft, g = nc.dlg; if (!d || !g || !g.sel.length) return;
  var first = d.products.length, n = g.sel.length;
  d.products = svAddRows(d.products, g.sel);
  nc.dlg = null; repo.fieldError = null;
  markDirty();
  nc.keptNote = 'Added ' + svPlural(n, 'product') + '. Give ' + (n === 1 ? 'it' : 'each') + ' a weight.'; nc.addedNote = true;
  sv.focus = '[data-repoweight="' + first + '"]';
  render();
  App.announce('polite', nc.keptNote);
}

/* ---- the table ------------------------------------------------------------ */
var SV_COLUMNS = [
  ['name', 'Sleeve'], ['category', 'Category'], ['variant', 'Implementation type'],
  ['products', 'Products', 'num'], ['vehicles', 'Vehicles'], ['cost', 'Weighted cost', 'num'],
  ['status', 'Status'], ['created', 'This version created']
];
function svRows() {
  return svSort(svFilter(repo.data.sleeves, { variant: sv.fv, category: sv.fc, query: repo.query }), sv.sort, svOrder());
}
function svCountText(n) {
  var all = (repo.data.sleeves || []).length;
  return n === all ? svPlural(all, 'sleeve') : n + ' of ' + svPlural(all, 'sleeve');
}
function svTbodyHtml(rows) {
  if (!rows.length) {
    return '<tr><td colspan="' + (SV_COLUMNS.length + 1) + '" class="sv-none">No sleeve matches. Clear the search or a filter.</td></tr>';
  }
  var full = sv.sel.length >= SV_COMPARE_MAX;
  return rows.map(function (s) {
    var on = sv.sel.indexOf(s.id) !== -1;
    var open = sv.drawer && repo.draft && repo.draft.id === s.id;
    return '<tr data-svrow="' + s.id + '" data-svmenu="' + s.id + '"' + (open ? ' class="on" aria-current="true"' : '') + '>'
      + '<td class="tick"><input type="checkbox" data-svtick="' + s.id + '"' + (on ? ' checked' : '') + (full && !on ? ' disabled' : '')
      + ' aria-label="Compare ' + esc(s.name + (s.label ? ' (' + s.label + ')' : '') + ', ' + s.variant) + '"></td>'
      + '<td class="nm"><button type="button" class="sv-rowbtn" data-svrow="' + s.id + '">' + esc(s.name) + '</button>' + svEditionChip(s) + '</td>'
      + '<td><span class="sv-cell">' + svSw(s.category) + esc(s.category) + '</span>' + (isFixed(s.category) ? ' <span class="sv-chip mute">Fixed</span>' : '') + '</td>'
      + '<td class="sv-nw">' + esc(s.variant) + '</td>'
      + '<td class="num">' + s.products.length + '</td>'
      + '<td class="sv-nw">' + esc(svVehicles(s).join(', ')) + '</td>'
      + '<td class="num">' + esc(svCostText(s)) + '</td>'
      + '<td>' + svStatusChip(s) + '</td>'
      + '<td class="sv-when">' + esc(shortDateTime(s.createdAt)) + '</td></tr>';
  }).join('');
}
function svSelect(attr, label, all, values, chosen) {
  return '<label class="sv-sel"><span class="sr-only">' + esc(label) + '</span><select ' + attr + ' aria-label="' + esc(label) + '">'
    + '<option value="">' + esc(all) + '</option>'
    + values.map(function (v) { return '<option value="' + esc(v) + '"' + (v === chosen ? ' selected' : '') + '>' + esc(v) + '</option>'; }).join('')
    + '</select></label>';
}
/* The comparison lines the ticked sleeves up product by product - one row
   per product any of them holds, a column per sleeve, a row whose weights
   differ marked - in a panel above the table that closes, rather than a
   page over it (D156 QA). */
function svCompareHtml() {
  var list = sv.sel.map(sleeveById).filter(Boolean);
  if (list.length < 2) return '';
  var names = [], weights = {};
  list.forEach(function (s, i) {
    s.products.forEach(function (r) {
      var key = r.productId;
      if (!weights[key]) { weights[key] = { name: r.product ? r.product.name : r.productId, w: [] }; names.push(key); }
      weights[key].w[i] = r.weight * 100;
    });
  });
  var head = list.map(function (s) {
    return '<th scope="col"><span class="sv-cmp-n">' + esc(s.name) + '</span>' + svEditionChip(s)
      + '<span class="sv-cmp-w">' + svWhere(s.variant, s.category) + '</span>'
      + '<button type="button" class="btn sv-cmp-open" data-svopen="' + s.id + '">Open</button></th>';
  }).join('');
  var body = names.map(function (key) {
    var row = weights[key], vals = list.map(function (s, i) { return row.w[i]; });
    var differ = vals.some(function (v) { return v === undefined || Math.abs(v - vals[0]) > 1e-9; });
    return '<tr' + (differ ? ' class="diff"' : '') + '><th scope="row">' + esc(row.name) + '</th>'
      + vals.map(function (v) { return '<td class="num">' + (v === undefined ? '<span class="sv-hint">not held</span>' : money2(v) + '%') + '</td>'; }).join('') + '</tr>';
  }).join('');
  return '<section class="sv-cmp" aria-labelledby="svCmpTitle"><div class="sv-cmp-h">'
    + '<h3 id="svCmpTitle" tabindex="-1">Comparing ' + svPlural(list.length, 'sleeve') + '</h3>'
    + '<span class="sv-hint">Rows whose weights differ are marked.</span>'
    + '<span class="spacer"></span><button type="button" class="btn" data-svcmpclose>Close the comparison</button></div>'
    + '<div class="sv-cmp-b"><table class="sv-cmpt"><thead><tr><th scope="col">Product</th>' + head + '</tr></thead><tbody>' + body + '</tbody>'
    + '<tfoot><tr><th scope="row">Weighted product cost</th>' + list.map(function (s) { return '<td class="num">' + esc(svCostText(s)) + '</td>'; }).join('') + '</tr>'
    + '<tr><th scope="row">Vehicles</th>' + list.map(function (s) { return '<td>' + esc(svVehicles(s).join(', ')) + '</td>'; }).join('') + '</tr>'
    + '<tr><th scope="row">Status</th>' + list.map(function (s) { return '<td>' + svStatusChip(s) + '</td>'; }).join('') + '</tr>'
    + '</tfoot></table></div></section>';
}
function svDrawerHtml() {
  if (!sv.drawer || !repo.draft) return '';
  var d = repo.draft, editing = svEditing(d, sv.editing, repo.dirty);
  var title = d.create ? 'New sleeve' : d.edition ? 'New edition of ' + d.name : (editing ? 'Editing ' : '') + svDraftName();
  return '<div class="sv-scrim" data-svclose></div>'
    + '<aside class="sv-drawer' + (d.create ? ' is-create' : '') + '" role="dialog" aria-labelledby="svDrawerTitle">'
    + '<div class="sv-drawer-h"><h3 id="svDrawerTitle" tabindex="-1">' + esc(title) + '</h3>'
    + '<button type="button" class="btn" data-svclose>Close</button></div>'
    + '<div class="sv-drawer-b sv-scroll" id="svDrawerBody">' + svPageHtml(true) + '</div>'
    + (editing && !d.create ? '<div class="sv-drawer-f">' + svEditControlsHtml() + '</div>' : '')
    + '</aside>';
}
function svTableViewHtml() {
  var rows = svRows();
  var head = SV_COLUMNS.map(function (c) {
    var on = sv.sort && sv.sort.key === c[0];
    var dirWords = c[2] ? (sv.sort && sv.sort.dir === 'desc' ? 'highest first' : 'lowest first')
                        : (sv.sort && sv.sort.dir === 'desc' ? 'Z to A' : 'A to Z');
    return '<th scope="col"' + (c[2] ? ' class="num"' : '') + ' aria-sort="' + (on ? (sv.sort.dir === 'desc' ? 'descending' : 'ascending') : 'none') + '">'
      + '<button type="button" data-svsort="' + c[0] + '"' + (on ? ' title="Sorted ' + dirWords + '"' : '') + '>' + esc(c[1])
      + '<span class="sv-arrow" aria-hidden="true">' + (on ? (sv.sort.dir === 'desc' ? '\u25bc' : '\u25b2') : '') + '</span></button></th>';
  }).join('');
  var tools = '<div class="sv-tools">'
    + svSelect('data-svfv', 'Implementation type', 'All implementation types', repo.data.variants, sv.fv)
    + svSelect('data-svfc', 'Category', 'All categories', repo.data.categories, sv.fc)
    + '<span class="sv-count" id="svCount" role="status">' + svCountText(rows.length) + '</span>'
    + (sv.sort ? '<button type="button" class="btn sv-libord" data-svsort="">Library order</button>' : '')
    + '<span class="spacer"></span>'
    + (sv.sel.length
        ? '<span class="sv-hint">' + sv.sel.length + ' ticked</span><button type="button" class="btn" data-svclearticks>Clear ticks</button>'
          + '<button type="button" class="btn" data-svcompare' + (sv.sel.length < 2 ? ' disabled title="Tick two or three sleeves to compare"' : '') + '>Compare ' + sv.sel.length + '</button>'
        : '<span class="sv-hint">Tick up to three rows to compare them</span>')
    + '</div>';
  return '<div class="sv-tarea">'
    + '<div class="sv-tmain"' + (sv.drawer ? ' inert' : '') + '>' + tools
    + (sv.compare ? svCompareHtml() : '')
    + '<div class="sv-tblwrap" id="svTblWrap"><table class="sv-tbl"><caption class="sr-only">Sleeves in the library</caption>'
    + '<thead><tr><th scope="col" class="tick"><span class="sr-only">Compare</span></th>' + head + '</tr></thead>'
    + '<tbody id="svTbody">' + svTbodyHtml(rows) + '</tbody></table></div>'
    + '</div>' + svDrawerHtml() + '</div>';
}

function sleevesViewHtml() {
  var body = sv.mode === 'table'
    ? svTableViewHtml()
    : '<div class="sv-body sv-scroll" id="svBody">' + svCardsBodyHtml() + '</div>';
  return '<div class="sv is-' + sv.mode + '">' + '<div class="sv-top">' + svBarHtml() + svKeptOfferHtml() + '</div>'
    + body + ncDialogHtml() + '</div>' + sleeveMenuHtml();
}

/* ---- moving about --------------------------------------------------------- */
function svCardsKey() { return [repo.variant, sv.level, repo.category, repo.draft && repo.draft.id].join('|'); }
function svSwitchMode(to) {
  if (to === sv.mode || (to !== 'cards' && to !== 'table')) return;
  /* not while unsaved changes are holding a move: the notice answers first */
  if (repo.leaving) { sv.focus = '[data-repokeep]'; render(); return; }
  if (to === 'table') {
    var category = repo.draft && repo.draft.create && sv.level === 2 ? repo.draft.category : repo.category;
    var t = svToTable({ variant: repo.variant, category: category, level: sv.level, query: repo.query });
    /* back from a look at the cards that changed nothing: the table as it was */
    var was = sv.lastTable;
    if (was && was.cardsKey === svCardsKey() && !repo.query.trim()) { t.fv = was.fv; t.fc = was.fc; sv.compare = was.compare; }
    sv.fv = t.fv; sv.fc = t.fc; sv.drawer = t.drawer && !!repo.draft;
    if (sv.drawer) sv.compare = false;
    sv.returnTo = repo.draft && repo.draft.id;
  } else {
    var open = sv.drawer ? (svOpenEntry() || (repo.draft ? { variant: repo.draft.edition ? repo.draft.edition.variant : repo.variant,
      category: repo.draft.create ? repo.draft.category : repo.category } : null)) : null;
    var c = svToCards({ fv: sv.fv, fc: sv.fc, drawer: sv.drawer }, open);
    var keep = { fv: sv.fv, fc: sv.fc, compare: sv.compare };
    if (c.variant && repo.data.variants.indexOf(c.variant) !== -1) repo.variant = c.variant;
    if (c.category && repo.data.categories.indexOf(c.category) !== -1) repo.category = c.category;
    sv.level = c.level; sv.drawer = false;
    keep.cardsKey = svCardsKey(); sv.lastTable = keep;
  }
  sv.mode = to;
  try { window.localStorage.setItem(SV_VIEW_KEY, to); } catch (e) { /* private window */ }
  sv.focus = '[data-svmode="' + to + '"]';
  render();
  App.announce('polite', (to === 'table' ? 'Table' : 'Cards') + ' view' + (repo.dirty ? '. Your unsaved changes are still open.' : '.'));
}
/* Opening a sleeve: through goTo, so an unsaved draft asks first. The
   type and category always come from the sleeve itself - the editor's
   checks (fixed, editions, the preview) read them. */
function svOpen(id) {
  var s = sleeveById(id); if (!s) return;
  var table = sv.mode === 'table';
  if (table) sv.returnTo = id;
  var place = table ? { drawer: true, compare: false } : { level: 2 };
  if (table) { var f = svFollow({ fv: sv.fv, fc: sv.fc }, s); place.fv = f.fv; place.fc = f.fc; }
  sv.focus = table ? '#svDrawerTitle' : '#svTitle';
  if (repo.draft && repo.draft.id === id && !repo.draft.create && !repo.draft.edition) {
    repo.variant = s.variant; repo.category = s.category;
    Object.keys(place).forEach(function (k) { sv[k] = place[k]; });
    if (!table) repo.query = '';
    render(); return;
  }
  var go = { to: id, variant: s.variant, category: s.category, sv: place };
  if (!table) go.then = function () { repo.query = ''; };
  goTo(go);
}
function svRowFocus() {
  return (sv.returnTo != null ? '.sv-rowbtn[data-svrow="' + sv.returnTo + '"]||' : '') + '.sv-rowbtn||[data-svfv]';
}
function svCloseDrawer() {
  sv.focus = svRowFocus();
  goTo({ sv: { drawer: false } });
}
/* up one level, the way Escape and Cancel read it: a sleeve's page to its
   category, a category to the tiles; a new edition back to the sleeve it
   came from */
function svUp() {
  var d = repo.draft;
  if (d && d.create && (sv.mode === 'table' ? sv.drawer : sv.level === 2)) { ncCancel(); return; }
  if (sv.mode === 'table') { if (sv.drawer) svCloseDrawer(); return; }
  if (sv.level === 2 && d && d.edition && sleeveById(d.edition.ofId)) {
    var of = sleeveById(d.edition.ofId);
    sv.focus = '#svTitle';
    goTo({ to: of.id, variant: of.variant, category: of.category, sv: { level: 2 } }); return;
  }
  if (sv.level === 2) { sv.focus = '#svCatTitle'; goTo({ sv: { level: 1 } }); return; }
  if (sv.level === 1) {
    sv.focus = '.sv-tile[data-svtile="' + svCssEscape(repo.category) + '"]||.sv-tile';
    goTo({ sv: { level: 0 } });
  }
}
function svCancel() {
  var d = repo.draft; if (!d) return;
  var was = repo.dirty;
  /* the kept copy (F1) goes only with changes to this draft that are given up */
  if (was && keptDraftFor(d)) { forgetKeptDraft(); repo.kept = null; }
  repo.dirty = false; repo.fieldError = null; repo.error = null; repo.picker = null; repo.stale = null;
  if (d.id) {
    var kept = repo.kept;
    loadDraft(d.id);
    if (!was) repo.kept = kept;
    sv.focus = '[data-svedit]';
    render();
  } else if (d.edition && sleeveById(d.edition.ofId)) {
    var of = sleeveById(d.edition.ofId);
    sv.focus = sv.mode === 'table' ? '#svDrawerTitle' : '#svTitle';
    applyTarget({ to: of.id, variant: of.variant, category: of.category,
                  sv: sv.mode === 'table' ? { drawer: true } : { level: 2 } });
  } else {
    /* a new sleeve that is given up goes back where it came from */
    sv.focus = sv.mode === 'table' ? svRowFocus() : '#svCatTitle||.sv-tile';
    applyTarget(sv.mode === 'table' ? { sv: { drawer: false } } : { sv: { level: sv.level === 2 ? 1 : sv.level } });
  }
  if (was) App.announce('polite', 'Changes discarded.');
}
function svEdit() {
  sv.editing = true; repo.confirmDelete = false;
  sv.focus = '#repoName||#repoLabel||#repoNote';
  render();
}
function svCssEscape(v) {
  return (window.CSS && CSS.escape) ? CSS.escape(String(v)) : String(v).replace(/["\\]/g, '\\$&');
}
/* the first of '||'-separated selectors that finds something enabled and
   visible; failing all of them the view switch, so focus never drops to
   the page behind */
function svFocusNow(selectors) {
  var list = String(selectors || '').split('||').concat(['[data-svmode][aria-pressed="true"]']);
  for (var i = 0; i < list.length; i += 1) {
    var sel = list[i].trim(); if (!sel) continue;
    var el = null;
    try { el = document.querySelector('#repoDialog ' + sel); } catch (e) { el = null; }
    if (el && el.focus && !el.disabled && (el.offsetParent !== null || el.getClientRects().length)) {
      el.focus({ preventScroll: false });
      if (document.activeElement === el) return;
    }
  }
}
/* after every render of the view: focus where the move asked, keep the
   keyboard in the drawer while it is open, and put it on the notice when
   unsaved changes block a move */
function svAfterRender() {
  var dialog = document.querySelector('#repoDialog .dialog.repo'); if (!dialog) return;
  var dlg = repo.view === 'sleeves' && !!nc.dlg && ncOpen();
  var modal = repo.view === 'sleeves' && ((sv.mode === 'table' && sv.drawer) || dlg);
  ['.sv-top', '#svBody', '.sv-tarea'].forEach(function (sel) {
    var el = dialog.querySelector(sel);
    if (el) { if (dlg) el.setAttribute('inert', ''); else el.removeAttribute('inert'); }
  });
  /* the pinned summary fits the box it scrolls in, Create always in view */
  var side = dialog.querySelector('#ncSide'), box = side && side.closest('.sv-scroll');
  if (side && box) side.style.maxHeight = Math.max(260, box.clientHeight - 30) + 'px';
  ['.repo-h', '.repo-f'].forEach(function (sel) {
    var el = dialog.querySelector(':scope > ' + sel);
    if (el && repo.view === 'sleeves') { if (modal) el.setAttribute('inert', ''); else el.removeAttribute('inert'); }
  });
  if (repo.view !== 'sleeves') return;
  if (repo.leaving && sv.leavingSeen !== repo.leaving) {
    sv.leavingSeen = repo.leaving; sv.pending = sv.focus; sv.focus = null;
    var keep = dialog.querySelector('[data-repokeep]'); if (keep) keep.focus();
    return;
  }
  if (!repo.leaving) sv.leavingSeen = null;
  if (sv.focus) { var f = sv.focus; sv.focus = null; svFocusNow(f); }
}
/* A redraw replaces the boxes that scroll, and a new box starts at its top:
   ticking a rule chip or opening a revision far down a sleeve's page threw
   the reader back to its title. Where the redraw leaves you in the same place
   - the same view, level, sleeve and drawer - each box is put back where it
   was; a move somewhere else starts at the top, as it should. */
function svPlace() {
  return [repo.view, sv.mode, sv.level, sv.drawer, sv.compare, !!repo.query.trim(),
          repo.draft ? (repo.draft.id || (repo.draft.edition ? 'edition' : 'new')) : ''].join('|');
}
function svScrollKeep() {
  var same = sv.lastPlace === svPlace();
  var saved = ['svBody', 'svTblWrap', 'svDrawerBody'].map(function (id) {
    var el = document.getElementById(id);
    return el ? { id: id, top: el.scrollTop, left: el.scrollLeft } : null;
  });
  return function () {
    if (same) {
      saved.forEach(function (x) {
        var el = x && document.getElementById(x.id);
        if (el) { el.scrollTop = x.top; el.scrollLeft = x.left; }
      });
    }
    sv.lastPlace = repo.open ? svPlace() : null;
  };
}
/* typing in the search redraws the results, never the box (C1) */
function svRefreshResults() {
  if (sv.mode === 'table') {
    var rows = svRows();
    var body = document.getElementById('svTbody'); if (body) body.innerHTML = svTbodyHtml(rows);
    var count = document.getElementById('svCount'); if (count) count.textContent = svCountText(rows.length);
  } else {
    var pane = document.getElementById('svBody'); if (pane) pane.innerHTML = svCardsBodyHtml();
  }
  var ctx = document.getElementById('svCtx'); if (ctx) ctx.innerHTML = svContextHtml();
}
/* the editing controls follow typing without a redraw (F2) */
function svRefreshControls() {
  document.querySelectorAll('#repoDialog [data-svstate]').forEach(function (el) { el.innerHTML = svStateHtml(); });
  document.querySelectorAll('#repoDialog [data-svcancel]').forEach(function (el) { el.textContent = svCancelLabel(); });
  document.querySelectorAll('#repoDialog [data-reposave]').forEach(function (b) { b.disabled = !svCanSave(); });
  var note = document.getElementById('repoRenameNote'); if (note) note.textContent = renameWarning();
}
/* an unsaved draft kept from an earlier visit, read once the library is in */
function svReadKeptOffer() {
  try {
    var held = JSON.parse(window.localStorage.getItem(DRAFT_KEY) || 'null');
    if (!held || !held.key || !held.draft) return null;
    var m = /^(sleeve|edition):(\d+)$/.exec(held.key);
    if (m && !sleeveById(parseInt(m[2], 10))) return null;     /* what it belonged to is gone */
    return held;
  } catch (e) { return null; }
}
function svOpenKept() {
  var k = sv.keptOffer; if (!k) return;
  sv.keptOffer = null;
  var m = /^(sleeve|edition):(\d+)$/.exec(k.key);
  var place = sv.mode === 'table' ? { drawer: true, compare: false } : { level: 2 };
  var then = function () {
    repo.draft = ncRevive(k.draft); repo.kept = null; sv.editing = true; markDirty();
    sv.focus = '#repoName||#repoLabel||#repoNote';
    render();
  };
  if (m && m[1] === 'sleeve') {
    var s = sleeveById(parseInt(m[2], 10));
    goTo({ to: s.id, variant: s.variant, category: s.category, sv: place, then: then });
  } else if (m) {
    var of = sleeveById(parseInt(m[2], 10));
    goTo({ variant: of.variant, category: of.category, fresh: true, edition: { ofId: of.id, name: of.name }, sv: place, then: then });
  } else {
    goTo({ variant: k.variant, category: k.category, fresh: true, create: true, sv: place, then: then });
  }
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
    + arcSelect('variant', 'Implementation type', repo.data.variants)
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
  return '<tr><th class="pinc">'
    + (visible.length
        ? '<input type="checkbox" data-arcselall aria-label="Select every shown sleeve"'
          + (allOn ? ' checked' : '') + '>' : '')
    + '</th>'
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
      + '<td>' + esc(shortDateTime(s.createdAt)) + '</td>'
      + '<td>' + esc(shortDate(s.archivedAt)) + (s.archivedBy ? ' <span class="mut">· ' + esc(s.archivedBy) + '</span>' : '') + '</td>'
      + '<td class="num">' + s.revisions + '</td>'
      + '<td class="arc-status">' + (s.supersededBy
          ? '<span class="arc-badge gone" title="Replaced when a newer version was saved">earlier version</span>'
          : r.liveElsewhere.length
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
    + '<p class="repo-prov">' + (entry.supersededBy ? 'Replaced by a newer version ' : 'Archived ')
    + esc(shortDateTime(entry.archivedAt)) + (entry.archivedBy ? ' by ' + esc(entry.archivedBy) : '')
    + ' · this version created ' + esc(shortDateTime(entry.createdAt)) + (entry.createdBy ? ' by ' + esc(entry.createdBy) : '')
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
       /* the hint only where there is something to tick (E3): under an empty
          table, beside a count of zero, it described a control that was not
          there */
       : (archivedSleeves().length
            ? '<span class="repo-src">Tick sleeves to restore several at once</span>' : ''))
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
  var books = [['', 'All implementation types']].concat((repo.data.variants || []).map(function (v) { return [v, v + ' (' + (facets.variant[v] || 0) + ')']; }));
  return '<div class="cat-tools act-tools">'
    + '<label class="cat-search"><span aria-hidden="true">⌕</span>'
    + '<input type="search" id="actSearch" placeholder="Search sleeve, product, who, what changed…" value="' + esc(act.query) + '"'
    + ' aria-label="Search the record"><kbd aria-hidden="true">/</kbd></label>'
    + '<span class="act-chips" role="group" aria-label="Actions">' + chips + '</span>'
    + actSelect('who', 'actwho', 'Who', people, act.actor)
    + actSelect('book', 'actbook', 'Implementation type', books, act.variant)
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
  if (reg.view === 'moved') q.set('moved', '1');
  if (reg.view === 'noAccount') q.set('noAccount', '1');
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
    reg.summary = r.body.summary || null;
    reg.loaded = true;
    loadRegisterViews();
  } catch (err) { reg.error = err.message; }
  reg.busy = false; render();
}
/* The view counts are over the whole register, so they do not move with the
   filters and are read once per session rather than once per page. */
async function loadRegisterViews() {
  if (reg.views || reg.viewsBusy) return;
  reg.viewsBusy = true;
  try {
    var r = await api('GET', '/scenario/repository/proposals/views');
    if (r && r.ok) { reg.views = r.body.views; render(); }
  } catch (err) { /* the chips simply go countless */ }
  reg.viewsBusy = false;
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
/* A figure in the currency it was agreed in (D1). The register is the record
   of what was delivered to a client, and it printed every proposal in dollars
   - a GBP mandate as $200.0m, with "GBP" in the next column along. The row
   carries its currency, so the only thing missing was asking for it.

   A currency with no unambiguous symbol prints its ISO code instead: better a
   reader who has to think than one who is quietly told the wrong thing. */
var CURRENCY_MARK = { USD: '$', GBP: '\u00a3', EUR: '\u20ac', CHF: 'CHF\u00a0', JPY: '\u00a5' };
function mark(currency) {
  if (!currency) return '';
  return CURRENCY_MARK[currency] || (currency + '\u00a0');
}
function money(n, currency) {
  if (typeof n !== 'number') return '\u2014';
  var m = mark(currency);
  /* There was nothing above m, so a 5e14 mandate - a real row - printed as
     $500000000.0m, wider than its column and read as nothing at all (D116). */
  if (n >= 1e12) return m + (Math.round(n / 1e11) / 10).toFixed(1) + 'tn';
  if (n >= 1e9) return m + (Math.round(n / 1e8) / 10).toFixed(1) + 'bn';
  if (n >= 1e6) return m + (Math.round(n / 1e5) / 10).toFixed(1) + 'm';
  if (n >= 1e3) return m + Math.round(n / 1e3) + 'k';
  return m + Math.round(n);
}
function regViewsHtml() {
  var counts = reg.views || {};
  return '<div class="reg-views" role="tablist" aria-label="Saved views">'
    + REG_VIEWS.map(function (v) {
        var on = reg.view === v[0];
        var n = counts[v[0]];
        return '<button type="button" role="tab" class="reg-view' + (on ? ' on' : '') + '"'
          + ' data-regview="' + v[0] + '" aria-selected="' + on + '">' + esc(v[1])
          + (typeof n === 'number' ? '<b>' + n + '</b>' : '') + '</button>';
      }).join('')
    + '</div>';
}

/* What the rows in force come to (D116). The register could answer a question
   about a row and none about a set; this is the set, and mandate is totalled
   per currency because adding across them would be a lie (D1). */
function regSummaryHtml() {
  var sm = reg.summary;
  if (!sm || !reg.loaded) return '';
  var totals = (sm.totals || []).map(function (t) { return money(t.total, t.currency); }).join(' \u00b7 ');
  var span = sm.earliest
    ? shortDate(sm.earliest) + (sm.latest && sm.latest.slice(0, 10) !== sm.earliest.slice(0, 10)
        ? ' \u2013 ' + shortDate(sm.latest) : '')
    : '\u2014';
  var cells = [
    ['Proposals', String(sm.proposals)],
    ['Mandate', totals || '\u2014'],
    [sm.pwas === 1 ? 'PWA' : 'PWAs', String(sm.pwas)],
    ['Span', span]
  ];
  return '<dl class="reg-stat">' + cells.map(function (c) {
    return '<div><dt>' + esc(c[0]) + '</dt><dd>' + esc(c[1]) + '</dd></div>';
  }).join('') + '</dl>';
}

function regToolbarHtml() {
  var filtered = !!(reg.exportedBy || reg.primaryPwa || reg.currency || reg.variant);
  return regViewsHtml()
    + '<div class="cat-tools reg-tools">'
    + '<label class="cat-search"><span aria-hidden="true">\u2315</span>'
    + '<input type="search" id="regSearch" placeholder="Search UID, PWA, person, sleeve\u2026" value="' + esc(reg.query) + '"'
    + ' aria-label="Search the register"><kbd aria-hidden="true">/</kbd></label>'
    /* the five selects are the long tail of asking, so they fold away behind
       one control and the saved views take the front (D116) */
    + '<button type="button" class="cat-chip' + (filtered || reg.openFilters ? ' on' : '') + '" data-regfilters'
    + ' aria-expanded="' + reg.openFilters + '">Filter'
    + (filtered ? ' <b>' + [reg.exportedBy, reg.primaryPwa, reg.currency, reg.variant].filter(Boolean).length + '</b>' : '')
    + ' <span class="car">\u25be</span></button>'
    + (regFiltersInForce() ? '<button type="button" class="cat-clear" data-regclear>Clear</button>' : '')
    + '<span class="spacer"></span>'
    + '<a class="btn arc-export" href="' + esc(window.API_BASE + '/scenario/repository/proposals.csv?' + regParams(null).replace(/&?limit=\d+/, '')) + '" download>Export CSV</a>'
    + '</div>'
    + (reg.openFilters
        ? '<div class="cat-tools reg-filters" id="regFilters">'
          + regSelect('regwho', 'By', regFacetOptions('exportedBy', reg.exportedBy, 'Anyone'), reg.exportedBy)
          + regSelect('regpwa', 'PWA', regFacetOptions('primaryPwa', reg.primaryPwa, 'Any PWA'), reg.primaryPwa)
          + regSelect('regccy', 'Currency', regFacetOptions('currency', reg.currency, 'Any'), reg.currency)
          + regSelect('regbook', 'Implementation type', regFacetOptions('variant', reg.variant, 'All implementation types'), reg.variant)
          + regSelect('regrange', 'When', REG_RANGES, reg.range)
          + '</div>'
        : '')
    + regSummaryHtml();
}

/* Exported and By are one cell (D3): on a real register both repeat - one
   desk, a handful of PWAs - and giving two identity columns the same weight
   as the portfolio and the price spent the width on what varies least. */
function regHeadHtml() {
  return '<tr><th>Exported</th><th>Primary PWA</th><th class="num">Mandate</th>'
    + '<th>Proposal UID</th><th>Since</th></tr>';
}
function regBodyHtml() {
  if (!reg.loaded && reg.busy) return '<tr><td colspan="5" class="cat-empty">Reading the register\u2026</td></tr>';
  if (reg.error && !reg.entries.length) return '<tr><td colspan="5" class="cat-empty md-err">' + esc(reg.error) + '</td></tr>';
  if (!reg.entries.length) {
    var view = REG_VIEWS.filter(function (v) { return v[0] === reg.view; })[0];
    return '<tr><td colspan="5" class="cat-empty">No proposal on record'
      + (view ? ' under \u201c' + esc(view[1]) + '\u201d' : '')
      + (regFiltersInForce() ? ' with the filters in force.' : '.')
      + (regFiltersInForce() ? ' <button type="button" class="cat-clear" data-regclear>Clear the filters</button>' : '')
      + '</td></tr>';
  }
  return reg.entries.map(function (e) {
    var when = e.exportedAt ? shortDate(e.exportedAt) + ' ' + e.exportedAt.slice(11, 16) : '';
    /* what has happened to this proposal since it was delivered - the two
       things that were readable only inside the record (D116) */
    var since = [];
    if (e.moved) {
      since.push('<span class="arc-badge warn" title="The library has moved on since this proposal">'
        + e.moved + ' moved</span>');
    }
    if (e.accountRequested) since.push('<span class="arc-badge ok" title="An account opening request exists">account</span>');
    if (e.sequence > 1) since.push('<span class="arc-badge acc" title="The ' + e.sequence + 'th proposal of its scenario">#' + e.sequence + '</span>');
    if (e.tacticalTilt) since.push('<span class="arc-badge mute">tilt</span>');
    var name = String(e.primaryPwa || '');
    var dash = name.indexOf('\u2014');
    return '<tr data-regrow="' + esc(e.proposalId) + '" class="' + (reg.detail === e.proposalId ? 'on' : '') + '" aria-selected="' + (reg.detail === e.proposalId) + '">'
      + '<td class="reg-when">' + esc(when) + '<small>' + esc(e.exportedBy) + '</small></td>'
      + '<td class="reg-pwa"><b>' + esc(dash === -1 ? name : name.slice(0, dash).trim()) + '</b>'
      + (dash === -1 ? '' : '<small>' + esc(name.slice(dash + 1).trim()) + '</small>') + '</td>'
      + '<td class="num reg-size">' + money(e.mandateSize, e.currency)
      + '<small>' + esc(e.currency + ' \u00b7 ' + e.hedging) + '</small></td>'
      /* the id everything else quotes, and one click to have it (D116) */
      + '<td><button type="button" class="reg-uid" data-reguid="' + esc(e.proposalId) + '"'
      + ' title="Copy the Proposal UID">' + esc(e.proposalId) + '</button></td>'
      + '<td class="arc-status">' + since.join(' ') + '</td>'
      + '</tr>';
  }).join('');
}
/* The UID is what the workbook's name, cell B1 and every account opening
   request quote, so the register hands it over rather than making it text to
   select by hand (D116). */
function copyProposalUid(button, uid) {
  function done() {
    button.classList.add('copied');
    App.announce('polite', 'Proposal UID ' + uid + ' copied.');
    window.setTimeout(function () {
      if (button.isConnected) button.classList.remove('copied');
    }, 1400);
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(uid).then(done, function () { /* nothing to do */ });
    return;
  }
  var area = document.createElement('textarea');
  area.value = uid; area.setAttribute('readonly', '');
  area.style.position = 'fixed'; area.style.opacity = '0';
  document.body.appendChild(area); area.select();
  try { document.execCommand('copy'); } catch (err) { /* nothing to do */ }
  area.remove();
  done();
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
  var ccy = record.currency;                   /* the proposal's own (D1) */
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
        + '<td class="num">' + money(it.notional, ccy) + '</td></tr>';
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
    + '<h3>' + esc(r.primaryPwa) + '</h3>'
    + '<p><b>' + money(r.mandateSize, r.currency) + '</b> · ' + esc(r.currency) + ' · ' + esc(r.hedging)
    + ' · ' + esc((r.baseKey || '').split('|').slice(1, 3).join(' '))
    + (r.tacticalTilt ? ' · tactical tilt' : '') + (r.volPremium ? ' · vol premium' : '') + '</p>'
    + '<p>' + esc(r.variant)
    + (r.includeFees ? ' · ' + esc((r.feeSchedule || '') + ' ' + (r.feeLevel || '')) : ' · no fees') + '</p>'
    + '<p class="repo-prov"><button type="button" class="reg-uid" data-reguid="' + esc(r.proposalId)
    + '" title="Copy the Proposal UID">' + esc(r.proposalId) + '</button>'
    + ' · exported ' + esc(shortDate(r.exportedAt)) + ' ' + esc((r.exportedAt || '').slice(11, 16))
    + ' by ' + esc(r.exportedBy) + (r.createdBy && r.createdBy !== r.exportedBy ? ' · started by ' + esc(r.createdBy) : '')
    + ' · workbook ' + Math.round(r.workbookBytes / 1024) + ' KB · sha ' + esc((r.workbookSha || '').slice(0, 8)) + '…'
    + (moved ? ' · <b class="warn">' + moved + ' sleeve' + (moved === 1 ? '' : 's') + ' moved since</b>' : '') + '</p>'
    + '</div><div class="arc-dact">'
    + '<a class="btn btn-primary" href="' + esc(window.API_BASE + '/scenario/repository/proposals/' + encodeURIComponent(r.proposalId) + '/workbook') + '" download>Download the workbook</a>'
    /* the deck delivered with it (D123); a proposal from before decks has none */
    + (r.deckBytes
        ? '<a class="btn" href="' + esc(window.API_BASE + '/scenario/repository/proposals/' + encodeURIComponent(r.proposalId) + '/deck') + '" download>Download the deck</a>'
        : '')
    + '<div class="repo-seg" role="tablist" aria-label="Picture">'
    + '<button type="button" role="tab" data-regpic="allocation" aria-selected="' + (reg.picture === 'allocation') + '">Allocation</button>'
    + '<button type="button" role="tab" data-regpic="implemented" aria-selected="' + (reg.picture === 'implemented') + '">Implemented</button>'
    + '</div>'
    + '<button type="button" class="dlg-close arc-dclose" data-regdetailclose aria-label="Close">×</button>'
    + '</div></div>'
    + '<div class="reg-pic-wrap">' + (reg.picture === 'allocation' ? regAllocationHtml(r) : regImplementedHtml(r)) + '</div>'
    + '</div>';
}
/* The record moved out of a drawer along the foot and into a pane beside the
   list (D116). The drawer took up to 52% of the card's height, so reading one
   proposal halved the list you were reading it from; the pane costs no rows,
   and arrowing down the list with it open turns the register into something
   that can be read through. Below 1100px the CSS drops it back under the
   table, where it is the only thing that fits. */
function registerViewHtml() {
  return regToolbarHtml()
    + '<div class="arc-b reg-b' + (reg.detail ? ' open' : '') + '">'
    + '<div class="cat-tblwrap arc-tblwrap reg-list" tabindex="0" aria-label="Proposal register, scrolls">'
    + '<table class="cat-tbl dense arc-tbl reg-tbl"><thead>' + regHeadHtml() + '</thead>'
    + '<tbody id="regBody">' + regBodyHtml()
    + (reg.next ? '<tr><td colspan="5" class="act-more"><button type="button" class="btn" data-regmore' + (reg.busy ? ' disabled' : '') + '>' + (reg.busy ? 'Reading…' : 'Earlier proposals') + '</button></td></tr>' : '')
    + '</tbody></table></div>'
    + (reg.detail ? '<div class="reg-pane">' + regDetailHtml() + '</div>' : '')
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
/* Banded (D100): the visible rows under their sleeve categories, in the
   repository's own order, the unplaced last. */
function catBandOrder() { return repo.data.categories.concat([UNPLACED]); }
function catBands() { return catGroups(catVisible(), catBandOrder(), cat.filters.category); }
function catShut(key) { return cat.folded.indexOf(key) !== -1; }
/* every row the table holds, in the order it holds them, each with the band
   it stands in and whether that band is shut - what the keyboard walks */
function catEntries() {
  if (!cat.group) return catVisible().map(function (row) { return { row: row, band: null, shut: false }; });
  var out = [];
  catBands().forEach(function (g) {
    var shut = catShut(g.key);
    g.rows.forEach(function (row) { out.push({ row: row, band: g.key, shut: shut }); });
  });
  return out;
}
/* which entry the cursor is on: the product, and banded, its copy in the
   band the cursor was put in - or the first copy, when that is not known */
function catCursorAt(entries) {
  var first = -1;
  for (var i = 0; i < entries.length; i += 1) {
    if (entries[i].row.p.productId !== cat.cursor) continue;
    if (entries[i].band === cat.cursorBand) return i;
    if (first === -1) first = i;
  }
  return first;
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
function liqClass(v) {
  if (!v) return '';
  var s = String(v).toLowerCase();
  if (s === 'daily' || s === 'weekly') return '';
  if (s === 'drawdown' || s === 'closed' || s === 'illiquid') return ' lock';
  return ' slow';
}

/* ---- the catalogue, as a file (H2) ---------------------------------------
   Three of the four tables export and the one that did not was the one with
   the search, the facets, the column picker and the grouping - the view most
   likely to be the answer to "send me the list", whose only alternative was
   a screenshot.

   Built here rather than on the server on purpose: every one of those filters
   is client state, so the server would have to reimplement the lot and the
   two would drift. What is written is exactly what is on screen, in the order
   it is on screen, with the columns that are on screen.

   Excel decides a field's type from the text, so a ticker like SEP1 must not
   arrive as a date: every text field is quoted. The figures are the products'
   own; nothing in the file depends on a proposal's fees (D114). */
function catCsvCell(row, c) {
  var p = row.p;
  switch (c.key) {
    case 'net': return row.net === null ? '' : row.net.toFixed(2);
    case 'used': return String(row.used);
    case 'productCost': case 'distributionYield':
      return (typeof p[c.key] === 'number') ? p[c.key].toFixed(2) : '';
    case 'minimumInvestment':
      return (typeof p.minimumInvestment === 'number') ? String(p.minimumInvestment) : '';
    default: return p[c.key] == null ? '' : String(p[c.key]);
  }
}

function catCsv() {
  var cols = catColumns();
  var quote = function (v) { return '"' + String(v).replace(/"/g, '""') + '"'; };
  var lines = [];
  var grouped = cat.group;
  var head = (grouped ? ['Category'] : []).concat(cols.map(function (c) { return c.label; }));
  lines.push(head.map(quote).join(','));
  var put = function (row, band) {
    lines.push((grouped ? [quote(band)] : [])
      .concat(cols.map(function (c) {
        var v = catCsvCell(row, c);
        /* a figure goes bare so a spreadsheet adds it up; text is quoted so it
           is never read as a date */
        return CAT_NUMERIC[c.key] && v !== '' ? v : quote(v);
      })).join(','));
  };
  if (grouped) catBands().forEach(function (g) { g.rows.forEach(function (r) { put(r, g.key); }); });
  else catVisible().forEach(function (r) { put(r, ''); });
  return lines.join('\r\n') + '\r\n';
}

function catCsvName() {
  var bits = ['catalogue'];
  if (cat.query.trim()) bits.push(cat.query.trim().replace(/[^A-Za-z0-9]+/g, '-'));
  Object.keys(cat.filters).forEach(function (f) {
    if (cat.filters[f] && cat.filters[f].length) {
      bits.push(cat.filters[f].join('-').replace(/[^A-Za-z0-9]+/g, '-'));
    }
  });
  bits.push(new Date().toISOString().slice(0, 10));
  return bits.join('_').slice(0, 120) + '.csv';
}

function downloadCatalogue() {
  var blob = new Blob(['\ufeff' + catCsv()], { type: 'text/csv;charset=utf-8' });
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url; a.download = catCsvName();
  document.body.appendChild(a); a.click(); a.remove();
  window.setTimeout(function () { URL.revokeObjectURL(url); }, 0);
  App.announce('polite', catVisible().length + ' products written to ' + a.download + '.');
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
    /* one ranked list, or the same rows banded by sleeve category (D100) */
    + '<div class="repo-seg cat-density" role="tablist" aria-label="Rows">'
    + '<button type="button" role="tab" id="catByCategory" data-catgroup="category" aria-selected="' + cat.group + '">By category</button>'
    + '<button type="button" role="tab" id="catFlat" data-catgroup="flat" aria-selected="' + !cat.group + '">Flat</button>'
    + '</div>'
    + '<span id="catFoldAll">' + catFoldAllHtml() + '</span>'
    + '<span class="cat-chipwrap">'
    + '<button type="button" class="cat-chip' + (cat.hidden.length ? ' on' : '') + '" data-catcols aria-expanded="' + (cat.openChip === 'cols') + '">Columns'
    + (cat.hidden.length ? ' <b>' + (CAT_COLUMNS.length - cat.hidden.length) + ' of ' + CAT_COLUMNS.length + '</b>' : '') + ' <span class="car">▾</span></button>'
    + (cat.openChip === 'cols'
        ? '<div class="cat-menu" role="group" aria-label="Columns">'
          + CAT_COLUMNS.map(function (c) {
              return '<label class="cat-opt' + (c.fixed ? ' fixed' : '') + '"><input type="checkbox" data-catcol="' + c.key + '"'
                + (cat.hidden.indexOf(c.key) === -1 ? ' checked' : '') + (c.fixed ? ' disabled' : '') + '> <span>' + esc(c.label)
                + (c.derived ? ' (derived)' : '') + '</span></label>';
            }).join('')
          + (cat.hidden.length ? '<button type="button" class="cat-clear" data-catshowall>Show all</button>' : '')
          + '</div>' : '')
    + '</span>'
    + '<button type="button" class="btn arc-export" data-catcsv'
    + (catVisible().length ? '' : ' disabled') + '>Export CSV</button>'
    + '<span class="cat-count" id="catCount">' + catCountText() + '</span>'
    + '</div>';
}

/* every band at once: with eight of them, one at a time is a chore */
function catFoldAllHtml() {
  if (!cat.group || cat.compare) return '';
  var bands = catBands(); if (bands.length < 2) return '';
  var allShut = bands.every(function (g) { return catShut(g.key); });
  return '<button type="button" class="cat-clear" id="catFoldAllBtn" data-catfoldall="' + (allShut ? 'open' : 'shut') + '">'
    + (allShut ? 'Open all' : 'Fold all') + '</button>';
}

function catCountText() {
  var n = catVisible().length, all = repo.data.products.length;
  return (n === all ? all + ' products' : 'Showing ' + n + ' of ' + all)
    + (cat.sort ? ' · sorted by ' + esc(catColumnLabel(cat.sort.key)) + (cat.sort.dir === 'desc' ? ' ↓' : ' ↑')
        + (cat.group ? ' within category' : '') : '')
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

/* The table says what it can do (D99): the sorted column carries a class its
   whole height, a header shows it can sort before it has, and every row ends
   in a cell that says it opens. */
function catHeadHtml() {
  return '<tr><th class="pinc"></th>' + catColumns().map(function (c) {
    var sorted = cat.sort && cat.sort.key === c.key;
    var cls = (c.num ? 'num' : '') + (sorted ? ' srt' : '');
    return '<th' + (cls ? ' class="' + cls.trim() + '"' : '') + ' aria-sort="' + (sorted ? (cat.sort.dir === 'desc' ? 'descending' : 'ascending') : 'none') + '">'
      + '<button type="button" class="cat-sort' + (sorted ? ' on' : '') + (c.derived ? ' drv' : '') + '" data-catsort="' + c.key + '"'
      + (c.derived ? ' title="Derived: distribution yield less product cost"' : '') + '><span class="lb">' + esc(c.label)
      + '</span>'
      + (sorted ? (cat.sort.dir === 'desc' ? ' ▼' : ' ▲') : '') + '</button></th>';
  }).join('') + '<th class="go"></th></tr>';
}

function catCellHtml(row, c, sorted) {
  var p = row.p;
  var td = function (cls, html) {
    var all = (cls + (sorted ? ' srt' : '')).trim();
    return '<td' + (all ? ' class="' + all + '"' : '') + '>' + html + '</td>';
  };
  switch (c.key) {
    case 'ticker': return td('tk', esc(p.ticker));
    case 'name': return td('nm', '<b>' + esc(p.name) + (row.tooBig ? ' <span class="flag" title="Minimum above the open mandate">min ' + esc(catMoney(p.minimumInvestment)) + '</span>' : '') + '</b>');
    case 'vehicle': return td('', '<span class="veh">' + esc(p.vehicle) + '</span>');
    case 'liquidity': return td('', '<span class="liq' + liqClass(p.liquidity) + '">' + esc(p.liquidity) + '</span>');
    case 'productCost': return td('num', catFigure(p.productCost, 2));
    case 'distributionYield': return td('num', catFigure(p.distributionYield, 2));
    case 'net': return td('num' + (row.net === null ? ' mute' : (row.net < 0 ? ' neg' : ' pos')),
      row.net === null ? '—' : (row.net >= 0 ? '+' : '−') + Math.abs(row.net).toFixed(2));
    case 'minimumInvestment': return td('num' + (typeof p.minimumInvestment === 'number' ? '' : ' mute'), esc(catMoney(p.minimumInvestment)));
    case 'used': return td('num used' + (row.used ? '' : ' zero'), String(row.used));
    default: return td('mute', esc(p[c.key]));
  }
}

/* a band (D100): the category, and what can be said of it before a row is
   read. The whole band is the button that folds it. */
function catBandHtml(g, span) {
  var shut = catShut(g.key), n = g.rows.length, at = catBandOrder().indexOf(g.key);
  return '<tr class="cat-band' + (shut ? ' shut' : '') + '" data-catfold="' + esc(g.key) + '"><th colspan="' + span + '" scope="rowgroup">'
    + '<button type="button" class="cat-fold"' + (at === -1 ? '' : ' id="catFold' + at + '"') + ' aria-expanded="' + !shut + '">'
    + '<span class="car" aria-hidden="true">▾</span><b>' + esc(g.key) + '</b>'
    + '<span class="n">' + n + ' product' + (n === 1 ? '' : 's') + '</span>'
    + (g.lo === null ? '' : '<span class="rng">cost ' + catFigure(g.lo, 2) + (g.hi > g.lo ? '–' + catFigure(g.hi, 2) : '') + '</span>')
    + (g.notDaily ? '<span class="cst">' + g.notDaily + ' not daily</span>' : '')
    + '</button></th></tr>';
}

function catRowHtml(row, cols, band, isCursor) {
  var id = row.p.productId, pinned = cat.pins.indexOf(id) !== -1;
  var sortKey = cat.sort ? cat.sort.key : null;
  return '<tr data-catrow="' + esc(id) + '"' + (band === null ? '' : ' data-catband="' + esc(band) + '"')
    + ' class="' + (pinned ? 'pin' : '') + (isCursor ? ' cur' : '')
    + (id === cat.detail ? ' on' : '') + (row.tooBig ? ' dim' : '') + '" tabindex="-1" aria-selected="' + (id === cat.detail) + '">'
    + '<td class="pinc"><button type="button" class="pinbox' + (pinned ? ' on' : '') + '" data-catpin="' + esc(id) + '"'
    + ' title="' + (pinned ? 'Unpin' : 'Pin to compare') + '"'
    + ' aria-label="' + (pinned ? 'Unpin' : 'Pin') + ' ' + esc(row.p.name) + '" aria-pressed="' + pinned + '"></button></td>'
    + cols.map(function (c) { return catCellHtml(row, c, c.key === sortKey); }).join('')
    + '<td class="go"><span aria-hidden="true">›</span></td></tr>';
}

/* the table's bodies: one for the flat list, or one per band, so a band's
   header is the header of a row group and says so */
function catBodyHtml() {
  var entries = catEntries();
  var cols = catColumns(), span = cols.length + 2;      /* the pin cell, the columns, the chevron */
  if (!entries.length) {
    return '<tbody><tr><td colspan="' + span + '" class="cat-empty">No product matches'
      + (cat.query.trim() ? ' <b>“' + esc(cat.query.trim()) + '”</b>' : '') + ' with the filters in force. '
      + '<button type="button" class="cat-clear" data-catclearall>Clear the filters</button> · '
      + repo.data.products.length + ' in the catalogue.</td></tr></tbody>';
  }
  var cursorAt = cat.cursor ? catCursorAt(entries) : -1;
  if (!cat.group) {
    return '<tbody>' + entries.map(function (e, i) { return catRowHtml(e.row, cols, null, i === cursorAt); }).join('') + '</tbody>';
  }
  var i = 0;
  return catBands().map(function (g) {
    var shut = catShut(g.key);
    var rows = g.rows.map(function (row) {
      var html = shut ? '' : catRowHtml(row, cols, g.key, i === cursorAt);
      i += 1;
      return html;
    }).join('');
    return '<tbody>' + catBandHtml(g, span) + rows + '</tbody>';
  }).join('');
}

function catTableHtml() {
  return '<div class="cat-tblwrap cat-main' + (cat.group ? ' grouped' : '') + '" id="catTblWrap" tabindex="0" aria-label="Product catalogue, scrolls">'
    + '<table class="cat-tbl ' + esc(cat.density) + (cat.group ? ' grouped' : '') + '" id="catTbl"><thead>' + catHeadHtml() + '</thead>'
    + catBodyHtml() + '</table></div>'
    /* the shadow that says the table carries on to the right (D99) */
    + '<span class="cat-more" id="catMore" aria-hidden="true"></span>';
}

function catCompareHtml() {
  var pinned = cat.pins.map(catRow).filter(Boolean);
  if (!pinned.length) return '<div class="cat-cmp-empty"><p>Pin two or more products to compare them.</p></div>';
  var best = catBest(pinned);
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
    + line('<b>Product cost</b>', function (r) { return '<b>' + catFigure(r.p.productCost, 2) + '</b>'; }, 'productCost')
    + section('Yield · % p.a.')
    + line('Distribution yield', function (r) { return catFigure(r.p.distributionYield, 2); }, 'distributionYield')
    + line('<em>Net of product cost</em>', function (r) { return r.net === null ? '—' : (r.net >= 0 ? '+' : '−') + Math.abs(r.net).toFixed(2); }, 'net')
    + section('Placement')
    + line('Offered in', function (r) { return r.books.length ? r.books.length + ' implementation type' + (r.books.length === 1 ? '' : 's') : '—'; })
    + line('Used in sleeves', function (r) { return String(r.used); })
    + '</tbody></table>'
    + '<p class="cat-cmp-key">Lowest cost and highest yield marked per row. Product fees only: management fees belong to a proposal, not to the catalogue.</p>'
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
    + '<div class="m"><small>Product cost</small><b>' + catFigure(p.productCost, 2) + '</b></div>'
    + '<div class="m"><small>Yield</small><b>' + catFigure(p.distributionYield, 2) + '</b></div>'
    + '<div class="m"><small>Net</small><b>' + (row.net === null ? '—' : (row.net >= 0 ? '+' : '−') + Math.abs(row.net).toFixed(2)) + '</b></div>'
    + '</div>'
    + '<dl class="cat-dl">'
    + '<dt>Vehicle</dt><dd>' + esc(p.vehicle) + ' · ' + esc(p.style) + '</dd><dt>Source</dt><dd>' + esc(p.source) + '</dd>'
    + '<dt>Exposure</dt><dd>' + esc(p.exposureCurrency) + '</dd><dt>Liquidity</dt><dd><span class="liq' + liqClass(p.liquidity) + '">' + esc(p.liquidity) + '</span></dd>'
    + '<dt>Minimum</dt><dd>' + esc(catMoney(p.minimumInvestment)) + (row.tooBig ? ' <span class="flag">above mandate</span>' : '') + '</dd>'
    + '<dt>Offered in</dt><dd>' + (row.books.length ? esc(row.books.join(', ')) : '—') + '</dd></dl>'
    + '<div class="cat-usedin"><div class="h">Used in · ' + uses.length + ' sleeve' + (uses.length === 1 ? '' : 's') + '</div>' + list + '</div>'
    + '<div class="acts"><button type="button" class="btn" data-catpin="' + esc(p.productId) + '">' + (cat.pins.indexOf(p.productId) !== -1 ? 'Unpin' : 'Pin to compare') + '</button></div>'
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
    + (cat.compare ? '← Back to list' : 'Compare') + '</button>'
    + '</div>';
}

/* The keys, where they can be seen (D99): the view was always driven from
   the keyboard, and said so nowhere. Only the keys that do something from
   where the reader is. */
function catKeysHtml() {
  var keys = cat.compare
    ? [['/', 'search'], ['c', 'back to the list'], ['esc', 'back']]
    : [['/', 'search'], ['↑ ↓', 'move'], ['space', 'pin'], ['enter', 'open'], ['c', 'compare'], ['g', 'group'], ['esc', 'back']];
  return '<div class="cat-keys" id="catKeys"><span class="ttl">Keys</span>'
    + keys.map(function (k) {
        return '<span class="k">' + k[0].split(' ').map(function (cap) { return '<kbd>' + esc(cap) + '</kbd>'; }).join('')
          + esc(k[1]) + '</span>';
      }).join('')
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
/* the cursor's row, as drawn: banded, a product two categories hold is on
   the page twice, and only one copy is the cursor's */
function catCursorRow() {
  return cat.cursor ? document.querySelector('#catTbl tr.cur[data-catrow]') : null;
}
function catFocusCursor() {
  var row = catCursorRow();
  if (row && row.focus) row.focus({ preventScroll: true });
}
function catMoveCursor(step) {
  /* over every row the table holds, shut bands included, so a cursor left
     inside a band that was then folded moves on from where it was */
  var entries = catEntries();
  var at = catCursorAt(entries), next = at;
  if (at === -1) next = step > 0 ? -1 : entries.length;
  do { next += step; } while (entries[next] && entries[next].shut);
  if (!entries[next]) {
    if (at !== -1 && !entries[at].shut) return;     /* already at the end */
    /* nothing open that way: the nearest open row the other way, if any */
    next = at === -1 ? (step > 0 ? entries.length : -1) : at;
    do { next -= step; } while (entries[next] && entries[next].shut);
    if (!entries[next]) return;
  }
  cat.cursor = entries[next].row.p.productId;
  cat.cursorBand = entries[next].band;
  if (cat.detail) cat.detail = cat.cursor;      /* an open panel follows the cursor */
  render(); catScrollCursorIntoView(); catFocusCursor();
}
function catSetGroup(on) {
  cat.group = !!on;
  cat.cursorBand = null;
  /* a new order: from the top, or - with a row under the cursor - wherever
     that row has gone */
  render(); catScrollTo(0); catScrollCursorIntoView('center');
}
/* fold or open one band, or all of them. A band folded from deep inside its
   rows would leave the reader looking at whatever slid up from below; the
   band is brought back under the header instead. */
function catFold(key, shut) {
  var want = shut === undefined ? !catShut(key) : shut;
  cat.folded = cat.folded.filter(function (k) { return k !== key; });
  if (want) cat.folded.push(key);
  render();
  if (!want) return;
  var band = [].slice.call(document.querySelectorAll('#catTbl tr.cat-band')).filter(function (tr) {
    return tr.dataset.catfold === key;
  })[0];
  if (band) catBandIntoView(band);
}
/* a band the scroll has left behind is still on show, held under the header:
   brought back to where it starts, so what follows it is what follows it */
function catBandIntoView(band) {
  var wrap = document.getElementById('catTblWrap'), head = document.querySelector('#catTbl thead');
  if (!wrap || !head || !band) return;
  var top = band.parentNode.offsetTop - head.offsetHeight;   /* the scroll that puts the band under the header */
  if (top < wrap.scrollTop) catScrollTo(top);
}
function catFoldAll(shut) {
  cat.folded = shut ? catBands().map(function (g) { return g.key; }) : [];
  render(); catScrollTo(0);
}

/* ---- rendering: the dialog --------------------------------------------- */
/* Whether there is anything to save, said in the footer rather than left to
   the Save button's enabled state (F2). An enabled button is a weak signal
   for state in an editor whose draft survives a lot of navigation - and it
   made the leaving notice abrupt, because the first mention of unsaved work
   was a warning about losing it. */
function saveStateHtml() {
  if (!repo.draft) return '';
  if (repo.saving) return '<span class="repo-state">Saving…</span>';
  if (repo.dirty) return '<span class="repo-state is-dirty">Unsaved changes</span>';
  if (repo.savedAt) {
    return '<span class="repo-state is-saved">Saved ' + esc(clockTime(repo.savedAt)) + '</span>';
  }
  return '';
}
function clockTime(iso) {
  var t = new Date(iso);
  if (isNaN(t.getTime())) return '';
  return ('0' + t.getHours()).slice(-2) + ':' + ('0' + t.getMinutes()).slice(-2);
}

/* ---- unsaved work, across a reload (F1) ----------------------------------
   Switching view, closing the dialog and moving to another sleeve were all
   guarded, named the sleeve and offered Discard. The fourth exit - a refresh,
   a closed tab, a crash - lost a twelve-product sleeve silently, while the
   proposal tool beside it snapshots its own state on every render. Two
   halves: the browser's own prompt, and a copy kept locally so the answer to
   "did I lose it" is no rather than a prompt.

   Keyed by what the draft IS, so a draft of one sleeve cannot be offered back
   over another. Wrapped, because storage throws in a private window. */
var DRAFT_KEY = 'pmg.repository.draft';

function draftKeyFor(d) {
  if (!d) return null;
  if (d.id) return 'sleeve:' + d.id;
  if (d.edition) return 'edition:' + d.edition.ofId;
  return 'new:' + (d.category || repo.category) + '|' + (repo.variant || '');
}

/* Written while there is something to lose, and cleared only by the three
   things that mean it is no longer wanted: a save, a discard, and the admin
   turning the offer down. Clearing it merely because the draft in hand is
   clean would erase it on the first render after the reload it exists for. */
function keepDraft() {
  if (!repo.draft || !repo.dirty) return;
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify({
      key: draftKeyFor(repo.draft), at: new Date().toISOString(),
      category: repo.category, variant: repo.variant, draft: repo.draft
    }, function (k, v) { return typeof v === 'number' && !isFinite(v) ? '' : v; }));   /* NaN would come back as null, read as 0 */
  } catch (e) { /* private window, or full: the prompt still stands */ }
}

function forgetKeptDraft() {
  try { window.localStorage.removeItem(DRAFT_KEY); } catch (e) {}
}

/* Offered back only for the draft it was taken from, and only when what is
   on screen has not itself been edited. */
function keptDraftFor(d) {
  var want = draftKeyFor(d);
  if (!want) return null;
  try {
    var raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    var held = JSON.parse(raw);
    return (held && held.key === want && held.draft) ? held : null;
  } catch (e) { return null; }
}

window.addEventListener('beforeunload', function (e) {
  if (!repo.open || !repo.dirty) return;
  keepDraft();
  e.preventDefault();
  e.returnValue = '';           /* the wording is the browser's, not ours */
  return '';
});

function render() {
  var host = document.getElementById('repoDialog'); if (!host) return;
  if (!repo.open) {
    host.innerHTML = ''; host.hidden = true; App.setBackgroundInert(false); return;
  }
  host.hidden = false;
  App.setBackgroundInert(true);
  var focused = document.activeElement && document.activeElement.id;
  var putBack = catScrollKeep();
  var svPutBack = svScrollKeep();
  var d = repo.data;
  var onCatalogue = repo.view === 'catalogue';
  var onArchive = repo.view === 'archive', onActivity = repo.view === 'activity';
  var onRegister = repo.view === 'proposals', onUncalled = repo.view === 'uncalled';
  var onOverlays = repo.view === 'overlays';
  var onSleeves = !onCatalogue && !onArchive && !onActivity && !onRegister && !onUncalled && !onOverlays;

  /* One tab strip in the header, and it navigates (A1). The implementation
     type used to sit beside it, identical in shape and selected state but
     filtering rather than navigating - and it existed on one view of five, so
     the header changed size as you moved. It is a property of the sleeve
     list, so it has moved onto the sleeve list's own header. */
  var VIEWS = [['sleeves', 'Sleeves', onSleeves, 0],
               ['catalogue', 'Catalogue', onCatalogue, 0],
               ['archive', 'Archive', onArchive, d ? archivedSleeves().length : 0],
               ['activity', 'Activity', onActivity, 0],
               ['proposals', 'Proposals', onRegister, d && d.register ? d.register.proposals : 0],
               ['uncalled', 'Uncalled Capital Allocation', onUncalled, 0],
               ['overlays', 'Overlay Funding', onOverlays, 0]];
  var header = '<div class="repo-h"><h2 id="repoTitle" class="dlg-shout">Repository</h2>'
    + '<div class="repo-seg" role="tablist" aria-label="View">'
    + VIEWS.map(function (v) {
        /* the pair is one tab stop; the arrows move within it (G1) */
        return '<button type="button" role="tab" id="repotab-' + v[0] + '"'
          + ' data-repoview="' + v[0] + '" aria-selected="' + v[2] + '"'
          + ' aria-controls="repoPanel" tabindex="' + (v[2] ? '0' : '-1') + '">' + esc(v[1])
          + (v[3] ? '<span class="n">' + v[3] + '</span>' : '') + '</button>';
      }).join('')
    + '</div>';
  if (d) {
    /* what the date is OF (A3): it is the product catalogue's, and on the
       sleeves view it sat directly over a sleeve carrying its own saved-on
       line, where an unlabelled date reads as that sleeve's */
    header += '<span class="repo-src">Catalogue as of ' + esc(shortDate(d.catalogue.modified)) + '</span>';
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
  } else if (onUncalled) {
    body = uncalledViewHtml();
  } else if (onOverlays) {
    body = overlaysViewHtml();
  } else {
    body = sleevesViewHtml();
  }

  var leaving = '';
  if (repo.leaving) {
    leaving = '<div class="repo-notice" role="alertdialog" aria-live="assertive">'
      + '<span>You have unsaved changes to ' + esc(svDraftWords()) + '.</span>'
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
  } else if (onUncalled) {
    footer = uncalledFooterHtml();
  } else if (onOverlays) {
    footer = overlaysFooterHtml();
  } else if (onCatalogue && d) {
    footer = catKeysHtml()
      + '<div class="repo-f cat-f">' + catTrayHtml()
      + (repo.error ? '<span class="md-err" role="alert">' + esc(repo.error) + '</span>' : '')
      + '</div>';
  } else {
    /* Save lives with the sleeve it saves - the bar over the page in Cards,
       the drawer's footer in Table (D156) - so the footer only says what the
       library holds, in words rather than as a file name */
    var editingNow = !!repo.draft && svEditing(repo.draft, sv.editing, repo.dirty)
      && (sv.mode === 'table' ? sv.drawer : sv.level === 2);
    footer = '<div class="repo-f">'
      + (d ? '<span class="repo-src">' + d.store.sleeves + ' sleeves in the library \u00b7 '
          + (d.store.revisions || 0) + ' versions on record'
          + (d.store.archived ? ' \u00b7 ' + d.store.archived + ' in the Archive' : '') + '</span>' : '')
      + '<span class="spacer"></span>'
      + (repo.error && !editingNow ? '<span class="md-err" role="alert">' + esc(repo.error) + '</span>' : '')
      + '</div>';
  }

  /* the tabs name this, and it is labelled by whichever is selected (G1) */
  body = '<div id="repoPanel" role="tabpanel" aria-labelledby="repotab-' + repo.view + '">'
    + body + '</div>';

  host.innerHTML = '<div class="scrim" data-reposcrim></div>'
    + '<div class="dialog repo' + (onCatalogue ? ' catalogue' : '') + (onArchive ? ' archive' : '') + (onActivity ? ' activity' : '') + (onRegister ? ' register' : '')
    + (onUncalled ? ' uncalled' : '') + (onOverlays ? ' uncalled overlays' : '') + (onSleeves ? ' sleeves' : '') + '" role="dialog" aria-modal="true" aria-labelledby="repoTitle">'
    + header + leaving + body + footer + '</div>';
  syncHash();
  if (onOverlays) ovlAfterRender();
  keepDraft();                  /* the local copy follows the draft (F1) */
  putBack(); catEdge(); svPutBack();

  if (focused) {
    var again = document.getElementById(focused);
    if (again && again.focus) {
      /* the catalogue has just put its own scroll back, and a band's button
         taking focus must not move it again */
      again.focus(onCatalogue ? { preventScroll: true } : undefined);
      if ((again.id === 'repoSearch' || again.id === 'catSearch' || again.id === 'arcSearch' || again.id === 'actSearch' || again.id === 'regSearch') && again.setSelectionRange && again.type !== 'search') {
        var end = again.value.length; again.setSelectionRange(end, end);
      }
    }
  }
  /* the Sleeves view's own focus, after the restore above (D156) */
  if (onSleeves) svAfterRender();
}

function updateTotals() {
  var tot = document.getElementById('repoTotal'); if (tot) tot.innerHTML = totalHtml();
  document.querySelectorAll('#repoDialog [data-reposave]').forEach(function (b) {
    b.disabled = !(repo.dirty && !draftProblems().length && !repo.saving);
  });
  /* typing is a partial redraw, so the editing controls have to be told (F2) */
  svRefreshControls();
  ncRefresh();
  keepDraft();
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
  var putBack = catScrollKeep();
  var table = document.getElementById('catTbl');
  if (table) {
    [].slice.call(table.tBodies).forEach(function (b) { table.removeChild(b); });
    table.insertAdjacentHTML('beforeend', catBodyHtml());
  }
  var facets = document.getElementById('catFacets'); if (facets) facets.outerHTML = catFacetsHtml();
  var count = document.getElementById('catCount'); if (count) count.innerHTML = catCountText();
  var foldAll = document.getElementById('catFoldAll'); if (foldAll) foldAll.innerHTML = catFoldAllHtml();
  var tray = document.getElementById('catTray'); if (tray) tray.outerHTML = catTrayHtml();
  syncHash();
  putBack(); catEdge();
}
/* A redraw replaces the boxes that scroll, and a new box starts at its top:
   a pin far down the list, or a facet ticked at the foot of the rail, threw
   the reader back to the start. What was scrolled is put back where it was. */
function catScrollKeep() {
  var saved = ['catTblWrap', 'catFacets'].map(function (id) {
    var el = document.getElementById(id);
    return el ? { id: id, top: el.scrollTop, left: el.scrollLeft } : null;
  });
  return function () {
    saved.forEach(function (s) {
      var el = s && document.getElementById(s.id);
      if (el) { el.scrollTop = s.top; el.scrollLeft = s.left; }
    });
  };
}
function catScrollTo(top) {
  var wrap = document.getElementById('catTblWrap'); if (wrap) wrap.scrollTop = Math.max(0, top);
}
/* the header, and banded the band under it, stand over the rows: the
   stylesheet's scroll-padding keeps the cursor's row clear of both */
function catScrollCursorIntoView(block) {
  var row = catCursorRow();
  if (row && row.scrollIntoView) row.scrollIntoView({ block: block || 'nearest', inline: 'nearest' });
}
/* The shadow at the table's right edge (D99), for as long as there are
   columns beyond it. Placed from the box as measured, so it sits inside a
   classic scrollbar and against the chevron column, whatever their widths. */
function catEdge() {
  var wrap = document.getElementById('catTblWrap'), edge = document.getElementById('catMore');
  if (!wrap || !edge) return;
  var go = wrap.querySelector('th.go');
  edge.style.right = (wrap.offsetWidth - wrap.clientWidth + (go ? go.offsetWidth : 0)) + 'px';
  edge.style.bottom = (wrap.offsetHeight - wrap.clientHeight) + 'px';
  edge.classList.toggle('on', wrap.scrollWidth - wrap.clientWidth - wrap.scrollLeft > 1);
}

/* ---- the entry points --------------------------------------------------- */
/* ---- the way in from the landing page (D106) ------------------------------
   The console has never needed a scenario - it opens cold at #repository and
   works - but the only visible entry was the rail's admin bar, and the rail
   is display:none on the landing page. So an admin arriving to maintain the
   library had to know a URL fragment, or start a proposal they did not want
   in order to reveal the way to the thing they did.

   The strip says whose session this is before it says what the session
   opens: a row that appears for some people and not others should explain
   why it is there, and on a shared machine it also answers "am I still
   signed in as them". The five views are named rather than hidden behind one
   "Repository" link, because the admin knows which of them they came for. */
var LANDING_VIEWS = [
  ['repository', 'Sleeves'], ['catalogue', 'Catalogue'], ['archive', 'Archive'],
  ['activity', 'Activity'], ['proposals', 'Proposals'], ['uncalled', 'Uncalled Capital Allocation'],
  ['overlays', 'Overlay Funding']
];

function renderLandingAdmin(show) {
  var host = document.getElementById('lpadmin'); if (!host) return;
  /* Only on the landing page: in the workspace the rail carries the same two
     entry points, and a second copy under the document would be furniture. */
  var on = show && App.phase() !== 'workspace';
  host.hidden = !on;
  if (!on) { host.innerHTML = ''; return; }
  var who = App.opt('capabilities.user', '');
  var html = '<div class="lp-admin-who"><span class="lp-admin-role">Admin</span>'
    + (who ? '<span>Signed in as <b>' + esc(who) + '</b></span>'
           : '<span>You can maintain the library</span>')
    + '</div>'
    + '<nav class="lp-admin-links" aria-label="The library">'
    + LANDING_VIEWS.map(function (v, i) {
        return (i ? '<span class="lp-admin-dot" aria-hidden="true">\u00b7</span>' : '')
          + '<a href="#' + v[0] + '" data-repoopen="' + v[0] + '">' + esc(v[1]) + '</a>';
      }).join('')
    + '</nav>';
  if (host.innerHTML !== html) host.innerHTML = html;
}

function renderEntryLinks() {
  var show = canAdmin();
  /* the whole bar, not the individual buttons: an empty bordered strip would
     be worse than no strip (D62) */
  var bar = document.getElementById('railadmin');
  if (bar) bar.hidden = !show;
  renderLandingAdmin(show);
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
    if (show && window.location.hash.indexOf('#uncalled') === 0) openRepository(null, 'uncalled');
    if (show && window.location.hash.indexOf('#overlays') === 0) openRepository(null, 'overlays');
  }
}

/* The fee card, if something opens it above this dialog.
   Closing it releases the page's inert state, which this dialog still needs;
   watching the card's host puts it back. */
(function watchFeeDialog() {
  var feeHost = document.getElementById('feeDialog');
  if (!feeHost || typeof MutationObserver === 'undefined') return;
  new MutationObserver(function () {
    if (feeHost.hidden && repo.open) {
      App.setBackgroundInert(true);
    }
  }).observe(feeHost, { attributes: true, attributeFilter: ['hidden'] });
})();

/* ---- the Uncalled Capital Allocation view (D148, D149) -------------------
   How a private-markets commitment is held until capital is called. The house
   split applies to every implementation type; a type may carry its own. The
   weights are percentages to four places adding up to exactly 100, and only a
   category every private-markets book holds may take a share - the server
   says which, with the count, and refuses the rest; the checks here only
   save a round trip. Every change carries a note and adds a revision. */
var FUND_PLACES = 4;

async function loadFunding() {
  fund.busy = true; fund.error = null; render();
  try {
    var r = await api('GET', '/scenario/repository/funding');
    if (!r) return;
    if (!r.ok) throw new Error(r.body.error || ('Could not load the funding split (' + r.status + ')'));
    fund.data = r.body; fund.loaded = true;
    if (fund.scope !== '*' && fund.data.variants.indexOf(fund.scope) === -1) fund.scope = '*';
  } catch (err) { fund.error = err.message; }
  fund.busy = false;
  render();
  loadFundingHistory();
}

async function loadFundingHistory() {
  var scope = fund.scope;
  fund.historyBusy = true;
  try {
    var r = await api('GET', '/scenario/repository/funding/history?scope=' + encodeURIComponent(scope));
    if (r && r.ok && scope === fund.scope) fund.history = { scope: scope, entries: r.body.history || [] };
  } catch (err) { /* the history is a side panel; the split itself still shows */ }
  fund.historyBusy = false;
  if (repo.open && repo.view === 'uncalled') render();
}

/* the split in force for the chosen scope, and whether it is the house
   split standing in for a type with none of its own */
function fundInForce() {
  var d = fund.data; if (!d) return null;
  if (fund.scope === '*') return { entry: d.house, inherited: false };
  var own = (d.overrides || {})[fund.scope];
  return own ? { entry: own, inherited: false } : { entry: d.house, inherited: true };
}
function fundRowsFrom(entry) {
  return (entry && entry.destinations || []).map(function (x) {
    return { category: x.category, weight: String(x.weightPct) };
  });
}
/* the rows on screen: the draft while there is one, else the split in force */
function fundRows() {
  if (fund.draft) return fund.draft;
  var now = fundInForce();
  return now ? fundRowsFrom(now.entry) : [];
}
function fundDirty() {
  if (!fund.draft) return false;
  var now = fundInForce();
  if (!now || now.inherited) return true;          /* a new override is a change */
  var was = fundRowsFrom(now.entry);
  if (was.length !== fund.draft.length) return true;
  return fund.draft.some(function (row, i) {
    return row.category !== was[i].category || parseFloat(row.weight) !== parseFloat(was[i].weight);
  });
}
function fundEligible() { return (fund.data && fund.data.eligible) || []; }
function fundUnits(text) {
  var v = parseFloat(String(text).replace(/[%,\s]/g, ''));
  return isFinite(v) ? v : NaN;
}
/* what the server would refuse, in its words, so Save stays off until none */
function fundProblems(rows) {
  var out = [], seen = {}, units = 0, scale = Math.pow(10, FUND_PLACES);
  var byName = {}; fundEligible().forEach(function (e) { byName[e.category] = e; });
  if (!rows.length) out.push('Name at least one category to hold the money.');
  var most = (fund.data && fund.data.maxDestinations) || 3;
  if (rows.length > most) out.push('At most ' + most + ' categories can hold the money.');
  rows.forEach(function (row) {
    var name = row.category;
    if (seen[name]) out.push(name + ' is named twice.');
    seen[name] = 1;
    var e = byName[name];
    if (e && !e.eligible) {
      out.push(name + ' is held by only ' + e.heldBy + ' of the ' + e.of + ' private-markets portfolios; the other '
        + (e.of - e.heldBy) + ' would have nowhere to put its share.');
    }
    var w = fundUnits(row.weight);
    if (!(w > 0)) { out.push('Give ' + name + ' a weight above zero.'); return; }
    if (w > 100) out.push(name + '’s weight is over 100%.');
    if (Math.abs(w * scale - Math.round(w * scale)) > 1e-6) out.push(name + '’s weight has more than ' + FUND_PLACES + ' decimal places.');
    units += Math.round(w * scale);
  });
  if (rows.length && units !== 100 * scale && !out.some(function (m) { return /above zero/.test(m); })) {
    out.push('The weights add up to ' + (units / scale).toFixed(FUND_PLACES) + '%; they must add up to exactly 100%.');
  }
  return out;
}
function fundTotalText(rows) {
  var scale = Math.pow(10, FUND_PLACES), units = 0;
  rows.forEach(function (row) { var w = fundUnits(row.weight); if (w > 0) units += Math.round(w * scale); });
  return (units / scale).toFixed(FUND_PLACES) + '%';
}
/* the rule in words: what a 10% commitment is held as until called */
function fundExample(rows) {
  if (fundProblems(rows).length) return '';
  return 'A 10.00% commitment is held '
    + rows.map(function (row) {
        return (fundUnits(row.weight) / 10).toFixed(2) + '% in ' + esc(row.category);
      }).join(' and ') + ' until capital is called.';
}
var FUND_COLOURS = ['#2A78D6', '#1BAF7A', '#EB6834'];
function fundBarHtml(rows) {
  var total = 0; rows.forEach(function (r) { var w = fundUnits(r.weight); if (w > 0) total += w; });
  if (!(total > 0)) return '';
  return rows.map(function (r, i) {
    var w = fundUnits(r.weight); if (!(w > 0)) return '';
    return '<span style="width:' + (w / total * 100) + '%;background:' + FUND_COLOURS[i % 3] + '">'
      + esc(String(+w.toFixed(FUND_PLACES))) + '%</span>';
  }).join('');
}

function uncalledViewHtml() {
  if (!fund.data) {
    return '<div class="ucap-b"><p class="repo-loading">'
      + (fund.error ? esc(fund.error) : 'Loading the funding split…') + '</p></div>';
  }
  var d = fund.data, now = fundInForce(), rows = fundRows(), editing = !!fund.draft;
  var readOnly = now.inherited && !editing;
  var scopes = '<option value="*"' + (fund.scope === '*' ? ' selected' : '') + '>House split · every implementation type</option>'
    + d.variants.map(function (v) {
        var own = (d.overrides || {})[v];
        return '<option value="' + esc(v) + '"' + (fund.scope === v ? ' selected' : '') + '>'
          + esc(v) + (own ? ' · its own split' : ' · uses the house split') + '</option>';
      }).join('');
  var said = fund.scope === '*'
    ? 'Applies to every implementation type that has no split of its own.'
    : now.inherited ? esc(fund.scope) + ' has no split of its own: it uses the house split.'
    : 'Applies to ' + esc(fund.scope) + ' only. Remove it to put the type back on the house split.';
  var elig = fundEligible();
  var body = rows.map(function (row, i) {
    var e = elig.filter(function (x) { return x.category === row.category; })[0];
    var choose = readOnly ? esc(row.category)
      : '<select id="fundCat' + i + '" data-fundcat="' + i + '" aria-label="Category ' + (i + 1) + '">'
        + elig.map(function (x) {
            return '<option value="' + esc(x.category) + '"' + (x.category === row.category ? ' selected' : '')
              + (x.eligible ? '' : ' disabled') + '>' + esc(x.category)
              + (x.eligible ? '' : ' (held by ' + x.heldBy + ' of ' + x.of + ')') + '</option>';
          }).join('') + '</select>';
    var weight = readOnly ? esc(row.weight) + '%'
      : '<span class="fund-w"><input id="fundW' + i + '" data-fundw="' + i + '" inputmode="decimal" autocomplete="off"'
        + ' value="' + esc(row.weight) + '" aria-label="Weight for ' + esc(row.category) + ', percent"><span>%</span></span>';
    return '<tr><td>' + choose + '</td><td class="fund-held">'
      + (e ? e.heldBy + ' of ' + e.of + ' portfolios' : '') + '</td><td class="num">' + weight + '</td><td>'
      + (readOnly || rows.length < 2 ? '' : '<button type="button" class="btn btn-danger fund-rm" data-fundrm="' + i + '" aria-label="Remove ' + esc(row.category) + '">Remove</button>')
      + '</td></tr>';
  }).join('');
  var most = d.maxDestinations || 3;
  var canAdd = !readOnly && rows.length < most && elig.some(function (x) {
    return x.eligible && !rows.some(function (r) { return r.category === x.category; });
  });
  var problems = readOnly ? [] : fundProblems(rows);
  var hist = fund.history && fund.history.scope === fund.scope ? fund.history.entries : null;
  var histHtml = hist == null ? '<p class="repo-loading">' + (fund.historyBusy ? 'Loading…' : '') + '</p>'
    : !hist.length ? '<p class="fund-none">No revisions yet: ' + esc(fund.scope) + ' has always used the house split.</p>'
    : '<ol class="fund-hist">' + hist.map(function (h, i) {
        var what = h.destinations ? h.destinations.map(function (x) { return esc(x.category) + ' ' + esc(String(x.weightPct)) + '%'; }).join(' · ')
          : 'Back on the house split';
        var restorable = i > 0 && h.destinations;
        return '<li><div class="fund-hist-h"><b>r' + h.revision + '</b> <span class="act">' + esc(h.action) + '</span>'
          + (i === 0 && !now.inherited ? ' <span class="now">in force</span>' : '')
          + (restorable ? '<button type="button" class="cat-link" data-fundrestore="' + h.revision + '">Restore</button>' : '')
          + '</div><div class="fund-hist-w">' + what + '</div>'
          + (h.note ? '<div class="fund-hist-n">' + esc(h.note) + '</div>' : '')
          + '<div class="fund-hist-by">' + esc(h.actor || '') + ' · ' + esc(shortDate(h.at)) + '</div></li>';
      }).join('') + '</ol>';
  return '<div class="ucap-b">'
    + '<section class="ucap-main" aria-labelledby="fundTitle">'
    + '<h3 id="fundTitle">Uncalled capital allocation</h3>'
    + '<p class="ucap-lede">Until capital is called, a private-markets commitment is held in these categories, in these weights. '
    + 'The initial allocation on the Implementation screen, the workbook’s Initial Allocation sheet and the deck’s slide all follow it. '
    + 'A change applies to scenarios in progress at once; a delivered proposal keeps the split it was built with, and the register records which.</p>'
    + '<div class="ucap-scope"><label for="fundScope">Applies to</label><select id="fundScope">' + scopes + '</select>'
    + '<span class="ucap-said">' + said + '</span></div>'
    + '<table class="fund-ed"><thead><tr><th scope="col">Held in</th><th scope="col">Held by</th><th scope="col" class="num">Weight</th><th></th></tr></thead>'
    + '<tbody>' + body + '</tbody>'
    + '<tfoot><tr><th scope="row">Total</th><td></td><td class="num" id="fundTotal">' + fundTotalText(rows) + '</td><td></td></tr></tfoot></table>'
    + '<div class="fund-acts">'
    + (canAdd ? '<button type="button" class="btn" data-fundadd>+ Add a category</button>' : '')
    + (fund.scope !== '*' && now.inherited && !editing ? '<button type="button" class="btn" data-fundown>Give ' + esc(fund.scope) + ' its own split</button>' : '')
    + (fund.scope !== '*' && !now.inherited && !editing ? '<button type="button" class="btn btn-danger" data-funddrop>Put ' + esc(fund.scope) + ' back on the house split</button>' : '')
    + '</div>'
    + '<div class="fund-bar" id="fundBar" aria-hidden="true">' + fundBarHtml(rows) + '</div>'
    + '<ul class="fund-msgs" id="fundMsgs">' + problems.map(function (m) { return '<li>' + esc(m) + '</li>'; }).join('') + '</ul>'
    + '<p class="fund-eg" id="fundEg">' + fundExample(rows) + '</p>'
    + '<p class="fund-rule">Only a category every private-markets portfolio holds can take a share: otherwise a portfolio without it would have nowhere to put it. '
    + 'Weights are percentages with up to ' + FUND_PLACES + ' decimal places, so a third is 33.3333.</p>'
    + '</section>'
    + '<aside class="ucap-side" aria-label="History"><h4>History · ' + (fund.scope === '*' ? 'house split' : esc(fund.scope)) + '</h4>' + histHtml + '</aside>'
    + '</div>';
}

function uncalledFooterHtml() {
  var rows = fundRows(), dirty = fundDirty();
  var ok = dirty && !fundProblems(rows).length && fund.note.trim() && !fund.saving;
  return '<div class="repo-f">'
    + '<label class="fund-note" for="fundNote">Note</label>'
    + '<input id="fundNote" class="fund-note-in" type="text" autocomplete="off" value="' + esc(fund.note) + '"'
    + ' placeholder="Why the split is changing (required)">'
    + '<span class="spacer"></span>'
    + (fund.error ? '<span class="md-err" role="alert">' + esc(fund.error) + '</span>' : '')
    + (fund.draft ? '<button type="button" class="btn" data-funddiscard>Discard</button>' : '')
    + '<button type="button" class="btn btn-primary" id="fundSave" data-fundsave' + (ok ? '' : ' disabled') + '>'
    + (fund.saving ? 'Saving…' : 'Save split') + '</button></div>';
}

/* the parts of the view that follow typing, redrawn without the boxes so the
   caret stays where it was */
function fundUpdate() {
  var rows = fundRows();
  var total = document.getElementById('fundTotal'); if (total) total.textContent = fundTotalText(rows);
  var bar = document.getElementById('fundBar'); if (bar) bar.innerHTML = fundBarHtml(rows);
  var msgs = document.getElementById('fundMsgs');
  if (msgs) msgs.innerHTML = fundProblems(rows).map(function (m) { return '<li>' + esc(m) + '</li>'; }).join('');
  var eg = document.getElementById('fundEg'); if (eg) eg.innerHTML = fundExample(rows);
  var save = document.getElementById('fundSave');
  if (save) save.disabled = !(fundDirty() && !fundProblems(rows).length && fund.note.trim() && !fund.saving);
}

function fundStartDraft() {
  if (!fund.draft) fund.draft = fundRows().map(function (r) { return { category: r.category, weight: r.weight }; });
}

async function fundPost(method, path, body) {
  fund.saving = true; fund.error = null; render();
  try {
    var r = await api(method, path, body);
    if (!r) return false;
    if (!r.ok) { fund.error = r.body.error || ('Could not save (' + r.status + ')'); return false; }
    fund.data = r.body.funding || fund.data;
    fund.draft = null; fund.note = '';
    return true;
  } catch (err) { fund.error = err.message; return false; }
  finally {
    fund.saving = false; render(); loadFundingHistory();
  }
}

function fundClick(ds) {
  if (ds.fundadd !== undefined) {
    fundStartDraft();
    var free = fundEligible().filter(function (x) {
      return x.eligible && !fund.draft.some(function (r) { return r.category === x.category; });
    })[0];
    if (free) fund.draft.push({ category: free.category, weight: '' });
    render(); return true;
  }
  if (ds.fundrm !== undefined) {
    fundStartDraft(); fund.draft.splice(parseInt(ds.fundrm, 10), 1); render(); return true;
  }
  if (ds.fundown !== undefined) { fundStartDraft(); render(); return true; }
  if (ds.funddiscard !== undefined) { fund.draft = null; fund.note = ''; fund.error = null; render(); return true; }
  if (ds.fundsave !== undefined) {
    fundPost('PUT', '/scenario/repository/funding', {
      scope: fund.scope, note: fund.note,
      destinations: fund.draft.map(function (r) { return { category: r.category, weightPct: fundUnits(r.weight) }; })
    });
    return true;
  }
  if (ds.funddrop !== undefined) {
    if (!fund.note.trim()) { fund.error = 'Write a note first: removing a type’s split is a change like any other.'; render(); return true; }
    fundPost('POST', '/scenario/repository/funding/remove', { scope: fund.scope, note: fund.note });
    return true;
  }
  if (ds.fundrestore !== undefined) {
    if (!fund.note.trim()) { fund.error = 'Write a note first: a restore is a new revision, and it says why.'; render(); return true; }
    fundPost('POST', '/scenario/repository/funding/revert', {
      scope: fund.scope, revision: parseInt(ds.fundrestore, 10), note: fund.note
    });
    return true;
  }
  return false;
}

document.addEventListener('change', function (e) {
  if (!repo.open || repo.view !== 'uncalled') return;
  var el = e.target;
  if (el.id === 'fundScope') {
    fund.scope = el.value; fund.draft = null; fund.note = ''; fund.error = null; fund.history = null;
    render(); loadFundingHistory(); return;
  }
  if (el.dataset && el.dataset.fundcat !== undefined) {
    fundStartDraft(); fund.draft[parseInt(el.dataset.fundcat, 10)].category = el.value; render();
  }
});

document.addEventListener('input', function (e) {
  if (!repo.open || repo.view !== 'uncalled') return;
  var el = e.target;
  if (el.id === 'fundNote') { fund.note = el.value; fund.error = null; fundUpdate(); return; }
  if (el.dataset && el.dataset.fundw !== undefined) {
    fundStartDraft(); fund.draft[parseInt(el.dataset.fundw, 10)].weight = el.value.trim(); fundUpdate();
  }
});

/* ---- the Overlay Funding view (D155) -------------------------------------
   The overlays - the tilt, the premium and any the desk adds - as an ORDERED
   list of rules, resolved top to bottom: each rule reads the allocation as
   the rules above it left it. A card per rule says where it sits, what it
   takes and where it goes; drag a card by its handle, or use its arrows, to
   change the order. Edit opens a drawer on the rule, modal to the keyboard.
   The panel on the right resolves the list, step by step, on a real
   portfolio of the type under the switches chosen there, with the same engine
   the Implementation screen uses (App.resolveOverlays, the mirror of
   rules.resolveOverlays). A rule some portfolio of the type holds the sources
   of but could not fund - under any setting of the proposal's switches - is
   drawn red, says which portfolio, and blocks Save; the server makes the same
   check and its word is final. Every change carries a note and adds a
   revision; a save names the list it was made against, and one made against
   a list someone has since changed is refused (409) and offers a reload. */
var OVL_PLACES = 4;
var OVL_COLOURS = ['#5E7690', '#B8962E', '#7A5C99', '#2E8C8C', '#9C6B4E', '#6B7F3A'];
var OVL_SWITCH_NAMES = { tacticalTilt: 'Tactical Tilts', volPremium: 'Strategic Volatility Premium' };
var OVL_TOGGLE_WORDS = { tacticalTilt: 'the proposal’s Tactical Tilts switch',
                         volPremium: 'the proposal’s Strategic Volatility Premium switch' };
/* a number as the desk types it: digits, one point, an optional % */
var OVL_NUMBER = /^\s*(\d+(\.\d*)?|\.\d+)\s*%?\s*$/;

var ovl = {
  loaded: false, busy: false, saving: false, error: null,
  stale: false,                /* the last save was refused as stale (409) */
  data: null,                  /* describe(): lists, categories, portfolios, samples */
  scope: '*',                  /* '*' for the house list, or a type */
  draft: null,                 /* [rule] while the desk is editing; sizes and weights as typed */
  base: null,                  /* { scope, revision } of the list the draft was made against */
  note: '',
  history: null,               /* { scope, entries } */
  historyBusy: false,
  edit: null,                  /* the index of the rule open in the drawer */
  editSnap: null,              /* the draft as it was when the drawer opened, for Cancel */
  editFresh: false,            /* the open rule was just added: Cancel removes it */
  drag: null,                  /* the index of the card being dragged */
  sample: null,                /* the keyStr of the portfolio the trace resolves */
  traceSel: null,              /* the switches the trace resolves under; null = every switch on */
  leaving: null,               /* { scope } | { close } while an unsaved draft blocks a move */
  undo: null,                  /* { rule, index } after a card is removed */
  flash: ''                    /* what the last successful write said */
};

async function loadOverlays() {
  ovl.busy = true; ovl.error = null; render();
  try {
    var r = await api('GET', '/scenario/repository/overlays');
    if (!r) return;
    if (!r.ok) throw new Error(r.body.error || ('Could not load the overlay rules (' + r.status + ')'));
    ovl.data = r.body; ovl.loaded = true;
    if (ovl.scope !== '*' && ovl.data.variants.indexOf(ovl.scope) === -1) ovl.scope = '*';
  } catch (err) { ovl.error = err.message; }
  ovl.busy = false;
  render();
  loadOverlayHistory();
}

async function loadOverlayHistory() {
  var scope = ovl.scope;
  ovl.historyBusy = true;
  try {
    var r = await api('GET', '/scenario/repository/overlays/history?scope=' + encodeURIComponent(scope));
    if (r && r.ok && scope === ovl.scope) ovl.history = { scope: scope, entries: r.body.history || [] };
  } catch (err) { /* the history is a side panel; the rules themselves still show */ }
  ovl.historyBusy = false;
  if (repo.open && repo.view === 'overlays') render();
}

/* ---- what is in force, and the draft ---- */
function ovlInForce() {
  var d = ovl.data; if (!d) return null;
  if (ovl.scope === '*') return { entry: d.house, inherited: false };
  var own = (d.overrides || {})[ovl.scope];
  return own ? { entry: own, inherited: false } : { entry: d.house, inherited: true };
}
function ovlClone(x) { return JSON.parse(JSON.stringify(x)); }
/* a rule as the draft holds it: the size and the weights as the text typed */
function ovlDraftRule(rule) {
  var r = ovlClone(rule);
  r.size = String(rule.size);
  r.sources = (rule.sources || []).map(function (s) { return { category: s.category, weightPct: String(s.weightPct) }; });
  r.currencies = (rule.currencies || []).slice();
  return r;
}
function ovlList() {
  if (ovl.draft) return ovl.draft;
  var now = ovlInForce();
  return now ? now.entry.rules.map(ovlDraftRule) : [];
}
function ovlStartDraft() {
  if (ovl.draft) return;
  var now = ovlInForce();
  ovl.draft = ovlList().map(ovlClone);
  ovl.base = now ? { scope: now.entry.scope, revision: now.entry.revision } : null;
  ovl.flash = '';
}
function ovlDropDraft() {
  ovl.draft = null; ovl.base = null; ovl.note = ''; ovl.error = null; ovl.stale = false;
  ovl.edit = null; ovl.editSnap = null; ovl.undo = null; ovl.leaving = null;
}
/* the text of a size or a weight as a number, or NaN unless it is a clean
   one - '1,5' and '5abc' are not numbers here */
function ovlNum(text) {
  if (typeof text === 'number') return isFinite(text) ? text : NaN;
  return OVL_NUMBER.test(String(text)) ? parseFloat(String(text).replace(/[%\s]/g, '')) : NaN;
}
/* the rules as the engine and the server read them: numbers, not text */
function ovlNumeric(rule) {
  var r = ovlClone(rule);
  r.size = ovlNum(rule.size);
  r.sources = (rule.sources || []).map(function (s) { return { category: s.category, weightPct: ovlNum(s.weightPct) }; });
  r.name = (rule.name || '').trim(); r.into = (rule.into || '').trim();
  r.row = (rule.row || '').trim() || r.into;
  /* a rule with no place of its own sits after its first source */
  r.place = rule.place || (r.sources[0] && r.sources[0].category) || 'end';
  return r;
}
function ovlPayload(list) { return list.map(ovlNumeric); }
function ovlDirty() {
  if (!ovl.draft) return false;
  var now = ovlInForce();
  if (!now || now.inherited) return true;           /* a type's own list is a change */
  return JSON.stringify(ovlPayload(ovl.draft)) !== JSON.stringify(ovlPayload(now.entry.rules.map(ovlDraftRule)));
}

/* ---- whom a list applies to, and the sample ---- */
function ovlVariants() {
  var d = ovl.data; if (!d) return [];
  if (ovl.scope !== '*') return [ovl.scope];
  var inheriting = d.variants.filter(function (v) { return !(d.overrides || {})[v]; });
  return inheriting.length ? inheriting : d.variants.slice();
}
/* the portfolios the types in scope offer, as the picker judges it */
function ovlPortfolios() {
  var d = ovl.data; if (!d) return [];
  var offered = {};
  ovlVariants().forEach(function (v) { (d.offered[v] || []).forEach(function (k) { offered[k] = 1; }); });
  return d.portfolios.filter(function (p) { return offered[p.keyStr]; });
}
function ovlSample() {
  var d = ovl.data; if (!d) return null;
  var scope = ovl.scope === '*' ? d.variants[0] : ovl.scope;
  var inScope = ovlPortfolios();
  var want = ovl.sample || d.samples[scope];
  return inScope.filter(function (p) { return p.keyStr === want; })[0]
    || inScope.filter(function (p) { return p.keyStr === d.samples[scope]; })[0] || inScope[0] || null;
}
function ovlCategoriesOf(portfolio) {
  var d = ovl.data;
  return d.tableCategories.filter(function (c) { return portfolio.weights[c] != null; }).map(function (c) {
    return { name: c, weightPct: portfolio.weights[c], assets: [] };
  });
}
/* every setting of the proposal's switches, as overlayRules._selections */
function ovlSelections() {
  var out = [{}];
  ((ovl.data && ovl.data.toggles) || []).forEach(function (t) {
    var next = [];
    out.forEach(function (s) {
      [true, false].forEach(function (on) { var c = ovlClone(s); c[t] = on; next.push(c); });
    });
    out = next;
  });
  return out;
}
function ovlTraceSel() {
  if (ovl.traceSel) return ovl.traceSel;
  var all = {}; ((ovl.data && ovl.data.toggles) || []).forEach(function (t) { all[t] = true; });
  return all;
}
function ovlResolve(list) {
  var sample = ovlSample();
  return sample ? App.resolveOverlays(ovlCategoriesOf(sample), ovlPayload(list), ovlTraceSel(), sample.currency) : null;
}

/* ---- the checks the server makes, in its words ---- */
function ovlNth(n) {
  var v = n % 100, suffix = (v >= 10 && v <= 20) ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' })[n % 10] || 'th';
  return n + suffix;
}
function ovlNumberProblem(text, what) {
  var v = ovlNum(text);
  if (String(text).trim() === '') return 'Give ' + what + '.';
  if (isNaN(v)) return what.charAt(0).toUpperCase() + what.slice(1) + ' must be a number, such as 7.5 - not “' + String(text).trim() + '”.';
  return null;
}
function ovlRuleProblems(list) {
  var d = ovl.data, strategic = d ? d.categories : [], groups = d ? d.groups : [];
  var taken = {};
  strategic.concat(groups).forEach(function (c) { taken[c.toLowerCase()] = 1; });
  var names = {}, intos = {}, toggles = {}, above = [];
  return list.map(function (rule, i) {
    var out = [], name = (rule.name || '').trim(), into = (rule.into || '').trim();
    if (!name) out.push('Give the rule a name.');
    else if (names[name.toLowerCase()]) out.push('Two rules are called ' + name + '.');
    names[name.toLowerCase()] = 1;
    if (!into) out.push('Name the category it goes into.');
    else if (taken[into.toLowerCase()]) out.push('It cannot go into ' + into + ', a strategic category: an overlay goes into a category of its own.');
    else if (into.toLowerCase() === 'end') out.push('It cannot go into a category called “end”.');
    else if (intos[into.toLowerCase()] != null) out.push('Rule ' + (intos[into.toLowerCase()] + 1) + ' already goes into ' + into + '.');
    if (into) intos[into.toLowerCase()] = i;
    var scale = Math.pow(10, OVL_PLACES);
    var bad = ovlNumberProblem(rule.size, 'the size');
    var size = ovlNum(rule.size);
    if (bad) out.push(bad);
    else if (!(size > 0)) out.push('The size must be above zero.');
    else if (size > 100) out.push('The size is over 100%.');
    else if (Math.abs(size * scale - Math.round(size * scale)) > 1e-6) out.push('The size has more than ' + OVL_PLACES + ' decimal places.');
    var sources = rule.sources || [], seen = {}, units = 0, broken = false;
    if (!sources.length) out.push('Name at least one category it is funded from.');
    if (sources.length > ((d && d.maxSources) || 4)) out.push('At most ' + ((d && d.maxSources) || 4) + ' sources.');
    sources.forEach(function (s) {
      if (seen[s.category]) out.push(s.category + ' is named twice.');
      seen[s.category] = 1;
      var wrong = ovlNumberProblem(s.weightPct, 'the weight on ' + s.category);
      var w = ovlNum(s.weightPct);
      if (wrong) { out.push(wrong); broken = true; return; }
      if (!(w > 0)) { out.push('Give ' + s.category + ' a weight above zero.'); broken = true; return; }
      if (w > 100) out.push('The weight on ' + s.category + ' is over 100%.');
      if (Math.abs(w * scale - Math.round(w * scale)) > 1e-6) out.push('The weight on ' + s.category + ' has more than ' + OVL_PLACES + ' decimal places.');
      units += Math.round(w * scale);
    });
    if (sources.length && !broken && units !== 100 * scale) {
      out.push('The weights add up to ' + (units / scale).toFixed(OVL_PLACES) + '%; they must add up to exactly 100%.');
    }
    var place = rule.place || (sources[0] && sources[0].category) || 'end';
    if (place !== 'end' && strategic.indexOf(place) < 0 && above.indexOf(place) < 0) {
      out.push('It sits after ' + place + ', which is not above it: a rule sits at the end, after a strategic category, or after the category of a rule above it.');
    }
    if (rule.toggle) {
      if (toggles[rule.toggle]) out.push('Two rules answer to the same switch on the proposal.');
      toggles[rule.toggle] = 1;
    }
    if (into) above.push(into);
    return out;
  });
}
/* per rule, the worst portfolio in scope that holds its sources but could
   not fund it at its step under some setting of the switches - what the
   server refuses (overlayRules.shortfalls) */
function ovlShortfalls(list) {
  var worst = list.map(function () { return null; });
  var numeric = ovlPayload(list);
  if (numeric.some(function (r) { return !(r.size > 0) || !r.sources.length || r.sources.some(function (s) { return !(s.weightPct > 0); }); })) return worst;
  var tol = (ovl.data && ovl.data.tolerance) || 1e-9, selections = ovlSelections();
  ovlPortfolios().forEach(function (p) {
    var strategic = ovlCategoriesOf(p);
    selections.forEach(function (sel) {
      var result = strategic;
      numeric.forEach(function (rule, i) {
        if (rule.toggle && !sel[rule.toggle]) return;
        if (rule.currencies && rule.currencies.length && rule.currencies.indexOf(p.currency) < 0) return;
        App.overlayShortfall(rule, result, strategic).forEach(function (s) {
          if (!worst[i] || s.after < worst[i].after - tol) {
            worst[i] = { portfolio: p.label, keyStr: p.keyStr, category: s.category, after: s.after, selection: ovlClone(sel) };
          }
        });
        result = App.resolveOverlays(result, [rule], sel, p.currency).categories;
      });
    });
  });
  return worst;
}
function ovlSwitchWords(sel, list) {
  var used = list.filter(function (r) { return r.toggle; }).map(function (r) { return r.toggle; });
  var parts = ((ovl.data && ovl.data.toggles) || []).filter(function (t) { return used.indexOf(t) >= 0; })
    .map(function (t) { return OVL_SWITCH_NAMES[t] + ' ' + (sel[t] ? 'on' : 'off'); });
  return parts.length ? 'with ' + parts.join(' and ') : '';
}
/* is *after* the list in force with only the order changed? */
function ovlOrderOnly(before, after) {
  if (!before || before.length !== after.length) return false;
  var old = {};
  before.forEach(function (r) { old[r.id] = JSON.stringify(ovlNumeric(r)); });
  var same = after.every(function (r) { return r.id && old[r.id] === JSON.stringify(ovlNumeric(r)); });
  return same && before.map(function (r) { return r.id; }).join() !== after.map(function (r) { return r.id; }).join();
}
function ovlChecks(list) {
  var per = ovlRuleProblems(list), worst = ovlShortfalls(list), now = ovlInForce();
  var inForce = now ? now.entry.rules : null;
  worst.forEach(function (w, i) {
    if (!w) return;
    var rule = list[i], lead = '';
    if (ovlOrderOnly(inForce, list)) {
      var was = inForce.map(function (r) { return r.id; }).indexOf(rule.id);
      var below = inForce.slice(was + 1).map(function (r) { return r.id; });
      var moved = list.slice(0, i).filter(function (r) { return below.indexOf(r.id) >= 0; });
      if (moved.length) {
        lead = 'Moving ' + moved.map(function (r) { return r.name; }).join(' and ') + ' above ' + rule.name
          + ' leaves ' + rule.name + ' unable to fund itself: ';
      }
    }
    var when = ovlSwitchWords(w.selection, list);
    per[i].push(lead + (lead ? 'it' : 'It') + ' would take ' + w.category + ' to ' + w.after.toFixed(2) + '% in ' + w.portfolio
      + (when ? ' ' + when : '') + '. Every portfolio it applies to that holds '
      + rule.sources.map(function (s) { return s.category; }).join(' and ') + ' must be able to fund it.');
  });
  return { per: per, bad: per.some(function (p) { return p.length; }), worst: worst };
}
/* a rule whose category has no sleeve under a type it applies to cannot be
   exported - saved, with a warning */
function ovlWarnings(list) {
  var d = ovl.data; if (!d) return [];
  var out = [];
  list.forEach(function (rule) {
    var into = (rule.into || '').trim(); if (!into) return;
    var missing = ovlVariants().filter(function (v) { return (d.sleeved[v] || []).indexOf(into) < 0; });
    if (missing.length) {
      out.push('No sleeve in ' + into + ' yet for ' + missing.join(', ') + ': a proposal holding '
        + (rule.name || 'this rule') + ' cannot be exported until one is created in the Sleeves view.');
    }
  });
  return out;
}
/* what changed from the list in force, as the history will say it */
function ovlFieldChanges(was, now) {
  var a = ovlNumeric(was), b = ovlNumeric(now), out = [];
  var srcText = function (r) { return r.sources.map(function (s) { return s.category + ' ' + App.overlayPct(s.weightPct) + '%'; }).join(' and '); };
  if (a.name !== b.name) out.push('renamed from ' + a.name);
  if (a.into !== b.into) out.push('goes into ' + b.into + ' (was ' + a.into + ')');
  if (a.row !== b.row) out.push('row ' + b.row + ' (was ' + a.row + ')');
  if (a.place !== b.place) out.push('sits after ' + b.place + ' (was ' + a.place + ')');
  if (a.size !== b.size) out.push('size ' + App.overlayPct(a.size) + '% → ' + App.overlayPct(b.size) + '%');
  if (a.basis !== b.basis) out.push('of ' + (b.basis === 'portfolio' ? 'the portfolio' : 'its sources') + ' (was of ' + (a.basis === 'portfolio' ? 'the portfolio' : 'its sources') + ')');
  if (srcText(a) !== srcText(b)) out.push('funded from ' + srcText(b) + ' (was ' + srcText(a) + ')');
  if ((a.currencies || []).join() !== (b.currencies || []).join()) out.push('held in ' + ((b.currencies || []).join(' · ') || 'any currency'));
  return out;
}
function ovlChange(before, after) {
  var old = {}, now = {}, parts = [];
  before.forEach(function (r) { old[r.id] = r; });
  after.forEach(function (r) { if (r.id) now[r.id] = r; });
  after.forEach(function (r, i) { if (!r.id || !old[r.id]) parts.push('Added ' + (r.name || 'a rule') + ' (' + ovlNth(i + 1) + ')'); });
  before.forEach(function (r) { if (!now[r.id]) parts.push('Removed ' + r.name); });
  after.forEach(function (r) {
    if (r.id && old[r.id] && JSON.stringify(ovlNumeric(r)) !== JSON.stringify(ovlNumeric(old[r.id]))) {
      parts.push(r.name + ': ' + (ovlFieldChanges(old[r.id], r).join(', ') || 'changed'));
    }
  });
  var common = after.filter(function (r) { return r.id && old[r.id]; }).map(function (r) { return r.id; });
  var previously = before.filter(function (r) { return now[r.id]; }).map(function (r) { return r.id; });
  for (var i = 0; i < common.length; i++) {
    if (common[i] !== previously[i]) { parts.push('Order changed: ' + now[common[i]].name + ' moved above ' + now[previously[i]].name); break; }
  }
  return parts.join('; ');
}

/* ---- drawing ---- */
function ovlColour(into, i) {
  return App.categoryColour(into) || OVL_COLOURS[i % OVL_COLOURS.length];
}
function ovlCatSw(name) {
  var c = App.categoryColour(name);
  return '<i class="ovl-sw" style="background:' + (c || '#9AA7B5') + '"></i>';
}
function ovlPc(v) { return (isFinite(v) ? v : 0).toFixed(2) + '%'; }
function ovlWords(rule) {
  var n = ovlNumeric(rule);
  if (!(n.size > 0) || !n.sources.length || n.sources.some(function (s) { return !(s.weightPct > 0); })) return 'Not yet complete';
  return App.overlayWords(n);
}
function ovlScopeHtml(now) {
  var d = ovl.data;
  var scopes = '<option value="*"' + (ovl.scope === '*' ? ' selected' : '') + '>House list · every implementation type</option>'
    + d.variants.map(function (v) {
        var own = (d.overrides || {})[v];
        return '<option value="' + esc(v) + '"' + (ovl.scope === v ? ' selected' : '') + '>'
          + esc(v) + (own ? ' · its own list' : ' · uses the house list') + '</option>';
      }).join('');
  var said = ovl.scope === '*'
    ? 'Applies to every implementation type that has no list of its own.'
    : now.inherited && !ovl.draft ? esc(ovl.scope) + ' has no list of its own: it uses the house list.'
    : now.inherited ? 'Saving gives ' + esc(ovl.scope) + ' a list of its own.'
    : 'Applies to ' + esc(ovl.scope) + ' only. Remove it to put the type back on the house list.';
  return '<div class="ucap-scope"><label for="ovlScope">Rules for</label><select id="ovlScope">' + scopes + '</select>'
    + '<span class="ucap-said">' + said + '</span></div>';
}
function ovlLeavingHtml() {
  if (!ovl.leaving) return '';
  var to = ovl.leaving.close ? 'close the repository' : 'switch to ' + (ovl.leaving.scope === '*' ? 'the house list' : ovl.leaving.scope);
  return '<div class="repo-notice ovl-leaving" role="alertdialog" aria-live="assertive" aria-label="Unsaved changes">'
    + '<span>You have unsaved changes to the ' + (ovl.scope === '*' ? 'house list' : esc(ovl.scope) + ' list') + '.</span>'
    + '<button type="button" class="btn" data-ovl="leavekeep">Keep editing</button>'
    + '<button type="button" class="btn btn-danger" data-ovl="leavego">Discard them and ' + esc(to) + '</button></div>';
}
function ovlCardsHtml(list, chk, res, readOnly) {
  var sample = ovlSample();
  var h = '<ol class="ovl-list" id="ovlList" aria-label="Overlay rules, resolved top to bottom">';
  list.forEach(function (rule, i) {
    var step = res ? res.steps[i] : null, problems = chk.per[i];
    var tags = (rule.toggle ? '<span class="ovl-tag">Switch on the proposal</span>' : '<span class="ovl-tag always">Every portfolio</span>')
      + (rule.currencies && rule.currencies.length ? '<span class="ovl-tag">' + esc(rule.currencies.join(' · ')) + ' only</span>' : '');
    var effect;
    if (problems.length) {
      effect = '<em>' + esc(problems[0]) + (problems.length > 1 ? ' (+' + (problems.length - 1) + ' more)' : '') + '</em>'
        + (chk.worst[i] ? ' <button type="button" class="cat-link ovl-show" data-ovl="showcase" data-i="' + i + '">Show it on ' + esc(chk.worst[i].portfolio) + '</button>' : '');
    } else if (!step || !sample) {
      effect = '';
    } else if (step.status === 'applied') {
      effect = 'On ' + esc(sample.label) + ': <b>' + ovlPc(step.amount) + '</b>'
        + (i ? ', from what the rules above left' : ', from the strategic allocation');
    } else if (step.status === 'off') {
      effect = 'Switched off in the panel on the right: not applied to ' + esc(sample.label);
    } else if (step.status === 'currency') {
      effect = 'Not held in ' + esc(sample.currency) + ', so not applied to ' + esc(sample.label);
    } else {
      effect = esc(sample.label) + ' does not hold ' + esc(rule.sources.map(function (s) { return s.category; }).join(' and ')) + ' at this step: not applied';
    }
    var label = esc(rule.name || 'rule ' + (i + 1));
    h += '<li class="ovl-card' + (problems.length ? ' bad' : '') + '" data-ovlcard="' + i + '"'
      + (readOnly ? '' : ' draggable="true"') + ' style="--cc:' + ovlColour(rule.into, i) + '">'
      + (readOnly ? '' : '<span class="ovl-grip" aria-hidden="true" title="Drag to change the order">⠿</span>')
      + '<span class="ovl-ord">' + ovlNth(i + 1) + '</span>'
      + '<div class="ovl-cb"><b>' + esc(rule.name || '(unnamed rule)') + ' <span class="ovl-into">→ ' + esc(rule.into || '?') + '</span></b>'
      + '<span class="ovl-w">' + esc(ovlWords(rule)) + '</span>'
      + '<span class="ovl-tags">' + tags + '</span>'
      + '<span class="ovl-fx">' + effect + '</span></div>'
      + (readOnly ? '' : '<span class="ovl-mv"><button type="button" class="ovl-mb" data-ovl="up" data-i="' + i + '"' + (i ? '' : ' disabled') + ' aria-label="Move ' + label + ' up, to ' + ovlNth(i) + '">↑</button>'
        + '<button type="button" class="ovl-mb" data-ovl="down" data-i="' + i + '"' + (i < list.length - 1 ? '' : ' disabled') + ' aria-label="Move ' + label + ' down, to ' + ovlNth(i + 2) + '">↓</button></span>'
        + '<button type="button" class="btn ovl-edit" data-ovl="edit" data-i="' + i + '" aria-label="Edit ' + label + '">Edit</button>'
        + '<button type="button" class="ovl-x" data-ovl="remove" data-i="' + i + '" aria-label="Remove ' + label + '">×</button>')
      + '</li>';
  });
  if (!list.length) h += '<li class="ovl-empty">No overlay rules: every portfolio of ' + (ovl.scope === '*' ? 'these types' : esc(ovl.scope)) + ' is implemented as its strategic allocation stands.</li>';
  return h + '</ol>';
}
function ovlTraceHtml(list, chk, res) {
  var sample = ovlSample(); if (!sample) return '';
  var inScope = ovlPortfolios(), sel = ovlTraceSel(), numeric = ovlPayload(list);
  var start = ovlCategoriesOf(sample);
  var used = list.filter(function (r) { return r.toggle; }).map(function (r) { return r.toggle; });
  var h = '<div class="ovl-sample"><label for="ovlSample">Resolved on</label><select id="ovlSample">'
    + inScope.map(function (p) {
        return '<option value="' + esc(p.keyStr) + '"' + (p.keyStr === sample.keyStr ? ' selected' : '') + '>' + esc(p.label) + '</option>';
      }).join('') + '</select></div>'
    + '<div class="ovl-switches" role="group" aria-label="The proposal’s switches">'
    + ((ovl.data && ovl.data.toggles) || []).filter(function (t) { return used.indexOf(t) >= 0; }).map(function (t) {
        return '<label><input type="checkbox" data-ovltrace="' + t + '"' + (sel[t] ? ' checked' : '') + '> ' + esc(OVL_SWITCH_NAMES[t]) + '</label>';
      }).join('')
    + '<span class="ovl-note">' + esc(sample.currency) + ' base currency</span></div>'
    + '<ol class="ovl-trace" id="ovlTrace"><li class="t0"><b>Start</b> · the strategic allocation: '
    + start.map(function (c) { return esc(c.name) + ' ' + ovlPc(c.weightPct); }).join(' · ') + '</li>';
  res.steps.forEach(function (step, i) {
    var rule = list[i], bad = chk.per[i].length;
    h += '<li class="' + (bad ? 'tbad' : '') + (step.status !== 'applied' ? ' tskip' : '') + '" style="--cc:' + ovlColour(rule.into, i) + '"><b>' + ovlNth(i + 1) + ' · ' + esc(rule.name || '(unnamed rule)') + '</b> <span class="tw">' + esc(ovlWords(rule)) + '</span>';
    if (step.status !== 'applied') {
      var note = step.status === 'off' ? 'Switched off.' : step.status === 'currency' ? 'Not held in ' + esc(sample.currency) + '.' : '';
      var shortfall = [];
      if (step.status === 'unfundable') {
        var at = App.resolveOverlays(start, numeric.slice(0, i), sel, sample.currency).categories;
        shortfall = App.overlayShortfall(numeric[i], at, start);
        var byName = {}; at.forEach(function (c) { byName[c.name] = c.weightPct; });
        note = shortfall.length ? 'Cannot be funded here: it would leave'
          : 'Not applied: this portfolio holds none of ' + esc(rule.sources.map(function (s) { return s.category; }).join(' and ')) + ' at this step.';
        if (shortfall.length) {
          h += '<div class="tf neg">' + note + '</div><table class="ovl-tt"><tbody>' + shortfall.map(function (s) {
            return '<tr class="neg"><td>' + ovlCatSw(s.category) + esc(s.category) + '</td><td>' + ovlPc(byName[s.category] || 0) + '</td><td>→</td><td>' + ovlPc(s.after) + '</td><td></td></tr>';
          }).join('') + '</tbody></table></li>';
          return;
        }
      }
      h += '<div class="tf">' + note + '</div></li>';
      return;
    }
    h += '<table class="ovl-tt"><tbody>';
    step.takes.forEach(function (t) {
      h += '<tr><td>' + ovlCatSw(t.category) + esc(t.category) + '</td><td>' + ovlPc(t.before) + '</td><td>→</td><td>' + ovlPc(t.after) + '</td><td class="d">−' + t.take.toFixed(2) + '</td></tr>';
    });
    h += '<tr class="ovr"><td><i class="ovl-sw" style="background:' + ovlColour(rule.into, i) + '"></i>' + esc(rule.into) + '</td><td></td><td></td><td>' + ovlPc(step.amount) + '</td><td class="d">+' + step.amount.toFixed(2) + '</td></tr>';
    h += '</tbody></table></li>';
  });
  h += '</ol>';
  var total = 0;
  h += '<table class="ovl-fin"><caption>Implemented allocation</caption><thead><tr><th scope="col">Category</th><th scope="col">Strategic</th><th scope="col">Implemented</th></tr></thead><tbody>';
  res.categories.forEach(function (c) {
    var index = list.map(function (r) { return (r.into || '').trim(); }).indexOf(c.name);
    var strategicW = sample.weights[c.name];
    total += c.weightPct;
    h += '<tr class="' + (index >= 0 ? 'ov' : '') + (Math.abs((strategicW || 0) - c.weightPct) > 1e-9 ? ' moved' : '') + '"><td>'
      + (index >= 0 ? '<i class="ovl-sw" style="background:' + ovlColour(c.name, index) + '"></i><span class="ovl-ord sm">' + ovlNth(index + 1) + '</span>' : ovlCatSw(c.name))
      + esc(c.name) + '</td><td>' + (strategicW != null ? ovlPc(strategicW) : '–') + '</td><td>' + ovlPc(c.weightPct) + '</td></tr>';
  });
  h += '</tbody><tfoot><tr><td>Total</td><td>100.00%</td><td>' + ovlPc(total) + '</td></tr></tfoot></table>';
  return h;
}
function ovlHistoryHtml(now) {
  var hist = ovl.history && ovl.history.scope === ovl.scope ? ovl.history.entries : null;
  if (hist == null) return '<p class="repo-loading">' + (ovl.historyBusy ? 'Loading…' : '') + '</p>';
  if (!hist.length) return '<p class="fund-none">No revisions yet: ' + esc(ovl.scope) + ' has always used the house list.</p>';
  return '<ol class="fund-hist">' + hist.map(function (h, i) {
    var restorable = i > 0 && h.rules;
    return '<li><div class="fund-hist-h"><b>r' + h.revision + '</b> <span class="act">' + esc(h.action) + '</span>'
      + (i === 0 && !now.inherited ? ' <span class="now">in force</span>' : '')
      + (restorable ? '<button type="button" class="cat-link" data-ovl="restore" data-rev="' + h.revision + '"'
          + (ovl.saving || ovl.draft ? ' disabled' : '') + ' aria-label="Restore revision ' + h.revision + '">Restore r' + h.revision + '</button>' : '')
      + '</div><div class="fund-hist-w">' + esc(h.change || '') + '</div>'
      + (h.rules ? '<div class="ovl-hist-o">' + h.rules.map(function (r, j) { return esc(ovlNth(j + 1) + ' ' + r.name); }).join(' · ') + '</div>' : '')
      + (h.note ? '<div class="fund-hist-n">' + esc(h.note) + '</div>' : '')
      + '<div class="fund-hist-by">' + esc(h.actor || '') + ' · ' + esc(shortDateTime(h.at)) + '</div></li>';
  }).join('') + '</ol>';
}
function ovlDrawerHtml(list, chk) {
  var i = ovl.edit, rule = list[i]; if (!rule) return '';
  var d = ovl.data;
  /* only what is above it: a rule placed after a rule below it would land
     at the end of the table */
  var above = list.slice(0, i).map(function (r) { return (r.into || '').trim(); }).filter(Boolean);
  var place = rule.place || (rule.sources[0] && rule.sources[0].category) || 'end';
  var placeOptions = [['end', 'At the end of the table']].concat(d.categories.map(function (c) { return [c, 'After ' + c]; }))
    .concat(above.map(function (c) { return [c, 'After ' + c + ' (a rule above)']; }));
  if (!placeOptions.some(function (o) { return o[0] === place; })) placeOptions.push([place, 'After ' + place + ' (not above this rule)']);
  var sizeBad = !!ovlNumberProblem(rule.size, 'the size');
  var h = '<div class="ovl-scrim" data-ovl="cancel"></div>'
    + '<aside class="ovl-drawer" id="ovlDrawer" role="dialog" aria-modal="true" aria-labelledby="ovlDrawerTitle">'
    + '<h4 id="ovlDrawerTitle">' + esc(rule.name || 'New rule') + '</h4>'
    + '<p class="ovl-lede">Resolves <b>' + ovlNth(i + 1) + '</b> · ' + (i ? 'reads the allocation as ' + list.slice(0, i).map(function (x) { return esc(x.name); }).join(', then ') + ' left it' : 'reads the strategic allocation as it stands') + '.</p>'
    + '<div class="ovl-row"><label for="ovlName">Name</label><input id="ovlName" type="text" autocomplete="off" maxlength="' + d.nameMax + '" data-ovlf="name" value="' + esc(rule.name) + '"></div>'
    + '<div class="ovl-row"><label for="ovlInto">Goes into</label><input id="ovlInto" type="text" autocomplete="off" maxlength="' + d.nameMax + '" data-ovlf="into" value="' + esc(rule.into) + '" placeholder="A category of its own"></div>'
    + '<div class="ovl-row"><label for="ovlRowName">Row</label><input id="ovlRowName" type="text" autocomplete="off" maxlength="' + d.nameMax + '" data-ovlf="row" value="' + esc(rule.row || '') + '" placeholder="' + esc(rule.into || 'The asset-class row') + '"></div>'
    + '<div class="ovl-row"><label for="ovlPlace">Sits</label><select id="ovlPlace" data-ovlf="place">'
    + placeOptions.map(function (o) { return '<option value="' + esc(o[0]) + '"' + (o[0] === place ? ' selected' : '') + '>' + esc(o[1]) + '</option>'; }).join('') + '</select></div>'
    + '<div class="ovl-row"><label for="ovlSize">Size</label><span class="fund-w"><input id="ovlSize" inputmode="decimal" autocomplete="off" data-ovlf="size" value="' + esc(rule.size) + '"'
    + (sizeBad ? ' aria-invalid="true"' : '') + '><span>%</span></span>'
    + '<select id="ovlBasis" data-ovlf="basis" aria-label="Of what">'
    + '<option value="portfolio"' + (rule.basis === 'portfolio' ? ' selected' : '') + '>of the portfolio, split by weight</option>'
    + '<option value="sources"' + (rule.basis === 'sources' ? ' selected' : '') + '>of each source as it stands</option></select></div>'
    + '<table class="fund-ed ovl-src"><colgroup><col><col class="w"><col class="x"></colgroup>'
    + '<thead><tr><th scope="col">Funded from</th><th scope="col" class="num">Weight</th><th><span class="sr-only">Remove</span></th></tr></thead><tbody>';
  rule.sources.forEach(function (s, j) {
    var wBad = !!ovlNumberProblem(s.weightPct, 'the weight');
    h += '<tr><td><select id="ovlSrc' + j + '" data-ovlf="src" data-j="' + j + '" aria-label="Source ' + (j + 1) + '">'
      + d.categories.map(function (c) { return '<option value="' + esc(c) + '"' + (c === s.category ? ' selected' : '') + '>' + esc(c) + '</option>'; }).join('')
      + '</select></td><td class="num"><span class="fund-w"><input id="ovlW' + j + '" inputmode="decimal" autocomplete="off" data-ovlf="w" data-j="' + j + '" value="' + esc(s.weightPct) + '"'
      + (wBad ? ' aria-invalid="true"' : '') + ' aria-label="Weight on ' + esc(s.category) + ', percent"><span>%</span></span></td>'
      + '<td>' + (rule.sources.length > 1 ? '<button type="button" class="ovl-x" data-ovl="rmsrc" data-j="' + j + '" aria-label="Remove ' + esc(s.category) + '">×</button>' : '') + '</td></tr>';
  });
  h += '</tbody><tfoot><tr><th scope="row">Total</th><td class="num" id="ovlSum">' + ovlSumText(rule) + '</td><td></td></tr></tfoot></table>'
    + (rule.sources.length < d.maxSources ? '<button type="button" class="btn ovl-addsrc" data-ovl="addsrc">+ Add a source</button>' : '')
    + '<fieldset class="ovl-ccy"><legend>Held in</legend>'
    + d.currencies.map(function (c) {
        return '<label><input type="checkbox" data-ovlf="ccy" value="' + esc(c) + '"' + (rule.currencies.indexOf(c) >= 0 ? ' checked' : '') + '> ' + esc(c) + '</label>';
      }).join('') + '<span class="ovl-hint" id="ovlCcyHint">' + (rule.currencies.length ? '' : 'None ticked: any base currency.') + '</span></fieldset>'
    + '<p class="ovl-hint">' + (rule.toggle ? 'Applies when ' + OVL_TOGGLE_WORDS[rule.toggle] + ' is on.' : 'Applies to every portfolio of the type that can fund it.') + '</p>'
    + '<div id="ovlDrawerCheck" aria-live="polite">' + ovlDrawerCheckHtml(list, chk) + '</div>'
    + '<div class="ovl-dfoot"><button type="button" class="btn btn-primary" data-ovl="done">Done</button>'
    + '<button type="button" class="btn" data-ovl="cancel">Cancel</button>'
    + '<span class="ovl-hint">Done keeps the change in the list; Save records it.</span></div></aside>';
  return h;
}
function ovlSumText(rule) {
  var scale = Math.pow(10, OVL_PLACES), units = 0;
  rule.sources.forEach(function (s) { var w = ovlNum(s.weightPct); if (w > 0) units += Math.round(w * scale); });
  return (units / scale).toFixed(OVL_PLACES) + '%';
}
function ovlDrawerCheckHtml(list, chk) {
  var i = ovl.edit; if (list[i] == null) return '';
  return chk.per[i].length
    ? '<ul class="fund-msgs">' + chk.per[i].map(function (m) { return '<li>' + esc(m) + '</li>'; }).join('') + '</ul>'
    : '<p class="ovl-ok">Every portfolio it applies to can fund it at this step, whatever the switches.</p>';
}
function ovlActionsHtml(list, readOnly, now) {
  var editing = !!ovl.draft, max = ovl.data.maxRules;
  return '<div class="fund-acts" id="ovlActs">'
    + (readOnly ? '' : list.length < max ? '<button type="button" class="btn" id="ovlAdd" data-ovl="add">+ Add rule</button>'
        : '<button type="button" class="btn" disabled>' + max + ' rules at most</button>')
    + (ovl.scope !== '*' && now.inherited && !editing ? '<button type="button" class="btn" data-ovl="own">Give ' + esc(ovl.scope) + ' its own list</button>' : '')
    + (ovl.scope !== '*' && !now.inherited && !editing ? '<button type="button" class="btn btn-danger" data-ovl="drop">Put ' + esc(ovl.scope) + ' back on the house list</button>' : '')
    + (ovl.undo ? '<span class="ovl-undo" role="status">Removed ' + esc(ovl.undo.rule.name || 'a rule') + ' <button type="button" class="cat-link" id="ovlUndo" data-ovl="undo">Undo</button></span>' : '')
    + '</div>';
}

function overlaysViewHtml() {
  if (!ovl.data) {
    return '<div class="ucap-b"><p class="repo-loading">' + (ovl.error ? esc(ovl.error) : 'Loading the overlay rules…') + '</p></div>';
  }
  var now = ovlInForce(), list = ovlList(), editing = !!ovl.draft;
  var readOnly = now.inherited && !editing;
  var chk = ovlChecks(list), res = ovlResolve(list), warn = ovlWarnings(list);
  var modal = ovl.edit != null ? ' inert' : '';
  return ovlLeavingHtml() + '<div class="ucap-b ovl-b">'
    + '<section class="ucap-main" aria-labelledby="ovlTitle"' + modal + '>'
    + '<h3 id="ovlTitle">Overlay funding</h3>'
    + '<p class="ucap-lede">Overlays move weight out of strategic categories into a category of their own as a portfolio is implemented. '
    + 'The rules resolve <b>top to bottom</b>: each reads the allocation as the rules above it left it, so the order matters. '
    + 'Drag a card by its handle, or use its arrows, to change the order. The Implementation screen, the workbook and the deck all follow these rules; '
    + 'a change applies to scenarios in progress at once, and the register records which rules each delivered proposal was built with.</p>'
    + ovlScopeHtml(now)
    + ovlCardsHtml(list, chk, res, readOnly)
    + ovlActionsHtml(list, readOnly, now)
    + '<ul class="ovl-warn" id="ovlWarn">' + warn.map(function (m) { return '<li>' + esc(m) + '</li>'; }).join('') + '</ul>'
    + '</section>'
    + '<aside class="ucap-side ovl-side" aria-label="The rules resolved, and their history"' + modal + '>'
    + '<h4>Resolved, step by step</h4><div id="ovlTraceBox">' + (res ? ovlTraceHtml(list, chk, res) : '') + '</div>'
    + '<h4 class="ovl-hh">History · ' + (ovl.scope === '*' ? 'house list' : esc(ovl.scope)) + '</h4>' + ovlHistoryHtml(now)
    + '</aside>'
    + (ovl.edit != null ? ovlDrawerHtml(list, chk) : '')
    + '</div>';
}

function ovlStatusText(dirty, chk, now, list) {
  if (ovl.error) return '';
  if (!dirty) return ovl.flash || '';
  var change = ovlChange(now.entry.rules, list);
  var what = now.inherited ? 'Own list for ' + ovl.scope + (change ? ': ' + change : '') : change;
  return (chk.bad ? 'Fix the red rules first · ' : ovl.edit != null ? 'Close the rule first · ' : '')
    + 'Unsaved' + (what ? ': ' + what : '');
}
function overlaysFooterHtml() {
  var list = ovlList(), dirty = ovlDirty(), chk = ovlChecks(list), now = ovlInForce();
  var ok = dirty && !chk.bad && ovl.note.trim() && !ovl.saving && ovl.edit == null && !ovl.stale;
  var status = now ? ovlStatusText(dirty, chk, now, list) : '';
  return '<div class="repo-f ovl-f"' + (ovl.edit != null ? ' inert' : '') + '>'
    + '<label class="fund-note" for="ovlNote">Note</label>'
    + '<input id="ovlNote" class="fund-note-in" type="text" autocomplete="off" value="' + esc(ovl.note) + '"'
    + ' placeholder="Why the rules are changing (required)">'
    + '<span class="spacer"></span>'
    + '<span class="ovl-pend' + (chk.bad && dirty ? ' bad' : '') + '" id="ovlPending" role="status">' + esc(status) + '</span>'
    + (ovl.error ? '<span class="md-err" role="alert">' + esc(ovl.error) + '</span>' : '')
    + (ovl.stale ? '<button type="button" class="btn" data-ovl="reload">Reload the rules</button>' : '')
    + (ovl.draft ? '<button type="button" class="btn" data-ovl="discard">Discard</button>' : '')
    + '<button type="button" class="btn btn-primary" id="ovlSave" data-ovl="save"' + (ok ? '' : ' disabled') + '>'
    + (ovl.saving ? 'Saving…' : 'Save rules') + '</button></div>';
}

/* the header and footer sit outside the view; while the drawer is open they
   are inert too, so the keyboard stays in the drawer */
function ovlAfterRender() {
  var dialog = document.querySelector('#repoDialog .dialog.repo'); if (!dialog) return;
  var modal = repo.view === 'overlays' && ovl.edit != null;
  ['.repo-h', '.repo-f'].forEach(function (sel) {
    var el = dialog.querySelector(':scope > ' + sel);
    if (el) { if (modal) el.setAttribute('inert', ''); else el.removeAttribute('inert'); }
  });
}

/* the parts that follow typing in the drawer, redrawn without its boxes so
   the caret stays where it was */
function ovlUpdate() {
  var list = ovlList(), chk = ovlChecks(list), now = ovlInForce();
  var res = ovlResolve(list);
  var put = function (id, html, outer) { var el = document.getElementById(id); if (el) { if (outer) el.outerHTML = html; else el.innerHTML = html; } };
  put('ovlList', ovlCardsHtml(list, chk, res, now.inherited && !ovl.draft), true);
  put('ovlTraceBox', res ? ovlTraceHtml(list, chk, res) : '');
  put('ovlDrawerCheck', ovlDrawerCheckHtml(list, chk));
  put('ovlWarn', ovlWarnings(list).map(function (m) { return '<li>' + esc(m) + '</li>'; }).join(''));
  var rule = list[ovl.edit];
  if (rule) {
    put('ovlSum', ovlSumText(rule));
    put('ovlCcyHint', rule.currencies.length ? '' : 'None ticked: any base currency.');
    var title = document.getElementById('ovlDrawerTitle'); if (title) title.textContent = rule.name || 'New rule';
    var size = document.getElementById('ovlSize');
    if (size) { if (ovlNumberProblem(rule.size, 'the size')) size.setAttribute('aria-invalid', 'true'); else size.removeAttribute('aria-invalid'); }
    rule.sources.forEach(function (s, j) {
      var w = document.getElementById('ovlW' + j);
      if (w) { if (ovlNumberProblem(s.weightPct, 'the weight')) w.setAttribute('aria-invalid', 'true'); else w.removeAttribute('aria-invalid'); }
    });
  }
  var foot = document.querySelector('.dialog.repo > .repo-f');
  if (foot) {
    var focused = document.activeElement && document.activeElement.id;
    foot.outerHTML = overlaysFooterHtml();
    ovlAfterRender();
    if (focused === 'ovlNote') { var n = document.getElementById('ovlNote'); if (n) { n.focus(); n.setSelectionRange(n.value.length, n.value.length); } }
  }
}

async function ovlPost(method, path, body) {
  ovl.saving = true; ovl.error = null; ovl.stale = false; ovl.flash = ''; render();
  try {
    var r = await api(method, path, body);
    if (!r) return false;
    if (!r.ok) {
      ovl.error = r.body.error || ('Could not save (' + r.status + ')');
      ovl.stale = r.status === 409;
      return false;
    }
    ovl.data = r.body.overlays || ovl.data;
    ovlDropDraft();
    ovl.flash = 'Saved' + (r.body.warnings && r.body.warnings.length ? ' · ' + r.body.warnings.length + ' warning' + (r.body.warnings.length > 1 ? 's' : '') : '');
    /* the implementation screen reads the rules from the schema */
    if (App.refetchSchema) {
      try {
        var again = App.refetchSchema();
        if (again && again.catch) again.catch(function () { /* the page refetches on its own next */ });
      } catch (e) { /* likewise */ }
    }
    return true;
  } catch (err) { ovl.error = err.message; return false; }
  finally {
    ovl.saving = false; render(); loadOverlayHistory();
  }
}
function ovlBaseNow() {
  if (ovl.base) return ovl.base;
  var now = ovlInForce();
  return now ? { scope: now.entry.scope, revision: now.entry.revision } : null;
}

/* a new rule that every portfolio in scope can fund: from the category the
   portfolios that hold it hold the most of at worst, at the largest of a
   few small sizes the checks pass */
function ovlNewRule(list) {
  var d = ovl.data, n = list.length + 1, names = {}, intos = {};
  list.forEach(function (r) { names[(r.name || '').toLowerCase()] = 1; intos[(r.into || '').toLowerCase()] = 1; });
  while (names[('New overlay ' + n).toLowerCase()] || intos[('New Overlay ' + n).toLowerCase()]) n += 1;
  var best = d.categories[0], bestMin = -1;
  d.categories.forEach(function (c) {
    var min = Infinity;
    ovlPortfolios().forEach(function (p) { var w = p.weights[c] || 0; if (w > 0 && w < min) min = w; });
    if (min !== Infinity && min > bestMin) { bestMin = min; best = c; }
  });
  var rule = { id: '', name: 'New overlay ' + n, into: 'New Overlay ' + n, row: '', place: '', size: '1', basis: 'portfolio',
               sources: [{ category: best, weightPct: '100' }], toggle: null, currencies: [] };
  var sizes = ['1', '0.5', '0.25', '0.1'];
  for (var i = 0; i < sizes.length; i++) {
    rule.size = sizes[i];
    if (!ovlChecks(list.concat([rule])).per[list.length].length) break;
  }
  return rule;
}
function ovlMove(from, to) {
  ovlStartDraft();
  var len = ovl.draft.length;
  if (from == null || isNaN(from) || from < 0 || from >= len || to < 0 || to >= len || from === to) return false;
  var rule = ovl.draft.splice(from, 1)[0];
  ovl.draft.splice(to, 0, rule);
  ovl.undo = null; ovl.flash = '';
  return true;
}
function ovlFocus(selector) {
  var all = document.querySelectorAll(selector);
  for (var k = 0; k < all.length; k++) { if (!all[k].disabled) { all[k].focus(); return true; } }
  return false;
}
function ovlFocusRule(i) {
  return ovlFocus('[data-ovl="edit"][data-i="' + i + '"]') || ovlFocus('#ovlAdd');
}
function ovlCloseDrawer(keep) {
  var i = ovl.edit, fresh = ovl.editFresh;
  if (!keep && ovl.editSnap) ovl.draft = ovl.editSnap;
  ovl.edit = null; ovl.editSnap = null; ovl.editFresh = false;
  if (!ovlDirty() && !(ovlInForce() || {}).inherited) ovl.draft = null;   /* nothing changed */
  render();
  if (!keep && fresh) ovlFocus('#ovlAdd'); else ovlFocusRule(i);
}
function ovlTrySwitch(scope) {
  if (ovlDirty()) { ovl.leaving = { scope: scope }; render(); ovlFocus('[data-ovl="leavekeep"]'); return; }
  ovlDropDraft();
  ovl.scope = scope; ovl.history = null; ovl.sample = null; ovl.traceSel = null; ovl.flash = '';
  render(); loadOverlayHistory();
}

function ovlClick(el) {
  var act = el.dataset.ovl; if (act === undefined) return false;
  var i = el.dataset.i != null ? parseInt(el.dataset.i, 10) : null;
  var j = el.dataset.j != null ? parseInt(el.dataset.j, 10) : null;
  if (act === 'up' || act === 'down') {
    var to = act === 'up' ? i - 1 : i + 1;
    if (ovlMove(i, to)) {
      render();
      ovlFocus('[data-ovl="' + act + '"][data-i="' + to + '"]')
        || ovlFocus('[data-ovl="' + (act === 'up' ? 'down' : 'up') + '"][data-i="' + to + '"]');
      App.announce('polite', (ovl.draft[to].name || 'The rule') + ' is now ' + ovlNth(to + 1) + '.');
    }
    return true;
  }
  if (act === 'add') {
    ovlStartDraft();
    ovl.editSnap = ovlClone(ovl.draft);
    ovl.draft.push(ovlNewRule(ovl.draft));
    ovl.edit = ovl.draft.length - 1; ovl.editFresh = true; ovl.undo = null;
    render(); var f = document.getElementById('ovlName'); if (f) { f.focus(); f.select(); }
    return true;
  }
  if (act === 'edit') {
    ovlStartDraft();
    if (i == null || i < 0 || i >= ovl.draft.length) return true;
    ovl.editSnap = ovlClone(ovl.draft); ovl.edit = i; ovl.editFresh = false; ovl.undo = null;
    render(); var g = document.getElementById('ovlName'); if (g) g.focus();
    return true;
  }
  if (act === 'remove') {
    ovlStartDraft();
    if (i == null || i < 0 || i >= ovl.draft.length) return true;
    var gone = ovl.draft.splice(i, 1)[0];
    ovl.undo = { rule: gone, index: i }; ovl.flash = '';
    render();
    App.announce('polite', 'Removed ' + (gone.name || 'a rule') + '. Undo is beside Add rule.');
    ovlFocus('#ovlUndo');
    return true;
  }
  if (act === 'undo') {
    if (ovl.undo && ovl.draft) {
      ovl.draft.splice(Math.min(ovl.undo.index, ovl.draft.length), 0, ovl.undo.rule);
      var back = ovl.undo.index; ovl.undo = null;
      if (!ovlDirty() && !(ovlInForce() || {}).inherited) ovl.draft = null;
      render(); ovlFocusRule(back);
    }
    return true;
  }
  if (act === 'done') { ovlCloseDrawer(true); return true; }
  if (act === 'cancel') { ovlCloseDrawer(false); return true; }
  if (act === 'addsrc') {
    var rule = ovl.draft[ovl.edit];
    var free = ovl.data.categories.filter(function (c) { return !rule.sources.some(function (s) { return s.category === c; }); })[0];
    if (free) rule.sources.push({ category: free, weightPct: '' });
    render(); var w = document.getElementById('ovlW' + (rule.sources.length - 1)); if (w) w.focus();
    return true;
  }
  if (act === 'rmsrc') {
    ovl.draft[ovl.edit].sources.splice(j, 1); render();
    ovlFocus('#ovlW' + Math.max(0, j - 1));
    return true;
  }
  if (act === 'showcase') {
    var list = ovlList(), worst = ovlChecks(list).worst[i];
    if (worst) { ovl.sample = worst.keyStr; ovl.traceSel = worst.selection; render(); ovlFocus('#ovlSample'); }
    return true;
  }
  if (act === 'own') { ovlStartDraft(); render(); return true; }
  if (act === 'discard') { ovlDropDraft(); ovl.flash = ''; render(); return true; }
  if (act === 'reload') { ovlDropDraft(); ovl.flash = ''; loadOverlays(); return true; }
  if (act === 'leavekeep') { ovl.leaving = null; render(); ovlFocus('#ovlNote'); return true; }
  if (act === 'leavego') {
    var leaving = ovl.leaving;
    ovlDropDraft();
    if (leaving && leaving.close) { closeRepository(true); return true; }
    ovlTrySwitch(leaving ? leaving.scope : ovl.scope);
    return true;
  }
  if (act === 'save') {
    ovlPost('PUT', '/scenario/repository/overlays', { scope: ovl.scope, note: ovl.note, rules: ovlPayload(ovl.draft), base: ovlBaseNow() });
    return true;
  }
  if (act === 'drop') {
    if (!ovl.note.trim()) { ovl.error = 'Write a note first: putting a type back on the house list is a change like any other.'; ovl.flash = ''; render(); return true; }
    ovlPost('POST', '/scenario/repository/overlays/remove', { scope: ovl.scope, note: ovl.note, base: ovlBaseNow() });
    return true;
  }
  if (act === 'restore') {
    if (ovl.saving || ovl.draft) return true;
    if (!ovl.note.trim()) { ovl.error = 'Write a note first: restoring r' + el.dataset.rev + ' is a new revision, and it says why.'; ovl.flash = ''; render(); ovlFocus('#ovlNote'); return true; }
    ovlPost('POST', '/scenario/repository/overlays/revert', { scope: ovl.scope, revision: parseInt(el.dataset.rev, 10), note: ovl.note, base: ovlBaseNow() });
    return true;
  }
  return false;
}

document.addEventListener('change', function (e) {
  if (!repo.open || repo.view !== 'overlays') return;
  var el = e.target;
  if (el.id === 'ovlScope') {
    var want = el.value;
    el.value = ovl.scope;                         /* until the move is allowed */
    ovlTrySwitch(want);
    return;
  }
  if (el.id === 'ovlSample') { ovl.sample = el.value; ovlUpdate(); return; }
  if (el.dataset && el.dataset.ovltrace) {
    var sel = ovlClone(ovlTraceSel()); sel[el.dataset.ovltrace] = el.checked; ovl.traceSel = sel; ovlUpdate(); return;
  }
  var f = el.dataset && el.dataset.ovlf;
  if (!f || ovl.edit == null || !ovl.draft) return;
  var rule = ovl.draft[ovl.edit];
  if (f === 'basis') { rule.basis = el.value; ovlUpdate(); return; }
  if (f === 'place') { rule.place = el.value; ovlUpdate(); return; }
  if (f === 'src') { rule.sources[parseInt(el.dataset.j, 10)].category = el.value; render(); ovlFocus('#ovlSrc' + el.dataset.j); return; }
  if (f === 'ccy') {
    var c = el.value, at = rule.currencies.indexOf(c);
    if (el.checked && at < 0) rule.currencies.push(c);
    if (!el.checked && at >= 0) rule.currencies.splice(at, 1);
    ovlUpdate(); return;
  }
});

document.addEventListener('input', function (e) {
  if (!repo.open || repo.view !== 'overlays') return;
  var el = e.target;
  if (el.id === 'ovlNote') { ovl.note = el.value; ovl.error = null; ovl.stale = false; ovl.flash = ''; ovlUpdate(); return; }
  var f = el.dataset && el.dataset.ovlf;
  if (!f || ovl.edit == null || !ovl.draft) return;
  var rule = ovl.draft[ovl.edit];
  if (f === 'name') rule.name = el.value;
  else if (f === 'into') {
    var was = (rule.into || '').trim();
    rule.into = el.value;
    /* a rule placed after this one's category follows the rename */
    ovl.draft.forEach(function (r) { if (r !== rule && was && r.place === was) r.place = el.value.trim(); });
  }
  else if (f === 'row') rule.row = el.value;
  else if (f === 'size') rule.size = el.value.trim();
  else if (f === 'w') rule.sources[parseInt(el.dataset.j, 10)].weightPct = el.value.trim();
  else return;
  ovlUpdate();
});

/* drag a card by its handle: the card under the pointer shows where it would
   land, above or below, and the drop moves it there */
function ovlDropMark(card, after) {
  document.querySelectorAll('.ovl-card.drop-above, .ovl-card.drop-below').forEach(function (c) {
    c.classList.remove('drop-above'); c.classList.remove('drop-below');
  });
  if (card) card.classList.add(after ? 'drop-below' : 'drop-above');
}
document.addEventListener('dragstart', function (e) {
  if (!repo.open || repo.view !== 'overlays' || ovl.edit != null) return;
  var card = e.target.closest && e.target.closest('.ovl-card[draggable="true"]');
  if (!card) return;
  ovl.drag = parseInt(card.dataset.ovlcard, 10);
  card.classList.add('dragging');
  if (e.dataTransfer) { e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', String(ovl.drag)); } catch (err) { /* old engines */ } }
});
document.addEventListener('dragover', function (e) {
  if (ovl.drag == null || !repo.open || repo.view !== 'overlays') return;
  var card = e.target.closest && e.target.closest('.ovl-card');
  if (!card) return;
  e.preventDefault();
  var box = card.getBoundingClientRect();
  ovlDropMark(card, e.clientY > box.top + box.height / 2);
});
document.addEventListener('drop', function (e) {
  if (ovl.drag == null || !repo.open || repo.view !== 'overlays') return;
  var card = e.target.closest && e.target.closest('.ovl-card');
  if (!card) return;
  e.preventDefault();
  var box = card.getBoundingClientRect();
  var over = parseInt(card.dataset.ovlcard, 10);
  var after = e.clientY > box.top + box.height / 2;
  var from = ovl.drag, to = over + (after ? 1 : 0);
  if (from < to) to -= 1;
  ovl.drag = null;
  if (ovlMove(from, to)) {
    render(); ovlFocusRule(to);
    App.announce('polite', (ovl.draft[to].name || 'The rule') + ' is now ' + ovlNth(to + 1) + '.');
  } else { ovlDropMark(null); }
});
document.addEventListener('dragend', function () {
  if (ovl.drag == null) return;
  ovl.drag = null; ovlDropMark(null);
  document.querySelectorAll('.ovl-card.dragging').forEach(function (c) { c.classList.remove('dragging'); });
});

/* ---- events ------------------------------------------------------------- */
document.addEventListener('click', function (e) {
  var t = e.target.closest ? e.target.closest('#repolink, #catlink') : null;
  if (t) { e.preventDefault(); openRepository(t, t.id.indexOf('cat') !== -1 ? 'catalogue' : 'sleeves'); return; }
  /* the landing strip (D106). Real anchors carrying the same hashes the
     console already answers to, so a middle click opens a second tab and a
     copied link works - the handler only saves the round trip. */
  var go = e.target.closest ? e.target.closest('[data-repoopen]') : null;
  if (go) {
    e.preventDefault();
    var view = go.dataset.repoopen;
    openRepository(go, view === 'repository' ? 'sleeves' : view);
    return;
  }
  var host = document.getElementById('repoDialog');
  if (!host || !repo.open || !host.contains(e.target)) return;
  var el = e.target.closest ? e.target.closest(
    '[data-repoclose],[data-reposcrim],[data-repoview],[data-repovariant],'
    + '[data-svmode],[data-svtile],[data-svcard],[data-svcrumb],[data-svedit],[data-svcancel],[data-svrow],[data-svclose],'
    + '[data-svsort],[data-svcompare],[data-svcmpclose],[data-svclearticks],[data-svcopy],[data-svopen],'
    + '[data-svkeptopen],[data-svkeptdrop],[data-svreload],[data-repomenukeep],[data-reporemoveyes],'
    + '[data-reponew],[data-repoadd],[data-reporm],[data-repopick],[data-repochoose],[data-reposave],'
    + '[data-ncstart],[data-nccat],[data-ncsrc],[data-nckeepmine],[data-ncreplace],[data-ncadd],[data-ncspread],[data-nccreate],[data-nccancel],'
    + '[data-ncdlgclose],[data-ncdlgscrim],[data-ncusename],[data-ncdscope],[data-ncdclear],[data-ncdsort],[data-ncdrow],[data-ncdadd],'
    + '[data-repodelete],[data-repocanceldelete],[data-repokeep],[data-repodiscard],'
    + '[data-repokeptrestore],[data-repokeptdrop],'
    + '[data-catcols],[data-catshowall],[data-catdensity],[data-catclearall],[data-catsort],[data-catcsv],'
    + '[data-catpin],[data-catunpin],[data-catclearpins],[data-catcompare],[data-catdetailclose],'
    + '[data-catrow],[data-catopen],[data-catgroup],[data-catfold],[data-catfoldall],'
    + '[data-repocopy],[data-reporemove],[data-repoedition],[data-reporuleadd],[data-reporulerm],'
    + '[data-repohistory],[data-reporev],[data-reporevert],'
    + '[data-arcsort],[data-arcrow],[data-arcclear],[data-arcclearsel],[data-arcrestore],[data-arcrestoresel],[data-arcdetailclose],'
    + '[data-acttoggle],[data-actclear],[data-actmore],[data-actview],[data-actrestore],[data-actrange],'
    + '[data-regrow],[data-regclear],[data-regmore],[data-regpic],[data-regdetailclose],'
    + '[data-regview],[data-regfilters],[data-reguid],'
    + '[data-fundadd],[data-fundrm],[data-fundsave],[data-funddiscard],[data-fundown],[data-funddrop],[data-fundrestore],'
    + '[data-ovl]') : null;
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
  if (ds.repoclose !== undefined || ds.reposcrim !== undefined) { if (!repo.saving) closeRepository(false); return; }
  if (ds.repoview !== undefined) { switchView(ds.repoview); return; }
  if (fundClick(ds)) return;
  if (ovlClick(el)) return;
  /* the Sleeves view (D156): every move that leaves the open sleeve goes
     through goTo, so an unsaved draft asks first in either view */
  if (ds.svmode !== undefined) { svSwitchMode(ds.svmode); return; }
  if (ds.repovariant !== undefined) {
    sv.focus = '[data-repovariant="' + ds.repovariant.replace(/"/g, '\\"') + '"]';
    goTo({ variant: ds.repovariant, sv: { level: sv.level === 2 ? 1 : sv.level } }); return;
  }
  if (ds.svtile !== undefined) { sv.focus = '#svCatTitle'; goTo({ category: ds.svtile, sv: { level: 1 } }); return; }
  if (ds.svcard !== undefined) { svOpen(parseInt(ds.svcard, 10)); return; }
  if (ds.svcrumb !== undefined) {
    var lvl = parseInt(ds.svcrumb, 10);
    sv.focus = lvl === 0 ? '.sv-tile' : '#svCatTitle';
    goTo({ sv: { level: lvl } }); return;
  }
  if (ds.svedit !== undefined) { svEdit(); return; }
  if (ds.svcancel !== undefined) { svCancel(); return; }
  if (ds.svrow !== undefined) {
    if (e.target.closest('input')) return;                 /* the compare box has its own handler */
    svOpen(parseInt(ds.svrow, 10)); return;
  }
  if (ds.svclose !== undefined) {
    if (repo.saving) return;                 /* a save in flight lands first */
    if (ncOpen()) ncCancel(); else svCloseDrawer();
    return;
  }
  if (ds.svsort !== undefined) {
    var key = ds.svsort;
    if (!key) { sv.sort = null; sv.focus = '[data-svsort="category"]'; render(); return; }   /* library order */
    if (sv.sort && sv.sort.key === key) sv.sort.dir = sv.sort.dir === 'asc' ? 'desc' : 'asc';
    else sv.sort = { key: key, dir: (key === 'products' || key === 'cost' || key === 'created') ? 'desc' : 'asc' };
    sv.focus = '[data-svsort="' + key + '"]'; render(); return;
  }
  if (ds.svcompare !== undefined) { if (sv.sel.length >= 2) { sv.compare = true; sv.focus = '#svCmpTitle'; render(); } return; }
  if (ds.svcmpclose !== undefined) { sv.compare = false; sv.focus = '[data-svcompare]||.sv-rowbtn'; render(); return; }
  if (ds.svclearticks !== undefined) { sv.sel = []; sv.compare = false; sv.focus = '.sv-rowbtn||[data-svfv]'; render(); return; }
  if (ds.svkeptopen !== undefined) { svOpenKept(); return; }
  if (ds.svkeptdrop !== undefined) {
    sv.keptOffer = null; forgetKeptDraft(); repo.kept = null;
    sv.focus = '[data-reponew]||[data-svmode][aria-pressed="true"]';
    App.announce('polite', 'The kept changes are discarded.'); render(); return;
  }
  if (ds.svreload !== undefined) {
    /* someone else's save stands: read the library again and open what is stored */
    var reId = repo.draft && repo.draft.id;
    repo.stale = null; repo.dirty = false; repo.error = null;
    if (repo.draft && keptDraftFor(repo.draft)) forgetKeptDraft();
    repo.pending = reId ? { variant: repo.variant, category: repo.category } : null;
    loadRepository().then(function () {
      if (reId && sleeveById(reId)) { sv.focus = sv.mode === 'table' ? '#svDrawerTitle' : '#svTitle'; loadDraft(reId); render(); }
      App.announce('polite', 'Reloaded. Your changes were not saved.');
    });
    return;
  }
  if (ds.repomenukeep !== undefined) { repo.menu = null; render(); return; }
  if (ds.reporemoveyes !== undefined) { removeFromCategory(parseInt(ds.reporemoveyes, 10)); return; }
  if (ds.svopen !== undefined) { sv.compare = false; svOpen(parseInt(ds.svopen, 10)); return; }
  if (ds.svcopy !== undefined) { copyToVariant(repo.draft && repo.draft.id, ds.svcopy); return; }
  /* One create path (B2), from wherever the admin is standing: the cards'
     type and category, or the table's filters, fill the form in (D156) */
  if (ds.reponew !== undefined) {
    /* the category in hand fills the form in; with none in hand the desk
       chooses it there, in plain sight (D157) */
    var here = svNewContext();
    var onForm = ncOpen() && (sv.mode === 'table' ? sv.drawer : sv.level === 2);
    var back = onForm && nc.back ? nc.back : {
      level: sv.level, category: sv.mode === 'table' ? sv.fc : (sv.level >= 1 ? repo.category : null),
      variant: here.variant,
      sleeveId: (sv.mode === 'table' ? sv.drawer : sv.level === 2) && repo.draft && repo.draft.id ? repo.draft.id : null };
    sv.focus = here.category ? '#repoName' : '[data-nccat]:not([disabled])';
    goTo({ fresh: true, create: true, variant: here.variant || repo.variant, category: here.category, noVariant: !here.variant,
           sv: sv.mode === 'table' ? { drawer: true, compare: false } : { level: 2 },
           then: function () { nc.back = back; } });
    return;
  }
  /* the New sleeve form (D157) */
  if (ds.ncstart !== undefined) { ncSetStart(ds.ncstart === 'existing' ? 'existing' : 'blank'); return; }
  if (ds.nccat !== undefined) { ncSetCategory(ds.nccat); return; }
  if (ds.ncsrc !== undefined) { ncPick(parseInt(ds.ncsrc, 10)); return; }
  if (ds.nckeepmine !== undefined) {
    var keptFor = nc.ask; nc.ask = null;
    sv.focus = '[data-ncsrc="' + keptFor + '"]||#ncSrcQ'; render(); return;
  }
  if (ds.ncreplace !== undefined) { if (nc.ask != null) ncUseSource(nc.ask); return; }
  if (ds.ncadd !== undefined) { ncOpenDialog(el.classList.contains('nc-addbtn') ? '[data-ncadd]' : '.nc-addrow [data-ncadd]'); return; }
  if (ds.ncspread !== undefined) {
    var spread = svSpread(repo.draft.products.length);
    repo.draft.products.forEach(function (r, i) { r.weightPct = spread[i]; r.weightText = null; });
    markDirty(); sv.focus = '[data-ncspread]'; render(); return;
  }
  if (ds.nccreate !== undefined) { ncCreate(); return; }
  if (ds.nccancel !== undefined) { ncCancel(); return; }
  if (ds.ncdlgclose !== undefined) { ncCloseDialog(); return; }
  if (ds.ncdlgscrim !== undefined) {
    if (nc.dlg && nc.dlg.sel.length) { nc.dlg.nudge = true; ncDlgRefresh(); var addB = document.querySelector('#repoDialog [data-ncdadd]'); if (addB) addB.focus(); return; }
    ncCloseDialog(); return;
  }
  if (ds.ncusename !== undefined) {
    repo.draft.name = ds.ncusename; markDirty(); repo.fieldError = null;
    sv.focus = '#repoName'; render(); App.announce('polite', 'Name set to ' + ds.ncusename + '.'); return;
  }
  if (ds.ncdscope !== undefined) { nc.dlg.scope = ds.ncdscope === 'all' ? 'all' : 'cat'; sv.focus = '[data-ncdscope="' + nc.dlg.scope + '"]||#ncDlgQ'; render(); return; }
  if (ds.ncdclear !== undefined) { nc.dlg.q = ''; nc.dlg.vehicle = ''; nc.dlg.style = ''; nc.dlg.source = ''; sv.focus = '#ncDlgQ'; render(); return; }
  if (ds.ncdsort !== undefined) {
    var g = nc.dlg, k = ds.ncdsort;
    if (g.sort === k) g.dir = g.dir === 'asc' ? 'desc' : 'asc'; else { g.sort = k; g.dir = 'asc'; }
    ncDlgRefresh(); var sb = document.querySelector('#repoDialog [data-ncdsort="' + k + '"]'); if (sb) sb.focus(); return;
  }
  if (ds.ncdrow !== undefined) {
    if (e.target.closest('input')) return;                 /* the box has its own handler */
    ncDlgTick(ds.ncdrow, nc.dlg.sel.indexOf(ds.ncdrow) === -1); return;
  }
  if (ds.ncdadd !== undefined) { ncDlgAdd(); return; }
  if (ds.repocopy !== undefined) { copyToVariant(repo.menu && repo.menu.id, ds.repocopy); return; }
  /* editions (D89) */
  if (ds.repoedition !== undefined) {
    var ofId = parseInt(ds.repoedition, 10); var of = sleeveById(ofId); repo.menu = null;
    if (of) goTo({ variant: of.variant, category: of.category, fresh: true, edition: { ofId: ofId, name: of.name } });
    return;
  }
  if (ds.reporuleadd !== undefined) {
    var blank = {}; RULE_FIELDS.forEach(function (f) { blank[f] = []; });
    repo.draft.rules.push(blank); markDirty(); repo.fieldError = null; render(); return;
  }
  if (ds.reporulerm !== undefined) {
    repo.draft.rules.splice(parseInt(ds.reporulerm, 10), 1); markDirty(); repo.fieldError = null;
    render(); schedulePreview(); return;
  }
  /* archiving from the menu asks first, as it does on the page (D156 QA 15) */
  if (ds.reporemove !== undefined) { if (repo.menu) { repo.menu.confirm = true; sv.focus = '[data-repomenukeep]'; render(); } return; }
  /* the record (D65) */
  if (ds.repohistory !== undefined) { toggleHistory(parseInt(ds.repohistory, 10)); return; }
  if (ds.reporev !== undefined) {
    var n = parseInt(ds.reporev, 10);
    repo.openRevision = repo.openRevision === n ? null : n;
    render();
    /* a revision opened at the foot of the page comes into view (D156) */
    var opened = document.querySelector('#repoDialog .rev.open');
    if (opened && opened.scrollIntoView) opened.scrollIntoView({ block: 'nearest' });
    return;
  }
  if (ds.reporevert !== undefined) {
    /* putting a version back replaces the sleeve in force: unsaved changes
       to it ask first, like any other move away from them (D156) */
    var revN = parseInt(ds.reporevert, 10);
    if (repo.view === 'sleeves' && repo.dirty && repo.draft && repo.draft.id) {
      var revE = sleeveById(repo.draft.id);
      goTo({ to: repo.draft.id, variant: revE ? revE.variant : repo.variant, category: revE ? revE.category : repo.category,
             sv: sv.mode === 'table' ? { drawer: true } : { level: 2 }, then: function () { revertTo(revN); } });
      return;
    }
    revertTo(revN); return;
  }
  /* the archive (D66) */
  if (ds.arcsort !== undefined) {
    if (arc.sort.key === ds.arcsort) arc.sort.dir = arc.sort.dir === 'asc' ? 'desc' : 'asc';
    else arc.sort = { key: ds.arcsort, dir: ds.arcsort === 'archivedAt' || ds.arcsort === 'createdAt' || ds.arcsort === 'revisions' ? 'desc' : 'asc' };
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
  if (ds.regdetailclose !== undefined) { reg.detail = null; syncHash(); render(); return; }
  if (ds.regview !== undefined) {
    if (reg.view !== ds.regview) { regApplyView(ds.regview); syncHash(); loadRegister(true); }
    return;
  }
  if (ds.regfilters !== undefined) { reg.openFilters = !reg.openFilters; render(); return; }
  if (ds.reguid !== undefined) { copyProposalUid(e.target, ds.reguid); return; }
  if (ds.regpic !== undefined) { reg.picture = ds.regpic === 'allocation' ? 'allocation' : 'implemented'; render(); return; }
  if (ds.regclear !== undefined) {
    reg.query = ''; regApplyView('recent'); syncHash(); loadRegister(true); return;
  }
  if (ds.regmore !== undefined) { loadRegister(false); return; }
  if (ds.repoadd !== undefined) { addRow(); return; }
  if (ds.reporm !== undefined) { removeRow(parseInt(ds.reporm, 10)); return; }
  if (ds.repopick !== undefined) { openPicker(parseInt(ds.repopick, 10)); return; }
  if (ds.repochoose !== undefined) { choose(ds.repochoose); return; }
  if (ds.reposave !== undefined) { saveDraft(); return; }
  if (ds.repodelete !== undefined) {
    if (!repo.confirmDelete) sv.focus = '[data-repocanceldelete]';
    deleteCurrent(); return;
  }
  if (ds.repocanceldelete !== undefined) { repo.confirmDelete = false; sv.focus = '[data-repodelete]'; render(); return; }
  if (ds.repokeptrestore !== undefined) {
    var held = repo.kept; repo.kept = null;
    if (held && held.draft) {
      repo.draft = ncRevive(held.draft); markDirty(); sv.editing = true;
      sv.focus = '#repoName||#repoLabel||#repoNote';
      App.announce('polite', 'Unsaved changes restored.');
    }
    render(); return;
  }
  if (ds.repokeptdrop !== undefined) { repo.kept = null; forgetKeptDraft(); sv.focus = '[data-svedit]'; render(); return; }
  if (ds.repokeep !== undefined) { resolveLeaving(false); return; }
  if (ds.repodiscard !== undefined) { resolveLeaving(true); return; }
  /* the catalogue (D63) */
  if (ds.catcsv !== undefined) { downloadCatalogue(); return; }
  if (ds.catcols !== undefined) { cat.openChip = cat.openChip === 'cols' ? null : 'cols'; render(); return; }
  if (ds.catshowall !== undefined) { cat.hidden = []; render(); return; }
  if (ds.catdensity !== undefined) { cat.density = ds.catdensity === 'comfortable' ? 'comfortable' : 'dense'; render(); return; }
  if (ds.catgroup !== undefined) { catSetGroup(ds.catgroup === 'category'); return; }
  if (ds.catfoldall !== undefined) { catFoldAll(ds.catfoldall === 'shut'); return; }
  if (ds.catfold !== undefined) { catFold(ds.catfold); return; }
  if (ds.catclearall !== undefined) { cat.query = ''; cat.filters = {}; cat.openChip = null; render(); return; }
  if (ds.catsort !== undefined) {
    /* ascending, then descending, then back to the delivery's own order */
    if (!cat.sort || cat.sort.key !== ds.catsort) cat.sort = { key: ds.catsort, dir: 'asc' };
    else if (cat.sort.dir === 'asc') cat.sort.dir = 'desc';
    else cat.sort = null;
    /* a new order reads from the top - and stays on the column that was
       clicked, however far along the table it is */
    render(); catScrollTo(0); return;
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
  if (ds.catopen !== undefined) {
    var used = sleeveById(parseInt(ds.catopen, 10));
    if (used) {
      cat.detail = null; cat.compare = false;
      goTo({ to: used.id, variant: used.variant, category: used.category });
    }
    return;
  }
  if (ds.catrow !== undefined) {
    cat.cursor = ds.catrow;
    cat.cursorBand = ds.catband === undefined ? null : ds.catband;
    cat.detail = cat.detail === ds.catrow ? null : ds.catrow;
    render(); return;
  }
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
  /* the Sleeves table's compare boxes and filters (D156) */
  if (ds.svtick !== undefined) {
    var tid = parseInt(ds.svtick, 10);
    sv.sel = svTick(sv.sel, tid, e.target.checked, SV_COMPARE_MAX);
    if (sv.sel.length < 2) sv.compare = false;
    sv.focus = '[data-svtick="' + tid + '"]'; render(); return;
  }
  if (ds.svfv !== undefined) { sv.fv = e.target.value; sv.focus = '[data-svfv]'; render(); return; }
  if (ds.svfc !== undefined) { sv.fc = e.target.value; sv.focus = '[data-svfc]'; render(); return; }
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
  var row = e.target.closest ? e.target.closest('[data-svmenu]') : null;
  if (!row) return;
  e.preventDefault();
  var id = parseInt(row.dataset.svmenu, 10);
  var host = document.getElementById('repoDialog');
  var box = host.querySelector('.dialog.repo').getBoundingClientRect();
  /* positioned inside the dialog, and kept off its edges so the menu is
     never half outside the thing it belongs to */
  repo.menu = { id: id,
                x: Math.min(e.clientX - box.left, box.width - 250),
                y: Math.min(e.clientY - box.top, box.height - 210) };
  /* it acts on the sleeve it was opened on, by id; the draft in hand and
     the place it reads its checks from are left alone (D156) */
  sv.focus = '.repo-ctx .repo-mi:not([disabled])';
  render();
});

document.addEventListener('change', function (e) {
  if (!repo.open || !repo.draft) return;
  var el = e.target;
  /* the New sleeve form and its Add a product dialog (D157) */
  if (el.dataset && el.dataset.ncvar !== undefined) { ncToggleType(el.dataset.ncvar, el.checked); return; }
  if (el.dataset && el.dataset.ncallv !== undefined) {
    nc.allV = el.checked;
    var list = document.getElementById('ncSrcList'); if (list) list.innerHTML = ncSourcesHtml(repo.draft);
    var count = document.getElementById('ncSrcCount'); if (count) count.textContent = ncSourceCount(repo.draft);
    return;
  }
  if (el.dataset && el.dataset.ncdtick !== undefined) { ncDlgTick(el.dataset.ncdtick, el.checked); return; }
  if (el.dataset && el.dataset.ncdf !== undefined && nc.dlg) { nc.dlg[el.dataset.ncdf] = el.value; ncDlgRefresh(); return; }
  /* a rule's chip (D89): the value goes in or out of that rule's field, in
     the vocabulary's order, and the count is asked for again */
  if (el.dataset && el.dataset.reporule !== undefined && el.dataset.repofld !== undefined) {
    var rule = repo.draft.rules[parseInt(el.dataset.reporule, 10)]; if (!rule) return;
    var field = el.dataset.repofld, value = el.dataset.repoval;
    var have = (rule[field] || []).filter(function (v) { return v !== value; });
    if (el.checked) have.push(value);
    var order = (repo.data.ruleVocabulary || {})[field] || [];
    rule[field] = order.filter(function (v) { return have.indexOf(v) !== -1; });
    markDirty(); repo.fieldError = null;
    el.closest('.repo-chip').classList.toggle('on', el.checked);
    schedulePreview(); updateTotals();
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
  if (el.id === 'repoName') {
    repo.draft.name = el.value;
    if (repo.fieldError && repo.fieldError.field === 'name') { repo.fieldError = null; el.removeAttribute('aria-invalid'); }
    markDirty(); updateTotals(); return;
  }
  if (el.id === 'repoNote') { repo.draft.note = el.value; markDirty(); updateTotals(); return; }
  if (el.id === 'repoLabel') {
    repo.draft.label = el.value; markDirty(); repo.fieldError = null;
    /* the words under the rules speak of the label */
    var applies = document.getElementById('repoApplies'); if (applies && !(repo.draft.rules || []).length) applies.innerHTML = appliesHtml();
    updateTotals(); return;
  }
  if (el.dataset && el.dataset.repoweight !== undefined) {
    var i = parseInt(el.dataset.repoweight, 10);
    /* a plain number or nothing: '1,5' is not 15 (D156 QA 15) */
    var v = svWeight(el.value);
    repo.draft.products[i].weightText = el.value;
    repo.draft.products[i].weightPct = v;
    if (repo.fieldError && (repo.fieldError.field === 'weights' || repo.fieldError.field === 'products')) repo.fieldError = null;
    if (el.value.trim() !== '' && !isFinite(v)) el.setAttribute('aria-invalid', 'true'); else el.removeAttribute('aria-invalid');
    markDirty(); updateTotals(); return;
  }
  if (el.id === 'repoSearch' && repo.picker) {
    repo.picker.query = el.value; repo.picker.index = 0; updatePickerList();
  }
  /* the New sleeve form's two searches redraw their results, never the box (D157) */
  if (el.id === 'ncSrcQ') {
    nc.srcq = el.value;
    var list = document.getElementById('ncSrcList'); if (list) list.innerHTML = ncSourcesHtml(repo.draft);
    var count = document.getElementById('ncSrcCount'); if (count) count.textContent = ncSourceCount(repo.draft);
    return;
  }
  if (el.id === 'ncDlgQ' && nc.dlg) { nc.dlg.q = el.value; ncDlgRefresh(); }
});

/* The sleeve search redraws the results, never the box, so the caret stays
   where it was typed (C1): the cards' results, or the table's rows (D156). */
document.addEventListener('input', function (e) {
  if (!repo.open || repo.view !== 'sleeves' || !e.target || e.target.id !== 'repoFind') return;
  repo.query = e.target.value;
  svRefreshResults();
});

/* Capture phase, on purpose: the fee card's own Escape handler was
   registered first and hides the card synchronously, and a bubbling handler
   here would then find no card open and close this dialog as well. Seen
   first, the card's presence is real and the key is left to it. */
document.addEventListener('keydown', function (e) {
  if (!repo.open) return;
  /* the Add a product dialog is modal (D157): it keeps the keyboard, and
     Escape closes it before anything else hears the key */
  /* a save in flight: nothing closes or discards until it lands (D157 review) */
  if (repo.view === 'sleeves' && repo.saving && e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); return; }
  if (repo.view === 'sleeves' && nc.dlg && ncOpen()) {
    if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation();
      var q = document.getElementById('ncDlgQ');
      if (q && e.target === q && q.value) { q.value = ''; nc.dlg.q = ''; ncDlgRefresh(); return; }
      ncCloseDialog(); return;
    }
    if (e.key === 'Tab') {
      var ring = [].slice.call(document.querySelectorAll('#repoDialog .nc-dlg button, #repoDialog .nc-dlg input, #repoDialog .nc-dlg select'))
        .filter(function (x) { return !x.disabled && x.offsetParent !== null; });
      if (ring.length) {
        var at = ring.indexOf(document.activeElement);
        var next = at === -1 ? (e.shiftKey ? ring.length - 1 : 0)
          : e.shiftKey ? (at === 0 ? ring.length - 1 : -1) : (at === ring.length - 1 ? 0 : -1);
        if (next !== -1) { e.preventDefault(); ring[next].focus(); }
      }
      return;
    }
    if (e.key === 'Enter' && e.target && e.target.id === 'ncDlgQ') { e.preventDefault(); return; }
    return;
  }
  if (repo.view === 'sleeves' && e.key === 'Escape' && nc.ask != null && ncOpen()) {
    e.preventDefault(); e.stopPropagation();
    var keptFor = nc.ask; nc.ask = null; sv.focus = '[data-ncsrc="' + keptFor + '"]||#ncSrcQ'; render(); return;
  }
  if (repo.view === 'sleeves' && e.key === 'Escape' && ncOpen() && e.target && e.target.id === 'ncSrcQ' && e.target.value) {
    e.preventDefault(); e.stopPropagation();
    e.target.value = ''; nc.srcq = '';
    var srcList = document.getElementById('ncSrcList'); if (srcList) srcList.innerHTML = ncSourcesHtml(repo.draft);
    var srcCount = document.getElementById('ncSrcCount'); if (srcCount) srcCount.textContent = ncSourceCount(repo.draft);
    return;
  }
  /* the overlay drawer, then the unsaved-changes notice, take Escape
     before the console does (D155) */
  if (repo.view === 'overlays' && e.key === 'Escape' && ovl.edit != null) {
    e.preventDefault(); e.stopPropagation(); ovlCloseDrawer(false); return;
  }
  if (repo.view === 'overlays' && e.key === 'Escape' && ovl.leaving) {
    e.preventDefault(); e.stopPropagation(); ovl.leaving = null; render(); ovlFocus('#ovlNote'); return;
  }
  if (repo.picker && e.target.id === 'repoSearch') {
    var hits = pickerMatches();
    if (e.key === 'ArrowDown') { e.preventDefault(); repo.picker.index = Math.min(repo.picker.index + 1, Math.min(hits.length, 40) - 1); updatePickerList(); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); repo.picker.index = Math.max(repo.picker.index - 1, 0); updatePickerList(); return; }
    if (e.key === 'Enter') { e.preventDefault(); if (hits[repo.picker.index]) choose(hits[repo.picker.index].productId); return; }
    if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation();
      sv.focus = '[data-repopick="' + repo.picker.row + '"]||[data-repoadd]';
      closePicker(); return;
    }
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
      /* space and enter on a button are that button's: a header sorts, a
         band folds, a pin box pins. Anywhere else they are the cursor row's. */
      var onButton = e.target && e.target.tagName === 'BUTTON';
      var plain = !e.metaKey && !e.ctrlKey && !e.altKey;
      if (e.key === 'ArrowDown') { e.preventDefault(); catMoveCursor(1); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); catMoveCursor(-1); return; }
      if (e.key === ' ' && cat.cursor && !onButton) { e.preventDefault(); catTogglePin(cat.cursor); render(); catFocusCursor(); return; }
      if (e.key === 'Enter' && cat.cursor && !cat.compare && !onButton) { e.preventDefault(); cat.detail = cat.detail === cat.cursor ? null : cat.cursor; render(); catFocusCursor(); return; }
      if ((e.key === 'c' || e.key === 'C') && plain && (cat.pins.length || cat.compare)) { e.preventDefault(); cat.compare = !cat.compare; cat.detail = null; render(); return; }
      if ((e.key === 'g' || e.key === 'G') && plain && !cat.compare) { e.preventDefault(); catSetGroup(!cat.group); return; }
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
  /* the drawer keeps the keyboard (D156): Tab cycles through it and the view
     switch above it - the one control outside it that still applies - and
     never wanders to the page behind the console */
  if (repo.view === 'sleeves' && e.key === 'Tab' && sv.mode === 'table' && sv.drawer && !repo.leaving) {
    var ring = [].slice.call(document.querySelectorAll('#repoDialog .sv-mode button, #repoDialog .sv-drawer button, '
      + '#repoDialog .sv-drawer input, #repoDialog .sv-drawer select, #repoDialog .sv-drawer [tabindex="0"]'))
      .filter(function (el) { return !el.disabled && el.offsetParent !== null; });
    if (ring.length) {
      var at = ring.indexOf(document.activeElement);
      var next = at === -1 ? (e.shiftKey ? ring.length - 1 : 0)
        : e.shiftKey ? (at === 0 ? ring.length - 1 : -1) : (at === ring.length - 1 ? 0 : -1);
      if (next !== -1) { e.preventDefault(); ring[next].focus(); return; }
    }
  }
  /* Escape climbs the Sleeves view a level at a time - the comparison, the
     drawer, a sleeve's page, a category - and closes the console only from
     the top, as the catalogue and the archive do once their panels are shut
     (D156). Unsaved changes still ask first: every step goes through goTo. */
  if (repo.view === 'sleeves' && e.key === 'Escape' && !repo.leaving && !repo.menu && !repo.confirmDelete && !repo.picker) {
    var feeOn = document.getElementById('feeDialog');
    if (!(feeOn && !feeOn.hidden) && e.target.id !== 'repoFind') {
      if (sv.compare && !sv.drawer) { e.preventDefault(); e.stopPropagation(); sv.compare = false; sv.focus = '[data-svcompare]||.sv-rowbtn'; render(); return; }
      if (sv.mode === 'table' && sv.drawer) { e.preventDefault(); e.stopPropagation(); if (ncOpen()) ncCancel(); else svCloseDrawer(); return; }
      if (sv.mode !== 'table' && sv.level > 0 && !repo.query.trim()) { e.preventDefault(); e.stopPropagation(); svUp(); return; }
    }
  }
  if (repo.view === 'sleeves') {
    var find = document.getElementById('repoFind');
    var typing = e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT'
                              || e.target.tagName === 'TEXTAREA');
    if (e.key === '/' && !typing && find) { e.preventDefault(); find.focus(); find.select(); return; }
    if (e.key === 'Escape' && find && e.target === find) {
      e.preventDefault(); e.stopPropagation();
      if (repo.query) { repo.query = ''; find.value = ''; render(); }
      else find.blur();
      return;
    }
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
  if (e.key === 'Enter' && (e.target.id === 'repoName' || e.target.id === 'repoLabel')) {
    e.preventDefault();
    if (repo.draft && repo.draft.create) ncCreate(); else saveDraft();
  }
}, true);

/* The catalogue's edge shadow follows its table's scroll (D99). A scroll does
   not bubble, and every redraw makes a new box to listen on, so it is caught
   on the way down instead. */
document.addEventListener('scroll', function (e) {
  if (repo.open && e.target && e.target.id === 'catTblWrap') catEdge();
}, true);
/* Every band the scroll has passed is held under the header, beneath the one
   on show - and the Tab key still reaches it there. Focus is never left on a
   band that cannot be seen: the keyboard's arrival brings it back. */
document.addEventListener('focusout', function (e) {
  var el = e.target;
  if (repo.open && ncOpen() && el && el.dataset && el.dataset.repoweight !== undefined && !nc.touched) {
    nc.touched = true;
    window.setTimeout(ncRefresh, 0);
  }
});
document.addEventListener('focusin', function (e) {
  var el = e.target;
  if (repo.open && el && el.closest && el.closest('#repoDialog .sv-edit')) {
    sv.editFocus = el.id ? '#' + svCssEscape(el.id)
      : (el.dataset && el.dataset.repoweight !== undefined ? '[data-repoweight="' + el.dataset.repoweight + '"]' : null);
  }
  if (!repo.open || !el.classList || !el.classList.contains('cat-fold')) return;
  if (el.matches && el.matches(':focus-visible')) catBandIntoView(el.closest('tr'));
});
window.addEventListener('resize', function () {
  var side = document.querySelector('#repoDialog #ncSide'), box = side && side.closest('.sv-scroll');
  if (side && box) side.style.maxHeight = Math.max(260, box.clientHeight - 30) + 'px';
  if (repo.open && repo.view === 'catalogue') catEdge();
});

App.addRenderer(renderEntryLinks);
App.openRepository = openRepository;
})();
