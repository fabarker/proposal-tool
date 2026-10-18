"""Custom portfolios (D171): a proposal built product by product, chosen as
"Custom" in the rail's Allocation selector. Its categories follow from its
products, its risk profile sets the limits, an admin who did not build it
approves one outside the profile's usual range, and the delivered files lead
with its own allocation, the strategic portfolios beside it as comparisons."""
import io
import json
import zipfile

import pytest

from cyrus_pmg.pmgService.scenario import (customPortfolio, feeCard, pptWriter, products,
                                           proposalRegister, repositoryFeed, rules,
                                           scenarioStore)
from cyrus_pmg.pmgService.scenario.types import BasisInput, ValidationError

VARIANT = 'PMG Multi-Asset Portfolio'
IG = 'Investment Grade Fixed Income'
PE = 'Public Equity'
PRIVATE = 'Private Equity & Other Private Assets'
KEY = {'currency': 'USD', 'riskLevel': 'Moderate', 'allocationType': 'Full', 'excludeRealAssets': False}
BASIS = BasisInput('USD', 'Hedged')


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path / 'scenarios'))
    products.reload()
    feeCard._forget()
    yield tmp_path
    products.reload()
    feeCard._forget()


def _strategic(mandate=1e9):
    """Moderate Full with the first sleeve of each category and both
    overlays: the reference a custom portfolio is measured against."""
    from test_initial_allocation import _book, _sleeves
    from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows
    _, result = _book('USD', 'USD|Moderate|Full|0')
    model = buildImplementationRows(result, _sleeves(result), rules.autoSleeveCategories(), mandate,
                                    VARIANT, True, 'CASP', 'PMG Target', mandate, True, 'USD')
    return result, model


def _asBuild(model, profile='Moderate'):
    """A strategic model's products at their printed weights, as a build."""
    return {'profile': profile, 'products': [
        {'productId': item['productId'], 'weightPct': item['printedPct'], 'category': group['category']}
        for group in model['groups'] for item in group['items'] if item['printedPct']]}


def _anchor(profile='Moderate', variant=VARIANT):
    from test_scenario_backend import PORT
    return customPortfolio.anchorLines(customPortfolio.anchorFor(PORT, BASIS, variant, profile))


def _move(build, fromId, toId, points):
    rows = {r['productId']: r for r in build['products']}
    rows[fromId]['weightPct'] = round(rows[fromId]['weightPct'] - points, 2)
    rows[toId]['weightPct'] = round(rows[toId]['weightPct'] + points, 2)
    return build


# ------------------------------------------------------------ who builds ---

def test_only_admins_build_until_opened_to_pwas_and_the_limits_are_fixed(store):
    from cyrus_pmg.pmgService.scenario import customAccess
    assert customPortfolio.LIMITS == {'soft': 8, 'hard': 15, 'concentration': 25, 'illiquid': 30,
                                      'maxProducts': 40}
    assert customPortfolio.forCaller(True)['limits'] == customPortfolio.LIMITS
    assert not hasattr(customPortfolio, 'setLimits') and not hasattr(customPortfolio, 'settings')
    assert customPortfolio.mayBuild(VARIANT, admin=True) and not customPortfolio.mayBuild(VARIANT, admin=False)
    assert customPortfolio.forCaller(False)['mayBuild'] == []
    assert customPortfolio.forCaller(True)['mayBuild'] == list(rules.VARIANT_ALLOCATIONS)
    # the one switch opens custom portfolios with custom sleeves (D172)
    customAccess.setPwas(True, user='alice')
    assert customPortfolio.mayBuild(VARIANT, admin=False)
    assert customPortfolio.forCaller(False)['mayBuild'] == list(rules.VARIANT_ALLOCATIONS)
    customAccess.setPwas(False, user='alice')
    # the switch is all the Activity tab records of the custom workflows
    feed = repositoryFeed.activity(areas=['access'])
    assert [e['area'] for e in feed['entries']] == ['access', 'access']
    assert not {'custom', 'portfolios'} & set(repositoryFeed.AREAS)


