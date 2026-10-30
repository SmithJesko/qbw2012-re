"""Learn the sector-7 correction rows C[r7] (OpenSQLAnywhere issue #16, section 4).

After the per-sector fill is removed, sector 7 (0xE00..0xFF0) still carries
d * C[r7](i), with d an odd per-page multiplier and C a 496-entry row of
values in 0..16 that depends only on the sector-7 rotation r7.

Rows are learned in two passes over one or more decoded files:
  1. pages whose sector 7 is entirely d * C (zero plaintext): unique odd d
     that maps every byte into 0..16 gives the full row;
  2. remaining r7 values: per position, vote over pages for the value in
     0..16 (zero plaintext bytes are common), using the multipliers seen
     in pass 1.
Usage: sector7.py OUT.json decoded1 [decoded2 ...]
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rowpage11 import find_r0  # noqa: E402

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
            yield pn, r7_for(pn, r0), pg[0xE00:0xFF0]


def learn(paths):
    pages = [p for path in paths for p in sector7_pages(open(path, "rb").read())]
    rows, ds = {}, collections.Counter()
    for _, r7, res in pages:
        if res.count(0) > 480:
            continue
        cands = [(d, bytes((x * INV[d]) & 255 for x in res)) for d in range(1, 256, 2)]
        cands = [(d, c) for d, c in cands if max(c) <= 16]
        if len(cands) == 1:
            rows[r7] = cands[0][1]
            ds[cands[0][0]] += 1
    common_d = [d for d, _ in ds.most_common(6)]
    votes = collections.defaultdict(lambda: [collections.Counter() for _ in range(N7)])
    for _, r7, res in pages:
        if r7 in rows:
            continue
        best = None
        for d in common_d:
            c = [(x * INV[d]) & 255 for x in res]
            fit = sum(v <= 16 for v in c)
            if best is None or fit > best[0]:
                best = (fit, c)
        for i, v in enumerate(best[1]):
            if v <= 16:
                votes[r7][i][v] += 1
    partial = {}
    for r7, pos in votes.items():
        row = [ctr.most_common(1)[0][0] if ctr else None for ctr in pos]
        partial[r7] = row
    return rows, partial, ds


if __name__ == "__main__":
    rows, partial, ds = learn(sys.argv[2:])
    full = {str(k): list(v) for k, v in rows.items()}
    filled = {str(k): v for k, v in partial.items() if None not in v}
    print(f"exact rows {len(rows)}, voted rows complete {len(filled)} of {len(partial)}; d: {ds.most_common(6)}")
    json.dump({"exact": full, "voted": filled, "d": [d for d, _ in ds.most_common(6)]}, open(sys.argv[1], "w"))
