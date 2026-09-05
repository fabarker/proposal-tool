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

This package binds them concretely: BasisInput, MandateInput and PortfolioKey
are dataclasses in ``types.py``; Schema, PortfolioResult, Sleeve, Advisor and
Implementation travel as plain dicts whose shapes ``payloads.py`` assembles
and the service README documents.

The HTTP surface that sits in front of this port is section 3.4. The page calls
/api/scenario/...; the Flask proxy rewrites to /api/v1/scenario/... .
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from .types import BasisInput, MandateInput, PortfolioKey

    Advisor = Any
    Implementation = Any
    PortfolioResult = Any
    Schema = Any
    Sleeve = Any


class ScenarioPort(Protocol):
    """Everything the UI needs from the host. Eight methods, no more."""

    def get_schema(self, basis: "BasisInput", mandate: "MandateInput",
                   variant: str = None) -> "Schema":
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

    def list_sleeves(
        self, category: str, basis: "BasisInput", variant: str
    ) -> "Sequence[Sleeve]":
        """The PMG-authored sleeve library for one category, under one
        implementation type. A sleeve is a fixed block: name plus products
        whose weights sum to 1.

        *variant* is one of the schema's options.implementationVariants and
        decides both which sleeves the category offers and what they contain
        (deviation D29). It is not optional and has no default: a caller that
        has not settled a variant has nothing to list. An empty sequence is a
        legitimate answer - that variant reaches no sleeve for that category.
        """

    def build_export(
        self,
        basis: "BasisInput",
        mandate: "MandateInput",
        portfolios: "Sequence[PortfolioResult]",
        implementation: "Implementation | None",
    ) -> bytes:
        """Return an .xlsx byte stream. Every adapter writes it with the same
        function - ``workbook.writeWorkbook`` - from the resolved payloads
        alone, so no configuration can change what a proposal looks like and
        no analytics library is needed to produce one (D67). Four sheets:
        portfolios, risk_dashboard, assumptions, Implementation.

        *implementation* carries ``sleeves``, ``variant``, ``tacticalTilt``,
        ``volPremium``, ``includeFees``, ``feeSchedule`` and ``feeLevel``; the fee tier comes
        from the mandate's top account size (D51). With ``includeFees`` false
        the sheet carries no fee columns at all and no schedule is needed
        (D52). ``proposalId`` is the Proposal UID the caller minted before
        asking for the file; the writer puts it in the Implementation sheet's
        first row, in every sheet's print header and in the file's properties
        (D75)."""

    def capabilities(self) -> dict:
        """{"canExport": bool, "canEdit": bool}. One role today; this is the seam."""

    def describe(self) -> dict:
        """Data version, source, as-of date. Surfaced in the footer for support
        and reproducibility."""
