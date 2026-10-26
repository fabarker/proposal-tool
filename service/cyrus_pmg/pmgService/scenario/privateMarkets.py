"""The private markets minimums: the mandate a private markets product
needs, by allocation type, risk level and base currency (D200).

There are two sets of minimums (D217). APS's are for the one product
Alternatives Portfolio Services; Other's are for every other product in
Private Equity & Other Private Assets - a product held in that category, or
one the catalogue puts in it. A product in no other category is not checked.

A ROW of the table reads: under this allocation type, at this risk level and
in this base currency, APS (or any other product) needs a mandate of at
least this many millions of dollars - dollars whatever the currency, as
every amount in the tool is. The allocation types are the four that hold private markets: Full,
ex-HFs, and each of them with real assets excluded, which the desk's file
calls ex-RA and ex-HFs ex-RA. Core, ex-Alts and an all-equity level hold no
private markets, so the table never gates them.

The table is read twice, against the proposal's mandate:

* choosing the portfolio - an allocation that holds private markets is
  offered at a risk level and in a currency only where the mandate reaches
  the LOWER of APS's and Other's minimums there; below it the page shows the
  allocation greyed out with the mandate it needs;
* the implementation table - each private markets product is held only where
  the mandate reaches its set's minimum; below it the line is marked and the
  download refused, as for a position below its product's minimum. A custom
  portfolio is checked the same way, product by product, against the Full
  minimums at its risk profile.

Each set is meant to have a minimum for every one of the 112 cells (4
allocation types x 7 risk levels x 4 currencies). A cell left without one is
a GAP: the console lists every gap, and a proposal never reads a gap as no
minimum - a product with none where the proposal needs one is refused, and
an allocation neither set has a minimum for is not offered.

The admin keeps the table in the console's PM Minimums tab, a tab a set:
loaded whole from the desk's Excel file (long form: allocation_type,
risk_level, currency, value, Type, productId - All in the first or the third
means every one, and the productId the APS product's or Other), or edited a
value at a time on screen. Every change is a revision with who,
when, an optional note and what moved, and an earlier revision can be put
back.

Storage. The ``policies`` and ``policyHistory`` tables (sleeveRepo, schema 4)
under the kind 'privateMarkets' and the one scope '*', as {aps: {cell: $m},
other: {cell: $m}}. A revision from before the two sets, a list of products,
reads as APS for the APS product and, cell by cell, the highest of the other
products' as Other. Until the first save there is no table, and the one rule
before it stands: no private markets below a $20m mandate. A read never
writes.
"""

from __future__ import annotations

import datetime
import io
import json
import os
import threading

from . import saaKeys, sleeveRepo
from .types import PortfolioKey, ValidationError

KIND = 'privateMarkets'
SCOPE = '*'
#: the rule before the first table, and while a table holds no product
FALLBACK_MINIMUM = 20_000_000
MILLION = 1_000_000
NOTE_MAX = 240
VALUE_MAX = 100_000              # $m: a typing slip, not a minimum
#: the product APS's minimums are for; every other private markets product
#: takes Other's (D217)
APS_PRODUCT = 'gs-alternatives-portfolio-services'
#: the two sets, in the console's order, with their tab labels and names
SETS = ('aps', 'other')
LABELS = {'aps': 'APS', 'other': 'Other'}
NAMES = {'aps': 'Alternatives Portfolio Services',
         'other': 'Private Equity Managers (PEM)'}
#: what the file's productId says for Other
OTHER_ID = 'Other'
#: changes listed for one revision before the rest are counted
CHANGES_SHOWN = 40

#: the allocation types that hold private markets, in the file's words, and
#: what each is in a portfolio key: (allocation type, real assets excluded)
ALLOCATIONS = ('Full', 'ex-HFs', 'ex-RA', 'ex-HFs ex-RA')
_KEY_OF = {'Full': ('Full', False), 'ex-HFs': ('ex-HFs', False),
           'ex-RA': ('Full', True), 'ex-HFs ex-RA': ('ex-HFs', True)}
