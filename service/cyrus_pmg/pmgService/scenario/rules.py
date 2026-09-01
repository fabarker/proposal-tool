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

from . import fees
from . import portfolio_weights as pw
from .sleeves import VARIANTS as IMPLEMENTATION_VARIANTS, variantExists
from .types import BasisInput, MandateInput, PortfolioKey, ValidationError

MANDATE_FLOOR = 5_000_000
PRIVATE_ASSETS_MINIMUM = 20_000_000
MAX_PORTFOLIOS = 4

CURRENCIES = list(pw.CURRENCIES)
HEDGING_POLICIES = ['Hedged', 'ISG Hedged', 'Unhedged', 'Equity Not Hedged']
ALLOCATIONS = ['Full', 'Core', 'Ex HFs', 'Ex Alts']
RISK_LEVELS = [str(level['name']) for level in pw.RISK_LEVELS]

# Display names for the risk levels. The VALUES above are load-bearing: they
# are the fourth field of the canonical portfolio key, so they key the
# availability set, every stored scenario and all 272 baked payloads. Renaming
# them in the data would invalidate the bake. The UI therefore shows a label
# and submits the value, which is the schema-is-data rule of spec 4.3 applied
# to a name rather than an option list (D35).
RISK_LEVEL_LABELS = {
    'Low Vol': 'Low Vol',
    'Cons': 'Conservative',
    'Cons Mod': 'Conservative-Moderate',
    'Mod': 'Moderate',
    'Mod Agg': 'Moderate-Aggressive',
    'Agg': 'Aggressive',
    'All Equity': 'All Equity',
}

# Real estate is only ever held by these allocations (spec 2.1); the others are
# rendered checked-and-disabled because the exclusion is true, not inapplicable.
RE_ALLOWED = ['Full', 'Ex HFs']

# Which allocations each implementation variant offers (D49).
#
# This is why the variant is now chosen BEFORE the base portfolio rather than
# on the implementation step: it decides what can be built, not merely what the
# built thing is implemented with. An onshore book cannot hold alternatives, so
# offering Full and then withdrawing it at step 2 would be asking a question
# whose answer was never available.
#
# Risk levels are deliberately absent here. They stay data-driven through the
# availability set - Ex Alts already has no Cons or Low Vol rows in the
# supplied weights, so the ladder narrows on its own (spec 4.5).
VARIANT_ALLOCATIONS = {
    'PMG Multi-Asset Portfolio': ['Full', 'Core', 'Ex HFs', 'Ex Alts'],
    'PMG ESG': ['Ex Alts', 'Ex HFs'],
    'US Onshore': ['Ex Alts'],
    'Irish Onshore': ['Ex Alts'],
}

# Variants that mandate the real-estate exclusion. Only bites on an allocation
# in RE_ALLOWED - the others hold none in the first place - so under ESG the
# Ex HFs sleeve is offered with the exclusion forced on and its toggle locked.
VARIANTS_EXCLUDING_RE = ['PMG ESG']

# --------------------------------------------------------- tactical tilts ---
# Tactical asset allocation is an IMPLEMENTATION concept, not a strategic one
# (D50). No strategic allocation carries it: every key is built with
# excludeTAA true, which is why _availabilityForCurrency filters on it below
# rather than offering both halves of that axis.
#
# The field stays in the canonical key - fourth of four - pinned to 1. Dropping
# it would rekey all 272 baked payloads for no gain; pinning it leaves the bake
# valid and simply narrows the offered set to the half that was already baked.
#
# At implementation level the tilt is a toggle: TACTICAL_TILT_PCT of the
# portfolio, funded out of TACTICAL_TILT_FUNDED_FROM. The reduction is applied
# to that category's weight, which scales its products pro rata by
# construction, since every product weight is category weight x product share.
TACTICAL_TILT_PCT = 8.0
TACTICAL_TILT_FUNDED_FROM = 'Investment Grade Fixed Income'

# The sleeve-library category the tilt is drawn from. It is no longer present
# in any strategic allocation, so the toggle introduces it rather than the
# weights carrying it; its library, and therefore its product, is still chosen
# per variant (ESG holds an ESG tilt fund).
TACTICAL_TILT_CATEGORY = 'Asset Allocation Strategies'


