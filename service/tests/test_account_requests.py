"""Account opening requests (D76).

The landing card's third button opens a form that fills itself from a Proposal
UID (D75) and records one request against that proposal, in the register's own
database file. These tests drive the two endpoints and the store the way the
form does. The helpers come from the register's tests: ``_deliver`` does what
Download Excel does, ``_caller`` is the identity a router dependency hands in.
"""

import json
import re
import sqlite3

import pytest
from starlette.requests import Request

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import accountRequests, proposalRegister, restrictions
from cyrus_pmg.pmgService.scenario.registry import getScenarioPort
from cyrus_pmg.pmgService.scenario.types import BasisInput, PortfolioKey
from test_proposal_register import _caller, _deliver


@pytest.fixture(scope='module', autouse=True)
def _noGaps(tmp_path_factory):
    """A catalogue, a mandate codes table and a benchmarks table with nothing
    missing, so a request is refused only where a test takes something away
    (D206): every portfolio a code (D205) and a benchmark (D211), and every
    listing of every SMA a code and a benchmark of its own (D207)."""
    import base64
    import csv
    import os
    from cyrus_pmg.pmgService.scenario import mandateCodes, portfolioBenchmarks, products, sleeveRepo
    previous = os.environ.get('SCENARIO_SLEEVES_DB')
    os.environ['SCENARIO_SLEEVES_DB'] = str(tmp_path_factory.mktemp('codes') / 'sleeves.db')
    products.reload()
    sleeveRepo.describe()
    path = tmp_path_factory.mktemp('codesfile') / 'codes.csv'
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        csv.writer(fh).writerows([list(mandateCodes.COLUMNS), ['Every portfolio', 'PMG-TEST', 'All', 'All', 'All', 'All', 'All']])
    mandateCodes.importFile('codes.csv', base64.b64encode(path.read_bytes()).decode('ascii'), user='seed')
    portfolioBenchmarks.save({'rows': [
        {'benchmark': 'Test benchmark {} {}'.format(c.split('|')[1], c.split('|')[0]), 'mnemonic': 'TB-' + c.split('|')[1],
         'risk': c.split('|')[0], 'currency': c.split('|')[1], 'hedging': c.split('|')[2]}
        for c in portfolioBenchmarks.cells()]}, user='seed')
    for p in [p for p in products.entries() if products.isSma(p)]:
        fields = {k: p[k] for k in products.PRODUCT_FIELDS}
        fields['listings'] = [dict(l, code='{}-USD'.format(p['productId'][:20]), benchmark='Bench of ' + p['name'])
                              for l in p['listings']]
        products.update(p['productId'], fields, user='seed', base=p['revision'])
    yield
    if previous is None:
        os.environ.pop('SCENARIO_SLEEVES_DB', None)
    else:
        os.environ['SCENARIO_SLEEVES_DB'] = previous
    products.reload()


def _request(kerberos=None):
    headers = [(b'x-kerberos', kerberos.encode())] if kerberos else []
    return Request({'type': 'http', 'method': 'GET', 'path': '/', 'query_string': b'',
                    'headers': headers})


def _payload(uid, **over):
    base = {'proposalId': uid, 'mandateName': 'Herrera Family Trust', 'clientRelationship': 'New',
            'accountType': 'Trust', 'mifidClass': 'Professional',
            'taxDomicile': 'Spain', 'ukRndClient': 'No', 'gsAffiliate': 'No',
            'offeringEntity': 'GSBE', 'phasingTimeline': '3 months', 'custodyPlatform': 'GSI',
            'restrictions': ['r5', 'r2']}
    base.update(over)
    return base


def _body(response):
    return json.loads(response.body.decode('utf-8'))


# ----------------------------------------------------------------- lookup ---

def test_the_form_fills_from_the_uid():
    entry, _, _ = _deliver()
    uid = entry['proposalId']
    found = dashboardRouter.lookupProposal(uid)
    p = found['proposal']
    assert p['proposalId'] == uid
    assert p['primaryPwa'] == 'A. Castellanos — Madrid'
    assert p['mandateSize'] == 50e6 and p['topAccountSize'] == 50e6
    assert (p['currency'], p['hedging']) == ('USD', 'Hedged')
    assert p['variant'] == 'PMG Multi-Asset Portfolio'
    assert (p['riskLevel'], p['allocationType'], p['excludeRealAssets']) == ('Moderate', 'Full', False)
    assert p['includeFees'] is True and p['feeSchedule'] and p['feeLevel']
    assert p['tacticalTilt'] is True and p['volPremium'] is True
    assert p['exportedBy'] == 'alice' and p['workbookName'].endswith(uid + '.xlsx')
    assert p['sleeves'] and all(set(s) == {'category', 'sleeve', 'revision', 'custom'} for s in p['sleeves'])
    assert not any(s['custom'] for s in p['sleeves']), 'library sleeves only (D170)'
    assert 'allocation' not in p and 'implemented' not in p, 'parameters, not the pictures'
    # what the proposal implements, worked out by the server and fixed on the card (D202)
    equity = [s['sleeve'] for s in p['sleeves'] if s['category'] == 'Public Equity']
    assert len(equity) == 1 and equity[0]
    assert p['allocationInfo'] == {'allocationType': 'Full', 'tacticalTilt': True,
                                   'equityImplementation': equity[0], 'hedgingPolicy': 'Hedged',
                                   'riskProfile': 'Moderate'}
    # the restrictions on offer ride the lookup, so a change the desk confirmed shows (D203)
    assert [r['name'] for r in found['restrictions']] == restrictions.SEED
    assert found['restrictions'] == restrictions.served()
    assert found['requests'] == []


def test_the_lookup_forgives_case_and_whitespace():
    """A UID copied out of a filename or a cell arrives however it arrives."""
    entry, _, _ = _deliver()
    uid = entry['proposalId']
    assert dashboardRouter.lookupProposal('  ' + uid.upper() + ' ')['proposal']['proposalId'] == uid


def test_an_unknown_uid_is_404_and_a_non_uid_is_422_both_on_the_field():
    missing = dashboardRouter.lookupProposal('pr_000000000000')
    assert missing.status_code == 404
    assert _body(missing)['field'] == 'proposalId' and 'B1' in _body(missing)['error']
    malformed = dashboardRouter.lookupProposal('PR-123')
    assert malformed.status_code == 422 and _body(malformed)['field'] == 'proposalId'


