#!/usr/bin/env python3
"""Build the paste bundle: everything a Cyrus update needs, as text a browser can carry.

The Cyrus machine reaches GitHub only through a browser - the firewall stops a
clone - so an update crosses by copy and paste. Pasting thirty files one at a
time is slow and easy to get wrong, the five deck fonts cannot be pasted at
all, and an editor can quietly re-encode a pasted Python file. So the files an
update needs are zipped, the zip is written as base-64 text in parts small
enough to copy from GitHub's file view, and `unbundle.py` - written here with
every part's hash inside it - checks each pasted part, names any that was
pasted wrongly, and unpacks a copy of this repository's layout on the other
side. PORTING_UPDATE.md, step 1, is the procedure.

Run on the development machine after the last change and before a port, then
commit `porting/bundle/` with the code it carries:

    python3 service/tools/buildPortBundle.py

The zip is deterministic - sorted entries, fixed timestamps - so the same
files always give the same parts, and a rebuild that changes nothing changes
no part. Python 3.8 or later; standard library only.
"""

import base64
import hashlib
import io
import os
import subprocess
import sys
import textwrap
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, 'porting', 'bundle')

#: what an update copies - folders whole, then single files
FOLDERS = [
    'service/cyrus_pmg/pmgService/scenario',     # the package
    'proposalTool',                               # the page folder
]
FILES = [
    'service/cyrus_pmg/pmgService/dashboardRouter.py',   # the block and its imports
    'service/tools/portCheck.py',                         # the before-and-after check
    'PORTING_UPDATE.md',                                  # the guide, to read offline
    'requirements.txt',                                   # the dependency list
]
SKIP_DIRS = {'__pycache__'}
SKIP_FILES = {'.DS_Store', 'Thumbs.db'}

#: raw bytes per part: a multiple of 3, so every part is whole base-64 on its
#: own, and about 200 KB of text - comfortable for GitHub's file view and a
#: clipboard
CHUNK = 150000
LINE = 76
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def _files():
    paths = []
    for folder in FOLDERS:
        base = os.path.join(ROOT, folder)
        for here, dirs, names in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(names):
                if name in SKIP_FILES or name.endswith('.pyc'):
                    continue
                paths.append(os.path.relpath(os.path.join(here, name), ROOT).replace(os.sep, '/'))
    for rel in FILES:
        if not os.path.isfile(os.path.join(ROOT, rel)):
            sys.exit('missing {} - nothing written'.format(rel))
        paths.append(rel)
    return sorted(set(paths))


