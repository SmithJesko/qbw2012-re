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


def load_c7(path):
    """Sector-7 correction rows written by sector7.py, keyed by r7."""
    import json
    j = json.load(open(path))
    return {int(k): v for part in ("voted", "exact") for k, v in j[part].items()}


def find_r0(sec0, pn):
    key = pn.to_bytes(4, "little")
    ring = sec0 + sec0[:3]
    hits = [i for i in range(SECTOR) if ring[i:i + 4] == key]
    return hits[0] if len(hits) == 1 else None


N7 = 496


def _place7(plain, page, r7):
    """Undo the sector-7 rotation over the 512-byte unit that includes the
    plaintext trailer, moving the 16 displaced bytes back (#16 section 4.2)."""
    unit = plain + page[0xFF0:0x1000]
    lrot = unit[r7:] + unit[:r7]
    t = (N7 - r7) % SECTOR
    return lrot[:t] + lrot[N7:SECTOR] + lrot[t + 16:N7]


def sector7(page, r7, crow, head):
    """Logical sector 7 (496 bytes) or None.

    Removes d * C[r7]. The odd multiplier d drifts across the file, so every
    odd value is tried; the winner chains the most rows that start in
    sector 7 (row length == gap to the next start), then has the most zero
    bytes. head is the logical page up to 0xE00 (header and slots).
    """
    if (N7 - r7) % SECTOR > N7 - 16:
        return None
    count = int.from_bytes(head[22:24], "little")
    starts = sorted(28 + int.from_bytes(head[28 + 2 * i:30 + 2 * i], "little") for i in range(count))
    starts = [a for a in starts if a > 28]
    pairs = [(a - 0xE00, b) for a, b in zip(starts, starts[1:] + [4092]) if 0xE00 <= a < 0xFF0 - 1]
    res = page[0xE00:0xFF0]
    best = None
    for d in range(1, 256, 2):
        plain = bytes((x - d * c) & 255 for x, c in zip(res, crow))
        s7 = _place7(plain, page, r7)
        chained = sum(int.from_bytes(s7[a:a + 2], "little") == b - a - 0xE00 for a, b in pairs)
        score = (chained, plain.count(0))
        if best is None or score > best[0]:
            best = (score, s7)
    return best[1]


def logical_page(page, pn, q=None, c7=None):
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
    rot6 = (r0 - 2 * ((6 + q) // 2)) % SECTOR
    r7 = rot6 if q == 0 else (rot6 - 2) % SECTOR
    s7 = sector7(page, r7, c7[r7], out) if c7 and r7 in c7 else None
    out += s7 if s7 is not None else page[7 * SECTOR:0xFF0]
    out += page[0xFF0:]
    return bytes(out), s7 is not None


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
            res = logical_page(page, pn, None if q_mode == "rule" else q_mode)
            if res is None or res[0][16] != 4:
                skipped += 1
                continue
            lp = res[0]
            ok, bad = chain_stats(lp)
            tot_ok += ok
            tot_bad += bad
            pages += 1
        print(f"q={q_mode}: pages {pages} (skipped {skipped}), row chain ok {tot_ok}, bad {tot_bad}, "
              f"rate {tot_ok / max(1, tot_ok + tot_bad):.3f}")
