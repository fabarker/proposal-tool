"""The fee card, held in the repository and maintained from the console (D166).

The fee framework (fees.py) prices from a card: a rate for every schedule
and, under a grouped schedule, every fee group, at every account-size tier
and every level. The card was delivered as a long CSV at SCENARIO_FEES_SOURCE
and read once at start (D55). It is now a setting in the repository's file
(sleeveRepo, schema 7), kept the way the overlay rules and the funding split
are (D148, D155): the version in force in ``policies`` under the kind
'feeCard', and every version on the record in ``policyHistory``, each with
who saved it, when, and why.

Seeding. The first open after the upgrade loads the card from the file it
used to be read from - SCENARIO_FEES_SOURCE, else the packaged placeholder -
as version 1, marked *seeded*, so an upgrade changes no rate. Race-safe the
way the library's seed is.

What may be saved, from the Fees tab, a restore or an imported file:

* a card the framework can price from - exactly the check a delivery had
  (fees.checkCard): tiers contiguous from $0 with an open top, every rate
  present for every schedule, group, tier and level, floor <= target <=
  ceiling on every band - with every rate a number from 0 up to but not
  including 10, since a rate is a percentage and 45 for 0.45 is a slip;
* fee groups with names, each once;
* no fee group a live product is priced by taken away - the refusal names
  the products - and a group renamed carries its products with it, each
  product taking a revision of its own in the same step (products.py);
* a note, always: every change to the fee card says why.

Tiers are numbered T1, T2, ... from the lowest. Their edges may be moved and
tiers added or taken away; when the edges change, the ids are given again in
order, and what changed is described by the tiers' ranges, which is what a
reader sees. A tier's rates go where its range goes.

Concurrency, as the overlay rules: every write takes the file's write lock
first (BEGIN IMMEDIATE), and a write names the version it was made against
(``base``); when that is no longer the version in force it is refused as
STALE (HTTP 409) and the console reloads.

Reads are kept in memory against the file's stamp (sleeveRepo.fileStamp),
so pricing costs what it did when the card was a module constant. A change
reaches proposals being priced at once; a delivered proposal keeps its
workbook as sent, and the register records the version that priced it.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import math
import os
import threading

from . import fees
from .types import ValidationError

KIND = 'feeCard'
SCOPE = '*'
ACTIONS = ['seeded', 'updated', 'imported', 'reverted']
#: a rate is a percentage: 0.45 means 0.45%, and 10 or more is a slip
RATE_MAX = 10.0
GROUP_MAX = 40
NOTE_MAX = 240
#: rate changes a history line lists before it counts the rest
_LIST_RATES = 24

_lock = threading.Lock()
_cache = None            # (stamp, parsed card)
_seedProblem = None


class StaleError(ValidationError):
    """The card a write was made against is no longer the version in force
    -> HTTP 409 {error, field}."""


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def _number(value):
    """A rate as a float, or None when it is not a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value.strip().rstrip('%').strip())
        except ValueError:
            return None
    else:
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _noRate(value) -> bool:
    """Whether a box was left without a rate: nothing, blank, or a - (D185)."""
    return value is None or (isinstance(value, str) and fees.isNoRate(value))


def _amount(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value.strip().replace(',', ''))
        except ValueError:
            return None
    else:
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _rateText(value) -> str:
    """A rate as the card reads: two places at least, as the viewers print
    it, and a third or fourth when the rate has one; a cell without one is
    a - (D185)."""
    if value is None:
        return fees.NO_RATE
    short = '{:g}'.format(value)
    places = len(short.split('.')[1]) if '.' in short and 'e' not in short else 0
    return '{:.{}f}'.format(value, max(2, places))


def _groupName(name) -> str:
    return ' '.join(str(name or '').split())


def _rowsFor(schedule, groups):
    """The rows a schedule has: one, '', for a uniform schedule; one per
    group for a grouped one."""
    return list(groups) if fees.byGroup(schedule) else ['']


def _where(schedule, group, tierLabel, level=None) -> str:
    parts = [schedule] + ([group] if group else []) + [tierLabel] + ([level] if level else [])
    return ' · '.join(parts)


