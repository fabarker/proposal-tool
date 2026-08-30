"""Build the Proposal Tool's complete portfolio-weight universe.

The module exposes two ready-to-use pandas dataframes:

``portfolio_weights_df``
    Long form: one row per portfolio and asset. This is the canonical output.

``portfolio_weights_wide_df``
    Wide form: one row per portfolio and one weight column per asset code.

``load_portfolio_weights(...)``
    Select one portfolio using the five Proposal Tool inputs and return its
    asset rows.

``load_portfolio_weights_from_ui(...)``
    Accept the UI/API payload shape directly, including nested ``basis`` and
    ``key`` dictionaries.

Typical use from a notebook or IDE console::

    from pathlib import Path
    import runpy

    result = runpy.run_path("proposal-tool/portfolio_weights.py")
    weights = result["portfolio_weights_df"]
    weights.to_excel("portfolio_weights.xlsx", index=False)

    selected = result["load_portfolio_weights"](
        currency="USD",
        allocation="Core",
        risk_level="Mod",
        exclude_real_estate=True,
        exclude_tactical_asset_allocation=False,
    )

Run the module directly to load one portfolio and print its allocation::

    python proposal-tool/portfolio_weights.py \
        --currency GBP \
        --allocation "Full" \
        --risk-level "Mod Agg" \
        --exclude-real-estate

``save_portfolio_weights_excel(...)`` remains available when a workbook export
is required.

Methodology
-----------
The four product-level anchors come from ``Core Portfolios.xlsx``: Low
Volatility, Conservative, Moderate and Aggressive. The product universe and
reporting metadata come from the table supplied for the Proposal Tool.

The workbook contains a few products that are not in the supplied universe
(short-duration debt, public REITs, infrastructure equity and private
infrastructure). Their category totals are retained and redistributed across
the supplied products in the same category. This means every generated
portfolio uses only the supplied codes and sums to exactly 100%.

Important assumptions are deliberately configurable and are also written to
the Excel Assumptions sheet:

* Cons Mod and Mod Agg are linearly interpolated using the Proposal Tool's risk
  scores. All Equity is linearly extrapolated from Moderate/Aggressive, with
  negative weights clipped to zero before renormalisation.
* Allocation exclusions remove the relevant assets and redistribute their
  weights pro rata across the remaining strategic holdings.
* Excluding real estate removes only ``PA_REAL_ESTATE``. Private Credit remains
  in Full and Ex HFs portfolios.
* Including Tactical Tilt gives ``LHUT1T3`` a configurable 5% overlay, funded
  pro rata from the strategic holdings. Excluding it sets the weight to zero.
* Currency changes portfolio identity but not weights, as requested.
* All 84 theoretical combinations per currency are included. ``ui_available``
  identifies the combinations currently selectable in the prototype.

These are model-construction assumptions, not investment recommendations.
Replace the constants with approved stored allocations before production use.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Iterable, Mapping, MutableMapping, Sequence

import pandas as pd
from chromadb.utils import lru_cache

CURRENCIES = ("CHF", "USD", "GBP", "EUR")
TACTICAL_TILT_CODE = "LHUT1T3"
DEFAULT_TACTICAL_TILT_WEIGHT = 0.05


# Order follows the supplied asset table and is preserved in both dataframe
# forms and in the generated workbook.
ASSET_METADATA = (
    ("LHTRYIN", "US Dollar Debt", "Investment Grade Fixed Income"),
    ("LHYIELD", "US High Yield", "Other Fixed Income"),
    ("FRUS1GR", "US Large Cap Growth Equity", "Public Equity"),
    ("FRUS1VA", "US Large Cap Value Equity", "Public Equity"),
    ("FRUSS2L", "US Small Cap Equity", "Public Equity"),
    ("MSEXUKL", "Europe ex-UK Equity", "Public Equity"),
    ("MSUTDKL", "UK Equity", "Public Equity"),
    ("MSJPANL", "Japanese Equity", "Public Equity"),
    ("MSPXJPL", "Asia-Pacific Equity", "Public Equity"),
    ("MSEMKF$", "Emerging Market Equity", "Public Equity"),
    ("CSTEVDH", "Event Driven", "Hedge Funds"),
    ("CSTLNSH", "Equity Long/Short", "Hedge Funds"),
    ("CSFBMTT", "Tactical Trading", "Hedge Funds"),
    ("PE_BUYOUT", "Buyout", "Private Equity"),
    ("PE_GROWTH", "Growth", "Private Equity"),
    ("PE_VENTURE", "Venture", "Private Equity"),
    ("PRIVATE_CREDIT", "Private Credit", "Other Private Assets"),
    ("PA_REAL_ESTATE", "Core Real Estate", "Other Private Assets"),
    (TACTICAL_TILT_CODE, "Tactical Tilt Fund", "Asset Allocation Strategies"),
)

ASSET_CODES = tuple(item[0] for item in ASSET_METADATA)
STRATEGIC_CODES = tuple(code for code in ASSET_CODES if code != TACTICAL_TILT_CODE)
ASSET_LOOKUP = {
    code: {"reporting_name": reporting_name, "category": category}
    for code, reporting_name, category in ASSET_METADATA
}

PUBLIC_EQUITY_CODES = (
    "FRUS1GR",
    "FRUS1VA",
    "FRUSS2L",
    "MSEXUKL",
    "MSUTDKL",
    "MSJPANL",
    "MSPXJPL",
    "MSEMKF$",
)
HEDGE_FUND_CODES = ("CSTEVDH", "CSTLNSH", "CSFBMTT")
PRIVATE_EQUITY_CODES = ("PE_BUYOUT", "PE_GROWTH", "PE_VENTURE")
OTHER_PRIVATE_ASSET_CODES = ("PRIVATE_CREDIT", "PA_REAL_ESTATE")


# The score is the same risk-axis value used by the Proposal Tool prototype.
RISK_LEVELS = (
    {"code": "LV", "name": "Low Vol", "score": 10},
    {"code": "C", "name": "Cons", "score": 25},
    {"code": "CM", "name": "Cons Mod", "score": 38},
    {"code": "M", "name": "Mod", "score": 45},
    {"code": "MA", "name": "Mod Agg", "score": 58},
    {"code": "A", "name": "Agg", "score": 72},
    {"code": "AE", "name": "All Equity", "score": 98},
)


# Six real-estate/allocation variants: the four AA types do not form a full
# 4 x 2 cross-product because Core and Ex Alts never admit real estate.
VARIANTS = (
    {
        "id": "full-re",
        "allocation_type": "Full",
        "label": "Full + RE",
        "include_real_estate": True,
        "excluded_codes": (),
    },
    {
        "id": "full",
        "allocation_type": "Full",
        "label": "Full ex RE",
        "include_real_estate": False,
        "excluded_codes": ("PA_REAL_ESTATE",),
    },
    {
        "id": "exhf-re",
        "allocation_type": "Ex HFs",
        "label": "Ex HFs + RE",
        "include_real_estate": True,
        "excluded_codes": HEDGE_FUND_CODES,
    },
    {
        "id": "exhf",
        "allocation_type": "Ex HFs",
        "label": "Ex HFs ex RE",
        "include_real_estate": False,
        "excluded_codes": HEDGE_FUND_CODES + ("PA_REAL_ESTATE",),
    },
    {
        "id": "core",
        "allocation_type": "Core",
        "label": "Core",
        "include_real_estate": False,
        "excluded_codes": PRIVATE_EQUITY_CODES + OTHER_PRIVATE_ASSET_CODES,
    },
    {
        "id": "exalts",
        "allocation_type": "Ex Alts",
        "label": "Ex Alts",
        "include_real_estate": False,
        "excluded_codes": (
            HEDGE_FUND_CODES + PRIVATE_EQUITY_CODES + OTHER_PRIVATE_ASSET_CODES
        ),
    },
)


# Combinations disabled by the current Proposal Tool prototype. They are still
# generated by default so the dataframe covers the full requested combination
# space; filter ``ui_available`` or pass include_unavailable=False if needed.
UI_MISSING = {
    ("exalts", "LV"),
    ("exalts", "C"),
    ("full-re", "AE"),
    ("exhf-re", "AE"),
    ("full", "LV"),
    ("exhf", "LV"),
    ("full-re", "LV"),
    ("exhf-re", "LV"),
}


# Four repository anchor columns transcribed from the existing workbook.
# Category totals are percentages. Component figures are the workbook's raw
# product figures and are rescaled to the supplied universe within category.
_ANCHOR_INPUTS = {
    10: {
        "igfi": 74.5,
        "ofi": 2.0,
        "public_equity": 14.0,
        "hedge_funds": 1.5,
        "private_equity": 4.0,
        "other_private_assets": 4.0,
        "public_equity_raw": (3.9, 4.4, 1.4, 1.6, 0.5, 0.7, 0.4, 0.4),
        "hedge_funds_raw": (0.3, 0.6, 0.6),
        "private_equity_raw": (3.0, 1.0, 0.0),
        "other_private_assets_raw": (1.3, 1.35),
    },
    25: {
        "igfi": 58.5,
        "ofi": 3.0,
        "public_equity": 22.5,
        "hedge_funds": 3.5,
        "private_equity": 5.5,
        "other_private_assets": 7.0,
        "public_equity_raw": (6.3, 7.1, 2.2, 2.5, 0.8, 1.1, 0.6, 0.6),
        "hedge_funds_raw": (0.7, 1.4, 1.4),
        "private_equity_raw": (4.1, 1.4, 0.0),
        "other_private_assets_raw": (2.3, 2.35),
    },
    45: {
        "igfi": 36.0,
        "ofi": 4.5,
        "public_equity": 35.5,
        "hedge_funds": 4.5,
        "private_equity": 12.0,
        "other_private_assets": 7.5,
        "public_equity_raw": (10.0, 11.2, 3.5, 4.0, 1.2, 1.8, 0.9, 0.9),
        "hedge_funds_raw": (0.9, 1.8, 1.8),
        "private_equity_raw": (8.4, 2.8, 0.8),
        "other_private_assets_raw": (3.5, 2.0),
    },
    72: {
        "igfi": 15.0,
        "ofi": 5.5,
        "public_equity": 50.5,
        "hedge_funds": 3.0,
        "private_equity": 18.5,
        "other_private_assets": 7.5,
        "public_equity_raw": (14.2, 15.9, 5.0, 5.7, 1.7, 2.6, 1.3, 1.3),
        "hedge_funds_raw": (0.6, 1.2, 1.2),
        "private_equity_raw": (13.0, 4.3, 1.2),
        "other_private_assets_raw": (3.5, 2.0),
    },
}


PORTFOLIO_COLUMNS = (
    "portfolio_id",
    "portfolio_name",
    "currency",
    "allocation_type",
    "allocation_variant",
    "include_real_estate",
    "exclude_real_estate",
    "include_tactical_tilt",
    "exclude_tactical_asset_allocation",
    "risk_code",
    "risk_level",
    "risk_score",
    "ui_available",
)


def _normalise(
    weights: Mapping[str, float], target: float = 1.0
) -> Dict[str, float]:
    """Return non-negative weights rescaled to *target*."""

    clean = {code: max(0.0, float(weight)) for code, weight in weights.items()}
    total = sum(clean.values())
    if total <= 0.0:
        raise ValueError("Cannot normalise a portfolio with no positive weights")
    return {code: weight * target / total for code, weight in clean.items()}


def _allocate_component(
    codes: Sequence[str], raw_weights: Sequence[float], category_total_pct: float
) -> Dict[str, float]:
    """Allocate a category percentage across its supplied product codes."""

    if len(codes) != len(raw_weights):
        raise ValueError("Product codes and component weights must have equal length")
    raw_total = sum(raw_weights)
    if raw_total <= 0.0:
        raise ValueError("A populated category requires a positive component total")
    category_fraction = category_total_pct / 100.0
    return {
        code: float(raw) / raw_total * category_fraction
        for code, raw in zip(codes, raw_weights)
    }


def _build_anchor(anchor_input: Mapping[str, object]) -> Dict[str, float]:
    """Expand one category-level workbook anchor to the supplied products."""

    weights: MutableMapping[str, float] = {code: 0.0 for code in STRATEGIC_CODES}
    weights["LHTRYIN"] = float(anchor_input["igfi"]) / 100.0
    weights["LHYIELD"] = float(anchor_input["ofi"]) / 100.0
    weights.update(
        _allocate_component(
            PUBLIC_EQUITY_CODES,
            anchor_input["public_equity_raw"],  # type: ignore[arg-type]
            float(anchor_input["public_equity"]),
        )
    )
    weights.update(
        _allocate_component(
            HEDGE_FUND_CODES,
            anchor_input["hedge_funds_raw"],  # type: ignore[arg-type]
            float(anchor_input["hedge_funds"]),
        )
    )
    weights.update(
        _allocate_component(
            PRIVATE_EQUITY_CODES,
            anchor_input["private_equity_raw"],  # type: ignore[arg-type]
            float(anchor_input["private_equity"]),
        )
    )
    weights.update(
        _allocate_component(
            OTHER_PRIVATE_ASSET_CODES,
            anchor_input["other_private_assets_raw"],  # type: ignore[arg-type]
            float(anchor_input["other_private_assets"]),
        )
    )
    return _normalise(weights)


_ANCHORS = {
    risk_score: _build_anchor(anchor_input)
    for risk_score, anchor_input in _ANCHOR_INPUTS.items()
}


def _interpolated_full_re_weights(risk_score: int) -> Dict[str, float]:
    """Return a Full + RE strategic portfolio for any UI risk score."""

    if risk_score in _ANCHORS:
        return dict(_ANCHORS[risk_score])

    anchor_scores = sorted(_ANCHORS)
    if risk_score < anchor_scores[0]:
        lower, upper = anchor_scores[0], anchor_scores[1]
    elif risk_score > anchor_scores[-1]:
        lower, upper = anchor_scores[-2], anchor_scores[-1]
    else:
        lower = max(score for score in anchor_scores if score < risk_score)
        upper = min(score for score in anchor_scores if score > risk_score)

    interpolation_fraction = (risk_score - lower) / (upper - lower)
    interpolated = {
        code: max(
            0.0,
            _ANCHORS[lower][code]
            + interpolation_fraction
            * (_ANCHORS[upper][code] - _ANCHORS[lower][code]),
        )
        for code in STRATEGIC_CODES
    }
    return _normalise(interpolated)


def _apply_variant(
    full_re_weights: Mapping[str, float], variant: Mapping[str, object]
) -> Dict[str, float]:
    """Remove variant exclusions and redistribute their weight pro rata."""

    excluded = set(variant["excluded_codes"])  # type: ignore[arg-type]
    remaining = {
        code: (0.0 if code in excluded else float(weight))
        for code, weight in full_re_weights.items()
    }
    return _normalise(remaining)


def _apply_tactical_tilt(
    strategic_weights: Mapping[str, float],
    include_tactical_tilt: bool,
    tactical_tilt_weight: float,
) -> Dict[str, float]:
    """Add the tactical overlay, funding it pro rata from strategic assets."""

    if not 0.0 <= tactical_tilt_weight < 1.0:
        raise ValueError("tactical_tilt_weight must be in the interval [0, 1)")

    strategic_total = 1.0 - tactical_tilt_weight if include_tactical_tilt else 1.0
    weights = {
        code: float(weight) * strategic_total
        for code, weight in strategic_weights.items()
    }
    weights[TACTICAL_TILT_CODE] = (
        tactical_tilt_weight if include_tactical_tilt else 0.0
    )
    return weights


def _portfolio_name(
    currency: str,
    variant: Mapping[str, object],
    risk_name: str,
    include_tactical_tilt: bool,
) -> str:
    suffix = ""
    if (
        not bool(variant["include_real_estate"])
        and variant["allocation_type"] in {"Full", "Ex HFs"}
    ):
        suffix += " ex RE"
    if not include_tactical_tilt:
        suffix += " ex TAA"
    return f"{currency} {variant['allocation_type']} {risk_name}{suffix}"


def build_portfolio_weights(
    currencies: Iterable[str] = CURRENCIES,
    tactical_tilt_weight: float = DEFAULT_TACTICAL_TILT_WEIGHT,
    include_unavailable: bool = True,
) -> pd.DataFrame:
    """Return all Proposal Tool combinations as a long-form dataframe.

    Parameters
    ----------
    currencies:
        Currency labels to repeat. Weights deliberately remain identical across
        currencies.
    tactical_tilt_weight:
        Decimal portfolio weight assigned to ``LHUT1T3`` when tactical asset
        allocation is included. The default is 0.05 (5%).
    include_unavailable:
        Include the full theoretical 84 combinations per currency when True.
        When False, omit combinations disabled in the current UI prototype.
    """

    currency_values = tuple(dict.fromkeys(str(currency).upper() for currency in currencies))
    if not currency_values:
        raise ValueError("At least one currency is required")

    rows = []
    for currency in currency_values:
        for variant_order, variant in enumerate(VARIANTS):
            for risk_order, risk_level in enumerate(RISK_LEVELS):
                ui_available = (
                    str(variant["id"]), str(risk_level["code"])
                ) not in UI_MISSING
                if not include_unavailable and not ui_available:
                    continue

                full_re_weights = _interpolated_full_re_weights(
                    int(risk_level["score"])
                )
                strategic_weights = _apply_variant(full_re_weights, variant)

                # Included first because it is the UI default state.
                for tactical_order, include_tactical_tilt in enumerate((True, False)):
                    weights = _apply_tactical_tilt(
                        strategic_weights,
                        include_tactical_tilt,
                        tactical_tilt_weight,
                    )
                    tactical_state = "taa-in" if include_tactical_tilt else "taa-ex"
                    portfolio_id = "|".join(
                        (
                            currency,
                            str(variant["id"]),
                            str(risk_level["code"]),
                            tactical_state,
                        )
                    )
                    portfolio_name = _portfolio_name(
                        currency,
                        variant,
                        str(risk_level["name"]),
                        include_tactical_tilt,
                    )

                    for asset_order, code in enumerate(ASSET_CODES):
                        metadata = ASSET_LOOKUP[code]
                        weight = float(weights.get(code, 0.0))
                        rows.append(
                            {
                                "portfolio_id": portfolio_id,
                                "portfolio_name": portfolio_name,
                                "currency": currency,
                                "allocation_type": variant["allocation_type"],
                                "allocation_variant": variant["label"],
                                "include_real_estate": bool(
                                    variant["include_real_estate"]
                                ),
                                "exclude_real_estate": not bool(
                                    variant["include_real_estate"]
                                ),
                                "include_tactical_tilt": include_tactical_tilt,
                                "exclude_tactical_asset_allocation": (
                                    not include_tactical_tilt
                                ),
                                "risk_code": risk_level["code"],
                                "risk_level": risk_level["name"],
                                "risk_score": risk_level["score"],
                                "ui_available": ui_available,
                                "code": code,
                                "reporting_name": metadata["reporting_name"],
                                "category": metadata["category"],
                                "weight": weight,
                                "weight_pct": weight * 100.0,
                                "is_held": weight > 1e-12,
                                "_variant_order": variant_order,
                                "_risk_order": risk_order,
                                "_tactical_order": tactical_order,
                                "_asset_order": asset_order,
                            }
                        )

    dataframe = pd.DataFrame(rows)
    if dataframe.empty:
        raise ValueError("The requested settings produced an empty dataframe")

    group_columns = ["portfolio_id", "category"]
    dataframe["category_weight"] = dataframe.groupby(group_columns)[
        "weight"
    ].transform("sum")
    dataframe["category_weight_pct"] = dataframe["category_weight"] * 100.0

    dataframe.sort_values(
        [
            "currency",
            "_variant_order",
            "_risk_order",
            "_tactical_order",
            "_asset_order",
        ],
        inplace=True,
        kind="stable",
    )
    dataframe.drop(
        columns=[
            "_variant_order",
            "_risk_order",
            "_tactical_order",
            "_asset_order",
        ],
        inplace=True,
    )

    final_columns = list(PORTFOLIO_COLUMNS) + [
        "code",
        "reporting_name",
        "category",
        "weight",
        "weight_pct",
        "category_weight",
        "category_weight_pct",
        "is_held",
    ]
    dataframe = dataframe.loc[:, final_columns].reset_index(drop=True)
    validate_portfolio_weights(
        dataframe,
        tactical_tilt_weight=tactical_tilt_weight,
    )
    return dataframe


def validate_portfolio_weights(
    dataframe: pd.DataFrame,
    tactical_tilt_weight: float = DEFAULT_TACTICAL_TILT_WEIGHT,
) -> None:
    """Raise ``AssertionError`` if portfolio arithmetic or exclusions drift."""

    portfolio_sums = dataframe.groupby("portfolio_id", sort=False)["weight"].sum()
    if not ((portfolio_sums - 1.0).abs() < 1e-10).all():
        raise AssertionError("Every portfolio must sum to exactly 100%")
    if (dataframe["weight"] < -1e-12).any():
        raise AssertionError("Portfolio weights cannot be negative")
    if dataframe.groupby("portfolio_id", sort=False)["code"].nunique().ne(
        len(ASSET_CODES)
    ).any():
        raise AssertionError("Every portfolio must carry every supplied asset code")

    tactical_rows = dataframe[dataframe["code"] == TACTICAL_TILT_CODE]
    included_tactical = tactical_rows["include_tactical_tilt"]
    if not (
        tactical_rows.loc[included_tactical, "weight"] - tactical_tilt_weight
    ).abs().lt(1e-12).all():
        raise AssertionError("Included Tactical Tilt weights are inconsistent")
    if not tactical_rows.loc[~included_tactical, "weight"].abs().lt(1e-12).all():
        raise AssertionError("Excluded Tactical Tilt must have zero weight")

    real_estate_rows = dataframe[dataframe["code"] == "PA_REAL_ESTATE"]
    if not real_estate_rows.loc[
        real_estate_rows["exclude_real_estate"], "weight"
    ].abs().lt(1e-12).all():
        raise AssertionError("Excluded Real Estate must have zero weight")

    excluded_hf = dataframe["allocation_type"].isin(("Ex HFs", "Ex Alts"))
    if not dataframe.loc[
        excluded_hf & dataframe["code"].isin(HEDGE_FUND_CODES), "weight"
    ].abs().lt(1e-12).all():
        raise AssertionError("Hedge Funds found in an excluded allocation")

    excluded_pe = dataframe["allocation_type"].isin(("Core", "Ex Alts"))
    if not dataframe.loc[
        excluded_pe & dataframe["code"].isin(PRIVATE_EQUITY_CODES), "weight"
    ].abs().lt(1e-12).all():
        raise AssertionError("Private Equity found in an excluded allocation")

    excluded_opa = dataframe["allocation_type"].isin(("Core", "Ex Alts"))
    if not dataframe.loc[
        excluded_opa & dataframe["code"].isin(OTHER_PRIVATE_ASSET_CODES), "weight"
    ].abs().lt(1e-12).all():
        raise AssertionError("Other Private Assets found in an excluded allocation")

    # Same combination and asset must have exactly the same weight in every
    # currency. Rounding avoids treating sub-machine-epsilon noise as variation.
    currency_check_columns = [
        "allocation_variant",
        "risk_code",
        "include_tactical_tilt",
        "code",
    ]
    cross_currency_counts = (
        dataframe.assign(_weight_check=dataframe["weight"].round(14))
        .groupby(currency_check_columns, sort=False)["_weight_check"]
        .nunique()
    )
    if cross_currency_counts.max() != 1:
        raise AssertionError("Asset weights must be identical across currencies")


_ALLOCATION_ALIASES = {
    "full": "Full",
    "core": "Core",
    "ex hf": "Ex HFs",
    "ex hfs": "Ex HFs",
    "ex hedge fund": "Ex HFs",
    "ex hedge funds": "Ex HFs",
    "exclude hedge fund": "Ex HFs",
    "exclude hedge funds": "Ex HFs",
    "ex alt": "Ex Alts",
    "ex alts": "Ex Alts",
    "ex alternative": "Ex Alts",
    "ex alternatives": "Ex Alts",
    "exclude alternative": "Ex Alts",
    "exclude alternatives": "Ex Alts",
}

_RISK_ALIASES = {
    "lv": "LV",
    "low vol": "LV",
    "low volatility": "LV",
    "c": "C",
    "cons": "C",
    "conservative": "C",
    "cm": "CM",
    "cons mod": "CM",
    "conservative moderate": "CM",
    "m": "M",
    "mod": "M",
    "moderate": "M",
    "ma": "MA",
    "mod agg": "MA",
    "moderate aggressive": "MA",
    "a": "A",
    "agg": "A",
    "aggressive": "A",
    "ae": "AE",
    "all equity": "AE",
}

_MISSING_VALUE = object()
_NO_DEFAULT = object()


def _normalise_selector(value: object) -> str:
    """Make UI labels, snake case and hyphenated values comparable."""

    return " ".join(
        str(value).strip().lower().replace("_", " ").replace("-", " ").split()
    )


def _normalise_allocation(value: object) -> str:
    normalised = _normalise_selector(value)
    try:
        return _ALLOCATION_ALIASES[normalised]
    except KeyError as exc:
        allowed = "Full, Core, Ex HFs, Ex Alts"
        raise ValueError(
            f"Unknown allocation {value!r}; expected one of: {allowed}"
        ) from exc


def _normalise_risk(value: object) -> str:
    normalised = _normalise_selector(value)
    try:
        return _RISK_ALIASES[normalised]
    except KeyError as exc:
        allowed = ", ".join(str(risk["name"]) for risk in RISK_LEVELS)
        raise ValueError(
            f"Unknown risk level {value!r}; expected one of: {allowed}"
        ) from exc


def _coerce_bool(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    normalised = _normalise_selector(value)
    if normalised in {"true", "yes", "y", "1", "on", "included", "include"}:
        return True
    if normalised in {"false", "no", "n", "0", "off", "excluded", "exclude"}:
        return False
    raise ValueError(f"{field_name} must be a boolean; received {value!r}")


def _variant_label_for_inputs(
    allocation: str, exclude_real_estate: bool
) -> str:
    if allocation in {"Core", "Ex Alts"}:
        if not exclude_real_estate:
            raise ValueError(
                f"{allocation} cannot include Real Estate; the UI sends "
                "exclude_real_estate=True for this allocation"
            )
        return allocation
    if allocation == "Full":
        return "Full ex RE" if exclude_real_estate else "Full + RE"
    return "Ex HFs ex RE" if exclude_real_estate else "Ex HFs + RE"

@lru_cache
def get_schema(currency: str):

    from epsilonPhi.core.schema.Schema import ContextCreator
    return ContextCreator(
        currency=currency,
    ).create_context()

@lru_cache
def get_portfolio(currency, weights_dict):

    from epsilonPhi.core.portfolio.SAAPortfolio import SAAPortfolio
    return SAAPortfolio.from_dict(
        'Portfolio',
        weights_dict,
        get_schema(currency),
    )


def load_portfolio_weights(
    currency: str,
    allocation: str,
    risk_level: str,
    exclude_real_estate: bool,
    exclude_tactical_asset_allocation: bool,
    dataframe: pd.DataFrame | None = None,
    include_zero_weights: bool = False,
    require_ui_available: bool = True,
) -> pd.DataFrame:
    """Load the asset weights matching one set of Proposal Tool inputs.

    Parameters match the UI controls. ``allocation`` accepts the exact UI
    labels plus common long-form aliases; ``risk_level`` accepts either a risk
    code (for example ``"MA"``) or UI label (for example ``"Mod Agg"``).

    The returned dataframe contains portfolio metadata, asset metadata,
    decimal ``weight`` and ``weight_pct``. It contains only held assets by
    default; pass ``include_zero_weights=True`` to retain all 19 supplied codes.

    ``require_ui_available=True`` prevents the caller from resolving one of the
    prototype's disabled combinations. Set it to False to access all synthetic
    theoretical combinations in ``portfolio_weights_df``.
    """

    canonical_currency = str(currency).strip().upper()
    if canonical_currency not in CURRENCIES:
        raise ValueError(
            f"Unknown currency {currency!r}; expected one of: {', '.join(CURRENCIES)}"
        )
    canonical_allocation = _normalise_allocation(allocation)
    canonical_risk = _normalise_risk(risk_level)
    exclude_re = _coerce_bool(exclude_real_estate, "exclude_real_estate")
    exclude_taa = _coerce_bool(
        exclude_tactical_asset_allocation,
        "exclude_tactical_asset_allocation",
    )
    allocation_variant = _variant_label_for_inputs(
        canonical_allocation,
        exclude_re,
    )

    source = portfolio_weights_df if dataframe is None else dataframe
    required_columns = {
        "currency",
        "allocation_variant",
        "risk_code",
        "exclude_tactical_asset_allocation",
        "ui_available",
        "portfolio_id",
        "code",
        "weight",
    }
    missing_columns = required_columns.difference(source.columns)
    if missing_columns:
        raise ValueError(
            "Weights dataframe is missing required columns: "
            + ", ".join(sorted(missing_columns))
        )

    matches = source.loc[
        source["currency"].astype(str).str.upper().eq(canonical_currency)
        & source["allocation_variant"].eq(allocation_variant)
        & source["risk_code"].eq(canonical_risk)
        & source["exclude_tactical_asset_allocation"].eq(exclude_taa)
    ].copy()

    if matches.empty:
        raise LookupError(
            "No portfolio weights matched "
            f"currency={canonical_currency}, allocation={canonical_allocation}, "
            f"exclude_real_estate={exclude_re}, "
            f"exclude_tactical_asset_allocation={exclude_taa}, "
            f"risk_level={canonical_risk}"
        )
    if matches["portfolio_id"].nunique() != 1:
        raise AssertionError("Portfolio selector matched more than one portfolio")
    if require_ui_available and not bool(matches["ui_available"].iloc[0]):
        raise LookupError(
            f"{matches['portfolio_name'].iloc[0]} is not available in the current UI schema"
        )

    if not include_zero_weights:
        matches = matches.loc[matches["weight"] > 1e-12].copy()
    if abs(float(matches["weight"].sum()) - 1.0) > 1e-10:
        raise AssertionError("Selected holdings do not sum to 100%")
    return matches.reset_index(drop=True)


def load_portfolio_weight_map(
    currency: str,
    allocation: str,
    risk_level: str,
    exclude_real_estate: bool,
    exclude_tactical_asset_allocation: bool,
    dataframe: pd.DataFrame | None = None,
    include_zero_weights: bool = False,
    require_ui_available: bool = True,
) -> Dict[str, float]:
    """Return one selected portfolio as ``{asset_code: decimal_weight}``."""

    selected = load_portfolio_weights(
        currency=currency,
        allocation=allocation,
        risk_level=risk_level,
        exclude_real_estate=exclude_real_estate,
        exclude_tactical_asset_allocation=exclude_tactical_asset_allocation,
        dataframe=dataframe,
        include_zero_weights=include_zero_weights,
        require_ui_available=require_ui_available,
    )
    return dict(zip(selected["code"], selected["weight"]))


def _first_present(
    mappings: Sequence[Mapping[str, object]],
    keys: Sequence[str],
    default: object = _NO_DEFAULT,
) -> object:
    for mapping in mappings:
        for key in keys:
            if key in mapping:
                return mapping[key]
    if default is _NO_DEFAULT:
        raise KeyError(f"Missing required UI input; accepted keys: {', '.join(keys)}")
    return default


def load_portfolio_weights_from_ui(
    ui_inputs: Mapping[str, object],
    dataframe: pd.DataFrame | None = None,
    include_zero_weights: bool = False,
    require_ui_available: bool = True,
) -> pd.DataFrame:
    """Load weights directly from a Proposal Tool UI/API input mapping.

    Supported payloads include the production-shaped form::

        {
            "basis": {"currency": "GBP"},
            "key": {
                "allocation": "Full",
                "excludeRE": True,
                "excludeTAA": False,
                "riskLevel": "Mod Agg",
            },
        }

    Flat dictionaries are also accepted, as are the prototype aliases
    ``ccy``, ``v``, ``r`` and ``taa``. When ``v`` is one of the six variant IDs
    (for example ``"full-re"`` or ``"exhf"``), the RE state is derived from it.
    In the prototype, ``taa=True`` means TAA is excluded; this is preserved.
    """

    basis_value = ui_inputs.get("basis", {})
    key_value = ui_inputs.get("key", {})
    basis = basis_value if isinstance(basis_value, Mapping) else {}
    key = key_value if isinstance(key_value, Mapping) else {}
    lookup_mappings = (key, ui_inputs, basis)

    currency = _first_present(lookup_mappings, ("currency", "ccy"))
    allocation_value = _first_present(
        lookup_mappings,
        ("allocation", "allocationType", "allocation_type", "aa"),
        default=_MISSING_VALUE,
    )
    variant_value = _first_present(
        lookup_mappings,
        ("v",),
        default=_MISSING_VALUE,
    )
    uses_compact_variant = allocation_value is _MISSING_VALUE
    if uses_compact_variant:
        if variant_value is _MISSING_VALUE:
            raise KeyError(
                "Missing required UI input; accepted keys: allocation, "
                "allocationType, allocation_type, aa, v"
            )
        allocation_value = variant_value
    risk_level = _first_present(
        lookup_mappings,
        ("riskLevel", "risk_level", "risk", "r"),
    )

    variant_by_id = {str(variant["id"]): variant for variant in VARIANTS}
    raw_variant_id = str(allocation_value).strip().lower().replace("_", "-")
    derived_exclude_re = _MISSING_VALUE
    if uses_compact_variant and raw_variant_id in variant_by_id:
        variant = variant_by_id[raw_variant_id]
        allocation = str(variant["allocation_type"])
        derived_exclude_re = not bool(variant["include_real_estate"])
    elif uses_compact_variant:
        allowed = ", ".join(variant_by_id)
        raise ValueError(
            f"Unknown compact allocation variant {allocation_value!r}; "
            f"expected one of: {allowed}"
        )
    else:
        allocation = _normalise_allocation(allocation_value)

    exclude_re_input = _first_present(
        lookup_mappings,
        (
            "excludeRE",
            "exclude_re",
            "excludeRealEstate",
            "exclude_real_estate",
        ),
        default=derived_exclude_re,
    )
    if exclude_re_input is _MISSING_VALUE:
        exclude_re = allocation in {"Core", "Ex Alts"}
    else:
        exclude_re = _coerce_bool(exclude_re_input, "exclude_real_estate")
    if derived_exclude_re is not _MISSING_VALUE and exclude_re != derived_exclude_re:
        raise ValueError(
            f"Variant {allocation_value!r} conflicts with exclude_real_estate={exclude_re}"
        )

    exclude_taa_input = _first_present(
        lookup_mappings,
        (
            "excludeTAA",
            "exclude_taa",
            "excludeTacticalAssetAllocation",
            "exclude_tactical_asset_allocation",
            "taa",
        ),
        default=False,
    )
    exclude_taa = _coerce_bool(
        exclude_taa_input,
        "exclude_tactical_asset_allocation",
    )

    return load_portfolio_weights(
        currency=str(currency),
        allocation=allocation,
        risk_level=str(risk_level),
        exclude_real_estate=exclude_re,
        exclude_tactical_asset_allocation=exclude_taa,
        dataframe=dataframe,
        include_zero_weights=include_zero_weights,
        require_ui_available=require_ui_available,
    )


def portfolio_weights_wide(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Pivot the canonical long dataframe to one row per portfolio."""

    wide = dataframe.pivot(
        index=list(PORTFOLIO_COLUMNS),
        columns="code",
        values="weight",
    ).reset_index()
    wide.columns.name = None
    ordered_columns = list(PORTFOLIO_COLUMNS) + list(ASSET_CODES)
    return wide.loc[:, ordered_columns]


