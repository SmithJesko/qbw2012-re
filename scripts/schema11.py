"""Read table schemas (ISYSTABCOL, owner id 2) and decode typed rows.

Row and column formats follow OpenQBW issue #22 (pete-green):
  row:   u16 len, u8 flags, [u8 extra if flags & 0x40], null bitmap
         (ceil(nullable / 8) bytes, MSB first, set = present), then values
         in ascending column_id order
  ISYSTABCOL row (after the extra byte is removed):
         +4 u32 table_id, +8 u32 column_id, +12 u16 domain,
         +14 01 'Y'/'N' nullable, +16 u32 width, +20 u16 scale,
         +22 u64 object id, +30 u64 max identity, +38 u8 len + name
A typed walk is accepted only if it ends exactly on the row length.
"""
import collections
import datetime
import decimal
import re
import struct

from page11 import iter_pages

DATE_EPOCH = datetime.datetime(1600, 2, 29)  # issue #22; see NOTES F11
FIXED = {1: 2, 22: 2, 2: 4, 19: 4, 21: 4, 20: 8, 23: 8, 4: 4, 5: 8, 24: 1, 6: 4, 13: 8, 14: 8}
TEXT = {7, 8, 9, 10, 11, 12, 28}
BINARY = {11, 12, 28}  # long values of these domains hold binary data (NOTES F14)
NUMERIC = {3, 27}
DOMAINS = set(FIXED) | TEXT | NUMERIC


CP1252_UNDEFINED = b"\x81\x8d\x8f\x90\x9d"


def decode_text(b):
    """Decode stored text as windows-1252, the file's character set (NOTES
    F1). The 5 bytes cp1252 leaves undefined map to the same code points as
    latin-1, so the decode stays lossless and reversible."""
    if not any(x in CP1252_UNDEFINED for x in b):
        return b.decode("cp1252")
    return "".join(bytes([x]).decode("latin-1" if x in CP1252_UNDEFINED else "cp1252") for x in b)


class LongRef(collections.namedtuple("LongRef", "prefix total page unknown slot")):
    """Out-of-row long value: in-row prefix plus a 13-byte descriptor
    [00][u32 total_len][u32 first chunk page][u16 ?][u16 slot] (#22 2.3)."""

    def __str__(self):
        return f"{decode_text(self.prefix)}...<{self.total} bytes out of row>"


def strip_extra(row):
    """Return the row with the optional flags & 0x40 byte removed."""
    if len(row) > 3 and row[2] & 0x40:
        return row[:3] + row[4:]
    return row


def parse_tabcol(row):
    r = strip_extra(row)
    if len(r) < 40 or r[14] != 1 or r[15] not in b"YN":
        return None
    n = r[38]
    name = r[39:39 + n]
    if not re.fullmatch(rb"[A-Za-z_][A-Za-z0-9_$#@]*", name):
        return None
    table_id, column_id, domain = struct.unpack_from("<IIH", r, 4)
    if table_id >= 1 << 20 or not 0 < column_id < 4096 or domain not in DOMAINS:
        return None
    return dict(
        table_id=table_id,
        column_id=column_id,
        domain=domain,
        nullable=r[15] == ord("Y"),
        width=struct.unpack_from("<I", r, 16)[0],
        scale=struct.unpack_from("<H", r, 20)[0],
        object_id=struct.unpack_from("<Q", r, 22)[0],
        name=name.decode(),
    )


def load_names(data, c7=None):
    """table_id -> name from ISYSTAB rows (owner id 1): +4 u32 table_id,
    +54 u8 name length, +55 name (OpenQBW#22 section 5.3)."""
    names = {}
    for p in iter_pages(data, c7=c7):
        if p.owner != 1:
            continue
        for _, row, trusted in p.rows():
            r = strip_extra(row)
            if not trusted or len(r) < 56:
                continue
            name = r[55:55 + r[54]]
            if re.fullmatch(rb"[A-Za-z_][A-Za-z0-9_]*", name):
                names[struct.unpack_from("<I", r, 4)[0]] = name.decode()
    return names


def load_schema(data, c7=None):
    """table_id -> list of column dicts sorted by column_id.

    One definition per (table_id, column_id): most frequent, ties to the
    highest object id (issue #22 section 5.2).
    """
    seen = collections.defaultdict(collections.Counter)
    defs = {}
    for p in iter_pages(data, c7=c7):
        if p.owner != 2:
            continue
        for _, row, trusted in p.rows():
            if not trusted:
                continue
            c = parse_tabcol(row)
            if not c:
                continue
            key = (c["table_id"], c["column_id"])
            sig = (c["domain"], c["nullable"], c["width"], c["scale"], c["name"])
            seen[key][sig] += 1
            if (key, sig) not in defs or c["object_id"] > defs[(key, sig)]["object_id"]:
                defs[(key, sig)] = c
    tables = collections.defaultdict(list)
    for key, ctr in seen.items():
        top = max(ctr.items(), key=lambda kv: (kv[1], defs[(key, kv[0])]["object_id"]))[0]
        tables[key[0]].append(defs[(key, top)])
    for cols in tables.values():
        cols.sort(key=lambda c: c["column_id"])
    return dict(tables)


