"""A product's listings (D207, D208, D214): the ways it can be bought - a
share class of a fund, a mandate code of a separate account - each with its
own ticker, issue currency, hedging and figures, as facts only. They are kept
in an order the console sets by dragging them, and a proposal holds the
first, whatever its currency or its amount; nothing is ever "not offered".
The file gives a product a row per listing in their order, reads no Rank
columns, and still reads the layout before listings, a row per base
currency; a store from before listings is listed as it read, without the
rankings it once kept."""
import base64
import csv
import json
import sqlite3

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import products, proposalRegister, rules, sleeveRepo, sleeves
from cyrus_pmg.pmgService.scenario.fixturesAdapter import FixturesScenarioPort
from cyrus_pmg.pmgService.scenario.types import BasisInput, PortfolioKey, ValidationError
from cyrus_pmg.pmgService.scenario.workbook import IMPL_COLUMNS, buildImplementationRows, implColumns

MA = sleeves.VARIANTS[0]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path / 'store'))
    products.reload()
    yield tmp_path
    products.reload()


def _caller(kerberos='alice'):
    from cyrus_pmg.pmgService.core import accessControl
    return accessControl.UserData(kerberos, accessControl._rolesFor(kerberos))


def _held():
    """A fund an ordinary Multi-Asset sleeve holds - chosen, not attached by
    an overlay, and offered for every portfolio - with the sleeve's category
    and name."""
    for entry in sleeveRepo.listAll(MA):
        if (entry['products'] and not entry.get('problems') and not entry.get('label')
                and entry['category'] not in rules.AUTO_SLEEVE_CATEGORIES
                and entry['category'] == rules.sleeveCategory(entry['category'])):
            for row in entry['products']:
                if not products.isSma(products.get(row['productId'])):
                    return row['productId'], entry['category'], entry['name']
    raise AssertionError('no sleeve holds a fund')


def _main(pid):
    entry = products.entry(pid)
    return {k: entry['listings'][0][k] for k in products.LISTING_FIELDS}


def _list(pid, *listings, **over):
    """Save *pid* with *listings*, each its main listing's values but those
    given."""
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


def _rowsFor(pid, listings, base=False):
    """The export with *pid*'s one row replaced by one per entry of
    *listings*: [{column: value}], the rest of each row as the export wrote
    it - or, with *base*, in the layout before listings, each entry naming
    its BaseCurrency."""
    rows = products.exportRows()
    head = rows[0]
    at = next(n for n, r in enumerate(rows) if r[0] == pid)
    if base:
        head2 = head + ['BaseCurrency']
        lines = []
        for columns in listings:
            line = rows[at] + ['']
            for column, value in columns.items():
                line[head2.index(column)] = value
            lines.append(line)
        return [head2] + [r + [''] for r in rows[1:at]] + lines + [r + [''] for r in rows[at + 1:]]
    lines = []
    for columns in listings:
        line = list(rows[at])
        for column, value in columns.items():
            line[head.index(column)] = value
        lines.append(line)
    return rows[:at] + lines + rows[at + 1:]


def _model(port, currency, category, name, mandate=5e7):
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
        result, chosen, rules.AUTO_SLEEVE_CATEGORIES, mandate, MA, False, None, None, mandate, False, currency)


def _line(model, pid):
    return next(i for g in model['groups'] for i in g['items'] if i['productId'] == pid)


# ---------------------------------------------------------------- the file ---

