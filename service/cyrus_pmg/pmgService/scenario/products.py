"""The product catalogue - the table every sleeve selects from (D58), kept in
the repository's own database and maintained from the console (D165).

It used to be read, read only, from a delivered extract (D56): a CSV or XLSX
named by SCENARIO_PRODUCTS_SOURCE, one row per product, one column per field -

    ProductId  Name  Ticker  AssetClass  Category  Style  Vehicle  ShareClass
    Source  Liquidity  ExposureCurrency  IssueCurrency  ProductCost  FeeGroup
    DistributionYield  MinimumInvestment

- Category, ShareClass, IssueCurrency and the last two optional in the file
and blank or None when absent (D63, D81, D178, D184).

A product has LISTINGS (D207): the ways it can be bought - a share class of a
fund, a mandate code of a separate account - each with its own identifiers, issue
currency, hedging, share class, exposure currency, asset class, product cost,
distribution yield, minimum and benchmark. What the product is - its id,
name, category, style, vehicle, source, liquidity and fee group - is the same
whichever listing is held. A base currency is not a property of a product,
and any proposal may hold any product. The catalogue holds facts only
(D214): nothing on a product or a listing speaks of a proposal - the
rankings by base currency it once kept (D207, D208) are gone. Its listings
are kept in an order the console sets by dragging them, and until the rules
for choosing between them are settled a proposal holds the FIRST one,
whatever its currency and its amount (``asHeld``); the product carries the
first one's values at its top level. An ISIN or a CUSIP may repeat across
a product's listings - one class quoted in several currencies - but belongs
to that product alone, and so do a SEDOL and a mandate code. The file gives a
product one row per listing under the same ProductId, in their order, with
a MandateCode and a Hedging column; Rank columns a file kept before are
not read. A file in the layout before listings is still read (D182): one
row per base currency, named in a BaseCurrency column, the All row first
and each currency's row a listing after it.

The catalogue now lives in the repository's file beside the
sleeves (sleeveRepo, schema 7). That extract is read twice in its life: once,
on the first open after the upgrade, to fill the catalogue (``seed``); and
whenever an admin imports a file from the console or the command line, which
is previewed, product by product and field by field, before anything moves.
Changing the file on disk changes nothing by itself.

Nothing is ever destroyed (the sleeves' rule, D65). Every write appends a
revision holding the whole product as it stood after it, with who did it,
when, and their note; a product leaves the catalogue by being WITHDRAWN,
which keeps the row and its history and can be undone. A product a live
sleeve holds cannot be withdrawn - the refusal names the sleeves - and an id
is never reused. Each product carries the stamp of its current values: the
console calls it *Last edited* (who and when), beside the version number.

A product served from here carries exactly the fields a sleeve product always
carried (spec 4.1 minus the management fee, D51) so nothing downstream - the
implementation table, the workbook, the fee resolver - can tell where it came
from. A listing has no ticker (D220): it is named by its ISIN, SEDOL or CUSIP.
The console reads the same products with their stamps (``entries``).

What a product must be, checked at every save and on every row of an import:
a unique id, a name, a cost that is a number and not negative, a share class
from the pair or none, figures that are numbers and not negative, and a fee
group the fee card prices - the last matters most, since an unpriced group
would otherwise fail at export, under RDR, long after the product had been
placed in a sleeve. A save or an import also needs the six words a product is
described by (asset class, style, vehicle, source, liquidity, exposure
currency); the first load does not, so a file the tool used to read whole is
read whole once more on the day it moves in.

Reads are cheap: the catalogue is kept in memory against the file's stamp
(sleeveRepo.fileStamp) and re-read through a plain read-only connection when
the file moves, the way the overlay rules are (D155). A write from any
process is seen by the next read in every process.

A separate account - an SMA, a product whose vehicle says so - is listed
under its mandate codes (D197, D207): each listing of an SMA names the code
the account is opened under, the currency it is run in as its issue
currency and how much of it is hedged. HEDGING is a percentage (D224): the
proportion hedged, 100 fully, 0 not at all, 50 half, or blank for not said,
on a fund's share class or an SMA's mandate; an SMA's mandate may also be
ticked ISG, ISG's hedged version. The words it was before read as the
percentage: Hedged 100, Unhedged 0, ISG Hedged 50 and ticked ISG. The proposal's hedging policy plays no part in which one is
held. The code shows nowhere in a proposal's files;
the account opening request reads it. The word "mandate" on its own is the
client's mandate everywhere else in the tool.

A fund has THREE LEVELS (D218): the product, its share classes and their
listings. A listing as stored carries the three together - what the product
is at the top, the share class's values (its ISIN, class letter, income,
hedging, class and exposure currency, domicile, UCITS compliance,
replication, UK reporting, cost, yield, minimum and benchmark) and the
listing's own (its exchange, CUSIP, SEDOL and issue currency, D220) - and
the rows of one ISIN are one share class: they must agree on every one of the class's
values, and that is checked wherever a product is read. A class with no ISIN
is the rows that agree on all of them. Within a class a listing is its
exchange and issue currency. An SMA keeps its rows, a mandate code each, and
refuses the class's and the listing's fields. A product stored or recorded
before the levels reads as they read it (``modernise``): a ticker shaped
like an ISIN is the ISIN, Accumulating or Distributing where the class letter
goes is the income, the asset class moves up to the product, and a hedged
class's exposure is the currency it is hedged to.

Each listing names its BENCHMARK (D204), which the account opening request
reads for an SMA. A file without a Benchmark column leaves the benchmarks
the catalogue holds as they are, since a file the desk kept before there
were benchmarks must not wipe the ones typed since; one without a Hedging
column leaves the hedging, and one without a MandateCode column leaves an
SMA's coded listings whole.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import math
import os
import re
import threading

from .csvText import unwrapRows
from .saaKeys import CURRENCIES
from .types import ValidationError

_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', '..', '..', '..', 'productSource', 'products.csv')

COLUMNS = ['ProductId', 'Strategy', 'AssetClass', 'Style', 'Vehicle', 'Source', 'Liquidity',
           'ExposureCurrency', 'ProductCost', 'FeeGroup']
# Columns a delivery may leave out entirely, or leave blank on a row: each is
# served as blank when it is absent, so an older extract still loads (D63,
# D81, D218). DistributionYield and MinimumInvestment are figures the
# catalogue view compares on and must parse as numbers.
OPTIONAL = ['Category', 'Provider', 'ISIN', 'ShareClass', 'Income', 'Hedging', 'ClassCurrency',
            'Domicile', 'UCITSCompliant', 'Replication', 'UKReporting', 'DistributionYield',
            'MinimumInvestment', 'Benchmark', 'Exchange', 'CUSIP', 'SEDOL', 'IssueCurrency',
            'MandateCode']
#: Columns a file kept before that are read only to say what they become
#: (D220): a Ticker shaped like an ISIN is the ISIN, any other is not kept;
#: a Regulation of UCITS is UCITS compliant, of AIF not.
OLD_COLUMNS = ['Ticker', 'Regulation']
#: The product's own category (D178), the sleeve category the catalogue bands
#: it under: a file may head the column with any of these.
CATEGORY_HEADERS = ('Category', 'Catagory', 'Group')
#: Other headings a file may give a column, lower-cased and stripped of all
#: but letters and digits (D184, D207, D218).
_HEADINGS = {
    # the product's name is its strategy's (D220); Name is still read
    'Strategy': ('strategy', 'strategyname', 'name', 'productname'),
    'UCITSCompliant': ('ucitscompliant', 'ucits', 'isucits'),
    'CUSIP': ('cusip',),
    'SEDOL': ('sedol',),
    'IssueCurrency': ('issuecurrency', 'issueccy', 'issuercurrency', 'issuerccy', 'quotecurrency',
                      'quoteccy', 'dealingcurrency', 'tradingcurrency'),
    'MandateCode': ('mandatecode', 'mandate'),
    'MandateName': ('mandatename', 'variantname'),
    'Hedging': ('hedging', 'hedgingversion', 'currencyhedging', 'hedgeratio', 'hedgedpercent',
                'hedgingpercent', 'hedgingpercentage', 'hedgeproportion', 'hedgelevel'),
    'ISG': ('isg', 'isghedged', 'isgversion'),
    'Income': ('income', 'incometreatment', 'dividendtreatment'),
    'UKReporting': ('ukreporting', 'ukreportingstatus', 'ukreportingfund'),
    'Exchange': ('exchange', 'listingexchange'),
    'ClassCurrency': ('classcurrency', 'classccy', 'shareclasscurrency'),
    'Domicile': ('domicile', 'domicilecountry'),
    'Replication': ('replication', 'replicationmethod'),
    'Provider': ('provider', 'fundprovider'),
    'ISIN': ('isin',),
}

#: What a class's income can be: distributing, or accumulating - the field
#: that was the share class (D81, D218). Two values and a blank, checked on
#: load - a typo is refused rather than printed.
INCOMES = ('Distributing', 'Accumulating')
SHARE_CLASSES = INCOMES
#: The short forms a delivery used before the words were spelled out (D164),
#: still read, and served as the word.
INCOME_SHORT = {'Dis': 'Distributing', 'Dist': 'Distributing', 'Acc': 'Accumulating'}
SHARE_CLASS_SHORT = INCOME_SHORT

NO_TICKER = '—'

#: Every field a product carries, in the order the file and the console give
#: them - the product, then the share class, then the listing, then an SMA's
#: code (D218): the key the package uses, the file's column, and the words a
#: message or a history line uses for it.
_FIELDS = [
    ('productId', 'ProductId', 'Product ID'),
    ('name', 'Strategy', 'Strategy'),
    ('provider', 'Provider', 'Provider'),
    ('category', 'Category', 'Category'),
    ('assetClass', 'AssetClass', 'Asset class'),
    ('style', 'Style', 'Style'),
    ('vehicle', 'Vehicle', 'Vehicle'),
    ('source', 'Source', 'Source'),
    ('liquidity', 'Liquidity', 'Liquidity'),
    ('feeGroup', 'FeeGroup', 'Fee group'),
    # the share class (D218)
    ('isin', 'ISIN', 'ISIN'),
    ('shareClass', 'ShareClass', 'Share class'),
    ('income', 'Income', 'Income'),
    # the proportion hedged, a percentage (D224)
    ('hedging', 'Hedging', 'Hedging'),
    # an SMA mandate that is ISG's hedged version (D224): Yes or blank
    ('isg', 'ISG', 'ISG'),
    ('classCurrency', 'ClassCurrency', 'Class currency'),
    ('exposureCurrency', 'ExposureCurrency', 'Exposure currency'),
    ('domicile', 'Domicile', 'Domicile'),
    # whether the class is a UCITS (D220): Yes, No or blank for not said
    ('ucits', 'UCITSCompliant', 'UCITS compliant'),
    ('replication', 'Replication', 'Replication'),
    ('ukReporting', 'UKReporting', 'UK reporting'),
    ('productCost', 'ProductCost', 'Product cost'),
    ('distributionYield', 'DistributionYield', 'Distribution yield'),
    ('minimumInvestment', 'MinimumInvestment', 'Minimum investment'),
    # what it is measured against (D204): optional, blank for not given
    ('benchmark', 'Benchmark', 'Benchmark'),
    # the listing (D218): where and in what the class is bought
    ('exchange', 'Exchange', 'Exchange'),
    # the listing's own identifiers (D220): the CUSIP and the SEDOL
    ('cusip', 'CUSIP', 'CUSIP'),
    ('sedol', 'SEDOL', 'SEDOL'),
    ('issueCurrency', 'IssueCurrency', 'Issue currency'),
    # an SMA listing's code (D197, D207)
    ('code', 'MandateCode', 'Mandate code'),
    # and the name of the mandate: an SMA's mandates are its strategy's
    # variants, each with a name of its own, or blank for the strategy's (D222)
    ('mandateName', 'MandateName', 'Mandate name'),
]
KEYS = [k for k, _, _ in _FIELDS]
#: the file's columns in the order an export writes them
FILE_COLUMNS = [c for _, c, _ in _FIELDS]
LABELS = {k: label for k, _, label in _FIELDS}

#: What belongs to the SHARE CLASS (D218), the same on every listing of one
#: ISIN: what the class is, its figures and its benchmark.
CLASS_FIELDS = ('isin', 'shareClass', 'income', 'hedging', 'classCurrency', 'exposureCurrency',
                'domicile', 'ucits', 'replication', 'ukReporting', 'productCost',
                'distributionYield', 'minimumInvestment', 'benchmark')
#: what belongs to the LISTING alone: where it is bought, what identifies
#: it there, and in what currency (D220)
PLACE_FIELDS = ('exchange', 'cusip', 'sedol', 'issueCurrency')
#: Every field a listing carries (D207, D218): its class's values and its
#: own, and an SMA's mandate code. A listing holds all of them whole:
#: nothing in one is read from another.
LISTING_FIELDS = CLASS_FIELDS + PLACE_FIELDS + ('code', 'mandateName', 'isg')
#: the ones of those that are figures
LISTING_FIGURES = ('productCost', 'distributionYield', 'minimumInvestment')
#: and the ones that are numbers: the figures and the hedging (D224)
LISTING_NUMBERS = LISTING_FIGURES + ('hedging',)
#: what the product is, whichever listing is held: the same on every one of
#: its rows in a file
PRODUCT_FIELDS = [k for k in KEYS if k not in LISTING_FIELDS]
#: what a listing is told apart by, where a file names it: the fields an
#: import matches a file's row to the catalogue's listing on
LISTING_KEY = ('isin', 'code', 'exchange', 'cusip', 'sedol', 'issueCurrency', 'hedging', 'isg', 'shareClass',
               'income')
#: the class's and the listing's fields a separate account has none of
SMA_REFUSED = ('isin', 'shareClass', 'classCurrency', 'domicile', 'ucits', 'replication',
               'ukReporting', 'exchange', 'cusip', 'sedol')
#: the fields a listing carried before the three levels (D207), read once
#: to list a product stored or recorded then
_OLD_LISTING_FIELDS = ('ticker', 'code', 'issueCurrency', 'hedging', 'shareClass', 'exposureCurrency',
                       'assetClass', 'productCost', 'distributionYield', 'minimumInvestment', 'benchmark')
#: what a listing kept before the catalogue held facts only (D214): its
#: place in each base currency's ranking, dropped wherever it is read
RANKS = 'ranks'
#: what a stored listing may carry that the catalogue no longer keeps: the
#: rankings (D214) and the quotation unit (D219)
DROPPED = (RANKS, 'quotationUnit')
LABELS.update({'listings': 'Listings', 'order': 'Listing order'})
#: The layout before listings (D182): the same ProductId on one row per base
#: currency, named in a BaseCurrency column - USD, GBP, CHF, EUR, or All (or
#: blank) for the main row. Still read, each currency's row a listing.
BASE_COLUMN = 'BaseCurrency'
#: what a file may head that column with, lower-cased and stripped of all but
#: letters and digits
_BASE_HEADERS = ('basecurrency', 'baseccy')
ALL = 'All'
#: what an export writes: the file's columns
EXPORT_COLUMNS = list(FILE_COLUMNS)
_COLUMN_OF = {k: c for k, c, _ in _FIELDS}
#: the six words a product is described by: free values but the vehicle,
#: which the console keeps consistent by offering the ones already in use
WORDS = ['assetClass', 'style', 'vehicle', 'source', 'liquidity', 'exposureCurrency']
#: the figures, all percentages but the minimum, which is currency units
_PERCENT = ('productCost', 'distributionYield')

#: The three vehicles (D218): a product is one of these, and the vehicle
#: picks its card. A file may write SMA as Separate Account, as Poros does.
VEHICLES = ('ETF', 'Mutual Fund', 'SMA')
_VEHICLE_WORDS = {'etf': 'ETF', 'mutual fund': 'Mutual Fund', 'mutualfund': 'Mutual Fund',
                  'sma': 'SMA', 'separate account': 'SMA'}
#: The vehicles a file wrote before the levels (UCITS, ICAV): read as a fund
#: - an ETF where the name says ETF, else a Mutual Fund - a UCITS one's
#: classes UCITS compliant (D220); an ICAV may be either, so says nothing.
REGULATED_VEHICLES = ('UCITS', 'ICAV')
#: What marks a product as a separate account, an SMA (D197): its vehicle,
#: which reads Separate Account on Poros and SMA in the sample file here,
#: compared without regard to case.
SMA_VEHICLES = ('separate account', 'sma')
#: the sleeve category a proposal's Public Equity SMAs are in
EQUITY_CATEGORY = 'Public Equity'
#: a product whose own category is this is a hedge fund (D198)
HEDGE_FUND_CATEGORY = 'Hedge Funds'
#: the hedging versions a Public Equity SMA's listing came in before the
#: hedging was a percentage (D197, D224), in their order
MANDATE_VERSIONS = ('Hedged', 'ISG Hedged', 'Unhedged')
#: The hedging is the proportion hedged, a percentage from 0 to 100 (D224):
#: a fully hedged class is hedged to its class currency, which is then its
#: exposure. The words it was before read as the percentage; ISG Hedged is
#: also ticked ISG.
FULLY_HEDGED = 100.0
_HEDGE_WORDS = {'hedged': 100.0, 'fully hedged': 100.0, 'unhedged': 0.0, 'not hedged': 0.0,
                'isg hedged': 50.0}
ISG_WORD = 'isg hedged'
ISG = 'Yes'
_ISG_WORDS = {'yes': ISG, 'y': ISG, 'true': ISG, '1': ISG, 'x': ISG, 'isg': ISG, 'isg hedged': ISG,
              'no': '', 'n': '', 'false': '', '0': ''}
#: how an ETF's class tracks its index, or blank for not said
REPLICATIONS = ('Physical', 'Sampled', 'Synthetic')
#: whether a class has UK reporting status, or blank for not said
UK_REPORTING = ('Yes', 'No')
_UK_WORDS = {'yes': 'Yes', 'y': 'Yes', 'true': 'Yes', '1': 'Yes', 'no': 'No', 'n': 'No', 'false': 'No',
             '0': 'No'}
#: whether a class is UCITS compliant (D220): the same Yes, No or blank
UCITS = UK_REPORTING
#: what a class's regulation said before (D218) and the UCITS compliance it
#: reads as; any other word says nothing
_REGULATION_UCITS = {'ucits': 'Yes', 'aif': 'No', 'non-ucits': 'No', 'non ucits': 'No', 'nurs': 'No',
                     'qiaif': 'No', 'riaif': 'No'}
CODE_MAX = 40
#: what a product stored before listings kept by base currency, figures and
#: words (D180, D182, D184, D189, D190, D194, D204), read once to list it
_LEGACY_FIGURES = ('minimumInvestment', 'distributionYield', 'productCost')
_LEGACY_WORDS = ('exposureCurrency', 'issueCurrency', 'shareClass', 'assetClass', 'benchmark')

ID_MAX = 80
NAME_MAX = 120
#: the shapes of a CUSIP (eight letters or digits and a check digit) and of
#: a SEDOL (six letters, no vowels, or digits and a check digit) (D220)
CUSIP_RE = re.compile(r'^[0-9A-Z*@#]{8}[0-9]$')
SEDOL_RE = re.compile(r'^[0-9BCDFGHJKLMNPQRSTVWXYZ]{6}[0-9]$')
WORD_MAX = 60
#: a share class letter, as the fund names it: A, I, R, IX…
SHARE_CLASS_MAX = 20
EXCHANGE_MAX = 40
#: a benchmark is an index's whole name, so it may run longer than a word
BENCHMARK_MAX = 200
NOTE_MAX = 240
#: a figure at or above these is one typed in the wrong unit - 45 for 0.45
PERCENT_MAX = {'productCost': 10.0, 'distributionYield': 50.0}
#: the shape of an ISIN: two letters, nine letters or digits, a check digit
ISIN_RE = re.compile(r'^[A-Z]{2}[A-Z0-9]{9}[0-9]$')
#: the country codes a domicile may be (ISO 3166-1 alpha-2)
COUNTRY_CODES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV
BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES
ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE
IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY
MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU
NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM
SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE
VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())

#: what a revision's action can say
ACTIONS = ['seeded', 'created', 'updated', 'imported', 'withdrawn', 'restored', 'reverted', 'upgraded']


class BadCatalogue(ValueError):
    """The catalogue cannot be read - the file it is loaded from, or the
    first load itself, failed."""


class StaleError(ValidationError):
    """The product (or the catalogue) a write was made against is no longer
    the version in force: someone saved since -> HTTP 409."""


def sourcePath() -> str:
    """The file the catalogue is first loaded from: SCENARIO_PRODUCTS_SOURCE,
    else the packaged stand-in."""
    return os.path.abspath(os.getenv('SCENARIO_PRODUCTS_SOURCE', '') or _DEFAULT)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


# ------------------------------------------------------------ the extract ---

def _readRows(path: str, data: bytes = None, sheet: str = 'products'):
    """(header, rows...) from a CSV or an XLSX - a path, or the bytes of an
    upload named *path*. A workbook is read from its *sheet* when it has one
    (the data template keeps the products on 'products'), else its first."""
    if path.lower().endswith('.csv'):
        if data is None:
            # utf-8-sig drops a BOM from an Excel export; unwrapRows takes off
            # a pair of quotes wrapped around a whole row (csvText.py)
            with open(path, newline='', encoding='utf-8-sig') as fh:
                text = fh.read()
        else:
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                # a CSV saved from Excel on Windows is often its code page
                text = data.decode('cp1252')
        reader = csv.reader(unwrapRows(io.StringIO(text, newline='')))
        head = next(reader, None) or []
        yield [str(h).strip() for h in head]
        for row in reader:
            if row and str(row[0]).strip():
                yield row
        return
    from openpyxl import load_workbook
    book = load_workbook(io.BytesIO(data) if data is not None else path, read_only=True,
                         data_only=True)
    named = [s for s in book.sheetnames if s.strip().lower() == sheet]
    sheet = book[named[0]] if named else book.active
    rows = (tuple(_cellValue(c) for c in row) for row in sheet.iter_rows())
    head = next(rows, None) or []
    yield [str(h).strip() if h is not None else '' for h in head]
    for row in rows:
        if row and row[0] is not None and str(row[0]).strip():
            yield ['' if v is None else v for v in row]


def _cellValue(cell):
    """A spreadsheet cell's value; one shown as a percentage, which Excel
    holds as the fraction - 50% as 0.5 - is read as it is shown (D224)."""
    value = getattr(cell, 'value', None)
    if isinstance(value, (int, float)) and not isinstance(value, bool) \
            and _percentFormat(getattr(cell, 'number_format', '') or ''):
        return '{:g}%'.format(round(value * 100, 9))
    return value


def _percentFormat(fmt) -> bool:
    """Whether a number format shows a percentage: a % outside quotes."""
    return '%' in re.sub(r'"[^"]*"|\\.', '', fmt)


def _norm(column) -> str:
    """A column's heading lower-cased and stripped of all but letters and digits."""
    return ''.join(ch for ch in str(column).lower() if ch.isalnum())


