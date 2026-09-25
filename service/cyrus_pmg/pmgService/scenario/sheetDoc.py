"""The report as data - every layout and styling decision, no openpyxl (D119).

Phase 1 of the PowerPoint export plan (proposals/powerpoint-export-plan.html):
each of the workbook's four sheets is built here as a ``SheetDoc`` - rows of
cells carrying values, number-format strings and neutral style attributes -
and ``workbook.py`` renders those Docs onto openpyxl sheets. The renderer is
mechanical; this module is where the report IS decided, once, so a second
renderer (the deck) reads the same decisions rather than reimplementing them.

The one hard rule, pinned by a test: nothing in this module imports openpyxl
or any other file format. A style here is a dict of plain values - face,
points, bold, hex ink, hex fill, border kinds, alignment words - that each
renderer maps to its own classes.

The builders are transcriptions of the writers they replace, comment for
comment where the comment explains a decision. The golden workbook test
(``compareSheets``) held every cell of three sheets steady through the move;
the production styling tests held the fourth.
"""

from __future__ import annotations

import datetime

from . import fees
from . import portfolio_weights as pw

# House palette from the existing report (spec 14.3). The UI navy differs by
# design - open item 2 keeps each surface on its own until the theme decision.
NAVY = '0F243E'
HEADER_NAVY = '092532'
SUBHEAD_NAVY = '092539'           # the bold section labels on the risk sheet
BAND = 'D3DDEA'
WHITE = 'FFFFFF'
BREACH_FILL = 'FDE8E8'            # a position below its product's minimum
BREACH_INK = '9B1C1C'
PREMIA_LOW = 'C00000'             # assumptions: the low end of a range, red
PREMIA_HIGH = '039644'            # ...and the high end, green
HEADING_INK = '092C61'            # a category heading's ink (D79)
#: the stress rule's inks: red below zero, green above (the report's own)
STRESS_NEG = '9C0006'
STRESS_POS = '006100'

# Border kinds, as (style, colour) pairs a renderer turns into its own edges.
DOTTED = ('dotted', 'A9A9A9')
THIN = ('thin', None)
#: the rule that closes a table: solid, thin, black (D78)
BLACK_THIN = ('thin', '000000')
#: the heavier rule that brackets the strategic table (D79)
BLACK_THICK = ('thick', '000000')
HAIR = ('hair', None)


def columnLetter(index: int) -> str:
    """1 -> A, 27 -> AA. Local so the module needs no spreadsheet library."""
    letters = ''
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord('A') + rem) + letters
    return letters


class Cell:
    """One cell's value and the style attributes the writer would set.

    Every field is optional and None means "the writer did not touch this
    attribute" - which a renderer must honour by not touching it either, so
    an untouched cell keeps the format's own defaults exactly as today.
    """

    __slots__ = ('value', 'fmt', 'font', 'fill', 'border', 'align')

    def __init__(self, value=None, fmt=None, font=None, fill=None,
                 border=None, align=None):
        self.value = value
        self.fmt = fmt              # a number-format string, e.g. '0.0%'
        self.font = font            # {'name','size','bold','color'} - set keys only
        self.fill = fill            # solid fill, hex
        self.border = border        # {'top': (style, colour), 'bottom': ...}
        self.align = align          # {'horizontal','vertical','indent','wrap'}


class Row:
    __slots__ = ('cells', 'height')

    def __init__(self):
        self.cells = {}             # column (1-based) -> Cell
        self.height = None

    def cell(self, column: int) -> Cell:
        found = self.cells.get(column)
        if found is None:
            found = self.cells[column] = Cell()
        return found


class SheetDoc:
    """One sheet, decided: rows of cells, merges, widths, the freeze line,
    and the sign rules a renderer projects as conditional formatting or as
    resolved ink. ``sections`` marks the rows a paginator may break above."""

    __slots__ = ('name', 'rows', 'merges', 'widths', 'freeze', 'signRules',
                 'sections')

    def __init__(self, name: str):
        self.name = name
        self.rows = {}              # row (1-based) -> Row
        self.merges = []            # (row1, col1, row2, col2)
        self.widths = {}            # column letter -> width (Excel characters)
        self.freeze = None          # e.g. 'B2'
        self.signRules = []         # (col1, row1, col2, row2, negInk, posInk)
        self.sections = set()       # row numbers a page may start at

    def row(self, index: int) -> Row:
        found = self.rows.get(index)
        if found is None:
            found = self.rows[index] = Row()
        return found

    def cell(self, row: int, column: int) -> Cell:
        return self.row(row).cell(column)

    def maxRow(self) -> int:
        return max(self.rows) if self.rows else 0


