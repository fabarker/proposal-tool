"""Concrete types behind the ScenarioPort contract.

The spec leaves these to the receiving project (scenario_port.py's docstring
says so); the shapes here follow the sections that define each surface:
BasisInput/MandateInput from the state shape (spec 5.3), PortfolioKey from
what identifies a portfolio (spec 2.1), the error types from the error
contract (spec 3.5).

Schema, PortfolioResult, Sleeve and Advisor travel as plain dicts - they are
wire payloads, assembled in payloads.py and the adapters, and binding them to
classes would add a serialisation layer without adding safety.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote


class ValidationError(Exception):
    """A mandate or request field failed validation -> HTTP 422 {error, field}."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


class ScenarioNotFound(Exception):
    """The scenario id is unknown or expired -> HTTP 404 {error}."""


class AnalyticsError(Exception):
    """The analytics engine failed to resolve a portfolio -> HTTP 502 {error}."""


@dataclass(frozen=True)
class BasisInput:
    """Scenario basis - fixed across every column (spec 2.1)."""

    currency: str
    hedging: str

    @staticmethod
    def fromDict(data) -> "BasisInput":
        return BasisInput(
            currency=str(data.get('currency', '')).strip(),
            hedging=str(data.get('hedging', '')).strip(),
        )

    def toDict(self) -> dict:
        return {'currency': self.currency, 'hedging': self.hedging}


@dataclass(frozen=True)
class MandateInput:
    """The client mandate (spec 2.4). Amounts are USD."""

    topAccountSize: float
    mandateSize: float
    primaryPwa: str

    @staticmethod
    def fromDict(data) -> "MandateInput":
        def _amount(field):
            value = data.get(field)
            try:
                return float(value)
            except (TypeError, ValueError):
                raise ValidationError(field, 'Enter an amount.')
        return MandateInput(
            topAccountSize=_amount('topAccountSize'),
            mandateSize=_amount('mandateSize'),
            primaryPwa=str(data.get('primaryPwa', '')).strip(),
        )

    def toDict(self) -> dict:
        return {
            'topAccountSize': self.topAccountSize,
            'mandateSize': self.mandateSize,
            'primaryPwa': self.primaryPwa,
        }


@dataclass(frozen=True)
class PortfolioKey:
    """What identifies a portfolio within a scenario (spec 2.1).

    The canonical string form - ``allocation|RE|TAA|riskLevel`` with the
    booleans as 0/1, e.g. ``Ex HFs|1|0|Mod Agg`` - is the availability-set
    entry, the column identity in the UI, and (URL-encoded) the path parameter
    of DELETE /api/scenario/{id}/portfolio/{key}.
    """

    allocation: str
    excludeRE: bool
    excludeTAA: bool
    riskLevel: str

    def toStr(self) -> str:
        return '|'.join([
            self.allocation,
            '1' if self.excludeRE else '0',
            '1' if self.excludeTAA else '0',
            self.riskLevel,
        ])

    def toPath(self) -> str:
        return quote(self.toStr(), safe='')

    def toDict(self) -> dict:
        return {
            'allocation': self.allocation,
            'excludeRE': self.excludeRE,
            'excludeTAA': self.excludeTAA,
            'riskLevel': self.riskLevel,
        }

    @staticmethod
    def fromStr(value: str) -> "PortfolioKey":
        parts = unquote(str(value)).split('|')
        if len(parts) != 4:
            raise ValidationError('key', 'Malformed portfolio key: {!r}'.format(value))
        return PortfolioKey(
            allocation=parts[0],
            excludeRE=parts[1] == '1',
            excludeTAA=parts[2] == '1',
            riskLevel=parts[3],
        )

    @staticmethod
    def fromDict(data) -> "PortfolioKey":
        if not isinstance(data, dict):
            raise ValidationError('key', 'The portfolio key must be an object.')
        try:
            return PortfolioKey(
                allocation=str(data['allocation']).strip(),
                excludeRE=bool(data['excludeRE']),
                excludeTAA=bool(data['excludeTAA']),
                riskLevel=str(data['riskLevel']).strip(),
            )
        except KeyError as exc:
            raise ValidationError('key', 'The portfolio key is missing {}.'.format(exc))
