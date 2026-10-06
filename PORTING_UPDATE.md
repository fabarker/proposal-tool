# Bringing Cyrus up to date with the Proposal Tool

This guide updates a Cyrus host that already runs the Proposal Tool. It replaces the update notes
in `PORTING.md` §0A, which it was checked against and corrects in three places (see the end).
`PORTING.md` remains the reference for a **first** port and for everything this guide cites by
section number.

- **Host checkout:** `H:\cyrus-repo\isg-cyrus-pmg`, on the Cyrus machine.
- **No clone on that machine.** It reaches GitHub only through a browser; the firewall stops
  `git clone`. So the update crosses by **copy and paste**: step 1 pastes a small set of text
  files from the repository's `porting/bundle` folder and unpacks them.
- **`<ep>`:** the folder step 1 unpacks, `H:\pt-update\proposal-tool`. It has this repository's
  layout, holding everything the update copies.

Every command is for the Cyrus machine.

---

## 1. Where Cyrus stands

The last port was made on **10 September 2026**, at the change numbered **D89** (sleeve editions).
It was verified end to end on the host: the page served through the Flask route, and the schema
answered through the proxy with `adapter: baked`.

| Where | Commit |
|---|---|
| epsilon-phi, where the tool lived then | `718d750` |
| this repository, after the history was carried over (D126 rewrote the hashes) | `35dbbbf` |

Since then this repository has moved on by 37 commits and 70 numbered changes, **D90 to D159**
(one line each in `service/DEVIATIONS.md`).

You don't need to know any of that to update. Step 2 below runs a read-only check on the host that
reports exactly what it carries. The procedure is the same whether the host is at D89 or anywhere
after it, because every part is copied whole and both databases upgrade themselves step by step.

---

## 2. What changed since D89, in porting terms

Measured with `git diff 35dbbbf..HEAD` over everything that ships.

| What | Change | On the host |
|---|---|---|
| **Python dependency** | `python-pptx` for the PowerPoint deck. It brings `lxml`, `Pillow`, `XlsxWriter` and `typing_extensions`. `openpyxl` and `pandas` are unchanged. | **Install it first.** Without it every *Download proposal* fails, including the workbook: the workbook and the deck are one delivery (D123). |
| **The package** `pmgService/scenario/` | **11 new files:** `sheetDoc.py`, `pptWriter.py`, `houseFonts.py`, `fontMetrics.py`, `fundingSplit.py`, `overlayRules.py`, and five fonts under `fonts/`. **16 changed files.** **2 removed:** `engine.py` and `liveAdapter.py`, retired with the live adapter (D126). | Copy the folder over the host's, then **delete the two retired files**. A copy that doesn't mirror leaves them behind. |
| **Host-owned files in the package** | `fees.json` gained an optional `custom` block (D96). | Keep the host's `fees.json` if `feeTools --accept` was ever run there. Keep its `advisors.xlsx` if the directory was replaced there. Without the block, `fees.py` uses the same values. |
| **The router** `pmgService/dashboardRouter.py` | The block grows from **30 to 42 routes**. **Six names** are imported above the block markers, where pasting the block doesn't reach. | Add the imports by hand (§4 step 7), then replace the block between its markers. |
| **The page folder** `dashboard/proposalTool/` | `proposalTool.html`, the CSS and the JS changed. The fonts did not. | Copy the folder whole. |
| **The sleeve library** (`SCENARIO_SLEEVES_DB`) | Schema **3 → 6**: two new tables, `policies` and `policyHistory`. They hold the funding split (D148) and the overlay rules (D155). | **Nothing to run.** It upgrades on first open and seeds the house split and the house overlay list. **Back it up first.** |
| **The proposal register** (`SCENARIO_REGISTER_DB`) | Schema **1 → 5**, adding columns: `customFees` (D96); `deck`, `deckName`, `deckSha` and `deckBytes` (D123); `fundingSplit` (D148); `overlays` (D155). Account requests share this file. | **Nothing to run.** It upgrades on first open. Earlier rows keep NULLs and have no deck. **Back it up first.** |
| **Configuration** | **No new variable.** `SCENARIO_BAKED_FALLBACK` and `SAA_ENGINE_PACKAGE` are no longer read. `SCENARIO_ADAPTER=live` no longer exists. | `SCENARIO_ADAPTER` must be `baked`. Remove the other two when convenient; leaving them is harmless. |
| **The bake and the data extracts** | **Unchanged.** No file under `service/var/baked`, and no SAA, product, fee-card or seed CSV, changed since D89. | Nothing to deliver. If the host ran with `SCENARIO_BAKED_FALLBACK=1`, a combination missing from the bake now shows the column's error state with Retry instead of being computed. The delivered bake covers the whole space. |
| **Flask route, nav link, authentication, proxy** | **Unchanged.** The new routes use the existing `requireAdmin`. The proxy already passes the zip and its headers (`PORTING.md` §0A.1, checked against the real host files). | Nothing. |

