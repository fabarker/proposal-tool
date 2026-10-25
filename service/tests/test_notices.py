"""D199: the notices.

A notice is about sleeve categories, product categories, product asset
classes and particular products. When a sleeve or a product it is about is
attached to a proposal the PWA confirms it - ticking its box first where it
asks for one - or does not attach it; either is recorded on the proposal as
the signed-in user, once a proposal for a confirmation. The download refuses
while a notice the proposal raises is unconfirmed, and the register keeps each
notice raised with who confirmed it. The admin keeps them on the Notices tab:
Active or Disabled, removed into the history, every save a revision.
"""

import json

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import (marketing, notices, products, proposalRegister,
                                           scenarioStore, sleeveRepo)
from cyrus_pmg.pmgService.scenario.types import MandateInput, PortfolioKey, ValidationError

MA = 'PMG Multi-Asset Portfolio'
USD_MOD_FULL = PortfolioKey('USD', 'Moderate', 'Full', False)
HF = 'Hedge Funds'
PE = 'Private Equity & Other Private Assets'


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path / 'store'))
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice,bob')
    products.reload()
    sleeveRepo.describe()
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


def _notice(**over):
    n = {'name': 'Private markets · liquidity', 'active': True,
         'about': {'sleeveCategories': [PE], 'productCategories': [], 'assetClasses': [], 'products': []},
         'title': 'Private markets: lock-ups and capital calls', 'body': 'Check the client can wait.',
         'tick': False, 'tickWords': ''}
    n.update(over)
    return n


def _with(*added, base=0):
    return notices.save({'notices': notices.current()['notices'] + list(added)}, 'n', user='alice', base=base)


def _act(scenarioId, notice, action='confirm', user='alice', ticked=True, sleeve='HFM & ART'):
    return dashboardRouter.updateScenario(scenarioId, {'noticeAction': {
        'notice': notice, 'action': action, 'ticked': ticked, 'category': HF, 'sleeve': sleeve}},
        caller=_caller(user))


# ------------------------------------------------------------- the list ---

def test_the_first_list_carries_the_hedge_fund_notice_and_the_marketing_warnings(store):
    first = notices.current()
    assert first['revision'] == 0 and [n['id'] for n in first['notices']] == ['n1']
    hf = first['notices'][0]
    assert hf['about']['productCategories'] == [HF] and hf['tick'] and hf['active']
    assert hf['title'] == marketing.NOTICE['title'] and hf['tickWords'] == marketing.NOTICE['attestation']
    marketing.save({'offering': [{'name': 'Qatar'}], 'warnings': [
        {'categories': [HF], 'lines': ['Buyout'], 'banned': ['qatar'], 'text': 'Not in Qatar.'}]},
        'first', user='alice', base=0)
    carried = notices.current()['notices'][1]
    assert carried['id'] == 'n2' and carried['about']['sleeveCategories'] == [HF, PE]
    assert carried['body'] == 'Not in Qatar.\n\nMay not be marketed by: Qatar.' and not carried['tick']
    assert notices.current()['revision'] == 0, 'a read never writes'
    schema = dashboardRouter.getScenarioSchema(None, variant=MA, caller=_caller())
    assert [n['id'] for n in schema['options']['notices']] == ['n1', 'n2']
    assert 'marketing' not in schema['options'] and 'hedgeFundNotice' not in schema['options']


def test_a_notice_is_added_edited_disabled_removed_and_restored(store):
    saved = _with(_notice())
    assert [n['id'] for n in saved['notices']] == ['n1', 'n2'] and saved['revision'] == 1
    assert notices.history()[0]['changes'] == ['Added Private markets · liquidity']
    listed = [dict(n) for n in notices.current()['notices']]
    listed[1].update(active=False, body='Lock-ups.\r\n\r\n\r\n1. One', tick=True, tickWords=' I  will. ')
    after = notices.save({'notices': listed}, '', user='bob', base=1)
    pm = after['notices'][1]
    assert (pm['active'], pm['body'], pm['tickWords']) == (False, 'Lock-ups.\n\n1. One', 'I will.')
    assert notices.history()[0]['changes'] == ['Private markets · liquidity disabled',
                                               'Private markets · liquidity: words changed',
                                               'Private markets · liquidity: tick box asked for']
    assert [n['id'] for n in notices.served()] == ['n1'], 'a disabled notice is not served'
    gone = notices.save({'notices': after['notices'][:1]}, 'not wanted', user='bob', base=2)
    assert [n['id'] for n in gone['notices']] == ['n1']
    assert notices.history()[0]['changes'] == ['Removed Private markets · liquidity']
    back = notices.revert(2, user='bob', base=3)
    assert [n['id'] for n in back['notices']] == ['n1', 'n2']
    new = notices.save({'notices': back['notices'] + [_notice(name='Another')]}, '', user='bob', base=4)
    assert new['notices'][-1]['id'] == 'n3', 'an id is never given again'
    with pytest.raises(sleeveRepo.StaleError):
        notices.save({'notices': []}, '', user='bob', base=1)


@pytest.mark.parametrize('over, words', [
    ({'name': ''}, 'needs a name'),
    ({'title': ''}, 'needs a title'),
    ({'body': ' '}, 'the words the PWA confirms'),
    ({'tick': True, 'tickWords': ''}, 'give the box its words'),
    ({'about': {'sleeveCategories': [], 'productCategories': [], 'assetClasses': [], 'products': []}},
     'is about nothing'),
    ({'about': {'sleeveCategories': ['Made Up']}}, 'not a category'),
    ({'about': {'products': ['no-such']}}, 'no product'),
    ({'name': 'Hedge funds · DNE eligibility'}, 'Two notices are called'),
])
def test_a_notice_that_is_not_one_is_refused(store, over, words):
    with pytest.raises(ValidationError) as caught:
        _with(_notice(**over))
    assert caught.value.field == 'notices' and words in caught.value.message


