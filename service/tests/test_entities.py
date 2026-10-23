"""D193, D195: the custodian a PMG ESG proposal is held at.

One of a fixed list - GSI, GSBZ, GSLU, GSCO - chosen on a card that opens as PMG
ESG is chosen; under any other type a scenario has none. Its one use is to
pick sleeve editions: a rule in an edition may be for particular custodians,
and the edition served is the one for the scenario's custodian, else the
fallback. An overlay whose sleeve has no edition for the custodian is not
given there. The export under PMG ESG needs one, and the register keeps it.
"""

import json
import os
import shutil
import subprocess

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import (entities, overlayRules, proposalRegister, rules,
                                           sleeveRepo, sleeveRules, sleeves)
from cyrus_pmg.pmgService.scenario.types import MandateInput, PortfolioKey, ValidationError

HERE = os.path.dirname(os.path.abspath(__file__))
MA, ESG = 'PMG Multi-Asset Portfolio', 'PMG ESG'
IG, HFI = 'Investment Grade Fixed Income', 'Hybrid Fixed Income'
SMA, FUND = 'gsam-core-municipal-sma', 'gs-us-corporate-bond-fund'
GBP_ESG = PortfolioKey('GBP', 'Moderate', 'ex-HFs', True)
USD_ESG = PortfolioKey('USD', 'Moderate', 'ex-HFs', True)
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


# ------------------------------------------------------------- the list ---

def test_the_custodians_are_a_fixed_list_asked_for_under_pmg_esg():
    assert entities.everyEntity() == [{'id': 'gsi', 'name': 'GSI'}, {'id': 'gsbz', 'name': 'GSBZ'},
                                      {'id': 'gslu', 'name': 'GSLU'}, {'id': 'gsco', 'name': 'GSCO'}]
    assert entities.offered() == entities.everyEntity()
    assert entities.resolve('GSLU') == entities.resolve(' gslu ') == 'gslu'
    assert entities.resolve('gsco') == 'gsco' and entities.name('gsco') == 'GSCO'
    assert entities.resolve('GSBE') is None and entities.name('gsbz') == 'GSBZ'
    assert entities.custodyTypes() == [ESG]
    assert entities.needed(ESG) and not entities.needed(MA) and not entities.needed(None)
    assert entities.effective('gsbz', ESG) == 'gsbz'
    assert entities.effective('gsbz', MA) == '', 'none under a type that does not ask'
    assert entities.effective('gsbe', ESG) == '', 'one no longer on the list is none, and asked again'
    assert entities.checkChoice('') == '' and entities.checkChoice('gslu') == 'gslu'
    with pytest.raises(ValidationError, match='one of GSI, GSBZ, GSLU, GSCO'):
        entities.checkChoice('gsbe')


# ------------------------------------------------------------ the rules ---

def test_a_rule_may_name_custodians_by_id_or_name(store):
    clean = sleeveRules.normalise([{'currency': 'GBP', 'entity': 'GSBZ|gslu'}])
    assert clean == [{'currency': ['GBP'], 'entity': ['gsbz', 'gslu']}]
    assert sleeveRules.matches(clean, GBP_ESG, 'gsbz')
    assert not sleeveRules.matches(clean, GBP_ESG, 'gsi')
    assert not sleeveRules.matches(clean, GBP_ESG, None)
    with pytest.raises(ValidationError, match='not a custodian. Known: GSI, GSBZ, GSLU, GSCO'):
        sleeveRules.normalise([{'entity': 'Middle East'}])
    assert 'entity GSBZ|GSLU' in sleeveRules.describe(clean)


def test_editions_for_different_custodians_never_clash(store):
    sleeveRepo.createSleeve(ESG, IG, 'By Custodian', [{'productId': SMA, 'weight': 1.0}], user='a')
    sleeveRepo.createSleeve(ESG, IG, 'By Custodian', [{'productId': FUND, 'weight': 1.0}], user='a',
                            label='GSBZ', rules=[{'entity': ['gsbz']}])
    sleeveRepo.createSleeve(ESG, IG, 'By Custodian', [{'productId': 'gs-short-duration-income', 'weight': 1.0}],
                            user='a', label='GBP at GSI', rules=[{'currency': ['GBP'], 'entity': ['gsi']}])
    with pytest.raises(ValidationError, match='Overlaps the GSBZ edition'):
        sleeveRepo.createSleeve(ESG, IG, 'By Custodian', [{'productId': SMA, 'weight': 1.0}], user='a',
                                label='Any GBP', rules=[{'currency': ['GBP']}])

    def held(key, entity):
        served = next(s for s in sleeves.listSleeves(IG, ESG, key, entity) if s['name'] == 'By Custodian')
        return [p['productId'] for p in served['products']]
    assert held(GBP_ESG, 'gsbz') == [FUND]
    assert held(GBP_ESG, 'gsi') == ['gs-short-duration-income']
    assert held(USD_ESG, 'gsi') == [SMA], 'everyone else: the fallback'
    assert held(GBP_ESG, 'gslu') == [SMA]
    assert held(GBP_ESG, None) == [SMA], 'no custodian: no custodian edition'
    assert sleeves.sleeveExists(IG, 'By Custodian', ESG, GBP_ESG, 'gslu')


