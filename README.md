# qbw2012-re

Reverse engineering the QuickBooks Desktop **2012** company file (`.qbw`) format, so it can be read without QuickBooks.

[OpenQBW](https://github.com/Sigilweaver/OpenQBW) already reads QuickBooks 2018 and newer. QuickBooks 2012 files are built on an older database engine, and OpenQBW gets nothing out of them (0 tables, 0 transactions). This project works out the differences, with the aim of eventually contributing 2012 support upstream.

## The short version

A `.qbw` file is a database (Sybase/SAP SQL Anywhere) cut into 4 KB pages, with every page **scrambled**. The scrambling is simple, though. Each 512-byte chunk of a page has a counting pattern added to it:

```
stored byte = original byte + (start + position * step)      (wrapping at 256)
```

Undo the pattern and you get the original database back. The trick is figuring out `start` and `step` for every chunk of every page.

- **QuickBooks 2018+** (SQL Anywhere 17) has already been worked out by the OpenQBW project.
- **QuickBooks 2012** (SQL Anywhere 11) uses the same trick with different numbers. This project found that:
  - `start` = a single per-file key + the page number + the chunk number. One number per file unlocks the starting value of every chunk.
  - `step` comes from a per-file sequence. Its exact formula isn't known yet, but it can be recovered statistically, because databases are mostly zero bytes and the right `step` makes those zeros reappear.

Applying this to the QuickBooks 2012 sample companies recovers readable data: the database's internal table catalog, the company names ("Rock Castle Construction, Inc.", "Larry's Landscaping & Supply"), customers, jobs and report names.

The full technical findings, with evidence, are in [NOTES.md](NOTES.md).

## Status

| Step | Status |
|---|---|
| Identify engine version and page layout | done |
| Undo the scrambling (`start` formula) | done |
| Undo the scrambling (`step`, statistical recovery) | working, ~98% of pages look clean |
| Closed-form `step` formula | open |
| Parse SQL Anywhere 11 tables and rows | next |
| Port into OpenSQLAnywhere / OpenQBW | planned |

## Layout

- `NOTES.md`: numbered findings, evidence, open questions.
- `scripts/`: the Python analysis scripts (standard library only).
  - `pagecheck.py`: page trailer, page types, checksum.
  - `apfit.py`, `apdump.py`, `apmodel11.py`: fitting the scrambling pattern.
  - `apstep.py`: recover the per-file step table.
  - `decode11.py`: unscramble a whole file.
- `out/`: generated tables (git-ignored).

## Ground rules

- **Clean room**: findings come only from analysing the files themselves, never from QuickBooks or SQL Anywhere software, SDKs or docs. That matches OpenQBW's contribution policy, so the work stays contributable.
- **No company files or decoded data in the repo.** `.qbw` files and decoded output are git-ignored.
- Read-only: this only reads copies of files the owner can already open.

## Running the scripts

Work on a **copy** of the file. Python 3, no dependencies:

```bash
python3 -I scripts/pagecheck.py copy.qbw
python3 -I scripts/apstep.py copy.qbw other.qbw scripts/apmodel11.py out/steps.json   # ~3.5 min
python3 -I scripts/decode11.py copy.qbw <K> out/steps.json out/copy.plain
```
