"""A product's values by base currency (D180, D182, D184): one product, listed
under a ticker of its own in each currency, taking a minimum of its own,
exposing a holder to a currency of its own and issued in one of its own there - or not offered there at
all. Blank in a currency is the main value. The file gives a product one row
per base currency, named in a BaseCurrency column (All for the main row), the
console a table by currency, and a scenario reads the ones for its own
currency - in the library it is served, the model its export is built from,
and so the sheet, the deck and the record kept of it. A product not offered
in a scenario's currency is flagged there, and stops the export."""
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


def _own(ticker='', minimumInvestment=None, exposureCurrency='', offered=True, issueCurrency=''):
    """One currency's values as the catalogue stores them."""
    return {'ticker': ticker, 'minimumInvestment': minimumInvestment,
            'exposureCurrency': exposureCurrency, 'issueCurrency': issueCurrency,
            'offered': offered}


NOT_OFFERED = _own(offered=False)


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


def _listed():
    """The export, and the row of a product with a ticker and a minimum."""
    rows = products.exportRows()
    head = rows[0]
    return rows, next(r for r in rows[1:] if r[head.index('Ticker')]
                      and r[head.index('MinimumInvestment')])


def _rowsFor(pid, extra):
    """The export with *pid*'s one row replaced by a row for each currency in
    *extra*: {currency: {column: value}}, the rest of each row as the export
    wrote it."""
    rows = products.exportRows()
    head = rows[0]
    at = next(n for n, r in enumerate(rows) if r[0] == pid)
    lines = []
    for currency, columns in extra.items():
        line = list(rows[at])
        line[head.index('BaseCurrency')] = currency
        for column, value in columns.items():
            line[head.index(column)] = value
        lines.append(line)
    return rows[:at] + lines + rows[at + 1:]


def test_a_file_gives_a_product_a_row_per_base_currency(store, tmp_path):
    rows, listed = _listed()
    assert rows[0] == products.FILE_COLUMNS + ['BaseCurrency']
    pid, name = listed[0], listed[1]
    path = _file(tmp_path, _rowsFor(pid, {
        'All': {},
        'GBP': {'Name': name + ' (GBP)', 'Ticker': 'EMXG LN', 'MinimumInvestment': '250,000',
                'ExposureCurrency': 'GBP'},
        'chf': {'Name': name + ' (CHF)', 'Ticker': 'EMXC SW'},
        'EUR': {'Name': name + ' (EUR)', 'Ticker': '', 'MinimumInvestment': ''}}))
    byId, order = products.readFile(path)
    first = byId[pid]
    assert first['name'] == name, 'the currency in a row\'s name is its row\'s, not the name'
    assert first['ticker'] == listed[products.FILE_COLUMNS.index('Ticker')]
    assert first['currencies'] == {
        'GBP': _own('EMXG LN', 250000.0, 'GBP'),
        'CHF': _own('EMXC SW'),
        # a row is the product whole: a blank there is none, not the main one
        'EUR': _own('—', 0.0)}
    assert all(byId[o]['currencies'] == {} for o in order if o != pid), \
        'one row, All, is the main values everywhere'
    assert len(order) == len(rows) - 1, 'one product, however many rows'


def test_a_product_with_no_all_row_is_offered_only_where_it_has_rows(store, tmp_path):
    pid = _listed()[1][0]
    path = _file(tmp_path, _rowsFor(pid, {
        'GBP': {'Ticker': 'GB1', 'ExposureCurrency': 'GBP'},
        'USD': {'Ticker': 'US1'},
        'EUR': {'Ticker': 'EU1', 'ExposureCurrency': 'EUR'}}))
    product = products.readFile(path)[0][pid]
    # the main values are its first currency's, in the vocabulary's order
    assert product['ticker'] == 'US1'
    assert product['currencies'] == {'GBP': _own('GB1', None, 'GBP'), 'CHF': NOT_OFFERED,
                                     'EUR': _own('EU1', None, 'EUR')}
    assert products.offeredIn(product) == ['USD', 'GBP', 'EUR']
    chf, gbp = products.inCurrency(product, 'CHF'), products.inCurrency(product, 'GBP')
    assert chf['offered'] is False and gbp['offered'] is True
    assert (gbp['ticker'], gbp['exposureCurrency']) == ('GB1', 'GBP')