# ------------------------------------------------------------ the shapes ---
# A stored card (the body of its row):
#   {"tiers": [{"id": "T1", "min": 0, "max": 10000000}, ..., {"id": "T5", "min": 1e8, "max": null}],
#    "feeGroups": ["Passive", ...],
#    "rates": {"CASP": {"": {"T1": {"Management Floor": 0.45, ...}, ...}},
#              "RDR": {"Passive": {"T1": {...}, ...}, ...}},
#    "placeholder": false,
#    "renames": [["Old name", "New name"]]}      <- only on the version that made them

def fromDelivery(delivered, placeholder=False) -> dict:
    """A delivered file's rows (fees._readDelivery) as a stored card."""
    tierList = [{'id': t['id'], 'min': t['min'], 'max': t['max']} for t in delivered['tiers']]
    rates = {}
    for (schedule, group, tier, source, point), rate in delivered['cells'].items():
        rates.setdefault(schedule, {}).setdefault(group or '', {}).setdefault(tier, {})[
            fees.levelId(source, point)] = float(rate)
    return {'tiers': tierList, 'feeGroups': list(delivered['feeGroups']), 'rates': rates,
            'placeholder': bool(placeholder)}


def _parse(body) -> dict:
    """A stored card as the framework reads it: cells keyed by coordinate
    and tiers labelled."""
    tierList = [{'id': t['id'], 'min': float(t['min']),
                 'max': None if t['max'] is None else float(t['max']),
                 'label': fees.tierLabel(float(t['min']), None if t['max'] is None else float(t['max']))}
                for t in body['tiers']]
    cells = {}
    for schedule, byRow in body['rates'].items():
        for group, byTier in byRow.items():
            for tier, byLevel in byTier.items():
                for level, rate in byLevel.items():
                    source, point = fees.splitLevel(level)
                    cells[(schedule, group or None, tier, source, point)] = float(rate)
    return {'tiers': tierList, 'feeGroups': list(body['feeGroups']), 'cells': cells,
            'placeholder': bool(body.get('placeholder'))}


def _stored(body) -> dict:
    """A body without what belongs to its version only."""
    return {k: body[k] for k in ('tiers', 'feeGroups', 'rates', 'placeholder') if k in body}


# ------------------------------------------------------------- the seed ---

def _rowOn(conn):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'policies'"
                    ).fetchone() is None:
        return None
    return conn.execute('SELECT * FROM policies WHERE kind = ? AND scope = ?',
                        (KIND, SCOPE)).fetchone()


def groupsOn(conn):
    """The fee groups of the card in force on *conn*, or None before the card
    has been loaded - for the catalogue's checks inside a write."""
    row = _rowOn(conn)
    return list(json.loads(row['body'])['feeGroups']) if row is not None else None


def _seedNote(path) -> str:
    """What the card's first version says it was loaded from."""
    named = os.path.basename(path)
    if os.path.abspath(path) == os.path.abspath(fees._DEFAULT_RATES) and fees.PLACEHOLDER:
        return 'Loaded from {}, the placeholder card the tool was installed with'.format(named)
    return 'Loaded from {}'.format(named)


def seed(conn) -> None:
    """The card in force, once, from the file it used to be read from. A file
    that cannot be read leaves the card unloaded - every price then says why
    - and the next connection tries again."""
    global _seedProblem
    if _rowOn(conn) is not None:
        return
    path = fees.ratesPath()
    try:
        delivered = fees._readDelivery(path)
        body = fromDelivery(delivered, placeholder=fees.PLACEHOLDER)
        parsed = _parse(body)
        fees.checkCard(parsed['tiers'], parsed['feeGroups'], parsed['cells'])
    except (OSError, ValueError) as exc:
        _seedProblem = 'The fee card could not be loaded from {}: {}'.format(path, exc)
        return
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    try:
        if _rowOn(conn) is None:
            stamp = _now()
            text = json.dumps(body)
            conn.execute('INSERT INTO policies (kind, scope, body, revision, updatedBy, updatedAt) '
                         'VALUES (?, ?, ?, 1, ?, ?)', (KIND, SCOPE, text, 'seed', stamp))
            conn.execute('INSERT INTO policyHistory (kind, scope, revision, action, body, note, '
                         'actor, at) VALUES (?, ?, 1, ?, ?, ?, ?, ?)',
                         (KIND, SCOPE, 'seeded', text, _seedNote(path), 'seed', stamp))
        conn.commit()
        _seedProblem = None
    except Exception:
        conn.rollback()
        raise


