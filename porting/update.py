#!/usr/bin/env python3
"""Update the Proposal Tool on this machine, and undo it if need be.

    python update.py           look: what it would change; changes nothing
    python update.py go        change it
    python update.py undo      put back what the last 'go' changed

It carries the files that changed and nothing else. Before it changes
anything it checks that every file it will replace is exactly what it was
built to replace, and stops - naming the file - if one was edited here. It
backs up what it replaces, and the databases, into a folder beside this file,
then writes the code, upgrades the databases if the new code needs it, and
checks that the package imports and the router carries every endpoint.

It writes nothing into the project but the code itself: no record of the
update, no comment, no marker. The bake's manifest keeps the name of each
file the bake was made from, but not the folder. Its backups and its report
stay beside this file.

Python 3.8 or later, standard library only.
"""
import argparse
import ast
import base64
import datetime
import glob
import hashlib
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
import zipfile

DEFAULT_SRC = r'H:\cyrus-repo\isg-cyrus-pmg\src'
HERE = os.path.dirname(os.path.abspath(__file__))
BACKUPS = os.path.join(HERE, 'backups')
AREAS = {'package': os.path.join('cyrus_pmg', 'pmgService', 'scenario'),
         'page': os.path.join('cyrus_pmg', 'dashboard', 'proposalTool')}
ROUTER = os.path.join('cyrus_pmg', 'pmgService', 'dashboardRouter.py')
SERVICE_PORT = 8002
FACES = ['GS Sans', 'GS Sans Condensed', 'GS Sans Light']
#: modules the new block may import from when the router lacks a name
IMPORTABLE = ('cyrus_pmg.pmgService.scenario', 'fastapi')

_report = []


def say(text=''):
    print(text)
    _report.append(text)


def _saveReport():
    path = os.path.join(HERE, 'update-report.txt')
    try:
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(_report) + '\n')
        print('(saved to {})'.format(path))
    except OSError as exc:
        print('(could not save the report: {})'.format(exc))


# ------------------------------------------------------------------ files ---

def _norm(data):
    return data.replace(b'\r\n', b'\n')


def _hash(data):
    return hashlib.sha256(_norm(data)).hexdigest()


def _read(path):
    with open(path, 'rb') as fh:
        return fh.read()


def _hostPath(src, key):
    area, rel = key.split('/', 1)
    return os.path.join(src, AREAS[area], *rel.split('/'))


def _bakeNeutral(key, data):
    """bake.py as the repository has it: this machine keeps the bake one
    folder higher, a one-line difference that is its own and is kept."""
    if key == 'package/bake.py' and BAKE['to'].encode() in data:
        return data.replace(BAKE['to'].encode(), BAKE['from'].encode(), 1), True
    return data, False


# ----------------------------------------------------------------- router ---

def _readText(path):
    raw = _read(path)
    bom = raw.startswith(b'\xef\xbb\xbf')
    text = (raw[3:] if bom else raw).decode('utf-8')
    parts = text.split('\n')
    return ([p[:-1] if p.endswith('\r') else p for p in parts],
            ['\r' if p.endswith('\r') else '' for p in parts], bom)


def _writeText(path, lines, ends, bom):
    data = '\n'.join(l + e for l, e in zip(lines, ends)).encode('utf-8')
    with open(path, 'wb') as fh:
        fh.write((b'\xef\xbb\xbf' if bom else b'') + data)


def _whole(lines):
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:16]


def _blockRange(lines):
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


def _dropped(lines, ends):
    """The router without the lines this update takes out, and how many."""
    keep, keepEnds, gone = [], [], 0
    for line, end in zip(lines, ends):
        if line in ROUTER_DROP:
            gone += 1
            if line in ROUTER_DROP_AFTER_BLANK and keep and not keep[-1].strip():
                keep.pop()
                keepEnds.pop()
                gone += 1
            continue
        keep.append(line)
        keepEnds.append(end)
    return keep, keepEnds, gone


def _bindings(tree, skip):
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
        if skip[0] <= node.lineno <= skip[1]:
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = ('defined',)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        out[n.id] = ('defined',)
        else:
            for sub in ast.walk(node):
                imports(sub)
    return out


def _importLines(specs):
    lines = ['import ' + s[1] + (' as ' + s[2] if s[2] else '')
             for s in sorted({s for s in specs if s[0] == 'import'})]
    modules = {}
    for s in specs:
        if s[0] == 'from':
            modules.setdefault(s[1], set()).add(s[2] + (' as ' + s[3] if s[3] else ''))
    for module in sorted(modules):
        names = ', '.join(sorted(modules[module]))
        line = 'from {} import {}'.format(module, names)
        if len(line) > 99:
            head = 'from {} import ('.format(module)
            line = '\n'.join(textwrap.wrap(head + names + ')', 99, subsequent_indent=' ' * len(head),
                                           break_on_hyphens=False))
        lines += line.split('\n')
    return lines


