"""D205, D211: the mandate codes - a code for each portfolio a PWA can be
offered, for the mandate's own row of the account opening request's SMA
Details. Its benchmark is the portfolio benchmarks' (D211).

A portfolio is an implementation type, a currency, a risk level, an
allocation (with its form without real assets, all equity and custom) and a
hedging policy. The table is loaded from a long file, where All stands for
every one and the row naming more wins, or edited on screen; a portfolio on
offer with no code is a gap.
"""

import base64
import csv

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import mandateCodes, repositoryFeed, sleeveRepo
from cyrus_pmg.pmgService.scenario.types import ValidationError
from test_proposal_register import _caller

MA = 'PMG Multi-Asset Portfolio'
ESG = 'PMG ESG'


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path / 'store'))
    sleeveRepo.describe()
    yield tmp_path


def _upload(tmp_path, rows, name='codes.csv'):
    path = tmp_path / name
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        csv.writer(fh).writerows(rows)
    return base64.b64encode(path.read_bytes()).decode('ascii')


HEAD = list(mandateCodes.COLUMNS)


def test_the_portfolios_on_offer_are_each_types_own(store):
    forms = {t['type']: [f['form'] for f in t['forms']] for t in mandateCodes.layout()}
    assert forms[MA] == ['Full', 'Full ex-RA', 'Core', 'ex-Alts', 'ex-HFs', 'ex-HFs ex-RA', 'All Equity', 'Custom']
    assert forms[ESG] == ['ex-Alts', 'ex-HFs ex-RA', 'All Equity', 'Custom'], 'ESG leaves real assets out'
    assert forms['US Onshore'] == ['ex-Alts', 'All Equity', 'Custom']
    cells = mandateCodes.cells()
    assert len(cells) == len(set(cells)) == (51 + 23 + 16 + 16) * 4 * 4
    assert mandateCodes.cell(MA, 'Full', 'Moderate', 'USD', 'Hedged') in cells
    assert mandateCodes.cell(ESG, 'ex-HFs', 'Moderate', 'USD', 'Hedged') not in cells
    # a proposal finds its cell by its key, or a custom portfolio by its profile
    assert mandateCodes.keyOf(MA, 'USD|Moderate|Full|1', hedging='ISG Hedged') == \
        'PMG Multi-Asset Portfolio|Full ex-RA|Moderate|USD|ISG Hedged'
    assert mandateCodes.keyOf(MA, 'GBP|All Equity|NA|NA', hedging='Unhedged') == \
        'PMG Multi-Asset Portfolio|All Equity|All Equity|GBP|Unhedged'
    assert mandateCodes.keyOf(ESG, profile='ModAgg', currency='CHF', hedging='Hedged') == \
        'PMG ESG|Custom|ModAgg|CHF|Hedged'
    # nothing yet: every portfolio a gap
    assert mandateCodes.current()['revision'] == 0
    assert len(mandateCodes.gaps()['noCode']) == len(cells)


def test_a_file_with_all_fills_every_portfolio_and_the_row_naming_more_wins(store, tmp_path):
    rows = [HEAD,
            ['All', 'All', 'All', 'All', 'All', 'PMG-ANY'],
            [MA, 'USD', 'All', 'Full', 'All', 'PMG-MA-USD-FULL'],
            [MA, 'usd', 'Moderate-Aggressive', 'full', 'ISG Hedged', 'PMG-MA-USD-MA-FULL-I'],
            [ESG, 'GBP', 'All Equity', '', 'All', 'ESG-GBP-AE'],
            ['PMG Multi', 'EUR', 'Conservative', 'ex-RA', 'Hedged', 'PMG-MA-EUR-CON-FRA']]
    preview = mandateCodes.preview('codes.csv', _upload(tmp_path, rows))
    assert preview['problems'] == [] and preview['rows'] == 5 and not preview['benchmarkColumn']
    assert preview['cells'] == len(mandateCodes.cells()) and preview['noCode'] == 0
    mandateCodes.importFile('codes.csv', _upload(tmp_path, rows), user='alice', base=preview['base'])
    at = lambda *k: mandateCodes.codeFor(mandateCodes.cell(*k))
    assert at(MA, 'Full', 'Moderate', 'USD', 'Hedged') == 'PMG-MA-USD-FULL'
    assert at(MA, 'Full', 'ModAgg', 'USD', 'ISG Hedged') == 'PMG-MA-USD-MA-FULL-I'
    assert at(MA, 'Full', 'ModAgg', 'USD', 'Hedged') == 'PMG-MA-USD-FULL'
    assert at(MA, 'Full ex-RA', 'Conservative', 'EUR', 'Hedged') == 'PMG-MA-EUR-CON-FRA'
    assert at(ESG, 'All Equity', 'All Equity', 'GBP', 'Unhedged') == 'ESG-GBP-AE'
    assert at('US Onshore', 'Custom', 'LowVol', 'CHF', 'Hedged') == 'PMG-ANY'
    top = mandateCodes.history()[0]
    assert top['action'] == 'imported' and top['note'] == 'Loaded codes.csv' and top['current']
    assert top['changed'] == len(mandateCodes.cells()), 'a code a portfolio'
    assert len(top['changes']) == mandateCodes.CHANGES_SHOWN + 1 and top['changes'][-1].startswith('and ')
    assert mandateCodes.gaps() == {'noCode': []}
    feed = [e for e in repositoryFeed.activity(limit=50)['entries'] if e['area'] == 'mandateCodes']
    assert [e['name'] for e in feed] == ['Mandate codes']


