"""The notices a PWA must confirm before a sleeve or a product goes into a
proposal (D199).

A NOTICE is the desk's words about something a proposal may hold, and what
it is about: sleeve categories (a sleeve attached under Hedge Funds),
product categories (a product whose own category is Hedge Funds), product
asset classes (a product of the asset class Global Macro) and particular
products. When a sleeve or a product that any of these match is attached to
a proposal - a library sleeve, a custom sleeve, a product added to a custom
portfolio - the page shows the notice on a card, and the PWA confirms it or
does not attach what raised it. A notice may ask for a tick box to be ticked
before Confirm. Each notice is confirmed once a proposal; every confirmation
and every decline is recorded on the proposal as the signed-in user and the
server's time, the download refuses while a notice the proposal raises is
unconfirmed, and the register keeps each notice a delivered proposal raised
with who confirmed it. A notice is Active or Disabled, and may be removed;
a removed one stays in the history, and a notice's id is never given to
another.

The admin keeps them in the console's Notices tab, as tiles. Every change is
a revision of the whole list, with who, when and an optional note, and an
earlier revision can be put back.

Storage. The ``policies`` and ``policyHistory`` tables (sleeveRepo, schema 4),
under the kind 'notices' and the one scope '*'. Until the first save the list
is the one the earlier lists make (D196, D198): the hedge fund notice, and
each marketing warning as a notice about its categories, at revision 0 -
and nothing is written: a read never writes.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading

from . import sleeveRepo
from .types import ValidationError

KIND = 'notices'
SCOPE = '*'
NAME_MAX = 80
TITLE_MAX = 160
BODY_MAX = 3000
TICK_MAX = 300
NOTE_MAX = 240
WORD_MAX = 60
MAX_NOTICES = 60
MAX_ABOUT = 80

#: what a notice may be about, in the order the console offers them
ABOUT = ('sleeveCategories', 'productCategories', 'assetClasses', 'products')
ABOUT_LABELS = {'sleeveCategories': 'Sleeve category', 'productCategories': 'Product category',
                'assetClasses': 'Asset class', 'products': 'Product'}
ACTIONS = ('confirm', 'decline')

_ID = re.compile(r'^n([1-9][0-9]{0,5})$')


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


# --------------------------------------------------------- the first list ---

def _carried() -> dict:
    """The list before the first save: the hedge fund notice (D198) and
    each marketing warning (D196), which now show when a sleeve in their
    categories is attached, the offering entities that may not market them
    named in their words."""
    from . import marketing
    body = marketing.current()
    hf = body['hedgeFundNotice']
    out = [{'id': 'n1', 'name': 'Hedge funds · DNE eligibility', 'active': True,
            'about': {'sleeveCategories': [], 'productCategories': [marketing.HEDGE_FUND_CATEGORY],
                      'assetClasses': [], 'products': []},
            'title': hf['title'], 'body': hf['body'], 'tick': True, 'tickWords': hf['attestation']}]
    names = {e['id']: e['name'] for e in body['offering']}
    parent = {line: v['category'] for v in marketing.vocabulary() for line in v['lines']}
    from .rules import sleeveCategory
    for n, w in enumerate(body['warnings'], start=2):
        cats = []
        for c in list(w['categories']) + [parent[x] for x in w['lines'] if x in parent]:
            c = sleeveCategory(c)
            if c not in cats:
                cats.append(c)
        about = ', '.join(w['categories'] + w['lines'])
        barred = ', '.join(names.get(i, i) for i in w['banned'])
        out.append({'id': 'n{}'.format(n), 'name': 'Marketing · ' + about, 'active': True,
                    'about': {'sleeveCategories': cats, 'productCategories': [], 'assetClasses': [],
                              'products': []},
                    'title': 'Marketing restriction: ' + about,
                    'body': w['text'] + ('\n\nMay not be marketed by: {}.'.format(barred) if barred else ''),
                    'tick': False, 'tickWords': ''})
    return {'notices': out, 'nextId': len(out) + 1}


# --------------------------------------------------------------- storage ---

def _row(conn):
    return conn.execute('SELECT * FROM policies WHERE kind = ? AND scope = ?',
                        (KIND, SCOPE)).fetchone()


def _parse(text) -> dict:
    body = json.loads(text) if isinstance(text, str) else (text or {})
    return {'notices': [dict(n) for n in body.get('notices') or []],
            'nextId': int(body.get('nextId') or 1)}


_lock = threading.Lock()
_cache = None            # (file stamp, the row as read, or None for none)


def _stored(path):
    """The row in force as plain values, read through a plain read-only
    connection and kept against the file's stamp, as the overlay rules are:
    the schema carries the notices and is read on every change a PWA
    makes."""
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
    """The list in force: {revision, notices, nextId, updatedBy, updatedAt},
    a fresh copy on every call. Revision 0 is the list the earlier lists
    make, before the first save. A read never writes."""
    path = sleeveRepo.dbPath()
    row = _stored(path) if os.path.exists(path) else None
    if row is not None:
        return dict(_parse(row['body']), revision=row['revision'], updatedBy=row['updatedBy'],
                    updatedAt=row['updatedAt'])
    return dict(_carried(), revision=0, updatedBy='', updatedAt='')


def get(noticeId):
    """One notice in the list in force, or None."""
    return next((dict(n) for n in current()['notices'] if n['id'] == noticeId), None)


def served() -> list:
    """The Active notices as a page applies them, each with the list's
    revision."""
    body = current()
    return [dict(n, revision=body['revision']) for n in body['notices'] if n['active']]


# ------------------------------------------------------------ the matching ---

def _hits(notice, sleeveCategory, products) -> bool:
    about = notice['about']
    if sleeveCategory and sleeveCategory in about['sleeveCategories']:
        return True
    for p in products or []:
        for x in (p, (p or {}).get('alternative')):
            if not isinstance(x, dict):
                continue
            if x.get('productId') in about['products']:
                return True
            if x.get('ownCategory') and x['ownCategory'] in about['productCategories']:
                return True
            if x.get('assetClass') and x['assetClass'] in about['assetClasses']:
                return True
    return False


def raisedBy(sleeveCategory, products, notices=None) -> list:
    """The Active notices a sleeve attached under *sleeveCategory* holding
    *products* (as the library serves them, each with its ownCategory and
    the one it names below a minimum) raises, in the list's order."""
    notices = served() if notices is None else notices
    return [n for n in notices if n.get('active', True) and _hits(n, sleeveCategory, products)]


