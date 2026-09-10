"""Sleeve editions and their applicability rules (D89).

A sleeve NAME may hold several EDITIONS in the repository, each with its own
products and a set of rules over the strategic portfolio's currency, risk
level and allocation type. The resolver serves one row per name for a base
portfolio, and the PWA is never told which. These tests hold that model to
its rules: the vocabulary, the counts, the refusals, the resolution, the
history, the seed in both shapes, the migration, and the two endpoints.

The store is the per-session temporary database conftest points
SCENARIO_SLEEVES_DB at. Tests that write clean up after themselves.
"""

import csv
import json
import os
import sqlite3

import pytest

from cyrus_pmg.pmgService import dashboardRouter
from cyrus_pmg.pmgService.core import accessControl
from cyrus_pmg.pmgService.scenario import sleeveRepo, sleeveRules, sleeveTools, sleeves, universe
from cyrus_pmg.pmgService.scenario.types import BasisInput, PortfolioKey, ValidationError

V = 'PMG Multi-Asset Portfolio'
C = 'Investment Grade Fixed Income'
A_PRODUCT = 'gs-us-corporate-bond-fund'
ANOTHER = 'ashfield-global-credit-fund'

GBP_MOD_XALTS = PortfolioKey('GBP', 'Moderate', 'ex-Alts', False)
GBP_MOD_FULL = PortfolioKey('GBP', 'Moderate', 'Full', False)
USD_MOD_FULL = PortfolioKey('USD', 'Moderate', 'Full', False)
EUR_MOD_CORE = PortfolioKey('EUR', 'Moderate', 'Core', False)


def _caller(kerberos='alice'):
    return accessControl.UserData(kerberos, accessControl._rolesFor(kerberos))


def _products(*pairs):
    pairs = pairs or ((A_PRODUCT, 0.6), (ANOTHER, 0.4))
    return [{'productId': p, 'weight': w} for p, w in pairs]


def _archive(*ids):
    for sleeveId in ids:
        try:
            sleeveRepo.deleteSleeve(sleeveId, user='cleanup')
        except ValidationError:
            pass


# ---- the vocabulary and the counts ----------------------------------------------

def test_the_vocabulary_is_three_fields_from_the_universe():
    vocab = sleeveRules.vocabulary()
    assert list(vocab) == ['currency', 'riskLevel', 'allocationType'] == list(sleeveRules.FIELDS)
    assert vocab['currency'] == ['USD', 'GBP', 'CHF', 'EUR']
    assert vocab['riskLevel'][0] == 'LowVol' and vocab['riskLevel'][-1] == 'All Equity'
    assert vocab['allocationType'] == ['Full', 'Core', 'ex-Alts', 'ex-HFs', 'NA']
    # hedging and the real-assets exclusion are deliberately not fields
    for gone in ('hedging', 'excludeRealAssets'):
        assert gone not in vocab
        with pytest.raises(ValidationError) as exc:
            sleeveRules.normalise([{gone: ['x']}])
        assert exc.value.field == 'rules' and gone in exc.value.message


def test_the_examples_from_the_proposal_count_as_the_proposal_said():
    keys = len(universe.keys())
    assert keys == 172
    count = lambda rules: len(sleeveRules.applicability(sleeveRules.normalise(rules)))
    assert count([{'currency': 'GBP'}]) == 43
    assert count([{'currency': 'GBP', 'allocationType': 'ex-Alts'}]) == 7
    assert count([{'currency': 'EUR', 'riskLevel': 'Moderate'},
                  {'currency': 'GBP', 'allocationType': 'Full'}]) == 20
    assert count([{'currency': 'GBP', 'allocationType': 'Full|Core|ex-HFs|NA'}]) == 36
    # a rule matches on the three fields alone, so both exclusion variants of
    # a portfolio match the same rule (Full offers the ex-RAs variant)
    both = [k for k in universe.keys() if k.currency == 'GBP' and k.riskLevel == 'Moderate'
            and k.allocationType == 'Full']
    assert len(both) == 2 and {k.excludeRealAssets for k in both} == {False, True}
    assert all(sleeveRules.matches(
        sleeveRules.normalise([{'currency': 'GBP', 'allocationType': 'Full'}]), k) for k in both)
    # overlap is a set intersection, named key by key
    shared = sleeveRules.overlap(sleeveRules.normalise([{'currency': 'GBP'}]),
                                 sleeveRules.normalise([{'currency': 'GBP', 'allocationType': 'ex-Alts'}]))
    assert len(shared) == 7 and shared[0].startswith('GBP|') and '|ex-Alts|' in shared[0]


