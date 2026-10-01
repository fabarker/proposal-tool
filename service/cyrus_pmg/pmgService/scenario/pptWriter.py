"""The deck renderer - the SheetDocs as PowerPoint, natively (D121).

Phase 3 of the PowerPoint export plan (proposals/powerpoint-export-plan.html):
``writeDeck`` renders the same Docs the Excel renderer transcribes - built by
``sheetDoc``'s builders, paginated by ``sheetDoc.paginate`` - as one slide per
page: a heading, a subheading, the table in the report's own faces, fills and
rules, footnotes, and the Proposal UID in the footer and the file's core
properties (``dc:identifier``, so ``stampedProposalId`` reads a deck exactly
as it reads a workbook). The deck and the workbook are one delivery - one
export, one UID, both recorded (D123) - and the deck is locked like the
workbook: a password to modify and Mark as Final (``_protectDeck``).

Like ``workbook.py``, this module decides nothing about the report: every
colour, face, weight, border and number format comes from the Doc, and the
number formats are rendered to text by ``sheetDoc.renderNumber`` because a
PowerPoint table cell holds text. The two helpers that write raw XML exist
because python-pptx has no first-class API for table cell borders or for a
doughnut's hole size; each writes only what the Doc or the chart spec says.

The stress block's red/green is the Doc's sign rule resolved per cell from
the RAW value with strict inequalities - a -0.04% that prints as -0.0% is
red, an exact zero keeps the base ink - which is precisely how the Excel
renderer's CellIsRule behaves.

Scale is the workbook's own print convention transplanted: a page's table
scales to the slide's content box by ONE factor - fonts, row heights and
column widths together, ``fitToWidth=1`` for a fixed canvas - so proportions
survive even where absolute sizes cannot.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import io
import os
import struct

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.opc.package import Part
from pptx.opc.packuri import PackURI
from pptx.oxml import parse_xml
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from . import houseFonts, sheetDoc
from .fontMetrics import METRICS as _METRICS
from .sheetDoc import SheetDoc, paginate, renderNumber
# the measurer lives with the Docs now, which size Excel's columns with it
# too (D130); textWidth stays public here for the tests
from .sheetDoc import _metricsKey, textWidth
from .workbook import (DONUT_DIMENSIONS, DONUT_PALETTE, SHEET_PASSWORD,
                       buildImplementationRows, donutBreakdown, implColumns,
                       _WIDTHS)

# ---- geometry: one 16:9 canvas, one content box --------------------------
SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5
MARGIN_IN = 0.45
CONTENT_TOP_IN = 1.16                 # below heading, subheading and rule
FOOTER_H_IN = 0.62
#: the content box every table slide shares, in points: left, top, width, height
FULL_BOX = (MARGIN_IN * 72.0, CONTENT_TOP_IN * 72.0,
            (SLIDE_W_IN - 2 * MARGIN_IN) * 72.0,
            (SLIDE_H_IN - CONTENT_TOP_IN - FOOTER_H_IN) * 72.0)
#: the combined slide (no comparison, or one) sits its two tables in from the
#: full box - a little more white around them than a lone table gets (D124)
_COMBINED_INSET = 12.0                # each side
_COMBINED_VINSET = 8.0                # top and bottom
_COMBINED_GUTTER = 36.0               # between the two tables
_COMBINED_SPLIT = 0.46                # the allocation's share of the width
#: the deck's own chrome inks - slide furniture, not report styling
_HEAD_INK = '0F243E'
_SUB_INK = '4B5A6A'
_NOTE_INK = '7C8B9C'
#: a cell no style touched, in the house face (D125)
_DEFAULT_FONT = {'name': sheetDoc.SANS, 'size': 11}
#: border kinds to (width in points, dash) - the Excel edges, drawn - and the
#: two lighter weights only the deck draws (D137, ``_deckRules``)
_EDGE_PT = {'hair': (0.25, None), 'dotted': (0.75, 'sysDot'),
            'thin': (1.0, None), 'thick': (2.25, None),
            'names': (1.25, None), 'class': (0.5, None)}
#: the allocation's rule under the portfolio names and its rule over each
#: asset class, lighter on a slide than the sheet's thick and thin (D137)
_NAMES_RULE = ('names', '000000')
_CLASS_RULE = ('class', '000000')

_A_NS = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def _in(inches: float) -> Emu:
    return Emu(int(round(inches * 914400)))


def _pt(points: float) -> Emu:
    return Emu(int(round(points * 12700)))


# ---- the two XML helpers -------------------------------------------------

def _edgeXml(tag: str, edge, scale: float) -> str:
    """One a:lnX element: the Doc's edge, or an explicit no-line - because a
    PowerPoint table style draws borders of its own, and the only way to get
    exactly the Doc's rules is to state every edge."""
    if edge is None:
        return '<a:{tag} {ns}><a:noFill/></a:{tag}>'.format(tag=tag, ns=_A_NS)
    style, colour = edge
    widthPt, dash = _EDGE_PT[style]
    return ('<a:{tag} {ns} w="{w}" cap="flat"><a:solidFill>'
            '<a:srgbClr val="{c}"/></a:solidFill>{dash}</a:{tag}>').format(
                tag=tag, ns=_A_NS, w=int(round(widthPt * scale * 12700)),
                c=colour or '000000',
                dash='<a:prstDash val="{}"/>'.format(dash) if dash else '')


def _setCellBorders(cell, border, scale: float) -> None:
    """Write all four edges of a table cell. The Doc only ever draws top and
    bottom rules; left and right are stated as no-line so nothing inherited
    from the table style survives."""
    border = border or {}
    tcPr = cell._tc.get_or_add_tcPr()
    for tag in ('a:lnL', 'a:lnR', 'a:lnT', 'a:lnB'):
        for element in tcPr.findall(qn(tag)):
            tcPr.remove(element)
    ordered = [('lnL', border.get('left')), ('lnR', border.get('right')),
               ('lnT', border.get('top')), ('lnB', border.get('bottom'))]
    for position, (tag, edge) in enumerate(ordered):
        tcPr.insert(position, parse_xml(_edgeXml(tag, edge, scale)))


def _setHoleSize(chart, value: int) -> None:
    """The doughnut's hole - python-pptx has no property for it, so the one
    element is written by hand."""
    space = chart._chartSpace
    holes = space.findall('.//' + qn('c:holeSize'))
    if holes:
        for hole in holes:
            hole.set('val', str(value))
        return
    for plot in space.findall('.//' + qn('c:doughnutChart')):
        element = plot.makeelement(qn('c:holeSize'), {'val': str(value)})
        plot.append(element)


# ---- slide chrome --------------------------------------------------------

def _textbox(slide, left, top, width, height):
    box = slide.shapes.add_textbox(_in(left), _in(top), _in(width), _in(height))
    frame = box.text_frame
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = 0
    frame.margin_top = frame.margin_bottom = 0
    return frame


def _para(frame, text, sizePt, colour, bold=False, first=False,
          align=PP_ALIGN.LEFT, name=sheetDoc.SANS):
    paragraph = frame.paragraphs[0] if first else frame.add_paragraph()
    paragraph.alignment = align
    run = paragraph.add_run()
    run.text = text
    run.font.name = name
    run.font.size = Pt(sizePt)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(colour)
    return paragraph


def _chrome(slide, heading, subheading, footnotes, footer, pageText):
    """Heading, subheading, the navy rule, footnotes bottom-left and the
    identity bottom-right - the frame every content slide shares."""
    head = _textbox(slide, MARGIN_IN, 0.26, SLIDE_W_IN - 2 * MARGIN_IN, 0.44)
    _para(head, heading, 22, _HEAD_INK, bold=True, first=True)
    if subheading:
        sub = _textbox(slide, MARGIN_IN, 0.70, SLIDE_W_IN - 2 * MARGIN_IN, 0.34)
        _para(sub, subheading, 11, _SUB_INK, first=True, name=sheetDoc.SANS_LIGHT)
    rule = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, _in(MARGIN_IN), _in(1.05),
                                  _in(SLIDE_W_IN - 2 * MARGIN_IN), _pt(2.5))
    rule.fill.solid()
    rule.fill.fore_color.rgb = RGBColor.from_string(_HEAD_INK)
    rule.line.fill.background()
    rule.shadow.inherit = False
    if footnotes:
        notes = _textbox(slide, MARGIN_IN, SLIDE_H_IN - 0.58,
                         SLIDE_W_IN - 2 * MARGIN_IN - 2.6, 0.5)
        for index, note in enumerate(footnotes):
            _para(notes, note, 8, _NOTE_INK, first=index == 0)
    page = _textbox(slide, SLIDE_W_IN - MARGIN_IN - 2.5, SLIDE_H_IN - 0.58,
                    2.5, 0.5)
    _para(page, footer, 8, _NOTE_INK, first=True, align=PP_ALIGN.RIGHT)
    _para(page, pageText, 8, _NOTE_INK, align=PP_ALIGN.RIGHT)


