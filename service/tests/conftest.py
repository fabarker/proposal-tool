"""Session-wide fixtures.

The sleeve repository is a database the service writes (D57). The suite must
never touch the developer's own store, so every test session gets a fresh
file under pytest's temp root, seeded from the packaged extract exactly as a
first run would be. Tests that exercise saves get the same library everyone
else does and leave it behind for the next session to discard.
"""

import os

import pytest


@pytest.fixture(scope='session', autouse=True)
def _isolatedSleeveStore(tmp_path_factory):
    path = tmp_path_factory.mktemp('sleeves') / 'sleeves.db'
    previous = os.environ.get('SCENARIO_SLEEVES_DB')
    os.environ['SCENARIO_SLEEVES_DB'] = str(path)
    # and an isolated proposal register beside it (D69)
    register = tmp_path_factory.mktemp('register') / 'proposals.db'
    previousRegister = os.environ.get('SCENARIO_REGISTER_DB')
    os.environ['SCENARIO_REGISTER_DB'] = str(register)
    yield str(path)
    if previous is None:
        os.environ.pop('SCENARIO_SLEEVES_DB', None)
    else:
        os.environ['SCENARIO_SLEEVES_DB'] = previous
    if previousRegister is None:
        os.environ.pop('SCENARIO_REGISTER_DB', None)
    else:
        os.environ['SCENARIO_REGISTER_DB'] = previousRegister
