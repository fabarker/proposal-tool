"""Custom portfolios: a proposal built product by product (D171).

A proposal is one of two kinds. A STRATEGIC proposal chooses an analysed
portfolio on the allocation step and implements it with sleeves. A CUSTOM
proposal is chosen in the same place - "Custom" in the rail's Allocation
selector - and is built on the implementation page from nothing: products and
their weights of the portfolio. Its categories follow from its products. There
is no starting point, so nothing is measured against one.

The scenario keeps the build under ``customPortfolio``:

    {active, profile, products: [{productId, weightPct, category}],
     builtBy, builtAt, approval, notice}

*active* says which kind the proposal is. Choosing a strategic allocation
again leaves the build where it is, and choosing Custom brings it back; the
strategic base and its sleeves are kept the same way while the proposal is
custom. *profile* is a risk level. It fills nothing in: it sets the limits.

The limits. Each category may weigh what the profile's strategic portfolio
gives it (its ANCHOR: the first allocation the implementation type offers at
that risk level), plus or minus ``soft`` points freely, plus or minus ``hard``
points with an admin's approval, and no further. No product may hold more
than ``concentration`` percent of the portfolio, and at most ``illiquid``
percent may sit in products that do not deal daily. A portfolio holds at most
``maxProducts`` products, every position meets its product's minimum, and
private markets need the $20m mandate a strategic book does.

Who builds one. An admin, always. A PWA, only while an admin has opened the
custom workflows to PWAs - one switch for custom portfolios and custom
sleeves alike (customAccess, D172). Who approves one: an admin who did not
build it. An approval is of one build against one profile under one type -
change any of them and it no longer applies.

What it may hold. The live products the type's library sleeves hold, each in
the category this type's sleeves put it in.

The limits are fixed (``LIMITS``); there is nothing for an admin to set but
who may build (D173).
"""

from __future__ import annotations

import hashlib
import json
import math

from . import assetEstimates, customAccess, products, rules, sleeveRepo
from .types import PortfolioKey, ValidationError

#: the implemented portfolio adds to 100 within half a hundredth of a point
BALANCE_TOLERANCE = 0.005
#: a weight is typed to two places
WEIGHT_PLACES = 2
NOTE_MAX = 300

#: what every build is checked against: points either side of the anchor
#: freely (soft) and with an admin's approval (hard), the most one product
#: and the products not dealing daily may hold, in percent, and the most
#: products
LIMITS = {'soft': 8.0, 'hard': 15.0, 'concentration': 25.0, 'illiquid': 30.0,
          'maxProducts': 40}

APPROVAL_ACTIONS = ('submit', 'withdraw', 'approve', 'return')


# ------------------------------------------------------------ who builds ---

def mayBuild(variant, admin: bool) -> bool:
    """Whether a caller may build or change a custom portfolio: an admin,
    or a PWA while the custom workflows are open to PWAs (D172)."""
    return customAccess.may(admin)


def forCaller(admin: bool) -> dict:
    """What the page is told: under which types this caller builds - every
    one, or none - and the limits every build is checked against. The server
    checks again."""
    variants = list(rules.VARIANT_ALLOCATIONS) if customAccess.may(admin) else []
    return {'mayBuild': variants, 'admin': bool(admin), 'limits': dict(LIMITS),
            'privateMinimum': rules.PRIVATE_ASSETS_MINIMUM,
            'privateLine': rules.PRIVATE_FUNDED_FROM, 'countedIn': countedIn(),
            'balanceTolerance': BALANCE_TOLERANCE, 'noteMax': NOTE_MAX}


# ----------------------------------------------------------- the products ---

def lines() -> list:
    """The lines a product can be counted in: every repository category, the
    grouped ones as their group, in the repository's order."""
    out = []
    for category in sleeveRepo.categories():
        line = rules.sleeveCategory(category)
        if line not in out:
            out.append(line)
    return out


