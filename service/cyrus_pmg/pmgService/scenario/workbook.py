"""Excel writers - the whole workbook, with no analytics library behind it.

Four sheets, and none of them needs ``Reporting`` or a ``SAAPortfolio``
(D67). Every figure on ``portfolios`` and ``risk_dashboard`` was already
carried by the resolved payloads the export passes in; ``assumptions`` reads
the per-asset estimates the bake now records beside them; ``Implementation``
was always the tool's own work. That is what lets the service be ported
somewhere the analytics library cannot go.

Three jobs live here:

* ``buildImplementationRows`` + ``writeImplementationSheet`` - the
  implementation model as a sheet (spec 14.4), with the rounding rule of spec
  8.4 applied exactly as on screen: every printed figure derives from the
  printed weight, so the workbook hand-reconciles against the page.
  ``buildImplementationRows`` is the single Python source of those numbers -
  the real adapter and the fixtures adapter both use it, and the test suite
  checks it against the page's JavaScript mirror.

* ``writePortfoliosSheet`` / ``writeRiskDashboardSheet`` /
  ``writeAssumptionsSheet`` - the three sheets the analytics library used to
  lay out, reproduced from the payloads cell for cell. Layout, fonts, fills,
  number formats, merges, row heights and the one conditional-formatting rule
  are the house report's own (spec 14.3), taken from a captured reference
  workbook rather than guessed at. Two conventions are deliberate and easy to
  break: asset rows carry weights x100 with format ``0.0`` while category and
  total rows carry fractions with ``0.0%``; and VaR and CVaR are stored
  NEGATED in the payload (the screen reads them as losses) but printed
  positive here.

* ``writeWorkbook`` - the four sheets in order, the only export path.
"""

from __future__ import annotations

import datetime
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from openpyxl.chart import DoughnutChart, Reference
from openpyxl.chart.series import DataPoint
from openpyxl.formatting.rule import CellIsRule

from . import fees, rules
from . import portfolio_weights as pw
from .payloads import roundWeightsLargestRemainder
from .rules import sleeveCategory
from .sleeves import listSleeves

# House palette from the existing report (spec 14.3). The UI navy differs by
# design - open item 2 keeps each surface on its own until the theme decision.
_NAVY = '0F243E'
_HEADER_NAVY = '092532'
_SUBHEAD_NAVY = '092539'          # the bold section labels on the risk sheet
_BAND = 'D3DDEA'
_WHITE = 'FFFFFF'
_BREACH_FILL = 'FDE8E8'           # a position below its product's minimum
_BREACH_INK = '9B1C1C'
_PREMIA_LOW = 'C00000'            # assumptions: the low end of a range, red
_PREMIA_HIGH = '039644'           # ...and the high end, green
_DOTTED = Side(style='dotted', color='A9A9A9')
_THIN = Side(style='thin')
_HAIR = Side(style='hair')
_WHITE_FILL = PatternFill('solid', fgColor=_WHITE)

# The nine stress periods and three horizons arrive in the payload in the
# order the engine produced them; the sheet prints them in that same order.
_ASSUMPTION_WIDTHS = [('A', 40), ('B', 7), ('C', 7), ('D', 7), ('E', 12),
                      ('F', 14.5), ('G', 26), ('H', 16.5), ('I', 11), ('J', 11)]

# The composition doughnuts under the implementation table, and the palette
# the page draws them in (--cat-1..7). A slice takes its colour from where its
# name falls in the ALPHABETICAL order of that dimension's names, then the
# slices are ordered biggest-first to read - the same two steps the page's
# breakdown() does, so a chart in the workbook is the chart on the screen.
DONUT_DIMENSIONS = [('style', 'Style'), ('vehicle', 'Vehicle'), ('source', 'Source'),
                    ('liquidity', 'Liquidity'), ('exposureCurrency', 'Exposure currency')]
DONUT_PALETTE = ['2A78D6', 'EB6834', '1BAF7A', 'EDA100', 'E87BA4', '4A3AA7', 'E34948']
_CHART_SHEET = 'chartData'

# The columns of spec 9.3, with 'Minimum Investment' beside the notional it
# is checked against. The fee group is resolved from the product and still
# prices the management fee; it is no longer a column of its own.
IMPL_COLUMNS = [
    'Categories & Asset Classes', 'Products', 'Allocation (%)', 'Ticker',
    'Style', 'Vehicle', 'Source', 'Liquidity', 'Exposure ccy', 'Product Cost',
    'Mgmt fee', 'Wtd fee (bp)', 'Minimum Investment', 'Notional',
]
# The two the sheet loses when the proposal excludes fees (D52). A proposal
# that does not show fees must not ship a sheet with empty columns and a
# header saying which schedule priced them: the columns go, and so do the fee
# rows above the header.
FEE_COLUMNS = ('Mgmt fee', 'Wtd fee (bp)')
# The minimum sits beside the notional it is compared against, not beside the
# cost: a reader checking a position against its minimum reads the two
# adjacent figures rather than looking across the sheet.
_WIDTHS = {
    'Categories & Asset Classes': 34, 'Products': 32, 'Allocation (%)': 12,
    'Ticker': 9, 'Style': 9, 'Vehicle': 12, 'Source': 10, 'Liquidity': 11,
    'Exposure ccy': 12, 'Product Cost': 12, 'Mgmt fee': 10,
    'Wtd fee (bp)': 12, 'Minimum Investment': 18, 'Notional': 14,
}


