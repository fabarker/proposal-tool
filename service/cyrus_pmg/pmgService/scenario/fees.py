"""The management fee framework: CASP and RDR pricing (deviations D51, D55).

A product's management fee is not a property of the product. It is resolved
from four inputs at pricing time:

  fee schedule     CASP or RDR. CASP applies one rate to every product; RDR
                   prices each product by its fee group.
  top account size sets the account-size tier. Tiers are half-open [min, max)
                   in dollars and cover every non-negative size.
  fee level        one of six: {Management, PMG} x {Floor, Target, Ceiling}.
                   The level id is the source and the point joined by a space,
                   e.g. 'PMG Target'.
  fee group        the product's group under RDR; ignored under CASP.

WHERE THE DATA LIVES (D55). The rate card is DELIVERED by another team and
read from a long CSV at SCENARIO_FEES_SOURCE - one row per cell, every
coordinate stated: schedule, feeGroup, tier, tierMin, tierMax, source, point,
rate. The tiers and the fee groups are derived from those rows, the way the
strategic selectors derive from portfolio names (D54), so nothing here is a
list that can drift from the delivery. fees.json beside this module holds only
what is not the delivering team's to say - the schedules, the level vocabulary
in its order (which a row cannot supply), the default level, the placeholder
flag - and the delivery's provenance.

The card is READ ONLY in the service: it is what the delivering team sent, and
it changes by delivery, never by hand. feeTools --diff shows what a candidate
would move and --accept puts it in place; the service reads it at import, so a
new card is a restart, as a new strategic universe is. The UI shows the card
and cannot edit it.

Rates are annual management fees in percent (0.30 means 0.30%), the same unit
as productCost, so cost + fee is the all-in rate and the workbook's
"percent x percent = bp" weighted-fee arithmetic is unchanged.

Nothing here defaults a missing input. An unknown schedule, level or fee
group raises rather than pricing at a guess (spec 8.1). The fee level has a
prescribed default, 'PMG Target', which the store applies when it creates a
scenario; this module only names it.
"""

from __future__ import annotations

import csv
import json
import os
from numbers import Real

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_HERE, 'fees.json')
_DEFAULT_RATES = os.path.join(_HERE, 'feeRates.csv')


def ratesPath() -> str:
    """The delivered card: SCENARIO_FEES_SOURCE, else the packaged copy."""
    return os.path.abspath(os.getenv('SCENARIO_FEES_SOURCE', '') or _DEFAULT_RATES)


def levelId(source: str, point: str) -> str:
    """The id of a fee level: the source and the point joined by a space."""
    return '{} {}'.format(source, point)


def tierLabel(low, high) -> str:
    """'Under $10m', '$10m – $25m', '$100m and up' - presentation, derived."""
    def m(v):
        return '${:g}m'.format(v / 1e6)
    if low <= 0:
        return 'Under {}'.format(m(high))
    if high is None:
        return '{} and up'.format(m(low))
    return '{} – {}'.format(m(low), m(high))


class BadRateCard(ValueError):
    """The delivered card is not one this framework can price from."""


