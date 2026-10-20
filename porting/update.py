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
    say('Done. ' + ('Start isgPMGService, then ' if plan['code'] else '')
        + 'press Ctrl+F5 on the page.')
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
            say('Nothing to do: this machine already has this update.')
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
BASE = json.loads(r'''{"package/__init__.py": "d13f9cc8dde33a81a4df3f863ab9680bcae7d21a86d02a4e87c2b7490a666e1f", "package/accountRequests.py": "0d0352931d5462a5e0fa64c0e2422f712c413d224b833801b3f366ec4a65cf52", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "9dde28f78d2b4e5e60572155990986c622186fb467aede6d62f354c6066105e7", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/customAccess.py": "44dab81cf3574c2f9875f77de3c85f14cdd05e9ac1809e407f29fd61c120d3d1", "package/customPortfolio.py": "e05f32e3c0c6f7a129d128752cdd3602596dcdcff0c1f0ba4289ebd22ed9d76a", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/feeCard.py": "a58f694ddfbc4781c9173d65b42773c48b66695a92600de72c3a09e2d62be673", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "75654c95ec9ef8770517393df78a4c0a5e806dc23378ccae2a1c74ed421edd95", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "1c5dfcfa3eb8d73ad9ccc5e6950dd731f72d56971d243b8c7c98e97296bf379b", "package/fixturesAdapter.py": "561b0eaea5a41d7301b4810de8c5a934c619758c1955f90e89efe035cd32b317", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "7e0d39c670eb6683d75dd9e27e2927246b629a2d97725f9bbed4a6d0ce8f798e", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/houseTemplate.py": "52ea6c0c0d680bef6aa40a8fb99ba298a7925f43d9510e612ea016c69e043a0e", "package/otherCharges.py": "2559db2093b8240638998f250e9c805461719319b9a2bcf4104878d6b7e81914", "package/overlayRules.py": "f20fbc114c366dcc5b1e6aab6f74f94ebb218aa8e168a81d9783540ad437ad24", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "5d64c518771402b34ea986ea80d96233985b33260d256cd411e73bcf895b2551", "package/products.py": "b971fcc5306123aad21c40ae040bc68d4dead45b43d89ce1b3dcc63a7e1dd478", "package/proposalRegister.py": "363eef64bb675df8adefe386b8af46e625ae4fd155c944877d6a7d0cfb3638cd", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/repositoryFeed.py": "1bb562fa5017f4d982faaa2407cc35eb6c91a3bc247f65581de0b857ee367d1e", "package/rules.py": "d64b78029080ab974f4d66c8568080b32869833481fe65e3f2a9f63308fc70f2", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "529416459004d3a0804663e1cb58913deea8f40e815a68366b02b38b4d3dab38", "package/scenarioStore.py": "a51dc4bf5494c5ec0572062caaaf5821c3488db5d54b3429246be3f53292cfac", "package/sheetDoc.py": "bebc6fe530ac555d9f3f8459856f208675d56a5fafb60eb7d1361e40c6a41bc4", "package/sleeveRepo.py": "ccec707af12f3d02d2c4e9f62bf3e4f9984dfddd9717d80b80256677939f024d", "package/sleeveRules.py": "7fa0ae37696ac0060c141faf05f1b5fb29067ee30c8024599384b6d4e0bbf253", "package/sleeveTools.py": "fc5a594fef48d540dee2d22d1b46803e0c611b7e660fd82b1f06e51141ea1a31", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "a3550c15086820986e7e0354e9a5997dbd23bbd2bb8c4d0615e7cc461c41f8ea", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "b959a67663dd15989386fd8b4728d03209cc79f7521ea6a134ac4658090e81f7", "page/proposalTool.html": "372a0389afbd4e586be216a22bd77feffd5f9fc45c18ed351476ef1ec7bd4168", "page/static/css/proposalTool.css": "a12c8f6d15ba83b2187c600a9b9ae6847e454fb14bb9f0d50fc4fb6b98839b44", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "7d1e2a4db4cf60efa3c890d3bdb6679e770ad54e70059336a9143fb50fd7e876"}''')
TARGET = json.loads(r'''{"package/feeCard.py": "648355dd42004822205595edc35fceb0bd19f1fa4ec6550bed58d2d799d76860", "package/fees.py": "1c073b5a4434ab15689495d9ac6310baa8412cb6cc71363cb0a73f279c5b0ca2", "package/pptWriter.py": "06060d774220c29cc65b2c6917b7ac3cbb37c303fbe50142473a26753fe44d0c", "package/sheetDoc.py": "9417ad786e4223e83ef4caee021fbed42cb83d2319d65806f918c6421e31a18c", "package/workbook.py": "cc966f670425ba889fa7f2b3bb71cdef3af8c5dca6bdc68270b52de9b3760cfa", "page/static/css/proposalTool.css": "babad82918c4fa6d60addad6cdec6b295e732187d50f2762c1d3013fe7b7ed7e", "page/static/js/proposalTool.js": "4c125be2db8db51912f46946cda5d74364bc0190e1343ea81285f4ea08afa21b"}''')
WRITE = json.loads(r'''["package/feeCard.py", "package/fees.py", "package/pptWriter.py", "package/sheetDoc.py", "package/workbook.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "3ace2581d4439d7e", "changed": true, "target": "640f363dcec697d7"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "_otherCharges": ["from", "cyrus_pmg.pmgService.scenario", "otherCharges", "_otherCharges"], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "customAccess": ["from", "cyrus_pmg.pmgService.scenario", "customAccess", null], "customPortfolio": ["from", "cyrus_pmg.pmgService.scenario", "customPortfolio", null], "customSleeves": ["from", "cyrus_pmg.pmgService.scenario", "customSleeves", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "feeCard": ["from", "cyrus_pmg.pmgService.scenario", "feeCard", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "repositoryFeed": ["from", "cyrus_pmg.pmgService.scenario", "repositoryFeed", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('65')
PAYLOAD = """
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
"""

if __name__ == '__main__':
    sys.exit(main())