@pytest.mark.parametrize('row, words', [
    ([MA, 'USD', 'Moderate', 'Full', 'Hedged', ''], 'no MandateCode'),
    ([MA, 'JPY', 'Moderate', 'Full', 'Hedged', 'X'], 'Currency "JPY" is not one the table takes'),
    (['Somewhere', 'USD', 'Moderate', 'Full', 'Hedged', 'X'], 'ImplementationType "Somewhere"'),
    ([ESG, 'USD', 'Moderate', 'Full', 'Hedged', 'X'], 'is not a portfolio on offer'),
    ([MA, 'USD', 'Moderate', 'Full', 'Hedged', 'X' * 41], 'keep the code to 40 characters'),
])
def test_a_bad_row_is_a_problem_and_nothing_loads(store, tmp_path, row, words):
    preview = mandateCodes.preview('codes.csv', _upload(tmp_path, [HEAD, row]))
    assert any(words in p for p in preview['problems']), preview['problems']
    with pytest.raises(ValidationError):
        mandateCodes.importFile('codes.csv', _upload(tmp_path, [HEAD, row]), user='alice')
    assert mandateCodes.current()['revision'] == 0


def test_two_rows_naming_as_much_and_disagreeing_are_named(store, tmp_path):
    rows = [HEAD, [MA, 'USD', 'All', 'Core', 'Hedged', 'A'], [MA, 'USD', 'All', 'Core', 'Hedged', 'B'],
            [MA, 'GBP', 'Moderate', 'Core', 'Hedged', 'C'], [MA, 'GBP', 'Moderate', 'Core', 'Hedged', 'C']]
    problems = mandateCodes.preview('codes.csv', _upload(tmp_path, rows))['problems']
    assert 'Rows 2 and 3 both give PMG Multi-Asset Portfolio · Core · Low Vol · USD · Hedged: codes A and B.' in problems
    assert not any('Rows 4 and 5' in p for p in problems), 'two rows that agree are no problem'


def test_a_file_from_before_reads_its_codes_and_leaves_the_benchmark_column(store, tmp_path):
    """D211: a file with the Benchmark column it once had loads its codes;
    the column is not read, and a revision stored as [code, benchmark] reads
    as its code."""
    rows = [HEAD + ['Benchmark'], [MA, 'USD', 'Moderate', 'Core', 'Hedged', 'OLD', 'Not read']]
    preview = mandateCodes.preview('codes.csv', _upload(tmp_path, rows))
    assert preview['problems'] == [] and preview['benchmarkColumn']
    mandateCodes.importFile('codes.csv', _upload(tmp_path, rows), user='alice')
    c = mandateCodes.cell(MA, 'Core', 'Moderate', 'USD', 'Hedged')
    assert mandateCodes.current()['codes'] == {c: 'OLD'}
    conn = sleeveRepo.connection()
    conn.execute("UPDATE policies SET body = ? WHERE kind = 'mandateCodes'",
                 ('{"codes":{"%s":["KEPT","Old benchmark"]}}' % c,))
    conn.commit()
    conn.close()
    assert mandateCodes.codeFor(c) == 'KEPT'
    assert mandateCodes.exportRows()[1] == [MA, 'USD', 'Moderate', 'Core', 'Hedged', 'KEPT']