def implColumns(includeFees: bool = True) -> list:
    """The sheet's columns, in order, for a priced or an unpriced proposal."""
    if includeFees:
        return list(IMPL_COLUMNS)
    return [name for name in IMPL_COLUMNS if name not in FEE_COLUMNS]


def buildImplementationRows(baseResult: dict, sleevesMap: dict,
                            autoCategories, mandateSize: float,
                            variant: str = None, tacticalTilt: bool = False,
                            feeSchedule: str = None, feeLevel: str = None,
                            topAccountSize: float = None,
                            volPremium: bool = False,
                            currency: str = None) -> dict:
    """The implementation model's numbers, derived per spec 8.4.

    Returns {'groups': [...], 'total': {...}, 'complete': bool, 'priced': bool,
    'tier': {...} | None}. Each group is a category: its sleeve name (auto
    categories take their single library sleeve), product line items with
    printed weight, weighted fee in bp and notional, and group subtotals.
    Weights are rounded to 2dp by largest remainder across the WHOLE table so
    the column sums to exactly 100.00; notional and weighted fee derive from
    that printed weight.

    The management fee is resolved per product from *feeSchedule*, the tier
    that *topAccountSize* falls in, *feeLevel* and the product's fee group
    (D51). Without a schedule the model is unpriced: ``managementFee`` and
    ``wtdFeeBp`` are None on every item and in the total. The level defaults
    to the framework's prescribed one, as the store does; the schedule never
    defaults.
    """
    autoCategories = set(autoCategories or [])
    groups = []
    lineItems = []
    priced = feeSchedule is not None
    tier = fees.tierFor(topAccountSize) if priced else None
    feeLevel = feeLevel or fees.DEFAULT_LEVEL

    # The implemented book, not the strategic one: with the tilt on, the
    # funding category is reduced and the tilt category appended (D50); with
    # the volatility premium on, the same category gives up a share of what is
    # left and Hybrid Fixed Income follows it (D53). The currency goes in
    # because the premium is forbidden outside USD and GBP, and the rule
    # belongs where the model is built, not only where the toggle is drawn.
    categories = rules.implementedCategories(baseResult['categories'],
                                             tacticalTilt, volPremium, currency)

    # Categories that share one sleeve share one LINE. Private Equity and
    # Other Private Assets are a single choice in the rail (D60) and a single
    # sleeve in the repository; showing them as two rows against the same
    # sleeve printed that sleeve twice and split a position that is bought
    # once. The combined weight is the sum of theirs, and the row takes the
    # group's own name. Driven off SLEEVE_GROUPS, so a second group needs no
    # code here.
    combined, order = {}, []
    for category in categories:
        key = sleeveCategory(category['name'])
        if key not in combined:
            combined[key] = {'name': key, 'weightPct': 0.0}
            order.append(key)
        combined[key]['weightPct'] += float(category['weightPct'])
    categories = [combined[key] for key in order]

    for category in categories:
        name = category['name']
        catWeight = float(category['weightPct'])
        # grouped categories share one choice, stored under the group (D60)
        pickedUnder = sleeveCategory(name)
        if name in autoCategories:
            library = listSleeves(pickedUnder, variant)
            sleeve = library[0] if library else None
            auto = True
        else:
            chosen = (sleevesMap or {}).get(pickedUnder)
            sleeve = None
            if chosen:
                sleeve = next((s for s in listSleeves(pickedUnder, variant)
                               if s['name'] == chosen), None)
            auto = False
        items = []
        if sleeve:
            for product in sleeve['products']:
                item = dict(product)
                item['exactPct'] = catWeight * float(product['weight'])
                items.append(item)
                lineItems.append(item)
        groups.append({
            'category': name,
            'weightPct': catWeight,
            'sleeve': sleeve['name'] if sleeve else None,
            'auto': auto,
            'items': items,
        })

    complete = all(group['sleeve'] for group in groups) and bool(groups)

    if lineItems:
        if complete:
            printed = roundWeightsLargestRemainder([i['exactPct'] for i in lineItems])
        else:
            printed = [round(i['exactPct'], 2) for i in lineItems]
        for item, weight in zip(lineItems, printed):
            item['printedPct'] = weight
            item['notional'] = round(mandateSize * weight / 100.0 / 100.0) * 100.0
            if priced:
                item['managementFee'] = fees.managementFee(
                    feeSchedule, topAccountSize, feeLevel, item['feeGroup'])
                allIn = float(item['productCost']) + item['managementFee']
                item['wtdFeeBp'] = allIn * weight        # percent x percent = bp
            else:
                item['managementFee'] = None
                item['wtdFeeBp'] = None
            # A position smaller than the product will accept is not a
            # position (item 3). Flagged per line here; the export refuses
            # while any survives, so the block cannot be walked past.
            minimum = item.get('minimumInvestment')
            item['belowMinimum'] = bool(minimum) and item['notional'] < float(minimum)

    total = {
        'weightPct': sum(i.get('printedPct', 0.0) for i in lineItems),
        'wtdFeeBp': sum(i.get('wtdFeeBp') or 0.0 for i in lineItems) if priced else None,
        'notional': sum(i.get('notional', 0.0) for i in lineItems),
    }

    # Nothing that prints as zero earns a line (D68). Dropped AFTER the
    # rounding, so the largest-remainder pass still closes the column on
    # 100.00 exactly - a row worth 0.00 contributes nothing to that sum and
    # taking it out cannot move it. A category keeps its row while it has
    # weight, sleeve or no sleeve: an unimplemented category with an
    # allocation is the thing the page is asking a PWA to fix.
    for group in groups:
        group['items'] = [item for item in group['items']
                          if item.get('printedPct', 0.0) != 0]
    groups = [group for group in groups if group['weightPct'] != 0]

    breaches = [{'category': group['category'], 'name': item.get('name'),
                 'productId': item.get('productId'), 'notional': item['notional'],
                 'minimumInvestment': float(item['minimumInvestment'])}
                for group in groups for item in group['items']
                if item.get('belowMinimum')]

    return {'groups': groups, 'total': total, 'complete': complete,
            'priced': priced, 'tier': tier, 'breaches': breaches}