# ----------------------------------------------------------------- submit ---

def test_a_request_is_recorded_against_the_proposal():
    entry, _, _ = _deliver()
    uid = entry['proposalId']
    made = dashboardRouter.createAccountRequest(_payload(uid), caller=_caller('alice'))
    r = made['request']
    assert re.fullmatch(r'ar_[0-9a-f]{12}', r['requestId'])
    assert r['proposalId'] == uid and r['submittedBy'] == 'alice'
    assert r['mandateName'] == 'Herrera Family Trust' and r['clientRelationship'] == 'New'
    assert r['accountType'] == 'Trust' and r['mifidClass'] == 'Professional'
    assert r['taxDomicile'] == 'Spain'
    assert r['ukRndClient'] == 'No' and r['gsAffiliate'] == 'No'
    assert r['custodyPlatform'] == 'GSI' and r['custodianName'] == ''
    assert (r['offeringEntity'], r['phasingTimeline']) == ('GSBE', '3 months')
    # the restrictions chosen, in the list's order and as they read at Submit (D203)
    assert r['restrictions'] == [{'id': 'r2', 'name': 'No UK Situs Asset', 'description': ''},
                                 {'id': 'r5', 'name': 'No UK Reporting Funds', 'description': ''}]
    assert 'capitalIncomeSplit' not in r, 'asked as a restriction now, not Yes or No'
    assert r['allocationInfo'] == dashboardRouter.lookupProposal(uid)['proposal']['allocationInfo'], \
        'the allocation info as the card showed it, worked out again by the server (D202)'
    assert not set(r) & set(accountRequests.BEFORE), 'nothing of the form it never saw'
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', r['submittedAt'])
    assert accountRequests.get(r['requestId']) == r
    assert dashboardRouter.getAccountRequest(r['requestId'])['request'] == r
    assert dashboardRouter.lookupProposal(uid)['requests'] == [r], 'the form learns of it next time'
    assert r in accountRequests.listRequests()


def test_one_request_per_proposal():
    """A duplicate account opening is the costlier mistake: the second Submit
    is refused and names the first."""
    entry, _, _ = _deliver()
    uid = entry['proposalId']
    first = dashboardRouter.createAccountRequest(_payload(uid), caller=_caller('alice'))['request']
    again = dashboardRouter.createAccountRequest(_payload(uid, mandateName='Someone Else'),
                                                 caller=_caller('bob'))
    assert again.status_code == 422
    body = _body(again)
    assert body['field'] == 'proposalId'
    assert first['requestId'] in body['error'] and 'alice' in body['error']
    assert len(accountRequests.forProposal(uid)) == 1


def test_a_request_for_a_uid_the_register_does_not_hold_is_404():
    response = dashboardRouter.createAccountRequest(_payload('pr_000000000000'),
                                                    caller=_caller('alice'))
    assert response.status_code == 404 and _body(response)['field'] == 'proposalId'


@pytest.mark.parametrize('field, value', [
    ('mandateName', ''), ('mandateName', 'x' * 201), ('clientRelationship', 'Former'),
    ('accountType', 'Partnership'), ('accountType', 'Individual'), ('mifidClass', ''),
    ('mifidClass', 'Elective Professional'), ('restrictions', ['r99']), ('restrictions', 'r1'),
    ('restrictions', ['r1', 'r1']),
    ('taxDomicile', '   '), ('ukRndClient', None), ('gsAffiliate', 'Y'),
    ('custodyPlatform', 'Elsewhere'), ('proposalId', 'sc_0123456789ab'),
    ('offeringEntity', ''), ('offeringEntity', 'GSLU'), ('phasingTimeline', None),
    ('phasingTimeline', '13 months'), ('phasingTimeline', 'Immediate'),
])
def test_each_wrong_field_is_named_and_nothing_is_written(field, value):
    entry, _, _ = _deliver()
    response = dashboardRouter.createAccountRequest(
        _payload(entry['proposalId'], **{field: value}), caller=_caller('alice'))
    assert response.status_code == 422, field
    assert _body(response)['field'] == field
    assert accountRequests.forProposal(entry['proposalId']) == []


def test_external_custody_names_its_custodian_and_no_other_platform_does():
    entry, _, _ = _deliver()
    uid = entry['proposalId']
    for name in (None, '  '):
        refused = dashboardRouter.createAccountRequest(
            _payload(uid, custodyPlatform='External COD', custodianName=name), caller=_caller('alice'))
        assert refused.status_code == 422 and _body(refused)['field'] == 'custodianName'
    r = dashboardRouter.createAccountRequest(
        _payload(uid, custodyPlatform='External COD', custodianName=' Pictet '),
        caller=_caller('alice'))['request']
    assert (r['custodyPlatform'], r['custodianName']) == ('External COD', 'Pictet')
    # a name sent with a platform of our own is not recorded
    other, _, _ = _deliver()
    r = dashboardRouter.createAccountRequest(
        _payload(other['proposalId'], custodyPlatform='GSCO', custodianName='Pictet'),
        caller=_caller('alice'))['request']
    assert r['custodianName'] == ''


def test_text_is_trimmed():
    entry, _, _ = _deliver()
    r = dashboardRouter.createAccountRequest(
        _payload(entry['proposalId'], mandateName='  Herrera Family Trust  ', taxDomicile=' Spain '),
        caller=_caller('alice'))['request']
    assert r['mandateName'] == 'Herrera Family Trust' and r['taxDomicile'] == 'Spain'


# ------------------------------------------------------------------ store ---

