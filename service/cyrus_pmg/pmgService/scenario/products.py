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
fund, a mandate code of a separate account - each with its own ticker, issue
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
first one's values at its top level. A ticker may repeat across a product's
listings - one ISIN quoted in several currencies is one ticker - but
belongs to that product alone, and so does a mandate code. The file gives a
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
from. An empty ticker is served as the em dash the table has always shown.
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
currency and, for a Public Equity SMA, the hedging version it comes in
(Hedged, ISG Hedged, Unhedged). A fund's listing may be Hedged or Unhedged,
or say nothing. The proposal's hedging policy plays no part in which one is
held. The code shows nowhere in a proposal's files;
the account opening request reads it. The word "mandate" on its own is the
client's mandate everywhere else in the tool.

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

COLUMNS = ['ProductId', 'Name', 'Ticker', 'AssetClass', 'Style', 'Vehicle', 'Source',
           'Liquidity', 'ExposureCurrency', 'ProductCost', 'FeeGroup']
# Columns a delivery may leave out entirely, or leave blank on a row: each is
# served as None when it is absent, so an older extract still loads (D63, D81).
# Of these, the last two are figures the catalogue view compares on and must
# parse as numbers; the first is a word from a closed pair.
OPTIONAL = ['Category', 'ShareClass', 'IssueCurrency', 'DistributionYield', 'MinimumInvestment',
            'Benchmark', 'MandateCode', 'Hedging']
#: The product's own category (D178), the sleeve category the catalogue bands
#: it under: a file may head the column with any of these.
CATEGORY_HEADERS = ('Category', 'Catagory', 'Group')
#: The currency the product is issued in (D184): a file may head the column
#: with any of these, lower-cased and stripped of all but letters and digits.
_ISSUE_HEADERS = ('issuecurrency', 'issueccy', 'issuercurrency', 'issuerccy', 'quotecurrency',
                  'quoteccy', 'dealingcurrency')
#: A listing's mandate code and its hedging (D207), headed any of these ways
_CODE_HEADERS = ('mandatecode', 'mandate')
_HEDGING_HEADERS = ('hedging', 'hedgingversion')

#: What a share class can be: distributing, or accumulating (D81). Two values
#: and a blank, checked on load - a typo is refused rather than printed.
SHARE_CLASSES = ('Distributing', 'Accumulating')
#: The short forms a delivery used before the words were spelled out (D164),
#: still read, and served as the word.
SHARE_CLASS_SHORT = {'Dis': 'Distributing', 'Acc': 'Accumulating'}

# the field names the rest of the package has always used for a product
FIELDS = ['name', 'ticker', 'assetClass', 'style', 'vehicle', 'shareClass', 'source',
          'liquidity', 'exposureCurrency', 'issueCurrency', 'productCost', 'feeGroup']

NO_TICKER = '—'

#: Every field a product carries, in the order the file and the console give
#: them: the key the package uses, the file's column, and the words a message
#: or a history line uses for it.
_FIELDS = [
    ('productId', 'ProductId', 'Product ID'),
    ('name', 'Name', 'Name'),
    ('ticker', 'Ticker', 'Ticker'),
    ('assetClass', 'AssetClass', 'Asset class'),
    ('category', 'Category', 'Category'),
    ('style', 'Style', 'Style'),
    ('vehicle', 'Vehicle', 'Vehicle'),
    ('shareClass', 'ShareClass', 'Share class'),
    ('source', 'Source', 'Source'),
    ('liquidity', 'Liquidity', 'Liquidity'),
    ('exposureCurrency', 'ExposureCurrency', 'Exposure currency'),
    ('issueCurrency', 'IssueCurrency', 'Issue currency'),
    ('productCost', 'ProductCost', 'Product cost'),
    ('feeGroup', 'FeeGroup', 'Fee group'),
    ('distributionYield', 'DistributionYield', 'Distribution yield'),
    ('minimumInvestment', 'MinimumInvestment', 'Minimum investment'),
    # what it is measured against (D204): optional, blank for not given
    ('benchmark', 'Benchmark', 'Benchmark'),
    # an SMA listing's code and a listing's hedging (D197, D207)
    ('code', 'MandateCode', 'Mandate code'),
    ('hedging', 'Hedging', 'Hedging'),
]
KEYS = [k for k, _, _ in _FIELDS]
#: the file's columns in the order an export writes them - the packaged
#: file's own order
FILE_COLUMNS = [c for _, c, _ in _FIELDS]
LABELS = {k: label for k, _, label in _FIELDS}