def raisedByModel(model, notices=None) -> list:
    """The notices an implemented model raises: [{id, name, title, holds}],
    *holds* the sleeves or products that raised each, by name."""
    from .rules import sleeveCategory
    notices = served() if notices is None else notices
    out = {}
    for group in (model or {}).get('groups') or []:
        under = sleeveCategory(group.get('category') or '')
        items = group.get('items') or []
        for n in raisedBy(under, items, notices):
            entry = out.setdefault(n['id'], {'id': n['id'], 'name': n['name'], 'title': n['title'],
                                             'holds': []})
            what = group.get('sleeve') or under
            if what and what not in entry['holds']:
                entry['holds'].append(what)
    return [out[n['id']] for n in notices if n['id'] in out]


def raisedByProducts(products, notices=None) -> list:
    """The notices a custom portfolio's products raise, each product read
    under the line it is placed in: [{id, name, title, holds}]."""
    notices = served() if notices is None else notices
    out = {}
    for p in products or []:
        for n in raisedBy(p.get('category') or '', [p], notices):
            entry = out.setdefault(n['id'], {'id': n['id'], 'name': n['name'], 'title': n['title'],
                                             'holds': []})
            name = p.get('name') or p.get('productId')
            if name not in entry['holds']:
                entry['holds'].append(name)
    return [out[n['id']] for n in notices if n['id'] in out]


# ------------------------------------------------------------- the record ---

