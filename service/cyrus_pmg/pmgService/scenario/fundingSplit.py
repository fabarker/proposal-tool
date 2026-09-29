"""The private-markets funding split, held in the repository (D148).

A private-markets commitment is called over years. Until it is, the money
earmarked for it is held in other categories, and this module says which and
in what proportion: a short list of DESTINATIONS, each a category and a
weight in percent, the weights adding up to exactly 100. D136 wrote it as a
constant in rules.py, a third Investment Grade Fixed Income and two thirds
Public Equity. It is now a repository setting the desk changes in the
console, with a note, a history and a restore, like a sleeve.

Scopes. The HOUSE split (scope '*') applies to every implementation type. A
type may carry an OVERRIDE of its own, which then applies to it alone;
removing the override puts the type back on the house split. None is set on
first open.

Weights. Percentages with at most four decimal places, each above zero,
adding up to exactly 100.0000. Four places lets a third be written as
33.3333, so the seeded house split, 33.3333 and 66.6667, reproduces D136's
thirds to within a millionth of a percentage point, which no printed figure
can show.

Destinations. A category every private-markets book holds (the recommendation
the desk took, proposals/allocation-rules-and-funding-split.html §7):
otherwise a book without it would have nowhere to put its share. Judged
against the strategic universe as loaded, so it moves with the data - today
Investment Grade Fixed Income, Other Fixed Income and Public Equity, each held
by all 112 private-markets books, and not Hedge Funds, held by 56. The private
categories themselves and the two automatic categories are never destinations.

Storage. Two tables in the repository's own file (sleeveRepo, schema
revision 4): ``policies`` holds the split in force per scope, and
``policyHistory`` one append-only row per revision - the split in full, the
note, who and when. A restore adds a revision; nothing is rewritten. The
house split is seeded, once, the first time anything reads it: a *baseline*
revision recording D136's thirds, so an upgrade changes no figure.

Reads open a connection per call, like the sleeves: the split is read when
a model or a schema is built, a handful of rows, and a save from one admin is
seen by the next request from anyone - which is decision 5: a change reaches
scenarios in progress at once. A delivered proposal keeps the split it was
built with; the register records it (proposalRegister, schema 4).
"""

from __future__ import annotations

import datetime
import json
import threading

from . import sleeveRepo, universe
from .types import ValidationError

KIND = 'funding'
HOUSE = '*'
PLACES = 4
MAX_DESTINATIONS = 3
_SCALE = 10 ** PLACES
_WHOLE = 100 * _SCALE

#: D136's thirds, as percentages to four places: what the house split is
#: seeded with, and what it was before this module existed
BASELINE = [('Investment Grade Fixed Income', 33.3333), ('Public Equity', 66.6667)]

ACTIONS = ['baseline', 'updated', 'reverted', 'removed']

_lock = threading.Lock()
_eligibleCache = None        # (universe generation, [entries])


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


# ----------------------------------------------------------- eligibility ---

def _privateCategories() -> set:
    from .rules import SLEEVE_GROUPS
    return {c for g in SLEEVE_GROUPS for c in g['categories']}


def eligible() -> list:
    """Every category a split could name, in the universe's order, with how
    many of the private-markets books hold it: ``{category, heldBy, of,
    eligible}``. A category is eligible when every private-markets book holds
    it. Cached against the universe's generation."""
    global _eligibleCache
    generation = universe.generation()
    with _lock:
        if _eligibleCache is not None and _eligibleCache[0] == generation:
            return [dict(e) for e in _eligibleCache[1]]
    from .rules import AUTO_SLEEVE_CATEGORIES, categoriesInUniverseOrder
    private = _privateCategories()
    books, held = 0, {}
    for key in universe.keys():
        rows = universe.categoryRows(key)
        holds = {r['name'] for r in rows if float(r.get('weightPct') or 0.0) > 0}
        if not holds & private:
            continue
        books += 1
        for name in holds:
            held[name] = held.get(name, 0) + 1
    out = []
    for name in categoriesInUniverseOrder():
        if name in private or name in AUTO_SLEEVE_CATEGORIES:
            continue
        count = held.get(name, 0)
        out.append({'category': name, 'heldBy': count, 'of': books,
                    'eligible': books > 0 and count == books})
    with _lock:
        _eligibleCache = (generation, [dict(e) for e in out])
    return out


# ------------------------------------------------------------ validation ---

def _places(value: float) -> bool:
    """Whether *value* has at most PLACES decimal places."""
    return abs(value * _SCALE - round(value * _SCALE)) < 1e-6


