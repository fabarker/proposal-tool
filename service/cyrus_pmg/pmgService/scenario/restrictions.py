"""The investment restrictions an account opening request may name (D203).

A RESTRICTION is a name, with a description where the desk gives one, that a
PWA may pick on the account opening request - any number of them, or none.
The admin keeps the list in the console's Account Opening tab: adds, renames,
describes and reorders them, and retires one that is no longer offered. A
retired restriction leaves the form but never the list, so a request that
chose it still says what it chose, and it may be put back. Nothing is
removed, and a restriction's id is never given to another.

Every change is a revision of the whole list, with who, when and an optional
note, and an earlier revision can be put back.

A request records each restriction it chose as it read at Submit - its id,
name and description - so a later rename does not rewrite what was asked.

Storage. The ``policies`` and ``policyHistory`` tables (sleeveRepo, schema 4),
under the kind 'restrictions' and the one scope '*'. Until the first save
the list is the five the desk started with, at revision 0 - and nothing is
written: a read never writes.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading

from . import sleeveRepo
from .types import ValidationError

KIND = 'restrictions'
SCOPE = '*'
NAME_MAX = 120
DESCRIPTION_MAX = 400
NOTE_MAX = 240
MAX_RESTRICTIONS = 100

#: the list before the first save, in the desk's order
SEED = ['Capital/Income Split', 'No UK Situs Asset', 'No UK Listed Asset', 'No French Situs Assets',
        'No UK Reporting Funds']

_ID = re.compile(r'^r([1-9][0-9]{0,5})$')


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def _seeded() -> dict:
    return {'restrictions': [{'id': 'r{}'.format(n), 'name': name, 'description': '', 'active': True}
                             for n, name in enumerate(SEED, start=1)],
            'nextId': len(SEED) + 1}


# --------------------------------------------------------------- storage ---

def _row(conn):
    return conn.execute('SELECT * FROM policies WHERE kind = ? AND scope = ?',
                        (KIND, SCOPE)).fetchone()


def _parse(text) -> dict:
    body = json.loads(text) if isinstance(text, str) else (text or {})
    return {'restrictions': [dict(r) for r in body.get('restrictions') or []],
            'nextId': int(body.get('nextId') or 1)}


_lock = threading.Lock()
_cache = None            # (file stamp, the row as read, or None for none)


def _stored(path):
    """The row in force as plain values, kept against the file's stamp: the
    account card reads the list on every lookup."""
    global _cache
    stamp = sleeveRepo.fileStamp(path)
    if stamp is None:
        return None
    with _lock:
        if _cache is not None and _cache[0] == stamp:
            return _cache[1]
    found = None
    conn = sleeveRepo.readOnly(path)
    if conn is not None:
        try:
            row = _row(conn)
            found = ({k: row[k] for k in ('body', 'revision', 'updatedBy', 'updatedAt')}
                     if row is not None else None)
        except Exception:                      # noqa: BLE001 - an older file: nothing yet
            found = None
        finally:
            conn.close()
    with _lock:
        _cache = (stamp, found)
    return found


def current() -> dict:
    """The list in force: {revision, restrictions, nextId, updatedBy,
    updatedAt}, a fresh copy on every call. Revision 0 is the five the desk
    started with, before the first save. A read never writes."""
    path = sleeveRepo.dbPath()
    row = _stored(path) if os.path.exists(path) else None
    if row is not None:
        return dict(_parse(row['body']), revision=row['revision'], updatedBy=row['updatedBy'],
                    updatedAt=row['updatedAt'])
    return dict(_seeded(), revision=0, updatedBy='', updatedAt='')


def served() -> list:
    """The restrictions on offer, in the list's order, as the form shows
    them."""
    return [{'id': r['id'], 'name': r['name'], 'description': r['description']}
            for r in current()['restrictions'] if r['active']]


def chosen(ids) -> list:
    """The restrictions a request names, as they read now and in the list's
    order, or a ValidationError naming ``restrictions``. None is none."""
    if ids is None:
        return []
    if not isinstance(ids, (list, tuple)):
        raise ValidationError('restrictions', 'Send the restrictions as a list.')
    offered = {r['id']: r for r in served()}
    wanted = []
    for raw in ids:
        rid = str(raw or '').strip()
        if rid not in offered:
            raise ValidationError('restrictions', 'That restriction is not on offer: {}. '
                                  'Look the proposal up again to see the list.'.format(rid or 'blank'))
        if rid in wanted:
            raise ValidationError('restrictions', 'A restriction is chosen twice.')
        wanted.append(rid)
    return [dict(r) for r in served() if r['id'] in wanted]


# ------------------------------------------------------------ the list ---

def _text(value) -> str:
    return ' '.join(str(value or '').split())


def normalise(body, previous) -> dict:
    """The canonical list, or a ValidationError naming ``restrictions``.
    Every restriction the list held stays in it: one no longer offered is
    retired, not taken out."""
    if not isinstance(body, dict) or not isinstance(body.get('restrictions'), (list, tuple)):
        raise ValidationError('restrictions', 'Send the restrictions as a list.')
    given = body['restrictions']
    if len(given) > MAX_RESTRICTIONS:
        raise ValidationError('restrictions', 'Keep to {} restrictions.'.format(MAX_RESTRICTIONS))
    had = {r['id']: r for r in previous['restrictions']}
    nextId = previous['nextId']
    for rid in had:
        m = _ID.match(rid)
        if m:
            nextId = max(nextId, int(m.group(1)) + 1)
    out, seen, names = [], set(), set()
    for position, raw in enumerate(given, start=1):
        if not isinstance(raw, dict):
            raise ValidationError('restrictions', 'Restriction {} is not a restriction.'.format(position))
        rid = str(raw.get('id') or '').strip()
        if rid and rid not in had:
            raise ValidationError('restrictions', 'There is no restriction {!r} to change.'.format(rid))
        if not rid:
            rid = 'r{}'.format(nextId)
            nextId += 1
        if rid in seen:
            raise ValidationError('restrictions', 'Restriction {} appears twice.'.format(position))
        seen.add(rid)
        name = _text(raw.get('name'))
        if not name:
            raise ValidationError('restrictions', 'Restriction {} needs a name.'.format(position))
        if len(name) > NAME_MAX:
            raise ValidationError('restrictions', 'Keep the name of restriction {} to {} characters.'.format(
                position, NAME_MAX))
        if name.lower() in names:
            raise ValidationError('restrictions', 'Two restrictions are called {}.'.format(name))
        names.add(name.lower())
        description = _text(raw.get('description'))
        if len(description) > DESCRIPTION_MAX:
            raise ValidationError('restrictions', 'Keep the description of {} to {} characters.'.format(
                name, DESCRIPTION_MAX))
        out.append({'id': rid, 'name': name, 'description': description,
                    'active': raw.get('active', True) is not False})
    missing = [had[rid]['name'] for rid in had if rid not in seen]
    if missing:
        raise ValidationError('restrictions', 'Retire {} rather than taking it out: a request may name '
                              'it.'.format(', '.join(missing)))
    return {'restrictions': out, 'nextId': nextId}


# ------------------------------------------------------------ the record ---

def _note(note) -> str:
    if note is not None and not isinstance(note, str):
        raise ValidationError('note', 'A note is words.')
    note = ' '.join((note or '').split())
    if len(note) > NOTE_MAX:
        raise ValidationError('note', 'Keep the note to {} characters.'.format(NOTE_MAX))
    return note


def _write(conn, body, action, note, user) -> None:
    top = conn.execute('SELECT COALESCE(MAX(revision), 0) FROM policyHistory WHERE kind = ? '
                       'AND scope = ?', (KIND, SCOPE)).fetchone()[0]
    revision = top + 1
    text = json.dumps(body)
    stamp = _now()
    conn.execute('INSERT OR REPLACE INTO policies (kind, scope, body, revision, updatedBy, '
                 'updatedAt) VALUES (?, ?, ?, ?, ?, ?)',
                 (KIND, SCOPE, text, revision, user or '', stamp))
    conn.execute('INSERT INTO policyHistory (kind, scope, revision, action, body, note, actor, at) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                 (KIND, SCOPE, revision, action, text, note, user or '', stamp))


def _refuseStale(conn, base) -> None:
    if base is None:
        return
    try:
        base = int(base)
    except (TypeError, ValueError):
        raise ValidationError('base', 'The revision the change was made against is a number.')
    row = _row(conn)
    now = row['revision'] if row is not None else 0
    if now != base:
        raise sleeveRepo.StaleError(
            'base', 'The restrictions were changed by {} since you opened them. Reload to see the '
            'change.'.format((row['updatedBy'] if row is not None else '') or 'someone'))


def _previous(conn) -> dict:
    existing = _row(conn)
    return _parse(existing['body']) if existing is not None else _seeded()


def save(body, note='', user='', base=None) -> dict:
    """Put a new list in force: {restrictions: [{id?, name, description,
    active}]}, in the order the form shows them. One without an id is new.
    Refused, naming the field, when the list is invalid or leaves one out,
    someone saved in between (*base*) or nothing would change."""
    note = _note(note)
    conn = sleeveRepo.connection()
    try:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        try:
            _refuseStale(conn, base)
            previous = _previous(conn)
            cleaned = normalise(body, previous)
            if cleaned['restrictions'] == previous['restrictions']:
                raise ValidationError('restrictions', 'Those are the restrictions already in force.')
            _write(conn, cleaned, 'updated', note, user)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return current()
    finally:
        conn.close()


def _changes(before, after) -> list:
    """What moved between two revisions, in the words the console shows."""
    had = {r['id']: r for r in before['restrictions']}
    out = []
    for r in after['restrictions']:
        old = had.get(r['id'])
        if old is None:
            out.append('Added {}'.format(r['name']) + ('' if r['active'] else ', retired'))
            continue
        if old['name'] != r['name']:
            out.append('{} renamed {}'.format(old['name'], r['name']))
        if old['description'] != r['description']:
            out.append('{}: {}'.format(r['name'], 'description changed' if r['description'] and old['description']
                                       else 'description added' if r['description'] else 'description taken off'))
        if old['active'] != r['active']:
            out.append('{} {}'.format(r['name'], 'put back' if r['active'] else 'retired'))
    kept = [r['id'] for r in after['restrictions'] if r['id'] in had]
    if kept != [r['id'] for r in before['restrictions'] if r['id'] in {x['id'] for x in after['restrictions']}] \
            and len(kept) > 1:
        out.append('Order now: {}'.format(', '.join(r['name'] for r in after['restrictions'])))
    return out


def _rows(conn) -> list:
    return conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? '
                        'ORDER BY revision', (KIND, SCOPE)).fetchall()


def history() -> list:
    """Every revision, newest first, each with what it changed. Before the
    first save there is none: the five the desk started with are revision
    0, and the first save is measured against them."""
    conn = sleeveRepo.connection()
    try:
        rows = _rows(conn)
    finally:
        conn.close()
    out, previous = [], _seeded()
    top = rows[-1]['revision'] if rows else None
    for r in rows:
        body = _parse(r['body'])
        out.append(dict(body, revision=r['revision'], action=r['action'], note=r['note'],
                        actor=r['actor'], at=r['at'], changes=_changes(previous, body),
                        current=r['revision'] == top))
        previous = body
    return list(reversed(out))


def feedRows(conn) -> list:
    """The record as the Activity feed reads it."""
    out, previous = [], _seeded()
    for r in _rows(conn):
        body = _parse(r['body'])
        out.append({'id': r['id'], 'revision': r['revision'], 'action': r['action'], 'note': r['note'],
                    'actor': r['actor'], 'at': r['at'], 'changes': _changes(previous, body)})
        previous = body
    return out


def revert(revision, note='', user='', base=None) -> dict:
    """Put an earlier revision's list back in force, as a new revision. A
    restriction added since that revision is kept, retired: nothing leaves
    the list, and an id is never given to another."""
    note = _note(note)
    conn = sleeveRepo.connection()
    try:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        try:
            _refuseStale(conn, base)
            try:
                number = int(revision)
            except (TypeError, ValueError):
                raise ValidationError('revision', 'Name the revision by its number.')
            if number == 0:
                wanted = _seeded()
            else:
                row = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? AND '
                                   'revision = ?', (KIND, SCOPE, number)).fetchone()
                if row is None:
                    raise ValidationError('revision', 'There is no revision {} of the restrictions.'.format(number))
                wanted = _parse(row['body'])
            previous = _previous(conn)
            back = {r['id'] for r in wanted['restrictions']}
            since = [dict(r, active=False) for r in previous['restrictions'] if r['id'] not in back]
            # a restriction added since may carry a name the earlier list
            # gave another: it is kept, retired, under a name of its own
            taken = {r['name'].lower() for r in wanted['restrictions']}
            for r in since:
                if r['name'].lower() in taken:
                    r['name'] = '{} ({})'.format(r['name'], r['id'])
            cleaned = normalise({'restrictions': wanted['restrictions'] + since}, previous)
            cleaned['nextId'] = max(cleaned['nextId'], previous['nextId'])
            if cleaned['restrictions'] == previous['restrictions']:
                raise ValidationError('revision', 'Those restrictions are already in force.')
            _write(conn, cleaned, 'reverted', note or 'Restored revision {}'.format(number), user)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return current()
    finally:
        conn.close()


def describe() -> dict:
    """Everything the console's Account Opening tab shows of the list."""
    return dict(current(), seed=list(SEED), nameMax=NAME_MAX, descriptionMax=DESCRIPTION_MAX,
                noteMax=NOTE_MAX, maxRestrictions=MAX_RESTRICTIONS)