def _load(path: str, feeGroups=None, strict=False, data: bytes = None, categories=None,
          found=None, moved=None):
    """The extract at *path* (or the upload *data* named *path*) as
    ({id: record}, [ids in file order]). Refuses the whole file with
    BadCatalogue at the first bad row, naming the row and the product: a
    file is loaded whole or not at all. *feeGroups*, when given, is the set
    a product's group must belong to, and *categories* the list its category
    must be one of. *strict* is the check a save gets
    (``clean``); without it, what the tool has always accepted from a file.
    *found*, when given, is a set the optional columns the file has are
    added to - 'Rank' for rank columns, which are not read (D214),
    BaseCurrency for the layout before listings. *moved*, when given, is a
    dict told what the file's older layout was read as (D218): how many
    tickers shaped like an ISIN were read as the ISIN, and the products
    whose UCITS or ICAV vehicle was read as a fund's."""
    rows = _readRows(path, data)
    head = next(rows)
    name = os.path.basename(path)
    index = {}
    for c in COLUMNS + OPTIONAL + OLD_COLUMNS:
        if c in head:
            index[c] = head.index(c)
    for c in CATEGORY_HEADERS:
        if c in head:
            index['Category'] = head.index(c)
            break
    ranked = False
    for at, column in enumerate(head):
        key = _norm(column)
        for c, headings in _HEADINGS.items():
            if key in headings and c not in index and at not in index.values():
                index[c] = at
        if any(key in ('rank' + c.lower(), c.lower() + 'rank') for c in CURRENCIES):
            ranked = True
    missing = [c for c in COLUMNS if c not in index]
    if missing:
        raise BadCatalogue('{}: missing column(s) {}'.format(name, ', '.join(missing)))
    baseAt = next((at for at, column in enumerate(head) if _norm(column) in _BASE_HEADERS), None)
    rows = list(rows)
    if 'ShareClass' in index and 'Income' not in index:
        # a file from before the levels (D218): a ShareClass column that says
        # only Accumulating or Distributing is the Income column
        at = index['ShareClass']
        said = {_text(row[at] if at < len(row) else '') for row in rows}
        if said - {''} and not any(v and not _incomeWord(v) for v in said):
            index['Income'] = index.pop('ShareClass')
    if found is not None:
        found.update(c for c in OPTIONAL + OLD_COLUMNS if c in index)
        if ranked:
            found.add('Rank')
        if baseAt is not None:
            found.add(BASE_COLUMN)
    isinsFromTickers, tickersDropped, vehiclesMoved = 0, 0, []
    rowsOf, order = {}, []
    for n, row in enumerate(rows, start=2):
        fields = {}
        for key, column in _COLUMN_OF.items():
            if column not in index:
                fields[key] = None
                continue
            at = index[column]
            fields[key] = row[at] if at < len(row) else ''
        old = {c: (row[index[c]] if index[c] < len(row) else '') for c in OLD_COLUMNS if c in index}
        pid = _text(fields['productId'], keepSpaces=True)
        ticker = _text(old.get('Ticker'))
        if ticker in (NO_TICKER, '-', '–'):
            ticker = ''
        if _isinShaped(ticker) and not _text(fields.get('isin')):
            # a file from before the ISIN had a column of its own (D218)
            fields['isin'] = ticker.upper()
            isinsFromTickers += 1
        elif ticker:
            # a ticker is no longer kept (D220): a listing is its exchange,
            # its CUSIP and SEDOL, and its issue currency
            tickersDropped += 1
        if 'UCITSCompliant' not in index and _text(old.get('Regulation')):
            fields['ucits'] = _ucitsOf(old['Regulation'])
        vehicle = _text(fields.get('vehicle'))
        if vehicle.upper() in REGULATED_VEHICLES and pid not in {m['productId'] for m in vehiclesMoved}:
            vehiclesMoved.append({'productId': pid, 'name': _text(fields.get('name')), 'was': vehicle,
                                  'now': _fundVehicle(fields.get('name')), 'ucits': _ucitsOf(vehicle)})
        currency = ALL
        if baseAt is not None:
            given = _text(row[baseAt] if baseAt < len(row) else '')
            currency = ALL if given.lower() in ('', 'all') else given.upper()
            if currency not in CURRENCIES + (ALL,):
                raise BadCatalogue('{}: row {} ({}): the {} {!r} is not one of {} or All.'.format(
                    name, n, pid or 'no ProductId', BASE_COLUMN, given, ', '.join(CURRENCIES)))
            same = [m for m, c, _ in rowsOf.get(pid, []) if c == currency]
            if same:
                raise BadCatalogue('{}: row {}: {} already has a row for {}, row {}.'.format(
                    name, n, pid, 'All' if currency == ALL else currency, same[0]))
            if currency != ALL:
                # 'Green Bond ETF (GBP)' on the GBP row is the product's name
                fields['name'] = _withoutCurrency(fields['name'], currency)
            if pid in rowsOf:
                # a currency's row says what the product is only where it differs
                first = rowsOf[pid][0][2]
                for key in PRODUCT_FIELDS:
                    if key != 'productId' and not _text(fields.get(key)):
                        fields[key] = first.get(key)
        try:
            record = clean(fields, feeGroups, strict=strict, categories=categories)
        except ValidationError as exc:
            raise BadCatalogue('{}: row {} ({}): {}'.format(name, n, pid or 'no ProductId',
                                                            exc.message))
        if pid not in rowsOf:
            order.append(pid)
        rowsOf.setdefault(pid, []).append((n, currency, record))
    if moved is not None:
        moved.update(isinsFromTickers=isinsFromTickers, tickersDropped=tickersDropped,
                     vehicles=vehiclesMoved)
    byId = {pid: _assemble(name, pid, rowsOf[pid], baseAt is not None, feeGroups, strict,
                           categories, 'ClassCurrency' in index) for pid in order}
    _refuseShared(name, byId, order)
    return byId, order


def _withoutCurrency(value, currency):
    """A name with its row's currency taken off the end: '(GBP)', ' - GBP'."""
    text = _text(value)
    for pattern in (r'\s*\(\s*{}\s*\)$', r'\s*[-–—]\s*{}$', r'\s+{}$'):
        cut = re.sub(pattern.format(currency), '', text, flags=re.IGNORECASE)
        if cut != text and cut.strip():
            return cut
    return text


