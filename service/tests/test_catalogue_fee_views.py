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
        'style': 'Passive', 'vehicle': 'ETF', 'income': 'Accumulating', 'shareClass': '', 'source': 'Internal',
        'liquidity': 'Daily', 'exposureCurrency': 'USD', 'productCost': 0.12,
        'feeGroup': 'Passive', 'distributionYield': None, 'minimumInvestment': None}
#: what the product is (D218); the rest is its one share class's, or its one
#: listing's
PRODUCT = ('productId', 'name', 'provider', 'category', 'assetClass', 'vehicle', 'style', 'source', 'liquidity',
           'feeGroup')
PLACE = ('exchange', 'ticker', 'issueCurrency', 'quotationUnit', 'code')


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
        'income': {'income': 'Distribution'},
        'letter': {'shareClass': 'A'},
        'letterMf': {'shareClass': 'A', 'vehicle': 'Mutual Fund'},
        'word': {'style': ''},
        'noProvider': {'provider': ''},
        'vehicle': {'vehicle': 'Unit Trust'},
        'ucits': {'vehicle': 'UCITS'},
        'minimum': {'minimumInvestment': '5m'},
        'commas': {'minimumInvestment': '5,000,000'},
        'isin': {'isin': 'IE00B4L5Y98'},
        'isinOk': {'isin': 'ie00b4l5y983'},
        'domicile': {'domicile': 'Ireland'},
        'ccy': {'classCurrency': 'Sterling'},
        'hedgedElsewhere': {'hedging': 'Hedged', 'classCurrency': 'GBP', 'exposureCurrency': 'USD'},
        'hedgedFilled': {'hedging': 'Hedged', 'classCurrency': 'GBP', 'exposureCurrency': ''},
        'replication': {'replication': 'Full'},
        'uk': {'ukReporting': 'Maybe'},
        'exchange': {'exchange': 'x' * 41},
        'unit': {'quotationUnit': 'pence sterling'},
        'taken': {'productId': 'gs-taken'},
        'gone': {'productId': 'gs-gone'},
        'noId': {'productId': ''},
    }
    got = _node(_products() + '''
var good = prdDraftFrom(%s), ctx = %s, cases = %s, product = %s, place = %s, out = {};
function probe(change) {
  var d = JSON.parse(JSON.stringify(good));
  Object.keys(change).forEach(function (k) {
    if (product.indexOf(k) !== -1) d[k] = change[k];
    else if (place.indexOf(k) !== -1) d.listings[0][k] = change[k];
    else d.classes[0][k] = change[k];
  });
  return d;
}
Object.keys(cases).forEach(function (k) { out[k] = prdProblems(probe(cases[k]), ctx); });
out.editIgnoresTheId = prdProblems(probe({ productId: 'gs-taken' }), Object.assign({}, ctx, { isNew: false }));
process.stdout.write(JSON.stringify(out));
''' % (json.dumps(GOOD), json.dumps(CTX), json.dumps(cases), json.dumps(list(PRODUCT)), json.dumps(list(PLACE))))
    assert got['good'] == {} and got['pctSign'] == {} and got['commas'] == {}
    assert got['noProvider'] == {} and got['isinOk'] == {} and got['hedgedFilled'] == {}, 'all optional (Q3)'
    assert got['letterMf'] == {} and got['ucits'] == {}
    assert list(got['noName']) == ['name']
    assert 'not a number' in got['freeCost']['class0productCost']
    assert 'cannot be negative' in got['negative']['class0productCost']
    assert 'looks wrong' in got['slip']['class0productCost']
    assert 'not on the fee card' in got['group']['feeGroup']
    assert 'Choose a fee group' in got['noGroup']['feeGroup']
    assert list(got['income']) == ['class0income']
    assert 'Only a Mutual Fund' in got['letter']['class0shareClass']
    assert got['word'] == {'style': 'Style is needed.'}
    assert 'ETF, Mutual Fund or SMA' in got['vehicle']['vehicle']
    assert 'not a number' in got['minimum']['class0minimumInvestment']
    assert 'is not one' in got['isin']['class0isin']
    assert 'two-letter country code' in got['domicile']['class0domicile']
    assert 'three letters' in got['ccy']['class0classCurrency']
    assert 'gives GBP exposure, not USD' in got['hedgedElsewhere']['class0exposureCurrency']
    assert list(got['replication']) == ['class0replication'] and list(got['uk']) == ['class0ukReporting']
    assert list(got['exchange']) == ['listing0exchange'] and list(got['unit']) == ['listing0quotationUnit']
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
d.classes[0].productCost = '0.25%'; d.liquidity = 'Weekly'; d.classes[0].minimumInvestment = ''; d.name = 'Probe  ';
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
    cls, one = got['draft']['classes'][0], got['draft']['listings'][0]
    assert one['ticker'] == '' and cls['shareClass'] == '' and cls['minimumInvestment'] == '5000000'
    assert one['cls'] == cls['uid'], 'a listing points at its class'
    assert len(got['draft']['listings']) == 1 and len(got['draft']['classes']) == 1, \
        'a product served without listings is its one'
    assert got['same'] == []
    assert [(c['key'], c['before'], c['after']) for c in got['moved']] == [
        ('liquidity', 'Daily', 'Weekly'), ('productCost', '0.2', '0.25'),
        ('minimumInvestment', '5,000,000', '—')]
    listing = got['payload']['listings'][0]
    assert listing['productCost'] == 0.25 and listing['minimumInvestment'] is None
    assert got['payload']['name'] == 'Probe' and listing['distributionYield'] is None
    assert 'ranks' not in listing, 'a listing is facts only (D214)'
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


