"""
ScenarioPort — the adapter the receiving project implements.

This file is extracted verbatim from spec.html section 4.2. It is the whole
backend contract: eight methods, no more. Implement it once against your data
layer and the front end needs no changes.

The referenced types are specified in the spec rather than here, because their
shapes are defined alongside the surfaces that carry them:

    BasisInput, MandateInput   section 5.3, state shape
    PortfolioKey               section 2.1, what identifies a portfolio
    Schema                     section 3.4, GET /api/scenario/schema
    PortfolioResult            section 3.4, POST /api/scenario/{id}/portfolio
    Sleeve                     section 2.6, sleeves
    Advisor                    section 3.4, GET /api/scenario/advisors
    Implementation             section 8, step 2
    ValidationError            section 3.5, error contract

They are left as forward references deliberately. Binding them to concrete
dataclasses here would invent detail the spec does not fix, and the receiving
project's own types are the right home for them.

The HTTP surface that sits in front of this port is section 3.4. The page calls
/api/scenario/...; the Flask proxy rewrites to /api/v1/scenario/... .
"""

from __future__ import annotations

from typing import Protocol, Sequence, TYPE_CHECKING

if TYPE_CHECKING:  # resolved by the receiving project
    from .types import (
        Advisor,
        BasisInput,
        Implementation,
        MandateInput,
        PortfolioKey,
        PortfolioResult,
        Schema,
        Sleeve,
    )


class ScenarioPort(Protocol):
    """Everything the UI needs from the host. Eight methods, no more."""

    def get_schema(self, basis: "BasisInput", mandate: "MandateInput") -> "Schema":
        """Field definitions, option values, validation rules, and the set of
        (allocation, excludeRE, excludeTAA, riskLevel) combinations that have a
        stored portfolio for this currency, hedging policy AND mandate size.

        Mandate size matters: private assets are blocked under $20m, which
        removes four of the six allocation variants. Must be cheap - called on
        scenario open and whenever the basis or the mandate changes."""

    def search_advisors(self, query: str, limit: int = 20) -> "Sequence[Advisor]":
        """Typeahead over the Primary PWA directory. The reference adapter reads
        an Excel file; production reads a database table."""

    def validate_mandate(self, mandate: "MandateInput") -> None:
        """Raise ValidationError with a field name and message if invalid. The UI
        enforces the same rules client-side for immediacy; the server is
        authoritative."""

    def resolve_portfolio(
        self, basis: "BasisInput", key: "PortfolioKey"
    ) -> "PortfolioResult":
        """Look up the stored allocation, construct the portfolio, compute the
        analytics, return everything the tables need. THE EXPENSIVE CALL - may
        take seconds. Implementations should cache."""

    def list_sleeves(self, category: str, basis: "BasisInput") -> "Sequence[Sleeve]":
        """The PMG-authored sleeve library for one category. A sleeve is a fixed
        block: name plus products whose weights sum to 1."""

    def build_export(
        self,
        basis: "BasisInput",
        mandate: "MandateInput",
        portfolios: "Sequence[PortfolioResult]",
        implementation: "Implementation | None",
    ) -> bytes:
        """Return an .xlsx byte stream. The reference adapter calls
        Reporting.generate_report(include_wealth_simulations=False)."""

    def capabilities(self) -> dict:
        """{"canExport": bool, "canEdit": bool}. One role today; this is the seam."""

    def describe(self) -> dict:
        """Data version, source, as-of date. Surfaced in the footer for support
        and reproducibility."""
