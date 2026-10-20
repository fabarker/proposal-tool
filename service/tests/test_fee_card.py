"""The fee card kept in the repository and maintained from the console's
Fees tab (D166).

Each test has a repository file of its own, its card loaded from the
packaged file on the first open exactly as an upgraded host's is."""
import base64
import copy
import inspect
import json

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import feeCard, fees, products, proposalRegister, rules
from cyrus_pmg.pmgService.scenario.types import MandateInput, ValidationError


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(tmp_path / 'proposals.db'))
    products.reload()
    feeCard._forget()
    yield tmp_path
    products.reload()
    feeCard._forget()


def _caller(kerberos='alice'):
    from cyrus_pmg.pmgService.core import accessControl
    return accessControl.UserData(kerberos, accessControl._rolesFor(kerberos))


def _card():
    return copy.deepcopy(feeCard.describe()['card'])


def _withGroup(card, name, like='Passive'):
    card['feeGroups'].append(name)
    card['rates']['RDR'][name] = copy.deepcopy(card['rates']['RDR'][like])
    return card


def _product(pid, group):
    return {'productId': pid, 'name': pid.title(), 'ticker': '', 'assetClass': 'Global Equity',
            'style': 'Active', 'vehicle': 'SMA', 'shareClass': '', 'source': 'Internal',
            'liquidity': 'Daily', 'exposureCurrency': 'USD', 'productCost': 0.2,
            'feeGroup': group}


# ------------------------------------------------------------ the first load ---

def test_the_first_open_loads_the_file_and_prices_exactly_as_the_file_did(store):
    delivered = fees._readDelivery(fees.ratesPath())
    held = feeCard.current()
    assert held['cells'] == delivered['cells']
    assert [t['id'] for t in held['tiers']] == [t['id'] for t in delivered['tiers']]
    assert held['feeGroups'] == delivered['feeGroups']
    info = fees.deliveryInfo()
    assert info['version'] == 'Version 1' and info['placeholder'] is fees.PLACEHOLDER
    assert info['lastEditedBy'] == 'seed' and 'feeRates.csv' in info['source']
    assert [h['action'] for h in feeCard.history()] == ['seeded']


# ------------------------------------------------------------------ rates ---

def test_a_rate_changed_in_the_console_prices_at_once_and_is_on_the_record(store):
    card = _card()
    before = fees.managementFee('RDR', 15e6, 'PMG Target', 'Core Active')
    card['rates']['RDR']['Core Active']['T2']['PMG Target'] = 0.46
    saved = feeCard.save(card, 'Pricing round', user='alice', base=1)
    assert saved['revision'] == 2
    assert fees.managementFee('RDR', 15e6, 'PMG Target', 'Core Active') == 0.46 != before
    assert saved['changes'] == ['RDR · Core Active · 10m – 25m · PMG Target 0.44 → 0.46']
    info = fees.deliveryInfo()
    assert info['version'] == 'Version 2' and not info['placeholder'] and info['lastEditedBy'] == 'alice'
    top = feeCard.history()[0]
    assert top['action'] == 'updated' and top['note'] == 'Pricing round' and top['current']
    assert top['changes'] == saved['changes']
    # the schema the page prices from carries it, and the PWA's viewer names it
    payload = fees.feePayload(15e6, 26e6)
    assert payload['rates']['RDR']['groups']['Core Active']['PMG Target'] == 0.46
    assert payload['delivery']['version'] == 'Version 2' and payload['delivery']['lastEditedBy'] == 'alice'
    assert fees.card()['delivery']['lastEditedAt'] == info['lastEditedAt']


def test_every_change_says_why_and_a_stale_or_empty_one_is_refused(store):
    with pytest.raises(ValidationError) as caught:
        feeCard.save(_card(), 'n', user='alice', base=1)
    assert 'Nothing has changed' in caught.value.message
    card = _card()
    card['rates']['RDR']['Passive']['T1']['PMG Ceiling'] += 0.01
    with pytest.raises(ValidationError) as caught:
        feeCard.save(card, '  ', user='alice', base=1)
    assert caught.value.field == 'note'
    feeCard.save(card, 'first', user='alice', base=1)
    with pytest.raises(feeCard.StaleError) as caught:
        feeCard.save(card, 'second', user='bob', base=1)
    assert 'alice' in caught.value.message and 'version 2' in caught.value.message