_ALLOCATION_OF = {v: k for k, v in _KEY_OF.items()}
#: the risk levels, least to most risky, all equity aside
RISKS = tuple(r for r in saaKeys.RISK_LEVELS if r != 'All Equity')
CURRENCIES = saaKeys.CURRENCIES
#: the file's own columns, matched by name whatever the case or spacing
COLUMNS = ('allocation_type', 'risk_level', 'currency', 'value', 'Type', 'productId')
_REQUIRED = ('allocationtype', 'risklevel', 'currency', 'value', 'productid')
ALL = 'All'


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def _squash(text) -> str:
    return ''.join(ch for ch in str(text or '').lower() if ch.isalnum())


def riskLabel(risk) -> str:
    from .rules import RISK_LEVEL_LABELS
    return RISK_LEVEL_LABELS.get(risk, risk)


def cell(allocation, risk, currency) -> str:
    return '{}|{}|{}'.format(allocation, risk, currency)


def cells() -> list:
    """Every cell a product needs a minimum for, in the console's order."""
    return [cell(a, r, c) for a in ALLOCATIONS for r in RISKS for c in CURRENCIES]


def cellWords(key) -> str:
    a, r, c = key.split('|')
    return '{} · {} · {}'.format(a, riskLabel(r), c)


def cellOf(key):
    """The cell a portfolio key reads, or None for one that holds no private
    markets (Core, ex-Alts, all equity)."""
    if isinstance(key, str):
        key = PortfolioKey.fromStr(key)
    if key.isAllEquity:
        return None
    allocation = _ALLOCATION_OF.get((key.allocationType, bool(key.excludeRealAssets)))
    if allocation is None or key.riskLevel not in RISKS:
        return None
    return cell(allocation, key.riskLevel, key.currency)


def keyStrOf(cellKey) -> str:
    a, r, c = cellKey.split('|')
    allocationType, exclude = _KEY_OF[a]
    return '|'.join([c, r, allocationType, '1' if exclude else '0'])


def customCell(currency, profile):
    """The cell a custom portfolio's products are checked against: Full, at
    its risk profile, in its currency."""
    return cell('Full', profile, currency) if profile in RISKS and currency in CURRENCIES else None


# --------------------------------------------------------------- storage ---

def _row(conn):
    return conn.execute('SELECT * FROM policies WHERE kind = ? AND scope = ?',
                        (KIND, SCOPE)).fetchone()


def _ordered(minimums) -> dict:
    return {c: minimums[c] for c in cells() if minimums.get(c) is not None}


def _fromProducts(listed) -> dict:
    """A table stored as a list of products, before the two sets: APS the APS
    product's, Other the highest of every other product's in each cell, so
    none is held below the minimum it had."""
    aps, other = {}, {}
    for p in listed:
        minimums = (p or {}).get('minimums') or {}
        if (p or {}).get('productId') == APS_PRODUCT:
            aps = dict(minimums)
            continue
        for c, v in minimums.items():
            if v is not None and (other.get(c) is None or v > other[c]):
                other[c] = v
    return {'aps': _ordered(aps), 'other': _ordered(other)}


def _parse(text) -> dict:
    """A stored body as {aps: {cell: $m}, other: {cell: $m}}."""
    body = json.loads(text) if isinstance(text, str) else (text or {})
    if isinstance(body.get('products'), list):
        return _fromProducts(body['products'])
    return {s: dict(body.get(s) or {}) for s in SETS}


def _holds(table) -> bool:
    """Whether a table holds any minimum at all."""
    return any(table.get(s) for s in SETS)


_lock = threading.Lock()
_cache = None            # (file stamp, the row as read, or None for none)


def _stored(path):
    """The row in force, read through a plain read-only connection and kept
    against the file's stamp: the schema reads the table on every change a
    PWA makes."""
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
    """The table in force: {aps, other, revision, inForce, updatedBy,
    updatedAt}, a fresh copy on every call. Revision 0 is no table: the $20m
    rule stands. A table that holds no minimum leaves it standing too."""
    path = sleeveRepo.dbPath()
    row = _stored(path) if os.path.exists(path) else None
    if row is None:
        return {'aps': {}, 'other': {}, 'revision': 0, 'inForce': False, 'updatedBy': '', 'updatedAt': ''}
    body = _parse(row['body'])
    return dict(body, revision=row['revision'], inForce=_holds(body),
                updatedBy=row['updatedBy'], updatedAt=row['updatedAt'])


