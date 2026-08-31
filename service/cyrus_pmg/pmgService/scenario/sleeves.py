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

Implementation variants (D29)
-----------------------------
The library is not one library but four. An implementation variant is chosen
before any sleeve is, and it decides two things: which sleeves a category
offers at all, and what those sleeves contain. The same sleeve name can carry
different products under different variants - a US Onshore book reaches US
mutual funds and SMAs, an Irish Onshore book reaches UCITS - which is why the
variant has to be settled before the sleeve pickers mean anything.

BASELINE below is the PMG Multi-Asset Portfolio variant and is the library as
it stood before variants existed, unchanged. The other three are expressed as
deltas against it in _VARIANT_OFFERS, where a category maps to a list whose
entries are either:

    'Funds Only'          take that BASELINE sleeve verbatim
    {'name': ..., ...}    a sleeve specific to this variant

A category absent from a variant's map offers no sleeves at all under that
variant. That is a real state, not an error - see listSleeves' note on what it
means for the completeness gate.

All of it is stub data standing in for the PMG-maintained source, and the
per-variant product mixes are illustrative rather than authored by PMG. Open
item 18 in the spec records that they need replacing before anyone sees them.
"""

from __future__ import annotations

# The order the UI offers them in. First is not a default - nothing is
# selected until a PWA selects it (spec 8.1).
VARIANTS = [
    'PMG Multi-Asset Portfolio',
    'PMG ESG',
    'US Onshore',
    'Irish Onshore',
]


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

BASELINE = {
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


# ---------------------------------------------------------------------------
# The variant deltas. A bare string reuses the BASELINE sleeve of that name; a
# dict is a sleeve this variant alone offers. Products still carry the eleven
# fields and their weights still sum to 1 - _assertWellFormed proves both at
# import, because a sleeve whose weights do not sum to 1 silently corrupts
# every printed weight downstream of it.
# ---------------------------------------------------------------------------
_VARIANT_OFFERS = {
    'PMG ESG': {
        'Investment Grade Fixed Income': [
            {'name': 'ESG Core Fixed Income', 'products': [
                P('GS ESG US Corporate Bond Fund', 'GSECX', 'IG Corporate', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.44, 0.30, 0.55),
                P('GS Access ESG Aggregate ETF', 'GSEA', 'Aggregate', 'Passive', 'ETF', 'Internal', 'Daily', 'USD', 0.16, 0.30, 0.45)]},
            {'name': 'Green & Social Bonds', 'products': [
                P('GS Green Bond Fund', 'GSGBX', 'Green Bonds', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.48, 0.30, 0.60),
                P('Ashfield Social Impact Bond', 'ASIBX', 'Social Bonds', 'Active', 'Mutual Fund', 'External', 'Daily', 'Local', 0.62, 0.30, 0.40)]},
        ],
        'Other Fixed Income': [
            {'name': 'ESG High Yield & EM', 'products': [
                P('GS ESG High Yield Fund', 'GSEHX', 'High Yield', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.76, 0.30, 0.55),
                P('GS ESG Emerging Markets Debt', 'GSEDX', 'EM Debt', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.89, 0.30, 0.45)]},
        ],
        'Public Equity': [
            {'name': 'ESG Core Equity', 'products': [
                P('GS Access ESG US Equity ETF', 'GSEU', 'US Large Cap', 'Passive', 'ETF', 'Internal', 'Daily', 'USD', 0.14, 0.30, 0.44),
                P('GS ESG International Equity', 'GSIEX', 'Intl Developed', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'Local', 0.72, 0.30, 0.32),
                P('GS ESG Emerging Markets Equity', 'GSEEX', 'Emerging Markets', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'Local', 1.05, 0.30, 0.24)]},
            {'name': 'Climate Transition', 'products': [
                P('GS Climate Solutions Fund', 'GCSLX', 'Global Thematic', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'Local', 0.88, 0.30, 0.55),
                P('Northbrook Paris-Aligned Equity', 'NPAEX', 'Intl Developed', 'Active', 'Mutual Fund', 'External', 'Daily', 'Local', 0.79, 0.30, 0.45)]},
        ],
        'Hedge Funds': [
            {'name': 'ESG-Screened Multi-Strategy', 'products': [
                P('GS HedgeWorks ESG Multi-Strategy', '—', 'Multi-Strategy', 'Active', 'SMA', 'Internal', 'Quarterly', 'USD', 1.38, 0.30, 1.00)]},
        ],
        'Private Equity': [
            {'name': 'Impact Private Equity', 'products': [
                P('GS Sustainable Investing Group Fund', '—', 'Impact', 'Active', 'SMA', 'Internal', 'Drawdown', 'USD', 1.55, 0.30, 0.60),
                P('GS Growth Equity Partners', '—', 'Growth', 'Active', 'SMA', 'Internal', 'Drawdown', 'USD', 1.50, 0.30, 0.40)]},
        ],
        'Other Private Assets': [
            {'name': 'Energy Transition & Real Assets', 'products': [
                P('Calderwood Energy Transition', '—', 'Energy', 'Active', 'SMA', 'External', 'Drawdown', 'EUR', 1.38, 0.30, 0.55),
                P('GS Sustainable Real Estate', '—', 'Core Real Estate', 'Active', 'SMA', 'Internal', 'Drawdown', 'USD', 1.32, 0.30, 0.45)]},
        ],
        'Asset Allocation Strategies': [
            {'name': 'ESG Tactical Tilt Fund', 'products': [
                P('ESG Tactical Tilt Fund', 'ETTF', 'Tactical Tilts', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.88, 0.30, 1.00)]},
        ],
    },

    # US-domiciled vehicles only: mutual funds, ETFs and SMAs. No UCITS, and
    # every exposure struck in USD.
    'US Onshore': {
        'Investment Grade Fixed Income': [
            'GSAM Separately Managed Account',
            'Funds Only',
            'ETF & Mutual Funds',
        ],
        'Other Fixed Income': [
            'High Yield & EM Funds',
            'ETF Only',
        ],
        'Public Equity': [
            'Active-Passive',
            'Passive',
            'Concentrated Active',
        ],
        'Hedge Funds': [
            {'name': 'Registered Liquid Alternatives', 'products': [
                P('GS Absolute Return Tracker', 'GARTX', 'Multi-Strategy', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 0.96, 0.30, 0.60),
                P('GS Managed Futures Strategy', 'GMFAX', 'Managed Futures', 'Active', 'Mutual Fund', 'Internal', 'Daily', 'USD', 1.08, 0.30, 0.40)]},
            'Multi-Strategy Fund of Funds',
        ],
        'Private Equity': [
            'Diversified Vintage Program',
            'Buyout Focus',
        ],
        'Other Private Assets': [
            'Real Estate & Private Credit',
            'Private Credit Focus',
        ],
        'Asset Allocation Strategies': [
            'Tactical Tilt Fund',
        ],
    },

    # Irish-domiciled UCITS. Daily-dealing fund vehicles, no SMAs and no US
    # mutual funds, so the private-asset categories reach far less: an ICAV
    # feeder rather than the drawdown programmes.
    'Irish Onshore': {
        'Investment Grade Fixed Income': [
            {'name': 'UCITS Core Fixed Income', 'products': [
                P('GS Global Credit Portfolio (UCITS)', '—', 'IG Corporate', 'Active', 'UCITS', 'Internal', 'Daily', 'EUR', 0.52, 0.30, 0.55),
                P('GS Sterling Credit Portfolio (UCITS)', '—', 'IG Corporate', 'Active', 'UCITS', 'Internal', 'Daily', 'GBP', 0.54, 0.30, 0.25),
                P('GS Global Short Duration (UCITS)', '—', 'Short Duration', 'Active', 'UCITS', 'Internal', 'Daily', 'EUR', 0.40, 0.30, 0.20)]},
            {'name': 'UCITS Passive', 'products': [
                P('GS Access Global Aggregate (UCITS)', '—', 'Aggregate', 'Passive', 'UCITS', 'Internal', 'Daily', 'EUR', 0.18, 0.30, 0.60),
                P('GS Access Euro Government (UCITS)', '—', 'Government', 'Passive', 'UCITS', 'Internal', 'Daily', 'EUR', 0.15, 0.30, 0.40)]},
        ],
        'Other Fixed Income': [
            {'name': 'UCITS High Yield & EM', 'products': [
                P('GS Global High Yield (UCITS)', '—', 'High Yield', 'Active', 'UCITS', 'Internal', 'Daily', 'EUR', 0.81, 0.30, 0.55),
                P('GS Emerging Markets Debt (UCITS)', '—', 'EM Debt', 'Active', 'UCITS', 'Internal', 'Daily', 'EUR', 0.94, 0.30, 0.45)]},
        ],
        'Public Equity': [
            {'name': 'UCITS Core Equity', 'products': [
                P('GS Access World Equity (UCITS)', '—', 'Global Developed', 'Passive', 'UCITS', 'Internal', 'Daily', 'EUR', 0.20, 0.30, 0.50),
                P('GS Europe Core Equity (UCITS)', '—', 'Europe', 'Active', 'UCITS', 'Internal', 'Daily', 'EUR', 0.78, 0.30, 0.28),
                P('GS Emerging Markets Equity (UCITS)', '—', 'Emerging Markets', 'Active', 'UCITS', 'Internal', 'Daily', 'EUR', 1.08, 0.30, 0.22)]},
            {'name': 'UCITS Passive', 'products': [
                P('GS Access World Equity (UCITS)', '—', 'Global Developed', 'Passive', 'UCITS', 'Internal', 'Daily', 'EUR', 0.20, 0.30, 0.70),
                P('GS Access EM Equity (UCITS)', '—', 'Emerging Markets', 'Passive', 'UCITS', 'Internal', 'Daily', 'EUR', 0.32, 0.30, 0.30)]},
        ],
        'Hedge Funds': [
            {'name': 'UCITS Liquid Alternatives', 'products': [
                P('GS Absolute Return (UCITS)', '—', 'Multi-Strategy', 'Active', 'UCITS', 'Internal', 'Daily', 'EUR', 1.12, 0.30, 0.60),
                P('Calderwood Global Macro (UCITS)', '—', 'Global Macro', 'Active', 'UCITS', 'External', 'Daily', 'EUR', 1.24, 0.30, 0.40)]},
        ],
        'Private Equity': [
            {'name': 'ICAV Private Markets Feeder', 'products': [
                P('GS Private Markets ICAV Feeder', '—', 'Buyout', 'Active', 'ICAV', 'Internal', 'Drawdown', 'EUR', 1.58, 0.30, 1.00)]},
        ],
        'Other Private Assets': [
            {'name': 'ICAV Private Credit Feeder', 'products': [
                P('GS Private Credit ICAV Feeder', '—', 'Private Credit', 'Active', 'ICAV', 'Internal', 'Quarterly', 'EUR', 1.48, 0.30, 1.00)]},
        ],
        'Asset Allocation Strategies': [
            {'name': 'Tactical Tilt Fund (UCITS)', 'products': [
                P('Tactical Tilt Fund (UCITS)', '—', 'Tactical Tilts', 'Active', 'UCITS', 'Internal', 'Daily', 'EUR', 0.92, 0.30, 1.00)]},
        ],
    },
}


def _resolveVariant(variant: str) -> dict:
    """Compose one variant's {category: [sleeve]} map from BASELINE + deltas."""
    offers = _VARIANT_OFFERS.get(variant)
    if offers is None:
        return BASELINE
    resolved = {}
    for category, entries in offers.items():
        sleeves = []
        for entry in entries:
            if isinstance(entry, str):
                found = next((s for s in BASELINE.get(category, [])
                              if s['name'] == entry), None)
                if found is None:
                    raise KeyError(
                        '%s: %s has no BASELINE sleeve named %r'
                        % (variant, category, entry))
                sleeves.append(found)
            else:
                sleeves.append(entry)
        resolved[category] = sleeves
    return resolved


