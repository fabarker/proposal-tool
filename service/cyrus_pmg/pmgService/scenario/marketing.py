"""Who may not market what: the offering entities and the warnings a
strategic allocation raises (D196).

Some asset classes may not be shown to clients in some jurisdictions - hedge
funds, most often - and the jurisdiction is the OFFERING entity's: where the
entity showing the proposal to the client is. That is not the custodian the
account is held at, so the offering entities are a list of
their own.

A WARNING names asset classes - whole categories of the strategic allocation
(Hedge Funds), lines within one (Tactical Trading), or both - the offering
entities that may not market them, and the words the PWA is shown. When the
allocation a proposal is built on holds any of them, the page opens a card
with those words; the PWA acknowledges it, and the register records each
warning with who acknowledged it and when. Nothing is blocked: the warning
is the desk's word to the PWA, who knows which entity is offering.

The admin keeps both lists in the console's Marketing tab, each change with
a note and a revision, the way the custodians are kept. An offering entity
keeps the id it was given for life and is retired, never deleted, so a
warning naming it keeps its meaning; a warning may be removed. A warning's
id is never given to another, so an acknowledgement names one warning.

The HEDGE FUND NOTICE (D198) is kept with them: a title, a body and the
words of a tick box, all the admin's, shown on a card the first time a
sleeve holding a hedge fund - a product whose catalogue category is Hedge
Funds - is attached to a proposal. The PWA ticks the box and confirms, or
does not attach the sleeve; either is recorded on the proposal with who and
when, and the register keeps the confirmation, with the words ticked and the
wording's revision, beside a proposal that holds a hedge fund. Until the
admin words it, the desk's own wording is in force.

Storage. The ``policies`` and ``policyHistory`` tables (sleeveRepo, schema 4),
under the kind 'marketing' and the one scope '*'. Until the first save both
lists are empty, at revision 0, and nothing is written: a read never writes.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading

from . import portfolio_weights as pw
from . import sleeveRepo
from .types import ValidationError

KIND = 'marketing'
SCOPE = '*'
NAME_MAX = 60
NOTE_MAX = 240
TEXT_MAX = 1200
MAX_OFFERING = 40
MAX_WARNINGS = 60
TITLE_MAX = 160
BODY_MAX = 3000
ATTEST_MAX = 300

#: the sleeve category whose products the hedge fund notice is about
HEDGE_FUND_CATEGORY = 'Hedge Funds'
#: the desk's wording, in force until the admin words it (D198)
NOTICE = {
    'title': 'Hedge Funds in Non-Discretionary (DNE) accounts: check fund eligibility before '
             'marketing',
    'body': 'Your intended proposed portfolio includes an allocation to Hedge Funds.\n\n'
            'Before you market any hedge fund offering to this client:\n\n'
            '1. You MUST check the fund is registered for sale in the clients country of tax '
            'domicile. To confirm, please contact Alternative Capital Markets for the latest '
            'registration status of the funds.\n'
            '2. If the specific fund(s) are not registered there, you MUST check that country\u2019s '
            'local rules before marketing it.\n\n'
            'If in doubt, contact your local compliance team.\n\n'
            'By continuing on with this implementation, you confirm that you are responsible for '
            'completing these checks before sharing any information with the client.\n\n'
            'Confirm you understand by clicking confirm below.',
    # the words beside the tick box, which must be ticked before Confirm
    'attestation': 'I understand that I am responsible for completing these checks before sharing '
                   'any information with the client.',
}

ACTIONS = ['updated', 'reverted']

_ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')
_WARNING_ID = re.compile(r'^w([1-9][0-9]{0,5})$')


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def slug(name) -> str:
    """'GS Bank Europe' -> 'gs-bank-europe': an offering entity's id, given once."""
    return re.sub(r'[^a-z0-9]+', '-', str(name or '').lower()).strip('-')[:40] or 'entity'


def _empty() -> dict:
    return {'offering': [], 'warnings': [], 'nextWarning': 1, 'hedgeFundNotice': dict(NOTICE)}


# ------------------------------------------------------------ vocabulary ---

def vocabulary() -> list:
    """What a warning may name: every category of the strategic allocations
    and the lines within it, in the allocations' own order:
    [{category, lines: [...]}]."""
    order, lines = [], {}
    for _, line, category in pw.ASSET_METADATA:
        if category not in lines:
            order.append(category)
            lines[category] = []
        if line not in lines[category]:
            lines[category].append(line)
    return [{'category': c, 'lines': lines[c]} for c in order]


