#!/usr/bin/env python3
"""Unpack the Proposal Tool paste bundle on a machine that reads GitHub only in a browser.

Put this file and every part-NN.txt in one folder, as PORTING_UPDATE.md step 1
describes, then run:

    python unbundle.py

It checks every part against the hash it was built with, names any part that
was pasted wrongly, and unpacks a copy of the repository's layout into the
folder proposal-tool beside this file. Nothing else is read or written.

Built from commit e948732 of 2026-10-14: 53 files, 6 parts.
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

SOURCE = 'e948732'
PARTS = [
    ('part-01.txt', 'cdff51673d259aae9dab329db521c0328f2b145361438810ff9958ea727c5e92', 150000),
    ('part-02.txt', '58223acb53d7a787c5da2500e80046d3ac4f5a910a11b995e90e310064164a20', 150000),
    ('part-03.txt', '36cca67600936e021f3c1ff42f28515986d6d19144d30ee2d7699b6173086ba0', 150000),
    ('part-04.txt', 'd2f4e871876fe7c62223047e3244e1842ea65e9de905f5adc430d798c4952840', 150000),
    ('part-05.txt', 'a5f5a9b3a4b16c1fb5b00d35378e192894b91e16c08c808e5b9bb2200660aca8', 150000),
    ('part-06.txt', 'b936102258d60a2f7376e29a63afdccdb56f8c266106a35172288d0003aef0e8', 132649),
]
ZIP_SHA256 = '04432441ba59d8df02fa5dd32e1da6308156ff908e2efd2de9ec8e96c75017e7'
FILES = 53

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
            problems.append('{}: missing - copy it from GitHub into this folder'.format(name))
            continue
        with open(path, 'rb') as fh:
            text = _NOT_BASE64.sub(b'', fh.read())
        try:
            data = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            problems.append('{}: not whole - paste it again (check the first and last lines)'.format(name))
            continue
        if not data:
            problems.append('{}: empty - paste it again'.format(name))
            continue
        if hashlib.sha256(data).hexdigest() != want:
            if len(data) == size:
                problems.append('{}: the right length but not the right text - it may hold another '
                                "part's text; copy {} from GitHub again".format(name, name))
            else:
                problems.append('{}: {:,} bytes where {:,} were built - paste it again; the first and '
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
        print('{} already exists. Delete or rename it and run again, so files from an older bundle '
              'cannot mix with these.'.format(out))
        return 1
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        broken = zf.testzip()
        if broken:
            print('{} is damaged inside the bundle. Nothing was unpacked.'.format(broken))
            return 1
        zf.extractall(out)
    print('Unpacked {} files from commit {} into {}'.format(FILES, SOURCE, out))
    print('Next: PORTING_UPDATE.md step 2, with <ep> = {}'.format(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
