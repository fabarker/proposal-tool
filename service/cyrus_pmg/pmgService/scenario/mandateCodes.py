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

Most mandates share a code, so the codes are given by GROUP (D215). A group
is a name, a code, and the mandates it holds: the implementation types,
allocations, risk levels, currencies and hedging policies chosen in it, All
where it chooses none. The groups are kept in order, and a mandate takes
the code of the FIRST group that holds it - so a narrow group goes above a
wide one, and a last group of All holds everything else. A group that gives
no mandate its code - every one it holds taken by a group above it - is
refused, since it would only mislead. Until the first save the groups are
the desk's two, at revision 0: PMG ESG, every mandate of that type, G3; and
everything else, 7L. Nothing is written: a read never writes.

A mandate on offer that no group holds is a GAP: the console counts and
lists them, and an account opening request for that portfolio cannot be
submitted until the gap is filled. Nothing else reads the codes: a proposal
and its download are untouched by them.

The admin keeps the groups in the console's Account Opening tab - added,
named, coded, chosen, dragged into order, taken off - or loads them whole
from a file: a row a group, in order, with the columns Group, MandateCode,
ImplementationType, AllocationType, RiskLevel, Currency and Hedging, each of
the last five All, blank, or the ones it holds separated by commas. A file
in the layout before groups - a row a portfolio, any of its five All, the
row naming more winning - is still read, each row a group, the ones naming
more first. Every change is a revision with who, when, an optional note and
what moved, and an earlier revision can be put back.

Storage. The ``policies`` and ``policyHistory`` tables (sleeveRepo, schema 4)
under the kind 'mandateCodes' and the one scope '*', the groups in order. A
revision from before groups holds each portfolio's code under its key - an
older one still [code, benchmark] (D211) - and reads as groups giving each
portfolio that code, the portfolios of one code folded together.
"""

from __future__ import annotations

import datetime
import io
import json
import os
import re
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
#: a group's name; one read from a portfolio's words runs to about a hundred
NAME_MAX = 120
GROUPS_MAX = 2000
#: what a group holds, in the order a portfolio's key gives them: its
#: implementation types, allocations, risk levels, currencies and hedging
#: policies, each [] for All (D215)
GROUP_DIMENSIONS = ('types', 'forms', 'risks', 'currencies', 'hedgings')
_WORDS = {'types': 'implementation types', 'forms': 'allocations', 'risks': 'risk levels',
          'currencies': 'currencies', 'hedgings': 'hedging policies'}
_ALL_WORDS = {d: 'All ' + w for d, w in _WORDS.items()}
#: the groups before the first save: the desk's two codes
SEED = ({'name': 'PMG ESG', 'code': 'G3', 'types': ['PMG ESG']},
        {'name': 'Everything else', 'code': '7L'})
#: the file's columns, in the order an export writes them, matched by name
#: whatever the case or spacing
COLUMNS = ('Group', 'MandateCode', 'ImplementationType', 'AllocationType', 'RiskLevel', 'Currency', 'Hedging')
_HEADS = {'group': 'name', 'groupname': 'name', 'name': 'name',
          'implementationtype': 'type', 'implementation': 'type', 'type': 'type', 'variant': 'type',
          'currency': 'currency', 'basecurrency': 'currency', 'ccy': 'currency',
          'risklevel': 'risk', 'risk': 'risk', 'riskprofile': 'risk',
          'allocationtype': 'form', 'allocation': 'form',
          'hedging': 'hedging', 'hedgingpolicy': 'hedging', 'currencyhedgingpolicy': 'hedging',
          'mandatecode': 'code', 'code': 'code'}
_COLUMN_OF = {'name': 'Group', 'code': 'MandateCode', 'type': 'ImplementationType', 'form': 'AllocationType',
              'risk': 'RiskLevel', 'currency': 'Currency', 'hedging': 'Hedging'}
#: what a file in the layout before groups must have: a row a portfolio
_REQUIRED = ('type', 'currency', 'risk', 'form', 'hedging', 'code')
_DIM_FIELD = {'types': 'type', 'forms': 'form', 'risks': 'risk', 'currencies': 'currency', 'hedgings': 'hedging'}


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


# --------------------------------------------------------------- the groups ---

def forms() -> list:
    """Every allocation form on offer, in the console's order."""
    out = []
    for o in _offered():
        if o[1] not in out:
            out.append(o[1])
    return out