@pytest.mark.parametrize('bad, needle', [
    ([{'currency': 'JPY'}], 'JPY'),
    ([{}], 'constrains nothing'),
    ([{'currency': 'GBP'}, {'currency': 'GBP'}], 'repeats'),
    ('not json', 'could not be read'),
    ([{'riskLevel': 'Balanced'}], 'Balanced'),
])
def test_a_rule_that_cannot_be_judged_is_refused_by_name(bad, needle):
    with pytest.raises(ValidationError) as exc:
        sleeveRules.normalise(bad)
    assert exc.value.field == 'rules' and needle in exc.value.message


def test_rules_normalise_to_vocabulary_order_and_pack_round_trips():
    rules = sleeveRules.normalise([{'allocationType': 'ex-HFs|Full', 'currency': ['EUR', 'USD']}])
    assert rules == [{'currency': ['USD', 'EUR'], 'allocationType': ['Full', 'ex-HFs']}]
    assert sleeveRules.unpack(sleeveRules.pack(rules)) == rules
    assert sleeveRules.describe(rules) == 'currency USD|EUR; allocationType Full|ex-HFs'
    assert sleeveRules.normalise(None) == [] and sleeveRules.applicability([]) == []


# ---- editions in the repository -------------------------------------------------

def _fallbackOf(name):
    return [e for e in sleeveRepo.listAll(V) if e['category'] == C and e['name'] == name][0]


def test_an_edition_is_another_row_of_the_same_name_and_resolves_by_portfolio():
    fb = _fallbackOf('ETFs Only')
    assert fb['fallback'] and fb['label'] == '' and fb['applies'] is None
    gbp = sleeveRepo.addEdition(fb['id'], 'GBP', [{'currency': 'GBP', 'allocationType': 'Full|Core|ex-HFs|NA'}],
                                _products(), user='desk')
    gbpx = sleeveRepo.addEdition(fb['id'], 'GBP ex-Alts', [{'currency': 'GBP', 'allocationType': 'ex-Alts'}],
                                 _products((A_PRODUCT, 1.0)), user='desk')
    try:
        assert (gbp['name'], gbp['label'], gbp['applies'], gbp['fallback']) == ('ETFs Only', 'GBP', 36, False)
        assert (gbpx['label'], gbpx['applies']) == ('GBP ex-Alts', 7)
        assert gbp['rules'] == [{'currency': ['GBP'], 'allocationType': ['Full', 'Core', 'ex-HFs', 'NA']}]
        # one entry per name, whichever edition answers
        for key, expect in ((GBP_MOD_XALTS, gbpx['id']), (GBP_MOD_FULL, gbp['id']),
                            (USD_MOD_FULL, fb['id']), (None, fb['id'])):
            served = sleeves.listSleeves(C, V, key)
            names = [s['name'] for s in served]
            assert names.count('ETFs Only') == 1 and len(names) == len(set(names))
            mine = [s for s in served if s['name'] == 'ETFs Only'][0]
            assert mine['id'] == expect, (key, mine['id'], expect)
            # the PWA is never told which edition it got
            assert set(mine) == {'id', 'name', 'note', 'products'}
        assert sleeves.sleeveExists(C, 'ETFs Only', V, GBP_MOD_XALTS)
        assert sleeves.sleeveExists(C, 'ETFs Only', V, EUR_MOD_CORE)      # the fallback answers
        # the three editions read as one name in the console's list
        siblings = [e for e in sleeveRepo.listAll(V) if e['category'] == C and e['name'] == 'ETFs Only']
        assert sorted(e['label'] for e in siblings) == ['', 'GBP', 'GBP ex-Alts']
        assert all(e['offeredUnder'] == fb['offeredUnder'] for e in siblings)
    finally:
        _archive(gbp['id'], gbpx['id'])


