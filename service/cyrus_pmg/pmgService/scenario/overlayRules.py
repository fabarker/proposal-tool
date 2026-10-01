"""The overlay rules, held in the repository (D155).

An overlay moves weight out of one or more strategic categories into a
category of its own: the Tactical Tilts (D50) and the Strategic Volatility
Premium (D53) were the two, written as code in rules.py. They are now an
ORDERED LIST of rules the desk keeps in the console's Overlay Funding tab,
resolved top to bottom - each rule reading the allocation as the rules above
it left it - with a note, a history and a restore, like the private-markets
funding split (fundingSplit.py, D148) whose pattern this follows. The shape
of a rule and the arithmetic are rules.py's (``resolveOverlays``); this
module keeps the lists and decides what may be saved.

Scopes. The HOUSE list (scope '*') applies to every implementation type. A
type may carry a list of its own, which then applies to it alone; removing it
puts the type back on the house list. None is set on first open.

Seeding. The house list is seeded, once, with ``rules.baselineOverlays()`` -
the tilt, 8% of the portfolio from Investment Grade Fixed Income, then the
premium, 7.5% of Investment Grade Fixed Income as the tilt left it - as a
*baseline* revision, so an upgrade changes no figure. The repository's file
records this as schema revision 6 (sleeveRepo); the seed itself is written
the first time anything opens the file, race-safe the way the library's is.

What may be saved. A list is refused, naming the field, when a rule is
malformed (see ``normalise``), and - the check that matters - when any
portfolio of an implementation type the list applies to HOLDS every source
of a rule but cannot fund it at its step: the source would go below zero.
Every setting of the proposal's switches is judged, since a rule below a
switched rule reads a different allocation with the switch off, and a source
counts as HELD when the strategic portfolio holds it - one an earlier rule
drained to exactly zero is still held, and asking more of it is a shortfall.
The refusal names the rule, the category, the worst portfolio and the switch
setting. A portfolio that does not hold a source at all is not a refusal: the
rule is simply not offered to it, as the tilt never was to an all-equity
portfolio. So at proposal time *unfundable* only ever means *does not hold a
source*.
A rule whose category has no sleeve under a type is saved with a WARNING:
it carries its weight with no product until one is created, which is what
the tool has always done for an automatic category without a sleeve.

Storage. The ``policies`` and ``policyHistory`` tables fundingSplit
introduced (sleeveRepo, schema 4), under the kind 'overlay': the list in
force per scope, and one append-only row per revision. What changed between
two revisions - a rule added, removed or edited field by field, the order,
which revision a restore brought back - is worked out when the history is
read, from the bodies themselves.

Concurrency. Every write takes the file's write lock first (BEGIN IMMEDIATE),
so its reads, its checks and its new revision are one step: two saves cannot
claim one revision, and a house save and a type's return to the house list
cannot each pass a check the other invalidates. A write may name the list it
was made against (``base``: the scope and revision the console showed); when
that is no longer the list in force it is refused as STALE (HTTP 409), and
the console reloads.

A change reaches scenarios in progress at once, as the funding split's does;
a delivered proposal keeps the list it was built with - the register records
it (proposalRegister, schema 5).
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
import threading

from . import rules as R
from . import sleeveRepo, universe
from .types import ValidationError

KIND = 'overlay'
HOUSE = '*'
PLACES = 4
MAX_RULES = 8
MAX_SOURCES = 4
NAME_MAX = 60
_SCALE = 10 ** PLACES
_WHOLE = 100 * _SCALE
_ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')

ACTIONS = ['baseline', 'updated', 'reverted', 'removed']

#: the words a switch is named by in a refusal
_SWITCH_WORDS = {'tacticalTilt': 'Tactical Tilts', 'volPremium': 'Strategic Volatility Premium'}

_lock = threading.Lock()


class StaleError(ValidationError):
    """The list a write was made against is no longer the one in force ->
    HTTP 409 {error, field}."""


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def _ordinal(n: int) -> str:
    suffix = 'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return '{}{}'.format(n, suffix)


def _selections():
    """Every setting of the proposal's switches: a rule below a switched rule
    reads a different allocation with the switch off."""
    out = [{}]
    for toggle in R.OVERLAY_TOGGLES:
        out = [dict(s, **{toggle: on}) for s in out for on in (True, False)]
    return out


# ------------------------------------------------------------ validation ---

def _places(value: float) -> bool:
    return abs(value * _SCALE - round(value * _SCALE)) < 1e-6


def _number(value, what):
    """*value* as a float, or a ValidationError naming *what*. A boolean is
    not a number here, however Python counts it, and neither is NaN."""
    if isinstance(value, bool):
        raise ValidationError('rules', 'Give {} as a number.'.format(what))
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValidationError('rules', 'Give {} as a number.'.format(what))
    if number != number or number in (float('inf'), float('-inf')):
        raise ValidationError('rules', 'Give {} as a number.'.format(what))
    return number


def _text(value, what) -> str:
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValidationError('rules', 'Give {} as text.'.format(what))
    return value.strip()


def _slug(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:36].strip('-')
    return slug or 'rule'


def _canonicalInto(into: str) -> str:
    """A category name in the spelling the library already uses for it, when
    it differs only in case: an overlay's category is a sleeve category, and
    'asset allocation strategies' must not become a second one."""
    for known in R.AUTO_SLEEVE_CATEGORIES:
        if into.lower() == known.lower():
            return known
    return into


def normalise(ruleList) -> list:
    """The canonical form of a list of rules, in the order given, or a
    ValidationError naming ``rules``. Each rule: a name; the category it goes
    into, not a strategic category or another rule's (judged without regard
    to case); where that category sits - at the end, after a strategic
    category, or after the category of a rule ABOVE it; a size above zero
    and at most 100, to four places; a basis; one to four sources, each a
    strategic category, weights to four places adding up to exactly 100; at
    most one rule per switch; known currencies. A rule keeps the key it was
    given; a new one is given a key no other rule in the list has."""
    if not isinstance(ruleList, (list, tuple)):
        raise ValidationError('rules', 'Send the rules as a list.')
    if len(ruleList) > MAX_RULES:
        raise ValidationError('rules', 'At most {} rules.'.format(MAX_RULES))
    strategic = R.overlaySourceCategories()
    taken = {c.lower() for c in strategic} | {g['name'].lower() for g in R.SLEEVE_GROUPS}
    currencies = R._derived()['CURRENCIES']

    # the keys rules already carry are reserved first, so a new rule's
    # generated key can never take one an existing rule holds
    reserved = set()
    for position, rule in enumerate(ruleList, start=1):
        if not isinstance(rule, dict):
            raise ValidationError('rules', 'Rule {} is not a rule.'.format(position))
        given = _text(rule.get('id'), 'the key of rule {}'.format(position)).lower()
        if not given:
            continue
        if not _ID.match(given):
            raise ValidationError('rules', 'The key {!r} of rule {} is not a short lower-case '
                                  'key.'.format(given, position))
        if given in reserved:
            raise ValidationError('rules', 'Two rules carry the key {!r}.'.format(given))
        reserved.add(given)

    out, names, intos, toggles, above = [], set(), set(), set(), []
    for position, rule in enumerate(ruleList, start=1):
        name = _text(rule.get('name'), 'the name of rule {}'.format(position))
        if not name:
            raise ValidationError('rules', 'Rule {} has no name.'.format(position))
        if len(name) > NAME_MAX:
            raise ValidationError('rules', 'The name of rule {} is over {} characters.'.format(
                position, NAME_MAX))
        if name.lower() in names:
            raise ValidationError('rules', 'Two rules are called {}.'.format(name))
        names.add(name.lower())
        at = name

        ident = _text(rule.get('id'), 'the key of ' + at).lower()
        if not ident:
            base = _slug(name)
            ident, n = base, 2
            while ident in reserved:
                ident = '{}-{}'.format(base, n)
                n += 1
            reserved.add(ident)

        into = _canonicalInto(_text(rule.get('into'), 'the category {} goes into'.format(at)))
        if not into:
            raise ValidationError('rules', 'Name the category {} goes into.'.format(at))
        if len(into) > NAME_MAX:
            raise ValidationError('rules', 'The category {} goes into has a name over {} '
                                  'characters.'.format(at, NAME_MAX))
        if into.lower() == R.OVERLAY_END:
            raise ValidationError('rules', '{} cannot go into a category called {!r}.'.format(at, into))
        if into.lower() in taken:
            raise ValidationError('rules', '{} cannot go into {}: it is a strategic category, and an '
                                  'overlay goes into a category of its own.'.format(at, into))
        if into.lower() in intos:
            raise ValidationError('rules', 'Two rules go into {}.'.format(into))
        intos.add(into.lower())
        row = _text(rule.get('row'), 'the row of ' + at) or into
        if len(row) > NAME_MAX:
            raise ValidationError('rules', 'The row of {} has a name over {} characters.'.format(
                at, NAME_MAX))

        basis = rule.get('basis')
        if basis not in R.OVERLAY_BASES:
            raise ValidationError('rules', 'The basis of {} must be one of {}.'.format(
                at, ', '.join(R.OVERLAY_BASES)))
        size = _number(rule.get('size'), 'the size of ' + at)
        if not size > 0:
            raise ValidationError('rules', 'The size of {} must be above zero.'.format(at))
        if size > 100:
            raise ValidationError('rules', 'The size of {} is over 100%.'.format(at))
        if not _places(size):
            raise ValidationError('rules', 'The size of {} has more than {} decimal places.'.format(
                at, PLACES))

        sources = rule.get('sources')
        if not isinstance(sources, (list, tuple)) or not sources:
            raise ValidationError('rules', 'Name at least one category {} is funded from.'.format(at))
        if len(sources) > MAX_SOURCES:
            raise ValidationError('rules', '{} can be funded from at most {} categories.'.format(
                at, MAX_SOURCES))
        cleaned, seen, units = [], set(), 0
        for source in sources:
            if not isinstance(source, dict):
                raise ValidationError('rules', 'One of the sources of {} is not a category and a '
                                      'weight.'.format(at))
            category = _text(source.get('category'), 'a source of ' + at)
            if category not in strategic:
                raise ValidationError('rules', '{} cannot be funded from {!r}: fund it from a strategic '
                                      'category.'.format(at, category))
            if category in seen:
                raise ValidationError('rules', '{} names {} twice.'.format(at, category))
            seen.add(category)
            what = 'the weight {} puts on {}'.format(at, category)
            weight = _number(source.get('weightPct'), what)
            if not weight > 0:
                raise ValidationError('rules', 'The weight {} puts on {} must be above zero.'.format(
                    at, category))
            if weight > 100:
                raise ValidationError('rules', 'The weight {} puts on {} is over 100%.'.format(
                    at, category))
            if not _places(weight):
                raise ValidationError('rules', 'The weight {} puts on {} has more than {} decimal '
                                      'places.'.format(at, category, PLACES))
            units += int(round(weight * _SCALE))
            cleaned.append({'category': category, 'weightPct': round(weight, PLACES)})
        if units != _WHOLE:
            raise ValidationError('rules', 'The weights of {} add up to {:.{p}f}%; they must add up to '
                                  'exactly 100%.'.format(at, units / _SCALE, p=PLACES))

        place = _text(rule.get('place'), 'where {} sits'.format(at)) or cleaned[0]['category']
        if place.lower() == into.lower():
            raise ValidationError('rules', '{} cannot be placed after itself.'.format(at))
        if place != R.OVERLAY_END and place not in strategic and place not in above:
            raise ValidationError('rules', '{} is placed after {!r}: it can only sit at the end of the '
                                  'table, after a strategic category, or after the category of a rule '
                                  'above it.'.format(at, place))

        toggle = rule.get('toggle') or None
        if toggle is not None:
            if toggle not in R.OVERLAY_TOGGLES:
                raise ValidationError('rules', 'The switch {!r} on {} is not one the proposal '
                                      'has.'.format(toggle, at))
            if toggle in toggles:
                raise ValidationError('rules', 'Two rules answer to the same switch on the proposal.')
            toggles.add(toggle)

        held = rule.get('currencies') or []
        if not isinstance(held, (list, tuple)):
            raise ValidationError('rules', 'The currencies of {} must be a list.'.format(at))
        kept = []
        for currency in held:
            if currency not in currencies:
                raise ValidationError('rules', '{} names an unknown currency {!r}.'.format(at, currency))
            if currency not in kept:
                kept.append(currency)

        out.append({'id': ident, 'name': name, 'into': into, 'row': row, 'place': place,
                    'size': round(size, PLACES), 'basis': basis, 'sources': cleaned,
                    'toggle': toggle, 'currencies': kept})
        above.append(into)
    return out


def _scope(scope) -> str:
    if scope is not None and not isinstance(scope, str):
        raise ValidationError('scope', 'Name the scope: the house list or an implementation type.')
    scope = (scope or '').strip()
    if scope == HOUSE:
        return HOUSE
    if scope not in sleeveRepo.VARIANTS:
        raise ValidationError('scope', 'Unknown implementation type {!r}.'.format(scope))
    return scope


def _note(note) -> str:
    if note is not None and not isinstance(note, str):
        raise ValidationError('note', 'Say why, in words: every change to the overlay rules carries a note.')
    note = (note or '').strip()
    if not note:
        raise ValidationError('note', 'Say why: every change to the overlay rules carries a note.')
    return note


def _base(base):
    """The list a write was made against, ``(scope, revision)``, or None
    when the caller did not say."""
    if base is None:
        return None
    if not isinstance(base, dict) or not isinstance(base.get('scope'), str) \
            or isinstance(base.get('revision'), bool) or not isinstance(base.get('revision'), int):
        raise ValidationError('base', 'The list this change was made against is not a scope and '
                              'a revision.')
    return (base['scope'], base['revision'])


# ----------------------------------------------- what a list asks of whom ---

def _variantsFor(scope, overrides) -> list:
    """The implementation types a list under *scope* applies to: the type
    itself, or every type without a list of its own - and, should every type
    have one, every type, so a house list is never saved unchecked."""
    if scope != HOUSE:
        return [scope]
    inheriting = [v for v in sleeveRepo.VARIANTS if v not in overrides]
    return inheriting or list(sleeveRepo.VARIANTS)


def _offers(key, variant) -> bool:
    try:
        R.validateKey(key, variant)
        return True
    except ValidationError:
        return False


def _keysFor(variants) -> list:
    """Every strategic portfolio one of *variants* offers, as the picker and
    ``rules.validateKey`` judge it: its allocations, the real-assets
    exclusion where the type forces it, and every all-equity portfolio. The
    mandate's $20m rule is left out - it only ever narrows the set."""
    return [k for k in universe.keys() if any(_offers(k, v) for v in variants)]


