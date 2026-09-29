"""D148: the private-markets funding split, held in the repository.

Until capital is called, a private-markets commitment is held in other
categories by the split the desk sets in the console: a house split for
every implementation type, and optionally a type's own. The weights are
percentages to four places adding up to exactly 100; only a category every
private-markets book holds may take a share. Every change carries a note and
adds a revision; a restore is a revision too. The seeded house split is
D136's thirds, and reproduces its figures.
"""

import json
import os
import shutil
import subprocess

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.scenario import (fundingSplit, pptWriter, proposalRegister, rules,
                                           sleeveRepo)
from cyrus_pmg.pmgService.scenario.types import ValidationError
from cyrus_pmg.pmgService.scenario.workbook import buildImplementationRows

HERE = os.path.dirname(os.path.abspath(__file__))
IG, OF, EQ, HF = ('Investment Grade Fixed Income', 'Other Fixed Income', 'Public Equity',
                  'Hedge Funds')
ESG = 'PMG ESG'


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A repository file of this test's own, so a saved split never reaches
    another test's figures."""
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    return tmp_path


def _caller(kerberos='alice'):
    from cyrus_pmg.pmgService.core import accessControl
    return accessControl.UserData(kerberos, accessControl._rolesFor(kerberos))


def _split(*pairs):
    return [{'category': c, 'weightPct': w} for c, w in pairs]


# ------------------------------------------------------------ the store ---

def test_the_house_split_is_seeded_with_d136s_thirds(store):
    house = fundingSplit.describe()['house']
    assert house['scope'] == '*' and house['revision'] == 1
    assert house['destinations'] == _split((IG, 33.3333), (EQ, 66.6667))
    assert fundingSplit.describe()['overrides'] == {}
    (first,) = fundingSplit.history('*')
    assert first['action'] == 'baseline' and first['actor'] == 'system' and 'D136' in first['note']
    # opening again seeds nothing more
    fundingSplit.describe()
    assert len(fundingSplit.history('*')) == 1
    assert sleeveRepo.describe()['schemaVersion'] == 4


def test_only_a_category_every_private_markets_book_holds_may_take_a_share(store):
    by = {e['category']: e for e in fundingSplit.eligible()}
    assert [c for c, e in by.items() if e['eligible']] == [IG, OF, EQ]
    assert all(by[c]['heldBy'] == by[c]['of'] == 112 for c in (IG, OF, EQ))
    assert by[HF]['heldBy'] == 56 and not by[HF]['eligible']
    assert 'Private Equity' not in by and 'Other Private Assets' not in by
    assert 'Asset Allocation Strategies' not in by


@pytest.mark.parametrize('destinations, says', [
    ([], 'at least one category'),
    (_split((IG, 25), (OF, 25), (EQ, 25), (HF, 25)), 'At most 3'),
    (_split((IG, 50), (IG, 50)), 'named twice'),
    (_split(('Private Equity', 50), (EQ, 50)), 'cannot also hold it'),
    (_split(('Commodities', 50), (EQ, 50)), 'not a category'),
    (_split((HF, 50), (EQ, 50)), 'held by only 56 of the 112 private-markets portfolios; the other 56'),
    (_split((IG, 0), (EQ, 100)), 'above zero'),
    (_split((IG, 33.33333), (EQ, 66.66667)), 'more than 4 decimal places'),
    (_split((OF, 50), (EQ, 49.5)), 'add up to 99.5000%'),
    (_split((IG, 'lots'), (EQ, 50)), 'weight in percent'),
])
def test_an_invalid_split_is_refused_naming_why(store, destinations, says):
    with pytest.raises(ValidationError) as caught:
        fundingSplit.save('*', destinations, 'a note', 'alice')
    assert caught.value.field == 'destinations' and says in caught.value.message
    assert fundingSplit.describe()['house']['revision'] == 1, 'nothing written'