def routerPlan(path, newBlock):
    """What the router needs, or why it cannot be done."""
    try:
        lines, ends, bom = _readText(path)
    except (OSError, UnicodeDecodeError) as exc:
        return {'problem': 'cannot read the router: {}'.format(exc)}
    lines, ends, gone = _dropped(lines, ends)
    found = _blockRange(lines)
    if found is None:
        return {'problem': 'the router has no endpoint block: no single line starts "_XLSX = " '
                           'followed by "def exportScenario"'}
    start, end = found
    current = _whole(lines[start:end])
    if newBlock is None:
        state = 'unchanged'
    elif current == ROUTER_BLOCK['target']:
        state = 'current'
    elif current == ROUTER_BLOCK['base']:
        state = 'replace'
    else:
        return {'problem': 'the endpoint block (lines {}-{}) was edited here, so it is left '
                           'alone'.format(start + 1, end)}
    block = newBlock if state == 'replace' else lines[start:end]
    try:
        tree = ast.parse('\n'.join(lines))
        used = {n.id for n in ast.walk(ast.parse('\n'.join(block))) if isinstance(n, ast.Name)}
    except SyntaxError as exc:
        return {'problem': 'the router does not parse: {}'.format(exc)}
    have = _bindings(tree, (start + 1, end))
    missing, refused = [], []
    for name in sorted(used & set(NEEDS)):
        if name in have:
            continue
        spec = tuple(NEEDS[name])
        if spec[0] == 'import' or spec[1].startswith(IMPORTABLE):
            missing.append(spec)
        else:
            refused.append('{} (from {})'.format(name, spec[1]))
    if refused:
        return {'problem': 'the new block needs names the router lacks and this update must not '
                           'add: ' + ', '.join(refused)}
    after = max([n.end_lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
                 and n.lineno - 1 < start] or [start])
    work = state == 'replace' or missing or gone
    return {'state': state, 'lines': lines, 'ends': ends, 'bom': bom, 'range': found,
            'block': block, 'imports': _importLines(missing) if missing else [], 'at': after,
            'dropped': gone, 'work': bool(work)}


def applyRouter(path, plan):
    lines, ends = list(plan['lines']), list(plan['ends'])
    eol = '\r' if ends.count('\r') * 2 > len(ends) else ''
    start, end = plan['range']
    if plan['state'] == 'replace':
        lines[start:end] = plan['block']
        ends[start:end] = [eol] * len(plan['block'])
    if plan['imports']:
        lines[plan['at']:plan['at']] = plan['imports']
        ends[plan['at']:plan['at']] = [eol] * len(plan['imports'])
    compile('\n'.join(lines), path, 'exec')
    _writeText(path, lines, ends, plan['bom'])


# ------------------------------------------------------------------- bake ---

def _isFolder(value):
    return value.startswith(('/', '\\')) or (len(value) > 2 and value[1] == ':'
                                               and value[2] in '/\\')


def _fileName(value):
    return value.replace('\\', '/').rsplit('/', 1)[-1]


def _named(data, fix=False):
    """Every `path` in the manifest that names a folder; with *fix*, cut to its file name."""
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key == 'path' and isinstance(value, str) and _isFolder(value):
                found.append(value)
                if fix:
                    data[key] = _fileName(value)
            else:
                found += _named(value, fix)
    elif isinstance(data, list):
        for value in data:
            found += _named(value, fix)
    return found


def bakeDirs(src):
    """Every folder the service may read the bake from: the setting, then the
    default the host's bake.py names."""
    dirs = [os.getenv('SCENARIO_BAKED_DIR', '')] + sorted(_runConfigs(src).get('SCENARIO_BAKED_DIR', ()))
    package = os.path.join(src, AREAS['package'])
    try:
        line = BAKE['to'] if BAKE['to'].encode() in _read(os.path.join(package, 'bake.py')) else BAKE['from']
    except OSError:
        line = BAKE['from']
    dirs.append(os.path.join(package, *(['..'] * line.count("'..'") + ['var', 'baked'])))
    out = []
    for d in dirs:
        if d and os.path.abspath(d) not in out:
            out.append(os.path.abspath(d))
    return out


def manifestPlan(path):
    """The bake's manifest as the update leaves it: the folders a file was
    read from where the bake was made, cut to the file's name."""
    raw = _read(path)
    bom = raw.startswith(b'\xef\xbb\xbf')
    text = (raw[3:] if bom else raw).decode('utf-8')
    data = json.loads(text)
    found = _named(data, fix=True)
    if not found:
        return None
    new = text
    for value in set(found):
        new = new.replace(json.dumps(value), json.dumps(_fileName(value)))
    if json.loads(new) != data:                  # written some other way: written as the bake writes
        new = json.dumps(data, separators=(',', ':'))
    return {'path': path, 'count': len(found),
            'data': (b'\xef\xbb\xbf' if bom else b'') + new.encode('utf-8')}


def applyManifest(item):
    tmp = item['path'] + '.tmp'
    try:
        with open(tmp, 'wb') as fh:
            fh.write(item['data'])
        os.replace(tmp, item['path'])
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ------------------------------------------------------- service settings ---