def assumptions_dataframe(
    tactical_tilt_weight: float = DEFAULT_TACTICAL_TILT_WEIGHT,
) -> pd.DataFrame:
    """Return the construction assumptions as a dataframe for audit/export."""

    assumptions = (
        (
            "Product universe",
            "Codes, reporting names and categories transcribed from the supplied Proposal Tool asset table.",
        ),
        (
            "Anchor portfolios",
            "Low Volatility, Conservative, Moderate and Aggressive product weights are based on Core Portfolios.xlsx.",
        ),
        (
            "Omitted workbook assets",
            "Short-duration debt, public REITs, infrastructure equity and private infrastructure are not in the supplied universe; their category totals are redistributed within category.",
        ),
        (
            "Intermediate risks",
            "Cons Mod and Mod Agg use linear interpolation on Proposal Tool risk scores 38 and 58.",
        ),
        (
            "All Equity",
            "Extrapolated from Moderate/Aggressive to risk score 98; negative weights are clipped to zero and the remainder is renormalised.",
        ),
        (
            "Allocation variants",
            "Excluded asset groups are zeroed, then remaining strategic holdings are redistributed pro rata.",
        ),
        (
            "Real Estate",
            "Excluding RE removes PA_REAL_ESTATE only; PRIVATE_CREDIT remains in Full and Ex HFs.",
        ),
        (
            "Tactical Tilt",
            f"When included, {TACTICAL_TILT_CODE} receives {tactical_tilt_weight:.2%}, funded pro rata from strategic holdings; otherwise it is zero.",
        ),
        (
            "Currencies",
            "CHF, USD, GBP and EUR repeat identical weights; currency changes portfolio identity only.",
        ),
        (
            "Availability",
            "All theoretical combinations are generated. ui_available flags combinations currently disabled in the prototype.",
        ),
        (
            "Production status",
            "Synthetic construction for review. Replace with approved stored allocations before production use.",
        ),
    )
    return pd.DataFrame(assumptions, columns=("assumption", "treatment"))