def _sleeveCategories(variant=None) -> dict:
    """``{productId: line}`` from the live library sleeves - under *variant*
    alone when given - each product in the first category, in the
    repository's order, that holds it."""
    order = {c: i for i, c in enumerate(sleeveRepo.categories())}
    conn = sleeveRepo.connection()
    try:
        sql = ('SELECT DISTINCT p.productId, s.category FROM sleeveProducts p JOIN sleeves s '
               "ON s.id = p.sleeveId WHERE s.deletedAt = ''")
        rows = conn.execute(sql + (' AND s.variant = ?' if variant else ''),
                            (variant,) if variant else ()).fetchall()
    finally:
        conn.close()
    out = {}
    for row in sorted(rows, key=lambda r: order.get(r['category'], len(order))):
        out.setdefault(row['productId'], rules.sleeveCategory(row['category']))
    return out


def categories(variant) -> dict:
    """``{productId: line}`` for every live product that has one: this
    type's sleeves first, then any type's."""
    own = _sleeveCategories(variant)
    anyType = _sleeveCategories()
    out = {}
    for product in products.all():
        pid = product['productId']
        line = own.get(pid) or anyType.get(pid)
        if line:
            out[pid] = line
    return out


def allowedIds(variant) -> list:
    """The ids a custom portfolio under *variant* may hold, in catalogue order."""
    own = _sleeveCategories(variant)
    return [p['productId'] for p in products.all() if p['productId'] in own]


def library(variant, key=None) -> list:
    """The type's library sleeves, for "Insert a sleeve": every category's,
    as the picker serves them for the profile's anchor portfolio."""
    out = []
    for category in sleeveRepo.categories():
        line = rules.sleeveCategory(category)
        if category != line and any(s['category'] == line for s in out):
            continue
        for sleeve in sleeveRepo.listSleeves(line, variant, key):
            out.append({'category': line, 'name': sleeve['name'],
                        'products': [{'productId': p['productId'], 'weight': p['weight']}
                                     for p in sleeve['products']]})
    return out


def catalogue(variant) -> dict:
    """What the builder works from under *variant*: the products it may hold
    (whole, each with its category or none), every line, and the library."""
    allowed = set(allowedIds(variant))
    known = categories(variant)
    held = [dict(p, category=known.get(p['productId'])) for p in products.all()
            if p['productId'] in allowed]
    return {'variant': variant, 'products': held, 'lines': lines()}


# ------------------------------------------------------------ the profile ---

def _isAllEquity(currency, riskLevel) -> bool:
    from . import universe
    return universe.has(PortfolioKey(currency, riskLevel, None, None))


def anchorKey(currency, variant, riskLevel):
    """The profile's strategic portfolio under *variant*: an all-equity
    level's own book, else the first allocation the type offers at that
    level. The mandate does not narrow it: the limits describe the risk
    level, not what this mandate could hold."""
    from . import universe
    if riskLevel not in rules._derived()['RISK_LEVELS']:
        return None
    if _isAllEquity(currency, riskLevel):
        return PortfolioKey(currency, riskLevel, None, None)
    for allocation in rules.allocationsForVariant(variant):
        exclude = (rules.variantForcesExcludeRE(variant)
                   and allocation in rules._derived()['RE_ALLOWED'])
        key = PortfolioKey(currency, riskLevel, allocation, exclude)
        if universe.has(key):
            return key
    return None


def anchorLines(result) -> dict:
    """``{line: weightPct}`` of a resolved portfolio, grouped categories as one."""
    out = {}
    for category in result['categories']:
        line = rules.sleeveCategory(category['name'])
        out[line] = out.get(line, 0.0) + float(category['weightPct'])
    return out


def countedIn() -> dict:
    """The lines held to another line's limits: the overlays' own lines,
    each counted in the line a strategic proposal funds it from - so a build
    holding the house's own proposal, tilt and premium included, sits on its
    anchor."""
    return {rules.TACTICAL_TILT_CATEGORY: rules.TACTICAL_TILT_FUNDED_FROM,
            rules.VOL_PREMIUM_CATEGORY: rules.VOL_PREMIUM_FUNDED_FROM}


