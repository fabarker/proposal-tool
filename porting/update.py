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
BASE = json.loads(r'''{"package/__init__.py": "d13f9cc8dde33a81a4df3f863ab9680bcae7d21a86d02a4e87c2b7490a666e1f", "package/accountRequests.py": "0d0352931d5462a5e0fa64c0e2422f712c413d224b833801b3f366ec4a65cf52", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "dbe73f522b121de5e5b5450ef8615c918a7577805a95e83769732a0e45252755", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/customAccess.py": "44dab81cf3574c2f9875f77de3c85f14cdd05e9ac1809e407f29fd61c120d3d1", "package/customPortfolio.py": "e05f32e3c0c6f7a129d128752cdd3602596dcdcff0c1f0ba4289ebd22ed9d76a", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/entities.py": "32fdfcbfe042664eb04a790dd35387b3aeb799702ec794a5eb2a63b70e1bfa7c", "package/feeCard.py": "343c6363c39679810fca4f1498ec3dbf8cbd76361ceccafb5d04723616a634d9", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "75654c95ec9ef8770517393df78a4c0a5e806dc23378ccae2a1c74ed421edd95", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "75e173df01b41481358257ab4e7f05f25f1b177006658ee1cdfaae61db974144", "package/fixturesAdapter.py": "ec7501805601226357435c297d2001d7c9216c45b2610f98e108c058fd070a94", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "7e0d39c670eb6683d75dd9e27e2927246b629a2d97725f9bbed4a6d0ce8f798e", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/houseTemplate.py": "52ea6c0c0d680bef6aa40a8fb99ba298a7925f43d9510e612ea016c69e043a0e", "package/marketing.py": "6731d20f22b044de32892585cdf50a72b205f445251c327525d32a3bb9c525fe", "package/otherCharges.py": "2559db2093b8240638998f250e9c805461719319b9a2bcf4104878d6b7e81914", "package/overlayRules.py": "28f7f58859f55ab8a4032b6ef4ef05bf1eb4f48b263697298694bc10bd4abd69", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "06060d774220c29cc65b2c6917b7ac3cbb37c303fbe50142473a26753fe44d0c", "package/products.py": "6716e03eef84bc86e3f7be25e263abddfd548c70d79aa1327c79f7ba87509357", "package/proposalRegister.py": "0c973f440ee8d1d6b10de32b7f8a905ecf9775e2a111c23e17411e1a7c483ba2", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/repositoryFeed.py": "1bb562fa5017f4d982faaa2407cc35eb6c91a3bc247f65581de0b857ee367d1e", "package/rules.py": "81b50966b2b69d1fde3a5a171bca6c3a8d2e79ace2e088bbe17a46536383a904", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "92b3918327dd9c93b8e8d8d95583d4e059dacaa19990f2683c14271492ee6b13", "package/scenarioStore.py": "efee33953d17fac4633d5f76d280ff91f90ff8d8268d4110ae2515d36559aa3d", "package/sheetDoc.py": "9417ad786e4223e83ef4caee021fbed42cb83d2319d65806f918c6421e31a18c", "package/sleeveRepo.py": "88e8794efb908e0e5cc4a769c4c2ceaabdeda52e7a4d9f71a10d64d58ac71dd4", "package/sleeveRules.py": "087f7ad77fba37a0a1d0955a9078e50b92d97f4b3d3227dd0d5a4558b8425f2f", "package/sleeveTools.py": "081b13d2f19fc12113a4f637badaf40fdd9c821bbc51fee61b91230813c0adba", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "4a00b650ec251bc37f8dd75870e7bda94a1c6cb726027cf12c0f3b1a305f34e5", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "124f17ccd237fff98e775c59a1966c0f0f7942feb5c2bff11a493d441e43b472", "page/proposalTool.html": "5426ad23c005de365da3531a2b01120887436595179a119a40a93677f7dbbcd4", "page/static/css/proposalTool.css": "1488030dbd77a625656e80c583449fe27cba8dfd7f8b1bdd393c329be86c328b", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "47d7a2b359d5c12763b8a706fe458e22bfc42a9f4a9e3c39a5c0bcb53e2aec4b"}''')
TARGET = json.loads(r'''{"package/notices.py": "7a72db303f9aa46896e256e976931ee6b79a1f3868646e8b9c8a4ae49a4f99a0", "package/products.py": "ef9c47a93216cd4f0d76862bce65aa6d74b0523cd34345f83dbf842e8b53c3e6", "package/proposalRegister.py": "76857fc0148dabcf74f9648dd1f78207909a5999f7a753c3ff1ef1a7d23bc425", "package/rules.py": "1a7697c06b2763c67de57dfcc088aee3c6969daaa3ecd024d4554dde59d0a589", "package/scenarioStore.py": "225f02e3be6d554c4e5069c9fb287b53c4b5da9e08036124b8855a79719d706b", "page/proposalTool.html": "07834491e82f22aa6394f3265772782ebebdda08a8858ca28625af4420f4a397", "page/static/css/proposalTool.css": "b6f012f897c9e6825dd1885c7ff23074053c83681a3f4df27dc3adb5cccb2f48", "page/static/js/proposalTool.js": "dff82f90a8a1b23f6e4729cac75a2f5226aeaf7b7d1be549ebe60fb94f3c921d"}''')
WRITE = json.loads(r'''["package/notices.py", "package/products.py", "package/proposalRegister.py", "package/rules.py", "package/scenarioStore.py", "page/proposalTool.html", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "a2610be0fa3a155d", "changed": true, "target": "ebc003d930a1a7fe"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "_otherCharges": ["from", "cyrus_pmg.pmgService.scenario", "otherCharges", "_otherCharges"], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "customAccess": ["from", "cyrus_pmg.pmgService.scenario", "customAccess", null], "customPortfolio": ["from", "cyrus_pmg.pmgService.scenario", "customPortfolio", null], "customSleeves": ["from", "cyrus_pmg.pmgService.scenario", "customSleeves", null], "entities": ["from", "cyrus_pmg.pmgService.scenario", "entities", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "feeCard": ["from", "cyrus_pmg.pmgService.scenario", "feeCard", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "notices": ["from", "cyrus_pmg.pmgService.scenario", "notices", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "repositoryFeed": ["from", "cyrus_pmg.pmgService.scenario", "repositoryFeed", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('72')
#: what this update carries, and a short id from the files it writes, so each
#: run says which update it is
TITLE = json.loads(r'''"Notices: the Notices tab in place of Marketing, notices confirmed when a sleeve or product is attached, and bold words in a notice"''')
UPDATE_ID = hashlib.sha256(json.dumps(TARGET, sort_keys=True).encode('ascii')).hexdigest()[:8]
PAYLOAD = """
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
"""

if __name__ == '__main__':
    sys.exit(main())
