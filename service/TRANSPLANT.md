# Transplant rehearsal — superseded

The file-by-file porting list that used to live here now lives in **`../PORTING.md`**, which is the
single porting guide and is written against commit `9f4dc50` (2026-09-03). This file is kept only
so that existing references to `service/TRANSPLANT.md` still land somewhere; do not add to it, and
do not maintain the two side by side — that is how the previous version drifted.

Where each of the old sections went:

| Old section | Now |
|---|---|
| §1 The page folder — COPY | `PORTING.md` §3.1, §6 (COPY), §10 |
| §2 The scenario package — COPY, and what the host swaps later | `PORTING.md` §3.2, §3.6, §6, §11.4, §11.5 |
| §3 The router endpoints — INSERT | `PORTING.md` §3.3, §9.1, §11.1 |
| §4 The Flask route + nav link — INSERT | `PORTING.md` §10.2, §10.3 |
| §5 Stand-ins — NOT COPIED | `PORTING.md` §3.4, §3.5, §6 (EXCLUDE) |
| §6 Configuration — the complete list | `PORTING.md` §11.4 (twenty variables, with what the host must set) |

The governing test is unchanged — what has to change must be configuration, never structure — but
the honest answer at `9f4dc50` is in `PORTING.md` §6: two verbatim copies, four inserts (the router
block, one Flask route, one nav link, and three names in `accessControl.py`), configuration, and
four data deliveries. The evidence is in `../PORTING_GUIDE_AUDIT.md`.