#: What belongs to a LISTING rather than to the product (D207): the ticker it
#: is bought under, an SMA's mandate code, the currency it is dealt in, its
#: hedging, its share class, the currency exposure of what it holds, its asset
#: class, what it costs and yields, the least it takes and the index it is
#: measured against. A listing holds all of them whole: nothing in one is
#: read from another.
LISTING_FIELDS = ('ticker', 'code', 'issueCurrency', 'hedging', 'shareClass', 'exposureCurrency',
                  'assetClass', 'productCost', 'distributionYield', 'minimumInvestment', 'benchmark')
#: the ones of those that are figures
LISTING_FIGURES = ('productCost', 'distributionYield', 'minimumInvestment')
#: what the product is, whichever listing is held: the same on every one of
#: its rows in a file
PRODUCT_FIELDS = [k for k in KEYS if k not in LISTING_FIELDS]
#: what makes two listings of a product the same one: two the same are refused
LISTING_KEY = ('ticker', 'code', 'issueCurrency', 'hedging', 'shareClass')
#: what a listing kept before the catalogue held facts only (D214): its
#: place in each base currency's ranking, dropped wherever it is read
RANKS = 'ranks'
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
#: the six words a product is described by: free values, which the console
#: keeps consistent by offering the ones already in use
WORDS = ['assetClass', 'style', 'vehicle', 'source', 'liquidity', 'exposureCurrency']
#: the figures, all percentages but the minimum, which is currency units
_PERCENT = ('productCost', 'distributionYield')

#: What marks a product as a separate account, an SMA (D197): its vehicle,
#: which reads Separate Account on Poros and SMA in the sample file here,
#: compared without regard to case.
SMA_VEHICLES = ('separate account', 'sma')
#: the one sleeve category whose SMAs come in hedging versions
EQUITY_CATEGORY = 'Public Equity'
#: a product whose own category is this is a hedge fund (D198)
HEDGE_FUND_CATEGORY = 'Hedge Funds'
#: the hedging versions a Public Equity SMA's listing comes in, in order; an
#: SMA in any other category has none, stored as ''
MANDATE_VERSIONS = ('Hedged', 'ISG Hedged', 'Unhedged')
#: what a fund's listing may say of its hedging (D207): a hedged share class
#: is hedged to the currency it is issued in
FUND_HEDGING = ('Hedged', 'Unhedged')
#: a ticker every SMA reads (D197), and so no one product's
SHARED_TICKERS = ('sma',)
CODE_MAX = 40
#: what a product stored before listings kept by base currency, figures and
#: words (D180, D182, D184, D189, D190, D194, D204), read once to list it
_LEGACY_FIGURES = ('minimumInvestment', 'distributionYield', 'productCost')
_LEGACY_WORDS = ('exposureCurrency', 'issueCurrency', 'shareClass', 'assetClass', 'benchmark')

ID_MAX = 80
NAME_MAX = 120
TICKER_MAX = 24
WORD_MAX = 60
#: a benchmark is an index's whole name, so it may run longer than a word
BENCHMARK_MAX = 200
NOTE_MAX = 240
#: a figure at or above these is one typed in the wrong unit - 45 for 0.45
PERCENT_MAX = {'productCost': 10.0, 'distributionYield': 50.0}

#: what a revision's action can say
ACTIONS = ['seeded', 'created', 'updated', 'imported', 'withdrawn', 'restored', 'reverted']


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
    rows = sheet.iter_rows(values_only=True)
    head = next(rows, None) or []
    yield [str(h).strip() if h is not None else '' for h in head]
    for row in rows:
        if row and row[0] is not None and str(row[0]).strip():
            yield ['' if v is None else v for v in row]


def _norm(column) -> str:
    """A column's heading lower-cased and stripped of all but letters and digits."""
    return ''.join(ch for ch in str(column).lower() if ch.isalnum())


