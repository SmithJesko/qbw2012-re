"""Recover the SA11 additive fill (NOTES F3) quickly.

  stored[i] = plain[i] + base + i * step  (mod 256), per 512-byte sector
  base = (K + pn + si) mod 256, one K per file
  step = T[(pn + si + 1) // 2]

K: most common (base - pn - si) over pure-fill sectors.
T: for every byte at an odd index i, the step that would make plain[i]
zero is unique, (stored[i] - base) * inverse(i) mod 256. Plaintext is
mostly zero bytes, so the true step collects most votes in its slot.
"""
import collections

SECTOR = 512
PAGE = 4096
INV = {x: pow(x, -1, 256) for x in range(1, 256, 2)}


def sector(data, pn, si):
    off = pn * PAGE + si * SECTOR
    return data[off:off + (SECTOR if si < 7 else 0xFF0 - 7 * SECTOR)]


def find_k(data):
    """Most common base - pn - si over sectors that are a pure progression."""
    votes = collections.Counter()
    for pn in range(1, len(data) // PAGE):
        for si in range(8):
            s = sector(data, pn, si)
            step = (s[1] - s[0]) & 255
            if all((s[i + 1] - s[i]) & 255 == step for i in range(len(s) - 1)):
                votes[(s[0] - pn - si) & 255] += 1
    return votes.most_common(1)[0][0]


def find_steps(data, k):
    """slot -> step, by per-byte voting over odd byte positions."""
    votes = collections.defaultdict(collections.Counter)
    for pn in range(1, len(data) // PAGE):
        for si in range(8):
            s = sector(data, pn, si)
            base = (k + pn + si) & 255
            ctr = votes[(pn + si + 1) // 2]
            ctr.update(((s[i] - base) * INV[i & 255]) & 255 for i in range(1, len(s), 2))
    return {slot: ctr.most_common(1)[0][0] for slot, ctr in votes.items()}


def decode(data, k, steps):
    """Remove the fill from every page except page 0 (sector 7 keeps its
    extra d * C term; see sector7.py)."""
    out = bytearray(data)
    for pn in range(1, len(data) // PAGE):
        for si in range(8):
            off = pn * PAGE + si * SECTOR
            n = SECTOR if si < 7 else 0xFF0 - 7 * SECTOR
            base = (k + pn + si) & 255
            step = steps.get((pn + si + 1) // 2, 1)
            out[off:off + n] = bytes((out[off + i] - base - i * step) & 255 for i in range(n))
    return bytes(out)


def repair_steps(data, k, steps):
    """Fix slots whose step hides the page-number anchor of a row page.

    For a row page (trailer type 5 or 7) exactly one occurrence of the u32
    page number in decoded sector 0 must start a valid header (byte 16 =
    0x04, slot count <= 1500); see OpenSQLAnywhere#16 section 1.5.
    Returns the list of repaired slots.
    """
    fixed = []
    for pn in range(1, len(data) // PAGE):
        if data[pn * PAGE + 0xFF2] & 0xBF not in (5, 7):
            continue
        s0 = sector(data, pn, 0)
        base = (k + pn) & 255
        slot = (pn + 1) // 2
        if _anchor(s0, pn, base, steps.get(slot, 1)):
            continue
        good = [st for st in range(256) if st != steps.get(slot) and _anchor(s0, pn, base, st)]
        if len(good) == 1:
            steps[slot] = good[0]
            fixed.append(slot)
    return fixed


def _anchor(s0, pn, base, step):
    dec = bytes((x - base - i * step) & 255 for i, x in enumerate(s0))
    ring = dec + dec[:3]
    key = pn.to_bytes(4, "little")
    hits = [i for i in range(SECTOR) if ring[i:i + 4] == key]
    valid = 0
    for h in hits:
        hdr = bytes(dec[(h + j) % SECTOR] for j in range(24))
        valid += hdr[16] == 4 and int.from_bytes(hdr[22:24], "little") <= 1500
    return valid == 1

