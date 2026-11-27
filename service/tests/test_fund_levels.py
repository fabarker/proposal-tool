"""A fund in three levels (D218): the product, its share classes and their
listings. A listing as stored carries the three together, the rows of one
ISIN are one share class and must agree on its values, a class with no ISIN
is the rows that agree on all of them, and within a class a listing is its
exchange and issue currency. The file has the levels' 29 columns (D220:
Strategy, UCITSCompliant, CUSIP and SEDOL, no Ticker), still
reads the layouts before it, and a store from before the levels is moved
on its first open."""
import base64
import csv
import json
import os
import shutil
import sqlite3
import subprocess

import pytest

from cyrus_pmg.pmgService.scenario import products, proposalRegister, rules, sleeveRepo, sleeves
from cyrus_pmg.pmgService.scenario.fixturesAdapter import FixturesScenarioPort
from cyrus_pmg.pmgService.scenario.types import BasisInput, PortfolioKey, ValidationError
from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows, writeImplementationSheet

MA = sleeves.VARIANTS[0]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path / 'store'))
    products.reload()
    yield tmp_path
    products.reload()


def _held():
    """A fund an ordinary Multi-Asset sleeve holds."""
    for entry in sleeveRepo.listAll(MA):
        if (entry['products'] and not entry.get('problems') and not entry.get('label')
                and entry['category'] not in rules.AUTO_SLEEVE_CATEGORIES
                and entry['category'] == rules.sleeveCategory(entry['category'])):
            for row in entry['products']:
                if not products.isSma(products.get(row['productId'])):
                    return row['productId']
    raise AssertionError('no sleeve holds a fund')


def _fund(vehicle=None):
    """A fund of the catalogue, of *vehicle* when given."""
    return next(p['productId'] for p in products.all()
                if not products.isSma(p) and (vehicle is None or p['vehicle'] == vehicle))


def _main(pid):
    entry = products.entry(pid)
    return {k: entry['listings'][0][k] for k in products.LISTING_FIELDS}


def _list(pid, *listings, **over):
    """Save *pid* with *listings*, each its main listing's values but those
    given, and the product's values but *over*."""
    entry = products.entry(pid)
    fields = {k: entry[k] for k in products.PRODUCT_FIELDS}
    fields['category'] = entry['category']
    fields.update(over)
    main = _main(pid)
    fields['listings'] = [dict(main, **l) for l in listings]
    return products.update(pid, fields, user='alice', base=entry['revision'])


def _file(tmp_path, rows, name='products.csv'):
    path = tmp_path / name
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        csv.writer(fh).writerows(rows)
    return str(path)


def _upload(path):
    with open(path, 'rb') as fh:
        return base64.b64encode(fh.read()).decode('ascii')


def _rowsFor(pid, listings):
    """The export with *pid*'s rows replaced by one per entry of *listings*:
    [{column: value}], the rest of each row as the export wrote it."""
    rows = products.exportRows()
    head = rows[0]
    at = next(n for n, r in enumerate(rows) if r[0] == pid)
    end = at
    while end < len(rows) and rows[end][0] == pid:
        end += 1
    lines = []
    for columns in listings:
        line = list(rows[at])
        for column, value in columns.items():
            line[head.index(column)] = value
        lines.append(line)
    return rows[:at] + lines + rows[end:]


ETF = [
    {'ISIN': 'IE0000000021', 'Income': 'Distributing', 'Hedging': 'Unhedged', 'ClassCurrency': 'USD',
     'ExposureCurrency': 'USD', 'Domicile': 'IE', 'UCITSCompliant': 'Yes', 'Replication': 'Physical',
     'UKReporting': 'Yes', 'TotalExpenseRatio': '0.12', 'Exchange': 'LSE', 'SEDOL': 'BPRBL01', 'IssueCurrency': 'USD'},
    {'ISIN': 'IE0000000021', 'Income': 'Distributing', 'Hedging': 'Unhedged', 'ClassCurrency': 'USD',
     'ExposureCurrency': 'USD', 'Domicile': 'IE', 'UCITSCompliant': 'Yes', 'Replication': 'Physical',
     'UKReporting': 'Yes', 'TotalExpenseRatio': '0.12', 'Exchange': 'LSE', 'SEDOL': 'BPRBP02', 'IssueCurrency': 'GBP'},
    {'ISIN': 'IE0000000021', 'Income': 'Distributing', 'Hedging': 'Unhedged', 'ClassCurrency': 'USD',
     'ExposureCurrency': 'USD', 'Domicile': 'IE', 'UCITSCompliant': 'Yes', 'Replication': 'Physical',
     'UKReporting': 'Yes', 'TotalExpenseRatio': '0.12', 'Exchange': 'Xetra', 'SEDOL': 'BPRBX03', 'IssueCurrency': 'EUR'},
    {'ISIN': 'IE0000000039', 'Income': 'Distributing', 'Hedging': 'Hedged', 'ClassCurrency': 'GBP',
     'ExposureCurrency': 'GBP', 'Domicile': 'IE', 'UCITSCompliant': 'Yes', 'Replication': 'Physical',
     'UKReporting': 'Yes', 'TotalExpenseRatio': '0.15', 'Exchange': 'LSE', 'SEDOL': 'BPRBH04', 'IssueCurrency': 'GBP'},
]


# ------------------------------------------------------------------ the file ---

def test_the_file_has_the_levels_columns_and_an_export_loads_back(store, tmp_path):
    columns = products.EXPORT_COLUMNS
    assert columns[:10] == ['ProductId', 'Strategy', 'Provider', 'Category', 'AssetClass', 'Style', 'Vehicle',
                            'Source', 'Liquidity', 'FeeGroup'], 'the product'
    assert columns[10:26] == ['Name', 'ISIN', 'ShareClass', 'Income', 'Hedging', 'ISG', 'ClassCurrency',
                              'ExposureCurrency', 'Domicile', 'UCITSCompliant', 'Replication', 'UKReporting',
                              'TotalExpenseRatio', 'Yield', 'MinimumInvestment', 'Benchmark'], \
        'the share class and its name (D225), and beside its hedging an SMA mandate\'s ISG tick (D224)'
    assert columns[26:] == ['Exchange', 'ExchangeCountry', 'RIC', 'CUSIP', 'SEDOL', 'IssueCurrency', 'MandateCode']
    assert len(columns) == 33, 'no quotation unit (D219), no ticker (D220), ISG (D224), a name, a RIC and ' \
                               'the exchange\'s country (D225)'
    rows = products.exportRows()
    assert len(rows) == 74
    path = _file(tmp_path, rows)
    preview = products.importPreview(path, _upload(path))
    assert not preview['added'] and not preview['changed'] and not preview['restored']
    assert preview['isinsFromTickers'] == 0 and preview['vehiclesMoved'] == []


def test_the_sample_file_is_in_the_levels_layout_with_the_worked_examples(store):
    here = os.path.join(os.path.dirname(__file__), '..', '..', 'productSource')
    with open(os.path.join(here, 'products-listings.csv'), newline='', encoding='utf-8') as fh:
        assert next(csv.reader(fh)) == products.EXPORT_COLUMNS
    for name in ('products-listings.csv', 'products-listings.xlsx'):
        byId, order = products.readFile(os.path.join(here, name))
        assert len(order) == 73
        fund = byId['gs-global-credit-portfolio-ucits']
        assert fund['vehicle'] == 'Mutual Fund'
        assert [(l['isin'], l['shareClass'], l['issueCurrency']) for l in fund['listings']] == [
            ('LU0000000011', 'I', 'EUR'), ('LU0000000011', 'I', 'USD'), ('LU0000000029', 'I', 'EUR'),
            ('LU0000000037', 'I', 'GBP'), ('LU0000000045', 'A', 'EUR')]
        assert fund['listings'][3]['exposureCurrency'] == 'GBP', 'the GBP-hedged class'
        etf = byId['gs-access-treasury-0-1-etf']
        assert [(l['isin'], l['exchange'], l['sedol'], l['issueCurrency']) for l in etf['listings']] == [
            ('IE0000000021', 'LSE', 'B000011', 'USD'), ('IE0000000021', 'LSE', 'B000029', 'GBP'),
            ('IE0000000021', 'Xetra', 'B000037', 'EUR'), ('IE0000000039', 'LSE', 'B000045', 'GBP')]
        assert {l['ucits'] for l in etf['listings'] + fund['listings']} == {'Yes'}
        assert not any('ticker' in l or 'regulation' in l for l in etf['listings'])


