"""A product's ticker and minimum by base currency (D180): one product, listed
under a ticker of its own in each currency and taking a minimum of its own
there. Blank in a currency is the main value. The file carries them as
Ticker USD ... MinimumInvestment EUR, the console as a table by currency, and
a scenario reads the ones for its own currency - in the library it is
served, the model its export is built from, and so the sheet, the deck and
the record kept of it."""
import base64
import csv
import json
import sqlite3

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import products, rules, sleeveRepo, sleeves
from cyrus_pmg.pmgService.scenario.fixturesAdapter import FixturesScenarioPort
from cyrus_pmg.pmgService.scenario.types import BasisInput, PortfolioKey, ValidationError
from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows

MA = sleeves.VARIANTS[0]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    products.reload()
    yield tmp_path
    products.reload()


def _caller(kerberos='alice'):
    from cyrus_pmg.pmgService.core import accessControl
    return accessControl.UserData(kerberos, accessControl._rolesFor(kerberos))


def _held():
    """A product an ordinary Multi-Asset sleeve holds - chosen, not
    attached by an overlay, and offered for every portfolio - with the
    sleeve's category and name."""
    for entry in sleeveRepo.listAll(MA):
        if (entry['products'] and not entry.get('problems') and not entry.get('label')
                and entry['category'] not in rules.AUTO_SLEEVE_CATEGORIES
                and entry['category'] == rules.sleeveCategory(entry['category'])):
            return entry['products'][0]['productId'], entry['category'], entry['name']
    raise AssertionError('no sleeve holds a product')


def _set(pid, currencies, **over):
    entry = products.entry(pid)
    fields = {k: entry[k] for k in products.KEYS}
    fields.update(over, currencies=currencies)
    return products.update(pid, fields, user='alice', base=entry['revision'])


def _file(tmp_path, rows, name='products.csv'):
    path = tmp_path / name
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        csv.writer(fh).writerows(rows)
    return str(path)


def _upload(path):
    with open(path, 'rb') as fh:
        return base64.b64encode(fh.read()).decode('ascii')


def test_a_file_carries_a_ticker_and_a_minimum_per_currency_under_any_heading(store, tmp_path):
    rows = products.exportRows()
    head = rows[0][:len(products.FILE_COLUMNS)] + ['Ticker GBP', 'CHF Ticker', 'Minimum (GBP)',
                                                     'MinimumInvestment EUR']
    body = [r[:len(products.FILE_COLUMNS)] + ['', '', '', ''] for r in rows[1:]]
    body[0][-4:] = ['EMXG LN', 'EMXC SW', '250,000', '0']
    byId, order = products.readFile(_file(tmp_path, [head] + body))
    first = byId[order[0]]
    assert first['currencies'] == {
        'GBP': {'ticker': 'EMXG LN', 'minimumInvestment': 250000.0},
        'CHF': {'ticker': 'EMXC SW', 'minimumInvestment': None},
        'EUR': {'ticker': '', 'minimumInvestment': 0.0}}
    assert byId[order[1]]['currencies'] == {}, 'a blank row is the main values everywhere'


def test_a_scenario_reads_the_ticker_and_minimum_of_its_own_currency(store):
    pid, _, _ = _held()
    _set(pid, {'GBP': {'ticker': 'GBX1', 'minimumInvestment': 750000},
               'EUR': {'minimumInvestment': 0}}, ticker='MAIN', minimumInvestment='1000000')
    served = products.get(pid)
    gbp, eur, usd = (products.inCurrency(served, c) for c in ('GBP', 'EUR', 'USD'))
    assert (gbp['ticker'], gbp['minimumInvestment']) == ('GBX1', 750000.0)
    assert (eur['ticker'], eur['minimumInvestment']) == ('MAIN', 0.0), 'its own minimum of none'
    assert (usd['ticker'], usd['minimumInvestment']) == ('MAIN', 1000000.0)
    assert products.inCurrency(served, None)['ticker'] == 'MAIN'
    assert served['ticker'] == 'MAIN', 'reading in a currency changes nothing it was given'


def test_a_wrong_value_in_a_currency_is_refused_naming_that_box(store):
    pid, _, _ = _held()
    with pytest.raises(ValidationError) as caught:
        _set(pid, {'GBP': {'minimumInvestment': 'lots'}})
    assert caught.value.field == 'minimumInvestmentGBP'
    assert caught.value.message.startswith('Minimum investment in GBP')
    with pytest.raises(ValidationError) as caught:
        _set(pid, {'CHF': {'ticker': 'X' * 30}})
    assert caught.value.field == 'tickerCHF' and 'ticker in CHF' in caught.value.message
    with pytest.raises(ValidationError) as caught:
        _set(pid, {'JPY': {'ticker': 'J'}})
    assert caught.value.field == 'currencies' and 'JPY' in caught.value.message
    with pytest.raises(ValidationError) as caught:
        _set(pid, {'GBP': {'minimumInvestment': -5}})
    assert caught.value.field == 'minimumInvestmentGBP'


