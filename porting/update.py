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
                    if name.endswith(('_DB', '_DIR')):
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
from cyrus_pmg.pmgService.scenario import proposalRegister, sleeveRepo
c = sleeveRepo.connection()
c.close()
proposalRegister.describe()
print('OK')
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
            elif now == BASE.get(key):
                files.append(('write', key))
            elif key not in BASE and not here:
                files.append(('new', key))
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
            ok, out = _python(src, _UPGRADE, {d[1]: d[2] for d in plan['dbs']})
            if not ok:
                raise RuntimeError('opening them with the new code failed: ' + out)
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
BASE = json.loads(r'''{"package/__init__.py": "da868ae634ab007e52e3031296d600c8cd0e1bbda61e53cac8c03477bcd0e683", "package/accountRequests.py": "ba388739175adea084032b4a46929646316b6edc9c8dde28d2759a01da7e0c1a", "package/advisors.py": "ee8e2d33f6f9e40b76d05712597e1c4c9607af3740853717152833b4a8b4610b", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "d02a7da71265bcec48552b9d0b6a2e3da5ff523d4680d9ddb964c859b9905aa6", "package/bake.py": "f4c4e7478f4cd16776473858c56312e7fe9341ea611d3d752b4a46c7d8331d9e", "package/bakedAdapter.py": "6869bbd66e2aca7cf0541219d0a3c4e637eba2f59968a5df37d669c6360a4bef", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "808c6e56a66df2a55a93b3db54a17a1d763a27de93424895ce98cbbca782d12d", "package/fees.json": "b6efce725269003f65170d5b6ee51eeab626595a463dccb16f3cd323a8d19931", "package/fees.py": "c987dfca2671752bb7b29b95558aa033fa093b1ce21aa8263335f6126256ff7b", "package/fixturesAdapter.py": "a0661bc4df48481d5fa3b9d2e7c4cb8f18677b08cb05c94115f3f0aa78562f19", "package/fontMetrics.py": "8488bb229685b2f48f1db4dcbe191cb82ccdd91228a4cc2058160651ad8cbc65", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "739cf2476303f395731046425105fa008612b3882d7a5006131422f477041879", "package/houseFonts.py": "3247946076f4b8d6a38e9ec9d5e42cf0dd320d1df03cc8537efbe2e19c674a02", "package/overlayRules.py": "690150cb94de11dbfd152cce6b0e56523f5c038107129df3bcb8f72e2fc7af2b", "package/payloads.py": "c8a766473bd6ed97c90a60f9f91443b2a3e2a9c8744f95cb7295a59a5a0723e0", "package/portfolio_weights.py": "fe5b348a206ef60624fd04477683c4569fab853edfe67ba939c70b8d900572f9", "package/pptWriter.py": "0160f2efa863c33b585ecf140f6f433bc66012b362dfd7989eec0d2323294254", "package/products.py": "bb5ad31fc2875a093a9ab4e7f77e24e3ac00c3de2582b3e4762478b105deb2d9", "package/proposalRegister.py": "3b07e6c8566e90250f737909b009dcbc50e01d5a4c40daeef1b6fe335a60f7c2", "package/registry.py": "26d42f5484e9006777c68a5049bbdd6320ce33b480652a640d7a573c4a655ad3", "package/rules.py": "fa4ec7604dc822651cee29b24de2ac6750f46934888abe90a9768d8c1f927d94", "package/saaKeys.py": "68156acc87a624534b5fc73056c5f4baa56789ab0df1048154c71fb6e18c1c7a", "package/scenarioPort.py": "78a563fe4af688a750e76dde65c8aac58f47461bd584789635e0e66bb900281c", "package/scenarioStore.py": "65e1a51dc8be316092c520660143c2c32391b2d2ae44de748c16a31c6b7c9710", "package/sheetDoc.py": "6791b4c7d52007ff10fc0801d4a89c1d9bf171b44aaa7efd77efd645305430b3", "package/sleeveRepo.py": "d503c751539776d79b4ff8e6dae6c6b165cc420393ccb12b834d5281104fc7fc", "package/sleeveRules.py": "742cb3474bcd26bb071b69efca3d2cc830ff9ab71a656af720af3a0646f21e73", "package/sleeveTools.py": "cbe30aba9c94fc6348736f49fdcc81d02454ce1c53b7b9877f5409121fb3cbf2", "package/sleeves.py": "667dc04753b86e63bb259b1b27afe97cf1fd0dc1f4f9e2017d585a6bf0e28834", "package/types.py": "1a9cd1da911531e529e075127828459084865e8c666a8fcdbc12ac6215b69352", "package/universe.py": "bdada4c55138114f4b31adc4705f294e8a6193f5b95de28c99d257062988c0b2", "package/workbook.py": "7d4b16b42a77ec84fe907d4f19dc3bb9be1d2f7b95233c96039d62b97c5bf5a8", "page/proposalTool.html": "322c7bb71fe92b9ba7e19dfe87d1228a38f3f29b20168d14f4873e19d971b2c7", "page/static/css/proposalTool.css": "0255895529944894503c511ea3087b1261653a338f855d675be80213557a3af7", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "517fb94661b3d01892da93640c721922c55dc2e843e44327fcb43c5fbc11f407"}''')
TARGET = json.loads(r'''{"package/__init__.py": "bd7f6f77f7f622066cd7ba16dce29aaf55e8c2b16463e33fc1a77a141cff5fb3", "package/accountRequests.py": "4c6a02163d191e660e525f82fcada03ff32fe0253a4193a07ad1f364ff47f7e1", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "9dde28f78d2b4e5e60572155990986c622186fb467aede6d62f354c6066105e7", "package/feeTools.py": "80cedb6a219a3ef0c0e1124c0a7bb929754da482f6633eb770cf6e101a9a41f2", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "631d4c639fd15f9498a7dfcd609587ad4e109b0df91d8ef0818c5678e6ab260d", "package/fixturesAdapter.py": "561b0eaea5a41d7301b4810de8c5a934c619758c1955f90e89efe035cd32b317", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fundingSplit.py": "0ff9f93acce07048b09bd364ff44350e41b74c9864dedf7b66352cb94759c448", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/overlayRules.py": "f2d6fda4286444df3ff05d2cc9f1dd8cbd7fd67363f1b92329861389d7cd5917", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "775b13025d79bd140469079cf9e988626be2fb1c86c0656b8e0811d794eb745b", "package/products.py": "1af2d6e72cb8e841d6bddf1d6cc63e1f51b16e3d84d9bf1aec0e7dcb153cc8a0", "package/proposalRegister.py": "1dfddcd86d038f07c867713f3298700280f1ade042a871c407e1c4221c6d7d87", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/rules.py": "dc5a25097cb0e82293b8b9740b61d269afb45c23d84e25a79f8a7db5903a4dab", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "529416459004d3a0804663e1cb58913deea8f40e815a68366b02b38b4d3dab38", "package/scenarioStore.py": "7aa7b619bde4950c40c845f064e58675a53edfc29a535e3712774b3e96ea89a1", "package/sheetDoc.py": "d7e824ea2135bee25c3f04bf409c4da31a9f57adb2e4cd7b8a3ba0673925a29a", "package/sleeveRepo.py": "b3c01424cf4b26c22d289f83a438aa7ef073fc05c09edcb4fa41c19182934642", "package/sleeveRules.py": "cf70688d2fe3b30be390b850c663fbd0fa90a0fd284245dc47fe81718554f4d6", "package/sleeveTools.py": "9ba820ef59fcaeb2a7e554bc00c945f39a072940f36e25f2a50f04002999e679", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "a3550c15086820986e7e0354e9a5997dbd23bbd2bb8c4d0615e7cc461c41f8ea", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "6c808a3fb1e3e8bcc0d6873ac72374dff8cc2312abde12475c36974bc90bd60f", "page/proposalTool.html": "ff4e3ee555ab61aef726ab8814d1cadd21e6ed92387e43b332b27681ab8bf061", "page/static/css/proposalTool.css": "ccc57c28a7ad7a8569882d804bf21675f3279c9d3a547d2953ab040ec017be92", "page/static/js/proposalTool.js": "9f41e045b48662be06c74cdf52454c9261a7b06a9f5bdbe09eec5aabe991aea7"}''')
WRITE = json.loads(r'''["package/__init__.py", "package/accountRequests.py", "package/advisors.py", "package/assetEstimates.py", "package/bake.py", "package/bakedAdapter.py", "package/feeTools.py", "package/fees.json", "package/fees.py", "package/fixturesAdapter.py", "package/fontMetrics.py", "package/fundingSplit.py", "package/houseFonts.py", "package/overlayRules.py", "package/payloads.py", "package/portfolio_weights.py", "package/pptWriter.py", "package/products.py", "package/proposalRegister.py", "package/registry.py", "package/rules.py", "package/saaKeys.py", "package/scenarioPort.py", "package/scenarioStore.py", "package/sheetDoc.py", "package/sleeveRepo.py", "package/sleeveRules.py", "package/sleeveTools.py", "package/sleeves.py", "package/types.py", "package/universe.py", "package/workbook.py", "page/proposalTool.html", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "49efab8918b723ea", "changed": true, "target": "6d4a97d852d9959d"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[["D136: a third in Investment Grade Fixed Income, two thirds in Public Equity", "The original split: a third in Investment Grade Fixed Income, two thirds in Public Equity"], ["D155: the two overlays as the code applied them - the tilt, 8% of the portfolio from Investment Grade Fixed Income, then the premium, 7.5% of Investment Grade Fixed Income as the tilt left it", "The two overlays as the code applied them - the tilt, 8% of the portfolio from Investment Grade Fixed Income, then the premium, 7.5% of Investment Grade Fixed Income as the tilt left it"]]''')
ROUTES = int('42')
PAYLOAD = """
UEsDBBQAAAAIAAAAIQAE2oP1wHoAAKtjAQAMAAAAcGF0Y2hlcy5qc29utL0JexxHkiX4V6LI6QHJ
TUB5H8Co9kORUInbvBaApJkt9ScEkAEginl1RiYhVE//97X3zMzdIzJBSdW7muksicz08PDDzmfP
/uPZKr/5nN8V3/zyS7koN7/8crR6fHac/e1vw1Y2amV/ezbNN3kryxfTbLHc3JeLu+y+WBfZzXK+
2m6KKsuz2/Juuy6Ossv7IisW09WyXGyy69ny5nNWLp7927/JKJ12K+t0MFwm/1Q3xSJfl8tPy/VG
HpfpPxv5+Ur+RIZebNb5zSYrvhTrxyyf5qtNsc7K+WpWzIvFprIxezJm38fc/0/xK0e8LWfyxHlh
PxzID4dhMnn+r8VjFefBf/Cz2+WsXGYfTt+fZYd/zj4Xj8ec481sWRXT7MvyJr/ezuQ9CptQV16y
2/n6hDDAcjF7zKrldn0j/34rS5vPHjflTYW/lJUtfTh5v+5vvJ99cyzfnPg3V+vldHuzabwRHjwt
ZqUsqUzevpPdyObOlndbW5mezL7X+/ojq6KYygjLhcz+dr2cZxevzz6cnr/9+MvFu7OzH88ufrk4
O3vzrJXp6s4K2cXz7axI5/NwX97cZ5Xs8qa4K2/iastxWmTFtNyUywXXxgaQRclul2ubpSxKb+Cz
vC2K5ubZ68rfyAxl2x+W6888wfVFwNNlBdZTG1aORD+8fHVfFJs3y5vdVVwXPFN5leFq6Jm4ld3U
n1TZ9bacbbg+vgh4/vVy+Xl3rLNfb4qZjLiYyozWeOPNvZyuTB5c+a9Xq81P61JuQO3n+PWn5UOx
/sTr5kMc2zvefG7Jqd9s5DXx4riMxdRHvF9uq+I7uWW1hcMP+TfZbX5TVAe8NVXLF65cZ8X8uphO
IQLKhWwNnuJD3spo74vNWo5xMuZdsSiwylOdlv5epmQPmOsPWthaXcZyE956vVwtq3x2LgekCi+v
EqF2jvmtllzP1Ybj4Cs+SH5zs9wuNufFv2+LKryt/Wm2XBULvMza/jrL7/JyUcnWhnHlZMjRCDJy
dzyKSgg3nJuf+f9Onxj+yI6ZnDJ8WQ44Xnkmy4tvrWR8rPisyspNVcxu9XLl2SebSvbD2zct/kS2
aC1ScVrJPco3JtPk+kP+/O0ZTj0O9UGVrWayzvfLmZwMHvbqJffhMVuX04JDVTf3xVwu2VKlL+ag
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
"""

if __name__ == '__main__':
    sys.exit(main())
