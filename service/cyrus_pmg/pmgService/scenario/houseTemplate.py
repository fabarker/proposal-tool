"""The house slide template a deck is built in (D174).

A deck is not drawn on a blank canvas. It opens the template the setting
``SCENARIO_DECK_TEMPLATE`` names and adds every slide it makes from one of
the template's layouts - ``SCENARIO_DECK_LAYOUT``, *Content* unless told
otherwise - so the frame, the faces, the sizes and the colours are the
template's, and a change to the template is a change to every deck. No
template travels with this package: the deck reads whichever one the
setting names, and the deployment keeps its own.

The text the deck puts in a placeholder carries no formatting of its own,
and the roles - title, subtitle, content box, footnotes and the page
number - are found on the layout by what they are, not by where a
particular file happens to put them (``_roles``). The content box's place is
what the tables and charts are fitted into, so the canvas and the margins
are the template's too.

A template may hold slides of its own, which a deck leaves as they are. The
tool's slides go where the template marks them, and nowhere else:

* a MARKER SLIDE, whose speaker notes carry the line ``tool: <slide>``, is
  replaced by the tool's slide of that name - by several, one after another,
  where a table continues;
* a FIELD, ``{{name}}`` typed in any text, is replaced by the figure, which
  takes the field's own formatting;
* an AREA, a shape named ``tool: <table>``, is replaced by that table, drawn
  in the shape's place and size;
* a CONDITION, a notes line ``tool: when fees`` (``casp``, ``strategic``,
  ``private markets``), keeps its slide only for a proposal it applies to,
  and ``tool: template only`` keeps it out of every deck.

A slide without a mark comes out as it went in. A marker whose slide does
not apply is taken out, and so is a slide whose area has nothing to draw. A
tool slide the template has no marker for goes at the end. What the deck
cannot honour - a name it does not know, a field it has no figure for - is
refused with the slide named, so nothing half-filled is ever delivered. The
tool's own ``tool:`` lines are taken out of the notes of every delivered
slide; a slide's other notes stay.

``report`` reads a template without changing it and lists everything the
deck would use and anything it would refuse; run as a module it prints that
for the template the setting names, or for a path given:

    python -m cyrus_pmg.pmgService.scenario.houseTemplate [template.pptx]
"""

from __future__ import annotations

import copy
import os
import re
import sys
import uuid

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.opc.packuri import PackURI
from pptx.oxml import parse_xml
from pptx.oxml.ns import qn

from .types import ValidationError

TEMPLATE_SETTING = 'SCENARIO_DECK_TEMPLATE'
LAYOUT_SETTING = 'SCENARIO_DECK_LAYOUT'
DEFAULT_LAYOUT = 'Content'

#: the slides the tool builds whole, each put in at a marker slide of its name
SLIDES = ('cover', 'comparing-allocations', 'risk-return', 'implemented-model',
          'composition', 'fee-structure', 'assumptions')
#: the tables the tool can draw into an area on one of the template's slides
AREAS = ('comparing-allocations', 'risk-return', 'fee-ladder')
#: what a slide's presence may turn on
CONDITIONS = ('fees', 'casp', 'strategic', 'private markets')
TEMPLATE_ONLY = 'template only'
#: the figures a field may ask for
FIELDS = ('weightedCost', 'advisoryFee', 'productCost', 'totalCost', 'initialCost',
          'mandateSize', 'date', 'primaryPwa', 'minimumFee', 'custodyFee',
          'equityTradeBps', 'fxHighEdge', 'fxLowEdge', 'fxHighBps', 'fxMidBps', 'fxLowBps')

_PREFIX = 'tool:'
_FIELD = re.compile(r'\{\{\s*([^{}\s]+)\s*\}\}')
_EMU_PER_PT = 12700.0
#: a slide's sections live in presentation.xml's extension list
_SECTIONS_URI = '{521415D9-36F7-43E2-AB2F-B90AF26B5E84}'
_R_ID = qn('r:id')


class TemplateError(ValidationError):
    """The template, or what it asks of a proposal, cannot be honoured: the
    export refuses it as it refuses any other incomplete proposal (422)."""

    def __init__(self, message: str):
        super().__init__('deckTemplate', message)