def test_an_edit_on_screen_a_restore_and_an_export_that_loads_back(store, tmp_path):
    c1 = mandateCodes.cell(MA, 'Full', 'Moderate', 'USD', 'Hedged')
    c2 = mandateCodes.cell(ESG, 'Custom', 'All Equity', 'EUR', 'Equity Not Hedged')
    mandateCodes.save({'codes': {c1: 'PMG-1', c2: ' ESG-CU '}}, 'First codes', user='alice', base=0)
    now = mandateCodes.current()
    assert now['revision'] == 1 and now['codes'] == {c1: 'PMG-1', c2: 'ESG-CU'}
    with pytest.raises(ValidationError, match='already in force'):
        mandateCodes.save({'codes': {c1: 'PMG-1', c2: 'ESG-CU'}}, user='alice')
    with pytest.raises(ValidationError, match='not a portfolio on offer'):
        mandateCodes.save({'codes': {mandateCodes.cell(ESG, 'Full', 'Moderate', 'USD', 'Hedged'): 'X'}},
                          user='alice')
    with pytest.raises(ValidationError, match='a code is a word'):
        mandateCodes.save({'codes': {c1: ['PMG-1', 'MSCI ACWI']}}, user='alice')
    with pytest.raises(sleeveRepo.StaleError):
        mandateCodes.save({'codes': {c1: 'PMG-2'}}, user='bob', base=0)
    mandateCodes.save({'codes': {c1: 'PMG-2'}}, user='bob', base=1)
    assert mandateCodes.history()[0]['changes'] == [
        'PMG Multi-Asset Portfolio · Full · Moderate · USD · Hedged: code PMG-1 → PMG-2',
        'PMG ESG · Custom · All Equity · EUR · Equity Not Hedged: code ESG-CU → —']
    mandateCodes.revert(1, user='bob', base=2)
    assert mandateCodes.current()['codes'] == {c1: 'PMG-1', c2: 'ESG-CU'}
    rows = mandateCodes.exportRows()
    assert rows[0] == HEAD
    assert rows[1:] == [[MA, 'USD', 'Moderate', 'Full', 'Hedged', 'PMG-1'],
                        [ESG, 'EUR', 'All Equity', 'Custom', 'Equity Not Hedged', 'ESG-CU']]
    again = mandateCodes.preview('codes.csv', _upload(tmp_path, rows))
    assert again['problems'] == [] and again['changed'] == 0, 'an export loads back with nothing to do'
    from openpyxl import load_workbook
    import io
    book = load_workbook(io.BytesIO(mandateCodes.exportWorkbook()))
    assert book.sheetnames == ['Mandate Codes'] and book.active.max_row == 3


def test_the_tab_is_the_admins_and_answers_over_its_routes(store, tmp_path):
    got = dashboardRouter.getMandateCodes(caller=_caller('alice'))
    assert got['revision'] == 0 and got['cells'] == len(mandateCodes.cells())
    assert [t['type'] for t in got['layout']][:2] == [MA, ESG] and got['codeMax'] == 40
    c1 = mandateCodes.cell(MA, 'Core', 'LowVol', 'GBP', 'Unhedged')
    saved = dashboardRouter.saveMandateCodes({'codes': {c1: 'X'}, 'base': 0}, caller=_caller('alice'))
    assert saved['mandateCodes']['revision'] == 1
    assert dashboardRouter.saveMandateCodes({'codes': {c1: 'Z'}, 'base': 0}, caller=_caller('alice')).status_code == 409
    assert dashboardRouter.saveMandateCodes({'codes': {'nope': 'Z'}, 'base': 1}, caller=_caller('alice')).status_code == 422
    assert dashboardRouter.getMandateCodesHistory(caller=_caller('alice'))['history'][0]['revision'] == 1
    rows = [HEAD, [MA, 'GBP', 'Low Vol', 'Core', 'Unhedged', 'W']]
    preview = dashboardRouter.previewMandateCodes({'name': 'codes.csv', 'content': _upload(tmp_path, rows)},
                                                  caller=_caller('alice'))
    assert preview['changed'] == 1
    done = dashboardRouter.importMandateCodes({'name': 'codes.csv', 'content': _upload(tmp_path, rows),
                                               'base': 1}, caller=_caller('alice'))
    assert done['mandateCodes']['codes'] == {c1: 'W'}
    back = dashboardRouter.revertMandateCodes({'revision': 1, 'base': 2}, caller=_caller('alice'))
    assert back['mandateCodes']['revision'] == 3
    assert dashboardRouter.exportMandateCodes(caller=_caller('alice')).body[:2] == b'PK'
    from cyrus_pmg.pmgService.core.accessControl import requireAdmin
    for route in dashboardRouter.router.routes:
        if route.path.startswith('/scenario/repository/mandate-codes'):
            assert requireAdmin in [d.call for d in route.dependant.dependencies], route.path
