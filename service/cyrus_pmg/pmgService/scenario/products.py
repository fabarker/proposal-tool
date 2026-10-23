"""The product catalogue - the table every sleeve selects from (D58), kept in
the repository's own database and maintained from the console (D165).

It used to be read, read only, from a delivered extract (D56): a CSV or XLSX
named by SCENARIO_PRODUCTS_SOURCE, one row per product, one column per field -

    ProductId  Name  Ticker  AssetClass  Category  Style  Vehicle  ShareClass
    Source  Liquidity  ExposureCurrency  IssueCurrency  ProductCost  FeeGroup
    DistributionYield  MinimumInvestment

- Category, ShareClass, IssueCurrency and the last two optional in the file
and blank or None when absent (D63, D81, D178, D184). A product may be listed
under a ticker of its own in each base currency, with a minimum, an exposure
currency and an issue currency of its own there (D180, D182, D184): the file gives it one row per base currency, the same
ProductId on each, the currency in an optional BaseCurrency column - All or
blank for the main row - and a name may end in its row's currency, '(GBP)'.
A product with no All row is offered only in the currencies it has rows for.
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

A separate account - an SMA, a product whose vehicle says so - carries its
SMA mandates beside its values by currency (D197): one mandate code for each
base currency it is offered in and, for a Public Equity SMA, for each of the
three hedging versions the account comes in there (Hedged, ISG Hedged,
Unhedged); an SMA in any other category has one mandate a currency and no
hedging version. A mandate may carry a product cost, a minimum and a
distribution yield of its own, which a scenario on that basis will read in
place of the product's (not yet: the catalogue holds them first). They come
from a mandate file of their own, one row per code, or from the product's
form, and a code is never given to two products. The word "mandate" on its
own is the client's mandate everywhere else in the tool, so on screen these
are always "SMA mandates".
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
OPTIONAL = ['Category', 'ShareClass', 'IssueCurrency', 'DistributionYield', 'MinimumInvestment']
#: The product's own category (D178), the sleeve category the catalogue bands
#: it under: a file may head the column with any of these.
CATEGORY_HEADERS = ('Category', 'Catagory', 'Group')
#: The currency the product is issued in (D184): a file may head the column
#: with any of these, lower-cased and stripped of all but letters and digits.
_ISSUE_HEADERS = ('issuecurrency', 'issueccy', 'issuercurrency', 'issuerccy')

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
]
KEYS = [k for k, _, _ in _FIELDS]
#: the file's columns in the order an export writes them - the packaged
#: file's own order
FILE_COLUMNS = [c for _, c, _ in _FIELDS]
LABELS = {k: label for k, _, label in _FIELDS}

#: What may differ with a scenario's base currency
#: (D180, D182, D184, D189, D190, D194): the ticker a product is listed under
#: there, the minimum it takes there, the currency it exposes a holder to
#: there, the currency that listing is issued in, its share class, its
#: distribution yield, its asset class and its product cost - a share class
#: hedged to each base currency is one product, an accumulating class
#: distributes nothing, and a hedged class costs more. Each is kept per
#: currency only where it differs from the main one, so a blank is the main
#: value - and a minimum or a yield of 0 is none, in a currency that has none.
CURRENCY_FIELDS = ('ticker', 'minimumInvestment', 'exposureCurrency', 'issueCurrency', 'shareClass',
                   'distributionYield', 'assetClass', 'productCost')
#: the ones of those that are figures: a blank is the main one, 0 is none
#: (a cost of 0 is a product that costs nothing there)
CURRENCY_FIGURES = ('minimumInvestment', 'distributionYield', 'productCost')
#: the ones of those that are words: a blank is the main one
CURRENCY_WORDS = ('exposureCurrency', 'issueCurrency', 'shareClass', 'assetClass')
#: A product may also not be offered at all in a base currency (D182): a
#: scenario there flags it in the implementation table, and the export
#: refuses while a sleeve holds it.
OFFERED = 'offered'
#: the flat key the console and a refusal name one currency's field by:
#: tickerGBP, minimumInvestmentGBP, exposureCurrencyGBP, issueCurrencyGBP, shareClassGBP,
#: distributionYieldGBP, assetClassGBP, productCostGBP, offeredGBP
CURRENCY_KEYS = [f + c for f in CURRENCY_FIELDS + (OFFERED,) for c in CURRENCIES]
#: A file gives a product's values in a currency as a row of their own
#: (D182): the same ProductId on one row per base currency, named in the
#: BaseCurrency column - USD, GBP, CHF, EUR, or All (or blank) for the main
#: row every currency without one of its own reads. A product with no All
#: row is offered in the currencies it has rows for and no others.
BASE_COLUMN = 'BaseCurrency'
#: what a file may head that column with, lower-cased and stripped of all but
#: letters and digits
_BASE_HEADERS = ('basecurrency', 'baseccy')
ALL = 'All'
#: what an export writes: the file's columns, then the row's base currency
EXPORT_COLUMNS = FILE_COLUMNS + [BASE_COLUMN]
LABELS.update({f + c: '{} in {}'.format(LABELS[f], c) for f in CURRENCY_FIELDS
               for c in CURRENCIES})
LABELS.update({OFFERED + c: 'Offered in {}'.format(c) for c in CURRENCIES})
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
#: the versions a Public Equity SMA's mandates come in, in the grid's order;
#: an SMA in any other category has no version, stored as ''
MANDATE_VERSIONS = ('Hedged', 'ISG Hedged', 'Unhedged')
#: what a mandate may carry of its own, read on its basis in place of the
#: product's figure there
MANDATE_FIGURES = ('productCost', 'minimumInvestment', 'distributionYield')
CODE_MAX = 40
#: the mandate file's columns (D197): one row per code, the first four and
#: Category required, the figures blank for none of its own. A row's Category
#: is the product's, checked against it, and fills a blank one.
MANDATE_COLUMNS = ['ProductId', 'MandateCode', 'BaseCurrency', 'Hedging', 'Category',
                   'ProductCost', 'MinimumInvestment', 'DistributionYield']
#: what a file may head each with, lower-cased and stripped of all but
#: letters and digits
_MANDATE_HEADERS = {
    'productid': 'productId', 'mandatecode': 'code', 'code': 'code', 'mandate': 'code',
    'basecurrency': 'currency', 'baseccy': 'currency', 'currency': 'currency', 'ccy': 'currency',
    'hedging': 'hedging', 'hedgingversion': 'hedging', 'version': 'hedging',
    'category': 'category', 'catagory': 'category', 'group': 'category',
    'productcost': 'productCost', 'cost': 'productCost',
    'minimuminvestment': 'minimumInvestment', 'minimum': 'minimumInvestment',
    'distributionyield': 'distributionYield', 'yield': 'distributionYield',
}
_MANDATE_REQUIRED = ('productId', 'code', 'currency', 'category')
LABELS.update({'mandates': 'SMA mandates', 'code': 'Mandate code', 'hedging': 'Hedging'})

ID_MAX = 80
NAME_MAX = 120
TICKER_MAX = 24
WORD_MAX = 60
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


def _load(path: str, feeGroups=None, strict=False, data: bytes = None, categories=None):
    """The extract at *path* (or the upload *data* named *path*) as
    ({id: record}, [ids in file order]). Refuses the whole file with
    BadCatalogue at the first bad row, naming the row and the product: a
    file is loaded whole or not at all. *feeGroups*, when given, is the set
    a product's group must belong to, and *categories* the list its category
    must be one of. *strict* is the check a save gets
    (``clean``); without it, what the tool has always accepted from a file."""
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
    for at, column in enumerate(head):
        if ''.join(ch for ch in column.lower() if ch.isalnum()) in _ISSUE_HEADERS:
            index['IssueCurrency'] = at
            break
    baseAt = next((at for at, column in enumerate(head)
                   if ''.join(ch for ch in column.lower() if ch.isalnum()) in _BASE_HEADERS), None)
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
            if currency != ALL and currency not in CURRENCIES:
                raise BadCatalogue('{}: row {} ({}): the {} {!r} is not one of {} or All.'.format(
                    name, n, pid or 'no ProductId', BASE_COLUMN, given, ', '.join(CURRENCIES)))
        if pid in rowsOf:
            if baseAt is None:
                raise BadCatalogue('{}: row {}: the ProductId {} is already on row {} - each product '
                                   'appears once, or once per base currency with a {} column.'.format(
                                       name, n, pid, rowsOf[pid][0][0], BASE_COLUMN))
            same = [m for m, c, _ in rowsOf[pid] if c == currency]
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
    byId = {pid: _fold(name, pid, rowsOf[pid], feeGroups, strict, categories) for pid in order}
    return byId, order


def _withoutCurrency(value, currency):
    """A name with its row's currency taken off the end: '(GBP)', ' - GBP'."""
    text = _text(value)
    for pattern in (r'\s*\(\s*{}\s*\)$', r'\s*[-–—]\s*{}$', r'\s+{}$'):
        cut = re.sub(pattern.format(currency), '', text, flags=re.IGNORECASE)
        if cut != text and cut.strip():
            return cut
    return text


