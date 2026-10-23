"""D198: the hedge fund notice.

A hedge fund - a product whose catalogue category is Hedge Funds - may not be
marketed to a client until its registration where the client is domiciled
has been checked. The first time a sleeve holding one is attached to a
proposal, the PWA is shown the desk's notice and confirms it, or does not
attach the sleeve; either is recorded on the proposal as the signed-in user,
and the register keeps the confirmation beside a proposal that holds a hedge
fund. The wording is the admin's, kept with the marketing lists. A proposal
holding a hedge fund is not delivered without it.
"""

import json

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import (marketing, products, proposalRegister, scenarioStore,
                                           sleeveRepo)
from cyrus_pmg.pmgService.scenario.types import MandateInput, PortfolioKey, ValidationError

MA = 'PMG Multi-Asset Portfolio'
USD_MOD_FULL = PortfolioKey('USD', 'Moderate', 'Full', False)
HF = 'Hedge Funds'


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path / 'store'))
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice,bob')
    products.reload()
    yield tmp_path
    products.reload()


def _caller(kerberos='alice'):
    from cyrus_pmg.pmgService.core import accessControl
    return accessControl.UserData(kerberos, accessControl._rolesFor(kerberos))


def _scenario():
    from test_scenario_backend import BASIS, PORT, _sleeveMap
    state = scenarioStore.createScenario(
        MandateInput(topAccountSize=1e9, mandateSize=1e9, primaryPwa='A. Castellanos — Madrid'),
        BASIS, createdBy='alice')
    result = PORT.resolve_portfolio(BASIS, USD_MOD_FULL)
    scenarioStore.updateScenario(state['id'], variant=MA)
    scenarioStore.recordColumn(state['id'], USD_MOD_FULL, 'base')
    scenarioStore.updateScenario(state['id'], sleeves=_sleeveMap(result['categories'], MA))
    return state['id']


def _held(scenarioId):
    """The hedge funds the scenario's Hedge Funds sleeve holds, by name: the
    packaged catalogue gives them the category Hedge Funds."""
    name = scenarioStore.getScenario(scenarioId)['sleeves'][HF]
    entry = next(e for e in sleeveRepo.listAll(MA) if e['category'] == HF and e['name'] == name)
    return [products.get(r['productId'])['name'] for r in entry['products']
            if products.get(r['productId'])['hedgeFund']]


def _clearHedgeFunds():
    """Every product's category Hedge Funds taken off: a catalogue that
    marks no hedge fund."""
    for p in products.entries():
        if p['category'] == HF:
            fields = {k: p[k] for k in products.KEYS}
            fields.update(currencies=p['currencies'], category='')
            products.update(p['productId'], fields, user='alice', base=p['revision'])


def _respond(scenarioId, action, user='alice', sleeve='Global Hedge Fund Sleeve', attested=True):
    return dashboardRouter.updateScenario(scenarioId, {'hedgeFundNotice': {
        'action': action, 'attested': attested, 'category': HF, 'sleeve': sleeve}}, caller=_caller(user))


# --------------------------------------------------------------- the wording ---

def test_the_desks_wording_is_in_force_until_the_admin_words_it(store):
    sleeveRepo.describe()
    held = marketing.notice()
    assert held['revision'] == 0
    assert held['title'] == ('Hedge Funds in Non-Discretionary (DNE) accounts: check fund eligibility '
                             'before marketing')
    assert held['body'].startswith('Your intended proposed portfolio includes an allocation to Hedge Funds.\n\n')
    assert '\n1. You MUST check the fund is registered for sale' in held['body']
    assert held['body'].endswith('Confirm you understand by clicking confirm below.')
    assert held['attestation'] == ('I understand that I am responsible for completing these checks before '
                                   'sharing any information with the client.')
    schema = dashboardRouter.getScenarioSchema(None, variant=MA, caller=_caller())
    assert schema['options']['hedgeFundNotice'] == held
    assert marketing.describe()['noticeDefault'] == marketing.NOTICE


def test_the_admin_words_it_with_a_note_and_a_revision_and_can_put_it_back(store):
    sleeveRepo.describe()
    marketing.save({'offering': [], 'warnings': [], 'hedgeFundNotice': {
        'title': '  Hedge funds:   check eligibility ', 'body': 'Check it.\r\n\r\n\r\n1. One\n2.  Two  ',
        'attestation': ' I  will check. '}},
        'reworded', user='alice', base=0)
    held = marketing.notice()
    assert (held['title'], held['body'], held['attestation'], held['revision']) == (
        'Hedge funds: check eligibility', 'Check it.\n\n1. One\n2. Two', 'I will check.', 1)
    assert marketing.history()[0]['changes'] == ['Hedge fund notice: title changed',
                                                 'Hedge fund notice: words changed',
                                                 'Hedge fund notice: tick box words changed']
    # a notice sent without its tick box words keeps the desk's
    assert marketing.normalise({'hedgeFundNotice': {'title': 'T', 'body': 'B'}}, marketing._empty())[
        'hedgeFundNotice']['attestation'] == marketing.NOTICE['attestation']
    # a save that does not send it keeps it
    marketing.save({'offering': [{'name': 'GS Bank Europe'}], 'warnings': []}, 'entity', user='alice', base=1)
    assert marketing.notice()['title'] == 'Hedge funds: check eligibility'
    marketing.revert(1, 'back', user='alice', base=2)
    assert marketing.notice()['title'] == 'Hedge funds: check eligibility'
    marketing.save({'offering': [{'id': 'gs-bank-europe', 'name': 'GS Bank Europe', 'retired': True}],
                    'warnings': [], 'hedgeFundNotice': marketing.NOTICE}, 'desk', user='alice', base=3)
    assert marketing.notice()['title'] == marketing.NOTICE['title']
    offering = marketing.current()['offering']
    for bad, words in (({'title': '', 'body': 'x'}, 'a title'), ({'title': 'x', 'body': ' '}, 'the words'),
                       ({'title': 'x' * 161, 'body': 'x'}, '160 characters'),
                       ({'title': 'x', 'body': 'x', 'attestation': ' '}, 'tick box its words'),
                       ({'title': 'x', 'body': 'x', 'attestation': 'x' * 301}, '300 characters'),
                       ('words', 'a title, a body and the words of its tick box')):
        with pytest.raises(ValidationError) as caught:
            marketing.save({'offering': offering, 'warnings': [], 'hedgeFundNotice': bad}, 'bad', user='alice')
        assert caught.value.field == 'hedgeFundNotice' and words in caught.value.message


