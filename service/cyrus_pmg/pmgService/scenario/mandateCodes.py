"""The mandate codes: a code for each portfolio a PWA can be offered, read by
the account opening request for the mandate's own row of its SMA Details
(D205). The mandate's benchmark is the portfolio benchmarks' (D211).

A PORTFOLIO here is an implementation type, a base currency, a risk level,
an allocation and a hedging policy. The allocations are the ones the type
offers - Full, Core, ex-HFs and ex-Alts, each with real assets left out
where it holds them (Full ex-RA, ex-HFs ex-RA; a type that requires them left
out offers only that form), an all-equity level, which holds no
alternatives and every type offers, and Custom, a portfolio built product by
product (D171) at any risk profile the type anchors. Each is held at the
risk levels and in the currencies the strategic universe offers it, under
every hedging policy.

These are the mandate's codes alone. A separate account's own code and
benchmark are the Catalogue's (D197, D204), and the implementation type plays
no part in them.

A CELL is a portfolio's code. A portfolio on offer with no code is a GAP: the
console counts and lists them, and an account opening request for that
portfolio cannot be submitted until the gap is filled. Nothing else reads the
table: a proposal and its download are untouched by it.

The admin keeps the table in the console's Account Opening tab: loaded whole
from a file - a long table, ImplementationType, Currency, RiskLevel,
AllocationType, Hedging and MandateCode, any of the first five All for every
one, the row naming more winning where two give a portfolio - or edited, a
cell at a time or pasted from Excel, on screen. A Benchmark column is not
read: the benchmarks are the MAP benchmarks' (D211). Every change is a
revision with who, when, an optional note and what moved, and an earlier
revision can be put back.

Storage. The ``policies`` and ``policyHistory`` tables (sleeveRepo, schema 4)
under the kind 'mandateCodes' and the one scope '*', each cell's code under
its key - a revision from before D211 holds [code, benchmark], and is read
for its code. Until the first save there is no table. A
read never writes.
"""

from __future__ import annotations

import datetime
import io
import json
import os
import threading
from functools import lru_cache

from . import saaKeys, sleeveRepo
from .types import PortfolioKey, ValidationError

KIND = 'mandateCodes'
SCOPE = '*'
CODE_MAX = 40
NOTE_MAX = 240
#: changes listed for one revision before the rest are counted
CHANGES_SHOWN = 40
ALL = 'All'
CURRENCIES = saaKeys.CURRENCIES
ALL_EQUITY = 'All Equity'
CUSTOM = 'Custom'
#: what a file's AllocationType may say for an allocation form, beyond its
#: own name: the desk's 'ex-RA' is Full with real assets left out
_ALIASES = {'exra': 'Full ex-RA', 'fullexrealassets': 'Full ex-RA',
            'exhfsexrealassets': 'ex-HFs ex-RA', 'na': ALL_EQUITY, 'none': ALL_EQUITY}
#: the file's columns, matched by name whatever the case or spacing
COLUMNS = ('ImplementationType', 'Currency', 'RiskLevel', 'AllocationType', 'Hedging', 'MandateCode')
_HEADS = {'implementationtype': 'type', 'implementation': 'type', 'type': 'type', 'variant': 'type',
          'currency': 'currency', 'basecurrency': 'currency', 'ccy': 'currency',
          'risklevel': 'risk', 'risk': 'risk', 'riskprofile': 'risk',
          'allocationtype': 'form', 'allocation': 'form',
          'hedging': 'hedging', 'hedgingpolicy': 'hedging', 'currencyhedgingpolicy': 'hedging',
          'mandatecode': 'code', 'code': 'code'}
_REQUIRED = ('type', 'currency', 'risk', 'form', 'hedging', 'code')
_DIMENSIONS = ('type', 'form', 'risk', 'currency', 'hedging')


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def _squash(text) -> str:
    return ''.join(ch for ch in str(text or '').lower() if ch.isalnum())


