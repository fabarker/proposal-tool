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
BASE = json.loads(r'''{"package/__init__.py": "d13f9cc8dde33a81a4df3f863ab9680bcae7d21a86d02a4e87c2b7490a666e1f", "package/accountRequests.py": "0d0352931d5462a5e0fa64c0e2422f712c413d224b833801b3f366ec4a65cf52", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "dbe73f522b121de5e5b5450ef8615c918a7577805a95e83769732a0e45252755", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/customAccess.py": "44dab81cf3574c2f9875f77de3c85f14cdd05e9ac1809e407f29fd61c120d3d1", "package/customPortfolio.py": "e05f32e3c0c6f7a129d128752cdd3602596dcdcff0c1f0ba4289ebd22ed9d76a", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/entities.py": "32fdfcbfe042664eb04a790dd35387b3aeb799702ec794a5eb2a63b70e1bfa7c", "package/feeCard.py": "343c6363c39679810fca4f1498ec3dbf8cbd76361ceccafb5d04723616a634d9", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "75654c95ec9ef8770517393df78a4c0a5e806dc23378ccae2a1c74ed421edd95", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "75e173df01b41481358257ab4e7f05f25f1b177006658ee1cdfaae61db974144", "package/fixturesAdapter.py": "ec7501805601226357435c297d2001d7c9216c45b2610f98e108c058fd070a94", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "7e0d39c670eb6683d75dd9e27e2927246b629a2d97725f9bbed4a6d0ce8f798e", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/houseTemplate.py": "52ea6c0c0d680bef6aa40a8fb99ba298a7925f43d9510e612ea016c69e043a0e", "package/marketing.py": "5074896c10a45158850e673b6c71f3eb92c29cc766b4b85b15d590e87034946a", "package/otherCharges.py": "2559db2093b8240638998f250e9c805461719319b9a2bcf4104878d6b7e81914", "package/overlayRules.py": "28f7f58859f55ab8a4032b6ef4ef05bf1eb4f48b263697298694bc10bd4abd69", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "06060d774220c29cc65b2c6917b7ac3cbb37c303fbe50142473a26753fe44d0c", "package/products.py": "116e2bb02de7c1bb46abe1dc940923f68eb0d458bbc0eab0d865946886dec242", "package/proposalRegister.py": "d7629c476a795b1ec3be69c87403e2a2819fcfa5239be5bdac68a87d92d99d96", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/repositoryFeed.py": "1bb562fa5017f4d982faaa2407cc35eb6c91a3bc247f65581de0b857ee367d1e", "package/rules.py": "b7dcb5d493fa03077a53996f62955660037ab6ccbe5abf38c5938ac8f9e60d27", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "92b3918327dd9c93b8e8d8d95583d4e059dacaa19990f2683c14271492ee6b13", "package/scenarioStore.py": "68c57af4743a3743bb9bcdcc8697c49da9b61964da552fb4268cf5a7ad007c0b", "package/sheetDoc.py": "9417ad786e4223e83ef4caee021fbed42cb83d2319d65806f918c6421e31a18c", "package/sleeveRepo.py": "9f8fe2dcdf98b4694f97e5b5b4ef5a4215a9203e3b191523395da09c9767b5b6", "package/sleeveRules.py": "087f7ad77fba37a0a1d0955a9078e50b92d97f4b3d3227dd0d5a4558b8425f2f", "package/sleeveTools.py": "5eed74f201d2ef949d34643160d45d1bad035c7b7681b8c566245e932fa2ab9f", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "4a00b650ec251bc37f8dd75870e7bda94a1c6cb726027cf12c0f3b1a305f34e5", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "124f17ccd237fff98e775c59a1966c0f0f7942feb5c2bff11a493d441e43b472", "page/proposalTool.html": "77dab8c6fc304d9f27c7ce8a571d4f7dab11aebaff9e066292bfe30c29b1a2cc", "page/static/css/proposalTool.css": "c36bf01bc9cddd4640d0850f3498c178ff70b62c6786effae144352c82462896", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "2d3617e171a0dfab79a695dd32bd278966ec7ad79a0240ee989e68bea6899e23"}''')
TARGET = json.loads(r'''{"package/products.py": "8c9f23dbb83db222fe18c66184355f5ad77687cd0f3c0d53fbfd054efa249114", "package/sleeveRepo.py": "88e8794efb908e0e5cc4a769c4c2ceaabdeda52e7a4d9f71a10d64d58ac71dd4", "package/sleeveTools.py": "081b13d2f19fc12113a4f637badaf40fdd9c821bbc51fee61b91230813c0adba", "page/static/css/proposalTool.css": "c3456ff5c237ff5b09cdaf8ed2d0a7a892ace92ba2797b74fffad199d17fba09", "page/static/js/proposalTool.js": "2fbbf57bfb33102f767ba0b78d3bce2d339af1b498752638936821d614813efe"}''')
WRITE = json.loads(r'''["package/products.py", "package/sleeveRepo.py", "package/sleeveTools.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "7e51c483b4809fc1", "changed": true, "target": "8dcc3a81562cd128"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "_otherCharges": ["from", "cyrus_pmg.pmgService.scenario", "otherCharges", "_otherCharges"], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "customAccess": ["from", "cyrus_pmg.pmgService.scenario", "customAccess", null], "customPortfolio": ["from", "cyrus_pmg.pmgService.scenario", "customPortfolio", null], "customSleeves": ["from", "cyrus_pmg.pmgService.scenario", "customSleeves", null], "entities": ["from", "cyrus_pmg.pmgService.scenario", "entities", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "feeCard": ["from", "cyrus_pmg.pmgService.scenario", "feeCard", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "marketing": ["from", "cyrus_pmg.pmgService.scenario", "marketing", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "repositoryFeed": ["from", "cyrus_pmg.pmgService.scenario", "repositoryFeed", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('72')
#: what this update carries, and a short id from the files it writes, so each
#: run says which update it is
TITLE = json.loads(r'''"SMA mandates in the catalogue; the custodian's card after reaching the implementation step, and the custodian in the rail's Mandate section"''')
UPDATE_ID = hashlib.sha256(json.dumps(TARGET, sort_keys=True).encode('ascii')).hexdigest()[:8]
PAYLOAD = """
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
AAIAAgB4AAAAW5cAAAAA
"""

if __name__ == '__main__':
    sys.exit(main())
