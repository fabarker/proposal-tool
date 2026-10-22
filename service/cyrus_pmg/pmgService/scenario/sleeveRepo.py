"""The sleeve repository - the library, in the tool's own database (D57).

Sleeves used to be tables in sleeves.py. They are now rows in a SQLite file
the service owns and writes, built and maintained by an admin from inside the
app. Five tables:

    sleeves         id, variant, category, name, label, note, provenance,
                    deletion - unique on (variant, category, name, label)
                    among the LIVE ones; variant names every type the
                    sleeve is offered under (D179)
    sleeveProducts  sleeveId, position, productId, weight   (weight a fraction),
                    alternative (held below the product's minimum, D192)
    sleeveRules     sleeveId, position, currency, riskLevel, allocationType -
                    which strategic portfolios this row is for (D89)
    sleeveHistory   one append-only row per revision: the whole sleeve as it
                    stood after that action, with who did it and when
    meta            schema version and the seed stamp

and, since schema revision 4, the repository's other settings (D148):

    policies        kind, scope, body - one setting in force per scope; the
                    private-markets funding split (fundingSplit.py),
                    from revision 6 the overlay rules (overlayRules.py, D155)
                    and from revision 7 the fee card (feeCard.py, D166)
    policyHistory   one append-only row per revision of a setting

and, since schema revision 7, the product catalogue itself (products.py,
D165), which an admin now maintains here rather than receiving as a file:

    products        one row per product, live or withdrawn
    productHistory  one append-only row per revision: the whole product as
                    it stood after that action, with who did it and when

Editions (D89). A row is an EDITION of a sleeve name. A name a PWA picks may
have several: a fallback with no rules, which applies wherever nothing else
does, and any number of editions each carrying rules over the strategic
portfolio's currency, risk level and allocation type (sleeveRules.py). The
resolver serves ONE row per name for a given base portfolio - the edition
whose rules match it, else the fallback, else nothing - and the PWA never
learns which; the served shape is the shape it always was. Two editions of a
name whose rules claim the same portfolio are refused at save; if the
universe later changes under them so that both claim one, the name is
withheld for that portfolio and the console shows both as broken.

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
catalogue (products.py, D165) and are joined in when a sleeve is served.
That join is where a sleeve can fail: a product the catalogue does not carry
leaves every sleeve holding it *broken*. A broken sleeve stays in the
repository, flagged, but is withheld from listSleeves - a sleeve that cannot
be priced is never offered to a PWA. With the catalogue kept here, a product
in a live sleeve cannot be withdrawn, so a sleeve breaks this way only when
the first load of the catalogue lacked a product the library already named.

Seeding. A database that does not exist yet is created and filled from the
seed extract (SCENARIO_SLEEVES_SEED; the packaged default is the stand-in
under sleeveSource, today's library written out row by row).
Once the database exists the seed is never read again. The same table shape
is the interchange format - exportRows / importRows, driven by sleeveTools.
The fee card and the catalogue are loaded once the same way, from the files
they used to be read from (schema 7): the card first, because a product
names a fee group the card must price, and both before the library, whose
sleeves name products.

Shared sleeves (D179). One sleeve may be offered under several types: its
variant column holds them all, in VARIANTS order, separated by TYPE_SEP,
and a sleeve under one type holds that type alone, as it always did. It is
ONE sleeve - one id, one history, one set of products - so an edit made
under any of its types is what every one of them serves. Each type still
sees its own library: a name is unique per type, and the edition rules, the
clash checks and the fixed categories' one sleeve are all asked type by
type, of every sleeve offered under that type. Where it is offered is not a
revision (offerUnder, withdrawFrom, splitOff): what it holds has not
changed, so those are switches, recorded beside disable and enable. A type
that needs it to differ is given a copy of its own (splitOff), and the rest
stay shared.

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

from . import entities, products, sleeveRules, universe
from .types import ValidationError
from .csvText import unwrapRows


class StaleError(ValidationError):
    """The sleeve a save was made against is no longer the version in force:
    someone else saved it since the editor opened it -> HTTP 409 (D156)."""

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

#: The seed as delivered before editions existed, still accepted unchanged:
#: every row becomes a fallback edition. SEED_COLUMNS_EDITIONS adds the label.
SEED_COLUMNS = ['Variant', 'Category', 'Sleeve', 'ProductId', 'Weight']
SEED_COLUMNS_EDITIONS = ['Variant', 'Category', 'Sleeve', 'Edition', 'ProductId', 'Weight']
#: an optional seventh column after either header: the product a row's
#: product gives way to where the amount reaching it is below its minimum
ALTERNATIVE_COLUMN = 'BelowMinimum'
#: The rules, one row per rule, in a file beside the seed (D89). A cell holds
#: one value or several separated by |; blank means any. Several rows for one
#: edition are alternatives - the edition applies where any of them matches.
RULE_COLUMNS = ['Variant', 'Category', 'Sleeve', 'Edition', 'Currency', 'RiskLevel',
                'AllocationType']
#: an optional eighth column: the custodians / entities a rule is for, by
#: name or id (D193); blank, or the column left out, means any
ENTITY_COLUMN = 'Entity'
NAME_MAX = 80
WEIGHT_TOLERANCE = 1e-6

#: schema revision written to meta. 1 was the original two-table store; 2
#: added the soft delete and the history table (D65); 3 added the edition
#: label and the rules table (D89); 4 the policies and their history (D148);
#: 5 the version stamps and the superseded versions in the archive (D152);
#: 6 the overlay rules (D155) - a policy kind, no new table: the default list is
#: seeded with the two overlays as the code applied them on the first open;
#: 7 the product catalogue and its history (D165) and the fee card, a policy
#: kind (D166) - each loaded once from the file it used to be read from;
#: 8 a sleeve can be disabled - kept in the library, not offered to PWAs - and
#: every disable and enable is on the record in sleeveSwitches (D169);
#: 9 a product has a category of its own, the one the catalogue bands it under
#: (D178); 10 a sleeve may be offered under several types, and a switch names
#: the type it added, took away or gave a copy of its own (D179); 11 a product
#: has a ticker and a minimum of its own in each base currency (D180); 12 a
#: product has the currency it is issued in (D184); 13 a product in a sleeve
#: may name the product held instead below its minimum (D192); 14 a rule may
#: name the custodians / entities an edition is for (D193).
SCHEMA_VERSION = 14

#: how a sleeve offered under several types stores them (D179), and the word
#: a file may use for every type
TYPE_SEP = '|'
ALL_TYPES = 'All'

#: what a history row's action can say. baseline is the one nobody performed:
#: it is the state a sleeve was in when history started being kept.
ACTIONS = ['baseline', 'created', 'updated', 'reverted', 'deleted', 'restored',
           'imported', 'seeded']
#: the actions that make a new VERSION of a sleeve - what it holds, its name
#: or its edition changed - as against the ones that move it in or out of
#: the library (D152)
_VERSION_ACTIONS = ('baseline', 'created', 'updated', 'reverted', 'imported', 'seeded')

_SLEEVES_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    id        INTEGER PRIMARY KEY,
    variant   TEXT NOT NULL,
    category  TEXT NOT NULL,
    name      TEXT NOT NULL,
    label     TEXT NOT NULL DEFAULT '',
    note      TEXT NOT NULL DEFAULT '',
    createdBy TEXT NOT NULL DEFAULT '',
    createdAt TEXT NOT NULL,
    updatedBy TEXT NOT NULL DEFAULT '',
    updatedAt TEXT NOT NULL,
    deletedBy TEXT NOT NULL DEFAULT '',
    deletedAt TEXT NOT NULL DEFAULT '',
    firstCreatedAt TEXT NOT NULL DEFAULT '',
    supersededBy   INTEGER NOT NULL DEFAULT 0,
    disabledBy     TEXT NOT NULL DEFAULT '',
    disabledAt     TEXT NOT NULL DEFAULT ''
)
"""

# The uniqueness of a sleeve name is a rule about the LIBRARY, not about the
# record: a deleted sleeve must not stop the same name being used again, and a
# partial index is how SQLite says exactly that. Since D89 the unit of
# uniqueness is the EDITION - name plus label - so a name may hold a fallback
# (label '') and any number of labelled editions.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sleeveProducts (
    sleeveId  INTEGER NOT NULL REFERENCES sleeves(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    productId TEXT NOT NULL,
    weight    REAL NOT NULL,
    alternative TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (sleeveId, productId)
);
CREATE TABLE IF NOT EXISTS sleeveRules (
    sleeveId       INTEGER NOT NULL REFERENCES sleeves(id) ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    currency       TEXT NOT NULL DEFAULT '',
    riskLevel      TEXT NOT NULL DEFAULT '',
    allocationType TEXT NOT NULL DEFAULT '',
    entity         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (sleeveId, position)
);
CREATE TABLE IF NOT EXISTS sleeveHistory (
    id        INTEGER PRIMARY KEY,
    sleeveId  INTEGER NOT NULL,
    revision  INTEGER NOT NULL,
    action    TEXT NOT NULL,
    variant   TEXT NOT NULL,
    category  TEXT NOT NULL,
    name      TEXT NOT NULL,
    label     TEXT NOT NULL DEFAULT '',
    note      TEXT NOT NULL DEFAULT '',
    products  TEXT NOT NULL,
    rules     TEXT NOT NULL DEFAULT '[]',
    actor     TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    UNIQUE (sleeveId, revision)
);
CREATE INDEX IF NOT EXISTS sleeves_by_home ON sleeves (variant, category);
CREATE INDEX IF NOT EXISTS sleeves_live ON sleeves (deletedAt);
CREATE UNIQUE INDEX IF NOT EXISTS sleeves_live_edition
    ON sleeves (variant, category, name, label) WHERE deletedAt = '';
