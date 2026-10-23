"""D196: who may not market what.

The offering entities - who shows a proposal to the client, which is not the
custodian - and the warnings: asset classes of the strategic allocation,
whole categories or lines within one, the offering entities that may not
market them, and the words the PWA is shown. Kept in the console's
Marketing tab with a note and a revision per change. An allocation holding a
warned asset class raises the warning; the PWA acknowledges it, and the
register records it with who acknowledged it and when. Nothing is blocked.
"""

import json

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import marketing, proposalRegister, sleeveRepo
from cyrus_pmg.pmgService.scenario.types import MandateInput, PortfolioKey, ValidationError

MA = 'PMG Multi-Asset Portfolio'
USD_MOD_FULL = PortfolioKey('USD', 'Moderate', 'Full', False)
HF = 'Hedge Funds'
WORDS = 'Hedge funds may not be marketed by GS Bank Europe.\nCheck who is offering this proposal.'


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path / 'store'))
    return tmp_path


def _caller(kerberos='alice'):
    from cyrus_pmg.pmgService.core import accessControl
    return accessControl.UserData(kerberos, accessControl._rolesFor(kerberos))


def _lists(warnings=None, offering=None):
    """GS Bank Europe and GS Middle East, and a warning on hedge funds."""
    sleeveRepo.describe()
    return marketing.save({
        'offering': offering or [{'name': 'GS Bank Europe'}, {'name': 'GS Middle East'}],
        'warnings': warnings if warnings is not None else [
            {'categories': [HF], 'lines': [], 'banned': ['gs-bank-europe'], 'text': WORDS}]},
        'first', user='alice', base=0)


def _picture(*categories):
    """An allocation as the register keeps one: (name, weight, [(line, weight)])."""
    return {'categories': [{'name': n, 'weightPct': w,
                            'assets': [{'reportingName': a, 'weightPct': x} for a, x in lines]}
                           for n, w, lines in categories]}


# --------------------------------------------------------------- the lists ---

def test_both_lists_start_empty_and_a_read_never_writes(store):
    sleeveRepo.describe()
    now = marketing.current()
    assert (now['revision'], now['offering'], now['warnings']) == (0, [], [])
    assert marketing.served() == []
    assert marketing.current()['revision'] == 0
    vocab = {v['category']: v['lines'] for v in marketing.vocabulary()}
    assert vocab[HF] == ['Event Driven', 'Equity Long/Short', 'Tactical Trading']


def test_a_warning_names_asset_classes_the_entities_it_bars_and_words(store):
    saved = _lists()
    assert [e['id'] for e in saved['offering']] == ['gs-bank-europe', 'gs-middle-east']
    assert saved['warnings'] == [{'id': 'w1', 'categories': [HF], 'lines': [], 'banned': ['gs-bank-europe'],
                                  'text': WORDS}]
    assert marketing.served() == [{'id': 'w1', 'categories': [HF], 'lines': [], 'text': WORDS,
                                   'banned': ['gs-bank-europe'], 'bannedNames': ['GS Bank Europe']}]
    history = marketing.history()
    assert history[0]['changes'] == ['Added offering entity GS Bank Europe', 'Added offering entity GS Middle East',
                                     'Warning on Hedge Funds added: not by GS Bank Europe']


@pytest.mark.parametrize('warning, words', [
    ({'categories': [], 'lines': [], 'banned': ['gs-bank-europe'], 'text': 'x'}, 'names no asset class'),
    ({'categories': ['Crypto'], 'banned': ['gs-bank-europe'], 'text': 'x'}, "'Crypto' is not a category"),
    ({'lines': ['Moon Shots'], 'banned': ['gs-bank-europe'], 'text': 'x'}, "'Moon Shots' is not a line"),
    ({'categories': [HF], 'banned': [], 'text': 'x'}, 'bars no one'),
    ({'categories': [HF], 'banned': ['narnia'], 'text': 'x'}, "no offering entity 'narnia'"),
    ({'categories': [HF], 'banned': ['gs-bank-europe'], 'text': '  '}, 'needs the words'),
])
def test_a_warning_that_cannot_be_applied_is_refused(store, warning, words):
    with pytest.raises(ValidationError, match=words):
        _lists([warning])