def roundSharesOneDp(exact) -> list:
    """Round shares to 1dp so they sum to exactly 100.0. Mirrors the page's
    roundSharesOneDp(); the two must agree or the export contradicts the UI."""
    units = [int(share * 10 + 1e-9) for share in exact]
    short = 1000 - sum(units)
    order = sorted(range(len(exact)),
                   key=lambda i: (-(exact[i] * 10 - units[i]), i))
    for n in range(max(short, 0)):
        if not order:
            break
        units[order[n % len(order)]] += 1
    return [unit / 10.0 for unit in units]


def donutBreakdown(items, key) -> list:
    """One doughnut's slices: share of what is attached, biggest first, each
    keeping the colour its name earns alphabetically. Mirrors the page's
    breakdown() exactly - see DONUT_PALETTE."""
    byValue, total = {}, 0.0
    for item in items:
        value = item.get(key)
        if value is None or value == '':
            value = '\u2014'
        byValue[value] = byValue.get(value, 0.0) + float(item.get('printedPct') or 0.0)
        total += float(item.get('printedPct') or 0.0)
    names = sorted(byValue)
    slot = {name: index % len(DONUT_PALETTE) for index, name in enumerate(names)}
    slices = [{'name': name, 'weight': byValue[name], 'slot': slot[name],
               'share': (byValue[name] / total) if total > 0 else 0.0}
              for name in names]
    slices.sort(key=lambda entry: (-entry['weight'], entry['name']))
    # printed shares close on 100.0 exactly, the way the page prints them
    printed = roundSharesOneDp([entry['share'] * 100 for entry in slices])
    for entry, pct in zip(slices, printed):
        entry['pct'] = pct
    return slices


def writeDonutCharts(book, sheet, model, firstRow: int) -> None:
    """The five composition doughnuts, under the implementation table.

    Native charts rather than pictures, so the figures stay live and the file
    stays small. Excel charts must read from cells, and those cells have no
    business on a sheet a client reads, so they go on a hidden sheet.
    """
    items = [item for group in model.get('groups', []) for item in group['items']]
    if not items:
        return
    data = book.create_sheet(_CHART_SHEET)
    data.sheet_state = 'hidden'

    sheet.cell(row=firstRow, column=1).value = 'Composition of the Implemented Model'
    sheet.cell(row=firstRow, column=1).font = Font(name='Calibri', size=12, bold=True,
                                                   color=_NAVY)
    sheet.cell(row=firstRow + 1, column=1).value = (
        'Share of allocation by product attribute.')
    sheet.cell(row=firstRow + 1, column=1).font = Font(name='Calibri', size=10,
                                                       color='5B6B7C')

    column = 1
    for index, (key, label) in enumerate(DONUT_DIMENSIONS):
        slices = donutBreakdown(items, key)
        if not slices:
            continue
        data.cell(row=1, column=column).value = label
        data.cell(row=1, column=column + 1).value = 'Share'
        for offset, entry in enumerate(slices, start=1):
            data.cell(row=1 + offset, column=column).value = entry['name']
            share = data.cell(row=1 + offset, column=column + 1)
            share.value = entry['pct'] / 100.0
            share.number_format = '0.0%'

        chart = DoughnutChart(holeSize=55)
        chart.title = label
        chart.height, chart.width = 7.4, 7.4
        chart.add_data(Reference(data, min_col=column + 1, min_row=1,
                                 max_row=1 + len(slices)), titles_from_data=True)
        chart.set_categories(Reference(data, min_col=column, min_row=2,
                                       max_row=1 + len(slices)))
        # one point per slice, in the palette the page uses
        series = chart.series[0]
        for offset, entry in enumerate(slices):
            point = DataPoint(idx=offset)
            point.graphicalProperties.solidFill = DONUT_PALETTE[entry['slot']]
            point.graphicalProperties.line.solidFill = 'FFFFFF'
            series.data_points.append(point)
        chart.dataLabels = None
        # laid out across the sheet, in the order the page shows them
        sheet.add_chart(chart, '{}{}'.format(
            get_column_letter(1 + index * 4), firstRow + 3))
        column += 2