def test_a_fund_in_a_file_is_its_classes_and_their_listings_in_order(store, tmp_path):
    pid = _fund('ETF')
    path = _file(tmp_path, _rowsFor(pid, ETF))
    byId, _ = products.readFile(path)
    listed = byId[pid]['listings']
    assert [(l['isin'], l['exchange'], l['sedol'], l['issueCurrency']) for l in listed] == [
        ('IE0000000021', 'LSE', 'BPRBL01', 'USD'), ('IE0000000021', 'LSE', 'BPRBP02', 'GBP'),
        ('IE0000000021', 'Xetra', 'BPRBX03', 'EUR'), ('IE0000000039', 'LSE', 'BPRBH04', 'GBP')]
    assert not any('quotationUnit' in l for l in listed), 'no quotation unit (D219)'
    assert [l['productCost'] for l in listed] == [0.12, 0.12, 0.12, 0.15], 'each row carries its class'
    assert listed[3]['hedging'] == 100.0 and listed[3]['classCurrency'] == listed[3]['exposureCurrency'] == 'GBP'
    assert byId[pid]['assetClass'] and 'assetClass' not in listed[0], 'the asset class is the product\'s'
    preview = products.importPreview(path, _upload(path))
    products.importFile(path, _upload(path), user='alice', base=preview['revision'])
    held = products.asHeld(products.get(pid))
    assert (held['isin'], held['sedol'], held['issueCurrency'], held['listing']) == (
        'IE0000000021', 'BPRBL01', 'USD', 'IE0000000021 · USD'), 'a proposal holds the first listing of the first class'
    assert 'ticker' not in held
    words = products.history(pid)[0]['changeWords']
    assert any(w.startswith('Listing IE0000000039') for w in words), words


def test_the_rows_of_one_isin_must_agree_on_the_class(store, tmp_path):
    pid = _fund('ETF')
    rows = [dict(ETF[0]), dict(ETF[1], TotalExpenseRatio='0.15')]
    with pytest.raises(products.BadCatalogue) as caught:
        products.readFile(_file(tmp_path, _rowsFor(pid, rows)))
    said = str(caught.value)
    assert 'IE0000000021 has total expense ratio 0.12 on row' in said and 'one ISIN is one share class' in said
    assert 'listing' not in said, said
    # and the form says the same on the listing's box
    with pytest.raises(ValidationError, match='one ISIN is one share class') as refused:
        _list(pid, {'isin': 'IE0000000021', 'issueCurrency': 'USD', 'income': 'Distributing'},
              {'isin': 'IE0000000021', 'issueCurrency': 'GBP', 'income': 'Accumulating'})
    assert refused.value.field == 'listing1income'
    # UK reporting and domicile are the class's too
    with pytest.raises(ValidationError) as refused:
        _list(pid, {'isin': 'IE0000000021', 'issueCurrency': 'USD', 'ukReporting': 'Yes'},
              {'isin': 'IE0000000021', 'issueCurrency': 'GBP', 'ukReporting': 'No'})
    assert refused.value.field == 'listing1ukReporting'


def test_within_a_class_a_listing_is_its_exchange_and_issue_currency(store, tmp_path):
    pid = _fund('ETF')
    # the same class, exchange and currency twice, under two SEDOLs: one listing
    rows = [dict(ETF[0]), dict(ETF[0], SEDOL='BPRBL19')]
    with pytest.raises(products.BadCatalogue, match='same listing'):
        products.readFile(_file(tmp_path, _rowsFor(pid, rows)))
    with pytest.raises(ValidationError, match='same listing') as refused:
        _list(pid, {'isin': 'IE0000000021', 'exchange': 'LSE', 'issueCurrency': 'USD'},
              {'isin': 'IE0000000021', 'exchange': 'lse', 'issueCurrency': 'usd', 'sedol': 'BPRBL19'})
    assert refused.value.field == 'listing1exchange'
    # with no ISIN, the rows that agree on every class value are one class
    out = _list(pid, {'isin': '', 'issueCurrency': 'EUR'}, {'isin': '', 'issueCurrency': 'USD'})
    assert len(out['listings']) == 2
    # and rows that differ in the class are two classes, listed in the same place
    out = _list(pid, {'isin': '', 'issueCurrency': 'EUR', 'productCost': 0.2},
                {'isin': '', 'issueCurrency': 'EUR', 'productCost': 0.3})
    assert [l['productCost'] for l in out['listings']] == [0.2, 0.3]
    with pytest.raises(ValidationError, match='same listing'):
        _list(pid, {'isin': '', 'issueCurrency': 'EUR', 'productCost': 0.2},
              {'isin': '', 'issueCurrency': 'EUR', 'productCost': 0.2})


def test_a_mutual_fund_class_is_dealt_in_each_currency_with_no_exchange(store, tmp_path):
    pid = _fund('Mutual Fund')
    out = _list(pid, {'isin': 'LU0000000011', 'shareClass': 'I', 'income': 'Accumulating',
                      'classCurrency': 'EUR', 'issueCurrency': 'EUR', 'exposureCurrency': 'MUL', 'domicile': 'LU',
                      'ucits': 'Yes'},
                {'isin': 'LU0000000011', 'shareClass': 'I', 'income': 'Accumulating', 'classCurrency': 'EUR',
                 'issueCurrency': 'USD', 'exposureCurrency': 'MUL', 'domicile': 'LU', 'ucits': 'Yes'},
                {'isin': 'LU0000000037', 'shareClass': 'I', 'income': 'Accumulating', 'hedging': 'Hedged',
                 'classCurrency': 'GBP', 'issueCurrency': 'GBP', 'exposureCurrency': '', 'domicile': 'LU',
                 'ucits': 'Yes', 'productCost': 0.55},
                {'isin': 'LU0000000045', 'shareClass': 'A', 'income': 'Accumulating', 'classCurrency': 'EUR',
                 'issueCurrency': 'EUR', 'exposureCurrency': 'MUL', 'domicile': 'LU', 'ucits': 'Yes',
                 'productCost': 1.2})
    assert [(l['isin'], l['shareClass'], l['issueCurrency'], l['exposureCurrency']) for l in out['listings']] == [
        ('LU0000000011', 'I', 'EUR', 'MUL'), ('LU0000000011', 'I', 'USD', 'MUL'),
        ('LU0000000037', 'I', 'GBP', 'GBP'), ('LU0000000045', 'A', 'EUR', 'MUL')]
    assert out['listings'][2]['exposureCurrency'] == 'GBP', 'a hedged class left blank is its class currency'
    assert products.listingLabel(out['listings'][1], out['listings']) == 'LU0000000011 · USD'
    # a class letter is a Mutual Fund's alone
    etf = _fund('ETF')
    with pytest.raises(ValidationError, match='Only a Mutual Fund has a share class letter') as refused:
        _list(etf, {'shareClass': 'A'})
    assert refused.value.field == 'listing0shareClass'
    with pytest.raises(ValidationError, match='Keep the share class'):
        _list(pid, {'shareClass': 'A' * 21})


# --------------------------------------------------------------- the checks ---

def test_a_hedged_class_gives_its_class_currencys_exposure(store, tmp_path):
    pid = _fund('ETF')
    with pytest.raises(ValidationError, match='hedged to GBP gives GBP exposure, not USD') as refused:
        _list(pid, {'hedging': 'Hedged', 'classCurrency': 'GBP', 'exposureCurrency': 'USD'})
    assert refused.value.field == 'listing0exposureCurrency'
    out = _list(pid, {'hedging': 'Hedged', 'classCurrency': 'gbp', 'exposureCurrency': ''})
    assert (out['classCurrency'], out['exposureCurrency']) == ('GBP', 'GBP')
    out = _list(pid, {'hedging': 'Unhedged', 'classCurrency': 'GBP', 'exposureCurrency': 'USD'})
    assert out['exposureCurrency'] == 'USD', 'unhedged, the class gives what it holds'
    # in a file with its own ClassCurrency column the same is refused on the row
    rows = _rowsFor(pid, [{'Hedging': 'Hedged', 'ClassCurrency': 'GBP', 'ExposureCurrency': 'USD'}])
    with pytest.raises(products.BadCatalogue, match='hedged to GBP'):
        products.readFile(_file(tmp_path, rows))
    # a file without one gives each class its first row's issue currency, and
    # a hedged class's exposure follows it
    rows = _rowsFor(pid, [{'Hedging': 'Hedged', 'IssueCurrency': 'GBP', 'ExposureCurrency': 'USD', 'ClassCurrency': ''},
                          {'Hedging': 'Unhedged', 'IssueCurrency': 'EUR', 'ClassCurrency': ''}])
    head = rows[0]
    at = head.index('ClassCurrency')
    rows = [[v for i, v in enumerate(r) if i != at] for r in rows]
    byId, _ = products.readFile(_file(tmp_path, rows))
    first, second = byId[pid]['listings']
    assert (first['classCurrency'], first['exposureCurrency']) == ('GBP', 'GBP')
    assert second['classCurrency'] == 'EUR'