def save_portfolio_weights_excel(
    output_path: str | Path,
    dataframe: pd.DataFrame | None = None,
    tactical_tilt_weight: float = DEFAULT_TACTICAL_TILT_WEIGHT,
) -> Path:
    """Write long, wide, metadata and assumption sheets to an Excel workbook."""

    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    long_dataframe = (
        build_portfolio_weights(tactical_tilt_weight=tactical_tilt_weight)
        if dataframe is None
        else dataframe.copy()
    )
    validate_portfolio_weights(long_dataframe, tactical_tilt_weight)
    wide_dataframe = portfolio_weights_wide(long_dataframe)
    metadata = pd.DataFrame(
        ASSET_METADATA,
        columns=("code", "reporting_name", "category"),
    )

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        long_dataframe.to_excel(writer, sheet_name="Weights_Long", index=False)
        wide_dataframe.to_excel(writer, sheet_name="Weights_Wide", index=False)
        metadata.to_excel(writer, sheet_name="Asset_Metadata", index=False)
        assumptions_dataframe(tactical_tilt_weight).to_excel(
            writer,
            sheet_name="Assumptions",
            index=False,
        )

        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for column_cells in worksheet.columns:
                letter = column_cells[0].column_letter
                width = min(
                    52,
                    max(
                        10,
                        max(len(str(cell.value or "")) for cell in column_cells) + 2,
                    ),
                )
                worksheet.column_dimensions[letter].width = width

        long_sheet = writer.book["Weights_Long"]
        header_to_column = {
            cell.value: cell.column for cell in long_sheet[1] if cell.value is not None
        }
        for header in ("weight", "category_weight"):
            column = header_to_column[header]
            for row in range(2, long_sheet.max_row + 1):
                long_sheet.cell(row=row, column=column).number_format = "0.0000%"
        for header in ("weight_pct", "category_weight_pct"):
            column = header_to_column[header]
            for row in range(2, long_sheet.max_row + 1):
                long_sheet.cell(row=row, column=column).number_format = "0.0000"

        wide_sheet = writer.book["Weights_Wide"]
        for column in range(len(PORTFOLIO_COLUMNS) + 1, wide_sheet.max_column + 1):
            for row in range(2, wide_sheet.max_row + 1):
                wide_sheet.cell(row=row, column=column).number_format = "0.0000%"

    return output


