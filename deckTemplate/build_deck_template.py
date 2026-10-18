"""Build the deck template: one slide carrying the house slide style.

The proposal deck is to be built by adding slides to this template rather than
by drawing its frame in code, so the template is the one place the deck's look
is kept (proposals/deck-reference-restyle.html, "The template"):

* the slide master: the thin rule across the top, and the master text styles;
* one layout, ``Content``: the title, the subtitle, the content box, the
  footnotes and the page number, each a placeholder in its place and
  dressed (no Proposal UID: it goes in the workbook only);
* the theme: the house face, GS Sans, for titles and text alike, and the
  colours each role takes - the rules, the navy box, the headings, the
  proposed column's tint;
* GS Sans itself, regular and bold, embedded, so the file shows true on any
  machine;
* one slide made from the layout, showing every text style and the table
  style as named samples (each shape is named ``Style · ...``), its notes
  ``tool: template only`` so no deck delivers it;
* a marker slide for each slide the tool builds, in the deck's running
  order, each with the notes line ``tool: <slide>`` (houseTemplate, D174).

A deck finds the layout by name and its placeholders by what they are, so the
file can be restyled - or replaced by a house template - in PowerPoint,
without a code change. A figure prints as the text around it does: the
reference's red and yellow only marked the figures to work out.

    cd proposal-tool/service
    PYTHONPATH=. python3 ../deckTemplate/build_deck_template.py

It writes ProposalDeck_Template.pptx beside this script. Once the file has been
edited in PowerPoint, the file is the source: running this again starts it over.
"""
import os
import uuid

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml import parse_xml
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt
from lxml import etree

from cyrus_pmg.pmgService.scenario import houseFonts, pptWriter, sheetDoc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'ProposalDeck_Template.pptx')

# ---- the canvas: the reference slides measure 4:3 -------------------------
SLIDE_W, SLIDE_H = 10.0, 7.5
MARGIN = 0.4
WIDTH = SLIDE_W - 2 * MARGIN

# ---- the theme: every colour a role, so the deck asks for the role ----------
THEME_NAME = 'Proposal Deck'
COLOURS = [            # (theme slot, value, role)
    ('dk1', '1D2733', 'text'),
    ('lt1', 'FFFFFF', 'background'),
    ('dk2', '0F243E', 'dark ink'),
    ('lt2', 'F1F4F9', 'light band'),
    ('accent1', '5B7DB1', 'the rules: the top rule and a section’s rule'),
    ('accent2', '132A7A', 'the navy box'),
    ('accent3', '143D8F', 'headings, portfolio names and the divider'),
    ('accent4', 'C21D3B', 'not used by the deck'),
    ('accent5', 'F3EC5B', 'not used by the deck'),
    ('accent6', 'BFD0F4', 'the proposed portfolio’s column'),
    ('hlink', '2E5FCB', 'links'),
    ('folHlink', '6E7E8F', 'visited links'),
]
SUBTITLE_INK = '4F6688'
NOTE_INK = '7C8B9C'
HAIR_INK = 'C9D1DC'
TITLE_FACE = sheetDoc.SANS                # the theme's major face: GS Sans, as the minor
TEXT_FACE = sheetDoc.SANS                 # the theme's minor face

# ---- the layout's placeholders: what a deck asks for, by index -------------
#: role -> (index, type, name, left, top, width, height in inches)
ROLES = {
    'title': (0, 'title', 'Title', MARGIN, 0.36, WIDTH, 0.48),
    'subtitle': (10, 'body', 'Subtitle', MARGIN, 0.82, WIDTH, 0.38),
    'content': (11, None, 'Content', MARGIN, 1.40, WIDTH, 4.90),
    'footnotes': (12, 'body', 'Footnotes', MARGIN, 6.40, WIDTH, 0.60),
    'uid': (13, 'ftr', 'Proposal UID', 5.6, 7.08, 3.45, 0.24),
    'page': (14, 'sldNum', 'Page number', 9.1, 7.08, 0.5, 0.24),
}
TOP_RULE_Y = 0.28
LAYOUT_NAME = 'Content'

_NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
       'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')


def emu(inches):
    return int(round(inches * 914400))


def xfrm(left, top, width, height):
    return ('<a:xfrm><a:off x="{}" y="{}"/><a:ext cx="{}" cy="{}"/></a:xfrm>'
            .format(emu(left), emu(top), emu(width), emu(height)))


