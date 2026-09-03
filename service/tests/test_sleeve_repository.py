"""The sleeve repository and the product catalogue (D56, D57).

The store under test is the per-session temporary database conftest points
SCENARIO_SLEEVES_DB at, seeded from the packaged extract exactly as a first
run would be. Tests that write clean up after themselves.
"""

import csv
import os
import sqlite3

import pytest
from starlette.requests import Request

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.core import accessControl
from cyrus_pmg.pmgService.scenario import (fees, products, rules, sleeveRepo, sleeveTools,
                                           sleeves)
from cyrus_pmg.pmgService.scenario.types import ValidationError

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.join(HERE, '..', '..', 'sleeveSource', 'sleeves.csv')
CATALOGUE = os.path.join(HERE, '..', '..', 'productSource', 'products.csv')

A_PRODUCT = 'gs-us-corporate-bond-fund'
ANOTHER = 'ashfield-global-credit-fund'


def _aPlacedProduct():
    """A product some sleeve actually holds. Chosen from the library rather
    than named, because which products are placed is the seed's business and
    the seed is data - it changed once already (D9's authored sleeves for
    PMG's own names) and will change again when PMG's real library lands."""
    for entry in sleeveRepo.listAll():
        for row in entry['products']:
            return row['productId']
    raise AssertionError('the library is empty')


def _seedRows():
    with open(SEED, newline='', encoding='utf-8') as fh:
        reader = csv.reader(fh)
        next(reader)
        return [(v, c, s, p, float(w)) for v, c, s, p, w in reader]


def _request(kerberos=None):
    headers = [(b'x-kerberos', kerberos.encode())] if kerberos else []
    return Request({'type': 'http', 'method': 'GET', 'path': '/', 'query_string': b'',
                    'headers': headers})


# ---- the catalogue (D56) -----------------------------------------------------

def test_catalogue_serves_the_delivered_table_in_the_product_shape():
    """One row per product, every field a sleeve product has always carried,
    the fee left out and the group in its place; an empty ticker is the dash
    the table has always shown."""
    rows = products.all()
    assert len(rows) == 73
    expected = {'productId', 'name', 'ticker', 'assetClass', 'style', 'vehicle', 'source',
                'liquidity', 'exposureCurrency', 'productCost', 'feeGroup',
                'distributionYield', 'minimumInvestment'}
    for p in rows:
        assert set(p) == expected, p['productId']
        assert 'managementFee' not in p
        assert p['feeGroup'] in fees.FEE_GROUPS
        assert p['productCost'] >= 0
    sma = products.get('gsam-core-municipal-sma')
    assert sma['ticker'] == '—'
    # the two figures the catalogue compares on (D63): a number, or None
    # where the delivery leaves the cell blank - an ETF has no minimum
    assert isinstance(sma['distributionYield'], float) and sma['minimumInvestment'] == 5_000_000.0
    assert products.get('gs-access-ig-corporate-etf')['minimumInvestment'] is None
    assert products.get('nope') is None and not products.has('nope')
    assert products.describeSource()['products'] == 73


def test_an_extract_without_the_optional_figures_still_loads(tmp_path, monkeypatch):
    """The two figures are optional in the file (D63): a delivery from before
    they existed loads, and serves them as None rather than refusing."""
    with open(CATALOGUE, newline='', encoding='utf-8') as fh:
        rows = list(csv.reader(fh))
    keep = [i for i, h in enumerate(rows[0]) if h not in ('DistributionYield', 'MinimumInvestment')]
    path = tmp_path / 'products.csv'
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        csv.writer(fh).writerows([[r[i] for i in keep] for r in rows])
    monkeypatch.setenv('SCENARIO_PRODUCTS_SOURCE', str(path))
    products.reload()
    try:
        p = products.get('gsam-core-municipal-sma')
        assert p['distributionYield'] is None and p['minimumInvestment'] is None
        assert len(products.all()) == 73
    finally:
        monkeypatch.delenv('SCENARIO_PRODUCTS_SOURCE')
        products.reload()


@pytest.mark.parametrize('bad, message', [
    ('FeeGroup', 'fee group'),
    ('ProductCost', 'not a number'),
    ('ProductId', 'duplicate'),
    ('DistributionYield', 'not a number'),
    ('MinimumInvestment', 'negative'),
])
def test_catalogue_rejects_a_bad_extract_at_load(tmp_path, monkeypatch, bad, message):
    """A wrong fee group would otherwise fail at export under RDR; a bad
    cost or a repeated id would corrupt every sleeve that used it. All are
    refused before anything is served."""
    with open(CATALOGUE, newline='', encoding='utf-8') as fh:
        rows = list(csv.reader(fh))
    head, body = rows[0], rows[1:]
    col = head.index(bad)
    if bad == 'ProductId':
        body[1][col] = body[0][col]
    elif bad == 'FeeGroup':
        body[0][col] = 'Made Up'
    elif bad == 'MinimumInvestment':
        body[0][col] = '-1'
    else:
        body[0][col] = 'free'
    path = tmp_path / 'products.csv'
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        csv.writer(fh).writerows([head] + body)
    monkeypatch.setenv('SCENARIO_PRODUCTS_SOURCE', str(path))
    products.reload()
    try:
        with pytest.raises(products.BadCatalogue) as exc:
            products.all()
        assert message in str(exc.value)
    finally:
        monkeypatch.delenv('SCENARIO_PRODUCTS_SOURCE')
        products.reload()


# ---- the seed and the served shape (D57) --------------------------------------

def test_seed_equals_the_extract_sleeve_for_sleeve_and_weight_for_weight():
    """The parity that lets the rest of the suite pass unchanged: what the
    repository serves is what the tables used to say."""
    want = {}
    for v, c, s, p, w in _seedRows():
        want.setdefault((v, c, s), {})[p] = w
    got = {}
    for variant in sleeves.VARIANTS:
        for category in sleeveRepo.categories():
            for sleeve in sleeves.listSleeves(category, variant):
                for product in sleeve['products']:
                    got.setdefault((variant, category, sleeve['name']), {})[product['productId']] = product['weight']
    assert got == want
    assert len(got) == 102
    counts = {}
    for (v, _, _) in got:
        counts[v] = counts.get(v, 0) + 1
    assert counts == {'PMG Multi-Asset Portfolio': 22, 'PMG ESG': 31,
                      'US Onshore': 26, 'Irish Onshore': 23}
    # every book can be completed: no category is empty under any type
    for variant in sleeves.VARIANTS:
        for category in sleeveRepo.categories():
            assert sleeves.listSleeves(category, variant), (variant, category)


def test_served_sleeve_keeps_the_shape_every_consumer_reads():
    sleeve = sleeves.listSleeves('Investment Grade Fixed Income', sleeves.VARIANTS[0])[0]
    assert set(sleeve) == {'id', 'name', 'note', 'products'}
    assert abs(sum(p['weight'] for p in sleeve['products']) - 1.0) < 1e-9
    for p in sleeve['products']:
        assert 'managementFee' not in p and 'feeGroup' in p and 'weight' in p
    assert sleeves.sleeveExists('Investment Grade Fixed Income', sleeve['name'], sleeves.VARIANTS[0])
    assert not sleeves.sleeveExists('Investment Grade Fixed Income', sleeve['name'], 'Not A Type')
    assert sleeves.listSleeves('Public Equity', 'Not A Type') == []


