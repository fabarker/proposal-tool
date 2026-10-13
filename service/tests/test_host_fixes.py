"""Fixes found on the Cyrus host and carried into the repository (csvText.py,
fees.py, sheetDoc.py), so that a port keeps them instead of overwriting them.

The host's extracts had been saved from Excel - a byte-order mark at the
front - and copied through a tool that wrapped every row in one extra pair of
double quotes. Each reader must read such a copy exactly as it reads the
original. And the assumptions sheet must group the estimates under their own
category headers whatever order the bake recorded them in."""
import json
import os
import random

import pytest

from cyrus_pmg.pmgService.scenario import (assetEstimates, fees, products, sheetDoc, sleeveRepo,
                                           universe)
from cyrus_pmg.pmgService.scenario.csvText import unwrapRows

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
SCENARIO = os.path.join(ROOT, 'service', 'cyrus_pmg', 'pmgService', 'scenario')


def _excelCopy(source, tmp_path):
    """*source* as the host received it: a BOM, CRLF, every row wrapped in
    one extra pair of double quotes."""
    with open(source, encoding='utf-8') as fh:
        lines = fh.read().splitlines()
    assert lines and not any('"' in line for line in lines), 'the test needs an unquoted source'
    target = tmp_path / ('excel-' + os.path.basename(source))
    with open(target, 'w', encoding='utf-8-sig', newline='') as fh:
        fh.write('\r\n'.join('"{}"'.format(line) for line in lines) + '\r\n')
    return str(target)


def test_unwrap_rows_takes_off_only_a_pair_around_the_whole_row():
    lines = ['"a,b,c"\n', 'a,"b, c",d\n', '"x"\r\n', '"a","b"\n', 'plain\n', '  "padded,row"  \n']
    assert list(unwrapRows(lines)) == ['a,b,c\n', 'a,"b, c",d\n', 'x\n', '"a","b"\n', 'plain\n',
                                       'padded,row\n']


def test_the_product_catalogue_reads_an_excel_copy_as_it_reads_the_original(tmp_path):
    source = os.path.join(ROOT, 'productSource', 'products.csv')
    assert list(products._readRows(_excelCopy(source, tmp_path))) == list(products._readRows(source))
    products._load(_excelCopy(source, tmp_path))        # the header is found: no BadCatalogue


def test_the_strategic_universe_reads_an_excel_copy_as_it_reads_the_original(tmp_path):
    source = os.path.join(ROOT, 'saaSource', 'saaPortfolios.csv')
    original = list(universe._readRows(source))
    assert original and list(universe._readRows(_excelCopy(source, tmp_path))) == original


def test_the_rate_card_reads_an_excel_copy_as_it_reads_the_original(tmp_path):
    source = os.path.join(SCENARIO, 'feeRates.csv')
    # a BOM alone used to name the first column '﻿schedule' and fail the
    # column check; the card is not wrapped, so only the BOM is added here
    with open(source, encoding='utf-8') as fh:
        text = fh.read()
    bom = tmp_path / 'bom-feeRates.csv'
    bom.write_text(text, encoding='utf-8-sig')
    assert fees._readDelivery(str(bom)) == fees._readDelivery(source)


def test_the_sleeve_seed_reads_an_excel_copy_as_it_reads_the_original(tmp_path):
    source = os.path.join(ROOT, 'sleeveSource', 'sleeves.csv')
    assert sleeveRepo._tableRows(_excelCopy(source, tmp_path)) == sleeveRepo._tableRows(source)


def _labels(doc):
    """Column A of the assumptions sheet from the body down: category
    headers and asset names, in order."""
    return [row.cells[1].value for number, row in sorted(doc.rows.items())
            if number >= 3 and 1 in row.cells and row.cells[1].value]


def test_the_assumptions_group_each_estimate_under_its_own_category():
    with open(os.path.join(HERE, 'golden', 'engine_USD_Hedged_2col.results.json'), encoding='utf-8') as fh:
        results = json.load(fh)
    assets = assetEstimates.forSlice('USD', 'Hedged')
    assert assets, 'the delivered estimates'
    expected = _labels(sheetDoc.buildAssumptionsDoc(assets, results))
    # the bake may hand them over in any order, categories interleaved
    shuffled = list(assets)
    random.Random(7).shuffle(shuffled)
    assert [a['category'] for a in shuffled] != [a['category'] for a in assets]
    assert _labels(sheetDoc.buildAssumptionsDoc(shuffled, results)) == expected
    # and every asset sits under its own category's header
    categories = {a['category'] for a in assets}
    byName = {a['reportingName']: a['category'] for a in assets}
    current = None
    for label in expected:
        if label in categories and label not in byName:
            current = label
        elif label in byName:
            assert byName[label] == current, label