def _label(key) -> str:
    return '{} {}'.format(key.currency, R.portfolioHeader(key))


def _switchWords(selection, ruleList) -> str:
    """The switches a shortfall needs, in words - only those a rule in the
    list answers to: 'with Tactical Tilts off'."""
    used = [r['toggle'] for r in ruleList if r.get('toggle')]
    parts = ['{} {}'.format(_SWITCH_WORDS.get(t, t), 'on' if selection.get(t) else 'off')
             for t in R.OVERLAY_TOGGLES if t in used]
    return ('with ' + ' and '.join(parts)) if parts else ''


def shortfalls(ruleList, variants) -> list:
    """Per rule, the worst case among the portfolios of *variants* that hold
    every source of it but could not fund it at its step, under any setting
    of the proposal's switches: ``{'portfolio', 'keyStr', 'category',
    'after', 'selection'}``, or None. A rule a portfolio cannot fund is
    skipped for it, as it would be on the page, and the rules below read
    what is left."""
    worst = [None] * len(ruleList)
    for key in _keysFor(variants):
        strategic = universe.categoryRows(key)
        for selection in _selections():
            result = strategic
            for index, rule in enumerate(ruleList):
                if R._skipReason(rule, selection, key.currency):
                    continue
                for category, after in R.overlayShortfall(rule, result, strategic):
                    if worst[index] is None or after < worst[index]['after'] - R.OVERLAY_TOLERANCE:
                        worst[index] = {'portfolio': _label(key), 'keyStr': key.toStr(),
                                        'category': category, 'after': after,
                                        'selection': dict(selection)}
                result = R.resolveOverlays(result, [rule], selection, key.currency)['categories']
    return worst


