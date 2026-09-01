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

from cyrus_pmg.pmgService.scenario import fees, rules, sleeves
from cyrus_pmg.pmgService.scenario.fixturesAdapter import FixturesScenarioPort
from cyrus_pmg.pmgService.scenario.payloads import roundWeightsLargestRemainder
from cyrus_pmg.pmgService.scenario.types import BasisInput, MandateInput, PortfolioKey, ValidationError
from cyrus_pmg.pmgService.scenario.workbook import (
    FEE_COLUMNS, IMPL_COLUMNS, buildImplementationRows, implColumns)

BASIS = BasisInput(currency='USD', hedging='Hedged')
PORT = FixturesScenarioPort()


def _allKeys(currency='USD'):
    schema = PORT.get_schema(BasisInput(currency=currency, hedging='Hedged'), None)
    return [PortfolioKey.fromStr(k) for k in schema['availability']]


# ---------------------------------------------------------------- schema ----

def test_availability_matches_supplied_universe():
    schema = PORT.get_schema(BASIS, None)
    assert len(schema['availability']) == 34          # spec 4.5, D50
    assert schema['categories'][0] == 'Investment Grade Fixed Income'
    assert len(schema['categories']) == 7


def test_twenty_million_rule_removes_private_allocations():
    small = MandateInput(topAccountSize=15e6, mandateSize=10e6, primaryPwa='')
    schema = PORT.get_schema(BASIS, small)
    assert schema['options']['allocations'] == ['Core', 'Ex Alts']
    assert len(schema['availability']) == 12
    assert all(not k.startswith(('Full|', 'Ex HFs|')) for k in schema['availability'])


def test_variant_restricts_the_allocations_offered():
    """The variant decides what can be built, not only how it is implemented
    (D49). Onshore books hold no alternatives; ESG holds no real estate."""
    offered = {}
    for variant in rules.IMPLEMENTATION_VARIANTS:
        offered[variant] = PORT.get_schema(BASIS, None, variant)['options']['allocations']
    assert offered['PMG Multi-Asset Portfolio'] == ['Full', 'Core', 'Ex HFs', 'Ex Alts']
    assert offered['PMG ESG'] == ['Ex HFs', 'Ex Alts']
    assert offered['US Onshore'] == ['Ex Alts']
    assert offered['Irish Onshore'] == ['Ex Alts']
    # no variant applies no filter: the schema is fetched before one is chosen
    assert PORT.get_schema(BASIS, None)['options']['allocations'] == \
        ['Full', 'Core', 'Ex HFs', 'Ex Alts']


def test_variant_narrows_the_availability_set():
    """The availability set is the authority the picker selects against, so
    the restriction has to reach it and not merely the option list."""
    full = PORT.get_schema(BASIS, None, 'PMG Multi-Asset Portfolio')['availability']
    onshore = PORT.get_schema(BASIS, None, 'US Onshore')['availability']
    esg = PORT.get_schema(BASIS, None, 'PMG ESG')['availability']
    assert len(full) == 34
    assert len(onshore) == 5 and all(k.startswith('Ex Alts|') for k in onshore)
    # ESG keeps Ex HFs only where real estate is already excluded
    assert len(esg) == 11
    for keyStr in esg:
        key = PortfolioKey.fromStr(keyStr)
        assert key.allocation in ('Ex HFs', 'Ex Alts')
        if key.allocation in rules.RE_ALLOWED:
            assert key.excludeRE, keyStr


def test_risk_levels_stay_data_driven_under_a_variant():
    """Risk is not restricted by variant - Ex Alts simply has no Cons or Low
    Vol rows in the supplied weights, so the ladder narrows on its own (4.5)."""
    onshore = PORT.get_schema(BASIS, None, 'US Onshore')['availability']
    levels = sorted({PortfolioKey.fromStr(k).riskLevel for k in onshore})
    assert levels == ['Agg', 'All Equity', 'Cons Mod', 'Mod', 'Mod Agg']
    assert 'All Equity' in rules.RISK_LEVELS         # a risk level, not an allocation


def test_variant_is_enforced_server_side():
    """The picker cannot offer these, but a caller that is not the picker can
    still ask for them - the variant governs which products a client may be
    shown at all, so it is checked again here."""
    full = PortfolioKey(allocation='Full', excludeRE=False,
                        excludeTAA=True, riskLevel='Mod')
    with pytest.raises(ValidationError) as exc:
        rules.validateKey(full, 'US Onshore')
    assert exc.value.field == 'allocation'

    with pytest.raises(ValidationError) as exc:
        rules.validateKey(full, None)
    assert exc.value.field == 'variant'

    withRE = PortfolioKey(allocation='Ex HFs', excludeRE=False,
                          excludeTAA=True, riskLevel='Mod')
    with pytest.raises(ValidationError) as exc:
        rules.validateKey(withRE, 'PMG ESG')
    assert exc.value.field == 'excludeRE'
    # the same allocation is fine once the exclusion is on
    withRE = PortfolioKey(allocation='Ex HFs', excludeRE=True,
                          excludeTAA=True, riskLevel='Mod')
    rules.validateKey(withRE, 'PMG ESG')