The 12 new routes:

| Area | Routes |
|---|---|
| Funding split (D148) | `GET` and `PUT /scenario/repository/funding`; `GET …/funding/history`; `POST …/funding/remove` and `…/funding/revert` |
| Overlay rules (D155) | `GET` and `PUT /scenario/repository/overlays`; `GET …/overlays/history`; `POST …/overlays/remove` and `…/overlays/revert` |
| Proposal register | `GET /scenario/repository/proposals/views` (D116); `GET …/proposals/{proposalId}/deck` (D123) |

### What people will notice

- **PWAs:**
  - The first build is guided, one control at a time (D91).
  - A second door on the landing page, *Open an Account*, starts from a Proposal UID (D107, D111).
  - A *Custom* fee level, with rates the PWA enters (D96).
  - *Show initial allocation* for portfolios that hold private markets (D136, D141).
  - **One *Download proposal* button that returns a zip holding the workbook and a PowerPoint
    deck**, both set in GS Sans and both locked (D121–D125).
- **Admins, in the repository console:**
  - The proposal register, with saved views and the deck beside the workbook (D116).
  - The *Uncalled Capital Allocation* tab (D148, D149) and the *Overlay Funding* tab (D155).
  - The Sleeves view as cards or a table (D156).
  - New *New sleeve* and *New edition* forms (D157, D158).
  - Saving a changed sleeve now archives the version it replaces (D153).
  - A seed workbook for loading sleeves and editions from Excel (D159).

---

## 3. Before you start

- **Choose a quiet window.** The page and the service must change together. A new page talking to
  the old service, or the reverse, fails in ways nobody will enjoy reading about.
- **Every proposal exported is kept for ever** in the register. That is the point of the register.
  The test export in step 11 will stay on the record, so make it under a mandate you'll recognise.
- **The gate runs on the development machine, not on Cyrus.** Before a bundle is built, the suite
  must pass there (`cd service`, then `PYTHONPATH=. python3 -m pytest tests -q`). It printed
  **556 passed** on 4 October 2026, in about 5 minutes. Don't port a bundle built from a commit
  that fails it.
- **The bundle must be current.** It is rebuilt after the last change with
  `python3 service/tools/buildPortBundle.py` and committed with the code it carries.
  `porting/bundle/README.md` on GitHub names the commit it was built from. That should be the
  latest commit on the repository's front page, or one that changed nothing in the package, the
  page folder or the router.

---

## 4. The update, step by step

### Step 1: Carry the update across, by copy and paste

The update is about 30 files, and five of them are fonts the deck embeds. A font can't be pasted,
and an editor can quietly re-encode a pasted Python file. So everything the update copies is
packed into the repository's `porting/bundle` folder as plain text:
- `unbundle.py`, about 4 KB;
- six `part-NN.txt` files of about 200 KB each.

`unbundle.py` checks every part against the hash it was built with before it unpacks anything.
A paste that went wrong is caught and named, never installed.

