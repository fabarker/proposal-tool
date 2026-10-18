"""The charges a proposal quotes beside its fees (D177).

The Fees Summary slide quotes three charges the fee card does not hold: the
foreign exchange commissions, by trade size; the charge on buying or selling
equities and ETFs; and the annual custody fee. A PWA sets them at their
discretion, so they are the proposal's, not the house's: entered on the
scenario, beside the fee schedule and level, saved with it, carried into
the export and recorded in the register with the proposal.

The FX trade sizes are the house's, not the PWA's: trades over $5M and under
$1M, fixed, shown on the form greyed out. The custody fee is $5,000 unless the
PWA changes it - to $0 for a client in GSBZ Custody, say. The commissions are
the PWA's to enter. Any may be left unset while the proposal is being built,
but a priced proposal is not delivered until every one is set: the page asks
for them when Download is pressed, and the export refuses them missing.
"""

from __future__ import annotations

from numbers import Real

from .types import ValidationError

#: each charge: its id (the template's field), what the form calls it, its
#: unit - US dollars or basis points - and the most it may be; a ``fixed``
#: one is the house's and cannot be changed, a ``default`` one stands until
#: the PWA changes it, and ``zero`` is what the form's button to set it to
#: nothing says
FIELDS = (
    {'id': 'fxHighEdge', 'group': 'Foreign exchange commissions',
     'label': 'Trades over', 'unit': 'usd', 'max': 1e9, 'fixed': 5e6},
    {'id': 'fxHighBps', 'group': 'Foreign exchange commissions',
     'label': 'Commission on trades over that', 'unit': 'bps', 'max': 500.0},
    {'id': 'fxMidBps', 'group': 'Foreign exchange commissions',
     'label': 'Commission on trades between the two', 'unit': 'bps', 'max': 500.0},
    {'id': 'fxLowEdge', 'group': 'Foreign exchange commissions',
     'label': 'Trades under', 'unit': 'usd', 'max': 1e9, 'fixed': 1e6},
    {'id': 'fxLowBps', 'group': 'Foreign exchange commissions',
     'label': 'Commission on trades under that', 'unit': 'bps', 'max': 500.0},
    {'id': 'equityTradeBps', 'group': 'Transaction fees',
     'label': 'Buying or selling equities and ETFs', 'unit': 'bps', 'max': 500.0},
    {'id': 'custodyFee', 'group': 'Custody fees',
     'label': 'Annual custody fee', 'unit': 'usd', 'max': 1e7, 'default': 5000,
     'zero': 'Remove custody for GSBZ Custody'},
)
IDS = tuple(f['id'] for f in FIELDS)
_BY_ID = {f['id']: f for f in FIELDS}


def describe() -> list:
    """The fields, for the page to build its form from."""
    return [dict(f) for f in FIELDS]


def withDefaults(charges) -> dict:
    """The charges as they stand: the fixed ones at the house's values, a
    default one at its default until it is set, the rest as entered."""
    out = dict(charges or {})
    for f in FIELDS:
        if 'fixed' in f:
            out[f['id']] = f['fixed']
        elif 'default' in f and out.get(f['id']) is None:
            out[f['id']] = f['default']
    return out


def clean(payload) -> dict:
    """A charges payload checked: every key a charge, every value a number
    from zero to its most, or None for one not set; the lower FX edge under
    the higher; a fixed charge at the house's value whatever was sent.
    ValidationError (field ``otherCharges``) otherwise."""
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValidationError('otherCharges', 'The other charges must be a set of named values.')
    out = {}
    for key, value in payload.items():
        field = _BY_ID.get(key)
        if field is None:
            raise ValidationError('otherCharges', 'There is no charge called {!r}.'.format(key))
        if value is None or value == '' or 'fixed' in field:
            continue
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValidationError('otherCharges', '{} must be a number.'.format(field['label']))
        value = float(value)
        if value < 0 or value > field['max']:
            raise ValidationError('otherCharges', '{} must be between 0 and {}.'.format(
                field['label'], _format(field, field['max'])))
        out[key] = round(value, 2) if field['unit'] == 'bps' else round(value)
    out = withDefaults(out)
    high, low = out.get('fxHighEdge'), out.get('fxLowEdge')
    if high is not None and low is not None and low >= high:
        raise ValidationError('otherCharges', 'The lower FX trade size must be under the higher one.')
    return out


def missing(charges) -> list:
    """The charges not yet set, by what the form calls them."""
    charges = withDefaults(charges)
    return [f['label'] for f in FIELDS if charges.get(f['id']) is None]


def _format(field, value) -> str:
    if field['unit'] == 'bps':
        return '{:g}'.format(value)
    if value >= 1e6 and value % 1e5 == 0:
        return '${:g}M'.format(value / 1e6)
    return '${:,.0f}'.format(value)


def fieldValues(charges) -> dict:
    """Each charge set, as the template's field prints it: a trade size as
    $5M, a commission or charge in basis points as 10, a fee as $5,000."""
    charges = withDefaults(charges)
    return {key: _format(_BY_ID[key], value) for key, value in charges.items()
            if key in _BY_ID and value is not None}


def required(charges, includeFees: bool) -> dict:
    """The charges a delivery carries: as they stand, and every one set for
    a priced proposal - ValidationError naming the ones still missing."""
    out = withDefaults(charges)
    gaps = missing(out)
    if includeFees and gaps:
        raise ValidationError('otherCharges', 'Enter the other charges before downloading: '
                              '{} still to set.'.format(', '.join(gaps)))
    return out