def gaps(table=None) -> list:
    """Every cell a set has no minimum for, once the table holds any:
    [{set, label, cells: [cell, ...]}], a set with none missing left out."""
    table = current() if table is None else table
    if not _holds(table):
        return []
    out = []
    for s in SETS:
        missing = [c for c in cells() if table[s].get(c) is None]
        if missing:
            out.append({'set': s, 'label': LABELS[s], 'cells': missing})
    return out


def privateCategories() -> list:
    """The categories whose products take Other's minimums: Private Equity &
    Other Private Assets, and the two it stands for."""
    from .rules import PRIVATE_FUNDED_FROM, SLEEVE_GROUPS
    members = [c for g in SLEEVE_GROUPS if g['name'] == PRIVATE_FUNDED_FROM for c in g['categories']]
    return [PRIVATE_FUNDED_FROM] + members


def setOf(productId, category='', ownCategory='') -> str:
    """Which minimums a product is held to: APS's for the APS product,
    Other's for any other in Private Equity & Other Private Assets - the
    line's category or the product's own - and none ('') for the rest."""
    if not productId:
        return ''
    if productId == APS_PRODUCT:
        return 'aps'
    private = privateCategories()
    return 'other' if (category in private or ownCategory in private) else ''


def _ownCategory(productId) -> str:
    """The category the catalogue puts a product in, blank where none."""
    from . import products
    try:
        found = products.get(productId) or {}
    except products.BadCatalogue:
        return ''
    return found.get('ownCategory') or found.get('category') or ''


# ------------------------------------------------------------ the rules ---

def _dollars(value):
    return None if value is None else float(value) * MILLION


def gate(key, table=None):
    """What an allocation needs at this key: {minimum, gap} - the lower of
    APS's and Other's minimums, in dollars, at the key's cell, and whether
    neither has one there. None for a key that holds no private
    markets. Before the first table, $20m for every key that holds them."""
    from .rules import PRIVATE_ASSET_ALLOCATIONS
    if isinstance(key, str):
        key = PortfolioKey.fromStr(key)
    if key.isAllEquity or key.allocationType not in PRIVATE_ASSET_ALLOCATIONS:
        return None
    table = current() if table is None else table
    if not table['inForce']:
        return {'minimum': float(FALLBACK_MINIMUM), 'gap': False}
    at = cellOf(key)
    values = [table[s].get(at) for s in SETS] if at else []
    values = [v for v in values if v is not None]
    if not values:
        return {'minimum': None, 'gap': True}
    return {'minimum': _dollars(min(values)), 'gap': False}


def opens(key, mandateSize, table=None) -> bool:
    """Whether the mandate opens this key's allocation. A missing mandate
    applies no rule, since a rule cannot bind before its input exists."""
    need = gate(key, table)
    if need is None or mandateSize is None:
        return True
    if need['gap']:
        return False
    return float(mandateSize) >= need['minimum'] - 1e-6


def closedWords(key, table=None) -> str:
    """Why a key's allocation is not offered at this mandate, in the rail's words."""
    if isinstance(key, str):
        key = PortfolioKey.fromStr(key)
    need = gate(key, table)
    if need is None:
        return ''
    where = '{} {}'.format(key.currency, riskLabel(key.riskLevel))
    if need['gap']:
        return 'No private markets minimum is set for {} {}{} yet.'.format(
            where, key.allocationType, ' ex-RAs' if key.excludeRealAssets else '')
    return '{}{} needs a mandate of ${:,.0f} in {}.'.format(
        key.allocationType, ' ex-RAs' if key.excludeRealAssets else '', need['minimum'], where)