def options() -> dict:
    """What each of a group's five may hold, in order."""
    return {'types': types(), 'forms': forms(), 'risks': risks(), 'currencies': list(CURRENCIES),
            'hedgings': hedgings()}


def _label(dim, value) -> str:
    return formLabel(value) if dim == 'forms' else riskLabel(value) if dim == 'risks' else value


def _seeded() -> list:
    return [dict(g, **{d: list(g.get(d) or []) for d in GROUP_DIMENSIONS}) for g in SEED]


def holds(group, key) -> bool:
    """Whether *group* holds the portfolio at *key*: each of its five All,
    or naming the portfolio's."""
    parts = key.split('|')
    return not any(group.get(d) and parts[at] not in group[d] for at, d in enumerate(GROUP_DIMENSIONS))


def resolve(groups) -> dict:
    """{cell: index of the group that gives it its code}: the first group
    that holds it, for every portfolio on offer that one holds."""
    out = {}
    for key in cells():
        for n, group in enumerate(groups):
            if holds(group, key):
                out[key] = n
                break
    return out


def _codesOf(groups, given=None) -> dict:
    given = resolve(groups) if given is None else given
    return {key: groups[n]['code'] for key, n in given.items()}


def _given(groups, given=None) -> list:
    """How many portfolios each group gives their code, and how many it
    holds that a group above it takes: [{gives, holds}]."""
    given = resolve(groups) if given is None else given
    out = [{'gives': 0, 'holds': 0} for _ in groups]
    for n in given.values():
        out[n]['gives'] += 1
    for key in cells():
        for n, group in enumerate(groups):
            if holds(group, key):
                out[n]['holds'] += 1
    return out


def _dimWords(dim, values) -> str:
    if not values:
        return _ALL_WORDS[dim]
    return ', '.join(_label(dim, v) for v in values)


def groupWords(group) -> str:
    """A group as a history line shows it: 'PMG ESG · All allocations · ...'."""
    return ' · '.join(_dimWords(d, group.get(d)) for d in GROUP_DIMENSIONS)


# --------------------------------------------------------------- storage ---

def _row(conn):
    return conn.execute('SELECT * FROM policies WHERE kind = ? AND scope = ?',
                        (KIND, SCOPE)).fetchone()


def _code(held) -> str:
    """A stored cell's code: the code itself, or the first of an older
    revision's [code, benchmark] (D211)."""
    if isinstance(held, (list, tuple)):
        held = held[0] if held else ''
    return str(held or '')


def _parse(text) -> dict:
    """A stored body as groups: its own, or - for a revision from before
    groups - the portfolios it gave a code, as groups (``_fromCodes``)."""
    body = json.loads(text) if isinstance(text, str) else (text or {})
    if isinstance(body.get('groups'), list):
        return {'groups': [{'name': str(g.get('name') or ''), 'code': str(g.get('code') or ''),
                            **{d: [str(v) for v in g.get(d) or []] for d in GROUP_DIMENSIONS}}
                           for g in body['groups'] if isinstance(g, dict)]}
    codes = {k: _code(v) for k, v in (body.get('codes') or {}).items()}
    return {'groups': _fromCodes({k: codes[k] for k in cells() if codes.get(k)})}


