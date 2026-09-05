"""The proposal register - every delivered proposal, kept for ever (D69).

A scenario is a working session and expires in a day. A PROPOSAL is what a
session produced the moment someone pressed Download Excel, and it is kept
permanently in this store: who, for which PWA, at what mandate, the strategic
allocation that was proposed, the implemented model it became once sleeves
were attached, and the workbook itself, byte for byte.

The allocation is the PROPOSAL PORTFOLIO alone - the base column, the one the
implementation is built on. The columns it was compared against on screen are
analysis, not the proposal, and are not recorded.

Two pictures and one artefact. The pictures are JSON and are what the
register can be QUERIED on; the workbook is an opaque blob and is what it can
HAND BACK. Analytics and pricing have no columns here by decision - they are
inside the file, un-indexed, and that is the intended line.

Three properties the design holds to:

* Append-only. There is no update and no delete. A row is written once, in
  the same transaction as its file, and never touched again. If a proposal
  must ever be withdrawn that is a new row saying so, not a deletion.
* Self-contained. Product names, tickers and vehicles are written into the
  implemented picture at export time, and every sleeve is pinned to the
  revision it was at (D65). A snapshot resolved against next year's library
  is a re-run, not a record.
* Record first, deliver second. The export endpoint writes here BEFORE it
  returns the bytes, and a failed write is a failed export. A register with
  silent holes is worse than none, because it will be trusted.

The store is a SQLite file the tool owns, on the same pattern as the sleeve
repository: SCENARIO_REGISTER_DB names it, a schema version lives in meta,
and a migration runs on connect. Sizes: ~13 KB of pictures and ~17 KB of
workbook per proposal, so ten thousand of them is ~300 MB. Reads are
primary-key lookups and stay flat with size; the panel's list names its
columns so the blobs are never read for rows nobody asked to download.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading

from . import rules, sleeveRepo, sleeves
from .types import ValidationError
from .workbook import stampedProposalId

SCHEMA_VERSION = 1
LIST_LIMIT_MAX = 500

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '..', '..', '..', 'var', 'proposals.db')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    proposalId     TEXT PRIMARY KEY,
    scenarioId     TEXT NOT NULL,
    sequence       INTEGER NOT NULL,
    exportedAt     TEXT NOT NULL,
    exportedBy     TEXT NOT NULL,
    createdBy      TEXT NOT NULL DEFAULT '',
    primaryPwa     TEXT NOT NULL,
    topAccountSize REAL NOT NULL,
    mandateSize    REAL NOT NULL,
    currency       TEXT NOT NULL,
    hedging        TEXT NOT NULL,
    variant        TEXT NOT NULL,
    baseKey        TEXT NOT NULL,
    tacticalTilt   INTEGER NOT NULL,
    volPremium     INTEGER NOT NULL,
    includeFees    INTEGER NOT NULL,
    feeSchedule    TEXT,
    feeLevel       TEXT,
    allocation     TEXT NOT NULL,
    implemented    TEXT NOT NULL,
    workbook       BLOB NOT NULL,
    workbookName   TEXT NOT NULL,
    workbookSha    TEXT NOT NULL,
    workbookBytes  INTEGER NOT NULL,
    UNIQUE (scenarioId, sequence)
);
CREATE TABLE IF NOT EXISTS proposalSleeves (
    proposalId TEXT NOT NULL REFERENCES proposals(proposalId),
    category   TEXT NOT NULL,
    sleeveId   INTEGER,
    revision   INTEGER,
    sleeveName TEXT NOT NULL,
    PRIMARY KEY (proposalId, category)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS p_at  ON proposals (exportedAt DESC);
CREATE INDEX IF NOT EXISTS p_by  ON proposals (exportedBy, exportedAt DESC);
CREATE INDEX IF NOT EXISTS p_pwa ON proposals (primaryPwa, exportedAt DESC);
CREATE INDEX IF NOT EXISTS p_ccy ON proposals (currency, exportedAt DESC);
CREATE INDEX IF NOT EXISTS p_var ON proposals (variant, exportedAt DESC);
CREATE INDEX IF NOT EXISTS p_sc  ON proposals (scenarioId, sequence);
CREATE INDEX IF NOT EXISTS ps_sleeve ON proposalSleeves (sleeveId);
"""

