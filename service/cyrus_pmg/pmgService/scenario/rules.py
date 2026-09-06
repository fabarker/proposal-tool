"""Product rules shared by every ScenarioPort implementation.

These are the firm's rules from the spec - the $5m mandate floor (2.4), the
$20m private-assets rule (2.5), portfolio naming (2.3), the four-portfolio cap
(2.7) - plus the availability set, derived from the strategic universe - the supplying
database's extract - so that database stays the single authority (spec 4.5, D54).

They are served to the UI through get_schema (spec 4.3: the schema is data,
not code) and enforced again server-side where they gate writes.
"""

from __future__ import annotations

from functools import lru_cache

from . import fees
from . import portfolio_weights as pw
from . import saaKeys, universe
from .sleeves import VARIANTS as IMPLEMENTATION_VARIANTS, variantExists
from .types import BasisInput, MandateInput, PortfolioKey, ValidationError

MANDATE_FLOOR = 5_000_000
PRIVATE_ASSETS_MINIMUM = 20_000_000
MAX_PORTFOLIOS = 4

# The option lists are DERIVED from the strategic universe - the supplying
# database's extract, parsed into keys (D54). A currency, risk level or
# allocation type is offered because a portfolio carrying it exists, and for
# no other reason; the vocabularies in saaKeys only fix the ORDER, which a
# name cannot supply. Nothing here is a list that can drift from the data.
_FACETS = universe.facets()
CURRENCIES = list(_FACETS['currencies'])
HEDGING_POLICIES = ['Hedged', 'ISG Hedged', 'Unhedged', 'Equity Not Hedged']
RISK_LEVELS = list(_FACETS['riskLevels'])
ALLOCATIONS = [t for t in saaKeys.ALLOCATION_TYPES
               if any(t in offered for offered in _FACETS['allocationTypes'].values())]

# Display names for the risk levels. The VALUES are the supplying database's
# own spellings - they are the key's second field and key the bake - and three
# of them are compressed forms that should not be put in front of a PWA or
# into a client workbook. So the label map stays, demoted: it used to exist
# because the value could not be renamed (D35); now it is purely cosmetic, and
# a level it has not been told about prints as itself.
RISK_LEVEL_LABELS = {
    'LowVol': 'Low Vol',
    'Conservative': 'Conservative',
    'ConsMod': 'Conservative-Moderate',
    'Moderate': 'Moderate',
    'ModAgg': 'Moderate-Aggressive',
    'Agg': 'Aggressive',
    'Higher Risk': 'Higher Risk',
    'All Equity': 'All Equity',
}

# The allocation types that hold real assets, and so offer the ex-RAs toggle.
# Derived: a type is here because an ex-RAs variant of it exists in the
# universe. Core and ex-Alts hold no private assets in the first place, so no
# such variant exists and the toggle has nothing to offer them - which is what
# spec 2.1 said, and used to be a literal list here.
RE_ALLOWED = universe.realAssetTypes()

# Which allocations each implementation type offers (D49).
#
# This is why the variant is now chosen BEFORE the base portfolio rather than
# on the implementation step: it decides what can be built, not merely what the
# built thing is implemented with. An onshore book cannot hold alternatives, so
# offering Full and then withdrawing it at step 2 would be asking a question
# whose answer was never available.
#
# Risk levels are deliberately absent here. They stay data-driven through the
# availability set - ex-Alts already has no Cons or Low Vol rows in the
# supplied weights, so the ladder narrows on its own (spec 4.5).
VARIANT_ALLOCATIONS = {
    'PMG Multi-Asset Portfolio': ['Full', 'Core', 'ex-HFs', 'ex-Alts'],
    'PMG ESG': ['ex-Alts', 'ex-HFs'],
    'US Onshore': ['ex-Alts'],
    'Irish Onshore': ['ex-Alts'],
}

# Variants that mandate the real-assets exclusion. Only bites on an allocation
# in RE_ALLOWED - the others hold none in the first place - so under ESG the
# ex-HFs book is offered with the exclusion forced on and its toggle locked.
VARIANTS_EXCLUDING_RE = ['PMG ESG']

# --------------------------------------------------------- tactical tilts ---
# Tactical asset allocation is an IMPLEMENTATION concept, not a strategic one
# (D50). No strategic portfolio carries it - the supplying database's names
# have no such field and neither does the key (D54).
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
PRIVATE_ASSET_ALLOCATIONS = {'Full', 'ex-HFs'}

# Categories that carry exactly one sleeve, attached automatically whenever
# the category is present (spec 2.6). Both are introduced by an implementation
# toggle rather than by any strategic allocation - Asset Allocation Strategies
# by the tactical tilt, Hybrid Fixed Income by the volatility premium (D53) -
# so neither is ever present without its sleeve. Served in the schema so the
# UI never hardcodes a category name.
AUTO_SLEEVE_CATEGORIES = ['Asset Allocation Strategies', 'Hybrid Fixed Income']