def writeImplementationSheet(book, baseResult: dict, sleevesMap: dict,
                             autoCategories, mandateSize: float,
                             variant: str = None,
                             tacticalTilt: bool = False,
                             feeSchedule: str = None, feeLevel: str = None,
                             topAccountSize: float = None,
                             includeFees: bool = True,
                             volPremium: bool = False,
                             currency: str = None,
                             model: dict = None) -> None:
    """Append the implementation sheet: the columns of ``implColumns``, in
    order, grouped by category with subtotals and a grand total.

    The variant is written above the header, because the same category and
    sleeve name can carry different products under a different variant and a
    workbook that does not say which one it was built from cannot be checked
    against anything (D29). The fee schedule, fee level and account-size tier
    follow it for the same reason: every management fee on the sheet was
    resolved from those three, and a reader re-pricing a row needs them (D51).

    With *includeFees* false the proposal does not show fees at all (D52): the
    three fee columns and the three fee header rows are absent, and the model
    is built unpriced whatever schedule the scenario happens to remember, so
    there is no path by which a fee reaches a sheet that does not name it.
    """
    if not includeFees:
        feeSchedule = None
    columns = implColumns(includeFees)
    at = {name: index for index, name in enumerate(columns, start=1)}
    # A caller that has already built the model passes it in, so that what
    # the sheet prints and what the register records are the SAME model
    # rather than two builds a few microseconds apart (D69).
    if model is None:
        model = buildImplementationRows(baseResult, sleevesMap, autoCategories,
                                        mandateSize, variant, tacticalTilt,
                                        feeSchedule, feeLevel, topAccountSize,
                                        volPremium, currency)
    sheet = book.create_sheet('Implementation')
    headFont = Font(name='Aptos Narrow', size=12, bold=True, color='FFFFFF')
    bodyFont = Font(name='Aptos Narrow', size=12)
    boldFont = Font(name='Aptos Narrow', size=12, bold=True)
    headFill = PatternFill('solid', fgColor=_HEADER_NAVY)
    bandFill = PatternFill('solid', fgColor=_BAND)

    # openpyxl reports max_row == 1 for an empty sheet, so the header row is
    # counted rather than measured.
    headerRow = 1
    preamble = []
    if variant:
        preamble.append(['Implementation Type', variant])
    if model['priced']:
        preamble.append(['Fee Schedule', feeSchedule])
        preamble.append(['Fee Level', feeLevel or fees.DEFAULT_LEVEL])
        preamble.append(['Account Size Tier',
                         '{} ({})'.format(model['tier']['id'], model['tier']['label'])])
        # which card priced it: without the version, a re-delivery would leave
        # the sheet claiming rates it no longer matches (D55)
        card = fees.deliveryInfo()
        preamble.append(['Fee Card', '{}{}'.format(
            card.get('version') or 'unversioned',
            ' · placeholder' if card.get('placeholder') else '')])
    if preamble:
        for line in preamble:
            sheet.append(line)
            sheet.cell(row=sheet.max_row, column=1).font = boldFont
        sheet.append([])
        headerRow = len(preamble) + 2

    sheet.append(columns)
    for cell in sheet[headerRow]:
        cell.font = headFont
        cell.fill = headFill
    sheet.row_dimensions[headerRow].height = 20
    # A string, not sheet.cell(...).coordinate: addressing a cell materialises
    # it and pushes max_row past the header, so the first category row would
    # append one row late and leave a blank behind it.
    sheet.freeze_panes = 'A' + str(headerRow + 1)

    def _weightCell(cell, pct):
        cell.value = pct / 100.0
        cell.number_format = '0.00%'

    def _feeCell(cell, pct):
        if pct is None:                     # unpriced: the cell stays empty
            return
        cell.value = pct / 100.0
        cell.number_format = '0.00%'

    def _bpCell(cell, bp):
        if bp is None:
            return
        cell.value = bp
        cell.number_format = '0.0'

    def _bpAt(row, value):
        """The weighted-fee cell, when the sheet has one."""
        if includeFees:
            _bpCell(sheet.cell(row=row, column=at['Wtd fee (bp)']), value)

    for group in model['groups']:
        sheet.append([group['category'], group['sleeve'] or 'No sleeve attached'])
        row = sheet.max_row
        for column in range(1, len(columns) + 1):
            cell = sheet.cell(row=row, column=column)
            cell.fill = bandFill
            cell.font = boldFont
        _weightCell(sheet.cell(row=row, column=3),
                    sum(i['printedPct'] for i in group['items'])
                    if group['items'] else group['weightPct'])
        if group['items']:
            _bpAt(row, sum(i['wtdFeeBp'] for i in group['items'])
                  if model['priced'] else None)
            notional = sheet.cell(row=row, column=at['Notional'])
            notional.value = sum(i['notional'] for i in group['items'])
            notional.number_format = '$#,##0'
        for item in group['items']:
            line = [
                '  ' + item['assetClass'], item['name'], None, item['ticker'],
                item['style'], item['vehicle'], item['source'], item['liquidity'],
                item['exposureCurrency'], None,
            ]
            if includeFees:
                line += [None, None]
            sheet.append(line + [None, None])
            row = sheet.max_row
            for column in range(1, len(columns) + 1):
                sheet.cell(row=row, column=column).font = bodyFont
            _weightCell(sheet.cell(row=row, column=3), item['printedPct'])
            _feeCell(sheet.cell(row=row, column=at['Product Cost']),
                     float(item['productCost']))
            if includeFees:
                _feeCell(sheet.cell(row=row, column=at['Mgmt fee']), item['managementFee'])
            _bpAt(row, item['wtdFeeBp'])
            minimum = sheet.cell(row=row, column=at['Minimum Investment'])
            if item.get('minimumInvestment') is not None:
                minimum.value = float(item['minimumInvestment'])
                minimum.number_format = '$#,##0'
            notional = sheet.cell(row=row, column=at['Notional'])
            notional.value = item['notional']
            notional.number_format = '$#,##0'
            if item.get('belowMinimum'):
                # the sheet says so too: a workbook read away from the page
                # must not look clean when the page refused to export it
                for column in (at['Minimum Investment'], at['Notional']):
                    breached = sheet.cell(row=row, column=column)
                    breached.font = Font(name='Calibri', size=11, bold=True,
                                         color=_BREACH_INK)
                    breached.fill = PatternFill('solid', fgColor=_BREACH_FILL)

    sheet.append(['Total'])
    row = sheet.max_row
    for column in range(1, len(columns) + 1):
        cell = sheet.cell(row=row, column=column)
        cell.font = boldFont
        # ruled above and below: the total closes the table, and a single
        # line above it reads as just another separator between groups
        cell.border = Border(top=_DOTTED, bottom=_DOTTED)
    _weightCell(sheet.cell(row=row, column=3), model['total']['weightPct'])
    _bpAt(row, model['total']['wtdFeeBp'])
    notional = sheet.cell(row=row, column=at['Notional'])
    notional.value = model['total']['notional']
    notional.number_format = '$#,##0'

    totalRow = row
    for index, name in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = _WIDTHS[name]
    for row_cells in sheet.iter_rows(min_row=headerRow + 1):
        for cell in row_cells[2:]:
            cell.alignment = Alignment(horizontal='right')
        for cell in row_cells[3:9]:
            cell.alignment = Alignment(horizontal='left')

    # the composition doughnuts, under the table the page draws them under
    writeDonutCharts(book, sheet, model, totalRow + 3)



