"""D126: this repository consumes a delivered bake, and computes nothing.

The analytics library that produces the bake lives in epsilon-phi. Nothing
here imports it, names it, or can fall through to it: the adapter registry
offers fixtures and baked only, the baked adapter has no delegate, the bake
CLI bakes from fixtures alone, and a production cycle against the delivered
store never brings the library into the process.
"""

import inspect
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICE = os.path.abspath(os.path.join(HERE, '..'))
PACKAGE = os.path.join(SERVICE, 'cyrus_pmg')
SCENARIO = os.path.join(PACKAGE, 'pmgService', 'scenario')
FORBIDDEN = ('epsilonPhi', 'liveAdapter', 'LiveScenarioPort', 'SAA_ENGINE',
             'SCENARIO_BAKED_FALLBACK', 'from .engine', 'import engine')


def _sources():
    for root in (PACKAGE, os.path.join(SERVICE, 'tools')):
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            for name in files:
                if name.endswith('.py'):
                    yield os.path.join(dirpath, name)
    yield os.path.join(SERVICE, 'start_dashboard.sh')
    yield os.path.join(SERVICE, 'dashboard.env.defaults')


def test_no_source_names_the_analytics_library_or_a_live_path():
    offending = []
    for path in _sources():
        text = open(path, encoding='utf-8').read()
        offending += [(os.path.relpath(path, SERVICE), word)
                      for word in FORBIDDEN if word in text]
    assert offending == []
    assert not os.path.exists(os.path.join(SCENARIO, 'engine.py'))
    assert not os.path.exists(os.path.join(SCENARIO, 'liveAdapter.py'))


def test_the_registry_offers_fixtures_and_baked_only(monkeypatch):
    from cyrus_pmg.pmgService.scenario import registry
    assert registry.ADAPTERS == ('fixtures', 'baked')
    monkeypatch.setattr(registry, '_port', None)      # restored after the test
    monkeypatch.setenv('SCENARIO_ADAPTER', 'live')
    with pytest.raises(RuntimeError):
        registry.getScenarioPort()


def test_the_baked_adapter_has_nothing_to_fall_through_to():
    from cyrus_pmg.pmgService.scenario import bake
    from cyrus_pmg.pmgService.scenario.bakedAdapter import BakedScenarioPort
    assert 'delegate' not in inspect.signature(BakedScenarioPort).parameters
    with pytest.raises(ValueError):
        bake._makePort('live')


def test_a_production_cycle_against_the_delivered_bake_never_loads_the_library(tmp_path):
    """The PORTING.md §14.3 probe, in a subprocess so this process's own
    imports cannot mask it: schema, create and resolve under
    SCENARIO_ADAPTER=baked against the store this repository ships, and the
    library absent from sys.modules at the end."""
    store = os.path.join(SERVICE, 'var', 'baked')
    assert os.path.exists(os.path.join(store, 'manifest.json')), 'the delivered bake'
    script = (
        "import sys\n"
        "from fastapi.testclient import TestClient\n"
        "from cyrus_pmg.pmgService.isgPMGService import app\n"
        "c = TestClient(app)\n"
        "h = {'X-Kerberos': 'bob'}\n"
        "schema = c.get('/api/v1/scenario/schema?currency=USD&hedging=Hedged', headers=h)\n"
        "assert schema.status_code == 200, schema.text\n"
        "assert schema.json()['dataInfo']['adapter'] == 'baked', schema.json()['dataInfo']\n"
        "made = c.post('/api/v1/scenario', headers=h, json={\n"
        "    'mandate': {'topAccountSize': 5e7, 'mandateSize': 5e7,\n"
        "                'primaryPwa': 'A. Castellanos \u2014 Madrid'},\n"
        "    'basis': {'currency': 'USD', 'hedging': 'Hedged'}})\n"
        "assert made.status_code == 200, made.text\n"
        "from cyrus_pmg.pmgService.scenario.sleeves import VARIANTS\n"
        "chosen = c.put('/api/v1/scenario/' + made.json()['id'], headers=h,\n"
        "               json={'variant': VARIANTS[0]})\n"
        "assert chosen.status_code == 200, chosen.text\n"
        "key = {'currency': 'USD', 'riskLevel': 'Moderate', 'allocationType': 'Full',\n"
        "       'excludeRealAssets': False}\n"
        "resolved = c.post('/api/v1/scenario/' + made.json()['id'] + '/portfolio',\n"
        "                  headers=h, json={'key': key, 'role': 'base'})\n"
        "assert resolved.status_code == 200, resolved.text\n"
        "assert resolved.json()['portfolio']['metrics']['volatilityPct'] > 0\n"
        "print('LEAKED', [m for m in sys.modules if m.split('.')[0] == 'epsilonPhi'])\n"
    )
    environment = dict(os.environ, PYTHONPATH=SERVICE, SCENARIO_ADAPTER='baked',
                       SCENARIO_BAKED_DIR=store, PMG_ALLOWED_KERBEROS='bob',
                       SCENARIO_STORE_DIR=str(tmp_path / 'store'),
                       SCENARIO_SLEEVES_DB=str(tmp_path / 'sleeves.db'),
                       SCENARIO_REGISTER_DB=str(tmp_path / 'proposals.db'))
    finished = subprocess.run([sys.executable, '-c', script], cwd=SERVICE,
                              capture_output=True, text=True, env=environment)
    assert finished.returncode == 0, finished.stderr[-3000:]
    assert 'LEAKED []' in finished.stdout, finished.stdout
