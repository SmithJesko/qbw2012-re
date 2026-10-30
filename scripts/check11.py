"""Self-checks for an export written by qbw2012.py / export11.py.

Usage: check11.py BOOKS.sqlite

All checks use only numbers stored in the file itself (ledger rules in
ledger11.py):
  1. catalog: exported rows per table against the SYSTABLE row count
  2. transactions: posting lines sum to zero per transaction
  3. accounts: each account's stored ending_balance_amt against the sum of
     its ledger entries (posting lines plus inventory records)
  4. customers: each customer's stored end_balance_amt against its entries
     on Accounts Receivable accounts
  5. vendors: each vendor's stored end_balance_amt against its entries on
     Accounts Payable accounts plus, for a sales tax agency, the sales tax
     owed to it (entries whose tax item names that agency)
  6. open items: every invoice and bill has no open amount exactly when its
     stored is_paid_bool is set, and open amounts summed per customer equal
     the stored customer balance
  7. duplicates: a table's own id that occurs more than once, e.g. older
     row versions the export keeps. Reported only. The index catalog isn't
     decoded yet, so the id column is picked by name (main_id_column).
Money is compared as integer cents from the export's exact decimal text. An
amount with sub-cent digits is rounded half-even and reported.
Exit status is 1 if any of checks 2 to 6 fails.
"""
import collections
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger11 as L  # noqa: E402


def check_catalog(db):
    if not L.has_table(db, "_export_report"):
        print("catalog: skipped, no _export_report table")
        return
    total, matched = db.execute(
        "SELECT count(*), sum(exported = catalog_rows) FROM _export_report WHERE catalog_rows > 0").fetchone()
    print(f"catalog: {matched or 0} / {total} tables match the SYSTABLE row count")


def check_transactions(entries):
    failed = False
    print("transactions (posting lines sum to zero):")
    sums = collections.defaultdict(lambda: collections.defaultdict(int))
    for e in entries:
        if not e.table.endswith("inventory_lineitem"):
            sums[e.table][e.transaction_id] += e.amount
    for table in sorted(sums):
        n = len(sums[table])
        ok = sum(1 for s in sums[table].values() if s == 0)
        failed |= n != ok
        print(f"  {table:40} {ok} / {n}{'' if n == ok else '  <-- not balanced'}")
    return failed


def compare(label, stored, computed, names):
    """Print stored against computed balances; return True if any differ."""
    bad = [(i, s, computed.get(i, 0)) for i, s in stored.items() if s != computed.get(i, 0)]
    print(f"{label}: {len(stored) - len(bad)} / {len(stored)} stored balances match "
          f"(stored balances sum to {L.fmt(sum(stored.values()))})")
    for i, s, c in sorted(bad, key=lambda r: -abs(r[1] - r[2])):
        print(f"  {i:5} {str(names.get(i))[:32]:32} stored {L.fmt(s):>14} computed {L.fmt(c):>14} "
              f"diff {L.fmt(s - c):>12}")
    return bool(bad)


def stored_balances(db, table, key, column):
    return {i: L.cents(b) for i, b in db.execute(f'SELECT {key}, {column} FROM "{table}"')}


def check_accounts(db, entries, names):
    if not L.has_table(db, "abmc_account_internal"):
        print("accounts: skipped, no abmc_account_internal table in this export")
        return False
    computed = collections.defaultdict(int)
    for e in entries:
        computed[e.account_id] += e.amount
    return compare("accounts", stored_balances(db, "abmc_account_internal", "account_id", "ending_balance_amt"),
                   computed, names.account)


def check_customers(db, entries, names):
    if not L.has_table(db, "abmc_customer_internal"):
        print("customers: skipped, no abmc_customer_internal table in this export")
        return False
    computed = collections.defaultdict(int)
    for e in entries:
        if names.account_type.get(e.account_id) == L.AR:
            computed[e.customer_id] += e.amount
    return compare("customers", stored_balances(db, "abmc_customer_internal", "customer_id", "end_balance_amt"),
                   computed, names.customer)


def check_vendors(db, entries, names):
    if not L.has_table(db, "abmc_vendor_internal"):
        print("vendors: skipped, no abmc_vendor_internal table in this export")
        return False
    agency = L.tax_agencies(db)
    computed = collections.defaultdict(int)
    for e in entries:
        ty = names.account_type.get(e.account_id)
        if ty == L.AP:
            computed[e.vendor_id] += e.amount
        elif ty == 7 and e.item_id in agency:
            computed[agency[e.item_id]] += e.amount
    return compare("vendors", stored_balances(db, "abmc_vendor_internal", "vendor_id", "end_balance_amt"),
                   computed, names.vendor)


def check_open_items(db, entries, names):
    _, items = L.open_items(db, entries, names)
    failed = False
    for side, header in (("AR", "abmc_invoice_header"), ("AP", "abmc_bill_header")):
        if not L.has_table(db, header):
            continue
        open_by_txn = {i.transaction_id: i.open for i in items if i.side == side}
        paid = {t: p for t, p in db.execute(f'SELECT transaction_id, is_paid_bool FROM "{header}"')
                if t in open_by_txn}
        bad = [t for t, p in paid.items() if (open_by_txn[t] == 0) != bool(p)]
        failed |= bool(bad)
        kind = "invoices" if side == "AR" else "bills"
        print(f"open items: {len(paid) - len(bad)} / {len(paid)} {kind} are fully applied exactly when "
              f"marked paid{'' if not bad else '  <-- ' + ', '.join(map(str, bad[:8]))}")
    if L.has_table(db, "abmc_customer_internal"):
        per = collections.defaultdict(int)
        for i in items:
            if i.side == "AR":
                per[i.party_id] += i.open
        stored = stored_balances(db, "abmc_customer_internal", "customer_id", "end_balance_amt")
        bad = [c for c, s in stored.items() if s != per.get(c, 0)]
        failed |= bool(bad)
        print(f"open items: {len(stored) - len(bad)} / {len(stored)} customers' open AR items sum to "
              f"their stored balance")
    return failed


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
        col = main_id_column(table, L.columns(db, table))
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
    L.NOT_CENTS.clear()
    check_catalog(db)
    entries = L.entries(db)
    names = L.Names(db)
    failed = check_transactions(entries)
    failed |= check_accounts(db, entries, names)
    failed |= check_customers(db, entries, names)
    failed |= check_vendors(db, entries, names)
    failed |= check_open_items(db, entries, names)
    check_duplicates(db)
    if L.NOT_CENTS:
        print(f"note: {len(L.NOT_CENTS)} amounts had sub-cent digits and were rounded half-even, "
              f"e.g. {L.NOT_CENTS[:3]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
