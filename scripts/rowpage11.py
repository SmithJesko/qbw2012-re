"""Un-rotate decoded SA11 row pages and check row-length chaining.

Rotation model from OpenSQLAnywhere issue #16 (pete-green), applied to SA11:
  r0     = position of the u32_LE page number in decoded sector 0
  rot_s  = (r0 - 2 * ((s + q) // 2)) mod 512, sectors 0..6
  q      = pairing phase; in SA11 the step pairs are (pn + s + 1) // 2,
           so sectors pair as (0,1),(2,3).. on odd pages (q=0) and
           (1,2),(3,4).. on even pages (q=1)
Header (logical offsets): u16 @22 slot count, u32 @24 owner table id,
slot array at +28, slot values relative to +28.
"""
import sys

SECTOR = 512


def find_r0(sec0, pn):
    key = pn.to_bytes(4, "little")
    ring = sec0 + sec0[:3]
    hits = [i for i in range(SECTOR) if ring[i:i + 4] == key]
    return hits[0] if len(hits) == 1 else None


def logical_page(page, pn, q=None):
    r0 = find_r0(page[:SECTOR], pn)
    if r0 is None:
        return None
    if q is None:
        q = 0 if pn & 1 else 1
    out = bytearray()
    for s in range(7):
        sec = page[s * SECTOR:(s + 1) * SECTOR]
        rot = (r0 - 2 * ((s + q) // 2)) % SECTOR
        out += sec[rot:] + sec[:rot]
    out += page[7 * SECTOR:0xFF0]  # sector 7 not yet modelled
    return bytes(out)


def rows(lp):
    count = int.from_bytes(lp[22:24], "little")
    slots = [int.from_bytes(lp[28 + 2 * i:30 + 2 * i], "little") for i in range(count)]
    return count, int.from_bytes(lp[24:28], "little"), slots


def chain_stats(lp):
    """Rows whose u16 length equals the gap to the next row start."""
    count, owner, slots = rows(lp)
    starts = sorted(28 + s for s in slots if s)
    ok = bad = 0
    for a, b in zip(starts, starts[1:]):
        if b > 0xE00:  # stop before sector 7
            break
        if int.from_bytes(lp[a:a + 2], "little") == b - a:
            ok += 1
        else:
            bad += 1
    return ok, bad


if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    tot_ok = tot_bad = pages = skipped = 0
    for q_mode in ("rule", 0, 1):
        tot_ok = tot_bad = pages = skipped = 0
        for pn in range(1, len(data) // 4096):
            page = data[pn * 4096:(pn + 1) * 4096]
            if page[0xFF2] & 0xBF != 5:
                continue
            lp = logical_page(page, pn, None if q_mode == "rule" else q_mode)
            if lp is None or lp[16] != 4:
                skipped += 1
                continue
            ok, bad = chain_stats(lp)
            tot_ok += ok
            tot_bad += bad
            pages += 1
        print(f"q={q_mode}: pages {pages} (skipped {skipped}), row chain ok {tot_ok}, bad {tot_bad}, "
              f"rate {tot_ok / max(1, tot_ok + tot_bad):.3f}")