def _text(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return ' '.join(str(value if value is not None else '').split())


# ------------------------------------------------------------- the cells ---

def types() -> list:
    from . import rules
    return list(rules.VARIANT_ALLOCATIONS)


def hedgings() -> list:
    from . import rules
    return list(rules.HEDGING_POLICIES)


def risks() -> list:
    return list(saaKeys.RISK_LEVELS)


def riskLabel(risk) -> str:
    from .rules import RISK_LEVEL_LABELS
    return RISK_LEVEL_LABELS.get(risk, risk)


def formOf(allocation, exclude) -> str:
    """An allocation's form: its name, 'ex-RA' after it with real assets left out."""
    return allocation + (' ex-RA' if exclude else '')


def formLabel(form) -> str:
    return form.replace(' ex-RA', ' · ex Real Assets')


def cell(variant, form, risk, currency, hedging) -> str:
    return '|'.join((variant, form, risk, currency, hedging))


def cellWords(key) -> str:
    variant, form, risk, currency, hedging = key.split('|')
    return '{} · {} · {} · {} · {}'.format(variant, formLabel(form), riskLabel(risk), currency, hedging)


@lru_cache(maxsize=None)
def _offered() -> tuple:
    """(type, form, risk, currency) for every portfolio on offer, the
    console's order: by type, its forms in turn - the allocations as the
    selector lists them, each beside its form without real assets, then all
    equity, then custom - then risk, then currency."""
    from . import customPortfolio, rules, universe
    held = {}
    for key in universe.keys():
        if key.isAllEquity:
            held.setdefault((ALL_EQUITY, False), set()).add((key.riskLevel, key.currency))
        else:
            held.setdefault((key.allocationType, bool(key.excludeRealAssets)), set()).add(
                (key.riskLevel, key.currency))
    order = {r: i for i, r in enumerate(risks())}
    out = []
    for variant in types():
        forms = []
        for allocation in rules.allocationsForVariant(variant):
            withRe = allocation in rules._derived()['RE_ALLOWED']
            if not (withRe and rules.variantForcesExcludeRE(variant)):
                forms.append((allocation, False))
            if withRe:
                forms.append((allocation, True))
        forms.append((ALL_EQUITY, False))
        for allocation, exclude in forms:
            at = held.get((allocation, exclude), set())
            form = ALL_EQUITY if allocation == ALL_EQUITY else formOf(allocation, exclude)
            for risk, currency in sorted(at, key=lambda rc: (order.get(rc[0], 99), CURRENCIES.index(rc[1]))):
                out.append((variant, form, risk, currency))
        for risk in risks():
            for currency in CURRENCIES:
                if customPortfolio.anchorKey(currency, variant, risk) is not None:
                    out.append((variant, CUSTOM, risk, currency))
    return tuple(out)


def cells() -> list:
    """Every cell on offer, in the console's order: type, hedging, form,
    risk, currency."""
    out = []
    for variant in types():
        mine = [o for o in _offered() if o[0] == variant]
        for hedging in hedgings():
            out.extend(cell(v, f, r, c, hedging) for v, f, r, c in mine)
    return out


def layout() -> list:
    """What the console draws: each type with its forms, each form with the
    risk levels it is held at and, a risk level at a time, the currencies."""
    out = []
    for variant in types():
        forms = []
        for v, form, risk, currency in _offered():
            if v != variant:
                continue
            if not forms or forms[-1]['form'] != form:
                forms.append({'form': form, 'label': formLabel(form), 'risks': []})
            rows = forms[-1]['risks']
            if not rows or rows[-1]['risk'] != risk:
                rows.append({'risk': risk, 'label': riskLabel(risk), 'currencies': []})
            rows[-1]['currencies'].append(currency)
        out.append({'type': variant, 'forms': forms})
    return out


def keyOf(variant, keyStr=None, profile=None, currency=None, hedging=None):
    """The cell a proposal reads: a strategic portfolio's by its key, a
    custom portfolio's by its risk profile and currency."""
    if keyStr:
        key = PortfolioKey.fromStr(keyStr) if isinstance(keyStr, str) else keyStr
        form = ALL_EQUITY if key.isAllEquity else formOf(key.allocationType, bool(key.excludeRealAssets))
        return cell(variant, form, key.riskLevel, key.currency, hedging)
    return cell(variant, CUSTOM, profile, currency, hedging)


# --------------------------------------------------------------- storage ---

def _row(conn):
    return conn.execute('SELECT * FROM policies WHERE kind = ? AND scope = ?',
                        (KIND, SCOPE)).fetchone()


def _code(held) -> str:
    """A stored cell's code: the code itself, or the first of a revision
    from before D211's [code, benchmark]."""
    if isinstance(held, (list, tuple)):
        held = held[0] if held else ''
    return str(held or '')


def _parse(text) -> dict:
    body = json.loads(text) if isinstance(text, str) else (text or {})
    codes = {k: _code(v) for k, v in (body.get('codes') or {}).items()}
    return {'codes': {k: v for k, v in codes.items() if v}}


_lock = threading.Lock()
_cache = None            # (file stamp, the row as read, or None for none)


def _stored(path):
    """The row in force, kept against the file's stamp: the account card
    reads it on every lookup."""
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
    """The table in force: {revision, codes: {cell: code}, updatedBy,
    updatedAt}, a fresh copy on every call. Revision 0 is no
    table. A read never writes."""
    path = sleeveRepo.dbPath()
    row = _stored(path) if os.path.exists(path) else None
    if row is None:
        return {'codes': {}, 'revision': 0, 'updatedBy': '', 'updatedAt': ''}
    return dict(_parse(row['body']), revision=row['revision'], updatedBy=row['updatedBy'],
                updatedAt=row['updatedAt'])


def codeFor(cellKey, table=None):
    """The code at a cell, or None where it has none - a gap."""
    table = current() if table is None else table
    return table['codes'].get(cellKey) or None


def gaps(table=None) -> dict:
    """{noCode: [cell]}: the portfolios on offer with no code."""
    table = current() if table is None else table
    return {'noCode': [c for c in cells() if not table['codes'].get(c)]}


# ------------------------------------------------------------ the table ---

def _entry(raw, where) -> str:
    if raw is not None and not isinstance(raw, (str, int, float)):
        raise ValidationError('codes', '{}: a code is a word.'.format(where))
    code = _text(raw)
    if len(code) > CODE_MAX:
        raise ValidationError('codes', '{}: keep the code to {} characters.'.format(where, CODE_MAX))
    return code


def normalise(body) -> dict:
    """The canonical table, or a ValidationError naming ``codes``: every
    cell one on offer, each code within its length, and a blank cell left
    out."""
    if not isinstance(body, dict) or not isinstance(body.get('codes'), dict):
        raise ValidationError('codes', 'Send the codes as a table, a portfolio to a code.')
    offered = set(cells())
    out = {}
    for key, raw in body['codes'].items():
        if key not in offered:
            raise ValidationError('codes', '{} is not a portfolio on offer.'.format(
                cellWords(key) if key.count('|') == 4 else repr(key)))
        code = _entry(raw, cellWords(key))
        if code:
            out[key] = code
    return {'codes': {k: out[k] for k in cells() if k in out}}


# ------------------------------------------------------------- the file ---

def _fileName(name) -> str:
    name = os.path.basename(str(name or '').replace('\\', '/')).strip()
    if not name.lower().endswith(('.csv', '.xlsx')):
        raise ValidationError('file', 'A mandate codes file is a .csv or an .xlsx.')
    return name


def _readSheet(name, data):
    """(header, rows) of the sheet that holds the table: a CSV's only one, or
    the first sheet of a workbook whose header names every required column."""
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
        heads = {_HEADS.get(_squash(h)) for h in head}
        if all(r in heads for r in _REQUIRED):
            return ([('' if h is None else str(h)) for h in head],
                    [['' if v is None else v for v in r] for r in rows])
    raise ValidationError('file', 'No sheet in {} has the columns {}.'.format(
        name, ', '.join(COLUMNS[:6])))


def _pick(raw, options, labels=None, aliases=None):
    """The option *raw* names - by its name, its label, an alias, or a name
    it is the start of when only one is - or ALL, or None."""
    said = _squash(raw)
    if said == 'all':
        return ALL
    for o in options:
        if said == _squash(o) or (labels and said == _squash(labels.get(o, ''))):
            return o
    if aliases and said in aliases:
        return aliases[said]
    starts = [o for o in options if said and _squash(o).startswith(said)]
    return starts[0] if len(starts) == 1 else None


def readFile(name, content) -> dict:
    """The table a file holds, with what is wrong with it: {table, rows,
    problems: [words]}. A row's All means every one; where two rows give a
    portfolio, the one naming more wins, and two that name as much and
    disagree are a problem. A row naming a portfolio not on offer is a
    problem too. A Benchmark column is not read (D211)."""
    from . import products
    from .rules import RISK_LEVEL_LABELS
    name = _fileName(name)
    data = products._decode(content)
    head, rows = _readSheet(name, data)
    at = {}
    for i, h in enumerate(head):
        field = _HEADS.get(_squash(h))
        if field is not None:
            at.setdefault(field, i)
    missing = [c for c, f in zip(COLUMNS, _REQUIRED) if f not in at]
    if missing:
        raise ValidationError('file', '{}: missing column(s) {}.'.format(name, ', '.join(missing)))
    forms = sorted({o[1] for o in _offered()}, key=lambda f: [o[1] for o in _offered()].index(f))
    offered = cells()
    known = set(offered)
    claimed, problems, count = {}, [], 0

    def field(row, key):
        i = at.get(key)
        return row[i] if i is not None and i < len(row) else ''
    for n, row in enumerate(rows, start=2):
        if not any(str(v).strip() for v in row):
            continue
        count += 1
        where = 'Row {}'.format(n)
        given = {'type': _pick(field(row, 'type'), types()),
                 'currency': _pick(field(row, 'currency'), CURRENCIES),
                 'risk': _pick(field(row, 'risk'), risks(), RISK_LEVEL_LABELS),
                 'form': _pick(field(row, 'form'), forms, {f: formLabel(f) for f in forms}, _ALIASES),
                 'hedging': _pick(field(row, 'hedging'), hedgings())}
        # an all-equity level is its own allocation: blank says so
        if given['risk'] == ALL_EQUITY and not _text(field(row, 'form')):
            given['form'] = ALL_EQUITY
        words = {'type': 'ImplementationType', 'currency': 'Currency', 'risk': 'RiskLevel',
                 'form': 'AllocationType', 'hedging': 'Hedging'}
        bad = [(words[k], field(row, k)) for k in _DIMENSIONS if given[k] is None]
        if bad:
            problems.append('{}: {}.'.format(where, '; '.join(
                '{} "{}" is not one the table takes'.format(c, _text(v)) for c, v in bad)))
            continue
        try:
            code = _entry(field(row, 'code'), where)
        except ValidationError as exc:
            problems.append(exc.message)
            continue
        if not code:
            problems.append('{}: no MandateCode.'.format(where))
            continue
        rank = sum(1 for k in _DIMENSIONS if given[k] != ALL)
        if rank == len(_DIMENSIONS):
            one = cell(*(given[k] for k in _DIMENSIONS))
            hit = [one] if one in known else []
        else:
            hit = [c for c in offered if all(given[k] == ALL or given[k] == part
                                             for k, part in zip(_DIMENSIONS, c.split('|')))]
        if not hit:
            problems.append('{}: {} is not a portfolio on offer.'.format(where, ' · '.join(
                formLabel(given[k]) if k == 'form' else riskLabel(given[k]) if k == 'risk' else given[k]
                for k in _DIMENSIONS)))
            continue
        for c in hit:
            had = claimed.get(c)
            if had is None or rank > had[0]:
                claimed[c] = (rank, code, n)
            elif rank == had[0] and code != had[1]:
                problems.append('Rows {} and {} both give {}: codes {} and {}.'.format(
                    had[2], n, cellWords(c), had[1], code))
    if count == 0:
        problems.append('{} holds no rows.'.format(name))
    table = {'codes': {c: claimed[c][1] for c in offered if c in claimed}}
    return {'name': name, 'table': table, 'rows': count, 'problems': problems,
            'benchmarkColumn': 'benchmark' in {_squash(h) for h in head}}


def _changes(before, after) -> list:
    """What moved between two tables, in the words the console shows."""
    was, now = before['codes'], after['codes']
    out = []
    for c in cells():
        a, b = was.get(c) or '', now.get(c) or ''
        if a != b:
            out.append('{}: code {} → {}'.format(cellWords(c), a or '—', b or '—'))
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
    gap = gaps(dict(read['table']))
    return {'name': read['name'], 'rows': read['rows'], 'problems': read['problems'],
            'benchmarkColumn': read['benchmarkColumn'], 'cells': len(read['table']['codes']),
            'changes': _summary(changes), 'changed': len(changes),
            'noCode': len(gap['noCode']), 'base': now['revision']}


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
    text = json.dumps(body, separators=(',', ':'))
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
            'base', 'The mandate codes were changed by {} since you opened them. Reload to see the '
            'change.'.format((row['updatedBy'] if row is not None else '') or 'someone'))