def served(currency) -> dict:
    """What the page reads for one base currency: whether a table is in
    force, each private key's gate, each set's minimum by key, in dollars
    (None for a gap), the APS product and the categories Other covers."""
    table = current()
    keys = [keyStrOf(cell(a, r, currency)) for a in ALLOCATIONS for r in RISKS] \
        if currency in CURRENCIES else []
    gates = {}
    for k in keys:
        need = gate(k, table)
        gates[k] = None if need is None or need['gap'] else need['minimum']
    minimums = {}
    if table['inForce']:
        for s in SETS:
            minimums[s] = {k: _dollars(table[s].get(cellOf(k))) for k in keys}
    return {'inForce': table['inForce'], 'revision': table['revision'], 'gates': gates,
            'minimums': minimums, 'apsProduct': APS_PRODUCT, 'categories': privateCategories(),
            'fallback': FALLBACK_MINIMUM}


def shortfalls(groups, cellKey, mandateSize, table=None) -> list:
    """Every line of an implemented model whose product is APS or another
    private markets product and whose set's minimum at *cellKey* the mandate
    does not reach, or which has no minimum there: [{category, name,
    productId, minimum, gap}]. A line the book holds no weight in, long term
    or initially, is not a position."""
    table = current() if table is None else table
    if not table['inForce'] or not cellKey or mandateSize is None:
        return []
    out = []
    for group in groups or []:
        for item in group.get('items') or []:
            pid = item.get('productId')
            if not pid or not (item.get('printedPct') or item.get('initialPct') or item.get('weightPct')):
                continue
            which = setOf(pid, group.get('category') or '') or setOf(pid, ownCategory=_ownCategory(pid))
            if not which:
                continue
            value = table[which].get(cellKey)
            if value is None:
                out.append({'category': group.get('category'), 'name': item.get('name'),
                            'productId': pid, 'minimum': None, 'gap': True})
            elif float(mandateSize) < _dollars(value) - 1e-6:
                out.append({'category': group.get('category'), 'name': item.get('name'),
                            'productId': pid, 'minimum': _dollars(value), 'gap': False})
    return out


def shortfallText(found, cellKey, mandateSize) -> str:
    """Why a proposal holding *found* is not delivered."""
    def one(s):
        if s['gap']:
            return '{} in {} (no minimum is set for {})'.format(s['name'], s['category'],
                                                                 cellWords(cellKey))
        return '{} in {} (needs a ${:,.0f} mandate)'.format(s['name'], s['category'], s['minimum'])
    return ('Below the private markets minimum for {} at a ${:,.0f} mandate: {}. Raise the '
            'mandate or change the sleeve before exporting.'.format(
                cellWords(cellKey), float(mandateSize or 0),
                ', '.join(one(s) for s in found[:4])
                + ('' if len(found) <= 4 else ', and {} more'.format(len(found) - 4))))


# ------------------------------------------------------------- the table ---

def _value(raw, where):
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        raise ValidationError('value', '{}: the value is a number of millions of dollars.'.format(where))
    try:
        value = float(str(raw).replace(',', '').replace('$', '').strip()) \
            if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError):
        raise ValidationError('value', '{}: "{}" is not a number of millions of dollars.'.format(where, raw))
    if value != value or value < 0 or value > VALUE_MAX:
        raise ValidationError('value', '{}: a minimum is between 0 and {:,} million dollars.'.format(
            where, VALUE_MAX))
    return round(value, 4)


def _setNamed(productId, typ):
    """The set a file's row is for, by its productId - the APS product's, or
    Other - or, where it gives none, by its Type; None for anything else."""
    said = _squash(productId)
    if not said:
        said = _squash(typ)
        return {'aps': 'aps', _squash(OTHER_ID): 'other'}.get(said)
    if said == _squash(APS_PRODUCT):
        return 'aps'
    return 'other' if said == _squash(OTHER_ID) else None


def normalise(body) -> dict:
    """A table from the console: {aps: {cell: $m or None}, other: {...}}. A
    blank value is a gap. Refused, naming the field, when it is not a
    table."""
    if not isinstance(body, dict) or any(not isinstance(body.get(s), dict) for s in SETS):
        raise ValidationError('minimums', 'The minimums are APS\'s and Other\'s, each by cell.')
    known, out = set(cells()), {}
    for s in SETS:
        minimums = {}
        for key, value in body[s].items():
            if key not in known:
                raise ValidationError('minimums', '{}: "{}" is not a cell of the table.'.format(LABELS[s], key))
            value = _value(value, '{} {}'.format(LABELS[s], cellWords(key)))
            if value is not None:
                minimums[key] = value
        out[s] = _ordered(minimums)
    return out