def _runConfigs(src):
    """{variable: value} from PyCharm's Python run configurations for this
    project: where the service's settings come from on this machine."""
    found, here = [], src
    for _ in range(3):
        found += [(here, p) for p in [os.path.join(here, '.idea', 'workspace.xml')]
                  + sorted(glob.glob(os.path.join(here, '.idea', 'runConfigurations', '*.xml')))
                  + sorted(glob.glob(os.path.join(here, '.run', '*.xml'))) if os.path.isfile(p)]
        here = os.path.dirname(here)
    values = {}
    for project, path in found:
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        for conf in root.iter('configuration'):
            if conf.get('type') != 'PythonConfigurationType' or conf.get('default') == 'true':
                continue
            node = conf.find("option[@name='WORKING_DIRECTORY']")
            workdir = os.path.normpath((node.get('value', '') if node is not None else '')
                                       .replace('$PROJECT_DIR$', project) or project)
            envs = conf.find('envs')
            for env in (envs.findall('env') if envs is not None else []):
                name, value = env.get('name', ''), env.get('value', '').replace('$PROJECT_DIR$', project)
                if name.startswith('SCENARIO_') and value:
                    if name.endswith(('_DB', '_DIR', '_SOURCE', '_SEED')):
                        value = os.path.normpath(os.path.join(workdir, value))
                    values.setdefault(name, set()).add(value)
    return values


def dbPaths(src):
    configs = _runConfigs(src)
    out, problems = [], []
    for title, var, default, table in (
            ('the sleeve library', 'SCENARIO_SLEEVES_DB', 'sleeves.db', 'sleeves'),
            ('the register', 'SCENARIO_REGISTER_DB', 'proposals.db', 'proposals')):
        chosen = configs.get(var, set())
        if os.getenv(var):
            path = os.getenv(var)
        elif len(chosen) > 1:
            problems.append('the run configurations name different files for {}'.format(var))
            continue
        elif chosen:
            path = next(iter(chosen))
        else:
            path = os.path.join(src, 'var', default)
        out.append((title, var, os.path.abspath(path), table))
    return out, problems


#: the files the service reads its first catalogue and fee card from, which
#: the upgrade must read too, or it loads the packaged stand-ins instead
SOURCES = (('the product file', 'SCENARIO_PRODUCTS_SOURCE'),
           ('the fee card file', 'SCENARIO_FEES_SOURCE'))


def sourcePaths(src):
    """{variable: path} for each file the service is set to read, as the
    upgrade must see it - this shell's setting first, else the run
    configurations' - and what is wrong with them."""
    configs = _runConfigs(src)
    out, problems = {}, []
    for title, var in SOURCES:
        chosen = configs.get(var, set())
        if os.getenv(var):
            path = os.getenv(var)
        elif len(chosen) > 1:
            problems.append('the run configurations name different files for {}'.format(var))
            continue
        elif chosen:
            path = next(iter(chosen))
        else:
            continue
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            problems.append('{} names {} for {}, and there is no such file'.format(var, path, title))
            continue
        out[var] = path
    return out, problems


def dbState(path, table):
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


def _notesToReword(path):
    conn = sqlite3.connect('file:{}?mode=ro'.format(path.replace('\\', '/')), uri=True)
    try:
        return conn.execute('SELECT COUNT(*) FROM policyHistory WHERE note IN ({})'.format(
            ', '.join('?' * len(NOTES))), [old for old, _ in NOTES]).fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _rewordNotes(path):
    conn = sqlite3.connect(path)
    try:
        done = sum(conn.execute('UPDATE policyHistory SET note = ? WHERE note = ?', (new, old)).rowcount
                   for old, new in NOTES)
        conn.commit()
        return done
    finally:
        conn.close()


def _serviceRunning():
    try:
        with socket.create_connection(('127.0.0.1', SERVICE_PORT), timeout=1):
            return True
    except OSError:
        return False


