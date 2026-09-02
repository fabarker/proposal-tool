"""The sleeve repository - the library, in the tool's own database (D57).

Sleeves used to be tables in sleeves.py. They are now rows in a SQLite file
the service owns and writes, built and maintained by an admin from inside the
app. Two tables:

    sleeves         id, variant, category, name, note, provenance
                    unique on (variant, category, name)
    sleeveProducts  sleeveId, position, productId, weight   (weight a fraction)

A sleeve references products by id; the products themselves live in the
delivered catalogue (products.py, D56) and are joined in when a sleeve is
served. That join is where a sleeve can fail: a new product extract that no
longer carries a product leaves every sleeve holding it *broken*. A broken
sleeve stays in the repository, flagged, but is withheld from listSleeves -
a sleeve that cannot be priced is never offered to a PWA.

Seeding. A database that does not exist yet is created and filled from the
seed extract (SCENARIO_SLEEVES_SEED; the packaged default is the stand-in
under proposal-tool/sleeveSource, today's library written out row by row).
Once the database exists the seed is never read again. The same table shape
is the interchange format - exportRows / importRows, driven by sleeveTools.

Validation happens at save, not import: weights sum to 1, at least one product,
no product twice, every product in the catalogue, a unique name within its
type and category, and the two fixed categories - the ones an implementation
toggle attaches automatically - holding exactly one sleeve each. Every failure
is a ValidationError naming the field, which the router turns into the
{error, field} body the page already knows how to show.

Reads open a connection per call. The library is a few hundred rows; there is
nothing to cache and nothing to invalidate, and a save from one admin is seen
by the next request from anyone.
"""

from __future__ import annotations

import csv
import datetime
import os
import sqlite3
import threading

from . import products
from .types import ValidationError

# The order the UI offers implementation types in. First is not a default -
# nothing is selected until a PWA selects it (spec 8.1). Configuration, not
# data: a fifth type is a line here, and sleeves for it are rows in the store.
VARIANTS = [
    'PMG Multi-Asset Portfolio',
    'PMG ESG',
    'US Onshore',
    'Irish Onshore',
]

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '..', '..', '..', 'var', 'sleeves.db')
_DEFAULT_SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '..', '..', '..', '..', 'sleeveSource', 'sleeves.csv')

SEED_COLUMNS = ['Variant', 'Category', 'Sleeve', 'ProductId', 'Weight']
NAME_MAX = 80
WEIGHT_TOLERANCE = 1e-6

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sleeves (
    id        INTEGER PRIMARY KEY,
    variant   TEXT NOT NULL,
    category  TEXT NOT NULL,
    name      TEXT NOT NULL,
    note      TEXT NOT NULL DEFAULT '',
    createdBy TEXT NOT NULL DEFAULT '',
    createdAt TEXT NOT NULL,
    updatedBy TEXT NOT NULL DEFAULT '',
    updatedAt TEXT NOT NULL,
    UNIQUE (variant, category, name)
);
CREATE TABLE IF NOT EXISTS sleeveProducts (
    sleeveId  INTEGER NOT NULL REFERENCES sleeves(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    productId TEXT NOT NULL,
    weight    REAL NOT NULL,
    PRIMARY KEY (sleeveId, productId)
);
CREATE INDEX IF NOT EXISTS sleeves_by_home ON sleeves (variant, category);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_lock = threading.Lock()


def dbPath() -> str:
    return os.path.abspath(os.getenv('SCENARIO_SLEEVES_DB', '') or _DEFAULT_DB)


def seedPath() -> str:
    return os.path.abspath(os.getenv('SCENARIO_SLEEVES_SEED', '') or _DEFAULT_SEED)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


# ------------------------------------------------------------ vocabulary ---

def variantExists(variant) -> bool:
    return variant in VARIANTS


def fixedCategories() -> list:
    """The categories an implementation toggle attaches a sleeve to
    automatically. Each holds exactly one sleeve per type, and the rules that
    attach it depend on that. Read from rules lazily - rules imports the
    sleeve facade, so a module-level import here would be circular."""
    from .rules import AUTO_SLEEVE_CATEGORIES
    return list(AUTO_SLEEVE_CATEGORIES)


def categories() -> list:
    """Every category a sleeve is built for, in the implementation table's own
    order: the universe's categories, with the volatility premium's category
    after Investment Grade Fixed Income and the tilt's last - where the table
    puts them (rules.applyTacticalTilt / applyVolPremium) - and grouped
    categories collapsed to the one name they share a sleeve under (D60)."""
    from .rules import (TACTICAL_TILT_CATEGORY, VOL_PREMIUM_CATEGORY,
                        VOL_PREMIUM_FUNDED_FROM, categoriesInUniverseOrder,
                        sleeveCategory)
    out = []
    for name in categoriesInUniverseOrder():
        out.append(name)
        if name == VOL_PREMIUM_FUNDED_FROM and VOL_PREMIUM_CATEGORY not in out:
            out.append(VOL_PREMIUM_CATEGORY)
    for extra in (VOL_PREMIUM_CATEGORY, TACTICAL_TILT_CATEGORY):
        if extra not in out:
            out.append(extra)
    grouped = []
    for name in out:
        name = sleeveCategory(name)
        if name not in grouped:
            grouped.append(name)
    return grouped


# ------------------------------------------------------------ connection ---

def _connect() -> sqlite3.Connection:
    """A connection with the schema in place and, the first time, the seed
    loaded. Callers close it; use it as a context manager for the commit."""
    path = dbPath()
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    with _lock:
        conn.executescript(_SCHEMA)
        count = conn.execute('SELECT COUNT(*) FROM sleeves').fetchone()[0]
        seeded = conn.execute("SELECT value FROM meta WHERE key = 'seededAt'").fetchone()
        if count == 0 and seeded is None:
            _seed(conn)
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    path = seedPath()
    stamp = _now()
    if not os.path.exists(path):
        # an empty library is a legitimate start; record that it was one
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('seededAt', ?)", (stamp,))
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('seededFrom', '')")
        conn.commit()
        return
    rows = list(readSeedRows(path))
    _importInto(conn, rows, replace=False, user='seed', strict=False)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('seededAt', ?)", (stamp,))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('seededFrom', ?)", (path,))
    conn.commit()


