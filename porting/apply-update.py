#!/usr/bin/env python3
"""Apply the Proposal Tool update to the Cyrus host, and undo it if need be.

Put this file in the folder that holds unbundle.py and the proposal-tool
folder it unpacked (H:\\pt-update), then run it from anywhere:

    python H:\\pt-update\\apply-update.py         look: what it would do; changes nothing
    python H:\\pt-update\\apply-update.py go      back up, then update - the service stopped
    python H:\\pt-update\\apply-update.py undo    put back everything the last 'go' changed

'go' is PORTING_UPDATE.md steps 5 to 8 and the database upgrade of step 10,
done the way the host needs them:

  - reads the settings the service starts with from PyCharm's run
    configurations, so the databases it backs up and upgrades are the ones
    the service really uses;
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
e948732, for a host ported from commit a550821. Python 3.8 or later,
standard library only.
"""
import argparse
import ast
import datetime
import glob
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET

SOURCE = 'e948732'
BASE = 'a550821'
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

#: the settings the update depends on, as the service's environment gives them
SETTINGS = ('SCENARIO_SLEEVES_DB', 'SCENARIO_REGISTER_DB', 'SCENARIO_BAKED_DIR', 'SCENARIO_ADAPTER')
PATHS = ('SCENARIO_SLEEVES_DB', 'SCENARIO_REGISTER_DB', 'SCENARIO_BAKED_DIR')


def _runConfigFiles(src):
    """(project folder, file) for every file where PyCharm keeps run
    configurations, in the folders at and above src."""
    found, here = [], src
    for _ in range(3):
        for path in ([os.path.join(here, '.idea', 'workspace.xml')]
                     + sorted(glob.glob(os.path.join(here, '.idea', 'runConfigurations', '*.xml')))
                     + sorted(glob.glob(os.path.join(here, '.run', '*.xml')))):
            if os.path.isfile(path):
                found.append((here, path))
        here = os.path.dirname(here)
    return found


def _envFile(path):
    out = {}
    try:
        with open(path, encoding='utf-8-sig') as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def runConfigurations(src):
    """[{name, file, script, workdir, env}] for every Python run configuration
    PyCharm keeps for the project - how the service and the frontend are
    started on the host, and so where their settings come from."""
    out = []
    for project, path in _runConfigFiles(src):
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        for conf in root.iter('configuration'):
            if conf.get('type') != 'PythonConfigurationType' or conf.get('default') == 'true':
                continue

            def option(name):
                node = conf.find("option[@name='{}']".format(name))
                return node.get('value', '') if node is not None else ''

            def expand(value):
                return value.replace('$PROJECT_DIR$', project)

            workdir = os.path.normpath(expand(option('WORKING_DIRECTORY')) or project)
            env = {}
            for name in option('ENV_FILES').replace('|', ';').split(';'):
                if name.strip():
                    env.update(_envFile(os.path.join(workdir, expand(name.strip()))))
            envs = conf.find('envs')
            for node in (envs.findall('env') if envs is not None else []):
                if node.get('name'):
                    env[node.get('name')] = expand(node.get('value', ''))
            out.append({'name': conf.get('name', '?'), 'file': path, 'workdir': workdir,
                        'script': expand(option('SCRIPT_NAME')) or option('MODULE_NAME'),
                        'env': env})
    return out


def serviceSettings(src, configurations):
    """({setting: (value, where it comes from)}, [disagreements]) - this
    terminal first, then the run configurations, else the package's default."""
    settings, disagree = {}, []
    for var in SETTINGS:
        if os.getenv(var):
            settings[var] = (os.getenv(var), 'this terminal')
            continue
        values = {}
        for conf in configurations:
            value = conf['env'].get(var, '')
            if value:
                if var in PATHS:
                    value = os.path.normpath(os.path.join(conf['workdir'], value))
                values.setdefault(value, []).append(conf['name'])
        if len(values) == 1:
            value, names = next(iter(values.items()))
            settings[var] = (value, 'run configuration ' + ', '.join(repr(n) for n in names))
        elif len(values) > 1:
            disagree.append('{}: {}'.format(var, '; '.join('{} in {}'.format(v, ', '.join(n))
                                                         for v, n in values.items())))
    return settings, disagree