def read_numeric(r, o):
    k, ctrl = r[o], r[o + 1]
    digits = r[o + 2:o + 2 + k]
    if len(digits) != k or any(x > 99 for x in digits):
        raise ValueError("numeric out of sync")
    coef = 0
    for x in reversed(digits):
        coef = coef * 100 + x
    exp = (ctrl & 0x7F) - 64
    val = decimal.Decimal(coef).scaleb(2 * exp) if coef else decimal.Decimal(0)
    return (-val if ctrl & 0x80 else val), o + 2 + k


def read_text(r, o, width):
    b = r[o]
    if width > 255 and b == 0xFF:
        n = int.from_bytes(r[o + 1:o + 3], "big")
        return decode_text(r[o + 3:o + 3 + n]), o + 3 + n
    if width > 255 and b & 0x80:
        k = b & 0x7F
        d = r[o + 1 + k:o + 1 + k + 13]
        if len(d) != 13:
            raise ValueError("short descriptor")
        ref = LongRef(r[o + 1:o + 1 + k], *struct.unpack("<xIIHH", d))
        return ref, o + 1 + k + 13
    return decode_text(r[o + 1:o + 1 + b]), o + 1 + b


def read_value(r, o, col):
    dom = col["domain"]
    if dom in FIXED:
        w = FIXED[dom]
        raw = r[o:o + w]
        if len(raw) != w:
            raise ValueError("short")
        if dom == 6:
            v = DATE_EPOCH + datetime.timedelta(minutes=int.from_bytes(raw, "little"))
        elif dom in (13, 14):
            v = DATE_EPOCH + datetime.timedelta(minutes=int.from_bytes(raw[:4], "little"),
                                                microseconds=int.from_bytes(raw[4:], "little"))
        elif dom == 4:
            v = struct.unpack("<f", raw)[0]
        elif dom == 5:
            v = struct.unpack("<d", raw)[0]
        else:
            v = int.from_bytes(raw, "little", signed=dom in (1, 2, 20))
        return v, o + w
    if dom in NUMERIC:
        return read_numeric(r, o)
    if dom in TEXT:
        return read_text(r, o, col["width"])
    raise ValueError(f"domain {dom}")


def walk(row, cols):
    """Decode a row against cols; return {name: value} or None.

    Retries with trailing columns dropped (rows written before columns were
    appended, issue #22 section 3).
    """
    r = strip_extra(row)
    n = len(r)
    for drop in range(0, min(16, len(cols)) + 1):
        use = cols[:len(cols) - drop]
        nullable = [c for c in use if c["nullable"]]
        nb = (len(nullable) + 7) // 8
        bitmap = r[3:3 + nb]
        o = 3 + nb
        out = {}
        bit = 0
        try:
            for c in use:
                if c["nullable"]:
                    present = bitmap[bit // 8] & (0x80 >> (bit % 8))
                    bit += 1
                    if not present:
                        out[c["name"]] = None
                        continue
                out[c["name"]], o = read_value(r, o, c)
                if o > n:
                    raise ValueError("overrun")
        except (ValueError, IndexError, OverflowError):
            continue
        if o == n:
            for c in cols[len(use):]:
                out[c["name"]] = None
            return out
    return None


def resolve_long(pages, ref, domain):
    """Full value for a LongRef: the chunk chain trimmed to ref.total (the
    in-row prefix repeats the first bytes). Text domains decode as text,
    binary ones stay bytes. Returns (value, complete)."""
    data = chunk_bytes(pages, ref)
    if data is None or len(data) < ref.total:
        return str(ref), False
    data = data[:ref.total]
    return (data if domain in BINARY else decode_text(data)), True


def chunk_bytes(pages, ref, limit=10000):
    """Concatenate the chunk chain a LongRef points at (#22 section 4).

    Chunk row: u16 len, u8 flags, u32 next_page if flags & 4, data.
    The first chunk is slot `ref.slot` of `ref.page`; continuation pages
    hold a single chunk row. Returns None if the chain breaks.
    """
    out = bytearray()
    page, slot = ref.page, ref.slot
    for _ in range(limit):
        p = pages.get(page)
        if p is None or slot >= len(p.slots) or not p.slots[slot]:
            return None
        a = 28 + p.slots[slot]
        n = int.from_bytes(p.buf[a:a + 2], "little")
        flags = p.buf[a + 2]
        body = p.buf[a + 3:a + n]
        if flags & 4:
            page, slot = int.from_bytes(body[:4], "little"), 0
            out += body[4:]
        else:
            out += body
            return bytes(out)
    return None
