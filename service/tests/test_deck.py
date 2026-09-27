"""Phase 3 of the PowerPoint export plan (D121): the deck renderer.

The same Docs the Excel renderer transcribes, rendered as slides - and held
to them cell for cell: every table cell's text must equal renderNumber of
the Doc's value under the Doc's format, the sign rule must resolve to the
CellIsRule's inks, the band fills and black rules must survive the trip,
and the doughnuts must carry the page's palette with the workbook's hole.
"""

import io
import json
import os

import pytest
from pptx import Presentation
from pptx.enum.chart import XL_CHART_TYPE
from pptx.oxml.ns import qn

from cyrus_pmg.pmgService.scenario import assetEstimates, pptWriter, rules, sheetDoc
from cyrus_pmg.pmgService.scenario.pptWriter import writeDeck
from cyrus_pmg.pmgService.scenario.types import BasisInput, MandateInput
from cyrus_pmg.pmgService.scenario.workbook import (
    DONUT_PALETTE, buildImplementationRows, donutBreakdown, implColumns,
    stampedProposalId, _TEXT_COLUMNS, _WIDTHS)

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, 'golden')
UID = 'pr_0123456789ab'
BASIS = BasisInput(currency='USD', hedging='Hedged')
MANDATE = MandateInput(topAccountSize=10_000_000.0, mandateSize=25_000_000.0,
                       primaryPwa='J. Mercer')


def _goldenCase():
    with open(os.path.join(GOLDEN, 'engine_USD_Hedged_2col.results.json'),
              encoding='utf-8') as handle:
        results = json.load(handle)
    with open(os.path.join(GOLDEN, 'engine_USD_Hedged_2col.impl.json'),
              encoding='utf-8') as handle:
        implementation = json.load(handle)
    return results, implementation


def _model(results, implementation, includeFees=True):
    return buildImplementationRows(
        results[0], implementation['sleeves'], rules.AUTO_SLEEVE_CATEGORIES,
        MANDATE.mandateSize, implementation['variant'],
        implementation['tacticalTilt'],
        implementation['feeSchedule'] if includeFees else None,
        implementation['feeLevel'], MANDATE.topAccountSize,
        implementation['volPremium'], 'USD')


def _four(results):
    """Four portfolios: the golden pair and a renamed copy of it. The slide
    configuration turns on the count alone, so the figures may repeat."""
    return list(results) + [dict(r, name=r['name'] + ' (B)') for r in results]


def _deck(proposalId=UID, includeFees=True, cover=True, results=None):
    golden, implementation = _goldenCase()
    return writeDeck(BASIS, MANDATE, results or golden, implementation['sleeves'],
                     rules.AUTO_SLEEVE_CATEGORIES, implementation['variant'],
                     implementation['tacticalTilt'],
                     implementation['feeSchedule'], implementation['feeLevel'],
                     includeFees, implementation['volPremium'],
                     assets=assetEstimates.forSlice('USD', 'Hedged'),
                     proposalId=proposalId, cover=cover)


def _plan(results=None, includeFees=True):
    """The deck's own plan for the golden case, as writeDeck makes it."""
    golden, implementation = _goldenCase()
    return pptWriter.planDeck(
        BASIS, MANDATE, results or golden, _model(golden, implementation, includeFees),
        assetEstimates.forSlice('USD', 'Hedged'), includeFees,
        implementation['variant'],
        implementation['feeSchedule'] if includeFees else None,
        implementation['feeLevel'], UID)


def _tables(slide):
    return [shape.table for shape in slide.shapes if shape.has_table]


def _normal(text):
    lines = str(text).split('\n')
    return '\n'.join([lines[0]] + [line.strip() for line in lines[1:]])


