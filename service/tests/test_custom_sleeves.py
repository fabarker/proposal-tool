"""Custom sleeves (D170): a sleeve built for one proposal from the products its
category's library sleeves hold, weighted as a share of the sleeve or of the
whole portfolio. Admins always build them; PWAs where an admin has opened the
category. A custom sleeve that moves a category's weight takes the strategic
allocation out of the delivered files, and the book is not delivered until it
adds to 100% again."""
import io
import json
import os
import subprocess
import zipfile

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import (customSleeves, feeCard, pptWriter, products,
                                           proposalRegister, repositoryFeed, rules, sleeveRepo,
                                           sleeves)
from cyrus_pmg.pmgService.scenario.types import ValidationError
from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows

VARIANT = 'PMG Multi-Asset Portfolio'
IG = 'Investment Grade Fixed Income'
PE = 'Public Equity'
HYBRID = 'Hybrid Fixed Income'
KEY = {'currency': 'USD', 'riskLevel': 'Moderate', 'allocationType': 'Full', 'excludeRealAssets': False}


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    products.reload()
    feeCard._forget()
    yield tmp_path
    products.reload()
    feeCard._forget()


def _book():
    from test_initial_allocation import _book as book, _sleeves
    _, result = book('USD', 'USD|Moderate|Full|0')
    return result, _sleeves(result)


def _model(result, sleevesMap, custom=None):
    return buildImplementationRows(result, sleevesMap, rules.autoSleeveCategories(), 1e9, VARIANT,
                                   True, 'CASP', 'PMG Target', 1e9, True, 'USD', customSleeves=custom)


def _group(model, name):
    return next(g for g in model['groups'] if g['category'] == name)


def _asCustom(sleeve, mode='sleeve', scale=100.0, name='Mine'):
    """A library sleeve's own products and weights as a custom sleeve."""
    return {'name': name, 'note': '', 'mode': mode,
            'products': [{'productId': p['productId'], 'weightPct': p['weight'] * scale}
                         for p in sleeve['products']]}


def _library(name, category=IG):
    return next(s for s in sleeves.listSleeves(category, VARIANT) if s['name'] == name)


# ------------------------------------------------------------ who builds ---

def test_only_admins_build_until_the_custom_workflows_are_opened_to_pwas(store):
    from cyrus_pmg.pmgService.scenario import customAccess
    assert not hasattr(customSleeves, 'settings') and 'maxProducts' not in customSleeves.describe()
    assert customSleeves.mayBuild(IG, admin=True) and not customSleeves.mayBuild(IG, admin=False)
    assert not customSleeves.mayBuild(HYBRID, admin=True), 'an overlay fills it'
    # one switch, for custom sleeves and custom portfolios alike (D172)
    assert customAccess.settings()['pwas'] is False
    opened = customAccess.setPwas(True, user='alice')
    assert opened['pwas'] is True and opened['updatedBy'] == 'alice'
    assert customSleeves.mayBuild(IG, admin=False) and not customSleeves.mayBuild(HYBRID, admin=False)
    with pytest.raises(ValidationError) as exc:
        customAccess.setPwas(True, user='alice')
    assert exc.value.message == 'That is the setting already in force.'
    customAccess.setPwas(False, user='carol')
    assert not customSleeves.mayBuild(IG, admin=False)
    feed = repositoryFeed.activity(areas=['access'], limit=10)['entries']
    # both land in one second, where the order is not the point
    assert {(e['area'], e['actor'], e['changes'][0]) for e in feed} == {
        ('access', 'carol', 'Custom sleeves and custom portfolios closed to PWAs'),
        ('access', 'alice', 'Custom sleeves and custom portfolios opened to PWAs')}


def test_a_category_offers_the_products_its_sleeves_hold_under_the_type(store):
    held = {p['productId'] for s in sleeveRepo.listAll(VARIANT) if s['category'] == IG
            for p in s['products']}
    assert set(customSleeves.categoryProductIds(IG, VARIANT)) == held
    # a disabled sleeve's products stay on the list; an archived one's go
    only = {}
    for s in sleeveRepo.listAll(VARIANT):
        if s['category'] == IG:
            for p in s['products']:
                only.setdefault(p['productId'], []).append(s['id'])
    pid, (sid,) = next((p, ids) for p, ids in only.items() if len(ids) == 1)
    sleeveRepo.setOffered(sid, False, user='alice')
    assert pid in customSleeveProductIds()
    sleeveRepo.deleteSleeve(sid, user='alice')
    assert pid not in customSleeveProductIds()


