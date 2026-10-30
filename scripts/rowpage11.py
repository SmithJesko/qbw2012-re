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
    """Position of the page number in decoded sector 0. With several hits,
    the one whose header has byte 16 == 0x04 wins, if it is unique."""
    key = pn.to_bytes(4, "little")
    ring = sec0 + sec0[:3]
    hits = [i for i in range(SECTOR) if ring[i:i + 4] == key]
    if len(hits) > 1:
        hits = [h for h in hits if sec0[(h + 16) % SECTOR] == 4]
    return hits[0] if len(hits) == 1 else None


N7 = 496


_PLAUSIBLE = bytes(2 if x in (0, 0x30) else 1 if 32 <= x < 127 or x in (9, 10, 13) else 0
                   for x in range(128)) + bytes(128)


def _plausibility(b):
    """Score text and zero bytes up, high bytes down (#16 follow-up 1)."""
    return sum(_PLAUSIBLE[x] for x in b) - sum(x >= 0x80 for x in b)


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
    sector 7 (row length == gap to the next start), then scores best on
    plausibility (#16 follow-up: 0x00 and '0' +2, printable +1, >= 0x80 -1),
    which separates the d + 64k aliases that chaining can't. head is the
    logical page up to 0xE00 (header and slots).
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
        score = (chained, _plausibility(s7))
        if best is None or score > best[0]:
            best = (score, s7)
    return best[1]


FLIP_BELOW = 42


def sector_rotation(r0, s, q):
    """Rotation of sector s. The pair-wise prediction drops by 2 per pair;
    below 42 the true rotation is the prediction + 256 (OpenSQLAnywhere#16
    section 1.4). On SA11 the cut is exact: every decisive sector with a
    prediction <= 41 is flipped, none >= 43 is (NOTES F15)."""
    pred = (r0 - 2 * ((s + q) // 2)) % SECTOR
    return pred + 256 if pred < FLIP_BELOW else pred


def sector7_rotation(r0, q):
    """r7 = rot6 on odd pages, rot6 - 2 on even pages (#16 section 4.1),
    with the same +256 flip below 42 as the other sectors."""
    rot6 = sector_rotation(r0, 6, q)
    r7 = rot6 if q == 0 else (rot6 - 2) % SECTOR
    return r7 + 256 if r7 < FLIP_BELOW else r7


def logical_page(page, pn, q=None, c7=None):
    """Un-rotate a decoded row page. Returns (logical page, sector 7 ok)."""
    r0 = find_r0(page[:SECTOR], pn)
    if r0 is None:
        return None
    if q is None:
        q = 0 if pn & 1 else 1
    out = bytearray()
    for s in range(7):
        sec = page[s * SECTOR:(s + 1) * SECTOR]
        rot = sector_rotation(r0, s, q)
        out += sec[rot:] + sec[:rot]
    r7 = sector7_rotation(r0, q)
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