def _assemble(fileName, pid, rows, legacy, feeGroups, strict, categories, classCurrencyGiven=True):
    """One product from its rows in a file (D207, D218): each row a listing,
    in the file's order, the first the one a proposal holds (D214). What the
    product is must read the same on every row; only a share class's and a
    listing's values may differ, and the rows of one ISIN must agree on the
    class's. A file with no ClassCurrency column gives each class the issue
    currency of its first row, and a fully hedged class's exposure follows it. A
    file in the layout before listings (*legacy*, D182) gives a row per base
    currency instead: its All row - else the row of the first currency - is
    the first listing, and each currency's row a listing after it, two rows
    alike being one listing."""
    if legacy:
        rows = sorted(rows, key=lambda r: -1 if r[1] == ALL else CURRENCIES.index(r[1]))
    mainRow, _, main = rows[0]
    for n, currency, record in rows[1:]:
        for key in PRODUCT_FIELDS:
            if key == 'productId' or _same(record[key], main[key]):
                continue
            raise BadCatalogue(
                '{}: row {} ({}): it gives {} {} where row {} gives {} - a product\'s rows are its '
                'listings, and only a share class\'s values (its ISIN, class, income, hedging, '
                'currencies, domicile, UCITS compliance, replication, UK reporting, cost, yield, '
                'minimum and benchmark) and a listing\'s (its exchange, CUSIP, SEDOL, issue currency '
                'and mandate code) may differ between them.'.format(
                    fileName, n, pid, LABELS[key].lower(), show(key, record[key]), mainRow,
                    show(key, main[key])))
    listings, rowOf = [], []
    for n, currency, record in rows:
        listing = dict(record['listings'][0])
        if legacy:
            # a currency's row read as it always was: a word it leaves blank
            # is the main row's
            for word in ('exposureCurrency', 'issueCurrency', 'benchmark', 'income', 'shareClass',
                         'classCurrency', 'domicile', 'ucits', 'replication', 'ukReporting'):
                if not listing[word] and listings:
                    listing[word] = listings[0][word]
            if any(_sameListing(held, listing) for held in listings):
                continue
            if not listing['issueCurrency'] and currency != ALL and any(
                    _sameIdentity(held, listing) for held in listings):
                listing['issueCurrency'] = currency
        listings.append(listing)
        rowOf.append(n)
    if not classCurrencyGiven and not isSma(main):
        _fillClassCurrency(listings)
    try:
        return clean(dict(main, listings=listings), feeGroups, strict=strict, categories=categories)
    except ValidationError as exc:
        at = re.match(r'listing(\d+)', exc.field or '')
        row = rowOf[int(at.group(1))] if at and int(at.group(1)) < len(rowOf) else mainRow
        message = re.sub(r'\blisting (\d+)\b', lambda m: 'row {}'.format(
            rowOf[int(m.group(1)) - 1] if 0 < int(m.group(1)) <= len(rowOf) else m.group(1)), exc.message)
        raise BadCatalogue('{}: row {} ({}): {}'.format(fileName, row, pid, message))


def _fillClassCurrency(listings) -> None:
    """A file with no ClassCurrency column (D218): each class is given the
    issue currency of its first row, and a fully hedged class's exposure follows
    it, since hedged means hedged to the class currency."""
    firstOf = {}
    for listing in listings:
        key = ('isin', _text(listing.get('isin')).upper()) if _text(listing.get('isin')) else \
            tuple(_text(listing.get(f)).lower() if f != 'classCurrency' else '' for f in CLASS_FIELDS)
        first = firstOf.setdefault(key, listing)
        if listing.get('classCurrency'):
            continue
        listing['classCurrency'] = (first.get('classCurrency') or _text(first.get('issueCurrency'))).upper()
        if listing['classCurrency'] and fullyHedged(listing):
            listing['exposureCurrency'] = listing['classCurrency']


def _refuseShared(fileName, byId, order) -> None:
    """An ISIN, a CUSIP, a SEDOL or a mandate code on two products of one
    file is refused, naming both: each is one product's (D207, D220)."""
    seen = {}
    for pid in order:
        for kind, value in _identifiers(byId[pid]):
            other = seen.get((kind, value.lower()))
            if other is not None and other != pid:
                raise BadCatalogue('{}: the {} {} is on {} and on {}. {} is one product\'s.'.format(
                    fileName, _kindWords(kind)[0], value, other, pid, _kindWords(kind)[1]))
            seen[(kind, value.lower())] = pid


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= 1e-12
    return a == b or (a in (None, '') and b in (None, ''))


def readFile(path: str, strict=True):
    """The products in a file, checked as an import checks them against the
    fee card in force and the library's categories. ({id: record}, [ids in
    file order])."""
    from . import fees
    return _load(path, set(fees.feeGroups()), strict=strict, categories=knownCategories())


# ------------------------------------------------------------ the checks ---

def _text(value, keepSpaces=False) -> str:
    """A cell or a typed value as text: None is empty, a whole number from a
    spreadsheet loses its '.0', and the spacing is one space between words."""
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value)
    return text.strip() if keepSpaces else ' '.join(text.split())


def _figure(value, key, required=False, strict=True):
    """A number a product carries, or None for a blank one. A percentage may
    be typed with its sign and a large amount with its commas. *strict*
    also refuses a percentage typed in the wrong unit."""
    label = LABELS[key]
    example = ('a percentage, such as 0.25 for 0.25%' if key == 'productCost'
               else 'a percentage, such as 3.2' if key == 'distributionYield'
               else 'an amount, such as 5000000')
    if isinstance(value, bool):
        raise ValidationError(key, '{} must be a number: {}.'.format(label, example))
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValidationError(key, '{} is needed: {}.'.format(label, example))
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        shown = value
    else:
        shown = str(value).strip()
        text = shown.replace(',', '')
        if key in _PERCENT and text.endswith('%'):
            text = text[:-1].strip()
        try:
            number = float(text)
        except ValueError:
            raise ValidationError(key, '{} {!r} is not a number: give {}.'.format(
                label, shown, example))
    if math.isnan(number) or math.isinf(number):
        raise ValidationError(key, '{} {!r} is not a number: give {}.'.format(label, shown, example))
    if number < 0:
        raise ValidationError(key, '{} cannot be negative.'.format(label))
    if strict and key in _PERCENT and number >= PERCENT_MAX[key]:
        raise ValidationError(key, '{} of {:g}% looks wrong: it is a percentage, so 0.25 means '
                                   '0.25%.'.format(label, number))
    return number


def knownCategories() -> list:
    """The categories a product's own may be: the ones the library's sleeves
    are built for, in the implementation table's order (D178)."""
    from . import sleeveRepo
    return sleeveRepo.categories()


def clean(fields, feeGroups=None, strict=True, categories=None) -> dict:
    """A product as the catalogue stores it, from what was typed or read, or
    a ValidationError naming the first field that is wrong. *feeGroups*, when
    given, is the set the fee group must belong to, and *categories* the list
    a category must be one of. Its listings come as a list (D207); a product
    given its values at the top level alone has one listing, and one given
    them by base currency, as products were stored before listings, is
    listed as it reads (``legacyListings``). A CUSIP or a SEDOL is '' when
    there is none, and so is a category, and an income None."""
    fields = fields or {}
    pid = _text(fields.get('productId'), keepSpaces=True)
    if not pid:
        raise ValidationError('productId', 'Give the product an ID. Sleeves refer to products by '
                                           'it, and it cannot be changed later.')
    if strict and len(pid) > ID_MAX:
        raise ValidationError('productId', 'Keep the product ID to {} characters.'.format(ID_MAX))
    if any(ord(ch) < 32 for ch in pid):
        raise ValidationError('productId', 'A product ID cannot hold tabs or line breaks.')
    name = _text(fields.get('name'))
    if not name:
        raise ValidationError('name', 'Give the product its strategy\'s name.')
    if strict and len(name) > NAME_MAX:
        raise ValidationError('name', 'Keep the strategy to {} characters.'.format(NAME_MAX))
    record = {'productId': pid, 'name': name}
    # who runs it (D218): optional, a word
    record['provider'] = _word(fields.get('provider'), 'provider', LABELS['provider'], strict,
                               required=False)
    record['assetClass'] = _word(fields.get('assetClass'), 'assetClass', LABELS['assetClass'], strict)
    for key in ('style', 'source', 'liquidity'):
        record[key] = _word(fields.get(key), key, LABELS[key], strict)
    record['vehicle'], ucits = _vehicle(fields.get('vehicle'), name, strict)
    # the sleeve category the catalogue bands the product under (D178): blank
    # for none, else one the library knows, spelled as it spells it
    category = _text(fields.get('category'))
    if category and categories is not None and category not in categories:
        same = [c for c in categories if c.lower() == category.lower()]
        if same:
            raise ValidationError('category', 'The category {!r} is spelled {}.'.format(
                category, same[0]))
        raise ValidationError('category', 'The category {!r} is not one the sleeves are built '
                                          'for: {}, or blank for none.'.format(
                                              category, ', '.join(categories)))
    record['category'] = category
    group = _text(fields.get('feeGroup'))
    if not group:
        raise ValidationError('feeGroup', 'Choose a fee group: it is how the fee card prices '
                                          'the product.')
    if feeGroups is not None and group not in feeGroups:
        same = [g for g in feeGroups if g.lower() == group.lower()]
        if same:
            raise ValidationError('feeGroup', 'The fee group {!r} is not on the fee card: it is '
                                              'spelled {}.'.format(group, same[0]))
        raise ValidationError('feeGroup', 'The fee group {!r} is not on the fee card, which '
                                          'prices {}.'.format(group, ', '.join(sorted(feeGroups))))
    record['feeGroup'] = group
    given, bare = _listingsGiven(fields)
    record['listings'] = _listings(given, record, strict, bare, ucits)
    return _mirrored(record)


def _vehicle(value, name, strict):
    """A product's vehicle as stored (D218): ETF, Mutual Fund or SMA, however
    a file spells it (Separate Account is an SMA), with what a UCITS or ICAV
    vehicle says of its classes' UCITS compliance (D220) - (vehicle, ucits)."""
    word = _text(value)
    if not word:
        if strict:
            raise ValidationError('vehicle', 'Vehicle is needed: ETF, Mutual Fund or SMA.')
        return '', ''
    if word.lower() in _VEHICLE_WORDS:
        return _VEHICLE_WORDS[word.lower()], ''
    if word.upper() in REGULATED_VEHICLES:
        return _fundVehicle(name), _ucitsOf(word)
    raise ValidationError('vehicle', 'The vehicle {!r} is not one of ETF, Mutual Fund or SMA. Separate '
                                     'Account is read as SMA, and UCITS or ICAV as a fund.'.format(word))


def _ucitsOf(regulation) -> str:
    """The UCITS compliance a class's regulation, or a vehicle, said before
    (D220): Yes for UCITS, No for AIF and the like, '' for anything else."""
    return _REGULATION_UCITS.get(_text(regulation).lower(), '')


def _fundVehicle(name) -> str:
    """The vehicle a UCITS or ICAV product is (D218): an ETF where its name
    says so, else a Mutual Fund."""
    return 'ETF' if re.search(r'\bETFs?\b', _text(name), flags=re.IGNORECASE) else 'Mutual Fund'


def _mirrored(record) -> dict:
    """*record* in the catalogue's order, carrying its main listing's values
    at the top level, where every reader of a product has always found them."""
    main = record['listings'][0]
    out = {key: main[key] if key in LISTING_FIELDS else record[key] for key in KEYS}
    out['listings'] = record['listings']
    return out


def _word(value, key, label, strict, required=True) -> str:
    """One of the words a product or a listing is described by: needed on a
    save or an import, and kept to a word's length."""
    word = _text(value)
    if strict and required and not word:
        raise ValidationError(key, '{} is needed.'.format(label))
    if strict and len(word) > WORD_MAX:
        raise ValidationError(key, 'Keep the {} to {} characters.'.format(_lower(label), WORD_MAX))
    return word


def _lower(label) -> str:
    # an initialism keeps its capitals: ISIN, CUSIP, SEDOL, UCITS compliant
    return label if label[:2].isupper() else label[0].lower() + label[1:]


def _listingsGiven(fields):
    """The listings *fields* give, and whether they came bare - at the top
    level, so a refusal names the field as the form before listings did."""
    value = fields.get('listings')
    if value is not None and value != '':
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                raise ValidationError('listings', 'The listings could not be read.')
        return value, False
    if fields.get('currencies') or fields.get('mandates'):
        return legacyListings(fields), False
    return [{f: fields.get(f) for f in LISTING_FIELDS}], True