def test_a_name_with_no_fallback_is_offered_only_where_an_edition_applies():
    made = sleeveRepo.createSleeve(V, C, 'Sterling Only', _products(), user='desk',
                                   label='GBP', rules=[{'currency': 'GBP'}])
    try:
        assert 'Sterling Only' in [s['name'] for s in sleeves.listSleeves(C, V, GBP_MOD_FULL)]
        assert 'Sterling Only' not in [s['name'] for s in sleeves.listSleeves(C, V, USD_MOD_FULL)]
        assert 'Sterling Only' not in [s['name'] for s in sleeves.listSleeves(C, V)]
    finally:
        _archive(made['id'])


def test_the_edition_rules_are_enforced_at_save():
    fb = _fallbackOf('SMA Only')
    gbp = sleeveRepo.addEdition(fb['id'], 'GBP', [{'currency': 'GBP'}], _products(), user='desk')
    try:
        def refused(field, needle, **kw):
            with pytest.raises(ValidationError) as exc:
                sleeveRepo.addEdition(fb['id'], kw.pop('label', ''), kw.pop('rules', None),
                                      _products(), user='desk')
            assert exc.value.field == field, exc.value.message
            assert needle in exc.value.message, exc.value.message
        refused('rules', 'Overlaps the GBP edition on 7 portfolio', label='GBP ex-Alts',
                rules=[{'currency': 'GBP', 'allocationType': 'ex-Alts'}])
        refused('label', 'already has a GBP edition', label='GBP', rules=[{'currency': 'EUR'}])
        refused('name', 'already exists', label='', rules=[])
        refused('label', 'needs a label', label='', rules=[{'currency': 'EUR'}])
        refused('rules', 'has no rules', label='Lonely', rules=[])
        refused('rules', 'JPY', label='Yen', rules=[{'currency': 'JPY'}])
        # nothing above was written
        assert sorted(e['label'] for e in sleeveRepo.listAll(V)
                      if e['category'] == C and e['name'] == 'SMA Only') == ['', 'GBP']
        # a disjoint edition is fine, and the overlap report names the clash exactly
        eur = sleeveRepo.addEdition(fb['id'], 'EUR Mod', [{'currency': 'EUR', 'riskLevel': 'Moderate'}],
                                    _products(), user='desk')
        _archive(eur['id'])
    finally:
        _archive(gbp['id'])


def test_a_duplicate_fallback_is_still_the_name_clash_it_always_was():
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.createSleeve(V, C, 'SMA Only', _products(), user='desk')
    assert exc.value.field == 'name' and 'already exists' in exc.value.message


def test_a_fixed_category_holds_one_name_but_may_hold_its_editions():
    from cyrus_pmg.pmgService.scenario import rules as R
    fixed = R.AUTO_SLEEVE_CATEGORIES[0]
    one = [e for e in sleeveRepo.listAll(V) if e['category'] == fixed][0]
    products = [{'productId': p['productId'], 'weight': p['weight']} for p in one['products']]
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.createSleeve(V, fixed, 'A Second Name', products, user='desk')
    assert exc.value.field == 'category'
    edition = sleeveRepo.addEdition(one['id'], 'GBP', [{'currency': 'GBP'}], products, user='desk')
    try:
        assert sleeveRepo.census()['fixedCategoryProblems'] == []
        # the auto-attach still finds exactly one sleeve, the right edition
        assert [s['id'] for s in sleeves.listSleeves(fixed, V, GBP_MOD_FULL)] == [edition['id']]
        assert [s['id'] for s in sleeves.listSleeves(fixed, V, USD_MOD_FULL)] == [one['id']]
    finally:
        # a fixed-category edition cannot be deleted through the ordinary
        # path (the category always holds one); retire it directly
        conn = sqlite3.connect(sleeveRepo.dbPath())
        conn.execute("UPDATE sleeves SET deletedAt = 'cleanup', deletedBy = 'cleanup' WHERE id = ?",
                     (edition['id'],))
        conn.commit(); conn.close()