# ---- the table (D124) -------------------------------------------------------
#
# The deck used to scale the workbook's geometry by one factor, which set the
# tables in spreadsheet proportions - a third of the slide wide, 9pt type. A
# table now FITS its box: it fills the box's width, its rows are sized to its
# text, and the text is the largest size, from BASE_PT down to MIN_PT, at
# which every figure fits its cell and every row fits the box. The style is
# the workbook's own - version 2, "workbook, compact", of
# proposals/deck-table-styles.html - so colours, faces, weights and rules
# still come from the Doc; only the geometry is the deck's.
#
# PowerPoint grows a row whose text does not fit, which is how a fitted table
# would silently spill off its slide. So nothing is left to PowerPoint: widths
# and line heights are predicted from fontMetrics - measured from the very
# font files the deck embeds (D125) - and each row is set to its predicted
# height plus a little, so PowerPoint never needs to grow one.

#: compact's starting size - no table in the deck is set larger than 9pt,
#: on request (D137; it was 11) - and the floor below which a table paginates
BASE_PT = 9.0
MIN_PT = 8.0
#: a one-line table (D137) sets below MIN_PT when its entries, each on one
#: line, need the width: rows can continue on a second slide, columns cannot
ONE_LINE_MIN_PT = 6.5
_STEP_PT = 0.25
#: compact padding, in ems of the fitted size
_PAD_V_EM = 0.04
_PAD_H_EM = 0.5
_PAD_NAMES_EM = 0.25                  # the portfolios header row
_PAD_TOTAL_EM = 0.12                  # its TOTAL and navy metric bars
_INDENT_EM = 1.0125                   # an indented label's step: 1.35 less a quarter (D134)
#: the allocation's and the implementation's step, a third less again; the
#: risk table's crises and horizons keep _INDENT_EM (D135); the assumptions'
#: asset names step in half as far (D137)
_INDENT_EM_BY_DOC = {'portfolios': _INDENT_EM * 2 / 3,
                     'Implementation': _INDENT_EM * 2 / 3,
                     'Initial Allocation': _INDENT_EM * 2 / 3,
                     'assumptions': _INDENT_EM / 2}
_NUM_INDENT_EM = 0.35                 # per Excel indent level, on figures
_SPACER_EM = 3.0 / 11.0               # the 3pt hairline between metric bars
_ROW_SAFETY_PT = 0.6                  # so PowerPoint never has to grow a row
_WIDTH_SLACK = 0.97                   # predicted text must clear the cell by 3%
#: the assumptions table's second header row - the column heads - set at
#: 1.4 cm, on request (D142)
_ASSUMPTIONS_HEAD_PT = 1.4 / 2.54 * 72.0
#: the space a table leaves under the slide's rule: the implementation's
#: always, the assumptions' where its slide has the height (D137)
_TABLE_DROP_PT = 10.0
#: how far the implementation table's rows open into the height its box
#: leaves: to this much padding a side at most (D137; the assumptions' rows
#: no longer open, D138)
_PAD_OPEN_EM = 0.3
#: and the allocation's and risk table's: a little over compact, where their
#: slide has the height, and never at the cost of their size (D139)
_PAD_STRATEGIC_EM = 0.14
#: the padding the combined slide's two tables keep on their rows whatever
#: their height: the pair sets smaller to have it (D140)
_PAD_PAIR_EM = 0.08
#: each Doc's body size: a cell's deck size is the fitted size scaled by its
#: own over this, so a Doc's relative sizes survive the fit
_DOC_BASE = {'portfolios': 11.0, 'risk_dashboard': 12.0,
             'Implementation': 12.0, 'Initial Allocation': 12.0, 'assumptions': 11.0}
#: the label column's share of the width, by portfolio count; the figures
#: share the rest equally
_PORT_LABEL = {1: 50.0, 2: 44.0, 3: 36.0, 4: 32.0}
_RISK_LABEL = {1: 40.0, 2: 36.0, 3: 26.0, 4: 22.0}


def _docColumns(doc: SheetDoc) -> int:
    return max((max(row.cells) for row in doc.rows.values() if row.cells),
               default=1)


_ALIGN = {'left': PP_ALIGN.LEFT, 'center': PP_ALIGN.CENTER,
          'right': PP_ALIGN.RIGHT}


def _signInk(doc: SheetDoc, rowIndex: int, column: int, value):
    """The sign rule, resolved per cell from the RAW value - strict
    inequalities, so an exact zero keeps the base ink (D121)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    for col1, row1, col2, row2, negInk, posInk in doc.signRules:
        if row1 <= rowIndex <= row2 and col1 <= column <= col2:
            if value < 0:
                return negInk
            if value > 0:
                return posInk
    return None


# ---- measuring text --------------------------------------------------------

def _lineHeight(face: str, bold: bool, size: float) -> float:
    return _METRICS[_metricsKey(face, bold)]['line'] * size


def _wrapLines(text: str, face: str, bold: bool, size: float, width: float) -> int:
    """How many lines *text* takes in *width*, wrapped greedily at spaces the
    way PowerPoint wraps a cell. Explicit newlines start new lines."""
    total = 0
    for paragraph in text.split('\n'):
        lines, current = 1, ''
        for word in paragraph.strip().split(' '):
            trial = word if not current else current + ' ' + word
            if not current or textWidth(trial, face, bold, size) <= width:
                current = trial
            else:
                lines += 1
                current = word
        total += lines
    return total


# ---- planning a table ------------------------------------------------------

class TablePlan:
    """One table on one slide, decided before PowerPoint sees it (D124): the
    Doc rows it shows, the box it fills, its column shares, the size it is
    set in and every row's height. ``header`` rows repeat on a continuation
    and may wrap; ``spans`` are deck-only merges that let a heading with no
    figures beside it run the width of its row. With ``fitLabel`` the label
    column is never narrower than its longest label at the size the table is
    set in - no label that shares a row wraps - and the figures share what is
    left (D128). ``pad`` is the vertical padding its rows are fitted with, in
    ems a side: compact's, or more where the plan asks for it (D140).
    ``fixed`` maps a Doc row to the height it is set at whatever its text,
    and ``breaks`` names the (row, column) cells of a one-line table whose
    own line breaks stand (D142).

    A ``oneLine`` table (D137) wraps nothing, its header included, and sets
    every row at one height: each column is at least as wide as its widest
    entry, and the columns share out the rest of the box by their shares -
    or, ``natural``, the columns in ``hold`` stay exactly as wide as their
    widest entry and the rest of the box is shared EQUALLY among the others,
    so the table always spans its box (D138; D137 left it only as wide as
    its columns). Its size runs from ``ceiling`` - the largest at which the
    entries fit the box - down to ``floor``; ``measureRows`` are the rows
    the widths are read from, the whole table's on every page, so a
    continued table keeps its columns."""

    __slots__ = ('doc', 'rows', 'header', 'box', 'shares', 'spans',
                 'font', 'heights', 'fitLabel', 'oneLine', 'natural', 'hold',
                 'ceiling', 'floor', 'measureRows', 'pad', 'fixed', 'breaks')

    def __init__(self, doc, rows, header, box, shares, spans, fitLabel=False,
                 oneLine=False, natural=False, hold=(), pad=_PAD_V_EM, fixed=None,
                 breaks=()):
        self.doc = doc
        self.rows = list(rows)
        self.header = tuple(header)
        self.box = box
        self.shares = list(shares)
        self.spans = spans
        self.font = None
        self.heights = None
        self.fitLabel = fitLabel
        self.oneLine = oneLine or natural
        self.natural = natural
        self.hold = tuple(hold)
        self.pad = pad
        self.fixed = dict(fixed or {})
        self.breaks = frozenset(breaks)
        self.ceiling = BASE_PT
        self.floor = MIN_PT
        self.measureRows = list(rows)

    def widths(self, f=None):
        """The column widths in points at size *f* (the plan's own size when
        not given): the shares, bar a fitted label column; a one-line
        table's by its entries."""
        f = self.font if f is None else f
        if self.oneLine:
            needs = _columnNeeds(self, f)
            if self.natural:
                return _spreadWidths(needs, self.hold, self.box[2])
            return _fillWidths(needs, self.shares, self.box[2])
        total = float(sum(self.shares))
        widths = [self.box[2] * share / total for share in self.shares]
        if self.fitLabel and len(widths) > 1:
            need = _labelWidth(self, f)
            if need > widths[0]:
                others = float(sum(self.shares[1:]))
                widths = [need] + [(self.box[2] - need) * share / others
                                   for share in self.shares[1:]]
        return widths


def _shares(label: float, count: int):
    rest = (100.0 - label) / max(count - 1, 1)
    return [label] + [rest] * (count - 1)


def _base(doc: SheetDoc) -> float:
    return _DOC_BASE.get(doc.name, 11.0)


def _isSpacer(doc: SheetDoc, r: int) -> bool:
    row = doc.rows.get(r)
    return row is not None and row.height is not None and row.height <= 3


def _rowPad(doc: SheetDoc, r: int, pad: float = _PAD_V_EM) -> float:
    """Compact's vertical padding, in ems: the portfolios sheet's header,
    TOTAL and metric bars keep a little more than its lines do. *pad* is
    the plan's padding for every other row - compact's, or more (D140)."""
    row = doc.rows.get(r)
    if doc.name == 'portfolios' and row is not None:
        if r == 1:
            return max(_PAD_NAMES_EM, pad)
        first = row.cells.get(1)
        label = first.value if first is not None else None
        if label == 'TOTAL' or (first is not None and first.fill == sheetDoc.NAVY):
            return max(_PAD_TOTAL_EM, pad)
    return pad


