# Updating Cyrus

Development happens here; Cyrus takes each change as **one file**, `update.py`,
copied from this folder on GitHub. Cyrus has no clone, its terminal takes no
paste, and nothing on Cyrus may record that it was updated: the file writes
the code and nothing else - no stamp, no comment, no marker. Its backups and
its report stay beside it in `H:\pt-update`, outside the project.

## Here

```bash
cd service && PYTHONPATH=. python3 -m pytest tests -q     # the gate: it must pass
cd .. && python3 service/tools/buildCyrusUpdate.py        # writes porting/update.py
git add porting && git commit && git push
```

`update.py` carries the package and page files changed since the commit Cyrus
has (`cyrus.json`), the endpoint block when it changed, and the content hash of
every file as Cyrus should have it. It prints whether the service must be
stopped.

Cyrus gets a **clean copy** of each file: `service/tools/cyrusClean.py` takes
out of the comments what only this repository has - decision numbers, spec
sections, tests, design pages, the generator's files, "the host" - so the code
there reads as Cyrus's own, and checks that the code itself is untouched. The
build stops, naming the line, if a trace would reach Cyrus; so does
`tests/test_cyrus_copy.py`, so it shows up while you work. Reword the comment,
or add the rewording to `REWRITES` in `cyrusClean.py`. A string in the code
that carries a reference is reworded in the source itself; if Cyrus's database
already holds it (a history note), add the pair to `notes` in `cyrus.json` and
the update rewords it there.

When Cyrus has taken it:

```bash
python3 service/tools/buildCyrusUpdate.py --confirm && git commit -am "Cyrus has the update" && git push
```

That moves `cyrus.json` on, so the next update starts there. An update that is
never confirmed is simply included in the next one.

A new bake is copied to Cyrus as it is; the next `update.py` cuts the folders
its manifest names down to file names.

## On Cyrus

1. On GitHub, open `porting/update.py`, click in the code, **Ctrl+A**, **Ctrl+C**;
   paste into Notepad and save over `H:\pt-update\update.py` (Save as type: All files).
2. If the build said so, stop **isgPMGService**.
3. Run the **Proposal Tool update** run configuration (or `python H:\pt-update\update.py go`).
4. Start **isgPMGService** if it was stopped; **Ctrl+F5** on the page.

Run without `go` it only looks. It stops, changing nothing, if any file it would
replace was edited on Cyrus, naming the file. `python H:\pt-update\update.py undo`
puts back what the last `go` changed.

Two rules keep this simple: never edit the tool's files on Cyrus - fix them
here and send the update - and keep Cyrus's own settings in its run
configuration (`SCENARIO_ADAPTER=baked` on `isgPMGService`), not in the files.

A first install on a machine with no copy at all is `PORTING.md`;
`service/tools/buildPortBundle.py` builds its paste bundle when one is needed.