def test_variant_and_mandate_filters_compose():
    """Both are subtractive and independent; every variant offers Ex Alts,
    which holds no private assets, so no intersection is empty."""
    small = MandateInput(topAccountSize=15e6, mandateSize=10e6, primaryPwa='')
    esg = PORT.get_schema(BASIS, small, 'PMG ESG')['options']['allocations']
    assert esg == ['Ex Alts']                 # Ex HFs is a private-asset allocation
    for variant in rules.IMPLEMENTATION_VARIANTS:
        assert rules.allocationsFor(10e6, variant), variant


def test_columns_the_new_variant_cannot_build_are_named():
    """What the store prunes on a variant change (D49)."""
    keys = ['Full|0|1|Mod', 'Ex Alts|1|1|Mod']
    assert rules.keysInvalidForVariant(keys, 'US Onshore') == ['Full|0|1|Mod']
    assert rules.keysInvalidForVariant(keys, 'PMG Multi-Asset Portfolio') == []


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
    """All 34 x 4 currencies resolve; every number on every surface finite."""
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


def test_no_strategic_allocation_carries_tactical():
    """Tactical allocation is an implementation concept (D50). The weights
    still hold both halves of the axis; the offered set is only the ex-TAA
    half, and the server refuses the other even if asked directly."""
    for keyStr in rules.availability(BASIS, None):
        assert PortfolioKey.fromStr(keyStr).excludeTAA, keyStr
    for result in (PORT.resolve_portfolio(BASIS, PortfolioKey('Core', True, True, 'Mod')),):
        assert rules.TACTICAL_TILT_CATEGORY not in [c['name'] for c in result['categories']]
    with pytest.raises(ValidationError) as exc:
        rules.validateKey(PortfolioKey('Core', True, False, 'Mod'),
                          'PMG Multi-Asset Portfolio')
    assert exc.value.field == 'excludeTAA'


def test_tactical_tilt_moves_weight_it_does_not_create_it():
    """8% out of investment grade fixed income, pro rata across its assets,
    into the tilt category. The book still adds to 100."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('Core', True, True, 'Mod'))
    before = result['categories']
    after = rules.tiltedCategories(before, True)
    byName = lambda cats: {c['name']: c['weightPct'] for c in cats}
    src = rules.TACTICAL_TILT_FUNDED_FROM
    assert byName(after)[src] == pytest.approx(byName(before)[src] - rules.TACTICAL_TILT_PCT)
    assert byName(after)[rules.TACTICAL_TILT_CATEGORY] == rules.TACTICAL_TILT_PCT
    assert sum(byName(after).values()) == pytest.approx(100.0)
    # every other category is untouched
    for name, weight in byName(before).items():
        if name != src:
            assert byName(after)[name] == pytest.approx(weight)
    # the funding category's own assets scale in proportion
    srcBefore = next(c for c in before if c['name'] == src)
    srcAfter = next(c for c in after if c['name'] == src)
    share = srcAfter['weightPct'] / srcBefore['weightPct']
    for a, b in zip(srcBefore['assets'], srcAfter['assets']):
        assert b['weightPct'] == pytest.approx(a['weightPct'] * share)


def test_tactical_tilt_is_on_by_default():
    """House practice is to hold the tilt wherever it can be funded (D50), so
    a new scenario starts with it on. A portfolio that cannot fund it needs no
    separate default: the toggle renders off and disabled, and tiltedCategories
    no-ops, so the one default serves both cases. An explicit off must stick."""
    from cyrus_pmg.pmgService.scenario import scenarioStore
    mandate = MandateInput(48.5e6, 26e6, 'M. Aldridge — Zurich')
    state = scenarioStore.createScenario(mandate, BASIS)
    assert state['tacticalTilt'] is True

    off = scenarioStore.updateScenario(state['id'], tacticalTilt=False)
    assert off['tacticalTilt'] is False
    # an unrelated write must not resurrect the default
    after = scenarioStore.updateScenario(state['id'], variant='PMG Multi-Asset Portfolio')
    assert after['tacticalTilt'] is False


def test_tactical_tilt_is_refused_where_it_cannot_be_funded():
    """All Equity holds no investment grade fixed income at all, so there is
    nothing to fund the tilt from and it is a no-op rather than a book that
    does not add to 100 (D50)."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('Core', True, True, 'All Equity'))
    assert not rules.canFundTacticalTilt(result['categories'])
    after = rules.tiltedCategories(result['categories'], True)
    assert [c['name'] for c in after] == [c['name'] for c in result['categories']]
    assert sum(c['weightPct'] for c in after) == pytest.approx(100.0)


def test_tactical_tilt_never_mutates_the_payload_it_is_given():
    """Payloads are cached in the adapters and read straight off the baked
    slices, so tilting one must not change the portfolio for the next reader."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('Core', True, True, 'Mod'))
    snapshot = [(c['name'], c['weightPct']) for c in result['categories']]
    rules.tiltedCategories(result['categories'], True)
    assert [(c['name'], c['weightPct']) for c in result['categories']] == snapshot


def test_tilted_implementation_still_sums_to_one_hundred():
    """The rounding guarantee of spec 8.4 has to survive the tilt."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('Core', True, True, 'Mod'))
    variant = sleeves.VARIANTS[0]
    chosen = {c['name']: sleeves.listSleeves(c['name'], variant)[0]['name']
              for c in rules.tiltedCategories(result['categories'], True)
              if c['name'] not in rules.AUTO_SLEEVE_CATEGORIES}
    model = buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES,
                                    26_000_000, variant, True)
    assert model['complete']
    assert model['total']['weightPct'] == pytest.approx(100.0)
    assert rules.TACTICAL_TILT_CATEGORY in [g['category'] for g in model['groups']]


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