def _spansFor(doc: SheetDoc, rows):
    """Deck-only merges, row -> the column the merge ends at: a risk heading
    with no figures beside it spans its row, so a long title never forces the
    whole table's type down to fit the label column - but only as far as the
    first vertical rule on the row, so a rule between portfolios runs down
    the table unbroken (D130)."""
    spans = {}
    if doc.name != 'risk_dashboard':
        return spans
    n = _docColumns(doc)
    for r in rows:
        if r not in doc.sections:
            continue
        row = doc.rows[r]
        if all(row.cells.get(c) is None or row.cells[c].value in (None, '')
               for c in range(2, n + 1)):
            end = n
            for c in range(1, n):
                here, after = row.cells.get(c), row.cells.get(c + 1)
                if ((here is not None and (here.border or {}).get('right'))
                        or (after is not None and (after.border or {}).get('left'))):
                    end = c
                    break
            if end > 1:
                spans[r] = end
    return spans


def _cells(doc: SheetDoc, r: int, widths, spans):
    """column -> the width of the cell there, merged cells counted once."""
    if r in spans:
        out = {1: sum(widths[:spans[r]])}
        out.update((c, widths[c - 1]) for c in range(spans[r] + 1, len(widths) + 1))
        return out
    out, covered = {}, set()
    for r1, c1, r2, c2 in doc.merges:
        if r1 == r:
            out[c1] = sum(widths[c1 - 1:c2])
            covered.update(range(c1 + 1, c2 + 1))
    for c in range(1, len(widths) + 1):
        if c not in covered and c not in out:
            out[c] = widths[c - 1]
    return out


def _indentPt(spec, text: str, f: float, column: int, doc: SheetDoc = None) -> float:
    """A label set in, by leading spaces or an indent level, steps in once
    (the allocation's risk-free line has only the level, D127); a figure
    steps in per level."""
    extra = 0.0
    indent = ((spec.align or {}).get('indent') or 0) if spec is not None else 0
    if column == 1 and (text.startswith('  ') or indent):
        extra += _INDENT_EM_BY_DOC.get(doc.name if doc is not None else None, _INDENT_EM) * f
    if indent and column > 1:
        extra += indent * _NUM_INDENT_EM * f
    return extra


def _oneLineText(text: str) -> str:
    """A cell's text on one line: a head the sheet breaks over two lines
    ('Risk Premia\\n with Estimated Range') reads as one on a one-line
    table (D137)."""
    if '\n' not in text:
        return text
    return ' '.join(part.strip() for part in text.split('\n'))


def _oneLineNeed(spec, doc: SheetDoc, f: float, column: int, keepBreaks: bool = False) -> float:
    """The width *spec* needs at size *f* to sit on one line in its cell:
    its text with the padding, the indent and the measure's slack - or,
    *keepBreaks*, its widest line (D142)."""
    base = _base(doc)
    font = spec.font or _DEFAULT_FONT
    size = f * font.get('size', base) / base
    text = renderNumber(spec.value, spec.fmt)
    shown = text.strip() if column == 1 else text
    lines = ([part.strip() for part in shown.split('\n')] if keepBreaks
             else [_oneLineText(shown)])
    width = max(textWidth(line, font.get('name', sheetDoc.SANS), bool(font.get('bold')), size)
                for line in lines)
    return (width / _WIDTH_SLACK + 2 * _PAD_H_EM * f
            + _indentPt(spec, text, f, column, doc) + 0.01)


def _columnNeeds(plan: TablePlan, f: float):
    """Each column's width at size *f* with every entry on one line (D137):
    its widest entry, the header's included. A merged cell's need is shared
    across the columns it covers, in proportion to their shares, where they
    do not already hold it between them."""
    doc = plan.doc
    count = len(plan.shares)
    needs = [2 * _PAD_H_EM * f] * count
    ends = {(r1, c1): c2 for r1, c1, _, c2 in doc.merges}
    covered = {(r1, c) for r1, c1, _, c2 in doc.merges for c in range(c1 + 1, c2 + 1)}
    wide = []
    for r in plan.measureRows:
        row = doc.rows.get(r)
        if row is None or _isSpacer(doc, r):
            continue
        for column, spec in row.cells.items():
            if column > count or (r, column) in covered or spec.value in (None, ''):
                continue
            need = _oneLineNeed(spec, doc, f, column, (r, column) in plan.breaks)
            if (r, column) in ends:
                wide.append((column, min(ends[(r, column)], count), need))
            else:
                needs[column - 1] = max(needs[column - 1], need)
    for first, last, need in wide:
        span = range(first - 1, last)
        have = sum(needs[i] for i in span)
        if need > have:
            weight = float(sum(plan.shares[i] for i in span)) or 1.0
            for i in span:
                needs[i] += (need - have) * plan.shares[i] / weight
    return needs


def _fillWidths(needs, shares, width: float):
    """Widths that fill *width*, each column at least its need and otherwise
    in proportion to its share: the shares scaled until what is left over,
    after the columns held at their needs, is used exactly. When the needs
    alone overflow, they are returned as they are - the fit refuses them."""
    if sum(needs) >= width:
        return list(needs)
    held = set()
    while True:
        rest = width - sum(needs[i] for i in held)
        free = float(sum(shares[i] for i in range(len(needs)) if i not in held)) or 1.0
        scale = rest / free
        more = {i for i in range(len(needs))
                if i not in held and shares[i] * scale < needs[i]}
        if not more:
            return [needs[i] if i in held else shares[i] * scale for i in range(len(needs))]
        held |= more


def _spreadWidths(needs, hold, width: float):
    """Widths that span *width*: every column its need, and what the box has
    left over them shared equally among the columns not in *hold*, which
    stay exactly as wide as their widest entry (D138). When the needs alone
    overflow they are returned as they are - the fit refuses them."""
    rest = width - sum(needs)
    free = [i for i in range(len(needs)) if i not in hold] or list(range(len(needs)))
    if rest <= 0:
        return list(needs)
    return [need + (rest / len(free) if i in free else 0.0) for i, need in enumerate(needs)]


def _widthCeiling(plan: TablePlan) -> float:
    """The largest size, from BASE_PT down to ONE_LINE_MIN_PT, at which a
    one-line table's columns fit its box's width with every entry on one
    line."""
    f = BASE_PT
    while f - _STEP_PT >= ONE_LINE_MIN_PT - 1e-9 and \
            sum(_columnNeeds(plan, f)) > plan.box[2] + 1e-6:
        f = round(f - _STEP_PT, 2)
    return f


def _isFigure(spec, r: int, column: int, header) -> bool:
    """A figure must fit its cell on one line; everything else may wrap."""
    if spec is None or column == 1 or r in header:
        return False
    return (spec.align or {}).get('horizontal') in ('right', 'center')


def _measure(plan: TablePlan, f: float):
    """Each row's height at size *f*, and whether every figure fits - on a
    one-line table every entry, in a table no wider than its box, and every
    row the height of the tallest (D137)."""
    doc, base = plan.doc, _base(plan.doc)
    widths = plan.widths(f)
    heights, fits = [], True
    if plan.oneLine and sum(widths) > plan.box[2] + 1e-6:
        fits = False
    for r in plan.rows:
        if _isSpacer(doc, r):
            heights.append(_SPACER_EM * f)
            continue
        row = doc.rows.get(r)
        tallest = 0.0
        for c, width in _cells(doc, r, widths, plan.spans).items():
            spec = row.cells.get(c) if row is not None else None
            font = (spec.font if spec is not None and spec.font else None) or _DEFAULT_FONT
            size = f * font.get('size', base) / base
            face, bold = font.get('name', sheetDoc.SANS), bool(font.get('bold'))
            text = renderNumber(spec.value, spec.fmt) if spec is not None else ''
            inner = (width - 2 * _PAD_H_EM * f - _indentPt(spec, text, f, c, doc)) * _WIDTH_SLACK
            shown = text.strip() if c == 1 else text
            if plan.oneLine:
                parts = ([part.strip() for part in shown.split('\n')]
                         if (r, c) in plan.breaks else [_oneLineText(shown)])
                if max(textWidth(part, face, bold, size) for part in parts) > inner:
                    fits = False
                lines = len(parts)
            elif _isFigure(spec, r, c, plan.header):
                if textWidth(shown, face, bold, size) > inner:
                    fits = False
                lines = 1
            else:
                lines = _wrapLines(shown, face, bold, size, inner) if shown else 1
            tallest = max(tallest, lines * _lineHeight(face, bold, size))
        natural = tallest + 2 * _rowPad(doc, r, plan.pad) * f + _ROW_SAFETY_PT
        if r in plan.fixed:
            # set at its own height, which must still hold its text (D142)
            if natural > plan.fixed[r] + 1e-9:
                fits = False
            natural = plan.fixed[r]
        heights.append(natural)
    if plan.oneLine:
        # every row one height, bar a row set at its own (D137, D142)
        free = lambda r: not _isSpacer(doc, r) and r not in plan.fixed
        live = [h for r, h in zip(plan.rows, heights) if free(r)]
        if live:
            heights = [max(live) if free(r) else h for r, h in zip(plan.rows, heights)]
    return heights, fits