1. **Make an empty folder:** `H:\pt-update`. If it holds a `proposal-tool` folder from an earlier
   update, delete that first; `unbundle.py` refuses to unpack over one.
2. **Open `porting/bundle` on GitHub** in the browser. Its README lists each file to copy, and the
   commit the bundle was built from (see §3).
3. **Copy each file the README lists into `H:\pt-update`, under exactly its name.** Start with
   `unbundle.py`, then each `part-NN.txt`. For each one:
   1. Open the file on GitHub.
   2. Press **Copy raw file**, the button with two overlapping squares at the top right of the
      file. Don't select the text by dragging: that is how first and last lines go missing.
   3. Open Notepad, paste, and choose **File → Save As**.
   4. Set *Save as type* to **All files (\*.\*)**, so Notepad doesn't turn `unbundle.py` into
      `unbundle.py.txt`, and save into `H:\pt-update` under the file's own name.

   The encoding and the line endings Notepad chooses don't matter. If the browser is allowed to
   download, GitHub's **Download raw file** button (beside *Copy raw file*) saves the file
   directly and does the same job.
4. **Unpack.** Use any Python 3.8 or later on that machine; the service's virtualenv will do.

   ```bat
   cd /d H:\pt-update
   python unbundle.py
   ```

   It should end with these two lines:
   - `Unpacked 52 files from commit <commit> into H:\pt-update\proposal-tool`
   - `Next: PORTING_UPDATE.md step 2, with <ep> = H:\pt-update\proposal-tool`

   If it names a part instead, copy that part again; nothing is unpacked until every part matches.

   | It says | Do |
   |---|---|
   | `missing` | The file isn't in `H:\pt-update` under that name. |
   | `empty` | Notepad saved before the paste landed. Paste again and save. |
   | `… bytes where … were built` | Lines were lost or added. Use *Copy raw file*, not a drag. |
   | `the right length but not the right text` | The file holds another part's text. |
   | `already exists` | Delete `H:\pt-update\proposal-tool` and run again. |

From here on, **`<ep>` is `H:\pt-update\proposal-tool`**. It holds the package, the page folder,
the router, `service\tools\portCheck.py`, this guide and `requirements.txt`, in the repository's
own layout. The bake and the data extracts are not in it: none of them changed since D89 (§2).

**If the browser can download a zip from GitHub** (*Code → Download ZIP* on the front page), that
is a shortcut. Unzip it to `H:\pt-update`, and use the `proposal-tool-main` folder inside as
`<ep>` in place of steps 1 to 4 above.

### Step 2: Measure the host

Run this in the same environment the service starts from: its virtualenv active and its `SCENARIO_*`
variables set. The check then reads the databases the service reads, and tests the interpreter the
service runs on. It writes nothing, and opens the databases read-only.

```bat
cd /d H:\cyrus-repo\isg-cyrus-pmg
python <ep>\service\tools\portCheck.py --src H:\cyrus-repo\isg-cyrus-pmg\src
```

**A host at the 10 September port** reports:
- "changes up to about D89";
- in the package: 15 files differ, 11 are missing, and `engine.py` and `liveAdapter.py` are only on
  the host;
- in the page folder: 3 files differ;
- in the router: 30 routes against 42, and the six imports missing;
- the sleeve library at schema 3 and the register at schema 1;
- and `pptx MISSING`, if the dependency isn't installed yet.

It ends with *The host is behind this checkout* and exits 1.

