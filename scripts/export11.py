"""Export every decodable table of a decoded SA11 .qbw to SQLite.

Usage: export11.py DECODED C7_JSON OUT.sqlite

Only rows whose typed walk ends exactly on the row length are written.
NUMERIC values are stored as exact decimal text, dates and timestamps as
ISO 8601 text. Out-of-row long values are reassembled from their chunk
chains (text or BLOB by domain); a value whose chain is incomplete keeps a
"<N bytes out of row>" marker. A `_export_report` table compares exported
row counts with the row count each table's SYSTABLE entry records
(catalog11.py) and counts incomplete long values.
"""
import datetime
import decimal
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from catalog11 import catalog  # noqa: E402
from page11 import iter_pages  # noqa: E402
from rowpage11 import load_c7  # noqa: E402
from schema11 import LongRef, load_names, load_schema, resolve_long, walk  # noqa: E402


def sql_value(v):
    if isinstance(v, decimal.Decimal):
        return format(v, "f")
    if isinstance(v, datetime.datetime):
        return v.isoformat(sep=" ")
    return v


def main(decoded, c7_path, out):
    export(open(decoded, "rb").read(), load_c7(c7_path), out)


def export(data, c7, out):
    names = load_names(data, c7)
    schema = load_schema(data, c7)
    counts = {r["table_id"]: r["rows"] for r in catalog(data)}

    pages = {p.pn: p for p in iter_pages(data, c7=c7)}
    walked, failed = [], {}
    for p in pages.values():
        cols = schema.get(p.owner)
        if p.owner < 3 or not cols or p.owner not in names:
            continue
        for i, row, trusted in p.rows():
            if trusted and len(row) == 9 and row[2] & 4:
                continue  # forwarding stub: u16 len, flags, u32 page, u16 slot (NOTES F17)
            rec = walk(row, cols) if trusted else None
            if rec is not None:
                walked.append((p, i, rec))
            elif p.kind < 0x11:
                failed[p.owner] = failed.get(p.owner, 0) + 1

    # Pages of kind 0x11..0x14 mix chunk rows of long values with ordinary
    # rows; a row is a chunk exactly when a long-value descriptor points at it.
    chunks = {(v.page, v.slot) for _, _, rec in walked for v in rec.values() if isinstance(v, LongRef)}
    rows, partial = {}, {}
    for p, i, rec in walked:
        if (p.pn, i) in chunks:
            continue
        domains = {c["name"]: c["domain"] for c in schema[p.owner]}
        for name, v in rec.items():
            if isinstance(v, LongRef):
                rec[name], complete = resolve_long(pages, v, domains[name])
                if not complete:
                    partial[p.owner] = partial.get(p.owner, 0) + 1
        rows.setdefault(p.owner, []).append(rec)

    if os.path.exists(out):
        os.remove(out)
    db = sqlite3.connect(out)
    db.execute("CREATE TABLE _export_report (table_name TEXT, table_id INTEGER, "
               "exported INTEGER, failed INTEGER, catalog_rows INTEGER, long_incomplete INTEGER)")
    for tid in sorted(rows):
        name, cols = names[tid], schema[tid]
        decl = ", ".join(f'"{c["name"]}"' for c in cols)
        db.execute(f'CREATE TABLE "{name}" ({decl})')
        marks = ", ".join("?" for _ in cols)
        db.executemany(f'INSERT INTO "{name}" VALUES ({marks})',
                       [[sql_value(r.get(c["name"])) for c in cols] for r in rows[tid]])
    # One report row per table that exported rows, failed rows, or has a
    # catalog row count, so a table whose rows all fail still shows up.
    # Catalog-only ids must be named in ISYSTAB (the tag scan can misread ids).
    report = set(rows) | set(failed) | (set(counts) & set(names))
    for tid in sorted(report):
        db.execute("INSERT INTO _export_report VALUES (?, ?, ?, ?, ?, ?)",
                   (names.get(tid), tid, len(rows.get(tid, ())), failed.get(tid, 0),
                    counts.get(tid), partial.get(tid, 0)))
    db.commit()
    exported = sum(len(v) for v in rows.values())
    print(f"{len(rows)} tables, {exported} rows, {sum(failed.values())} rows not decoded, "
          f"{sum(partial.values())} long values incomplete -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