def run(size, colour=None, face=None, bold=False, scheme=None):
    fill = ('<a:solidFill><a:schemeClr val="{}"/></a:solidFill>'.format(scheme) if scheme
            else '<a:solidFill><a:srgbClr val="{}"/></a:solidFill>'.format(colour) if colour else '')
    return '<a:defRPr sz="{}"{}>{}{}</a:defRPr>'.format(
        int(size * 100), ' b="1"' if bold else ' b="0"', fill,
        '<a:latin typeface="{}"/>'.format(face) if face else '')


# ---- the master --------------------------------------------------------------

def _masterTree():
    title = ROLES['title']
    body = ROLES['content']
    uid, page = ROLES['uid'], ROLES['page']
    return parse_xml(
        '<p:spTree {ns}><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
        '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        # the thin rule across the top of every slide
        '<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="7" name="Top rule"/><p:cNvCxnSpPr/><p:nvPr userDrawn="1"/>'
        '</p:nvCxnSpPr><p:spPr>{rule}<a:prstGeom prst="line"><a:avLst/></a:prstGeom>'
        '<a:ln w="15875"><a:solidFill><a:schemeClr val="accent1"/></a:solidFill></a:ln></p:spPr></p:cxnSp>'
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr>{tx}<a:prstGeom prst="rect"><a:avLst/>'
        '</a:prstGeom></p:spPr><p:txBody><a:bodyPr vert="horz" lIns="0" tIns="0" rIns="0" bIns="0" '
        'rtlCol="0" anchor="t"><a:noAutofit/></a:bodyPr><a:lstStyle/><a:p><a:r><a:rPr lang="en-GB"/>'
        '<a:t>Slide title</a:t></a:r></a:p></p:txBody></p:sp>'
        '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Text"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        '<p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr>{bx}<a:prstGeom prst="rect">'
        '<a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr vert="horz" lIns="0" tIns="0" rIns="0" '
        'bIns="0" rtlCol="0"><a:noAutofit/></a:bodyPr><a:lstStyle/>'
        '<a:p><a:pPr lvl="0"/><a:r><a:rPr lang="en-GB"/><a:t>Section heading</a:t></a:r></a:p>'
        '<a:p><a:pPr lvl="1"/><a:r><a:rPr lang="en-GB"/><a:t>Body text</a:t></a:r></a:p>'
        '<a:p><a:pPr lvl="2"/><a:r><a:rPr lang="en-GB"/><a:t>Bullet</a:t></a:r></a:p>'
        '<a:p><a:pPr lvl="3"/><a:r><a:rPr lang="en-GB"/><a:t>Note</a:t></a:r></a:p>'
        '<a:p><a:pPr lvl="4"/><a:r><a:rPr lang="en-GB"/><a:t>Sub-bullet</a:t></a:r></a:p>'
        '</p:txBody></p:sp>'
        '<p:sp><p:nvSpPr><p:cNvPr id="5" name="Footer"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        '<p:nvPr><p:ph type="ftr" sz="quarter" idx="3"/></p:nvPr></p:nvSpPr><p:spPr>{ux}'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr vert="horz" lIns="0" '
        'tIns="0" rIns="0" bIns="0" rtlCol="0" anchor="b"/><a:lstStyle><a:lvl1pPr algn="r">{uidRun}'
        '</a:lvl1pPr></a:lstStyle><a:p><a:endParaRPr lang="en-GB"/></a:p></p:txBody></p:sp>'
        '<p:sp><p:nvSpPr><p:cNvPr id="6" name="Slide number"/><p:cNvSpPr><a:spLocks noGrp="1"/>'
        '</p:cNvSpPr><p:nvPr><p:ph type="sldNum" sz="quarter" idx="4"/></p:nvPr></p:nvSpPr><p:spPr>{px}'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr vert="horz" lIns="0" '
        'tIns="0" rIns="0" bIns="0" rtlCol="0" anchor="b"/><a:lstStyle><a:lvl1pPr algn="r">{pageRun}'
        '</a:lvl1pPr></a:lstStyle><a:p><a:fld id="{{{fid}}}" type="slidenum"><a:rPr lang="en-GB"/>'
        '<a:t>‹#›</a:t></a:fld><a:endParaRPr lang="en-GB"/></a:p></p:txBody></p:sp>'
        '</p:spTree>'.format(
            ns=_NS, rule=xfrm(MARGIN, TOP_RULE_Y, WIDTH, 0),
            tx=xfrm(*title[3:]), bx=xfrm(*body[3:]), ux=xfrm(*uid[3:]), px=xfrm(*page[3:]),
            uidRun=run(6.5, NOTE_INK), pageRun=run(7, NOTE_INK), fid=str(uuid.uuid4()).upper()))


