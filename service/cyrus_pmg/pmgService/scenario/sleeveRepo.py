"""The sleeve repository - the library, in the tool's own database (D57).

Sleeves used to be tables in sleeves.py. They are now rows in a SQLite file
the service owns and writes, built and maintained by an admin from inside the
app. Four tables:

    sleeves         id, variant, category, name, note, provenance, deletion
                    unique on (variant, category, name) among the LIVE ones
    sleeveProducts  sleeveId, position, productId, weight   (weight a fraction)
    sleeveHistory   one append-only row per revision: the whole sleeve as it
                    stood after that action, with who did it and when
    meta            schema version and the seed stamp

Nothing is ever destroyed (D65). Every write appends a revision recording the
sleeve in full - name, note and the complete product set - so an earlier
version can always be read back and put in force again. A delete is a delete
from the LIBRARY only: the row stays, marked with who removed it and when,
withheld from every read that serves sleeves, and restorable. The history
outlives the sleeve's presence in the library, which is the point.

The unit of history is the sleeve id, and ids are never reused because rows
are never removed - which is the other reason a delete is soft. A revision
records the state AFTER its action, so revision N-1 is what the sleeve looked
like before revision N, and a restore is just re-applying a stored revision as
a new one. History is append-only in the strict sense: a revert adds a
revision, it never rewrites or removes one.

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
import json
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

#: schema revision written to meta. 1 was the original two-table store; 2
#: added the soft delete and the history table (D65).
SCHEMA_VERSION = 2

#: what a history row's action can say. baseline is the one nobody performed:
#: it is the state a sleeve was in when history started being kept.
ACTIONS = ['baseline', 'created', 'updated', 'reverted', 'deleted', 'restored',
           'imported', 'seeded']

_SLEEVES_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    id        INTEGER PRIMARY KEY,
    variant   TEXT NOT NULL,
    category  TEXT NOT NULL,
    name      TEXT NOT NULL,
    note      TEXT NOT NULL DEFAULT '',
    createdBy TEXT NOT NULL DEFAULT '',
    createdAt TEXT NOT NULL,
    updatedBy TEXT NOT NULL DEFAULT '',
    updatedAt TEXT NOT NULL,
    deletedBy TEXT NOT NULL DEFAULT '',
    deletedAt TEXT NOT NULL DEFAULT ''
)
"""

# The uniqueness of a sleeve name is a rule about the LIBRARY, not about the
# record: a deleted sleeve must not stop the same name being used again, and a
# partial index is how SQLite says exactly that.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sleeveProducts (
    sleeveId  INTEGER NOT NULL REFERENCES sleeves(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    productId TEXT NOT NULL,
    weight    REAL NOT NULL,
    PRIMARY KEY (sleeveId, productId)
);
CREATE TABLE IF NOT EXISTS sleeveHistory (
    id        INTEGER PRIMARY KEY,
    sleeveId  INTEGER NOT NULL,
    revision  INTEGER NOT NULL,
    action    TEXT NOT NULL,
    variant   TEXT NOT NULL,
    category  TEXT NOT NULL,
    name      TEXT NOT NULL,
    note      TEXT NOT NULL DEFAULT '',
    products  TEXT NOT NULL,
    actor     TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    UNIQUE (sleeveId, revision)
);
CREATE INDEX IF NOT EXISTS sleeves_by_home ON sleeves (variant, category);
CREATE INDEX IF NOT EXISTS sleeves_live ON sleeves (deletedAt);
CREATE UNIQUE INDEX IF NOT EXISTS sleeves_live_name
    ON sleeves (variant, category, name) WHERE deletedAt = '';