def test_only_a_sleeve_under_pmg_esg_may_have_an_edition_for_a_custodian(store):
    sleeveRepo.createSleeve(MA, IG, 'Not Here', [{'productId': SMA, 'weight': 1.0}], user='a')
    with pytest.raises(ValidationError, match='Only a sleeve offered under PMG ESG'):
        sleeveRepo.createSleeve(MA, IG, 'Not Here', [{'productId': FUND, 'weight': 1.0}], user='a',
                                label='GSBZ', rules=[{'entity': ['gsbz']}])
    # shared with PMG ESG, it may: a scenario there can be at GSBZ
    sleeveRepo.createSleeves([MA, ESG], IG, 'Shared Here', [{'productId': SMA, 'weight': 1.0}], user='a')
    sleeveRepo.createSleeves([MA, ESG], IG, 'Shared Here', [{'productId': FUND, 'weight': 1.0}], user='a',
                             label='GSBZ', rules=[{'entity': ['gsbz']}])


def test_the_rules_file_carries_an_entity_column(store, tmp_path):
    from cyrus_pmg.pmgService.scenario import sleeveTools
    sleeveRepo.createSleeve(ESG, IG, 'File Custodian', [{'productId': SMA, 'weight': 1.0}], user='a',
                            label='LU', rules=[{'entity': ['gslu']}])
    out = tmp_path / 'lib.csv'
    assert sleeveTools.main(['--export', str(out)]) == 0
    rules_file = (tmp_path / 'lib.rules.csv').read_text().splitlines()
    assert rules_file[0].endswith(',Entity')
    assert any(line.endswith(',GSLU') and 'File Custodian' in line for line in rules_file)
    assert sleeveTools.main(['--import', str(out), '--replace']) == 0
    again = next(e for e in sleeveRepo.listAll(ESG) if e['name'] == 'File Custodian')
    assert again['rules'] == [{'entity': ['gslu']}]


def test_an_overlay_without_an_edition_for_the_custodian_is_not_given_there(store):
    svp = next(e for e in sleeveRepo.listAll(ESG) if e['category'] == HFI)
    sleeveRepo.updateSleeve(svp['id'], svp['name'],
                            [{'productId': p['productId'], 'weight': p['weight']} for p in svp['products']],
                            user='a', label='GSI only', rules=[{'entity': ['gsi']}])
    assert [r['id'] for r in overlayRules.current(ESG, 'gsbz')['withheld']] == ['svp']
    assert 'svp' in [r['id'] for r in overlayRules.current(ESG, 'gsi')['rules']]
    assert [r['id'] for r in rules._servedOverlays(ESG, 'gsbz')['withheld']] == ['svp']


# --------------------------------------------------- the scenario & export ---

def _scenario(entity, variant=ESG, key=USD_ESG):
    from test_scenario_backend import BASIS, PORT, _confirmHedgeFunds, _sleeveMap
    from cyrus_pmg.pmgService.scenario import scenarioStore
    state = scenarioStore.createScenario(
        MandateInput(topAccountSize=1e9, mandateSize=1e9, primaryPwa='A. Castellanos — Madrid',
                     entity=entity), BASIS, createdBy='alice')
    result = PORT.resolve_portfolio(BASIS, key)
    scenarioStore.updateScenario(state['id'], variant=variant)
    scenarioStore.recordColumn(state['id'], key, 'base')
    scenarioStore.updateScenario(state['id'], sleeves=_sleeveMap(result['categories'], variant))
    _confirmHedgeFunds(state['id'])
    return state['id']


def test_the_export_under_pmg_esg_needs_the_custodian_and_records_it(store):
    refused = dashboardRouter.exportScenario(_scenario(''), caller=_caller())
    assert refused.status_code == 422 and json.loads(refused.body)['field'] == 'entity'
    assert json.loads(refused.body)['error'].startswith('Choose the custodian for PMG ESG')
    done = dashboardRouter.exportScenario(_scenario('gslu'), caller=_caller())
    assert done.status_code == 200, done.body
    kept = proposalRegister.getProposal(done.headers['X-Proposal-Id'])
    assert kept['entity'] == 'gslu' and kept['entityName'] == 'GSLU'
    row = dict(zip(proposalRegister.EXPORT_COLUMNS, proposalRegister.exportRows()[0]))
    assert row['Entity'] == 'GSLU'


def test_under_another_type_none_is_needed_or_recorded(store):
    done = dashboardRouter.exportScenario(_scenario('gsbz', MA, USD_MOD_FULL), caller=_caller())
    assert done.status_code == 200, done.body
    assert proposalRegister.getProposal(done.headers['X-Proposal-Id'])['entity'] is None


