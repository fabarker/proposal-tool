"""The product form and the Fees tab, in the page (D165, D166).

Their pure helpers run through node against fixture data, the way the
catalogue's and the Sleeves view's are proved: the form's checks say what
products.clean would, and the card editor's say what feeCard.normalise and
describeChange would - so the page offers Save only for what the server
will take, and its review says what the history will. The wiring that keeps
an unsaved form or card safe is held by reading the source."""
import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(HERE, '..', '..', 'generator', 'js', 'repository.js')


def _source():
    with open(JS, encoding='utf-8') as fh:
        return fh.read()


def _between(source, begin, end):
    start = source.index(begin)
    return source[start:source.index(end)]


def _node(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    done = subprocess.run([node, '-e', script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-1200:]
    return json.loads(done.stdout)


def _products():
    return _between(_source(), '/* product-editor-helpers-begin', '/* product-editor-helpers-end */')


def _fees():
    return _between(_source(), '/* fee-editor-helpers-begin', '/* fee-editor-helpers-end */')


CTX = {'isNew': True, 'feeGroups': ['Passive', 'Core Active'],
       'takenIds': {'gs-taken': 'GS Taken Fund'}, 'withdrawnIds': {'gs-gone': 'GS Gone Fund'}}
GOOD = {'productId': 'probe-etf', 'name': 'Probe ETF', 'ticker': 'PRB', 'assetClass': 'Equity',
        'style': 'Passive', 'vehicle': 'ETF', 'shareClass': 'Accumulating', 'source': 'Internal',
        'liquidity': 'Daily', 'exposureCurrency': 'USD', 'productCost': '0.12',
        'feeGroup': 'Passive', 'distributionYield': '', 'minimumInvestment': ''}


def test_the_product_form_refuses_what_the_server_refuses_naming_the_same_field():
    cases = {
        'good': {},
        'noName': {'name': '  '},
        'freeCost': {'productCost': 'free'},
        'negative': {'productCost': '-1'},
        'slip': {'productCost': '45'},
        'pctSign': {'productCost': '0.25%'},
        'group': {'feeGroup': 'Made Up'},
        'noGroup': {'feeGroup': ''},
        'share': {'shareClass': 'Distribution'},
        'word': {'style': ''},
        'minimum': {'minimumInvestment': '5m'},
        'commas': {'minimumInvestment': '5,000,000'},
        'taken': {'productId': 'gs-taken'},
        'gone': {'productId': 'gs-gone'},
        'noId': {'productId': ''},
    }
    got = _node(_products() + '''
var good = %s, ctx = %s, cases = %s, out = {};
Object.keys(cases).forEach(function (k) { out[k] = prdProblems(Object.assign({}, good, cases[k]), ctx); });
out.editIgnoresTheId = prdProblems(Object.assign({}, good, { productId: 'gs-taken' }), Object.assign({}, ctx, { isNew: false }));
process.stdout.write(JSON.stringify(out));
''' % (json.dumps(GOOD), json.dumps(CTX), json.dumps(cases)))
    assert got['good'] == {} and got['pctSign'] == {} and got['commas'] == {}
    assert list(got['noName']) == ['name']
    assert 'not a number' in got['freeCost']['productCost']
    assert 'cannot be negative' in got['negative']['productCost']
    assert 'looks wrong' in got['slip']['productCost']
    assert 'not on the fee card' in got['group']['feeGroup']
    assert 'Choose a fee group' in got['noGroup']['feeGroup']
    assert list(got['share']) == ['shareClass']
    assert got['word'] == {'style': 'Style is needed.'}
    assert 'not a number' in got['minimum']['minimumInvestment']
    assert 'GS Taken Fund' in got['taken']['productId']
    assert 'never reused' in got['gone']['productId']
    assert 'Give the product an ID' in got['noId']['productId']
    assert got['editIgnoresTheId'] == {}, 'an edit cannot change the id, so it is not checked'


def test_the_product_draft_reads_back_as_the_server_reads_it_and_says_what_moved():
    got = _node(_products() + '''
var p = { productId: 'x', name: 'Probe', ticker: '—', assetClass: 'Equity', style: 'Active',
          vehicle: 'SMA', shareClass: null, source: 'Internal', liquidity: 'Daily',
          exposureCurrency: 'USD', productCost: 0.2, feeGroup: 'Passive',
          distributionYield: null, minimumInvestment: 5000000 };
var d = prdDraftFrom(p);
var same = prdChanges(p, d);
d.productCost = '0.25%'; d.liquidity = 'Weekly'; d.minimumInvestment = ''; d.name = 'Probe  ';
process.stdout.write(JSON.stringify({
  draft: prdDraftFrom(p), same: same, moved: prdChanges(p, d), payload: prdPayload(d),
  slug: [prdSlug('GS Access Green Bond ETF'), prdSlug('S&P 500  Index (USD)'), prdSlug('  ')],
  near: [prdNear('Dialy', ['Daily', 'Weekly']), prdNear('Mutual fund', ['Mutual Fund']), prdNear('ETF', ['SMA']),
         prdNear('Quaterly', ['Daily', 'Quarterly'])],
  buckets: [catEditedBucket('2026-10-15T09:00:00', Date.parse('2026-10-16T09:00:00')),
            catEditedBucket('2026-09-20T09:00:00', Date.parse('2026-10-16T09:00:00')),
            catEditedBucket('2026-08-01T09:00:00', Date.parse('2026-10-16T09:00:00')),
            catEditedBucket('2025-01-01T09:00:00', Date.parse('2026-10-16T09:00:00')),
            catEditedBucket('', Date.parse('2026-10-16T09:00:00'))]
}));
''')
    assert got['draft']['ticker'] == '' and got['draft']['shareClass'] == '' and got['draft']['minimumInvestment'] == '5000000'
    assert got['same'] == []
    assert [(c['key'], c['before'], c['after']) for c in got['moved']] == [
        ('liquidity', 'Daily', 'Weekly'), ('productCost', '0.2', '0.25'),
        ('minimumInvestment', '5,000,000', '—')]
    assert got['payload']['productCost'] == 0.25 and got['payload']['minimumInvestment'] is None
    assert got['payload']['name'] == 'Probe' and got['payload']['distributionYield'] is None
    assert got['slug'] == ['gs-access-green-bond-etf', 's-and-p-500-index-usd', '']
    assert got['near'] == ['Daily', 'Mutual Fund', None, 'Quarterly']
    assert got['buckets'] == ['Last 7 days', '8 to 30 days ago', '31 to 90 days ago', 'Over 90 days ago',
                              'Over 90 days ago']


CARD = {
    'tiers': [{'id': 'T1', 'min': 0, 'max': 10000000}, {'id': 'T2', 'min': 10000000, 'max': None}],
    'feeGroups': ['Passive', 'Core Active'],
    'rates': {
        'CASP': {'': {'T1': {'Management Floor': 0.4, 'Management Target': 0.5, 'Management Ceiling': 0.6},
                      'T2': {'Management Floor': 0.3, 'Management Target': 0.4, 'Management Ceiling': 0.5}}},
        'RDR': {'Passive': {'T1': {'Management Floor': 0.1, 'Management Target': 0.2, 'Management Ceiling': 0.3},
                            'T2': {'Management Floor': 0.1, 'Management Target': 0.15, 'Management Ceiling': 0.2}},
                'Core Active': {'T1': {'Management Floor': 0.3, 'Management Target': 0.4, 'Management Ceiling': 0.5},
                                'T2': {'Management Floor': 0.2, 'Management Target': 0.3, 'Management Ceiling': 0.4}}}},
}
FEECTX = {'schedules': [{'id': 'CASP', 'byGroup': False}, {'id': 'RDR', 'byGroup': True}],
          'sources': ['Management'], 'points': ['Floor', 'Target', 'Ceiling'],
          'levels': ['Management Floor', 'Management Target', 'Management Ceiling'],
          'rateMax': 10, 'groupMax': 40}


def test_the_card_editor_checks_and_describes_a_change_as_the_server_does():
    got = _node(_fees() + '''
var card = %s, ctx = %s;
var d = feeDraftFrom(card);
var untouched = { problems: feeProblems(d, ctx), changes: feeChanges(card, d, ctx) };
d.rates.RDR['g:Core Active'].T1['Management Target'] = '0.45';
d.rates.RDR['g:Passive'].T2['Management Floor'] = '0.3';
d.rates.CASP[''].T1['Management Ceiling'] = '45';
var bad = feeProblems(d, ctx);
d.rates.RDR['g:Passive'].T2['Management Floor'] = '0.1';
d.rates.CASP[''].T1['Management Ceiling'] = '0.6';
d.feeGroups[0].name = 'Index';
var key = feeAddGroup(d, 'Thematic', 'g:Core Active', ctx.schedules, ctx.levels);
feeSplitTier(d, 1);
var after = { problems: feeProblems(d, ctx), changes: feeChanges(card, d, ctx), payload: feePayload(d, ctx.schedules, ctx.levels),
              tiers: d.tiers.map(function (t) { return feeTierLabel(t.min, t.max); }),
              old: feeOldRate(card, d, 'RDR', 'g:Passive', 'T1', 'Management Target'),
              fresh: feeOldRate(card, d, 'RDR', key, 'T1', 'Management Target') === undefined };
feeMergeTier(d, 1);
after.merged = d.tiers.map(function (t) { return feeTierLabel(t.min, t.max); });
feeSetEdge(d, 0, 12000000);
after.moved = feeChanges(card, d, ctx).lines.filter(function (l) { return l.indexOf('Tiers:') === 0; });
d.feeGroups[1].name = 'index';
after.twice = feeProblems(d, ctx).map(function (p) { return p.message; });
feeRemoveGroup(d, key);
after.removed = Object.keys(d.rates.RDR);
process.stdout.write(JSON.stringify({ untouched: untouched, bad: bad, after: after,
  labels: [feeTierLabel(0, 10000000), feeTierLabel(10000000, 25000000), feeTierLabel(100000000, null),
           feeTierLabel(0, null), feeTierLabel(17500000, 25000000)],
  nums: [feeNum('0.45'), feeNum(' 0.5%% '), feeNum(''), feeNum('a'), feeNum('1e2')].map(function (v) {
    return v === null ? 'blank' : isNaN(v) ? 'nan' : v; }) }));
''' % (json.dumps(CARD), json.dumps(FEECTX)))
    assert got['untouched'] == {'problems': [], 'changes': {'lines': [], 'rates': 0, 'structure': 0}}
    messages = [p['message'] for p in got['bad']]
    assert 'RDR · Passive · $10m and up · Management: Floor 0.30 is above Target 0.15.' in messages
    assert any(m.startswith('CASP · Under $10m · Management Ceiling: 45.00 is not a rate') for m in messages)
    after = got['after']
    assert after['problems'] == []
    lines = after['changes']['lines']
    assert lines[0] == 'Tiers: Under $10m · $10m and up → Under $10m · $10m – $20m · $20m and up'
    assert 'Renamed fee group Passive to Index' in lines and 'Added fee group Thematic' in lines
    assert 'RDR · Core Active · Under $10m · Management Target 0.40 → 0.45' in lines
    assert after['changes']['rates'] == 1 and after['changes']['structure'] == 3
    card = after['payload']['card']
    assert card['feeGroups'] == ['Index', 'Core Active', 'Thematic']
    assert set(card['rates']['RDR']) == {'Index', 'Core Active', 'Thematic'}
    assert card['rates']['RDR']['Thematic']['T1']['Management Target'] == 0.45, 'copied from Core Active as typed'
    assert [t['id'] for t in card['tiers']] == ['T1', 'T2', 'n2'] and card['tiers'][1]['max'] == 20000000
    assert after['payload']['renames'] == [['Passive', 'Index']]
    assert after['tiers'] == ['Under $10m', '$10m – $20m', '$20m and up']
    assert after['old'] == 0.2 and after['fresh']
    assert after['merged'] == ['Under $10m', '$10m and up']
    assert after['moved'] == ['Tiers: Under $10m · $10m and up → Under $12m · $12m and up']
    assert 'There are two fee groups called index.' in after['twice']
    assert after['removed'] == ['g:Passive', 'g:Core Active']
    assert got['labels'] == ['Under $10m', '$10m – $25m', '$100m and up', 'Every account size', '$17.5m – $25m']
    assert got['nums'] == [0.45, 0.5, 'blank', 'nan', 'nan']


def test_a_block_copied_in_excel_pastes_into_a_group_card():
    """A group's rates copied in Excel - headings and tier names with them,
    rates as percentages - fill its card; a block that is not all rates, too
    big, or turned round is refused whole, and nothing is written."""
    got = _node(_fees() + r'''
var card = %s, ctx = %s;
var d = feeDraftFrom(card), out = {};
var copied = '\tManagement\t\t\r\nTier\tFloor\tTarget\tCeiling\r\nUnder $10m\t0.35%%\t0.45%%\t0.55%%\r\n$10m and up\t0.20\t0.30\t0.40\r\n';
var block = feeReadBlock(copied);
out.cells = block.cells;
out.whole = feePasteBlock(d, 'RDR', 'g:Core Active', ctx.levels, block.cells, { tier: 1, level: 2 });
out.after = d.rates.RDR['g:Core Active'];
out.changes = feeChanges(card, d, ctx).lines;
out.part = feePasteBlock(d, 'RDR', 'g:Passive', ctx.levels, feeReadBlock('0.25\t0.35').cells, { tier: 1, level: 1 });
out.passive = d.rates.RDR['g:Passive'].T2;
var before = JSON.stringify(d);
out.big = feePasteBlock(d, 'RDR', 'g:Passive', ctx.levels, feeReadBlock('0.1\t0.2\n0.1\t0.2\n0.1\t0.2\n0.1\t0.2').cells, { tier: 1, level: 0 }).error;
out.turned = feePasteBlock(d, 'CASP', '', ctx.levels, feeReadBlock('0.1\t0.2\n0.1\t0.2\n0.1\t0.2').cells, { tier: 0, level: 0 }).error;
out.untouched = JSON.stringify(d) === before;
out.word = feeReadBlock('0.1\t0.2\n0.3\tn/a').error;
out.gap = feeReadBlock('0.1\t\t0.3').error;
out.empty = feeReadBlock('Floor\tTarget\n').error;
process.stdout.write(JSON.stringify(out));
''' % (json.dumps(CARD), json.dumps(FEECTX)))
    assert got['cells'] == [['0.35', '0.45', '0.55'], ['0.2', '0.3', '0.4']], 'headings, tier names and % taken off'
    assert got['whole'] == {'count': 6, 'changed': 3, 'whole': True}, 'the card shape fills the card wherever it lands'
    assert got['after']['T1'] == {'Management Floor': '0.35', 'Management Target': '0.45', 'Management Ceiling': '0.55'}
    assert 'RDR · Core Active · Under $10m · Management Floor 0.30 → 0.35' in got['changes']
    assert got['part'] == {'count': 2, 'changed': 2, 'whole': False}
    assert got['passive'] == {'Management Floor': '0.1', 'Management Target': '0.25', 'Management Ceiling': '0.35'}, 'from the box on'
    assert got['big'] == 'The block is 4 rows by 2 columns; from that box the card has room for 1 by 3. Nothing was pasted.'
    assert got['turned'].startswith('The block is 3 rows by 2 columns: its tiers run across.')
    assert got['untouched'], 'a refused block writes nothing'
    assert got['word'] == 'Row 2, column 2 of the block is "n/a", not a rate. Nothing was pasted.'
    assert got['gap'] == 'Row 1, column 2 of the block is empty. Nothing was pasted.'
    assert got['empty'].startswith('The clipboard holds no rates.')


def test_the_paste_is_caught_where_it_lands_and_the_layout_is_remembered():
    source = _source()
    assert "document.addEventListener('paste', function (e) {" in source
    assert 'feePasteInto(el.dataset.feecell, text);' in source
    assert "if (!/[\\t\\n]/.test(" in source, 'one figure pastes into its box as typed'
    assert "window.localStorage.setItem(FEE_LAYOUT_KEY, fct.layout)" in source


def test_the_server_and_the_page_label_tiers_and_read_a_change_alike():
    """The words the review shows are the words the history keeps."""
    import copy
    from cyrus_pmg.pmgService.scenario import fees
    for low, high in ((0, 10e6), (10e6, 25e6), (100e6, None), (0, None), (17.5e6, 25e6)):
        js = _node(_fees() + 'process.stdout.write(JSON.stringify(feeTierLabel(%s, %s)));' % (
            json.dumps(low), 'null' if high is None else json.dumps(high)))
        assert js == fees.tierLabel(low, high)


def test_an_open_form_or_card_is_not_lost_to_a_stray_move():
    source = _source()
    assert "if (repo.view === 'catalogue' && prdDirty()) { prd.leaving = true;" in source
    assert "if (repo.view === 'fees' && feeDirty())" in source
    assert "if (!force && prdDirty())" in source and "if (!force && feeDirty())" in source
    assert "if (prd.mode) { if (prdDirty()) { prd.leaving = true;" in source, 'another row waits'
    assert "!prd.mode && !imp && !e.target.closest('.cat-detail, .imp-dlg')" in source, 'a stray click spares a form'
    # the new dialogs hear Escape before the console's own handler
    assert source.index("if (repo.view === 'catalogue' && prd.mode) {") < source.index('/* ---- events ----')
    assert 'e.stopImmediatePropagation()' in source


def test_the_form_holds_a_ticker_and_a_minimum_per_currency_as_the_server_does():
    """D180: the boxes by base currency are flat in the draft, a table by
    currency on the wire with only what was typed, checked as the server
    checks them and named in what moved by their currency."""
    got = _node(_products() + '''
var p = { productId: 'x', name: 'Probe', ticker: 'MAIN', assetClass: 'Equity', style: 'Active',
          vehicle: 'SMA', shareClass: null, source: 'Internal', liquidity: 'Daily',
          exposureCurrency: 'USD', productCost: 0.2, feeGroup: 'Passive',
          distributionYield: null, minimumInvestment: 5000000,
          currencies: { GBP: { ticker: 'GBX1', minimumInvestment: 250000 } } };
var d = prdDraftFrom(p), ctx = %s;
var before = { payload: prdPayload(d), same: prdChanges(p, d), draft: [d.tickerGBP, d.minimumInvestmentGBP, d.tickerUSD] };
d.tickerEUR = 'E1'; d.minimumInvestmentGBP = ''; d.minimumInvestmentCHF = '0';
var moved = prdChanges(p, d), payload = prdPayload(d);
var bad = prdProblems(Object.assign({}, d, { minimumInvestmentUSD: 'lots', tickerCHF: 'X'.repeat(30) }), ctx);
process.stdout.write(JSON.stringify({ before: before, moved: moved, payload: payload, bad: bad }));
''' % json.dumps(dict(CTX, isNew=False)))
    assert got['before']['draft'] == ['GBX1', '250000', '']
    assert got['before']['same'] == []
    assert got['before']['payload']['currencies'] == {'GBP': {'ticker': 'GBX1', 'minimumInvestment': 250000}}
    assert [(c['key'], c['label'], c['before'], c['after']) for c in got['moved']] == [
        ('tickerEUR', 'Ticker in EUR', '—', 'E1'),
        ('minimumInvestmentGBP', 'Minimum investment in GBP', '250,000', '—'),
        ('minimumInvestmentCHF', 'Minimum investment in CHF', '—', '0')]
    assert got['payload']['currencies'] == {'GBP': {'ticker': 'GBX1'}, 'EUR': {'ticker': 'E1'},
                                            'CHF': {'minimumInvestment': 0}}
    assert sorted(got['bad']) == ['minimumInvestmentUSD', 'tickerCHF']
    assert got['bad']['minimumInvestmentUSD'].startswith('Minimum investment in USD')
    assert 'ticker in CHF' in got['bad']['tickerCHF']


def test_the_form_holds_an_exposure_and_whether_it_is_offered_per_currency():
    """D182: a currency's exposure is a box like its ticker; Not offered
    sends that alone, whatever its other boxes hold, and reads in what moved
    as yes and no."""
    got = _node(_products() + '''
var p = { productId: 'x', name: 'Probe', ticker: 'MAIN', assetClass: 'Equity', style: 'Active',
          vehicle: 'SMA', shareClass: null, source: 'Internal', liquidity: 'Daily',
          exposureCurrency: 'USD', productCost: 0.2, feeGroup: 'Passive',
          distributionYield: null, minimumInvestment: 5000000,
          currencies: { GBP: { ticker: 'GBX1', minimumInvestment: null, exposureCurrency: 'GBP', offered: true },
                        CHF: { ticker: '', minimumInvestment: null, exposureCurrency: '', offered: false } } };
var d = prdDraftFrom(p), ctx = %s;
var before = { draft: [d.exposureCurrencyGBP, d.offeredGBP, d.offeredCHF], same: prdChanges(p, d),
               payload: prdPayload(d) };
d.offeredGBP = 'no'; d.offeredCHF = ''; d.tickerCHF = 'C1'; d.exposureCurrencyEUR = 'EUR';
var moved = prdChanges(p, d), payload = prdPayload(d);
var bad = prdProblems(Object.assign({}, d, { exposureCurrencyUSD: 'X'.repeat(61), tickerGBP: 'X'.repeat(30) }), ctx);
process.stdout.write(JSON.stringify({ before: before, moved: moved, payload: payload, bad: bad }));
''' % json.dumps(dict(CTX, isNew=False)))
    assert got['before']['draft'] == ['GBP', '', 'no']
    assert got['before']['same'] == []
    assert got['before']['payload']['currencies'] == {
        'GBP': {'ticker': 'GBX1', 'exposureCurrency': 'GBP'}, 'CHF': {'offered': False}}
    assert [(c['key'], c['before'], c['after']) for c in got['moved']] == [
        ('tickerGBP', 'GBX1', '—'), ('tickerCHF', '—', 'C1'),
        ('exposureCurrencyGBP', 'GBP', '—'), ('exposureCurrencyEUR', '—', 'EUR'),
        ('offeredGBP', 'yes', 'no'), ('offeredCHF', 'no', 'yes')]
    assert got['payload']['currencies'] == {'GBP': {'offered': False}, 'CHF': {'ticker': 'C1'},
                                            'EUR': {'exposureCurrency': 'EUR'}}
    assert list(got['bad']) == ['exposureCurrencyUSD'], 'a currency not offered is not checked'


def test_the_form_holds_the_issue_currency_main_and_per_currency():
    """D184: the issue currency is an optional box of its own, and a box in
    each base currency like the exposure, sent only where it is typed."""
    got = _node(_products() + '''
var p = { productId: 'x', name: 'Probe', ticker: 'MAIN', assetClass: 'Equity', style: 'Active',
          vehicle: 'SMA', shareClass: null, source: 'Internal', liquidity: 'Daily',
          exposureCurrency: 'USD', issueCurrency: 'USD', productCost: 0.2, feeGroup: 'Passive',
          distributionYield: null, minimumInvestment: 5000000,
          currencies: { GBP: { ticker: '', minimumInvestment: null, exposureCurrency: '',
                               issueCurrency: 'GBP', offered: true } } };
var d = prdDraftFrom(p), ctx = %s;
var before = { draft: [d.issueCurrency, d.issueCurrencyGBP], same: prdChanges(p, d) };
d.issueCurrency = ''; d.issueCurrencyEUR = 'EUR';
var moved = prdChanges(p, d), payload = prdPayload(d);
var bad = prdProblems(Object.assign({}, d, { issueCurrency: 'X'.repeat(61), issueCurrencyCHF: 'X'.repeat(61) }), ctx);
process.stdout.write(JSON.stringify({ before: before, moved: moved, payload: payload, bad: bad,
                                      blank: prdProblems(d, ctx) }));
''' % json.dumps(dict(CTX, isNew=False)))
    assert got['before']['draft'] == ['USD', 'GBP'] and got['before']['same'] == []
    assert [(c['key'], c['before'], c['after']) for c in got['moved']] == [
        ('issueCurrency', 'USD', '—'), ('issueCurrencyEUR', '—', 'EUR')]
    assert got['payload']['issueCurrency'] == ''
    assert got['payload']['currencies'] == {'GBP': {'issueCurrency': 'GBP'}, 'EUR': {'issueCurrency': 'EUR'}}
    assert sorted(got['bad']) == ['issueCurrency', 'issueCurrencyCHF']
    assert 'issueCurrency' not in got['blank'], 'it is optional'


def test_the_form_gives_each_currency_a_share_class_of_its_own():
    """D189: a currency's share class is a choice, blank for the main one,
    checked as the server checks it and sent in the currencies table."""
    got = _node(_products() + '''
var p = { productId: 'x', name: 'Probe', ticker: 'PRB', assetClass: 'Equity', style: 'Active',
          vehicle: 'SMA', shareClass: 'Distributing', source: 'Internal', liquidity: 'Daily',
          exposureCurrency: 'USD', productCost: 0.2, feeGroup: 'Passive',
          distributionYield: null, minimumInvestment: null,
          currencies: { GBP: { ticker: '', minimumInvestment: null, exposureCurrency: '', issueCurrency: '',
                               shareClass: 'Accumulating', offered: true } } };
var d = prdDraftFrom(p);
var out = { gbp: d.shareClassGBP, eur: d.shareClassEUR, same: prdChanges(p, d) };
d.shareClassEUR = 'Accumulating'; d.shareClassGBP = '';
out.payload = prdPayload(d).currencies;
out.moved = prdChanges(p, d).map(function (c) { return [c.key, c.label, c.before, c.after]; });
d.shareClassCHF = 'Income';
out.problems = prdProblems(d, { isNew: false, feeGroups: ['Passive'] });
process.stdout.write(JSON.stringify(out));
''')
    assert got['gbp'] == 'Accumulating' and got['eur'] == '' and got['same'] == []
    assert got['payload'] == {'EUR': {'shareClass': 'Accumulating'}}
    assert got['moved'] == [['shareClassGBP', 'Share class in GBP', 'Accumulating', '—'],
                            ['shareClassEUR', 'Share class in EUR', '—', 'Accumulating']]
    assert got['problems'] == {'shareClassCHF': 'The share class in CHF must be Distributing or '
                                                'Accumulating, or the main one.'}