CREATE INDEX IF NOT EXISTS history_by_sleeve ON sleeveHistory (sleeveId, revision);
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
    loaded. Callers close it; use it as a context manager for the commit.

    The migration runs before foreign keys are switched on, because bringing a
    version 1 file up to version 2 rebuilds the sleeves table and the child
    rows have to survive the swap."""
    path = dbPath()
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    with _lock:
        _migrate(conn)
        conn.executescript(_SCHEMA)
        _seedOnce(conn)
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def _seedOnce(conn: sqlite3.Connection) -> None:
    """Fill an empty library from the seed - once, whatever else is starting.

    The host runs several service workers, so *is the library empty?* and
    *fill it* are asked by several processes at once against one file, and a
    plain check-then-act lets two of them both answer yes. Asking again inside
    a write transaction is what makes the pair atomic: BEGIN IMMEDIATE takes
    the reserved lock first (the connection's busy timeout covers the wait),
    so a worker that loses the race re-reads under the lock, finds the library
    filled, and does nothing.

    Measured before it was written, with four workers on an empty file: the
    sleeves themselves survive a double seed because the live-name index
    refuses the duplicates, but ``sleeveHistory`` has no such index and ends
    up with one *seeded* revision per worker - the record trebled, which is
    the half that cannot be repaired by looking at it.

    The lock is taken only when the library looks empty, which is once in the
    life of a store; every other connection pays two indexed lookups.
    """
    if conn.execute('SELECT 1 FROM sleeves LIMIT 1').fetchone() is not None:
        return
    if conn.execute("SELECT 1 FROM meta WHERE key = 'seededAt'").fetchone() is not None:
        return
    conn.commit()                       # nothing open, so BEGIN is legal
    conn.execute('BEGIN IMMEDIATE')
    try:
        empty = conn.execute('SELECT 1 FROM sleeves LIMIT 1').fetchone() is None
        never = conn.execute(
            "SELECT 1 FROM meta WHERE key = 'seededAt'").fetchone() is None
        if not (empty and never):
            conn.rollback()
            return
        _seed(conn)                     # commits the transaction on its way out
    except Exception:
        conn.rollback()
        raise


def _tableExists(conn, name) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (name,)).fetchone() is not None


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring the file up to SCHEMA_VERSION. A no-op on a current file and on a
    file that does not exist yet; the work is the one-way step from 1 to 2.

    Version 1 kept uniqueness as a table constraint over every row, which a
    soft delete cannot live with - a deleted sleeve would hold its name for
    ever. SQLite cannot drop a table constraint, so the table is rebuilt: new
    shape, copy, drop, rename, which is the procedure the SQLite manual sets
    out. legacy_alter_table keeps the final rename from rewriting the foreign
    key in sleeveProducts, and foreign keys are still off at this point so the
    child rows sit patiently through the swap.
    """
    if not _tableExists(conn, 'sleeves'):
        conn.executescript(_SLEEVES_TABLE.format(name='sleeves'))
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('schemaVersion', ?)",
                     (str(SCHEMA_VERSION),))
        conn.commit()
        return

    columns = {r['name'] for r in conn.execute('PRAGMA table_info(sleeves)')}
    if 'deletedAt' in columns:
        return

    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute('PRAGMA legacy_alter_table = ON')
    try:
        with conn:
            conn.executescript(_SLEEVES_TABLE.format(name='sleevesNew'))
            conn.execute(
                'INSERT INTO sleevesNew (id, variant, category, name, note, createdBy, '
                'createdAt, updatedBy, updatedAt, deletedBy, deletedAt) '
                "SELECT id, variant, category, name, note, createdBy, createdAt, "
                "updatedBy, updatedAt, '', '' FROM sleeves")
            conn.execute('DROP TABLE sleeves')
            conn.execute('ALTER TABLE sleevesNew RENAME TO sleeves')
            conn.executescript(_SCHEMA)
            _baseline(conn)
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('schemaVersion', ?)",
                         (str(SCHEMA_VERSION),))
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('migratedAt', ?)", (_now(),))
    finally:
        conn.execute('PRAGMA legacy_alter_table = OFF')


