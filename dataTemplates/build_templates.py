"""Build the data-delivery template workbook for the Cyrus side.

One sheet per file the host must supply, each a literal table to fill in, plus
a read me, a column reference, the closed vocabularies, and one sheet for the
bake - which is generated rather than typed.

Every column list, vocabulary and example row is READ OUT OF THE PACKAGE at
build time: `products.COLUMNS`, `sleeveRepo.SEED_COLUMNS`, the fee reader's own
fieldnames, `rules` and `fees` for the vocabularies, and the packaged stand-in
extracts for the examples. So the template cannot drift from the readers that
validate against it - when a reader gains or loses a column, rebuild and the
template follows.

    cd proposal-tool/service
    PYTHONPATH=. python3 ../dataTemplates/build_templates.py

It writes ProposalTool_DataTemplates.xlsx beside this script. The contract each
sheet expresses is written out in prose in PORTING.md Appendix C; the two are
built from the same facts and should be changed together.
"""
import csv
import io
import os

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from cyrus_pmg.pmgService.scenario import (fees, products, rules, sleeveRepo,
                                           portfolio_weights as pw)
from cyrus_pmg.pmgService.scenario import sleeves as sleeveFacade

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.abspath(products.__file__))
ROOT = os.path.abspath(os.path.join(PKG, '..', '..', '..', '..'))

NAVY = '092C61'
HEAD_FILL = PatternFill('solid', fgColor=NAVY)
EXAMPLE_FILL = PatternFill('solid', fgColor='FFF4D6')
NOTE_FILL = PatternFill('solid', fgColor='F2F5F9')
HEAD_FONT = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
EXAMPLE_FONT = Font(name='Calibri', size=11, italic=True, color='8A6D1B')
BODY = Font(name='Calibri', size=11)
BOLD = Font(name='Calibri', size=11, bold=True)
TITLE = Font(name='Calibri', size=15, bold=True, color=NAVY)
SUB = Font(name='Calibri', size=11, color='5B6B7C')
THIN = Side(style='thin', color='D4DAE2')
GRID = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(vertical='top', wrap_text=True)

CURRENCIES = list(rules.CURRENCIES)
HEDGING = list(rules.HEDGING_POLICIES)
SLICES = ['{}|{}'.format(c, h) for c in CURRENCIES for h in HEDGING]
TICKERS = [t for t, _, _ in pw.ASSET_METADATA]
ASSET_NAMES = [n for _, n, _ in pw.ASSET_METADATA]