def recordOf(state) -> dict:
    """A scenario's record of its notices: {noticeId: {confirmed, declined}}.
    One saved before the notices (D198) holds the hedge fund notice's on
    its own, which is notice n1's."""
    record = dict((state or {}).get('notices') or {})
    held = (state or {}).get('hedgeFundNotice')
    if held and 'n1' not in record:
        record['n1'] = {'confirmed': held.get('confirmed'), 'declined': list(held.get('declined') or [])}
    return record


def confirmedIds(state) -> set:
    return {i for i, r in recordOf(state).items() if (r or {}).get('confirmed')}


def respond(state, noticeId, action, user, ticked=False, category='', sleeve='') -> dict:
    """The scenario's record after *user* confirms *noticeId*, or declines
    to attach what it was shown for. A confirmation of a notice that asks
    for its tick box needs *ticked*, and is once a proposal: one already
    made is kept, and a decline after it is refused. Refused, naming
    ``notice``, for a notice there is not or an action that is neither."""
    if action not in ACTIONS:
        raise ValidationError('notice', 'Confirm the notice, or decline it.')
    notice = get(noticeId) if isinstance(noticeId, str) else None
    if notice is None:
        raise ValidationError('notice', 'There is no notice {!r}.'.format(noticeId))
    record = recordOf(state)
    mine = dict(record.get(noticeId) or {'confirmed': None, 'declined': []})
    mine['declined'] = list(mine.get('declined') or [])
    words = ' '.join(str(sleeve or '').split())[:160]
    where = ' '.join(str(category or '').split())[:80]
    stamp = _now()
    if action == 'confirm':
        if notice['tick'] and ticked is not True:
            raise ValidationError('notice', 'Tick the box to confirm the notice.')
        if not mine.get('confirmed'):
            mine['confirmed'] = {'by': user or '', 'at': stamp, 'revision': current()['revision'],
                                 'name': notice['name'], 'title': notice['title'],
                                 'ticked': bool(notice['tick']),
                                 'tickWords': notice['tickWords'] if notice['tick'] else '',
                                 'category': where, 'sleeve': words}
    else:
        if mine.get('confirmed'):
            raise ValidationError('notice', 'The notice is already confirmed for this proposal.')
        mine['declined'].append({'by': user or '', 'at': stamp, 'category': where, 'sleeve': words})
    record[noticeId] = mine
    return record


def unconfirmed(raised, state) -> list:
    done = confirmedIds(state)
    return [r for r in raised if r['id'] not in done]


def delivered(raised, state) -> list:
    """What the register keeps of the notices a delivered proposal raised:
    each with who confirmed it, when, the words ticked and the revision."""
    record = recordOf(state)
    return [dict(r, confirmed=(record.get(r['id']) or {}).get('confirmed')) for r in raised]


# ------------------------------------------------------------ validation ---

def vocabulary() -> dict:
    """What a notice may be about: the library's categories (for sleeves and
    for products, whose own category is one of them), the asset classes and
    the products the catalogue holds."""
    from . import products
    cats = sleeveRepo.categories()
    try:
        live = products.entries()
        classes = sorted({p['assetClass'] for p in live if p['assetClass']}
                         | {o['assetClass'] for p in live for o in (p.get('currencies') or {}).values()
                            if o.get('assetClass')}, key=str.lower)
        listed = [{'id': p['productId'], 'name': p['name'], 'category': p.get('category') or '',
                   'assetClass': p['assetClass']} for p in live]
    except products.BadCatalogue:
        classes, listed = [], []
    return {'sleeveCategories': cats, 'productCategories': cats, 'assetClasses': classes,
            'products': listed}


def _text(value) -> str:
    return ' '.join(str(value or '').split())


