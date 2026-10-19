"""The product catalogue - the table every sleeve selects from (D58), kept in
the repository's own database and maintained from the console (D165).

It used to be read, read only, from a delivered extract (D56): a CSV or XLSX
named by SCENARIO_PRODUCTS_SOURCE, one row per product, one column per field -

    ProductId  Name  Ticker  AssetClass  Category  Style  Vehicle  ShareClass
    Source  Liquidity  ExposureCurrency  ProductCost  FeeGroup
    DistributionYield  MinimumInvestment

- Category, ShareClass and the last two optional in the file and blank or None
when absent (D63, D81, D178). The catalogue now lives in the repository's file beside the
sleeves (sleeveRepo, schema 7). That extract is read twice in its life: once,
on the first open after the upgrade, to fill the catalogue (``seed``); and
whenever an admin imports a file from the console or the command line, which
is previewed, product by product and field by field, before anything moves.
Changing the file on disk changes nothing by itself.

Nothing is ever destroyed (the sleeves' rule, D65). Every write appends a
revision holding the whole product as it stood after it, with who did it,
when, and their note; a product leaves the catalogue by being WITHDRAWN,
which keeps the row and its history and can be undone. A product a live
sleeve holds cannot be withdrawn - the refusal names the sleeves - and an id
is never reused. Each product carries the stamp of its current values: the
console calls it *Last edited* (who and when), beside the version number.

A product served from here carries exactly the fields a sleeve product always
carried (spec 4.1 minus the management fee, D51) so nothing downstream - the
implementation table, the workbook, the fee resolver - can tell where it came
from. An empty ticker is served as the em dash the table has always shown.
The console reads the same products with their stamps (``entries``).

What a product must be, checked at every save and on every row of an import:
a unique id, a name, a cost that is a number and not negative, a share class
from the pair or none, figures that are numbers and not negative, and a fee
group the fee card prices - the last matters most, since an unpriced group
would otherwise fail at export, under RDR, long after the product had been
placed in a sleeve. A save or an import also needs the six words a product is
described by (asset class, style, vehicle, source, liquidity, exposure
currency); the first load does not, so a file the tool used to read whole is
read whole once more on the day it moves in.

Reads are cheap: the catalogue is kept in memory against the file's stamp
(sleeveRepo.fileStamp) and re-read through a plain read-only connection when
the file moves, the way the overlay rules are (D155). A write from any
process is seen by the next read in every process.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import math
import os
import threading

from .csvText import unwrapRows
from .types import ValidationError

_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', '..', '..', '..', 'productSource', 'products.csv')

COLUMNS = ['ProductId', 'Name', 'Ticker', 'AssetClass', 'Style', 'Vehicle', 'Source',
           'Liquidity', 'ExposureCurrency', 'ProductCost', 'FeeGroup']
# Columns a delivery may leave out entirely, or leave blank on a row: each is
# served as None when it is absent, so an older extract still loads (D63, D81).
# Of these, the last two are figures the catalogue view compares on and must
# parse as numbers; the first is a word from a closed pair.
OPTIONAL = ['Category', 'ShareClass', 'DistributionYield', 'MinimumInvestment']
#: The product's own category (D178), the sleeve category the catalogue bands
#: it under: a file may head the column with any of these.
CATEGORY_HEADERS = ('Category', 'Catagory', 'Group')

#: What a share class can be: distributing, or accumulating (D81). Two values
#: and a blank, checked on load - a typo is refused rather than printed.
SHARE_CLASSES = ('Distributing', 'Accumulating')
#: The short forms a delivery used before the words were spelled out (D164),
#: still read, and served as the word.
SHARE_CLASS_SHORT = {'Dis': 'Distributing', 'Acc': 'Accumulating'}

# the field names the rest of the package has always used for a product
FIELDS = ['name', 'ticker', 'assetClass', 'style', 'vehicle', 'shareClass', 'source',
          'liquidity', 'exposureCurrency', 'productCost', 'feeGroup']

NO_TICKER = '—'

#: Every field a product carries, in the order the file and the console give
#: them: the key the package uses, the file's column, and the words a message
#: or a history line uses for it.
_FIELDS = [
    ('productId', 'ProductId', 'Product ID'),
    ('name', 'Name', 'Name'),
    ('ticker', 'Ticker', 'Ticker'),
    ('assetClass', 'AssetClass', 'Asset class'),
    ('category', 'Category', 'Category'),
    ('style', 'Style', 'Style'),
    ('vehicle', 'Vehicle', 'Vehicle'),
    ('shareClass', 'ShareClass', 'Share class'),
    ('source', 'Source', 'Source'),
    ('liquidity', 'Liquidity', 'Liquidity'),
    ('exposureCurrency', 'ExposureCurrency', 'Exposure currency'),
    ('productCost', 'ProductCost', 'Product cost'),
    ('feeGroup', 'FeeGroup', 'Fee group'),
    ('distributionYield', 'DistributionYield', 'Distribution yield'),
    ('minimumInvestment', 'MinimumInvestment', 'Minimum investment'),
]
KEYS = [k for k, _, _ in _FIELDS]
#: the file's columns in the order an export writes them - the packaged
#: file's own order
FILE_COLUMNS = [c for _, c, _ in _FIELDS]
LABELS = {k: label for k, _, label in _FIELDS}
_COLUMN_OF = {k: c for k, c, _ in _FIELDS}
#: the six words a product is described by: free values, which the console
#: keeps consistent by offering the ones already in use
WORDS = ['assetClass', 'style', 'vehicle', 'source', 'liquidity', 'exposureCurrency']
#: the figures, all percentages but the minimum, which is currency units
_PERCENT = ('productCost', 'distributionYield')

ID_MAX = 80
NAME_MAX = 120
TICKER_MAX = 24
WORD_MAX = 60
NOTE_MAX = 240
#: a figure at or above these is one typed in the wrong unit - 45 for 0.45
PERCENT_MAX = {'productCost': 10.0, 'distributionYield': 50.0}

#: what a revision's action can say
ACTIONS = ['seeded', 'created', 'updated', 'imported', 'withdrawn', 'restored', 'reverted']


class BadCatalogue(ValueError):
    """The catalogue cannot be read - the file it is loaded from, or the
    first load itself, failed."""


class StaleError(ValidationError):
    """The product (or the catalogue) a write was made against is no longer
    the version in force: someone saved since -> HTTP 409."""


def sourcePath() -> str:
    """The file the catalogue is first loaded from: SCENARIO_PRODUCTS_SOURCE,
    else the packaged stand-in."""
    return os.path.abspath(os.getenv('SCENARIO_PRODUCTS_SOURCE', '') or _DEFAULT)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


# ------------------------------------------------------------ the extract ---

def _readRows(path: str, data: bytes = None):
    """(header, rows...) from a CSV or an XLSX - a path, or the bytes of an
    upload named *path*. A workbook is read from its 'products' sheet when it
    has one (the data template keeps the products there), else its first."""
    if path.lower().endswith('.csv'):
        if data is None:
            # utf-8-sig drops a BOM from an Excel export; unwrapRows takes off
            # a pair of quotes wrapped around a whole row (csvText.py)
            with open(path, newline='', encoding='utf-8-sig') as fh:
                text = fh.read()
        else:
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                # a CSV saved from Excel on Windows is often its code page
                text = data.decode('cp1252')
        reader = csv.reader(unwrapRows(io.StringIO(text, newline='')))
        head = next(reader, None) or []
        yield [str(h).strip() for h in head]
        for row in reader:
            if row and str(row[0]).strip():
                yield row
        return
    from openpyxl import load_workbook
    book = load_workbook(io.BytesIO(data) if data is not None else path, read_only=True,
                         data_only=True)
    named = [s for s in book.sheetnames if s.strip().lower() == 'products']
    sheet = book[named[0]] if named else book.active
    rows = sheet.iter_rows(values_only=True)
    head = next(rows, None) or []
    yield [str(h).strip() if h is not None else '' for h in head]
    for row in rows:
        if row and row[0] is not None and str(row[0]).strip():
            yield ['' if v is None else v for v in row]


def _load(path: str, feeGroups=None, strict=False, data: bytes = None, categories=None):
    """The extract at *path* (or the upload *data* named *path*) as
    ({id: record}, [ids in file order]). Refuses the whole file with
    BadCatalogue at the first bad row, naming the row and the product: a
    file is loaded whole or not at all. *feeGroups*, when given, is the set
    a product's group must belong to, and *categories* the list its category
    must be one of. *strict* is the check a save gets
    (``clean``); without it, what the tool has always accepted from a file."""
    rows = _readRows(path, data)
    head = next(rows)
    name = os.path.basename(path)
    missing = [c for c in COLUMNS if c not in head]
    if missing:
        raise BadCatalogue('{}: missing column(s) {}'.format(name, ', '.join(missing)))
    index = {c: head.index(c) for c in COLUMNS}
    for c in OPTIONAL:
        if c in head:
            index[c] = head.index(c)
    for c in CATEGORY_HEADERS:
        if c in head:
            index['Category'] = head.index(c)
            break
    byId, order, rowOf = {}, [], {}
    for n, row in enumerate(rows, start=2):
        fields = {}
        for key, column in _COLUMN_OF.items():
            if column not in index:
                fields[key] = None
                continue
            at = index[column]
            fields[key] = row[at] if at < len(row) else ''
        pid = _text(fields['productId'], keepSpaces=True)
        if pid in byId:
            raise BadCatalogue('{}: row {}: the ProductId {} is already on row {} - each product '
                               'appears once.'.format(name, n, pid, rowOf[pid]))
        try:
            record = clean(fields, feeGroups, strict=strict, categories=categories)
        except ValidationError as exc:
            raise BadCatalogue('{}: row {} ({}): {}'.format(name, n, pid or 'no ProductId',
                                                            exc.message))
        byId[pid] = record
        rowOf[pid] = n
        order.append(pid)
    return byId, order


def readFile(path: str, strict=True):
    """The products in a file, checked as an import checks them against the
    fee card in force and the library's categories. ({id: record}, [ids in
    file order])."""
    from . import fees
    return _load(path, set(fees.feeGroups()), strict=strict, categories=knownCategories())


# ------------------------------------------------------------ the checks ---

def _text(value, keepSpaces=False) -> str:
    """A cell or a typed value as text: None is empty, a whole number from a
    spreadsheet loses its '.0', and the spacing is one space between words."""
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value)
    return text.strip() if keepSpaces else ' '.join(text.split())


def _figure(value, key, required=False, strict=True):
    """A number a product carries, or None for a blank one. A percentage may
    be typed with its sign and a large amount with its commas. *strict*
    also refuses a percentage typed in the wrong unit."""
    label = LABELS[key]
    example = ('a percentage, such as 0.25 for 0.25%' if key == 'productCost'
               else 'a percentage, such as 3.2' if key == 'distributionYield'
               else 'an amount, such as 5000000')
    if isinstance(value, bool):
        raise ValidationError(key, '{} must be a number: {}.'.format(label, example))
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValidationError(key, '{} is needed: {}.'.format(label, example))
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        shown = value
    else:
        shown = str(value).strip()
        text = shown.replace(',', '')
        if key in _PERCENT and text.endswith('%'):
            text = text[:-1].strip()
        try:
            number = float(text)
        except ValueError:
            raise ValidationError(key, '{} {!r} is not a number: give {}.'.format(
                label, shown, example))
    if math.isnan(number) or math.isinf(number):
        raise ValidationError(key, '{} {!r} is not a number: give {}.'.format(label, shown, example))
    if number < 0:
        raise ValidationError(key, '{} cannot be negative.'.format(label))
    if strict and key in _PERCENT and number >= PERCENT_MAX[key]:
        raise ValidationError(key, '{} of {:g}% looks wrong: it is a percentage, so 0.25 means '
                                   '0.25%.'.format(label, number))
    return number


def knownCategories() -> list:
    """The categories a product's own may be: the ones the library's sleeves
    are built for, in the implementation table's order (D178)."""
    from . import sleeveRepo
    return sleeveRepo.categories()