def customSleeveProductIds():
    return customSleeves.categoryProductIds(IG, VARIANT)


# ------------------------------------------------------------- the checks ---

def test_a_custom_sleeve_is_checked_as_a_library_sleeve_is_and_more(store):
    ids = customSleeves.categoryProductIds(IG, VARIANT)
    good = {'name': '  IG   mine ', 'note': ' a  note ', 'mode': 'sleeve',
            'products': [{'productId': ids[0], 'weightPct': 60}, {'productId': ids[1], 'weightPct': 40}]}
    cleaned = customSleeves.clean(IG, good, VARIANT)
    assert cleaned == {'name': 'IG mine', 'note': 'a note', 'mode': 'sleeve',
                       'products': [{'productId': ids[0], 'weightPct': 60.0},
                                    {'productId': ids[1], 'weightPct': 40.0}]}

    def refused(change, words, category=IG):
        bad = json.loads(json.dumps(good))
        change(bad)
        with pytest.raises(ValidationError) as exc:
            customSleeves.clean(category, bad, VARIANT)
        assert words in exc.value.message, exc.value.message

    refused(lambda d: d.update(mode='half'), 'percentage of the sleeve or of the portfolio')
    refused(lambda d: d.update(name='x' * 81), 'at most 80')
    refused(lambda d: d.update(products=[]), 'has no products')
    refused(lambda d: d['products'].append(dict(d['products'][0])), 'twice')
    refused(lambda d: d['products'][0].update(productId='gs-us-equity-insights-fund'),
            'is not one of the products')
    refused(lambda d: d['products'][0].update(weightPct=0), 'weight above zero')
    refused(lambda d: d['products'][0].update(weightPct='60'), 'weight above zero')
    refused(lambda d: d['products'][0].update(weightPct=59), 'adds to 99.00%')
    refused(lambda d: d.update(mode='portfolio', products=[{'productId': ids[0], 'weightPct': 101}]),
            'more than all of it')
    refused(lambda d: None, 'carries its sleeve automatically', category=HYBRID)
    # as many of the category's products as the person likes (D173)
    share = round(100 / len(ids), 4)
    every = [{'productId': p, 'weightPct': share} for p in ids]
    every[0]['weightPct'] = round(100 - share * (len(ids) - 1), 4)
    assert len(customSleeves.clean(IG, dict(good, products=every), VARIANT)['products']) == len(ids)
    portfolio = customSleeves.clean(IG, dict(good, mode='portfolio', products=[
        {'productId': ids[0], 'weightPct': 12.5}]), VARIANT)
    assert portfolio['mode'] == 'portfolio' and customSleeves.total(portfolio) == 12.5
    assert customSleeves.displayName(portfolio) == 'Custom: IG mine'
    assert customSleeves.displayName({'name': ''}) == 'Custom'


# --------------------------------------------------------------- the model ---

def test_a_custom_sleeve_holding_a_library_sleeves_products_builds_the_same_book(store):
    result, chosen = _book()
    library = _library(chosen[IG])
    withLibrary = _model(result, chosen)
    asCustom = dict(chosen)
    del asCustom[IG]
    withCustom = _model(result, asCustom, {IG: _asCustom(library)})
    # the same answer both ways: every row, weight, fee and minimum
    fields = ('productId', 'printedPct', 'notional', 'managementFee', 'wtdFeeBp', 'belowMinimum',
              'initialPct', 'initialNotional')
    flat = lambda m: [(g['category'], g['weightPct']) + tuple(i.get(f) for f in fields)
                      for g in m['groups'] for i in g['items']]
    assert flat(withCustom) == flat(withLibrary)
    assert withCustom['total'] == withLibrary['total']
    group = _group(withCustom, IG)
    assert group['sleeve'] == 'Custom: Mine' and group['custom']['mode'] == 'sleeve'
    assert withCustom['balanced'] and not withCustom['departs']
    # a library name chosen as well wins: the custom sleeve waits
    assert _group(_model(result, chosen, {IG: _asCustom(library)}), IG)['sleeve'] == library['name']


