"""A fee group with no rates yet (D187).

A fee group may be on the card with a - in every box, so products can be put
in it before its rates are known. Nothing prices it: its products are
unpriced at every level, it moves no level, it has no custom row, and a
priced proposal holding one is not delivered until the card has its rates.
Each test has a repository file of its own."""
import base64
import copy
import csv
import io
import json
import os
import shutil
import subprocess

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import feeCard, fees, products, rules, scenarioStore, sleeves
from cyrus_pmg.pmgService.scenario.fixturesAdapter import FixturesScenarioPort
from cyrus_pmg.pmgService.scenario.types import (BasisInput, MandateInput, PortfolioKey,
                                                 ValidationError)
from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows

HERE = os.path.dirname(os.path.abspath(__file__))
MA = sleeves.VARIANTS[0]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path / 'store'))
    products.reload()
    feeCard._forget()
    yield tmp_path
    products.reload()
    feeCard._forget()


def _caller(kerberos='alice'):
    from cyrus_pmg.pmgService.core import accessControl
    return accessControl.UserData(kerberos, accessControl._rolesFor(kerberos))


def _card():
    return copy.deepcopy(feeCard.describe()['card'])


def _saved(card, note='A group with no rates yet'):
    return feeCard.save(card, note, user='alice', base=fees.deliveryInfo()['revision'])


def _withEmptyGroup(card, name='CFI SMA'):
    card['feeGroups'].append(name)
    card['rates']['RDR'][name] = {t: {level: '-' for level in fees.LEVELS}
                                  for t in card['rates']['RDR']['Passive']}
    return card


def _emptied(card, group):
    for byLevel in card['rates']['RDR'][group].values():
        for level in fees.LEVELS:
            byLevel[level] = '-'
    return card


def _product(pid, group):
    return {'productId': pid, 'name': pid.title(), 'ticker': '', 'assetClass': 'Global Equity',
            'style': 'Active', 'vehicle': 'SMA', 'shareClass': '', 'source': 'Internal',
            'liquidity': 'Daily', 'exposureCurrency': 'USD', 'productCost': 0.2,
            'feeGroup': group}


def test_a_group_with_a_dash_in_every_box_takes_products_and_prices_nothing(store):
    saved = _saved(_withEmptyGroup(_card()))
    assert 'Added fee group CFI SMA' in saved['changes']
    assert fees.unratedGroups() == ['CFI SMA'] and fees.unrated('RDR', 'CFI SMA')
    assert fees.absent()['RDR']['CFI SMA'] == list(fees.SOURCES)
    # the product file's check is only that the group is on the card
    products.create(_product('probe-cfi', 'CFI SMA'), user='alice')
    for level in fees.LEVELS + [fees.CUSTOM_LEVEL]:
        assert fees.productFee('RDR', level, 'CFI SMA', 5e6, 5e6, customFees={}) is None
    # CASP prices every product by its own row, whatever its group
    assert fees.productFee('CASP', 'PMG Target', 'CFI SMA', 5e6, 5e6) is not None
    # it moves no level, has no custom row, and is served as having no rates
    assert fees.levelInForce('RDR', 'PMG Target', ['Passive', 'CFI SMA']) == ('PMG Target', [])
    assert 'CFI SMA' not in fees.customRows('RDR')
    assert fees.feePayload(5e6, 5e6)['unrated'] == ['CFI SMA']
    with pytest.raises(ValidationError) as caught:
        rules.validateCustomFees({'RDR': {'CFI SMA': 0.5}},
                                 MandateInput(topAccountSize=5e6, mandateSize=5e6,
                                              primaryPwa='A. Castellanos — Madrid'))
    assert 'no rates for it yet' in caught.value.message
    # the file writes it as it was given, and loads back the same
    exported = list(csv.reader(io.StringIO(feeCard.exportCsv())))
    rows = [r for r in exported[1:] if r[1] == 'CFI SMA']
    assert len(rows) == len(fees.tiers()) * len(fees.LEVELS) and {r[7] for r in rows} == {'-'}
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows(exported)
    again = feeCard.importPreview('again.csv', base64.b64encode(buf.getvalue().encode()).decode())
    assert again['same']


def test_a_uniform_schedules_row_still_needs_its_rates(store):
    card = _card()
    for byLevel in card['rates']['CASP'][''].values():
        for level in fees.LEVELS:
            byLevel[level] = '-'
    with pytest.raises(ValidationError) as caught:
        _saved(card)
    assert 'Every product is priced by this row' in caught.value.message


def _model(feeLevel='PMG Target', customFees=None):
    port = FixturesScenarioPort()
    result = port.resolve_portfolio(BasisInput(currency='USD', hedging='Hedged'),
                                    PortfolioKey('USD', 'Moderate', 'Full', False))
    chosen = {}
    for c in result['categories']:
        if c['name'] in rules.AUTO_SLEEVE_CATEGORIES:
            continue
        under = rules.sleeveCategory(c['name'])
        chosen.setdefault(under, sleeves.listSleeves(under, MA)[0]['name'])
    return chosen, buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES, 5e9, MA,
                                           False, 'RDR', feeLevel, 5e9, False, 'USD',
                                           customFees=customFees)