def test_the_history_and_an_import_preview_name_the_currency(store, tmp_path):
    pid, _, _ = _held()
    done = _set(pid, {'GBP': {'ticker': 'GBX1', 'minimumInvestment': 750000}})
    assert done['currencies'] == {'GBP': {'ticker': 'GBX1', 'minimumInvestment': 750000.0}}
    words = products.history(pid)[0]['changeWords']
    assert 'Ticker in GBP — → GBX1' in words
    assert 'Minimum investment in GBP — → 750,000' in words
    rows = products.exportRows()
    at = rows[0].index('Ticker GBP')
    line = next(r for r in rows if r[0] == pid)
    assert line[at] == 'GBX1' and line[rows[0].index('MinimumInvestment GBP')] == '750000'
    line[at] = 'GBX2'
    preview = products.importPreview('products.csv', _upload(_file(tmp_path, rows)))
    assert [c['changeWords'] for c in preview['changed']] == [['Ticker in GBP GBX1 → GBX2']]


def test_an_export_loads_back_unchanged(store, tmp_path):
    pid, _, _ = _held()
    _set(pid, {'USD': {'ticker': 'U1'}, 'CHF': {'minimumInvestment': 125000}})
    rows = products.exportRows()
    assert rows[0] == products.EXPORT_COLUMNS
    preview = products.importPreview('products.csv', _upload(_file(tmp_path, rows)))
    assert not preview['changed'] and not preview['added'] and preview['unchanged'] == 73


def test_a_catalogue_from_before_gains_the_column_blank(store):
    pid, _, _ = _held()
    products.all()
    conn = sqlite3.connect(str(store / 'sleeves.db'))
    conn.execute('ALTER TABLE products DROP COLUMN currencies')
    conn.execute("UPDATE meta SET value = '10' WHERE key = 'schemaVersion'")
    conn.commit()
    conn.close()
    products.reload()
    assert sleeveRepo.describe()['schemaVersion'] == 11
    assert products.get(pid)['currencies'] == {}
    _set(pid, {'GBP': {'ticker': 'GBX1'}})
    assert products.get(pid)['currencies'] == {'GBP': {'ticker': 'GBX1', 'minimumInvestment': None}}


def test_the_library_a_scenario_is_served_is_in_its_currency(store):
    pid, category, name = _held()
    _set(pid, {'GBP': {'ticker': 'GBX1', 'minimumInvestment': 750000}}, ticker='MAIN')

    def ticker(currency):
        body = dashboardRouter.listSleeves(category, MA, currency=currency)
        sleeve = next(s for s in body['sleeves'] if s['name'] == name)
        return next(p['ticker'] for p in sleeve['products'] if p['productId'] == pid)
    assert ticker('GBP') == 'GBX1' and ticker('USD') == 'MAIN'


def test_the_model_an_export_is_built_from_prices_minimums_in_its_currency(store):
    """A minimum the product only has in GBP stops a GBP proposal and not a
    USD one: the model the sheet, the deck and the record are built from
    reads each product in the portfolio's currency."""
    port = FixturesScenarioPort()
    pid, category, name = _held()
    _set(pid, {'GBP': {'ticker': 'GBX1', 'minimumInvestment': 1e12}}, minimumInvestment='')

    def model(currency):
        basis = BasisInput(currency=currency, hedging='Hedged')
        result = port.resolve_portfolio(basis, PortfolioKey(currency, 'Moderate', 'Full', False))
        chosen = {}
        for c in result['categories']:
            if c['name'] in rules.AUTO_SLEEVE_CATEGORIES:
                continue
            under = rules.sleeveCategory(c['name'])
            library = sleeves.listSleeves(under, MA)
            chosen.setdefault(under, name if under == category else library[0]['name'])
        return buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES, 5e7, MA,
                                       False, None, None, 5e7, False, currency)

    def line(m):
        return next(i for g in m['groups'] for i in g['items'] if i['productId'] == pid)
    gbp, usd = model('GBP'), model('USD')
    assert line(gbp)['ticker'] == 'GBX1' and line(gbp)['belowMinimum']
    assert any(b['name'] == line(gbp)['name'] for b in gbp['breaches'])
    assert line(usd)['ticker'] != 'GBX1' and not line(usd)['belowMinimum']


def test_the_console_saves_the_table_by_currency(store):
    alice = _caller()
    pid, _, _ = _held()
    entry = products.entry(pid)
    fields = {k: entry[k] for k in products.KEYS}
    fields['currencies'] = {'EUR': {'ticker': 'E1', 'minimumInvestment': 50000}}
    body = dashboardRouter.updateRepositoryProduct(
        {'productId': pid, 'product': fields, 'base': entry['revision']}, caller=alice)
    assert body['product']['currencies'] == {'EUR': {'ticker': 'E1', 'minimumInvestment': 50000.0}}
    fields['currencies'] = {'EUR': {'minimumInvestment': 'x'}}
    bad = dashboardRouter.updateRepositoryProduct(
        {'productId': pid, 'product': fields, 'base': body['product']['revision']}, caller=alice)
    assert bad.status_code == 422 and json.loads(bad.body)['field'] == 'minimumInvestmentEUR'