# ------------------------------------------------------------- the seed ---

def readSeedRows(path: str):
    """Rows of the interchange shape from a CSV or XLSX:
    (variant, category, sleeve, productId, weight)."""
    def _clean(row):
        cells = ['' if v is None else str(v).strip() for v in row[:5]]
        while len(cells) < 5:
            cells.append('')
        try:
            cells[4] = float(cells[4])
        except ValueError:
            raise ValueError('{}: weight {!r} is not a number'.format(' / '.join(cells[:3]), cells[4]))
        return tuple(cells)

    if path.lower().endswith('.csv'):
        with open(path, newline='', encoding='utf-8') as fh:
            reader = csv.reader(fh)
            head = [str(h).strip() for h in (next(reader, None) or [])]
            if head[:5] != SEED_COLUMNS:
                raise ValueError('{}: header is {!r}, expected {!r}'.format(path, head, SEED_COLUMNS))
            for row in reader:
                if row and str(row[0]).strip():
                    yield _clean(row)
        return
    from openpyxl import load_workbook
    sheet = load_workbook(path, read_only=True).active
    rows = sheet.iter_rows(values_only=True)
    head = [str(h).strip() if h is not None else '' for h in (next(rows, None) or [])]
    if head[:5] != SEED_COLUMNS:
        raise ValueError('{}: header is {!r}, expected {!r}'.format(path, head, SEED_COLUMNS))
    for row in rows:
        if row and row[0] is not None and str(row[0]).strip():
            yield _clean(row)


# ------------------------------------------------------------ validation ---