CREATE INDEX IF NOT EXISTS history_by_sleeve ON sleeveHistory (sleeveId, revision);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policies (
    kind      TEXT NOT NULL,
    scope     TEXT NOT NULL,
    body      TEXT NOT NULL,
    revision  INTEGER NOT NULL,
    updatedBy TEXT NOT NULL DEFAULT '',
    updatedAt TEXT NOT NULL,
    PRIMARY KEY (kind, scope)
);
CREATE TABLE IF NOT EXISTS policyHistory (
    id       INTEGER PRIMARY KEY,
    kind     TEXT NOT NULL,
    scope    TEXT NOT NULL,
    revision INTEGER NOT NULL,
    action   TEXT NOT NULL,
    body     TEXT NOT NULL,
    note     TEXT NOT NULL DEFAULT '',
    actor    TEXT NOT NULL DEFAULT '',
    at       TEXT NOT NULL,
    UNIQUE (kind, scope, revision)
);
CREATE TABLE IF NOT EXISTS products (
    productId         TEXT PRIMARY KEY,
    position          INTEGER NOT NULL,
    name              TEXT NOT NULL,
    ticker            TEXT NOT NULL DEFAULT '',
    assetClass        TEXT NOT NULL DEFAULT '',
    category          TEXT NOT NULL DEFAULT '',
    style             TEXT NOT NULL DEFAULT '',
    vehicle           TEXT NOT NULL DEFAULT '',
    shareClass        TEXT NOT NULL DEFAULT '',
    source            TEXT NOT NULL DEFAULT '',
    liquidity         TEXT NOT NULL DEFAULT '',
    exposureCurrency  TEXT NOT NULL DEFAULT '',
    issueCurrency     TEXT NOT NULL DEFAULT '',
    productCost       REAL NOT NULL,
    feeGroup          TEXT NOT NULL,
    distributionYield REAL,
    minimumInvestment REAL,
    currencies        TEXT NOT NULL DEFAULT '',
    revision          INTEGER NOT NULL,
    createdBy         TEXT NOT NULL DEFAULT '',
    createdAt         TEXT NOT NULL,
    updatedBy         TEXT NOT NULL DEFAULT '',
    updatedAt         TEXT NOT NULL,
    withdrawnBy       TEXT NOT NULL DEFAULT '',
    withdrawnAt       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS productHistory (
    id        INTEGER PRIMARY KEY,
    productId TEXT NOT NULL,
    revision  INTEGER NOT NULL,
    action    TEXT NOT NULL,
    body      TEXT NOT NULL,
    note      TEXT NOT NULL DEFAULT '',
    source    TEXT NOT NULL DEFAULT '',
    actor     TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    UNIQUE (productId, revision)
);
CREATE TABLE IF NOT EXISTS sleeveSwitches (
    id        INTEGER PRIMARY KEY,
    sleeveId  INTEGER NOT NULL,
    action    TEXT NOT NULL,
    variant   TEXT NOT NULL,
    category  TEXT NOT NULL,
    name      TEXT NOT NULL,
    label     TEXT NOT NULL DEFAULT '',
    actor     TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    detail    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sleeve_switches_at ON sleeveSwitches (at);
CREATE INDEX IF NOT EXISTS products_live ON products (withdrawnAt);
CREATE INDEX IF NOT EXISTS product_history_at ON productHistory (at);
"""

_lock = threading.Lock()


def dbPath() -> str:
    return os.path.abspath(os.getenv('SCENARIO_SLEEVES_DB', '') or _DEFAULT_DB)


def seedPath() -> str:
    return os.path.abspath(os.getenv('SCENARIO_SLEEVES_SEED', '') or _DEFAULT_SEED)


def rulesPath() -> str:
    """The rules file: SCENARIO_SLEEVES_RULES, else sleeveRules.csv beside
    the seed. Optional - a library with no editions has no rules to read."""
    named = os.getenv('SCENARIO_SLEEVES_RULES', '')
    if named:
        return os.path.abspath(named)
    return os.path.join(os.path.dirname(seedPath()), 'sleeveRules.csv')


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


# ------------------------------------------------------------ vocabulary ---

def variantExists(variant) -> bool:
    return variant in VARIANTS


def typesOf(text) -> list:
    """The types a stored variant names, in its own order (D179)."""
    return [t for t in str(text or '').split(TYPE_SEP) if t]


def parseTypes(value) -> list:
    """The types a caller or a file names: a list, or a cell holding one
    type, several separated by TYPE_SEP, or ALL_TYPES. Spaces around each
    are dropped; nothing is checked here."""
    items = value if isinstance(value, (list, tuple)) else [value]
    out = []
    for item in items:
        for part in str(item or '').split(TYPE_SEP):
            part = ' '.join(part.split())
            if part.lower() == ALL_TYPES.lower():
                out.extend(VARIANTS)
            elif part:
                out.append(part)
    return out


def typesText(value) -> str:
    """The stored form of the types *value* names: each known, each once,
    in VARIANTS order. Raises a ValidationError naming the field."""
    named = parseTypes(value)
    if not named:
        raise ValidationError('variant', 'Choose an implementation type.')
    for variant in named:
        if not variantExists(variant):
            raise ValidationError('variant', '{!r} is not an implementation type.'.format(variant))
    return TYPE_SEP.join(v for v in VARIANTS if v in set(named))


def typesLabel(types) -> str:
    """Where a sleeve is offered, in words: the type, or every type, or the
    types it shares."""
    types = typesOf(types) if isinstance(types, str) else list(types)
    if len(types) > 1 and set(types) == set(VARIANTS):
        return 'All types'
    return ', '.join(types)


def fileTypes(text) -> str:
    """A stored variant as a file writes it: ALL_TYPES for every type."""
    types = typesOf(text)
    return ALL_TYPES if len(types) > 1 and set(types) == set(VARIANTS) else TYPE_SEP.join(types)


def underType(column='variant') -> str:
    """SQL that is true where *column* names the type bound to it, with
    typeArg(variant) as the argument."""
    return "instr('{sep}' || {col} || '{sep}', ?) > 0".format(sep=TYPE_SEP, col=column)


def typeArg(variant) -> str:
    return '{sep}{}{sep}'.format(variant, sep=TYPE_SEP)


def _sharing(row, types) -> list:
    """The types of *types* that the sleeve *row* is offered under too."""
    theirs = set(typesOf(row['variant']))
    return [t for t in types if t in theirs]


def fixedCategories() -> list:
    """The categories an overlay attaches a sleeve to automatically - the two
    seeded overlays' and every category a rule in the repository goes into
    (D155). Each holds at most one sleeve per type: one where the type offers
    the overlay, none where it does not, and the overlay is then not applied
    under that type (D191). Read from rules lazily - rules imports the sleeve
    facade, so a module-level import here would be circular."""
    from .rules import autoSleeveCategories
    return autoSleeveCategories()


def hasSleeve(category, variant, entity=None) -> bool:
    """Whether *variant* has a live, enabled sleeve in *category* - any
    edition of it, labelled or not. What decides whether an overlay into
    that category is applied under the type (D191); listSleeves without a
    key serves fallbacks only, so it cannot answer this.

    At an *entity* (D193), only an edition that may be for it - no rules, or
    a rule naming no entity or this one - and holds nothing it excludes."""
    if not variantExists(variant):
        return False
    barred = entities.excluded(entity)
    conn = _connect()
    try:
        for row in _sleeveRows(conn, variant, category):
            if _column(row, 'disabledAt'):
                continue
            if entity is None:
                return True
            rules = _ruleRows(conn, row['id'])
            if rules and not any(not r.get('entity') or entity in r['entity'] for r in rules):
                continue
            if any(p['productId'] in barred for p in _productRows(conn, row['id'])):
                continue
            return True
        return False
    finally:
        conn.close()


def categories() -> list:
    """Every category a sleeve is built for, in the implementation table's own
    order: the universe's categories, with each overlay rule's category where
    the rule places it - with the seeded rules, the volatility premium's after
    Investment Grade Fixed Income and the tilt's last (D155) - and grouped
    categories collapsed to the one name they share a sleeve under (D60). The
    two seeded overlays' categories stay even when no rule goes into them:
    their sleeves are in the library either way."""
    from .overlayRules import allRules
    from .rules import (OVERLAY_END, TACTICAL_TILT_CATEGORY, VOL_PREMIUM_CATEGORY,
                        categoriesInUniverseOrder, sleeveCategory)
    out = list(categoriesInUniverseOrder())
    for rule in allRules():
        if rule['into'] in out:
            continue
        place = rule.get('place') or OVERLAY_END
        if place != OVERLAY_END and place in out:
            out.insert(out.index(place) + 1, rule['into'])
        else:
            out.append(rule['into'])
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
        # the fee card, then the catalogue, once each (schema 7, D165, D166):
        # a product must name a group the card prices, and a sleeve names
        # products. Both read on this connection only - anything that opened
        # another here would wait on the lock this holds.
        from . import feeCard
        feeCard.seed(conn)
        products.seed(conn, feeCard.groupsOn(conn))
        _seedOnce(conn)
        # the overlay rules' default list, once (schema 6, D155); read from
        # overlayRules lazily - it imports rules, which imports this module
        from .overlayRules import seed as _seedOverlays
        _seedOverlays(conn)
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def connection() -> sqlite3.Connection:
    """A connection to the repository's file, schema in place, for the other
    settings kept beside the sleeves (fundingSplit, D148). The caller closes
    it."""
    return _connect()


#: POSIX: one handle per file, held open for reading the header (fileStamp)
_stampHandles = {}
_stampOrder = []
_stampLock = threading.Lock()
_STAMP_HANDLES = 16


def _header(path, inode) -> bytes:
    """The first 28 bytes of *path*, without closing a handle on it while the
    process may hold SQLite's locks on the file. On POSIX, closing ANY
    descriptor of a file releases every lock the process holds on it - the
    read here is asked for inside a write transaction (a save checks the
    catalogue) - so the file is read through a handle kept open, by offset.
    Windows locks per handle, and has no pread: it reads as it always has."""
    if not hasattr(os, 'pread') or os.name != 'posix':
        with open(path, 'rb') as handle:
            return handle.read(28)
    with _stampLock:
        held = _stampHandles.get(path)
        if held is None or held[0] != inode:
            if held is not None:
                _closeQuietly(held[1])
            fd = os.open(path, os.O_RDONLY)
            _stampHandles[path] = (inode, fd)
            if path in _stampOrder:
                _stampOrder.remove(path)
            _stampOrder.append(path)
            while len(_stampOrder) > _STAMP_HANDLES:
                gone = _stampHandles.pop(_stampOrder.pop(0), None)
                if gone is not None:
                    _closeQuietly(gone[1])
        return os.pread(_stampHandles[path][1], 28, 0)


def _closeQuietly(fd) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def fileStamp(path=None):
    """What identifies one state of the repository's file: its identity, size
    and modification time, and SQLite's own file change counter (header
    offset 24), which every committed write increments - the counter is what
    a coarse or cached modification time on a network share cannot hide.
    None when there is no file. The settings, the catalogue and the fee card
    are each kept in memory against it and re-read when it moves, so a read
    on the hot path costs a stat (D155, D165). The header is read without
    dropping the process's locks on the file (_header, D193)."""
    path = path or dbPath()
    try:
        st = os.stat(path)
        header = _header(path, st.st_ino)
    except OSError:
        return None
    counter = int.from_bytes(header[24:28], 'big') if len(header) >= 28 else -1
    return (path, st.st_ino, st.st_size, st.st_mtime_ns, counter)


def readOnly(path=None):
    """A plain read-only connection to the file, or None when it cannot be
    opened: no migration, no schema script, no seed. For the cached reads,
    which fall back to a full connection when what they need is not there
    yet."""
    path = path or dbPath()
    try:
        conn = sqlite3.connect('file:{}?mode=ro'.format(path), uri=True, timeout=10)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def _seedOnce(conn: sqlite3.Connection) -> None:
    """Fill an empty library from the seed - once, whatever else is starting.

    The service runs several workers, so *is the library empty?* and
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
    if 'deletedAt' not in columns:
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
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(sleeves)')}

    # 2 -> 3 (D89): the edition label on the sleeve and its revisions, the
    # rules on the revisions, and uniqueness widened from the name to the
    # edition. Additive, so ALTER TABLE does it and no row is rewritten -
    # every existing sleeve is the fallback edition of its name, which is
    # exactly what a blank label and no rules mean. The rules table and the
    # new index come from _SCHEMA, which every connection runs.
    if 'label' not in columns:
        history = {r['name'] for r in conn.execute('PRAGMA table_info(sleeveHistory)')}
        with conn:
            conn.execute("ALTER TABLE sleeves ADD COLUMN label TEXT NOT NULL DEFAULT ''")
            if 'label' not in history:
                conn.execute("ALTER TABLE sleeveHistory ADD COLUMN label TEXT NOT NULL DEFAULT ''")
            if 'rules' not in history:
                conn.execute("ALTER TABLE sleeveHistory ADD COLUMN rules TEXT NOT NULL DEFAULT '[]'")
            conn.execute('DROP INDEX IF EXISTS sleeves_live_name')
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('schemaVersion', ?)",
                         (str(SCHEMA_VERSION),))
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('migratedAt', ?)", (_now(),))

    # 4 -> 5 (D152): every sleeve carries the moment its CURRENT version was
    # created (createdAt, now re-stamped on every save) beside the moment the
    # sleeve itself first was (firstCreatedAt), and a version a save replaced
    # is kept in the archive pointing at the sleeve that replaced it
    # (supersededBy). Backfilled from the history: a live sleeve's version
    # was created by its latest revision that changed what it holds, and a
    # sleeve with no such revision keeps the stamp it has.
    if 'supersededBy' not in columns:
        with conn:
            conn.execute("ALTER TABLE sleeves ADD COLUMN firstCreatedAt TEXT NOT NULL DEFAULT ''")
            conn.execute('ALTER TABLE sleeves ADD COLUMN supersededBy INTEGER NOT NULL DEFAULT 0')
            conn.execute('UPDATE sleeves SET firstCreatedAt = createdAt')
            if _tableExists(conn, 'sleeveHistory'):
                conn.execute(
                    "UPDATE sleeves SET createdAt = COALESCE((SELECT h.at FROM sleeveHistory h "
                    "WHERE h.sleeveId = sleeves.id AND h.action IN ({}) "
                    "ORDER BY h.revision DESC LIMIT 1), createdAt)".format(
                        ','.join("'{}'".format(a) for a in _VERSION_ACTIONS)))

    # 7 -> 8 (D169): a sleeve can be disabled - in the library, not offered.
    # Additive: every sleeve already here is offered, which is what the
    # blank stamp says. The record of the switches is a table of its own,
    # from _SCHEMA, so a switch is not a revision and no pinned proposal
    # reads it as the sleeve having moved.
    if 'disabledAt' not in columns:
        with conn:
            conn.execute("ALTER TABLE sleeves ADD COLUMN disabledBy TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE sleeves ADD COLUMN disabledAt TEXT NOT NULL DEFAULT ''")

    # 8 -> 9 (D178): a product's own category. Additive and blank for every
    # product already here, which the catalogue bands by its sleeves as before
    # until a file or the form gives it one.
    productColumns = {r['name'] for r in conn.execute('PRAGMA table_info(products)')}
    if productColumns and 'category' not in productColumns:
        with conn:
            conn.execute("ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT ''")

    # 9 -> 10 (D179): a switch names the type it acted on. The sleeves
    # themselves need nothing: a sleeve under one type is stored as it was.
    if _tableExists(conn, 'sleeveSwitches'):
        switchColumns = {r['name'] for r in conn.execute('PRAGMA table_info(sleeveSwitches)')}
        if 'detail' not in switchColumns:
            with conn:
                conn.execute("ALTER TABLE sleeveSwitches ADD COLUMN detail TEXT NOT NULL DEFAULT ''")

    # 10 -> 11 (D180): a product's ticker and minimum by base currency, blank
    # for every product already here, which reads its main ones everywhere
    if productColumns and 'currencies' not in productColumns:
        with conn:
            conn.execute("ALTER TABLE products ADD COLUMN currencies TEXT NOT NULL DEFAULT ''")

    # 11 -> 12 (D184): the currency a product is issued in, blank for every
    # product already here until a file gives it
    if productColumns and 'issueCurrency' not in productColumns:
        with conn:
            conn.execute("ALTER TABLE products ADD COLUMN issueCurrency TEXT NOT NULL DEFAULT ''")

    # 12 -> 13 (D192): a product in a sleeve may name the product held instead
    # where the amount reaching it is below its minimum; blank for every row
    # already here, which holds its own product whatever the amount
    if _tableExists(conn, 'sleeveProducts'):
        held = {r['name'] for r in conn.execute('PRAGMA table_info(sleeveProducts)')}
        if 'alternative' not in held:
            with conn:
                conn.execute("ALTER TABLE sleeveProducts ADD COLUMN alternative TEXT NOT NULL DEFAULT ''")

    # 13 -> 14 (D193): a rule may name the custodians / entities an edition
    # is for; blank for every rule already here, which is for any entity
    if _tableExists(conn, 'sleeveRules'):
        ruleColumns = {r['name'] for r in conn.execute('PRAGMA table_info(sleeveRules)')}
        if 'entity' not in ruleColumns:
            with conn:
                conn.execute("ALTER TABLE sleeveRules ADD COLUMN entity TEXT NOT NULL DEFAULT ''")

    # 3 -> 4 (D148): the policies and their history. New tables only, which
    # _SCHEMA creates on every connection; the version is all there is to move.
    version = conn.execute("SELECT value FROM meta WHERE key = 'schemaVersion'").fetchone()
    if version is not None and int(version[0]) < SCHEMA_VERSION:
        with conn:
            conn.execute("UPDATE meta SET value = ? WHERE key = 'schemaVersion'",
                         (str(SCHEMA_VERSION),))
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('migratedAt', ?)", (_now(),))


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
    rulesFile = path if workbookRules(path) else rulesPath()
    ruleRows = list(readRuleRows(rulesFile)) if os.path.exists(rulesFile) else []
    _importInto(conn, rows, replace=False, user='seed', strict=False, action='seeded',
                ruleRows=ruleRows)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('seededAt', ?)", (stamp,))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('seededFrom', ?)", (path,))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('rulesFrom', ?)",
                 (rulesFile if ruleRows else '',))
    conn.commit()


# ------------------------------------------------------------- the seed ---

#: the seed workbook's sheets (D159): one file carries the products and the
#: editions' rules. A workbook without them is read from its first sheet.
SEED_SHEET = 'Sleeves'
RULES_SHEET = 'Rules'


def _tableRows(path: str, sheetName: str = None):
    """(header, row iterator) from a CSV, or from an XLSX's *sheetName* sheet
    when it has one, else its first sheet."""
    if path.lower().endswith('.csv'):
        # utf-8-sig drops a BOM from an Excel export; unwrapRows takes off a
        # pair of quotes wrapped around a whole row (csvText.py)
        with open(path, newline='', encoding='utf-8-sig') as fh:
            reader = csv.reader(unwrapRows(fh))
            head = [str(h).strip() for h in (next(reader, None) or [])]
            body = [row for row in reader if row and str(row[0]).strip()]
        return head, body
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True, data_only=True)
    sheet = book[sheetName] if sheetName and sheetName in book.sheetnames else book.active
    rows = sheet.iter_rows(values_only=True)
    head = [str(h).strip() if h is not None else '' for h in (next(rows, None) or [])]
    body = [row for row in rows if row and row[0] is not None and str(row[0]).strip()]
    return head, body


def readSeedRows(path: str):
    """Rows of the interchange shape from a CSV or XLSX:
    (variant, category, sleeve, edition, productId, weight, alternative).

    Both headers are read: the one delivered before editions existed, whose
    rows become fallback editions (edition ''), and the one with the Edition
    column. A reader is told which it has so the two never get confused.
    Either may carry a BelowMinimum column after its own: the product held
    instead where the amount reaching the row's product is below its
    minimum (D192); without it every row's alternative is ''."""
    head, body = _tableRows(path, SEED_SHEET)
    if head[:6] == SEED_COLUMNS_EDITIONS:
        width, editions = 6, True
    elif head[:5] == SEED_COLUMNS:
        width, editions = 5, False
    else:
        raise ValueError('{}: header is {!r}, expected {!r} or {!r}'.format(
            path, head, SEED_COLUMNS_EDITIONS, SEED_COLUMNS))
    alternatives = len(head) > width and str(head[width] or '').strip() == ALTERNATIVE_COLUMN
    for row in body:
        cells = ['' if v is None else str(v).strip() for v in row[:width]]
        while len(cells) < width:
            cells.append('')
        if not editions:
            cells.insert(3, '')
        try:
            cells[5] = float(cells[5])
        except ValueError:
            raise ValueError('{}: weight {!r} is not a number'.format(' / '.join(cells[:4]), cells[5]))
        alternative = row[width] if alternatives and len(row) > width else None
        cells.append('' if alternative is None else str(alternative).strip())
        yield tuple(cells)


def workbookRules(path: str) -> bool:
    """Whether *path* is a seed workbook carrying its own Rules sheet (D159)."""
    if not path.lower().endswith('.xlsx') or not os.path.exists(path):
        return False
    from openpyxl import load_workbook
    return RULES_SHEET in load_workbook(path, read_only=True).sheetnames


def readRuleRows(path: str):
    """Rules from a CSV or XLSX with RULE_COLUMNS: one dict per row, the
    fields as lists (blank means any), and the entities from an Entity
    column after them where the file has one (D193)."""
    head, body = _tableRows(path, RULES_SHEET)
    if head[:7] != RULE_COLUMNS:
        raise ValueError('{}: header is {!r}, expected {!r}'.format(path, head, RULE_COLUMNS))
    width = 8 if len(head) > 7 and str(head[7] or '').strip() == ENTITY_COLUMN else 7
    for row in body:
        cells = ['' if v is None else str(v).strip() for v in row[:width]]
        while len(cells) < 8:
            cells.append('')
        yield {'variant': cells[0], 'category': cells[1], 'sleeve': cells[2], 'edition': cells[3],
               'currency': sleeveRules.parseCell(cells[4]),
               'riskLevel': sleeveRules.parseCell(cells[5]),
               'allocationType': sleeveRules.parseCell(cells[6]),
               'entity': sleeveRules.parseCell(cells[7])}


# ------------------------------------------------------------ validation ---

def _validate(conn, variant, category, name, note, productRows, sleeveId=None,
              label='', rules=None):
    """Everything an edition has to satisfy to be saved. Raises the first
    failure as a ValidationError naming the field; returns the cleaned
    (name, note, [(productId, weight, alternative)], label, rules).

    The edition rules (D89): a labelled edition carries rules and a fallback
    carries none, never the other way round; labels are unique within a name;
    a name has at most one fallback; and two labelled editions of one name may
    not claim the same portfolio - the clash is named, portfolio by portfolio,
    because it is computed rather than guessed.

    *variant* is every type the edition is offered under (D179), stored or
    as a list, and each rule is asked of each of them: a sleeve shared by
    two types meets every sleeve of either.

    Also returned, last: the stored form of the types."""
    stored = typesText(variant)
    types = typesOf(stored)
    if category not in categories():
        raise ValidationError('category', '{!r} is not a category a sleeve can implement.'.format(category))
    # one space between words, none around them (D157): "Active  Passive"
    # is the name "Active Passive", not a second sleeve that reads the same
    name = normaliseName(name)
    if not name:
        raise ValidationError('name', 'Give the sleeve a name.')
    if len(name) > NAME_MAX:
        raise ValidationError('name', 'Keep the name to {} characters.'.format(NAME_MAX))
    note = (note or '').strip()
    # an edition label is spelled like a name (D158): one space between words,
    # and one label per name whatever its capitals - "gbp" beside "GBP" is the
    # same edition twice; "G BP" is a different label
    label = normaliseName(label)
    if label.lower() in RESERVED_LABELS:
        raise ValidationError('label', '"{}" is what the console calls the edition without rules; '
                                       'give this edition another label.'.format(label))
    if len(label) > NAME_MAX:
        raise ValidationError('label', 'Keep the edition label to {} characters.'.format(NAME_MAX))
    rules = sleeveRules.normalise(rules)
    if rules and not label:
        raise ValidationError('label', 'An edition with rules needs a label - the desk\'s short '
                                       'name for it, such as "GBP" or "GBP ex-Alts".')
    if label and not rules:
        raise ValidationError('rules', 'The {} edition has no rules. Add at least one, or clear the '
                                       'label to make it the fallback.'.format(label))

    # every live sleeve of the category offered under one of these types
    # too, and of those the editions of this name - the ones it meets
    near = [r for r in conn.execute(
        "SELECT * FROM sleeves WHERE category = ? AND deletedAt = '' AND id != ?",
        (category, sleeveId if sleeveId is not None else -1)).fetchall() if _sharing(r, types)]
    siblings = [r for r in near if r['name'] == name]
    # A name that differs from one the type already has here only in capitals
    # or spacing is that name (D157): refused, naming the spelling in use,
    # rather than becoming a second sleeve PWAs could not tell apart.
    for other in near:
        if other['name'] != name and _nameKey(other['name']) == _nameKey(name):
            raise ValidationError(
                'name', 'A sleeve named {} already exists in {} under {}; names that differ only in '
                'capitals or spacing are the same name. Use {} to add an edition to it, or choose '
                'another name.'.format(other['name'], category,
                                       typesLabel(_sharing(other, types)), other['name']))
    for sib in siblings:
        if _nameKey(sib['label']) == _nameKey(label):
            if label:
                raise ValidationError(
                    'label', '{} already has a {} edition in {} under {}.'.format(
                        name, sib['label'], category, typesLabel(_sharing(sib, types))))
            # a second fallback is, from where the desk stands, the name
            # clash it always was: the same words, on the same field
            raise ValidationError(
                'name', 'A sleeve named {} already exists in {} under {}. To offer it for '
                'particular portfolios, add an edition to it instead.'.format(
                    name, category, typesLabel(_sharing(sib, types))))
    if rules:
        for sib in siblings:
            sibRules = _ruleRows(conn, sib['id'])
            if not sibRules:
                continue
            shared = sleeveRules.overlap(rules, sibRules)
            if shared:
                raise ValidationError(
                    'rules', 'Overlaps the {} edition on {} portfolio{}: {}{}. Narrow one of them; '
                    'exactly one edition may apply to a portfolio.'.format(
                        sib['label'], len(shared), '' if len(shared) == 1 else 's',
                        ', '.join(shared[:4]), '' if len(shared) <= 4 else ', …'))

    if category in fixedCategories():
        # one NAME per type - its editions are all that one sleeve
        if any(r['name'] != name for r in near):
            raise ValidationError(
                'category', '{} holds one sleeve at most under each implementation type - '
                'it is attached automatically by its overlay. Edit the one it has.'.format(category))

    cleaned, seen, total = [], set(), 0.0
    for entry in productRows or []:
        pid = str(entry.get('productId', '') if isinstance(entry, dict) else entry[0]).strip()
        raw = entry.get('weight') if isinstance(entry, dict) else entry[1]
        alternative = _alternativeOf(entry, pid)
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
        cleaned.append((pid, weight, alternative))
        total += weight
    if not cleaned:
        raise ValidationError('products', 'A sleeve needs at least one product.')
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        raise ValidationError(
            'weights', 'Weights sum to {}%; a sleeve must sum to exactly 100%.'.format(_pctText(total * 100.0)))
    return name, note, cleaned, label, rules, stored


def _alternativeOf(entry, pid) -> str:
    """The product a row names to hold instead where the amount reaching
    *pid* is below its minimum (D192): '' for none. It must be in the
    catalogue and be another product; it may be one the sleeve holds too,
    whose line the amount then joins."""
    raw = entry.get('alternative') if isinstance(entry, dict) else (
        entry[2] if len(entry) > 2 else '')
    if raw is not None and not isinstance(raw, str):
        raise ValidationError('products', 'Name the product held below the minimum by its id.')
    alternative = (raw or '').strip()
    if not alternative:
        return ''
    if alternative == pid:
        raise ValidationError('products', 'A product cannot be its own alternative below its minimum.')
    if not products.has(alternative):
        raise ValidationError('products', 'Product {!r}, named to hold below the minimum, is not in '
                                          'the catalogue.'.format(alternative))
    return alternative


def _held(rows) -> list:
    """Product rows as the store reads them, in the cleaned shape."""
    return [(r['productId'], r['weight'], _column(r, 'alternative') or '') for r in rows]


def _asRows(rows) -> list:
    """Product rows as the store reads them, in the shape a save takes."""
    return [{'productId': p, 'weight': w, 'alternative': a} for p, w, a in _held(rows)]


#: labels the console uses for the edition without rules (D158)
RESERVED_LABELS = ('fallback', 'the fallback')


def normaliseName(name) -> str:
    """A sleeve name as stored: trimmed, inner runs of spaces collapsed (D157)."""
    return ' '.join(str(name or '').split())


def _nameKey(name) -> str:
    """What makes two names the same name: spacing and capitals aside (D157)."""
    return normaliseName(name).lower()


def _pctText(pct) -> str:
    """A total as the desk needs to read it to see why it is refused: two
    places, or as many as it takes to differ from 100 (99.999, not 100.00)."""
    for places in (2, 3, 4):
        text = '{:.{}f}'.format(pct, places)
        if abs(float(text) - pct) < 1e-9 or text not in ('100.00', '100.000', '100.0000'):
            return text
    return '{:.6f}'.format(pct).rstrip('0')


# --------------------------------------------------------------- reading ---

def _sleeveRows(conn, variant=None, category=None, deleted=False):
    """Live sleeves by default. *deleted* True lists the removed ones instead -
    the two are never mixed, because every consumer wants one or the other.
    *variant* lists the sleeves offered under that type, shared ones too."""
    sql = 'SELECT * FROM sleeves'
    clauses, args = ["deletedAt {} ''".format('!=' if deleted else '=')], []
    if variant is not None:
        clauses.append(underType()); args.append(typeArg(variant))
    if category is not None:
        clauses.append('category = ?'); args.append(category)
    sql += ' WHERE ' + ' AND '.join(clauses)
    sql += ' ORDER BY {}variant, category, id'.format('deletedAt DESC, ' if deleted else '')
    return conn.execute(sql, args).fetchall()


def _productRows(conn, sleeveId):
    return conn.execute(
        'SELECT productId, weight, position, alternative FROM sleeveProducts WHERE sleeveId = ? '
        'ORDER BY position',
        (sleeveId,)).fetchall()


def _ruleRows(conn, sleeveId) -> list:
    """An edition's rules in the canonical shape: [] for the fallback."""
    out = []
    for r in conn.execute('SELECT currency, riskLevel, allocationType, entity FROM sleeveRules '
                          'WHERE sleeveId = ? ORDER BY position', (sleeveId,)):
        rule = {}
        for field in sleeveRules.FIELDS:
            values = sleeveRules.parseCell(r[field])
            if values:
                rule[field] = values
        if rule:
            out.append(rule)
    return out


def _resolveEdition(conn, rows, key, entity=None):
    """The one row of a name to serve for *key*: the edition whose rules
    match, else the fallback, else None. With no key only the fallback can
    answer. More than one match is a library fault - the universe moved under
    two editions that were disjoint when saved - and the name is withheld
    rather than one of them chosen; the console lists both as broken."""
    fallback, hits = None, []
    for row in rows:
        rules = _ruleRows(conn, row['id'])
        if not rules:
            if fallback is None:
                fallback = row
        elif key is not None and sleeveRules.matches(rules, key, entity):
            hits.append(row)
    if len(hits) == 1:
        return hits[0]
    if hits:
        return None
    return fallback


def _served(row, productRows):
    """The shape a sleeve has always been served in - name and fully hydrated
    products - or None when a product is missing from the catalogue. Each
    product carries the one held instead below its minimum, hydrated, or None
    (D192); one the catalogue no longer has is None, and the console lists it
    as a problem."""
    served = []
    for pr in productRows:
        product = products.get(pr['productId'])
        if product is None:
            return None
        product['weight'] = pr['weight']
        alternative = _column(pr, 'alternative') or ''
        product['alternative'] = products.get(alternative) if alternative else None
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
        alternative = _column(pr, 'alternative') or ''
        held = products.get(alternative) if alternative else None
        if alternative and held is None:
            problems.append('Product {!r}, held below the minimum of {}, is no longer in the '
                            'catalogue.'.format(alternative, (product or {}).get('name') or pr['productId']))
        hydrated.append({'productId': pr['productId'], 'weight': pr['weight'],
                         'position': pr['position'], 'product': product,
                         'alternative': alternative, 'alternativeProduct': held})
        total += pr['weight']
    if not productRows:
        problems.append('The sleeve has no products.')
    elif abs(total - 1.0) > WEIGHT_TOLERANCE:
        problems.append('Weights sum to {:.2f}%, not 100%.'.format(total * 100.0))
    rules = _ruleRows(conn, row['id'])
    types = typesOf(row['variant'])
    sameName = conn.execute(
        "SELECT id, label, variant FROM sleeves WHERE category = ? AND name = ? "
        "AND deletedAt = '' ORDER BY id", (row['category'], row['name'])).fetchall()
    if rules:
        # an overlap can only arise after the fact - the universe changed
        # under two editions that were disjoint when saved - and it is shown
        # exactly like a missing product: a problem, and withheld meanwhile
        for sib in sameName:
            if sib['id'] == row['id'] or not _sharing(sib, types):
                continue
            shared = sleeveRules.overlap(rules, _ruleRows(conn, sib['id']))
            if shared:
                problems.append('Overlaps the {} edition on {} portfolio{}: {}{}.'.format(
                    sib['label'], len(shared), '' if len(shared) == 1 else 's',
                    ', '.join(shared[:4]), '' if len(shared) <= 4 else ', …'))
    # every type a sleeve of this name is offered under, this one or another:
    # a type with a copy of its own is in the list, and not in variants (D179)
    under = set()
    for r in sameName:
        under.update(typesOf(r['variant']))
    offeredUnder = [v for v in VARIANTS if v in under] + sorted(under - set(VARIANTS))
    return {
        # variant says where in words - the type, or the types it is shared
        # by - and variants is the list (D179)
        'id': row['id'], 'variant': typesLabel(types), 'variants': types,
        'shared': len(types) > 1, 'category': row['category'],
        'name': row['name'], 'note': row['note'],
        # the edition (D89): its label, its rules, and how many strategic
        # portfolios those rules name - None for the fallback, which applies
        # wherever no labelled edition does
        'label': row['label'], 'rules': rules, 'fallback': not rules,
        'applies': len(sleeveRules.applicability(rules)) if rules else None,
        'fixed': row['category'] in fixedCategories(),
        'products': hydrated, 'problems': problems, 'offeredUnder': offeredUnder,
        # createdAt is when THIS version was created: a save re-stamps it and
        # the version it replaced goes to the archive with its own (D152)
        'createdBy': row['createdBy'], 'createdAt': row['createdAt'],
        'firstCreatedAt': _column(row, 'firstCreatedAt') or row['createdAt'],
        'supersededBy': _column(row, 'supersededBy') or None,
        'updatedBy': row['updatedBy'], 'updatedAt': row['updatedAt'],
        # the one place the stored word becomes the shown one: a caller DELETES
        # a sleeve, and what happens to it is that it is ARCHIVED (D66)
        'archived': bool(row['deletedAt']),
        'archivedBy': row['deletedBy'], 'archivedAt': row['deletedAt'],
        # in the library but not offered to PWAs (D169)
        'disabled': bool(_column(row, 'disabledAt')),
        'disabledBy': _column(row, 'disabledBy') or '', 'disabledAt': _column(row, 'disabledAt') or '',
        'revisions': revisions if revisions is not None else _revisionCount(conn, row['id']),
    }


def _column(row, name):
    """A column an older file may not have yet, read as None rather than
    failing the read."""
    return row[name] if name in row.keys() else None


def listSleeves(category: str, variant: str, key=None, entity=None) -> list:
    """The library for one category under one type, in the shape every
    consumer has always read: one entry per NAME, well-formed sleeves only.

    *key* is the base portfolio the sleeves are for (D89). Each name resolves
    to the edition whose rules match it, else its fallback; a name with
    neither is not offered under that portfolio. With no key only fallbacks
    are served, which is the whole library as it stood before editions.
    Nothing served says which edition it is - the PWA is not to know.

    An unknown type returns nothing rather than falling back: the caller has
    to have chosen one, and quietly serving the Multi-Asset library to a book
    that cannot hold it is the worse failure. An empty list is also a
    legitimate answer for a known type - it reaches no sleeve for that
    category, or every sleeve it has is broken - and the completeness gate
    reports it as such.

    A disabled sleeve (D169) is left out as though it were not there: a
    disabled edition lets the name fall back to its fallback edition, and a
    name whose every edition is disabled is not offered.

    *entity* is the custodian / entity the proposal is for (D193): an edition
    may be for particular entities, and the one resolved is judged against
    the entity's exclusions. One holding a product the entity excludes is
    still served, saying so under ``notAvailable`` - the picker greys it out
    with the reason - and an alternative the entity excludes is not one
    there."""
    if not variantExists(variant):
        return []
    barred = entities.excluded(entity)
    conn = _connect()
    try:
        byName, order = {}, []
        for row in _sleeveRows(conn, variant, category):
            if _column(row, 'disabledAt'):
                continue
            if row['name'] not in byName:
                byName[row['name']] = []
                order.append(row['name'])
            byName[row['name']].append(row)
        out = []
        for name in order:
            row = _resolveEdition(conn, byName[name], key, entity)
            if row is None:
                continue
            served = _served(row, _productRows(conn, row['id']))
            if served is None:
                continue
            if barred:
                held = [p['name'] for p in served['products'] if p['productId'] in barred]
                if held:
                    served['notAvailable'] = {'entity': entity, 'entityName': entities.name(entity),
                                              'products': held}
                for product in served['products']:
                    if product.get('alternative') and product['alternative']['productId'] in barred:
                        product['alternative'] = None
            out.append(served)
        return out
    finally:
        conn.close()


def sleeveExists(category: str, name, variant: str, key=None, entity=None) -> bool:
    """Whether *name* is offered for *key* at *entity* - and may be held
    there: a sleeve greyed out at the entity (D193) is not."""
    return any(s['name'] == name and not s.get('notAvailable')
               for s in listSleeves(category, variant, key, entity))


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


def revisionMap() -> dict:
    """sleeveId -> (revisions, archived) for every sleeve the library holds.
    Two reads and no hydration: the register asks this of the whole library at
    once, where asking a sleeve at a time is a query per pinned sleeve per
    proposal (D116)."""
    conn = _connect()
    try:
        counts = _revisionCounts(conn)
        return {row['id']: (counts.get(row['id'], 0), bool(row['deletedAt']))
                for row in conn.execute('SELECT id, deletedAt FROM sleeves')}
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
    does. Order is the sleeve's own order and is part of what is preserved.
    A row naming an alternative below its minimum carries it third (D192);
    the rest stay pairs, as every revision written before it is."""
    return json.dumps([[held[0], float(held[1])] + ([held[2]] if len(held) > 2 and held[2] else [])
                       for held in cleaned])


def _unpackProducts(packed):
    """[(productId, weight, alternative)] from a revision, '' for none."""
    try:
        return [(str(held[0]), float(held[1]), str(held[2]) if len(held) > 2 else '')
                for held in json.loads(packed or '[]')]
    except (ValueError, TypeError, IndexError):
        return []


def _revisionCount(conn, sleeveId) -> int:
    return conn.execute('SELECT COUNT(*) FROM sleeveHistory WHERE sleeveId = ?',
                        (sleeveId,)).fetchone()[0]


def _revisionCounts(conn) -> dict:
    """Every sleeve's revision count in one query, for the list views."""
    return {r['sleeveId']: r['n'] for r in conn.execute(
        'SELECT sleeveId, COUNT(*) AS n FROM sleeveHistory GROUP BY sleeveId')}


def _recordHistory(conn, sleeveId, action, variant, category, name, note, cleaned,
                   actor, at=None, label='', rules=None) -> int:
    """Append one revision. Called inside the caller's transaction, always -
    a write that reached the library but not the history would be worse than
    either failing. The label and rules ride along (D89) so a revert puts
    back the whole edition, not only its products."""
    revision = conn.execute(
        'SELECT COALESCE(MAX(revision), 0) FROM sleeveHistory WHERE sleeveId = ?',
        (sleeveId,)).fetchone()[0] + 1
    conn.execute(
        'INSERT INTO sleeveHistory (sleeveId, revision, action, variant, category, name, '
        'label, note, products, rules, actor, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (sleeveId, revision, action, variant, category, name, label or '', note,
         _packProducts(cleaned), sleeveRules.pack(rules), actor, at or _now()))
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
    if (previous['label'] or '') != (current['label'] or ''):
        out.append('Relabelled from {}'.format(previous['label']) if previous['label']
                   else 'Labelled {}'.format(current['label']))
    if (previous['rules'] or '[]') != (current['rules'] or '[]'):
        now = sleeveRules.unpack(current['rules'])
        out.append('Rules now: {}'.format(sleeveRules.describe(now)) if now else 'Rules cleared')
    if (previous['note'] or '') != (current['note'] or ''):
        out.append('Note changed' if current['note'] else 'Note cleared')
    wasHeld = _unpackProducts(previous['products'])
    nowHeld = _unpackProducts(current['products'])
    was = {p: w for p, w, _ in wasHeld}
    now = {p: w for p, w, _ in nowHeld}
    for pid in [p for p in now if p not in was]:
        out.append('Added {} at {:g}%'.format(_productLabel(pid), now[pid] * 100.0))
    for pid in [p for p in was if p not in now]:
        out.append('Removed {}'.format(_productLabel(pid)))
    for pid in [p for p in now if p in was and not _weightsMatch(was[p], now[p])]:
        out.append('{} {:g}% to {:g}%'.format(
            _productLabel(pid), was[pid] * 100.0, now[pid] * 100.0))
    # the product held instead below the minimum (D192)
    wasAlt = {p: a for p, _, a in wasHeld}
    for pid, _, alt in nowHeld:
        if pid in was and wasAlt.get(pid, '') != alt:
            out.append('{}: below its minimum, holds {}'.format(_productLabel(pid), _productLabel(alt))
                       if alt else '{}: no alternative below its minimum'.format(_productLabel(pid)))
        elif pid not in was and alt:
            out.append('{}: below its minimum, holds {}'.format(_productLabel(pid), _productLabel(alt)))
    return out