# ------------------------------------------------------------ the reads ---

def _readOn(conn):
    row = _rowOn(conn)
    if row is None:
        return None
    body = json.loads(row['body'])
    parsed = _parse(body)
    held = conn.execute('SELECT action, note FROM policyHistory WHERE kind = ? AND scope = ? '
                        'AND revision = ?', (KIND, SCOPE, row['revision'])).fetchone()
    parsed.update(revision=row['revision'], lastEditedAt=row['updatedAt'],
                  lastEditedBy=row['updatedBy'], body=_stored(body),
                  action=held['action'] if held else '', note=held['note'] if held else '',
                  source=(held['note'] if held and held['action'] == 'seeded' else ''))
    return parsed


def current() -> dict:
    """The card in force, from memory while the file has not moved."""
    global _cache
    from . import sleeveRepo
    path = sleeveRepo.dbPath()
    stamp = sleeveRepo.fileStamp(path)
    with _lock:
        if stamp is not None and _cache is not None and _cache[0] == stamp:
            return _cache[1]
    found = None
    if stamp is not None:
        conn = sleeveRepo.readOnly(path)
        if conn is not None:
            try:
                found = _readOn(conn)
            except Exception:                  # noqa: BLE001 - read again the full way
                found = None
            finally:
                conn.close()
    if found is None:
        conn = sleeveRepo.connection()
        try:
            stamp = sleeveRepo.fileStamp(path)
            found = _readOn(conn)
        finally:
            conn.close()
    if found is None:
        raise fees.BadRateCard(_seedProblem or 'The fee card has not been loaded.')
    with _lock:
        _cache = (stamp, found)
    return found


def _forget() -> None:
    global _cache
    with _lock:
        _cache = None


def describe() -> dict:
    """What the Fees tab needs: the card in force as the console edits it,
    its version and stamps, the framework's vocabulary, and the products
    each fee group prices - so a group in use is known before a remove is
    asked for."""
    from . import products
    held = current()
    usage = {}
    names = {p['productId']: p['name'] for p in products.all()}
    for group, ids in products.feeGroupUsage().items():
        usage[group] = [{'productId': pid, 'name': names.get(pid, pid)} for pid in ids]
    return {
        'card': held['body'],
        'revision': held['revision'],
        'lastEditedAt': held['lastEditedAt'], 'lastEditedBy': held['lastEditedBy'],
        'action': held['action'], 'note': held['note'],
        'placeholder': held['placeholder'],
        'tiers': [dict(t) for t in held['tiers']],
        'schedules': [{'id': s, 'byGroup': fees.byGroup(s), 'marginal': fees.isMarginal(s),
                       'note': fees.SCHEDULE_NOTES[s]} for s in fees.SCHEDULES],
        'sources': list(fees.SOURCES), 'points': list(fees.POINTS),
        'levels': list(fees.LEVELS),
        # the source no row may leave out (D185)
        'requiredSource': fees.REQUIRED_SOURCE,
        'usage': usage,
        'rateMax': RATE_MAX, 'groupMax': GROUP_MAX, 'noteMax': NOTE_MAX,
    }


# --------------------------------------------------------- the checking ---