def test_each_profile_is_anchored_on_the_first_allocation_its_type_offers(store):
    assert customPortfolio.anchorKey('USD', VARIANT, 'Moderate').toStr() == 'USD|Moderate|Full|0'
    assert customPortfolio.anchorKey('USD', 'PMG ESG', 'Moderate').toStr() == 'USD|Moderate|ex-Alts|0'
    assert customPortfolio.anchorKey('USD', VARIANT, 'All Equity').toStr() == 'USD|All Equity|NA|NA'
    assert customPortfolio.anchorKey('USD', VARIANT, 'Nonsense') is None
    anchor = _anchor()
    bands = customPortfolio.limits(anchor, [IG, 'Hybrid Fixed Income'])
    assert bands[IG]['soft'] == [anchor[IG] - 8, anchor[IG] + 8]
    assert bands['Hybrid Fixed Income'] == {'centre': 0.0, 'soft': [0.0, 8.0], 'hard': [0.0, 15.0]}


# --------------------------------------------------------------- the build ---

def test_a_build_holding_a_strategic_books_products_builds_the_same_rows(store):
    _, strategic = _strategic()
    built = customPortfolio.buildModel(_asBuild(strategic), 1e9, VARIANT, 'CASP', 'PMG Target', 1e9, 'USD')
    assert built['balanced'] and built['complete']
    mine = {i['productId']: i for g in built['groups'] for i in g['items']}
    theirs = {i['productId']: i for g in strategic['groups'] for i in g['items']}
    assert set(mine) == set(theirs)
    for pid, item in theirs.items():
        for field in ('printedPct', 'notional', 'managementFee', 'wtdFeeBp', 'initialPct',
                      'initialNotional', 'belowMinimum'):
            assert mine[pid].get(field) == pytest.approx(item.get(field)), (pid, field)
    assert built['total']['weightPct'] == pytest.approx(100.0)
    assert built['total']['wtdFeeBp'] == pytest.approx(strategic['total']['wtdFeeBp'])
    assert built['overlays']['rules'] == [], 'no overlay applies: the tilt and premium are products'
    assert {g['sleeve'] for g in built['groups']} == {'Custom portfolio'}


def test_a_build_is_checked_against_its_risk_profile(store):
    _, strategic = _strategic()
    build = customPortfolio.clean(_asBuild(strategic), VARIANT)
    anchor = _anchor()
    assert customPortfolio.checks(build, VARIANT, 1e9, anchor)['status'] == 'ok'
    # nine points out of the hedge funds and into equity: outside the usual
    # range, inside the outer one - an admin approves it
    equity = next(r['productId'] for r in build['products'] if r['category'] == PE)
    hedge = [r for r in build['products'] if r['category'] == 'Hedge Funds']
    for row in hedge:
        row['weightPct'] = round(row['weightPct'] / 2, 2)
    moved = round(sum(r['weightPct'] for r in hedge), 2)
    next(r for r in build['products'] if r['productId'] == equity)['weightPct'] += moved
    check = customPortfolio.checks(build, VARIANT, 1e9, anchor)
    assert check['status'] in ('warn', 'bad')
    # a build still being placed is not flagged for being under a range
    partial = {'profile': 'Moderate', 'products': [dict(build['products'][0], weightPct=10.0)]}
    first = customPortfolio.checks(partial, VARIANT, 1e9, anchor)
    assert first['wide'] == [] and not any('Outside' in p for p in first['problems'])
    assert any('adds to 10.00%' in p for p in first['problems'])
    # a product above the concentration limit, a weightless one, a small mandate
    heavy = {'profile': 'Moderate', 'products': [dict(build['products'][0], weightPct=30.0)]}
    assert any('at most 25%' in p for p in customPortfolio.checks(heavy, VARIANT, 1e9, anchor)['problems'])
    empty = {'profile': 'Moderate', 'products': [dict(build['products'][0], weightPct=None)]}
    assert any(p.startswith('Give ') for p in customPortfolio.checks(empty, VARIANT, 1e9, anchor)['problems'])
    small = customPortfolio.checks(build, VARIANT, 10e6, anchor)
    assert any('Private markets need' in p for p in small['problems'])