def _baseline(conn: sqlite3.Connection) -> None:
    """Give every sleeve already in the store a first revision.

    It is deliberately called *baseline* and not *created*: these are the
    sleeves as they stood the day history started being kept, and whatever
    they looked like before that was never recorded. Claiming otherwise would
    be the one lie in the whole table. The stamp is the sleeve's own creation
    stamp, which is the most that is honestly known about it.
    """
    for row in conn.execute('SELECT * FROM sleeves ORDER BY id').fetchall():
        rows = conn.execute(
            'SELECT productId, weight FROM sleeveProducts WHERE sleeveId = ? ORDER BY position',
            (row['id'],)).fetchall()
        conn.execute(
            'INSERT INTO sleeveHistory (sleeveId, revision, action, variant, category, name, '
            'note, products, actor, at) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)',
            (row['id'], 'baseline', row['variant'], row['category'], row['name'], row['note'],
             _packProducts([(r['productId'], r['weight']) for r in rows]),
             row['createdBy'], row['createdAt']))


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
    _importInto(conn, rows, replace=False, user='seed', strict=False, action='seeded')
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
        "SELECT id FROM sleeves WHERE variant = ? AND category = ? AND name = ? "
        "AND deletedAt = ''", (variant, category, name)).fetchone()
    if clash and clash['id'] != sleeveId:
        raise ValidationError(
            'name', 'A sleeve named {} already exists in {} under {}.'.format(name, category, variant))

    if category in fixedCategories():
        others = conn.execute(
            "SELECT COUNT(*) FROM sleeves WHERE variant = ? AND category = ? AND id IS NOT ? "
            "AND deletedAt = ''", (variant, category, sleeveId)).fetchone()[0]
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

def _sleeveRows(conn, variant=None, category=None, deleted=False):
    """Live sleeves by default. *deleted* True lists the removed ones instead -
    the two are never mixed, because every consumer wants one or the other."""
    sql = 'SELECT * FROM sleeves'
    clauses, args = ["deletedAt {} ''".format('!=' if deleted else '=')], []
    if variant is not None:
        clauses.append('variant = ?'); args.append(variant)
    if category is not None:
        clauses.append('category = ?'); args.append(category)
    sql += ' WHERE ' + ' AND '.join(clauses)
    sql += ' ORDER BY {}variant, category, id'.format('deletedAt DESC, ' if deleted else '')
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


def _entry(conn, row, productRows, revisions=None):
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
        "SELECT variant FROM sleeves WHERE category = ? AND name = ? AND deletedAt = '' "
        "ORDER BY id", (row['category'], row['name'])).fetchall()]
    return {
        'id': row['id'], 'variant': row['variant'], 'category': row['category'],
        'name': row['name'], 'note': row['note'],
        'fixed': row['category'] in fixedCategories(),
        'products': hydrated, 'problems': problems, 'offeredUnder': offeredUnder,
        'createdBy': row['createdBy'], 'createdAt': row['createdAt'],
        'updatedBy': row['updatedBy'], 'updatedAt': row['updatedAt'],
        # the one place the stored word becomes the shown one: a caller DELETES
        # a sleeve, and what happens to it is that it is ARCHIVED (D66)
        'archived': bool(row['deletedAt']),
        'archivedBy': row['deletedBy'], 'archivedAt': row['deletedAt'],
        'revisions': revisions if revisions is not None else _revisionCount(conn, row['id']),
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
    """Every live sleeve, broken ones included, for the console and the census."""
    conn = _connect()
    try:
        counts = _revisionCounts(conn)
        return [_entry(conn, row, _productRows(conn, row['id']), counts.get(row['id'], 0))
                for row in _sleeveRows(conn, variant)]
    finally:
        conn.close()


def listArchived(variant: str = None) -> list:
    """The sleeves an admin has taken out of the library. They are still here,
    still carry their whole history, and can be put back."""
    conn = _connect()
    try:
        counts = _revisionCounts(conn)
        return [_entry(conn, row, _productRows(conn, row['id']), counts.get(row['id'], 0))
                for row in _sleeveRows(conn, variant, deleted=True)]
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
        live = conn.execute("SELECT COUNT(*) FROM sleeves WHERE deletedAt = ''").fetchone()[0]
        gone = conn.execute("SELECT COUNT(*) FROM sleeves WHERE deletedAt != ''").fetchone()[0]
        revisions = conn.execute('SELECT COUNT(*) FROM sleeveHistory').fetchone()[0]
        return {'path': dbPath(), 'sleeves': live, 'archived': gone, 'revisions': revisions,
                'schemaVersion': int(meta.get('schemaVersion', SCHEMA_VERSION)),
                'seededFrom': meta.get('seededFrom', ''), 'seededAt': meta.get('seededAt', '')}
    finally:
        conn.close()


# --------------------------------------------------------------- history ---

def _packProducts(cleaned) -> str:
    """A revision's product set, stored as JSON rather than as rows.

    A revision is a photograph, never a thing to query across: it is read back
    whole, for one sleeve, to be shown or re-applied. Rows would buy nothing
    and would need the same delete-and-reinsert dance the live table already
    does. Order is the sleeve's own order and is part of what is preserved."""
    return json.dumps([[pid, float(weight)] for pid, weight in cleaned])


def _unpackProducts(packed):
    try:
        return [(str(pid), float(weight)) for pid, weight in json.loads(packed or '[]')]
    except (ValueError, TypeError):
        return []


def _revisionCount(conn, sleeveId) -> int:
    return conn.execute('SELECT COUNT(*) FROM sleeveHistory WHERE sleeveId = ?',
                        (sleeveId,)).fetchone()[0]


def _revisionCounts(conn) -> dict:
    """Every sleeve's revision count in one query, for the list views."""
    return {r['sleeveId']: r['n'] for r in conn.execute(
        'SELECT sleeveId, COUNT(*) AS n FROM sleeveHistory GROUP BY sleeveId')}


def _recordHistory(conn, sleeveId, action, variant, category, name, note, cleaned,
                   actor, at=None) -> int:
    """Append one revision. Called inside the caller's transaction, always -
    a write that reached the library but not the history would be worse than
    either failing."""
    revision = conn.execute(
        'SELECT COALESCE(MAX(revision), 0) FROM sleeveHistory WHERE sleeveId = ?',
        (sleeveId,)).fetchone()[0] + 1
    conn.execute(
        'INSERT INTO sleeveHistory (sleeveId, revision, action, variant, category, name, '
        'note, products, actor, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (sleeveId, revision, action, variant, category, name, note,
         _packProducts(cleaned), actor, at or _now()))
    return revision


