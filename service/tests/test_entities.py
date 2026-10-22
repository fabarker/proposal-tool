"""D193: the custodian / entity a proposal is for.

The PWA chooses it on the first card; a rule in an edition may be for
particular entities; each entity carries a list of products it may not hold,
kept in the console's Entities tab with a note and a revision per change. A
sleeve whose edition holds an excluded product is served saying so - the
picker greys it out - and nothing excluded is delivered there.
"""

import json
import os
import shutil
import subprocess

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import (entities, overlayRules, proposalRegister, rules,
                                           sleeveRepo, sleeveRules, sleeves)
from cyrus_pmg.pmgService.scenario.types import BasisInput, MandateInput, PortfolioKey, ValidationError

HERE = os.path.dirname(os.path.abspath(__file__))
MA, US = 'PMG Multi-Asset Portfolio', 'US Onshore'
IG, HFI = 'Investment Grade Fixed Income', 'Hybrid Fixed Income'
SMA, FUND = 'gsam-core-municipal-sma', 'gs-us-corporate-bond-fund'
GBP_MOD_FULL = PortfolioKey('GBP', 'Moderate', 'Full', False)
USD_MOD_FULL = PortfolioKey('USD', 'Moderate', 'Full', False)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path / 'store'))
    return tmp_path


def _caller(kerberos='alice'):
    from cyrus_pmg.pmgService.core import accessControl
    return accessControl.UserData(kerberos, accessControl._rolesFor(kerberos))


def _exclude(entityId, *productIds, note='test'):
    listed = entities.everyEntity()
    for e in listed:
        if e['id'] == entityId:
            e['excluded'] = sorted(set(e['excluded']) | set(productIds))
    return entities.save(listed, note, 'alice')


# ------------------------------------------------------------- the list ---

def test_the_four_are_read_without_writing_and_seeded_on_the_first_save(store):
    sleeveRepo.describe()
    assert entities.current()['revision'] == 0, 'a read never writes'
    assert [e['id'] for e in entities.offered()] == ['gsi', 'gsbe', 'gsbz', 'middle-east']
    assert entities.resolve('Middle East') == entities.resolve('middle-east') == 'middle-east'
    saved = _exclude('gsbz', SMA)
    assert saved['revision'] == 2, 'the baseline first, then the change'
    history = entities.history()
    assert [h['action'] for h in history] == ['updated', 'baseline']
    assert history[0]['changes'] == ['GSBZ excludes GSAM Core Municipal SMA']
    assert entities.excluded('gsbz') == {SMA}
    assert entities.notAvailableAt(SMA) == ['GSBZ']


def test_an_entity_is_added_renamed_and_retired_never_deleted(store):
    listed = entities.everyEntity() + [{'name': 'GS Japan', 'excluded': [FUND]}]
    entities.save(listed, 'a new one', 'alice')
    assert entities.resolve('GS Japan') == 'gs-japan'
    renamed = [dict(e, name='GS Tokyo') if e['id'] == 'gs-japan' else e for e in entities.everyEntity()]
    entities.save(renamed, 'renamed', 'alice')
    assert entities.name('gs-japan') == 'GS Tokyo', 'the id stays; rules and scenarios keep their meaning'
    with pytest.raises(ValidationError, match='Retire it instead'):
        entities.save([e for e in entities.everyEntity() if e['id'] != 'gs-japan'], 'gone', 'alice')
    retired = [dict(e, retired=True) if e['id'] == 'gs-japan' else e for e in entities.everyEntity()]
    entities.save(retired, 'retired', 'alice')
    assert 'gs-japan' not in [e['id'] for e in entities.offered()]
    assert entities.checkChoice('gs-japan', allowRetired=True) == 'gs-japan'
    with pytest.raises(ValidationError, match='retired'):
        entities.checkChoice('gs-japan')
    with pytest.raises(ValidationError, match='note'):
        entities.save(entities.everyEntity(), '', 'alice')
    with pytest.raises(ValidationError, match='not in the catalogue'):
        entities.save([dict(e, excluded=['no-such']) for e in entities.everyEntity()], 'x', 'alice')
    with pytest.raises(ValidationError, match='Two entities'):
        entities.save(entities.everyEntity() + [{'name': 'gsi'}], 'x', 'alice')