TOP_ACCOUNT = 48.5e6      # tier T3 under the placeholder tiers


def _implementationFor(key, sleeveChoice=0, mandateSize=26_000_000,
                       variant=sleeves.VARIANTS[0], feeSchedule='RDR',
                       feeLevel=None, topAccountSize=TOP_ACCOUNT,
                       volPremium=False, currency=None):
    result = PORT.resolve_portfolio(BASIS, key)
    chosen = {}
    for category in result['categories']:
        name = category['name']
        if name in rules.AUTO_SLEEVE_CATEGORIES:
            continue
        library = sleeves.listSleeves(name, variant)
        chosen[name] = library[sleeveChoice % len(library)]['name']
    return buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES,
                                   mandateSize, variant, False,
                                   feeSchedule, feeLevel, topAccountSize,
                                   volPremium, currency)


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
            # the fee is resolved, never carried (D51)
            assert item['managementFee'] == fees.managementFee(
                'RDR', TOP_ACCOUNT, fees.DEFAULT_LEVEL, item['feeGroup'])
            allIn = item['productCost'] + item['managementFee']
            assert abs(item['wtdFeeBp'] - allIn * item['printedPct']) < 1e-9
        total = model['total']
        assert abs(total['weightPct'] - 100.0) < 1e-9
        assert abs(total['wtdFeeBp'] - sum(i['wtdFeeBp'] for i in items)) < 1e-9


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
                                {'sleeves': chosen, 'variant': sleeves.VARIANTS[0],
                                 'feeSchedule': 'CASP', 'feeLevel': 'PMG Target'})
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
    notional = IMPL_COLUMNS.index('Notional')
    notionals = [r[notional] for r in rows_
                 if r[notional] is not None and r[0] and str(r[0]).startswith('  ')]
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


# ------------------------------------ the management fee framework (D51) ----

def _assetRows(rows):
    return [r for r in rows if r[0] and str(r[0]).startswith('  ')]


def test_tiers_are_half_open_and_exhaustive():
    """[min, max): a size on a boundary belongs to the tier above it, the
    first tier starts at zero and the last has no ceiling. Nothing between
    zero and infinity is unpriced, and nothing else is priced."""
    assert fees.tierFor(0)['id'] == fees.TIERS[0]['id']
    for earlier, later in zip(fees.TIERS, fees.TIERS[1:]):
        edge = earlier['max']
        assert fees.tierFor(edge - 0.01)['id'] == earlier['id']
        assert fees.tierFor(edge)['id'] == later['id']
    assert fees.tierFor(10 ** 13)['id'] == fees.TIERS[-1]['id']
    for bad in (None, -1, 'big', True):
        with pytest.raises(ValueError):
            fees.tierFor(bad)


def test_casp_prices_every_group_the_same():
    """One rate per tier and level, whatever the product's group - and the
    group may be omitted altogether."""
    for tier in fees.TIERS:
        size = tier['min']
        for level in fees.LEVELS:
            uniform = fees.managementFee('CASP', size, level)
            for group in fees.FEE_GROUPS + ['not a group']:
                assert fees.managementFee('CASP', size, level, group) == uniform


def test_rdr_prices_by_group_and_requires_one():
    for tier in fees.TIERS:
        size = tier['min']
        for level in fees.LEVELS:
            byGroup = {g: fees.managementFee('RDR', size, level, g) for g in fees.FEE_GROUPS}
            assert len(set(byGroup.values())) > 1, (tier['id'], level)
    with pytest.raises(KeyError):
        fees.managementFee('RDR', 1e6, fees.DEFAULT_LEVEL)
    with pytest.raises(KeyError):
        fees.managementFee('RDR', 1e6, fees.DEFAULT_LEVEL, 'not a group')


def test_levels_run_floor_to_ceiling_and_tiers_run_downhill():
    """A level is a point on a band, so floor <= target <= ceiling at every
    cell; and a bigger account never pays a higher rate than a smaller one."""
    schedules = [('CASP', [None]), ('RDR', fees.FEE_GROUPS)]
    for schedule, groups in schedules:
        for group in groups:
            for source in fees.SOURCES:
                previous = None
                for tier in fees.TIERS:
                    size = tier['min']
                    band = [fees.managementFee(schedule, size, fees.levelId(source, p), group)
                            for p in ('Floor', 'Target', 'Ceiling')]
                    assert band[0] <= band[1] <= band[2], (schedule, group, tier['id'], source)
                    if previous is not None:
                        assert band[1] <= previous, (schedule, group, tier['id'], source)
                    previous = band[1]


def test_unknown_schedule_and_level_raise_rather_than_default():
    with pytest.raises(KeyError):
        fees.managementFee('Flat', 1e6, fees.DEFAULT_LEVEL)
    with pytest.raises(KeyError):
        fees.managementFee('CASP', 1e6, 'PMG Middle')
    with pytest.raises(KeyError):
        fees.splitLevel('Target')
    assert fees.splitLevel('PMG Target') == ('PMG', 'Target')
    assert fees.DEFAULT_LEVEL in fees.LEVELS
    assert len(fees.LEVELS) == 6