def _labelWidth(plan: TablePlan, f: float) -> float:
    """The width the label column needs so that none of its labels wraps at
    size *f*: the widest, with its padding and indent, and the same slack
    ``_measure`` allows. A spanned row runs the table's width and is not
    counted."""
    doc, base, need = plan.doc, _base(plan.doc), 0.0
    for r in plan.rows:
        row = doc.rows.get(r)
        spec = row.cells.get(1) if row is not None else None
        if spec is None or r in plan.spans or spec.value in (None, ''):
            continue
        font = spec.font or _DEFAULT_FONT
        size = f * font.get('size', base) / base
        text = renderNumber(spec.value, spec.fmt)
        width = textWidth(text.strip(), font.get('name', sheetDoc.SANS),
                          bool(font.get('bold')), size)
        need = max(need, width / _WIDTH_SLACK + 2 * _PAD_H_EM * f
                   + _indentPt(spec, text, f, 1, doc) + 0.01)
    return need


def _fit(plan: TablePlan):
    """The largest size from the plan's ceiling (BASE_PT) down to its floor
    (MIN_PT) at which *plan* fits its box, with its row heights; None when
    not even the floor does."""
    f = plan.ceiling
    while True:
        heights, fits = _measure(plan, f)
        if fits and sum(heights) <= plan.box[3]:
            return f, heights
        if f - _STEP_PT < plan.floor - 1e-9:
            return None
        f = round(f - _STEP_PT, 2)


def _view(doc: SheetDoc, rows) -> SheetDoc:
    """A Doc of just *rows*, sharing its cells, for paginate to cut."""
    keep = set(rows)
    view = SheetDoc(doc.name)
    view.rows = {r: row for r, row in doc.rows.items() if r in keep}
    view.merges = [m for m in doc.merges if m[0] in keep]
    view.widths = doc.widths
    view.signRules = list(doc.signRules)
    view.sections = {r for r in doc.sections if r in keep}
    return view


def planTable(doc: SheetDoc, box, shares, header=(1,), rows=None, fitLabel=False,
              oneLine=False, natural=False, hold=(), pad=_PAD_V_EM, fixed=None, breaks=()):
    """One Doc as one or more TablePlans in *box*. Whole when it fits at
    MIN_PT or above; otherwise cut at the Doc's section marks with each row
    costed at MIN_PT, the header repeated, and every page set in the one
    size the tallest page allows - a table reads at one size throughout.
    A one-line table (D137) starts no larger than its columns' width allows
    and, where that is under MIN_PT, is floored there instead."""
    rows = sorted(doc.rows) if rows is None else list(rows)
    spans = _spansFor(doc, rows)
    whole = TablePlan(doc, rows, header, box, shares, spans, fitLabel, oneLine, natural, hold,
                      pad, fixed, breaks)
    if whole.oneLine:
        whole.ceiling = _widthCeiling(whole)
        whole.floor = min(MIN_PT, whole.ceiling)
    got = _fit(whole)
    if got is not None:
        whole.font, whole.heights = got
        return [whole]
    floorHeights, _ = _measure(whole, whole.floor)
    cost = dict(zip(rows, floorHeights))
    pages = paginate(_view(doc, rows), box[3], headerRows=header, costOf=cost.get)
    plans = []
    for page in pages:
        plan = TablePlan(doc, page.header + page.body, header, box, shares, spans,
                         fitLabel, oneLine, natural, hold, pad, fixed, breaks)
        plan.ceiling, plan.floor, plan.measureRows = whole.ceiling, whole.floor, rows
        plans.append(plan)
    sizes = []
    for plan in plans:
        fitted = _fit(plan)
        sizes.append(fitted[0] if fitted else plan.floor)
    size = min(sizes)
    for plan in plans:
        plan.font = size
        plan.heights, _ = _measure(plan, size)
    return plans


def _dropped(box, drop: float = _TABLE_DROP_PT):
    """*box* less *drop* points off its top: a table set a little further
    under the slide's rule (D137)."""
    left, top, width, height = box
    return (left, top + drop, width, height - drop)


def settleDown(plans, drop: float = _TABLE_DROP_PT) -> None:
    """Each planned page moved down by up to *drop* points, as far as the
    height its rows leave in its box allows, and not at all where they fill
    it (D137)."""
    for plan in plans:
        room = plan.box[3] - sum(plan.heights)
        if room > 0:
            plan.box = _dropped(plan.box, min(drop, room))


def openRows(plans, upToEm: float = _PAD_OPEN_EM) -> None:
    """A table's rows opened evenly into the height its box leaves, each by
    no more than takes a row's fitted padding to *upToEm* a side, and by
    the same on every page, so rows that were one height stay one height
    (D137): the room is used, but a short table is not stretched to fill
    its slide. The allocation and the risk table open the same way (D139)."""
    def live(plan):
        return [i for i, r in enumerate(plan.rows) if not _isSpacer(plan.doc, r)]
    if not plans:
        return
    add = min((plan.box[3] - sum(plan.heights)) / max(len(live(plan)), 1) for plan in plans)
    add = min(add, 2 * (upToEm - plans[0].pad) * plans[0].font)
    if add <= 0:
        return
    for plan in plans:
        for i in live(plan):
            plan.heights[i] += add


def centreDown(plans) -> None:
    """Each planned page moved down its box by half the height its rows
    leave, so it sits centred between the slide's rule and its footnotes
    (D139)."""
    for plan in plans:
        room = plan.box[3] - sum(plan.heights)
        if room > 0:
            plan.box = _dropped(plan.box, room / 2.0)


def strategicRows(plans) -> None:
    """The allocation's and the risk table's rows: opened a little, as far
    as each table's own box allows, and the table centred down its slide
    (D139)."""
    openRows(plans, _PAD_STRATEGIC_EM)
    centreDown(plans)


def shareSlide(plans) -> None:
    """Tables on one slide share a size: all step down to the smallest fit
    (D124). Their rows are their own: D124 opened the shorter table's rows
    so the two ended at the bottom together, which at the 9pt cap (D137)
    padded every row out, and D138 left them compact; each now opens a
    little into its own box's height and sits centred down it (D139)."""
    size = min(plan.font for plan in plans)
    for plan in plans:
        plan.font = size
        plan.heights, _ = _measure(plan, size)
        strategicRows([plan])


def combinedBoxes():
    """The combined slide's two boxes: allocation left, risk right."""
    left, top, width, height = FULL_BOX
    left += _COMBINED_INSET
    width -= 2 * _COMBINED_INSET
    top += _COMBINED_VINSET
    height -= 2 * _COMBINED_VINSET
    usable = width - _COMBINED_GUTTER
    leftWidth = usable * _COMBINED_SPLIT
    return ((left, top, leftWidth, height),
            (left + leftWidth + _COMBINED_GUTTER, top, usable - leftWidth, height))


def riskWithoutRepeats(doc: SheetDoc):
    """The risk sheet's rows less the ones that repeat the allocation beside
    it - its category weights and the three portfolio metrics - for the
    combined slide, where they would show every figure twice (D124). The
    header stays, and everything from the first section mark down."""
    band = min(doc.sections)
    return [r for r in sorted(doc.rows) if r == 1 or r >= band]


def headedRisk(doc: SheetDoc):
    """The risk table for the combined slide, as a view Doc and its rows:
    ``riskWithoutRepeats``, and then the first band's title - *Factor Based
    Risk Analytics* - moved into the header's empty corner, beside the
    portfolio names, and the band row dropped (D128). The header and the
    band are dressed alike, so the corner takes the band's label cell whole.
    *doc* is not touched: its header row is replaced in the view only."""
    rows = riskWithoutRepeats(doc)
    band = min(doc.sections)
    header, title = doc.rows[1], doc.rows[band].cells[1]
    corner = header.cells.get(1) or sheetDoc.Cell()
    top = sheetDoc.Row()
    top.height = header.height
    top.cells = dict(header.cells)
    top.cells[1] = sheetDoc.Cell(title.value, title.fmt, title.font, title.fill,
                                 corner.border, title.align)
    rows = [r for r in rows if r != band]
    view = _view(doc, rows)
    view.rows[1] = top
    return view, rows


# ---- rendering a planned table ---------------------------------------------