**Write down the two database paths it prints.** They are where the service keeps its data. Unless
the settings were moved to durable storage after the first port, they are the package defaults
under `H:\cyrus-repo\isg-cyrus-pmg\src\var\`.

If the report is not what you expect:

| The report says | What it means |
|---|---|
| *no change since D89 shows* and no `sleeveRules.py` | The host predates the 10 September port. Follow `PORTING.md` §8 instead. |
| *The host is level with this checkout* | Nothing to do. |

### Step 3: Install the dependency

Install into the host's virtualenv, and prove it imports:

```bat
pip install python-pptx
python -c "import pptx, lxml, PIL, xlsxwriter; print('python-pptx', pptx.__version__)"
```

Version 1.0.2 is proven here, with lxml 4.9.3, Pillow 10.4.0 and XlsxWriter 3.2.9. If the firm's
package index doesn't carry it, **stop**: the remaining steps would leave the host unable to export.

### Step 4: Stop the service and the frontend

Stop both the FastAPI service and the Flask frontend, the way the host normally stops them. The
databases are copied next, and a SQLite file should be copied while nothing has it open.

### Step 5: Back up

Save everything the update replaces or upgrades. Use the two paths from step 2 for the databases.

```bat
set BK=H:\pt-backup-before-update
mkdir %BK%
robocopy H:\cyrus-repo\isg-cyrus-pmg\src\cyrus_pmg\pmgService\scenario %BK%\scenario /E /XD __pycache__
robocopy H:\cyrus-repo\isg-cyrus-pmg\src\cyrus_pmg\dashboard\proposalTool %BK%\proposalTool /E
copy H:\cyrus-repo\isg-cyrus-pmg\src\cyrus_pmg\pmgService\dashboardRouter.py %BK%\
copy "<the SCENARIO_SLEEVES_DB path from step 2>" %BK%\sleeves.db
copy "<the SCENARIO_REGISTER_DB path from step 2>" %BK%\proposals.db
```

### Step 6: Replace the package

Copy the package over the host's, then delete the two retired files:

```bat
robocopy <ep>\service\cyrus_pmg\pmgService\scenario H:\cyrus-repo\isg-cyrus-pmg\src\cyrus_pmg\pmgService\scenario /E /XD __pycache__ /XF *.pyc .DS_Store
del H:\cyrus-repo\isg-cyrus-pmg\src\cyrus_pmg\pmgService\scenario\engine.py
del H:\cyrus-repo\isg-cyrus-pmg\src\cyrus_pmg\pmgService\scenario\liveAdapter.py
```

If step 2 listed `fees.json` or `advisors.xlsx` as host-owned and the host's copy matters (see the
table in §2), put that copy back from `%BK%\scenario`.

Then check the deck's modules load. Run this from `H:\cyrus-repo\isg-cyrus-pmg\src`, or with it on
`PYTHONPATH`:

```bat
python -c "import cyrus_pmg.pmgService.scenario.pptWriter, cyrus_pmg.pmgService.scenario.houseFonts as h; print(sorted(h.FACES))"
```

It should print `['GS Sans', 'GS Sans Condensed', 'GS Sans Light']`.

### Step 7: Update the router

The changes go in the host's `pmgService\dashboardRouter.py`.

**First, the imports above the `TRANSPLANT BLOCK BEGIN` line.** Make them read as below. Merge with
lines the host already has, and keep the host's own authentication import exactly as the first port
left it: pointed at `pmgEntitlement` (`PORTING.md` §9.2). New since D89 are:
- the three `import` lines;
- `fundingSplit` and `overlayRules` in the `scenario` import;
- `validateCustomFees` in the `rules` import.

```python
import io
import json
import zipfile

from cyrus_pmg.pmgService.scenario import (accountRequests, fees, fundingSplit, overlayRules,
                                           products, proposalRegister, scenarioStore,
                                           sleeveRepo, sleeveRules)
from cyrus_pmg.pmgService.scenario.registry import getScenarioPort
from cyrus_pmg.pmgService.scenario.rules import (
    exportFilename, validateBasis, validateCustomFees, validateFeeLevel, validateFeeSchedule,
    validateKey, validateVariant)
```

**Then the block.** In `<ep>\service\cyrus_pmg\pmgService\dashboardRouter.py`, select everything
from the line after `# ===== TRANSPLANT BLOCK BEGIN` to the line before
`# ===== TRANSPLANT BLOCK END`. Paste it over the same range in the host's file, and keep the two
marker lines.

Check it, from `src` or with it on `PYTHONPATH`:

```bat
python -c "from cyrus_pmg.pmgService.dashboardRouter import router; print(len([r for r in router.routes if r.path.startswith('/scenario')]))"
```

It should print **42**. Older text in `PORTING.md` says 32 or 37; both are out of date.

### Step 8: Replace the page folder

```bat
robocopy <ep>\proposalTool H:\cyrus-repo\isg-cyrus-pmg\src\cyrus_pmg\dashboard\proposalTool /E /XF .DS_Store
```

### Step 9: Check the configuration

The host's environment defaults need these values:
- `SCENARIO_ADAPTER` is `baked`.
- `SCENARIO_BAKED_FALLBACK` and `SAA_ENGINE_PACKAGE` can go. Nothing reads them.

Nothing else changes.

### Step 10: Start, and let the databases upgrade

Start the FastAPI service and the Flask frontend. Then run both commands below, in the service's
environment, so each database upgrades now rather than on a user's first click. These are the only
writes besides the copies.

**The sleeve library.** This one opens the library and upgrades it from schema 3 to 6:

```bat
python -m cyrus_pmg.pmgService.scenario.sleeveTools --census
```

It should list the four implementation types with their sleeve counts, and the fixed categories.

**The register.** This one opens it and upgrades it from schema 1 to 5:

```bat
python -c "from cyrus_pmg.pmgService.scenario import proposalRegister as r; d = r.describe(); print(d['schemaVersion'], d['proposals'])"
```

It should print `5` and the number of proposals already delivered.

### Step 11: Measure again, then walk the flow

```bat
python <ep>\service\tools\portCheck.py --src H:\cyrus-repo\isg-cyrus-pmg\src
```

The check should end with *The host is level with this checkout* and exit 0, and report:
- the package and the page identical (only a host-owned `fees.json` or `advisors.xlsx` may differ);
- the router at 42 routes, text identical, every import ticked;
- the sleeve library at schema 6 and the register at 5;
- `pptx` present.

Then walk it in the browser, first as an allowlisted PWA and then as an admin:

1. **The landing page** has two doors, *Start Here* and *Open an Account*. An admin also sees the
   library strip beneath them; a PWA does not.
2. **Build a proposal.** Choose a base and one comparison. Move to step 2, choose the implementation
   type and a sleeve for each category. Tick *Include fees* and choose a schedule. Choosing the
   *Custom* level opens the custom fee card.
3. **The initial allocation.** With a *Full* or *ex-HFs* base, *Show initial allocation* opens the
   initial columns. Reload the page: the scenario comes back with its private-markets sleeve still
   chosen.
4. **Download proposal.** This downloads one `.zip` holding a `.xlsx` and a `.pptx` named with the
   same Proposal UID. The deck opens in PowerPoint, set in GS Sans and marked final. At the
   packaged catalogue a small mandate is refused below product minimums, so use a mandate large
   enough for the sleeves you chose (`PORTING.md` §15 item 11).
5. **Proposals tab (admin).** It shows that UID at the top. Both *Download the workbook* and
   *Download the deck* work. A proposal delivered before the update has a workbook and no deck,
   which is correct.
6. **Uncalled Capital Allocation tab (admin).** It shows the house split, Investment Grade Fixed
   Income 33.3333% and Public Equity 66.6667%, at revision 1, *baseline*.
7. **Overlay Funding tab (admin).** It shows the house list: *Tactical Tilts*, then *Strategic
   Volatility Premium*.
8. **Sleeves tab (admin).**
   - The *Cards / Table* switch works.
   - *+ New sleeve* and a sleeve's *+ Add an edition* open their forms, with *Rules / Grid* on the
     edition form. Use **Cancel** rather than creating test sleeves in the production library.
   - A sleeve edited before the update still shows its history.
9. **Open an Account** with the UID from item 4 fills itself from the proposal.

The full acceptance list is in `PORTING.md` §15. Items 10a, 10b, 10c, 12, 12a, 15 and 17 are the
ones the changes since D89 touch.

---

## 5. Rollback