def test_a_priced_proposal_holding_one_is_unpriced_and_not_delivered(store):
    chosen, model = _model()
    held = sorted({i['feeGroup'] for g in model['groups'] for i in g['items']})
    empty = held[0]
    _saved(_emptied(_card(), empty))
    chosen, model = _model()
    assert model['unratedGroups'] == [empty] and model['feeLevel'] == 'PMG Target'
    assert model['total']['wtdFeeBp'] is None
    for group in model['groups']:
        for item in group['items']:
            assert (item['managementFee'] is None) == (item['feeGroup'] == empty)

    state = scenarioStore.createScenario(
        MandateInput(topAccountSize=5e9, mandateSize=5e9, primaryPwa='A. Castellanos — Madrid'),
        BasisInput(currency='USD', hedging='Hedged'), createdBy='alice')
    scenarioStore.updateScenario(state['id'], variant=MA)
    scenarioStore.recordColumn(state['id'], PortfolioKey('USD', 'Moderate', 'Full', False), 'base')
    scenarioStore.updateScenario(state['id'], sleeves=chosen, includeFees=True, feeSchedule='RDR',
                                 feeLevel='PMG Target')
    refused = dashboardRouter.exportScenario(state['id'], caller=_caller('alice'))
    assert getattr(refused, 'status_code', 200) == 422
    error = json.loads(refused.body.decode('utf-8'))
    assert error['field'] == 'feeCard'
    assert error['error'].startswith('The fee card has no rates yet for {}'.format(empty))
    # unpriced, it is delivered as it always was
    scenarioStore.updateScenario(state['id'], includeFees=False)
    model = dashboardRouter._assembleExport(state['id'], FixturesScenarioPort())[-1]
    assert model['unratedGroups'] == []


# ------------------------------------------------------- the page ---

def _node(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    done = subprocess.run([node, '-e', script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-1200:]
    return json.loads(done.stdout)


def _js(name):
    with open(os.path.join(HERE, '..', '..', 'generator', 'js', name), encoding='utf-8') as fh:
        return fh.read()


def _function(source, name):
    start = source.index('function {}('.format(name))
    return source[start:source.index('\n}\n', start) + 3]


LEVELS = ['Management Floor', 'Management Target', 'Management Ceiling', 'PMG Floor', 'PMG Target',
          'PMG Ceiling']
CTX = {'schedules': [{'id': 'CASP', 'byGroup': False}, {'id': 'RDR', 'byGroup': True}],
       'sources': ['Management', 'PMG'], 'points': ['Floor', 'Target', 'Ceiling'], 'levels': LEVELS,
       'rateMax': 10, 'groupMax': 40, 'requiredSource': 'Management'}
CARD = {
    'tiers': [{'id': 'T1', 'min': 0, 'max': None}],
    'feeGroups': ['Passive'],
    'rates': {'CASP': {'': {'T1': dict(zip(LEVELS, [0.4, 0.5, 0.6, 0.5, 0.6, 0.7]))}},
              'RDR': {'Passive': {'T1': dict(zip(LEVELS, [0.1, 0.2, 0.3, 0.2, 0.3, 0.4]))}}},
}


def test_the_editor_adds_a_group_with_no_rates_yet_and_saves_it():
    source = _js('repository.js')
    helpers = source[source.index('/* fee-editor-helpers-begin'):source.index('/* fee-editor-helpers-end */')]
    got = _node(helpers + '''
var card = %s, ctx = %s;
var d = feeDraftFrom(card, ctx.levels);
var key = feeAddGroup(d, 'CFI SMA', null, ctx.schedules, ctx.levels);
var out = { boxes: feeClone(d.rates.RDR[key].T1), problems: feeProblems(d, ctx),
            payload: feePayload(d, ctx.schedules, ctx.levels).card.rates.RDR['CFI SMA'].T1 };
d.rates.RDR[key].T1['Management Target'] = '0.5';
out.some = feeProblems(d, ctx).map(function (p) { return p.message; });
process.stdout.write(JSON.stringify(out));
''' % (json.dumps(CARD), json.dumps(CTX)))
    assert set(got['boxes'].values()) == {'-'}
    assert got['problems'] == []
    assert set(got['payload'].values()) == {None}
    assert ('RDR · CFI SMA · Every account size · Management Floor has no rate. Give its Management '
            'rates, or none of its rates yet: a - in every box.') in got['some']


def test_the_page_says_a_held_group_has_no_rates_and_holds_the_download():
    source = _js('implementation.js')
    fns = ''.join(_function(source, n) for n in ('rowsLacking', 'unratedHeld', 'unratedNote',
                                                 'customRowsFor'))
    got = _node(fns + '''
var OPT = { 'fees.absent': { RDR: { 'CFI SMA': ['Management', 'PMG'] } }, 'fees.unrated': ['CFI SMA'],
            'fees.feeGroups': ['Passive', 'CFI SMA'] };
var held = ['Passive'], schedule = 'RDR';
var App = { opt: function (k, d) { return k in OPT ? OPT[k] : d; }, feeSchedule: function () { return schedule; } };
function heldFeeGroups() { return held.slice(); }
function scheduleByGroup(s) { return s === 'RDR'; }
var out = { none: unratedHeld(), quiet: unratedNote() };
held.push('CFI SMA');
out.held = unratedHeld();
out.note = unratedNote();
out.lacking = rowsLacking('RDR', 'PMG', held);
out.rows = customRowsFor('RDR').map(function (r) { return r.group; });
schedule = 'CASP';
out.casp = unratedHeld();
process.stdout.write(JSON.stringify(out));
''')
    assert got['none'] == [] and got['quiet'] == ''
    assert got['held'] == ['CFI SMA']
    assert got['note'] == 'The fee card has no rates yet for CFI SMA: its products are unpriced until it has them.'
    assert got['lacking'] == [] and got['rows'] == ['Passive'] and got['casp'] == []
    gate = _function(source, 'exportGate')
    assert "The fee card has no rates yet for ' + unratedHeld().join(', ')" in gate