def sharedEdges(plan: TablePlan) -> dict:
    """(Doc row, column) -> that cell's edges, with every horizontal rule
    stated on BOTH cells that share it.

    Excel draws a rule once, from whichever of the two cells carries it; the
    Doc says it once, on one of them. A PowerPoint cell owns all four of its
    edges, and ``_setCellBorders`` states every one of them - so a rule given
    only to the lower cell's top met an explicit no-line on the upper cell's
    bottom, and PowerPoint drew the no-line: the deck's tables lost their
    rules (D127). Each rule is now given to both cells, which is how
    PowerPoint itself writes a border. Neighbours are the rows the table
    SHOWS, so a continuation page's header takes the rule of the row it
    now sits on, and a dropped row's rule is not left dangling."""
    def edges(rowIndex, column):
        row = plan.doc.rows.get(rowIndex)
        cell = row.cells.get(column) if row is not None else None
        return (cell.border if cell is not None else None) or {}

    out, columns = {}, len(plan.shares)
    for position, rowIndex in enumerate(plan.rows):
        above = plan.rows[position - 1] if position else None
        below = plan.rows[position + 1] if position + 1 < len(plan.rows) else None
        for column in range(1, columns + 1):
            own = dict(edges(rowIndex, column))
            if own.get('top') is None and above is not None:
                own['top'] = edges(above, column).get('bottom')
            if own.get('bottom') is None and below is not None:
                own['bottom'] = edges(below, column).get('top')
            # and the vertical rules the same way, left against right (D130)
            if own.get('left') is None and column > 1:
                own['left'] = edges(rowIndex, column - 1).get('right')
            if own.get('right') is None and column < columns:
                own['right'] = edges(rowIndex, column + 1).get('left')
            out[(rowIndex, column)] = {edge: kind for edge, kind in own.items() if kind}
    # A merged cell is drawn from its first cell, so that cell carries the
    # merge's far edge: the right edge of the last column it covers.
    shown = set(plan.rows)
    merges = [(r1, c1, c2) for r1, c1, _, c2 in plan.doc.merges if r1 in shown]
    merges += [(r, 1, end) for r, end in plan.spans.items() if r in shown]
    for r, first, last in merges:
        far = out[(r, last)].get('right')
        origin = out[(r, first)]
        if far:
            origin['right'] = far
        else:
            origin.pop('right', None)
    return out


def _deckRules(plan: TablePlan, shared: dict) -> dict:
    """The allocation's rule under the portfolio names, and the rule over
    each of its asset classes, drawn lighter on a slide (D137): the sheet's
    thick and thin become ``_NAMES_RULE`` and ``_CLASS_RULE``, from both
    cells that share them. TOTAL's thick rule and the metric block's white
    ones are the sheet's still. Every other table is drawn as its Doc says."""
    if plan.doc.name != 'portfolios':
        return shared
    sections = plan.doc.sections
    for position, r in enumerate(plan.rows):
        above = plan.rows[position - 1] if position else None
        below = plan.rows[position + 1] if position + 1 < len(plan.rows) else None
        for column in range(1, len(plan.shares) + 1):
            edges = shared[(r, column)]
            for edge, upper, lower in (('top', above, r), ('bottom', r, below)):
                kind = edges.get(edge)
                if kind == sheetDoc.BLACK_THICK and upper == 1:
                    edges[edge] = _NAMES_RULE
                elif kind == sheetDoc.BLACK_THIN and lower in sections:
                    edges[edge] = _CLASS_RULE
    return shared


def _fillCell(cell, spec, plan: TablePlan, rowIndex: int, column: int,
              border=None) -> None:
    doc, f, base = plan.doc, plan.font, _base(plan.doc)
    font = spec.font or _DEFAULT_FONT
    face = font.get('name', sheetDoc.SANS)
    size = f * font.get('size', base) / base
    ink = _signInk(doc, rowIndex, column, spec.value) or font.get('color')
    align = spec.align or {}
    text = renderNumber(spec.value, spec.fmt)
    if plan.oneLine and (rowIndex, column) not in plan.breaks:
        text = _oneLineText(text)
    frame = cell.text_frame
    frame.word_wrap = not (plan.oneLine or _isFigure(spec, rowIndex, column, plan.header))
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    padH = _PAD_H_EM * f
    left = right = padH
    extra = _indentPt(spec, text, f, column, doc)
    if extra:
        # an indent leans on the side the alignment leans on
        if column > 1 and align.get('horizontal') == 'right':
            right += extra
        else:
            left += extra
    cell.margin_left, cell.margin_right = _pt(left), _pt(right)
    pad = 0.0 if _isSpacer(doc, rowIndex) else _rowPad(doc, rowIndex, plan.pad) * f
    cell.margin_top = cell.margin_bottom = _pt(pad)
    alignment = _ALIGN.get(align.get('horizontal'), PP_ALIGN.LEFT)
    lines = (text.split('\n') if text else [''])
    for index, line in enumerate(lines):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = alignment
        run = paragraph.add_run()
        run.text = line.strip() if (index or column == 1) else line
        run.font.name = face
        run.font.size = Pt(max(size, 1))
        run.font.bold = bool(font.get('bold'))
        run.font.color.rgb = RGBColor.from_string(ink or '000000')
        if font.get('underline'):
            run.font.underline = True                  # 'single', as in the Doc
        if not run.text:
            # an empty cell is sized by its paragraph mark, which is 18pt
            # unless told otherwise - and would grow its row past the plan
            # (the mean return's bar, D127, and every header's corner cell)
            mark = paragraph._p.get_or_add_endParaRPr()
            mark.set('sz', str(int(round(max(size, 1) * 100))))
    if spec.fill:
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor.from_string(spec.fill)
    else:
        cell.fill.background()
    _setCellBorders(cell, spec.border if border is None else border, 1.0)


def _renderPlan(slide, plan: TablePlan) -> None:
    """A planned table, transcribed: its box, its column widths, its rows at
    their planned heights, every cell at the planned size (D124)."""
    left, top, _, _ = plan.box
    widths = plan.widths()
    frame = slide.shapes.add_table(len(plan.rows), len(widths), _pt(left), _pt(top),
                                   _pt(sum(widths)), _pt(sum(plan.heights)))
    table = frame.table
    table.first_row = False
    table.last_row = False
    table.first_col = False
    table.last_col = False
    table.horz_banding = False
    table.vert_banding = False
    for index, w in enumerate(widths):
        table.columns[index].width = _pt(w)
    position = {}
    shared = _deckRules(plan, sharedEdges(plan))
    for tableRow, rowIndex in enumerate(plan.rows):
        position[rowIndex] = tableRow
        table.rows[tableRow].height = _pt(plan.heights[tableRow])
        spec = plan.doc.rows.get(rowIndex)
        cells = spec.cells if spec is not None else {}
        for column in range(1, len(widths) + 1):
            _fillCell(table.cell(tableRow, column - 1),
                      cells.get(column, sheetDoc.Cell()), plan, rowIndex, column,
                      shared[(rowIndex, column)])
    for row1, col1, row2, col2 in plan.doc.merges:
        if row1 in position and row2 in position:
            table.cell(position[row1], col1 - 1).merge(
                table.cell(position[row2], col2 - 1))
    for rowIndex, span in plan.spans.items():
        if rowIndex in position:
            table.cell(position[rowIndex], 0).merge(
                table.cell(position[rowIndex], span - 1))


# ---- the charts ----------------------------------------------------------

#: the doughnuts' text: titles at 12pt (D137), legends 2pt smaller (D142),
#: and the hole half the chart (D137). No share is printed on a slice (D138)
DONUT_TITLE_PT = 12
DONUT_LEGEND_PT = 10
DONUT_HOLE = 50


#: the legend under each doughnut, stacked one entry to a line (D144): each
#: line this many legend sizes tall, the colour key this many wide, and the
#: gap between key and name this many
_LEGEND_LINE_EM = 1.5
_LEGEND_KEY_EM = 0.8
_LEGEND_GAP_EM = 0.5
#: between a doughnut's frame and its legend, in points
_LEGEND_DROP_PT = 6.0
#: the frame's height over its width: room for the title over the ring
_DONUT_TITLE_ALLOW_PT = 26.0
_DONUT_GAP_IN = 0.18


def donutLayout(items):
    """Where each composition doughnut and its legend go, in points (D144).

    The five charts share the content width, as before. Each chart's frame
    is as tall as it is wide plus room for its title, so the ring fills it;
    under it its legend stacks one entry to a line - a colour key, then the
    name and its printed share, *SMA (39.0%)* (D145) - as a block exactly as
    wide as its widest entry, measured in the
    house face at the legend's size, and centred on the chart's own centre.
    The charts share one top, so the rings and the legends' first lines
    align across the slide, and the group - frames and the tallest legend -
    is centred down the content box. Returns one dict per chart: its
    dimension key and label, its slices, its ``frame`` (left, top, width,
    height) and its ``legend``: per entry its ``name``, the ``label`` it
    shows, the ``key`` square (left, top, side) and the ``text`` box (left,
    top, width, height)."""
    charts = [(key, label, donutBreakdown(items, key)) for key, label in DONUT_DIMENSIONS]
    charts = [chart for chart in charts if chart[2]]
    across = len(DONUT_DIMENSIONS)
    left0, top0, boxWidth, boxHeight = FULL_BOX
    gap = _DONUT_GAP_IN * 72.0
    width = (boxWidth - gap * (across - 1)) / across
    frameHeight = width + _DONUT_TITLE_ALLOW_PT
    size = float(DONUT_LEGEND_PT)
    line = _LEGEND_LINE_EM * size
    key = _LEGEND_KEY_EM * size
    between = _LEGEND_GAP_EM * size
    tallest = max((len(slices) for _, _, slices in charts), default=0)
    group = frameHeight + _LEGEND_DROP_PT + tallest * line
    top = top0 + max(0.0, (boxHeight - group) / 2.0)
    out = []
    for position, (dimension, label, slices) in enumerate(charts):
        index = [k for k, _ in DONUT_DIMENSIONS].index(dimension)
        frameLeft = left0 + index * (width + gap)
        centre = frameLeft + width / 2.0
        # the name and its share as the page's legend prints it, to one place
        labels = ['{} ({:.1f}%)'.format(entry['name'], entry['pct']) for entry in slices]
        widest = max(textWidth(label_, sheetDoc.SANS, False, size) for label_ in labels)
        block = key + between + widest
        blockLeft = centre - block / 2.0
        legendTop = top + frameHeight + _LEGEND_DROP_PT
        entries = []
        for row, entry in enumerate(slices):
            lineTop = legendTop + row * line
            entries.append({
                'name': entry['name'], 'label': labels[row], 'slot': entry['slot'],
                'key': (blockLeft, lineTop + (line - key) / 2.0, key),
                'text': (blockLeft + key + between, lineTop, widest, line)})
        out.append({'key': dimension, 'label': label, 'slices': slices,
                    'frame': (frameLeft, top, width, frameHeight),
                    'legend': entries, 'block': (blockLeft, legendTop, block, len(slices) * line)})
    return out