def _fold(fileName, pid, rows, feeGroups, strict, categories):
    """One product from its rows in a file, one a base currency (D182): the
    All row is the main one, else the row of the first currency it is
    offered in; each other row gives the values it has in its currency,
    which may differ only in the ticker, the minimum, the exposure currency,
    the issue currency (D184), the share class (D189), the distribution
    yield (D190), the asset class and the product cost (D194). A row is the
    product whole, so a blank there is none: a minimum of 0, a ticker of
    none. With no All row, a currency without a
    row of its own is not offered."""
    if len(rows) == 1 and rows[0][1] == ALL:
        return rows[0][2]
    byCcy = {c: (n, r) for n, c, r in rows}
    first = ALL if ALL in byCcy else next(c for c in CURRENCIES if c in byCcy)
    mainRow, main = byCcy[first]
    for n, currency, record in rows:
        if record is main:
            continue
        for key in KEYS:
            if key == 'productId' or key in CURRENCY_FIELDS or _same(record[key], main[key]):
                continue
            raise BadCatalogue(
                '{}: row {} ({}): the {} row gives {} {} where row {} gives {} - only the ticker, the '
                'minimum, the exposure currency, the issue currency, the share class, the '
                'distribution yield, the asset class and the product cost may differ by base '
                'currency.'.format(
                    fileName, n, pid, currency, LABELS[key].lower(), show(key, record[key]),
                    mainRow, show(key, main[key])))
    currencies = {}
    for currency in CURRENCIES:
        if currency not in byCcy:
            if ALL not in byCcy:
                currencies[currency] = {OFFERED: False}
            continue
        record = byCcy[currency][1]
        if record is main:
            continue
        own = {}
        if record['ticker'] != main['ticker']:
            own['ticker'] = record['ticker'] or NO_TICKER
        for figure in CURRENCY_FIGURES:
            if not _same(record[figure], main[figure]):
                own[figure] = record[figure] or 0.0
        for word in CURRENCY_WORDS:
            if not _same(record[word], main[word]):
                own[word] = record[word]
        if own:
            currencies[currency] = own
    fields = dict(main, currencies=currencies)
    try:
        return clean(fields, feeGroups, strict=strict, categories=categories)
    except ValidationError as exc:
        raise BadCatalogue('{}: row {} ({}): {}'.format(fileName, mainRow, pid, exc.message))


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
    a category must be one of. A ticker is '' when there is none, and so is a
    category, and a share class None."""
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
    ticker = _ticker(fields.get('ticker'), 'ticker', strict)
    record = {'productId': pid, 'name': name, 'ticker': ticker}
    for key in WORDS:
        word = _text(fields.get(key))
        if strict and not word:
            raise ValidationError(key, '{} is needed.'.format(LABELS[key]))
        if strict and len(word) > WORD_MAX:
            raise ValidationError(key, 'Keep the {} to {} characters.'.format(
                LABELS[key].lower(), WORD_MAX))
        record[key] = word
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
    record['shareClass'] = _shareClass(_text(fields.get('shareClass')), 'shareClass') or None
    # the currency it is issued in (D184): optional, blank for not given
    issue = _text(fields.get('issueCurrency'))
    if strict and len(issue) > WORD_MAX:
        raise ValidationError('issueCurrency', 'Keep the issue currency to {} characters.'.format(
            WORD_MAX))
    record['issueCurrency'] = issue
    record['productCost'] = _figure(fields.get('productCost'), 'productCost', required=True,
                                    strict=strict)
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
    record['distributionYield'] = _figure(fields.get('distributionYield'), 'distributionYield',
                                          strict=strict)
    record['minimumInvestment'] = _figure(fields.get('minimumInvestment'), 'minimumInvestment',
                                          strict=strict)
    out = {k: record[k] for k in KEYS}
    out['currencies'] = _currencies(fields.get('currencies'), strict)
    _canonical(out)
    # the SMA mandates (D197), checked against the product as it now stands:
    # its vehicle, its category and the currencies it is offered in
    out['mandates'] = _mandates(fields.get('mandates'), out, strict)
    return out


def _canonical(record):
    """A product not offered everywhere takes its main values from the first
    currency it is offered in (D182), so the file it is exported to - one
    row per currency it is offered in - loads back as the same product.
    One offered nowhere is refused."""
    own = record['currencies']
    offered = [c for c in CURRENCIES if (own.get(c) or {}).get(OFFERED, True)]
    if not offered:
        raise ValidationError('offered' + CURRENCIES[0], 'Offer the product in at least one base '
                                                         'currency.')
    if len(offered) < len(CURRENCIES) and offered[0] in own:
        first = own.pop(offered[0])
        if first['ticker']:
            record['ticker'] = '' if first['ticker'] == NO_TICKER else first['ticker']
        for figure in CURRENCY_FIGURES:
            if first[figure] is not None:
                record[figure] = first[figure]
        for word in CURRENCY_WORDS:
            if first[word]:
                record[word] = first[word]
    # a value a currency has of its own that is the main one is no value of
    # its own: kept, it would read as one the next time the main one moved
    for currency in list(own):
        entry = own[currency]
        if entry['ticker'] and entry['ticker'] == (record['ticker'] or NO_TICKER):
            entry['ticker'] = ''
        for figure in CURRENCY_FIGURES:
            if entry[figure] is not None and _same(entry[figure], record[figure] or 0.0):
                entry[figure] = None
        for word in CURRENCY_WORDS:
            if entry[word] == (record[word] or ''):
                entry[word] = ''
        if not entry['ticker'] and not [f for f in CURRENCY_FIGURES if entry[f] is not None] \
                and not any(entry[word] for word in CURRENCY_WORDS) and entry[OFFERED]:
            del own[currency]


def _shareClass(text, key) -> str:
    """A share class as stored: one of the two words, its short form read as
    the word, or '' for none - in a currency, a blank is the main one."""
    shareClass = SHARE_CLASS_SHORT.get(text, text)
    if shareClass and shareClass not in SHARE_CLASSES:
        if key == 'shareClass':
            raise ValidationError(key, 'The share class {!r} is not one of {}, or blank for '
                                       'none.'.format(shareClass, ', '.join(SHARE_CLASSES)))
        raise ValidationError(key, 'The {} {!r} is not one of {}, or blank for the main '
                                   'one.'.format(LABELS[key][0].lower() + LABELS[key][1:],
                                                 shareClass, ', '.join(SHARE_CLASSES)))
    return shareClass


def _ticker(value, key, strict=True) -> str:
    """A ticker as stored: '' for none, however a file wrote none."""
    ticker = _text(value)
    if ticker in (NO_TICKER, '-', '–'):
        ticker = ''
    if strict and len(ticker) > TICKER_MAX:
        raise ValidationError(key, 'Keep the {} to {} characters.'.format(
            LABELS[key][0].lower() + LABELS[key][1:], TICKER_MAX))
    return ticker


def _currencies(value, strict=True) -> dict:
    """The values by currency (D180, D182, D184, D189, D190, D194) as stored: {currency:
    {'ticker', 'minimumInvestment', 'distributionYield', 'productCost', 'exposureCurrency',
    'issueCurrency', 'shareClass', 'assetClass', 'offered'}} for each currency
    that sets one or is not offered, in the vocabulary's order. A ticker of
    a dash is none there, whatever the main one; a currency not offered
    keeps no values. A refusal names the one field."""
    if value is None or value == '' or value == {}:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValidationError('currencies', 'The values by currency could not be read.')
    if not isinstance(value, dict):
        raise ValidationError('currencies', 'The values by currency are a table, one per currency.')
    for currency in value:
        if currency not in CURRENCIES:
            raise ValidationError('currencies', '{!r} is not a base currency: {}.'.format(
                currency, ', '.join(CURRENCIES)))
    out = {}
    for currency in CURRENCIES:
        given = value.get(currency) or {}
        if not isinstance(given, dict):
            raise ValidationError('currencies', 'The {} values are a ticker, a minimum, an exposure '
                                                'currency, an issue currency, a share class, a '
                                                'distribution yield, an asset class, a product cost '
                                                'and whether it is offered.'.format(currency))
        offered = given.get(OFFERED, True)
        if not isinstance(offered, bool):
            raise ValidationError(OFFERED + currency, 'Whether it is offered in {} is yes or '
                                                      'no.'.format(currency))
        if not offered:
            out[currency] = dict({'ticker': '', OFFERED: False},
                                 **{figure: None for figure in CURRENCY_FIGURES},
                                 **{word: '' for word in CURRENCY_WORDS})
            continue
        none = _text(given.get('ticker')) in (NO_TICKER, '-', '–')
        ticker = NO_TICKER if none else _ticker(given.get('ticker'), 'ticker' + currency, strict)
        figures = {}
        for figure in CURRENCY_FIGURES:
            try:
                figures[figure] = _figure(given.get(figure), figure, strict=strict)
            except ValidationError as exc:
                raise ValidationError(figure + currency, exc.message.replace(
                    LABELS[figure], LABELS[figure + currency], 1))
        words = {}
        for word in CURRENCY_WORDS:
            words[word] = _text(given.get(word))
            if word == 'shareClass':
                words[word] = _shareClass(words[word], word + currency)
            elif strict and len(words[word]) > WORD_MAX:
                raise ValidationError(word + currency, 'Keep the {} to {} characters.'.format(
                    LABELS[word + currency][0].lower() + LABELS[word + currency][1:], WORD_MAX))
        if ticker or any(v is not None for v in figures.values()) or any(words.values()):
            out[currency] = dict({'ticker': ticker, OFFERED: True}, **figures, **words)
    return out


def isSma(product) -> bool:
    """Whether *product* is a separate account (D197): its vehicle says so."""
    return _text((product or {}).get('vehicle')).lower() in SMA_VEHICLES


def _mandates(value, record, strict=True) -> list:
    """The SMA mandates (D197) as stored: [{code, currency, hedging,
    productCost, minimumInvestment, distributionYield}] in the grid's order
    - by base currency, then by version - from a list, or its JSON. Each is
    checked as a product's figures are, and the list against *record*: only
    an SMA has any; a Public Equity SMA's each name a version and any other
    SMA's none; a currency is one the product is offered in; and no code or
    cell is on the product twice. A refusal names the row's box
    (mandate0code, mandate2hedging...) or, for the list, ``mandates``."""
    if value is None or value == '' or value == []:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValidationError('mandates', 'The SMA mandates could not be read.')
    # (all() here is the catalogue, not the builtin)
    if not isinstance(value, list) or any(not isinstance(m, dict) for m in value):
        raise ValidationError('mandates', 'The SMA mandates are a list, one a mandate code.')
    name = record.get('name') or record.get('productId') or 'the product'
    if value and not isSma(record):
        raise ValidationError('mandates', 'Only a separate account (an SMA) has SMA mandates: '
                                          '{} is {}.'.format(name, _an(record.get('vehicle'))))
    equity = record.get('category') == EQUITY_CATEGORY
    offered = offeredIn(record)
    out, codes, cells = [], {}, {}
    for n, given in enumerate(value):
        box = 'mandate{}'.format(n)
        code = _text(given.get('code'))
        if not code:
            raise ValidationError(box + 'code', 'Give the SMA mandate its code.')
        if strict and len(code) > CODE_MAX:
            raise ValidationError(box + 'code', 'Keep the mandate code to {} characters.'.format(
                CODE_MAX))
        if code.lower() in codes:
            raise ValidationError(box + 'code', 'The mandate code {} is on {} twice.'.format(
                code, name))
        codes[code.lower()] = code
        currency = _text(given.get('currency')).upper()
        if currency not in CURRENCIES:
            raise ValidationError(box + 'currency', 'The base currency of {} is one of {}.'.format(
                code, ', '.join(CURRENCIES)))
        if currency not in offered:
            raise ValidationError(box + 'currency', 'The SMA mandate {} is for {}, where {} is not '
                                                    'offered.'.format(code, currency, name))
        hedging = _text(given.get('hedging'))
        if equity:
            if not hedging:
                raise ValidationError(box + 'hedging', 'The SMA mandate {} needs its hedging version: '
                                      '{} is {}, whose SMAs come as {}.'.format(
                                          code, name, EQUITY_CATEGORY, ', '.join(MANDATE_VERSIONS)))
            if hedging not in MANDATE_VERSIONS:
                same = [v for v in MANDATE_VERSIONS if v.lower() == hedging.lower()]
                if same:
                    raise ValidationError(box + 'hedging', 'The hedging version {!r} is spelled '
                                                           '{}.'.format(hedging, same[0]))
                raise ValidationError(box + 'hedging', 'The hedging version of {} is one of {}.'.format(
                    code, ', '.join(MANDATE_VERSIONS)))
        elif hedging:
            raise ValidationError(box + 'hedging', 'Only a {} SMA has hedging versions: {} is {}, so '
                                  'leave the hedging of {} blank.'.format(
                                      EQUITY_CATEGORY, name,
                                      record.get('category') or 'in no category', code))
        cell = (currency, hedging)
        if cell in cells:
            raise ValidationError(box + 'code', 'Two SMA mandates for {}: {} and {}. One code a '
                                  'cell.'.format(_cellWords(currency, hedging), cells[cell], code))
        cells[cell] = code
        figures = {}
        for figure in MANDATE_FIGURES:
            try:
                figures[figure] = _figure(given.get(figure), figure, strict=strict)
            except ValidationError as exc:
                raise ValidationError(box + figure, exc.message.replace(
                    LABELS[figure], LABELS[figure] + ' of ' + code, 1))
        out.append(dict({'code': code, 'currency': currency, 'hedging': hedging}, **figures))
    out.sort(key=lambda m: (CURRENCIES.index(m['currency']),
                            MANDATE_VERSIONS.index(m['hedging']) if m['hedging'] else -1))
    return out


def _an(word) -> str:
    """'an ETF', 'a Mutual Fund', 'of no vehicle'."""
    word = _text(word)
    if not word:
        return 'of no vehicle'
    return ('an ' if word[0].lower() in 'aeiou' else 'a ') + word


def _cellWords(currency, hedging) -> str:
    return currency + (' · ' + hedging if hedging else '')


def showMandate(mandate) -> str:
    """One SMA mandate as a history line or a preview shows it: its cell,
    then the figures it carries of its own."""
    if mandate is None:
        return '—'
    bits = [_cellWords(mandate['currency'], mandate['hedging'])]
    for figure, word in (('productCost', 'cost'), ('minimumInvestment', 'min'),
                         ('distributionYield', 'yield')):
        if mandate.get(figure) is not None:
            bits.append('{} {}'.format(word, show(figure, mandate[figure])))
    return ' · '.join(bits)


def _sameMandate(a, b) -> bool:
    return not any(not _same(a.get(k), b.get(k))
                   for k in ('code', 'currency', 'hedging') + MANDATE_FIGURES)


def mandateFor(product, currency, hedging, version=None):
    """The SMA mandate of *product* a scenario in *currency* with the
    hedging policy *hedging* holds (D197), or None where it has none there.
    An SMA whose mandates come in versions takes the version the policy
    names - Equity Not Hedged leaves equity unhedged, so it takes Unhedged -
    unless *version* fixes one; an SMA with no versions takes its one
    mandate in the currency, whatever the policy."""
    held = (product or {}).get('mandates') or []
    if not held:
        return None
    versioned = any(m['hedging'] for m in held)
    if versioned:
        wanted = version or (hedging if hedging in MANDATE_VERSIONS else MANDATE_VERSIONS[-1])
    else:
        wanted = ''
    return next((dict(m) for m in held if m['currency'] == currency and m['hedging'] == wanted),
                None)


def inCurrency(product, currency):
    """*product* as a scenario in *currency* reads it
    (D180, D182, D184, D189, D190, D194): the ticker, the minimum, the
    exposure currency, the issue currency, the share class, the distribution
    yield, the asset class and the product cost it has there, where it has
    its own, else its main ones; and whether it is offered there at all. Any
    other currency, or none, reads the main ones, offered."""
    if not product:
        return product
    out = dict(product)
    own = (product.get('currencies') or {}).get(currency) or {}
    if own.get('ticker'):
        out['ticker'] = own['ticker']
    for figure in CURRENCY_FIGURES:
        if own.get(figure) is not None:
            out[figure] = own[figure]
    for word in CURRENCY_WORDS:
        if own.get(word):
            out[word] = own[word]
    out[OFFERED] = own.get(OFFERED, True)
    # a sleeve's product carries the one held instead below its minimum
    # (D192), read in the same currency
    if isinstance(product.get('alternative'), dict):
        out['alternative'] = inCurrency(product['alternative'], currency)
    return out


def offeredIn(product) -> list:
    """The base currencies *product* is offered in (D182)."""
    own = (product or {}).get('currencies') or {}
    return [c for c in CURRENCIES if (own.get(c) or {}).get(OFFERED, True)]


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


def changes(before, after) -> list:
    """Field by field, what moved between two records: [{field, label,
    before, after}] in the order the console shows the fields."""
    if before is None:
        return []
    out = []
    for key in KEYS[1:]:
        was, now = before.get(key), after.get(key)
        if isinstance(was, float) or isinstance(now, float):
            if was is not None and now is not None and abs(was - now) <= 1e-12:
                continue
        if was == now or (was in (None, '') and now in (None, '')):
            continue
        out.append({'field': key, 'label': LABELS[key], 'before': show(key, was),
                    'after': show(key, now)})
    for field in CURRENCY_FIELDS + (OFFERED,):
        for currency in CURRENCIES:
            was = ((before.get('currencies') or {}).get(currency) or {}).get(field)
            now = ((after.get('currencies') or {}).get(currency) or {}).get(field)
            if field == OFFERED:
                was, now = was is not False, now is not False
                if was == now:
                    continue
                key = field + currency
                out.append({'field': key, 'label': LABELS[key], 'before': 'yes' if was else 'no',
                            'after': 'yes' if now else 'no'})
                continue
            if was in (None, '') and now in (None, ''):
                continue
            if was is not None and now is not None and field in CURRENCY_FIGURES \
                    and abs(float(was) - float(now)) <= 1e-12:
                continue
            if was == now:
                continue
            key = field + currency
            out.append({'field': key, 'label': LABELS[key], 'before': _showOwn(field, was),
                        'after': _showOwn(field, now)})
    # the SMA mandates (D197), one line a code that came, went or moved
    was = {m['code']: m for m in before.get('mandates') or []}
    now = {m['code']: m for m in after.get('mandates') or []}
    for code in list(was) + [c for c in now if c not in was]:
        a, b = was.get(code), now.get(code)
        if a is not None and b is not None and _sameMandate(a, b):
            continue
        out.append({'field': 'mandate:' + code, 'label': 'SMA mandate ' + code,
                    'before': showMandate(a), 'after': showMandate(b)})
    return out


def _showOwn(field, value) -> str:
    """A currency's own value as a history line shows it: a dash ticker is none."""
    return 'none' if field == 'ticker' and value == NO_TICKER else show(field, value)


