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
TARGET = json.loads(r'''{"package/customPortfolio.py": "e05f32e3c0c6f7a129d128752cdd3602596dcdcff0c1f0ba4289ebd22ed9d76a", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/products.py": "22fb52e944517af8a793a7255278c309849d1752f0cdfae5f516906cb2896c60", "package/repositoryFeed.py": "1bb562fa5017f4d982faaa2407cc35eb6c91a3bc247f65581de0b857ee367d1e", "package/sleeveRepo.py": "23779a9b7b5d995dc4637ce0f9dd70f4e1a0788d38b33187a6fc3ef1e33c3b00", "package/sleeveTools.py": "1c711fb517ffb3cb51151c9ea10342ad1b98df5849b9a008c0d1c2c65d1155d5", "page/static/css/proposalTool.css": "62efcd8907395b25d6258d5f07b943eb49afc843be4bc31ee2355cc0da22d6fe", "page/static/js/proposalTool.js": "6619b88637e237165b3e7059fda64da5be8769e72a6bb15df59a5136a9f6a748"}''')
WRITE = json.loads(r'''["package/customPortfolio.py", "package/customSleeves.py", "package/products.py", "package/repositoryFeed.py", "package/sleeveRepo.py", "package/sleeveTools.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "4c78d2e0da5b1fd6", "changed": true, "target": "15ea3107ae5b3090"}''')
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