def _renderDonuts(slide, items) -> None:
    """The five composition doughnuts, native charts fed straight from
    ``donutBreakdown`` - no hidden sheet, because a PowerPoint chart carries
    its data - each slice coloured by its palette slot, and no share printed
    on it: D137 labelled every slice, and D138 took the labels off.

    The legends are drawn, not the chart's own (D144): a chart legend lays
    its entries out as PowerPoint sees fit, and at the bottom of a frame it
    runs them across. Each is a stack of colour keys and names placed where
    ``donutLayout`` puts them - one entry to a line, centred under its ring -
    in the embedded house face, so what was measured is what is shown."""
    for chart_ in donutLayout(items):
        slices = chart_['slices']
        # the printed shares, as fractions, so the chart's data reads them as
        # percentages to one place
        data = CategoryChartData(number_format='0.0%')
        data.categories = [entry['name'] for entry in slices]
        data.add_series('Share', [round(entry['pct'] / 100.0, 6) for entry in slices])
        left, top, width, height = chart_['frame']
        chart = slide.shapes.add_chart(
            XL_CHART_TYPE.DOUGHNUT, _pt(left), _pt(top), _pt(width), _pt(height), data).chart
        chart.has_title = True
        chart.chart_title.text_frame.text = chart_['label']
        title = chart.chart_title.text_frame.paragraphs[0].runs[0].font
        title.size = Pt(DONUT_TITLE_PT)
        title.bold = True
        title.name = sheetDoc.SANS
        title.color.rgb = RGBColor.from_string(_HEAD_INK)
        chart.has_legend = False
        chart.plots[0].has_data_labels = False
        series = chart.series[0]
        for offset, entry in enumerate(slices):
            fill = DONUT_PALETTE[entry['slot']]
            point = series.points[offset]
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = RGBColor.from_string(fill)
            point.format.line.color.rgb = RGBColor.from_string('FFFFFF')
            point.format.line.width = Pt(0.75)
        _setHoleSize(chart, DONUT_HOLE)
        for entry in chart_['legend']:
            x, y, side = entry['key']
            mark = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, _pt(x), _pt(y), _pt(side), _pt(side))
            mark.name = 'Legend key: {} / {}'.format(chart_['label'], entry['name'])
            mark.fill.solid()
            mark.fill.fore_color.rgb = RGBColor.from_string(DONUT_PALETTE[entry['slot']])
            mark.line.fill.background()
            mark.shadow.inherit = False
            x, y, w, h = entry['text']
            # a hair of slack so the measured name never wraps, left-aligned
            # at the block's text edge, so the block stays centred
            box = slide.shapes.add_textbox(_pt(x), _pt(y), _pt(w + 2.0), _pt(h))
            box.name = 'Legend: {} / {}'.format(chart_['label'], entry['name'])
            frame = box.text_frame
            frame.word_wrap = False
            frame.margin_left = frame.margin_right = 0
            frame.margin_top = frame.margin_bottom = 0
            frame.vertical_anchor = MSO_ANCHOR.MIDDLE
            _para(frame, entry['label'], DONUT_LEGEND_PT, '404040', first=True)


# ---- the house faces (D125) ----------------------------------------------
#
# Every run the deck writes names its face, but a chart's axis and legend
# text, or anything PowerPoint adds when the deck is unlocked, reads the
# theme's - so the theme's heading and body faces become GS Sans too. And the
# faces travel with the file: embedded, the deck shows GS Sans on a machine
# that has never installed it, which the workbook cannot do (houseFonts).

_FONTDATA_CT = 'application/x-fontdata'
#: variable pitch, sans serif (FF_SWISS) - what PowerPoint writes for GS Sans
_PITCH_FAMILY = 34


def _houseTheme(presentation) -> None:
    theme = presentation.slide_master.part.part_related_by(RT.THEME)
    root = parse_xml(theme.blob)
    for which in ('a:majorFont', 'a:minorFont'):
        root.find('.//' + qn(which)).find(qn('a:latin')).set('typeface', sheetDoc.SANS)
    theme._blob = etree.tostring(root, xml_declaration=True, encoding='UTF-8',
                                 standalone=True)


def _embedFonts(presentation) -> None:
    """Every house face, as a font part the presentation lists - whole, not
    subset, so an unlocked deck can still be edited in them."""
    part = presentation.part
    package = part.package
    entries = []
    number = 0
    for typeface, slots in houseFonts.FACES.items():
        panose = None
        refs = []
        for slot in ('regular', 'bold', 'italic', 'boldItalic'):
            if slot not in slots:
                continue
            font = houseFonts.fontBytes(slots[slot])
            if panose is None:
                panose = houseFonts.details(font)['panose'].hex().upper()
            number += 1
            fontPart = Part(PackURI('/ppt/fonts/font{}.fntdata'.format(number)),
                            _FONTDATA_CT, package, houseFonts.eot(font))
            refs.append('<p:{} r:id="{}"/>'.format(slot, part.relate_to(fontPart, RT.FONT)))
        entries.append('<p:embeddedFont><p:font typeface="{}" panose="{}" '
                       'pitchFamily="{}" charset="0"/>{}</p:embeddedFont>'.format(
                           typeface, panose, _PITCH_FAMILY, ''.join(refs)))
    fonts = parse_xml(
        '<p:embeddedFontLst xmlns:p="http://schemas.openxmlformats.org/presentationml/'
        '2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships">{}</p:embeddedFontLst>'.format(''.join(entries)))
    root = part._element
    for old in root.findall(qn('p:embeddedFontLst')):
        root.remove(old)
    # schema order: straight after notesSz, before defaultTextStyle
    root.find(qn('p:notesSz')).addnext(fonts)
    root.set('embedTrueTypeFonts', '1')
    # the template asks PowerPoint to subset on save; these are whole
    root.attrib.pop('saveSubsetFonts', None)


# ---- protection (D123) ---------------------------------------------------
#
# The deck is locked the way the workbook is: with the same password
# (``workbook.SHEET_PASSWORD``, D104), as the same kind of convention. Two
# layers, because PowerPoint has no cell locks to lean on:
#
# * a password to MODIFY - ``p:modifyVerifier`` - so PowerPoint opens the file
#   asking for the password or offering Read Only, and saving over it needs
#   the password. The verifier is ISO/IEC 29500 write protection
#   ([MS-OFFCRYPTO] 2.4.2.4): SHA-512 over a random salt and the UTF-16LE
#   password, iterated 100,000 times with a little-endian counter appended -
#   the parameters PowerPoint writes itself. Only the hash reaches the file.
# * Mark as Final - the ``_MarkAsFinal`` custom property, with content status
#   Final - so even opened read only, editing is switched off behind a banner
#   rather than merely unsaveable.
#
# Neither is encryption and neither is a security control, exactly as the
# workbook's docstring says of its own: the file is a zip, and the register's
# stored copy remains the record of what was delivered.

#: PowerPoint's own iteration count for a password to modify
_SPIN_COUNT = 100000
#: the custom-properties part that marks a presentation final
_MARK_AS_FINAL = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
    'custom-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/'
    '2006/docPropsVTypes"><property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" '
    'pid="2" name="_MarkAsFinal"><vt:bool>true</vt:bool></property></Properties>')
_CUSTOM_PROPERTIES_CT = 'application/vnd.openxmlformats-officedocument.custom-properties+xml'


def modifyHash(password: str, salt: bytes, spinCount: int = _SPIN_COUNT) -> bytes:
    """The ISO write-protection verifier: H0 = SHA-512(salt + UTF-16LE
    password), then spinCount rounds of H = SHA-512(H + counter), the counter
    four bytes little-endian from zero. Public so a test can recompute it."""
    digest = hashlib.sha512(salt + password.encode('utf-16-le')).digest()
    for iteration in range(spinCount):
        digest = hashlib.sha512(digest + struct.pack('<I', iteration)).digest()
    return digest


