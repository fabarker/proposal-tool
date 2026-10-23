"""A separate account's SMA mandates (D197): one code a base currency and,
for a Public Equity SMA, a hedging version, each with figures of its own,
kept on the product and loaded from a mandate file of their own.

Each test has a repository file of its own, loaded from the packaged files
on its first open exactly as an upgraded host's is."""
import base64
import csv
import json
import sqlite3

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import products, sleeveRepo, sleeveTools
from cyrus_pmg.pmgService.scenario.types import ValidationError

#: a Public Equity SMA, a fixed-income SMA and a fund in the packaged file
EQUITY = 'gs-concentrated-growth-sma'
BOND = 'gsam-core-municipal-sma'
FUND = 'gs-us-corporate-bond-fund'


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


def _fields(pid, **over):
    entry = products.entry(pid)
    fields = {k: entry[k] for k in products.KEYS}
    fields['currencies'] = entry['currencies']
    fields['mandates'] = entry['mandates']
    fields.update(over)
    return fields


def _save(pid, mandates, **over):
    entry = products.entry(pid)
    return products.update(pid, _fields(pid, mandates=mandates, **over), user='alice',
                           base=entry['revision'])


def _m(code, currency, hedging='', **figures):
    return dict({'code': code, 'currency': currency, 'hedging': hedging}, **figures)


def _stored(code, currency, hedging='', productCost=None, minimumInvestment=None,
            distributionYield=None):
    return {'code': code, 'currency': currency, 'hedging': hedging, 'productCost': productCost,
            'minimumInvestment': minimumInvestment, 'distributionYield': distributionYield}


def _file(tmp_path, rows, name='mandates.csv'):
    path = tmp_path / name
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        csv.writer(fh).writerows(rows)
    return str(path)


def _upload(path):
    with open(path, 'rb') as fh:
        return base64.b64encode(fh.read()).decode('ascii')


def _row(pid, code, currency, hedging, category, cost='', minimum='', yield_=''):
    return [pid, code, currency, hedging, category, cost, minimum, yield_]


def _equityRows():
    return [list(products.MANDATE_COLUMNS),
            _row(EQUITY, 'PMG-CG-USD-H', 'USD', 'Hedged', 'Public Equity', '0.45', '1000000'),
            _row(EQUITY, 'PMG-CG-USD-U', 'USD', 'Unhedged', 'Public Equity', '0.4'),
            _row(EQUITY, 'PMG-CG-GBP-I', 'GBP', 'ISG Hedged', 'Public Equity', '', '', '1.2'),
            _row(BOND, 'MUNI-USD', 'USD', '', 'Investment Grade Fixed Income')]


# ------------------------------------------------------------ the product ---

def test_the_packaged_file_marks_the_separate_accounts_by_vehicle(store):
    assert products.isSma(products.get(EQUITY)) and products.isSma(products.get(BOND))
    assert products.isSma({'vehicle': 'Separate Account'}) and products.isSma({'vehicle': 'SMA'})
    assert not products.isSma({'vehicle': 'ETF'}) and not products.isSma({})
    assert products.entry(EQUITY)['category'] == products.EQUITY_CATEGORY
    assert products.get(EQUITY)['mandates'] == [] and products.entry(BOND)['mandates'] == []


def test_an_equity_sma_keeps_a_code_a_currency_and_version_in_the_grid_order(store):
    saved = _save(EQUITY, [_m('PMG-CG-GBP-U', 'gbp', 'Unhedged', productCost='0.5'),
                           _m('PMG-CG-USD-H', 'USD', 'Hedged', minimumInvestment='1,000,000'),
                           _m('PMG-CG-USD-I', 'USD', 'ISG Hedged', distributionYield='2.1%')])
    assert saved['mandates'] == [
        _stored('PMG-CG-USD-H', 'USD', 'Hedged', minimumInvestment=1000000.0),
        _stored('PMG-CG-USD-I', 'USD', 'ISG Hedged', distributionYield=2.1),
        _stored('PMG-CG-GBP-U', 'GBP', 'Unhedged', productCost=0.5)]
    assert products.get(EQUITY)['mandates'] == saved['mandates'], 'served with the product'
    top = products.history(EQUITY)[0]
    assert top['changeWords'] == ['SMA mandate PMG-CG-USD-H — → USD · Hedged · min 1,000,000',
                                  'SMA mandate PMG-CG-USD-I — → USD · ISG Hedged · yield 2.1',
                                  'SMA mandate PMG-CG-GBP-U — → GBP · Unhedged · cost 0.5']
    # a change to one, and one taken off, read as such
    again = _save(EQUITY, [_m('PMG-CG-GBP-U', 'GBP', 'Unhedged', productCost='0.55'),
                           _m('PMG-CG-USD-H', 'USD', 'Hedged', minimumInvestment='1000000')])
    assert [m['code'] for m in again['mandates']] == ['PMG-CG-USD-H', 'PMG-CG-GBP-U']
    assert products.history(EQUITY)[0]['changeWords'] == [
        'SMA mandate PMG-CG-USD-I USD · ISG Hedged · yield 2.1 → —',
        'SMA mandate PMG-CG-GBP-U GBP · Unhedged · cost 0.5 → GBP · Unhedged · cost 0.55']


