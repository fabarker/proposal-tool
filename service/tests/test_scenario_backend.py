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


def _implementationFor(key, sleeveChoice=0, mandateSize=26_000_000,
                       variant=sleeves.VARIANTS[0]):
    result = PORT.resolve_portfolio(BASIS, key)
    chosen = {}
    for category in result['categories']:
        name = category['name']
        if name in rules.AUTO_SLEEVE_CATEGORIES:
            continue
        library = sleeves.listSleeves(name, variant)
        chosen[name] = library[sleeveChoice % len(library)]['name']
    return buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES,
                                   mandateSize, variant)


@pytest.mark.parametrize('variant', sleeves.VARIANTS)
@pytest.mark.parametrize('sleeveChoice', [0, 1, 2])
@pytest.mark.parametrize('mandateSize', [26_000_000, 5_000_000, 19_999_900, 137_400_000])
def test_implementation_invariants_over_the_whole_space(sleeveChoice, mandateSize,
                                                        variant):
    """The section 8.4 table, held across every available USD combination,
    every sleeve column, every implementation variant, and awkward mandate
    sizes. The variant axis matters because each one carries its own product
    mix and its own weights, so the rounding has to close on all four."""
    for key in _allKeys():
        model = _implementationFor(key, sleeveChoice, mandateSize, variant)
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
    chosen = {c['name']: sleeves.listSleeves(c['name'], sleeves.VARIANTS[0])[0]['name']
              for c in result['categories']
              if c['name'] not in rules.AUTO_SLEEVE_CATEGORIES}
    mandate = MandateInput(topAccountSize=48.5e6, mandateSize=26e6,
                           primaryPwa='M. Aldridge — Zurich')
    payload = PORT.build_export(BASIS, mandate, [result],
                                {'sleeves': chosen, 'variant': sleeves.VARIANTS[0]})
    path = tmp_path / 'wb.xlsx'
    path.write_bytes(payload)
    book = load_workbook(path)
    assert book.sheetnames == ['Portfolios', 'Risk Dashboard', 'Implementation']
    sheet = book['Implementation']
    rows_ = list(sheet.iter_rows(values_only=True))
    assert rows_[0][0] == 'Implementation variant'
    assert rows_[0][1] == sleeves.VARIANTS[0]
    weights = [r[2] for r in rows_
               if r[2] is not None and r[0] and str(r[0]).startswith('  ')]
    assert abs(sum(weights) * 100 - 100.0) < 1e-9
    notionals = [r[12] for r in rows_
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


# ------------------------------------------- implementation variants (D29) --

def test_schema_offers_the_variants_and_never_defaults_one():
    """The four names are data the UI reads, not a list it carries."""
    schema = PORT.get_schema(BASIS, None)
    assert schema['options']['implementationVariants'] == sleeves.VARIANTS
    assert len(sleeves.VARIANTS) == 4
    assert 'PMG Multi-Asset Portfolio' in sleeves.VARIANTS


@pytest.mark.parametrize('variant', sleeves.VARIANTS)
def test_every_variant_carries_exactly_one_auto_sleeve(variant):
    """Spec 2.6 has to hold under all four, or the auto-attach that never
    blocks the gate would block it for whichever variant lacks the sleeve."""
    for category in rules.AUTO_SLEEVE_CATEGORIES:
        assert len(sleeves.listSleeves(category, variant)) == 1


@pytest.mark.parametrize('variant', sleeves.VARIANTS)
def test_every_variant_covers_every_category_in_the_universe(variant):
    """Not a rule the code enforces - a variant reaching no sleeve for a held
    category is a legitimate state the gate reports. This pins what the stub
    data currently is, so replacing it with PMG's own source has to be a
    deliberate act rather than a silent hole in the completeness gate."""
    for category in rules.categoriesInUniverseOrder():
        assert sleeves.listSleeves(category, variant), \
            '{} offers no sleeve for {}'.format(variant, category)


def test_an_unchosen_variant_lists_nothing_rather_than_defaulting():
    """The failure that matters: quietly serving one book's products to
    another. None and nonsense both have to come back empty, never as the
    Multi-Asset library."""
    for bad in (None, '', 'Multi-Asset', 'PMG  ESG'):
        assert sleeves.listSleeves('Public Equity', bad) == []
        assert not sleeves.sleeveExists('Public Equity', 'Passive', bad)


def test_sleeve_names_are_scoped_to_their_variant():
    """A UCITS sleeve is not attachable in a US Onshore book."""
    assert sleeves.sleeveExists('Public Equity', 'UCITS Core Equity', 'Irish Onshore')
    assert not sleeves.sleeveExists('Public Equity', 'UCITS Core Equity', 'US Onshore')
    assert sleeves.sleeveExists('Public Equity', 'Concentrated Active', 'US Onshore')
    assert not sleeves.sleeveExists('Public Equity', 'Concentrated Active', 'Irish Onshore')


def test_variants_differ_in_what_they_offer_and_in_what_a_sleeve_contains():
    """Both halves of the requirement: a restricted set of sleeves, and the
    same category resolving to different products."""
    offered = {v: {s['name'] for s in sleeves.listSleeves('Public Equity', v)}
               for v in sleeves.VARIANTS}
    assert offered['PMG ESG'] != offered['PMG Multi-Asset Portfolio']
    assert offered['Irish Onshore'] != offered['US Onshore']

    def products(variant, sleeveName):
        found = next(s for s in sleeves.listSleeves('Public Equity', variant)
                     if s['name'] == sleeveName)
        return {p['name'] for p in found['products']}

    # 'Passive' exists under both, and is not the same sleeve.
    assert products('PMG Multi-Asset Portfolio', 'Passive') \
        != products('Irish Onshore', 'UCITS Passive')


def test_validateVariant_rejects_absent_and_unknown():
    with pytest.raises(ValidationError) as caught:
        rules.validateVariant(None)
    assert caught.value.field == 'variant'
    with pytest.raises(ValidationError):
        rules.validateVariant('PMG Offshore')
    rules.validateVariant(sleeves.VARIANTS[-1])


def test_changing_variant_clears_the_sleeve_map(tmp_path, monkeypatch):
    """Sleeve names only mean something under the variant they came from, so
    the store drops them in the same write rather than leaving a map that
    validates against nothing."""
    from cyrus_pmg.pmgService.scenario import scenarioStore
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path))
    mandate = MandateInput(48.5e6, 26e6, 'M. Aldridge — Zurich')
    state = scenarioStore.createScenario(mandate, BASIS)
    assert state['variant'] is None

    scenarioStore.updateScenario(state['id'], variant='US Onshore')
    scenarioStore.updateScenario(state['id'], sleeves={'Public Equity': 'Passive'})
    assert scenarioStore.getScenario(state['id'])['sleeves'] == {'Public Equity': 'Passive'}

    after = scenarioStore.updateScenario(state['id'], variant='Irish Onshore')
    assert after['variant'] == 'Irish Onshore'
    assert after['sleeves'] == {}

    # Re-stating the same variant is not a change and keeps the map.
    scenarioStore.updateScenario(after['id'], sleeves={'Public Equity': 'UCITS Passive'})
    again = scenarioStore.updateScenario(after['id'], variant='Irish Onshore')
    assert again['sleeves'] == {'Public Equity': 'UCITS Passive'}


