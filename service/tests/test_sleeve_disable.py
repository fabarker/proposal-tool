"""A sleeve disabled for PWAs (D169): kept in the library, the console and the
record, left out of everything a PWA picks from, and switched back on as
quickly. Not a version of the sleeve, so nothing pinned to it moves."""
import os
import sqlite3

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.core import accessControl
from cyrus_pmg.pmgService.scenario import (feeCard, products, proposalRegister, repositoryFeed,
                                           sleeveRepo, sleeves)
from cyrus_pmg.pmgService.scenario.types import PortfolioKey, ValidationError

V = 'PMG Multi-Asset Portfolio'
C = 'Investment Grade Fixed Income'
GBP_MOD_FULL = PortfolioKey('GBP', 'Moderate', 'Full', False)
USD_MOD_FULL = PortfolioKey('USD', 'Moderate', 'Full', False)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    products.reload()
    feeCard._forget()
    yield tmp_path
    products.reload()
    feeCard._forget()


def _caller(kerberos='alice'):
    return accessControl.UserData(kerberos, accessControl._rolesFor(kerberos))


def _sleeve(name, label=''):
    return next(e for e in sleeveRepo.listAll(V)
                if e['category'] == C and e['name'] == name and e['label'] == label)


def _names(key=None):
    return [s['name'] for s in sleeves.listSleeves(C, V, key)]


def test_a_disabled_sleeve_leaves_the_picker_and_stays_in_the_library(store):
    etfs = _sleeve('ETFs Only')
    assert not etfs['disabled'] and 'ETFs Only' in _names()
    off = sleeveRepo.setOffered(etfs['id'], False, user='alice')
    assert off['disabled'] and off['disabledBy'] == 'alice' and off['disabledAt']
    assert 'ETFs Only' not in _names() and not sleeves.sleeveExists(C, 'ETFs Only', V)
    # the console still lists it, whole, and it can still be edited
    assert _sleeve('ETFs Only')['disabled'] and _sleeve('ETFs Only')['products'] == etfs['products']
    assert len(_names()) == len([e for e in sleeveRepo.listAll(V) if e['category'] == C]) - 1
    edited = sleeveRepo.updateSleeve(etfs['id'], 'ETFs Only', [
        {'productId': r['productId'], 'weight': r['weight']} for r in etfs['products']],
        note='still off', user='bob')
    assert edited['disabled'], 'an edit does not put it back in front of PWAs'
    on = sleeveRepo.setOffered(etfs['id'], True, user='bob')
    assert not on['disabled'] and on['disabledBy'] == '' and on['disabledAt'] == ''
    assert 'ETFs Only' in _names()


def test_a_switch_is_not_a_version_so_nothing_pinned_moves(store):
    etfs = _sleeve('ETFs Only')
    moved = proposalRegister.movedCounts()
    before = sleeveRepo.revisionMap()[etfs['id']]
    history = sleeveRepo.history(etfs['id'])
    sleeveRepo.setOffered(etfs['id'], False, user='alice')
    sleeveRepo.setOffered(etfs['id'], True, user='alice')
    assert sleeveRepo.revisionMap()[etfs['id']] == before
    assert proposalRegister.movedCounts() == moved
    assert sleeveRepo.history(etfs['id']) == history
    assert _sleeve('ETFs Only')['createdAt'] == etfs['createdAt']
    # an editor opened before the switch still saves against its base
    sleeveRepo.setOffered(etfs['id'], False, user='alice')
    saved = sleeveRepo.updateSleeve(etfs['id'], 'ETFs Only', [
        {'productId': r['productId'], 'weight': r['weight']} for r in etfs['products']],
        note='after the switch', user='bob', base=etfs['revisions'])
    assert saved['note'] == 'after the switch'


def test_a_disabled_edition_lets_its_name_fall_back(store):
    fb = _sleeve('ETFs Only')
    gbp = sleeveRepo.addEdition(fb['id'], 'GBP', [{'currency': 'GBP'}],
                                [{'productId': r['productId'], 'weight': r['weight']}
                                 for r in fb['products']], user='desk')
    served = lambda key: next(s['id'] for s in sleeves.listSleeves(C, V, key) if s['name'] == 'ETFs Only')
    assert served(GBP_MOD_FULL) == gbp['id']
    sleeveRepo.setOffered(gbp['id'], False, user='desk')
    assert served(GBP_MOD_FULL) == fb['id'], 'the fallback answers for GBP while the edition is off'
    sleeveRepo.setOffered(fb['id'], False, user='desk')
    assert 'ETFs Only' not in _names(GBP_MOD_FULL) and 'ETFs Only' not in _names(USD_MOD_FULL)
    sleeveRepo.setOffered(gbp['id'], True, user='desk')
    assert served(GBP_MOD_FULL) == gbp['id'] and 'ETFs Only' not in _names(USD_MOD_FULL)