LISTED = {'productId': 'gef', 'name': 'Global Equity Fund', 'ticker': 'GEF-USD', 'assetClass': 'Equity',
          'style': 'Active', 'vehicle': 'Mutual Fund', 'income': 'Accumulating', 'source': 'Internal',
          'liquidity': 'Daily', 'exposureCurrency': 'USD', 'productCost': 0.45, 'feeGroup': 'Passive',
          'distributionYield': None, 'minimumInvestment': 1000, 'category': '', 'provider': 'GSAM',
          'listings': [
              {'ticker': 'GEF-USD', 'code': '', 'issueCurrency': 'USD', 'hedging': 'Unhedged', 'isin': '',
               'income': 'Accumulating', 'shareClass': 'I', 'exposureCurrency': 'USD', 'productCost': 0.45,
               'distributionYield': None, 'minimumInvestment': 1000, 'benchmark': 'MSCI World'},
              {'ticker': 'GEF-USD', 'code': '', 'issueCurrency': 'GBP', 'hedging': 'Unhedged', 'isin': '',
               'income': 'Accumulating', 'shareClass': 'I', 'exposureCurrency': 'USD', 'productCost': 0.45,
               'distributionYield': None, 'minimumInvestment': 1000, 'benchmark': 'MSCI World'},
              {'ticker': 'GEF-GBPH', 'code': '', 'issueCurrency': 'GBP', 'hedging': 'Hedged', 'isin': '',
               'classCurrency': 'GBP', 'income': 'Accumulating', 'shareClass': 'I', 'exposureCurrency': 'GBP',
               'productCost': 0.5, 'distributionYield': None, 'minimumInvestment': 1000000,
               'benchmark': 'MSCI World (GBP hedged)'}]}