def test_the_deck_is_the_plan_cell_for_cell():
    """The heart of parity: walk every planned table on every slide against
    the table drawn there. Every cell's text is renderNumber of the Doc's
    value under the Doc's format; every row sits at its planned height and
    every column at its planned width; every run at the planned size."""
    for results in (None, _four(_goldenCase()[0])):
        plan = _plan(results)
        prs = Presentation(io.BytesIO(_deck(results=results)))
        assert len(prs.slides) == len(plan)
        for slideIndex, entry in enumerate(plan):
            drawn = _tables(prs.slides[slideIndex])
            planned = entry.get('tables', [])
            assert len(drawn) == len(planned), (slideIndex, entry['kind'])
            for table, tp in zip(drawn, planned):
                columns = pptWriter._docColumns(tp.doc)
                assert (len(table.rows), len(table.columns)) == (len(tp.rows), columns)
                for i, width in enumerate(tp.widths()):
                    assert table.columns[i].width == pptWriter._pt(width)
                base = pptWriter._base(tp.doc)
                for tableRow, rowIndex in enumerate(tp.rows):
                    assert table.rows[tableRow].height == pptWriter._pt(tp.heights[tableRow])
                    spec = tp.doc.rows.get(rowIndex)
                    for column in range(1, columns + 1):
                        cell = spec.cells.get(column) if spec is not None else None
                        expected = ('' if cell is None
                                    else sheetDoc.renderNumber(cell.value, cell.fmt))
                        if column == 1:
                            expected = expected.strip()
                        got = table.cell(tableRow, column - 1)
                        assert _normal(got.text) == _normal(expected), (
                            tp.doc.name, rowIndex, column)
                        font = (cell.font if cell is not None and cell.font else None) \
                            or pptWriter._DEFAULT_FONT
                        want = tp.font * font.get('size', base) / base
                        for paragraph in got.text_frame.paragraphs:
                            for run in paragraph.runs:
                                assert abs(run.font.size.pt - want) < 0.01


def test_one_or_two_portfolios_share_one_slide_without_the_repeated_rows():
    """D124. With no comparison or one: allocation and risk on ONE slide,
    allocation left and risk right; the risk table without the rows that
    repeat the allocation; both set in one size, both a little in from the
    full box, and both ending at the bottom of their boxes together."""
    plan = _plan()
    table = [entry for entry in plan if entry['kind'] == 'table']
    first = table[0]
    assert first['heading'] == 'Strategic Asset Allocation & Risk'
    alloc, risk = first['tables']
    assert (alloc.doc.name, risk.doc.name) == ('portfolios', 'risk_dashboard')
    assert risk.rows == pptWriter.riskWithoutRepeats(risk.doc)
    labels = {str(risk.doc.rows[r].cells[1].value).strip()
              for r in risk.rows if 1 in risk.doc.rows[r].cells}
    for repeated in ('Public Equity', 'Estimated Mean Return', 'Sharpe Ratio', 'Volatility'):
        assert repeated not in labels, repeated
    assert 'Factor Based Risk Analytics' in labels and 'Financial Crisis' in labels
    assert alloc.font == risk.font, 'one size across the slide'
    full = pptWriter.FULL_BOX
    for tp in (alloc, risk):
        left, top, width, height = tp.box
        assert left > full[0] and top > full[1], 'more border than a lone table'
        assert left + width < full[0] + full[2] and top + height < full[1] + full[3]
        assert abs(sum(tp.heights) - height) < 0.5, 'ends at the bottom of its box'
    assert alloc.box[0] + alloc.box[2] < risk.box[0], 'allocation left, risk right'
    # and no Risk Dashboard slide of its own
    assert not [e for e in table if e['heading'] == 'Risk Dashboard']


def test_three_or_more_portfolios_take_a_slide_each_at_full_width():
    """D124. With two comparisons or more: allocation on one slide and risk
    on the next, each filling the full box, and the risk table whole."""
    plan = _plan(_four(_goldenCase()[0]))
    headings = [entry.get('heading') for entry in plan]
    assert 'Strategic Asset Allocation & Risk' not in headings
    alloc = [e for e in plan if e.get('heading') == 'Strategic Asset Allocation']
    risk = [e for e in plan if e.get('heading') == 'Risk Dashboard']
    assert alloc and risk
    assert headings.index('Strategic Asset Allocation') + len(alloc) == \
        headings.index('Risk Dashboard'), 'risk follows allocation'
    for entry in alloc + risk:
        (tp,) = entry['tables']
        assert tp.box == pptWriter.FULL_BOX
    riskRows = [r for entry in risk for r in entry['tables'][0].rows]
    whole = risk[0]['tables'][0].doc
    assert set(riskRows) == set(whole.rows), 'the risk table keeps every row here'