def test_a_file_gives_a_product_a_row_per_listing_in_its_order(store, tmp_path):
    pid, _, _ = _held()
    rows = _rowsFor(pid, [
        {'Ticker': 'GEF-USD', 'IssueCurrency': 'USD', 'Hedging': 'Unhedged'},
        {'Ticker': 'GEF-USD', 'IssueCurrency': 'GBP', 'Hedging': 'Unhedged'},
        {'Ticker': 'GEF-GBPH', 'IssueCurrency': 'GBP', 'Hedging': 'Hedged', 'MinimumInvestment': '1000000'}])
    byId, order = products.readFile(_file(tmp_path, rows))
    record = byId[pid]
    assert [(l['ticker'], l['issueCurrency'], l['hedging']) for l in record['listings']] == [
        ('GEF-USD', 'USD', 'Unhedged'), ('GEF-USD', 'GBP', 'Unhedged'), ('GEF-GBPH', 'GBP', 'Hedged')]
    assert not any('ranks' in l for l in record['listings']), 'a listing is facts only (D214)'
    # the product carries its first listing's values at the top
    assert (record['ticker'], record['issueCurrency']) == ('GEF-USD', 'USD')
    assert len(order) == len(set(order)), 'a product is one product, whatever its rows'


def test_rank_columns_are_not_read(store, tmp_path):
    """A file kept while listings were ranked (D207) still loads: its Rank
    columns are not read, the rows' order is the listings' order, and the
    preview says so (D214)."""
    pid, _, _ = _held()
    rows = _rowsFor(pid, [{'Ticker': 'A1', 'IssueCurrency': 'USD'}, {'Ticker': 'A2', 'IssueCurrency': 'GBP'}])
    rows[0] += ['Rank USD', 'Rank GBP']
    for r in rows[1:]:
        r += ['', '1' if r[0] == pid and 'A2' in r else 'first']
    path = _file(tmp_path, rows)
    byId, _ = products.readFile(path)
    assert [l['ticker'] for l in byId[pid]['listings']] == ['A1', 'A2']
    preview = products.importPreview(path, _upload(path))
    assert preview['ranksIgnored']
    # and beside a BaseCurrency column, as the layout before listings
    rows = _rowsFor(pid, [{'BaseCurrency': 'All'}], base=True)
    rows[0].append('Rank GBP')
    for r in rows[1:]:
        r.append('2')
    assert pid in products.readFile(_file(tmp_path, rows, 'old.csv'))[0]


@pytest.mark.parametrize('rows, words', [
    ([{'Ticker': 'A1'}, {'Ticker': 'A1'}], 'same listing'),
    ([{'Ticker': 'A1'}, {'Ticker': 'A2', 'Style': 'Passive2'}], 'only a listing'),
    ([{'Ticker': 'A1', 'Hedging': 'ISG Hedged'}], 'separate account'),
    ([{'Ticker': 'A1', 'MandateCode': 'M1'}], 'Only a separate account'),
])
def test_rows_that_cannot_be_one_product_are_refused_naming_the_row(store, tmp_path, rows, words):
    pid, _, _ = _held()
    with pytest.raises(products.BadCatalogue, match=words):
        products.readFile(_file(tmp_path, _rowsFor(pid, rows)))


def test_a_ticker_repeats_within_a_product_but_not_across_two(store, tmp_path):
    pid, _, _ = _held()
    other = next(p['productId'] for p in products.all() if p['productId'] != pid
                 and not products.isSma(p))
    rows = _rowsFor(pid, [{'Ticker': 'ISIN1', 'IssueCurrency': 'USD'}, {'Ticker': 'ISIN1', 'IssueCurrency': 'GBP'}])
    assert len(products.readFile(_file(tmp_path, rows))[0][pid]['listings']) == 2
    head = rows[0]
    at = next(n for n, r in enumerate(rows) if r[0] == other)
    rows[at][head.index('Ticker')] = 'ISIN1'
    with pytest.raises(products.BadCatalogue, match='ticker ISIN1 is on'):
        products.readFile(_file(tmp_path, rows, 'two.csv'))
    # and a save gives another product's ticker no more than a file does
    _list(pid, {'ticker': 'ISIN9'})
    entry = products.entry(other)
    fields = {k: entry[k] for k in products.KEYS}
    fields['ticker'] = 'ISIN9'
    with pytest.raises(ValidationError, match='belongs to') as refused:
        products.update(other, fields, user='alice', base=entry['revision'])
    assert refused.value.field == 'listing0ticker'


