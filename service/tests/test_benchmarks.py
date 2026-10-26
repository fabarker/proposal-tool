"""D204, D207: each listing of a product names its benchmark - a fund's share
class, an SMA's mandate code alike.

The account opening request reads the benchmark of the listing a proposal
held. It is kept on the listing, edited on the product's card and loaded from
the product file, which may leave the column out and so leave the benchmarks
the catalogue holds as they are.
"""

import pytest

from cyrus_pmg.pmgService.scenario import products, sleeveRepo
from cyrus_pmg.pmgService.scenario.types import ValidationError
from test_product_listings import _file, _held, _list, _upload


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    products.reload()
    yield tmp_path
    products.reload()


def test_each_listing_names_its_benchmark(store):
    pid, _, _ = _held()
    saved = _list(pid, {'benchmark': '  MSCI  World '},
                  {'ticker': 'GBX', 'issueCurrency': 'GBP', 'hedging': 'Hedged',
                   'benchmark': 'MSCI World (GBP hedged)'})
    assert saved['benchmark'] == 'MSCI World', 'spacing tidied, and the first listing\'s at the top'
    assert [l['benchmark'] for l in saved['listings']] == ['MSCI World', 'MSCI World (GBP hedged)']
    assert products.asHeld(products.get(pid))['benchmark'] == 'MSCI World', 'the first listing\'s (D214)'
    assert 'MSCI World (GBP hedged)' in products.vocabulary()['benchmark']
    with pytest.raises(ValidationError) as caught:
        _list(pid, {}, {'ticker': 'GBX', 'issueCurrency': 'GBP', 'benchmark': 'x' * 201})
    assert caught.value.field == 'listing1benchmark'


def test_an_smas_mandate_code_names_its_own(store):
    sma = next(p for p in products.all() if products.isSma(p) and p['ownCategory'] != products.EQUITY_CATEGORY)
    saved = _list(sma['productId'], {'code': 'MUNI-USD', 'issueCurrency': 'USD',
                                     'benchmark': 'Bloomberg Municipal 1-10 Year'},
                  {'code': 'MUNI-GBP', 'issueCurrency': 'GBP', 'benchmark': ''})
    assert [(l['code'], l['benchmark']) for l in saved['listings']] == [
        ('MUNI-USD', 'Bloomberg Municipal 1-10 Year'), ('MUNI-GBP', '')]
    _list(sma['productId'], {'code': 'MUNI-USD', 'issueCurrency': 'USD', 'benchmark': 'Bloomberg Municipal Bond'},
          {'code': 'MUNI-GBP', 'issueCurrency': 'GBP', 'benchmark': ''})
    assert products.history(sma['productId'])[0]['changeWords'] == [
        'Benchmark of MUNI-USD Bloomberg Municipal 1-10 Year → Bloomberg Municipal Bond']


def test_the_product_file_carries_the_benchmark_and_an_export_loads_back(store, tmp_path):
    head = products.exportRows()[0]
    assert head.index('Benchmark') == head.index('MinimumInvestment') + 1
    pid, _, _ = _held()
    rows = products.exportRows()
    at = rows[0].index('Benchmark')
    for r in rows:
        if r[0] == pid:
            r[at] = 'FTSE All-World'
    path = _file(tmp_path, rows)
    preview = products.importPreview('products.csv', _upload(path))
    [changed] = [c for c in preview['changed'] if c['productId'] == pid]
    assert changed['changeWords'] == ['Benchmark — → FTSE All-World']
    products.importFile('products.csv', _upload(path), user='bob', base=preview['revision'])
    assert products.entry(pid)['benchmark'] == 'FTSE All-World'
    again = products.importPreview('products.csv', _upload(_file(tmp_path, products.exportRows(), 'back.csv')))
    assert not again['changed'] and not again['added'], 'an export loads back with nothing to do'


def test_a_product_file_without_the_column_leaves_the_benchmarks_as_they_are(store, tmp_path):
    pid, _, _ = _held()
    name = products.get(pid)['name']
    _list(pid, {'benchmark': 'FTSE All-World'},
          {'ticker': 'GBX', 'issueCurrency': 'GBP', 'benchmark': 'FTSE All-World (GBP)'})
    rows = products.exportRows()
    at = rows[0].index('Benchmark')
    old = [r[:at] + r[at + 1:] for r in rows]          # a file from before there were benchmarks
    for r in old:
        if r[0] == pid:
            r[1] = name + ' Renamed'
    preview = products.importPreview('products.csv', _upload(_file(tmp_path, old)))
    [changed] = [c for c in preview['changed'] if c['productId'] == pid]
    assert [c['field'] for c in changed['changes']] == ['name'], 'the benchmarks are not touched'
    products.importFile('products.csv', _upload(_file(tmp_path, old)), user='bob', base=preview['revision'])
    now = products.entry(pid)
    assert now['name'] == name + ' Renamed'
    assert [l['benchmark'] for l in now['listings']] == ['FTSE All-World', 'FTSE All-World (GBP)']
    # with the column, a blank is none
    for r in rows:
        if r[0] == pid:
            r[1] = name + ' Renamed'
            r[at] = ''
    path = _file(tmp_path, rows, 'with.csv')
    preview = products.importPreview('products.csv', _upload(path))
    products.importFile('products.csv', _upload(path), user='bob', base=preview['revision'])
    assert [l['benchmark'] for l in products.entry(pid)['listings']] == ['', '']


def test_a_store_from_before_the_benchmark_gains_the_column_blank(store):
    pid, _, _ = _held()
    before = products.entry(pid)
    conn = sleeveRepo.connection()
    conn.execute('ALTER TABLE products DROP COLUMN benchmark')
    conn.execute("UPDATE meta SET value = '15' WHERE key = 'schemaVersion'")
    conn.commit()
    conn.close()
    products.reload()
    assert sleeveRepo.describe()['schemaVersion'] == 17
    after = products.entry(pid)
    assert after['benchmark'] == '' and after['name'] == before['name']