def _load(path: str, feeGroups=None, strict=False, data: bytes = None, categories=None,
          found=None):
    """The extract at *path* (or the upload *data* named *path*) as
    ({id: record}, [ids in file order]). Refuses the whole file with
    BadCatalogue at the first bad row, naming the row and the product: a
    file is loaded whole or not at all. *feeGroups*, when given, is the set
    a product's group must belong to, and *categories* the list its category
    must be one of. *strict* is the check a save gets
    (``clean``); without it, what the tool has always accepted from a file.
    *found*, when given, is a set the optional columns the file has are
    added to - 'Rank' for rank columns, which are not read (D214),
    BaseCurrency for the layout before listings."""
    rows = _readRows(path, data)
    head = next(rows)
    name = os.path.basename(path)
    missing = [c for c in COLUMNS if c not in head]
    if missing:
        raise BadCatalogue('{}: missing column(s) {}'.format(name, ', '.join(missing)))
    index = {c: head.index(c) for c in COLUMNS}
    for c in OPTIONAL:
        if c in head:
            index[c] = head.index(c)
    for c in CATEGORY_HEADERS:
        if c in head:
            index['Category'] = head.index(c)
            break
    ranked = False
    for at, column in enumerate(head):
        key = _norm(column)
        if key in _ISSUE_HEADERS and 'IssueCurrency' not in index:
            index['IssueCurrency'] = at
        elif key in _CODE_HEADERS and 'MandateCode' not in index:
            index['MandateCode'] = at
        elif key in _HEDGING_HEADERS and 'Hedging' not in index:
            index['Hedging'] = at
        if any(key in ('rank' + c.lower(), c.lower() + 'rank') for c in CURRENCIES):
            ranked = True
    baseAt = next((at for at, column in enumerate(head) if _norm(column) in _BASE_HEADERS), None)
    if found is not None:
        found.update(c for c in OPTIONAL if c in index)
        if ranked:
            found.add('Rank')
        if baseAt is not None:
            found.add(BASE_COLUMN)
    rowsOf, order = {}, []
    for n, row in enumerate(rows, start=2):
        fields = {}
        for key, column in _COLUMN_OF.items():
            if column not in index:
                fields[key] = None
                continue
            at = index[column]
            fields[key] = row[at] if at < len(row) else ''
        pid = _text(fields['productId'], keepSpaces=True)
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
        try:
            record = clean(fields, feeGroups, strict=strict, categories=categories)
        except ValidationError as exc:
            raise BadCatalogue('{}: row {} ({}): {}'.format(name, n, pid or 'no ProductId',
                                                            exc.message))
        if pid not in rowsOf:
            order.append(pid)
        rowsOf.setdefault(pid, []).append((n, currency, record))
    byId = {pid: _assemble(name, pid, rowsOf[pid], baseAt is not None, feeGroups, strict,
                           categories) for pid in order}
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


def _assemble(fileName, pid, rows, legacy, feeGroups, strict, categories):
    """One product from its rows in a file (D207): each row a listing, in the
    file's order, the first the one a proposal holds (D214). What the
    product is must read the same on every row; only a listing's values may
    differ. A file in the layout before listings (*legacy*, D182) gives a
    row per base currency instead: its All row - else the row of the first
    currency - is the first listing, and each currency's row a listing after
    it, two rows alike being one listing."""
    if legacy:
        rows = sorted(rows, key=lambda r: -1 if r[1] == ALL else CURRENCIES.index(r[1]))
    mainRow, _, main = rows[0]
    for n, currency, record in rows[1:]:
        for key in PRODUCT_FIELDS:
            if key == 'productId' or _same(record[key], main[key]):
                continue
            raise BadCatalogue(
                '{}: row {} ({}): it gives {} {} where row {} gives {} - a product\'s rows are its '
                'listings, and only a listing\'s ticker, mandate code, issue currency, hedging, '
                'share class, exposure currency, asset class, cost, yield, minimum and benchmark may '
                'differ between them.'.format(fileName, n, pid, LABELS[key].lower(),
                                              show(key, record[key]), mainRow, show(key, main[key])))
    listings, rowOf = [], []
    for n, currency, record in rows:
        listing = dict(record['listings'][0])
        if legacy:
            # a currency's row read as it always was: a word it leaves blank
            # is the main row's
            for word in ('exposureCurrency', 'assetClass', 'issueCurrency', 'benchmark', 'shareClass'):
                if not listing[word] and listings:
                    listing[word] = listings[0][word]
            if any(_sameListing(held, listing) for held in listings):
                continue
            if not listing['issueCurrency'] and currency != ALL and any(
                    _listingKey(held) == _listingKey(listing) for held in listings):
                listing['issueCurrency'] = currency
        listings.append(listing)
        rowOf.append(n)
    try:
        return clean(dict(main, listings=listings), feeGroups, strict=strict, categories=categories)
    except ValidationError as exc:
        at = re.match(r'listing(\d+)', exc.field or '')
        row = rowOf[int(at.group(1))] if at and int(at.group(1)) < len(rowOf) else mainRow
        raise BadCatalogue('{}: row {} ({}): {}'.format(fileName, row, pid, exc.message))