def _protectDeck(presentation, password: str = SHEET_PASSWORD) -> None:
    """Lock the deck (D123): a password to modify, and Mark as Final."""
    salt = os.urandom(16)
    verifier = parse_xml(
        '<p:modifyVerifier xmlns:p="http://schemas.openxmlformats.org/'
        'presentationml/2006/main" cryptProviderType="rsaAES" '
        'cryptAlgorithmClass="hash" cryptAlgorithmType="typeAny" '
        'cryptAlgorithmSid="14" spinCount="{spin}" saltData="{salt}" '
        'hashData="{hash}"/>'.format(
            spin=_SPIN_COUNT,
            salt=base64.b64encode(salt).decode('ascii'),
            hash=base64.b64encode(modifyHash(password, salt)).decode('ascii')))
    root = presentation.part._element
    for old in root.findall(qn('p:modifyVerifier')):
        root.remove(old)
    # schema order: after defaultTextStyle, before any extLst
    extLst = root.find(qn('p:extLst'))
    if extLst is not None:
        extLst.addprevious(verifier)
    else:
        root.append(verifier)

    package = presentation.part.package
    final = Part(PackURI('/docProps/custom.xml'), _CUSTOM_PROPERTIES_CT, package,
                 _MARK_AS_FINAL.encode('utf-8'))
    package.relate_to(final, RT.CUSTOM_PROPERTIES)
    presentation.core_properties.content_status = 'Final'


# ---- the deck ------------------------------------------------------------

def _tableSlice(doc: SheetDoc, fromRow: int) -> SheetDoc:
    """The Doc from *fromRow* on, sharing its cells - the implementation
    sheet's preamble becomes the slide's subheading, not table rows."""
    view = SheetDoc(doc.name)
    view.rows = {index: row for index, row in doc.rows.items()
                 if index >= fromRow}
    view.merges = [m for m in doc.merges if m[0] >= fromRow]
    view.widths = doc.widths
    view.signRules = list(doc.signRules)
    view.sections = {index for index in doc.sections if index >= fromRow}
    return view


def _preamblePairs(doc: SheetDoc, headerRow: int):
    pairs = []
    for index in range(1, headerRow):
        row = doc.rows.get(index)
        if not row or 1 not in row.cells or row.cells[1].value is None:
            continue
        label = str(row.cells[1].value)
        value = row.cells.get(2).value if 2 in row.cells else None
        pairs.append('{} {}'.format(label, value) if value is not None else label)
    return pairs


def _continued(subheading, part, parts):
    if parts == 1:
        return subheading
    return '{} — continued, page {} of {}'.format(subheading, part, parts) \
        if part > 1 else '{} — page {} of {}'.format(subheading, part, parts)


_NOTE_ALLOC = ('Category rows show the share of the total portfolio; asset rows sum to '
               'their category. A category one portfolio holds and another does not '
               'prints 0.0, so the columns compare row for row.')
_NOTE_ESTIMATES = ('Estimates derive from the long-term assumptions on the final slide, at '
                   'a 2.5% risk-free rate.')
_NOTE_RISK = ('Stress rows show predicted performance had each portfolio been held '
              'through the episode; losses print red, gains green.')
_NOTE_TAIL = ('VaR and CVaR are stated as positive loss magnitudes at 99% confidence, '
              'over 1 month, 1 year and 3 years.')


#: the words for the shares the rule parks the commitment in (D141)
_SHARE_WORDS = ((1.0 / 3.0, 'a third'), (2.0 / 3.0, 'two thirds'), (0.5, 'half'),
                (1.0, 'all'))


def _shareWords(share: float) -> str:
    """A share in words. The split is kept in percent to four places
    (D148), so a third arrives as 0.333333: within a millionth is close
    enough to say 'a third'. Anything else prints as a percentage."""
    for value, words in _SHARE_WORDS:
        if abs(share - value) < 1e-6:
            return words
    return '{:g}%'.format(round(share * 100, 4))


def _initialSlide(model: dict, includeFees: bool, proposalId: str = None):
    """The slide after the implementation table for a private-markets book
    (D141): its initial allocation against the long-term target - each
    measure a Long-term and an Initial column, no descriptive columns - with
    the table's own arguments for ``planDeck``'s ``tables``: its plans, its
    heading, its subheading and its notes."""
    initial = model['initial']
    committed = initial['commitment']
    doc, headerRow, _ = sheetDoc.buildInitialAllocationDoc(model, includeFees, proposalId,
                                                            deck=True)
    # the measures' row sits above the header and repeats with it
    view = _tableSlice(doc, headerRow - 1)
    count = 2 + 3 * len(sheetDoc.initialMeasures(includeFees, deck=True))
    plans = planTable(view, _dropped(FULL_BOX), [1.0] * count,
                      header=(headerRow - 1, headerRow), natural=True, hold=(0, 1))
    openRows(plans)
    sub = ('Until capital is called, the {:.2f}% (${:,.0f}) committed to private markets is '
           'held {}'.format(committed['weightPct'], committed['notional'], ' and '.join(
               '{} in {}'.format(_shareWords(h['share']), h['category'])
               for h in committed['held'])))
    notes = ['Long-term is the target once the private-markets commitment is fully called; '
             'initial is what the portfolio holds on day one - {}.'.format(' and '.join(
                 '{:.2f}% in {}'.format(h['weightPct'], h['category'])
                 for h in committed['held']))]
    fee, target = initial['total'].get('wtdFeeBp'), model['total'].get('wtdFeeBp')
    notes.append(('Fees are charged on what is invested: {:.1f}bp initially against {:.1f}bp '
                  'long-term. '.format(fee, target) if includeFees and fee is not None
                  and target is not None else '')
                 + 'Each allocation column closes on exactly 100.00%.')
    return plans, 'Implemented Portfolio: Initial Allocation', sub, notes


