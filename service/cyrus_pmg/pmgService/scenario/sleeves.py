"""The PMG sleeve library - the facade every consumer reads through.

The library itself lives in the sleeve repository (sleeveRepo.py, D57): a
database the service owns, built and maintained by an admin from inside the
app, whose products come from the delivered catalogue (products.py, D56).
This module is the contract the rest of the package has always depended on
and nothing more - the four implementation types, and three functions:

    VARIANTS                          the types, in the order the UI offers them
    listSleeves(category, variant, key=None)
                                      the sleeves a category offers under a type,
                                      resolved for the base portfolio *key* (D89)
    sleeveExists(category, name, variant, key=None)
    variantExists(variant)

Until D57 the tables were written here: a BASELINE library for the Multi-Asset
type and per-type deltas (D9, D29), checked at import for weights summing to
1, unique names and priced fee groups. The same sleeves, product for product
and weight for weight, are now the seed the repository loads on first open
(proposal-tool/sleeveSource), and the same checks run at save instead.

A sleeve is served in the shape it always was: a name and a list of products,
each carrying the eleven fields of spec 4.1 minus the management fee (D51) -
a fee group instead, and its fee resolved at pricing time. Two fields ride
along now that did not before, the sleeve's id and note; nothing downstream
reads them.

Editions (D89). A name may hold several rows in the repository, each for a
set of strategic portfolios; listSleeves takes the base portfolio's key and
serves ONE row per name - the edition whose rules match, else the fallback.
The served shape does not say which. Without a key only fallbacks are served,
which is the library exactly as it stood before editions existed.

Implementation types (D29)
--------------------------
The library is not one library but four. A type is chosen before any sleeve
is, and it decides two things: which sleeves a category offers at all, and
what those sleeves contain. The same sleeve name can carry different products
under different types - a US Onshore book reaches US mutual funds and SMAs,
an Irish Onshore book reaches UCITS - which is why the type has to be settled
before the sleeve pickers mean anything. A category with no sleeve under a
type is a real state, not an error - see listSleeves in the repository.
"""

from __future__ import annotations

from .sleeveRepo import VARIANTS, listSleeves, sleeveExists, variantExists

__all__ = ['VARIANTS', 'listSleeves', 'sleeveExists', 'variantExists']
