"""Portfolio names -> keys: the vocabularies and the parser (D54).

The supplying database keys a strategic portfolio by its NAME, and the name
carries everything except hedging:

    USD Moderate ex-HFs ex-RAs   ->   USD|Moderate|ex-HFs|1
    USD Moderate Full            ->   USD|Moderate|Full|0
    USD All Equity               ->   USD|All Equity|NA|NA

Two things the parse does that the name does not say outright. ``ex-RAs`` is
not an allocation type but a toggle over one, so the name's trailing field
yields two key fields - the UI offers four allocation types and a separate
exclusion tick box, not six types. And an all-equity book has no alternatives
to include or exclude, so it carries no allocation type at all: both fields are
None. Hedging is not in the name and not in the key; it is appended when the
analytics run (PortfolioKey.withHedging).

Names cannot be split on whitespace - risk levels and allocation types both
contain spaces - so every token is matched longest-first against the closed
vocabularies below. Anything unrecognised is REJECTED, never guessed: the
parse runs in the bake's enumeration step, offline, where a rename upstream
fails visibly in the manifest rather than on a request path.

The vocabularies are the one piece of configuration a name cannot supply.
RISK_LEVELS is ORDERED, least to most risky - that order is not derivable and
not alphabetical - and the same list is the parser's dictionary, the validator
and the selector ordering. Everything else the UI offers is derived from the
key set (see universe.facets).
"""

from __future__ import annotations

from .types import PortfolioKey

CURRENCIES = ('USD', 'GBP', 'CHF', 'EUR')

RISK_LEVELS = ('LowVol', 'Conservative', 'ConsMod', 'Moderate',
               'ModAgg', 'Agg', 'Higher Risk', 'All Equity')

ALLOCATION_TYPES = ('Full', 'Core', 'ex-Alts', 'ex-HFs')

EXCLUSION_SUFFIX = ' ex-RAs'


class UnparseableName(ValueError):
    """A portfolio name the vocabulary does not cover. Recorded, never swallowed."""


def _stripLongest(text: str, vocabulary) -> tuple:
    """(head, matched) for the longest vocabulary entry ending *text*."""
    for entry in sorted(vocabulary, key=len, reverse=True):
        if text == entry:
            return '', entry
        if text.endswith(' ' + entry):
            return text[:-(len(entry) + 1)], entry
    return text, None


def parseName(name: str) -> PortfolioKey:
    """The key a portfolio name denotes. Raises UnparseableName rather than guess."""
    text = ' '.join(str(name or '').split())
    if not text:
        raise UnparseableName('empty portfolio name')

    currency, _, rest = text.partition(' ')
    if currency not in CURRENCIES:
        raise UnparseableName('unknown currency {!r} in {!r}'.format(currency, name))
    if not rest:
        raise UnparseableName('no risk level in {!r}'.format(name))

    # The allocation phrase, if there is one, is the tail. Take the exclusion
    # off it first: 'ex-HFs ex-RAs' is the ex-HFs type with the toggle set.
    excludeRealAssets = rest.endswith(EXCLUSION_SUFFIX)
    head = rest[:-len(EXCLUSION_SUFFIX)] if excludeRealAssets else rest

    head, allocation = _stripLongest(head, ALLOCATION_TYPES)
    if allocation is None:
        if excludeRealAssets:
            raise UnparseableName(
                'exclusion without an allocation type in {!r}'.format(name))
        head = rest                       # no allocation phrase at all

    riskLevel = head.strip()
    if riskLevel not in RISK_LEVELS:
        raise UnparseableName('unknown risk level {!r} in {!r}'.format(riskLevel, name))

    if allocation is None:
        return PortfolioKey(currency, riskLevel, None, None)
    return PortfolioKey(currency, riskLevel, allocation, bool(excludeRealAssets))


def formatKey(key: PortfolioKey) -> str:
    """The name a key denotes - the inverse of parseName."""
    if key.isAllEquity:
        return '{} {}'.format(key.currency, key.riskLevel)
    suffix = EXCLUSION_SUFFIX if key.excludeRealAssets else ''
    return '{} {} {}{}'.format(key.currency, key.riskLevel,
                               key.allocationType, suffix)


def sortKey(key: PortfolioKey) -> tuple:
    """Vocabulary order: currency, then risk, then type, then the exclusion."""
    return (CURRENCIES.index(key.currency), RISK_LEVELS.index(key.riskLevel),
            -1 if key.isAllEquity else ALLOCATION_TYPES.index(key.allocationType),
            bool(key.excludeRealAssets))
