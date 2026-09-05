"""Account opening requests (D76).

A request to open an account on the terms of a delivered proposal. The form
on the landing page fills itself from a Proposal UID (D75), the user adds what
the proposal cannot know - the client, the account, the funding - and Submit
records one row here, beside the proposal it names.

The table lives IN THE REGISTER'S DATABASE FILE (``SCENARIO_REGISTER_DB``): a
request references a proposal row by its UID and has no meaning without it,
so the two are one durable file to back up and one foreign key to keep them
honest. Like the register it is append-only by construction: no update, no
delete, and a test that says so.

One request per proposal. A second Submit for the same UID is refused and
names the request that already exists - a duplicate account opening is the
costlier mistake, and the rule is one line to relax if a proposal may ever
fund several accounts.

The option lists are PLACEHOLDERS until Operations confirms them (like the
fee card's placeholder rates, D55): they ride the schema so the page renders
whatever this module says and a later delivery can source them elsewhere
without touching the page.
"""

from __future__ import annotations

import datetime
import secrets
import sqlite3
import threading

from . import proposalRegister
from .types import PortfolioKey, ValidationError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accountRequests (
    requestId     TEXT PRIMARY KEY,
    proposalId    TEXT NOT NULL REFERENCES proposals(proposalId),
    submittedAt   TEXT NOT NULL,
    submittedBy   TEXT NOT NULL,
    clientName    TEXT NOT NULL,
    accountType   TEXT NOT NULL,
    bookingCentre TEXT NOT NULL,
    taxResidency  TEXT NOT NULL,
    fundingAmount REAL NOT NULL,
    fundingSource TEXT NOT NULL,
    fundingDate   TEXT NOT NULL,
    kycReference  TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ar_proposal ON accountRequests (proposalId, submittedAt);
CREATE INDEX IF NOT EXISTS ar_at ON accountRequests (submittedAt DESC);
"""

#: Placeholder option lists - to be confirmed with Operations (see module doc).
ACCOUNT_TYPES = ['Individual', 'Joint', 'Trust', 'Corporate', 'Foundation']
BOOKING_CENTRES = ['New York', 'London', 'Frankfurt', 'Zurich', 'Singapore', 'Hong Kong']
FUNDING_SOURCES = ['Wire', 'Transfer in kind', 'Internal transfer']

#: What the form must have before Submit is live; the server re-enforces.
REQUIRED = ('clientName', 'accountType', 'bookingCentre', 'taxResidency',
            'fundingAmount', 'fundingSource', 'fundingDate')
OPTIONAL = ('kycReference', 'notes')
_COLUMNS = ('requestId', 'proposalId', 'submittedAt', 'submittedBy') + REQUIRED + OPTIONAL

_MAX_TEXT = {'clientName': 200, 'taxResidency': 100, 'kycReference': 100, 'notes': 2000}

_lock = threading.Lock()


class ProposalNotFound(LookupError):
    """No proposal has that UID."""


def options() -> dict:
    return {'accountTypes': list(ACCOUNT_TYPES), 'bookingCentres': list(BOOKING_CENTRES),
            'fundingSources': list(FUNDING_SOURCES), 'required': list(REQUIRED),
            'placeholder': True}


def _connect() -> sqlite3.Connection:
    # the register's tables first: ours references one of them
    conn = proposalRegister._connect()
    with _lock:
        conn.executescript(_SCHEMA)
        conn.commit()
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def newRequestId() -> str:
    return 'ar_' + secrets.token_hex(6)


# ---------------------------------------------------------------- summary ---

def proposalSummary(entry: dict) -> dict:
    """The proposal as the form fills from it: the register row's parameters
    and the pinned sleeves, nothing a client-facing form does not show."""
    key = PortfolioKey.fromStr(entry['baseKey'])
    return {
        'proposalId': entry['proposalId'],
        'scenarioId': entry['scenarioId'],
        'sequence': entry['sequence'],
        'exportedAt': entry['exportedAt'],
        'exportedBy': entry['exportedBy'],
        'createdBy': entry.get('createdBy', ''),
        'primaryPwa': entry['primaryPwa'],
        'topAccountSize': entry['topAccountSize'],
        'mandateSize': entry['mandateSize'],
        'currency': entry['currency'],
        'hedging': entry['hedging'],
        'variant': entry['variant'],
        'riskLevel': key.riskLevel,
        'allocationType': key.allocationType,
        'excludeRealAssets': key.excludeRealAssets,
        'tacticalTilt': bool(entry['tacticalTilt']),
        'volPremium': bool(entry['volPremium']),
        'includeFees': bool(entry['includeFees']),
        'feeSchedule': entry.get('feeSchedule'),
        'feeLevel': entry.get('feeLevel'),
        'workbookName': entry.get('workbookName', ''),
        'sleeves': [{'category': s['category'], 'sleeve': s['sleeve'], 'revision': s['revision']}
                    for s in entry.get('sleeves', [])],
    }


def lookup(proposalId: str) -> dict:
    """What the form needs for a UID: the proposal's summary and any request
    already made against it. Raises ProposalNotFound."""
    entry = proposalRegister.getProposal(proposalId)
    if entry is None:
        raise ProposalNotFound(proposalId)
    return {'proposal': proposalSummary(entry), 'requests': forProposal(proposalId)}


# ------------------------------------------------------------- validation ---

def _text(payload, field, required, limit=None) -> str:
    value = payload.get(field)
    value = '' if value is None else str(value).strip()
    if required and not value:
        raise ValidationError(field, 'This field is required.')
    if limit and len(value) > limit:
        raise ValidationError(field, 'At most {} characters.'.format(limit))
    return value


def _choice(payload, field, choices, label) -> str:
    value = _text(payload, field, True)
    if value not in choices:
        raise ValidationError(field, 'Choose a {} from the list.'.format(label))
    return value


def validate(payload) -> dict:
    """The fields of one request, cleaned, or a ValidationError naming the
    first field that is wrong. The proposal's existence is checked at record
    time, against the register."""
    if not isinstance(payload, dict):
        raise ValidationError('payload', 'The request must be an object.')
    proposalId = str(payload.get('proposalId') or '').strip().lower()
    if not proposalRegister.isProposalId(proposalId):
        raise ValidationError('proposalId',
                              'A Proposal UID is pr_ followed by twelve letters or digits.')
    out = {'proposalId': proposalId}
    out['clientName'] = _text(payload, 'clientName', True, _MAX_TEXT['clientName'])
    out['accountType'] = _choice(payload, 'accountType', ACCOUNT_TYPES, 'account type')
    out['bookingCentre'] = _choice(payload, 'bookingCentre', BOOKING_CENTRES, 'booking centre')
    out['taxResidency'] = _text(payload, 'taxResidency', True, _MAX_TEXT['taxResidency'])
    amount = payload.get('fundingAmount')
    try:
        amount = float(amount) if amount not in (None, '') else 0.0
    except (TypeError, ValueError):
        raise ValidationError('fundingAmount', 'Enter the initial funding as an amount.')
    if not amount > 0 or amount != amount or amount == float('inf'):
        raise ValidationError('fundingAmount', 'Enter the initial funding as an amount above zero.')
    out['fundingAmount'] = amount
    out['fundingSource'] = _choice(payload, 'fundingSource', FUNDING_SOURCES, 'funding source')
    date = _text(payload, 'fundingDate', True)
    try:
        out['fundingDate'] = datetime.date.fromisoformat(date).isoformat()
    except ValueError:
        raise ValidationError('fundingDate', 'Enter the expected funding date.')
    out['kycReference'] = _text(payload, 'kycReference', False, _MAX_TEXT['kycReference'])
    out['notes'] = _text(payload, 'notes', False, _MAX_TEXT['notes'])
    return out


# ------------------------------------------------------------------ write ---

def record(user: str, payload) -> dict:
    """Record one request. One transaction, one row.

    Raises ValidationError for a field that is wrong or a proposal that
    already has a request, ProposalNotFound for a UID the register does not
    hold. The endpoint lets both propagate to the caller as 422 / 404."""
    fields = validate(payload)
    proposalId = fields['proposalId']
    conn = _connect()
    try:
        with conn:
            if proposalRegister.getProposal(proposalId, conn=conn) is None:
                raise ProposalNotFound(proposalId)
            existing = _forProposal(conn, proposalId)
            if existing:
                first = existing[0]
                raise ValidationError(
                    'proposalId',
                    'An account opening request already exists for this proposal: {} by {} '
                    'on {}.'.format(first['requestId'], first['submittedBy'],
                                    first['submittedAt'][:10]))
            requestId = newRequestId()
            conn.execute(
                'INSERT INTO accountRequests ({}) VALUES ({})'.format(
                    ', '.join(_COLUMNS), ', '.join('?' * len(_COLUMNS))),
                (requestId, proposalId, _now(), user or '') + tuple(fields[f] for f in REQUIRED + OPTIONAL))
        return get(requestId, conn=conn)
    finally:
        conn.close()


# ------------------------------------------------------------------- read ---

def _row(row) -> dict:
    return {key: row[key] for key in row.keys()}


def _forProposal(conn, proposalId) -> list:
    return [_row(r) for r in conn.execute(
        'SELECT {} FROM accountRequests WHERE proposalId = ? ORDER BY submittedAt, rowid'.format(
            ', '.join(_COLUMNS)), (proposalId,))]


def forProposal(proposalId: str) -> list:
    conn = _connect()
    try:
        return _forProposal(conn, proposalId)
    finally:
        conn.close()


def get(requestId: str, conn=None):
    own = conn is None
    conn = conn or _connect()
    try:
        row = conn.execute('SELECT {} FROM accountRequests WHERE requestId = ?'.format(
            ', '.join(_COLUMNS)), (requestId,)).fetchone()
        return _row(row) if row is not None else None
    finally:
        if own:
            conn.close()


def listRequests(limit: int = 200) -> list:
    """Newest first, for whoever comes to read them."""
    conn = _connect()
    try:
        return [_row(r) for r in conn.execute(
            'SELECT {} FROM accountRequests ORDER BY submittedAt DESC, rowid DESC LIMIT ?'.format(
                ', '.join(_COLUMNS)), (max(1, min(int(limit), 1000)),))]
    finally:
        conn.close()


def describe() -> dict:
    conn = _connect()
    try:
        row = conn.execute('SELECT COUNT(*) AS n, MIN(submittedAt) AS first, '
                           'MAX(submittedAt) AS last FROM accountRequests').fetchone()
        return {'path': proposalRegister.dbPath(), 'requests': row['n'],
                'earliest': row['first'] or '', 'latest': row['last'] or ''}
    finally:
        conn.close()