def test_weights_against_the_portfolio_set_the_category_and_nothing_else_moves(store):
    result, chosen = _book()
    library = _library(chosen[IG])
    base = _model(result, chosen)
    strategic = _group(base, IG)['weightPct']
    customs = dict(chosen)
    del customs[IG]
    # the same weights as a share of the portfolio: the same book again
    same = _model(result, customs, {IG: _asCustom(library, 'portfolio', strategic)})
    assert [(g['category'], g['weightPct']) for g in same['groups']] == \
        [(g['category'], pytest.approx(g['weightPct'])) for g in base['groups']]
    assert same['balanced'] and not same['departs']
    # five points fewer: the category is what was typed, the rest stays, and
    # the book is short until someone puts the five points somewhere
    less = _model(result, customs, {IG: _asCustom(library, 'portfolio', strategic - 5)})
    assert _group(less, IG)['weightPct'] == pytest.approx(strategic - 5)
    assert _group(less, IG)['strategicWeightPct'] == pytest.approx(strategic)
    assert _group(less, PE)['weightPct'] == pytest.approx(_group(base, PE)['weightPct'])
    assert not less['balanced'] and less['departs']
    assert less['implementedPct'] == pytest.approx(95.0)
    # Public Equity takes them, as a custom sleeve weighted the same way
    equity = _library(chosen[PE], PE)
    peWeight = _group(base, PE)['weightPct']
    both = dict(customs)
    del both[PE]
    whole = _model(result, both, {IG: _asCustom(library, 'portfolio', strategic - 5),
                                  PE: _asCustom(equity, 'portfolio', peWeight + 5)})
    assert whole['balanced'] and whole['departs']
    assert sum(i['printedPct'] for g in whole['groups'] for i in g['items']) == pytest.approx(100.0)
    # one hundredth short is short
    nearly = _model(result, both, {IG: _asCustom(library, 'portfolio', strategic - 5),
                                   PE: _asCustom(equity, 'portfolio', peWeight + 4.99)})
    assert not nearly['balanced']


def test_a_book_off_its_strategic_allocation_is_delivered_without_it(store):
    result, chosen = _book()
    library, equity = _library(chosen[IG]), _library(chosen[PE], PE)
    base = _model(result, chosen)
    customs = dict(chosen)
    del customs[IG], customs[PE]
    model = _model(result, customs, {
        IG: _asCustom(library, 'portfolio', _group(base, IG)['weightPct'] - 2),
        PE: _asCustom(equity, 'portfolio', _group(base, PE)['weightPct'] + 2)})
    from cyrus_pmg.pmgService.scenario import assetEstimates
    from cyrus_pmg.pmgService.scenario.types import BasisInput, MandateInput
    from cyrus_pmg.pmgService.scenario.workbook import writeWorkbook
    import openpyxl
    basis = BasisInput(currency='USD', hedging='Hedged')
    mandate = MandateInput(topAccountSize=1e9, mandateSize=1e9, primaryPwa='A. Castellanos — Madrid', entity='gsi')
    args = (basis, mandate, [result], customs, rules.autoSleeveCategories(), VARIANT, True, 'CASP',
            'PMG Target', True, True)
    book = openpyxl.load_workbook(io.BytesIO(writeWorkbook(
        *args, assets=assetEstimates.forSlice('USD', 'Hedged'), model=model)))
    assert 'portfolios' not in book.sheetnames and 'risk_dashboard' not in book.sheetnames
    assert 'assumptions' not in book.sheetnames and book.sheetnames[0] == 'Implementation'
    slides = pptWriter.planDeck(basis, mandate, [result], model,
                                assetEstimates.forSlice('USD', 'Hedged'))
    headings = [s.get('heading') for s in slides]
    assert not any('Strategic' in (h or '') or 'Risk' in (h or '') or 'Assumptions' in (h or '')
                   for h in headings), headings
    assert 'Implemented Model' in headings
    # unchanged weights keep every page
    kept = pptWriter.planDeck(basis, mandate, [result], base, assetEstimates.forSlice('USD', 'Hedged'))
    assert any('Strategic' in (s.get('heading') or '') for s in kept)


# ------------------------------------------------------- over the wire ---

def _client(monkeypatch):
    from fastapi.testclient import TestClient
    from cyrus_pmg.pmgService.isgPMGService import app
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice,bob')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    return TestClient(app), {'X-Kerberos': 'bob'}, {'X-Kerberos': 'alice'}