def test_a_change_needs_a_note_a_known_scope_and_a_change(store):
    with pytest.raises(ValidationError) as caught:
        fundingSplit.save('*', _split((OF, 50), (EQ, 50)), '  ', 'alice')
    assert caught.value.field == 'note'
    with pytest.raises(ValidationError) as caught:
        fundingSplit.save('Not A Type', _split((OF, 50), (EQ, 50)), 'n', 'alice')
    assert caught.value.field == 'scope'
    with pytest.raises(ValidationError) as caught:
        fundingSplit.save('*', _split((IG, 33.3333), (EQ, 66.6667)), 'n', 'alice')
    assert 'already in force' in caught.value.message


def test_a_type_can_carry_its_own_split_and_go_back_to_the_house(store):
    assert fundingSplit.current(ESG)['inherited']
    own = fundingSplit.save(ESG, _split((OF, 50), (EQ, 50)), 'ESG parks in credit', 'alice')
    assert own['scope'] == ESG and own['revision'] == 1 and not own['inherited']
    assert rules.privateFunding(ESG) == [(OF, 0.5), (EQ, 0.5)]
    assert rules.privateFunding('US Onshore') == rules.privateFunding(), 'the others keep the house'
    back = fundingSplit.removeOverride(ESG, 'back to the house', 'alice')
    assert back['inherited'] and rules.privateFunding(ESG) == rules.privateFunding()
    history = fundingSplit.history(ESG)
    assert [(h['revision'], h['action']) for h in history] == [(2, 'removed'), (1, 'updated')]
    assert history[0]['destinations'] is None and history[0]['note'] == 'back to the house'
    with pytest.raises(ValidationError):
        fundingSplit.removeOverride(ESG, 'again', 'alice')
    with pytest.raises(ValidationError):
        fundingSplit.removeOverride('*', 'the house', 'alice')


def test_a_restore_is_a_new_revision(store):
    fundingSplit.save('*', _split((OF, 40), (EQ, 60)), 'try credit', 'alice')
    restored = fundingSplit.revert('*', 1, 'back to thirds', 'bob')
    assert restored['revision'] == 3 and restored['destinations'] == _split((IG, 33.3333), (EQ, 66.6667))
    history = fundingSplit.history('*')
    assert [(h['revision'], h['action'], h['actor']) for h in history] == [
        (3, 'reverted', 'bob'), (2, 'updated', 'alice'), (1, 'baseline', 'system')]
    with pytest.raises(ValidationError):
        fundingSplit.revert('*', 3, 'again', 'bob')           # already in force
    with pytest.raises(ValidationError):
        fundingSplit.revert('*', 9, 'none', 'bob')
    fundingSplit.save(ESG, _split((OF, 50), (EQ, 50)), 'own', 'alice')
    fundingSplit.removeOverride(ESG, 'gone', 'alice')
    with pytest.raises(ValidationError) as caught:
        fundingSplit.revert(ESG, 2, 'restore a removal', 'alice')
    assert 'no split in it' in caught.value.message


# ------------------------------------------------------------ the model ---

def _book(key='USD|Moderate|Full|0'):
    from test_initial_allocation import _book as book, _sleeves
    _, result = book('USD', key)
    return result, _sleeves(result)


def _model(result, sleeves, variant='PMG Multi-Asset Portfolio'):
    return buildImplementationRows(result, sleeves, rules.AUTO_SLEEVE_CATEGORIES, 50e6, variant,
                                   True, 'CASP', 'PMG Target', 50e6, True, 'USD')