# ---------------------------------------------------------------- the sheets --
# (column, required, type, rule) per template sheet. The rule text is what a
# reader is validated against; it is also what the header comment says.
SHEETS = [
    dict(
        name='saaPortfolios', tab='1F5FBF',
        target='saaPortfolios.csv', env='SCENARIO_SAA_SOURCE', fmt='CSV or XLSX',
        what='Every strategic portfolio the tool can offer, one row per holding. '
             'The portfolio NAME is the key: it is parsed, never guessed.',
        columns=[
            ('PortfolioName', 'yes', 'text',
             '"<Currency> <RiskLevel> <AllocationType>[ ex-RAs]", or "<Currency> All Equity". '
             'See the Valid values sheet. A name that does not parse is rejected and listed in '
             'the bake manifest.'),
            ('AssetTicker', 'yes', 'text',
             'One of the 19 tickers on the Valid values sheet. Anything else fails bake --census.'),
            ('Weight', 'yes', 'number',
             'A FRACTION, not a percent: 0.62 means 62%. One portfolio\'s rows should sum to 1.'),
        ],
        validation={'AssetTicker': ('tickers', len(TICKERS))},
    ),
    dict(
        name='products', tab='176A33',
        target='products.csv', env='SCENARIO_PRODUCTS_SOURCE', fmt='CSV',
        what='The delivered product catalogue every sleeve picks from. Re-read whenever the '
             'file changes on disk.',
        columns=[
            ('ProductId', 'yes', 'text',
             'The key. Unique and non-empty. Referenced by the sleeves sheet and by every sleeve '
             'saved in the console, so keep it stable across deliveries or sleeves lose their products.'),
            ('Name', 'yes', 'text', 'Non-empty. What the proposal prints.'),
            ('Ticker', 'no', 'text', 'May be blank; blank shows as an em dash on screen.'),
            ('AssetClass', 'yes', 'text', 'Free text. Printed as the row label under its category.'),
            ('Style', 'yes', 'text', 'Free text. One of the five composition doughnuts groups by this.'),
            ('Vehicle', 'yes', 'text', 'Free text, e.g. SMA, Mutual Fund, ETF. A doughnut axis.'),
            ('Source', 'yes', 'text', 'Free text, e.g. Internal, External. A doughnut axis.'),
            ('Liquidity', 'yes', 'text', 'Free text, e.g. Daily, Quarterly. A doughnut axis.'),
            ('ExposureCurrency', 'yes', 'text', 'Free text, e.g. USD. A doughnut axis.'),
            ('ProductCost', 'yes', 'number >= 0',
             'A PERCENT: 0.25 means 0.25%. Not a fraction.'),
            ('FeeGroup', 'yes', 'text',
             'One of the fee groups on the Valid values sheet. The set is defined by the feeRates '
             'sheet, so the two must agree.'),
            ('DistributionYield', 'optional', 'number >= 0',
             'A PERCENT. Leave blank, or drop the column, if there is none.'),
            ('MinimumInvestment', 'optional', 'number >= 0',
             'CURRENCY UNITS: 5000000 means $5m. Blank means no minimum. THIS BLOCKS THE EXPORT: '
             'a position below its product minimum refuses the download, so get these right.'),
        ],
        validation={'FeeGroup': ('feeGroups', len(fees.FEE_GROUPS))},
    ),
    dict(
        name='sleeves', tab='7A5A12',
        target='sleeves.csv', env='SCENARIO_SLEEVES_SEED', fmt='CSV',
        what='The starting sleeve library, one row per product in a sleeve. READ ONCE, on the '
             'first start against an empty sleeve database; after that the admin console is the '
             'source of truth and this file is not read again.',
        columns=[
            ('Variant', 'yes', 'text', 'One of the four implementation types on the Valid values sheet.'),
            ('Category', 'yes', 'text',
             'One of the seven sleeve categories on the Valid values sheet. Private Equity and '
             'Other Private Assets share the one combined category.'),
            ('Sleeve', 'yes', 'text (<= 80)', 'The sleeve name. Unique within a variant and category.'),
            ('ProductId', 'yes', 'text', 'Must exist in the products sheet.'),
            ('Weight', 'yes', 'number',
             'A FRACTION. One sleeve\'s rows must sum to 1 within 0.000001.'),
        ],
        validation={'Variant': ('variants', len(rules.IMPLEMENTATION_VARIANTS)),
                    'Category': ('categories', len(sleeveRepo.categories()))},
    ),
    dict(
        name='feeRates', tab='B42318',
        target='feeRates.csv', env='SCENARIO_FEES_SOURCE', fmt='CSV',
        what='The rate card: one row per cell. Deliver it through "feeTools --diff <csv>" then '
             '"--accept", which records the version and clears the placeholder flag the rail, '
             'the workbook and the bake manifest all read.',
        columns=[
            ('schedule', 'yes', 'text', 'CASP (one rate for all) or RDR (a rate per fee group).'),
            ('feeGroup', 'yes for RDR', 'text',
             'Blank for CASP. For RDR, the fee group. The distinct values here DEFINE the set the '
             'products sheet must use.'),
            ('tier', 'yes', 'text', 'Tier id, e.g. T1. Every row of a tier must carry the same edges.'),
            ('tierMin', 'yes', 'number', 'Top-account-size lower edge, CURRENCY UNITS. Inclusive.'),
            ('tierMax', 'no', 'number', 'Upper edge, CURRENCY UNITS, exclusive. BLANK means open-ended.'),
            ('source', 'yes', 'text', 'Management or PMG.'),
            ('point', 'yes', 'text',
             'Floor, Target or Ceiling. A proposal prices at "<source> <point>", e.g. PMG Floor.'),
            ('rate', 'yes', 'number >= 0', 'A PERCENT: 0.45 means 0.45%.'),
        ],
        validation={'schedule': ('schedules', len(fees.SCHEDULES)),
                    'feeGroup': ('feeGroups', len(fees.FEE_GROUPS)),
                    'source': ('sources', len(fees.SOURCES)),
                    'point': ('points', len(fees.POINTS))},
    ),
    dict(
        name='advisors', tab='5B6B7C',
        target='advisors.xlsx (inside the package, beside advisors.py)',
        env='none - there is no override', fmt='XLSX, first sheet',
        what='The Primary PWA directory behind the typeahead. A mandate\'s Primary PWA must equal '
             'a display string exactly, or creating a scenario is refused.',
        columns=[
            ('name', 'yes', 'text', 'The advisor name as it should read.'),
            ('office', 'yes', 'text', 'The office. The display string is "name - office" with an em dash.'),
        ],
        validation={},
    ),
    dict(
        name='assetEstimates', tab='8A6D1B',
        target='assetEstimates.json', env='SCENARIO_ASSET_ESTIMATES', fmt='JSON (see note)',
        what='The per-asset long-term estimates behind the workbook\'s assumptions sheet, one '
             'block per currency-and-hedging slice. THE SERVICE READS JSON: fill this table in, '
             'then convert it - the shape is on the portfolioAnalytics sheet.',
        columns=[
            ('Slice', 'yes', 'text', 'Currency and hedging, e.g. "USD|Hedged". Sixteen in all.'),
            ('AnalyticsCurrency', 'yes', 'text',
             'The currency the figures were actually computed in. Equal to the slice currency '
             'unless it was run in another, which the bake manifest then records.'),
            ('ReportingName', 'yes', 'text', 'One of the 19 asset reporting names on the Valid values sheet.'),
            ('Category', 'yes', 'text', 'That asset\'s category, as on the Valid values sheet.'),
            ('Lower', 'yes', 'number', 'FRACTION: 0.006 means 0.6%. Lower bound of the risk-premium estimate.'),
            ('Mean', 'yes', 'number', 'FRACTION. The central risk-premium estimate.'),
            ('Upper', 'yes', 'number', 'FRACTION. Upper bound.'),
            ('Volatility', 'yes', 'number', 'FRACTION.'),
            ('Sharpe', 'yes', 'number', 'A ratio, not a percent.'),
            ('TotalReturn', 'yes', 'number', 'FRACTION.'),
            ('HedgingRatio', 'yes', 'number', 'FRACTION: 1.0 means fully hedged.'),
            ('From', 'yes', 'date', 'ISO date, e.g. 1973-02-28. Start of the estimation window.'),
            ('To', 'yes', 'date', 'ISO date. End of the estimation window.'),
        ],
        validation={'Slice': ('slices', len(SLICES)),
                    'ReportingName': ('assetNames', len(ASSET_NAMES))},
    ),
]

