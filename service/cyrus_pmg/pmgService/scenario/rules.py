"""Product rules shared by every ScenarioPort implementation.

These are the firm's rules from the spec - the $5m mandate floor (2.4), the
$20m private-assets rule (2.5), portfolio naming (2.3), the four-portfolio cap
(2.7) - plus the availability set, derived from the supplied weights'
``ui_available`` column so the loader stays the single authority (spec 4.5).

They are served to the UI through get_schema (spec 4.3: the schema is data,
not code) and enforced again server-side where they gate writes.
"""

from __future__ import annotations

from functools import lru_cache

from . import portfolio_weights as pw
from .types import BasisInput, MandateInput, PortfolioKey, ValidationError

MANDATE_FLOOR = 5_000_000
PRIVATE_ASSETS_MINIMUM = 20_000_000
MAX_PORTFOLIOS = 4

CURRENCIES = list(pw.CURRENCIES)
HEDGING_POLICIES = ['Hedged', 'ISG Hedged', 'Unhedged', 'Equity Not Hedged']
ALLOCATIONS = ['Full', 'Core', 'Ex HFs', 'Ex Alts']
RISK_LEVELS = [str(level['name']) for level in pw.RISK_LEVELS]

# Real estate is only ever held by these allocations (spec 2.1); the others are
# rendered checked-and-disabled because the exclusion is true, not inapplicable.
RE_ALLOWED = ['Full', 'Ex HFs']

# Private assets mean Private Equity and Other Private Assets; hedge funds are
# not private assets (spec 2.5). These allocations hold private assets and are
# therefore blocked under the $20m minimum.
PRIVATE_ASSET_ALLOCATIONS = {'Full', 'Ex HFs'}

# Asset Allocation Strategies carries exactly one sleeve, attached
# automatically whenever the category is present (spec 2.6). Served in the
# schema so the UI never hardcodes a category name.
AUTO_SLEEVE_CATEGORIES = ['Asset Allocation Strategies']


def categoriesInUniverseOrder() -> list:
    """The seven category names in the supplied weights' own order.

    This is the display order of the allocation table and the palette's
    name->slot source (spec 2.2, 6.6). It is data - read from the weights,
    never hardcoded in the UI.
    """
    seen = []
    for _, _, category in pw.ASSET_METADATA:
        if category not in seen:
            seen.append(category)
    return seen


def allocationsFor(mandateSize) -> list:
    """Allocations available at this mandate size (spec 2.5, 10.5).

    Under $20m the private-asset allocations are absent entirely - not
    disabled options. A missing mandate (the pre-mandate schema fetch) applies
    no filter, because the rule cannot bind before a mandate exists.
    """
    if mandateSize is not None and mandateSize < PRIVATE_ASSETS_MINIMUM:
        return [a for a in ALLOCATIONS if a not in PRIVATE_ASSET_ALLOCATIONS]
    return list(ALLOCATIONS)


@lru_cache(maxsize=None)
def _availabilityForCurrency(currency: str) -> tuple:
    """Every stored, UI-available combination for one currency.

    Derived from the loader's ``ui_available`` column - the single authority
    (spec 4.5). Memoised: the weights are static for the life of the process,
    and get_schema is called on open and on every basis or mandate change, so
    re-running the pandas filter each time is pure overhead.
    """
    frame = pw.portfolio_weights_df
    rows = frame.loc[
        frame['currency'].eq(str(currency).upper()) & frame['ui_available'],
        ['allocation_type', 'exclude_real_estate',
         'exclude_tactical_asset_allocation', 'risk_level'],
    ].drop_duplicates()

    keys = [
        (str(record.allocation_type), PortfolioKey(
            allocation=str(record.allocation_type),
            excludeRE=bool(record.exclude_real_estate),
            excludeTAA=bool(record.exclude_tactical_asset_allocation),
            riskLevel=str(record.risk_level),
        ).toStr())
        for record in rows.itertuples(index=False)
    ]
    return tuple(sorted(keys, key=lambda item: item[1]))


