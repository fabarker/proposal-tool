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
page. So does the custody platform that needs a custodian named.

The request also names the offering entity and the phasing timeline, and
states what the proposal implements - its allocation info - which is worked
out here from the register's record, shown fixed on the form and recorded as
shown, never taken from the page (D202). It names the investment
restrictions chosen from the desk's list, each as it read at Submit (D203);
the capital/income split it once asked Yes or No is one of them now, so a
request made since reads without that answer. A request recorded before has
none of what was asked since, and reads without it.

SMA DETAILS (D206). The request lists the mandate first, then each separate
account the implementation holds, each with its mandate code, benchmark,
management fee and funding - all worked out here, none taken from the page
but a fee the proposal never priced. The mandate's code is the mandate codes
table's for its portfolio (D205), its benchmark the one the proposal was
delivered with (D211); a separate account's are
the Catalogue's, its mandate for the proposal's currency and hedging and the
benchmark of that mandate, else the product's (D197, D204). The fees are
read from the workbook the proposal was delivered with, so the request quotes
what the client was given: a separate account's own rate where the schedule
prices by fee group, none where one rate is charged on the whole mandate,
and the mandate's rate weighted over the portfolio. A missing code or
benchmark stops the request; so does a fee left out where the proposal had
none.

The form once asked for the client, a booking centre, the funding and a KYC
reference. A request recorded then keeps those answers in the columns at the
end of the table, and is otherwise read as it was: its client name as the
mandate name and its tax residency as the tax domicile; what it was never
asked is left empty.
"""

from __future__ import annotations

import datetime
import json
import secrets
import sqlite3
import threading

from . import fees, mandateCodes, portfolioBenchmarks, products, proposalRegister, restrictions, rules
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
    notes              TEXT,
    -- asked since the form took the offering entity and the phasing; NULL on
    -- every request recorded before. A register already open is given them
    -- in place.
    offeringEntity     TEXT,
    phasingTimeline    TEXT,
    allocationInfo     TEXT,
    restrictions       TEXT,
    smaDetails         TEXT
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
#: the entity the mandate is offered by (D202)
OFFERING_ENTITIES = ['GSI', 'GSBE', 'GSCO', 'GSBZ']
#: how soon the mandate is to be fully invested (D202)
PHASING_TIMELINES = (['Immediate (No Phasing/Phasing Outside PMG)', '1 month']
                     + ['{} months'.format(n) for n in range(2, 13)])
#: what the form says beside the phasing timeline
PHASING_NOTE = ('Timeline for the mandate to be fully invested, as discussed with the client. '
                'PMG maintain full discretionary authority, including accelerating phasing. '
                'If sales teams prefer to direct phasing, please select Immediate, keep cash '
                'outside PMG and only submit inflow for the portion to be fully invested to '
                'the final implementation target.')
#: the sleeve category whose sleeve is the equity implementation
EQUITY_CATEGORY = 'Public Equity'
#: the platform that is someone else's custody, so the custodian is named
EXTERNAL_CUSTODY = 'External COD'
#: the answers the form gives before the user does: none, since the
#: capital/income split became a restriction (D203)
DEFAULTS = {}

#: What the form must have before Submit is live; the server re-enforces. The
#: custodian's name is required too, but only for external custody.
REQUIRED = ('mandateName', 'clientRelationship', 'accountType', 'mifidClass',
            'taxDomicile', 'ukRndClient', 'gsAffiliate',
            'offeringEntity', 'phasingTimeline', 'custodyPlatform')
FIELDS = REQUIRED + ('custodianName',)
BEFORE = ('bookingCentre', 'fundingAmount', 'fundingSource', 'fundingDate', 'kycReference', 'notes')
#: the columns added since (D202, D203, D206): NULL on a request recorded before them
SINCE = ('offeringEntity', 'phasingTimeline', 'allocationInfo', 'restrictions', 'smaDetails')
#: a management fee typed for a proposal that was not priced: basis points
FEE_BP_MAX = 500
#: asked once and no longer: empty on a request made since
RETIRED = ('capitalIncomeSplit',)
_WRITTEN = (('requestId', 'proposalId', 'submittedAt', 'submittedBy') + FIELDS
            + ('allocationInfo', 'restrictions', 'smaDetails'))
_COLUMNS = (('requestId', 'proposalId', 'submittedAt', 'submittedBy') + FIELDS + RETIRED
            + ('allocationInfo', 'restrictions', 'smaDetails') + BEFORE)

_MAX_TEXT = {'mandateName': 200, 'taxDomicile': 100, 'custodianName': 200}

_lock = threading.Lock()


class ProposalNotFound(LookupError):
    """No proposal has that UID."""


def options() -> dict:
    return {'clientRelationships': list(CLIENT_RELATIONSHIPS), 'accountTypes': list(ACCOUNT_TYPES),
            'mifidClasses': list(MIFID_CLASSES), 'yesNo': list(YES_NO),
            'custodyPlatforms': list(CUSTODY_PLATFORMS), 'externalCustody': EXTERNAL_CUSTODY,
            'offeringEntities': list(OFFERING_ENTITIES), 'phasingTimelines': list(PHASING_TIMELINES),
            'phasingNote': PHASING_NOTE, 'defaults': dict(DEFAULTS), 'required': list(REQUIRED)}


def _connect() -> sqlite3.Connection:
    # the register's tables first: ours references one of them
    conn = proposalRegister._connect()
    with _lock:
        held = {row[1] for row in conn.execute('PRAGMA table_info(accountRequests)')}
        if 'clientName' in held:
            conn.executescript(_REBUILD.format(schema=_SCHEMA))
        conn.executescript(_SCHEMA)
        held = {row[1] for row in conn.execute('PRAGMA table_info(accountRequests)')}
        for column in SINCE:
            if column not in held:
                conn.execute('ALTER TABLE accountRequests ADD COLUMN {} TEXT'.format(column))
        conn.commit()
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def newRequestId() -> str:
    return 'ar_' + secrets.token_hex(6)


# ---------------------------------------------------------------- summary ---

def allocationInfo(entry: dict) -> dict:
    """What the proposal implements, as the request states it (D202): the
    allocation, whether the tactical tilt is implemented, the equity sleeve,
    the hedging policy and the risk profile. Worked out from the register's
    record alone, so a proposal delivered before the request asked for it
    reads the same. A custom portfolio (D171) has no switch - the tilt is a
    product there - so it implements the tilt when it holds a product in the
    tilt's category; its equity is built by hand."""
    custom = entry.get('customPortfolio')
    if custom:
        risk, allocation = custom.get('profile'), 'Custom'
        tilt = any(rules.sleeveCategory(g.get('category')) == rules.TACTICAL_TILT_CATEGORY
                   and float(g.get('weightPct') or 0) > 0 for g in entry.get('implemented') or [])
        equity = 'Custom portfolio'
    else:
        key = PortfolioKey.fromStr(entry['baseKey'])
        risk = key.riskLevel
        allocation = key.allocationType + (' · ex Real Assets' if key.excludeRealAssets else '')
        tilt = bool(entry['tacticalTilt'])
        held = [s for s in entry.get('sleeves') or []
                if rules.sleeveCategory(s['category']) == EQUITY_CATEGORY]
        equity = ('None' if not held else 'Custom sleeve' if held[0].get('custom')
                  else held[0]['sleeve'] or 'None')
    return {'allocationType': allocation, 'tacticalTilt': tilt, 'equityImplementation': equity,
            'hedgingPolicy': entry['hedging'],
            'riskProfile': rules.RISK_LEVEL_LABELS.get(risk, risk or '')}