def limited(weights: dict) -> dict:
    """*weights* by line with each counted line folded into its funder."""
    folded = countedIn()
    out = {}
    for line, weight in weights.items():
        target = folded.get(line, line)
        out[target] = out.get(target, 0.0) + weight
    return out


def limits(anchor: dict, lineNames, entry=None) -> dict:
    """``{line: {centre, soft: [lo, hi], hard: [lo, hi]}}``: what each line
    may weigh, from the anchor's weights; a line the anchor does not hold is
    centred on nothing. *entry* is the limits, ``LIMITS`` unless given."""
    entry = entry or LIMITS
    out = {}
    for line in lineNames:
        centre = float(anchor.get(line, 0.0))
        out[line] = {'centre': centre,
                     'soft': [max(0.0, centre - entry['soft']), min(100.0, centre + entry['soft'])],
                     'hard': [max(0.0, centre - entry['hard']), min(100.0, centre + entry['hard'])]}
    return out


def lineStats(result, currency, hedging) -> dict:
    """``{line: {ret, vol}}``: each line's long-term total return and
    volatility in percent, its assets weighted as the anchor holds them. A
    line the anchor does not hold takes its category's estimates evenly; a
    line with no estimate of its own (Hybrid Fixed Income) is counted at
    Other Fixed Income's and marked ``borrowed``."""
    rows = assetEstimates.forSlice(currency, hedging)
    byName = {r['reportingName']: r for r in rows}
    acc = {}
    for category in result['categories']:
        line = rules.sleeveCategory(category['name'])
        for asset in category.get('assets') or []:
            est = byName.get(asset['reportingName'])
            if not est or not asset['weightPct']:
                continue
            total = acc.setdefault(line, [0.0, 0.0, 0.0])
            total[0] += asset['weightPct']
            total[1] += asset['weightPct'] * est['totalReturn'] * 100.0
            total[2] += asset['weightPct'] * est['volatility'] * 100.0
    for line in {rules.sleeveCategory(r.get('category') or '') for r in rows}:
        if not line or line in acc:
            continue
        group = [r for r in rows if rules.sleeveCategory(r.get('category') or '') == line]
        acc[line] = [float(len(group)), sum(r['totalReturn'] * 100.0 for r in group),
                     sum(r['volatility'] * 100.0 for r in group)]
    out = {line: {'ret': t[1] / t[0], 'vol': t[2] / t[0]} for line, t in acc.items() if t[0]}
    hybrid = rules.VOL_PREMIUM_CATEGORY
    if hybrid not in out and 'Other Fixed Income' in out:
        out[hybrid] = dict(out['Other Fixed Income'], borrowed=True)
    return out


def figures(weights: dict, stats: dict, anchorResult) -> dict:
    """What can be said of a mix nobody analysed: its expected return, the
    lines' long-term returns weighted; its volatility's upper limit, their
    volatilities weighted (no diversification at all); and an indicative
    volatility, the anchor's analysed figure scaled by how the two limits
    compare. ``None`` throughout for an empty mix."""
    total = sum(weights.values())
    if total <= 0:
        return {'estimatedReturnPct': None, 'volatilityLimitPct': None, 'volatilityPct': None}
    ret = sum(w / 100.0 * stats.get(line, {}).get('ret', 0.0) for line, w in weights.items())
    cap = sum(w / 100.0 * stats.get(line, {}).get('vol', 0.0) for line, w in weights.items())
    anchorWeights = anchorLines(anchorResult) if anchorResult else {}
    anchorCap = sum(w / 100.0 * stats.get(line, {}).get('vol', 0.0)
                    for line, w in anchorWeights.items())
    analysed = ((anchorResult or {}).get('metrics') or {}).get('volatilityPct')
    indicative = analysed * cap / anchorCap if analysed is not None and anchorCap > 0 else None
    return {'estimatedReturnPct': ret, 'volatilityLimitPct': cap, 'volatilityPct': indicative}