def test_history_carries_the_edition_and_a_revert_puts_it_back():
    fb = _fallbackOf('SMA & ETFs')
    ed = sleeveRepo.addEdition(fb['id'], 'EUR', [{'currency': 'EUR'}], _products(), user='desk')
    try:
        first = sleeveRepo.history(ed['id'])[0]
        assert first['label'] == 'EUR' and first['rules'] == [{'currency': ['EUR']}]
        sleeveRepo.updateSleeve(ed['id'], 'SMA & ETFs', _products(), user='desk', label='EUR Moderate',
                                rules=[{'currency': 'EUR', 'riskLevel': 'Moderate'}])
        trail = sleeveRepo.history(ed['id'])
        assert trail[0]['label'] == 'EUR Moderate'
        assert any('Relabelled from EUR' in c for c in trail[0]['changes'])
        assert any(c.startswith('Rules now:') for c in trail[0]['changes'])
        back = sleeveRepo.revertSleeve(ed['id'], 1, user='desk')
        assert back['label'] == 'EUR' and back['rules'] == [{'currency': ['EUR']}]
        assert back['revisions'] == 3
        # an update that says nothing about the edition leaves it alone
        same = sleeveRepo.updateSleeve(ed['id'], 'SMA & ETFs', _products((A_PRODUCT, 1.0)), user='desk')
        assert same['label'] == 'EUR' and same['rules'] == [{'currency': ['EUR']}]
        # the activity feed and the exports carry it too
        feed = sleeveRepo.activity(limit=5)['entries']
        assert feed[0]['sleeveId'] == ed['id'] and feed[0]['label'] == 'EUR'
        assert 'Edition' in sleeveRepo.ACTIVITY_COLUMNS and 'Rules' in sleeveRepo.ARCHIVE_COLUMNS
    finally:
        _archive(ed['id'])
    archived = [r for r in sleeveRepo.archiveRows() if r[0] == ed['id']][0]
    assert archived[4] == 'EUR' and archived[5] == 'currency EUR'


def test_an_overlap_that_arrives_after_the_fact_withholds_the_name_and_flags_both(monkeypatch):
    """Two editions disjoint when saved can both claim a portfolio once the
    universe changes under them. That is a fault in the library, not a
    choice to make quietly: the name is withheld for that portfolio and the
    console shows both as broken."""
    fb = _fallbackOf('SMA Sov. Agency')
    a = sleeveRepo.addEdition(fb['id'], 'A', [{'currency': 'GBP', 'riskLevel': 'Moderate'}], _products(), user='desk')
    b = sleeveRepo.addEdition(fb['id'], 'B', [{'currency': 'GBP', 'riskLevel': 'Agg'}], _products(), user='desk')
    try:
        # force the clash without touching the universe: write B's rule as
        # Moderate straight into the table, past validation
        conn = sqlite3.connect(sleeveRepo.dbPath())
        conn.execute("UPDATE sleeveRules SET riskLevel = 'Moderate' WHERE sleeveId = ?", (b['id'],))
        conn.commit(); conn.close()
        served = [s['name'] for s in sleeves.listSleeves(C, V, GBP_MOD_FULL)]
        assert 'SMA Sov. Agency' not in served, 'ambiguous, so withheld'
        assert 'SMA Sov. Agency' in [s['name'] for s in sleeves.listSleeves(C, V, USD_MOD_FULL)]
        flagged = {e['label']: e['problems'] for e in sleeveRepo.listAll(V)
                   if e['category'] == C and e['name'] == 'SMA Sov. Agency'}
        assert any('Overlaps the B edition' in p for p in flagged['A'])
        assert any('Overlaps the A edition' in p for p in flagged['B'])
        assert flagged[''] == []
    finally:
        _archive(a['id'], b['id'])


# ---- the seed, the interchange and the migration ---------------------------------

def test_the_old_seed_loads_unchanged_as_fallbacks():
    store = sleeveRepo.describe()
    assert store['schemaVersion'] == 3
    entries = sleeveRepo.listAll()
    assert len(entries) == 102 and all(e['fallback'] for e in entries)
    assert sleeveRepo.census()['editions'] == 0


