"""The product catalogue - the delivered table every sleeve selects from.

Read from the supplying database's extract the way the strategic universe is
(universe.py, D54): SCENARIO_PRODUCTS_SOURCE names a CSV or XLSX with one row
per product and one column per field -

    ProductId  Name  Ticker  AssetClass  Style  Vehicle  Source
    Liquidity  ExposureCurrency  ProductCost  FeeGroup
    DistributionYield  MinimumInvestment

- the last two optional in the file and None when absent (D63) - and the
packaged default is the stand-in under proposal-tool/productSource.
ProductId is the key sleeves reference (sleeveRepo.py). The catalogue is read
only here: it is what the delivering team sent, and it changes by delivery,
never through the service (D56).

A product served from here carries exactly the fields a sleeve product always
carried (spec 4.1 minus the management fee, D51) so nothing downstream - the
implementation table, the workbook, the fee resolver - can tell where it came
from. An empty ticker is served as the em dash the table has always shown.

Rejected outright, at load, with BadCatalogue: a missing column, a duplicate
id, an empty name, a non-numeric cost, or a fee group the card does not price.
The last one matters most - an unpriced group would otherwise fail at export,
under RDR, long after the product had been placed in a sleeve and chosen.
"""

from __future__ import annotations

import csv
import os
import threading

from .fees import FEE_GROUPS

_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', '..', '..', '..', 'productSource', 'products.csv')

COLUMNS = ['ProductId', 'Name', 'Ticker', 'AssetClass', 'Style', 'Vehicle', 'Source',
           'Liquidity', 'ExposureCurrency', 'ProductCost', 'FeeGroup']
# figures the catalogue view compares on (D63): served as None when the
# delivery does not carry the column or leaves the cell blank
OPTIONAL = ['DistributionYield', 'MinimumInvestment']

# the field names the rest of the package has always used for a product
FIELDS = ['name', 'ticker', 'assetClass', 'style', 'vehicle', 'source',
          'liquidity', 'exposureCurrency', 'productCost', 'feeGroup']

NO_TICKER = '—'


class BadCatalogue(ValueError):
    """The extract cannot be used as delivered."""


def sourcePath() -> str:
    return os.path.abspath(os.getenv('SCENARIO_PRODUCTS_SOURCE', '') or _DEFAULT)


_lock = threading.Lock()
_loaded = None          # (path, mtime, {id: product}, [ids in file order])


def _readRows(path: str):
    if path.lower().endswith('.csv'):
        with open(path, newline='', encoding='utf-8') as fh:
            reader = csv.reader(fh)
            head = next(reader, None) or []
            yield [str(h).strip() for h in head]
            for row in reader:
                if row and str(row[0]).strip():
                    yield row
        return
    from openpyxl import load_workbook
    sheet = load_workbook(path, read_only=True).active
    rows = sheet.iter_rows(values_only=True)
    head = next(rows, None) or []
    yield [str(h).strip() if h is not None else '' for h in head]
    for row in rows:
        if row and row[0] is not None and str(row[0]).strip():
            yield ['' if v is None else v for v in row]


def _load(path: str):
    rows = _readRows(path)
    head = next(rows)
    missing = [c for c in COLUMNS if c not in head]
    if missing:
        raise BadCatalogue('{}: missing column(s) {}'.format(path, ', '.join(missing)))
    index = {c: head.index(c) for c in COLUMNS}
    for c in OPTIONAL:
        if c in head:
            index[c] = head.index(c)
    byId, order = {}, []
    for row in rows:
        def cell(column):
            value = row[index[column]] if index[column] < len(row) else ''
            return '' if value is None else str(value).strip()
        pid = cell('ProductId')
        if pid in byId:
            raise BadCatalogue('{}: duplicate ProductId {!r}'.format(path, pid))
        name = cell('Name')
        if not name:
            raise BadCatalogue('{}: {} has an empty Name'.format(path, pid))
        try:
            cost = float(cell('ProductCost'))
        except ValueError:
            raise BadCatalogue('{}: {} ProductCost {!r} is not a number'.format(
                path, pid, cell('ProductCost')))
        if cost < 0:
            raise BadCatalogue('{}: {} ProductCost is negative'.format(path, pid))
        group = cell('FeeGroup')
        if group not in FEE_GROUPS:
            raise BadCatalogue('{}: {} fee group {!r} is not one of {}'.format(
                path, pid, group, FEE_GROUPS))

        def figure(column):
            if column not in index:
                return None
            raw = cell(column)
            if raw == '':
                return None
            try:
                value = float(raw)
            except ValueError:
                raise BadCatalogue('{}: {} {} {!r} is not a number'.format(path, pid, column, raw))
            if value < 0:
                raise BadCatalogue('{}: {} {} is negative'.format(path, pid, column))
            return value
        byId[pid] = {
            'productId': pid,
            'name': name,
            'ticker': cell('Ticker') or NO_TICKER,
            'assetClass': cell('AssetClass'),
            'style': cell('Style'),
            'vehicle': cell('Vehicle'),
            'source': cell('Source'),
            'liquidity': cell('Liquidity'),
            'exposureCurrency': cell('ExposureCurrency'),
            'productCost': cost,
            'feeGroup': group,
            'distributionYield': figure('DistributionYield'),
            'minimumInvestment': figure('MinimumInvestment'),
        }
        order.append(pid)
    return byId, order


def _get():
    """The catalogue, re-read when the file it came from changes on disk."""
    global _loaded
    path = sourcePath()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        raise BadCatalogue('{}: product catalogue not found'.format(path))
    with _lock:
        if _loaded is None or _loaded[0] != path or _loaded[1] != mtime:
            byId, order = _load(path)
            _loaded = (path, mtime, byId, order)
        return _loaded


def reload() -> None:
    global _loaded
    with _lock:
        _loaded = None


def all() -> list:                                # noqa: A001 - the catalogue, file order
    _, _, byId, order = _get()
    return [dict(byId[pid]) for pid in order]


def get(productId: str):
    """One product, or None. Callers that need a product to exist say so."""
    _, _, byId, _ = _get()
    entry = byId.get(productId)
    return dict(entry) if entry else None


def has(productId: str) -> bool:
    return get(productId) is not None


def describeSource() -> dict:
    path, mtime, byId, _ = _get()
    import datetime
    return {
        'path': path,
        'modified': datetime.datetime.fromtimestamp(mtime).isoformat(timespec='seconds'),
        'products': len(byId),
    }
