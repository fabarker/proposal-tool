"""A sleeve shared by several implementation types (D179): one sleeve - one
id, one history, one set of products - offered under each of its types, so
an edit made under any of them is what every one of them serves. Each type
still sees its own library: names, editions and the fixed categories are
asked type by type. Where it is offered is a switch, not a version, and a
type that needs it to differ is given a copy of its own."""
import os
import sqlite3

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.core import accessControl
from cyrus_pmg.pmgService.scenario import (feeCard, products, repositoryFeed, sleeveRepo,
                                           sleeveTools, sleeves)
from cyrus_pmg.pmgService.scenario.types import PortfolioKey, ValidationError

MA, ESG, US, IRISH = sleeveRepo.VARIANTS
C = 'Investment Grade Fixed Income'
HELD = [{'productId': 'gsam-core-municipal-sma', 'weight': 1.0}]
GBP_MOD_FULL = PortfolioKey('GBP', 'Moderate', 'Full', False)
USD_MOD_FULL = PortfolioKey('USD', 'Moderate', 'Full', False)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'sleeves.db'))
    products.reload()
    feeCard._forget()
    yield tmp_path
    products.reload()
    feeCard._forget()


def _caller(kerberos='alice'):
    return accessControl.UserData(kerberos, accessControl._rolesFor(kerberos))


def _served(variant, name, key=None):
    return next((s for s in sleeves.listSleeves(C, variant, key) if s['name'] == name), None)


def _shared(name='Core Shared', variants=sleeveRepo.VARIANTS, **kw):
    return sleeveRepo.createSleeves(list(variants), C, name, kw.pop('products', HELD),
                                    user='alice', **kw)[0]


def test_one_sleeve_is_served_under_every_type_it_names(store):
    made = _shared()
    assert made['variants'] == sleeveRepo.VARIANTS and made['shared']
    assert made['variant'] == 'All types'
    for variant in sleeveRepo.VARIANTS:
        assert _served(variant, 'Core Shared')['id'] == made['id']
        assert [e['id'] for e in sleeveRepo.listAll(variant) if e['name'] == 'Core Shared'] == [made['id']]
    # one row in the store, not four
    assert sum(1 for e in sleeveRepo.listAll() if e['name'] == 'Core Shared') == 1


def test_an_edit_under_one_type_is_what_every_type_serves(store):
    made = _shared(variants=[MA, ESG])
    sleeveRepo.updateSleeve(made['id'], 'Core Shared',
                            [{'productId': 'gsam-short-duration-sma', 'weight': 1.0}], user='bob')
    for variant in (MA, ESG):
        assert [p['productId'] for p in _served(variant, 'Core Shared')['products']] == \
            ['gsam-short-duration-sma']
    assert _served(US, 'Core Shared') is None
    assert [h['action'] for h in sleeveRepo.history(made['id'])] == ['updated', 'created']


def test_a_name_any_of_its_types_has_writes_nothing(store):
    before = sleeveRepo.describe()['sleeves']
    with pytest.raises(ValidationError) as exc:
        _shared(name='ETFs Only', variants=[US, MA])      # the seed's MA has ETFs Only
    assert exc.value.field == 'name' and MA in str(exc.value)
    assert sleeveRepo.describe()['sleeves'] == before


def test_editions_are_asked_type_by_type(store):
    """A shared fallback beside one type's own GBP edition is a name with an
    edition under that type and a fallback alone under the others; a shared
    edition whose rules claim that type's GBP portfolios is refused."""
    fallback = _shared(variants=[MA, ESG])
    sterling = sleeveRepo.createSleeves(
        [ESG], C, 'Core Shared', [{'productId': 'gs-access-ig-corporate-etf', 'weight': 1.0}],
        label='GBP', rules=[{'currency': ['GBP']}], user='alice')[0]
    assert _served(ESG, 'Core Shared', GBP_MOD_FULL)['id'] == sterling['id']
    assert _served(MA, 'Core Shared', GBP_MOD_FULL)['id'] == fallback['id']
    assert _served(ESG, 'Core Shared', USD_MOD_FULL)['id'] == fallback['id']
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.createSleeves([MA, ESG], C, 'Core Shared', HELD, label='Sterling',
                                 rules=[{'currency': ['GBP'], 'riskLevel': ['Moderate']}])
    assert exc.value.field == 'rules' and 'Overlaps the GBP edition' in str(exc.value)
    # a labelled edition added to the shared fallback is shared by the same types
    added = sleeveRepo.addEdition(fallback['id'], 'CHF', [{'currency': ['CHF']}], HELD, user='alice')
    assert added['variants'] == [MA, ESG]


def test_offering_under_another_type_shares_the_same_sleeve(store):
    made = _shared(variants=[MA])
    out = sleeveRepo.offerUnder(made['id'], US, user='bob')
    assert [e['id'] for e in out] == [made['id']] and out[0]['variants'] == [MA, US]
    assert _served(US, 'Core Shared')['id'] == made['id']
    assert sleeveRepo.revisionMap()[made['id']][0] == 1, 'not a version'
    with pytest.raises(ValidationError, match='already offered under'):
        sleeveRepo.offerUnder(made['id'], US)
    # a type with a sleeve of the name of its own keeps it: the two would be one name twice
    sleeveRepo.createSleeves([IRISH], C, 'core shared', HELD)
    with pytest.raises(ValidationError, match='already has its own core shared'):
        sleeveRepo.offerUnder(made['id'], IRISH)


