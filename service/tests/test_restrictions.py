"""D203: the investment restrictions an account opening request may name.

The admin keeps the list in the console's Account Opening tab. It starts with
the five the desk gave; every change is a revision of the whole list, nothing
is ever taken out - a restriction no longer offered is retired - and an
earlier revision can be put back. A request names any number of those on
offer, each as it read at Submit.
"""

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import repositoryFeed, restrictions, sleeveRepo
from cyrus_pmg.pmgService.scenario.types import ValidationError
from test_proposal_register import _caller


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path / 'store'))
    sleeveRepo.describe()
    yield tmp_path


def _list():
    return [dict(r) for r in restrictions.current()['restrictions']]


def test_the_list_starts_with_the_desks_five_and_a_read_writes_nothing(store):
    now = restrictions.current()
    assert now['revision'] == 0
    assert [r['name'] for r in now['restrictions']] == [
        'Capital/Income Split', 'No UK Situs Asset', 'No UK Listed Asset', 'No French Situs Assets',
        'No UK Reporting Funds']
    assert [r['id'] for r in now['restrictions']] == ['r1', 'r2', 'r3', 'r4', 'r5']
    assert all(r['active'] and r['description'] == '' for r in now['restrictions'])
    assert restrictions.history() == []
    assert [r['name'] for r in restrictions.served()] == restrictions.SEED
    assert restrictions.describe()['seed'] == restrictions.SEED


def test_a_change_is_a_revision_with_what_moved(store):
    rows = _list()
    rows[1]['description'] = 'No assets sited in the UK.'
    rows[3]['active'] = False
    rows.insert(0, {'name': 'No  leverage ', 'description': ''})
    rows[2], rows[3] = rows[3], rows[2]                    # No UK Listed Asset before No UK Situs Asset
    rows[4]['name'] = 'No UK Listed Assets'
    restrictions.save({'restrictions': rows}, 'Desk review', user='alice', base=0)
    now = restrictions.current()
    assert now['revision'] == 1 and now['updatedBy'] == 'alice'
    assert [r['id'] for r in now['restrictions']] == ['r6', 'r1', 'r3', 'r2', 'r4', 'r5']
    assert now['restrictions'][0]['name'] == 'No leverage', 'whitespace tidied'
    [top] = restrictions.history()
    assert top['note'] == 'Desk review' and top['current']
    assert top['changes'] == [
        'Added No leverage',
        'No UK Situs Asset: description added',
        'No French Situs Assets renamed No UK Listed Assets',
        'No UK Listed Assets retired',
        'Order now: No leverage, Capital/Income Split, No UK Listed Asset, No UK Situs Asset, '
        'No UK Listed Assets, No UK Reporting Funds']
    # a retired restriction leaves the form, not the list
    assert 'r4' not in {r['id'] for r in restrictions.served()}
    assert len(restrictions.current()['restrictions']) == 6
    # and the Activity tab has it
    feed = [e for e in repositoryFeed.activity(limit=50)['entries'] if e['area'] == 'restrictions']
    assert [e['name'] for e in feed] == ['Investment restrictions']
    assert feed[0]['changes'] == top['changes'] and feed[0]['note'] == 'Desk review'


@pytest.mark.parametrize('change, words', [
    (lambda rows: rows.pop(2), 'Retire No UK Listed Asset rather than taking it out'),
    (lambda rows: rows[1].update(name='capital/income split'), 'Two restrictions are called'),
    (lambda rows: rows[0].update(name='  '), 'needs a name'),
    (lambda rows: rows[0].update(name='x' * 121), 'to 120 characters'),
    (lambda rows: rows[0].update(description='x' * 401), 'to 400 characters'),
    (lambda rows: rows.append(dict(rows[0])), 'appears twice'),
    (lambda rows: rows[0].update(id='r42'), "no restriction 'r42'"),
])
def test_a_list_that_drops_or_muddles_one_is_refused(store, change, words):
    rows = _list()
    change(rows)
    with pytest.raises(ValidationError) as caught:
        restrictions.save({'restrictions': rows}, user='alice')
    assert words in str(caught.value)
    assert restrictions.current()['revision'] == 0