def _scenario(client, who):
    from test_scenario_backend import BASIS, PORT, _sleeveMap
    from cyrus_pmg.pmgService.scenario.types import PortfolioKey
    made = client.post('/api/v1/scenario', headers=who, json={
        'mandate': {'topAccountSize': 1e9, 'mandateSize': 1e9, 'primaryPwa': 'A. Castellanos — Madrid', 'entity': 'gsi'},
        'basis': {'currency': 'USD', 'hedging': 'Hedged'}})
    sid = made.json()['id']
    assert client.put('/api/v1/scenario/' + sid, headers=who, json={'variant': VARIANT}).status_code == 200
    assert client.post('/api/v1/scenario/' + sid + '/portfolio', headers=who,
                       json={'key': KEY, 'role': 'base'}).status_code == 200
    result = PORT.resolve_portfolio(BASIS, PortfolioKey.fromDict(KEY))
    chosen = _sleeveMap(result['categories'], VARIANT)
    assert client.put('/api/v1/scenario/' + sid, headers=who, json={'sleeves': chosen}).status_code == 200
    return sid, chosen


def test_admins_build_anywhere_and_pwas_where_the_category_is_open(store, monkeypatch):
    client, pwa, admin = _client(monkeypatch)
    sid, chosen = _scenario(client, pwa)
    library = _library(chosen[IG])
    custom = {IG: _asCustom(library)}
    library_less = {k: v for k, v in chosen.items() if k != IG}
    # the schema and the product list say where each may build
    assert client.get('/api/v1/scenario/schema', headers=pwa).json()['customSleeves']['mayBuild'] == []
    assert IG in client.get('/api/v1/scenario/schema', headers=admin).json()['customSleeves']['mayBuild']
    listed = client.get('/api/v1/scenario/products', headers=pwa,
                        params={'category': IG, 'variant': VARIANT}).json()
    assert listed['mayBuild'] is False and 'maxProducts' not in listed
    assert [p['productId'] for p in listed['products']] == customSleeves.categoryProductIds(IG, VARIANT)
    # a PWA in a closed category: refused, nothing written
    refused = client.put('/api/v1/scenario/' + sid, headers=pwa,
                         json={'sleeves': library_less, 'customSleeves': custom})
    assert refused.status_code == 422 and 'built by an admin' in refused.json()['error']
    # an admin builds it; the PWA keeps and delivers it unchanged
    built = client.put('/api/v1/scenario/' + sid, headers=admin,
                       json={'sleeves': library_less, 'customSleeves': custom}).json()
    assert built['customSleeves'][IG]['builtBy'] == 'alice' and IG not in built['sleeves']
    kept = client.put('/api/v1/scenario/' + sid, headers=pwa,
                      json={'customSleeves': built['customSleeves']})
    assert kept.status_code == 200 and kept.json()['customSleeves'][IG]['builtBy'] == 'alice'
    changed = json.loads(json.dumps(built['customSleeves']))
    changed[IG]['name'] = 'Bob\'s'
    assert client.put('/api/v1/scenario/' + sid, headers=pwa,
                      json={'customSleeves': changed}).status_code == 422
    # opened to PWAs: the PWA may change it, and is named as its builder (D172)
    assert client.post('/api/v1/scenario/repository/custom-access', headers=pwa,
                       json={'open': True}).status_code == 403
    assert client.post('/api/v1/scenario/repository/custom-access', headers=admin,
                       json={'open': True}).status_code == 200
    assert IG in client.get('/api/v1/scenario/schema', headers=pwa).json()['customSleeves']['mayBuild']
    mine = client.put('/api/v1/scenario/' + sid, headers=pwa, json={'customSleeves': changed}).json()
    assert mine['customSleeves'][IG]['name'] == 'Bob\'s' and mine['customSleeves'][IG]['builtBy'] == 'bob'
    # a fixed category takes none, from anyone
    fixed = dict(changed, **{HYBRID: changed[IG]})
    assert client.put('/api/v1/scenario/' + sid, headers=admin,
                      json={'customSleeves': fixed}).status_code == 422
    # a new type clears them with the library choices
    other = sleeves.VARIANTS[1]
    cleared = client.put('/api/v1/scenario/' + sid, headers=pwa, json={'variant': other}).json()
    assert cleared['customSleeves'] == {} and cleared['sleeves'] == {}


