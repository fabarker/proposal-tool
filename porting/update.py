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
BASE = json.loads(r'''{"package/__init__.py": "d13f9cc8dde33a81a4df3f863ab9680bcae7d21a86d02a4e87c2b7490a666e1f", "package/accountRequests.py": "6a2206a8e4a78b5048fbd5db6ca38e0b6a1cf5cb960425bfe75dddba4b979b7a", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "dbe73f522b121de5e5b5450ef8615c918a7577805a95e83769732a0e45252755", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/customAccess.py": "44dab81cf3574c2f9875f77de3c85f14cdd05e9ac1809e407f29fd61c120d3d1", "package/customPortfolio.py": "f5031bf9e3af86bc0ac55e6d9cbb3a557ad4b0f3086c0446c5a7fbbe1f083c71", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/entities.py": "32fdfcbfe042664eb04a790dd35387b3aeb799702ec794a5eb2a63b70e1bfa7c", "package/feeCard.py": "343c6363c39679810fca4f1498ec3dbf8cbd76361ceccafb5d04723616a634d9", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "75654c95ec9ef8770517393df78a4c0a5e806dc23378ccae2a1c74ed421edd95", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "75e173df01b41481358257ab4e7f05f25f1b177006658ee1cdfaae61db974144", "package/fixturesAdapter.py": "ec7501805601226357435c297d2001d7c9216c45b2610f98e108c058fd070a94", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "7e0d39c670eb6683d75dd9e27e2927246b629a2d97725f9bbed4a6d0ce8f798e", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/houseTemplate.py": "52ea6c0c0d680bef6aa40a8fb99ba298a7925f43d9510e612ea016c69e043a0e", "package/mandateCodes.py": "146976726d8ea2efe37ad748f0abf3b795badbb2885709b5b332b44963039eb9", "package/marketing.py": "6731d20f22b044de32892585cdf50a72b205f445251c327525d32a3bb9c525fe", "package/notices.py": "240dbbad53a5a3ed174ab6e8b6afad411d686f5d4f36db35ec0cb4ac43d05d90", "package/otherCharges.py": "2559db2093b8240638998f250e9c805461719319b9a2bcf4104878d6b7e81914", "package/overlayRules.py": "28f7f58859f55ab8a4032b6ef4ef05bf1eb4f48b263697298694bc10bd4abd69", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolioBenchmarks.py": "ebe9397b17e439914f706fad1e0a59fb7481f429f39c09e1584993eb24d295cb", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "06060d774220c29cc65b2c6917b7ac3cbb37c303fbe50142473a26753fe44d0c", "package/privateMarkets.py": "8704c0c9cc233e0df6105b93de3286d52a95bf72f0a20450799a6a8348d69225", "package/products.py": "7752ce8b69b71d611174a85039391024f197b3988b4f2728de10ca5521d0b666", "package/proposalRegister.py": "44cbd0f49dacc4c5e5a9a84ddc355d4b98c98460d40e51c73e492da6718064fa", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/repositoryFeed.py": "d4241dc9136c98d6216be6d0c4ace45062ed97a1937080ea63d9b03a159bd558", "package/restrictions.py": "0d73b8ea7866bbf84416a65b10e915d3a4a0aed88bc14f8209d1d224a066533f", "package/rules.py": "d058a6fc87c23734be22e38a9b9f71207a7347499bb795d5a42828cad391d1ef", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "92b3918327dd9c93b8e8d8d95583d4e059dacaa19990f2683c14271492ee6b13", "package/scenarioStore.py": "225f02e3be6d554c4e5069c9fb287b53c4b5da9e08036124b8855a79719d706b", "package/sheetDoc.py": "65f27fbda0ce94328addb4eed491f78f2101a0ea02fe7338b4f8c9c11efa659b", "package/sleeveRepo.py": "f317568e3f75976364eba8d1da10832234c19e578f19c85640054407554db881", "package/sleeveRules.py": "087f7ad77fba37a0a1d0955a9078e50b92d97f4b3d3227dd0d5a4558b8425f2f", "package/sleeveTools.py": "f34beced7034b22a736f34dce335e8db74054499cf1aa4ee244ef0add8277ad3", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "4a00b650ec251bc37f8dd75870e7bda94a1c6cb726027cf12c0f3b1a305f34e5", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "695ef48afa00c37dd8cf5685b19fc82c663cc730c37451ca088caede53871d57", "page/proposalTool.html": "07834491e82f22aa6394f3265772782ebebdda08a8858ca28625af4420f4a397", "page/static/css/proposalTool.css": "09e6efb5c18e8bf1199aa3d53513861a57dd42f80cc4a3a0ba798e794c384bea", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "6cca44b52f90fe66f6366d35996ec40aa5d3b3c181f1a6440c1c3ed9d15432ce"}''')
TARGET = json.loads(r'''{"package/privateMarkets.py": "640e1397105e083812943eae67124e50915f6b74e5f5b95999690b9b75db2c01", "page/static/css/proposalTool.css": "4a2a284914e78c96428442f76c64295deae950e8a2a080b17d3b678930256684", "page/static/js/proposalTool.js": "aabd05f39b6de06dda42c1b2594ef82fcb04055dbca9a4d590cfa89de1953c88"}''')
WRITE = json.loads(r'''["package/privateMarkets.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "3278200689dcb6a4", "changed": true, "target": "8673a24e0c8b5047"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "_otherCharges": ["from", "cyrus_pmg.pmgService.scenario", "otherCharges", "_otherCharges"], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "customAccess": ["from", "cyrus_pmg.pmgService.scenario", "customAccess", null], "customPortfolio": ["from", "cyrus_pmg.pmgService.scenario", "customPortfolio", null], "customSleeves": ["from", "cyrus_pmg.pmgService.scenario", "customSleeves", null], "entities": ["from", "cyrus_pmg.pmgService.scenario", "entities", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "feeCard": ["from", "cyrus_pmg.pmgService.scenario", "feeCard", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "mandateCodes": ["from", "cyrus_pmg.pmgService.scenario", "mandateCodes", null], "notices": ["from", "cyrus_pmg.pmgService.scenario", "notices", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "portfolioBenchmarks": ["from", "cyrus_pmg.pmgService.scenario", "portfolioBenchmarks", null], "privateMarkets": ["from", "cyrus_pmg.pmgService.scenario", "privateMarkets", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "repositoryFeed": ["from", "cyrus_pmg.pmgService.scenario", "repositoryFeed", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "restrictions": ["from", "cyrus_pmg.pmgService.scenario", "restrictions", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('95')
#: what this update carries, and a short id from the files it writes, so each
#: run says which update it is
TITLE = json.loads(r'''"Private Market Minimums: two tabs, Alternatives Portfolio Services (APS) and Private Equity Managers (PEM)"''')
UPDATE_ID = hashlib.sha256(json.dumps(TARGET, sort_keys=True).encode('ascii')).hexdigest()[:8]
PAYLOAD = """
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
"""

if __name__ == '__main__':
    sys.exit(main())