def test_a_restore_keeps_an_entity_added_since_retired(store):
    _exclude('gsbe', SMA)
    entities.save(entities.everyEntity() + [{'name': 'Later'}], 'added', 'alice')
    back = entities.revert(2, 'back', 'alice')
    later = next(e for e in back['entities'] if e['id'] == 'later')
    assert later['retired'] is True
    assert entities.excluded('gsbe') == {SMA}


def test_a_stale_save_is_refused(store):
    # the first save is made against the four at revision 0
    first = [dict(e, excluded=[SMA]) if e['id'] == 'gsi' else e for e in entities.everyEntity()]
    assert entities.save(first, 'first', 'alice', base=0)['revision'] == 2
    with pytest.raises(sleeveRepo.StaleError):
        entities.save(entities.everyEntity(), 'old view', 'bob', base=1)


# ------------------------------------------------------------ the rules ---

def test_a_rule_may_name_entities_by_id_or_name(store):
    clean = sleeveRules.normalise([{'currency': 'GBP', 'entity': 'GSBE|middle east'}])
    assert clean == [{'currency': ['GBP'], 'entity': ['gsbe', 'middle-east']}]
    assert sleeveRules.matches(clean, GBP_MOD_FULL, 'gsbe')
    assert not sleeveRules.matches(clean, GBP_MOD_FULL, 'gsi')
    assert not sleeveRules.matches(clean, GBP_MOD_FULL, None)
    with pytest.raises(ValidationError, match='not a custodian'):
        sleeveRules.normalise([{'entity': 'Narnia'}])
    assert 'entity GSBE|Middle East' in sleeveRules.describe(clean)


def test_editions_for_different_entities_never_clash(store):
    sleeveRepo.createSleeve(MA, IG, 'By Entity', [{'productId': SMA, 'weight': 1.0}], user='a')
    sleeveRepo.createSleeve(MA, IG, 'By Entity', [{'productId': FUND, 'weight': 1.0}], user='a',
                            label='GSBE', rules=[{'entity': ['gsbe']}])
    sleeveRepo.createSleeve(MA, IG, 'By Entity', [{'productId': 'gs-short-duration-income', 'weight': 1.0}],
                            user='a', label='GBP at GSI', rules=[{'currency': ['GBP'], 'entity': ['gsi']}])
    with pytest.raises(ValidationError, match='Overlaps the GSBE edition'):
        sleeveRepo.createSleeve(MA, IG, 'By Entity', [{'productId': SMA, 'weight': 1.0}], user='a',
                                label='Any GBP', rules=[{'currency': ['GBP']}])

    def held(key, entity):
        served = next(s for s in sleeves.listSleeves(IG, MA, key, entity) if s['name'] == 'By Entity')
        return [p['productId'] for p in served['products']]
    assert held(GBP_MOD_FULL, 'gsbe') == [FUND]
    assert held(GBP_MOD_FULL, 'gsi') == ['gs-short-duration-income']
    assert held(USD_MOD_FULL, 'gsi') == [SMA], 'everyone else: the fallback'
    assert held(GBP_MOD_FULL, None) == [SMA], 'no entity: no entity edition'


def test_the_rules_file_carries_an_entity_column(store, tmp_path):
    from cyrus_pmg.pmgService.scenario import sleeveTools
    sleeveRepo.createSleeve(MA, IG, 'File Entity', [{'productId': SMA, 'weight': 1.0}], user='a',
                            label='ME', rules=[{'entity': ['middle-east']}])
    out = tmp_path / 'lib.csv'
    assert sleeveTools.main(['--export', str(out)]) == 0
    rules_file = (tmp_path / 'lib.rules.csv').read_text().splitlines()
    assert rules_file[0].endswith(',Entity')
    assert any(line.endswith(',Middle East') and 'File Entity' in line for line in rules_file)
    assert sleeveTools.main(['--import', str(out), '--replace']) == 0
    again = next(e for e in sleeveRepo.listAll(MA) if e['name'] == 'File Entity')
    assert again['rules'] == [{'entity': ['middle-east']}]