def test_the_layout_before_listings_is_read_a_currency_row_a_listing(store, tmp_path):
    """D182's file: an All row, and a row per currency with values of its
    own - each read as a listing after the All row's, the name's currency
    taken off, a blank word the All row's, two alike one listing."""
    pid, _, _ = _held()
    name = products.get(pid)['name']
    rows = _rowsFor(pid, [
        {'BaseCurrency': 'All', 'Ticker': 'MAIN'},
        {'BaseCurrency': 'GBP', 'Ticker': 'GBX', 'Name': name + ' (GBP)', 'ExposureCurrency': '',
         'IssueCurrency': 'GBP'},
        {'BaseCurrency': 'CHF', 'Ticker': 'GBX', 'Name': name + ' (CHF)', 'ExposureCurrency': '',
         'IssueCurrency': 'GBP'},
        {'BaseCurrency': 'EUR', 'Ticker': 'MAIN', 'MinimumInvestment': '250000'}], base=True)
    found = set()
    byId, _ = products._load(_file(tmp_path, rows), strict=False, found=found)
    listed = byId[pid]['listings']
    assert 'BaseCurrency' in found
    assert [(l['ticker'], l['issueCurrency']) for l in listed] == [
        ('MAIN', products.get(pid)['issueCurrency'] or ''), ('GBX', 'GBP'), ('MAIN', 'EUR')]
    assert listed[1]['exposureCurrency'] == listed[0]['exposureCurrency'], 'a blank word is the main row\'s'
    assert listed[2]['minimumInvestment'] == 250000.0
    assert byId[pid]['name'] == name


def test_an_export_loads_back_with_nothing_to_do(store, tmp_path):
    pid, _, _ = _held()
    _list(pid, {}, {'ticker': 'GBX', 'issueCurrency': 'GBP', 'hedging': 'Hedged'})
    rows = products.exportRows()
    assert rows[0] == products.EXPORT_COLUMNS
    assert not [c for c in rows[0] if c.startswith('Rank')]
    mine = [r for r in rows if r[0] == pid]
    assert len(mine) == 2 and mine[1][rows[0].index('Hedging')] == 'Hedged'
    path = _file(tmp_path, rows)
    preview = products.importPreview(path, _upload(path))
    assert not preview['added'] and not preview['changed'] and not preview['restored']
    assert not preview['legacy'] and not preview['kept'] and not preview['ranksIgnored']


def test_a_file_without_a_column_keeps_what_the_catalogue_holds_of_it(store, tmp_path):
    """No Benchmark or Hedging column: each listing keeps its own, matched
    to the one it is (D204, D207)."""
    pid, _, _ = _held()
    _list(pid, {'benchmark': 'Bench A'},
          {'ticker': 'GBX', 'issueCurrency': 'GBP', 'hedging': 'Hedged', 'benchmark': 'Bench B'})
    rows = products.exportRows()
    head = rows[0]
    keep = [n for n, c in enumerate(head) if c not in ('Benchmark', 'Hedging')]
    trimmed = [[r[n] for n in keep] for r in rows]
    path = _file(tmp_path, trimmed)
    preview = products.importPreview(path, _upload(path))
    assert not preview['changed'], preview['changed']