def normalise(card, current=None) -> dict:
    """A card as the console sent it - {tiers: [{id, min, max}], feeGroups,
    rates} - as one that may be stored, or a ValidationError naming the
    first thing wrong with it, in the words the Fees tab shows. *current* is
    the stored card in force, whose tier ids are kept when the edges have
    not moved."""
    if not isinstance(card, dict):
        raise ValidationError('card', 'The fee card is not one the tool can read.')
    given = card.get('tiers')
    if not isinstance(given, list) or not given:
        raise ValidationError('tiers', 'The card needs at least one account-size tier.')
    tierList = []
    for t in given:
        if not isinstance(t, dict):
            raise ValidationError('tiers', 'A tier is a range of account sizes.')
        low = _amount(t.get('min'))
        high = None if t.get('max') in (None, '') else _amount(t.get('max'))
        if low is None or (t.get('max') not in (None, '') and high is None):
            raise ValidationError('tiers', 'A tier edge is not an amount.')
        tierList.append({'key': str(t.get('id') or ''), 'min': low, 'max': high})
    tierList.sort(key=lambda t: t['min'])
    if tierList[0]['min'] != 0:
        raise ValidationError('tiers', 'The lowest tier must start at $0, so every account '
                                       'size has a tier.')
    if tierList[-1]['max'] is not None:
        raise ValidationError('tiers', 'The highest tier must have no upper edge: it is the one '
                                       'every larger account falls in.')
    for earlier, later in zip(tierList, tierList[1:]):
        if earlier['max'] is None:
            raise ValidationError('tiers', 'Only the highest tier may have no upper edge.')
        if earlier['max'] <= earlier['min']:
            raise ValidationError('tiers', 'The tier from {} has no room: its upper edge must be '
                                           'above its lower one.'.format(
                                               fees.tierLabel(earlier['min'], earlier['max'])))
        if earlier['max'] != later['min']:
            raise ValidationError('tiers', 'The tiers must meet edge to edge: {} and {} leave a '
                                           'gap or overlap.'.format(
                                               fees.tierLabel(earlier['min'], earlier['max']),
                                               fees.tierLabel(later['min'], later['max'])))
    keys = [t['key'] for t in tierList]
    if not all(keys) or len(set(keys)) != len(keys):
        raise ValidationError('tiers', 'Every tier needs an id of its own.')
    edges = [(t['min'], t['max']) for t in tierList]
    kept = current is not None and edges == [(float(t['min']), None if t['max'] is None
                                             else float(t['max'])) for t in current['tiers']]
    ids = [t['id'] for t in current['tiers']] if kept else ['T{}'.format(i + 1)
                                                         for i in range(len(tierList))]

    groups = card.get('feeGroups')
    if not isinstance(groups, list) or not groups:
        raise ValidationError('feeGroups', 'The card needs at least one fee group.')
    cleanGroups, seen = [], set()
    for name in groups:
        name = _groupName(name)
        if not name:
            raise ValidationError('feeGroups', 'Every fee group needs a name.')
        if len(name) > GROUP_MAX:
            raise ValidationError('feeGroups', 'Keep a fee group name to {} characters.'.format(GROUP_MAX))
        if name.lower() in seen:
            raise ValidationError('feeGroups', 'There are two fee groups called {}.'.format(name))
        seen.add(name.lower())
        cleanGroups.append(name)

    given = card.get('rates')
    if not isinstance(given, dict):
        raise ValidationError('rates', 'The card has no rates.')
    rates = {}
    originals = {_groupName(g): g for g in groups}
    for schedule in fees.SCHEDULES:
        bySchedule = given.get(schedule) if isinstance(given.get(schedule), dict) else {}
        rates[schedule] = {}
        for group in _rowsFor(schedule, cleanGroups):
            byRow = bySchedule.get(originals.get(group, group), bySchedule.get(group))
            byRow = byRow if isinstance(byRow, dict) else {}
            rates[schedule][group] = {}
            # A row may leave out every rate of a source but the required
            # one - a - in each box - and none of a source it gives (D185).
            boxes = {}
            for t in tierList:
                byLevel = byRow.get(t['key'])
                boxes[t['key']] = byLevel if isinstance(byLevel, dict) else {}
            left = set()
            for source in fees.SOURCES:
                values = [boxes[t['key']].get(fees.levelId(source, point))
                          for t in tierList for point in fees.POINTS]
                if source != fees.REQUIRED_SOURCE and all(_noRate(v) for v in values):
                    left.add(source)
            for t, tierId in zip(tierList, ids):
                label = fees.tierLabel(t['min'], t['max'])
                byLevel = boxes[t['key']]
                out = {}
                for level in fees.LEVELS:
                    source = fees.splitLevel(level)[0]
                    if source in left:
                        continue
                    value = _number(byLevel.get(level))
                    if value is None:
                        raise ValidationError('rates', '{} has no rate. {}'.format(
                            _where(schedule, group, label, level),
                            'Every row has its {} rates.'.format(source)
                            if source == fees.REQUIRED_SOURCE else
                            'Give every {0} rate on the row, or none: a - in each {0} box.'.format(
                                source)))
                    if value < 0 or value >= RATE_MAX:
                        raise ValidationError('rates', '{}: {} is not a rate. A rate is a '
                                              'percentage from 0 up to {:g}: 0.45 means 0.45%.'.format(
                                                  _where(schedule, group, label, level),
                                                  _rateText(value), RATE_MAX))
                    out[level] = round(value, 6)
                for source in [x for x in fees.SOURCES if x not in left]:
                    band = [(point, out[fees.levelId(source, point)]) for point in fees.POINTS]
                    for (pa, a), (pb, b) in zip(band, band[1:]):
                        if a > b:
                            raise ValidationError('rates', '{} · {}: {} {} is above {} {}. On '
                                                  'every band the floor is at most the target, '
                                                  'and the target at most the ceiling.'.format(
                                                      _where(schedule, group, label), source,
                                                      pa, _rateText(a), pb, _rateText(b)))
                rates[schedule][group][tierId] = out
    body = {'tiers': [{'id': tierId, 'min': t['min'], 'max': t['max']}
                      for t, tierId in zip(tierList, ids)],
            'feeGroups': cleanGroups, 'rates': rates, 'placeholder': False}
    parsed = _parse(body)
    try:
        fees.checkCard(parsed['tiers'], parsed['feeGroups'], parsed['cells'])
    except (fees.BadRateCard, ValueError) as exc:
        raise ValidationError('rates', str(exc))
    return body


