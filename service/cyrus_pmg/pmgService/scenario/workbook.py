"""Excel writers.

Two jobs live here:

* ``buildImplementationRows`` + ``writeImplementationSheet`` - the
  implementation model as a sheet (spec 14.4), with the rounding rule of spec
  8.4 applied exactly as on screen: every printed figure derives from the
  printed weight, so the workbook hand-reconciles against the page.
  ``buildImplementationRows`` is the single Python source of those numbers -
  the real adapter and the fixtures adapter both use it, and the test suite
  checks it against the page's JavaScript mirror.

* ``writeFixturesWorkbook`` - a complete workbook for the fixtures adapter,
  which has no ``Reporting`` behind it. Sheet layout and formats follow the
  house report (spec 14.3): asset rows carry weights x100 with number format
  ``0.0`` while category and total rows carry fractions with ``0.0%`` - that
  asymmetry is the report's own convention and is preserved deliberately.
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .payloads import roundWeightsLargestRemainder
from .sleeves import listSleeves

# House palette from the existing report (spec 14.3). The UI navy differs by
# design - open item 2 keeps each surface on its own until the theme decision.
_NAVY = '0F243E'
_HEADER_NAVY = '092532'
_BAND = 'D3DDEA'
_DOTTED = Side(style='dotted', color='A9A9A9')

IMPL_COLUMNS = [
    'Categories & Asset Classes', 'Products', 'Allocation (%)', 'Ticker',
    'Style', 'Vehicle', 'Source', 'Liquidity', 'Exposure ccy', 'Cost',
    'Mgmt fee', 'Wtd fee (bp)', 'Notional',
]


def buildImplementationRows(baseResult: dict, sleevesMap: dict,
                            autoCategories, mandateSize: float,
                            variant: str = None) -> dict:
    """The implementation model's numbers, derived per spec 8.4.

    Returns {'groups': [...], 'total': {...}, 'complete': bool}. Each group is
    a category: its sleeve name (auto categories take their single library
    sleeve), product line items with printed weight, weighted fee in bp and
    notional, and group subtotals. Weights are rounded to 2dp by largest
    remainder across the WHOLE table so the column sums to exactly 100.00;
    notional and weighted fee derive from that printed weight.
    """
    autoCategories = set(autoCategories or [])
    groups = []
    lineItems = []

    for category in baseResult['categories']:
        name = category['name']
        catWeight = float(category['weightPct'])
        if name in autoCategories:
            library = listSleeves(name, variant)
            sleeve = library[0] if library else None
            auto = True
        else:
            chosen = (sleevesMap or {}).get(name)
            sleeve = None
            if chosen:
                sleeve = next((s for s in listSleeves(name, variant)
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
            allIn = float(item['productCost']) + float(item['managementFee'])
            item['printedPct'] = weight
            item['wtdFeeBp'] = allIn * weight            # percent x percent = bp
            item['notional'] = round(mandateSize * weight / 100.0 / 100.0) * 100.0

    total = {
        'weightPct': sum(i.get('printedPct', 0.0) for i in lineItems),
        'wtdFeeBp': sum(i.get('wtdFeeBp', 0.0) for i in lineItems),
        'notional': sum(i.get('notional', 0.0) for i in lineItems),
    }
    return {'groups': groups, 'total': total, 'complete': complete}


def writeImplementationSheet(book, baseResult: dict, sleevesMap: dict,
                             autoCategories, mandateSize: float,
                             variant: str = None) -> None:
    """Append the implementation sheet: the thirteen columns of spec 9.3, in
    order, grouped by category with subtotals and a grand total.

    The variant is written above the header, because the same category and
    sleeve name can carry different products under a different variant and a
    workbook that does not say which one it was built from cannot be checked
    against anything (D29).
    """
    model = buildImplementationRows(baseResult, sleevesMap, autoCategories,
                                    mandateSize, variant)
    sheet = book.create_sheet('Implementation')
    headFont = Font(name='Aptos Narrow', size=12, bold=True, color='FFFFFF')
    bodyFont = Font(name='Aptos Narrow', size=12)
    boldFont = Font(name='Aptos Narrow', size=12, bold=True)
    headFill = PatternFill('solid', fgColor=_HEADER_NAVY)
    bandFill = PatternFill('solid', fgColor=_BAND)

    # openpyxl reports max_row == 1 for an empty sheet, so the header row is
    # counted rather than measured.
    headerRow = 1
    if variant:
        sheet.append(['Implementation variant', variant])
        sheet.cell(row=1, column=1).font = boldFont
        sheet.append([])
        headerRow = 3

    sheet.append(IMPL_COLUMNS)
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
        cell.value = pct / 100.0
        cell.number_format = '0.00%'

    for group in model['groups']:
        sheet.append([group['category'], group['sleeve'] or 'No sleeve attached'])
        row = sheet.max_row
        for column in range(1, len(IMPL_COLUMNS) + 1):
            cell = sheet.cell(row=row, column=column)
            cell.fill = bandFill
            cell.font = boldFont
        _weightCell(sheet.cell(row=row, column=3),
                    sum(i['printedPct'] for i in group['items'])
                    if group['items'] else group['weightPct'])
        if group['items']:
            bp = sheet.cell(row=row, column=12)
            bp.value = sum(i['wtdFeeBp'] for i in group['items'])
            bp.number_format = '0.0'
            notional = sheet.cell(row=row, column=13)
            notional.value = sum(i['notional'] for i in group['items'])
            notional.number_format = '$#,##0'
        for item in group['items']:
            sheet.append([
                '  ' + item['assetClass'], item['name'], None, item['ticker'],
                item['style'], item['vehicle'], item['source'], item['liquidity'],
                item['exposureCurrency'], None, None, None, None,
            ])
            row = sheet.max_row
            for column in range(1, len(IMPL_COLUMNS) + 1):
                sheet.cell(row=row, column=column).font = bodyFont
            _weightCell(sheet.cell(row=row, column=3), item['printedPct'])
            _feeCell(sheet.cell(row=row, column=10), float(item['productCost']))
            _feeCell(sheet.cell(row=row, column=11), float(item['managementFee']))
            bp = sheet.cell(row=row, column=12)
            bp.value = item['wtdFeeBp']
            bp.number_format = '0.0'
            notional = sheet.cell(row=row, column=13)
            notional.value = item['notional']
            notional.number_format = '$#,##0'

    sheet.append(['Total'])
    row = sheet.max_row
    for column in range(1, len(IMPL_COLUMNS) + 1):
        cell = sheet.cell(row=row, column=column)
        cell.font = boldFont
        cell.border = Border(top=_DOTTED)
    _weightCell(sheet.cell(row=row, column=3), model['total']['weightPct'])
    bp = sheet.cell(row=row, column=12)
    bp.value = model['total']['wtdFeeBp']
    bp.number_format = '0.0'
    notional = sheet.cell(row=row, column=13)
    notional.value = model['total']['notional']
    notional.number_format = '$#,##0'

    widths = [34, 32, 12, 9, 9, 12, 10, 11, 12, 8, 10, 12, 14]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row_cells in sheet.iter_rows(min_row=2):
        for cell in row_cells[2:]:
            cell.alignment = Alignment(horizontal='right')
        for cell in row_cells[3:9]:
            cell.alignment = Alignment(horizontal='left')


def writeFixturesWorkbook(basis, mandate, results, sleevesMap, autoCategories,
                          variant: str = None) -> bytes:
    """A complete workbook from fixture payloads: Portfolios, Risk Dashboard
    and Implementation sheets, styled after the house report (spec 14.3)."""
    book = Workbook()

    # ---- Portfolios sheet -------------------------------------------------
    sheet = book.active
    sheet.title = 'Portfolios'
    light = Font(name='Calibri Light', size=12.5)
    lightBold = Font(name='Calibri Light', size=12.5, bold=True)
    white = Font(name='Calibri Light', size=12.5, color='FFFFFF')
    bandFill = PatternFill('solid', fgColor=_BAND)
    navyFill = PatternFill('solid', fgColor=_NAVY)

    names = [result['name'] for result in results]
    sheet.append([''] + names)
    for cell in sheet[1]:
        cell.font = lightBold
        cell.border = Border(bottom=_DOTTED)
    sheet.row_dimensions[1].height = 37
    sheet.freeze_panes = 'B2'

    categoryOrder = []
    assetOrder = {}
    for result in results:
        for category in result['categories']:
            if category['name'] not in categoryOrder:
                categoryOrder.append(category['name'])
                assetOrder[category['name']] = []
            for asset in category['assets']:
                if asset['reportingName'] not in assetOrder[category['name']]:
                    assetOrder[category['name']].append(asset['reportingName'])

    def _lookup(result, categoryName, assetName=None):
        for category in result['categories']:
            if category['name'] != categoryName:
                continue
            if assetName is None:
                return category['weightPct']
            for asset in category['assets']:
                if asset['reportingName'] == assetName:
                    return asset['weightPct']
            return 0.0        # category held, asset not: the report's zero-fill
        return None           # variant does not have the category

    for categoryName in categoryOrder:
        sheet.append([categoryName] +
                     [None if (w := _lookup(r, categoryName)) is None else w / 100.0
                      for r in results])
        row = sheet.max_row
        sheet.row_dimensions[row].height = 17
        for cell in sheet[row]:
            cell.font = lightBold
            cell.fill = bandFill
            if cell.column > 1:
                cell.number_format = '0.0%'
        for assetName in assetOrder[categoryName]:
            sheet.append(['  ' + assetName] +
                         [None if (w := _lookup(r, categoryName, assetName)) is None else w
                          for r in results])
            for cell in sheet[sheet.max_row]:
                cell.font = light
                if cell.column > 1:
                    cell.number_format = '0.0'
    sheet.append(['TOTAL'] + [1.0 for _ in results])
    row = sheet.max_row
    sheet.row_dimensions[row].height = 23
    for cell in sheet[row]:
        cell.font = lightBold
        cell.border = Border(top=_DOTTED)
        if cell.column > 1:
            cell.number_format = '0.0%'
    for label, field, fmt in (
            ('Estimated Mean Return', 'estimatedReturnPct', '0.0%'),
            ('Sharpe Ratio', 'sharpe', '0.00'),
            ('Volatility', 'volatilityPct', '0.0%')):
        sheet.append([label] +
                     [r['metrics'][field] / (100.0 if fmt == '0.0%' else 1.0)
                      for r in results])
        row = sheet.max_row
        sheet.row_dimensions[row].height = 23
        for cell in sheet[row]:
            cell.fill = navyFill
            cell.font = Font(name='Calibri Light', size=12.5, color='FFFFFF')
            if cell.column > 1:
                cell.number_format = fmt
    sheet.column_dimensions['A'].width = 40
    for index in range(2, len(results) + 2):
        sheet.column_dimensions[get_column_letter(index)].width = 18
        for rowCells in sheet.iter_rows(min_col=index, max_col=index):
            for cell in rowCells:
                cell.alignment = Alignment(horizontal='right', indent=4)

    # ---- Risk sheet -------------------------------------------------------
    risk = book.create_sheet('Risk Dashboard')
    narrow = Font(name='Aptos Narrow', size=12)
    narrowBold = Font(name='Aptos Narrow', size=12, bold=True, color=_HEADER_NAVY)
    headFill = PatternFill('solid', fgColor=_HEADER_NAVY)

    risk.append([''] + sum([[name, ''] for name in names], []))
    risk.append([''] + ['Nominal', 'Real'] * len(results))
    for rowIndex in (1, 2):
        risk.row_dimensions[rowIndex].height = 20
        for cell in risk[rowIndex]:
            cell.fill = headFill
            cell.font = Font(name='Aptos Narrow', size=12, color='FFFFFF')
            cell.alignment = Alignment(horizontal='center')
    for column in range(len(results)):
        first = 2 + column * 2
        risk.merge_cells(start_row=1, start_column=first, end_row=1, end_column=first + 1)
    risk.freeze_panes = 'B3'

    def _band(title):
        risk.append([title])
        for cell in risk[risk.max_row]:
            cell.font = Font(name='Aptos Narrow', size=12, bold=True, color=_HEADER_NAVY)
            cell.border = Border(top=_DOTTED)

    _band('Factor Based Risk Analytics')
    for categoryName in categoryOrder:
        values = []
        for result in results:
            weight = _lookup(result, categoryName)
            pair = [None, None] if weight is None else [weight / 100.0, None]
            values.extend(pair)
        risk.append([categoryName] + values)
        for cell in risk[risk.max_row]:
            cell.font = narrow
            if cell.column > 1:
                cell.number_format = '0.0%'
                cell.alignment = Alignment(horizontal='center')
    for label, field, scale in (('Estimated Mean Return', 'estimatedReturnPct', 100.0),
                                ('Sharpe Ratio', 'sharpe', 1.0),
                                ('Volatility', 'volatilityPct', 100.0)):
        values = []
        for result in results:
            values.extend([result['metrics'][field] / scale, None])
        risk.append([label] + values)
        for cell in risk[risk.max_row]:
            cell.font = narrowBold
            if cell.column > 1:
                cell.number_format = '0.0%' if scale == 100.0 else '0.00'
                cell.alignment = Alignment(horizontal='center')

    _band('Predicted Performance Over Stress Periods')
    stressPeriods = results[0]['stress'] if results else []
    for index in range(len(stressPeriods)):
        label = stressPeriods[index]['period']
        values = []
        for result in results:
            entry = result['stress'][index]
            values.extend([entry['nominalPct'] / 100.0, entry['realPct'] / 100.0])
        risk.append([label] + values)
        for cell in risk[risk.max_row]:
            cell.font = narrow
            if cell.column > 1:
                cell.number_format = '0.0%'
                cell.alignment = Alignment(horizontal='center')

    _band('Portfolio Risk Premia')
    premiaRows = results[0]['premia'] if results else []
    for index in range(len(premiaRows)):
        label = premiaRows[index]['label']
        values = []
        for result in results:
            entry = result['premia'][index]
            values.extend([entry['nominalPct'] / 100.0, entry['realPct'] / 100.0])
        risk.append([label] + values)
        for cell in risk[risk.max_row]:
            cell.font = narrow
            if cell.column > 1:
                cell.number_format = '0.0%'
                cell.alignment = Alignment(horizontal='center')

    risk.column_dimensions['A'].width = 40
    for index in range(2, 2 + len(results) * 2):
        risk.column_dimensions[get_column_letter(index)].width = 15

    # ---- Implementation sheet --------------------------------------------
    writeImplementationSheet(book, results[0], sleevesMap, autoCategories,
                             mandate.mandateSize, variant)

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