def _python(src, code, env=None, timeout=300):
    full = dict(os.environ)
    full['PYTHONPATH'] = src + (os.pathsep + full['PYTHONPATH'] if full.get('PYTHONPATH') else '')
    full.update(env or {})
    try:
        out = subprocess.run([sys.executable, '-c', code], cwd=src, env=full, capture_output=True,
                             text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, 'did not finish in {} seconds'.format(timeout)
    if out.returncode != 0:
        tail = [l for l in out.stderr.strip().splitlines() if l.strip()][-3:]
        return False, ' | '.join(tail) or 'exit code {}'.format(out.returncode)
    lines = [l for l in out.stdout.strip().splitlines() if l.strip()]
    return True, lines[-1] if lines else ''


_MODULES = '''
import importlib, pkgutil
import cyrus_pmg.pmgService.scenario as p
bad = []
for m in pkgutil.iter_modules(p.__path__):
    try:
        importlib.import_module('cyrus_pmg.pmgService.scenario.' + m.name)
    except Exception as exc:
        bad.append('{}: {}: {}'.format(m.name, type(exc).__name__, exc))
print('; '.join(bad) or 'OK')
'''
_FONTS = 'import cyrus_pmg.pmgService.scenario.houseFonts as h; print(sorted(h.FACES))'
_ROUTES = ("from cyrus_pmg.pmgService.dashboardRouter import router; "
           "print(len([r for r in router.routes if getattr(r, 'path', '').startswith('/scenario')]))")
_UPGRADE = '''
import json
from cyrus_pmg.pmgService.scenario import accountRequests, fees, products, proposalRegister, sleeveRepo
c = sleeveRepo.connection()
c.close()
proposalRegister.describe()
accountRequests.describe()
out = {}
try:
    d = products.describeSource()
    out.update(products=d['products'], productsFrom=d['seededFrom'])
except Exception as exc:
    out['productsProblem'] = str(exc)
try:
    f = fees.deliveryInfo()
    out.update(feeCard=f['version'], feeCardFrom=f['source'])
except Exception as exc:
    out['feeCardProblem'] = str(exc)
print(json.dumps(out))
'''


# ------------------------------------------------------------------ survey ---

def survey(src):
    problems, warnings = [], []
    plan = {'src': src}
    say('Proposal Tool update: {} file(s) to write, {} to delete{}'.format(
        len(WRITE), len(DELETE), ', the endpoint block' if ROUTER_BLOCK['changed'] else ''))
    say('Project: {}'.format(src))
    for area, rel in AREAS.items():
        if not os.path.isdir(os.path.join(src, rel)):
            problems.append('no {} folder at {}'.format(area, os.path.join(src, rel)))
    if problems:
        return plan, problems, warnings

    files, current, bakeKept = [], 0, False
    for key in sorted(set(BASE) | set(WRITE) | set(DELETE)):
        path = _hostPath(src, key)
        if key.rsplit('/', 1)[-1] in HOST_OWNED:
            continue
        here = os.path.isfile(path)
        data, kept = _bakeNeutral(key, _read(path)) if here else (None, False)
        bakeKept = bakeKept or kept
        now = _hash(data) if here else None
        if key in WRITE:
            if now == TARGET[key]:
                current += 1
            elif key not in BASE and not here:
                files.append(('new', key))
            elif now == BASE.get(key):
                files.append(('write', key))
            else:
                problems.append('{} was edited here - the update would overwrite it'.format(key))
        elif key in DELETE:
            if not here:
                current += 1
            elif now == BASE[key]:
                files.append(('delete', key))
            else:
                problems.append('{} was edited here - the update would delete it'.format(key))
        elif here and now != BASE[key]:
            warnings.append('{} was edited here; this update leaves it as it is'.format(key))
    plan['files'], plan['bakeKept'] = files, bakeKept
    say('')
    say('== Files: {} to write, {} new, {} to delete, {} already current'.format(
        sum(1 for a, _ in files if a == 'write'), sum(1 for a, _ in files if a == 'new'),
        sum(1 for a, _ in files if a == 'delete'), current))
    for action, key in files:
        say('   {}: {}'.format(action, key))

    router = routerPlan(os.path.join(src, ROUTER),
                        _payload('router/block.txt').decode('utf-8').split('\n')
                        if ROUTER_BLOCK['changed'] else None)
    plan['router'] = router
    say('')
    if 'problem' in router:
        problems.append('router: ' + router['problem'])
        say('== The router: ' + router['problem'])
    else:
        say('== The router: {}'.format({'replace': 'the endpoint block is replaced',
                                        'current': 'the endpoint block is already current',
                                        'unchanged': 'the endpoint block is unchanged'}[router['state']]))
        for line in router['imports']:
            say('   import added: ' + line)
        if router['dropped']:
            say('   {} line(s) taken out that are not part of the code'.format(router['dropped']))

    plan['manifests'] = []
    say('')
    say('== The bake')
    for folder in bakeDirs(src):
        path = os.path.join(folder, 'manifest.json')
        if not os.path.isfile(path):
            continue
        try:
            item = manifestPlan(path)
        except (OSError, ValueError) as exc:
            problems.append('the bake\'s manifest cannot be read: {} ({})'.format(path, exc))
            continue
        if item:
            plan['manifests'].append(item)
            say('   {}: {} folder name(s) cut to the file name'.format(path, item['count']))
        else:
            say('   {}: names no folder'.format(path))

    dbs, dbProblems = dbPaths(src)
    problems += dbProblems
    plan['sources'], sourceProblems = sourcePaths(src)
    problems += sourceProblems
    plan['dbs'] = [(t, v, p, table, dbState(p, table)) for t, v, p, table in dbs]
    say('')
    say('== The databases')
    for title, var, path, table, state in plan['dbs']:
        if state is None:
            say('   {}: not found at {} - not touched'.format(title, path))
        elif 'error' in state:
            problems.append('{} cannot be read: {}'.format(title, state['error']))
        else:
            say('   {}: schema {}, {} {} ({})'.format(title, state['version'], state['rows'], table, path))

    for title, var in SOURCES:
        if var in plan['sources']:
            say('   {}: {} ({})'.format(title, plan['sources'][var], var))

    plan['reword'] = 0
    for title, var, path, table, state in plan['dbs']:
        if table == 'sleeves' and state and 'error' not in state and NOTES:
            plan['reword'] = _notesToReword(path)
    if plan['reword']:
        say('   {} history note(s) to reword'.format(plan['reword']))

    plan['code'] = any(k.startswith('package/') for _, k in files) or router.get('work', False)
    running = _serviceRunning()
    if plan['code'] and running:
        problems.append('the service answers on port {}: stop isgPMGService first'.format(SERVICE_PORT))
    say('')
    say('== The service on port {}: {}'.format(SERVICE_PORT, 'running' if running else 'stopped'))
    for w in warnings:
        say('   note: ' + w)
    return plan, problems, warnings


# --------------------------------------------------------------------- go ---

def _payload(name):
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(''.join(PAYLOAD.split())))) as zf:
        return zf.read(name)