def test_a_build_holds_only_what_it_may(store):
    _, strategic = _strategic()
    build = _asBuild(strategic)
    clean = customPortfolio.clean(build, VARIANT)
    assert len(clean['products']) == len(build['products'])
    held = {p['productId'] for p in clean['products']}
    elsewhere = next(p['productId'] for p in products.all()
                     if p['productId'] not in customPortfolio.allowedIds(VARIANT))
    with pytest.raises(ValidationError) as exc:
        customPortfolio.clean(dict(build, products=build['products'] + [
            {'productId': elsewhere, 'weightPct': None, 'category': IG}]), VARIANT)
    assert 'may hold' in exc.value.message
    # kept where the stored build already holds it, so it can be swapped out
    kept = customPortfolio.clean(dict(build, products=[{'productId': elsewhere, 'weightPct': 1}]),
                                 VARIANT, keep={elsewhere: IG})
    assert kept['products'][0]['category'] == IG
    for bad in ([build['products'][0], build['products'][0]],
                [dict(build['products'][0], weightPct=1.234)],
                [dict(build['products'][0], weightPct=101)]):
        with pytest.raises(ValidationError):
            customPortfolio.clean({'profile': 'Moderate', 'products': bad}, VARIANT)
    with pytest.raises(ValidationError):
        customPortfolio.clean(dict(build, profile='Nonsense'), VARIANT)
    with pytest.raises(ValidationError):
        customPortfolio.clean(build, VARIANT, dict(customPortfolio.LIMITS, maxProducts=3))
    assert held


# ------------------------------------------------------- over the wire ---

def _client(monkeypatch):
    from fastapi.testclient import TestClient
    from cyrus_pmg.pmgService.isgPMGService import app
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice,bob,carol')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice,carol')
    return (TestClient(app), {'X-Kerberos': 'bob'}, {'X-Kerberos': 'alice'},
            {'X-Kerberos': 'carol'})


def _scenario(client, who, mandate=1e9):
    made = client.post('/api/v1/scenario', headers=who, json={
        'mandate': {'topAccountSize': mandate, 'mandateSize': mandate,
                    'primaryPwa': 'A. Castellanos — Madrid'},
        'basis': {'currency': 'USD', 'hedging': 'Hedged'}})
    sid = made.json()['id']
    assert client.put('/api/v1/scenario/' + sid, headers=who, json={'variant': VARIANT}).status_code == 200
    assert client.post('/api/v1/scenario/' + sid + '/portfolio', headers=who,
                       json={'key': KEY, 'role': 'base'}).status_code == 200
    return sid


def _put(client, who, sid, build, active=True):
    return client.put('/api/v1/scenario/' + sid, headers=who,
                      json={'customPortfolio': dict(build, active=active)})


