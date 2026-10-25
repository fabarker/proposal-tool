#!/usr/bin/env python3
"""Turn a long sleeve table into the workbook the sleeve import reads.

The long table has one row per product held, for one sleeve, one
implementation type and one portfolio:

    Currency | Sleeve Name | Category | Implementation Type | Portfolio Name |
    Product Name | Product Weight

(or Long Name for the sleeve and Implementation / Product for the product;
the columns may come in any order, under title rows), and optionally Below
Minimum: the product held instead where the amount reaching the row's
product is below its minimum, by name, ID or ticker

Each (Currency, Sleeve Name, Implementation Type, Portfolio Name) holds a set
of products whose weights sum to 1. The import wants the library instead:
each sleeve once, with an edition for every distinct set of holdings and a
rule saying which portfolios that edition is for. This works that out:

  - Implementation types are matched loosely (Multi-Asset, ESG, Irish, US
    Onshore, All, or the tool's own names; several may share a cell with |).
    A row for one type beats an All row for the same portfolio.
  - Types that hold the same products wherever they meet become ONE shared
    sleeve, so an edit made under any of them shows in all. Only the
    portfolios a type offers are compared: US Onshore and Irish Onshore offer
    ex-Alts alone, so a Full row cannot set them apart. A type that differs
    anywhere gets its own copy.
  - Portfolios holding the same products share one edition, named by what
    picks them out (USD, GBP Moderate) and given the rules that say so. A
    rule cannot tell a portfolio from its ex-RAs twin, so those two must
    hold the same products.
  - When a sleeve's editions cover every portfolio in the table between
    them, the largest has no rules: it serves every portfolio the others do
    not. A sleeve holding the same products everywhere is that one edition.
  - Product names are looked up in the product file (the one loaded into the
    catalogue, or a --products-export of it), by name, ID, or the ticker of
    any of its listings - a row each.

Nothing is written unless the whole table converts. The import checks the
result again - categories, products, overlaps - and writes nothing if any
sleeve is refused.

    python3 convertSleeveTable.py SLEEVES.xlsx PRODUCTS.xlsx OUT.xlsx

OUT.xlsx gets a Sleeves sheet and a Rules sheet; OUT.csv gets OUT.csv and
OUT.rules.csv. Then:

    python -m cyrus_pmg.pmgService.scenario.sleeveTools --import OUT.xlsx --replace

Python 3.8 or later; openpyxl for workbooks.
"""

from __future__ import annotations

import csv
import difflib
import os
import re
import sys

# --- the tool's vocabulary: these must read as the service's own do --------

VARIANTS = ['PMG Multi-Asset Portfolio', 'PMG ESG', 'US Onshore', 'Irish Onshore']
ALL_TYPES = 'All'
TYPE_SEP = '|'
#: which allocation types each implementation type offers
VARIANT_ALLOCATIONS = {
    'PMG Multi-Asset Portfolio': ['Full', 'Core', 'ex-HFs', 'ex-Alts'],
    'PMG ESG': ['ex-Alts', 'ex-HFs'],
    'US Onshore': ['ex-Alts'],
    'Irish Onshore': ['ex-Alts'],
}
CURRENCIES = ('USD', 'GBP', 'CHF', 'EUR')
RISK_LEVELS = ('LowVol', 'Conservative', 'ConsMod', 'Moderate', 'ModAgg', 'Agg',
               'Higher Risk', 'All Equity')
RISK_LABELS = {'LowVol': 'Low Vol', 'ConsMod': 'Conservative-Moderate',
               'ModAgg': 'Moderate-Aggressive', 'Agg': 'Aggressive'}
ALLOCATION_TYPES = ('Full', 'Core', 'ex-Alts', 'ex-HFs')
EXCLUSION = 'ex-RAs'
#: an all-equity portfolio's allocation type, as a rule names it
NA = 'NA'

SEED_COLUMNS = ['Variant', 'Category', 'Sleeve', 'Edition', 'ProductId', 'Weight', 'BelowMinimum']
RULE_COLUMNS = ['Variant', 'Category', 'Sleeve', 'Edition', 'Currency', 'RiskLevel',
                'AllocationType']
SEED_SHEET, RULES_SHEET = 'Sleeves', 'Rules'
LABEL_MAX = 80
WEIGHT_TOLERANCE = 1e-6