def _weightsMatch(a, b) -> bool:
    return abs(a - b) <= WEIGHT_TOLERANCE


def _changes(previous, current) -> list:
    """What moved between two revisions, in the words the console shows.

    Held to what an admin would say out loud - renamed, added, removed, moved
    from this weight to that - rather than a field-by-field dump. A revision
    with no previous one has nothing to say and says nothing."""
    if previous is None:
        return []
    out = []
    if previous['name'] != current['name']:
        out.append('Renamed from {}'.format(previous['name']))
    if (previous['note'] or '') != (current['note'] or ''):
        out.append('Note changed' if current['note'] else 'Note cleared')
    was = dict(_unpackProducts(previous['products']))
    now = dict(_unpackProducts(current['products']))
    for pid in [p for p in now if p not in was]:
        out.append('Added {} at {:g}%'.format(_productLabel(pid), now[pid] * 100.0))
    for pid in [p for p in was if p not in now]:
        out.append('Removed {}'.format(_productLabel(pid)))
    for pid in [p for p in now if p in was and not _weightsMatch(was[p], now[p])]:
        out.append('{} {:g}% to {:g}%'.format(
            _productLabel(pid), was[pid] * 100.0, now[pid] * 100.0))
    return out


def _productLabel(productId) -> str:
    product = products.get(productId)
    return product['name'] if product else productId


def _historyEntry(row, previous, currentRevision) -> dict:
    hydrated = []
    for pid, weight in _unpackProducts(row['products']):
        hydrated.append({'productId': pid, 'weight': weight, 'product': products.get(pid)})
    return {
        'sleeveId': row['sleeveId'], 'revision': row['revision'], 'action': row['action'],
        'variant': row['variant'], 'category': row['category'], 'name': row['name'],
        'note': row['note'], 'actor': row['actor'], 'at': row['at'],
        'products': hydrated, 'changes': _changes(previous, row),
        'current': row['revision'] == currentRevision,
    }


def history(sleeveId: int) -> list:
    """Every revision of one sleeve, newest first.

    Present for a deleted sleeve exactly as for a live one - that is the whole
    reason a delete is soft."""
    conn = _connect()
    try:
        rows = conn.execute(
            'SELECT * FROM sleeveHistory WHERE sleeveId = ? ORDER BY revision',
            (sleeveId,)).fetchall()
        if not rows:
            return []
        top = rows[-1]['revision']
        out = [_historyEntry(row, rows[i - 1] if i else None, top)
               for i, row in enumerate(rows)]
        out.reverse()
        return out
    finally:
        conn.close()