SLEEVE_LIBRARY = {name: _resolveVariant(name) for name in VARIANTS}


def _assertWellFormed() -> None:
    """Every sleeve's weights sum to 1, and no variant offers a name twice.

    Weights that do not sum to 1 do not fail loudly - they quietly scale every
    printed weight and notional in the category, and the workbook and the
    screen would agree with each other while both being wrong. Cheaper to
    prove here, once, at import.
    """
    for variant, library in SLEEVE_LIBRARY.items():
        for category, sleeves in library.items():
            names = [s['name'] for s in sleeves]
            if len(names) != len(set(names)):
                raise ValueError('%s / %s: duplicate sleeve name' % (variant, category))
            for sleeve in sleeves:
                total = sum(p['weight'] for p in sleeve['products'])
                if abs(total - 1.0) > 1e-9:
                    raise ValueError(
                        '%s / %s / %s: product weights sum to %r, not 1'
                        % (variant, category, sleeve['name'], total))


_assertWellFormed()


def variantExists(variant) -> bool:
    return variant in SLEEVE_LIBRARY


def listSleeves(category: str, variant: str) -> list:
    """The sleeve library for one category under one variant.

    An unknown variant returns nothing rather than falling back to a default:
    the caller has to have chosen one, and quietly serving the Multi-Asset
    library to a book that cannot hold it is the worse failure. An empty list
    is also a legitimate answer for a known variant - the variant reaches no
    sleeve for that category - and the completeness gate reports it as such
    instead of waiting for a choice that cannot be made.
    """
    return SLEEVE_LIBRARY.get(variant, {}).get(category, [])


def sleeveExists(category: str, name, variant: str) -> bool:
    return any(s['name'] == name for s in listSleeves(category, variant))