#: whose asset mix the lines' long-term estimates are weighted by, so that a
#: build's figures do not move when only its risk profile does
STATS_LEVEL = 'Moderate'


def estimates(port, basis, variant) -> dict:
    """The lines' long-term estimates under this basis (``lineStats``),
    weighted by the middle risk level's anchor, else the first level's."""
    order = [STATS_LEVEL] + [r for r in rules._derived()['RISK_LEVELS'] if r != STATS_LEVEL]
    for riskLevel in order:
        key = anchorKey(basis.currency, variant, riskLevel)
        if key is None or _isAllEquity(basis.currency, riskLevel):
            continue
        try:
            return lineStats(port.resolve_portfolio(basis, key), basis.currency, basis.hedging)
        except Exception:                      # noqa: BLE001 - try the next level
            continue
    return {}


def anchorFor(port, basis, variant, riskLevel):
    """The profile's anchor resolved, or None where the type has none."""
    key = anchorKey(basis.currency, variant, riskLevel)
    return port.resolve_portfolio(basis, key) if key is not None else None


def profiles(port, basis, variant) -> dict:
    """Every risk level's anchor under *variant*, for the page: its key and
    name, its weights by line and its analysed figures, and the lines'
    long-term estimates. A level with no anchor under the type is left out."""
    out = {}
    for riskLevel in rules._derived()['RISK_LEVELS']:
        key = anchorKey(basis.currency, variant, riskLevel)
        if key is None:
            continue
        try:
            result = port.resolve_portfolio(basis, key)
        except Exception:                      # noqa: BLE001 - a level not baked is not offered
            continue
        out[riskLevel] = {'key': key.toStr(), 'name': result['name'], 'header': result['header'],
                          'label': rules.RISK_LEVEL_LABELS.get(riskLevel, riskLevel),
                          'lines': anchorLines(result), 'metrics': result.get('metrics') or {}}
    return {'profiles': out, 'lineStats': estimates(port, basis, variant)}


# ------------------------------------------------------------- the build ---

def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _weight(value):
    if value is None or value == '':
        return None
    number = _number(value)
    if number is None or number < 0 or number > 100:
        raise ValidationError('customPortfolio', 'A weight is a percentage of the portfolio, '
                              'from 0 to 100.')
    if abs(round(number, WEIGHT_PLACES) - number) > 1e-9:
        raise ValidationError('customPortfolio', 'A weight is typed to two places.')
    return round(number, WEIGHT_PLACES)


def _label(productId) -> str:
    product = products.get(productId)
    return product['name'] if product else productId


def clean(definition, variant, entry=None, keep=None) -> dict:
    """*definition* checked and normalised, or ValidationError naming what is
    wrong: ``{profile, products: [{productId, weightPct, category}]}``. A
    build in progress is a build: weights may still be missing and the
    total anything - the export is what refuses an unfinished one.
    *keep* maps the products the stored build already holds to their
    categories: one of them is kept even where it may no longer be held
    (``checks`` says so), so the build can still be changed around it."""
    if not isinstance(definition, dict):
        raise ValidationError('customPortfolio', 'A custom portfolio is a risk profile, products '
                              'and weights.')
    entry = entry or LIMITS
    profile = definition.get('profile')
    if profile not in rules._derived()['RISK_LEVELS']:
        raise ValidationError('customPortfolio', 'Choose the portfolio\'s risk profile.')
    rows = definition.get('products') or []
    if not isinstance(rows, list):
        raise ValidationError('customPortfolio', 'The products are a list.')
    if len(rows) > entry['maxProducts']:
        raise ValidationError('customPortfolio', 'A custom portfolio holds at most {} products; '
                              'this one has {}.'.format(entry['maxProducts'], len(rows)))
    keep = keep or {}
    allowed = set(allowedIds(variant)) | set(keep)
    known = categories(variant)
    lineNames = set(lines())
    seen, out = set(), []
    for row in rows:
        pid = str((row or {}).get('productId') or '').strip()
        if not pid:
            raise ValidationError('customPortfolio', 'A product in the portfolio has no id.')
        if pid in seen:
            raise ValidationError('customPortfolio', '{} is in the portfolio twice.'.format(_label(pid)))
        seen.add(pid)
        if pid not in allowed:
            raise ValidationError('customPortfolio', '{} is not a product a custom portfolio under '
                                  '{} may hold.'.format(_label(pid), variant))
        category = known.get(pid) or (row or {}).get('category') or keep.get(pid)
        if category not in lineNames:
            raise ValidationError('customPortfolio', 'Choose the category {} is counted in.'.format(
                _label(pid)))
        out.append({'productId': pid, 'weightPct': _weight((row or {}).get('weightPct')),
                    'category': category})
    # a build on its way may add to more than 100% for a moment - a weight
    # raised before another is lowered; the page says how much too much, and
    # the export refuses anything but 100%
    return {'profile': profile, 'products': out}