def _refuseShared(fileName, byId, order) -> None:
    """A ticker or a mandate code on two products of one file is refused,
    naming both: each is one product's (D207)."""
    seen = {}
    for pid in order:
        for kind, value in _identifiers(byId[pid]):
            other = seen.get((kind, value.lower()))
            if other is not None and other != pid:
                raise BadCatalogue('{}: the {} {} is on {} and on {}. A {} is one product\'s.'.format(
                    fileName, LABELS[kind].lower(), value, other, pid, LABELS[kind].lower()))
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
    listed as it reads (``legacyListings``). A ticker is '' when there is
    none, and so is a category, and a share class None."""
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
        raise ValidationError('name', 'Give the product a name.')
    if strict and len(name) > NAME_MAX:
        raise ValidationError('name', 'Keep the name to {} characters.'.format(NAME_MAX))
    record = {'productId': pid, 'name': name}
    for key in ('style', 'vehicle', 'source', 'liquidity'):
        record[key] = _word(fields.get(key), key, LABELS[key], strict)
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
    record['listings'] = _listings(given, record, strict, bare)
    return _mirrored(record)


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
    return label[0].lower() + label[1:]


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


def _listings(value, record, strict=True, bare=False) -> list:
    """The listings as stored (D207): [{ticker, code, issueCurrency, hedging,
    shareClass, exposureCurrency, assetClass, productCost, distributionYield,
    minimumInvestment, benchmark}], in the order given, each whole and
    checked as a product's values always were, and the list against
    *record*: only an SMA's listing has a mandate code; a fund's may be
    Hedged or Unhedged, a Public Equity SMA's any of its three versions,
    another SMA's none; and no two listings are the same one. Ranks given
    with a listing are not kept (D214). A refusal names the listing's box -
    listing0ticker, listing2hedging - or, for a product given its values at
    the top level (*bare*), the field."""
    if not isinstance(value, list) or not value or any(not isinstance(l, dict) for l in value):
        raise ValidationError('listings', 'A product has at least one listing: its ticker, its cost '
                                          'and the rest.')
    sma = isSma(record)
    equity = record.get('category') == EQUITY_CATEGORY
    name = record.get('name') or record.get('productId') or 'the product'
    out, seen = [], {}
    for n, given in enumerate(value):
        def box(field, n=n):
            return field if bare else 'listing{}{}'.format(n, field)
        named = _text(given.get('code')) or _ticker(given.get('ticker'), 'ticker', strict=False)
        where = '' if bare else ' of listing {}{}'.format(n + 1, ' ({})'.format(named) if named else '')
        listing = {'ticker': _ticker(given.get('ticker'), box('ticker'), strict, where)}
        code = _text(given.get('code'))
        if code and not sma:
            raise ValidationError(box('code'), 'Only a separate account (an SMA) has a mandate code: '
                                  '{} is {}. Take the code off{}, or make the vehicle Separate '
                                  'Account.'.format(name, _an(record.get('vehicle')), where))
        if strict and len(code) > CODE_MAX:
            raise ValidationError(box('code'), 'Keep the mandate code{} to {} characters.'.format(
                where, CODE_MAX))
        listing['code'] = code
        listing['issueCurrency'] = _word(given.get('issueCurrency'), box('issueCurrency'),
                                         LABELS['issueCurrency'] + where, strict, required=False)
        listing['hedging'] = _hedging(given.get('hedging'), record, sma, equity, box('hedging'), where)
        listing['shareClass'] = _shareClass(_text(given.get('shareClass')), box('shareClass'),
                                            where) or None
        for word in ('exposureCurrency', 'assetClass'):
            listing[word] = _word(given.get(word), box(word), LABELS[word] + where, strict)
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
        key = _listingKey(listing)
        if key in seen:
            first = seen[key]
            raise ValidationError(box('code' if code else 'ticker'), 'Listings {} and {} of {} are the '
                                  'same listing: {}. Give one its own issue currency, hedging or share '
                                  'class.'.format(first + 1, n + 1, name, showListing(listing, ident=True)))
        seen[key] = n
        out.append(listing)
    return out


def _listingKey(listing) -> tuple:
    """What makes a listing the one it is, among its product's (D207)."""
    return (_text(listing.get('ticker')).lower(), _text(listing.get('code')).lower(),
            _text(listing.get('issueCurrency')).upper(), _text(listing.get('hedging')),
            _text(listing.get('shareClass')))