#: the long table's columns: what each is found by, once lower-cased and
#: stripped of everything but letters and digits
COLUMNS = {
    'currency': ('currency', 'ccy', 'basecurrency'),
    'sleeve': ('sleevename', 'sleeve', 'longname', 'sleevelongname'),
    'category': ('category', 'catagory', 'sleevecategory'),
    'type': ('implementationtype', 'implementation', 'variant', 'type'),
    'portfolio': ('portfolioname', 'portfolio'),
    'product': ('productname', 'product', 'implementationproduct', 'productid'),
    'weight': ('productweight', 'weight'),
}
#: optional: the product held instead where the amount reaching a row's
#: product is below its minimum, by name, ID or ticker
OPTIONAL_COLUMNS = {
    'alternative': ('belowminimum', 'ifbelowminimum', 'alternative', 'alternativeproduct',
                    'belowminimumproduct'),
}
TYPE_WORDS = {
    'all': ALL_TYPES, 'alltypes': ALL_TYPES,
    'multiasset': VARIANTS[0], 'pmgmultiasset': VARIANTS[0], 'ma': VARIANTS[0],
    'multiassetportfolio': VARIANTS[0], 'pmgmultiassetportfolio': VARIANTS[0],
    'esg': VARIANTS[1], 'pmgesg': VARIANTS[1],
    'usonshore': VARIANTS[2], 'us': VARIANTS[2],
    'irish': VARIANTS[3], 'irishonshore': VARIANTS[3], 'ireland': VARIANTS[3],
}


class Problems(Exception):
    pass


def _key(text) -> str:
    return re.sub(r'[^a-z0-9]', '', str(text or '').lower())


def _words(text) -> str:
    return ' '.join(str(text if text is not None else '').split())


# --------------------------------------------------------------- reading ---

def _sheets(path):
    """(sheet name, rows) for every sheet of a workbook, or the one of a CSV."""
    if path.lower().endswith('.csv'):
        with open(path, newline='', encoding='utf-8-sig') as fh:
            yield os.path.basename(path), [row for row in csv.reader(fh)]
        return
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True, data_only=True)
    for name in book.sheetnames:
        yield name, [list(r) for r in book[name].iter_rows(values_only=True)]


def _findTable(path, wanted, prefer=None, optional=None):
    """The first sheet with a header row (within its first ten rows) naming
    every column in *wanted*: (sheet, {field: index}, header row number,
    rows below it). Columns in *optional* are found when they are there."""
    sheets = list(_sheets(path))
    if prefer:
        sheets.sort(key=lambda s: s[0].strip().lower() != prefer)
    for sheet, rows in sheets:
        for at, row in enumerate(rows[:10]):
            keys = [_key(c) for c in row]
            found = {}
            for field, names in list(wanted.items()) + list((optional or {}).items()):
                for name in names:
                    if name in keys:
                        found[field] = keys.index(name)
                        break
            if all(f in found for f in wanted):
                return sheet, found, at + 1, rows[at + 1:]
    raise Problems(['{}: no sheet has a header naming {}'.format(
        path, ', '.join(names[0] for names in wanted.values()))])


def readProducts(path):
    """name, ticker and ID -> product ID, from a product file; the keys more
    than one product answers to; every ID; each key as the file writes it."""
    optional = {'ticker': ('ticker',)}
    # the ticker each product has in a base currency, where it has its own
    optional.update({'ticker' + c: ('ticker' + c.lower(), c.lower() + 'ticker') for c in CURRENCIES})
    _, cols, _, rows = _findTable(path, {'id': ('productid',), 'name': ('name', 'productname')},
                                  prefer='products', optional=optional)
    byKey, clash, ids, shown = {}, set(), [], {}
    for row in rows:
        cell = {f: _words(row[i]) if i < len(row) else '' for f, i in cols.items()}
        if not cell['id']:
            continue
        ids.append(cell['id'])
        tickers = [v for f, v in cell.items() if f.startswith('ticker')]
        # a product on a row per base currency may carry it in its name:
        # 'Green Bond ETF (GBP)' is the Green Bond ETF too
        bare = re.sub(r'\s*\((?:{})\)$'.format('|'.join(CURRENCIES)), '', cell['name'],
                      flags=re.IGNORECASE)
        for text in [cell['id'], cell['name'], bare] + tickers:
            k = text.casefold()
            if not k or k == '\u2014':
                continue
            if k in byKey and byKey[k] != cell['id']:
                clash.add(k)
            byKey[k] = cell['id']
            shown.setdefault(k, text)
    return byKey, clash, ids, shown