def test_admins_build_under_any_type_and_pwas_where_it_is_open(store, monkeypatch):
    client, pwa, admin, _ = _client(monkeypatch)
    sid = _scenario(client, pwa)
    build = _asBuild(_strategic()[1])
    assert client.get('/api/v1/scenario/schema', headers=pwa).json()['customPortfolio']['mayBuild'] == []
    assert VARIANT in client.get('/api/v1/scenario/schema', headers=admin).json()['customPortfolio']['mayBuild']
    listed = client.get('/api/v1/scenario/custom-portfolio', headers=pwa, params={'variant': VARIANT}).json()
    assert listed['mayBuild'] is False and 'universe' not in listed
    assert [p['productId'] for p in listed['products']] == customPortfolio.allowedIds(VARIANT)
    assert all(p['category'] for p in listed['products'])
    assert listed['profiles']['Moderate']['key'] == 'USD|Moderate|Full|0'
    assert listed['lineStats'][IG]['ret'] > 0 and listed['library']
    refused = _put(client, pwa, sid, build)
    assert refused.status_code == 422 and 'built by an admin' in refused.json()['error']
    made = _put(client, admin, sid, build).json()['customPortfolio']
    assert made['active'] and made['builtBy'] == 'alice' and len(made['products']) == len(build['products'])
    # the PWA may not make it strategic again or change it while closed
    assert _put(client, pwa, sid, build, active=False).status_code == 422
    assert client.post('/api/v1/scenario/repository/custom-access', headers=pwa,
                       json={'open': True}).status_code == 403
    assert client.post('/api/v1/scenario/repository/custom-access', headers=admin,
                       json={'open': True}).status_code == 200
    assert client.get('/api/v1/scenario/schema', headers=pwa).json()['customPortfolio']['mayBuild']
    back = _put(client, pwa, sid, build, active=False).json()
    assert back['customPortfolio']['active'] is False and back['base'] == 'USD|Moderate|Full|0'
    assert back['customPortfolio']['builtBy'] == 'alice', 'switching kind is not building'
    again = _put(client, pwa, sid, dict(build, profile='ModAgg')).json()['customPortfolio']
    assert again['active'] and again['profile'] == 'ModAgg' and again['builtBy'] == 'bob'
    # a new type keeps the build
    other = 'PMG ESG'
    kept = client.put('/api/v1/scenario/' + sid, headers=pwa, json={'variant': other}).json()
    assert kept['customPortfolio']['products'] == again['products']


def test_outside_the_usual_range_an_admin_who_did_not_build_it_approves_it(store, monkeypatch):
    client, pwa, admin, other = _client(monkeypatch)
    client.post('/api/v1/scenario/repository/custom-access', headers=admin, json={'open': True})
    sid = _scenario(client, pwa)
    _, strategic = _strategic()
    build = _asBuild(strategic)
    ig = [r for r in build['products'] if r['category'] == IG]
    pe = [r for r in build['products'] if r['category'] == PE]
    # ten points from Investment Grade into Public Equity
    shift = 10.0
    for row in ig:
        row['weightPct'] = round(row['weightPct'] * (1 - shift / sum(r['weightPct'] for r in ig)), 2)
    gap = round(100 - sum(r['weightPct'] for r in build['products']), 2)
    for row in pe:
        row['weightPct'] = round(row['weightPct'] + gap / len(pe), 2)
    build['products'][-1]['weightPct'] = round(
        build['products'][-1]['weightPct'] + 100 - sum(r['weightPct'] for r in build['products']), 2)
    assert _put(client, pwa, sid, build).status_code == 200
    blocked = client.post('/api/v1/scenario/' + sid + '/export', headers=pwa)
    assert blocked.status_code == 422 and 'approves it' in blocked.json()['error'], blocked.text
    approval = '/api/v1/scenario/' + sid + '/custom-portfolio/approval'
    assert client.post(approval, headers=admin, json={'action': 'approve'}).status_code == 422
    submitted = client.post(approval, headers=pwa, json={'action': 'submit', 'note': 'Client view.'}).json()
    assert submitted['customPortfolio']['approval']['state'] == 'submitted'
    awaiting = client.get('/api/v1/scenario/repository/custom-portfolios/awaiting', headers=admin).json()
    assert [a['scenarioId'] for a in awaiting['awaiting']] == [sid]
    assert awaiting['awaiting'][0]['note'] == 'Client view.'
    assert client.get('/api/v1/scenario/repository/custom-portfolios/awaiting', headers=pwa).status_code == 403
    # sent back with a reason, resubmitted, approved by an admin
    assert client.post(approval, headers=admin, json={'action': 'return'}).status_code == 422
    returned = client.post(approval, headers=admin, json={'action': 'return', 'note': 'Less equity.'}).json()
    assert returned['customPortfolio']['approval']['reason'] == 'Less equity.'
    client.post(approval, headers=pwa, json={'action': 'submit'})
    approved = client.post(approval, headers=admin, json={'action': 'approve'}).json()
    assert approved['customPortfolio']['approval']['state'] == 'approved'
    # a change voids it, and says so
    changed = _put(client, pwa, sid, dict(build, profile='ModAgg')).json()['customPortfolio']
    assert changed['approval'] is None and 'no longer applies' in changed['notice']
    _put(client, pwa, sid, build)
    client.post(approval, headers=pwa, json={'action': 'submit'})
    client.post(approval, headers=other, json={'action': 'approve'})
    # an admin who built it is not its approver
    tweaked = json.loads(json.dumps(build))
    _move(tweaked, pe[0]['productId'], pe[1]['productId'], 0.01)
    assert _put(client, admin, sid, tweaked).json()['customPortfolio']['builtBy'] == 'alice'
    assert client.post(approval, headers=admin, json={'action': 'submit'}).status_code == 200
    mine = client.post(approval, headers=admin, json={'action': 'approve'})
    assert mine.status_code == 422 and 'another admin' in mine.json()['error']
    assert client.post(approval, headers=other, json={'action': 'approve'}).status_code == 200
    delivered = client.post('/api/v1/scenario/' + sid + '/export', headers=pwa)
    assert delivered.status_code == 200, delivered.text
    record = proposalRegister.getProposal(delivered.headers['x-proposal-id'])
    assert record['customPortfolio']['approvedBy'] == 'carol'
    assert record['customPortfolio']['builtBy'] == 'alice'