LISTS = [
    ('currencies', 'Currency', CURRENCIES),
    ('hedging', 'Hedging basis', HEDGING),
    ('riskLevels', 'Risk level (least to most risky)', list(rules.RISK_LEVELS)),
    ('allocations', 'Allocation type', list(rules.ALLOCATIONS)),
    ('variants', 'Implementation type', list(rules.IMPLEMENTATION_VARIANTS)),
    ('categories', 'Sleeve category', sleeveRepo.categories()),
    ('feeGroups', 'Fee group', list(fees.FEE_GROUPS)),
    ('schedules', 'Fee schedule', list(fees.SCHEDULES)),
    ('sources', 'Fee source', list(fees.SOURCES)),
    ('points', 'Fee point', list(fees.POINTS)),
    ('levels', 'Fee level (source + point)', list(fees.LEVELS)),
    ('slices', 'Slice (currency|hedging)', SLICES),
    ('tickers', 'Asset ticker', TICKERS),
    ('assetNames', 'Asset reporting name', ASSET_NAMES),
]


def examples(name):
    """Two rows of the packaged stand-in, so the shape is never in doubt."""
    def csvRows(path, want):
        if not os.path.exists(path):
            return []
        with io.open(path, encoding='utf-8-sig') as fh:
            reader = csv.DictReader(fh)
            out = []
            for row in reader:
                out.append([row.get(c) if row.get(c) not in ('', None) else None for c in want])
                if len(out) == 2:
                    break
        return out
    want = [c for c, _, _, _ in dict((s['name'], s) for s in SHEETS)[name]['columns']]
    if name == 'saaPortfolios':
        return csvRows(os.path.join(ROOT, 'saaSource', 'saaPortfolios.csv'), want)
    if name == 'products':
        return csvRows(os.path.join(ROOT, 'productSource', 'products.csv'), want)
    if name == 'sleeves':
        return csvRows(os.path.join(ROOT, 'sleeveSource', 'sleeves.csv'), want)
    if name == 'feeRates':
        return csvRows(os.path.join(PKG, 'feeRates.csv'), want)
    if name == 'advisors':
        book = load_workbook(os.path.join(PKG, 'advisors.xlsx'))
        return [list(r) for r in book.active.iter_rows(min_row=2, max_row=3, values_only=True)]
    if name == 'assetEstimates':
        import json
        data = json.load(io.open(os.path.join(PKG, 'assetEstimates.json'), encoding='utf-8'))
        key = 'USD|Hedged'
        block = data['slices'][key]
        out = []
        for asset in block['assets'][:2]:
            out.append([key, block['analyticsCurrency'], asset['reportingName'], asset['category'],
                        asset['lower'], asset['mean'], asset['upper'], asset['volatility'],
                        asset['sharpe'], asset['totalReturn'], asset['hedgingRatio'],
                        asset['from'], asset['to']])
        return out
    return []


