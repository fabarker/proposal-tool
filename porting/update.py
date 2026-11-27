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
BASE = json.loads(r'''{"package/__init__.py": "d13f9cc8dde33a81a4df3f863ab9680bcae7d21a86d02a4e87c2b7490a666e1f", "package/accountRequests.py": "7a0cb7785def0989263d72347012f53c5db3adc213b664541d9be4465ca8dd50", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "dbe73f522b121de5e5b5450ef8615c918a7577805a95e83769732a0e45252755", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/customAccess.py": "44dab81cf3574c2f9875f77de3c85f14cdd05e9ac1809e407f29fd61c120d3d1", "package/customPortfolio.py": "f5031bf9e3af86bc0ac55e6d9cbb3a557ad4b0f3086c0446c5a7fbbe1f083c71", "package/customSleeves.py": "c3bf83add0c582558bf348ba3081a1df62c375e9dd866007ffa6ae527bd7c8eb", "package/entities.py": "32fdfcbfe042664eb04a790dd35387b3aeb799702ec794a5eb2a63b70e1bfa7c", "package/feeCard.py": "343c6363c39679810fca4f1498ec3dbf8cbd76361ceccafb5d04723616a634d9", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "75654c95ec9ef8770517393df78a4c0a5e806dc23378ccae2a1c74ed421edd95", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "75e173df01b41481358257ab4e7f05f25f1b177006658ee1cdfaae61db974144", "package/fixturesAdapter.py": "ec7501805601226357435c297d2001d7c9216c45b2610f98e108c058fd070a94", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "7e0d39c670eb6683d75dd9e27e2927246b629a2d97725f9bbed4a6d0ce8f798e", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/houseTemplate.py": "52ea6c0c0d680bef6aa40a8fb99ba298a7925f43d9510e612ea016c69e043a0e", "package/mandateCodes.py": "146976726d8ea2efe37ad748f0abf3b795badbb2885709b5b332b44963039eb9", "package/marketing.py": "6731d20f22b044de32892585cdf50a72b205f445251c327525d32a3bb9c525fe", "package/notices.py": "240dbbad53a5a3ed174ab6e8b6afad411d686f5d4f36db35ec0cb4ac43d05d90", "package/otherCharges.py": "2559db2093b8240638998f250e9c805461719319b9a2bcf4104878d6b7e81914", "package/overlayRules.py": "28f7f58859f55ab8a4032b6ef4ef05bf1eb4f48b263697298694bc10bd4abd69", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolioBenchmarks.py": "ebe9397b17e439914f706fad1e0a59fb7481f429f39c09e1584993eb24d295cb", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "06060d774220c29cc65b2c6917b7ac3cbb37c303fbe50142473a26753fe44d0c", "package/privateMarkets.py": "640e1397105e083812943eae67124e50915f6b74e5f5b95999690b9b75db2c01", "package/products.py": "853ade0d5462569cbc0711895b3ce44c60ee36af7102dbf99fb764f28c49a384", "package/proposalRegister.py": "234258512b83b96ac40cf7e5c7fafa11bcb246f04d7a21d817ac49fdc01e7acc", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/repositoryFeed.py": "d4241dc9136c98d6216be6d0c4ace45062ed97a1937080ea63d9b03a159bd558", "package/restrictions.py": "0d73b8ea7866bbf84416a65b10e915d3a4a0aed88bc14f8209d1d224a066533f", "package/rules.py": "d058a6fc87c23734be22e38a9b9f71207a7347499bb795d5a42828cad391d1ef", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "92b3918327dd9c93b8e8d8d95583d4e059dacaa19990f2683c14271492ee6b13", "package/scenarioStore.py": "225f02e3be6d554c4e5069c9fb287b53c4b5da9e08036124b8855a79719d706b", "package/sheetDoc.py": "b959cad0c87cbb5bcd9cd56a070ffbfc377782fc74f5cba9fc988d5e8418c88b", "package/sleeveRepo.py": "6794682dd3f9bd81c1dad0cbbbc3916c7af4a9dad7fb56be9c49f66003b31a90", "package/sleeveRules.py": "087f7ad77fba37a0a1d0955a9078e50b92d97f4b3d3227dd0d5a4558b8425f2f", "package/sleeveTools.py": "f34beced7034b22a736f34dce335e8db74054499cf1aa4ee244ef0add8277ad3", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "4a00b650ec251bc37f8dd75870e7bda94a1c6cb726027cf12c0f3b1a305f34e5", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "66fbcf087b58e04eea82965181a3d25063d638fe9d69cf4919a97db6fa7accef", "page/proposalTool.html": "07834491e82f22aa6394f3265772782ebebdda08a8858ca28625af4420f4a397", "page/static/css/proposalTool.css": "c024f39340154d9bfdef5bf72acb8390297d9b0b26ec4207cfa86c2d2dd87a21", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "0f478190fc97c83a38eaa0a7363a9777a4f5f9321ea76f910de193c0c52fb696"}''')
TARGET = json.loads(r'''{"package/products.py": "645f6bfaa1c5f76f15b1145417f7d860b3d340ebd3ec3d4ad967348792cd0972", "page/static/css/proposalTool.css": "70ad6c19b8dd94720e811039504f88b55bdec1980844c09a32bb28fe5155f3a0", "page/static/js/proposalTool.js": "1aeadc619408ad760aff00129de30709fd7afcd83da23a8fbbeba8703dd03590"}''')
WRITE = json.loads(r'''["package/products.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "e93e94ffc564335c", "changed": false, "target": "e93e94ffc564335c"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "_otherCharges": ["from", "cyrus_pmg.pmgService.scenario", "otherCharges", "_otherCharges"], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "customAccess": ["from", "cyrus_pmg.pmgService.scenario", "customAccess", null], "customPortfolio": ["from", "cyrus_pmg.pmgService.scenario", "customPortfolio", null], "customSleeves": ["from", "cyrus_pmg.pmgService.scenario", "customSleeves", null], "entities": ["from", "cyrus_pmg.pmgService.scenario", "entities", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "feeCard": ["from", "cyrus_pmg.pmgService.scenario", "feeCard", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "mandateCodes": ["from", "cyrus_pmg.pmgService.scenario", "mandateCodes", null], "notices": ["from", "cyrus_pmg.pmgService.scenario", "notices", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "portfolioBenchmarks": ["from", "cyrus_pmg.pmgService.scenario", "portfolioBenchmarks", null], "privateMarkets": ["from", "cyrus_pmg.pmgService.scenario", "privateMarkets", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "repositoryFeed": ["from", "cyrus_pmg.pmgService.scenario", "repositoryFeed", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "restrictions": ["from", "cyrus_pmg.pmgService.scenario", "restrictions", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('96')
#: what this update carries, and a short id from the files it writes, so each
#: run says which update it is
TITLE = json.loads(r'''"The product table as the desk's data sheet lays it out, and ISG hedging on a fund's share class"''')
UPDATE_ID = hashlib.sha256(json.dumps(TARGET, sort_keys=True).encode('ascii')).hexdigest()[:8]
PAYLOAD = """
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
"""

if __name__ == '__main__':
    sys.exit(main())
