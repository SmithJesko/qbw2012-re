# QuickBooks 2012 / SQL Anywhere 11 `.qbw` notes

Goal: extend OpenQBW / OpenSQLAnywhere (tested on QB 2018+, SA 17.0.4) to read QuickBooks 2012 files.
Corpus so far: the two QB 2012 sample companies in `../reference/QuickBooks/Sample Company Files/QuickBooks 2012/`.
Work on copies only (the scripts were run against copies in the scratchpad).

Findings are kept in the order they were made. Where a later finding corrected an entry, the entry is fixed and says so; where a later finding went further, the entry ends with a **Later:** line pointing to it. Current results are in F18 and F19 and in the README.

## Findings (2026-10-30)

### F1. Same container family, older engine
- Page 0 superblock matches the SA17 layout: `0xDA7ABA5E` magic at 0x14, `format_major`=3, `version_a`=201, `version_b`=12, collation block (`1252LATIN1`, `windows-1252`, `UCA`, `UTF-`) at 0x16C+.
- Engine fingerprint: `"Sybase Inc., Copyright (c)2000 11.0.1.2250 "` rolled from 0x2C4 to the end of page 0 (SA17: `"2182 SAP SE, Copyright (c)2015 17.0.4."` from 0x400).
- Second `0xDA7ABA5E` at 0x2B8.
- 4096-byte pages; file size is a multiple of 4096.

### F2. Page trailer: same layout, numeric page type, no CRC
- `0xFF3` = 0 and `0xFF6..0xFFB` = 0 on every page except page 0 (`scripts/pagecheck.py`).
- Page type at `0xFF2` is the SA17 letter with bit 0x40 cleared: `0`=`@`, `1`=`A`, `3`=`C`, `5`=`E`, `7`=`G`, `8`=`H`, `9`=`I`, `0xd`=`M`. Mask with `& 0xBF` to normalise.
- The CRC-32 footer at `0xFFC` does **not** validate (11 / 7733 pages pass). SA11 doesn't checksum pages by default. Any integrity check keyed on it must be skipped for SA11.
- Histogram (Rock Castle): `5`:4064, `1`:2445, `7`:1138, `3`:71, `d`:5, `0`:4, `8`:3, `9`:2. There's far more type `7` (`G`) than SA17's 0.02%.

### F3. Obfuscation: same additive AP keystream, different parameters
`stored[i] = (base + i*step + plain[i]) mod 256` per 512-byte sector (sector 7 stops at 0xFF0; trailer is not obfuscated).