def test_the_options_ride_the_schema():
    schema = dashboardRouter.getScenarioSchema(_request('alice'), caller=_caller('alice'))
    o = schema['options']['accountRequest']
    assert o['clientRelationships'] == ['New', 'Existing']
    assert o['accountTypes'] == ['Individual/Joint', 'Trust', 'PIV', 'Insurance Wrapper',
                                 'Private Pension', 'Charity/Foundation/Endowment', 'Fund',
                                 'Corporation']
    assert o['mifidClasses'] == ['Retail', 'Professional']
    assert o['yesNo'] == ['Yes', 'No']
    assert o['custodyPlatforms'] == ['GSI', 'GSLU', 'GSCO', 'GSBZ', 'External COD']
    assert o['externalCustody'] == 'External COD'
    assert o['offeringEntities'] == ['GSI', 'GSBE', 'GSCO', 'GSBZ']
    assert o['phasingTimelines'] == (['Immediate (No Phasing/Phasing Outside PMG)', '1 month']
                                     + ['%d months' % n for n in range(2, 13)])
    assert o['phasingNote'].startswith('Timeline for the mandate to be fully invested')
    assert o['phasingNote'].endswith('to the final implementation target.')
    assert o['defaults'] == {}, 'no answer before the user gives one, now the split is a restriction'
    assert 'capitalIncomeSplit' not in o['required']
    assert set(o['required']) == set(accountRequests.REQUIRED)
    assert 'placeholder' not in o
    # stamped on a copy: the port's own schema is not mutated for the next caller
    own = getScenarioPort().get_schema(BasisInput(currency='USD', hedging='Hedged'), None, None)
    assert 'accountRequest' not in (own.get('options') or {})


def test_requests_live_in_the_registers_file_and_reference_its_rows():
    entry, _, _ = _deliver()
    dashboardRouter.createAccountRequest(_payload(entry['proposalId']), caller=_caller('alice'))
    assert accountRequests.describe()['path'] == proposalRegister.dbPath()
    conn = sqlite3.connect(proposalRegister.dbPath())
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'proposals', 'proposalSleeves', 'accountRequests'} <= tables
        fk = conn.execute('PRAGMA foreign_key_list(accountRequests)').fetchall()
        assert [(row[2], row[3], row[4]) for row in fk] == [('proposals', 'proposalId', 'proposalId')]
    finally:
        conn.close()


def test_a_table_from_before_the_current_fields_keeps_every_request(tmp_path, monkeypatch):
    """The form once asked for the client, a booking centre, the funding and a
    KYC reference. Opening a register whose table has that shape rebuilds it:
    every request stays, in order, read as it was made, and still counts as
    the proposal's one request."""
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'before.db'))
    entry, _, _ = _deliver()
    uid = entry['proposalId']
    conn = sqlite3.connect(str(tmp_path / 'before.db'))
    conn.executescript("""
        CREATE TABLE accountRequests (
            requestId TEXT PRIMARY KEY, proposalId TEXT NOT NULL REFERENCES proposals(proposalId),
            submittedAt TEXT NOT NULL, submittedBy TEXT NOT NULL, clientName TEXT NOT NULL,
            accountType TEXT NOT NULL, bookingCentre TEXT NOT NULL, taxResidency TEXT NOT NULL,
            fundingAmount REAL NOT NULL, fundingSource TEXT NOT NULL, fundingDate TEXT NOT NULL,
            kycReference TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '');
        CREATE INDEX ar_proposal ON accountRequests (proposalId, submittedAt);
        CREATE INDEX ar_at ON accountRequests (submittedAt DESC);""")
    conn.execute('INSERT INTO accountRequests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                 ('ar_0123456789ab', uid, '2026-09-18T14:05:00', 'jmercer', 'Ashworth', 'Trust',
                  'London', 'Spain', 25e6, 'Wire', '2026-09-30', 'KYC-1', 'Two tranches.'))
    conn.commit()
    conn.close()

    [r] = accountRequests.forProposal(uid)
    assert r['requestId'] == 'ar_0123456789ab' and r['submittedBy'] == 'jmercer'
    assert (r['mandateName'], r['accountType'], r['taxDomicile']) == ('Ashworth', 'Trust', 'Spain')
    assert (r['bookingCentre'], r['fundingAmount'], r['fundingSource']) == ('London', 25e6, 'Wire')
    assert (r['fundingDate'], r['kycReference'], r['notes']) == ('2026-09-30', 'KYC-1', 'Two tranches.')
    assert r['clientRelationship'] == r['mifidClass'] == r['custodyPlatform'] == ''
    assert not set(r) & set(accountRequests.SINCE), 'never asked for what the form took later'
    again = dashboardRouter.createAccountRequest(_payload(uid), caller=_caller('alice'))
    assert again.status_code == 422 and 'ar_0123456789ab' in _body(again)['error']

    # and a request made now sits beside it in the rebuilt table
    other, _, _ = _deliver()
    made = dashboardRouter.createAccountRequest(_payload(other['proposalId']),
                                                caller=_caller('alice'))['request']
    assert [x['requestId'] for x in accountRequests.listRequests()] == [made['requestId'],
                                                                         'ar_0123456789ab']
    conn = sqlite3.connect(str(tmp_path / 'before.db'))
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert 'accountRequestsBefore' not in names and {'ar_proposal', 'ar_at'} <= names
        held = [r[1] for r in conn.execute('PRAGMA table_info(accountRequests)')]
        assert 'clientName' not in held and 'mandateName' in held
        fk = conn.execute('PRAGMA foreign_key_list(accountRequests)').fetchall()
        assert [(row[2], row[3], row[4]) for row in fk] == [('proposals', 'proposalId', 'proposalId')]
    finally:
        conn.close()


def test_a_table_from_before_the_offering_entity_is_given_its_columns_in_place(tmp_path, monkeypatch):
    """D202 added the offering entity, the phasing timeline and the
    allocation info. A register already holding requests is given the three
    columns in place - no rebuild - and its requests read as they were made,
    without them."""
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'before.db'))
    entry, _, _ = _deliver()
    uid = entry['proposalId']
    conn = sqlite3.connect(str(tmp_path / 'before.db'))
    schema = accountRequests._SCHEMA
    cut = schema.index(',\n    -- asked since the form took the offering entity')
    conn.executescript(schema[:cut] + '\n);' + schema[schema.index('CREATE INDEX'):])
    held = [r[1] for r in conn.execute('PRAGMA table_info(accountRequests)')]
    assert not set(held) & set(accountRequests.SINCE), 'the table as it stood before'
    conn.execute('INSERT INTO accountRequests (requestId, proposalId, submittedAt, submittedBy, '
                 'mandateName, accountType, taxDomicile, custodyPlatform) VALUES (?,?,?,?,?,?,?,?)',
                 ('ar_0123456789ab', uid, '2026-10-01T09:00:00', 'jmercer', 'Ashworth', 'Trust',
                  'Spain', 'GSI'))
    conn.commit()
    conn.close()

    [r] = accountRequests.forProposal(uid)
    assert r['requestId'] == 'ar_0123456789ab' and r['mandateName'] == 'Ashworth'
    assert not set(r) & set(accountRequests.SINCE)
    other, _, _ = _deliver()
    made = dashboardRouter.createAccountRequest(_payload(other['proposalId']),
                                                caller=_caller('alice'))['request']
    assert made['offeringEntity'] == 'GSBE' and made['allocationInfo']['riskProfile'] == 'Moderate'
    conn = sqlite3.connect(str(tmp_path / 'before.db'))
    try:
        held = [r[1] for r in conn.execute('PRAGMA table_info(accountRequests)')]
        assert held[-len(accountRequests.SINCE):] == list(accountRequests.SINCE)
        assert conn.execute('SELECT COUNT(*) FROM accountRequests').fetchone()[0] == 2
    finally:
        conn.close()