def fingerprint(definition, variant) -> str:
    """What an approval is of: this profile, these products at these weights
    in these categories, under this type."""
    body = {'profile': (definition or {}).get('profile'), 'variant': variant,
            'products': sorted([[p['productId'], p.get('weightPct'), p.get('category')]
                                for p in (definition or {}).get('products') or []],
                               key=lambda r: r[0])}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode('utf-8')).hexdigest()[:16]


def weightsByLine(definition) -> dict:
    out = {}
    for row in (definition or {}).get('products') or []:
        out[row['category']] = out.get(row['category'], 0.0) + float(row.get('weightPct') or 0.0)
    return out


def checks(definition, variant, mandateSize, anchor: dict, entry=None) -> dict:
    """The build against its limits: ``{status, problems, wide, lines}``.
    *status* is ``bad`` while anything stops it being delivered, ``warn``
    while it delivers only with an admin's approval, ``ok`` otherwise.
    *problems* say what is wrong, *wide* names the lines outside their usual
    range, and *lines* is each line's weight against its limits. Minimums are
    the model's to find (``buildModel``)."""
    entry = entry or LIMITS
    rows = (definition or {}).get('products') or []
    problems = []
    if not rows:
        # nothing built: every line the profile holds, at nothing, against
        # its range - what a blank slate is checked against
        names = [n for n in lines() if n in anchor] + [n for n in anchor if n not in lines()]
        bands = limits(anchor, names, entry)
        return {'status': 'bad', 'problems': ['Nothing is built yet.'], 'wide': [],
                'lines': {n: dict(bands[n], weightPct=0.0) for n in names}}
    allowed = set(allowedIds(variant))
    gone = [_label(r['productId']) for r in rows if r['productId'] not in allowed]
    if gone:
        problems.append('{} {} no longer a product this portfolio may hold.'.format(
            ', '.join(gone), 'is' if len(gone) == 1 else 'are'))
    unweighted = [_label(r['productId']) for r in rows if not r.get('weightPct')]
    if unweighted:
        problems.append('Give {} a weight, or remove {}.'.format(
            ', '.join(unweighted), 'it' if len(unweighted) == 1 else 'them'))
    total = sum(float(r.get('weightPct') or 0.0) for r in rows)
    balanced = abs(total - 100.0) < BALANCE_TOLERANCE
    if not balanced:
        problems.append('The portfolio adds to {:.2f}%, not 100%.'.format(total))
    if len(rows) > entry['maxProducts']:
        problems.append('{} products; at most {}.'.format(len(rows), entry['maxProducts']))
    weights = weightsByLine(definition)
    held = limited(weights)
    names = [n for n in lines() if n in held or n in anchor]
    names += [n for n in held if n not in names]
    bands = limits(anchor, names, entry)
    outside, wide = [], []
    for line in names:
        w, band = held.get(line, 0.0), bands[line]
        # over a limit is over it however much is still to place; under one
        # only counts once the portfolio adds to 100%
        if w > band['hard'][1] + 1e-9 or (balanced and w < band['hard'][0] - 1e-9):
            outside.append('{} at {:.2f}% ({:g}-{:g}%)'.format(line, w, *band['hard']))
        elif w > band['soft'][1] + 1e-9 or (balanced and w < band['soft'][0] - 1e-9):
            wide.append(line)
    if outside:
        problems.append('Outside what the profile allows: {}.'.format('; '.join(outside)))
    heavy = [_label(r['productId']) for r in rows
             if float(r.get('weightPct') or 0.0) > entry['concentration'] + 1e-9]
    if heavy:
        problems.append('{}: a single product holds at most {:g}% of the portfolio.'.format(
            ', '.join(heavy), entry['concentration']))
    illiquid = sum(float(r.get('weightPct') or 0.0) for r in rows
                   if (products.get(r['productId']) or {}).get('liquidity') != 'Daily')
    if illiquid > entry['illiquid'] + 1e-9:
        problems.append('{:.2f}% is not daily-dealing; at most {:g}%.'.format(
            illiquid, entry['illiquid']))
    private = weights.get(rules.PRIVATE_FUNDED_FROM, 0.0)
    if private > 0 and mandateSize is not None and mandateSize < rules.PRIVATE_ASSETS_MINIMUM:
        problems.append('Private markets need a mandate of ${:,.0f} or more.'.format(
            rules.PRIVATE_ASSETS_MINIMUM))
    status = 'bad' if problems else 'warn' if wide else 'ok'
    return {'status': status, 'problems': problems, 'wide': wide,
            'lines': {n: dict(bands[n], weightPct=held.get(n, 0.0)) for n in names}}


