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
  { key: 'book', label: 'Book' }
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
    draft.edition = { ofId: edition.ofId, variant: from ? from.variant : repo.variant };
    draft.name = from ? from.name : edition.name;
    draft.note = from ? (from.note || '') : '';
    draft.products = from ? from.products.map(function (row) {
      return { productId: row.productId, weightPct: Math.round(row.weight * 10000) / 100 };
    }) : [];
    return draft;
  }
  if (create) {
    draft.create = true;
    draft.category = repo.category;
    draft.variants = [repo.variant];
  }
  return draft;
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
  var variant = d.edition ? d.edition.variant : (d.create ? (d.variants || [])[0] : repo.variant);
  var category = d.create ? d.category : repo.category;
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

function loadDraft(sleeveId, create, edition) {
  var entry = sleeveId ? sleeveById(sleeveId) : null;
  repo.sleeveId = entry ? entry.id : null;
  repo.draft = entry ? draftFrom(entry) : newDraft(create, edition);
  repo.dirty = false; repo.fieldError = null; repo.picker = null;
  repo.savedAt = null; repo.edMenu = false;
  repo.kept = keptDraftFor(repo.draft);        /* a copy that outlived a reload (F1) */
  repo.confirmDelete = false; repo.leaving = null; repo.menu = null;
  /* an existing edition already knows how many portfolios it names */
  repo.preview = entry && entry.rules && entry.rules.length ? { applies: entry.applies } : null;
  repo.previewStamp += 1;
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
  if (t.fresh) { loadDraft(null, t.create, t.edition); }
  else if (t.to != null) { loadDraft(t.to); }
  else { repo.sleeveId = null; repo.dirty = false; chooseDefaults(); }
  render();
  if (t.fresh) {
    var focusOn = document.getElementById(t.edition ? 'repoLabel' : 'repoName');
    if (focusOn) focusOn.focus();
  }
  if (t.then) t.then();
}
function selectSleeve(id) {
  if (id === repo.sleeveId && repo.draft && repo.draft.id === id) return;
  /* a search hit can live under another category or book, so following it
     moves both - and the row said where it was going (C1) */
  var entry = sleeveById(id);
  goTo(entry ? { to: id, category: entry.category, variant: entry.variant } : { to: id });
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
    name: d.name.trim(), note: d.note.trim(),
    label: d.label.trim(), rules: cleanRules(d.rules),
    products: d.products.map(function (r) {
      return { productId: r.productId, weight: Math.round(r.weightPct * 10000) / 1000000 };
    })
  };
  repo.saving = true; repo.fieldError = null; repo.error = null; render();
  try {
    var r;
    if (d.id) {
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
      repo.savedAt = new Date().toISOString();
      repo.kept = null; forgetKeptDraft();     /* it is in the store now (F1) */
      /* the outcome, not just the redraw (G2): the revision it became is the
         part an admin checks, and it is the part a screen reader could not see */
      App.announce('polite', madeAll.length > 1
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
      variant: d.edition ? d.edition.variant : (d.create ? (d.variants || [])[0] || repo.variant : repo.variant),
      category: d.create ? d.category : repo.category,
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
  if (d.edition) {
    var of = sleeveById(d.edition.ofId);
    prov = 'New edition of ' + esc(d.name) + ' · ' + esc(repo.category) + ' under ' + esc(d.edition.variant)
      + (of ? ' · starts as a copy of the ' + (of.label ? esc(of.label) + ' edition' : 'fallback') : '');
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
          var full = isFixed(d.category) && sleevesIn(v, d.category).some(function (x) {
            return x.name.trim().toLowerCase() !== d.name.trim().toLowerCase();
          });
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
      + (repo.fieldError && repo.fieldError.field === 'name' ? ' aria-invalid="true"' : '') + '>' + fieldErr('name') + '</div>';
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
    + variantField
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
  var pane = document.querySelector('.repo-ed');
  var panel = document.querySelector('.repo-hist');
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
    + '<button type="button" class="repo-mi danger" data-reporemove="' + entry.id + '"'
    + (entry.fixed ? ' disabled title="A fixed category always holds one sleeve"' : '')
    + '>Archive from ' + esc(entry.category) + '</button>'
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

function sleeveHitsHtml() {
  var hits = sleeveHits();
  if (!hits.length) {
    return '<p class="repo-none">Nothing matches \u201c' + esc(repo.query.trim())
      + '\u201d in any category or book.</p>';
  }
  return '<p class="repo-hits">' + hits.length + ' sleeve' + (hits.length === 1 ? '' : 's')
    + ' match</p>'
    + hits.map(function (h) {
        var s = h.s;
        return '<button type="button" class="repo-sleeve" data-reposleeve="' + s.id + '"'
          + ' aria-selected="' + (s.id === repo.sleeveId ? 'true' : 'false') + '">'
          + '<b>' + esc(s.name) + (s.label ? ' <span class="repo-fixed">' + esc(s.label) + '</span>' : '')
          + (s.problems && s.problems.length
              ? ' <span class="warn">' + s.problems.length + ' problem'
                + (s.problems.length === 1 ? '' : 's') + '</span>' : '')
          + '</b>'
          /* where it lives, because following a hit moves the category and
             the book under the admin: saying so makes that read as navigation */
          + '<small>' + esc(s.category) + ' \u00b7 ' + esc(s.variant)
          + (h.why ? ' \u00b7 ' + esc(h.why) : '') + '</small></button>';
      }).join('');
}

/* The sleeves of the category and book in hand, editions of one name
   gathered under it (D89). Its own function because the search redraws just
   this part, leaving the box the query was typed into alone (C1). */
function sleeveRowHtml(s, grouped) {
  var vehicles = [];
  s.products.forEach(function (r) {
    if (r.product && vehicles.indexOf(r.product.vehicle) === -1) vehicles.push(r.product.vehicle);
  });
  var sub = s.products.length + ' product' + (s.products.length === 1 ? '' : 's')
    + (vehicles.length ? ' \u00b7 ' + vehicles.join(', ') : '')
    + ' \u00b7 saved ' + shortDate(s.updatedAt) + (s.updatedBy ? ' by ' + s.updatedBy : '');
  var head = grouped
    ? (s.fallback ? 'Fallback <small>applies wherever no other edition does</small>'
                  : esc(s.label) + ' <small>' + s.applies + ' portfolio' + (s.applies === 1 ? '' : 's') + '</small>')
    : esc(s.name) + (s.fallback ? '' : ' <span class="repo-fixed">' + esc(s.label) + '</span>');
  return '<button type="button" class="repo-sleeve' + (grouped ? ' ed' : '') + '" data-reposleeve="' + s.id + '"'
    + ' aria-selected="' + (s.id === repo.sleeveId && !(repo.draft && repo.draft.id === null) ? 'true' : 'false') + '">'
    + '<b>' + head + (s.problems.length ? ' <span class="warn">' + s.problems.length + ' problem'
        + (s.problems.length === 1 ? '' : 's') + '</span>' : '') + '</b>'
    + '<small>' + esc(sub) + '</small></button>';
}

function sleeveListHtml() {
  var offered = sleevesIn(repo.variant, repo.category);
  var groups = [], byName = {};
  offered.forEach(function (s) {
    if (!byName[s.name]) { byName[s.name] = []; groups.push(s.name); }
    byName[s.name].push(s);
  });
  var list = groups.map(function (name) {
    var eds = byName[name];
    if (eds.length === 1) return sleeveRowHtml(eds[0], false);
    return '<div class="repo-grp"><b>' + esc(name) + '</b><small>' + eds.length + ' editions</small></div>'
      + eds.map(function (s) { return sleeveRowHtml(s, true); }).join('');
  }).join('') || '<p class="repo-none">No sleeve under ' + esc(repo.variant) + ' yet.</p>';
  if (repo.draft && repo.draft.id === null) {
    list += '<div class="repo-sleeve new" aria-current="true"><b>' + (esc(repo.draft.name) || 'New sleeve')
      + (repo.draft.edition ? ' <span class="repo-fixed">' + (esc(repo.draft.label) || 'new edition') + '</span>' : '')
      + '</b><small>unsaved</small></div>';
  }
  return list;
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
  var newDisabled = isFixed(repo.category)
    && sleevesIn(repo.variant, repo.category).length >= 1;
  /* Searching (C1) replaces the list rather than filtering it in place: a hit
     may live under another category or book, and the point of asking is that
     you do not remember which. */
  var searching = !!repo.query.trim();
  var body = searching ? sleeveHitsHtml() : sleeveListHtml();
  return '<div class="repo-b">'
    + '<div class="repo-pane"><div class="repo-pane-h">Categories</div>' + cats + '</div>'
    + '<div class="repo-pane repo-list">'
    + '<div class="repo-pane-h">' + (searching ? 'Search results' : esc(repo.category))
    + '<button type="button" class="btn" data-reponew'
    + (newDisabled && !searching ? ' disabled title="A fixed category holds exactly one sleeve"' : '')
    + '>+ New sleeve</button></div>'
    /* the book strip belongs to this list, not to the console's header (A1) */
    + '<div class="repo-books" role="group" aria-label="Implementation type">'
    + d.variants.map(function (v) {
        return '<button type="button" class="repo-book" data-repovariant="' + esc(v) + '"'
          + ' aria-pressed="' + (v === repo.variant ? 'true' : 'false') + '"'
          + (searching ? ' disabled' : '') + '>' + esc(v) + '</button>';
      }).join('') + '</div>'
    + '<label class="repo-find"><span aria-hidden="true">\u2315</span>'
    + '<input type="search" id="repoFind" placeholder="Search every book\u2026" autocomplete="off"'
    + ' aria-label="Search sleeves by name, edition or product, across every category and book"'
    + ' value="' + esc(repo.query) + '"><kbd aria-hidden="true">/</kbd></label>'
    + body + '</div>'
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
          + regSelect('regbook', 'Book', regFacetOptions('variant', reg.variant, 'All books'), reg.variant)
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
    + line('Offered in', function (r) { return r.books.length ? r.books.length + ' book' + (r.books.length === 1 ? '' : 's') : '—'; })
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
    }));
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
  var d = repo.data;
  var onCatalogue = repo.view === 'catalogue';
  var onArchive = repo.view === 'archive', onActivity = repo.view === 'activity';
  var onRegister = repo.view === 'proposals';
  var onSleeves = !onCatalogue && !onArchive && !onActivity && !onRegister;

  /* One tab strip in the header, and it navigates (A1). The implementation
     type used to sit beside it, identical in shape and selected state but
     filtering rather than navigating - and it existed on one view of five, so
     the header changed size as you moved. It is a property of the sleeve
     list, so it has moved onto the sleeve list's own header. */
  var VIEWS = [['sleeves', 'Sleeves', onSleeves, 0],
               ['catalogue', 'Catalogue', onCatalogue, 0],
               ['archive', 'Archive', onArchive, d ? archivedSleeves().length : 0],
               ['activity', 'Activity', onActivity, 0],
               ['proposals', 'Proposals', onRegister, d && d.register ? d.register.proposals : 0]];
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
    footer = catKeysHtml()
      + '<div class="repo-f cat-f">' + catTrayHtml()
      + (repo.error ? '<span class="md-err" role="alert">' + esc(repo.error) + '</span>' : '')
      + '</div>';
  } else {
    /* One primary action on the right, and nothing destructive beside it
       (A2). Archiving and adding an edition act on the sleeve being edited,
       so they have moved into the editor where that sleeve is named; what is
       left here is the library's own provenance and Save. */
    var canSave = !!repo.draft && repo.dirty && !draftProblems().length && !repo.saving;
    footer = '<div class="repo-f">'
      + (d ? '<button type="button" class="btn btn-create" data-repocreate'
          + (repo.saving ? ' disabled' : '') + '>+ Create sleeve</button>' : '')
      + (d ? '<span class="repo-src">Library · ' + esc(d.store.path.split('/').pop()) + ' · ' + d.store.sleeves
          + ' sleeves · ' + (d.store.revisions || 0) + ' versions on record'
          + (d.store.archived ? ' · ' + d.store.archived + ' archived' : '') + '</span>' : '')
      + '<span class="spacer"></span>'
      + (repo.error ? '<span class="md-err" role="alert">' + esc(repo.error) + '</span>' : '')
      + saveStateHtml()
      + '<button type="button" class="btn btn-primary" data-reposave' + (canSave ? '' : ' disabled') + '>'
        + (repo.saving ? 'Saving…' : (repo.draft && repo.draft.create ? 'Create sleeve'
           : (repo.draft && repo.draft.edition ? 'Create edition' : 'Save sleeve'))) + '</button>'
      + '</div>';
  }

  /* the tabs name this, and it is labelled by whichever is selected (G1) */
  body = '<div id="repoPanel" role="tabpanel" aria-labelledby="repotab-' + repo.view + '">'
    + body + '</div>';

  host.innerHTML = '<div class="scrim" data-reposcrim></div>'
    + '<div class="dialog repo' + (onCatalogue ? ' catalogue' : '') + (onArchive ? ' archive' : '') + (onActivity ? ' activity' : '') + (onRegister ? ' register' : '')
    + (!onCatalogue && !onArchive && !onActivity && !onRegister ? ' sleeves' : '') + '" role="dialog" aria-modal="true" aria-labelledby="repoTitle">'
    + header + leaving + body + footer + '</div>';
  syncHash();
  keepDraft();                  /* the local copy follows the draft (F1) */
  putBack(); catEdge();

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
}