def _validate(conn, variant, category, name, note, productRows, sleeveId=None):
    """Everything a sleeve has to satisfy to be saved. Raises the first
    failure as a ValidationError naming the field; returns the cleaned
    (name, note, [(productId, weight)])."""
    if not variantExists(variant):
        raise ValidationError('variant', 'Choose an implementation type.')
    if category not in categories():
        raise ValidationError('category', '{!r} is not a category a sleeve can implement.'.format(category))
    name = (name or '').strip()
    if not name:
        raise ValidationError('name', 'Give the sleeve a name.')
    if len(name) > NAME_MAX:
        raise ValidationError('name', 'Keep the name to {} characters.'.format(NAME_MAX))
    note = (note or '').strip()

    clash = conn.execute(
        'SELECT id FROM sleeves WHERE variant = ? AND category = ? AND name = ?',
        (variant, category, name)).fetchone()
    if clash and clash['id'] != sleeveId:
        raise ValidationError(
            'name', 'A sleeve named {} already exists in {} under {}.'.format(name, category, variant))

    if category in fixedCategories():
        others = conn.execute(
            'SELECT COUNT(*) FROM sleeves WHERE variant = ? AND category = ? AND id IS NOT ?',
            (variant, category, sleeveId)).fetchone()[0]
        if others:
            raise ValidationError(
                'category', '{} holds exactly one sleeve under each implementation type - '
                'it is attached automatically by its toggle. Edit the one it has.'.format(category))

    cleaned, seen, total = [], set(), 0.0
    for entry in productRows or []:
        pid = str(entry.get('productId', '') if isinstance(entry, dict) else entry[0]).strip()
        raw = entry.get('weight') if isinstance(entry, dict) else entry[1]
        if not pid:
            raise ValidationError('products', 'Every row needs a product from the catalogue.')
        if pid in seen:
            raise ValidationError('products', 'A product can appear in a sleeve once.')
        if not products.has(pid):
            raise ValidationError('products', 'Product {!r} is not in the catalogue.'.format(pid))
        try:
            weight = float(raw)
        except (TypeError, ValueError):
            raise ValidationError('weights', 'Enter a weight for every product.')
        if weight <= 0:
            raise ValidationError('weights', 'Weights must be greater than zero.')
        seen.add(pid)
        cleaned.append((pid, weight))
        total += weight
    if not cleaned:
        raise ValidationError('products', 'A sleeve needs at least one product.')
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        raise ValidationError(
            'weights', 'Weights sum to {:.2f}%; a sleeve must sum to 100%.'.format(total * 100.0))
    return name, note, cleaned


# --------------------------------------------------------------- reading ---

def _sleeveRows(conn, variant=None, category=None):
    sql = 'SELECT * FROM sleeves'
    clauses, args = [], []
    if variant is not None:
        clauses.append('variant = ?'); args.append(variant)
    if category is not None:
        clauses.append('category = ?'); args.append(category)
    if clauses:
        sql += ' WHERE ' + ' AND '.join(clauses)
    sql += ' ORDER BY variant, category, id'
    return conn.execute(sql, args).fetchall()


def _productRows(conn, sleeveId):
    return conn.execute(
        'SELECT productId, weight, position FROM sleeveProducts WHERE sleeveId = ? ORDER BY position',
        (sleeveId,)).fetchall()


def _served(row, productRows):
    """The shape a sleeve has always been served in - name and fully hydrated
    products - or None when a product is missing from the catalogue."""
    served = []
    for pr in productRows:
        product = products.get(pr['productId'])
        if product is None:
            return None
        product['weight'] = pr['weight']
        served.append(product)
    return {'id': row['id'], 'name': row['name'], 'note': row['note'], 'products': served}


def _entry(conn, row, productRows):
    """The console's view of a sleeve: everything, including what is wrong."""
    problems = []
    hydrated = []
    total = 0.0
    for pr in productRows:
        product = products.get(pr['productId'])
        if product is None:
            problems.append('Product {!r} is no longer in the catalogue.'.format(pr['productId']))
        hydrated.append({'productId': pr['productId'], 'weight': pr['weight'],
                         'position': pr['position'], 'product': product})
        total += pr['weight']
    if not productRows:
        problems.append('The sleeve has no products.')
    elif abs(total - 1.0) > WEIGHT_TOLERANCE:
        problems.append('Weights sum to {:.2f}%, not 100%.'.format(total * 100.0))
    offeredUnder = [r['variant'] for r in conn.execute(
        'SELECT variant FROM sleeves WHERE category = ? AND name = ? ORDER BY id',
        (row['category'], row['name'])).fetchall()]
    return {
        'id': row['id'], 'variant': row['variant'], 'category': row['category'],
        'name': row['name'], 'note': row['note'],
        'fixed': row['category'] in fixedCategories(),
        'products': hydrated, 'problems': problems, 'offeredUnder': offeredUnder,
        'createdBy': row['createdBy'], 'createdAt': row['createdAt'],
        'updatedBy': row['updatedBy'], 'updatedAt': row['updatedAt'],
    }


def listSleeves(category: str, variant: str) -> list:
    """The library for one category under one type, in the shape every
    consumer has always read: well-formed sleeves only.

    An unknown type returns nothing rather than falling back: the caller has
    to have chosen one, and quietly serving the Multi-Asset library to a book
    that cannot hold it is the worse failure. An empty list is also a
    legitimate answer for a known type - it reaches no sleeve for that
    category, or every sleeve it has is broken - and the completeness gate
    reports it as such."""
    if not variantExists(variant):
        return []
    conn = _connect()
    try:
        out = []
        for row in _sleeveRows(conn, variant, category):
            served = _served(row, _productRows(conn, row['id']))
            if served is not None:
                out.append(served)
        return out
    finally:
        conn.close()