def test_a_bond_sma_has_one_code_a_currency_and_no_version(store):
    saved = _save(BOND, [_m('MUNI-USD', 'USD'), _m('MUNI-GBP', 'GBP', productCost='0.3')],
                  category='Investment Grade Fixed Income')
    assert saved['mandates'] == [_stored('MUNI-USD', 'USD'), _stored('MUNI-GBP', 'GBP', productCost=0.3)]
    assert products.history(BOND)[0]['changeWords'][0] == 'SMA mandate MUNI-USD — → USD'


@pytest.mark.parametrize('pid, mandates, over, field, words', [
    (FUND, [_m('X-USD', 'USD')], {'vehicle': 'Mutual Fund'}, 'mandates', 'Only a separate account'),
    (EQUITY, [_m('X-USD', 'USD')], {}, 'mandate0hedging', 'needs its hedging version'),
    (EQUITY, [_m('X-USD', 'USD', 'hedged')], {}, 'mandate0hedging', 'spelled Hedged'),
    (EQUITY, [_m('X-USD', 'USD', 'Half')], {}, 'mandate0hedging', 'one of Hedged, ISG Hedged, Unhedged'),
    (BOND, [_m('X-USD', 'USD', 'Hedged')], {}, 'mandate0hedging', 'leave the hedging of X-USD blank'),
    (EQUITY, [_m('', 'USD', 'Hedged')], {}, 'mandate0code', 'Give the SMA mandate its code'),
    (EQUITY, [_m('X-USD', 'JPY', 'Hedged')], {}, 'mandate0currency', 'one of USD, GBP, CHF, EUR'),
    (EQUITY, [_m('X-USD', 'USD', 'Hedged'), _m('x-usd', 'GBP', 'Hedged')], {}, 'mandate1code', 'twice'),
    (EQUITY, [_m('X-1', 'USD', 'Hedged'), _m('X-2', 'USD', 'Hedged')], {}, 'mandate1code',
     'Two SMA mandates for USD · Hedged: X-1 and X-2'),
    (EQUITY, [_m('X-USD', 'USD', 'Hedged', productCost='45')], {}, 'mandate0productCost', 'looks wrong'),
    (EQUITY, [_m('X-USD', 'USD', 'Hedged', minimumInvestment='lots')], {}, 'mandate0minimumInvestment',
     'Minimum investment of X-USD'),
    (EQUITY, [_m('X' * 41, 'USD', 'Hedged')], {}, 'mandate0code', '40 characters'),
])
def test_a_mandate_that_is_not_one_is_refused_naming_its_box(store, pid, mandates, over, field, words):
    with pytest.raises(ValidationError) as caught:
        _save(pid, mandates, **over)
    assert caught.value.field == field and words in caught.value.message


def test_a_mandate_in_a_currency_the_sma_is_not_offered_in_is_refused(store):
    not_offered = {'CHF': {'offered': False}}
    with pytest.raises(ValidationError) as caught:
        _save(EQUITY, [_m('X-CHF', 'CHF', 'Hedged')], currencies=not_offered)
    assert caught.value.field == 'mandate0currency' and 'where' in caught.value.message
    assert 'not offered' in caught.value.message


def test_a_code_belongs_to_one_product_withdrawn_or_not(store):
    _save(EQUITY, [_m('PMG-CG-USD-H', 'USD', 'Hedged')])
    with pytest.raises(ValidationError) as caught:
        _save(BOND, [_m('pmg-cg-usd-h', 'USD')])
    assert caught.value.field == 'mandate0code'
    assert 'belongs to GS Concentrated Growth SMA (gs-concentrated-growth-sma)' in caught.value.message
    # the form adds a product with a taken code: refused too
    with pytest.raises(ValidationError):
        products.create(dict(_fields(EQUITY), productId='x-sma', name='X', mandates=[
            _m('PMG-CG-USD-H', 'USD', 'Hedged')]), user='alice')


