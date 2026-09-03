"""Safety net for the Proposal Tool back end.

The host dashboard package is untested and this adds nothing to it (spec 16
item 10); these tests live on the epsilon-phi side, where the brief invites a
net "around the fee and rounding arithmetic in section 8.4, where a silent
error is both most likely and most costly" - plus the finiteness guard of
section 15.8, because reconciliation tests pass happily on nonsense.

Run:  cd proposal-tool/service && PYTHONPATH=. python3 -m pytest tests -q
"""

import io
import json
import math
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cyrus_pmg.pmgService.scenario import fees, products, rules, sleeves
from cyrus_pmg.pmgService.scenario.fixturesAdapter import FixturesScenarioPort
from cyrus_pmg.pmgService.scenario.payloads import roundWeightsLargestRemainder
from cyrus_pmg.pmgService.scenario.types import BasisInput, MandateInput, PortfolioKey, ValidationError
from cyrus_pmg.pmgService.scenario.workbook import (
    FEE_COLUMNS, IMPL_COLUMNS, buildImplementationRows, implColumns,
    roundSharesOneDp)

BASIS = BasisInput(currency='USD', hedging='Hedged')
PORT = FixturesScenarioPort()


def _allKeys(currency='USD'):
    schema = PORT.get_schema(BasisInput(currency=currency, hedging='Hedged'), None)
    return [PortfolioKey.fromStr(k) for k in schema['availability']]


# ---------------------------------------------------------------- schema ----

def test_availability_matches_supplied_universe():
    schema = PORT.get_schema(BASIS, None)
    # 7 risk levels x (4 types + the two ex-RAs variants) + one all-equity book
    assert len(schema['availability']) == 43          # spec 4.5, D54
    assert schema['categories'][0] == 'Investment Grade Fixed Income'
    assert len(schema['categories']) == 7


def test_twenty_million_rule_removes_private_allocations():
    small = MandateInput(topAccountSize=15e6, mandateSize=10e6, primaryPwa='')
    schema = PORT.get_schema(BASIS, small)
    assert schema['options']['allocations'] == ['Core', 'ex-Alts']
    assert len(schema['availability']) == 15          # 7 x (Core, ex-Alts) + all-equity
    assert all('|Full|' not in k and '|ex-HFs|' not in k for k in schema['availability'])


def test_variant_restricts_the_allocations_offered():
    """The variant decides what can be built, not only how it is implemented
    (D49). Onshore books hold no alternatives; ESG holds no real estate."""
    offered = {}
    for variant in rules.IMPLEMENTATION_VARIANTS:
        offered[variant] = PORT.get_schema(BASIS, None, variant)['options']['allocations']
    assert offered['PMG Multi-Asset Portfolio'] == ['Full', 'Core', 'ex-Alts', 'ex-HFs']
    assert offered['PMG ESG'] == ['ex-Alts', 'ex-HFs']
    assert offered['US Onshore'] == ['ex-Alts']
    assert offered['Irish Onshore'] == ['ex-Alts']
    # no variant applies no filter: the schema is fetched before one is chosen
    assert PORT.get_schema(BASIS, None)['options']['allocations'] == \
        ['Full', 'Core', 'ex-Alts', 'ex-HFs']


def test_variant_narrows_the_availability_set():
    """The availability set is the authority the picker selects against, so
    the restriction has to reach it and not merely the option list."""
    full = PORT.get_schema(BASIS, None, 'PMG Multi-Asset Portfolio')['availability']
    onshore = PORT.get_schema(BASIS, None, 'US Onshore')['availability']
    esg = PORT.get_schema(BASIS, None, 'PMG ESG')['availability']
    assert len(full) == 43
    # ex-Alts at every risk level, plus the all-equity book any variant may hold
    assert len(onshore) == 8
    assert all('|ex-Alts|' in k or PortfolioKey.fromStr(k).isAllEquity for k in onshore)
    # ESG keeps ex-HFs only where real assets are already excluded
    assert len(esg) == 15
    for keyStr in esg:
        key = PortfolioKey.fromStr(keyStr)
        assert key.isAllEquity or key.allocationType in ('ex-HFs', 'ex-Alts')
        if key.allocationType in rules.RE_ALLOWED:
            assert key.excludeRealAssets, keyStr


def test_risk_levels_stay_data_driven_under_a_variant():
    """Risk is not restricted by variant: the ladder is whatever the universe
    holds for the allocations the variant offers (4.5, D54). It is served in
    risk order, least to most risky, which no name can supply."""
    onshore = PORT.get_schema(BASIS, None, 'US Onshore')['availability']
    levels = {PortfolioKey.fromStr(k).riskLevel for k in onshore}
    assert levels == set(rules.RISK_LEVELS)
    assert rules.RISK_LEVELS[0] == 'LowVol' and rules.RISK_LEVELS[-1] == 'All Equity'
    assert 'All Equity' in rules.RISK_LEVELS         # a risk level, not an allocation


def test_variant_is_enforced_server_side():
    """The picker cannot offer these, but a caller that is not the picker can
    still ask for them - the variant governs which products a client may be
    shown at all, so it is checked again here."""
    full = PortfolioKey('USD', 'Moderate', 'Full', False)
    with pytest.raises(ValidationError) as exc:
        rules.validateKey(full, 'US Onshore')
    assert exc.value.field == 'allocationType'

    with pytest.raises(ValidationError) as exc:
        rules.validateKey(full, None)
    assert exc.value.field == 'variant'

    withRA = PortfolioKey('USD', 'Moderate', 'ex-HFs', False)
    with pytest.raises(ValidationError) as exc:
        rules.validateKey(withRA, 'PMG ESG')
    assert exc.value.field == 'excludeRealAssets'
    # the same allocation is fine once the exclusion is on
    rules.validateKey(PortfolioKey('USD', 'Moderate', 'ex-HFs', True), 'PMG ESG')

    # a key the universe does not hold is refused before any variant rule:
    # Core holds no real assets, so no ex-RAs variant of it exists (D54)
    with pytest.raises(ValidationError) as exc:
        rules.validateKey(PortfolioKey('USD', 'Moderate', 'Core', True),
                          'PMG Multi-Asset Portfolio')
    assert exc.value.field == 'key'
    # and an all-equity book passes every variant, since it holds nothing to restrict
    rules.validateKey(PortfolioKey('USD', 'All Equity', None, None), 'US Onshore')


def test_variant_and_mandate_filters_compose():
    """Both are subtractive and independent; every variant offers Ex Alts,
    which holds no private assets, so no intersection is empty."""
    small = MandateInput(topAccountSize=15e6, mandateSize=10e6, primaryPwa='')
    esg = PORT.get_schema(BASIS, small, 'PMG ESG')['options']['allocations']
    assert esg == ['ex-Alts']                 # ex-HFs is a private-asset allocation
    for variant in rules.IMPLEMENTATION_VARIANTS:
        assert rules.allocationsFor(10e6, variant), variant


def test_columns_the_new_variant_cannot_build_are_named():
    """What the store prunes on a variant change (D49)."""
    keys = ['USD|Moderate|Full|0', 'USD|Moderate|ex-Alts|0']
    assert rules.keysInvalidForVariant(keys, 'US Onshore') == ['USD|Moderate|Full|0']
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
    """Every portfolio the universe offers, in every currency, resolves; every
    number on every surface is finite. An all-equity book holds one category."""
    for currency in rules.CURRENCIES:
        for key in _allKeys(currency):
            result = PORT.resolve_portfolio(
                BasisInput(currency=currency, hedging='Hedged'), key)
            total = sum(c['weightPct'] for c in result['categories'])
            assert abs(total - 100.0) < 1e-6, (currency, key.toStr(), total)
            assert (1 if key.isAllEquity else 2) <= len(result['categories']) <= 7
            for metric in result['metrics'].values():
                assert math.isfinite(metric)
            for row in result['stress'] + result['premia']:
                assert math.isfinite(row['nominalPct'])
                assert math.isfinite(row['realPct'])


def test_no_strategic_allocation_carries_tactical():
    """Tactical allocation is an implementation concept (D50). The supplying
    database's names have no such field, the key has none (D54), and no
    strategic portfolio resolves with the tilt category in it."""
    for keyStr in rules.availability(BASIS, None):
        assert len(keyStr.split('|')) == 4, keyStr
    for keyStr in rules.availability(BASIS, None)[:4]:
        result = PORT.resolve_portfolio(BASIS, PortfolioKey.fromStr(keyStr))
        assert rules.TACTICAL_TILT_CATEGORY not in [c['name'] for c in result['categories']]
    with pytest.raises(ValidationError):
        PortfolioKey.fromStr('USD|Moderate|Core|0|1')


