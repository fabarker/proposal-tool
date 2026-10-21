#!/usr/bin/env python3
"""The copy of a shipped file that Poros gets: its comments read as Poros's own.

The code here cites its decision log (D-numbers), the spec's sections, its
tests and its earlier versions, and some comments speak of Poros from outside
("the host"). None of that is on Poros, so buildPorosUpdate.py ships each
package and page file through clean(): the same file, with those references
taken out of its comments and docstrings, and nothing else touched.

Only comments and docstrings change, and that is checked for every file
(check()): a Python file must give the same syntax tree, docstrings aside; a
JavaScript, CSS or HTML file the same text once its comments are removed.
What is left must name none of the things in TRACES, or the build stops.

The update builds the copy Poros has from the commit Poros is at with THIS
file as it stood at that commit, so a change here is shipped like any other:
the next update moves Poros to the new copy.

    python3 service/tools/porosClean.py            what is still left, per file
"""

import ast
import io
import json
import os
import re
import sys
import tokenize

TEXT = ('.py', '.js', '.css', '.html', '.json')


# ------------------------------------------------------------- comments ---

def _isDoc(node):
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
        and isinstance(node.value.value, str)


def _pySegments(text):
    """(start, end) offsets of every comment and docstring in Python source."""
    lines = text.split('\n')
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)

    def offset(row, col):
        return starts[row - 1] + col

    # a docstring, or any other string standing alone as a statement
    docs = {node.lineno for node in ast.walk(ast.parse(text)) if _isDoc(node)}
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT or (tok.type == tokenize.STRING and tok.start[0] in docs):
            out.append((offset(*tok.start), offset(*tok.end)))
    return out


_REGEX_AFTER = set('(,=:[!&|?{};+-*%<>~^')
_REGEX_WORDS = ('return', 'typeof', 'case', 'do', 'else', 'in', 'of', 'void', 'delete', 'throw')


def _jsSegments(text):
    """(start, end) offsets of every comment in JavaScript source."""
    out, i, n = [], 0, len(text)
    stack = []                     # template literals we are inside, by brace depth
    depth = 0
    last = ''                      # the last significant code character
    word = ''
    while i < n:
        c = text[i]
        if c == '/' and text.startswith('//', i):
            j = text.find('\n', i)
            j = n if j < 0 else j
            out.append((i, j))
            i = j
            continue
        if c == '/' and text.startswith('/*', i):
            j = text.index('*/', i + 2) + 2
            out.append((i, j))
            i = j
            continue
        if c in '\'"':
            j = i + 1
            while text[j] != c:
                j += 2 if text[j] == '\\' else 1
            i, last, word = j + 1, c, ''
            continue
        if c == '`' or (c == '}' and stack and stack[-1] == depth):
            if c == '}':
                stack.pop()
            j = i + 1
            while True:
                if text[j] == '\\':
                    j += 2
                elif text[j] == '`':
                    j += 1
                    break
                elif text.startswith('${', j):
                    j += 2
                    stack.append(depth)
                    break
                else:
                    j += 1
            i, last, word = j, '`', ''
            continue
        if c == '/' and (last in _REGEX_AFTER or last == '' or word in _REGEX_WORDS):
            j, cls = i + 1, False
            while True:
                if text[j] == '\\':
                    j += 2
                    continue
                if text[j] == '[':
                    cls = True
                elif text[j] == ']':
                    cls = False
                elif text[j] == '/' and not cls:
                    break
                elif text[j] == '\n':
                    raise ValueError('unterminated regular expression at offset {}'.format(i))
                j += 1
            j += 1
            while j < n and (text[j].isalnum()):
                j += 1
            i, last, word = j, '/', ''
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
        if not c.isspace():
            if c.isalnum() or c in '_$':
                word = word + c if (last.isalnum() or last in '_$') else c
            else:
                word = ''
            last = c
        i += 1
    return out