def test_every_planned_table_fits_its_box_at_a_readable_size():
    """The fit's invariants, over both configurations and fees on and off:
    no table taller than its box, every size between MIN_PT and BASE_PT,
    every figure clear of its cell on one line, and one size per slide."""
    golden = _goldenCase()[0]
    for results in (None, _four(golden)):
        for includeFees in (True, False):
            for entry in _plan(results, includeFees):
                plans = entry.get('tables', [])
                if len(plans) > 1:
                    assert len({tp.font for tp in plans}) == 1
                for tp in plans:
                    assert pptWriter.MIN_PT <= tp.font <= pptWriter.BASE_PT
                    assert sum(tp.heights) <= tp.box[3] + 1e-6, (tp.doc.name, sum(tp.heights))
                    heights, fits = pptWriter._measure(tp, tp.font)
                    assert fits, (tp.doc.name, tp.font)


def test_a_table_too_tall_for_one_slide_continues_with_its_header():
    """Under MIN_PT it paginates rather than shrinking further: cut at a
    section mark, the header repeated, one size on every page, every row on
    exactly one page."""
    golden = _goldenCase()[0]
    doc = sheetDoc.buildPortfoliosDoc(golden)
    short = (pptWriter.FULL_BOX[0], pptWriter.FULL_BOX[1], pptWriter.FULL_BOX[2], 220.0)
    plans = pptWriter.planTable(doc, short, pptWriter._shares(44.0, 3))
    assert len(plans) > 1
    # cut at the floor size, then every page re-fitted and set in the one size
    # the tallest page allows - never below the floor, never mixed
    assert len({tp.font for tp in plans}) == 1
    assert pptWriter.MIN_PT <= plans[0].font < pptWriter.BASE_PT
    body = [r for tp in plans for r in tp.rows if r != 1]
    assert body == [r for r in sorted(doc.rows) if r != 1]
    assert all(tp.rows[0] == 1 for tp in plans), 'the header repeats'
    assert all(tp.rows[1] in doc.sections for tp in plans[1:]), 'breaks at a category'
    assert all(sum(tp.heights) <= short[3] + 1e-6 for tp in plans)


def test_font_metrics_measure_the_faces_the_deck_embeds():
    """The committed metrics, spot-checked against the house faces' own
    figures (D125): GS Sans's figures are 0.58 em wide, GS Sans Condensed's
    tabular figures 0.494 em in both weights, and both lines 1.247 em."""
    sans, condensed = sheetDoc.SANS, sheetDoc.CONDENSED
    assert abs(pptWriter.textWidth('0', sans, False, 10.0) - 5.8) < 1e-9
    assert abs(pptWriter.textWidth('100.0%', sans, False, 10.0)
               - (4 * 5.8 + pptWriter.textWidth('.', sans, False, 10.0)
                  + pptWriter.textWidth('%', sans, False, 10.0))) < 1e-9
    for bold in (False, True):
        assert {pptWriter.textWidth(d, condensed, bold, 10.0) for d in '0123456789'} == {4.94}
    assert pptWriter._lineHeight(sans, False, 10.0) == pytest.approx(12.47, abs=0.01)
    # GS Sans Light has no bold cut and a face nothing measured has no
    # metrics: both are measured as GS Sans
    assert pptWriter.textWidth('Abc', sheetDoc.SANS_LIGHT, True, 11) == \
        pptWriter.textWidth('Abc', sans, True, 11)
    assert pptWriter.textWidth('Abc', 'Unmeasured', False, 11) == \
        pptWriter.textWidth('Abc', sans, False, 11)
    assert pptWriter._wrapLines('Conditional Value at Risk with 99% Confidence',
                                condensed, True, 10, 80) >= 2