def test_a_file_without_mandate_codes_leaves_an_smas_coded_listings_whole(store, tmp_path):
    sma = next(p for p in products.all() if products.isSma(p) and p['ownCategory'] != products.EQUITY_CATEGORY)
    pid = sma['productId']
    _list(pid, {'code': 'C-USD', 'issueCurrency': 'USD'}, {'code': 'C-GBP', 'issueCurrency': 'GBP'})
    rows = products.exportRows()
    head = rows[0]
    keep = [n for n, c in enumerate(head) if c != 'MandateCode']
    # the file's one row for the SMA, its name changed
    lines = [[r[n] for n in keep] for r in rows if r[0] != pid]
    first = next(r for r in rows if r[0] == pid)
    first = [first[n] for n in keep]
    first[[head[n] for n in keep].index('Name')] = 'Renamed SMA'
    path = _file(tmp_path, lines[:1] + [first] + lines[1:])
    preview = products.importPreview(path, _upload(path))
    assert [k['productId'] for k in preview['kept']] == [pid]
    products.importFile(path, _upload(path), user='alice', base=preview['revision'])
    after = products.entry(pid)
    assert after['name'] == 'Renamed SMA'
    assert [l['code'] for l in after['listings']] == ['C-USD', 'C-GBP']


# -------------------------------------------------------------- the checks ---

def test_a_listing_says_its_hedging_as_its_kind_may(store):
    pid, _, _ = _held()
    assert _list(pid, {'hedging': 'Hedged'})['listings'][0]['hedging'] == 'Hedged'
    with pytest.raises(ValidationError, match='Hedged or Unhedged') as refused:
        _list(pid, {'hedging': 'ISG Hedged'})
    assert refused.value.field == 'listing0hedging'
    equity = next(p for p in products.all() if products.isSma(p) and p['ownCategory'] == products.EQUITY_CATEGORY)
    out = _list(equity['productId'], {'code': 'EQ-1', 'hedging': 'ISG Hedged'})
    assert out['listings'][0]['hedging'] == 'ISG Hedged'
    bond = next(p for p in products.all() if products.isSma(p) and p['ownCategory'] != products.EQUITY_CATEGORY)
    with pytest.raises(ValidationError, match='Only a Public Equity SMA'):
        _list(bond['productId'], {'hedging': 'Hedged'})


def test_a_mandate_code_is_an_smas_and_one_products_withdrawn_or_not(store):
    pid, _, _ = _held()
    with pytest.raises(ValidationError, match='Only a separate account') as refused:
        _list(pid, {'code': 'X1'})
    assert refused.value.field == 'listing0code'
    smas = [p['productId'] for p in products.all() if products.isSma(p)
            and p['ownCategory'] != products.EQUITY_CATEGORY]
    _list(smas[0], {'code': 'M34', 'issueCurrency': 'USD'}, {'code': 'M34', 'issueCurrency': 'EUR'},
          {'code': 'M34', 'issueCurrency': 'GBP'})
    assert [l['code'] for l in products.entry(smas[0])['listings']] == ['M34'] * 3, 'one code, three currencies'
    with pytest.raises(ValidationError, match='belongs to'):
        _list(smas[1], {'code': 'm34'})


def test_a_save_that_says_nothing_of_the_listings_keeps_them(store):
    pid, _, _ = _held()
    _list(pid, {}, {'ticker': 'GBX', 'issueCurrency': 'GBP'})
    entry = products.entry(pid)
    fields = {k: entry[k] for k in products.KEYS}
    fields['productCost'] = 0.77
    out = products.update(pid, fields, user='alice', base=entry['revision'])
    assert [l['ticker'] for l in out['listings']] == [entry['listings'][0]['ticker'], 'GBX']
    assert out['listings'][0]['productCost'] == 0.77 and out['productCost'] == 0.77
    assert out['listings'][1]['productCost'] == entry['listings'][1]['productCost']


def test_ranks_given_with_a_listing_are_not_kept(store):
    pid, _, _ = _held()
    out = _list(pid, {'ranks': {'GBP': 5}}, {'ticker': 'GBX', 'issueCurrency': 'GBP', 'ranks': {'GBP': 1}})
    assert [l['ticker'] for l in out['listings']][1] == 'GBX', 'the order given, not a ranking'
    assert not any('ranks' in l for l in out['listings'])