def test_withdrawing_a_type_leaves_the_rest_sharing(store):
    made = _shared(variants=[MA, ESG, US])
    out = sleeveRepo.withdrawFrom(made['id'], ESG, user='bob')
    assert out[0]['variants'] == [MA, US] and _served(ESG, 'Core Shared') is None
    with pytest.raises(ValidationError, match='not offered under'):
        sleeveRepo.withdrawFrom(made['id'], ESG)
    sleeveRepo.withdrawFrom(made['id'], US)
    with pytest.raises(ValidationError, match='Archive it'):
        sleeveRepo.withdrawFrom(made['id'], MA)


def test_a_type_given_its_own_copy_is_separate_from_then_on(store):
    fallback = _shared(note='for everyone')
    edition = sleeveRepo.addEdition(fallback['id'], 'GBP', [{'currency': ['GBP']}],
                                    [{'productId': 'gs-access-ig-corporate-etf', 'weight': 1.0}],
                                    user='alice')
    sleeveRepo.setOffered(edition['id'], False, user='alice')
    out = sleeveRepo.splitOff(fallback['id'], ESG, user='bob')
    shared, copies = out[:2], out[2:]
    # the name's editions move together: both stay shared by the rest, and
    # ESG gets a copy of each, holding what it held
    assert {e['id'] for e in shared} == {fallback['id'], edition['id']}
    assert all(e['variants'] == [MA, US, IRISH] for e in shared)
    assert [(c['variants'], c['label'], c['note']) for c in copies] == \
        [([ESG], '', 'for everyone'), ([ESG], 'GBP', '')]
    assert copies[1]['disabled'], 'whether it is offered is copied too'
    assert copies[1]['rules'] == [{'currency': ['GBP']}]
    assert [h['action'] for h in sleeveRepo.history(copies[0]['id'])] == ['created']
    # separate: an edit to the copy leaves the shared one as it was
    sleeveRepo.updateSleeve(copies[0]['id'], 'Core Shared',
                            [{'productId': 'gsam-short-duration-sma', 'weight': 1.0}], user='bob')
    assert [p['productId'] for p in _served(ESG, 'Core Shared')['products']] == ['gsam-short-duration-sma']
    assert [p['productId'] for p in _served(MA, 'Core Shared')['products']] == ['gsam-core-municipal-sma']
    assert sleeveRepo.getSleeve(fallback['id'])['offeredUnder'] == sleeveRepo.VARIANTS
    with pytest.raises(ValidationError, match='its own already'):
        sleeveRepo.splitOff(copies[0]['id'], ESG)


def test_a_fixed_category_is_shared_and_a_type_may_give_it_up(store):
    """One shared sleeve can be every type's one in a fixed category; a type
    can be given a copy, or taken from it - and is then not given the
    overlay (D191)."""
    fixed = sleeveRepo.fixedCategories()[0]
    rows = [r for r in sleeveRepo.exportRows() if r[1] != fixed]
    one = next(r for r in sleeveRepo.exportRows() if r[1] == fixed)
    rows.append(('All', fixed, 'One For All', '', one[4], 1.0))
    sleeveRepo.importRows(rows, replace=True, ruleRows=[
        dict(zip(('variant', 'category', 'sleeve', 'edition', 'currency', 'riskLevel',
                  'allocationType'), (r[0], r[1], r[2], r[3], r[4].split('|') if r[4] else [],
                                      r[5].split('|') if r[5] else [], r[6].split('|') if r[6] else [])))
        for r in sleeveRepo.exportRuleRows()])
    assert sleeveRepo.census()['fixedCategoryProblems'] == []
    shared = next(e for e in sleeveRepo.listAll() if e['category'] == fixed)
    assert shared['variants'] == sleeveRepo.VARIANTS
    sleeveRepo.splitOff(shared['id'], ESG)
    assert sleeveRepo.census()['fixedCategoryProblems'] == []
    sleeveRepo.withdrawFrom(shared['id'], US)
    assert US not in sleeveRepo.getSleeve(shared['id'])['variants']
    census = sleeveRepo.census()
    assert census['fixedCategoryProblems'] == []
    assert [line.split(':')[0] for line in census['overlaysWithheld']] == [US]