#: the list view's columns - named, never SELECT *, so the blobs stay on disk
_LIST_COLUMNS = ('proposalId, scenarioId, sequence, exportedAt, exportedBy, createdBy, '
                 'primaryPwa, topAccountSize, mandateSize, currency, hedging, variant, '
                 'baseKey, tacticalTilt, volPremium, includeFees, '
                 'feeSchedule, feeLevel, workbookName, workbookBytes, workbookSha')

#: what the implemented picture keeps of a product: identity and description,
#: never a fee or a cost
_ITEM_FIELDS = ('productId', 'name', 'ticker', 'assetClass', 'style', 'vehicle',
                'source', 'liquidity', 'exposureCurrency', 'printedPct', 'notional')

_lock = threading.Lock()


def dbPath() -> str:
    return os.path.abspath(os.getenv('SCENARIO_REGISTER_DB', '') or _DEFAULT_DB)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def _connect() -> sqlite3.Connection:
    path = dbPath()
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    with _lock:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta VALUES ('schemaVersion', ?)",
                     (str(SCHEMA_VERSION),))
        conn.execute("INSERT OR IGNORE INTO meta VALUES ('openedAt', ?)", (_now(),))
        conn.commit()
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


# --------------------------------------------------------------- pictures ---

def allocationPicture(results) -> dict:
    """Picture one: the proposal portfolio's strategic allocation, with the
    analytics left out.

    Only the base column is kept. The results list arrives with the base
    first by construction and any comparisons after it; the comparisons were
    the analysis a PWA did on the way to this proposal, and the register is
    a record of the proposal, not of the analysis."""
    base = results[0] if results else {}
    return {
        'keyStr': base.get('keyStr'),
        'name': base.get('name'),
        'header': base.get('header'),
        'categories': [
            {'name': c['name'], 'weightPct': c['weightPct'],
             'assets': [{'reportingName': a['reportingName'], 'weightPct': a['weightPct']}
                        for a in c.get('assets', [])]}
            for c in base.get('categories', [])],
    }


def _pinSleeve(category: str, sleeveName, variant: str):
    """The (sleeveId, revision) a sleeve name resolves to right now - the
    version this proposal is being made against."""
    if not sleeveName:
        return None, None
    under = rules.sleeveCategory(category)
    for entry in sleeves.listSleeves(under, variant):
        if entry['name'] == sleeveName:
            full = sleeveRepo.getSleeve(entry['id'])
            return entry['id'], (full['revisions'] if full else None)
    return None, None


def implementedPicture(model: dict, variant: str) -> list:
    """Picture two: the implemented model, sleeves pinned to their revision,
    products carried by name, and no fee field anywhere."""
    out = []
    for group in model.get('groups', []):
        sleeveId, revision = _pinSleeve(group['category'], group.get('sleeve'), variant)
        out.append({
            'category': group['category'],
            'weightPct': group['weightPct'],
            'auto': bool(group.get('auto')),
            'sleeve': group.get('sleeve'),
            'sleeveId': sleeveId,
            'revision': revision,
            'items': [{field: item.get(field) for field in _ITEM_FIELDS}
                      for item in group.get('items', [])],
        })
    return out


# ------------------------------------------------------------ the UID ---

#: The shape of a Proposal UID: ``pr_`` and twelve hex digits. Lower case,
#: filename-safe, URL-safe, and the register's primary key.
PROPOSAL_ID = re.compile(r'^pr_[0-9a-f]{12}$')


def newProposalId() -> str:
    """Mint a Proposal UID.

    Minted BEFORE the workbook is written, not by the insert: the one id has
    to land in the file, in the file's name and in the row (D75), and only an
    id that exists before any of the three are made can be in all of them."""
    return 'pr_' + secrets.token_hex(6)


