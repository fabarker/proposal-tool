"""The house faces as files: which exist, where, and how a deck carries them.

D125. The exports are set in GS Sans and GS Sans Condensed (see
``sheetDoc.HOUSE_FACES``). The TrueType files in ``fonts/`` are written by
``tools/buildOfficeFonts.py`` from the web front end's own font files, and
serve three ends: installing on a desktop, so Excel shows the workbook in its
faces; measuring, for the deck's fit (``fontMetrics``); and embedding, so the
deck shows its faces on a machine that has never installed them.

A workbook cannot carry a font - the xlsx format has no place for one - so on
a machine without GS Sans, Excel substitutes. A deck can: PowerPoint stores
each embedded face as an Embedded OpenType (EOT) wrapper around the font
file, in a ``/ppt/fonts/*.fntdata`` part. ``eot`` writes that wrapper - the
W3C-submitted EOT layout, version 0x00020001, uncompressed and unencrypted -
from nothing but the font file, so this runs on a server with no font
tooling installed.
"""

from __future__ import annotations

import os
import struct

from .sheetDoc import CONDENSED, SANS, SANS_LIGHT

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')

#: typeface -> {slot: file}, the slots PresentationML names (regular, bold,
#: italic, boldItalic). GS Sans Light has no bold of its own: a bold Light
#: cell is set in GS Sans Bold (sheetDoc.houseFaces).
FACES = {
    SANS: {'regular': 'GSSans-Regular.ttf', 'bold': 'GSSans-Bold.ttf'},
    SANS_LIGHT: {'regular': 'GSSans-Light.ttf'},
    CONDENSED: {'regular': 'GSSansCondensed-Regular.ttf',
                'bold': 'GSSansCondensed-Bold.ttf'},
}

_EOT_VERSION = 0x00020001
_EOT_MAGIC = 0x504C
_DEFAULT_CHARSET = 1


def fontBytes(filename: str) -> bytes:
    with open(os.path.join(FONT_DIR, filename), 'rb') as handle:
        return handle.read()


def _tables(font: bytes) -> dict:
    """tag -> (offset, length), from the sfnt table directory."""
    count = struct.unpack_from('>H', font, 4)[0]
    tables = {}
    for index in range(count):
        tag, _, offset, length = struct.unpack_from('>4sIII', font, 12 + 16 * index)
        tables[tag.decode('latin-1')] = (offset, length)
    return tables


def _names(font: bytes, table) -> dict:
    """nameID -> text, from the Windows Unicode BMP records (3, 1, 0x409)."""
    base, _ = table
    _, count, storage = struct.unpack_from('>HHH', font, base)
    names = {}
    for index in range(count):
        platform, encoding, language, nameID, length, offset = struct.unpack_from(
            '>HHHHHH', font, base + 6 + 12 * index)
        if (platform, encoding, language) == (3, 1, 0x409):
            start = base + storage + offset
            names[nameID] = font[start:start + length].decode('utf-16-be')
    return names


def details(font: bytes) -> dict:
    """What a deck needs to say about a face besides its bytes."""
    tables = _tables(font)
    os2, _ = tables['OS/2']
    head, _ = tables['head']
    weight, = struct.unpack_from('>H', font, os2 + 4)
    fsType, = struct.unpack_from('>H', font, os2 + 8)
    panose = font[os2 + 32:os2 + 42]
    unicodeRange = struct.unpack_from('>4I', font, os2 + 42)
    fsSelection, = struct.unpack_from('>H', font, os2 + 62)
    codePageRange = struct.unpack_from('>2I', font, os2 + 78)
    checkSumAdjustment, = struct.unpack_from('>I', font, head + 8)
    return {'weight': weight, 'fsType': fsType, 'panose': panose,
            'unicodeRange': unicodeRange, 'codePageRange': codePageRange,
            'italic': bool(fsSelection & 0x1),
            'checkSumAdjustment': checkSumAdjustment,
            'names': _names(font, tables['name'])}


def eot(font: bytes) -> bytes:
    """*font* (a TrueType file) wrapped as Embedded OpenType, which is what
    PowerPoint keeps in a ``.fntdata`` part. Refuses a font whose licence
    bits (OS/2 fsType) forbid embedding it."""
    info = details(font)
    if info['fsType'] & 0x0002:
        raise ValueError('the font forbids embedding (fsType restricted licence)')
    names = info['names']

    def string(text):
        data = (text or '').encode('utf-16-le')
        return struct.pack('<HH', 0, len(data)) + data      # padding, size, bytes

    body = (info['panose']
            + struct.pack('<BBIHH', _DEFAULT_CHARSET, 1 if info['italic'] else 0,
                          info['weight'], info['fsType'], _EOT_MAGIC)
            + struct.pack('<4I', *info['unicodeRange'])
            + struct.pack('<2I', *info['codePageRange'])
            + struct.pack('<I', info['checkSumAdjustment'])
            + struct.pack('<4I', 0, 0, 0, 0)
            + string(names.get(1)) + string(names.get(2))
            + string(names.get(5)) + string(names.get(4))
            + string(''))                                   # no root string
    headerSize = 16 + len(body)
    return (struct.pack('<IIII', headerSize + len(font), len(font), _EOT_VERSION, 0)
            + body + font)
