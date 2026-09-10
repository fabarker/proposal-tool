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
from cyrus_pmg.pmgService.scenario import accountRequests, proposalRegister
from cyrus_pmg.pmgService.scenario.registry import getScenarioPort
from cyrus_pmg.pmgService.scenario.types import BasisInput, PortfolioKey
from test_proposal_register import _caller, _deliver


def _request(kerberos=None):
    headers = [(b'x-kerberos', kerberos.encode())] if kerberos else []
    return Request({'type': 'http', 'method': 'GET', 'path': '/', 'query_string': b'',
                    'headers': headers})


def _payload(uid, **over):
    base = {'proposalId': uid, 'clientName': 'Herrera Family Trust', 'accountType': 'Trust',
            'bookingCentre': 'London', 'taxResidency': 'Spain', 'fundingAmount': 50_000_000,
            'fundingSource': 'Wire', 'fundingDate': '2026-09-30',
            'kycReference': 'KYC-2026-08812', 'notes': 'Funding in two tranches.'}
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
    assert p['sleeves'] and all(set(s) == {'category', 'sleeve', 'revision'} for s in p['sleeves'])
    assert 'allocation' not in p and 'implemented' not in p, 'parameters, not the pictures'
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
    assert r['clientName'] == 'Herrera Family Trust' and r['accountType'] == 'Trust'
    assert r['bookingCentre'] == 'London' and r['taxResidency'] == 'Spain'
    assert r['fundingAmount'] == 50e6 and r['fundingSource'] == 'Wire'
    assert r['fundingDate'] == '2026-09-30' and r['kycReference'] == 'KYC-2026-08812'
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
    again = dashboardRouter.createAccountRequest(_payload(uid, clientName='Someone Else'),
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
    ('clientName', ''), ('clientName', 'x' * 201),
    ('accountType', 'Partnership'), ('bookingCentre', 'Mars'), ('taxResidency', '   '),
    ('fundingAmount', 0), ('fundingAmount', -5), ('fundingAmount', 'lots'),
    ('fundingSource', 'Cash'), ('fundingDate', '30/09/2026'), ('fundingDate', ''),
    ('proposalId', 'sc_0123456789ab'), ('notes', 'x' * 2001),
])
def test_each_wrong_field_is_named_and_nothing_is_written(field, value):
    entry, _, _ = _deliver()
    response = dashboardRouter.createAccountRequest(
        _payload(entry['proposalId'], **{field: value}), caller=_caller('alice'))
    assert response.status_code == 422, field
    assert _body(response)['field'] == field
    assert accountRequests.forProposal(entry['proposalId']) == []


def test_the_optional_fields_may_be_left_out():
    entry, _, _ = _deliver()
    payload = _payload(entry['proposalId'])
    del payload['kycReference']
    del payload['notes']
    r = dashboardRouter.createAccountRequest(payload, caller=_caller('alice'))['request']
    assert r['kycReference'] == '' and r['notes'] == ''


def test_text_is_trimmed_and_the_amount_may_arrive_as_a_string():
    entry, _, _ = _deliver()
    r = dashboardRouter.createAccountRequest(
        _payload(entry['proposalId'], clientName='  Herrera Family Trust  ', fundingAmount='25000000'),
        caller=_caller('alice'))['request']
    assert r['clientName'] == 'Herrera Family Trust' and r['fundingAmount'] == 25e6


# ------------------------------------------------------------------ store ---

def test_the_options_ride_the_schema_and_are_marked_placeholder():
    schema = dashboardRouter.getScenarioSchema(_request('alice'), caller=_caller('alice'))
    o = schema['options']['accountRequest']
    assert o['accountTypes'] == accountRequests.ACCOUNT_TYPES
    assert o['bookingCentres'] == accountRequests.BOOKING_CENTRES
    assert o['fundingSources'] == accountRequests.FUNDING_SOURCES
    assert set(o['required']) == set(accountRequests.REQUIRED)
    assert o['placeholder'] is True, 'until Operations confirms the lists'
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
    assert seen == 30


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
        'mandate': {'topAccountSize': 1e9, 'mandateSize': 1e9, 'primaryPwa': 'A. Castellanos — Madrid'},
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
    exported = client.post('/api/v1/scenario/' + sid + '/export', headers=pwa)
    assert exported.status_code == 200, exported.text
    uid = exported.headers['x-proposal-id']
    requested = client.post('/api/v1/scenario/account-requests', headers=pwa, json=_payload(uid))
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
