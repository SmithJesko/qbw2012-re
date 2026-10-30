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

Applying this to the QuickBooks 2012 sample companies recovers the full database: table catalog, column definitions and rows. Invoices, bills, checks, deposits and journal entries export to SQLite, and every transaction's posting lines sum to zero, which checks the decoding without needing QuickBooks.

The full technical findings, with evidence, are in [NOTES.md](NOTES.md).

## Status

| Step | Status |
|---|---|
| Identify engine version and page layout | done |
| Undo the scrambling (`start` formula) | done |
| Undo the scrambling (`step`, statistical recovery) | working, ~98% of pages look clean |
| Closed-form `step` formula | open |
| Read the table catalog (names, ids) | done |
| Locate each table's pages, split rows | done (98.6% of rows check out) |
| Decode column values | done (invoices, bills, checks, customers, ...) |
| Export to SQLite, accounting self-checks | done (every transaction type balances) |
| One-step tool from the raw `.qbw` | done (`scripts/qbw2012.py`) |
| Test on more 2012 files | needs files |
| Port into OpenSQLAnywhere / OpenQBW | planned |

## Layout

- `NOTES.md`: numbered findings, evidence, open questions.
- `scripts/`: the Python analysis scripts (standard library only).
  - `pagecheck.py`: page trailer, page types, checksum.
  - `apfit.py`, `apdump.py`, `apmodel11.py`: fitting the scrambling pattern.
  - `apstep.py`: recover the per-file step table.
  - `decode11.py`: unscramble a whole file.
  - `systable_tag.py`, `catalog11.py`: find and list table catalog rows.
  - `rowpage11.py`: un-rotate row pages and check row boundaries.
  - `sector7.py`: learn the sector-7 correction rows.
  - `page11.py`, `schema11.py`: row pages, table schemas, typed row decoding.
  - `export11.py`: write every decodable table to SQLite.
  - `fill11.py`: fast fill recovery (key, steps, step repair).
  - `qbw2012.py`: the one-step tool.
- `data/`: format tables the tool needs.
- `out/`: generated tables (git-ignored).

## Ground rules

- **Clean room**: findings come only from analysing the files themselves, never from QuickBooks or SQL Anywhere software, SDKs or docs. That matches OpenQBW's contribution policy, so the work stays contributable.
- **No company files or decoded data in the repo.** `.qbw` files and decoded output are git-ignored.
- Read-only: this only reads copies of files the owner can already open.

## Usage

Work on a **copy** of the company file. Python 3, standard library only:

```bash
python3 -I scripts/qbw2012.py copy.qbw books.sqlite
```

This takes one to two minutes and writes one SQLite table per QuickBooks table, plus `_export_report` (exported rows against the row count the file's own catalog records). Money is exact decimal text and dates are ISO 8601. The runbook's SQLite-to-CSV/JSON loop works on the result as is.

`data/c7_sa11.json` is a format table (sector-7 correction rows, NOTES F9) learned from the two 2012 sample companies. It holds no company data.

The individual analysis scripts in `scripts/` reproduce each finding in NOTES.md.

## License

Apache-2.0, the same as OpenQBW, so findings and code can move upstream freely. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

QuickBooks(R) is a registered trademark of Intuit Inc. SQL Anywhere(R) is a registered trademark of SAP SE. This project is independent and not affiliated with either.