def _orderOnly(before, after) -> bool:
    """Whether *after* is *before* with nothing but the order changed."""
    if before is None or len(before) != len(after):
        return False
    old = {r['id']: r for r in before}
    return all(old.get(r['id']) == r for r in after) and [r['id'] for r in before] != \
        [r['id'] for r in after]


def _refuseShortfalls(ruleList, variants, scope, inForce=None) -> None:
    worst = shortfalls(ruleList, variants)
    for index, case in enumerate(worst):
        if case is None:
            continue
        rule = ruleList[index]
        lead = ''
        if inForce is not None and _orderOnly(inForce, ruleList):
            was = [r['id'] for r in inForce].index(rule['id'])
            moved = [r for r in ruleList[:index] if r['id'] in {x['id'] for x in inForce[was + 1:]}]
            if moved:
                lead = 'Moving {} above {} leaves {} unable to fund itself: '.format(
                    ' and '.join(r['name'] for r in moved), rule['name'], rule['name'])
        when = _switchWords(case['selection'], ruleList)
        raise ValidationError(
            'rules', '{}{} ({}) would take {} to {:.2f}% in {}{}. Every portfolio {} that holds {} must '
            'be able to fund it: lower its size, fund it from elsewhere, or move it.'.format(
                lead, rule['name'], _ordinal(index + 1), case['category'], case['after'],
                case['portfolio'], ' ' + when if when else '',
                'of the types on the house list' if scope == HOUSE else 'of ' + scope,
                ' and '.join(s['category'] for s in rule['sources'])))