def changeWords(entries) -> list:
    """The changes as the lines a history or the feed prints."""
    return ['{} {} → {}'.format(c['label'], c['before'], c['after']) for c in entries]


# ---------------------------------------------------------- the database ---

_lock = threading.Lock()
_cache = None            # (stamp, catalogue)
_seedProblem = None      # why the first load could not be made, if it could not


def _record(row) -> dict:
    """A stored row as the record ``clean`` returns."""
    return {
        'productId': row['productId'], 'name': row['name'], 'ticker': row['ticker'] or '',
        'assetClass': row['assetClass'], 'category': row['category'] or '',
        'style': row['style'], 'vehicle': row['vehicle'], 'shareClass': row['shareClass'] or None, 'source': row['source'],
        'liquidity': row['liquidity'], 'exposureCurrency': row['exposureCurrency'],
        'issueCurrency': (row['issueCurrency'] if 'issueCurrency' in row.keys() else '') or '',
        'productCost': float(row['productCost']), 'feeGroup': row['feeGroup'],
        'distributionYield': None if row['distributionYield'] is None else float(row['distributionYield']),
        'minimumInvestment': None if row['minimumInvestment'] is None else float(row['minimumInvestment']),
        'currencies': _storedCurrencies(row),
        'mandates': _storedMandates(row),
    }


