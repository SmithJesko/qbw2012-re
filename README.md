# qbw2012-re

Reverse engineering the QuickBooks Desktop **2012** company file (`.qbw`) format, so it can be read without QuickBooks.

If you have an old QuickBooks 2012 company file and no working copy of QuickBooks, `scripts/qbw2012.py` exports its data to SQLite (standard-library Python, nothing to install), and `scripts/reports11.py` turns that into CSV reports you can open in Excel: trial balance, balance sheet, profit and loss, general ledger, and customer and supplier aging. See [Usage](#usage).

[OpenQBW](https://github.com/Sigilweaver/OpenQBW) already reads QuickBooks 2018 and newer. QuickBooks 2012 files are built on an older database engine, and OpenQBW gets nothing out of them (0 tables, 0 transactions). This project works out the differences.

## The short version

A `.qbw` file is a database (Sybase/SAP SQL Anywhere) cut into 4 KB pages, and every page is **scrambled**. Getting the data back takes three layers:

1. **Fill.** Each 512-byte chunk of a page has a counting pattern added to it:

   ```
   stored byte = original byte + (start + position * step)      (wrapping at 256)
   ```

   In QuickBooks 2012 files, `start` is one per-file key plus the page and chunk number. `step` comes from a per-file sequence with no known formula yet, but it can be recovered statistically, because databases are mostly zero bytes and the right `step` makes those zeros reappear.
2. **Rotation.** After the fill is removed, each chunk's contents are still rotated inside the chunk. The page's own number sits in its header, so finding it gives the rotation. Past a fixed point the rotation shifts by half a chunk; on SQL Anywhere 11 that rule is exact.
3. **Sector 7.** The last chunk of each page has one more additive term and a displaced 16-byte block, removed with correction rows learned from the files.

After that, the pages are ordinary SQL Anywhere row pages: a catalog of tables and columns, and rows that decode into dates, amounts and text, with long values chained across pages and moved rows leaving a forwarding stub. On top of that sits the QuickBooks accounting layer: transaction lines, inventory records that carry cost of goods sold, memorized templates that never post, and link records that apply payments and credits.

Applying this to the two QuickBooks 2012 sample companies recovers the full database, and the result checks out against numbers QuickBooks stores in the file itself:

| Check | Rock Castle | Larry's Landscaping |
|---|---|---|
| Rows exported (tables) | 65,747 (144) | 43,979 (134) |
| Rows that don't decode | 874 (1.3%), 4 in QuickBooks tables | 869 (1.9%), 0 in QuickBooks tables |
| Stored account balances reproduced from the exported transactions, to the cent | **144 / 144** | **107 / 107** |
| Stored customer balances reproduced | **147 / 147** | **65 / 65** |
| Stored vendor balances reproduced | **60 / 60** | **37 / 37** |
| Invoices whose open amount is zero exactly when the file marks them paid | 113 / 113 | 150 / 150 |
| Bills, the same | 197 / 197 | 106 / 106 |
| Transactions whose posting lines sum to zero | all, 14 types | all, 13 types |
| Tables whose exported row count equals the file's own catalog count | 96 / 105 | 90 / 99 |
| Report tie-outs (trial balance, balance sheet, P&L, ledger, AR/AP) | 8 / 8 | 8 / 8 |

Every QuickBooks table matches its catalog row count except two: Rock Castle's audit trail (23,746 of 23,748 rows) and Larry's `abmc_estimate_header` (the catalog says 7 rows, but only index pages for it are found). The other differences are SQL Anywhere's own system tables.

None of this needs QuickBooks to check, so the work stays clean-room. The evidence comes from three independent places: the file's own stored balances and paid flags, structural invariants (every row's stored length must equal the gap to the next row, every typed row must end exactly on its length), and 26 unit tests on synthetic data that run without any company file.

The full technical findings, with evidence, are in [NOTES.md](NOTES.md).

## Status

| Step | Status |
|---|---|
| Identify engine version and page layout | done |
| Remove the fill (`start` formula, `step` recovery) | done; a closed form for `step` is still open |
| Undo chunk rotation, including sector 7 | done |
| Read the catalog (tables, columns) | done |
| Decode rows, including long values stored outside the row and forwarded rows | done |
| Export to SQLite | done |
| Self-checks against stored account, customer and vendor balances | done; all match on both samples |
| Open items (payments and credits applied to invoices and bills) | done; agree with every paid flag on both samples |
| Accounting reports: trial balance, balance sheet, P&L, general ledger, AR/AP aging | done; all tie-outs pass on both samples |
| Unit tests independent of company files | done (26 tests) |
| One-step tool from the raw `.qbw` | done (`scripts/qbw2012.py`) |
| Test on more 2012 files | needs files |

## Limitations

- Tested on the two QuickBooks 2012 sample companies only. The bundled sector-7 table was learned from the same two files, so a new file is the real test; the self-checks will show whether it worked.
- About 1 to 2% of rows don't decode, all but 4 of them (on Rock Castle) in SQL Anywhere's own system tables. They are left out rather than guessed; `_export_report` lists them per table.
- Some SQL Anywhere system values (view and procedure sources) are stored compressed and aren't decoded.
- Older row versions that the database keeps around are not filtered out. `check11.py` reports any table that repeats its own id; none do on the two samples.
- The reports follow accounting rules inferred from the file (NOTES F16, F19) and haven't been compared with QuickBooks' own reports. The fiscal year start month isn't stored in a decodable place, so it's an option. Open items are as the file stands: applications carry no date of their own, so `--as-of` sets the aging date, not a historical AR/AP balance.

## Layout

- `NOTES.md`: numbered findings (F1 to F19), evidence, open questions.
- `scripts/`: the Python scripts (standard library only).
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
  - `ledger11.py`: ledger, names, account classes and open items, shared by the checks and reports.
  - `check11.py`: self-checks on an export.
  - `reports11.py`: accounting reports as CSV.
- `tests/`: unit tests on synthetic data: decoding primitives, and the ledger and reports on a small in-memory export (`python3 -I -m unittest discover -s tests`).
- `data/`: format tables the tool needs.
- `out/`: generated tables (git-ignored, as are `csv/`, `json/` and `*.csv`).

## Ground rules

- **Clean room**: findings come only from analysing the files themselves, never from QuickBooks or SQL Anywhere software, SDKs or docs.
- **No company files or decoded data in the repo.** `.qbw` files and decoded output are git-ignored.
- Read-only: this only reads copies of files the owner can already open.

## Usage

Work on a **copy** of the company file. Python 3, standard library only:

```bash
python3 -I scripts/qbw2012.py copy.qbw books.sqlite
```

This takes two to three minutes and writes one SQLite table per QuickBooks table, plus `_export_report`: one row per table with exported rows, rows that didn't decode, and the row count the file's own catalog records (tables where nothing decoded are listed too). Long text such as memos and notes is reassembled in full; binary long values are stored as BLOBs. It then runs the self-checks and prints, for example:

```
catalog: 96 / 105 tables match the SYSTABLE row count
transactions (posting lines sum to zero):
  abmc_bill_lineitem                       197 / 197
  abmc_invoice_lineitem                    113 / 113
  ...
accounts: 144 / 144 stored balances match (stored balances sum to 0.00)
customers: 147 / 147 stored balances match (stored balances sum to -93007.93)
vendors: 60 / 60 stored balances match (stored balances sum to 27594.55)
open items: 113 / 113 invoices are fully applied exactly when marked paid
open items: 197 / 197 bills are fully applied exactly when marked paid
open items: 147 / 147 customers' open AR items sum to their stored balance
duplicates: none (no table repeats its own id)
```

Money is compared as integer cents. Anything that doesn't match is listed with its stored and computed balance, and the exit status is 1; repeated ids are reported but don't change the exit status. `python3 -I scripts/check11.py books.sqlite` reruns the checks on an existing export. In the export, money is exact decimal text, dates are ISO 8601, and text is decoded as windows-1252, the file's character set. To dump every table to CSV:

```bash
mkdir -p csv
for t in $(sqlite3 books.sqlite "SELECT name FROM sqlite_master WHERE type='table'"); do
  sqlite3 -header -csv books.sqlite "SELECT * FROM \"$t\";" > "csv/$t.csv"
done
```

### Reports

```bash
python3 -I scripts/reports11.py books.sqlite --out reports
```

writes CSV files that open directly in Excel:

| File | Contents |
|---|---|
| `trial_balance.csv` | debit and credit per account as of `--as-of` |
| `balance_sheet.csv` | assets, liabilities, equity and current-year net income as of `--as-of` |
| `profit_and_loss.csv` | income, cost of goods sold, expenses, net income; one column per `--period` (month, quarter, year) plus a total |
| `general_ledger.csv` | every entry with date, type, number, customer/vendor/employee name, memo, debit, credit and running balance |
| `ar_open_items.csv`, `ar_aging.csv` | open invoices, payments and credits per customer, aged current / 1-30 / 31-60 / 61-90 / 91+ |
| `ap_open_items.csv`, `ap_aging.csv` | the same for vendors |

Accounts appear with their numbers and full names (`Construction Income:Labor Income`), customers as `Customer:Job`. Options: `--as-of`, `--from`, `--to`, `--period`, and `--fiscal-year-start MONTH` (default 1). Earlier fiscal years' income and expense close into Retained Earnings. Each run prints its tie-outs, for example:

```
  ok   trial balance: debits equal credits  (1082101.10 / 1082101.10)
  ok   balance sheet: assets equal liabilities + equity  (635033.93 / 635033.93)
  ok   P&L: net income equals the income and expense entries for the period  (113560.69)
  ok   P&L net income equals Net Income on the balance sheet  (113560.69)
  ok   general ledger: debits equal credits in the period  (1953680.83 / 1953680.83)
  ok   general ledger: closing balances equal the trial balance
  ok   AR open items: total equals the Accounts Receivable balance  (93007.93 / 93007.93)
  ok   AP open items: total equals the Accounts Payable balance  (26636.92 / 26636.92)
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