def _renames(renames, before, after) -> list:
    """[[old, new], ...]: groups of the card in force taking new names."""
    out, fromSeen, toSeen = [], set(), set()
    for pair in renames or []:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValidationError('feeGroups', 'A rename names a fee group and its new name.')
        old, new = _groupName(pair[0]), _groupName(pair[1])
        if old == new:
            continue
        if old not in before['feeGroups']:
            raise ValidationError('feeGroups', 'There is no fee group {} to rename.'.format(old))
        if new not in after['feeGroups']:
            raise ValidationError('feeGroups', '{} is renamed to {}, which is not on the card.'.format(old, new))
        if new in before['feeGroups']:
            raise ValidationError('feeGroups', '{} cannot take the name {}: a group on the card has '
                                               'it already.'.format(old, new))
        if old in after['feeGroups']:
            raise ValidationError('feeGroups', '{} is renamed but still on the card.'.format(old))
        if old in fromSeen or new in toSeen:
            raise ValidationError('feeGroups', 'A fee group can be renamed once in a change.')
        fromSeen.add(old)
        toSeen.add(new)
        out.append([old, new])
    return out


def _refuseRemovedInUse(conn, before, after, renames) -> None:
    from . import products
    moved = {old for old, _ in renames}
    gone = [g for g in before['feeGroups'] if g not in after['feeGroups'] and g not in moved]
    if not gone:
        return
    usage = products.feeGroupUsage(conn)
    for group in gone:
        ids = usage.get(group) or []
        if ids:
            names = [r['name'] for r in conn.execute(
                'SELECT name FROM products WHERE productId IN ({}) ORDER BY position'.format(
                    ','.join('?' * len(ids))), ids)]
            raise ValidationError('feeGroups', 'The fee group {} prices {} product{}: {}{}. Move '
                                  '{} to another group first, or rename the group instead of '
                                  'removing it.'.format(group, len(ids), '' if len(ids) == 1 else 's',
                                                        ', '.join(names[:5]),
                                                        ', …' if len(names) > 5 else '',
                                                        'it' if len(ids) == 1 else 'them'))


def _note(note) -> str:
    if note is not None and not isinstance(note, str):
        raise ValidationError('note', 'Say why, in words: every change to the fee card carries a note.')
    note = ' '.join((note or '').split())
    if not note:
        raise ValidationError('note', 'Say why: every change to the fee card carries a note.')
    if len(note) > NOTE_MAX:
        raise ValidationError('note', 'Keep the note to {} characters.'.format(NOTE_MAX))
    return note


