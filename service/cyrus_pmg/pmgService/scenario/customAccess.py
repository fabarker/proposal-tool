"""Who may enter the custom workflows (D172).

There are two kinds of user: admins and everyone else, the PWAs. The custom
workflows - a custom sleeve on the implementation page (D170) and a custom
portfolio chosen as Custom in the Allocation selector (D171) - are always
open to admins. To PWAs they are open only while an admin has switched them
on, one switch for both, in the Repository's Sleeves tab. It starts off: a
PWA sees neither "Build a custom sleeve…" nor "Custom", and cannot put either
in force. Something an admin built stays on its proposal, shown to a PWA as it
is; changing it is an admin's.

The switch is a policy kind in the repository's ``policies`` table, one
scope, every change a revision in ``policyHistory`` shown in the Activity tab.
"""

from __future__ import annotations

import datetime
import json
import threading

from . import sleeveRepo
from .types import ValidationError

KIND = 'customAccess'
SCOPE = '*'
ACTIONS = ['enabled', 'disabled']

_lock = threading.Lock()
_cache = None        # (the file's stamp, entry)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def _defaults() -> dict:
    return {'pwas': False, 'revision': 0, 'updatedBy': '', 'updatedAt': ''}


def _entryFrom(row) -> dict:
    body = json.loads(row['body'])
    return {'pwas': bool(body.get('pwas')), 'revision': row['revision'],
            'updatedBy': row['updatedBy'], 'updatedAt': row['updatedAt']}


def _read(conn):
    row = conn.execute('SELECT * FROM policies WHERE kind = ? AND scope = ?', (KIND, SCOPE)).fetchone()
    return _entryFrom(row) if row is not None else _defaults()


def settings() -> dict:
    """The switch in force, with who set it last. From memory while the file
    has not moved, through a plain read-only connection."""
    global _cache
    path = sleeveRepo.dbPath()
    stamp = sleeveRepo.fileStamp(path)
    with _lock:
        if stamp is not None and _cache is not None and _cache[0] == stamp:
            return dict(_cache[1])
    entry = None
    conn = sleeveRepo.readOnly(path) if stamp is not None else None
    if conn is not None:
        try:
            entry = _read(conn)
        except Exception:                      # noqa: BLE001 - read again the full way
            entry = None
        finally:
            conn.close()
    if entry is None:
        conn = sleeveRepo.connection()
        try:
            stamp = sleeveRepo.fileStamp(path)
            entry = _read(conn)
        finally:
            conn.close()
    with _lock:
        _cache = (stamp, entry)
    return dict(entry)


def pwasMay() -> bool:
    """Whether PWAs may enter the custom workflows."""
    return settings()['pwas']


def may(admin: bool) -> bool:
    """Whether a caller may enter the custom workflows: an admin always."""
    return bool(admin) or pwasMay()


def setPwas(open_: bool, user='') -> dict:
    """Open the custom workflows to PWAs, or close them. A proposal that
    already holds a custom sleeve or portfolio keeps it when they close."""
    if not isinstance(open_, bool):
        raise ValidationError('open', 'Say whether PWAs may: true or false.')
    conn = sleeveRepo.connection()
    try:
        with conn:
            if _read(conn)['pwas'] == open_:
                raise ValidationError('settings', 'That is the setting already in force.')
            top = conn.execute('SELECT COALESCE(MAX(revision), 0) FROM policyHistory WHERE kind = ? '
                               'AND scope = ?', (KIND, SCOPE)).fetchone()[0]
            text = json.dumps({'pwas': open_})
            stamp = _now()
            note = ('Custom sleeves and custom portfolios opened to PWAs' if open_
                    else 'Custom sleeves and custom portfolios closed to PWAs')
            conn.execute('INSERT OR REPLACE INTO policies (kind, scope, body, revision, updatedBy, '
                         'updatedAt) VALUES (?, ?, ?, ?, ?, ?)',
                         (KIND, SCOPE, text, top + 1, user or '', stamp))
            conn.execute('INSERT INTO policyHistory (kind, scope, revision, action, body, note, actor, '
                         'at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                         (KIND, SCOPE, top + 1, 'enabled' if open_ else 'disabled', text, note,
                          user or '', stamp))
    finally:
        conn.close()
    return settings()


def describe() -> dict:
    """What the console is told."""
    return settings()