def _about(raw, position, vocab, known) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError('notices', 'Notice {}: say what it is about.'.format(position))
    out = {}
    for key in ABOUT:
        given = raw.get(key) or []
        if not isinstance(given, (list, tuple)):
            raise ValidationError('notices', 'Notice {}: the {} must be a list.'.format(
                position, ABOUT_LABELS[key].lower() + 's'))
        if len(given) > MAX_ABOUT:
            raise ValidationError('notices', 'Notice {}: keep to {} of each.'.format(position, MAX_ABOUT))
        values = []
        for v in given:
            v = _text(v) if key != 'products' else str(v or '').strip()
            if not v or v in values:
                continue
            if key in ('sleeveCategories', 'productCategories') and v not in vocab[key]:
                raise ValidationError('notices', 'Notice {}: {!r} is not a category the sleeves are '
                                                 'built for.'.format(position, v))
            if key == 'assetClasses' and len(v) > WORD_MAX:
                raise ValidationError('notices', 'Notice {}: keep an asset class to {} characters.'.format(
                    position, WORD_MAX))
            if key == 'products' and v not in known:
                raise ValidationError('notices', 'Notice {}: there is no product {!r} in the '
                                                 'catalogue.'.format(position, v))
            values.append(v)
        out[key] = values
    if not any(out[k] for k in ABOUT):
        raise ValidationError('notices', 'Notice {} is about nothing: choose a sleeve category, a '
                                         'product category, an asset class or a product.'.format(position))
    return out


def _body(text) -> str:
    lines = [' '.join(line.split()) for line in str(text or '').replace('\r', '').split('\n')]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def normalise(body, previous) -> dict:
    """The canonical list, or a ValidationError naming ``notices``."""
    if not isinstance(body, dict) or not isinstance(body.get('notices'), (list, tuple)):
        raise ValidationError('notices', 'Send the notices as a list.')
    given = body['notices']
    if len(given) > MAX_NOTICES:
        raise ValidationError('notices', 'Keep to {} notices.'.format(MAX_NOTICES))
    vocab = vocabulary()
    from . import products
    try:
        known = {p['productId'] for p in products.entries()} | {
            p['productId'] for p in products.entries(withdrawn=True)}
    except products.BadCatalogue:
        known = set()
    before = {n['id'] for n in previous['notices']}
    nextId = previous['nextId']
    for n in previous['notices']:
        m = _ID.match(n['id'])
        if m:
            nextId = max(nextId, int(m.group(1)) + 1)
    out, seen, names = [], set(), set()
    for position, raw in enumerate(given, start=1):
        if not isinstance(raw, dict):
            raise ValidationError('notices', 'Notice {} is not a notice.'.format(position))
        noticeId = str(raw.get('id') or '').strip()
        if noticeId and noticeId not in before:
            raise ValidationError('notices', 'There is no notice {!r} to change.'.format(noticeId))
        if not noticeId:
            noticeId = 'n{}'.format(nextId)
            nextId += 1
        if noticeId in seen:
            raise ValidationError('notices', 'Notice {} appears twice.'.format(position))
        seen.add(noticeId)
        name = _text(raw.get('name'))
        if not name:
            raise ValidationError('notices', 'Notice {} needs a name.'.format(position))
        if len(name) > NAME_MAX:
            raise ValidationError('notices', 'Keep the name of notice {} to {} characters.'.format(
                position, NAME_MAX))
        if name.lower() in names:
            raise ValidationError('notices', 'Two notices are called {}.'.format(name))
        names.add(name.lower())
        title = _text(raw.get('title'))
        if not title:
            raise ValidationError('notices', '{} needs a title.'.format(name))
        if len(title) > TITLE_MAX:
            raise ValidationError('notices', 'Keep the title of {} to {} characters.'.format(name, TITLE_MAX))
        text = _body(raw.get('body'))
        if not text:
            raise ValidationError('notices', '{} needs the words the PWA confirms.'.format(name))
        if len(text) > BODY_MAX:
            raise ValidationError('notices', 'Keep {} to {} characters.'.format(name, BODY_MAX))
        tick = bool(raw.get('tick'))
        tickWords = _text(raw.get('tickWords'))
        if tick and not tickWords:
            raise ValidationError('notices', '{} asks for its tick box: give the box its words.'.format(name))
        if len(tickWords) > TICK_MAX:
            raise ValidationError('notices', 'Keep the tick box words of {} to {} characters.'.format(
                name, TICK_MAX))
        out.append({'id': noticeId, 'name': name, 'active': raw.get('active', True) is not False,
                    'about': _about(raw.get('about'), position, vocab, known),
                    'title': title, 'body': text, 'tick': tick, 'tickWords': tickWords})
    return {'notices': out, 'nextId': nextId}


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
            'base', 'The notices were changed by {} since you opened them. Reload to see the '
            'change.'.format((row['updatedBy'] if row is not None else '') or 'someone'))


