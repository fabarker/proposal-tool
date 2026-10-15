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

When Cyrus has taken it:

```bash
python3 service/tools/buildCyrusUpdate.py --confirm && git commit -am "Cyrus has the update" && git push
```

That moves `cyrus.json` on, so the next update starts there. An update that is
never confirmed is simply included in the next one.

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