# ------------------------------------------------------- the exclusions ---

def test_a_sleeve_holding_an_excluded_product_is_served_greyed(store):
    sleeveRepo.createSleeve(MA, IG, 'Muni Test', [{'productId': SMA, 'weight': 1.0, 'alternative': FUND}],
                            user='a')
    _exclude('gsbz', SMA)
    at = next(s for s in sleeves.listSleeves(IG, MA, USD_MOD_FULL, 'gsbz') if s['name'] == 'Muni Test')
    assert at['notAvailable'] == {'entity': 'gsbz', 'entityName': 'GSBZ', 'products': ['GSAM Core Municipal SMA']}
    assert not sleeves.sleeveExists(IG, 'Muni Test', MA, USD_MOD_FULL, 'gsbz')
    assert sleeves.sleeveExists(IG, 'Muni Test', MA, USD_MOD_FULL, 'gsi')
    _exclude('gsi', FUND)
    there = next(s for s in sleeves.listSleeves(IG, MA, USD_MOD_FULL, 'gsi') if s['name'] == 'Muni Test')
    assert there['products'][0]['alternative'] is None, 'an excluded alternative is not one there'


def test_an_overlay_whose_sleeve_an_entity_cannot_hold_is_not_given_there(store):
    svp = next(e for e in sleeveRepo.listAll(MA) if e['category'] == HFI)
    _exclude('middle-east', *[p['productId'] for p in svp['products']])
    assert [r['id'] for r in overlayRules.current(MA, 'middle-east')['rules']] == ['ttf']
    assert [r['id'] for r in overlayRules.current(MA, 'middle-east')['withheld']] == ['svp']
    assert [r['id'] for r in overlayRules.current(MA, 'gsi')['rules']] == ['ttf', 'svp']
    assert [r['id'] for r in rules._servedOverlays(MA, 'middle-east')['withheld']] == ['svp']


# --------------------------------------------------- the scenario & export ---

def _scenario(entity, sleevesFor=None):
    from test_scenario_backend import BASIS, PORT, _sleeveMap
    from cyrus_pmg.pmgService.scenario import scenarioStore
    state = scenarioStore.createScenario(
        MandateInput(topAccountSize=1e9, mandateSize=1e9, primaryPwa='A. Castellanos — Madrid',
                     entity=entity), BASIS, createdBy='alice')
    result = PORT.resolve_portfolio(BASIS, USD_MOD_FULL)
    scenarioStore.updateScenario(state['id'], variant=MA)
    scenarioStore.recordColumn(state['id'], USD_MOD_FULL, 'base')
    chosen = _sleeveMap(result['categories'], MA)
    chosen.update(sleevesFor or {})
    scenarioStore.updateScenario(state['id'], sleeves=chosen)
    return state['id']


def test_the_export_needs_the_entity_and_records_it(store):
    blank = _scenario('')
    refused = dashboardRouter.exportScenario(blank, caller=_caller())
    assert refused.status_code == 422 and json.loads(refused.body)['field'] == 'entity'
    done = dashboardRouter.exportScenario(_scenario('gsbe'), caller=_caller())
    assert done.status_code == 200, done.body
    kept = proposalRegister.getProposal(done.headers['X-Proposal-Id'])
    assert kept['entity'] == 'gsbe' and kept['entityName'] == 'GSBE'
    row = dict(zip(proposalRegister.EXPORT_COLUMNS, proposalRegister.exportRows()[0]))
    assert row['Entity'] == 'GSBE'


def test_nothing_excluded_is_delivered(store):
    sleeveRepo.createSleeve(MA, IG, 'Muni Test', [{'productId': SMA, 'weight': 1.0}], user='a')
    scenarioId = _scenario('gsbz', {IG: 'Muni Test'})
    _exclude('gsbz', SMA)
    refused = dashboardRouter.exportScenario(scenarioId, caller=_caller())
    body = json.loads(refused.body)
    assert refused.status_code == 422 and body['field'] == 'entity'
    assert body['error'] == ('The sleeve chosen for Investment Grade Fixed Income is not available at '
                             'GSBZ: it holds GSAM Core Municipal SMA. Choose another.')