# --------------------------------------------------------------------- #
# The three sheets the analytics library used to lay out (D67).
#
# Each is written from the resolved payloads alone. The layout is not
# invented: it was read cell by cell off a workbook the engine produced, and
# the test suite compares what these functions emit against captured golden
# files so a change here cannot drift away from the house report unnoticed.
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
    (D68). It aligns nothing - there is nothing to align it with - and on the
    strategic sheets it is actively wrong: Asset Allocation Strategies exists
    in the asset universe only so that the tactical tilt has somewhere to
    live, and the tilt is an IMPLEMENTATION choice. No strategic portfolio
    carries it, the supplied extract names it nowhere, and a row reading
    "Asset Allocation Strategies 0.0%" says the strategic model has a nil
    allocation to something that was never part of it.

    *engineParity* keeps the empty rows, which is how the analytics library
    laid the sheet out: it padded every portfolio to the whole universe before
    reporting on it, so its workbook shows the tilt fund at zero. The golden
    test uses it to prove this writer can still reproduce that output exactly.
    Nothing else should.
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

    # Keep a row only where SOME column carries weight. A category one book
    # holds and another does not keeps its row and the other book prints the
    # zero - that is the alignment the zero-fill exists for. A row that is
    # zero the whole way across aligns nothing, and says the model has a nil
    # allocation to something that is not in it (D68).
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


def writePortfoliosSheet(book, results, engineParity: bool = False):
    """The strategic allocation, one column per portfolio.

    Note the scale asymmetry, which is the report's own: a CATEGORY row is a
    fraction formatted ``0.0%``, an ASSET row is already x100 and formatted
    ``0.0``. They print identically and store differently."""
    sheet = book.create_sheet('portfolios')
    light = Font(name='Calibri Light', size=12.5)
    spacerFont = Font(name='Arial', size=12.5)
    metricFont = Font(name='Calibri', size=12.5, color=_WHITE)
    band = PatternFill('solid', fgColor=_BAND)
    navy = PatternFill('solid', fgColor=_NAVY)
    right = Alignment(horizontal='right', vertical='center', indent=4)
    columns = len(results)

    def style(row, font, fill=_WHITE_FILL, fmt=None, border=None, height=None,
              labelAlign='left', vertical='center', labelFmt=False):
        if height is not None:
            sheet.row_dimensions[row].height = height
        for index in range(1, columns + 2):
            cell = sheet.cell(row=row, column=index)
            cell.font = font
            cell.fill = fill
            if border is not None:
                cell.border = border
            if index == 1:
                cell.alignment = Alignment(horizontal=labelAlign, vertical=vertical)
                if labelFmt and fmt:
                    cell.number_format = fmt
            else:
                cell.alignment = (right if vertical
                                  else Alignment(horizontal='right', indent=4))
                if fmt:
                    cell.number_format = fmt

    sheet.append([None] + [r['name'] for r in results])
    style(1, light, border=Border(bottom=_DOTTED), height=37, labelAlign=None)
    for index in range(2, columns + 2):
        sheet.cell(row=1, column=index).alignment = Alignment(
            horizontal='center', vertical='center')

    order, assets = _categoryOrder(results, engineParity)
    for categoryName in order:
        sheet.append([categoryName] + [
            None if (w := _weight(r, categoryName)) is None else w / 100.0
            for r in results])
        style(sheet.max_row, light, band, '0.0%', height=17)
        for assetName in assets[categoryName]:
            sheet.append(['  ' + assetName] + [
                None if (w := _weight(r, categoryName, assetName)) is None else w
                for r in results])
            style(sheet.max_row, light, fmt='0.0', height=17)

    sheet.append(['TOTAL'] + [1.0] * columns)
    style(sheet.max_row, light, fmt='0.0%', border=Border(top=_DOTTED),
          height=23, labelAlign=None, vertical=None)

    for label, field, fmt in (('Estimated Mean Return', 'estimatedReturnPct', '0.0%'),
                              ('Sharpe Ratio', 'sharpe', '0.00'),
                              ('Volatility', 'volatilityPct', '0.0%')):
        if label != 'Sharpe Ratio':
            # a hairline spacer above each block, exactly as the report has it
            sheet.append([None] + [0] * columns)
            style(sheet.max_row, spacerFont, height=3, labelAlign=None, vertical=None)
            for index in range(2, columns + 2):
                sheet.cell(row=sheet.max_row, column=index).alignment = Alignment()
                sheet.cell(row=sheet.max_row, column=index).number_format = 'General'
        scale = 100.0 if fmt == '0.0%' else 1.0
        sheet.append([label] + [r['metrics'][field] / scale for r in results])
        style(sheet.max_row, metricFont, navy, fmt, height=23, labelAlign=None)
        sheet.cell(row=sheet.max_row, column=1).number_format = '0.0%'

    sheet.column_dimensions['A'].width = 40
    for index in range(2, columns + 2):
        sheet.column_dimensions[get_column_letter(index)].width = 18
    sheet.freeze_panes = 'B2'                                  # enhancement
    return sheet