def test_tactical_tilt_moves_weight_it_does_not_create_it():
    """8% out of investment grade fixed income, pro rata across its assets,
    into the tilt category. The book still adds to 100."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('USD', 'Moderate', 'Core', False))
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
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('USD', 'All Equity', None, None))
    assert not rules.canFundTacticalTilt(result['categories'])
    after = rules.tiltedCategories(result['categories'], True)
    assert [c['name'] for c in after] == [c['name'] for c in result['categories']]
    assert sum(c['weightPct'] for c in after) == pytest.approx(100.0)


def test_tactical_tilt_never_mutates_the_payload_it_is_given():
    """Payloads are cached in the adapters and read straight off the baked
    slices, so tilting one must not change the portfolio for the next reader."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('USD', 'Moderate', 'Core', False))
    snapshot = [(c['name'], c['weightPct']) for c in result['categories']]
    rules.tiltedCategories(result['categories'], True)
    assert [(c['name'], c['weightPct']) for c in result['categories']] == snapshot


def test_tilted_implementation_still_sums_to_one_hundred():
    """The rounding guarantee of spec 8.4 has to survive the tilt."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('USD', 'Moderate', 'Core', False))
    variant = sleeves.VARIANTS[0]
    chosen = _sleeveMap(rules.tiltedCategories(result['categories'], True), variant)
    model = buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES,
                                    26_000_000, variant, True)
    assert model['complete']
    assert model['total']['weightPct'] == pytest.approx(100.0)
    assert rules.TACTICAL_TILT_CATEGORY in [g['category'] for g in model['groups']]


def test_ex_re_narrows_other_private_assets_instead_of_dropping_it():
    """Spec 2.2: excluding RE keeps Other Private Assets via Private Credit."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('USD', 'Moderate', 'Full', True))
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
    chosen = _sleeveMap(result['categories'], variant, sleeveChoice)
    return buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES,
                                   mandateSize, variant, False,
                                   feeSchedule, feeLevel, topAccountSize,
                                   volPremium, currency)


def _sleeveMap(categories, variant, pick=0):
    """A complete sleeve map for *categories*: one choice per SLEEVE category,
    which is not one per category - grouped categories share a sleeve and so
    appear once, under the group's name (D60). Keyed the way the store, the
    export gate and the workbook all key it."""
    chosen = {}
    for category in categories:
        if category['name'] in rules.AUTO_SLEEVE_CATEGORIES:
            continue
        under = rules.sleeveCategory(category['name'])
        if under in chosen:
            continue
        library = sleeves.listSleeves(under, variant)
        assert library, '{} offers no sleeve for {}'.format(variant, under)
        chosen[under] = library[pick % len(library)]['name']
    return chosen


@pytest.mark.parametrize('variant', sleeves.VARIANTS)
@pytest.mark.parametrize('sleeveChoice', [0, 1, 2])
@pytest.mark.parametrize('mandateSize', [26_000_000, 5_000_000, 19_999_900, 137_400_000])
def test_implementation_invariants_over_the_whole_space(sleeveChoice, mandateSize,
                                                        variant):
    """The section 8.4 table, held across every available USD combination,
    every sleeve column, every implementation type, and awkward mandate
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
    model = _implementationFor(PortfolioKey('USD', 'Moderate', 'Core', False))
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

def test_fixtures_workbook_reconciles_and_has_four_sheets(tmp_path):
    from openpyxl import load_workbook
    key = PortfolioKey('USD', 'Moderate', 'Core', False)
    result = PORT.resolve_portfolio(BASIS, key)
    chosen = _sleeveMap(result['categories'], sleeves.VARIANTS[0])
    mandate = MandateInput(topAccountSize=48.5e6, mandateSize=26e6,
                           primaryPwa='M. Aldridge — Zurich')
    payload = PORT.build_export(BASIS, mandate, [result],
                                {'sleeves': chosen, 'variant': sleeves.VARIANTS[0],
                                 'feeSchedule': 'CASP', 'feeLevel': 'PMG Target'})
    path = tmp_path / 'wb.xlsx'
    path.write_bytes(payload)
    book = load_workbook(path)
    assert book.sheetnames == ['portfolios', 'risk_dashboard', 'assumptions',
                               'Implementation', 'chartData']
    sheet = book['Implementation']
    rows_ = list(sheet.iter_rows(values_only=True))
    assert rows_[0][0] == 'Implementation Type'
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
    key = PortfolioKey('USD', 'Moderate', 'Core', False)
    store.recordColumn(state['id'], key, 'base')
    other = PortfolioKey('USD', 'ModAgg', 'Full', False)
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


# ------------------------------------------- implementation types (D29) --

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
        under = rules.sleeveCategory(category)
        assert sleeves.listSleeves(under, variant), \
            '{} offers no sleeve for {} (chosen under {})'.format(
                variant, category, under)


def test_an_unchosen_variant_lists_nothing_rather_than_defaulting():
    """The failure that matters: quietly serving one book's products to
    another. None and nonsense both have to come back empty, never as the
    Multi-Asset library."""
    for bad in (None, '', 'Multi-Asset', 'PMG  ESG'):
        assert sleeves.listSleeves('Public Equity', bad) == []
        assert not sleeves.sleeveExists('Public Equity', 'Passive', bad)


def test_sleeve_names_are_scoped_to_their_variant():
    """An Irish book's own sleeve is not attachable in a US Onshore one, and
    the other way round. The names are PMG's; which book each belongs to is
    the library's business, so this asserts the scoping, not the vocabulary."""
    assert sleeves.sleeveExists('Other Fixed Income', 'Funds Irish', 'Irish Onshore')
    assert not sleeves.sleeveExists('Other Fixed Income', 'Funds Irish', 'US Onshore')
    assert sleeves.sleeveExists('Public Equity', 'US Onshore ETFs', 'US Onshore')
    assert not sleeves.sleeveExists('Public Equity', 'US Onshore ETFs', 'Irish Onshore')


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

    # 'Passive' is offered under both, and is not the same sleeve: an Irish
    # book reaches UCITS where a Multi-Asset one reaches US-listed ETFs.
    assert products('PMG Multi-Asset Portfolio', 'Passive') \
        != products('Irish Onshore', 'Passive')
    assert products('PMG Multi-Asset Portfolio', 'Passive') \
        != products('PMG ESG', 'Passive')


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
    key = PortfolioKey('USD', 'Moderate', 'Core', False)
    result = PORT.resolve_portfolio(BASIS, key)
    chosen = _sleeveMap(result['categories'], variant)
    mandate = MandateInput(48.5e6, 26e6, 'M. Aldridge — Zurich')
    path = tmp_path / 'wb.xlsx'
    path.write_bytes(PORT.build_export(BASIS, mandate, [result],
                                       {'sleeves': chosen, 'variant': variant}))
    rows = list(load_workbook(path)['Implementation'].iter_rows(values_only=True))
    assert rows[0][:2] == ('Implementation Type', variant)
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
    from cyrus_pmg.pmgService.scenario import sleeveRepo
    seen = set()
    for variant in sleeves.VARIANTS:
        for category in sleeveRepo.categories():
            for sleeve in sleeves.listSleeves(category, variant):
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
    key = PortfolioKey('USD', 'Moderate', 'Core', False)
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
    key = PortfolioKey('USD', 'Moderate', 'Core', False)
    result = PORT.resolve_portfolio(BASIS, key)
    variant = sleeves.VARIANTS[0]
    chosen = _sleeveMap(result['categories'], variant)
    mandate = MandateInput(TOP_ACCOUNT, 26e6, 'M. Aldridge — Zurich')
    path = tmp_path / 'wb.xlsx'
    path.write_bytes(PORT.build_export(BASIS, mandate, [result], {
        'sleeves': chosen, 'variant': variant,
        'feeSchedule': schedule, 'feeLevel': 'PMG Floor'}))
    rows = list(load_workbook(path)['Implementation'].iter_rows(values_only=True))
    tier = fees.tierFor(TOP_ACCOUNT)
    assert rows[0][:2] == ('Implementation Type', variant)
    assert rows[1][:2] == ('Fee Schedule', schedule)
    assert rows[2][:2] == ('Fee Level', 'PMG Floor')
    assert rows[3][:2] == ('Account Size Tier', '{} ({})'.format(tier['id'], tier['label']))
    # and which card priced it (D55): the delivered version, flagged as placeholder
    assert rows[4][0] == 'Fee Card'
    assert fees.DELIVERY['version'] in rows[4][1] and 'placeholder' in rows[4][1]
    assert rows[5] == (None,) * len(IMPL_COLUMNS)
    assert rows[6] == tuple(IMPL_COLUMNS)
    mgmt, bp = (IMPL_COLUMNS.index(c) for c in ('Mgmt fee', 'Wtd fee (bp)'))
    cost = IMPL_COLUMNS.index('Product Cost')
    assets = _assetRows(rows)
    assert assets
    # the fee group still prices the row; it is no longer a column of its own
    # (item 1), so the expected fee is looked up from the catalogue
    for row in assets:
        product = next(p for p in products.all() if p['name'] == row[1])
        expected = fees.managementFee(schedule, TOP_ACCOUNT, 'PMG Floor', product['feeGroup'])
        assert row[mgmt] == pytest.approx(expected / 100.0)
        assert row[bp] == pytest.approx((row[cost] * 100 + expected) * row[2] * 100)