def test_the_custodian_goes_with_pmg_esg(store, monkeypatch):
    """The first card asks for none; choosing PMG ESG sends one with the
    type, a mandate write keeps it, and moving to another type clears it."""
    from cyrus_pmg.pmgService.scenario import scenarioStore
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    alice = _caller()
    mandate = {'topAccountSize': 1e9, 'mandateSize': 1e9, 'primaryPwa': 'A. Castellanos — Madrid'}
    made = dashboardRouter.createScenario({'mandate': dict(mandate, entity='gsbz'),
                                           'basis': {'currency': 'USD', 'hedging': 'Hedged'}}, caller=alice)
    scenarioId = made['id']
    assert made['scenario']['mandate']['entity'] == '', 'no type yet, so no custodian'
    refused = dashboardRouter.updateScenario(scenarioId, {'variant': ESG, 'mandate': dict(mandate, entity='gsbe')},
                                             caller=alice)
    assert refused.status_code == 422 and json.loads(refused.body)['field'] == 'entity'
    chosen = dashboardRouter.updateScenario(scenarioId, {'variant': ESG, 'mandate': dict(mandate, entity='gsbz')},
                                            caller=alice)
    assert chosen['variant'] == ESG and chosen['mandate']['entity'] == 'gsbz'
    # a mandate write that does not name the custodian keeps it
    kept = dashboardRouter.updateScenario(scenarioId, {'mandate': dict(mandate, topAccountSize=2e9)}, caller=alice)
    assert kept['mandate']['entity'] == 'gsbz'
    moved = dashboardRouter.updateScenario(scenarioId, {'variant': MA}, caller=alice)
    assert moved['mandate']['entity'] == ''
    assert scenarioStore.getScenario(scenarioId)['mandate']['entity'] == '', 'cleared, not hidden'
    named = dashboardRouter.updateScenario(scenarioId, {'mandate': dict(mandate, entity='gsbz')}, caller=alice)
    assert named['mandate']['entity'] == '', 'not kept under a type that does not ask'
    schema = dashboardRouter.getScenarioSchema(None, variant=ESG, entity='gsbz', caller=alice)
    assert [e['id'] for e in schema['options']['entities']] == ['gsi', 'gsbz', 'gslu', 'gsco']
    assert schema['options']['custodyTypes'] == [ESG]


def test_one_no_longer_on_the_list_is_none_and_a_mandate_write_still_saves(store, monkeypatch):
    """A scenario saved holding a custodian since taken off the list (GSBE)
    is served with none, so the page asks again; editing its mandate is not
    refused for it."""
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    scenarioId = _scenario('gsbe')
    assert dashboardRouter.getScenario(scenarioId)['mandate']['entity'] == ''
    edited = dashboardRouter.updateScenario(scenarioId, {'mandate': {
        'topAccountSize': 2e9, 'mandateSize': 1e9, 'primaryPwa': 'A. Castellanos — Madrid'}}, caller=_caller())
    assert edited['mandate']['entity'] == '' and edited['mandate']['topAccountSize'] == 2e9


# ------------------------------------------------------------- the console ---

def test_the_console_clashes_only_at_a_shared_custodian():
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    with open(os.path.join(HERE, '..', '..', 'generator', 'js', 'repository.js'), encoding='utf-8') as fh:
        js = fh.read()
    start = js.index('/* sleeve-view-helpers-begin')
    helpers = js[start:js.index('/* sleeve-view-helpers-end */')]
    script = helpers + '''
var keys = ['GBP|Moderate|Full|0', 'USD|Moderate|Full|0'];
var gsbz = [{ entity: ['gsbz'] }], gbpGsi = [{ currency: ['GBP'], entity: ['gsi'] }], gbp = [{ currency: ['GBP'] }];
var vocab = { currency: ['USD', 'GBP'], riskLevel: ['Moderate'], allocationType: ['Full'], entity: ['gsi', 'gsbz'] };
console.log(JSON.stringify({
  apart: edCoverage(keys, gbpGsi, [{ label: 'GSBZ', rules: gsbz }]).clash,
  meet: edCoverage(keys, gbp, [{ label: 'GSBZ', rules: gsbz }]).clash.map(function (c) { return [c.label, c.keys]; }),
  grid: edGridOf(gbpGsi, vocab),
  back: edRulesOf({ alloc: null, entity: ['gsi'], cells: { 'GBP|Moderate': 1 } }, vocab),
  words: edRuleWords(gbpGsi[0], { entity: { gsi: 'GSI' } })
}));
'''
    got = json.loads(subprocess.run([node, '-e', script], capture_output=True, text=True, check=True).stdout)
    assert got['apart'] == [], 'GBP at GSI and anything at GSBZ never meet'
    assert got['meet'] == [['GSBZ', ['GBP|Moderate|Full|0']]]
    assert got['grid']['entity'] == ['gsi'] and got['grid']['cells'] == {'GBP|Moderate': 1}
    assert got['back'] == [{'currency': ['GBP'], 'entity': ['gsi']}]
    assert got['words'] == 'GBP portfolios at any risk level, any allocation, held at GSI'
