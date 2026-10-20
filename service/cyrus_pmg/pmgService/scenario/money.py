"""Amounts in a scenario's base currency (D181).

Every amount the tool prints - a mandate, a position's notional, a product's
minimum, a fee tier's bounds, a flat charge - is in the base currency of the
proposal it belongs to; nothing is converted. So each is printed with that
currency's mark: $ for USD, £ for GBP, € for EUR and CHF for the franc,
which has no symbol in common use. Where no proposal is in view - the fee
card or the catalogue in the console, which every currency shares - an
amount has no mark at all.

The sheet carries the same marks as number formats, and the deck renders
those formats back to text (sheetDoc.renderNumber), so the two agree.
"""

from __future__ import annotations

from .saaKeys import CURRENCIES

#: the mark each base currency is printed with
MARKS = {'USD': '$', 'GBP': '£', 'EUR': '€', 'CHF': 'CHF '}

#: the number format a whole amount is written in, per currency: USD's is
#: the one the sheet has always used; the others quote their mark, which
#: Excel takes as text
_FORMATS = {c: '"{}"#,##0'.format(MARKS[c]) for c in CURRENCIES}
_FORMATS['USD'] = '$#,##0'
#: an amount whose currency is not known
PLAIN_FORMAT = '#,##0'
FORMATS = tuple(_FORMATS.values()) + (PLAIN_FORMAT,)


#: what one unit of each is called, for the fee ladder's flat-rate row
UNITS = {'USD': 'dollar', 'GBP': 'pound', 'EUR': 'euro', 'CHF': 'franc'}


def mark(currency) -> str:
    """The mark for *currency*, or '' for none or one the tool does not price in."""
    return MARKS.get(currency or '', '')


def amount(value, currency, places=0) -> str:
    """A whole amount as text: '£250,000', '-$1,200', 'CHF 5,000'."""
    text = '{:,.{}f}'.format(abs(float(value)), places)
    return ('-' if float(value) < 0 else '') + mark(currency) + text


def numberFormat(currency) -> str:
    """The sheet's number format for a whole amount in *currency*."""
    return _FORMATS.get(currency or '', PLAIN_FORMAT)


def formatMark(fmt):
    """The mark an amount format prints, or None when *fmt* is not one."""
    for currency, known in _FORMATS.items():
        if fmt == known:
            return MARKS[currency]
    return '' if fmt == PLAIN_FORMAT else None