def test_the_allocation_info_of_a_custom_portfolio_and_of_the_edge_cases():
    """D202. A custom portfolio (D171) has no tilt switch: it implements the
    tilt when it holds a product in the tilt's category, and its equity is
    built by hand. A strategic proposal names its allocation with the real
    assets left out where they were, a custom equity sleeve as such, and none
    where it holds no equity."""
    info = accountRequests.allocationInfo
    custom = {'customPortfolio': {'profile': 'ModAgg'}, 'hedging': 'Unhedged',
              'implemented': [{'category': 'Public Equity', 'weightPct': 60.0},
                              {'category': 'Asset Allocation Strategies', 'weightPct': 4.0}]}
    assert info(custom) == {'allocationType': 'Custom', 'tacticalTilt': True,
                            'equityImplementation': 'Custom portfolio', 'hedgingPolicy': 'Unhedged',
                            'riskProfile': 'Moderate-Aggressive'}
    custom['implemented'] = custom['implemented'][:1]
    assert info(custom)['tacticalTilt'] is False
    strategic = {'baseKey': 'GBP|Conservative|ex-HFs|1', 'hedging': 'ISG Hedged', 'tacticalTilt': 0,
                 'sleeves': [{'category': 'Public Equity', 'sleeve': 'Custom', 'custom': {'products': []}}]}
    assert info(strategic) == {'allocationType': 'ex-HFs · ex Real Assets', 'tacticalTilt': False,
                               'equityImplementation': 'Custom sleeve', 'hedgingPolicy': 'ISG Hedged',
                               'riskProfile': 'Conservative'}
    strategic['sleeves'] = [{'category': 'Investment Grade Fixed Income', 'sleeve': 'Agg'}]
    assert info(strategic)['equityImplementation'] == 'None'


def test_the_store_is_append_only_by_construction():
    """No update, no delete: the module simply has no such function."""
    public = [name for name in dir(accountRequests) if not name.startswith('_')]
    assert not [n for n in public if any(w in n.lower() for w in ('update', 'delete', 'remove', 'edit'))]


# ------------------------------------------------------------ the two tiers ---
# A PWA on the access list runs the whole proposal flow; the repository is the
# admin's alone (D77). Locked structurally on the routes, then proven over
# HTTP through the mirror app with the strictest grant the host ever makes.

def test_the_proposal_flow_needs_only_the_allowlist_and_the_repository_needs_the_admin():
    seen = 0
    for route in dashboardRouter.router.routes:
        if not route.path.startswith('/scenario'):
            continue
        names = [d.call.__name__ for d in route.dependant.dependencies]
        seen += 1
        if route.path.startswith('/scenario/repository'):
            assert 'requireAdmin' in names, (route.path, names)
        else:
            assert 'requireEditor' not in names and 'requireAdmin' not in names, (route.path, names)
            assert set(names) <= {'requireAuth'}, (route.path, names)
    # 32, the funding split's five (D148), the overlays' five (D155), the
    # catalogue's nine (D165), the fee card's seven (D166), the sleeve switch (D169) and custom sleeves' one (D170)
    assert seen == 95                      # custom portfolios' three (D171), the switch (D172), sharing (D179), the notices' four where the marketing lists' were (D196, D199); the entities' four gone (D195); the SMA mandate file's three (D197) gone again (D207); the private markets minimums' seven (D200); the restrictions' four (D203); the mandate codes' seven (D205); the MAP benchmarks' seven (D211); the listings' order (D214)