def revision(sleeveId: int, number: int):
    """One stored revision, or None."""
    conn = _connect()
    try:
        rows = conn.execute(
            'SELECT * FROM sleeveHistory WHERE sleeveId = ? AND revision <= ? ORDER BY revision',
            (sleeveId, number)).fetchall()
        if not rows or rows[-1]['revision'] != number:
            return None
        top = conn.execute('SELECT COALESCE(MAX(revision), 0) FROM sleeveHistory '
                           'WHERE sleeveId = ?', (sleeveId,)).fetchone()[0]
        return _historyEntry(rows[-1], rows[-2] if len(rows) > 1 else None, top)
    finally:
        conn.close()


# -------------------------------------------------------------- the feed ---
# The record read as a story rather than a filing cabinet (D66): every
# revision across every sleeve, newest first, filtered and paged. The archive
# answers "find one thing"; this answers "what happened".

FEED_LIMIT_MAX = 500
_FEED_SCAN_CAP = 5000      # rows read before a text query narrows them


def _feedWhere(actions=None, variant=None, category=None, actor=None,
               since=None, until=None, exclude=None):
    """The SQL for a filter set, with one dimension left out so a facet can
    count what it would offer, not only what is chosen (the catalogue's
    convention, D63)."""
    clauses, args = [], []
    if actions and exclude != 'action':
        wanted = [a for a in actions if a in ACTIONS]
        if wanted:
            clauses.append('h.action IN ({})'.format(','.join('?' * len(wanted))))
            args.extend(wanted)
    if variant and exclude != 'variant':
        clauses.append('h.variant = ?'); args.append(variant)
    if category and exclude != 'category':
        clauses.append('h.category = ?'); args.append(category)
    if actor and exclude != 'actor':
        clauses.append('h.actor = ?'); args.append(actor)
    if since:
        clauses.append('h.at >= ?'); args.append(since)
    if until:
        clauses.append('h.at < ?'); args.append(until)
    return (' AND '.join(clauses) or '1 = 1'), args


def _feedMatches(entry, query) -> bool:
    q = query.lower()
    if q in entry['name'].lower() or q in entry['category'].lower() \
            or q in (entry['actor'] or '').lower() or q in entry['variant'].lower():
        return True
    for row in entry['products']:
        if q in row['productId'].lower():
            return True
        if row['product'] and q in row['product']['name'].lower():
            return True
    return any(q in c.lower() for c in entry['changes'])


def _cursor(row) -> str:
    return '{}|{}'.format(row['at'], row['id'])


def _cursorClause(before):
    """Newest first means 'before' is strictly older: an earlier stamp, or the
    same stamp and a lower id (a batch writes several rows at one stamp)."""
    if not before or '|' not in before:
        return '1 = 1', []
    at, _, ident = before.partition('|')
    try:
        ident = int(ident)
    except ValueError:
        return '1 = 1', []
    return '(h.at < ? OR (h.at = ? AND h.id < ?))', [at, at, ident]