def _storedMandates(row) -> list:
    """The SMA mandates as stored (D197): read back without the checks, which
    were made on the way in and would need the product whole."""
    text = row['mandates'] if 'mandates' in row.keys() else ''
    return json.loads(text) if text else []


def _mandateText(record) -> str:
    return json.dumps(record.get('mandates') or []) if record.get('mandates') else ''


def _storedCurrencies(row) -> dict:
    text = row['currencies'] if 'currencies' in row.keys() else ''
    # read through the check, unstrict, so one stored before a field joined
    # them (D182) has the shape of one stored since
    return _currencies(text, strict=False) if text else {}


def _currencyText(record) -> str:
    return json.dumps(record.get('currencies') or {}, sort_keys=True) if record.get('currencies') else ''


def _served(record) -> dict:
    """A record in the shape every consumer has always read. The product's
    own category is the catalogue's (D178): a sleeve product's category is
    its sleeve's, so it is not served beside it."""
    out = dict(record)
    out['ticker'] = record['ticker'] or NO_TICKER
    out['currencies'] = {c: dict(v) for c, v in (record.get('currencies') or {}).items()}
    out['mandates'] = [dict(m) for m in record.get('mandates') or []]
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
    live product already uses, in A-Z order, its values by currency too - with
    the share classes."""
    cat = _catalogue()
    out = {}
    for key in WORDS:
        seen = {p[key] for p in cat['live'].values() if p[key]}
        if key in CURRENCY_WORDS:
            seen.update(own[key] for p in cat['live'].values()
                        for own in (p.get('currencies') or {}).values() if own.get(key))
        out[key] = sorted(seen, key=lambda v: v.lower())
    out['shareClass'] = list(SHARE_CLASSES)
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
        'feeGroup, distributionYield, minimumInvestment, currencies, mandates, revision, createdBy, '
        'createdAt, updatedBy, updatedAt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '
        '?, ?, ?, 1, ?, ?, ?, ?)',
        (record['productId'], position, record['name'], record['ticker'], record['assetClass'],
         record.get('category') or '', record['style'], record['vehicle'], record['shareClass'] or '', record['source'],
         record['liquidity'], record['exposureCurrency'], record.get('issueCurrency') or '',
         record['productCost'], record['feeGroup'], record['distributionYield'],
         record['minimumInvestment'], _currencyText(record), _mandateText(record), user, stamp,
         user, stamp))


def _update(conn, record, revision, user, stamp) -> None:
    conn.execute(
        'UPDATE products SET name = ?, ticker = ?, assetClass = ?, category = ?, style = ?, '
        'vehicle = ?, shareClass = ?, source = ?, liquidity = ?, exposureCurrency = ?, '
        'issueCurrency = ?, productCost = ?, feeGroup = ?, distributionYield = ?, minimumInvestment = ?, '
        'currencies = ?, mandates = ?, revision = ?, updatedBy = ?, updatedAt = ? WHERE productId = ?',
        (record['name'], record['ticker'], record['assetClass'], record.get('category') or '',
         record['style'],
         record['vehicle'], record['shareClass'] or '', record['source'], record['liquidity'],
         record['exposureCurrency'], record.get('issueCurrency') or '', record['productCost'],
         record['feeGroup'], record['distributionYield'], record['minimumInvestment'],
         _currencyText(record), _mandateText(record), revision, user, stamp, record['productId']))


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


def _codesHeld(conn, exceptId=None) -> dict:
    """{code, lower-cased: (code, productId, name)} for every SMA mandate on
    any other product than *exceptId*, withdrawn ones included (D197): a
    code belongs to one product, and a withdrawn product keeps its own."""
    out = {}
    for r in conn.execute("SELECT productId, name, mandates FROM products WHERE mandates != ''"):
        if r['productId'] == exceptId:
            continue
        for m in json.loads(r['mandates']):
            out[m['code'].lower()] = (m['code'], r['productId'], r['name'])
    return out


def _refuseTakenCodes(conn, record) -> None:
    """A mandate code another product holds refuses the save, naming it."""
    if not record.get('mandates'):
        return
    held = _codesHeld(conn, record['productId'])
    for n, m in enumerate(record['mandates']):
        taken = held.get(m['code'].lower())
        if taken is not None:
            raise ValidationError('mandate{}code'.format(n), 'The SMA mandate code {} belongs to {} '
                                  '({}). A code is one product\'s.'.format(taken[0], taken[2], taken[1]))


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
        _refuseTakenCodes(conn, record)
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
        if 'mandates' not in fields:
            # a save that says nothing of the SMA mandates keeps them
            fields['mandates'] = _record(row)['mandates']
        record = clean(fields, _groupsOn(conn), strict=True, categories=known)
        if not changes(_record(row), record):
            raise ValidationError('product', 'Nothing has changed: these are the values in force.')
        _refuseTakenCodes(conn, record)
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
        # a version from before the SMA mandates (D197) keeps the ones in force
        body.setdefault('mandates', _record(row)['mandates'])
        record = clean(body, _groupsOn(conn), strict=False)
        if not changes(_record(row), record):
            raise ValidationError('revision', 'Those are the values in force already.')
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


def _compare(conn, byId, order, fileName='') -> dict:
    """What a file would do to the catalogue on *conn*. A product file says
    nothing of the SMA mandates (D197), so a product already here keeps its
    own - checked again against the product as the file gives it, since a
    row that moves an SMA out of Public Equity, or makes it another vehicle,
    leaves them wrong, and is refused naming the product."""
    rows = {r['productId']: r for r in conn.execute('SELECT * FROM products ORDER BY position')}
    added, changed, back, unchanged = [], [], [], 0
    for pid in order:
        incoming, row = byId[pid], rows.get(pid)
        if row is None:
            added.append({'productId': pid, 'name': incoming['name']})
            continue
        kept = _storedMandates(row)
        if kept:
            try:
                incoming['mandates'] = _mandates(kept, incoming)
            except ValidationError as exc:
                raise BadCatalogue('{}: {} ({}) keeps its SMA mandates, which the file leaves wrong: '
                                   '{}'.format(fileName, incoming['name'], pid, exc.message))
        moved = changes(_record(row), incoming)
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
            'missing': missing}


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
        byId, order = _load(name, groups, strict=True, data=data, categories=known)
        out = _compare(conn, byId, order, name)
        out.update(file=name, rows=len(order),
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
        byId, order = _load(name, _groupsOn(conn), strict=True, data=data, categories=known)
        plan = _compare(conn, byId, order, name)
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
    """The live catalogue as the file it can be loaded from again: the header
    and, in catalogue order, each product's rows - one for All and one for
    each currency with values of its own, or, for a product not offered
    everywhere, one for each currency it is offered in (D182). A row is the
    product whole as that currency reads it."""
    out = [list(EXPORT_COLUMNS)]

    def row(p, currency):
        values = inCurrency(p, None if currency == ALL else currency)
        return [_cell('' if key == 'ticker' and values[key] == NO_TICKER else values[key])
                for key in KEYS] + [currency]
    for p in entries():
        own = p.get('currencies') or {}
        offered = offeredIn(p)
        if len(offered) == len(CURRENCIES):
            out.append(row(p, ALL))
            out.extend(row(p, c) for c in CURRENCIES if c in own)
        else:
            out.extend(row(p, c) for c in offered)
    return out


def exportCsv() -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows(exportRows())
    return buf.getvalue()


# ------------------------------------------------- the SMA mandate file ---
# One row per mandate code (D197), each naming its SMA by ProductId. A file
# adds codes and changes the ones it names, by code; it never takes one off
# - that is done on the product's card. Previewed, product by product, before
# anything moves, and refused whole at the first bad row.

_MANDATE_COLUMN_OF = dict(zip(('productId', 'code', 'currency', 'hedging', 'category') + MANDATE_FIGURES,
                              MANDATE_COLUMNS))


def _mandateFileName(name) -> str:
    name = os.path.basename(str(name or '').replace('\\', '/')).strip()
    if not name.lower().endswith(('.csv', '.xlsx')):
        raise ValidationError('file', 'An SMA mandate file is a .csv or an .xlsx.')
    return name


def _loadMandates(name, data, conn, categories):
    """The mandate file *data* named *name* read against the catalogue on
    *conn*: ({productId: record}, [ids in file order], rows) - each product
    as it would stand, its mandates merged by code with the file's rows and
    its category filled where it was blank. Refused whole with BadCatalogue
    at the first bad row, naming the row and the product."""
    rows = _readRows(name, data, sheet='mandates')
    head = next(rows)
    index = {}
    for at, column in enumerate(head):
        key = ''.join(ch for ch in column.lower() if ch.isalnum())
        if not key:
            continue
        field = _MANDATE_HEADERS.get(key)
        if field is None:
            raise BadCatalogue('{}: the column {!r} is not one of the mandate file\'s: {}.'.format(
                name, column, ', '.join(MANDATE_COLUMNS)))
        if field in index:
            raise BadCatalogue('{}: the column {} is there twice.'.format(
                name, _MANDATE_COLUMN_OF[field]))
        index[field] = at
    missing = [_MANDATE_COLUMN_OF[f] for f in _MANDATE_REQUIRED if f not in index]
    if missing:
        raise BadCatalogue('{}: missing column(s) {}'.format(name, ', '.join(missing)))
    live = {r['productId']: r for r in conn.execute('SELECT * FROM products')}
    held = _codesHeld(conn)
    touched, order, seen, count = {}, [], {}, 0
    for n, row in enumerate(rows, start=2):
        count += 1

        def cell(field):
            at = index.get(field)
            return row[at] if at is not None and at < len(row) else ''
        pid = _text(cell('productId'), keepSpaces=True)
        where = '{}: row {} ({})'.format(name, n, pid or 'no ProductId')
        if not pid:
            raise BadCatalogue(where + ': give the ProductId of the SMA.')
        r = live.get(pid)
        if r is None:
            raise BadCatalogue('{}: there is no product {} in the catalogue.'.format(where, pid))
        if r['withdrawnAt']:
            raise BadCatalogue('{}: {} was withdrawn on {}. Restore it first.'.format(
                where, r['name'], r['withdrawnAt'][:10]))
        record = touched[pid]['record'] if pid in touched else _record(r)
        if not isSma(record):
            raise BadCatalogue('{}: {} is not a separate account: it is {}. Only an SMA has '
                               'mandates.'.format(where, record['name'], _an(record['vehicle'])))
        category = _text(cell('category'))
        if not category:
            raise BadCatalogue(where + ': give the Category, the product\'s own.')
        if category not in categories:
            same = [c for c in categories if c.lower() == category.lower()]
            raise BadCatalogue('{}: the Category {!r} is {}.'.format(
                where, category, 'spelled ' + same[0] if same
                else 'not one the sleeves are built for: ' + ', '.join(categories)))
        if pid not in touched:
            if record['category'] and record['category'] != category:
                raise BadCatalogue('{}: the Category {} is not {}\'s, which is {}.'.format(
                    where, category, record['name'], record['category']))
            touched[pid] = {'record': dict(record, category=category), 'given': {}, 'category': category}
            order.append(pid)
        elif touched[pid]['category'] != category:
            raise BadCatalogue('{}: the Category {} differs from the product\'s other rows, which '
                               'say {}.'.format(where, category, touched[pid]['category']))
        code = _text(cell('code'))
        if not code:
            raise BadCatalogue(where + ': give the MandateCode.')
        if code.lower() in seen:
            raise BadCatalogue('{}: the code {} is already on row {}.'.format(
                where, code, seen[code.lower()]))
        seen[code.lower()] = n
        taken = held.get(code.lower())
        if taken is not None and taken[1] != pid:
            raise BadCatalogue('{}: the code {} belongs to {} ({}). A code is one product\'s.'.format(
                where, taken[0], taken[2], taken[1]))
        mandate = {'code': code, 'currency': _text(cell('currency')).upper(),
                   'hedging': _text(cell('hedging'))}
        for figure in MANDATE_FIGURES:
            mandate[figure] = cell(figure)
        touched[pid]['given'][code.lower()] = (n, mandate)
    out = {}
    for pid in order:
        entry = touched[pid]
        record, given = entry['record'], entry['given']
        # the file's row replaces the mandate with its code; the rest stay
        merged, rowsOf = [], []
        for m in record['mandates']:
            at = given.get(m['code'].lower())
            merged.append(at[1] if at else m)
            rowsOf.append(at[0] if at else None)
        for c, (n, m) in given.items():
            if not any(m['code'].lower() == c for m in record['mandates']):
                merged.append(m)
                rowsOf.append(n)
        # a new code for a cell another code already holds: one code a cell
        cells = {(m['currency'], m['hedging']): m['code'] for m in record['mandates']}
        for c, (n, m) in given.items():
            at = cells.get((m['currency'], m['hedging']))
            if at is not None and at.lower() != c:
                raise BadCatalogue('{}: row {} ({}): {} already has the code {}; one code a cell. '
                                   'Take it off the product\'s card first.'.format(
                                       name, n, pid, _cellWords(m['currency'], m['hedging']), at))
        try:
            record['mandates'] = _mandates(merged, record, strict=True)
        except ValidationError as exc:
            box = re.match(r'mandate(\d+)', exc.field)
            n = rowsOf[int(box.group(1))] if box and int(box.group(1)) < len(rowsOf) else None
            raise BadCatalogue('{}: {}({}): {}'.format(name, 'row {} '.format(n) if n else '', pid,
                                                      exc.message))
        out[pid] = record
    return out, order, count


def _compareMandates(conn, byId, order) -> dict:
    """What a mandate file would do, product by product."""
    products, touched, added, changed = [], 0, 0, 0
    for pid in order:
        before, after = _record(_row(conn, pid)), byId[pid]
        moved = changes(before, after)
        was = {m['code'] for m in before['mandates']}
        new = [m['code'] for m in after['mandates'] if m['code'] not in was]
        moving = [c['field'][8:] for c in moved
                  if c['field'].startswith('mandate:') and c['field'][8:] in was]
        products.append({
            'productId': pid, 'name': after['name'], 'added': new, 'changed': moving,
            'unchanged': len(after['mandates']) - len(new) - len(moving),
            'category': after['category'] if before['category'] != after['category'] else '',
            'same': not moved, 'changes': moved, 'changeWords': changeWords(moved)})
        if moved:
            touched += 1
        added += len(new)
        changed += len(moving)
    return {'products': products, 'touched': touched, 'same': len(order) - touched,
            'added': added, 'changed': changed}


def mandatePreview(name, content) -> dict:
    """What importing an SMA mandate file would do, without doing it (D197):
    for each product it names, the codes it adds, the ones it changes, how
    many it leaves as they are, and the category it fills in. A file with
    any bad row is refused whole, naming the row. *revision* is the
    catalogue version the preview was made against."""
    from . import sleeveRepo
    name = _mandateFileName(name)
    data = _decode(content)
    known = knownCategories()
    conn = sleeveRepo.connection()
    try:
        byId, order, rows = _loadMandates(name, data, conn, known)
        out = _compareMandates(conn, byId, order)
        out.update(file=name, rows=rows,
                   revision=int(_meta(conn, 'catalogueRevision', '0') or 0))
        return out
    finally:
        conn.close()


def importMandates(name, content, user='', note='', base=None) -> dict:
    """Apply an SMA mandate file: each product it changes gets one revision
    marked *imported*, naming the file; the products it leaves as they are
    are not touched. All or nothing, and refused as STALE when *base* is not
    the catalogue version the preview showed."""
    name = _mandateFileName(name)
    data = _decode(content)
    note = _note(note)
    base = _base(base)
    known = knownCategories()

    def work(conn):
        _refuseStaleCatalogue(conn, base)
        byId, order, _ = _loadMandates(name, data, conn, known)
        plan = _compareMandates(conn, byId, order)
        if not plan['touched']:
            raise ValidationError('file', 'The file matches the catalogue: every SMA mandate in it '
                                          'is there already.')
        stamp = _now()
        for entry in plan['products']:
            if entry['same']:
                continue
            row = _row(conn, entry['productId'])
            record = byId[entry['productId']]
            revision = row['revision'] + 1
            _update(conn, record, revision, user or '', stamp)
            _history(conn, entry['productId'], revision, 'imported', record, user or '', stamp,
                     note=note, source=name)
        revision = _bump(conn)
        return {'file': name, 'products': plan['touched'], 'same': plan['same'],
                'added': plan['added'], 'changed': plan['changed'], 'revision': revision}

    return _writing(work)


def mandateRows() -> list:
    """The live catalogue's SMA mandates as the file they can be loaded from
    again: the header, then one row per code in catalogue order."""
    out = [list(MANDATE_COLUMNS)]
    for p in entries():
        for m in p.get('mandates') or []:
            out.append([p['productId'], m['code'], m['currency'], m['hedging'], p['category']]
                       + [_cell(m.get(figure)) for figure in MANDATE_FIGURES])
    return out
