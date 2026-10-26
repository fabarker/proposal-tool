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
BASE = json.loads(r'''{"package/__init__.py": "d13f9cc8dde33a81a4df3f863ab9680bcae7d21a86d02a4e87c2b7490a666e1f", "package/accountRequests.py": "9832bfb422e79f89b12f836979aba6cab5ee5642f1d4365cbe0b9df6edf6de71", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "dbe73f522b121de5e5b5450ef8615c918a7577805a95e83769732a0e45252755", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/customAccess.py": "44dab81cf3574c2f9875f77de3c85f14cdd05e9ac1809e407f29fd61c120d3d1", "package/customPortfolio.py": "f5031bf9e3af86bc0ac55e6d9cbb3a557ad4b0f3086c0446c5a7fbbe1f083c71", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/entities.py": "32fdfcbfe042664eb04a790dd35387b3aeb799702ec794a5eb2a63b70e1bfa7c", "package/feeCard.py": "343c6363c39679810fca4f1498ec3dbf8cbd76361ceccafb5d04723616a634d9", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "75654c95ec9ef8770517393df78a4c0a5e806dc23378ccae2a1c74ed421edd95", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "75e173df01b41481358257ab4e7f05f25f1b177006658ee1cdfaae61db974144", "package/fixturesAdapter.py": "ec7501805601226357435c297d2001d7c9216c45b2610f98e108c058fd070a94", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "7e0d39c670eb6683d75dd9e27e2927246b629a2d97725f9bbed4a6d0ce8f798e", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/houseTemplate.py": "52ea6c0c0d680bef6aa40a8fb99ba298a7925f43d9510e612ea016c69e043a0e", "package/mandateCodes.py": "b58cc7c083b4150afb154741a5977c9bb45b55ebcd27e43e59d83419817e8bf7", "package/marketing.py": "6731d20f22b044de32892585cdf50a72b205f445251c327525d32a3bb9c525fe", "package/notices.py": "240dbbad53a5a3ed174ab6e8b6afad411d686f5d4f36db35ec0cb4ac43d05d90", "package/otherCharges.py": "2559db2093b8240638998f250e9c805461719319b9a2bcf4104878d6b7e81914", "package/overlayRules.py": "28f7f58859f55ab8a4032b6ef4ef05bf1eb4f48b263697298694bc10bd4abd69", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolioBenchmarks.py": "3fed957b32a2a4a957bdc1b8c83562b7395d3758650c34af8b62883922a5ecbc", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "06060d774220c29cc65b2c6917b7ac3cbb37c303fbe50142473a26753fe44d0c", "package/privateMarkets.py": "8704c0c9cc233e0df6105b93de3286d52a95bf72f0a20450799a6a8348d69225", "package/products.py": "b437ec60305b223858654335eebd018269ed5b0d6ba8830f68efc55bd1737dd2", "package/proposalRegister.py": "a9a4a0bdc6e75dae19d57c04f20bee9d8843215b9dddc7b9895fd3ce449c4f19", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/repositoryFeed.py": "d4241dc9136c98d6216be6d0c4ace45062ed97a1937080ea63d9b03a159bd558", "package/restrictions.py": "0d73b8ea7866bbf84416a65b10e915d3a4a0aed88bc14f8209d1d224a066533f", "package/rules.py": "d058a6fc87c23734be22e38a9b9f71207a7347499bb795d5a42828cad391d1ef", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "92b3918327dd9c93b8e8d8d95583d4e059dacaa19990f2683c14271492ee6b13", "package/scenarioStore.py": "225f02e3be6d554c4e5069c9fb287b53c4b5da9e08036124b8855a79719d706b", "package/sheetDoc.py": "65f27fbda0ce94328addb4eed491f78f2101a0ea02fe7338b4f8c9c11efa659b", "package/sleeveRepo.py": "f317568e3f75976364eba8d1da10832234c19e578f19c85640054407554db881", "package/sleeveRules.py": "087f7ad77fba37a0a1d0955a9078e50b92d97f4b3d3227dd0d5a4558b8425f2f", "package/sleeveTools.py": "f34beced7034b22a736f34dce335e8db74054499cf1aa4ee244ef0add8277ad3", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "4a00b650ec251bc37f8dd75870e7bda94a1c6cb726027cf12c0f3b1a305f34e5", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "45bb3b05878c351321bae9e5c663716896cf16cee2a92f676048df5da4553580", "page/proposalTool.html": "07834491e82f22aa6394f3265772782ebebdda08a8858ca28625af4420f4a397", "page/static/css/proposalTool.css": "2398ca3612e5fac77f576eaa885410077f98d39c414d68d36ca2b07c6b1b2786", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "b9dc954cf6c7bbcef4709fefa895c3a761b22a5d05aeffa002751e722e93cda7"}''')
TARGET = json.loads(r'''{"package/accountRequests.py": "6a2206a8e4a78b5048fbd5db6ca38e0b6a1cf5cb960425bfe75dddba4b979b7a", "package/mandateCodes.py": "146976726d8ea2efe37ad748f0abf3b795badbb2885709b5b332b44963039eb9", "package/products.py": "7752ce8b69b71d611174a85039391024f197b3988b4f2728de10ca5521d0b666", "package/proposalRegister.py": "44cbd0f49dacc4c5e5a9a84ddc355d4b98c98460d40e51c73e492da6718064fa", "package/workbook.py": "695ef48afa00c37dd8cf5685b19fc82c663cc730c37451ca088caede53871d57", "page/static/css/proposalTool.css": "09e6efb5c18e8bf1199aa3d53513861a57dd42f80cc4a3a0ba798e794c384bea", "page/static/js/proposalTool.js": "3220b697891e7fb62893f0380d744544ccde737461dfaeefcfd5e1b15ba26bc2"}''')
WRITE = json.loads(r'''["package/accountRequests.py", "package/mandateCodes.py", "package/products.py", "package/proposalRegister.py", "package/workbook.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "5cab67eaebe8f152", "changed": true, "target": "3278200689dcb6a4"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "_otherCharges": ["from", "cyrus_pmg.pmgService.scenario", "otherCharges", "_otherCharges"], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "customAccess": ["from", "cyrus_pmg.pmgService.scenario", "customAccess", null], "customPortfolio": ["from", "cyrus_pmg.pmgService.scenario", "customPortfolio", null], "customSleeves": ["from", "cyrus_pmg.pmgService.scenario", "customSleeves", null], "entities": ["from", "cyrus_pmg.pmgService.scenario", "entities", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "feeCard": ["from", "cyrus_pmg.pmgService.scenario", "feeCard", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "mandateCodes": ["from", "cyrus_pmg.pmgService.scenario", "mandateCodes", null], "notices": ["from", "cyrus_pmg.pmgService.scenario", "notices", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "portfolioBenchmarks": ["from", "cyrus_pmg.pmgService.scenario", "portfolioBenchmarks", null], "privateMarkets": ["from", "cyrus_pmg.pmgService.scenario", "privateMarkets", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "repositoryFeed": ["from", "cyrus_pmg.pmgService.scenario", "repositoryFeed", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "restrictions": ["from", "cyrus_pmg.pmgService.scenario", "restrictions", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('95')
#: what this update carries, and a short id from the files it writes, so each
#: run says which update it is
TITLE = json.loads(r'''"Product catalogue facts only, listings dragged into order, mandate codes by group"''')
UPDATE_ID = hashlib.sha256(json.dumps(TARGET, sort_keys=True).encode('ascii')).hexdigest()[:8]
PAYLOAD = """
UEsDBBQAAAAIAAAAIQDSweSeimAAANNJAQAMAAAAcGF0Y2hlcy5qc29u7b0LdxtHsib4V0ruMxeA
DMIAHyAJmvJSEmVrW7Y8knp67oF55CJQJKuJ10UVSPGy+d8nvojIZxVAyu67M7tn1W0CqEdmZGRk
ZLzz/ptFOrpOL7Pv0tFovpqVH7L/WGVFWXQWd98MkuFwe7/fTrb399vJ8JtxdpF8vsom43d5Ueaz
y+ZiOR+vRmU7ycts2hp8c3ZGj20fdOmNgx28kdC/q3SczGdZspXMl+3kYr5M0mSS04VxNslvsmU2
Ts4zupzRVW62aCflVcbvpAl1sZgX6eSbdiKtzSfjIpnNb9FpkVzky6LsJL/gYWlaYaJHksl8dpkt
k3yG9kwDr9IyncwvV1nnt2/wPwP0IQF9aIE2oCTHpsHOZVY2G+Z6o0WjSYZnptVlVq6WM/vasHuW
5BeulWxSZAyk9Laz02snOzvbpjevR+pwA4r19f4uvd7v+6/Tc+eTbEqvNxu/zJNpOhunZZaM5mNB
zP3DACi5XM5XC2DkRKY7eb/IZtRPO/lZ33hFbxSK5pxQezJL0vGU3miYsUb/GumYH03KOaFfOiB8
L7NOo0M9T9OyqdBw051RNpn8fb4cF018+2t212phXDQwS4vB80qIvQOaIMbYz/OiNAMskuIqXYJQ
MNJ2UsyZdkY8CNy4JBKbJed3yY8f3v/tVxqOQIix5PREMkun9Jp5nRrl923rNCxGxYAv59MF4Tib
lWmZz4mq7hZZ0UZT6WQyH/FFIt5lXlwnk+yGJr2djFbLZTYb5QCHGr/KxpeY5cV8kvPF0dW8IAAJ
vzlN8smECf0W6EPfdJdug95nWSf5RCAw9DK062xR4kXCZbYU2FM78WV6nRVoy6AjmV/wGN68/fDx
k52mtLRTTQu0mDNGlsv5rT5xOQfg5/MbQjFDlo95ZZruJinNhTxK7RP42hyNfnlXXmGooH2Ld+kS
k8LAzRylYjUznFvyMq9/g34eDk+joTCBiaHOualldrEqsjGRQD4b8Z3b+WoC1jO5S6Z5McnScSf5
26zMJ4wH5hxJkVIrZYBXgzSioOsG9Xw7p8GWtMJv8gKz3h0kv/78Y3L68ce2QmqGwBimR0EX7eTH
nSMgCc1F2Ggn++/As+QKkeHtMi/LbDag0S0JzGSGF/gq0b8yKGJN4BrDb05cfzT3Fxf0JPdqV7dO
KKj7x5NfB7ogZsV8gk9a9IWBCzynwP2pzietdeULc+ELBA/vB8xDuJvFfFleEPXOk1E6m81LYt5o
qlidTzGIcbKyKL5MFwDjIp9MsrEbMDNDjLNwa3UQsXpAA4oYz29nkzmhBARPLc9XoytsGHcMdQeP
4j+sDOFS11m2KPwZFe5vEEATGnE+Iq1zIiJiYkQ8TJPEEYiMABY+eH22k/EyvbyknnOCwaw4IUqa
At7bEsAp2ExuaQ4YLRfL+ZSGRijIeHZpYSkFt72le5uXVwrlZDWdFcmP8sTPjg8yaG8D/vOJyezE
sh75/YG4zzswn3bySnjPHc/tT8J5iGbT0RUBbcicV/AFLUi01E7OJ+nsup3wbPNKdzwwKbJFukxL
wf9oPp2mBRY2Bsf8VDA9Se/mq9Ls6ToLW3bwln5AcndYM7KPGwB0q8bDNBOYoCnauc1nPFtY7glt
jcRpQEI6nACvFnB5H61xEyornPKaHV2lJBzIKrFLm+eB5o5m5AqTTuthvgBq0wktL1pxum5usRCm
xIDGdt1k6XKS01K0bdHqIBwkC6CCtrWO2flpGbMwtZqNM0HydU5NNPw9r2E3IjDBYkSLMWk8b7Qj
umbqoRkQ9qf9MsWFyFeWDERZ7DeU2woYmILr7A7TNENr9AJd5c4Z1UPZHs+Jmq6m6fL6jJ8c6ypO
C9MTMXZe4dQVmnG8glmHNIIx2BsFCAD9qKBC/dLA5yRr0RaoONvdayf4b/gN7b/0IvUqN/p0sX+A
G38ZmOlv8LRnR9woM1Ndgv7IbyF+JMsVrTQILeeg1zS5IlyQNArAfzn5+fTzzyf/k8Sp3nYXV1h8
+KjXtrtdvkjdMimkPudtG5bD0xP1DCTz7sd8YgDEa0O1skXytYKFNmbEC10ewzPm3ypd8FA+v377
8+kvH9++/+Ujy4zcH1FYAyIbf0Fv/MX1hF/aEUnAaOvz399/eI0W7rWFQdKoG4lrmR7wBuU6GsgX
HV+jHUqbPhCDtSAN7HeLgcYDg3ny7p0DdUzPQVRpJN8mt4wZWse3mDZ5pgNZu2i2HhSb3qrThRUK
EPKEkxdkS8PLH09PXwO99w0QJeBT0YHxSk/h0o87+GXQN7SPnD0ESHBtnIbihN/W/rvGQ8sDG7yZ
lzrvKzFpEtv6witKRA2VBEhiN5ssevQIncUS3qfSAo0kBUnsSnWv3r/7289KTLx9ASxvA8PP6v6F
q+EOhit2D8MPs4vhu25iSns/nZ4o7cnSp/EziuhBvmDwZS6GvyMCC6kWs4FHS4UovOvfiZ+8SZd5
OivdJeFV+8T491m9/CyY+vz+DYNugLI4M1MZ4c70U49DrK3GoIrLqs7GqwyPBkgeGSQPQoTrcsJl
g/uHkPOBwpJNW/90RcvkipdJRQZQ3JA6u88MHlzp85u3p+9ehyyl9IbJF3i8Me+ImNXAG1Y9l6Cx
iHDdpS2Z/9BWkmz9uX8+u6CfRkSF8YTBb7aSrRes7g/M7IglQgQTxxz5cSvkt6uSrOz/asbQpoD/
Y88wAe42Z9bGrWTjZmvgE0VO2++wdwbxhkWKVTmIaYauddIFScvjJh5tRTYPum3GaMYpQpOOdJyP
4pH+HaSjcqi3cbMQOE3veB918nE0Qu3WUQd/NlseeSiePfrgT74SEAhmofnqbx8+nP7y6u3px1Zl
vfhUY77qxuAP+PMkPc8mzXFO7PMmnawyHnlRLgcR1IDsHT+rjxH+6a3k+NhAr+oRFuea52RM8hzf
rUBTZNkYM10hNIViiDlpki7w/Dk2Q8bCJRu4xmrXasm2iDmIxYUHuXeJe9jfzuLuWQpqqjBOAg+D
cT6fTypUwFJe8pwffW40fV88hOL9nJp4PrDkEmgLluqXRlkIXm8UEe2Q/lLCpkdNdorFJC+bjX82
YoLGUiDVREZgsAIZi98epqVdLfzEcCzSVVq2BWHZbDXNoCc1Y9S1WjGulhmt5ZtM+irWrZd7WMpI
RCRZ/Yux48TmFKhpxobyMPAkFGvvQnuBxUessWLDcCgPrQoQo/n5Wj5z/+DzGQi3NH4AW0RsBrdJ
obLmR4cjHXmF6+Q1hDSoM0ESJEO6eUbgzOrun5MWcP04z/rMQtv7CwWoLZbDYxhtq9MiVsXjyvQR
0HKL9Eq2SfMi5Usx+yKQB7pNDGdnQ9n2zwwe2wnbBPnNQBwNQObb6wCu2WF+ot13Cq3b0794Xamx
zygm+dKzh16Zl/IyNMKXvupj7XFseCQx9p5ba8uzD2cR+fwJBOoOR0IfOiC+3MXmjl7w/YEx+Nmu
ziLYCT2kMueMyRS0hMmQps+Sb0n/+z+DwgGVDDKEagNB034hdna3LRWVfYmAADOTu0Hv2rJTnWjX
mMYODwhWnX/M81kz2AJli7gBHrTfGDoes4DHXyuACb0Ys3EKO80VUfSceBV7jwqiSzAxp1Mlv626
3fN9tkJ7Kqa53Ol0GvWiRMM8YobiMKcTqrvAhl3RDVAkSthr+c8wGA0GANVqPr5zZhM2AyTz2xnb
3bbUkVVn0bHLx1jVImMKrb9LWLStP8NaZpq///4ZDbF96fffW6H3a3sX0O4aaIkoqOtZUaazUdYE
sOL9krYaJEaBubTqyOXePAQWYJQbmleVL+QKCxmNRstpO94TfMU+scbpZP+JBDPE+z7VhdLM2QZp
5uGRHqysAzQMzejOIhyRLIUNonVmNIrtPWDUuuiq2HGz0by/HojJYHitG4DHZNAR3+QRXbceavYB
1xQ/uY79/yikYGWGyCQIyeI5N/CcKEtlDhYnmJBSt2rWmxrZJ8Um8C3n0cEuMqPmIe0NfF/s08yA
CbvD1PRwR3hm6QTawSxTQ7aINti3WDRMJ+yiYncWtyh7SjpazguRMMv0fJI58yWpsdltuLjaIgca
/uNghSODrTyedZVB8SzbxTyESuwteREqcEcKXqPw3Xrsf4DxhZ1bP+/sgiuY5uhn0uzRwuEv261Y
KluweGv1L3P9fP4lK+rkNfFzMLkxjamoEazszUKzNk/3y9VikjWJKP4zmxVEq0MrL6s6kbLQvITh
vTkhwaUqHset0pJoqul5/qUFEe/TcmXJhy3w0bXbK9gj+E4wCPPsm5QEinjfVsBAYwXpTRshrOzQ
2DK46fsKK0HbFnzhIV+iXdaxzwJijYy2lXyLR4cD6BrylUD8NukNzureFQg6hHPiBulqUjbRGlEh
TUKr1VktYEdqSitnrSqQvNrWwS9NyS6OIWhndYQSkgNeHPYGAjiG4ZGGCgU8UH5u0/AEwCEPatg9
q6cFT6jCxMk7reR7/sVor4e1joYCAqR70pjTNbGWj1WCnxnBT0VDJ/Yp/3bsGi+zFQhqWVNDZrwu
HaUcc7RHze5KKm6T+w93Co52MPsEbh0fO85LolAzHhlWptIj67NGfzXX3SiuvSXvrc/FJB0x3gpi
erRkGFWMhGMenn1QfK0x55GhspQuLUWIoHfY5YRZ5l+8+clbXRCNlX+LDI46o422I8tXsPhqesLb
tiP8WNuP3Rh8o63wzosQYBLORWtp3D8kzfuHlg2D0RAV26cn//A2W2VLbTb6bLYpVMhaQ0pIR7px
AhF2BhbgAe+NN/eVJadGDTwKQ6JZUdJqC+PDT9teSwYrtyNdypgNRcKvUVlUVkLs1U6XDcAjEkky
6piVP+/fX4h9gLOTvDVVH6/s4bR7YxsXjwAMX5/4srRQkZT4HasxP3/OzT1Zv49GwFY0T6QzzfKk
soxXa1PAtPMXPFGjwrceHGQm+APaxM5uqE18sjjImQuNsrbIX+llCsHUdwBxa2IVMhEeo3Q51uZ3
Yf7ePfTjyZSXwl6fFywKAZ+Dev68moEVKHI/ExMpsia9OWxAZm6ctZzY3LbS4zE/YH7Rncckffon
O9n45Z28bH+iXf1xUgb3TkrqXke5hwiyvQMbyTiaL+6aDHOVAPwZ5kdUESyOa22nw/FTLabcmMNH
/bCZbI5d50OlKHAMsfIIFF+k4S9+w0xYZ2exPrqHNdbffoyCBsm9mZK2pWeN0Ak1Au7I4is297ho
DDtJ3hRBoSDtISuuEkwCTI5shbSbBu1ZneSDjbgykrPnYjWwqfoB+xRcsYhHcSFUViiUSKrIBDWZ
n6eTxPEMxVQfa03CCliAV3byWVT35iItr1gpmxcdfO9kXxBFpdddoKezryiGi+oKyi+87mufwL+A
q5lFZkz83oLqesg+bjT8JdEI5XU3Jjv2kO79taFo2e8hDriGgETQgKoE+mATBjNvCWKMo9JKDgW6
TBehAQIBxtv7B2Hr97M5tFpS79HymVq1faXRmKrDXqLI3m0bjvznfHuxY0+bh+nkYM9ylWxWLu9I
hbhtCwbaxIKzyfhYl7Czc+nrCK8+2PdZ7zLNiYj+RzrJx2zBOl0u58smt9KGUDEwWi0HLyGWxUXa
cpetVhDU3P26xhFBZwPzEB9DYszoKl2mo5LUo6irdvLq/WsOlbGdIohaI6mFzbJkIIZBZhqKEYsf
iBLrTBYn1jlIzeSjzNgJPEcQGydkcQ5qgtbymaeKx87TttiHTHQM0Sbpj6MrLwJVvC1T5kDzecRC
KtZqcHkRHY6TRiP4efLuXZ29zMmrkUFJMEWUEgp4RjIZ8pezyIxbbaDGUPcoBdB0SfQZzTwbR9BI
ZeI9o7DTC2B3vWXFwAqIVmXIR9eiMkAt9aV01gatZT7mkHK3BoO1WAxeUjedAlV9+VFM3D9bPhj5
x1ED8GJYj0NL3UauqGJo1mDMoaaTjsfq7o18HZ4cbxBspHh5NTbLO7GdWrAyu76ru1SdyyAfTwJf
ZM2KHDbZDdmWILqWrB3PieR5JIkrO+OisQZGS0gDko9DSbi1KZyBqNq4TPjtVqgg/We+aDrJBa23
KiEPorAZN09Vg5rPynwWGgR0hemb6orZFCvRnDER2YyVGhfrQAP8RWKXqNv0mt1odN+RlqhkonWe
RVaydfCKQ+6YBKEvEFD1ZSeIqutxMGNSgb9bnVGkRLTU8ec7p1oVy3QIRzzy08itbDGRb4jmb3Oe
ylRdiejXQ0ljg4bQWI+vtrTugVvjM9OtC3k13cMaGSedzWc5iaVW7nTB2+ysiZiIH41AerYlE14n
HGUsmSdmH5vfzjShgjddPCHRPvkSK/eyvCrafgSEXQ4aCAEgeNtz0UJoTpalxOTSqpxkiPUOTNV+
YzWrs7qtQKNTXwdHXVTvPsFVVMt3zStEOx+zMA/H5POYvQhYkKc7TrBVhhe6aVrJi8SF7n4dFH+F
ICR5TUS8pj9DY65VR1jrzO9EaW2e74KZWTuyhtFyIXkx5GLRMALIRbI+1phBaswCNYPJqlXDtrw5
YtGUJ/CJG6KHEisdEFp4NsQ7WhE/vXaZzKG3gA9R374bsA5S3PkqwO4fuDGLgpaw3RmpNibBayOA
Sjd4DtRiQr//BTB4sjRjYb0svUF2MPBUkYWBYUNfNnkHZPr601CrjxCSH00zPpZgYcSxoJRnY2bQ
znmYbuTKPoOO2J0bO8M99EcDZ0EPETDygYmpkFT8RhT8w2z02NPGfN9yW7P+DBb4qsNCq3bbH38t
VUathlTpM7YQFWH3FZOzQr7JbKwm49gKNdho6A3UNIMv+tFyaPL5DBumLSJZg3uS7RcCJnpjQROy
qa/90LUnc+iIdvHusHtG/yegzI9qrKpnrKXO1Ve/g7TSHRer4QX3X2VZKTGqJO1en8/n10gPIqCu
shReIOHpqXW2tDVPzkpidRHR7Yp6yft4YLXY2UWG8G6QIUwoknnH7F6J65rWqDJTdxECyWTSXLor
7MfjIOAPp//9b28/nL5umWztnV3IPLsuUKEe52xGJlL4Za5IocYg0aVFkLd2/xASc9sLENIEgVbL
ZjEjF6rvDAWsyiyZ6CwZcIhBZ7maQKWX7J8Pbz/+9fO70/9x+u7zu5OXp+8+rtt9A+2zEmdbp4xP
0un5OAX5DwQc2SzRMO3ZFwMvfPdC5OEL41t5aCNS6u3Jx9OPrZo+NQ75a/usjDUm6A1vRxZgTRqv
EW5N7IGE8msOk6bCwTOO/NQ5SZB8CQFX92oRX85vC2u/1gR0hP6xZnhmbLMPbJOtS3zUkgBBDsGm
hZM0n8vv5632E/MJQ8l7U5pg4nIE22xh1sspMhhGV6zX+u6hSI62aT+SyApUYNwvTcacLhFjTICN
uhMq1jpN+11kCCLTV3/TjO0cWBueIuRYs1isicPCNmPDMHisWeugQ32NFf+mTYiR7dCGU+RFIRUI
hi5HZXhx5ghd26b2LlzHZwo47Kw7B5aRyGouNN/KLu5NbpI4AMeQlOrxoQiN/7osSUv3h8gS7Aa2
ziLNq3ENX7NDQio/Fmsl/Fm0xG1+ilhx4hcksZAApDfPYrwPJdfsVmJN1K++bAzbR7/9Njuj+bAy
cosdDLedolzmC5Ju4m4wMOuhbRJcimy+1rzVOEXuSNaiJlJ2e8BRP9pUrAtlnAwXEj5LLeJl7kd5
1xm7toeIBgVdffr3M43rKhVuD02SnlMTKyMNCgMW4OGncG2GQzW51+fpuOLd/9qptNbHuiECbRUk
o9dv6XmIbb99c//w2ze+JdA5oDng2e563sqJiOXMTPBt1YDjj+g2hgxztDAm5rN6b3/oryeEOtug
Ep8Me+FdN80a566lkn1QScUbbFajEezEQPpQsQs3jsyOTxi0u/0u6nfsbgetrlOXv3Zu61AAlQ6G
Zu9ea23wwm+/1U3I5vAJr+FgTjgM2iq8/qJQndeoFD5rqAJQiUnW8Oq27ZiF3fXclM2c5lEJp41M
co0aPYd7qS6euqmfzaVMwRoNW2YdUR27O4F7y6g0Gj2heg13HCo2SN+a30Ln4YAIDUDxYUOhHGh+
2BZYYeFWjFIY63H89KZghpAX4nGblqEBVYN6b33V+lqLtQ+IWHeaNWvVLEoAnSI82LubbQMCG7BD
WIaXR7Dngyxu+1a0CdU1FsNlNX6XZuTDU9v302zSui7ljU22llqCs8aUS0t2j7sn680n6wAUy0JA
RWApQQqVk1DWqLgG4cGo/sKQ11S14Oh/qbPDwmdNN4ixayK0TqX9y0GyVaymzd7m1c9RKRWmmkN0
flJ+Cp4+91wFuYRo1q4B6u4JEKXMip4dP+XZczw7WLdRBkldESCpXbXU07n5sb4pkLr6SZrXtfH3
4glJI8/IOX6TJsIZV+tax0iog8EmK9l/IaPw/6V21Z7bb64YFzqBWuX4h0Wd4+a0fe/u9uLQmCDn
43pAhOdHp8bqBlFRRfKP3VJnsX1cBGtYMNhyFEVRRfHqNehMRFgJPUSSeKeYEHdm1WAU2t24Y+Qc
iwaMlwverlg/aZiu6ZJTXhrCDuiafNGKALtc12R3P1TI/25Ly9DT5W2WzVi15P5c/QZBh1/YidOh
BrbyittopfIKQ3+bobDTUmvfjNt+ckKU6+eCbGz0BLxw6YyTOOpcRbcpp0xAYZJh+kF/6QUxZ3fB
e+flHQjI+icN1yUicjRBj1l6oB6e8go99mDQDMFzz1rS/EfC3D+WJRgkliUqHcTyxFzEiVpRwjOA
Wi+N1JgCGRrZzidI5668dEvQz5JrPXmPnWODVn8vsUDb9NOgXGaSgeIB5zXYds3VIcRnvZdrWa/f
r2Fpgpbt3uH2GrT4rUOapYe7vd2Ghy//cusPaxNat5EHLToCjcWEeeL3uhzNSDF6dEBeOEg7zta0
nbef5l6J/0XNKeStIOzGrK04OKJC+MbWw2vvaUTkSqM1fXJv/TF65xqPrLxXYfNHUg+9gfwszuDY
1Bo42brWmEf4lmWFj+iEmx6scYUoeuSZevKQGl8zo/XVxG1Q53+UKB5vnEbtoZ0EnnN2DWkUO3ft
fuLhOBXLCXajSIZKJbWCV9O5fvdl5mrKVrik3Jak5MUSkNuOXLFRqcvWaNhW/ZyMovEHkRcgsqhv
vGElJRRn3e3vV4KIJXoX1t+hyhJnnhhjXsaO1a8YYJwcIe/rzzOWMNTM/IqtzN4j4fVquLkT3aCZ
rQMMwhumUh+Lg9IVbpiud/d7FbgljFffvUwXhUYWD80d7uA8LVjQCtMCdBff65GwtNeL82k114BD
sEmpsgkHmB1z0Vf5ZZ78LFwTR236Qd7u3vZ+TSrEaJKls2zspQDTzH9eANT5Cpm3s5mX6FCf5PdY
fMUndi6mWmEsin6ZYHbubKg+Ql8E5h2il73dbihI/roqjdskG+co2jifJcVomUm1WWki+WCqpoYB
S1IA0aX8alUD9QQhv3WacXXA9IZrYlpZtfkc0/jcxAZxjTKuw6qgQubdi2XeX1emkJTLhHUpLVxL
00KqCZ4mtYU9kKk2jvSGPclxYIeioSKdnLUBjWyesyGDLBwjsBFZf1wbU1xC1its0mvYO8a7zszb
VkeZsbfAVeZlLZgOvjbFX9FYrb8CB4kQn7pE/LwD5/KFiiIIiJzrENX/UcxnHS5c2lwGq8f+0iXz
EGbmjiGDm0yf9DaWBC1g6+RjD/La1RhFQ9sAjiisrLKeh2cP64ISl+1Egtc+6xQ3DRByI5KvPQhx
97HYwT1w/L1+GMhv6A+4iojRvEVcfm/fsvpyvuCsM310uNWDldfnhzw3tt1KuoktjmLfGGAqvTSr
pJGOSntDv+MyCpzKRf6mT86X9kH6WrN/pKU+UPIriluezNWUtp67pllR9u5YtwJ7Y/OmFIXC2Rpe
ZTw2MGTCYJzGzEvBTL72qfnegkZ1fOwhlWPvsBftMYTMfCxd0SfG+C/AbWV3qMP11yC3Pne7ZtQx
IZrBH/Lg+zWcuVrc1rFplLb1eDXHBM6yW8fZtipVI7pRKhfc5M6P7sr22+Qe4iGT7KLkxey7yvtw
Z/Z7O17Vktlqeg7R/jjpDmoYVv3GbyIEaC1FacgzZFoUkyy7yT5ki3kHVzKe1dBZVS6ratGSTSF4
o5N9yUarMms2Pp6+O331KXmevPnw/mcpUXr3kxa1+ftPpx9OpRbwcfJDcvLLay38a349JdDOEiPe
IoGi+de3v7xuJx9fvf/1tK3YabU6F1k5uiKuEbnc8lk6mdzVRcPPOqMJSSbNmM9remgti18j7Fga
YnGH69xzdLyFHMkVF1X5xwss0mFs2JG83NPHS2PEpBFlePpuQJjUrQdQfIIbi8u0NszZmsIybgOz
Mu6Kxoyh05YngcEtYUIZUu8bbSlNDdvHh0zrC3nIjNHWTkiMAmMgOU13oT7k6n7vYG1okFngiYZ7
XagIdsc1z1l6EVFHwuJUzXQFY+qCf8Kl3KMtsL+9VzHSKadvrpOsvQ1+WGdSQCjpsFqkiktUITyr
WlAxnGwWZE/evftqnXEtTRgtr4/jP/o7lnuxCIiq+4u7L5NOUd55IW8nk/xyhpqv7eTNHH9/Tcsy
W87e5JOJtgarfH83sHJOMjyEUK4xArdmCTJjGicvX70+fYMCxM1tmnFMPZy05j/5bcMD+7s7aHfH
b5cX/UziAFEHZCkiJukP+HK8Xa2LxraAZWRvZdbSSc3QaA3aYTZvl+mCHejHqMtBs0WkjvSL4wbt
70bv6e/RlsV/hvUrXJ9ClfC9fqzR6Rt49JiX+X2Dc6+IAdwQvTChYJ/wKOYhjgfYuPZr7QzYdY69
5F1ow8f0RzwxxTGLRZpeIx7cn9MvxybHUiKgcMW4NWs7kZWCx1ySgnAJfvX9J/GIRoeu6EkzwYEr
ckQN50RyAaaJU6nhBsbWDT5iipHfGV1bDnOgSbuT6Flzio052oFdDHrcg634aq7yaTuJpN3oWT2a
OnRBklHBJ3oM5PQQUTCJz7lzf/R8IYGzWGTpdWFhkP627BkD6eyaj+c5v4uHgSCfkR6xwsfI8AEs
b6l3c6YPD8U7hSU1QY2BVyYruXU+PUL1az1rwx2TwdGtfFgE+2bmc47Cs94fZJ+iI2qrRAC+NxJX
MVVOdUHVK3M+Ac8XJ935p0DgQjrl+g/N339Pi59oFn//vXUkmdWKwlG6XOZSCZLBYq2X2iZ5TesA
pVJrFOoK12sHIZQcg8azucwI76Wpt2XnplGYQ0cY61scSPX249tfkv9YzUsxJBSAm4bmlbfPC4m4
kva3knPRvWhZztFMqacLGOi1+BdGW8yTMR9gE8bbM23xHiZ5irxRWfqZiZt+4ZG7O6uhQNDBr/Lo
27HxxzknGmJkmTL8szs0tUsLaGsk6FHygcjPRk5rECxTkwlCEWOADRfVozbqi21brAbHYwwS1Q3N
iAIyb2utMVBv8pLu2ANDBCoxeiC9DK/b6ASbWWaraxe6xRt8sbsPp0dpfW/iwPvMgIVp2KoBUsNx
NtfzjmYebXM5aFoXR9IEgkslyf4Wj1q7ppYXB45u80XmgnpRFHosZwHJWRCYGTnn4Sd3VIIG5U6y
VCufmiLPQj/he/6Mhi/KoRkffz7RUzXGbjrEeiWD6EHB7MkwXAF1gzR/6suQ+SFwyeN93qkRXNOI
wzkwIcHsYlaEweH0mvliwcYzraSXc1w3SMSARvPTk1IBEmluyobduzPPBknjnflOWySTvHdR1gDO
H5AmD1FvRSJy7WijEwfqDinAyE7/56/vP3z67M4UYP/bm7fvTs01k0TA8ooTXdnFCpawRdIwjb4h
Eou30tqa4s+l9nRp2V00WASayrFmoYXpGtgod5wwhw69cnN+SsduFH2LzFutHUbKEYP8LclF6m5q
u690We63nEzllS6v0b8UBi5tpiDsAYRDG1y+B5D24iwTedMkiKDmx87+Tiw/FZLXN5wyNLSpjbSg
F4TB9xdsrlvk4zYXE4f5nGPoFLcmZlyC2w8sPAji3jkMZDVtzituZ1r1040dS1tmI6JDF3ZK+N7t
WbOckpo5wMnadKsHDmopkYQDMvyalbpP5Mp6uNqM3RpMJRuAfSRnj9kV7rZP2iNtQTiuZvlE3t58
Psku09Hd85bdufzgWusBsFv+lqmcI6PUhrzk4M0s3LrnNCUCc0H7a34NDiySl4UuWA+7SKnadQVl
p2k++8AB9G3+DiMntTXsxon98Twaghpy4JlGIWFKd/q+SrLhzWBduEMWWceXJ4eOw3FMrYkb2Q7C
c6Il+xnTbQ5pvOKKEdqKLE8JNnUnQlqFCsGw/Z2DipER2UUIFpZAVj+96N3bj5/e/vKjhJJ/fKBW
3IoW/WzP2ixJCcunq+nbGVFHKRqj3SvxZnD+i5YIkaR07FPmfCsJqs44SjwNBDgrAE5u0zva3TjG
0hxWxanZ6h0xzTwXHD8f6GKYJbJNmplg/00gnqEq6gVJXA1eJ3q8HJrCvg031jL52+yKvyOU8dfV
+SQfJaf/scpJ25DGvUSg8mqZZdAf2eRpGb3xHMvzOF7xyJSpBaHbFWdccbq44Yci5liEFck1od8u
Hd1ZsKFLPaqLFRiKZK8YRAlDQGlCayLVy12RdC1BbZszjfwTVA0b0vozpSeaK2WgBE8fVl3hrftd
oud95oR6IA2xi32XaHSAZXXgLAgoSZDPpMaQGRnb5ebm2Alio+Y8IF7TkLs01Etr5oSld9zBGzb9
i7OmvIOsmLUgmkKZixPD/PQrOdjx3JsZi/rCzuw4Syel0DsC4JQ7YD7Md8fd2IvHmR1yRooOXPXL
gTkiwXF+77mwcJlhvCTT8tGpRF9CHu5EUaF9Pihx7DBFV9JxmK0mSaUh8i3qA83uCgfhLVczM9wY
HO+EwYv8crXUM8PY2WsT1HKj+I29NO32o5PIWqXKnz/pqnz78Uf7nU2BZsEGm8QB9IKD/X7NKbji
0dTHIJoeOCHhAFLqwWFv3WvM2UGQxrVxAKni4LAST6GYPCbwX43umgZZrZqAsArHn7iC+mxqq2P1
h11aUYecbiW/IYsc9vZCF6Gw/88s3jcnXnPVzUnHc4hFfNir3ULkpUe3kGoFcene7GD1x5Cc+Oss
VFHk2E14vDn7Eq21rerkSU/rTS6e0UZr5IvwozS55hCg64FabK/bYpgz0ojWJ8bkXSPk6MPJL3/9
qEEehyC8Q1Z25Dc0lV63u2siB8QuYs5ebkVoeK7Xn9u90bfGAAd2tRk+eeEOq46YfTs8WM1Tfy1m
jBqSswVGrDdtsR/5S1OSpGttSMF2pnYkcEJxbVGjOiJV3LpdxofzrMHKvpgvRD9hl0MQ8+6tvs/m
+/sLiz7PN+1Ts2p1xpZm+lG1kw/Bbnrnabfd+jIKZhcLiv4GK4p7IdafLWdpSZsjdxROaHTfNQcd
uLttF2yvC12d/lrWUZTpHZia2ZE4sBoTdMuiljJVW/DdnuMskWJb/nSJRC5i9Hrt48TZNS98adt6
r1y+kmy8Xkl7ZxBjWW0MtYlFTCw+/nLkF/VnoTlce3ZibatiN5ANLGDmvR4KKNDfA98dq8mSAO8z
o+RnIKJpYsVRcJrQ0Frn0rlvMJiwMYhtwTPMi5ViqDfWl491UXMCgUSXSihjg3EW3UJYo7Vh9JCx
KH+HIcfcMJxHzrvyJECpZSq4N0eZI+RFbHIqydg6U7HwOvED3G4dVbXXE1TIRVOump2Pm+etQfIP
5h3/aEvOj0sPYny4glPLwryU0kv5mpwi4PehUix8mJZD7k0rjmtfWkOckSc1xDXRaTGQDvHSAqrZ
mUsQNrl/nLZzHrTHDXEGdVqo7RWha2kh5UEC3qUbiYL4zBY0lwvs/mtiLhkNNi/Q+DkQrGg1xl5v
l4llz+4jPllZ7lV/0M07a7+NLMomhxtHRJ6+2frx5a8/qVCFn3/7+NpEV9OddefbhHG/ConUsZh4
fLVGlPFD6s0oIYrR38N1AliNLOOFmcGrZySzHp+5SH8PvJ0m2iNEnnuSYNTbhwdZ/g71eDXGomH8
sNgrWuEE5yK5cIvxF4gja4778sMUnSo88aZMol48s5IrNArfj23pOT/yPOABDaG+QqWqO+EAka5+
m87AcmE7D9eiGnjqt4/gyHU555j1DexHhX9MycwdRm8VfyjyftwlUZABX8mfhX6rn8xGWTtuT1Q1
CT/S0Eca5cdPJ+9O6asEi5rWWGASRT0OqYzomgMeSNjAZxN/XDQHZEq6g8+mhDhEpy+gkE9dOCSH
65D4cpPx3XZiaSZQCD6zNp99JME3k8BR24u1jMoeyjF/oFQ81qqcb5BIKro87BNzHGPTjOq3KZFJ
/CO4Hukm3u28zXtPXXW+HDRVtv5A/DszeRu6UdsCTgMW1inP2KwZOU1FKnW2nlxzzu73P9paY674
ql15dIlrsSL2LFguja8cZEPXl3dWA0vZHEm0Lt1J0kjqhvnVo/yklXbcCAIpIWX3WCes4q0iG9HZ
NEdzhHrmlUJSjq8fD6tENszPokmNxucFkkWF+uNTMfhoAVmNt2GA2GeV5WU9CbiuXrnwYDnnq53o
cQf+23rcW7wc/RZMmf+Ga77SqLL4gE1w++er6UKDcWuOvePKce9ntcwgjtCCMwtWAfCWqOxS7wCl
zWnPtMb5SkkgdSL6zBSWQftYxRPpG0SdQ9l3XxJvDXizOdKbfeWyWYgkH1l3jD7vXOTGWWKI021a
EsWVvA8g0zEfyJgPI11AKwwNjIMQAZz0d8cqXIfwM8nfYd3yhf5ZvL2cgZyP1cHHwWkr2jDNWc59
OczZIlxrtZk91RktTOKruLdNGWPP3ibno6lubKyFszo8cOVwcRQFdsKZq/Vm1cNIbW5I+VcxFYSl
67tABP3di9Hhh719RryQEerkJFQYPcSlGBp+RA6Q0wxx2mTLP9Tyr6f/LsFpUVAQCxQfskvofEsb
HLSHGd47rJunv/BAfZN+m80fkkKNe1uaxQGTBsIVMnZOjwkNdWz7LwlCPeCcUu+GnnuoVOy/zlF0
R1qLfU1jtF+KbDLOsNs7dTcauqn4Z4eMInm9vrUa9VC6rucOimCpTRaO1bzVwqKmfxkG28t1IIAd
i7DtFfAy9YADl91RcpHmE7EVpKrg8TGYxvBWxJYBViKL5GKSXl7yENE3ntO+OVhbBm7CE3AOe2/f
eryw6Qjdlp7FQuKAWGLtJL//zqMu3s5+/93zb5jx+9norDbJyZ38igaVhkYEhHn0Dhw/YFXjcNv8
5miC7a71WWzDCLq9bX0aO/CGecUMw4n3lq09HmBQNcwFpt+ShGXO8tLYuMgyaEmG6IWGVeaj70ZF
YVfNJxLEOnSB6WdnV7zvcs7Od8+rIc4DL95WeZMWWdWlY+tMKqxc5W50lS8KZ/OTmn06vCOZ93Z0
0qOGqaBFxMloa6Ipmx1EotPlYI8yR/fs6WYv3vPv8EonnY+2gND7c0lp7S2+kAxIIg4xs2Vzawuz
3TqSm1vLdJyvioHckR90j9Yaxjwb641itSTtMRPbAXdwuVzcj/OCCO5ucLnMx0f4s0XzQlfKbEuj
Ogb9beqcSHuafml2272LZevoMl0Met3FlyOOMd1ii/CAZmlZHi3S8RgbEW6T/knPCA4U1vN5Wc6n
9eMJIBtM0qLcohmYjO/Dd7vBcy86y2wx35re3GvPW+V8Mdim3oPHOnlBjV3ee2j5y5s3bw5OX1Ye
QxmM8LmXb3be7BC2v2wVVykxw0GOo/aSHRpEl/73l5e72zu9g7AhE5d4Pyeul5d3g87uXvzEfLEl
bOK+0nY32ebW/9J7s0f917zJBra6F7fWvTmNZpsm8YDmh6Z2iwOZA8yW9uGLSfblCH+2EDs8wJ9g
3vkW7ZfcYF9n3bV0Ue0VVHExn5VbRf6f2aBHxHAkEdVbBZBF1NPp9rPpEQwaWyVJJgVUh8GKNqPl
iHTCIyLN+VLpOp9db+14tAOvzGJV3qMDwslVRhKk9HaRTvPJnb52scXh/j4cO529KiTdGAr2aEcA
tJTIDfFTOwlQu2H1bhUlEj7jRbyLt9Yt3SOZpm1Q9xEtR522Xrf737zxjzwExGOeraamld5B10zD
bZZfXpWD/W63gsZhusxTGuIN9Kvj374pl6vst2/OzJIUPOgC8FdIl1cGBl5dHYivqRJFLySKbZ6M
eKK3W4bBGG6wGyx0Dt05Xzv0+Mlimk4m9z4p1va6E7/YARHc6+APX/Ze9V4FmOx3g6WUdL7ck75B
7GAL6VaDdFXOdQkV2eRiMCKNKFv6owfxMJVcSYO9GpiEMMdQzljxFco0JMgMJAJicIWwghBu98g4
nxYbF71d3wHO6TX7Vj5joP2F7j0YYLpbt9j+xLJHDZIOKSDYMhb3bnFs90HmGPcFbdeDq3w8zmbS
hb1IUj5t9HmxprUXBN5swJKMbkhf29w0m60YJp3One0AKKYHx4a3d4LdCy8n/A3E+oR9NF4ifeYW
TH5u0Wyg12y5VHIdRJcHJBuUd3a6QXAPNv4RwYy7eztOYtxjoUzyQ6SJpBzzl5v1zMlnAVX+dBRz
2JgRe/Dazjo1G3/v1avoUaLXul0/XORR966J86RT3Gxd7QjV0DRc328YyS6NxGcI3JRijQM3d8Wo
8X9Ns3GeJk1HzoSDxZfWPfVJW8b89v6pItuDeeNFZ1aOthb5ZNJ2l/AFngVpTloZbB/9Y0Ui/cWd
MCkW79TxZMXH+u4rPW+Q1Lpy/3wyH10XT2rPVJ/qQmWhv0YnCdJa2Ikwy9YEaa4z6xvjYqqG5ALe
XVu5y9e/ruCYTEkzLhMJlpcz+rxDX6yF0Z6q7sL50iXsIkhJK2y1baM/nWezLNUgBwyhA53AFBnm
EfeslrbLkQS77K03V3b5imhqncVyvDUhtkXcn/CuG/9/swxiydRoCdk9TeshlqiJhCO+gksmfWyL
tzOaqTFJz7WtJTr95waMQ3pd+aET1/EScbh4R2Hy8Fgkb5BP4SG9Ddu5B+Cyw0p0OQ5YwOnpm/6b
7rqH+RKiocyG2tvvn+zs+OLbX16/Pn1zulPTgktbGocagplF1APs7nXtLHKX54/wTY9RVhmjtoxi
9bts/hpWVex/RBr2P0TB7u8iv4L+mrUWK/ttp+xbcu3vIjqM/tqoj/4eIiX7eztdd2Wnx1eMuau4
QgrD/GIQ2vuo8Rp7T2hzKb3F93Qrj4m8aFt7TmoaURMOMxYvrEXKUkuxQA7tRTttPr1dUCJGJm6d
w1S0OQ6plEHYEztx8GHyOhvlY6kiw2DNseSy5RSGXZLr0gnciZ55k/opc2JZOHRuxKKfl67GQZs2
1e8Dm9ELO1znCZ3Nb7U5ta79zJ4OQu2KJJy3xIEzBDlz8yia6dhQfw/E2d/b67lZlEzQPW+mYb3r
77F9Va+ADvr9rmVVh7sH1AL9tc/0ets9CXVxwbSIEGHf/44zTtEPBCniw8TBa/ClmlLUrOZRqeRD
bW2O5FCEKEWzA1DCQdKkyNLl6MpY+wfJfbJoI5vVbCn6qt63ZfVp/eWASioB0WRll3O5AvsnYlsy
QDKfv8wvkwf4bW+eezNdDQ4vJTgq3sk0ahXprTxUN1tAEqyrPangb8IpENmFj75azOq8720THyfm
YC8p0eQxuiw9s+6kAJvbcjf40D0QOY2EPnb6Hoi7cm2vG/KcyoZ6Ww3+qfa15WFVIOMEDjkdjyGk
zdjaDD3I9noChUZv04QmC/HcdYgCaAmiFEVamiCT5qLVNgE0dHPYPTtyTcmA+jogNKEzf8zGwvmF
AeAj7V3JMRLopbRAI/m3fwvuvUi6xqhKd/RlTgGspClU2lnz2Au/Awd0n0NSdvf9mdnXa8bc6jtz
VPBqx2uwImqFOwaSYVczLgACdJ7OlvnoyhiDEU/BjLNo+0C2knsLJuRlfHg8gvcsfFizOyZPohHu
EQBlTrkcYKY6chhg2y3xCySYtV0PMmjneVMXJUFL/PGklJAFmfMQTIfMQ4Hy0EPmHkeE0sehd21H
o52UUuCTMn5GIpXlys1Pv89N9vs70Sppewf6cXIRfPykplaioGoytRz38SbsKEi3Op9/ycR5fp3d
uaxMk2ARysA9tXx3Oh0T31xkyxt7yhMCZ3yOtd9jnOxvbzuc7CMPEB/ezsCJFfgwYgmyWkhmb5tk
E7MhttUCr4nOnOahBn9h8FasjxjTwT6j98An/wN2pfQ49t1eEx57cHjgeSBIxrsoowFLuCdHMnNg
It3lL22XPlBoNRmbUhPEK4TT54F62GWWftjzkHbIcbe9w14l16qazoS6yuNfuOfmpFOT7cTroeVl
PfEbnxAlOOnYi63kwRLnYZ/n57BvGMXkXxKKF/MLguK9iwakFQjGwKjTJYrlPU0XTfsGgt7ubYDi
cvwuittDG0fJQ0ui+myQXyv55z9tzB6bfFmyp4HdrgtANelRT41ANde/IvDUSG6hzFB0vDjazjqU
eVG2EIE1yFaxx3udCTDlW51iko+yZkvq5jt0fmkndx5Gqb0OH2X8/qL5hc/S2Aqu3eEa8Gv2sGh2
0JfXXO7exB0cPoeXjxyUEh4FKBncJ4L5hcC6i1rSrVtGe5FD6l8PGEPTPeMdVmJg0Rrv+T75KVBM
SwjeMpGw5goRVZPGKBsQtnbs0s1nDArd41Mhemf8Ys6Btm5HOTxgefnwwMjBmEUm1vXB8k7Ut6Si
Rcw8Hrzd5aBz+lDWgXC5Z6A/gm0j9Vjy0eSGVXHVvMcu4YWay1kocXq7OdZsEK1oiShnf1d8Kydq
tfP3YGDnY1How2ko+LEt13a98cje/YrEF0+Y2GbDJT7218TKeGekL+Yd2ufTjpVPqEmtZtB087TN
XBkfVvvp7R+wGEUfbuff55wm+tjueddY8tiXrJpvuFfUzkRRoz+i5AjX0CnaUtpw1qskCID3hG1b
uyRli1fbc5bbXTxf2iQ6yZws83LiWbB6B90uxyF0vcikA0mKOWDrlsH3t0nj+/MXSNjPilFz0ZEz
ZL9Nmp7U/EPSSL6HbT4ZTdKiOP7tG6jwv30jvR5Lna118qdnsWjr2aFTqQ+RzioKwW/fvKANMQx9
FMgw3YTfu2aoA9AqrW6hLS408P13AJmGNkBVY7lCI7W0ciDJMvRhJU04P5JGpT2uRcwcphw3IeED
XU1VBm4e0wR+QB1ngiGZrko+uTUYzk0d+B6M2zJlOz1P3vFjR1GzhxCYjY02wII3FIV2nQQaaGZ8
uFw5N2moIN8rSamhiSYJTg2nEla3HKuEZUVOjqLh4jQe2e0I2RlJ8Qlq3NFjIgRUHI/ZockyhS5n
gkETng/Sg3BytH7FFHSZclWgwciUFhx2Uc8CH9shAwLPutZZ5BOvbBrC9+XYLoLZNClW541Kh8ue
R3NE0WZxEdBKhuX4RcMC0dvmSENJO7LXDvnajl2p33KpQTf1GA5ROPyqL6jBqxcNW6CFhfUDV15g
nI+PzEmtpjaRcDAv5qpWVExReA1SezrLJkZKFkbGti1IWmxg5TxioiSVqkzspQraovHAqJEGRrrA
cNgo1jPRwEBibEShJcjnfaJLH+zuB0cp0jQxUzNEQzs+Z0pg4tDuD5pkEvswtNZKBBJPcTvYGET7
YPMVM2ohgM733y0MEzIAii56sNfztRgsMc8oNPCWcmVubA0CZ4kNK03qBDxiGfIyN0EKSAPQ1kzm
5pFCkRd2os/v+OHLZb7gedGTXuQqO1ysQmXcMDb8Vopt2yQ8remDHEHE31IPtzPPvgHy4vo+avOa
Te78ad5DtRD9MLyGgNE69c8WHdsmCTyrAiUySajpEAyYLazUcV7gaeULdkXuyQra86xk9GNfrh14
K5J2RV6AJthtUF/ZjOfBLdZxfuNWq5ggDpxhnjkEiadcOZW4zGg+oZ3W8JzpDTGUcC8ullvADF1n
eU23vZAnsIUDHz1vRHBg4SMo5wMk6nn36/S1mlBzgniZQD7bopdGKKH42zdge7mwQAsrM8vc8UqP
KePOhO/4ixSzlM9u8uw24qlhqOO3IWdmLHFfSg/VwEhqOESj+kwr6OXrIHYaBi8BtGeGEtITK+4M
J0xHBlaHF7QSIcZIUK+pZWzFWEx0mYOBJObBxgK9IH14O8vkrxFu6iI+WaRblSXcHHdMQ/LLmwf1
EHrAgdKIAWn/fDWPYOWL53d0datnp/IHWVoDfzXxwGUMrIDQG9BgErMRGhmzkawWMq7eYY+EM4by
f9eY3JC+98yUJLj3/uQYEdRtRrljR7l+AgcxZaqr1pB0bsQcK+F6UoVb8Hu8uPd6VdXKk2W4wdFw
+4wXmhVsVlMgw6437ndEqjHMOd8a0Yi1Z28tV4GGz5agVlGlIowHUhB7tvDh89fvF0F7aIjaO4l5
rK/ARDzLmInBuKwO0PYVt8oOGFQtcMUmO3aqHFthrPHSlV2ybmvk2ixy7LjdHrHSF1yXXJRABmWw
fqvkXMdOgLyFN9d7soVIKoOxlNWIFNxnnOlp+5GrakczTN5L9syXJsmT88RrUjvV+JUWd7NR4pvA
PmgOa+6SVy/mo1VxUoY2MNl0TpG2RM+2jsR4sICo5nFa+vVME/SElBXY9DbNUUNz/Ou8KJuNX99/
/IS8tO+KUTajhTr/DhyiyJGQZSvFfmcsIvcuP2qQWDgHBlwQxiBZdGxOmauN6zQVDnR1iopupuwH
cXSJshJ4sGMC8L39FIMk1brAYY4N3s11elSHMamloHa/DQcWBM6kskDwfLwyJO+bk/5FTm2EgGhR
9zfzJVJtfCgxKYBUk9vek5LY4iraWlRU3+QZNG9Z2xFe1rlHeuVsBSsytfYG15qNYY0QYR5njn2W
DGvZ+9mA1lJzaPjzWavRCgzHflaiHk9YhH6zMnkOW89zLM7n5fz5BlMuEuXU8YxXsJ4DQrancxkD
JXKUmrhD/DLpEl3TJsMN4Ou3x+DnkovGVrw8QBbsT8d8LAQon39+TxyXvtMV/cZXXxxrNJXcMj+t
8gpca8PSGY5vH2XNcs7H0AfXZFi9FhuQAwWdH7Nsuy9isVaDYwN97iWgVRUO9dKIy0bSaAY1MS3U
pOjmfSOeml3rScStlfk4+bVg8v6DPN8NVDbUvk19YQULZ8QXgSuQA6jqlbbAAWh1mprguFClwm5i
wTL7hzmbzkTQyKNgE+N9U7VLNSgO3vGVQ+h0KNrGeTAAgE9sQBU7tgoYE9Fclwru8m4mCW+crobi
vIJ9HKMkh/v4c7fH+m1/z1dqqmzpSTMyYKnbbpWa/gljAn3lmtvQJaX4WGeNktXvs6Wv348MzWL8
fOdbkXzp6vxFoK9IaMDdV8o6Fgb2IBz0xXNpM2vFXra/E6SL/hCIPrfpciZiD3YvsKJqFp4mlj8x
/BJpxfOFrkQST8G8OfYpTa7yS6DZtDuiYQpJmxYKoQrGPE28dW7U2Tm4Uhg++o4OmouOVACNRplP
F1tIcKaRfkKzUku02FxO1Ng7K3WhB0HdZ60T7M5+eCKWKjVG2SV5qIVGi7py0T4WnLxIgoOX9rtp
5JKybPoJ62t75X5rLA1+cIh5Pxqp47N6AIIpXVw7eZwcSh/9yqI58cNz1yQft59cchwFNdayBpnI
qrpvkOJHL7rT1KRcFNcUZNk7rmT+ky2RHS4CmhvbiARLBDNQBcOfE1sBdw1NtjcSsqFRjlRs2PTy
LmeTd7s2f5pjyCCpu0q4Yl/NODncVdGM81215rzCzjmWUw6wzK0iMlBfkgne0MTnolhlYQ1+k2Fp
Wf5hl+vWyEcQtiNRNOo3tFJNOlIviZHgGp7gXK+9SMkyqRbOR5DyrLWripIKbkYAT8ttdjQvi+zt
rGxmE3YkFjj4A5XRWu1ksR0rHt6hns3FdivWYSJRMBTu6Xkb40R98x+IKDUAnN8xBK2nPHNkEuoP
djmj/kDi9ZgcDBeqMdGaYFdnVQVaB8b06juFrFJo1U+fY2yKLqT3WX9bjl9R46wQHztx05eg5f58
8XO6vNZALnBOO/ljkvXhA+v8xypb3n3MJhkOEiM+HqkGZ35WZzupuyeF0VqohnJKfMnzKC09Wlt2
mA1j/kiLAv00G17TpEWsf0R7OAq1G5TKYRumeysdj5uyQxDn914Fww07syqLRQS9e3pDX9BQNiMC
bGCWOUmEhFo3qCzQQHjqj5OsQ8+R/iZnX9EskgwTX/Mxi9fOGv5onnFLULnZB8/OWvwapWVnnJVp
Pqlq53gFBRpOynKZn/OxYdZy2pDADGMhDRT4kH7uSQ0fJK6fth7SZBcJd2OWiYGeFwtCoywqJBCR
H64ZMeil8YTpa5hEguDhjPv/xCl0mU9W4Y1OdnFBhExkPL/l8JkGszzLZojt0HCjd2hUr+l3s4Fo
wu+I7+U4cOxjuVSWY1DVAVpaaixh6GsaoufeTtNLrffEjpNtEC7wS3t2M1tyvA1xE8h1xL04hv35
d4Zn84eS+SOkiWy7NZQp5hxvlgO6iqjBzd3XUnFlTp/JpPqtZx2cjknAv9aS/H5wErQj8UNQDy+R
YUI4fzXJ6fEPNI/22VqGlhFIePLfE5RQ/NKBqvItf5MknOS7ZFsX+hPwOV/8fx2XbanB7W3ReNhf
14xWWtZeq2IVSeJlwJbVY2nwWyzPx6eCWHKP+DDkgHwcNZmPY0PM92zqweEcx0mvlm3xtlehDLZz
HW0wdbp+Fl8pbjhpw9qi2onr72mLloTHgM7WktlGlv342L9mfzf8ds0OvmZftlza7Mr6oXbALfon
uuFIjGUwX7L3Pj0fRMdVb8X/lF/bw384xVDbmWs7y4yGhvN8OFbLHMCiDWuhJBC0tiXF70lg43PO
BlGhFJfJaAufFDYXLMi1yINEInaSFW0vcwkZO3lxLWklRTsoWe8EehPaisIpuDVCqXwpol+2WZ2W
6I8ZZ6oU4bmnLl2LkTO/qIR66BncrvyUsyUU3IOkX5kaMn54SdNG7lrjmKiQYhJrSfyJ9cLPJPNT
OpTkEwlm0XJACaqemCngh+YXPD5WY1HvylWd7CSgvihHNZwPLlbghpsv9RgIW2RZyiAo2lwG3Gzs
WWIgcneSl1ztRtvaWPPm6dVuOtrcO1FxJZuO+Zme2UVKpx7Fnd2yDkyz5UpvysSKDKNFu40ugihP
fFTKe6XFtR7lOWC20OYF8iG7MT+JYM1XmB1/TBeFSQVJFqSBmptce1S/T+bza31IRTzw0pP3r3Bo
Ic5YGjaY8OH2gW2Wv8jRmPbIZS7830boAaOtaJzFDX3muswsgXJrJKS/ra6uBh9jMcXdE7vKGrLK
cPGDXW0Nf7XRHXM6U6Nt5g5X1STRiAb29/cfXn8MYKlb6R4w3pL3oPHWfgxOgBYPoICu+LSEB6eD
7u3LvKu3Np2POhline0mgCvWldWQ35jW4IGxrymapg9EvTWR1vwcIn/1pMsOD6lmT4ALhovvzjts
AUHl3XmHnfOyF5gODrkClx6/gKwqORrD8jFzarP1cbpCuKIeN6fOnFR0OBmwZU0haHlbOth2EdB2
FJ/1cRPuGF63QB5KmbDDbjfCgjyHoa1K7+lteZqtyBYl9NKnfHzXvPFC6VV/uEHRT+DdhKfetJA2
DuNK87vffiu+/e6S1khCmy89P21iNw1UeWr61Xxx1xROHeeByNUojvMyiuEcMVVjtxsklx3Z9cDe
8Is3kAerH5kVXjPrY4xthDONj6kLfJIcjJO1NDXBU85dJpmxR3n6tj+yHxn8poc1JdYL9q3ZySTc
2XnRLWuAvo9MhDymZFdmZs+LhrettbyGjy1OozZb3jTzqjuUUw28bJjnl889u6mjZPg0r7O75wNb
fVbsOTdsGveyYQj7JoLR221cAkNA8JoNE7lHCeqfcAOnTqMwY+DYT5d8ri1dZwcjaRj/tEqF9Y1y
KffuET6/d1Muwi1fFUdpSEQioJB+PzRvDNPyTKkgsOPJoyauh9QeveBSXghIvCwhoVs9u0h5wwlt
mLFx07iZ7zn/gps0PhkRLzwpIJc8e9D4gwh4Vqrwqg3q6Wh8V4Q9I0sa6SgstY1aj8htX+m5r6mW
rK6ZJ9qY5xNklEQmI2722F8BtDdz1uz//fH9L52CWUd+YVe9p7SAZj9PSQgHYu2PDr+NPJz8MmB5
fNdTh0QKJn7woKmiBZtsariItyjvBUcDOK/VcdolpuEWPPVEZP5KziKu4R0+mapaqWcDbPUs6SgU
1beJzmdBtIdZ28EyiPQlWzu7GM7OZCUxWUfhFQqHkuG9hWt25L0tFMJvJw+VQAu/oWfaEKNZ6qse
S5shJ0zc5DBzpmkb4E9b3hyY49YEhoGZqocohD+Y4Jp9Y5ydlM2YR8yE8hxtdhy0YfszRgy8Sxf5
TLxp2MHMbueRL7CEZbaOz6fC5at0WAMIImXYf3QMerOLBRTrh3sYVt0Tvu88n5gNHZGkmNkBVEhE
OwrowhgNrj0GSmDAjIDMuG8TugAPwnDX/9ELWeAzAXt4eQaisj+seDngxqwUOODmRLQccGsGCbrS
vJiVy5ZHhLblTnUoD1E8yb1tUj4NbjX0qkaICKUM6gVLXonQ8GHe7/gBORronERmOZjDuUdY2hkk
9070gAlokj3EmZ7B7jdDcfUJSqFXGeuv2tFTGOtYLvBO32a2LcQkdoOIFYb0KJcVjU/lT4aATB/S
Kq8Ykb70sPbguls8loQksFUSxSDbc3F7t8+J/cILJuN6zXwGAK7b3MvmuKMHwWOr7ndbfpt/zbIF
t8kXSK/nOIz4DQ4qoU1umY7KbFms65N4JXou5+9wCO0rnKrQqqxAbzw8DO5w/dsmRoQPmyCNi4gK
b3AbuEz6tDBLv/qtdyadB6sBExvn+g6PQ3A3PJjMwvniKaTx6dzG8xVHB9Vhka/7M4cLOg+7MnOm
dTtzYh+zMxe+sX7mFORgi6tustEOCgCwcAHAW2NOms1rVLqBFBnO5GQQSStKrzmoie4HIZA8+nvf
4c8yKpuSrGwiSkYXK62axHzVCt/3N0hPLkEElS+UhGqHCBNXxLpaKlo58SJUbmT3d0nRGslpMXMa
qbnWjIgEIY4nO8cRdr7JLC8lu41/cH6U/DY7La5rxigS9lczPoy90XKBygMbA71abES3E2B8Unh/
/o9sVHaIbRfO0N1iLX8GWl94G+4hb7hyYK/PlF/nU8lhHudTd+Shb97WgAk4ULSEvenIBh2Sooyh
W4vMkBo78wRYNjMI2/YkKd9uwW+sM1vI62stF/5+qRCG26Kv4WtbN6wD3QTVFRqeLolADfrYqZgM
mAEKxvzd1ipkYc/jkFwdsmkfJ23cr+5gopgbRlRgDRYn0M1nJIxO3WE7oBlrfZH6HXwyhAQWo8hT
sXGP/qx59jVb9Cu+A59B5OMIVPxnZvYsCQz92cahXMex7t/WUG/b0FfYP2p34kG8EX+9RQRTsMYC
YuhgRwTVnT2XRHeOVDkvAZeP6bmX/DohcTbe18kceG46lGFUt6ZLhsUAMT0K/PSE1Jfo+PxOCxEQ
Os0FBKkbIcnOBA6orQfBR/Oc60xw4/VwhXsPPa52RJFrfXlAXo9YHacBjccoZKQP8dbnFTCRZwzx
0494kVmUHIXcj2CRHiGsKGnUQmYfREfLjEHjG/rSUdwqgyitCmXZVi9tQwPZw3lQ9p2aYW33DrfX
Dr31VIoNd3h06NnyhH/8U2NHAjOfvRVttrUDilg4a0pmRnzOJb1XBljH31q1e1grVnZQlWXNchGp
GrReT5/1U+6G5YLBk6bc4Vl4hOZwv9Wo1HdB8RxdL1WBxt8ONgDMLH+D2lbzTgSGUah5hW+CYtOy
/lNQYFIEGRHxiVJaR3XxLKkvEsgHNh8DpbYMUkSAwMcfaacVGi81UZonuvtUW5khVWeeZic5rBlY
8Ioez75DuJFO1ELlo5ZveIStD9IgnflZ8+wAJTXc41w79kRy2JzIrIb/anIAIGrE+Sq+nwQZ3fTB
adCRkLgs73zj4jNPICC5OzKABqZ6tuTL8Dc95ln0jSjvCyNeWSLdBlRE0soNqTVnwH4/YPtuvjCW
++DIlLYNRtDSdKU5JFG1H3b9bhkfAI4TUmP1tEZiIpb3UzmdQOPI2wlJsYFxg432uTU5sYzNQYLH
nssPhFG2fWM9hGFh4a6KIz3uSYJ0E1ckqtAXE1gcPK5K9tI6xIRFGYhnvgAe8DqEYhwnz0LHwA8s
7YPGwusvOFQovAbaI8TxFkydmstacy6kwyhnxNS8R8H/RCvhV3LPpZr/b98kfCQERjzZMtnLqJzv
6osErmvBrpcg7DIRHs2iNnBJZnCMGSLGIA9f8qcJMGTB0FR7V9LlpebvpZq654MhmdPZFwKPRCd9
kOlG2nWJ1ZNszPnZtaNnKrip4sTm1Zu8YJ4QBZqHxkRXrQGh+c4+wnhG7ATcrJkAdMIU5WdmJ/Gc
LyURfO88s2Ua4qx3SFvAA3JExvlNMDF8QoE5qoBGuZzzIJkxBEjzRl5PGYZsm5HmwbzcWocardp6
CxggdxMARysg0cMTQMh8qokhMz6Q9Xz+xaePySSmj1qSk9pJeqKrzo/g9UW9Vu7h//vvGMwYel6r
4T46r9hqvIVbO9anjJEe82ZCxslsUhX9VrQw/NSSZuSetK+IM0cS9hUrHukqakyfakZo1SHlKOzW
ig3hEZ1P5RmTDNQdM4Qx7V6Eqdf04ZU+4OS8OEnKciu3Xe9KWEO/V2uj8HYlTcaFoT8sq7jRgo4T
RIne6/2IbY3RpBbNcw9fu1dJakrFUlCVab/41RJrJFPZQsOlWpVdne1PXOMQLtD9M4GDoFwjnwz5
gbPNQszw64wVbROwdNnRYESNULrk82kLG5t0KRE9YVQSNWJ/+VFJlx3zPXlwype1wCHAGSuthqld
Oj1ecjz9l0WF0oRpjXAgAUl/MnnEu7nPm/XgDcNQndho5oDL2PDBaF61GzXN6j18r99bq+V0lO9z
2k+ZTX3+/O2/pK6N2R03VbQhyP4LC9p8fcEXYTp+sZdAChEZ1atcQ9PzzIyz8fTiLkJnjxav+S+C
369SI3HHKFDzp4fytBo139aRfYX6Kk+U8RPhfsqHoBnyfPFjvEz9nSvYb5EW44nGv9BK36qU9qEb
YAERkeIcqNEccZQlbs0vqP9kmn4RqcMsAsu1q27HSJjlndnb6S9tGU0j1qh/8QczF/FBbx5b2CiZ
ViSaNfhM5IQ6i9afPRffU7AJJb4Wm2jgKdgsFiS6s3yCcqFgIxtRXPUPPopiMVz6KGa7138Niitq
Ge8JjYiNI767Wvux6W8jvmhlCgm8+JEjebSMkf+0qUMQmUWCR5x1JC+tbQQx6Lr2vSmv5C83ZeNj
QLhwApqXa3iVC7VaPyEbXNxLtt/CG7Kp967B5Z4z0ZRmiOQ+v+KV4iHw3JpIuajW1R/Sa3FaWPIl
5LeSOLKe4z5BXQy56yc4OGGMDTisXyzXeUCZ5aJkR/3+oUUsNvBWHCUYWgAe98/5xhzfPWdQW+k0
LMKVLZeJOabOYxg/ni6XWzUyCs47WhWekjxcaKyvrNe2eqjPjFD8ck4vpjPrNGy0vIpXMXIUWqcx
yMHpWghfafzSRCFZis2FZNOlMSDmMGgLtXISSqP+vatUXrxKb8yb9BqHR/Aq9QE8ma1NDbqIbG6j
dIYaAucZ6slO87KUkphIUQmTU2TBR+kpuuRs0QAa/r74dt1hCvhxINcOvcK8UG5EPXGRS6h1BaUh
cL2zXmXzzNimeBwbMNscwseOQY3ZtfAcSDC7OzQEP3bkWj+oRb0Ba+5sbsnOCuJfXZnu6ZxLMNv6
tKqmZeNOQEJCRKuJJ43dbKnbWKmVh2IjPGhbipbWyFtatAnnlsRHSrG4dFSvVyO6KHlBjTLvpQcx
Bgk6IonOhMiA/0ozfum31eSFm+pDQePhflA274XuJcLGCWKDBNVItCdL2LJt2IK1RMrrc3+0dqvD
q2+6Au1Uy4UvptMtukN4/ZUkDj4Mz4UgNcx7tdBwDXSCe2nTkupKCdpqeNs9LpWBj3WFBAmW6ark
gjpyIKAue1fE9Ue54BUNaSdhBtAn2mU4H05TbeQ3Un7eIcemnbzyq2XYaiM+fzAR+VITBFzBhuWf
T9LZtV/zuPACtrTsUrZIl2kpwYwkfE1T6Kv057VWMsH30y+jzDANqWZi6sskmuuhzESa7oT8Syvw
1JXfMQEP5khFt11ztZUgj04S7rzqJpqMEOAeXfDhOerOYMHDTLc/uSjvjw9bs/aKdjN4mDyi5zqQ
N04EZ3r2XKF0j/tiZzm+MGThAxqziUcckbrrFVIN2ErUlmTsBM6tQiONhXUHTAm9KJq96uUbpsG3
KNy482Fecc2aoA0SpV6a21rTRs4/kfo1Qf0fIPlAcH3oc2jGrGyLP1TWlazx90g/kGo6bYMDEwfs
v+8EV0/ck+H4mKrdpasFiwjSHVn5pkSzDevRuKFKVJcHPc6UzWfgCobxic3sLtMKRFxwA9e93FSC
h+ZhpNWTanxU98hy5CNICFBeToKWRjtZLcBbcD0b5yVfQg69PruU1ElcLbJMgktqoGjYAJrvnie2
BmRXIKY98rpRGHiVZCB2YGSmrDnX98q4MCiO07Slc9h6mbH18N62zBbSlHc/UD0D1kDtGkwD35kv
5asJ4XYKHjemMRtGjhATX2nTPlEEi9+vqQCsW/O6Jh/zirv4j6eFR2DbbhvznUS7R/a/uRMdaAxb
oLEEawBfVIDwaY+2S9oqmkMAXYH3yh6KAvI9ZCre7UWFlFhMIBb3wcwzurgKSn/GnM56voZXHZk3
NmSbH6ILd9LS5xKeM2uJAiTZp3ya8VOtVqy6yeuY9roW9JYXDeo/cVXPY6/Ws1iDIFSExMdeXJcs
RIYQtFmjPyRvS7sYdPfzDnXUpxBhYQwTJiSA415NGr4JLKQuitEyAwtHknpeoKpSVmGhezKX/aAK
s53PNSqrak5lem4MpwXXSbB+Us7ePDbLp8YVyzEnV8avVqPaYnpu1tZSt2RDYxfyyc84RVRpTFXV
IzvMvuzK/W2/AHFt7ropuKYToblrG/LZzUkCJqt94LLa21rkClKRhLzxAa03clpbFMXwEkUMWfUt
WVa7CN1Eo7sgZMB3PtSnb2zIFNrkKeBdcnKdyFHc17DPXe1aqeVCvXWeQH3B3hKplfZqRbxk2lAB
G9aUGql2xA95G2gbIiLnf5Mah00ossTIpAIKX70uub613/ylqP0AmWYwHdPH8kXlpIIXEIL18AHb
HNC7UXmqtBKrUmivxlYBCL4z0KAist/rhaaKhx0vI0+vd4JBMBqSDD04loEfFdCsH1a4nLgqk0dQ
1qE7quSZhqfLzKl7iGacuJCNTb30oHHQLmnoQod/ze4McTPRkETBGKB9rC17kaws1dQr6XU+xLM4
46QOxMt04dm13jrpTFwJJKWvBfrS5lAMZ2dtz1dZCS9DsmIYYFabm1QpbR/YyW40tsB54wJXXF3E
SOMNLAprYyQ8d+JM3Ilr2KlhqHEsdxxJW4OqOnIPciYq93kJ0CcWb2wcI/4sAv1+z5mAwiy0ela3
xkz0da5wGIzGHdFfHvN/82agHm/+oc5tNG7aCH3dYmUE+YytDGCHvS+7077VGQdyllIkTLP8CYFf
RP8gNMEXXMbGi+8FgTn5Jb5bb9QImqvVD03Gkjd9MOfRx0FvvWFjQiK5lpH9YwWKTOFYrVJkUtY+
/nySvOYqfEVoJVAbiTl2Uyxz52pQGFgPQE1xgsLVIhhdzXM+VwCC+lZdKSO/kpFfyKhdW5a0plKR
CdyLTBwbihfxdxK39fAeEqy9RP6gYhGf5PBoxSGWbaom3dp6Qz6Qn6qTY3V8rY/888mvTrEvTF/G
RGQIwJ/akSmEe+7ZA6aZs6K+In4wmV+ussj+wlZb+tjzabAi4HCc22MemfOSVM9ytrVY5gTAXSjB
puOx73nhopL+snpx7Jaa+g23u91ua61X79vkZDw29PiH3PUEawij6PaeJ5MumHNE18Lxll8S01w9
GGmAoXQ5Qpgkd5RcLbMLP6iRRIn5befk17efX558PMXbtYddKPlsydFgXybFFxfJoebCFxW74fff
pf68y9ZxuL1u3rHLTlj19aNT4qBE3w1t2a7UKOC/YaRAM5zzH9bUlSE9IHRyxdFhQTMmSmyNYZ6d
kfV2IWvViAsG0SqE3XPOfEB8MSA2Wveyepw+eCjL59BbPgTvR9pvGNwgGu5qJ4Bih2A4DVQqs6Bt
ms6TnaAhHaOqlpmoKCIXVI3bvjfbXiMS/4n4G1P3xytSnEP9kHSKnWA2zYuxC7zWNh+oicJOWVmM
YqxYOWejs6+dm43c+ii8FdhJfmIV0Tiq/aIqcG2ADxbMCGOH0Tqyl8Dn6pOY2eySJ3ZNAbGGflrB
KKTsqlQkZqmSv7c29WeqiblCZ9yNCZ4L9YHqEEMncP3QRZUlrbpQbRZflUhKifGLxnMRLlNPJe8Y
pfwxf7Su0MZjK0XPoBEaFNzoJVloXtNOwIITbbvX6/ZtYW2ANdbkfUkvsTRmTzHxzBCNIiHiKdLL
zBqMIEuaQzpSlLqf8TEXtgCiK5I/IgGqrK3Wc7HMiqtfUZ6omYd1leol82rgqicqw39/7OpwXmbl
qVDmy7u342YjcOUHpY5RIZj+dBAp9Go+K/mg2+Tr3fna5rARBG0RoQZhR3Wp0qiFeh2ld3LM57rR
xKXcn0GDj4qtuLNe4YvVOK2BDodGPEH9ZK+StR9bxEuYAyOPxBVPT0tAyboXbAI2kZlS277TxkhG
LR+ZnI8ceuDNC95p8Zsd4iDZ8qdPP78TknDbitcp7+P0L46oRlBgs/YwI+ftP50Vq2X2GkTf/MNH
FHkBAu6gIu9ibdUtYzIQl3zliCJ9yr+rhxpd+oWN/PqDcvVksegg+IIQQTMFi2MJflyr8X9yWRGm
9AcSviUiau5KgmhmhT9OPl4rykuT0mFmXsSv2evvWtaDavyyKV3iiGWjUVQ0iLY7dV50GE0FU/oW
TUXl+rQUZUTCRuyxEUfMwerqSkpKTpUnkXjTfAqRhOVsYHXq+XO1JiNWqptsDHdHNjlj0Eux5eOJ
tGhLs/GLxR8HQrQqWbUzvyqRITAT4x6/boLcG87l9lgK/qWk4A/PouRWnkEh5aFPJFtJzzdpcGId
P/tv/+b6YtFzTXjXsyYe93KjTem8B6hFUVfE4oJQncoCQsLemgWkiSxSeTSq/VU6mt5nP01v3/PT
2MqJx8mywyfY+XUbjoJyiK5MKPyCXhlRz9p0rFyiurwViG1heNtdzyfMC4QN6pBGcfCIk3OPpfQD
viMpH5tz01UsUIpzjbN7Z3t7v67xy3k5b5i6CQA1XV761Q+tdYsTIomJ+TIZX+vpNYVMel8LlGXH
DgRgxD9YxZ/DIDlzifNruRzoAD+8chHaWV2RPNsEEWh9XW9T1DtM96LO5Ci/hh5awA22NN6DT/tr
VipW1B8jEw4VyUaNSD5wpTrwRE0d2soQud20MDPHXsTjxwdoMyDPhlV/G6AIx4xmW9y4HbGrH/DE
MafjcTzcVCeGGfRR/fxJRf5NQoYVy9LSgDtnZQSl8y24+CGeyT8CvMhJMfzr9xH/gABCrB7dcwnz
XigXzEKhYN3W/4TNH20TbeNzgxxgyy90klfCmSDUXKMQFs7Km3c8oW97WznGYR3HqEPItMI42vbw
A4OOKRftY3zw8Qd8/1v/fu/MP0DBluwMhb7AdVWhHAXn/IlrQU/lrFkLLCtJGiQA03VRPb7TV/lg
+H+sU5cmJT24s0IDiNacE+pcX9PzFg2zwoc20reZ3p0uT+9Or256pby63W+CbUyYeIU5FTcCB3a/
v7B+xO80nrA3cV0f+rCBoXEpwtoiRV8q59fWlSn6Up/596VSpmizlLStlYq2uXjzF/6yoXpzpX5z
UO2UgPj7MofC9evf+NxfPtTXmOw8F48AzEKFFrdGsmfjler1Xlyrt24lKGd7t1s3sRr+YWf2UQmm
Wir9KTLN43O+J8LO3oHVIsS1Ike6jeXsZ2OpAM8csA+Cc3fFooHAsyMXuKkvmjMmCn6ca+e5eue9
bYS34GPb8yuCyNwpZoEVAsgbFx0RCqJarhAl+Bayyetd0LFeaIpLe20+i+rDujsD13yrWoraSmrR
9jO0rFSKRCu7PeNyGMKJ6aoV36XUaquGU4RDj2pQalUMr8JVWIMQ0HEalOlRV8m2VwXJK7LR9Kts
2BXVFhEhSi6X1PJge6DJ0/RyLlbiqhHrq1y3pea9Z5UXn4UvevpFLxRMTNmf+tJ51YR9W+JFUuMD
ZmHRIAV7Kh5nv2JejAyTYx80aGcnrFBwbGsCWS/F8MwWD6nW3WF2/lGsVmvptU5sdk+rIAk76LBa
acDSt1fP/xuu5v8b/n1jXD9nTnMLdvrHZV4zhPWS7HpDx3Z/T3hF3zHSTEC2lc4cJoh09aYtWOat
l2Bh5MEZj2tb/CHx7w385o1EWbf+87iOct3ifmQ0LROKyoxRKZZX+KUpn2qve5taZP49eooYsi8M
ed/tA2LKqDkf0p4EqRYm2pfkdGFTU6mmINCfOb9RBLXa4xuDW///6Y1fd1abZuM/6fBG/gFZVI5i
1PgAPQir8WdOcPQOdak/plHBjI9zW39GoxLF/wuPaDS4+ANHM/b/9xzN6GZPz4b51x8pWDed4YmC
loeJxH3Q9eqSPvFYxgqPeuKRjKbrA2GfEmj1x09n/D8Cnf/lBzQKCGvOZzQo+DPnMj7lCMb4MKmA
BPzzB9cYPgIZ6F9wYmI89evZ5GZwv2I7/deeluiEhoY6gdgyV1ytSo5NPCWNFx77OQdSTKClzukW
wt1y48TegMDr7A6RRxsWT4bMY9lopK+GrcvMOvFXbmo1q+1rLcP/zxqDzcLF8YC/EmmQ0g00GRJt
s+D3KKHyzGw6QPZfhE+ZsafwrY6WLWi0atZE1UrLdCgBRXp25lU6G0+IjRDt3mpIKcdbwChxhIeJ
ncH4qvGlbD0zWW1PB1O6JOm2AqdjmU9riausDPMx7S2QEtJlluKX5w4OqMwztrM9n20t66314JmI
chCug4db8k7kVXAEI5vczj6bKunDjyYWYiW5AzylbX+BJPyfywVNQuFdYUbh/b4gSSX6SYqrd2VS
lGdtE124y6HZ+Djk+gBnD/8LUEsDBBQAAAAIAAAAIQBoKJ/UMV4AAGCxAQAQAAAAcm91dGVyL2Js
b2NrLnR4dO19aXPbaJrYd/0KjJ0USS9FuzudVJYOd6J2293Otm2V5T4SRSVDJCRiRQIcALTMVfTf
85zvBYCHDrd7drpqxhSOF+/x3Ofp7z8f/R6Nok68WMzScVylefb0UzYZ5Isk+zyfnefFPK7K/fz8
PB0nk3y8nCdZNSgXRRJPymmSVPPZgP7t7O1NkvPo9Cwuk39NVq+KfN69TFZHVdEb7kXw36NHjw4i
vBst8qI6z2dpHsEDUVkVaXYRxWUUR4d6B0boR3kRvc2zJIJJRBn8eB7FNNI8pnklkwjvpvjid99+
G2XxHAeqpvBGmswm/WiWXiZR8ikpVlEOlwv4/CRKs8WyGsBsaKz0HIauIp4pTxT/K5JqWWT0ebpW
FavaTXeyg3NYL4ygS6Znk8/jZFFF3V/j2TJ5WRR50Y8+rBb8s4crhiecYeMUNgceTid0DvRYtwMj
dvpR5y3MMva3bhhd/6W4GXQGfEr67d7e3t7j6PtlOpvQZuA7UVxF6Zx+FTFtRTWNM9g/2a6ihBvJ
35ZJWfWjMo/ibBVdxcV8f7mI0gqGm+RJGXXx4bP4EnY+nsSLCoYBQNg/w0+VNFJZwdTH0VWSXkyr
qIrPZvAafCWOJnEyzzMYqZoi7PSiHM5lFi/KqEyKTwBc+C5ML83KCu5H+bkztSU8M4he5HCvWI5x
c+DYYazxNIkXBB98yjqrfXo3yS7SLOnABK4y+EoFa4FvrMpoFv/7Cp6JM1gHjDJPy3GenacXy8JZ
2XwJH0bYKKt8QeMV+RJvzPJ4AoBG+3QepzN4q4TNSmG9OYxWLovzGJYjeyu7ivsNwyUJbFRaDfYM
QF0k1dE4yeIizRGgur09AZuX9A8sdRht899jmOvf4mH0/c8vnz37BlZXJHjcsKAFzFlmQV9cxGW5
J9j6KYA2BMgQBAV/H8MhlrMENrqPgFjkEzgJxFE6piSJxnExicr4E3zyDNA6nyeInskMgLpMM9gR
eFBGSiZpBW8ikYGn00pRMS3x+GN4GGfSj7r8wfewlMFRFc8Ui+TrpXcR5vACpuBc6/VqSPu/jt69
fZ+UCwClpIvQuixPx/kkGX337J/7EYBBBfRtdN1J8P3OEDF0ME/KMr6AZXeIrshV+n3DmL5x8G+/
7e+1ndxtPirHB+D5Kl9mk24pIPR6IkvevNzvnOWauZkpdBQoo+sbJLFZDnCfXQAoxZ8A5hGxAcb0
s0CCiwRIEoAtImOnvtbOt99F03xZlIPoiPA8BmS4QgJu6ZezCLvEOItnK6AppQOgB961LVf8X599
23TAQFFwUOeLAKuz5SR5lSQlDZAMo0k6rnrR/r9EZ3k+M+zst2kipDRBiFzkZTyLyml+VSIslkhz
49lssEfPH5yV8OFonsQZMixdK1IXpDpnCWwCoQhQkYsL2N3kcwqUcDKI3mUJU49pPKGh4hlS0BVQ
vxzGpMGmyWQJ71wBT8EJIB/EKfSJxl0mycJeh0/Mn0eGsTFZAurJw+FZz5LzivkxHpEzUzg3GPIq
raa8QJAKBroZisIdZ/s6QM0j3sIQD3EjeXePvTdOPISyTw2ATnY78NUjWWunZw5MZ3gYr5A21w4N
fzgyiOy4WVfMrOsqhf0vp/EiQd7z48sP0VN95Ol1OrkxIsNjenwM7CGfpMREZ6sIkBC28PDNj9HL
ox952+k6kkDhBDM4z6GIMI+dbWWKyec/j1eAJjORbCpmu8iEgAOK7JMBaU5AZMNV6Q7K1c4J0uNr
IUpy8bgDcJdWK7g5iuhnmpSD5Pw8AUb6KenKY7zB8mivHzm7/gnmGWcV7rhzNg7RSCeESHyYk86J
pXVmZkOdjnMPhMG0tG/ynyf+A4l3P/Fuj/P5AqZWAqrbp9yL7sO6iGHjypwHqxj2ZRzPPqQzfDoE
Qe82yHPFMum5r3/KZ4dFMk+X84aXnZsNrzKzc9aiF9x1uMgybCBW7oAuunjr9vDIf+Fn+OKs9jRf
dR8l+J/LNJyHnes9hkb7kmDONC4uEka6w98Oor8t8wr+PEvKdJKoNFHaL5Ho/oJfwiW7fw+QGP2Q
nMfLWVW6O+295O7yY49cq3DIG91HwQVvgypBM1kRXxM6SygebsCRf2buHuithm2ILb9A4bky4hR8
X34yDbkSFgOkAAgzUFOgEuMknITRRJqmYW/2VKNyp3I1zSOSfkmhSuLxFOkNyOM6gTyaJONZ6ghq
9GV+CBcuvwZFMs6LybtzgUP/YzeklOzf8b+I1E4eBoY7Ym1jARoNSyA409hhpw4Ft8LFDQrudLjM
90D0GE9hNOR4oFdMgGH/Txb1eRvtIMhn53GnR2zHEdyP6HpXhOxh9F41qfGyKJJsvCIxA9XsX45+
AMwHzAPN5MJc/Qn+TiadNhFRKOdR+u/A1c6BxVXwEm1tJARMR/IP1/8PtJiD8RikxappICb824wD
tG+WFKMfEhDeJyUtGjjnwbKa9qym/wrlVJDvUYkAtCqWqAciPCF2iQSZzuCLqJeJhPREFvME0A+E
M5CiPn6Eb+VslSg/fqQB4Jrz+sePg+g14gaNwN9DpErGMXBMVknpcBB9zpMKiR7wVlBGBEJi3UJ8
gBH9OQtZJK/1UfDVJ84BU1EARlOJs5xFOr6Ey6xaTtISZWMW1ABtYIx9omuw7+lMDRbOop3DfRJd
xJUQxkWRfkI+P4+Ly6QqQUfN0vlyzl994p/lExqpxMdUFwMeDwrzS8TmsxSOiYURoCZZdAFMH3TK
WDdGdoXGcKRQFTRIDC1RuStw0aJnm2Oh+cAe4UcLnL0vEBJPB4D6Hv99jZaXruLESH8YfBjJv726
yUX01ITG6dKovb0APwRuHdr2hKH6CW65L7O5JF2ltiE9w2aJFZslZFuEaMvOwDdKFoJhINRkcRtQ
7XHII3+4WeDiewZ37TpAenaggbWuik1gqGZ7Zx7eFfzda6AbMIs3/IsPwB9o5P/ZrqY2E6SR8xv1
8hQAdnV4FY86HSUqI/7HrlMAT0TYmgEEqe4pP8Mn3Teyo9mznnMuvwGLQiNgeYnqDbAAQLeSMG0c
L4ROoPFokVrGkBqaIdJ4Fc8XyWRoDWYkiSNwn8XjS8CWcUy0A6es4n0itLDjjhRP5simZ/GFtSCd
MeBkyefKvDLYswRVZon4pXI9LV/YuHNfhYle49sg/cbZAU6AhP20pN9d/qS7ZYPBQJm7Sjl94YrR
DO7gRi7iC1wgCEcxUg7UDcukLFPSZrzNS43wdoXaJJvKQBtF8w7o+x/ICgbSDtp8QL5hLU21WGcc
pjEoZoAgthSTWSq0yyPehMzAE/Ar+AB8aNCyI2g6pN3gXRgAqT5LirwMoPHY32Z+wV5wZonLiRlh
6MOka+fFHEVJ4j+k6JVRodIsf0D31xmJdhhIINCg0m4d0Z0Cd52JPQspPCYopmlFIuvAGUdA+1xs
cSUKr7C3JVJnIXzOUuybwp6bYE5u1cFNbhx3ZAtE1KEN8y+VA3m226vttQ6Pb8lvD6ETIrXIkenQ
CBnJyoyISFKtiOxDYUjAjJILgC5PxuZHEECIRdMO9WmbAKoZT+PZle4zXSeF5GqaAlzFWQ2rp3Gp
NkvDTubRVV5cgjAF8kqV4/vlc/Y2XKE5H/0WgwB65IRhL8aX8O2LOCVLPK8EzQH2BWDqFQAYnpGn
VAyAxI2L9Cxp2Fxf+8AtvvaIOujkK/IOgOzeRbjq6leOO7hRKL90TnrIjngoYA+A+AN4rdtGbXrt
bIMMwMcnPZ+zdFDFehN/Ro3FfF2vnfRJv6jfl2snN86O/kI8HM4MZJ1qtSD5aS3kGB9KX0U4Z7RZ
OkcMi+UlEgoTAh46p7ICTuJewIMLjsw7Bqt9nZgzNNfQ8PmC5rnFxorZhYd3/UuBub7uV+IXm2z9
np/Kt6q2DlM3yKIhrkVjUkO96EwIbS8EVQ/lVldxl5SPZpWmpkitU1C21VE+sB2AJhHSFVS1n+jE
nlizHAuMRlMZig01F11D3wBeMEvPihjxmfHQfZ2MvACpfWSrM4Y+Q6ireJZfLBOiNWiq7bNOLswB
NxAZDsgvOCCIRgit5CRjCDzI4JPpBZCofVgCMj+aPLJaePS5MSgQ27fIIfz840elDaBYqQzfKIj/
yjvQbRJidRdI9knVukJOHLkFQN7t+cJqi/fT7CiQhA/EF9gZYb7hu0D1cq+GN9d2qKF5u+/aBeVX
Haw6BoqH0bFxPcXlT6Djdhe99RIzspUF7oJPv8dtWGBl3JOGmTik2x9PbzjjBOJf7+YhiIbZju/j
yQsDvC2j3ck3w9Yjli5RqyF3464GJBjlPXmXUaREcOo73mPg3UPBwoJI/xwIfBWz6Uu9uSrHdtD/
jGZO9HL3RUAmMWGfBsddZ6PUWQLCASuM4RCDwIjV39aCRZENpIwiNpftJiuGkv2FNQKq8eqFz4vM
4XW3IsBsydqoLLZZuraj0L+hTFzn26RwkNkUoy5qNNlwdEPc08pQcCGndBop2TUYXZ4LDKCh87mh
0KLZhbQc5DwQBZIC6CmNVqTlJX7tHOTGDkqT4ylivZmvK2bgB8oOOVP34bzmEYjKoDRXaH1WCV6F
EDEwcWQCiJWORVgYT4OgM9jAA25J1+9gydlowDnLJ6sG6WgcgqSrwtLGq9l8M3f0vnVsKTpKZQ00
3ZLt8PkTb6TBcoGr6oZTF2Ao69aNfiQ2jbopgz8lwNYoL8q9htOhVw13aHrXcAjlczUG4XkFUPWu
ULEHYEYjpLWPEtYFXzaP8Y4a0P/eXB/Ay+gm6PbqrmCxAvwJ5Nlz8mspCX3FESddtLb65HK9sZ3E
PvYOwrVXMWhINVK6xuiv7n73k75YiwzMqMbJbIYIwn/hVNkO/IRm8aTPChpas+jePvqg0gnPR+gR
3YhxUcbyUaIN8px4XJqJpZm+pIZifG+SzFL4qlhRiWs7Xj76W11anreLhNNJen6ORmwS0+MsmQGj
veSBaTiaPrNqfHeRfsqZ1KNpejZbiYH9N6T0vpUdnomz8gqtLhptZrgzwCoQLvHLTTD87SyZphQo
RtA+Q3PNhObOssJZnFHsG3KqOW0NIP4M8NtdIhIrX9ukgeyxzNC/Ooie6NE+IZNFKWqtTmq22l8U
GIdpQICPacIOUIruyMVGLbEHlcjMvlneI/3tZufQnCxbAxSzJALJHklAicHRi59e/vDLzy+PcDS6
kpZv5Plu2TvxBpKYSx1vWJMiWnQBXTSHQ9qoFzTw8LbYjRp0fLlcSNfIvpUX5vHjZ7UJyvOiwew6
1Ua5yJu/RlVV9ak/RwBlR6dipaPg0Lx6/uKElNG+6zgaDMMv9N0j9pQ1pkWtwyF+db0XcM5E8MIt
qzso6jC05nRxTNyZH4FkILFBmgN0qIHuUGQwzXv0TXjO9NqI546/X8HQ/sR6HKOy17TaiyJlau4x
EroF+s172L8XRG/uXb1xw4X/NVlJRKMNHO7t/E0MOVzzTSeukHfen8SdeHAr94wnn9IyL5SDlklc
jKcHcrH7N6MldPosAg8B95DtffvMsrdDdjGRoRZF9HgKmGJkWqPsm08NSSps8DLx109j83n5KCrK
7REAGtNhzFmigN/KitWiRLUqTRTz7Q5Y99r7coAYs1R/QZrNbiu1EbDuhNc86wdoG/nsk+NTCOL2
n8BMntTc9oDxosdNxCtv1VnjmifORK4Q5JiZ+NlNPGKBrBK4fKCPEO8c53OMEyJ/XHvQP8e3a3Aj
uUBexWV1cPhagn2WGT0Jk3r0xmQSSOzG4FF0hOHomJCgbNVTAYG/xqpySVAmk3ETjOnEE0xB76fQ
wgK0PV8lpXXamygLXpKLiCN92OkTk4SFC9a9xn1XbzadyTifLecZrMymUoACqAFMMj/fT23jPj2N
mv3dbIhEuYRf6IuDH1RNVKzUCsIEh+MOyFmeS8w9omWJVhGcQTkg8QswCZaCE2RPnqxJP0FWEhY3
UeJD4HnpKneuRLnR/rmjy59tr7xdzS799q152EiHdYo4JtjAp8Jcm96twg80TsvxOdFqR22EEy+f
lgHZC9Xavs5TiZQjspDoaMzs/DlfTOC7m9X0LYIVjCLPYzJBt24JXP3xycntrcVe2CYt5eYrU2Z3
ttY+9pU3xykxjjOJZ0D5VIylRIEk7j10pjHRJOGDIxMZy94nFW6n8VYzjTKT17Sah7IfC/A5QQk7
WZDJakK0rC9yukThmh0xkQmhwZcpIMeF9THmUEx8qszqRotugEk0THmFLsE2EkFHq56wc6SAMBLG
BNyTLflx9JKU0qsihQO7yIn2gjx0MW1YDzBUC7sBm+agSnznOpEcHcpfYYMfhdpgCITmM1xmCELE
NHOCp3aztjMHY415b651W6zLeG6OeZlWydvrHpskaWUkFlHYydAYhFmv5+A+48rqq+uewap7VuSX
ScZxYgoZfWNiSDFpLgf9f87GZTJeFPknWFk2Tnp9B0JLZeGf0HCC0gVIrCkSZ2SpkiEnSMZMaFlx
Fo5JPsOQ3H4TiPU1DprlEfmgyGDIhjnORiyrFjJtIpDwZROojK4OACHZHMBqtBM2W5trKQRuiL6R
2R2/4a8H718fvP1wFEYR2ENAdG/2MwavnKefk8mLlveCm+HLTBk/oVrmSlWABxzhimZ2lCX6lHQn
Aik5KVzCqCFnaB26AHRdZkhoSyKDqLM7UYc0Yxz713wcny1nMTEkmTB+cvDJ3KitFN+jQNxwR+nF
V69f/vzDUW2BAspmcjUvBsa7TEoJl8FPdErZEZRfG7brbXLlylRzCiojdbxPs6KB5zHlWM79FZhp
GDN2GSx/4SThlo078HN8lszC12jCfKf2ksPPLWDgRA9ms9rDipENT8ud2it5sQC9JBifLxpvcCPc
WSQkWuJ6QOAMKISxDNAaH5wU8VUWDEcwiXF29kBZkEZokVB6DjCiZE7jiUpCKL4A+rwonYRQRYG9
Ng+6kUGABTYiqJnz4ZqXzEMjSqPpN37OQxozxBqMgfX9SAuC58nqYy50GygPH4Y7tG7aUb4sxkn9
FNXAjVFitFOwHAkIqQWTcfZg8FEnHsuEAEh4lo0Ia4IdjoxytBs9J/Wvko7kp7jYT05WmLeOnzQK
hXu9+ZOSF2ID8YhiWJddxRG1ZA4sg+CwWnJJ2+o6lM3n41LrswWQkhI2nI+M8m/ey6X2lyiAdBiG
j9qHbv6IcIgva520YhjsWYscFpjGxiDSVYkVydhM1pXgX07ORG0ZQKg7GAx6rREBgcz2vcTyafjn
tVXOmPFijKBNEB9Gx9fy+zXKYFSXACQWTOTIYtSHb04kM4vvYRw5UMd4zJaJcjmfi1vlG7WWfVQ5
5WPUjYmDkRaplz+yXeCdGLxmKwfrYL4zZDqSEYOzs7YBjCKgPLu+ZPlqjgXiGE6TKT7R6Ig/jDKg
SCOIXGxK4wBX9X8dIUOPZ0IA5vEl+/oEJcspKwgkjc/xm0OdrkMMsBoES5tz1EzIOUxzIYW3HETA
GSMq0kHS9FAtKiQ+gg5STkkSro1FCkaprw2ijwJDH7moh65Qki5g8pNkjR9LjwX9zwxmXpJn2am5
MfSOb3xwxjluGqjTO8EBmm9puKuTnDLBDAxXNiXUULtx46f7/ujGFtELbuAudwKC5T0QGjoYP0b+
IHAFHS84OhK7USupI0Mowq8/AF3SEQiw/ft0qdMUkmdFLdwmY0uRv4+fnfzJY9YM2VxuoJqgkdMP
0MeFgHJgSY2A6mPkGjHQ0EBP15nGtiO17xOmqEWyz0QVfkhhlxwriuyX43yRGFqMJn8bT0X5dqhM
MdJXzBBQ8Ni3AsE859T36gqVWyUvsbj+kZbJg1yrBQ0dhPVMynLUilfRU9HLEaFRCWUK8ZGAUlIG
CQA/Gvoo+pjJRJklqGBTxRgRjuKSLb8yFpoyP4pHP/mUUg4MuyP1JS6OgA4TGLxPZJYEHxwYM8rY
Uu6UROmgjf9KfC9x9N2zf/YK88Cr6Yz8IvAykclmRVr2x6MvDDkCLw1mVWSETVRkA+XwRnowKkJp
B/IbbTB6RkRW61Hf7dSGxuHfW4yDJ+wPQ9UG2mmWkTj/XijURsHOIVFP1XMjtCqeTCyhesn37oNS
bUenDjIu7qXICxIDijCfxVUk51WKfJhmHNeW55dIHFxT3ioQz0Jh8mYQvUkwy5ZtJPiMhAUxS58Y
uoZPwXuzZemSFTaXonREX7GVBtxUOGNVQxpLRiWVotK5VwAMScTZDFdI99AOVyB9Ogfs42WZoCbz
Em/CZHtaAier57kNJfEkgSasfGBR5R/ounoqlfw471UQdIFsK7k6cG/dXReT8GzCgzKpoqt8CZoJ
Vjjqe3m2XOIGkMuLMQgVNx/zFMD+eoMVfK4l576vyfcfch5ETad9U07uhiMGxZhPiTifEuHW5J1A
dUSKoz03ZlnyQrl+DGbdxTIrTWIsm8moWsRaq7aLPu6GH/IhdNtRwGoqiALNCka7atA+7kY1YTPe
mgPxEVKvKqe83/ChSTJLqmRXyZnf2iQ5b8lb2Ijr2LBqjhcMtyBJ183+omj8xKYioIwq/kAOKqQq
DJzMywUHGGxtyio+NEkA+fOVesfTSkp54WvszpmmaP9aEV4kcVE6GWYWA2QRfeNuQgM8+XC5FJZk
jrmVvlJQ6CW0xnzVsisYwrh7pfyFZEp0rHtIgjk64vfl+hecYAu6A4e37ptAl3J5Bl+tlhRYsdZn
1OHznYQ2P7wYHHULk7h5AFDdTX7SvWGApb9ur+ltCco/cGUSp4ojlY2gaxgRjdmsQKNlahydDsT0
gCOMpayJsRtRsZM0cxHBeAkE8tgxKKAsw/Kxcpo0e1XT+WKWYFFZJrzoEO6UWlOFNDPSxWCXeTxb
Oo8JdwCqdbjcDibF8clw2ScjoOSJG5drBiouV2HFsnjIdkTatA5bA7nyodAUZc/dsUI5RS/lfp82
v7cZCGtBtPoBIOVHVLbBVii0pMUccVUsySp/jhkIA6XhzVgXyFGEdMD03/FYDsqZNTww7v2J1CWy
UGjQK1pcHxzZuYRC3HTo12zbNpFaN2h3xUl95GdK5TU2NnRORYGQhe3bmB2lIlQt2LEYR0nKJctK
SdMAIRHjAthY/Dz6CDIVfw4L68rEBHvtJ6XSBe0cF2T6WIIsVX0kzlnWnkE3Vr5YORUyYGbJIqYM
DLX4YEwGMzsKMIzRQyqREkZLNEGJlpPScqW0QJ5rRCRVctL4QUsQBhjcBHBW+kEhVb5EbxcC2AKD
GUEKtZl46m0YIZ7h3gS+YdwhKnMAaC275z+h3lCMBKzp0x3auABz8RLg7o3zfargQRMhYuURLr6h
FAIIV6xVmIMg/60plYxoCBUGmuTRJB8yhqCzhX4gfaLZbkWf0JTMI3dbVFRHkP4PSqAkJThmL7KG
4ks+MDuT764bvluwRbfu1973Ky1wmmuY4ouPVczhUVQgWWU8kzIL8z6IKTA+Sif42dK60edKfa5Q
okYsZ11zGgP/Z+OyseAGMVIoOWQq0hxgrCyG0FbxWbM46vneYfsOr+LSxxmc4g6Giu18+zcPn6Wx
npeJxtEUdse87Cd+4Hb6Fgce2iM6d5yVfbKYlxWHe4s3FM1lK43gs+U0hOqyHR/mk0/YGkd0ht0O
piY5fcLYK7RcsyQISphTIu/wsMlnoDHo2NX7ZGPAYfaJF84X1Yq8l1yHl0o+gxBaLtFlqmshSVIG
JEudBtlr8GMtp8Uo20PH/tbRA/Ho+zQ4BC+nZQvhRVRDOWX5KzzpeyATh6BOl+KhNpK2EgbO9Kio
5BfILekEHeYw+smNepz3xBcxpXr8REj8aj0oDCSzczypK8qDLT3VvRGz6x5bWX+wasYbmJW1WO5k
k1S355+c41AJ7vMkmbyi8phlV4QIN5sryIbqUAEIJ19KrsGbeeFcqHsGCGndV0iFcy94WWQxlkGz
F+rVwD9wUNsEBL+/LSXwkWHJ1NKccHINWv9gvAtqgIIhDyKzqQ+BPhWh8Z7MjfN5vP9vOZUO0WKk
IKFO0wVZSlMWYFE8nScVynVEDiTTyJEM67W26TtYiCYW2gMcq8sfx8r7nR7Ldt1On31gsVs8Wo6m
9rqsZfMAtfwHU+U3asqYcO7S2bJ0hmHrYSHiDp0swhaRZfseHTBcl/qp5jodF1z/G8/aVhnezNVi
Ye5NPEwZ/+2heI3jPYTvHcF53cg+oAf5k988e6YW+e2G29I4yuH7YgFhOYoz8xBtTP0Blr0qW/3Q
VNt2Q1X7Xh+RvluLNSgffL6kApU8FIJqKBgYvxdZ/cWBdo6ohvw9+sg78VHssSwgLotSwhNB9shB
UqClYQHQZGJSDj9iBU9bGIvrAaPa1kT/nFQo6+ogCJBzl9OGMxZC5ZXXt/D6CsYeKMx26WBH9P96
piMRXgxyPHkiUzP0eVx+6nKiDlZjBjEcUzFmFOBBEEGUUVmAWJu5XRC86f7J/RJOxfp2tjzH1afY
nQXp5ut3kitOIQsIZ/D6gP/owsPuTbmcX+nEGm8C8MH/eTtjOJUyJhgYsZ0C4bDAyhx0+PgUFfZR
p4ITewqTeE5l54H2jpbV+f5/b4F+zCWGbQNW94LH3v8hLekcUFcdAhWjqFVkAc/NBo4eXd88Mgn6
etVXx9YQI5Z6BjBHoUfJZ9xphyTxE1smtVB5UvVaIC5SE6Yh8RvYSkpMCCStvpZ7gfXParnUBDuO
MHTw/sVPr399efri3c+/vHl71Pf8XTzwezy3ngnn2vfXuAuJXrctD0mpH5RYN9Hr7Q8XSU1onBJ0
10QkJctIIiwMfBm6ReASEK+DFx9e//r6w/+2MNNC3QhwLPVqKIBnX9wPYMTL8LO5wyr63jbBz2TH
oRJgeZ3OAr1/+rFUwivEiAADUZ6bG9fFYWfVtG+lQmw5oFG15AwLUj1izO6zyZiSpTyxNSljIm2w
G+RIw9ElT3Pg5fFJYMkeB+fb+mBSCJe9ivGympLfI8y2ryfx6fJqaXzkgnnuRc2ps0SDYUzvLwmV
23vsBsttCJQbKE/TPbIa0D13gpKDZVtAV/7yP3KtcSs2DaS/ZXLIlip5UEI1DKyXJJm7q+IHk4lN
zwB1u/YLrQ0cqgHD3nB4zl9v1jprm3ewlBDoblP8j2mXskUU4eb/1oUQ3WPIglu+pVHz7tfSNFor
ujTB9VahxAGghAHE9wYoL1igj53cM85B8EHFYAPBCdcDkOCcdCI+JrLNaPcxHLW8FThJET4/Qg2w
uQm+MFZFFN3miNd6vx4KNGoCxg0Raq1BpX9OwNtMop6CApgUxmhIfz0AAB5SPRELfVIUhOIRpK1g
Qc4y20BUzby28RINRjVEfLClV4fRsWRqz+IxZahc9dU1QA8AWwb2MRHTiAfhtwFg2au7QrBs/zak
82uC3J1slfcApeotFUDVPx8AUj9gupXTLNUGj5lVBPzVo5Xv3WBebkrArgZxGxD4UllvTPrv26q2
Niwsm4j13tMAdoFN3Z5bAOc/wHAdGG7wsdwzuTQJ07bXHIj2DdB3WwpG078vIPl7FNy2AgkQhSoD
EfjHQwEEFiUtZlgtUZQzI8Y1AIY6ZJVAacUrJFJc6M2LgXluC4ZhpbMrbFFsoCpbzs/INOjHGcsX
bGicExbHr/TZUEnRN3RhgM6aRbc3SMtJepFWbksB85U0q+T93i2AGk/gFjCtE/5zEcCHxoB2c59B
gDXRBQL+Gl5gtn13KxpbRZwATvLFGXWl7kuwUQN1p7w9fqPqB255s6vTcOq7mgCeshH+qaRz+Mkd
sj+v6ZF7S+5QZoFmbUnwmOQmvSPOpH4Sbg4K0mwfENvKjZ+FREPEJQH1f/tuLZcxW8YL1syJzVmL
8uXOV4og91GtlZoM3PRuBTkCMbqrUmzm7qajxQKjYTxgCYGhbyQQUK7SSXniC7vRvuEpAFymJJcR
w1wLogA8GRrbktkmiNOjEJBewcR2gaKtzE3NAoyu1iffVvVoyqq9hRGrv0vqqgDBkHZnezPlfxxM
2syhag4pp4HPth4czkhzNQwfdbiqINWapbBqSqOgo7PsByMpHfAW5xj53ereGLh8/OyEHb/H3wyx
vVqwHs95ok7427Rr3uMWd/S6OKO007Ut085J8lSortZXZygOCfK+7D32gwhMtF9MSCEJIY7kieXC
2d1BUVwaBRZnWCFxWe3n5/sT0zxIcmWZQ7FjYTMg+B0g3rt+LOoFsT0YcJch0/+AfSzaUYhOviQP
ntHqlQa6fQRMpS+uDQMnRxW5eKfXW1PPecZhK0HF0BAV+80K8q7CoIaLiaxNqaGhS4UuItLiv30q
yyUVuYIJ32xlGHeODEesn9k9qdgIh96hUnki7MRxjSXWSyJvXE6sz82SbxDVqVIyD3V8TC2KYKCT
E80+38KqaLdTtwjX2Q0SRYtJvX4M8pHe1k6VZk7XxoC2Hla2ICjjwBcfTNd5IPDejoCs03MAKFXH
uaU6oy3MdtZlrM6icKQqy7aaCi1urRXjfnGuZsVgh7WDgL7pIkSnr9EYUcdmsUPol5tQ+K7e0i9s
aXhg7NsEoev0aADQe9ahjTzlKNEs0NQ05i31YwWMr0w9vuupfnH12AEGTzW+Z8HABwCHOAUa8h0Y
/gPqt21FJe6H8Pz9EZ/1rL+mQN6D4hBAWN8L9kRBM9QbFWp2VhvhxX38jlEbSZzXuqoaScjDJpPv
veyOBXfuObyKPZNt2BOnLXSyIaZzp4jMIPnDCD12slje1vzhpBrYiZOdRP+oJzFIwVxeEOXw8U9n
rPYEiqCs7d3SIW62NWqQau72M9KCuId672FO816OdN3YWyRAsBiszzxD+it9wuy1dUkSlKHpvG6z
V7d7/xZJFsaYoiinWRY2QFQfUelfQn5vkRlRz4pgk0N7ZgRAY3Q2y8+Aa4ECOislWtXPaXLCj9sJ
hwtgFqyc2OQgFNkjX7Xizvh9C9BbpFDswL8IiN5ls9UIi3Z06c+eA0v2lrm0E99lMLOj8N89B9zs
PQuBvSALZAdi0BBz/+VIwsMShWgblK8h9toBm5B+h2h+xQAnTr8luN/kP3H1DTSNRSj28/TCwP9o
X8uWscwpqc/0/rjAcqvUt4urnH4BzJShNVTzVkjTigp7686lETvq0k6NZrz8/fDd+w82ZaH2gCM8
rU1Z0Bf3dU93yIMxSPkUj7o0NhUe6Fe49oIo+ZbS408gE86xArcZV+p4G2jimLJB9IP2DorPci6O
S2Nc64vYOIhmj2UUFssCLiZDTT8wea2i7JZXWMz8KvpIi/hYNzzx4hqq8n8KV+gW6thtA92pu3Y3
n7Z17VPSyXHLeheZZc5DrnezAFkT+7a7HX3gYlaqIQ5L9Lj1s6ScDAWbOs1WLrFWqalfSo2X8e4U
6xjuU8sfqiKFRU1XlWPzx1YZK3YS+ZsKy25YrrF18XttlV3adfRn3zXp6Nip10gt1ze2k63z5Zte
LaaCbnG/iWJ1l6N+anZQqgPmVxnqmbobv8nt2x78B69Vmn6sT2dBKdb4ww3NxGOjQGVONtSeOZ1q
ClsughfRbOqCXSTjhPrLcP/1ZHxJLMKrFV2CBl459txzjLdqOvir+lrNqfNLf9yp17I6aULHHYLp
zomX1Xn6+89Hvz9cBid/mCwnJ5tEtc7v+wpB+0c/HXz7X/8bWs95hHIad05uencB3gkceAvgAoW+
J6DFj9QAtgan+wE94rzpfL6IMyp7BZAgJQXMwdtPiHiNX8JKHKiGpayZcPbHNKYCe15tLXbhLhdR
fK5Ncs7jcbUZzCf+ztwHiK8Fgmbwx1lg9l6Yu3f/2HB4+OHrwQYEyy0wYbcCeluVo7ldxSG20AYF
fqjlLhzCvnZjnWi1qKsYWGM2tMVQ5zHX7bZN+qoYu/+JFMwRCiZDVNrWcvImvjFLziu3pSDbFTdU
QG2qxejVqPlyFVD/RFUY17olv3x7jcB/aWpetTkwsejRgkbkUAMhF/KWKxAUyVVKJTKEZvNakfBJ
xp1X+awZ1jyXZbNztB898w30XWxUVG9df4vigM43Oi+meU7Vc8xaq1xJwC1qlvJm1NBk+4jtO7o0
/uSo5+XYp5+ANO7P4+ISuyJIXRauyRJtCBJDTTSuDYF1k9IK2RG1T8dGV2zoHseLtOIYMjqcydDp
z/5YPonSB9tBgqLCVLoUKHHftKHHzP6YrksPW8w7L1LsL9WQ/i/9Hmy7YAoxe16PTUupEKmGpmFW
+2Ti1RzsyiYdUUXLxaq3XcwZv2S11lfOKN1dRD7aMK2aw9PXlWsf15Sq6mkHVFtmQJnUHISnFVAW
Emam1qDgnyTKiCW/iBaGUOX3tsEJQNsqnMvbDTRdeNtxTz5bBimHDlO3IxCZnnS4kSECT59KtKdZ
LBVIjm07Oqen3OG4upFwLkmANr3mQCZeJDA+QOoFws6EMAhkXoz9lCqD3zx71kyjVcH3NrMe+UUT
r/mA3Ylv5wje2vvbVPFOStDSlNGPKGc4bIOEL1yjXWbxlOVAIyPgHwJc7wRN7gu+iCYRrxdu7CGn
wptCTeP5e1vHkw1nuQ4Cdgn+2/KY/5Bj3Ug369F2cqamVCmjtngQnnTuWK2Udrtsqk22Sa6nN4eR
HL0bj+du59Sd9h+HKTVp+gHI8BbysWyWH+eneYlOfW59YG1y4t+NDNzIGySOsJk2rIsuvE8a8Yey
Akd89YsL3irLgeotJBPTqFSGLEXvAsG10iKHyTxdztkakK2E3peXLB/uU00mCcsV2cB0RXREMU4U
MQ3AUJ9TOTehJN0yn31KUOyscpIgzvKKHJlGVOaZ7ion44BGUh7cVTyG0WzuRigeyw5Kb3cQj6MD
k8+x0qayCS8DxDMujuWlenSvUdGq04Wbm95z9LlkvNXUlkTbgtghMU+Ew6q4c1WYItIPm5zAE4gt
pRalkgUYoBuGECukwI8phge1g5Ws/Qi0nTbascZS+c9NChyMPpgnZQkiphMpiVe5kpfvDL01t1Xo
t2z2nbOiW6gpeCSqpTiqGl9mZUVc2noYWZJMSi0c/2/LCZUpmhTxeaX5NdSEooDzxE7HdKHCALmL
dOxgmnx0z/Mjd5zmpiK/98PueuRMJSziVhoM5NphbDGjUouBgmXfmcZOs/Q0c5yAcZFJgRuxfAOE
hiqVBz47qlTB0aEO4Z3dPcm8gmMbVCptjY2Vvt2Y2L/ecO8OCpsgoi0Uz6/MQ521TO2UqPvdt9/2
GPFd4qnaLE1JmsohyWTzLDe1QxmOkiBL8rKOyZeNvtZz8iNUz6ltKw5NQz3BST5ppSzBqfmtF8n/
PvLPcFtNrrG14hcI4N0h8RS3QPO5jjvE+dGJ0VHAtvfMlYbU2I4B02EbtN9KVghp9paSsM6mSWkU
7PliSiNusK8zGpxpBDhv+7686rhT1vIfcOqbmVxdp5Qjf1idknlfW1oXi1qTW2uZ3vbeg5Z5d8Sq
6ZgPwJZuq2P6XMktg2OfK5L9sCNFlU9ibBFpxYavJRuteWRqRYf8OehVp/dhlC+v3jYyzC+v3vb/
vpmgoy3DTmFC+W1KApiaAKYh6VsZrIrPht7ots1q4XjNOUgBzhugAtVHGwQfpyVK60T8SI5GFDzL
l5UbPzdhyR8bj8IUgGjOS1FisSjAVkWet+EMsgbLEGSVOyg+ug1KeVzaHstdaS3Lywx1ABlgV/Hf
nzoCoc79XihsTQjGzDf+ALpM0slftbAwlfTGf8+o32eVVjOJxcdS03gBI5Xw/3/Dc73xi8j8FV3k
uk24cwwIUl8bCDM3EkR+SmEXeJt0fE6HblIZ7NTxVIiokdQvJRnhiTap35Cy9ZK/nhgJ+9cdPYlh
jU7xAd3cPft3B4plZ1MHrC9ryZfv16UuAdTbZMy7Mpae8/ayVUPSvG7SrknzurgGcede8XCXaJMQ
sdYCbxPjtVLLfSSsf4VAW4+EiNT5DVufzpfzrbhljTkevone6PuGQc6Bn+EXiMmFnxM+iYZVtH9h
KGeEAfdja9UFSTQtL4HsfUpm0nBR8zZwS6Umj7FLd9CsSkHIaCqepBIAx5kjg536I2wV/Drf1y2z
mH3Iq3xDi9Qd2YGZci6NZaUYfH0RY+kVz3SItXbYWBei9sKdwM48tb4mJPHNi7ov6wCtODwuF7s1
TgjZrlOskCCEgztkSli5JkET6X+a3zRw2QZOmVba6pvtnTQZayG7K58MDkPYpSl5P2ws5d7OMPce
VNI3Bz9sB6Ivyz8daKzz0EagvA+OSjBwhyo0webtylfdRTfw1ofExfvmtMFOPDzD3Z3v/lFAvyUA
rC+d+iCQIOVfjDTwpWuoeoew+COLxNzytIJypQ/IOv0zcvRv7CjcXKhlfeVSa8nasmBpcFwPV89l
fTXRL+AM+qq54+DzrPwcFPi8TzlU/dAsZZMgtI+ZNk7moFf8kwOYQGDFjWUbcsgxa7lCwa7yMkye
YwuQuE0TyR/K2sPTT9lkgL26Ps9nnBFU7oO4jGU88zH1wh2UC5xxOU2Saj4b0L/3217Rjy3Go+Jj
etQJQ9LTDEgox4+jOlKk0tj2VlZKyTuPsJU9obUqY97QMRkW0Si3SI1fDiO/5e1c3kYISVD+8ZzV
RNPx+KlpGnWwr6tX0rLtDgqWO2M399le3al8rWPKa9CmKNe7pk25U9hVl2qYPhfydOb/YJZK9+OB
uZKXQaFSDPBsv2xQmDA9vNEiycmOYtNVqyR7lHYwR65VsfxEofVqlndKomR5uz8MfVPuyeyubN1Z
1Qom1wJlX5aduJNoqvRp734VZktvz3bVsby1NmbY3TeO3rtu5a3/C2lWXzWEOwxV7Y4YXri18287
Tkp1qktT2MoG0RFon6OvFq2dMJhTtWrCtsYW5kpkuaT4VmfyEjZY5FeDHXu7boPs8pF92iGL5W/4
8gu8ugNvlU2x3NUsmy2/Nn4N26YGRk0G7ebNDAnA3Jnfruy4acnIK7w13wuuy3asM2nyI8iYVUfD
VDaUYwUFUbwq2f5dmnpFFOQ5TSYX6GbfZOCUJn/xXJW7s/xzX0MKudw61lcqgOPeLzf2Dkm4Ma83
5MNy9T78gzurc84kYVotgPVlObAHoXUW7ALq12Dn9PZsVw7sr7WBBd8/Wt47C/Y24OtjwX8ciG99
7uvMm/cLAGrV9CSDXUybe07NlJtbmTa9A/j6DZtNJ+WZNh8AQ5vOZ0uzpm3uh+XF7mjW9I7qoYya
D2HS/HvigHWj5i3F02Zb5kYjpjFgspFkKyOmt4t/ehNm/Tjqxss3B4eAaRm8HBeXu0VabqNx9ekD
35sPYFqg+Rz3mKoomy5L5nmWjjlNELUOGzpi631yCUIWoEHfgL1eaW4ThmjarCpTy4+JEJcx1M+1
KnJ0HA+hrhnVaN9uteMJ17t2m+4t4mRLxWxRn8LOoSfta6QYlIZFfrEIFKwlj8qamZlTQpa3AYDN
qGZ9A43bBaHEVPWe41AkRDOmRFFgUSibnN1nRErDQanFNL+qW0rx2r3pZ7szqYbp4hTXQtsX9sI1
gG1DsEp9xl9FqErDRu4cr9K0AU2BKw+GwPcettKwK18sduXPiiG7QsjayJaHABXRAAN55cuqgE3H
8icIcWk/Pz/W5eFYdNOp/RGKYdMBPnATo4eJfPmPwYgb4mLuUVZWTVL1RxTZqKwGhaVvDnhp2Ly/
A5VxEe6/rzO26E7bao6iM85irtpI/UHxFNJiguUOKpSNYGTqLoW6PfeKSWczU0aUUgZQ29Nq8tEv
r38gXYcri3JpF1boTPYrjqI64SA6wsozY8zpi6tpiREV0nZA2IeFz2v9hZ0HjCaYY9XTchD9DGeM
laKp6OLR8mwOAERF+NMMHkg5soMBf5+TIBQ2l9V0yKXWsRgfRfOww5BieNjNug3ScC+KfavhPY2v
YjhiU6gQ/fQHcukFPWwwaBfE4e+4mqSMycW3M26fDEsAcC7yT9gHiNyoJowUtVJdQB+bhOg7AAJ8
5NgLqCbqmtUMzdtHlJ+rNw7ke11sx4fEFBtg+eQU5eBTgJHTn16//QAsrNs58GGHWkSfwkowhAih
YBVVV8mM6iRX1JoE1kgJzSWHYs1igKlFXJA5oOOj36PG/gmU/JLMZtH333CnlDJ67VdBIlwePFpD
K9c2lZgRKLb1k/AP02SyniUgJGGlaNiFvlLDEMGdQLG0sh5fLChl0GzGhjdGQCfrHNs7gZ5MQFLm
84SFMlsKSdM2FINtNXe8UrmdaiZ5wm9MqVkxoZnjHKWaQkwJtQMNzhuBayEhVlKMSItYnOWfkeCf
JaiEYxUWA33LFJO8nT3k7mY9k96OYFJ0Tfo6zqpWDj4tD837XRjxNtnqdgYgLhgQXlN8WM7uvdDk
AUMFfd7l5eFjOtO3efUKC9PdthGD30xOC0E9crsyTAXK8HyvbwbRC6xTQFeABKlaHKAPbj8hz6Nm
waizAalMVXnch6Djneng6ez12mrfsnf7yvcE/8aAAVVy4G3sLYRn4AsO5X1P+ESkssViKbAME51T
UEvc0JKN4/qoVQzgkW0XE0cljM+9CbTimIdRm0tHyjRg/0KQYlLQrQXZyZb4suUmeNy9c+sWYNlZ
A5YGYsI+rz6UPLh4HALb02v55XUTCqDOPOKSfm1bEW41ftC8cL9tWdq3vQ2a3Z23kwr79Figo2mu
adPjSm92u47kYtfeDZjk+2S6mqArQZqAoEB5XiTldH1/XB3vkGG860sszV/2bQH6QCshPs3kjjuE
Ec7v8B/XOxQpvpX6ecTOrOauZO4FDYf0SEZkGV99yf+Eem5aPpc9IEUgSq2Cj0IpfNXZXxRxu71W
tzA8LJ7I1xkIBwP82g8wdV/91zg+qkJzfWN1aqwkyTWxqDlSXoLsukoqkmrhDonKoN9lVBakxCI8
ANIb5tCt8oWg8VH674n6IQf+5Y2GAXnNG8K55jbRM/edvnqbhge0TqvVqNOxm0FHg2eO/67bTnqw
vpna6oTe79JDPc9aM9AnTmXCXfnXPoYkCDfUx7cASOWtPs+4LzA8+X61RfHYdEINCeD5Y/yN5XMM
Rgwb8B6ffBgj7XITfeNOh20kbtfeIluh7yFoSJgdEO6w02pY6uvw5FCzxVcBsGFrStIkyuVZmZA6
dd0yTD+qUDGGGX2gAref8tkh17cVqZ9br2Cmy3i2nCSvEvzjPIG9mCaTJbpZ4I+f1Z+M6iw/Qsr8
i2lcXCQlCKSClTpX0s9tPx4u5LlfppPkuXySM9jjBT9aq7tlO+1JW55lle+b2kJund1CozRZFEMd
ORrPUkwrOluiqSRDRYPKU2E1Ga7pjy24S1khBzxwsQUZrGk61HkXRWyp7Kbnwd1JJJq5bS16qMaG
9oRY5JOIGsnxpknkai6NP3HC0mfLXRNmn0iVXh2UtGSuyiXHSZzARBhjlTPTthD9riVID2j/R941
iH6j3obotfemSj6Avp0Lb8wsiYvS7ZEIR8jaPXaP4E3AlmFK4dnsZD6Py91zGsdypeC8Eve64saO
DIqd0lWNlrXIDg1MRemx7t/ITHBEwpx5xcUm7s2uiMJ/EaZQuTaDTuEQFgXhjkUp8wf+Olvy+GNG
MPjFmkE4FgidhuPi2Sqd2mtgb5v497EZ6SRkN8y/K7VoIZseRpdIoU2mExd+NuaGjNtUA105D0bh
2hNllYJmiulUuc2U8p7EpTHn7HA1uyxqn+lwr27InZE5QmCjWTjhq/KVHtsr9lqkg4cRPbYVQbYd
526SSiCx0B5yk9G0AmJL24UXe1EyA2rjSjTmNXyOSOCLaZ6Ojdwx4DH9F7aUVBAUWBBqhfEtBKpj
GaMRutcKVPh9oQftM7AEw3xP3zlp/NSvfLcrT7mScuyIw0SYucu01JQlQyCIyJw8g6omEmsyQORi
GRSENU8bvBQCLiZJQkBE1QnbGpcYEWaEdHwekdwSP7jxNr9yqCOamFwE0xV7e4fjGNDAqkfJpCtD
BYgrSKsYB++anzwdJH4Me82kTCbjEIheSFTwGwKMw1viOg1xD4hO49wRy2kMt8l3g7aB8Mv1tQ7I
SdIOxI9t6UIpv4jNCqhdcf6ZavlhfX0SGFIfBkEiLIKh0D+UZjiA1nNPL+DPffg4uhykfehVOAMp
N9gXj05fPts3Fd37wqZvvBfLmOzPBvm8BQvOh7AQVEnFIfok4jcwFKpfGdWMzfwZKozKG+bUyOyT
B4R3AY36Abk0LN2WZkPr0EShuE9rcosKouvbXuP96Rh7wa6ecN7XkR2QL/CAvNXOTb20tQ+fD8kZ
QTr89XzAdCWqdsAM5C5uEa9H7Q1xEtBtI22tId2uROYP7bweDOxIeO0j+2KgP7Q7QDC2I1a2j+3L
nmZY991mxvPKPtF1nq5NgWTZtd9Xadf9OL/V+mW63dXn/G9aYXgdhTpDQ16gXxk2YWIUpA0x5fqB
ZOqN4QndZu7O15vIha7hhXms6+rDOgN4cWvW5K/e1ajb1281glP3hQEy9swClzdYAF2i5Kw5WVTa
xqtiWZ4u5hcD+N8RKPBAfwaqRQ24pwPHAJFyzmrwC6OYewPiA3MMHkBTesPDXZ8w6snuImc4FF1e
35aEN1Iz84VmYmeqYGeNbXk0no7sFdRmk7c85ADEblFTkuYYNh2aIrac9kEmppPCArI8xAl8fIWm
IJQWz4EdYChoSY7cOCuvMEGbA/6laf0UWHZZiQ1wP5xU5et60VORKrbEL5zNvybY0epUfr4CiPJV
sTDkyqodLGEGekOLLNjOiUhI3EX7o6u1Ma1BICAHeFBWIGF119VQFclU2xjAJs3LboNgASszvWrQ
PKO4Mmxc3A4wTICq82hn2p3rG9PwieJN2LBj5oGNpayDSacaHJ2iH+2DI6uzjFfJoC8/o+Gs6+9b
XxG2r4DTt5DQa96Fx9ETHYSSAjSUotK0fJ4JS6oVy6tl5Hnl6raNBYY5lNS0WVMRFizxkvr1pY7j
ba5HwJrZ9V+KGwI3OCfWA12H31pZTCJDzXbLRjecnczrWJ89QbEU3m7gz0eb2Ic1Y516bxARCNit
DnaiqSWVY7pmsbZJRjDRWGssAmJCOw3eaJqFHW6reSQzmIlrfdXffxm1sCmnjK/cCj/cGzZY23AN
E1DLgfl+SrRhBXKY5xTdIEFcHMUhsCGm2GAwREW1ATTv0qc8Rb184okqtd1Br34Dw+ts8ioF/hWf
xBrLrJGiRr43YyQ+DQHSkTotmuwvo0bHx8jzgrSJ0aNGv8fIOEDaRPuR5zyxKsMo9LbUBdCRK0Pa
33X/WtMAgjwyhhnAnNeITtd/0xUMRyJM+t6chm+rhjpilbW32YdfGQPe3Xz09+cMPH3xy9GHd29O
X71++fMPRxTQSKHq/chEls/zCf1rKxLvSafAOh0r4qs1pCLa/xeyIzRFg6pIEYvNnDhOX91G7Eyh
0H70rWB9cOvIQZ6mrTPEwUJY32dmi5tyVqIn5JxawKC3CwOYSJijidFQk5R5sriRYCwy5celOp32
nUJ5l0lCoboaIWXafkg4o4SjMqRFbAsSqiJSMMoUthkffGU8y0v+Cgxrm/cRDEZz2CQsr5bTvLWF
fMFV15Ac9imUkTJZ5ZhMpRZjoijNJlNTc0sZ2bTZJsSa2IhdFRBgBwAPRtipKSAttiOjY2ypT9D6
ceVx1NnWVBgpy5QtURXkscNN1CpMUg+eEJ+fx1DYxqeGPPKmIkPZc30tIKDsyhj3NsjsvpSgkju/
xpA3AgCm2Ouuy6axdp+R2REbBE8Am9OMgvlJXMdTa5PPYSnu02FUl2ZFpdkycbgfIdCIrcy4Avqu
J8LIM6GU7Ngh7WfFGsku8NmsLu3ZR+lr571oNJIv6AVc/Tmu1qeAgcABG3ZMc0XJjwdYv1RRtr0D
GgCWfo9A2pXtpiMKvrSlyBwcfUNzpRcBPS0M4VkZwjToNKoptYmn5av0czIJj8uDzN0UJR7JDuVt
sIhZ7gzYgFMDUyu0ry0BhetuC8rRnqPLStlZll8ppIsJB6WtKp0n7gt6bWB+0HuwV7msES+Wi2Q8
6nBMrMMxRZ6UxtFIWepM8QndfcI2DVegRZm+yC+KpKQkAuoSSk9NAGFtr9U929fEUeAo5r6MV0go
r6YrbDMkcjTFhGCKLDK10inwFZd5Ji2KnaeYbFTpTH3WlKpSJWFpXzPtEffJFuu8XFWipYxCr4tN
vGKLBBKjjvkAcgR+Ds3xQaNwdW10EJA6KFggXHLpSGFpspTrG4odub4ZytzJaUNzb9Yf8ZjcT7vz
PZa5niCFcR5h1HCm7gewUm6EbpDN3Zcurtt8oc4iOpziwNGvuFADGg5Dkb0yx3Dixk4IhNMzvozn
64m3l/Ks7a5Nzvv48VpaaHG4S5FjPlnfiDU3Hz9iXNWlRpabqGv+Qp/PHO+Sf45AXHxwc34LaymY
2h507kQk2caI4gaImVawqotJtn9xTVR67rR8c8KVeCC1xnB/XpEvWZQnRFb/IQf/KEiA0Idck0AW
bqLpEi6VV3CCGAi1rEgmxJoRsVTajpD+2IIdnEUokWCli7oGVes+PzrhwOXXIq6F2jBgykH9uKmx
JFVhqZ3oLlKbQSAq2cwtrFV2u4V4+oXEUtmNXaRTs3O6OpEmXUtEi8nEDeRgyBuFKrCwVTMfakOD
p4LVWeMr04GGLvVc3c+/L/ogfvH45MaaLM3AyFJG14vjjmnV0zkZRovQd0rIt0BybyTE+vA9pz61
ug3NZPiyms6V7oP0TVleCREB3o1js7ATFMC9D/J6w2Py3uQ51V+VuRrQ6uocUP3kOf9FJm3fM7Pu
GS0pPCkjOzb5gvqhlH8rdH1Ry8dsERpdJYIAkSkTr2KknQ9lH0fhbluMH9W202xbx+QLkw0Tznfo
yYrO/RMyY5K+b5KmLUDY53p956Scy2Y1gzKppAu1I6Cw9aD5IRPeYB9BfwWfeDhhFkJpuoEg2vDg
QcXrIpkyGBhBqbuTHumAA2mxaN5pMlQa6Z8/1CLou+SbXqljxagZK2SAhSVS5hMqElO3TBKIW6Tz
hpPmgyWBAzi6kTfq7KdT2oIRQzGi1KqVUS6osSrlGQfzKNOOy35kzEcH2Qrvq6BABeJcYUSIEHIJ
VIqeO2ZrUzZMpT/ENWDK+RJNrC5LRi28yWkUsmmbbbQG7S3cE3+2VdNQdMFEd8VuMaCBqBl1Bv+W
A2lRcwAlrYJ8WnV7DmTS0xJrjT8BFrtKbztq4udbEq/aVOpgTFGRnqFhm4WgaYV3iAx95w3H6vjo
aBY9D774mi/rUhYp22r7NFk/4dmX6UxuMoa/g6wvldJS7mlImIJJCfCdYs+rGaLObXvk64I1JV9E
QjV7e35oJd1tEnHUj+E80CIuiN0S5/k+KZccZh2wIr79Ck4BV1HPqaCvBby05wzcOuTPaQZn7369
RxqQOx2iIDJPJzgQSZqzumZntvN8xz/+UCzCoy+7YrY1C/OynnhWvb43vTU5vm5eTa3EwlOrD3My
nD8hU5Dgjqk3u+XgHDgetLguysMyMWUlgPFluaR21sA/eKBrDsQzSvb/M/oo/BQ1Fn7xSXB9nhuX
sspJqxqGWhsNRLYPHap8bo3tSI2zJCV7OQN+JkhnTRRI0fnTSJcpm4g826kYN9jmIrYPnI1jfWF2
TUQmLdXqLhvA2haw0qH6DzARiSlBXb+/RbZhswNxUyqH4v/E0JAtdAZHN3IwWkXVbayWTULmhylV
xhBlXQs21KDLjQXagsCpRpBnNmbNDwN1lyRPCh8Kkf/g8PD9u18Pfj49ePHh9bu3R1utVD4DC+SS
LX0Dmn0D5AJlBGHu+hDkXUYLiN1tKB3XyHtlRbMkQw0n6UX/UlvP23cfXp6+Ofh9q3Wos096uJP/
iw1oBSwxKRDRMPspSKyuERszoX7rfJwVrPNVBDZEAmEXHgNDYs9tQ5dVpw3M5jxFJKTbAYlvghK0
tjHh6Az3Wi3lDYqaYRtNZv4H1cxsNGSCTeZPbZxHXabZKRdKxI+W9IHHygrYnBOzH8CU2aUiU9QX
jPPmTKNCiQ/w1Q30Ns8aDo/o7Ru8qWfXmJOsm7+rSUlnO9pC+hrow7U4S9p4NtguRR2CtyYdBFFa
GAxSoKsaY3u2BgxHJe28rjR38yxxynNoyrgJ6oInppL7cgvzmrEzDwGzDLIbQiXLhCfOQJUsQ3i4
w39I6uJIK4vhQrCkzhUBVOe+PkL2NYbW/9sxrc4HYdxly3nml51bnRwXbbUxjWyfTzNamys9kciA
XdBvd3IgbRhnSkAXHHJ6LV6FYeT7V85W9ZpXGgsyZM5A7GhNpF6M1TTYgEHVTgzR5W6ZQJtv6sFW
nvvGizelsDKHJit/rVPlZh/SX0buEm91dG8FmdKgUlnLNguD6G7LIchO7W85rtRfztnKM+fsMPt3
GPyMBbNck4URqo0k3ZCK48BLcCRl0sgTaT23muSBSvFGPF8vG359Rx6eIPouHXlFzYC9/r0d7P/O
lyILUJai1XGkJh6HDvG23oUJNIFGbe9dPKcTYFS/1cI+uHXmm524ZrlNE7PEgrXLjrGdIQHbJYmN
ZOIjwBcynzJVrnRKbegicZ0sNowCv7Gdm1xXXzFPteYqroe2jNFe2hSOKLcOqpFQ37UDsY47YmG/
cdNq2cxCqT3nsQ23DfIEtgp5daNcG8M0/4RBlc44B1k8WwFbK9cPE3uPbSrG7BmUXJM6l1cnq6LZ
xbtbjrYsLkefJTOsxdz96MNPL6OXvx++fHv0+teX0YuDn3+W+iD4VRAuH10mq0fwLzbc5bq1fdwi
VAvevzQ/PxwccPc7Cji+8Sb6qMhnCQzxCLMkHkX/L3o0zucLWDEA96MbrmuiptZonM+W84xr1El1
yTwzJn9TMYoqi0iVLurUAZBLRPCWlThuZ7y5pFwhc5L/mqxaqiHBg16Z5txNfdTWDTO1JNjt6bha
L72lFSU4D6nvPbyd5Yc+BC/ScBq1SnlYKOSY0QZbVn1qsnvbRADcITf0TPUyDMOQox5rrYAz1Zu5
ebUzkqm+RDVznU4nmOVSANHH+F6qxFIqU3U3DiZhFEJy8eJczZXbBvnx2+ysub5xuaHR/uIJwi8W
N8AnTA7i+hwYd7b9YK49d38/OEVvLoBNYa007iMRO5uJJRdmMw1OofhriuNxBpJwCAlFSbISXeFI
VGA3SECppmjHEOY+2GsoCXBLI7+bnwrY073EHKomU6Jn4bfvejgsbXbZM0IVOoSqnJqz6YpDBL7j
BFwxJ+Dqzs1swG54/ClOZ/FZOsPcPqxhJU1fpvEnLkmfZCjhkQG6TGYJ8ezBNniJNIJchcRdnMQU
jyQxVXxBqOPxZto6xOl11eC5tS/s0M3fMTeewLaDbLeRHz+9XjiUe4jFwW8Mj8YqUutYtPsmXdq7
A2fGjxFBVOIrtHEQvZ4k80WOBTaba1G2cKCjij2AerEdmvDTzdDU2CWaWtR0hkSkqhw/07v5qrJl
yEscl2UyP5slLymwrvn0gl49rEtSmWkJx5OS/IDgcZEMyUbqRgyypXbPpWBIYJPPIKYDMwt7Dcig
8ewqXpUcqyDUyckkEY5YTQGSL6ammRczPLYH4LUgPoTtse/evngpCdEUSIjLybFo2jwt6Y9afT4c
NlpmoAWOUfBiVZ7CFDFoXwQoMfNZG5+Y5axVjgthilePN01jIGNQBHllcUElZk01/Q42LcMibu+R
GArfkrLQixzU0pILtIXwxBsewEg/oBsGVXaX6jAsbAsvXJvDzRAoAUD2DigYhrZ9MclYCSpxTc5t
0RUi973Nw1x7kDMMZKj0toWDbm1Zp8fRgXUJUkUmEfhLTDMsRd/OcluUELsxfrZQq4LcYy7OTbnJ
BEFJaQRDeB3rGir0Y51mrNkgKWASNPtYnRHsqGDJlbiuxK5QPSnHuh5JLBgzX7+gyanzp6ue+qVJ
nC1zi5OYR7WKiP8c1xExJ+x8abi3c0WTDQVIdhXMrRdJi5Z9yH9gF4WnpXPpAniKf9mHTH6rgZI9
YUM4jWMPlE+if6LqeDoTy97MfFgYoVfXC2yNHO6yng+OFkDqJIYzOuHJ7VoV5OCXD+9Oj35++fLX
l6cvDj68/PHd+9cvjzSLV4p/rPasTIiy9wxkbhlFs/Q4XB3rVqwWCTcrJSo9lLxGr+LIY2kPQpU3
/NQco+4woWdSDJuZ8MqIJ2GCJe51X5ENdQ5sFGhar8zTyb4wIS0tit5qQiZGEVnGS8wtgBORP9/j
okQBqaxd3K2K51ZJoQNv3sEewMOxJW9YSKjKOye0TwVumzuB4w7tJtxGzdc8K+pv8wdOtj1u2wGI
T5xYq99L4H1+Ve75hTQEjIMCGXtOHQbbhRTUVH2ToxlTBzA4jwBjTri6qJ4Zp2WZFNk9vyZBjS0F
yYV7NmOeY/Wo/r8Mqk6tIVeZ8EvRkoSDK0gLXrOIDH2QDtQwY4x/4lFElD3py//21hRP8UhCz6Q1
jil2mNH/+NnJccfOx2WCaLU/5nznkzVFRmpJfpwSMgpwtmvGqsXRaKEbm/oopQ9o+53Lw1o1FXGy
2oAVzYxT76smx8lgkk1naWhjaRUd1Q+FkRGbq4H4zwww/wTkbBkoqALRvOaay4j3UeYgYNFQfZBv
6BeDXET6njeQBavmOiuyX1rbI9gvW27lDu5moV+hiu8mp2N3jRSNSpxYV3JZPKfCMzfsHHrGKqkY
j/QYm0xk3JsaRWjDDpwMK4QwYy0ie403kN2nhq3FwkLhgWxhvWqp5NI5EM6jFAPlON8fTh1Con3z
1fVhRiijciyCPN+zMcl2Xfcy9Q+2qrNWb+aqMxp5TIc4aTiBQeB1s3lDpKXY5dnV2Lk7C2rDzm0W
tS5d2OZpyfo4M82msHmxi/N4RYcSvZxwyqQY9oI1shSA+GqX99wEjngrwSWarPvyKq3G2KoWd6gg
WV9T7gTUZZK4z+gBvCiSlcmAczQB2BXXIGkN/efnpAiSOdcz91MHLoxZpxFk3FF0LS1Aqvzigqzn
jaIEP4NedBzBJCtpFrGrSlPUhZzE0NaYD85oGJnCJUaXGm6Ka/KLPQ45z8jh6d7tfvShWCa9bYN2
kI4E9SiF0vJWtc7JqRJZn5FzU+fDX3JLU273Hbdk5NAr2d/2hqtwDf26/lbJGkbNJW5q2CUfrolQ
dF3kpy2G+H7VNsj3pgz3FsMcVG3DHKzpNOqXRhwGZRQRvH9ghbd0D9F7qdc6+GOn+mvpltDVun7a
mG2vNaaJXuUIJCkKi2j97tzrj8EKk8E6wGJWalKt3UFmLJKPEdSmcSYeFLLMWRJCubyF0Yx0dhph
J8YG03wA5pJwJSrPJCGFbKmKDNeiEkGYFSqemusDqa5yVhbKgZpHHBOaGzutlUjkliFr1k5iDSNK
5abE5Cl8AdArmZ/B8/u2SL00xdPp8Qw0GLNFhenu+Z4RlLX7lrAZcXrbWE2fXoYVbL0GHIHxg2MV
3lI1ljrWtlShr33PLWsbOsf6ew1lqsIB3GKpfY9NjNw/wrFqFaukWrWrBjMOcUMO7jiYX3KzSQKB
uMIebGjo4kJ/bAQosIVHjKCJGIzJdE7PAhZmynyJWuNcUyIAPN7EWXxBy7LOUExyKE2/xsd+O8VS
A2RkMmllZEgEoNB4ZSUYtzVEsJW2Zi6WPufY2aCOLpoPfQGGM8KDKrhWIi6TqvQKL3XUPMIvqr5A
1nqCKGn5TcMkBfpOOMimtEUUZQQV5mEvL/JE0sBiPia/2Hw8IZrzzbNn/1llSBPIO3BNtxoxHM8w
2W+y2Xy7g+gXEkpHbZhwjNP1cPDt+c1/pswcmiz2V6TUFqIZnHvvpQh1muRpcmPGJFUmAGhwvhcm
U89+1FQl0K/TB9vlf9kaZwWH48rUDibCKQjXccpsbSxTAwBFoidJoTKUedsXQ7UshFjgTEYLZfjQ
AafVc8dqwoKtCtoblTfuQo/dRgU0pe7EY4v8UvNWpXWFOG5LTSBntT9BTBRguU60Lo6LjoVgKCaX
Am25xxfHNlOf7WkXpK/zERC5KYPK1gDAF+LFgP3t2Oz2C7/mOV2H8XmNdITRv0TPThQLcAr3osTZ
6qEwc9DdsCgwq3FDad7FdrRMCJv2RMb4cMGgiPrBb6/0kMDGag8uo2ctMgqkv5jKIoJA0m6J3GRC
sPEvnJ2QcTfRQkZRKUDO35cYSI0TMLjI2WUnd7mdaQOhBh0U21P9yAe7OSMXqPIL7Lzdj07l1Q/J
56or8OGPdmK1Wu+LPKdtP7nXJoQHp36UOClmtI145mx4sKxSDp1lnVsYIHSd3hpOepYWeTMAVKP0
6LOkukqkS5A0Lj+f5Xkh3Z3TmYYcm+ZQj21TNOXKxOEzNNgTZS7rADwUX+tj/jqSSqc3eazZihjU
U2JCpOR2MpFZSmokVpoyjoR8Eq/qAT9SSIdl61KjdKkPkvyWTw8aXFe0HvT4DdKSnZrWE1V3azml
5tfLX17HHPI1qsO5nHP5RBa7pc+S1AZiI8gVNmKKqamc3136sRll2FRe52qK2aBIS7Q+rdEafnmN
ZQhm+fjScBYlz1fxDPdwgYaIsxUFdmh9I3VZg4ABCgU6/7tYky/6L71B9NL49h+7yK41gxAaxMom
QqyP7s3JSC1YLv741xksqcIt72wykHa+J1+SegVlALYhkWPe7VHQV0jEa5uC1TsaYoCNREAtUv8I
bYCAdgNJ3sLeYdGaagsQr+j+p+thf/Ds/MZIlXFkLsmqert8hJir+gr6+NPyV/oTldw8wwDr/vbD
1Q/ohE1WZw63tgd+PPxuq7Stf4q6nY6mmtZG6UX/YxR9t90kOZyciRds6xxOyexay+D70Xc9S0iJ
dX0iWIqLy8SyQieARHaBi/W7EhX7Eil4jwd5w2MMsBm42hF8T1I55Qjf4AW6jM0Eym4gA8HamvMS
DdOjdzfimv/BjYjWNj9iw/RX+8Rc5bKxXIopZzHPknmeoTIPFAovUDmYmfbAZAKjJ4XWlWRCZdgW
MCKG5FotUrrgWRVKZFZiM5HmEGiT6aZyIVSh7lVeODF07vGp7i5TcF2YCmReFaGQjfh3aSK16kFO
W7zW19/IloXD6FZ6ozwWWu1UblQTEQUxcTCMGM9gVY2fpmcnb8VcduJ2UaI73684hZZG5rM/Zdb1
S2bscvK6hhRt+EbPmz3nHJFQwC4diUeQ/iyoUphoLCpMabploRVeAwxAXPghv8owfp36epfsPyZ5
hyftmScl8nFSkwf8fi9eO1evDoisVMJBDEsSq1a4BX0+GI0HdIVepgS1ikS/TVdW8LZ94nMtEGeF
UVXqCuoXi2pKasvTWzOBxqDJ/Lsdbd9KEqGol3YMVnW02YkFLZJ1QOudTDjsSbpOBtoNiWiAosRv
UVOHs33NOxlxifZ2nmsZqmxNz8RRMqGnbVOa72+cLG4nOYLSDOpmii2Egntm/dsy+M2sWzfH59ge
X7b7h+x4I6d1tpsYrJ5IS9QWHopbF8Avh2SzJwzeWMAOGjZqjUuxmSDum5gNLKylYC3FDncpRiDA
smuDwlt2HlzTanBvfVDAyPR8TM7PE4rx7DrNF5sTFOwRbQyZq1ddpe2PPf+BLRwAOz8UW9bhmx+j
l0c/Ml+pTPIO24lNdILoO+RZpd7SzzWeIRPqz3HkFJWWuYfa0vOyHqciR2myCdUc6rU+2ihESQcj
W67Tb96k1h9HdSi1+iqVx98+Y7YzoQj8iKolW8rim3tsbd3NzWCDQPPWOF9z1h8/BlHpHz9yunIt
h3roUH91JNnANncz+l6kOLmsyHZIoVvIygjDYdlaz6Nv3AOterEtg4f9vTOs5l1O0XTl1pSSOmp7
Ug/CveWUSSithcwp0kRmT+6irLWW0Cco8dpa9Nepp3KWyODK+2wmIryQLIBCenWueFZXmYlXucA2
ZFXuOklKJ9cCu4SQa05C9hULDVbcNYb6H6HHDxR6XKuf19D4Z8+pthMUz1tfeGdNrZjbVHBtL8zS
26u1dlwbp7C3ZQ0ePyd8g4P33h23gWu2uXPQln7T3V2Vzd7IfxjRH86Ifk/m4obOpL17tYh6Sk3d
nmVUZYyFbLNluSVFPWtW4lRbfLWczdotWkx5ktmsGyKKVHAwNZm/MkPXnUxZG+S1QZEsZjFIWx1f
RRRfJIAiSrjxZeJZk0HA0Nx62bSf47NG4uje7jbvs1NPZ00lu3UM4ip2q5nUqqlKpZGwut3Gc3CL
r4DCWC/Gkzp9qzwFogW7w5ldgZBFZyjM0t0sRXJJ8+mGeT7UHMi5wizj+KT3BTKAwpQfzfsrGwAA
qAAogRXQmabyuDwhtUfU35Y736+wGG7XqX0iqFeTPTCcdM+tZyb1+0PQ65ubtNtUhMv+aYl9hwUY
2AB4xBVmjjF5HPaDs2XqRXmJeYfDvEXbRzjONIlBU9xyHC2YNIyaqihJOGRkqqbXH8NwxzBq0hTj
oYH9Ckamsk6n115aqqHLS7h+uUtzavwCTezOX4hnb7komz+M1Ay9w/AzrMaMYchMf/jPE+8BVNDg
ievLYQ2Of3795vWHo+PLE4s68PH8nBpnTFnEAVTNMBSoiLl46l0K+3VA10v/tkwnnZ4TZNs5Ty+W
hRNN7VYBpTtdQTg2uJS+/O5EsnpkZRgdFw46mIBwoTPOJj22Zm7WCjGFL9AvUXk9Ww1FlsJ9puDy
gnuEZW4RDZIYnGIspbVs8pt2vvR+w7rpHLv1lVHG/fG6wtxFr77SLQLc0eBci22ni7cOa38VzzBR
xo8s54tfeXC4I3r+R44E3xzJvWFdh06xkTbO6GZNWj+mk7wDMowxd5HrseOU7Vfn3G3dhS4buoO7
sMntqVODzapPrBf9XzftsGHmUs+tydvoFfBlIbHR3yiGeUnKNF17/Mr/XtseR4NWh648Qc8vvLde
Tzp+fetzv8yHTkET1qgotBkvLn+Ce116p2cbfI/q7Ya4onbvli7UQ/kg2e3/4US9Fyfqpv3j/SJX
lG04Sr1ewpry1rfBNufYwri60sn4bNNQWKrPzXZSyHAx2So+S8yKQYyWse7OkvPKgSDnq11ekfh7
jN6ML+xmuTG9iDoveGx0+YmyNpHz38IUYzN0jFcR50IC4zdOg0El4A2dR5+7DlRZH27X9Y31kWbW
LWqtP3CRPNEYlfRfTtYmplELZyQ5ODnyiJJv7PTw8MPvEYu2s5Tlq6efsskAAf/zfCbldvbz83O0
L+XjJdUxWQBwKkDOZ96fnb3T//P6MBzy39NFZ9vSjOwBkZpP/EdDZRgpGrRVLacPjgOBScLLz+Nk
ZusAHbz9gS4f5ldJccggifq2Hz0QVTkQxKlkOIl7AiTBahAdiBdpwgzSrUYkhRvfi0On+9233/YE
xbLcuDFMfYE+Gg01dH4Wjy9Lk/3bN4hjcTdz676wfcNmM/mD2lJCEhhrAp7J42WDPKkBZHtpIcl+
+uW1tP8uURIoxUdVVUiiyD6UFLhBRbKfZFRhAdbe4G/ryf5gWZlfXv+Ac4bvVJKEDjs4x7oI5Cqi
bDPSCp7zbyLoBZazzYxgwo6mTEyqzoMmoTXNuPWjZuHgjmicA9dLkv3RyrJRNw7K7Ywpy0CJnMwA
iUaNmPae72kuKCfLFTEaw2UOgBYYBDKOl5jeHJ3BVErkYHEmDkA8e+qFy3utbIkWSzUlZhhUgqM/
ouERy3kRj9gRzZNx3Kx3KcW5K5NCd05Q9csjUW5tM/bxBIUUD3XXfcjAaIh+cGbUTSahSgUaOewM
RkkK5D3V0wogqi9MHolsWctgdEaCI9LiqAo/gtn0AiLNOfv0zzk2iBOcBk6bDV7Ta5Hp6I/3Mtgg
S64OzQNdS9EFoFba0aNxv0f0/33nEyP7046FdTmSzNRjJO/UKRPabusB6wTcKQHoNYxxijd2GQgP
gGIbR0LuX8kFHcQuQl1WI0qLdrw4tZpgbq1GjPsemc8cD/eRS3cGn2fl507vxFvQW54HvfJPwGcX
i+pzxzn+95ycStKlWYvgKBBGF6CoXV4RZ2UclJusHTqTpq67TK/Ac1iw2tVNKQkHjW99FM03m4R2
w+C+AkvfbN/mT+A+joh/mi0d6Q+3KHY2Ia96mg++X1VJ+fpd1y2kyjy0LxTsfIbLHPIGcxYuC/Ta
aBBIaSk4B2gMhNJ2cMTQQLiNCxj8n3SB0NXlr8OeXcHGmZuvD0+PPrx7//KHHqXeFeMpHK+vRsnF
ASX/Yl1Qsy+6Vb31z+tO8ObU6jm+T8pFnpVJt1ZKB4Ye8bTx6D/Fs2US1ioH+TiNTzGGaIRSWD9Q
ANGKXI6uUeDF0fZ/SEvl+p3m4jXYlQIrkCBcPDcgMHoEMjFs2iMjniK+tAFf5/d9JWn7rydsRxc4
dyyPf3eVyP8/UEsBAhQDFAAAAAgAAAAhANLB5J6KYAAA00kBAAwAAAAAAAAAAAAAAIABAAAAAHBh
dGNoZXMuanNvblBLAQIUAxQAAAAIAAAAIQBoKJ/UMV4AAGCxAQAQAAAAAAAAAAAAAACAAbRgAABy
b3V0ZXIvYmxvY2sudHh0UEsFBgAAAAACAAIAeAAAABO/AAAAAA==
"""

if __name__ == '__main__':
    sys.exit(main())
