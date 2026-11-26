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
BASE = json.loads(r'''{"package/__init__.py": "d13f9cc8dde33a81a4df3f863ab9680bcae7d21a86d02a4e87c2b7490a666e1f", "package/accountRequests.py": "6a2206a8e4a78b5048fbd5db6ca38e0b6a1cf5cb960425bfe75dddba4b979b7a", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "dbe73f522b121de5e5b5450ef8615c918a7577805a95e83769732a0e45252755", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/customAccess.py": "44dab81cf3574c2f9875f77de3c85f14cdd05e9ac1809e407f29fd61c120d3d1", "package/customPortfolio.py": "f5031bf9e3af86bc0ac55e6d9cbb3a557ad4b0f3086c0446c5a7fbbe1f083c71", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/entities.py": "32fdfcbfe042664eb04a790dd35387b3aeb799702ec794a5eb2a63b70e1bfa7c", "package/feeCard.py": "343c6363c39679810fca4f1498ec3dbf8cbd76361ceccafb5d04723616a634d9", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "75654c95ec9ef8770517393df78a4c0a5e806dc23378ccae2a1c74ed421edd95", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "75e173df01b41481358257ab4e7f05f25f1b177006658ee1cdfaae61db974144", "package/fixturesAdapter.py": "ec7501805601226357435c297d2001d7c9216c45b2610f98e108c058fd070a94", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "7e0d39c670eb6683d75dd9e27e2927246b629a2d97725f9bbed4a6d0ce8f798e", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/houseTemplate.py": "52ea6c0c0d680bef6aa40a8fb99ba298a7925f43d9510e612ea016c69e043a0e", "package/mandateCodes.py": "146976726d8ea2efe37ad748f0abf3b795badbb2885709b5b332b44963039eb9", "package/marketing.py": "6731d20f22b044de32892585cdf50a72b205f445251c327525d32a3bb9c525fe", "package/notices.py": "240dbbad53a5a3ed174ab6e8b6afad411d686f5d4f36db35ec0cb4ac43d05d90", "package/otherCharges.py": "2559db2093b8240638998f250e9c805461719319b9a2bcf4104878d6b7e81914", "package/overlayRules.py": "28f7f58859f55ab8a4032b6ef4ef05bf1eb4f48b263697298694bc10bd4abd69", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolioBenchmarks.py": "ebe9397b17e439914f706fad1e0a59fb7481f429f39c09e1584993eb24d295cb", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "06060d774220c29cc65b2c6917b7ac3cbb37c303fbe50142473a26753fe44d0c", "package/privateMarkets.py": "640e1397105e083812943eae67124e50915f6b74e5f5b95999690b9b75db2c01", "package/products.py": "7752ce8b69b71d611174a85039391024f197b3988b4f2728de10ca5521d0b666", "package/proposalRegister.py": "44cbd0f49dacc4c5e5a9a84ddc355d4b98c98460d40e51c73e492da6718064fa", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/repositoryFeed.py": "d4241dc9136c98d6216be6d0c4ace45062ed97a1937080ea63d9b03a159bd558", "package/restrictions.py": "0d73b8ea7866bbf84416a65b10e915d3a4a0aed88bc14f8209d1d224a066533f", "package/rules.py": "d058a6fc87c23734be22e38a9b9f71207a7347499bb795d5a42828cad391d1ef", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "92b3918327dd9c93b8e8d8d95583d4e059dacaa19990f2683c14271492ee6b13", "package/scenarioStore.py": "225f02e3be6d554c4e5069c9fb287b53c4b5da9e08036124b8855a79719d706b", "package/sheetDoc.py": "65f27fbda0ce94328addb4eed491f78f2101a0ea02fe7338b4f8c9c11efa659b", "package/sleeveRepo.py": "f317568e3f75976364eba8d1da10832234c19e578f19c85640054407554db881", "package/sleeveRules.py": "087f7ad77fba37a0a1d0955a9078e50b92d97f4b3d3227dd0d5a4558b8425f2f", "package/sleeveTools.py": "f34beced7034b22a736f34dce335e8db74054499cf1aa4ee244ef0add8277ad3", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "4a00b650ec251bc37f8dd75870e7bda94a1c6cb726027cf12c0f3b1a305f34e5", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "695ef48afa00c37dd8cf5685b19fc82c663cc730c37451ca088caede53871d57", "page/proposalTool.html": "07834491e82f22aa6394f3265772782ebebdda08a8858ca28625af4420f4a397", "page/static/css/proposalTool.css": "4a2a284914e78c96428442f76c64295deae950e8a2a080b17d3b678930256684", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "aabd05f39b6de06dda42c1b2594ef82fcb04055dbca9a4d590cfa89de1953c88"}''')
TARGET = json.loads(r'''{"package/products.py": "b1cedc110f0979eb3d51897a20e9e17e824dd80468c9640eb92dccaf65875940", "package/proposalRegister.py": "234258512b83b96ac40cf7e5c7fafa11bcb246f04d7a21d817ac49fdc01e7acc", "package/sheetDoc.py": "56f540e13394c2d43d371c329d17e781852f199a85bb7301ece669fb6676cb0b", "package/sleeveRepo.py": "6794682dd3f9bd81c1dad0cbbbc3916c7af4a9dad7fb56be9c49f66003b31a90", "package/workbook.py": "66fbcf087b58e04eea82965181a3d25063d638fe9d69cf4919a97db6fa7accef", "page/static/css/proposalTool.css": "8c54a42d7f71a48c66f6e4e11e726545bcf874af4327f6b3e7c6aaebc4539981", "page/static/js/proposalTool.js": "35283f3cc8aa563159828285233d6b19f44fa6a7b213c8ce8e9dd6a9c19cefd6"}''')
WRITE = json.loads(r'''["package/products.py", "package/proposalRegister.py", "package/sheetDoc.py", "package/sleeveRepo.py", "package/workbook.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "8673a24e0c8b5047", "changed": true, "target": "e93e94ffc564335c"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "_otherCharges": ["from", "cyrus_pmg.pmgService.scenario", "otherCharges", "_otherCharges"], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "customAccess": ["from", "cyrus_pmg.pmgService.scenario", "customAccess", null], "customPortfolio": ["from", "cyrus_pmg.pmgService.scenario", "customPortfolio", null], "customSleeves": ["from", "cyrus_pmg.pmgService.scenario", "customSleeves", null], "entities": ["from", "cyrus_pmg.pmgService.scenario", "entities", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "feeCard": ["from", "cyrus_pmg.pmgService.scenario", "feeCard", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "mandateCodes": ["from", "cyrus_pmg.pmgService.scenario", "mandateCodes", null], "notices": ["from", "cyrus_pmg.pmgService.scenario", "notices", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "portfolioBenchmarks": ["from", "cyrus_pmg.pmgService.scenario", "portfolioBenchmarks", null], "privateMarkets": ["from", "cyrus_pmg.pmgService.scenario", "privateMarkets", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "repositoryFeed": ["from", "cyrus_pmg.pmgService.scenario", "repositoryFeed", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "restrictions": ["from", "cyrus_pmg.pmgService.scenario", "restrictions", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('96')
#: what this update carries, and a short id from the files it writes, so each
#: run says which update it is
TITLE = json.loads(r'''"The product catalogue in three levels with Strategy, UCITS compliant, CUSIP and SEDOL; the ISIN, Income, Share class and UK reporting on proposals; Export to Excel on the sleeves table"''')
UPDATE_ID = hashlib.sha256(json.dumps(TARGET, sort_keys=True).encode('ascii')).hexdigest()[:8]
PAYLOAD = """
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
ay50eHRQSwUGAAAAAAIAAgB4AAAAmv0AAAAA
"""

if __name__ == '__main__':
    sys.exit(main())