def _previous(conn) -> dict:
    existing = _row(conn)
    return _parse(existing['body']) if existing is not None else {'codes': {}}


def _put(cleaned, action, note, user, base) -> dict:
    conn = sleeveRepo.connection()
    try:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        try:
            _refuseStale(conn, base)
            if cleaned['codes'] == _previous(conn)['codes']:
                raise ValidationError('codes', 'Those are the mandate codes already in force.')
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
    """Put a file's table in force, whole: a cell the file leaves out loses
    its code, and stays in the history. Refused while the file has a
    problem."""
    note = _note(note)
    read = readFile(name, content)
    if read['problems']:
        raise ValidationError('file', '{}: {}'.format(read['name'], ' '.join(read['problems'][:6])
                                                       + ('' if len(read['problems']) <= 6 else
                                                          ' And {} more.'.format(len(read['problems']) - 6))))
    return _put(normalise(read['table']), 'imported', note or 'Loaded {}'.format(read['name']), user, base)


def _rows(conn) -> list:
    return conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? '
                        'ORDER BY revision', (KIND, SCOPE)).fetchall()


def history() -> list:
    """Every revision, newest first, each with what it changed."""
    conn = sleeveRepo.connection()
    try:
        rows = _rows(conn)
    finally:
        conn.close()
    out, previous = [], {'codes': {}}
    top = rows[-1]['revision'] if rows else None
    for r in rows:
        body = _parse(r['body'])
        changes = _changes(previous, body)
        out.append({'revision': r['revision'], 'action': r['action'], 'note': r['note'],
                    'actor': r['actor'], 'at': r['at'], 'changes': _summary(changes),
                    'changed': len(changes), 'cells': len(body['codes']),
                    'current': r['revision'] == top})
        previous = body
    return list(reversed(out))


