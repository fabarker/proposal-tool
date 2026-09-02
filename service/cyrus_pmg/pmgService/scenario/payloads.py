"""Wire payload shaping shared by both ScenarioPort implementations.

The PortfolioResult rows come from the strategic universe (D54), which maps
each held ticker to its reporting name and category (spec 2.2). The ticker is
a join key and never enters a payload. Numbers differ per adapter; the shape
does not.

Also home to the largest-remainder rounding of spec 8.4, because the export
sheet must reproduce the screen's printed figures exactly (spec 14.4). The
JavaScript mirror of ``roundWeightsLargestRemainder`` lives in the page's
implementation layer; the two must agree including the tie-break, which is
remainder descending, then original row order.
"""

from __future__ import annotations

import math

from . import universe
from .types import AnalyticsError, BasisInput, PortfolioKey
from .rules import portfolioHeader, portfolioName


def categoryRows(key: PortfolioKey) -> list:
    """The payload's category/asset structure for one portfolio.

    Read from the strategic universe - the supplying database's extract - not
    from a generated frame (D54). Raises LookupError when the portfolio is not
    offered; the router maps that to 422 because the availability set should
    have prevented it.
    """
    return universe.categoryRows(key)


def portfolioResult(basis: BasisInput, key: PortfolioKey, categories: list,
                    metrics: dict, stress: list, premia: list) -> dict:
    """Assemble one PortfolioResult payload (spec 3.4, resolve response)."""
    result = {
        'key': key.toDict(),
        'keyStr': key.toStr(),
        'name': portfolioName(basis, key),
        'header': portfolioHeader(key),
        'categories': categories,
        'metrics': metrics,
        'stress': stress,
        'premia': premia,
    }
    assertFinitePayload(result)
    return result


def assertFinitePayload(payload) -> None:
    """The finiteness guard of spec 15.8.

    Reconciliation tests pass happily on nonsense; a NaN that reaches a
    rendered surface is a silent lie. Every number leaving an adapter goes
    through this.
    """
    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, '{}.{}'.format(path, k))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, '{}[{}]'.format(path, i))
        elif isinstance(node, float) and not math.isfinite(node):
            raise AnalyticsError('Non-finite value at {}'.format(path))
    walk(payload, '$')


def roundWeightsLargestRemainder(exactPercents) -> list:
    """Round percents to 2dp so they sum to exactly 100.00 (spec 8.4).

    Floors every weight to 0.01% units, then hands the shortfall to the
    largest remainders; ties break by original row order. Only meaningful when
    the inputs sum to 100 - the caller gates on completeness.
    """
    units = [math.floor(w * 100 + 1e-9) for w in exactPercents]
    short = 10000 - sum(units)
    order = sorted(
        range(len(exactPercents)),
        key=lambda i: (-(exactPercents[i] * 100 - units[i]), i),
    )
    for n in range(max(0, short)):
        units[order[n % len(order)]] += 1
    return [u / 100.0 for u in units]