def configuredPath():
    """The template the setting names, or None where none is set - in which
    case the deck is drawn as it was before templates (D174)."""
    return os.getenv(TEMPLATE_SETTING, '').strip() or None


def _box(shape):
    """A shape's place as (left, top, width, height) in points, or None."""
    values = (shape.left, shape.top, shape.width, shape.height)
    if any(v is None for v in values):
        return None
    return tuple(v / _EMU_PER_PT for v in values)


def _toolLine(text: str):
    """What follows ``tool:`` on a line, lower-cased with its spaces
    collapsed, or None for a line that is not a mark."""
    text = (text or '').strip()
    if not text.lower().startswith(_PREFIX):
        return None
    return ' '.join(text[len(_PREFIX):].split()).lower()


# ---- the layout's roles ------------------------------------------------------

_TITLES = (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE)
_BODIES = (PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT, PP_PLACEHOLDER.SUBTITLE)


def _roles(layout):
    """The layout's placeholders by role: title, subtitle, content,
    footnotes and page, each the layout placeholder that plays it. The
    Proposal UID is not on the slides: it goes in the workbook only.

    Found by what a placeholder is: the title by its type, the page number
    by its type. Of the text placeholders, a subtitle by its type or a name with
    "subtitle" in it, the footnotes by a name with "footnote" or "source" in
    it, the content box the largest of the rest - and of any text
    placeholders still left, one above the content box is the subtitle and
    one below it the footnotes. A role the layout does not have is absent."""
    roles = {}
    rest = []
    for ph in layout.placeholders:
        kind = ph.placeholder_format.type
        name = (ph.name or '').lower()
        if kind in _TITLES and 'title' not in roles:
            roles['title'] = ph
        elif kind == PP_PLACEHOLDER.SLIDE_NUMBER and 'page' not in roles:
            roles['page'] = ph
        elif kind in _BODIES:
            rest.append(ph)
    for ph in list(rest):
        name = (ph.name or '').lower()
        if 'subtitle' not in roles and (ph.placeholder_format.type == PP_PLACEHOLDER.SUBTITLE
                                        or 'subtitle' in name):
            roles['subtitle'] = ph
            rest.remove(ph)
        elif 'footnotes' not in roles and ('footnote' in name or 'source' in name):
            roles['footnotes'] = ph
            rest.remove(ph)
    sized = [ph for ph in rest if _box(ph)]
    if sized:
        content = max(sized, key=lambda ph: _box(ph)[2] * _box(ph)[3])
        roles['content'] = content
        rest.remove(content)
        top = _box(content)[1]
        for ph in rest:
            box = _box(ph)
            if box is None:
                continue
            if box[1] < top and 'subtitle' not in roles:
                roles['subtitle'] = ph
            elif box[1] >= top and 'footnotes' not in roles:
                roles['footnotes'] = ph
    return roles


# ---- reading the template's own slides ----------------------------------------

def _textFrames(shapes):
    """Every text frame among *shapes*: in shapes, in groups, in table cells."""
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            for frame in _textFrames(shape.shapes):
                yield frame
            continue
        if getattr(shape, 'has_text_frame', False) and shape.has_text_frame:
            yield shape.text_frame
        if getattr(shape, 'has_table', False) and shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    yield cell.text_frame


def _areaShapes(shapes, inGroup=False):
    """(shape, name, grouped) for every shape named ``tool: ...``."""
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            for found in _areaShapes(shape.shapes, True):
                yield found
            continue
        name = _toolLine(shape.name)
        if name is not None:
            yield shape, name, inGroup


def _notesFrame(slide):
    if not slide.has_notes_slide:
        return None
    return slide.notes_slide.notes_text_frame


