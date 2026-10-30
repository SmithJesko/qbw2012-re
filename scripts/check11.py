"""Self-checks for an export written by qbw2012.py / export11.py.

Usage: check11.py BOOKS.sqlite

All checks use only numbers stored in the file itself:
  1. catalog: exported rows per table against the SYSTABLE row count
  2. transactions: posting lines (account_id set, not memorized, not
     no-post) sum to zero per transaction
  3. accounts: each account's stored ending_balance_amt against the sum of
     its posting lines across all posting tables plus the inventory records
     (*_inventory_lineitem), which carry cost of goods sold. Debits are
     stored as negative amounts.
Estimates, sales orders and purchase orders don't post, and the audit trail
holds history copies, so they are left out.
Exit status is 1 if any account or transaction check fails.
"""
import sqlite3
import sys

NON_POSTING = ("estimate", "sales_order", "purchase_order", "audit")
CENT = 0.005


def posting_tables(db):
    out = []
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE '%lineitem'"):
        cols = {r[1] for r in db.execute(f'PRAGMA table_info("{name}")')}
        if name.endswith("inventory_lineitem"):
            continue
        if {"account_id", "amount_amt", "transaction_id"} <= cols and not any(n in name for n in NON_POSTING):
            out.append(name)
    return sorted(out)


def skip_flags(db, table):
    """Leave out memorized (template) and no-post lines where the table has
    those flags."""
    cols = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
    return "".join(f" AND coalesce({c}, 0) = 0" for c in ("is_memorized_transaction_bool", "is_no_post_bool")
                   if c in cols)


def main(path):
    db = sqlite3.connect(path)
    failed = False

    total, matched = db.execute(
        "SELECT count(*), sum(exported = catalog_rows) FROM _export_report WHERE catalog_rows > 0").fetchone()
    print(f"catalog: {matched} / {total} tables match the SYSTABLE row count")

    tables = posting_tables(db)
    union = " UNION ALL ".join(
        f"SELECT '{t}' AS src, transaction_id, account_id, CAST(amount_amt AS REAL) AS amt "
        f'FROM "{t}" WHERE account_id IS NOT NULL{skip_flags(db, t)}' for t in tables)
    db.execute(f"CREATE TEMP VIEW post AS {union}")
    print("transactions (posting lines sum to zero):")
    for t in tables:
        n, ok = db.execute(
            "SELECT count(*), sum(abs(s) < ?) FROM (SELECT sum(amt) AS s FROM post WHERE src = ? "
            "GROUP BY transaction_id)", (CENT, t)).fetchone()
        if not n:
            continue
        flag = "" if n == ok else "  <-- not balanced"
        failed |= n != ok
        print(f"  {t:40} {ok} / {n}{flag}")

    # Cost of goods sold posts through inventory records (*_inventory_lineitem:
    # paired rows on the inventory asset and COGS accounts), not through the
    # transaction lines themselves.
    inventory = [n for (n,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE '%inventory_lineitem'")]
    extra = "".join(f" UNION ALL SELECT '{t}', NULL, account_id, CAST(amount_amt AS REAL) FROM \"{t}\" "
                    f"WHERE account_id IS NOT NULL" for t in inventory)
    db.execute("CREATE TEMP VIEW ledger AS SELECT * FROM post" + extra)

    has_names = db.execute(
        "SELECT count(*) FROM sqlite_master WHERE name = 'abmc_account_user'").fetchone()[0]
    name_join = "LEFT JOIN abmc_account_user u USING (account_id)" if has_names else ""
    name_col = "u.name" if has_names else "NULL"
    rows = db.execute(
        f"SELECT a.account_id, {name_col}, CAST(a.ending_balance_amt AS REAL), coalesce(p.s, 0) "
        f"FROM abmc_account_internal a {name_join} "
        "LEFT JOIN (SELECT account_id, sum(amt) AS s FROM ledger GROUP BY account_id) p USING (account_id)").fetchall()
    stored_sum = sum(r[2] or 0 for r in rows)
    bad = [r for r in rows if abs((r[2] or 0) - r[3]) >= CENT]
    failed |= bool(bad)
    print(f"accounts: {len(rows) - len(bad)} / {len(rows)} stored ending balances match the posting lines "
          f"(stored balances sum to {stored_sum:.2f})")
    for acct, name, stored, computed in sorted(bad, key=lambda r: -abs((r[2] or 0) - r[3])):
        print(f"  account {acct:4} {str(name)[:30]:30} stored {stored:14.2f} computed {computed:14.2f} "
              f"diff {stored - computed:12.2f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