def test_a_save_that_says_nothing_of_the_mandates_keeps_them_and_a_revert_too(store):
    _save(EQUITY, [_m('PMG-CG-USD-H', 'USD', 'Hedged')])
    entry = products.entry(EQUITY)
    fields = {k: entry[k] for k in products.KEYS}
    fields['liquidity'] = 'Weekly'
    saved = products.update(EQUITY, fields, user='alice', base=entry['revision'])
    assert saved['liquidity'] == 'Weekly' and [m['code'] for m in saved['mandates']] == ['PMG-CG-USD-H']
    # version 1 was written before the mandates: put back, it keeps them
    conn = sqlite3.connect(str(store / 'sleeves.db'))
    body = json.loads(conn.execute('SELECT body FROM productHistory WHERE productId = ? AND revision = 1',
                                   (EQUITY,)).fetchone()[0])
    conn.close()
    assert 'mandates' in body
    body.pop('mandates')
    conn = sqlite3.connect(str(store / 'sleeves.db'))
    conn.execute('UPDATE productHistory SET body = ? WHERE productId = ? AND revision = 1',
                 (json.dumps(body), EQUITY))
    conn.commit()
    conn.close()
    back = products.revert(EQUITY, 1, user='alice', base=saved['revision'])
    assert back['liquidity'] == entry['liquidity'] and [m['code'] for m in back['mandates']] == ['PMG-CG-USD-H']


def test_a_catalogue_from_before_gains_the_column_blank(store):
    products.all()
    conn = sqlite3.connect(str(store / 'sleeves.db'))
    conn.execute('ALTER TABLE products DROP COLUMN mandates')
    conn.execute("UPDATE meta SET value = '14' WHERE key = 'schemaVersion'")
    conn.commit()
    conn.close()
    products.reload()
    assert sleeveRepo.describe()['schemaVersion'] == 15
    assert products.get(EQUITY)['mandates'] == []
    _save(EQUITY, [_m('PMG-CG-USD-H', 'USD', 'Hedged')])
    assert [m['code'] for m in products.get(EQUITY)['mandates']] == ['PMG-CG-USD-H']


# --------------------------------------------------------- the mandate file ---

def test_a_file_is_previewed_then_imported_adding_and_changing_by_code(store, tmp_path):
    _save(EQUITY, [_m('PMG-CG-USD-H', 'USD', 'Hedged', productCost='0.4'),
                   _m('PMG-CG-EUR-H', 'EUR', 'Hedged')])
    untouched = products.entry(FUND)
    path = _file(tmp_path, _equityRows())
    preview = products.mandatePreview('mandates.csv', _upload(path))
    assert (preview['rows'], preview['touched'], preview['same']) == (4, 2, 0)
    assert (preview['added'], preview['changed']) == (3, 1)
    equity, bond = preview['products']
    assert equity['added'] == ['PMG-CG-USD-U', 'PMG-CG-GBP-I'] and equity['changed'] == ['PMG-CG-USD-H']
    assert equity['unchanged'] == 1, 'the EUR code the file does not name stays'
    assert equity['changeWords'][0] == ('SMA mandate PMG-CG-USD-H USD · Hedged · cost 0.4 → '
                                        'USD · Hedged · cost 0.45 · min 1,000,000')
    assert bond['added'] == ['MUNI-USD'] and bond['category'] == ''

    done = products.importMandates('mandates.csv', _upload(path), user='bob', note='from the desk',
                                   base=preview['revision'])
    assert (done['products'], done['added'], done['changed']) == (2, 3, 1)
    assert [m['code'] for m in products.get(EQUITY)['mandates']] == [
        'PMG-CG-USD-H', 'PMG-CG-USD-U', 'PMG-CG-GBP-I', 'PMG-CG-EUR-H']
    assert products.get(EQUITY)['mandates'][0]['minimumInvestment'] == 1000000.0
    assert products.get(BOND)['mandates'] == [_stored('MUNI-USD', 'USD')]
    top = products.history(BOND)[0]
    assert top['action'] == 'imported' and top['source'] == 'mandates.csv' and top['note'] == 'from the desk'
    after = products.entry(FUND)
    assert (after['revision'], after['lastEditedAt']) == (untouched['revision'], untouched['lastEditedAt'])
    # the same file again has nothing to do, and against its old preview is stale
    with pytest.raises(ValidationError) as caught:
        products.importMandates('mandates.csv', _upload(path), user='bob')
    assert 'nothing' in caught.value.message or 'already' in caught.value.message
    with pytest.raises(products.StaleError):
        products.importMandates('mandates.csv', _upload(path), user='bob', base=preview['revision'])