def test_a_pwa_runs_the_flow_end_to_end_and_only_an_admin_reaches_the_repository(monkeypatch):
    """Over HTTP. bob is on the access list only - which in PROD is `view` and
    nothing more - and alice is an admin. bob creates, resolves, attaches,
    exports and requests; the repository answers 403 to bob and 200 to alice;
    the schema tells the page which is which."""
    from fastapi.testclient import TestClient
    from cyrus_pmg.pmgService.core import accessControl
    from cyrus_pmg.pmgService.isgPMGService import app
    from cyrus_pmg.pmgService.scenario import sleeves
    from test_scenario_backend import BASIS, PORT, _sleeveMap
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice,bob')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    bob = accessControl.requireAuth(_request('bob'))
    assert not accessControl.can(bob, accessControl.RESOURCE_PMGAPI, accessControl.ACTION_MODIFY), \
        'the strictest footing: bob may not modify, and still does everything below'

    client = TestClient(app)
    pwa, admin = {'X-Kerberos': 'bob'}, {'X-Kerberos': 'alice'}
    made = client.post('/api/v1/scenario', headers=pwa, json={
        'mandate': {'topAccountSize': 1e9, 'mandateSize': 1e9, 'primaryPwa': 'A. Castellanos — Madrid', 'entity': 'gsi'},
        'basis': {'currency': 'USD', 'hedging': 'Hedged'}})
    assert made.status_code == 200, made.text
    sid = made.json()['id']
    key = {'currency': 'USD', 'riskLevel': 'Moderate', 'allocationType': 'Full', 'excludeRealAssets': False}
    assert client.put('/api/v1/scenario/' + sid, headers=pwa,
                      json={'variant': sleeves.VARIANTS[0]}).status_code == 200
    resolved = client.post('/api/v1/scenario/' + sid + '/portfolio', headers=pwa,
                           json={'key': key, 'role': 'base'})
    assert resolved.status_code == 200, resolved.text
    chosen = _sleeveMap(PORT.resolve_portfolio(BASIS, PortfolioKey.fromDict(key))['categories'],
                        sleeves.VARIANTS[0])
    assert client.put('/api/v1/scenario/' + sid, headers=pwa, json={'sleeves': chosen}).status_code == 200
    # the notice the hedge funds it holds ask for (D198)
    assert client.put('/api/v1/scenario/' + sid, headers=pwa, json={'noticeAction': {'notice': 'n1', 'action': 'confirm', 'ticked': True, 'category': 'Hedge Funds', 'sleeve': ''}}).status_code == 200
    exported = client.post('/api/v1/scenario/' + sid + '/export', headers=pwa)
    assert exported.status_code == 200, exported.text
    uid = exported.headers['x-proposal-id']
    # a proposal delivered unpriced: the PWA gives each row of the SMA Details its fee (D206)
    found = client.get('/api/v1/scenario/proposals/' + uid, headers=pwa).json()['proposal']['smaDetails']
    assert found['fees'] == 'none' and found['rows'][0]['kind'] == 'mandate'
    requested = client.post('/api/v1/scenario/account-requests', headers=pwa, json=_payload(
        uid, smaFees={r['key']: '45' for r in found['rows']}))
    assert requested.status_code == 200, requested.text
    assert requested.json()['request']['submittedBy'] == 'bob'

    for path in ('/api/v1/scenario/repository', '/api/v1/scenario/repository/proposals',
                 '/api/v1/scenario/repository/proposals/' + uid):
        refused = client.get(path, headers=pwa)
        assert refused.status_code == 403, (path, refused.text)
        assert 'error' in refused.json()
    assert client.get('/api/v1/scenario/repository', headers=admin).status_code == 200
    assert client.get('/api/v1/scenario/repository/proposals/' + uid, headers=admin).status_code == 200
    assert client.get('/api/v1/scenario/schema', headers=pwa).json()['capabilities']['canAdmin'] is False
    assert client.get('/api/v1/scenario/schema', headers=admin).json()['capabilities']['canAdmin'] is True