def _productLabel(productId) -> str:
    product = products.get(productId)
    return product['name'] if product else productId


def _historyEntry(row, previous, currentRevision) -> dict:
    hydrated = []
    for pid, weight, alternative in _unpackProducts(row['products']):
        hydrated.append({'productId': pid, 'weight': weight, 'product': products.get(pid),
                         'alternative': alternative})
    return {
        'sleeveId': row['sleeveId'], 'revision': row['revision'], 'action': row['action'],
        'variant': typesLabel(row['variant']), 'variants': typesOf(row['variant']),
        'category': row['category'], 'name': row['name'],
        'label': row['label'], 'rules': sleeveRules.unpack(row['rules']),
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
        clauses.append(underType('h.variant')); args.append(typeArg(variant))
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
            or q in (entry['actor'] or '').lower() or q in ' '.join(entry['variants']).lower():
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
               'p.label AS prevLabel, p.rules AS prevRules, '
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
                            'products': row['prevProducts'], 'label': row['prevLabel'],
                            'rules': row['prevRules']}
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
                # a shared sleeve's revision counts under each of its types
                for k in (typesOf(r['k']) if dimension == 'variant' else [r['k']]):
                    out[k] = out.get(k, 0) + r['n']
            return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))

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

ARCHIVE_COLUMNS = ['SleeveId', 'Variant', 'Category', 'Sleeve', 'Edition', 'Rules', 'ArchivedAt',
                   'ArchivedBy', 'CreatedAt', 'CreatedBy', 'Revisions', 'Products']