def test_a_row_fills_a_blank_category_and_must_match_one_that_is_set(store, tmp_path):
    products.update(BOND, _fields(BOND, category=''), user='alice', base=products.entry(BOND)['revision'])
    assert products.entry(BOND)['category'] == ''
    path = _file(tmp_path, [list(products.MANDATE_COLUMNS),
                            _row(BOND, 'MUNI-USD', 'USD', '', 'Investment Grade Fixed Income')])
    preview = products.mandatePreview('mandates.csv', _upload(path))
    assert preview['products'][0]['category'] == 'Investment Grade Fixed Income'
    assert 'Category — → Investment Grade Fixed Income' in preview['products'][0]['changeWords']
    products.importMandates('mandates.csv', _upload(path), user='bob', base=preview['revision'])
    assert products.entry(BOND)['category'] == 'Investment Grade Fixed Income'
    path = _file(tmp_path, [list(products.MANDATE_COLUMNS),
                            _row(BOND, 'MUNI-GBP', 'GBP', '', 'Hedge Funds')])
    with pytest.raises(products.BadCatalogue) as caught:
        products.mandatePreview('mandates.csv', _upload(path))
    assert 'row 2' in str(caught.value) and 'Investment Grade Fixed Income' in str(caught.value)


@pytest.mark.parametrize('rows, words', [
    ([_row('no-such', 'X', 'USD', '', 'Public Equity')], 'no product no-such'),
    ([_row(FUND, 'X', 'USD', '', 'Public Equity')], 'not a separate account'),
    ([_row(EQUITY, 'X', 'USD', 'Hedged', '')], 'give the Category'),
    ([_row(EQUITY, 'X', 'USD', 'Hedged', 'public equity')], 'spelled Public Equity'),
    ([_row(EQUITY, 'X', 'USD', 'Hedged', 'Made Up')], 'not one the sleeves are built for'),
    ([_row(EQUITY, '', 'USD', 'Hedged', 'Public Equity')], 'give the MandateCode'),
    ([_row(EQUITY, 'X', 'USD', 'Hedged', 'Public Equity'), _row(EQUITY, 'x', 'GBP', 'Hedged', 'Public Equity')],
     'already on row 2'),
    ([_row(EQUITY, 'X', 'USD', '', 'Public Equity')], 'needs its hedging version'),
    ([_row(BOND, 'X', 'USD', 'Hedged', 'Investment Grade Fixed Income')], 'leave the hedging'),
    ([_row(EQUITY, 'X', 'USD', 'Hedged', 'Public Equity', cost='abc')], 'not a number'),
    ([_row(EQUITY, 'X', 'USD', 'Hedged', 'Public Equity'), _row(EQUITY, 'Y', 'USD', 'Hedged', 'Public Equity')],
     'Two SMA mandates for USD · Hedged'),
    ([_row(EQUITY, 'X', 'USD', 'Hedged', 'Public Equity'), _row(EQUITY, 'Y', 'GBP', 'Hedged', 'Hedge Funds')],
     'differs from the product'),
])
def test_a_file_with_one_bad_row_is_refused_whole_naming_the_row(store, tmp_path, rows, words):
    before = products.describeSource()['revision']
    path = _file(tmp_path, [list(products.MANDATE_COLUMNS)] + rows)
    with pytest.raises(products.BadCatalogue) as caught:
        products.mandatePreview('mandates.csv', _upload(path))
    assert 'row ' in str(caught.value) and words in str(caught.value)
    with pytest.raises(products.BadCatalogue):
        products.importMandates('mandates.csv', _upload(path), user='bob')
    assert products.describeSource()['revision'] == before