def test_the_form_holds_a_products_classes_and_listings_in_their_order_as_the_server_does():
    """D207, D214, D218: the draft is the product's share classes and its
    listings, each pointing at its class, in their order; the payload sends
    the listings whole, each with its class's values, a class's in its
    class's place; what moved is named by listing, as the history names it -
    the order too, where another class is put first. A refusal the server
    names on a listing's class field lands on the class's box."""
    got = _node(_products() + '''
var p = %s, d = prdDraftFrom(p), ctx = %s;
var before = { classes: d.classes.length, rows: d.listings.length, same: prdChanges(p, d),
               of: d.listings.map(function (l) { return prdClassIndex(d, l.cls); }),
               labels: p.listings.map(function (l) { return prdListingLabel(l, p.listings); }),
               names: d.classes.map(prdClassName) };
d.classes[1].productCost = '0.55';
var eur = prdClassRow(p.listings[0]); eur.hedging = 'Hedged';
d.classes.push(eur);
d.listings.push(Object.assign(prdPlaceRow(null, eur.uid), { ticker: 'GEF-EURH', issueCurrency: 'EUR' }));
var moved = prdChanges(p, d), payload = prdPayload(d);
var d2 = prdDraftFrom(p);
d2.classes.unshift(d2.classes.splice(1, 1)[0]); prdNormalise(d2);
var d3 = prdDraftFrom(p);
d3.listings.push(prdPlaceRow({ issueCurrency: 'CHF' }, d3.classes[0].uid)); prdNormalise(d3);
process.stdout.write(JSON.stringify({ before: before, moved: moved, payload: payload,
  fields: prdBoxFields(d).length, reordered: prdChanges(p, d2), firstNow: prdPayload(d2).listings[0].ticker,
  added: d3.listings.map(function (l) { return l.issueCurrency; }),
  boxes: [prdBoxOf(d, 'listing2productCost'), prdBoxOf(d, 'listing2ticker'), prdBoxOf(d, 'listing1isin'), prdBoxOf(d, 'vehicle')],
  orders: [prdClassOrder(p.listings, false, 1, 0), prdClassOrder(p.listings, false, 1, 0, 0),
           prdClassOrder(p.listings, false, 0, 0), prdClassOrder(p.listings, true, 2, 0)] }));
''' % (json.dumps(LISTED), json.dumps(dict(CTX, isNew=False))))
    assert (got['before']['classes'], got['before']['rows']) == (2, 3), 'two listings agree on every class value'
    assert got['before']['of'] == [0, 0, 1] and got['before']['same'] == []
    assert got['before']['labels'] == ['GEF-USD · USD', 'GEF-USD · GBP', 'GEF-GBPH']
    assert got['before']['names'] == ['I Acc · class 1', 'I Acc hedged · class 2']
    assert [(c['label'], c['before'], c['after']) for c in got['moved']] == [
        ('Product cost of GEF-GBPH', '0.5', '0.55'),
        ('Listing GEF-EURH', '—', 'EUR · Hedged · Accumulating · class I · exposure USD · cost 0.45 · min 1,000 '
                                  '· benchmark MSCI World')]
    listings = got['payload']['listings']
    assert not any('ranks' in l for l in listings) and not any('assetClass' in l for l in listings)
    assert [l['ticker'] for l in listings] == ['GEF-USD', 'GEF-USD', 'GEF-GBPH', 'GEF-EURH']
    assert listings[2]['productCost'] == 0.55 and listings[1]['shareClass'] == 'I' and listings[1]['issueCurrency'] == 'GBP'
    assert got['payload']['assetClass'] == 'Equity' and got['payload']['provider'] == 'GSAM', 'the product\'s own'
    assert got['fields'] == 10 + 3 * 15 + 4 * 6, 'the product, a class\'s fourteen boxes and a listing\'s five'
    assert [(c['label'], c['before'], c['after']) for c in got['reordered']] == [
        ('Listing order', 'GEF-USD · USD, then GEF-USD · GBP, then GEF-GBPH',
         'GEF-GBPH, then GEF-USD · USD, then GEF-USD · GBP')]
    assert got['firstNow'] == 'GEF-GBPH', 'a class moves with its listings'
    assert got['added'] == ['USD', 'GBP', 'CHF', 'GBP'], 'a listing stands with its class\'s'
    assert got['boxes'] == ['class1productCost', 'listing2ticker', 'class0isin', 'vehicle']
    assert got['orders'] == [[2, 0, 1], [1, 0, 2], None, [2, 0, 1]], 'a card\'s class, a listing in its class, an SMA\'s code'