ACTIVITY_COLUMNS = ['At', 'Action', 'Actor', 'Variant', 'Category', 'Sleeve', 'Edition', 'Rules',
                    'SleeveId', 'Revision', 'Changes', 'Products']


def _productsCell(products) -> str:
    return '; '.join('{} {:.2f}%'.format(r['productId'], r['weight'] * 100.0) for r in products)


def archiveRows() -> list:
    """The archive as a table, one row per archived sleeve, for the export."""
    out = []
    for e in listArchived():
        out.append((e['id'], e['variant'], e['category'], e['name'], e['label'],
                    sleeveRules.describe(e['rules']), e['archivedAt'],
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
                        e['name'], e['label'], sleeveRules.describe(e['rules']),
                        e['sleeveId'], e['revision'], ' | '.join(e['changes']),
                        _productsCell(e['products'])))
        if not page['next'] or not page['entries']:
            return out
        before = page['next']


# --------------------------------------------------------------- writing ---

def _writeProducts(conn, sleeveId, cleaned):
    conn.execute('DELETE FROM sleeveProducts WHERE sleeveId = ?', (sleeveId,))
    conn.executemany(
        'INSERT INTO sleeveProducts (sleeveId, position, productId, weight, alternative) '
        'VALUES (?, ?, ?, ?, ?)',
        [(sleeveId, position, held[0], held[1], held[2] if len(held) > 2 else '')
         for position, held in enumerate(cleaned)])