@pytest.mark.parametrize('over, field, words', [
    ({'domicile': 'LUX'}, 'domicile', 'is not a country'),
    ({'domicile': 'XX'}, 'domicile', 'is not a country'),
    ({'isin': 'IE00B4L5Y98'}, 'isin', 'not one: two letters'),
    ({'classCurrency': 'GBPX'}, 'classCurrency', 'not a currency code'),
    ({'replication': 'Mostly'}, 'replication', 'not one of Physical, Sampled or Synthetic'),
    ({'ukReporting': 'Maybe'}, 'ukReporting', 'Yes, No or blank'),
    ({'ucits': 'UCITS V'}, 'ucits', 'UCITS compliant .* is Yes, No or blank'),
    ({'sedol': 'BAEIOU1'}, 'sedol', 'SEDOL .* is not one'),
    ({'sedol': 'B0YBKJ'}, 'sedol', 'SEDOL .* is not one'),
    ({'cusip': '0378-3310'}, 'cusip', 'CUSIP .* is not one'),
    ({'cusip': 'A' * 65}, 'cusip', 'Keep the CUSIP'),
    ({'ric': 'CSPX L'}, 'ric', 'RIC .* is not one'),
    ({'exchangeCountry': 'GBR'}, 'exchangeCountry', 'exchange country .* is not a country'),
    ({'income': 'Growth'}, 'income', 'not one of Distributing, Accumulating'),
    ({'exchange': 'X' * 41}, 'exchange', 'Keep the exchange'),
])
def test_a_class_value_that_cannot_be_is_refused_on_its_box(store, over, field, words):
    pid = _fund('ETF')
    with pytest.raises(ValidationError, match=words) as refused:
        _list(pid, over)
    assert refused.value.field == 'listing0' + field


def test_values_are_read_as_people_type_them(store):
    pid = _fund('ETF')
    out = _list(pid, {'isin': 'ie0000000021', 'domicile': 'ie', 'replication': 'physical', 'ukReporting': 'y',
                      'income': 'Dist', 'classCurrency': 'usd', 'exchange': 'LSE', 'ucits': 'true',
                      'sedol': 'b0ybkj7', 'cusip': '037833100'})
    one = out['listings'][0]
    assert (one['isin'], one['domicile'], one['replication'], one['ukReporting'], one['income'],
            one['classCurrency']) == ('IE0000000021', 'Ireland', 'Physical', 'Yes', 'Distributing', 'USD')
    assert (one['ucits'], one['sedol'], one['cusip']) == ('Yes', 'B0YBKJ7', '037833100')
    assert _list(pid, {'ucits': False})['ucits'] == 'No'
    assert out['isin'] == 'IE0000000021', 'the first listing\'s values are at the top'
    assert _list(pid, {'ukReporting': False})['ukReporting'] == 'No'
    assert _list(pid, {'ukReporting': ''})['ukReporting'] == ''
    # an income word typed where the class letter goes is the income, as
    # the field was called before the levels
    out = _list(pid, {'shareClass': 'Accumulating', 'income': ''})
    assert (out['income'], out['shareClass']) == ('Accumulating', '')


def test_the_vehicles_are_three_and_older_words_are_read(store, tmp_path):
    pid = _fund('ETF')
    assert _list(pid, {}, vehicle='separate account', category='Public Equity')['vehicle'] == 'SMA'
    assert _list(pid, {}, vehicle='mutual fund')['vehicle'] == 'Mutual Fund'
    with pytest.raises(ValidationError, match='not one of ETF, Mutual Fund or SMA') as refused:
        _list(pid, {}, vehicle='Trust')
    assert refused.value.field == 'vehicle'
    # UCITS and ICAV are a fund: an ETF where the name says so, a UCITS
    # one's classes UCITS compliant (D220), an ICAV's not said
    out = _list(pid, {'ucits': ''}, vehicle='UCITS', name='Probe Treasury ETF')
    assert out['vehicle'] == 'ETF' and out['listings'][0]['ucits'] == 'Yes'
    out = _list(pid, {'ucits': ''}, vehicle='ICAV', name='Probe Credit Portfolio')
    assert out['vehicle'] == 'Mutual Fund' and out['listings'][0]['ucits'] == ''
    out = _list(pid, {'ucits': 'No'}, vehicle='UCITS', name='Probe Credit Portfolio')
    assert out['listings'][0]['ucits'] == 'No', 'a compliance given is kept'
    # the seeded catalogue has none left, and the preview says what a file's are read as
    assert {p['vehicle'] for p in products.all()} <= set(products.VEHICLES)
    _list(pid, {'ucits': ''}, vehicle='ETF', name='Probe Treasury ETF')
    rows = _rowsFor(pid, [{'Vehicle': 'UCITS', 'UCITSCompliant': ''}])
    path = _file(tmp_path, rows)
    preview = products.importPreview(path, _upload(path))
    assert preview['vehiclesMoved'] == [{'productId': pid, 'name': 'Probe Treasury ETF', 'was': 'UCITS',
                                         'now': 'ETF', 'ucits': 'Yes'}]
    assert any('Vehicle' in w or 'UCITS' in w for c in preview['changed'] for w in c['changeWords'])
    vocab = products.vocabulary()
    assert vocab['vehicle'] == ['ETF', 'Mutual Fund', 'SMA'] and vocab['income'] == ['Distributing', 'Accumulating']
    assert vocab['replication'] == ['Physical', 'Sampled', 'Synthetic'] and vocab['ukReporting'] == ['Yes', 'No']
    assert vocab['ucits'] == ['Yes', 'No'] and 'regulation' not in vocab
    assert 'Ireland' in products.vocabulary()['domicile'] or _list(pid, {'domicile': 'IE'}) and \
        'Ireland' in products.vocabulary()['domicile']


def test_an_sma_keeps_its_rows_and_refuses_the_levels_fields(store):
    sma = next(p['productId'] for p in products.all() if products.isSma(p)
               and p['ownCategory'] == products.EQUITY_CATEGORY)
    out = _list(sma, {'code': 'PMG-CG-USD-U', 'issueCurrency': 'USD', 'hedging': 'Unhedged'},
                {'code': 'PMG-CG-GBP-H', 'issueCurrency': 'GBP', 'hedging': 'Hedged', 'productCost': 0.6})
    assert [l['code'] for l in out['listings']] == ['PMG-CG-USD-U', 'PMG-CG-GBP-H']
    assert all(l['isin'] == '' and l['classCurrency'] == '' for l in out['listings'])
    for field in ('isin', 'domicile', 'exchange', 'ucits', 'sedol', 'cusip'):
        with pytest.raises(ValidationError, match='A separate account has no') as refused:
            _list(sma, {'code': 'PMG-CG-USD-U', 'issueCurrency': 'USD',
                        field: {'isin': 'IE0000000021', 'domicile': 'IE', 'exchange': 'LSE', 'ucits': 'Yes',
                                'sedol': 'B0YBKJ7', 'cusip': '037833100'}[field]})
        assert refused.value.field == 'listing0' + field
    with pytest.raises(ValidationError, match='is an SMA') as refused:
        _list(sma, {'code': 'PMG-CG-USD-U', 'issueCurrency': 'USD', 'shareClass': 'A'})
    assert refused.value.field == 'listing0shareClass'
    assert _list(sma, {'code': 'PMG-CG-USD-U', 'issueCurrency': 'USD', 'income': 'Distributing'})['income'] == 'Distributing'


def test_an_isin_is_one_products(store, tmp_path):
    pid, other = _fund('ETF'), _fund('Mutual Fund')
    _list(pid, {'isin': 'IE0000000021'})
    with pytest.raises(ValidationError, match='The ISIN IE0000000021 belongs to') as refused:
        _list(other, {'isin': 'ie0000000021'})
    assert refused.value.field == 'listing0isin'
    rows = _rowsFor(other, [{'ISIN': 'IE0000000021'}])
    with pytest.raises(products.BadCatalogue, match='the ISIN IE0000000021 is on'):
        products.readFile(_file(tmp_path, rows))
    # and so is a SEDOL or a CUSIP (D220)
    _list(pid, {'isin': 'IE0000000021', 'sedol': 'B0YBKJ7', 'cusip': '037833100'})
    for field, value in (('sedol', 'B0YBKJ7'), ('cusip', '037833100')):
        with pytest.raises(ValidationError, match='belongs to') as refused:
            _list(other, {'isin': '', field: value})
        assert refused.value.field == 'listing0' + field
    rows = _rowsFor(other, [{'ISIN': '', 'SEDOL': 'B0YBKJ7'}])
    with pytest.raises(products.BadCatalogue, match='the SEDOL B0YBKJ7 is on'):
        products.readFile(_file(tmp_path, rows))


# ----------------------------------------------------- the layouts before ---

def _oldLayout(rows):
    """The export in the layout before the levels: Ticker holding the ISIN
    where the listing has one, the income under ShareClass, and none of the
    levels' other columns."""
    head = rows[0]
    old = ['ProductId', 'Name', 'Ticker', 'AssetClass', 'Category', 'Style', 'Vehicle', 'ShareClass', 'Source',
           'Liquidity', 'ExposureCurrency', 'IssueCurrency', 'ProductCost', 'FeeGroup', 'DistributionYield',
           'MinimumInvestment', 'Benchmark', 'MandateCode', 'Hedging']
    out = [old]
    for r in rows[1:]:
        cell = dict(zip(head, r))
        # the cost and the yield under their headings before D225
        cell.update(ProductCost=cell['TotalExpenseRatio'], DistributionYield=cell['Yield'])
        line = [cell.get(c, '') for c in old]
        line[1] = cell['Strategy']
        line[2] = cell['ISIN'] or cell['SEDOL']
        line[7] = cell['Income']
        out.append(line)
    return out