def test_a_custom_portfolio_is_delivered_with_its_own_allocation_and_recorded(store, monkeypatch):
    client, pwa, admin, _ = _client(monkeypatch)
    sid = _scenario(client, pwa)
    client.post('/api/v1/scenario/' + sid + '/portfolio', headers=pwa,
                json={'key': dict(KEY, riskLevel='ModAgg'), 'role': 'comparison'})
    _, strategic = _strategic()
    build = _asBuild(strategic)
    assert _put(client, admin, sid, build).status_code == 200
    client.put('/api/v1/scenario/' + sid, headers=pwa, json={
        'includeFees': True, 'feeSchedule': 'CASP',
        'otherCharges': {'fxHighBps': 10, 'fxMidBps': 10, 'fxLowBps': 20, 'equityTradeBps': 35}})
    delivered = client.post('/api/v1/scenario/' + sid + '/export', headers=pwa)
    assert delivered.status_code == 200, delivered.text
    uid = delivered.headers['x-proposal-id']
    assert 'PMG_Scenario_Custom_USD_Hedged_' in delivered.headers['content-disposition']
    bundle = zipfile.ZipFile(io.BytesIO(delivered.content))
    import openpyxl
    book = openpyxl.load_workbook(io.BytesIO(bundle.read(next(n for n in bundle.namelist()
                                                                  if n.endswith('.xlsx')))))
    shown = [s for s in book.sheetnames if book[s].sheet_state == 'visible']
    assert shown[:2] == ['Custom Allocation', 'Implementation'] and 'portfolios' not in shown
    sheet = book['Custom Allocation']
    assert sheet['B1'].value == 'Custom portfolio (Moderate risk profile)'
    assert sheet['C1'].value == 'USD Moderate Full' and sheet['D1'].value == 'USD Moderate-Aggressive Full'
    labels = [sheet.cell(row=r, column=1).value for r in range(1, sheet.max_row + 1)]
    assert IG in labels and 'TOTAL' in labels
    assert any(str(v).startswith('Built product by product with the Moderate risk profile by alice')
               for v in labels if v)
    impl = book['Implementation']
    preamble = {impl.cell(row=r, column=1).value: impl.cell(row=r, column=2).value for r in range(1, 8)}
    assert preamble['Portfolio'] == 'Custom, Moderate risk profile'
    # the deck: the custom allocation, no strategic or risk slides
    from test_scenario_backend import PORT
    model = customPortfolio.buildModel(build, 1e9, VARIANT)
    model['customPortfolio'] = {'profileLabel': 'Moderate', 'figures': {}, 'order': customPortfolio.lines(),
                                'compared': []}
    slides = pptWriter.planDeck(BASIS, type('M', (), {'mandateSize': 1e9})(), [], model, cover=False)
    headings = [s['heading'] for s in slides]
    assert headings[0] == 'Custom Allocation' and 'Implemented Model' in headings
    assert not any('Strategic' in h or 'Risk' in h or 'Assumptions' in h for h in headings)
    # the record
    record = proposalRegister.getProposal(uid)
    assert record['baseKey'] == '' and record['sleeves'] == []
    assert record['customPortfolio']['profile'] == 'Moderate'
    assert record['customPortfolio']['comparisons'] == ['USD|Moderate|Full|0', 'USD|ModAgg|Full|0']
    assert record['allocation']['kind'] == 'custom'
    assert {c['name'] for c in record['allocation']['categories']} >= {IG, PE}
    assert proposalRegister.viewCounts()['portfolios'] == 1
    assert proposalRegister.listProposals(portfolioOnly=True)['total'] == 1
    row = dict(zip(proposalRegister.EXPORT_COLUMNS, proposalRegister.exportRows(portfolioOnly=True)[0]))
    assert row['BasePortfolio'] == 'Custom' and row['CustomPortfolio'].startswith('Moderate risk profile')
    found = client.get('/api/v1/scenario/proposals/' + uid, headers=pwa).json()['proposal']
    assert (found['riskLevel'], found['allocationType'], found['custom']) == ('Moderate', 'Custom', True)