# ------------------------------------------------------- sleeve groups (D60) ---
# Categories that are implemented together and must carry the SAME sleeve.
# Private markets are one programme: a book does not hold one manager for its
# private equity and another for its other private assets, so the two
# categories are one choice - one picker in the rail, one sleeve in the
# repository - even though the strategic allocation keeps them apart.
#
# The aggregate is unaffected by grouping. A sleeve applied to each member at
# its own weight puts the same money in the same products as applying it once
# to their combined weight: 6% x w + 4% x w = 10% x w. What changes is that
# the choice is made once, and cannot be made inconsistently.
SLEEVE_GROUPS = [
    {'name': 'Private Equity & Other Private Assets',
     'categories': ['Private Equity', 'Other Private Assets']},
]

_GROUP_OF = {c: g['name'] for g in SLEEVE_GROUPS for c in g['categories']}


def sleeveCategory(category: str) -> str:
    """The category a sleeve for *category* is chosen and stored under: the
    group's name when it belongs to one, otherwise itself. Every lookup of a
    sleeve - the rail, the store, the workbook, the export gate - goes through
    this, so a group is one line above rather than a special case anywhere."""
    return _GROUP_OF.get(category, category)


def sleeveCategories(categories) -> list:
    """*categories* reduced to the things a sleeve is chosen for, in order,
    each with the summed weight of the categories it stands for."""
    out, byName = [], {}
    for entry in categories:
        name = sleeveCategory(entry['name'])
        if name in byName:
            byName[name]['weightPct'] += float(entry['weightPct'])
            byName[name]['members'].append(entry['name'])
            continue
        byName[name] = {'name': name, 'weightPct': float(entry['weightPct']),
                        'members': [entry['name']]}
        out.append(byName[name])
    return out

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
    offered here, since every one of them offers ex-Alts, which holds no
    private assets.
    """
    allowed = allocationsForVariant(variant)
    if mandateSize is not None and mandateSize < PRIVATE_ASSETS_MINIMUM:
        allowed = [a for a in allowed if a not in PRIVATE_ASSET_ALLOCATIONS]
    return allowed


@lru_cache(maxsize=None)
@lru_cache(maxsize=None)
def _keysForCurrency(currency: str) -> tuple:
    """Every portfolio the universe offers in one currency, vocabulary order."""
    return tuple(k for k in universe.keys() if k.currency == currency)


def availability(basis: BasisInput, mandateSize, variant=None,
                 availableKeyStrs=None) -> list:
    """The availability set as canonical key strings (spec 2.5, 3.4; D49, D54).

    Every portfolio the universe offers in the basis currency, filtered by the
    $20m rule for this mandate size and by the variant's own allocation list,
    and - where the variant mandates it - down to the keys that already carry
    the real-assets exclusion. An all-equity book holds no alternatives, so it
    passes every allocation filter: any variant, any mandate.

    *availableKeyStrs*, when given, is the set the caller can actually serve -
    the baked adapter passes the slice it holds - so that a portfolio which
    enumerated but never baked is not offered. The set is the single authority
    the UI selects against; ``validateKey`` enforces the same thing server-side
    for a caller that does not use the picker.
    """
    allowed = set(allocationsFor(mandateSize, variant))
    forceExRE = variantForcesExcludeRE(variant)
    out = []
    for key in _keysForCurrency(basis.currency):
        keyStr = key.toStr()
        if availableKeyStrs is not None and keyStr not in availableKeyStrs:
            continue
        if key.isAllEquity:
            out.append(keyStr)
            continue
        if key.allocationType not in allowed:
            continue
        if forceExRE and key.allocationType in RE_ALLOWED and not key.excludeRealAssets:
            continue
        out.append(keyStr)
    return out


def portfolioHeader(key: PortfolioKey) -> str:
    """Column-header name: no currency, since it is constant (spec 2.3).

    Risk level, then allocation type, then the exclusion - the risk level
    printed through RISK_LEVEL_LABELS so a name reads the way the rail reads
    ("Moderate-Aggressive Core ex-RAs"). An all-equity book is its risk level
    and nothing more. The KEY is untouched by this: it carries the database's
    own spelling, which is what the availability set and the bake are keyed on.
    """
    risk = RISK_LEVEL_LABELS.get(key.riskLevel, key.riskLevel)
    if key.isAllEquity:
        return risk
    suffix = saaKeys.EXCLUSION_SUFFIX if key.excludeRealAssets else ''
    return '{} {}{}'.format(risk, key.allocationType, suffix)


def portfolioName(basis: BasisInput, key: PortfolioKey) -> str:
    """Full derived name, used in exports, tooltips and aria-labels.

    The currency is the key's own; *basis* is kept for the callers' sake and
    agrees with it by construction (the router refuses a key in another
    currency).
    """
    return '{} {}'.format(key.currency, portfolioHeader(key))


def exportFilename(basis: BasisInput, proposalId: str) -> str:
    """The workbook filename (spec 14.2, D75): hedging slugged, host-local
    date, and the Proposal UID last - beside the extension, where a reader
    finds it, and after the date, so a folder of proposals still sorts by
    currency and day. There is no form of this name without the UID."""
    import datetime
    slug = basis.hedging.replace(' ', '')
    return 'PMG_Scenario_{}_{}_{}_{}.xlsx'.format(
        basis.currency, slug, datetime.date.today().isoformat(), proposalId)


def validateBasis(basis: BasisInput) -> None:
    """Reject a basis outside the option lists."""
    if basis.currency not in CURRENCIES:
        raise ValidationError('currency', 'Unknown currency {!r}.'.format(basis.currency))
    if basis.hedging not in HEDGING_POLICIES:
        raise ValidationError('hedging', 'Unknown hedging policy {!r}.'.format(basis.hedging))


def validateVariant(variant) -> None:
    """Reject an implementation type outside the offered list (D29).

    Enforced server-side as well as in the UI because the variant decides
    which products a client can be shown at all - an unchecked one would let
    a caller pull the US Onshore library into an Irish book.
    """
    if not variant:
        raise ValidationError('variant', 'Choose an implementation type.')
    if not variantExists(variant):
        raise ValidationError(
            'variant', 'Unknown implementation type {!r}.'.format(variant))


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
    """Reject a portfolio the universe does not hold or the variant does not
    offer (D49, D54).

    Enforced server-side as well as in the picker because the variant governs
    what may be built at all: an unchecked key would let a caller resolve a
    Full portfolio into an onshore book, which is the same class of error as
    pulling the wrong sleeve library and is checked for the same reason. A key
    outside the universe is refused first: nothing downstream can price it.
    """
    if not variant:
        raise ValidationError(
            'variant', 'Choose an implementation type before a portfolio.')
    if not universe.has(key):
        raise ValidationError(
            'key', '{} is not a portfolio the strategic universe offers.'.format(key.toStr()))
    if key.isAllEquity:
        return                    # no alternatives: every variant and mandate may hold it
    allowed = allocationsFor(mandateSize, variant)
    if key.allocationType not in allowed:
        raise ValidationError('allocationType', '{} does not offer the {} allocation.'
                              .format(variant, key.allocationType))
    if (variantForcesExcludeRE(variant) and key.allocationType in RE_ALLOWED
            and not key.excludeRealAssets):
        raise ValidationError(
            'excludeRealAssets',
            '{} requires real assets to be excluded.'.format(variant))


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
                  dataInfo: dict, variant=None, topAccountSize=None,
                  availableKeyStrs=None) -> dict:
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
            # the facets: which allocation types each risk level offers (empty
            # for an all-equity level, which greys the selector), and which
            # types offer the ex-RAs toggle - both derived from the universe
            'allocationTypesByRisk': _FACETS['allocationTypes'],
            'exclusionOffered': _FACETS['exclusionOffered'],
            'implementationVariants': IMPLEMENTATION_VARIANTS,
            'variantAllocations': VARIANT_ALLOCATIONS,
            'variantsExcludingRealEstate': VARIANTS_EXCLUDING_RE,
        },
        'availability': availability(basis, mandateSize, variant, availableKeyStrs),
        'categories': categoriesInUniverseOrder(),
        'rules': {
            'mandateFloor': MANDATE_FLOOR,
            'privateAssetsMinimum': PRIVATE_ASSETS_MINIMUM,
            'maxPortfolios': MAX_PORTFOLIOS,
            'autoSleeveCategories': AUTO_SLEEVE_CATEGORIES,
            'sleeveGroups': SLEEVE_GROUPS,
            'tacticalTiltPct': TACTICAL_TILT_PCT,
            'tacticalTiltFundedFrom': TACTICAL_TILT_FUNDED_FROM,
            'tacticalTiltCategory': TACTICAL_TILT_CATEGORY,
            'volPremiumShare': VOL_PREMIUM_SHARE,
            'volPremiumFundedFrom': VOL_PREMIUM_FUNDED_FROM,
            'volPremiumCategory': VOL_PREMIUM_CATEGORY,
            'volPremiumCurrencies': VOL_PREMIUM_CURRENCIES,
        },
        'fees': fees.feePayload(topAccountSize, mandateSize),
        'capabilities': capabilities,
        'dataInfo': dataInfo,
    }
