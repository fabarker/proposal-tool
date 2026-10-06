#!/usr/bin/env python3
"""Unpack the Proposal Tool paste bundle on a machine that reads GitHub only in a browser.

Put this file and every part-NN.txt in one folder, as PORTING_UPDATE.md step 1
describes, then run:

    python unbundle.py

It checks every part against the hash it was built with, names any part that
was pasted wrongly, and unpacks a copy of the repository's layout into the
folder proposal-tool beside this file. Nothing else is read or written.

Built from commit fb14dd7 of 2026-10-06: 52 files, 6 parts.
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

SOURCE = 'fb14dd7'
PARTS = [
    ('part-01.txt', '77d59333246aa6d079cf8a98f8132459871def326107c215a033ee0b38898850', 150000),
    ('part-02.txt', 'c7ab042b858896d2ca71052a9b11c968d45e5cdef343963b859e86232392ca1d', 150000),
    ('part-03.txt', 'acdc9c87dd0bfbb91368f792b1af7b45d5f99e8a46d063a2a3d7a3746cb225dd', 150000),
    ('part-04.txt', 'aeae429e5571ceaeab0b0c5faea6273fc253b13b0c0abe64d702c9cd5210a506', 150000),
    ('part-05.txt', 'b4d2776f4f8e43d92d10856e968b35d2a3855bf73a7c37b707b689bb3ffabbdf', 150000),
    ('part-06.txt', '6fb0979054b226782c3362068283322b308d69b0bb3f024cca0e06413653c1c5', 130513),
]
ZIP_SHA256 = 'd046591b2dc3b18d82e35b86a8e3aff4f34a0485e5daf023f77cb71ad9a896b5'
FILES = 52

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
