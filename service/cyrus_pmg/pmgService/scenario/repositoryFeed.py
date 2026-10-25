"""The repository's record as one feed (D66, D167).

Every change the repository keeps a history of, newest first, in one place:
the sleeves (sleeveRepo), the products of the catalogue (products, D165),
the fee card (feeCard, D166), the overlay rules (overlayRules, D155) and the
private-markets funding split (fundingSplit, D148), the sleeves disabled
and enabled again (sleeveRepo's switches, D169), the switch that opens
the custom workflows to PWAs (customAccess, D172), the private markets
minimums (privateMarkets, D200), the investment restrictions
(restrictions, D203), the mandate codes (mandateCodes, D205) and the
portfolio benchmarks (portfolioBenchmarks, D211). Each keeps its own
history and is the authority on it; this reads them side by side, filtered
and paged together, so the console's Activity tab and its CSV answer "what
happened, who did it and when" for everything at once.

An entry carries its AREA beside what it always carried: when, what action,
who, what it was done to, what moved, and the note when one was given.
Sleeve entries are exactly sleeveRepo.activity's. A product file loaded or
imported is ONE entry - the file, the products it touched and how - rather
than a line per product; the product's own history still holds a revision
for each.

Filters are the feed's: actions, who, an implementation type, a category, a
date range and a text search, and now the areas. A type names the sleeves of
that type and the overlay rules and funding split of that type's own list;
a category names sleeves and their switches only. Products and the fee
card belong to no type, so either filter leaves them out.

Paging is by a cursor of (when, area, id), so entries made at the same
second in two areas page in a fixed order. The cursor sleeveRepo used before
- (when, id) - still reads, as a sleeve cursor.
"""

from __future__ import annotations

import json

from . import (customAccess, feeCard, fundingSplit, mandateCodes, overlayRules, portfolioBenchmarks,
               privateMarkets, products, restrictions, sleeveRepo, sleeveRules)

AREAS = ['sleeves', 'products', 'fees', 'overlays', 'funding', 'availability', 'access', 'minimums',
         'restrictions', 'mandateCodes', 'benchmarks']
AREA_LABELS = {'sleeves': 'Sleeves', 'products': 'Products', 'fees': 'Fee card',
               'overlays': 'Overlay rules', 'funding': 'Funding split',
               'availability': 'Sleeves offered', 'access': 'Custom workflows for PWAs',
               'minimums': 'Private markets minimums', 'restrictions': 'Investment restrictions',
               'mandateCodes': 'Mandate codes', 'benchmarks': 'Portfolio benchmarks'}
_RANK = {a: i for i, a in enumerate(AREAS)}
#: every action any area records, in the order the feed's chips offer them
ACTIONS = ['created', 'updated', 'imported', 'reverted', 'deleted', 'withdrawn', 'removed',
           'restored', 'disabled', 'enabled', 'shared', 'unshared', 'split', 'seeded', 'baseline']
FEED_LIMIT_MAX = sleeveRepo.FEED_LIMIT_MAX
#: products listed by name in a file's entry before the rest are counted
_BATCH_NAMES = 12

ACTIVITY_COLUMNS = ['At', 'Area', 'Action', 'Actor', 'Subject', 'Variant', 'Category', 'Edition',
                    'Rules', 'Id', 'Revision', 'Changes', 'Products', 'Note']


