"""List SYSTABLE-style catalog rows from a decoded SA11 .qbw.

Row shape around the tag (offsets relative to the tag start):
  -30  u32  table_id (compact per-table id, same position as SA17)
  -24  u32  candidate: row count (unconfirmed)
  -12  u32  candidate: second count (unconfirmed)
   +0  05 00 00 00 <tid u32> 00 00 00 00 <stamp u32> 00 00 00 00 <len u8> <name>
       stamp = minutes since 1600-01-01 (row change time)
  name+6  u16  unknown (not a column count: mostly 16, up to 32874)

The same table can appear on several pages (older row versions); the row
with the newest stamp is kept.
"""
import datetime
import re
import sys

TAG = re.compile(rb"\x05\x00\x00\x00(.{4})\x00\x00\x00\x00(.{3}[\x0b-\x0e])\x00\x00\x00\x00([\x01-\x80])", re.S)
EPOCH = datetime.datetime(1600, 1, 1)


def u32(b, o):
    return int.from_bytes(b[o:o + 4], "little")


def catalog(data):
    best = {}
    for m in TAG.finditer(data):
        o, n = m.start(), m.group(3)[0]
        name = data[m.end():m.end() + n]
        if o < 30 or not re.fullmatch(rb"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        end = m.end() + n
        row = dict(
            name=name.decode(),
            page=o // 4096,
            tid=u32(m.group(1), 0),
            stamp=u32(m.group(2), 0),
            table_id=u32(data, o - 30),
            count_a=u32(data, o - 24),
            count_b=u32(data, o - 12),
        )
        cur = best.get(row["name"])
        if cur is None or row["stamp"] > cur["stamp"]:
            best[row["name"]] = row
    return sorted(best.values(), key=lambda r: r["tid"])


if __name__ == "__main__":
    rows = catalog(open(sys.argv[1], "rb").read())
    pat = re.compile(sys.argv[2]) if len(sys.argv) > 2 else None
    print(f"{'name':40} {'tid':>6} {'table_id':>8} {'count_a':>8} {'count_b':>7}  changed")
    for r in rows:
        if pat and not pat.search(r["name"]):
            continue
        when = EPOCH + datetime.timedelta(minutes=r["stamp"])
        print(f"{r['name']:40} {r['tid']:6} {r['table_id']:8} {r['count_a']:8} {r['count_b']:7}  {when:%Y-%m-%d}")
    print(f"# {len(rows)} distinct names", file=sys.stderr)