def test_the_switch_is_refused_where_it_cannot_mean_anything(store):
    etfs = _sleeve('ETFs Only')
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.setOffered(etfs['id'], True, user='alice')
    assert exc.value.message == 'ETFs Only is already offered to PWAs.'
    sleeveRepo.setOffered(etfs['id'], False, user='alice')
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.setOffered(etfs['id'], False, user='alice')
    assert exc.value.message == 'ETFs Only is already disabled.'
    fixed = next(e for e in sleeveRepo.listAll(V) if e['category'] in sleeveRepo.fixedCategories())
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.setOffered(fixed['id'], False, user='alice')
    assert exc.value.field == 'category' and 'cannot be disabled' in exc.value.message
    other = _sleeve('SMA Only')
    sleeveRepo.deleteSleeve(other['id'], user='alice')
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.setOffered(other['id'], False, user='alice')
    assert 'Archive' in exc.value.message
    with pytest.raises(ValidationError):
        sleeveRepo.setOffered(10 ** 6, False, user='alice')


def test_every_switch_is_on_the_record_as_its_own_area(store):
    etfs = _sleeve('ETFs Only')
    sleeveRepo.setOffered(etfs['id'], False, user='alice')
    sleeveRepo.setOffered(etfs['id'], True, user='bob')
    page = repositoryFeed.activity(areas=['availability'], limit=10)
    assert [(e['action'], e['actor']) for e in page['entries']] == [('enabled', 'bob'), ('disabled', 'alice')]
    off = page['entries'][1]
    assert (off['name'], off['variant'], off['category'], off['sleeveId']) == ('ETFs Only', V, C, etfs['id'])
    assert off['changes'] == ['Not offered to PWAs'] and off['live']
    assert repositoryFeed.activity(actions=['disabled'], limit=10)['total'] == 1
    assert repositoryFeed.activity(category=C, areas=['availability'], limit=10)['total'] == 2
    assert repositoryFeed.activity(category='Public Equity', areas=['availability'], limit=10)['total'] == 0
    assert {e['area'] for e in repositoryFeed.activity(areas=['sleeves'], limit=50)['entries']} == {'sleeves'}
    assert repositoryFeed.activity(limit=5)['facets']['area']['availability'] == 2
    rows = repositoryFeed.activityRows(areas=['availability'])
    assert rows[1][:9] == (off['at'], 'Sleeves offered', 'disabled', 'alice', 'ETFs Only', V, C, '', '')
    assert rows[1][9] == etfs['id']


def test_the_route_switches_and_the_picker_route_follows(store):
    etfs = _sleeve('ETFs Only')
    out = dashboardRouter.offerRepositorySleeve(etfs['id'], {'offered': False}, caller=_caller())
    assert out['sleeve']['disabled'] and out['sleeve']['disabledBy'] == 'alice'
    served = dashboardRouter.listSleeves(C, V)
    assert 'ETFs Only' not in [s['name'] for s in served['sleeves']]
    refused = dashboardRouter.offerRepositorySleeve(etfs['id'], {'offered': 'no'}, caller=_caller())
    assert refused.status_code == 422
    again = dashboardRouter.offerRepositorySleeve(etfs['id'], {'offered': False}, caller=_caller())
    assert again.status_code == 422
    back = dashboardRouter.offerRepositorySleeve(etfs['id'], {'offered': True}, caller=_caller())
    assert not back['sleeve']['disabled']
    assert sleeveRepo.census()['byVariant'][V]['disabled'] == 0


def test_a_version_seven_store_gains_the_switch_in_place(store):
    sleeveRepo.listAll()
    path = str(store / 'sleeves.db')
    conn = sqlite3.connect(path)
    conn.execute('ALTER TABLE sleeves DROP COLUMN disabledAt')
    conn.execute('ALTER TABLE sleeves DROP COLUMN disabledBy')
    conn.execute('DROP TABLE sleeveSwitches')
    conn.execute("UPDATE meta SET value = '7' WHERE key = 'schemaVersion'")
    conn.commit()
    conn.close()
    count = len(sleeveRepo.listAll())
    assert sleeveRepo.describe()['schemaVersion'] == sleeveRepo.SCHEMA_VERSION == 12
    assert len(sleeveRepo.listAll()) == count and not any(e['disabled'] for e in sleeveRepo.listAll())
    sleeveRepo.setOffered(_sleeve('ETFs Only')['id'], False, user='alice')
    assert 'ETFs Only' not in _names()


def test_the_page_offers_the_switch_on_the_card_the_page_and_the_menu():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, '..', '..', 'generator', 'js', 'repository.js'), encoding='utf-8') as fh:
        js = fh.read()
    assert "'/scenario/repository/sleeves/' + sleeveId + '/offered'" in js
    assert "svOfferBtnHtml(s, 'sv-offbtn')" in js and "svOfferBtnHtml(entry, 'btn')" in js
    assert 'Disable: stop offering it to PWAs' in js
    assert "if (ds.svoffer !== undefined) { setOffered(parseInt(ds.svoffer, 10), ds.to === '1'); return; }" in js
    assert "['availability', 'Sleeves offered']" in js