def canFundTacticalTilt(categories) -> bool:
    """Whether these payload categories can fund the tilt.

    All Equity portfolios hold no investment grade fixed income at all, so
    there is nothing to fund it from and the toggle is offered disabled rather
    than producing a book that does not add to 100 (D50).
    """
    for category in categories or []:
        if category.get('name') == TACTICAL_TILT_FUNDED_FROM:
            return float(category.get('weightPct') or 0.0) >= TACTICAL_TILT_PCT
    return False


def tiltedCategories(categories, tacticalTilt) -> list:
    """The payload's categories as *implemented* (D50).

    With the tilt off, the strategic categories unchanged. With it on, the
    funding category loses TACTICAL_TILT_PCT and the tilt category is appended
    carrying it. Weight is moved, never created: the list still sums to 100.

    The reduction is spread across the funding category's own assets in
    proportion, which is what "pro rata" means here - and the product weights
    in the implementation table need no separate treatment, because each is
    the category weight times a fixed share of it.

    Returns copies; the caller's payload (a cached or baked slice) is never
    mutated. An unfundable tilt is silently no-op rather than an error: the
    UI disables the toggle, and this is the same rule applied where the
    workbook is written.

    THE JAVASCRIPT MIRROR of this lives in implementation.js as
    tiltCategories(). The two must agree exactly or the screen and the
    workbook drift.
    """
    result = []
    for category in categories or []:
        copy = dict(category)
        copy['assets'] = [dict(a) for a in category.get('assets') or []]
        result.append(copy)
    if not tacticalTilt or not canFundTacticalTilt(result):
        return result

    for category in result:
        if category['name'] != TACTICAL_TILT_FUNDED_FROM:
            continue
        before = float(category['weightPct'])
        after = before - TACTICAL_TILT_PCT
        category['weightPct'] = after
        share = (after / before) if before else 0.0
        for asset in category['assets']:
            asset['weightPct'] = float(asset['weightPct']) * share
        break

    result.append({
        'name': TACTICAL_TILT_CATEGORY,
        'weightPct': TACTICAL_TILT_PCT,
        'assets': [{'reportingName': TACTICAL_TILT_CATEGORY,
                    'weightPct': TACTICAL_TILT_PCT}],
    })
    return result


def allocationsForVariant(variant) -> list:
    """The allocations *variant* offers, in the canonical ALLOCATIONS order.

    An unknown or absent variant applies no filter: the schema is fetched once
    before a variant is chosen, and a rule cannot bind before its input exists
    (the same reasoning as the mandate filter below).
    """
    offered = VARIANT_ALLOCATIONS.get(variant)
    if not offered:
        return list(ALLOCATIONS)
    return [a for a in ALLOCATIONS if a in offered]


def variantForcesExcludeRE(variant) -> bool:
    """Whether *variant* mandates the real-estate exclusion."""
    return variant in VARIANTS_EXCLUDING_RE

# Private assets mean Private Equity and Other Private Assets; hedge funds are
# not private assets (spec 2.5). These allocations hold private assets and are
# therefore blocked under the $20m minimum.
PRIVATE_ASSET_ALLOCATIONS = {'Full', 'Ex HFs'}

# Categories that carry exactly one sleeve, attached automatically whenever
# the category is present (spec 2.6). Both are introduced by an implementation
# toggle rather than by any strategic allocation - Asset Allocation Strategies
# by the tactical tilt, Hybrid Fixed Income by the volatility premium (D53) -
# so neither is ever present without its sleeve. Served in the schema so the
# UI never hardcodes a category name.
AUTO_SLEEVE_CATEGORIES = ['Asset Allocation Strategies', 'Hybrid Fixed Income']

# ----------------------------------------- the strategic volatility premium ---
# A second implementation overlay, the same shape as the tilt but proportional
# rather than fixed (D53). Switched on, it holds VOL_PREMIUM_SHARE of the
# funding category AS IMPLEMENTED - that is, of what is left after the tilt has
# been funded out of it - and is funded pro rata from that category's own
# products, exactly as the tilt is.
#
#   weight = (funding category, tilt already taken out) x VOL_PREMIUM_SHARE
#
# The category it introduces is placed immediately after the funding category,
# which is where the sheet reads it: under Investment Grade Fixed Income and
# before Other Fixed Income.
VOL_PREMIUM_SHARE = 0.075
VOL_PREMIUM_FUNDED_FROM = 'Investment Grade Fixed Income'
VOL_PREMIUM_CATEGORY = 'Hybrid Fixed Income'

