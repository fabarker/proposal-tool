#!/usr/bin/env python3
"""What the host changed in the Proposal Tool since it was ported. Read only.

Compares the host's scenario package, the endpoint block in its router and its
page folder with the files as they stood at commit @@COMMIT@@ (@@DATE@@), the
commit it was ported from, and prints every line the host added or changed,
in order, with its text. A line the host removed is shown by its number in the
original file. Nothing on the host is written except the report, which is
saved as edits-report.txt beside this script.

Run it from anywhere:

    python edits-check.py
    python edits-check.py --src H:\\cyrus-repo\\isg-cyrus-pmg\\src     (that is the default)

Built by service/tools/buildEditsCheck.py; Python 3.8 or later, standard
library only.
"""
import argparse
import base64
import difflib
import hashlib
import json
import os
import sys
import zlib

SOURCE = '@@COMMIT@@'
DEFAULT_SRC = r'H:\cyrus-repo\isg-cyrus-pmg\src'
PACKAGE = os.path.join('cyrus_pmg', 'pmgService', 'scenario')
ROUTER = os.path.join('cyrus_pmg', 'pmgService', 'dashboardRouter.py')
PAGE = os.path.join('cyrus_pmg', 'dashboard', 'proposalTool')
TEXT = ('.py', '.json', '.csv', '.html', '.css', '.js', '.md', '.txt')


def _lines(text):
    return text.lstrip('\ufeff').replace('\r', '').split('\n')


def _lineHashes(lines):
    return [hashlib.sha256(l.encode('utf-8')).digest()[:3] for l in lines]


def _whole(data):
    return hashlib.sha256(data.replace(b'\r', b'')).hexdigest()[:16]


def _routerBlock(lines):
    starts = [i for i, l in enumerate(lines) if l.startswith('_XLSX = ')]
    exports = [i for i, l in enumerate(lines) if l.startswith('def exportScenario')]
    if not starts or not exports:
        return None, None
    start, export = starts[0], exports[0]
    end = export + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][:1] in (' ', '\t')):
        end += 1
    while end > export and not lines[end - 1].strip():
        end -= 1
    return lines[start:end], start


def _changes(original, host, offset=0):
    """The host's edits, in order: (kind, text) pairs."""
    out = []
    hostHashes = _lineHashes(host)
    matcher = difflib.SequenceMatcher(None, original, hostHashes, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        if i2 > i1:
            out.append(('removed', 'original lines {}-{} ({} lines)'.format(i1 + 1, i2, i2 - i1)
                        if i2 - i1 > 1 else 'original line {}'.format(i1 + 1)))
        for j in range(j1, j2):
            out.append(('added', 'host line {:>5}: {}'.format(j + 1 + offset, host[j])))
    return out


def _host(path):
    with open(path, 'rb') as fh:
        return fh.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--src', default=DEFAULT_SRC, help="the folder holding the host's cyrus_pmg")
    args = ap.parse_args(argv)
    src = os.path.abspath(args.src)
    known = json.loads(zlib.decompress(base64.b64decode(''.join(DATA.split()))).decode('ascii'))
    report = []
    say = report.append
    say('Edits made on the host since the port (commit {}).'.format(SOURCE))
    say('Host source: {}'.format(src))
    edited, missing = [], []
    for area, folder in (('package', os.path.join(src, PACKAGE)), ('page', os.path.join(src, PAGE))):
        hostFiles = {}
        for here, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            if area == 'package' and os.path.abspath(here) != os.path.abspath(folder) \
                    and os.path.basename(here) != 'fonts':
                continue
            for name in names:
                if name.endswith('.pyc'):
                    continue
                rel = os.path.relpath(os.path.join(here, name), folder).replace(os.sep, '/')
                hostFiles[rel] = os.path.join(here, name)
        mine = {k[len(area) + 1:]: v for k, v in known.items() if k.startswith(area + '/')}
        same = [r for r in mine if r in hostFiles and _whole(_host(hostFiles[r])) == mine[r]['whole']]
        say('')
        say('== The {} ({})'.format(area, folder))
        say('   {} files as they were at the port; {} edited; {} not on the host; {} only on the host'.format(
            len(same), len([r for r in mine if r in hostFiles and r not in same]),
            len([r for r in mine if r not in hostFiles]), len([r for r in hostFiles if r not in mine])))
        for rel in sorted(mine):
            if rel not in hostFiles:
                say('   not on the host: ' + rel)
                missing.append(area + '/' + rel)
                continue
            if rel in same:
                continue
            edited.append(area + '/' + rel)
            say('')
            say('-- EDITED: {}'.format(rel))
            if 'lines' not in mine[rel]:
                say('   (a binary file: it differs from the port as a whole)')
                continue
            raw = base64.b64decode(mine[rel]['lines'])
            original = [raw[i:i + 3] for i in range(0, len(raw), 3)]
            host = _lines(_host(hostFiles[rel]).decode('utf-8', 'replace'))
            for kind, text in _changes(original, host):
                say(('   - ' if kind == 'removed' else '   + ') + text)
        for rel in sorted(r for r in hostFiles if r not in mine):
            say('   only on the host: ' + rel)
    say('')
    routerPath = os.path.join(src, ROUTER)
    say('== The router block ({})'.format(routerPath))
    if not os.path.isfile(routerPath):
        say('   no router at that path')
    else:
        lines = _lines(_host(routerPath).decode('utf-8', 'replace'))
        block, start = _routerBlock(lines)
        if block is None:
            say('   no block found: no line starts "_XLSX = " or "def exportScenario"')
        else:
            say('   the block is host lines {}-{}'.format(start + 1, start + len(block)))
            mine = known['router/block']
            if _whole('\n'.join(block).encode('utf-8')) == mine['whole']:
                say('   as it was at the port: no edits')
            else:
                edited.append('router/block')
                raw = base64.b64decode(mine['lines'])
                original = [raw[i:i + 3] for i in range(0, len(raw), 3)]
                for kind, text in _changes(original, block, offset=start):
                    say(('   - ' if kind == 'removed' else '   + ') + text)
    say('')
    say('Summary: {} edited ({}); {} not on the host.'.format(
        len(edited), ', '.join(edited) or 'none', len(missing)))
    text = '\n'.join(report) + '\n'
    print(text)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'edits-report.txt')
    try:
        with open(out, 'w', encoding='utf-8') as fh:
            fh.write(text)
        print('Saved to ' + out)
    except OSError as exc:
        print('Could not save the report: {}'.format(exc))
    return 0


DATA = """
@@DATA@@
"""

if __name__ == '__main__':
    sys.exit(main())