def _readDelivery(path: str) -> dict:
    """The delivered rows -> cells, tiers, fee groups. Rejects, never guesses."""
    cells, tiers, groups = {}, {}, []
    with open(path, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        need = {'schedule', 'feeGroup', 'tier', 'tierMin', 'tierMax', 'source', 'point', 'rate'}
        if not need.issubset(set(reader.fieldnames or [])):
            raise BadRateCard('{} lacks columns {}'.format(
                path, sorted(need - set(reader.fieldnames or []))))
        for n, row in enumerate(reader, start=2):
            schedule = row['schedule'].strip()
            group = row['feeGroup'].strip() or None
            tier = row['tier'].strip()
            try:
                low = float(row['tierMin'])
                high = float(row['tierMax']) if row['tierMax'].strip() else None
                rate = float(row['rate'])
            except ValueError:
                raise BadRateCard('{} row {}: a number is not a number'.format(path, n))
            if rate < 0:
                raise BadRateCard('{} row {}: negative rate'.format(path, n))
            edges = (low, high)
            if tier in tiers and tiers[tier] != edges:
                raise BadRateCard('{} row {}: tier {} edges disagree with an earlier row'
                                  .format(path, n, tier))
            tiers[tier] = edges
            if group and group not in groups:
                groups.append(group)
            key = (schedule, group, tier, row['source'].strip(), row['point'].strip())
            if key in cells:
                raise BadRateCard('{} row {}: duplicate cell {}'.format(path, n, key))
            cells[key] = rate
    ordered = sorted(tiers.items(), key=lambda kv: kv[1][0])
    return {
        'cells': cells,
        'tiers': [{'id': tid, 'label': tierLabel(low, high), 'min': low, 'max': high}
                  for tid, (low, high) in ordered],
        'feeGroups': groups,
        'rows': len(cells),
    }


_CONFIG = json.load(open(_CONFIG_PATH, encoding='utf-8'))
_DELIVERED = _readDelivery(ratesPath())

PLACEHOLDER = bool(_CONFIG.get('placeholder'))
SCHEDULES = [s['id'] for s in _CONFIG['schedules']]
SCHEDULE_NOTES = {s['id']: s['note'] for s in _CONFIG['schedules']}
_BY_GROUP = {s['id']: bool(s['byGroup']) for s in _CONFIG['schedules']}
SOURCES = list(_CONFIG['sources'])
POINTS = list(_CONFIG['points'])
LEVELS = [levelId(s, p) for s in SOURCES for p in POINTS]
DEFAULT_LEVEL = _CONFIG['defaultLevel']
TIERS = [dict(t) for t in _DELIVERED['tiers']]
FEE_GROUPS = list(_DELIVERED['feeGroups'])
DELIVERY = dict(_CONFIG.get('delivery') or {})


def splitLevel(level: str) -> tuple:
    """(source, point) for a level id; KeyError when it is not one of LEVELS."""
    if level not in LEVELS:
        raise KeyError('Unknown fee level: {!r}'.format(level))
    source, point = level.split(' ', 1)
    return source, point


def tierFor(topAccountSize) -> dict:
    """The account-size tier containing *topAccountSize* (half-open [min, max)).

    ValueError for a missing, non-numeric or negative size; a tier is a
    statement about a real account and a guess would price it wrong.
    """
    if isinstance(topAccountSize, bool) or not isinstance(topAccountSize, Real):
        raise ValueError('Top account size must be a number, got {!r}'.format(topAccountSize))
    if topAccountSize < 0:
        raise ValueError('Top account size cannot be negative: {!r}'.format(topAccountSize))
    for tier in TIERS:
        if tier['min'] <= topAccountSize and (tier['max'] is None or topAccountSize < tier['max']):
            return dict(tier)
    raise ValueError('No account-size tier covers {!r}'.format(topAccountSize))


def byGroup(schedule: str) -> bool:
    """Whether *schedule* prices by fee group (RDR) or uniformly (CASP)."""
    if schedule not in _BY_GROUP:
        raise KeyError('Unknown fee schedule: {!r}'.format(schedule))
    return _BY_GROUP[schedule]


# ------------------------------------------------ the rates ---------------

def _rate(schedule, group, tier, source, point) -> float:
    try:
        return float(_DELIVERED['cells'][(schedule, group, tier, source, point)])
    except KeyError:
        raise KeyError('No rate for {} {} {} {} {}'.format(schedule, group, tier, source, point))


def managementFee(schedule: str, topAccountSize, level: str, feeGroup: str = None) -> float:
    """The annual management fee, in percent, for one product.

    Under CASP the fee group is ignored; under RDR it is required and must be
    one of FEE_GROUPS. Unknown inputs raise (KeyError for a schedule, level or
    group; ValueError for the account size).
    """
    tierId = tierFor(topAccountSize)['id']
    source, point = splitLevel(level)
    if not byGroup(schedule):
        return _rate(schedule, None, tierId, source, point)
    if feeGroup not in FEE_GROUPS:
        raise KeyError('Unknown fee group: {!r}'.format(feeGroup))
    return _rate(schedule, feeGroup, tierId, source, point)


def ratesAtTier(tierId: str) -> dict:
    """Both schedules' rates at one tier, flattened to level ids.

    Shape: {schedule: {'byGroup': bool, 'levels': {level: rate}}} for a
    uniform schedule and {schedule: {'byGroup': bool, 'groups': {group:
    {level: rate}}}} for a grouped one. The client's mirror resolver reads
    exactly this and never learns the schedule names.
    """
    def flatten(group):
        return {levelId(s, p): _rate(schedule, group, tierId, s, p)
                for s in SOURCES for p in POINTS}

    out = {}
    for schedule in SCHEDULES:
        if byGroup(schedule):
            out[schedule] = {'byGroup': True, 'groups': {g: flatten(g) for g in FEE_GROUPS}}
        else:
            out[schedule] = {'byGroup': False, 'levels': flatten(None)}
    return out


def deliveryInfo() -> dict:
    """Provenance of the delivered card, for describe() and the workbook."""
    info = dict(DELIVERY)
    info.update(path=ratesPath(), cells=_DELIVERED['rows'], placeholder=PLACEHOLDER)
    return info


def feePayload(topAccountSize=None) -> dict:
    """The 'fees' block of the schema payload (spec 4.3: schema is data).

    Without a top account size there is no tier and no rates, and the client
    shows the fee columns unpriced.
    """
    tier = tierFor(topAccountSize) if topAccountSize is not None else None
    return {
        'placeholder': PLACEHOLDER,
        'schedules': [{'id': s, 'note': SCHEDULE_NOTES[s], 'byGroup': byGroup(s)} for s in SCHEDULES],
        'sources': SOURCES,
        'points': POINTS,
        'levels': [{'id': levelId(s, p), 'source': s, 'point': p} for s in SOURCES for p in POINTS],
        'defaultLevel': DEFAULT_LEVEL,
        'feeGroups': FEE_GROUPS,
        'tiers': [dict(t) for t in TIERS],
        'tier': tier,
        'rates': ratesAtTier(tier['id']) if tier else None,
        'delivery': {k: DELIVERY.get(k) for k in ('source', 'version', 'asOf')},
    }


# ------------------------------------------------ the rates viewer (D55) ---

def card() -> dict:
    """Every cell at every tier, flat. The viewer pivots this in the client -
    by tier (a tier's fee groups) or by fee group (a group's tier ladder) -
    so flipping the axes is not a round trip. 180 cells is a small payload."""
    groups = []
    for schedule in SCHEDULES:
        for group in ([None] if not byGroup(schedule) else FEE_GROUPS):
            groups.append({'schedule': schedule, 'feeGroup': group})
    cells = {'|'.join([k[0], k[1] or '', k[2], k[3], k[4]]): float(v)
             for k, v in _DELIVERED['cells'].items()}
    return {'tiers': [dict(t) for t in TIERS], 'groups': groups,
            'levels': list(LEVELS), 'sources': SOURCES, 'points': POINTS,
            'cells': cells, 'delivery': deliveryInfo()}


def grid(tierId: str) -> dict:
    """Every cell at one tier: a row per schedule and fee group, a column per
    level."""
    if tierId not in {t['id'] for t in TIERS}:
        raise KeyError('Unknown tier: {!r}'.format(tierId))
    rows = []
    for schedule in SCHEDULES:
        for group in ([None] if not byGroup(schedule) else FEE_GROUPS):
            rows.append({'schedule': schedule, 'feeGroup': group,
                         'cells': {levelId(s, p): _rate(schedule, group, tierId, s, p)
                                   for s in SOURCES for p in POINTS}})
    return {'tier': dict(next(t for t in TIERS if t['id'] == tierId)),
            'levels': list(LEVELS), 'sources': SOURCES, 'points': POINTS,
            'rows': rows, 'delivery': deliveryInfo()}


def _checkBands(cells, touched) -> None:
    """floor <= target <= ceiling on every band an edit touched."""
    for (schedule, group, tier, source, _point) in touched:
        band = [cells[(schedule, group, tier, source, p)] for p in POINTS]
        if any(a > b for a, b in zip(band, band[1:])):
            where = '{} {} {} {}'.format(schedule, group or '', tier, source).replace('  ', ' ')
            raise ValueError('{}: floor <= target <= ceiling fails ({})'.format(
                where, ' / '.join('{:g}'.format(x) for x in band)))


# ------------------------------------------------ the delivery, checked ----

def _assertWellFormed() -> None:
    """Fail at import if the card is inconsistent.

    Exactly two schedules, one uniform and one grouped; the level vocabulary
    complete; tiers contiguous from zero and open at the top; every cell the
    framework will ever ask for present; and at every cell floor <= target
    <= ceiling, since a level is a point on a band and a band that crosses
    itself is a data error.
    """
    if sorted(_BY_GROUP.values()) != [False, True]:
        raise BadRateCard('fees.json must describe one uniform and one grouped schedule')
    if DEFAULT_LEVEL not in LEVELS:
        raise BadRateCard('defaultLevel {!r} is not a level'.format(DEFAULT_LEVEL))
    if {'Floor', 'Target', 'Ceiling'} - set(POINTS):
        raise BadRateCard('points must include Floor, Target and Ceiling')
    if not TIERS or TIERS[0]['min'] != 0 or TIERS[-1]['max'] is not None:
        raise BadRateCard('tiers must start at 0 and the last must be open-ended')
    for earlier, later in zip(TIERS, TIERS[1:]):
        if earlier['max'] is None or earlier['max'] != later['min']:
            raise BadRateCard('tiers {} and {} are not contiguous'.format(earlier['id'], later['id']))
        if earlier['max'] <= earlier['min']:
            raise BadRateCard('tier {} is empty'.format(earlier['id']))
    if not FEE_GROUPS:
        raise BadRateCard('the card names no fee group')
    cells = _DELIVERED['cells']
    for schedule in SCHEDULES:
        for group in ([None] if not byGroup(schedule) else FEE_GROUPS):
            for tier in TIERS:
                for source in SOURCES:
                    for point in POINTS:
                        if (schedule, group, tier['id'], source, point) not in cells:
                            raise BadRateCard('no rate for {} {} {} {} {}'.format(
                                schedule, group or '', tier['id'], source, point))
    _checkBands(cells, set(cells))
    stray = {k for k in cells if k[3] not in SOURCES or k[4] not in POINTS
             or k[0] not in SCHEDULES}
    if stray:
        raise BadRateCard('rows outside the framework vocabulary: {}'.format(sorted(stray)[:3]))


_assertWellFormed()
