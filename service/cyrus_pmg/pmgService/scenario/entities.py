"""The custodians / entities a proposal is for, held in the repository (D193).

A proposal is written for a client account held at one entity - GSI, GSBE,
GSBZ, Middle East - which the PWA chooses on the first card. The entity does
two things. An edition of a sleeve may be FOR particular entities, as it may
be for particular currencies (sleeveRules' fourth field). And each entity
carries a list of products it may not hold: a sleeve whose edition holds one
is greyed out at that entity, with the reason, and nothing excluded is
delivered there.

The list is the desk's, kept in the console's Entities tab: an admin adds an
entity, renames one, retires one and ticks the products it excludes, each
change with a note and a revision, like the funding split (fundingSplit.py,
whose pattern this follows). An entity keeps the id it was given when it was
added - a slug of its first name - for life, so a rename carries through to
every rule and scenario that names it: they hold the id. An entity is never
deleted. One retired is not offered on the first card; the rules, scenarios
and proposals that name it keep their meaning.

Storage. The ``policies`` and ``policyHistory`` tables (sleeveRepo, schema 4),
under the kind 'entities' and the one scope '*': the list in force, and one
append-only row per revision. It is seeded, once, with the four the desk
named, excluding nothing.
"""

from __future__ import annotations

import datetime
import json
import os
import re

from . import products, sleeveRepo
from .types import ValidationError

KIND = 'entities'
SCOPE = '*'
NAME_MAX = 40
NOTE_MAX = 240
MAX_ENTITIES = 24

#: the four the desk named, in the order the first card offers them
BASELINE = ['GSI', 'GSBE', 'GSBZ', 'Middle East']

ACTIONS = ['baseline', 'updated', 'reverted']

_ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def slug(name) -> str:
    """'Middle East' -> 'middle-east': an entity's id, given once."""
    return re.sub(r'[^a-z0-9]+', '-', str(name or '').lower()).strip('-')[:40] or 'entity'


def _baseline() -> list:
    return [{'id': slug(n), 'name': n, 'retired': False, 'excluded': [], 'note': ''}
            for n in BASELINE]


# --------------------------------------------------------------- storage ---

def _row(conn):
    return conn.execute('SELECT * FROM policies WHERE kind = ? AND scope = ?',
                        (KIND, SCOPE)).fetchone()


def _seed(conn) -> None:
    """The four, once - race-safe the way the funding split's seed is."""
    if _row(conn) is not None:
        return
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    try:
        if _row(conn) is None:
            body = json.dumps(_baseline())
            stamp = _now()
            conn.execute('INSERT INTO policies (kind, scope, body, revision, updatedBy, updatedAt) '
                         'VALUES (?, ?, ?, 1, ?, ?)', (KIND, SCOPE, body, 'system', stamp))
            conn.execute('INSERT INTO policyHistory (kind, scope, revision, action, body, note, '
                         'actor, at) VALUES (?, ?, 1, ?, ?, ?, ?, ?)',
                         (KIND, SCOPE, 'baseline', body,
                          'The four entities, excluding nothing', 'system', stamp))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _connect():
    conn = sleeveRepo.connection()
    _seed(conn)
    return conn


def _entry(row) -> dict:
    return {'revision': row['revision'], 'entities': json.loads(row['body']),
            'updatedBy': row['updatedBy'], 'updatedAt': row['updatedAt']}


def current() -> dict:
    """The list in force: {revision, entities, updatedBy, updatedAt}.

    Read through a plain read-only SQLite connection every time, and never
    through the file itself. The library's seed and every save read the list
    (a rule may name an entity) while they hold the file's write lock, and a
    read that opened and closed the file directly would drop that lock - on
    POSIX, closing any handle on a file releases every lock the process holds
    on it; SQLite's own connections are written to keep them. Nor does a read
    ever write: until the first change is saved, the list is the four, at
    revision 0, and the save writes their baseline first."""
    path = sleeveRepo.dbPath()
    found = None
    conn = sleeveRepo.readOnly(path) if os.path.exists(path) else None
    if conn is not None:
        try:
            row = _row(conn)
            found = _entry(row) if row is not None else None
        except Exception:                      # noqa: BLE001 - an older file: the four
            found = None
        finally:
            conn.close()
    if found is None:
        found = {'revision': 0, 'entities': _baseline(), 'updatedBy': '', 'updatedAt': ''}
    return found


def everyEntity() -> list:
    """Every entity, retired ones too, in the list's order."""
    return current()['entities']


def ids() -> list:
    return [e['id'] for e in current()['entities']]


def offered() -> list:
    """The entities the first card offers: every one not retired."""
    return [{'id': e['id'], 'name': e['name']} for e in current()['entities'] if not e['retired']]


def get(entityId):
    """One entity by id, or None."""
    return next((e for e in current()['entities'] if e['id'] == entityId), None)


def name(entityId) -> str:
    """An entity's name, or its id when the list no longer has it."""
    found = get(entityId)
    return found['name'] if found else str(entityId or '')


def resolve(text):
    """An entity's id from its id or its name, whatever the capitals; None
    for one the list does not have."""
    key = str(text or '').strip().lower()
    for e in current()['entities']:
        if e['id'] == key or e['name'].lower() == key:
            return e['id']
    return None