def normalise(destinations) -> list:
    """The canonical form of a split, ``[{category, weightPct}]`` in the
    order given, or a ValidationError naming ``destinations``."""
    if not isinstance(destinations, (list, tuple)) or not destinations:
        raise ValidationError('destinations', 'Name at least one category to hold the money.')
    if len(destinations) > MAX_DESTINATIONS:
        raise ValidationError('destinations', 'At most {} categories can hold the money.'.format(
            MAX_DESTINATIONS))
    known = {e['category']: e for e in eligible()}
    private = _privateCategories()
    out, seen, units = [], set(), 0
    for position, entry in enumerate(destinations, start=1):
        if not isinstance(entry, dict):
            raise ValidationError('destinations', 'Destination {} is not a category and a weight.'
                                  .format(position))
        name = str(entry.get('category') or '').strip()
        if not name:
            raise ValidationError('destinations', 'Destination {} names no category.'.format(position))
        if name in seen:
            raise ValidationError('destinations', '{} is named twice.'.format(name))
        seen.add(name)
        if name in private:
            raise ValidationError('destinations', '{} is where the commitment goes; it cannot also '
                                  'hold it until it is called.'.format(name))
        if name not in known:
            raise ValidationError('destinations', '{} is not a category the strategic portfolios hold.'
                                  .format(name))
        if not known[name]['eligible']:
            e = known[name]
            raise ValidationError(
                'destinations', '{} is held by only {} of the {} private-markets portfolios; the other {} '
                'would have nowhere to put its share.'.format(name, e['heldBy'], e['of'],
                                                                e['of'] - e['heldBy']))
        try:
            weight = float(entry.get('weightPct'))
        except (TypeError, ValueError):
            raise ValidationError('destinations', 'Give {} a weight in percent.'.format(name))
        if not weight > 0:
            raise ValidationError('destinations', "{}'s weight must be above zero.".format(name))
        if weight > 100:
            raise ValidationError('destinations', "{}'s weight is over 100%.".format(name))
        if not _places(weight):
            raise ValidationError('destinations', "{}'s weight has more than {} decimal places."
                                  .format(name, PLACES))
        units += int(round(weight * _SCALE))
        out.append({'category': name, 'weightPct': round(weight, PLACES)})
    if units != _WHOLE:
        raise ValidationError('destinations', 'The weights add up to {:.{p}f}%; they must add up '
                              'to exactly 100%.'.format(units / _SCALE, p=PLACES))
    return out


def _scope(scope) -> str:
    scope = (scope or '').strip()
    if scope == HOUSE:
        return HOUSE
    if scope not in sleeveRepo.VARIANTS:
        raise ValidationError('scope', 'Unknown implementation type {!r}.'.format(scope))
    return scope


def _note(note) -> str:
    note = (note or '').strip()
    if not note:
        raise ValidationError('note', 'Say why: every change to the split carries a note.')
    return note


# --------------------------------------------------------------- storage ---

def _row(conn, scope):
    return conn.execute('SELECT * FROM policies WHERE kind = ? AND scope = ?',
                        (KIND, scope)).fetchone()


def _seedHouse(conn) -> None:
    """The house split's baseline, once - race-safe the way the library's
    seed is (sleeveRepo._seedOnce): asked again inside a write transaction."""
    if _row(conn, HOUSE) is not None:
        return
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    try:
        if _row(conn, HOUSE) is None:
            body = json.dumps([{'category': c, 'weightPct': w} for c, w in BASELINE])
            stamp = _now()
            conn.execute('INSERT INTO policies (kind, scope, body, revision, updatedBy, updatedAt) '
                         'VALUES (?, ?, ?, 1, ?, ?)', (KIND, HOUSE, body, 'system', stamp))
            conn.execute('INSERT INTO policyHistory (kind, scope, revision, action, body, note, '
                         'actor, at) VALUES (?, ?, 1, ?, ?, ?, ?, ?)',
                         (KIND, HOUSE, 'baseline', body,
                          'D136: a third in Investment Grade Fixed Income, two thirds in Public Equity',
                          'system', stamp))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _connect():
    conn = sleeveRepo.connection()
    _seedHouse(conn)
    return conn


def _entry(row, inherited=False) -> dict:
    return {'scope': row['scope'], 'revision': row['revision'],
            'destinations': json.loads(row['body']), 'updatedBy': row['updatedBy'],
            'updatedAt': row['updatedAt'], 'inherited': inherited}


def current(variant=None) -> dict:
    """The split in force for *variant*: its override, or the house split
    (``inherited`` True). No variant means the house split."""
    conn = _connect()
    try:
        if variant and variant != HOUSE:
            own = _row(conn, variant)
            if own is not None:
                return _entry(own)
            return _entry(_row(conn, HOUSE), inherited=True)
        return _entry(_row(conn, HOUSE))
    finally:
        conn.close()


def shares(variant=None) -> list:
    """The split in force as ``[(category, share)]``, shares as fractions:
    what the initial allocation parks the commitment by."""
    return [(d['category'], d['weightPct'] / 100.0) for d in current(variant)['destinations']]


