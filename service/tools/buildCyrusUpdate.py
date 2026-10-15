#!/usr/bin/env python3
"""Build porting/update.py: one file that brings Cyrus from the commit it has to HEAD.

Cyrus reads GitHub only in a browser and its terminal takes no paste, so an
update reaches it as one file copied from GitHub's page and run there. This
builds that file from cyrusUpdateTemplate.py: the package and page files that
changed since the commit Cyrus has (porting/cyrus.json), as a zip written in
base 64, with the content hash of every file as it stands at that commit, so
the file refuses to overwrite anything edited on Cyrus, the new endpoint block
when it changed, and the imports it may need. Nothing in it names a commit, a
date or this repository, and it writes nothing on Cyrus but the code.

    python3 service/tools/buildCyrusUpdate.py            build porting/update.py
    python3 service/tools/buildCyrusUpdate.py --confirm  Cyrus has it: record that

The shipped paths must be committed first. --confirm moves porting/cyrus.json
to the commit the last build was made from (porting/update.json), so the next
update starts there. Python 3.8 or later; standard library only.
"""

import ast
import base64
import difflib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import textwrap
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE = os.path.join(ROOT, 'porting', 'cyrus.json')
OUT = os.path.join(ROOT, 'porting', 'update.py')
BUILT = os.path.join(ROOT, 'porting', 'update.json')
AREAS = {'package': 'service/cyrus_pmg/pmgService/scenario/', 'page': 'proposalTool/'}
ROUTER = 'service/cyrus_pmg/pmgService/dashboardRouter.py'
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
#: GitHub's file view and a clipboard both carry a few hundred KB comfortably
WARN_BYTES = 450000


def _git(*args, binary=False):
    out = subprocess.run(['git', '-C', ROOT] + list(args), capture_output=True, check=True).stdout
    return out if binary else out.decode('utf-8')


def _hash(data):
    return hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()


def _lines(text):
    return text.lstrip('﻿').replace('\r', '').split('\n')


def _blockRange(lines):
    start = next(i for i, l in enumerate(lines) if l.startswith('_XLSX = '))
    export = next(i for i, l in enumerate(lines) if l.startswith('def exportScenario'))
    end = export + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][:1] in (' ', '\t')):
        end += 1
    while end > export and not lines[end - 1].strip():
        end -= 1
    return start, end


def _text(data):
    """The lines of a text file, line ends aside, or None for anything else."""
    if data is None:
        return None
    try:
        return data.replace(b'\r\n', b'\n').decode('utf-8').split('\n')
    except UnicodeDecodeError:
        return None


def _whole(lines):
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:16]


def _tree(commit):
    """{key: bytes} for the package and the page at *commit*."""
    out = {}
    for area, prefix in AREAS.items():
        for path in _git('ls-tree', '-r', '--name-only', commit, prefix).split('\n'):
            if path and not path.endswith(('.pyc', '.DS_Store')):
                out[area + '/' + path[len(prefix):]] = _git('show', commit + ':' + path, binary=True)
    return out


def _needs(lines, block):
    """{name: import spec} for every top-level name the router imports outside the block."""
    tree = ast.parse('\n'.join(lines))
    out = {}
    for node in tree.body:
        if block[0] < node.lineno <= block[1]:
            continue
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split('.')[0]] = ['import', a.name, a.asname]
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                out[a.asname or a.name] = ['from', '.' * node.level + (node.module or ''), a.name,
                                           a.asname]
    return out