def test_account_opening_has_two_doors_and_neither_is_an_action_row_button():
    """D107. The request used to be a third button in the Start a proposal
    card's action row, beside Continue and Cancel - a terminal action on a
    delivered proposal sitting among the controls for starting one. It now has
    a door of its own on the landing page, and the card carries only a link
    below the rule. Pinned because the placement is the whole point of the
    change and a later tidy-up would not know that."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.join(here, '..', '..')
    with open(os.path.join(root, 'generator', 'js', 'core.js'), encoding='utf-8') as fh:
        source = fh.read()
    with open(os.path.join(root, 'proposalTool', 'proposalTool.html'), encoding='utf-8') as fh:
        page = fh.read()

    # the landing page's own door, beside Start Here and outside any card
    assert 'id="lpaccount"' in page, 'the landing page needs its own entry'
    assert page.index('id="startbtn"') < page.index('id="lpaccount"'), \
        'Start Here leads; the account door is the quieter second'
    # and it sits on the far side of the column, on the admin strip's own axis
    with open(os.path.join(root, 'proposalTool', 'static', 'css', 'proposalTool.css'),
              encoding='utf-8') as fh:
        css = fh.read()
    rule = css[css.index('.lp-second{'):css.index('.lp-second:hover')]
    assert 'margin-left:auto' in rule, 'the second door is right-aligned (D109)'
    # and Start Here carries no arrow: pointing right along the row, it pointed
    # at the other door rather than saying "begin" (D112)
    assert '.lp-cta::after' not in css, 'Start Here must not point at Open an Account'

    # the card's link is below the rule, not in the action row
    actions = source.index("id=\"dlgsave\"")
    assert actions < source.index("id=\"dlgaccount\""), \
        'the account link must come after the action row, not inside it'
    assert 'dlg-aside' in source and 'btn-inline' in source
    assert 'dlg-actions split"><div class="dlg-grp"' not in source, \
        'the three-button landing row is gone'
    assert "(first ? ' start' : '')" not in source, \
        'the 600px card existed only to seat the third button'

    # and opening it no longer throws away what the user typed
    assert 'acDraftBack = draft;' in source
    assert 'if (acDraftBack) { draft = acDraftBack; acDraftBack = null; }' in source
    assert 'draft = null; renderDialog(); }    /* the card gives way' not in source


def test_the_account_card_opens_on_the_uid_alone_and_grows_only_for_a_live_proposal():
    """D111. The card opens holding only the Proposal UID. It grows into the
    request - the proposal's terms, then the fields - only when the UID names a
    proposal that can still take one; looking up, a malformed or unknown UID and
    an already-requested proposal are all answered in the small card. Rendered
    in node from the page's own source, every state in turn."""
    import os
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, '..', '..', 'generator', 'js', 'core.js'), encoding='utf-8') as fh:
        source = fh.read()
    region = source[source.index('var account = null;'):source.index('function accountReceiptMarkup(')]
    script = r'''
function esc(v) { return String(v == null ? '' : v).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function money(n) { return '$' + Math.round(n).toLocaleString('en-US'); }
const OPTIONS = { accountTypes: ['Trust', 'Fund'], yesNo: ['Yes', 'No'], custodyPlatforms: ['GSI', 'External COD'],
  externalCustody: 'External COD', defaults: { capitalIncomeSplit: 'No' },
  offeringEntities: ['GSI', 'GSBE'], phasingTimelines: ['Immediate', '1 month'],
  phasingNote: 'Timeline for the mandate <to be> fully invested.' };
function opt(path, d) { const k = path.split('.').pop(); return k in OPTIONS ? OPTIONS[k] : d; }
function riskLabel(r) { return 'Risk ' + r; }
''' + region + r'''
function fresh() {
  return { uid: '', status: 'idle', message: '', proposal: null, requests: [],
    fields: acBlankFields(), restrictions: [], chosen: [], rq: '', fees: {},
    err: null, dirty: false, submitting: false, receipt: null, seq: 0, timer: null };
}
const P = { proposalId: 'pr_bafdb99b80ba', primaryPwa: 'J. Mercer', mandateSize: 25000000,
  topAccountSize: 10000000, currency: 'USD', hedging: 'Hedged', variant: 'PMG Multi-Asset Portfolio',
  riskLevel: '4', allocationType: 'Strategic', includeFees: false, exportedAt: '2026-09-21T10:00:00',
  exportedBy: 'fbarker', sequence: 1,
  sleeves: [{category:'Equity',sleeve:'Core',revision:3},{category:'Fixed Income',sleeve:'Agg'},
            {category:'Real Assets',sleeve:'Infra'},{category:'Cash',sleeve:'MMF'}],
  tacticalTilt: true, volPremium: true,
  allocationInfo: { allocationType: 'Full · ex Real Assets', tacticalTilt: false,
    equityImplementation: 'Global Equity Core', hedgingPolicy: 'Hedged', riskProfile: 'Moderate' } };
const out = {};
function snap(name) {
  const html = accountFormMarkup();
  out[name] = { open: accountExpanded(), status: accountStatusText(),
    uidBox: html.includes('id="acuid"'), fields: html.includes('id="acname"'),
    custodian: html.includes('id="accustodian"'), split: html.includes('acsplit'),
    chips: (html.match(/<span class="ac-chip">[^<]*/g) || []).map(c => c.slice(22)),
    ticks: (html.match(/data-acr="[^"]*"( checked)?/g) || []),
    rnone: html.includes('No restrictions are on offer yet.'), rsearch: html.includes('id="acrsearch"'),
    sma: html.includes('class="ac-sma"'), feeBoxes: (html.match(/data-acfee=/g) || []).length,
    smaName: (html.match(/id="acsmaname">([^<]*)/) || [])[1] || null, blockedRows: (html.match(/<tr class="blocked">/g) || []).length,
    digest: html.includes('class="ac-digest"'), submit: html.includes('id="acsubmit"'),
    change: html.includes('id="acchange"'), spin: html.includes('ac-spin'),
    held: html.includes('dlg-held'), copy: html.includes('id="accopy"'),
    kept: html.includes('details are kept'),
    sleeves: html.includes('sleeve') && /<b>\d+ sleeves?<\/b>/.test(html),
    switches: html.includes('Switches') || html.includes('Vol premium'),
    terms: (html.match(/<dl class="ac-digest">([^]*?)<\/dl>/) || ['', ''])[1].match(/<dt>[^<]*/g),
    info: (html.match(/<dl class="ac-digest ac-fixed"[^>]*>([^]*?)<\/dl>/) || ['', ''])[1]
      .match(/<dt>[^<]*<\/dt><dd>[^<]*/g),
    entity: html.includes('id="acentity"'),
    note: (html.match(/<p class="ac-note" id="acphasingnote" role="tooltip">([^<]*)</) || [])[1] || null,
    described: (html.match(/<select id="acphasing"[^>]*aria-describedby="([^"]*)"/) || [])[1] || null };
}
account = fresh(); snap('idle');
account.uid = 'pr_bafdb99b80ba'; account.status = 'looking'; snap('looking');
account.status = 'malformed'; account.message = UID_SHAPE; snap('malformed');
account.status = 'notfound'; account.message = 'No proposal.'; snap('notfound');
account.status = 'found'; account.proposal = P;
account.requests = [{requestId:'ar_52e1c07b9a3d', submittedBy:'jmercer', submittedAt:'2026-09-18T14:05:00'}];
snap('held');
account.requests = []; snap('untouched');
account.restrictions = [{id: 'r1', name: 'Capital/Income Split', description: ''},
                        {id: 'r2', name: 'No UK <Situs> Asset', description: 'Land & buildings'}];
account.chosen = ['r2'];
account.fields.mandateName = 'Ashworth'; snap('found');
['clientRelationship','accountType','mifidClass','taxDomicile','ukRndClient','gsAffiliate',
 'offeringEntity','phasingTimeline'].forEach(k => account.fields[k] = 'x');
account.fields.custodyPlatform = 'External COD'; snap('external');
account.fields.custodianName = 'Pictet'; snap('ready');
account.fields.custodyPlatform = 'GSI'; account.fields.custodianName = ''; snap('ownCustody');
P.smaDetails = { fees: 'none', rows: [
  { key: 'mandate', kind: 'mandate', name: '', category: '', code: 'PMG-1', benchmark: 'B', benchmarkFrom: 'table',
    feeBp: null, feeFrom: 'typed', funding: 25000000, inSmas: 5000000, initialFunding: null, problem: '' },
  { key: 'sma:x|IGFI', kind: 'sma', name: 'X SMA', category: 'IGFI', code: '', benchmark: '', benchmarkFrom: '',
    feeBp: null, feeFrom: 'typed', funding: 5000000, initialFunding: 6000000, problem: 'No mandate code for USD.' }] };
snap('smaBlocked');
P.smaDetails.rows[1].problem = ''; snap('smaFees');
account.fees = { mandate: '45', 'sma:x|IGFI': '30.5' }; snap('smaReady');
account.fees = { mandate: '45', 'sma:x|IGFI': '600' }; snap('smaTooHigh');
delete P.smaDetails;
account.status = 'idle'; account.proposal = null; snap('changed');
process.stdout.write(JSON.stringify(out));
'''
    got = json.loads(subprocess.run([node, '-e', script], capture_output=True, text=True,
                                    check=True).stdout)

    # the small card: the UID box and nothing else to fill, in every state short of a live match
    for state in ('idle', 'looking', 'malformed', 'notfound', 'held'):
        s = got[state]
        assert s['uidBox'] and not s['open'], state
        assert not s['fields'] and not s['digest'] and not s['submit'], \
            '%s must not show the request, nor a Submit waiting under one box' % state
    assert got['idle']['status'] == 'Paste the UID to continue.'
    assert got['looking']['spin'], 'the spinner sits inside the UID box'
    # already requested is answered where it is asked, with the reference to copy
    assert got['held']['held'] and got['held']['copy']

    # a live match: the card grows, the UID locks with a way back, the terms read as text
    f = got['found']
    assert f['open'] and not f['uidBox'] and f['change'] and f['digest'] and f['fields'] and f['submit']
    # the terms without the switches, the risk and allocation or the sleeves;
    # what the proposal implements fixed beneath them (D202)
    assert not f['sleeves'] and not f['switches']
    assert [t[4:] for t in f['terms']] == ['Primary PWA', 'Base currency', 'Mandate size',
                                          'Top account size', 'Implementation', 'Fees']
    assert f['info'] == ['<dt>Allocation Type</dt><dd>Full · ex Real Assets',
                         '<dt>Risk Profile</dt><dd>Moderate',
                         '<dt>Currency Hedging Policy</dt><dd>Hedged',
                         '<dt>Tactical Tilt Implementation</dt><dd>False',
                         '<dt>Equity Allocation Implementation</dt><dd>Global Equity Core']
    assert f['entity'], 'the offering entity is asked for'
    assert f['note'] == 'Timeline for the mandate &lt;to be&gt; fully invested.', \
        'the phasing note rides the schema, escaped'
    assert f['described'] == 'acphasingnote', 'and is read with the drop-down it describes'
    assert not f['split'], 'the capital/income split is a restriction now (D203)'
    # the restrictions: what is chosen as chips, the list ticked to match (D203)
    assert f['chips'] == ['No UK &lt;Situs&gt; Asset'] and not f['rsearch'], 'a short list needs no search'
    assert f['ticks'] == ['data-acr="r1"', 'data-acr="r2" checked']
    assert got['untouched']['rnone'] and not f['rnone'], 'an empty list says so'
    assert not got['untouched']['kept'] and got['untouched']['status'].startswith('10 required fields'), \
        'the default is the card\'s answer, not the user\'s, and is not one left to give'
    # the custodian is asked for only for external custody, and then required
    assert not f['custodian'] and got['external']['custodian']
    assert got['external']['status'] == '1 required field remaining: Custodian Name.'
    assert not got['ownCustody']['custodian'] and got['ownCustody']['status'].startswith('Ready.')
    # SMA Details (D206): a row without its code stops Submit and says so; a
    # proposal never priced asks each row its fee; the mandate's row is named
    # as the mandate is
    assert not got['ownCustody']['sma']
    b = got['smaBlocked']
    assert b['sma'] and b['feeBoxes'] == 2 and b['smaName'] == 'Ashworth' and b['blockedRows'] == 1
    assert b['status'] == ('2 required fields remaining: Management fee of Ashworth, Management fee of X SMA. '
                           'Submit is off: X SMA: No mandate code for USD.')
    assert got['smaFees']['status'] == '2 required fields remaining: Management fee of Ashworth, Management fee of X SMA.'
    assert got['smaReady']['status'].startswith('Ready.')
    assert got['smaTooHigh']['status'] == '1 required field remaining: Management fee of X SMA.'
    assert got['ready']['status'] == ('Ready. Submitting records the request against '
                                      'pr_bafdb99b80ba (J. Mercer, $25,000,000).')

    # Change: back to the small card, and what was typed is kept and said to be
    c = got['changed']
    assert c['uidBox'] and not c['open'] and c['kept']


