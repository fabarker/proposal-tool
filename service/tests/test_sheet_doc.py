"""Phase 1 of the PowerPoint export plan (D119): the report as data.

Every layout and styling decision now lives in ``sheetDoc``'s builders, and
``workbook.py`` is the Excel renderer. These tests pin the properties the
split exists for; the golden workbook test proves the split moved nothing.
"""

import io
import json
import os

from openpyxl import load_workbook

from cyrus_pmg.pmgService.scenario import assetEstimates, rules, sheetDoc
from cyrus_pmg.pmgService.scenario.workbook import (
    _TEXT_COLUMNS, _WIDTHS, buildImplementationRows, implColumns)

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, 'golden')


def _goldenCase():
    with open(os.path.join(GOLDEN, 'engine_USD_Hedged_2col.results.json'),
              encoding='utf-8') as handle:
        results = json.load(handle)
    with open(os.path.join(GOLDEN, 'engine_USD_Hedged_2col.impl.json'),
              encoding='utf-8') as handle:
        implementation = json.load(handle)
    return results, implementation


def _dump(doc):
    """A Doc as plain data, so two builds can be compared for identity."""
    return {
        'name': doc.name,
        'rows': {row: {'height': spec.height,
                       'cells': {col: (cell.value, cell.fmt, cell.font,
                                       cell.fill, cell.border, cell.align)
                                 for col, cell in spec.cells.items()}}
                 for row, spec in doc.rows.items()},
        'merges': doc.merges, 'widths': doc.widths, 'freeze': doc.freeze,
        'signRules': doc.signRules, 'sections': sorted(doc.sections),
    }


def _docs():
    results, implementation = _goldenCase()
    model = buildImplementationRows(
        results[0], implementation['sleeves'], rules.AUTO_SLEEVE_CATEGORIES,
        25_000_000, implementation['variant'], implementation['tacticalTilt'],
        implementation['feeSchedule'], implementation['feeLevel'],
        10_000_000, implementation['volPremium'], 'USD')
    impl, headerRow, totalRow = sheetDoc.buildImplementationDoc(
        model, implColumns(True), _WIDTHS, _TEXT_COLUMNS,
        variant=implementation['variant'],
        feeSchedule=implementation['feeSchedule'],
        feeLevel=implementation['feeLevel'],
        includeFees=True, proposalId='pr_0123456789ab')
    return {
        'portfolios': sheetDoc.buildPortfoliosDoc(results),
        'risk_dashboard': sheetDoc.buildRiskDoc(results),
        'assumptions': sheetDoc.buildAssumptionsDoc(
            assetEstimates.forSlice('USD', 'Hedged'), results),
        'Implementation': impl,
    }, headerRow, totalRow


def test_the_builders_import_no_file_format():
    """The one hard rule (C1): a style in the Doc is plain data. The module
    must not import openpyxl - or any other file format - however its
    comments talk about them. Read from the AST, so prose cannot trip it
    and an import cannot hide in it."""
    import ast
    path = os.path.join(HERE, '..', 'cyrus_pmg', 'pmgService', 'scenario', 'sheetDoc.py')
    with open(path, encoding='utf-8') as handle:
        tree = ast.parse(handle.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            imported.add(module)
            imported.update('{}.{}'.format(module, alias.name) for alias in node.names)
    for banned in ('openpyxl', 'pptx', 'xlsxwriter'):
        assert not any(name == banned or name.startswith(banned + '.')
                       for name in imported), (banned, sorted(imported))
    # and nothing sideways: the module reaches only its own package's data
    assert all(name.startswith(('datetime', 'fees', 'portfolio_weights', ''))
               for name in imported), sorted(imported)


def test_the_excel_writers_hold_no_styling_literal_of_their_own():
    """The four sheet writers are wrappers: create the sheet, render the Doc,
    add the Excel-only extras. A Font or Fill appearing in one again would
    mean a styling decision the deck cannot see (D119)."""
    path = os.path.join(HERE, '..', 'cyrus_pmg', 'pmgService', 'scenario', 'workbook.py')
    with open(path, encoding='utf-8') as handle:
        source = handle.read()
    wrappers = source[source.index('def writeImplementationSheet'):
                      source.index('def writeWorkbook')]
    for construct in ('Font(', 'PatternFill(', 'Border(', 'Alignment(',
                      'Side(', 'CellIsRule('):
        assert construct not in wrappers, construct


def test_the_builders_are_deterministic():
    """The same payloads produce the same Doc, attribute for attribute -
    the property that lets two renderers trust one build."""
    first, _, _ = _docs()
    second, _, _ = _docs()
    for name in first:
        assert _dump(first[name]) == _dump(second[name]), name


def test_the_docs_carry_the_sheets_shape():
    """A few load-bearing facts a renderer relies on, read from the Docs
    rather than from a rendered file."""
    docs, headerRow, totalRow = _docs()

    impl = docs['Implementation']
    header = [impl.cell(headerRow, col).value
              for col in range(1, len(implColumns(True)) + 1)]
    assert header == implColumns(True)
    assert impl.cell(1, 1).value == 'Proposal UID'
    assert impl.cell(totalRow, 1).value == 'Total'
    assert impl.freeze == 'A' + str(headerRow + 1)
    assert impl.sections, 'category bands mark the page-break rows'

    risk = docs['risk_dashboard']
    assert len(risk.signRules) == 1, 'the stress rule is declared once'
    (col1, row1, col2, row2, neg, pos) = risk.signRules[0]
    assert (neg, pos) == (sheetDoc.STRESS_NEG, sheetDoc.STRESS_POS)
    assert risk.cell(row1 - 1, 1).value == 'Predicted Performance Over Stress Periods'
    assert risk.merges, 'the nominal/real pairs merge on the summary rows'

    port = docs['portfolios']
    labels = [row.cells[1].value for row in port.rows.values()
              if 1 in row.cells and row.cells[1].value]
    assert 'TOTAL' in labels and 'Estimated Mean Return' in labels

    assum = docs['assumptions']
    assert assum.cell(1, 2).value == 'Long-Term Estimates'
    assert (2, 2, 2, 4) in [tuple(m) for m in assum.merges]


def test_a_doc_renders_the_same_sheet_twice():
    """Rendering is mechanical: the same Doc onto two sheets produces
    identical cells - the renderer holds no state and no opinion."""
    from openpyxl import Workbook
    from cyrus_pmg.pmgService.scenario.workbook import _renderDoc
    docs, _, _ = _docs()
    doc = docs['portfolios']
    book = Workbook()
    one = book.create_sheet('one')
    two = book.create_sheet('two')
    _renderDoc(one, doc)
    _renderDoc(two, doc)
    for row in range(1, one.max_row + 1):
        for col in range(1, one.max_column + 1):
            a, b = one.cell(row=row, column=col), two.cell(row=row, column=col)
            assert (a.value, a.number_format, a.font.name, a.font.bold,
                    a.fill.fgColor.rgb if a.fill.patternType else None) == \
                   (b.value, b.number_format, b.font.name, b.font.bold,
                    b.fill.fgColor.rgb if b.fill.patternType else None)