def test_the_file_before_the_levels_still_imports_and_keeps_what_it_cannot_say(store, tmp_path):
    pid = _fund('Mutual Fund')
    _list(pid, {'isin': 'LU0000000011', 'shareClass': 'I', 'income': 'Accumulating',
                'classCurrency': 'EUR', 'issueCurrency': 'EUR', 'domicile': 'LU', 'ucits': 'Yes',
                'ukReporting': 'Yes'},
          {'isin': 'LU0000000011', 'shareClass': 'I', 'income': 'Accumulating', 'classCurrency': 'EUR',
           'issueCurrency': 'USD', 'domicile': 'LU', 'ucits': 'Yes', 'ukReporting': 'Yes'})
    rows = _oldLayout(products.exportRows())
    path = _file(tmp_path, rows, 'old.csv')
    preview = products.importPreview(path, _upload(path))
    assert preview['isinsFromTickers'] == 2, 'the ISINs under Ticker are read as ISINs'
    assert not preview['changed'] and not preview['added'], [c['changeWords'] for c in preview['changed']]
    byId, _ = products.readFile(path)
    one = byId[pid]['listings'][0]
    assert (one['isin'], one['sedol'], one['income'], one['classCurrency']) == ('LU0000000011', '', 'Accumulating', 'EUR')
    assert 'ticker' not in one
    assert one['shareClass'] == '' and one['domicile'] == '', 'read alone, the file says nothing of them'
    # a change the file does make is read, and the rest still kept
    for r in rows:
        if r[0] == pid:
            r[12] = '0.99'
    path = _file(tmp_path, rows, 'old2.csv')
    preview = products.importPreview(path, _upload(path))
    products.importFile(path, _upload(path), user='alice', base=preview['revision'])
    after = products.entry(pid)
    assert after['productCost'] == 0.99
    assert (after['listings'][0]['shareClass'], after['listings'][0]['domicile'], after['listings'][0]['ukReporting']) == ('I', 'Luxembourg', 'Yes')
    assert after['listings'][1]['issueCurrency'] == 'USD' and after['listings'][1]['shareClass'] == 'I'


def test_the_layout_before_listings_is_still_read(store, tmp_path):
    pid = _fund('ETF')
    name = products.get(pid)['name']
    rows = _oldLayout(products.exportRows())
    head = rows[0] + ['BaseCurrency']
    at = next(n for n, r in enumerate(rows) if r[0] == pid)
    main = rows[at] + ['All']
    gbp = list(rows[at]) + ['GBP']
    gbp[1] = name + ' (GBP)'
    gbp[11] = 'GBP'
    gbp[2] = 'IE0000000039'
    out = [head] + [r + [''] for r in rows[1:at]] + [main, gbp] + [r + [''] for r in rows[at + 1:]]
    found = set()
    byId, _ = products._load(_file(tmp_path, out), strict=False, found=found)
    assert 'BaseCurrency' in found
    listed = byId[pid]['listings']
    assert [(l['isin'], l['issueCurrency']) for l in listed] == [(products.get(pid)['isin'] or '', products.get(pid)['issueCurrency'] or ''), ('IE0000000039', 'GBP')]
    assert byId[pid]['name'] == name


# ------------------------------------------------- a store from before them ---

def _rawStore(path):
    conn = sleeveRepo.connection()
    conn.close()
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    return raw


def test_a_quotation_unit_in_a_file_or_a_stored_listing_is_not_kept(store, tmp_path):
    """D219: the quotation unit is gone. A file that still has the column
    reads without it, and a listing stored with one reads without it."""
    pid = _fund('ETF')
    rows = _rowsFor(pid, ETF)
    rows = [r + [u] for r, u in zip(rows, ['QuotationUnit'] + ['GBX'] * (len(rows) - 1))]
    byId, _ = products.readFile(_file(tmp_path, rows))
    assert [l['sedol'] for l in byId[pid]['listings']] == ['BPRBL01', 'BPRBP02', 'BPRBX03', 'BPRBH04']
    assert not any('quotationUnit' in l for p in byId.values() for l in p['listings'])
    raw = _rawStore(store / 'sleeves.db')
    stored = json.loads(raw.execute('SELECT listings FROM products WHERE productId = ?', (pid,)).fetchone()[0])
    with raw:
        raw.execute('UPDATE products SET listings = ? WHERE productId = ?',
                    (json.dumps([dict(l, quotationUnit='GBX') for l in stored]), pid))
    raw.close()
    products.reload()
    assert not any('quotationUnit' in l for l in products.entry(pid)['listings'])
    assert 'QuotationUnit' not in products.exportRows()[0]


def test_a_file_or_a_listing_with_a_ticker_and_a_regulation_reads_as_now(store, tmp_path):
    """D220: a file in the layout before (Name, Regulation, Ticker) imports -
    the name is the strategy, UCITS is UCITS compliant and AIF not, and a
    ticker is not kept but for one shaped like an ISIN - and a listing
    stored with them reads the same way."""
    pid = _fund('ETF')
    rows = _rowsFor(pid, ETF)
    head = ['Name' if c == 'Strategy' else 'Regulation' if c == 'UCITSCompliant' else 'Ticker' if c == 'SEDOL'
            else c for c in rows[0]]
    at = {c: head.index(c) for c in ('Regulation', 'Ticker')}
    body = [list(r) for r in rows[1:]]
    for r in body:
        r[at['Ticker']] = ''
        r[at['Regulation']] = ''
    mine = [r for r in body if r[0] == pid]
    for r, ticker, regulation in zip(mine, ['PRBL', 'PRBP', 'PRBE', 'PRBH'], ['UCITS', 'UCITS', 'UCITS', 'AIF']):
        r[at['Ticker']], r[at['Regulation']] = ticker, regulation
    mine[3][head.index('ISIN')] = 'IE0000000039'
    path = _file(tmp_path, [head] + body, 'before.csv')
    found = set()
    byId, _ = products._load(path, strict=False, found=found)
    listed = byId[pid]['listings']
    assert [l['ucits'] for l in listed] == ['Yes', 'Yes', 'Yes', 'No']
    assert not any(l['sedol'] or l['cusip'] for l in listed) and 'ticker' not in listed[0]
    assert byId[pid]['name'] == products.get(pid)['name'], 'Name is the strategy'
    preview = products.importPreview(path, _upload(path))
    assert preview['tickersDropped'] == 4 and preview['regulationRead'] is True
    # a stored listing with a ticker and a regulation
    raw = _rawStore(store / 'sleeves.db')
    stored = json.loads(raw.execute('SELECT listings FROM products WHERE productId = ?', (pid,)).fetchone()[0])
    old = [{k: v for k, v in l.items() if k not in ('ucits', 'cusip', 'sedol')} for l in stored]
    old[0].update(ticker='PRBL', regulation='UCITS', isin='')
    old.append(dict(old[0], ticker='IE0000000039', regulation='AIF', issueCurrency='CHF', productCost=0.3))
    with raw:
        raw.execute('UPDATE products SET listings = ? WHERE productId = ?', (json.dumps(old), pid))
    raw.close()
    products.reload()
    now = products.entry(pid)['listings']
    assert [(l['isin'], l['ucits'], l['sedol']) for l in now[-2:]] == [('', 'Yes', ''), ('IE0000000039', 'No', '')]
    assert not any('ticker' in l or 'regulation' in l for l in now)
    notes = []
    products.modernise(dict(products.get(pid), listings=old), notes)
    assert 'ticker PRBL of — not kept' in notes and any('→ the ISIN' in n for n in notes), notes