def writeRiskDashboardSheet(book, results, engineParity: bool = False):
    """Risk, one PAIR of columns per portfolio - nominal and real.

    The pair is merged on the summary rows and split from the stress block
    down. VaR and CVaR are printed POSITIVE: the payload stores them negated
    because the screen reads them as losses, and the sheet does not."""
    sheet = book.create_sheet('risk_dashboard')
    narrow = Font(name='Aptos Narrow', size=12)
    onNavy = Font(name='Aptos Narrow', size=12, color=_WHITE)
    section = Font(name='Aptos Narrow', size=12, bold=True, color=_SUBHEAD_NAVY)
    navy = PatternFill('solid', fgColor=_HEADER_NAVY)
    centre = Alignment(horizontal='center')
    columns = len(results)
    span = columns * 2

    def paint(row, font, fill=_WHITE_FILL, fmt=None, height=16, labelAlign=None,
              border=None, vertical=None, indent=0):
        sheet.row_dimensions[row].height = height
        for index in range(1, span + 2):
            cell = sheet.cell(row=row, column=index)
            cell.font = font
            cell.fill = fill
            if border is not None:
                cell.border = border
            if index == 1:
                cell.alignment = Alignment(horizontal=labelAlign, vertical=vertical,
                                           indent=indent)
            else:
                cell.alignment = (Alignment(horizontal='center', vertical=vertical)
                                  if vertical else centre)
                if fmt:
                    cell.number_format = fmt

    def merge(row):
        for column in range(columns):
            first = 2 + column * 2
            sheet.merge_cells(start_row=row, start_column=first,
                              end_row=row, end_column=first + 1)

    sheet.append([None] + sum([[r['name'], None] for r in results], []))
    paint(1, onNavy, navy, height=35, vertical='center')
    merge(1)

    order, _ = _categoryOrder(results, engineParity)
    for categoryName in order:
        values = []
        for result in results:
            weight = _weight(result, categoryName)
            values.extend([None if weight is None else weight / 100.0, None])
        sheet.append([categoryName] + values)
        paint(sheet.max_row, narrow, fmt='0.0%',
              height=20 if sheet.max_row == 2 else 16)
        merge(sheet.max_row)

    # The library left four unlabelled rows in here - two before the metrics
    # and two before the volatility. They are not spacers: they carry a 0 with
    # a percent format, so they PRINT as "0.0%" against a blank label. They go
    # (D68); the dotted rule above Estimated Mean Return already separates the
    # metrics from the categories. engineParity restores them for the golden
    # comparison and nothing else.
    metrics = [('Estimated Mean Return', 'estimatedReturnPct', '0.0%'),
               ('Sharpe Ratio', 'sharpe', '0.00'),
               ('Volatility', 'volatilityPct', '0.0%')]
    if engineParity:
        metrics = ([(None, None, None)] * 2 + metrics[:2]
                   + [(None, None, None)] * 2 + metrics[2:])
    for label, field, fmt in metrics:
        if label is None:
            sheet.append([None] + [0, None] * columns)
            paint(sheet.max_row, narrow, fmt='0.0%')
        else:
            scale = 100.0 if fmt == '0.0%' else 1.0
            values = []
            for result in results:
                values.extend([result['metrics'][field] / scale, None])
            sheet.append([label] + values)
            paint(sheet.max_row, narrow, fmt=fmt,
                  border=Border(top=_DOTTED) if field == 'estimatedReturnPct' else None)
        merge(sheet.max_row)

    def band(title):
        sheet.append([title])
        paint(sheet.max_row, onNavy, navy, vertical='center')

    def subhead(title, heads=False):
        sheet.append([title] + (['Nominal', 'Real'] * columns if heads else []))
        paint(sheet.max_row, section, border=Border(top=_DOTTED))
        for index in range(2, span + 2):
            cell = sheet.cell(row=sheet.max_row, column=index)
            cell.font = narrow

    def rows(entries, read, indent='  '):
        for entry in entries:
            values = []
            for result in results:
                nominal, real = read(result, entry)
                values.extend([nominal, real])
            sheet.append([indent + entry] + values)
            # the report gives the first stress row a taller band and the rest
            # a plain one; nothing else on the sheet varies
            paint(sheet.max_row, narrow, fmt='0.0%', labelAlign='left', indent=1,
                  height=20 if sheet.max_row == 18 else 16)

    band('Factor Based Risk Analytics')
    subhead('Predicted Performance Over Stress Periods', heads=True)
    stressStart = sheet.max_row + 1
    periods = [s['period'] for s in (results[0]['stress'] if results else [])]

    def readStress(result, period):
        for entry in result['stress']:
            if entry['period'] == period:
                return entry['nominalPct'] / 100.0, entry['realPct'] / 100.0
        return None, None
    rows(periods, readStress)
    stressEnd = sheet.max_row

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
    band('Portfolio Risk Premia')

    # red below zero, green above - the report's own rule over the stress block
    sheet.conditional_formatting.add(
        '{}:{}'.format('B%d' % stressStart, '%s%d' % (get_column_letter(span + 1), stressEnd)),
        CellIsRule(operator='lessThan', formula=['0'],
                   font=Font(color='9C0006')))
    sheet.conditional_formatting.add(
        '{}:{}'.format('B%d' % stressStart, '%s%d' % (get_column_letter(span + 1), stressEnd)),
        CellIsRule(operator='greaterThan', formula=['0'],
                   font=Font(color='006100')))

    sheet.column_dimensions['A'].width = 40
    for index in range(2, span + 2):
        sheet.column_dimensions[get_column_letter(index)].width = 15
    sheet.freeze_panes = 'B2'                                  # enhancement
    return sheet