def _patches():
    global _PATCHES
    if _PATCHES is None:
        _PATCHES = json.loads(_payload('patches.json').decode('utf-8'))
    return _PATCHES


_PATCHES = None


def _newContent(path, key):
    """The file as the update writes it: whole from the payload, or this
    machine's copy - checked to be the base - with the changed lines put in,
    in the line ends it already had."""
    ops = _patches().get(key)
    if ops is None:
        data = _payload(key)
    else:
        old, _ = _bakeNeutral(key, _read(path))
        crlf = old.count(b'\r\n') * 2 > old.count(b'\n')
        lines = _norm(old).decode('utf-8').split('\n')
        for i1, i2, new in sorted(ops, key=lambda op: op[0], reverse=True):
            lines[i1:i2] = new
        data = '\n'.join(lines).encode('utf-8')
        if crlf:
            data = data.replace(b'\n', b'\r\n')
    if _hash(data) != TARGET[key]:
        raise RuntimeError('{} did not come out as built - nothing of it was written'.format(key))
    return data


def go(plan):
    src = plan['src']
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = os.path.join(BACKUPS, stamp)
    os.makedirs(dest)
    saved = []
    for action, key in plan['files']:
        path = _hostPath(src, key)
        if os.path.isfile(path):
            os.makedirs(os.path.join(dest, os.path.dirname(key)), exist_ok=True)
            shutil.copyfile(path, os.path.join(dest, *key.split('/')))
        saved.append({'key': key, 'existed': os.path.isfile(path)})
    router = plan['router']
    if router['work']:
        shutil.copyfile(os.path.join(src, ROUTER), os.path.join(dest, 'dashboardRouter.py'))
    manifests = []
    for n, item in enumerate(plan['manifests']):
        to = os.path.join(dest, 'manifest-{}.json'.format(n))
        shutil.copyfile(item['path'], to)
        manifests.append([item['path'], to])
    dbs = []
    for title, var, path, table, state in (plan['dbs'] if plan['code'] or plan['reword'] else []):
        if state is None:
            continue
        copies = []
        for suffix in ('', '-wal', '-shm'):
            if os.path.isfile(path + suffix):
                to = os.path.join(dest, table + '.db' + suffix)
                shutil.copyfile(path + suffix, to)
                if _hash(_read(to)) != _hash(_read(path + suffix)):
                    raise RuntimeError('the copy of {} does not match it'.format(path + suffix))
                copies.append([path + suffix, to])
        dbs.append({'title': title, 'path': path, 'copies': copies})
    with open(os.path.join(dest, 'backup.json'), 'w', encoding='utf-8') as fh:
        json.dump({'src': src, 'files': saved, 'router': router['work'], 'manifests': manifests,
                   'dbs': dbs, 'complete': True}, fh, indent=2)
    say('')
    say('== Backed up to {}'.format(dest))

    step = 'the files'
    try:
        for action, key in plan['files']:
            path = _hostPath(src, key)
            if action == 'delete':
                os.remove(path)
                continue
            data = _newContent(path, key)
            if key == 'package/bake.py' and plan['bakeKept']:
                data = data.replace(BAKE['from'].encode(), BAKE['to'].encode(), 1)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as fh:
                fh.write(data)
        say('== Files: done')
        step = 'the router'
        if router['work']:
            applyRouter(os.path.join(src, ROUTER), router)
            say('== The router: done')
        step = 'the bake\'s manifest'
        for item in plan['manifests']:
            applyManifest(item)
        if plan['manifests']:
            say('== The bake\'s manifest: done')
        step = 'the databases'
        present = [d for d in plan['dbs'] if d[4] is not None]
        if plan['code'] and len(present) == len(plan['dbs']):
            env = {d[1]: d[2] for d in plan['dbs']}
            env.update(plan.get('sources') or {})
            ok, out = _python(src, _UPGRADE, env)
            if not ok:
                raise RuntimeError('opening them with the new code failed: ' + out)
            try:
                loaded = json.loads(out)
            except ValueError:
                raise RuntimeError('opening them with the new code said: ' + out)
            for key in ('productsProblem', 'feeCardProblem'):
                if loaded.get(key):
                    raise RuntimeError(loaded[key])
            say('== The product catalogue: {} products in the database (first loaded from {})'.format(
                loaded['products'], os.path.basename(loaded['productsFrom'] or '') or 'nothing'))
            say('== The fee card: {} in the database ({})'.format(
                loaded['feeCard'], loaded['feeCardFrom'] or 'edited since it was loaded'))
            for title, var, path, table, before in plan['dbs']:
                now = dbState(path, table)
                if now is None or 'error' in now or now['rows'] != before['rows']:
                    raise RuntimeError('{} changed its rows: {} before, {} after'.format(
                        title, before, now))
                say('== {}: schema {}{}, all {} {} kept'.format(
                    title, before['version'],
                    '' if now['version'] == before['version'] else ' -> ' + now['version'],
                    now['rows'], table))
        if plan['reword']:
            title, var, path, table, before = next(d for d in plan['dbs'] if d[3] == 'sleeves')
            done = _rewordNotes(path)
            now = dbState(path, table)
            if now is None or 'error' in now or now['rows'] != before['rows']:
                raise RuntimeError('{} changed its rows: {} before, {} after'.format(title, before, now))
            say('== {}: {} history note(s) reworded'.format(title, done))
    except Exception as exc:                          # noqa: BLE001 - reported with the way back
        say('')
        say('STOPPED at {}: {}'.format(step, exc))
        say('To put everything back:  python {} undo'.format(os.path.join(HERE, 'update.py')))
        return 1

    if plan['code']:
        ok, out = _python(src, _MODULES)
        say('== Every package module imports: ' + ('yes' if ok and out == 'OK' else 'NO - ' + out))
        ok, out = _python(src, _FONTS)
        say('== The deck\'s fonts: ' + ('yes' if ok and out == str(FACES) else 'NO - ' + out))
        ok, out = _python(src, _ROUTES, timeout=120)
        say('== The router\'s endpoints: ' + ('{} of {}'.format(out, ROUTES) if ok else
                                             'not checked here ({})'.format(out)))
    say('')
    say('Done. ' + ('Start isgPMGService, then press' if plan['code'] else 'Press')
        + ' Ctrl+F5 on the page.')
    return 0