def _cssSegments(text):
    out, i = [], 0
    while True:
        i = text.find('/*', i)
        if i < 0:
            return out
        j = text.index('*/', i + 2) + 2
        out.append((i, j))
        i = j


def _htmlSegments(text):
    out, i = [], 0
    while True:
        i = text.find('<!--', i)
        if i < 0:
            return out
        j = text.index('-->', i + 4) + 3
        out.append((i, j))
        i = j


def _jsonSegments(text):
    """The notes in a JSON file: the strings under keys that start with _,
    which nothing reads."""
    return [m.span(1) for m in re.finditer(r'"_\w+"\s*:\s*"((?:[^"\\]|\\.)*)"', text)]


def segments(key, text):
    ext = os.path.splitext(key)[1]
    return {'.py': _pySegments, '.js': _jsSegments, '.css': _cssSegments,
            '.html': _htmlSegments, '.json': _jsonSegments}[ext](text)


def _code(key, text):
    """The text with its comments (and, for Python, its docstrings) blanked."""
    parts, at = [], 0
    for a, b in segments(key, text):
        parts.append(text[at:a])
        at = b
    parts.append(text[at:])
    return ''.join(parts)


def _pyRegions(text, segs):
    """Python comments on consecutive lines join into one region, so a
    parenthesis that runs from one comment line to the next is seen whole."""
    out = []
    for a, b in segs:
        if out and text[out[-1][1]:a].strip() == '' and text[out[-1][1]:a].count('\n') == 1 \
                and text[out[-1][0]] == '#' and text[a] == '#':
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


# --------------------------------------------------------------- cleaning ---

#: the run of characters that continues a comment onto its next line
_CONT = r'(?:\n[ \t]*(?:#|//|\*(?!/))?[ \t]?)'
_SP = r'(?:[ \t]|' + _CONT + r')'
#: a decision number, alone or as a range, with what may qualify it
_DREF = r'(?:deviations?' + _SP + r'+)?D\d{1,3}(?:' + _SP + r'*[-–]' + _SP + r'*D\d{1,3})?' \
        r'(?:' + _SP + r'+(?:review|QA(?:' + _SP + r'+\d+)?))?'
_SPEC = r'spec(?:' + _SP + r'+section)?' + _SP + r'+\d+(?:\.\d+)*(?:' + _SP + r'+(?:step|item)' \
        + _SP + r'+\d+)?|§' + _SP + r'*\d+(?:\.\d+)*'
#: a reference to the design pages' options and styles
_OPTION = r'(?:plan' + _SP + r'+)?[A-G]\d+[a-z]?|option' + _SP + r'+[A-G]|Q\d{1,3}|gap' + _SP + r'+G\d+'
_ITEM = re.compile(r'(?:' + _SP + r'*)(?:' + _DREF + r'|' + _SPEC + r'|' + _OPTION + r'|named' + _SP
                   + r'+by' + _SP + r'+D\d{1,3}|recorded' + _SP + r'+as' + _SP + r'+' + _DREF
                   + r')(?:' + _SP + r'*)$')
_NUMBER = re.compile(r'(?:' + _SP + r'*)\d+(?:\.\d+)*(?:' + _SP + r'+step' + _SP + r'+\d+)?(?:'
                     + _SP + r'*)$')
_PAREN = re.compile(r'(' + _SP + r'*)\(([^()]*)\)([.,;:]?)([ \t]?)')


def _paren(match):
    lead, body = match.group(1), match.group(2)
    pieces = re.split(r'([,;])', body)
    items = [(pieces[k - 1] if k else '', pieces[k]) for k in range(0, len(pieces), 2)]
    hasSpec = any(re.search(_SPEC, i) and _ITEM.match(i) for _, i in items)
    keep = [(sep, i) for sep, i in items
            if not (_ITEM.match(i) or (hasSpec and _NUMBER.match(i)))]
    if len(keep) == len(items):
        return match.group(0)
    punct, space = match.group(3), match.group(4)
    if not keep:
        # gone, with the space before it; a line break before it stays, and
        # the stop after it goes up to the end of the line before
        return punct + lead if '\n' in lead else punct + space
    text = keep[0][1].lstrip()
    for sep, i in keep[1:]:
        text += sep + i
    return lead + '(' + text + ')' + punct + space


