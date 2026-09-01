"""The management fee framework: CASP and RDR pricing (deviation D51).

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

All of the data - schedules, sources, points, tiers, fee groups and both rate
grids - lives in fees.json beside this module, so swapping the placeholder
pricing for the published schedule is a data change and nothing here moves.
Rates are annual management fees in percent (0.30 means 0.30%), the same unit
as productCost, so cost + fee is the all-in rate and the workbook's
"percent x percent = bp" weighted-fee arithmetic is unchanged.

Nothing here defaults a missing input. An unknown schedule, level or fee
group raises rather than pricing at a guess (spec 8.1: nothing is chosen until
a PWA chooses it). The fee level has a prescribed default, 'PMG Target', which
the store applies when it creates a scenario; this module only names it.

This module has no intra-package imports so that sleeves.py can import it to
validate the fee group on every product at import time.
"""

from __future__ import annotations

import json
import os
from numbers import Real

_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fees.json')


def _load() -> dict:
    with open(_DATA_PATH, encoding='utf-8') as fh:
        return json.load(fh)


_DATA = _load()


def levelId(source: str, point: str) -> str:
    """The id of a fee level: the source and the point joined by a space."""
    return '{} {}'.format(source, point)


PLACEHOLDER = bool(_DATA.get('placeholder'))
SCHEDULES = [s['id'] for s in _DATA['schedules']]
SCHEDULE_NOTES = {s['id']: s['note'] for s in _DATA['schedules']}
_BY_GROUP = {s['id']: bool(s['byGroup']) for s in _DATA['schedules']}
SOURCES = list(_DATA['sources'])
POINTS = list(_DATA['points'])
LEVELS = [levelId(s, p) for s in SOURCES for p in POINTS]
DEFAULT_LEVEL = _DATA['defaultLevel']
TIERS = [dict(t) for t in _DATA['tiers']]
FEE_GROUPS = list(_DATA['feeGroups'])
_CASP = _DATA['casp']    # tierId -> source -> point -> rate
_RDR = _DATA['rdr']      # feeGroup -> tierId -> source -> point -> rate


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


def managementFee(schedule: str, topAccountSize, level: str, feeGroup: str = None) -> float:
    """The annual management fee, in percent, for one product.

    Under CASP the fee group is ignored; under RDR it is required and must be
    one of FEE_GROUPS. Unknown inputs raise (KeyError for a schedule, level or
    group; ValueError for the account size).
    """
    tierId = tierFor(topAccountSize)['id']
    source, point = splitLevel(level)
    if not byGroup(schedule):
        return float(_CASP[tierId][source][point])
    if feeGroup not in _RDR:
        raise KeyError('Unknown fee group: {!r}'.format(feeGroup))
    return float(_RDR[feeGroup][tierId][source][point])


def ratesAtTier(tierId: str) -> dict:
    """Both schedules' rates at one tier, flattened to level ids.

    Shape: {schedule: {'byGroup': bool, 'levels': {level: rate}}} for a
    uniform schedule and {schedule: {'byGroup': bool, 'groups': {group:
    {level: rate}}}} for a grouped one. The client's mirror resolver reads
    exactly this and never learns the schedule names.
    """
    def flatten(grid):
        return {levelId(s, p): float(grid[s][p]) for s in SOURCES for p in POINTS}

    out = {}
    for schedule in SCHEDULES:
        if byGroup(schedule):
            out[schedule] = {'byGroup': True,
                             'groups': {g: flatten(_RDR[g][tierId]) for g in FEE_GROUPS}}
        else:
            out[schedule] = {'byGroup': False, 'levels': flatten(_CASP[tierId])}
    return out


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
    }


def _assertWellFormed() -> None:
    """Fail at import if fees.json is inconsistent.

    Tiers must start at zero, be contiguous and end open; every grid must have
    a rate for every tier, source and point; and at every cell floor <= target
    <= ceiling, since a level is a point on a band and a band that crosses
    itself is a data error. Exactly two schedules - one uniform, one grouped -
    is what the framework describes.
    """
    if sorted(_BY_GROUP.values()) != [False, True]:
        raise AssertionError('fees.json must describe one uniform and one grouped schedule')
    if DEFAULT_LEVEL not in LEVELS:
        raise AssertionError('defaultLevel {!r} is not a level'.format(DEFAULT_LEVEL))
    if not TIERS or TIERS[0]['min'] != 0 or TIERS[-1]['max'] is not None:
        raise AssertionError('tiers must start at 0 and the last must be open-ended')
    for earlier, later in zip(TIERS, TIERS[1:]):
        if earlier['max'] is None or earlier['max'] != later['min']:
            raise AssertionError('tiers {} and {} are not contiguous'.format(earlier['id'], later['id']))
        if earlier['max'] <= earlier['min']:
            raise AssertionError('tier {} is empty'.format(earlier['id']))
    if len({t['id'] for t in TIERS}) != len(TIERS):
        raise AssertionError('duplicate tier ids')
    if len(FEE_GROUPS) != len(set(FEE_GROUPS)):
        raise AssertionError('duplicate fee groups')
    if {'Floor', 'Target', 'Ceiling'} - set(POINTS):
        raise AssertionError('points must include Floor, Target and Ceiling')

    def checkGrid(where, grid):
        for tier in TIERS:
            if tier['id'] not in grid:
                raise AssertionError('{} has no rates for tier {}'.format(where, tier['id']))
            for source in SOURCES:
                cell = grid[tier['id']].get(source)
                if cell is None:
                    raise AssertionError('{} tier {} has no {} rates'.format(where, tier['id'], source))
                for point in POINTS:
                    if not isinstance(cell.get(point), Real) or cell[point] < 0:
                        raise AssertionError('{} tier {} {} {} is not a rate'.format(
                            where, tier['id'], source, point))
                if not cell['Floor'] <= cell['Target'] <= cell['Ceiling']:
                    raise AssertionError('{} tier {} {}: floor <= target <= ceiling fails'.format(
                        where, tier['id'], source))

    checkGrid('casp', _CASP)
    if set(_RDR) != set(FEE_GROUPS):
        raise AssertionError('rdr grids {} do not match feeGroups {}'.format(sorted(_RDR), FEE_GROUPS))
    for group in FEE_GROUPS:
        checkGrid('rdr[{}]'.format(group), _RDR[group])


_assertWellFormed()