def test_an_offering_entity_is_retired_never_deleted(store):
    saved = _lists()
    with pytest.raises(ValidationError, match='Retire it instead'):
        marketing.save({'offering': saved['offering'][1:], 'warnings': saved['warnings']}, 'gone', 'alice',
                       base=saved['revision'])
    with pytest.raises(ValidationError, match='Two offering entities'):
        marketing.save({'offering': saved['offering'] + [{'name': 'gs bank europe'}],
                        'warnings': saved['warnings']}, 'twice', 'alice', base=saved['revision'])
    retired = [dict(e, retired=True) if e['id'] == 'gs-bank-europe' else e for e in saved['offering']]
    marketing.save({'offering': retired, 'warnings': saved['warnings']}, 'retired', 'alice',
                   base=saved['revision'])
    assert marketing.served() == [], 'a warning barring only retired entities bars no one'
    assert marketing.history()[0]['changes'] == ['GS Bank Europe retired']


def test_a_warning_id_is_never_given_again(store):
    saved = _lists()
    emptied = marketing.save({'offering': saved['offering'], 'warnings': []}, 'drop', 'alice',
                             base=saved['revision'])
    assert marketing.history()[0]['changes'] == ['Warning on Hedge Funds removed']
    again = marketing.save({'offering': saved['offering'], 'warnings': [
        {'categories': [HF], 'banned': ['gs-middle-east'], 'text': 'x'}]}, 'again', 'alice',
        base=emptied['revision'])
    assert [w['id'] for w in again['warnings']] == ['w2']
    back = marketing.revert(1, 'the first lists', 'alice', base=again['revision'])
    assert [w['id'] for w in back['warnings']] == ['w1'] and back['nextWarning'] == 3


def test_a_stale_save_is_refused(store):
    saved = _lists()
    with pytest.raises(sleeveRepo.StaleError):
        marketing.save({'offering': saved['offering'], 'warnings': []}, 'late', 'bob', base=0)


# ------------------------------------------------------------ the warnings ---

def test_an_allocation_raises_the_warnings_on_what_it_holds(store):
    _lists([{'categories': [HF], 'banned': ['gs-bank-europe'], 'text': 'hedge funds'},
            {'lines': ['Tactical Trading'], 'banned': ['gs-middle-east'], 'text': 'trading'},
            {'categories': ['Private Equity'], 'banned': ['gs-middle-east'], 'text': 'private equity'}])
    held = _picture((HF, 10.0, [('Event Driven', 4.0), ('Tactical Trading', 6.0)]),
                    ('Public Equity', 90.0, []))
    found = marketing.warningsFor(held)
    assert [(w['id'], w['matched']) for w in found] == [
        ('w1', [{'name': HF, 'kind': 'category', 'weightPct': 10.0}]),
        ('w2', [{'name': 'Tactical Trading', 'kind': 'line', 'weightPct': 6.0}])]
    assert found[0]['bannedNames'] == ['GS Bank Europe']
    none = _picture((HF, 0.0, [('Tactical Trading', 0.0)]), ('Public Equity', 100.0, []))
    assert marketing.warningsFor(none) == [], 'a weight of nothing raises nothing'
    # a custom portfolio's line may stand for a group of categories
    grouped = _picture(('Private Equity & Other Private Assets', 8.0, []), ('Public Equity', 92.0, []))
    assert [w['id'] for w in marketing.warningsFor(grouped)] == ['w3']


def test_acknowledgements_are_kept_for_the_allocation_they_were_shown_for(store):
    _lists()
    first = marketing.acknowledge(None, 'USD|Moderate|Full|0', ['w1'], 'bob')
    assert first['allocation'] == 'USD|Moderate|Full|0' and first['acks'][0]['by'] == 'bob'
    assert marketing.acknowledge(first, 'USD|Moderate|Full|0', ['w1'], 'carol')['acks'] == first['acks']
    moved = marketing.acknowledge(first, 'USD|Growth|Full|0', ['w1'], 'carol')
    assert [(a['warning'], a['by']) for a in moved['acks']] == [('w1', 'carol')]
    with pytest.raises(ValidationError, match='no warning w9'):
        marketing.acknowledge(first, 'USD|Moderate|Full|0', ['w9'], 'bob')
    found = marketing.warningsFor(_picture((HF, 10.0, [])))
    assert marketing.acknowledged(found, first, 'USD|Moderate|Full|0')[0]['acknowledgedBy'] == 'bob'
    assert marketing.acknowledged(found, first, 'custom')[0]['acknowledgedBy'] == ''