def warnings(ruleList, variants) -> list:
    """What a saved list leaves undone: a rule whose category has no sleeve
    under a type it applies to cannot be exported until one is created."""
    from .sleeves import listSleeves
    out = []
    for rule in ruleList:
        missing = [v for v in variants if not listSleeves(rule['into'], v)]
        if missing:
            out.append('No sleeve in {} yet for {}: a proposal holding {} cannot be exported until one '
                       'is created in the Sleeves view.'.format(
                           rule['into'], ', '.join(missing), rule['name']))
    return out


# --------------------------------------------------------------- storage ---

def _row(conn, scope):
    return conn.execute('SELECT * FROM policies WHERE kind = ? AND scope = ?',
                        (KIND, scope)).fetchone()


def seed(conn) -> None:
    """The house list's baseline, once - race-safe the way the library's
    seed is (sleeveRepo._seedOnce): asked again inside a write transaction.
    Called on every connection to the repository's file, so the first open
    after an upgrade writes it (schema 6)."""
    if _row(conn, HOUSE) is not None:
        return
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    try:
        if _row(conn, HOUSE) is None:
            body = json.dumps(R.baselineOverlays())
            stamp = _now()
            conn.execute('INSERT INTO policies (kind, scope, body, revision, updatedBy, updatedAt) '
                         'VALUES (?, ?, ?, 1, ?, ?)', (KIND, HOUSE, body, 'system', stamp))
            conn.execute('INSERT INTO policyHistory (kind, scope, revision, action, body, note, '
                         'actor, at) VALUES (?, ?, 1, ?, ?, ?, ?, ?)',
                         (KIND, HOUSE, 'baseline', body,
                          'D155: the two overlays as the code applied them - the tilt, 8% of the '
                          'portfolio from Investment Grade Fixed Income, then the premium, 7.5% of '
                          'Investment Grade Fixed Income as the tilt left it',
                          'system', stamp))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _connect():
    # sleeveRepo's connection seeds the list itself (schema 6)
    return sleeveRepo.connection()