def activity(actions=None, variant=None, category=None, actor=None, since=None,
             until=None, query='', limit=100, before=None) -> dict:
    """A page of the feed and the counts that frame it.

    Returns {entries, next, facets, total}. *next* is the cursor for the page
    after this one, or None. The facets are counts by action, actor and book
    over the rows that pass every OTHER filter, so a zero is a value that
    cannot be chosen from here. A text query narrows in Python after the SQL
    has done the rest - it reaches into product names, which the row does not
    carry - and is capped at _FEED_SCAN_CAP rows read, which is years of this
    library."""
    limit = max(1, min(int(limit or 100), FEED_LIMIT_MAX))
    query = (query or '').strip()
    where, args = _feedWhere(actions, variant, category, actor, since, until)
    cursorSql, cursorArgs = _cursorClause(before)
    conn = _connect()
    try:
        sql = ('SELECT h.*, p.name AS prevName, p.note AS prevNote, p.products AS prevProducts, '
               "COALESCE(s.deletedAt, '') AS sleeveDeletedAt, "
               '(SELECT MAX(revision) FROM sleeveHistory m WHERE m.sleeveId = h.sleeveId) AS top '
               'FROM sleeveHistory h '
               'LEFT JOIN sleeveHistory p ON p.sleeveId = h.sleeveId AND p.revision = h.revision - 1 '
               'LEFT JOIN sleeves s ON s.id = h.sleeveId '
               'WHERE {} AND {} ORDER BY h.at DESC, h.id DESC LIMIT ?').format(where, cursorSql)
        scan = (_FEED_SCAN_CAP if query else limit) + 1
        rows = conn.execute(sql, args + cursorArgs + [scan]).fetchall()

        entries, nextCursor = [], None
        for row in rows:
            previous = None
            if row['prevProducts'] is not None:
                previous = {'name': row['prevName'], 'note': row['prevNote'],
                            'products': row['prevProducts']}
            entry = _historyEntry(row, previous, row['top'])
            entry['sleeveArchived'] = bool(row['sleeveDeletedAt'])
            entry['cursor'] = _cursor(row)
            if query and not _feedMatches(entry, query):
                continue
            if len(entries) == limit:
                nextCursor = entries[-1]['cursor']
                break
            entries.append(entry)
        if not query and len(rows) > limit and nextCursor is None:
            nextCursor = entries[-1]['cursor'] if entries else None

        def count(dimension):
            w, a = _feedWhere(actions, variant, category, actor, since, until, exclude=dimension)
            column = {'action': 'h.action', 'actor': 'h.actor', 'variant': 'h.variant'}[dimension]
            out = {}
            for r in conn.execute('SELECT {} AS k, COUNT(*) AS n FROM sleeveHistory h WHERE {} '
                                  'GROUP BY k ORDER BY n DESC, k'.format(column, w), a):
                out[r['k']] = r['n']
            return out

        total = conn.execute('SELECT COUNT(*) FROM sleeveHistory h WHERE {}'.format(where),
                             args).fetchone()[0]
        return {
            'entries': entries, 'next': nextCursor, 'total': total,
            'facets': {'action': count('action'), 'actor': count('actor'),
                       'variant': count('variant')},
            'earliest': (conn.execute('SELECT MIN(at) FROM sleeveHistory').fetchone()[0] or ''),
        }
    finally:
        conn.close()


# ------------------------------------------------------------- the export ---

ARCHIVE_COLUMNS = ['SleeveId', 'Variant', 'Category', 'Sleeve', 'ArchivedAt', 'ArchivedBy',
                   'CreatedAt', 'CreatedBy', 'Revisions', 'Products']
ACTIVITY_COLUMNS = ['At', 'Action', 'Actor', 'Variant', 'Category', 'Sleeve', 'SleeveId',
                    'Revision', 'Changes', 'Products']


def _productsCell(products) -> str:
    return '; '.join('{} {:.2f}%'.format(r['productId'], r['weight'] * 100.0) for r in products)


def archiveRows() -> list:
    """The archive as a table, one row per archived sleeve, for the export."""
    out = []
    for e in listArchived():
        out.append((e['id'], e['variant'], e['category'], e['name'], e['archivedAt'],
                    e['archivedBy'], e['createdAt'], e['createdBy'], e['revisions'],
                    _productsCell(e['products'])))
    return out


def activityRows(**filters) -> list:
    """The feed as a table under the same filters, every page of it, for the
    export. A record is for handing to someone, and a screen is not."""
    out, before = [], None
    while True:
        page = activity(limit=FEED_LIMIT_MAX, before=before, **filters)
        for e in page['entries']:
            out.append((e['at'], e['action'], e['actor'], e['variant'], e['category'],
                        e['name'], e['sleeveId'], e['revision'], ' | '.join(e['changes']),
                        _productsCell(e['products'])))
        if not page['next'] or not page['entries']:
            return out
        before = page['next']


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
                _recordHistory(conn, cur.lastrowid, 'created', variant, category,
                               cleanName, cleanNote, cleaned, user, stamp)
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


