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