def test_categories_follow_the_implementation_table_order():
    """The universe's categories, the volatility premium's after the category
    that funds it, the tilt's last - the order the table renders them in."""
    cats = sleeveRepo.categories()
    universe = rules.categoriesInUniverseOrder()
    assert cats.index(rules.VOL_PREMIUM_CATEGORY) == cats.index(rules.VOL_PREMIUM_FUNDED_FROM) + 1
    assert cats[-1] == rules.TACTICAL_TILT_CATEGORY
    # the universe's order, with grouped categories collapsed to their one
    # name and appearing where the first of them did (D60)
    seen = []
    for c in universe:
        under = rules.sleeveCategory(c)
        if under not in seen:
            seen.append(under)
    assert [c for c in cats if c in seen] == seen
    assert set(sleeveRepo.fixedCategories()) == set(rules.AUTO_SLEEVE_CATEGORIES)
    # a grouped category is never offered on its own
    for group in rules.SLEEVE_GROUPS:
        assert group['name'] in cats
        for member in group['categories']:
            assert member not in cats


def test_every_fixed_category_holds_exactly_one_sleeve_under_every_type():
    for variant in sleeves.VARIANTS:
        for category in sleeveRepo.fixedCategories():
            assert len(sleeves.listSleeves(category, variant)) == 1, (variant, category)
    assert sleeveRepo.census()['fixedCategoryProblems'] == []


# ---- writing -----------------------------------------------------------------

def test_create_update_delete_round_trip_with_provenance():
    variant, category = 'PMG ESG', 'Public Equity'
    before = [s['name'] for s in sleeves.listSleeves(category, variant)]
    made = sleeveRepo.createSleeve(
        variant, category, '  Round Trip  ',
        [{'productId': A_PRODUCT, 'weight': 0.6}, {'productId': ANOTHER, 'weight': 0.4}],
        note='temporary', user='alice')
    try:
        assert made['name'] == 'Round Trip' and made['createdBy'] == 'alice'
        assert made['problems'] == [] and made['fixed'] is False
        assert [p['productId'] for p in made['products']] == [A_PRODUCT, ANOTHER]
        assert made['products'][0]['product']['name'] == 'GS US Corporate Bond Fund'
        served = [s['name'] for s in sleeves.listSleeves(category, variant)]
        assert served == before + ['Round Trip']

        upd = sleeveRepo.updateSleeve(made['id'], 'Round Trip 2',
                                      [{'productId': ANOTHER, 'weight': 1.0}], user='bob')
        assert upd['name'] == 'Round Trip 2' and upd['updatedBy'] == 'bob'
        assert upd['createdBy'] == 'alice'
        assert len(upd['products']) == 1
        assert sleeveRepo.getSleeve(made['id'])['name'] == 'Round Trip 2'
    finally:
        gone = sleeveRepo.deleteSleeve(made['id'], user='alice')
    assert gone['name'] == 'Round Trip 2'
    assert [s['name'] for s in sleeves.listSleeves(category, variant)] == before
    # out of the library, still on the record (D65)
    kept = sleeveRepo.getSleeve(made['id'])
    assert kept is not None and kept['archived'] is True and kept['archivedBy'] == 'alice'
    assert made['id'] not in [e['id'] for e in sleeveRepo.listAll()]
    assert made['id'] in [e['id'] for e in sleeveRepo.listArchived()]


@pytest.mark.parametrize('field, kwargs', [
    ('variant',  dict(variant='Not A Type')),
    ('category', dict(category='Cash')),
    ('name',     dict(name='   ')),
    ('name',     dict(name='x' * 81)),
    ('name',     dict(name='Passive')),                               # already there
    ('products', dict(products=[])),
    ('products', dict(products=[{'productId': 'nope', 'weight': 1}])),
    ('products', dict(products=[{'productId': A_PRODUCT, 'weight': .5},
                                {'productId': A_PRODUCT, 'weight': .5}])),
    ('weights',  dict(products=[{'productId': A_PRODUCT, 'weight': .98}])),
    ('weights',  dict(products=[{'productId': A_PRODUCT, 'weight': 'lots'}])),
    ('weights',  dict(products=[{'productId': A_PRODUCT, 'weight': 0},
                                {'productId': ANOTHER, 'weight': 1}])),
    ('category', dict(category='Hybrid Fixed Income')),                # fixed: already has its one
])
def test_every_rule_refuses_naming_its_field(field, kwargs):
    args = dict(variant='PMG ESG', category='Public Equity', name='Should Not Save',
                products=[{'productId': A_PRODUCT, 'weight': 1.0}])
    args.update(kwargs)
    total = sleeveRepo.census()['total']
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.createSleeve(args['variant'], args['category'], args['name'], args['products'])
    assert exc.value.field == field
    assert sleeveRepo.census()['total'] == total, 'nothing may be written on refusal'


def test_a_fixed_category_sleeve_can_be_edited_but_not_deleted():
    variant = sleeves.VARIANTS[0]
    entry = [e for e in sleeveRepo.listAll(variant)
             if e['category'] == rules.VOL_PREMIUM_CATEGORY][0]
    assert entry['fixed'] is True
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.deleteSleeve(entry['id'])
    assert exc.value.field == 'category'
    # edits are fine - the products and weights are the admin's to maintain
    original = [{'productId': p['productId'], 'weight': p['weight']} for p in entry['products']]
    upd = sleeveRepo.updateSleeve(entry['id'], entry['name'], original, note='checked', user='alice')
    assert upd['note'] == 'checked'
    sleeveRepo.updateSleeve(entry['id'], entry['name'], original, note=entry['note'])


def test_renaming_onto_an_existing_name_is_refused():
    variant, category = 'PMG ESG', 'Public Equity'
    names = [s['name'] for s in sleeves.listSleeves(category, variant)]
    mine = sleeveRepo.createSleeve(variant, category, 'Rename Me',
                                   [{'productId': A_PRODUCT, 'weight': 1}])
    try:
        with pytest.raises(ValidationError) as exc:
            sleeveRepo.updateSleeve(mine['id'], names[0], [{'productId': A_PRODUCT, 'weight': 1}])
        assert exc.value.field == 'name'
        # renaming onto its own name is not a clash
        sleeveRepo.updateSleeve(mine['id'], 'Rename Me', [{'productId': A_PRODUCT, 'weight': 1}])
    finally:
        sleeveRepo.deleteSleeve(mine['id'])


def test_a_sleeve_whose_product_left_the_catalogue_is_withheld_but_listed(tmp_path, monkeypatch):
    """A new product extract without a product breaks every sleeve holding
    it: gone from the pickers, present in the console with the reason."""
    with open(CATALOGUE, newline='', encoding='utf-8') as fh:
        rows = list(csv.reader(fh))
    dropped = _aPlacedProduct()
    kept = [r for r in rows if r[0] != dropped]
    assert len(kept) == len(rows) - 1
    path = tmp_path / 'products.csv'
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        csv.writer(fh).writerows(kept)
    monkeypatch.setenv('SCENARIO_PRODUCTS_SOURCE', str(path))
    products.reload()
    try:
        holders = [e for e in sleeveRepo.listAll()
                   if any(p['productId'] == dropped for p in e['products'])]
        assert holders, 'the fixture product is in at least one sleeve'
        for e in holders:
            assert any(dropped in problem for problem in e['problems'])
            assert not sleeves.sleeveExists(e['category'], e['name'], e['variant'])
        assert len(sleeveRepo.census()['broken']) == len(holders)
    finally:
        monkeypatch.delenv('SCENARIO_PRODUCTS_SOURCE')
        products.reload()
    assert sleeveRepo.census()['broken'] == []


