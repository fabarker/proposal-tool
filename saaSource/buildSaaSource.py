#!/usr/bin/env python3
"""Build the stand-in SAA source: every portfolio the downstream system offers.

This is the *supplying database* the Proposal Tool will bake from, expressed
as the three-column extract that database would hand over:

    PortfolioName        AssetTicker        Weight

One row per holding. The portfolio name is the key, and it is the only key:
currency, risk level and allocation type are read back out of it, which is why
the vocabularies below are ordered and closed. Hedging is NOT in the name - the
same weights are analysed under each hedging assumption, so it joins the key
only when the analytics are run.

    USD Moderate Full ex-RAs
    EUR Higher Risk ex-HFs ex-RAs
    └─┘ └──────────┘ └──────────┘
    ccy   risk level   allocation type

An all-equity book has no alternatives to include or exclude, so it carries no
allocation type and its name is two fields, not three:

    CHF All Equity

Note the names cannot be parsed by splitting on spaces: both the risk level and
the allocation type contain them. A reader has to match longest-token-first
against these vocabularies, which is the reason they are data rather than a
regular expression.

THE WEIGHTS ARE FICTITIOUS. The asset tickers are the real ones the tool
already carries (portfolio_weights.ASSET_METADATA); the allocations across them
are invented to be plausible, ordered by risk, and internally consistent - each
portfolio sums to exactly 1. Nothing here should reach a client.

Usage::

    python3 buildSaaSource.py                 # writes saaPortfolios.xlsx
    python3 buildSaaSource.py --csv           # writes saaPortfolios.csv too
    python3 buildSaaSource.py --census        # print the token census, write nothing
"""

from __future__ import annotations

import argparse
import os

# ── the vocabularies, in order ───────────────────────────────────────────────
# Risk runs least to most risky. That order is the one thing a name cannot
# tell you, so it lives here and nowhere else; the score beside it drives the
# glide path below.
CURRENCIES = ('USD', 'GBP', 'CHF', 'EUR')

RISK_LEVELS = (
    ('LowVol',      10),
    ('Conservative', 25),
    ('ConsMod',      38),
    ('Moderate',     45),
    ('ModAgg',       58),
    ('Agg',          72),
    ('Higher Risk',  85),
    ('All Equity',   98),
)

# Each allocation type is the set of categories it admits. "RAs" are real
# assets - the real-estate line; private credit is not one.
ALLOCATION_TYPES = (
    'Full',
    'Core',
    'ex-Alts',
    'ex-HFs',
    'ex-HFs ex-RAs',
    'Full ex-RAs',
)

# ── the asset universe: real tickers, from ASSET_METADATA ────────────────────
# The tactical tilt fund (LHUT1T3) is deliberately absent: tactical allocation
# is an implementation choice, and no strategic portfolio carries it (D50).
CATEGORIES = {
    'Investment Grade Fixed Income': (('LHTRYIN', 1.00),),
    'Other Fixed Income':            (('LHYIELD', 1.00),),
    'Public Equity': (
        ('FRUS1GR', 0.22), ('FRUS1VA', 0.22), ('FRUSS2L', 0.08),
        ('MSEXUKL', 0.14), ('MSUTDKL', 0.05), ('MSJPANL', 0.07),
        ('MSPXJPL', 0.07), ('MSEMKF$', 0.15),
    ),
    'Hedge Funds': (
        ('CSTEVDH', 0.35), ('CSTLNSH', 0.40), ('CSFBMTT', 0.25),
    ),
    'Private Equity': (
        ('PE_BUYOUT', 0.55), ('PE_GROWTH', 0.30), ('PE_VENTURE', 0.15),
    ),
    'Other Private Assets': (
        ('PRIVATE_CREDIT', 0.60), ('PA_REAL_ESTATE', 0.40),
    ),
}

REAL_ASSET_TICKERS = {'PA_REAL_ESTATE'}

# What each allocation type excludes.
EXCLUDES = {
    'Full':          set(),
    'Core':          {'Private Equity', 'Other Private Assets'},
    'ex-Alts':       {'Hedge Funds', 'Private Equity', 'Other Private Assets'},
    'ex-HFs':        {'Hedge Funds'},
    'ex-HFs ex-RAs': {'Hedge Funds'},
    'Full ex-RAs':   set(),
}
# Types whose name says ex-RAs drop the real-asset lines as well.
DROPS_REAL_ASSETS = {'ex-HFs ex-RAs', 'Full ex-RAs'}