def _listings(value, record, strict=True, bare=False, ucits='') -> list:
    """The listings as stored (D207, D218): [{the share class's values, the
    listing's own, and an SMA's code}], in the order given, each whole and
    checked as a product's values always were, and the list against
    *record*: only an SMA's listing has a mandate code, and an SMA has none
    of the class's or the listing's other fields; only a Mutual Fund's
    class has a letter; the hedging is a percentage from 0 to 100 (D224),
    and only an SMA's mandate may be ticked ISG; a fully hedged class's
    exposure is its class currency; the rows of one ISIN
    agree on the class's values; and no two listings are the same one.
    *ucits* is what a UCITS or ICAV vehicle gave a class that says
    nothing of it. Ranks given with a listing are not kept (D214). A refusal
    names the listing's box - listing0sedol, listing2hedging - or, for a product
    given its values at the top level (*bare*), the field."""
    if not isinstance(value, list) or not value or any(not isinstance(l, dict) for l in value):
        raise ValidationError('listings', 'A product has at least one listing: its share class, its '
                                          'cost and the rest.')
    sma = isSma(record)
    vehicle = record.get('vehicle') or ''
    name = record.get('name') or record.get('productId') or 'the product'
    out, seen = [], {}
    for n, given in enumerate(value):
        def box(field, n=n):
            return field if bare else 'listing{}{}'.format(n, field)
        named = (_text(given.get('code')) or _text(given.get('isin')).upper()
                 or _text(given.get('sedol')).upper() or _text(given.get('cusip')).upper())
        where = '' if bare else ' of listing {}{}'.format(n + 1, ' ({})'.format(named) if named else '')
        listing = {'isin': _isin(given.get('isin'), box('isin'), strict, where)}
        code = _text(given.get('code'))
        if code and not sma:
            raise ValidationError(box('code'), 'Only a separate account (an SMA) has a mandate code: '
                                  '{} is {}. Take the code off{}, or make the vehicle SMA.'.format(
                                      name, _an(record.get('vehicle')), where))
        if strict and len(code) > CODE_MAX:
            raise ValidationError(box('code'), 'Keep the mandate code{} to {} characters.'.format(
                where, CODE_MAX))
        listing['code'] = code
        mandateName = _text(given.get('mandateName'))
        if mandateName and not sma:
            raise ValidationError(box('mandateName'), 'Only a separate account (an SMA) has mandates with '
                                  'names of their own: {} is {}. Take the mandate name off{}, or make the '
                                  'vehicle SMA.'.format(name, _an(record.get('vehicle')), where))
        if strict and len(mandateName) > NAME_MAX:
            raise ValidationError(box('mandateName'), 'Keep the mandate name{} to {} characters.'.format(
                where, NAME_MAX))
        listing['mandateName'] = mandateName
        letter, income = _text(given.get('shareClass')), _text(given.get('income'))
        if letter and _incomeWord(letter) and (not income or _incomeWord(income)
                                               and INCOME_SHORT.get(income, income) == INCOME_SHORT.get(letter, letter)):
            # Accumulating or Distributing where the class letter goes: the
            # income, as the field was called before the two were apart
            income, letter = letter, ''
        if strict and len(letter) > SHARE_CLASS_MAX:
            raise ValidationError(box('shareClass'), 'Keep the share class{} to {} characters: the letter '
                                  'or letters the fund names it by, such as A or I.'.format(where, SHARE_CLASS_MAX))
        if letter and vehicle != 'Mutual Fund':
            raise ValidationError(box('shareClass'), 'Only a Mutual Fund has a share class letter: {} is '
                                  '{}. Take the share class{} off, or make the vehicle Mutual '
                                  'Fund.'.format(name, _an(vehicle), where))
        listing['shareClass'] = letter
        listing['income'] = _income(income, box('income'), where) or None
        listing['hedging'], listing['isg'] = _hedging(given.get('hedging'), given.get('isg'), record, sma,
                                                      box('hedging'), box('isg'), where)
        listing['classCurrency'] = _currencyCode(given.get('classCurrency'), box('classCurrency'), strict, where)
        exposure = _word(given.get('exposureCurrency'), box('exposureCurrency'),
                         LABELS['exposureCurrency'] + where, strict, required=False)
        if not sma and listing['hedging'] == FULLY_HEDGED and listing['classCurrency']:
            # hedged to the class currency means that currency's exposure
            if not exposure:
                exposure = listing['classCurrency']
            elif exposure.upper() != listing['classCurrency']:
                if strict:
                    raise ValidationError(box('exposureCurrency'), 'A class 100% hedged to {} gives {} '
                                          'exposure, not {}: leave the exposure currency{} blank to fill it '
                                          'in, or give the class a lower hedging.'.format(listing['classCurrency'],
                                                                            listing['classCurrency'],
                                                                            exposure, where))
                exposure = listing['classCurrency']
        if strict and not exposure:
            raise ValidationError(box('exposureCurrency'), '{}{} is needed.'.format(
                LABELS['exposureCurrency'], where))
        listing['exposureCurrency'] = exposure
        listing['domicile'] = _domicile(given.get('domicile'), box('domicile'), strict, where)
        listing['ucits'] = _yesNo(given.get('ucits'), box('ucits'), 'ucits', where) or ('' if sma else ucits)
        listing['replication'] = _replication(given.get('replication'), box('replication'), where)
        listing['ukReporting'] = _yesNo(given.get('ukReporting'), box('ukReporting'), 'ukReporting', where)
        for figure in LISTING_FIGURES:
            try:
                listing[figure] = _figure(given.get(figure), figure, required=figure == 'productCost',
                                          strict=strict)
            except ValidationError as exc:
                raise ValidationError(box(figure), exc.message.replace(
                    LABELS[figure], LABELS[figure] + where, 1))
        benchmark = _text(given.get('benchmark'))
        if strict and len(benchmark) > BENCHMARK_MAX:
            raise ValidationError(box('benchmark'), 'Keep the benchmark{} to {} characters.'.format(
                where, BENCHMARK_MAX))
        listing['benchmark'] = benchmark
        exchange = _text(given.get('exchange'))
        if strict and len(exchange) > EXCHANGE_MAX:
            raise ValidationError(box('exchange'), 'Keep the exchange{} to {} characters, as you name '
                                  'it: LSE, Xetra.'.format(where, EXCHANGE_MAX))
        listing['exchange'] = exchange
        listing['cusip'] = _identifier(given.get('cusip'), box('cusip'), 'cusip', strict, where)
        listing['sedol'] = _identifier(given.get('sedol'), box('sedol'), 'sedol', strict, where)
        listing['issueCurrency'] = _word(given.get('issueCurrency'), box('issueCurrency'),
                                         LABELS['issueCurrency'] + where, strict, required=False)
        if sma:
            wrong = next((f for f in SMA_REFUSED if listing[f]), None)
            if wrong is not None:
                raise ValidationError(box(wrong), 'A separate account has no {}: {} is an SMA, listed under '
                                      'its mandate codes. Take it off{}.'.format(_lower(LABELS[wrong]), name,
                                                                                  where))
        key = _listingKey(listing, sma)
        if key in seen:
            first = seen[key]
            if sma:
                raise ValidationError(box('code'), 'Listings {} and {} of {} are the '
                                      'same listing: {}. Give one its own issue currency, '
                                      'hedging or ISG tick.'.format(first + 1, n + 1, name, showListing(listing, ident=True)))
            raise ValidationError(box('exchange'), 'Listings {} and {} of {} are the same listing: {}. A '
                                  'share class is listed once on an exchange in a currency, so give one '
                                  'its own exchange or issue currency{}.'.format(
                                      first + 1, n + 1, name, showListing(listing, ident=True),
                                      '' if listing['isin'] else ', or its own share class values'))
        seen[key] = n
        out.append(listing)
    _refuseDisagreeing(out, bare)
    return out


def _refuseDisagreeing(listings, bare) -> None:
    """The rows of one ISIN are one share class (D218): where two give the
    class a different value, the later is refused, naming both."""
    places = {}
    for n, listing in enumerate(listings):
        if listing.get('isin'):
            places.setdefault(listing['isin'], []).append(n)
    for isin, at in places.items():
        first = listings[at[0]]
        for n in at[1:]:
            for field in CLASS_FIELDS:
                if field == 'isin' or _sameValue(first[field], listings[n][field]):
                    continue
                raise ValidationError(field if bare else 'listing{}{}'.format(n, field),
                                      '{} has {} {} on listing {} and {} on listing {}: one ISIN is one '
                                      'share class, so its rows give the same {}.'.format(
                                          isin, _lower(LABELS[field]), show(field, first[field]), at[0] + 1,
                                          show(field, listings[n][field]), n + 1, _lower(LABELS[field])))


def _listingKey(listing, sma=None) -> tuple:
    """What makes a listing the one it is, among its product's (D207, D218):
    an SMA's its mandate code, currency, hedging and ISG tick; a fund's its
    share class - the ISIN, or every value of a class with none - and the
    exchange and issue currency it is bought on."""
    if sma is None:
        sma = bool(_text(listing.get('code')))
    if sma:
        return ('sma', _text(listing.get('code')).lower(), _text(listing.get('issueCurrency')).upper(),
                readHedging(listing.get('hedging'), listing.get('isg')))
    isin = _text(listing.get('isin')).upper()
    if isin:
        cls = ('isin', isin)
    else:
        cls = tuple(_keyValue(f, listing.get(f)) for f in CLASS_FIELDS)
    return (cls, _text(listing.get('exchange')).lower(), _text(listing.get('issueCurrency')).upper())


def _keyValue(field, value):
    if field == 'hedging':
        return readHedging(value)[0]
    if field in LISTING_NUMBERS:
        return None if value in (None, '') else round(float(value), 9)
    return _text(value).lower()


def _sameIdentity(a, b) -> bool:
    """Whether two listings are named the same way: the same ISIN, CUSIP,
    SEDOL, code, exchange, issue currency, hedging, income and class."""
    # (all() here is the catalogue, not the builtin)
    return not any(_text(a.get(f)).lower() != _text(b.get(f)).lower() for f in LISTING_KEY)


def _sameListing(a, b) -> bool:
    # (all() here is the catalogue, not the builtin)
    return not any(not _same(a.get(f), b.get(f)) for f in LISTING_FIELDS)


def _hedging(value, isg, record, sma, key, isgKey, where):
    """A listing's hedging as stored (D224): (the proportion hedged, a
    percentage from 0 to 100 or None for not said; ISG's tick, 'Yes' or '').
    The percentage may be typed with its sign, or as the word it was before
    - Hedged 100, Unhedged 0, ISG Hedged 50 and ticked ISG; only an SMA's
    mandate may be ISG's."""
    name = record.get('name') or 'it'
    tick = _isgTick(isg, isgKey, where)
    level = None
    if isinstance(value, bool):
        raise ValidationError(key, _hedgingAsked(where))
    if isinstance(value, (int, float)):
        level = float(value)
    else:
        text = _text(value)
        word = text.lower()
        if word in _HEDGE_WORDS:
            level = _HEDGE_WORDS[word]
            if word == ISG_WORD:
                if not sma:
                    raise ValidationError(key, 'ISG Hedged is a separate account\'s: {} is {}. Give the '
                                               'hedging{} as a percentage.'.format(
                                                   name, _an(record.get('vehicle')), where))
                tick = ISG
        elif text:
            number = text[:-1].strip() if text.endswith('%') else text
            try:
                level = float(number)
            except ValueError:
                raise ValidationError(key, _hedgingAsked(where, text))
    if level is not None and (math.isnan(level) or not 0 <= level <= FULLY_HEDGED):
        raise ValidationError(key, _hedgingAsked(where, _text(value)))
    if tick and not sma:
        raise ValidationError(isgKey, 'Only a separate account\'s mandate is ISG\'s: {} is {}. Take '
                                      'the ISG tick off{}.'.format(name, _an(record.get('vehicle')), where))
    return level, tick


def _hedgingAsked(where, given='') -> str:
    return 'The hedging{}{} is the proportion hedged, a percentage from 0 to 100: 100 for fully hedged, ' \
           '0 for unhedged, 50 for half, or blank.'.format(where, ' {!r}'.format(given) if given else '')


def _isgTick(value, key, where) -> str:
    """ISG's tick on an SMA's mandate (D224): 'Yes' or ''."""
    if isinstance(value, bool):
        return ISG if value else ''
    word = _text(value).lower()
    if not word:
        return ''
    if word in _ISG_WORDS:
        return _ISG_WORDS[word]
    raise ValidationError(key, 'The ISG tick{} {!r} is Yes or blank.'.format(where, _text(value)))


def readHedging(value, isg=None):
    """Any hedging as it was stored, recorded or shown, at any time (D224):
    (the percentage or None, ISG's tick 'Yes' or '') - a number, a word it
    was before, or the words hedgingText gives, '50% · ISG'."""
    tick = ISG if isg is True or _ISG_WORDS.get(_text(isg).lower()) else ''
    if isinstance(value, bool) or value is None:
        return None, tick
    if isinstance(value, (int, float)):
        return float(value), tick
    level = None
    for bit in _text(value).split('\u00b7'):
        word = bit.strip().lower()
        if word in _HEDGE_WORDS:
            level = _HEDGE_WORDS[word]
            tick = ISG if word == ISG_WORD else tick
        elif word == 'isg':
            tick = ISG
        elif word:
            try:
                level = float(word.rstrip('%').strip())
            except ValueError:
                pass
    return level, tick


def hedgingText(listing, said=False) -> str:
    """A listing's hedging as a person reads it (D224): 100%, 50% · ISG, or
    '' for not said; *said*, in a line of words: 100% hedged · ISG."""
    level, tick = readHedging((listing or {}).get('hedging'), (listing or {}).get('isg'))
    bits = (['{:g}%{}'.format(level, ' hedged' if said else '')] if level is not None else []) \
        + (['ISG'] if tick else [])
    return ' \u00b7 '.join(bits)


def _said(listing, field) -> str:
    """A listing's field in a line of words: the hedging as hedgingText says it."""
    return hedgingText(listing, said=True) if field == 'hedging' else _text(listing.get(field))


def fullyHedged(listing) -> bool:
    """Whether a listing's class is hedged whole to its class currency."""
    return readHedging((listing or {}).get('hedging'))[0] == FULLY_HEDGED


def _incomeWord(text) -> bool:
    """Whether *text* says Accumulating or Distributing, long or short."""
    return INCOME_SHORT.get(_text(text), _text(text)) in INCOMES


def _income(text, key, where='') -> str:
    """A class's income as stored: one of the two words, its short form read
    as the word, or '' for none."""
    income = INCOME_SHORT.get(text, text)
    if income and income not in INCOMES:
        same = [v for v in INCOMES if v.lower() == income.lower()]
        if same:
            return same[0]
        raise ValidationError(key, 'The income{} {!r} is not one of {}, or blank for '
                                   'none.'.format(where, income, ', '.join(INCOMES)))
    return income


def _shareClass(text, key, where='') -> str:
    """The income, by the name the field had before the levels (D218)."""
    return _income(text, key, where)


def _ticker(value) -> str:
    """A ticker as a file or a record kept it before (D220): '' for none."""
    ticker = _text(value)
    return '' if ticker in (NO_TICKER, '-', '–') else ticker


def _identifier(value, key, kind, strict=True, where='') -> str:
    """A listing's CUSIP or SEDOL as stored (D220): upper case, '' for
    none, and on a save or an import one of its shape."""
    code = _text(value).upper()
    if code in (NO_TICKER, '-', '–'):
        code = ''
    if strict and code and not (CUSIP_RE if kind == 'cusip' else SEDOL_RE).match(code):
        raise ValidationError(key, 'The {}{} {!r} is not one: {}.'.format(
            LABELS[kind], where, code, 'eight letters or digits and a check digit, such as 037833100'
            if kind == 'cusip' else 'six letters or digits and a check digit, such as B0YBKJ7'))
    return code


def _isinShaped(value) -> bool:
    """Whether *value* has an ISIN's shape: two letters, nine letters or
    digits, a check digit."""
    return bool(ISIN_RE.match(_text(value).upper()))


def _isin(value, key, strict=True, where='') -> str:
    """An ISIN as stored (D218): upper case, '' for none, and on a save or
    an import one with an ISIN's shape."""
    isin = _text(value).upper()
    if isin in (NO_TICKER, '-', '–'):
        isin = ''
    if strict and isin and not ISIN_RE.match(isin):
        raise ValidationError(key, 'The ISIN{} {!r} is not one: two letters, nine letters or digits and '
                                   'a check digit, such as IE00B4L5Y983.'.format(where, isin))
    return isin


def _currencyCode(value, key, strict=True, where='') -> str:
    """A class currency as stored (D218): a three-letter code in upper
    case, or '' for none."""
    code = _text(value).upper()
    if strict and code and not re.match(r'^[A-Z]{3}$', code):
        raise ValidationError(key, 'The class currency{} {!r} is not a currency code: three letters, such '
                                   'as USD or GBP.'.format(where, code))
    return code


def _domicile(value, key, strict=True, where='') -> str:
    """A domicile as stored (D218): a two-letter country code in upper case,
    checked on a save or an import, or '' for none."""
    code = _text(value).upper()
    if strict and code and code not in COUNTRY_CODES:
        raise ValidationError(key, 'The domicile{} {!r} is not a country code: two letters, such as IE, LU, '
                                   'GB or US.'.format(where, code))
    return code


def _replication(value, key, where='') -> str:
    """How a class tracks its index, as stored: one of the three words, or ''."""
    word = _text(value)
    if not word:
        return ''
    same = [v for v in REPLICATIONS if v.lower() == word.lower()]
    if same:
        return same[0]
    raise ValidationError(key, 'The replication{} {!r} is not one of {} or {}, or blank.'.format(
        where, word, ', '.join(REPLICATIONS[:-1]), REPLICATIONS[-1]))


def _yesNo(value, key, field, where='') -> str:
    """Whether a class has UK reporting status, or is UCITS compliant
    (D220), as stored: Yes, No or ''."""
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    word = _text(value)
    if not word:
        return ''
    if word.lower() in _UK_WORDS:
        return _UK_WORDS[word.lower()]
    raise ValidationError(key, 'The {}{} {!r} is Yes, No or blank.'.format(LABELS[field], where, word))


def _legacyCurrencies(value) -> dict:
    """A product's values by base currency as they were stored before
    listings (D180, D182), read whatever shape they were kept in."""
    if not value:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    if not isinstance(value, dict):
        return {}
    return {c: dict(v) for c, v in value.items() if c in CURRENCIES and isinstance(v, dict)}