def feedRows(conn) -> list:
    """The record as the Activity feed reads it."""
    out, previous = [], {'codes': {}}
    for r in _rows(conn):
        body = _parse(r['body'])
        out.append({'id': r['id'], 'revision': r['revision'], 'action': r['action'], 'note': r['note'],
                    'actor': r['actor'], 'at': r['at'], 'changes': _summary(_changes(previous, body))})
        previous = body
    return out


def revert(revision, note='', user='', base=None) -> dict:
    """Put an earlier revision's table back in force, as a new revision. A
    cell no longer on offer is left out."""
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
        raise ValidationError('revision', 'There is no revision {} of the mandate codes.'.format(number))
    offered = set(cells())
    body = _parse(row['body'])
    return _put(normalise({'codes': {k: v for k, v in body['codes'].items() if k in offered}}), 'reverted',
                note or 'Restored revision {}'.format(number), user, base)


def exportRows() -> list:
    """The table in force as the file it loads from: the header, then a row
    a cell that has a code, in the console's order."""
    out = [list(COLUMNS)]
    codes = current()['codes']
    for c in cells():
        held = codes.get(c)
        if held:
            variant, form, risk, currency, hedging = c.split('|')
            out.append([variant, currency, riskLabel(risk), form, hedging, held])
    return out


def exportWorkbook() -> bytes:
    """``exportRows`` as an .xlsx with one sheet, Mandate Codes, ready to
    edit and load again."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    book = Workbook()
    sheet = book.active
    sheet.title = 'Mandate Codes'
    for row in exportRows():
        sheet.append(row)
    for c in sheet[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='0B2545')
    for letter, width in zip('ABCDEF', (26, 10, 22, 16, 18, 26)):
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def describe() -> dict:
    """Everything the console's Mandate Codes view shows."""
    table = current()
    gap = gaps(table)
    return dict(table, layout=layout(), hedgings=hedgings(), currencies=list(CURRENCIES),
                gaps=gap, cells=len(cells()), codeMax=CODE_MAX, noteMax=NOTE_MAX)