# --------------------------------------------------------------------- #
# Shared row-order helpers (moved verbatim from workbook.py).
# --------------------------------------------------------------------- #

def _categoryOrder(results, engineParity: bool = False):
    """Every category and asset in the SUPPLIED UNIVERSE'S order, unioned with
    anything the payloads add.

    The universe leads rather than the payloads so that columns line up: a
    category one book holds and another does not still gets its row, and the
    book without it prints a zero against the book with it. That is the
    report's zero-fill convention (D13) and it is what makes two portfolios
    comparable row by row.

    A category no column holds AT ALL is a different thing, and it is dropped
    (D68). *engineParity* keeps the empty rows, which is how the analytics
    library laid the sheet out; the golden test uses it and nothing else
    should - workbook.py says the rest.
    """
    order, assets = [], {}
    for _, reportingName, category in pw.ASSET_METADATA:
        if category not in order:
            order.append(category)
            assets[category] = []
        if reportingName not in assets[category]:
            assets[category].append(reportingName)
    for result in results:
        for category in result['categories']:
            if category['name'] not in order:
                order.append(category['name'])
                assets[category['name']] = []
            for asset in category['assets']:
                if asset['reportingName'] not in assets[category['name']]:
                    assets[category['name']].append(asset['reportingName'])
    if engineParity:
        return order, assets

    # Keep a row only where SOME column carries weight (D68).
    kept, keptAssets = [], {}
    for category in order:
        live = [name for name in assets[category]
                if any(_weight(result, category, name) for result in results)]
        if not live and not any(_weight(result, category) for result in results):
            continue
        kept.append(category)
        keptAssets[category] = live
    return kept, keptAssets


def _weight(result, categoryName, assetName=None):
    """The weight a column carries. Absent means zero, not blank: the whole
    asset set is padded, which is what lets columns with different holdings
    line up row for row (D13)."""
    for category in result['categories']:
        if category['name'] != categoryName:
            continue
        if assetName is None:
            return category['weightPct']
        for asset in category['assets']:
            if asset['reportingName'] == assetName:
                return asset['weightPct']
        return 0.0
    return 0.0


def _asDate(value):
    """An ISO date string as a datetime, so ``mmm-yy`` has something to format."""
    if isinstance(value, datetime.datetime):
        return value
    try:
        return datetime.datetime.strptime(str(value)[:10], '%Y-%m-%d')
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------- #
# portfolios
# --------------------------------------------------------------------- #