class TemplateSlide:
    """One of the template's own slides and the marks it carries."""

    def __init__(self, slide, number: int, problems: list):
        self.slide = slide
        self.number = number
        self.marker = None
        self.templateOnly = False
        self.conditions = []
        self.areas = []                     # (shape, name)
        self.fields = []
        frame = _notesFrame(slide)
        lines = [p.text for p in frame.paragraphs] if frame is not None else []
        for line in lines:
            mark = _toolLine(line)
            if mark is None:
                continue
            if mark == TEMPLATE_ONLY:
                self.templateOnly = True
            elif mark.startswith('when '):
                condition = mark[len('when '):]
                if condition in CONDITIONS:
                    self.conditions.append(condition)
                else:
                    problems.append('Slide {}: "tool: {}" asks for a condition the tool does '
                                    'not know; it knows when {}.'.format(
                                        number, mark, ', when '.join(CONDITIONS)))
            elif mark in SLIDES:
                if self.marker and self.marker != mark:
                    problems.append('Slide {}: one marker to a slide, and it has two '
                                    '("{}" and "{}").'.format(number, self.marker, mark))
                self.marker = mark
            else:
                problems.append('Slide {}: "tool: {}" is not a slide the tool makes; the '
                                'slides are {}.'.format(number, mark, ', '.join(SLIDES)))
        for shape, name, grouped in _areaShapes(slide.shapes):
            if name not in AREAS:
                problems.append('Slide {}: the shape "{}" names a table the tool does not '
                                'draw; the tables are {}.'.format(number, shape.name,
                                                                  ', '.join(AREAS)))
            elif grouped:
                problems.append('Slide {}: the area "{}" is inside a group; ungroup it so '
                                'its place on the slide is its own.'.format(number, shape.name))
            else:
                self.areas.append((shape, name))
        for frame in _textFrames(slide.shapes):
            for paragraph in frame.paragraphs:
                for match in _FIELD.finditer(''.join(r.text for r in paragraph.runs)):
                    name = match.group(1)
                    if name not in FIELDS:
                        problems.append('Slide {}: the field {{{{{}}}}} is not one the tool '
                                        'fills; the fields are {}.'.format(
                                            number, name, ', '.join(FIELDS)))
                    elif name not in self.fields:
                        self.fields.append(name)

    def keepFor(self, context: dict) -> bool:
        """Whether the slide is delivered for a proposal described by
        *context* (condition -> bool): never a template-only slide, and a
        conditioned one only where every condition holds."""
        return not self.templateOnly and all(context.get(c) for c in self.conditions)


# ---- the template --------------------------------------------------------------