# -------------------------------------------------------------- the file ---

def _readSheets(name, data):
    """(header, rows) of the sheet in the file that holds the long-form
    table: a CSV's only sheet, or the first sheet of a workbook whose header
    names every required column ('Long Form' in the desk's workbook)."""
    if name.lower().endswith('.csv'):
        import csv
        from .csvText import unwrapRows
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = data.decode('cp1252')
        rows = list(csv.reader(unwrapRows(io.StringIO(text, newline=''))))
        return (rows[0] if rows else []), rows[1:]
    from openpyxl import load_workbook
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:                          # noqa: BLE001 - not a workbook
        raise ValidationError('file', '{} could not be read as a workbook.'.format(name))
    for sheet in book.worksheets:
        rows = sheet.iter_rows(values_only=True)
        head = next(rows, None) or []
        heads = [_squash(h) for h in head]
        if all(r in heads for r in _REQUIRED):
            return ([('' if h is None else str(h)) for h in head],
                    [['' if v is None else v for v in r] for r in rows])
    raise ValidationError('file', 'No sheet in {} has the columns {}.'.format(name, ', '.join(COLUMNS)))


def _fileName(name) -> str:
    name = os.path.basename(str(name or '').replace('\\', '/')).strip()
    if not name.lower().endswith(('.csv', '.xlsx')):
        raise ValidationError('file', 'A minimums file is a .csv or an .xlsx.')
    return name


def _choice(raw, options, labels=None):
    """The option *raw* names, by its name or its label, or ALL, or None."""
    said = _squash(raw)
    if said == 'all':
        return ALL
    for o in options:
        if said == _squash(o) or (labels and said == _squash(labels.get(o, ''))):
            return o
    if said.endswith('ras'):                    # 'ex-HFs ex-RAs', as the tool spells it
        return _choice(str(raw)[:-1], options, labels)
    return None


def readFile(name, content) -> dict:
    """The table a file holds, with what is wrong with it: {table, rows,
    problems: [words], gaps}. A row is APS's where its productId is the APS
    product's, Other's where it is Other (or, with none, where its Type is
    APS or Other). A row's All means every allocation type, risk level or
    currency; where two rows give a set's cell, the one naming more wins,
    and two that name as much and disagree are a problem."""
    from . import products
    from .rules import RISK_LEVEL_LABELS
    name = _fileName(name)
    data = products._decode(content)
    head, rows = _readSheets(name, data)
    at = {}
    for i, h in enumerate(head):
        at.setdefault(_squash(h), i)
    missing = [c for c, s in zip(COLUMNS, ('allocationtype', 'risklevel', 'currency', 'value', 'type',
                                           'productid')) if s in _REQUIRED and s not in at]
    if missing:
        raise ValidationError('file', '{}: missing column(s) {}.'.format(name, ', '.join(missing)))

    def field(row, key):
        i = at.get(key)
        return row[i] if i is not None and i < len(row) else ''

    problems, claimed = [], {}
    count = 0
    for n, row in enumerate(rows, start=2):
        if not any(str(v).strip() for v in row):
            continue
        count += 1
        where = 'Row {}'.format(n)
        pid = ' '.join(str(field(row, 'productid') or '').split())
        which = _setNamed(pid, field(row, 'type'))
        if which is None:
            problems.append('{}: {} - the minimums are APS\'s ({}) and Other\'s, for every other '
                            'product: name one of them.'.format(
                                where, '"{}" is neither'.format(pid) if pid else 'no productId', APS_PRODUCT))
            continue
        try:
            value = _value(field(row, 'value'), where)
        except ValidationError as exc:
            problems.append(exc.message)
            continue
        alloc = _choice(field(row, 'allocationtype'), ALLOCATIONS)
        risk = _choice(field(row, 'risklevel'), RISKS, RISK_LEVEL_LABELS)
        cur = _choice(field(row, 'currency'), CURRENCIES)
        bad = [(c, field(row, k)) for c, k, v in (('allocation_type', 'allocationtype', alloc),
                                                 ('risk_level', 'risklevel', risk),
                                                 ('currency', 'currency', cur)) if v is None]
        if bad:
            problems.append('{}: {}.'.format(where, '; '.join(
                '{} "{}" is not one the table takes'.format(c, v) for c, v in bad)))
            continue
        if value is None:
            problems.append('{}: no value.'.format(where))
            continue
        rank = sum(1 for v in (alloc, risk, cur) if v != ALL)
        for a in ALLOCATIONS if alloc == ALL else [alloc]:
            for r in RISKS if risk == ALL else [risk]:
                for c in CURRENCIES if cur == ALL else [cur]:
                    key = (which, cell(a, r, c))
                    had = claimed.get(key)
                    if had is None or rank > had[0]:
                        claimed[key] = (rank, value, n)
                    elif rank == had[0] and value != had[1]:
                        problems.append('Rows {} and {} both give {} {}: {:g} and {:g}.'.format(
                            had[2], n, LABELS[which], cellWords(key[1]), had[1], value))
    if count == 0:
        problems.append('{} holds no rows.'.format(name))
    table = {s: {c: claimed[(s, c)][1] for c in cells() if (s, c) in claimed} for s in SETS}
    try:
        apsKnown = products.has(APS_PRODUCT)
    except products.BadCatalogue:
        apsKnown = True
    return {'name': name, 'table': table, 'rows': count, 'problems': problems,
            'unknown': [] if apsKnown or not table['aps'] else [APS_PRODUCT], 'gaps': gaps(table)}