@pytest.mark.parametrize('change, words', [
    (lambda c: c['rates']['RDR']['Passive']['T1'].update({'PMG Floor': 9.0}), 'is above'),
    (lambda c: c['rates']['RDR']['Passive']['T1'].update({'PMG Ceiling': 45}), 'is not a rate'),
    (lambda c: c['rates']['RDR']['Passive']['T1'].update({'PMG Floor': -0.1}), 'is not a rate'),
    (lambda c: c['rates']['RDR']['Passive']['T1'].pop('PMG Target'), 'has no rate'),
    (lambda c: c['rates']['RDR']['Passive']['T1'].update({'PMG Target': 'lots'}), 'has no rate'),
])
def test_a_card_the_framework_cannot_price_from_is_refused_naming_where(store, change, words):
    card = _card()
    change(card)
    with pytest.raises(ValidationError) as caught:
        feeCard.save(card, 'n', user='alice', base=1)
    assert caught.value.field == 'rates' and words in caught.value.message
    assert 'RDR · Passive · Under 10m' in caught.value.message
    assert fees.deliveryInfo()['revision'] == 1


# ------------------------------------------------------------ fee groups ---

def test_a_fee_group_is_added_removed_when_unused_and_kept_while_it_prices_a_product(store):
    saved = feeCard.save(_withGroup(_card(), 'Thematic'), 'New group', user='alice', base=1)
    assert 'Added fee group Thematic' in saved['changes']
    assert 'Thematic' in fees.feeGroups()
    assert fees.managementFee('RDR', 5e6, 'PMG Target', 'Thematic') == \
        fees.managementFee('RDR', 5e6, 'PMG Target', 'Passive')
    products.create(_product('probe-thematic', 'Thematic'), user='alice')
    card = _card()
    card['feeGroups'].remove('Thematic')
    del card['rates']['RDR']['Thematic']
    with pytest.raises(ValidationError) as caught:
        feeCard.save(card, 'drop it', user='alice', base=2)
    assert caught.value.field == 'feeGroups' and 'prices 1 product' in caught.value.message
    assert 'Probe-Thematic' in caught.value.message
    products.withdraw('probe-thematic', user='alice')
    gone = feeCard.save(card, 'drop it', user='alice', base=2)
    assert 'Removed fee group Thematic' in gone['changes'] and 'Thematic' not in fees.feeGroups()
    # and a withdrawn product whose group has gone cannot come back as it was
    with pytest.raises(ValidationError) as caught:
        products.restore('probe-thematic', user='alice')
    assert caught.value.field == 'feeGroup' and 'no longer on the fee card' in caught.value.message


def test_a_renamed_fee_group_takes_its_products_with_it(store):
    priced = products.feeGroupUsage()['Passive']
    assert priced
    rate = fees.managementFee('RDR', 15e6, 'PMG Target', 'Passive')
    catalogue = products.describeSource()['revision']
    card = _card()
    card['feeGroups'][card['feeGroups'].index('Passive')] = 'Index'
    card['rates']['RDR']['Index'] = card['rates']['RDR'].pop('Passive')
    saved = feeCard.save(card, 'Say Index', user='alice', base=1, renames=[['Passive', 'Index']])
    assert saved['changes'] == ['Renamed fee group Passive to Index']
    assert saved['productsMoved'] == len(priced)
    assert fees.managementFee('RDR', 15e6, 'PMG Target', 'Index') == rate
    assert all(products.get(pid)['feeGroup'] == 'Index' for pid in priced)
    top = products.history(priced[0])[0]
    assert top['action'] == 'updated' and 'renamed from Passive to Index' in top['note']
    assert top['actor'] == 'alice'
    assert products.describeSource()['revision'] == catalogue + 1
    with pytest.raises(KeyError):
        fees.managementFee('RDR', 15e6, 'PMG Target', 'Passive')


# ------------------------------------------------------------------ tiers ---