_readCache = None            # (stamp, {scope: row fields})


def _stamp(path):
    """What identifies one state of the file: its identity, size and
    modification time, and SQLite's own file change counter (header offset
    24), which every committed write increments - the counter is what a
    coarse or cached modification time on a network share cannot hide."""
    try:
        st = os.stat(path)
        with open(path, 'rb') as handle:
            header = handle.read(28)
    except OSError:
        return None
    counter = int.from_bytes(header[24:28], 'big') if len(header) >= 28 else -1
    return (path, st.st_ino, st.st_size, st.st_mtime_ns, counter)


def _forget() -> None:
    """Drop the cached lists: called after every write from this process."""
    global _readCache
    with _lock:
        _readCache = None


def _readPolicies():
    """Every overlay list, ``{scope: row}``, read the cheap way: the schema
    is assembled on every basis and mandate change and must stay cheap
    (test_baked_adapter), and a full connection runs the repository's
    migration and schema script each time. The lists are kept in memory
    against ``_stamp`` - taken BEFORE the read, so a write landing during it
    makes the next read look again rather than keep a stale answer - and
    re-read through a plain read-only connection when the file has moved. A
    file without the house list yet - never opened since the upgrade - goes
    through the full connection, which seeds it."""
    global _readCache
    path = sleeveRepo.dbPath()
    stamp = _stamp(path)
    with _lock:
        if stamp is not None and _readCache is not None and _readCache[0] == stamp:
            return dict(_readCache[1])
    rows = None
    if stamp is not None:
        try:
            conn = sqlite3.connect('file:{}?mode=ro'.format(path), uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute('SELECT * FROM policies WHERE kind = ?', (KIND,)).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            rows = None
    if not rows or not any(r['scope'] == HOUSE for r in rows):
        conn = _connect()
        try:
            stamp = _stamp(path)
            rows = conn.execute('SELECT * FROM policies WHERE kind = ?', (KIND,)).fetchall()
        finally:
            conn.close()
    found = {r['scope']: {k: r[k] for k in r.keys()} for r in rows}
    with _lock:
        _readCache = (stamp, found)
    return dict(found)


def _entry(row, inherited=False) -> dict:
    return {'scope': row['scope'], 'revision': row['revision'], 'rules': json.loads(row['body']),
            'updatedBy': row['updatedBy'], 'updatedAt': row['updatedAt'], 'inherited': inherited}


def _overrides(conn) -> dict:
    rows = conn.execute('SELECT * FROM policies WHERE kind = ? AND scope != ?',
                        (KIND, HOUSE)).fetchall()
    return {r['scope']: _entry(r) for r in rows}


def current(variant=None) -> dict:
    """The list in force for *variant*: its own, or the house list
    (``inherited`` True). No variant means the house list."""
    rows = _readPolicies()
    if variant and variant != HOUSE:
        if variant in rows:
            return _entry(rows[variant])
        return _entry(rows[HOUSE], inherited=True)
    return _entry(rows[HOUSE])


def allRules() -> list:
    """Every rule in every list, the house list first and then the types'
    in name order: what places the categories in the repository's own list
    (sleeveRepo.categories)."""
    rows = _readPolicies()
    scopes = [HOUSE] + sorted(s for s in rows if s != HOUSE)
    return [rule for scope in scopes for rule in json.loads(rows[scope]['body'])]


def allIntos() -> list:
    """Every category a rule in any list goes into, the house list's first."""
    out = []
    for rule in allRules():
        if rule['into'] not in out:
            out.append(rule['into'])
    return out


def _sample(variant) -> str:
    """A portfolio to show a type's rules working on: USD Moderate in the
    first allocation the type offers, Full where it does."""
    offered = [k for k in universe.keys() if _offers(k, variant)]
    for allocation in R.VARIANT_ALLOCATIONS.get(variant) or ['Full']:
        for key in offered:
            if key.currency == 'USD' and key.riskLevel == 'Moderate' and key.allocationType == allocation:
                return key.toStr()
    return offered[0].toStr() if offered else universe.keys()[0].toStr()


def describe() -> dict:
    """Everything the console shows: the house list, every type's own, the
    strategic categories a rule may be funded from, every portfolio's
    category weights (so the console can judge a draft as the server will),
    which portfolios each type offers, a sample portfolio per type, which
    categories each type has a sleeve in, and the warnings in force."""
    from .sleeves import listSleeves
    rows = _readPolicies()
    house = _entry(rows[HOUSE])
    overrides = {s: _entry(r) for s, r in rows.items() if s != HOUSE}
    keys = list(universe.keys())
    portfolios = []
    for key in keys:
        portfolios.append({'keyStr': key.toStr(), 'label': _label(key), 'currency': key.currency,
                           'allocationType': key.allocationType, 'allEquity': key.isAllEquity,
                           'weights': {r['name']: float(r['weightPct'])
                                       for r in universe.categoryRows(key)}})
    intos = []
    for entry in [house] + list(overrides.values()):
        for rule in entry['rules']:
            if rule['into'] not in intos:
                intos.append(rule['into'])
    sleeved = {v: [c for c in intos if listSleeves(c, v)] for v in sleeveRepo.VARIANTS}
    scopeWarnings = {HOUSE: warnings(house['rules'], _variantsFor(HOUSE, overrides))}
    for scope, entry in overrides.items():
        scopeWarnings[scope] = warnings(entry['rules'], [scope])
    return {'house': house, 'overrides': overrides, 'variants': list(sleeveRepo.VARIANTS),
            'categories': R.overlaySourceCategories(),
            'tableCategories': R.categoriesInUniverseOrder(),
            'automatic': list(R.AUTO_SLEEVE_CATEGORIES),
            'groups': [g['name'] for g in R.SLEEVE_GROUPS],
            'currencies': R._derived()['CURRENCIES'],
            'offered': {v: [k.toStr() for k in keys if _offers(k, v)] for v in sleeveRepo.VARIANTS},
            'samples': {v: _sample(v) for v in sleeveRepo.VARIANTS},
            'portfolios': portfolios, 'sleeved': sleeved, 'warnings': scopeWarnings,
            'toggles': list(R.OVERLAY_TOGGLES), 'bases': list(R.OVERLAY_BASES),
            'tolerance': R.OVERLAY_TOLERANCE,
            'places': PLACES, 'maxRules': MAX_RULES, 'maxSources': MAX_SOURCES,
            'nameMax': NAME_MAX}


# ---------------------------------------------------------- the history ---

def _names(ruleList) -> str:
    return ', '.join('{} {}'.format(_ordinal(i + 1), r['name']) for i, r in enumerate(ruleList))


def _sourcesText(sources) -> str:
    return ' and '.join('{} {}%'.format(s['category'], R._pct(s['weightPct'])) for s in sources)


def _fieldChanges(was, now) -> list:
    """What changed in one rule, field by field: 'size 7.5% → 8%'."""
    out = []
    if was['name'] != now['name']:
        out.append('renamed from {}'.format(was['name']))
    if was['into'] != now['into']:
        out.append('goes into {} (was {})'.format(now['into'], was['into']))
    if was.get('row') != now.get('row'):
        out.append('row {} (was {})'.format(now.get('row'), was.get('row')))
    if was.get('place') != now.get('place'):
        out.append('sits after {} (was {})'.format(now.get('place'), was.get('place')))
    if was['size'] != now['size']:
        out.append('size {}% → {}%'.format(R._pct(was['size']), R._pct(now['size'])))
    if was['basis'] != now['basis']:
        out.append('of {} (was of {})'.format(
            'the portfolio' if now['basis'] == 'portfolio' else 'its sources',
            'the portfolio' if was['basis'] == 'portfolio' else 'its sources'))
    if was['sources'] != now['sources']:
        out.append('funded from {} (was {})'.format(_sourcesText(now['sources']),
                                                   _sourcesText(was['sources'])))
    if (was.get('toggle') or None) != (now.get('toggle') or None):
        out.append('switch {}'.format(_SWITCH_WORDS.get(now.get('toggle'), 'none')))
    if (was.get('currencies') or []) != (now.get('currencies') or []):
        out.append('held in {}'.format(' · '.join(now.get('currencies') or []) or 'any currency'))
    return out


def describeChange(before, after) -> str:
    """What changed from one list to the next, in words: rules added,
    removed, or edited field by field, and the order - 'Strategic
    Volatility Premium: size 7.5% → 8%', 'Order changed: Strategic
    Volatility Premium moved above Tactical Tilts'."""
    if after is None:
        return 'Back on the house list'
    before = before or []
    old = {r['id']: r for r in before}
    new = {r['id']: r for r in after}
    parts = []
    for index, rule in enumerate(after):
        if rule['id'] not in old:
            parts.append('Added {} ({})'.format(rule['name'], _ordinal(index + 1)))
    for rule in before:
        if rule['id'] not in new:
            parts.append('Removed {}'.format(rule['name']))
    for rule in after:
        was = old.get(rule['id'])
        if was is not None and was != rule:
            changes = _fieldChanges(was, rule)
            parts.append('{}: {}'.format(rule['name'], ', '.join(changes) or 'changed'))
    common = [r['id'] for r in after if r['id'] in old]
    previously = [r['id'] for r in before if r['id'] in new]
    for now, then in zip(common, previously):
        if now != then:
            parts.append('Order changed: {} moved above {}'.format(new[now]['name'], new[then]['name']))
            break
    return '; '.join(parts) or 'No change'


def history(scope) -> list:
    """Every revision of one scope's list, newest first, each with what
    changed from the list in force before it - for a type's first list of
    its own, the house list it was taken from - and, for a restore, which
    revision it brought back."""
    scope = _scope(scope)
    conn = _connect()
    try:
        rows = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? '
                            'ORDER BY revision', (KIND, scope)).fetchall()
        houseRows = [] if scope == HOUSE else conn.execute(
            'SELECT id, body FROM policyHistory WHERE kind = ? AND scope = ? ORDER BY id',
            (KIND, HOUSE)).fetchall()
    finally:
        conn.close()

    def houseAt(rowId):
        body = None
        for h in houseRows:
            if h['id'] < rowId and h['body']:
                body = json.loads(h['body'])
        return body

    out, previous, bodies = [], None, []
    for r in rows:
        body = json.loads(r['body']) if r['body'] else None
        if r['action'] == 'baseline':
            change = 'Seeded from the code: ' + _names(body or [])
        elif r['action'] == 'removed':
            change = describeChange(previous, None)
        else:
            own = previous is None and scope != HOUSE
            against = houseAt(r['id']) if own else previous
            change = describeChange(against, body)
            if own:
                change = 'Own list' + ('' if change == 'No change' else ': ' + change) \
                    if against is not None else 'Own list: ' + (_names(body) or 'no rules')
            if r['action'] == 'reverted':
                # the latest earlier revision holding exactly this list: the one
                # a restore from the history panel is nearly always made from
                source = next((n for n, b in reversed(bodies) if b == body), None)
                change = 'Restored {}: {}'.format('r{}'.format(source) if source else 'an earlier list',
                                                  change)
        out.append({'scope': r['scope'], 'revision': r['revision'], 'action': r['action'],
                    'rules': body, 'change': change, 'note': r['note'], 'actor': r['actor'],
                    'at': r['at']})
        previous = body
        if body is not None:
            bodies.append((r['revision'], body))
    out.reverse()
    return out