def test_a_file_cannot_move_a_code_to_another_product_or_recode_a_cell(store, tmp_path):
    _save(EQUITY, [_m('PMG-CG-USD-H', 'USD', 'Hedged')])
    path = _file(tmp_path, [list(products.MANDATE_COLUMNS),
                            _row(BOND, 'PMG-CG-USD-H', 'USD', '', 'Investment Grade Fixed Income')])
    with pytest.raises(products.BadCatalogue) as caught:
        products.mandatePreview('mandates.csv', _upload(path))
    assert 'belongs to GS Concentrated Growth SMA' in str(caught.value)
    path = _file(tmp_path, [list(products.MANDATE_COLUMNS),
                            _row(EQUITY, 'PMG-CG-USD-HEDGED', 'USD', 'Hedged', 'Public Equity')])
    with pytest.raises(products.BadCatalogue) as caught:
        products.mandatePreview('mandates.csv', _upload(path))
    assert 'USD · Hedged already has the code PMG-CG-USD-H' in str(caught.value)
    # a code may move cell, though: the file changes it by code
    path = _file(tmp_path, [list(products.MANDATE_COLUMNS),
                            _row(EQUITY, 'PMG-CG-USD-H', 'USD', 'ISG Hedged', 'Public Equity')])
    preview = products.mandatePreview('mandates.csv', _upload(path))
    assert preview['products'][0]['changed'] == ['PMG-CG-USD-H']


def test_the_columns_are_the_files_own_under_their_headings_and_no_others(store, tmp_path):
    path = _file(tmp_path, [['productid', 'Code', 'Currency', 'Version', 'Group', 'Cost', 'Minimum', 'Yield', ''],
                            [EQUITY, 'PMG-CG-USD-H', 'usd', 'Hedged', 'Public Equity', '0.4', '', '', '']])
    preview = products.mandatePreview('mandates.csv', _upload(path))
    assert preview['products'][0]['added'] == ['PMG-CG-USD-H']
    path = _file(tmp_path, [list(products.MANDATE_COLUMNS) + ['Benchmark'],
                            _row(EQUITY, 'X', 'USD', 'Hedged', 'Public Equity') + ['R1000G']])
    with pytest.raises(products.BadCatalogue) as caught:
        products.mandatePreview('mandates.csv', _upload(path))
    assert "'Benchmark' is not one of the mandate file's" in str(caught.value)
    path = _file(tmp_path, [['ProductId', 'MandateCode', 'Hedging'], [EQUITY, 'X', 'Hedged']])
    with pytest.raises(products.BadCatalogue) as caught:
        products.mandatePreview('mandates.csv', _upload(path))
    assert 'missing column(s) BaseCurrency, Category' in str(caught.value)
    with pytest.raises(ValidationError):
        products.mandatePreview('mandates.txt', 'eA==')


def test_an_export_loads_back_with_nothing_to_do_and_a_workbook_imports_too(store, tmp_path):
    from openpyxl import Workbook
    path = _file(tmp_path, _equityRows())
    products.importMandates('mandates.csv', _upload(path), user='bob')
    rows = products.mandateRows()
    assert rows[0] == products.MANDATE_COLUMNS and len(rows) == 5
    assert [EQUITY, 'PMG-CG-USD-H', 'USD', 'Hedged', 'Public Equity', '0.45', '1000000', ''] in rows
    assert [BOND, 'MUNI-USD', 'USD', '', 'Investment Grade Fixed Income', '', '', ''] in rows
    assert [r[0] for r in rows[1:]] == sorted((r[0] for r in rows[1:]),
                                               key=[p['productId'] for p in products.all()].index), 'catalogue order'
    again = _file(tmp_path, rows, name='again.csv')
    preview = products.mandatePreview('again.csv', _upload(again))
    assert preview['touched'] == 0 and preview['same'] == 2
    book = Workbook()
    sheet = book.active
    sheet.title = 'mandates'
    for row in rows:
        sheet.append(row)
    at = next(n for n, r in enumerate(rows, start=1) if r[1] == 'PMG-CG-USD-H')
    sheet.cell(row=at, column=6).value = 0.5
    xlsx = tmp_path / 'mandates.xlsx'
    book.save(str(xlsx))
    preview = products.mandatePreview('mandates.xlsx', _upload(str(xlsx)))
    equity = next(p for p in preview['products'] if p['productId'] == EQUITY)
    assert equity['changed'] == ['PMG-CG-USD-H'] and preview['touched'] == 1