def legacyListings(record) -> list:
    """The listings a product stored before them reads as (D207). Its main
    values are the main listing; each base currency it had values of its
    own in is a listing of those values over the main ones, after them in
    the currencies' order - two currencies alike sharing one, and one that
    would be the same listing as another dealt in that currency. A currency
    it was not offered in is nothing: every product is offered in every
    currency now. An SMA with mandates (D197) is listed under them instead:
    each code a listing of the values its currency had, run in that
    currency, with the figures and the benchmark it carried of its own, in
    the currencies' order and in each Hedged, ISG Hedged, then Unhedged."""
    main = {f: record.get(f) for f in _OLD_LISTING_FIELDS}
    main['ticker'] = _ticker(main['ticker'])
    main['code'], main['hedging'] = '', ''
    for word in ('issueCurrency', 'exposureCurrency', 'assetClass', 'benchmark'):
        main[word] = _text(main[word])
    main['shareClass'] = main['shareClass'] or None
    own = _legacyCurrencies(record.get('currencies'))

    def inCcy(currency):
        values = dict(main)
        mine = own.get(currency) or {}
        if mine.get('ticker'):
            values['ticker'] = '' if mine['ticker'] == NO_TICKER else mine['ticker']
        for figure in _LEGACY_FIGURES:
            if mine.get(figure) is not None:
                values[figure] = mine[figure]
        for word in _LEGACY_WORDS:
            if mine.get(word):
                values[word] = mine[word]
        return values
    mandates = record.get('mandates') or []
    if isinstance(mandates, str):
        try:
            mandates = json.loads(mandates)
        except ValueError:
            mandates = []
    mandates = [m for m in mandates if isinstance(m, dict) and m.get('code')]
    if mandates:
        def order(m):
            return (CURRENCIES.index(m.get('currency')) if m.get('currency') in CURRENCIES
                    else len(CURRENCIES),
                    MANDATE_VERSIONS.index(m.get('hedging')) if m.get('hedging') in MANDATE_VERSIONS
                    else -1)
        listings = []
        for m in sorted(mandates, key=order):
            currency = _text(m.get('currency')).upper()
            values = inCcy(currency)
            for figure in _LEGACY_FIGURES:
                if m.get(figure) is not None:
                    values[figure] = m[figure]
            if m.get('benchmark'):
                values['benchmark'] = m['benchmark']
            values.update(code=_text(m.get('code')), issueCurrency=currency,
                          hedging=_text(m.get('hedging')))
            listings.append(values)
    else:
        listings = [dict(main)]
        for currency in CURRENCIES:
            mine = own.get(currency)
            if not mine or mine.get('offered') is False:
                continue
            values = inCcy(currency)
            if any(_sameListing(l, values) for l in listings):
                continue
            if not values['issueCurrency'] and any(_sameIdentity(l, values) for l in listings):
                values['issueCurrency'] = currency
            listings.append(values)
    return listings


def _listingsOf(record) -> list:
    """The listings of a record, stored before listings (D207), before the
    three levels (D218), or since - as they read now."""
    return modernise(record)['listings'] if record else []


def _facts(listing) -> dict:
    """A listing as the catalogue keeps it (D214): its facts, without the
    rankings by base currency a listing stored before carried, and the
    quotation unit one carried for a while (D219)."""
    return {k: v for k, v in listing.items() if k not in DROPPED}


def modernise(record, notes=None) -> dict:
    """*record* - a product as stored, served or recorded at any time - as
    the three levels read it (D218), its listings each carrying the class's
    values and its own. One stored or recorded before: a ticker shaped like
    an ISIN is the ISIN; Accumulating or Distributing where the class letter
    goes is the income; the asset class moves up from the first listing to
    the product; a class is in the currency its listing is issued in, and a
    hedged class's exposure is that currency; a hedging stored as a word is
    the percentage it reads as (D224), ISG Hedged ticked ISG; a UCITS or ICAV vehicle is an
    ETF or a Mutual Fund, a UCITS one's classes UCITS compliant; and
    Separate Account reads SMA. One stored before the CUSIP and the SEDOL
    (D220): a class's regulation reads as its UCITS compliance, and a
    listing's ticker is not kept, but for one shaped like an ISIN on a class
    with none. *notes*, when given, is a list each value that moved is said
    in. A record in the shape already is returned as it is, so this is safe
    to read through twice."""
    if not record:
        return record
    out = dict(record)
    if isinstance(record.get('listings'), list) and record['listings']:
        listings = [_facts(l) for l in record['listings']]
    elif record.get('currencies') or record.get('mandates'):
        listings = legacyListings(record)
    elif 'income' in record:
        listings = [{f: record.get(f) for f in LISTING_FIELDS}]
    else:
        listings = [{f: record.get(f) for f in _OLD_LISTING_FIELDS}]
    vehicle = _text(out.get('vehicle'))
    ucits = ''
    if vehicle.upper() in REGULATED_VEHICLES:
        ucits = _ucitsOf(vehicle)
        out['vehicle'] = _fundVehicle(out.get('name'))
        _say(notes, 'vehicle {} → {}{}'.format(vehicle, out['vehicle'],
                                               ', UCITS compliant' if ucits else ''))
    elif vehicle.lower() in _VEHICLE_WORDS and _VEHICLE_WORDS[vehicle.lower()] != vehicle:
        out['vehicle'] = _VEHICLE_WORDS[vehicle.lower()]
        _say(notes, 'vehicle {} → {}'.format(vehicle, out['vehicle']))
    if not _text(out.get('provider')):
        out['provider'] = ''
    sma = isSma(out)
    modern, old = [], []
    for listing in listings:
        if 'income' in listing:
            modern.append(_identified(listing, sma, notes))
            continue
        old.append(listing)
        modern.append(_modernListing(listing, sma, notes))
    if not _text(out.get('assetClass')):
        out['assetClass'] = next((_text(l.get('assetClass')) for l in old if _text(l.get('assetClass'))), '')
    for listing in old:
        if _text(listing.get('assetClass')) and _text(listing.get('assetClass')) != out['assetClass']:
            _say(notes, 'asset class {} of listing {} → the product\'s, {}'.format(
                listing['assetClass'], _ident(listing), out['assetClass']))
    if ucits:
        for listing in modern:
            if not listing.get('ucits') and not sma:
                listing['ucits'] = ucits
    for listing in modern:
        # a field a listing was stored without, as one saved before it
        # existed (a mandate's name, D222): blank, or no figure
        for field in LISTING_FIELDS:
            listing.setdefault(field, None if field in LISTING_NUMBERS else '')
        # the hedging stored as a word, before it was a percentage (D224)
        if not isinstance(listing['hedging'], (int, float)) or isinstance(listing['hedging'], bool):
            was = _text(listing['hedging'])
            listing['hedging'], listing['isg'] = readHedging(listing['hedging'], listing['isg'])
            if was:
                _say(notes, 'hedging {} of {} → {}'.format(was, _ident(listing), hedgingText(listing)))
        elif not isinstance(listing['hedging'], float):
            listing['hedging'] = float(listing['hedging'])
    out['listings'] = modern
    for field in LISTING_FIELDS:
        out[field] = modern[0].get(field)
    return out


def _modernListing(listing, sma, notes=None) -> dict:
    """A listing stored or recorded before the three levels (D218), as they
    read it."""
    new = {f: '' for f in LISTING_FIELDS}
    new['code'] = _text(listing.get('code'))
    new['mandateName'] = _text(listing.get('mandateName'))
    ticker = _ticker(listing.get('ticker'))
    if not sma and _isinShaped(ticker):
        new['isin'] = ticker.upper()
        _say(notes, 'ticker {} → the ISIN'.format(new['isin']))
    elif ticker and ticker.lower() != 'sma':
        _say(notes, 'ticker {} not kept'.format(ticker))
    was = _text(listing.get('shareClass'))
    if _incomeWord(was):
        new['income'] = INCOME_SHORT.get(was, was)
    elif was and not sma:
        new['shareClass'] = was
    new['income'] = new['income'] or None
    new['hedging'] = listing.get('hedging')
    new['issueCurrency'] = _text(listing.get('issueCurrency'))
    new['classCurrency'] = '' if sma else new['issueCurrency'].upper()
    new['exposureCurrency'] = _text(listing.get('exposureCurrency'))
    if (not sma and fullyHedged(new) and new['classCurrency']
            and new['exposureCurrency'].upper() != new['classCurrency']):
        _say(notes, 'exposure currency {} of {} → {}, the currency it is hedged to'.format(
            new['exposureCurrency'] or '—', _ident(new), new['classCurrency']))
        new['exposureCurrency'] = new['classCurrency']
    for figure in LISTING_FIGURES:
        new[figure] = listing.get(figure)
    new['benchmark'] = _text(listing.get('benchmark'))
    return new


def _identified(listing, sma, notes=None) -> dict:
    """A listing stored in the three levels (D218), as the CUSIP and the
    SEDOL read it (D220): its regulation is its UCITS compliance, and its
    ticker is not kept - one shaped like an ISIN is the class's ISIN where
    it has none. One stored since is returned as it is."""
    if 'sedol' in listing and 'ticker' not in listing and 'regulation' not in listing:
        return dict(listing)
    new = {k: v for k, v in listing.items() if k not in ('ticker', 'regulation')}
    new.setdefault('cusip', '')
    new.setdefault('sedol', '')
    if 'ucits' not in new:
        new['ucits'] = '' if sma else _ucitsOf(listing.get('regulation'))
        if _text(listing.get('regulation')):
            _say(notes, 'regulation {} of {} → UCITS compliant {}'.format(
                _text(listing['regulation']), _ident(new), new['ucits'] or '—'))
    ticker = _ticker(listing.get('ticker'))
    if ticker and not sma and _isinShaped(ticker) and not _text(new.get('isin')):
        new['isin'] = ticker.upper()
        _say(notes, 'ticker {} → the ISIN'.format(new['isin']))
    elif ticker and ticker.lower() != 'sma':
        _say(notes, 'ticker {} of {} not kept'.format(ticker, _ident(new)))
    return new


def _say(notes, words) -> None:
    if notes is not None:
        notes.append(words)


def isSma(product) -> bool:
    """Whether *product* is a separate account (D197): its vehicle says so."""
    return _text((product or {}).get('vehicle')).lower() in SMA_VEHICLES


def _an(word) -> str:
    """'an ETF', 'a Mutual Fund', 'of no vehicle'."""
    word = _text(word)
    if not word:
        return 'of no vehicle'
    # SMA and ETF are said letter by letter: an es, an ee
    spoken = 'aeiou' if word != word.upper() else 'aefhilmnorsx'
    return ('an ' if word[0].lower() in spoken else 'a ') + word


def _ident(listing) -> str:
    """What a listing is called by: its mandate code, else its ISIN, else
    its SEDOL, else its CUSIP (D220)."""
    return (_text(listing.get('code')) or _text(listing.get('isin')).upper()
            or _text(listing.get('sedol')).upper() or _text(listing.get('cusip')).upper() or NO_TICKER)


def listingLabel(listing, listings=None) -> str:
    """A listing as a person names it (D207, D220): its mandate code, else
    its ISIN, else its SEDOL or CUSIP, and where the product has another
    listing of that name, what tells them apart - its issue currency, then
    its hedging, then its income, its class, its exchange: IE00B4L5Y983 ·
    GBP, M34 · EUR, IE00B4L5Y983 · LSE."""
    ident = _ident(listing)
    peers = [l for l in (listings or [listing]) if _ident(l) == ident]
    if len(peers) <= 1:
        return ident
    bits = [ident]
    for field in ('issueCurrency', 'hedging', 'income', 'shareClass', 'exchange', 'sedol', 'cusip'):
        if len({_said(p, field) for p in peers}) > 1 and _said(listing, field):
            bits.append(_said(listing, field))
            peers = [p for p in peers if _said(p, field) == _said(listing, field)]
            if len(peers) == 1:
                break
    return ' · '.join(bits)


def showListing(listing, ident=False) -> str:
    """One listing as a history line or a preview shows it."""
    bits = [_ident(listing)] if ident else []
    if ident and listing.get('isin') and _ident(listing) != _text(listing.get('isin')).upper():
        bits.append(_text(listing['isin']).upper())
    bits += [_said(listing, f) for f in ('exchange', 'issueCurrency', 'hedging', 'income')
             if _said(listing, f)]
    for field, word in (('sedol', 'SEDOL'), ('cusip', 'CUSIP')):
        if _text(listing.get(field)) and not (ident and _ident(listing) == _text(listing[field]).upper()):
            bits.append('{} {}'.format(word, listing[field]))
    if _text(listing.get('shareClass')):
        bits.append('class {}'.format(listing['shareClass']))
    for field, word in (('exposureCurrency', 'exposure'), ('domicile', 'domicile'),
                        ('replication', 'replication')):
        if _text(listing.get(field)):
            bits.append('{} {}'.format(word, listing[field]))
    if _text(listing.get('ucits')):
        bits.append('UCITS {}'.format(listing['ucits']))
    if _text(listing.get('ukReporting')):
        bits.append('UK reporting {}'.format(listing['ukReporting']))
    for field, word in (('productCost', 'cost'), ('distributionYield', 'yield'),
                        ('minimumInvestment', 'min')):
        if listing.get(field) is not None:
            bits.append('{} {}'.format(word, show(field, listing[field])))
    if listing.get('benchmark'):
        bits.append('benchmark {}'.format(listing['benchmark']))
    return ' · '.join(bits) or '—'


def _held(listing, listings) -> dict:
    """A listing's values as a proposal's line carries them."""
    out = {f: listing.get(f) for f in LISTING_FIELDS}
    out['listing'] = listingLabel(listing, listings)
    return out


def asHeld(product):
    """*product* as a proposal holds it (D214): with the values of its first
    listing, whatever the proposal's currency or the line's amount, until
    the rules for choosing between listings are settled. A sleeve's product
    carries the one held instead below its minimum (D192), read the same way."""
    if not product:
        return product
    out = {k: v for k, v in product.items() if k not in ('ranked', 'ticker', 'regulation')}
    listings = _listingsOf(product)
    out['listings'] = listings
    out.update(_held(listings[0], listings))
    if isinstance(product.get('alternative'), dict):
        out['alternative'] = asHeld(product['alternative'])
    return out


def holdListing(item, listing) -> dict:
    """*item*, a line of a proposal, holding *listing* of its product."""
    out = dict(item)
    out.update({f: listing.get(f) for f in LISTING_FIELDS})
    out.pop('ticker', None)
    out.pop('regulation', None)
    out['listing'] = listing.get('listing') or listingLabel(listing)
    return out