def test_the_same_list_or_a_stale_one_is_refused(store):
    with pytest.raises(ValidationError, match='already in force'):
        restrictions.save({'restrictions': _list()}, user='alice')
    rows = _list()
    rows[0]['description'] = 'Income and capital kept apart.'
    restrictions.save({'restrictions': rows}, user='alice', base=0)
    rows[1]['description'] = 'Late.'
    with pytest.raises(sleeveRepo.StaleError, match='changed by alice'):
        restrictions.save({'restrictions': rows}, user='bob', base=0)


def test_putting_back_an_earlier_list_keeps_what_was_added_since_retired(store):
    rows = _list()
    rows.append({'name': 'No leverage', 'description': ''})
    restrictions.save({'restrictions': rows}, user='alice', base=0)
    rows = _list()
    rows[0]['active'] = False
    restrictions.save({'restrictions': rows}, user='alice', base=1)
    restrictions.revert(0, user='bob', base=2)
    now = restrictions.current()
    assert now['revision'] == 3
    assert [(r['id'], r['active']) for r in now['restrictions']] == [
        ('r1', True), ('r2', True), ('r3', True), ('r4', True), ('r5', True), ('r6', False)]
    top = restrictions.history()[0]
    assert top['action'] == 'reverted' and top['note'] == 'Restored revision 0'
    assert top['changes'] == ['Capital/Income Split put back', 'No leverage retired']
    # an id is never given to another
    rows = _list() + [{'name': 'No derivatives'}]
    restrictions.save({'restrictions': rows}, user='alice', base=3)
    assert restrictions.current()['restrictions'][-1]['id'] == 'r7'


def test_a_request_names_only_those_on_offer_each_as_it_reads(store):
    assert restrictions.chosen(None) == [] and restrictions.chosen([]) == []
    assert restrictions.chosen(['r5', 'r1']) == [
        {'id': 'r1', 'name': 'Capital/Income Split', 'description': ''},
        {'id': 'r5', 'name': 'No UK Reporting Funds', 'description': ''}]
    rows = _list()
    rows[0]['active'] = False
    restrictions.save({'restrictions': rows}, user='alice')
    for bad in (['r1'], ['r9'], 'r2', ['r2', 'r2']):
        with pytest.raises(ValidationError) as caught:
            restrictions.chosen(bad)
        assert caught.value.field == 'restrictions'


def test_the_tab_is_the_admins_and_answers_over_its_routes(store):
    got = dashboardRouter.getRestrictions(caller=_caller('alice'))
    assert got['revision'] == 0 and got['nameMax'] == 120 and got['descriptionMax'] == 400
    rows = got['restrictions'] + [{'name': 'No leverage'}]
    saved = dashboardRouter.saveRestrictions({'restrictions': rows, 'note': 'n', 'base': 0},
                                             caller=_caller('alice'))
    assert saved['restrictions']['revision'] == 1
    stale = dashboardRouter.saveRestrictions({'restrictions': rows, 'base': 0}, caller=_caller('alice'))
    assert stale.status_code == 409
    bad = dashboardRouter.saveRestrictions({'restrictions': rows[:2], 'base': 1}, caller=_caller('alice'))
    assert bad.status_code == 422
    assert dashboardRouter.getRestrictionsHistory(caller=_caller('alice'))['history'][0]['revision'] == 1
    back = dashboardRouter.revertRestrictions({'revision': 0, 'base': 1}, caller=_caller('alice'))
    assert back['restrictions']['revision'] == 2
    from cyrus_pmg.pmgService.core.accessControl import requireAdmin
    for route in dashboardRouter.router.routes:
        if route.path.startswith('/scenario/repository/restrictions'):
            assert requireAdmin in [d.call for d in route.dependant.dependencies], route.path