def test_the_console_routes_save_and_restore_as_the_signed_in_admin(store):
    body = dashboardRouter.saveNotices({'notices': notices.current()['notices'] + [_notice()], 'base': 0},
                                       caller=_caller())
    assert body['notices']['updatedBy'] == 'alice' and len(body['notices']['notices']) == 2
    assert body['notices']['vocabulary']['sleeveCategories'] == sleeveRepo.categories()
    assert dashboardRouter.getNoticesHistory(caller=_caller())['history'][0]['revision'] == 1
    stale = dashboardRouter.saveNotices({'notices': [], 'base': 0}, caller=_caller())
    assert stale.status_code == 409
    assert dashboardRouter.getRepository(caller=_caller())['notices']['revision'] == 1


# -------------------------------------------------------------- matching ---

def test_a_sleeve_or_a_product_raises_the_notices_it_is_about(store):
    gm = next(p for p in products.all() if p['assetClass'] == 'Global Macro')
    _with(_notice(), _notice(name='Macro', about={'assetClasses': ['Global Macro']}),
          _notice(name='One fund', about={'products': [gm['productId']]}))
    hf = next(p for p in products.all() if p['ownCategory'] == HF)
    plain = next(p for p in products.all() if p['ownCategory'] not in (HF, PE) and p['assetClass'] != 'Global Macro')
    ids = lambda cat, ps: [n['id'] for n in notices.raisedBy(cat, ps)]
    assert ids(HF, [hf]) == ['n1'], 'by its own category'
    assert ids(PE, [plain]) == ['n2'], 'by the sleeve category'
    assert ids('Public Equity', [gm]) == ['n1', 'n3', 'n4'], 'by its category, its asset class and its name'
    assert ids('Public Equity', [dict(plain, alternative=hf)]) == ['n1'], 'the product held below a minimum'
    assert ids('Public Equity', [plain]) == []


# ---------------------------------------------------------------- record ---

def test_a_confirmation_needs_the_tick_where_asked_and_is_once_a_proposal(store):
    scenarioId = _scenario()
    refused = _act(scenarioId, 'n1', ticked=False)
    assert refused.status_code == 422 and 'Tick the box' in json.loads(refused.body)['error']
    body = _act(scenarioId, 'n1', action='decline', user='bob')
    assert body['notices']['n1']['confirmed'] is None
    assert [(d['by'], d['sleeve']) for d in body['notices']['n1']['declined']] == [('bob', 'HFM & ART')]
    done = _act(scenarioId, 'n1')['notices']['n1']['confirmed']
    assert (done['by'], done['ticked'], done['tickWords'], done['revision']) == (
        'alice', True, marketing.NOTICE['attestation'], 0)
    assert _act(scenarioId, 'n1', user='bob')['notices']['n1']['confirmed'] == done
    assert _act(scenarioId, 'n1', action='decline').status_code == 422
    assert _act(scenarioId, 'n9').status_code == 422
    # a notice without a tick box is confirmed without one
    _with(_notice())
    plain = _act(scenarioId, 'n2', ticked=False)['notices']['n2']['confirmed']
    assert plain['ticked'] is False and plain['tickWords'] == ''


def test_a_record_from_before_the_notices_is_the_hedge_fund_notices(store):
    scenarioId = _scenario()
    state = scenarioStore.getScenario(scenarioId)
    state['hedgeFundNotice'] = {'confirmed': {'by': 'old', 'at': '2026-10-22T10:00:00'}, 'declined': []}
    assert notices.recordOf(state)['n1']['confirmed']['by'] == 'old'
    assert notices.confirmedIds(state) == {'n1'}


def test_the_download_waits_for_every_notice_raised_and_the_register_keeps_them(store):
    _with(_notice())
    scenarioId = _scenario()
    refused = dashboardRouter.exportScenario(scenarioId, caller=_caller())
    said = json.loads(refused.body)
    assert refused.status_code == 422 and said['field'] == 'notice'
    assert 'Hedge funds · DNE eligibility' in said['error'] and 'Private markets · liquidity' in said['error']
    _act(scenarioId, 'n1', user='bob')
    still = json.loads(dashboardRouter.exportScenario(scenarioId, caller=_caller()).body)
    assert 'Hedge funds' not in still['error'] and 'Private markets' in still['error']
    _act(scenarioId, 'n2', ticked=False)
    done = dashboardRouter.exportScenario(scenarioId, caller=_caller())
    assert done.status_code == 200, done.body
    kept = proposalRegister.getProposal(done.headers['X-Proposal-Id'])['notices']
    assert [(n['id'], n['confirmed']['by'], bool(n['holds'])) for n in kept] == [
        ('n1', 'bob', True), ('n2', 'alice', True)]
    rows = [dict(zip(proposalRegister.EXPORT_COLUMNS, r)) for r in proposalRegister.exportRows()]
    assert rows[0]['Notices'].startswith('Hedge funds · DNE eligibility - confirmed by bob ')
    assert ', ticked) - raised by ' in rows[0]['Notices']
    assert proposalRegister.describe()['schemaVersion'] == 14


def test_a_disabled_notice_asks_for_nothing(store):
    listed = [dict(n, active=False) for n in notices.current()['notices']]
    notices.save({'notices': listed}, '', user='alice', base=0)
    done = dashboardRouter.exportScenario(_scenario(), caller=_caller())
    assert done.status_code == 200, done.body
    assert proposalRegister.getProposal(done.headers['X-Proposal-Id'])['notices'] is None