def findListing(product, held):
    """The listing of *product* a proposal recorded holding - by its ISIN,
    SEDOL and CUSIP where it recorded them, issue currency, hedging and
    income as the register kept them, its exchange and class where it
    recorded them - or None. A line recorded before the levels (D218) named
    the ISIN as its ticker and the income as its share class, and is matched
    as it reads now; a ticker it recorded otherwise is not kept (D220), so
    plays no part. Its mandate code is matched where the record names one,
    since one held without may have been given its code since."""
    ticker = _ticker(held.get('ticker'))
    isin = _text(held.get('isin')).upper() or (ticker.upper() if _isinShaped(ticker) else '')
    ids = {f: _text(held.get(f)).upper() for f in ('sedol', 'cusip') if _text(held.get(f))}
    if 'income' in held:
        income, letter = _text(held.get('income')), _text(held.get('shareClass'))
    else:
        was = _text(held.get('shareClass'))
        income, letter = (INCOME_SHORT.get(was, was) if _incomeWord(was) else ''), ''
    for listing in modernise(product).get('listings') or []:
        if (not isin or _text(listing.get('isin')).upper() == isin) \
                and not any(_text(listing.get(f)).upper() != v for f, v in ids.items()) \
                and _text(listing.get('issueCurrency')).upper() == _text(held.get('issueCurrency')).upper() \
                and readHedging(listing.get('hedging'), listing.get('isg')) \
                == readHedging(held.get('hedging'), held.get('isg')) \
                and (not income or _text(listing.get('income')) == income) \
                and (not letter or _text(listing.get('shareClass')) == letter) \
                and (not held.get('exchange') or _text(listing.get('exchange')).lower() == _text(held.get('exchange')).lower()) \
                and (not held.get('code') or _text(listing.get('code')).lower() == _text(held.get('code')).lower()):
            return listing
    return None


def _note(note) -> str:
    if note is not None and not isinstance(note, str):
        raise ValidationError('note', 'A note is words.')
    note = ' '.join((note or '').split())
    if len(note) > NOTE_MAX:
        raise ValidationError('note', 'Keep the note to {} characters.'.format(NOTE_MAX))
    return note


def show(key, value) -> str:
    """One field's value in the words a history line or a preview uses."""
    if value is None or value == '':
        return '—'
    if key == 'minimumInvestment':
        return '{:,.0f}'.format(value)
    if key in _PERCENT:
        return '{:g}'.format(value)
    if key == 'hedging':
        return hedgingText({'hedging': value}) or '—'
    return str(value)


