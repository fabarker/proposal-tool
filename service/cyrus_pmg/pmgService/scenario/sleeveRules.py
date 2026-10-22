"""Applicability rules: which strategic portfolios an edition of a sleeve is
for (D89).

A sleeve NAME is what a PWA picks. Behind the name the library may hold more
than one EDITION - each its own row in the repository with its own products,
weights and history - and each edition carries rules saying which strategic
portfolios it applies to. The PWA never sees an edition: the rail lists one
entry per name, and the one served is whichever edition's rules match the
base portfolio, or the fallback edition, which has no rules.

A rule constrains any subset of three fields of a strategic portfolio:

    currency        riskLevel        allocationType

and the custodian the proposal is held at under PMG ESG (D193, D195),
which is the scenario's rather than the portfolio's:

    entity

A constrained field lists the values it allows; an unconstrained one means
any. A rule matches a portfolio when every constrained field contains the
portfolio's value. An edition has one or more rules and applies to a
portfolio when ANY of them matches. A rule that constrains nothing is
refused: that is what the fallback is for, and an edition claiming every
portfolio under a label would make the fallback ambiguous.

Hedging and the real-assets exclusion are deliberately NOT fields. Both are
part of what identifies a resolved portfolio, and both were in the first
draft of this design; they were taken out on review. An all-equity book has
no allocation type and is matched as ``NA``.

Everything here is judged against the strategic universe as loaded - the
finite set of portfolios the supplying database delivered, which the tool
already holds in full. That is what makes applicability a SET rather than a
predicate: an edition's rules expand to the exact keys they name, the console
can show the desk how many and which, and two editions of one name that
claim the same portfolio can be refused at save by intersecting two sets.
The sets are cached against the generation universe.reload() bumps, like
the option lists in rules.py.
"""

from __future__ import annotations

import json
import threading

from . import entities, saaKeys, universe
from .types import PortfolioKey, ValidationError

FIELDS = ('currency', 'riskLevel', 'allocationType', 'entity')
#: the fields a strategic portfolio answers; the entity is the scenario's
PORTFOLIO_FIELDS = FIELDS[:3]

#: every entity: what a rule naming none allows
ANY = None

#: what an all-equity portfolio's allocation type reads as in a rule
NA = 'NA'

#: how several values share one CSV cell: ``Full|Core``
SEPARATOR = '|'

_lock = threading.Lock()
_cache = {}        # pack(rules) -> (generation, [keyStr, ...])


# ------------------------------------------------------------ vocabulary ---

def vocabulary() -> dict:
    """The values a rule may name, per field, in the vocabulary's own order -
    and only the ones the universe actually holds. A rule about a portfolio
    that does not exist is refused by name (most often a typo), the way an
    unknown ticker is."""
    keys = universe.keys()
    currencies = {k.currency for k in keys}
    risks = {k.riskLevel for k in keys}
    types = {k.allocationType or NA for k in keys}
    return {
        'currency': [c for c in saaKeys.CURRENCIES if c in currencies],
        'riskLevel': [r for r in saaKeys.RISK_LEVELS if r in risks],
        'allocationType': ([t for t in saaKeys.ALLOCATION_TYPES if t in types]
                           + ([NA] if NA in types else [])),
        # the custodians, by id (D195)
        'entity': entities.ids(),
    }


def portfolioKeys() -> list:
    """Every strategic portfolio as its key string, in the universe's order:
    what an edition's rules are judged against (D158). The console reads the
    rules the way matches() does against these, so it can count, list and map
    the portfolios as the desk ticks - the save still decides."""
    return universe.keyStrs()


def valueLabels() -> dict:
    """The words a rule's values are shown in (D158): risk levels by the
    firm's names, and the all-equity "allocation" as what it is. Cosmetic -
    a rule still names the value."""
    from .rules import RISK_LEVEL_LABELS
    return {'riskLevel': dict(RISK_LEVEL_LABELS), 'allocationType': {NA: 'All equity'},
            'entity': {e['id']: e['name'] for e in entities.everyEntity()}}


def fieldsOf(key: PortfolioKey) -> dict:
    """The three fields a rule is judged against, for one portfolio."""
    return {'currency': key.currency, 'riskLevel': key.riskLevel,
            'allocationType': key.allocationType or NA}


# ------------------------------------------------------------- the shape ---

def parseCell(text) -> list:
    """``Full|Core`` -> ['Full', 'Core']; blank -> []."""
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        return [str(v).strip() for v in text if str(v).strip()]
    return [v.strip() for v in str(text).split(SEPARATOR) if v.strip()]


def cell(values) -> str:
    return SEPARATOR.join(values or [])


