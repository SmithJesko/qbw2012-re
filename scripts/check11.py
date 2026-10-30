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
  4. duplicates: a table's own id that occurs more than once, e.g. older
     row versions the export keeps. Reported only. The index catalog isn't
     decoded yet, so the id column is picked by name (main_id_column).
Estimates, sales orders and purchase orders don't post, and the audit trail
holds history copies, so they are left out.
Money is compared as integer cents from the export's exact decimal text. An
amount with sub-cent digits is rounded half-even and reported.
Exit status is 1 if any account or transaction check fails.
"""
import collections
import decimal
import sqlite3
import sys

NON_POSTING = ("estimate", "sales_order", "purchase_order", "audit")
NOT_CENTS = []


def cents(text):
    """Integer cents from an exact decimal string (None counts as 0)."""
    if text is None:
        return 0
    d = decimal.Decimal(str(text)) * 100
    c = d.to_integral_value(rounding=decimal.ROUND_HALF_EVEN)
    if c != d:
        NOT_CENTS.append(text)
    return int(c)


def fmt(c):
    sign = "-" if c < 0 else ""
    return f"{sign}{abs(c) // 100}.{abs(c) % 100:02d}"


def has_table(db, name):
    return db.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
                      (name,)).fetchone()[0] > 0


def columns(db, table):
    return [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]


def posting_tables(db):
    out = []
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE '%lineitem'"):
        if name.endswith("inventory_lineitem"):
            continue
        if {"account_id", "amount_amt", "transaction_id"} <= set(columns(db, name)) \
                and not any(n in name for n in NON_POSTING):
            out.append(name)
    return sorted(out)


def skip_flags(db, table):
    """Leave out memorized (template) and no-post lines where the table has
    those flags."""
    cols = set(columns(db, table))
    return "".join(f" AND coalesce({c}, 0) = 0" for c in ("is_memorized_transaction_bool", "is_no_post_bool")
                   if c in cols)


def check_catalog(db):
    if not has_table(db, "_export_report"):
        print("catalog: skipped, no _export_report table")
        return
    total, matched = db.execute(
        "SELECT count(*), sum(exported = catalog_rows) FROM _export_report WHERE catalog_rows > 0").fetchone()
    print(f"catalog: {matched or 0} / {total} tables match the SYSTABLE row count")


def check_transactions(ledger):
    """ledger: list of (table, transaction_id, account_id, cents)."""
    failed = False
    print("transactions (posting lines sum to zero):")
    sums = collections.defaultdict(lambda: collections.defaultdict(int))
    for table, txn, _, amt in ledger:
        sums[table][txn] += amt
    for table in sorted(sums):
        n = len(sums[table])
        ok = sum(1 for s in sums[table].values() if s == 0)
        failed |= n != ok
        print(f"  {table:40} {ok} / {n}{'' if n == ok else '  <-- not balanced'}")
    return failed


def check_accounts(db, ledger):
    if not has_table(db, "abmc_account_internal"):
        print("accounts: skipped, no abmc_account_internal table in this export")
        return False
    computed = collections.defaultdict(int)
    for _, _, acct, amt in ledger:
        computed[acct] += amt
    names = dict(db.execute("SELECT account_id, name FROM abmc_account_user")) \
        if has_table(db, "abmc_account_user") else {}
    rows = [(acct, cents(bal)) for acct, bal in db.execute(
        "SELECT account_id, ending_balance_amt FROM abmc_account_internal")]
    bad = [(acct, stored, computed[acct]) for acct, stored in rows if stored != computed[acct]]
    print(f"accounts: {len(rows) - len(bad)} / {len(rows)} stored ending balances match the posting lines "
          f"(stored balances sum to {fmt(sum(s for _, s in rows))})")
    for acct, stored, comp in sorted(bad, key=lambda r: -abs(r[1] - r[2])):
        print(f"  account {acct:4} {str(names.get(acct))[:30]:30} stored {fmt(stored):>14} "
              f"computed {fmt(comp):>14} diff {fmt(stored - comp):>12}")
    return bool(bad)


def main_id_column(table, cols):
    """The column that names the table's own rows, or None. A first column
    is only taken as the table's id when its name says so: target_id (line
    tables), transaction_id on *_header tables, or <entity>_id on a table
    named after that entity (abmc_account_internal.account_id). Audit and
    history tables repeat ids by design and are skipped."""
    if not cols or "audit" in table or "history" in table:
        return None
    first = cols[0]
    if first == "target_id":
        return first
    if first == "transaction_id":
        return first if table.endswith("_header") else None
    if first.endswith("_id"):
        entity = first[:-3]
        stem = table.split("_", 1)[-1]
        for suffix in ("_internal", "_user", ""):
            if stem == entity + suffix:
                return first
    return None


def check_duplicates(db):
    """Report ids that occur more than once in a table's own id column."""
    found = []
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name <> '_export_report' "
                               "ORDER BY name").fetchall():
        col = main_id_column(table, columns(db, table))
        if col is None:
            continue
        dup = db.execute(f'SELECT "{col}", count(*) FROM "{table}" WHERE "{col}" IS NOT NULL '
                         f'GROUP BY 1 HAVING count(*) > 1 ORDER BY 1').fetchall()
        if dup:
            found.append((table, col, dup))
    if not found:
        print("duplicates: none (no table repeats its own id)")
        return
    print(f"duplicates: {len(found)} tables repeat their own id (report only):")
    for table, col, dup in found:
        ids = ", ".join(f"{i} x{n}" for i, n in dup[:8]) + (" ..." if len(dup) > 8 else "")
        print(f"  {table}.{col}: {len(dup)} ids repeated: {ids}")


def main(path):
    db = sqlite3.connect(path)
    NOT_CENTS.clear()
    check_catalog(db)

    ledger = []
    for t in posting_tables(db):
        for txn, acct, amt in db.execute(
                f'SELECT transaction_id, account_id, amount_amt FROM "{t}" '
                f"WHERE account_id IS NOT NULL{skip_flags(db, t)}"):
            ledger.append((t, txn, acct, cents(amt)))
    failed = check_transactions(ledger)

    # Cost of goods sold posts through inventory records (*_inventory_lineitem:
    # paired rows on the inventory asset and COGS accounts), not through the
    # transaction lines themselves.
    for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                           "AND name LIKE '%inventory_lineitem'").fetchall():
        for acct, amt in db.execute(f'SELECT account_id, amount_amt FROM "{t}" WHERE account_id IS NOT NULL'):
            ledger.append((t, None, acct, cents(amt)))
    failed |= check_accounts(db, ledger)

    check_duplicates(db)
    if NOT_CENTS:
        print(f"note: {len(NOT_CENTS)} amounts had sub-cent digits and were rounded half-even, "
              f"e.g. {NOT_CENTS[:3]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
