"""D155: the overlay rules, held in the repository and resolved in order.

The tilt and the premium were code in rules.py. They are now an ordered list
of rules the desk keeps in the console's Overlay Funding tab - a house list
for every implementation type, optionally a type's own - resolved top to
bottom, each rule reading the allocation as the rules above it left it. The
seeded house list reproduces the code it replaced to the last bit; moving a
rule changes the figures the way the order says; a list under which a
portfolio holds a rule's sources but cannot fund it is refused; and the page
resolves the list exactly as the files do.
"""

import json
import os
import shutil
import sqlite3
import subprocess

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import (overlayRules, proposalRegister, rules, sleeveRepo,
                                           universe)
from cyrus_pmg.pmgService.scenario.types import PortfolioKey, ValidationError
from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows

HERE = os.path.dirname(os.path.abspath(__file__))
IG, OF, EQ, HF = ('Investment Grade Fixed Income', 'Other Fixed Income', 'Public Equity',
                  'Hedge Funds')
AAS, HFI = 'Asset Allocation Strategies', 'Hybrid Fixed Income'
ESG = 'PMG ESG'
MODERATE_FULL = PortfolioKey('USD', 'Moderate', 'Full', False)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A repository file of this test's own, so a saved list never reaches
    another test's figures."""
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    return tmp_path


def _caller(kerberos='alice'):
    from cyrus_pmg.pmgService.core import accessControl
    return accessControl.UserData(kerberos, accessControl._rolesFor(kerberos))


def _house():
    return overlayRules.current()['rules']


def _weights(categories):
    return {c['name']: c['weightPct'] for c in categories}


def _gold(**changes):
    rule = {'id': 'gold', 'name': 'Gold Overlay', 'into': 'Gold', 'row': 'Gold', 'place': 'end',
            'size': 2.0, 'basis': 'portfolio',
            'sources': [{'category': IG, 'weightPct': 50.0}, {'category': EQ, 'weightPct': 50.0}],
            'toggle': None, 'currencies': []}
    rule.update(changes)
    return rule


# ---------------------------------------------- the code it replaced ---
# rules.tiltedCategories and rules.volPremiumCategories as they stood before
# D155, verbatim but for the names, so the seeded list can be held to them.

def _oldTilt(categories, on):
    result = []
    for category in categories or []:
        copy = dict(category)
        copy['assets'] = [dict(a) for a in category.get('assets') or []]
        result.append(copy)
    fundable = False
    for category in result:
        if category.get('name') == IG:
            fundable = float(category.get('weightPct') or 0.0) >= 8.0
            break
    if not on or not fundable:
        return result
    for category in result:
        if category['name'] != IG:
            continue
        before = float(category['weightPct'])
        after = before - 8.0
        category['weightPct'] = after
        share = (after / before) if before else 0.0
        for asset in category['assets']:
            asset['weightPct'] = float(asset['weightPct']) * share
        break
    result.append({'name': AAS, 'weightPct': 8.0,
                   'assets': [{'reportingName': AAS, 'weightPct': 8.0}]})
    return result


def _oldPremium(categories, on, currency):
    result = []
    for category in categories or []:
        copy = dict(category)
        copy['assets'] = [dict(a) for a in category.get('assets') or []]
        result.append(copy)
    if not on or currency not in ('USD', 'GBP'):
        return result
    for index, category in enumerate(result):
        if category['name'] != IG:
            continue
        before = float(category['weightPct'])
        if before <= 0:
            break
        take = before * 0.075
        after = before - take
        category['weightPct'] = after
        share = after / before
        for asset in category['assets']:
            asset['weightPct'] = float(asset['weightPct']) * share
        result.insert(index + 1, {'name': HFI, 'weightPct': take,
                                  'assets': [{'reportingName': HFI, 'weightPct': take}]})
        break
    return result


# ------------------------------------------------------------ the store ---

def test_the_house_list_is_seeded_with_the_two_overlays_as_the_code_had_them(store):
    entry = overlayRules.current()
    assert entry['scope'] == '*' and entry['revision'] == 1 and not entry['inherited']
    assert entry['rules'] == rules.baselineOverlays()
    assert [r['id'] for r in entry['rules']] == ['ttf', 'svp']
    ttf, svp = entry['rules']
    assert (ttf['size'], ttf['basis'], ttf['place'], ttf['toggle']) == (8.0, 'portfolio', 'end', 'tacticalTilt')
    assert (svp['size'], svp['basis'], svp['place'], svp['toggle']) == (7.5, 'sources', IG, 'volPremium')
    assert svp['currencies'] == ['USD', 'GBP'] and ttf['currencies'] == []
    (first,) = overlayRules.history('*')
    assert first['action'] == 'baseline' and first['actor'] == 'system'
    assert first['change'] == 'Seeded from the code: 1st Tactical Tilts, 2nd Strategic Volatility Premium'
    # read twice, seeded once
    overlayRules.describe()
    assert len(overlayRules.history('*')) == 1
    assert overlayRules.current(ESG)['inherited']


def test_a_schema_5_file_is_brought_to_6_and_seeded(store):
    path = store / 'sleeves.db'
    conn = sleeveRepo.connection()
    conn.close()
    raw = sqlite3.connect(str(path))
    raw.execute("DELETE FROM policies WHERE kind = 'overlay'")
    raw.execute("DELETE FROM policyHistory WHERE kind = 'overlay'")
    raw.execute("UPDATE meta SET value = '5' WHERE key = 'schemaVersion'")
    raw.commit()
    raw.close()
    assert overlayRules.current()['rules'] == rules.baselineOverlays()
    raw = sqlite3.connect(str(path))
    version = raw.execute("SELECT value FROM meta WHERE key = 'schemaVersion'").fetchone()[0]
    raw.close()
    assert int(version) == sleeveRepo.SCHEMA_VERSION == 6


# ------------------------------------------------------------ the engine ---

