#!/usr/bin/env python3
"""Unpack the Proposal Tool paste bundle on a machine that reads GitHub only in a browser.

Put this file and every part-NN.txt in one folder, as PORTING_UPDATE.md step 1
describes, then run:

    python unbundle.py

It checks every part against the hash it was built with, names any part that
was pasted wrongly, and unpacks a copy of the repository's layout into the
folder proposal-tool beside this file. Nothing else is read or written.

Built from commit 8a078dc of 2026-10-13: 53 files, 6 parts.
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

SOURCE = '8a078dc'
PARTS = [
    ('part-01.txt', 'e2de8cdd277f3502801fe68fb2f544ba704aaeaf05a0c390c63ebbb289bb1774', 150000),
    ('part-02.txt', '6a23e28baed50a6bde6f0313781cf782e34d93740e2f78d9f97df132e12b9531', 150000),
    ('part-03.txt', '06e65b829fe6f13e9a8c8325c85e77324244369ccaf8c74a28229dc180ed1d0e', 150000),
    ('part-04.txt', '8bbf42006d641ca27b32f633a2189d4db8ac41a0b985a91498b4aa0a0bb66cb6', 150000),
    ('part-05.txt', '308ca54c18caee78b4054f1baecf5d4135a3ace22f7d4b3f98f6a7829d2d0d4a', 150000),
    ('part-06.txt', 'f58f2d8754f9d67b26055bf494704218d9cf2d1d670a565232170f7db25e9b33', 132630),
]
ZIP_SHA256 = 'cadde3e066d994d932216a23a454b65665efdafc2c58b173cd1e9c88a0079f6f'
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