#: how a portfolio name may say its allocation type, and whether real assets
#: are excluded, once lower-cased with hyphens read as spaces
ALLOCATION_WORDS = [('ex-Alts', r'ex alts'), ('ex-HFs', r'ex hfs'), ('Full', r'full'),
                    ('Core', r'core')]
REAL_ASSETS = re.compile(r' (ex|exc|excl|excluding|inc|incl|including) ras? ')
#: risk levels by the words a name may use for them
RISK_WORDS = dict({_key(r): r for r in RISK_LEVELS},
                  **{_key(label): r for r, label in RISK_LABELS.items()},
                  lowvolatility='LowVol')


def parsePortfolio(name, currency):
    """(currency, risk level, allocation type, ex-RAs) for a portfolio name in
    any order: 'GBP Moderate ex-HFs ex-RAs', 'GBP ex HFs ex RA Moderate',
    'Full inc RA Low Volatility', 'GBP All Equity'; the currency in front
    is optional."""
    text = ' {} '.format(_words(name).lower().replace('-', ' '))
    first = text.split()[0].upper() if text.strip() else ''
    if first in CURRENCIES:
        if first != currency:
            raise ValueError('portfolio {!r} is not in {}'.format(name, currency))
        text = text.replace(' {} '.format(first.lower()), ' ', 1)
    exclude = False
    found = REAL_ASSETS.search(text)
    if found:
        exclude = found.group(1).startswith('ex')
        text = text[:found.start()] + ' ' + text[found.end():]
    allocation = None
    for code, words in ALLOCATION_WORDS:
        hit = re.search(' {} '.format(words), text)
        if hit:
            allocation = code
            text = text[:hit.start()] + ' ' + text[hit.end():]
            break
    risk = RISK_WORDS.get(_key(text))
    if risk is None:
        raise ValueError('portfolio {!r}: {!r} is not a risk level'.format(name, ' '.join(text.split())))
    if allocation is None and risk != 'All Equity':
        raise ValueError('portfolio {!r} has no allocation type'.format(name))
    if allocation is not None and risk == 'All Equity':
        raise ValueError('portfolio {!r}: an all-equity portfolio has no allocation type'.format(name))
    return currency, risk, allocation or NA, exclude and allocation is not None


def parseTypes(cell):
    out = []
    for part in str(cell or '').split(TYPE_SEP):
        if not part.strip():
            continue
        word = TYPE_WORDS.get(_key(part)) or next(
            (v for v in VARIANTS if _key(v) == _key(part)), None)
        if word is None:
            raise ValueError('{!r} is not an implementation type'.format(part.strip()))
        out.extend(VARIANTS if word == ALL_TYPES else [word])
    if not out:
        raise ValueError('no implementation type')
    return [v for v in VARIANTS if v in out]


