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
from numbers import Real

from . import fees
from . import portfolio_weights as pw
from . import saaKeys, universe
from .sleeves import VARIANTS as IMPLEMENTATION_VARIANTS, variantExists
from .types import BasisInput, MandateInput, PortfolioKey, ValidationError

MANDATE_FLOOR = 5_000_000
PRIVATE_ASSETS_MINIMUM = 20_000_000
MAX_PORTFOLIOS = 4

HEDGING_POLICIES = ['Hedged', 'ISG Hedged', 'Unhedged', 'Equity Not Hedged']

# The option lists are DERIVED from the strategic universe - the supplying
# database's extract, parsed into keys (D54). A currency, risk level or
# allocation type is offered because a portfolio carrying it exists, and for
# no other reason; the vocabularies in saaKeys only fix the ORDER, which a
# name cannot supply. Nothing here is a list that can drift from the data.
#
# THEY RESOLVE ON FIRST USE, NOT AT IMPORT (D86). Reading the extract while
# this module is being imported made a missing or unreadable extract an
# ImportError - and since the endpoint block lives inside the HOST's own
# dashboardRouter.py, that ImportError took every other dashboard endpoint
# down with it. Deferred, the same fault is an error from the Proposal Tool
# alone, on the request that needs the data. engine.py defers the analytics
# library's symbols the same way and for the same reason.
#
# `rules.CURRENCIES` and the rest still read as module constants from outside
# - PEP 562's module __getattr__ below resolves them - so no caller changed.
# Inside this module they are read through _derived(), because a bare global
# would not reach __getattr__.

#: (universe generation, the lists) - rebuilt when the extract is re-read.
_derivedCache = None

#: The names __getattr__ resolves. Everything else here is a literal.
_DERIVED_NAMES = ('_FACETS', 'CURRENCIES', 'RISK_LEVELS', 'ALLOCATIONS', 'RE_ALLOWED')


def _derived() -> dict:
    """The universe-derived option lists, read once and cached against the
    generation universe.reload() bumps."""
    global _derivedCache
    generation = universe.generation()
    if _derivedCache is None or _derivedCache[0] != generation:
        facets = universe.facets()
        _derivedCache = (generation, {
            '_FACETS': facets,
            'CURRENCIES': list(facets['currencies']),
            'RISK_LEVELS': list(facets['riskLevels']),
            'ALLOCATIONS': [t for t in saaKeys.ALLOCATION_TYPES
                            if any(t in offered
                                   for offered in facets['allocationTypes'].values())],
            # The allocation types that hold real assets, and so offer the
            # ex-RAs toggle. Derived: a type is here because an ex-RAs variant
            # of it exists. Core and ex-Alts hold no private assets in the
            # first place, so no such variant exists and the toggle has
            # nothing to offer them - which is what spec 2.1 said, and used to
            # be a literal list here.
            'RE_ALLOWED': universe.realAssetTypes(),
        })
    return _derivedCache[1]


def __getattr__(name):
    """PEP 562: the universe-derived names resolve on first access."""
    if name in _DERIVED_NAMES:
        return _derived()[name]
    raise AttributeError('module {!r} has no attribute {!r}'.format(__name__, name))

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
    """Whether these payload categories can fund the tilt AS SEEDED.

    All Equity portfolios hold no investment grade fixed income at all, so
    there is nothing to fund it from and the toggle is offered disabled rather
    than producing a book that does not add to 100 (D50). The test is the
    overlay engine's own (``overlayTakes``) on the baseline rule; the rule in
    force for a type is the repository's (D155).
    """
    return overlayTakes(baselineOverlays()[0], categories) is not None


def tiltedCategories(categories, tacticalTilt) -> list:
    """The payload's categories with the tilt AS SEEDED applied alone (D50):
    the funding category loses TACTICAL_TILT_PCT, its assets pro rata, and
    the tilt category is appended carrying it. Weight is moved, never created.

    Kept for the baseline's sake - the tests that pin today's figures read it.
    Every model goes through ``implementedCategories``, which applies the
    rules in force for the type, in their order (D155). Returns copies.
    """
    return resolveOverlays(categories, baselineOverlays()[:1],
                           {'tacticalTilt': tacticalTilt})['categories']