def test_unpriced_workbook_leaves_the_fee_cells_empty(tmp_path):
    from openpyxl import load_workbook
    key = PortfolioKey('USD', 'Moderate', 'Core', False)
    result = PORT.resolve_portfolio(BASIS, key)
    variant = sleeves.VARIANTS[0]
    chosen = _sleeveMap(result['categories'], variant)
    path = tmp_path / 'wb.xlsx'
    path.write_bytes(PORT.build_export(BASIS, MandateInput(TOP_ACCOUNT, 26e6, ''), [result],
                                       {'sleeves': chosen, 'variant': variant}))
    rows = list(load_workbook(path)['Implementation'].iter_rows(values_only=True))
    assert [r[0] for r in rows[:3]] == ['Implementation Type', None, 'Categories & Asset Classes']
    mgmt, bp = IMPL_COLUMNS.index('Mgmt fee'), IMPL_COLUMNS.index('Wtd fee (bp)')
    assert all(r[mgmt] is None and r[bp] is None for r in _assetRows(rows))


# ---- the strategic volatility premium (D53) ----

def _categoriesFor(tilt=True, volPremium=True, currency='USD',
                   key=PortfolioKey('USD', 'Moderate', 'Core', False)):
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
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('USD', 'Moderate', 'Core', False))
    before = json.dumps(result['categories'], sort_keys=True)
    rules.implementedCategories(result['categories'], True, True, 'USD')
    assert json.dumps(result['categories'], sort_keys=True) == before


def test_a_portfolio_with_nothing_to_fund_it_from_is_a_no_op():
    """All Equity holds no investment grade fixed income at all; the toggle is
    offered disabled and this is the same rule where the workbook is written."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('USD', 'All Equity', None, None))
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
    key = PortfolioKey('USD', 'Moderate', 'Core', False)
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
    for allocation, riskLevel in (('Core', 'Moderate'), ('Full', 'ModAgg'),
                                  ('ex-HFs', 'Conservative'), (None, 'All Equity')):
        result = PORT.resolve_portfolio(
            BASIS, PortfolioKey('USD', riskLevel, allocation,
                                None if allocation is None else False))
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


# ---- the strategic universe: the database is the authority (D54) ----

def test_every_name_in_the_source_parses_round_trips_and_is_unique():
    """The parse is total over the extract, invertible, and injective - the
    three invariants that let a rename upstream fail at bake time."""
    from cyrus_pmg.pmgService.scenario import saaKeys, universe
    assert universe.failures() == []
    assert universe.unknownTickers() == {}
    keys = universe.keys()
    assert len(keys) == 172
    assert len({k.toStr() for k in keys}) == len(keys)
    for key in keys:
        name = universe.nameOf(key)
        assert saaKeys.parseName(name) == key
        assert saaKeys.formatKey(key) == name


def test_the_parser_rejects_rather_than_guesses():
    from cyrus_pmg.pmgService.scenario import saaKeys
    good = saaKeys.parseName('USD Moderate ex-HFs ex-RAs')
    assert good == PortfolioKey('USD', 'Moderate', 'ex-HFs', True)
    assert good.toStr() == 'USD|Moderate|ex-HFs|1'
    assert good.withHedging('Hedged') == 'USD|Moderate|ex-HFs|1|Hedged'
    assert saaKeys.parseName('USD All Equity') == PortfolioKey('USD', 'All Equity', None, None)
    assert saaKeys.parseName('  USD   Moderate  Full ') == PortfolioKey('USD', 'Moderate', 'Full', False)
    for bad in ('', 'SGD Moderate Full', 'USD Medium Full', 'USD Moderate Balanced',
                'USD Moderate ex-RAs', 'USD', 'Moderate Full'):
        with pytest.raises(saaKeys.UnparseableName):
            saaKeys.parseName(bad)


def test_the_selectors_are_derived_from_the_key_set():
    """Nothing names a risk level or an allocation type: a selector is empty
    because the data holds nothing for it, which is what greys it out."""
    from cyrus_pmg.pmgService.scenario import universe
    f = universe.facets()
    assert f['currencies'] == ['USD', 'GBP', 'CHF', 'EUR']
    assert f['riskLevels'][0] == 'LowVol' and f['riskLevels'][-1] == 'All Equity'
    assert f['allocationTypes']['Moderate'] == ['Full', 'Core', 'ex-Alts', 'ex-HFs']
    assert f['allocationTypes']['All Equity'] == []          # the selector greys out
    assert f['exclusionOffered'] == {'Full': True, 'Core': False,
                                     'ex-Alts': False, 'ex-HFs': True}
    # and the literal list the tool used to carry falls out of the data
    assert rules.RE_ALLOWED == ['Full', 'ex-HFs'] == universe.realAssetTypes()
    schema = PORT.get_schema(BASIS, None)
    assert schema['options']['allocationTypesByRisk'] == f['allocationTypes']
    assert schema['options']['exclusionOffered'] == f['exclusionOffered']


def test_every_portfolio_in_the_universe_has_whole_weights():
    from cyrus_pmg.pmgService.scenario import universe
    from cyrus_pmg.pmgService.scenario import portfolio_weights as pw
    codes = [code for code, _, _ in pw.ASSET_METADATA]
    for key in universe.keys():
        held = universe.holdings(key)
        assert abs(sum(w for _, w in held) - 1.0) < 1e-9, key.toStr()
        padded = universe.weightMap(key)
        assert list(padded) == codes                          # every code, in universe order
        assert abs(sum(padded.values()) - 1.0) < 1e-9
        rows = universe.categoryRows(key)
        assert abs(sum(c['weightPct'] for c in rows) - 100.0) < 1e-6
        if key.isAllEquity:
            assert [c['name'] for c in rows] == ['Public Equity']


def test_all_equity_holds_nothing_the_allocation_axis_describes():
    """One all-equity book per currency, 100% public equity, no allocation
    type - so it passes every variant and mandate filter, and Core, ex-Alts,
    ex-HFs and Full are all absent from its key."""
    from cyrus_pmg.pmgService.scenario import universe
    allEquity = [k for k in universe.keys() if k.isAllEquity]
    assert [k.currency for k in allEquity] == ['USD', 'GBP', 'CHF', 'EUR']
    for key in allEquity:
        assert key.toStr().endswith('|All Equity|NA|NA')
        for variant in rules.IMPLEMENTATION_VARIANTS:
            rules.validateKey(key, variant, 5e6)
        result = PORT.resolve_portfolio(BasisInput(key.currency, 'Hedged'), key)
        assert [c['name'] for c in result['categories']] == ['Public Equity']


def test_unparsed_names_are_recorded_not_fatal(tmp_path, monkeypatch):
    """A name the vocabulary does not cover is left out and reported; the
    rest of the extract still loads. The bake's census prints exactly this."""
    from cyrus_pmg.pmgService.scenario import universe
    src = tmp_path / 'saa.csv'
    src.write_text('PortfolioName,AssetTicker,Weight\n'
                   'USD Moderate Core,LHTRYIN,0.6\nUSD Moderate Core,FRUS1GR,0.4\n'
                   'USD Balanced Core,LHTRYIN,1.0\n'
                   'GBP All Equity,FRUS1GR,1.0\n'
                   'USD Moderate Core,NOT_A_TICKER,0.0\n')
    monkeypatch.setenv('SCENARIO_SAA_SOURCE', str(src))
    universe.reload()
    try:
        assert [k.toStr() for k in universe.keys()] == ['USD|Moderate|Core|0', 'GBP|All Equity|NA|NA']
        assert universe.failures() == [('USD Balanced Core',
                                        "unknown risk level 'Balanced' in 'USD Balanced Core'")]
        assert universe.unknownTickers() == {'NOT_A_TICKER': 1}
        assert universe.describeSource()['unparsed'] == 1
    finally:
        monkeypatch.delenv('SCENARIO_SAA_SOURCE')
        universe.reload()