def test_every_product_carries_a_priced_fee_group_and_no_fee():
    """The fee left the product (D51): what remains is the group the RDR
    schedule reads, and every group is one the framework prices."""
    seen = set()
    for variant, library in sleeves.SLEEVE_LIBRARY.items():
        for category, offered in library.items():
            for sleeve in offered:
                for product in sleeve['products']:
                    assert 'managementFee' not in product, (variant, product['name'])
                    assert product['feeGroup'] in fees.FEE_GROUPS, (variant, product['name'])
                    seen.add(product['feeGroup'])
    assert seen == set(fees.FEE_GROUPS), 'every group should be exercised by some product'


def test_fee_schedule_has_no_default_and_the_level_has_the_prescribed_one(tmp_path, monkeypatch):
    from cyrus_pmg.pmgService.scenario import scenarioStore
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path))
    state = scenarioStore.createScenario(MandateInput(48.5e6, 26e6, 'M. Aldridge — Zurich'), BASIS)
    assert state['feeSchedule'] is None
    assert state['feeLevel'] == fees.DEFAULT_LEVEL

    after = scenarioStore.updateScenario(state['id'], feeSchedule='CASP', feeLevel='Management Floor')
    assert (after['feeSchedule'], after['feeLevel']) == ('CASP', 'Management Floor')
    # an unrelated write keeps both
    again = scenarioStore.updateScenario(state['id'], variant='US Onshore')
    assert (again['feeSchedule'], again['feeLevel']) == ('CASP', 'Management Floor')


def test_validate_fee_schedule_and_level_reject_absent_and_unknown():
    for bad in (None, '', 'Flat'):
        with pytest.raises(ValidationError) as caught:
            rules.validateFeeSchedule(bad)
        assert caught.value.field == 'feeSchedule'
    for bad in (None, '', 'PMG Middle', 'Target'):
        with pytest.raises(ValidationError) as caught:
            rules.validateFeeLevel(bad)
        assert caught.value.field == 'feeLevel'
    for schedule in fees.SCHEDULES:
        rules.validateFeeSchedule(schedule)
    for level in fees.LEVELS:
        rules.validateFeeLevel(level)


def test_schema_carries_the_fee_framework_at_the_mandates_tier():
    """Schema is data (spec 4.3): the client prices from what it is served,
    at the tier the top account size falls in, and is served nothing to price
    from until there is a top account size."""
    schema = PORT.get_schema(BASIS, MandateInput(TOP_ACCOUNT, 26e6, ''))
    block = schema['fees']
    assert block['placeholder'] is fees.PLACEHOLDER
    assert [s['id'] for s in block['schedules']] == fees.SCHEDULES
    assert [l['id'] for l in block['levels']] == fees.LEVELS
    assert block['defaultLevel'] == fees.DEFAULT_LEVEL
    assert block['tier']['id'] == fees.tierFor(TOP_ACCOUNT)['id']
    rates = block['rates']
    assert rates['CASP']['byGroup'] is False
    assert set(rates['CASP']['levels']) == set(fees.LEVELS)
    assert rates['RDR']['byGroup'] is True
    assert set(rates['RDR']['groups']) == set(fees.FEE_GROUPS)
    for level in fees.LEVELS:
        assert rates['CASP']['levels'][level] == fees.managementFee('CASP', TOP_ACCOUNT, level)
        for group in fees.FEE_GROUPS:
            assert rates['RDR']['groups'][group][level] == \
                fees.managementFee('RDR', TOP_ACCOUNT, level, group)

    # the tier follows the TOP account size, not the mandate size
    other = PORT.get_schema(BASIS, MandateInput(5e6, 5e6, ''))['fees']
    assert other['tier']['id'] != block['tier']['id']

    bare = PORT.get_schema(BASIS, None)['fees']
    assert bare['tier'] is None and bare['rates'] is None
    assert [s['id'] for s in bare['schedules']] == fees.SCHEDULES


def test_implementation_fee_is_resolved_from_the_schedule_not_the_product():
    key = PortfolioKey('Core', True, False, 'Mod')
    casp = _implementationFor(key, feeSchedule='CASP')
    items = [i for g in casp['groups'] for i in g['items']]
    assert casp['priced'] and casp['tier']['id'] == fees.tierFor(TOP_ACCOUNT)['id']
    assert len({i['managementFee'] for i in items}) == 1

    rdr = _implementationFor(key, feeSchedule='RDR', feeLevel='Management Ceiling')
    for item in (i for g in rdr['groups'] for i in g['items']):
        assert item['managementFee'] == fees.managementFee(
            'RDR', TOP_ACCOUNT, 'Management Ceiling', item['feeGroup'])
    assert len({i['managementFee'] for g in rdr['groups'] for i in g['items']}) > 1

    # a different tier re-prices every row without touching a weight
    small = _implementationFor(key, feeSchedule='CASP', topAccountSize=5e6)
    smallItems = [i for g in small['groups'] for i in g['items']]
    assert [i['printedPct'] for i in smallItems] == [i['printedPct'] for i in items]
    assert smallItems[0]['managementFee'] > items[0]['managementFee']

    unpriced = _implementationFor(key, feeSchedule=None)
    assert unpriced['priced'] is False and unpriced['tier'] is None
    assert all(i['managementFee'] is None and i['wtdFeeBp'] is None
               for g in unpriced['groups'] for i in g['items'])
    assert unpriced['total']['wtdFeeBp'] is None
    assert unpriced['total']['weightPct'] == pytest.approx(100.0)

    with pytest.raises(ValueError):          # a schedule with no account size is an error
        _implementationFor(key, feeSchedule='CASP', topAccountSize=None)