def test_the_console_route_saves_the_wording(store):
    sleeveRepo.describe()
    body = dashboardRouter.saveMarketing({'offering': [], 'warnings': [], 'note': 'n', 'base': 0,
                                          'hedgeFundNotice': {'title': 'T', 'body': 'B', 'attestation': 'A'}},
                                         caller=_caller())
    assert body['marketing']['hedgeFundNotice'] == {'title': 'T', 'body': 'B', 'attestation': 'A'}


# ------------------------------------------------------------ the products ---

def test_a_product_is_served_as_a_hedge_fund_by_its_own_category(store):
    marked = [p['productId'] for p in products.entries() if p['category'] == HF]
    assert marked and [p['productId'] for p in products.all() if p['hedgeFund']] == marked
    assert not any('category' in p for p in products.all())
    scenarioId = _scenario()
    sleeve = scenarioStore.getScenario(scenarioId)['sleeves'][HF]
    served = dashboardRouter.listSleeves(category=HF, variant=MA, key=USD_MOD_FULL.toStr())
    mine = next(s for s in served['sleeves'] if s['name'] == sleeve)
    assert [p['name'] for p in mine['products'] if p['hedgeFund']] == _held(scenarioId)
    _clearHedgeFunds()
    assert not any(p['hedgeFund'] for p in products.all())


# --------------------------------------------------------------- the record ---

def test_a_confirmation_is_the_signed_in_users_once_a_proposal(store):
    scenarioId = _scenario()
    body = _respond(scenarioId, 'decline', user='bob')
    assert body['hedgeFundNotice']['confirmed'] is None
    assert [(d['by'], d['category'], d['sleeve']) for d in body['hedgeFundNotice']['declined']] == [
        ('bob', HF, 'Global Hedge Fund Sleeve')]
    # the box must be ticked: a confirmation without it is refused, and nothing is kept
    for unticked in (False, None, 'yes'):
        refused = _respond(scenarioId, 'confirm', attested=unticked)
        assert refused.status_code == 422 and 'Tick the box' in json.loads(refused.body)['error']
    assert dashboardRouter.getScenario(scenarioId)['hedgeFundNotice']['confirmed'] is None
    body = _respond(scenarioId, 'confirm')
    done = body['hedgeFundNotice']['confirmed']
    assert (done['by'], done['revision'], done['title'], done['sleeve']) == (
        'alice', 0, marketing.NOTICE['title'], 'Global Hedge Fund Sleeve')
    assert done['attestation'] == marketing.NOTICE['attestation'], 'the words ticked are kept'
    assert done['at'] and len(body['hedgeFundNotice']['declined']) == 1, 'the decline stays on record'
    again = _respond(scenarioId, 'confirm', user='bob')
    assert again['hedgeFundNotice']['confirmed'] == done, 'once a proposal: the first stands'
    refused = _respond(scenarioId, 'decline')
    assert refused.status_code == 422 and json.loads(refused.body)['field'] == 'hedgeFundNotice'
    refused = dashboardRouter.updateScenario(scenarioId, {'hedgeFundNotice': {'action': 'maybe'}},
                                             caller=_caller())
    assert refused.status_code == 422
    assert dashboardRouter.getScenario(scenarioId)['hedgeFundNotice']['confirmed'] == done


def test_a_proposal_holding_a_hedge_fund_is_not_delivered_without_it(store):
    scenarioId = _scenario()
    names = _held(scenarioId)
    assert names
    refused = dashboardRouter.exportScenario(scenarioId, caller=_caller())
    assert refused.status_code == 422
    said = json.loads(refused.body)
    assert said['field'] == 'hedgeFundNotice' and names[0] in said['error']
    _respond(scenarioId, 'confirm', user='bob')
    done = dashboardRouter.exportScenario(scenarioId, caller=_caller())
    assert done.status_code == 200, done.body
    kept = proposalRegister.getProposal(done.headers['X-Proposal-Id'])['hedgeFundNotice']
    assert (kept['by'], kept['revision']) == ('bob', 0) and names[0] in kept['holds']
    assert kept['title'] == marketing.NOTICE['title'] and kept['at']
    assert kept['attestation'] == marketing.NOTICE['attestation']
    rows = [dict(zip(proposalRegister.EXPORT_COLUMNS, r)) for r in proposalRegister.exportRows()]
    text = rows[0]['HedgeFundNotice']
    assert text.startswith('confirmed by bob ') and '(wording r0) - holds ' + names[0] in text


def test_a_proposal_holding_none_records_none(store):
    _clearHedgeFunds()
    scenarioId = _scenario()
    done = dashboardRouter.exportScenario(scenarioId, caller=_caller())
    assert done.status_code == 200, done.body
    assert proposalRegister.getProposal(done.headers['X-Proposal-Id'])['hedgeFundNotice'] is None
    rows = [dict(zip(proposalRegister.EXPORT_COLUMNS, r)) for r in proposalRegister.exportRows()]
    assert rows[0]['HedgeFundNotice'] == ''