def updateSleeve(sleeveId, name, productRows, note='', user='', action='updated') -> dict:
    """Save a sleeve and append the revision that records it. A deleted sleeve
    is not editable: restore it first, so the history reads in the order the
    events happened."""
    conn = _connect()
    try:
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        if row is None:
            raise ValidationError('id', 'That sleeve no longer exists.')
        if row['deletedAt']:
            raise ValidationError('id', 'That sleeve was removed from the library. '
                                        'Restore it before editing it.')
        name, note, cleaned = _validate(conn, row['variant'], row['category'], name, note,
                                        productRows, sleeveId=sleeveId)
        stamp = _now()
        with conn:
            conn.execute('UPDATE sleeves SET name = ?, note = ?, updatedBy = ?, updatedAt = ? '
                         'WHERE id = ?', (name, note, user, stamp, sleeveId))
            _writeProducts(conn, sleeveId, cleaned)
            _recordHistory(conn, sleeveId, action, row['variant'], row['category'],
                           name, note, cleaned, user, stamp)
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        return _entry(conn, row, _productRows(conn, row['id']))
    finally:
        conn.close()


def deleteSleeve(sleeveId, user='') -> dict:
    """Remove a sleeve from the library, not from the record (D65).

    The row stays, marked with who removed it and when, and is withheld from
    every read that serves or lists sleeves. Its history stays whole and gains
    a final revision holding what it looked like at the moment it went - which
    is what makes restoring it possible, and what makes the question "what did
    we used to offer here?" answerable at all.
    """
    conn = _connect()
    try:
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        if row is None:
            raise ValidationError('id', 'That sleeve no longer exists.')
        if row['deletedAt']:
            raise ValidationError('id', 'That sleeve is already out of the library.')
        if row['category'] in fixedCategories():
            raise ValidationError(
                'category', '{} is attached automatically by its toggle and always holds one '
                'sleeve. Edit it rather than deleting it.'.format(row['category']))
        cleaned = [(r['productId'], r['weight']) for r in _productRows(conn, sleeveId)]
        stamp = _now()
        with conn:
            conn.execute('UPDATE sleeves SET deletedBy = ?, deletedAt = ? WHERE id = ?',
                         (user, stamp, sleeveId))
            revisionNumber = _recordHistory(conn, sleeveId, 'deleted', row['variant'],
                                            row['category'], row['name'], row['note'],
                                            cleaned, user, stamp)
        return {'id': sleeveId, 'variant': row['variant'], 'category': row['category'],
                'name': row['name'], 'archivedAt': stamp, 'archivedBy': user,
                'revision': revisionNumber}
    finally:
        conn.close()


def restoreSleeve(sleeveId, user='') -> dict:
    """Put a removed sleeve back in the library.

    Re-validated on the way in, because the library has moved on: the name may
    have been taken since, a fixed category may already hold its one sleeve,
    and a product the sleeve holds may have left the catalogue. A restore that
    cannot be clean is refused with the reason rather than forced.
    """
    conn = _connect()
    try:
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        if row is None:
            raise ValidationError('id', 'That sleeve no longer exists.')
        if not row['deletedAt']:
            raise ValidationError('id', 'That sleeve is already in the library.')
        productRows = [{'productId': r['productId'], 'weight': r['weight']}
                       for r in _productRows(conn, sleeveId)]
        name, note, cleaned = _validate(conn, row['variant'], row['category'], row['name'],
                                        row['note'], productRows, sleeveId=sleeveId)
        stamp = _now()
        with conn:
            conn.execute("UPDATE sleeves SET deletedBy = '', deletedAt = '', updatedBy = ?, "
                         'updatedAt = ? WHERE id = ?', (user, stamp, sleeveId))
            _recordHistory(conn, sleeveId, 'restored', row['variant'], row['category'],
                           name, note, cleaned, user, stamp)
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        return _entry(conn, row, _productRows(conn, sleeveId))
    finally:
        conn.close()


