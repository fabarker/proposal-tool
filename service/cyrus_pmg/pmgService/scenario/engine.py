"""The single seam onto the SAA analytics library.

Every symbol the Proposal Tool needs from the analytics engine is named here
and nowhere else in the package. Porting to a host whose library differs in
name or in internal layout is an edit to ``_PACKAGE`` and ``_SYMBOLS`` below —
one file, no call-site changes.

**Resolution is lazy by construction, and call sites must keep it that way.**
Import from this module *inside* the function that needs the symbol, never at
module scope::

    def build_export(self, ...):
        from .engine import Reporting        # right: resolved on use

    from .engine import Reporting           # WRONG: resolves at import time

That distinction is load-bearing. The engine's ``setup()`` is expensive — a
database connection and the factor models, 12-14s — and two things depend on
never paying it: a fixtures-configured service, and ``registry`` constructing
the live adapter as the baked adapter's fallback delegate without ever
connecting it. A module-level import here would connect it at process start.

The lookup is memoised, so the cost of the indirection is one dict hit per
call after the first.
"""

from __future__ import annotations

import importlib
import os
import threading

#: Root package of the SAA analytics library. A host whose library is named
#: differently sets SAA_ENGINE_PACKAGE rather than editing code; the default is
#: the epsilon-phi library this was built and measured against.
_PACKAGE = os.getenv('SAA_ENGINE_PACKAGE', 'epsilonPhi')

#: Public name -> (module path beneath the root package, attribute).
#: The whole dependency surface: six symbols, five of them types.
# ``Reporting`` used to be here. The export no longer uses it (D67): the
# workbook is written from the resolved payloads, so the only reason this
# package ever reaches the library is to COMPUTE analytics, never to lay
# them out.
_SYMBOLS = {
    'CAppConfig': ('core.config.appConfig', 'CAppConfig'),
    'DATAVERSION': ('core.env.Env', 'DATAVERSION'),
    'ContextCreator': ('core.schema.Schema', 'ContextCreator'),
    'SAAPortfolio': ('core.portfolio.SAAPortfolio', 'SAAPortfolio'),
    'AssetReturnEstimator': ('core.estimator.assetReturnEstimator',
                             'AssetReturnEstimator'),
}

_lock = threading.Lock()
_resolved = {}


def packageName() -> str:
    """The configured root package, for diagnostics and error messages."""
    return _PACKAGE


def __getattr__(name):
    """Resolve a symbol on first access (PEP 562).

    This is what makes ``from .engine import SAAPortfolio`` inside a function
    behave exactly as the direct import it replaced: nothing is imported until
    the line runs.
    """
    try:
        module, attribute = _SYMBOLS[name]
    except KeyError:
        raise AttributeError(
            '{!r} is not a symbol this package takes from the analytics '
            'library; add it to _SYMBOLS in engine.py.'.format(name))
    with _lock:
        if name not in _resolved:
            path = '{}.{}'.format(_PACKAGE, module)
            try:
                imported = importlib.import_module(path)
            except ImportError as exc:
                raise ImportError(
                    'Could not import {} from the SAA analytics library at '
                    '{!r}: {}. Set SAA_ENGINE_PACKAGE if the host names it '
                    'differently.'.format(attribute, path, exc))
            _resolved[name] = getattr(imported, attribute)
    return _resolved[name]


def __dir__():
    return sorted(list(_SYMBOLS) + ['packageName'])