def test_database_free_availability_means_baked(tmp_path):
    """Without a live fallback, a portfolio that enumerated but never baked is
    not offered - or a PWA would pick a column that errors (D54)."""
    from cyrus_pmg.pmgService.scenario import bake
    from cyrus_pmg.pmgService.scenario.bakedAdapter import BakedScenarioPort
    store = str(tmp_path / 'baked')
    bake.bakeSlice(PORT, 'USD', 'Hedged', store, limit=5, verbose=False)
    alone = BakedScenarioPort(storeDirectory=store, warm=False)
    assert len(alone.get_schema(BASIS, None)['availability']) == 5
    withFallback = BakedScenarioPort(storeDirectory=store, delegate=PORT, warm=False)
    assert len(withFallback.get_schema(BASIS, None)['availability']) == 43
    manifest = json.load(open(os.path.join(store, 'manifest.json')))
    assert manifest['facets']['allocationTypes']['All Equity'] == []
    assert manifest['vocabulary']['riskLevels'][0] == 'LowVol'
    assert manifest['source']['portfolios'] == 172 and manifest['unparsedNames'] == []


# ---- the rate card: delivered, versioned, read only (D55) ----

def test_the_card_is_read_from_the_delivery_and_its_shape_is_derived():
    """Tiers and fee groups come from the rows; the level vocabulary and the
    default from fees.json; nothing is a list that can drift from the file."""
    from cyrus_pmg.pmgService.scenario import feeTools
    assert fees.ratesPath().endswith('feeRates.csv')
    assert fees.deliveryInfo()['cells'] == 180
    assert [t['id'] for t in fees.TIERS] == ['T1', 'T2', 'T3', 'T4', 'T5']
    assert fees.TIERS[0]['label'] == 'Under $10m' and fees.TIERS[-1]['label'] == '$100m and up'
    assert fees.TIERS[1]['label'] == '$10m – $25m'
    assert fees.FEE_GROUPS == ['Passive', 'Core Active', 'Specialist Active',
                               'Alternatives', 'Asset Allocation']
    assert fees.DELIVERY['version'] and fees.PLACEHOLDER
    assert feeTools.census() == 0


def test_a_bad_delivery_is_refused_at_read(tmp_path):
    from cyrus_pmg.pmgService.scenario import fees as f
    good = open(f.ratesPath()).read().splitlines()
    def write(lines):
        p = tmp_path / 'card.csv'; p.write_text('\n'.join(lines) + '\n'); return str(p)
    def withRate(prefix, rate):
        return [l.rsplit(',', 1)[0] + ',' + str(rate) if l.startswith(prefix) else l for l in good]
    # a crossed band: a floor above its target and ceiling
    card = f._readDelivery(write(withRate('CASP,,T2,10000000,25000000,Management,Floor,', 9.0)))
    with pytest.raises(ValueError):
        f._checkBands(card['cells'], set(card['cells']))
    # a tier whose edges disagree between rows
    bad = list(good); bad[1] = bad[1].replace('T1,0,10000000,', 'T1,0,12000000,')
    with pytest.raises(f.BadRateCard):
        f._readDelivery(write(bad))
    # a negative rate
    with pytest.raises(f.BadRateCard):
        f._readDelivery(write(withRate('CASP,,T1,0,10000000,Management,Target,', -0.1)))
    # a missing column
    with pytest.raises(f.BadRateCard):
        f._readDelivery(write(['schedule,tier,rate', 'CASP,T1,0.5']))


def test_the_card_the_viewer_reads_pivots_both_ways_over_the_same_cells():
    """One payload, both axes: a tier's fee groups and a group's tier ladder
    are the same 180 cells addressed differently, so the client can flip
    without a round trip."""
    card = fees.card()
    assert len(card['cells']) == 180
    assert [g['schedule'] for g in card['groups']] == ['CASP'] + ['RDR'] * 5
    assert card['groups'][0]['feeGroup'] is None
    assert card['levels'][:2] == ['Management Floor', 'Management Target']
    # every cell the two pivots address is in the payload, and agrees with grid()
    for tier in card['tiers']:
        grid = fees.grid(tier['id'])
        for row in grid['rows']:
            for level, rate in row['cells'].items():
                source, point = level.split(' ', 1)
                key = '|'.join([row['schedule'], row['feeGroup'] or '',
                                tier['id'], source, point])
                assert card['cells'][key] == rate
                assert rate == fees.managementFee(
                    row['schedule'], tier['min'], level, row['feeGroup'])
    assert card['delivery']['version'] == fees.DELIVERY['version']
    with pytest.raises(KeyError):
        fees.grid('T9')


def test_the_card_cannot_be_written_through_the_service():
    """Read only (D55): the module offers no way to change a rate, and the
    router carries no write for it. The card changes by delivery."""
    from cyrus_pmg.pmgService import dashboardRouter
    for gone in ('applyEdits', 'resetEdits', 'overlayInfo'):
        assert not hasattr(fees, gone), gone
    paths = {(r.path, tuple(sorted(r.methods))) for r in dashboardRouter.router.routes}
    feeRoutes = {p: m for p, m in paths if p.endswith('/fees')}
    assert feeRoutes == {'/scenario/fees': ('GET',)}, feeRoutes
    from cyrus_pmg.pmgService.core import accessControl
    assert not hasattr(accessControl, 'requireFeeAdmin')


def test_the_workbook_names_the_card_that_priced_it(tmp_path):
    rows = _exported(tmp_path, {'includeFees': True, 'feeSchedule': 'RDR',
                                'feeLevel': 'PMG Target'})
    assert rows[4][0] == 'Fee Card'
    assert fees.DELIVERY['version'] in rows[4][1] and 'placeholder' in rows[4][1]


def test_a_redelivery_is_diffed_cell_by_cell(tmp_path, capsys):
    from cyrus_pmg.pmgService.scenario import feeTools
    lines = open(fees.ratesPath()).read().splitlines()
    moved = [l if not l.startswith('RDR,Passive,T1,') else l.rsplit(',', 1)[0] + ',0.20'
             for l in lines][:-1]                                     # and one cell removed
    path = tmp_path / 'redelivery.csv'; path.write_text('\n'.join(moved) + '\n')
    assert feeTools.diff(str(path)) == 0
    out = capsys.readouterr().out
    assert 'of 180 rates changed, 0 added, 1 removed' in out
    assert 'REMOVED' in out


# ---- excluding fees from the proposal (D52) ----

def _exported(tmp_path, implementation, mandate=None):
    """The Implementation sheet's rows for one implementation dict."""
    from openpyxl import load_workbook
    key = PortfolioKey('USD', 'Moderate', 'Core', False)
    result = PORT.resolve_portfolio(BASIS, key)
    variant = implementation.get('variant') or sleeves.VARIANTS[0]
    chosen = _sleeveMap(result['categories'], variant)
    payload = dict({'sleeves': chosen, 'variant': variant}, **implementation)
    path = tmp_path / 'wb.xlsx'
    path.write_bytes(PORT.build_export(
        BASIS, mandate or MandateInput(TOP_ACCOUNT, 26e6, 'M. Aldridge — Zurich'),
        [result], payload))
    return list(load_workbook(path)['Implementation'].iter_rows(values_only=True))


def test_excluding_fees_takes_the_columns_off_the_sheet():
    """The two fee columns are the only difference, and they leave in one
    piece: nothing else about the sheet's shape depends on the toggle."""
    assert list(FEE_COLUMNS) == ['Mgmt fee', 'Wtd fee (bp)']
    assert implColumns(True) == IMPL_COLUMNS
    assert implColumns(False) == [c for c in IMPL_COLUMNS if c not in FEE_COLUMNS]
    assert len(implColumns(False)) == len(IMPL_COLUMNS) - 2
    # the columns that survive keep their order and their neighbours
    assert implColumns(False)[-2:] == ['Minimum Investment', 'Notional']


def test_an_excluded_workbook_carries_no_fee_column_and_no_fee_header(tmp_path):
    """A proposal that does not show fees must not ship a sheet that names the
    schedule it was not priced at, nor three columns with nothing in them."""
    rows = _exported(tmp_path, {'includeFees': False,
                                'feeSchedule': 'RDR', 'feeLevel': 'PMG Target'})
    columns = implColumns(False)
    assert rows[0][:2] == ('Implementation Type', sleeves.VARIANTS[0])
    assert rows[1] == (None,) * len(columns)          # no fee schedule/level/tier rows
    assert rows[2] == tuple(columns)
    assert 'Mgmt fee' not in rows[2] and 'Wtd fee (bp)' not in rows[2]
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
        (PortfolioKey('USD', 'ModAgg', 'Core', False), 'USD Moderate-Aggressive Core'),
        (PortfolioKey('USD', 'Conservative', 'Full', False), 'USD Conservative Full'),
        (PortfolioKey('USD', 'Moderate', 'ex-HFs', True), 'USD Moderate ex-HFs ex-RAs'),
        (PortfolioKey('USD', 'LowVol', 'ex-Alts', False), 'USD Low Vol ex-Alts'),
        # an all-equity book is its risk level and nothing more
        (PortfolioKey('USD', 'All Equity', None, None), 'USD All Equity'),
    ]
    for key, expected in cases:
        assert rules.portfolioName(basis, key) == expected
        assert rules.portfolioHeader(key) == expected[len('USD '):]
    # the exclusion is only stated where it is set
    assert 'ex-RAs' not in rules.portfolioHeader(
        PortfolioKey('USD', 'Moderate', 'Core', False))


