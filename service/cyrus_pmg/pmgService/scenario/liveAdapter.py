"""ScenarioPort over the live SAA analytics library - the real analytics.

Every symbol taken from that library is imported from ``engine``, the one
module that names it (see engine.py); the imports below stay inside the
methods that use them, which is what keeps the engine's setup off the import
path of a baked or fixtures-configured service.

The chain is spec 4.5's: the supplied loader selects the stored weights, the
memoised ``get_portfolio`` builds the ``SAAPortfolio`` against the cached
per-currency context, and every figure on screen is read off that portfolio
object - never off ``Reporting``'s table builders, which exist to lay out a
workbook. ``Reporting.generate_report(include_wealth_simulations=False)`` is
used exactly once, for the export.

Operational choices (the brief leaves these to the adapter):

* ``CAppConfig.setup()`` runs lazily on first use (about 12-14s: database +
  factor models), so a fixtures-configured service never pays for it and
  /health is live immediately.
* Every resolve is memoised on (currency, hedging, key) as a finished wire
  payload, on top of the loader's own portfolio/context caches - a refresh or
  rehydrate re-resolves in microseconds, which is what makes the 200ms
  skeleton rule read as instant on warm paths (spec 10.1).
* One coarse lock serialises analytics compute. The engine's caches and the
  mutable, self-memoising SAAPortfolio are not demonstrably thread-safe, and
  FastAPI runs sync handlers in a threadpool. Concurrent column resolves
  therefore queue; each column is its own HTTP request, so the UI's
  per-column states are unaffected.
* Hedging is applied inside the loader's cached build as per-asset ratios -
  see HEDGE_RATIOS_BY_OPTION in portfolio_weights.py (deviation D3).
* Measured cold-path numbers (recorded per the brief): first portfolio in a
  currency with full metrics ~2 minutes (an IMF rates fetch lives inside);
  later cold portfolios ~20-30s; warm repeats sub-second. The proxy's 300s
  timeout covers the worst single resolve.
"""

from __future__ import annotations

import datetime
import io
import os
import tempfile
import threading

from . import advisors, rules, sleeves
from . import portfolio_weights as pw
from .payloads import categoryRows, portfolioResult, selectWeights
from .types import AnalyticsError, BasisInput, MandateInput, PortfolioKey
from .workbook import writeImplementationSheet

# Horizon wording follows the existing report's var/pol table, which
# labels its rows "Over 1 Month" and so on beneath the measure title.
_VAR_HORIZONS = ((0, 'Over 1 Month'), (11, 'Over 1 Year'), (33, 'Over 3 Years'))


def _paddedWeights(basis: BasisInput, key: PortfolioKey) -> dict:
    """The full 19-code weight map, zeros included.

    The analytics portfolio is built zero-padded so every portfolio carries an
    identical asset set: padding changes no number (verified to 1e-12), and it
    is what lets ``Reporting.get_portfolios_table`` union portfolios with
    different holdings - its own union-with-zero-fill convention - instead of
    tripping over heterogeneous row indexes. The SCREEN payload still comes
    from the held rows only, preserving the blank/dash semantics of spec 2.2.
    """
    padded = pw.load_portfolio_weights(
        currency=basis.currency,
        allocation=key.allocation,
        risk_level=key.riskLevel,
        exclude_real_estate=key.excludeRE,
        exclude_tactical_asset_allocation=key.excludeTAA,
        include_zero_weights=True,
    )
    return dict(zip(padded['code'], padded['weight']))