def test_the_seeded_list_reproduces_the_code_it_replaced_to_the_last_bit(store):
    """Every strategic portfolio, both switches, a currency that may hold the
    premium and one that may not: the same names, in the same order, with
    the same floats - not approximately, exactly."""
    house = _house()
    checked = 0
    for key in universe.keys():
        payload = universe.categoryRows(key)
        for tilt in (False, True):
            for premium in (False, True):
                for currency in ('USD', 'EUR'):
                    old = _oldPremium(_oldTilt(payload, tilt), premium, currency)
                    new = rules.resolveOverlays(payload, house, {'tacticalTilt': tilt,
                                                                 'volPremium': premium},
                                                currency)['categories']
                    assert old == new, (key, tilt, premium, currency)
                    checked += 1
    assert checked == 172 * 8


def test_the_wrappers_kept_for_the_baseline_agree_with_the_engine(store):
    payload = universe.categoryRows(MODERATE_FULL)
    assert rules.tiltedCategories(payload, True) == _oldTilt(payload, True)
    assert rules.volPremiumCategories(payload, True, 'USD') == _oldPremium(payload, True, 'USD')
    assert rules.canFundTacticalTilt(payload)
    allEquity = next(k for k in universe.keys() if k.isAllEquity)
    assert not rules.canFundTacticalTilt(universe.categoryRows(allEquity))


def test_moving_the_premium_above_the_tilt_changes_the_figures_as_the_order_says(store):
    """USD Moderate Full holds 34% IGFI. Tilt first: 34 - 8 = 26, the premium
    7.5% of that, 1.95, leaving 24.05. Premium first: 7.5% of 34 is 2.55,
    leaving 31.45, and the tilt's 8 then leaves 23.45."""
    payload = universe.categoryRows(MODERATE_FULL)
    on = {'tacticalTilt': True, 'volPremium': True}
    ttf, svp = _house()
    tiltFirst = _weights(rules.resolveOverlays(payload, [ttf, svp], on, 'USD')['categories'])
    premiumFirst = _weights(rules.resolveOverlays(payload, [svp, ttf], on, 'USD')['categories'])
    assert tiltFirst[IG] == pytest.approx(24.05) and tiltFirst[HFI] == pytest.approx(1.95)
    assert premiumFirst[IG] == pytest.approx(23.45) and premiumFirst[HFI] == pytest.approx(2.55)
    assert tiltFirst[AAS] == premiumFirst[AAS] == 8.0
    assert sum(premiumFirst.values()) == pytest.approx(100.0)
    names = [c['name'] for c in rules.resolveOverlays(payload, [svp, ttf], on, 'USD')['categories']]
    assert names.index(HFI) == names.index(IG) + 1 and names[-1] == AAS


def test_a_third_rule_reads_what_the_two_above_left(store):
    payload = universe.categoryRows(MODERATE_FULL)
    on = {'tacticalTilt': True, 'volPremium': True}
    ttf, svp = _house()
    result = rules.resolveOverlays(payload, [ttf, svp, _gold()], on, 'USD')
    weights = _weights(result['categories'])
    assert weights['Gold'] == pytest.approx(2.0)
    assert weights[IG] == pytest.approx(24.05 - 1.0)
    assert weights[EQ] == pytest.approx(38.0 - 1.0)
    assert weights[HFI] == pytest.approx(1.95)
    assert [s['status'] for s in result['steps']] == ['applied'] * 3
    assert [t['category'] for t in result['steps'][2]['takes']] == [IG, EQ]
    # first, it shrinks what the premium reads: 7.5% of (34 - 8 - 1)
    first = _weights(rules.resolveOverlays(payload, [_gold(), ttf, svp], on, 'USD')['categories'])
    assert first[HFI] == pytest.approx(0.075 * 25.0)
    assert sum(first.values()) == pytest.approx(100.0)


def test_a_source_basis_rule_takes_its_share_of_each_source_as_it_stands(store):
    payload = universe.categoryRows(MODERATE_FULL)
    rule = _gold(basis='sources', size=10.0)
    result = rules.resolveOverlays(payload, [rule], {}, 'USD')
    takes = {t['category']: t['take'] for t in result['steps'][0]['takes']}
    assert takes[IG] == pytest.approx(34.0 * 0.10 * 0.5)
    assert takes[EQ] == pytest.approx(38.0 * 0.10 * 0.5)


def test_an_unselected_rule_is_skipped_and_the_rest_resolve_as_normal(store):
    payload = universe.categoryRows(MODERATE_FULL)
    result = rules.resolveOverlays(payload, _house(), {'tacticalTilt': False, 'volPremium': True},
                                   'USD')
    assert [s['status'] for s in result['steps']] == ['off', 'applied']
    assert _weights(result['categories'])[HFI] == pytest.approx(2.55)
    eur = rules.resolveOverlays(payload, _house(), {'tacticalTilt': True, 'volPremium': True}, 'EUR')
    assert [s['status'] for s in eur['steps']] == ['applied', 'currency']
    allEquity = next(k for k in universe.keys() if k.isAllEquity)
    none = rules.resolveOverlays(universe.categoryRows(allEquity), _house(),
                                 {'tacticalTilt': True, 'volPremium': True}, 'USD')
    assert [s['status'] for s in none['steps']] == ['unfundable', 'unfundable']
    always = rules.resolveOverlays(payload, [_gold()], {}, 'USD')
    assert always['steps'][0]['status'] == 'applied', 'a rule with no switch always applies'


def test_the_resolver_never_mutates_its_input(store):
    payload = universe.categoryRows(MODERATE_FULL)
    before = json.dumps(payload, sort_keys=True)
    rules.resolveOverlays(payload, _house() + [_gold()], {'tacticalTilt': True, 'volPremium': True},
                          'USD')
    assert json.dumps(payload, sort_keys=True) == before


# ------------------------------------------------------- what may be saved ---