def test_risk_level_labels_cover_every_value_and_leave_keys_alone():
    for value in rules.RISK_LEVELS:
        assert value in rules.RISK_LEVEL_LABELS, value
    # the key is built from the database's value, never the label
    key = PortfolioKey('USD', 'ModAgg', 'Core', False)
    assert key.toStr() == 'USD|ModAgg|Core|0'
    assert 'Moderate-Aggressive' not in key.toStr()


# --------------------------------------------------------------------------
# The export against the engine's own workbook (D67)
#
# The analytics library used to lay out three of the four sheets. It cannot be
# ported into the host, so the workbook is written here instead - and these
# tests hold that writing to a reference the library itself produced, captured
# while it was still available. tests/golden/README.md says why it can never
# be regenerated.
# --------------------------------------------------------------------------

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'golden')


def _goldenCase():
    import json
    with open(os.path.join(GOLDEN, 'engine_USD_Hedged_2col.results.json'),
              encoding='utf-8') as handle:
        results = json.load(handle)
    with open(os.path.join(GOLDEN, 'engine_USD_Hedged_2col.impl.json'),
              encoding='utf-8') as handle:
        implementation = json.load(handle)
    return results, implementation


def _cellSignature(cell):
    def rgb(colour):
        value = getattr(colour, 'rgb', None)
        if not isinstance(value, str):
            return None
        return value[2:] if len(value) == 8 else value      # 00FFFFFF == FFFFFFFF

    fill = rgb(cell.fill.fgColor) if cell.fill and cell.fill.patternType else None
    return {
        'value': cell.value,
        'format': cell.number_format,
        'font': (cell.font.name, cell.font.size, bool(cell.font.bold),
                 rgb(cell.font.color)),
        'fill': fill,
        'alignment': (cell.alignment.horizontal, cell.alignment.vertical,
                      bool(cell.alignment.wrap_text), cell.alignment.indent),
        'border': (getattr(cell.border.bottom, 'style', None),
                   getattr(cell.border.top, 'style', None)),
    }


def compareSheets(golden, built, name):
    """Every difference between two sheets, as a list. A list, not a boolean:
    when this fails the diff IS the review."""
    out = []
    if (golden.max_row, golden.max_column) != (built.max_row, built.max_column):
        out.append('{}: {}x{} vs {}x{}'.format(name, golden.max_row, golden.max_column,
                                               built.max_row, built.max_column))
    goldMerges = {str(r) for r in golden.merged_cells.ranges}
    builtMerges = {str(r) for r in built.merged_cells.ranges}
    if goldMerges != builtMerges:
        out.append('{}: merges differ, only-golden={} only-built={}'.format(
            name, sorted(goldMerges - builtMerges), sorted(builtMerges - goldMerges)))
    for row in range(1, max(golden.max_row, built.max_row) + 1):
        if golden.row_dimensions[row].height != built.row_dimensions[row].height:
            out.append('{} row {}: height {} vs {}'.format(
                name, row, golden.row_dimensions[row].height,
                built.row_dimensions[row].height))
        for column in range(1, max(golden.max_column, built.max_column) + 1):
            want = _cellSignature(golden.cell(row=row, column=column))
            got = _cellSignature(built.cell(row=row, column=column))
            for field in want:
                a, b = want[field], got[field]
                if field == 'value' and isinstance(a, float) and isinstance(b, float):
                    if abs(a - b) > 1e-9:
                        out.append('{} {}{} value: {!r} vs {!r}'.format(
                            name, get_column_letter(column), row, a, b))
                elif a != b:
                    out.append('{} {}{} {}: {!r} vs {!r}'.format(
                        name, get_column_letter(column), row, field, a, b))
    goldWidths = {k: v.width for k, v in golden.column_dimensions.items() if v.width}
    builtWidths = {k: v.width for k, v in built.column_dimensions.items() if v.width}
    if goldWidths != builtWidths:
        out.append('{}: widths {} vs {}'.format(name, goldWidths, builtWidths))
    goldRules = sorted(str(r.sqref) for r in golden.conditional_formatting)
    builtRules = sorted(str(r.sqref) for r in built.conditional_formatting)
    if goldRules != builtRules:
        out.append('{}: conditional ranges {} vs {}'.format(name, goldRules, builtRules))
    return out


def test_the_export_reproduces_the_engines_workbook_cell_for_cell():
    """The whole point of D67: the same three sheets, without the library."""
    from openpyxl import load_workbook
    from cyrus_pmg.pmgService.scenario import assetEstimates
    from cyrus_pmg.pmgService.scenario.workbook import writeWorkbook

    results, implementation = _goldenCase()
    basis = BasisInput(currency='USD', hedging='Hedged')
    mandate = MandateInput(topAccountSize=50_000_000.0, mandateSize=50_000_000.0,
                           primaryPwa='A. Castellanos — Madrid')
    content = writeWorkbook(
        basis, mandate, results, implementation['sleeves'],
        rules.AUTO_SLEEVE_CATEGORIES, implementation['variant'],
        implementation['tacticalTilt'], implementation['feeSchedule'],
        implementation['feeLevel'], implementation['includeFees'],
        implementation['volPremium'],
        assets=assetEstimates.forSlice('USD', 'Hedged'),
        # the library padded every portfolio to the whole universe before
        # reporting on it, so its workbook carries rows no book holds. This
        # flag restores them, which is what makes a cell-for-cell comparison
        # meaningful; production drops them (D68).
        engineParity=True)

    golden = load_workbook(os.path.join(GOLDEN, 'engine_USD_Hedged_2col.xlsx'))
    built = load_workbook(io.BytesIO(content))
    differences = []
    for name in ('portfolios', 'risk_dashboard', 'assumptions'):
        assert name in built.sheetnames, '{} is missing from the export'.format(name)
        differences.extend(compareSheets(golden[name], built[name], name))
    assert differences == [], '\n'.join(differences[:40])


def test_the_export_carries_four_sheets_and_keeps_the_assumptions():
    from openpyxl import load_workbook
    from cyrus_pmg.pmgService.scenario import assetEstimates
    from cyrus_pmg.pmgService.scenario.workbook import writeWorkbook
    results, implementation = _goldenCase()
    content = writeWorkbook(
        BasisInput(currency='USD', hedging='Hedged'),
        MandateInput(topAccountSize=50_000_000.0, mandateSize=50_000_000.0,
                     primaryPwa='x'),
        results, implementation['sleeves'], rules.AUTO_SLEEVE_CATEGORIES,
        implementation['variant'], implementation['tacticalTilt'],
        implementation['feeSchedule'], implementation['feeLevel'],
        implementation['includeFees'], implementation['volPremium'],
        assets=assetEstimates.forSlice('USD', 'Hedged'))
    book = load_workbook(io.BytesIO(content))
    assert book.sheetnames == ['portfolios', 'risk_dashboard', 'assumptions',
                               'Implementation', 'chartData']
    assert book['chartData'].sheet_state == 'hidden', 'the chart data is not for reading'
    assumptions = book['assumptions']
    assert assumptions['A1'].value is None and assumptions['B1'].value == 'Long-Term Estimates'
    # two header rows, then the rows the strategic sheets kept: six categories
    # and eighteen assets for this lineup (D68)
    assert assumptions.max_row == 26


