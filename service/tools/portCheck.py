#!/usr/bin/env python3
"""What a Cyrus host carries of the Proposal Tool, measured against this checkout.

Read only. Nothing on the host is written, and its databases are opened
read-only. Run it on the Cyrus machine with the service's own environment
set, so the SCENARIO_* variables point where the service points, and with
the service's virtualenv active, so the dependency check is about that
interpreter:

    python <ep>\\service\\tools\\portCheck.py --src H:\\cyrus-repo\\isg-cyrus-pmg\\src

<ep> is a copy of this repository's layout: on the Cyrus machine, the folder
porting/bundle/unbundle.py unpacks (PORTING_UPDATE.md step 1), since that
machine reaches GitHub only through a browser; anywhere else, a checkout.
--src is the directory that holds the host's cyrus_pmg package. It reports, for the package, the router block and
the page folder, which files are identical to this checkout, which differ,
which are missing and which only the host has; the schema versions stamped
in the host's two databases; the newest change the host's page and package
show; and the configuration and dependencies the update depends on. It exits
0 when the host is level with this checkout (host-owned files aside) and 1
otherwise, so it serves as the check after the update too. PORTING_UPDATE.md
is the guide that uses it.

Python 3.8 or later; standard library only.
"""

import argparse
import hashlib
import importlib
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EP = os.path.dirname(os.path.dirname(HERE))              # the unpacked bundle, or a checkout

PACKAGE = os.path.join('cyrus_pmg', 'pmgService', 'scenario')
ROUTER = os.path.join('cyrus_pmg', 'pmgService', 'dashboardRouter.py')
PAGE = os.path.join('cyrus_pmg', 'dashboard', 'proposalTool')

IGNORED_DIRS = {'__pycache__'}
IGNORED_FILES = {'.DS_Store', 'Thumbs.db'}
TEXT = {'.py', '.json', '.csv', '.html', '.css', '.js', '.md', '.txt'}

#: package files the host may legitimately hold a copy of its own
HOST_OWNED = {
    'fees.json': 'feeTools --accept records the delivered card here: keep the host copy if it was run there',
    'advisors.xlsx': 'keep the host copy if the directory was replaced there',
}
#: package files this checkout no longer has, and why
RETIRED = {
    'engine.py': 'retired with the live adapter (D126): delete it',
    'liveAdapter.py': 'retired with the live adapter (D126): delete it',
}

#: (D-number, what, how it shows) - modules in the package
PACKAGE_MARKS = [
    (89, 'sleeve editions', 'sleeveRules.py'),
    (119, 'the report as data', 'sheetDoc.py'),
    (121, 'the PowerPoint deck', 'pptWriter.py'),
    (125, 'the house fonts in the deck', 'houseFonts.py'),
    (148, 'the funding split', 'fundingSplit.py'),
    (155, 'the overlay rules', 'overlayRules.py'),
]
#: (D-number, what, a string the built page carries from then on)
PAGE_MARKS = [
    (96, 'custom fees', 'customFees'),
    (107, 'the Open an Account door', 'Open an Account'),
    (116, 'the register\'s saved views', 'proposals/views'),
    (123, 'one Download proposal button', 'Download proposal'),
    (149, 'the Uncalled Capital Allocation tab', 'Uncalled Capital Allocation'),
    (155, 'the Overlay Funding tab', 'Overlay Funding'),
    (156, 'the Sleeves view as cards and a table', 'pmg.repository.sleevesView'),
    (157, 'the New sleeve form', 'pmg.repository.newSleeveStart'),
    (158, 'the New edition form', 'pmg.repository.editionMode'),
]
#: names the router imports outside the block markers, and since when
ROUTER_IMPORTS = [
    ('import io', 'D123'), ('import json', 'D122'), ('import zipfile', 'D123'),
    ('validateCustomFees', 'D96'), ('fundingSplit', 'D148'), ('overlayRules', 'D155'),
]


def _digest(path):
    with open(path, 'rb') as fh:
        data = fh.read()
    if os.path.splitext(path)[1].lower() in TEXT:
        data = data.replace(b'\r\n', b'\n')             # a Windows checkout writes CRLF
    return hashlib.sha256(data).hexdigest()


def _files(root):
    out = {}
    if not os.path.isdir(root):
        return out
    for here, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for name in names:
            if name in IGNORED_FILES or name.endswith('.pyc'):
                continue
            full = os.path.join(here, name)
            out[os.path.relpath(full, root).replace(os.sep, '/')] = full
    return out


def compareTree(ours, theirs):
    """{'same': [...], 'differs': [...], 'missing': [...], 'extra': [...]} by relative path."""
    a, b = _files(ours), _files(theirs)
    result = {'same': [], 'differs': [], 'missing': [], 'extra': sorted(set(b) - set(a))}
    for rel in sorted(a):
        if rel not in b:
            result['missing'].append(rel)
        elif _digest(a[rel]) == _digest(b[rel]):
            result['same'].append(rel)
        else:
            result['differs'].append(rel)
    return result