def clean(fields, feeGroups=None, strict=True, categories=None) -> dict:
    """A product as the catalogue stores it, from what was typed or read, or
    a ValidationError naming the first field that is wrong. *feeGroups*, when
    given, is the set the fee group must belong to, and *categories* the list
    a category must be one of. A ticker is '' when there is none, and so is a
    category, and a share class None."""
    fields = fields or {}
    pid = _text(fields.get('productId'), keepSpaces=True)
    if not pid:
        raise ValidationError('productId', 'Give the product an ID. Sleeves refer to products by '
                                           'it, and it cannot be changed later.')
    if strict and len(pid) > ID_MAX:
        raise ValidationError('productId', 'Keep the product ID to {} characters.'.format(ID_MAX))
    if any(ord(ch) < 32 for ch in pid):
        raise ValidationError('productId', 'A product ID cannot hold tabs or line breaks.')
    name = _text(fields.get('name'))
    if not name:
        raise ValidationError('name', 'Give the product a name.')
    if strict and len(name) > NAME_MAX:
        raise ValidationError('name', 'Keep the name to {} characters.'.format(NAME_MAX))
    ticker = _text(fields.get('ticker'))
    if ticker in (NO_TICKER, '-', '–'):
        ticker = ''
    if strict and len(ticker) > TICKER_MAX:
        raise ValidationError('ticker', 'Keep the ticker to {} characters.'.format(TICKER_MAX))
    record = {'productId': pid, 'name': name, 'ticker': ticker}
    for key in WORDS:
        word = _text(fields.get(key))
        if strict and not word:
            raise ValidationError(key, '{} is needed.'.format(LABELS[key]))
        if strict and len(word) > WORD_MAX:
            raise ValidationError(key, 'Keep the {} to {} characters.'.format(
                LABELS[key].lower(), WORD_MAX))
        record[key] = word
    # the sleeve category the catalogue bands the product under (D178): blank
    # for none, else one the library knows, spelled as it spells it
    category = _text(fields.get('category'))
    if category and categories is not None and category not in categories:
        same = [c for c in categories if c.lower() == category.lower()]
        if same:
            raise ValidationError('category', 'The category {!r} is spelled {}.'.format(
                category, same[0]))
        raise ValidationError('category', 'The category {!r} is not one the sleeves are built '
                                          'for: {}, or blank for none.'.format(
                                              category, ', '.join(categories)))
    record['category'] = category
    shareClass = _text(fields.get('shareClass'))
    shareClass = SHARE_CLASS_SHORT.get(shareClass, shareClass)
    if shareClass and shareClass not in SHARE_CLASSES:
        raise ValidationError('shareClass', 'The share class {!r} is not one of {}, or blank for '
                                            'none.'.format(shareClass, ', '.join(SHARE_CLASSES)))
    record['shareClass'] = shareClass or None
    record['productCost'] = _figure(fields.get('productCost'), 'productCost', required=True,
                                    strict=strict)
    group = _text(fields.get('feeGroup'))
    if not group:
        raise ValidationError('feeGroup', 'Choose a fee group: it is how the fee card prices '
                                          'the product.')
    if feeGroups is not None and group not in feeGroups:
        same = [g for g in feeGroups if g.lower() == group.lower()]
        if same:
            raise ValidationError('feeGroup', 'The fee group {!r} is not on the fee card: it is '
                                              'spelled {}.'.format(group, same[0]))
        raise ValidationError('feeGroup', 'The fee group {!r} is not on the fee card, which '
                                          'prices {}.'.format(group, ', '.join(sorted(feeGroups))))
    record['feeGroup'] = group
    record['distributionYield'] = _figure(fields.get('distributionYield'), 'distributionYield',
                                          strict=strict)
    record['minimumInvestment'] = _figure(fields.get('minimumInvestment'), 'minimumInvestment',
                                          strict=strict)
    return {k: record[k] for k in KEYS}


