"""D163: the copy Cyrus gets reads as Cyrus's own.

Every shipped file goes through tools/cyrusClean.py on its way: its comments
lose the decision numbers, the spec's sections and the other references to
what is only here, and its code is untouched. These tests fail as soon as
something written here would reach Cyrus with a trace - a new reference the
cleaner does not take out, or one in the code itself - so it is reworded when
it is written, not when an update is built.
"""

import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICE = os.path.abspath(os.path.join(HERE, '..'))
ROOT = os.path.dirname(SERVICE)
sys.path.insert(0, os.path.join(SERVICE, 'tools'))

import cyrusClean  # noqa: E402


def _shipped():
    files = subprocess.run(['git', '-C', ROOT, 'ls-files', 'service/cyrus_pmg/pmgService/scenario',
                            'proposalTool'], capture_output=True, text=True, check=True).stdout.split()
    return [f for f in files if f.endswith(cyrusClean.TEXT)]


def test_every_shipped_file_reaches_cyrus_with_no_trace_and_its_code_unchanged():
    problems = []
    for rel in _shipped():
        before = open(os.path.join(ROOT, rel), encoding='utf-8').read()
        problems += ['{}: {}'.format(rel, p)
                     for p in cyrusClean.check(rel, before, cyrusClean.cleanText(rel, before))]
    assert problems == []


def test_the_endpoint_block_reaches_cyrus_with_no_trace():
    lines = open(os.path.join(ROOT, cyrusClean.ROUTER), encoding='utf-8').read().split('\n')
    start, end = cyrusClean.blockRange(lines)
    block = '\n'.join(lines[start:end])
    assert cyrusClean.check('block.py', block, cyrusClean.cleanText('block.py', block)) == []


def test_only_comments_change():
    """A reference is taken out of a comment and a docstring, never out of code."""
    source = ('def f():\n'
              '    """Resolve it (spec 3.4, D12)."""\n'
              "    return 'kept (D12)'   # the floor (D7)\n")
    out = cyrusClean.cleanText('x.py', source)
    assert out == ('def f():\n'
                   '    """Resolve it."""\n'
                   "    return 'kept (D12)'   # the floor\n")
    problems = cyrusClean.check('x.py', source, out)
    assert len(problems) == 1 and problems[0].startswith('in the code') and "'kept (D12)'" in problems[0]


def test_a_reference_that_shares_a_parenthesis_keeps_the_rest():
    assert cyrusClean.cleanComment('# held (fundingSplit, D148) here') == '# held (fundingSplit) here'
    assert cyrusClean.cleanComment('# dropped\n    # (D68). Next') == '# dropped.\n    # Next'


def test_a_change_to_the_code_is_caught():
    js = 'var a = 1; /* note (D5) */\n'
    assert cyrusClean.check('x.js', js, 'var a = 2; /* note */\n') == ['the code changed']


def test_the_delivered_bake_names_no_folder_of_the_machine_that_made_it():
    """The manifest keeps the name of each file the bake was made from, not
    where it was: the update does the same to the copy on Cyrus."""
    folder = re.compile(r'"(?:/|\\\\|[A-Za-z]:(?:\\\\|/))')
    found = [os.path.basename(p) for p in glob.glob(os.path.join(SERVICE, 'var', 'baked', '*.json'))
             if folder.search(open(p, encoding='utf-8').read())]
    assert found == []
    manifest = json.load(open(os.path.join(SERVICE, 'var', 'baked', 'manifest.json'), encoding='utf-8'))
    assert manifest['source']['path'] == 'saaPortfolios.csv'
    assert manifest['feeCard']['path'] == 'feeRates.csv'