def test_a_build_outside_its_limits_or_unfinished_is_not_delivered(store, monkeypatch):
    client, pwa, admin, _ = _client(monkeypatch)
    sid = _scenario(client, pwa)
    build = _asBuild(_strategic()[1])
    short = dict(build, products=build['products'][1:])
    assert _put(client, admin, sid, short).status_code == 200
    refused = client.post('/api/v1/scenario/' + sid + '/export', headers=pwa)
    assert refused.status_code == 422 and 'not 100%' in refused.json()['error']
    approval = '/api/v1/scenario/' + sid + '/custom-portfolio/approval'
    nothing = client.post(approval, headers=admin, json={'action': 'submit'})
    assert nothing.status_code == 422 and 'cannot be delivered yet' in nothing.json()['error']
    _put(client, admin, sid, build)
    fine = client.post(approval, headers=admin, json={'action': 'submit'})
    assert fine.status_code == 422 and 'needs no approval' in fine.json()['error']


# ------------------------------------------------- the page agrees ---

def _pageHelpers():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, '..', '..', 'generator', 'js', 'customPortfolio.js'), encoding='utf-8') as fh:
        js = fh.read()
    return js, js[js.index('/* custom-portfolio-helpers-begin'):js.index('/* custom-portfolio-helpers-end */')]