def _note(note) -> str:
    if note is not None and not isinstance(note, str):
        raise ValidationError('note', 'A note is words.')
    note = ' '.join((note or '').split())
    if len(note) > NOTE_MAX:
        raise ValidationError('note', 'Keep the note to {} characters.'.format(NOTE_MAX))
    return note


def show(key, value) -> str:
    """One field's value in the words a history line or a preview uses."""
    if value is None or value == '':
        return '—'
    if key == 'minimumInvestment':
        return '{:,.0f}'.format(value)
    if key in _PERCENT:
        return '{:g}'.format(value)
    return str(value)


def changes(before, after) -> list:
    """Field by field, what moved between two records: [{field, label,
    before, after}] in the order the console shows the fields."""
    if before is None:
        return []
    out = []
    for key in KEYS[1:]:
        was, now = before.get(key), after.get(key)
        if isinstance(was, float) or isinstance(now, float):
            if was is not None and now is not None and abs(was - now) <= 1e-12:
                continue
        if was == now or (was in (None, '') and now in (None, '')):
            continue
        out.append({'field': key, 'label': LABELS[key], 'before': show(key, was),
                    'after': show(key, now)})
    return out


def changeWords(entries) -> list:
    """The changes as the lines a history or the feed prints."""
    return ['{} {} → {}'.format(c['label'], c['before'], c['after']) for c in entries]