# ---- interchange ---------------------------------------------------------------

def test_export_returns_the_seed_and_import_round_trips(tmp_path, monkeypatch):
    assert sorted(sleeveRepo.exportRows()) == sorted(_seedRows())
    out = tmp_path / 'out.csv'
    assert sleeveTools.main(['--export', str(out)]) == 0
    # import the export into a fresh store: the same library comes back
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'fresh.db'))
    monkeypatch.setenv('SCENARIO_SLEEVES_SEED', str(tmp_path / 'absent.csv'))
    assert sleeveRepo.describe()['sleeves'] == 0
    assert sleeveTools.main(['--import', str(out)]) == 0
    assert sleeveRepo.describe()['sleeves'] == 102
    assert sorted(sleeveRepo.exportRows()) == sorted(_seedRows())
    assert sleeveTools.main(['--census']) == 0


def test_import_refuses_a_bad_library_wholesale(tmp_path, monkeypatch):
    bad = tmp_path / 'bad.csv'
    with open(bad, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(sleeveRepo.SEED_COLUMNS)
        w.writerow(['PMG ESG', 'Public Equity', 'Fine', A_PRODUCT, 1.0])
        w.writerow(['PMG ESG', 'Public Equity', 'Short', A_PRODUCT, 0.9])
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'fresh.db'))
    monkeypatch.setenv('SCENARIO_SLEEVES_SEED', str(tmp_path / 'absent.csv'))
    assert sleeveTools.main(['--import', str(bad)]) == 1
    assert sleeveRepo.describe()['sleeves'] == 0, 'a single bad sleeve aborts the whole load'


# ---- the admin role --------------------------------------------------------------

def test_admin_is_a_third_role_gating_the_repository(monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice,bob')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice,carol')
    assert accessControl.isAdmin('alice')
    assert not accessControl.isAdmin('bob'), 'an editor is not an admin'
    assert not accessControl.isAdmin('carol'), 'an admin must also be on the access list'
    assert accessControl.requireAdmin(_request('alice')) == 'alice'
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        accessControl.requireAdmin(_request('bob'))
    assert exc.value.status_code == 403 and 'error' in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        accessControl.requireAdmin(_request(None))
    assert exc.value.status_code == 401