def test_a_file_names_the_types_and_reimporting_it_keeps_the_sleeve(store, tmp_path):
    path = tmp_path / 'library.csv'
    path.write_text('Variant,Category,Sleeve,Edition,ProductId,Weight\n'
                    'All,{c},Core Shared,,gsam-core-municipal-sma,1\n'
                    'PMG ESG | US Onshore,{c},Core Shared,GBP,gs-access-ig-corporate-etf,1\n'.format(c=C))
    (tmp_path / 'library.rules.csv').write_text(
        'Variant,Category,Sleeve,Edition,Currency,RiskLevel,AllocationType\n'
        'PMG ESG|US Onshore,{c},Core Shared,GBP,GBP,,\n'.format(c=C))
    assert sleeveTools.main(['--import', str(path)]) == 0
    fallback = next(e for e in sleeveRepo.listAll() if e['name'] == 'Core Shared' and e['fallback'])
    sterling = next(e for e in sleeveRepo.listAll() if e['name'] == 'Core Shared' and e['label'])
    assert fallback['variants'] == sleeveRepo.VARIANTS and sterling['variants'] == [ESG, US]
    assert _served(US, 'Core Shared', GBP_MOD_FULL)['id'] == sterling['id']
    assert _served(MA, 'Core Shared', GBP_MOD_FULL)['id'] == fallback['id']
    # the export writes them back the same way, and loading it again is the same sleeves
    out = [r for r in sleeveRepo.exportRows() if r[2] == 'Core Shared']
    assert sorted(r[0] for r in out) == ['All', 'PMG ESG|US Onshore']
    assert sleeveTools.main(['--import', str(path)]) == 0
    assert {e['id'] for e in sleeveRepo.listAll() if e['name'] == 'Core Shared'} == \
        {fallback['id'], sterling['id']}
    # a file edition that shares only some of a live one's types is that name twice
    path.write_text('Variant,Category,Sleeve,Edition,ProductId,Weight\n'
                    'PMG ESG,{c},Core Shared,,gsam-core-municipal-sma,1\n'.format(c=C))
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.importRows(sleeveRepo.readSeedRows(str(path)))
    assert exc.value.field == 'name'
    path.write_text('Variant,Category,Sleeve,Edition,ProductId,Weight\n'
                    'Everywhere,{c},Core Shared,,gsam-core-municipal-sma,1\n'.format(c=C))
    with pytest.raises(ValidationError, match="'Everywhere' is not an implementation type"):
        sleeveRepo.importRows(sleeveRepo.readSeedRows(str(path)))


def test_the_census_counts_a_shared_sleeve_under_each_type(store, capsys):
    before = sleeveRepo.census()['byVariant']
    _shared(variants=[MA, IRISH])
    after = sleeveRepo.census()
    assert after['byVariant'][MA]['sleeves'] == before[MA]['sleeves'] + 1
    assert after['byVariant'][IRISH]['shared'] == 1
    assert after['byVariant'][ESG]['sleeves'] == before[ESG]['sleeves']
    assert after['shared'] == 1
    sleeveTools.main(['--census'])
    assert '1 shared by several types' in capsys.readouterr().out


def test_the_record_shows_each_switch_under_each_type(store):
    made = _shared(variants=[MA, ESG])
    sleeveRepo.offerUnder(made['id'], US, user='bob')
    sleeveRepo.splitOff(made['id'], ESG, user='carol')
    page = repositoryFeed.activity(areas=['availability'])
    words = [(e['action'], e['changes'][0]) for e in page['entries']]
    assert words[:2] == [('split', 'PMG ESG given a copy of its own; the rest still share this one'),
                         ('shared', 'Now offered under US Onshore too, the same sleeve')]
    assert page['facets']['variant'][MA] >= 2 and page['facets']['variant'][US] >= 2
    # a type's filter finds the shared sleeve's revisions and switches
    under = repositoryFeed.activity(variant=MA, query='Core Shared')
    assert {e['area'] for e in under['entries']} == {'sleeves', 'availability'}
    assert all(MA in e['variants'] for e in under['entries'])


def test_a_ninth_store_gains_the_switch_detail_in_place(store):
    sleeveRepo.describe()
    path = os.environ['SCENARIO_SLEEVES_DB']
    conn = sqlite3.connect(path)
    conn.execute('ALTER TABLE sleeveSwitches DROP COLUMN detail')
    conn.execute("UPDATE meta SET value = '9' WHERE key = 'schemaVersion'")
    conn.commit()
    conn.close()
    assert sleeveRepo.describe()['schemaVersion'] == 12
    made = _shared(variants=[MA, ESG])
    sleeveRepo.withdrawFrom(made['id'], ESG)
    conn = sqlite3.connect(path)
    assert conn.execute('SELECT detail FROM sleeveSwitches').fetchall() == [('PMG ESG',)]
    conn.close()


def test_the_route_shares_unshares_and_splits(store, monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    made = _shared(variants=[MA])
    route = dashboardRouter.shareRepositorySleeve
    assert route(made['id'], {'action': 'share', 'variant': ESG},
                 caller=_caller())['sleeves'][0]['variants'] == [MA, ESG]
    assert route(made['id'], {'action': 'split', 'variant': ESG},
                 caller=_caller())['sleeves'][1]['variants'] == [ESG]
    refused = route(made['id'], {'action': 'unshare', 'variant': MA}, caller=_caller())
    assert refused.status_code == 422
    assert route(made['id'], {'action': 'move'}, caller=_caller()).status_code == 422
    # the PWA's picker under the copy's type is the copy
    assert _served(ESG, 'Core Shared')['id'] != made['id']