# ---------------------------------------------------------- the database ---

_lock = threading.Lock()
_cache = None            # (stamp, catalogue)
_seedProblem = None      # why the first load could not be made, if it could not


def _record(row) -> dict:
    """A stored row as the record ``clean`` returns."""
    return {
        'productId': row['productId'], 'name': row['name'], 'ticker': row['ticker'] or '',
        'assetClass': row['assetClass'], 'category': row['category'] or '',
        'style': row['style'], 'vehicle': row['vehicle'], 'shareClass': row['shareClass'] or None, 'source': row['source'],
        'liquidity': row['liquidity'], 'exposureCurrency': row['exposureCurrency'],
        'productCost': float(row['productCost']), 'feeGroup': row['feeGroup'],
        'distributionYield': None if row['distributionYield'] is None else float(row['distributionYield']),
        'minimumInvestment': None if row['minimumInvestment'] is None else float(row['minimumInvestment']),
    }


def _served(record) -> dict:
    """A record in the shape every consumer has always read. The product's
    own category is the catalogue's (D178): a sleeve product's category is
    its sleeve's, so it is not served beside it."""
    out = dict(record)
    out['ticker'] = record['ticker'] or NO_TICKER
    out.pop('category', None)
    return out


def _entry(row) -> dict:
    """A product as the console reads it: served fields, its category and
    its stamps."""
    out = _served(_record(row))
    out.update(category=row['category'] or '', revision=row['revision'], createdBy=row['createdBy'], createdAt=row['createdAt'],
               lastEditedBy=row['updatedBy'], lastEditedAt=row['updatedAt'],
               withdrawn=bool(row['withdrawnAt']), withdrawnBy=row['withdrawnBy'],
               withdrawnAt=row['withdrawnAt'])
    return out


def _meta(conn, key, default=''):
    row = conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
    return row[0] if row is not None else default


def _tableThere(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND "
                        "name = 'products'").fetchone() is not None


def _read(conn):
    """The catalogue on *conn*, or None when it has not been loaded yet."""
    if not _tableThere(conn) or not _meta(conn, 'productsSeededAt'):
        return None
    rows = conn.execute('SELECT * FROM products ORDER BY position, productId').fetchall()
    live, order, entries = {}, [], {}
    for row in rows:
        entries[row['productId']] = _entry(row)
        if not row['withdrawnAt']:
            live[row['productId']] = _served(_record(row))
            order.append(row['productId'])
    last = conn.execute('SELECT actor, at FROM productHistory ORDER BY at DESC, id DESC '
                        'LIMIT 1').fetchone()
    return {
        'live': live, 'order': order, 'entries': entries,
        'revision': int(_meta(conn, 'catalogueRevision', '0') or 0),
        'lastEditedAt': last['at'] if last else '', 'lastEditedBy': last['actor'] if last else '',
        'seededAt': _meta(conn, 'productsSeededAt'), 'seededFrom': _meta(conn, 'productsSeededFrom'),
    }


def _catalogue() -> dict:
    """The catalogue, from memory while the file has not moved. A file whose
    catalogue has not been loaded yet goes through the full connection,
    which loads it; if that cannot, BadCatalogue says why."""
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
                found = _read(conn)
            except Exception:                  # noqa: BLE001 - read again the full way
                found = None
            finally:
                conn.close()
    if found is None:
        conn = sleeveRepo.connection()
        try:
            stamp = sleeveRepo.fileStamp(path)
            found = _read(conn)
        finally:
            conn.close()
    if found is None:
        raise BadCatalogue(_seedProblem or 'The product catalogue has not been loaded.')
    with _lock:
        _cache = (stamp, found)
    return found


def _forget() -> None:
    """Drop the cached catalogue: after every write from this process."""
    global _cache
    with _lock:
        _cache = None


def reload() -> None:
    _forget()


# ----------------------------------------------------------------- reads ---

def all() -> list:                          # noqa: A001 - the catalogue, in its order
    """Every live product, served shape, in catalogue order."""
    cat = _catalogue()
    return [dict(cat['live'][pid]) for pid in cat['order']]


def get(productId: str):
    """One live product, or None. Callers that need a product to exist say so."""
    entry = _catalogue()['live'].get(productId)
    return dict(entry) if entry else None


def has(productId: str) -> bool:
    return productId in _catalogue()['live']


def entries(withdrawn=False) -> list:
    """The console's products, with their stamps: the live ones in catalogue
    order, or with *withdrawn* the withdrawn ones, most recently first."""
    cat = _catalogue()
    if not withdrawn:
        return [dict(cat['entries'][pid]) for pid in cat['order']]
    out = [dict(e) for e in cat['entries'].values() if e['withdrawn']]
    out.sort(key=lambda e: e['withdrawnAt'], reverse=True)
    return out


def entry(productId: str):
    """One product as the console reads it, live or withdrawn, or None."""
    found = _catalogue()['entries'].get(productId)
    return dict(found) if found else None


def describeSource() -> dict:
    """What the catalogue holds and when it last moved."""
    cat = _catalogue()
    from . import sleeveRepo
    return {
        'path': sleeveRepo.dbPath(),
        'products': len(cat['order']),
        'withdrawn': len(cat['entries']) - len(cat['order']),
        'revision': cat['revision'],
        'lastEditedAt': cat['lastEditedAt'], 'lastEditedBy': cat['lastEditedBy'],
        # the date the console's header gives the catalogue
        'modified': cat['lastEditedAt'],
        'seededAt': cat['seededAt'], 'seededFrom': cat['seededFrom'],
    }