def _workbookFees(proposalId, items) -> list:
    """Each implemented line's management fee in basis points, as the
    workbook the proposal was delivered with printed it on its
    Implementation sheet, in the order of *items*; None for a line the sheet
    gives no fee, or where there is no workbook to read."""
    found = proposalRegister.workbook(proposalId)
    if not found:
        return [None] * len(items)
    import io
    from openpyxl import load_workbook
    try:
        book = load_workbook(io.BytesIO(found['bytes']), read_only=True, data_only=True)
        sheet = book['Implementation']
    except Exception:                          # noqa: BLE001 - an unreadable file prices nothing
        return [None] * len(items)
    at, rows = None, []
    for row in sheet.iter_rows(values_only=True):
        if at is None:
            if 'Products' in row and 'Allocation (%)' in row:
                at = {name: row.index(name) for name in ('Products', 'Mgmt fee') if name in row}
            continue
        name = row[at['Products']] if at['Products'] < len(row) else None
        if isinstance(name, str) and name.strip():
            fee = row[at['Mgmt fee']] if 'Mgmt fee' in at and at['Mgmt fee'] < len(row) else None
            rows.append((name.strip(), round(float(fee) * 10000, 2) if isinstance(fee, (int, float)) else None))
    if [r[0] for r in rows] == [i.get('name') for i in items]:
        return [r[1] for r in rows]
    # a sheet out of step with the record: each line by its name, in turn
    out, left = [], list(rows)
    for item in items:
        hit = next((r for r in left if r[0] == item.get('name')), None)
        if hit:
            left.remove(hit)
        out.append(hit[1] if hit else None)
    return out


