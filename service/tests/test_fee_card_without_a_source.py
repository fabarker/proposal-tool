"""A fee group the card prices without a source (D185).

A row of the card - a fee group's under RDR, or CASP's one - may leave out
every rate of a source but Management: a - in each of its PMG boxes. That
source's levels are then not offered to a portfolio holding a product the
row prices, and a portfolio on one is priced at the same point on
Management. Each test has a repository file of its own."""
import base64
import copy
import csv
import io
import json
import os
import shutil
import subprocess

import pytest

from cyrus_pmg.pmgService.scenario import feeCard, fees, products, rules, sleeves
from cyrus_pmg.pmgService.scenario.fixturesAdapter import FixturesScenarioPort
from cyrus_pmg.pmgService.scenario.types import BasisInput, PortfolioKey, ValidationError
from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows

HERE = os.path.dirname(os.path.abspath(__file__))
MA = sleeves.VARIANTS[0]
PMG = [fees.levelId('PMG', p) for p in fees.POINTS]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    products.reload()
    feeCard._forget()
    yield tmp_path
    products.reload()
    feeCard._forget()


def _withoutPmg(card, schedule='RDR', row='Alternatives'):
    for byLevel in card['rates'][schedule][row].values():
        for level in PMG:
            byLevel.pop(level)
    return card


def _saved(card, note='No PMG schedule'):
    return feeCard.save(card, note, user='alice', base=fees.deliveryInfo()['revision'])


def _file(rows):
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows(rows)
    return base64.b64encode(buf.getvalue().encode('utf-8')).decode('ascii')


def _packagedRows(dash=lambda r: False, value='-'):
    with open(fees.ratesPath(), newline='', encoding='utf-8-sig') as fh:
        rows = list(csv.reader(fh))
    return [rows[0]] + [r[:7] + [value] if dash(r) else r for r in rows[1:]]


def _pmgOf(group):
    return lambda r: r[0] == 'RDR' and r[1] == group and r[5] == 'PMG'


# ------------------------------------------------------------ the card ---

def test_a_file_leaves_a_groups_pmg_rates_out_with_a_dash_and_exports_them_so(store):
    content = _file(_packagedRows(_pmgOf('Alternatives')))
    preview = feeCard.importPreview('no-pmg.csv', content)
    assert preview['rates'] == 15
    assert any(line.startswith('RDR · Alternatives · Under $10m · PMG') and line.endswith('→ -')
               for line in preview['changes'])
    feeCard.importFile('no-pmg.csv', content, 'Alternatives has no PMG schedule', user='alice', base=1)
    assert fees.rowSources('RDR', 'Alternatives') == ['Management']
    assert fees.rowSources('RDR', 'Passive') == list(fees.SOURCES)
    assert fees.absent() == {'RDR': {'Alternatives': ['PMG']}}
    exported = list(csv.reader(io.StringIO(feeCard.exportCsv())))
    dashed = [r for r in exported[1:] if r[7] == '-']
    assert len(dashed) == 15 and all(_pmgOf('Alternatives')(r) for r in dashed)
    # an export loads back as the very card it came from
    again = feeCard.importPreview('again.csv', _file(exported))
    assert again['same'] and again['rates'] == 0


def test_a_blank_rate_in_a_file_says_the_same_as_a_dash(store):
    content = _file(_packagedRows(_pmgOf('Passive'), value=''))
    feeCard.importFile('blank.csv', content, 'Passive has no PMG schedule', user='alice', base=1)
    assert fees.absent() == {'RDR': {'Passive': ['PMG']}}


@pytest.mark.parametrize('dash, words', [
    (lambda r: _pmgOf('Alternatives')(r) and r[2] == 'T1', 'a row gives every PMG rate or none'),
    (lambda r: r[0] == 'RDR' and r[1] == 'Passive' and r[5] == 'Management',
     'every row prices Management'),
])
def test_some_of_a_rows_pmg_rates_or_any_management_rate_left_out_is_refused(store, dash, words):
    with pytest.raises(ValidationError) as caught:
        feeCard.importPreview('bad.csv', _file(_packagedRows(dash)))
    assert words in caught.value.message
    assert fees.absent() == {}


