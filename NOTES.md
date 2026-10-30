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
- `sample_product-based business.qbw` **is Rock Castle Construction** ("Rock Castle Construction, Inc.", customers like "Abercrombie, Kristy:Remodel Bathroom", report names like "FY2016-17 - Profit & Loss by Account").
- `sample_service-based business.qbw` is **Larry's Landscaping & (Garden) Supply**; it decodes the same way with K=243 (step recovery agrees on 1200/1204 pure-learned slots; `out/steps_service.json`).
- Decode quality proxy: ~2% of pages (155/7733) have <5% zero bytes, so they're either dense data or slots whose step was recovered wrong. Some strings show local corruption.

## Open questions / next steps
1. Closed form (or table + key derivation) for `T[k]`, and how `K` relates to the superblock (candidates: `file_id_lo` @0x08, 0x20..0x2C, 0x2BC..). That would remove the brute-force pass.
2. Fix weak slots: per-slot confidence, and check the 155 low-zero pages.
3. SA11 slotted-page / row layout vs SA17 (OpenSQLAnywhere `slotted.rs`), and the SYSTABLE row tag. OpenQBW's catalog walk expects SA17 shapes.
4. Port into OpenSQLAnywhere: version detection from the page-0 fingerprint, `ApModel` variant for SA11, page-type masking, CRC optional.
