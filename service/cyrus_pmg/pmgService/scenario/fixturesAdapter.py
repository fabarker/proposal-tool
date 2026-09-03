"""ScenarioPort over static data - no analytics engine, no database.

This adapter is permanent, not scaffolding (spec 4.4): it is how the UI is
demonstrated and debugged with nothing but the service running, and every
state in spec section 10 - including error and timeout - must stay reachable
through it.

Weights and availability are the REAL supplied universe from
``portfolio_weights.py`` (its ``ui_available`` column is the single
authority), so the category structure, the 68 available combinations and the
two-to-seven-category variation are all genuine. Only the analytics numbers
are synthesised - deterministically, from the weights, so they are stable,
finite and plausibly shaped.

Failure knobs, read from the environment (documented in
``dashboard.env.defaults``; recorded as deviation D7):

    SCENARIO_FIXTURES_LATENCY_MS   delay each resolve, e.g. 3000 for skeletons,
                                   310000 to drive the proxy's 504
    SCENARIO_FIXTURES_FAIL         'any' or 'Allocation|Risk Level' - those
                                   resolves raise AnalyticsError -> 502 + Retry
    SCENARIO_FIXTURES_FAIL_SCHEMA  '1' - get_schema raises -> full-page error
    SCENARIO_FIXTURES_FAIL_SLEEVES 'any' or a category name -> 502 on that list
    SCENARIO_FIXTURES_FAIL_EXPORT  '1' - build_export raises -> inline error
"""

from __future__ import annotations

import datetime
import os
import time

from . import advisors, assetEstimates, rules, sleeves
from .payloads import categoryRows, portfolioResult
from .types import AnalyticsError, BasisInput, MandateInput, PortfolioKey
from .workbook import writeWorkbook

# Deterministic per-category coefficients, keyed by name - the fixtures'
# stand-in for the estimation engine. Percent-weight x coefficient.
_RETURN_COEF = {
    'Investment Grade Fixed Income': 0.012,
    'Other Fixed Income': 0.022,
    'Public Equity': 0.0555,
    'Hedge Funds': 0.036,
    'Private Equity': 0.041,
    'Other Private Assets': 0.020,
    'Asset Allocation Strategies': 0.028,
}
_VOL_COEF = {
    'Investment Grade Fixed Income': 0.016,
    'Other Fixed Income': 0.030,
    'Public Equity': 0.1665,
    'Hedge Funds': 0.062,
    'Private Equity': 0.075,
    'Other Private Assets': 0.055,
    'Asset Allocation Strategies': 0.040,
}
_STRESS_BETA = {
    'Investment Grade Fixed Income': 0.06,
    'Other Fixed Income': 0.14,
    'Public Equity': 0.58,
    'Hedge Funds': 0.50,
    'Private Equity': 0.50,
    'Other Private Assets': 0.47,
    'Asset Allocation Strategies': 0.30,
}
_CRISES = [
    ('Global Financial Crisis 2008', 1.00),
    ('European Debt Crisis 2011', 0.41),
    ('COVID-19 Drawdown 2020', 0.60),
    ('Rates Repricing 2022', 0.52),
]
_CCY_FACTOR = {'USD': 1.00, 'CHF': 0.88, 'GBP': 1.04, 'EUR': 0.94}
_HEDGE_VOL = {'Hedged': 1.00, 'ISG Hedged': 1.02, 'Unhedged': 1.12,
              'Equity Not Hedged': 1.07}


def _knob(name, default=''):
    return os.getenv(name, default).strip()


