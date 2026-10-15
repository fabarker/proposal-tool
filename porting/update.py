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
from cyrus_pmg.pmgService.scenario import accountRequests, proposalRegister, sleeveRepo
c = sleeveRepo.connection()
c.close()
proposalRegister.describe()
accountRequests.describe()
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
BASE = json.loads(r'''{"package/__init__.py": "bd7f6f77f7f622066cd7ba16dce29aaf55e8c2b16463e33fc1a77a141cff5fb3", "package/accountRequests.py": "4c6a02163d191e660e525f82fcada03ff32fe0253a4193a07ad1f364ff47f7e1", "package/advisors.py": "9b3b950a5b3d345c6dcddeb17e666a9834cb7543b7147d308331202064835180", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "3f4738bdc9583e3d27761a513b05391554088a277100f5e9a0e991089c8e72a8", "package/bake.py": "392629c8ca7636f1fb45b71d9d60b33742146041d920623224cac53571840bfa", "package/bakedAdapter.py": "9dde28f78d2b4e5e60572155990986c622186fb467aede6d62f354c6066105e7", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "80cedb6a219a3ef0c0e1124c0a7bb929754da482f6633eb770cf6e101a9a41f2", "package/fees.json": "370d27a15d69661d89c6041e005f91200ac73f0a12b73a43e709a3c3da28caad", "package/fees.py": "631d4c639fd15f9498a7dfcd609587ad4e109b0df91d8ef0818c5678e6ab260d", "package/fixturesAdapter.py": "561b0eaea5a41d7301b4810de8c5a934c619758c1955f90e89efe035cd32b317", "package/fontMetrics.py": "bd984b1c56f77eec18ad5cb9d2ef9371dd047806cb1f0179814320577c09b744", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "0ff9f93acce07048b09bd364ff44350e41b74c9864dedf7b66352cb94759c448", "package/houseFonts.py": "afe5e3c00a5601cc3088f996932a4a9226ec0def557a889078ab4e2cc1a602be", "package/overlayRules.py": "f2d6fda4286444df3ff05d2cc9f1dd8cbd7fd67363f1b92329861389d7cd5917", "package/payloads.py": "3e62f31e9dc76a7095595013bb4026a155b939c0e8806b220f43869b8bfe39cd", "package/portfolio_weights.py": "4804d1fe11a7ebec8c15bfd9c51c788f2e9bc5688e40ac80d2cb051e785aecb9", "package/pptWriter.py": "775b13025d79bd140469079cf9e988626be2fb1c86c0656b8e0811d794eb745b", "package/products.py": "1af2d6e72cb8e841d6bddf1d6cc63e1f51b16e3d84d9bf1aec0e7dcb153cc8a0", "package/proposalRegister.py": "1dfddcd86d038f07c867713f3298700280f1ade042a871c407e1c4221c6d7d87", "package/registry.py": "8714a3b1041b5c0d87fd94c5e540c62324b288c5e5d3a7d452320a10adae58b5", "package/rules.py": "dc5a25097cb0e82293b8b9740b61d269afb45c23d84e25a79f8a7db5903a4dab", "package/saaKeys.py": "50038eff679f5834812930b0e142a26f039313d116718fe256215e1d52e16863", "package/scenarioPort.py": "529416459004d3a0804663e1cb58913deea8f40e815a68366b02b38b4d3dab38", "package/scenarioStore.py": "7aa7b619bde4950c40c845f064e58675a53edfc29a535e3712774b3e96ea89a1", "package/sheetDoc.py": "d7e824ea2135bee25c3f04bf409c4da31a9f57adb2e4cd7b8a3ba0673925a29a", "package/sleeveRepo.py": "b3c01424cf4b26c22d289f83a438aa7ef073fc05c09edcb4fa41c19182934642", "package/sleeveRules.py": "cf70688d2fe3b30be390b850c663fbd0fa90a0fd284245dc47fe81718554f4d6", "package/sleeveTools.py": "9ba820ef59fcaeb2a7e554bc00c945f39a072940f36e25f2a50f04002999e679", "package/sleeves.py": "3489d09570a664f51fa6d472219d4eedff2c1e918f90c3661bf42bbc77ce371c", "package/types.py": "a3550c15086820986e7e0354e9a5997dbd23bbd2bb8c4d0615e7cc461c41f8ea", "package/universe.py": "7c379bfca46e9837d8849117059a6a502ecf501ba561ef0d9602c88bc25d4611", "package/workbook.py": "6c808a3fb1e3e8bcc0d6873ac72374dff8cc2312abde12475c36974bc90bd60f", "page/proposalTool.html": "ff4e3ee555ab61aef726ab8814d1cadd21e6ed92387e43b332b27681ab8bf061", "page/static/css/proposalTool.css": "72d8ab73850dbd4e617a547d60ef082126622248c95659e04c6ca67fee12d6c2", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "c5f9cce8c6efda63792d40ea3090c30be55bd80f922d844d1e33ca7dd153eb0c"}''')
TARGET = json.loads(r'''{"package/accountRequests.py": "bbdf46bc39eae2885c66f790b73b76c246a5a7f3193fe0296543d6ef31e42efb", "page/static/css/proposalTool.css": "c9551bad836da8e03d725a1409e8e2bdcca005f214f9d0879a1bfe0e1a1a1932", "page/static/js/proposalTool.js": "c2fcf3f97981dda26ef2d78650a923ead79ce8d81920d14de0760c2a67b8e9a4"}''')
WRITE = json.loads(r'''["package/accountRequests.py", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "6d4a97d852d9959d", "changed": false, "target": "6d4a97d852d9959d"}''')
ROUTER_DROP = json.loads(r'''[]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''[]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
#: history notes an earlier version wrote, and how they read now
NOTES = json.loads(r'''[]''')
ROUTES = int('42')
PAYLOAD = """
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
AAAA3Q4AAAAA
"""

if __name__ == '__main__':
    sys.exit(main())