# ------------------------------------------------------------ SMA Details ---
# The mandate, then each separate account the implementation holds (D206):
# codes and benchmarks from the mandate codes table and the Catalogue, fees
# from the delivered workbook, funding from the record.

def _smaItems(uid):
    entry = proposalRegister.getProposal(uid)
    from cyrus_pmg.pmgService.scenario import products
    return [(g, i) for g in entry['implemented'] for i in g['items'] if products.isSma(i)]


def test_the_sma_details_name_the_mandate_then_each_sma():
    from cyrus_pmg.pmgService.scenario import fees
    entry, _, _ = _deliver()
    uid = entry['proposalId']
    d = dashboardRouter.lookupProposal(uid)['proposal']['smaDetails']
    assert d['fees'] == 'oneRate', 'CASP: one rate on the whole mandate'
    mandate, smas = d['rows'][0], d['rows'][1:]
    rate = fees.productFee('CASP', 'PMG Target', None, topAccountSize=50e6, mandateSize=50e6) * 100
    # delivered before it named a benchmark: the Benchmarks tab's for its portfolio (D211)
    assert (mandate['kind'], mandate['code'], mandate['benchmark'], mandate['benchmarkFrom']) == \
        ('mandate', 'PMG-TEST', 'Test benchmark USD Moderate', 'table')
    assert mandate['benchmarkMnemonic'] == 'TB-USD'
    assert mandate['feeBp'] == pytest.approx(rate, abs=0.01) and mandate['feeFrom'] == 'proposal'
    held = _smaItems(uid)
    assert [r['productId'] for r in smas] == [i['productId'] for _, i in held] and smas
    for r, (g, i) in zip(smas, held):
        assert r['code'].startswith(i['productId'][:20] + '-USD') and r['category'] == g['category']
        assert (r['benchmark'], r['benchmarkFrom']) == ('Bench of ' + i['name'], 'listing')
        assert (r['feeBp'], r['feeFrom']) == (None, 'n/a') and r['funding'] == i['notional'] and not r['problem']
    assert mandate['funding'] == 50e6 and mandate['inSmas'] == sum(i['notional'] for _, i in held)
    made = dashboardRouter.createAccountRequest(_payload(uid), caller=_caller('alice'))['request']
    recorded = made['smaDetails']
    assert recorded[0]['name'] == 'Herrera Family Trust' and recorded[0]['code'] == 'PMG-TEST'
    assert [r['name'] for r in recorded[1:]] == [i['name'] for _, i in held]
    assert all('problem' not in r for r in recorded)


def test_under_rdr_each_sma_quotes_its_own_rate_and_the_mandate_the_weighted_one(monkeypatch):
    import test_proposal_register
    from cyrus_pmg.pmgService.scenario import fees, products
    results, impl = test_proposal_register._case()
    monkeypatch.setattr(test_proposal_register, '_case', lambda: (results, dict(impl, feeSchedule='RDR')))
    entry, _, _ = test_proposal_register._deliver()
    uid = entry['proposalId']
    d = dashboardRouter.lookupProposal(uid)['proposal']['smaDetails']
    assert d['fees'] == 'byProduct'
    for r, (_, i) in zip(d['rows'][1:], _smaItems(uid)):
        group = products.get(i['productId'])['feeGroup']
        assert r['feeBp'] == pytest.approx(fees.productFee('RDR', 'PMG Target', group, topAccountSize=50e6,
                                                           mandateSize=50e6) * 100, abs=0.01)
        assert r['feeFrom'] == 'proposal'
    every = [i for g in proposalRegister.getProposal(uid)['implemented'] for i in g['items']]
    weighted = sum(fees.productFee('RDR', 'PMG Target', products.get(i['productId'])['feeGroup'],
                                   topAccountSize=50e6, mandateSize=50e6) * i['printedPct'] for i in every)
    assert d['rows'][0]['feeBp'] == pytest.approx(weighted, abs=0.05)