def vocabulary() -> dict:
    """The values the console offers for each of the six words - every one a
    live product already uses, in A-Z order - with the share classes."""
    cat = _catalogue()
    out = {}
    for key in WORDS:
        seen = {p[key] for p in cat['live'].values() if p[key]}
        out[key] = sorted(seen, key=lambda v: v.lower())
    out['shareClass'] = list(SHARE_CLASSES)
    return out


def _holders(conn, productId) -> list:
    """The live sleeves that hold a product."""
    from . import sleeveRepo
    return [{'id': r['id'], 'variant': sleeveRepo.typesLabel(r['variant']),
             'variants': sleeveRepo.typesOf(r['variant']), 'category': r['category'],
             'name': r['name'], 'label': r['label']}
            for r in conn.execute(
                "SELECT s.id, s.variant, s.category, s.name, s.label FROM sleeveProducts p "
                "JOIN sleeves s ON s.id = p.sleeveId WHERE p.productId = ? AND s.deletedAt = '' "
                "ORDER BY s.variant, s.category, s.name, s.label", (productId,)).fetchall()]


def feeGroupUsage(conn=None) -> dict:
    """{fee group: [live product ids]} - what the fee card's groups price."""
    from . import sleeveRepo
    own = conn is None
    conn = conn or sleeveRepo.connection()
    try:
        out = {}
        for r in conn.execute("SELECT productId, feeGroup FROM products WHERE withdrawnAt = '' "
                              "ORDER BY position"):
            out.setdefault(r['feeGroup'], []).append(r['productId'])
        return out
    finally:
        if own:
            conn.close()


def history(productId: str) -> list:
    """Every revision of one product, newest first, each with the product as
    it stood and what moved from the one before it. Empty for an id the
    catalogue never held."""
    from . import sleeveRepo
    conn = sleeveRepo.connection()
    try:
        rows = conn.execute('SELECT * FROM productHistory WHERE productId = ? ORDER BY revision',
                            (productId,)).fetchall()
    finally:
        conn.close()
    out, previous = [], None
    top = rows[-1]['revision'] if rows else None
    for r in rows:
        body = json.loads(r['body'])
        moved = changes(previous, body)
        restoredFrom = None
        if r['action'] == 'reverted':
            restoredFrom = next((e['revision'] for e in reversed(out)
                                 if e['product'] == body), None)
        out.append({'productId': productId, 'revision': r['revision'], 'action': r['action'],
                    'product': body, 'changes': moved, 'changeWords': changeWords(moved),
                    'note': r['note'], 'source': r['source'], 'actor': r['actor'], 'at': r['at'],
                    'restoredFrom': restoredFrom, 'current': r['revision'] == top})
        previous = body
    out.reverse()
    return out


# ------------------------------------------------------------- the seed ---

def seed(conn, feeGroups) -> None:
    """Fill the catalogue from the product file, once (schema 7) - race-safe
    the way the library's seed is, asked again inside a write transaction.
    Called on every connection to the repository's file, before the
    library's own seed. A file that cannot be read leaves the catalogue
    unloaded and says why on every read, and the next connection tries
    again; with no fee card yet there is nothing to check a product's group
    against, so it waits for the card."""
    global _seedProblem
    if _meta(conn, 'productsSeededAt'):
        return
    if feeGroups is None:
        _seedProblem = 'The product catalogue waits for the fee card, which has not been loaded.'
        return
    path = sourcePath()
    try:
        byId, order = _load(path, set(feeGroups), strict=False)
    except FileNotFoundError:
        _seedProblem = '{}: the product file to load the catalogue from is not there.'.format(path)
        return
    except (BadCatalogue, OSError, ValueError) as exc:
        _seedProblem = 'The product catalogue could not be loaded from {}: {}'.format(path, exc)
        return
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    try:
        if _meta(conn, 'productsSeededAt'):
            conn.rollback()
            return
        stamp = _now()
        source = os.path.basename(path)
        for position, pid in enumerate(order):
            _insert(conn, byId[pid], position, 'seed', stamp)
            _history(conn, pid, 1, 'seeded', byId[pid], 'seed', stamp, source=source)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('productsSeededAt', ?)", (stamp,))
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('productsSeededFrom', ?)", (path,))
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('catalogueRevision', '1')")
        conn.commit()
        _seedProblem = None
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------- writes ---

def _insert(conn, record, position, user, stamp) -> None:
    conn.execute(
        'INSERT INTO products (productId, position, name, ticker, assetClass, category, style, '
        'vehicle, shareClass, source, liquidity, exposureCurrency, productCost, feeGroup, '
        'distributionYield, minimumInvestment, revision, createdBy, createdAt, updatedBy, '
        'updatedAt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)',
        (record['productId'], position, record['name'], record['ticker'], record['assetClass'],
         record.get('category') or '', record['style'], record['vehicle'], record['shareClass'] or '', record['source'],
         record['liquidity'], record['exposureCurrency'], record['productCost'],
         record['feeGroup'], record['distributionYield'], record['minimumInvestment'],
         user, stamp, user, stamp))