def allocationsForVariant(variant) -> list:
    """The allocations *variant* offers, in the canonical ALLOCATIONS order.

    An unknown or absent variant applies no filter: the schema is fetched once
    before a variant is chosen, and a rule cannot bind before its input exists
    (the same reasoning as the mandate filter below).
    """
    allocations = _derived()['ALLOCATIONS']
    offered = VARIANT_ALLOCATIONS.get(variant)
    if not offered:
        return list(allocations)
    return [a for a in allocations if a in offered]


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


def canHoldVolPremium(currency, variant=None) -> bool:
    """Whether a book in *currency* may hold the volatility premium at all:
    what the premium's rule in force for *variant* says (D155) - the
    currencies it names, any when it names none, and never when no rule
    answers to the premium's switch. Seeded, USD and GBP."""
    rule = next((r for r in overlayRulesFor(variant) if r.get('toggle') == 'volPremium'), None)
    if rule is None:
        return False
    return not rule.get('currencies') or currency in rule['currencies']


def volPremiumCategories(categories, volPremium, currency) -> list:
    """The categories with the volatility premium AS SEEDED applied alone
    (D53): VOL_PREMIUM_SHARE of the funding category as it stands, taken pro
    rata from its products and placed straight after it. A wrong currency, an
    absent funding category or one with no weight are silent no-ops.

    Kept for the baseline's sake, like ``tiltedCategories``; models go
    through ``implementedCategories`` (D155). Returns copies.
    """
    return resolveOverlays(categories, baselineOverlays()[1:2],
                           {'volPremium': volPremium}, currency)['categories']


# ------------------------------------------------ the overlay rules (D155) ---
# The two overlays above were hard-coded until D155. They are now an ORDERED
# LIST OF RULES the repository holds (overlayRules.py): a house list for every
# implementation type and, optionally, a type's own. Each rule moves weight
# out of one or more strategic categories into a category of its own, and the
# list is resolved top to bottom - each rule reading the allocation as the
# rules above it left it. That ordering is what made the premium "7.5% of
# IGFI after the tilt"; it is now a property of the list, not of the code.
#
# A rule:
#   id          stable key ('ttf', 'svp' for the two seeded rules)
#   name        what the desk calls it ('Tactical Tilts')
#   into        the category it creates ('Asset Allocation Strategies')
#   row         the asset-class row inside it (today the category's own name)
#   place       where the category sits: 'end', or after a named category
#   size        a percentage, to four places
#   basis       'portfolio' - size% of the whole portfolio, split across the
#               sources by their weights - or 'sources' - size% of what each
#               source holds at this step, times its weight
#   sources     [{category, weightPct}], weights adding up to exactly 100
#   toggle      'tacticalTilt' | 'volPremium' | None. A toggled rule applies
#               only when the proposal selects it; any other rule applies to
#               every portfolio of the type
#   currencies  the base currencies it may be held in; empty means any
#
# A rule is FUNDABLE for an allocation when every source is held (above zero)
# and none would go below zero. An unfundable rule is skipped - the toggle is
# offered disabled, as the tilt always was for an all-equity portfolio - and
# the rules below it resolve as normal. The repository refuses to save a list
# under which a portfolio HOLDS every source of a rule yet cannot fund it
# (overlayRules.save), so at proposal time "unfundable" only ever means
# "does not hold a source".
#
# THE JAVASCRIPT MIRROR is resolveOverlays() in core.js, between the markers
# "overlay resolver (D155)" and "end of the overlay resolver"; the two must
# agree exactly or the screen and the workbook drift (tests/test_overlay_rules).
# The arithmetic is written so the seeded rules reproduce the code they
# replaced to the last bit: a portfolio-basis take is size x weight, and a
# source-basis take is held x (size / 100) x weight - with one source the
# weight is 1.0, which no product changes.
OVERLAY_END = 'end'
OVERLAY_BASES = ('portfolio', 'sources')
OVERLAY_TOGGLES = ('tacticalTilt', 'volPremium')
#: how far below a take a source may fall and still be judged to fund it:
#: category weights are sums of floats, and GBP Low Vol Full's Public Equity
#: is 15.999999999999998 - a 16% rule from it must not be refused for the
#: last bit. A take within it is clamped to what the source holds, so the
#: source ends at exactly zero and the list still sums to 100.
OVERLAY_TOLERANCE = 1e-9


