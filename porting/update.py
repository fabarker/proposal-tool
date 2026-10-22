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
BASE = json.loads(r'''{"package/__init__.py": "d13f9cc8dde33a81a4df3f863ab9680bcae7d21a86d02a4e87c2b7490a666e1f", "package/accountRequests.py": "0d0352931d5462a5e0fa64c0e2422f712c413d224b833801b3f366ec4a65cf52", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "9dde28f78d2b4e5e60572155990986c622186fb467aede6d62f354c6066105e7", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/customAccess.py": "44dab81cf3574c2f9875f77de3c85f14cdd05e9ac1809e407f29fd61c120d3d1", "package/customPortfolio.py": "e05f32e3c0c6f7a129d128752cdd3602596dcdcff0c1f0ba4289ebd22ed9d76a", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/feeCard.py": "343c6363c39679810fca4f1498ec3dbf8cbd76361ceccafb5d04723616a634d9", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "75654c95ec9ef8770517393df78a4c0a5e806dc23378ccae2a1c74ed421edd95", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "75e173df01b41481358257ab4e7f05f25f1b177006658ee1cdfaae61db974144", "package/fixturesAdapter.py": "561b0eaea5a41d7301b4810de8c5a934c619758c1955f90e89efe035cd32b317", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "7e0d39c670eb6683d75dd9e27e2927246b629a2d97725f9bbed4a6d0ce8f798e", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/houseTemplate.py": "52ea6c0c0d680bef6aa40a8fb99ba298a7925f43d9510e612ea016c69e043a0e", "package/otherCharges.py": "2559db2093b8240638998f250e9c805461719319b9a2bcf4104878d6b7e81914", "package/overlayRules.py": "8d871f1f5002b55066394642bf0dea066927812bb7a2de25c6a7454eef0d3a82", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "06060d774220c29cc65b2c6917b7ac3cbb37c303fbe50142473a26753fe44d0c", "package/products.py": "235deba0e6146b14a1287903c138d5abe9ade55965d0a02f9d583cea052662bf", "package/proposalRegister.py": "9eb59faae3064cd26d790b4b5a04b5a536f917cdcad9c581a39777aa3737cf75", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/repositoryFeed.py": "1bb562fa5017f4d982faaa2407cc35eb6c91a3bc247f65581de0b857ee367d1e", "package/rules.py": "48fd604a87d2276f035fcb3cd0f77d7694e3f5bc90d1674558c0089609dac01d", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "529416459004d3a0804663e1cb58913deea8f40e815a68366b02b38b4d3dab38", "package/scenarioStore.py": "a51dc4bf5494c5ec0572062caaaf5821c3488db5d54b3429246be3f53292cfac", "package/sheetDoc.py": "9417ad786e4223e83ef4caee021fbed42cb83d2319d65806f918c6421e31a18c", "package/sleeveRepo.py": "d0722caede593ef894677d04974fb2018ca8c43d1d0bda2e04153eb1452966ef", "package/sleeveRules.py": "7fa0ae37696ac0060c141faf05f1b5fb29067ee30c8024599384b6d4e0bbf253", "package/sleeveTools.py": "87a1734ef7cdf12b34fe150004c96860586f93cc5ee9e6f822a30578b53f14b9", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "a3550c15086820986e7e0354e9a5997dbd23bbd2bb8c4d0615e7cc461c41f8ea", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "d1ba1ea5b616ee0badfcebafa0efa877d1c69aa630996bf9b6c8fc4a8321d27a", "page/proposalTool.html": "372a0389afbd4e586be216a22bd77feffd5f9fc45c18ed351476ef1ec7bd4168", "page/static/css/proposalTool.css": "4b9f097f4d5e7d4dece0004bae388b1c130dc1d6f9d39e72c8bef357dd1646df", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "72c1ff5300e22e1f5bb19e9f002c93a61d0ea10c1e3787c0e14e021a330d0cd7"}''')
TARGET = json.loads(r'''{"package/bakedAdapter.py": "dbe73f522b121de5e5b5450ef8615c918a7577805a95e83769732a0e45252755", "package/entities.py": "1311fd18d11e3a8c1950fa480cc182623ddd26f387fea42256da6ee3582477b0", "package/fixturesAdapter.py": "ec7501805601226357435c297d2001d7c9216c45b2610f98e108c058fd070a94", "package/overlayRules.py": "0ef24656d19e247f86ebd7e1988e4049846baaf0a4737f0c74657b23a01c7e7a", "package/products.py": "dd81e757da5b9b471f600ee8fae75f965ade8b70b56d29e3e0ae0e00ab62daa3", "package/proposalRegister.py": "6d095fba4bdc063a096f192715f397afaa985875f0ad9cfd559b287bae95832c", "package/rules.py": "5692d86759a3e1e24a82143f6853a1e0d301df7f9e98cfd263e68fa7ec26ad88", "package/scenarioPort.py": "f3f4c9547ad12046e529bbd09a7a5d5317e251f55325ec65fe11ad1ef90853ce", "package/sleeveRepo.py": "ce91de29ca12a6a2c9f5b938ef15eff64034efdb193b4a87e7635ad1fd24dfde", "package/sleeveRules.py": "a1f3ec3dfc24c38db02206535d1aebc70c478afa7d64189db7e9fd2415bbbb74", "package/sleeveTools.py": "5eed74f201d2ef949d34643160d45d1bad035c7b7681b8c566245e932fa2ab9f", "package/types.py": "4a00b650ec251bc37f8dd75870e7bda94a1c6cb726027cf12c0f3b1a305f34e5", "package/workbook.py": "124f17ccd237fff98e775c59a1966c0f0f7942feb5c2bff11a493d441e43b472", "page/static/css/proposalTool.css": "9ace03519ba9a4c48eef082b544f0f6ebc183596bc8a30b09608dd960f0b9e28", "page/static/js/proposalTool.js": "93e8e2aa805a600aa9f0e818c8dac5af2b642330ad1c7f7b30a8e75cce1609f5"}''')
WRITE = json.loads(r'''["package/bakedAdapter.py", "package/entities.py", "package/fixturesAdapter.py", "package/overlayRules.py", "package/products.py", "package/proposalRegister.py", "package/rules.py", "package/scenarioPort.py", "package/sleeveRepo.py", "package/sleeveRules.py", "package/sleeveTools.py", "package/types.py", "package/workbook.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "b6e7ff710d4051f0", "changed": true, "target": "184257d5e251027a"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "_otherCharges": ["from", "cyrus_pmg.pmgService.scenario", "otherCharges", "_otherCharges"], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "customAccess": ["from", "cyrus_pmg.pmgService.scenario", "customAccess", null], "customPortfolio": ["from", "cyrus_pmg.pmgService.scenario", "customPortfolio", null], "customSleeves": ["from", "cyrus_pmg.pmgService.scenario", "customSleeves", null], "entities": ["from", "cyrus_pmg.pmgService.scenario", "entities", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "feeCard": ["from", "cyrus_pmg.pmgService.scenario", "feeCard", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "repositoryFeed": ["from", "cyrus_pmg.pmgService.scenario", "repositoryFeed", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('69')
PAYLOAD = """
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
tAAAAAA=
"""

if __name__ == '__main__':
    sys.exit(main())