def _level(n, size, bold=False, scheme=None, colour=None, bullet=None, marL=0, indent=0,
           before=0, after=0):
    # the reference's small square: GS Sans has no glyph for it, so the marker
    # alone is drawn from Arial; the dash is GS Sans's own
    face = 'Arial' if bullet == '▪' else '+mn-lt'
    bu = ('<a:buFont typeface="{}"/><a:buChar char="{}"/>'.format(face, bullet) if bullet else '<a:buNone/>')
    return ('<a:lvl{n}pPr marL="{m}" indent="{i}" algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" '
            'latinLnBrk="0" hangingPunct="1"><a:lnSpc><a:spcPct val="100000"/></a:lnSpc>'
            '<a:spcBef><a:spcPts val="{b}"/></a:spcBef><a:spcAft><a:spcPts val="{a}"/></a:spcAft>{bu}'
            '<a:defRPr sz="{s}" b="{bold}" kern="1200"><a:solidFill>{fill}</a:solidFill>'
            '<a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/></a:defRPr>'
            '</a:lvl{n}pPr>').format(
                n=n, m=emu(marL), i=emu(indent), b=int(before * 100), a=int(after * 100), bu=bu,
                s=int(size * 100), bold=1 if bold else 0,
                fill=('<a:schemeClr val="{}"/>'.format(scheme) if scheme
                      else '<a:srgbClr val="{}"/>'.format(colour)))


#: the master's text levels, each a role a deck asks for by level
TEXT_LEVELS = [
    ('Section heading', dict(size=12, bold=True, scheme='accent3', after=3)),
    ('Body text', dict(size=11, scheme='tx1', before=2, after=2)),
    ('Bullet', dict(size=11, scheme='tx1', bullet='▪', marL=0.19, indent=-0.19, before=2, after=2)),
    ('Note', dict(size=9, colour=NOTE_INK, before=2)),
    ('Sub-bullet', dict(size=10, scheme='tx1', bullet='–', marL=0.38, indent=-0.19, before=1)),
]


def _txStyles():
    body = ''.join(_level(n, **spec) for n, (_, spec) in enumerate(TEXT_LEVELS, 1))
    # levels 6 to 9 carry level 5's dress, so nothing falls back to Office's
    body += ''.join(_level(n, **TEXT_LEVELS[-1][1]) for n in range(6, 10))
    return parse_xml(
        '<p:txStyles {ns}><p:titleStyle><a:lvl1pPr algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" '
        'latinLnBrk="0" hangingPunct="1"><a:lnSpc><a:spcPct val="90000"/></a:lnSpc><a:spcBef>'
        '<a:spcPct val="0"/></a:spcBef><a:buNone/><a:defRPr sz="2600" b="0" kern="1200"><a:solidFill>'
        '<a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mj-lt"/><a:ea typeface="+mj-ea"/>'
        '<a:cs typeface="+mj-cs"/></a:defRPr></a:lvl1pPr></p:titleStyle>'
        '<p:bodyStyle>{body}</p:bodyStyle>'
        '<p:otherStyle><a:defPPr><a:defRPr lang="en-GB"/></a:defPPr><a:lvl1pPr marL="0" algn="l" '
        'defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1"><a:defRPr sz="1100" '
        'kern="1200"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mn-lt"/>'
        '<a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/></a:defRPr></a:lvl1pPr></p:otherStyle>'
        '</p:txStyles>'.format(ns=_NS, body=body))


# ---- the layout ---------------------------------------------------------------