def build():
    with open(STATE, encoding='utf-8') as fh:
        state = json.load(fh)
    dirty = _git('status', '--porcelain', '--', *(list(AREAS.values()) + [ROUTER])).strip()
    if dirty:
        sys.exit('commit the shipped files first:\n' + dirty)
    base = _git('rev-parse', '--short', state['commit']).strip()
    head = _git('rev-parse', '--short', 'HEAD').strip()
    then, now = _tree(base), _tree(head)
    write = sorted(k for k in now if then.get(k) is None or _hash(then[k]) != _hash(now[k]))
    delete = sorted(k for k in then if k not in now)

    oldRouter, newRouter = _lines(_git('show', base + ':' + ROUTER)), _lines(_git('show', head + ':' + ROUTER))
    oldRange, newRange = _blockRange(oldRouter), _blockRange(newRouter)
    oldBlock, newBlock = oldRouter[oldRange[0]:oldRange[1]], newRouter[newRange[0]:newRange[1]]
    changed = oldBlock != newBlock
    routes = sum(1 for l in newBlock if re.match(r"@router\.\w+\('/scenario", l))

    # a text file Cyrus has is sent as the lines that changed - the update
    # checks the file is exactly the base before it applies them, and the
    # result against the target after - and anything else whole
    patches, whole = {}, []
    for key in write:
        old, new = _text(then.get(key)), _text(now[key])
        if old is None or new is None:
            whole.append(key)
            continue
        matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
        patches[key] = [[i1, i2, new[j1:j2]] for tag, i1, i2, j1, j2 in matcher.get_opcodes()
                        if tag != 'equal']
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for key in whole:
            info = zipfile.ZipInfo(key, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, now[key], compresslevel=9)
        info = zipfile.ZipInfo('patches.json', date_time=FIXED_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, json.dumps(patches, sort_keys=True).encode('utf-8'), compresslevel=9)
        if changed:
            info = zipfile.ZipInfo('router/block.txt', date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, '\n'.join(newBlock).encode('utf-8'), compresslevel=9)
    payload = '\n'.join(textwrap.wrap(base64.b64encode(buf.getvalue()).decode('ascii'), 76))

    fill = {
        'BASE': {k: _hash(v) for k, v in sorted(then.items())},
        'TARGET': {k: _hash(now[k]) for k in write},
        'WRITE': write,
        'DELETE': delete,
        'HOSTOWNED': state.get('hostOwned', []),
        'BAKE': state['bake'],
        'ROUTERBLOCK': {'changed': changed, 'base': _whole(oldBlock), 'target': _whole(newBlock)},
        'ROUTERDROP': state.get('routerDrop', []),
        'ROUTERDROPBLANK': state.get('routerDropAfterBlank', []),
        'NEEDS': _needs(newRouter, (newRange[0], newRange[1])),
    }
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cyrusUpdateTemplate.py'),
              encoding='utf-8') as fh:
        script = fh.read()
    for name, value in fill.items():
        script = script.replace('@@{}@@'.format(name), json.dumps(value, sort_keys=True))
    script = script.replace('@@ROUTES@@', str(routes)).replace('@@PAYLOAD@@', payload)
    script.encode('ascii')                          # a paste must survive any editor
    if re.search(r'@@[A-Z]+@@', script):
        sys.exit('a placeholder was left unfilled')
    with open(OUT, 'w', encoding='ascii', newline='\n') as fh:
        fh.write(script)
    with open(BUILT, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump({'from': base, 'to': head, 'write': write, 'delete': delete,
                   'routerBlock': changed, 'bytes': len(script)}, fh, indent=2)
        fh.write('\n')
    code = any(k.startswith('package/') for k in write + delete) or changed or fill['ROUTERDROP']
    print('porting/update.py: {} -> {}: {} to write ({} as changed lines), {} to delete{}; '
          '{:,} bytes{}'.format(
        base, head, len(write), len(patches), len(delete), ', the endpoint block' if changed else '',
        len(script), ' - LARGE for one copy' if len(script) > WARN_BYTES else ''))
    print('on Cyrus: ' + ('stop isgPMGService, run it with go, start isgPMGService, Ctrl+F5'
                          if code else 'run it with go, then Ctrl+F5 (the service can stay up)'))
    return 0


def confirm():
    with open(BUILT, encoding='utf-8') as fh:
        built = json.load(fh)
    with open(STATE, encoding='utf-8') as fh:
        state = json.load(fh)
    if _git('rev-parse', '--short', state['commit']).strip() != built['from']:
        sys.exit('porting/update.json was built from {}, but Cyrus is recorded at {}'.format(
            built['from'], state['commit']))
    state['commit'] = built['to']
    state['routerDrop'], state['routerDropAfterBlank'] = [], []
    with open(STATE, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write('\n')
    print('porting/cyrus.json: Cyrus now at {}'.format(built['to']))
    return 0


if __name__ == '__main__':
    sys.exit(confirm() if sys.argv[1:] == ['--confirm'] else build())