def test_an_unpriced_proposal_asks_each_row_its_fee_in_basis_points():
    entry, _, _ = _deliver(includeFees=False)
    uid = entry['proposalId']
    d = dashboardRouter.lookupProposal(uid)['proposal']['smaDetails']
    assert d['fees'] == 'none' and all(r['feeBp'] is None and r['feeFrom'] == 'typed' for r in d['rows'])
    keys = [r['key'] for r in d['rows']]
    for fees_ in ({}, dict.fromkeys(keys, '600'), dict.fromkeys(keys, '1.234'), dict.fromkeys(keys, 'x')):
        refused = dashboardRouter.createAccountRequest(_payload(uid, smaFees=fees_), caller=_caller('alice'))
        assert refused.status_code == 422 and _body(refused)['field'] == 'smaFees'
    typed = dict.fromkeys(keys, '35')
    typed['mandate'] = '62.5 bp'
    made = dashboardRouter.createAccountRequest(_payload(uid, smaFees=typed), caller=_caller('alice'))['request']
    assert [r['feeBp'] for r in made['smaDetails']] == [62.5] + [35.0] * (len(keys) - 1)
    assert all(r['feeFrom'] == 'typed' for r in made['smaDetails'])


def test_a_missing_code_or_benchmark_stops_the_request_and_says_who_gives_it():
    from cyrus_pmg.pmgService.scenario import accountRequests, mandateCodes, portfolioBenchmarks, products
    entry, _, _ = _deliver()
    uid = entry['proposalId']
    pid = _smaItems(uid)[0][1]['productId']
    kept = products.entry(pid)

    def save(**over):
        p = products.entry(pid)
        fields = {k: p[k] for k in products.PRODUCT_FIELDS}
        fields['listings'] = [dict(l, **over) for l in p['listings']]
        products.update(pid, fields, user='alice', base=p['revision'])

    def row():
        rows = dashboardRouter.lookupProposal(uid)['proposal']['smaDetails']['rows']
        return next(r for r in rows if r['productId'] == pid)
    try:
        # the listing the proposal held, as the Catalogue has it now (D207)
        save(code='')
        assert row()['problem'] == ("No mandate code for the listing it holds. An admin adds it to the listing on "
                                    "the product's card in the Catalogue.")
        refused = dashboardRouter.createAccountRequest(_payload(uid), caller=_caller('alice'))
        assert refused.status_code == 422 and _body(refused)['field'] == 'smaDetails'
        assert kept['name'] in _body(refused)['error']
        # given its code since: the held listing is found again by what else it is
        save(code=kept['listings'][0]['code'], benchmark='')
        assert row()['problem'].startswith('No benchmark for the listing it holds.')
        save(benchmark='Its own benchmark', issueCurrency='GBP', hedging='')
        assert (row()['benchmark'], row()['benchmarkFrom'], row()['problem']) == ('Its own benchmark', 'listing', '')
    finally:
        save(code=kept['listings'][0]['code'], benchmark=kept['listings'][0]['benchmark'],
             issueCurrency=kept['listings'][0]['issueCurrency'])
    assert row()['problem'] == ''
    # the mandate's own code
    cell = mandateCodes.keyOf('PMG Multi-Asset Portfolio', 'USD|Moderate|Full|0', hedging='Hedged')
    before = mandateCodes.current()
    assert mandateCodes.codeFor(cell) == 'PMG-TEST'
    mandateCodes.save({'groups': [{'name': 'GBP only', 'code': 'PMG-GBP', 'currencies': ['GBP']}]}, user='alice',
                      base=before['revision'])
    try:
        rows = dashboardRouter.lookupProposal(uid)['proposal']['smaDetails']['rows']
        assert rows[0]['problem'] == ('No mandate code for PMG Multi-Asset Portfolio · Full · Moderate · USD · '
                                      'Hedged: no group in Account Opening, Mandate Codes holds it. An admin adds it '
                                      'to a group there.')
    finally:
        mandateCodes.save({'groups': before['groups']}, user='alice', base=before['revision'] + 1)
    # and its benchmark (D211): delivered without one, the Benchmarks tab's for its portfolio
    before = portfolioBenchmarks.current()
    portfolioBenchmarks.save({'rows': [r for r in before['rows'] if portfolioBenchmarks.rowKey(r) != 'Moderate|USD|Hedged']},
                             user='alice', base=before['revision'])
    try:
        rows = dashboardRouter.lookupProposal(uid)['proposal']['smaDetails']['rows']
        assert rows[0]['problem'] == 'No benchmark for Moderate · USD · Hedged. An admin adds it in Account Opening, MAP Benchmarks.'
        # one delivered with a benchmark names that one, whatever the tab says now
        named = accountRequests.smaDetails(dict(proposalRegister.getProposal(uid), benchmark='Delivered with',
                                                benchmarkMnemonic='DW'))
        first = named['rows'][0]
        assert (first['benchmark'], first['benchmarkMnemonic'], first['benchmarkFrom'], first['problem']) == \
            ('Delivered with', 'DW', 'proposal', '')
        # a custom portfolio's is the one chosen for it, its mnemonic the table's for that benchmark
        custom = dict(proposalRegister.getProposal(uid), customPortfolio={'profile': 'Moderate'})
        assert accountRequests.mandateBenchmark(custom) == ('', '', '')
        assert accountRequests.mandateBenchmark(dict(custom, customPortfolio={
            'profile': 'Moderate', 'benchmark': 'Test benchmark GBP Agg'})) == ('Test benchmark GBP Agg', 'TB-GBP', 'proposal')
    finally:
        portfolioBenchmarks.save({'rows': before['rows']}, user='alice', base=before['revision'] + 1)
