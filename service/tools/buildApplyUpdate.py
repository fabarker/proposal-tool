#!/usr/bin/env python3
"""Build porting/apply-update.py: the update applied on the host by one script.

The host's terminal takes no paste and its GitHub is a browser page, so the
backup, the copies, the router edit and the database upgrade of
PORTING_UPDATE.md steps 5 to 10 cannot reach it as commands to type. They
travel as one file copied from GitHub's page instead: this builds it from
applyUpdateTemplate.py, carrying the SHA-256 of every file the paste bundle
unpacks (so it acts only on the bundle it was built for), the fingerprint of
the endpoint block as the host was ported (so it replaces only a block nobody
has edited since), and the package files retired since then.

    python3 service/tools/buildApplyUpdate.py a550821     # the commit Cyrus was ported from

Run it after buildPortBundle.py, and commit both. Python 3.8 or later;
standard library only.
"""

import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUNDLE = os.path.join(ROOT, 'porting', 'bundle')
OUT = os.path.join(ROOT, 'porting', 'apply-update.py')
PACKAGE = 'service/cyrus_pmg/pmgService/scenario/'
ROUTER = 'service/cyrus_pmg/pmgService/dashboardRouter.py'


def _git(*args):
    return subprocess.run(['git', '-C', ROOT] + list(args), capture_output=True, check=True
                          ).stdout.decode('utf-8')


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


def bundleZip():
    """(commit, the bundle's zip) - the parts as committed, checked as unbundle.py checks them."""
    with open(os.path.join(BUNDLE, 'unbundle.py'), encoding='ascii') as fh:
        unbundle = fh.read()
    commit = re.search(r"^SOURCE = '(\w+)'", unbundle, re.M).group(1)
    want = re.search(r"^ZIP_SHA256 = '(\w+)'", unbundle, re.M).group(1)
    names = re.findall(r"^    \('(part-\d+\.txt)', '\w+', \d+\),", unbundle, re.M)
    blob = b''
    for name in names:
        with open(os.path.join(BUNDLE, name), 'rb') as fh:
            blob += base64.b64decode(re.sub(rb'[^A-Za-z0-9+/=]', b'', fh.read()), validate=True)
    if hashlib.sha256(blob).hexdigest() != want:
        sys.exit('porting/bundle does not reassemble to the zip unbundle.py names - rebuild it')
    return commit, blob


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit('usage: buildApplyUpdate.py <the commit the host was ported from>')
    base = _git('rev-parse', '--short', argv[0]).strip()
    commit, blob = bundleZip()
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        manifest = {n: hashlib.sha256(zf.read(n)).hexdigest() for n in sorted(zf.namelist())
                    if not n.endswith('/')}
        router = _lines(zf.read(ROUTER).decode('utf-8'))
    oldRouter = _lines(_git('show', base + ':' + ROUTER))
    start, end = _blockRange(oldRouter)
    oldBlock = hashlib.sha256('\n'.join(oldRouter[start:end]).encode('utf-8')).hexdigest()[:16]
    start, end = _blockRange(router)
    routes = sum(1 for l in router[start:end] if re.match(r"@router\.\w+\('/scenario", l))
    then = {p[len(PACKAGE):] for p in _git('ls-tree', '-r', '--name-only', base, PACKAGE).split()}
    now = {p[len(PACKAGE):] for p in manifest if p.startswith(PACKAGE)}
    retired = sorted(r for r in then - now if '/' not in r)

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'applyUpdateTemplate.py'),
              encoding='utf-8') as fh:
        template = fh.read()
    manifestText = '{\n' + ',\n'.join('  "{}": "{}"'.format(k, v) for k, v in manifest.items()) + '\n}'
    script = (template.replace('@@COMMIT@@', commit).replace('@@BASE@@', base)
              .replace('@@MANIFEST@@', manifestText).replace('@@OLDBLOCK@@', oldBlock)
              .replace('@@RETIRED@@', json.dumps(retired)).replace('@@ROUTES@@', str(routes)))
    script.encode('ascii')                          # a paste must survive any editor
    with open(OUT, 'w', encoding='ascii', newline='\n') as fh:
        fh.write(script)
    print('porting/apply-update.py: bundle {} ({} files), host ported from {}, {} retired, '
          '{} endpoints, {:,} bytes'.format(commit, len(manifest), base, len(retired), routes,
                                            len(script)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