# The product is forbidden outside these two currencies, so the rule is
# enforced where the model is built and not only where the toggle is drawn: a
# stale flag on a scenario whose basis has since moved to EUR must not put it
# in the book. Served in the schema so the page never hardcodes a currency.
VOL_PREMIUM_CURRENCIES = ['USD', 'GBP']


def canHoldVolPremium(currency) -> bool:
    """Whether a book in *currency* may hold the volatility premium at all."""
    return currency in VOL_PREMIUM_CURRENCIES


def volPremiumCategories(categories, volPremium, currency) -> list:
    """The implemented categories with the volatility premium applied (D53).

    Takes categories that have ALREADY been through tiltedCategories, since
    the share is of the funding category as implemented. Weight is moved, not
    created: the funding category loses exactly what the new one gains, and
    its products scale pro rata, so the list still sums to 100.

    A wrong currency, an absent funding category or one with no weight are all
    silent no-ops rather than errors - the UI disables the toggle, and this is
    the same rule applied where the workbook is written.

    THE JAVASCRIPT MIRROR of this lives in implementation.js as
    volPremiumCategories(). The two must agree exactly or the screen and the
    workbook drift.
    """
    result = []
    for category in categories or []:
        copy = dict(category)
        copy['assets'] = [dict(a) for a in category.get('assets') or []]
        result.append(copy)
    if not volPremium or not canHoldVolPremium(currency):
        return result

    for index, category in enumerate(result):
        if category['name'] != VOL_PREMIUM_FUNDED_FROM:
            continue
        before = float(category['weightPct'])
        if before <= 0:
            break
        take = before * VOL_PREMIUM_SHARE
        after = before - take
        category['weightPct'] = after
        share = after / before
        for asset in category['assets']:
            asset['weightPct'] = float(asset['weightPct']) * share
        result.insert(index + 1, {
            'name': VOL_PREMIUM_CATEGORY,
            'weightPct': take,
            'assets': [{'reportingName': VOL_PREMIUM_CATEGORY, 'weightPct': take}],
        })
        break
    return result


def implementedCategories(categories, tacticalTilt, volPremium=False,
                          currency=None) -> list:
    """The strategic categories as implemented: both overlays, in order.

    The tilt first, because the volatility premium's share is of what the
    tilt leaves behind. Every caller that builds an implementation model goes
    through here, so the two can never be applied in the other order.
    """
    return volPremiumCategories(
        tiltedCategories(categories, tacticalTilt), volPremium, currency)


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


def allocationsFor(mandateSize, variant=None) -> list:
    """Allocations available at this mandate size, under this variant.

    Two independent filters (spec 2.5, 10.5; D49), both subtractive: under
    $20m the private-asset allocations are absent entirely - not disabled
    options - and outside a variant's own list an allocation is absent for the
    same reason. A missing mandate or variant applies no filter, because a
    rule cannot bind before its input exists.

    The intersection can be empty in principle; it is not for any variant
    offered here, since every one of them offers Ex Alts, which holds no
    private assets.
    """
    allowed = allocationsForVariant(variant)
    if mandateSize is not None and mandateSize < PRIVATE_ASSETS_MINIMUM:
        allowed = [a for a in allowed if a not in PRIVATE_ASSET_ALLOCATIONS]
    return allowed


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
        frame['currency'].eq(str(currency).upper()) & frame['ui_available']
        # tactical allocation is an implementation concept: no strategic
        # allocation carries it, so only the ex-TAA half is offered (D50)
        & frame['exclude_tactical_asset_allocation'],
        ['allocation_type', 'exclude_real_estate',
         'exclude_tactical_asset_allocation', 'risk_level'],
    ].drop_duplicates()

    keys = [
        (str(record.allocation_type), bool(record.exclude_real_estate),
         PortfolioKey(
            allocation=str(record.allocation_type),
            excludeRE=bool(record.exclude_real_estate),
            excludeTAA=bool(record.exclude_tactical_asset_allocation),
            riskLevel=str(record.risk_level),
        ).toStr())
        for record in rows.itertuples(index=False)
    ]
    return tuple(sorted(keys, key=lambda item: item[2]))