# --------------------------------------------------------------- storage ---

def _row(conn):
    return conn.execute('SELECT * FROM policies WHERE kind = ? AND scope = ?',
                        (KIND, SCOPE)).fetchone()


def _parse(text) -> dict:
    body = json.loads(text) if isinstance(text, str) else (text or {})
    notice = body.get('hedgeFundNotice') or {}
    return {'offering': list(body.get('offering') or []),
            'warnings': list(body.get('warnings') or []),
            'nextWarning': int(body.get('nextWarning') or 1),
            # a revision saved before the notice was worded here holds the
            # desk's wording (D198)
            'hedgeFundNotice': {'title': notice.get('title') or NOTICE['title'],
                                'body': notice.get('body') or NOTICE['body'],
                                'attestation': notice.get('attestation') or NOTICE['attestation']}}


def _entry(row) -> dict:
    return dict(_parse(row['body']), revision=row['revision'], updatedBy=row['updatedBy'],
                updatedAt=row['updatedAt'])


_lock = threading.Lock()
_cache = None            # (file stamp, the row as read, or None for none)


def _stored(path):
    """The row in force as plain values, through a plain read-only SQLite
    connection - never through the file itself: a save elsewhere may hold the
    file's write lock, and on POSIX closing any handle on a file drops every
    lock the process holds on it; SQLite's own connections keep them. Kept in
    memory against the file's stamp, which reads the file without dropping
    them, since the schema carries the warnings and is read on every change
    a PWA makes."""
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
    """The lists in force: {revision, offering, warnings, nextWarning,
    updatedBy, updatedAt}, a fresh copy on every call; revision 0 is the two
    empty lists before the first save. A read never writes."""
    path = sleeveRepo.dbPath()
    row = _stored(path) if os.path.exists(path) else None
    found = _entry(row) if row is not None else None
    if found is None:
        found = dict(_empty(), revision=0, updatedBy='', updatedAt='')
    return found



# ------------------------------------------------------------ the warnings ---

def served() -> list:
    """The warnings as a page applies them: [{id, categories, lines, text,
    banned, bannedNames}], *banned* the offering entities' ids and
    *bannedNames* their names, retired ones left out: a warning barring only
    retired entities bars no one, and is left out."""
    body = current()
    names = {e['id']: e['name'] for e in body['offering'] if not e['retired']}
    out = []
    for w in body['warnings']:
        banned = [i for i in w['banned'] if i in names]
        if banned:
            out.append({'id': w['id'], 'categories': list(w['categories']),
                        'lines': list(w['lines']), 'text': w['text'], 'banned': banned,
                        'bannedNames': [names[i] for i in banned]})
    return out


def _groupOf(name) -> list:
    """The categories a custom portfolio's line stands for: a sleeve group's
    members, else itself."""
    from .rules import SLEEVE_GROUPS
    for g in SLEEVE_GROUPS:
        if g['name'] == name:
            return list(g['categories'])
    return [name]


def warningsFor(picture, warnings=None) -> list:
    """The warnings an allocation raises: [{id, text, banned, bannedNames,
    matched: [{name, kind, weightPct}]}], in the warnings' order. *picture* is an
    allocation as the register keeps it - categories, each with its weight
    and its lines (``assets``, by ``reportingName``) - for a strategic
    allocation or a custom portfolio, whose lines may be a sleeve group
    standing for several categories. Only what it holds counts: a weight of
    nothing raises nothing."""
    warnings = served() if warnings is None else warnings
    held, lines = {}, {}
    for c in (picture or {}).get('categories') or []:
        weight = float(c.get('weightPct') or 0.0)
        if weight <= 0:
            continue
        for name in _groupOf(c['name']):
            held[name] = held.get(name, 0.0) + weight
        for a in c.get('assets') or []:
            w = float(a.get('weightPct') or 0.0)
            if w > 0:
                lines[a['reportingName']] = lines.get(a['reportingName'], 0.0) + w
    out = []
    for w in warnings:
        matched = ([{'name': c, 'kind': 'category', 'weightPct': held[c]}
                    for c in w['categories'] if c in held]
                   + [{'name': n, 'kind': 'line', 'weightPct': lines[n]}
                      for n in w['lines'] if n in lines])
        if matched:
            out.append({'id': w['id'], 'text': w['text'], 'banned': list(w['banned']),
                        'bannedNames': list(w.get('bannedNames') or w['banned']),
                        'matched': matched})
    return out