def test_the_stress_rule_resolves_to_the_workbooks_inks():
    """Red below zero, green above, base ink outside the rule's range -
    resolved from the raw value exactly as the CellIsRule would."""
    prs = Presentation(io.BytesIO(_deck()))
    # the combined slide: allocation first, risk second (D124)
    table = _tables(prs.slides[1])[1]
    inks = {}
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    inks.setdefault(run.text, str(run.font.color.rgb))
    assert inks['-17.7%'] == '9C0006', 'a loss in the stress block is red'
    assert inks['10.7%'] == '006100', 'a gain in the stress block is green'
    # VaR is positive but OUTSIDE the rule's range: base ink, not green
    assert inks['17.4%'] == '000000'
    # and on the full-width risk slide, the Sharpe row too
    four = Presentation(io.BytesIO(_deck(results=_four(_goldenCase()[0]))))
    risk = [t for s in four.slides for t in _tables(s)
            if any('Oil Embargo' in c.text for row in t.rows for c in row.cells)][0]
    sharpe = [row for row in risk.rows if row.cells[0].text == 'Sharpe Ratio'][0]
    assert str(sharpe.cells[1].text_frame.paragraphs[0].runs[0].font.color.rgb) == '000000', \
        'Sharpe sits outside the rule'


def test_the_implementation_slide_keeps_the_bands_and_the_rules():
    """The category band's fill, the total's black rules, and no styling
    invented: left and right edges are explicit no-lines."""
    prs = Presentation(io.BytesIO(_deck()))
    table = _tables(prs.slides[2])[0]             # cover, combined, implementation
    bandCell = totalCell = None
    for row in table.rows:
        cells = list(row.cells)
        if cells[0].text == 'Public Equity':
            bandCell = cells[0]
        if cells[0].text == 'Total':
            totalCell = cells[0]
    assert bandCell is not None and totalCell is not None
    assert str(bandCell.fill.fore_color.rgb) == 'D3DDEA'
    tcPr = totalCell._tc.get_or_add_tcPr()
    for edge, lit in (('a:lnT', True), ('a:lnB', True), ('a:lnL', False)):
        lines = tcPr.findall(qn(edge))
        assert len(lines) == 1, edge
        fills = lines[0].findall(qn('a:solidFill'))
        if lit:
            assert fills and fills[0].find(qn('a:srgbClr')).get('val') == '000000'
        else:
            assert lines[0].find(qn('a:noFill')) is not None


def test_the_doughnuts_are_native_with_the_pages_palette_and_the_hole():
    """Five doughnut charts, slice colours by the alphabetical palette rule,
    the workbook's 55% hole written where python-pptx has no property."""
    results, implementation = _goldenCase()
    model = _model(results, implementation)
    items = [item for group in model['groups'] for item in group['items']]
    prs = Presentation(io.BytesIO(_deck()))
    chartSlide = prs.slides[3]                    # after the implementation
    charts = [shape.chart for shape in chartSlide.shapes if shape.has_chart]
    assert len(charts) == 5
    for chart in charts:
        assert chart.chart_type == XL_CHART_TYPE.DOUGHNUT
        holes = chart._chartSpace.findall('.//' + qn('c:holeSize'))
        assert holes and all(hole.get('val') == '55' for hole in holes)
    styleSlices = donutBreakdown(items, 'style')
    points = charts[0].series[0].points
    got = [str(points[index].format.fill.fore_color.rgb)
           for index in range(len(styleSlices))]
    assert got == [DONUT_PALETTE[entry['slot']] for entry in styleSlices]


def test_the_deck_is_stamped_like_a_workbook_and_a_draft_is_not():
    """dc:identifier carries the UID, so stampedProposalId reads a deck
    exactly as it reads a workbook; a deck with no delivered proposal says
    Draft in every footer and stamps nothing."""
    deck = _deck()
    assert stampedProposalId(deck) == UID
    prs = Presentation(io.BytesIO(deck))
    assert prs.core_properties.title == 'PMG Proposal {} - USD Hedged'.format(UID)

    draft = _deck(proposalId=None)
    assert stampedProposalId(draft) is None
    drafted = Presentation(io.BytesIO(draft))
    texts = ' '.join(shape.text_frame.text for slide in drafted.slides
                     for shape in slide.shapes if shape.has_text_frame)
    assert 'Draft — no delivered proposal for this scenario' in texts
    assert UID not in texts


def test_the_fee_columns_leave_the_deck_with_the_fees():
    """includeFees=False: the two fee columns are absent from the slide as
    they are absent from the sheet, because implColumns drives both."""
    prs = Presentation(io.BytesIO(_deck(includeFees=False)))
    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_table:
                continue
            header = [cell.text for cell in next(iter(shape.table.rows)).cells]
            if 'Products' in header:
                assert 'Mgmt fee' not in header
                assert 'Wtd fee (bp)' not in header
                assert 'Product Cost' in header, 'product cost stays (D52)'
                return
    pytest.fail('no implementation table found')