def _cursorKey(cursor):
    """(at, rank, id) from a cursor string, or None for none."""
    if not cursor or '|' not in cursor:
        return None
    parts = cursor.split('|')
    try:
        if len(parts) == 2:
            return (parts[0], 0, int(parts[1]))
        return (parts[0], int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _cursorOf(entry) -> str:
    return '{}|{}|{}'.format(entry['at'], _RANK[entry['area']], entry['feedId'])


def _key(entry):
    return (entry['at'], _RANK[entry['area']], entry['feedId'])


# ------------------------------------------------------------- the areas ---

def _productEntries(conn) -> list:
    """Every product revision, a file's load or import as one entry."""
    rows = conn.execute('SELECT * FROM productHistory ORDER BY id').fetchall()
    previous, out, batches = {}, [], {}
    tops = {r['productId']: r['revision'] for r in conn.execute(
        'SELECT productId, MAX(revision) AS revision FROM productHistory GROUP BY productId')}
    names = {r['productId']: (r['name'], bool(r['withdrawnAt']))
             for r in conn.execute('SELECT productId, name, withdrawnAt FROM products')}
    for r in rows:
        body = json.loads(r['body'])
        moved = products.changes(previous.get(r['productId']), body)
        words = products.changeWords(moved)
        if r['action'] == 'withdrawn':
            words = ['Withdrawn from the catalogue']
        elif r['action'] == 'restored':
            words = ['Back in the catalogue']
        previous[r['productId']] = body
        if r['action'] in ('seeded', 'imported', 'withdrawn') and r['source']:
            # a file's load or import, and the products it withdrew, are one
            # entry: one admin, one file, one moment
            batchAction = 'seeded' if r['action'] == 'seeded' else 'imported'
            key = (batchAction, r['at'], r['actor'], r['source'])
            batch = batches.get(key)
            if batch is None:
                batch = {'area': 'products', 'feedId': r['id'], 'at': r['at'],
                         'action': batchAction, 'actor': r['actor'], 'name': r['source'],
                         'productId': None, 'revision': None, 'note': r['note'],
                         'source': r['source'], 'batch': 0, 'products': [], 'changes': [],
                         'counts': {'added': 0, 'changed': 0, 'withdrawn': 0}}
                batches[key] = batch
                out.append(batch)
            batch['feedId'] = max(batch['feedId'], r['id'])
            batch['batch'] += 1
            kind = 'withdrawn' if r['action'] == 'withdrawn' else \
                'added' if r['revision'] == 1 else 'changed'
            batch['counts'][kind] += 1
            # a file's first load is the whole catalogue: its count says it,
            # where a list of names would only be the start of the catalogue
            if batchAction == 'imported' and len(batch['products']) < _BATCH_NAMES:
                batch['products'].append({'productId': r['productId'], 'name': body['name'],
                                          'changes': words})
            continue
        out.append({'area': 'products', 'feedId': r['id'], 'at': r['at'], 'action': r['action'],
                    'actor': r['actor'], 'name': body['name'], 'productId': r['productId'],
                    'revision': r['revision'], 'note': r['note'], 'source': r['source'],
                    'batch': 0, 'products': [], 'changes': words,
                    'current': tops.get(r['productId']) == r['revision'],
                    'withdrawn': names.get(r['productId'], ('', False))[1]})
    for batch in out:
        if batch['batch']:
            c = batch['counts']
            said = []
            if c['added']:
                said.append('{} added'.format(c['added']))
            if c['changed']:
                said.append('{} changed'.format(c['changed']))
            if c['withdrawn']:
                said.append('{} withdrawn'.format(c['withdrawn']))
            batch['changes'] = [('{} product{} loaded'.format(batch['batch'], '' if batch['batch'] == 1 else 's')
                                 if batch['action'] == 'seeded' else ', '.join(said))]
    return out


def _feeEntries(conn) -> list:
    rows = feeCard.feedRows(conn)
    top = rows[-1]['revision'] if rows else None
    return [{'area': 'fees', 'feedId': r['id'], 'at': r['at'], 'action': r['action'],
             'actor': r['actor'], 'name': 'Fee card', 'revision': r['revision'],
             'note': r['note'],
             'changes': r['changes'] or (['The card loaded'] if r['action'] == 'seeded' else []),
             'current': r['revision'] == top}
            for r in rows]


def _scopes(conn, kind) -> list:
    return [r['scope'] for r in conn.execute(
        'SELECT DISTINCT scope FROM policyHistory WHERE kind = ? ORDER BY scope', (kind,))]


def _idsFor(conn, kind) -> dict:
    return {(r['scope'], r['revision']): r['id'] for r in conn.execute(
        'SELECT id, scope, revision FROM policyHistory WHERE kind = ?', (kind,))}


def _scopeName(scope) -> str:
    return 'Default' if scope == '*' else scope


def _overlayEntries(conn) -> list:
    out, ids = [], _idsFor(conn, overlayRules.KIND)
    for scope in _scopes(conn, overlayRules.KIND):
        for h in overlayRules.history(scope):
            out.append({'area': 'overlays', 'feedId': ids.get((scope, h['revision']), 0),
                        'at': h['at'], 'action': h['action'], 'actor': h['actor'],
                        'name': 'Overlay rules · ' + _scopeName(scope),
                        'variant': '' if scope == '*' else scope, 'scope': scope,
                        'revision': h['revision'], 'note': h['note'],
                        'changes': [h['change']] if h.get('change') else []})
    return out


def _splitWords(destinations) -> str:
    return ' · '.join('{} {:g}%'.format(d['category'], d['weightPct']) for d in destinations or [])


def _fundingEntries(conn) -> list:
    out, ids = [], _idsFor(conn, fundingSplit.KIND)
    for scope in _scopes(conn, fundingSplit.KIND):
        for h in fundingSplit.history(scope):
            if h['action'] == 'removed':
                words = ['Back on the default split']
            else:
                words = [_splitWords(h.get('destinations'))] if h.get('destinations') else []
            out.append({'area': 'funding', 'feedId': ids.get((scope, h['revision']), 0),
                        'at': h['at'], 'action': h['action'], 'actor': h['actor'],
                        'name': 'Funding split · ' + _scopeName(scope),
                        'variant': '' if scope == '*' else scope, 'scope': scope,
                        'revision': h['revision'], 'note': h['note'], 'changes': words})
    return out


#: what each switch did, in the feed's words; the type a sharing switch
#: acted on fills the gap (D179)
_SWITCH_WORDS = {'disabled': 'Not offered to PWAs', 'enabled': 'Offered to PWAs again',
                 'shared': 'Now offered under {} too, the same sleeve',
                 'unshared': 'No longer offered under {}',
                 'split': '{} given a copy of its own; the rest still share this one'}


def _switchEntries(conn) -> list:
    """Every sleeve disabled or enabled again (D169), and every type a
    sleeve was shared with, taken from or given a copy for (D179). Not
    revisions of the sleeve, so an area of their own rather than the
    sleeves' feed."""
    live = {r['id']: not r['deletedAt'] for r in conn.execute('SELECT id, deletedAt FROM sleeves')}
    out = []
    for r in sleeveRepo.switches(conn):
        words = _SWITCH_WORDS.get(r['action'], r['action'])
        out.append({'area': 'availability', 'feedId': r['id'], 'at': r['at'],
                    'action': r['action'], 'actor': r['actor'], 'name': r['name'],
                    'variant': sleeveRepo.typesLabel(r['variant']),
                    'variants': sleeveRepo.typesOf(r['variant']),
                    'category': r['category'], 'label': r['label'],
                    'sleeveId': r['sleeveId'], 'live': live.get(r['sleeveId'], False),
                    'revision': None, 'note': '',
                    'changes': [words.format(r['detail'] if 'detail' in r.keys() else '')]})
    return out


def _accessEntries(conn) -> list:
    """Every time the custom workflows were opened to PWAs or closed (D172)."""
    rows = conn.execute('SELECT * FROM policyHistory WHERE kind = ? ORDER BY revision',
                        (customAccess.KIND,)).fetchall()
    return [{'area': 'access', 'feedId': r['id'], 'at': r['at'], 'action': r['action'],
             'actor': r['actor'], 'name': 'Custom workflows for PWAs', 'variant': '',
             'revision': r['revision'], 'note': '', 'changes': [r['note']] if r['note'] else []}
            for r in rows]


def _minimumEntries(conn) -> list:
    """Every revision of the private markets minimums (D200), with each value that moved."""
    rows = privateMarkets.feedRows(conn)
    top = rows[-1]['revision'] if rows else None
    return [{'area': 'minimums', 'feedId': r['id'], 'at': r['at'], 'action': r['action'],
             'actor': r['actor'], 'name': 'Private markets minimums', 'variant': '',
             'revision': r['revision'], 'note': r['note'], 'changes': r['changes'],
             'current': r['revision'] == top}
            for r in rows]


def _restrictionEntries(conn) -> list:
    """Every revision of the investment restrictions (D203), with what changed."""
    rows = restrictions.feedRows(conn)
    top = rows[-1]['revision'] if rows else None
    return [{'area': 'restrictions', 'feedId': r['id'], 'at': r['at'], 'action': r['action'],
             'actor': r['actor'], 'name': 'Investment restrictions', 'variant': '',
             'revision': r['revision'], 'note': r['note'], 'changes': r['changes'],
             'current': r['revision'] == top}
            for r in rows]


def _mandateCodeEntries(conn) -> list:
    """Every revision of the mandate codes (D205), with what moved."""
    rows = mandateCodes.feedRows(conn)
    top = rows[-1]['revision'] if rows else None
    return [{'area': 'mandateCodes', 'feedId': r['id'], 'at': r['at'], 'action': r['action'],
             'actor': r['actor'], 'name': 'Mandate codes', 'variant': '',
             'revision': r['revision'], 'note': r['note'], 'changes': r['changes'],
             'current': r['revision'] == top}
            for r in rows]


def _benchmarkEntries(conn) -> list:
    """Every revision of the portfolio benchmarks (D211), with what moved."""
    rows = portfolioBenchmarks.feedRows(conn)
    top = rows[-1]['revision'] if rows else None
    return [{'area': 'benchmarks', 'feedId': r['id'], 'at': r['at'], 'action': r['action'],
             'actor': r['actor'], 'name': 'Portfolio benchmarks', 'variant': '',
             'revision': r['revision'], 'note': r['note'], 'changes': r['changes'],
             'current': r['revision'] == top}
            for r in rows]


def _others(conn) -> list:
    return (_productEntries(conn) + _feeEntries(conn) + _overlayEntries(conn)
            + _fundingEntries(conn) + _switchEntries(conn) + _accessEntries(conn)
            + _minimumEntries(conn) + _restrictionEntries(conn) + _mandateCodeEntries(conn)
            + _benchmarkEntries(conn))


# ------------------------------------------------------------ filtering ---

def _passes(entry, f, exclude=None) -> bool:
    if f['areas'] and exclude != 'area' and entry['area'] not in f['areas']:
        return False
    if f['actions'] and exclude != 'action' and entry['action'] not in f['actions']:
        return False
    if f['actor'] and exclude != 'actor' and entry['actor'] != f['actor']:
        return False
    if f['variant'] and exclude != 'variant' and f['variant'] not in _types(entry):
        return False
    if f['category'] and exclude != 'category' and entry.get('category') != f['category']:
        return False                # only sleeves and their switches have a category
    if f['since'] and entry['at'] < f['since']:
        return False
    if f['until'] and entry['at'] >= f['until']:
        return False
    return True


def _types(entry) -> list:
    """The types an entry is about: a shared sleeve's are several (D179)."""
    if entry.get('variants') is not None:
        return entry['variants']
    return [entry['variant']] if entry.get('variant') else []


def _matches(entry, query) -> bool:
    q = query.lower()
    words = [entry.get('name') or '', entry.get('actor') or '', entry.get('note') or '',
             entry.get('productId') or '', entry.get('source') or '', AREA_LABELS[entry['area']]]
    words += entry.get('changes') or []
    for p in entry.get('products') or []:
        words += [p.get('productId') or '', p.get('name') or ''] + list(p.get('changes') or [])
    return any(q in (w or '').lower() for w in words)


def _filters(areas=None, actions=None, variant=None, category=None, actor=None, since=None,
             until=None, query='') -> dict:
    return {'areas': [a for a in (areas or []) if a in AREAS],
            'actions': [a for a in (actions or []) if a in ACTIONS],
            'variant': variant or None, 'category': category or None, 'actor': actor or None,
            'since': since or None, 'until': until or None, 'query': (query or '').strip()}


def _sleevePage(f, before, limit, exclude=None) -> dict:
    """sleeveRepo's own feed under the filters it knows, or nothing when the
    areas leave the sleeves out."""
    wanted = [a for a in f['actions'] if a in sleeveRepo.ACTIONS] if exclude != 'action' else []
    if f['actions'] and exclude != 'action' and not wanted:
        return {'entries': [], 'total': 0, 'facets': {'action': {}, 'actor': {}, 'variant': {}},
                'earliest': '', 'next': None}
    return sleeveRepo.activity(
        actions=wanted, variant=f['variant'] if exclude != 'variant' else None,
        category=f['category'] if exclude != 'category' else None,
        actor=f['actor'] if exclude != 'actor' else None, since=f['since'], until=f['until'],
        query=f['query'], limit=limit, before=before)


def activity(areas=None, actions=None, variant=None, category=None, actor=None, since=None,
             until=None, query='', limit=100, before=None) -> dict:
    """A page of the whole record and the counts that frame it: {entries,
    next, total, facets: {area, action, actor, variant}, earliest}. A facet
    counts what each value would show with every OTHER filter in force, so
    a zero is a value that cannot be chosen from here. *total* counts the
    record under the filters before the text search narrows it, as the
    sleeves' feed always has."""
    limit = max(1, min(int(limit or 100), FEED_LIMIT_MAX))
    f = _filters(areas, actions, variant, category, actor, since, until, query)
    cursor = _cursorKey(before)

    # the sleeves, through their own feed: a cursor in another area reads as
    # "everything at that second", which the sleeves' own clause takes as
    # the same stamp and any id
    sleeveBefore = None
    if cursor is not None:
        at, rank, ident = cursor
        sleeveBefore = '{}|{}'.format(at, ident if rank == 0 else (2 ** 62 if rank > 0 else 0))
    includeSleeves = not f['areas'] or 'sleeves' in f['areas']
    page = _sleevePage(f, sleeveBefore, limit + 1) if includeSleeves else None
    candidates = []
    if page is not None:
        for e in page['entries']:
            e = dict(e, area='sleeves', feedId=int(e['cursor'].split('|')[1]))
            candidates.append(e)

    conn = sleeveRepo.connection()
    try:
        others = _others(conn)
    finally:
        conn.close()
    shown = [e for e in others if _passes(e, f) and (not f['query'] or _matches(e, f['query']))]
    if cursor is not None:
        shown = [e for e in shown if _key(e) < cursor]
    candidates += shown
    candidates.sort(key=_key, reverse=True)
    # one more sleeve than a page was asked for, and every other entry: if
    # all of that fits, there is nothing after it
    entries = candidates[:limit]
    for e in entries:
        e['cursor'] = _cursorOf(e)
    nextCursor = entries[-1]['cursor'] if entries and len(candidates) > limit else None

    # the counts: the sleeves' from their own feed, the rest counted here
    def count(dimension):
        out = {}
        if dimension == 'area':
            sleevesOnly = _sleevePage(f, None, 1, exclude='area')
            if sleevesOnly['total']:
                out['sleeves'] = sleevesOnly['total']
            for e in others:
                if _passes(e, f, exclude='area'):
                    out[e['area']] = out.get(e['area'], 0) + 1
            return out
        if includeSleeves:
            got = _sleevePage(f, None, 1, exclude=dimension)['facets'].get(dimension, {})
            for k, n in got.items():
                out[k] = out.get(k, 0) + n
        for e in others:
            if _passes(e, f, exclude=dimension):
                if dimension == 'variant':
                    values = _types(e)
                else:
                    values = [e.get(dimension) if dimension != 'action' else e['action']]
                for value in values:
                    if value:
                        out[value] = out.get(value, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))

    total = (page['total'] if page is not None else 0) + sum(1 for e in others if _passes(e, f))
    earliest = [x for x in ([page['earliest']] if page is not None else []) +
                [min((e['at'] for e in others), default='')] if x]
    return {'entries': entries, 'next': nextCursor, 'total': total,
            'facets': {'area': count('area'), 'action': count('action'),
                       'actor': count('actor'), 'variant': count('variant')},
            'earliest': min(earliest) if earliest else '',
            'areas': [{'id': a, 'label': AREA_LABELS[a]} for a in AREAS]}