def _fromCodes(codes) -> list:
    """Groups that give each portfolio of *codes* - {cell: code}, as a
    revision from before groups kept them - its code and no other: the
    portfolios of one code folded together wherever they differ in one of
    their five alone, so a code given across the table reads as a few
    groups, not a group a portfolio. No two groups hold one portfolio, so
    their order is the console's; a code's groups are named by it - M34, or
    M34 (1), M34 (2)."""
    opts = options()
    boxes = {}
    for key, code in codes.items():
        parts = key.split('|')
        box = tuple(frozenset([parts[at]]) for at in range(len(GROUP_DIMENSIONS)))
        boxes[(code, box)] = True
    moved = True
    while moved:
        moved = False
        for at in reversed(range(len(GROUP_DIMENSIONS))):
            joined = {}
            for code, box in boxes:
                rest = (code,) + box[:at] + box[at + 1:]
                joined.setdefault(rest, set()).update(box[at])
            folded = {}
            for rest, values in joined.items():
                box = rest[1:at + 1] + (frozenset(values),) + rest[at + 1:]
                folded[(rest[0], box)] = True
            if len(folded) < len(boxes):
                moved = True
            boxes = folded
    order = {key: n for n, key in enumerate(cells())}

    def first(item):
        code, box = item
        return min(order[k] for k in codes if codes[k] == code and all(
            part in box[at] for at, part in enumerate(k.split('|'))))
    placed = sorted(boxes, key=first)
    count = {}
    for code, _ in placed:
        count[code] = count.get(code, 0) + 1
    seen, out = {}, []
    for code, box in placed:
        seen[code] = seen.get(code, 0) + 1
        group = {'name': code if count[code] == 1 else '{} ({})'.format(code, seen[code]), 'code': code}
        for at, dim in enumerate(GROUP_DIMENSIONS):
            chosen = [v for v in opts[dim] if v in box[at]]
            group[dim] = [] if len(chosen) == len(opts[dim]) else chosen
        out.append(group)
    return out


_lock = threading.Lock()
_cache = None            # (file stamp, the table as read)
_seedTable = None


def _table(groups, **stamp) -> dict:
    given = resolve(groups)
    return dict({'groups': groups, 'codes': _codesOf(groups, given), 'given': _given(groups, given)}, **stamp)


def _stored(path):
    """The table in force, kept against the file's stamp: the account card
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
            if row is not None:
                found = _table(_parse(row['body'])['groups'], revision=row['revision'],
                               updatedBy=row['updatedBy'], updatedAt=row['updatedAt'])
        except Exception:                      # noqa: BLE001 - an older file: nothing yet
            found = None
        finally:
            conn.close()
    with _lock:
        _cache = (stamp, found)
    return found


def _copy(table) -> dict:
    return dict(table, groups=[dict(g, **{d: list(g[d]) for d in GROUP_DIMENSIONS}) for g in table['groups']],
                codes=dict(table['codes']), given=[dict(x) for x in table['given']])


def current() -> dict:
    """The table in force: {revision, groups, codes: {cell: code}, given:
    [{gives, holds}] a group, updatedBy, updatedAt}, a fresh copy on every
    call. Revision 0 is the desk's two groups, before any save. A read never
    writes."""
    global _seedTable
    path = sleeveRepo.dbPath()
    table = _stored(path) if os.path.exists(path) else None
    if table is None:
        if _seedTable is None:
            _seedTable = _table(_seeded(), revision=0, updatedBy='', updatedAt='')
        table = _seedTable
    return _copy(table)


def codeFor(cellKey, table=None):
    """The code at a cell, or None where no group holds it - a gap."""
    table = current() if table is None else table
    return table['codes'].get(cellKey) or None


def gaps(table=None) -> dict:
    """{noCode: [cell]}: the portfolios on offer no group holds."""
    table = current() if table is None else table
    return {'noCode': [c for c in cells() if not table['codes'].get(c)]}


# ------------------------------------------------------------ the groups ---

def _entry(raw, where, field='codes') -> str:
    if raw is not None and not isinstance(raw, (str, int, float)):
        raise ValidationError(field, '{}: a code is a word.'.format(where))
    code = _text(raw)
    if len(code) > CODE_MAX:
        raise ValidationError(field, '{}: keep the code to {} characters.'.format(where, CODE_MAX))
    return code


def _chosen(dim, given, field, where, opts) -> list:
    """A group's choice of one of its five, as stored: the ones it holds in
    the console's order, or [] for All - which every one of them is too."""
    if given is None or given == '' or given == ALL:
        return []
    if isinstance(given, str):
        given = [given]
    if not isinstance(given, list):
        raise ValidationError(field, '{}: send the {} as a list.'.format(where, _WORDS[dim]))
    allowed = opts[dim]
    picked = set()
    for value in given:
        if value == ALL:
            return []
        if value not in allowed:
            raise ValidationError(field, '{}: {!r} is not one of the {} on offer.'.format(
                where, value, _WORDS[dim]))
        picked.add(value)
    out = [v for v in allowed if v in picked]
    return [] if len(out) == len(allowed) else out