def _sameListing(a, b) -> bool:
    # (all() here is the catalogue, not the builtin)
    return not any(not _same(a.get(f), b.get(f)) for f in LISTING_FIELDS)


def _hedging(value, record, sma, equity, key, where) -> str:
    """A listing's hedging as stored (D207): a fund's Hedged, Unhedged or
    blank; a Public Equity SMA's one of its versions or blank (D197);
    another SMA's blank, since only Public Equity comes hedged."""
    hedging = _text(value)
    if not hedging:
        return ''
    if sma and not equity:
        raise ValidationError(key, 'Only a {} SMA comes in hedging versions: {} is {}, so leave the '
                                   'hedging{} blank.'.format(EQUITY_CATEGORY, record.get('name') or 'it',
                                                             record.get('category') or 'in no category',
                                                             where))
    allowed = MANDATE_VERSIONS if sma else FUND_HEDGING
    if hedging in allowed:
        return hedging
    same = [v for v in allowed if v.lower() == hedging.lower()]
    if same:
        raise ValidationError(key, 'The hedging {!r} is spelled {}.'.format(hedging, same[0]))
    raise ValidationError(key, 'The hedging{} is {}, or blank.'.format(
        where, ' or '.join(allowed) if len(allowed) == 2 else ', '.join(allowed[:-1]) + ' or ' + allowed[-1])
        + (' ISG Hedged is a separate account\'s.' if not sma and hedging.lower() == 'isg hedged' else ''))


def _shareClass(text, key, where='') -> str:
    """A share class as stored: one of the two words, its short form read as
    the word, or '' for none."""
    shareClass = SHARE_CLASS_SHORT.get(text, text)
    if shareClass and shareClass not in SHARE_CLASSES:
        raise ValidationError(key, 'The share class{} {!r} is not one of {}, or blank for '
                                   'none.'.format(where, shareClass, ', '.join(SHARE_CLASSES)))
    return shareClass


def _ticker(value, key, strict=True, where='') -> str:
    """A ticker as stored: '' for none, however a file wrote none."""
    ticker = _text(value)
    if ticker in (NO_TICKER, '-', '–'):
        ticker = ''
    if strict and len(ticker) > TICKER_MAX:
        raise ValidationError(key, 'Keep the ticker{} to {} characters.'.format(where, TICKER_MAX))
    return ticker


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
    main = {f: record.get(f) for f in LISTING_FIELDS}
    main['ticker'] = _ticker(main['ticker'], 'ticker', strict=False)
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
            if not values['issueCurrency'] and any(_listingKey(l) == _listingKey(values)
                                                   for l in listings):
                values['issueCurrency'] = currency
            listings.append(values)
    return listings


def _listingsOf(record) -> list:
    """The listings of a record, stored before listings (D207) or since."""
    if not record:
        return []
    if isinstance(record.get('listings'), list) and record['listings']:
        return [_facts(l) for l in record['listings']]
    if record.get('currencies') or record.get('mandates'):
        return legacyListings(record)
    return [{f: record.get(f) for f in LISTING_FIELDS}]


