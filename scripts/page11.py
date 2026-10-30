"""Logical row pages and rows for decoded SA11 .qbw files.

Builds on rowpage11.py (rotation, F7) and the page layout described in
OpenSQLAnywhere issue #16 (pete-green):
  header: u32 @0 page number, u8 @16 = 0x04, u8 @17 page kind,
          u16 @20 lowest row offset, u16 @22 slot count, u32 @24 owner id
  header[6:16] and header[18:20] hold logical row-area bytes 4082..4092
  and 4080..4082 (the "stash")
  slots at +28, values relative to +28, 0 = deleted
  each row starts with u16 total length; the row area ends at 4092

Sector 7 (0xE00..0xFF0) needs the learned correction rows (sector7.py).
Without a row for the page's rotation, bytes from 0xE00 on are untrusted.
"""
from rowpage11 import logical_page

PAGE = 4096
ROW_PAGE_TYPES = (5, 7)  # trailer type byte, 0x40 bit cleared ('E', 'G')
SAFE_END = 0xE00
ROW_END = 4092


class Page:
    def __init__(self, pn, lp, s7_ok=False):
        self.pn = pn
        self.safe_end = ROW_END if s7_ok else SAFE_END
        self.raw = lp
        buf = bytearray(lp[:PAGE - 16]) + bytearray(16)
        buf[4080:4082] = lp[18:20]
        buf[4082:4092] = lp[6:16]
        self.buf = bytes(buf)
        self.kind = lp[17]
        self.slot_count = int.from_bytes(lp[22:24], "little")
        self.owner = int.from_bytes(lp[24:28], "little")
        self.slots = [int.from_bytes(lp[28 + 2 * i:30 + 2 * i], "little") for i in range(self.slot_count)]

    def rows(self):
        """Yield (slot index, row bytes, trusted) for live rows.

        A row is trusted when its u16 length closes on the next row start
        (or on 4092 for the last row) and it lies outside sector 7.
        """
        starts = sorted(28 + s for s in self.slots if s)
        nxt = {a: b for a, b in zip(starts, starts[1:] + [ROW_END])}
        for i, s in enumerate(self.slots):
            if not s:
                continue
            a = 28 + s
            n = int.from_bytes(self.buf[a:a + 2], "little")
            end = a + n
            trusted = n >= 3 and end == nxt[a] and end <= self.safe_end
            yield i, self.buf[a:end] if n >= 3 else b"", trusted


def iter_pages(data, kinds=None, c7=None):
    """Yield row pages; c7 = load_c7(...) enables sector 7."""
    for pn in range(1, len(data) // PAGE):
        raw = data[pn * PAGE:(pn + 1) * PAGE]
        if raw[0xFF2] & 0xBF not in ROW_PAGE_TYPES:
            continue
        res = logical_page(raw, pn, c7=c7)
        if res is None or res[0][16] != 4:
            continue
        p = Page(pn, res[0], res[1])
        if kinds is None or p.kind in kinds:
            yield p