def test_an_export_imports_no_part_of_the_analytics_library():
    """The property the port depends on. Run in a subprocess so this process's
    own imports cannot mask it."""
    import subprocess
    import sys
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
    script = (
        'import json, sys\n'
        'from cyrus_pmg.pmgService.scenario import assetEstimates, rules\n'
        'from cyrus_pmg.pmgService.scenario.types import BasisInput, MandateInput\n'
        'from cyrus_pmg.pmgService.scenario.workbook import writeWorkbook\n'
        'g = {!r}\n'
        'results = json.load(open(g + "/engine_USD_Hedged_2col.results.json"))\n'
        'impl = json.load(open(g + "/engine_USD_Hedged_2col.impl.json"))\n'
        'data = writeWorkbook(BasisInput(currency="USD", hedging="Hedged"),\n'
        '    MandateInput(topAccountSize=5e7, mandateSize=5e7, primaryPwa="x"),\n'
        '    results, impl["sleeves"], rules.AUTO_SLEEVE_CATEGORIES,\n'
        '    impl["variant"], impl["tacticalTilt"], impl["feeSchedule"],\n'
        '    impl["feeLevel"], impl["includeFees"], impl["volPremium"],\n'
        '    assets=assetEstimates.forSlice("USD", "Hedged"))\n'
        'leaked = [m for m in sys.modules if m.split(".")[0] == "epsilonPhi"]\n'
        'print("BYTES", len(data))\n'
        'print("LEAKED", leaked)\n'
    ).format(GOLDEN)
    environment = dict(os.environ, PYTHONPATH=os.path.abspath(here))
    finished = subprocess.run([sys.executable, '-c', script], cwd=here,
                              capture_output=True, text=True, env=environment)
    assert finished.returncode == 0, finished.stderr[-2000:]
    assert 'LEAKED []' in finished.stdout, finished.stdout
    assert int(finished.stdout.split('BYTES')[1].split()[0]) > 10000


def test_the_asset_estimates_cover_every_basis_the_bake_does():
    from cyrus_pmg.pmgService.scenario import assetEstimates
    for currency in rules.CURRENCIES:
        for hedging in rules.HEDGING_POLICIES:
            rows = assetEstimates.forSlice(currency, hedging)
            assert len(rows) == 19, '{} {} has {} assets'.format(currency, hedging, len(rows))
            assert assetEstimates.analyticsCurrencyFor(currency, hedging)
            first = rows[0]
            for field in ('reportingName', 'category', 'lower', 'mean', 'upper',
                          'volatility', 'sharpe', 'totalReturn', 'hedgingRatio',
                          'from', 'to'):
                assert field in first
            assert first['lower'] <= first['mean'] <= first['upper']


def test_a_missing_estimate_block_still_produces_the_sheet():
    """Four sheets always. A proposal with three where there should be four is
    harder to notice than a sheet that explains itself."""
    from openpyxl import load_workbook
    from cyrus_pmg.pmgService.scenario.workbook import writeWorkbook
    results, implementation = _goldenCase()
    content = writeWorkbook(
        BasisInput(currency='USD', hedging='Hedged'),
        MandateInput(topAccountSize=5e7, mandateSize=5e7, primaryPwa='x'),
        results, implementation['sleeves'], rules.AUTO_SLEEVE_CATEGORIES,
        implementation['variant'], implementation['tacticalTilt'],
        implementation['feeSchedule'], implementation['feeLevel'],
        implementation['includeFees'], implementation['volPremium'],
        assets=[])
    book = load_workbook(io.BytesIO(content))
    assert 'assumptions' in book.sheetnames
    assert 'not in the baked store' in book['assumptions']['A3'].value


def test_the_strategic_sheets_carry_no_category_nobody_holds():
    """D68. The tactical tilt fund is in the asset universe so the tilt has
    somewhere to live, and it is an IMPLEMENTATION choice: the supplied
    extract names it nowhere and no strategic portfolio holds it. A row
    reading 'Asset Allocation Strategies 0.0%' on the strategic sheets claims
    a nil allocation to something that was never in the model."""
    from openpyxl import load_workbook
    from cyrus_pmg.pmgService.scenario import assetEstimates
    from cyrus_pmg.pmgService.scenario.workbook import writeWorkbook
    results, implementation = _goldenCase()
    content = writeWorkbook(
        BasisInput(currency='USD', hedging='Hedged'),
        MandateInput(topAccountSize=5e7, mandateSize=5e7, primaryPwa='x'),
        results, implementation['sleeves'], rules.AUTO_SLEEVE_CATEGORIES,
        implementation['variant'], implementation['tacticalTilt'],
        implementation['feeSchedule'], implementation['feeLevel'],
        implementation['includeFees'], implementation['volPremium'],
        assets=assetEstimates.forSlice('USD', 'Hedged'))
    book = load_workbook(io.BytesIO(content))

    held = {c['name'] for r in results for c in r['categories']}
    assert 'Asset Allocation Strategies' not in held, 'the fixture must not hold it'
    for name in ('portfolios', 'risk_dashboard'):
        labels = [book[name].cell(row=r, column=1).value
                  for r in range(1, book[name].max_row + 1)]
        labels = [str(x).strip() for x in labels if x]
        assert 'Asset Allocation Strategies' not in labels, name
        assert 'Tactical Tilt Fund' not in labels, name

    # ...but a category ONE column holds still prints a zero in the other, so
    # the columns stay comparable row for row
    thin = [dict(results[0])]
    thin[0] = dict(results[0])
    thin[0]['categories'] = [c for c in results[0]['categories']
                             if c['name'] != 'Hedge Funds']
    twoColumn = writeWorkbook(
        BasisInput(currency='USD', hedging='Hedged'),
        MandateInput(topAccountSize=5e7, mandateSize=5e7, primaryPwa='x'),
        [thin[0], results[1]], implementation['sleeves'],
        rules.AUTO_SLEEVE_CATEGORIES, implementation['variant'],
        implementation['tacticalTilt'], implementation['feeSchedule'],
        implementation['feeLevel'], implementation['includeFees'],
        implementation['volPremium'],
        assets=assetEstimates.forSlice('USD', 'Hedged'))
    sheet = load_workbook(io.BytesIO(twoColumn))['portfolios']
    rows = {sheet.cell(row=r, column=1).value: r for r in range(1, sheet.max_row + 1)}
    assert 'Hedge Funds' in rows, 'the column that holds it keeps the row'
    assert sheet.cell(row=rows['Hedge Funds'], column=2).value == 0.0
    assert sheet.cell(row=rows['Hedge Funds'], column=3).value > 0


def test_the_supplied_extract_names_no_tactical_tilt_holding():
    """The data behind D68: if this ever fails, a strategic portfolio has
    started carrying the tilt fund and the sheets should show it again."""
    import csv
    here = os.path.dirname(os.path.abspath(__file__))
    extract = os.path.join(here, '..', '..', 'saaSource', 'saaPortfolios.csv')
    with open(extract, newline='', encoding='utf-8') as handle:
        rows = list(csv.DictReader(handle))
    from cyrus_pmg.pmgService.scenario import portfolio_weights as pw
    assert not [r for r in rows if r['AssetTicker'] == pw.TACTICAL_TILT_CODE]


def test_the_strategic_sheets_drop_a_line_item_zero_in_every_column():
    """D68 for line items. An ex-RAs book keeps Other Private Assets - it
    still holds Private Credit - but nothing holds Core Real Estate, so that
    row goes. A book that DOES hold it keeps it, and the book that does not
    then prints the zero, because that is the alignment worth having."""
    import copy
    from openpyxl import load_workbook
    from cyrus_pmg.pmgService.scenario import assetEstimates
    from cyrus_pmg.pmgService.scenario.workbook import writeWorkbook
    results, implementation = _goldenCase()

    exRAs = copy.deepcopy(results[0])
    for category in exRAs['categories']:
        if category['name'] == 'Other Private Assets':
            category['assets'] = [a for a in category['assets']
                                  if a['reportingName'] != 'Core Real Estate']
    assert any(c['name'] == 'Other Private Assets' for c in exRAs['categories'])

    def labels(lineup):
        content = writeWorkbook(
            BasisInput(currency='USD', hedging='Hedged'),
            MandateInput(topAccountSize=5e7, mandateSize=5e7, primaryPwa='x'),
            lineup, implementation['sleeves'], rules.AUTO_SLEEVE_CATEGORIES,
            implementation['variant'], implementation['tacticalTilt'],
            implementation['feeSchedule'], implementation['feeLevel'],
            implementation['includeFees'], implementation['volPremium'],
            assets=assetEstimates.forSlice('USD', 'Hedged'))
        sheet = load_workbook(io.BytesIO(content))['portfolios']
        return {str(sheet.cell(row=r, column=1).value).strip(): r
                for r in range(1, sheet.max_row + 1)
                if sheet.cell(row=r, column=1).value}, sheet

    alone, _ = labels([exRAs])
    assert 'Other Private Assets' in alone, 'the category still holds Private Credit'
    assert 'Private Credit' in alone
    assert 'Core Real Estate' not in alone, 'no column holds it, so it has no row'

    beside, sheet = labels([exRAs, results[1]])
    assert 'Core Real Estate' in beside, 'the second column holds it, so the row returns'
    row = beside['Core Real Estate']
    assert sheet.cell(row=row, column=2).value == 0.0, 'the book without it prints the zero'
    assert sheet.cell(row=row, column=3).value > 0