def test_the_seeded_split_prints_exactly_what_d136s_thirds_printed(store, monkeypatch):
    """33.3333 and 66.6667 are within a millionth of a point of the thirds,
    so every printed weight, notional and fee is the one D136 printed."""
    result, sleeves = _book()
    seeded = _model(result, sleeves)
    monkeypatch.setattr(fundingSplit, 'current', lambda variant=None: {
        'scope': '*', 'revision': 0, 'inherited': False,
        'destinations': [{'category': IG, 'weightPct': 100 / 3.0},
                         {'category': EQ, 'weightPct': 200 / 3.0}]})
    thirds = _model(result, sleeves)
    items = lambda m: [(i['name'], i['initialPct'], i['initialNotional'], i['initialWtdFeeBp'])
                       for g in m['groups'] for i in g['items']]
    assert items(seeded) == items(thirds)
    assert seeded['initial']['total'] == thirds['initial']['total']
    assert seeded['initial']['split'] == {'scope': '*', 'revision': 1,
                                          'destinations': _split((IG, 33.3333), (EQ, 66.6667))}


def test_a_types_own_split_parks_its_books_and_no_others(store):
    result, sleeves = _book()
    fundingSplit.save(ESG, _split((OF, 50), (EQ, 50)), 'ESG parks in credit', 'alice')
    esg = _model(result, sleeves, ESG)
    house = _model(result, sleeves)
    group = lambda m, name: next(g for g in m['groups'] if g['category'] == name)
    committed = esg['initial']['commitment']['weightPct']
    assert group(esg, OF)['initialWeightPct'] == pytest.approx(group(esg, OF)['weightPct'] + committed / 2)
    assert group(esg, IG)['initialWeightPct'] == pytest.approx(group(esg, IG)['weightPct'])
    assert [h['category'] for h in esg['initial']['commitment']['held']] == [OF, EQ]
    assert esg['initial']['split']['scope'] == ESG
    assert [h['category'] for h in house['initial']['commitment']['held']] == [IG, EQ]
    assert house['initial']['split']['scope'] == '*'


def test_the_schema_serves_the_split_in_force_for_the_type(store):
    fundingSplit.save(ESG, _split((OF, 50), (EQ, 50)), 'own', 'alice')
    served = rules._servedFunding(ESG)
    assert [(t['category'], t['share'], t['weightPct']) for t in served['to']] == [
        (OF, 0.5, 50.0), (EQ, 0.5, 50.0)]
    assert served['scope'] == ESG and served['revision'] == 1 and not served['inherited']
    house = rules._servedFunding('US Onshore')
    assert house['scope'] == '*' and house['inherited']


# ------------------------------------------------------- where it lands ---

def test_the_register_records_the_split_a_proposal_was_built_with(store, monkeypatch):
    import test_proposal_register as reg
    monkeypatch.setenv('SCENARIO_REGISTER_DB', str(store / 'proposals.db'))
    entry, _, _ = reg._deliver()
    kept = proposalRegister.getProposal(entry['proposalId'])
    assert kept['fundingSplit'] == {'scope': '*', 'revision': 1,
                                    'destinations': _split((IG, 33.3333), (EQ, 66.6667))}
    row = dict(zip(proposalRegister.EXPORT_COLUMNS, proposalRegister.exportRows()[0]))
    assert row['FundingSplit'] == ('r1 (house) Investment Grade Fixed Income 33.3333% · '
                                   'Public Equity 66.6667%')
    # a later change to the split leaves the delivered record as it was
    fundingSplit.save('*', _split((OF, 40), (EQ, 60)), 'later', 'alice')
    assert proposalRegister.getProposal(entry['proposalId'])['fundingSplit']['revision'] == 1


def test_the_deck_and_the_page_say_a_four_place_third_as_a_third():
    assert pptWriter._shareWords(0.333333) == 'a third'
    assert pptWriter._shareWords(0.666667) == 'two thirds'
    assert pptWriter._shareWords(0.5) == 'half'
    assert pptWriter._shareWords(0.4) == '40%'
    assert pptWriter._shareWords(0.123456) == '12.3456%'
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    with open(os.path.join(HERE, '..', '..', 'generator', 'js', 'implementation.js'),
              encoding='utf-8') as handle:
        source = handle.read()
    start = source.index('function shareText(')
    fn = source[start:source.index('\n}\n', start) + 3]
    script = fn + 'process.stdout.write(JSON.stringify([0.333333, 0.666667, 0.5, 0.4].map(shareText)));'
    got = json.loads(subprocess.run([node, '-e', script], capture_output=True, text=True,
                                    check=True).stdout)
    assert got == ['⅓', '⅔', '½', '40%']


