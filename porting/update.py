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
TARGET = json.loads(r'''{"package/customPortfolio.py": "e05f32e3c0c6f7a129d128752cdd3602596dcdcff0c1f0ba4289ebd22ed9d76a", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/fees.py": "1c5dfcfa3eb8d73ad9ccc5e6950dd731f72d56971d243b8c7c98e97296bf379b", "package/products.py": "b971fcc5306123aad21c40ae040bc68d4dead45b43d89ce1b3dcc63a7e1dd478", "package/proposalRegister.py": "363eef64bb675df8adefe386b8af46e625ae4fd155c944877d6a7d0cfb3638cd", "package/repositoryFeed.py": "1bb562fa5017f4d982faaa2407cc35eb6c91a3bc247f65581de0b857ee367d1e", "package/sleeveRepo.py": "ccec707af12f3d02d2c4e9f62bf3e4f9984dfddd9717d80b80256677939f024d", "package/sleeveTools.py": "fc5a594fef48d540dee2d22d1b46803e0c611b7e660fd82b1f06e51141ea1a31", "package/workbook.py": "b959a67663dd15989386fd8b4728d03209cc79f7521ea6a134ac4658090e81f7", "page/static/css/proposalTool.css": "a12c8f6d15ba83b2187c600a9b9ae6847e454fb14bb9f0d50fc4fb6b98839b44", "page/static/js/proposalTool.js": "7d1e2a4db4cf60efa3c890d3bdb6679e770ad54e70059336a9143fb50fd7e876"}''')
WRITE = json.loads(r'''["package/customPortfolio.py", "package/customSleeves.py", "package/fees.py", "package/products.py", "package/proposalRegister.py", "package/repositoryFeed.py", "package/sleeveRepo.py", "package/sleeveTools.py", "package/workbook.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "4c78d2e0da5b1fd6", "changed": true, "target": "3ace2581d4439d7e"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "_otherCharges": ["from", "cyrus_pmg.pmgService.scenario", "otherCharges", "_otherCharges"], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "customAccess": ["from", "cyrus_pmg.pmgService.scenario", "customAccess", null], "customPortfolio": ["from", "cyrus_pmg.pmgService.scenario", "customPortfolio", null], "customSleeves": ["from", "cyrus_pmg.pmgService.scenario", "customSleeves", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "feeCard": ["from", "cyrus_pmg.pmgService.scenario", "feeCard", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "repositoryFeed": ["from", "cyrus_pmg.pmgService.scenario", "repositoryFeed", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('65')
PAYLOAD = """
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
AABAqAAAAAA=
"""

if __name__ == '__main__':
    sys.exit(main())