def _writeRules(conn, sleeveId, rules):
    conn.execute('DELETE FROM sleeveRules WHERE sleeveId = ?', (sleeveId,))
    conn.executemany(
        'INSERT INTO sleeveRules (sleeveId, position, currency, riskLevel, allocationType, entity) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        [(sleeveId, position, sleeveRules.cell(r.get('currency')),
          sleeveRules.cell(r.get('riskLevel')), sleeveRules.cell(r.get('allocationType')),
          sleeveRules.cell(r.get('entity')))
         for position, r in enumerate(rules or [])])


def createSleeves(variants, category, name, productRows, note='', user='', label='',
                  rules=None) -> list:
    """A sleeve offered under one or more implementation types (D61, D179).

    Several types make ONE sleeve shared by them all - one id, one history,
    so an edit under any of them is what each serves - not a copy per type.
    It is validated under every type before it is written, so a name one of
    them already has leaves nothing half-applied. Returns [the sleeve], a
    list as it always was.
    """
    requested = list(dict.fromkeys(parseTypes(variants or [])))
    if not requested:
        raise ValidationError('variants', 'Choose at least one implementation type.')
    for variant in requested:
        # named and unknown, not quietly dropped: a caller that asks for a
        # type that does not exist has made a mistake worth hearing about
        if not variantExists(variant):
            raise ValidationError('variant', '{!r} is not an implementation type.'.format(variant))
    conn = _connect()
    try:
        cleanName, cleanNote, cleaned, cleanLabel, cleanRules, stored = _validate(
            conn, requested, category, name, note, productRows, label=label, rules=rules)
        stamp = _now()
        with conn:
            cur = conn.execute(
                'INSERT INTO sleeves (variant, category, name, label, note, createdBy, '
                'createdAt, updatedBy, updatedAt, firstCreatedAt) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (stored, category, cleanName, cleanLabel, cleanNote, user, stamp, user,
                 stamp, stamp))
            _writeProducts(conn, cur.lastrowid, cleaned)
            _writeRules(conn, cur.lastrowid, cleanRules)
            _recordHistory(conn, cur.lastrowid, 'created', stored, category,
                           cleanName, cleanNote, cleaned, user, stamp,
                           label=cleanLabel, rules=cleanRules)
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (cur.lastrowid,)).fetchone()
        return [_entry(conn, row, _productRows(conn, row['id']))]
    finally:
        conn.close()