def isProposalId(value) -> bool:
    return isinstance(value, str) and bool(PROPOSAL_ID.match(value))


# ------------------------------------------------------------------ write ---

def record(proposalId: str, scenarioId: str, user: str, createdBy: str, basis, mandate,
           results, implementation: dict, model: dict, workbook: bytes, filename: str) -> dict:
    """Write one delivered proposal. One transaction, one row, one file.

    Raises on any failure, and the export endpoint lets that propagate: a
    proposal that could not be recorded is not delivered.

    *proposalId* is the UID the caller minted with ``newProposalId`` and wrote
    into the workbook and its name. There is only ever one UID for a finished
    proposal (D75), so the register checks rather than trusts: the workbook
    must be stamped with this id and the filename must carry it, or nothing is
    written. The column is the primary key, so an id can never take two rows."""
    if not workbook:
        raise ValidationError('workbook', 'Nothing to record: the export produced no file.')
    if not isProposalId(proposalId):
        raise ValidationError('proposalId', 'Not a Proposal UID: {!r}.'.format(proposalId))
    stamped = stampedProposalId(workbook)
    if stamped != proposalId:
        raise ValidationError(
            'proposalId', 'The workbook is stamped {} but would be recorded as {}: a proposal '
            'has one UID.'.format(stamped or 'with no UID', proposalId))
    if proposalId not in filename:
        raise ValidationError(
            'proposalId', 'The filename {!r} does not carry the Proposal UID {}.'.format(
                filename, proposalId))
    allocation = allocationPicture(results)
    implemented = implementedPicture(model, implementation.get('variant'))
    stamp = _now()
    sha = hashlib.sha256(workbook).hexdigest()
    conn = _connect()
    try:
        with conn:
            sequence = conn.execute(
                'SELECT COALESCE(MAX(sequence), 0) + 1 FROM proposals WHERE scenarioId = ?',
                (scenarioId,)).fetchone()[0]
            conn.execute(
                'INSERT INTO proposals (proposalId, scenarioId, sequence, exportedAt, exportedBy, '
                'createdBy, primaryPwa, topAccountSize, mandateSize, currency, hedging, variant, '
                'baseKey, tacticalTilt, volPremium, includeFees, feeSchedule, '
                'feeLevel, allocation, implemented, workbook, workbookName, workbookSha, '
                'workbookBytes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (proposalId, scenarioId, sequence, stamp, user or '', createdBy or '',
                 mandate.primaryPwa, float(mandate.topAccountSize), float(mandate.mandateSize),
                 basis.currency, basis.hedging, implementation.get('variant') or '',
                 allocation.get('keyStr') or '',
                 1 if implementation.get('tacticalTilt') else 0,
                 1 if implementation.get('volPremium') else 0,
                 1 if implementation.get('includeFees') else 0,
                 implementation.get('feeSchedule') if implementation.get('includeFees') else None,
                 implementation.get('feeLevel') if implementation.get('includeFees') else None,
                 json.dumps(allocation), json.dumps(implemented),
                 sqlite3.Binary(workbook), filename, sha, len(workbook)))
            conn.executemany(
                'INSERT INTO proposalSleeves (proposalId, category, sleeveId, revision, sleeveName) '
                'VALUES (?,?,?,?,?)',
                [(proposalId, g['category'], g['sleeveId'], g['revision'], g['sleeve'] or '')
                 for g in implemented if g.get('sleeve')])
        return getProposal(proposalId, conn=conn)
    finally:
        conn.close()


# ------------------------------------------------------------------- read ---

def _rowToEntry(row) -> dict:
    entry = {key: row[key] for key in row.keys() if key not in ('allocation', 'implemented')}
    for flag in ('tacticalTilt', 'volPremium', 'includeFees'):
        entry[flag] = bool(entry[flag])
    return entry