def _facts(listing) -> dict:
    """A listing as the catalogue keeps it (D214): its facts, without the
    rankings by base currency a listing stored before carried."""
    return {k: v for k, v in listing.items() if k != RANKS}


def isSma(product) -> bool:
    """Whether *product* is a separate account (D197): its vehicle says so."""
    return _text((product or {}).get('vehicle')).lower() in SMA_VEHICLES


def _an(word) -> str:
    """'an ETF', 'a Mutual Fund', 'of no vehicle'."""
    word = _text(word)
    if not word:
        return 'of no vehicle'
    return ('an ' if word[0].lower() in 'aeiou' else 'a ') + word


def _ident(listing) -> str:
    """What a listing is called by: its mandate code, else its ticker."""
    return (_text(listing.get('code')) or _ticker(listing.get('ticker'), 'ticker', strict=False)
            or NO_TICKER)


def listingLabel(listing, listings=None) -> str:
    """A listing as a person names it (D207): its mandate code, else its
    ticker, and where the product has another listing of that name, what
    tells them apart - its issue currency, then its hedging, then its share
    class: GEF-USD · GBP, M34 · EUR."""
    ident = _ident(listing)
    peers = [l for l in (listings or [listing]) if _ident(l) == ident]
    if len(peers) <= 1:
        return ident
    bits = [ident]
    for field in ('issueCurrency', 'hedging', 'shareClass'):
        if len({_text(p.get(field)) for p in peers}) > 1 and _text(listing.get(field)):
            bits.append(_text(listing.get(field)))
            peers = [p for p in peers if _text(p.get(field)) == _text(listing.get(field))]
            if len(peers) == 1:
                break
    return ' · '.join(bits)


def showListing(listing, ident=False) -> str:
    """One listing as a history line or a preview shows it."""
    bits = [_ident(listing)] if ident else []
    bits += [_text(listing.get(f)) for f in ('issueCurrency', 'hedging', 'shareClass')
             if _text(listing.get(f))]
    for field, word in (('exposureCurrency', 'exposure'), ('assetClass', 'class')):
        if _text(listing.get(field)):
            bits.append('{} {}'.format(word, listing[field]))
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
    out['ticker'] = out['ticker'] or NO_TICKER
    out['listing'] = listingLabel(listing, listings)
    return out


def asHeld(product):
    """*product* as a proposal holds it (D214): with the values of its first
    listing, whatever the proposal's currency or the line's amount, until
    the rules for choosing between listings are settled. A sleeve's product
    carries the one held instead below its minimum (D192), read the same way."""
    if not product:
        return product
    out = dict(product)
    out.pop('ranked', None)
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
    out['ticker'] = listing.get('ticker') or NO_TICKER
    out['listing'] = listing.get('listing') or listingLabel(listing)
    return out


def findListing(product, held):
    """The listing of *product* a proposal recorded holding - by its ticker,
    issue currency, hedging and share class as the register kept them - or
    None. Its mandate code is matched where the record names one, since one
    held without may have been given its code since."""
    ticker = '' if held.get('ticker') in (None, NO_TICKER) else _text(held.get('ticker'))
    for listing in (product or {}).get('listings') or []:
        if (_text(listing.get('ticker')) == ticker
                and _text(listing.get('issueCurrency')).upper() == _text(held.get('issueCurrency')).upper()
                and _text(listing.get('hedging')) == _text(held.get('hedging'))
                and _text(listing.get('shareClass')) == _text(held.get('shareClass'))
                and (not held.get('code') or _text(listing.get('code')).lower()
                     == _text(held.get('code')).lower())):
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
            if field == 'ticker':
                x, y = _ticker(x, field, strict=False), _ticker(y, field, strict=False)
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
    is: (pairs, gone, came). One whose ticker, code, issue currency, hedging
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


