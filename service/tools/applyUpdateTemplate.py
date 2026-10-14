#!/usr/bin/env python3
"""Apply the Proposal Tool update to the Cyrus host, and undo it if need be.

Put this file in the folder that holds unbundle.py and the proposal-tool
folder it unpacked (H:\\pt-update), then run it from anywhere:

    python H:\\pt-update\\apply-update.py         look: what it would do; changes nothing
    python H:\\pt-update\\apply-update.py go      back up, then update - the service stopped
    python H:\\pt-update\\apply-update.py undo    put back everything the last 'go' changed

'go' is PORTING_UPDATE.md steps 5 to 8 and the database upgrade of step 10,
done the way the host needs them:

  - backs up the package, the page folder, the router and both databases into
    a backup-<date> folder beside this file, and checks the copies;
  - copies the package over the host's, keeping the host's own advisor
    directory, deletes the files the tool retired, and re-applies the host's
    one-line edit to bake.py (the bake lives one folder higher on Cyrus);
  - replaces the endpoint block in the host's dashboardRouter.py - found by its
    first and last lines, since the host's copy has no markers - adds the
    markers around it, and adds the imports the new block needs and the host
    does not have; nothing else in the router is touched;
  - copies the page folder over the host's;
  - opens both databases with the new code, which upgrades them, and checks
    that no sleeve and no proposal was lost;
  - checks that every module imports, the deck finds its fonts and the router
    carries the new endpoints.

It refuses to start while the service answers on its port, or when anything
it would rely on is not as expected, and says what. Every run saves what it
printed as apply-report.txt beside this file.

Built by service/tools/buildApplyUpdate.py from the paste bundle of commit
@@COMMIT@@, for a host ported from commit @@BASE@@. Python 3.8 or later,
standard library only.
"""
import argparse
import ast
import datetime
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import textwrap

SOURCE = '@@COMMIT@@'
BASE = '@@BASE@@'
DEFAULT_SRC = r'H:\cyrus-repo\isg-cyrus-pmg\src'
HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.join('cyrus_pmg', 'pmgService', 'scenario')
ROUTER = os.path.join('cyrus_pmg', 'pmgService', 'dashboardRouter.py')
PAGE = os.path.join('cyrus_pmg', 'dashboard', 'proposalTool')
EP_PACKAGE = 'service/cyrus_pmg/pmgService/scenario/'
EP_ROUTER = 'service/cyrus_pmg/pmgService/dashboardRouter.py'
EP_PAGE = 'proposalTool/'
SKIP_DIRS = {'__pycache__'}
SKIP_FILES = {'.DS_Store', 'Thumbs.db'}
#: package files that are the host's own: kept when the host has one
HOST_OWNED = {'advisors.xlsx'}
SERVICE_PORT = 8002
#: the host keeps the bake one folder higher than the repository does
BAKE_FROM = "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')"
BAKE_TO = "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"
FACES = ['GS Sans', 'GS Sans Condensed', 'GS Sans Light']
#: modules the new block may import from when the host lacks a name
IMPORTABLE = ('cyrus_pmg.pmgService.scenario', 'fastapi')

_report = []


def say(text=''):
    print(text)
    _report.append(text)


def _saveReport():
    path = os.path.join(HERE, 'apply-report.txt')
    try:
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(_report) + '\n')
        print('(saved to {})'.format(path))
    except OSError as exc:
        print('(could not save the report: {})'.format(exc))


# ------------------------------------------------------------------ files ---

def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _walk(root):
    """{relative path with forward slashes: absolute path} under root."""
    out = {}
    for here, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            if name in SKIP_FILES or name.endswith('.pyc'):
                continue
            path = os.path.join(here, name)
            out[os.path.relpath(path, root).replace(os.sep, '/')] = path
    return out


def _copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)


def _folderPlan(epRoot, hostRoot, manifestPrefix, owned=()):
    """[(action, rel)] for copying the bundle's folder over the host's."""
    plan = []
    for rel in sorted(k[len(manifestPrefix):] for k in MANIFEST if k.startswith(manifestPrefix)):
        mine = os.path.join(epRoot, *rel.split('/'))
        theirs = os.path.join(hostRoot, *rel.split('/'))
        if rel in owned and os.path.isfile(theirs):
            plan.append(('keep', rel))
        elif not os.path.isfile(theirs):
            plan.append(('new', rel))
        elif _sha(mine) == _sha(theirs):
            plan.append(('same', rel))
        else:
            plan.append(('replace', rel))
    return plan