@pytest.mark.parametrize('schedule', fees.SCHEDULES)
def test_workbook_records_the_pricing_it_was_built_from(tmp_path, schedule):
    """Every fee on the sheet was resolved from the schedule, the level and the
    tier, so the sheet says which - and the fee group column lets a reader
    re-price any row against the published table."""
    from openpyxl import load_workbook
    key = PortfolioKey('Core', True, False, 'Mod')
    result = PORT.resolve_portfolio(BASIS, key)
    variant = sleeves.VARIANTS[0]
    chosen = {c['name']: sleeves.listSleeves(c['name'], variant)[0]['name']
              for c in result['categories']
              if c['name'] not in rules.AUTO_SLEEVE_CATEGORIES}
    mandate = MandateInput(TOP_ACCOUNT, 26e6, 'M. Aldridge — Zurich')
    path = tmp_path / 'wb.xlsx'
    path.write_bytes(PORT.build_export(BASIS, mandate, [result], {
        'sleeves': chosen, 'variant': variant,
        'feeSchedule': schedule, 'feeLevel': 'PMG Floor'}))
    rows = list(load_workbook(path)['Implementation'].iter_rows(values_only=True))
    tier = fees.tierFor(TOP_ACCOUNT)
    assert rows[0][:2] == ('Implementation variant', variant)
    assert rows[1][:2] == ('Fee schedule', schedule)
    assert rows[2][:2] == ('Fee level', 'PMG Floor')
    assert rows[3][:2] == ('Account size tier', '{} ({})'.format(tier['id'], tier['label']))
    assert rows[4] == (None,) * len(IMPL_COLUMNS)
    assert rows[5] == tuple(IMPL_COLUMNS)
    group, mgmt, bp = (IMPL_COLUMNS.index(c) for c in ('Fee group', 'Mgmt fee', 'Wtd fee (bp)'))
    assets = _assetRows(rows)
    assert assets
    for row in assets:
        assert row[group] in fees.FEE_GROUPS
        expected = fees.managementFee(schedule, TOP_ACCOUNT, 'PMG Floor', row[group])
        assert row[mgmt] == pytest.approx(expected / 100.0)
        assert row[bp] == pytest.approx((row[9] * 100 + expected) * row[2] * 100)


def test_unpriced_workbook_leaves_the_fee_cells_empty(tmp_path):
    from openpyxl import load_workbook
    key = PortfolioKey('Core', True, False, 'Mod')
    result = PORT.resolve_portfolio(BASIS, key)
    variant = sleeves.VARIANTS[0]
    chosen = {c['name']: sleeves.listSleeves(c['name'], variant)[0]['name']
              for c in result['categories']
              if c['name'] not in rules.AUTO_SLEEVE_CATEGORIES}
    path = tmp_path / 'wb.xlsx'
    path.write_bytes(PORT.build_export(BASIS, MandateInput(TOP_ACCOUNT, 26e6, ''), [result],
                                       {'sleeves': chosen, 'variant': variant}))
    rows = list(load_workbook(path)['Implementation'].iter_rows(values_only=True))
    assert [r[0] for r in rows[:3]] == ['Implementation variant', None, 'Categories & Asset Classes']
    mgmt, bp = IMPL_COLUMNS.index('Mgmt fee'), IMPL_COLUMNS.index('Wtd fee (bp)')
    assert all(r[mgmt] is None and r[bp] is None for r in _assetRows(rows))


# ---- the strategic volatility premium (D53) ----

def _categoriesFor(tilt=True, volPremium=True, currency='USD',
                   key=PortfolioKey('Core', True, True, 'Mod')):
    result = PORT.resolve_portfolio(BasisInput(currency, 'Hedged'), key)
    return rules.implementedCategories(result['categories'], tilt, volPremium, currency)


def _weight(categories, name):
    for category in categories:
        if category['name'] == name:
            return category['weightPct']
    return None


def test_the_premium_takes_its_share_of_what_the_tilt_leaves():
    """The share is of the funding category AS IMPLEMENTED, so the tilt comes
    out first and the premium takes 7.5% of the remainder (D53)."""
    tilted = _categoriesFor(tilt=True, volPremium=False)
    both = _categoriesFor(tilt=True, volPremium=True)
    afterTilt = _weight(tilted, rules.VOL_PREMIUM_FUNDED_FROM)
    premium = _weight(both, rules.VOL_PREMIUM_CATEGORY)
    assert premium == pytest.approx(afterTilt * rules.VOL_PREMIUM_SHARE)
    # and the funding category gave up exactly that, no more
    assert _weight(both, rules.VOL_PREMIUM_FUNDED_FROM) == pytest.approx(afterTilt - premium)

    # without the tilt the base is larger, so the premium is larger
    alone = _categoriesFor(tilt=False, volPremium=True)
    assert _weight(alone, rules.VOL_PREMIUM_CATEGORY) > premium
    assert _weight(alone, rules.VOL_PREMIUM_CATEGORY) == pytest.approx(
        _weight(_categoriesFor(tilt=False, volPremium=False),
                rules.VOL_PREMIUM_FUNDED_FROM) * rules.VOL_PREMIUM_SHARE)


