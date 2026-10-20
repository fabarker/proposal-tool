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
BASE = json.loads(r'''{"package/__init__.py": "d13f9cc8dde33a81a4df3f863ab9680bcae7d21a86d02a4e87c2b7490a666e1f", "package/accountRequests.py": "0d0352931d5462a5e0fa64c0e2422f712c413d224b833801b3f366ec4a65cf52", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "9dde28f78d2b4e5e60572155990986c622186fb467aede6d62f354c6066105e7", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/customAccess.py": "44dab81cf3574c2f9875f77de3c85f14cdd05e9ac1809e407f29fd61c120d3d1", "package/customPortfolio.py": "911565d30da3e440ceec4db6c130a9d0b35b21756acd14c2154993f310e70a20", "package/customSleeves.py": "81ceac44a8ccc5dc73e91a1781c7931c6b8206aab55c1d09c979792c08189bba", "package/feeCard.py": "a58f694ddfbc4781c9173d65b42773c48b66695a92600de72c3a09e2d62be673", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "75654c95ec9ef8770517393df78a4c0a5e806dc23378ccae2a1c74ed421edd95", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "0d068f5ada942a6c9d3f4341c83e477517e6fcf4087df7a68f2861f588866314", "package/fixturesAdapter.py": "561b0eaea5a41d7301b4810de8c5a934c619758c1955f90e89efe035cd32b317", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "7e0d39c670eb6683d75dd9e27e2927246b629a2d97725f9bbed4a6d0ce8f798e", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/houseTemplate.py": "52ea6c0c0d680bef6aa40a8fb99ba298a7925f43d9510e612ea016c69e043a0e", "package/otherCharges.py": "2559db2093b8240638998f250e9c805461719319b9a2bcf4104878d6b7e81914", "package/overlayRules.py": "f20fbc114c366dcc5b1e6aab6f74f94ebb218aa8e168a81d9783540ad437ad24", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "5d64c518771402b34ea986ea80d96233985b33260d256cd411e73bcf895b2551", "package/products.py": "e1fb0405f3274d7cffb6c1704833cf19784cdf464602c2044f60b3da4978ef59", "package/proposalRegister.py": "99552ff04fc1e4a9e29ed51fbaf72a67b5f38899649a34bb705ab2bb5def2adc", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/repositoryFeed.py": "60253cf00cbc3d4eead42039c2fe532ca63bb5576e6d7022e75e1c1881042784", "package/rules.py": "d64b78029080ab974f4d66c8568080b32869833481fe65e3f2a9f63308fc70f2", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "529416459004d3a0804663e1cb58913deea8f40e815a68366b02b38b4d3dab38", "package/scenarioStore.py": "a51dc4bf5494c5ec0572062caaaf5821c3488db5d54b3429246be3f53292cfac", "package/sheetDoc.py": "bebc6fe530ac555d9f3f8459856f208675d56a5fafb60eb7d1361e40c6a41bc4", "package/sleeveRepo.py": "3dee12d15033a8287157562cf216d626e553babee0222fce3b8dac51ca5e8d4b", "package/sleeveRules.py": "7fa0ae37696ac0060c141faf05f1b5fb29067ee30c8024599384b6d4e0bbf253", "package/sleeveTools.py": "2ad3ca0a0be3997581ffc3797cca759c3daa9abcd8c9aaa2e9a9b78c97e231ea", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "a3550c15086820986e7e0354e9a5997dbd23bbd2bb8c4d0615e7cc461c41f8ea", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "f25398b41ebcefc49cc874ae7f00f6e4b047dd42a78f1ab87bcca7f543b6a48b", "page/proposalTool.html": "372a0389afbd4e586be216a22bd77feffd5f9fc45c18ed351476ef1ec7bd4168", "page/static/css/proposalTool.css": "3539418ace516f442e28f669d632a20ba8df5f6dc13eb6d972d716df38a6b410", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "291aa2f08009cf23e8b1d51ac03c77c29f1f09254ebd08255ab25f15acd920d1"}''')
TARGET = json.loads(r'''{"package/bakedAdapter.py": "9af46a85ed977beef95acb44e222bdf703e19c9df4b61f0132a70d254e2c9164", "package/customPortfolio.py": "9cf201387243bf6b472ca794a687bcdbadb57f24f06bf970566938cb59b5dc05", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/feeCard.py": "b7617bde36d9ca2a8e84d2ac4b41007a7e9cf70f75de0304a6545dcc05fa6444", "package/fees.py": "cf0d1d8b5f8def8282f36846185a1daefad2b67567c286d48cc51d8606938f31", "package/fixturesAdapter.py": "4cdef7d3e59dd0ff9a518abdd4ba05cee52f2c10aeee0cab66dd6866cc585889", "package/money.py": "be4342f04e944aa3dbef50c0840eb536f5852dc7ea0d5c41e1f2ef4906e93e21", "package/otherCharges.py": "c6cbbf290d8ce5d7f969487023daa4cee496d3710134a6bfa4939cd13244cf9a", "package/pptWriter.py": "61c0f41674b076d697887f76cc4d72c312bb92a97ef77cd0adf9c280d77cb1b2", "package/products.py": "78db2273c93f9839cf592db736a4698eaad1937334a6256e08b9092afac3fef4", "package/repositoryFeed.py": "1bb562fa5017f4d982faaa2407cc35eb6c91a3bc247f65581de0b857ee367d1e", "package/rules.py": "99b1d58dfdbce54401c421c564d51283e7b53feea14fd560fa507a2b83768273", "package/scenarioPort.py": "761c83875998c83c4ae9a3a5d8b1395505950e51fee6558f799461cd7c5e286d", "package/sheetDoc.py": "d6b3d03bb87b17341f8914b1eb72f5f0a7edb129a63b2e5dc2cc70573f1545e3", "package/sleeveRepo.py": "e5aff135f16723fac472ae8b15a335e0b7d00e31ade424b953b48c10165cd5b2", "package/sleeveTools.py": "b9e8afda00dad60189a4887bd1ce40063dc367174c3593a54d2cf5beb128d9f2", "package/workbook.py": "b9b491dd0a49433ef6c141df9537e884efdb4794c49bd66d874dae49b605f06e", "page/static/css/proposalTool.css": "d13e40a2a32ef816e3a86839d7522f61f47d4f79d529fc2626af066d2b0d12e9", "page/static/js/proposalTool.js": "c235a327204d585400853652a7e12893c679c10bf86b1d237bbc1e379399397c"}''')
WRITE = json.loads(r'''["package/bakedAdapter.py", "package/customPortfolio.py", "package/customSleeves.py", "package/feeCard.py", "package/fees.py", "package/fixturesAdapter.py", "package/money.py", "package/otherCharges.py", "package/pptWriter.py", "package/products.py", "package/repositoryFeed.py", "package/rules.py", "package/scenarioPort.py", "package/sheetDoc.py", "package/sleeveRepo.py", "package/sleeveTools.py", "package/workbook.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "4c78d2e0da5b1fd6", "changed": true, "target": "c37249e92450f2e2"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "_money": ["from", "cyrus_pmg.pmgService.scenario", "money", "_money"], "_otherCharges": ["from", "cyrus_pmg.pmgService.scenario", "otherCharges", "_otherCharges"], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "customAccess": ["from", "cyrus_pmg.pmgService.scenario", "customAccess", null], "customPortfolio": ["from", "cyrus_pmg.pmgService.scenario", "customPortfolio", null], "customSleeves": ["from", "cyrus_pmg.pmgService.scenario", "customSleeves", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "feeCard": ["from", "cyrus_pmg.pmgService.scenario", "feeCard", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "repositoryFeed": ["from", "cyrus_pmg.pmgService.scenario", "repositoryFeed", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('65')
PAYLOAD = """
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
"""

if __name__ == '__main__':
    sys.exit(main())
