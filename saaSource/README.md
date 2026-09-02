# The SAA source — a stand-in for the supplying database

Strategic asset allocations are owned downstream. This directory holds a
fictitious extract in the shape that database hands over, so the bake can be
built and tested against something before it is pointed at the real thing.

| File | What it is |
|---|---|
| `saaPortfolios.xlsx` | the extract: `PortfolioName`, `AssetTicker`, `Weight`. 172 portfolios, 2,468 rows |
| `saaPortfolios.csv` | the same rows, diffable in review |
| `buildSaaSource.py` | generates both, so the invented weight model is reproducible |
| — | the parser and the facet derivation live in the package: `scenario/saaKeys.py`, `scenario/universe.py` |

**The weights are invented.** The tickers are the real ones the tool already
carries (`portfolio_weights.ASSET_METADATA`); the allocations across them are
made up to be plausible and internally consistent. Nothing here reaches a
client. The tactical tilt fund is deliberately absent — tactical allocation is
an implementation choice and no strategic portfolio carries it (D50).

## The key

The database keys a portfolio by its **name**, and the name carries everything
except hedging:

    USD Moderate ex-HFs ex-RAs   ->   USD|Moderate|ex-HFs|1
    USD Moderate Full            ->   USD|Moderate|Full|0
    USD All Equity               ->   USD|All Equity|NA|NA

    currency | riskLevel | allocationType | excludeRealAssets [ | hedging ]

Three things the parse does that the name does not say outright:

* **`ex-RAs` is a toggle, not an allocation type.** The name's trailing field
  carries two key fields. The UI offers four allocation types and a separate
  exclusion tick box, not six types.
* **An all-equity book has no allocation type.** It is 100% public equity, so
  there are no alternatives to include or exclude and both fields read `NA`.
  The database carries one such portfolio per currency, not six identical ones.
* **Hedging is not in the name and not in the portfolio key.** The same weights
  are analysed under each hedging assumption, so hedging joins the key only
  when the analytics run: `USD|Moderate|ex-HFs|1|Hedged`.

Names cannot be split on whitespace — 72 of the 172 do not have three
whitespace-delimited fields, because risk levels and allocation types both
contain spaces. Every token is matched longest-first against a closed
vocabulary, and anything unrecognised is rejected rather than guessed.

## Parse offline, never at runtime

The parse belongs in the bake's enumeration step. It runs offline, a rename
upstream fails visibly in the manifest, and a human fixes the data before
anything ships. If it ran on the request path, a rename would break the live
service. `scenario/saaKeys.py` and `universe.py` assert three invariants that make that failure loud
(`python3 -m cyrus_pmg.pmgService.scenario.bake --census` is the dry run):

* unknown tokens are **rejected**, with the raw name recorded — never guessed
* `format(parse(name)) == name` for every name (172 of 172)
* keys are unique (no two names collapse to one key)

## What the vocabularies are for

`CURRENCIES`, `RISK_LEVELS` and `ALLOCATION_TYPES` in `scenario/saaKeys.py` are the
parser's dictionary, the validator, and the selector ordering, all at once.
Ordering is the one thing a name genuinely cannot tell you — least to most
risky is not alphabetical and not derivable — so that ordered list is the
irreducible configuration. Everything else falls out of the data.

### Three things that stop being code

Run the census and the selectors are derived from the key set alone:

| Derived | Today |
|---|---|
| Allocation types offered per risk level | `rules.ALLOCATIONS`, a literal list |
| All Equity offers none, so the selector greys out | would need an `if risk == 'All Equity'` rule |
| `ex-RAs` enabled for Full and ex-HFs only | `rules.RE_ALLOWED = ['Full', 'Ex HFs']`, a literal list |

The third is the telling one: the derivation independently reproduces a
hardcoded list that carries a spec citation beside it. Core and ex-Alts hold no
private assets, so no `ex-RAs` variant of them exists in the database, so the
toggle has nothing to offer. Nobody has to remember to keep the list in step.

## Decision: adopt the database's spelling

The risk value in the key is the database's, not the tool's short code:

| Tool today | Database |
|---|---|
| `Low Vol` | `LowVol` |
| `Cons` | `Conservative` |
| `Cons Mod` | `ConsMod` |
| `Mod` | `Moderate` |
| `Mod Agg` | `ModAgg` |
| `Agg` | `Agg` |
| — | `Higher Risk` *(new)* |
| `All Equity` | `All Equity` |

This is the right call — one spelling, no mapping table to drift — but it has
two consequences worth carrying deliberately.

**It invalidates the bake and every stored scenario.** The risk level is the
key's second field. Ship the change with the scenario store cleared; the
24-hour retention makes that cheap.

**It does not settle what the UI displays.** Three of the eight are compressed
forms. `ConsMod` and `ModAgg` are keys, not words to put in front of a PWA or
into a client workbook, where the tool currently prints *Conservative-Moderate*
and *Moderate-Aggressive*. So D35's label map does not die — it is demoted. It
stops being load-bearing (it existed because the short value could not be
renamed) and becomes purely cosmetic. The alternatives are to ask the supplying
system for a display-name column, or to accept `ConsMod` on screen and in the
workbook. Decided: keep the map (`rules.RISK_LEVEL_LABELS`), purely cosmetic.

## What the bake does (built — D54)

1. **Enumerate.** Read the distinct names, parse each to a key, record failures
   rather than aborting. Cheap. This alone gives the selectors.
2. **Weights.** Bulk read, keyed by the parsed key. Also cheap; this is what
   the allocation table renders.
3. **Analytics.** Risk and return per key × hedging assumption. Expensive
   (~97s live per portfolio), so resumable, parallel, failures recorded per key.

Then: **availability means "completed step 3"**, not "the database offered it".
A portfolio that enumerates but fails analysis must not be offered, or a PWA
picks a column that errors. `bake.py` already records per-key failures; they
just do not feed the offered set yet.

The manifest should carry the derived field sets and their order alongside the
coverage it already records, so `get_schema` reads one file rather than
recomputing from a weights module. That is the seam to change:
`bakedAdapter.get_schema` currently calls `rules.schemaPayload`, which derives
availability from the generated frame — so today the bake and the offered set
come from different sources.

Operationally: the bake job holds the database credentials and the service holds
none. Bake into a new directory and flip a symlink, so a half-written store is
never served. Stamp the manifest with the data version it came from.
