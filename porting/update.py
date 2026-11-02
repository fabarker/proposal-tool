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
TARGET = json.loads(r'''{"package/products.py": "0d81e7b989a065d8691883a285512b61b2dacd7c7f78ac431110228f5963abac", "package/proposalRegister.py": "74f5b3e892ce213fd47785c218a1cfc5633d5a2064b2592ae5e8fa6854968bd2", "package/sheetDoc.py": "56f540e13394c2d43d371c329d17e781852f199a85bb7301ece669fb6676cb0b", "package/sleeveRepo.py": "67db78d853c7b4c8e3c968cd3aeaac2d4794df3fbeef978bd76763f1215f33c2", "package/workbook.py": "66fbcf087b58e04eea82965181a3d25063d638fe9d69cf4919a97db6fa7accef", "page/static/css/proposalTool.css": "b8de1d7f7032bbb6dc6036ae2d6a9d8e152c9da3f71b487ab9c1a551371a4479", "page/static/js/proposalTool.js": "ee729b32f4d2f431611800c370d4fe71bb2e80aac3b08a42989c3276f5d54be8"}''')
WRITE = json.loads(r'''["package/products.py", "package/proposalRegister.py", "package/sheetDoc.py", "package/sleeveRepo.py", "package/workbook.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "8673a24e0c8b5047", "changed": false, "target": "8673a24e0c8b5047"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "_otherCharges": ["from", "cyrus_pmg.pmgService.scenario", "otherCharges", "_otherCharges"], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "customAccess": ["from", "cyrus_pmg.pmgService.scenario", "customAccess", null], "customPortfolio": ["from", "cyrus_pmg.pmgService.scenario", "customPortfolio", null], "customSleeves": ["from", "cyrus_pmg.pmgService.scenario", "customSleeves", null], "entities": ["from", "cyrus_pmg.pmgService.scenario", "entities", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "feeCard": ["from", "cyrus_pmg.pmgService.scenario", "feeCard", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "mandateCodes": ["from", "cyrus_pmg.pmgService.scenario", "mandateCodes", null], "notices": ["from", "cyrus_pmg.pmgService.scenario", "notices", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "portfolioBenchmarks": ["from", "cyrus_pmg.pmgService.scenario", "portfolioBenchmarks", null], "privateMarkets": ["from", "cyrus_pmg.pmgService.scenario", "privateMarkets", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "repositoryFeed": ["from", "cyrus_pmg.pmgService.scenario", "repositoryFeed", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "restrictions": ["from", "cyrus_pmg.pmgService.scenario", "restrictions", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('95')
#: what this update carries, and a short id from the files it writes, so each
#: run says which update it is
TITLE = json.loads(r'''"The product catalogue in three levels: product, share class by ISIN, listing; the ISIN, Income, Share class and UK reporting on proposals"''')
UPDATE_ID = hashlib.sha256(json.dumps(TARGET, sort_keys=True).encode('ascii')).hexdigest()[:8]
PAYLOAD = """
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
YLD+GsVSOHVhDv0eA+ndHgACVcUMnnfX9xmQ6gFha3IzHqSMuPdDGCOQOsX7sCHPWm3wDNEShmGV
2JS+T9/CKfMOBH9kl7Dv31MibAcj2SPwgt8e9ylpIeSde/U7n9ym6rKtfg9HeAEN4m//8zIBbLOX
hQ1Tm5VHojiamwfKSP8QciQBQljDo2AkmVQ9Q7EB26JpjuDf3z7cAoVdivUg+xu/wezmZvH+9m9b
bzYOdrbl5gLdPD3YPZEb7Tk7HkTWeS1bkPmINMtD4ezzu5FVLDfNENUSVs0Mq8BaZrSQkRaKr60l
ERYFOpukLbQ1wMavT2r/ebYx88f517kH/Ds7s3r+dfXhDP/+W8h6sPaPWEQkAJYhoF1zkZr67vGh
mW8vLc20gcCPr5OZOfp86/D04OTo949bh69okwJz+N/pEM66+ofv8f/wnY1XZmPbbLw2GztmY9ds
7JmNfbNxaDb+aTaOzMax2TgxG6dm41cDsNr4w2xumM1Ns/nKbG6bzddmc8dsvjGbu2bzH2Zzz2zu
m80Ds3loNv9pNo/M5rHZPDGb77GjzV/N5u9m8w+ztWG2tszWK7P12mztmK03ZmvXbL0FxtRs7Zut
A7N1aLaOzNap2Xpvtn41W7+Zrd/N1h/m1bZ59Q/z6q15tW9eHZpXf5jtLbO9bbZ3zPYbs31kto9p
uU/M613z+h/m9Vvzet+8PjSvj8zOhtnZNDuvzM622XltdnbMzhuzs2t29szOvtk5MDvvzM4/zQ68
eWx2TszOqdn51ez8bt68NW/2zZsD8+bIvDkxb07N7iuzu02Lumd2983ugdk9NLv/NLtHgCJm98T8
Y9v8Y9/849D84515u23e7pi3b8zbXfN237w9MG/h5pF5+6t5+7t5+4fZ2zB7m2ZvC+Qbs/fW7B2Z
vWOzd2L2Ts3ee7P3O3YER+X+ltl/Zfa3zf5rs79j9t+Y/bdmf8/s75v9A7N/aPbfmf1/mv0js39s
9k/M/qnZf2/2fzX7v5n9383+H+ZgwxxsmYNtc/DaAF042DUHe+bg0By8MwdH5uAUOzr4wxzum3cb
5t22effavNsx796Yd29BSDDv4P6BeXdk3h2bdyfm3a/m3e/mnxsGkPro0Bwdm6NTc/SrOd4wx5vm
eMscvzLH2+Z4B4Roc7xrjv9hjt+a4z0492mrHhhA2eMjc3xsjk/M8Xtz/Js5/t0c/2FOtszJKwNH
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
OgAAAByLAAAAAA==
"""

if __name__ == '__main__':
    sys.exit(main())