def _idle(groups) -> list:
    """[(index, words)] for each group that gives no portfolio its code."""
    counts = _given(groups)
    out = []
    for n, (group, count) in enumerate(zip(groups, counts)):
        if count['gives']:
            continue
        if not count['holds']:
            out.append((n, '{} holds no portfolio on offer: choose again, or take it off.'.format(group['name'])))
            continue
        above = next(g['name'] for g in groups[:n] if any(holds(g, k) and holds(group, k) for k in cells()))
        out.append((n, 'Every portfolio {} holds is taken by a group above it, {}: move it up, or take it '
                       'off.'.format(group['name'], above)))
    return out


def normalise(body) -> dict:
    """The canonical groups, in order, or a ValidationError naming the box:
    each with a name of its own and a code within their lengths, each of its
    five All or ones on offer, and each giving at least one portfolio its
    code."""
    if not isinstance(body, dict) or not isinstance(body.get('groups'), list):
        raise ValidationError('groups', 'Send the mandate codes as a list of groups.')
    if len(body['groups']) > GROUPS_MAX:
        raise ValidationError('groups', 'Keep it to {} groups.'.format(GROUPS_MAX))
    opts = options()
    out, names = [], {}
    for n, raw in enumerate(body['groups']):
        where = 'Group {}'.format(n + 1)
        if not isinstance(raw, dict):
            raise ValidationError('groups', '{}: send it as a group.'.format(where))
        name = _text(raw.get('name'))
        if not name:
            raise ValidationError('group{}name'.format(n), '{} needs a name.'.format(where))
        if len(name) > NAME_MAX:
            raise ValidationError('group{}name'.format(n), '{}: keep the name to {} characters.'.format(
                where, NAME_MAX))
        if name.lower() in names:
            raise ValidationError('group{}name'.format(n), 'Groups {} and {} are both called {}: give each a '
                                  'name of its own.'.format(names[name.lower()] + 1, n + 1, name))
        names[name.lower()] = n
        code = _entry(raw.get('code'), name, 'group{}code'.format(n))
        if not code:
            raise ValidationError('group{}code'.format(n), '{} needs a mandate code.'.format(name))
        group = {'name': name, 'code': code}
        for dim in GROUP_DIMENSIONS:
            group[dim] = _chosen(dim, raw.get(dim), 'group{}{}'.format(n, dim), name, opts)
        out.append(group)
    idle = _idle(out)
    if idle:
        raise ValidationError('group{}name'.format(idle[0][0]), idle[0][1])
    return {'groups': out}


# ------------------------------------------------------------- the file ---

def _fileName(name) -> str:
    name = os.path.basename(str(name or '').replace('\\', '/')).strip()
    if not name.lower().endswith(('.csv', '.xlsx')):
        raise ValidationError('file', 'A mandate codes file is a .csv or an .xlsx.')
    return name


def _readSheet(name, data):
    """(header, rows) of the sheet that holds the table: a CSV's only one, or
    the first sheet of a workbook whose header names a code and, for the
    layout before groups, every one of the five."""
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
        if 'code' in heads and ('name' in heads or all(r in heads for r in _REQUIRED)):
            return ([('' if h is None else str(h)) for h in head],
                    [['' if v is None else v for v in r] for r in rows])
    raise ValidationError('file', 'No sheet in {} has the columns {}.'.format(name, ', '.join(COLUMNS)))


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