def _amount(value):
    return None if value is None else float(value)


def _heldListing(product, item):
    """The listing of *product* a proposal's line held, as the Catalogue has
    it now (D207, D208): the one the register recorded, matched by its
    ticker, issue currency, hedging and share class - its code too where it
    had one - or, for a line delivered before listings, the one a proposal
    holds now, its first (D214). None for a product no longer in the
    Catalogue."""
    if product is None:
        return None
    if item.get('issueCurrency') is not None:
        found = products.findListing(product, item)
        if found is not None:
            return found
    listings = product.get('listings') or []
    return listings[0] if listings else None


def mandateBenchmark(entry: dict):
    """(benchmark, its mnemonic, where it came from) for the mandate's own
    row (D211): the one the proposal was delivered with; for a proposal
    delivered before there was one, a strategic portfolio's from the MAP
    benchmarks as they are now, a custom portfolio's the one chosen for it.
    Blank where there is none."""
    if entry.get('benchmark'):
        return entry['benchmark'], entry.get('benchmarkMnemonic') or '', 'proposal'
    custom = entry.get('customPortfolio')
    if custom:
        chosen = custom.get('benchmark') or ''
        return chosen, portfolioBenchmarks.mnemonicOf(chosen) if chosen else '', 'proposal' if chosen else ''
    found = portfolioBenchmarks.entryForPortfolio(entry.get('baseKey'), entry['hedging']) or {}
    return found.get('benchmark') or '', found.get('mnemonic') or '', 'table' if found else ''


def smaDetails(entry: dict) -> dict:
    """The mandate and each separate account the proposal implements, as the
    request states them (D206): {fees: 'byProduct' | 'oneRate' | 'none',
    rows: [...]} - each row its key, kind, name, category, code, benchmark
    and where it came from, management fee in basis points and where it
    came from, funding and initial funding, and the problem that stops the
    request, if any."""
    currency, hedging, variant = entry['currency'], entry['hedging'], entry['variant']
    schedule = entry.get('feeSchedule') if entry.get('includeFees') else None
    try:
        kind = 'none' if not schedule else 'byProduct' if fees.byGroup(schedule) else 'oneRate'
    except KeyError:
        kind = 'none'
    items = [(g, i) for g in entry.get('implemented') or [] for i in g.get('items') or []]
    perLine = _workbookFees(entry['proposalId'], [i for _, i in items]) if kind != 'none' else [None] * len(items)
    rows = []
    for (group, item), feeBp in zip(items, perLine):
        if not products.isSma(item):
            continue
        product = products.entry(item['productId'])
        listing = _heldListing(product, item)
        where = ' · '.join(w for w in ((listing or {}).get('issueCurrency'), (listing or {}).get('hedging'))
                           if w) or 'the listing it holds'
        problem = ''
        if product is None:
            problem = '{} is not in the Catalogue.'.format(item['name'])
        elif not (listing or {}).get('code'):
            problem = ('No mandate code for {}. An admin adds it to the listing on the product\'s card in '
                       'the Catalogue.'.format(where))
        benchmark = (listing or {}).get('benchmark') or ''
        benchmarkFrom = 'listing' if benchmark else ''
        if not problem and not benchmark:
            problem = ('No benchmark for {}. An admin adds one to the listing on the product\'s card in '
                       'the Catalogue.'.format(where))
        mandate = listing
        initial = _amount(item.get('initialNotional'))
        rows.append({
            'key': 'sma:{}|{}'.format(item['productId'], group['category']), 'kind': 'sma',
            'productId': item['productId'], 'name': item['name'], 'category': group['category'],
            'code': (mandate or {}).get('code') or '', 'benchmark': benchmark, 'benchmarkFrom': benchmarkFrom,
            'feeBp': feeBp if kind == 'byProduct' else None,
            'feeFrom': 'proposal' if kind == 'byProduct' else 'n/a' if kind == 'oneRate' else 'typed',
            'funding': _amount(item.get('notional')),
            'initialFunding': initial if initial is not None and abs(initial - (item.get('notional') or 0)) > 0.5 else None,
            'problem': problem})
    # the mandate's own: its portfolio's code, and its rate over the portfolio
    custom = entry.get('customPortfolio')
    cellKey = mandateCodes.keyOf(variant, keyStr=None if custom else entry['baseKey'],
                                 profile=(custom or {}).get('profile'), currency=currency, hedging=hedging)
    code = mandateCodes.codeFor(cellKey)
    benchmark, mnemonic, benchmarkFrom = mandateBenchmark(entry)
    problem = ''
    if code is None:
        problem = ('No mandate code for {}: no group in Account Opening, Mandate Codes holds it. An admin '
                   'adds it to a group there.'.format(mandateCodes.cellWords(cellKey)))
    elif not benchmark and custom:
        problem = ('No benchmark was chosen for this custom portfolio. Choose one on the proposal and '
                   'download it again.')
    elif not benchmark:
        problem = 'No benchmark for {}. An admin adds it in Account Opening, MAP Benchmarks.'.format(
            portfolioBenchmarks.cellWords(portfolioBenchmarks.keyOf(entry['baseKey'], hedging)))
    weighted = None
    if kind != 'none' and items and all(f is not None for f in perLine):
        weighted = round(sum(f * float(i.get('printedPct') or 0) / 100.0 for f, (_, i) in zip(perLine, items)), 2)
    mandateRow = {'key': 'mandate', 'kind': 'mandate', 'productId': '', 'name': '', 'category': '',
                  'code': code or '', 'benchmark': benchmark, 'benchmarkMnemonic': mnemonic,
                  'benchmarkFrom': benchmarkFrom,
                  'feeBp': weighted, 'feeFrom': 'proposal' if kind != 'none' else 'typed',
                  'funding': float(entry['mandateSize']),
                  'inSmas': sum(r['funding'] or 0.0 for r in rows), 'initialFunding': None,
                  'problem': problem}
    if kind != 'none' and weighted is None:
        mandateRow['problem'] = mandateRow['problem'] or 'The delivered workbook gives no management fee to read.'
    return {'fees': kind, 'rows': [mandateRow] + rows}