def baselineOverlays() -> list:
    """The two rules as the code applied them before D155, in their order:
    what the repository's house list is seeded with, so an upgrade changes
    no figure."""
    return [
        {'id': 'ttf', 'name': 'Tactical Tilts', 'into': TACTICAL_TILT_CATEGORY,
         'row': TACTICAL_TILT_CATEGORY, 'place': OVERLAY_END,
         'size': TACTICAL_TILT_PCT, 'basis': 'portfolio',
         'sources': [{'category': TACTICAL_TILT_FUNDED_FROM, 'weightPct': 100.0}],
         'toggle': 'tacticalTilt', 'currencies': []},
        {'id': 'svp', 'name': 'Strategic Volatility Premium', 'into': VOL_PREMIUM_CATEGORY,
         'row': VOL_PREMIUM_CATEGORY, 'place': VOL_PREMIUM_FUNDED_FROM,
         'size': round(VOL_PREMIUM_SHARE * 100, 4), 'basis': 'sources',
         'sources': [{'category': VOL_PREMIUM_FUNDED_FROM, 'weightPct': 100.0}],
         'toggle': 'volPremium', 'currencies': list(VOL_PREMIUM_CURRENCIES)},
    ]


def _copyCategories(categories) -> list:
    out = []
    for category in categories or []:
        copy = dict(category)
        copy['assets'] = [dict(a) for a in category.get('assets') or []]
        out.append(copy)
    return out


def _weightOf(category) -> float:
    try:
        return float(category.get('weightPct') or 0.0)
    except (TypeError, ValueError):
        return float('nan')


def _take(rule, source, before) -> float:
    """What *rule* asks of one *source* holding *before*: size x weight of
    the portfolio, or before x (size / 100) x weight of the source."""
    size = float(rule['size'])
    weight = float(source['weightPct']) / 100.0
    if rule['basis'] == 'portfolio':
        return size * weight
    return before * (size / 100.0) * weight


def _rawTakes(rule, categories):
    """``[(category, before, take)]`` for every source, or None when a source
    is not held at this step - absent, at zero, or not a number. No
    sufficiency test - ``overlayTakes`` applies its own."""
    byName = {c.get('name'): c for c in categories or []}
    out = []
    for source in rule.get('sources') or []:
        held = byName.get(source['category'])
        if held is None:
            return None
        before = _weightOf(held)
        if not before > 0:                       # NaN as well as zero
            return None
        out.append((source['category'], before, _take(rule, source, before)))
    return out or None


def overlayTakes(rule, categories):
    """``[(category, take)]`` - what *rule* takes from each source of
    *categories* as they stand - or None when it cannot be funded: a source
    not held, or one that would go below zero by more than the tolerance. A
    take within the tolerance is clamped to what the source holds."""
    raw = _rawTakes(rule, categories)
    if raw is None or any(before < take - OVERLAY_TOLERANCE for _, before, take in raw):
        return None
    return [(name, min(take, before)) for name, before, take in raw]


def overlayShortfall(rule, categories, strategic=None) -> list:
    """``[(category, after)]`` for every source *rule* would take below zero
    - what the repository refuses - where the portfolio HOLDS every source:
    in *strategic*, its strategic allocation, when given, else in
    *categories*. Judged on *strategic*, a source an earlier rule drained to
    exactly zero still counts as held, so a rule that would then ask for
    more of it is a shortfall, not a quiet skip. Empty when the rule is
    fundable or not offered (a source the portfolio does not hold)."""
    heldIn = {c.get('name'): _weightOf(c) for c in (categories if strategic is None else strategic) or []}
    sources = rule.get('sources') or []
    if not sources or any(not heldIn.get(s['category'], 0.0) > 0 for s in sources):
        return []
    byName = {c.get('name'): c for c in categories or []}
    out = []
    for source in sources:
        held = byName.get(source['category'])
        before = _weightOf(held) if held is not None else 0.0
        if not before == before:                 # NaN: nothing to judge
            continue
        take = _take(rule, source, before)
        if before < take - OVERLAY_TOLERANCE:
            out.append((source['category'], before - take))
    return out


