"""D192: a product held instead below another's minimum.

A product in a sleeve may name an alternative. Where the amount reaching the
product - mandate size times the line's weight in the implemented portfolio -
is below its minimum, the line holds the alternative: the whole line, priced
as the alternative, saying what it stands in for. The choice is kept with the
sleeve through every save, revision, revert, export and import; the page
makes the same swap the files do; the converter reads it from a long table.
"""

import csv
import json
import os
import shutil
import subprocess

import pytest

from cyrus_pmg.pmgService.scenario import products, proposalRegister, rules, sleeveRepo, sleeveTools
from cyrus_pmg.pmgService.scenario.bakedAdapter import BakedScenarioPort
from cyrus_pmg.pmgService.scenario.types import BasisInput, PortfolioKey, ValidationError
from cyrus_pmg.pmgService.scenario.workbook import _holdAlternatives, buildImplementationRows

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = BakedScenarioPort()
MA = 'PMG Multi-Asset Portfolio'
IG = 'Investment Grade Fixed Income'
SMA, FUND, OTHER_SMA = 'gsam-core-municipal-sma', 'gs-us-corporate-bond-fund', 'gsam-intermediate-credit-sma'
KEY = 'USD|Moderate|Full|0'


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    return tmp_path


def _sleeve(rows, name='Muni Test'):
    return sleeveRepo.createSleeve(MA, IG, name, rows, user='alice')


def _book():
    basis = BasisInput(currency='USD', hedging='Hedged')
    result = PORT.resolve_portfolio(basis, PortfolioKey.fromStr(KEY))
    from test_initial_allocation import _sleeves
    return result, _sleeves(result)


def _model(sleeveName, mandate, tilt=True):
    result, chosen = _book()
    chosen = dict(chosen, **{IG: sleeveName})
    return buildImplementationRows(result, chosen, rules.autoSleeveCategories(), mandate, MA,
                                   tilt, 'CASP', 'PMG Target', mandate, False, 'USD')


def _ig(model):
    return next(g for g in model['groups'] if g['category'] == IG)


# ------------------------------------------------------------- the store ---

def test_the_alternative_is_kept_through_save_history_revert_and_export(store):
    made = _sleeve([{'productId': SMA, 'weight': 1.0, 'alternative': FUND}])
    assert [(r['productId'], r['alternative']) for r in made['products']] == [(SMA, FUND)]
    assert made['products'][0]['alternativeProduct']['productId'] == FUND
    served = next(s for s in sleeveRepo.listSleeves(IG, MA) if s['name'] == 'Muni Test')
    assert served['products'][0]['alternative']['productId'] == FUND
    # cleared, then reverted to the revision that had it
    cleared = sleeveRepo.updateSleeve(made['id'], 'Muni Test', [{'productId': SMA, 'weight': 1.0}],
                                      user='bob')
    assert cleared['products'][0]['alternative'] == ''
    history = sleeveRepo.history(made['id'])
    assert any('no alternative below its minimum' in c for h in history for c in h['changes'])
    back = sleeveRepo.revertSleeve(made['id'], 1, user='bob')
    assert back['products'][0]['alternative'] == FUND
    assert any(c.endswith('below its minimum, holds GS US Corporate Bond Fund')
               for h in sleeveRepo.history(made['id']) for c in h['changes'])
    assert ('PMG Multi-Asset Portfolio', IG, 'Muni Test', '', SMA, 1.0, FUND) in sleeveRepo.exportRows()


def test_the_file_column_round_trips_and_an_old_file_still_loads(store, tmp_path):
    _sleeve([{'productId': SMA, 'weight': 0.6, 'alternative': FUND}, {'productId': FUND, 'weight': 0.4}])
    out = tmp_path / 'library.csv'
    assert sleeveTools.main(['--export', str(out)]) == 0
    with open(out, newline='', encoding='utf-8') as fh:
        rows = list(csv.reader(fh))
    assert rows[0][-1] == 'BelowMinimum'
    assert [MA, IG, 'Muni Test', '', SMA, '0.6', FUND] in rows
    assert sleeveTools.main(['--import', str(out), '--replace']) == 0
    again = next(e for e in sleeveRepo.listAll(MA) if e['name'] == 'Muni Test')
    assert [(r['productId'], r['alternative']) for r in again['products']] == [(SMA, FUND), (FUND, '')]
    old = tmp_path / 'old.csv'
    old.write_text('Variant,Category,Sleeve,Edition,ProductId,Weight\n'
                   '{},{},Old Shape,,{},1\n'.format(MA, IG, SMA))
    assert sleeveTools.main(['--import', str(old)]) == 0
    assert next(e for e in sleeveRepo.listAll(MA) if e['name'] == 'Old Shape')['products'][0]['alternative'] == ''