def test_schema_tells_the_page_whether_the_caller_is_an_admin(monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice,bob')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    admin = dashboardRouter.getScenarioSchema(_request('alice'))
    editor = dashboardRouter.getScenarioSchema(_request('bob'))
    assert admin['capabilities']['canAdmin'] is True
    assert editor['capabilities']['canAdmin'] is False
    assert admin['capabilities']['canEdit'] is True, 'the existing capabilities survive'
    # and the stamp did not leak into whatever the port hands back
    again = dashboardRouter.getScenarioSchema(_request('bob'))
    assert again['capabilities']['canAdmin'] is False


def test_every_repository_route_requires_the_admin_role():
    found = {}
    for route in dashboardRouter.router.routes:
        if '/scenario/repository' in route.path:
            names = [d.call.__name__ for d in route.dependant.dependencies]
            found[(route.path, tuple(sorted(route.methods)))] = names
    assert set(found) == {
        ('/scenario/repository', ('GET',)),
        ('/scenario/repository/sleeves', ('POST',)),
        ('/scenario/repository/sleeves/{sleeveId}', ('PUT',)),
        ('/scenario/repository/sleeves/{sleeveId}', ('DELETE',)),
        ('/scenario/repository/sleeves/{sleeveId}/history', ('GET',)),
        ('/scenario/repository/sleeves/{sleeveId}/restore', ('POST',)),
        ('/scenario/repository/sleeves/{sleeveId}/revert', ('POST',)),
        ('/scenario/repository/sleeves/restore', ('POST',)),
        ('/scenario/repository/activity', ('GET',)),
        ('/scenario/repository/archive.csv', ('GET',)),
        ('/scenario/repository/activity.csv', ('GET',)),
        ('/scenario/repository/proposals', ('GET',)),
        ('/scenario/repository/proposals.csv', ('GET',)),
        ('/scenario/repository/proposals/{proposalId}', ('GET',)),
        ('/scenario/repository/proposals/{proposalId}/workbook', ('GET',)),
    }
    for key, names in found.items():
        assert 'requireAdmin' in names, key


def test_the_console_payload_and_the_write_handlers(monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    body = dashboardRouter.getRepository(user='alice')
    assert body['variants'] == sleeves.VARIANTS
    assert body['categories'] == sleeveRepo.categories()
    assert len(body['products']) == 73 and len(body['sleeves']) == 102
    assert body['user'] == 'alice' and body['store']['sleeves'] == 102

    made = dashboardRouter.createRepositorySleeve(
        {'variant': 'PMG ESG', 'category': 'Public Equity', 'name': 'Via Route',
         'products': [{'productId': A_PRODUCT, 'weight': 1}]}, user='alice')
    sleeveId = made['sleeve']['id']
    try:
        refused = dashboardRouter.updateRepositorySleeve(
            sleeveId, {'name': 'Via Route', 'products': [{'productId': A_PRODUCT, 'weight': .5}]},
            user='alice')
        assert refused.status_code == 422
        import json
        assert json.loads(refused.body)['field'] == 'weights'
        ok = dashboardRouter.updateRepositorySleeve(
            sleeveId, {'name': 'Via Route 2', 'products': [{'productId': A_PRODUCT, 'weight': 1}]},
            user='alice')
        assert ok['sleeve']['name'] == 'Via Route 2'
    finally:
        gone = dashboardRouter.deleteRepositorySleeve(sleeveId, user='alice')
    assert gone['deleted']['id'] == sleeveId


# ---- the catalogue view (D58) ----------------------------------------------------

def test_no_orphans_in_the_seed_and_the_payload_carries_the_field(monkeypatch):
    assert sleeveRepo.orphanProducts() == []
    assert sleeveRepo.census()['orphans'] == []
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    assert dashboardRouter.getRepository(user='alice')['orphans'] == []


def test_a_dropped_product_is_reported_by_product_with_the_sleeves_it_breaks(tmp_path, monkeypatch, capsys):
    """The other side of a broken sleeve: gathered by product, which is the
    question the catalogue view answers, and printed by the census."""
    dropped = _aPlacedProduct()
    with open(CATALOGUE, newline='', encoding='utf-8') as fh:
        rows = list(csv.reader(fh))
    kept = [r for r in rows if r[0] != dropped]
    path = tmp_path / 'products.csv'
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        csv.writer(fh).writerows(kept)
    monkeypatch.setenv('SCENARIO_PRODUCTS_SOURCE', str(path))
    products.reload()
    try:
        orphans = sleeveRepo.orphanProducts()
        assert [o['productId'] for o in orphans] == [dropped]
        breaks = orphans[0]['sleeves']
        assert breaks and all(set(s) == {'id', 'variant', 'category', 'name'} for s in breaks)
        broken = {e['id'] for e in sleeveRepo.census()['broken']}
        assert {s['id'] for s in breaks} == broken, 'the two views of the same failure agree'
        assert sleeveTools.main(['--census']) == 1
        out = capsys.readouterr().out
        assert 'NOT IN THE CATALOGUE (1)' in out and dropped in out
    finally:
        monkeypatch.delenv('SCENARIO_PRODUCTS_SOURCE')
        products.reload()


def test_catalogue_helpers_mirror_the_rules_they_implement():
    """The view's pure functions - the join, the enrichment, the derived
    figures, the faceted counts, the filter, the sort and the best-per-row
    marking - run through node against fixture data, the way the rounding and
    tilt mirrors are proved (D63)."""
    import json
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    jsPath = os.path.join(HERE, '..', '..', 'generator', 'js', 'repository.js')
    with open(jsPath, encoding='utf-8') as fh:
        source = fh.read()
    start = source.index('/* catalogue-helpers-begin')
    end = source.index('/* catalogue-helpers-end */')
    helpers = source[start:end]
    productsFx = [
        {'productId': 'a', 'name': 'Alpha ETF', 'ticker': 'ALP', 'assetClass': 'IG Corporate', 'style': 'Passive',
         'vehicle': 'ETF', 'source': 'Internal', 'liquidity': 'Daily', 'exposureCurrency': 'USD',
         'productCost': 0.14, 'feeGroup': 'Passive', 'distributionYield': 4.62, 'minimumInvestment': None},
        {'productId': 'b', 'name': 'Beta SMA', 'ticker': '—', 'assetClass': 'IG Corporate', 'style': 'Active',
         'vehicle': 'SMA', 'source': 'Internal', 'liquidity': 'Daily', 'exposureCurrency': 'USD',
         'productCost': 0.28, 'feeGroup': 'Core Active', 'distributionYield': 4.85, 'minimumInvestment': 5_000_000},
        {'productId': 'c', 'name': 'Gamma Fund', 'ticker': 'GAM', 'assetClass': 'Multi-Strategy', 'style': 'Active',
         'vehicle': 'Mutual Fund', 'source': 'External', 'liquidity': 'Quarterly', 'exposureCurrency': 'Local',
         'productCost': 0.90, 'feeGroup': 'Alternatives', 'distributionYield': None, 'minimumInvestment': 1000},
    ]
    sleevesFx = [
        {'id': 1, 'variant': 'T1', 'category': 'Fixed Income', 'name': 'S1',
         'products': [{'productId': 'a', 'weight': 0.6}, {'productId': 'b', 'weight': 0.4}]},
        {'id': 2, 'variant': 'T2', 'category': 'Fixed Income', 'name': 'S2',
         'products': [{'productId': 'a', 'weight': 1.0}]},
    ]
    facets = [{'key': 'category'}, {'key': 'vehicle'}, {'key': 'liquidity'}, {'key': 'book'}]
    script = helpers + '''
const products = %s, sleeves = %s, facets = %s;
const mgmt = g => ({ 'Passive': 0.26, 'Core Active': 0.32, 'Alternatives': 0.52 })[g];
const rows = catEnrich(products, sleeves, mgmt, 2000000);
const ids = rs => rs.map(r => r.p.productId);
const run = (state, sort) => ids(catSort(catFilter(rows, state), sort));
const none = { query: '', filters: {} };
process.stdout.write(JSON.stringify({
  join: catJoin(sleeves),
  enriched: rows.map(r => ({ id: r.p.productId, used: r.used, cats: r.categories, books: r.books, mgmt: r.mgmt,
                              allIn: +r.allIn.toFixed(2), net: r.net === null ? null : +r.net.toFixed(2), tooBig: r.tooBig })),
  unpriced: catEnrich(products, sleeves, null, null).map(r => [r.mgmt, +r.allIn.toFixed(2), r.tooBig]),
  facets: catFacets(rows, facets, none, { book: ['T1', 'T2', 'Not yet placed'] }),
  facetsOtherFilters: catFacets(rows, facets, { query: '', filters: { vehicle: ['ETF'] } }, {}),
  byVehicle: run({ query: '', filters: { vehicle: ['SMA', 'ETF'] } }, null),
  byBook: run({ query: '', filters: { book: ['T2'] } }, null),
  unplaced: run({ query: '', filters: { category: ['Not yet placed'] } }, null),
  bySearch: run({ query: 'gam', filters: {} }, null),
  allInAsc: run(none, { key: 'allIn', dir: 'asc' }),
  yieldDesc: run(none, { key: 'distributionYield', dir: 'desc' }),
  yieldAsc: run(none, { key: 'distributionYield', dir: 'asc' }),
  minAsc: run(none, { key: 'minimumInvestment', dir: 'asc' }),
  best: catBest(rows),
}));
''' % (json.dumps(productsFx), json.dumps(sleevesFx), json.dumps(facets))
    out = subprocess.run([node, '-e', script], capture_output=True, text=True, check=True)
    got = json.loads(out.stdout)
    assert got['join'] == {'a': {'used': 2, 'categories': ['Fixed Income'], 'books': ['T1', 'T2']},
                           'b': {'used': 1, 'categories': ['Fixed Income'], 'books': ['T1']}}
    a, b, c = got['enriched']
    assert (a['mgmt'], a['allIn'], a['net']) == (0.26, 0.40, 4.22)
    assert (b['mgmt'], b['allIn'], b['net'], b['tooBig']) == (0.32, 0.60, 4.25, True), 'a $5m minimum is above a $2m mandate'
    assert (c['net'], c['used'], c['cats'], c['books']) == (None, 0, [], []), 'no yield means no net; unplaced means empty joins'
    assert got['unpriced'] == [[None, 0.14, False], [None, 0.28, False], [None, 0.90, False]], 'no schedule: all-in is cost, nothing flagged'
    f = got['facets']
    assert f['category'] == [{'value': 'Fixed Income', 'count': 2}, {'value': 'Not yet placed', 'count': 1}]
    assert f['book'] == [{'value': 'T1', 'count': 2}, {'value': 'T2', 'count': 1}, {'value': 'Not yet placed', 'count': 1}]
    assert f['liquidity'] == [{'value': 'Daily', 'count': 2}, {'value': 'Quarterly', 'count': 1}]
    # with Vehicle = ETF in force, every OTHER facet counts only the ETF, and
    # the vehicle facet itself still counts everything - so a choice can be widened
    g = got['facetsOtherFilters']
    assert g['liquidity'] == [{'value': 'Daily', 'count': 1}, {'value': 'Quarterly', 'count': 0}]
    assert g['vehicle'] == [{'value': 'ETF', 'count': 1}, {'value': 'Mutual Fund', 'count': 1}, {'value': 'SMA', 'count': 1}]
    assert got['byVehicle'] == ['a', 'b'] and got['byBook'] == ['a'] and got['unplaced'] == ['c']
    assert got['bySearch'] == ['c']
    assert got['allInAsc'] == ['a', 'b', 'c']
    assert got['yieldDesc'] == ['b', 'a', 'c'] and got['yieldAsc'] == ['a', 'b', 'c'], 'a blank sorts last either way'
    assert got['minAsc'] == ['c', 'b', 'a']
    assert got['best'] == {'productCost': 'a', 'mgmt': 'a', 'allIn': 'a', 'distributionYield': 'b', 'net': 'b'}
# ---- creating under several books, and copying between them (D61) ----------------

def test_one_definition_can_be_created_under_several_types_at_once():
    """The console's Create, and its right-click Add to implementation type,
    are the same call: a sleeve is defined once and offered wherever it is
    wanted, rather than typed out per book."""
    made = sleeveRepo.createSleeves(
        ['PMG ESG', 'Irish Onshore'], 'Public Equity', 'Shared Definition',
        [{'productId': A_PRODUCT, 'weight': 0.6}, {'productId': ANOTHER, 'weight': 0.4}],
        note='one definition', user='alice')
    try:
        assert [m['variant'] for m in made] == ['PMG ESG', 'Irish Onshore'], 'VARIANTS order'
        for m in made:
            assert m['name'] == 'Shared Definition' and m['note'] == 'one definition'
            assert [p['productId'] for p in m['products']] == [A_PRODUCT, ANOTHER]
            assert sorted(m['offeredUnder']) == ['Irish Onshore', 'PMG ESG']
        # and it reaches the pickers of both, but not of the books not asked for
        for variant in ('PMG ESG', 'Irish Onshore'):
            assert sleeves.sleeveExists('Public Equity', 'Shared Definition', variant)
        for variant in ('PMG Multi-Asset Portfolio', 'US Onshore'):
            assert not sleeves.sleeveExists('Public Equity', 'Shared Definition', variant)
    finally:
        for m in made:
            sleeveRepo.deleteSleeve(m['id'])


def test_a_clash_in_one_type_writes_none_of_them():
    """All or nothing. A half-applied create would leave the library saying
    something nobody asked for, and the admin with no way to tell."""
    # a name PMG ESG offers and Irish Onshore does not, so the refusal comes
    # from one of the two and the other is left provably untouched
    irish = {s['name'] for s in sleeves.listSleeves('Public Equity', 'Irish Onshore')}
    clashing = next(s['name'] for s in sleeves.listSleeves('Public Equity', 'PMG ESG')
                    if s['name'] not in irish)
    before = sleeveRepo.describe()['sleeves']
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.createSleeves(
            ['Irish Onshore', 'PMG ESG'], 'Public Equity', clashing,
            [{'productId': A_PRODUCT, 'weight': 1.0}])
    assert exc.value.field == 'name'
    assert sleeveRepo.describe()['sleeves'] == before
    assert not sleeves.sleeveExists('Public Equity', clashing, 'Irish Onshore')


@pytest.mark.parametrize('variants, field', [
    ([], 'variants'),
    (['Not A Type'], 'variant'),
    (['PMG ESG', 'Not A Type'], 'variant'),
])
def test_the_type_list_itself_is_validated(variants, field):
    """An unknown type is named, never quietly dropped - a caller that asks
    for a book that does not exist has made a mistake worth hearing about."""
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.createSleeves(variants, 'Public Equity', 'Should Not Save',
                                 [{'productId': A_PRODUCT, 'weight': 1.0}])
    assert exc.value.field == field


def test_a_fixed_category_refuses_a_second_sleeve_however_it_arrives():
    """Copying into a book whose fixed category already holds its one sleeve
    is the same refusal as building a second one by hand."""
    variant = sleeves.VARIANTS[1]
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.createSleeves([variant], rules.VOL_PREMIUM_CATEGORY, 'A Second One',
                                 [{'productId': A_PRODUCT, 'weight': 1.0}])
    assert exc.value.field == 'category'


def test_the_route_takes_a_list_of_types_and_reports_them_all(monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    body = dashboardRouter.createRepositorySleeve(
        {'variants': ['US Onshore', 'PMG Multi-Asset Portfolio'], 'category': 'Hedge Funds',
         'name': 'Route Shared', 'products': [{'productId': A_PRODUCT, 'weight': 1}]},
        user='alice')
    made = body['sleeves']
    try:
        assert len(made) == 2 and body['sleeve'] == made[0]
        assert {m['variant'] for m in made} == {'US Onshore', 'PMG Multi-Asset Portfolio'}
        # the single-type form still works, for callers that send one
        one = dashboardRouter.createRepositorySleeve(
            {'variant': 'PMG ESG', 'category': 'Hedge Funds', 'name': 'Route Shared',
             'products': [{'productId': A_PRODUCT, 'weight': 1}]}, user='alice')
        made.append(one['sleeve'])
        assert one['sleeve']['variant'] == 'PMG ESG'
    finally:
        for m in made:
            sleeveRepo.deleteSleeve(m['id'])


# ---- the built stylesheet carries every section the console needs ----------------

def test_the_built_stylesheet_still_carries_every_console_section():
    """A splice that replaces one stylesheet section by its neighbours' markers
    can take a whole section with it and nothing fails - the page renders,
    unstyled. It happened once (D62 took the catalogue's styles). The built
    CSS is what the host serves, so that is what is checked: one selector
    from each section of the console's stylesheet, and the page's own."""
    css = os.path.join(HERE, '..', '..', 'proposalTool', 'static', 'css', 'proposalTool.css')
    with open(css, encoding='utf-8') as fh:
        built = fh.read()
    sections = {
        'console frame':      ['.dialog.repo{', '.repo-h{', '.repo-seg button[aria-selected="true"]'],
        'sleeve panes':       ['.repo-cat[aria-selected="true"]', '.repo-sleeve{', '.repo-fixed{'],
        'editor':             ['.repo-prods .pr{', '.repo-search{', '.repo-menu li[aria-selected="true"]', '.repo-tot{'],
        'creation and menu':  ['.btn.btn-create{', '.repo-vars{', '.repo-ctx{', '.repo-mi.danger{'],
        'catalogue view':     ['.cat-tools{', '.cat-facets{', '.cat-fo.on{', '.cat-menu{', '.cat-tbl th{',
                               '.cat-tbl td.nm{', '.cat-tbl tr.pin td{', '.cat-tbl tr.cur td{', '.cat-tbl td.used.zero{',
                               '.liq.slow{', '.cat-tray{', '.cat-cmp td.best{', '.cat-detail{', '.cat-usedin .r{',
                               '.cat-link{'],
        'admin entry points': ['.rail-admin-bar{', '.rail-admin-btn{', 'body.rail-collapsed .rail-admin-bar{',
                               '.tier-admin{'],
        'the record (D65)':   ['.repo-hist{', '.repo-histh{', '.rev-h{', '.rev.now .rev-n{', '.rev-changes{',
                               '.rev-products{', '.rev-put{'],
        'archive and feed':   ['.arc-sel{', '.arc-sel select{', '.arc-tbl tr.pin td{', '.arc-badge.gone{',
                               '.arc-detail{', '.arc-db{', '.act-chip.on{', '.act-day{', '.act-ev{',
                               '.act-badge.deleted{', '.act-more{'],
        'proposal register':  ['.dialog.repo.register{', '.reg-tbl tr[data-regrow]{', '.arc-badge.warn{',
                               '.reg-detail{', '.reg-pic tr.grp td{', '.reg-pic td.sub{'],
        'fee card':           ['.dialog.rc{', '.rc-seg button[aria-selected="true"]', '.rate-grid .rc-grp{',
                               '.rate-grid td.ring{', '.rc-legend i.k-ring{'],
    }
    missing = {name: [s for s in selectors if s not in built] for name, selectors in sections.items()}
    missing = {name: gone for name, gone in missing.items() if gone}
    assert not missing, 'stylesheet sections missing from the build: {}'.format(missing)
    # and the mirror the host serves is the same file
    mirror = os.path.join(HERE, '..', 'cyrus_pmg', 'dashboard', 'proposalTool', 'static', 'css', 'proposalTool.css')
    with open(mirror, encoding='utf-8') as fh:
        assert fh.read() == built, 'the service mirror has drifted from the page'


# ---- surviving an outage: the degraded banner and the cold-landing page (D64) ----

def test_the_shell_carries_both_outage_surfaces():
    """Two mutually exclusive states, and each needs its markup in the shell:
    the banner for a page that survived on its cache, the full page for a cold
    landing with nothing to show."""
    html = os.path.join(HERE, '..', '..', 'proposalTool', 'proposalTool.html')
    with open(html, encoding='utf-8') as fh:
        shell = fh.read()
    for needed in ('id="degraded"', 'id="degraded-retry"', 'id="degraded-now"',
                   'id="view-schema-error"', 'id="down-safe"', 'id="down-retry"',
                   'id="down-more"', 'id="schema-error-reason"', 'id="schema-retry"'):
        assert needed in shell, 'the shell is missing {}'.format(needed)
    mirror = os.path.join(HERE, '..', 'cyrus_pmg', 'dashboard', 'proposalTool', 'proposalTool.html')
    with open(mirror, encoding='utf-8') as fh:
        assert fh.read() == shell, 'the service mirror has drifted from the page'


def test_the_proxy_says_which_outage_it_is():
    """The page's two messages are only honest because the proxy distinguishes
    an unreachable backend from a slow one. Pinned here because the wording is
    quoted to the user (D64)."""
    front = os.path.join(HERE, '..', 'cyrus_pmg', 'dashboard', 'dashboardFrontend.py')
    with open(front, encoding='utf-8') as fh:
        source = fh.read()
    assert "'The scenario service is not reachable. '" in source and '502' in source
    assert "'The scenario service timed out.'" in source and '504' in source
    # and apiFetch has to report both to the page, or nothing goes read only
    js = os.path.join(HERE, '..', '..', 'proposalTool', 'static', 'js', 'proposalTool.js')
    with open(js, encoding='utf-8') as fh:
        built = fh.read()
    assert 'App.noteService(resp.status !== 502 && resp.status !== 504' in built
    assert 'App.noteService(false, 0)' in built, 'a failed fetch must count as down too'


def test_the_outage_state_machine_runs():
    """The backoff, the read-only gate and the snapshot round trip, exercised
    in node against the built page - no DOM, just the rules."""
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    js = os.path.join(HERE, '..', '..', 'generator', 'js', 'core.js')
    with open(js, encoding='utf-8') as fh:
        source = fh.read()
    start = source.index('var RETRY_STEPS')
    end = source.index('function noteService(')
    backoff = source[start:end]
    script = backoff + '''
const seen = [0,1,2,3,4,5,6,7,20].map(retryDelay);
process.stdout.write(JSON.stringify({
  steps: seen,
  monotonic: seen.every((v, i, a) => i === 0 || v >= a[i - 1]),
  capped: seen[seen.length - 1] === seen[6],
}));
'''
    out = subprocess.run([node, '-e', script], capture_output=True, text=True, check=True)
    import json
    got = json.loads(out.stdout)
    assert got['steps'][:6] == [3000, 5000, 8000, 13000, 21000, 30000]
    assert got['monotonic'], 'a backoff that shortens is not a backoff'
    assert got['capped'], 'the interval has to stop growing or an outage stops being watched'


def test_read_only_is_one_gate_not_many():
    """canEdit is what every control in the rail, the pickers and the dialogs
    asks before enabling itself. Degraded mode works by failing that one
    question, so nothing can be changed that could not be saved (D64)."""
    js = os.path.join(HERE, '..', '..', 'generator', 'js', 'core.js')
    with open(js, encoding='utf-8') as fh:
        source = fh.read()
    assert "function canEdit() { return state.service !== 'down'" in source
    assert "function canExport() { return state.service !== 'down'" in source
    # and recovery must not leave the restored columns beside their refetch
    assert 'state.columns = [];' in source[source.index('async function recover()'):
                                           source.index('async function recover()') + 600]


# --------------------------------------------------------------------------
# The running history (D65)
#
# The rule the whole table exists to keep: a sleeve's earlier versions are
# readable for ever, and a delete takes a sleeve out of the LIBRARY without
# taking it off the record. Everything below is a way of trying to break that.
# --------------------------------------------------------------------------

def _sleeve(name='History Test', products_=None, **kw):
    return sleeveRepo.createSleeve(
        kw.pop('variant', 'PMG ESG'), kw.pop('category', 'Public Equity'), name,
        products_ or [{'productId': A_PRODUCT, 'weight': 0.6},
                      {'productId': ANOTHER, 'weight': 0.4}],
        user=kw.pop('user', 'alice'), **kw)


def test_every_seeded_sleeve_starts_with_a_baseline_revision():
    entry = sleeveRepo.listAll()[0]
    trail = sleeveRepo.history(entry['id'])
    assert trail, 'a seeded sleeve has a history from the first connection'
    assert trail[-1]['revision'] == 1
    assert trail[-1]['action'] in ('baseline', 'seeded')
    assert trail[-1]['changes'] == [], 'the first revision has nothing to compare against'


def test_an_edit_keeps_the_version_it_replaced():
    made = _sleeve('Keeps What It Replaced')
    try:
        sleeveRepo.updateSleeve(made['id'], 'Renamed',
                                [{'productId': A_PRODUCT, 'weight': 1.0}], user='bob')
        trail = sleeveRepo.history(made['id'])
        assert [e['revision'] for e in trail] == [2, 1], 'newest first'

        was, now = trail[1], trail[0]
        assert was['name'] == 'Keeps What It Replaced' and now['name'] == 'Renamed'
        assert [p['productId'] for p in was['products']] == [A_PRODUCT, ANOTHER]
        assert [p['productId'] for p in now['products']] == [A_PRODUCT]
        assert was['products'][0]['weight'] == 0.6, 'the earlier weights survive the edit'
        assert now['actor'] == 'bob' and was['actor'] == 'alice'
        assert now['current'] is True and was['current'] is False
    finally:
        sleeveRepo.deleteSleeve(made['id'], user='alice')


def test_the_history_says_what_moved():
    made = _sleeve('Says What Moved')
    try:
        sleeveRepo.updateSleeve(made['id'], 'Says What Moved',
                                [{'productId': A_PRODUCT, 'weight': 0.75},
                                 {'productId': ANOTHER, 'weight': 0.25}], user='bob')
        changes = sleeveRepo.history(made['id'])[0]['changes']
        assert any('60% to 75%' in c for c in changes), changes
        assert any('40% to 25%' in c for c in changes), changes
        assert all('gs-us-corporate' not in c for c in changes), 'products read by name'

        sleeveRepo.updateSleeve(made['id'], 'Third Name',
                                [{'productId': A_PRODUCT, 'weight': 1.0}], user='bob')
        changes = sleeveRepo.history(made['id'])[0]['changes']
        assert 'Renamed from Says What Moved' in changes
        assert any(c.startswith('Removed ') for c in changes), changes
    finally:
        sleeveRepo.deleteSleeve(made['id'], user='alice')


def test_a_delete_leaves_the_library_but_not_the_record():
    made = _sleeve('Leaves The Library')
    sleeveRepo.deleteSleeve(made['id'], user='dave')

    assert made['id'] not in [e['id'] for e in sleeveRepo.listAll()]
    assert 'Leaves The Library' not in [
        s['name'] for s in sleeves.listSleeves('Public Equity', 'PMG ESG')]

    gone = [e for e in sleeveRepo.listArchived() if e['id'] == made['id']]
    assert len(gone) == 1 and gone[0]['archivedBy'] == 'dave'
    assert gone[0]['archived'] is True

    trail = sleeveRepo.history(made['id'])
    assert [e['action'] for e in trail] == ['deleted', 'created']
    assert [p['productId'] for p in trail[0]['products']] == [A_PRODUCT, ANOTHER], \
        'the deleting revision holds what the sleeve looked like when it went'


def test_a_deleted_sleeve_frees_its_name_and_can_still_come_back():
    first = _sleeve('Freed Name')
    sleeveRepo.deleteSleeve(first['id'], user='dave')

    second = _sleeve('Freed Name')                       # the name is available again
    assert second['id'] != first['id']
    with pytest.raises(ValidationError):
        sleeveRepo.restoreSleeve(first['id'], user='dave')   # ...so the restore is refused

    sleeveRepo.deleteSleeve(second['id'], user='dave')
    back = sleeveRepo.restoreSleeve(first['id'], user='dave')
    try:
        assert back['archived'] is False and back['id'] == first['id']
        assert [e['action'] for e in sleeveRepo.history(first['id'])] == \
            ['restored', 'deleted', 'created']
        assert 'Freed Name' in [s['name'] for s in sleeves.listSleeves('Public Equity', 'PMG ESG')]
    finally:
        sleeveRepo.deleteSleeve(first['id'], user='dave')


def test_a_deleted_sleeve_cannot_be_edited_or_deleted_twice():
    made = _sleeve('Not Editable Once Gone')
    sleeveRepo.deleteSleeve(made['id'], user='dave')
    with pytest.raises(ValidationError):
        sleeveRepo.updateSleeve(made['id'], 'Nope',
                                [{'productId': A_PRODUCT, 'weight': 1.0}], user='dave')
    with pytest.raises(ValidationError):
        sleeveRepo.deleteSleeve(made['id'], user='dave')


def test_a_revert_puts_a_version_back_without_rewinding_the_record():
    made = _sleeve('Reverts Cleanly')
    try:
        sleeveRepo.updateSleeve(made['id'], 'Reverts Cleanly',
                                [{'productId': A_PRODUCT, 'weight': 1.0}], user='bob')
        back = sleeveRepo.revertSleeve(made['id'], 1, user='carol')

        assert [(p['productId'], p['weight']) for p in back['products']] == \
            [(A_PRODUCT, 0.6), (ANOTHER, 0.4)], 'the original composition is in force again'

        trail = sleeveRepo.history(made['id'])
        assert [e['revision'] for e in trail] == [3, 2, 1]
        assert trail[0]['action'] == 'reverted' and trail[0]['actor'] == 'carol'
        assert trail[1]['action'] == 'updated', 'the revision it replaced is still there'
        assert len(trail[1]['products']) == 1
    finally:
        sleeveRepo.deleteSleeve(made['id'], user='alice')


def test_a_revert_is_validated_like_any_other_save():
    made = _sleeve('Validated Revert')
    try:
        with pytest.raises(ValidationError):
            sleeveRepo.revertSleeve(made['id'], 99, user='carol')
    finally:
        sleeveRepo.deleteSleeve(made['id'], user='alice')


def test_history_is_append_only_across_a_whole_life():
    """The property that matters: no operation ever shortens the trail."""
    made = _sleeve('Append Only')
    depth = len(sleeveRepo.history(made['id']))
    for step in (lambda: sleeveRepo.updateSleeve(
                     made['id'], 'Append Only', [{'productId': A_PRODUCT, 'weight': 1.0}],
                     user='bob'),
                 lambda: sleeveRepo.revertSleeve(made['id'], 1, user='carol'),
                 lambda: sleeveRepo.deleteSleeve(made['id'], user='dave'),
                 lambda: sleeveRepo.restoreSleeve(made['id'], user='dave')):
        step()
        grown = len(sleeveRepo.history(made['id']))
        assert grown == depth + 1, 'every write appends exactly one revision'
        depth = grown
    revisions = [e['revision'] for e in sleeveRepo.history(made['id'])]
    assert revisions == sorted(revisions, reverse=True)
    assert len(set(revisions)) == len(revisions), 'revision numbers are unique and dense'
    sleeveRepo.deleteSleeve(made['id'], user='alice')


def test_the_console_payload_and_the_endpoints_carry_the_record(monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    made = _sleeve('Reaches The Console')
    sleeveRepo.updateSleeve(made['id'], 'Reaches The Console',
                            [{'productId': A_PRODUCT, 'weight': 1.0}], user='alice')

    body = dashboardRouter.getRepository(user='alice')
    assert 'archived' in body and isinstance(body['archived'], list)
    mine = [s for s in body['sleeves'] if s['id'] == made['id']][0]
    assert mine['revisions'] == 2

    trail = dashboardRouter.getRepositorySleeveHistory(made['id'], user='alice')
    assert trail['sleeveId'] == made['id'] and len(trail['history']) == 2

    dashboardRouter.deleteRepositorySleeve(made['id'], user='alice')
    after = dashboardRouter.getRepository(user='alice')
    assert made['id'] in [s['id'] for s in after['archived']]
    assert made['id'] not in [s['id'] for s in after['sleeves']]
    assert len(dashboardRouter.getRepositorySleeveHistory(made['id'], user='alice')['history']) == 3

    restored = dashboardRouter.restoreRepositorySleeve(made['id'], user='alice')
    assert restored['sleeve']['archived'] is False
    reverted = dashboardRouter.revertRepositorySleeve(
        made['id'], {'revision': 1}, user='alice')
    assert len(reverted['sleeve']['products']) == 2
    sleeveRepo.deleteSleeve(made['id'], user='alice')


def test_a_replacing_import_retires_the_library_rather_than_erasing_it():
    before = {e['id'] for e in sleeveRepo.listAll()}
    rows = [('PMG ESG', 'Public Equity', 'Only Survivor', A_PRODUCT, 1.0)]
    sleeveRepo.importRows(rows, replace=True, user='importer')
    try:
        assert len(sleeveRepo.listAll()) == 1
        retired = {e['id'] for e in sleeveRepo.listArchived()}
        assert before <= retired, 'every retired sleeve is still on the record'
        one = sorted(before)[0]
        assert sleeveRepo.history(one)[0]['action'] == 'deleted'
        assert sleeveRepo.history(one)[0]['actor'] == 'importer'
    finally:
        # put the library back the way the rest of the session expects it
        sleeveRepo.importRows(_seedRows(), replace=True, user='seed')


# --------------------------------------------------------------------------
# The archive and the feed (D66)
# --------------------------------------------------------------------------

def test_the_feed_is_the_whole_record_newest_first_and_pages_without_overlap():
    made = _sleeve('Feed Subject')
    sleeveRepo.updateSleeve(made['id'], 'Feed Subject',
                            [{'productId': A_PRODUCT, 'weight': 1.0}], user='bob')
    sleeveRepo.deleteSleeve(made['id'], user='dave')

    page = sleeveRepo.activity(limit=2)
    assert [e['action'] for e in page['entries']] == ['deleted', 'updated']
    assert page['entries'][0]['sleeveId'] == made['id']
    assert page['entries'][0]['sleeveArchived'] is True
    assert page['next'], 'more than two rows on record'

    rest = sleeveRepo.activity(limit=500, before=page['next'])
    seen = {e['cursor'] for e in page['entries']}
    assert not (seen & {e['cursor'] for e in rest['entries']}), 'no row appears on two pages'
    assert page['total'] == len(page['entries']) + len(rest['entries']) + (1 if rest['next'] else 0) \
        or page['total'] >= len(page['entries']) + len(rest['entries'])


def test_the_feed_filters_and_its_facets_count_what_the_other_filters_leave():
    made = _sleeve('Feed Filter', user='zed')
    sleeveRepo.deleteSleeve(made['id'], user='zed')
    only = sleeveRepo.activity(actions=['deleted'], actor='zed')
    assert all(e['action'] == 'deleted' and e['actor'] == 'zed' for e in only['entries'])
    assert only['entries'] and only['entries'][0]['sleeveId'] == made['id']
    # the action facet is counted with the action filter left out, so the
    # other actions zed performed are still offered from here
    assert 'created' in only['facets']['action']
    # and the actor facet, with the actor left out, still offers everyone
    assert 'alice' in only['facets']['actor']


def test_the_feed_text_query_reaches_product_names_and_change_lines():
    made = _sleeve('Feed Query Target')
    sleeveRepo.updateSleeve(made['id'], 'Feed Query Target',
                            [{'productId': A_PRODUCT, 'weight': 1.0}], user='bob')
    byProduct = sleeveRepo.activity(query='corporate bond', limit=500)
    assert made['id'] in {e['sleeveId'] for e in byProduct['entries']}
    byChange = sleeveRepo.activity(query='Removed', actions=['updated'], limit=500)
    assert any(e['sleeveId'] == made['id'] for e in byChange['entries'])
    nothing = sleeveRepo.activity(query='zzz-no-such-thing-zzz')
    assert nothing['entries'] == [] and nothing['next'] is None
    sleeveRepo.deleteSleeve(made['id'], user='alice')


def test_a_batch_restore_is_all_or_nothing_and_checks_itself():
    a = _sleeve('Batch A'); b = _sleeve('Batch B')
    sleeveRepo.deleteSleeve(a['id'], user='dave'); sleeveRepo.deleteSleeve(b['id'], user='dave')
    # a live sleeve now takes B's name, so the batch cannot come back clean
    blocker = _sleeve('Batch B')
    with pytest.raises(ValidationError):
        sleeveRepo.restoreSleeves([a['id'], b['id']], user='dave')
    assert a['id'] in [e['id'] for e in sleeveRepo.listArchived()], 'A was not restored alone'
    sleeveRepo.deleteSleeve(blocker['id'], user='dave')

    back = sleeveRepo.restoreSleeves([a['id'], b['id']], user='dave')
    assert sorted(e['name'] for e in back) == ['Batch A', 'Batch B']
    assert all(e['archived'] is False for e in back)
    for sleeveId in (a['id'], b['id']):
        assert sleeveRepo.history(sleeveId)[0]['action'] == 'restored'
        sleeveRepo.deleteSleeve(sleeveId, user='alice')


def test_a_batch_restore_refuses_two_sleeves_that_would_collide_with_each_other():
    a = _sleeve('Same Name')
    sleeveRepo.deleteSleeve(a['id'], user='dave')
    b = _sleeve('Same Name')                       # the name is free again
    sleeveRepo.deleteSleeve(b['id'], user='dave')
    with pytest.raises(ValidationError):
        sleeveRepo.restoreSleeves([a['id'], b['id']], user='dave')
    assert {a['id'], b['id']} <= {e['id'] for e in sleeveRepo.listArchived()}
    with pytest.raises(ValidationError):
        sleeveRepo.restoreSleeves([], user='dave')


def test_the_exports_carry_the_archive_and_the_feed_as_tables():
    made = _sleeve('Exported')
    sleeveRepo.deleteSleeve(made['id'], user='dave')
    archive = sleeveRepo.archiveRows()
    row = [r for r in archive if r[0] == made['id']][0]
    assert dict(zip(sleeveRepo.ARCHIVE_COLUMNS, row))['Sleeve'] == 'Exported'
    assert 'gs-us-corporate-bond-fund 60.00%' in row[-1]
    feed = sleeveRepo.activityRows(actions=['deleted'], actor='dave')
    assert feed and all(r[1] == 'deleted' and r[2] == 'dave' for r in feed)
    assert len(feed) == sleeveRepo.activity(actions=['deleted'], actor='dave')['total']


def test_the_feed_and_export_endpoints_answer_as_the_console_expects(monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    made = _sleeve('Endpoint Feed')
    sleeveRepo.deleteSleeve(made['id'], user='alice')

    page = dashboardRouter.getRepositoryActivity(actions='deleted,created', actor='alice',
                                                 limit=5, user='alice')
    assert page['entries'] and set(e['action'] for e in page['entries']) <= {'deleted', 'created'}
    assert 'facets' in page and 'total' in page

    csv_ = dashboardRouter.exportRepositoryArchive(user='alice')
    assert csv_.media_type.startswith('text/csv')
    body = csv_.body.decode('utf-8')
    assert body.splitlines()[0] == ','.join(sleeveRepo.ARCHIVE_COLUMNS)
    assert 'Endpoint Feed' in body

    feedCsv = dashboardRouter.exportRepositoryActivity(actions='deleted', user='alice')
    assert 'Endpoint Feed' in feedCsv.body.decode('utf-8')

    back = dashboardRouter.restoreRepositorySleeves({'ids': [made['id']]}, user='alice')
    assert back['sleeves'][0]['archived'] is False
    sleeveRepo.deleteSleeve(made['id'], user='alice')


def test_four_workers_cold_starting_together_seed_the_library_once(tmp_path):
    """The host runs several uvicorn workers against one store file, so the
    first request after a deployment can find four processes creating and
    seeding an empty database at the same moment.

    Both halves of that were broken and are asserted here. The sleeves table
    was created without IF NOT EXISTS behind a check-then-create, so three
    workers in four failed outright; and the seed itself was a second
    check-then-act, which let two workers both fill the library - the live-name
    index refused the duplicate sleeves, but sleeveHistory has no such index
    and ended up with one *seeded* revision per worker.

    The window between the check and the seed is widened deliberately: at real
    speed the four processes stagger themselves and the second race almost
    never fires, which is exactly what would have let it reach the host.
    """
    import subprocess
    import sys
    import time

    rows = list(sleeveRepo.readSeedRows(sleeveRepo.seedPath()))
    wantedSleeves = len({(variant, category, name) for variant, category, name, _, _ in rows})
    wantedProducts = len(rows)

    database = tmp_path / 'cold.db'
    worker = tmp_path / 'worker.py'
    worker.write_text(
        'import os, sys, time\n'
        'os.environ["SCENARIO_SLEEVES_DB"] = sys.argv[1]\n'
        'from cyrus_pmg.pmgService.scenario import sleeveRepo\n'
        'realSeed = sleeveRepo._seed\n'
        'def slowSeed(conn):\n'
        '    time.sleep(1.0)\n'
        '    return realSeed(conn)\n'
        'sleeveRepo._seed = slowSeed\n'
        'while time.time() < float(sys.argv[2]):\n'
        '    pass\n'
        'print(len(sleeveRepo.listAll()))\n')

    environment = dict(os.environ, PYTHONPATH=os.path.abspath(os.path.join(HERE, '..')))
    environment.pop('SCENARIO_SLEEVES_DB', None)
    startAt = repr(time.time() + 3)
    running = [subprocess.Popen([sys.executable, str(worker), str(database), startAt],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=environment)
               for _ in range(4)]
    finished = [(process, ) + process.communicate() for process in running]

    for process, out, err in finished:
        assert process.returncode == 0, err[-2000:]
        assert int(out.strip()) == wantedSleeves

    opened = sqlite3.connect(str(database))
    try:
        counted = lambda sql: opened.execute(sql).fetchone()[0]
        assert counted('SELECT COUNT(*) FROM sleeves') == wantedSleeves
        assert counted('SELECT COUNT(*) FROM sleeveProducts') == wantedProducts
        # one 'seeded' revision per sleeve, not one per sleeve per worker
        assert counted('SELECT COUNT(*) FROM sleeveHistory') == wantedSleeves
        assert counted("SELECT COUNT(*) FROM meta WHERE key = 'seededAt'") == 1
    finally:
        opened.close()