def buildPortfoliosDoc(results, engineParity: bool = False) -> SheetDoc:
    """The strategic allocation, one column per portfolio.

    Note the scale asymmetry, which is the report's own: a CATEGORY row is a
    fraction formatted ``0.0%``, an ASSET row is already x100 and formatted
    ``0.0``. They print identically and store differently."""
    doc = SheetDoc('portfolios')
    light = {'name': 'Calibri Light', 'size': 12.5}
    spacerFont = {'name': 'Arial', 'size': 12.5}
    metricFont = {'name': 'Calibri', 'size': 12.5, 'color': WHITE}

    # How this sheet is dressed in a delivered proposal, against how the
    # library dressed it (D79). engineParity keeps the library's own styling,
    # which is what the golden workbook was captured with.
    headFont = light if engineParity else {'name': 'Calibri Light', 'size': 12.5,
                                           'bold': True}
    headRule = {'bottom': DOTTED if engineParity else BLACK_THICK}
    headingFont = light if engineParity else {'name': 'Calibri Light', 'size': 12.5,
                                              'bold': True, 'color': HEADING_INK}
    headingFill = BAND if engineParity else WHITE
    # each category opens with a thin rule, so the blocks read apart now that
    # the blue band no longer separates them (D80)
    headingRule = None if engineParity else {'top': BLACK_THIN}
    totalRule = {'top': DOTTED if engineParity else BLACK_THICK}
    right = {'horizontal': 'right', 'vertical': 'center', 'indent': 4}
    columns = len(results)

    def style(row, font, fill=WHITE, fmt=None, border=None, height=None,
              labelAlign='left', vertical='center', labelFmt=False):
        if height is not None:
            doc.row(row).height = height
        for index in range(1, columns + 2):
            cell = doc.cell(row, index)
            cell.font = font
            cell.fill = fill
            if border is not None:
                cell.border = border
            if index == 1:
                cell.align = {'horizontal': labelAlign, 'vertical': vertical}
                if labelFmt and fmt:
                    cell.fmt = fmt
            else:
                cell.align = (right if vertical
                              else {'horizontal': 'right', 'indent': 4})
                if fmt:
                    cell.fmt = fmt

    row = 1
    for index, result in enumerate(results, start=2):
        doc.cell(row, index).value = result['name']
    style(1, headFont, border=headRule, height=37, labelAlign=None)
    for index in range(2, columns + 2):
        doc.cell(1, index).align = {'horizontal': 'center', 'vertical': 'center'}

    order, assets = _categoryOrder(results, engineParity)
    for categoryName in order:
        row += 1
        doc.sections.add(row)
        doc.cell(row, 1).value = categoryName
        for index, r in enumerate(results, start=2):
            w = _weight(r, categoryName)
            doc.cell(row, index).value = None if w is None else w / 100.0
        style(row, headingFont, headingFill, '0.0%', border=headingRule, height=17)
        for assetName in assets[categoryName]:
            row += 1
            doc.cell(row, 1).value = '  ' + assetName
            for index, r in enumerate(results, start=2):
                w = _weight(r, categoryName, assetName)
                doc.cell(row, index).value = None if w is None else w
            style(row, light, fmt='0.0', height=17)

    row += 1
    doc.cell(row, 1).value = 'TOTAL'
    for index in range(2, columns + 2):
        doc.cell(row, index).value = 1.0
    style(row, headingFont, fmt='0.0%', border=totalRule, height=23,
          labelAlign=None, vertical=None)
    if not engineParity:
        # An empty row under the total, so the metric bands below read as a
        # separate block rather than as a continuation of the table (D79).
        # Styled but never written to: it carries the sheet's white ground
        # and nothing else.
        row += 1
        style(row, light)

    for label, field, fmt in (('Estimated Mean Return', 'estimatedReturnPct', '0.0%'),
                              ('Sharpe Ratio', 'sharpe', '0.00'),
                              ('Volatility', 'volatilityPct', '0.0%')):
        if not engineParity or label != 'Sharpe Ratio':
            # A hairline spacer above each block. The report has one above the
            # return and the volatility but not above the Sharpe ratio; a
            # delivered proposal parts all three evenly (D79).
            row += 1
            for index in range(2, columns + 2):
                doc.cell(row, index).value = 0
            style(row, spacerFont, height=3, labelAlign=None, vertical=None)
            for index in range(2, columns + 2):
                doc.cell(row, index).align = {}
                doc.cell(row, index).fmt = 'General'
        scale = 100.0 if fmt == '0.0%' else 1.0
        row += 1
        doc.cell(row, 1).value = label
        for index, r in enumerate(results, start=2):
            doc.cell(row, index).value = r['metrics'][field] / scale
        style(row, metricFont, NAVY, fmt, height=23, labelAlign=None)
        doc.cell(row, 1).fmt = '0.0%'

    doc.widths['A'] = 40
    for index in range(2, columns + 2):
        doc.widths[columnLetter(index)] = 18
    doc.freeze = 'B2'                                          # enhancement
    return doc


# --------------------------------------------------------------------- #
# risk_dashboard
# --------------------------------------------------------------------- #