@pytest.mark.parametrize('change, words', [
    ({'into': IG}, 'is a strategic category'),
    ({'into': 'Private Equity & Other Private Assets'}, 'is a strategic category'),
    ({'into': HFI}, 'Two rules go into'),
    ({'name': 'Tactical Tilts'}, 'Two rules are called'),
    ({'size': 0}, 'above zero'),
    ({'size': 101}, 'over 100%'),
    ({'size': 1.23456}, 'decimal places'),
    ({'basis': 'nav'}, 'The basis of Gold Overlay must be one of'),
    ({'sources': []}, 'at least one category'),
    ({'sources': [{'category': IG, 'weightPct': 60}, {'category': EQ, 'weightPct': 30}]},
     'add up to 90.0000%'),
    ({'sources': [{'category': IG, 'weightPct': 50}, {'category': IG, 'weightPct': 50}]},
     'names Investment Grade Fixed Income twice'),
    ({'sources': [{'category': HFI, 'weightPct': 100}]}, 'fund it from a strategic category'),
    ({'toggle': 'tacticalTilt'}, 'same switch'),
    ({'toggle': 'somethingElse'}, 'not one the proposal has'),
    ({'currencies': ['XYZ']}, 'unknown currency'),
    ({'place': 'Nowhere'}, 'it can only sit at the end of the table'),
    ({'into': 'public equity'}, 'is a strategic category'),
    ({'into': 'hybrid fixed income'}, 'Two rules go into'),
    ({'size': True}, 'Give the size of Gold Overlay as a number'),
    ({'size': float('nan')}, 'as a number'),
    ({'name': 5}, 'Give the name of rule 3 as text'),
    ({'id': 'ttf'}, "Two rules carry the key 'ttf'"),
])
def test_a_malformed_rule_is_refused_naming_it(store, change, words):
    with pytest.raises(ValidationError) as caught:
        overlayRules.normalise(_house() + [_gold(**change)])
    assert caught.value.field == 'rules'
    assert words in str(caught.value), str(caught.value)


def test_normalise_fills_what_a_new_rule_leaves_out(store):
    (cleaned,) = overlayRules.normalise([{'name': 'Gold Overlay!', 'into': 'Gold', 'size': '2',
                                         'basis': 'portfolio',
                                         'sources': [{'category': EQ, 'weightPct': '100'}]}])
    assert cleaned['id'] == 'gold-overlay' and cleaned['row'] == 'Gold'
    assert cleaned['place'] == EQ, 'a new rule sits after its first source'
    assert cleaned['toggle'] is None and cleaned['currencies'] == []
    assert cleaned['size'] == 2.0 and cleaned['sources'] == [{'category': EQ, 'weightPct': 100.0}]


def test_a_list_a_held_source_cannot_fund_is_refused_naming_the_worst_case(store):
    """A 30% tilt: every portfolio holding IGFI but less than 30% of it would
    go below zero. Refused, naming the rule, the category and the worst
    portfolio; an all-equity portfolio, holding no IGFI, is not the reason."""
    ttf, svp = _house()
    greedy = dict(ttf, size=30.0)
    with pytest.raises(ValidationError) as caught:
        overlayRules.save('*', [greedy, svp], 'too much', 'alice')
    message = str(caught.value)
    assert message.startswith('Tactical Tilts (1st) would take Investment Grade Fixed Income to -')
    worst = overlayRules.shortfalls([greedy, svp], sleeveRepo.VARIANTS)[0]
    assert worst['category'] == IG and worst['after'] < 0 and worst['portfolio'] in message
    assert overlayRules.current()['revision'] == 1, 'nothing written'


def test_a_rule_a_portfolio_does_not_hold_the_sources_of_is_not_a_refusal(store):
    """Hedge Funds are held by half the portfolios: a rule funded from them is
    not offered to the others, which is no reason to refuse it."""
    rule = _gold(sources=[{'category': HF, 'weightPct': 100.0}], size=1.0)
    saved = overlayRules.save('*', _house() + [rule], 'a hedge fund overlay', 'alice')
    assert [r['id'] for r in saved['entry']['rules']] == ['ttf', 'svp', 'gold']


def test_a_note_is_required_and_a_no_op_is_refused(store):
    with pytest.raises(ValidationError) as caught:
        overlayRules.save('*', list(reversed(_house())), '  ', 'alice')
    assert caught.value.field == 'note'
    with pytest.raises(ValidationError):
        overlayRules.save('*', _house(), 'the same', 'alice')
    with pytest.raises(ValidationError):
        overlayRules.save('Not A Type', _house(), 'n', 'alice')


# --------------------------------------------- order, types, history ---

def test_premium_first_is_refused_for_the_house_list_and_why(store):
    """USD Higher Risk Full holds exactly 8% IGFI: the tilt takes all of it
    when it comes first, but after the premium's 7.5% only 7.4% is left.
    Every Multi-Asset portfolio holding IGFI must be able to fund the tilt,
    so the house list cannot put the premium first - and says which
    portfolio stops it. A type without that portfolio can (below)."""
    ttf, svp = _house()
    with pytest.raises(ValidationError) as caught:
        overlayRules.save('*', [svp, ttf], 'premium first', 'alice')
    assert 'Tactical Tilts (2nd) would take Investment Grade Fixed Income to -0.60% in USD Higher ' \
           'Risk Full' in str(caught.value)


def test_reordering_is_recorded_as_such_and_can_be_restored(store):
    ttf, svp = _house()
    overlayRules.save(ESG, [ttf, svp], 'ESG keeps its own list', 'alice')
    overlayRules.save(ESG, [svp, ttf], 'premium first', 'alice')
    history = overlayRules.history(ESG)
    assert history[0]['change'] == 'Order changed: Strategic Volatility Premium moved above Tactical Tilts'
    assert history[0]['note'] == 'premium first' and history[0]['actor'] == 'alice'
    restored = overlayRules.revert(ESG, 1, 'back', 'bob')
    assert [r['id'] for r in restored['entry']['rules']] == ['ttf', 'svp']
    assert restored['entry']['revision'] == 3
    assert overlayRules.history(ESG)[0]['change'] == ('Restored r1: Order changed: Tactical Tilts '
        'moved above Strategic Volatility Premium')
    with pytest.raises(ValidationError):
        overlayRules.revert(ESG, 3, 'again', 'bob')


EQUITY_ONLY = [{'category': EQ, 'weightPct': 100.0}]


def test_a_rule_a_held_source_could_not_fund_after_the_rules_above_is_refused(store):
    """Gold from IGFI and equity, third: USD Higher Risk Core holds a little
    IGFI and the tilt and premium leave too little of it."""
    ttf, svp = _house()
    with pytest.raises(ValidationError) as caught:
        overlayRules.save('*', [ttf, svp, _gold()], 'gold', 'alice')
    assert 'Gold Overlay (3rd) would take Investment Grade Fixed Income' in str(caught.value)
    saved = overlayRules.save('*', [ttf, svp, _gold(sources=EQUITY_ONLY)], 'gold from equity', 'alice')
    assert [r['id'] for r in saved['entry']['rules']] == ['ttf', 'svp', 'gold']