def test_a_tier_is_split_and_merged_and_its_range_follows_its_rates(store):
    original = fees.effectiveRate('CASP', 26e6, 'PMG Target')
    card = _card()
    tiers = card['tiers']
    t2 = tiers[1]
    tiers.insert(2, {'id': 'new-1', 'min': 15000000, 'max': t2['max']})
    t2['max'] = 15000000
    for schedule, byRow in card['rates'].items():
        for group, byTier in byRow.items():
            byTier['new-1'] = copy.deepcopy(byTier['T2'])
    saved = feeCard.save(card, 'Split the second tier', user='alice', base=1)
    assert saved['changes'][0].startswith('Tiers: Under 10m · 10m – 25m')
    assert '10m – 15m · 15m – 25m' in saved['changes'][0]
    assert [t['id'] for t in fees.tiers()] == ['T1', 'T2', 'T3', 'T4', 'T5', 'T6']
    assert fees.tierFor(12e6)['label'] == '10m – 15m' and fees.tierFor(20e6)['label'] == '15m – 25m'
    assert fees.tierFor(20e6)['id'] == 'T3'
    # a tier split with its rates copied blends a mandate exactly as before
    assert fees.effectiveRate('CASP', 26e6, 'PMG Target') == pytest.approx(original)
    # and merged back: five tiers, the second as it was
    merged = _card()
    merged['tiers'][1]['max'] = merged['tiers'][2]['max']
    del merged['tiers'][2]
    for byRow in merged['rates'].values():
        for byTier in byRow.values():
            del byTier['T3']
    feeCard.save(merged, 'Back to five', user='alice', base=2)
    assert [t['label'] for t in fees.tiers()][:2] == ['Under 10m', '10m – 25m']
    assert len(fees.tiers()) == 5
    assert fees.effectiveRate('CASP', 26e6, 'PMG Target') == pytest.approx(original)


@pytest.mark.parametrize('shape, words', [
    (lambda t: t[0].update({'min': 1000}), 'start at 0'),
    (lambda t: t[-1].update({'max': 900000000}), 'no upper edge'),
    (lambda t: t[1].update({'max': 20000000}), 'edge to edge'),
    (lambda t: t[1].update({'max': 10000000}), 'no room'),
])
def test_tiers_that_leave_an_account_size_without_a_tier_are_refused(store, shape, words):
    card = _card()
    shape(card['tiers'])
    with pytest.raises(ValidationError) as caught:
        feeCard.save(card, 'n', user='alice', base=1)
    assert caught.value.field == 'tiers' and words in caught.value.message


# ---------------------------------------------------------------- restore ---

def test_an_earlier_version_comes_back_as_a_new_one_unless_a_product_needs_what_it_lacks(store):
    card = _card()
    card['rates']['CASP']['']['T1']['Management Ceiling'] += 0.05
    feeCard.save(card, 'up', user='alice', base=1)
    back = feeCard.revert(1, 'Undo the change', user='bob', base=2)
    assert back['revision'] == 3 and feeCard.current()['cells'] == \
        {k: v for k, v in fees._readDelivery(fees.ratesPath())['cells'].items()}
    top = feeCard.history()[0]
    assert top['action'] == 'reverted' and top['restoredFrom'] == 1 and top['placeholder']
    assert fees.deliveryInfo()['placeholder'] is fees.PLACEHOLDER
    feeCard.save(_withGroup(_card(), 'Thematic'), 'group', user='alice', base=3)
    products.create(_product('probe-thematic', 'Thematic'), user='alice')
    with pytest.raises(ValidationError) as caught:
        feeCard.revert(1, 'too far', user='bob', base=4)
    assert 'Thematic' in caught.value.message and 'prices 1 product' in caught.value.message


# ------------------------------------------------------- import / export ---

def test_a_file_is_previewed_rate_by_rate_and_put_in_force_with_its_name(store, tmp_path):
    text = feeCard.exportCsv()
    same = feeCard.importPreview('card.csv', base64.b64encode(text.encode()).decode())
    assert same['same'] and same['rates'] == 0 and same['rows'] == 180
    lines = text.splitlines()
    lines = [l if not l.startswith('RDR,Alternatives,T3,') or ',PMG,Target,' not in l
             else l.rsplit(',', 1)[0] + ',0.8' for l in lines]
    content = base64.b64encode('\n'.join(lines).encode()).decode()
    preview = feeCard.importPreview('october.csv', content)
    assert preview['rates'] == 1 and not preview['same'] and preview['blocked'] == []
    assert preview['changes'][0].startswith('RDR · Alternatives · 25m – 50m · PMG Target')
    with pytest.raises(ValidationError) as caught:
        feeCard.importFile('october.csv', content, '', user='alice')
    assert caught.value.field == 'note'
    done = feeCard.importFile('october.csv', content, 'October card', user='alice', base=1)
    assert done['revision'] == 2
    top = feeCard.history()[0]
    assert top['action'] == 'imported' and top['note'] == 'October card (from october.csv)'
    assert fees.managementFee('RDR', 30e6, 'PMG Target', 'Alternatives') == 0.8
    # a file that would take away a group products are priced by is refused
    dropped = [l for l in text.splitlines() if ',Passive,' not in l]
    content = base64.b64encode('\n'.join(dropped).encode()).decode()
    preview = feeCard.importPreview('nopassive.csv', content)
    assert preview['blocked'] and preview['blocked'][0]['feeGroup'] == 'Passive'
    with pytest.raises(ValidationError) as caught:
        feeCard.importFile('nopassive.csv', content, 'n', user='alice')
    assert caught.value.field == 'feeGroups'


