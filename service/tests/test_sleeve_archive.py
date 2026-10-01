"""Every save of a changed sleeve archives the version it replaced (D152).

Each sleeve carries the moment its current version was created (createdAt,
re-stamped on every save that changes it) and the moment the sleeve itself
first was (firstCreatedAt). The replaced version is an archived row of its
own, stamped with its own created time and pointing at its successor."""

import json
import sqlite3

import pytest

from cyrus_pmg.pmgService.scenario import sleeveRepo
from cyrus_pmg.pmgService.scenario.types import ValidationError

V, C = 'PMG ESG', 'Public Equity'
A = 'gs-us-corporate-bond-fund'
B = 'ashfield-global-credit-fund'


@pytest.fixture
def sleeve():
    made = sleeveRepo.createSleeve(V, C, 'Archive On Save', [{'productId': A, 'weight': 1.0}],
                                   note='first', user='alice')
    yield made
    try:
        sleeveRepo.deleteSleeve(made['id'], user='alice')
    except ValidationError:
        pass


def _versions(sleeveId):
    return [s for s in sleeveRepo.listArchived() if s['supersededBy'] == sleeveId]


def test_a_new_sleeve_carries_its_created_stamps(sleeve):
    assert sleeve['createdAt'] and sleeve['firstCreatedAt'] == sleeve['createdAt']
    assert sleeve['supersededBy'] is None and _versions(sleeve['id']) == []


def test_saving_a_change_archives_the_version_it_replaced(sleeve):
    upd = sleeveRepo.updateSleeve(sleeve['id'], 'Archive On Save',
                                  [{'productId': A, 'weight': 0.5}, {'productId': B, 'weight': 0.5}],
                                  note='second', user='bob')
    kept = upd['archivedVersion']
    assert kept['archived'] and kept['supersededBy'] == sleeve['id']
    assert kept['createdAt'] == sleeve['createdAt'] and kept['createdBy'] == 'alice'
    assert kept['archivedBy'] == 'bob' and kept['archivedAt'] == upd['createdAt']
    assert [p['productId'] for p in kept['products']] == [A] and kept['note'] == 'first'
    # the live sleeve is the new version, same id, freshly stamped
    assert upd['id'] == sleeve['id'] and upd['createdBy'] == 'bob'
    assert upd['createdAt'] >= sleeve['createdAt'] and upd['firstCreatedAt'] == sleeve['createdAt']
    assert [s['id'] for s in _versions(sleeve['id'])] == [kept['id']]
    # a second change keeps a second version, newest first in the archive
    again = sleeveRepo.updateSleeve(sleeve['id'], 'Archive On Save',
                                    [{'productId': B, 'weight': 1.0}], user='carol')
    assert again['archivedVersion']['createdAt'] == upd['createdAt']
    assert len(_versions(sleeve['id'])) == 2


def test_a_save_that_changes_nothing_archives_nothing(sleeve):
    same = sleeveRepo.updateSleeve(sleeve['id'], 'Archive On Save',
                                   [{'productId': A, 'weight': 1.0}], note='first', user='bob')
    assert 'archivedVersion' not in same and _versions(sleeve['id']) == []
    assert same['createdAt'] == sleeve['createdAt']


def test_a_revert_archives_the_version_it_replaced(sleeve):
    sleeveRepo.updateSleeve(sleeve['id'], 'Archive On Save', [{'productId': B, 'weight': 1.0}],
                            user='bob')
    back = sleeveRepo.revertSleeve(sleeve['id'], 1, user='bob')
    assert [p['productId'] for p in back['products']] == [A]
    assert len(_versions(sleeve['id'])) == 2


def test_an_earlier_version_cannot_be_restored_over_the_live_one(sleeve):
    kept = sleeveRepo.updateSleeve(sleeve['id'], 'Archive On Save',
                                   [{'productId': B, 'weight': 1.0}], user='bob')['archivedVersion']
    with pytest.raises(ValidationError):
        sleeveRepo.restoreSleeve(kept['id'], user='bob')
    # once the live one is archived, the earlier version comes back, no longer superseded
    sleeveRepo.deleteSleeve(sleeve['id'], user='bob')
    back = sleeveRepo.restoreSleeve(kept['id'], user='bob')
    assert not back['archived'] and back['supersededBy'] is None
    sleeveRepo.deleteSleeve(kept['id'], user='bob')


def test_a_version_four_store_gains_the_stamps_from_its_history(tmp_path, monkeypatch):
    path = tmp_path / 'v4.db'
    monkeypatch.setenv('SCENARIO_SLEEVES_DB', str(path))
    monkeypatch.setenv('SCENARIO_SLEEVES_SEED', str(tmp_path / 'absent.csv'))
    sleeveRepo.describe()                                   # a current, empty file
    conn = sqlite3.connect(str(path))
    # wind it back to 4: the shape before D152
    conn.executescript("""
        CREATE TABLE s4 AS SELECT id, variant, category, name, label, note, createdBy, createdAt,
            updatedBy, updatedAt, deletedBy, deletedAt FROM sleeves;
        DROP TABLE sleeves; ALTER TABLE s4 RENAME TO sleeves;
        UPDATE meta SET value = '4' WHERE key = 'schemaVersion';""")
    conn.execute("INSERT INTO sleeves VALUES (3, ?, ?, 'Old', '', '', 'a', '2026-01-01T00:00:00Z', "
                 "'b', '2026-03-01T00:00:00Z', '', '')", (V, C))
    conn.execute("INSERT INTO sleeveProducts VALUES (3, 0, ?, 1.0)", (A,))
    for rev, action, at in ((1, 'created', '2026-01-01T00:00:00Z'), (2, 'updated', '2026-02-01T00:00:00Z'),
                            (3, 'deleted', '2026-02-10T00:00:00Z'), (4, 'restored', '2026-03-01T00:00:00Z')):
        conn.execute("INSERT INTO sleeveHistory (sleeveId, revision, action, variant, category, name, "
                     "products, at) VALUES (3, ?, ?, ?, ?, 'Old', ?, ?)",
                     (rev, action, V, C, json.dumps([[A, 1.0]]), at))
    conn.commit(); conn.close()

    assert sleeveRepo.describe()['schemaVersion'] == 6          # and the overlays' (D155)
    old = sleeveRepo.getSleeve(3)
    assert old['firstCreatedAt'] == '2026-01-01T00:00:00Z'
    assert old['createdAt'] == '2026-02-01T00:00:00Z', 'the last save that changed it, not the restore'
    assert old['supersededBy'] is None