def test_the_premium_moves_weight_it_does_not_create_it():
    """Weight comes out of the funding category's own products pro rata, so
    the book still adds to 100 and their relative sizes are untouched."""
    before = _categoriesFor(tilt=True, volPremium=False)
    after = _categoriesFor(tilt=True, volPremium=True)
    assert sum(c['weightPct'] for c in after) == pytest.approx(100.0)
    assert sum(c['weightPct'] for c in before) == pytest.approx(100.0)

    fundingBefore = [c for c in before if c['name'] == rules.VOL_PREMIUM_FUNDED_FROM][0]
    fundingAfter = [c for c in after if c['name'] == rules.VOL_PREMIUM_FUNDED_FROM][0]
    assert len(fundingAfter['assets']) == len(fundingBefore['assets'])
    scale = 1.0 - rules.VOL_PREMIUM_SHARE
    for was, now in zip(fundingBefore['assets'], fundingAfter['assets']):
        assert now['weightPct'] == pytest.approx(was['weightPct'] * scale)
    # every other category is untouched
    for category in after:
        if category['name'] in (rules.VOL_PREMIUM_FUNDED_FROM, rules.VOL_PREMIUM_CATEGORY):
            continue
        assert category['weightPct'] == pytest.approx(_weight(before, category['name']))


def test_the_premium_sits_under_its_funding_category():
    """Under Investment Grade Fixed Income and before Other Fixed Income, which
    is the position the sheet reads it in - not appended like the tilt."""
    names = [c['name'] for c in _categoriesFor()]
    assert names.index(rules.VOL_PREMIUM_CATEGORY) == \
        names.index(rules.VOL_PREMIUM_FUNDED_FROM) + 1
    assert names.index(rules.VOL_PREMIUM_CATEGORY) < names.index('Other Fixed Income')


@pytest.mark.parametrize('currency,allowed', [
    ('USD', True), ('GBP', True), ('EUR', False), ('CHF', False), (None, False)])
def test_the_premium_is_forbidden_outside_its_currencies(currency, allowed):
    """The product may not be held in any other currency, so the rule is
    applied where the model is built - a stale flag cannot smuggle it in."""
    assert rules.canHoldVolPremium(currency) is allowed
    if currency in ('EUR', 'CHF'):
        categories = _categoriesFor(currency=currency)
        assert rules.VOL_PREMIUM_CATEGORY not in [c['name'] for c in categories]
        assert _weight(categories, rules.VOL_PREMIUM_FUNDED_FROM) == pytest.approx(
            _weight(_categoriesFor(volPremium=False, currency=currency),
                    rules.VOL_PREMIUM_FUNDED_FROM))


def test_the_premium_never_mutates_the_payload_it_is_given():
    """Payloads are cached and shared, like the tilt's (D50)."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('Core', True, True, 'Mod'))
    before = json.dumps(result['categories'], sort_keys=True)
    rules.implementedCategories(result['categories'], True, True, 'USD')
    assert json.dumps(result['categories'], sort_keys=True) == before


def test_a_portfolio_with_nothing_to_fund_it_from_is_a_no_op():
    """All Equity holds no investment grade fixed income at all; the toggle is
    offered disabled and this is the same rule where the workbook is written."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('Core', True, True, 'All Equity'))
    categories = rules.implementedCategories(result['categories'], True, True, 'USD')
    assert rules.VOL_PREMIUM_CATEGORY not in [c['name'] for c in categories]
    assert sum(c['weightPct'] for c in categories) == pytest.approx(100.0)


def test_the_premium_product_is_priced_like_any_other():
    """It carries a fee group, so it prices under both schedules at every tier
    and level, and it reaches the implementation model as a line item (D53)."""
    library = sleeves.listSleeves(rules.VOL_PREMIUM_CATEGORY, sleeves.VARIANTS[0])
    assert len(library) == 1, 'auto-attached categories carry exactly one sleeve'
    products = library[0]['products']
    assert len(products) == 1
    product = products[0]
    assert product['name'] == 'Strategic Volatility Premium'
    assert product['feeGroup'] in fees.FEE_GROUPS
    assert (product['vehicle'], product['source'], product['liquidity'],
            product['exposureCurrency']) == ('Mutual Fund', 'Internal', 'Daily', 'USD')
    for schedule in fees.SCHEDULES:
        for level in fees.LEVELS:
            rate = fees.managementFee(schedule, TOP_ACCOUNT, level, product['feeGroup'])
            assert isinstance(rate, float) and rate >= 0

    # and every variant offers it, so the completeness gate can always be met
    for variant in sleeves.VARIANTS:
        assert sleeves.listSleeves(rules.VOL_PREMIUM_CATEGORY, variant), variant


