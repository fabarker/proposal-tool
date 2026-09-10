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
  { key: 'shareClass', label: 'Share class', order: ['Dis', 'Acc'] },
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
  { key: 'shareClass', label: 'Share' },
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

function loadDraft(sleeveId, create, edition) {
  var entry = sleeveId ? sleeveById(sleeveId) : null;
  repo.sleeveId = entry ? entry.id : null;
  repo.draft = entry ? draftFrom(entry) : newDraft(create, edition);
  repo.dirty = false; repo.fieldError = null; repo.picker = null;
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
      App.announce('polite', madeAll.length > 1
        ? 'Saved ' + last.name + ' under ' + madeAll.length + ' implementation types.'
        : 'Saved ' + last.name + (last.label ? ' (' + last.label + ' edition)' : '') + '.');
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
  /* editions of one name sit together under it (D89); a name with a single
     fallback reads exactly as a sleeve always did */
  var groups = [], byName = {};
  offered.forEach(function (s) {
    if (!byName[s.name]) { byName[s.name] = []; groups.push(s.name); }
    byName[s.name].push(s);
  });
  var newDisabled = isFixed(repo.category) && groups.length >= 1;
  var rowHtml = function (s, grouped) {
    var vehicles = [];
    s.products.forEach(function (r) { if (r.product && vehicles.indexOf(r.product.vehicle) === -1) vehicles.push(r.product.vehicle); });
    var sub = s.products.length + ' product' + (s.products.length === 1 ? '' : 's')
      + (vehicles.length ? ' · ' + vehicles.join(', ') : '')
      + ' · saved ' + shortDate(s.updatedAt) + (s.updatedBy ? ' by ' + s.updatedBy : '');
    var head = grouped
      ? (s.fallback ? 'Fallback <small>applies wherever no other edition does</small>'
                    : esc(s.label) + ' <small>' + s.applies + ' portfolio' + (s.applies === 1 ? '' : 's') + '</small>')
      : esc(s.name) + (s.fallback ? '' : ' <span class="repo-fixed">' + esc(s.label) + '</span>');
    return '<button type="button" class="repo-sleeve' + (grouped ? ' ed' : '') + '" data-reposleeve="' + s.id + '"'
      + ' aria-selected="' + (s.id === repo.sleeveId && !(repo.draft && repo.draft.id === null) ? 'true' : 'false') + '">'
      + '<b>' + head + (s.problems.length ? ' <span class="warn">' + s.problems.length + ' problem' + (s.problems.length === 1 ? '' : 's') + '</span>' : '') + '</b>'
      + '<small>' + esc(sub) + '</small></button>';
  };
  var list = groups.map(function (name) {
    var eds = byName[name];
    if (eds.length === 1) return rowHtml(eds[0], false);
    return '<div class="repo-grp"><b>' + esc(name) + '</b><small>' + eds.length + ' editions</small></div>'
      + eds.map(function (s) { return rowHtml(s, true); }).join('');
  }).join('') || '<p class="repo-none">No sleeve under ' + esc(repo.variant) + ' yet.</p>';
  if (repo.draft && repo.draft.id === null) {
    list += '<div class="repo-sleeve new" aria-current="true"><b>' + (esc(repo.draft.name) || 'New sleeve')
      + (repo.draft.edition ? ' <span class="repo-fixed">' + (esc(repo.draft.label) || 'new edition') + '</span>' : '')
      + '</b><small>unsaved</small></div>';
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
    + (cat.compare ? '← Back to list' : 'Compare') + '</button>'
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

  var header = '<div class="repo-h"><h2 id="repoTitle" class="dlg-shout">Repository</h2>'
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
    header += '<span class="repo-src">' + esc(shortDate(d.catalogue.modified)) + '</span>';
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
      + (!onRemoved && repo.draft && repo.draft.id
          ? '<button type="button" class="btn" data-repoedition="' + repo.draft.id + '"' + (repo.saving ? ' disabled' : '')
            + ' title="Another edition of ' + esc(repo.draft.name) + ', for particular portfolios">+ Add edition</button>'
          : '')
      + (!onRemoved && repo.draft && repo.draft.id && !isFixed(repo.category)
          ? (repo.confirmDelete
              ? '<span class="repo-src">It keeps its history and can be restored from the Archive.</span>'
                + '<button type="button" class="btn" data-repocanceldelete>Keep</button>'
                + '<button type="button" class="btn btn-danger" data-repodelete>Confirm archive</button>'
              : '<button type="button" class="btn btn-danger" data-repodelete' + (repo.saving ? ' disabled' : '') + '>Archive sleeve</button>')
          : '')
      + (onRemoved ? ''
          : '<button type="button" class="btn btn-primary" data-reposave' + (canSave ? '' : ' disabled') + '>'
            + (repo.saving ? 'Saving…' : (repo.draft && repo.draft.create ? 'Create sleeve'
               : (repo.draft && repo.draft.edition ? 'Create edition' : 'Save sleeve'))) + '</button>')
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
    + '[data-repocreate],[data-repocopy],[data-reporemove],[data-repoedition],[data-reporuleadd],[data-reporulerm],'
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
  if (e.key === 'Enter' && (e.target.id === 'repoName' || e.target.id === 'repoLabel')) { e.preventDefault(); saveDraft(); }
}, true);

App.addRenderer(renderEntryLinks);
App.openRepository = openRepository;
})();
