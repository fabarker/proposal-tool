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
BASE = json.loads(r'''{"package/__init__.py": "d13f9cc8dde33a81a4df3f863ab9680bcae7d21a86d02a4e87c2b7490a666e1f", "package/accountRequests.py": "0d0352931d5462a5e0fa64c0e2422f712c413d224b833801b3f366ec4a65cf52", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "9dde28f78d2b4e5e60572155990986c622186fb467aede6d62f354c6066105e7", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/customAccess.py": "44dab81cf3574c2f9875f77de3c85f14cdd05e9ac1809e407f29fd61c120d3d1", "package/customPortfolio.py": "e05f32e3c0c6f7a129d128752cdd3602596dcdcff0c1f0ba4289ebd22ed9d76a", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/feeCard.py": "648355dd42004822205595edc35fceb0bd19f1fa4ec6550bed58d2d799d76860", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "75654c95ec9ef8770517393df78a4c0a5e806dc23378ccae2a1c74ed421edd95", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "1c073b5a4434ab15689495d9ac6310baa8412cb6cc71363cb0a73f279c5b0ca2", "package/fixturesAdapter.py": "561b0eaea5a41d7301b4810de8c5a934c619758c1955f90e89efe035cd32b317", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "7e0d39c670eb6683d75dd9e27e2927246b629a2d97725f9bbed4a6d0ce8f798e", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/houseTemplate.py": "52ea6c0c0d680bef6aa40a8fb99ba298a7925f43d9510e612ea016c69e043a0e", "package/otherCharges.py": "2559db2093b8240638998f250e9c805461719319b9a2bcf4104878d6b7e81914", "package/overlayRules.py": "f20fbc114c366dcc5b1e6aab6f74f94ebb218aa8e168a81d9783540ad437ad24", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "06060d774220c29cc65b2c6917b7ac3cbb37c303fbe50142473a26753fe44d0c", "package/products.py": "b971fcc5306123aad21c40ae040bc68d4dead45b43d89ce1b3dcc63a7e1dd478", "package/proposalRegister.py": "363eef64bb675df8adefe386b8af46e625ae4fd155c944877d6a7d0cfb3638cd", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/repositoryFeed.py": "1bb562fa5017f4d982faaa2407cc35eb6c91a3bc247f65581de0b857ee367d1e", "package/rules.py": "d64b78029080ab974f4d66c8568080b32869833481fe65e3f2a9f63308fc70f2", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "529416459004d3a0804663e1cb58913deea8f40e815a68366b02b38b4d3dab38", "package/scenarioStore.py": "a51dc4bf5494c5ec0572062caaaf5821c3488db5d54b3429246be3f53292cfac", "package/sheetDoc.py": "9417ad786e4223e83ef4caee021fbed42cb83d2319d65806f918c6421e31a18c", "package/sleeveRepo.py": "ccec707af12f3d02d2c4e9f62bf3e4f9984dfddd9717d80b80256677939f024d", "package/sleeveRules.py": "7fa0ae37696ac0060c141faf05f1b5fb29067ee30c8024599384b6d4e0bbf253", "package/sleeveTools.py": "fc5a594fef48d540dee2d22d1b46803e0c611b7e660fd82b1f06e51141ea1a31", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "a3550c15086820986e7e0354e9a5997dbd23bbd2bb8c4d0615e7cc461c41f8ea", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "cc966f670425ba889fa7f2b3bb71cdef3af8c5dca6bdc68270b52de9b3760cfa", "page/proposalTool.html": "372a0389afbd4e586be216a22bd77feffd5f9fc45c18ed351476ef1ec7bd4168", "page/static/css/proposalTool.css": "babad82918c4fa6d60addad6cdec6b295e732187d50f2762c1d3013fe7b7ed7e", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "4c125be2db8db51912f46946cda5d74364bc0190e1343ea81285f4ea08afa21b"}''')
TARGET = json.loads(r'''{"package/feeCard.py": "343c6363c39679810fca4f1498ec3dbf8cbd76361ceccafb5d04723616a634d9", "package/fees.py": "75e173df01b41481358257ab4e7f05f25f1b177006658ee1cdfaae61db974144", "package/rules.py": "85d52a58b21a59cabf85b4e48dc276ee4fcfca804f48dccf02919d53217b020d", "package/workbook.py": "edf00654f568b38c86744c3191e63687abbfde137d6e8de491c75bde72a398de", "page/static/css/proposalTool.css": "e30ccf1e3c97ea146aa0fbfd47070572c422daa071c3c05c84d7aed2b6694ff5", "page/static/js/proposalTool.js": "6ec9a9ad729ef980417176b0af1b7ee0ff1b4413a9c1ca7f25cbdbda2e118459"}''')
WRITE = json.loads(r'''["package/feeCard.py", "package/fees.py", "package/rules.py", "package/workbook.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "640f363dcec697d7", "changed": true, "target": "c59a9b965e6601e3"}''')
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