def _picker(dim):
    from .rules import RISK_LEVEL_LABELS
    opts = options()[dim]
    if dim == 'forms':
        return lambda raw: _pick(raw, opts, {f: formLabel(f) for f in opts}, _ALIASES)
    if dim == 'risks':
        return lambda raw: _pick(raw, opts, RISK_LEVEL_LABELS)
    return lambda raw: _pick(raw, opts)


def readFile(name, content) -> dict:
    """The groups a file holds, with what is wrong with it: {table, rows,
    problems: [words], before}. A row a group, in order - or, a file in the
    layout before groups (*before*), a row a portfolio, any of its five All:
    each row a group, the ones naming more first, two naming as much that
    give one portfolio two codes a problem. A Benchmark column is not read
    (D211)."""
    from . import products
    name = _fileName(name)
    data = products._decode(content)
    head, rows = _readSheet(name, data)
    at = {}
    for i, h in enumerate(head):
        field = _HEADS.get(_squash(h))
        if field is not None:
            at.setdefault(field, i)
    before = 'name' not in at
    needed = _REQUIRED if before else ('name', 'code')
    missing = [_COLUMN_OF[f] for f in needed if f not in at]
    if missing:
        raise ValidationError('file', '{}: missing column(s) {}.'.format(name, ', '.join(missing)))
    pickers = {d: _picker(d) for d in GROUP_DIMENSIONS}
    groups, problems, count, names = [], [], 0, {}

    def field(row, key):
        i = at.get(key)
        return row[i] if i is not None and i < len(row) else ''
    for n, row in enumerate(rows, start=2):
        if not any(str(v).strip() for v in row):
            continue
        count += 1
        where = 'Row {}'.format(n)
        said = {}
        for dim in GROUP_DIMENSIONS:
            raw = field(row, _DIM_FIELD[dim])
            words = [raw] if before else [w for w in re.split(r'[,;\n]', _text(raw)) if w.strip()]
            said[dim] = [(w, pickers[dim](w)) for w in words]
        # an all-equity level is its own allocation: blank says so
        if before and [p for _, p in said['risks']] == [ALL_EQUITY] and not _text(field(row, 'form')):
            said['forms'] = [('', ALL_EQUITY)]
        chosen, bad = {}, []
        for dim in GROUP_DIMENSIONS:
            picked = [p for _, p in said[dim]]
            bad += ['{} "{}" is not one the table takes'.format(_COLUMN_OF[_DIM_FIELD[dim]], _text(w))
                    for w, p in said[dim] if p is None]
            chosen[dim] = [] if ALL in picked else [p for p in picked if p is not None]
        if bad:
            problems.append('{}: {}.'.format(where, '; '.join(bad)))
            continue
        try:
            code = _entry(field(row, 'code'), where)
        except ValidationError as exc:
            problems.append(exc.message)
            continue
        if not code:
            problems.append('{}: no MandateCode.'.format(where))
            continue
        opts = options()
        for dim in GROUP_DIMENSIONS:
            chosen[dim] = [] if len(set(chosen[dim])) == len(opts[dim]) else \
                [v for v in opts[dim] if v in chosen[dim]]
        label = _text(field(row, 'name')) if not before else \
            ' · '.join(_label(d, chosen[d][0]) for d in GROUP_DIMENSIONS if chosen[d]) or 'Every portfolio'
        if not label:
            problems.append('{}: no Group name.'.format(where))
            continue
        group = dict({'name': label, 'code': code, 'row': n}, **chosen)
        held = names.get(label.lower())
        if held is not None:
            if before and held['code'] == code:
                continue
            problems.append('Rows {} and {} both give {}: codes {} and {}.'.format(
                held['row'], n, label, held['code'], code) if before else
                'Rows {} and {} are both the group {}.'.format(held['row'], n, label))
            continue
        if len(label) > NAME_MAX:
            problems.append('{}: keep the group name to {} characters.'.format(where, NAME_MAX))
            continue
        names[label.lower()] = group
        groups.append(group)
    if before:
        # the row naming more wins: it goes first
        groups.sort(key=lambda g: -sum(1 for d in GROUP_DIMENSIONS if g[d]))
        for i, a in enumerate(groups):
            for b in groups[i + 1:]:
                if sum(1 for d in GROUP_DIMENSIONS if a[d]) != sum(1 for d in GROUP_DIMENSIONS if b[d]):
                    break
                if a['code'] != b['code']:
                    both = next((k for k in cells() if holds(a, k) and holds(b, k)), None)
                    if both:
                        problems.append('Rows {} and {} both give {}: codes {} and {}.'.format(
                            a['row'], b['row'], cellWords(both), a['code'], b['code']))
    if count == 0:
        problems.append('{} holds no rows.'.format(name))
    table = {'groups': [{k: g[k] for k in ('name', 'code') + GROUP_DIMENSIONS} for g in groups]}
    for n, words in _idle(table['groups']):
        problems.append('Row {}: {}'.format(groups[n]['row'], words))
    return {'name': name, 'table': table, 'rows': count, 'problems': problems, 'before': before,
            'benchmarkColumn': 'benchmark' in {_squash(h) for h in head}}