def test_the_pages_checks_and_figures_agree_with_the_server(store):
    import subprocess
    from test_scenario_backend import PORT
    _, strategic = _strategic()
    good = customPortfolio.clean(_asBuild(strategic), VARIANT)
    equity = [r for r in good['products'] if r['category'] == PE]
    hedge = [r for r in good['products'] if r['category'] == 'Hedge Funds']
    wide = json.loads(json.dumps(good))
    # the hedge funds' ten points into equity: hedge funds under their usual
    # range, equity over it, both within the outer limits
    wide['products'] = [r for r in wide['products'] if r['category'] != 'Hedge Funds']
    moved = round(100 - sum(r['weightPct'] for r in wide['products']), 2)
    rows = [r for r in wide['products'] if r['category'] == PE]
    for row in rows:
        row['weightPct'] = round(row['weightPct'] + round(moved / len(rows), 2), 2)
    rows[0]['weightPct'] = round(rows[0]['weightPct'] + 100 - sum(r['weightPct'] for r in wide['products']), 2)
    partial = {'profile': 'Moderate', 'products': [dict(good['products'][0], weightPct=10.0)]}
    heavy = {'profile': 'Moderate', 'products': [dict(good['products'][0], weightPct=30.0),
                                                 dict(good['products'][1], weightPct=None)]}
    builds = {'good': good, 'wide': wide, 'partial': partial, 'heavy': heavy,
              'empty': {'profile': 'Moderate', 'products': []}}
    anchor = _anchor()
    allowed = set(customPortfolio.allowedIds(VARIANT))
    ctx = {'lines': customPortfolio.lines(), 'anchor': anchor, 'limits': dict(customPortfolio.LIMITS),
           'countedIn': customPortfolio.countedIn(),
           'allowed': {p['productId']: True for p in products.all() if p['productId'] in allowed},
           'liquidity': {p['productId']: p['liquidity'] for p in products.all()},
           'label': {p['productId']: p['name'] for p in products.all()},
           'mandateSize': 1e9, 'privateMinimum': rules.PRIVATE_ASSETS_MINIMUM,
           'privateLine': rules.PRIVATE_FUNDED_FROM}
    stats = customPortfolio.estimates(PORT, BASIS, VARIANT)
    result = customPortfolio.anchorFor(PORT, BASIS, VARIANT, 'Moderate')
    _, helpers = _pageHelpers()
    script = helpers + """
var builds = %s, ctx = %s, stats = %s, anchor = %s, vol = %s;
var out = {};
Object.keys(builds).forEach(function (k) { out[k] = cpCheck(builds[k].products, ctx); });
out.small = cpCheck(builds.good.products, Object.assign({}, ctx, {mandateSize: 10000000}));
out.figures = cpFigures(cpWeightsByLine(builds.wide.products), stats, anchor, vol);
out.parse = ['12.5', '12.50%%', ' 7 ', '.5', '1.234', '0', '101', 'x', ''].map(function (t) {
  var v = cpParse(t); return v === null ? 'none' : isNaN(v) ? 'bad' : v; });
out.scaled = cpScaleTo([45, 25, 20, 10], 25.9);
console.log(JSON.stringify(out));
""" % (json.dumps(builds), json.dumps(ctx), json.dumps(stats), json.dumps(anchor),
       json.dumps(result['metrics']['volatilityPct']))
    done = subprocess.run(['node', '-e', script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    for name, build in list(builds.items()) + [('small', good)]:
        server = customPortfolio.checks(build, VARIANT, 10e6 if name == 'small' else 1e9, anchor)
        page = got[name]
        assert page['status'] == server['status'], name
        assert page['problems'] == server['problems'], name
        assert page['wide'] == server['wide'], name
        assert set(page['lines']) == set(server['lines']), name
        for line, band in server['lines'].items():
            assert page['lines'][line]['soft'] == pytest.approx(band['soft']), (name, line)
            assert page['lines'][line]['weightPct'] == pytest.approx(band['weightPct']), (name, line)
    assert got['good']['status'] == 'ok' and got['wide']['status'] == 'warn'
    assert got['empty']['lines'], 'a blank slate shows what the profile allows'
    figures = customPortfolio.figures(customPortfolio.weightsByLine(wide), stats, result)
    for key in ('estimatedReturnPct', 'volatilityLimitPct', 'volatilityPct'):
        assert got['figures'][key] == pytest.approx(figures[key])
    assert got['parse'] == [12.5, 12.5, 7, 0.5, 'bad', 0, 'bad', 'bad', 'none']
    assert got['scaled'] == [11.66, 6.47, 5.18, 2.59]


def test_the_page_offers_custom_where_the_person_may_build():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.join(here, '..', '..', 'generator')
    js, _ = _pageHelpers()
    core = open(os.path.join(root, 'js', 'core.js'), encoding='utf-8').read()
    shell = open(os.path.join(root, 'build_styles.py'), encoding='utf-8').read()
    impl = open(os.path.join(root, 'implementation.py'), encoding='utf-8').read()
    assert "'<optgroup label=\"Build it yourself\"><option value=\"__custom__\"'" in core
    assert "(opt('customPortfolio.mayBuild', []) || []).indexOf(state.variant) !== -1" in core
    assert "body: JSON.stringify({ customPortfolio: { active: !!next.active, profile: next.profile," in core
    assert '<div id="cpDialog" hidden></div>' in shell
    assert "_asset('implementation.js') + '\\n' + _asset('customPortfolio.js')" in impl
    assert 'var CP_BALANCE = {};'.format(customPortfolio.BALANCE_TOLERANCE) in js