class Template:
    """A template, opened for one deck: its layout and the layout's roles,
    its canvas and content box, and its own slides with their marks. Any
    problem with it is listed in ``problems``; ``check`` refuses on the
    first."""

    def __init__(self, path: str, layoutName: str = None):
        self.path = path
        self.problems = []
        if not os.path.isfile(path):
            raise TemplateError('The deck template "{}" ({}) is not a file this service '
                                'can read.'.format(path, TEMPLATE_SETTING))
        try:
            self.presentation = Presentation(path)
        except Exception as exc:                     # not a deck at all
            raise TemplateError('The deck template "{}" cannot be opened as a PowerPoint '
                                'file ({}).'.format(path, exc))
        self.layoutName = (layoutName or os.getenv(LAYOUT_SETTING, '').strip()
                           or DEFAULT_LAYOUT)
        self.layouts = [layout for master in self.presentation.slide_masters
                        for layout in master.slide_layouts]
        self.layout = next((layout for layout in self.layouts
                            if layout.name == self.layoutName), None)
        self.roles = {}
        if self.layout is None:
            self.problems.append('The template has no layout named "{}"; its layouts are {}.'
                                 .format(self.layoutName, ', '.join(
                                     '"{}"'.format(layout.name) for layout in self.layouts)))
        else:
            self.roles = _roles(self.layout)
            for role in ('title', 'content'):
                if role not in self.roles:
                    self.problems.append('The layout "{}" has no {} placeholder.'.format(
                        self.layoutName, 'title' if role == 'title' else 'content'))
        self.size = (self.presentation.slide_width / _EMU_PER_PT,
                     self.presentation.slide_height / _EMU_PER_PT)
        self.box = _box(self.roles['content']) if 'content' in self.roles else None
        self.slides = [TemplateSlide(slide, number, self.problems)
                       for number, slide in enumerate(self.presentation.slides, 1)]
        self.markers = {s.marker for s in self.slides if s.marker}
        self.areas = {}
        for s in self.slides:
            for shape, name in s.areas:
                if name in self.areas:
                    self.problems.append('Slide {}: the area "{}" is the template\'s second; '
                                         'a table is drawn once.'.format(s.number, name))
                else:
                    self.areas[name] = _box(shape)

    def check(self) -> None:
        if self.problems:
            raise TemplateError('The deck template cannot be used. ' + ' '.join(self.problems))

    # ---- making a slide -------------------------------------------------------

    def addSlide(self, heading: str, sub: str = None, notes=(), body=None):
        """A slide added from the layout: *heading* in the title, *sub* in the
        subtitle and *notes* in the footnotes - each without formatting of its
        own, so the template's is what shows - and the page number
        placeholder put on, which a slide added from a layout does
        not take by itself. *body*, lines of text, goes in the content box;
        without it the content box comes off, for the tables and charts the
        deck draws in its place. A role the layout lacks is left out."""
        slide = self.presentation.slides.add_slide(self.layout)
        _uniquePartname(self.presentation, slide.part)
        byIdx = {ph.placeholder_format.idx: role for role, ph in self.roles.items()}
        notes = [n for n in (notes or []) if n]
        text = {'title': heading, 'subtitle': sub, 'footnotes': '\n'.join(notes),
                'content': '\n'.join(body) if body else None}
        for ph in list(slide.placeholders):
            role = byIdx.get(ph.placeholder_format.idx)
            if role == 'page':
                continue
            if text.get(role):
                ph.text_frame.text = text[role]
            else:
                element = ph._element
                element.getparent().remove(element)
        return slide

    def stamp(self, slide) -> None:
        """The page number on a slide the deck made."""
        layoutPh = self.roles.get('page')
        if layoutPh is None:
            return
        idx = layoutPh.placeholder_format.idx
        if idx in {ph.placeholder_format.idx for ph in slide.placeholders}:
            slide.placeholders[idx].text_frame.text = ''
            return
        _clonePlaceholder(slide, layoutPh, None)

    # ---- the marks ----------------------------------------------------------------

    def place(self, built: dict, order, context: dict, values: dict, drawArea) -> None:
        """Put the deck together: *built* is the slides the deck made, mark ->
        [slide], already added (at the end); *order* is the marks in the
        deck's own order. Each marker gives way to its slides, a marker or
        slide that does not apply comes out, every field is filled and every
        area drawn by *drawArea(slide, name, box)* - which says whether it
        had anything to draw - and what the template has no marker for goes
        at the end. Then the tool's notes lines come off and the page
        numbers are set."""
        presentation = self.presentation
        idList = presentation.slides._sldIdLst
        own = list(idList)[:len(self.slides)]
        slideIds = {}
        for mark, slides in built.items():
            slideIds[mark] = [_idOf(presentation, slide) for slide in slides]

        final, dropped = [], []
        replaced = {}
        for templateSlide, element in zip(self.slides, own):
            if not templateSlide.keepFor(context):
                dropped.append(element)
                continue
            if templateSlide.marker:
                made = slideIds.get(templateSlide.marker, [])
                final.extend(made)
                replaced[element.get('id')] = [e.get('id') for e in made]
                dropped.append(element)
                continue
            slide = templateSlide.slide
            if not _drawAreas(templateSlide, drawArea):
                dropped.append(element)
                continue
            _fillFields(slide, templateSlide.number, values)
            final.append(element)
        # a slide whose marker the template has goes there or nowhere; one
        # the template has no marker for goes at the end
        trailing = [e for mark in order if mark not in self.markers
                    for e in slideIds.get(mark, [])]
        final.extend(trailing)
        kept = {id(e) for e in final}
        dropped += [e for slides in slideIds.values() for e in slides if id(e) not in kept]

        for element in list(idList):
            idList.remove(element)
        for element in final:
            idList.append(element)
        _updateSections(presentation, replaced, [e.get('id') for e in dropped],
                        [e.get('id') for e in trailing])
        for element in dropped:
            _dropSlide(presentation, element)
        for number, slide in enumerate(presentation.slides, 1):
            _stripToolNotes(slide)
            _setPageNumbers(slide, number)