def _placeholder(shapeId, role, prompt, lst='', anchor='t', autofit='<a:noAutofit/>'):
    index, kind, name, left, top, width, height = ROLES[role]
    ph = '<p:ph{t}{i}{sz}{c}/>'.format(
        t=' type="{}"'.format(kind) if kind else '',
        i=' idx="{}"'.format(index) if index else '',
        sz=' sz="quarter"' if role in ('footnotes', 'uid', 'page') else '',
        c=' hasCustomPrompt="1"' if prompt and role not in ('uid', 'page') else '')
    if role == 'page':
        para = ('<a:p><a:fld id="{{{}}}" type="slidenum"><a:rPr lang="en-GB"/><a:t>‹#›</a:t></a:fld>'
                '</a:p>').format(str(uuid.uuid4()).upper())
    elif prompt:
        para = '<a:p><a:r><a:rPr lang="en-GB"/><a:t>{}</a:t></a:r></a:p>'.format(prompt)
    else:
        para = '<a:p><a:endParaRPr lang="en-GB"/></a:p>'
    return ('<p:sp><p:nvSpPr><p:cNvPr id="{id}" name="{name}"/><p:cNvSpPr><a:spLocks noGrp="1"/>'
            '</p:cNvSpPr><p:nvPr>{ph}</p:nvPr></p:nvSpPr><p:spPr>{x}</p:spPr><p:txBody><a:bodyPr '
            'lIns="0" tIns="0" rIns="0" bIns="0" anchor="{a}">{fit}</a:bodyPr><a:lstStyle>{lst}'
            '</a:lstStyle>{para}</p:txBody></p:sp>').format(
                id=shapeId, name=name, ph=ph, x=xfrm(left, top, width, height), a=anchor,
                fit=autofit, lst=lst, para=para)


def _layoutTree():
    plain = '<a:lvl1pPr marL="0" indent="0"><a:spcBef><a:spcPts val="0"/></a:spcBef><a:buNone/>{}</a:lvl1pPr>'
    subtitle = plain.format(run(18, SUBTITLE_INK, '+mj-lt'))
    notes = plain.format(run(7, NOTE_INK)).replace(
        '<a:spcBef><a:spcPts val="0"/></a:spcBef>', '<a:spcBef><a:spcPts val="200"/></a:spcBef>')
    shapes = ''.join([
        _placeholder(2, 'title', 'Slide title'),
        _placeholder(3, 'subtitle', 'Subtitle: the section, short', subtitle),
        _placeholder(4, 'content', 'The content box: tables, columns, boxes or text'),
        _placeholder(5, 'footnotes', '¹ Footnotes, numbered to the table, then the source line '
                     'and disclaimers', notes, anchor='b'),
        _placeholder(7, 'page', '‹#›'),
    ])
    return parse_xml(
        '<p:spTree {ns}><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
        '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>{}</p:spTree>'.format(shapes, ns=_NS))


# ---- the theme --------------------------------------------------------------

def _theme(presentation):
    theme = presentation.slide_master.part.part_related_by(RT.THEME)
    root = parse_xml(theme.blob)
    root.set('name', THEME_NAME)
    scheme = root.find('.//' + qn('a:clrScheme'))
    scheme.set('name', THEME_NAME)
    for slot, value, _ in COLOURS:
        holder = scheme.find(qn('a:' + slot))
        for child in list(holder):
            holder.remove(child)
        holder.append(parse_xml('<a:srgbClr {} val="{}"/>'.format(_NS, value)))
    root.find('.//' + qn('a:fontScheme')).set('name', THEME_NAME)
    theme._blob = etree.tostring(_gsSans(root), xml_declaration=True, encoding='UTF-8', standalone=True)


def _gsSans(root):
    """A theme's faces set to GS Sans, heading and body alike, and Office's
    per-script fallbacks taken out: the template names GS Sans and nothing
    else, but for the bullet's square, which GS Sans has no glyph for."""
    fonts = root.find('.//' + qn('a:fontScheme'))
    fonts.find(qn('a:majorFont')).find(qn('a:latin')).set('typeface', TITLE_FACE)
    fonts.find(qn('a:minorFont')).find(qn('a:latin')).set('typeface', TEXT_FACE)
    for which in ('a:majorFont', 'a:minorFont'):
        holder = fonts.find(qn(which))
        for script in holder.findall(qn('a:font')):
            holder.remove(script)
    return root


def _everyTheme(presentation):
    """The notes pages' theme too, which Office brings in with the first note."""
    for part in presentation.part.package.iter_parts():
        if not str(part.partname).startswith('/ppt/theme/') or \
                part is presentation.slide_master.part.part_related_by(RT.THEME):
            continue
        if hasattr(part, '_element'):            # held as XML: it saves from its element
            _gsSans(part._element)
        else:
            part._blob = etree.tostring(_gsSans(parse_xml(part.blob)), xml_declaration=True,
                                        encoding='UTF-8', standalone=True)


# ---- the one slide: the samples -------------------------------------------------

def _textbox(slide, name, left, top, width, height):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    box.name = name
    frame = box.text_frame
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.word_wrap = True
    return box, frame