def buildRiskDoc(results, engineParity: bool = False) -> SheetDoc:
    """Risk, one PAIR of columns per portfolio - nominal and real.

    The pair is merged on the summary rows and split from the stress block
    down. VaR and CVaR are printed POSITIVE: the payload stores them negated
    because the screen reads them as losses, and the sheet does not."""
    doc = SheetDoc('risk_dashboard')
    narrow = {'name': 'Aptos Narrow', 'size': 12}
    # The category and metric labels take the strategic sheet's heading ink
    # (D80), in this sheet's own face. The LABEL only.
    headingFont = narrow if engineParity else {'name': 'Aptos Narrow', 'size': 12,
                                               'bold': True, 'color': HEADING_INK}
    onNavy = {'name': 'Aptos Narrow', 'size': 12, 'color': WHITE}
    section = {'name': 'Aptos Narrow', 'size': 12, 'bold': True,
               'color': SUBHEAD_NAVY}
    centre = {'horizontal': 'center'}
    columns = len(results)
    span = columns * 2

    def paint(row, font, fill=WHITE, fmt=None, height=16, labelAlign=None,
              border=None, vertical=None, indent=0):
        doc.row(row).height = height
        for index in range(1, span + 2):
            cell = doc.cell(row, index)
            cell.font = font
            cell.fill = fill
            if border is not None:
                cell.border = border
            if index == 1:
                cell.align = {'horizontal': labelAlign, 'vertical': vertical,
                              'indent': indent}
            else:
                cell.align = ({'horizontal': 'center', 'vertical': vertical}
                              if vertical else centre)
                if fmt:
                    cell.fmt = fmt

    def merge(row):
        for column in range(columns):
            first = 2 + column * 2
            doc.merges.append((row, first, row, first + 1))

    row = 1
    for index, result in enumerate(results):
        doc.cell(row, 2 + index * 2).value = result['name']
    # Every row of this table is 16 high on request (D78). engineParity
    # restores the library's three taller ones for the golden comparison.
    paint(1, onNavy, HEADER_NAVY, height=35 if engineParity else 16,
          vertical='center')
    merge(1)

    order, _ = _categoryOrder(results, engineParity)
    for categoryName in order:
        row += 1
        doc.cell(row, 1).value = categoryName
        for index, result in enumerate(results):
            weight = _weight(result, categoryName)
            doc.cell(row, 2 + index * 2).value = (None if weight is None
                                                  else weight / 100.0)
        paint(row, narrow, fmt='0.0%',
              height=20 if engineParity and row == 2 else 16)
        doc.cell(row, 1).font = headingFont
        merge(row)

    # The library's four unlabelled rows print "0.0%" against a blank label;
    # they go in production (D68) and engineParity restores them.
    metrics = [('Estimated Mean Return', 'estimatedReturnPct', '0.0%'),
               ('Sharpe Ratio', 'sharpe', '0.00'),
               ('Volatility', 'volatilityPct', '0.0%')]
    if engineParity:
        metrics = ([(None, None, None)] * 2 + metrics[:2]
                   + [(None, None, None)] * 2 + metrics[2:])
    for label, field, fmt in metrics:
        row += 1
        if label is None:
            for index in range(columns):
                doc.cell(row, 2 + index * 2).value = 0
            paint(row, narrow, fmt='0.0%')
        else:
            scale = 100.0 if fmt == '0.0%' else 1.0
            doc.cell(row, 1).value = label
            for index, result in enumerate(results):
                doc.cell(row, 2 + index * 2).value = result['metrics'][field] / scale
            paint(row, narrow, fmt=fmt,
                  border={'top': DOTTED} if field == 'estimatedReturnPct' else None)
            doc.cell(row, 1).font = headingFont
        merge(row)

    def band(title):
        nonlocal row
        row += 1
        doc.sections.add(row)
        doc.cell(row, 1).value = title
        paint(row, onNavy, HEADER_NAVY, vertical='center')

    def subhead(title, heads=False):
        nonlocal row
        row += 1
        doc.sections.add(row)
        doc.cell(row, 1).value = title
        if heads:
            for index in range(columns):
                doc.cell(row, 2 + index * 2).value = 'Nominal'
                doc.cell(row, 3 + index * 2).value = 'Real'
        paint(row, section, border={'top': DOTTED})
        for index in range(2, span + 2):
            doc.cell(row, index).font = narrow

    def rows(entries, read, indent='  '):
        nonlocal row
        for entry in entries:
            row += 1
            doc.cell(row, 1).value = indent + entry
            for index, result in enumerate(results):
                nominal, real = read(result, entry)
                doc.cell(row, 2 + index * 2).value = nominal
                doc.cell(row, 3 + index * 2).value = real
            # the report gives the first stress row a taller band and the
            # rest a plain one; nothing else on the sheet varies
            paint(row, narrow, fmt='0.0%', labelAlign='left', indent=1,
                  height=20 if engineParity and row == 18 else 16)

    band('Factor Based Risk Analytics')
    subhead('Predicted Performance Over Stress Periods', heads=True)
    stressStart = row + 1
    periods = [s['period'] for s in (results[0]['stress'] if results else [])]

    def readStress(result, period):
        for entry in result['stress']:
            if entry['period'] == period:
                return entry['nominalPct'] / 100.0, entry['realPct'] / 100.0
        return None, None
    rows(periods, readStress)
    stressEnd = row

    horizons = ['Over 1 Month', 'Over 1 Year', 'Over 3 Years']
    for group in ('Value at Risk with 99% Confidence',
                  'Conditional Value at Risk with 99% Confidence',
                  'Probability of Loss'):
        subhead(group)

        def readPremia(result, horizon, group=group):
            for entry in result['premia']:
                if entry['group'] == group and entry['horizon'] == horizon:
                    # stored negated for the screen; printed positive here
                    sign = -1.0 if entry.get('kind') == 'loss' else 1.0
                    return (sign * entry['nominalPct'] / 100.0,
                            sign * entry['realPct'] / 100.0)
            return None, None
        rows(horizons, readPremia)

    # The library closed the sheet with a navy band naming the premia blocks
    # above it. It goes (D78): the table ends on its last figure, ruled off
    # in solid black. engineParity keeps the band for the golden comparison.
    if engineParity:
        band('Portfolio Risk Premia')
    else:
        for index in range(1, span + 2):
            doc.cell(row, index).border = {'bottom': BLACK_THIN}

    # red below zero, green above - the report's own rule over the stress
    # block. Declared once here; the Excel renderer projects it as the same
    # CellIsRule as ever, and a deck resolves it per cell from the raw value
    # (strict inequalities: exact zero keeps the base ink).
    doc.signRules.append((2, stressStart, span + 1, stressEnd,
                          STRESS_NEG, STRESS_POS))

    doc.widths['A'] = 40
    for index in range(2, span + 2):
        doc.widths[columnLetter(index)] = 15
    doc.freeze = 'B2'                                          # enhancement
    return doc