#: the marker lines themselves - the block's own preamble names END in prose
_MARKER = re.compile(r'^#\s*=+\s*TRANSPLANT BLOCK (BEGIN|END)\b.*$', re.M)


def _block(text):
    """(the text between the marker lines, everything else), or (None, text)."""
    begins = [m for m in _MARKER.finditer(text) if m.group(1) == 'BEGIN']
    ends = [m for m in _MARKER.finditer(text) if m.group(1) == 'END']
    if not begins or not ends or ends[-1].start() < begins[0].end():
        return None, text
    start, stop = begins[0].end() + 1, ends[-1].start()
    return text[start:stop], text[:start] + text[stop:]


def _norm(text):
    return '\n'.join(line.rstrip() for line in text.replace('\r\n', '\n').split('\n')).strip()


def _imports(text, name):
    """Whether *text* imports *name*: 'import io' also matches 'import io, json'."""
    if name.startswith('import '):
        module = re.escape(name[len('import '):])
        return bool(re.search(r'^\s*import\s+[\w\s,]*\b' + module + r'\b', text, re.M))
    return bool(re.search(r'\b' + re.escape(name) + r'\b', text))


def checkRouter(src):
    path = os.path.join(src, ROUTER)
    if not os.path.isfile(path):
        return {'found': False, 'path': path}
    with open(path, encoding='utf-8') as fh:
        host = fh.read()
    with open(os.path.join(EP, 'service', ROUTER), encoding='utf-8') as fh:
        ours = fh.read()
    hostBlock, outside = _block(host)
    ourBlock, _ = _block(ours)
    report = {'found': True, 'path': path, 'markers': hostBlock is not None}
    if hostBlock is None:
        return report
    report['same'] = _norm(hostBlock) == _norm(ourBlock)
    report['routes'] = len(re.findall(r'^@router\.', hostBlock, re.M))
    report['ourRoutes'] = len(re.findall(r'^@router\.', ourBlock, re.M))
    report['imports'] = [(name, since, _imports(outside, name)) for name, since in ROUTER_IMPORTS]
    return report


def _pageText(pageDir):
    text = ''
    for rel in ('proposalTool.html', 'static/js/proposalTool.js'):
        path = os.path.join(pageDir, *rel.split('/'))
        if os.path.isfile(path):
            with open(path, encoding='utf-8', errors='replace') as fh:
                text += fh.read()
    return text


def _schemaVersion(path):
    """(stamped schema version, a row count) of a SQLite file, opened read-only."""
    if not os.path.isfile(path):
        return None
    uri = 'file:{}?mode=ro'.format(path.replace('\\', '/'))
    conn = sqlite3.connect(uri, uri=True)
    try:
        version = conn.execute("SELECT value FROM meta WHERE key = 'schemaVersion'").fetchone()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        counts = {}
        for table in ('sleeves', 'proposals', 'accountRequests', 'policies'):
            if table in tables:
                counts[table] = conn.execute('SELECT COUNT(*) FROM "{}"'.format(table)).fetchone()[0]
        return {'version': version[0] if version else None, 'counts': counts}
    except sqlite3.Error as exc:
        return {'error': str(exc)}
    finally:
        conn.close()


def storePaths(src):
    """Where the service will look: the variable when set, else the package's own default."""
    pkg = os.path.join(src, PACKAGE)
    var = os.path.normpath(os.path.join(pkg, '..', '..', '..', 'var'))
    return {
        'SCENARIO_SLEEVES_DB': os.getenv('SCENARIO_SLEEVES_DB', '') or os.path.join(var, 'sleeves.db'),
        'SCENARIO_REGISTER_DB': os.getenv('SCENARIO_REGISTER_DB', '') or os.path.join(var, 'proposals.db'),
    }


def _version(module):
    try:
        mod = importlib.import_module(module)
        return getattr(mod, '__version__', 'present')
    except Exception as exc:                          # noqa: BLE001 - reported, never raised
        return 'MISSING ({})'.format(type(exc).__name__)