def test_the_cover_is_a_flag():
    withCover = Presentation(io.BytesIO(_deck()))
    without = Presentation(io.BytesIO(_deck(cover=False)))
    assert len(withCover.slides) == len(without.slides) + 1
    assert not any(shape.has_table for shape in withCover.slides[0].shapes)
    assert any(shape.has_table for shape in without.slides[0].shapes)


# --------------------------------------------------------------------- #
# One delivery, both files, both locked (D123).
# --------------------------------------------------------------------- #

def test_one_export_delivers_both_files_or_neither(monkeypatch):
    """Over HTTP, the D123 story. The export refuses (both files) where the
    workbook always refused; delivered, it is ONE zip holding the workbook
    and the deck, both stamped with one UID, both recorded; the admin
    register hands each back byte for byte; and there is no deck-only route
    left to take one without the other."""
    import zipfile
    from fastapi.testclient import TestClient
    from cyrus_pmg.pmgService.isgPMGService import app
    from cyrus_pmg.pmgService.scenario import proposalRegister, sleeves
    from cyrus_pmg.pmgService.scenario.sleeves import listSleeves
    from cyrus_pmg.pmgService.scenario.types import PortfolioKey
    monkeypatch.setenv('PMG_ALLOWED_KERBEROS', 'bob,alice')
    monkeypatch.setenv('PMG_ADMIN_KERBEROS', 'alice')
    client = TestClient(app)
    pwa, admin = {'X-Kerberos': 'bob'}, {'X-Kerberos': 'alice'}

    made = client.post('/api/v1/scenario', headers=pwa, json={
        'mandate': {'topAccountSize': 1e9, 'mandateSize': 1e9,
                    'primaryPwa': 'A. Castellanos — Madrid'},
        'basis': {'currency': 'USD', 'hedging': 'Hedged'}})
    assert made.status_code == 200, made.text
    sid = made.json()['id']
    assert client.put('/api/v1/scenario/' + sid, headers=pwa,
                      json={'variant': sleeves.VARIANTS[0]}).status_code == 200
    key = {'currency': 'USD', 'riskLevel': 'Moderate', 'allocationType': 'Full',
           'excludeRealAssets': False}
    resolved = client.post('/api/v1/scenario/' + sid + '/portfolio', headers=pwa,
                           json={'key': key, 'role': 'base'})
    assert resolved.status_code == 200, resolved.text

    # refused as ever: no sleeves - and nothing half-delivered
    refused = client.post('/api/v1/scenario/' + sid + '/export', headers=pwa)
    assert refused.status_code == 422
    assert 'sleeve' in refused.json()['error'].lower()

    chosen = {}
    for category in resolved.json()['portfolio']['categories']:
        if category['name'] in rules.AUTO_SLEEVE_CATEGORIES:
            continue
        under = rules.sleeveCategory(category['name'])
        if under not in chosen:
            chosen[under] = listSleeves(under, sleeves.VARIANTS[0],
                                        PortfolioKey.fromDict(key))[0]['name']
    assert client.put('/api/v1/scenario/' + sid, headers=pwa,
                      json={'sleeves': chosen}).status_code == 200

    delivered = client.post('/api/v1/scenario/' + sid + '/export', headers=pwa)
    assert delivered.status_code == 200, delivered.text
    assert delivered.headers['content-type'] == 'application/zip'
    uid = delivered.headers['x-proposal-id']
    assert delivered.headers['content-disposition'].endswith('_{}.zip"'.format(uid))
    archive = zipfile.ZipFile(io.BytesIO(delivered.content))
    byKind = {name.rsplit('.', 1)[1]: archive.read(name) for name in archive.namelist()}
    assert sorted(byKind) == ['pptx', 'xlsx'], 'both files, and only them'
    assert stampedProposalId(byKind['xlsx']) == uid
    assert stampedProposalId(byKind['pptx']) == uid

    # the register holds both, and the admin console hands both back
    for leaf, kind in (('workbook', 'xlsx'), ('deck', 'pptx')):
        fetched = client.get('/api/v1/scenario/repository/proposals/{}/{}'.format(uid, leaf),
                             headers=admin)
        assert fetched.status_code == 200, (leaf, fetched.text)
        assert fetched.content == byKind[kind], leaf
        assert client.get('/api/v1/scenario/repository/proposals/{}/{}'.format(uid, leaf),
                          headers=pwa).status_code == 403
    assert proposalRegister.getProposal(uid)['deckBytes'] == len(byKind['pptx'])

    # there is no way to take the deck alone
    assert client.post('/api/v1/scenario/' + sid + '/export.pptx',
                       headers=pwa).status_code in (404, 405)