# --------------------------------------------------------------------- #
# assumptions
# --------------------------------------------------------------------- #

#: the assumptions sheet's columns: (header, key, number format, alignment,
#: font colour). The two range ends are coloured because the sheet is read as
#: "low - mid - high" across, and the colour is what makes that legible.
ASSUMPTION_COLUMNS = [
    ('Risk Premia\n with Estimated Range', 'lower', '0.0%', 'right', PREMIA_LOW),
    ('Mean', 'mean', '0.0%', 'center', None),
    ('Upper Range', 'upper', '0.0%', 'left', PREMIA_HIGH),
    ('Volatility', 'volatility', '0.0%', 'center', None),
    ('Sharpe Ratio', 'sharpe', '0.00', 'center', None),
    ('Estimated Mean Return\n(2.5% Risk Free Rate)', 'totalReturn', '0.0%', 'center', None),
    ('Hedging Ratio', 'hedgingRatio', '0%', 'center', None),
    ('From', 'from', 'mmm-yy', 'center', None),
    ('To', 'to', 'mmm-yy', 'center', None),
]

ASSUMPTION_WIDTHS = [('A', 40), ('B', 7), ('C', 7), ('D', 7), ('E', 12),
                     ('F', 14.5), ('G', 26), ('H', 16.5), ('I', 11), ('J', 11)]