def test_the_premium_reaches_the_implementation_model_and_the_sheet():
    key = PortfolioKey('Core', True, True, 'Mod')
    model = _implementationFor(key, feeSchedule='RDR', feeLevel='PMG Target',
                               volPremium=True, currency='USD')
    group = [g for g in model['groups'] if g['category'] == rules.VOL_PREMIUM_CATEGORY]
    assert len(group) == 1
    item = group[0]['items'][0]
    assert item['name'] == 'Strategic Volatility Premium'
    assert item['managementFee'] == fees.managementFee(
        'RDR', TOP_ACCOUNT, 'PMG Target', item['feeGroup'])
    assert item['notional'] > 0
    assert model['total']['weightPct'] == pytest.approx(100.0)
    # off, the category is not in the model at all
    without = _implementationFor(key, feeSchedule='RDR', feeLevel='PMG Target')
    assert rules.VOL_PREMIUM_CATEGORY not in [g['category'] for g in without['groups']]


def test_js_vol_premium_mirror_agrees_with_python():
    """The screen applies the overlay in JavaScript and the workbook in
    Python. Across the whole availability set, both toggles and a currency
    that may not hold it, the two must land on the same categories."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    jsPath = os.path.join(os.path.dirname(__file__), '..', '..',
                          'generator', 'js', 'implementation.js')
    with open(jsPath, encoding='utf-8') as fh:
        source = fh.read()
    fn = source[source.index('function volPremiumShare('):
                source.index('/* The categories AS IMPLEMENTED')]

    cases, expected = [], []
    for allocation, riskLevel in (('Core', 'Mod'), ('Full', 'Mod Agg'),
                                  ('Ex HFs', 'Cons'), ('Core', 'All Equity')):
        result = PORT.resolve_portfolio(
            BASIS, PortfolioKey(allocation, True, True, riskLevel))
        for tilt in (False, True):
            tilted = rules.tiltedCategories(result['categories'], tilt)
            for on in (False, True):
                cases.append([[{'name': c['name'], 'weightPct': c['weightPct'],
                                'assets': c['assets']} for c in tilted], on])
                expected.append(rules.volPremiumCategories(tilted, on, 'USD'))

    stub = ('var App = { opt: function (path, fallback) { return ({'
            "'rules.volPremiumShare': " + json.dumps(rules.VOL_PREMIUM_SHARE) + ','
            "'rules.volPremiumFundedFrom': " + json.dumps(rules.VOL_PREMIUM_FUNDED_FROM) + ','
            "'rules.volPremiumCategory': " + json.dumps(rules.VOL_PREMIUM_CATEGORY) + '})'
            '[path]; } };\n')
    script = stub + fn + '\nconst cases = ' + json.dumps(cases) + ';\n' \
        + 'process.stdout.write(JSON.stringify(cases.map(function (c) {\n' \
        + '  return volPremiumCategories(c[0], c[1]); })));\n'
    out = subprocess.run([node, '-e', script], capture_output=True, text=True, check=True)
    jsResults = json.loads(out.stdout)

    assert len(jsResults) == len(expected) >= 16
    for js, py in zip(jsResults, expected):
        assert [c['name'] for c in js] == [c['name'] for c in py]
        for a, b in zip(js, py):
            assert a['weightPct'] == pytest.approx(b['weightPct'], abs=1e-12)
            assert [x['weightPct'] for x in a['assets']] == pytest.approx(
                [x['weightPct'] for x in b['assets']], abs=1e-12)


# ---- excluding fees from the proposal (D52) ----

def _exported(tmp_path, implementation, mandate=None):
    """The Implementation sheet's rows for one implementation dict."""
    from openpyxl import load_workbook
    key = PortfolioKey('Core', True, False, 'Mod')
    result = PORT.resolve_portfolio(BASIS, key)
    variant = implementation.get('variant') or sleeves.VARIANTS[0]
    chosen = {c['name']: sleeves.listSleeves(c['name'], variant)[0]['name']
              for c in result['categories']
              if c['name'] not in rules.AUTO_SLEEVE_CATEGORIES}
    payload = dict({'sleeves': chosen, 'variant': variant}, **implementation)
    path = tmp_path / 'wb.xlsx'
    path.write_bytes(PORT.build_export(
        BASIS, mandate or MandateInput(TOP_ACCOUNT, 26e6, 'M. Aldridge — Zurich'),
        [result], payload))
    return list(load_workbook(path)['Implementation'].iter_rows(values_only=True))


def test_excluding_fees_takes_the_columns_off_the_sheet():
    """The three fee columns are the only difference, and they leave in one
    piece: nothing else about the sheet's shape depends on the toggle."""
    assert list(FEE_COLUMNS) == ['Fee group', 'Mgmt fee', 'Wtd fee (bp)']
    assert implColumns(True) == IMPL_COLUMNS
    assert implColumns(False) == [c for c in IMPL_COLUMNS if c not in FEE_COLUMNS]
    assert len(implColumns(False)) == len(IMPL_COLUMNS) - 3
    # the columns that survive keep their order and their neighbours
    assert implColumns(False)[-2:] == ['Cost', 'Notional']