def test_a_store_from_before_the_levels_is_moved_on_its_first_open(store):
    """17 -> 18: each product rewritten as the levels read it, one revision
    by the upgrade, what moved on the revision and in the report."""
    pid = _fund('ETF')
    before = products.entry(pid)
    fundName = before['name']
    old = {'ticker': 'IE0000000021', 'code': '', 'issueCurrency': 'USD', 'hedging': 'Unhedged',
           'shareClass': 'Accumulating', 'exposureCurrency': 'USD', 'assetClass': before['assetClass'],
           'productCost': 0.12, 'distributionYield': None, 'minimumInvestment': None, 'benchmark': 'Bench'}
    hedged = dict(old, ticker='IE0000000039', issueCurrency='GBP', hedging='Hedged', productCost=0.15)
    raw = _rawStore(store / 'sleeves.db')
    with raw:
        raw.execute("UPDATE products SET listings = ?, vehicle = 'UCITS', ticker = 'IE0000000021' WHERE productId = ?",
                    (json.dumps([old, hedged]), pid))
        revision = int(raw.execute("SELECT value FROM meta WHERE key = 'catalogueRevision'").fetchone()[0])
        raw.execute('ALTER TABLE products DROP COLUMN provider')
        raw.execute("UPDATE meta SET value = '17' WHERE key = 'schemaVersion'")
    raw.close()
    products.reload()
    # the first read moves the file rather than read it as it was, so the
    # report is there from the start
    early = products.get(pid)
    assert early['isin'] == 'IE0000000021' and 'ticker' not in early
    assert early['vehicle'] == ('ETF' if 'ETF' in fundName else 'Mutual Fund') and early['ucits'] == 'Yes'
    assert any(r['productId'] == pid for r in products.describeSource()['levelsReport'])
    # the move
    conn = sleeveRepo.connection()
    try:
        assert int(conn.execute("SELECT value FROM meta WHERE key = 'schemaVersion'").fetchone()[0]) == 18
        stored = conn.execute('SELECT listings, provider, vehicle FROM products WHERE productId = ?', (pid,)).fetchone()
        listed = json.loads(stored['listings'])
        assert (listed[0]['isin'], listed[0]['sedol'], listed[0]['income'], listed[0]['shareClass']) == (
            'IE0000000021', '', 'Accumulating', '')
        assert 'ticker' not in listed[0] and listed[0]['ucits'] == 'Yes'
        assert (listed[1]['classCurrency'], listed[1]['exposureCurrency']) == ('GBP', 'GBP'), 'hedged to GBP'
        assert 'assetClass' not in listed[0] and stored['provider'] == '' and stored['vehicle'] != 'UCITS'
        assert int(conn.execute("SELECT value FROM meta WHERE key = 'catalogueRevision'").fetchone()[0]) == revision + 1
    finally:
        conn.close()
    products.reload()
    after = products.entry(pid)
    assert after['revision'] == before['revision'] + 1 and after['lastEditedBy'] == 'upgrade'
    top = products.history(pid)[0]
    assert top['action'] == 'upgraded' and 'three levels' in top['note']
    assert 'IE0000000021' in top['note'] and 'hedged to' in top['note']
    # a product the move rebuilt without a value moving: its version before
    # reads as the levels read it, so nothing moved
    other = products.history(_fund('Mutual Fund'))[0]
    assert other['action'] == 'upgraded' and other['changeWords'] == [], other['changeWords']
    report = products.describeSource()['levelsReport']
    mine = next(r for r in report if r['productId'] == pid)
    assert mine['name'] == fundName and any('ISIN' in n for n in mine['notes'])
    assert all(p['vehicle'] in products.VEHICLES for p in products.all())
    # the next save writes the levels alone, and the old version restores as they read it
    out = _list(pid, {'isin': 'IE0000000021', 'sedol': 'BPRBL01', 'exchange': 'LSE'})
    assert out['listings'][0]['sedol'] == 'BPRBL01'
    back = products.revert(pid, before['revision'], user='alice', base=out['revision'])
    assert back['listings'][0]['isin'] == before['listings'][0]['isin'] and back['vehicle'] == before['vehicle']


def test_a_line_recorded_before_the_levels_finds_its_listing(store):
    pid = _fund('ETF')
    out = _list(pid, {'isin': 'IE0000000021', 'sedol': 'BPRBL01', 'income': 'Distributing', 'issueCurrency': 'USD',
                      'hedging': ''},
                {'isin': 'IE0000000021', 'sedol': 'BPRBP02', 'income': 'Distributing', 'issueCurrency': 'GBP',
                 'hedging': ''})
    # as the register kept it then: the ISIN as the ticker, the income as the share class
    found = products.findListing(out, {'ticker': 'IE0000000021', 'shareClass': 'Distributing',
                                       'issueCurrency': 'GBP', 'hedging': ''})
    assert found == out['listings'][1]
    # and as it keeps it now
    found = products.findListing(out, {'isin': 'IE0000000021', 'sedol': 'BPRBL01', 'income': 'Distributing',
                                       'shareClass': '', 'issueCurrency': 'USD', 'hedging': ''})
    assert found == out['listings'][0]
    assert products.findListing(out, {'isin': 'IE0000000021', 'sedol': 'B999999', 'issueCurrency': 'USD',
                                      'hedging': ''}) is None
    # a ticker it kept before (D220) is no longer the listing's: it is found
    # by what else it kept
    found = products.findListing(out, {'ticker': 'PRBP', 'issueCurrency': 'GBP', 'hedging': ''})
    assert found == out['listings'][1]


# ------------------------------------------------------------- the outputs ---

def _heldIn():
    """A fund an ordinary Multi-Asset sleeve holds, its category and the
    sleeve's name."""
    for entry in sleeveRepo.listAll(MA):
        if (entry['products'] and not entry.get('problems') and not entry.get('label')
                and entry['category'] not in rules.AUTO_SLEEVE_CATEGORIES
                and entry['category'] == rules.sleeveCategory(entry['category'])):
            for row in entry['products']:
                if not products.isSma(products.get(row['productId'])):
                    return row['productId'], entry['category'], entry['name']
    raise AssertionError('no sleeve holds a fund')


def _model(category, name, currency='USD', mandate=5e7):
    basis = BasisInput(currency=currency, hedging='Hedged')
    result = FixturesScenarioPort().resolve_portfolio(basis, PortfolioKey(currency, 'Moderate', 'Full', False))
    chosen = {}
    for c in result['categories']:
        if c['name'] in rules.AUTO_SLEEVE_CATEGORIES:
            continue
        under = rules.sleeveCategory(c['name'])
        chosen.setdefault(under, name if under == category else sleeves.listSleeves(under, MA)[0]['name'])
    model = buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES, mandate, MA,
                                    False, None, None, mandate, False, currency)
    return result, chosen, model


_CLASSES = ({'isin': 'LU0000000011', 'shareClass': 'I', 'income': 'Accumulating', 'hedging': 'Hedged',
             'classCurrency': 'GBP', 'exposureCurrency': '', 'ukReporting': 'Yes', 'domicile': 'LU',
             'ucits': 'Yes', 'exchange': '', 'sedol': 'B0YBKJ7', 'issueCurrency': 'GBP'},
            {'isin': 'LU0000000011', 'shareClass': 'I', 'income': 'Accumulating', 'hedging': 'Hedged',
             'classCurrency': 'GBP', 'exposureCurrency': '', 'ukReporting': 'Yes', 'domicile': 'LU',
             'ucits': 'Yes', 'exchange': '', 'issueCurrency': 'USD'},
            {'isin': 'LU0000000029', 'shareClass': 'P', 'income': 'Distributing', 'hedging': 'Unhedged',
             'classCurrency': 'USD', 'exposureCurrency': 'USD', 'ukReporting': 'No', 'domicile': 'LU',
             'ucits': 'Yes', 'exchange': '', 'issueCurrency': 'USD'})


def test_a_line_holds_its_first_class_and_the_sheet_names_it(store):
    pid, category, name = _heldIn()
    _list(pid, *_CLASSES, vehicle='Mutual Fund')
    result, chosen, model = _model(category, name)
    line = next(i for g in model['groups'] for i in g['items'] if i['productId'] == pid)
    # the first listing of the first class, whatever the proposal's currency
    assert (line['isin'], line['income'], line['shareClass'], line['ukReporting'], line['issueCurrency']) == (
        'LU0000000011', 'Accumulating', 'I', 'Yes', 'GBP')
    # its exposure is the class's: hedged to GBP, it counts as GBP
    assert line['exposureCurrency'] == 'GBP'
    from openpyxl import Workbook
    book = Workbook()
    writeImplementationSheet(book, result, chosen, rules.AUTO_SLEEVE_CATEGORIES, 5e7, MA,
                             currency='USD', model=model)
    rows = list(book['Implementation'].iter_rows(values_only=True))
    head = next(r for r in rows if r and 'ISIN' in r)
    row = next(r for r in rows if r and r[head.index('Products')] == line['name'])
    got = {c: row[head.index(c)] for c in ('ISIN', 'Income', 'Share Class', 'Issue Currency', 'Hedging',
                                           'UK Reporting')}
    assert got == {'ISIN': 'LU0000000011', 'Income': 'Accumulating', 'Share Class': 'I',
                   'Issue Currency': 'GBP', 'Hedging': '100%', 'UK Reporting': 'Yes'}
    # an SMA's line has no ISIN, nor a code: the code is the account opening's
    sma = next(i for g in model['groups'] for i in g['items'] if products.isSma(i))
    smaRow = next(r for r in rows if r and r[head.index('Products')] == sma['name'])
    assert smaRow[head.index('ISIN')] is None and smaRow[head.index('Share Class')] is None


def test_the_register_keeps_the_class_and_reads_a_line_from_before_as_now(store):
    pid, category, name = _heldIn()
    _list(pid, *_CLASSES, vehicle='Mutual Fund')
    _, _, model = _model(category, name)
    picture = proposalRegister.implementedPicture(model, MA)
    kept = next(i for g in picture for i in g['items'] if i['productId'] == pid)
    assert (kept['isin'], kept['income'], kept['shareClass'], kept['ukReporting'], kept['domicile'],
            kept['ucits'], kept['classCurrency'], kept['sedol']) == (
        'LU0000000011', 'Accumulating', 'I', 'Yes', 'Luxembourg', 'Yes', 'GBP', 'B0YBKJ7')
    assert 'ticker' not in kept and 'regulation' not in kept
    assert 'productCost' not in kept and 'minimumInvestment' not in kept
    assert proposalRegister._heldAsNow(kept) == kept, 'a line from the levels on reads as kept'
    # a line kept before the levels: its income as its share class, its ISIN
    # as its ticker
    old = {'productId': pid, 'name': 'X', 'ticker': 'ie00b4l5y983', 'shareClass': 'Acc',
           'issueCurrency': 'USD', 'hedging': ''}
    now = proposalRegister._heldAsNow(old)
    assert (now['income'], now['shareClass'], now['isin'], now['ticker']) == (
        'Accumulating', None, 'IE00B4L5Y983', products.NO_TICKER)
    assert old['shareClass'] == 'Acc', 'the record itself is not changed'
    # a ticker that is a ticker stays one; a share class that is no income
    # stays the class
    now = proposalRegister._heldAsNow({'ticker': 'IWDA', 'shareClass': 'I'})
    assert (now['ticker'], now.get('isin'), now['income'], now['shareClass']) == ('IWDA', None, None, 'I')