# ------------------------------------------------------------------- undo ---

def undo():
    backups = sorted(d for d in (os.listdir(BACKUPS) if os.path.isdir(BACKUPS) else [])
                     if not d.endswith('-undone')
                     and os.path.isfile(os.path.join(BACKUPS, d, 'backup.json')))
    if not backups:
        say('No backup beside this file: nothing to put back.')
        return 1
    dest = os.path.join(BACKUPS, backups[-1])
    with open(os.path.join(dest, 'backup.json'), encoding='utf-8') as fh:
        record = json.load(fh)
    if _serviceRunning():
        say('The service answers on port {}: stop isgPMGService, then run this again.'.format(
            SERVICE_PORT))
        return 1
    src = record['src']
    for item in record['files']:
        path = _hostPath(src, item['key'])
        if item['existed']:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copyfile(os.path.join(dest, *item['key'].split('/')), path)
        elif os.path.isfile(path):
            os.remove(path)
    if record['router']:
        shutil.copyfile(os.path.join(dest, 'dashboardRouter.py'), os.path.join(src, ROUTER))
    for live, saved in record.get('manifests', []):
        shutil.copyfile(saved, live)
    for db in record['dbs']:
        for suffix in ('-wal', '-shm'):
            if os.path.isfile(db['path'] + suffix):
                os.remove(db['path'] + suffix)
        for live, saved in db['copies']:
            shutil.copyfile(saved, live)
    shutil.move(dest, dest + '-undone')
    say('Put back as it was before the update ({} file(s){}{}{}).'.format(
        len(record['files']), ', the router' if record['router'] else '',
        ', the bake\'s manifest' if record.get('manifests') else '',
        ', the databases' if record['dbs'] else ''))
    say('Start isgPMGService, then press Ctrl+F5 on the page.')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('action', nargs='?', default='look', choices=('look', 'go', 'undo'))
    ap.add_argument('--src', default=DEFAULT_SRC, help="the folder holding cyrus_pmg")
    args = ap.parse_args(argv)
    try:
        say('This update: {} (id {})'.format(TITLE, UPDATE_ID))
        if args.action == 'undo':
            return undo()
        plan, problems, warnings = survey(os.path.abspath(args.src))
        say('')
        if problems:
            say('NOT DONE - nothing was changed:')
            for p in problems:
                say('  - ' + p)
            return 1
        if not (plan['files'] or plan['router']['work'] or plan['manifests'] or plan['reword']):
            say('Nothing to do: this machine already has this update ({}).'.format(UPDATE_ID))
            return 0
        if args.action == 'look':
            say('Ready. Nothing was changed. To update:  python {} go'.format(
                os.path.join(HERE, 'update.py')))
            return 0
        return go(plan)
    finally:
        _saveReport()