def test_a_book_that_does_not_add_to_100_is_not_delivered_and_one_that_does_is_recorded(store, monkeypatch):
    client, pwa, admin = _client(monkeypatch)
    sid, chosen = _scenario(client, pwa)
    result, _ = _book()
    base = _model(result, chosen)
    ig, pe = _group(base, IG)['weightPct'], _group(base, PE)['weightPct']
    rest = {k: v for k, v in chosen.items() if k not in (IG, PE)}
    customs = {IG: _asCustom(_library(chosen[IG]), 'portfolio', round(ig - 3, 2), 'Less IG'),
               PE: _asCustom(_library(chosen[PE], PE), 'portfolio', round(pe + 2, 2))}
    for c in customs.values():
        for p in c['products']:
            p['weightPct'] = round(p['weightPct'], 2)
    assert client.put('/api/v1/scenario/' + sid, headers=admin,
                      json={'sleeves': rest, 'customSleeves': customs}).status_code == 200
    # the notice the hedge funds it holds ask for (D198)
    assert client.put('/api/v1/scenario/' + sid, headers=pwa, json={'hedgeFundNotice': {'action': 'confirm', 'attested': True, 'category': 'Hedge Funds', 'sleeve': ''}}).status_code == 200
    short = client.post('/api/v1/scenario/' + sid + '/export', headers=pwa)
    assert short.status_code == 422 and 'not 100%' in short.json()['error'], short.text
    # the missing point goes to Public Equity
    total = sum(p['weightPct'] for p in customs[PE]['products'])
    gap = round(100 - (sum(g['weightPct'] for g in base['groups']) - ig - pe)
                - sum(p['weightPct'] for p in customs[IG]['products']) - total, 2)
    customs[PE]['products'][0]['weightPct'] = round(customs[PE]['products'][0]['weightPct'] + gap, 2)
    assert client.put('/api/v1/scenario/' + sid, headers=admin,
                      json={'customSleeves': customs}).status_code == 200
    delivered = client.post('/api/v1/scenario/' + sid + '/export', headers=pwa)
    assert delivered.status_code == 200, delivered.text
    uid = delivered.headers['x-proposal-id']
    names = zipfile.ZipFile(io.BytesIO(delivered.content)).namelist()
    import openpyxl
    sheets = openpyxl.load_workbook(io.BytesIO(zipfile.ZipFile(io.BytesIO(delivered.content)).read(
        next(n for n in names if n.endswith('.xlsx'))))).sheetnames
    assert 'portfolios' not in sheets and 'Implementation' in sheets
    # the record: custom pins without a library version, what they held, and
    # that the strategic allocation was left out
    record = proposalRegister.getProposal(uid)
    assert record['departs'] is True
    pins = {p['category']: p for p in record['sleeves']}
    assert pins[IG]['custom']['mode'] == 'portfolio' and pins[IG]['sleeveId'] is None
    assert pins[IG]['sleeve'] == 'Custom: Less IG' and pins[PE]['sleeve'] == 'Custom: Mine'
    picture = {g['category']: g for g in record['implemented']}
    assert picture[IG]['strategicWeightPct'] == pytest.approx(ig)
    assert picture[IG]['custom']['builtBy'] == 'alice'
    assert proposalRegister.viewCounts()['custom'] == 1
    assert proposalRegister.listProposals(customOnly=True)['total'] == 1
    assert uid not in proposalRegister.movedCounts()
    row = dict(zip(proposalRegister.EXPORT_COLUMNS, proposalRegister.exportRows(customOnly=True)[0]))
    assert '{}=Custom: Less IG (custom)'.format(IG) in row['Sleeves']
    assert row['CustomSleeves'].startswith('{}: '.format(IG)) and row['StrategicAllocation'] == 'Left out'
    summary = client.get('/api/v1/scenario/account-requests/' + uid, headers=pwa)
    if summary.status_code == 200:
        assert any(s['custom'] for s in summary.json()['proposal']['sleeves'])


