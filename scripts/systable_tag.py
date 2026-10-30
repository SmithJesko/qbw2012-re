"""Find SYSTABLE-style row tags in a decoded file and report the magic values.

Tag shape (same as SA17, see OpenQBW SPECIFICATION.md section 5):
  05 00 00 00 <tid u32_LE> 00 00 00 00 <magic 4B> 00 00 00 00 <name_len u8> <name>
"""
import collections
import re
import sys

TAG = re.compile(rb"\x05\x00\x00\x00(.{4})\x00\x00\x00\x00(.{4})\x00\x00\x00\x00([\x01-\x80])", re.S)


def scan(data):
    rows = []
    for m in TAG.finditer(data):
        n = m.group(3)[0]
        name = data[m.end():m.end() + n]
        if re.fullmatch(rb"[A-Za-z_][A-Za-z0-9_]*", name):
            rows.append((m.start(), int.from_bytes(m.group(1), "little"), m.group(2).hex(), name.decode()))
    return rows


for path in sys.argv[1:]:
    rows = scan(open(path, "rb").read())
    magics = collections.Counter(r[2] for r in rows)
    pages = collections.Counter(r[0] // 4096 for r in rows)
    print(f"{path}: {len(rows)} tagged rows, {len(set(r[3] for r in rows))} distinct names")
    print("  magic:", magics.most_common(5))
    print("  pages:", sorted(pages.items())[:20], "..." if len(pages) > 20 else "")
    for r in rows[:6] + rows[-4:]:
        print(f"    p{r[0] // 4096}+{r[0] % 4096:03x} tid={r[1]:6} {r[2]} {r[3]}")