def _uniquePartname(presentation, part) -> None:
    """A new slide's part given a name no other part has. python-pptx names
    it by the number of slides, and a template whose slide files have gaps -
    one with a slide taken out, say - already has that name: two parts of
    one name make a file PowerPoint cannot open."""
    taken = {str(other.partname) for other in presentation.part.package.iter_parts()
             if other is not part}
    if str(part.partname) not in taken:
        return
    number = 1
    while '/ppt/slides/slide{}.xml'.format(number) in taken:
        number += 1
    part.partname = PackURI('/ppt/slides/slide{}.xml'.format(number))


def _idOf(presentation, slide):
    for element in presentation.slides._sldIdLst:
        if presentation.part.related_part(element.get(_R_ID)) is slide.part:
            return element
    raise ValueError('a slide the deck made is not in the presentation')


def _clonePlaceholder(slide, layoutPh, text):
    """A copy of the layout's placeholder on *slide*, positioned by the
    layout: the UID with *text*, or the page number as a field."""
    nvSpPr = copy.deepcopy(layoutPh._element.find(qn('p:nvSpPr')))
    tree = slide.shapes._spTree
    used = [int(e.get('id')) for e in tree.iter(qn('p:cNvPr')) if (e.get('id') or '').isdigit()]
    nvSpPr.find(qn('p:cNvPr')).set('id', str(max(used + [1]) + 1))
    for extra in nvSpPr.find(qn('p:cNvPr')).findall(qn('a:hlinkClick')):
        extra.getparent().remove(extra)
    if text is None:
        run = ('<a:fld id="{{{}}}" type="slidenum"><a:rPr lang="en-GB"/><a:t>‹#›</a:t>'
               '</a:fld>'.format(str(uuid.uuid4()).upper()))
    else:
        run = '<a:r><a:rPr lang="en-GB" dirty="0"/><a:t>{}</a:t></a:r>'.format(
            text.replace('&', '&amp;').replace('<', '&lt;'))
    sp = parse_xml(
        '<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:spPr/>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p>{}</a:p></p:txBody></p:sp>'.format(run))
    sp.insert(0, nvSpPr)
    tree.append(sp)


def _drawAreas(templateSlide, drawArea) -> bool:
    """Each of the slide's areas replaced by its table. False where one has
    nothing to draw, and the slide is then not delivered."""
    for shape, name in templateSlide.areas:
        box = _box(shape)
        element = shape._element
        element.getparent().remove(element)
        if not drawArea(templateSlide.slide, name, box):
            return False
    return True


def _fillParagraph(paragraph, values: dict, number: int) -> None:
    """Every field in *paragraph* replaced by its figure, which takes the
    formatting of the run the field begins in."""
    runs = list(paragraph.runs)
    texts = [r.text for r in runs]
    matches = list(_FIELD.finditer(''.join(texts)))
    if not matches:
        return
    starts = []
    position = 0
    for text in texts:
        starts.append(position)
        position += len(text)

    def runAt(offset, end=False):
        # the run holding the field's first character, or its last
        for index, text in enumerate(texts):
            if (starts[index] < offset <= starts[index] + len(text) if end
                    else starts[index] <= offset < starts[index] + len(text)):
                return index
        return len(runs) - 1

    for match in reversed(matches):
        name = match.group(1)
        if name not in FIELDS:
            raise TemplateError('Slide {}: the field {{{{{}}}}} is not one the tool fills.'
                                .format(number, name))
        value = values.get(name)
        if value is None:
            raise TemplateError('Slide {}: {{{{{}}}}} has no figure for this proposal{}.'.format(
                number, name, _MISSING.get(name, '')))
        start, end = match.span()
        first, last = runAt(start), runAt(end, end=True)
        head = runs[first].text[:start - starts[first]]
        tail = runs[last].text[end - starts[last]:]
        if first == last:
            runs[first].text = head + value + tail
        else:
            runs[first].text = head + value
            for index in range(first + 1, last):
                runs[index].text = ''
            runs[last].text = tail
        for index in range(first + 1, last + 1):
            if not runs[index].text:
                element = runs[index]._r
                element.getparent().remove(element)