# ---------------------------------------------------------------- writes ---

def _write(conn, scope, ruleList, action, note, user) -> None:
    top = conn.execute('SELECT COALESCE(MAX(revision), 0) FROM policyHistory WHERE kind = ? '
                       'AND scope = ?', (KIND, scope)).fetchone()[0]
    revision = top + 1
    body = json.dumps(ruleList)
    stamp = _now()
    # REPLACE rather than an upsert: the host's SQLite may predate 3.24
    conn.execute('INSERT OR REPLACE INTO policies (kind, scope, body, revision, updatedBy, '
                 'updatedAt) VALUES (?, ?, ?, ?, ?, ?)',
                 (KIND, scope, body, revision, user or '', stamp))
    conn.execute('INSERT INTO policyHistory (kind, scope, revision, action, body, note, actor, at) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                 (KIND, scope, revision, action, body, note, user or '', stamp))


def _writing(work):
    """Run *work(conn)* holding the file's write lock from its first read to
    its commit (BEGIN IMMEDIATE), so a save's checks and its revision are
    one step. The cached lists are dropped after."""
    conn = _connect()
    try:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        try:
            result = work(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return result
    finally:
        conn.close()
        _forget()


def _inForce(conn, scope):
    """The row in force for *scope* - its own, or the house row - and
    whether it is the house row standing in."""
    own = _row(conn, scope)
    if own is not None:
        return own, False
    return _row(conn, HOUSE), scope != HOUSE


def _refuseStale(conn, scope, base) -> None:
    if base is None:
        return
    row, _ = _inForce(conn, scope)
    if (row['scope'], row['revision']) != base:
        raise StaleError(
            'base', 'The overlay rules for {} were changed by {} at {} since you opened them '
            '(now {} r{}). Reload to see the change, then make yours again.'.format(
                'the house list' if scope == HOUSE else scope, row['updatedBy'] or 'someone',
                (row['updatedAt'] or '').replace('T', ' '), 'the house list' if row['scope'] == HOUSE else row['scope'],
                row['revision']))


def save(scope, ruleList, note, user='', base=None) -> dict:
    """Put a list in force for *scope* ('*' or a type). Refused, naming the
    field, when a rule is malformed, a portfolio could not fund a rule it
    holds the sources of, the note is missing, nothing would change, or -
    as STALE - *base* is not the list in force. Returns the entry in force
    and the warnings it carries."""
    scope = _scope(scope)
    cleaned = normalise(ruleList)
    note = _note(note)
    base = _base(base)

    def work(conn):
        _refuseStale(conn, scope, base)
        overrides = _overrides(conn)
        variants = _variantsFor(scope, overrides)
        row, _ = _inForce(conn, scope)
        inForce = json.loads(row['body'])
        existing = _row(conn, scope)
        if existing is not None and json.loads(existing['body']) == cleaned:
            raise ValidationError('rules', 'Those are the rules already in force.')
        _refuseShortfalls(cleaned, variants, scope, inForce)
        _write(conn, scope, cleaned, 'updated', note, user)
        return _entry(_row(conn, scope)), variants

    entry, variants = _writing(work)
    return {'entry': entry, 'warnings': warnings(cleaned, variants)}


def removeOverride(scope, note, user='', base=None) -> dict:
    """Put *scope* back on the house list. Refused when the house list could
    not be funded by the type's portfolios. The type's revisions stay on the
    record, closed by a *removed* revision."""
    scope = _scope(scope)
    if scope == HOUSE:
        raise ValidationError('scope', 'The house list cannot be removed, only changed.')
    note = _note(note)
    base = _base(base)

    def work(conn):
        _refuseStale(conn, scope, base)
        if _row(conn, scope) is None:
            raise ValidationError('scope', '{} has no list of its own to remove.'.format(scope))
        house = json.loads(_row(conn, HOUSE)['body'])
        # back on the house list, the type must be able to fund it
        _refuseShortfalls(house, [scope], scope)
        top = conn.execute('SELECT MAX(revision) FROM policyHistory WHERE kind = ? AND '
                           'scope = ?', (KIND, scope)).fetchone()[0]
        conn.execute('DELETE FROM policies WHERE kind = ? AND scope = ?', (KIND, scope))
        conn.execute('INSERT INTO policyHistory (kind, scope, revision, action, body, note, '
                     'actor, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                     (KIND, scope, top + 1, 'removed', '', note, user or '', _now()))
        return _entry(_row(conn, HOUSE), inherited=True)

    return _writing(work)


def revert(scope, revision, note, user='', base=None) -> dict:
    """Put an earlier revision of *scope*'s list back in force, as a new
    revision - re-validated, since the universe and the other lists may have
    moved since it was saved."""
    scope = _scope(scope)
    note = _note(note)
    base = _base(base)
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValidationError('revision', 'Choose a revision to restore.')

    def work(conn):
        _refuseStale(conn, scope, base)
        row = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? AND '
                           'revision = ?', (KIND, scope, revision)).fetchone()
        if row is None:
            raise ValidationError('revision', 'There is no revision {} of this list.'.format(revision))
        if not row['body']:
            raise ValidationError('revision', 'Revision {} removed the list; there are no rules in '
                                  'it to restore.'.format(revision))
        cleaned = normalise(json.loads(row['body']))
        existing = _row(conn, scope)
        if existing is not None and json.loads(existing['body']) == cleaned:
            raise ValidationError('revision', 'Those rules are already in force.')
        inForce = json.loads(_inForce(conn, scope)[0]['body'])
        variants = _variantsFor(scope, _overrides(conn))
        _refuseShortfalls(cleaned, variants, scope, inForce)
        _write(conn, scope, cleaned, 'reverted', note, user)
        return _entry(_row(conn, scope)), variants, cleaned

    entry, variants, cleaned = _writing(work)
    return {'entry': entry, 'warnings': warnings(cleaned, variants)}