#: every file of the package and the page this update was built against, by
#: content (line ends aside), and every file it writes, as it writes it
BASE = json.loads(r'''{"package/__init__.py": "d13f9cc8dde33a81a4df3f863ab9680bcae7d21a86d02a4e87c2b7490a666e1f", "package/accountRequests.py": "0d0352931d5462a5e0fa64c0e2422f712c413d224b833801b3f366ec4a65cf52", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "dbe73f522b121de5e5b5450ef8615c918a7577805a95e83769732a0e45252755", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/customAccess.py": "44dab81cf3574c2f9875f77de3c85f14cdd05e9ac1809e407f29fd61c120d3d1", "package/customPortfolio.py": "e05f32e3c0c6f7a129d128752cdd3602596dcdcff0c1f0ba4289ebd22ed9d76a", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/entities.py": "32fdfcbfe042664eb04a790dd35387b3aeb799702ec794a5eb2a63b70e1bfa7c", "package/feeCard.py": "343c6363c39679810fca4f1498ec3dbf8cbd76361ceccafb5d04723616a634d9", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "75654c95ec9ef8770517393df78a4c0a5e806dc23378ccae2a1c74ed421edd95", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "75e173df01b41481358257ab4e7f05f25f1b177006658ee1cdfaae61db974144", "package/fixturesAdapter.py": "ec7501805601226357435c297d2001d7c9216c45b2610f98e108c058fd070a94", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "7e0d39c670eb6683d75dd9e27e2927246b629a2d97725f9bbed4a6d0ce8f798e", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/houseTemplate.py": "52ea6c0c0d680bef6aa40a8fb99ba298a7925f43d9510e612ea016c69e043a0e", "package/marketing.py": "5074896c10a45158850e673b6c71f3eb92c29cc766b4b85b15d590e87034946a", "package/otherCharges.py": "2559db2093b8240638998f250e9c805461719319b9a2bcf4104878d6b7e81914", "package/overlayRules.py": "28f7f58859f55ab8a4032b6ef4ef05bf1eb4f48b263697298694bc10bd4abd69", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "06060d774220c29cc65b2c6917b7ac3cbb37c303fbe50142473a26753fe44d0c", "package/products.py": "8c9f23dbb83db222fe18c66184355f5ad77687cd0f3c0d53fbfd054efa249114", "package/proposalRegister.py": "d7629c476a795b1ec3be69c87403e2a2819fcfa5239be5bdac68a87d92d99d96", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/repositoryFeed.py": "1bb562fa5017f4d982faaa2407cc35eb6c91a3bc247f65581de0b857ee367d1e", "package/rules.py": "b7dcb5d493fa03077a53996f62955660037ab6ccbe5abf38c5938ac8f9e60d27", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "92b3918327dd9c93b8e8d8d95583d4e059dacaa19990f2683c14271492ee6b13", "package/scenarioStore.py": "68c57af4743a3743bb9bcdcc8697c49da9b61964da552fb4268cf5a7ad007c0b", "package/sheetDoc.py": "9417ad786e4223e83ef4caee021fbed42cb83d2319d65806f918c6421e31a18c", "package/sleeveRepo.py": "88e8794efb908e0e5cc4a769c4c2ceaabdeda52e7a4d9f71a10d64d58ac71dd4", "package/sleeveRules.py": "087f7ad77fba37a0a1d0955a9078e50b92d97f4b3d3227dd0d5a4558b8425f2f", "package/sleeveTools.py": "081b13d2f19fc12113a4f637badaf40fdd9c821bbc51fee61b91230813c0adba", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "4a00b650ec251bc37f8dd75870e7bda94a1c6cb726027cf12c0f3b1a305f34e5", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "124f17ccd237fff98e775c59a1966c0f0f7942feb5c2bff11a493d441e43b472", "page/proposalTool.html": "77dab8c6fc304d9f27c7ce8a571d4f7dab11aebaff9e066292bfe30c29b1a2cc", "page/static/css/proposalTool.css": "c3456ff5c237ff5b09cdaf8ed2d0a7a892ace92ba2797b74fffad199d17fba09", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "2fbbf57bfb33102f767ba0b78d3bce2d339af1b498752638936821d614813efe"}''')
TARGET = json.loads(r'''{"package/marketing.py": "6731d20f22b044de32892585cdf50a72b205f445251c327525d32a3bb9c525fe", "package/products.py": "6716e03eef84bc86e3f7be25e263abddfd548c70d79aa1327c79f7ba87509357", "package/proposalRegister.py": "0c973f440ee8d1d6b10de32b7f8a905ecf9775e2a111c23e17411e1a7c483ba2", "package/rules.py": "81b50966b2b69d1fde3a5a171bca6c3a8d2e79ace2e088bbe17a46536383a904", "package/scenarioStore.py": "efee33953d17fac4633d5f76d280ff91f90ff8d8268d4110ae2515d36559aa3d", "page/proposalTool.html": "5426ad23c005de365da3531a2b01120887436595179a119a40a93677f7dbbcd4", "page/static/css/proposalTool.css": "1488030dbd77a625656e80c583449fe27cba8dfd7f8b1bdd393c329be86c328b", "page/static/js/proposalTool.js": "47d7a2b359d5c12763b8a706fe458e22bfc42a9f4a9e3c39a5c0bcb53e2aec4b"}''')
WRITE = json.loads(r'''["package/marketing.py", "package/products.py", "package/proposalRegister.py", "package/rules.py", "package/scenarioStore.py", "page/proposalTool.html", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "8dcc3a81562cd128", "changed": true, "target": "a2610be0fa3a155d"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "_otherCharges": ["from", "cyrus_pmg.pmgService.scenario", "otherCharges", "_otherCharges"], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "customAccess": ["from", "cyrus_pmg.pmgService.scenario", "customAccess", null], "customPortfolio": ["from", "cyrus_pmg.pmgService.scenario", "customPortfolio", null], "customSleeves": ["from", "cyrus_pmg.pmgService.scenario", "customSleeves", null], "entities": ["from", "cyrus_pmg.pmgService.scenario", "entities", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "feeCard": ["from", "cyrus_pmg.pmgService.scenario", "feeCard", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "marketing": ["from", "cyrus_pmg.pmgService.scenario", "marketing", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "repositoryFeed": ["from", "cyrus_pmg.pmgService.scenario", "repositoryFeed", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('72')
#: what this update carries, and a short id from the files it writes, so each
#: run says which update it is
TITLE = json.loads(r'''"The hedge fund notice: a card with a tick box to confirm before a sleeve holding a hedge fund is attached, recorded on the proposal and in the register"''')
UPDATE_ID = hashlib.sha256(json.dumps(TARGET, sort_keys=True).encode('ascii')).hexdigest()[:8]
PAYLOAD = """
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
jh2Zc7O6YtQdCAXdPu1D11eY198HbZtUavMa71Vrj/VOX9XvhmMFujm4z+xG7ZGabi3O1oPBdL/Z
V0s/bKm02zuNynXThwZruc2O29NNGO8w87NtWAWq0z3FoMw1ItNoOGTbqG08M2dzXZL+Da+6CdYY
xtSEwQZtvY/QBKmvXsX5hCvXEhlTjfUFj8ODvX0kUPhDu9RwJqCQrwuQ7m6pdOAxNZVj26ao6Vna
OYXkZm3T3kO/clcu5ZOoHyUgxZP9PI5TuYF3RI6VKn+6Mz8VMoJOzU+OEhF61oXYoa+/q3c1nLZ4
kBQ4iZW+YCBLOAClyiowJnYuS+kNGls/ijg82Mcrj9WfidrOgHIeXIX2r3tl7UOufVjWbgYRu1md
+lmG6jESr5rJN5XFtZQ6+WbztTOoUnDWHGfy1YR/cyK2pwWdLFADNmHg9jwiEbx3uoVIHksReKl5
TpdstlBVJUtJ5hgQ1CuKEhNbqkcQs2pZzQvtqauuqXltFdZ9G4udMhfoqr/YaWeui52ukDm6RGNy
GLz6Sj+bXTxwhOr7fylb6J/JtkHp/Mnsx57DKn9a2/oNPaHOplePROuBxhUINt/8Uj9RRajxxcmD
P8Td+ZvbFTekdSttHaaWH+CODPczv7yrObhfad84XOWpO6pZDa+p7+hc8ht5DXWvGRkj5U0Z9VyT
evU5Cv5qRg8Wl/k88JToPC7625JV41RzfeyMJas5Z79hAW+wZXDDPsEj5QYLfX0LO27V4d6A5BU0
vAritVS1KI/MVz8gQ68fSqR79DRVbtVvrT9z0jrtVWVXRwmqUcc2G6Vbg3Az4D0IhouOFbucAskl
KIWsX0yiX4vkDVRhss2V19eqlIjmCZR4Jn6VpdcI4HAX3rSgXF1yjxjHn2HXOC1RbWWT6RH/Mowf
E8ckyrJ/EeDRuH5TXgNNWUBqzYgZMLOonqxs4HAj3WroSoSqNNUWNFZ/GUBhs5t+K7+h0kHFLsmn
1x1TP1UnWQ2LUSzHb9PvXq5ePeSiBjmA1wJd2tMNWXC++gVwRMksRceglmsaBi8VlcMDUlQOlUfa
8nSGuW+NQMk8yPrmEV3tXI1LsjQ3SsIGqS6w1kmSrzP5Dn3aULnpQjZ6hCXsK9pcKevbFLq6qNc6
XZlzccUJAJjq8NTwP2eswtWl30vlgitkHeyQVkd/CFkclSePmMIWw1rBFY6uXxPInW8V7W7CdMJo
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
"""

if __name__ == '__main__':
    sys.exit(main())