class LiveScenarioPort:
    """The eight methods over the live analytics library."""

    def __init__(self):
        self._lock = threading.RLock()
        self._ready = False
        self._resolveCache = {}

    # ---------------------------------------------------------------- infra --
    def _ensureReady(self):
        """Run CAppConfig.setup() once - database, factor models, config."""
        with self._lock:
            if self._ready:
                return
            from .engine import CAppConfig
            CAppConfig.setup()
            self._ready = True

    def _dataversion(self):
        try:
            from .engine import DATAVERSION
            return str(DATAVERSION)
        except Exception:
            return 'default'

    # ------------------------------------------------------------- the port --
    def get_schema(self, basis: BasisInput, mandate: MandateInput):
        mandateSize = mandate.mandateSize if mandate else None
        return rules.schemaPayload(basis, mandateSize, self.capabilities(),
                                   self.describe())

    def search_advisors(self, query: str, limit: int = 20):
        return advisors.searchAdvisors(query, limit)

    def validate_mandate(self, mandate: MandateInput) -> None:
        rules.validateMandate(mandate, advisors.advisorExists)

    def resolve_portfolio(self, basis: BasisInput, key: PortfolioKey):
        cacheKey = (basis.currency, basis.hedging, key.toStr())
        cached = self._resolveCache.get(cacheKey)
        if cached is not None:
            return cached

        selected = selectWeights(basis, key)      # LookupError -> router 422
        categories = categoryRows(selected)
        weights = _paddedWeights(basis, key)

        self._ensureReady()
        with self._lock:
            try:
                portfolio = pw.get_portfolio(basis.currency, weights,
                                             hedging_option=basis.hedging)
                metrics = {
                    'estimatedReturnPct': float(portfolio.get_total_return()) * 100.0,
                    'volatilityPct': float(portfolio.get_risk()) * 100.0,
                    'sharpe': float(portfolio.get_sharpe_ratio()),
                }
                stress = []
                for period, entry in portfolio.get_factor_stress_tests().items():
                    stress.append({
                        'period': str(period),
                        'nominalPct': float(entry.total) * 100.0,
                        'realPct': float(entry.real) * 100.0,
                    })
                premia = []
                varPol = portfolio.get_portfolio_var_pol(confidence=0.99, loss=0)
                measures = (
                    ('Value at Risk with 99% Confidence', varPol.get_VaR, 'loss', -1.0),
                    ('Conditional Value at Risk with 99% Confidence', varPol.get_CVaR,
                     'loss', -1.0),
                    ('Probability of Loss', varPol.get_PoL, 'probability', 1.0),
                )
                for group, read, kind, sign in measures:
                    for horizon, label in _VAR_HORIZONS:
                        nominal, real = read(horizon)
                        premia.append({
                            # group + horizon drive the on-screen blocks; label
                            # stays the flat form the export and any consumer
                            # without the grouping still reads
                            'group': group,
                            'horizon': label,
                            'label': group + ' · ' + label,
                            'nominalPct': sign * float(nominal) * 100.0,
                            'realPct': sign * float(real) * 100.0,
                            'kind': kind,
                        })
            except AnalyticsError:
                raise
            except Exception as exc:
                raise AnalyticsError(
                    'The analytics engine could not build {}: {}: {}'.format(
                        rules.portfolioName(basis, key), type(exc).__name__, exc))

        payload = portfolioResult(basis, key, categories, metrics, stress, premia)
        self._resolveCache[cacheKey] = payload
        return payload

    def list_sleeves(self, category: str, basis: BasisInput, variant: str):
        return sleeves.listSleeves(category, variant)

    def build_export(self, basis: BasisInput, mandate: MandateInput,
                     portfolios, implementation) -> bytes:
        """The workbook: Reporting's portfolios + risk sheets, the
        implementation sheet appended (spec 14.4), the assumptions sheet
        Reporting always emits removed to honour open item 6's "three tables
        and nothing else"."""
        from .engine import Reporting
        from openpyxl import load_workbook

        results = list(portfolios)
        self._ensureReady()
        with self._lock:
            try:
                copies = []
                for result in results:
                    key = PortfolioKey.fromDict(result['key'])
                    built = pw.get_portfolio(basis.currency,
                                             _paddedWeights(basis, key),
                                             hedging_option=basis.hedging)
                    # the cached instance is shared - name the deepcopy only
                    copy = built.deepcopy()
                    copy.name = result['name']
                    copies.append(copy)

                with tempfile.TemporaryDirectory() as tmpdir:
                    report = Reporting(tmpdir, 'proposal')
                    report.add_portfolios(copies)
                    report.generate_report(include_wealth_simulations=False)
                    book = load_workbook(report.output_path)
                    if 'assumptions' in book.sheetnames:
                        book.remove(book['assumptions'])
                    writeImplementationSheet(
                        book, results[0], (implementation or {}).get('sleeves', {}),
                        rules.AUTO_SLEEVE_CATEGORIES, mandate.mandateSize,
                        (implementation or {}).get('variant'))
                    buffer = io.BytesIO()
                    book.save(buffer)
                    return buffer.getvalue()
            except AnalyticsError:
                raise
            except Exception as exc:
                raise AnalyticsError('The export could not be produced: {}: {}'.format(
                    type(exc).__name__, exc))

    def capabilities(self) -> dict:
        return {'canExport': True, 'canEdit': True}

    def describe(self) -> dict:
        return {
            'adapter': 'live',
            'source': 'SAA analytics over the supplied model allocations',
            'dataversion': 'dataversion {} · window {} to {}'.format(
                self._dataversion(), pw.CONTEXT_START_DATE, pw.CONTEXT_END_DATE),
            'asOf': datetime.date.today().isoformat(),
        }