# Ready-to-use objects for notebook/console users.
portfolio_weights_df = build_portfolio_weights()
portfolio_weights_wide_df = portfolio_weights_wide(portfolio_weights_df)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load and print one Proposal Tool portfolio allocation."
    )
    parser.add_argument(
        "--currency",
        type=str.upper,
        choices=CURRENCIES,
        default="USD",
        help="Portfolio currency (default: USD).",
    )
    parser.add_argument(
        "--allocation",
        default="Full",
        help='Asset allocation type: Full, Core, "Ex HFs" or "Ex Alts" (default: Full).',
    )
    parser.add_argument(
        "--risk-level",
        "--risk",
        dest="risk_level",
        default="Mod",
        help='Risk level or code, for example "Mod Agg" or MA (default: Mod).',
    )
    real_estate_group = parser.add_mutually_exclusive_group()
    real_estate_group.add_argument(
        "--exclude-real-estate",
        "--exclude-re",
        dest="exclude_real_estate",
        action="store_true",
        help=(
            "Exclude Real Estate. When omitted, Core/Ex Alts exclude it and "
            "Full/Ex HFs include it."
        ),
    )
    real_estate_group.add_argument(
        "--include-real-estate",
        "--include-re",
        "--no-exclude-real-estate",
        "--no-exclude-re",
        dest="exclude_real_estate",
        action="store_false",
        help="Explicitly include Real Estate.",
    )
    parser.set_defaults(exclude_real_estate=None)
    parser.add_argument(
        "--exclude-tactical-asset-allocation",
        "--exclude-taa",
        dest="exclude_tactical_asset_allocation",
        action="store_true",
        help="Exclude the Tactical Tilt portfolio.",
    )
    parser.add_argument(
        "--include-zero-weights",
        action="store_true",
        help="Print excluded assets with a 0.00%% weight.",
    )
    parser.add_argument(
        "--allow-ui-unavailable",
        action="store_true",
        help="Allow theoretical combinations disabled in the current UI.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """Load one UI-selected portfolio and print its asset allocation."""

    args = _parse_args(argv)
    ui_inputs: Dict[str, object] = {
        "currency": args.currency,
        "allocation": args.allocation,
        "riskLevel": args.risk_level,
        "excludeTAA": args.exclude_tactical_asset_allocation,
    }
    if args.exclude_real_estate is not None:
        ui_inputs["excludeRE"] = args.exclude_real_estate

    try:
        selected = load_portfolio_weights_from_ui(
            ui_inputs,
            include_zero_weights=args.include_zero_weights,
            require_ui_available=not args.allow_ui_unavailable,
        )
    except (KeyError, LookupError, ValueError) as exc:
        raise SystemExit(f"Unable to load portfolio weights: {exc}") from exc

    portfolio = selected.iloc[0]
    allocation = selected.loc[
        :, ["code", "reporting_name", "category", "weight_pct"]
    ].copy()
    allocation.rename(
        columns={
            "code": "Code",
            "reporting_name": "Reporting Name",
            "category": "Category",
            "weight_pct": "Weight",
        },
        inplace=True,
    )

    print(f"\n{portfolio['portfolio_name']}")
    print("=" * len(str(portfolio["portfolio_name"])))
    print(
        allocation.to_string(
            index=False,
            formatters={"Weight": lambda value: f"{value:,.2f}%"},
        )
    )
    print(f"\nTotal: {selected['weight'].sum():.2%}")


if __name__ == "__main__":
    main()