def _sameValue(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if a is not None and b is not None and abs(float(a) - float(b)) <= 1e-12:
            return True
    return a == b or (a in (None, '') and b in (None, ''))


def changes(before, after) -> list:
    """Field by field, what moved between two records: [{field, label,
    before, after}] - what the product is, then its listings (D207), matched
    by what makes each the one it is: each value that moved in one that
    stayed, one that came or went, and their order where it moved (D214) -
    the first being the one a proposal holds. A product of one listing
    before and after reads as a product always did, field by field; a
    record stored before listings reads as it is listed."""
    if before is None:
        return []
    before, after = modernise(before), modernise(after)
    out = []
    for key in PRODUCT_FIELDS[1:]:
        was, now = before.get(key), after.get(key)
        if _sameValue(was, now):
            continue
        out.append({'field': key, 'label': LABELS[key], 'before': show(key, was),
                    'after': show(key, now)})
    was, now = _listingsOf(before), _listingsOf(after)
    one = len(was) == 1 and len(now) == 1
    if one:
        pairs, gone, came = [(was[0], now[0])], [], []
    else:
        pairs, gone, came = _pairListings(was, now)
    for a, b in pairs:
        label = listingLabel(b, now)
        for field in LISTING_FIELDS:
            x, y = a.get(field), b.get(field)
            if _sameValue(x, y):
                continue
            out.append({'field': field if one else 'listing:{}:{}'.format(label, field),
                        'label': LABELS[field] if one else '{} of {}'.format(LABELS[field], label),
                        'before': show(field, x), 'after': show(field, y)})
    for b in came:
        label = listingLabel(b, now)
        out.append({'field': 'listing+:' + label, 'label': 'Listing ' + label, 'before': '—',
                    'after': showListing(b)})
    for a in gone:
        label = listingLabel(a, was)
        out.append({'field': 'listing-:' + label, 'label': 'Listing ' + label,
                    'before': showListing(a), 'after': 'taken off'})
    if not one and _orderMoved(was, now, pairs):
        out.append({'field': 'order', 'label': LABELS['order'],
                    'before': _orderWords(was), 'after': _orderWords(now)})
    return out


def _orderMoved(was, now, pairs) -> bool:
    """Whether the listings two records share stand in another order, or
    another listing is now the first, the one a proposal holds (D214)."""
    at = {id(b): j for j, b in enumerate(now)}
    first = {id(a): i for i, a in enumerate(was)}
    placed = [at[id(b)] for a, b in sorted(pairs, key=lambda p: first[id(p[0])])]
    held = next((b for a, b in pairs if was and a is was[0]), None)
    return placed != sorted(placed) or (bool(now) and held is not now[0])


def _pairListings(was, now):
    """The listings two records share, matched by what makes each the one it
    is: (pairs, gone, came). One whose identifiers, code, issue currency, hedging
    or share class moved is paired with the one that came at its turn."""
    pairs, left, gone = [], list(range(len(now))), []
    for a in was:
        hit = next((j for j in left if _listingKey(now[j]) == _listingKey(a)), None)
        if hit is None:
            gone.append(a)
            continue
        left.remove(hit)
        pairs.append((a, now[hit]))
    came = [now[j] for j in left]
    while gone and came:
        pairs.append((gone.pop(0), came.pop(0)))
    return pairs, gone, came


def _orderWords(listings) -> str:
    """Listings in their order, in words: 'GEF-GBPH, then GEF-USD · GBP'."""
    return ', then '.join(listingLabel(l, listings) for l in listings) or '—'


def changeWords(entries) -> list:
    """The changes as the lines a history or the feed prints."""
    return ['{} {} → {}'.format(c['label'], c['before'], c['after']) for c in entries]


# ---------------------------------------------------------- the database ---

_lock = threading.Lock()
_cache = None            # (stamp, catalogue)
_seedProblem = None      # why the first load could not be made, if it could not


def _record(row, notes=None) -> dict:
    """A stored row as the record ``clean`` returns: its listings as stored
    (D207, D218) - or, for a row stored before them, as its values by base
    currency and its SMA mandates read - and, for one stored before the
    three levels, as they read it (``modernise``, which says each value
    that moved in *notes*)."""
    keys = row.keys()
    base = {
        'productId': row['productId'], 'name': row['name'],
        'provider': (row['provider'] if 'provider' in keys else '') or '',
        # what a row stored before listings kept its ticker in, read only
        # for an ISIN now (D220)
        'ticker': row['ticker'] or '',
        'assetClass': row['assetClass'], 'category': row['category'] or '',
        'style': row['style'], 'vehicle': row['vehicle'], 'shareClass': row['shareClass'] or None,
        'source': row['source'], 'liquidity': row['liquidity'],
        'exposureCurrency': row['exposureCurrency'],
        'issueCurrency': (row['issueCurrency'] if 'issueCurrency' in keys else '') or '',
        'productCost': float(row['productCost']), 'feeGroup': row['feeGroup'],
        'distributionYield': None if row['distributionYield'] is None else float(row['distributionYield']),
        'minimumInvestment': None if row['minimumInvestment'] is None else float(row['minimumInvestment']),
        'benchmark': (row['benchmark'] if 'benchmark' in keys else '') or '',
        'code': '', 'hedging': None, 'isg': '',
    }
    text = row['listings'] if 'listings' in keys else ''
    if text:
        listings = [_facts(l) for l in json.loads(text)]
    else:
        listings = legacyListings(dict(
            base, currencies=row['currencies'] if 'currencies' in keys else '',
            mandates=row['mandates'] if 'mandates' in keys else ''))
    return _mirrored(modernise(dict(base, listings=listings), notes))


def _listingText(record) -> str:
    return json.dumps(record['listings'])


def _served(record) -> dict:
    """A record in the shape every consumer has always read: its main
    listing's values at the top level, and its listings (D207). The
    product's own category is the catalogue's (D178): a sleeve product's
    category is its sleeve's, so it is not served beside it."""
    out = dict(record)
    out['listings'] = [dict(l) for l in record['listings']]
    # its own category, which a notice may be about (D199): served under its
    # own name, since the category a sleeve's product is served under is the
    # sleeve's
    out['ownCategory'] = record.get('category') or ''
    out.pop('category', None)
    return out


def _entry(row) -> dict:
    """A product as the console reads it: served fields, its category and
    its stamps."""
    out = _served(_record(row))
    out.update(category=row['category'] or '', revision=row['revision'], createdBy=row['createdBy'], createdAt=row['createdAt'],
               lastEditedBy=row['updatedBy'], lastEditedAt=row['updatedAt'],
               withdrawn=bool(row['withdrawnAt']), withdrawnBy=row['withdrawnBy'],
               withdrawnAt=row['withdrawnAt'])
    return out


def _meta(conn, key, default=''):
    row = conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
    return row[0] if row is not None else default


def _tableThere(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND "
                        "name = 'products'").fetchone() is not None


def _read(conn):
    """The catalogue on *conn*, or None when it has not been loaded yet."""
    if not _tableThere(conn) or not _meta(conn, 'productsSeededAt'):
        return None
    rows = conn.execute('SELECT * FROM products ORDER BY position, productId').fetchall()
    live, order, entries = {}, [], {}
    for row in rows:
        entries[row['productId']] = _entry(row)
        if not row['withdrawnAt']:
            live[row['productId']] = _served(_record(row))
            order.append(row['productId'])
    last = conn.execute('SELECT actor, at FROM productHistory ORDER BY at DESC, id DESC '
                        'LIMIT 1').fetchone()
    return {
        'live': live, 'order': order, 'entries': entries,
        'revision': int(_meta(conn, 'catalogueRevision', '0') or 0),
        'lastEditedAt': last['at'] if last else '', 'lastEditedBy': last['actor'] if last else '',
        'seededAt': _meta(conn, 'productsSeededAt'), 'seededFrom': _meta(conn, 'productsSeededFrom'),
        # what the move to three levels changed (D218), product by product
        'levelsReport': json.loads(_meta(conn, 'levelsReport', '') or '[]'),
    }


def _catalogue() -> dict:
    """The catalogue, from memory while the file has not moved. A file whose
    catalogue has not been loaded yet goes through the full connection,
    which loads it; if that cannot, BadCatalogue says why."""
    global _cache
    from . import sleeveRepo
    path = sleeveRepo.dbPath()
    stamp = sleeveRepo.fileStamp(path)
    with _lock:
        if stamp is not None and _cache is not None and _cache[0] == stamp:
            return _cache[1]
    found = None
    if stamp is not None:
        conn = sleeveRepo.readOnly(path)
        if conn is not None:
            try:
                # a file not yet moved to this schema is read the full way,
                # which moves it first
                if int(_meta(conn, 'schemaVersion', '0') or 0) >= sleeveRepo.SCHEMA_VERSION:
                    found = _read(conn)
            except Exception:                  # noqa: BLE001 - read again the full way
                found = None
            finally:
                conn.close()
    if found is None:
        conn = sleeveRepo.connection()
        try:
            stamp = sleeveRepo.fileStamp(path)
            found = _read(conn)
        finally:
            conn.close()
    if found is None:
        raise BadCatalogue(_seedProblem or 'The product catalogue has not been loaded.')
    with _lock:
        _cache = (stamp, found)
    return found


def _forget() -> None:
    """Drop the cached catalogue: after every write from this process."""
    global _cache
    with _lock:
        _cache = None


def reload() -> None:
    _forget()


# ----------------------------------------------------------------- reads ---

def all() -> list:                          # noqa: A001 - the catalogue, in its order
    """Every live product, served shape, in catalogue order."""
    cat = _catalogue()
    return [dict(cat['live'][pid]) for pid in cat['order']]


def get(productId: str):
    """One live product, or None. Callers that need a product to exist say so."""
    entry = _catalogue()['live'].get(productId)
    return dict(entry) if entry else None


def has(productId: str) -> bool:
    return productId in _catalogue()['live']


def entries(withdrawn=False) -> list:
    """The console's products, with their stamps: the live ones in catalogue
    order, or with *withdrawn* the withdrawn ones, most recently first."""
    cat = _catalogue()
    if not withdrawn:
        return [dict(cat['entries'][pid]) for pid in cat['order']]
    out = [dict(e) for e in cat['entries'].values() if e['withdrawn']]
    out.sort(key=lambda e: e['withdrawnAt'], reverse=True)
    return out


def entry(productId: str):
    """One product as the console reads it, live or withdrawn, or None."""
    found = _catalogue()['entries'].get(productId)
    return dict(found) if found else None


def describeSource() -> dict:
    """What the catalogue holds and when it last moved."""
    cat = _catalogue()
    from . import sleeveRepo
    return {
        'path': sleeveRepo.dbPath(),
        'products': len(cat['order']),
        'withdrawn': len(cat['entries']) - len(cat['order']),
        'revision': cat['revision'],
        'lastEditedAt': cat['lastEditedAt'], 'lastEditedBy': cat['lastEditedBy'],
        # the date the console's header gives the catalogue
        'modified': cat['lastEditedAt'],
        'seededAt': cat['seededAt'], 'seededFrom': cat['seededFrom'],
        'levelsReport': cat.get('levelsReport') or [],
    }


def vocabulary() -> dict:
    """The values the console offers for each word a product or a share
    class is described by - every one a live product already uses, in A-Z
    order, on any of its listings (D207) - with the fixed lists: the
    vehicles, the incomes, the replications, UK reporting, UCITS
    compliance (D220). The hedging is a percentage (D224), so has none."""
    cat = _catalogue()
    out = {}
    for key in ('assetClass', 'style', 'source', 'liquidity', 'provider', 'exposureCurrency',
                'issueCurrency', 'benchmark', 'classCurrency', 'domicile', 'exchange',
                'shareClass'):
        seen = {p[key] for p in cat['live'].values() if p.get(key)}
        if key in LISTING_FIELDS:
            seen.update(l[key] for p in cat['live'].values() for l in p.get('listings') or []
                        if l.get(key))
        out[key] = sorted(seen, key=lambda v: v.lower())
    out['vehicle'] = list(VEHICLES)
    out['income'] = list(INCOMES)
    out['replication'] = list(REPLICATIONS)
    out['ukReporting'] = list(UK_REPORTING)
    out['ucits'] = list(UCITS)
    return out


def _holders(conn, productId) -> list:
    """The live sleeves that hold a product, or name it to hold below another
    product's minimum (D192)."""
    from . import sleeveRepo
    return [{'id': r['id'], 'variant': sleeveRepo.typesLabel(r['variant']),
             'variants': sleeveRepo.typesOf(r['variant']), 'category': r['category'],
             'name': r['name'], 'label': r['label']}
            for r in conn.execute(
                "SELECT DISTINCT s.id, s.variant, s.category, s.name, s.label FROM sleeveProducts p "
                "JOIN sleeves s ON s.id = p.sleeveId WHERE (p.productId = ? OR p.alternative = ?) "
                "AND s.deletedAt = '' ORDER BY s.variant, s.category, s.name, s.label",
                (productId, productId)).fetchall()]


def feeGroupUsage(conn=None) -> dict:
    """{fee group: [live product ids]} - what the fee card's groups price."""
    from . import sleeveRepo
    own = conn is None
    conn = conn or sleeveRepo.connection()
    try:
        out = {}
        for r in conn.execute("SELECT productId, feeGroup FROM products WHERE withdrawnAt = '' "
                              "ORDER BY position"):
            out.setdefault(r['feeGroup'], []).append(r['productId'])
        return out
    finally:
        if own:
            conn.close()


def history(productId: str) -> list:
    """Every revision of one product, newest first, each with the product as
    it stood and what moved from the one before it. Empty for an id the
    catalogue never held."""
    from . import sleeveRepo
    conn = sleeveRepo.connection()
    try:
        rows = conn.execute('SELECT * FROM productHistory WHERE productId = ? ORDER BY revision',
                            (productId,)).fetchall()
    finally:
        conn.close()
    out, previous = [], None
    top = rows[-1]['revision'] if rows else None
    for r in rows:
        body = json.loads(r['body'])
        moved = changes(previous, body)
        restoredFrom = None
        if r['action'] == 'reverted':
            restoredFrom = next((e['revision'] for e in reversed(out)
                                 if e['product'] == body), None)
        out.append({'productId': productId, 'revision': r['revision'], 'action': r['action'],
                    'product': body, 'changes': moved, 'changeWords': changeWords(moved),
                    'note': r['note'], 'source': r['source'], 'actor': r['actor'], 'at': r['at'],
                    'restoredFrom': restoredFrom, 'current': r['revision'] == top})
        previous = body
    out.reverse()
    return out


# ------------------------------------------------------------- the seed ---

def seed(conn, feeGroups) -> None:
    """Fill the catalogue from the product file, once (schema 7) - race-safe
    the way the library's seed is, asked again inside a write transaction.
    Called on every connection to the repository's file, before the
    library's own seed. A file that cannot be read leaves the catalogue
    unloaded and says why on every read, and the next connection tries
    again; with no fee card yet there is nothing to check a product's group
    against, so it waits for the card."""
    global _seedProblem
    if _meta(conn, 'productsSeededAt'):
        return
    if feeGroups is None:
        _seedProblem = 'The product catalogue waits for the fee card, which has not been loaded.'
        return
    path = sourcePath()
    try:
        byId, order = _load(path, set(feeGroups), strict=False)
    except FileNotFoundError:
        _seedProblem = '{}: the product file to load the catalogue from is not there.'.format(path)
        return
    except (BadCatalogue, OSError, ValueError) as exc:
        _seedProblem = 'The product catalogue could not be loaded from {}: {}'.format(path, exc)
        return
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    try:
        if _meta(conn, 'productsSeededAt'):
            conn.rollback()
            return
        stamp = _now()
        source = os.path.basename(path)
        for position, pid in enumerate(order):
            _insert(conn, byId[pid], position, 'seed', stamp)
            _history(conn, pid, 1, 'seeded', byId[pid], 'seed', stamp, source=source)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('productsSeededAt', ?)", (stamp,))
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('productsSeededFrom', ?)", (path,))
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('catalogueRevision', '1')")
        conn.commit()
        _seedProblem = None
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------- writes ---

def _insert(conn, record, position, user, stamp) -> None:
    # the ticker column is kept blank: a listing has none (D220)
    conn.execute(
        'INSERT INTO products (productId, position, name, provider, ticker, assetClass, category, style, '
        'vehicle, shareClass, source, liquidity, exposureCurrency, issueCurrency, productCost, '
        'feeGroup, distributionYield, minimumInvestment, listings, benchmark, revision, '
        'createdBy, createdAt, updatedBy, updatedAt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '
        '?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)',
        (record['productId'], position, record['name'], record.get('provider') or '', '',
         record['assetClass'],
         record.get('category') or '', record['style'], record['vehicle'], record['shareClass'] or '', record['source'],
         record['liquidity'], record['exposureCurrency'], record.get('issueCurrency') or '',
         record['productCost'], record['feeGroup'], record['distributionYield'],
         record['minimumInvestment'], _listingText(record),
         record.get('benchmark') or '', user, stamp, user, stamp))


def _update(conn, record, revision, user, stamp) -> None:
    conn.execute(
        'UPDATE products SET name = ?, provider = ?, ticker = ?, assetClass = ?, category = ?, style = ?, '
        'vehicle = ?, shareClass = ?, source = ?, liquidity = ?, exposureCurrency = ?, '
        'issueCurrency = ?, productCost = ?, feeGroup = ?, distributionYield = ?, minimumInvestment = ?, '
        "listings = ?, currencies = '', mandates = '', benchmark = ?, revision = ?, updatedBy = ?, "
        'updatedAt = ? WHERE productId = ?',
        (record['name'], record.get('provider') or '', '', record['assetClass'],
         record.get('category') or '',
         record['style'],
         record['vehicle'], record['shareClass'] or '', record['source'], record['liquidity'],
         record['exposureCurrency'], record.get('issueCurrency') or '', record['productCost'],
         record['feeGroup'], record['distributionYield'], record['minimumInvestment'],
         _listingText(record), record.get('benchmark') or '', revision, user,
         stamp, record['productId']))


def _history(conn, productId, revision, action, record, user, stamp, note='', source='') -> None:
    conn.execute('INSERT INTO productHistory (productId, revision, action, body, note, source, '
                 'actor, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                 (productId, revision, action, json.dumps(record), note, source, user, stamp))


def _bump(conn) -> int:
    """The catalogue's own version: one more for every write, whatever it
    touched. A delivered proposal records the one it was priced with."""
    revision = int(_meta(conn, 'catalogueRevision', '0') or 0) + 1
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('catalogueRevision', ?)", (str(revision),))
    return revision


def _writing(work):
    """Run *work(conn)* holding the file's write lock from its first read to
    its commit (BEGIN IMMEDIATE), so a save's checks and its revision are one
    step and two saves cannot claim one revision. The cache is dropped after."""
    from . import sleeveRepo
    conn = sleeveRepo.connection()
    try:
        if not _meta(conn, 'productsSeededAt'):
            raise BadCatalogue(_seedProblem or 'The product catalogue has not been loaded.')
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        try:
            result = work(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return result
    finally:
        conn.close()
        _forget()


def _groupsOn(conn) -> set:
    from . import feeCard
    groups = feeCard.groupsOn(conn)
    if groups is None:
        raise ValidationError('feeGroup', 'The fee card has not been loaded, so no fee group '
                                          'can be checked.')
    return set(groups)


def _row(conn, productId):
    return conn.execute('SELECT * FROM products WHERE productId = ?', (productId,)).fetchone()


def _live(conn, productId):
    row = _row(conn, productId)
    if row is None:
        raise ValidationError('productId', 'There is no product {} in the catalogue.'.format(productId))
    if row['withdrawnAt']:
        raise ValidationError('productId', '{} was withdrawn by {} on {}. Restore it first.'.format(
            row['name'], row['withdrawnBy'] or 'someone', row['withdrawnAt'][:10]))
    return row


def _base(base):
    """The version a write was made against, or None when the caller did
    not say."""
    if base is None:
        return None
    if isinstance(base, str) and base.strip().isdigit():
        base = int(base)
    if isinstance(base, bool) or not isinstance(base, int):
        raise ValidationError('base', 'The version this change was made against is not a number.')
    return base


def _refuseStale(row, base) -> None:
    if base is not None and row['revision'] != base:
        raise StaleError('base', '{} was changed by {} at {} since you opened it (now version {}). '
                         'Reload to see the change, then make yours again.'.format(
                             row['name'], row['updatedBy'] or 'someone',
                             (row['updatedAt'] or '').replace('T', ' ')[:16], row['revision']))


def _entryOn(conn, productId) -> dict:
    return _entry(_row(conn, productId))


def _identifiers(record):
    """[(kind, value)] - the ISINs, CUSIPs, SEDOLs and mandate codes
    *record*'s listings are bought under, each its own (D207, D218, D220);
    a blank is nothing."""
    out, seen = [], set()
    for listing in record.get('listings') or []:
        for kind in ('isin', 'cusip', 'sedol', 'code'):
            value = _text(listing.get(kind))
            if not value:
                continue
            if (kind, value.lower()) not in seen:
                seen.add((kind, value.lower()))
                out.append((kind, value))
    return out


def _kindWords(kind):
    """('ISIN', 'An ISIN'), ('SEDOL', 'A SEDOL'), ('mandate code', 'A mandate code')."""
    if kind == 'isin':
        return 'ISIN', 'An ISIN'
    if kind in ('cusip', 'sedol'):
        return LABELS[kind], 'A ' + LABELS[kind]
    return LABELS[kind].lower(), 'A ' + LABELS[kind].lower()


def _identifiersHeld(conn, exceptIds=()) -> dict:
    """{(kind, value lower-cased): (value, productId, name)} for the ISINs,
    CUSIPs and SEDOLs of every live product and the mandate codes of every
    product, withdrawn ones included (D197, D207, D220), but *exceptIds*:
    each belongs to one product, and a withdrawn product keeps its codes."""
    out = {}
    for r in conn.execute('SELECT * FROM products'):
        if r['productId'] in exceptIds:
            continue
        for kind, value in _identifiers(_record(r)):
            if kind != 'code' and r['withdrawnAt']:
                continue
            out.setdefault((kind, value.lower()), (value, r['productId'], r['name']))
    return out


def _refuseTaken(conn, record, held=None) -> None:
    """An ISIN, a CUSIP, a SEDOL or a mandate code another product holds
    refuses the save, naming the listing's box and the product that holds it."""
    held = _identifiersHeld(conn, {record['productId']}) if held is None else held
    for n, listing in enumerate(record.get('listings') or []):
        for kind in ('code', 'isin', 'cusip', 'sedol'):
            value = _text(listing.get(kind))
            taken = held.get((kind, value.lower())) if value else None
            if taken is not None:
                raise ValidationError('listing{}{}'.format(n, kind), 'The {} {} belongs to {} ({}). {} '
                                      'is one product\'s.'.format(_kindWords(kind)[0], taken[0], taken[2],
                                                                  taken[1], _kindWords(kind)[1]))


def create(fields, user='', note='') -> dict:
    """Add a product to the catalogue. Refused, naming the field, when it is
    not a product the catalogue can hold or its id is taken - by a live
    product or a withdrawn one, since an id is never reused."""
    note = _note(note)
    known = knownCategories()

    def work(conn):
        record = clean(fields, _groupsOn(conn), strict=True, categories=known)
        taken = _row(conn, record['productId'])
        if taken is not None:
            if taken['withdrawnAt']:
                raise ValidationError('productId', 'The ID {} belongs to {}, withdrawn on {}. An ID '
                                      'is never reused: restore that product, or choose another '
                                      'ID.'.format(record['productId'], taken['name'],
                                                   taken['withdrawnAt'][:10]))
            raise ValidationError('productId', 'The ID {} is already {}. Choose another.'.format(
                record['productId'], taken['name']))
        _refuseTaken(conn, record)
        position = conn.execute('SELECT COALESCE(MAX(position), -1) + 1 FROM products').fetchone()[0]
        stamp = _now()
        _insert(conn, record, position, user or '', stamp)
        _history(conn, record['productId'], 1, 'created', record, user or '', stamp, note=note)
        _bump(conn)
        return _entryOn(conn, record['productId'])

    return _writing(work)


def update(productId, fields, user='', note='', base=None) -> dict:
    """Change a product's values. Its id is fixed: sleeves refer to it.
    Refused when a value is wrong, nothing would change, or - as STALE -
    *base* is not the version in force."""
    note = _note(note)
    base = _base(base)
    fields = dict(fields or {})
    asked = _text(fields.get('productId'), keepSpaces=True)
    if asked and asked != productId:
        raise ValidationError('productId', 'A product ID cannot be changed: sleeves refer to the '
                                           'product by it. Add a new product instead.')
    fields['productId'] = productId
    known = knownCategories()

    def work(conn):
        row = _live(conn, productId)
        _refuseStale(row, base)
        stored = _record(row)
        if 'listings' not in fields and not fields.get('currencies') and not fields.get('mandates'):
            # a save that says nothing of the listings keeps them, with any
            # value it gives at the top level its main listing's (D207)
            listings = [dict(l) for l in stored['listings']]
            for key in LISTING_FIELDS:
                if key in fields:
                    listings[0][key] = fields[key]
            fields['listings'] = listings
        elif isinstance(fields.get('listings'), list) and fields['listings'] \
                and isinstance(fields['listings'][0], dict):
            # a value at the top level that the main listing given does not
            # share is the main listing's: the whole product sent back with a
            # field changed where it has always been read (D207)
            main = dict(fields['listings'][0])
            for key in LISTING_FIELDS:
                if key not in fields:
                    continue
                if not _sameValue(fields[key], main.get(key)):
                    main[key] = fields[key]
            fields['listings'] = [main] + list(fields['listings'][1:])
        record = clean(fields, _groupsOn(conn), strict=True, categories=known)
        if not changes(stored, record):
            raise ValidationError('product', 'Nothing has changed: these are the values in force.')
        _refuseTaken(conn, record)
        revision = row['revision'] + 1
        stamp = _now()
        _update(conn, record, revision, user or '', stamp)
        _history(conn, productId, revision, 'updated', record, user or '', stamp, note=note)
        _bump(conn)
        return _entryOn(conn, productId)

    return _writing(work)


def reorder(productId, order, user='', note='', base=None) -> dict:
    """Put a product's listings in a new order (D214), the console's drag:
    *order* the listings' places as they stand, in the order wanted. The
    first is the one a proposal holds. Nothing else about them moves, so
    nothing is checked again. Refused when *order* is not each listing once,
    nothing would change, or - as STALE - *base* is not the version in
    force."""
    note = _note(note)
    base = _base(base)

    def work(conn):
        row = _live(conn, productId)
        _refuseStale(row, base)
        stored = _record(row)
        count = len(stored['listings'])
        if (not isinstance(order, list) or any(isinstance(i, bool) or not isinstance(i, int)
                                               for i in order)
                or sorted(order) != list(range(count))):
            raise ValidationError('order', 'Give each of the {} listings of {} once, in the order '
                                           'wanted.'.format(count, row['name']))
        if order == list(range(count)):
            raise ValidationError('order', 'That is the order the listings are in.')
        record = _mirrored(dict(stored, listings=[stored['listings'][i] for i in order]))
        revision = row['revision'] + 1
        stamp = _now()
        _update(conn, record, revision, user or '', stamp)
        _history(conn, productId, revision, 'updated', record, user or '', stamp, note=note)
        _bump(conn)
        return _entryOn(conn, productId)

    return _writing(work)


def _refuseHeld(conn, row) -> None:
    held = _holders(conn, row['productId'])
    if held:
        named = ['{}{} ({} · {})'.format(h['name'], ' · ' + h['label'] if h['label'] else '',
                                         h['variant'], h['category']) for h in held]
        raise ValidationError('product', '{} is in {} sleeve{}: {}{}. Take it out of {} first; a '
                              'product a sleeve holds cannot be withdrawn.'.format(
                                  row['name'], len(held), '' if len(held) == 1 else 's',
                                  ', '.join(named[:5]), ', …' if len(named) > 5 else '',
                                  'it' if len(held) == 1 else 'them'))


def withdraw(productId, user='', note='', base=None) -> dict:
    """Take a product out of the catalogue. It keeps its row, its history
    and its id, appears under the console's withdrawn products, and can be
    restored. Refused while a live sleeve holds it."""
    note = _note(note)
    base = _base(base)

    def work(conn):
        row = _live(conn, productId)
        _refuseStale(row, base)
        _refuseHeld(conn, row)
        revision = row['revision'] + 1
        stamp = _now()
        conn.execute('UPDATE products SET withdrawnBy = ?, withdrawnAt = ?, revision = ?, '
                     'updatedBy = ?, updatedAt = ? WHERE productId = ?',
                     (user or '', stamp, revision, user or '', stamp, productId))
        _history(conn, productId, revision, 'withdrawn', _record(row), user or '', stamp, note=note)
        _bump(conn)
        return _entryOn(conn, productId)

    return _writing(work)


def restore(productId, user='', note='') -> dict:
    """Put a withdrawn product back, as it was. Refused when it could not be
    saved today - its fee group has left the fee card."""
    note = _note(note)

    def work(conn):
        row = _row(conn, productId)
        if row is None:
            raise ValidationError('productId', 'There is no product {} in the catalogue.'.format(productId))
        if not row['withdrawnAt']:
            raise ValidationError('productId', '{} is in the catalogue already.'.format(row['name']))
        record = _record(row)
        groups = _groupsOn(conn)
        if record['feeGroup'] not in groups:
            raise ValidationError('feeGroup', '{} is priced by the fee group {}, which is no longer '
                                  'on the fee card. Add the group back on the Fees tab, then restore '
                                  'it.'.format(record['name'], record['feeGroup']))
        # an identifier another live product took while it was withdrawn (D207)
        _refuseTaken(conn, record)
        revision = row['revision'] + 1
        stamp = _now()
        conn.execute("UPDATE products SET withdrawnBy = '', withdrawnAt = '', revision = ?, "
                     'updatedBy = ?, updatedAt = ? WHERE productId = ?',
                     (revision, user or '', stamp, productId))
        _history(conn, productId, revision, 'restored', record, user or '', stamp, note=note)
        _bump(conn)
        return _entryOn(conn, productId)

    return _writing(work)


def revert(productId, revision, user='', note='', base=None) -> dict:
    """Put an earlier version's values back in force, as a new version -
    checked again, since the fee card may have moved since it was saved."""
    note = _note(note)
    base = _base(base)
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValidationError('revision', 'Choose a version to restore.')

    def work(conn):
        row = _live(conn, productId)
        _refuseStale(row, base)
        held = conn.execute('SELECT * FROM productHistory WHERE productId = ? AND revision = ?',
                            (productId, revision)).fetchone()
        if held is None:
            raise ValidationError('revision', 'There is no version {} of {}.'.format(revision, row['name']))
        body = json.loads(held['body'])
        current = _record(row)
        if 'listings' not in body and 'mandates' not in body \
                and any(l.get('code') for l in current['listings']):
            # a version from before the SMA mandates (D197) keeps the coded
            # listings in force (D207)
            body['listings'] = current['listings']
        record = clean(modernise(body), _groupsOn(conn), strict=False)
        if not changes(current, record):
            raise ValidationError('revision', 'Those are the values in force already.')
        _refuseTaken(conn, record)
        top = row['revision'] + 1
        stamp = _now()
        _update(conn, record, top, user or '', stamp)
        _history(conn, productId, top, 'reverted', record, user or '', stamp,
                 note=note or 'Restored version {}'.format(revision))
        _bump(conn)
        return _entryOn(conn, productId)

    return _writing(work)


def renameFeeGroup(conn, old, new, user, stamp, note) -> int:
    """Move every product priced by the fee group *old* to *new*, inside the
    fee card's own save (feeCard.save), one revision each. Returns how many
    products moved."""
    rows = conn.execute('SELECT * FROM products WHERE feeGroup = ? ORDER BY position',
                        (old,)).fetchall()
    for row in rows:
        record = _record(row)
        record['feeGroup'] = new
        revision = row['revision'] + 1
        conn.execute('UPDATE products SET feeGroup = ?, revision = ?, updatedBy = ?, '
                     'updatedAt = ? WHERE productId = ?',
                     (new, revision, user or '', stamp, row['productId']))
        _history(conn, row['productId'], revision, 'updated', record, user or '', stamp,
                 note=note)
    if rows:
        _bump(conn)
    return len(rows)


# ------------------------------------------------------- import / export ---

def _decode(content) -> bytes:
    """An upload's bytes, sent as base64 text so the console needs no
    multipart support on the server."""
    import base64
    import binascii
    if isinstance(content, bytes):
        return content
    if not isinstance(content, str) or not content:
        raise ValidationError('file', 'Choose a product file to import.')
    try:
        return base64.b64decode(content.split(',', 1)[-1] if content.startswith('data:') else content,
                                validate=False)
    except (binascii.Error, ValueError):
        raise ValidationError('file', 'The file could not be read.')


def _fileName(name) -> str:
    name = os.path.basename(str(name or '').replace('\\', '/')).strip()
    if not name.lower().endswith(('.csv', '.xlsx')):
        raise ValidationError('file', 'A product file is a .csv or an .xlsx.')
    return name


#: the columns a file may leave out that then leave the catalogue's values
#: as they are (D204, D207, D218): (field, column)
_KEPT_COLUMNS = [(f, _COLUMN_OF[f]) for f in ('isin', 'shareClass', 'income', 'hedging', 'classCurrency',
                                              'domicile', 'ucits', 'replication', 'ukReporting',
                                              'benchmark', 'exchange', 'cusip', 'sedol', 'mandateName', 'isg')]


def _keepStored(incoming, stored, found):
    """*incoming*, a product as a file gives it, with what the catalogue
    holds of it kept where the file has no column for it (D204, D207,
    D218): (record, whether its listings were kept whole). A file without a
    Benchmark column leaves each listing's benchmark, one without a Domicile
    column the domicile, and so for every column of the share class and the
    listing - each listing matched to the one it is in the catalogue by the
    columns the file does have; a file that gives the income under
    ShareClass, as before the levels, gives the income and keeps the class
    letters. The file's order is
    the listings' order (D214). One without a MandateCode column leaves an
    SMA whose listings carry codes listed as it is, since the file cannot
    say which row is which code; it takes what the product is from the
    file. One without a Provider column keeps the provider. A
    ValidationError says the listings kept are wrong for the product as the
    file gives it."""
    if 'MandateCode' not in found and isSma(incoming) and any(l.get('code') for l in stored['listings']):
        return clean(dict(incoming, listings=stored['listings']), strict=False), True
    present = set(found)
    # a file's Hedging column says ISG Hedged where it has no ISG column
    missing = [f for f, column in _KEPT_COLUMNS if column not in present
               and not (f == 'isg' and 'Hedging' in present)]
    keepProvider = 'Provider' not in present and stored.get('provider') and not incoming.get('provider')
    if not missing and not keepProvider:
        return incoming, False
    was, now = stored['listings'], [dict(l) for l in incoming['listings']]
    fields = [f for f in LISTING_KEY if _COLUMN_OF[f] in present]
    for listing in now:
        match = next((l for l in was if not any(_text(l.get(f)).lower() != _text(listing.get(f)).lower()
                                                for f in fields)), None)
        if match is None and len(was) == 1 and len(now) == 1:
            match = was[0]
        if match is None:
            continue
        for f in missing:
            if match.get(f) in (None, '') or (f == 'isin' and listing.get('isin')):
                continue
            listing[f] = match[f]
    product = dict(incoming, listings=now)
    if keepProvider:
        product['provider'] = stored['provider']
    return clean(product, strict=False), False


def _compare(conn, byId, order, fileName='', found=()) -> dict:
    """What a file would do to the catalogue on *conn*: each product the file
    gives with what it keeps of the one here (``_keepStored``), and refused
    when one is left wrong, or an ISIN, SEDOL, CUSIP or mandate code it gives is
    another product's (D207)."""
    rows = {r['productId']: r for r in conn.execute('SELECT * FROM products ORDER BY position')}
    held = _identifiersHeld(conn, set(order))
    added, changed, back, unchanged, kept = [], [], [], 0, []
    for pid in order:
        incoming, row = byId[pid], rows.get(pid)
        stored = _record(row) if row is not None else None
        if stored is not None:
            try:
                incoming, whole = _keepStored(incoming, stored, found)
            except ValidationError as exc:
                raise BadCatalogue('{}: {} ({}) keeps its coded listings, which the file leaves wrong: '
                                   '{}'.format(fileName, incoming['name'], pid, exc.message))
            byId[pid] = incoming
            if whole:
                kept.append({'productId': pid, 'name': incoming['name'],
                             'listings': len(incoming['listings'])})
        try:
            _refuseTaken(conn, incoming, held)
        except ValidationError as exc:
            raise BadCatalogue('{}: {} ({}): {}'.format(fileName, incoming['name'], pid, exc.message))
        if row is None:
            added.append({'productId': pid, 'name': incoming['name']})
            continue
        moved = changes(stored, incoming)
        if row['withdrawnAt']:
            back.append({'productId': pid, 'name': incoming['name'], 'changes': moved,
                         'changeWords': changeWords(moved)})
        elif moved:
            changed.append({'productId': pid, 'name': incoming['name'], 'changes': moved,
                            'changeWords': changeWords(moved)})
        else:
            unchanged += 1
    missing = []
    for pid, row in rows.items():
        if pid in byId or row['withdrawnAt']:
            continue
        held = _holders(conn, pid)
        missing.append({'productId': pid, 'name': row['name'], 'sleeves': held,
                        'canWithdraw': not held})
    return {'added': added, 'changed': changed, 'restored': back, 'unchanged': unchanged,
            'missing': missing, 'kept': kept}


def importPreview(name, content) -> dict:
    """What importing a file would do, without doing it: the products it
    adds, the ones it changes (field by field), the withdrawn ones it brings
    back, how many it leaves as they are, and the live ones it does not
    carry - kept unless the admin ticks them to be withdrawn, which a
    product a sleeve holds cannot be. A file with any bad row is refused
    whole, naming the row. *revision* is the catalogue version the preview
    was made against: the import is refused if it has moved since."""
    from . import sleeveRepo
    name = _fileName(name)
    data = _decode(content)
    known = knownCategories()
    conn = sleeveRepo.connection()
    try:
        groups = _groupsOn(conn)
        found, moved = set(), {}
        byId, order = _load(name, groups, strict=True, data=data, categories=known, found=found,
                            moved=moved)
        out = _compare(conn, byId, order, name, found)
        out.update(file=name, rows=len(order), legacy=BASE_COLUMN in found,
                   ranksIgnored='Rank' in found,
                   # what an older layout was read as (D218)
                   isinsFromTickers=moved.get('isinsFromTickers', 0),
                   # tickers are no longer kept (D220)
                   tickersDropped=moved.get('tickersDropped', 0),
                   regulationRead='Regulation' in found and 'UCITSCompliant' not in found,
                   vehiclesMoved=moved.get('vehicles', []),
                   revision=int(_meta(conn, 'catalogueRevision', '0') or 0))
        return out
    finally:
        conn.close()


def _refuseStaleCatalogue(conn, base) -> None:
    """An import made against a preview of an older catalogue is refused."""
    now = int(_meta(conn, 'catalogueRevision', '0') or 0)
    if base is not None and base != now:
        last = conn.execute('SELECT actor, at FROM productHistory ORDER BY at DESC, id DESC '
                            'LIMIT 1').fetchone()
        raise StaleError('base', 'The catalogue changed since this preview{} (now version {}). '
                         'Preview the file again.'.format(
                             ' - {} at {}'.format(last['actor'] or 'someone',
                                                  last['at'].replace('T', ' ')[:16]) if last else '',
                             now))


def importFile(name, content, user='', withdraw=None, note='', base=None) -> dict:
    """Apply a file: the new products added, the changed ones updated, the
    withdrawn ones it carries put back with its values - each one revision
    marked *imported*, naming the file - and the products ticked in
    *withdraw* withdrawn. Products the file leaves unchanged are not
    touched, so their Last edited stays true. All or nothing, and refused
    as STALE when *base* is not the catalogue version the preview showed."""
    name = _fileName(name)
    data = _decode(content)
    note = _note(note)
    base = _base(base)
    withdraw = [str(p) for p in (withdraw or [])]
    known = knownCategories()

    def work(conn):
        _refuseStaleCatalogue(conn, base)
        found = set()
        byId, order = _load(name, _groupsOn(conn), strict=True, data=data, categories=known,
                            found=found)
        plan = _compare(conn, byId, order, name, found)
        missing = {m['productId']: m for m in plan['missing']}
        for pid in withdraw:
            if pid not in missing:
                raise ValidationError('withdraw', '{} is not a product this file leaves out.'.format(pid))
            if not missing[pid]['canWithdraw']:
                _refuseHeld(conn, _row(conn, pid))
        if not (plan['added'] or plan['changed'] or plan['restored'] or withdraw):
            raise ValidationError('file', 'The file matches the catalogue: there is nothing to '
                                          'import.')
        stamp = _now()
        position = conn.execute('SELECT COALESCE(MAX(position), -1) + 1 FROM products').fetchone()[0]
        for item in plan['added']:
            record = byId[item['productId']]
            _insert(conn, record, position, user or '', stamp)
            _history(conn, record['productId'], 1, 'imported', record, user or '', stamp,
                     note=note, source=name)
            position += 1
        for item in plan['changed'] + plan['restored']:
            row = _row(conn, item['productId'])
            record = byId[item['productId']]
            revision = row['revision'] + 1
            _update(conn, record, revision, user or '', stamp)
            if row['withdrawnAt']:
                conn.execute("UPDATE products SET withdrawnBy = '', withdrawnAt = '' "
                             'WHERE productId = ?', (row['productId'],))
            _history(conn, record['productId'], revision, 'imported', record, user or '', stamp,
                     note=note, source=name)
        for pid in withdraw:
            row = _row(conn, pid)
            revision = row['revision'] + 1
            conn.execute('UPDATE products SET withdrawnBy = ?, withdrawnAt = ?, revision = ?, '
                         'updatedBy = ?, updatedAt = ? WHERE productId = ?',
                         (user or '', stamp, revision, user or '', stamp, pid))
            _history(conn, pid, revision, 'withdrawn', _record(row), user or '', stamp,
                     note=note or 'Not in {}'.format(name), source=name)
        revision = _bump(conn)
        return {'file': name, 'added': len(plan['added']), 'changed': len(plan['changed']),
                'restored': len(plan['restored']), 'withdrawn': len(withdraw),
                'unchanged': plan['unchanged'], 'revision': revision}

    return _writing(work)


def _cell(value):
    """A value as an export writes it: a whole number without its '.0', any
    other number at full precision, a blank as nothing."""
    if value is None:
        return ''
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return value


def exportRows() -> list:
    """The live catalogue as the file it can be loaded from again (D207): the
    header and, in catalogue order, a row for each listing of each product
    in the listings' order - what the product is on every one, and the
    listing's own values."""
    out = [list(EXPORT_COLUMNS)]
    for p in entries():
        for listing in p['listings']:
            out.append([_cell(hedgingText({'hedging': listing[key]}) if key == 'hedging'
                              else listing[key] if key in LISTING_FIELDS else p.get(key)) for key in KEYS])
    return out


def exportCsv() -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows(exportRows())
    return buf.getvalue()
