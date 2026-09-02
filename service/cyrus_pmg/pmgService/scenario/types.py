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
from typing import Optional
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
    """What identifies a strategic portfolio (spec 2.1; D54).

    Four fields, all read out of the supplying database's portfolio NAME:

        currency | riskLevel | allocationType | excludeRealAssets

    The canonical string form - ``USD|Moderate|ex-HFs|1`` - is the
    availability-set entry, the column identity in the UI, the bake's key and
    (URL-encoded) the path parameter of DELETE /api/scenario/{id}/portfolio/{key}.

    An all-equity book has no alternatives to include or exclude, so it has no
    allocation type: both trailing fields are None, and print as ``NA`` -
    ``USD|All Equity|NA|NA``. Hedging is not a property of the portfolio and is
    not in this key; it joins the string only when the analytics are run
    (``withHedging``).
    """

    currency: str
    riskLevel: str
    allocationType: Optional[str]          # None for an all-equity book
    excludeRealAssets: Optional[bool]      # None for an all-equity book

    NA = 'NA'

    @property
    def isAllEquity(self) -> bool:
        return self.allocationType is None

    def toStr(self) -> str:
        if self.isAllEquity:
            return '|'.join([self.currency, self.riskLevel, self.NA, self.NA])
        return '|'.join([
            self.currency,
            self.riskLevel,
            self.allocationType,
            '1' if self.excludeRealAssets else '0',
        ])

    def withHedging(self, hedging: str) -> str:
        """The analysis key: this portfolio under one hedging assumption."""
        return '{}|{}'.format(self.toStr(), hedging)

    def toPath(self) -> str:
        return quote(self.toStr(), safe='')

    def toDict(self) -> dict:
        return {
            'currency': self.currency,
            'riskLevel': self.riskLevel,
            'allocationType': self.allocationType,
            'excludeRealAssets': self.excludeRealAssets,
        }

    @staticmethod
    def fromStr(value: str) -> "PortfolioKey":
        parts = unquote(str(value)).split('|')
        if len(parts) != 4:
            raise ValidationError('key', 'Malformed portfolio key: {!r}'.format(value))
        currency, riskLevel, allocationType, exclusion = parts
        if allocationType == PortfolioKey.NA:
            return PortfolioKey(currency, riskLevel, None, None)
        return PortfolioKey(currency, riskLevel, allocationType, exclusion == '1')

    @staticmethod
    def fromDict(data) -> "PortfolioKey":
        if not isinstance(data, dict):
            raise ValidationError('key', 'The portfolio key must be an object.')
        try:
            currency = str(data['currency']).strip()
            riskLevel = str(data['riskLevel']).strip()
        except KeyError as exc:
            raise ValidationError('key', 'The portfolio key is missing {}.'.format(exc))
        allocationType = data.get('allocationType')
        if allocationType in (None, '', PortfolioKey.NA):
            return PortfolioKey(currency, riskLevel, None, None)
        return PortfolioKey(currency, riskLevel, str(allocationType).strip(),
                            bool(data.get('excludeRealAssets', False)))