def sleeveExists(category: str, name, variant: str) -> bool:
    return any(s['name'] == name for s in listSleeves(category, variant))


def listAll(variant: str = None) -> list:
    """Every sleeve, broken ones included, for the console and the census."""
    conn = _connect()
    try:
        return [_entry(conn, row, _productRows(conn, row['id']))
                for row in _sleeveRows(conn, variant)]
    finally:
        conn.close()


def getSleeve(sleeveId: int):
    conn = _connect()
    try:
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        return _entry(conn, row, _productRows(conn, row['id'])) if row else None
    finally:
        conn.close()


def describe() -> dict:
    conn = _connect()
    try:
        meta = {r['key']: r['value'] for r in conn.execute('SELECT key, value FROM meta')}
        count = conn.execute('SELECT COUNT(*) FROM sleeves').fetchone()[0]
        return {'path': dbPath(), 'sleeves': count,
                'seededFrom': meta.get('seededFrom', ''), 'seededAt': meta.get('seededAt', '')}
    finally:
        conn.close()


# --------------------------------------------------------------- writing ---

def _writeProducts(conn, sleeveId, cleaned):
    conn.execute('DELETE FROM sleeveProducts WHERE sleeveId = ?', (sleeveId,))
    conn.executemany(
        'INSERT INTO sleeveProducts (sleeveId, position, productId, weight) VALUES (?, ?, ?, ?)',
        [(sleeveId, position, pid, weight) for position, (pid, weight) in enumerate(cleaned)])