def proposalSummary(entry: dict) -> dict:
    """The proposal as the form fills from it: the register row's parameters
    and the pinned sleeves, nothing a client-facing form does not show. A
    custom portfolio (D171) has no strategic key: its risk level is its
    profile, and its allocation reads Custom."""
    custom = entry.get('customPortfolio')
    if custom:
        risk, allocation, excluded = custom.get('profile'), 'Custom', None
    else:
        key = PortfolioKey.fromStr(entry['baseKey'])
        risk, allocation, excluded = key.riskLevel, key.allocationType, key.excludeRealAssets
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
        'riskLevel': risk,
        'allocationType': allocation,
        'excludeRealAssets': excluded,
        'custom': bool(custom),
        'tacticalTilt': bool(entry['tacticalTilt']),
        'volPremium': bool(entry['volPremium']),
        'includeFees': bool(entry['includeFees']),
        'feeSchedule': entry.get('feeSchedule'),
        'feeLevel': entry.get('feeLevel'),
        'workbookName': entry.get('workbookName', ''),
        # a custom sleeve (D170) is marked so and pins no revision
        'sleeves': [{'category': s['category'], 'sleeve': s['sleeve'], 'revision': s['revision'],
                     'custom': bool(s.get('custom'))}
                    for s in entry.get('sleeves', [])],
        'allocationInfo': allocationInfo(entry),
        'smaDetails': smaDetails(entry),
    }