def test_the_implementation_sheet_drops_a_product_that_rounds_to_nothing():
    """A category too small for any of its products to reach 0.01% prints no
    products. The column must still close on 100.00 exactly - the filter runs
    after the largest-remainder pass, and a row worth 0.00 adds nothing."""
    import copy
    from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows
    results, implementation = _goldenCase()
    base = copy.deepcopy(results[0])

    moved = 0.0
    for category in base['categories']:
        if category['name'] == 'Hedge Funds':
            moved = category['weightPct'] - 0.004
            category['weightPct'] = 0.004
            share = 0.004 / len(category['assets'])
            for asset in category['assets']:
                asset['weightPct'] = share
    for category in base['categories']:
        if category['name'] == 'Public Equity':
            category['weightPct'] += moved
            category['assets'][0]['weightPct'] += moved
    assert abs(sum(c['weightPct'] for c in base['categories']) - 100.0) < 1e-9

    model = buildImplementationRows(
        base, implementation['sleeves'], rules.AUTO_SLEEVE_CATEGORIES, 50e6,
        implementation['variant'], False, implementation['feeSchedule'],
        implementation['feeLevel'], 50e6, True, 'USD')

    printed = [(g['category'], i['printedPct'])
               for g in model['groups'] for i in g['items']]
    assert all(weight != 0 for _, weight in printed), 'no 0.00 line survives'
    assert abs(model['total']['weightPct'] - 100.0) < 1e-9, 'the column still closes'

    hedge = [g for g in model['groups'] if g['category'] == 'Hedge Funds']
    assert hedge, 'the category keeps its row: it has an allocation, however small'
    assert hedge[0]['items'] == [], 'but nothing in it reaches a printable weight'


def test_an_unimplemented_category_keeps_its_row():
    """The filter must not hide the thing the page exists to prompt: a
    category with an allocation and no sleeve yet."""
    from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows
    results, implementation = _goldenCase()
    partial = dict(implementation['sleeves'])
    partial.pop('Public Equity', None)
    model = buildImplementationRows(
        results[0], partial, rules.AUTO_SLEEVE_CATEGORIES, 50e6,
        implementation['variant'], False, implementation['feeSchedule'],
        implementation['feeLevel'], 50e6, True, 'USD')
    equity = [g for g in model['groups'] if g['category'] == 'Public Equity']
    assert equity and equity[0]['sleeve'] is None
    assert equity[0]['weightPct'] > 0
    assert model['complete'] is False


def _builtWorkbook():
    from cyrus_pmg.pmgService.scenario import assetEstimates
    from cyrus_pmg.pmgService.scenario.workbook import writeWorkbook
    results, implementation = _goldenCase()
    return writeWorkbook(
        BasisInput(currency='USD', hedging='Hedged'),
        MandateInput(topAccountSize=5e7, mandateSize=5e7, primaryPwa='x'),
        results, implementation['sleeves'], rules.AUTO_SLEEVE_CATEGORIES,
        implementation['variant'], implementation['tacticalTilt'],
        implementation['feeSchedule'], implementation['feeLevel'],
        implementation['includeFees'], implementation['volPremium'],
        assets=assetEstimates.forSlice('USD', 'Hedged'))


def test_the_risk_dashboard_has_no_unlabelled_rows():
    """The library left four rows carrying a 0 with a percent format and no
    label, so they printed as '0.0%' against nothing. They are gone (D68); the
    dotted rule above Estimated Mean Return does the separating."""
    from openpyxl import load_workbook
    sheet = load_workbook(io.BytesIO(_builtWorkbook()))['risk_dashboard']
    blanks = [r for r in range(2, sheet.max_row + 1)
              if sheet.cell(row=r, column=1).value in (None, '')
              and sheet.cell(row=r, column=2).value is not None]
    assert blanks == [], 'rows with a value and no label: {}'.format(blanks)

    labels = {str(sheet.cell(row=r, column=1).value).strip(): r
              for r in range(1, sheet.max_row + 1)
              if sheet.cell(row=r, column=1).value}
    # the metrics follow the categories directly, and the rule marks the join
    assert labels['Estimated Mean Return'] == labels['Other Private Assets'] + 1
    assert labels['Sharpe Ratio'] == labels['Estimated Mean Return'] + 1
    assert labels['Volatility'] == labels['Sharpe Ratio'] + 1
    assert sheet.cell(row=labels['Estimated Mean Return'],
                      column=2).border.top.style == 'dotted'


def test_the_implementation_total_is_ruled_above_and_below():
    from openpyxl import load_workbook
    sheet = load_workbook(io.BytesIO(_builtWorkbook()))['Implementation']
    # the doughnut captions follow the table now (item 9), so find the total
    row = next(r for r in range(1, sheet.max_row + 1)
               if sheet.cell(row=r, column=1).value == 'Total')
    for column in range(1, sheet.max_column + 1):
        border = sheet.cell(row=row, column=column).border
        assert getattr(border.top, 'style', None) == 'dotted', column
        assert getattr(border.bottom, 'style', None) == 'dotted', column


def test_the_assumptions_sheet_explains_only_what_the_proposal_holds():
    """D68. The estimates exist for the whole universe; this sheet is here to
    explain THIS proposal. Its rows are exactly the rows the strategic sheets
    show - so the tactical tilt fund, which no strategic portfolio holds, is
    not on it."""
    from openpyxl import load_workbook
    book = load_workbook(io.BytesIO(_builtWorkbook()))
    assumptions, portfolios = book['assumptions'], book['portfolios']

    metrics = {'TOTAL', 'Estimated Mean Return', 'Sharpe Ratio', 'Volatility'}
    strategic = [str(portfolios.cell(row=r, column=1).value).strip()
                 for r in range(2, portfolios.max_row + 1)
                 if portfolios.cell(row=r, column=1).value
                 and str(portfolios.cell(row=r, column=1).value).strip() not in metrics]
    explained = [str(assumptions.cell(row=r, column=1).value).strip()
                 for r in range(3, assumptions.max_row + 1)
                 if assumptions.cell(row=r, column=1).value]

    assert explained == strategic, 'the two sheets must list the same rows in the same order'
    assert 'Tactical Tilt Fund' not in explained
    assert 'Asset Allocation Strategies' not in explained
    # ...but a legitimately held asset with a similar name stays
    assert 'Tactical Trading' in explained, 'a Hedge Funds asset, and genuinely held'


def test_engine_parity_restores_the_whole_universe_on_the_assumptions_sheet():
    """The golden comparison depends on this: with parity on, every asset the
    library reported on comes back."""
    from openpyxl import load_workbook
    from cyrus_pmg.pmgService.scenario import assetEstimates
    from cyrus_pmg.pmgService.scenario.workbook import writeWorkbook
    results, implementation = _goldenCase()
    content = writeWorkbook(
        BasisInput(currency='USD', hedging='Hedged'),
        MandateInput(topAccountSize=5e7, mandateSize=5e7, primaryPwa='x'),
        results, implementation['sleeves'], rules.AUTO_SLEEVE_CATEGORIES,
        implementation['variant'], implementation['tacticalTilt'],
        implementation['feeSchedule'], implementation['feeLevel'],
        implementation['includeFees'], implementation['volPremium'],
        assets=assetEstimates.forSlice('USD', 'Hedged'), engineParity=True)
    sheet = load_workbook(io.BytesIO(content))['assumptions']
    labels = [str(sheet.cell(row=r, column=1).value).strip()
              for r in range(3, sheet.max_row + 1) if sheet.cell(row=r, column=1).value]
    assert sheet.max_row == 28, 'seven categories and nineteen assets'
    assert 'Tactical Tilt Fund' in labels and 'Asset Allocation Strategies' in labels


# --------------------------------------------------------------------------
# The implementation table's columns and the minimum-investment block
# --------------------------------------------------------------------------

def test_the_fee_group_column_is_gone_but_still_prices_the_row():
    """Item 1. The group is a lookup, not a column: the management fee is
    still resolved from it, so removing the column changes no figure."""
    assert 'Fee group' not in IMPL_COLUMNS
    assert 'Fee group' not in FEE_COLUMNS
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('USD', 'Moderate', 'Full', False))
    chosen = _sleeveMap(result['categories'], sleeves.VARIANTS[0])
    model = buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES,
                                    26e6, sleeves.VARIANTS[0], False, 'CASP',
                                    'PMG Target', 48.5e6, False, 'USD')
    items = [i for g in model['groups'] for i in g['items']]
    assert items and all(item['feeGroup'] for item in items), 'the group still rides on the item'
    assert all(item['managementFee'] is not None for item in items)