def buildAssumptionsDoc(assets, results=None, engineParity: bool = False) -> SheetDoc:
    """The long-term estimates behind the analytics, one row per asset (D67).

    Filtered to the rows the strategic sheets show (D68): an assumption
    printed against an asset no portfolio in the lineup holds explains
    nothing. Given no assets, the sheet still appears and says why it is
    empty."""
    doc = SheetDoc('assumptions')
    body = {'name': 'Grotesque', 'size': 11}
    plain = {'name': 'Calibri', 'size': 11}
    heading = {'name': 'Grotesque', 'size': 11, 'bold': True, 'color': NAVY}
    thinBottom = {'bottom': THIN}
    centred = {'horizontal': 'center', 'vertical': 'center'}

    # ---- the two header rows ------------------------------------------
    doc.cell(1, 1)                                   # materialised, value None
    doc.cell(1, 2).value = 'Long-Term Estimates'
    doc.cell(1, 9).value = 'Modelling Dates'
    doc.merges.append((1, 2, 1, 7))
    doc.merges.append((1, 9, 1, 10))
    for index in range(1, 11):
        cell = doc.cell(1, index)
        letter = columnLetter(index)
        cell.font = plain if letter in ('C', 'D', 'E', 'F', 'G', 'J') else body
        if letter not in ('C', 'D', 'E', 'F', 'G', 'J'):
            cell.fill = WHITE
        cell.align = centred if index > 1 else {'vertical': 'center'}
        if letter not in ('A', 'H'):
            cell.border = thinBottom
    doc.row(1).height = 20

    doc.cell(2, 1)                                   # materialised, value None
    doc.merges.append((2, 2, 2, 4))
    for offset, (header, _, _, _, _) in enumerate(ASSUMPTION_COLUMNS):
        if offset in (1, 2):
            continue                       # inside the merged range-of-three
        doc.cell(2, 2 + offset).value = header
    doc.cell(2, 2).value = ASSUMPTION_COLUMNS[0][0]
    for index in range(1, 11):
        cell = doc.cell(2, index)
        cell.font = body
        cell.fill = WHITE
        cell.border = thinBottom
        wrap = index in (2, 7)
        cell.align = ({'horizontal': 'center', 'vertical': 'center', 'wrap': True}
                      if wrap else (centred if index > 1 else {'vertical': 'center'}))
    doc.row(2).height = 49

    # ---- the body -----------------------------------------------------
    # the same rows the strategic sheets kept, in the same order
    if results and not engineParity:
        order, keptAssets = _categoryOrder(results)
        allowed = {(category, name)
                   for category in order for name in keptAssets[category]}
        assets = [entry for entry in (assets or [])
                  if (entry.get('category'), entry.get('reportingName')) in allowed]

    row = 3
    if not assets:
        doc.cell(row, 1).value = (
            'The asset estimates for this basis are not in the baked store. '
            'Re-run the bake to record them.')
        doc.cell(row, 1).font = heading
        doc.row(row).height = 17
    else:
        firstCategory = True
        seen = set()
        for entry in assets:
            category = entry.get('category') or ''
            if category and category not in seen:
                seen.add(category)
                doc.sections.add(row)
                doc.cell(row, 1).value = category
                for index in range(1, 11):
                    cell = doc.cell(row, index)
                    cell.fill = WHITE
                    if not firstCategory:
                        cell.border = {'top': HAIR}
                    if index == 1:
                        cell.font = heading
                        continue
                    _, _, fmt, align, colour = ASSUMPTION_COLUMNS[index - 2]
                    # a category header is bold navy, except the upper end of
                    # the range which keeps its green from the second header
                    # down - the range ends stay red and green all the way
                    keepsGreen = colour == PREMIA_HIGH and not firstCategory
                    cell.font = {'name': 'Grotesque', 'size': 11, 'bold': True,
                                 'color': PREMIA_HIGH if keepsGreen else NAVY}
                    if firstCategory:
                        # the first header row sits directly under the column
                        # heads and takes their plain treatment - bar the
                        # Sharpe column, which keeps its format throughout
                        cell.fmt = fmt if fmt == '0.00' else 'General'
                        cell.align = {'horizontal': 'center'}
                    else:
                        cell.fmt = fmt
                        cell.align = {'horizontal': align}
                doc.row(row).height = 17
                firstCategory = False
                row += 1
            doc.cell(row, 1).value = '    ' + str(entry.get('reportingName', ''))
            doc.cell(row, 1).font = body
            doc.cell(row, 1).fill = WHITE
            for offset, (_, key, fmt, align, colour) in enumerate(ASSUMPTION_COLUMNS):
                cell = doc.cell(row, 2 + offset)
                value = entry.get(key)
                cell.value = _asDate(value) if fmt == 'mmm-yy' else value
                cell.font = ({'name': 'Grotesque', 'size': 11, 'color': colour}
                             if colour else body)
                cell.fill = WHITE
                cell.fmt = fmt
                cell.align = {'horizontal': align}
            doc.row(row).height = 17
            row += 1

    if row > 3:
        # a closing rule under the table, as the report has it
        for index in range(1, 11):
            doc.cell(row - 1, index).border = {'bottom': THIN}
    for letter, width in ASSUMPTION_WIDTHS:
        doc.widths[letter] = width
    doc.freeze = 'A3'                                          # enhancement
    return doc


