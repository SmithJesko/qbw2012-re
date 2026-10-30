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
- **-24 `u32`**: candidate row count. `abmc_invoice_header` 113, `abmc_invoice_lineitem` 1238, `abmc_customer_*` 147, `abmc_account_*` 144, empty tables 0. Unconfirmed.
- **-12 `u32`**: candidate page count (invoice lines 86, about 14 rows per page). Unconfirmed.
- **-16**: constant `65 00 00 00`.
- **name+6**: not a column count (SA17's position); mostly 16, values up to 32874.
- SA17's `data_root_page` (name+34) and `last_page` (name+50) do not hold here. The root page location is open.

## Open questions / next steps
1. Closed form (or table + key derivation) for `T[k]`, and how `K` relates to the superblock (candidates: `file_id_lo` @0x08, 0x20..0x2C, 0x2BC..). That would remove the brute-force pass.
2. Fix weak slots: per-slot confidence, and check the 155 low-zero pages.
3. SYSTABLE: find the table root page, then confirm the -24/-12 count candidates by counting rows on the table's own pages. Bridge SYSCOLUMN owners to table_id (-30) to get column names per table.
4. Report F5 upstream as a spec correction (SPECIFICATION.md sections 3.1 and 5).
5. Port into OpenSQLAnywhere: version detection from the page-0 fingerprint, `ApModel` variant for SA11, page-type masking, CRC optional.
