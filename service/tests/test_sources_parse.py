"""Every Python file in the repository parses (D161).

scenarioPort.py lost the end of a docstring at D122 and stayed a syntax error
for three weeks: nothing imports the port contract, so no test and no service
start ever read it. A host that compiles the package, or an editor that opens
it, does. This reads every source as the interpreter would, imported or not -
the porting tools and their templates included, since they reach the host as
pasted text and are run there with no other check."""
import ast
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
SKIP = {'__pycache__', 'node_modules', 'archive'}


def _sources():
    for here, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith('.')]
        for name in names:
            if name.endswith('.py'):
                yield os.path.join(here, name)


def test_every_python_source_parses():
    broken, seen = [], 0
    for path in _sources():
        seen += 1
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
        try:
            ast.parse(text, filename=path)
        except SyntaxError as exc:
            broken.append('{}:{}: {}'.format(os.path.relpath(path, ROOT), exc.lineno, exc.msg))
    assert seen > 80
    assert broken == []