1. Stop both processes.
2. Restore the package, the router and the page folder from `%BK%`:

   ```bat
   robocopy %BK%\scenario H:\cyrus-repo\isg-cyrus-pmg\src\cyrus_pmg\pmgService\scenario /MIR /XD __pycache__
   robocopy %BK%\proposalTool H:\cyrus-repo\isg-cyrus-pmg\src\cyrus_pmg\dashboard\proposalTool /MIR
   copy /Y %BK%\dashboardRouter.py H:\cyrus-repo\isg-cyrus-pmg\src\cyrus_pmg\pmgService\
   ```

3. Start both processes.

**The databases do not need restoring.** The 10 September code works on the upgraded files: it
names its columns on every write and never lowers the stamped schema version. A later re-update
finds the new tables and columns already there. This was tested on 4 October 2026 (§7).

Restore `%BK%\sleeves.db` and `%BK%\proposals.db` only if the data itself went wrong. If you do,
any proposal delivered after the update loses its register row, though the files the PWA downloaded
still exist.

`python-pptx` can stay installed. Nothing in the old code imports it.

---

## 6. Still open from the first port

None of these is code, and none blocks the update. The 10 September port left them for the host's
owners:

- **The nav link** in the host's `index.html` (`PORTING.md` §10.3).
- **The stores on durable storage.** Point `SCENARIO_SLEEVES_DB`, `SCENARIO_REGISTER_DB` and
  `SCENARIO_STORE_DIR` outside `src\` (`PORTING.md` §11.4), so a redeploy of `src\` can never take
  them with it. Step 2 shows where they are today.
- **A local sign-on convention.** GSSSO cannot set cookies for `localhost`. A `REMOTE_USER` header
  or a `.gs.com` hostname alias works.

**Next time.** The same steps carry any later update. The bundle is rebuilt and committed before
each port. On Cyrus, delete `H:\pt-update` and paste the new bundle into a fresh one. Step 2's
check then says what has changed since this update.

---

## 7. How this guide was checked

On 4 October 2026, at the commit that adds it:

- **The change table** comes from `git diff 35dbbbf..HEAD` over the package, the router, the
  page, the host stand-ins, the data and the requirements.
- **The suite** printed 556 passed. The live development databases were untouched; their hashes
  were compared before and after.
- **The upgrade was proved on real 10 September databases:**
  1. The 10 September code (`git archive 35dbbbf`) built a sleeve library and a register, with a
     sleeve edit and an exported proposal. That gave schemas 3 and 1.
  2. The current code then opened them, which upgraded them to 6 and 5. It exported a new proposal
     as the workbook-and-deck zip.
  3. Afterwards:
     - The old proposal still downloaded its workbook, and its deck was refused as never delivered.
     - The funding split and the overlay list were seeded.
     - The old sleeve's history was kept.
- **The rollback was proved the same way.** The 10 September code exported and edited on the
  upgraded files, which stayed at 6 and 5. The current code then carried on.
- **`service/tools/portCheck.py`** was run against a reconstruction of the 10 September host,
  where it reported every difference listed in step 2, and against the current mirror, where it
  reported level.
- **The step 6, 7 and 10 commands** were run against the 10 September databases, and printed what
  this guide says they print.
- **The paste route was tested on 5 October 2026**, with every part saved the way Notepad saves a
  paste: Windows line endings, a byte-order mark, trailing spaces and no final newline.
  - `unbundle.py` unpacked all 52 files byte for byte under Python 3.8.
  - `portCheck.py`, run from the unpacked folder against the reconstructed 10 September host, gave
    the same report as from the repository.
  - It refused to unpack, naming the right part each time, when a part was missing, empty, short
    of its first or last line, or held another part's text, and when an earlier unpack was still
    in the way.

Three corrections to `PORTING.md` §0A:
- It never says to delete `engine.py` and `liveAdapter.py`.
- Its steps back up only the register, though the sleeve library upgrades too.
- Its route check expects 32 where there are 42.
