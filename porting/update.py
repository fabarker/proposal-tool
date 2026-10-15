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
update, no comment, no marker. Its backups and its report stay beside this
file.

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
    dbs = []
    for title, var, path, table, state in (plan['dbs'] if plan['code'] else []):
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
        json.dump({'src': src, 'files': saved, 'router': router['work'], 'dbs': dbs,
                   'complete': True}, fh, indent=2)
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
    for db in record['dbs']:
        for suffix in ('-wal', '-shm'):
            if os.path.isfile(db['path'] + suffix):
                os.remove(db['path'] + suffix)
        for live, saved in db['copies']:
            shutil.copyfile(saved, live)
    shutil.move(dest, dest + '-undone')
    say('Put back as it was before the update ({} file(s){}{}).'.format(
        len(record['files']), ', the router' if record['router'] else '',
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
        if not plan['files'] and not plan['router']['work']:
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
BASE = json.loads(r'''{"package/__init__.py": "5fef4cd077d83be69bb683fc2a18735d3a1744f42a05e8007f066d0620a7f2ce", "package/accountRequests.py": "ba388739175adea084032b4a46929646316b6edc9c8dde28d2759a01da7e0c1a", "package/advisors.py": "ee8e2d33f6f9e40b76d05712597e1c4c9607af3740853717152833b4a8b4610b", "package/advisors.xlsx": "95db123b1ff9a8055a26705af4044c8dd40a84635a1ce2f5af5da19343469fb1", "package/assetEstimates.json": "8eeea1f1c14cf0b1369881df2b745e0820a7cada16645ae36030a6810bb1a5f9", "package/assetEstimates.py": "d02a7da71265bcec48552b9d0b6a2e3da5ff523d4680d9ddb964c859b9905aa6", "package/bake.py": "3245c727b54d8707b45d2c65dec8df5850a34ff2d3d86836ab78b77af0a4a4a9", "package/bakedAdapter.py": "7b71090cb058454e9e39b305298678a7b85597f764a9b85c2585d6d5e488eeca", "package/csvText.py": "2b7dc0627e32070818afa47542a55f5577b015fac3c8157f47d9799183a1bf85", "package/feeRates.csv": "22ceeccb67e0f2cde08a10e7d9989e1ee6076dc811a70381676db9c9c952076d", "package/feeTools.py": "808c6e56a66df2a55a93b3db54a17a1d763a27de93424895ce98cbbca782d12d", "package/fees.json": "b6efce725269003f65170d5b6ee51eeab626595a463dccb16f3cd323a8d19931", "package/fees.py": "c987dfca2671752bb7b29b95558aa033fa093b1ce21aa8263335f6126256ff7b", "package/fixturesAdapter.py": "a0661bc4df48481d5fa3b9d2e7c4cb8f18677b08cb05c94115f3f0aa78562f19", "package/fontMetrics.py": "8488bb229685b2f48f1db4dcbe191cb82ccdd91228a4cc2058160651ad8cbc65", "package/fonts/GSSans-Bold.ttf": "f30f53070ae396c2a22fbe70f236946a13a6aeeb1ce3c2889e68812ee17d7693", "package/fonts/GSSans-Light.ttf": "6b270df7012edb2ef15c37c5c2be4a486038a45f201cf142ed0949b893e9690e", "package/fonts/GSSans-Regular.ttf": "40ad741ede6a44df7b26e28384f13e23f51548639d58787e9ddf2cc49f2f13e4", "package/fonts/GSSansCondensed-Bold.ttf": "06c8d9e929c4b68a23410fb05d62b88ed140d11d7d0fbd1a204375f706c9fc89", "package/fonts/GSSansCondensed-Regular.ttf": "df158dc273631a04cfeeac7e8d4fa9762b7ee4290572a8a444ce516218ab5c26", "package/fundingSplit.py": "026b47c3cd5c0cc2082f387aa55da43dd527b066687efce76bf05e6a33bbfc4b", "package/houseFonts.py": "3247946076f4b8d6a38e9ec9d5e42cf0dd320d1df03cc8537efbe2e19c674a02", "package/overlayRules.py": "3d44c958ffb100d3f73f8387664b6290f199994d68c17ff5cedcc5f46c30ce39", "package/payloads.py": "c8a766473bd6ed97c90a60f9f91443b2a3e2a9c8744f95cb7295a59a5a0723e0", "package/portfolio_weights.py": "5595d0c5775db260fea5573eb0237de97474f20053b4d0adb4048a5911915b70", "package/pptWriter.py": "86bb573c873bbc257159caac4ae993935022a7d1c8e70b71dca2b2d2c53a7c6a", "package/products.py": "197615c015e2e5eeb3c2d8c1f9f108fb471607c6502e41533e3cdf767828e68c", "package/proposalRegister.py": "3b07e6c8566e90250f737909b009dcbc50e01d5a4c40daeef1b6fe335a60f7c2", "package/registry.py": "c2aeae3e7ed37861968f62c0bf22681ff74b5f38200d3575866f01240b02f0a5", "package/rules.py": "92a68c07414fb48576252342cfbae685f90fa5eeb90d2c55581ed93dc5f68f12", "package/saaKeys.py": "68156acc87a624534b5fc73056c5f4baa56789ab0df1048154c71fb6e18c1c7a", "package/scenarioPort.py": "13a3953a31400fcabbd2a1f39f4a9cc7fd676ace87a04bbcf04ef5bc97d188dd", "package/scenarioStore.py": "b31397eb86cad77a484b1fd3742b902cf7a86ba3b6875ccd08a6e9b3a571072b", "package/sheetDoc.py": "2c5edc1790e4e2c2ac0ac341eb1e112c5f9257c23823b06fed2d3ab07ccbd11d", "package/sleeveRepo.py": "aa38d451e1d7c8cc65e68e4841f7bc35ad6bd27a3e559b7d58e7287e2a902c2d", "package/sleeveRules.py": "742cb3474bcd26bb071b69efca3d2cc830ff9ab71a656af720af3a0646f21e73", "package/sleeveTools.py": "cbe30aba9c94fc6348736f49fdcc81d02454ce1c53b7b9877f5409121fb3cbf2", "package/sleeves.py": "8bf6190636587893f7188a699d14da442ff1ad9ca5bf31b3c65ec254b95d6ddc", "package/types.py": "1a9cd1da911531e529e075127828459084865e8c666a8fcdbc12ac6215b69352", "package/universe.py": "e108fc548861d755e4bf816d2d5f09cb66b3ceda8997f4382223cbe2686aa092", "package/workbook.py": "3463b6f98d2823f1b2dccc9054d1562cff61c7b29d4df9dc1f1159d2bf51fbd2", "page/proposalTool.html": "632f85e61cfaf0cd9c907054bb0f4b6c45dadf3b4f0cc47725205505bf903a7b", "page/static/css/proposalTool.css": "54c90bb6a7e31323b39406e6c2a0e3f5ee14186273f6a20f90e38e31d0bd5788", "page/static/fonts/goldman-sans-regular.woff2": "b179269bb1ad16de0f55b5ee817786bd8caf97f2acd1c845508daebc5b3b3a4d", "page/static/fonts/gs-sans-condensed-variable.woff2": "908d9f6f0b1a54696e8c5248e552a211a5eec1f0274ba4f03bd4c8e1a8f4c498", "page/static/fonts/gs-sans-variable.woff2": "874e4006d1d1f33326e3eaeb4139111655edf46a9fd94aa6de712b85fafba739", "page/static/fonts/roboto-medium.woff2": "3a0fd062d0e8a03061c589536ac7d11f51635d0ca7c626bd48e315bfb4a104c7", "page/static/fonts/roboto-regular.woff2": "77bfc801d1366174a49f7ec56426950a0b6ad749dadfa23f901a5e786500ef59", "page/static/js/proposalTool.js": "2fb7c6fe9e46b7dc8adf9ccb7ba71b191ce49d8de74400a0351ab428650c64cb"}''')
TARGET = json.loads(r'''{"package/__init__.py": "da868ae634ab007e52e3031296d600c8cd0e1bbda61e53cac8c03477bcd0e683", "package/bake.py": "f4c4e7478f4cd16776473858c56312e7fe9341ea611d3d752b4a46c7d8331d9e", "package/bakedAdapter.py": "6869bbd66e2aca7cf0541219d0a3c4e637eba2f59968a5df37d669c6360a4bef", "package/csvText.py": "e08f8d719a838cc91246e687608b86e07e08b888ea58d323b68ef4de83c7ca3d", "package/fundingSplit.py": "739cf2476303f395731046425105fa008612b3882d7a5006131422f477041879", "package/overlayRules.py": "690150cb94de11dbfd152cce6b0e56523f5c038107129df3bcb8f72e2fc7af2b", "package/portfolio_weights.py": "fe5b348a206ef60624fd04477683c4569fab853edfe67ba939c70b8d900572f9", "package/pptWriter.py": "0160f2efa863c33b585ecf140f6f433bc66012b362dfd7989eec0d2323294254", "package/products.py": "bb5ad31fc2875a093a9ab4e7f77e24e3ac00c3de2582b3e4762478b105deb2d9", "package/registry.py": "26d42f5484e9006777c68a5049bbdd6320ce33b480652a640d7a573c4a655ad3", "package/rules.py": "fa4ec7604dc822651cee29b24de2ac6750f46934888abe90a9768d8c1f927d94", "package/scenarioPort.py": "78a563fe4af688a750e76dde65c8aac58f47461bd584789635e0e66bb900281c", "package/scenarioStore.py": "65e1a51dc8be316092c520660143c2c32391b2d2ae44de748c16a31c6b7c9710", "package/sheetDoc.py": "6791b4c7d52007ff10fc0801d4a89c1d9bf171b44aaa7efd77efd645305430b3", "package/sleeveRepo.py": "d503c751539776d79b4ff8e6dae6c6b165cc420393ccb12b834d5281104fc7fc", "package/sleeves.py": "667dc04753b86e63bb259b1b27afe97cf1fd0dc1f4f9e2017d585a6bf0e28834", "package/universe.py": "bdada4c55138114f4b31adc4705f294e8a6193f5b95de28c99d257062988c0b2", "package/workbook.py": "7d4b16b42a77ec84fe907d4f19dc3bb9be1d2f7b95233c96039d62b97c5bf5a8", "page/proposalTool.html": "322c7bb71fe92b9ba7e19dfe87d1228a38f3f29b20168d14f4873e19d971b2c7", "page/static/css/proposalTool.css": "0255895529944894503c511ea3087b1261653a338f855d675be80213557a3af7", "page/static/js/proposalTool.js": "517fb94661b3d01892da93640c721922c55dc2e843e44327fcb43c5fbc11f407"}''')
WRITE = json.loads(r'''["package/__init__.py", "package/bake.py", "package/bakedAdapter.py", "package/csvText.py", "package/fundingSplit.py", "package/overlayRules.py", "package/portfolio_weights.py", "package/pptWriter.py", "package/products.py", "package/registry.py", "package/rules.py", "package/scenarioPort.py", "package/scenarioStore.py", "package/sheetDoc.py", "package/sleeveRepo.py", "package/sleeves.py", "package/universe.py", "package/workbook.py", "page/proposalTool.html", "page/static/css/proposalTool.css", "page/static/js/proposalTool.js"]''')
DELETE = json.loads(r'''[]''')
HOST_OWNED = json.loads(r'''["advisors.xlsx"]''')
BAKE = json.loads(r'''{"from": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'var', 'baked')", "to": "os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'var', 'baked')"}''')
ROUTER_BLOCK = json.loads(r'''{"base": "49efab8918b723ea", "changed": false, "target": "49efab8918b723ea"}''')
ROUTER_DROP = json.loads(r'''["# ===== TRANSPLANT BLOCK BEGIN (PORTING.md \u00a79.1) ==============================", "# ===== TRANSPLANT BLOCK END ==================================================", "# names the Proposal Tool endpoint block needs since e948732"]''')
ROUTER_DROP_AFTER_BLANK = json.loads(r'''["# ===== TRANSPLANT BLOCK END =================================================="]''')
NEEDS = json.loads(r'''{"APIRouter": ["from", "fastapi", "APIRouter", null], "AnalyticsError": ["from", "cyrus_pmg.pmgService.scenario.types", "AnalyticsError", null], "BasisInput": ["from", "cyrus_pmg.pmgService.scenario.types", "BasisInput", null], "Body": ["from", "fastapi", "Body", null], "Depends": ["from", "fastapi", "Depends", null], "JSONResponse": ["from", "fastapi.responses", "JSONResponse", null], "MandateInput": ["from", "cyrus_pmg.pmgService.scenario.types", "MandateInput", null], "PortfolioKey": ["from", "cyrus_pmg.pmgService.scenario.types", "PortfolioKey", null], "Request": ["from", "fastapi", "Request", null], "Response": ["from", "fastapi", "Response", null], "ScenarioNotFound": ["from", "cyrus_pmg.pmgService.scenario.types", "ScenarioNotFound", null], "ValidationError": ["from", "cyrus_pmg.pmgService.scenario.types", "ValidationError", null], "accountRequests": ["from", "cyrus_pmg.pmgService.scenario", "accountRequests", null], "exportFilename": ["from", "cyrus_pmg.pmgService.scenario.rules", "exportFilename", null], "fees": ["from", "cyrus_pmg.pmgService.scenario", "fees", null], "fundingSplit": ["from", "cyrus_pmg.pmgService.scenario", "fundingSplit", null], "getScenarioPort": ["from", "cyrus_pmg.pmgService.scenario.registry", "getScenarioPort", null], "io": ["import", "io", null], "isAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "isAdmin", null], "json": ["import", "json", null], "overlayRules": ["from", "cyrus_pmg.pmgService.scenario", "overlayRules", null], "products": ["from", "cyrus_pmg.pmgService.scenario", "products", null], "proposalRegister": ["from", "cyrus_pmg.pmgService.scenario", "proposalRegister", null], "requireAdmin": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAdmin", null], "requireAuth": ["from", "cyrus_pmg.pmgService.core.accessControl", "requireAuth", null], "scenarioStore": ["from", "cyrus_pmg.pmgService.scenario", "scenarioStore", null], "sleeveExists": ["from", "cyrus_pmg.pmgService.scenario.sleeves", "sleeveExists", null], "sleeveRepo": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRepo", null], "sleeveRules": ["from", "cyrus_pmg.pmgService.scenario", "sleeveRules", null], "validateBasis": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateBasis", null], "validateCustomFees": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateCustomFees", null], "validateFeeLevel": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeLevel", null], "validateFeeSchedule": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateFeeSchedule", null], "validateKey": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateKey", null], "validateVariant": ["from", "cyrus_pmg.pmgService.scenario.rules", "validateVariant", null], "zipfile": ["import", "zipfile", null]}''')
ROUTES = int('42')
PAYLOAD = """
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
AQABADoAAACKCQAAAAA=
"""

if __name__ == '__main__':
    sys.exit(main())