def createSleeve(variant, category, name, productRows, note='', user='', label='',
                 rules=None) -> dict:
    """One sleeve: under one type, or the types a stored variant names."""
    return createSleeves([variant], category, name, productRows, note=note, user=user,
                         label=label, rules=rules)[0]


def addEdition(sleeveId, label, rules, productRows, note='', user='') -> dict:
    """Another edition of an existing sleeve's name, in the same category and
    under the same types (D89, D179): the way the desk actually works, starting from an edition
    it has and changing the products and the portfolios it is for. The same
    create path, so it meets the same rules - a label the name already
    carries, or rules that overlap a sibling's, are refused."""
    conn = _connect()
    try:
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        if row is None:
            raise ValidationError('id', 'That sleeve no longer exists.')
        if row['deletedAt']:
            raise ValidationError('id', 'That sleeve was removed from the library. '
                                        'Restore it before adding an edition.')
        variant, category, name = row['variant'], row['category'], row['name']
    finally:
        conn.close()
    return createSleeve(variant, category, name, productRows, note=note, user=user,
                        label=label, rules=rules)


def _base(base):
    """The revision count an edit started from, or None when the caller did
    not say (D156)."""
    if base is None:
        return None
    if isinstance(base, bool) or not isinstance(base, int) or base < 0:
        raise ValidationError('base', 'The version the edit started from is not a revision number.')
    return base


def _refuseStale(conn, row, base) -> None:
    """Optimistic concurrency (D156): a save names the revision count the
    editor opened, and a sleeve saved by someone else since then is refused
    rather than silently overwritten - last save no longer wins."""
    if base is None:
        return
    now = _revisionCount(conn, row['id'])
    if now != base:
        raise StaleError(
            'base', '{} was changed by {} at {} since you opened it (now revision {}). '
            'Reload it to see the change, then make yours again.'.format(
                row['name'], row['updatedBy'] or 'someone', (row['updatedAt'] or '').replace('T', ' '), now))


def updateSleeve(sleeveId, name, productRows, note='', user='', action='updated',
                 label=None, rules=None, base=None) -> dict:
    """Save a sleeve and append the revision that records it. A deleted sleeve
    is not editable: restore it first, so the history reads in the order the
    events happened. *label* and *rules* left None keep what the edition has;
    given, they are validated with everything else (D89).

    *base* is the revision count the edit started from (D156): given, the
    check and the write happen under SQLite's write lock, and a sleeve saved
    by someone else in between raises StaleError instead of being overwritten."""
    base = _base(base)
    conn = _connect()
    try:
        if base is not None:
            conn.commit()                       # nothing open, so BEGIN is legal
            conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        if row is None:
            raise ValidationError('id', 'That sleeve no longer exists.')
        if row['deletedAt']:
            raise ValidationError('id', 'That sleeve was removed from the library. '
                                        'Restore it before editing it.')
        _refuseStale(conn, row, base)
        if label is None:
            label = row['label']
        if rules is None:
            rules = _ruleRows(conn, sleeveId)
        name, note, cleaned, label, rules, _ = _validate(
            conn, row['variant'], row['category'], name, note, productRows, sleeveId=sleeveId,
            label=label, rules=rules)
        stamp = _now()
        before = _held(_productRows(conn, sleeveId))
        beforeRules = _ruleRows(conn, sleeveId)
        changed = (name != row['name'] or label != row['label'] or note != row['note']
                   or sleeveRules.pack(rules) != sleeveRules.pack(beforeRules)
                   or len(before) != len(cleaned)
                   or any(a[0] != b[0] or not _weightsMatch(a[1], b[1]) or a[2] != b[2]
                          for a, b in zip(before, cleaned)))
        kept = None
        with conn:
            if changed:
                kept = _archiveVersion(conn, row, before, beforeRules, user, stamp)
                conn.execute('UPDATE sleeves SET name = ?, label = ?, note = ?, updatedBy = ?, '
                             'updatedAt = ?, createdBy = ?, createdAt = ?, '
                             "firstCreatedAt = CASE firstCreatedAt WHEN '' THEN ? "
                             'ELSE firstCreatedAt END WHERE id = ?',
                             (name, label, note, user, stamp, user, stamp, row['createdAt'],
                              sleeveId))
            else:
                conn.execute('UPDATE sleeves SET updatedBy = ?, updatedAt = ? WHERE id = ?',
                             (user, stamp, sleeveId))
            _writeProducts(conn, sleeveId, cleaned)
            _writeRules(conn, sleeveId, rules)
            _recordHistory(conn, sleeveId, action, row['variant'], row['category'],
                           name, note, cleaned, user, stamp, label=label, rules=rules)
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        out = _entry(conn, row, _productRows(conn, row['id']))
        # the version the save replaced, as the archive lists it, so the
        # console can show it there without reloading the library (D152)
        if kept is not None:
            keptRow = conn.execute('SELECT * FROM sleeves WHERE id = ?', (kept,)).fetchone()
            out['archivedVersion'] = _entry(conn, keptRow, _productRows(conn, kept), 0)
        return out
    finally:
        conn.close()


def _archiveVersion(conn, row, cleaned, rules, user, stamp):
    """Keep the version a save is about to replace, in the archive (D152).

    A row of its own, archived at *stamp* by whoever saved, carrying the
    version's own created stamp and pointing at the sleeve that replaced it.
    It has no history of its own - the sleeve's history already holds the
    revision - and it is restored like any archived sleeve, which the live
    edition of its name will refuse until that is renamed or archived.
    Inside the caller's transaction."""
    cur = conn.execute(
        'INSERT INTO sleeves (variant, category, name, label, note, createdBy, createdAt, '
        'updatedBy, updatedAt, deletedBy, deletedAt, firstCreatedAt, supersededBy) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (row['variant'], row['category'], row['name'], row['label'], row['note'],
         row['createdBy'], row['createdAt'], row['updatedBy'], row['updatedAt'],
         user, stamp, _column(row, 'firstCreatedAt') or row['createdAt'], row['id']))
    _writeProducts(conn, cur.lastrowid, cleaned)
    _writeRules(conn, cur.lastrowid, rules)
    return cur.lastrowid


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
        cleaned = _held(_productRows(conn, sleeveId))
        stamp = _now()
        with conn:
            conn.execute('UPDATE sleeves SET deletedBy = ?, deletedAt = ? WHERE id = ?',
                         (user, stamp, sleeveId))
            revisionNumber = _recordHistory(conn, sleeveId, 'deleted', row['variant'],
                                            row['category'], row['name'], row['note'],
                                            cleaned, user, stamp, label=row['label'],
                                            rules=_ruleRows(conn, sleeveId))
        return {'id': sleeveId, 'variant': typesLabel(row['variant']),
                'variants': typesOf(row['variant']), 'category': row['category'],
                'name': row['name'], 'archivedAt': stamp, 'archivedBy': user,
                'revision': revisionNumber}
    finally:
        conn.close()