def acknowledged(found, acks, allocation) -> list:
    """*found* (from warningsFor) with each one's acknowledgement, where the
    scenario's acknowledgements are of this *allocation*: who and when, or
    blanks."""
    acks = acks or {}
    mine = {a['warning']: a for a in acks.get('acks') or []} if acks.get('allocation') == allocation else {}
    return [dict(w, acknowledgedBy=(mine.get(w['id']) or {}).get('by', ''),
                 acknowledgedAt=(mine.get(w['id']) or {}).get('at', '')) for w in found]


def acknowledge(previous, allocation, warningIds, user) -> dict:
    """The scenario's acknowledgements after *user* acknowledges
    *warningIds* for *allocation*: added to the ones already made for it, or
    in place of those made for another allocation. Refused, naming
    ``marketingAck``, for an id that is no warning."""
    if not isinstance(allocation, str) or not allocation.strip():
        raise ValidationError('marketingAck', 'Say which allocation the warnings were shown for.')
    if not isinstance(warningIds, (list, tuple)) or not warningIds:
        raise ValidationError('marketingAck', 'Name the warnings acknowledged.')
    known = {w['id'] for w in current()['warnings']}
    unknown = [str(i) for i in warningIds if i not in known]
    if unknown:
        raise ValidationError('marketingAck', 'There is no warning {}.'.format(', '.join(unknown)))
    previous = previous or {}
    acks = list(previous.get('acks') or []) if previous.get('allocation') == allocation else []
    have = {a['warning'] for a in acks}
    stamp = _now()
    for warningId in warningIds:
        if warningId not in have:
            acks.append({'warning': warningId, 'by': user or '', 'at': stamp})
            have.add(warningId)
    return {'allocation': allocation, 'acks': acks}


# ------------------------------------------------- the hedge fund notice ---

def notice() -> dict:
    """The hedge fund notice in force (D198): {title, body, attestation,
    revision}, the revision the marketing lists' own."""
    body = current()
    return dict(body['hedgeFundNotice'], revision=body['revision'])


NOTICE_ACTIONS = ('confirm', 'decline')


def respond(previous, action, category, sleeve, user, attested=False) -> dict:
    """The proposal's record of the hedge fund notice after *user* ticks
    its box and confirms it, or declines to attach the sleeve it was shown
    for (D198): {confirmed: {by, at, revision, title, attestation, category,
    sleeve} or None, declined: [{by, at, category, sleeve}]}. A confirmation
    needs the box ticked (*attested*) and is once a proposal: one already
    made is kept, and a decline after it is refused. Refused, naming
    ``hedgeFundNotice``, for an action that is neither."""
    if action not in NOTICE_ACTIONS:
        raise ValidationError('hedgeFundNotice', 'Confirm the hedge fund notice, or decline it.')
    if action == 'confirm' and attested is not True:
        raise ValidationError('hedgeFundNotice', 'Tick the box to confirm the hedge fund notice.')
    previous = previous or {}
    confirmed = previous.get('confirmed') or None
    declined = list(previous.get('declined') or [])
    words = ' '.join(str(sleeve or '').split())[:160]
    where = ' '.join(str(category or '').split())[:80]
    stamp = _now()
    if action == 'confirm':
        if confirmed is None:
            held = notice()
            confirmed = {'by': user or '', 'at': stamp, 'revision': held['revision'],
                         'title': held['title'], 'attestation': held['attestation'],
                         'category': where, 'sleeve': words}
    else:
        if confirmed is not None:
            raise ValidationError('hedgeFundNotice', 'The hedge fund notice is already confirmed '
                                                     'for this proposal.')
        declined.append({'by': user or '', 'at': stamp, 'category': where, 'sleeve': words})
    return {'confirmed': confirmed, 'declined': declined}


def confirmed(record) -> bool:
    return bool((record or {}).get('confirmed'))


# ------------------------------------------------------------ validation ---

def _note(note) -> str:
    if note is not None and not isinstance(note, str):
        raise ValidationError('note', 'Say why, in words: every change to the marketing lists carries a note.')
    note = ' '.join((note or '').split())
    if not note:
        raise ValidationError('note', 'Say why: every change to the marketing lists carries a note.')
    if len(note) > NOTE_MAX:
        raise ValidationError('note', 'Keep the note to {} characters.'.format(NOTE_MAX))
    return note