def availability(basis: BasisInput, mandateSize, variant=None) -> list:
    """The availability set as canonical key strings (spec 2.5, 3.4; D49).

    Filtered by the $20m rule for this mandate size and by the variant's own
    allocation list, and - where the variant mandates it - down to the keys
    that already carry the real-estate exclusion. The set is the single
    authority the UI selects against, so a key absent from it cannot be built
    by the picker; ``validateKey`` enforces the same thing server-side for a
    caller that does not use the picker.
    """
    allowed = set(allocationsFor(mandateSize, variant))
    forceExRE = variantForcesExcludeRE(variant)
    return [keyStr
            for allocation, excludeRE, keyStr in _availabilityForCurrency(basis.currency)
            if allocation in allowed
            and not (forceExRE and allocation in RE_ALLOWED and not excludeRE)]


def portfolioHeader(key: PortfolioKey) -> str:
    """Column-header name: no currency, since it is constant (spec 2.3).

    Risk level, then allocation, then exclusions - and the risk level prints
    through RISK_LEVEL_LABELS, so a name reads the way the rail reads
    ("Moderate-Aggressive Core ex TAA"). The KEY is untouched by this: it
    still carries the short value, which is what the availability set and the
    bake are keyed on (D35, D36).
    """
    suffix = ''
    if key.excludeRE and key.allocation in RE_ALLOWED:
        suffix += ' ex RE'
    # no ' ex TAA': every strategic allocation is now ex-TAA, and a suffix
    # true of every portfolio distinguishes none of them (D50)
    risk = RISK_LEVEL_LABELS.get(key.riskLevel, key.riskLevel)
    return '{} {}{}'.format(risk, key.allocation, suffix)


def portfolioName(basis: BasisInput, key: PortfolioKey) -> str:
    """Full derived name, used in exports, tooltips and aria-labels."""
    return '{} {}'.format(basis.currency, portfolioHeader(key))


def exportFilename(basis: BasisInput) -> str:
    """The workbook filename (spec 14.2): hedging slugged, host-local date."""
    import datetime
    slug = basis.hedging.replace(' ', '')
    return 'PMG_Scenario_{}_{}_{}.xlsx'.format(
        basis.currency, slug, datetime.date.today().isoformat())


def validateBasis(basis: BasisInput) -> None:
    """Reject a basis outside the option lists."""
    if basis.currency not in CURRENCIES:
        raise ValidationError('currency', 'Unknown currency {!r}.'.format(basis.currency))
    if basis.hedging not in HEDGING_POLICIES:
        raise ValidationError('hedging', 'Unknown hedging policy {!r}.'.format(basis.hedging))


def validateVariant(variant) -> None:
    """Reject an implementation variant outside the offered list (D29).

    Enforced server-side as well as in the UI because the variant decides
    which products a client can be shown at all - an unchecked one would let
    a caller pull the US Onshore library into an Irish book.
    """
    if not variant:
        raise ValidationError('variant', 'Choose an implementation variant.')
    if not variantExists(variant):
        raise ValidationError(
            'variant', 'Unknown implementation variant {!r}.'.format(variant))


def validateFeeSchedule(schedule) -> None:
    """Reject a fee schedule outside CASP / RDR (D51).

    There is no default: a schedule decides every management fee on the
    sheet, and a book priced under the wrong one is wrong in every row.
    """
    if not schedule:
        raise ValidationError('feeSchedule', 'Choose a fee schedule.')
    if schedule not in fees.SCHEDULES:
        raise ValidationError(
            'feeSchedule', 'Unknown fee schedule {!r}.'.format(schedule))


def validateFeeLevel(level) -> None:
    """Reject a fee level outside the six the framework prices (D51)."""
    if not level:
        raise ValidationError('feeLevel', 'Choose a fee level.')
    if level not in fees.LEVELS:
        raise ValidationError('feeLevel', 'Unknown fee level {!r}.'.format(level))