def _zip(paths):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in paths:
            info = zipfile.ZipInfo(rel, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(os.path.join(ROOT, rel), 'rb') as fh:
                zf.writestr(info, fh.read(), compresslevel=9)
    return buf.getvalue()


def _git(*args):
    try:
        return subprocess.run(['git', '-C', ROOT] + list(args), capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ''


def _sha(data):
    return hashlib.sha256(data).hexdigest()


UNBUNDLE = '''\
#!/usr/bin/env python3
"""Unpack the Proposal Tool paste bundle on a machine that reads GitHub only in a browser.

Put this file and every part-NN.txt in one folder, as PORTING_UPDATE.md step 1
describes, then run:

    python unbundle.py

It checks every part against the hash it was built with, names any part that
was pasted wrongly, and unpacks a copy of the repository's layout into the
folder proposal-tool beside this file. Nothing else is read or written.

Built from commit {commit} of {date}{dirty}: {files} files, {count} parts.
Python 3.8 or later, standard library only.
"""
import base64
import binascii
import hashlib
import io
import os
import re
import sys
import zipfile

SOURCE = '{commit}'
PARTS = [
{parts}
]
ZIP_SHA256 = '{zipsha}'
FILES = {files}

# A paste can add a byte-order mark, CRLF line ends or trailing spaces. None of
# them is base-64, so each part is read as its base-64 characters alone.
_NOT_BASE64 = re.compile(rb'[^A-Za-z0-9+/=]')


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, 'proposal-tool')
    problems = []
    blob = b''
    for name, want, size in PARTS:
        path = os.path.join(here, name)
        if not os.path.isfile(path):
            problems.append('{{}}: missing - copy it from GitHub into this folder'.format(name))
            continue
        with open(path, 'rb') as fh:
            text = _NOT_BASE64.sub(b'', fh.read())
        try:
            data = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            problems.append('{{}}: not whole - paste it again (check the first and last lines)'.format(name))
            continue
        if not data:
            problems.append('{{}}: empty - paste it again'.format(name))
            continue
        if hashlib.sha256(data).hexdigest() != want:
            if len(data) == size:
                problems.append('{{}}: the right length but not the right text - it may hold another '
                                "part's text; copy {{}} from GitHub again".format(name, name))
            else:
                problems.append('{{}}: {{:,}} bytes where {{:,}} were built - paste it again; the first and '
                                'last lines are the usual casualties'.format(name, len(data), size))
            continue
        blob += data
    if problems:
        for line in problems:
            print(line)
        print('Nothing was unpacked.')
        return 1
    if hashlib.sha256(blob).hexdigest() != ZIP_SHA256:
        print('Every part matches but the whole does not: check each was saved under its own name.')
        return 1
    if os.path.exists(out):
        print('{{}} already exists. Delete or rename it and run again, so files from an older bundle '
              'cannot mix with these.'.format(out))
        return 1
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        broken = zf.testzip()
        if broken:
            print('{{}} is damaged inside the bundle. Nothing was unpacked.'.format(broken))
            return 1
        zf.extractall(out)
    print('Unpacked {{}} files from commit {{}} into {{}}'.format(FILES, SOURCE, out))
    print('Next: PORTING_UPDATE.md step 2, with <ep> = {{}}'.format(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
'''

README = '''\
# The paste bundle

Everything a Cyrus update copies - the `scenario` package, the page folder, the
router, `portCheck.py`, `PORTING_UPDATE.md` and `requirements.txt` - zipped and
written as text, because the Cyrus machine reaches GitHub only through a
browser. **`PORTING_UPDATE.md`, step 1, is the procedure.**

Built from commit `{commit}` of {date}{dirty}: {files} files in {count} parts.
Copy each of these into one folder on the Cyrus machine, under exactly this
name, then run `python unbundle.py` there:

| File | Size |
|---|---|
| `unbundle.py` | {unsize} |
{rows}

`unbundle.py` checks every part against the hash it was built with and names
any part to paste again; nothing is unpacked until all of them match.

Rebuild after any change, before a port: `python3 service/tools/buildPortBundle.py`.
'''


def _kb(n):
    return '{:,} KB'.format((n + 1023) // 1024)


def main():
    paths = _files()
    blob = _zip(paths)
    chunks = [blob[i:i + CHUNK] for i in range(0, len(blob), CHUNK)]
    commit = _git('rev-parse', '--short', 'HEAD') or 'unknown'
    date = _git('log', '-1', '--format=%ad', '--date=short') or 'unknown'
    dirty = _git('status', '--porcelain', '--', *(FOLDERS + FILES))
    dirtyNote = ' plus changes not yet committed' if dirty else ''

    os.makedirs(OUT, exist_ok=True)
    for name in os.listdir(OUT):
        if name.startswith('part-') and name.endswith('.txt'):
            os.remove(os.path.join(OUT, name))

    parts = []
    for i, chunk in enumerate(chunks, start=1):
        name = 'part-{:02d}.txt'.format(i)
        text = base64.b64encode(chunk).decode('ascii')
        body = '\n'.join(textwrap.wrap(text, LINE, break_on_hyphens=False)) + '\n'
        with open(os.path.join(OUT, name), 'w', encoding='ascii', newline='\n') as fh:
            fh.write(body)
        parts.append((name, _sha(chunk), len(chunk), len(body)))

    unbundle = UNBUNDLE.format(
        commit=commit, date=date, dirty=dirtyNote, files=len(paths), count=len(parts),
        parts='\n'.join("    ('{}', '{}', {}),".format(n, s, b) for n, s, b, _ in parts),
        zipsha=_sha(blob))
    with open(os.path.join(OUT, 'unbundle.py'), 'w', encoding='ascii', newline='\n') as fh:
        fh.write(unbundle)
    readme = README.format(
        commit=commit, date=date, dirty=dirtyNote, files=len(paths), count=len(parts),
        unsize=_kb(len(unbundle)), rows='\n'.join('| `{}` | {} |'.format(n, _kb(t)) for n, _, _, t in parts))
    with open(os.path.join(OUT, 'README.md'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(readme)

    print('porting/bundle: {} files, zip {:,} bytes, {} parts, from {}{}'.format(
        len(paths), len(blob), len(parts), commit, dirtyNote))
    return 0


if __name__ == '__main__':
    sys.exit(main())