def _offering(entities, previous) -> list:
    if not isinstance(entities, (list, tuple)):
        raise ValidationError('offering', 'The offering entities must be a list.')
    if len(entities) > MAX_OFFERING:
        raise ValidationError('offering', 'Keep to {} offering entities.'.format(MAX_OFFERING))
    before = {e['id'] for e in previous}
    out, names, seen = [], set(), set()
    for position, raw in enumerate(entities, start=1):
        if not isinstance(raw, dict):
            raise ValidationError('offering', 'Offering entity {} is not an entity.'.format(position))
        label = ' '.join(str(raw.get('name') or '').split())
        if not label:
            raise ValidationError('offering', 'Offering entity {} needs a name.'.format(position))
        if len(label) > NAME_MAX:
            raise ValidationError('offering', 'Keep {} to {} characters.'.format(label, NAME_MAX))
        if label.lower() in names:
            raise ValidationError('offering', 'Two offering entities are called {}.'.format(label))
        names.add(label.lower())
        entityId = str(raw.get('id') or '').strip()
        if entityId and entityId not in before:
            raise ValidationError('offering', 'There is no offering entity {!r} to change.'.format(entityId))
        if not entityId:
            base, n = slug(label), 2
            entityId = base
            while entityId in seen or entityId in before:
                entityId, n = '{}-{}'.format(base, n), n + 1
        if not _ID.match(entityId):
            raise ValidationError('offering', 'An offering entity id is lower-case letters, digits '
                                              'and hyphens.')
        if entityId in seen:
            raise ValidationError('offering', 'The offering entity {} appears twice.'.format(label))
        seen.add(entityId)
        note = ' '.join(str(raw.get('note') or '').split())
        if len(note) > NOTE_MAX:
            raise ValidationError('offering', 'Keep the note on {} to {} characters.'.format(
                label, NOTE_MAX))
        out.append({'id': entityId, 'name': label, 'retired': bool(raw.get('retired')),
                    'note': note})
    missing = before - seen
    if missing:
        gone = [e['name'] for e in previous if e['id'] in missing]
        raise ValidationError('offering', '{} cannot be deleted: warnings and proposals may name '
                                          'it. Retire it instead.'.format(', '.join(gone)))
    return out


def _warnings(warnings, offering, previous, nextWarning):
    """The warnings checked and in canonical shape, and the next id to give."""
    if not isinstance(warnings, (list, tuple)):
        raise ValidationError('warnings', 'The warnings must be a list.')
    if len(warnings) > MAX_WARNINGS:
        raise ValidationError('warnings', 'Keep to {} warnings.'.format(MAX_WARNINGS))
    vocab = vocabulary()
    categories = [v['category'] for v in vocab]
    lines = [n for v in vocab for n in v['lines']]
    entityIds = {e['id'] for e in offering}
    before = {w['id'] for w in previous}
    out, seen = [], set()
    for position, raw in enumerate(warnings, start=1):
        if not isinstance(raw, dict):
            raise ValidationError('warnings', 'Warning {} is not a warning.'.format(position))
        warningId = str(raw.get('id') or '').strip()
        if warningId and warningId not in before:
            raise ValidationError('warnings', 'There is no warning {!r} to change.'.format(warningId))
        if not warningId:
            warningId = 'w{}'.format(nextWarning)
            nextWarning += 1
        if warningId in seen:
            raise ValidationError('warnings', 'Warning {} appears twice.'.format(position))
        seen.add(warningId)
        named = []
        for field, known in (('categories', categories), ('lines', lines)):
            given = raw.get(field) or []
            if not isinstance(given, (list, tuple)):
                raise ValidationError('warnings', 'Warning {}: the {} must be a list.'.format(position, field))
            bad = [str(v) for v in given if v not in known]
            if bad:
                raise ValidationError(
                    'warnings', 'Warning {}: {} {} not {} of the strategic allocations.'.format(
                        position, ', '.join(repr(b) for b in bad), 'is' if len(bad) == 1 else 'are',
                        'a category' if field == 'categories' else 'a line'))
            named.append([v for v in known if v in set(given)])
        if not named[0] and not named[1]:
            raise ValidationError('warnings', 'Warning {} names no asset class: choose a category '
                                              'or a line it is about.'.format(position))
        banned = raw.get('banned') or []
        if not isinstance(banned, (list, tuple)):
            raise ValidationError('warnings', 'Warning {}: the offering entities must be a list.'.format(position))
        bad = [str(b) for b in banned if b not in entityIds]
        if bad:
            raise ValidationError('warnings', 'Warning {}: there is no offering entity {}.'.format(
                position, ', '.join(repr(b) for b in bad)))
        if not banned:
            raise ValidationError('warnings', 'Warning {} bars no one: tick the offering entities '
                                              'that may not market it.'.format(position))
        text = str(raw.get('text') or '').strip()
        text = '\n'.join(' '.join(line.split()) for line in text.splitlines()).strip()
        if not text:
            raise ValidationError('warnings', 'Warning {} needs the words the PWA is shown.'.format(position))
        if len(text) > TEXT_MAX:
            raise ValidationError('warnings', 'Keep warning {} to {} characters.'.format(position, TEXT_MAX))
        out.append({'id': warningId, 'categories': named[0], 'lines': named[1],
                    'banned': [e['id'] for e in offering if e['id'] in set(banned)], 'text': text})
    return out, nextWarning