def test_the_console_leaves_a_groups_pmg_rates_out_and_the_history_says_so(store):
    card = _withoutPmg(copy.deepcopy(feeCard.describe()['card']))
    saved = _saved(card)
    assert any(line.startswith('RDR · Alternatives · Under $10m · PMG Floor') and line.endswith('→ -')
               for line in saved['changes'])
    assert fees.absent() == {'RDR': {'Alternatives': ['PMG']}}
    stored = feeCard.describe()
    assert stored['requiredSource'] == 'Management'
    assert set(stored['card']['rates']['RDR']['Alternatives']['T1']) == {
        fees.levelId('Management', p) for p in fees.POINTS}
    # the rates come back as they were, each from a -
    back = feeCard.save(copy.deepcopy(_packagedCard()), 'PMG again', user='alice', base=2)
    assert any(line.startswith('RDR · Alternatives · Under $10m · PMG Floor -') for line in back['changes'])
    assert fees.absent() == {}


def _packagedCard():
    delivered = fees._readDelivery(fees.ratesPath())
    return feeCard.fromDelivery(delivered)


@pytest.mark.parametrize('change, words', [
    (lambda c: c['rates']['RDR']['Alternatives']['T1'].pop('PMG Target'),
     'Give every PMG rate on the row, or none'),
    (lambda c: [b.update({fees.levelId('Management', p): '-' for p in fees.POINTS})
                for b in c['rates']['RDR']['Passive'].values()],
     'Give its Management rates, or none of its rates yet'),
])
def test_the_console_refuses_part_of_a_source_or_management_left_out(store, change, words):
    card = copy.deepcopy(feeCard.describe()['card'])
    change(card)
    with pytest.raises(ValidationError) as caught:
        _saved(card)
    assert caught.value.field == 'rates' and words in caught.value.message
    assert fees.deliveryInfo()['revision'] == 1


# ------------------------------------------------------- pricing it ---

def test_a_pmg_level_moves_to_management_only_where_a_held_group_lacks_pmg(store):
    _saved(_withoutPmg(copy.deepcopy(feeCard.describe()['card'])))
    assert fees.levelInForce('RDR', 'PMG Target', ['Passive', 'Core Active']) == ('PMG Target', [])
    assert fees.levelInForce('RDR', 'PMG Ceiling', ['Passive', 'Alternatives']) == \
        ('Management Ceiling', ['Alternatives'])
    assert fees.levelInForce('RDR', 'Management Floor', ['Alternatives']) == ('Management Floor', [])
    # CASP prices every product by its one row, which has PMG
    assert fees.levelInForce('CASP', 'PMG Target', ['Alternatives']) == ('PMG Target', [])
    assert fees.levelInForce('RDR', fees.CUSTOM_LEVEL, ['Alternatives']) == (fees.CUSTOM_LEVEL, [])
    tier = fees.ratesAtTier('T1')['RDR']['groups']
    assert set(tier['Alternatives']) == {fees.levelId('Management', p) for p in fees.POINTS}
    assert set(tier['Passive']) == set(fees.LEVELS)
    payload = fees.feePayload(5e6, 5e6)
    assert payload['absent'] == {'RDR': {'Alternatives': ['PMG']}}
    assert payload['requiredSource'] == 'Management'
    # the viewers show a - where the row has none
    passive = next(r for r in fees.grid('T1')['rows'] if r['feeGroup'] == 'Passive')
    assert passive['cells']['PMG Target'] is not None
    alt = next(r for r in fees.grid('T1')['rows'] if r['feeGroup'] == 'Alternatives')
    assert alt['cells']['PMG Target'] is None and alt['cells']['Management Target'] is not None


def test_a_uniform_schedule_without_pmg_moves_every_portfolio_on_it(store):
    _saved(_withoutPmg(copy.deepcopy(feeCard.describe()['card']), 'CASP', ''))
    assert fees.levelInForce('CASP', 'PMG Target', []) == ('Management Target', [None])
    assert fees.levelInForce('RDR', 'PMG Target', ['Passive']) == ('PMG Target', [])
    blended = fees.marginalPayload('CASP', 3e7)
    assert set(blended['effective']) == {fees.levelId('Management', p) for p in fees.POINTS}


def _model(feeLevel):
    port = FixturesScenarioPort()
    basis = BasisInput(currency='USD', hedging='Hedged')
    result = port.resolve_portfolio(basis, PortfolioKey('USD', 'Moderate', 'Full', False))
    chosen = {}
    for c in result['categories']:
        if c['name'] in rules.AUTO_SLEEVE_CATEGORIES:
            continue
        under = rules.sleeveCategory(c['name'])
        chosen.setdefault(under, sleeves.listSleeves(under, MA)[0]['name'])
    return buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES, 5e7, MA,
                                   False, 'RDR', feeLevel, 5e7, False, 'USD')


