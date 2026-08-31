"""Safety net for the Proposal Tool back end.

The host dashboard package is untested and this adds nothing to it (spec 16
item 10); these tests live on the epsilon-phi side, where the brief invites a
net "around the fee and rounding arithmetic in section 8.4, where a silent
error is both most likely and most costly" - plus the finiteness guard of
section 15.8, because reconciliation tests pass happily on nonsense.

Run:  cd proposal-tool/service && PYTHONPATH=. python3 -m pytest tests -q
"""

import json
import math
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cyrus_pmg.pmgService.scenario import rules, sleeves
from cyrus_pmg.pmgService.scenario.fixturesAdapter import FixturesScenarioPort
from cyrus_pmg.pmgService.scenario.payloads import roundWeightsLargestRemainder
from cyrus_pmg.pmgService.scenario.types import BasisInput, MandateInput, PortfolioKey, ValidationError
from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows

BASIS = BasisInput(currency='USD', hedging='Hedged')
PORT = FixturesScenarioPort()


def _allKeys(currency='USD'):
    schema = PORT.get_schema(BasisInput(currency=currency, hedging='Hedged'), None)
    return [PortfolioKey.fromStr(k) for k in schema['availability']]


# ---------------------------------------------------------------- schema ----

def test_availability_matches_supplied_universe():
    schema = PORT.get_schema(BASIS, None)
    assert len(schema['availability']) == 68          # spec 4.5
    assert schema['categories'][0] == 'Investment Grade Fixed Income'
    assert len(schema['categories']) == 7


def test_twenty_million_rule_removes_private_allocations():
    small = MandateInput(topAccountSize=15e6, mandateSize=10e6, primaryPwa='')
    schema = PORT.get_schema(BASIS, small)
    assert schema['options']['allocations'] == ['Core', 'Ex Alts']
    assert len(schema['availability']) == 24
    assert all(not k.startswith(('Full|', 'Ex HFs|')) for k in schema['availability'])


def test_mandate_validation_fields():
    with pytest.raises(ValidationError) as err:
        PORT.validate_mandate(MandateInput(topAccountSize=50e6, mandateSize=4e6,
                                           primaryPwa='M. Aldridge — Zurich'))
    assert err.value.field == 'mandateSize'
    with pytest.raises(ValidationError) as err:
        PORT.validate_mandate(MandateInput(topAccountSize=10e6, mandateSize=26e6,
                                           primaryPwa='M. Aldridge — Zurich'))
    assert err.value.field == 'mandateSize'
    with pytest.raises(ValidationError) as err:
        PORT.validate_mandate(MandateInput(topAccountSize=50e6, mandateSize=26e6,
                                           primaryPwa='Nobody'))
    assert err.value.field == 'primaryPwa'


# ---------------------------------------------- finiteness guard (15.8) -----

def test_every_available_portfolio_resolves_finite():
    """All 68 x 4 currencies resolve; every number on every surface finite."""
    for currency in rules.CURRENCIES:
        for key in _allKeys(currency):
            result = PORT.resolve_portfolio(
                BasisInput(currency=currency, hedging='Hedged'), key)
            total = sum(c['weightPct'] for c in result['categories'])
            assert abs(total - 100.0) < 1e-6, (currency, key.toStr(), total)
            assert 2 <= len(result['categories']) <= 7
            for metric in result['metrics'].values():
                assert math.isfinite(metric)
            for row in result['stress'] + result['premia']:
                assert math.isfinite(row['nominalPct'])
                assert math.isfinite(row['realPct'])


def test_taa_category_tracks_the_tick_box():
    withTaa = PORT.resolve_portfolio(BASIS, PortfolioKey('Core', True, False, 'Mod'))
    without = PORT.resolve_portfolio(BASIS, PortfolioKey('Core', True, True, 'Mod'))
    names = lambda r: [c['name'] for c in r['categories']]
    assert 'Asset Allocation Strategies' in names(withTaa)
    assert 'Asset Allocation Strategies' not in names(without)


