"""Tier 0 guard: the optimised return-beta path must be BIT-IDENTICAL.

The optimisation in ``AssetReturnEstimator.calc_return_betas`` (memoised
window statistic, dead FX conversion removed) is a pure performance change.
This test re-implements the ORIGINAL function verbatim and asserts the new
one matches it exactly, on real assets, plus the two invariants the new cache
key depends on:

  * betas do not vary with ``hedging_ratio`` (the converted asset was, and
    still is, unused), and
  * the discarded conversion returned an asset carrying the same schema
    object, so reading ``asset.schema`` is the identical call.

Requires a database and ``CAppConfig.setup()``; skipped when unavailable, so
the fast suite still runs offline. Run explicitly with:

    PYTHONPATH=. python3 -m pytest tests/test_tier0_beta_equivalence.py -q
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

pytestmark = pytest.mark.skipif(
    os.getenv('EPSILONPHI_LIVE', '') != '1',
    reason='needs a live epsilonPhi database; set EPSILONPHI_LIVE=1 to run',
)


def _original_calc_return_betas(asset, hedging_ratio=0.5, normalized=True):
    """The pre-optimisation implementation, verbatim, as the reference."""
    import pandas as pd

    schema_currency = asset.schema.currency
    asset_in_schema_currency = asset.convert_asset_to_currency(schema_currency, hedging_ratio)

    model = asset.schema.BaseModel
    factor_df = asset_in_schema_currency.schema.get_return_factors_panel()

    rx = asset.get_excess_return_df()
    y, X = rx.intersect_over_date_range(factor_df)
    assert np.all(y.index == X.index), 'Error - date mismatch in regression'

    win = 60
    start = 60
    end = y.shape[0] + 1

    betas = np.full((end - win, factor_df.shape[1]), np.nan)
    for i in range(start, end):
        X_prime = X.iloc[i - win:i].copy()
        y_prime = y.iloc[i - win:i].copy()
        orth_X = model.regression.orthogonalize_columns(X_prime, model.orthogonal_list)
        stdev = np.std(orth_X, axis=0, ddof=1) if normalized else 1
        eDfArray = X_prime / stdev
        _, b = model.regression.simple_regression_OLS_with_array(
            eDfArray.values, y_prime.values)
        betas[i - win] = b

    return pd.DataFrame(betas, index=factor_df.index[-betas.shape[0]:],
                        columns=factor_df.columns)


@pytest.fixture(scope='module')
def portfolio():
    from epsilonPhi.core.config.appConfig import CAppConfig
    CAppConfig.setup()
    from cyrus_pmg.pmgService.scenario import portfolio_weights as pw
    frame = pw.load_portfolio_weights(
        currency='USD', allocation='Core', risk_level='Mod',
        exclude_real_estate=True, exclude_tactical_asset_allocation=False,
        include_zero_weights=True)
    return pw.get_portfolio('USD', dict(zip(frame['code'], frame['weight'])),
                            hedging_option='Hedged')


def test_optimised_betas_are_bit_identical(portfolio):
    from epsilonPhi.core.estimator.assetReturnEstimator import AssetReturnEstimator
    for name in list(portfolio.get_asset_names())[:4]:
        asset = portfolio.get_asset(name)
        reference = _original_calc_return_betas(asset, asset.hedging_ratio, True)
        optimised = AssetReturnEstimator.calc_return_betas(asset, asset.hedging_ratio, True)
        assert np.array_equal(reference.values, optimised.values, equal_nan=True), name
        assert list(reference.columns) == list(optimised.columns)
        assert reference.index.equals(optimised.index)


def test_betas_do_not_vary_with_hedging_ratio(portfolio):
    """The premise of dropping hedging_ratio from the cache key."""
    from epsilonPhi.core.estimator.assetReturnEstimator import AssetReturnEstimator
    asset = portfolio.get_asset(list(portfolio.get_asset_names())[2])
    at = [AssetReturnEstimator.calc_return_betas(asset, r, True) for r in (0.0, 0.5, 1.0)]
    for other in at[1:]:
        assert np.array_equal(at[0].values, other.values, equal_nan=True)


def test_currency_conversion_preserves_the_schema_object(portfolio):
    """The premise of dropping the discarded conversion."""
    asset = portfolio.get_asset(list(portfolio.get_asset_names())[0])
    converted = asset.convert_asset_to_currency(asset.schema.currency, asset.hedging_ratio)
    assert converted.schema is asset.schema


def test_cache_key_separates_currencies(portfolio):
    from epsilonPhi.core.estimator.assetReturnEstimator import AssetReturnEstimator
    from cyrus_pmg.pmgService.scenario import portfolio_weights as pw
    frame = pw.load_portfolio_weights(
        currency='CHF', allocation='Core', risk_level='Mod',
        exclude_real_estate=True, exclude_tactical_asset_allocation=False,
        include_zero_weights=True)
    chf = pw.get_portfolio('CHF', dict(zip(frame['code'], frame['weight'])),
                           hedging_option='Hedged')
    name = list(portfolio.get_asset_names())[2]
    usd_key = AssetReturnEstimator._return_beta_cache_key(portfolio.get_asset(name), True)
    chf_key = AssetReturnEstimator._return_beta_cache_key(chf.get_asset(name), True)
    assert usd_key != chf_key, 'currency must be part of the key'