# ----------------------------------------------------------------- router ---

def _readText(path):
    """(lines without line ends, the end each line had, BOM) of a text file."""
    with open(path, 'rb') as fh:
        raw = fh.read()
    bom = raw.startswith(b'\xef\xbb\xbf')
    text = raw[3:].decode('utf-8') if bom else raw.decode('utf-8')
    parts = text.split('\n')
    lines = [p[:-1] if p.endswith('\r') else p for p in parts]
    ends = ['\r' if p.endswith('\r') else '' for p in parts]
    return lines, ends, bom


def _writeText(path, lines, ends, bom):
    text = '\n'.join(line + end for line, end in zip(lines, ends))
    data = text.encode('utf-8')
    with open(path, 'wb') as fh:
        fh.write((b'\xef\xbb\xbf' if bom else b'') + data)


def _whole(lines):
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:16]


def _blockRange(lines):
    """(start, end) of the endpoint block: the `_XLSX = ` line to the end of
    exportScenario - the same range edits-check.py measured."""
    starts = [i for i, l in enumerate(lines) if l.startswith('_XLSX = ')]
    exports = [i for i, l in enumerate(lines) if l.startswith('def exportScenario')]
    if len(starts) != 1 or len(exports) != 1 or exports[0] < starts[0]:
        return None
    end = exports[0] + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][:1] in (' ', '\t')):
        end += 1
    while end > exports[0] and not lines[end - 1].strip():
        end -= 1
    return starts[0], end


def _markers(lines):
    begin = [i for i, l in enumerate(lines) if l.startswith('# ===== TRANSPLANT BLOCK BEGIN')]
    end = [i for i, l in enumerate(lines) if l.startswith('# ===== TRANSPLANT BLOCK END')]
    return begin, end


def _bindings(tree, skip=None):
    """{name: how it is bound} for the module's top-level names, outside the
    skipped (first, last) line range."""
    out = {}

    def imports(node):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split('.')[0]] = ('import', a.name, a.asname)
        elif isinstance(node, ast.ImportFrom):
            module = '.' * node.level + (node.module or '')
            for a in node.names:
                out[a.asname or a.name] = ('from', module, a.name, a.asname)

    for node in tree.body:
        if skip and skip[0] <= node.lineno <= skip[1]:
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = ('defined',)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        out[n.id] = ('defined',)
        else:
            for sub in ast.walk(node):              # an import under a top-level if or try
                imports(sub)
    return out


def _importLines(specs):
    """Import statements for [(name, spec)], one per module."""
    plain = sorted({s[1] if not s[2] else '{} as {}'.format(s[1], s[2])
                    for _, s in specs if s[0] == 'import'})
    lines = ['import ' + p for p in plain]
    modules = {}
    for _, s in specs:
        if s[0] == 'from':
            modules.setdefault(s[1], []).append(s[2] if not s[3] else '{} as {}'.format(s[2], s[3]))
    for module in sorted(modules):
        names = ', '.join(sorted(modules[module]))
        line = 'from {} import {}'.format(module, names)
        if len(line) > 99:
            head = 'from {} import ('.format(module)
            line = '\n'.join(textwrap.wrap(head + names + ')', 99, subsequent_indent=' ' * len(head),
                                           break_on_hyphens=False))
        lines += line.split('\n')
    return lines