def _update(conn, record, revision, user, stamp) -> None:
    conn.execute(
        'UPDATE products SET name = ?, ticker = ?, assetClass = ?, category = ?, style = ?, '
        'vehicle = ?, shareClass = ?, source = ?, liquidity = ?, exposureCurrency = ?, '
        'productCost = ?, feeGroup = ?, distributionYield = ?, minimumInvestment = ?, '
        'revision = ?, updatedBy = ?, updatedAt = ? WHERE productId = ?',
        (record['name'], record['ticker'], record['assetClass'], record.get('category') or '',
         record['style'],
         record['vehicle'], record['shareClass'] or '', record['source'], record['liquidity'],
         record['exposureCurrency'], record['productCost'], record['feeGroup'],
         record['distributionYield'], record['minimumInvestment'], revision, user, stamp,
         record['productId']))


def _history(conn, productId, revision, action, record, user, stamp, note='', source='') -> None:
    conn.execute('INSERT INTO productHistory (productId, revision, action, body, note, source, '
                 'actor, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                 (productId, revision, action, json.dumps(record), note, source, user, stamp))


def _bump(conn) -> int:
    """The catalogue's own version: one more for every write, whatever it
    touched. A delivered proposal records the one it was priced with."""
    revision = int(_meta(conn, 'catalogueRevision', '0') or 0) + 1
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('catalogueRevision', ?)", (str(revision),))
    return revision


def _writing(work):
    """Run *work(conn)* holding the file's write lock from its first read to
    its commit (BEGIN IMMEDIATE), so a save's checks and its revision are one
    step and two saves cannot claim one revision. The cache is dropped after."""
    from . import sleeveRepo
    conn = sleeveRepo.connection()
    try:
        if not _meta(conn, 'productsSeededAt'):
            raise BadCatalogue(_seedProblem or 'The product catalogue has not been loaded.')
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


def _groupsOn(conn) -> set:
    from . import feeCard
    groups = feeCard.groupsOn(conn)
    if groups is None:
        raise ValidationError('feeGroup', 'The fee card has not been loaded, so no fee group '
                                          'can be checked.')
    return set(groups)


def _row(conn, productId):
    return conn.execute('SELECT * FROM products WHERE productId = ?', (productId,)).fetchone()


def _live(conn, productId):
    row = _row(conn, productId)
    if row is None:
        raise ValidationError('productId', 'There is no product {} in the catalogue.'.format(productId))
    if row['withdrawnAt']:
        raise ValidationError('productId', '{} was withdrawn by {} on {}. Restore it first.'.format(
            row['name'], row['withdrawnBy'] or 'someone', row['withdrawnAt'][:10]))
    return row


def _base(base):
    """The version a write was made against, or None when the caller did
    not say."""
    if base is None:
        return None
    if isinstance(base, str) and base.strip().isdigit():
        base = int(base)
    if isinstance(base, bool) or not isinstance(base, int):
        raise ValidationError('base', 'The version this change was made against is not a number.')
    return base


def _refuseStale(row, base) -> None:
    if base is not None and row['revision'] != base:
        raise StaleError('base', '{} was changed by {} at {} since you opened it (now version {}). '
                         'Reload to see the change, then make yours again.'.format(
                             row['name'], row['updatedBy'] or 'someone',
                             (row['updatedAt'] or '').replace('T', ' ')[:16], row['revision']))


def _entryOn(conn, productId) -> dict:
    return _entry(_row(conn, productId))


def create(fields, user='', note='') -> dict:
    """Add a product to the catalogue. Refused, naming the field, when it is
    not a product the catalogue can hold or its id is taken - by a live
    product or a withdrawn one, since an id is never reused."""
    note = _note(note)
    known = knownCategories()

    def work(conn):
        record = clean(fields, _groupsOn(conn), strict=True, categories=known)
        taken = _row(conn, record['productId'])
        if taken is not None:
            if taken['withdrawnAt']:
                raise ValidationError('productId', 'The ID {} belongs to {}, withdrawn on {}. An ID '
                                      'is never reused: restore that product, or choose another '
                                      'ID.'.format(record['productId'], taken['name'],
                                                   taken['withdrawnAt'][:10]))
            raise ValidationError('productId', 'The ID {} is already {}. Choose another.'.format(
                record['productId'], taken['name']))
        position = conn.execute('SELECT COALESCE(MAX(position), -1) + 1 FROM products').fetchone()[0]
        stamp = _now()
        _insert(conn, record, position, user or '', stamp)
        _history(conn, record['productId'], 1, 'created', record, user or '', stamp, note=note)
        _bump(conn)
        return _entryOn(conn, record['productId'])

    return _writing(work)


def update(productId, fields, user='', note='', base=None) -> dict:
    """Change a product's values. Its id is fixed: sleeves refer to it.
    Refused when a value is wrong, nothing would change, or - as STALE -
    *base* is not the version in force."""
    note = _note(note)
    base = _base(base)
    fields = dict(fields or {})
    asked = _text(fields.get('productId'), keepSpaces=True)
    if asked and asked != productId:
        raise ValidationError('productId', 'A product ID cannot be changed: sleeves refer to the '
                                           'product by it. Add a new product instead.')
    fields['productId'] = productId
    known = knownCategories()

    def work(conn):
        row = _live(conn, productId)
        _refuseStale(row, base)
        record = clean(fields, _groupsOn(conn), strict=True, categories=known)
        if not changes(_record(row), record):
            raise ValidationError('product', 'Nothing has changed: these are the values in force.')
        revision = row['revision'] + 1
        stamp = _now()
        _update(conn, record, revision, user or '', stamp)
        _history(conn, productId, revision, 'updated', record, user or '', stamp, note=note)
        _bump(conn)
        return _entryOn(conn, productId)

    return _writing(work)


def _refuseHeld(conn, row) -> None:
    held = _holders(conn, row['productId'])
    if held:
        named = ['{}{} ({} · {})'.format(h['name'], ' · ' + h['label'] if h['label'] else '',
                                         h['variant'], h['category']) for h in held]
        raise ValidationError('product', '{} is in {} sleeve{}: {}{}. Take it out of {} first; a '
                              'product a sleeve holds cannot be withdrawn.'.format(
                                  row['name'], len(held), '' if len(held) == 1 else 's',
                                  ', '.join(named[:5]), ', …' if len(named) > 5 else '',
                                  'it' if len(held) == 1 else 'them'))


def withdraw(productId, user='', note='', base=None) -> dict:
    """Take a product out of the catalogue. It keeps its row, its history
    and its id, appears under the console's withdrawn products, and can be
    restored. Refused while a live sleeve holds it."""
    note = _note(note)
    base = _base(base)

    def work(conn):
        row = _live(conn, productId)
        _refuseStale(row, base)
        _refuseHeld(conn, row)
        revision = row['revision'] + 1
        stamp = _now()
        conn.execute('UPDATE products SET withdrawnBy = ?, withdrawnAt = ?, revision = ?, '
                     'updatedBy = ?, updatedAt = ? WHERE productId = ?',
                     (user or '', stamp, revision, user or '', stamp, productId))
        _history(conn, productId, revision, 'withdrawn', _record(row), user or '', stamp, note=note)
        _bump(conn)
        return _entryOn(conn, productId)

    return _writing(work)


def restore(productId, user='', note='') -> dict:
    """Put a withdrawn product back, as it was. Refused when it could not be
    saved today - its fee group has left the fee card."""
    note = _note(note)

    def work(conn):
        row = _row(conn, productId)
        if row is None:
            raise ValidationError('productId', 'There is no product {} in the catalogue.'.format(productId))
        if not row['withdrawnAt']:
            raise ValidationError('productId', '{} is in the catalogue already.'.format(row['name']))
        record = _record(row)
        groups = _groupsOn(conn)
        if record['feeGroup'] not in groups:
            raise ValidationError('feeGroup', '{} is priced by the fee group {}, which is no longer '
                                  'on the fee card. Add the group back on the Fees tab, then restore '
                                  'it.'.format(record['name'], record['feeGroup']))
        revision = row['revision'] + 1
        stamp = _now()
        conn.execute("UPDATE products SET withdrawnBy = '', withdrawnAt = '', revision = ?, "
                     'updatedBy = ?, updatedAt = ? WHERE productId = ?',
                     (revision, user or '', stamp, productId))
        _history(conn, productId, revision, 'restored', record, user or '', stamp, note=note)
        _bump(conn)
        return _entryOn(conn, productId)

    return _writing(work)


def revert(productId, revision, user='', note='', base=None) -> dict:
    """Put an earlier version's values back in force, as a new version -
    checked again, since the fee card may have moved since it was saved."""
    note = _note(note)
    base = _base(base)
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValidationError('revision', 'Choose a version to restore.')

    def work(conn):
        row = _live(conn, productId)
        _refuseStale(row, base)
        held = conn.execute('SELECT * FROM productHistory WHERE productId = ? AND revision = ?',
                            (productId, revision)).fetchone()
        if held is None:
            raise ValidationError('revision', 'There is no version {} of {}.'.format(revision, row['name']))
        record = clean(json.loads(held['body']), _groupsOn(conn), strict=False)
        if not changes(_record(row), record):
            raise ValidationError('revision', 'Those are the values in force already.')
        top = row['revision'] + 1
        stamp = _now()
        _update(conn, record, top, user or '', stamp)
        _history(conn, productId, top, 'reverted', record, user or '', stamp,
                 note=note or 'Restored version {}'.format(revision))
        _bump(conn)
        return _entryOn(conn, productId)

    return _writing(work)


def renameFeeGroup(conn, old, new, user, stamp, note) -> int:
    """Move every product priced by the fee group *old* to *new*, inside the
    fee card's own save (feeCard.save), one revision each. Returns how many
    products moved."""
    rows = conn.execute('SELECT * FROM products WHERE feeGroup = ? ORDER BY position',
                        (old,)).fetchall()
    for row in rows:
        record = _record(row)
        record['feeGroup'] = new
        revision = row['revision'] + 1
        conn.execute('UPDATE products SET feeGroup = ?, revision = ?, updatedBy = ?, '
                     'updatedAt = ? WHERE productId = ?',
                     (new, revision, user or '', stamp, row['productId']))
        _history(conn, row['productId'], revision, 'updated', record, user or '', stamp,
                 note=note)
    if rows:
        _bump(conn)
    return len(rows)


# ------------------------------------------------------- import / export ---

def _decode(content) -> bytes:
    """An upload's bytes, sent as base64 text so the console needs no
    multipart support on the server."""
    import base64
    import binascii
    if isinstance(content, bytes):
        return content
    if not isinstance(content, str) or not content:
        raise ValidationError('file', 'Choose a product file to import.')
    try:
        return base64.b64decode(content.split(',', 1)[-1] if content.startswith('data:') else content,
                                validate=False)
    except (binascii.Error, ValueError):
        raise ValidationError('file', 'The file could not be read.')


def _fileName(name) -> str:
    name = os.path.basename(str(name or '').replace('\\', '/')).strip()
    if not name.lower().endswith(('.csv', '.xlsx')):
        raise ValidationError('file', 'A product file is a .csv or an .xlsx.')
    return name


def _compare(conn, byId, order) -> dict:
    """What a file would do to the catalogue on *conn*."""
    rows = {r['productId']: r for r in conn.execute('SELECT * FROM products ORDER BY position')}
    added, changed, back, unchanged = [], [], [], 0
    for pid in order:
        incoming, row = byId[pid], rows.get(pid)
        if row is None:
            added.append({'productId': pid, 'name': incoming['name']})
            continue
        moved = changes(_record(row), incoming)
        if row['withdrawnAt']:
            back.append({'productId': pid, 'name': incoming['name'], 'changes': moved,
                         'changeWords': changeWords(moved)})
        elif moved:
            changed.append({'productId': pid, 'name': incoming['name'], 'changes': moved,
                            'changeWords': changeWords(moved)})
        else:
            unchanged += 1
    missing = []
    for pid, row in rows.items():
        if pid in byId or row['withdrawnAt']:
            continue
        held = _holders(conn, pid)
        missing.append({'productId': pid, 'name': row['name'], 'sleeves': held,
                        'canWithdraw': not held})
    return {'added': added, 'changed': changed, 'restored': back, 'unchanged': unchanged,
            'missing': missing}


def importPreview(name, content) -> dict:
    """What importing a file would do, without doing it: the products it
    adds, the ones it changes (field by field), the withdrawn ones it brings
    back, how many it leaves as they are, and the live ones it does not
    carry - kept unless the admin ticks them to be withdrawn, which a
    product a sleeve holds cannot be. A file with any bad row is refused
    whole, naming the row. *revision* is the catalogue version the preview
    was made against: the import is refused if it has moved since."""
    from . import sleeveRepo
    name = _fileName(name)
    data = _decode(content)
    known = knownCategories()
    conn = sleeveRepo.connection()
    try:
        groups = _groupsOn(conn)
        byId, order = _load(name, groups, strict=True, data=data, categories=known)
        out = _compare(conn, byId, order)
        out.update(file=name, rows=len(order),
                   revision=int(_meta(conn, 'catalogueRevision', '0') or 0))
        return out
    finally:
        conn.close()


def importFile(name, content, user='', withdraw=None, note='', base=None) -> dict:
    """Apply a file: the new products added, the changed ones updated, the
    withdrawn ones it carries put back with its values - each one revision
    marked *imported*, naming the file - and the products ticked in
    *withdraw* withdrawn. Products the file leaves unchanged are not
    touched, so their Last edited stays true. All or nothing, and refused
    as STALE when *base* is not the catalogue version the preview showed."""
    name = _fileName(name)
    data = _decode(content)
    note = _note(note)
    base = _base(base)
    withdraw = [str(p) for p in (withdraw or [])]
    known = knownCategories()

    def work(conn):
        now = int(_meta(conn, 'catalogueRevision', '0') or 0)
        if base is not None and base != now:
            last = conn.execute('SELECT actor, at FROM productHistory ORDER BY at DESC, id DESC '
                                'LIMIT 1').fetchone()
            raise StaleError('base', 'The catalogue changed since this preview{} (now version {}). '
                             'Preview the file again.'.format(
                                 ' - {} at {}'.format(last['actor'] or 'someone',
                                                      last['at'].replace('T', ' ')[:16]) if last else '',
                                 now))
        byId, order = _load(name, _groupsOn(conn), strict=True, data=data, categories=known)
        plan = _compare(conn, byId, order)
        missing = {m['productId']: m for m in plan['missing']}
        for pid in withdraw:
            if pid not in missing:
                raise ValidationError('withdraw', '{} is not a product this file leaves out.'.format(pid))
            if not missing[pid]['canWithdraw']:
                _refuseHeld(conn, _row(conn, pid))
        if not (plan['added'] or plan['changed'] or plan['restored'] or withdraw):
            raise ValidationError('file', 'The file matches the catalogue: there is nothing to '
                                          'import.')
        stamp = _now()
        position = conn.execute('SELECT COALESCE(MAX(position), -1) + 1 FROM products').fetchone()[0]
        for item in plan['added']:
            record = byId[item['productId']]
            _insert(conn, record, position, user or '', stamp)
            _history(conn, record['productId'], 1, 'imported', record, user or '', stamp,
                     note=note, source=name)
            position += 1
        for item in plan['changed'] + plan['restored']:
            row = _row(conn, item['productId'])
            record = byId[item['productId']]
            revision = row['revision'] + 1
            _update(conn, record, revision, user or '', stamp)
            if row['withdrawnAt']:
                conn.execute("UPDATE products SET withdrawnBy = '', withdrawnAt = '' "
                             'WHERE productId = ?', (row['productId'],))
            _history(conn, record['productId'], revision, 'imported', record, user or '', stamp,
                     note=note, source=name)
        for pid in withdraw:
            row = _row(conn, pid)
            revision = row['revision'] + 1
            conn.execute('UPDATE products SET withdrawnBy = ?, withdrawnAt = ?, revision = ?, '
                         'updatedBy = ?, updatedAt = ? WHERE productId = ?',
                         (user or '', stamp, revision, user or '', stamp, pid))
            _history(conn, pid, revision, 'withdrawn', _record(row), user or '', stamp,
                     note=note or 'Not in {}'.format(name), source=name)
        revision = _bump(conn)
        return {'file': name, 'added': len(plan['added']), 'changed': len(plan['changed']),
                'restored': len(plan['restored']), 'withdrawn': len(withdraw),
                'unchanged': plan['unchanged'], 'revision': revision}

    return _writing(work)


def _cell(value):
    """A value as an export writes it: a whole number without its '.0', any
    other number at full precision, a blank as nothing."""
    if value is None:
        return ''
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return value


def exportRows() -> list:
    """The live catalogue as the file it can be loaded from again: the header
    and one row per product, in catalogue order."""
    out = [list(FILE_COLUMNS)]
    for p in entries():
        out.append([_cell('' if key == 'ticker' and p[key] == NO_TICKER else p[key])
                    for key in KEYS])
    return out


def exportCsv() -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows(exportRows())
    return buf.getvalue()
