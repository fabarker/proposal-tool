"""Delivered CSV extracts, read the way Excel and copying leave them.

Two things the readers of the delivered extracts (the products, the strategic
universe, the sleeve seed) must tolerate:

* a file saved from Excel starts with a byte-order mark, which the readers
  drop by opening with ``encoding='utf-8-sig'``;
* an extract copied through some tools arrives with every row wrapped in one
  extra pair of double quotes end to end, so ``csv.reader`` reads the whole
  row as a single field. ``unwrapRows`` takes that pair off.

Both are handled here, in one place, for all three readers.
"""

from __future__ import annotations


def unwrapRows(lines):
    """*lines* (an open text file), with a row that is wrapped end to end in
    one extra pair of double quotes - it starts and ends with ``"`` and holds
    no other ``"`` - handed on without the pair. Every other line passes
    through untouched, so a properly quoted field (``"a, b"`` among others)
    is never disturbed."""
    for raw in lines:
        stripped = raw.rstrip('\r\n').strip()
        if (len(stripped) >= 2
                and stripped.startswith('"')
                and stripped.endswith('"')
                and stripped.count('"') == 2):
            yield stripped[1:-1] + '\n'
        else:
            yield raw