#: Rewordings for what the references leave behind: (as it reads once the
#: references are out, as Poros reads it). Each is applied wherever it occurs
#: in a comment or docstring; one that no longer occurs is simply unused.
REWRITES = [
    # -- the page
    ('<!-- Host convention:', '<!-- Dashboard convention:'),
    ("strip, which is the rule D62 set for the rail's own bar.",
     "strip, which is the rule for the rail's own bar."),
    ("matching proposalToolv2's\n   .stage-heading h1", "matching the design's\n   .stage-heading h1"),
    ("Colour is not in v2's rule either", "Colour is not in the design's rule either"),
    ('from the same v2 rule set', 'from the same design rule set'),
    ("our --ink-2 in place of v2's\n   --muted", "our --ink-2 in place of the design's\n   --muted"),
    ("/* v2's .eyebrow", "/* The design's .eyebrow"),
    ("--accent stands in for v2's --blue-600", "--accent stands in for the design's --blue-600"),
    ('as chosen from the style sheet (style 7b of\n   proposals/allocation-table-styles.html). Each',
     'in its chosen style. Each'),
    ('why spec 9.2\'s "negative figures take --red" had never actually rendered.',
     'why the rule "negative figures take --red" had never actually rendered.'),
    ('is the value spec 6.2 substituted for the house one', 'is the value substituted for the house one'),
    ('The numbered-circle treatment from proposalToolv2: a numeral', 'The numbered-circle treatment: a numeral'),
    ("Ported to this page's own tokens rather than\n   v2's, so the theme swap of spec 6.5 stays a token change.",
     "Set in this page's own tokens, so a\n   theme swap stays a token change."),
    ('The SEMANTICS are unchanged and deliberately not v2\'s. v2 marks these\n   aria-current="step", which '
     'describes a linear wizard; spec 13.2 fixes this\n   nav as role="tablist"',
     'The SEMANTICS are deliberately not a wizard\'s: aria-current="step"\n   would describe a linear '
     'wizard, so this\n   nav is role="tablist"'),
    ('Only\n   the appearance is borrowed.', "Only\n   the appearance is a wizard's."),
    ("/* ── completion progress, from proposalToolv2's .completion-summary ──", '/* ── completion progress ──'),
    ('/* ── export card, from proposalToolv2 ──\n   Its structure verbatim; its greens, ambers and reds swapped '
     'for the ones', '/* ── export card ──\n   Its greens, ambers and reds are the ones'),
    ("rather than carrying v2's !important", 'rather than carrying an !important'),
    ('The 248px fallback is the spec 9.3 value, used', 'The 248px fallback is the specified width, used'),
    ("in the Sleeves view's bar since D156 ──", "in the Sleeves view's bar ──"),
    ('   Proposal 11 of proposals/sleeves-screen-redesign.html. Regions', '   Regions'),
    ('   Option 11 of proposals/new-sleeve-card.html: the form and a summary', '   The form and a summary'),
    ('The same frame, rules and type as the D156 view.', 'The same frame, rules and type as the Sleeves view.'),
    ('the New edition form: the D157 form and summary', 'the New edition form: the New sleeve form and summary'),
    ('// Host integration seam.', '// Dashboard integration seam.'),
    ('#alertArea. Host convention.', '#alertArea. Dashboard convention.'),
    (' * Nothing in the prototype calls this yet - the data is generated client-side -\n * but this is the seam',
     ' * This is the seam'),
    ('drift, and tests/test_overlay_rules.py runs\n   this block, between these two markers, against it. Pure',
     'drift.\n   Pure'),
    ('the values are not - spec 4.3)', 'the values are not)'),
    ('skeletons exactly as spec 10.1 says.', 'shows a skeleton.'),
    ('(spec 2.2: rows come from the weight source)', '(rows come from the weight source)'),
    ('which is what spec 12 asks for anyway.', 'which is what a narrow screen needs anyway.'),
    ('/* spec 10.2 */', '/* nothing to show yet */'),
    ('/* spec 7.1 dialog rules */', '/* the dialog rules */'),
    ("because the spec never enumerates the popover's own\n   controls and Q32 settled that four options plus "
     'two tick boxes beat a\n   compound select (recorded as gap G2). Adding',
     'because four options plus two tick boxes beat a\n   compound select. Adding'),
    ('(the thirteen columns of spec 9.3 plus the fee group)', '(its thirteen columns plus the fee group)'),
    ('Since D155 the rules are data', 'The rules are data'),
    ('App.resolveOverlays() in core.js,', 'App.resolveOverlays(),'),
    ('the rule spec 6.6 sets for the category', 'the rule set for the category'),
    ('Same defect spec 8.4 exists to prevent, so the same',
     "Same defect the implementation table's rounding prevents, so the same"),
    ('the default since D114 */', 'the default */'),
    ('/* a link saved before D114 may sort', '/* an older saved link may sort'),
    ('   Proposal 11 of proposals/sleeves-screen-redesign.html. One view', '   One view'),
    ('   Option 11 of proposals/new-sleeve-card.html: one form', '   One form'),
    ('/* a draft kept before D158 had no rule to tick in */', '/* an older draft has no rule to tick in */'),
    ('   Options 4 and 1 of proposals/new-edition-screen.html, with a switch: one\n   form with the live '
     'summary pinned beside it (the D157 pattern), where who',
     '   One form with the live summary pinned beside it, as the New sleeve form\n   has, and a switch: who'),
    # -- the package
    ('(spec 4.3: schema is data)', '(the schema is data)'),
    ('state in spec section 10 - including error', 'state the page can be in - including error'),
    ('GENERATED by service/tools/buildFontMetrics.py - do not edit by hand.',
     'Generated from the font files - do not edit by hand.'),
    ('D136 wrote it as a\nconstant in rules.py', 'It was once a\nconstant in rules.py'),
    ("reproduces D136's\nthirds", 'reproduces the\noriginal thirds'),
    ('(the recommendation\nthe desk took, proposals/allocation-rules-and-funding-split.html §7):',
     '(the recommendation\nthe desk took):'),
    ("revision recording D136's thirds", 'revision recording the original thirds'),
    ('- which is decision 5: a change reaches', '- by design: a change reaches'),
    ("#: D136's thirds, as percentages", '#: the original thirds, as percentages'),
    ('\nD125. The exports are set', '\nThe exports are set'),
    ('are written by\n``tools/buildOfficeFonts.py`` from the web', 'are made\nfrom the web'),
    ('must stay cheap\n    (test_baked_adapter), and', 'must stay cheap,\n    and'),
    ('rounding of spec 8.4, because', 'rounding, because'),
    ('"""The finiteness guard of spec 15.8.', '"""The finiteness guard.'),
    ('currently selectable in the prototype.', 'currently selectable in the UI.'),
    ('used by the Proposal Tool prototype.', 'used by the Proposal Tool UI.'),
    ('Combinations disabled by the current Proposal Tool prototype.',
     'Combinations disabled in the current Proposal Tool UI.'),
    ('disabled in the current UI prototype.', 'disabled in the current UI.'),
    ("prototype's disabled combinations", "UI's disabled combinations"),
    ('as are the prototype aliases', 'as are the UI aliases'),
    ('In the prototype, ``taa=True``', 'In the UI, ``taa=True``'),
    ('Phase 3 of the PowerPoint export plan (proposals/powerpoint-export-plan.html):\n``writeDeck``',
     'The PowerPoint export:\n``writeDeck``'),
    ('- version 2, "workbook, compact", of\n# proposals/deck-table-styles.html - so',
     '- "workbook, compact" -\n# so'),
    ('(D137 left it only as wide as\n    its columns)', '(it was once only as wide as\n    its columns)'),
    ("D124 opened the shorter table's rows", "an earlier layout opened the shorter table's rows"),
    ('and D138 left them compact;', 'and the next left them compact;'),
    ('on it: D137 labelled every slice, and D138 took the labels off.', 'on it: the slices are not labelled.'),
    ('carried (spec 4.1 minus the management fee)', 'carried (all but the management fee)'),
    ('with every workbook from D123 on;\n        # a row from before keeps NULLs',
     'with every workbook now;\n        # an older row keeps NULLs'),
    ('# and, from D148, which revision', '# and, once the split became a setting, which revision'),
    ("the split was then D136's thirds", 'the split was then the original thirds'),
    ('# and, from D155, which overlay rules', '# and, once overlays became rules, which overlay rules'),
    ("These are the firm's rules from the spec - the $5m mandate floor (2.4), the\n$20m private-assets rule "
     '(2.5), portfolio naming (2.3), the four-portfolio cap\n(2.7) - plus',
     "These are the firm's rules - the $5m mandate floor, the\n$20m private-assets rule, portfolio naming, "
     'the four-portfolio cap\n- plus'),
    ('(spec 4.3: the schema is data,\nnot code)', '(the schema is data,\nnot code)'),
    ("the endpoint block lives inside the HOST's own\n# dashboardRouter.py",
     "the endpoint block lives inside the dashboard's\n# dashboardRouter.py"),
    ("the data. engine.py defers the analytics\n# library's symbols the same way and for the same reason.",
     'the data.'),
    ('which is what spec 2.1 said, and used to', 'which is what the rules say, and used to'),
    ('were hard-coded until D155. They are now', 'were once hard-coded. They are now'),
    ('resolveOverlays() in core.js, between the markers', 'resolveOverlays() in proposalTool.js, between the markers'),
    ('drift (tests/test_overlay_rules).\n', 'drift.\n'),
    ('as the code applied them before D155, in their order', 'as the code once applied them, in their order'),
    ('; D136\n# wrote it here as', '; it was once\n# written here as'),
    ('lives in implementation.js as\n    initialLines()', 'lives in proposalTool.js as\n    initialLines()'),
    ('describe() from first render. Recorded as deviation D4.', 'describe() from first render.'),
    ('ScenarioPort — the adapter the receiving project implements.',
     'ScenarioPort — the adapter every data source implements.'),
    ('This file is extracted verbatim from spec.html section 4.2. It is the whole\nbackend contract',
     'It is the whole\nbackend contract'),
    ('The referenced types are specified in the spec rather than here, because their\nshapes are defined '
     'alongside the surfaces that carry them:\n\n'
     '    BasisInput, MandateInput   section 5.3, state shape\n'
     '    PortfolioKey               section 2.1, what identifies a portfolio\n'
     '    Schema                     section 3.4, GET /api/scenario/schema\n'
     '    PortfolioResult            section 3.4, POST /api/scenario/{id}/portfolio\n'
     '    Sleeve                     section 2.6, sleeves\n'
     '    Advisor                    section 3.4, GET /api/scenario/advisors\n'
     '    Implementation             section 8, step 2\n'
     '    ValidationError            section 3.5, error contract\n',
     'The referenced types are defined alongside the surfaces that carry them:\n\n'
     "    BasisInput, MandateInput   the scenario's state\n"
     '    PortfolioKey               what identifies a portfolio\n'
     '    Schema                     GET /api/scenario/schema\n'
     '    PortfolioResult            POST /api/scenario/{id}/portfolio\n'
     '    Sleeve                     sleeves\n'
     '    Advisor                    GET /api/scenario/advisors\n'
     '    Implementation             the implementation model\n'
     '    ValidationError            the error contract\n'),
    ('assembles\nand the service README documents.', 'assembles.'),
    ('The HTTP surface that sits in front of this port is section 3.4. The page calls',
     'The HTTP surface in front of this port is dashboardRouter.py. The page calls'),
    (' Recorded as\ndeviation D8.', ''),
    ('(the PUT endpoint - deviation D2)', '(the PUT endpoint)'),
    ('Phase 1 of the PowerPoint export plan (proposals/powerpoint-export-plan.html):\neach of',
     'So that a deck can carry them too,\neach of'),
    ('The golden workbook test\n(``compareSheets``) held every cell of three sheets steady through the move;\n'
     'the production styling tests held the fourth.',
     'Every cell of the four\nsheets came through the move unchanged.'),
    ("than D136's D5ECE4 and EAF5F1", 'than the earlier D5ECE4 and EAF5F1'),
    ('(D130 drew it thin, light silver)', '(once thin, light silver)'),
    ('the golden comparison', 'the reference comparison'),
    ('the golden test', 'the reference comparison'),
    ('the golden\n    workbook', 'the reference\n    workbook'),
    ('the test suite compares what these functions emit against captured golden\n# files',
     'what these functions emit is compared against captured reference\n# files'),
    ('(moved verbatim from workbook.py)', '(moved unchanged from workbook.py)'),
    ("# the spec's 34 holds every name", '# a width of 34 holds every name'),
    ("(D130's was a thin light silver)", '(it was once a thin light silver)'),
    ("(D137 aligned\n        # the figures' heads right, over right-aligned figures)",
     '(they were once\n        # aligned right, over right-aligned figures)'),
    ('(D136 put the initial figures beside the\n    long-term ones in one table)',
     '(the initial figures once sat beside the\n    long-term ones in one table)'),
    ('(D142: initial first)', '(initial first)'),
    (' (style 3 of\n    # proposals/initial-allocation-slide-styles.html)', ''),
    ('(D142 put initial first)', '(it was once initial first)'),
    ('(D142 kept\n    # it off the header)', '(it once stopped\n    # at the header)'),
    ('Since D89 the unit of', 'The unit of'),
    ('Until D57 the tables were written here:', 'The tables were once written here:'),
    ('each carrying the eleven fields of spec 4.1 minus the management fee -',
     'each carrying the product fields, less the management fee -'),
    ("The spec leaves these to the receiving project (scenario_port.py's docstring\nsays so); the shapes here "
     'follow the sections that define each surface:',
     'The contract leaves these to the implementation; the shapes here follow\nthe surfaces that carry them:'),
    ('with the rounding rule of spec\n  8.4 applied', 'with the rounding rule\n  applied'),
    ('# The columns of spec 9.3, less the two', "# The implementation table's columns, less the two"),
    ('(and see D74 on\n# the screen and the sheet keeping their own header lists)',
     '(the screen and\n# the sheet keep their own header lists)'),
    ('(D136 carried it in the Implementation\n#: table', '(it was once carried in the Implementation\n#: table'),
    ('derived per spec 8.4.', 'derived as on screen.'),
    ('for the reasons D29,\n    D51, D52 and D96 give; ``buildImplementationDoc`` keeps those words.',
     'for the reasons\n    ``buildImplementationDoc`` gives.'),
]