def test_the_routes_check_and_keep_the_entity(store, monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    alice = _caller()
    made = dashboardRouter.createScenario({'mandate': {
        'topAccountSize': 1e9, 'mandateSize': 1e9, 'primaryPwa': 'A. Castellanos — Madrid', 'entity': 'narnia'},
        'basis': {'currency': 'USD', 'hedging': 'Hedged'}}, caller=alice)
    assert made.status_code == 422 and json.loads(made.body)['field'] == 'entity'
    made = dashboardRouter.createScenario({'mandate': {
        'topAccountSize': 1e9, 'mandateSize': 1e9, 'primaryPwa': 'A. Castellanos — Madrid', 'entity': 'gsbe'},
        'basis': {'currency': 'USD', 'hedging': 'Hedged'}}, caller=alice)
    scenarioId = made['id']
    # a mandate write that does not name the entity keeps it
    kept = dashboardRouter.updateScenario(scenarioId, {'mandate': {
        'topAccountSize': 2e9, 'mandateSize': 1e9, 'primaryPwa': 'A. Castellanos — Madrid'}}, caller=alice)
    assert kept['mandate']['entity'] == 'gsbe'
    schema = dashboardRouter.getScenarioSchema(None, entity='gsbe', caller=alice)
    assert [e['id'] for e in schema['options']['entities']] == ['gsi', 'gsbe', 'gsbz', 'middle-east']


def test_the_custom_lists_leave_out_what_the_entity_excludes(store, monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    _exclude('gsbz', SMA)
    body = dashboardRouter.listCategoryProducts(IG, variant=MA, currency='USD', entity='gsbz', caller=_caller())
    assert SMA not in [p['productId'] for p in body['products']]
    body = dashboardRouter.listCategoryProducts(IG, variant=MA, currency='USD', entity='gsi', caller=_caller())
    assert SMA in [p['productId'] for p in body['products']]


# ------------------------------------------------------------- the console ---

def test_the_console_clashes_only_at_a_shared_entity():
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    with open(os.path.join(HERE, '..', '..', 'generator', 'js', 'repository.js'), encoding='utf-8') as fh:
        js = fh.read()
    start = js.index('/* sleeve-view-helpers-begin')
    helpers = js[start:js.index('/* sleeve-view-helpers-end */')]
    script = helpers + '''
var keys = ['GBP|Moderate|Full|0', 'USD|Moderate|Full|0'];
var gsbe = [{ entity: ['gsbe'] }], gbpGsi = [{ currency: ['GBP'], entity: ['gsi'] }], gbp = [{ currency: ['GBP'] }];
var vocab = { currency: ['USD', 'GBP'], riskLevel: ['Moderate'], allocationType: ['Full'], entity: ['gsi', 'gsbe'] };
console.log(JSON.stringify({
  apart: edCoverage(keys, gbpGsi, [{ label: 'GSBE', rules: gsbe }]).clash,
  meet: edCoverage(keys, gbp, [{ label: 'GSBE', rules: gsbe }]).clash.map(function (c) { return [c.label, c.keys]; }),
  grid: edGridOf(gbpGsi, vocab),
  back: edRulesOf({ alloc: null, entity: ['gsi'], cells: { 'GBP|Moderate': 1 } }, vocab),
  words: edRuleWords(gbpGsi[0], { entity: { gsi: 'GSI' } })
}));
'''
    got = json.loads(subprocess.run([node, '-e', script], capture_output=True, text=True, check=True).stdout)
    assert got['apart'] == [], 'GBP at GSI and anything at GSBE never meet'
    assert got['meet'] == [['GSBE', ['GBP|Moderate|Full|0']]]
    assert got['grid']['entity'] == ['gsi'] and got['grid']['cells'] == {'GBP|Moderate': 1}
    assert got['back'] == [{'currency': ['GBP'], 'entity': ['gsi']}]
    assert got['words'] == 'GBP portfolios at any risk level, any allocation, held at GSI'