def test_adding_removing_and_editing_are_named(store):
    ttf, svp = _house()
    gold = _gold(sources=EQUITY_ONLY)
    overlayRules.save('*', [ttf, svp, gold], 'gold', 'alice')
    assert overlayRules.history('*')[0]['change'] == 'Added Gold Overlay (3rd)'
    overlayRules.save('*', [ttf, dict(svp, size=5.0), gold], 'smaller premium', 'alice')
    assert overlayRules.history('*')[0]['change'] == 'Strategic Volatility Premium: size 7.5% → 5%'
    overlayRules.save('*', [ttf, dict(svp, size=5.0)], 'no gold', 'alice')
    assert overlayRules.history('*')[0]['change'] == 'Removed Gold Overlay'


def test_a_type_inherits_until_edited_and_goes_back_on_removal(store):
    ttf, svp = _house()
    own = overlayRules.save(ESG, [svp, ttf], 'ESG reads the premium first', 'alice')
    assert own['entry']['scope'] == ESG and not overlayRules.current(ESG)['inherited']
    assert overlayRules.current('US Onshore')['inherited']
    assert overlayRules.current()['rules'] == [ttf, svp], 'the house list is untouched'
    assert overlayRules.history(ESG)[0]['change'] == ('Own list: Order changed: Strategic Volatility '
        'Premium moved above Tactical Tilts')
    back = overlayRules.removeOverride(ESG, 'back to the house', 'alice')
    assert back['inherited'] and overlayRules.current(ESG)['inherited']
    assert overlayRules.history(ESG)[0]['change'] == 'Back on the house list'
    with pytest.raises(ValidationError):
        overlayRules.removeOverride(ESG, 'again', 'alice')
    with pytest.raises(ValidationError):
        overlayRules.removeOverride('*', 'the house', 'alice')


def test_a_rule_with_no_sleeve_is_saved_with_a_warning_and_attaches_automatically(store):
    saved = overlayRules.save('*', _house() + [_gold(sources=EQUITY_ONLY)], 'gold', 'alice')
    assert any(w.startswith('No sleeve in Gold yet for ') for w in saved['warnings'])
    assert 'Gold' in rules.autoSleeveCategories()
    assert 'Gold' in sleeveRepo.fixedCategories()
    categories = sleeveRepo.categories()
    assert categories[-1] == 'Gold' and categories.index(HFI) == categories.index(IG) + 1
    assert overlayRules.describe()['warnings']['*']


# ------------------------------------------------------- where it lands ---

def _book():
    from test_initial_allocation import _book as book, _sleeves
    _, result = book('USD', 'USD|Moderate|Full|0')
    return result, _sleeves(result)


def _model(result, sleeves, variant='PMG Multi-Asset Portfolio', tilt=True, premium=True):
    return buildImplementationRows(result, sleeves, rules.autoSleeveCategories(), 50e6, variant,
                                   tilt, 'CASP', 'PMG Target', 50e6, premium, 'USD')


def test_the_model_follows_the_types_list_and_records_it(store):
    result, sleeves = _book()
    ttf, svp = _house()
    overlayRules.save(ESG, [svp, ttf], 'premium first', 'alice')
    house = _model(result, sleeves)
    esg = _model(result, sleeves, ESG)
    weight = lambda m, name: next(g['weightPct'] for g in m['groups'] if g['category'] == name)
    assert weight(house, HFI) == pytest.approx(1.95) and weight(house, IG) == pytest.approx(24.05)
    assert weight(esg, HFI) == pytest.approx(2.55) and weight(esg, IG) == pytest.approx(23.45)
    assert house['overlays']['scope'] == '*' and house['overlays']['revision'] == 1
    assert esg['overlays']['scope'] == ESG
    assert [(r['id'], r['status']) for r in esg['overlays']['rules']] == [('svp', 'applied'),
                                                                         ('ttf', 'applied')]
    off = _model(result, sleeves, tilt=False)
    assert [r['status'] for r in off['overlays']['rules']] == ['off', 'applied']
    assert AAS not in [g['category'] for g in off['groups']]


def test_the_register_records_the_rules_a_proposal_was_built_with(store, monkeypatch):
    import test_proposal_register as reg
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(store / 'proposals.db'))
    entry, _, _ = reg._deliver()
    kept = proposalRegister.getProposal(entry['proposalId'])
    assert kept['overlays']['scope'] == '*' and kept['overlays']['revision'] == 1
    assert [r['id'] for r in kept['overlays']['rules']] == ['ttf', 'svp']
    row = dict(zip(proposalRegister.EXPORT_COLUMNS, proposalRegister.exportRows()[0]))
    assert row['Overlays'].startswith('r1 (house) 1 Tactical Tilts applied 8%')
    # a later change leaves the delivered record as it was
    overlayRules.save('*', _house() + [_gold(sources=EQUITY_ONLY)], 'later', 'alice')
    assert proposalRegister.getProposal(entry['proposalId'])['overlays']['revision'] == 1
    conn = sqlite3.connect(str(store / 'proposals.db'))
    version = conn.execute("SELECT value FROM meta WHERE key = 'schemaVersion'").fetchone()[0]
    conn.close()
    assert int(version) == proposalRegister.SCHEMA_VERSION == 5


def test_the_schema_serves_the_list_in_force_for_the_type(store):
    ttf, svp = _house()
    overlayRules.save(ESG, [svp, ttf], 'own', 'alice')
    served = rules._servedOverlays(ESG)
    assert served['scope'] == ESG and not served['inherited']
    assert [r['id'] for r in served['rules']] == ['svp', 'ttf']
    house = rules._servedOverlays('US Onshore')
    assert house['scope'] == '*' and house['inherited']


# ------------------------------------------------------------ the API ---

