# The paste bundle

Everything a Cyrus update copies - the `scenario` package, the page folder, the
router, `portCheck.py`, `PORTING_UPDATE.md` and `requirements.txt` - zipped and
written as text, because the Cyrus machine reaches GitHub only through a
browser. **`PORTING_UPDATE.md`, step 1, is the procedure.**

Built from commit `fb14dd7` of 2026-10-06: 52 files in 6 parts.
Copy each of these into one folder on the Cyrus machine, under exactly this
name, then run `python unbundle.py` there:

| File | Size |
|---|---|
| `unbundle.py` | 4 KB |
| `part-01.txt` | 198 KB |
| `part-02.txt` | 198 KB |
| `part-03.txt` | 198 KB |
| `part-04.txt` | 198 KB |
| `part-05.txt` | 198 KB |
| `part-06.txt` | 173 KB |

`unbundle.py` checks every part against the hash it was built with and names
any part to paste again; nothing is unpacked until all of them match.

Rebuild after any change, before a port: `python3 service/tools/buildPortBundle.py`.