def _base(base):
    if base is None:
        return None
    if isinstance(base, str) and base.strip().isdigit():
        base = int(base)
    if isinstance(base, bool) or not isinstance(base, int):
        raise ValidationError('base', 'The version this change was made against is not a number.')
    return base


# ------------------------------------------------------- what changed ---

def _tierLabels(body) -> dict:
    """{range label: tier id} for a stored card, in tier order."""
    return {fees.tierLabel(float(t['min']), None if t['max'] is None else float(t['max'])): t['id']
            for t in body['tiers']}


def _rateMoveLines(before, after) -> list:
    """One line per rate that moved, read across a rename and matched by the
    tier's range, so a renumbered tier is compared with itself."""
    renames = {old: new for old, new in after.get('renames') or []}
    oldTiers, newTiers = _tierLabels(before), _tierLabels(after)
    moved = []
    for schedule in fees.SCHEDULES:
        for group in _rowsFor(schedule, before['feeGroups']):
            target = renames.get(group, group)
            if fees.byGroup(schedule) and target not in after['feeGroups']:
                continue
            for label in [l for l in newTiers if l in oldTiers]:
                a = before['rates'].get(schedule, {}).get(group, {}).get(oldTiers[label], {})
                b = after['rates'].get(schedule, {}).get(target, {}).get(newTiers[label], {})
                for level in fees.LEVELS:
                    # a rate given or taken away reads from or to a - (D185)
                    was, now = a.get(level), b.get(level)
                    if was is None and now is None:
                        continue
                    if was is None or now is None or abs(float(was) - float(now)) > 1e-9:
                        moved.append('{} {} → {}'.format(
                            _where(schedule, target, label, level), _rateText(was),
                            _rateText(now)))
    return moved


def describeChange(before, after) -> list:
    """What moved from one stored card to the next, as the lines the history
    and the review show: the tiers, the groups added, removed and renamed,
    then rate by rate - by the tiers' ranges, which survive a renumbering -
    with the count of the rest when there are many."""
    if before is None:
        return []
    out = []
    was, now = list(_tierLabels(before)), list(_tierLabels(after))
    if was != now:
        out.append('Tiers: {} → {}'.format(' · '.join(was), ' · '.join(now)))
    renames = {old: new for old, new in after.get('renames') or []}
    for old, new in renames.items():
        out.append('Renamed fee group {} to {}'.format(old, new))
    for group in after['feeGroups']:
        if group not in before['feeGroups'] and group not in renames.values():
            out.append('Added fee group {}'.format(group))
    for group in before['feeGroups']:
        if group not in after['feeGroups'] and group not in renames:
            out.append('Removed fee group {}'.format(group))
    moved = _rateMoveLines(before, after)
    out.extend(moved[:_LIST_RATES])
    if len(moved) > _LIST_RATES:
        out.append('and {} more rate{}'.format(len(moved) - _LIST_RATES,
                                              '' if len(moved) - _LIST_RATES == 1 else 's'))
    return out


def _rateMoves(before, after) -> int:
    return len(_rateMoveLines(before, after))


def _sameCard(a, b) -> bool:
    """Whether two cards price alike: the same tiers, groups and rates,
    whatever their placeholder flags say."""
    keep = ('tiers', 'feeGroups', 'rates')
    return {k: a.get(k) for k in keep} == {k: b.get(k) for k in keep}


# --------------------------------------------------------------- writes ---

def _writing(work):
    from . import products, sleeveRepo
    conn = sleeveRepo.connection()
    try:
        if _rowOn(conn) is None:
            raise fees.BadRateCard(_seedProblem or 'The fee card has not been loaded.')
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
        products.reload()


def _refuseStale(conn, base) -> None:
    if base is None:
        return
    row = _rowOn(conn)
    if row['revision'] != base:
        raise StaleError('base', 'The fee card was changed by {} at {} since you opened it (now '
                         'version {}). Reload to see the change, then make yours again.'.format(
                             row['updatedBy'] or 'someone',
                             (row['updatedAt'] or '').replace('T', ' ')[:16], row['revision']))