def createSleeves(variants, category, name, productRows, note='', user='') -> list:
    """The same sleeve under one or more implementation types at once.

    One definition applied to several books is the ordinary case - a sleeve
    the desk offers under Multi-Asset and ESG alike - and doing it as one
    operation is what makes it safe: every type is validated before any is
    written, so a name that already exists in one book leaves nothing
    half-applied. Returns the sleeves created, in VARIANTS order.
    """
    requested = list(dict.fromkeys(variants or []))
    if not requested:
        raise ValidationError('variants', 'Choose at least one implementation type.')
    for variant in requested:
        # named and unknown, not quietly dropped: a caller that asks for a
        # type that does not exist has made a mistake worth hearing about
        if not variantExists(variant):
            raise ValidationError('variant', '{!r} is not an implementation type.'.format(variant))
    ordered = [v for v in VARIANTS if v in set(requested)]
    conn = _connect()
    try:
        checked = []
        for variant in ordered:
            checked.append((variant,) + _validate(conn, variant, category, name, note, productRows))
        stamp = _now()
        made = []
        with conn:
            for variant, cleanName, cleanNote, cleaned in checked:
                cur = conn.execute(
                    'INSERT INTO sleeves (variant, category, name, note, createdBy, createdAt, '
                    'updatedBy, updatedAt) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (variant, category, cleanName, cleanNote, user, stamp, user, stamp))
                _writeProducts(conn, cur.lastrowid, cleaned)
                made.append(cur.lastrowid)
        out = []
        for sleeveId in made:
            row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
            out.append(_entry(conn, row, _productRows(conn, row['id'])))
        return out
    finally:
        conn.close()


def createSleeve(variant, category, name, productRows, note='', user='') -> dict:
    """One type. The single-book case of createSleeves."""
    return createSleeves([variant], category, name, productRows, note=note, user=user)[0]


def updateSleeve(sleeveId, name, productRows, note='', user='') -> dict:
    conn = _connect()
    try:
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        if row is None:
            raise ValidationError('id', 'That sleeve no longer exists.')
        name, note, cleaned = _validate(conn, row['variant'], row['category'], name, note,
                                        productRows, sleeveId=sleeveId)
        with conn:
            conn.execute('UPDATE sleeves SET name = ?, note = ?, updatedBy = ?, updatedAt = ? '
                         'WHERE id = ?', (name, note, user, _now(), sleeveId))
            _writeProducts(conn, sleeveId, cleaned)
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        return _entry(conn, row, _productRows(conn, row['id']))
    finally:
        conn.close()


def deleteSleeve(sleeveId, user='') -> dict:
    conn = _connect()
    try:
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        if row is None:
            raise ValidationError('id', 'That sleeve no longer exists.')
        if row['category'] in fixedCategories():
            raise ValidationError(
                'category', '{} is attached automatically by its toggle and always holds one '
                'sleeve. Edit it rather than deleting it.'.format(row['category']))
        with conn:
            conn.execute('DELETE FROM sleeves WHERE id = ?', (sleeveId,))
        return {'id': sleeveId, 'variant': row['variant'], 'category': row['category'],
                'name': row['name']}
    finally:
        conn.close()


# ----------------------------------------------------------- interchange ---

def exportRows() -> list:
    """The whole library in the interchange shape, one row per product."""
    conn = _connect()
    try:
        out = []
        for row in _sleeveRows(conn):
            for pr in _productRows(conn, row['id']):
                out.append((row['variant'], row['category'], row['name'],
                            pr['productId'], pr['weight']))
        return out
    finally:
        conn.close()


def _importInto(conn, rows, replace, user, strict):
    """Load interchange rows. Grouped by (variant, category, sleeve); a group
    is one sleeve. With *strict* every group goes through the same validation
    as a save and the first failure aborts the whole load; the seed loads
    non-strict, so a stand-in whose products drifted still comes up - broken
    sleeves are what the census is for."""
    grouped, order = {}, []
    for variant, category, name, pid, weight in rows:
        key = (variant, category, name)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append({'productId': pid, 'weight': weight})
    stamp = _now()
    with conn:
        if replace:
            conn.execute('DELETE FROM sleeves')
        for key in order:
            variant, category, name = key
            productRows = grouped[key]
            if strict:
                name, _, cleaned = _validate(conn, variant, category, name, '', productRows)
            else:
                cleaned = [(p['productId'], float(p['weight'])) for p in productRows]
            existing = conn.execute(
                'SELECT id FROM sleeves WHERE variant = ? AND category = ? AND name = ?',
                (variant, category, name)).fetchone()
            if existing:
                sleeveId = existing['id']
                conn.execute('UPDATE sleeves SET updatedBy = ?, updatedAt = ? WHERE id = ?',
                             (user, stamp, sleeveId))
            else:
                cur = conn.execute(
                    'INSERT INTO sleeves (variant, category, name, note, createdBy, createdAt, '
                    'updatedBy, updatedAt) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (variant, category, name, '', user, stamp, user, stamp))
                sleeveId = cur.lastrowid
            _writeProducts(conn, sleeveId, cleaned)
    return len(order)


def importRows(rows, replace=False, user='import') -> int:
    """Load interchange rows through full validation. Returns the number of
    sleeves written. Existing sleeves of the same name are overwritten;
    *replace* clears the library first."""
    conn = _connect()
    try:
        return _importInto(conn, list(rows), replace=replace, user=user, strict=True)
    finally:
        conn.close()


def orphanProducts() -> list:
    """Products the sleeves reference that the catalogue no longer carries -
    a new delivery dropped them - with the sleeves each one breaks. The
    other side of listAll's problems, gathered by product rather than by
    sleeve, because this is the question the catalogue view answers."""
    byProduct = {}
    for e in listAll():
        for row in e['products']:
            if row['product'] is None:
                entry = byProduct.setdefault(row['productId'], {'productId': row['productId'], 'sleeves': []})
                entry['sleeves'].append({'id': e['id'], 'variant': e['variant'],
                                         'category': e['category'], 'name': e['name']})
    return [byProduct[k] for k in sorted(byProduct)]


def census() -> dict:
    """What the library holds and what is wrong with it."""
    entries = listAll()
    byVariant = {}
    for e in entries:
        byVariant.setdefault(e['variant'], {'sleeves': 0, 'broken': 0})
        byVariant[e['variant']]['sleeves'] += 1
        if e['problems']:
            byVariant[e['variant']]['broken'] += 1
    fixed = []
    for variant in VARIANTS:
        for category in fixedCategories():
            n = sum(1 for e in entries if e['variant'] == variant and e['category'] == category)
            if n != 1:
                fixed.append('{} / {}: {} sleeve(s), expected exactly 1'.format(variant, category, n))
    return {
        'store': describe(),
        'catalogue': products.describeSource(),
        'byVariant': byVariant,
        'broken': [e for e in entries if e['problems']],
        'orphans': orphanProducts(),
        'fixedCategoryProblems': fixed,
        'total': len(entries),
    }