def test_a_delivery_with_editions_and_rules_round_trips(tmp_path, monkeypatch):
    seed = tmp_path / 'sleeves.csv'
    rules = tmp_path / 'sleeveRules.csv'
    with open(seed, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(sleeveRepo.SEED_COLUMNS_EDITIONS)
        w.writerow([V, C, 'Bonds', '', A_PRODUCT, 1.0])
        w.writerow([V, C, 'Bonds', 'GBP', A_PRODUCT, 0.5])
        w.writerow([V, C, 'Bonds', 'GBP', ANOTHER, 0.5])
        w.writerow([V, C, 'Bonds', 'EUR Mod / GBP Full', ANOTHER, 1.0])
    with open(rules, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(sleeveRepo.RULE_COLUMNS)
        w.writerow([V, C, 'Bonds', 'GBP', 'GBP', '', 'Core|ex-Alts|ex-HFs|NA'])
        w.writerow([V, C, 'Bonds', 'EUR Mod / GBP Full', 'EUR', 'Moderate', ''])
        w.writerow([V, C, 'Bonds', 'EUR Mod / GBP Full', 'GBP', '', 'Full'])
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'fresh.db'))
    monkeypatch.setenv('SCENARIO_SLEEVES_SEED', str(seed))
    entries = sleeveRepo.listAll(V)
    assert sorted((e['label'], e['applies']) for e in entries) == [
        ('', None), ('EUR Mod / GBP Full', 20), ('GBP', 29)]
    assert sleeveRepo.describe()['sleeves'] == 3
    assert sleeveRepo.census()['editions'] == 2
    # resolution from a delivered library
    assert [s['id'] for s in sleeves.listSleeves(C, V, GBP_MOD_FULL)] == \
        [[e for e in entries if e['label'] == 'EUR Mod / GBP Full'][0]['id']]
    assert [s['id'] for s in sleeves.listSleeves(C, V, EUR_MOD_CORE)] == \
        [[e for e in entries if e['label'] == 'EUR Mod / GBP Full'][0]['id']], 'EUR Moderate matches'
    assert [s['id'] for s in sleeves.listSleeves(C, V, USD_MOD_FULL)] == \
        [[e for e in entries if e['label'] == ''][0]['id']], 'nothing names USD: the fallback'
    # export and re-import through the CLI, rules file beside
    out = tmp_path / 'out.csv'
    assert sleeveTools.main(['--export', str(out)]) == 0
    assert (tmp_path / 'out.rules.csv').exists()
    with open(out) as fh:
        assert next(csv.reader(fh)) == sleeveRepo.SEED_COLUMNS_EDITIONS
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'again.db'))
    monkeypatch.setenv('SCENARIO_SLEEVES_SEED', str(tmp_path / 'absent.csv'))
    assert sleeveTools.main(['--import', str(out)]) == 0
    assert sorted((e['label'], e['applies']) for e in sleeveRepo.listAll(V)) == [
        ('', None), ('EUR Mod / GBP Full', 20), ('GBP', 29)]
    assert sorted(sleeveRepo.exportRuleRows()) == sorted([
        (V, C, 'Bonds', 'EUR Mod / GBP Full', 'EUR', 'Moderate', ''),
        (V, C, 'Bonds', 'EUR Mod / GBP Full', 'GBP', '', 'Full'),
        (V, C, 'Bonds', 'GBP', 'GBP', '', 'Core|ex-Alts|ex-HFs|NA')])


