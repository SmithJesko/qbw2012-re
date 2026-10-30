"""Learn the sector-7 correction rows C[r7] (OpenSQLAnywhere issue #16, section 4).

After the per-sector fill is removed, sector 7 (0xE00..0xFF0) still carries
d * C[r7](i), with d an odd per-page multiplier and C a 496-entry row of
values in 0..16 that depends only on the sector-7 rotation r7.

Rows are learned in two passes over one or more decoded files:
  1. pages whose sector 7 is entirely d * C (zero plaintext): unique odd d
     that maps every byte into 0..16 gives the full row;
  2. remaining r7 values: per position, vote over pages for the value in
     0..16 (zero plaintext bytes are common), using the multipliers seen
     in pass 1;
  3. re-vote the pass-2 rows using each page's own multiplier, picked by
     row chaining with the pass-2 rows (rowpage11.sector7).
Usage: sector7.py OUT.json decoded1 [decoded2 ...]
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rowpage11 import find_r0, logical_page  # noqa: E402

INV = {x: pow(x, -1, 256) for x in range(1, 256, 2)}
N7 = 496


def r7_for(pn, r0):
    q = 0 if pn & 1 else 1
    rot6 = (r0 - 2 * ((6 + q) // 2)) % 512
    return rot6 if q == 0 else (rot6 - 2) % 512


def sector7_pages(data):
    for pn in range(1, len(data) // 4096):
        pg = data[pn * 4096:(pn + 1) * 4096]
        if pg[0xFF2] & 0xBF != 5:
            continue
        r0 = find_r0(pg[:512], pn)
        if r0 is not None:
            yield pn, r7_for(pn, r0), pg


def page_multiplier(pg, r7, crow, head):
    """Odd d that chains the most sector-7 rows, then most zero bytes."""
    count = int.from_bytes(head[22:24], "little")
    starts = sorted(28 + int.from_bytes(head[28 + 2 * i:30 + 2 * i], "little") for i in range(count))
    starts = [a for a in starts if a > 28]
    pairs = [(a, b) for a, b in zip(starts, starts[1:] + [4092]) if 0xE00 <= a < 0xFEF]
    res = pg[0xE00:0xFF0]
    best = None
    for d in range(1, 256, 2):
        plain = bytes((x - d * c) & 255 for x, c in zip(res, crow))
        unit = plain + pg[0xFF0:0x1000]
        lrot = unit[r7:] + unit[:r7]
        t = (N7 - r7) % 512
        s7 = lrot[:t] + lrot[N7:512] + lrot[t + 16:N7]
        chained = sum(int.from_bytes(s7[a - 0xE00:a - 0xE00 + 2], "little") == b - a for a, b in pairs)
        score = (chained, len(pairs) > 0, plain.count(0))
        if best is None or score > best[0]:
            best = (score, d)
    return best[1] if best[0][1] else None


def learn(paths):
    pages = []
    for path in paths:
        for pn, r7, pg in sector7_pages(open(path, "rb").read()):
            pages.append((pn, r7, pg))
    rows, ds = {}, collections.Counter()
    for _, r7, pg in pages:
        res = pg[0xE00:0xFF0]
        if res.count(0) > 480:
            continue
        cands = [(d, bytes((x * INV[d]) & 255 for x in res)) for d in range(1, 256, 2)]
        cands = [(d, c) for d, c in cands if max(c) <= 16]
        if len(cands) == 1:
            rows[r7] = cands[0][1]
            ds[cands[0][0]] += 1
    common_d = [d for d, _ in ds.most_common(6)]
    votes = collections.defaultdict(lambda: [collections.Counter() for _ in range(N7)])
    for _, r7, pg in pages:
        if r7 in rows:
            continue
        res = pg[0xE00:0xFF0]
        best = None
        for d in common_d:
            c = [(x * INV[d]) & 255 for x in res]
            fit = sum(v <= 16 for v in c)
            if best is None or fit > best[0]:
                best = (fit, c)
        for i, v in enumerate(best[1]):
            if v <= 16:
                votes[r7][i][v] += 1
    partial = {r7: [ctr.most_common(1)[0][0] if ctr else 0 for ctr in pos] for r7, pos in votes.items()}
    votes = collections.defaultdict(lambda: [collections.Counter() for _ in range(N7)])
    for pn, r7, pg in pages:
        if r7 in rows or r7 not in partial or (N7 - r7) % 512 > N7 - 16:
            continue
        lp = logical_page(pg, pn)
        if lp is None or lp[0][16] != 4:
            continue
        d = page_multiplier(pg, r7, partial[r7], lp[0])
        if d is None:
            continue
        for i, x in enumerate(pg[0xE00:0xFF0]):
            v = (x * INV[d]) & 255
            if v <= 16:
                votes[r7][i][v] += 1
    revoted = {r7: [ctr.most_common(1)[0][0] if ctr else None for ctr in pos] for r7, pos in votes.items()}
    return rows, revoted, ds


if __name__ == "__main__":
    rows, partial, ds = learn(sys.argv[2:])
    full = {str(k): list(v) for k, v in rows.items()}
    filled = {str(k): v for k, v in partial.items() if None not in v}
    print(f"exact rows {len(rows)}, voted rows complete {len(filled)} of {len(partial)}; d: {ds.most_common(6)}")
    json.dump({"exact": full, "voted": filled, "d": [d for d, _ in ds.most_common(6)]}, open(sys.argv[1], "w"))