def planDeck(basis, mandate, results, model, assets=None, includeFees: bool = True,
             variant: str = None, feeSchedule: str = None, feeLevel: str = None,
             proposalId: str = None, customFeesBy: str = None, customFeesAt=None,
             cover: bool = True):
    """Every slide of the deck, decided and not yet drawn (D124).

    Returns a list of slides, each a dict with ``kind`` - 'cover', 'table' or
    'charts' - and for the others its heading, subheading, footnotes and
    either its TablePlans or the items the charts are drawn from. Pure: the
    renderer transcribes it and the tests read it.

    The configuration follows the number of portfolios. With no comparison or
    one, allocation and risk share ONE slide - allocation left, risk right,
    the risk table less the rows that repeat the allocation and with its
    first band's title in the header row beside the names (``headedRisk``),
    both a little in from the slide's edges and set in one size. With two
    comparisons or more, each has a slide of its own at full width and the
    risk table is whole. Should the pair ever not fit one slide at MIN_PT, it
    splits. The allocation's and the risk table's label columns are fitted to
    their longest labels, so none wraps (D128, D130).
    """
    portDoc = sheetDoc.buildPortfoliosDoc(results)
    riskDoc = sheetDoc.buildRiskDoc(results)
    assumDoc = sheetDoc.buildAssumptionsDoc(assets, results)
    # the long-term target's table; a private-markets book's initial
    # allocation follows it on a slide of its own (D141)
    initial = model.get('initial')
    columns = implColumns(includeFees, deck=True)
    implDoc, headerRow, _ = sheetDoc.buildImplementationDoc(
        model, columns, _WIDTHS,
        variant=variant, feeSchedule=feeSchedule, feeLevel=feeLevel,
        includeFees=includeFees, proposalId=proposalId,
        customFeesBy=customFeesBy, customFeesAt=customFeesAt)

    basisLine = '{} · {}'.format(basis.currency, basis.hedging)
    names = ' against '.join(str(r['name']) for r in results)
    money = renderNumber(mandate.mandateSize, '$#,##0')
    preamble = _preamblePairs(implDoc, headerRow)
    count = len(results)
    portShares = _shares(_PORT_LABEL.get(count, 30.0), _docColumns(portDoc))
    riskShares = _shares(_RISK_LABEL.get(count, 20.0), _docColumns(riskDoc))

    slides = []
    if cover:
        slides.append({'kind': 'cover'})

    def tables(plans, heading, sub, notes):
        for part, plan in enumerate(plans, 1):
            slides.append({'kind': 'table', 'heading': heading,
                           'sub': _continued(sub, part, len(plans)),
                           'notes': notes, 'tables': [plan]})

    together = None
    if count <= 2:
        leftBox, rightBox = combinedBoxes()
        # the pair keeps a little padding on every row, and sets smaller
        # where its rows need the height to have it (D140)
        left = planTable(portDoc, leftBox, portShares, fitLabel=True, pad=_PAD_PAIR_EM)
        headed, headedRows = headedRisk(riskDoc)
        right = planTable(headed, rightBox, riskShares, rows=headedRows, fitLabel=True,
                          pad=_PAD_PAIR_EM)
        if len(left) == 1 and len(right) == 1:
            together = left + right
            shareSlide(together)
    if together:
        slides.append({
            'kind': 'table', 'heading': 'Strategic Asset Allocation & Risk',
            'sub': '{} — {} — allocation per asset category, risk per portfolio, '
                   'nominal and real'.format(basisLine, names),
            'notes': [_NOTE_ALLOC, _NOTE_RISK + ' ' + _NOTE_TAIL],
            'tables': together})
    else:
        # each on a slide of its own, rows opened a little and centred (D139)
        portPlans = planTable(portDoc, FULL_BOX, portShares, fitLabel=True)
        strategicRows(portPlans)
        riskPlans = planTable(riskDoc, FULL_BOX, riskShares, fitLabel=True)
        strategicRows(riskPlans)
        tables(portPlans, 'Strategic Asset Allocation',
               '{} — {}, per asset category with portfolio-level estimates'.format(
                   basisLine, names), [_NOTE_ALLOC, _NOTE_ESTIMATES])
        tables(riskPlans, 'Risk Dashboard',
               'Factor-based risk analytics — stress periods and tail-loss measures, '
               'nominal and real, per portfolio', [_NOTE_RISK, _NOTE_TAIL])

    implView = _tableSlice(implDoc, headerRow)
    implNotes = ['Weights are rounded to 2dp by largest remainder across the whole table, '
                 'so the column closes on exactly 100.00%; notionals derive from the '
                 'printed weight on the {} mandate.'.format(money),
                 (('Management fees resolve from the schedule and level named above.'
                   if includeFees else '')
                  + (' This is the long-term target; the initial allocation, held while '
                     'private-markets capital is called, is on the next slide.'
                     if initial else '')).strip()]
    # nothing on a second line, every row one height, a little space under
    # the rule (D137); the category and product columns exactly as wide as
    # their longest names and the rest of the slide's width shared equally
    # among the others, so the table always spans the slide (D138)
    held = [columns.index(c) for c in ('Categories & Asset Classes', 'Products')
            if c in columns]
    implPlans = planTable(implView, _dropped(FULL_BOX), [_WIDTHS[c] for c in columns],
                          header=(headerRow,), natural=True, hold=held)
    openRows(implPlans)
    tables(implPlans, 'Implemented Model', ' · '.join(preamble) if preamble else basisLine,
           implNotes)
    if initial:
        tables(*_initialSlide(model, includeFees, proposalId))

    items = [item for group in model.get('groups', []) for item in group['items']]
    if items:
        slides.append({
            'kind': 'charts', 'heading': 'Composition of the Implemented Model',
            'sub': 'Share of allocation by product attribute — five views of the same 100%',
            'notes': ['Slice colours follow the page’s palette by the alphabetical rule '
                      'the screen uses; printed shares close on exactly 100.0.'],
            'items': items})

    # nothing on a second line and every row one height, across the width
    # as before; moved down under the rule where the slide has the room
    # (D137); its rows at their own height, not opened into the slide (D138)
    # its column heads' row at 1.4 cm, the mean return's head on two lines
    # broken where the sheet breaks it, after 'Return' (D142)
    meanReturn = [(2, c) for c, cell in assumDoc.rows[2].cells.items()
                  if str(cell.value or '').startswith('Estimated Mean Return')]
    assumptions = planTable(assumDoc, FULL_BOX, [w for _, w in sheetDoc.ASSUMPTION_WIDTHS],
                            header=(1, 2), oneLine=True, fixed={2: _ASSUMPTIONS_HEAD_PT},
                            breaks=meanReturn)
    settleDown(assumptions)
    tables(assumptions,
           'Long-Term Capital Market Assumptions',
           'Per-asset estimates behind the analytics — risk premia ranges, volatility '
           'and modelling windows',
           ['Range ends are inked red and green, low — mean — high across; the table '
            'shows only the assets the strategic slides hold.',
            'Estimates are a property of the {} universe recorded at bake time; '
            'modelling windows are per asset, at a 2.5% risk-free rate.'.format(basisLine)])
    return slides


def writeDeck(basis, mandate, results, sleevesMap, autoCategories,
              variant: str = None, tacticalTilt: bool = False,
              feeSchedule: str = None, feeLevel: str = None,
              includeFees: bool = True, volPremium: bool = False,
              assets=None, model: dict = None, proposalId: str = None,
              customFees: dict = None, customFeesBy: str = None,
              customFeesAt=None, cover: bool = True) -> bytes:
    """The proposal deck: the workbook's sheets as slides, same engine (D121).

    Same inputs as ``writeWorkbook``, and the model built once the same way
    (D69). *proposalId* is the UID minted for the delivery - the deck and the
    workbook are delivered together under it and never apart (D123) - and it
    goes in every footer and in ``dc:identifier``. Absent, as for a workbook
    that is not a delivery (a unit test), every footer says draft.

    What goes on which slide, and at what size, is ``planDeck``'s decision
    (D124); this function draws it. Every deck is locked like the workbook
    (D123): a password to modify - the workbook's own - and Mark as Final.
    """
    if not includeFees:
        feeSchedule = None
    if model is None:
        model = buildImplementationRows(results[0], sleevesMap, autoCategories,
                                        mandate.mandateSize, variant,
                                        tacticalTilt, feeSchedule, feeLevel,
                                        mandate.topAccountSize, volPremium,
                                        basis.currency, customFees)
    slides = planDeck(basis, mandate, results, model, assets, includeFees, variant,
                      feeSchedule, feeLevel, proposalId, customFeesBy, customFeesAt,
                      cover)

    basisLine = '{} · {}'.format(basis.currency, basis.hedging)
    money = renderNumber(mandate.mandateSize, '$#,##0')
    footer = ('Proposal UID ' + proposalId if proposalId
              else 'Draft — no delivered proposal for this scenario')

    presentation = Presentation()
    presentation.slide_width = _in(SLIDE_W_IN)
    presentation.slide_height = _in(SLIDE_H_IN)
    blank = presentation.slide_layouts[6]
    total = len(slides)

    for number, plan in enumerate(slides, 1):
        slide = presentation.slides.add_slide(blank)
        if plan['kind'] == 'cover':
            head = _textbox(slide, MARGIN_IN, 2.35, SLIDE_W_IN - 2 * MARGIN_IN, 1.0)
            _para(head, 'Investment Proposal', 34, _HEAD_INK, bold=True, first=True)
            rule = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, _in(MARGIN_IN), _in(3.35),
                _in(3.2), _pt(3))
            rule.fill.solid()
            rule.fill.fore_color.rgb = RGBColor.from_string(_HEAD_INK)
            rule.line.fill.background()
            rule.shadow.inherit = False
            lines = _textbox(slide, MARGIN_IN, 3.62, SLIDE_W_IN - 2 * MARGIN_IN, 1.6)
            details = [basisLine + (' · ' + variant if variant else ''),
                       'Mandate {} · {}'.format(money, mandate.primaryPwa),
                       datetime.date.today().strftime('%d %B %Y')]
            for index, line in enumerate(details):
                _para(lines, line, 13, _SUB_INK, first=index == 0)
            page_ = _textbox(slide, SLIDE_W_IN - MARGIN_IN - 2.5,
                             SLIDE_H_IN - 0.58, 2.5, 0.5)
            _para(page_, footer, 8, _NOTE_INK, first=True, align=PP_ALIGN.RIGHT)
            _para(page_, 'Slide 1 of {}'.format(total), 8, _NOTE_INK,
                  align=PP_ALIGN.RIGHT)
            continue
        _chrome(slide, plan['heading'], plan['sub'], plan['notes'], footer,
                'Slide {} of {}'.format(number, total))
        if plan['kind'] == 'charts':
            _renderDonuts(slide, plan['items'])
        else:
            for table in plan['tables']:
                _renderPlan(slide, table)

    presentation.core_properties.title = ('PMG Proposal {} - {} {}'.format(
        proposalId, basis.currency, basis.hedging) if proposalId
        else 'PMG Proposal - {} {}'.format(basis.currency, basis.hedging))
    presentation.core_properties.author = 'PMG Proposal Tool'
    presentation.core_properties.comments = (
        'Strategic allocation, risk dashboard, long-term estimates and the '
        'implemented model.')
    if proposalId:
        presentation.core_properties.identifier = proposalId
        presentation.core_properties.keywords = proposalId

    _houseTheme(presentation)
    _embedFonts(presentation)
    _protectDeck(presentation)
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def deckFromImplementation(basis, mandate, portfolios, implementation) -> bytes:
    """``writeDeck`` from an adapter's implementation dict - the exact
    unpacking ``build_export`` does for the workbook, deck-shaped (D122), so
    the three adapters cannot disagree about what a deck is either. The
    export calls both with the same dict, UID included (D123)."""
    from . import assetEstimates, rules
    implementation = implementation or {}
    return writeDeck(basis, mandate, list(portfolios),
                     implementation.get('sleeves', {}),
                     rules.autoSleeveCategories(),
                     implementation.get('variant'),
                     bool(implementation.get('tacticalTilt')),
                     implementation.get('feeSchedule'),
                     implementation.get('feeLevel'),
                     implementation.get('includeFees', True),
                     bool(implementation.get('volPremium')),
                     assets=assetEstimates.forSlice(basis.currency, basis.hedging),
                     model=implementation.get('model'),
                     proposalId=implementation.get('proposalId'),
                     customFees=implementation.get('customFees'),
                     customFeesBy=implementation.get('customFeesBy'),
                     customFeesAt=implementation.get('customFeesAt'),
                     cover=implementation.get('cover', True))
