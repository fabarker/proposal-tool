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
TARGET = json.loads(r'''{"package/feeCard.py": "343c6363c39679810fca4f1498ec3dbf8cbd76361ceccafb5d04723616a634d9", "package/fees.py": "75e173df01b41481358257ab4e7f05f25f1b177006658ee1cdfaae61db974144", "package/products.py": "ed4b2cb30dd0b92727ec3b2fc08f5fbaf04bdd0a299c3a416f193a6b9ca4b3e5", "package/rules.py": "85d52a58b21a59cabf85b4e48dc276ee4fcfca804f48dccf02919d53217b020d", "package/workbook.py": "edf00654f568b38c86744c3191e63687abbfde137d6e8de491c75bde72a398de", "page/static/css/proposalTool.css": "6e9e3bb4834e2c264770caafeb951946ffe989f5044e3bd568a3e2b3c947cde4", "page/static/js/proposalTool.js": "0cf5970ee963ffd5084ee359e9cac05188f54a682a90a974cf91c76094df0604"}''')
WRITE = json.loads(r'''["package/feeCard.py", "package/fees.py", "package/products.py", "package/rules.py", "package/workbook.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
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
Y2sudHh0UEsFBgAAAAACAAIAeAAAANxiAAAAAA==
"""

if __name__ == '__main__':
    sys.exit(main())