def validateKey(key: PortfolioKey, variant, mandateSize=None) -> None:
    """Reject a portfolio the variant does not offer (D49).

    Enforced server-side as well as in the picker because the variant governs
    what may be built at all: an unchecked key would let a caller resolve a
    Full portfolio into an onshore book, which is the same class of error as
    pulling the wrong sleeve library and is checked for the same reason.
    """
    if not variant:
        raise ValidationError(
            'variant', 'Choose an implementation variant before a portfolio.')
    allowed = allocationsFor(mandateSize, variant)
    if key.allocation not in allowed:
        raise ValidationError('allocation', '{} does not offer the {} allocation.'
                              .format(variant, key.allocation))
    if (variantForcesExcludeRE(variant) and key.allocation in RE_ALLOWED
            and not key.excludeRE):
        raise ValidationError(
            'excludeRE', '{} requires real estate to be excluded.'.format(variant))
    if not key.excludeTAA:
        raise ValidationError(
            'excludeTAA', 'Tactical allocation is chosen at implementation, not '
            'in the strategic allocation.')


def keysInvalidForVariant(keyStrs, variant, mandateSize=None) -> list:
    """Which of *keyStrs* the variant does not offer.

    Used when the variant changes on a scenario that already has columns: the
    ones it still offers are kept, so switching between two variants that
    share an allocation does not throw away work.
    """
    invalid = []
    for keyStr in keyStrs or []:
        try:
            validateKey(PortfolioKey.fromStr(keyStr), variant, mandateSize)
        except ValidationError:
            invalid.append(keyStr)
    return invalid


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
                  dataInfo: dict, variant=None, topAccountSize=None) -> dict:
    """Assemble the GET /api/scenario/schema response (spec 3.4).

    capabilities() and describe() ride along here: the port defines both but
    the HTTP surface gives them no endpoint of their own, and the footer needs
    describe() from first render. Recorded as deviation D4.

    *variant* narrows ``allocations`` and ``availability``, so the schema is
    re-fetched when it changes. The full per-variant table rides along under
    ``options.variantAllocations`` as well, so the picker can say what a
    variant would offer before it is chosen - the schema-is-data rule (4.3)
    applied to a restriction rather than an option list (D49).

    ``fees`` carries the whole fee framework - schedules, levels, tiers, fee
    groups and both rate grids at the tier the *topAccountSize* falls in - so
    the client can price the implementation sheet as it is built without a
    round trip per product, and without a rate of its own (D51). Without a
    top account size there is no tier and the block carries no rates.
    """
    return {
        'options': {
            'currencies': CURRENCIES,
            'hedgingPolicies': HEDGING_POLICIES,
            'allocations': allocationsFor(mandateSize, variant),
            'riskLevels': RISK_LEVELS,
            'riskLevelLabels': RISK_LEVEL_LABELS,
            'reAllowed': RE_ALLOWED,
            'implementationVariants': IMPLEMENTATION_VARIANTS,
            'variantAllocations': VARIANT_ALLOCATIONS,
            'variantsExcludingRealEstate': VARIANTS_EXCLUDING_RE,
        },
        'availability': availability(basis, mandateSize, variant),
        'categories': categoriesInUniverseOrder(),
        'rules': {
            'mandateFloor': MANDATE_FLOOR,
            'privateAssetsMinimum': PRIVATE_ASSETS_MINIMUM,
            'maxPortfolios': MAX_PORTFOLIOS,
            'autoSleeveCategories': AUTO_SLEEVE_CATEGORIES,
            'tacticalTiltPct': TACTICAL_TILT_PCT,
            'tacticalTiltFundedFrom': TACTICAL_TILT_FUNDED_FROM,
            'tacticalTiltCategory': TACTICAL_TILT_CATEGORY,
            'volPremiumShare': VOL_PREMIUM_SHARE,
            'volPremiumFundedFrom': VOL_PREMIUM_FUNDED_FROM,
            'volPremiumCategory': VOL_PREMIUM_CATEGORY,
            'volPremiumCurrencies': VOL_PREMIUM_CURRENCIES,
        },
        'fees': fees.feePayload(topAccountSize),
        'capabilities': capabilities,
        'dataInfo': dataInfo,
    }