def normalise(rules) -> list:
    """The canonical shape of a rule set, or a ValidationError naming
    ``rules``: a list of {field: [values]} with every value in the
    vocabulary, values in vocabulary order, no empty rule and no duplicate
    rule. None and [] both mean no rules - the fallback."""
    if rules is None:
        return []
    if isinstance(rules, str):
        try:
            rules = json.loads(rules or '[]')
        except ValueError:
            raise ValidationError('rules', 'The rules could not be read.')
    if not isinstance(rules, (list, tuple)):
        raise ValidationError('rules', 'Rules must be a list.')
    vocab = vocabulary()
    out, seen = [], set()
    for position, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise ValidationError('rules', 'Rule {} is not a rule.'.format(position))
        unknown = [f for f in rule if f not in FIELDS]
        if unknown:
            raise ValidationError(
                'rules', 'Rule {} names {}, which is not a field a rule can constrain. '
                'The fields are {}.'.format(position, ', '.join(sorted(unknown)), ', '.join(FIELDS)))
        clean = {}
        for field in FIELDS:
            values = parseCell(rule.get(field))
            if not values:
                continue
            if field == 'entity':
                # by id or by name, whatever the capitals: a file says GSBZ
                values = [entities.resolve(v) or v for v in values]
            bad = [v for v in values if v not in vocab[field]]
            if bad and field == 'entity':
                raise ValidationError(
                    'rules', 'Rule {}: {} {} not a custodian. Known: {}.'.format(
                        position, ', '.join(repr(b) for b in bad), 'is' if len(bad) == 1 else 'are',
                        ', '.join(entities.name(e) for e in vocab[field])))
            if bad:
                raise ValidationError(
                    'rules', 'Rule {}: {} {} not in the universe. Known values: {}.'.format(
                        position, ', '.join(repr(b) for b in bad), 'is' if len(bad) == 1 else 'are',
                        ', '.join(vocab[field])))
            clean[field] = [v for v in vocab[field] if v in set(values)]
        if not clean:
            raise ValidationError(
                'rules', 'Rule {} constrains nothing. Leave the rules empty for the fallback '
                'edition, or name at least one field.'.format(position))
        signature = pack([clean])
        if signature in seen:
            raise ValidationError('rules', 'Rule {} repeats an earlier rule.'.format(position))
        seen.add(signature)
        out.append(clean)
    return out


def pack(rules) -> str:
    """A rule set as JSON, for the history row and the cache key. Fields in
    FIELDS order, values as normalised, so equal sets pack equal."""
    return json.dumps([{f: r[f] for f in FIELDS if r.get(f)} for r in (rules or [])])


def unpack(packed) -> list:
    try:
        rules = json.loads(packed or '[]')
    except (ValueError, TypeError):
        return []
    return [{f: list(r.get(f) or []) for f in FIELDS if r.get(f)} for r in rules
            if isinstance(r, dict)]


def describe(rules) -> str:
    """One line for a table cell: ``currency GBP; allocationType Full|Core``
    per rule, rules joined by `` OR ``. Blank for the fallback."""
    parts = []
    for rule in rules or []:
        parts.append('; '.join('{} {}'.format(f, cell(
            [entities.name(v) for v in rule[f]] if f == 'entity' else rule[f]))
            for f in FIELDS if rule.get(f)))
    return ' OR '.join(parts)


# ------------------------------------------------------------ matching ---

def _portfolioMatch(rule, fields) -> bool:
    return all(fields[f] in rule[f] for f in PORTFOLIO_FIELDS if rule.get(f))


def matches(rules, key: PortfolioKey, entity=None) -> bool:
    """Whether any rule matches *key* at *entity* (D193). No rules match
    nothing - the fallback is chosen by the resolver, not by matching. A rule
    naming entities matches only a scenario at one of them; one naming none,
    a scenario at any entity or none."""
    fields = fieldsOf(key)
    for rule in rules or []:
        if _portfolioMatch(rule, fields) and (not rule.get('entity') or entity in rule['entity']):
            return True
    return False


def entitiesAt(rules, key: PortfolioKey):
    """The entities the rules claim *key* at: ANY where a rule matching it
    names none, else the set they name (empty where none matches)."""
    fields = fieldsOf(key)
    out = set()
    for rule in rules or []:
        if not _portfolioMatch(rule, fields):
            continue
        if not rule.get('entity'):
            return ANY
        out.update(rule['entity'])
    return out


def applicability(rules) -> list:
    """Every portfolio in the universe the rules name at some entity, as key
    strings in the universe's own order. Empty for the fallback. Cached
    against the universe's generation."""
    if not rules:
        return []
    signature = pack(rules)
    generation = universe.generation()
    with _lock:
        hit = _cache.get(signature)
        if hit is not None and hit[0] == generation:
            return list(hit[1])
    keys = [k.toStr() for k in universe.keys()
            if any(_portfolioMatch(rule, fieldsOf(k)) for rule in rules)]
    with _lock:
        _cache[signature] = (generation, keys)
    return list(keys)


def overlap(rulesA, rulesB) -> list:
    """The portfolios both rule sets name at a common entity, in universe
    order (D193): an edition for GSBE and one for GSI may name the same
    portfolios, and never meet."""
    b = set(applicability(rulesB))
    out = []
    for k in applicability(rulesA):
        if k not in b:
            continue
        key = PortfolioKey.fromStr(k)
        atA, atB = entitiesAt(rulesA, key), entitiesAt(rulesB, key)
        if atA is ANY or atB is ANY or atA & atB:
            out.append(k)
    return out