def test_the_listings_are_put_in_a_new_order_and_the_first_is_held(store):
    """The card's drag (D214): a new version with the listings in the order
    asked, the first's values at the top, and nothing else moved."""
    pid, _, _ = _held()
    first = _main(pid)['ticker'] or products.NO_TICKER
    _list(pid, {}, {'ticker': 'GBX', 'issueCurrency': 'GBP', 'hedging': 'Hedged', 'productCost': 0.31},
          {'ticker': 'EUX', 'issueCurrency': 'EUR'})
    entry = products.entry(pid)
    out = products.reorder(pid, [1, 2, 0], user='alice', base=entry['revision'])
    assert [l['ticker'] for l in out['listings']][:2] == ['GBX', 'EUX']
    assert (out['ticker'], out['issueCurrency'], out['hedging'], out['productCost']) == ('GBX', 'GBP', 'Hedged', 0.31)
    assert out['revision'] == entry['revision'] + 1
    assert products.asHeld(products.get(pid))['ticker'] == 'GBX'
    words = products.history(pid)[0]['changeWords']
    assert words == ['Listing order {} → GBX, then EUX, then {}'.format(
        ', then '.join([first, 'GBX', 'EUX']), first)], words
    for order, said in (([0, 1, 2], 'already'), ([0, 1], 'once'), ([0, 0, 1], 'once'), ([2, 1, True], 'once'),
                        ('2,1,0', 'once')):
        with pytest.raises(ValidationError, match='order the listings are in' if said == 'already' else said):
            products.reorder(pid, order, user='alice', base=out['revision'])
    with pytest.raises(products.StaleError):
        products.reorder(pid, [1, 0, 2], user='alice', base=entry['revision'])
    # the route says the same
    body = dashboardRouter.reorderRepositoryProduct({'productId': pid, 'order': [2, 1, 0], 'base': out['revision']},
                                                    caller=_caller())
    assert [l['ticker'] for l in body['product']['listings']][-1] == 'GBX'


def test_the_history_names_listings_and_their_order(store):
    pid, _, _ = _held()
    before = products.entry(pid)['listings'][0]['ticker'] or '—'
    _list(pid, {}, {'ticker': 'GBX', 'issueCurrency': 'GBP', 'hedging': 'Hedged'})
    words = products.history(pid)[0]['changeWords']
    assert any(w.startswith('Listing GBX — → GBP · Hedged') for w in words), words
    assert not [w for w in words if w.startswith('Listing order')], 'one added at the end moves nothing'
    _list(pid, {'productCost': 0.61}, {'ticker': 'GBX', 'issueCurrency': 'GBP', 'hedging': 'Hedged'})
    words = products.history(pid)[0]['changeWords']
    assert any(w.startswith('Product cost of {} '.format(before)) for w in words), words
    # one put first is the one held: the order is said
    main = _main(pid)
    _list(pid, {'ticker': 'GBX', 'issueCurrency': 'GBP', 'hedging': 'Hedged'}, main)
    words = products.history(pid)[0]['changeWords']
    assert 'Listing order {}, then GBX → GBX, then {}'.format(before, before) in words, words


# -------------------------------------------------------- what is held ---

def test_a_proposal_holds_the_first_listing_whatever_its_currency(store):
    pid, _, _ = _held()
    _list(pid, {'ticker': 'MAIN'}, {'ticker': 'GBX', 'issueCurrency': 'GBP'},
          {'ticker': 'GBY', 'issueCurrency': 'GBP', 'hedging': 'Hedged'})
    held = products.asHeld(products.get(pid))
    assert (held['ticker'], held['listing']) == ('MAIN', 'MAIN')
    assert 'ranked' not in held and 'offered' not in held
    assert not hasattr(products, 'inCurrency') and not hasattr(products, 'pickListing')