def excluded(entityId) -> set:
    """The product ids *entityId* may not hold; empty for none or no entity."""
    found = get(entityId) if entityId else None
    return set(found['excluded']) if found else set()


def exclusions() -> dict:
    """{entity id: [product ids]} for every entity excluding anything."""
    return {e['id']: list(e['excluded']) for e in current()['entities'] if e['excluded']}


def notAvailableAt(productId) -> list:
    """The names of the entities that exclude *productId*."""
    return [e['name'] for e in current()['entities'] if productId in e['excluded']]


def checkChoice(entityId, allowRetired=False) -> str:
    """The entity a scenario names, checked: '' for none, its id, or a
    ValidationError naming ``entity``."""
    if entityId is None or entityId == '':
        return ''
    if not isinstance(entityId, str):
        raise ValidationError('entity', 'Choose the custodian / entity from the list.')
    found = get(entityId.strip())
    if found is None:
        raise ValidationError('entity', 'There is no custodian / entity {!r}.'.format(entityId))
    if found['retired'] and not allowRetired:
        raise ValidationError('entity', '{} is retired; choose another custodian / entity.'.format(
            found['name']))
    return found['id']


# ------------------------------------------------------------ validation ---

def _note(note) -> str:
    if note is not None and not isinstance(note, str):
        raise ValidationError('note', 'Say why, in words: every change to the entities carries a note.')
    note = ' '.join((note or '').split())
    if not note:
        raise ValidationError('note', 'Say why: every change to the entities carries a note.')
    return note


def normalise(entities, previous=None) -> list:
    """The canonical list, or a ValidationError naming ``entities``. Each
    entry keeps its id; a new one (no id) gets the slug of its name. Names
    are unique whatever the capitals; every product excluded must be in the
    catalogue; and every entity the list had is still in it - an entity is
    retired, never deleted."""
    if not isinstance(entities, (list, tuple)):
        raise ValidationError('entities', 'The entities must be a list.')
    if not entities:
        raise ValidationError('entities', 'Keep at least one custodian / entity.')
    if len(entities) > MAX_ENTITIES:
        raise ValidationError('entities', 'Keep to {} entities.'.format(MAX_ENTITIES))
    before = {e['id'] for e in (previous or [])}
    out, names, seen = [], set(), set()
    for position, raw in enumerate(entities, start=1):
        if not isinstance(raw, dict):
            raise ValidationError('entities', 'Entity {} is not an entity.'.format(position))
        label = ' '.join(str(raw.get('name') or '').split())
        if not label:
            raise ValidationError('entities', 'Entity {} needs a name.'.format(position))
        if len(label) > NAME_MAX:
            raise ValidationError('entities', 'Keep {} to {} characters.'.format(label, NAME_MAX))
        if label.lower() in names:
            raise ValidationError('entities', 'Two entities are called {}.'.format(label))
        names.add(label.lower())
        entityId = str(raw.get('id') or '').strip()
        if entityId and entityId not in before:
            raise ValidationError('entities', 'There is no entity {!r} to change.'.format(entityId))
        if not entityId:
            base, n = slug(label), 2
            entityId = base
            while entityId in seen or entityId in before:
                entityId, n = '{}-{}'.format(base, n), n + 1
        if not _ID.match(entityId):
            raise ValidationError('entities', 'An entity id is lower-case letters, digits and hyphens.')
        if entityId in seen:
            raise ValidationError('entities', 'The entity {} appears twice.'.format(label))
        seen.add(entityId)
        wanted = raw.get('excluded') or []
        if not isinstance(wanted, (list, tuple)):
            raise ValidationError('entities', 'The products {} excludes must be a list.'.format(label))
        excludedIds = []
        for pid in wanted:
            pid = str(pid or '').strip()
            if not pid or pid in excludedIds:
                continue
            if not products.has(pid):
                raise ValidationError('entities', '{} excludes {!r}, which is not in the catalogue.'.format(
                    label, pid))
            excludedIds.append(pid)
        note = ' '.join(str(raw.get('note') or '').split())
        if len(note) > NOTE_MAX:
            raise ValidationError('entities', 'Keep the note on {} to {} characters.'.format(
                label, NOTE_MAX))
        out.append({'id': entityId, 'name': label, 'retired': bool(raw.get('retired')),
                    'excluded': sorted(excludedIds), 'note': note})
    missing = before - seen
    if missing:
        gone = [e['name'] for e in previous if e['id'] in missing]
        raise ValidationError('entities', '{} cannot be deleted: rules and proposals may name it. '
                              'Retire it instead.'.format(', '.join(gone)))
    if allRetired(out):
        raise ValidationError('entities', 'Keep at least one entity the first card can offer.')
    return out


def allRetired(entities) -> bool:
    return not any(not e['retired'] for e in entities)


# ------------------------------------------------------------ the record ---