def test_a_delivery_that_overlaps_or_names_the_unknown_is_refused_whole(tmp_path, monkeypatch):
    seed = tmp_path / 'sleeves.csv'
    rules = tmp_path / 'sleeveRules.csv'
    with open(seed, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(sleeveRepo.SEED_COLUMNS_EDITIONS)
        w.writerow([V, C, 'Bonds', 'GBP', A_PRODUCT, 1.0])
        w.writerow([V, C, 'Bonds', 'GBP ex-Alts', ANOTHER, 1.0])
    with open(rules, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(sleeveRepo.RULE_COLUMNS)
        w.writerow([V, C, 'Bonds', 'GBP', 'GBP', '', ''])
        w.writerow([V, C, 'Bonds', 'GBP ex-Alts', 'GBP', '', 'ex-Alts'])
    rows = list(sleeveRepo.readSeedRows(str(seed)))
    ruleRows = list(sleeveRepo.readRuleRows(str(rules)))
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(tmp_path / 'fresh.db'))
    monkeypatch.setenv('SCENARIO_SLEEVES_SEED', str(tmp_path / 'absent.csv'))
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.importRows(rows, ruleRows=ruleRows)
    assert exc.value.field == 'rules' and 'Overlaps' in exc.value.message
    assert sleeveRepo.describe()['sleeves'] == 0, 'nothing written'
    ruleRows[0]['currency'] = ['JPY']
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.importRows(rows, ruleRows=ruleRows)
    assert 'JPY' in exc.value.message
    # a rule for an edition with no product rows is a mistake in the delivery
    with pytest.raises(ValidationError) as exc:
        sleeveRepo.importRows(rows, ruleRows=[dict(ruleRows[0], edition='Nobody')])
    assert 'no product rows' in exc.value.message


_V2_DDL = """
CREATE TABLE sleeves (
    id INTEGER PRIMARY KEY, variant TEXT NOT NULL, category TEXT NOT NULL, name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', createdBy TEXT NOT NULL DEFAULT '', createdAt TEXT NOT NULL,
    updatedBy TEXT NOT NULL DEFAULT '', updatedAt TEXT NOT NULL,
    deletedBy TEXT NOT NULL DEFAULT '', deletedAt TEXT NOT NULL DEFAULT '');
CREATE TABLE sleeveProducts (sleeveId INTEGER NOT NULL REFERENCES sleeves(id) ON DELETE CASCADE,
    position INTEGER NOT NULL, productId TEXT NOT NULL, weight REAL NOT NULL,
    PRIMARY KEY (sleeveId, productId));
CREATE TABLE sleeveHistory (id INTEGER PRIMARY KEY, sleeveId INTEGER NOT NULL, revision INTEGER NOT NULL,
    action TEXT NOT NULL, variant TEXT NOT NULL, category TEXT NOT NULL, name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', products TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL, UNIQUE (sleeveId, revision));
CREATE UNIQUE INDEX sleeves_live_name ON sleeves (variant, category, name) WHERE deletedAt = '';
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta VALUES ('schemaVersion', '2');
INSERT INTO meta VALUES ('seededAt', '2026-01-01T00:00:00');
INSERT INTO meta VALUES ('seededFrom', 'old');
"""


def test_a_version_two_store_migrates_in_place_and_its_sleeves_become_fallbacks(tmp_path, monkeypatch):
    path = tmp_path / 'v2.db'
    conn = sqlite3.connect(str(path))
    conn.executescript(_V2_DDL)
    conn.execute("INSERT INTO sleeves (id, variant, category, name, createdAt, updatedAt) VALUES (7, ?, ?, 'Old Bonds', 't', 't')", (V, C))
    conn.execute("INSERT INTO sleeveProducts VALUES (7, 0, ?, 1.0)", (A_PRODUCT,))
    conn.execute("INSERT INTO sleeveHistory (sleeveId, revision, action, variant, category, name, products, at) "
                 "VALUES (7, 1, 'baseline', ?, ?, 'Old Bonds', ?, 't')", (V, C, json.dumps([[A_PRODUCT, 1.0]])))
    conn.commit(); conn.close()
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(path))
    monkeypatch.setenv('SCENARIO_SLEEVES_SEED', str(tmp_path / 'absent.csv'))

    store = sleeveRepo.describe()
    assert store['schemaVersion'] == 3 and store['sleeves'] == 1
    conn = sqlite3.connect(str(path))
    columns = {r[1] for r in conn.execute('PRAGMA table_info(sleeves)')}
    history = {r[1] for r in conn.execute('PRAGMA table_info(sleeveHistory)')}
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(sleeves)")}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert 'label' in columns and {'label', 'rules'} <= history
    assert 'sleeveRules' in tables
    assert 'sleeves_live_edition' in indexes and 'sleeves_live_name' not in indexes
    old = sleeveRepo.getSleeve(7)
    assert old['fallback'] and old['label'] == '' and old['rules'] == []
    assert sleeveRepo.history(7)[0]['label'] == '' and sleeveRepo.history(7)[0]['rules'] == []
    # and the migrated store takes an edition like any other
    made = sleeveRepo.addEdition(7, 'GBP', [{'currency': 'GBP'}], _products(), user='desk')
    assert made['applies'] == 43
    assert sleeveRepo.describe()['schemaVersion'] == 3, 'opening again is a no-op'


# ---- the endpoints ---------------------------------------------------------------