def test_the_screen_shows_the_isin_with_the_sedol_beneath(store):
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    path = os.path.join(os.path.dirname(__file__), '..', '..', 'generator', 'js', 'implementation.js')
    with open(path, encoding='utf-8') as fh:
        source = fh.read()
    fn = source[source.index('function listingSub('):source.index('function rows()')]
    probe = (
        "var App = {esc: function (s) { return String(s); }};\n" + fn
        + "process.stdout.write(JSON.stringify([\n"
        "  [heldIdent({isin: 'IE00B4L5Y983', sedol: 'B0YBKJ7'}), listingSub({isin: 'IE00B4L5Y983', sedol: 'B0YBKJ7', issueCurrency: 'USD', hedging: 'Unhedged'})],\n"
        "  [heldIdent({isin: '', sedol: 'B0YBKJ7'}), listingSub({isin: '', sedol: 'B0YBKJ7', issueCurrency: 'USD'})],\n"
        "  [heldIdent({isin: 'LU0000000011', sedol: ''}), listingSub({isin: 'LU0000000011', sedol: '', issueCurrency: 'GBP'})],\n"
        "  [textOrDash(''), textOrDash('Yes')]]));\n")
    got = json.loads(subprocess.run([node, '-e', probe], capture_output=True, text=True, check=True).stdout)
    assert got[0] == ['IE00B4L5Y983', '<small class="lst-sub">B0YBKJ7 \u00b7 USD \u00b7 Unhedged</small>']
    assert got[1] == ['B0YBKJ7', '<small class="lst-sub">USD</small>'], 'no ISIN: the SEDOL'
    assert got[2] == ['LU0000000011', '<small class="lst-sub">GBP</small>']
    assert got[1][0] and 'mdash' in got[3][0] and got[3][1] == 'Yes'
    # the line carries what the columns show
    item = source[source.index('function implItem('):source.index('function holdAlternatives(')]
    for field in ('isin: product.isin', 'income: product.income', 'ukReporting: product.ukReporting',
                  'sedol: product.sedol', 'cusip: product.cusip'):
        assert field in item
    row = source[source.index("'<td class=\"txt tick\">' + App.esc(heldIdent(item))"):]
    assert row.index('item.income') < row.index('item.shareClass') < row.index('item.ukReporting')


# ---------------------------------------------- an SMA's mandates by name ---

def test_an_smas_mandates_each_have_a_name_of_their_own(store):
    """An SMA's mandates are its strategy's variants (D222): each may carry a
    name, blank for the strategy's - as a fund's share class may (D225) -
    held to the strategy's length."""
    sma = next(p['productId'] for p in products.all() if products.isSma(p)
               and p['ownCategory'] == products.EQUITY_CATEGORY)
    out = _list(sma, {'code': 'QV1', 'issueCurrency': 'USD', 'hedging': 'Unhedged', 'className': ' GS Value (USD) '},
                {'code': 'QV2', 'issueCurrency': 'GBP', 'hedging': 'Hedged', 'className': 'GS Value (GBP Hedged)'},
                {'code': 'QV3', 'issueCurrency': 'EUR', 'hedging': 'Hedged', 'className': ''})
    assert [l['className'] for l in out['listings']] == ['GS Value (USD)', 'GS Value (GBP Hedged)', '']
    assert products.get(sma)['className'] == 'GS Value (USD)', 'the held mandate\'s, at the top level'
    renamed = _list(sma, {'code': 'QV1', 'issueCurrency': 'USD', 'hedging': 'Unhedged', 'className': 'GS Value'},
                    {'code': 'QV2', 'issueCurrency': 'GBP', 'hedging': 'Hedged', 'className': 'GS Value (GBP Hedged)'},
                    {'code': 'QV3', 'issueCurrency': 'EUR', 'hedging': 'Hedged', 'className': ''})
    lines = [(c['label'], c['before'], c['after']) for c in products.history(sma)[0]['changes']]
    assert ('Name of QV1', 'GS Value (USD)', 'GS Value') in lines
    assert renamed['listings'][0]['className'] == 'GS Value'
    with pytest.raises(ValidationError, match='to 120 characters') as refused:
        _list(sma, {'code': 'QV1', 'issueCurrency': 'USD', 'hedging': 'Unhedged', 'className': 'x' * 121})
    assert refused.value.field == 'listing0className'
    fund = _fund('ETF')
    assert _list(fund, {'className': 'A Class (Acc)'})['listings'][0]['className'] == 'A Class (Acc)', \
        'a fund\'s share class has a name too (D225)'


def test_a_file_names_mandates_and_one_without_the_column_keeps_them(store, tmp_path):
    sma = next(p['productId'] for p in products.all() if products.isSma(p)
               and p['ownCategory'] != products.EQUITY_CATEGORY)
    rows = _rowsFor(sma, [{'MandateCode': 'CF1', 'IssueCurrency': 'USD', 'ExposureCurrency': 'USD',
                           'Name': 'PMG Core Fixed Income (USD)'},
                          {'MandateCode': 'CF2', 'IssueCurrency': 'EUR', 'ExposureCurrency': 'EUR',
                           'Name': 'PMG Core Fixed Income (EUR)'}])
    path = _file(tmp_path, rows)
    preview = products.importPreview(path, _upload(path))
    products.importFile(path, _upload(path), user='alice', base=preview['revision'])
    assert [l['className'] for l in products.entry(sma)['listings']] == [
        'PMG Core Fixed Income (USD)', 'PMG Core Fixed Income (EUR)']
    # the export writes them, and loads back with nothing to do
    again = _file(tmp_path, products.exportRows(), 'again.csv')
    preview = products.importPreview(again, _upload(again))
    assert not preview['changed']
    # a file without the column leaves each mandate's name as it is
    head = rows[0]
    keep = [n for n, c in enumerate(head) if c != 'Name']
    path = _file(tmp_path, [[r[n] for n in keep] for r in products.exportRows()], 'older.csv')
    preview = products.importPreview(path, _upload(path))
    assert not preview['changed'], 'the names kept, so nothing moves'


def test_the_sample_files_sma_comes_in_three_named_mandates(store):
    here = os.path.join(os.path.dirname(__file__), '..', '..', 'productSource')
    byId, _ = products.readFile(os.path.join(here, 'products-listings.csv'))
    sma = byId['gs-us-focused-value-sma']
    assert [(l['code'], l['hedging'], l['isg'], l['className']) for l in sma['listings']] == [
        ('QV1', 0.0, '', 'GS US Focused Value'),
        ('QV2', 100.0, '', 'GS US Focused Value (GBP Hedged)'),
        ('QV3', 50.0, 'Yes', 'GS US Focused Value (GBP ISG Hedged)')]


def test_a_product_stored_before_mandate_names_reads_exports_and_saves(store, tmp_path):
    """A listing stored before D222 has no name at all: it reads as blank
    everywhere - the catalogue, the export, a save - with no upgrade."""
    import json as _json
    sma = next(p['productId'] for p in products.all() if products.isSma(p))
    conn = sleeveRepo.connection()
    try:
        row = conn.execute('SELECT listings FROM products WHERE productId = ?', (sma,)).fetchone()
        old = [{k: v for k, v in l.items() if k != 'className'} for l in _json.loads(row['listings'])]
        conn.execute('UPDATE products SET listings = ? WHERE productId = ?', (_json.dumps(old), sma))
        conn.commit()
    finally:
        conn.close()
    products.reload()
    assert products.get(sma)['className'] == ''
    assert all(l['className'] == '' for l in products.entry(sma)['listings'])
    rows = products.exportRows()
    at = rows[0].index('Name')
    assert all(r[at] == '' for r in rows[1:] if r[0] == sma)
    assert products.all() and products.history(sma)
    _list(sma, {'className': 'Now named'})
    assert products.entry(sma)['listings'][0]['className'] == 'Now named'


# -------------------------------------------------- hedging, a percentage ---