def number(value):
    """CSV gives strings; the template should hold numbers where numbers belong."""
    if value is None:
        return None
    try:
        text = str(value)
        return float(text) if ('.' in text or 'e' in text.lower()) else int(text)
    except ValueError:
        return value


NUMERIC = {'Weight', 'ProductCost', 'DistributionYield', 'MinimumInvestment',
           'tierMin', 'tierMax', 'rate', 'Lower', 'Mean', 'Upper', 'Volatility',
           'Sharpe', 'TotalReturn', 'HedgingRatio'}

book = Workbook()
book.remove(book.active)

# ------------------------------------------------------------------ read me --
sheet = book.create_sheet('Read me')
sheet.sheet_properties.tabColor = NAVY
sheet['A1'] = 'PMG Proposal Tool - data delivery templates'
sheet['A1'].font = TITLE
sheet['A2'] = ('One sheet per file the service reads. Fill each in, save it in the format named '
               'below, and point the environment variable at it. Every column, list and example '
               'here was generated from the code that validates the file, so what this workbook '
               'says is what the reader enforces.')
sheet['A2'].font = SUB
sheet['A2'].alignment = WRAP
sheet.merge_cells('A2:F2')
sheet.row_dimensions[2].height = 46
sheet['A4'] = 'Rows tinted like this are examples taken from the stand-in data. Delete them before you deliver.'
sheet['A4'].fill = EXAMPLE_FILL
sheet['A4'].font = EXAMPLE_FONT
sheet.merge_cells('A4:F4')

head = ['Sheet', 'Save as', 'Environment variable', 'Format', 'Required', 'What it feeds']
sheet.append([])
sheet.append(head)
headRow = sheet.max_row
for cell in sheet[headRow]:
    cell.font, cell.fill, cell.border = HEAD_FONT, HEAD_FILL, GRID
for spec in SHEETS:
    required = 'yes' if spec['env'].startswith('SCENARIO') else 'replace the packaged file'
    sheet.append([spec['name'], spec['target'], spec['env'], spec['fmt'], required, spec['what']])
    for cell in sheet[sheet.max_row]:
        cell.font, cell.border, cell.alignment = BODY, GRID, WRAP
sheet.append(['portfolioAnalytics', '<slice>.json + manifest.json', 'SCENARIO_BAKED_DIR',
              'JSON', 'yes', 'The risk and return figures themselves. Generated, not typed - '
              'see that sheet.'])
for cell in sheet[sheet.max_row]:
    cell.font, cell.border, cell.alignment = BODY, GRID, WRAP
for column, width in zip('ABCDEF', (18, 30, 30, 18, 24, 70)):
    sheet.column_dimensions[column].width = width