def readTable(path, productsBy, clash, shown):
    """The long table as {(category, sleeve): {type: {triple: holdings}}},
    the order sleeves first appear in, and what was read."""
    sheet, cols, headerRow, rows = _findTable(path, COLUMNS, optional=OPTIONAL_COLUMNS)
    problems, stats = [], {'rows': 0, 'zero': 0, 'overridden': 0, 'sheet': sheet}
    groups, order = {}, []          # (cat, sleeve, typesKey, portfolio) -> [(pid, w)]
    altOf = {}                      # ((cat, sleeve, typesKey, portfolio), pid) -> alternative
    for at, row in enumerate(rows, start=headerRow + 1):
        cell = {f: (row[i] if i < len(row) else None) for f, i in cols.items()}
        if not any(_words(v) for v in cell.values()):
            continue
        stats['rows'] += 1
        where = 'row {}'.format(at)
        try:
            currency = _words(cell['currency']).upper()
            if currency not in CURRENCIES:
                raise ValueError('{!r} is not a currency'.format(cell['currency']))
            sleeve, category = _words(cell['sleeve']), _words(cell['category'])
            if not sleeve or not category:
                raise ValueError('needs a sleeve name and a category')
            types = parseTypes(cell['type'])
            portfolio = parsePortfolio(cell['portfolio'], currency)
            product = _words(cell['product'])
            pid = productsBy.get(product.casefold())
            if product.casefold() in clash:
                raise ValueError('more than one product is called {!r}; use its ID'.format(product))
            if pid is None:
                near = difflib.get_close_matches(product.casefold(), list(productsBy), n=1)
                raise ValueError('product {!r} is not in the product file{}'.format(
                    product, '; did you mean {!r}?'.format(shown[near[0]]) if near else ''))
            try:
                weight = cell['weight']
                if isinstance(weight, str) and weight.strip().endswith('%'):
                    weight = float(weight.strip()[:-1]) / 100.0
                weight = float(weight)
            except (TypeError, ValueError):
                raise ValueError('weight {!r} is not a number'.format(cell['weight']))
            if weight < 0:
                raise ValueError('weight {} is below zero'.format(weight))
            alternative = _words(cell.get('alternative'))
            altId = productsBy.get(alternative.casefold()) if alternative else ''
            if alternative and alternative.casefold() in clash:
                raise ValueError('more than one product is called {!r}; use its ID'.format(alternative))
            if alternative and altId is None:
                near = difflib.get_close_matches(alternative.casefold(), list(productsBy), n=1)
                raise ValueError('the product to hold below the minimum, {!r}, is not in the product '
                                 'file{}'.format(alternative, '; did you mean {!r}?'.format(
                                     shown[near[0]]) if near else ''))
            if altId and altId == pid:
                raise ValueError('{} cannot be its own alternative below its minimum'.format(product))
        except ValueError as exc:
            problems.append('{}: {}'.format(where, exc))
            continue
        if weight == 0:
            stats['zero'] += 1
            continue
        key = (category, sleeve, tuple(types), portfolio)
        if key not in groups:
            groups[key] = []
            order.append(key)
        if any(p == pid for p, _, _ in groups[key]):
            problems.append('{}: {} appears twice in {} / {} / {}'.format(
                where, product, sleeve, ', '.join(types), _portfolioText(portfolio)))
            continue
        groups[key].append((pid, weight, at))
        altOf[(key, pid)] = altId or ''

    # each group's weights sum to 1
    for key in order:
        total = sum(w for _, w, _ in groups[key])
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            category, sleeve, types, portfolio = key
            problems.append('rows {}: {} / {} / {} sums to {:.6g}, not 1'.format(
                _rowSpan(groups[key]), sleeve, _typesWords(types), _portfolioText(portfolio), total))

    # per sleeve, type and portfolio, the holdings: the row naming the
    # fewest types wins (ESG beats All), and two equal claims must agree
    claims = {}
    for key in order:
        category, sleeve, types, portfolio = key
        holdings = tuple(sorted((p, round(w, 9), altOf.get((key, p), '')) for p, w, _ in groups[key]))
        for t in types:
            claims.setdefault((category, sleeve, t, portfolio), []).append(
                (len(types), holdings, groups[key]))
    library, sleeveOrder = {}, []
    for (category, sleeve, t, portfolio), cs in claims.items():
        best = min(n for n, _, _ in cs)
        top = [c for c in cs if c[0] == best]
        if len({h for _, h, _ in top}) > 1:
            problems.append('rows {}: {} under {} for {} is given two different sets of products'.format(
                ', '.join(_rowSpan(g) for _, _, g in top), sleeve, t, _portfolioText(portfolio)))
            continue
        if len(cs) > len(top):
            stats['overridden'] += 1
        triple = portfolio[:3]
        byType = library.setdefault((category, sleeve), {})
        if (category, sleeve) not in sleeveOrder:
            sleeveOrder.append((category, sleeve))
        cells = byType.setdefault(t, {})
        if triple in cells and cells[triple][0] != top[0][1]:
            problems.append('rows {} and {}: {} under {}: {} and its ex-RAs twin hold different products, '
                            'and a rule cannot tell them apart'.format(
                                _rowSpan(cells[triple][1]), _rowSpan(top[0][2]), sleeve, t,
                                _portfolioText(triple)))
            continue
        cells[triple] = (top[0][1], top[0][2])
    sleeveOrder.sort(key=lambda cs: order.index(next(k for k in order if k[:2] == cs)))
    if problems:
        raise Problems(problems)
    return library, sleeveOrder, stats


def _rowSpan(group):
    rows = sorted(r for _, _, r in group)
    return '{}-{}'.format(rows[0], rows[-1]) if len(rows) > 1 else str(rows[0])