def lookup(proposalId: str) -> dict:
    """What the form needs for a UID: the proposal's summary and any request
    already made against it. Raises ProposalNotFound."""
    entry = proposalRegister.getProposal(proposalId)
    if entry is None:
        raise ProposalNotFound(proposalId)
    return {'proposal': proposalSummary(entry), 'requests': forProposal(proposalId),
            # the list on offer now, so a change the desk confirmed shows at
            # the next lookup (D203)
            'restrictions': restrictions.served()}


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
    out['taxDomicile'] = _text(payload, 'taxDomicile', True, _MAX_TEXT['taxDomicile'])
    out['ukRndClient'] = _choice(payload, 'ukRndClient', YES_NO, 'Yes or No')
    out['gsAffiliate'] = _choice(payload, 'gsAffiliate', YES_NO, 'Yes or No')
    out['offeringEntity'] = _choice(payload, 'offeringEntity', OFFERING_ENTITIES, 'an offering entity')
    out['phasingTimeline'] = _choice(payload, 'phasingTimeline', PHASING_TIMELINES,
                                     'a phasing timeline')
    out['custodyPlatform'] = _choice(payload, 'custodyPlatform', CUSTODY_PLATFORMS, 'a custody platform')
    # a custodian of the platform's own needs no name; external custody does
    external = out['custodyPlatform'] == EXTERNAL_CUSTODY
    out['custodianName'] = (_text(payload, 'custodianName', True, _MAX_TEXT['custodianName'])
                            if external else '')
    # any number of the restrictions on offer, or none, each as it reads now
    out['restrictions'] = restrictions.chosen(payload.get('restrictions'))
    out['smaFees'] = payload.get('smaFees') or {}
    if not isinstance(out['smaFees'], dict):
        raise ValidationError('smaFees', 'Send the management fees as a table, a row to its basis points.')
    return out


def _feeBp(text, name):
    """A management fee typed in basis points: 0 to FEE_BP_MAX, at most two
    decimal places."""
    said = str(text if text is not None else '').strip().replace(',', '')
    if said.lower().endswith('bp'):
        said = said[:-2].strip()
    if not said:
        raise ValidationError('smaFees', 'Give the management fee of {} in basis points.'.format(name))
    try:
        value = float(said)
    except ValueError:
        raise ValidationError('smaFees', 'The management fee of {}, {!r}, is not a number of basis '
                                         'points.'.format(name, said))
    if not 0 <= value <= FEE_BP_MAX or round(value, 2) != value:
        raise ValidationError('smaFees', 'The management fee of {} is 0 to {} basis points, at most two '
                                         'decimal places.'.format(name, FEE_BP_MAX))
    return value


def smaRecord(entry, fields) -> list:
    """The SMA Details as the request records them, worked out again now
    (D206): a row that stops the request is refused, naming it, and a fee
    the proposal never priced is taken from what was typed."""
    details = smaDetails(entry)
    out = []
    for row in details['rows']:
        name = fields['mandateName'] if row['kind'] == 'mandate' else row['name']
        if row['problem']:
            raise ValidationError('smaDetails', '{}: {}'.format(name, row['problem']))
        fee = row['feeBp']
        if details['fees'] == 'none':
            fee = _feeBp(fields['smaFees'].get(row['key']), name)
        kept = {k: row[k] for k in ('key', 'kind', 'productId', 'category', 'code', 'benchmark',
                                    'benchmarkFrom', 'feeFrom', 'funding', 'initialFunding')}
        # the mandate's benchmark mnemonic (D211)
        extra = {k: row[k] for k in ('inSmas', 'benchmarkMnemonic') if row.get(k) is not None and k in row}
        out.append(dict(kept, name=name, feeBp=fee, **extra))
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
            entry = proposalRegister.getProposal(proposalId, conn=conn)
            if entry is None:
                raise ProposalNotFound(proposalId)
            existing = _forProposal(conn, proposalId)
            if existing:
                first = existing[0]
                raise ValidationError(
                    'proposalId',
                    'An account opening request already exists for this proposal: {} by {} '
                    'on {}.'.format(first['requestId'], first['submittedBy'],
                                    first['submittedAt'][:10]))
            # the SMA Details worked out again, against the catalogue and the
            # mandate codes as they are now (D206)
            sma = smaRecord(entry, fields)
            requestId = newRequestId()
            conn.execute(
                'INSERT INTO accountRequests ({}) VALUES ({})'.format(
                    ', '.join(_WRITTEN), ', '.join('?' * len(_WRITTEN))),
                (requestId, proposalId, _now(), user or '') + tuple(fields[f] for f in FIELDS)
                + (json.dumps(allocationInfo(entry)), json.dumps(fields['restrictions']),
                   json.dumps(sma)))
        return get(requestId, conn=conn)
    finally:
        conn.close()


# ------------------------------------------------------------------- read ---

def _row(row) -> dict:
    """A request as it was made: the answers the form asked for then, and none
    of the empty columns of a form it never saw."""
    out = {key: row[key] for key in row.keys()
           if not ((key in BEFORE or key in SINCE) and row[key] is None)
           and not (key in RETIRED and not row[key])}
    for key in ('allocationInfo', 'restrictions', 'smaDetails'):
        if out.get(key):
            out[key] = json.loads(out[key])
    return out


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