def test_a_bad_alternative_is_refused(store):
    with pytest.raises(ValidationError, match='its own alternative'):
        _sleeve([{'productId': SMA, 'weight': 1.0, 'alternative': SMA}])
    with pytest.raises(ValidationError, match='not in the catalogue'):
        _sleeve([{'productId': SMA, 'weight': 1.0, 'alternative': 'no-such-product'}])


def test_an_alternative_cannot_be_withdrawn_while_a_sleeve_names_it(store):
    _sleeve([{'productId': SMA, 'weight': 1.0, 'alternative': 'gs-short-duration-income'}])
    holders = [e for e in sleeveRepo.listAll() if any(
        r['productId'] == 'gs-short-duration-income' for r in e['products'])]
    assert not holders, 'held only as an alternative here'
    with pytest.raises(ValidationError):
        products.withdraw('gs-short-duration-income', user='alice')


# ------------------------------------------------------------- the model ---

def test_below_the_minimum_the_line_holds_the_alternative(store):
    _sleeve([{'productId': SMA, 'weight': 1.0, 'alternative': FUND}])
    big = _ig(_model('Muni Test', 100e6))
    assert [i['productId'] for i in big['items']] == [SMA]
    assert not big['items'][0].get('standsIn') and 'alternative' not in big['items'][0]
    small = _model('Muni Test', 10e6)
    line = _ig(small)['items'][0]
    assert line['productId'] == FUND and not line['belowMinimum']
    assert line['standsIn'] == [{'productId': SMA, 'name': 'GSAM Core Municipal SMA',
                                 'minimumInvestment': 5e6, 'notional': line['notional']}]
    assert line['notional'] < 5e6
    assert not [b for b in small['breaches'] if b['category'] == IG], 'nothing in IGFI is below a minimum'
    assert line['feeGroup'] == products.get(FUND)['feeGroup'], 'priced as the product held'


def test_the_switches_count(store):
    """The amount is the implemented one: turning the tilts on takes weight
    from IGFI and can move the SMA below its minimum."""
    _sleeve([{'productId': SMA, 'weight': 1.0, 'alternative': FUND}])
    on, off = _ig(_model('Muni Test', 100e6, tilt=True)), _ig(_model('Muni Test', 100e6, tilt=False))
    assert on['weightPct'] < off['weightPct']
    mandate = 5e6 / ((on['weightPct'] + off['weightPct']) / 2 / 100)
    assert _ig(_model('Muni Test', mandate, tilt=False))['items'][0]['productId'] == SMA
    assert _ig(_model('Muni Test', mandate, tilt=True))['items'][0]['productId'] == FUND


def test_an_alternative_the_sleeve_holds_too_joins_its_line(store):
    _sleeve([{'productId': SMA, 'weight': 0.6, 'alternative': FUND}, {'productId': FUND, 'weight': 0.4}])
    model = _model('Muni Test', 10e6)
    group = _ig(model)
    assert [i['productId'] for i in group['items']] == [FUND]
    line = group['items'][0]
    assert line['printedPct'] == pytest.approx(group['weightPct'], abs=0.011)
    assert line['standsIn'][0]['productId'] == SMA
    assert sum(i['printedPct'] for g in model['groups'] for i in g['items']) == pytest.approx(100.0)


def test_an_alternative_short_too_is_flagged_as_before(store):
    _sleeve([{'productId': SMA, 'weight': 1.0, 'alternative': OTHER_SMA}])
    model = _model('Muni Test', 10e6)
    line = _ig(model)['items'][0]
    assert line['productId'] == OTHER_SMA and line['belowMinimum']
    assert [b['name'] for b in model['breaches'] if b['category'] == IG] == [line['name']]


def test_the_register_records_what_a_line_stands_in_for():
    assert 'standsIn' in proposalRegister._ITEM_FIELDS


# -------------------------------------------------------------- the page ---

def _js(name):
    with open(os.path.join(HERE, '..', '..', 'generator', 'js', 'implementation.js'), encoding='utf-8') as fh:
        js = fh.read()
    start = js.index('function ' + name + '(')
    return js[start:js.index('\n}\n', start)] + '\n}\n'