def _sleevesFor(conn, proposalId) -> list:
    """The pinned sleeves, each carrying where the library is NOW - which is
    the one thing the register computes rather than stores."""
    out = []
    for row in conn.execute('SELECT category, sleeveId, revision, sleeveName FROM proposalSleeves '
                            'WHERE proposalId = ? ORDER BY rowid', (proposalId,)):
        entry = {'category': row['category'], 'sleeveId': row['sleeveId'],
                 'revision': row['revision'], 'sleeve': row['sleeveName'],
                 'nowRevision': None, 'archived': None, 'moved': False}
        if row['sleeveId'] is not None:
            current = sleeveRepo.getSleeve(row['sleeveId'])
            if current is not None:
                entry['nowRevision'] = current['revisions']
                entry['archived'] = bool(current.get('archived'))
                entry['moved'] = (row['revision'] is not None
                                  and current['revisions'] != row['revision'])
        out.append(entry)
    return out


def getProposal(proposalId: str, conn=None):
    """One proposal with both pictures and its sleeve pins - and no blob."""
    own = conn is None
    conn = conn or _connect()
    try:
        row = conn.execute(
            'SELECT {}, allocation, implemented FROM proposals WHERE proposalId = ?'.format(
                _LIST_COLUMNS), (proposalId,)).fetchone()
        if row is None:
            return None
        entry = _rowToEntry(row)
        entry['allocation'] = json.loads(row['allocation'])
        entry['implemented'] = json.loads(row['implemented'])
        entry['sleeves'] = _sleevesFor(conn, proposalId)
        return entry
    finally:
        if own:
            conn.close()


def workbook(proposalId: str):
    """The delivered file, or None. Fetched only when someone asks for it."""
    conn = _connect()
    try:
        row = conn.execute('SELECT workbook, workbookName, workbookSha FROM proposals '
                           'WHERE proposalId = ?', (proposalId,)).fetchone()
        if row is None:
            return None
        return {'bytes': bytes(row['workbook']), 'name': row['workbookName'],
                'sha': row['workbookSha']}
    finally:
        conn.close()


def _where(exportedBy=None, primaryPwa=None, currency=None, variant=None,
           since=None, until=None, query='', exclude=None):
    clauses, args = [], []
    if exportedBy and exclude != 'exportedBy':
        clauses.append('p.exportedBy = ?'); args.append(exportedBy)
    if primaryPwa and exclude != 'primaryPwa':
        clauses.append('p.primaryPwa = ?'); args.append(primaryPwa)
    if currency and exclude != 'currency':
        clauses.append('p.currency = ?'); args.append(currency)
    if variant and exclude != 'variant':
        clauses.append('p.variant = ?'); args.append(variant)
    if since:
        clauses.append('p.exportedAt >= ?'); args.append(since)
    if until:
        clauses.append('p.exportedAt < ?'); args.append(until)
    if query:
        like = '%' + query + '%'
        clauses.append('(p.primaryPwa LIKE ? OR p.exportedBy LIKE ? OR p.createdBy LIKE ? '
                       'OR p.baseKey LIKE ? OR p.variant LIKE ? OR p.proposalId LIKE ? '
                       'OR EXISTS (SELECT 1 FROM proposalSleeves s WHERE s.proposalId = p.proposalId '
                       'AND s.sleeveName LIKE ?))')
        args.extend([like] * 7)
    return (' AND '.join(clauses) or '1 = 1'), args


def _cursorClause(before):
    if not before or '|' not in before:
        return '1 = 1', []
    at, _, ident = before.partition('|')
    return '(p.exportedAt < ? OR (p.exportedAt = ? AND p.proposalId < ?))', [at, at, ident]


