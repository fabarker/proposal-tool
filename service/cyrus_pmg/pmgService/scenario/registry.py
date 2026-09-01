"""Adapter selection - the one switch the host flips.

SCENARIO_ADAPTER chooses the ScenarioPort implementation:

    fixtures    static data, no analytics (spec 4.4)
    live        live analytics over the SAA library (see engine.py)
    baked       precomputed analytics served from disk (the default
                for production: a cold process answers in ~1ms). Falls back to
                the live adapter for anything unbaked unless
                SCENARIO_BAKED_FALLBACK=0, which keeps the service entirely
                free of a database.

The instance is a process-level singleton so adapter caches live for the
process. Selection is configuration, not structure: the host sets the
variable in its environment defaults.
"""

from __future__ import annotations

import os
import threading

_lock = threading.Lock()
_port = None


def getScenarioPort():
    """The active ScenarioPort implementation."""
    global _port
    with _lock:
        if _port is None:
            name = os.getenv('SCENARIO_ADAPTER', 'fixtures').strip().lower()
            if name == 'live':
                from .liveAdapter import LiveScenarioPort
                _port = LiveScenarioPort()
            elif name == 'fixtures':
                from .fixturesAdapter import FixturesScenarioPort
                _port = FixturesScenarioPort()
            elif name == 'baked':
                from .bakedAdapter import BakedScenarioPort
                delegate = None
                if os.getenv('SCENARIO_BAKED_FALLBACK', '1').strip() != '0':
                    from .liveAdapter import LiveScenarioPort
                    # constructed, not connected: every engine symbol resolves
                    # lazily through engine.py, so a fully baked service never
                    # touches the database
                    delegate = LiveScenarioPort()
                _port = BakedScenarioPort(delegate=delegate)
            else:
                raise RuntimeError(
                    'Unknown SCENARIO_ADAPTER {!r}; expected fixtures, live '
                    'or baked.'.format(name))
        return _port
