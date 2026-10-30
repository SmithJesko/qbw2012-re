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

DATE_EPOCH = datetime.datetime(1600, 2, 29)  # issue #22; see NOTES F9
FIXED = {1: 2, 22: 2, 2: 4, 19: 4, 21: 4, 20: 8, 23: 8, 4: 4, 5: 8, 24: 1, 6: 4, 13: 8, 14: 8}
TEXT = {7, 8, 9, 10, 11, 12, 28}
NUMERIC = {3, 27}
DOMAINS = set(FIXED) | TEXT | NUMERIC


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
        return r[o + 3:o + 3 + n].decode("latin-1"), o + 3 + n
    if width > 255 and b & 0x80:
        k = b & 0x7F
        prefix = r[o + 1:o + 1 + k].decode("latin-1")
        total = int.from_bytes(r[o + 2 + k:o + 6 + k], "little")
        return f"{prefix}...<{total} bytes out of row>", o + 1 + k + 13
    return r[o + 1:o + 1 + b].decode("latin-1"), o + 1 + b


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