def _say(paragraph, text, size, theme=None, rgb=None, bold=False, face='+mn-lt'):
    r = paragraph.add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.name = face
    if theme is not None:
        r.font.color.theme_color = theme
    elif rgb:
        r.font.color.rgb = RGBColor.from_string(rgb)
    return r


def _rule(slide, name, left, top, width, theme, weight):
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(left), Inches(top),
                                      Inches(left + width), Inches(top))
    line.name = name
    line.line.color.theme_color = theme
    line.line.width = Pt(weight)
    return line


def _sampleTable(slide):
    rows = [['', 'Proposed Allocation', 'For Comparison', ''],
            ['', 'Moderate-Aggressive', 'Moderate', 'Aggressive'],
            ['Fixed Income', '25.0%', '35.0%', '15.0%'],
            ['Investment Grade Fixed Income', '15.7%', '24.1%', '7.4%'],
            ['Other Fixed Income', '8.0%', '9.0%', '7.0%'],
            ['Total', '100%', '100%', '100%']]
    widths = [4.4, 1.6, 1.6, 1.6]
    frame = slide.shapes.add_table(len(rows), 4, Inches(MARGIN), Inches(4.48), Inches(sum(widths)),
                                   Inches(0.24 * len(rows)))
    frame.name = 'Style · Table'
    table = frame.table
    tblPr = table._tbl.tblPr
    for flag in ('firstRow', 'bandRow'):
        tblPr.attrib.pop(flag, None)
    style = tblPr.find(qn('a:tableStyleId'))
    if style is None:
        style = etree.SubElement(tblPr, qn('a:tableStyleId'))
    style.text = '{2D5ABB26-0587-4C30-8999-92F81FD0307C}'       # No Style, No Grid
    for index, w in enumerate(widths):
        table.columns[index].width = Inches(w)
    table.cell(0, 2).merge(table.cell(0, 3))
    for r, values in enumerate(rows):
        table.rows[r].height = Inches(0.24)
        for c, value in enumerate(values):
            cell = table.cell(r, c)
            if cell.is_spanned:
                continue
            cell.margin_left = cell.margin_right = Inches(0.06)
            cell.margin_top = cell.margin_bottom = Inches(0.01)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = cell.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.CENTER
            label = c == 0 and r in (3, 4)
            bold = r in (0, 2, 5) or (r == 1)
            colour = MSO_THEME_COLOR.ACCENT_3 if r == 1 else MSO_THEME_COLOR.TEXT_1
            if value:
                _say(p, ('    ' if label else '') + value, 9, colour, bold=bold)
            tinted = c == 1 and r >= 1
            if tinted:
                cell.fill.solid()
                cell.fill.fore_color.theme_color = MSO_THEME_COLOR.ACCENT_6
            else:
                cell.fill.background()
            edges = {}
            if r == 0 and c >= 2:
                edges['bottom'] = ('thin', '1D2733')
            elif r == 1 or r == 5:
                edges['bottom'] = ('thin', '1D2733')
            elif r >= 2:
                edges['bottom'] = ('hair', HAIR_INK)
            if r == 5:
                edges['top'] = ('thin', '1D2733')
            pptWriter._setCellBorders(cell, edges, 1.0)


#: the notes line that keeps this slide out of every deck (the plan's marks)
TEMPLATE_ONLY = 'tool: template only'