def _skipReason(rule, selections, currency):
    toggle = rule.get('toggle')
    if toggle and not (selections or {}).get(toggle):
        return 'off'
    currencies = rule.get('currencies') or []
    if currencies and currency not in currencies:
        return 'currency'
    return None


def resolveOverlays(categories, ruleList, selections=None, currency=None) -> dict:
    """Apply *ruleList* to *categories*, top to bottom (D155).

    *selections* says which toggled rules the proposal has on
    (``{'tacticalTilt': bool, 'volPremium': bool}``); *currency* is the base
    currency, for a rule that names the currencies it may be held in.
    Returns ``{'categories': [...], 'steps': [...]}``: the implemented
    categories (copies - the caller's payload is never mutated) and, per
    rule, what happened - ``status`` 'applied', 'off', 'currency' or
    'unfundable', the ``amount`` it placed and, for each source, its weight
    ``before``, the ``take`` and the weight ``after``.

    A source's reduction is spread across its own assets in proportion, which
    is what "pro rata" means here: every product weight downstream is the
    category weight times a fixed share of it.
    """
    result = _copyCategories(categories)
    steps = []
    for rule in ruleList or []:
        step = {'id': rule.get('id'), 'name': rule.get('name'), 'into': rule.get('into'),
                'status': None, 'amount': 0.0, 'takes': []}
        steps.append(step)
        reason = _skipReason(rule, selections, currency)
        if reason:
            step['status'] = reason
            continue
        takes = overlayTakes(rule, result)
        if takes is None:
            step['status'] = 'unfundable'
            continue
        byName = {c['name']: c for c in result}
        amount = 0.0
        for name, take in takes:
            category = byName[name]
            before = float(category['weightPct'])
            after = before - take
            category['weightPct'] = after
            share = (after / before) if before else 0.0
            for asset in category['assets']:
                asset['weightPct'] = float(asset['weightPct']) * share
            amount += take
            step['takes'].append({'category': name, 'before': before, 'take': take,
                                  'after': after})
        entry = {'name': rule['into'], 'weightPct': amount,
                 'assets': [{'reportingName': rule.get('row') or rule['into'],
                             'weightPct': amount}]}
        place = rule.get('place') or OVERLAY_END
        index = None
        if place != OVERLAY_END:
            index = next((i for i, c in enumerate(result) if c['name'] == place), None)
        if index is None:
            result.append(entry)
        else:
            result.insert(index + 1, entry)
        step['status'] = 'applied'
        step['amount'] = amount
    return {'categories': result, 'steps': steps}


def _pct(value) -> str:
    return '{:g}'.format(round(float(value), 4))


def overlayWords(rule) -> str:
    """A rule in words, as the arithmetic reads: '8% of the portfolio from
    Investment Grade Fixed Income'; '7.5% of Investment Grade Fixed Income as
    it stands'; with several sources, '2% of the portfolio, split Investment
    Grade Fixed Income 50% and Public Equity 50%', or '10% of each source as
    it stands, times its weight: ...'. The page writes the same words."""
    sources = rule.get('sources') or []
    size = _pct(rule['size'])
    if len(sources) <= 1:
        named = sources[0]['category'] if sources else 'no source'
        if rule.get('basis') == 'portfolio':
            return '{}% of the portfolio from {}'.format(size, named)
        return '{}% of {} as it stands'.format(size, named)
    split = ' and '.join('{} {}%'.format(s['category'], _pct(s['weightPct'])) for s in sources)
    if rule.get('basis') == 'portfolio':
        return '{}% of the portfolio, split {}'.format(size, split)
    return '{}% of each source as it stands, times its weight: {}'.format(size, split)


def overlaySourceCategories() -> list:
    """The categories an overlay may be funded from: the universe's, in its
    order, less the ones an overlay itself introduces - Asset Allocation
    Strategies is in the universe (the tilt fund's holding names it) but no
    strategic portfolio holds it."""
    return [c for c in categoriesInUniverseOrder() if c not in AUTO_SLEEVE_CATEGORIES]


def overlayRulesFor(variant=None) -> list:
    """The rules in force for *variant*: its own list, or the house list."""
    from . import overlayRules
    return overlayRules.current(variant)['rules']


