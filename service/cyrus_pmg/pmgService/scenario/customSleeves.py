"""Custom sleeves: a sleeve built for one proposal (D170).

Beside the library's sleeves, a proposal may carry a CUSTOM sleeve for a
category: products and weights chosen on the implementation page, kept with
the scenario and never put in the library. It is priced, checked, delivered
and recorded as a library sleeve is - everything downstream of the choice
works on products and weights, never on the sleeve.

Who may build one. An admin, in any category a PWA picks a sleeve for. A PWA,
only while an admin has opened the custom workflows to PWAs - one switch for
custom sleeves and custom portfolios alike (customAccess, D172). A category an
overlay fills (rules.autoSleeveCategories) has no picker and takes no custom
sleeve.

What it may hold. The products the category's library sleeves hold under the
proposal's implementation type - every edition, disabled ones included,
archived ones not - so a PWA builds only from products an admin has already
placed in that category. Any number of them (D173), each once and each live
in the catalogue.

Its weights, in one of two modes:

- ``sleeve``: percentages of the sleeve, adding to exactly 100. The category
  keeps its strategic weight (after the overlays), and each product is that
  weight times its share - exactly as a library sleeve.
- ``portfolio``: percentages of the whole portfolio. Their total REPLACES the
  category's strategic weight. Nothing else moves to make room: the person
  decides where the difference goes, and the proposal cannot be delivered
  until the implemented portfolio adds to 100% again.

A proposal whose custom sleeves change a category's weight this way departs
from the strategic allocation, and its delivered files leave the strategic
allocation out (``departs``, the workbook and the deck).

There is nothing for an admin to set but who may build (D173).
"""

from __future__ import annotations

import math

from . import customAccess, products, sleeveRepo
from .types import ValidationError

MODES = ('sleeve', 'portfolio')
NAME_MAX = sleeveRepo.NAME_MAX
NOTE_MAX = 300
#: a sleeve's weights add to 100 within this many percentage points
SLEEVE_TOLERANCE = 1e-4
#: the implemented portfolio adds to 100 within half a hundredth of a percentage
#: point - half the smallest step a typed weight takes, so it reads 100.00
BALANCE_TOLERANCE = 0.005
#: what a custom sleeve is called when it is given no name
DEFAULT_NAME = 'Custom'


# ------------------------------------------------------------- who builds ---

def _pickable() -> list:
    """The categories a sleeve is picked for: every repository category but
    the ones an overlay fills."""
    from .rules import autoSleeveCategories
    automatic = set(autoSleeveCategories())
    return [c for c in sleeveRepo.categories() if c not in automatic]


def isFixed(category) -> bool:
    from .rules import autoSleeveCategories
    return category in set(autoSleeveCategories())


def mayBuild(category, admin: bool) -> bool:
    """Whether a caller may build or change a custom sleeve in *category*:
    an admin, or a PWA while the custom workflows are open to PWAs (D172)."""
    if isFixed(category):
        return False
    return customAccess.may(admin)


def describe() -> dict:
    """What the page is told."""
    return {'pickable': _pickable(), 'modes': list(MODES), 'nameMax': NAME_MAX,
            'noteMax': NOTE_MAX}


# --------------------------------------------------------- the products ---

def categoryProductIds(category, variant) -> list:
    """The ids of the products *category*'s library sleeves hold under
    *variant*: every edition, disabled sleeves included, archived ones (and
    the superseded versions in the archive) not."""
    conn = sleeveRepo.connection()
    try:
        held = {r['productId'] for r in conn.execute(
            'SELECT DISTINCT p.productId FROM sleeveProducts p JOIN sleeves s ON s.id = p.sleeveId '
            "WHERE s.deletedAt = '' AND s.variant = ? AND s.category = ?",
            (variant, category))}
    finally:
        conn.close()
    return [p['productId'] for p in products.all() if p['productId'] in held]


def categoryProducts(category, variant) -> list:
    """The products a custom sleeve in *category* may hold under *variant*,
    whole, in the catalogue's order."""
    ids = set(categoryProductIds(category, variant))
    return [p for p in products.all() if p['productId'] in ids]


# ------------------------------------------------------------ the checks ---

def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def total(definition) -> float:
    return sum(float(p['weightPct']) for p in (definition or {}).get('products') or [])