def availability(basis: BasisInput, mandateSize) -> list:
    """The availability set as canonical key strings, filtered by the $20m
    rule for this mandate size (spec 2.5, 3.4)."""
    allowed = set(allocationsFor(mandateSize))
    return [keyStr for allocation, keyStr in _availabilityForCurrency(basis.currency)
            if allocation in allowed]


def portfolioHeader(key: PortfolioKey) -> str:
    """Column-header name: no currency, since it is constant (spec 2.3)."""
    suffix = ''
    if key.excludeRE and key.allocation in RE_ALLOWED:
        suffix += ' ex RE'
    if key.excludeTAA:
        suffix += ' ex TAA'
    return '{} {}{}'.format(key.allocation, key.riskLevel, suffix)


def portfolioName(basis: BasisInput, key: PortfolioKey) -> str:
    """Full derived name, used in exports, tooltips and aria-labels."""
    return '{} {}'.format(basis.currency, portfolioHeader(key))


def exportFilename(basis: BasisInput) -> str:
    """The workbook filename (spec 14.2): hedging slugged, host-local date."""
    import datetime
    slug = basis.hedging.replace(' ', '')
    return 'EpsilonPhi_Scenario_{}_{}_{}.xlsx'.format(
        basis.currency, slug, datetime.date.today().isoformat())


def validateBasis(basis: BasisInput) -> None:
    """Reject a basis outside the option lists."""
    if basis.currency not in CURRENCIES:
        raise ValidationError('currency', 'Unknown currency {!r}.'.format(basis.currency))
    if basis.hedging not in HEDGING_POLICIES:
        raise ValidationError('hedging', 'Unknown hedging policy {!r}.'.format(basis.hedging))


def validateMandate(mandate: MandateInput, advisorExists) -> None:
    """The server-authoritative mandate rules (spec 2.4).

    *advisorExists* is a callable so the directory stays the adapter's
    concern. Raises ValidationError with the offending field name.
    """
    if not mandate.topAccountSize or mandate.topAccountSize <= 0:
        raise ValidationError('topAccountSize', 'Enter the top account size.')
    if not mandate.mandateSize or mandate.mandateSize < MANDATE_FLOOR:
        raise ValidationError(
            'mandateSize',
            'Mandate size must be at least ${:,.0f}.'.format(MANDATE_FLOOR))
    if mandate.mandateSize > mandate.topAccountSize:
        raise ValidationError(
            'mandateSize', 'Mandate size cannot exceed the top account size.')
    if not mandate.primaryPwa or not advisorExists(mandate.primaryPwa):
        raise ValidationError('primaryPwa', 'Choose a Primary PWA from the list.')


def schemaPayload(basis: BasisInput, mandateSize, capabilities: dict,
                  dataInfo: dict) -> dict:
    """Assemble the GET /api/scenario/schema response (spec 3.4).

    capabilities() and describe() ride along here: the port defines both but
    the HTTP surface gives them no endpoint of their own, and the footer needs
    describe() from first render. Recorded as deviation D4.
    """
    return {
        'options': {
            'currencies': CURRENCIES,
            'hedgingPolicies': HEDGING_POLICIES,
            'allocations': allocationsFor(mandateSize),
            'riskLevels': RISK_LEVELS,
            'reAllowed': RE_ALLOWED,
        },
        'availability': availability(basis, mandateSize),
        'categories': categoriesInUniverseOrder(),
        'rules': {
            'mandateFloor': MANDATE_FLOOR,
            'privateAssetsMinimum': PRIVATE_ASSETS_MINIMUM,
            'maxPortfolios': MAX_PORTFOLIOS,
            'autoSleeveCategories': AUTO_SLEEVE_CATEGORIES,
        },
        'capabilities': capabilities,
        'dataInfo': dataInfo,
    }