#: why a field may have no figure
_MISSING = {
    'minimumFee': ': where the minimum advisory fee comes from is not decided yet',
    'custodyFee': ': the PWA has not entered the custody fee',
    'equityTradeBps': ': the PWA has not entered the equity transaction charge',
    'fxHighEdge': ': the PWA has not entered the FX commissions',
    'fxLowEdge': ': the PWA has not entered the FX commissions',
    'fxHighBps': ': the PWA has not entered the FX commissions',
    'fxMidBps': ': the PWA has not entered the FX commissions',
    'fxLowBps': ': the PWA has not entered the FX commissions',
    'initialCost': ': it holds no private markets',
    'weightedCost': ': it is not priced',
    'advisoryFee': ': it is not priced',
    'productCost': ': it is not priced',
    'totalCost': ': it is not priced',
}


def _fillFields(slide, number: int, values: dict) -> None:
    for frame in _textFrames(slide.shapes):
        for paragraph in frame.paragraphs:
            _fillParagraph(paragraph, values, number)


def _stripToolNotes(slide) -> None:
    """The tool's own lines out of the speaker notes; the rest stay."""
    frame = _notesFrame(slide)
    if frame is None:
        return
    body = frame._txBody
    paragraphs = body.findall(qn('a:p'))
    marked = [p for p, para in zip(paragraphs, frame.paragraphs)
              if _toolLine(para.text) is not None]
    for p in marked:
        body.remove(p)
    if not body.findall(qn('a:p')):
        body.append(body.makeelement(qn('a:p'), {}))


def _setPageNumbers(slide, number: int) -> None:
    """Every slide-number field's shown text set to the slide's place, so
    a viewer that does not work fields out still shows the right number."""
    for field in slide._element.iter(qn('a:fld')):
        if field.get('type') == 'slidenum':
            text = field.find(qn('a:t'))
            if text is not None:
                text.text = str(number)


def _updateSections(presentation, replaced: dict, dropped, trailing) -> None:
    """The template's sections kept true: a marker's id becomes its slides',
    a dropped slide leaves its section, and the slides at the end join the
    last section."""
    root = presentation.part._element
    lists = [e for e in root.iter() if isinstance(e.tag, str)
             and e.tag.endswith('}sldIdLst') and e.getparent() is not None
             and e.getparent().tag.endswith('}section')]
    if not lists:
        return
    gone = set(dropped)
    for sectionList in lists:
        for entry in list(sectionList):
            key = entry.get('id')
            if key in replaced:
                for newId in replaced[key]:
                    added = entry.makeelement(entry.tag, {'id': newId})
                    entry.addprevious(added)
            if key in gone or key in replaced:
                sectionList.remove(entry)
    last = lists[-1]
    for newId in trailing:
        tag = last[0].tag if len(last) else '{http://schemas.microsoft.com/office/powerpoint/2010/main}sldId'
        last.append(last.makeelement(tag, {'id': newId}))


def _dropSlide(presentation, element) -> None:
    """A slide out of the deck: off the slide list, out of any custom show,
    and its part no longer reached, so it is not written."""
    rId = element.get(_R_ID)
    root = presentation.part._element
    for show in root.iter(qn('p:sld')):
        if show.get(_R_ID) == rId:
            show.getparent().remove(show)
    if element.getparent() is not None:
        element.getparent().remove(element)
    presentation.part.drop_rel(rId)


# ---- the check ---------------------------------------------------------------------