def test_the_sleeve_endpoint_resolves_by_key_and_says_nothing_of_editions(monkeypatch):
    monkeypatch.setenv('SCENARIO_ADAPTER', 'fixtures')
    fb = _fallbackOf('ETFs Only')
    ed = sleeveRepo.addEdition(fb['id'], 'GBP', [{'currency': 'GBP'}], _products((A_PRODUCT, 1.0)), user='desk')
    try:
        gbp = dashboardRouter.listSleeves(C, V, 'GBP', 'Hedged', key=GBP_MOD_FULL.toStr())
        usd = dashboardRouter.listSleeves(C, V, 'USD', 'Hedged', key=USD_MOD_FULL.toStr())
        none = dashboardRouter.listSleeves(C, V, 'GBP', 'Hedged')
        pick = lambda body: [s for s in body['sleeves'] if s['name'] == 'ETFs Only'][0]
        assert pick(gbp)['id'] == ed['id'] and pick(usd)['id'] == fb['id'] and pick(none)['id'] == fb['id']
        for body in (gbp, usd, none):
            for s in body['sleeves']:
                assert 'label' not in s and 'rules' not in s and 'edition' not in s
        bad = dashboardRouter.listSleeves(C, V, 'GBP', 'Hedged', key='nonsense')
        assert bad.status_code == 422 and json.loads(bad.body)['field'] == 'key'
    finally:
        _archive(ed['id'])


def test_the_console_payload_and_the_two_new_endpoints(monkeypatch):
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'alice')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    body = dashboardRouter.getRepository(caller=_caller('alice'))
    assert body['ruleVocabulary'] == sleeveRules.vocabulary()
    assert body['ruleFields'] == ['currency', 'riskLevel', 'allocationType']
    entry = [e for e in body['sleeves'] if e['category'] == C][0]
    assert {'label', 'rules', 'fallback', 'applies'} <= set(entry)

    fb = _fallbackOf('ETFs Only')
    preview = dashboardRouter.previewApplicability(
        {'variant': V, 'category': C, 'name': 'ETFs Only', 'label': 'GBP',
         'rules': [{'currency': ['GBP']}]}, caller=_caller('alice'))
    assert preview['applies'] == 43 and preview['universe'] == 172 and preview['overlaps'] == []
    assert len(preview['appliesTo']) == 43 and preview['appliesTo'][0].startswith('GBP|')
    refused = dashboardRouter.previewApplicability(
        {'variant': V, 'category': C, 'name': 'ETFs Only', 'rules': [{'currency': ['JPY']}]},
        caller=_caller('alice'))
    assert refused.status_code == 422 and json.loads(refused.body)['field'] == 'rules'

    made = dashboardRouter.addRepositoryEdition(
        fb['id'], {'label': 'GBP', 'rules': [{'currency': ['GBP']}], 'products': _products()},
        caller=_caller('alice'))
    try:
        assert made['sleeve']['label'] == 'GBP' and made['sleeve']['applies'] == 43
        clash = dashboardRouter.previewApplicability(
            {'variant': V, 'category': C, 'name': 'ETFs Only', 'label': 'GBP ex-Alts',
             'rules': [{'currency': ['GBP'], 'allocationType': ['ex-Alts']}]}, caller=_caller('alice'))
        assert [(o['label'], o['count']) for o in clash['overlaps']] == [('GBP', 7)]
        # the update route leaves the edition alone unless told
        kept = dashboardRouter.updateRepositorySleeve(
            made['sleeve']['id'], {'name': 'ETFs Only', 'products': _products((A_PRODUCT, 1.0))},
            caller=_caller('alice'))
        assert kept['sleeve']['label'] == 'GBP' and kept['sleeve']['rules'] == [{'currency': ['GBP']}]
        moved = dashboardRouter.updateRepositorySleeve(
            made['sleeve']['id'], {'name': 'ETFs Only', 'products': _products((A_PRODUCT, 1.0)),
                                   'label': 'Sterling', 'rules': [{'currency': ['GBP'], 'riskLevel': ['Moderate']}]},
            caller=_caller('alice'))
        assert moved['sleeve']['label'] == 'Sterling' and moved['sleeve']['applies'] == 6
    finally:
        _archive(made['sleeve']['id'])