@pytest.mark.parametrize('extra, words', [
    ({'All': {}, 'GBP': {'ProductCost': '0.9'}}, 'only the ticker, the minimum, the exposure '
                                                  'currency and the issue currency may differ'),
    ({'All': {}, 'GBP': {'Name': 'Something else (GBP)'}}, 'the GBP row gives name Something else'),
    ({'GBP': {}, 'gbp': {}}, 'already has a row for GBP'),
    ({'All': {}, '': {}}, 'already has a row for All'),
    ({'JPY': {}}, "the BaseCurrency 'JPY' is not one of"),
])
def test_rows_that_cannot_be_one_product_are_refused_naming_the_row(store, tmp_path, extra, words):
    pid = _listed()[1][0]
    with pytest.raises(products.BadCatalogue) as caught:
        products.readFile(_file(tmp_path, _rowsFor(pid, extra)))
    assert words in str(caught.value) and 'row ' in str(caught.value)


def test_the_issue_currency_is_kept_by_base_currency_and_round_trips(store, tmp_path):
    """D184: the currency a product is issued in, an optional column under
    any usual heading, differing by base currency like the exposure."""
    rows, listed = _listed()
    pid = listed[0]
    rows = _rowsFor(pid, {'All': {}, 'GBP': {'Ticker': 'GB1'}, 'EUR': {'Ticker': 'EU1'}})
    head = rows[0]
    at, base = head.index('IssueCurrency'), head.index('BaseCurrency')
    for row in rows[1:]:
        if row[0] == pid:
            row[at] = {'All': 'USD', 'GBP': 'GBP', 'EUR': 'USD'}[row[base]]
    named = [list(r) for r in rows]
    named[0][at] = 'Issue Currency'
    product = products.readFile(_file(tmp_path, named))[0][pid]
    assert product['issueCurrency'] == 'USD'
    assert product['currencies'] == {'GBP': _own('GB1', issueCurrency='GBP'), 'EUR': _own('EU1')}
    assert products.inCurrency(product, 'GBP')['issueCurrency'] == 'GBP'
    assert products.inCurrency(product, 'EUR')['issueCurrency'] == 'USD'
    # an older file, without the column, loads with none
    old = [[c for n, c in enumerate(r) if n != at] for r in products.exportRows()]
    assert all(p['issueCurrency'] == ''
               for p in products.readFile(_file(tmp_path, old, 'old.csv'))[0].values())
    # put in force, it is stored, named in the preview and exported back
    content = _upload(_file(tmp_path, named, 'p.csv'))
    preview = products.importPreview('p.csv', content)
    words = next(c['changeWords'] for c in preview['changed'] if c['productId'] == pid)
    assert 'Issue currency — → USD' in words and 'Issue currency in GBP — → GBP' in words
    products.importFile('p.csv', content, user='alice', base=preview['revision'])
    assert products.get(pid)['issueCurrency'] == 'USD'
    back = products.exportRows()
    mine = {r[back[0].index('BaseCurrency')]: r[back[0].index('IssueCurrency')]
            for r in back[1:] if r[0] == pid}
    assert mine == {'All': 'USD', 'GBP': 'GBP', 'EUR': 'USD'}


def test_a_catalogue_from_before_the_issue_currency_gains_it_blank(store):
    pid, _, _ = _held()
    products.all()
    conn = sqlite3.connect(str(store / 'sleeves.db'))
    conn.execute('ALTER TABLE products DROP COLUMN issueCurrency')
    conn.execute("UPDATE meta SET value = '11' WHERE key = 'schemaVersion'")
    conn.commit()
    conn.close()
    products.reload()
    assert sleeveRepo.describe()['schemaVersion'] == 12
    assert products.get(pid)['issueCurrency'] == ''
    _set(pid, {}, issueCurrency='EUR')
    assert products.get(pid)['issueCurrency'] == 'EUR'


def test_a_file_without_the_column_still_takes_each_product_once(store, tmp_path):
    rows = products.exportRows()
    head = rows[0][:-1]
    body = [r[:-1] for r in rows[1:]]
    with pytest.raises(products.BadCatalogue) as caught:
        products.readFile(_file(tmp_path, [head] + body + [body[0]]))
    assert 'once per base currency with a BaseCurrency column' in str(caught.value)


