# QuickBooks 2012 / SQL Anywhere 11 `.qbw` notes

Goal: extend OpenQBW / OpenSQLAnywhere (tested on QB 2018+, SA 17.0.4) to read QuickBooks 2012 files.
Corpus so far: the two QB 2012 sample companies in `../reference/QuickBooks/Sample Company Files/QuickBooks 2012/`.
Work on copies only (the scripts were run against copies in the scratchpad).

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

### F4. Decoded content
`scripts/decode11.py` (K + recovered step table) yields readable SA11 structures and business data:
- Catalog index names `ISYSTABLE`, `ISYSDOMAIN`, `ISYSIDXCOL`, `ISYSDBSPACE`, etc.; columns `foreign_table_id`, `primary_column_id`, `allocation_bitmap`, `long_value_bitmap`.
- `sample_product-based business.qbw` **is Rock Castle Construction** ("Rock Castle Construction, Inc.", customer:job names, report names like "FY2016-17 - Profit & Loss by Account").
- `sample_service-based business.qbw` is **Larry's Landscaping & (Garden) Supply**; it decodes the same way with K=243 (step recovery agrees on 1200/1204 pure-learned slots; `out/steps_service.json`).
- Decode quality proxy: ~2% of pages (155/7733) have <5% zero bytes, so they're either dense data or slots whose step was recovered wrong. Some strings show local corruption.

### F5. The SYSTABLE "fixed record tag" is a timestamp
OpenQBW's SPECIFICATION.md section 5 treats `b1 0d 19 0d` (8 bytes before `table_name`) as a fixed tag. In SA11 files the same 4 bytes vary per row, always end in `0x0c`, and are a `u32_LE` count of **minutes since 1600-01-01**:

| Value | Where | Decodes to |
|---|---|---|
| `2c68d60c` | most Rock Castle catalog rows | 2009-07-03 00:44 |
| `2d68d60c` | Rock Castle superblock 0x2A | 2009-07-03 00:45 |
| `f32fd60c` | Larry's superblock 0x2A and most rows | 2009-06-23 00:51 |
| `1bb2e50c` | `abmc_invoice_header` | 2011-05-29 20:11 |
| `b10d190d` | OpenQBW's SA17 "fixed tag" | 2017-10-22 04:33 |

So the superblock `u32` at 0x2A is the database creation time, and the row value is a per-row change time. The SA17 value only looks constant because the upstream corpus shares one 2017 template. OpenQBW's `systable.rs` already accepts any value there (it calls it a per-database salt), so this is a spec correction, not a parser bug (`scripts/systable_tag.py`).

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
- Header (logical offsets): byte 16 = `0x04` on row pages (709 pages have `0x0d` instead, not handled yet), `u16 @22` slot count, `u32 @24` owner table id (= SYSTABLE -30 field), slot array at +28 with values relative to +28.
- Check: each row starts with a `u16` length equal to the gap to the next row start. With the parity rule 98.6% of rows chain on Rock Castle (80984 / 82172, sectors 0..6) and 98.1% on Larry's (65528 / 66783); fixed `q=0` or `q=1` gives 74-75%. Larry's: page number found once on 2883 / 2889 `E` pages, always odd.
- Sector 7 (extra additive term and displaced block per #16) is not modelled yet.

`scripts/rowpage11.py`.

### F8. SYSTABLE counts confirmed from the row pages
Grouping row pages by owner table id (F7) and counting live slots, compared against the SYSTABLE fields from F6 (128 non-empty tables, Rock Castle 2012):
- **-24 = row count**: exact match on 97 tables.
- **-12 = page count**: exact match on 95 tables (`abmc_invoice_header` 6 pages, `abmc_invoice_lineitem` 86).
- Mismatches come from old page versions or stale catalog rows (`ISYSVIEW` 125 pages found vs 6), tables whose pages aren't `0x04` kind (`abmc_customer_user`, `abmc_received_payment_header`: 0 pages found), and partial coverage of the largest table (`abmc_audit_lineitem` 378 of 1478 pages).

### F9. Sector 7 on SA11 (OpenSQLAnywhere#16 section 4)
The sector-7 model holds: after the per-sector fill, sector 7 still carries `d * C[r7](i)` with odd `d` and `C` in 0..16, then a rotation over the 512-byte unit that includes the plaintext trailer, with a 16-byte displaced block.
- `r7 = rot6` on odd pages, `rot6 - 2` on even pages (F7 parity rule).
- `C[r7]` is format-wide: rows learned from the two 2012 samples never conflict. 79 rows are exact (pages whose sector 7 is all `d * C`), 50 more come from per-position voting, then a second vote using each page's own `d` (`scripts/sector7.py`, `out/c7.json`). That covers the 129 `r7` values in use.
- `d` drifts across the file (123 to 151 on Rock Castle, around 201 on Larry's), so it's chosen per page from all 128 odd values: most rows chaining in sector 7, then most zero bytes. With the fixed 6 most common values only 38% of sector-7 rows chained; per-page choice gives 95%.
- Voting with the wrong `d` left single-byte errors in voted rows (sawtooth breaks such as `15 11 6 3`) that hid column definitions; the second vote removed them.

### F10. Row pages, schema and typed rows
- Row pages are trailer types `5` and `7` (`E`, `G`). Type `7` was 1138 pages on Rock Castle: most of the audit trail and all of `abmc_customer_user`. Header byte 16 is `0x04`; `0x0d` / `0x0a` pages hold page-number lists (index pages), and type `1` pages have byte 16 = `0x05` (not decoded).
- ISYSTAB (owner 1) and ISYSTABCOL (owner 2) rows match OpenQBW#22 section 5 byte for byte. ISYSTAB gives 593 names on Rock Castle. Column definitions added by later releases live in sector 7 of catalog pages, so the schema is only complete once F9 works.
- Rows follow OpenQBW#22 sections 1 to 3: `u16 len, u8 flags, null bitmap (ceil(nullable / 8), MSB first, set = present), values in column_id order`; NUMERIC is base-100, text is `u8 len + bytes`.
- Exact typed walks (cursor ends on the row length): 89.5% of rows overall on Rock Castle; 100% for `abmc_invoice_header` (113), `abmc_customer_user` (147), `abmc_account_user` (144); 1237 / 1238 invoice lines. Failures are mostly SA system tables (`ISYSCOLSTAT`, `ISYSVIEW`, `ISYSSOURCE`).
- Stale step-table entries exist: pages 1963 to 1966 have no page-number anchor because the recovered `step` for slots 982 and 983 is wrong (65 and 31 fit, with the expected `base`). A header-validated step search would fix these.

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

## Open questions / next steps
1. Closed form (or table + key derivation) for `T[k]`, and how `K` relates to the superblock (candidates: `file_id_lo` @0x08, 0x20..0x2C, 0x2BC..). That would remove the brute-force pass.
2. Fix weak slots: per-slot confidence, and check the 155 low-zero pages.
3. Header-validated step search for slots the statistical recovery gets wrong (F10); out-of-row long values (blob chunk pages, kinds 0x11..0x14); stale row versions on kinds 1/2.
4. Posted F5 upstream as OpenQBW issue; post the F11 epoch correction there.
5. End-to-end tool from the raw `.qbw` (find `K`, steps, sector 7) instead of separate scripts.
6. Port into OpenSQLAnywhere: version detection from the page-0 fingerprint, `ApModel` variant for SA11, page-type masking, CRC optional.