- **base = (K + pn + si) mod 256**, one constant `K` per file. Holds on 99.7% of pure-fill sectors (6965/6986 Rock Castle, K=45; 5692/5710 service sample, K=243). There's no SA17-style `bv(block)` or `-4*floor((pn%16)/2)` term.
- **step = T[(pn + si + 1) // 2]**: one step per "slot", shared by the two consecutive `pn+si` indices. Steps are (nearly) all odd. `T` is a per-file pseudo-random sequence.
  - Rock Castle T[3..8] = 97, 219, 205, 7, 217, 19; service T[3..8] = 161, 255, 181, 243, 55, 25.
  - Cross-file: `A[k+s] == B[k]` matches ~44% at s = 899, 963, 1027, 1091 (spacing 64). That points to a 64-periodic component (table or generator), so a closed form looks findable but isn't found yet.
- Practical recovery without the closed form: with `K` known, for each slot pick the odd step maximising AP hits summed over all sectors in that slot (`scripts/apstep.py`, ~3.5 min in Python). This agrees with pure-fill-learned steps on 1517/1522 slots.

**Later:** F13 replaces this with per-byte voting (1.6 s) plus a step repair.

### F4. Decoded content
`scripts/decode11.py` (K + recovered step table) yields readable SA11 structures and business data:
- Catalog index names `ISYSTABLE`, `ISYSDOMAIN`, `ISYSIDXCOL`, `ISYSDBSPACE`, etc.; columns `foreign_table_id`, `primary_column_id`, `allocation_bitmap`, `long_value_bitmap`.
- `sample_product-based business.qbw` **is Rock Castle Construction** ("Rock Castle Construction, Inc.", customer:job names, report names like "FY2016-17 - Profit & Loss by Account").
- `sample_service-based business.qbw` is **Larry's Landscaping & Supply**; it decodes the same way with K=243 (step recovery agrees on 1200/1204 pure-learned slots; `out/steps_service.json`).
- Decode quality proxy: ~2% of pages (155/7733) have <5% zero bytes, so they're either dense data or slots whose step was recovered wrong. Some strings show local corruption.

**Later:** the corruption was mostly not the fill. It came from the per-sector rotation (F7, F15) and sector 7 (F9).

### F5. The SYSTABLE "fixed record tag" is a timestamp
OpenQBW's SPECIFICATION.md section 5 treats `b1 0d 19 0d` (8 bytes before `table_name`) as a fixed tag. In SA11 files the same 4 bytes vary per row, always end in `0x0c`, and are a `u32_LE` count of **minutes since 1600-02-29** (the DATE / TIMESTAMP epoch, F11; this entry first used 1600-01-01, which put every date 59 days early):

| Value | Where | Decodes to |
|---|---|---|
| `2c68d60c` | most Rock Castle catalog rows | 2009-08-31 00:44 |
| `2d68d60c` | Rock Castle superblock 0x2A | 2009-08-31 00:45 |
| `f32fd60c` | Larry's superblock 0x2A and most rows | 2009-08-21 00:51 |
| `1bb2e50c` | `abmc_invoice_header` | 2011-07-27 20:11 |
| `b10d190d` | OpenQBW's SA17 "fixed tag" | 2017-12-20 04:33 |

Across both files the row stamps run from 2009-08 to 2013-10. So the superblock `u32` at 0x2A is the database creation time, and the row value is a per-row change time. The SA17 value probably looks constant because the upstream corpus files were created in the same era. OpenQBW's `systable.rs` already accepts any value there (it calls it a per-database salt), so this is a spec correction, not a parser bug (`scripts/systable_tag.py`).

### F6. SYSTABLE rows: same anchor as SA17, different trailer
Tag shape matches SA17 apart from F5: `05 00 00 00 <tid u32> 00 00 00 00 <stamp u32> 00 00 00 00 <len u8> <name>`. Rock Castle: 716 tagged rows, 448 distinct names (older row versions repeat; keep the newest stamp). There are 115 `abmc_*` tables, 84 `v_*` views and 48 `ISYS*` system tables.

Offsets relative to the tag (`scripts/catalog11.py`):
- **-30 `u32` table_id**: same position as SA17 (OpenQBW SPECIFICATION.md 5.1). Distinct and sequential across `abmc_*` (726, 727, 728, ...).
- **-24 `u32` row count**: `abmc_invoice_header` 113, `abmc_invoice_lineitem` 1238. Confirmed in F8.
- **-12 `u32` page count**: `abmc_invoice_header` 6, `abmc_invoice_lineitem` 86. Confirmed in F8.
- **-16**: constant `65 00 00 00`.
- **name+6**: not a column count (SA17's position); mostly 16, values up to 32874.
- SA17's `data_root_page` (name+34) and `last_page` (name+50) do not hold here. Not needed: row pages name their owner table (F7).

### F7. Row pages: per-sector rotation and page header (confirms OpenSQLAnywhere#16 on SA11)
pete-green's structural write-up in OpenSQLAnywhere issue #16 (from QuickBooks Enterprise 24.0) holds on SA11 too. After removing the additive fill, each sector is rotated inside its 512-byte frame:
- `r0` = position of the `u32_LE` page number in decoded sector 0. On Rock Castle 2012 it appears exactly once on 4055 of 4064 `E` pages, always at an odd position.
- `rot_s = (r0 - 2 * ((s + q) // 2)) mod 512` for sectors 0..6, with `true[j] = decoded[(j + rot_s) mod 512]`.
- **SA11 pairing phase**: `q = 0` on odd pages and `q = 1` on even pages. This follows from F3 (`step` is shared by `pn + s` pairs `(2k-1, 2k)`), and is simpler than #16's per-32-page-group rule.
- Header (logical offsets): byte 16 = `0x04` on row pages (709 pages have `0x0d` instead: index pages, F10), `u16 @22` slot count, `u32 @24` owner table id (= SYSTABLE -30 field), slot array at +28 with values relative to +28.
- Check: each row starts with a `u16` length equal to the gap to the next row start. With the parity rule 98.6% of rows chain on Rock Castle (80984 / 82172, sectors 0..6) and 98.1% on Larry's (65528 / 66783); fixed `q=0` or `q=1` gives 74-75%. Larry's: page number found once on 2883 / 2889 `E` pages, always odd.
- Sector 7 (extra additive term and displaced block per #16) is not modelled here.

**Later:** sector 7 in F9; the rotation gets a +256 flip below 42 (F15).

`scripts/rowpage11.py`.

### F8. SYSTABLE counts confirmed from the row pages
Grouping row pages by owner table id (F7) and counting live slots, compared against the SYSTABLE fields from F6 (128 non-empty tables, Rock Castle 2012):
- **-24 = row count**: exact match on 97 tables.
- **-12 = page count**: exact match on 95 tables (`abmc_invoice_header` 6 pages, `abmc_invoice_lineitem` 86).
- Mismatches come from old page versions or stale catalog rows (`ISYSVIEW` 125 pages found vs 6), tables whose pages aren't `0x04` kind (`abmc_customer_user`, `abmc_received_payment_header`: 0 pages found), and partial coverage of the largest table (`abmc_audit_lineitem` 378 of 1478 pages).

**Later:** the missing tables' pages are trailer type `7`, not type `5` (F10); with them, the audit trail is 1476 of 1478 pages.

### F9. Sector 7 on SA11 (OpenSQLAnywhere#16 section 4)
The sector-7 model holds: after the per-sector fill, sector 7 still carries `d * C[r7](i)` with odd `d` and `C` in 0..16, then a rotation over the 512-byte unit that includes the plaintext trailer, with a 16-byte displaced block.
- `r7 = rot6` on odd pages, `rot6 - 2` on even pages (F7 parity rule).
- `C[r7]` is format-wide: rows learned from the two 2012 samples never conflict. 79 rows are exact (pages whose sector 7 is all `d * C`), 50 more come from per-position voting, then a second vote using each page's own `d` (`scripts/sector7.py`, bundled as `data/c7_sa11.json`). That covers the 129 `r7` values in use.
- `d` drifts across the file (123 to 151 on Rock Castle, around 201 on Larry's), so it's chosen per page from all 128 odd values: most rows chaining in sector 7, then most zero bytes. With the fixed 6 most common values only 38% of sector-7 rows chained; per-page choice gives 95%.
- Voting with the wrong `d` left single-byte errors in voted rows (sawtooth breaks such as `15 11 6 3`) that hid column definitions; the second vote removed them.

**Later:** `r7` also takes the +256 flip, which leaves 49 voted rows and 128 `r7` values in use; `d` ties are broken by plausibility, not zero count (F15).

### F10. Row pages, schema and typed rows
- Row pages are trailer types `5` and `7` (`E`, `G`). Type `7` was 1138 pages on Rock Castle: most of the audit trail and all of `abmc_customer_user`. Header byte 16 is `0x04`; `0x0d` / `0x0a` pages hold page-number lists (index pages), and type `1` pages have byte 16 = `0x05` (not decoded).
- ISYSTAB (owner 1) and ISYSTABCOL (owner 2) rows match OpenQBW#22 section 5 byte for byte. ISYSTAB gives 593 names on Rock Castle. Some column definitions sit in sector 7 of catalog pages (`abmc_invoice_header` ids 15 to 23 and 57, for example), so the schema is only complete once F9 works.
- Rows follow OpenQBW#22 sections 1 to 3: `u16 len, u8 flags, null bitmap (ceil(nullable / 8), MSB first, set = present), values in column_id order`; NUMERIC is base-100, text is `u8 len + bytes`.
- Exact typed walks (cursor ends on the row length): 89.5% of rows overall on Rock Castle; 100% for `abmc_invoice_header` (113), `abmc_customer_user` (147), `abmc_account_user` (144); 1237 / 1238 invoice lines. Failures are mostly SA system tables (`ISYSCOLSTAT`, `ISYSVIEW`, `ISYSSOURCE`).
- Stale step-table entries exist: pages 1963 to 1966 have no page-number anchor because the recovered `step` for slots 982 and 983 is wrong (65 and 31 fit, with the expected `base`). A header-validated step search would fix these.

**Later:** that search is the step repair in F13; F15 and F17 raise the share of rows that decode.

### F11. Date epoch is 1600-02-29, not 1600-01-01 (corrects F5)
OpenQBW#22 gives minute 0 = 1600-02-29. Invoice rows confirm it from the file alone: the plain integer `timestamp_tms` (Unix seconds, 933288149 = 1999-07-29 22:42:29 UTC) equals `db_created_tms` (domain 13) read with that epoch, to the second, on every row checked. The F5 dates are therefore 59 days late: the Rock Castle database creation stamp is 2009-08-31, not 2009-07-03. The F5 finding (the field is a timestamp, superblock 0x2A is the creation time) is unchanged.

### F12. Export and accounting self-checks
`scripts/export11.py` writes every decodable table to SQLite (exact decimal text for NUMERIC, ISO 8601 for dates) plus an `_export_report` table. Checks that need no outside reference:
- Exported row count equals the SYSTABLE row count (F6 -24 field) for 88 of 101 tables on Rock Castle and 72 of 82 on Larry's.
- Posting lines (non-null `account_id`) sum to zero per transaction:

| | Rock Castle | Larry's |
|---|---|---|
| invoices | 112 / 113 | 161 / 161 |
| bills | 197 / 197 | 111 / 111 |
| checks | 233 / 233 | 60 / 60 |
| deposits | 88 / 88 | 46 / 46 |
| general journal | 66 / 66 | 20 / 20 |
| sales receipts | 8 / 8 | 21 / 21 |

The one Rock Castle invoice that doesn't balance is missing its single undecoded line row.

**Later:** with memorized templates left out and F15 decoding the last rows, every transaction balances on both files (F16, F18).

### F13. Fast step recovery
For a byte at odd index `i`, the step that makes it zero is unique: `(stored - base) * inverse(i) mod 256`. Voting with that per slot (`scripts/fill11.py`) takes 1.6 s on Rock Castle instead of about 3.5 minutes, and agrees with the slow table on 3865 / 3870 slots. Slots that still hide a row page's anchor are repaired by searching the step that gives exactly one valid header (6 slots on Rock Castle, 0 on Larry's).

### F14. Out-of-row long values
OpenQBW#22 sections 2.3 and 4 hold on SA11, with one refinement.
- A long column value with first byte `b & 0x80` (not `0xFF`) is `k = b & 0x7F` prefix bytes plus a 13-byte descriptor `[00][u32 total][u32 page][u16 ?][u16 slot]`. On Rock Castle 7866 of 7868 descriptors point at a page of kind `0x11..0x14` owned by the same table.
- Chunk rows: `u16 len, u8 flags, u32 next_page if flags & 4, data`. Following the chain gives exactly `total` bytes for 6504 values and more than `total` for 1295 (trim); 69 chains are short or broken (mostly ISYSCOLSTAT).
- **The in-row prefix is a copy of the value's first `k` bytes; the chain holds the whole value.** So the value is `chain[:total]`, not `prefix + chain`.
- Domains 9 and 10 hold text (memos, `abmc_notepad.data_fld` customer notes); 12 and 28 hold binary (`ABMC_TSAFE.ADMIN_KEY`, `abmc_opaque_storage.blob`, ISYSCOLSTAT histograms). Some SA system long values look compressed, as #22 also saw.
- Pages of kind `0x11..0x14` also hold ordinary rows. A row there is a chunk exactly when a descriptor points at it; the others are table rows. Counting them fixes row counts that were short, e.g. `es_paycheck_header` 67 + 12 = 79, `abmc_vendor_user` 50 + 10 = 60, both matching SYSTABLE. Catalog row-count matches: 90 / 101 tables on Rock Castle, 74 / 83 on Larry's.

**Later:** after F15 no chain is short or broken on either file; current catalog matches are in F18.

### F15. Rotation flip (+256) is exact on SA11
OpenSQLAnywhere#16 section 1.4 describes the true rotation sometimes being the prediction + 256 "when the predicted rotation is small", with probabilities. On SA11 it is deterministic. Over both samples, every sector where row chaining decides between the two (pred and pred + 256) flips when pred <= 41 and keeps when pred >= 43 (rotations are odd), with no exceptions (thousands of sectors; e.g. 242 flips at 41, 261 keeps at 43). So:

    rot_s = pred_s + 256 if pred_s < 42 else pred_s

Because pred drops by 2 per sector pair, the flip starts at some sector and persists to the end of the page, which is the pattern #16 reports. A page whose anchor `r0` is already >= 256 (e.g. 297) is the same rule applied to sector 0. Sector 7 follows it too: `r7 = rot6` (odd pages) or `rot6 - 2` (even pages), then +256 if below 42 (22 pages on Rock Castle had `r7 = 41`; with 297 instead, 20 of 21 rows decode on one of them instead of 17).

With the rule, every row that starts and ends in sectors 0..6 chains on Rock Castle (82395 / 82395, `scripts/rowpage11.py`), against 98.6% in F7.

Before this rule, pages 2509..2514 on Rock Castle lost rows along a diagonal (one more broken sector per page), which looked like a bad step slot but wasn't: neither step nor base changes helped. With it, Rock Castle exports 65747 rows (57879 before) and Larry's 43979 (40904), and no long value is left incomplete.

The sector-7 multiplier `d` is now chosen by row chaining and then by plausibility (0x00 and `'0'` +2, printable +1, >= 0x80 -1, from #16 follow-up 1) instead of zero count, which resolves `d + 64k` aliases that hid single rows.

### F16. The export reproduces every stored account balance
Each account row (`abmc_account_internal.ending_balance_amt`) stores a running balance. Summing the exported posting lines per account reproduces it to the cent for **144 / 144 accounts on Rock Castle and 107 / 107 on Larry's**; the stored balances also sum to 0.00 on both. What the ledger needs (`scripts/check11.py`):
- posting lines: `*_lineitem` rows with `account_id` set, `amount_amt` as the amount; debits are negative;
- leave out estimates, sales orders, purchase orders and the audit trail (history copies);
- leave out memorized transactions (`is_memorized_transaction_bool`, recurring templates) and `is_no_post_bool` lines. Larry's memorized bills (insurance, utilities, loan interest) otherwise double several expense accounts;
- cost of goods sold posts through inventory records, `*_inventory_lineitem` (paired rows on the inventory asset and COGS accounts), not through the lines' `cogs_amt`. On Rock Castle invoice + bill inventory records (19372.53 + 4368.51) give exactly the 23741.04 COGS the balances need.

Every transaction's posting lines also sum to zero on both files (14 transaction types on Rock Castle, 13 on Larry's). The last mismatches before F15 were all rows in sector 7 that hadn't decoded.

### F17. Forwarded rows
A 9-byte row with flags `0x04` is a forwarding stub: `u16 len = 9, u8 flags, u32 page, u16 slot`, e.g. `09 00 04 1c 17 00 00 0c 00` points at page 5916, slot 12. The moved row sits on a kind `0x11..0x14` page of the same table and decodes normally there (F14 already exports it), so the stub is skipped. On Rock Castle these were 83 `es_paycheck_lineitem`, 12 `es_paycheck_header` and 10 `abmc_vendor_user` rows that had been counted as failures.

### F18. Export and check hardening; current results
- Text decodes as windows-1252 (F1's character set) instead of latin-1. The 5 bytes cp1252 leaves undefined (0x81, 0x8D, 0x8F, 0x90, 0x9D) fall back to latin-1, so the decode stays lossless (`schema11.decode_text`, covered by `tests/test_decode.py`).
- `_export_report` now has a row for every table that exported rows, failed rows, or has a SYSTABLE row count (catalog ids must be named in ISYSTAB). Tables where nothing decoded used to be left out, which hid them from the catalog check: the denominators grow from 102 to 105 (Rock Castle) and from 95 to 99 (Larry's).
- `check11.py` sums money as integer cents from the exported decimal text. Account and transaction results are unchanged: 144 / 144 and 107 / 107 accounts match, and every transaction balances.
- Duplicate check: no table repeats its own id on either sample, so the older row versions the database keeps (kinds 1 and 2) don't produce duplicate rows in the export. The id column is chosen by name, because ISYSIDX / ISYSIDXCOL (the index catalog, which would give the real primary keys) don't decode yet; taking each table's first column instead flagged 19 tables whose first column is a reference (`abmc_role_action.company_id`, `abmc_item_history.item_id`).
- `abmc_estimate_header` on Larry's: SYSTABLE says 7 rows, but the only pages carrying its owner id (768) are 13 type-1 pages with header byte 16 = 0x05, the index page format. No row pages and no failed rows, so either the catalog count is stale or the rows live in a page format not read yet.

Current results with `scripts/qbw2012.py`:

| | Rock Castle | Larry's |
|---|---|---|
| Rows exported (tables) | 65747 (144) | 43979 (134) |
| Rows not decoded (in `abmc_*` / `es_*` tables) | 874 (4) | 869 (0) |
| Accounts matching stored balance | 144 / 144 | 107 / 107 |
| Catalog row-count matches | 96 / 105 | 90 / 99 |
| Repeated own ids | none | none |

### F19. Customer, vendor and open-item balances; account classes
**Account classes.** `abmc_account_user.type` (not `account_type`, which is 0 on every account) classifies accounts. The meaning of each code is inferred from the names of the accounts that carry it, on both samples:

| type | accounts | type | accounts |
|---|---|---|---|
| 0 | Checking, Savings, Petty Cash | 8 | loans, mortgage, notes payable |
| 1 | Accounts Receivable | 9 | Opening Bal Equity, Retained Earnings, Capital Stock |
| 2 | Inventory Asset, Undeposited Funds, Pre-paid Insurance | 10 | income accounts |
| 3 | vehicles, buildings, land, accumulated depreciation | 11 | Cost of Goods Sold, job expenses |
| 4 | Security Deposits | 12 | expense accounts |
| 5 | Accounts Payable | 13 | Interest Income, Other Income |
| 6 | credit cards | 14 | Other Expenses |
| 7 | payroll liabilities, Sales Tax Payable | 15 | Estimates, Sales Orders, Purchase Orders |

**Customers.** Ledger entries on type-1 accounts, summed per `customer_id`, reproduce `abmc_customer_internal.end_balance_amt` for 147 / 147 customers (Rock Castle) and 65 / 65 (Larry's).

**Vendors.** A vendor's `end_balance_amt` is its type-5 (Accounts Payable) entries plus, for a sales tax agency, the sales tax owed to it: entries on type-7 accounts whose tax item's `abmc_item_user.tax_agency_id` names the vendor. That gives 60 / 60 and 37 / 37. Using the line's own `vendor_id` for the sales tax part gives only 53 / 60 and 34 / 37.

**Open items.** `abmc_transaction_link` records applications (payments, credits) between lines, with `amount_amt`. Three filters are needed:
- both `target_1_id` and `target_2_id` must be lines of real transactions. On Rock Castle 1848 of the 2823 type-4 links point at audit-trail copies of payment lines, which doubles every application otherwise;
- drop `is_secondary_bool` links (a bill paid by check also links the check to the bill's expense line);
- keep one link per `(link_type, target_1_id, target_2_id)`: identical repeats under different `link_id`s are older row versions.

Then `open = sign(amount) * (|amount| - |applied|)` per transaction, where `amount` is its AR (or AP) total and `applied` the sum of its links of the application types. Applications reduce what is open in either direction, which handles bill credits and credit memos without per-type signs. The application types, found by testing each link type against the file's own `is_paid_bool` flags: AR 4 and 5 (2 also joins a refund check to the credit memo it refunds), AP 6, 8 and 16. Results on both samples:
- invoices: 113 / 113 and 150 / 150 have no open amount exactly when `is_paid_bool` is set (Larry's 11 memorized invoices are templates and excluded);
- bills: 197 / 197 and 106 / 106;
- every customer's open AR items sum to its stored balance (147 / 147, 65 / 65); AR and AP open totals equal the AR and AP account balances.

**Not found:** the fiscal year start month (no decodable column names it; company settings sit in compressed values), so it is a report option. Some invoice due dates are a year past their date plus terms (invoice 1064: dated 2016-10-05, due 2017-10-20); they are reported as stored.

**Reports** (`scripts/reports11.py`, rules in `scripts/ledger11.py`): trial balance and balance sheet as of a date, P&L by month, quarter or fiscal year, general ledger detail with account, customer, vendor and employee names, AR/AP open items and aging. Earlier fiscal years' income and expense close into Retained Earnings. Each report checks itself: trial balance debits equal credits; assets equal liabilities plus equity; P&L net income equals both the period's income and expense entries and the balance sheet's Net Income line; general ledger closing balances equal the trial balance; AR and AP open items total to the AR and AP balances. All pass on both samples, for monthly, quarterly and July-start fiscal years.

## Open questions / next steps
1. Closed form (or table + key derivation) for `T[k]`, and how `K` relates to the superblock (candidates: `file_id_lo` @0x08, 0x20..0x2C, 0x2BC..). That would remove the brute-force pass.
2. `abmc_estimate_header` on Larry's (F18). Other row-count gaps are outside the business data: SA system tables (`ISYSVIEW`, `ISYSSOURCE`, `ISYSDEPENDENCY`, `ISYSCOLSTAT`), internal `I_*` tables, and 2 of 23748 audit rows on Rock Castle. Some SA system long values look compressed and aren't decoded. Decoding ISYSIDX / ISYSIDXCOL would give real primary keys for the duplicate check.
   Link types other than the application types (9, 11, 12, 15, 17 to 21) aren't characterised.
3. Upstream: F5 and F11 are posted as OpenQBW #45 (with the corrected epoch), and F7 as a comment on OpenSQLAnywhere #16.
4. Test `scripts/qbw2012.py` on a third 2012 file: the bundled sector-7 table was learned from the same two samples it's checked against.
5. Possible port into OpenSQLAnywhere: version detection from the page-0 fingerprint, an SA11 fill model, page-type masking, CRC optional, the rotation flip per version.