def _record(row) -> dict:
    """A stored row as the record ``clean`` returns: its listings as stored
    (D207), or, for a row stored before them, as its values by base currency
    and its SMA mandates read."""
    keys = row.keys()
    base = {
        'productId': row['productId'], 'name': row['name'], 'ticker': row['ticker'] or '',
        'assetClass': row['assetClass'], 'category': row['category'] or '',
        'style': row['style'], 'vehicle': row['vehicle'], 'shareClass': row['shareClass'] or None,
        'source': row['source'], 'liquidity': row['liquidity'],
        'exposureCurrency': row['exposureCurrency'],
        'issueCurrency': (row['issueCurrency'] if 'issueCurrency' in keys else '') or '',
        'productCost': float(row['productCost']), 'feeGroup': row['feeGroup'],
        'distributionYield': None if row['distributionYield'] is None else float(row['distributionYield']),
        'minimumInvestment': None if row['minimumInvestment'] is None else float(row['minimumInvestment']),
        'benchmark': (row['benchmark'] if 'benchmark' in keys else '') or '',
        'code': '', 'hedging': '',
    }
    text = row['listings'] if 'listings' in keys else ''
    if text:
        listings = [_facts(l) for l in json.loads(text)]
    else:
        listings = legacyListings(dict(
            base, currencies=row['currencies'] if 'currencies' in keys else '',
            mandates=row['mandates'] if 'mandates' in keys else ''))
    return _mirrored(dict(base, listings=listings))


def _listingText(record) -> str:
    return json.dumps(record['listings'])


def _served(record) -> dict:
    """A record in the shape every consumer has always read: its main
    listing's values at the top level, and its listings (D207). The
    product's own category is the catalogue's (D178): a sleeve product's
    category is its sleeve's, so it is not served beside it."""
    out = dict(record)
    out['ticker'] = record['ticker'] or NO_TICKER
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
    }


def vocabulary() -> dict:
    """The values the console offers for each of the six words - every one a
    live product already uses, in A-Z order, on any of its listings (D207) -
    with the share classes, the hedging a fund's and a Public Equity SMA's
    listing may say, and the issue currencies and benchmarks in use."""
    cat = _catalogue()
    out = {}
    for key in WORDS + ['issueCurrency', 'benchmark']:
        seen = {p[key] for p in cat['live'].values() if p.get(key)}
        if key in LISTING_FIELDS:
            seen.update(l[key] for p in cat['live'].values() for l in p.get('listings') or []
                        if l.get(key))
        out[key] = sorted(seen, key=lambda v: v.lower())
    out['shareClass'] = list(SHARE_CLASSES)
    out['hedging'] = {'fund': list(FUND_HEDGING), 'sma': list(MANDATE_VERSIONS)}
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
    conn.execute(
        'INSERT INTO products (productId, position, name, ticker, assetClass, category, style, '
        'vehicle, shareClass, source, liquidity, exposureCurrency, issueCurrency, productCost, '
        'feeGroup, distributionYield, minimumInvestment, listings, benchmark, revision, '
        'createdBy, createdAt, updatedBy, updatedAt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '
        '?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)',
        (record['productId'], position, record['name'], record['ticker'], record['assetClass'],
         record.get('category') or '', record['style'], record['vehicle'], record['shareClass'] or '', record['source'],
         record['liquidity'], record['exposureCurrency'], record.get('issueCurrency') or '',
         record['productCost'], record['feeGroup'], record['distributionYield'],
         record['minimumInvestment'], _listingText(record),
         record.get('benchmark') or '', user, stamp, user, stamp))