def test_the_endpoints_read_write_remove_and_restore(store, monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    alice = _caller('alice')
    body = dashboardRouter.getOverlayRules(caller=alice)
    assert body['house']['revision'] == 1 and body['maxRules'] == 8 and body['maxSources'] == 4
    assert len(body['portfolios']) == 172 and body['samples'][ESG]
    ttf, svp = body['house']['rules']
    refused = dashboardRouter.saveOverlayRules(
        {'scope': '*', 'rules': [dict(ttf, size=30.0), svp], 'note': 'n'}, caller=alice)
    assert refused.status_code == 422 and json.loads(refused.body)['field'] == 'rules'
    saved = dashboardRouter.saveOverlayRules(
        {'scope': ESG, 'rules': [svp, ttf], 'note': 'own'}, caller=alice)
    assert saved['list']['updatedBy'] == 'alice' and ESG in saved['overlays']['overrides']
    history = dashboardRouter.getOverlayHistory(scope=ESG, caller=alice)
    assert [h['action'] for h in history['history']] == ['updated']
    removed = dashboardRouter.removeOverlayOverride({'scope': ESG, 'note': 'gone'}, caller=alice)
    assert ESG not in removed['overlays']['overrides']
    restored = dashboardRouter.revertOverlayRules({'scope': ESG, 'revision': 1, 'note': 'again'},
                                                  caller=alice)
    assert restored['list']['revision'] == 3 and restored['list']['scope'] == ESG
    bad = dashboardRouter.revertOverlayRules({'scope': ESG, 'revision': 'x', 'note': 'n'},
                                             caller=alice)
    assert bad.status_code == 422


def test_the_five_routes_are_mounted_and_admin_only():
    paths = {(r.path, tuple(sorted(r.methods))) for r in dashboardRouter.router.routes}
    for path, method in (('/scenario/repository/overlays', 'GET'),
                         ('/scenario/repository/overlays', 'PUT'),
                         ('/scenario/repository/overlays/remove', 'POST'),
                         ('/scenario/repository/overlays/history', 'GET'),
                         ('/scenario/repository/overlays/revert', 'POST')):
        assert (path, (method,)) in paths, (path, method)
    from cyrus_pmg.pmgService.core.accessControl import requireAdmin
    for route in dashboardRouter.router.routes:
        if route.path.startswith('/scenario/repository/overlays'):
            assert requireAdmin in [d.call for d in route.dependant.dependencies], route.path


# ------------------------------------------- the page agrees with the files ---

def _jsResolver():
    with open(os.path.join(HERE, '..', '..', 'generator', 'js', 'core.js'), encoding='utf-8') as fh:
        source = fh.read()
    start = source.index('/* ---- overlay resolver (D155)')
    return source[start:source.index('/* ---- end of the overlay resolver ---- */')]


def test_the_page_resolves_every_list_exactly_as_the_files_do(store):
    """The JavaScript resolver, run by node over the house list, the premium
    first, a third rule of each basis in each place, every switch and two
    currencies, across a spread of portfolios: the same names in the same
    order and the same weights, asset by asset, as rules.resolveOverlays."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    ttf, svp = _house()
    lists = [[ttf, svp], [svp, ttf], [ttf, svp, _gold()], [_gold(basis='sources', size=10.0), ttf, svp],
             [svp, _gold(place=IG), ttf], [_gold(sources=[{'category': HF, 'weightPct': 100.0}])]]
    keys = [k for k in universe.keys() if k.currency == 'USD'][:40] \
        + [k for k in universe.keys() if k.isAllEquity][:2]
    cases, expected = [], []
    for key in keys:
        payload = universe.categoryRows(key)
        for ruleList in lists:
            for selections in ({'tacticalTilt': True, 'volPremium': True},
                               {'tacticalTilt': False, 'volPremium': True}):
                for currency in ('USD', 'EUR'):
                    cases.append([payload, ruleList, selections, currency])
                    expected.append(rules.resolveOverlays(payload, ruleList, selections, currency))
    script = _jsResolver() + '\nconst cases = ' + json.dumps(cases) + ';\n' \
        + 'process.stdout.write(JSON.stringify(cases.map(function (c) {\n' \
        + '  return resolveOverlays(c[0], c[1], c[2], c[3]); })));\n'
    out = subprocess.run([node], input=script, capture_output=True, text=True, check=True)
    got = json.loads(out.stdout)
    assert len(got) == len(expected) > 900
    for js, py in zip(got, expected):
        assert [c['name'] for c in js['categories']] == [c['name'] for c in py['categories']]
        assert [s['status'] for s in js['steps']] == [s['status'] for s in py['steps']]
        for a, b in zip(js['categories'], py['categories']):
            # JSON carries a double exactly, so equal here means equal to the bit
            assert a['weightPct'] == b['weightPct']
            assert [x['weightPct'] for x in a['assets']] == [x['weightPct'] for x in b['assets']]


def test_the_page_and_the_files_say_a_rule_in_the_same_words(store):
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    ruleList = _house() + [_gold(), _gold(basis='sources', size=12.3456, into='Gold 2', id='g2')]
    script = _jsResolver() + '\nprocess.stdout.write(JSON.stringify(' + json.dumps(ruleList) \
        + '.map(overlayWords)));'
    got = json.loads(subprocess.run([node, '-e', script], capture_output=True, text=True,
                                    check=True).stdout)
    assert got == [rules.overlayWords(r) for r in ruleList]
    assert got[:2] == ['8% of the portfolio from Investment Grade Fixed Income',
                       '7.5% of Investment Grade Fixed Income as it stands']
    assert got[2] == ('2% of the portfolio, split Investment Grade Fixed Income 50% and '
                      'Public Equity 50%')
    assert got[3] == ('12.3456% of each source as it stands, times its weight: Investment Grade '
                      'Fixed Income 50% and Public Equity 50%')


def test_the_console_has_the_tab_and_never_says_book():
    with open(os.path.join(HERE, '..', '..', 'generator', 'js', 'repository.js'),
              encoding='utf-8') as handle:
        source = handle.read()
    assert "['overlays', 'Overlay Funding', onOverlays, 0]" in source
    assert "['overlays', 'Overlay Funding']" in source
    assert "if (view === 'overlays') return '#overlays';" in source
    assert "'/scenario/repository/overlays'" in source


# ------------------------------------------- the review's findings (D155) ---

def test_a_source_drained_to_zero_by_a_rule_above_is_still_held(store):
    """(A) USD Higher Risk Full holds exactly 8% IGFI and the tilt takes all
    of it. A later rule wanting 6% of the portfolio, 10% of it from IGFI,
    asks 0.6 of a source with nothing left: a shortfall, not a quiet skip -
    and it is named with the switch setting that causes it."""
    ttf, svp = _house()
    x = _gold(id='x', name='X', into='X Cat', size=6.0,
              sources=[{'category': IG, 'weightPct': 10.0}, {'category': OF, 'weightPct': 90.0}])
    with pytest.raises(ValidationError) as caught:
        overlayRules.save('*', [ttf, svp, x], 'repro', 'alice')
    message = str(caught.value)
    assert message.startswith('X (3rd) would take Investment Grade Fixed Income to -0.60% in USD '
                              'Higher Risk Full with Tactical Tilts on')
    case = overlayRules.shortfalls([ttf, svp, x], sleeveRepo.VARIANTS)[2]
    assert case['selection']['tacticalTilt'] is True and case['after'] == pytest.approx(-0.6)
    # what the page would do: the rule is unfundable there, never silently short
    hr = PortfolioKey('USD', 'Higher Risk', 'Full', False)
    on = rules.resolveOverlays(universe.categoryRows(hr), [ttf, svp, x],
                               {'tacticalTilt': True, 'volPremium': True}, 'USD')
    assert [s['status'] for s in on['steps']] == ['applied', 'unfundable', 'unfundable']


def test_every_setting_of_the_switches_is_judged(store, monkeypatch):
    """(A) The check runs each rule under every combination of the proposal's
    switches, so a rule that only fails with a switch off is still caught."""
    seen = []
    real = rules._skipReason

    def spy(rule, selections, currency):
        seen.append(tuple(sorted((selections or {}).items())))
        return real(rule, selections, currency)
    monkeypatch.setattr(rules, '_skipReason', spy)
    overlayRules.shortfalls(_house(), [ESG])
    assert set(seen) == {(('tacticalTilt', a), ('volPremium', b))
                         for a in (True, False) for b in (True, False)}


def test_float_noise_does_not_refuse_a_rule_a_source_exactly_funds(store):
    """(C) GBP/EUR Low Vol Full holds Public Equity as 15.999999999999998: a
    16% rule from it is fundable, takes what is there, and leaves exactly
    zero - not -0.00%, and not a refusal."""
    key = PortfolioKey('EUR', 'LowVol', 'Full', False)
    held = _weights(universe.categoryRows(key))[EQ]
    assert held < 16.0 and held == pytest.approx(16.0)
    z = _gold(id='z', name='Z', into='Z Cat', size=16.0, sources=[{'category': EQ, 'weightPct': 100.0}])
    assert overlayRules.shortfalls([z], ['PMG Multi-Asset Portfolio']) == [None]
    result = rules.resolveOverlays(universe.categoryRows(key), [z], {}, 'EUR')
    weights = _weights(result['categories'])
    assert result['steps'][0]['status'] == 'applied'
    assert weights[EQ] == 0.0 and weights['Z Cat'] == held
    assert rules.overlayShortfall(z, universe.categoryRows(key)) == []


def test_a_new_rules_key_never_takes_one_an_existing_rule_holds(store):
    """(D) A new rule called 'TTF', placed above the rule whose key is 'ttf',
    gets a key of its own; the existing rule keeps 'ttf'."""
    ttf, svp = _house()
    new = _gold(id='', name='TTF', sources=[{'category': EQ, 'weightPct': 100.0}])
    ids = [r['id'] for r in overlayRules.normalise([new, ttf, svp])]
    assert ids == ['ttf-2', 'ttf', 'svp']
    saved = overlayRules.save('*', [new, ttf, svp], 'gold first', 'alice')
    assert overlayRules.history('*')[0]['change'] == 'Added TTF (1st)'
    assert [r['id'] for r in saved['entry']['rules']] == ['ttf-2', 'ttf', 'svp']


def test_a_rule_can_only_sit_after_a_rule_above_it(store):
    """(E) Placed after the category of a rule below it, a rule would land at
    the end of the table instead; refused."""
    a = _gold(id='a', name='A', into='Cat A', place='Cat B', sources=[{'category': EQ, 'weightPct': 100.0}])
    b = _gold(id='b', name='B', into='Cat B', place='end', sources=[{'category': EQ, 'weightPct': 100.0}])
    with pytest.raises(ValidationError) as caught:
        overlayRules.normalise([a, b])
    assert 'after the category of a rule above it' in str(caught.value)
    assert [r['place'] for r in overlayRules.normalise([b, a])] == ['end', 'Cat B']
    with pytest.raises(ValidationError):
        overlayRules.normalise([dict(a, place='Asset Allocation Strategies')])


@pytest.mark.parametrize('scope, note', [(5, 'x'), ('*', 5), (['*'], 'x'), ('*', {'a': 1})])
def test_a_scope_or_note_that_is_not_text_is_a_422_not_a_crash(store, scope, note):
    """(F) In both stores."""
    from cyrus_pmg.pmgService.scenario import fundingSplit
    with pytest.raises(ValidationError):
        overlayRules.save(scope, _house(), note, 'alice')
    with pytest.raises(ValidationError):
        fundingSplit.save(scope, [{'category': IG, 'weightPct': 50}, {'category': EQ, 'weightPct': 50}],
                          note, 'alice')
    with pytest.raises(ValidationError):
        overlayRules.removeOverride(scope, note, 'alice')
    with pytest.raises(ValidationError):
        fundingSplit.removeOverride(scope, note, 'alice')


def test_a_boolean_weight_is_not_a_number(store):
    from cyrus_pmg.pmgService.scenario import fundingSplit
    with pytest.raises(ValidationError):
        fundingSplit.normalise([{'category': IG, 'weightPct': True}])
    with pytest.raises(ValidationError):
        overlayRules.normalise([_gold(sources=[{'category': EQ, 'weightPct': True}])])


def test_a_category_name_differing_only_in_case_is_the_same_category(store):
    """(G) 'asset allocation strategies' is the tilt's category, and an
    overlay going into it alone takes the library's spelling."""
    ttf, svp = _house()
    with pytest.raises(ValidationError) as caught:
        overlayRules.normalise([ttf, svp, _gold(into='asset allocation strategies')])
    assert 'Two rules go into' in str(caught.value)
    (alone,) = overlayRules.normalise([dict(svp, into='asset allocation STRATEGIES', place='end')])
    assert alone['into'] == 'Asset Allocation Strategies'


def test_a_stale_write_is_refused_and_concurrent_saves_take_turns(store, monkeypatch):
    """(B) A write names the list it was made against; when that has moved
    on it is refused as stale (409 from the API). Two saves at once take
    turns under the write lock: two revisions, never a clash."""
    import threading
    import time
    ttf, svp = _house()
    base = {'scope': '*', 'revision': 1}
    overlayRules.save('*', [ttf, dict(svp, size=5.0)], 'first', 'alice', base=base)
    with pytest.raises(overlayRules.StaleError) as caught:
        overlayRules.save('*', [ttf, dict(svp, size=6.0)], 'second', 'bob', base=base)
    assert 'changed by alice' in str(caught.value) and 'r2' in str(caught.value)
    # a type inheriting the house list was made against the house list
    overlayRules.save(ESG, [ttf, svp], 'own', 'bob', base={'scope': '*', 'revision': 2})
    with pytest.raises(overlayRules.StaleError):
        overlayRules.removeOverride(ESG, 'back', 'bob', base={'scope': '*', 'revision': 2})
    overlayRules.removeOverride(ESG, 'back', 'bob', base={'scope': ESG, 'revision': 1})

    real = overlayRules._now

    def slow():
        time.sleep(0.2)
        return real()
    monkeypatch.setattr(overlayRules, '_now', slow)
    results = {}

    def go(name, ruleList):
        try:
            results[name] = overlayRules.save('*', ruleList, name, name)['entry']['revision']
        except Exception as exc:                            # what the review saw: a 500
            results[name] = '{}: {}'.format(type(exc).__name__, exc)
    one = threading.Thread(target=go, args=('alice', [ttf]))
    two = threading.Thread(target=go, args=('bob', [ttf, dict(svp, size=4.0)]))
    one.start(); time.sleep(0.05); two.start(); one.join(); two.join()
    assert sorted(results.values()) == [3, 4], results


def test_the_api_answers_a_stale_write_with_409(store, monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    alice = _caller('alice')
    ttf, svp = _house()
    ok = dashboardRouter.saveOverlayRules({'scope': '*', 'rules': [ttf, dict(svp, size=5.0)],
                                           'note': 'n', 'base': {'scope': '*', 'revision': 1}},
                                          caller=alice)
    assert ok['list']['revision'] == 2
    stale = dashboardRouter.saveOverlayRules({'scope': '*', 'rules': [ttf, dict(svp, size=6.0)],
                                              'note': 'n', 'base': {'scope': '*', 'revision': 1}},
                                             caller=alice)
    assert stale.status_code == 409 and json.loads(stale.body)['field'] == 'base'
    bad = dashboardRouter.saveOverlayRules({'scope': 7, 'rules': [], 'note': 'n'}, caller=alice)
    assert bad.status_code == 422
    restore = dashboardRouter.revertOverlayRules({'scope': '*', 'revision': '1', 'note': 'n',
                                                  'base': {'scope': '*', 'revision': 2}}, caller=alice)
    assert restore['list']['revision'] == 3


def test_premium_first_says_the_move_caused_it(store):
    ttf, svp = _house()
    with pytest.raises(ValidationError) as caught:
        overlayRules.save('*', [svp, ttf], 'premium first', 'alice')
    assert str(caught.value).startswith('Moving Strategic Volatility Premium above Tactical Tilts '
                                        'leaves Tactical Tilts unable to fund itself: Tactical Tilts '
                                        '(2nd) would take Investment Grade Fixed Income to -0.60% in '
                                        'USD Higher Risk Full')
    assert "'s " not in str(caught.value)


def test_the_cache_sees_every_commit(store):
    """(K) The read cache keys on SQLite's file change counter as well as the
    file's stat, and a local write drops it: a write through another
    connection is seen by the next read, whatever the file's mtime says."""
    ttf, svp = _house()
    first = overlayRules._stamp(sleeveRepo.dbPath())
    raw = sqlite3.connect(sleeveRepo.dbPath())
    body = json.dumps([ttf, dict(svp, size=5.0)])
    with raw:
        raw.execute("UPDATE policies SET body = ?, revision = 2 WHERE kind = 'overlay' AND scope = '*'",
                    (body,))
    raw.close()
    second = overlayRules._stamp(sleeveRepo.dbPath())
    assert second[-1] == first[-1] + 1, 'the change counter moved'
    assert overlayRules.current()['rules'][1]['size'] == 5.0
    overlayRules.save('*', [ttf, svp], 'back', 'alice')
    assert overlayRules._readCache is None or overlayRules.current()['rules'][1]['size'] == 7.5
    assert overlayRules.current()['rules'][1]['size'] == 7.5


def test_a_type_is_judged_on_the_portfolios_it_offers(store):
    """(L) PMG ESG forces the real-assets exclusion: the portfolios it is
    judged on are the ones the picker offers it."""
    keys = overlayRules._keysFor([ESG])
    assert keys and all(rules.validateKey(k, ESG) is None for k in keys)
    assert not any(k.allocationType == 'ex-HFs' and not k.excludeRealAssets for k in keys)
    offered = overlayRules.describe()['offered'][ESG]
    assert sorted(offered) == sorted(k.toStr() for k in keys)


def test_an_overlay_category_with_no_sleeve_blocks_the_export(store, tmp_path, monkeypatch):
    """(M, N) A rule into a category the type has no sleeve in: the export is
    refused naming it, and the model the export would build reads the list
    once - the same entry decides the automatic categories."""
    from test_scenario_backend import BASIS, PORT, _sleeveMap
    from cyrus_pmg.pmgService.scenario import scenarioStore, sleeves
    from cyrus_pmg.pmgService.scenario.types import MandateInput
    monkeypatch.setenv('SCENARIO_STORE_DIR', str(tmp_path / 'store'))
    overlayRules.save('*', _house() + [_gold(sources=[{'category': EQ, 'weightPct': 100.0}])],
                      'gold', 'alice')
    state = scenarioStore.createScenario(
        MandateInput(topAccountSize=1e9, mandateSize=1e9, primaryPwa='A. Castellanos — Madrid'),
        BASIS, createdBy='alice')
    result = PORT.resolve_portfolio(BASIS, MODERATE_FULL)
    scenarioStore.updateScenario(state['id'], variant=sleeves.VARIANTS[0])
    scenarioStore.recordColumn(state['id'], MODERATE_FULL, 'base')
    scenarioStore.updateScenario(state['id'], sleeves=_sleeveMap(result['categories'], sleeves.VARIANTS[0]))
    calls = []
    real = overlayRules.current
    monkeypatch.setattr(overlayRules, 'current', lambda v=None: calls.append(v) or real(v))
    refused = dashboardRouter.exportScenario(state['id'], caller=_caller('alice'))
    assert refused.status_code == 422
    body = json.loads(refused.body)
    assert body['field'] == 'sleeves' and body['error'].startswith('No sleeve in Gold yet for')
    assert calls == [sleeves.VARIANTS[0]], 'read once'


def test_the_premiums_currencies_come_from_its_rule(store):
    ttf, svp = _house()
    assert rules.canHoldVolPremium('USD') and not rules.canHoldVolPremium('EUR')
    overlayRules.save('*', [ttf, dict(svp, currencies=['USD', 'GBP', 'EUR'])], 'EUR too', 'alice')
    assert rules.canHoldVolPremium('EUR')
    overlayRules.save('*', [ttf], 'no premium', 'alice')
    assert not rules.canHoldVolPremium('USD')


# ------------------------------------- the console judges as the server ---

def _console(cases, scope='*'):
    """Run the console's checks (repository.js, the Overlay Funding view's
    pure part) in node over *cases*, each a list of rules as the console's
    draft holds them, against the server's own describe()."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    with open(os.path.join(HERE, '..', '..', 'generator', 'js', 'repository.js'), encoding='utf-8') as fh:
        source = fh.read()
    view = source[source.index('var OVL_PLACES'):source.index('/* ---- drawing ---- */')]
    view = view[view.index('var OVL_PLACES'):]
    data = overlayRules.describe()
    script = (_jsResolver()
              + 'var App = {resolveOverlays: resolveOverlays, overlayShortfall: overlayShortfall, '
                'overlayWords: overlayWords, overlayPct: overlayPct};\n'
              + 'function render() {} function api() {} function esc(s) { return String(s); }\n'
              + view.replace('var ovl = {', 'var ovl = {_: 0,', 1)
              + '\novl.data = ' + json.dumps(data) + '; ovl.scope = ' + json.dumps(scope) + ';\n'
              + 'const cases = ' + json.dumps(cases) + ';\n'
              + 'process.stdout.write(JSON.stringify(cases.map(function (c) {'
                ' var k = ovlChecks(c); return {per: k.per, worst: k.worst}; })));\n')
    out = subprocess.run([node], input=script, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _drafted(rule):
    r = json.loads(json.dumps(rule))
    r['size'] = str(rule['size'])
    r['sources'] = [{'category': s['category'], 'weightPct': str(s['weightPct'])} for s in rule['sources']]
    return r


def test_the_console_refuses_what_the_server_refuses(store):
    """(A, E, G, H) The drained source, the premium first with the move named,
    a number that is not one, a category differing only in case, and a
    rule placed after one below it - each a problem on the right card; the
    worst portfolio the same one the server names."""
    ttf, svp = [_drafted(r) for r in _house()]
    x = _drafted(_gold(id='x', name='X', into='X Cat', size=6.0,
                       sources=[{'category': IG, 'weightPct': 10.0}, {'category': OF, 'weightPct': 90.0}]))
    cases = [[ttf, svp], [ttf, svp, x], [svp, ttf],
             [ttf, dict(svp, size='1,5')], [ttf, dict(svp, size='5abc')],
             [ttf, svp, dict(_drafted(_gold()), into='public equity')],
             [dict(_drafted(_gold()), place='Gold 2'), dict(_drafted(_gold()), id='g2', name='G2', into='Gold 2')]]
    got = _console(cases)
    assert got[0]['per'] == [[], []]
    assert got[1]['per'][2] and got[1]['per'][2][-1].startswith(
        'It would take Investment Grade Fixed Income to -0.60% in USD Higher Risk Full with Tactical Tilts on')
    server = overlayRules.shortfalls(_house() + [overlayRules.normalise(
        [_gold(id='x', name='X', into='X Cat', size=6.0,
               sources=[{'category': IG, 'weightPct': 10.0}, {'category': OF, 'weightPct': 90.0}])])[0]],
        sleeveRepo.VARIANTS)
    assert got[1]['worst'][2]['keyStr'] == server[2]['keyStr']
    assert got[2]['per'][1][-1].startswith('Moving Strategic Volatility Premium above Tactical Tilts '
                                           'leaves Tactical Tilts unable to fund itself: it would take')
    assert got[3]['per'][1] == ['The size must be a number, such as 7.5 - not “1,5”.']
    assert got[4]['per'][1] == ['The size must be a number, such as 7.5 - not “5abc”.']
    assert any('a strategic category' in m for m in got[5]['per'][2])
    assert any('which is not above it' in m for m in got[6]['per'][0])


def test_the_console_and_the_server_agree_on_every_rules_worst_case(store):
    """The console's shortfall search is the server's: for a spread of lists
    the same rules are flagged, at the same portfolio, by the same amount."""
    ttf, svp = _house()
    lists = [[dict(ttf, size=30.0), svp], [svp, ttf],
             [ttf, svp, _gold(size=3.0, sources=[{'category': IG, 'weightPct': 50.0},
                                                {'category': OF, 'weightPct': 50.0}])],
             [ttf, svp, _gold(basis='sources', size=50.0)]]
    got = _console([[_drafted(r) for r in l] for l in lists])
    for ruleList, js in zip(lists, got):
        py = overlayRules.shortfalls(ruleList, sleeveRepo.VARIANTS)
        for a, b in zip(js['worst'], py):
            assert (a is None) == (b is None)
            if a is not None:
                assert (a['keyStr'], a['category']) == (b['keyStr'], b['category'])
                assert a['after'] == pytest.approx(b['after'], abs=1e-12)