class FixturesScenarioPort:
    """The eight methods over static data."""

    def get_schema(self, basis: BasisInput, mandate: MandateInput, variant=None):
        if _knob('SCENARIO_FIXTURES_FAIL_SCHEMA') == '1':
            raise AnalyticsError('Fixture failure: schema unavailable.')
        mandateSize = mandate.mandateSize if mandate else None
        topAccountSize = mandate.topAccountSize if mandate else None
        return rules.schemaPayload(basis, mandateSize, self.capabilities(),
                                   self.describe(), variant, topAccountSize)

    def search_advisors(self, query: str, limit: int = 20):
        return advisors.searchAdvisors(query, limit)

    def validate_mandate(self, mandate: MandateInput) -> None:
        rules.validateMandate(mandate, advisors.advisorExists)

    def resolve_portfolio(self, basis: BasisInput, key: PortfolioKey):
        latency = _knob('SCENARIO_FIXTURES_LATENCY_MS')
        if latency:
            time.sleep(float(latency) / 1000.0)
        fail = _knob('SCENARIO_FIXTURES_FAIL')
        if fail and fail.lower() in ('any', '{}|{}'.format(key.allocationType or 'NA', key.riskLevel).lower()):
            raise AnalyticsError(
                'Fixture failure: {} could not be built.'.format(rules.portfolioName(basis, key)))

        categories = categoryRows(key)          # LookupError -> router 422
        weightByName = {c['name']: c['weightPct'] for c in categories}

        ccyFactor = _CCY_FACTOR.get(basis.currency, 1.0)
        hedgeFactor = _HEDGE_VOL.get(basis.hedging, 1.0)
        returnPct = (1.55 + sum(w * _RETURN_COEF.get(n, 0.02)
                                for n, w in weightByName.items())) * ccyFactor
        volatilityPct = (1.05 + sum(w * _VOL_COEF.get(n, 0.04)
                                    for n, w in weightByName.items())) * hedgeFactor
        sharpe = (returnPct - 2.0 * ccyFactor) / volatilityPct
        metrics = {
            'estimatedReturnPct': returnPct,
            'volatilityPct': volatilityPct,
            'sharpe': sharpe,
        }

        stress = []
        exposure = sum(w * _STRESS_BETA.get(n, 0.2) for n, w in weightByName.items())
        for period, factor in _CRISES:
            nominal = -exposure * factor
            stress.append({'period': period, 'nominalPct': nominal,
                           'realPct': nominal - 2.4 * factor})

        # Same grouping and horizons as the live adapter, so both
        # implementations render an identical risk dashboard (spec 4.4).
        premia = []
        horizons = (('Over 1 Month', 0.62), ('Over 1 Year', 1.55), ('Over 3 Years', 2.02))
        for group, scale in (('Value at Risk with 99% Confidence', 1.0),
                             ('Conditional Value at Risk with 99% Confidence', 1.24)):
            for horizon, multiplier in horizons:
                nominal = -volatilityPct * multiplier * scale
                premia.append({'group': group, 'horizon': horizon,
                               'label': group + ' · ' + horizon,
                               'nominalPct': nominal, 'realPct': nominal * 1.18,
                               'kind': 'loss'})
        for index, (horizon, _) in enumerate(horizons):
            probability = max(1.5, 30.0 - returnPct * 2.6 - index * 3.4)
            premia.append({'group': 'Probability of Loss', 'horizon': horizon,
                           'label': 'Probability of Loss · ' + horizon,
                           'nominalPct': probability, 'realPct': probability + 6.2,
                           'kind': 'probability'})

        return portfolioResult(basis, key, categories, metrics, stress, premia)

    def list_sleeves(self, category: str, basis: BasisInput, variant: str):
        fail = _knob('SCENARIO_FIXTURES_FAIL_SLEEVES')
        if fail and fail.lower() in ('any', category.lower()):
            raise AnalyticsError('Fixture failure: sleeves for {} unavailable.'.format(category))
        return sleeves.listSleeves(category, variant)

    def build_export(self, basis: BasisInput, mandate: MandateInput,
                     portfolios, implementation) -> bytes:
        if _knob('SCENARIO_FIXTURES_FAIL_EXPORT') == '1':
            raise AnalyticsError('Fixture failure: export unavailable.')
        implementation = implementation or {}
        return writeWorkbook(basis, mandate, list(portfolios),
                             implementation.get('sleeves', {}),
                             rules.AUTO_SLEEVE_CATEGORIES,
                             implementation.get('variant'),
                             bool(implementation.get('tacticalTilt')),
                             implementation.get('feeSchedule'),
                             implementation.get('feeLevel'),
                             implementation.get('includeFees', True),
                             bool(implementation.get('volPremium')),
                             assets=assetEstimates.forSlice(basis.currency, basis.hedging),
                             model=implementation.get('model'))

    def capabilities(self) -> dict:
        return {'canExport': True, 'canEdit': True}

    def describe(self) -> dict:
        return {
            'adapter': 'fixtures',
            'source': 'Supplied model allocations (portfolio_weights.py); synthetic analytics',
            'dataversion': 'supplied weights, 272 available portfolios',
            'asOf': datetime.date.today().isoformat(),
        }
