"""Export every decodable table of a decoded SA11 .qbw to SQLite.

Usage: export11.py DECODED C7_JSON OUT.sqlite

Only rows whose typed walk ends exactly on the row length are written.
NUMERIC values are stored as exact decimal text, dates and timestamps as
ISO 8601 text. A `_export_report` table compares exported row counts with
the row count each table's SYSTABLE entry records (catalog11.py).
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
from schema11 import load_names, load_schema, walk  # noqa: E402


def sql_value(v):
    if isinstance(v, decimal.Decimal):
        return format(v, "f")
    if isinstance(v, datetime.datetime):
        return v.isoformat(sep=" ")
    return v


def main(decoded, c7_path, out):
    data = open(decoded, "rb").read()
    c7 = load_c7(c7_path)
    names = load_names(data, c7)
    schema = load_schema(data, c7)
    counts = {r["table_id"]: r["rows"] for r in catalog(data)}

    rows, failed = {}, {}
    for p in iter_pages(data, c7=c7):
        cols = schema.get(p.owner)
        if p.owner < 3 or not cols or p.owner not in names:
            continue
        for _, row, trusted in p.rows():
            rec = walk(row, cols) if trusted else None
            if rec is None:
                failed[p.owner] = failed.get(p.owner, 0) + 1
            else:
                rows.setdefault(p.owner, []).append(rec)

    if os.path.exists(out):
        os.remove(out)
    db = sqlite3.connect(out)
    db.execute("CREATE TABLE _export_report (table_name TEXT, table_id INTEGER, "
               "exported INTEGER, failed INTEGER, catalog_rows INTEGER)")
    for tid in sorted(rows):
        name, cols = names[tid], schema[tid]
        decl = ", ".join(f'"{c["name"]}"' for c in cols)
        db.execute(f'CREATE TABLE "{name}" ({decl})')
        marks = ", ".join("?" for _ in cols)
        db.executemany(f'INSERT INTO "{name}" VALUES ({marks})',
                       [[sql_value(r.get(c["name"])) for c in cols] for r in rows[tid]])
        db.execute("INSERT INTO _export_report VALUES (?, ?, ?, ?, ?)",
                   (name, tid, len(rows[tid]), failed.get(tid, 0), counts.get(tid)))
    db.commit()
    exported = sum(len(v) for v in rows.values())
    print(f"{len(rows)} tables, {exported} rows, {sum(failed.values())} rows not decoded -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