def routerPlan(hostPath, epPath):
    """What the router step would do, or a reason it cannot."""
    try:
        lines, ends, bom = _readText(hostPath)
    except (OSError, UnicodeDecodeError) as exc:
        return {'problem': 'cannot read the router: {}'.format(exc)}
    epLines = _readText(epPath)[0]
    epRange = _blockRange(epLines)
    newBlock = epLines[epRange[0]:epRange[1]]
    hostRange = _blockRange(lines)
    if hostRange is None:
        return {'problem': 'no endpoint block found: no single line starts "_XLSX = " followed '
                           'by "def exportScenario"'}
    start, end = hostRange
    block = lines[start:end]
    if block == newBlock:
        return {'state': 'current', 'range': hostRange, 'lines': lines}
    if _whole(block) != OLD_BLOCK:
        return {'problem': 'the endpoint block (lines {}-{}) is neither the one ported from {} nor '
                           'the new one: it was edited, so it is left alone'.format(start + 1, end, BASE)}
    try:
        tree = ast.parse('\n'.join(lines))
        epTree = ast.parse('\n'.join(epLines))
        used = {n.id for n in ast.walk(ast.parse('\n'.join(newBlock))) if isinstance(n, ast.Name)}
    except SyntaxError as exc:
        return {'problem': 'a router does not parse: {}'.format(exc)}
    have = _bindings(tree, skip=(start + 1, end))
    offered = _bindings(epTree, skip=(epRange[0] + 1, epRange[1]))
    missing, refused = [], []
    for name in sorted(used & set(offered)):
        if name in have:
            continue
        spec = offered[name]
        if spec[0] == 'import' or (spec[0] == 'from' and spec[1].startswith(IMPORTABLE)):
            missing.append((name, spec))
        else:
            refused.append('{} (from {})'.format(name, spec[1] if len(spec) > 1 else '?'))
    if refused:
        return {'problem': 'the new block needs names the host router does not have and this '
                           'script must not add: ' + ', '.join(refused)}
    begin, finish = _markers(lines)
    epBegin, epFinish = _markers(epLines)
    wrap = not begin and not finish and len(epBegin) == 1 and len(epFinish) == 1
    after = max([n.end_lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
                 and n.lineno - 1 < start] or [0])
    return {'state': 'replace', 'range': hostRange, 'newBlock': newBlock, 'lines': lines,
            'ends': ends, 'bom': bom, 'missing': missing,
            'importLines': _importLines(missing) if missing else [],
            'insertAt': after if after else start,
            'markers': (epLines[epBegin[0]], epLines[epFinish[0]]) if wrap else None}


def applyRouter(hostPath, plan):
    lines, ends = list(plan['lines']), list(plan['ends'])
    crlf = ends.count('\r') * 2 > len(ends)
    eol = '\r' if crlf else ''
    start, end = plan['range']
    replacement = list(plan['newBlock'])
    if plan['markers']:
        replacement = [plan['markers'][0]] + replacement + ['', plan['markers'][1]]
    lines[start:end] = replacement
    ends[start:end] = [eol] * len(replacement)
    if plan['importLines']:
        added = (['# names the Proposal Tool endpoint block needs since {}'.format(SOURCE)]
                 + plan['importLines'])
        at = plan['insertAt']
        lines[at:at] = added
        ends[at:at] = [eol] * len(added)
    compile('\n'.join(lines), hostPath, 'exec')
    _writeText(hostPath, lines, ends, plan['bom'])
    check = _readText(hostPath)[0]
    got = _blockRange(check)
    if got is None or check[got[0]:got[1]] != plan['newBlock']:
        raise RuntimeError('the router was written but its block does not read back as the new one')


# -------------------------------------------------------------- databases ---

def dbPaths(src):
    var = os.path.join(src, 'var')
    return [('the sleeve library', 'SCENARIO_SLEEVES_DB',
             os.path.abspath(os.getenv('SCENARIO_SLEEVES_DB', '') or os.path.join(var, 'sleeves.db')),
             'sleeves'),
            ('the register', 'SCENARIO_REGISTER_DB',
             os.path.abspath(os.getenv('SCENARIO_REGISTER_DB', '') or os.path.join(var, 'proposals.db')),
             'proposals')]


def dbState(path, table):
    """(schema version, rows in the table) read without writing, or an error."""
    if not os.path.isfile(path):
        return None
    conn = sqlite3.connect('file:{}?mode=ro'.format(path.replace('\\', '/')), uri=True)
    try:
        version = conn.execute("SELECT value FROM meta WHERE key = 'schemaVersion'").fetchone()
        rows = conn.execute('SELECT COUNT(*) FROM "{}"'.format(table)).fetchone()[0]
        return {'version': version[0] if version else '?', 'rows': rows}
    except sqlite3.Error as exc:
        return {'error': str(exc)}
    finally:
        conn.close()


# ----------------------------------------------------------------- checks ---