def _previous(conn) -> dict:
    existing = _row(conn)
    return _parse(existing['body']) if existing is not None else _carried()


def save(body, note='', user='', base=None) -> dict:
    """Put a new list in force: {notices: [{id?, name, active, about, title,
    body, tick, tickWords}]}. A notice without an id is new; one the list
    leaves out is removed, and stays in the history. Refused, naming the
    field, when the list is invalid, someone saved in between (*base*) or
    nothing would change."""
    note = _note(note)
    conn = sleeveRepo.connection()
    try:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        try:
            _refuseStale(conn, base)
            previous = _previous(conn)
            cleaned = normalise(body, previous)
            if cleaned['notices'] == previous['notices']:
                raise ValidationError('notices', 'Those are the notices already in force.')
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
    had = {n['id']: n for n in (before or {'notices': []})['notices']}
    out, kept = [], set()
    for n in after['notices']:
        old = had.get(n['id'])
        kept.add(n['id'])
        if old is None:
            out.append('Added {}'.format(n['name']))
            continue
        if old['name'] != n['name']:
            out.append('{} renamed {}'.format(old['name'], n['name']))
        if old['active'] != n['active']:
            out.append('{} {}'.format(n['name'], 'made active' if n['active'] else 'disabled'))
        if old['about'] != n['about']:
            out.append('{}: what it is about changed'.format(n['name']))
        if (old['title'], old['body']) != (n['title'], n['body']):
            out.append('{}: words changed'.format(n['name']))
        if (old['tick'], old['tickWords']) != (n['tick'], n['tickWords']):
            out.append('{}: tick box {}'.format(n['name'], 'changed' if n['tick'] and old['tick']
                                                else 'asked for' if n['tick'] else 'no longer asked for'))
    for n in (before or {'notices': []})['notices']:
        if n['id'] not in kept:
            out.append('Removed {}'.format(n['name']))
    return out


def history() -> list:
    """Every revision, newest first, each with what it changed."""
    conn = sleeveRepo.connection()
    try:
        rows = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? '
                            'ORDER BY revision', (KIND, SCOPE)).fetchall()
    finally:
        conn.close()
    out, previous = [], _carried() if rows else None
    top = rows[-1]['revision'] if rows else None
    for r in rows:
        body = _parse(r['body'])
        out.append(dict(body, revision=r['revision'], action=r['action'], note=r['note'],
                        actor=r['actor'], at=r['at'], changes=_changes(previous, body),
                        current=r['revision'] == top))
        previous = body
    return list(reversed(out))


def revert(revision, note='', user='', base=None) -> dict:
    """Put an earlier revision's list back in force, as a new revision. A
    notice's id is still never given to another."""
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
            row = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? AND '
                               'revision = ?', (KIND, SCOPE, number)).fetchone()
            if row is None:
                raise ValidationError('revision', 'There is no revision {} of the notices.'.format(number))
            previous = _previous(conn)
            wanted = _parse(row['body'])
            # a notice removed since may come back with its own id
            known = dict(previous, notices=previous['notices'] + [
                n for n in wanted['notices'] if n['id'] not in {x['id'] for x in previous['notices']}])
            cleaned = normalise(wanted, known)
            cleaned['nextId'] = max(cleaned['nextId'], previous['nextId'])
            if cleaned['notices'] == previous['notices']:
                raise ValidationError('revision', 'Those notices are already in force.')
            _write(conn, cleaned, 'reverted', note or 'Restored revision {}'.format(number), user)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return current()
    finally:
        conn.close()


def describe() -> dict:
    """Everything the console's Notices tab shows."""
    return dict(current(), vocabulary=vocabulary(), nameMax=NAME_MAX, titleMax=TITLE_MAX,
                bodyMax=BODY_MAX, tickMax=TICK_MAX, noteMax=NOTE_MAX, maxNotices=MAX_NOTICES)