def test_a_register_from_before_custom_sleeves_gains_the_columns(store):
    import sqlite3
    path = str(store / 'proposals.db')
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE proposalSleeves (proposalId TEXT NOT NULL, category TEXT NOT NULL, '
                 'sleeveId INTEGER, revision INTEGER, sleeveName TEXT NOT NULL, '
                 'PRIMARY KEY (proposalId, category))')
    conn.commit()
    conn.close()
    opened = proposalRegister._connect()
    pins = {r[1] for r in opened.execute('PRAGMA table_info(proposalSleeves)')}
    columns = {r[1] for r in opened.execute('PRAGMA table_info(proposals)')}
    opened.close()
    assert 'custom' in pins and 'departs' in columns


# ------------------------------------------------- the page agrees ---

def test_the_pages_helpers_agree_with_the_model(store):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, '..', '..', 'generator', 'js', 'implementation.js'), encoding='utf-8') as fh:
        js = fh.read()
    start = js.index('/* custom-sleeve-helpers-begin')
    end = js.index('/* custom-sleeve-helpers-end */')
    helpers = js[start:end]
    script = helpers + """
var lines = [{name: 'A', weightPct: 25.9}, {name: 'B', weightPct: 38}, {name: 'C', weightPct: 36.1}];
var custom = {A: {mode: 'portfolio', products: [{productId: 'x', weightPct: 6.65}, {productId: 'y', weightPct: 14.24}]},
              B: {mode: 'portfolio', products: [{productId: 'z', weightPct: 43.01}]},
              C: {mode: 'sleeve', products: [{productId: 'w', weightPct: 100}]}};
var out = implementLines(lines, {}, custom);
var withLibrary = implementLines(lines, {A: 'Lib'}, custom);
console.log(JSON.stringify({
  weights: out.map(function (l) { return l.weightPct; }),
  strategic: out.map(function (l) { return l.strategicWeightPct; }),
  balance: balanceOf(out), library: balanceOf(withLibrary),
  scaled: scaleWeights([45, 25, 20, 10], 25.9), back: scaleWeights([11.66, 6.47, 5.18, 2.59], 100),
  parse: ['12.5', '12.50%', ' 7 ', '.5', '1.234', '0', '-1', 'x', ''].map(parseWeight),
  names: [customName({name: ' Mine '}), customName({name: ''})]}));
"""
    done = subprocess.run(['node', '-e', script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    assert got['weights'] == pytest.approx([20.89, 43.01, 36.1])
    assert got['strategic'] == [25.9, 38, 36.1]
    # A gives five points and a hundredth, B takes them: whole, but off the strategic weights
    assert got['balance']['balanced'] is True and got['balance']['departs'] is True
    assert got['balance']['total'] == pytest.approx(100.0)
    # A back on a library sleeve: B's points are now too many
    assert got['library']['balanced'] is False and got['library']['total'] == pytest.approx(105.01)
    assert got['library']['departs'] is True, 'B still moved'
    assert got['scaled'] == [11.66, 6.47, 5.18, 2.59] and sum(got['scaled']) == pytest.approx(25.9)
    assert sum(got['back']) == pytest.approx(100.0)
    assert got['parse'][:4] == [12.5, 12.5, 7, 0.5] and all(v is None for v in got['parse'][4:])
    assert got['names'] == ['Custom: Mine', 'Custom']
    # the tolerance is the server's
    assert 'var CUSTOM_BALANCE = {};'.format(customSleeves.BALANCE_TOLERANCE) in js
    # and the model on the same lines says the same about balance
    total = 20.89 + 43.01 + 36.1
    assert (abs(total - 100) < customSleeves.BALANCE_TOLERANCE) is got['balance']['balanced']


def test_the_page_offers_the_builder_where_the_person_may_build():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, '..', '..', 'generator', 'js', 'implementation.js'), encoding='utf-8') as fh:
        js = fh.read()
    with open(os.path.join(here, '..', '..', 'generator', 'js', 'core.js'), encoding='utf-8') as fh:
        core = fh.read()
    assert "'<option value=\"__build__\">Build a custom sleeve…</option>'" in js
    assert "!held && mayBuild" in js
    assert "JSON.stringify({ sleeves: state.sleeves, customSleeves: state.customSleeves })" in core
    assert "(opt('customSleeves.mayBuild', []) || []).indexOf(category) !== -1" in core
    with open(os.path.join(here, '..', '..', 'generator', 'build_styles.py'), encoding='utf-8') as fh:
        assert '<div id="cslDialog" hidden></div>' in fh.read()