def setOffered(sleeveId, offered: bool, user='') -> dict:
    """Disable a sleeve, or enable it again (D169). A disabled sleeve stays
    in the library, editable, in the console and on the record, and is left
    out of everything a PWA picks from - listSleeves. Not a revision: what
    the sleeve holds has not changed, so its version, the proposals pinned
    to it and an editor open on it are all undisturbed. The switch is
    recorded in sleeveSwitches.

    Refused for an archived sleeve, which is offered to nobody already, and
    for a fixed category's, which an overlay attaches to every proposal: a
    type that is not to offer the overlay stops offering the sleeve, or it is
    archived (D191)."""
    conn = _connect()
    try:
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        if row is None:
            raise ValidationError('id', 'That sleeve no longer exists.')
        if row['deletedAt']:
            raise ValidationError('id', 'That sleeve is in the Archive. Restore it first.')
        if row['category'] in fixedCategories():
            raise ValidationError(
                'category', '{} is attached automatically by its overlay, so its sleeve cannot be '
                'disabled. To stop the overlay under a type, stop offering the sleeve there or '
                'archive it.'.format(row['category']))
        if offered == (not row['disabledAt']):
            raise ValidationError('offered', '{} is already {}.'.format(
                row['name'], 'offered to PWAs' if offered else 'disabled'))
        stamp = _now()
        with conn:
            conn.execute('UPDATE sleeves SET disabledBy = ?, disabledAt = ? WHERE id = ?',
                         ('' if offered else user, '' if offered else stamp, sleeveId))
            conn.execute(
                'INSERT INTO sleeveSwitches (sleeveId, action, variant, category, name, label, '
                'actor, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (sleeveId, 'enabled' if offered else 'disabled', row['variant'],
                 row['category'], row['name'], row['label'], user, stamp))
        row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
        return _entry(conn, row, _productRows(conn, sleeveId))
    finally:
        conn.close()


# ------------------------------------------------- where it is offered ---
# A sleeve's types (D179). Each call acts on the sleeve and on every other
# edition of its name offered under exactly the same types, so a sleeve and
# its editions move together - the way the console shows them, one name.
# None is a revision: what the editions hold has not changed, so pinned
# proposals and an editor open on one are undisturbed. Each is a switch on
# the record, beside disable and enable.

#: what a switch's action says, for the feed
TYPE_ACTIONS = ('shared', 'unshared', 'split')


def _typeGroup(conn, sleeveId):
    """The live sleeve and every edition of its name under the same types."""
    row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
    if row is None:
        raise ValidationError('id', 'That sleeve no longer exists.')
    if row['deletedAt']:
        raise ValidationError('id', 'That sleeve is in the Archive. Restore it first.')
    group = conn.execute(
        "SELECT * FROM sleeves WHERE category = ? AND name = ? AND variant = ? AND deletedAt = '' "
        'ORDER BY id', (row['category'], row['name'], row['variant'])).fetchall()
    return row, group


def _typeChange(sleeveId, variant, user, work):
    """Run *work(conn, row, group, types, variant, stamp)* in one write
    transaction, then return every sleeve it touched, as the console lists
    them."""
    if not variantExists(variant):
        raise ValidationError('variant', '{!r} is not an implementation type.'.format(variant))
    conn = _connect()
    try:
        conn.commit()                       # nothing open, so BEGIN is legal
        conn.execute('BEGIN IMMEDIATE')
        try:
            row, group = _typeGroup(conn, sleeveId)
            touched = work(conn, row, group, typesOf(row['variant']), variant, _now())
        except Exception:
            conn.rollback()
            raise
        conn.commit()
        return [_entry(conn, r, _productRows(conn, r['id']))
                for r in (conn.execute('SELECT * FROM sleeves WHERE id = ?', (i,)).fetchone()
                          for i in touched)]
    finally:
        conn.close()


def _switch(conn, row, action, variant, detail, user, stamp):
    conn.execute(
        'INSERT INTO sleeveSwitches (sleeveId, action, variant, category, name, label, '
        'actor, at, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (row['id'], action, variant, row['category'], row['name'], row['label'], user, stamp,
         detail))


def _revalidate(conn, row, variant):
    """The edition as it stands, checked as offered under *variant*."""
    productRows = _asRows(_productRows(conn, row['id']))
    return _validate(conn, variant, row['category'], row['name'], row['note'], productRows,
                     sleeveId=row['id'], label=row['label'], rules=_ruleRows(conn, row['id']))


def offerUnder(sleeveId, variant, user='') -> list:
    """Offer a sleeve under one more type: the same sleeve, shared (D179).
    Refused where that type already has a sleeve of the name of its own -
    the two would be one name twice - and wherever the sleeve would break a
    rule there, as a save would be."""
    def work(conn, row, group, types, variant, stamp):
        if variant in types:
            raise ValidationError('variant', '{} is already offered under {}.'.format(row['name'], variant))
        for other in conn.execute("SELECT * FROM sleeves WHERE category = ? AND deletedAt = ''",
                                  (row['category'],)).fetchall():
            if variant in typesOf(other['variant']) and _nameKey(other['name']) == _nameKey(row['name']):
                raise ValidationError(
                    'variant', '{} already has its own {} in {}. To share this one there instead, '
                    'archive that one first.'.format(variant, other['name'], row['category']))
        wider = typesText(types + [variant])
        for member in group:
            conn.execute('UPDATE sleeves SET variant = ? WHERE id = ?', (wider, member['id']))
        for member in group:
            _revalidate(conn, member, wider)
            _switch(conn, member, 'shared', wider, variant, user, stamp)
        return [m['id'] for m in group]
    return _typeChange(sleeveId, variant, user, work)


def withdrawFrom(sleeveId, variant, user='') -> list:
    """Stop offering a shared sleeve under one of its types (D179); the rest
    keep it. Its only type is not withdrawn - that is archiving it. A fixed
    category's sleeve may be: the type is left without one, and its overlay
    is not applied there (D191)."""
    def work(conn, row, group, types, variant, stamp):
        if variant not in types:
            raise ValidationError('variant', '{} is not offered under {}.'.format(row['name'], variant))
        if len(types) == 1:
            raise ValidationError('variant', '{} is offered under {} alone. Archive it to take it '
                                             'out of the library.'.format(row['name'], variant))
        narrower = typesText([t for t in types if t != variant])
        for member in group:
            conn.execute('UPDATE sleeves SET variant = ? WHERE id = ?', (narrower, member['id']))
            _switch(conn, member, 'unshared', member['variant'], variant, user, stamp)
        return [m['id'] for m in group]
    return _typeChange(sleeveId, variant, user, work)


def splitOff(sleeveId, variant, user='') -> list:
    """Give one of a shared sleeve's types a copy of its own (D179): a new
    sleeve under that type alone, holding what the shared one holds - its
    editions, products, rules, note and whether it is offered - with a
    history that starts here. The shared sleeve stays shared by the rest.
    From then on the two are separate: an edit to either changes it alone.
    Returns the shared editions, then the copies."""
    def work(conn, row, group, types, variant, stamp):
        if variant not in types:
            raise ValidationError('variant', '{} is not offered under {}.'.format(row['name'], variant))
        if len(types) == 1:
            raise ValidationError('variant', '{} is offered under {} alone: it is its own '
                                             'already.'.format(row['name'], variant))
        narrower = typesText([t for t in types if t != variant])
        for member in group:
            conn.execute('UPDATE sleeves SET variant = ? WHERE id = ?', (narrower, member['id']))
        made = []
        for member in group:
            name, note, cleaned, label, rules, stored = _revalidate(conn, member, variant)
            cur = conn.execute(
                'INSERT INTO sleeves (variant, category, name, label, note, createdBy, createdAt, '
                'updatedBy, updatedAt, firstCreatedAt, disabledBy, disabledAt) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (stored, member['category'], name, label, note, user, stamp, user, stamp, stamp,
                 _column(member, 'disabledBy') or '', _column(member, 'disabledAt') or ''))
            _writeProducts(conn, cur.lastrowid, cleaned)
            _writeRules(conn, cur.lastrowid, rules)
            _recordHistory(conn, cur.lastrowid, 'created', stored, member['category'], name,
                           note, cleaned, user, stamp, label=label, rules=rules)
            _switch(conn, member, 'split', member['variant'], variant, user, stamp)
            made.append(cur.lastrowid)
        return [m['id'] for m in group] + made
    return _typeChange(sleeveId, variant, user, work)