def _samples(presentation, layout):
    slide = presentation.slides.add_slide(layout)
    slide.notes_slide.notes_text_frame.text = TEMPLATE_ONLY
    placeholders = {p.placeholder_format.idx: p for p in slide.placeholders}
    for index, role in ((0, 'title'), (10, 'subtitle'), (12, 'footnotes')):
        placeholders[index].name = ROLES[role][2]
    placeholders[0].text = 'Slide title'
    placeholders[10].text = 'Subtitle: the section, short'
    notes = placeholders[12].text_frame
    notes.text = ('¹ A footnote, numbered to a superscript in the table. Footnotes sit here, small and '
                  'grey, above the page’s foot.')
    notes.add_paragraph().text = ('The source line and the disclaimers follow the footnotes, in the same '
                                  'style. This slide is the template: a deck adds its slides from the '
                                  'layout and leaves this one out.')
    # the content box shows the text levels, in the left column
    content = placeholders[11]
    content.left, content.top, content.width, content.height = (Inches(MARGIN), Inches(1.52),
                                                                Inches(4.3), Inches(2.6))
    frame = content.text_frame
    words = ['Section heading · level 1',
             'Body text · level 2: a paragraph of the slide’s wording, as on the reference’s narrative '
             'columns.',
             'Bullet · level 3, the small square the reference uses',
             'Note · level 4, for a line under a table or a box',
             'Sub-bullet · level 5']
    for n, text in enumerate(words):
        p = frame.paragraphs[0] if n == 0 else frame.add_paragraph()
        p.text = text
        p.level = n
    content.name = 'Content'
    _rule(slide, 'Style · Section rule', MARGIN, 1.42, 4.3, MSO_THEME_COLOR.ACCENT_1, 1.0)
    # the navy box with a yellow figure
    box = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(5.3), Inches(1.42), Inches(4.3), Inches(1.95))
    box.name = 'Style · Navy box'
    box.fill.solid()
    box.fill.fore_color.theme_color = MSO_THEME_COLOR.ACCENT_2
    box.line.fill.background()
    box.shadow.inherit = False
    tf = box.text_frame
    tf.margin_left = tf.margin_right = Inches(0.22)
    tf.margin_top = Inches(0.16)
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.word_wrap = True
    tf.paragraphs[0].alignment = PP_ALIGN.LEFT
    _say(tf.paragraphs[0], 'Estimated weighted average cost', 15, MSO_THEME_COLOR.BACKGROUND_1, face='+mj-lt')
    p = tf.add_paragraph()
    p.alignment = PP_ALIGN.LEFT
    p.space_before = Pt(8)
    _say(p, 'Advisory Fee', 11, MSO_THEME_COLOR.BACKGROUND_1)
    # a figure prints as the box's text does, white on navy
    p = tf.add_paragraph()
    p.alignment = PP_ALIGN.CENTER
    _say(p, '0.51%', 28, MSO_THEME_COLOR.BACKGROUND_1, bold=True)
    p = tf.add_paragraph()
    p.alignment = PP_ALIGN.LEFT
    _say(p, 'Based on a $25m portfolio', 11, MSO_THEME_COLOR.BACKGROUND_1)
    # the divider between two parts of a slide
    _rule(slide, 'Style · Divider', MARGIN, 4.3, WIDTH, MSO_THEME_COLOR.ACCENT_3, 0.75)
    _sampleTable(slide)
    # the page number, which a slide made from a layout does not take on its own
    tree = slide.shapes._spTree
    page = ROLES['page']
    tree.append(parse_xml(
        '<p:sp {ns}><p:nvSpPr><p:cNvPr id="91" name="Page number"/><p:cNvSpPr><a:spLocks noGrp="1"/>'
        '</p:cNvSpPr><p:nvPr><p:ph type="sldNum" sz="quarter" idx="{i}"/></p:nvPr></p:nvSpPr><p:spPr/>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:fld id="{{{f}}}" type="slidenum"><a:rPr lang="en-GB"/>'
        '<a:t>1</a:t></a:fld></a:p></p:txBody></p:sp>'.format(ns=_NS, i=page[0], f=str(uuid.uuid4()).upper())))
    return slide


#: a marker slide for each slide the tool builds, in the deck's running order
MARKERS = [
    ('cover', 'The cover'),
    ('comparing-allocations', 'Strategic Asset Allocation · Comparing Allocations'),
    ('risk-return', 'Strategic Asset Allocation · Risk and Return Analysis'),
    ('implemented-model', 'Implemented Model'),
    ('composition', 'Composition of the Implemented Model'),
    ('fee-structure', 'Portfolio Fee Structure'),
    ('assumptions', 'Long-Term Capital Market Assumptions'),
]


def _markers(presentation, layout, before=None):
    """One marker slide per tool slide: its title says what goes there, and
    its notes carry the mark the deck reads. *before* maps a marker to what
    makes the template's own slides that come before it."""
    for mark, title in MARKERS:
        if mark in (before or {}):
            before[mark](presentation, layout)
        slide = presentation.slides.add_slide(layout)
        slide.notes_slide.notes_text_frame.text = 'tool: ' + mark
        for ph in list(slide.placeholders):
            idx = ph.placeholder_format.idx
            if idx == 0:
                ph.text = 'Marker · ' + title
            elif idx == 10:
                ph.text = 'The tool puts its slide here; this one never reaches a deck'
            else:
                ph._element.getparent().remove(ph._element)