def test_the_cost_column_is_named_product_cost():
    """Item 2. Header text only - the field behind it is unchanged."""
    assert 'Product Cost' in IMPL_COLUMNS and 'Cost' not in IMPL_COLUMNS
    assert IMPL_COLUMNS.index('Product Cost') == 9
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('USD', 'Moderate', 'Full', False))
    chosen = _sleeveMap(result['categories'], sleeves.VARIANTS[0])
    model = buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES,
                                    26e6, sleeves.VARIANTS[0], False, None, None,
                                    48.5e6, False, 'USD')
    items = [i for g in model['groups'] for i in g['items']]
    assert all('productCost' in item for item in items)


def test_a_position_below_its_products_minimum_is_flagged_and_listed():
    """Item 3. Flagged on the line, and gathered for the gate."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('USD', 'Moderate', 'Full', False))
    chosen = _sleeveMap(result['categories'], sleeves.VARIANTS[0])
    model = buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES,
                                    5e6, sleeves.VARIANTS[0], False, None, None,
                                    5e6, False, 'USD')
    items = [i for g in model['groups'] for i in g['items']]
    for item in items:
        minimum = item.get('minimumInvestment')
        assert item['belowMinimum'] is (bool(minimum) and item['notional'] < float(minimum))
    assert model['breaches'], 'a $5m mandate cannot meet the $5m product minimums'
    for breach in model['breaches']:
        assert breach['notional'] < breach['minimumInvestment']
        assert breach['name'] and breach['category']
    named = {(b['category'], b['name']) for b in model['breaches']}
    flagged = {(g['category'], i['name']) for g in model['groups']
               for i in g['items'] if i['belowMinimum']}
    assert named == flagged


def test_a_product_with_no_minimum_never_breaches():
    """A blank minimum means there is none, not a minimum of nothing."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('USD', 'Moderate', 'Full', False))
    chosen = _sleeveMap(result['categories'], sleeves.VARIANTS[0])
    model = buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES,
                                    5e6, sleeves.VARIANTS[0], False, None, None,
                                    5e6, False, 'USD')
    unbounded = [i for g in model['groups'] for i in g['items']
                 if not i.get('minimumInvestment')]
    assert all(item['belowMinimum'] is False for item in unbounded)


def test_the_export_refuses_while_a_position_is_below_its_minimum(tmp_path):
    """Item 3's hard block, enforced on the server so the UI cannot be
    bypassed by calling the endpoint."""
    from cyrus_pmg.pmgService.scenario import scenarioStore
    from cyrus_pmg.pmgService import dashboardRouter
    state = scenarioStore.createScenario(
        MandateInput(topAccountSize=5e6, mandateSize=5e6, primaryPwa='A. Castellanos — Madrid'),
        BASIS, createdBy='alice')
    key = PortfolioKey('USD', 'Moderate', 'Full', False)
    result = PORT.resolve_portfolio(BASIS, key)
    chosen = _sleeveMap(result['categories'], sleeves.VARIANTS[0])
    # the variant first: changing it clears the columns and the sleeve map
    scenarioStore.updateScenario(state['id'], variant=sleeves.VARIANTS[0])
    scenarioStore.recordColumn(state['id'], key, 'base')
    scenarioStore.updateScenario(state['id'], sleeves=chosen)
    refused = dashboardRouter.exportScenario(state['id'], user='alice')
    assert getattr(refused, 'status_code', 200) == 422
    body = json.loads(refused.body.decode('utf-8'))
    assert body['field'] == 'minimumInvestment'
    assert 'below mandate minimum' in body['error'].lower()


def test_private_equity_and_other_private_assets_are_one_line():
    """Item 6. One sleeve (D60), so one row, and its weight is the sum."""
    result = PORT.resolve_portfolio(BASIS, PortfolioKey('USD', 'Moderate', 'Full', False))
    strategic = {c['name']: c['weightPct'] for c in result['categories']}
    assert 'Private Equity' in strategic and 'Other Private Assets' in strategic
    chosen = _sleeveMap(result['categories'], sleeves.VARIANTS[0])
    model = buildImplementationRows(result, chosen, rules.AUTO_SLEEVE_CATEGORIES,
                                    26e6, sleeves.VARIANTS[0], False, None, None,
                                    48.5e6, False, 'USD')
    names = [g['category'] for g in model['groups']]
    assert 'Private Equity' not in names and 'Other Private Assets' not in names
    combined = [g for g in model['groups'] if g['category'] == rules.SLEEVE_GROUPS[0]['name']]
    assert len(combined) == 1, 'one row, not two'
    assert combined[0]['weightPct'] == pytest.approx(
        strategic['Private Equity'] + strategic['Other Private Assets'])
    assert len(names) == len(set(names)), 'no category appears twice'
    assert model['total']['weightPct'] == pytest.approx(100.0, abs=0.005)


def test_the_workbook_carries_the_composition_doughnuts():
    """Item 9. Five native charts under the table, their data on a hidden
    sheet, in the page's own palette and order."""
    from openpyxl import load_workbook
    from cyrus_pmg.pmgService.scenario.workbook import (
        DONUT_DIMENSIONS, DONUT_PALETTE, donutBreakdown)
    content = _builtWorkbook()
    book = load_workbook(io.BytesIO(content))
    sheet = book['Implementation']
    charts = sheet._charts
    assert len(charts) == len(DONUT_DIMENSIONS) == 5
    titles = [c.title.tx.rich.p[0].r[0].t for c in charts]
    assert titles == [label for _, label in DONUT_DIMENSIONS]
    assert book['chartData'].sheet_state == 'hidden'

    total = next(r for r in range(1, sheet.max_row + 1)
                 if sheet.cell(row=r, column=1).value == 'Total')
    caption = next(r for r in range(total, sheet.max_row + 1)
                   if sheet.cell(row=r, column=1).value == 'Composition of the Implemented Model')
    assert caption > total, 'the charts sit below the table, as on the page'

    # the slices carry the page's colours
    data = book['chartData']
    assert data.cell(row=1, column=1).value == DONUT_DIMENSIONS[0][1]
    series = charts[0].series[0]
    assert len(series.data_points) >= 1
    assert all(point.graphicalProperties.solidFill.srgbClr in DONUT_PALETTE
               for point in series.data_points)


def test_the_donut_breakdown_mirrors_the_pages_rule():
    """Colour by alphabetical rank, order by size - the page's two steps."""
    from cyrus_pmg.pmgService.scenario.workbook import donutBreakdown, DONUT_PALETTE
    items = [{'vehicle': 'SMA', 'printedPct': 10.0},
             {'vehicle': 'ETF', 'printedPct': 30.0},
             {'vehicle': 'SMA', 'printedPct': 5.0},
             {'vehicle': None, 'printedPct': 55.0}]
    slices = donutBreakdown(items, 'vehicle')
    assert [s['name'] for s in slices] == ['—', 'ETF', 'SMA'], 'biggest first'
    assert [round(s['share'], 4) for s in slices] == [0.55, 0.30, 0.15]
    # colour follows the ALPHABETICAL rank, which puts the em dash last in
    # Python and in the page's own sort alike
    bySlot = {s['name']: s['slot'] for s in slices}
    assert bySlot['ETF'] == 0 and bySlot['SMA'] == 1 and bySlot['—'] == 2
    assert all(s['slot'] < len(DONUT_PALETTE) for s in slices)
    # the printed labels close on 100.0, as the page's do
    assert [s['pct'] for s in slices] == [55.0, 30.0, 15.0]


def test_js_donut_share_rounding_mirror_agrees_with_python():
    """The doughnut labels are printed twice - on the page in JavaScript and
    in the workbook in Python. A share the export rounds differently would
    contradict the screen it was exported from, so the two must agree."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    jsPath = os.path.join(os.path.dirname(__file__), '..', '..',
                          'generator', 'js', 'implementation.js')
    with open(jsPath, encoding='utf-8') as fh:
        source = fh.read()
    fn = source[source.index('function roundSharesOneDp('):
                source.index('function breakdown(')]

    cases = [[100.0],
             [62.0, 38.0],
             [100 / 3.0, 100 / 3.0, 100 / 3.0],       # the classic thirds
             [39.08, 37.99, 22.93],                   # the vehicle doughnut
             [85.02, 7.65, 7.33],                     # the liquidity doughnut
             [1 / 7.0 * 100] * 7,                     # seven ties at once
             [99.95, 0.05],
             [50.05, 49.95],
             [0.0, 100.0]]
    script = fn + '\nconst cases = ' + json.dumps(cases) + ';\n' \
        + 'process.stdout.write(JSON.stringify(cases.map(roundSharesOneDp)));\n'
    out = subprocess.run([node, '-e', script], capture_output=True, text=True, check=True)
    jsResults = json.loads(out.stdout)

    assert len(jsResults) == len(cases)
    for case, js in zip(cases, jsResults):
        py = roundSharesOneDp(case)
        assert js == pytest.approx(py), case
        assert sum(py) == pytest.approx(100.0), 'the labels must close on 100.0'
