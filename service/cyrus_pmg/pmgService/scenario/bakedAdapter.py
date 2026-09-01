"""ScenarioPort served from the bake — the fast path.

``resolve_portfolio`` is a dictionary lookup into a slice file produced by
``bake.py``: no analytics, no database, no engine import on the read path.
A cold process serves any baked portfolio in about a millisecond, against ~97s
to compute one live (255s before the Tier 0 fixes).

Everything else the port owes is already cheap and stays local: the schema and
its rules, the advisor directory, the sleeve library.

Misses - an unbaked combination, or a currency this database cannot serve -
fall through to *delegate* when one is configured (the live adapter), so
correctness never depends on the bake being complete. With no delegate the
service runs entirely without a database and a miss raises AnalyticsError,
which the UI already renders as a column error with Retry.

Exports: with a delegate, ``build_export`` is delegated so the workbook stays
the one ``Reporting.generate_report`` produces (spec 14.3). Database-free,
the workbook is written from the baked payloads instead - same three sheets,
same figures, this package's own writer.
"""

from __future__ import annotations

import os
import threading

from . import advisors, bake, rules, sleeves
from .types import AnalyticsError, BasisInput, MandateInput, PortfolioKey
from .workbook import writeFixturesWorkbook


class BakedScenarioPort:
    """The eight methods, served from precomputed results."""

    def __init__(self, storeDirectory: str = None, delegate=None, warm: bool = True):
        self._dir = bake.storeDir(storeDirectory)
        self._delegate = delegate
        self._lock = threading.RLock()
        self._slices = {}
        self._manifest = None
        self._counted = None
        if warm:
            # The static tables (the supplied weight universe behind the
            # availability set) cost ~450ms to build on first touch. Doing it
            # on a daemon thread at construction keeps that off the first
            # request, so a freshly started process answers immediately.
            threading.Thread(target=self._warm, name='scenario-warm',
                             daemon=True).start()

    def _warm(self) -> None:
        try:
            for currency in rules.CURRENCIES:
                rules.availability(BasisInput(currency=currency, hedging='Hedged'), None)
            self.coverage()
        except Exception:                              # noqa: BLE001 - best effort
            pass

    # ---------------------------------------------------------------- store --
    def _manifestData(self) -> dict:
        if self._manifest is None:
            self._manifest = bake.readJson(
                os.path.join(self._dir, 'manifest.json'), default={}) or {}
        return self._manifest

    def _slice(self, currency: str, hedging: str) -> dict:
        """Load one (currency, hedging) slice, memoised for the process."""
        key = (currency, hedging)
        with self._lock:
            if key not in self._slices:
                self._slices[key] = bake.readJson(
                    bake.slicePath(self._dir, currency, hedging), default={}) or {}
            return self._slices[key]

    def coverage(self) -> dict:
        """What the store holds - surfaced through describe().

        Falls back to counting the slice files when there is no manifest, so a
        store assembled by hand, or read while a bake is still running,
        describes itself truthfully rather than claiming nothing is baked.
        """
        manifest = self._manifestData()
        if manifest.get('portfoliosBaked'):
            return {
                'portfoliosBaked': manifest['portfoliosBaked'],
                'currencies': manifest.get('currencies', []),
                'bakedAt': manifest.get('updatedAt'),
            }
        with self._lock:
            if self._counted is None:
                total, currencies = 0, set()
                try:
                    names = sorted(os.listdir(self._dir))
                except OSError:
                    names = []
                for name in names:
                    if not name.endswith('.json') or name == 'manifest.json':
                        continue
                    payloads = bake.readJson(os.path.join(self._dir, name), default={}) or {}
                    if payloads:
                        total += len(payloads)
                        currencies.add(name.split('_')[0])
                self._counted = {'portfoliosBaked': total,
                                 'currencies': sorted(currencies),
                                 'bakedAt': manifest.get('updatedAt')}
            return dict(self._counted)

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
        payload = self._slice(basis.currency, basis.hedging).get(key.toStr())
        if payload is not None:
            return payload
        if self._delegate is not None:
            return self._delegate.resolve_portfolio(basis, key)
        raise AnalyticsError(
            '{} has not been baked for {} {}. Run the bake for this slice, or '
            'enable the live analytics fallback.'.format(
                rules.portfolioName(basis, key), basis.currency, basis.hedging))

    def list_sleeves(self, category: str, basis: BasisInput, variant: str):
        return sleeves.listSleeves(category, variant)

    def build_export(self, basis: BasisInput, mandate: MandateInput,
                     portfolios, implementation) -> bytes:
        if self._delegate is not None:
            return self._delegate.build_export(basis, mandate, portfolios, implementation)
        sleevesMap = (implementation or {}).get('sleeves', {})
        variant = (implementation or {}).get('variant')
        return writeFixturesWorkbook(basis, mandate, list(portfolios), sleevesMap,
                                     rules.AUTO_SLEEVE_CATEGORIES, variant)

    def capabilities(self) -> dict:
        return {'canExport': True, 'canEdit': True}

    def describe(self) -> dict:
        coverage = self.coverage()
        manifest = self._manifestData()
        # carry the analytics provenance recorded at bake time, so the footer
        # still names the data version the figures were produced from. Only
        # the dataversion is read back: the stored `source` is a string frozen
        # at bake time, and echoing it would let a stale bake put superseded
        # wording (an old adapter or library name) back on screen.
        source = 'baked SAA analytics'
        dataversion = 'bake'
        for entry in (manifest.get('slices') or {}).values():
            described = entry.get('describe') or {}
            if described.get('dataversion'):
                dataversion = described['dataversion']
                break
        return {
            'adapter': 'baked' + ('' if self._delegate is None else ' (live fallback)'),
            'source': source,
            'dataversion': '{} · {} portfolios baked{}'.format(
                dataversion, coverage['portfoliosBaked'],
                ' · ' + ', '.join(coverage['currencies']) if coverage['currencies'] else ''),
            'asOf': (coverage['bakedAt'] or '')[:10],
        }