def test_a_product_file_keeps_the_mandates_and_cannot_leave_them_wrong(store, tmp_path):
    _save(EQUITY, [_m('PMG-CG-USD-H', 'USD', 'Hedged')])
    rows = products.exportRows()
    head = rows[0]
    path = _file(tmp_path, rows, name='products.csv')
    preview = products.importPreview('products.csv', _upload(path))
    assert preview['changed'] == [] and preview['unchanged'] == 73
    at = next(n for n, r in enumerate(rows) if r[0] == EQUITY)
    rows[at][head.index('Category')] = 'Hedge Funds'
    path = _file(tmp_path, rows, name='products.csv')
    with pytest.raises(products.BadCatalogue) as caught:
        products.importPreview('products.csv', _upload(path))
    assert 'keeps its SMA mandates' in str(caught.value) and 'leave the hedging' in str(caught.value)


# ---------------------------------------------------------- the resolution ---

def test_a_scenario_finds_the_mandate_of_its_currency_and_version(store):
    _save(EQUITY, [_m('PMG-CG-USD-H', 'USD', 'Hedged'), _m('PMG-CG-USD-U', 'USD', 'Unhedged'),
                   _m('PMG-CG-USD-I', 'USD', 'ISG Hedged'), _m('PMG-CG-GBP-H', 'GBP', 'Hedged')])
    _save(BOND, [_m('MUNI-USD', 'USD')])
    equity, bond = products.get(EQUITY), products.get(BOND)
    pick = products.mandateFor
    assert pick(equity, 'USD', 'Hedged')['code'] == 'PMG-CG-USD-H'
    assert pick(equity, 'USD', 'ISG Hedged')['code'] == 'PMG-CG-USD-I'
    assert pick(equity, 'USD', 'Unhedged')['code'] == 'PMG-CG-USD-U'
    assert pick(equity, 'USD', 'Equity Not Hedged')['code'] == 'PMG-CG-USD-U', 'equity left unhedged'
    assert pick(equity, 'GBP', 'Unhedged') is None, 'no unhedged pound mandate yet'
    assert pick(equity, 'USD', 'Hedged', version='Unhedged')['code'] == 'PMG-CG-USD-U', 'a sleeve may fix one'
    for hedging in ('Hedged', 'ISG Hedged', 'Unhedged', 'Equity Not Hedged'):
        assert pick(bond, 'USD', hedging)['code'] == 'MUNI-USD', 'a bond SMA has no versions'
    assert pick(bond, 'GBP', 'Hedged') is None and pick(products.get(FUND), 'USD', 'Hedged') is None


# ------------------------------------------- the endpoints and the command ---

def test_the_endpoints_preview_import_and_export_as_the_signed_in_admin(store, tmp_path):
    alice = _caller('alice')
    path = _file(tmp_path, _equityRows())
    preview = dashboardRouter.previewMandateImport({'name': 'mandates.csv', 'content': _upload(path)},
                                                   caller=alice)
    assert preview['added'] == 4
    done = dashboardRouter.importMandates({'name': 'mandates.csv', 'content': _upload(path),
                                           'base': preview['revision']}, caller=alice)
    assert done['import']['added'] == 4 and done['catalogue']['lastEditedBy'] == 'alice'
    body = dashboardRouter.exportMandates(caller=alice).body.decode('utf-8')
    assert body.splitlines()[0] == ','.join(products.MANDATE_COLUMNS) and 'PMG-CG-GBP-I' in body
    bad = dashboardRouter.previewMandateImport({'name': 'x.csv', 'content': _upload(_file(
        tmp_path, [list(products.MANDATE_COLUMNS), _row(FUND, 'X', 'USD', '', 'Public Equity')]))},
        caller=alice)
    assert bad.status_code == 422 and b'not a separate account' in bad.body
    stale = dashboardRouter.importMandates({'name': 'mandates.csv', 'content': _upload(path),
                                            'base': preview['revision']}, caller=alice)
    assert stale.status_code == 409


def test_the_command_line_exports_and_imports_the_mandate_file(store, tmp_path, capsys):
    path = _file(tmp_path, _equityRows())
    capsys.readouterr()
    assert sleeveTools.main(['--mandates-import', path, '--note', 'from the desk']) == 0
    said = capsys.readouterr().out
    assert '4 mandate(s) new, 0 changed' in said and 'PMG-CG-GBP-I' in said
    assert 'the catalogue is now version 2' in said
    out = tmp_path / 'out.csv'
    assert sleeveTools.main(['--mandates-export', str(out)]) == 0
    rows = list(csv.reader(open(str(out), encoding='utf-8')))
    assert rows[0] == products.MANDATE_COLUMNS and len(rows) == 5
    assert sleeveTools.main(['--mandates-import', str(out)]) == 1
    assert 'nothing was written' in capsys.readouterr().out