# ----------------------------------------------------- what a change reaches ---

def test_a_custom_rate_is_checked_again_against_the_card_in_force(store):
    """A PWA's custom rate is held between the card's bounds for the mandate
    (D96). Lowering the card can leave one outside, so the export checks it
    again as it was checked when it was entered."""
    mandate = MandateInput(15e6, 26e6, 'M. Aldridge — Zurich')
    low, high = fees.customBounds('CASP', 15e6, 26e6)
    rules.validateCustomFees({'CASP': round(high - 0.01, 4)}, mandate)
    card = _card()
    for tier in card['rates']['CASP']['']:
        card['rates']['CASP'][''][tier]['Management Ceiling'] = round(
            card['rates']['CASP'][''][tier]['Management Ceiling'] - 0.1, 4)
    feeCard.save(card, 'Ceilings down', user='alice', base=1)
    with pytest.raises(ValidationError) as caught:
        rules.validateCustomFees({'CASP': round(high - 0.01, 4)}, mandate)
    assert caught.value.field == 'customFees' and 'outside' in caught.value.message
    source = inspect.getsource(dashboardRouter._assembleExport)
    assert 'validateCustomFees(implementation[\'customFees\'], mandate)' in source


def test_a_delivered_proposal_records_the_catalogue_and_card_that_priced_it(store):
    import test_proposal_register as reg
    entry, _, _ = reg._deliver()
    kept = proposalRegister.getProposal(entry['proposalId'])
    assert kept['catalogueRevision'] == 1 and kept['feeCardRevision'] == 1
    card = _card()
    card['rates']['RDR']['Passive']['T1']['PMG Ceiling'] += 0.01
    feeCard.save(card, 'later', user='alice', base=1)
    again, _, _ = reg._deliver(scenarioId='sc_later')
    assert proposalRegister.getProposal(again['proposalId'])['feeCardRevision'] == 2
    assert proposalRegister.getProposal(entry['proposalId'])['feeCardRevision'] == 1
    unpriced, _, _ = reg._deliver(scenarioId='sc_unpriced', includeFees=False)
    assert proposalRegister.getProposal(unpriced['proposalId'])['feeCardRevision'] is None
    row = dict(zip(proposalRegister.EXPORT_COLUMNS, proposalRegister.exportRows()[-1]))
    assert row['CatalogueVersion'] == 1 and row['FeeCardVersion'] == 1


# ---------------------------------------------------------- the console's API ---

def test_the_endpoints_read_save_restore_and_export_as_the_signed_in_admin(store, monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    alice = _caller('alice')
    body = dashboardRouter.getRepositoryFeeCard(caller=alice)
    assert body['revision'] == 1 and body['usage']['Passive'] and body['rateMax'] == 10.0
    assert [s['id'] for s in body['schedules']] == fees.SCHEDULES
    card = copy.deepcopy(body['card'])
    card['rates']['RDR']['Passive']['T1']['PMG Ceiling'] += 0.02
    refused = dashboardRouter.saveRepositoryFeeCard({'card': card, 'note': '', 'base': 1}, caller=alice)
    assert refused.status_code == 422 and json.loads(refused.body)['field'] == 'note'
    stale = dashboardRouter.saveRepositoryFeeCard({'card': card, 'note': 'n', 'base': 9}, caller=alice)
    assert stale.status_code == 409
    saved = dashboardRouter.saveRepositoryFeeCard({'card': card, 'note': 'n', 'base': 1}, caller=alice)
    assert saved['saved']['revision'] == 2 and saved['fees']['lastEditedBy'] == 'alice'
    assert dashboardRouter.getRepositoryFeeHistory(caller=alice)['history'][0]['revision'] == 2
    back = dashboardRouter.revertRepositoryFeeCard({'revision': '1', 'note': 'undo', 'base': 2}, caller=alice)
    assert back['saved']['revision'] == 3
    text = dashboardRouter.exportFeeCard(caller=alice).body.decode('utf-8')
    assert text.splitlines()[0] == 'schedule,feeGroup,tier,tierMin,tierMax,source,point,rate'
    assert len(text.strip().splitlines()) == 181