def listProposals(exportedBy=None, primaryPwa=None, currency=None, variant=None,
                  since=None, until=None, query='', limit=100, before=None) -> dict:
    """A page of the register, newest first, with the counts that frame it.
    Facets count over the rows that pass every OTHER filter (D63)."""
    limit = max(1, min(int(limit or 100), LIST_LIMIT_MAX))
    query = (query or '').strip()
    where, args = _where(exportedBy, primaryPwa, currency, variant, since, until, query)
    cursorSql, cursorArgs = _cursorClause(before)
    conn = _connect()
    try:
        rows = conn.execute(
            'SELECT {} FROM proposals p WHERE {} AND {} ORDER BY p.exportedAt DESC, '
            'p.proposalId DESC LIMIT ?'.format(
                ', '.join('p.' + c.strip() for c in _LIST_COLUMNS.split(',')), where, cursorSql),
            args + cursorArgs + [limit + 1]).fetchall()
        entries = [_rowToEntry(r) for r in rows[:limit]]
        for entry in entries:
            entry['cursor'] = '{}|{}'.format(entry['exportedAt'], entry['proposalId'])
            entry['sleeves'] = [
                {'category': s['category'], 'sleeve': s['sleeveName'], 'revision': s['revision']}
                for s in conn.execute('SELECT category, sleeveName, revision FROM proposalSleeves '
                                      'WHERE proposalId = ? ORDER BY rowid', (entry['proposalId'],))]
        nextCursor = entries[-1]['cursor'] if len(rows) > limit and entries else None

        def count(dimension):
            w, a = _where(exportedBy, primaryPwa, currency, variant, since, until, query,
                          exclude=dimension)
            return {r['k']: r['n'] for r in conn.execute(
                'SELECT p.{} AS k, COUNT(*) AS n FROM proposals p WHERE {} '
                'GROUP BY k ORDER BY n DESC, k'.format(dimension, w), a)}

        total = conn.execute('SELECT COUNT(*) FROM proposals p WHERE {}'.format(where),
                             args).fetchone()[0]
        return {'entries': entries, 'next': nextCursor, 'total': total,
                'facets': {d: count(d) for d in ('exportedBy', 'primaryPwa', 'currency', 'variant')}}
    finally:
        conn.close()


def describe() -> dict:
    conn = _connect()
    try:
        meta = {r['key']: r['value'] for r in conn.execute('SELECT key, value FROM meta')}
        row = conn.execute('SELECT COUNT(*) AS n, COALESCE(SUM(workbookBytes), 0) AS b, '
                           'MIN(exportedAt) AS first, MAX(exportedAt) AS last FROM proposals').fetchone()
        return {'path': dbPath(), 'proposals': row['n'], 'workbookBytes': row['b'],
                'earliest': row['first'] or '', 'latest': row['last'] or '',
                'schemaVersion': int(meta.get('schemaVersion', SCHEMA_VERSION))}
    finally:
        conn.close()


# ----------------------------------------------------------------- export ---

EXPORT_COLUMNS = ['ProposalId', 'ScenarioId', 'Sequence', 'ExportedAt', 'ExportedBy', 'CreatedBy',
                  'PrimaryPwa', 'TopAccountSize', 'MandateSize', 'Currency', 'Hedging', 'Variant',
                  'BasePortfolio', 'TacticalTilt', 'VolPremium', 'IncludeFees',
                  'FeeSchedule', 'FeeLevel', 'Sleeves', 'WorkbookName', 'WorkbookBytes', 'WorkbookSha']


def exportRows(**filters) -> list:
    """The register as a table under the same filters, every page of it."""
    out, before = [], None
    while True:
        page = listProposals(limit=LIST_LIMIT_MAX, before=before, **filters)
        for e in page['entries']:
            pins = '; '.join('{}={}@r{}'.format(s['category'], s['sleeve'], s['revision'])
                             for s in e['sleeves'])
            out.append((e['proposalId'], e['scenarioId'], e['sequence'], e['exportedAt'],
                        e['exportedBy'], e['createdBy'], e['primaryPwa'], e['topAccountSize'],
                        e['mandateSize'], e['currency'], e['hedging'], e['variant'], e['baseKey'],
                        int(e['tacticalTilt']), int(e['volPremium']),
                        int(e['includeFees']), e['feeSchedule'] or '', e['feeLevel'] or '', pins,
                        e['workbookName'], e['workbookBytes'], e['workbookSha']))
        if not page['next'] or not page['entries']:
            return out
        before = page['next']