def _portfolioText(p):
    currency, risk, allocation = p[:3]
    words = [currency, RISK_LABELS.get(risk, risk)] + ([] if allocation == NA else [allocation])
    if len(p) > 3 and p[3]:
        words.append(EXCLUSION)
    return ' '.join(words)


def _typesWords(types):
    return 'All types' if set(types) == set(VARIANTS) else ', '.join(types)


# ------------------------------------------------------------ converting ---

def _offers(variant, triple):
    return triple[2] == NA or triple[2] in VARIANT_ALLOCATIONS[variant]


def _shareGroups(byType):
    """Types that agree on every portfolio both offer, as lists in VARIANTS
    order, each with the holdings they serve between them."""
    groups = []                     # [[types], {triple: (holdings, rows)}]
    for t in VARIANTS:
        if t not in byType:
            continue
        mine = byType[t]
        for types, merged in groups:
            if all(mine.get(tr, (None,))[0] == merged.get(tr, (None,))[0]
                   for tr in set(mine) | set(merged)
                   if _offers(t, tr) and all(_offers(o, tr) for o in types)):
                types.append(t)
                for tr, v in mine.items():
                    merged.setdefault(tr, v)
                break
        else:
            groups.append([[t], dict(mine)])
    return groups


def _rules(triples):
    """Rules naming exactly *triples*: each a currency x risk x allocation
    product, values in the vocabulary's order."""
    byCA = {}
    for c, r, a in triples:
        byCA.setdefault((c, a), set()).add(r)
    byCR = {}
    for (c, a), risks in byCA.items():
        byCR.setdefault((c, frozenset(risks)), set()).add(a)
    byRA = {}
    for (c, risks), allocs in byCR.items():
        byRA.setdefault((risks, frozenset(allocs)), set()).add(c)
    aOrder = list(ALLOCATION_TYPES) + [NA]
    out = []
    for (risks, allocs), currencies in byRA.items():
        out.append({'currency': [c for c in CURRENCIES if c in currencies],
                    'riskLevel': [r for r in RISK_LEVELS if r in risks],
                    'allocationType': [a for a in aOrder if a in allocs]})
    out.sort(key=lambda r: (CURRENCIES.index(r['currency'][0]), RISK_LEVELS.index(r['riskLevel'][0])))
    return out


def _label(triples, scope):
    """The fewest words that pick *triples* out of *scope*: its currencies,
    risk levels or allocation types, or a pair of them, or all three; ''
    when nothing short does."""
    fields = (0, 1, 2)
    for chosen in ((0,), (1,), (2,), (0, 1), (0, 2), (1, 2), fields):
        values = [{t[f] for t in triples} for f in fields]
        if {t for t in scope if all(t[f] in values[f] for f in chosen)} != triples:
            continue
        words = []
        if 0 in chosen:
            words.append('/'.join(c for c in CURRENCIES if c in values[0]))
        if 1 in chosen:
            words.append('/'.join(RISK_LABELS.get(r, r) for r in RISK_LEVELS if r in values[1]))
        if 2 in chosen:
            allocs = [x for x in ALLOCATION_TYPES if x in values[2]]
            words.append('/'.join(allocs) if allocs else 'All Equity')
        label = ' '.join(words)
        if len(label) <= LABEL_MAX:
            return label
    return ''