def test_the_library_a_proposal_is_served_holds_the_first_listing(store):
    pid, category, name = _held()
    _list(pid, {'ticker': 'MAIN'}, {'ticker': 'GBX', 'issueCurrency': 'GBP', 'hedging': 'Hedged'})

    def served(currency):
        body = dashboardRouter.listSleeves(category, MA, currency=currency)
        sleeve = next(s for s in body['sleeves'] if s['name'] == name)
        return next(p for p in sleeve['products'] if p['productId'] == pid)
    assert served('GBP')['ticker'] == served('CHF')['ticker'] == 'MAIN'
    products.reorder(pid, [1, 0], user='alice', base=products.entry(pid)['revision'])
    gbp, usd = served('GBP'), served('USD')
    assert (gbp['ticker'], gbp['hedging'], gbp['issueCurrency']) == ('GBX', 'Hedged', 'GBP')
    assert usd['ticker'] == 'GBX', 'the first listing, in any currency'


def test_below_the_first_listings_minimum_a_line_does_not_step_down(store):
    """D214: the line holds the first listing whatever its amount - one
    after it with a smaller minimum plays no part - and is flagged below its
    minimum, as before."""
    port = FixturesScenarioPort()
    pid, category, name = _held()
    _list(pid, {'ticker': 'BIG', 'minimumInvestment': 1e12},
          {'ticker': 'SMALL', 'issueCurrency': 'GBP', 'hedging': 'Hedged', 'minimumInvestment': 1000,
           'productCost': 0.9})
    _, _, gbp = _model(port, 'GBP', category, name)
    line = _line(gbp, pid)
    assert line['ticker'] == 'BIG' and line['belowMinimum'] and 'steppedFrom' not in line
    products.reorder(pid, [1, 0], user='alice', base=products.entry(pid)['revision'])
    _, _, gbp = _model(port, 'GBP', category, name)
    line = _line(gbp, pid)
    assert (line['ticker'], line['hedging'], line['productCost']) == ('SMALL', 'Hedged', 0.9)
    assert not line['belowMinimum']


def test_the_model_has_nothing_not_offered_and_the_sheet_shows_the_listing(store):
    port = FixturesScenarioPort()
    pid, category, name = _held()
    _list(pid, {'ticker': 'GBX', 'issueCurrency': 'GBP', 'hedging': 'Hedged'})
    _, _, chf = _model(port, 'CHF', category, name)
    assert 'unavailable' not in chf
    line = _line(chf, pid)
    assert (line['issueCurrency'], line['hedging']) == ('GBP', 'Hedged')
    # the workbook's sheet names them beside the share class; the deck does not
    assert IMPL_COLUMNS.index('Issue Currency') == IMPL_COLUMNS.index('Share Class') + 1
    assert IMPL_COLUMNS.index('Hedging') == IMPL_COLUMNS.index('Issue Currency') + 1
    assert 'Issue Currency' in implColumns(True) and 'Hedging' in implColumns(False)
    assert 'Issue Currency' not in implColumns(True, deck=True)
    assert 'Hedging' not in implColumns(False, deck=True)


def test_the_register_records_the_listing_a_line_held(store):
    fields = proposalRegister._ITEM_FIELDS
    for field in ('issueCurrency', 'hedging', 'code', 'listing', 'steppedFrom'):
        assert field in fields


def test_the_mandate_file_and_its_routes_are_gone(store):
    paths = {r.path for r in dashboardRouter.router.routes}
    assert not [p for p in paths if '/mandates' in p and 'repository' in p]
    assert not hasattr(products, 'mandatePreview') and not hasattr(products, 'offeredIn')


# --------------------------------------------- a store from before them ---

def _legacyStore(path):
    """A sleeve store of schema 16 with a product's values by currency and
    an SMA's mandates - the shape the move to listings reads."""
    conn = sleeveRepo.connection()
    conn.close()
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    return raw