def _write(conn, body, action, note, user) -> dict:
    row = _rowOn(conn)
    revision = row['revision'] + 1
    stamp = _now()
    text = json.dumps(body)
    conn.execute('INSERT OR REPLACE INTO policies (kind, scope, body, revision, updatedBy, '
                 'updatedAt) VALUES (?, ?, ?, ?, ?, ?)',
                 (KIND, SCOPE, json.dumps(_stored(body)), revision, user or '', stamp))
    conn.execute('INSERT INTO policyHistory (kind, scope, revision, action, body, note, actor, at) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                 (KIND, SCOPE, revision, action, text, note, user or '', stamp))
    return {'revision': revision, 'at': stamp}


def _save(conn, body, renames, action, note, user):
    """Put *body* in force with its renames, after the checks every way in
    shares. Returns the new version's stamp and what it changed."""
    from . import products
    before = json.loads(_rowOn(conn)['body'])
    renames = _renames(renames, before, body)
    _refuseRemovedInUse(conn, before, body, renames)
    if _sameCard(body, before) and not renames:
        raise ValidationError('card', 'Nothing has changed: this is the card in force.')
    if renames:
        body = dict(body, renames=renames)
    written = _write(conn, body, action, note, user)
    moved = 0
    for old, new in renames:
        moved += products.renameFeeGroup(
            conn, old, new, user, written['at'],
            'Fee group renamed from {} to {} (fee card version {}): {}'.format(
                old, new, written['revision'], note))
    return dict(written, changes=describeChange(before, body), productsMoved=moved)


def save(card, note, user='', base=None, renames=None) -> dict:
    """Put a new card in force from the Fees tab. Refused, naming the field,
    when the card is not one the framework can price from, a group in use
    would be removed, the note is missing, nothing would change, or - as
    STALE - *base* is not the version in force."""
    note = _note(note)
    base = _base(base)

    def work(conn):
        _refuseStale(conn, base)
        body = normalise(card, json.loads(_rowOn(conn)['body']))
        return _save(conn, body, renames, 'updated', note, user)

    return _writing(work)


def revert(revision, note, user='', base=None) -> dict:
    """Put an earlier version back in force, as a new version - checked
    again, since a product may have come to need a group it lacks."""
    note = _note(note)
    base = _base(base)
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValidationError('revision', 'Choose a version to restore.')

    def work(conn):
        _refuseStale(conn, base)
        held = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? AND '
                            'revision = ?', (KIND, SCOPE, revision)).fetchone()
        if held is None:
            raise ValidationError('revision', 'There is no version {} of the fee card.'.format(revision))
        old = json.loads(held['body'])
        current = json.loads(_rowOn(conn)['body'])
        body = normalise(_stored(old), current)
        body['placeholder'] = bool(old.get('placeholder'))
        return _save(conn, body, None, 'reverted', note, user)

    return _writing(work)


def history() -> list:
    """Every version of the card, newest first: who, when, why, and what
    moved from the version before it."""
    from . import sleeveRepo
    conn = sleeveRepo.connection()
    try:
        rows = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? ORDER BY '
                            'revision', (KIND, SCOPE)).fetchall()
    finally:
        conn.close()
    out, previous, bodies = [], None, []
    top = rows[-1]['revision'] if rows else None
    for r in rows:
        body = json.loads(r['body'])
        restoredFrom = None
        if r['action'] == 'reverted':
            restoredFrom = next((n for n, b in reversed(bodies) if _stored(b) == _stored(body)), None)
        out.append({'revision': r['revision'], 'action': r['action'], 'actor': r['actor'],
                    'at': r['at'], 'note': r['note'], 'changes': describeChange(previous, body),
                    'placeholder': bool(body.get('placeholder')), 'restoredFrom': restoredFrom,
                    'current': r['revision'] == top,
                    'tiers': len(body['tiers']), 'feeGroups': list(body['feeGroups'])})
        previous = body
        bodies.append((r['revision'], body))
    out.reverse()
    return out