# --------------------------------------------------------------------- #
# Implementation
# --------------------------------------------------------------------- #

def buildImplementationDoc(model: dict, columns, widths, textColumns,
                           variant=None, feeSchedule=None, feeLevel=None,
                           includeFees: bool = True, proposalId=None,
                           customFeesBy=None, customFeesAt=None):
    """The implementation sheet as a Doc: the preamble, the header, grouped
    categories with product line items, subtotals and a grand total.

    Returns ``(doc, headerRow, totalRow)`` - the two rows the Excel side
    still needs, for the freeze line's sake and for anchoring the doughnut
    charts beneath the table. *model* is the prebuilt implementation model
    (D69); this function decides only presentation."""
    doc = SheetDoc('Implementation')
    at = {name: index for index, name in enumerate(columns, start=1)}
    headFont = {'name': 'Aptos Narrow', 'size': 12, 'bold': True, 'color': WHITE}
    bodyFont = {'name': 'Aptos Narrow', 'size': 12}
    boldFont = {'name': 'Aptos Narrow', 'size': 12, 'bold': True}

    row = 0
    preamble = []
    # The UID first, at A1: the one place in the file a reader finds it
    # without knowing where to look (D75).
    if proposalId:
        preamble.append(['Proposal UID', proposalId])
    if variant:
        preamble.append(['Implementation Type', variant])
    if model['priced']:
        preamble.append(['Fee Schedule', feeSchedule])
        preamble.append(['Fee Level', feeLevel or fees.DEFAULT_LEVEL])
        # Under the custom level the rates are the PWA's, one per row (D96).
        # Under a marginal schedule the blended rate is what a reader needs
        # (D83). A flat one names its tier.
        if model.get('custom'):
            for feeRow, rate in model.get('customRates') or []:
                preamble.append(['Custom Rate' + (' · ' + feeRow if feeRow else ''),
                                 '{:.2f}%'.format(rate) if rate is not None else 'not set'])
            if customFeesBy or customFeesAt:
                when = (datetime.datetime.fromtimestamp(customFeesAt).strftime('%Y-%m-%d')
                        if customFeesAt else '')
                preamble.append(['Custom Rates Entered By',
                                 ' · '.join(x for x in (customFeesBy, when) if x)])
        elif model.get('marginal'):
            preamble.append(['Effective Rate', '{:.4f}%'.format(model['effectiveRate'])])
        elif model.get('tier'):
            preamble.append(['Account Size Tier',
                             '{} ({})'.format(model['tier']['id'], model['tier']['label'])])
        # which card priced it (D55)
        card = fees.deliveryInfo()
        preamble.append(['Fee Card', '{}{}'.format(
            card.get('version') or 'unversioned',
            ' · placeholder' if card.get('placeholder') else '')])
    for line in preamble:
        row += 1
        doc.cell(row, 1).value = line[0]
        doc.cell(row, 1).font = boldFont
        doc.cell(row, 2).value = line[1]
    if preamble:
        row += 1                                     # the blank line beneath
    headerRow = row + 1

    row = headerRow
    for index, name in enumerate(columns, start=1):
        cell = doc.cell(row, index)
        cell.value = name
        cell.font = headFont
        cell.fill = HEADER_NAVY
    doc.row(row).height = 20
    doc.freeze = 'A' + str(headerRow + 1)

    def weightCell(cell, pct):
        cell.value = pct / 100.0
        cell.fmt = '0.00%'

    def feeCell(cell, pct):
        if pct is None:                     # unpriced: the cell stays empty
            return
        cell.value = pct / 100.0
        cell.fmt = '0.00%'

    def bpCell(cell, bp):
        if bp is None:
            return
        cell.value = bp
        cell.fmt = '0.0'

    def bpAt(rowIndex, value):
        """The weighted-fee cell, when the sheet has one."""
        if includeFees:
            bpCell(doc.cell(rowIndex, at['Wtd fee (bp)']), value)

    for group in model['groups']:
        # The category alone: the Products cell of a band row is left empty
        # on request (D78) - the band names the category the products beneath
        # it belong to.
        row += 1
        doc.sections.add(row)
        doc.cell(row, 1).value = group['category']
        for column in range(1, len(columns) + 1):
            cell = doc.cell(row, column)
            cell.fill = BAND
            cell.font = boldFont
        weightCell(doc.cell(row, 3),
                   sum(i['printedPct'] for i in group['items'])
                   if group['items'] else group['weightPct'])
        if group['items']:
            bpAt(row, sum(i['wtdFeeBp'] for i in group['items'])
                 if model['priced'] else None)
            notional = doc.cell(row, at['Notional'])
            notional.value = sum(i['notional'] for i in group['items'])
            notional.fmt = '$#,##0'
        for item in group['items']:
            row += 1
            line = [
                '  ' + item['assetClass'], item['name'], None,
                item['style'], item['vehicle'], item.get('shareClass'), item['source'],
                item['liquidity'], item['exposureCurrency'], None,
            ]
            if includeFees:
                line += [None, None]
            line += [None]
            for column, value in enumerate(line, start=1):
                doc.cell(row, column).value = value
            for column in range(1, len(columns) + 1):
                cell = doc.cell(row, column)
                cell.font = bodyFont
                # The table stands on white rather than on the sheet's
                # default nothing, so the grid does not show through it and
                # the block reads as one object (D78).
                cell.fill = WHITE
            weightCell(doc.cell(row, 3), item['printedPct'])
            feeCell(doc.cell(row, at['Product Cost']), float(item['productCost']))
            if includeFees:
                feeCell(doc.cell(row, at['Mgmt fee']), item['managementFee'])
            bpAt(row, item['wtdFeeBp'])
            notional = doc.cell(row, at['Notional'])
            notional.value = item['notional']
            notional.fmt = '$#,##0'
            if item.get('belowMinimum'):
                # the sheet says so too: a workbook read away from the page
                # must not look clean when the page refused to export it (D78)
                notional.font = {'name': 'Calibri', 'size': 11, 'bold': True,
                                 'color': BREACH_INK}
                notional.fill = BREACH_FILL

    row += 1
    doc.cell(row, 1).value = 'Total'
    for column in range(1, len(columns) + 1):
        cell = doc.cell(row, column)
        cell.font = boldFont
        cell.fill = WHITE
        # ruled above and below: the total closes the table, solid black and
        # thin, so the close is a rule rather than another separator (D78)
        cell.border = {'top': BLACK_THIN, 'bottom': BLACK_THIN}
    weightCell(doc.cell(row, 3), model['total']['weightPct'])
    # the blend every row carries, restated where a reader looks for a total
    if includeFees and model.get('marginal') and model.get('effectiveRate') is not None:
        feeCell(doc.cell(row, at['Mgmt fee']), model['effectiveRate'])
    bpAt(row, model['total']['wtdFeeBp'])
    notional = doc.cell(row, at['Notional'])
    notional.value = model['total']['notional']
    notional.fmt = '$#,##0'
    totalRow = row

    for index, name in enumerate(columns, start=1):
        doc.widths[columnLetter(index)] = widths[name]
    text = [at[name] for name in textColumns if name in at]
    for rowIndex in range(headerRow + 1, totalRow + 1):
        for column in range(3, len(columns) + 1):
            doc.cell(rowIndex, column).align = {'horizontal': 'right'}
        for column in text:
            doc.cell(rowIndex, column).align = {'horizontal': 'left'}
    return doc, headerRow, totalRow