def cleanComment(text):
    text = _PAREN.sub(_paren, text)
    for old, new in REWRITES:
        text = text.replace(old, new)
    return text


def _regions(key, text):
    segs = segments(key, text)
    return _pyRegions(text, segs) if key.endswith('.py') else segs


def cleanText(key, text):
    parts, at = [], 0
    for a, b in _regions(key, text):
        parts.append(text[at:a])
        parts.append(cleanComment(text[a:b]))
        at = b
    parts.append(text[at:])
    return ''.join(parts)


def clean(key, data):
    """The bytes Poros gets for the shipped file *key*."""
    if not key.endswith(TEXT):
        return data
    crlf = b'\r\n' in data
    text = data.replace(b'\r\n', b'\n').decode('utf-8')
    out = cleanText(key, text).encode('utf-8')
    return out.replace(b'\n', b'\r\n') if crlf else out


# ----------------------------------------------------------------- checks ---

TRACES = re.compile(
    r'\bD\d{1,3}\b|\bQ\d{1,3}\b|\bgap G\d|\bdecision \d|\bspec\b|\bsection \d|§'
    r'|\bhost\b|receiving project|prototype|\bv2\b|proposalToolv\d|golden|\btests?/|\btest_\w+'
    r'|README|engine\.py|\bPORTING\b|DEVIATIONS|epsilon|transplant|proposal-tool|/Users/'
    r'|\bport(?:ed|ing)\b|proposals/|\w+\.html#|spec\.html|\bservice/|\btools/'
    r'|\b(?:core|implementation|picker|repository)\.js\b|\bbuild_\w+\.py|\bverbatim\b', re.I)
