"""The custodians a PMG ESG proposal may be held at (D193, D195).

What ESG means differs by jurisdiction, so under PMG ESG the funds a client
may own follow the custodian the account is held at: GSI, GSBZ, GSLU or
GSCO. The PWA chooses it on a card that opens as PMG ESG is chosen, and the
scenario keeps it; under any other type a scenario has none. Its one use is
to pick sleeve editions: an edition may be FOR particular custodians, as it
may be for particular currencies (sleeveRules' fourth field), and the one
served is the edition for the scenario's custodian, else the fallback.

The list is fixed here, not kept in the repository. Each custodian keeps its
id for life - the lower-case name - since edition rules, scenarios and
delivered proposals name it by that.
"""

from __future__ import annotations

from .types import ValidationError

#: the custodians, in the order the card offers them: (id, name)
CUSTODIANS = [('gsi', 'GSI'), ('gsbz', 'GSBZ'), ('gslu', 'GSLU'), ('gsco', 'GSCO')]

#: the implementation types that ask for a custodian
CUSTODY_TYPES = ['PMG ESG']


def everyEntity() -> list:
    """Every custodian, in the card's order: [{id, name}]."""
    return [{'id': i, 'name': n} for i, n in CUSTODIANS]


def ids() -> list:
    return [i for i, _ in CUSTODIANS]


def offered() -> list:
    """The custodians a PWA may choose."""
    return everyEntity()


def get(entityId):
    """One custodian by id, or None."""
    return next((e for e in everyEntity() if e['id'] == entityId), None)


def name(entityId) -> str:
    """A custodian's name, or its id when the list does not have it."""
    found = get(entityId)
    return found['name'] if found else str(entityId or '')


def resolve(text):
    """A custodian's id from its id or its name, whatever the capitals; None
    for one the list does not have."""
    key = str(text or '').strip().lower()
    return next((i for i, n in CUSTODIANS if key in (i, n.lower())), None)


def custodyTypes() -> list:
    """The implementation types that ask for a custodian."""
    return list(CUSTODY_TYPES)


def needed(variant) -> bool:
    """Whether a scenario under *variant* asks for a custodian."""
    return bool(variant) and variant in CUSTODY_TYPES


def effective(entityId, variant) -> str:
    """The custodian a scenario under *variant* is at: the one it names,
    where the type asks for one and the list has it, else none - a scenario
    that moved to a type without one, or names one no longer on the list,
    holds none, and is asked again."""
    return entityId if needed(variant) and get(entityId) else ''


def checkChoice(entityId) -> str:
    """The custodian a scenario names, checked: '' for none, its id, or a
    ValidationError naming ``entity``."""
    if entityId is None or entityId == '':
        return ''
    if not isinstance(entityId, str) or get(entityId.strip()) is None:
        raise ValidationError('entity', 'The custodian is one of {}.'.format(
            ', '.join(n for _, n in CUSTODIANS)))
    return entityId.strip()
