"""Account opening requests (D76).

A request to open an account on the terms of a delivered proposal. The form
on the landing page fills itself from a Proposal UID (D75), the user adds what
the proposal cannot know - the mandate, the client, the account and its
custody - and Submit records one row here, beside the proposal it names.

The table lives IN THE REGISTER'S DATABASE FILE (``SCENARIO_REGISTER_DB``): a
request references a proposal row by its UID and has no meaning without it,
so the two are one durable file to back up and one foreign key to keep them
honest. Like the register it is append-only by construction: no update, no
delete, and a test that says so.

One request per proposal. A second Submit for the same UID is refused and
names the request that already exists - a duplicate account opening is the
costlier mistake, and the rule is one line to relax if a proposal may ever
fund several accounts.

The option lists ride the schema, so the page renders whatever this module
says and a later delivery can source them elsewhere without touching the
page. So do the one default (no capital/income split) and the custody platform
that needs a custodian named.

The form once asked for the client, a booking centre, the funding and a KYC
reference. A request recorded then keeps those answers in the columns at the
end of the table, and is otherwise read as it was: its client name as the
mandate name and its tax residency as the tax domicile; what it was never
asked is left empty.
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
    requestId          TEXT PRIMARY KEY,
    proposalId         TEXT NOT NULL REFERENCES proposals(proposalId),
    submittedAt        TEXT NOT NULL,
    submittedBy        TEXT NOT NULL,
    mandateName        TEXT NOT NULL,
    clientRelationship TEXT NOT NULL DEFAULT '',
    accountType        TEXT NOT NULL,
    mifidClass         TEXT NOT NULL DEFAULT '',
    capitalIncomeSplit TEXT NOT NULL DEFAULT '',
    taxDomicile        TEXT NOT NULL,
    ukRndClient        TEXT NOT NULL DEFAULT '',
    gsAffiliate        TEXT NOT NULL DEFAULT '',
    custodyPlatform    TEXT NOT NULL DEFAULT '',
    custodianName      TEXT NOT NULL DEFAULT '',
    -- asked by the form before it took the fields above; NULL on every
    -- request recorded since
    bookingCentre      TEXT,
    fundingAmount      REAL,
    fundingSource      TEXT,
    fundingDate        TEXT,
    kycReference       TEXT,
    notes              TEXT
);
CREATE INDEX IF NOT EXISTS ar_proposal ON accountRequests (proposalId, submittedAt);
CREATE INDEX IF NOT EXISTS ar_at ON accountRequests (submittedAt DESC);
"""

# A table from before the current fields is rebuilt into the one above in one
# transaction, every request kept and in the same order.
_REBUILD = """
BEGIN;
ALTER TABLE accountRequests RENAME TO accountRequestsBefore;
DROP INDEX IF EXISTS ar_proposal;
DROP INDEX IF EXISTS ar_at;
{schema}
INSERT INTO accountRequests (requestId, proposalId, submittedAt, submittedBy, mandateName,
                             accountType, taxDomicile, bookingCentre, fundingAmount,
                             fundingSource, fundingDate, kycReference, notes)
    SELECT requestId, proposalId, submittedAt, submittedBy, clientName, accountType,
           taxResidency, bookingCentre, fundingAmount, fundingSource, fundingDate,
           kycReference, notes
    FROM accountRequestsBefore ORDER BY rowid;
DROP TABLE accountRequestsBefore;
COMMIT;
"""

CLIENT_RELATIONSHIPS = ['New', 'Existing']
ACCOUNT_TYPES = ['Individual/Joint', 'Trust', 'PIV', 'Insurance Wrapper', 'Private Pension',
                 'Charity/Foundation/Endowment', 'Fund', 'Corporation']
MIFID_CLASSES = ['Retail', 'Professional']
YES_NO = ['Yes', 'No']
CUSTODY_PLATFORMS = ['GSI', 'GSLU', 'GSCO', 'GSBZ', 'External COD']
#: the platform that is someone else's custody, so the custodian is named
EXTERNAL_CUSTODY = 'External COD'
#: the only field with an answer before the user gives one
DEFAULTS = {'capitalIncomeSplit': 'No'}

#: What the form must have before Submit is live; the server re-enforces. The
#: custodian's name is required too, but only for external custody.
REQUIRED = ('mandateName', 'clientRelationship', 'accountType', 'mifidClass',
            'capitalIncomeSplit', 'taxDomicile', 'ukRndClient', 'gsAffiliate',
            'custodyPlatform')
FIELDS = REQUIRED + ('custodianName',)
BEFORE = ('bookingCentre', 'fundingAmount', 'fundingSource', 'fundingDate', 'kycReference', 'notes')
_COLUMNS = ('requestId', 'proposalId', 'submittedAt', 'submittedBy') + FIELDS + BEFORE

_MAX_TEXT = {'mandateName': 200, 'taxDomicile': 100, 'custodianName': 200}

_lock = threading.Lock()


class ProposalNotFound(LookupError):
    """No proposal has that UID."""


def options() -> dict:
    return {'clientRelationships': list(CLIENT_RELATIONSHIPS), 'accountTypes': list(ACCOUNT_TYPES),
            'mifidClasses': list(MIFID_CLASSES), 'yesNo': list(YES_NO),
            'custodyPlatforms': list(CUSTODY_PLATFORMS), 'externalCustody': EXTERNAL_CUSTODY,
            'defaults': dict(DEFAULTS), 'required': list(REQUIRED)}


def _connect() -> sqlite3.Connection:
    # the register's tables first: ours references one of them
    conn = proposalRegister._connect()
    with _lock:
        held = {row[1] for row in conn.execute('PRAGMA table_info(accountRequests)')}
        if 'clientName' in held:
            conn.executescript(_REBUILD.format(schema=_SCHEMA))
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
        raise ValidationError(field, 'Choose {} from the list.'.format(label))
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
    out['mandateName'] = _text(payload, 'mandateName', True, _MAX_TEXT['mandateName'])
    out['clientRelationship'] = _choice(payload, 'clientRelationship', CLIENT_RELATIONSHIPS,
                                        'the client relationship')
    out['accountType'] = _choice(payload, 'accountType', ACCOUNT_TYPES, 'an account type')
    out['mifidClass'] = _choice(payload, 'mifidClass', MIFID_CLASSES, 'the MiFID class')
    out['capitalIncomeSplit'] = _choice(payload, 'capitalIncomeSplit', YES_NO, 'Yes or No')
    out['taxDomicile'] = _text(payload, 'taxDomicile', True, _MAX_TEXT['taxDomicile'])
    out['ukRndClient'] = _choice(payload, 'ukRndClient', YES_NO, 'Yes or No')
    out['gsAffiliate'] = _choice(payload, 'gsAffiliate', YES_NO, 'Yes or No')
    out['custodyPlatform'] = _choice(payload, 'custodyPlatform', CUSTODY_PLATFORMS, 'a custody platform')
    # a custodian of the platform's own needs no name; external custody does
    external = out['custodyPlatform'] == EXTERNAL_CUSTODY
    out['custodianName'] = (_text(payload, 'custodianName', True, _MAX_TEXT['custodianName'])
                            if external else '')
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
                (requestId, proposalId, _now(), user or '') + tuple(fields[f] for f in FIELDS)
                + (None,) * len(BEFORE))
        return get(requestId, conn=conn)
    finally:
        conn.close()


# ------------------------------------------------------------------- read ---

def _row(row) -> dict:
    """A request as it was made: the answers the form asked for then, and none
    of the empty columns of a form it never saw."""
    return {key: row[key] for key in row.keys() if not (key in BEFORE and row[key] is None)}


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