def test_the_page_parks_any_split_as_the_files_do(store):
    """The page's mirror reads the split from the schema; given a split other
    than the house's, it still lands where the Python does."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    with open(os.path.join(HERE, '..', '..', 'generator', 'js', 'implementation.js'),
              encoding='utf-8') as handle:
        source = handle.read()
    fn = source[source.index('function privateFunding('):source.index('/* end of the initial overlay */')]
    funding = [(OF, 0.25), (EQ, 0.75)]
    result, _ = _book()
    categories = rules.implementedCategories(result['categories'], True, True, 'USD')
    lines = [{'name': s['name'], 'weightPct': s['weightPct']} for s in rules.sleeveCategories(categories)]
    expected = rules.initialLines(lines, funding)
    served = {'from': rules.PRIVATE_FUNDED_FROM,
              'to': [{'category': c, 'share': s} for c, s in funding]}
    stub = ('var App = { opt: function (path, fallback) { return ({'
            "'rules.privateFunding': " + json.dumps(served) + '})[path]; } };\n')
    script = stub + fn + '\nprocess.stdout.write(JSON.stringify(initialLines(' + json.dumps(lines) + ')));\n'
    got = json.loads(subprocess.run([node, '-e', script], capture_output=True, text=True,
                                    check=True).stdout)
    assert [l['name'] for l in got] == [l['name'] for l in expected]
    assert [l['weightPct'] for l in got] == pytest.approx([l['weightPct'] for l in expected], abs=1e-12)


# ------------------------------------------------------------ the API ---

def test_the_endpoints_read_write_remove_and_restore(store, monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    alice = _caller('alice')
    body = dashboardRouter.getFundingSplit(caller=alice)
    assert body['house']['revision'] == 1 and body['places'] == 4 and body['maxDestinations'] == 3
    refused = dashboardRouter.saveFundingSplit(
        {'scope': '*', 'destinations': _split((HF, 50), (EQ, 50)), 'note': 'n'}, caller=alice)
    assert refused.status_code == 422
    assert json.loads(refused.body)['field'] == 'destinations'
    saved = dashboardRouter.saveFundingSplit(
        {'scope': ESG, 'destinations': _split((OF, 50), (EQ, 50)), 'note': 'own'}, caller=alice)
    assert saved['split']['updatedBy'] == 'alice' and ESG in saved['funding']['overrides']
    history = dashboardRouter.getFundingHistory(scope=ESG, caller=alice)
    assert [h['action'] for h in history['history']] == ['updated']
    removed = dashboardRouter.removeFundingOverride({'scope': ESG, 'note': 'gone'}, caller=alice)
    assert ESG not in removed['funding']['overrides']
    restored = dashboardRouter.revertFundingSplit({'scope': ESG, 'revision': 1, 'note': 'again'},
                                                  caller=alice)
    assert restored['split']['revision'] == 3 and restored['split']['scope'] == ESG
    bad = dashboardRouter.revertFundingSplit({'scope': ESG, 'revision': 'x', 'note': 'n'},
                                             caller=alice)
    assert bad.status_code == 422


# -------------------------------------------------------- the console ---

def test_the_console_refuses_what_the_server_refuses():
    """The Rules view's checks mirror the server's, word for word where they
    overlap, so Save stays off until the server would accept."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    with open(os.path.join(HERE, '..', '..', 'generator', 'js', 'repository.js'),
              encoding='utf-8') as handle:
        source = handle.read()
    start = source.index('var FUND_PLACES')
    fn = source[start:source.index('function fundTotalText(')]
    eligible = [{'category': IG, 'heldBy': 112, 'of': 112, 'eligible': True},
                {'category': EQ, 'heldBy': 112, 'of': 112, 'eligible': True},
                {'category': HF, 'heldBy': 56, 'of': 112, 'eligible': False}]
    stub = ('var fund = {data: {eligible: ' + json.dumps(eligible) + ', maxDestinations: 3}};\n'
            'function render() {} function api() {}\n')
    cases = [[{'category': IG, 'weight': '33.3333'}, {'category': EQ, 'weight': '66.6667'}],
             [{'category': HF, 'weight': '50'}, {'category': EQ, 'weight': '50'}],
             [{'category': IG, 'weight': '50'}, {'category': EQ, 'weight': '49.5'}],
             [{'category': IG, 'weight': '33.33333'}, {'category': EQ, 'weight': '66.66667'}],
             []]
    script = stub + fn + 'process.stdout.write(JSON.stringify(' + json.dumps(cases) + '.map(fundProblems)));'
    got = json.loads(subprocess.run([node, '-e', script], capture_output=True, text=True,
                                    check=True).stdout)
    assert got[0] == []
    assert got[1] == ['Hedge Funds is held by only 56 of the 112 private-markets portfolios; the other 56 '
                      'would have nowhere to put its share.']
    assert got[2] == ['The weights add up to 99.5000%; they must add up to exactly 100%.']
    assert any('more than 4 decimal places' in m for m in got[3])
    assert got[4] == ['Name at least one category to hold the money.']
    # and the tab is in the console and on the landing strip, under the name
    # the desk gave it (D149), at #uncalled
    assert "['uncalled', 'Uncalled Capital Allocation', onUncalled, 0]" in source
    assert "['proposals', 'Proposals'], ['uncalled', 'Uncalled Capital Allocation']" in source
    assert "if (view === 'uncalled') return '#uncalled';" in source
    assert "repo.view === 'rules'" not in source and "'#rules'" not in source, \
        'no Rules tab left behind'