def dbPaths(src, settings):
    var = os.path.join(src, 'var')
    out = []
    for title, name, default, table in (
            ('the sleeve library', 'SCENARIO_SLEEVES_DB', 'sleeves.db', 'sleeves'),
            ('the register', 'SCENARIO_REGISTER_DB', 'proposals.db', 'proposals')):
        value, where = settings.get(name, (os.path.join(var, default), "the package's default"))
        out.append((title, name, os.path.abspath(value), table, where))
    return out


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

    configurations = runConfigurations(src)
    settings, disagree = serviceSettings(src, configurations)
    plan['settings'] = settings
    say('')
    say("== PyCharm's run configurations (how the service and the frontend start)")
    if not configurations:
        say('   none found in {}'.format(os.path.join(os.path.dirname(src), '.idea')))
    for conf in configurations:
        mine = sorted('{}={}'.format(k, v) for k, v in conf['env'].items() if k.startswith('SCENARIO_'))
        say("   '{}' runs {}".format(conf['name'], conf['script'] or '?'))
        say('      {}'.format(', '.join(mine) if mine else 'no SCENARIO_ settings'))
    for line in disagree:
        problems.append('the run configurations disagree on ' + line)
    adapter = settings.get('SCENARIO_ADAPTER')
    say('   SCENARIO_ADAPTER: {}'.format(
        '{} (from {})'.format(adapter[0], adapter[1]) if adapter else
        'not set here - the service then serves sample data; the update does not change this'))

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
        bakeDir, 'there' if os.path.isdir(bakeDir) else 'not there'))
    if 'SCENARIO_BAKED_DIR' in settings:
        say('   (the service reads the bake from {} instead, set in {})'.format(
            *settings['SCENARIO_BAKED_DIR']))

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
    for title, var, path, table, where in dbPaths(src, settings):
        state = dbState(path, table)
        plan['dbs'].append((title, var, path, table, state))
        if state is None:
            say('   {}: NOT FOUND at {} ({}) - it is not touched'.format(title, path, where))
        elif 'error' in state:
            problems.append('{} cannot be read: {}'.format(title, state['error']))
            say('   {}: {} - cannot be read: {}'.format(title, path, state['error']))
        else:
            say('   {}: {} ({}) - schema {}, {} {}'.format(title, path, where, state['version'],
                                                      state['rows'], table))

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
{
  "PORTING_UPDATE.md": "6d7847a99c105475ee51876511e6073d77cfba874b6524d40953e4563dff83f0",
  "proposalTool/proposalTool.html": "632f85e61cfaf0cd9c907054bb0f4b6c45dadf3b4f0cc47725205505bf903a7b",
  "proposalTool/static/css/proposalTool.css": "54c90bb6a7e31323b39406e6c2a0e3f5ee14186273f6a20f90e38e31d0bd5788",
  "proposalTool/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d",
  "proposalTool/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498",
  "proposalTool/static/fonts/gs-sans-variable.woff2": "a41e3199f942d0583d53c237c95733aead551422e13c13d17f4f90e877e1e34f",
  "proposalTool/static/fonts/roboto-medium.woff2": "5efafd26d85f9d6c3340aa7b81aff0a4d9fe27d8f9ec9885565afb9fa2097d91",
  "proposalTool/static/fonts/roboto-regular.woff2": "cc7b26ac53700f78f8a452be6d14f14943e88dceb14edf64cddceba6e66f3f5e",
  "proposalTool/static/js/proposalTool.js": "2fb7c6fe9e46b7dc8adf9ccb7ba71b191ce49d8de74400a0351ab428650c64cb",
  "requirements.txt": "a6215670f5aeaf789fc26b3c0369721aef832dacaacf499756cf638b2b5858e4",
  "service/cyrus_pmg/pmgService/dashboardRouter.py": "b6cf19b91364d0571daa2d8c0ea3894d8426c13ab49cd14fc729a32aafc8d9e4",
  "service/cyrus_pmg/pmgService/scenario/__init__.py": "5fef4cd077d83be69bb683fc2a18735d3a1744f42a05e8007f066d0620a7f2ce",
  "service/cyrus_pmg/pmgService/scenario/accountRequests.py": "ba388739175adea084032b4a46929646316b6edc9c8dde28d2759a01da7e0c1a",
  "service/cyrus_pmg/pmgService/scenario/advisors.py": "ee8e2d33f6f9e40b76d05712597e1c4c9607af3740853717152833b4a8b4610b",
  "service/cyrus_pmg/pmgService/scenario/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1",
  "service/cyrus_pmg/pmgService/scenario/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9",
  "service/cyrus_pmg/pmgService/scenario/assetEstimates.py": "d02a7da71265bcec48552b9d0b6a2e3da5ff523d4680d9ddb964c859b9905aa6",
  "service/cyrus_pmg/pmgService/scenario/bake.py": "3245c727b54d8707b45d2c65dec8df5850a34ff2d3d86836ab78b77af0a4a4a9",
  "service/cyrus_pmg/pmgService/scenario/bakedAdapter.py": "7b71090cb058454e9e39b305298678a7b85597f764a9b85c2585d6d5e488eeca",
  "service/cyrus_pmg/pmgService/scenario/csvText.py": "2b7dc0627e32070818afa47542a55f5577b015fac3c8157f47d9799183a1bf85",
  "service/cyrus_pmg/pmgService/scenario/feeRates.csv": "07cfb792194071c8c9cf0b228aefe133f29e185a1ed02257dd68ca5513846614",
  "service/cyrus_pmg/pmgService/scenario/feeTools.py": "808c6e56a66df2a55a93b3db54a17a1d763a27de93424895ce98cbbca782d12d",
  "service/cyrus_pmg/pmgService/scenario/fees.json": "b6efce725269003f65170d5b6ee51eeab626595a463dccb16f3cd323a8d19931",
  "service/cyrus_pmg/pmgService/scenario/fees.py": "c987dfca2671752bb7b29b95558aa033fa093b1ce21aa8263335f6126256ff7b",
  "service/cyrus_pmg/pmgService/scenario/fixturesAdapter.py": "a0661bc4df48481d5fa3b9d2e7c4cb8f18677b08cb05c94115f3f0aa78562f19",
  "service/cyrus_pmg/pmgService/scenario/fontMetrics.py": "8488bb229685b2f48f1db4dcbe191cb82ccdd91228a4cc2058160651ad8cbc65",
  "service/cyrus_pmg/pmgService/scenario/fonts/GSSans-Bold.ttf": "5255cb8ad829679489976a79f0bf310cbb5e4416b028ef61d71285f8ff755d43",
  "service/cyrus_pmg/pmgService/scenario/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e",
  "service/cyrus_pmg/pmgService/scenario/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4",
  "service/cyrus_pmg/pmgService/scenario/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89",
  "service/cyrus_pmg/pmgService/scenario/fonts/GSSansCondensed-Regular.ttf": "ed47eb3873cb4c8d3504864da4870f25edafa57f75439ca390bbc712ab31e429",
  "service/cyrus_pmg/pmgService/scenario/fundingSplit.py": "026b47c3cd5c0cc2082f387aa55da43dd527b066687efce76bf05e6a33bbfc4b",
  "service/cyrus_pmg/pmgService/scenario/houseFonts.py": "3247946076f4b8d6a38e9ec9d5e42cf0dd320d1df03cc8537efbe2e19c674a02",
  "service/cyrus_pmg/pmgService/scenario/overlayRules.py": "3d44c958ffb100d3f73f8387664b6290f199994d68c17ff5cedcc5f46c30ce39",
  "service/cyrus_pmg/pmgService/scenario/payloads.py": "c8a766473bd6ed97c90a60f9f91443b2a3e2a9c8744f95cb7295a59a5a0723e0",
  "service/cyrus_pmg/pmgService/scenario/portfolio_weights.py": "5595d0c5775db260fea5573eb0237de97474f20053b4d0adb4048a5911915b70",
  "service/cyrus_pmg/pmgService/scenario/pptWriter.py": "86bb573c873bbc257159caac4ae993935022a7d1c8e70b71dca2b2d2c53a7c6a",
  "service/cyrus_pmg/pmgService/scenario/products.py": "197615c015e2e5eeb3c2d8c1f9f108fb471607c6502e41533e3cdf767828e68c",
  "service/cyrus_pmg/pmgService/scenario/proposalRegister.py": "3b07e6c8566e90250f737909b009dcbc50e01d5a4c40daeef1b6fe335a60f7c2",
  "service/cyrus_pmg/pmgService/scenario/registry.py": "c2aeae3e7ed37861968f62c0bf22681ff74b5f38200d3575866f01240b02f0a5",
  "service/cyrus_pmg/pmgService/scenario/rules.py": "92a68c07414fb48576252342cfbae685f90fa5eeb90d2c55581ed93dc5f68f12",
  "service/cyrus_pmg/pmgService/scenario/saaKeys.py": "68156acc87a624534b5fc73056c5f4baa56789ab0df1048154c71fb6e18c1c7a",
  "service/cyrus_pmg/pmgService/scenario/scenarioPort.py": "13a3953a31400fcabbd2a1f39f4a9cc7fd676ace87a04bbcf04ef5bc97d188dd",
  "service/cyrus_pmg/pmgService/scenario/scenarioStore.py": "b31397eb86cad77a484b1fd3742b902cf7a86ba3b6875ccd08a6e9b3a571072b",
  "service/cyrus_pmg/pmgService/scenario/sheetDoc.py": "2c5edc1790e4e2c2ac0ac341eb1e112c5f9257c23823b06fed2d3ab07ccbd11d",
  "service/cyrus_pmg/pmgService/scenario/sleeveRepo.py": "aa38d451e1d7c8cc65e68e4841f7bc35ad6bd27a3e559b7d58e7287e2a902c2d",
  "service/cyrus_pmg/pmgService/scenario/sleeveRules.py": "742cb3474bcd26bb071b69efca3d2cc830ff9ab71a656af720af3a0646f21e73",
  "service/cyrus_pmg/pmgService/scenario/sleeveTools.py": "cbe30aba9c94fc6348736f49fdcc81d02454ce1c53b7b9877f5409121fb3cbf2",
  "service/cyrus_pmg/pmgService/scenario/sleeves.py": "8bf6190636587893f7188a699d14da442ff1ad9ca5bf31b3c65ec254b95d6ddc",
  "service/cyrus_pmg/pmgService/scenario/types.py": "1a9cd1da911531e529e075127828459084865e8c666a8fcdbc12ac6215b69352",
  "service/cyrus_pmg/pmgService/scenario/universe.py": "e108fc548861d755e4bf816d2d5f09cb66b3ceda8997f4382223cbe2686aa092",
  "service/cyrus_pmg/pmgService/scenario/workbook.py": "3463b6f98d2823f1b2dccc9054d1562cff61c7b29d4df9dc1f1159d2bf51fbd2",
  "service/tools/portCheck.py": "bae3d0b4bd288acd4a2c91dc1f13d12188c12bee60de6bd8fb765d97ad89db61"
}
''')
#: the endpoint block as it was ported, fingerprinted as edits-check.py does
OLD_BLOCK = 'b1de543325ed85ac'
#: package files the tool has retired since the host was ported
RETIRED = json.loads('["engine.py", "liveAdapter.py"]')
#: the /scenario endpoints the new block carries
ROUTES = int('42')

if __name__ == '__main__':
    sys.exit(main())