def summary(variant=None) -> dict:
    """What a delivered proposal records of the split it was built with."""
    entry = current(variant)
    return {'scope': entry['scope'], 'revision': entry['revision'],
            'destinations': entry['destinations']}


def describe() -> dict:
    """Everything the console shows: the house split, every override, the
    categories and whether each may hold the money, and the types."""
    conn = _connect()
    try:
        rows = conn.execute('SELECT * FROM policies WHERE kind = ? ORDER BY scope',
                            (KIND,)).fetchall()
        byScope = {r['scope']: _entry(r) for r in rows}
        return {'house': byScope.get(HOUSE),
                'overrides': {s: e for s, e in byScope.items() if s != HOUSE},
                'variants': list(sleeveRepo.VARIANTS), 'eligible': eligible(),
                'places': PLACES, 'maxDestinations': MAX_DESTINATIONS}
    finally:
        conn.close()


def history(scope) -> list:
    """Every revision of one scope's split, newest first."""
    scope = _scope(scope)
    conn = _connect()
    try:
        rows = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? '
                            'ORDER BY revision DESC', (KIND, scope)).fetchall()
        return [{'scope': r['scope'], 'revision': r['revision'], 'action': r['action'],
                 'destinations': json.loads(r['body']) if r['body'] else None,
                 'note': r['note'], 'actor': r['actor'], 'at': r['at']} for r in rows]
    finally:
        conn.close()


def _write(conn, scope, destinations, action, note, user) -> None:
    """Put *destinations* in force for *scope* as a new revision."""
    top = conn.execute('SELECT COALESCE(MAX(revision), 0) FROM policyHistory WHERE kind = ? '
                       'AND scope = ?', (KIND, scope)).fetchone()[0]
    revision = top + 1
    body = json.dumps(destinations)
    stamp = _now()
    # REPLACE rather than an upsert: the host's SQLite may predate 3.24
    conn.execute('INSERT OR REPLACE INTO policies (kind, scope, body, revision, updatedBy, '
                 'updatedAt) VALUES (?, ?, ?, ?, ?, ?)',
                 (KIND, scope, body, revision, user or '', stamp))
    conn.execute('INSERT INTO policyHistory (kind, scope, revision, action, body, note, actor, at) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                 (KIND, scope, revision, action, body, note, user or '', stamp))


def save(scope, destinations, note, user='') -> dict:
    """Put a new split in force for *scope* ('*' or a type). Refused, naming
    the field, when the split is invalid, the note is missing, or nothing
    would change."""
    scope = _scope(scope)
    cleaned = normalise(destinations)
    note = _note(note)
    conn = _connect()
    try:
        with conn:
            existing = _row(conn, scope)
            if existing is not None and json.loads(existing['body']) == cleaned:
                raise ValidationError('destinations', 'That is the split already in force.')
            _write(conn, scope, cleaned, 'updated', note, user)
        return _entry(_row(conn, scope))
    finally:
        conn.close()


def removeOverride(scope, note, user='') -> dict:
    """Put *scope* back on the house split. The override's revisions stay on
    the record, closed by a *removed* revision."""
    scope = _scope(scope)
    if scope == HOUSE:
        raise ValidationError('scope', 'The house split cannot be removed, only changed.')
    note = _note(note)
    conn = _connect()
    try:
        with conn:
            if _row(conn, scope) is None:
                raise ValidationError('scope', '{} has no override to remove.'.format(scope))
            top = conn.execute('SELECT MAX(revision) FROM policyHistory WHERE kind = ? AND '
                               'scope = ?', (KIND, scope)).fetchone()[0]
            conn.execute('DELETE FROM policies WHERE kind = ? AND scope = ?', (KIND, scope))
            conn.execute('INSERT INTO policyHistory (kind, scope, revision, action, body, note, '
                         'actor, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                         (KIND, scope, top + 1, 'removed', '', note, user or '', _now()))
        return _entry(_row(conn, HOUSE), inherited=True)
    finally:
        conn.close()


def revert(scope, revision, note, user='') -> dict:
    """Put an earlier revision of *scope*'s split back in force, as a new
    revision - the restore is itself on the record."""
    scope = _scope(scope)
    note = _note(note)
    conn = _connect()
    try:
        with conn:
            row = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? AND '
                               'revision = ?', (KIND, scope, int(revision))).fetchone()
            if row is None:
                raise ValidationError('revision', 'There is no revision {} of this split.'.format(
                    revision))
            if not row['body']:
                raise ValidationError('revision', 'Revision {} removed the override; there is '
                                      'no split in it to restore.'.format(revision))
            # re-validated: the universe may have moved since it was saved
            cleaned = normalise(json.loads(row['body']))
            existing = _row(conn, scope)
            if existing is not None and json.loads(existing['body']) == cleaned:
                raise ValidationError('revision', 'That split is already in force.')
            _write(conn, scope, cleaned, 'reverted', note, user)
        return _entry(_row(conn, scope))
    finally:
        conn.close()