def _changes(before, after) -> list:
    """What moved between two tables, in the words the console shows: each
    group that came, went or changed, their order, and how many portfolios
    take another code."""
    was, now = before['groups'], after['groups']
    wasBy = {g['name'].lower(): g for g in was}
    nowBy = {g['name'].lower(): g for g in now}
    out = []
    for g in now:
        old = wasBy.get(g['name'].lower())
        if old is None:
            out.append('Group {} added: {} · {}'.format(g['name'], g['code'], groupWords(g)))
            continue
        if old['name'] != g['name']:
            out.append('Group {} renamed {}'.format(old['name'], g['name']))
        if old['code'] != g['code']:
            out.append('{}: code {} → {}'.format(g['name'], old['code'] or '—', g['code'] or '—'))
        for dim in GROUP_DIMENSIONS:
            if list(old[dim]) != list(g[dim]):
                out.append('{}: {} {} → {}'.format(g['name'], _WORDS[dim], _dimWords(dim, old[dim]),
                                                  _dimWords(dim, g[dim])))
    for g in was:
        if g['name'].lower() not in nowBy:
            out.append('Group {} taken off ({} · {})'.format(g['name'], g['code'], groupWords(g)))
    shared = [g['name'].lower() for g in was if g['name'].lower() in nowBy]
    order = [g['name'].lower() for g in now if g['name'].lower() in wasBy]
    if shared != order:
        out.append('Group order: {} → {}'.format(', then '.join(g['name'] for g in was),
                                                  ', then '.join(g['name'] for g in now)))
    a, b = _codesOf(was), _codesOf(now)
    moved = sum(1 for c in cells() if a.get(c) != b.get(c))
    if moved:
        out.append('{} portfolio{} take{} another code'.format(moved, '' if moved == 1 else 's',
                                                              's' if moved == 1 else ''))
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
    table = _table(read['table']['groups'])
    return {'name': read['name'], 'rows': read['rows'], 'problems': read['problems'],
            'before': read['before'], 'benchmarkColumn': read['benchmarkColumn'],
            'groups': len(read['table']['groups']), 'cells': len(table['codes']),
            'changes': _summary(changes), 'changed': len(changes),
            'noCode': len(gaps(table)['noCode']), 'base': now['revision']}


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
    return _parse(existing['body']) if existing is not None else {'groups': _seeded()}


def _put(cleaned, action, note, user, base) -> dict:
    conn = sleeveRepo.connection()
    try:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        try:
            _refuseStale(conn, base)
            if cleaned['groups'] == _previous(conn)['groups']:
                raise ValidationError('groups', 'Those are the mandate codes already in force.')
            _write(conn, cleaned, action, note, user)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return current()
    finally:
        conn.close()


def save(body, note='', user='', base=None) -> dict:
    """Put groups edited on screen in force. Refused, naming the box, when a
    group is wrong, someone saved in between (*base*) or nothing would
    change."""
    return _put(normalise(body), 'updated', _note(note), user, base)