def test_a_scenario_reads_the_ticker_and_minimum_of_its_own_currency(store):
    pid, _, _ = _held()
    _set(pid, {'GBP': {'ticker': 'GBX1', 'minimumInvestment': 750000, 'exposureCurrency': 'GBP'},
               'EUR': {'minimumInvestment': 0, 'ticker': '-'}}, ticker='MAIN',
         minimumInvestment='1000000', exposureCurrency='USD')
    served = products.get(pid)
    gbp, eur, usd = (products.inCurrency(served, c) for c in ('GBP', 'EUR', 'USD'))
    assert (gbp['ticker'], gbp['minimumInvestment'], gbp['exposureCurrency']) == ('GBX1', 750000.0, 'GBP')
    assert (eur['ticker'], eur['minimumInvestment']) == ('—', 0.0), 'its own of none, both'
    assert (usd['ticker'], usd['minimumInvestment'], usd['exposureCurrency']) == ('MAIN', 1000000.0, 'USD')
    assert products.inCurrency(served, None)['ticker'] == 'MAIN'
    assert all(products.inCurrency(served, c)['offered'] for c in ('GBP', 'EUR', 'USD', None))
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
    with pytest.raises(ValidationError) as caught:
        _set(pid, {c: {'offered': False} for c in ('USD', 'GBP', 'CHF', 'EUR')})
    assert caught.value.field == 'offeredUSD' and 'at least one' in caught.value.message
    with pytest.raises(ValidationError) as caught:
        _set(pid, {'GBP': {'offered': 'no'}})
    assert caught.value.field == 'offeredGBP'


def test_the_history_and_an_import_preview_name_the_currency(store, tmp_path):
    pid, _, _ = _held()
    done = _set(pid, {'GBP': {'ticker': 'GBX1', 'minimumInvestment': 750000},
                      'CHF': {'offered': False}})
    assert done['currencies'] == {'GBP': _own('GBX1', 750000.0), 'CHF': NOT_OFFERED}
    words = products.history(pid)[0]['changeWords']
    assert 'Ticker in GBP — → GBX1' in words
    assert 'Minimum investment in GBP — → 750,000' in words
    assert 'Offered in CHF yes → no' in words
    rows = products.exportRows()
    mine = [r for r in rows if r[0] == pid]
    base = rows[0].index('BaseCurrency')
    assert [r[base] for r in mine] == ['USD', 'GBP', 'EUR'], 'not offered everywhere: no All row'
    line = mine[1]
    assert line[rows[0].index('Ticker')] == 'GBX1'
    assert line[rows[0].index('MinimumInvestment')] == '750000'
    line[rows[0].index('Ticker')] = 'GBX2'
    line[rows[0].index('ExposureCurrency')] = 'GBP'
    preview = products.importPreview('products.csv', _upload(_file(tmp_path, rows)))
    assert [c['changeWords'] for c in preview['changed']] == [
        ['Ticker in GBP GBX1 → GBX2', 'Exposure currency in GBP — → GBP']]


def test_an_export_loads_back_unchanged(store, tmp_path):
    pid, _, _ = _held()
    _set(pid, {'USD': {'ticker': 'U1'}, 'CHF': {'minimumInvestment': 125000, 'ticker': '—'},
               'GBP': {'exposureCurrency': 'GBP', 'minimumInvestment': 0}})
    other = next(p['productId'] for p in products.entries() if p['productId'] != pid)
    _set(other, {'GBP': {'ticker': 'G2'}, 'EUR': {'offered': False}, 'CHF': {'offered': False}})
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
    assert sleeveRepo.describe()['schemaVersion'] == 12
    assert products.get(pid)['currencies'] == {}
    _set(pid, {'GBP': {'ticker': 'GBX1'}})
    assert products.get(pid)['currencies'] == {'GBP': _own('GBX1')}


def test_values_stored_before_the_exposure_and_offered_read_as_they_did(store):
    """A product's values by currency stored before D182, a ticker and a
    minimum, read with the fields since added at their defaults."""
    pid, _, _ = _held()
    _set(pid, {'GBP': {'ticker': 'GBX1'}})
    conn = sqlite3.connect(str(store / 'sleeves.db'))
    conn.execute('UPDATE products SET currencies = ? WHERE productId = ?',
                 (json.dumps({'GBP': {'ticker': 'GBX1', 'minimumInvestment': 5.0}}), pid))
    conn.commit()
    conn.close()
    products.reload()
    assert products.get(pid)['currencies'] == {'GBP': _own('GBX1', 5.0)}
    assert products.inCurrency(products.get(pid), 'GBP')['offered'] is True


