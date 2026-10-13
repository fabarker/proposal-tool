#!/usr/bin/env python3
"""What the host changed in the Proposal Tool since it was ported. Read only.

Compares the host's scenario package, the endpoint block in its router and its
page folder with the files as they stood at commit a550821 (09 September 2026), the
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

SOURCE = 'a550821'
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
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
P5zntQQ=
"""

if __name__ == '__main__':
    sys.exit(main())