def test_the_new_routes_are_admin_only_and_the_count_is_thirty():
    found = {}
    for route in dashboardRouter.router.routes:
        if route.path.startswith('/scenario'):
            found[(route.path, tuple(sorted(route.methods)))] = [
                d.call.__name__ for d in route.dependant.dependencies]
    assert len(found) == 30
    assert 'requireAdmin' in found[('/scenario/repository/sleeves/{sleeveId}/editions', ('POST',))]
    assert 'requireAdmin' in found[('/scenario/repository/applicability', ('POST',))]


# ---- the scenario: a choice is judged against the base portfolio ----------------

def _scenarioOn(key):
    from cyrus_pmg.pmgService.scenario import scenarioStore
    from cyrus_pmg.pmgService.scenario.types import MandateInput
    state = scenarioStore.createScenario(MandateInput(1e9, 1e9, 'M. Aldridge — Zurich'),
                                         BasisInput(currency=key.currency, hedging='Hedged'))
    scenarioStore.updateScenario(state['id'], variant=V)
    scenarioStore.recordColumn(state['id'], key, 'base')
    return state['id']


def test_a_sleeve_choice_is_validated_for_the_base_portfolio(monkeypatch):
    monkeypatch.setenv('SCENARIO_ADAPTER', 'fixtures')
    made = sleeveRepo.createSleeve(V, C, 'Sterling Only', _products(), user='desk',
                                   label='GBP', rules=[{'currency': 'GBP'}])
    try:
        gbp = _scenarioOn(GBP_MOD_FULL)
        ok = dashboardRouter.updateScenario(gbp, {'sleeves': {C: 'Sterling Only'}}, caller=_caller('alice'))
        assert ok['sleeves'] == {C: 'Sterling Only'}
        usd = _scenarioOn(USD_MOD_FULL)
        refused = dashboardRouter.updateScenario(usd, {'sleeves': {C: 'Sterling Only'}}, caller=_caller('alice'))
        assert refused.status_code == 422
        body = json.loads(refused.body)
        assert body['field'] == 'sleeves' and 'Sterling Only' in body['error']
        # a fallback answers for both, as it always did
        for scenarioId in (gbp, usd):
            fine = dashboardRouter.updateScenario(scenarioId, {'sleeves': {C: 'ETFs Only'}}, caller=_caller('alice'))
            assert fine['sleeves'] == {C: 'ETFs Only'}
    finally:
        _archive(made['id'])


def test_the_export_refuses_a_name_the_base_has_no_edition_of(monkeypatch):
    """A choice made under one base and carried to another the name has no
    edition for cannot be built. The gate says so rather than exporting a
    category with nothing in it."""
    monkeypatch.setenv('SCENARIO_ADAPTER', 'fixtures')
    from cyrus_pmg.pmgService.scenario import rules as R, scenarioStore
    made = sleeveRepo.createSleeve(V, C, 'Sterling Only', _products(), user='desk',
                                   label='GBP', rules=[{'currency': 'GBP'}])
    try:
        scenarioId = _scenarioOn(GBP_MOD_FULL)
        state = scenarioStore.getScenario(scenarioId)
        # every other category gets its first fallback, this one the edition
        from cyrus_pmg.pmgService.scenario.registry import getScenarioPort
        port = getScenarioPort()
        result = port.resolve_portfolio(BasisInput(currency='GBP', hedging='Hedged'), GBP_MOD_FULL)
        chosen = {}
        for c in result['categories']:
            if c['name'] in R.AUTO_SLEEVE_CATEGORIES:
                continue
            under = R.sleeveCategory(c['name'])
            chosen[under] = 'Sterling Only' if under == C else sleeves.listSleeves(under, V, GBP_MOD_FULL)[0]['name']
        scenarioStore.updateScenario(scenarioId, sleeves=chosen)
        # move the base under the choice, past the page's own re-validation
        scenarioStore.recordColumn(scenarioId, PortfolioKey('GBP', 'Moderate', 'Full', False), 'base')
        scenarioStore.updateScenario(scenarioId, basis=BasisInput(currency='USD', hedging='Hedged'))
        scenarioStore.recordColumn(scenarioId, USD_MOD_FULL, 'base')
        refused = dashboardRouter.exportScenario(scenarioId, caller=_caller('alice'))
        assert refused.status_code == 422
        body = json.loads(refused.body)
        assert body['field'] == 'sleeves' and 'not offered for this portfolio' in body['error']
        assert C in body['error']
    finally:
        _archive(made['id'])
