"""Adapter selection - the one switch the host flips.

SCENARIO_ADAPTER chooses the ScenarioPort implementation:

    fixtures    synthetic analytics, no bake - tests and demos (spec 4.4)
    baked       the delivered bake, served from disk: the production
                setting (a cold process answers in ~1ms)

There is no other source of analytics. A portfolio the bake does not hold is
refused with AnalyticsError, never computed: the analytics library that
produces the bake lives in epsilon-phi, which delivers the store, and this
repository never imports it (D126).

The instance is a process-level singleton so adapter caches live for the
process. Selection is configuration, not structure: the host sets the
variable in its environment defaults.
"""

from __future__ import annotations

import os
import threading

ADAPTERS = ('fixtures', 'baked')

_lock = threading.Lock()
_port = None


def getScenarioPort():
    """The active ScenarioPort implementation."""
    global _port
    with _lock:
        if _port is None:
            name = os.getenv('SCENARIO_ADAPTER', 'fixtures').strip().lower()
            if name == 'fixtures':
                from .fixturesAdapter import FixturesScenarioPort
                _port = FixturesScenarioPort()
            elif name == 'baked':
                from .bakedAdapter import BakedScenarioPort
                _port = BakedScenarioPort()
            else:
                raise RuntimeError(
                    'Unknown SCENARIO_ADAPTER {!r}; expected one of {}.'.format(
                        name, ', '.join(ADAPTERS)))
        return _port