def _asDate(value):
    """An ISO date string as a datetime, so ``mmm-yy`` has something to format."""
    if isinstance(value, datetime.datetime):
        return value
    try:
        return datetime.datetime.strptime(str(value)[:10], '%Y-%m-%d')
    except (TypeError, ValueError):
        return None


#: the assumptions sheet's columns: (header, key, number format, alignment,
#: font colour). The two range ends are coloured because the sheet is read as
#: "low - mid - high" across, and the colour is what makes that legible.
_ASSUMPTION_COLUMNS = [
    ('Risk Premia\n with Estimated Range', 'lower', '0.0%', 'right', _PREMIA_LOW),
    ('Mean', 'mean', '0.0%', 'center', None),
    ('Upper Range', 'upper', '0.0%', 'left', _PREMIA_HIGH),
    ('Volatility', 'volatility', '0.0%', 'center', None),
    ('Sharpe Ratio', 'sharpe', '0.00', 'center', None),
    ('Estimated Mean Return\n(2.5% Risk Free Rate)', 'totalReturn', '0.0%', 'center', None),
    ('Hedging Ratio', 'hedgingRatio', '0%', 'center', None),
    ('From', 'from', 'mmm-yy', 'center', None),
    ('To', 'to', 'mmm-yy', 'center', None),
]