def restoreSleeves(ids, user='') -> list:
    """Put several archived sleeves back at once, all or nothing (D66).

    Each is validated as restoreSleeve validates one, and then the batch is
    checked against itself: two archived sleeves that would land on the same
    name in the same book, or two that would both fill a fixed category's one
    slot, pass singly and collide together. Nothing is written unless every
    one of them can come back clean.
    """
    wanted = list(dict.fromkeys(int(i) for i in (ids or [])))
    if not wanted:
        raise ValidationError('ids', 'Choose at least one archived sleeve.')
    conn = _connect()
    try:
        checked, names, slots = [], set(), set()
        for sleeveId in wanted:
            row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
            if row is None:
                raise ValidationError('ids', 'Sleeve {} no longer exists.'.format(sleeveId))
            if not row['deletedAt']:
                raise ValidationError('ids', '{} is already in the library.'.format(row['name']))
            productRows = [{'productId': r['productId'], 'weight': r['weight']}
                           for r in _productRows(conn, sleeveId)]
            name, note, cleaned = _validate(conn, row['variant'], row['category'], row['name'],
                                            row['note'], productRows, sleeveId=sleeveId)
            key = (row['variant'], row['category'], name.lower())
            if key in names:
                raise ValidationError('ids', 'Two of these are called {} in {} under {}; '
                                      'only one can come back.'.format(name, row['category'],
                                                                     row['variant']))
            names.add(key)
            if row['category'] in fixedCategories():
                slot = (row['variant'], row['category'])
                if slot in slots:
                    raise ValidationError('ids', '{} holds one sleeve under {}; two of these '
                                          'would fill it.'.format(row['category'], row['variant']))
                slots.add(slot)
            checked.append((row, name, note, cleaned))
        stamp = _now()
        with conn:
            for row, name, note, cleaned in checked:
                conn.execute("UPDATE sleeves SET deletedBy = '', deletedAt = '', updatedBy = ?, "
                             'updatedAt = ? WHERE id = ?', (user, stamp, row['id']))
                _recordHistory(conn, row['id'], 'restored', row['variant'], row['category'],
                               name, note, cleaned, user, stamp)
        out = []
        for row, _, _, _ in checked:
            fresh = conn.execute('SELECT * FROM sleeves WHERE id = ?', (row['id'],)).fetchone()
            out.append(_entry(conn, fresh, _productRows(conn, row['id'])))
        return out
    finally:
        conn.close()


def revertSleeve(sleeveId, number: int, user='') -> dict:
    """Put an earlier revision back in force.

    A revert is an ordinary save of an old state, not a rewind: the revision
    it restores stays where it is and a new one is appended on top, so the
    fact that someone reverted is itself part of the history. Everything a
    save has to satisfy, a revert has to satisfy too - an old composition
    holding a product the catalogue has since dropped cannot come back.
    """
    stored = revision(sleeveId, number)
    if stored is None:
        raise ValidationError('revision', 'Revision {} does not exist for this sleeve.'.format(number))
    productRows = [{'productId': p['productId'], 'weight': p['weight']}
                   for p in stored['products']]
    return updateSleeve(sleeveId, stored['name'], productRows, note=stored['note'],
                        user=user, action='reverted')


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


def _importInto(conn, rows, replace, user, strict, action='imported'):
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
            # 'clears the library first' means exactly that, and no more: the
            # sleeves leave the library the same way a hand delete takes them
            # out, each recording its own final state, none of them destroyed
            for row in _sleeveRows(conn):
                conn.execute('UPDATE sleeves SET deletedBy = ?, deletedAt = ? WHERE id = ?',
                             (user, stamp, row['id']))
                _recordHistory(conn, row['id'], 'deleted', row['variant'], row['category'],
                               row['name'], row['note'],
                               [(r['productId'], r['weight']) for r in _productRows(conn, row['id'])],
                               user, stamp)
        for key in order:
            variant, category, name = key
            productRows = grouped[key]
            if strict:
                name, _, cleaned = _validate(conn, variant, category, name, '', productRows)
            else:
                cleaned = [(p['productId'], float(p['weight'])) for p in productRows]
            existing = conn.execute(
                "SELECT id FROM sleeves WHERE variant = ? AND category = ? AND name = ? "
                "AND deletedAt = ''", (variant, category, name)).fetchone()
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
            _recordHistory(conn, sleeveId, action, variant, category, name, '',
                           cleaned, user, stamp)
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
        'archived': listArchived(),
    }