function updateTotals() {
  var tot = document.getElementById('repoTotal'); if (tot) tot.innerHTML = totalHtml();
  var save = document.querySelector('[data-reposave]');
  if (save) save.disabled = !(repo.dirty && !draftProblems().length && !repo.saving);
  /* typing is a partial redraw, so the footer's marker has to be told (F2) */
  var state = document.querySelector('.repo-f .repo-state');
  if (state) state.outerHTML = saveStateHtml();
  else if (save) save.insertAdjacentHTML('beforebegin', saveStateHtml());
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
  ['activity', 'Activity'], ['proposals', 'Proposals']
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
    '[data-repoclose],[data-reposcrim],[data-repoview],[data-repovariant],[data-repocat],[data-reposleeve],'
    + '[data-reponew],[data-repoadd],[data-reporm],[data-repopick],[data-repochoose],[data-reposave],'
    + '[data-repodelete],[data-repocanceldelete],[data-repokeep],[data-repodiscard],'
    + '[data-repokeptrestore],[data-repokeptdrop],'
    + '[data-catcols],[data-catshowall],[data-catdensity],[data-catclearall],[data-catsort],[data-catcsv],'
    + '[data-catpin],[data-catunpin],[data-catclearpins],[data-catcompare],[data-catdetailclose],'
    + '[data-catrow],[data-catopen],[data-catgroup],[data-catfold],[data-catfoldall],'
    + '[data-repocreate],[data-repocopy],[data-reporemove],[data-repoedition],[data-reporuleadd],[data-reporulerm],'
    + '[data-repohistory],[data-reporev],[data-reporevert],'
    + '[data-arcsort],[data-arcrow],[data-arcclear],[data-arcclearsel],[data-arcrestore],[data-arcrestoresel],[data-arcdetailclose],'
    + '[data-acttoggle],[data-actclear],[data-actmore],[data-actview],[data-actrestore],[data-actrange],'
    + '[data-regrow],[data-regclear],[data-regmore],[data-regpic],[data-regdetailclose],'
    + '[data-regview],[data-regfilters],[data-reguid]') : null;
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
  /* One create path (B2). Two buttons a verb apart used to make forms that
     differed in which fields existed, with nothing on screen to say which was
     which; now both open the create form, which arrives with the category and
     the book already filled in from wherever the admin was standing. */
  if (ds.reponew !== undefined || ds.repocreate !== undefined) {
    goTo({ fresh: true, create: true }); return;
  }
  if (ds.repocopy !== undefined) { copyToVariant(repo.menu && repo.menu.id, ds.repocopy); return; }
  /* editions (D89) */
  if (ds.repoedition !== undefined) {
    var ofId = parseInt(ds.repoedition, 10); var of = sleeveById(ofId); repo.menu = null;
    if (of) goTo({ variant: of.variant, category: of.category, fresh: true, edition: { ofId: ofId, name: of.name } });
    return;
  }
  if (ds.reporuleadd !== undefined) {
    var blank = {}; RULE_FIELDS.forEach(function (f) { blank[f] = []; });
    repo.draft.rules.push(blank); repo.dirty = true; repo.fieldError = null; render(); return;
  }
  if (ds.reporulerm !== undefined) {
    repo.draft.rules.splice(parseInt(ds.reporulerm, 10), 1); repo.dirty = true; repo.fieldError = null;
    render(); schedulePreview(); return;
  }
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
  if (ds.repodelete !== undefined) { deleteCurrent(); return; }
  if (ds.repocanceldelete !== undefined) { repo.confirmDelete = false; render(); return; }
  if (ds.repokeptrestore !== undefined) {
    var held = repo.kept; repo.kept = null;
    if (held && held.draft) {
      repo.draft = held.draft; repo.dirty = true;
      App.announce('polite', 'Unsaved changes restored.');
    }
    render(); return;
  }
  if (ds.repokeptdrop !== undefined) { repo.kept = null; forgetKeptDraft(); render(); return; }
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
    return;
  }
  /* a rule's chip (D89): the value goes in or out of that rule's field, in
     the vocabulary's order, and the count is asked for again */
  if (el.dataset && el.dataset.reporule !== undefined && el.dataset.repofld !== undefined) {
    var rule = repo.draft.rules[parseInt(el.dataset.reporule, 10)]; if (!rule) return;
    var field = el.dataset.repofld, value = el.dataset.repoval;
    var have = (rule[field] || []).filter(function (v) { return v !== value; });
    if (el.checked) have.push(value);
    var order = (repo.data.ruleVocabulary || {})[field] || [];
    rule[field] = order.filter(function (v) { return have.indexOf(v) !== -1; });
    repo.dirty = true; repo.fieldError = null;
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
  if (el.id === 'repoName') { repo.draft.name = el.value; repo.dirty = true; updateTotals(); return; }
  if (el.id === 'repoNote') { repo.draft.note = el.value; repo.dirty = true; updateTotals(); return; }
  if (el.id === 'repoLabel') {
    repo.draft.label = el.value; repo.dirty = true; repo.fieldError = null;
    /* the words under the rules speak of the label */
    var applies = document.getElementById('repoApplies'); if (applies && !(repo.draft.rules || []).length) applies.innerHTML = appliesHtml();
    updateTotals(); return;
  }
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

/* The sleeve search redraws the list and the pane heading, never the box, so
   the caret stays where it was typed (C1). */
document.addEventListener('input', function (e) {
  if (!repo.open || repo.view !== 'sleeves' || !e.target || e.target.id !== 'repoFind') return;
  repo.query = e.target.value;
  var pane = document.querySelector('.repo-list'); if (!pane) return;
  var head = pane.querySelector('.repo-pane-h');
  var searching = !!repo.query.trim();
  if (head) head.firstChild.nodeValue = searching ? 'Search results' : repo.category;
  pane.querySelectorAll('.repo-book').forEach(function (b) { b.disabled = searching; });
  var keep = pane.querySelector('.repo-find');
  var after = keep && keep.nextSibling;
  while (after) { var next = after.nextSibling; after.remove(); after = next; }
  keep.insertAdjacentHTML('afterend', searching ? sleeveHitsHtml() : sleeveListHtml());
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
  if (e.key === 'Enter' && (e.target.id === 'repoName' || e.target.id === 'repoLabel')) { e.preventDefault(); saveDraft(); }
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
document.addEventListener('focusin', function (e) {
  var el = e.target;
  if (!repo.open || !el.classList || !el.classList.contains('cat-fold')) return;
  if (el.matches && el.matches(':focus-visible')) catBandIntoView(el.closest('tr'));
});
window.addEventListener('resize', function () {
  if (repo.open && repo.view === 'catalogue') catEdge();
});

App.addRenderer(renderEntryLinks);
App.openRepository = openRepository;
})();