def test_a_store_from_before_listings_is_listed_as_it_read(store):
    pid, _, _ = _held()
    sma = next(p for p in products.all() if products.isSma(p) and p['ownCategory'] == products.EQUITY_CATEGORY)
    raw = _legacyStore(store / 'sleeves.db')
    with raw:
        raw.execute("UPDATE products SET listings = '', currencies = ?, ticker = 'MAIN' WHERE productId = ?",
                    (json.dumps({'GBP': {'ticker': 'GBX', 'minimumInvestment': 750000.0, 'offered': True},
                                 'CHF': {'offered': False}}), pid))
        raw.execute("UPDATE products SET listings = '', mandates = ? WHERE productId = ?", (json.dumps([
            {'code': 'EQ-U', 'currency': 'USD', 'hedging': 'Unhedged', 'productCost': None,
             'minimumInvestment': None, 'distributionYield': None, 'benchmark': ''},
            {'code': 'EQ-H', 'currency': 'USD', 'hedging': 'Hedged', 'productCost': 0.7,
             'minimumInvestment': None, 'distributionYield': None, 'benchmark': 'MSCI H'}]), sma['productId']))
        revision = int(raw.execute("SELECT value FROM meta WHERE key = 'catalogueRevision'").fetchone()[0])
        # as a store of schema 16 had it: no listings column at all
        raw.execute('ALTER TABLE products DROP COLUMN listings')
        raw.execute("UPDATE meta SET value = '16' WHERE key = 'schemaVersion'")
    raw.close()
    products.reload()
    # read before the move, through a read-only connection: listed as it reads
    early = products.get(pid)
    assert [l['ticker'] for l in early['listings']] == ['MAIN', 'GBX']
    assert not any('ranks' in l for l in early['listings'])
    # the move: the column, each product with more than its main values listed
    conn = sleeveRepo.connection()
    try:
        assert int(conn.execute("SELECT value FROM meta WHERE key = 'schemaVersion'").fetchone()[0]) == 17
        stored = conn.execute('SELECT listings, currencies, ticker FROM products WHERE productId = ?', (pid,)).fetchone()
        assert json.loads(stored['listings'])[1]['ticker'] == 'GBX'
        # what it was listed from is left where it was, for the code before listings
        assert json.loads(stored['currencies'])['GBP']['ticker'] == 'GBX' and stored['ticker'] == 'MAIN'
        assert int(conn.execute("SELECT value FROM meta WHERE key = 'catalogueRevision'").fetchone()[0]) == revision + 1
    finally:
        conn.close()
    products.reload()
    equity = products.entry(sma['productId'])
    assert [(l['code'], l['hedging']) for l in equity['listings']] == [('EQ-H', 'Hedged'), ('EQ-U', 'Unhedged')]
    assert equity['listings'][0]['productCost'] == 0.7 and equity['listings'][0]['benchmark'] == 'MSCI H'
    assert equity['listings'][1]['productCost'] == sma['productCost'], 'a figure the mandate left blank is the product\'s'
    # an older version reads as it is listed, so the history shows no move
    assert all(not h['changeWords'] or h['action'] != 'seeded' for h in products.history(pid))
    # the next save writes the listings alone
    _list(pid, {'ticker': 'MAIN'}, {'ticker': 'GBX', 'issueCurrency': 'GBP', 'minimumInvestment': 750000.0},
          {'ticker': 'GBY', 'issueCurrency': 'EUR'})
    conn = sleeveRepo.connection()
    try:
        assert conn.execute('SELECT currencies FROM products WHERE productId = ?', (pid,)).fetchone()[0] == ''
    finally:
        conn.close()


def test_the_vocabulary_offers_every_listings_words(store):
    pid, _, _ = _held()
    _list(pid, {}, {'ticker': 'GBX', 'issueCurrency': 'GBP', 'assetClass': 'A Brand New Class',
                    'benchmark': 'A New Index'})
    vocab = products.vocabulary()
    assert 'A Brand New Class' in vocab['assetClass'] and 'A New Index' in vocab['benchmark']
    assert 'GBP' in vocab['issueCurrency']
    assert vocab['hedging'] == {'fund': ['Hedged', 'Unhedged'], 'sma': ['Hedged', 'ISG Hedged', 'Unhedged']}