def test_an_excluded_workbook_carries_no_fee_column_and_no_fee_header(tmp_path):
    """A proposal that does not show fees must not ship a sheet that names the
    schedule it was not priced at, nor three columns with nothing in them."""
    rows = _exported(tmp_path, {'includeFees': False,
                                'feeSchedule': 'RDR', 'feeLevel': 'PMG Target'})
    columns = implColumns(False)
    assert rows[0][:2] == ('Implementation variant', sleeves.VARIANTS[0])
    assert rows[1] == (None,) * len(columns)          # no fee schedule/level/tier rows
    assert rows[2] == tuple(columns)
    assert 'Fee group' not in rows[2] and 'Mgmt fee' not in rows[2]
    assets = _assetRows(rows)
    assert assets
    for row in assets:
        assert len(row) == len(columns)
        assert row[columns.index('Notional')] is not None


def test_the_same_scenario_priced_and_unpriced_agrees_on_everything_else(tmp_path):
    """Turning fees off is a change to what the sheet shows, never to what the
    model holds: the same weights, the same products, the same notionals."""
    priced = _assetRows(_exported(tmp_path, {
        'includeFees': True, 'feeSchedule': 'RDR', 'feeLevel': 'PMG Target'}))
    plain = _assetRows(_exported(tmp_path, {'includeFees': False}))
    assert len(priced) == len(plain)
    keep = [IMPL_COLUMNS.index(c) for c in implColumns(False)]
    for withFees, without in zip(priced, plain):
        assert [withFees[i] for i in keep] == list(without)


def test_a_new_scenario_starts_with_fees_excluded(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path))
    from cyrus_pmg.pmgService.scenario import scenarioStore as store
    state = store.createScenario(MandateInput(TOP_ACCOUNT, 26e6, 'M. Aldridge'), BASIS)
    assert state['includeFees'] is False
    assert state['feeSchedule'] is None          # and no schedule to price with
    on = store.updateScenario(state['id'], includeFees=True)
    assert on['includeFees'] is True
    # False is a value, not an omission: it has to survive the update path
    assert store.updateScenario(state['id'], includeFees=False)['includeFees'] is False
    assert store.getScenario(state['id'])['feeLevel'] == fees.DEFAULT_LEVEL


def test_a_scenario_stored_before_the_toggle_keeps_the_fees_it_was_showing():
    """Back-compat: absent means the field was never written. One that had
    chosen a schedule was showing fees and keeps showing them (D52)."""
    from cyrus_pmg.pmgService.dashboardRouter import _includeFees
    assert _includeFees({'feeSchedule': 'RDR'}) is True
    assert _includeFees({'feeSchedule': None}) is False
    assert _includeFees({}) is False
    # an explicit value always wins over the guess
    assert _includeFees({'includeFees': False, 'feeSchedule': 'RDR'}) is False
    assert _includeFees({'includeFees': True, 'feeSchedule': None}) is True


def test_js_fee_mirror_agrees_with_python():
    """The page resolves fees from the schema's rates block; the workbook
    resolves them from fees.json. Across every tier, schedule, level and group
    the two must land on the same number, and the page must answer null - not
    a rate - to anything the framework does not price."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    jsPath = os.path.join(os.path.dirname(__file__), '..', '..',
                          'generator', 'js', 'implementation.js')
    with open(jsPath, encoding='utf-8') as fh:
        source = fh.read()
    fn = source[source.index('function resolveFee('):source.index('function feeRates(')]

    cases, expected = [], []
    for tier in fees.TIERS:
        size = tier['min']
        rates = fees.ratesAtTier(tier['id'])
        for schedule in fees.SCHEDULES:
            for level in fees.LEVELS:
                for group in fees.FEE_GROUPS:
                    cases.append([rates, schedule, level, group])
                    expected.append(fees.managementFee(schedule, size, level, group))
                cases.append([rates, schedule, level, 'not a group'])
                expected.append(fees.managementFee('CASP', size, level)
                                if schedule == 'CASP' else None)
            cases.append([rates, schedule, 'PMG Middle', fees.FEE_GROUPS[0]])
            expected.append(None)
        cases.append([rates, 'Flat', fees.DEFAULT_LEVEL, fees.FEE_GROUPS[0]])
        expected.append(None)
    cases.append([None, 'CASP', fees.DEFAULT_LEVEL, fees.FEE_GROUPS[0]])
    expected.append(None)

    script = fn + '\nconst cases = ' + json.dumps(cases) + ';\n' \
        + 'process.stdout.write(JSON.stringify(cases.map(function (c) { ' \
        + 'return resolveFee(c[0], c[1], c[2], c[3]); })));\n'
    out = subprocess.run([node, '-e', script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == expected
    assert len(expected) > 300


# ------------------------------------------------ portfolio naming (D36) ----

def test_portfolio_name_is_currency_risk_allocation_exclusions():
    """Order is fixed and the risk level prints as its display label, so a name
    reads the way the rail reads. The KEY is unaffected - it still carries the
    short risk value, which is what the availability set and the bake are
    keyed on."""
    basis = BasisInput(currency='USD', hedging='Hedged')
    cases = [
        (PortfolioKey('Core', True, False, 'Mod Agg'), 'USD Moderate-Aggressive Core'),
        (PortfolioKey('Full', False, True, 'Cons'), 'USD Conservative Full'),
        (PortfolioKey('Ex HFs', True, True, 'All Equity'),
         'USD All Equity Ex HFs ex RE'),
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
