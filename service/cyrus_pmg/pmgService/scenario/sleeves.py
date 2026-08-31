"""The PMG sleeve library - authored stub data behind list_sleeves.

The hand-off package supplies no separate sleeve source; the library the
prototype demonstrated is the authored data, lifted here minus its Cash
category (not in the supplied weight universe) and plus Asset Allocation
Strategies, whose single sleeve carries the Tactical Tilt Fund and is attached
automatically whenever the category is present (spec 2.6). Recorded as
deviation D9. Production replaces this module's tables with the PMG-maintained
source behind the same function.

A sleeve is a fixed block: (category, name) identity, products whose weights
sum to 1, every product carrying the eleven fields of spec 4.1.
"""

from __future__ import annotations


def _product(name, ticker, assetClass, style, vehicle, source, liquidity,
             exposureCurrency, productCost, managementFee, weight):
    return {
        'name': name, 'ticker': ticker, 'assetClass': assetClass,
        'style': style, 'vehicle': vehicle, 'source': source,
        'liquidity': liquidity, 'exposureCurrency': exposureCurrency,
        'productCost': productCost, 'managementFee': managementFee,
        'weight': weight,
    }


P = _product

SLEEVE_LIBRARY = {
    'Investment Grade Fixed Income': [
        {'name': 'GSAM Separately Managed Account', 'products': [
            P('GSAM Core Municipal SMA', '—', 'Municipals', 'Active', 'SMA', 'Internal', 'Daily', 'USD', 0.25, 0.30, 0.45),
            P('GSAM Intermediate Credit SMA', '—', 'IG Corporate', 'Active', 'SMA', 'Internal', 'Daily', 'USD', 0.28, 0.30, 0.35),
            P('GSAM Short Duration SMA', '—', 'Short Duration', 'Active', 'SMA', 'Internal', 'Daily', 'USD', 0.22, 0.30, 0.20)]},
        {'name': 'Funds Only', 'products': [
            P('GS US Corporate Bond Fund', 'GSUCX', 'IG Corporate', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.42, 0.30, 0.45),
            P('Ashfield Global Credit Fund', 'AGCIX', 'IG Corporate', 'Active', 'Mutual Fund', 'External', 'Daily', 'Local', 0.55, 0.30, 0.30),
            P('GS Short Duration Income', 'GSSDX', 'Short Duration', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.38, 0.30, 0.25)]},
        {'name': 'ETF & Mutual Funds', 'products': [
            P('GS Access IG Corporate ETF', 'GIGB', 'IG Corporate', 'Passive', 'ETF', 'Internal', 'Daily', 'USD', 0.14, 0.30, 0.40),
            P('GS Access Treasury 0-1 ETF', 'GBIL', 'Government', 'Passive', 'ETF', 'Internal', 'Daily', 'USD', 0.12, 0.30, 0.30),
            P('GS Core Fixed Income Fund', 'GCFIX', 'Aggregate', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.46, 0.30, 0.30)]},
    ],
    'Other Fixed Income': [
        {'name': 'High Yield & EM Funds', 'products': [
            P('GS High Yield Fund', 'GSHAX', 'High Yield', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.72, 0.30, 0.55),
            P('GS Emerging Markets Debt', 'GSDAX', 'EM Debt', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.85, 0.30, 0.45)]},
        {'name': 'Multi-Sector Funds', 'products': [
            P('GS Strategic Income Fund', 'GSZAX', 'Multi-Sector', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.79, 0.30, 0.60),
            P('Calderwood Local EM Debt', 'CLEDX', 'EM Debt', 'Active', 'Mutual Fund', 'External', 'Daily', 'Local', 0.91, 0.30, 0.40)]},
        {'name': 'ETF Only', 'products': [
            P('GS Access High Yield ETF', 'GHYB', 'High Yield', 'Passive', 'ETF', 'Internal', 'Daily', 'USD', 0.34, 0.30, 0.60),
            P('GS Access EM USD Bond ETF', 'GEMD', 'EM Debt', 'Passive', 'ETF', 'Internal', 'Daily', 'USD', 0.39, 0.30, 0.40)]},
    ],
    'Public Equity': [
        {'name': 'Active-Passive', 'products': [
            P('GS Access US Large Cap ETF', 'GSLC', 'US Large Cap', 'Passive', 'ETF', 'Internal', 'Daily', 'USD', 0.09, 0.30, 0.34),
            P('GS US Equity Insights Fund', 'GCSAX', 'US All Cap', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.71, 0.30, 0.22),
            P('Northbrook Intl Equity', 'NBIEX', 'Intl Developed', 'Active', 'Mutual Fund', 'External', 'Daily', 'Local', 0.68, 0.30, 0.24),
            P('GS Emerging Markets Equity', 'GEMAX', 'Emerging Markets', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'Local', 1.12, 0.30, 0.20)]},
        {'name': 'Passive', 'products': [
            P('GS Access US Large Cap ETF', 'GSLC', 'US Large Cap', 'Passive', 'ETF', 'Internal', 'Daily', 'USD', 0.09, 0.30, 0.46),
            P('GS Access Intl Equity ETF', 'GSIE', 'Intl Developed', 'Passive', 'ETF', 'Internal', 'Daily', 'Local', 0.25, 0.30, 0.32),
            P('GS Access EM Equity ETF', 'GEM', 'Emerging Markets', 'Passive', 'ETF', 'Internal', 'Daily', 'Local', 0.37, 0.30, 0.22)]},
        {'name': 'Concentrated Active', 'products': [
            P('GS Concentrated Growth SMA', '—', 'US Large Cap', 'Active', 'SMA', 'Internal', 'Daily', 'USD', 0.55, 0.30, 0.40),
            P('GS US Focused Value SMA', '—', 'US Large Cap', 'Active', 'SMA', 'Internal', 'Daily', 'USD', 0.55, 0.30, 0.32),
            P('Northbrook Focused Intl', 'NBFIX', 'Intl Developed', 'Active', 'Mutual Fund', 'External', 'Daily', 'Local', 0.94, 0.30, 0.28)]},
    ],
    'Hedge Funds': [
        {'name': 'Multi-Strategy Fund of Funds', 'products': [
            P('GS HedgeWorks Multi-Strategy', '—', 'Multi-Strategy', 'Active', 'SMA', 'Internal', 'Quarterly', 'USD', 1.35, 0.30, 0.60),
            P('Calderwood Global Macro', '—', 'Global Macro', 'Active', 'SMA', 'External', 'Quarterly', 'USD', 1.48, 0.30, 0.40)]},
        {'name': 'Direct Single Manager', 'products': [
            P('Ashfield Select Equity L/S', '—', 'Equity Long/Short', 'Active', 'SMA', 'External', 'Quarterly', 'USD', 1.62, 0.30, 0.55),
            P('Northbrook Relative Value', '—', 'Relative Value', 'Active', 'SMA', 'External', 'Monthly', 'USD', 1.55, 0.30, 0.45)]},
        {'name': 'Liquid Alternatives', 'products': [
            P('GS Absolute Return Tracker', 'GARTX', 'Multi-Strategy', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.96, 0.30, 0.60),
            P('GS Managed Futures Strategy', 'GMFAX', 'Managed Futures', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 1.08, 0.30, 0.40)]},
    ],
    'Private Equity': [
        {'name': 'Diversified Vintage Program', 'products': [
            P('GS Vintage Fund IX', '—', 'Secondaries', 'Active', 'SMA', 'Internal', 'Drawdown', 'USD', 1.25, 0.30, 0.40),
            P('GS Private Markets Buyout', '—', 'Buyout', 'Active', 'SMA', 'Internal', 'Drawdown', 'USD', 1.45, 0.30, 0.35),
            P('GS Growth Equity Partners', '—', 'Growth', 'Active', 'SMA', 'Internal', 'Drawdown', 'USD', 1.50, 0.30, 0.25)]},
        {'name': 'Buyout Focus', 'products': [
            P('GS Private Markets Buyout', '—', 'Buyout', 'Active', 'SMA', 'Internal', 'Drawdown', 'USD', 1.45, 0.30, 0.65),
            P('Ashfield Co-Investment', '—', 'Co-Investment', 'Active', 'SMA', 'External', 'Drawdown', 'EUR', 1.10, 0.30, 0.35)]},
        {'name': 'Growth & Venture', 'products': [
            P('GS Growth Equity Partners', '—', 'Growth', 'Active', 'SMA', 'Internal', 'Drawdown', 'USD', 1.50, 0.30, 0.55),
            P('GS Venture Access Fund', '—', 'Venture', 'Active', 'SMA', 'Internal', 'Drawdown', 'USD', 1.72, 0.30, 0.45)]},
    ],
    'Other Private Assets': [
        {'name': 'Real Estate & Private Credit', 'products': [
            P('GS Real Estate Partners', '—', 'Core Real Estate', 'Active', 'SMA', 'Internal', 'Drawdown', 'USD', 1.30, 0.30, 0.55),
            P('GS Private Credit Partners', '—', 'Private Credit', 'Active', 'SMA', 'Internal', 'Quarterly', 'USD', 1.40, 0.30, 0.45)]},
        {'name': 'Private Credit Focus', 'products': [
            P('GS Private Credit Partners', '—', 'Private Credit', 'Active', 'SMA', 'Internal', 'Quarterly', 'USD', 1.40, 0.30, 0.60),
            P('Ashfield Direct Lending', '—', 'Direct Lending', 'Active', 'SMA', 'External', 'Quarterly', 'USD', 1.35, 0.30, 0.40)]},
        {'name': 'Diversified Real Assets', 'products': [
            P('GS Real Assets Program', '—', 'Real Assets', 'Active', 'SMA', 'Internal', 'Drawdown', 'USD', 1.22, 0.30, 0.55),
            P('Calderwood Energy Transition', '—', 'Energy', 'Active', 'SMA', 'External', 'Drawdown', 'EUR', 1.38, 0.30, 0.45)]},
    ],
    'Asset Allocation Strategies': [
        {'name': 'Tactical Tilt Fund', 'products': [
            P('Tactical Tilt Fund', 'TTF', 'Tactical Tilts', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.85, 0.30, 1.00)]},
    ],
}


def listSleeves(category: str) -> list:
    """The sleeve library for one category. Unknown category -> empty list."""
    return SLEEVE_LIBRARY.get(category, [])


def sleeveExists(category: str, name) -> bool:
    return any(s['name'] == name for s in listSleeves(category))