def normalise(body, previous) -> dict:
    """The canonical lists, or a ValidationError naming the field."""
    if not isinstance(body, dict):
        raise ValidationError('marketing', 'Send the offering entities and the warnings.')
    offering = _offering(body.get('offering') or [], previous['offering'])
    warnings, nextWarning = _warnings(body.get('warnings') or [], offering, previous['warnings'],
                                      previous['nextWarning'])
    for w in previous['warnings']:
        number = _WARNING_ID.match(w['id'])
        if number:
            nextWarning = max(nextWarning, int(number.group(1)) + 1)
    given = body.get('hedgeFundNotice')
    return {'offering': offering, 'warnings': warnings, 'nextWarning': nextWarning,
            'hedgeFundNotice': _notice(given) if given is not None
            else dict(previous.get('hedgeFundNotice') or NOTICE)}


def _notice(raw) -> dict:
    """The hedge fund notice checked (D198): a title, a body and the tick
    box's words, all needed. The body keeps its line breaks - a blank line
    parts paragraphs, and lines numbered 1. 2. are a list - and loses the
    spaces around them. A notice saved before the tick box keeps the desk's
    words for it."""
    if not isinstance(raw, dict):
        raise ValidationError('hedgeFundNotice', 'The hedge fund notice is a title, a body and the '
                                                 'words of its tick box.')
    title = ' '.join(str(raw.get('title') or '').split())
    if not title:
        raise ValidationError('hedgeFundNotice', 'Give the hedge fund notice a title.')
    if len(title) > TITLE_MAX:
        raise ValidationError('hedgeFundNotice', 'Keep the notice\'s title to {} characters.'.format(
            TITLE_MAX))
    lines = [' '.join(line.split()) for line in str(raw.get('body') or '').replace('\r', '').split('\n')]
    body = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
    if not body:
        raise ValidationError('hedgeFundNotice', 'Give the hedge fund notice the words the PWA confirms.')
    if len(body) > BODY_MAX:
        raise ValidationError('hedgeFundNotice', 'Keep the notice to {} characters.'.format(BODY_MAX))
    given = raw.get('attestation')
    attestation = (' '.join(str(given).split()) if given is not None else NOTICE['attestation'])
    if not attestation:
        raise ValidationError('hedgeFundNotice', 'Give the tick box its words: what the PWA confirms '
                                                 'by ticking it.')
    if len(attestation) > ATTEST_MAX:
        raise ValidationError('hedgeFundNotice', 'Keep the tick box\'s words to {} characters.'.format(
            ATTEST_MAX))
    return {'title': title, 'body': body, 'attestation': attestation}


# ------------------------------------------------------------ the record ---

def _connect():
    return sleeveRepo.connection()


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
            'base', 'The marketing lists were changed by {} since you opened them. Reload to see '
            'the change.'.format((row['updatedBy'] if row is not None else '') or 'someone'))


def _same(a, b) -> bool:
    return (a['offering'] == b['offering'] and a['warnings'] == b['warnings']
            and a.get('hedgeFundNotice') == b.get('hedgeFundNotice'))


def save(body, note, user='', base=None) -> dict:
    """Put new lists in force: {offering: [{id?, name, retired, note}],
    warnings: [{id?, categories, lines, banned, text}], hedgeFundNotice?:
    {title, body}}. An entry without an id is new; a notice not sent is kept. Refused, naming the field, when either list is invalid, the
    note is missing, someone saved in between (*base*) or nothing would
    change."""
    note = _note(note)
    conn = _connect()
    try:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        try:
            _refuseStale(conn, base)
            existing = _row(conn)
            previous = _parse(existing['body']) if existing is not None else _empty()
            cleaned = normalise(body, previous)
            if _same(cleaned, previous):
                raise ValidationError('marketing', 'Those are the lists already in force.')
            _write(conn, cleaned, 'updated', note, user)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return _entry(_row(conn))
    finally:
        conn.close()


