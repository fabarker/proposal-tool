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
BASE = json.loads(r'''{"package/__init__.py": "d13f9cc8dde33a81a4df3f863ab9680bcae7d21a86d02a4e87c2b7490a666e1f", "package/accountRequests.py": "0d0352931d5462a5e0fa64c0e2422f712c413d224b833801b3f366ec4a65cf52", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "dbe73f522b121de5e5b5450ef8615c918a7577805a95e83769732a0e45252755", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/customAccess.py": "44dab81cf3574c2f9875f77de3c85f14cdd05e9ac1809e407f29fd61c120d3d1", "package/customPortfolio.py": "e05f32e3c0c6f7a129d128752cdd3602596dcdcff0c1f0ba4289ebd22ed9d76a", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/entities.py": "1311fd18d11e3a8c1950fa480cc182623ddd26f387fea42256da6ee3582477b0", "package/feeCard.py": "343c6363c39679810fca4f1498ec3dbf8cbd76361ceccafb5d04723616a634d9", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "75654c95ec9ef8770517393df78a4c0a5e806dc23378ccae2a1c74ed421edd95", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "75e173df01b41481358257ab4e7f05f25f1b177006658ee1cdfaae61db974144", "package/fixturesAdapter.py": "ec7501805601226357435c297d2001d7c9216c45b2610f98e108c058fd070a94", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "7e0d39c670eb6683d75dd9e27e2927246b629a2d97725f9bbed4a6d0ce8f798e", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/houseTemplate.py": "52ea6c0c0d680bef6aa40a8fb99ba298a7925f43d9510e612ea016c69e043a0e", "package/otherCharges.py": "2559db2093b8240638998f250e9c805461719319b9a2bcf4104878d6b7e81914", "package/overlayRules.py": "0ef24656d19e247f86ebd7e1988e4049846baaf0a4737f0c74657b23a01c7e7a", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "06060d774220c29cc65b2c6917b7ac3cbb37c303fbe50142473a26753fe44d0c", "package/products.py": "dd81e757da5b9b471f600ee8fae75f965ade8b70b56d29e3e0ae0e00ab62daa3", "package/proposalRegister.py": "6d095fba4bdc063a096f192715f397afaa985875f0ad9cfd559b287bae95832c", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/repositoryFeed.py": "1bb562fa5017f4d982faaa2407cc35eb6c91a3bc247f65581de0b857ee367d1e", "package/rules.py": "5692d86759a3e1e24a82143f6853a1e0d301df7f9e98cfd263e68fa7ec26ad88", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "f3f4c9547ad12046e529bbd09a7a5d5317e251f55325ec65fe11ad1ef90853ce", "package/scenarioStore.py": "a51dc4bf5494c5ec0572062caaaf5821c3488db5d54b3429246be3f53292cfac", "package/sheetDoc.py": "9417ad786e4223e83ef4caee021fbed42cb83d2319d65806f918c6421e31a18c", "package/sleeveRepo.py": "ce91de29ca12a6a2c9f5b938ef15eff64034efdb193b4a87e7635ad1fd24dfde", "package/sleeveRules.py": "a1f3ec3dfc24c38db02206535d1aebc70c478afa7d64189db7e9fd2415bbbb74", "package/sleeveTools.py": "5eed74f201d2ef949d34643160d45d1bad035c7b7681b8c566245e932fa2ab9f", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "4a00b650ec251bc37f8dd75870e7bda94a1c6cb726027cf12c0f3b1a305f34e5", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "124f17ccd237fff98e775c59a1966c0f0f7942feb5c2bff11a493d441e43b472", "page/proposalTool.html": "372a0389afbd4e586be216a22bd77feffd5f9fc45c18ed351476ef1ec7bd4168", "page/static/css/proposalTool.css": "9ace03519ba9a4c48eef082b544f0f6ebc183596bc8a30b09608dd960f0b9e28", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "93e8e2aa805a600aa9f0e818c8dac5af2b642330ad1c7f7b30a8e75cce1609f5"}''')
TARGET = json.loads(r'''{"package/entities.py": "32fdfcbfe042664eb04a790dd35387b3aeb799702ec794a5eb2a63b70e1bfa7c", "package/marketing.py": "5074896c10a45158850e673b6c71f3eb92c29cc766b4b85b15d590e87034946a", "package/overlayRules.py": "28f7f58859f55ab8a4032b6ef4ef05bf1eb4f48b263697298694bc10bd4abd69", "package/products.py": "116e2bb02de7c1bb46abe1dc940923f68eb0d458bbc0eab0d865946886dec242", "package/proposalRegister.py": "d7629c476a795b1ec3be69c87403e2a2819fcfa5239be5bdac68a87d92d99d96", "package/rules.py": "b7dcb5d493fa03077a53996f62955660037ab6ccbe5abf38c5938ac8f9e60d27", "package/scenarioPort.py": "92b3918327dd9c93b8e8d8d95583d4e059dacaa19990f2683c14271492ee6b13", "package/scenarioStore.py": "68c57af4743a3743bb9bcdcc8697c49da9b61964da552fb4268cf5a7ad007c0b", "package/sleeveRepo.py": "9f8fe2dcdf98b4694f97e5b5b4ef5a4215a9203e3b191523395da09c9767b5b6", "package/sleeveRules.py": "087f7ad77fba37a0a1d0955a9078e50b92d97f4b3d3227dd0d5a4558b8425f2f", "page/proposalTool.html": "77dab8c6fc304d9f27c7ce8a571d4f7dab11aebaff9e066292bfe30c29b1a2cc", "page/static/css/proposalTool.css": "c36bf01bc9cddd4640d0850f3498c178ff70b62c6786effae144352c82462896", "page/static/js/proposalTool.js": "a14e9b97ce8862678bb95d59cf2b9b449d3c2ba5e3d61bb0696c9e9a5a29901f"}''')
WRITE = json.loads(r'''["package/entities.py", "package/marketing.py", "package/overlayRules.py", "package/products.py", "package/proposalRegister.py", "package/rules.py", "package/scenarioPort.py", "package/scenarioStore.py", "package/sleeveRepo.py", "package/sleeveRules.py", "page/proposalTool.html", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "184257d5e251027a", "changed": true, "target": "7e51c483b4809fc1"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "_otherCharges": ["from", "cyrus_pmg.pmgService.scenario", "otherCharges", "_otherCharges"], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "customAccess": ["from", "cyrus_pmg.pmgService.scenario", "customAccess", null], "customPortfolio": ["from", "cyrus_pmg.pmgService.scenario", "customPortfolio", null], "customSleeves": ["from", "cyrus_pmg.pmgService.scenario", "customSleeves", null], "entities": ["from", "cyrus_pmg.pmgService.scenario", "entities", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "feeCard": ["from", "cyrus_pmg.pmgService.scenario", "feeCard", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "marketing": ["from", "cyrus_pmg.pmgService.scenario", "marketing", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "repositoryFeed": ["from", "cyrus_pmg.pmgService.scenario", "repositoryFeed", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('69')
#: what this update carries, and a short id from the files it writes, so each
#: run says which update it is
TITLE = json.loads(r'''"cost and asset class by currency, the custodian under PMG ESG, marketing warnings"''')
UPDATE_ID = hashlib.sha256(json.dumps(TARGET, sort_keys=True).encode('ascii')).hexdigest()[:8]
PAYLOAD = """
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
"""

if __name__ == '__main__':
    sys.exit(main())