def _subject(e) -> str:
    return e.get('name') or ''


def activityRows(**filters) -> list:
    """The feed as a table under the same filters, every page of it."""
    out, before = [], None
    while True:
        page = activity(limit=FEED_LIMIT_MAX, before=before, **filters)
        for e in page['entries']:
            if e['area'] == 'sleeves':
                out.append((e['at'], AREA_LABELS['sleeves'], e['action'], e['actor'], e['name'],
                            e['variant'], e['category'], e['label'],
                            sleeveRules.describe(e['rules']), e['sleeveId'], e['revision'],
                            ' | '.join(e['changes']),
                            '; '.join('{} {:.2f}%'.format(r['productId'], r['weight'] * 100.0)
                                      for r in e['products']), ''))
            else:
                held = '; '.join(p['productId'] for p in e.get('products') or [])
                out.append((e['at'], AREA_LABELS[e['area']], e['action'], e['actor'], _subject(e),
                            e.get('variant') or '', e.get('category') or '', e.get('label') or '', '',
                            e.get('productId') or e.get('scope') or e.get('sleeveId') or '',
                            e.get('revision') or '',
                            ' | '.join(e.get('changes') or []), held, e.get('note') or ''))
        if not page['next'] or not page['entries']:
            return out
        before = page['next']