def _say(title):
    print('\n' + title)
    print('-' * len(title))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--src', required=True,
                    help='the directory holding the host\'s cyrus_pmg package, e.g. H:\\cyrus-repo\\isg-cyrus-pmg\\src')
    ap.add_argument('--list', action='store_true', help='name every differing file, not only the count')
    args = ap.parse_args(argv)
    src = os.path.abspath(args.src)
    behind = False
    if not os.path.isdir(os.path.join(src, 'cyrus_pmg')):
        print('No cyrus_pmg package under {} - pass the directory that holds it.'.format(src))
        return 2

    print('Proposal Tool port check')
    print('  this checkout: {}'.format(EP))
    print('  host source:   {}'.format(src))

    # -- what the host shows of its own history ---------------------------
    pkg = os.path.join(src, PACKAGE)
    page = os.path.join(src, PAGE)
    pageText = _pageText(page)
    _say('Newest change the host carries')
    newest = 0
    for number, what, name in PACKAGE_MARKS:
        here = os.path.isfile(os.path.join(pkg, name))
        print('  [{}] D{:<4} {} ({})'.format('x' if here else ' ', number, what, name))
        if here:
            newest = max(newest, number)
    for number, what, needle in PAGE_MARKS:
        here = needle in pageText
        print('  [{}] D{:<4} {} (page)'.format('x' if here else ' ', number, what))
        if here:
            newest = max(newest, number)
    if newest:
        print('  => the host carries changes up to about D{}.'.format(newest))
    else:
        print('  => no change since D89 shows: the host predates the 10 September port, or has none.')

    # -- the package, the page and the router against this checkout ----------
    for label, ours, theirs in (('The package (pmgService/scenario)',
                                 os.path.join(EP, 'service', PACKAGE), pkg),
                                ('The page folder (dashboard/proposalTool)',
                                 os.path.join(EP, 'proposalTool'), page)):
        _say(label)
        diff = compareTree(ours, theirs)
        owned = [f for f in diff['differs'] if f in HOST_OWNED]
        differs = [f for f in diff['differs'] if f not in HOST_OWNED]
        print('  identical {}, differ {}, missing on the host {}, only on the host {}'.format(
            len(diff['same']), len(differs), len(diff['missing']), len(diff['extra'])))
        if differs or diff['missing']:
            behind = True
        shown = differs if args.list else differs[:12]
        for f in shown:
            print('    differs:  ' + f)
        if len(differs) > len(shown):
            print('    ... and {} more (--list names them all)'.format(len(differs) - len(shown)))
        for f in diff['missing']:
            print('    missing:  ' + f)
        for f in owned:
            print('    host-owned, differs: {} - {}'.format(f, HOST_OWNED[f]))
        for f in diff['extra']:
            print('    only on the host: {}{}'.format(f, ' - ' + RETIRED[f] if f in RETIRED else ''))
            if f in RETIRED:
                behind = True

    _say('The router (pmgService/dashboardRouter.py)')
    router = checkRouter(src)
    if not router['found']:
        print('  not found at {}'.format(router['path']))
        behind = True
    elif not router['markers']:
        print('  no TRANSPLANT BLOCK markers - the block was never pasted, or the markers were removed')
        behind = True
    else:
        print('  block: {} routes on the host, {} in this checkout; text {}'.format(
            router['routes'], router['ourRoutes'], 'identical' if router['same'] else 'DIFFERS'))
        if not router['same']:
            behind = True
        for name, since, present in router['imports']:
            print('  [{}] {:<20} imported outside the block (since {})'.format(
                'x' if present else ' ', name, since))
            if not present:
                behind = True

    _say('The stores (opened read-only)')
    expected = {'SCENARIO_SLEEVES_DB': 6, 'SCENARIO_REGISTER_DB': 5}
    for variable, path in storePaths(src).items():
        found = _schemaVersion(path)
        if found is None:
            print('  {}: no file at {}'.format(variable, path))
        elif 'error' in found:
            print('  {}: {} - {}'.format(variable, path, found['error']))
        else:
            print('  {}: {}'.format(variable, path))
            print('    schema version {} (this checkout migrates it to {} on first open); {}'.format(
                found['version'], expected[variable],
                ', '.join('{} {}'.format(n, t) for t, n in sorted(found['counts'].items())) or 'empty'))

    _say('Configuration and dependencies (this interpreter: {})'.format(sys.executable))
    adapter = os.getenv('SCENARIO_ADAPTER', '')
    print('  SCENARIO_ADAPTER = {!r}{}'.format(
        adapter, '' if adapter in ('baked', 'fixtures') else '  <- must be baked (fixtures for a demo); live is gone (D126)'))
    for gone in ('SCENARIO_BAKED_FALLBACK', 'SAA_ENGINE_PACKAGE'):
        if os.getenv(gone):
            print('  {} is set: nothing reads it since D126 - harmless, remove when convenient'.format(gone))
    for module, why in (('openpyxl', 'the workbook'), ('pptx', 'the deck (python-pptx; new since D121)'),
                        ('pandas', 'the strategic universe'), ('fastapi', 'the service')):
        found = _version(module)
        print('  {:<9} {:<20} {}'.format(module, str(found), why))
        if module == 'pptx' and str(found).startswith('MISSING'):
            behind = True

    print('\n' + ('The host is level with this checkout (host-owned files aside).' if not behind
                  else 'The host is behind this checkout: PORTING_UPDATE.md brings it up to date.'))
    return 1 if behind else 0


if __name__ == '__main__':
    sys.exit(main())