def test_a_hedging_stored_as_a_word_reads_as_its_percentage(store):
    """D224: a listing stored while the hedging was a word - Hedged,
    Unhedged, ISG Hedged - reads as the percentage, ISG Hedged ticked ISG,
    everywhere: the catalogue, the export, a save, and a proposal's line
    recorded holding it still finds it."""
    import json as _json
    sma = next(p['productId'] for p in products.all() if products.isSma(p))
    _list(sma, {'code': 'W1', 'issueCurrency': 'USD'}, {'code': 'W2', 'issueCurrency': 'GBP'},
          {'code': 'W3', 'issueCurrency': 'GBP'})
    conn = sleeveRepo.connection()
    try:
        row = conn.execute('SELECT listings FROM products WHERE productId = ?', (sma,)).fetchone()
        old = _json.loads(row['listings'])
        for listing, word in zip(old, ('Unhedged', 'Hedged', 'ISG Hedged')):
            listing['hedging'] = word
            listing.pop('isg', None)
        conn.execute('UPDATE products SET listings = ? WHERE productId = ?', (_json.dumps(old), sma))
        conn.commit()
    finally:
        conn.close()
    products.reload()
    listed = products.entry(sma)['listings']
    assert [(l['code'], l['hedging'], l['isg']) for l in listed] == [
        ('W1', 0.0, ''), ('W2', 100.0, ''), ('W3', 50.0, 'Yes')]
    rows = products.exportRows()
    at, isg = rows[0].index('Hedging'), rows[0].index('ISG')
    assert [(r[at], r[isg]) for r in rows[1:] if r[0] == sma] == [('0%', ''), ('100%', ''), ('50%', 'Yes')]
    # a line recorded holding the listing by its word finds it
    held = products.findListing(products.get(sma), {'code': 'W3', 'issueCurrency': 'GBP', 'hedging': 'ISG Hedged'})
    assert held is not None and held['code'] == 'W3'
    held = products.findListing(products.get(sma), {'code': 'W3', 'issueCurrency': 'GBP', 'hedging': '50% · ISG'})
    assert held is not None and held['code'] == 'W3', 'and by the words a line records now'
    assert products.findListing(products.get(sma), {'code': 'W3', 'issueCurrency': 'GBP', 'hedging': '50%'}) is None
    _list(sma, {'code': 'W1', 'issueCurrency': 'USD', 'hedging': '25'})
    assert products.entry(sma)['listings'][0]['hedging'] == 25.0


def test_a_spreadsheet_percentage_cell_reads_as_it_shows(store, tmp_path):
    """D224: Excel keeps 50% as 0.5; a cell formatted as a percentage is read
    as it shows, a plain number as the percentage it is."""
    from openpyxl import Workbook
    pid = _held()
    rows = _rowsFor(pid, [{'SEDOL': 'BPCT001', 'IssueCurrency': 'USD', 'Hedging': ''},
                          {'SEDOL': 'BPCT002', 'IssueCurrency': 'GBP', 'Hedging': ''},
                          {'SEDOL': 'BPCT003', 'IssueCurrency': 'EUR', 'Hedging': ''}])
    book = Workbook()
    sheet = book.active
    sheet.title = 'Products'
    at = rows[0].index('Hedging')
    for n, row in enumerate(rows):
        sheet.append(row)
    mine = [n + 1 for n, row in enumerate(rows) if row[0] == pid]
    sheet.cell(row=mine[0], column=at + 1, value=0.5).number_format = '0%'
    sheet.cell(row=mine[1], column=at + 1, value=75)
    sheet.cell(row=mine[2], column=at + 1, value=1).number_format = '0.0%'
    path = str(tmp_path / 'products.xlsx')
    book.save(path)
    byId, _ = products.readFile(path)
    assert [l['hedging'] for l in byId[pid]['listings']] == [50.0, 75.0, 100.0]
    # the sample workbook's percentage cells read as the CSV's words do
    here = os.path.join(os.path.dirname(__file__), '..', '..', 'productSource')
    fromCsv, _ = products.readFile(os.path.join(here, 'products-listings.csv'))
    fromXlsx, _ = products.readFile(os.path.join(here, 'products-listings.xlsx'))
    assert [(l['hedging'], l['isg']) for l in fromXlsx['gs-us-focused-value-sma']['listings']] == \
        [(l['hedging'], l['isg']) for l in fromCsv['gs-us-focused-value-sma']['listings']] == \
        [(0.0, ''), (100.0, ''), (50.0, 'Yes')]


# ---------------------------------------- the desk's data sheet (D225) ---

def test_a_fund_listing_has_a_name_a_ric_an_exchange_country_and_a_mandate_code(store, tmp_path):
    """The desk's product data sheet (D225): a share class's name, a listing's
    RIC and its exchange's country, and a fund's listing's own mandate code -
    saved, served, exported under their columns and loaded back with nothing
    to do; the CUSIP is the desk's own, any letters and digits."""
    pid = _fund('ETF')
    out = _list(pid, {'isin': 'IE0000000021', 'className': 'Probe UCITS ETF USD (Acc)', 'exchange': 'LSE',
                      'exchangeCountry': 'gb', 'ric': 'PRB.L', 'cusip': 'gs4471a', 'code': 'E01',
                      'issueCurrency': 'USD'},
                {'isin': 'IE0000000021', 'className': 'Probe UCITS ETF USD (Acc)', 'exchange': 'Xetra',
                 'exchangeCountry': 'DE', 'ric': 'PRB.DE', 'code': 'E01', 'issueCurrency': 'EUR'})
    one, two = out['listings']
    assert (one['className'], one['exchangeCountry'], one['ric'], one['cusip'], one['code']) == \
        ('Probe UCITS ETF USD (Acc)', 'United Kingdom', 'PRB.L', 'GS4471A', 'E01')
    assert two['code'] == 'E01', 'one code on two listings of one fund'
    rows = products.exportRows()
    head = rows[0]
    mine = [dict(zip(head, r)) for r in rows[1:] if r[0] == pid]
    assert [(r['Name'], r['ExchangeCountry'], r['RIC'], r['MandateCode']) for r in mine] == [
        ('Probe UCITS ETF USD (Acc)', 'United Kingdom', 'PRB.L', 'E01'),
        ('Probe UCITS ETF USD (Acc)', 'Germany', 'PRB.DE', 'E01')]
    path = _file(tmp_path, rows)
    preview = products.importPreview(path, _upload(path))
    assert not preview['changed'] and not preview['added']
    # a RIC is one product's, as a SEDOL is
    with pytest.raises(ValidationError, match='belongs to') as refused:
        _list(_fund('Mutual Fund'), {'ric': 'prb.l'})
    assert refused.value.field == 'listing0ric'
    # the rows of one ISIN give one name
    with pytest.raises(ValidationError, match='same name'):
        _list(pid, {'isin': 'IE0000000021', 'className': 'A', 'exchange': 'LSE'},
              {'isin': 'IE0000000021', 'className': 'B', 'exchange': 'Xetra'})


def test_the_headings_a_file_had_before_the_data_sheet_are_still_read(store, tmp_path):
    """ProductCost, DistributionYield and MandateName are read as the Total
    Expense Ratio, the Yield and the Name (D225); a file with a Name column
    and no Strategy is one from before D220, its Name the strategy's."""
    sma = next(p['productId'] for p in products.all() if products.isSma(p))
    rows = _rowsFor(sma, [{'MandateCode': 'OLD1', 'IssueCurrency': 'USD', 'ExposureCurrency': 'USD',
                           'Name': 'Old Named', 'TotalExpenseRatio': '0.44', 'Yield': '2.5'}])
    head = rows[0]
    rows[0] = [{'TotalExpenseRatio': 'ProductCost', 'Yield': 'DistributionYield', 'Name': 'MandateName'}.get(c, c)
               for c in head]
    byId, _ = products.readFile(_file(tmp_path, rows))
    one = byId[sma]['listings'][0]
    assert (one['productCost'], one['distributionYield'], one['className']) == (0.44, 2.5, 'Old Named')
    # Name and no Strategy: the strategy's name, as before D220
    at = head.index('Strategy')
    older = [[c for n, c in enumerate(r) if n != head.index('Name')] for r in rows]
    older[0][at] = 'Name'
    byId, _ = products.readFile(_file(tmp_path, older, 'older.csv'))
    assert byId[sma]['name'] == products.entry(sma)['name'] and byId[sma]['listings'][0]['className'] == ''


def test_the_headings_of_the_desks_product_table_are_read(store, tmp_path):
    """The desk's product table heads its columns as people write them (D225):
    Cusip, UCITs, Exchange Country and UKReportingStatus under their
    spellings, and Pricing Currency as the class currency (D226) - on an
    SMA's row, the currency its mandate is run in, its issue currency."""
    sma = next(p['productId'] for p in products.all() if products.isSma(p))
    rows = _rowsFor(sma, [{'MandateCode': 'DSK1', 'ClassCurrency': 'CHF', 'IssueCurrency': '',
                           'ExposureCurrency': 'CHF'}])
    head = rows[0]
    rows = [[c for n, c in enumerate(r) if head[n] != 'IssueCurrency'] for r in rows]
    rows[0] = [{'ClassCurrency': 'Pricing Currency', 'CUSIP': 'Cusip', 'UCITSCompliant': 'UCITs',
                'ExchangeCountry': 'Exchange Country', 'UKReporting': 'UKReportingStatus'}.get(c, c)
               for c in rows[0]]
    byId, _ = products.readFile(_file(tmp_path, rows))
    one = byId[sma]['listings'][0]
    assert (one['issueCurrency'], one['classCurrency']) == ('CHF', '')
    # an SMA's row that gives both currencies gives one
    rows = _rowsFor(sma, [{'MandateCode': 'DSK1', 'ClassCurrency': 'CHF', 'IssueCurrency': 'USD',
                           'ExposureCurrency': 'CHF'}])
    with pytest.raises(products.BadCatalogue, match='run in one currency'):
        products.readFile(_file(tmp_path, rows, 'both.csv'))