def test_a_portfolio_holding_such_a_group_is_priced_and_printed_at_management(store):
    held = sorted({i['feeGroup'] for g in _model('PMG Target')['groups'] for i in g['items']})
    assert held, 'the model holds products'
    lacking = held[0]
    _saved(_withoutPmg(copy.deepcopy(feeCard.describe()['card']), 'RDR', lacking))
    model = _model('PMG Target')
    assert model['feeLevel'] == 'Management Target' and model['feeLevelMovedBy'] == [lacking]
    for group in model['groups']:
        for item in group['items']:
            assert item['managementFee'] == fees.managementFee('RDR', 5e7, 'Management Target',
                                                               item['feeGroup'])
    # a group the portfolio does not hold moves nothing
    feeCard._forget()
    unheld = next(g for g in fees.feeGroups() if g not in held)
    _saved(_packagedCard())
    _saved(_withoutPmg(copy.deepcopy(feeCard.describe()['card']), 'RDR', unheld))
    assert _model('PMG Target')['feeLevel'] == 'PMG Target'


# ------------------------------------------------------- the page ---

def _node(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    done = subprocess.run([node, '-e', script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-1200:]
    return json.loads(done.stdout)


def _js(name):
    with open(os.path.join(HERE, '..', '..', 'generator', 'js', name), encoding='utf-8') as fh:
        return fh.read()


def _between(source, begin, end):
    start = source.index(begin)
    return source[start:source.index(end)]


def _function(source, name):
    start = source.index('function {}('.format(name))
    return source[start:source.index('\n}\n', start) + 3]


CARD = {
    'tiers': [{'id': 'T1', 'min': 0, 'max': 10000000}, {'id': 'T2', 'min': 10000000, 'max': None}],
    'feeGroups': ['Passive', 'Alternatives'],
    'rates': {
        'CASP': {'': {t: {'Management Floor': 0.4, 'Management Target': 0.5, 'Management Ceiling': 0.6,
                          'PMG Floor': 0.5, 'PMG Target': 0.6, 'PMG Ceiling': 0.7} for t in ('T1', 'T2')}},
        'RDR': {'Passive': {t: {'Management Floor': 0.1, 'Management Target': 0.2, 'Management Ceiling': 0.3,
                                'PMG Floor': 0.2, 'PMG Target': 0.3, 'PMG Ceiling': 0.4} for t in ('T1', 'T2')},
                'Alternatives': {t: {'Management Floor': 0.7, 'Management Target': 0.8,
                                     'Management Ceiling': 0.9} for t in ('T1', 'T2')}}},
}
LEVELS = ['Management Floor', 'Management Target', 'Management Ceiling', 'PMG Floor', 'PMG Target',
          'PMG Ceiling']
CTX = {'schedules': [{'id': 'CASP', 'byGroup': False}, {'id': 'RDR', 'byGroup': True}],
       'sources': ['Management', 'PMG'], 'points': ['Floor', 'Target', 'Ceiling'], 'levels': LEVELS,
       'rateMax': 10, 'groupMax': 40, 'requiredSource': 'Management'}


def test_the_card_editor_shows_takes_and_checks_a_dash_as_the_server_does():
    got = _node(_between(_js('repository.js'), '/* fee-editor-helpers-begin', '/* fee-editor-helpers-end */') + '''
var card = %s, ctx = %s, levels = %s;
var d = feeDraftFrom(card, levels);
var out = { shown: d.rates.RDR['g:Alternatives'].T1['PMG Target'], clean: feeProblems(d, ctx),
            none: feeChanges(card, d, ctx).lines };
d.rates.RDR['g:Alternatives'].T1['PMG Target'] = '0.95';
out.partial = feeProblems(d, ctx).map(function (p) { return p.message; });
d.rates.RDR['g:Alternatives'].T1['PMG Floor'] = '0.9';
d.rates.RDR['g:Alternatives'].T1['PMG Ceiling'] = '1';
d.rates.RDR['g:Alternatives'].T2['PMG Floor'] = '0.9';
d.rates.RDR['g:Alternatives'].T2['PMG Target'] = '0.95';
d.rates.RDR['g:Alternatives'].T2['PMG Ceiling'] = '1';
out.given = feeChanges(card, d, ctx).lines;
out.givenOk = feeProblems(d, ctx);
d.rates.RDR['g:Passive'].T1['PMG Floor'] = '-';
d.rates.RDR['g:Passive'].T1['PMG Target'] = '-';
d.rates.RDR['g:Passive'].T1['PMG Ceiling'] = '-';
d.rates.RDR['g:Passive'].T2['PMG Floor'] = '-';
d.rates.RDR['g:Passive'].T2['PMG Target'] = '\\u2014';
d.rates.RDR['g:Passive'].T2['PMG Ceiling'] = '-';
out.taken = feeChanges(card, d, ctx).lines.filter(function (l) { return l.indexOf('Passive') >= 0; });
out.payload = feePayload(d, ctx.schedules, ctx.levels).card.rates.RDR.Passive.T1;
d.rates.CASP[''].T1['Management Floor'] = '-';
out.management = feeProblems(d, ctx).map(function (p) { return p.message; });
out.block = feeReadBlock('Floor\\tTarget\\tCeiling\\tFloor\\tTarget\\tCeiling\\n0.1\\t0.2\\t0.3\\t-\\t-\\t-\\n');
out.moved = [feeCellMoved(null, '-'), feeCellMoved(null, '0.4'), feeCellMoved(0.4, '-'), feeCellMoved(undefined, '-')];
process.stdout.write(JSON.stringify(out));
''' % (json.dumps(CARD), json.dumps(CTX), json.dumps(LEVELS)))
    assert got['shown'] == '-' and got['clean'] == [] and got['none'] == []
    assert ('RDR · Alternatives · Under $10m · PMG Floor has no rate. Give every PMG rate on the row, '
            'or none: a - in each PMG box.') in got['partial']
    assert 'RDR · Alternatives · Under $10m · PMG Target - → 0.95' in got['given']
    assert got['givenOk'] == []
    assert 'RDR · Passive · Under $10m · PMG Floor 0.20 → -' in got['taken'] and len(got['taken']) == 6
    assert got['payload']['PMG Target'] is None and got['payload']['Management Target'] == 0.2
    assert ('CASP · Under $10m · Management Floor has no rate. Every product is priced by this row: '
            'it has its Management rates.' in got['management'])
    assert got['block'] == {'cells': [['0.1', '0.2', '0.3', '-', '-', '-']]}
    assert got['moved'] == [False, True, True, False]


def test_the_page_offers_a_portfolio_only_the_levels_its_groups_price():
    source = _js('implementation.js')
    fns = ''.join(_function(source, n) for n in (
        'rowsLacking', 'levelEntry', 'levelInForceWith', 'feeLevelInForce', 'sourceLackingNote',
        'syncFeeLevel'))
    got = _node(fns + '''
var LEVELS = %s.map(function (id) { var p = id.split(' '); return { id: id, source: p[0], point: p[1] }; });
var OPT = { 'fees.absent': { RDR: { Alternatives: ['PMG'] } }, 'fees.levels': LEVELS,
            'fees.requiredSource': 'Management' };
var held = ['Passive'], level = 'PMG Target', schedule = 'RDR', adopted = [];
var App = { opt: function (k, d) { return k in OPT ? OPT[k] : d; }, feeLevel: function () { return level; },
            feeSchedule: function () { return schedule; }, includeFees: function () { return true; },
            adoptFeeLevel: function (l, why) { adopted.push([l, why]); } };
function heldFeeGroups() { return held.slice(); }
function scheduleByGroup(s) { return s === 'RDR'; }
var out = { alone: feeLevelInForce(), ifAdded: levelInForceWith(['Alternatives']) };
syncFeeLevel();
out.quiet = adopted.length;
held.push('Alternatives');
out.held = feeLevelInForce();
out.note = sourceLackingNote('PMG');
out.management = sourceLackingNote('Management');
syncFeeLevel();
out.adopted = adopted;
schedule = 'CASP';
out.casp = feeLevelInForce();
level = 'Custom'; schedule = 'RDR';
out.custom = feeLevelInForce();
process.stdout.write(JSON.stringify(out));
''' % json.dumps(LEVELS))
    assert got['alone'] == 'PMG Target' and got['ifAdded'] == 'Management Target'
    assert got['quiet'] == 0
    assert got['held'] == 'Management Target'
    assert got['note'] == 'The fee card has no PMG rates for Alternatives, which a product here is in.'
    assert got['management'] == ''
    assert got['adopted'] == [['Management Target', got['note']]]
    assert got['casp'] == 'PMG Target' and got['custom'] == 'Custom'
