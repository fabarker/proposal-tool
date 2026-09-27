"""Regenerate scenario/fonts/*.ttf - the house faces as Office can use them.

Dev-only (D125). The web front end serves GS Sans and GS Sans Condensed as
variable woff2 files (generator/fonts/). Excel and PowerPoint want neither
variable fonts nor woff2: they want one static TrueType file per style,
named so that "GS Sans" plus bold finds the bold file. This pins each weight
the exports use, names the result, and writes plain .ttf files that can be
installed on a desktop and embedded in a deck.

Two liberties, both deliberate:

* The web files carry scrambled name tables. The names written here are the
  ones the front end's @font-face rules already use.
* GS Sans Condensed's default figures are proportional; the front end turns
  on tabular figures (font-variant-numeric: tabular-nums) wherever it sets
  numbers in it. Office has no switch for that, so the tabular figures are
  made the default here - a column of figures lines up on its points.

    python3 tools/buildOfficeFonts.py        (needs fontTools and brotli)
"""
import os

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.normpath(os.path.join(HERE, '..', '..', 'generator', 'fonts'))
OUT = os.path.normpath(os.path.join(HERE, '..', 'cyrus_pmg', 'pmgService', 'scenario', 'fonts'))

#: (source, weight, family, style, typographic family, typographic style, file)
#: A weight Office cannot reach as family + bold becomes a family of its own,
#: the way Calibri Light is.
FACES = [
    ('gs-sans-variable.woff2', 400, 'GS Sans', 'Regular', None, None, 'GSSans-Regular.ttf'),
    ('gs-sans-variable.woff2', 700, 'GS Sans', 'Bold', None, None, 'GSSans-Bold.ttf'),
    ('gs-sans-variable.woff2', 300, 'GS Sans Light', 'Regular', 'GS Sans', 'Light',
     'GSSans-Light.ttf'),
    ('gs-sans-condensed-variable.woff2', 400, 'GS Sans Condensed', 'Regular', None, None,
     'GSSansCondensed-Regular.ttf'),
    ('gs-sans-condensed-variable.woff2', 700, 'GS Sans Condensed', 'Bold', None, None,
     'GSSansCondensed-Bold.ttf'),
]
#: name records carried over from the source: copyright, version, maker,
#: designer and their URLs
KEEP_NAMES = (0, 5, 8, 9, 11, 12)


def tabularFigures(font):
    """Point the digits at their tnum alternates, if the font has any."""
    gsub = font['GSUB'].table
    mapping = {}
    for record in gsub.FeatureList.FeatureRecord:
        if record.FeatureTag != 'tnum':
            continue
        for index in record.Feature.LookupListIndex:
            for sub in gsub.LookupList.Lookup[index].SubTable:
                mapping.update(getattr(sub, 'mapping', {}) or {})
    digits = [ord(d) for d in '0123456789']
    for table in font['cmap'].tables:
        if not table.isUnicode():
            continue
        for code in digits:
            glyph = table.cmap.get(code)
            if glyph in mapping:
                table.cmap[code] = mapping[glyph]


def rename(font, family, style, typoFamily, typoStyle):
    name = font['name']
    kept = [record for record in name.names if record.nameID in KEEP_NAMES]
    name.names = kept
    version = font['head'].fontRevision
    full = family if style == 'Regular' else '{} {}'.format(family, style)
    postscript = '{}-{}'.format((typoFamily or family).replace(' ', ''),
                                typoStyle or style)
    records = {1: family, 2: style, 3: '{:.3f};{}'.format(version, postscript),
               4: full, 6: postscript}
    if typoFamily:
        records[16] = typoFamily
        records[17] = typoStyle
    for nameID, text in records.items():
        name.setName(text, nameID, 3, 1, 0x409)
        name.setName(text, nameID, 1, 0, 0)


def build(source, weight, family, style, typoFamily, typoStyle):
    # the source's own timestamp, so a rebuild writes the same bytes
    font = TTFont(os.path.join(SOURCE, source), recalcTimestamp=False)
    font.flavor = None
    font = instancer.instantiateVariableFont(font, {'wght': weight})
    for tag in ('STAT',):
        if tag in font:
            del font[tag]
    tabularFigures(font)
    rename(font, family, style, typoFamily, typoStyle)
    os2, head = font['OS/2'], font['head']
    os2.usWeightClass = weight
    # PANOSE weight: 3 light, 5 book, 8 bold - the variable file carries its
    # default instance's
    os2.panose.bWeight = {300: 3, 400: 5, 700: 8}[weight]
    bold = style == 'Bold'
    # fsSelection: bit 5 BOLD, bit 6 REGULAR; macStyle bit 0 bold
    os2.fsSelection = (os2.fsSelection & ~0x61) | (0x20 if bold else 0x40)
    head.macStyle = (head.macStyle & ~0x1) | (0x1 if bold else 0)
    return font


def main():
    os.makedirs(OUT, exist_ok=True)
    for source, weight, family, style, typoFamily, typoStyle, filename in FACES:
        font = build(source, weight, family, style, typoFamily, typoStyle)
        path = os.path.join(OUT, filename)
        font.save(path)
        print('wrote', os.path.relpath(path, os.path.join(HERE, '..')),
              os.path.getsize(path), 'bytes')


if __name__ == '__main__':
    main()