# ── the glide path: category shares by risk score, for a Full portfolio ──────
# Ordered least to most risky. Fixed income gives way to equity; alternatives
# peak in the middle. All Equity keeps a private-equity sleeve, private equity
# being equity - which is what makes the six allocation types distinguishable
# even at the top of the ladder.
GLIDE = {
    10: {'Investment Grade Fixed Income': .62, 'Other Fixed Income': .10,
         'Public Equity': .16, 'Hedge Funds': .07,
         'Private Equity': .03, 'Other Private Assets': .02},
    25: {'Investment Grade Fixed Income': .50, 'Other Fixed Income': .10,
         'Public Equity': .24, 'Hedge Funds': .09,
         'Private Equity': .04, 'Other Private Assets': .03},
    38: {'Investment Grade Fixed Income': .40, 'Other Fixed Income': .09,
         'Public Equity': .32, 'Hedge Funds': .10,
         'Private Equity': .05, 'Other Private Assets': .04},
    45: {'Investment Grade Fixed Income': .34, 'Other Fixed Income': .09,
         'Public Equity': .38, 'Hedge Funds': .10,
         'Private Equity': .05, 'Other Private Assets': .04},
    58: {'Investment Grade Fixed Income': .25, 'Other Fixed Income': .08,
         'Public Equity': .47, 'Hedge Funds': .11,
         'Private Equity': .06, 'Other Private Assets': .03},
    72: {'Investment Grade Fixed Income': .16, 'Other Fixed Income': .07,
         'Public Equity': .57, 'Hedge Funds': .11,
         'Private Equity': .06, 'Other Private Assets': .03},
    85: {'Investment Grade Fixed Income': .08, 'Other Fixed Income': .05,
         'Public Equity': .68, 'Hedge Funds': .10,
         'Private Equity': .06, 'Other Private Assets': .03},
    98: {'Investment Grade Fixed Income': .00, 'Other Fixed Income': .00,
         'Public Equity': 1.00, 'Hedge Funds': .00,
         'Private Equity': .00, 'Other Private Assets': .00},
}

# Home bias: how much of the public-equity sleeve moves from US large cap to
# the local market. Keeps the four currencies from being copies of each other.
HOME_BIAS = {
    'USD': {},
    'GBP': {'MSUTDKL': +0.10, 'FRUS1GR': -0.05, 'FRUS1VA': -0.05},
    'EUR': {'MSEXUKL': +0.10, 'FRUS1GR': -0.05, 'FRUS1VA': -0.05},
    'CHF': {'MSEXUKL': +0.06, 'FRUS1GR': -0.03, 'FRUS1VA': -0.03},
}


# A risk level that is entirely public equity has no alternatives to include
# or exclude, so an allocation type describes nothing: the database carries ONE
# such portfolio per currency and its name has no allocation type in it. The UI
# then greys the allocation selector out by derivation - the facet is empty for
# that risk level - rather than by a rule that hardcodes the words "All Equity".
NO_ALLOCATION_TYPE = {'All Equity'}


def portfolioName(currency: str, risk: str, allocation: str = None) -> str:
    """The key, as the supplying database spells it."""
    if allocation is None:
        return '{} {}'.format(currency, risk)
    return '{} {} {}'.format(currency, risk, allocation)


def holdings(currency: str, score: int, allocation: str = None) -> list:
    """(ticker, weight) for one portfolio; weights sum to exactly 1."""
    excluded = EXCLUDES[allocation] if allocation else set()
    shares = {category: share for category, share in GLIDE[score].items()
              if category not in excluded and share > 0}

    rows = []
    for category, share in shares.items():
        members = [(ticker, weight) for ticker, weight in CATEGORIES[category]
                   if not (allocation in DROPS_REAL_ASSETS
                           and ticker in REAL_ASSET_TICKERS)]
        if not members:
            continue
        if category == 'Public Equity':
            bias = HOME_BIAS[currency]
            members = [(t, max(w + bias.get(t, 0.0), 0.0)) for t, w in members]
        within = sum(w for _, w in members)
        for ticker, weight in members:
            rows.append([ticker, share * weight / within])

    # Renormalise: dropping a category (or a real-asset line) leaves the book
    # short, and the survivors take the slack in proportion - which is what
    # "ex-" means in the name.
    total = sum(w for _, w in rows)
    rows = [[t, w / total] for t, w in rows]

    # Round, then give the residual to the largest holding, so every portfolio
    # in the extract sums to exactly 1 and a consumer can assert it.
    rows = [[t, round(w, 6)] for t, w in rows]
    residual = round(1.0 - sum(w for _, w in rows), 6)
    rows.sort(key=lambda r: -r[1])
    rows[0][1] = round(rows[0][1] + residual, 6)
    return rows


