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
BASE = json.loads(r'''{"package/__init__.py": "d13f9cc8dde33a81a4df3f863ab9680bcae7d21a86d02a4e87c2b7490a666e1f", "package/accountRequests.py": "0d0352931d5462a5e0fa64c0e2422f712c413d224b833801b3f366ec4a65cf52", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "9dde28f78d2b4e5e60572155990986c622186fb467aede6d62f354c6066105e7", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/customAccess.py": "44dab81cf3574c2f9875f77de3c85f14cdd05e9ac1809e407f29fd61c120d3d1", "package/customPortfolio.py": "e05f32e3c0c6f7a129d128752cdd3602596dcdcff0c1f0ba4289ebd22ed9d76a", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/feeCard.py": "343c6363c39679810fca4f1498ec3dbf8cbd76361ceccafb5d04723616a634d9", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "75654c95ec9ef8770517393df78a4c0a5e806dc23378ccae2a1c74ed421edd95", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "75e173df01b41481358257ab4e7f05f25f1b177006658ee1cdfaae61db974144", "package/fixturesAdapter.py": "561b0eaea5a41d7301b4810de8c5a934c619758c1955f90e89efe035cd32b317", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "7e0d39c670eb6683d75dd9e27e2927246b629a2d97725f9bbed4a6d0ce8f798e", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/houseTemplate.py": "52ea6c0c0d680bef6aa40a8fb99ba298a7925f43d9510e612ea016c69e043a0e", "package/otherCharges.py": "2559db2093b8240638998f250e9c805461719319b9a2bcf4104878d6b7e81914", "package/overlayRules.py": "f20fbc114c366dcc5b1e6aab6f74f94ebb218aa8e168a81d9783540ad437ad24", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "06060d774220c29cc65b2c6917b7ac3cbb37c303fbe50142473a26753fe44d0c", "package/products.py": "235deba0e6146b14a1287903c138d5abe9ade55965d0a02f9d583cea052662bf", "package/proposalRegister.py": "363eef64bb675df8adefe386b8af46e625ae4fd155c944877d6a7d0cfb3638cd", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/repositoryFeed.py": "1bb562fa5017f4d982faaa2407cc35eb6c91a3bc247f65581de0b857ee367d1e", "package/rules.py": "85d52a58b21a59cabf85b4e48dc276ee4fcfca804f48dccf02919d53217b020d", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "529416459004d3a0804663e1cb58913deea8f40e815a68366b02b38b4d3dab38", "package/scenarioStore.py": "a51dc4bf5494c5ec0572062caaaf5821c3488db5d54b3429246be3f53292cfac", "package/sheetDoc.py": "9417ad786e4223e83ef4caee021fbed42cb83d2319d65806f918c6421e31a18c", "package/sleeveRepo.py": "ccec707af12f3d02d2c4e9f62bf3e4f9984dfddd9717d80b80256677939f024d", "package/sleeveRules.py": "7fa0ae37696ac0060c141faf05f1b5fb29067ee30c8024599384b6d4e0bbf253", "package/sleeveTools.py": "fc5a594fef48d540dee2d22d1b46803e0c611b7e660fd82b1f06e51141ea1a31", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "a3550c15086820986e7e0354e9a5997dbd23bbd2bb8c4d0615e7cc461c41f8ea", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "edf00654f568b38c86744c3191e63687abbfde137d6e8de491c75bde72a398de", "page/proposalTool.html": "372a0389afbd4e586be216a22bd77feffd5f9fc45c18ed351476ef1ec7bd4168", "page/static/css/proposalTool.css": "4b9f097f4d5e7d4dece0004bae388b1c130dc1d6f9d39e72c8bef357dd1646df", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "2c0a53934972814197d1249cd8739546dd81f53de7ece819535de15eaf2884dc"}''')
TARGET = json.loads(r'''{"package/overlayRules.py": "8d871f1f5002b55066394642bf0dea066927812bb7a2de25c6a7454eef0d3a82", "package/proposalRegister.py": "9eb59faae3064cd26d790b4b5a04b5a536f917cdcad9c581a39777aa3737cf75", "package/rules.py": "48fd604a87d2276f035fcb3cd0f77d7694e3f5bc90d1674558c0089609dac01d", "package/sleeveRepo.py": "d0722caede593ef894677d04974fb2018ca8c43d1d0bda2e04153eb1452966ef", "package/sleeveTools.py": "87a1734ef7cdf12b34fe150004c96860586f93cc5ee9e6f822a30578b53f14b9", "package/workbook.py": "d1ba1ea5b616ee0badfcebafa0efa877d1c69aa630996bf9b6c8fc4a8321d27a", "page/static/js/proposalTool.js": "72c1ff5300e22e1f5bb19e9f002c93a61d0ea10c1e3787c0e14e021a330d0cd7"}''')
WRITE = json.loads(r'''["package/overlayRules.py", "package/proposalRegister.py", "package/rules.py", "package/sleeveRepo.py", "package/sleeveTools.py", "package/workbook.py", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "c59a9b965e6601e3", "changed": true, "target": "b6e7ff710d4051f0"}''')
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