def writeAssumptionsSheet(book, assets, results=None, engineParity: bool = False):
    """The long-term estimates behind the analytics, one row per asset (D67).

    The engine used to emit this and the export used to throw it away. It is
    kept now, as the fourth sheet, and it is the one sheet whose numbers are
    a property of the ASSET UNIVERSE rather than of any portfolio - which is
    why the bake records it once per (currency, hedging) slice rather than on
    every payload.

    *assets* is that recorded block: a list of dicts in the universe's own
    order. Given none, the sheet still appears and says why it is empty,
    because a proposal with three sheets where there should be four is a
    harder thing to notice than a sheet that explains itself.

    It is filtered to the rows the strategic sheets show (D68). The estimates
    exist for the whole universe, but this sheet is here to explain THIS
    proposal: an assumption printed against an asset no portfolio in the
    lineup holds explains nothing, and the tactical tilt fund - which no
    strategic portfolio holds at all - has no business on it.
    """
    sheet = book.create_sheet('assumptions')
    body = Font(name='Grotesque', size=11)
    plain = Font(name='Calibri', size=11)
    heading = Font(name='Grotesque', size=11, bold=True, color=_NAVY)
    thinBottom = Border(bottom=_THIN)
    centred = Alignment(horizontal='center', vertical='center')

    # ---- the two header rows ------------------------------------------
    sheet.cell(row=1, column=1).value = None
    sheet.cell(row=1, column=2).value = 'Long-Term Estimates'
    sheet.cell(row=1, column=9).value = 'Modelling Dates'
    sheet.merge_cells(start_row=1, start_column=2, end_row=1, end_column=7)
    sheet.merge_cells(start_row=1, start_column=9, end_row=1, end_column=10)
    for index in range(1, 11):
        cell = sheet.cell(row=1, column=index)
        letter = get_column_letter(index)
        cell.font = plain if letter in ('C', 'D', 'E', 'F', 'G', 'J') else body
        if letter not in ('C', 'D', 'E', 'F', 'G', 'J'):
            cell.fill = _WHITE_FILL
        cell.alignment = centred if index > 1 else Alignment(vertical='center')
        if letter not in ('A', 'H'):
            cell.border = thinBottom
    sheet.row_dimensions[1].height = 20

    sheet.cell(row=2, column=1).value = None
    sheet.merge_cells(start_row=2, start_column=2, end_row=2, end_column=4)
    for offset, (header, _, _, _, _) in enumerate(_ASSUMPTION_COLUMNS):
        if offset in (1, 2):
            continue                       # inside the merged range-of-three
        column = 2 + offset
        sheet.cell(row=2, column=column).value = header
    sheet.cell(row=2, column=2).value = _ASSUMPTION_COLUMNS[0][0]
    for index in range(1, 11):
        cell = sheet.cell(row=2, column=index)
        cell.font = body
        cell.fill = _WHITE_FILL
        cell.border = thinBottom
        wrap = index in (2, 7)
        cell.alignment = (Alignment(horizontal='center', vertical='center', wrap_text=True)
                          if wrap else (centred if index > 1 else Alignment(vertical='center')))
    sheet.row_dimensions[2].height = 49

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
        sheet.cell(row=row, column=1).value = (
            'The asset estimates for this basis are not in the baked store. '
            'Re-run the bake to record them.')
        sheet.cell(row=row, column=1).font = heading
        sheet.row_dimensions[row].height = 17
    else:
        firstCategory = True
        seen = set()
        for entry in assets:
            category = entry.get('category') or ''
            if category and category not in seen:
                seen.add(category)
                sheet.cell(row=row, column=1).value = category
                for index in range(1, 11):
                    cell = sheet.cell(row=row, column=index)
                    cell.fill = _WHITE_FILL
                    if not firstCategory:
                        cell.border = Border(top=_HAIR)
                    if index == 1:
                        cell.font = heading
                        continue
                    _, _, fmt, align, colour = _ASSUMPTION_COLUMNS[index - 2]
                    # a category header is bold, and keeps the column's own
                    # colour where it has one: the range ends stay red and
                    # green all the way down the sheet
                    # bold navy, except the upper end of the range which keeps
                    # its green from the second header down
                    keepsGreen = colour == _PREMIA_HIGH and not firstCategory
                    cell.font = Font(name='Grotesque', size=11, bold=True,
                                     color=_PREMIA_HIGH if keepsGreen else _NAVY)
                    if firstCategory:
                        # the first header row sits directly under the column
                        # heads and takes their plain treatment - bar the
                        # Sharpe column, which keeps its format throughout
                        cell.number_format = fmt if fmt == '0.00' else 'General'
                        cell.alignment = Alignment(horizontal='center')
                    else:
                        cell.number_format = fmt
                        cell.alignment = Alignment(horizontal=align)
                sheet.row_dimensions[row].height = 17
                firstCategory = False
                row += 1
            sheet.cell(row=row, column=1).value = '    ' + str(entry.get('reportingName', ''))
            sheet.cell(row=row, column=1).font = body
            sheet.cell(row=row, column=1).fill = _WHITE_FILL
            for offset, (_, key, fmt, align, colour) in enumerate(_ASSUMPTION_COLUMNS):
                cell = sheet.cell(row=row, column=2 + offset)
                value = entry.get(key)
                cell.value = _asDate(value) if fmt == 'mmm-yy' else value
                cell.font = Font(name='Grotesque', size=11, color=colour) if colour else body
                cell.fill = _WHITE_FILL
                cell.number_format = fmt
                cell.alignment = Alignment(horizontal=align)
            sheet.row_dimensions[row].height = 17
            row += 1

    if row > 3:
        # a closing rule under the table, as the report has it
        for index in range(1, 11):
            sheet.cell(row=row - 1, column=index).border = Border(bottom=_THIN)
    for letter, width in _ASSUMPTION_WIDTHS:
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = 'A3'                                  # enhancement
    return sheet


def writeWorkbook(basis, mandate, results, sleevesMap, autoCategories,
                  variant: str = None, tacticalTilt: bool = False,
                  feeSchedule: str = None, feeLevel: str = None,
                  includeFees: bool = True, volPremium: bool = False,
                  assets=None, engineParity: bool = False, model: dict = None) -> bytes:
    """The proposal workbook: four sheets, no analytics library (D67).

    ``portfolios``, ``risk_dashboard`` and ``assumptions`` reproduce what the
    engine's reporting object used to lay out; ``Implementation`` is the
    tool's own and is unchanged. *assets* is the per-asset estimate block for
    this basis, which the adapter supplies.

    *engineParity* restores the empty universe rows the library used to print
    (see ``_categoryOrder``). It exists for the golden test and should not be
    set by a caller producing a proposal.
    """
    book = Workbook()
    book.remove(book.active)                      # the writers name their own

    writePortfoliosSheet(book, results, engineParity)
    writeRiskDashboardSheet(book, results, engineParity)
    writeAssumptionsSheet(book, assets, results, engineParity)
    writeImplementationSheet(book, results[0], sleevesMap, autoCategories,
                             mandate.mandateSize, variant, tacticalTilt,
                             feeSchedule, feeLevel, mandate.topAccountSize,
                             includeFees, volPremium, basis.currency,
                             model=model)

    # Enhancements that add nothing to the grid and cost nothing to read: a
    # coloured tab per sheet, a sensible print setup, and the proposal's own
    # identity in the file's properties.
    for name in book.sheetnames:
        sheet = book[name]
        sheet.sheet_properties.tabColor = _NAVY
        sheet.page_setup.orientation = 'landscape'
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.print_title_rows = '1:1'
    book.properties.title = 'PMG Proposal - {} {}'.format(basis.currency, basis.hedging)
    book.properties.creator = 'PMG Proposal Tool'
    book.properties.description = (
        'Strategic allocation, risk dashboard, long-term estimates and the '
        'implemented model.')

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


#: The name this had while it was only the fixtures adapter's writer. It is
#: every adapter's writer now; the old name stays so nothing has to change at
#: once.
writeFixturesWorkbook = writeWorkbook
