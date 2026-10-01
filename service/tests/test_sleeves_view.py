"""The Sleeves view as cards and a table (D156).

The view's pure functions run through node against fixture sleeves, the way
the catalogue's helpers and the overlay resolver are proved; the wiring that
keeps an unsaved draft safe is held by reading the source, because it lives
in event handlers a unit test cannot reach."""
import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(HERE, '..', '..', 'generator', 'js', 'repository.js')


def _source():
    with open(JS, encoding='utf-8') as fh:
        return fh.read()


def _body(source, name):
    start = source.index('function ' + name + '(')
    return source[start:source.index('\n}\n', start)]


def _helpers(source):
    start = source.index('/* sleeve-view-helpers-begin')
    return source[start:source.index('/* sleeve-view-helpers-end */')]


def _node(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    return json.loads(subprocess.run([node, '-e', script], capture_output=True, text=True, check=True).stdout)


SLEEVES = [
    {'id': 1, 'variant': 'T1', 'category': 'Equity', 'name': 'Passive', 'label': '', 'createdAt': '2026-09-02T19:17:38',
     'problems': [], 'products': [
         {'weight': 0.6, 'product': {'name': 'Alpha ETF', 'vehicle': 'ETF', 'productCost': 0.10}},
         {'weight': 0.4, 'product': {'name': 'Beta Fund', 'vehicle': 'Mutual Fund', 'productCost': 0.50}}]},
    {'id': 2, 'variant': 'T2', 'category': 'Equity', 'name': 'Active', 'label': 'GBP', 'createdAt': '2026-10-01T09:00:00',
     'problems': ['Weights sum to 90%.'], 'products': [
         {'weight': 0.9, 'product': {'name': 'Gamma SMA', 'vehicle': 'SMA', 'productCost': 0.80}}]},
    {'id': 3, 'variant': 'T1', 'category': 'Bonds', 'name': 'Treasuries', 'label': '', 'createdAt': None,
     'problems': [], 'products': [{'weight': 1.0, 'productId': 'gone', 'product': None}]},
    {'id': 4, 'variant': 'T1', 'category': 'Equity', 'name': 'Active', 'label': '', 'createdAt': '2026-09-03T10:00:00',
     'problems': [], 'products': [
         {'weight': 0.5, 'product': {'name': 'Gamma SMA', 'vehicle': 'SMA', 'productCost': 0.80}},
         {'weight': 0.5, 'product': {'name': 'Alpha ETF', 'vehicle': 'ETF', 'productCost': 0.10}}]},
]
ORDER = {'categories': ['Bonds', 'Equity'], 'variants': ['T1', 'T2']}


def test_the_views_pure_helpers_read_the_library_as_the_desk_does():
    """Weighted cost, vehicles, status, search, filters, sorting, the compare
    selection and the mapping between the two views' places (D156)."""
    got = _node(_helpers(_source()) + '''
const s = %s, order = %s;
const ids = rows => rows.map(r => r.id);
process.stdout.write(JSON.stringify({
  cost: s.map(svCost).map(v => v === null ? null : +v.toFixed(4)),
  costText: s.map(svCostText),
  unpriced: s.map(svUnpriced),
  vehicles: s.map(svVehicles),
  status: s.map(svStatus),
  byProduct: ids(svFilter(s, { query: 'gamma' })),
  byLabel: ids(svFilter(s, { query: 'gbp' })),
  byTypeAndCategory: ids(svFilter(s, { variant: 'T1', category: 'Equity', query: '' })),
  everything: ids(svFilter(s, { variant: '', category: '', query: '  ' })),
  library: ids(svSort(s, null, order)),
  categoryAZ: ids(svSort(s, { key: 'category', dir: 'asc' }, { categories: ['Equity', 'Bonds'], variants: order.variants })),
  categoryZA: ids(svSort(s, { key: 'category', dir: 'desc' }, order)),
  costDesc: ids(svSort(s, { key: 'cost', dir: 'desc' }, order)),
  costAsc: ids(svSort(s, { key: 'cost', dir: 'asc' }, order)),
  vehiclesAsc: ids(svSort(s, { key: 'vehicles', dir: 'asc' }, order)),
  vehiclesDesc: ids(svSort(s, { key: 'vehicles', dir: 'desc' }, order)),
  createdDesc: ids(svSort(s, { key: 'created', dir: 'desc' }, order)),
  name: ids(svSort(s, { key: 'name', dir: 'asc' }, order)),
  statusSort: ids(svSort(s, { key: 'status', dir: 'desc' }, order)),
  ticks: [svTick([], 1, true, 3), svTick([1, 2, 3], 4, true, 3), svTick([1, 2, 3], 2, false, 3), svTick([1], 1, true, 3)],
  editing: [svEditing(null, true, true), svEditing({ id: 5 }, false, false), svEditing({ id: 5 }, true, false),
            svEditing({ id: 5 }, false, true), svEditing({ id: null }, false, false)],
  toTable: [svToTable({ variant: 'T1', category: 'Equity', level: 0 }), svToTable({ variant: 'T1', category: 'Equity', level: 1 }),
            svToTable({ variant: 'T2', category: 'Bonds', level: 2 }), svToTable({ variant: 'T2', category: 'Bonds', level: 1, query: 'gam' })],
  toCards: [svToCards({ fv: '', fc: '', drawer: false }, null), svToCards({ fv: 'T2', fc: '', drawer: false }, null),
            svToCards({ fv: 'T1', fc: 'Bonds', drawer: false }, null),
            svToCards({ fv: '', fc: '', drawer: true }, { variant: 'T2', category: 'Equity' })],
  follow: [svFollow({ fv: 'T1', fc: 'Bonds' }, s[1]), svFollow({ fv: '', fc: '' }, s[1]), svFollow({ fv: 'T2', fc: '' }, s[1])],
  weights: ['12.5', ' 60 ', '60%%', '1,5', '5abc', '', '.5', '-3'].map(t => { const v = svWeight(t); return isFinite(v) ? v : null; }),
  plural: [svPlural(1, 'sleeve'), svPlural(2, 'sleeve'), svPlural(0, 'product')],
  sigSame: svDraftSig({ name: 'A ', note: '', label: '', rules: [], products: [{ productId: 'a', weightPct: 60 }] })
        === svDraftSig({ name: 'A', note: '', label: '', rules: [{}].slice(1), products: [{ productId: 'a', weightPct: 60.00000001 }] }),
  sigMoved: svDraftSig({ name: 'A', products: [{ productId: 'a', weightPct: 60 }] }) === svDraftSig({ name: 'A', products: [{ productId: 'a', weightPct: 61 }] }),
  sigBad: svDraftSig({ name: 'A', products: [{ productId: 'a', weightPct: NaN, weightText: '1,5' }] }) === svDraftSig({ name: 'A', products: [{ productId: 'a', weightPct: NaN, weightText: '' }] }),
}));
''' % (json.dumps(SLEEVES), json.dumps(ORDER)))
    # each product's own cost at its weight, in percent; an unpriced product is
    # left out of the figure and said to be (D156 review)
    assert got['cost'] == [0.26, 0.72, None, 0.45]
    assert got['unpriced'] == [0, 0, 1, 0]
    assert got['costText'] == ['0.26%', '0.72%', '— · 1 product unpriced', '0.45%']
    assert got['vehicles'] == [['ETF', 'Mutual Fund'], ['SMA'], [], ['SMA', 'ETF']]
    assert got['status'][0] == {'ok': True, 'label': 'Ready'}
    assert got['status'][1] == {'ok': False, 'label': 'Withheld · 1 problem'}
    # search reads names, edition labels and the products held
    assert got['byProduct'] == [2, 4]
    assert got['byLabel'] == [2]
    assert got['byTypeAndCategory'] == [1, 4]
    assert got['everything'] == [1, 2, 3, 4]
    # no key is the library's own order, the cards': category, type, name
    assert got['library'] == [3, 4, 1, 2]
    # a column sorts A to Z or Z to A by what it shows - not by library order
    assert got['categoryAZ'] == [3, 4, 1, 2], 'Bonds before Equity whatever the library order'
    assert got['categoryZA'] == [4, 1, 2, 3]
    # a blank sorts last either way
    assert got['costDesc'] == [2, 4, 1, 3]
    assert got['costAsc'] == [1, 4, 2, 3]
    assert got['vehiclesAsc'][-1] == 3 and got['vehiclesDesc'][-1] == 3, 'no vehicles sorts last both ways'
    assert got['createdDesc'] == [2, 4, 1, 3]
    assert got['name'] == [4, 2, 1, 3], 'by name, the edition label breaking the tie'
    assert got['statusSort'][0] == 2, 'withheld first when sorted that way'
    # the compare selection: three at most, unticking always works
    assert got['ticks'] == [[1], [1, 2, 3], [1, 3], [1]]
    # the page edits when asked, and always for a new or unsaved draft
    assert got['editing'] == [False, False, True, True, True]
    # the two views keep each other's place; a search keeps every type and category
    assert got['toTable'] == [{'fv': 'T1', 'fc': '', 'drawer': False}, {'fv': 'T1', 'fc': 'Equity', 'drawer': False},
                              {'fv': 'T2', 'fc': 'Bonds', 'drawer': True}, {'fv': '', 'fc': '', 'drawer': False}]
    assert got['toCards'] == [{'level': 0}, {'level': 0, 'variant': 'T2'},
                              {'level': 1, 'variant': 'T1', 'category': 'Bonds'},
                              {'variant': 'T2', 'category': 'Equity', 'level': 2}]
    # a sleeve arriving from elsewhere brings the table's filters with it
    assert got['follow'] == [{'fv': 'T2', 'fc': 'Equity'}, {'fv': '', 'fc': ''}, {'fv': 'T2', 'fc': ''}]
    # a weight is a plain number or nothing: '1,5' is not 15
    assert got['weights'] == [12.5, 60, 60, None, None, None, 0.5, None]
    assert got['plural'] == ['1 sleeve', '2 sleeves', '0 products']
    # dirty means changed: retyping a weight, trailing spaces and empty rules are not changes
    assert got['sigSame'] is True and got['sigMoved'] is False and got['sigBad'] is False


def test_every_move_away_from_an_open_sleeve_asks_first():
    """The Keep editing / Discard notice guarded the three panes; it guards
    both views. Opening a sleeve, closing the drawer, the breadcrumb, the
    tiles, the type switch and New sleeve all go through goTo - and switching
    view is not a move, so it never loads a draft over the one in hand."""
    source = _source()
    for name in ('svOpen', 'svCloseDrawer'):
        assert 'goTo(' in _body(source, name), name
    switch = _body(source, 'svSwitchMode')
    assert 'goTo(' not in switch and 'loadDraft(' not in switch, 'the draft carries across a switch'
    click = source[source.index("if (ds.svmode !== undefined)"):source.index("if (ds.repocopy !== undefined)")]
    for handler in ('ds.repovariant', 'ds.svtile', 'ds.svcrumb', 'ds.reponew'):
        segment = click[click.index(handler):]
        segment = segment[:segment.index('return;')]
        assert 'goTo(' in segment, handler
    # a held move keeps where it meant to put focus; keeping the edit returns to it
    leaving = _body(source, 'resolveLeaving')
    assert 'sv.pending' in leaving and 'sv.editFocus' in leaving and "[data-reposave]" in leaving, \
        'Keep editing goes back to the field, never onto Discard (D156 QA 9)'
    # a sleeve loaded opens to be read
    assert 'sv.editing = false' in _body(source, 'loadDraft')


def test_the_view_remembers_itself_and_says_things_in_words():
    """The switch is remembered per browser, behind try/catch for a private
    window; there is one way to start a sleeve; the footer names the library,
    not its file; and the catalogue's link into a sleeve now goes there."""
    source = _source()
    reads = re.findall(r"try \{ if \(window\.localStorage\.getItem\(SV_VIEW_KEY\)", source)
    assert len(reads) == 1
    assert "try { window.localStorage.setItem(SV_VIEW_KEY, to); } catch" in _body(source, 'svSwitchMode')
    assert 'data-repocreate' not in source, 'the second create button is gone (B2)'
    assert source.count('class="btn btn-primary sv-new" data-reponew') == 1, 'one button'
    assert 'store.path' not in source, 'the footer says what the library holds, not where the file is'
    assert 'ds.catopen !== undefined' in source
    # the drawer keeps the keyboard: what is behind it is inert
    assert "' inert'" in _body(source, 'svTableViewHtml')
    assert "setAttribute('inert'" in _body(source, 'svAfterRender')


# ---- the save that names the version it started from (D156) ---------------

def test_a_save_over_someone_elses_newer_one_is_refused_not_lost():
    """Two tabs on one sleeve used to end with whichever saved last, silently.
    A save now names the revision count the editor opened; a sleeve saved by
    someone else since then is refused with 409, and nothing is written."""
    import json as _json
    from test_sleeve_repository import _caller, A_PRODUCT, ANOTHER
    from cyrus_pmg.pmgService import dashboardRouter
    from cyrus_pmg.pmgService.scenario import sleeveRepo
    from cyrus_pmg.pmgService.scenario.types import ValidationError
    made = sleeveRepo.createSleeve('PMG ESG', 'Public Equity', 'Two Tabs',
                                   [{'productId': A_PRODUCT, 'weight': 0.6},
                                    {'productId': ANOTHER, 'weight': 0.4}], user='alice')
    try:
        opened = made['revisions']
        assert opened == 1
        # the other tab saves first
        sleeveRepo.updateSleeve(made['id'], 'Two Tabs', [{'productId': A_PRODUCT, 'weight': 1.0}],
                                user='bob', base=opened)
        # this tab's save, from the version it opened, is refused
        with pytest.raises(sleeveRepo.StaleError) as exc:
            sleeveRepo.updateSleeve(made['id'], 'Two Tabs', [{'productId': ANOTHER, 'weight': 1.0}],
                                    user='alice', base=opened)
        assert exc.value.field == 'base'
        assert 'bob' in exc.value.message and 'revision 2' in exc.value.message
        now = [s for s in sleeveRepo.listAll() if s['id'] == made['id']][0]
        assert [p['productId'] for p in now['products']] == [A_PRODUCT], 'bob\'s save stands'
        assert now['revisions'] == 2, 'nothing was written'
        # through the route: 409, and a revert from a stale view too
        resp = dashboardRouter.updateRepositorySleeve(
            made['id'], {'name': 'Two Tabs', 'products': [{'productId': ANOTHER, 'weight': 1}], 'base': 1},
            caller=_caller('alice'))
        assert resp.status_code == 409 and _json.loads(resp.body)['field'] == 'base'
        resp = dashboardRouter.revertRepositorySleeve(made['id'], {'revision': 1, 'base': 1},
                                                      caller=_caller('alice'))
        assert resp.status_code == 409
        # from the current version both go through
        ok = dashboardRouter.updateRepositorySleeve(
            made['id'], {'name': 'Two Tabs', 'products': [{'productId': ANOTHER, 'weight': 1}], 'base': 2},
            caller=_caller('alice'))
        assert ok['sleeve']['revisions'] == 3
        back = dashboardRouter.revertRepositorySleeve(made['id'], {'revision': 1, 'base': 3},
                                                      caller=_caller('alice'))
        assert back['sleeve']['revisions'] == 4
        # a caller that does not say keeps the old behaviour; nonsense is refused
        sleeveRepo.updateSleeve(made['id'], 'Two Tabs', [{'productId': A_PRODUCT, 'weight': 1.0}], user='alice')
        for bad in (True, -1, '2', 1.5):
            with pytest.raises(ValidationError) as exc:
                sleeveRepo.updateSleeve(made['id'], 'Two Tabs', [{'productId': A_PRODUCT, 'weight': 1.0}],
                                        user='alice', base=bad)
            assert exc.value.field == 'base' and not isinstance(exc.value, sleeveRepo.StaleError)
    finally:
        sleeveRepo.deleteSleeve(made['id'], user='alice')


# ---- the New sleeve form (D157) ---------------------------------------------

LIB = [
    {'id': 10, 'variant': 'T1', 'category': 'Equity', 'name': 'Passive', 'label': '', 'products': [
        {'productId': 'a', 'weight': 0.6, 'product': {'name': 'Alpha ETF'}},
        {'productId': 'b', 'weight': 0.4, 'product': {'name': 'Beta Fund'}}]},
    {'id': 11, 'variant': 'T2', 'category': 'Equity', 'name': 'Passive', 'label': '', 'products': [
        {'productId': 'a', 'weight': 0.6, 'product': {'name': 'Alpha ETF'}},
        {'productId': 'b', 'weight': 0.4, 'product': {'name': 'Beta Fund'}}]},
    {'id': 12, 'variant': 'T2', 'category': 'Equity', 'name': 'Active', 'label': 'GBP', 'products': [
        {'productId': 'c', 'weight': 1.0, 'product': {'name': 'Gamma SMA'}}]},
    {'id': 13, 'variant': 'T1', 'category': 'Overlay', 'name': 'Tilts', 'label': '', 'products': [
        {'productId': 'd', 'weight': 1.0, 'product': {'name': 'Tilt Fund'}}]},
]
CATALOGUE = [
    {'productId': 'a', 'name': 'Alpha ETF', 'assetClass': 'US Large Cap', 'vehicle': 'ETF', 'style': 'Passive', 'source': 'Internal',
     'productCost': 0.10, 'minimumInvestment': None, 'ticker': 'ALF'},
    {'productId': 'b', 'name': 'Beta Fund', 'assetClass': 'Intl', 'vehicle': 'Mutual Fund', 'style': 'Active', 'source': 'External',
     'productCost': 0.50, 'minimumInvestment': 1000},
    {'productId': 'c', 'name': 'Gamma SMA', 'assetClass': 'US Large Cap', 'vehicle': 'SMA', 'style': 'Active', 'source': 'Internal',
     'productCost': None, 'minimumInvestment': 5000000},
    {'productId': 'd', 'name': 'Tilt Fund', 'assetClass': 'Tilts', 'vehicle': 'Mutual Fund', 'style': 'Active', 'source': 'Internal',
     'productCost': 0.85, 'minimumInvestment': 1000},
]


def test_the_new_sleeve_forms_pure_helpers():
    """Availability per type, the source list and its change marks, the
    replace-or-keep decision, the summary's outcomes, the weighted cost with
    unpriced products said, Spread evenly, and the Add a product dialog's
    pool, filters, sorting and adding (D157)."""
    got = _node(_helpers(_source()) + '''
const lib = %s, cat = %s, fixed = ['Overlay'];
const byId = {}; cat.forEach(p => { byId[p.productId] = p; });
const ids = rows => rows.map(p => p.productId);
const src = lib[0];
const copied = src.products.map(r => ({ productId: r.productId, weightPct: svPctOf(r.weight) }));
const edited = [{ productId: 'a', weightPct: 50 }, { productId: 'c', weightPct: NaN, weightText: '' }];
process.stdout.write(JSON.stringify({
  noCategory: svAvail(lib, fixed, 'T1', null, 'Passive'),
  free: svAvail(lib, fixed, 'T2', 'Overlay', ''),
  full: svAvail(lib, fixed, 'T1', 'Overlay', 'Anything'),
  taken: svAvail(lib, fixed, 'T1', 'Equity', '  passive '),
  fine: svAvail(lib, fixed, 'T1', 'Equity', 'Passive Core'),
  catFull: [svCategoryFull(lib, fixed, ['T1'], 'Overlay'), svCategoryFull(lib, fixed, ['T1', 'T2'], 'Overlay'), svCategoryFull(lib, fixed, ['T1'], 'Equity')],
  outcomes: svOutcomes(lib, fixed, ['T1', 'T2', 'T3'], ['T1', 'T3'], 'Equity', 'Passive').map(o => [o.variant, o.chosen, o.ok, o.kind]),
  sourcesChosen: svSources(lib, { category: 'Equity', variants: ['T1'], order: ['T1', 'T2'] }).map(e => [e.s.id, e.types, e.ids]),
  sourcesAll: svSources(lib, { category: 'Equity', variants: ['T1'], allV: true, order: ['T1', 'T2'] }).map(e => [e.s.id, e.types, e.ids]),
  sourcesQuery: svSources(lib, { category: 'Equity', variants: [], query: 'gamma', order: ['T1', 'T2'] }).map(e => e.s.id),
  sourcesLabel: svSources(lib, { category: 'Equity', variants: [], query: 'gbp', order: ['T1', 'T2'] }).map(e => e.s.id),
  marksCopied: copied.map(r => svSourceMark(src, r)),
  marksEdited: edited.map(r => svSourceMark(src, r)),
  removed: svSourceRemoved(src, edited).map(r => r.productId),
  changes: [svSourceChanges(src, copied), svSourceChanges(src, edited), svSourceChanges(null, edited)],
  picks: [svPickAction(0, true, 10, null), svPickAction(2, true, 12, 10), svPickAction(2, false, 12, 10),
          svPickAction(2, false, 10, 10), svPickAction(2, false, 12, null)],
  cost: svRowsCost(copied, byId), costText: [svRowsCostText(copied, byId), svRowsCostText(edited, byId), svRowsCostText([], byId)],
  spread: [svSpread(3), svSpread(1), svSpread(0), svSpread(7).reduce((a, b) => a + b, 0)],
  pool: svCategoryProducts(lib, 'Equity'),
  hitsPoolCost: ids(svAddHits(cat, { pool: svCategoryProducts(lib, 'Equity') })),
  hitsAllCostDesc: ids(svAddHits(cat, { sort: 'cost', dir: 'desc' })),
  hitsName: ids(svAddHits(cat, { sort: 'name' })),
  hitsMin: ids(svAddHits(cat, { sort: 'min' })),
  hitsQuery: [ids(svAddHits(cat, { query: 'large cap' })), ids(svAddHits(cat, { query: 'alf' })), ids(svAddHits(cat, { query: 'tilt', vehicle: 'Mutual Fund' }))],
  hitsFilters: [ids(svAddHits(cat, { vehicle: 'SMA' })), ids(svAddHits(cat, { style: 'Active', source: 'Internal' }))],
  added: svAddRows(copied, ['b', 'c', 'd', 'c']).map(r => [r.productId, isFinite(r.weightPct) ? r.weightPct : null]),
}));
''' % (json.dumps(LIB), json.dumps(CATALOGUE)))
    # where a sleeve can be made: no category yet is no verdict; a fixed category
    # holding its sleeve is full; a name the type already has there is that sleeve
    assert got['noCategory'] == {'ok': True}
    assert got['free'] == {'ok': True}
    assert got['full']['ok'] is False and got['full']['kind'] == 'full' and 'Tilts' in got['full']['reason']
    assert got['taken']['kind'] == 'taken'
    assert 'open Passive and add an edition' in got['taken']['reason'], 'a clash points at the edition route, never makes one'
    assert got['taken']['short'] == 'Already has a sleeve called Passive here'
    assert got['fine'] == {'ok': True}
    assert got['catFull'] == [True, False, False]
    assert got['outcomes'] == [['T1', True, False, 'taken'], ['T2', False, False, 'taken'], ['T3', True, True, None]]
    # the source list: the chosen types, an identical copy under another type
    # listed once with both, editions included and searchable by label and product
    assert got['sourcesChosen'] == [[10, ['T1'], [10]]]
    assert got['sourcesAll'] == [[10, ['T1', 'T2'], [10, 11]], [12, ['T2'], [12]]]
    assert got['sourcesQuery'] == [12] and got['sourcesLabel'] == [12]
    # every change from the source is marked; a copy untouched has none
    assert got['marksCopied'] == ['', '']
    assert got['marksEdited'] == ['was 60.00%', 'added']
    assert got['removed'] == ['b']
    assert got['changes'] == [0, 3, 0]
    # picking a source: copies onto nothing or an untouched copy, asks over the desk's own work
    assert got['picks'] == ['copy', 'copy', 'ask', 'same', 'ask']
    # weighted cost of the products the catalogue prices, the rest said
    assert round(got['cost']['cost'], 6) == 0.26 and got['cost']['unpriced'] == 0
    assert got['costText'] == ['0.26%', '0.05% · 1 product unpriced', '—']
    assert got['spread'][:3] == [[33.33, 33.33, 33.34], [100], []] and round(got['spread'][3], 6) == 100
    # the dialog: the category's own products by default, cheapest first, a blank last
    assert got['pool'] == ['a', 'b', 'c']
    assert got['hitsPoolCost'] == ['a', 'b', 'c']
    assert got['hitsAllCostDesc'] == ['d', 'b', 'a', 'c'], 'unpriced last whichever way'
    assert got['hitsName'] == ['a', 'b', 'c', 'd']
    assert got['hitsMin'] == ['b', 'd', 'c', 'a']
    assert got['hitsQuery'] == [['a', 'c'], ['a'], ['d']]
    assert got['hitsFilters'] == [['c'], ['d', 'c']]
    # adding appends without a weight, and never twice
    assert got['added'] == [['a', 60.0], ['b', 40.0], ['c', None], ['d', None]]


def test_the_new_sleeve_form_is_wired_as_the_mock_says():
    """D157: the category is the desk's to choose (no first-with-room), the
    start mode is remembered behind try/catch, a name clash is never turned
    into an edition, and the form's moves go through the unsaved guard."""
    source = _source()
    new_draft = _body(source, 'newDraft')
    assert 'svFirstRoom' not in new_draft and 'create.category || null' in new_draft
    click = source[source.index("if (ds.reponew !== undefined)"):]
    click = click[:click.index('return;')]
    assert 'goTo(' in click and 'svFirstRoom' not in click
    assert "try { return window.localStorage.getItem(NC_START_KEY)" in _body(source, 'ncStartDefault')
    assert "try { window.localStorage.setItem(NC_START_KEY, to); } catch" in _body(source, 'ncSetStart')
    cancel = _body(source, 'ncCancel')
    assert 'goTo(' in cancel, 'Cancel with changes asks first'
    save = _body(source, 'saveDraft')
    assert "payload.category = d.create ? d.category" in save and 'label: d.label.trim()' in save
    problems = _body(source, 'draftProblems')
    assert 'ncProblems(d)' in problems
    # the dialog adds through the pure helper, and the old picker never opens on a new sleeve
    assert 'svAddRows(' in _body(source, 'ncDlgAdd')
    assert 'data-repoadd' not in _body(source, 'svCreateHtml') and 'data-repopick' not in _body(source, 'ncRowHtml')


def test_a_name_is_one_name_whatever_its_spacing_or_capitals():
    """D157 review: "Active  Passive" (two spaces) was saved as a second
    sleeve that reads exactly like the first. The server stores names with
    one space between words, and refuses a name that differs from one the
    type already has in the category only in capitals or spacing - naming
    the spelling in use. Editions of one name are untouched."""
    from test_sleeve_repository import A_PRODUCT, ANOTHER
    from cyrus_pmg.pmgService.scenario import sleeveRepo
    from cyrus_pmg.pmgService.scenario.types import ValidationError
    rows = [{'productId': A_PRODUCT, 'weight': 0.6}, {'productId': ANOTHER, 'weight': 0.4}]
    made = sleeveRepo.createSleeve('PMG ESG', 'Public Equity', '  Spacing   Test  ', rows, user='alice')
    try:
        assert made['name'] == 'Spacing Test'
        for clash in ('Spacing  Test', 'spacing test', ' SPACING TEST'):
            with pytest.raises(ValidationError) as exc:
                sleeveRepo.createSleeve('PMG ESG', 'Public Equity', clash, rows, user='alice')
            assert exc.value.field == 'name' and 'Spacing Test' in exc.value.message, clash
        # under another type, or in another category, it is free
        other = sleeveRepo.createSleeve('US Onshore', 'Public Equity', 'spacing test', rows, user='alice')
        sleeveRepo.deleteSleeve(other['id'], user='alice')
        # an exact-name edition is still an edition, not a clash
        gbp = sleeveRepo.addEdition(made['id'], 'GBP', [{'currency': ['GBP']}], rows, user='alice')
        sleeveRepo.deleteSleeve(gbp['id'], user='alice')
        # a total off by a thousandth says so, not "100.00%"
        with pytest.raises(ValidationError) as exc:
            sleeveRepo.createSleeve('PMG ESG', 'Public Equity', 'Thirds', [
                {'productId': A_PRODUCT, 'weight': 0.33333}, {'productId': ANOTHER, 'weight': 0.66666}], user='alice')
        assert '99.999%' in exc.value.message
    finally:
        sleeveRepo.deleteSleeve(made['id'], user='alice')


def test_the_new_sleeve_forms_review_fixes_in_its_pure_helpers():
    """D157 review: names are one name whatever their spacing; a total is
    judged at the server's tolerance and shown in enough places to see why;
    a weight says what is wrong with it; possessives and lists read as
    English; a source is matched whatever order its rows were saved in;
    the same name under other types is told apart from a case-only match;
    and no cost is shown before any weight."""
    got = _node(_helpers(_source()) + '''
const lib = %s, byId = {};
%s.forEach(p => { byId[p.productId] = p; });
const thirds = [{ productId: 'a', weightPct: 33.333 }, { productId: 'b', weightPct: 33.333 }, { productId: 'c', weightPct: 33.333 }];
process.stdout.write(JSON.stringify({
  norm: [svNormName('  Active   Passive '), svNormName(null), svSameName('Active  Passive', 'active passive')],
  ok: [svWeightsOk(100), svWeightsOk(100.00009), svWeightsOk(100.004), svWeightsOk(99.999)],
  text: [svTotalText(99.999), svTotalText(100.004), svTotalText(60), svTotalText(100.00001), svTotalText(33.333 * 3), svTotalText(NaN)],
  spread: svSpread(3).reduce((a, b) => a + b, 0),
  notes: ['12.5', '', '-5', '0', '1,5', ' - 2 '].map(svWeightNote),
  poss: [svPossessive('Managers'), svPossessive('Passive Core')],
  join: [svJoinAnd(['A']), svJoinAnd(['A', 'B']), svJoinAnd(['A', 'B', 'C'])],
  match: [svRowsMatch([{ productId: 'b', weightPct: 40 }, { productId: 'a', weightPct: 60 }], lib[0]),
          svRowsMatch([{ productId: 'a', weightPct: 60 }], lib[0]),
          svRowsMatch([{ productId: 'a', weightPct: 60 }, { productId: 'b', weightPct: 41 }], lib[0])],
  keyOrder: svSourceKey({ name: 'X', products: [{ productId: 'a', weight: 0.6 }, { productId: 'b', weight: 0.4 }] })
          === svSourceKey({ name: 'X', products: [{ productId: 'b', weight: 0.4 }, { productId: 'a', weight: 0.6 }] }),
  elsewhere: [svNameElsewhere(lib, ['T1', 'T2', 'T3'], ['T3'], 'Equity', ' passive'),
              svNameElsewhere(lib, ['T1', 'T2', 'T3'], ['T3'], 'Equity', 'Passive'),
              svNameElsewhere(lib, ['T1', 'T2'], ['T1', 'T2'], 'Equity', 'Passive')],
  noCost: svRowsCostText([{ productId: 'a', weightPct: NaN }, { productId: 'b', weightPct: NaN }], byId),
  takenSpaced: svAvail(lib, [], 'T1', 'Equity', 'Pass   ive').ok === true && svAvail(lib, [], 'T1', 'Equity', ' PASSIVE ').kind === 'taken',
}));
''' % (json.dumps(LIB), json.dumps(CATALOGUE)))
    assert got['norm'] == ['Active Passive', '', True]
    # the server's tolerance: 1e-6 of the whole, 1e-4 of a percent
    assert got['ok'] == [True, True, False, False]
    assert got['text'] == ['99.999', '100.004', '60.00', '100.00', '99.999', '—']
    assert round(got['spread'], 9) == 100
    assert got['notes'] == ['', '', 'Must be above zero', 'Must be above zero', 'Not a plain number', 'Must be above zero']
    assert got['poss'] == ['Managers’', 'Passive Core’s']
    assert got['join'] == ['A', 'A and B', 'A, B and C']
    assert got['match'] == [True, False, False]
    assert got['keyOrder'] is True
    assert got['elsewhere'][0] == [{'variant': 'T1', 'name': 'Passive', 'kind': 'case'}, {'variant': 'T2', 'name': 'Passive', 'kind': 'case'}]
    assert got['elsewhere'][1] == [{'variant': 'T1', 'name': 'Passive', 'kind': 'same'}, {'variant': 'T2', 'name': 'Passive', 'kind': 'same'}]
    assert got['elsewhere'][2] == []
    assert got['noCost'] == '—'
    assert got['takenSpaced'] is True


def test_the_new_sleeve_forms_state_never_outlives_the_form():
    """D157 review: the form's own state (an open dialog, a question it was
    asking) is cleared with every draft loaded and when the console closes,
    and the keys it listens for are listened for only while it is open."""
    source = _source()
    assert 'ncReset();' in _body(source, 'loadDraft')
    assert 'ncReset();' in _body(source, 'closeRepository')
    keys = source[source.index("if (repo.view === 'sleeves' && nc.dlg"):]
    assert keys.startswith("if (repo.view === 'sleeves' && nc.dlg && ncOpen())")
    assert "nc.ask != null && ncOpen()" in source and "ncOpen() && e.target && e.target.id === 'ncSrcQ'" in source
    # a save in flight cannot be discarded or closed under it
    assert 'if (repo.saving) return;' in _body(source, 'ncCancel')
    # the name sent is the name as stored
    assert 'name: svNormName(d.name)' in _body(source, 'saveDraft')