def wideText(check, profileLabel) -> str:
    bands = check['lines']
    return '{} {} outside the {} profile\'s usual range ({}).'.format(
        ', '.join('{} at {:.2f}%'.format(n, bands[n]['weightPct']) for n in check['wide']),
        'is' if len(check['wide']) == 1 else 'are', profileLabel,
        ', '.join('{:g}-{:g}%'.format(*bands[n]['soft']) for n in check['wide']))


def approved(state_cp, variant) -> bool:
    """Whether the build in force carries an approval that still applies."""
    approval = (state_cp or {}).get('approval') or {}
    return (approval.get('state') == 'approved'
            and approval.get('fingerprint') == fingerprint(state_cp, variant))


def buildModel(definition, mandateSize, variant=None, feeSchedule=None, feeLevel=None,
               topAccountSize=None, currency=None, customFees=None) -> dict:
    """The implemented model of a custom portfolio: the build's lines as the
    categories, each implemented by its own products weighted as a share of
    the portfolio, through the very rounding, pricing, minimums and initial
    allocation a strategic proposal goes through. No overlay applies: in a
    custom portfolio the tilt and the premium are products like any other."""
    from .workbook import buildImplementationRows
    weights, byLine = {}, {}
    for row in (definition or {}).get('products') or []:
        weights[row['category']] = weights.get(row['category'], 0.0) + float(row.get('weightPct') or 0.0)
        byLine.setdefault(row['category'], []).append(
            {'productId': row['productId'], 'weightPct': float(row.get('weightPct') or 0.0)})
    order = [n for n in lines() if n in weights] + [n for n in weights if n not in lines()]
    base = {'categories': [{'name': n, 'weightPct': weights[n]} for n in order], 'keyStr': None}
    customs = {n: {'name': '', 'note': '', 'mode': 'portfolio', 'products': byLine[n]}
               for n in order if any(p['weightPct'] > 0 for p in byLine[n])}
    model = buildImplementationRows(
        base, {}, [], mandateSize, variant, False, feeSchedule, feeLevel, topAccountSize, False,
        currency, customFees=customFees,
        overlayEntry={'scope': '', 'revision': 0, 'rules': []}, customSleeves=customs)
    for group in model['groups']:
        group['sleeve'] = 'Custom portfolio' if group.get('sleeve') else None
        group.pop('custom', None)
    model['departs'] = False
    return model


def profileLabel(riskLevel) -> str:
    return rules.RISK_LEVEL_LABELS.get(riskLevel, riskLevel)