#: the same, in the code itself, which this file never changes: a string
#: there that carries one is reworded in the source
CODE_TRACES = re.compile(r'\bD\d{1,3}\b|\bQ\d{1,3}\b|epsilon|proposal-tool|/Users/|transplant'
                         r'|\bPORTING\b|DEVIATIONS|the prototype|spec\.html|proposals/[\w-]+\.html')
#: words TRACES would catch that are the code's own, not a trace
ALLOWED = re.compile(r"machine-epsilon|\*spec\*|chart spec|table spec|card's host")


def _pyTree(text):
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if _isDoc(node):
            node.value.value = ''
    return ast.dump(tree)


def _jsonData(text):
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if not k.startswith('_')}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    return strip(json.loads(text))


def check(key, before, after):
    """[problems] if *after* differs from *before* other than in comments,
    or still carries a trace."""
    problems = []
    b, a = before.replace('\r\n', '\n'), after.replace('\r\n', '\n')
    if key.endswith('.py'):
        if _pyTree(b) != _pyTree(a):
            problems.append('the code changed')
    elif key.endswith('.json'):
        if _jsonData(b) != _jsonData(a):
            problems.append('the data changed')
    elif _code(key, b) != _code(key, a):
        problems.append('the code changed')
    code = _code(key, a)
    for m in CODE_TRACES.finditer(code):
        problems.append('in the code: ' + ' '.join(code[max(0, m.start() - 30):m.end() + 30].split()))
    for x, y in _regions(key, a):
        for m in TRACES.finditer(a, x, y):
            around = a[max(x, m.start() - 30):min(y, m.end() + 30)]
            if not ALLOWED.search(around):
                problems.append('line {}: {}'.format(a.count('\n', 0, m.start()) + 1,
                                                     ' '.join(around.split())))
    return problems


ROUTER = 'service/cyrus_pmg/pmgService/dashboardRouter.py'


def blockRange(lines):
    """The router's endpoint block: from `_XLSX = ` to the end of exportScenario."""
    start = next(i for i, l in enumerate(lines) if l.startswith('_XLSX = '))
    export = next(i for i, l in enumerate(lines) if l.startswith('def exportScenario'))
    end = export + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][:1] in (' ', '\t')):
        end += 1
    while end > export and not lines[end - 1].strip():
        end -= 1
    return start, end


def main():
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    files = subprocess.run(['git', '-C', root, 'ls-files', 'service/cyrus_pmg/pmgService/scenario',
                            'proposalTool'], capture_output=True, text=True, check=True).stdout.split()
    files.append(ROUTER)
    total = 0
    for rel in files:
        if not rel.endswith(TEXT):
            continue
        before = open(os.path.join(root, rel), encoding='utf-8').read()
        if rel == ROUTER:
            lines = before.split('\n')
            start, end = blockRange(lines)
            before = '\n'.join(lines[start:end])
        after = cleanText(rel, before)
        for p in check(rel, before, after):
            print('{}: {}'.format(rel, p))
            total += 1
    print('{} left'.format(total))
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())