def test_the_page_makes_the_same_swap_as_the_files():
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    fund = {'productId': FUND, 'name': 'Fund', 'feeGroup': 'Core Active', 'productCost': 0.4,
            'minimumInvestment': 1000}
    sma = {'productId': SMA, 'name': 'SMA', 'feeGroup': 'Core Active', 'productCost': 0.25,
           'minimumInvestment': 5e6}
    other = {'productId': OTHER_SMA, 'name': 'Other SMA', 'feeGroup': 'Core Active', 'productCost': 0.3,
             'minimumInvestment': 5e6}

    def server(mandate):
        groups = [{'items': [
            dict(sma, weight=0.6, exactPct=18.0, printedPct=18.0, alternative=dict(fund),
                 notional=round(mandate * 18.0 / 100 / 100) * 100),
            dict(fund, weight=0.4, exactPct=12.0, printedPct=12.0, alternative=None,
                 notional=round(mandate * 12.0 / 100 / 100) * 100)]},
            {'items': [dict(sma, weight=1.0, exactPct=10.0, printedPct=10.0, alternative=dict(other),
                            notional=round(mandate * 10.0 / 100 / 100) * 100)]}]
        _holdAlternatives(groups, 'USD', mandate)
        return [[(i['productId'], round(i['printedPct'], 2), [s['productId'] for s in i.get('standsIn') or []])
                 for i in g['items']] for g in groups]

    script = ('var ROUND_TO = 100, MANDATE = 0;\n' + _js('implItem') + _js('holdAlternatives') + '''
var App = { mandateSize: function () { return MANDATE; } };
function managementFee() { return null; }
var fund = %s, sma = %s, other = %s;
function page(mandate) {
  MANDATE = mandate;
  function line(p, share, w, alt) {
    var i = implItem(p, null); i.share = share; i.exact = w; i.weight = w;
    i.notional = Math.round(mandate * w / 100 / ROUND_TO) * ROUND_TO; i.alternative = alt; return i;
  }
  var groups = [{ items: [line(sma, 0.6, 18, fund), line(fund, 0.4, 12, null)] },
                { items: [line(sma, 1, 10, other)] }];
  holdAlternatives(groups, null);
  return groups.map(function (g) { return g.items.map(function (i) {
    return [i.productId, Math.round(i.weight * 100) / 100, (i.standsIn || []).map(function (s) { return s.productId; })];
  }); });
}
console.log(JSON.stringify([page(10e6), page(100e6)]));
''' % (json.dumps(fund), json.dumps(sma), json.dumps(other)))
    got = json.loads(subprocess.run([node, '-e', script], capture_output=True, text=True,
                                    check=True).stdout)
    want = [server(10e6), server(100e6)]
    assert got == [[[list(x) for x in g] for g in m] for m in want]
    assert want[0][0] == [(FUND, 30.0, [SMA])], 'below: one line, the fund'
    # every product is held in every currency (D207): an alternative short
    # too is still held, and flagged below its minimum, as before
    assert want[0][1] == [(OTHER_SMA, 10.0, [SMA])]
    assert want[1][0] == [(SMA, 18.0, []), (FUND, 12.0, [])], 'above: as the sleeve holds them'


# --------------------------------------------------------- the converter ---

def test_the_converter_reads_a_below_minimum_column(store, tmp_path):
    import sys
    sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
    import convertSleeveTable as conv
    table = tmp_path / 'table.csv'
    table.write_text(
        'Currency,Long Name,Category,Implementation Type,Portfolio Name,Implementation / Product,Weight,Below Minimum\n'
        'USD,Muni,{ig},Multi-Asset,USD Moderate Full,GSAM Core Municipal SMA,100%,GS US Corporate Bond Fund\n'
        'USD,Muni,{ig},Multi-Asset,USD Conservative Full,GSAM Core Municipal SMA,100%,\n'.format(ig=IG))
    out = tmp_path / 'library.csv'
    assert conv.main([str(table), os.path.join(HERE, '..', '..', 'productSource', 'products.csv'),
                      str(out)]) == 0
    with open(out, newline='', encoding='utf-8') as fh:
        rows = list(csv.reader(fh))
    assert rows[0][-1] == 'BelowMinimum'
    alts = sorted((r[3], r[6]) for r in rows[1:])
    assert len(alts) == 2 and {a for _, a in alts} == {FUND, ''}, 'the alternative sets one edition apart'