# --------------------------------------------------- the catalogue line ---

def test_the_catalogue_line_reads_the_first_listing():
    """D214: a line is its product at its first listing, the one a proposal
    holds - with a count, the cells its listings differ in, a search that
    names another listing, and a flag when the first listing is above the
    open mandate. There is no View as and no ranking."""
    import os
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'generator', 'js', 'repository.js')
    with open(path, encoding='utf-8') as fh:
        source = fh.read()
    helpers = source[source.index('/* catalogue-helpers-begin'):source.index('/* catalogue-helpers-end */')]
    edited = source[source.index('var CAT_EDITED'):source.index('/* product-editor-helpers-end */')]
    script = helpers + edited + '''
var L = function (t, i, h, min) { return { ticker: t, code: '', issueCurrency: i, hedging: h, shareClass: 'Accumulating',
  exposureCurrency: i, assetClass: 'Equity', productCost: 0.5, distributionYield: null, minimumInvestment: min,
  benchmark: '' }; };
var gef = { productId: 'gef', name: 'GEF', ticker: 'GEF-USD', vehicle: 'UCITS', liquidity: 'Daily', category: 'Equity',
  listings: [L('GEF-USD', 'USD', 'Unhedged', 3000000), L('GEF-USD', 'GBP', 'Unhedged', 1000),
             L('GEF-GBPH', 'GBP', 'Hedged', 1000000), L('GEF-EURH', 'EUR', 'Hedged', 3000000)] };
var one = { productId: 'one', name: 'One', ticker: 'ONE', vehicle: 'ETF', liquidity: 'Daily', category: 'Equity',
  listings: [L('ONE', 'USD', '', 5000)] };
var out = {};
out.rows = catEnrich([gef, one], [], 2000000).map(function (r) {
  return { t: r.v.ticker, tooBig: r.tooBig, count: r.count, varies: Object.keys(r.varies).sort(),
           keys: Object.keys(r).sort() };
});
var row = catEnrich([gef, one], [], 2000000)[0];
var hit = catMatched(row, 'gef-gbph', 2000000);
out.matched = [hit.v.ticker, hit.matched, hit.tooBig, catMatched(row, 'gef-usd', null).matched];
out.values = [catValuesOf(row, 'hedging'), catValuesOf(row, 'issueCurrency'), catValuesOf(row, 'listings')];
out.search = catFilter(catEnrich([gef, one], [], null), { query: 'gef-eurh', filters: {} }).map(function (r) { return r.p.productId; });
process.stdout.write(JSON.stringify(out));
'''
    got = json.loads(subprocess.run([node, '-e', script], capture_output=True, text=True, check=True).stdout)
    gefRow, oneRow = got['rows']
    assert (gefRow['t'], oneRow['t']) == ('GEF-USD', 'ONE')
    assert gefRow['tooBig'], 'the first listing needs more than the mandate, whatever the others take'
    assert not oneRow['tooBig'] and oneRow['count'] == 1 and oneRow['varies'] == []
    assert gefRow['varies'] == ['exposureCurrency', 'hedging', 'issueCurrency', 'minimumInvestment', 'ticker']
    assert gefRow['keys'] == ['books', 'categories', 'count', 'listing', 'matched', 'net', 'p', 'tooBig', 'used',
                              'v', 'varies']
    assert got['matched'] == ['GEF-GBPH', True, True, False], 'a matched listing shows; the flag is still the first\'s'
    assert got['values'] == [['Unhedged', 'Hedged'], ['USD', 'GBP', 'EUR'], ['Several listings']]
    assert got['search'] == ['gef']
    assert "key: 'unranked'" not in source and 'data-catview' not in source, 'no Unranked in filter, no View as'