def importFile(name, content, note='', user='', base=None) -> dict:
    """Put a file's groups in force, whole. Refused while the file has a
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


def _revisions(conn) -> list:
    """[(row, groups, changes)] oldest first, the first against what was in
    force before it: the desk's groups, or - for a revision from before
    groups - nothing."""
    out, previous = [], None
    for r in _rows(conn):
        raw = json.loads(r['body']) if r['body'] else {}
        body = _parse(raw)
        if previous is None:
            previous = {'groups': _seeded()} if isinstance(raw.get('groups'), list) else {'groups': []}
        out.append((r, body, _changes(previous, body)))
        previous = body
    return out


def history() -> list:
    """Every revision, newest first, each with what it changed."""
    conn = sleeveRepo.connection()
    try:
        revisions = _revisions(conn)
    finally:
        conn.close()
    top = revisions[-1][0]['revision'] if revisions else None
    out = [{'revision': r['revision'], 'action': r['action'], 'note': r['note'], 'actor': r['actor'],
            'at': r['at'], 'changes': _summary(changes), 'changed': len(changes),
            'groups': len(body['groups']), 'current': r['revision'] == top}
           for r, body, changes in revisions]
    return list(reversed(out))


def feedRows(conn) -> list:
    """The record as the Activity feed reads it."""
    return [{'id': r['id'], 'revision': r['revision'], 'action': r['action'], 'note': r['note'],
             'actor': r['actor'], 'at': r['at'], 'changes': _summary(changes)}
            for r, body, changes in _revisions(conn)]


def revert(revision, note='', user='', base=None) -> dict:
    """Put an earlier revision's groups back in force, as a new revision -
    revision 0 the desk's two. A portfolio no longer on offer is left out."""
    note = _note(note)
    try:
        number = int(revision)
    except (TypeError, ValueError):
        raise ValidationError('revision', 'Name the revision by its number.')
    if number == 0:
        body = {'groups': _seeded()}
    else:
        conn = sleeveRepo.connection()
        try:
            row = conn.execute('SELECT * FROM policyHistory WHERE kind = ? AND scope = ? AND '
                               'revision = ?', (KIND, SCOPE, number)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ValidationError('revision', 'There is no revision {} of the mandate codes.'.format(number))
        body = _parse(row['body'])
    opts = options()
    body = {'groups': [dict(g, **{d: [v for v in g[d] if v in opts[d]] for d in GROUP_DIMENSIONS})
                       for g in body['groups']]}
    return _put(normalise(body), 'reverted', note or 'Restored revision {}'.format(number), user, base)


def exportRows() -> list:
    """The groups in force as the file they load from: the header, then a
    row a group, in order."""
    out = [list(COLUMNS)]
    for g in current()['groups']:
        out.append([g['name'], g['code']] + [', '.join(_label(d, v) if d == 'risks' else v for v in g[d]) or ALL
                                             for d in GROUP_DIMENSIONS])
    return out


def exportWorkbook() -> bytes:
    """``exportRows`` as an .xlsx with one sheet, Mandate Codes, ready to
    edit and load again."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    book = Workbook()
    sheet = book.active
    sheet.title = 'Mandate Codes'
    for row in exportRows():
        sheet.append(row)
    for c in sheet[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='0B2545')
    for letter, width in zip('ABCDEFG', (28, 16, 30, 30, 30, 16, 30)):
        sheet.column_dimensions[letter].width = width
    for row in sheet.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical='top')
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def describe() -> dict:
    """Everything the console's Mandate Codes view shows."""
    table = current()
    gap = gaps(table)
    opts = options()
    return dict(table, layout=layout(), hedgings=hedgings(), currencies=list(CURRENCIES),
                options={d: [{'value': v, 'label': _label(d, v)} for v in opts[d]] for d in GROUP_DIMENSIONS},
                seed=_seeded(), gaps=gap, cells=len(cells()), codeMax=CODE_MAX, nameMax=NAME_MAX,
                groupsMax=GROUPS_MAX, noteMax=NOTE_MAX)