sheet.append([])
sheet.append(['Check your files with:'])
sheet[sheet.max_row][0].font = BOLD
for line in ('python -m cyrus_pmg.pmgService.scenario.bake --census        # the SAA extract: 0 unparsed, no unknown tickers',
             'python -m cyrus_pmg.pmgService.scenario.sleeveTools --census # the sleeve library: no broken sleeves, no orphans',
             'python -m cyrus_pmg.pmgService.scenario.feeTools --census    # the rate card, and whether it is still a placeholder'):
    sheet.append([line])
    sheet[sheet.max_row][0].font = Font(name='Consolas', size=10)
sheet.freeze_panes = 'A7'

# --------------------------------------------------------- column reference --
ref = book.create_sheet('Column reference')
ref.sheet_properties.tabColor = '5B6B7C'
ref['A1'] = 'Every column, and the rule it is validated against'
ref['A1'].font = TITLE
ref.append([])
ref.append(['Sheet', 'Column', 'Required', 'Type', 'Rule'])
for cell in ref[ref.max_row]:
    cell.font, cell.fill, cell.border = HEAD_FONT, HEAD_FILL, GRID
for spec in SHEETS:
    for name, required, kind, rule in spec['columns']:
        ref.append([spec['name'], name, required, kind, rule])
        for cell in ref[ref.max_row]:
            cell.font, cell.border, cell.alignment = BODY, GRID, WRAP
for column, width in zip('ABCDE', (18, 22, 14, 16, 96)):
    ref.column_dimensions[column].width = width
ref.freeze_panes = 'A4'

# ------------------------------------------------------------- valid values --
lists = book.create_sheet('Valid values')
lists.sheet_properties.tabColor = '1F5FBF'
ranges = {}
column = 1
for key, label, values in LISTS:
    letter = get_column_letter(column)
    cell = lists.cell(row=1, column=column, value=label)
    cell.font, cell.fill, cell.border = HEAD_FONT, HEAD_FILL, GRID
    for offset, value in enumerate(values, start=2):
        item = lists.cell(row=offset, column=column, value=value)
        item.font, item.border = BODY, GRID
    ranges[key] = "'Valid values'!${0}$2:${0}${1}".format(letter, len(values) + 1)
    lists.column_dimensions[letter].width = max(14, min(34, len(label) + 4,
                                                        max([len(str(v)) for v in values]) + 4))
    column += 1
# the ticker table, so a ticker can be matched to its name and category
start = column + 1
for offset, title in enumerate(('Asset ticker', 'Reporting name', 'Category'), start=start):
    cell = lists.cell(row=1, column=offset, value=title)
    cell.font, cell.fill, cell.border = HEAD_FONT, HEAD_FILL, GRID
    lists.column_dimensions[get_column_letter(offset)].width = 30
for row, (ticker, name, category) in enumerate(sorted(pw.ASSET_METADATA), start=2):
    for offset, value in enumerate((ticker, name, category), start=start):
        cell = lists.cell(row=row, column=offset, value=value)
        cell.font, cell.border = BODY, GRID
lists.freeze_panes = 'A2'

# ------------------------------------------------------------- the templates --
for spec in SHEETS:
    sheet = book.create_sheet(spec['name'])
    sheet.sheet_properties.tabColor = spec['tab']
    names = [c for c, _, _, _ in spec['columns']]
    sheet.append(names)
    for index, (name, required, kind, rule) in enumerate(spec['columns'], start=1):
        cell = sheet.cell(row=1, column=index)
        cell.font, cell.fill, cell.border = HEAD_FONT, HEAD_FILL, GRID
        cell.comment = Comment('{} ({}, {})\n\n{}'.format(name, required, kind, rule),
                               'Proposal Tool', height=150, width=320)
        sheet.column_dimensions[get_column_letter(index)].width = max(14, min(30, len(name) + 6))
    for row in examples(spec['name']):
        sheet.append([number(v) if names[i] in NUMERIC else v for i, v in enumerate(row)])
        for cell in sheet[sheet.max_row]:
            cell.font, cell.fill = EXAMPLE_FONT, EXAMPLE_FILL
    for name, (key, count) in spec['validation'].items():
        letter = get_column_letter(names.index(name) + 1)
        rule = DataValidation(type='list', formula1=ranges[key], allow_blank=True,
                              showDropDown=False, errorTitle='Not a permitted value',
                              error='Choose a value from the Valid values sheet.')
        sheet.add_data_validation(rule)
        rule.add('{0}2:{0}5000'.format(letter))
    sheet.freeze_panes = 'A2'