def _ownSlide(presentation, layout, title, notes, subtitle=None):
    slide = presentation.slides.add_slide(layout)
    slide.notes_slide.notes_text_frame.text = notes
    for ph in list(slide.placeholders):
        idx = ph.placeholder_format.idx
        if idx == 0:
            ph.text = title
        elif idx == 10 and subtitle:
            ph.text = subtitle
        else:
            ph._element.getparent().remove(ph._element)
    # its own footer: the page number
    tree = slide.shapes._spTree
    tree.append(parse_xml(
        '<p:sp {ns}><p:nvSpPr><p:cNvPr id="91" name="Page number"/><p:cNvSpPr><a:spLocks noGrp="1"/>'
        '</p:cNvSpPr><p:nvPr><p:ph type="sldNum" sz="quarter" idx="{i}"/></p:nvPr></p:nvSpPr><p:spPr/>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:fld id="{{{f}}}" type="slidenum"><a:rPr lang="en-GB"/>'
        '<a:t>‹#›</a:t></a:fld></a:p></p:txBody></p:sp>'.format(ns=_NS, i=ROLES['page'][0],
                                                               f=str(uuid.uuid4()).upper())))
    return slide


def _wording(slide, name, left, top, width, height, heading, lines):
    """A column of the house's wording: here a heading and stand-in lines."""
    box, frame = _textbox(slide, name, left, top, width, height)
    _say(frame.paragraphs[0], heading, 12, MSO_THEME_COLOR.ACCENT_3, bold=True)
    for line in lines:
        p = frame.add_paragraph()
        p.space_before = Pt(4)
        _say(p, line, 10, rgb=NOTE_INK)
    return box


def _navy(slide, name, left, top, width, height, lines):
    """A navy box of white text: (text, size, bold, centred) to a line."""
    box = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width),
                                 Inches(height))
    box.name = name
    box.fill.solid()
    box.fill.fore_color.theme_color = MSO_THEME_COLOR.ACCENT_2
    box.line.fill.background()
    box.shadow.inherit = False
    tf = box.text_frame
    tf.margin_left = tf.margin_right = Inches(0.2)
    tf.margin_top = Inches(0.14)
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.word_wrap = True
    for n, (text, size, bold, centred) in enumerate(lines):
        p = tf.paragraphs[0] if n == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.CENTER if centred else PP_ALIGN.LEFT
        if n:
            p.space_before = Pt(4)
        _say(p, text, size, MSO_THEME_COLOR.BACKGROUND_1, bold=bold)
    return box


_STANDIN = ['The house template’s own wording goes here, as it is on the reference.',
            'The deck leaves it as it is and fills in only the figures.']


def _block(slide, name, left, top, width, heading, lines):
    """One of Fees Summary's blocks: a heading under a rule, and its lines,
    figures as fields."""
    _rule(slide, name + ' rule', left, top, width, MSO_THEME_COLOR.ACCENT_1, 1.0)
    box, frame = _textbox(slide, name, left, top + 0.1, width, 2.1)
    _say(frame.paragraphs[0], heading, 12, MSO_THEME_COLOR.ACCENT_3, bold=True)
    for line in lines:
        p = frame.add_paragraph()
        p.space_before = Pt(5)
        _say(p, line, 10, MSO_THEME_COLOR.TEXT_1)
    return box


def _feesSummary(presentation, layout):
    """Fees Summary, as the reference words it: the weighted cost and the
    three charges the PWA enters on the scenario, each a field."""
    slide = _ownSlide(presentation, layout, 'Fees Summary', 'tool: when fees')
    half = (WIDTH - 0.4) / 2
    _block(slide, 'Annual Management Fees', MARGIN, 1.45, half, 'Annual Management Fees', [
        _STANDIN[0],
        'The weighted average cost of our proposal (taking into account both the product costs '
        'and advisory fees) is {{weightedCost}}.'])
    _block(slide, 'Foreign Exchange Commissions', MARGIN + half + 0.4, 1.45, half,
           'Foreign Exchange Commissions', [
               '▪ Trades over {{fxHighEdge}} (or currency equivalent): {{fxHighBps}} bps',
               '▪ Trades between {{fxLowEdge}} - {{fxHighEdge}} (or currency equivalent): '
               '{{fxMidBps}} bps',
               '▪ Trades under {{fxLowEdge}} (or currency equivalent): {{fxLowBps}} bps'])
    _block(slide, 'Transaction Fees', MARGIN, 3.95, half, 'Transaction Fees', [
        '▪ When we buy or sell equities (including ETFs), we charge {{equityTradeBps}} bps, '
        'levied as a percentage of the total value of the transaction.'])
    _block(slide, 'Custody Fees', MARGIN + half + 0.4, 3.95, half, 'Custody Fees', [
        '▪ We charge an annual custody fee of {{custodyFee}}.'])