def test_the_form_checks_classes_and_listings_as_the_server_does():
    """The same listing twice, a code on a fund, hedging a fund cannot say, a
    ticker or an ISIN another product holds, one ISIN on two classes, two
    classes the same, a class with no listing, and a fund's box on an SMA
    are each refused on their box (D207, D218)."""
    got = _node(_products() + '''
var p = %s, ctx = %s, out = {};
function draft(change) { var d = prdDraftFrom(p); change(d); return prdProblems(d, ctx); }
function sma(change) { var d = prdDraftFrom(p); d.vehicle = 'SMA'; d.category = 'Public Equity'; prdSplitClasses(d); change(d); return prdProblems(d, ctx); }
out.same = draft(function (d) { d.listings[1].issueCurrency = 'USD'; });
out.otherExchange = draft(function (d) { d.listings[1].issueCurrency = 'USD'; d.listings[1].exchange = 'LSE'; });
out.code = draft(function (d) { d.listings[0].code = 'M1'; });
out.isg = draft(function (d) { d.classes[0].hedging = 'ISG Hedged'; });
out.held = draft(function (d) { d.listings[2].ticker = 'TAKEN'; });
out.heldIsin = draft(function (d) { d.classes[0].isin = 'IE00B4L5Y983'; });
out.twoIsins = draft(function (d) { d.classes[0].isin = 'LU0000000011'; d.classes[1].isin = 'LU0000000011'; });
out.twins = draft(function (d) { d.classes[1].hedging = 'Unhedged'; d.classes[1].classCurrency = ''; d.classes[1].exposureCurrency = 'USD';
  d.classes[1].productCost = '0.45'; d.classes[1].minimumInvestment = '1000'; d.classes[1].benchmark = 'MSCI World'; });
out.bare = draft(function (d) { d.listings = d.listings.filter(function (l) { return l.cls !== d.classes[1].uid; }); });
out.etfLetter = draft(function (d) { d.vehicle = 'ETF'; });
out.split = (function () { var d = prdDraftFrom(p); d.vehicle = 'SMA'; prdSplitClasses(d);
  return [d.classes.length, d.listings.map(function (l) { return prdClassIndex(d, l.cls); })]; })();
out.smaIsg = sma(function (d) { d.classes[0].hedging = 'ISG Hedged'; d.listings[0].code = 'M34';
  d.classes.forEach(function (c) { c.shareClass = ''; c.classCurrency = ''; }); });
out.smaLeft = sma(function (d) { d.classes.forEach(function (c) { c.shareClass = ''; }); });
out.smaFund = sma(function (d) { d.classes[0].domicile = 'IE'; d.listings[1].exchange = 'LSE';
  d.classes.forEach(function (c) { c.shareClass = ''; }); });
out.bondHedged = (function () { var d = prdDraftFrom(p); d.vehicle = 'SMA'; d.category = 'Investment Grade Fixed Income'; prdSplitClasses(d);
  d.classes.forEach(function (c) { c.shareClass = ''; }); return prdProblems(d, ctx); })();
process.stdout.write(JSON.stringify(out));
''' % (json.dumps(LISTED), json.dumps(dict(CTX, isNew=False, categories=['Public Equity', 'Investment Grade Fixed Income'],
                                             held={'ticker:taken': 'Another Fund', 'isin:ie00b4l5y983': 'An ETF'}))))
    assert 'same listing' in got['same']['listing1issueCurrency']
    assert 'listing1issueCurrency' not in got['otherExchange'], 'another exchange is another listing'
    assert 'Only a separate account' in got['code']['listing0code']
    assert 'Hedged or Unhedged' in got['isg']['class0hedging']
    assert 'Another Fund' in got['held']['listing2ticker']
    assert 'An ETF' in got['heldIsin']['class0isin']
    assert 'Classes 1 and 2 have the same ISIN' in got['twoIsins']['class1isin']
    assert 'Classes 1 and 2 are the same share class' in got['twins']['class1']
    assert 'Class 2 has no listing' in got['bare']['class1']
    assert 'Only a Mutual Fund' in got['etfLetter']['class0shareClass']
    assert got['split'] == [3, [0, 1, 2]], 'an SMA\'s codes are a class each'
    assert got['smaIsg'] == {}
    assert 'no class currency' in got['smaLeft']['class2classCurrency'], 'a fund\'s value left on an SMA is shown to clear'
    assert 'no domicile' in got['smaFund']['class0domicile'] and 'no exchange' in got['smaFund']['listing1exchange']
    assert 'Only a Public Equity SMA' in got['bondHedged']['class0hedging']


def test_the_card_and_the_form_follow_the_vehicle_and_the_listings_move():
    """D207, D218: the template is the vehicle's - an ETF's, a Mutual Fund's,
    or an SMA's, whose rows are its mandate codes - and a class or a listing
    moves by its grip or its arrows, taken off by its x. On the card too
    (D214), both tables, where the order is saved as it moves."""
    source = _source()
    assert 'data-prdcgrip' in source and 'data-prdkgrip' in source and "'cardmove'" in source and "'clsmove'" in source
    assert "'/scenario/repository/products/order'" in source and 'Rank USD' not in source
    for kind in ("'ETF card'", "'Mutual Fund card'", "'SMA card'", "'SMA form'", "'ETF form'", "'Mutual Fund form'"):
        assert kind in source, kind
    assert "act === 'lstremove' && prd.draft" in source and "act === 'lstadd' && prd.draft" in source
    assert "act === 'clsadd' && prd.draft" in source and "act === 'clsremove' && prd.draft" in source
    assert 'data-prdgrip' in source and 'prdMoveClass(from, to) : prdMoveListing(from, to)' in source
    assert 'prdSplitClasses(prd.draft)' in source, 'an SMA chosen splits its classes'
    assert "'importmandates'" not in source and 'mandates.csv' not in source