def _update(conn, record, revision, user, stamp) -> None:
    conn.execute(
        'UPDATE products SET name = ?, ticker = ?, assetClass = ?, category = ?, style = ?, '
        'vehicle = ?, shareClass = ?, source = ?, liquidity = ?, exposureCurrency = ?, '
        'issueCurrency = ?, productCost = ?, feeGroup = ?, distributionYield = ?, minimumInvestment = ?, '
        "listings = ?, currencies = '', mandates = '', benchmark = ?, revision = ?, updatedBy = ?, "
        'updatedAt = ? WHERE productId = ?',
        (record['name'], record['ticker'], record['assetClass'], record.get('category') or '',
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
    """[(kind, value)] - the tickers and mandate codes *record*'s listings are
    bought under that are its own (D207): a ticker every SMA reads is no one
    product's, and a blank is nothing."""
    out, seen = [], set()
    for listing in record.get('listings') or []:
        for kind in ('ticker', 'code'):
            value = _ticker(listing.get(kind), kind, strict=False) if kind == 'ticker' \
                else _text(listing.get(kind))
            if not value or (kind == 'ticker' and value.lower() in SHARED_TICKERS):
                continue
            if (kind, value.lower()) not in seen:
                seen.add((kind, value.lower()))
                out.append((kind, value))
    return out


def _identifiersHeld(conn, exceptIds=()) -> dict:
    """{(kind, value lower-cased): (value, productId, name)} for the tickers
    of every live product and the mandate codes of every product, withdrawn
    ones included (D197, D207), but *exceptIds*: a ticker or a code belongs
    to one product, and a withdrawn product keeps its codes."""
    out = {}
    for r in conn.execute('SELECT * FROM products'):
        if r['productId'] in exceptIds:
            continue
        for kind, value in _identifiers(_record(r)):
            if kind == 'ticker' and r['withdrawnAt']:
                continue
            out.setdefault((kind, value.lower()), (value, r['productId'], r['name']))
    return out


def _refuseTaken(conn, record, held=None) -> None:
    """A ticker or a mandate code another product holds refuses the save,
    naming the listing's box and the product that holds it."""
    held = _identifiersHeld(conn, {record['productId']}) if held is None else held
    for n, listing in enumerate(record.get('listings') or []):
        for kind in ('code', 'ticker'):
            value = _text(listing.get(kind))
            taken = held.get((kind, value.lower())) if value else None
            if taken is not None:
                raise ValidationError('listing{}{}'.format(n, kind), 'The {} {} belongs to {} ({}). A {} '
                                      'is one product\'s.'.format(LABELS[kind].lower(), taken[0], taken[2],
                                                                  taken[1], LABELS[kind].lower()))


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
                top = _ticker(fields[key], key, strict=False) if key == 'ticker' else fields[key]
                given = _ticker(main.get(key), key, strict=False) if key == 'ticker' else main.get(key)
                if not _sameValue(top, given):
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
        # a ticker another live product took while it was withdrawn (D207)
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
        record = clean(body, _groupsOn(conn), strict=False)
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


def _keepStored(incoming, stored, found):
    """*incoming*, a product as a file gives it, with what the catalogue
    holds of it kept where the file has no column for it (D204, D207):
    (record, whether its listings were kept whole). A file without a
    Benchmark column leaves each listing's benchmark and one without a
    Hedging column the hedging - each listing matched to the one it is in
    the catalogue. The file's order is the listings' order (D214). One without a
    MandateCode column leaves an SMA whose listings carry codes listed as it
    is, since the file cannot say which row is which code; it takes what the
    product is from the file. A ValidationError says the listings kept are
    wrong for the product as the file gives it."""
    if 'MandateCode' not in found and isSma(incoming) and any(l.get('code') for l in stored['listings']):
        return clean(dict(incoming, listings=stored['listings']), strict=False), True
    missing = [f for f, column in (('benchmark', 'Benchmark'), ('hedging', 'Hedging')) if column not in found]
    if not missing:
        return incoming, False
    was, now = stored['listings'], [dict(l) for l in incoming['listings']]
    fields = [f for f in LISTING_KEY if f not in missing]
    for listing in now:
        match = next((l for l in was if not any(_text(l.get(f)).lower() != _text(listing.get(f)).lower()
                                                for f in fields)), None)
        if match is None and len(was) == 1 and len(now) == 1:
            match = was[0]
        if match is None:
            continue
        for f in missing:
            listing[f] = match.get(f) or ''
    return clean(dict(incoming, listings=now), strict=False), False


def _compare(conn, byId, order, fileName='', found=()) -> dict:
    """What a file would do to the catalogue on *conn*: each product the file
    gives with what it keeps of the one here (``_keepStored``), and refused
    when one is left wrong, or a ticker or a mandate code it gives is
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
        found = set()
        byId, order = _load(name, groups, strict=True, data=data, categories=known, found=found)
        out = _compare(conn, byId, order, name, found)
        out.update(file=name, rows=len(order), legacy=BASE_COLUMN in found,
                   ranksIgnored='Rank' in found,
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
            out.append([_cell(listing[key] if key in LISTING_FIELDS else p.get(key)) for key in KEYS])
    return out


def exportCsv() -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows(exportRows())
    return buf.getvalue()