def build() -> list:
    """Every portfolio on offer, as extract rows."""
    out = []
    for currency in CURRENCIES:
        for risk, score in RISK_LEVELS:
            types = (None,) if risk in NO_ALLOCATION_TYPE else ALLOCATION_TYPES
            for allocation in types:
                name = portfolioName(currency, risk, allocation)
                for ticker, weight in holdings(currency, score, allocation):
                    out.append((name, ticker, weight))
    return out


def census(rows) -> None:
    """What a consumer would find before trusting a parser (the dry run)."""
    names = sorted({r[0] for r in rows})
    print('portfolios %d, rows %d, distinct tickers %d'
          % (len(names), len(rows), len({r[1] for r in rows})))
    print('\ncurrencies      %s' % ', '.join(CURRENCIES))
    print('risk levels     %s' % ', '.join(n for n, _ in RISK_LEVELS))
    print('allocation types %s' % ', '.join(ALLOCATION_TYPES))
    print('\nnames that do NOT split cleanly on whitespace into 3 fields: %d of %d'
          % (sum(1 for n in names if len(n.split()) != 3), len(names)))
    noType = [n for n in names if not any(n.endswith(' ' + a) for a in ALLOCATION_TYPES)]
    print('names carrying no allocation type at all: %d  e.g. %s'
          % (len(noType), ', '.join(noType[:2])))
    print('\nfirst five names:')
    for n in names[:5]:
        print('   ', n)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', action='store_true', help='also write a CSV')
    parser.add_argument('--census', action='store_true',
                        help='print the token census and write nothing')
    parser.add_argument('--out', default=os.path.dirname(os.path.abspath(__file__)))
    args = parser.parse_args()

    rows = build()

    # Invariants worth failing the build over.
    byName = {}
    for name, ticker, weight in rows:
        byName.setdefault(name, []).append((ticker, weight))
    for name, holds in byName.items():
        total = round(sum(w for _, w in holds), 6)
        assert total == 1.0, '{} sums to {}'.format(name, total)
        assert len({t for t, _ in holds}) == len(holds), '{} repeats a ticker'.format(name)
    perCurrency = sum(1 if risk in NO_ALLOCATION_TYPE else len(ALLOCATION_TYPES)
                      for risk, _ in RISK_LEVELS)
    expected = len(CURRENCIES) * perCurrency
    assert len(byName) == expected, '{} names, expected {}'.format(len(byName), expected)

    if args.census:
        census(rows)
        return

    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = 'Portfolios'
    sheet.append(['PortfolioName', 'AssetTicker', 'Weight'])
    for row in rows:
        sheet.append(list(row))
    sheet.column_dimensions['A'].width = 34
    sheet.column_dimensions['B'].width = 18
    sheet.column_dimensions['C'].width = 12
    sheet.freeze_panes = 'A2'
    for cell in sheet['C'][1:]:
        cell.number_format = '0.000000'
    xlsx = os.path.join(args.out, 'saaPortfolios.xlsx')
    book.save(xlsx)
    print('wrote %s  (%d portfolios, %d rows)' % (xlsx, len(byName), len(rows)))

    if args.csv:
        import csv as csvmod
        path = os.path.join(args.out, 'saaPortfolios.csv')
        with open(path, 'w', newline='', encoding='utf-8') as fh:
            writer = csvmod.writer(fh)
            writer.writerow(['PortfolioName', 'AssetTicker', 'Weight'])
            writer.writerows(rows)
        print('wrote %s' % path)


if __name__ == '__main__':
    main()