# ------------------------------------------------- the scenario & register ---

def _scenario():
    from test_scenario_backend import BASIS, PORT, _sleeveMap
    from cyrus_pmg.pmgService.scenario import scenarioStore
    state = scenarioStore.createScenario(
        MandateInput(topAccountSize=1e9, mandateSize=1e9, primaryPwa='A. Castellanos — Madrid'),
        BASIS, createdBy='alice')
    result = PORT.resolve_portfolio(BASIS, USD_MOD_FULL)
    assert any(c['name'] == HF and c['weightPct'] > 0 for c in result['categories'])
    scenarioStore.updateScenario(state['id'], variant=MA)
    scenarioStore.recordColumn(state['id'], USD_MOD_FULL, 'base')
    scenarioStore.updateScenario(state['id'], sleeves=_sleeveMap(result['categories'], MA))
    # the hedge fund notice, which a proposal holding one needs (D198)
    from test_scenario_backend import _confirmHedgeFunds
    _confirmHedgeFunds(state['id'])
    return state['id']


def test_the_schema_serves_the_warnings_and_the_scenario_keeps_who_acknowledged(store, monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    _lists()
    schema = dashboardRouter.getScenarioSchema(None, variant=MA, caller=_caller())
    assert [w['id'] for w in schema['options']['marketing']] == ['w1']
    scenarioId = _scenario()
    body = dashboardRouter.updateScenario(scenarioId, {'marketingAck': {
        'allocation': USD_MOD_FULL.toStr(), 'warnings': ['w1']}}, caller=_caller())
    assert body['marketing']['allocation'] == USD_MOD_FULL.toStr()
    assert [(a['warning'], a['by']) for a in body['marketing']['acks']] == [('w1', 'alice')]
    assert dashboardRouter.getScenario(scenarioId)['marketing'] == body['marketing']
    refused = dashboardRouter.updateScenario(scenarioId, {'marketingAck': {
        'allocation': USD_MOD_FULL.toStr(), 'warnings': ['w7']}}, caller=_caller())
    assert refused.status_code == 422 and json.loads(refused.body)['field'] == 'marketingAck'


def test_the_register_records_each_warning_and_its_acknowledgement(store, monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    _lists()
    unread = _scenario()
    done = dashboardRouter.exportScenario(unread, caller=_caller())
    assert done.status_code == 200, done.body
    kept = proposalRegister.getProposal(done.headers['X-Proposal-Id'])['marketing']
    assert [(w['id'], w['bannedNames'], w['acknowledgedBy']) for w in kept] == [('w1', ['GS Bank Europe'], '')]
    scenarioId = _scenario()
    dashboardRouter.updateScenario(scenarioId, {'marketingAck': {
        'allocation': USD_MOD_FULL.toStr(), 'warnings': ['w1']}}, caller=_caller('bob'))
    done = dashboardRouter.exportScenario(scenarioId, caller=_caller())
    kept = proposalRegister.getProposal(done.headers['X-Proposal-Id'])['marketing']
    assert kept[0]['acknowledgedBy'] == 'bob' and kept[0]['text'] == WORDS
    assert kept[0]['matched'][0]['name'] == HF
    rows = [dict(zip(proposalRegister.EXPORT_COLUMNS, r)) for r in proposalRegister.exportRows()]
    texts = sorted(r['MarketingWarnings'] for r in rows)
    assert texts[0].startswith('Hedge Funds ') and ' - not by GS Bank Europe - acknowledged by bob ' in texts[0]
    assert texts[1].endswith(' - not by GS Bank Europe - not acknowledged')


def test_an_allocation_without_the_asset_class_records_none(store, monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    _lists([{'categories': ['Asset Allocation Strategies'], 'banned': ['gs-bank-europe'], 'text': 'x'}])
    done = dashboardRouter.exportScenario(_scenario(), caller=_caller())
    assert done.status_code == 200, done.body
    assert proposalRegister.getProposal(done.headers['X-Proposal-Id'])['marketing'] is None