def _serviceRunning():
    try:
        with socket.create_connection(('127.0.0.1', SERVICE_PORT), timeout=1):
            return True
    except OSError:
        return False


def _python(src, code, extraEnv=None, timeout=600):
    env = dict(os.environ)
    env['PYTHONPATH'] = src + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    env.update(extraEnv or {})
    try:
        out = subprocess.run([sys.executable, '-c', code], cwd=src, env=env, capture_output=True,
                             text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, 'did not finish in {} seconds'.format(timeout)
    if out.returncode != 0:
        tail = [l for l in out.stderr.strip().splitlines() if l.strip()][-3:]
        return False, ' | '.join(tail) or 'exit code {}'.format(out.returncode)
    return True, out.stdout.strip()


_MODULES = '''
import importlib, pkgutil
import cyrus_pmg.pmgService.scenario as p
bad = []
for m in pkgutil.iter_modules(p.__path__):
    try:
        importlib.import_module('cyrus_pmg.pmgService.scenario.' + m.name)
    except Exception as exc:
        bad.append('{}: {}: {}'.format(m.name, type(exc).__name__, exc))
print('\\n'.join(bad) or 'OK')
'''
_FONTS = 'import cyrus_pmg.pmgService.scenario.houseFonts as h; print(sorted(h.FACES))'
_ROUTES = ("from cyrus_pmg.pmgService.dashboardRouter import router; "
           "print(len([r for r in router.routes if getattr(r, 'path', '').startswith('/scenario')]))")
_UPGRADE = '''
from cyrus_pmg.pmgService.scenario import proposalRegister, sleeveRepo
c = sleeveRepo.connection()
c.close()
proposalRegister.describe()
print('OK')
'''


def _version(module):
    try:
        mod = __import__(module)
        return getattr(mod, '__version__', 'present')
    except Exception:                                 # noqa: BLE001 - reported
        return None


# ------------------------------------------------------------------ survey ---

def survey(src, ep):
    """Everything 'go' relies on, measured without writing. (plan, problems)."""
    problems = []
    plan = {'src': src, 'ep': ep}
    say('Proposal Tool update to {} - host ported from {}'.format(SOURCE, BASE))
    say('Host: {}'.format(src))
    say('Bundle: {}'.format(ep))
    say('')

    bad = [rel for rel, want in sorted(MANIFEST.items())
           if not os.path.isfile(os.path.join(ep, *rel.split('/')))
           or _sha(os.path.join(ep, *rel.split('/'))) != want]
    if bad:
        problems.append('the unpacked bundle is not the one for {}: {} of its {} files differ or are '
                        'missing (first: {}). Delete the proposal-tool folder and run unbundle.py '
                        'again'.format(SOURCE, len(bad), len(MANIFEST), bad[0]))
        say('== The bundle: NOT as built ({} files differ)'.format(len(bad)))
        return plan, problems
    say('== The bundle: all {} files as built'.format(len(MANIFEST)))

    hostPackage, hostPage, hostRouter = (os.path.join(src, PACKAGE), os.path.join(src, PAGE),
                                         os.path.join(src, ROUTER))
    for what, path in (('package', hostPackage), ('page folder', hostPage)):
        if not os.path.isdir(path):
            problems.append('no {} at {}'.format(what, path))
    if not os.path.isfile(hostRouter):
        problems.append('no router at {}'.format(hostRouter))
    if problems:
        return plan, problems

    epPackage = os.path.join(ep, *EP_PACKAGE.strip('/').split('/'))
    package = _folderPlan(epPackage, hostPackage, EP_PACKAGE, HOST_OWNED)
    retired = [r for r in RETIRED if os.path.isfile(os.path.join(hostPackage, r))]
    plan['package'] = package
    plan['retired'] = retired
    counts = {k: sum(1 for a, _ in package if a == k) for k in ('new', 'replace', 'same', 'keep')}
    say('')
    say('== The package ({})'.format(hostPackage))
    say('   {new} new, {replace} to replace, {same} already current, {keep} kept as the '
        'host\'s own'.format(**counts))
    for action, rel in package:
        if action in ('new', 'keep'):
            say('   {}: {}'.format(action, rel))
    for rel in retired:
        say('   delete (retired from the tool): {}'.format(rel))

    with open(os.path.join(epPackage, 'bake.py'), encoding='utf-8') as fh:
        bakeText = fh.read()
    if bakeText.count(BAKE_FROM) != 1:
        problems.append("the new bake.py does not hold the DEFAULT_STORE line this script edits")
    bakeDir = os.path.normpath(os.path.join(hostPackage, '..', '..', '..', '..', 'var', 'baked'))
    say("   bake.py: DEFAULT_STORE given one more '..', for {} ({})".format(
        bakeDir, 'there' if os.path.isdir(bakeDir) else 'NOT FOUND - fine if the service sets '
                                                        'SCENARIO_BAKED_DIR'))

    router = routerPlan(hostRouter, os.path.join(ep, *EP_ROUTER.split('/')))
    plan['router'] = router
    say('')
    say('== The router ({})'.format(hostRouter))
    if 'problem' in router:
        problems.append('router: ' + router['problem'])
        say('   ' + router['problem'])
    elif router['state'] == 'current':
        say('   the endpoint block is already the new one: left as it is')
    else:
        start, end = router['range']
        say('   the endpoint block, lines {}-{} ({} lines), as ported from {}: replaced by the new '
            'one ({} lines){}'.format(start + 1, end, end - start, BASE, len(router['newBlock']),
                                      ', between TRANSPLANT BLOCK markers' if router['markers'] else ''))
        if router['importLines']:
            say('   imports added after line {}:'.format(router['insertAt']))
            for line in router['importLines']:
                say('      ' + line)
        else:
            say('   imports: the host has every name the new block needs')

    epPage = os.path.join(ep, *EP_PAGE.strip('/').split('/'))
    page = _folderPlan(epPage, hostPage, EP_PAGE)
    plan['page'] = page
    counts = {k: sum(1 for a, _ in page if a == k) for k in ('new', 'replace', 'same')}
    say('')
    say('== The page folder ({})'.format(hostPage))
    say('   {new} new, {replace} to replace, {same} already current'.format(**counts))
    for action, rel in page:
        if action == 'new':
            say('   new: {}'.format(rel))

    say('')
    say('== The databases')
    plan['dbs'] = []
    for title, var, path, table in dbPaths(src):
        state = dbState(path, table)
        plan['dbs'].append((title, var, path, table, state))
        if state is None:
            say('   {}: NOT FOUND at {} - it is not touched'.format(title, path))
        elif 'error' in state:
            problems.append('{} cannot be read: {}'.format(title, state['error']))
            say('   {}: {} - cannot be read: {}'.format(title, path, state['error']))
        else:
            say('   {}: {} - schema {}, {} {}'.format(title, path, state['version'], state['rows'], table))
    say('   (from {} when it is set in this terminal, else the package\'s own default)'.format(
        ' and '.join(d[1] for d in plan['dbs'])))

    say('')
    say('== This Python ({})'.format(sys.executable))
    pptx = _version('pptx')
    say('   python-pptx {}'.format(pptx or 'MISSING'))
    if not pptx:
        problems.append('python-pptx is not installed in this Python: the deck cannot be written')
    running = _serviceRunning()
    plan['running'] = running
    say('   the service on port {}: {}'.format(SERVICE_PORT, 'RUNNING' if running else 'stopped'))
    return plan, problems


# --------------------------------------------------------------------- go ---

def backup(plan):
    src = plan['src']
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = os.path.join(HERE, 'backup-' + stamp)
    os.makedirs(dest)
    shutil.copytree(os.path.join(src, PACKAGE), os.path.join(dest, 'package'),
                    ignore=shutil.ignore_patterns(*SKIP_DIRS))
    shutil.copytree(os.path.join(src, PAGE), os.path.join(dest, 'page'))
    shutil.copyfile(os.path.join(src, ROUTER), os.path.join(dest, 'dashboardRouter.py'))
    dbs = []
    for title, var, path, table, state in plan['dbs']:
        if state is None:
            continue
        copies = []
        for suffix in ('', '-wal', '-shm'):
            if os.path.isfile(path + suffix):
                to = os.path.join(dest, table + '.db' + suffix)
                shutil.copyfile(path + suffix, to)
                if _sha(to) != _sha(path + suffix):
                    raise RuntimeError('the copy of {} does not match it'.format(path + suffix))
                copies.append([path + suffix, to])
        dbs.append({'title': title, 'path': path, 'copies': copies})
    record = {'made': stamp, 'update': SOURCE, 'src': src, 'dbs': dbs, 'complete': True}
    with open(os.path.join(dest, 'backup.json'), 'w', encoding='utf-8') as fh:
        json.dump(record, fh, indent=2)
    return dest


def go(plan):
    src, ep = plan['src'], plan['ep']
    hostPackage, hostPage = os.path.join(src, PACKAGE), os.path.join(src, PAGE)
    epPackage = os.path.join(ep, *EP_PACKAGE.strip('/').split('/'))
    epPage = os.path.join(ep, *EP_PAGE.strip('/').split('/'))
    say('')
    say('== Backing up')
    dest = backup(plan)
    say('   saved to {} (package, page folder, router, databases; the database copies '
        'checked)'.format(dest))
    step = 'the package'
    try:
        say('')
        say('== The package')
        copied = 0
        for action, rel in plan['package']:
            if action in ('new', 'replace'):
                _copy(os.path.join(epPackage, *rel.split('/')), os.path.join(hostPackage, *rel.split('/')))
                copied += 1
        say('   {} files copied; kept the host\'s own: {}'.format(
            copied, ', '.join(r for a, r in plan['package'] if a == 'keep') or 'none'))
        for rel in plan['retired']:
            os.remove(os.path.join(hostPackage, rel))
            say('   deleted {}'.format(rel))
        cache = os.path.join(hostPackage, '__pycache__')
        if os.path.isdir(cache):
            shutil.rmtree(cache, ignore_errors=True)
        bake = os.path.join(hostPackage, 'bake.py')
        with open(bake, 'rb') as fh:
            data = fh.read()
        data = data.replace(BAKE_FROM.encode('ascii'), BAKE_TO.encode('ascii'), 1)
        with open(bake, 'wb') as fh:
            fh.write(data)
        say("   bake.py: DEFAULT_STORE has its fourth '..' again")

        step = 'the router'
        say('')
        say('== The router')
        router = plan['router']
        if router['state'] == 'current':
            say('   already current: left as it is')
        else:
            applyRouter(os.path.join(src, ROUTER), router)
            say('   the endpoint block replaced{}, and {} import line(s) added'.format(
                ' between markers' if router['markers'] else '', len(router['importLines'])))

        step = 'the page folder'
        say('')
        say('== The page folder')
        copied = 0
        for action, rel in plan['page']:
            if action in ('new', 'replace'):
                _copy(os.path.join(epPage, *rel.split('/')), os.path.join(hostPage, *rel.split('/')))
                copied += 1
        say('   {} files copied'.format(copied))

        step = 'the database upgrade'
        say('')
        say('== The databases')
        present = [d for d in plan['dbs'] if d[4] is not None]
        if len(present) < len(plan['dbs']):
            say('   not both found, so neither is opened here: the service upgrades them itself')
        else:
            ok, out = _python(src, _UPGRADE, {d[1]: d[2] for d in plan['dbs']})
            if not ok:
                raise RuntimeError('the upgrade failed: ' + out)
            for title, var, path, table, before in plan['dbs']:
                now = dbState(path, table)
                if now is None or 'error' in now:
                    raise RuntimeError('{} cannot be read after the upgrade: {}'.format(title, now))
                if now['rows'] != before['rows']:
                    raise RuntimeError('{} held {} {} and now holds {}'.format(
                        title, before['rows'], table, now['rows']))
                say('   {}: schema {} -> {}, all {} {} kept'.format(
                    title, before['version'], now['version'], now['rows'], table))
    except Exception as exc:                          # noqa: BLE001 - reported with the way back
        say('')
        say('STOPPED at {}: {}'.format(step, exc))
        say('Everything is in the backup. To put the host back as it was, run:')
        say('    python {} undo'.format(os.path.join(HERE, 'apply-update.py')))
        return 1

    say('')
    say('== Checks')
    ok, out = _python(src, _MODULES)
    say('   every package module imports: ' + ('yes' if ok and out == 'OK' else 'NO - ' + out))
    ok, out = _python(src, _FONTS)
    say('   the deck\'s fonts: ' + ('yes' if ok and out == str(FACES) else 'NO - ' + out))
    ok, out = _python(src, _ROUTES, timeout=120)
    if ok:
        say('   the router\'s /scenario endpoints: {} (the update carries {})'.format(out, ROUTES))
    else:
        say('   the router could not be imported outside the service here ({}); the service '
            'start is the test'.format(out))
    say('')
    say('Done. Start the service and the frontend the way you normally do.')
    say('If anything is wrong, stop them and run:  python {} undo'.format(
        os.path.join(HERE, 'apply-update.py')))
    return 0


# ------------------------------------------------------------------- undo ---

def _restoreFolder(saved, live):
    keep = _walk(saved)
    for rel, path in _walk(live).items():
        if rel not in keep:
            os.remove(path)
    for rel, path in keep.items():
        _copy(path, os.path.join(live, *rel.split('/')))
    cache = os.path.join(live, '__pycache__')
    if os.path.isdir(cache):
        shutil.rmtree(cache, ignore_errors=True)


def undo(src):
    backups = []
    for name in sorted(os.listdir(HERE)):
        record = os.path.join(HERE, name, 'backup.json')
        if name.startswith('backup-') and os.path.isfile(record):
            with open(record, encoding='utf-8') as fh:
                data = json.load(fh)
            if data.get('complete'):
                backups.append((name, data))
    if not backups:
        say('No complete backup beside this file: nothing to put back.')
        return 1
    name, record = backups[-1]
    dest = os.path.join(HERE, name)
    if _serviceRunning():
        say('The service answers on port {}. Stop the service and the frontend, then run '
            'this again.'.format(SERVICE_PORT))
        return 1
    src = record['src']
    say('Putting back the host as it was before the update, from {}'.format(dest))
    _restoreFolder(os.path.join(dest, 'package'), os.path.join(src, PACKAGE))
    say('   the package: put back')
    _restoreFolder(os.path.join(dest, 'page'), os.path.join(src, PAGE))
    say('   the page folder: put back')
    shutil.copyfile(os.path.join(dest, 'dashboardRouter.py'), os.path.join(src, ROUTER))
    say('   the router: put back')
    for db in record['dbs']:
        for suffix in ('-wal', '-shm'):
            if os.path.isfile(db['path'] + suffix):
                os.remove(db['path'] + suffix)
        for live, saved in db['copies']:
            shutil.copyfile(saved, live)
        say('   {}: put back'.format(db['title']))
    say('')
    say('Done. Start the service and the frontend the way you normally do.')
    return 0


# ------------------------------------------------------------------- main ---

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('action', nargs='?', default='look', choices=('look', 'go', 'undo'))
    ap.add_argument('--src', default=DEFAULT_SRC, help="the folder holding the host's cyrus_pmg")
    ap.add_argument('--ep', default=os.path.join(HERE, 'proposal-tool'),
                    help='the folder unbundle.py unpacked')
    args = ap.parse_args(argv)
    src, ep = os.path.abspath(args.src), os.path.abspath(args.ep)
    try:
        if args.action == 'undo':
            return undo(src)
        plan, problems = survey(src, ep)
        if args.action == 'go' and plan.get('running'):
            problems.append('the service answers on port {}: stop the service and the frontend '
                            'first'.format(SERVICE_PORT))
        say('')
        if problems:
            say('NOT READY - nothing was changed:')
            for p in problems:
                say('  - ' + p)
            return 1
        if args.action == 'look':
            say('Ready. Nothing was changed. With the service and the frontend stopped, run:')
            say('    python {} go'.format(os.path.join(HERE, 'apply-update.py')))
            return 0
        return go(plan)
    finally:
        _saveReport()


#: every file the bundle unpacks, with its SHA-256
MANIFEST = json.loads('''
@@MANIFEST@@
''')
#: the endpoint block as it was ported, fingerprinted as edits-check.py does
OLD_BLOCK = '@@OLDBLOCK@@'
#: package files the tool has retired since the host was ported
RETIRED = json.loads('@@RETIRED@@')
#: the /scenario endpoints the new block carries
ROUTES = int('@@ROUTES@@')

if __name__ == '__main__':
    sys.exit(main())