def _write(conn, entities, action, note, user) -> None:
    top = conn.execute('SELECT COALESCE(MAX(revision), 0) FROM policyHistory WHERE kind = ? '
                       'AND scope = ?', (KIND, SCOPE)).fetchone()[0]
    revision = top + 1
    body = json.dumps(entities)
    stamp = _now()
    conn.execute('INSERT OR REPLACE INTO policies (kind, scope, body, revision, updatedBy, '
                 'updatedAt) VALUES (?, ?, ?, ?, ?, ?)',
                 (KIND, SCOPE, body, revision, user or '', stamp))
    conn.execute('INSERT INTO policyHistory (kind, scope, revision, action, body, note, actor, at) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                 (KIND, SCOPE, revision, action, body, note, user or '', stamp))


def _refuseStale(conn, base) -> None:
    if base is None:
        return
    try:
        base = int(base)
    except (TypeError, ValueError):
        raise ValidationError('base', 'The revision the change was made against is a number.')
    row = _row(conn)
    # the four served at revision 0 are the baseline the first save writes
    # as revision 1: the same list, so not a change made in between
    if row is not None and base == 0 and row['revision'] == 1:
        return
    if row is not None and row['revision'] != base:
        raise sleeveRepo.StaleError(
            'base', 'The entities were changed by {} since you opened them. Reload to see the '
            'change.'.format(row['updatedBy'] or 'someone'))


def save(entities, note, user='', base=None) -> dict:
    """Put a new list in force. Refused, naming the field, when the list is
    invalid, the note is missing, someone saved in between (*base*, the
    revision the console showed) or nothing would change."""
    note = _note(note)
    conn = _connect()
    try:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        try:
            _refuseStale(conn, base)
            existing = _row(conn)
            previous = json.loads(existing['body']) if existing is not None else []
            cleaned = normalise(entities, previous)
            if cleaned == previous:
                raise ValidationError('entities', 'That is the list already in force.')
            _write(conn, cleaned, 'updated', note, user)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return _entry(_row(conn))
    finally:
        conn.close()


def _changes(before, after) -> list:
    """What moved between two lists, in the words the console shows."""
    if before is None:
        return []
    out = []
    was = {e['id']: e for e in before}
    catalogue = {}

    def label(pid):
        if pid not in catalogue:
            found = products.get(pid)
            catalogue[pid] = found['name'] if found else pid
        return catalogue[pid]
    for e in after:
        old = was.get(e['id'])
        if old is None:
            out.append('Added {}'.format(e['name']))
            old = {'name': e['name'], 'retired': False, 'excluded': [], 'note': ''}
        if old['name'] != e['name']:
            out.append('{} renamed {}'.format(old['name'], e['name']))
        if old['retired'] != e['retired']:
            out.append('{} {}'.format(e['name'], 'retired' if e['retired'] else 'offered again'))
        added = [p for p in e['excluded'] if p not in old['excluded']]
        dropped = [p for p in old['excluded'] if p not in e['excluded']]
        if added:
            out.append('{} excludes {}'.format(e['name'], ', '.join(label(p) for p in added)))
        if dropped:
            out.append('{} may hold {} again'.format(e['name'], ', '.join(label(p) for p in dropped)))
        if (old.get('note') or '') != (e.get('note') or ''):
            out.append('{}: note {}'.format(e['name'], 'changed' if e.get('note') else 'cleared'))
    return out


def history() -> list:
    """Every revision of the list, newest first, each with what it changed."""
    conn = _connect()
    try:
        rows = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? '
                            'ORDER BY revision', (KIND, SCOPE)).fetchall()
    finally:
        conn.close()
    out, previous = [], None
    top = rows[-1]['revision'] if rows else None
    for r in rows:
        body = json.loads(r['body'])
        out.append({'revision': r['revision'], 'action': r['action'], 'entities': body,
                    'note': r['note'], 'actor': r['actor'], 'at': r['at'],
                    'changes': _changes(previous, body), 'current': r['revision'] == top})
        previous = body
    return list(reversed(out))


def revert(revision, note, user='', base=None) -> dict:
    """Put an earlier revision's list back in force, as a new revision. Every
    entity added since stays, retired: an entity is never deleted."""
    note = _note(note)
    conn = _connect()
    try:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        try:
            _refuseStale(conn, base)
            try:
                number = int(revision)
            except (TypeError, ValueError):
                raise ValidationError('revision', 'Name the revision by its number.')
            row = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? AND '
                               'revision = ?', (KIND, SCOPE, number)).fetchone()
            if row is None:
                raise ValidationError('revision', 'There is no revision {} of the entities.'.format(number))
            previous = json.loads(_row(conn)['body'])
            wanted = json.loads(row['body'])
            have = {e['id'] for e in wanted}
            wanted += [dict(e, retired=True) for e in previous if e['id'] not in have]
            cleaned = normalise(wanted, previous)
            if cleaned == previous:
                raise ValidationError('revision', 'That list is already in force.')
            _write(conn, cleaned, 'reverted', note, user)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return _entry(_row(conn))
    finally:
        conn.close()


def describe() -> dict:
    """Everything the console's Entities tab shows."""
    entry = current()
    return dict(entry, nameMax=NAME_MAX, noteMax=NOTE_MAX, maxEntities=MAX_ENTITIES)