def switches(conn) -> list:
    """Every disable and enable, oldest first, for the activity feed (D169)."""
    return [dict(r) for r in conn.execute('SELECT * FROM sleeveSwitches ORDER BY id')]


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
        productRows = _asRows(_productRows(conn, sleeveId))
        name, note, cleaned, label, rules, _ = _validate(
            conn, row['variant'], row['category'], row['name'], row['note'], productRows,
            sleeveId=sleeveId, label=row['label'], rules=_ruleRows(conn, sleeveId))
        stamp = _now()
        with conn:
            conn.execute("UPDATE sleeves SET deletedBy = '', deletedAt = '', supersededBy = 0, "
                         'updatedBy = ?, updatedAt = ? WHERE id = ?', (user, stamp, sleeveId))
            _recordHistory(conn, sleeveId, 'restored', row['variant'], row['category'],
                           name, note, cleaned, user, stamp, label=label, rules=rules)
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
        checked, names, slots = [], set(), {}
        for sleeveId in wanted:
            row = conn.execute('SELECT * FROM sleeves WHERE id = ?', (sleeveId,)).fetchone()
            if row is None:
                raise ValidationError('ids', 'Sleeve {} no longer exists.'.format(sleeveId))
            if not row['deletedAt']:
                raise ValidationError('ids', '{} is already in the library.'.format(row['name']))
            productRows = _asRows(_productRows(conn, sleeveId))
            name, note, cleaned, label, rules, _ = _validate(
                conn, row['variant'], row['category'], row['name'], row['note'], productRows,
                sleeveId=sleeveId, label=row['label'], rules=_ruleRows(conn, sleeveId))
            # asked type by type: two shared sleeves collide where they share one
            for variant in typesOf(row['variant']):
                key = (variant, row['category'], name.lower(), label.lower())
                if key in names:
                    raise ValidationError('ids', 'Two of these are the {} edition of {} in {} under '
                                          '{}; only one can come back.'.format(
                                              label or 'fallback', name, row['category'], variant))
                names.add(key)
                if row['category'] in fixedCategories():
                    # one NAME per fixed category; two editions of it may come back together
                    slot = (variant, row['category'])
                    if slot in slots and slots[slot] != name.lower():
                        raise ValidationError('ids', '{} holds one sleeve under {}; two of these '
                                              'would fill it.'.format(row['category'], variant))
                    slots[slot] = name.lower()
            checked.append((row, name, note, cleaned, label, rules))
        stamp = _now()
        with conn:
            for row, name, note, cleaned, label, rules in checked:
                conn.execute("UPDATE sleeves SET deletedBy = '', deletedAt = '', supersededBy = 0, "
                             'updatedBy = ?, updatedAt = ? WHERE id = ?', (user, stamp, row['id']))
                _recordHistory(conn, row['id'], 'restored', row['variant'], row['category'],
                               name, note, cleaned, user, stamp, label=label, rules=rules)
        out = []
        for row, _, _, _, _, _ in checked:
            fresh = conn.execute('SELECT * FROM sleeves WHERE id = ?', (row['id'],)).fetchone()
            out.append(_entry(conn, fresh, _productRows(conn, row['id'])))
        return out
    finally:
        conn.close()


def revertSleeve(sleeveId, number: int, user='', base=None) -> dict:
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
    productRows = [{'productId': p['productId'], 'weight': p['weight'],
                    'alternative': p.get('alternative') or ''} for p in stored['products']]
    return updateSleeve(sleeveId, stored['name'], productRows, note=stored['note'],
                        user=user, action='reverted', label=stored['label'],
                        rules=stored['rules'], base=base)


# ----------------------------------------------------------- interchange ---

def exportRows() -> list:
    """The whole library in the interchange shape, one row per product, with
    the edition label (SEED_COLUMNS_EDITIONS) and the product held instead
    below the minimum, or '' (ALTERNATIVE_COLUMN, D192)."""
    conn = _connect()
    try:
        out = []
        for row in _sleeveRows(conn):
            for pr in _productRows(conn, row['id']):
                out.append((fileTypes(row['variant']), row['category'], row['name'], row['label'],
                            pr['productId'], pr['weight'], pr['alternative'] or ''))
        return out
    finally:
        conn.close()


def exportRuleRows() -> list:
    """Every live edition's rules in the interchange shape (RULE_COLUMNS and
    ENTITY_COLUMN, the entities by name), one row per rule; a fallback has
    none and appears nowhere here."""
    conn = _connect()
    try:
        out = []
        for row in _sleeveRows(conn):
            for rule in _ruleRows(conn, row['id']):
                out.append((fileTypes(row['variant']), row['category'], row['name'], row['label'],
                            sleeveRules.cell(rule.get('currency')),
                            sleeveRules.cell(rule.get('riskLevel')),
                            sleeveRules.cell(rule.get('allocationType')),
                            sleeveRules.cell([entities.name(e) for e in rule.get('entity') or []])))
        return out
    finally:
        conn.close()


def _importInto(conn, rows, replace, user, strict, action='imported', ruleRows=None):
    """Load interchange rows. Grouped by (variant, category, sleeve, edition);
    a group is one edition. With *strict* every group goes through the same
    validation as a save and the first failure aborts the whole load; the
    seed loads non-strict, so a stand-in whose products drifted still comes
    up - broken sleeves are what the census is for. The rules are always
    checked for shape and vocabulary, strict or not: a rule about a
    portfolio that does not exist is a mistake in the delivery, not a
    drifted product.

    The Variant cell names every type an edition is offered under (D179):
    one, several separated by TYPE_SEP, or ALL_TYPES. An edition in the file
    replaces the live one offered under exactly the same types; one that
    shares only some of them with a live edition of its name meets it as a
    name the type already has."""
    def stored(variant):
        # canonical, so 'All' and the four spelled out are the same edition;
        # a cell naming a type that does not exist is the validation's to
        # refuse, strict, and stays as written for the census, not
        try:
            return typesText(variant)
        except ValidationError:
            if strict:
                raise
            return str(variant or '').strip()

    grouped, order = {}, []
    for held in rows:
        variant, category, name, label, pid, weight = held[:6]
        alternative = held[6] if len(held) > 6 else ''
        key = (stored(variant), category, name, label)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append({'productId': pid, 'weight': weight, 'alternative': alternative or ''})
    rulesFor = {}
    for r in ruleRows or []:
        key = (stored(r['variant']), r['category'], r['sleeve'], r['edition'])
        if key not in grouped:
            raise ValidationError('rules', 'A rule names the {} edition of {} in {} under {}, '
                                  'which has no product rows.'.format(
                                      r['edition'] or 'fallback', r['sleeve'], r['category'],
                                      typesLabel(key[0])))
        rulesFor.setdefault(key, []).append({f: r[f] for f in sleeveRules.FIELDS if r.get(f)})
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
                               _held(_productRows(conn, row['id'])),
                               user, stamp, label=row['label'], rules=_ruleRows(conn, row['id']))
        for key in order:
            variant, category, name, label = key
            productRows = grouped[key]
            rules = rulesFor.get(key, [])
            existing = conn.execute(
                "SELECT id FROM sleeves WHERE variant = ? AND category = ? AND name = ? "
                "AND label = ? AND deletedAt = ''", (variant, category, name, label)).fetchone()
            if strict:
                name, _, cleaned, label, rules, _ = _validate(
                    conn, variant, category, name, '', productRows,
                    sleeveId=existing['id'] if existing else None, label=label, rules=rules)
            else:
                cleaned = [(p['productId'], float(p['weight']), p.get('alternative') or '')
                           for p in productRows]
                rules = sleeveRules.normalise(rules)
                if rules and not label:
                    raise ValidationError('rules', 'The fallback edition of {} in {} under {} '
                                          'carries rules; give it an Edition label.'.format(
                                              name, category, typesLabel(variant)))
            if existing:
                sleeveId = existing['id']
                conn.execute('UPDATE sleeves SET updatedBy = ?, updatedAt = ? WHERE id = ?',
                             (user, stamp, sleeveId))
            else:
                cur = conn.execute(
                    'INSERT INTO sleeves (variant, category, name, label, note, createdBy, '
                    'createdAt, updatedBy, updatedAt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (variant, category, name, label, '', user, stamp, user, stamp))
                sleeveId = cur.lastrowid
            _writeProducts(conn, sleeveId, cleaned)
            _writeRules(conn, sleeveId, rules)
            _recordHistory(conn, sleeveId, action, variant, category, name, '',
                           cleaned, user, stamp, label=label, rules=rules)
    return len(order)


def importRows(rows, replace=False, user='import', ruleRows=None) -> int:
    """Load interchange rows through full validation. Returns the number of
    editions written. Existing editions of the same name and label are
    overwritten; *replace* clears the library first."""
    conn = _connect()
    try:
        return _importInto(conn, list(rows), replace=replace, user=user, strict=True,
                           ruleRows=list(ruleRows or []))
    finally:
        conn.close()


def applicabilityPreview(variant, category, name, label, rules, sleeveId=None) -> dict:
    """What a rule set would mean before it is saved (D89): how many
    strategic portfolios it names, which, and which of them a sibling
    edition of the same name already claims. Raises the same ValidationError
    a save would, so the console shows the same words either way.

    *variant* is the type or types the edition is offered under (D179); an
    existing edition's own are used when *sleeveId* names one."""
    rules = sleeveRules.normalise(rules)
    keys = sleeveRules.applicability(rules)
    conn = _connect()
    try:
        own = conn.execute('SELECT variant FROM sleeves WHERE id = ?',
                           (sleeveId,)).fetchone() if sleeveId is not None else None
        types = typesOf(own['variant']) if own else [v for v in parseTypes(variant) if variantExists(v)]
        overlaps = []
        for sib in conn.execute(
                "SELECT id, label, variant FROM sleeves WHERE category = ? AND name = ? "
                "AND deletedAt = '' ORDER BY id", (category, name or '')).fetchall():
            if sib['id'] == sleeveId or not _sharing(sib, types):
                continue
            sibRules = _ruleRows(conn, sib['id'])
            if not sibRules:
                continue
            shared = sleeveRules.overlap(rules, sibRules)
            if shared:
                overlaps.append({'sleeveId': sib['id'], 'label': sib['label'],
                                 'count': len(shared), 'keys': shared})
        return {'rules': rules, 'applies': len(keys), 'appliesTo': keys,
                'universe': len(universe.keys()), 'overlaps': overlaps}
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
                                         'variants': e['variants'],
                                         'category': e['category'], 'name': e['name']})
    return [byProduct[k] for k in sorted(byProduct)]


def census() -> dict:
    """What the library holds and what is wrong with it."""
    entries = listAll()
    byVariant = {}
    for e in entries:
        # a shared sleeve is one of each of its types' sleeves (D179)
        for variant in e['variants']:
            counts = byVariant.setdefault(variant, {'sleeves': 0, 'broken': 0, 'disabled': 0,
                                                    'shared': 0})
            counts['sleeves'] += 1
            if e['shared']:
                counts['shared'] += 1
            if e['disabled']:
                counts['disabled'] += 1
            if e['problems']:
                counts['broken'] += 1
    from . import overlayRules          # lazily: it imports rules, which imports this module
    fixed, withheld = [], []
    for variant in VARIANTS:
        for category in fixedCategories():
            names = {e['name'] for e in entries
                     if variant in e['variants'] and e['category'] == category}
            if len(names) > 1:
                fixed.append('{} / {}: {} sleeves, expected one at most'.format(
                    variant, category, len(names)))
        # an overlay into a category the type has no sleeve in is not
        # applied under it (D191): not a problem, but the census says so
        for rule in overlayRules.current(variant).get('withheld') or []:
            withheld.append('{}: {} not applied - no sleeve in {}'.format(
                variant, rule['name'], rule['into']))
    return {
        'store': describe(),
        'catalogue': products.describeSource(),
        'byVariant': byVariant,
        'broken': [e for e in entries if e['problems']],
        'orphans': orphanProducts(),
        'fixedCategoryProblems': fixed,
        'overlaysWithheld': withheld,
        'total': len(entries),
        'editions': sum(1 for e in entries if not e['fallback']),
        'shared': sum(1 for e in entries if e['shared']),
        'archived': listArchived(),
    }