def feedRows(conn) -> list:
    """Every version as the record's feed reads it, oldest first."""
    rows = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? ORDER BY '
                        'revision', (KIND, SCOPE)).fetchall()
    out, previous = [], None
    for r in rows:
        body = json.loads(r['body'])
        out.append({'id': r['id'], 'revision': r['revision'], 'action': r['action'],
                    'actor': r['actor'], 'at': r['at'], 'note': r['note'],
                    'changes': describeChange(previous, body)})
        previous = body
    return out


# ------------------------------------------------------- import / export ---

def _uploaded(name, content):
    import base64
    import binascii
    name = os.path.basename(str(name or '').replace('\\', '/')).strip()
    if not name.lower().endswith('.csv'):
        raise ValidationError('file', 'A fee card file is a .csv, one row per rate.')
    if not isinstance(content, str) or not content:
        raise ValidationError('file', 'Choose a fee card file to import.')
    try:
        data = base64.b64decode(content.split(',', 1)[-1] if content.startswith('data:') else content)
    except (binascii.Error, ValueError):
        raise ValidationError('file', 'The file could not be read.')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = data.decode('cp1252')
    return name, text


def _bodyFromFile(name, text) -> dict:
    try:
        delivered = fees._readDelivery(name, text=text)
        body = fromDelivery(delivered)
        parsed = _parse(body)
        fees.checkCard(parsed['tiers'], parsed['feeGroups'], parsed['cells'])
    except (fees.BadRateCard, ValueError, KeyError) as exc:
        raise ValidationError('file', '{}: {}'.format(name, exc))
    return body


def importPreview(name, content) -> dict:
    """What a fee card file would change, before anything does: the same
    lines the history shows, the groups it would take away that products
    still use, and the version it was compared with."""
    from . import products, sleeveRepo
    name, text = _uploaded(name, content)
    incoming = _bodyFromFile(name, text)
    conn = sleeveRepo.connection()
    try:
        row = _rowOn(conn)
        before = json.loads(row['body'])
        body = normalise(_stored(incoming), before)
        usage = products.feeGroupUsage(conn)
        blocked = [{'feeGroup': g, 'products': len(usage.get(g) or [])}
                   for g in before['feeGroups'] if g not in body['feeGroups'] and usage.get(g)]
        changed = describeChange(before, body)
        return {'file': name, 'rows': sum(len(t) for byRow in body['rates'].values()
                                          for byTier in byRow.values() for t in byTier.values()),
                'changes': changed, 'rates': _rateMoves(before, body), 'blocked': blocked,
                'same': _sameCard(body, before),
                'revision': row['revision']}
    finally:
        conn.close()


def importFile(name, content, note, user='', base=None) -> dict:
    """Put a fee card file in force as a new version marked *imported*."""
    note = _note(note)
    base = _base(base)
    name, text = _uploaded(name, content)
    incoming = _bodyFromFile(name, text)

    def work(conn):
        _refuseStale(conn, base)
        body = normalise(_stored(incoming), json.loads(_rowOn(conn)['body']))
        return _save(conn, body, None, 'imported', '{} (from {})'.format(note, name), user)

    return _writing(work)


def exportRows() -> list:
    """The card in force as the long file it can be loaded from again: one
    row per rate, every coordinate stated."""
    held = current()
    out = [['schedule', 'feeGroup', 'tier', 'tierMin', 'tierMax', 'source', 'point', 'rate']]

    def edge(v):
        return '' if v is None else str(int(v)) if float(v).is_integer() else repr(float(v))

    for schedule in fees.SCHEDULES:
        for group in ([None] if not fees.byGroup(schedule) else held['feeGroups']):
            for tier in held['tiers']:
                for source in fees.SOURCES:
                    for point in fees.POINTS:
                        # a - where the row leaves the source out (D185)
                        rate = held['cells'].get((schedule, group, tier['id'], source, point))
                        out.append([schedule, group or '', tier['id'], edge(tier['min']),
                                    edge(tier['max']), source, point,
                                    fees.NO_RATE if rate is None else '{:g}'.format(rate)])
    return out


def exportCsv() -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows(exportRows())
    return buf.getvalue()