def test_a_domicile_is_the_countrys_name(store, tmp_path):
    """A domicile, and an exchange's country (D228), is stored and shown as
    the country's name (D227): a sheet
    may give it in capitals, by another name or by its code; a name that is
    no country is refused; one stored as its code reads as the name."""
    pid = _fund('Mutual Fund')
    for given, name in (('LUXEMBOURG', 'Luxembourg'), ('lu', 'Luxembourg'), ('Republic of Ireland', 'Ireland'),
                        ('UK', 'United Kingdom'), ('the netherlands', 'Netherlands'), ('Cote d\'Ivoire', 'Côte d\'Ivoire'),
                        ('Cayman Islands', 'Cayman Islands'), ('-', '')):
        # each save moves the cost too, so two givings of one country both save
        given = {'domicile': given, 'productCost': 0.1 + len(given) / 1000.0 + len(name) / 100000.0}
        assert _list(pid, given)['listings'][0]['domicile'] == name, given
    with pytest.raises(ValidationError, match='is not a country') as refused:
        _list(pid, {'domicile': 'Luxemborg'})
    assert refused.value.field == 'listing0domicile'
    import json as _json
    _list(pid, {'domicile': 'Ireland'})
    conn = sleeveRepo.connection()
    try:
        row = conn.execute('SELECT listings FROM products WHERE productId = ?', (pid,)).fetchone()
        old = [dict(l, domicile='IE', exchangeCountry='GB') for l in _json.loads(row['listings'])]
        conn.execute('UPDATE products SET listings = ? WHERE productId = ?', (_json.dumps(old), pid))
        conn.commit()
    finally:
        conn.close()
    products.reload()
    assert products.entry(pid)['listings'][0]['domicile'] == 'Ireland'
    assert products.entry(pid)['listings'][0]['exchangeCountry'] == 'United Kingdom'
    out = _list(pid, {'domicile': 'Ireland', 'exchange': 'LSE', 'exchangeCountry': 'UNITED KINGDOM'})
    assert out['listings'][0]['exchangeCountry'] == 'United Kingdom'


def test_a_sheet_may_give_the_words_a_desk_uses(store):
    """A desk's sheet says Distribution, Capitalisation, Full replication,
    Swap or Reporting (D228): each reads as the word the tool keeps."""
    pid = _fund('ETF')
    for field, given, kept in (('income', 'Distribution', 'Distributing'), ('income', 'Accumulation', 'Accumulating'),
                               ('income', 'Capitalisation', 'Accumulating'), ('income', 'Inc', 'Distributing'),
                               ('replication', 'Full Replication', 'Physical'),
                               ('replication', 'Physical (Optimised)', 'Sampled'),
                               ('replication', 'Swap-based', 'Synthetic'), ('ukReporting', 'Reporting', 'Yes'),
                               ('ukReporting', 'Non-reporting', 'No'), ('ucits', 'UCITS', 'Yes')):
        out = _list(pid, {field: given, 'productCost': 0.1 + len(given + field) / 10000.0})
        assert out['listings'][0][field] == kept, (field, given)
    with pytest.raises(ValidationError, match='replication'):
        _list(pid, {'replication': 'Mostly'})


def test_a_cell_that_says_nothing_is_blank(store, tmp_path):
    """NULL, N/A, #N/A, None, nan, nil and a dash, as an export or a sheet
    writes nothing, are blank in every column of a file (D229) - an optional
    one is left empty, a needed one is refused as missing; NA is Namibia."""
    pid = _fund('ETF')
    rows = _rowsFor(pid, [dict(ETF[0], SEDOL='NULL', RIC='n/a', CUSIP='#N/A', Benchmark='None', Exchange='-',
                               Yield='nan', Replication='NULL', ExchangeCountry='null')])
    one = products.readFile(_file(tmp_path, rows))[0][pid]['listings'][0]
    assert (one['sedol'], one['ric'], one['cusip'], one['benchmark'], one['exchange'], one['distributionYield'],
            one['replication'], one['exchangeCountry']) == ('', '', '', '', '', None, '', '')
    rows = _rowsFor(pid, [dict(ETF[0], ExposureCurrency='NULL')])
    with pytest.raises(products.BadCatalogue, match='Exposure currency'):
        products.readFile(_file(tmp_path, rows, 'missing.csv'))
    rows = _rowsFor(pid, [dict(ETF[0], Domicile='NA')])
    assert products.readFile(_file(tmp_path, rows, 'na.csv'))[0][pid]['listings'][0]['domicile'] == 'Namibia'


def test_a_funds_share_class_may_be_hedged_by_isg(store, tmp_path):
    """ISG hedges a fund's class as it does an SMA's mandate (D226): a
    foreign currency bond fund's class hedged 100% by ISG is 100% and
    ticked, the tick the class's, so the rows of one ISIN give the same."""
    pid = _fund('Mutual Fund')
    out = _list(pid, {'isin': 'IE0000000021', 'hedging': '100', 'isg': 'Yes', 'classCurrency': 'GBP',
                      'exposureCurrency': 'GBP', 'issueCurrency': 'GBP'},
                {'isin': 'IE0000000021', 'hedging': '100', 'isg': 'Yes', 'classCurrency': 'GBP',
                 'exposureCurrency': 'GBP', 'issueCurrency': 'USD'})
    assert [(l['hedging'], l['isg']) for l in out['listings']] == [(100.0, 'Yes'), (100.0, 'Yes')]
    assert products.hedgingText(out['listings'][0]) == '100% \u00b7 ISG'
    with pytest.raises(ValidationError, match='same ISG') as refused:
        _list(pid, {'isin': 'IE0000000021', 'hedging': '100', 'isg': 'Yes', 'classCurrency': 'GBP',
                    'issueCurrency': 'GBP', 'exposureCurrency': 'GBP'},
              {'isin': 'IE0000000021', 'hedging': '100', 'isg': '', 'classCurrency': 'GBP',
               'issueCurrency': 'USD', 'exposureCurrency': 'GBP'})
    assert refused.value.field == 'listing1isg'
    rows = products.exportRows()
    mine = [dict(zip(rows[0], r)) for r in rows[1:] if r[0] == pid]
    assert [r['ISG'] for r in mine] == ['Yes', 'Yes']
    path = _file(tmp_path, rows)
    assert not products.importPreview(path, _upload(path))['changed']


def test_a_mandate_name_stored_before_the_data_sheet_is_its_name(store, tmp_path):
    """A listing stored between D222 and D225 kept its mandate's name as
    mandateName: it reads as the Name, and a file that changes the Name
    changes it - the column is read, not kept from the catalogue."""
    import json as _json
    sma = next(p['productId'] for p in products.all() if products.isSma(p))
    conn = sleeveRepo.connection()
    try:
        row = conn.execute('SELECT listings FROM products WHERE productId = ?', (sma,)).fetchone()
        old = []
        for l in _json.loads(row['listings']):
            l = {k: v for k, v in l.items() if k not in ('className', 'ric', 'exchangeCountry')}
            l['mandateName'] = 'Stored Variant'
            old.append(l)
        conn.execute('UPDATE products SET listings = ? WHERE productId = ?', (_json.dumps(old), sma))
        conn.commit()
    finally:
        conn.close()
    products.reload()
    assert all(l['className'] == 'Stored Variant' and 'mandateName' not in l
               for l in products.entry(sma)['listings'])
    rows = products.exportRows()
    at = rows[0].index('Name')
    for r in rows[1:]:
        if r[0] == sma:
            r[at] = 'Renamed Variant'
    path = _file(tmp_path, rows)
    preview = products.importPreview(path, _upload(path))
    products.importFile(path, _upload(path), user='alice', base=preview['revision'])
    assert all(l['className'] == 'Renamed Variant' for l in products.entry(sma)['listings'])


def test_a_file_without_the_mandate_code_column_keeps_a_funds_codes(store, tmp_path):
    """A fund's listing's code (D225) is kept by a file with no MandateCode
    column, as its benchmark is by one with no Benchmark column."""
    pid = _fund('ETF')
    _list(pid, {'isin': 'IE0000000021', 'exchange': 'LSE', 'issueCurrency': 'USD', 'code': 'E77'})
    rows = products.exportRows()
    drop = rows[0].index('MandateCode')
    path = _file(tmp_path, [[c for n, c in enumerate(r) if n != drop] for r in rows])
    preview = products.importPreview(path, _upload(path))
    assert not preview['changed'], 'the code kept, so nothing moves'
    with pytest.raises(ValidationError, match='nothing to import'):
        products.importFile(path, _upload(path), user='alice', base=preview['revision'])
    assert products.entry(pid)['listings'][0]['code'] == 'E77'
