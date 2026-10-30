# qbw2012-re

Reverse engineering the QuickBooks Desktop **2012** company file (`.qbw`) format, so it can be read without QuickBooks.

[OpenQBW](https://github.com/Sigilweaver/OpenQBW) already reads QuickBooks 2018 and newer. QuickBooks 2012 files are built on an older database engine, and OpenQBW gets nothing out of them (0 tables, 0 transactions). This project works out the differences, with the aim of eventually contributing 2012 support upstream.

## The short version

A `.qbw` file is a database (Sybase/SAP SQL Anywhere) cut into 4 KB pages, and every page is **scrambled**. Getting the data back takes three layers:

1. **Fill.** Each 512-byte chunk of a page has a counting pattern added to it:

   ```
   stored byte = original byte + (start + position * step)      (wrapping at 256)
   ```

   In QuickBooks 2012 files, `start` is one per-file key plus the page and chunk number. `step` comes from a per-file sequence with no known formula yet, but it can be recovered statistically, because databases are mostly zero bytes and the right `step` makes those zeros reappear.
2. **Rotation.** After the fill is removed, each chunk's contents are still rotated inside the chunk. The page's own number sits in its header, so finding it gives the rotation.
3. **Sector 7.** The last chunk of each page has one more additive term and a displaced 16-byte block, removed with correction rows learned from the files.

After that, the pages are ordinary SQL Anywhere row pages: a catalog of tables and columns, and rows that decode into dates, amounts and text.

Applying this to the two QuickBooks 2012 sample companies recovers the full database. Invoices, bills, checks, deposits and journal entries export to SQLite, and their posting lines balance (all transactions on one sample, all but one invoice on the other), which checks the decoding without needing QuickBooks.

The full technical findings, with evidence, are in [NOTES.md](NOTES.md).

## Status

| Step | Status |
|---|---|
| Identify engine version and page layout | done |
| Remove the fill (`start` formula, `step` recovery) | done; `step` formula still open |
| Undo chunk rotation, including sector 7 | done |
| Read the catalog (tables, columns) | done |
| Decode rows into values | done; 89.5% of all rows on Rock Castle, 100% of invoice headers, customers and accounts |
| Export to SQLite, accounting self-checks | done |
| One-step tool from the raw `.qbw` | done (`scripts/qbw2012.py`) |
| Test on more 2012 files | needs files |
| Port into OpenSQLAnywhere / OpenQBW | planned |

## Limitations

- Tested on the two QuickBooks 2012 sample companies only. The bundled sector-7 table was learned from the same two files.
- Long text stored outside the row (some memos and notes) exports as `<N bytes out of row>`, not the full text.
- Older row versions that the database keeps around are not filtered out yet.
- Rows that don't decode exactly are left out rather than guessed; `_export_report` lists how many per table.

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

This takes one to two minutes and writes one SQLite table per QuickBooks table, plus `_export_report` (exported rows against the row count the file's own catalog records). Money is exact decimal text and dates are ISO 8601. To dump every table to CSV:

```bash
mkdir -p csv
for t in $(sqlite3 books.sqlite "SELECT name FROM sqlite_master WHERE type='table'"); do
  sqlite3 -header -csv books.sqlite "SELECT * FROM \"$t\";" > "csv/$t.csv"
done
```

`data/c7_sa11.json` is a format table (sector-7 correction rows, NOTES F9) learned from the two 2012 sample companies. It holds no company data.

The individual analysis scripts in `scripts/` reproduce each finding in NOTES.md.

## Credits

- [OpenQBW](https://github.com/Sigilweaver/OpenQBW) and [OpenSQLAnywhere](https://github.com/Sigilweaver/OpenSQLAnywhere) by Nathan Riley: the SQL Anywhere 17 format work this builds on.
- pete-green's structural write-ups in [OpenSQLAnywhere#16](https://github.com/Sigilweaver/OpenSQLAnywhere/issues/16) (rotation, sector 7, page header) and [OpenQBW#22](https://github.com/Sigilweaver/OpenQBW/issues/22) (row format, column encodings, catalog layout), which this project confirmed on SQL Anywhere 11 and used for the row layer.

Analysis and code were developed with AI assistance.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

QuickBooks(R) is a registered trademark of Intuit Inc. SQL Anywhere(R) is a registered trademark of SAP SE. This project is independent and not affiliated with either.