def test_ex_re_narrows_other_private_assets_instead_of_dropping_it():
    """Spec 2.2: excluding RE keeps Other Private Assets via Private Credit."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('Full', True, False, 'Mod'))
    opa = [c for c in result['categories'] if c['name'] == 'Other Private Assets']
    assert opa, 'Full ex RE must still hold Other Private Assets'
    assets = [a['reportingName'] for a in opa[0]['assets']]
    assert assets == ['Private Credit']


# --------------------------------------------- rounding rule (spec 8.4) -----

def test_largest_remainder_sums_exactly():
    import random
    rng = random.Random(84)
    for _ in range(500):
        n = rng.randint(2, 30)
        raw = [rng.random() for _ in range(n)]
        total = sum(raw)
        exact = [w / total * 100.0 for w in raw]
        printed = roundWeightsLargestRemainder(exact)
        assert abs(sum(printed) - 100.0) < 1e-9
        assert all(abs(p - e) < 0.01 + 1e-9 for p, e in zip(printed, exact))


def _implementationFor(key, sleeveChoice=0, mandateSize=26_000_000):
    result = PORT.resolve_portfolio(BASIS, key)
    chosen = {}
    for category in result['categories']:
        name = category['name']
        if name in rules.AUTO_SLEEVE_CATEGORIES:
            continue
        library = sleeves.listSleeves(name)
        chosen[name] = library[sleeveChoice % len(library)]['name']
    return buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES,
                                   mandateSize)


@pytest.mark.parametrize('sleeveChoice', [0, 1, 2])
@pytest.mark.parametrize('mandateSize', [26_000_000, 5_000_000, 19_999_900, 137_400_000])
def test_implementation_invariants_over_the_whole_space(sleeveChoice, mandateSize):
    """The section 8.4 table, held across every available USD combination,
    every sleeve column, and awkward mandate sizes."""
    for key in _allKeys():
        model = _implementationFor(key, sleeveChoice, mandateSize)
        assert model['complete']
        items = [i for g in model['groups'] for i in g['items']]
        assert abs(sum(i['printedPct'] for i in items) - 100.0) < 1e-9
        assert all(i['notional'] % 100 == 0 for i in items)
        for item in items:
            reconciled = round(mandateSize * item['printedPct'] / 100 / 100) * 100
            assert reconciled == item['notional'], (key.toStr(), item['name'])
            allIn = item['productCost'] + item['managementFee']
            assert abs(item['wtdFeeBp'] - allIn * item['printedPct']) < 1e-9
        total = model['total']
        assert abs(total['weightPct'] - 100.0) < 1e-9


def test_round_million_mandate_notional_sums_exactly():
    model = _implementationFor(PortfolioKey('Core', True, False, 'Mod'))
    assert model['total']['notional'] == 26_000_000


# -------------------------------- JS mirror agreement (screen == sheet) -----

def test_js_rounding_mirror_agrees_with_python():
    """The page's largest-remainder mirror must agree with the Python one,
    ties included, or the workbook and the screen drift (spec 14.4)."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    jsPath = os.path.join(os.path.dirname(__file__), '..', '..',
                          'generator', 'js', 'implementation.js')
    with open(jsPath, encoding='utf-8') as fh:
        source = fh.read()
    start = source.index('function roundWeights(')
    end = source.index('function money(')
    fn = source[start:end]

    import random
    rng = random.Random(4242)
    cases = []
    for _ in range(300):
        n = rng.randint(2, 25)
        raw = [rng.random() for _ in range(n)]
        total = sum(raw)
        cases.append([w / total * 100.0 for w in raw])
    # tie-break cases: identical remainders
    cases.append([12.5] * 8)
    cases.append([100.0 / 3] * 3)
    cases.append([100.0 / 7] * 7)

    script = fn + '\nconst cases = ' + json.dumps(cases) + ';\n' \
        + 'process.stdout.write(JSON.stringify(cases.map(roundWeights)));\n'
    out = subprocess.run([node, '-e', script], capture_output=True, text=True,
                         check=True)
    jsResults = json.loads(out.stdout)
    for case, jsResult in zip(cases, jsResults):
        pyResult = roundWeightsLargestRemainder(case)
        assert [round(v, 2) for v in jsResult] == [round(v, 2) for v in pyResult]


# ----------------------------------------------------------- workbook -------

def test_fixtures_workbook_reconciles_and_has_three_sheets(tmp_path):
    from openpyxl import load_workbook
    key = PortfolioKey('Core', True, False, 'Mod')
    result = PORT.resolve_portfolio(BASIS, key)
    chosen = {c['name']: sleeves.listSleeves(c['name'])[0]['name']
              for c in result['categories']
              if c['name'] not in rules.AUTO_SLEEVE_CATEGORIES}
    mandate = MandateInput(topAccountSize=48.5e6, mandateSize=26e6,
                           primaryPwa='M. Aldridge — Zurich')
    payload = PORT.build_export(BASIS, mandate, [result], {'sleeves': chosen})
    path = tmp_path / 'wb.xlsx'
    path.write_bytes(payload)
    book = load_workbook(path)
    assert book.sheetnames == ['Portfolios', 'Risk Dashboard', 'Implementation']
    sheet = book['Implementation']
    rows_ = list(sheet.iter_rows(values_only=True))
    weights = [r[2] for r in rows_[1:]
               if r[2] is not None and r[0] and str(r[0]).startswith('  ')]
    assert abs(sum(weights) * 100 - 100.0) < 1e-9
    notionals = [r[12] for r in rows_[1:]
                 if r[12] is not None and r[0] and str(r[0]).startswith('  ')]
    assert sum(notionals) == 26_000_000
    assert all(n % 100 == 0 for n in notionals)


# ----------------------------------------------------------- store ----------

def test_store_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path))
    from cyrus_pmg.pmgService.scenario import scenarioStore as store
    from cyrus_pmg.pmgService.scenario.types import ScenarioNotFound
    mandate = MandateInput(topAccountSize=48.5e6, mandateSize=26e6,
                           primaryPwa='M. Aldridge — Zurich')
    state = store.createScenario(mandate, BASIS)
    key = PortfolioKey('Core', True, False, 'Mod')
    store.recordColumn(state['id'], key, 'base')
    other = PortfolioKey('Full', False, False, 'Mod Agg')
    store.recordColumn(state['id'], other, 'comparison')
    loaded = store.getScenario(state['id'])
    assert loaded['base'] == key.toStr()
    assert loaded['comparisons'] == [other.toStr()]
    # base replacement drops a duplicating comparison
    store.recordColumn(state['id'], other, 'base')
    loaded = store.getScenario(state['id'])
    assert loaded['base'] == other.toStr()
    assert loaded['comparisons'] == []
    with pytest.raises(ValidationError):
        store.removeColumn(state['id'], other)      # base cannot be removed
    with pytest.raises(ScenarioNotFound):
        store.getScenario('sc_000000000000')