def _feeSlides(presentation, layout):
    """The template's own fee slides, as a house template would carry them:
    its wording, with fields for the figures and an area for the tier table
    (decision 11 of the plan)."""
    slide = _ownSlide(presentation, layout, 'Fee Summary', 'tool: when fees')
    _wording(slide, 'Wording · Comprehensive Advisory Fee', MARGIN, 1.45, 2.9, 4.6,
             'Comprehensive Advisory Fee', _STANDIN)
    _wording(slide, 'Wording · Fee Structure', 3.5, 1.45, 2.9, 4.6, 'Fee Structure', _STANDIN)
    _navy(slide, 'Fee summary box', 6.6, 1.45, 3.0, 3.6, [
        ('Estimated weighted average cost', 14, False, False),
        ('Advisory Fee', 11, False, False), ('{{advisoryFee}}', 24, True, True),
        ('Product Cost', 11, False, False), ('{{productCost}}', 24, True, True),
        ('Total Cost', 11, False, False), ('{{totalCost}}', 24, True, True),
        ('Based on a {{mandateSize}} portfolio', 11, False, False)])
    slide = _ownSlide(presentation, layout, 'Advisory Fee Schedule', 'tool: when casp',
                      'Our Comprehensive Advisory Fee covers all of the following services:')
    for n, heading in enumerate(('Planning and Investment Management',
                                 'Monitoring, Evaluation and Reporting',
                                 'Ongoing Education and Research')):
        _wording(slide, 'Wording · ' + heading, MARGIN + n * 3.1, 1.45, 2.9, 2.3, heading,
                 _STANDIN)
    _navy(slide, 'Fee schedule box', MARGIN, 4.1, 3.6, 1.0,
          [('Comprehensive Advisory Fee Schedule', 14, False, False)])
    area = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(4.4), Inches(4.0), Inches(5.2),
                                  Inches(2.2))
    area.name = 'tool: fee-ladder'
    area.fill.background()
    area.line.color.theme_color = MSO_THEME_COLOR.ACCENT_1
    area.line.dash_style = 4                      # dashed: the area, not the slide's art
    area.shadow.inherit = False
    area.text_frame.text = 'tool: fee-ladder · the tier table is drawn here'
    notes, frame = _textbox(slide, 'Footnote', MARGIN, 6.45, WIDTH, 0.4)
    _say(frame.paragraphs[0], '¹ The house template’s footnote on the tiers goes here.', 7,
         rgb=NOTE_INK)


def build(path=OUT):
    presentation = Presentation()
    presentation.slide_width = Emu(emu(SLIDE_W))
    presentation.slide_height = Emu(emu(SLIDE_H))
    master = presentation.slide_master
    # one layout: keep the blank one, rebuilt as Content, and drop the rest
    keep = presentation.slide_layouts[6]
    for layout in list(presentation.slide_layouts):
        if layout is not keep:
            presentation.slide_layouts.remove(layout)
    element = keep._element
    element.attrib.pop('type', None)
    element.set('preserve', '1')
    csld = element.find(qn('p:cSld'))
    csld.set('name', LAYOUT_NAME)
    csld.remove(csld.find(qn('p:spTree')))
    csld.append(_layoutTree())
    # the master: the rule, the placeholders' places and the text styles
    mcsld = master._element.find(qn('p:cSld'))
    mcsld.remove(mcsld.find(qn('p:spTree')))
    mcsld.append(_masterTree())
    styles = master._element.find(qn('p:txStyles'))
    styles.addprevious(_txStyles())
    master._element.remove(styles)
    _theme(presentation)
    _samples(presentation, keep)
    _markers(presentation, keep, {'fee-structure': _feesSummary, 'assumptions': _feeSlides})
    _everyTheme(presentation)
    # GS Sans alone: the template sets nothing in another face
    faces = houseFonts.FACES
    houseFonts.FACES = {sheetDoc.SANS: faces[sheetDoc.SANS]}
    try:
        pptWriter._embedFonts(presentation)
    finally:
        houseFonts.FACES = faces
    props = presentation.core_properties
    props.title = 'Proposal deck template'
    props.subject = 'The house slide style every proposal deck is built from'
    props.author = 'Proposal Tool'
    props.keywords = 'template'
    presentation.save(path)
    return path


if __name__ == '__main__':
    print('wrote', build())