def _about(w) -> str:
    return ', '.join(w['categories'] + w['lines'])


def _changes(before, after) -> list:
    """What moved between two revisions, in the words the console shows."""
    before = before or _empty()
    out = []
    was = {e['id']: e for e in before['offering']}
    names = {e['id']: e['name'] for e in before['offering']}
    names.update({e['id']: e['name'] for e in after['offering']})
    for e in after['offering']:
        old = was.get(e['id'])
        if old is None:
            out.append('Added offering entity {}'.format(e['name']))
            continue
        if old['name'] != e['name']:
            out.append('{} renamed {}'.format(old['name'], e['name']))
        if old['retired'] != e['retired']:
            out.append('{} {}'.format(e['name'], 'retired' if e['retired'] else 'back in use'))
        if (old.get('note') or '') != (e.get('note') or ''):
            out.append('{}: note {}'.format(e['name'], 'changed' if e.get('note') else 'cleared'))
    had = {w['id']: w for w in before['warnings']}
    kept = set()
    for w in after['warnings']:
        old = had.get(w['id'])
        kept.add(w['id'])
        bars = ', '.join(names.get(i, i) for i in w['banned'])
        if old is None:
            out.append('Warning on {} added: not by {}'.format(_about(w), bars))
            continue
        if (old['categories'], old['lines']) != (w['categories'], w['lines']):
            out.append('Warning on {} now on {}'.format(_about(old), _about(w)))
        if old['banned'] != w['banned']:
            out.append('Warning on {}: not by {}'.format(_about(w), bars))
        if old['text'] != w['text']:
            out.append('Warning on {}: words changed'.format(_about(w)))
    for w in before['warnings']:
        if w['id'] not in kept:
            out.append('Warning on {} removed'.format(_about(w)))
    was, now = before.get('hedgeFundNotice') or NOTICE, after.get('hedgeFundNotice') or NOTICE
    if was['title'] != now['title']:
        out.append('Hedge fund notice: title changed')
    if was['body'] != now['body']:
        out.append('Hedge fund notice: words changed')
    if was.get('attestation', NOTICE['attestation']) != now.get('attestation', NOTICE['attestation']):
        out.append('Hedge fund notice: tick box words changed')
    return out


def history() -> list:
    """Every revision, newest first, each with what it changed."""
    conn = _connect()
    try:
        rows = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? '
                            'ORDER BY revision', (KIND, SCOPE)).fetchall()
    finally:
        conn.close()
    out, previous = [], None
    top = rows[-1]['revision'] if rows else None
    for r in rows:
        body = _parse(r['body'])
        out.append(dict(body, revision=r['revision'], action=r['action'], note=r['note'],
                        actor=r['actor'], at=r['at'], changes=_changes(previous, body),
                        current=r['revision'] == top))
        previous = body
    return list(reversed(out))


def revert(revision, note, user='', base=None) -> dict:
    """Put an earlier revision's lists back in force, as a new revision.
    Every offering entity added since stays, retired; a warning's id is
    still never given again."""
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
                raise ValidationError('revision', 'There is no revision {} of the marketing '
                                                  'lists.'.format(number))
            existing = _row(conn)
            previous = _parse(existing['body']) if existing is not None else _empty()
            wanted = _parse(row['body'])
            have = {e['id'] for e in wanted['offering']}
            wanted['offering'] += [dict(e, retired=True) for e in previous['offering']
                                   if e['id'] not in have]
            cleaned = normalise(wanted, dict(previous, warnings=previous['warnings']
                                             + [w for w in wanted['warnings']
                                                if w['id'] not in {x['id'] for x in previous['warnings']}]))
            if _same(cleaned, previous):
                raise ValidationError('revision', 'Those lists are already in force.')
            _write(conn, cleaned, 'reverted', note, user)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return _entry(_row(conn))
    finally:
        conn.close()


def describe() -> dict:
    """Everything the console's Marketing tab shows."""
    return dict(current(), vocabulary=vocabulary(), nameMax=NAME_MAX, noteMax=NOTE_MAX,
                textMax=TEXT_MAX, maxOffering=MAX_OFFERING, maxWarnings=MAX_WARNINGS,
                titleMax=TITLE_MAX, bodyMax=BODY_MAX, attestationMax=ATTEST_MAX,
                noticeDefault=dict(NOTICE))
