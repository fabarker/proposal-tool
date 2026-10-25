"""D201: the order of the rows within a category.

The implementation table, the workbook and the deck list a category's rows
in the order of the attached sleeve's products. The admin sets that order
sleeve by sleeve in the sleeve editor - up, down, or dragged - and a new
order alone is a change: a new version, with *Order now: ...* in its
history.
"""

import pytest

from cyrus_pmg.pmgService.scenario import products, rules, sleeveRepo
from cyrus_pmg.pmgService.scenario.sleeves import listSleeves
from cyrus_pmg.pmgService.scenario.types import PortfolioKey
from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows

VARIANT = 'PMG Multi-Asset Portfolio'
EQ = 'Public Equity'


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path / 'store'))
    products.reload()
    sleeveRepo.describe()
    yield tmp_path
    products.reload()


def _model():
    from test_initial_allocation import _book, _sleeves
    _, result = _book('USD', 'USD|Moderate|Full|0')
    chosen = _sleeves(result)
    model = buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES, 50e6, VARIANT,
                                    False, None, None, 50e6, False, 'USD')
    return model, chosen


def _rows(model, category):
    return [i['productId'] for g in model['groups'] if g['category'] == category for i in g['items']]


def test_a_sleeves_new_order_is_a_version_and_the_table_follows_it(store):
    model, chosen = _model()
    key = PortfolioKey.fromStr('USD|Moderate|Full|0')
    sleeve = next(s for s in listSleeves(EQ, VARIANT, key) if s['name'] == chosen[EQ])
    live = sleeveRepo.getSleeve(sleeve['id'])
    held = [{'productId': p['productId'], 'weight': p['weight']} for p in live['products']]
    assert len(held) > 2 and _rows(model, EQ) == [p['productId'] for p in held]
    moved = list(reversed(held))
    before = len(sleeveRepo.history(sleeve['id']))
    sleeveRepo.updateSleeve(sleeve['id'], live['name'], moved, user='alice')
    history = sleeveRepo.history(sleeve['id'])
    assert len(history) == before + 1
    assert history[0]['changes'] == ['Order now: ' + ', '.join(
        products.get(p['productId'])['name'] for p in moved)]
    again, _ = _model()
    assert _rows(again, EQ) == [p['productId'] for p in moved]
    # the weights went with their products
    weights = {i['productId']: i['exactPct'] for g in again['groups'] if g['category'] == EQ for i in g['items']}
    was = {i['productId']: i['exactPct'] for g in model['groups'] if g['category'] == EQ for i in g['items']}
    assert weights == pytest.approx(was)


def test_the_same_order_saved_again_says_nothing_of_the_order(store):
    _, chosen = _model()
    key = PortfolioKey.fromStr('USD|Moderate|Full|0')
    sleeve = next(s for s in listSleeves(EQ, VARIANT, key) if s['name'] == chosen[EQ])
    live = sleeveRepo.getSleeve(sleeve['id'])
    held = [{'productId': p['productId'], 'weight': p['weight']} for p in live['products']]
    before = len(sleeveRepo.history(sleeve['id']))
    sleeveRepo.updateSleeve(sleeve['id'], live['name'], held, user='alice')
    top = sleeveRepo.history(sleeve['id'])[0]
    assert not any(c.startswith('Order now') for c in top['changes'])
    assert len(sleeveRepo.history(sleeve['id'])) == before + 1 and top['changes'] == []