def test_the_deck_is_locked_like_the_workbook():
    """D123: the deck carries a password to modify - the workbook's own
    PA55WORD, verified by recomputing the ISO write-protection hash - sits in
    schema order after defaultTextStyle, is marked final, and never carries
    the plaintext. Every deck, draft or delivered."""
    import base64
    import re
    import zipfile
    from cyrus_pmg.pmgService.scenario.workbook import SHEET_PASSWORD
    for deck in (_deck(), _deck(proposalId=None)):
        archive = zipfile.ZipFile(io.BytesIO(deck))
        presentation = archive.read('ppt/presentation.xml').decode('utf-8')
        found = re.search(r'<p:modifyVerifier ([^>]*)/>', presentation)
        assert found, 'a password to modify'
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', found.group(1)))
        assert (attrs['cryptAlgorithmSid'], attrs['spinCount']) == ('14', '100000')
        salt = base64.b64decode(attrs['saltData'])
        stored = base64.b64decode(attrs['hashData'])
        assert pptWriter.modifyHash(SHEET_PASSWORD, salt) == stored
        assert pptWriter.modifyHash('pa55word', salt) != stored, 'case matters'
        tags = [t for t in re.findall(r'<(p:[A-Za-z]+)', presentation) if t.count(':') == 1]
        at = tags.index('p:modifyVerifier')
        assert tags[at - 1] == 'p:defaultTextStyle', 'schema order'
        custom = archive.read('docProps/custom.xml').decode('utf-8')
        assert 'name="_MarkAsFinal"' in custom and '<vt:bool>true</vt:bool>' in custom
        assert 'custom-properties' in archive.read('_rels/.rels').decode('utf-8')
        assert '/docProps/custom.xml' in archive.read('[Content_Types].xml').decode('utf-8')
        assert Presentation(io.BytesIO(deck)).core_properties.content_status == 'Final'
        for plain in (SHEET_PASSWORD.encode('utf-8'), SHEET_PASSWORD.encode('utf-16-le')):
            assert plain not in deck, 'only the hash reaches the file'
        # each deck salts afresh, so two locks never share a hash
    salts = set()
    for _ in range(2):
        text = zipfile.ZipFile(io.BytesIO(_deck())).read('ppt/presentation.xml').decode()
        salts.add(re.search(r'saltData="([^"]+)"', text).group(1))
    assert len(salts) == 2


def test_the_page_offers_one_download_and_awaits_its_writes():
    """The front end, pinned at the source (D122, D123): every
    fire-and-forget scenario write is tracked, the one export runs behind the
    save barrier, and the card offers a single button for both files."""
    root = os.path.join(HERE, '..', '..', 'generator')
    with open(os.path.join(root, 'js', 'core.js'), encoding='utf-8') as handle:
        core = handle.read()
    assert core.count("trackWrite(apiFetch('/scenario/'"
                      " + encodeURIComponent(state.scenarioId), {") == 6, \
        'six fire-and-forget writes, each tracked'
    assert 'writesSettled: writesSettled,' in core
    with open(os.path.join(root, 'js', 'implementation.js'), encoding='utf-8') as handle:
        impl = handle.read()
    assert 'await App.writesSettled();' in impl
    assert "exportProposal(); return;" in impl
    assert 'Download proposal' in impl
    for gone in ('implexportppt', 'Download PowerPoint', "'.pptx'", 'exportFile('):
        assert gone not in impl, gone
    assert 'Assumptions and ' in impl, 'the card names all four sheets (A7)'
