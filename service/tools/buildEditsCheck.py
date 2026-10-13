#!/usr/bin/env python3
"""Build porting/edits-check.py: what a host changed since the commit it was ported from.

An update copies the package, the router block and the page folder over the
host's. Any edit made on the host itself - a reader made tolerant of the host's
own data files, a path, a fix - would be lost unless it is found first. The
host cannot clone this repository (its browser can only read GitHub), so the
check has to travel as one file it can copy from GitHub's page: this builds
that file, carrying a three-byte fingerprint of every line of every file as it
stood at the port, and the script compares the host's files with them line by
line and prints each line the host added or changed, in order, with its text.
A line the host removed is printed by its number in the original file, which
the maintainer reads back here with `git show <commit>:<path>`.

    python3 service/tools/buildEditsCheck.py a550821     # the commit Cyrus was ported from

Python 3.8 or later; standard library only.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
import textwrap
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, 'porting', 'edits-check.py')
PACKAGE = 'service/cyrus_pmg/pmgService/scenario/'
ROUTER = 'service/cyrus_pmg/pmgService/dashboardRouter.py'
PAGE = 'proposalTool/'
TEXT = ('.py', '.json', '.csv', '.html', '.css', '.js', '.md', '.txt')


def _git(*args, binary=False):
    out = subprocess.run(['git', '-C', ROOT] + list(args), capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode('utf-8')


def _lines(text):
    """The file's lines as the check reads them: no BOM, no CR."""
    return text.lstrip('﻿').replace('\r', '').split('\n')


def _lineHashes(lines):
    return b''.join(hashlib.sha256(l.encode('utf-8')).digest()[:3] for l in lines)


def _whole(data):
    return hashlib.sha256(data.replace(b'\r', b'')).hexdigest()[:16]


def _routerBlock(text):
    """The endpoint block: from the `_XLSX = ` line to the end of exportScenario."""
    lines = _lines(text)
    start = next(i for i, l in enumerate(lines) if l.startswith('_XLSX = '))
    export = next(i for i, l in enumerate(lines) if l.startswith('def exportScenario'))
    end = export + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][:1] in (' ', '\t')):
        end += 1
    while end > export and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit('usage: buildEditsCheck.py <the commit the host was ported from>')
    commit = _git('rev-parse', '--short', argv[0]).strip()
    date = _git('log', '-1', '--format=%ad', '--date=format:%d %B %Y', commit).strip()
    files = {}
    for prefix, area in ((PACKAGE, 'package'), (PAGE, 'page')):
        for path in _git('ls-tree', '-r', '--name-only', commit, prefix).split():
            rel = path[len(prefix):]
            if area == 'package' and '/' in rel and not rel.startswith('fonts/'):
                continue
            data = _git('show', commit + ':' + path, binary=True)
            entry = {'whole': _whole(data)}
            if rel.lower().endswith(TEXT):
                entry['lines'] = base64.b64encode(_lineHashes(_lines(data.decode('utf-8')))).decode('ascii')
            files[area + '/' + rel] = entry
    block = _routerBlock(_git('show', commit + ':' + ROUTER))
    files['router/block'] = {'whole': _whole('\n'.join(block).encode('utf-8')),
                             'lines': base64.b64encode(_lineHashes(block)).decode('ascii')}
    blob = base64.b64encode(zlib.compress(json.dumps(files, sort_keys=True).encode('ascii'), 9)).decode('ascii')
    data = '\n'.join(textwrap.wrap(blob, 76))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'editsCheckTemplate.py'),
              encoding='utf-8') as fh:
        template = fh.read()
    script = (template.replace('@@COMMIT@@', commit).replace('@@DATE@@', date)
              .replace('@@DATA@@', data))
    script.encode('ascii')                          # a paste must survive any editor
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='ascii', newline='\n') as fh:
        fh.write(script)
    print('porting/edits-check.py: {} files from {} ({}), {:,} bytes'.format(
        len(files), commit, date, len(script)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