def convert(library, sleeveOrder):
    """(seed rows, rule rows, one line per sleeve)."""
    seen = {'currency': set(), 'riskLevel': set(), 'allTriples': set()}
    for byType in library.values():
        for cells in byType.values():
            for tr in cells:
                seen['currency'].add(tr[0])
                seen['riskLevel'].add(tr[1])
                seen['allTriples'].add(tr)
    seedRows, ruleRows, lines = [], [], []
    for category, sleeve in sleeveOrder:
        said = []
        for types, merged in _shareGroups(library[(category, sleeve)]):
            variant = ALL_TYPES if set(types) == set(VARIANTS) else TYPE_SEP.join(types)
            editions = {}
            for tr, (holdings, rows) in merged.items():
                editions.setdefault(holdings, {'triples': set(), 'rows': rows})['triples'].add(tr)
            scope = {tr for tr in seen['allTriples'] if any(_offers(t, tr) for t in types)}
            ranked = sorted(editions.items(), key=lambda kv: (
                -len(kv[1]['triples']),
                min((CURRENCIES.index(t[0]), RISK_LEVELS.index(t[1])) for t in kv[1]['triples'])))
            # when the editions cover every portfolio between them, the
            # largest is the one with no rules: it serves the rest
            fallback = ranked[0][0] if scope <= set(merged) else None
            made, labels = [], set()
            for n, (holdings, e) in enumerate(ranked, start=1):
                if holdings == fallback:
                    made.append(('', holdings, e['rows'], []))
                    continue
                label = _label(e['triples'], scope) or 'Edition {}'.format(n)
                base, k = label, 2
                while label.lower() in labels:
                    label, k = '{} ({})'.format(base, k), k + 1
                labels.add(label.lower())
                made.append((label, holdings, e['rows'], _rules(e['triples'])))
            made.sort(key=lambda m: m[0] != '')
            for label, holdings, rows, rules in made:
                altById = {held[0]: held[2] for held in holdings}
                for pid, weight, _ in rows:
                    seedRows.append([variant, category, sleeve, label, pid, weight,
                                     altById.get(pid, '')])
                for rule in rules:
                    ruleRows.append([variant, category, sleeve, label,
                                     TYPE_SEP.join(rule['currency']),
                                     TYPE_SEP.join(rule['riskLevel']),
                                     TYPE_SEP.join(rule['allocationType'])])
            named = [m[0] for m in made if m[0]]
            if not named:
                what = 'one edition, every portfolio'
            elif made[0][0] == '':
                what = '{} for the rest'.format(', '.join(named))
            else:
                what = ', '.join(named) + '; not offered for other portfolios'
            said.append('{}: {}'.format(_typesWords(types), what))
        lines.append('{} ({})\n      {}'.format(sleeve, category, '\n      '.join(said)))
    return seedRows, ruleRows, lines


# --------------------------------------------------------------- writing ---

def write(path, seedRows, ruleRows):
    if path.lower().endswith('.csv'):
        base, ext = os.path.splitext(path)
        for target, head, rows in ((path, SEED_COLUMNS, seedRows),
                                   (base + '.rules' + ext, RULE_COLUMNS, ruleRows)):
            with open(target, 'w', newline='', encoding='utf-8') as fh:
                w = csv.writer(fh)
                w.writerow(head)
                w.writerows(rows)
        return [path, base + '.rules' + ext]
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = SEED_SHEET
    sheet.append(SEED_COLUMNS)
    for row in seedRows:
        sheet.append(row)
    rules = book.create_sheet(RULES_SHEET)
    rules.append(RULE_COLUMNS)
    for row in ruleRows:
        rules.append(row)
    book.save(path)
    return [path]


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0 if argv and argv[0] in ('-h', '--help') else 2
    tablePath, productsPath, outPath = argv
    if os.path.abspath(outPath) in (os.path.abspath(tablePath), os.path.abspath(productsPath)):
        print('the output must be a new file, not one of the inputs')
        return 2
    try:
        productsBy, clash, ids, shown = readProducts(productsPath)
        library, sleeveOrder, stats = readTable(tablePath, productsBy, clash, shown)
    except Problems as exc:
        problems = exc.args[0]
        print('cannot convert: {} problem{}'.format(len(problems), '' if len(problems) == 1 else 's'))
        for p in problems[:60]:
            print('  ' + p)
        if len(problems) > 60:
            print('  ... and {} more'.format(len(problems) - 60))
        print('nothing was written')
        return 1
    seedRows, ruleRows, lines = convert(library, sleeveOrder)
    written = write(outPath, seedRows, ruleRows)
    print('read {} rows from {} (sheet {}), {} products in {}'.format(
        stats['rows'], tablePath, stats['sheet'], len(ids), productsPath))
    if stats['zero']:
        print('  skipped {} row(s) with a weight of 0'.format(stats['zero']))
    if stats['overridden']:
        print('  {} portfolio(s) where a row for one type replaced the All row'.format(stats['overridden']))
    editions = len({tuple(r[:4]) for r in seedRows})
    print('{} sleeves, {} editions, {} rules:'.format(len(lines), editions, len(ruleRows)))
    for line in lines:
        print('  ' + line)
    print('wrote ' + ' and '.join(written))
    return 0


if __name__ == '__main__':
    sys.exit(main())