@pytest.mark.parametrize('variant', sleeves.VARIANTS)
def test_workbook_records_the_variant_it_was_built_from(tmp_path, variant):
    from openpyxl import load_workbook
    key = PortfolioKey('Core', True, False, 'Mod')
    result = PORT.resolve_portfolio(BASIS, key)
    chosen = {c['name']: sleeves.listSleeves(c['name'], variant)[0]['name']
              for c in result['categories']
              if c['name'] not in rules.AUTO_SLEEVE_CATEGORIES}
    mandate = MandateInput(48.5e6, 26e6, 'M. Aldridge — Zurich')
    path = tmp_path / 'wb.xlsx'
    path.write_bytes(PORT.build_export(BASIS, mandate, [result],
                                       {'sleeves': chosen, 'variant': variant}))
    rows = list(load_workbook(path)['Implementation'].iter_rows(values_only=True))
    assert rows[0][:2] == ('Implementation variant', variant)
    weights = [r[2] for r in rows
               if r[2] is not None and r[0] and str(r[0]).startswith('  ')]
    assert abs(sum(weights) * 100 - 100.0) < 1e-9


# ------------------------------------------------ portfolio naming (D36) ----

def test_portfolio_name_is_currency_risk_allocation_exclusions():
    """Order is fixed and the risk level prints as its display label, so a name
    reads the way the rail reads. The KEY is unaffected - it still carries the
    short risk value, which is what the availability set and the bake are
    keyed on."""
    basis = BasisInput(currency='USD', hedging='Hedged')
    cases = [
        (PortfolioKey('Core', True, False, 'Mod Agg'), 'USD Moderate-Aggressive Core'),
        (PortfolioKey('Full', False, True, 'Cons'), 'USD Conservative Full ex TAA'),
        (PortfolioKey('Ex HFs', True, True, 'All Equity'),
         'USD All Equity Ex HFs ex RE ex TAA'),
        (PortfolioKey('Low Vol' and 'Ex Alts', True, False, 'Low Vol'),
         'USD Low Vol Ex Alts'),
    ]
    for key, expected in cases:
        assert rules.portfolioName(basis, key) == expected
        assert rules.portfolioHeader(key) == expected[len('USD '):]
    # 'ex RE' is only stated where the allocation could have held real estate
    assert 'ex RE' not in rules.portfolioHeader(
        PortfolioKey('Core', True, False, 'Mod'))


def test_risk_level_labels_cover_every_value_and_leave_keys_alone():
    for value in rules.RISK_LEVELS:
        assert value in rules.RISK_LEVEL_LABELS, value
    # the key is built from the value, never the label
    key = PortfolioKey('Core', True, False, 'Mod Agg')
    assert key.toStr().endswith('|Mod Agg')
    assert 'Moderate' not in key.toStr()