def test_a_product_not_offered_everywhere_takes_its_main_values_from_its_first(store):
    pid, _, _ = _held()
    done = _set(pid, {'USD': {'offered': False}, 'GBP': {'ticker': 'GB1', 'exposureCurrency': 'GBP'},
                      'EUR': {'ticker': 'MAIN'}}, ticker='MAIN')
    assert done['ticker'] == 'GB1' and done['exposureCurrency'] == 'GBP'
    assert done['currencies'] == {'USD': NOT_OFFERED, 'EUR': _own('MAIN')}, \
        'GBP is the main one now; EUR keeps the ticker it had'
    assert products.inCurrency(products.get(pid), 'CHF')['ticker'] == 'GB1'


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
    fields['currencies'] = {'EUR': {'ticker': 'E1', 'minimumInvestment': 50000}, 'CHF': {'offered': False}}
    body = dashboardRouter.updateRepositoryProduct(
        {'productId': pid, 'product': fields, 'base': entry['revision']}, caller=alice)
    assert body['product']['currencies'] == {'CHF': NOT_OFFERED, 'EUR': _own('E1', 50000.0)}
    fields['currencies'] = {'EUR': {'minimumInvestment': 'x'}}
    bad = dashboardRouter.updateRepositoryProduct(
        {'productId': pid, 'product': fields, 'base': body['product']['revision']}, caller=alice)
    assert bad.status_code == 422 and json.loads(bad.body)['field'] == 'minimumInvestmentEUR'


def _modelIn(port, currency, category, name, pid=None):
    basis = BasisInput(currency=currency, hedging='Hedged')
    result = port.resolve_portfolio(basis, PortfolioKey(currency, 'Moderate', 'Full', False))
    chosen = {}
    for c in result['categories']:
        if c['name'] in rules.AUTO_SLEEVE_CATEGORIES:
            continue
        under = rules.sleeveCategory(c['name'])
        library = sleeves.listSleeves(under, MA)
        chosen.setdefault(under, name if under == category else library[0]['name'])
    return result, chosen, buildImplementationRows(
        result, chosen, rules.AUTO_SLEEVE_CATEGORIES, 5e9, MA, False, None, None, 5e9, False, currency)


def test_a_product_not_offered_in_the_currency_is_flagged_and_stops_the_export(store, tmp_path,
                                                                                monkeypatch):
    from cyrus_pmg.pmgService.scenario import scenarioStore
    from cyrus_pmg.pmgService.scenario.types import MandateInput
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path / 'store'))
    port = FixturesScenarioPort()
    pid, category, name = _held()
    _set(pid, {'CHF': {'offered': False}})

    def line(m):
        return next(i for g in m['groups'] for i in g['items'] if i['productId'] == pid)
    _, _, usd = _modelIn(port, 'USD', category, name)
    result, chosen, chf = _modelIn(port, 'CHF', category, name)
    assert line(usd)['offered'] and not usd['unavailable']
    assert line(chf)['offered'] is False
    assert [u['productId'] for u in chf['unavailable']] == [pid]
    # the library a CHF scenario is served says so of the product
    body = dashboardRouter.listSleeves(category, MA, currency='CHF')
    sleeve = next(s for s in body['sleeves'] if s['name'] == name)
    assert next(p for p in sleeve['products'] if p['productId'] == pid)['offered'] is False

    basis = BasisInput(currency='CHF', hedging='Hedged')
    state = scenarioStore.createScenario(
        MandateInput(topAccountSize=5e9, mandateSize=5e9, primaryPwa='A. Castellanos — Madrid'),
        basis, createdBy='alice')
    key = PortfolioKey('CHF', 'Moderate', 'Full', False)
    scenarioStore.updateScenario(state['id'], variant=MA)
    scenarioStore.recordColumn(state['id'], key, 'base')
    scenarioStore.updateScenario(state['id'], sleeves=chosen)
    refused = dashboardRouter.exportScenario(state['id'], caller=_caller('alice'))
    assert getattr(refused, 'status_code', 200) == 422
    error = json.loads(refused.body.decode('utf-8'))
    assert error['field'] == 'offered'
    assert error['error'].startswith('Not offered in CHF: ') and category in error['error']