# ------------------------------------------------------ the bake, documented --
bake = book.create_sheet('portfolioAnalytics')
bake.sheet_properties.tabColor = '12233F'
bake['A1'] = 'The risk and return figures - generated, not typed'
bake['A1'].font = TITLE
rows = [
    ('', ''),
    ('What it is',
     'Under SCENARIO_ADAPTER=baked with SCENARIO_BAKED_FALLBACK=0 the service computes no '
     'analytics at all: it serves these files and nothing else. There is one file per currency '
     'and hedging basis, plus a manifest and the asset estimates. 43 portfolios x 16 slices = '
     '688 payloads, so this is produced by code, which is why it has no fill-in sheet.'),
    ('How to produce it',
     'On a machine with your analytics library on the path:\n'
     '    python -m cyrus_pmg.pmgService.scenario.bake --census\n'
     '    python -m cyrus_pmg.pmgService.scenario.bake --all --workers 4 --out <dir>\n'
     'then point SCENARIO_BAKED_DIR at <dir>. The one module that names the library is '
     'engine.py; SAA_ENGINE_PACKAGE renames it, and its _SYMBOLS table maps the calls.'),
    ('If you write the files directly',
     'The layout is below. Ship the SAA extract and the bake built from it TOGETHER: the '
     'manifest records the extract it was built from, and there is no staleness check at '
     'request time.'),
    ('', ''),
    ('manifest.json',
     'slices, updatedAt, portfoliosBaked, currencies, source{path, modified, portfolios, '
     'holdings, unparsed, unknownTickers}, vocabulary, facets, unparsedNames (must be empty), '
     'feeCard, currencySubstitutions.'),
    ('<CCY>_<HedgingSlug>.json  (16 files)',
     'USD_Hedged, USD_Unhedged, USD_ISGHedged, USD_EquityNotHedged, and the same for GBP, CHF '
     'and EUR. Each is an object keyed by portfolio key string, e.g. "USD|Moderate|Full|0".'),
    ('  each payload',
     'key{currency, riskLevel, allocationType, excludeRealAssets}, keyStr, name, header, '
     'categories[{name, weightPct, assets[{reportingName, weightPct}]}], '
     'metrics{estimatedReturnPct, volatilityPct, sharpe}, '
     'stress[{period, nominalPct, realPct}], '
     'premia[{group, horizon, label, nominalPct, realPct, kind}], '
     'and analyticsCurrency when it was computed in another currency.'),
    ('  units',
     'PERCENT throughout this file: 5.957 means 5.957%. Losses are negative. Every portfolio '
     'the SAA extract offers must appear in every slice, or choosing it returns a 502.'),
    ('', ''),
    ('assetEstimates.json',
     '{"slices": {"USD|Hedged": {"analyticsCurrency": "USD", "assets": [{reportingName, '
     'category, lower, mean, upper, volatility, sharpe, totalReturn, hedgingRatio, from, to}]}}}. '
     'FRACTIONS here, not percent. Note the hedging is written with a space ("USD|ISG Hedged") '
     'where the filename uses a slug (USD_ISGHedged). Fill the assetEstimates sheet in and '
     'convert it to this shape.'),
]
for label, text in rows:
    bake.append([label, text])
    cell = bake.cell(row=bake.max_row, column=1)
    cell.font = BOLD
    body = bake.cell(row=bake.max_row, column=2)
    body.font, body.alignment = BODY, WRAP
    if text:
        body.fill = NOTE_FILL
        bake.row_dimensions[bake.max_row].height = max(30, 15 * (len(text) // 95 + 1))
bake.column_dimensions['A'].width = 34
bake.column_dimensions['B'].width = 110

out = os.path.join(HERE, 'ProposalTool_DataTemplates.xlsx')
book.save(out)
print('wrote %s (%d sheets)' % (out, len(book.sheetnames)))
print('sheets:', book.sheetnames)