def autoSleeveCategories() -> list:
    """Every category that carries its sleeve automatically: the two the
    seeded overlays introduce and every category a rule in the repository
    goes into (D155) - an overlay's category is never a choice in the rail."""
    from . import overlayRules
    out = list(AUTO_SLEEVE_CATEGORIES)
    for name in overlayRules.allIntos():
        if name not in out:
            out.append(name)
    return out


# ------------------------------------------ private markets, initially (D136) ---
# A private-markets commitment is drawn down as managers call capital, over
# years; until it is called, the money earmarked for it is held in other
# categories - by the split the repository holds (fundingSplit, D148; D136
# wrote it here as a third Investment Grade Fixed Income and two thirds Public
# Equity). The model builds this INITIAL allocation beside the long-term one:
# after both overlays (the parked money neither funds the tilt nor grows the
# premium), on the sleeve LINES, with each uplift spread across the chosen
# sleeve's products by the sleeve's own shares - which is what keeps it
# working when the tilt has taken every point of IGFI. It is a rule, not a
# choice: every book that holds private assets carries it, and step 1 never
# shows it.
PRIVATE_FUNDED_FROM = SLEEVE_GROUPS[0]['name']


def privateFunding(variant=None) -> list:
    """The split in force for *variant* as ``[(category, share)]``, shares as
    fractions: the type's own override, or the house split (D148)."""
    from . import fundingSplit
    return fundingSplit.shares(variant)


def initialLines(lines, funding=None):
    """The sleeve lines of a book as initially invested, or None when it holds
    no private markets (D136).

    *lines* are ``{'name', 'weightPct'}`` with the private categories already
    combined under PRIVATE_FUNDED_FROM, as ``buildImplementationRows`` makes
    them. The private line's weight P moves to the *funding* lines -
    ``[(category, share)]``, the house split when not given - in their shares,
    and the private line is left at zero: its commitment is the long-term
    weight, which the caller keeps. Returns copies.

    THE JAVASCRIPT MIRROR of this lives in implementation.js as
    initialLines(). The two must agree exactly or the screen and the workbook
    drift."""
    out = [{'name': line['name'], 'weightPct': float(line['weightPct'])} for line in lines]
    private = next((line for line in out if line['name'] == PRIVATE_FUNDED_FROM), None)
    weight = private['weightPct'] if private else 0.0
    if weight <= 0:
        return None
    for name, share in (privateFunding() if funding is None else funding):
        target = next((line for line in out if line['name'] == name), None)
        if target is None:                       # never in the universe today
            target = {'name': name, 'weightPct': 0.0}
            out.append(target)
        target['weightPct'] += weight * share
    private['weightPct'] = 0.0
    return out


def implementedCategories(categories, tacticalTilt, volPremium=False,
                          currency=None, variant=None, overlays=None) -> list:
    """The strategic categories as implemented: the overlay rules in force
    for *variant*, in their order (D155) - or *overlays*, a list given.

    The order is the list's: with the seeded house list the tilt comes first,
    because the premium's share is of what the tilt leaves behind. Every
    caller that builds an implementation model goes through here.
    """
    ruleList = overlayRulesFor(variant) if overlays is None else overlays
    return resolveOverlays(categories, ruleList,
                           {'tacticalTilt': tacticalTilt, 'volPremium': volPremium},
                           currency)['categories']


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
        if forceExRE and key.allocationType in _derived()['RE_ALLOWED'] \
                and not key.excludeRealAssets:
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
    if basis.currency not in _derived()['CURRENCIES']:
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
    """Reject a fee level outside the six the framework prices (D51), or the
    custom level beside them (D96)."""
    if not level:
        raise ValidationError('feeLevel', 'Choose a fee level.')
    if level not in fees.LEVELS and not fees.isCustom(level):
        raise ValidationError('feeLevel', 'Unknown fee level {!r}.'.format(level))


def _customRowLabel(schedule, group) -> str:
    return '{} \u00b7 {}'.format(schedule, group) if group else schedule