def clean(category, definition, variant) -> dict:
    """*definition* checked and normalised, or ValidationError naming what is
    wrong: ``{name, note, mode, products: [{productId, weightPct}]}``."""
    if isFixed(category):
        raise ValidationError('customSleeves', '{} carries its sleeve automatically and takes '
                              'no custom sleeve.'.format(category))
    if not isinstance(definition, dict):
        raise ValidationError('customSleeves', 'A custom sleeve is products and weights.')
    mode = definition.get('mode') or 'sleeve'
    if mode not in MODES:
        raise ValidationError('customSleeves', 'Weights are a percentage of the sleeve or of the '
                              'portfolio.')
    name = sleeveRepo.normaliseName(definition.get('name') or '')
    if len(name) > NAME_MAX:
        raise ValidationError('customSleeves', 'The name is {} characters; at most {}.'.format(
            len(name), NAME_MAX))
    note = ' '.join(str(definition.get('note') or '').split())
    if len(note) > NOTE_MAX:
        raise ValidationError('customSleeves', 'The note is {} characters; at most {}.'.format(
            len(note), NOTE_MAX))
    rows = definition.get('products')
    if not isinstance(rows, list) or not rows:
        raise ValidationError('customSleeves', 'The custom sleeve for {} has no products.'.format(
            category))
    allowed = set(categoryProductIds(category, variant))
    seen, out = set(), []
    for row in rows:
        pid = str((row or {}).get('productId') or '').strip()
        if not pid:
            raise ValidationError('customSleeves', 'A product in the custom sleeve has no id.')
        if pid in seen:
            raise ValidationError('customSleeves', '{} is in the custom sleeve twice.'.format(
                _label(pid)))
        seen.add(pid)
        if pid not in allowed:
            raise ValidationError('customSleeves', '{} is not one of the products {}\'s sleeves '
                                  'hold under {}.'.format(_label(pid), category, variant))
        weight = _number((row or {}).get('weightPct'))
        if weight is None or weight <= 0:
            raise ValidationError('customSleeves', 'Give {} a weight above zero.'.format(_label(pid)))
        out.append({'productId': pid, 'weightPct': round(weight, 4)})
    sum_ = sum(r['weightPct'] for r in out)
    if mode == 'sleeve' and abs(sum_ - 100.0) > SLEEVE_TOLERANCE:
        raise ValidationError('customSleeves', 'The custom sleeve for {} adds to {:.2f}%; a '
                              'sleeve\'s weights add to 100%.'.format(category, sum_))
    if mode == 'portfolio' and sum_ > 100.0 + SLEEVE_TOLERANCE:
        raise ValidationError('customSleeves', 'The custom sleeve for {} is {:.2f}% of the '
                              'portfolio, more than all of it.'.format(category, sum_))
    return {'name': name, 'note': note, 'mode': mode, 'products': out}


def _label(productId) -> str:
    product = products.get(productId)
    return product['name'] if product else productId


def problems(category, definition, variant) -> list:
    """What stops a stored custom sleeve being built now: products that have
    left the category's list or the catalogue since it was made."""
    allowed = set(categoryProductIds(category, variant))
    return ['{} is no longer one of {}\'s products'.format(_label(p['productId']), category)
            for p in (definition or {}).get('products') or [] if p['productId'] not in allowed]


def displayName(definition) -> str:
    name = ((definition or {}).get('name') or '').strip()
    return '{}: {}'.format(DEFAULT_NAME, name) if name else DEFAULT_NAME


def hydrate(definition):
    """The custom sleeve in the shape a library sleeve is served in - name
    and whole products, each with ``weight``, its share of the sleeve - plus
    what it is: ``custom``, ``mode``, ``totalPct`` (the products' total as
    typed) and the definition. None while a product is missing from the
    catalogue."""
    rows = (definition or {}).get('products') or []
    whole = total(definition)
    if not rows or whole <= 0:
        return None
    served = []
    for row in rows:
        product = products.get(row['productId'])
        if product is None:
            return None
        product['weight'] = float(row['weightPct']) / whole
        product['weightPct'] = float(row['weightPct'])
        served.append(product)
    return {'id': None, 'name': displayName(definition), 'note': definition.get('note') or '',
            'products': served, 'custom': True, 'mode': definition.get('mode') or 'sleeve',
            'totalPct': whole,
            'definition': {'name': definition.get('name') or '', 'note': definition.get('note') or '',
                           'mode': definition.get('mode') or 'sleeve',
                           'products': [dict(r) for r in rows]}}


def activeFor(under, sleevesMap, customMap):
    """The custom sleeve in force for *under*, or None: one is in force when
    the category has no library sleeve chosen and a custom one is held."""
    if (sleevesMap or {}).get(under):
        return None
    return (customMap or {}).get(under) or None
