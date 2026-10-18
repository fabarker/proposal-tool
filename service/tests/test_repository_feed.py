"""The repository's record as one feed (D167): the sleeves, the products,
the fee card, the overlay rules and the funding split, newest first, filtered
and paged together."""
import copy

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import (feeCard, fundingSplit, overlayRules, products,
                                           repositoryFeed, sleeveRepo)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    products.reload()
    feeCard._forget()
    yield tmp_path
    products.reload()
    feeCard._forget()


def _caller(kerberos='alice'):
    from cyrus_pmg.pmgService.core import accessControl
    return accessControl.UserData(kerberos, accessControl._rolesFor(kerberos))


def _changes(store):
    """One change in every area that takes one by hand."""
    held = {row['productId'] for e in sleeveRepo.listAll() for row in e['products']}
    pid = next(p['productId'] for p in products.all() if p['productId'] not in held)
    entry = products.entry(pid)
    products.update(pid, dict(entry, liquidity='Weekly'), user='alice', note='Gated now')
    card = copy.deepcopy(feeCard.describe()['card'])
    card['rates']['RDR']['Passive']['T1']['PMG Ceiling'] += 0.01
    feeCard.save(card, 'Pricing round', user='bob', base=1)
    rules = overlayRules.current()['rules']
    overlayRules.save('*', [dict(rules[0], size=rules[0]['size'] - 1)] + rules[1:], 'Smaller tilt', 'carol')
    split = fundingSplit.current()['destinations']
    fundingSplit.save('*', list(reversed(split)), 'Reordered', user='dave')
    sleeve = sleeveRepo.listAll()[0]
    sleeveRepo.updateSleeve(sleeve['id'], sleeve['name'], [
        {'productId': r['productId'], 'weight': r['weight']} for r in sleeve['products']],
        note='touched', user='erin')
    return pid


def test_a_fresh_store_shows_every_area_loaded_and_a_file_as_one_entry(store):
    page = repositoryFeed.activity(limit=10)
    assert set(page['facets']['area']) == {'sleeves', 'products', 'fees', 'overlays'}
    assert page['facets']['area']['products'] == 1, 'the 73 products are one load'
    load = next(e for e in page['entries'] if e['area'] == 'products')
    assert load['action'] == 'seeded' and load['name'] == 'products.csv' and load['batch'] == 73
    assert load['changes'] == ['73 products loaded']
    fee = next(e for e in page['entries'] if e['area'] == 'fees')
    assert fee['action'] == 'seeded' and 'feeRates.csv' in fee['note'] and fee['current']
    assert [a['id'] for a in page['areas']] == repositoryFeed.AREAS


def test_every_area_reads_newest_first_with_who_and_why(store):
    pid = _changes(store)
    page = repositoryFeed.activity(actions=['updated'], limit=5)
    # newest first; changes made in the same second keep a fixed order
    assert {e['area'] for e in page['entries']} == {'sleeves', 'funding', 'overlays', 'fees', 'products'}
    stamps = [e['at'] for e in repositoryFeed.activity(limit=200)['entries']]
    assert stamps == sorted(stamps, reverse=True)
    byArea = {e['area']: e for e in page['entries']}
    assert byArea['products']['productId'] == pid and byArea['products']['actor'] == 'alice'
    assert byArea['products']['note'] == 'Gated now'
    assert any(c.startswith('Liquidity ') for c in byArea['products']['changes'])
    assert byArea['fees']['actor'] == 'bob' and byArea['fees']['changes'][0].startswith('RDR · Passive')
    assert byArea['overlays']['actor'] == 'carol' and byArea['overlays']['note'] == 'Smaller tilt'
    assert byArea['funding']['actor'] == 'dave' and byArea['funding']['changes']
    assert byArea['sleeves']['actor'] == 'erin'


def test_the_filters_narrow_every_area_and_count_what_each_value_would_show(store):
    _changes(store)
    only = repositoryFeed.activity(areas=['fees', 'products'], limit=50)
    assert {e['area'] for e in only['entries']} == {'fees', 'products'}
    assert only['facets']['area']['sleeves'] > 0, 'the other areas still count'
    who = repositoryFeed.activity(actor='bob', limit=50)
    assert [e['area'] for e in who['entries']] == ['fees']
    assert who['facets']['actor']['alice'] == 1 and who['facets']['actor']['bob'] == 1
    found = repositoryFeed.activity(query='gated', limit=50)
    assert [e['area'] for e in found['entries']] == ['products']
    # a type names its sleeves and its own lists; products and the card have none
    variant = sleeveRepo.VARIANTS[0]
    typed = repositoryFeed.activity(variant=variant, limit=200)
    assert {e['area'] for e in typed['entries']} == {'sleeves'}
    updated = repositoryFeed.activity(actions=['updated'], limit=50)
    assert {e['area'] for e in updated['entries']} == {'sleeves', 'products', 'fees', 'overlays',
                                                       'funding'}


def test_paging_across_areas_shows_every_entry_once(store):
    _changes(store)
    whole = repositoryFeed.activity(limit=500)
    seen, before = [], None
    while True:
        page = repositoryFeed.activity(limit=7, before=before)
        seen += [(e['area'], e['feedId']) for e in page['entries']]
        if not page['next']:
            break
        before = page['next']
    assert seen == [(e['area'], e['feedId']) for e in whole['entries']]
    assert len(seen) == len(set(seen)) == whole['total']
    # a cursor from before the feed had areas reads as a sleeve cursor
    sleeveEntry = next(e for e in whole['entries'] if e['area'] == 'sleeves')
    legacy = repositoryFeed.activity(limit=3, before='{}|{}'.format(sleeveEntry['at'], sleeveEntry['feedId']))
    assert legacy['entries']


def test_the_endpoint_and_the_table_carry_the_areas(store, monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    _changes(store)
    alice = _caller('alice')
    body = dashboardRouter.getRepositoryActivity(areas='fees,overlays', limit=10, caller=alice)
    assert {e['area'] for e in body['entries']} == {'fees', 'overlays'}
    table = dashboardRouter.exportRepositoryActivity(areas='products', caller=alice).body.decode('utf-8')
    lines = table.strip().splitlines()
    assert lines[0] == ','.join(repositoryFeed.ACTIVITY_COLUMNS)
    assert all(',Products,' in l for l in lines[1:]) and 'Gated now' in table