def validateCustomFees(customFees, mandate: MandateInput = None) -> None:
    """Reject custom rates that are not one number per row, or that fall
    outside the row's bounds (D96).

    The shape is {schedule: rate} for a uniform schedule and {schedule:
    {feeGroup: rate}} for a grouped one. A row may be absent or None - its
    products are then unpriced - but a rate that is present must be a real
    number between the bounding source's floor and ceiling for this mandate,
    which is the whole meaning of "custom": any value the PWA likes, within
    the room the card gives.
    """
    if customFees is None:
        return
    if not isinstance(customFees, dict):
        raise ValidationError('customFees', 'Custom fees must be a map of schedule to rate.')
    top = mandate.topAccountSize if mandate else None
    size = mandate.mandateSize if mandate else None
    for schedule, held in customFees.items():
        if schedule not in fees.SCHEDULES:
            raise ValidationError('customFees', 'Unknown fee schedule {!r}.'.format(schedule))
        if fees.byGroup(schedule):
            if held is None:
                continue
            if not isinstance(held, dict):
                raise ValidationError(
                    'customFees', '{} prices by fee group: give a rate per group.'.format(schedule))
            rows = dict(held)
        else:
            rows = {None: held}
        for group, value in rows.items():
            if value is None:
                continue
            label = _customRowLabel(schedule, group)
            if group is not None and group not in fees.FEE_GROUPS:
                raise ValidationError('customFees', 'Unknown fee group {!r}.'.format(group))
            if (isinstance(value, bool) or not isinstance(value, Real)
                    or value != value or value in (float('inf'), float('-inf'))):
                raise ValidationError(
                    'customFees', '{}: a custom rate must be a number.'.format(label))
            if top is None or size is None:
                continue                      # no mandate yet: nothing to bound against
            low, high = fees.customBounds(schedule, top, size, group)
            if value < low - 1e-6 or value > high + 1e-6:
                raise ValidationError(
                    'customFees',
                    '{}: {:.2f}% is outside {:.2f}% to {:.2f}%, the {} floor and ceiling '
                    'for this mandate.'.format(label, value, low, high,
                                               fees.CUSTOM_BOUNDS_SOURCE))


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
    if (variantForcesExcludeRE(variant) and key.allocationType in _derived()['RE_ALLOWED']
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


def _servedFunding(variant) -> dict:
    """The funding split as the schema serves it: shares as fractions for the
    page's mirror, the weights as entered, and which revision of which scope
    is in force."""
    from . import fundingSplit
    entry = fundingSplit.current(variant)
    return {'from': PRIVATE_FUNDED_FROM,
            'to': [{'category': d['category'], 'share': d['weightPct'] / 100.0,
                    'weightPct': d['weightPct']} for d in entry['destinations']],
            'scope': entry['scope'], 'revision': entry['revision'],
            'inherited': entry['inherited']}


def _servedOverlays(variant) -> dict:
    """The overlay rules as the schema serves them: the list in force for
    *variant*, which scope and revision it is, and whether it is the house
    list standing in for a type with none of its own."""
    from . import overlayRules
    entry = overlayRules.current(variant)
    return {'scope': entry['scope'], 'revision': entry['revision'],
            'inherited': entry['inherited'], 'rules': entry['rules']}


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
    derived = _derived()
    return {
        'options': {
            'currencies': derived['CURRENCIES'],
            'hedgingPolicies': HEDGING_POLICIES,
            'allocations': allocationsFor(mandateSize, variant),
            'riskLevels': derived['RISK_LEVELS'],
            'riskLevelLabels': RISK_LEVEL_LABELS,
            'reAllowed': derived['RE_ALLOWED'],
            # the facets: which allocation types each risk level offers (empty
            # for an all-equity level, which greys the selector), and which
            # types offer the ex-RAs toggle - both derived from the universe
            'allocationTypesByRisk': derived['_FACETS']['allocationTypes'],
            'exclusionOffered': derived['_FACETS']['exclusionOffered'],
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
            # the seeded overlays' two and every category a rule goes into (D155)
            'autoSleeveCategories': autoSleeveCategories(),
            'sleeveGroups': SLEEVE_GROUPS,
            # the initial allocation of a private-markets book (D136), by the
            # split in force for this type (D148): the page's mirror reads it
            # here, so the screen and the files park the money alike
            'privateFunding': _servedFunding(variant),
            # the overlay rules in force for this type, in the order they
            # resolve (D155): the page's mirror applies them as the model does
            'overlays': _servedOverlays(variant),
        },
        'fees': fees.feePayload(topAccountSize, mandateSize),
        'capabilities': capabilities,
        'dataInfo': dataInfo,
    }