def _changes(before, after) -> list:
    """What moved between two tables, in the words the console shows: a set
    filled from nothing or emptied in one line, else each value."""
    out = []
    for s in SETS:
        was, now = (before or {}).get(s) or {}, after.get(s) or {}
        if not was and now:
            out.append('{}: {} values set'.format(LABELS[s], len(now)))
            continue
        if was and not now:
            out.append('{}: its {} values cleared'.format(LABELS[s], len(was)))
            continue
        for c in cells():
            a, b = was.get(c), now.get(c)
            if a != b:
                out.append('{} · {}: {} → {}'.format(LABELS[s], cellWords(c),
                                                     '—' if a is None else '{:g}'.format(a),
                                                     '—' if b is None else '{:g}'.format(b)))
    return out


def _summary(changes) -> list:
    if len(changes) <= CHANGES_SHOWN:
        return changes
    return changes[:CHANGES_SHOWN] + ['and {} more'.format(len(changes) - CHANGES_SHOWN)]


def preview(name, content) -> dict:
    """What a file would do, before anything moves."""
    read = readFile(name, content)
    now = current()
    changes = _changes(now, read['table'])
    return {'name': read['name'], 'rows': read['rows'], 'problems': read['problems'],
            'unknown': read['unknown'], 'gaps': read['gaps'],
            'sets': [{'set': s, 'label': LABELS[s], 'values': len(read['table'][s])} for s in SETS],
            'changes': _summary(changes), 'changed': len(changes), 'base': now['revision']}


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
            'base', 'The minimums were changed by {} since you opened them. Reload to see the '
            'change.'.format((row['updatedBy'] if row is not None else '') or 'someone'))


def _previous(conn) -> dict:
    existing = _row(conn)
    return _parse(existing['body']) if existing is not None else {s: {} for s in SETS}


def _put(cleaned, action, note, user, base) -> dict:
    conn = sleeveRepo.connection()
    try:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        try:
            _refuseStale(conn, base)
            if cleaned == _previous(conn):
                raise ValidationError('minimums', 'Those are the minimums already in force.')
            _write(conn, cleaned, action, note, user)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return current()
    finally:
        conn.close()


def save(body, note='', user='', base=None) -> dict:
    """Put a table edited on screen in force. Refused, naming the field, when
    it is not a table, someone saved in between (*base*) or nothing would
    change."""
    return _put(normalise(body), 'updated', _note(note), user, base)