def report(path: str = None, layoutName: str = None) -> dict:
    """What the deck would make of a template, read without changing it:
    the layout and each role's placeholder, the canvas and content box, the
    theme's faces and colours, and every slide with its marks - and the
    problems the deck would refuse it for."""
    path = path or configuredPath()
    if not path:
        return {'path': None, 'problems': ['{} is not set: the deck is drawn without a '
                                           'template.'.format(TEMPLATE_SETTING)]}
    template = Template(path, layoutName)
    roles = {}
    for role in ('title', 'subtitle', 'content', 'footnotes', 'page'):
        ph = template.roles.get(role)
        roles[role] = None if ph is None else {
            'name': ph.name, 'idx': ph.placeholder_format.idx,
            'type': str(ph.placeholder_format.type).split()[0], 'box': _box(ph)}
    slides = [{'number': s.number, 'marker': s.marker, 'templateOnly': s.templateOnly,
               'conditions': list(s.conditions), 'areas': [n for _, n in s.areas],
               'fields': list(s.fields)} for s in template.slides]
    missing = [name for name in SLIDES if name not in template.markers]
    return {'path': path, 'layout': template.layoutName,
            'layouts': [layout.name for layout in template.layouts],
            'size': template.size, 'box': template.box, 'roles': roles,
            'theme': _theme(template.presentation), 'slides': slides,
            'unmarked': missing, 'problems': list(template.problems)}


def _theme(presentation) -> dict:
    """The theme's heading and body faces and its colours, by name."""
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    part = presentation.slide_master.part.part_related_by(RT.THEME)
    root = parse_xml(part.blob)
    faces = {}
    for which, label in (('a:majorFont', 'headings'), ('a:minorFont', 'body')):
        found = root.find('.//' + qn(which))
        latin = found.find(qn('a:latin')) if found is not None else None
        faces[label] = latin.get('typeface') if latin is not None else None
    colours = {}
    scheme = root.find('.//' + qn('a:clrScheme'))
    for slot in (scheme if scheme is not None else []):
        if not isinstance(slot.tag, str):
            continue
        name = slot.tag.split('}')[-1]
        value = slot.find(qn('a:srgbClr'))
        system = slot.find(qn('a:sysClr'))
        colours[name] = (value.get('val') if value is not None
                         else system.get('lastClr') if system is not None else None)
    return {'faces': faces, 'colours': colours}


def _printReport(found: dict) -> int:
    out = sys.stdout.write
    if not found.get('path'):
        out('\n'.join(found['problems']) + '\n')
        return 1
    out('Template  {}\n'.format(found['path']))
    width, height = found['size']
    out('Canvas    {:.2f} x {:.2f} in\n'.format(width / 72.0, height / 72.0))
    out('Layout    "{}" (the template has: {})\n'.format(
        found['layout'], ', '.join('"{}"'.format(n) for n in found['layouts'])))
    if found['box']:
        left, top, w, h = found['box']
        out('Content   {:.2f}, {:.2f} in from the top left, {:.2f} x {:.2f} in\n'.format(
            left / 72.0, top / 72.0, w / 72.0, h / 72.0))
    out('\nRoles\n')
    for role, ph in found['roles'].items():
        out('  {:<10} {}\n'.format(role, 'none - left out' if ph is None else
                                   '"{}" (idx {}, {})'.format(ph['name'], ph['idx'], ph['type'])))
    theme = found['theme']
    out('\nTheme     headings {}, body {}\n'.format(theme['faces']['headings'],
                                                   theme['faces']['body']))
    out('Colours   {}\n'.format(', '.join('{} {}'.format(k, v)
                                          for k, v in theme['colours'].items())))
    out('\nSlides\n')
    for s in found['slides']:
        marks = []
        if s['templateOnly']:
            marks.append('template only')
        if s['marker']:
            marks.append('marker: ' + s['marker'])
        marks += ['when ' + c for c in s['conditions']]
        marks += ['area: ' + a for a in s['areas']]
        marks += ['{{' + f + '}}' for f in s['fields']]
        out('  {:>3}  {}\n'.format(s['number'], '; '.join(marks) if marks else 'left as it is'))
    if found['unmarked']:
        out('\nNo marker yet, so at the end of the deck: {}\n'.format(', '.join(found['unmarked'])))
    if found['problems']:
        out('\nProblems - the deck refuses the template until these are put right\n')
        for problem in found['problems']:
            out('  - {}\n'.format(problem))
        return 1
    out('\nNo problems.\n')
    return 0


if __name__ == '__main__':
    sys.exit(_printReport(report(sys.argv[1] if len(sys.argv) > 1 else None,
                                 sys.argv[2] if len(sys.argv) > 2 else None)))