# ------------------------------------------------------ the desk's words ---

def _literals(source):
    """Every string literal in a JavaScript file, comments and regex literals
    skipped: a small scanner, enough for the page's own code."""
    out, i, n = [], 0, len(source)
    previous = ''
    while i < n:
        c = source[i]
        if source.startswith('//', i):
            i = source.find('\n', i)
            i = n if i < 0 else i
            continue
        if source.startswith('/*', i):
            i = source.find('*/', i + 2)
            i = n if i < 0 else i + 2
            continue
        if c in '\'"`':
            j, buf = i + 1, []
            while j < n and source[j] != c:
                if source[j] == '\\':
                    buf.append(source[j:j + 2]); j += 2; continue
                buf.append(source[j]); j += 1
            out.append(''.join(buf))
            i, previous = j + 1, 'x'
            continue
        if c == '/' and previous in ('', '(', ',', '=', ':', '[', '!', '&', '|', '?', '{', '}', ';', '+', '\n'):
            j, inClass = i + 1, False
            while j < n:
                if source[j] == '\\':
                    j += 2; continue
                if source[j] == '[':
                    inClass = True
                elif source[j] == ']':
                    inClass = False
                elif source[j] == '/' and not inClass:
                    break
                elif source[j] == '\n':
                    break
                j += 1
            i, previous = j + 1, 'x'
            continue
        if not c.isspace():
            previous = c
        i += 1
    return out


def test_the_page_never_says_book():
    """The desk says portfolio, or allocation, or implementation type - never
    book (D150). Every string the page's code can show is checked; the one
    class name and the URL keys that keep the old word are code, not words."""
    import re
    code = ('repo-books', 'repo-book')
    for name in ('core.js', 'picker.js', 'implementation.js', 'repository.js'):
        with open(os.path.join(HERE, '..', '..', 'generator', 'js', name), encoding='utf-8') as handle:
            source = handle.read()
        for literal in _literals(source):
            if literal == 'book':
                continue                                  # a key, never shown
            text = literal
            for token in code:
                text = text.replace(token, '')
            assert not re.search(r'\bbooks?\b', text, re.I), (name, literal)