def importFile(name, content, note='', user='', base=None) -> dict:
    """Put a file's table in force, whole: a value the file leaves out is
    gone, and stays in the history. Refused while the file has a problem."""
    note = _note(note)
    read = readFile(name, content)
    if read['problems']:
        raise ValidationError('file', '{}: {}'.format(read['name'], ' '.join(read['problems'][:6])
                                                       + ('' if len(read['problems']) <= 6 else
                                                          ' And {} more.'.format(len(read['problems']) - 6))))
    return _put(normalise(read['table']), 'imported', note or 'Loaded {}'.format(read['name']), user, base)


def history() -> list:
    """Every revision, newest first, each with what it changed."""
    conn = sleeveRepo.connection()
    try:
        rows = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? '
                            'ORDER BY revision', (KIND, SCOPE)).fetchall()
    finally:
        conn.close()
    out, previous = [], {s: {} for s in SETS}
    top = rows[-1]['revision'] if rows else None
    for r in rows:
        body = _parse(r['body'])
        changes = _changes(previous, body)
        out.append({'revision': r['revision'], 'action': r['action'], 'note': r['note'],
                    'actor': r['actor'], 'at': r['at'], 'id': r['id'], 'changes': _summary(changes),
                    'changed': len(changes), 'values': sum(len(body[s]) for s in SETS),
                    'current': r['revision'] == top})
        previous = body
    return list(reversed(out))


def revert(revision, note='', user='', base=None) -> dict:
    """Put an earlier revision's table back in force, as a new revision."""
    note = _note(note)
    try:
        number = int(revision)
    except (TypeError, ValueError):
        raise ValidationError('revision', 'Name the revision by its number.')
    conn = sleeveRepo.connection()
    try:
        row = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? AND '
                           'revision = ?', (KIND, SCOPE, number)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValidationError('revision', 'There is no revision {} of the minimums.'.format(number))
    return _put(normalise(_parse(row['body'])), 'reverted',
                note or 'Restored revision {}'.format(number), user, base)


def exportRows() -> list:
    """The table in force as the desk's long-form file: the header, then a
    row a set and cell, gaps left out - APS's under the APS product, Other's
    under Other."""
    out = [list(COLUMNS)]
    table = current()
    for s in SETS:
        for a in ALLOCATIONS:
            for r in RISKS:
                for c in sorted(CURRENCIES):
                    v = table[s].get(cell(a, r, c))
                    if v is not None:
                        out.append([a, riskLabel(r), c, v, LABELS[s], APS_PRODUCT if s == 'aps' else OTHER_ID])
    return out


def exportWorkbook() -> bytes:
    """``exportRows`` as an .xlsx with one sheet, Long Form, ready to edit
    and load again."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    book = Workbook()
    sheet = book.active
    sheet.title = 'Long Form'
    for row in exportRows():
        sheet.append(row)
    for c in sheet[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='0B2545')
    for letter, width in zip('ABCDEF', (16, 24, 10, 10, 10, 40)):
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def feedRows(conn) -> list:
    """The record as the Activity feed reads it."""
    rows = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? ORDER BY revision',
                        (KIND, SCOPE)).fetchall()
    out, previous = [], {s: {} for s in SETS}
    for r in rows:
        body = _parse(r['body'])
        out.append({'id': r['id'], 'revision': r['revision'], 'action': r['action'], 'note': r['note'],
                    'actor': r['actor'], 'at': r['at'], 'changes': _summary(_changes(previous, body))})
        previous = body
    return out


def describe() -> dict:
    """Everything the console's PM Minimums tab shows."""
    from . import products
    from .rules import RISK_LEVEL_LABELS
    table = current()
    try:
        found = products.get(APS_PRODUCT)
        apsName = found['name'] if found else ''
    except products.BadCatalogue:
        apsName = ''
    return dict(table, gaps=gaps(table),
                sets=[{'id': s, 'label': LABELS[s], 'name': NAMES[s]} for s in SETS],
                apsProduct=APS_PRODUCT, apsName=apsName, otherId=OTHER_ID,
                allocations=list(ALLOCATIONS),
                risks=[{'id': r, 'label': RISK_LEVEL_LABELS.get(r, r)} for r in RISKS],
                currencies=list(CURRENCIES), cells=len(cells()), fallback=FALLBACK_MINIMUM,
                noteMax=NOTE_MAX, valueMax=VALUE_MAX)
