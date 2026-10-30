"""General ledger, names and open items from an export (export11.py).

Shared by check11.py and reports11.py. Everything here is derived from the
exported tables only; the conventions are inferred from the files and
recorded in NOTES F16 and F19:

- posting lines: *_lineitem rows with account_id set, leaving out
  estimates, sales orders, purchase orders, the audit trail, memorized
  (template) and no-post lines; cost of goods sold comes from the
  *_inventory_lineitem records, each tied to its line by target_id
- amounts: debits are stored negative, credits positive
- account classes: abmc_account_user.type, see ACCOUNT_TYPES
- applications (payments and credits against invoices and bills):
  abmc_transaction_link rows between real lines, without secondary links,
  one per (link_type, target_1_id, target_2_id)
"""
import collections
import decimal

NON_POSTING = ("estimate", "sales_order", "purchase_order", "audit")

# abmc_account_user.type, named after the accounts each code holds (NOTES F19)
ACCOUNT_TYPES = {
    0: "Bank", 1: "Accounts Receivable", 2: "Other Current Asset", 3: "Fixed Asset",
    4: "Other Asset", 5: "Accounts Payable", 6: "Credit Card", 7: "Other Current Liability",
    8: "Long Term Liability", 9: "Equity", 10: "Income", 11: "Cost of Goods Sold",
    12: "Expense", 13: "Other Income", 14: "Other Expense", 15: "Non-Posting",
}
ASSET_TYPES = (0, 1, 2, 3, 4)
LIABILITY_TYPES = (5, 6, 7, 8)
EQUITY_TYPES = (9,)
PL_TYPES = (10, 11, 12, 13, 14)
AR, AP = 1, 5

# abmc_transaction_link.link_type values that apply payments or credits
# (NOTES F19): with these, every invoice and bill's open amount is zero
# exactly when its is_paid_bool flag is set, and a refund check cancels the
# credit memo it refunds (type 2).
AR_LINK_TYPES = (2, 4, 5)
AP_LINK_TYPES = (6, 8, 16)

# Transaction type labels, from the table names
TXN_TYPES = {
    "bill": "Bill", "bill_check": "Bill Payment", "check": "Check",
    "credit_cardcharge": "Credit Card Charge", "credit_memo": "Credit Memo", "deposit": "Deposit",
    "general_journal": "General Journal", "inventory_adjust": "Inventory Adjustment",
    "invoice": "Invoice", "item_assembly": "Build Assembly", "received_payment": "Payment",
    "sales_receipt": "Sales Receipt", "transfer": "Transfer", "liability_payment": "Liability Check",
    "paycheck": "Paycheck",
}

Entry = collections.namedtuple(
    "Entry", "table txn_type transaction_id target_id date num customer_id vendor_id "
             "employee_id other_id item_id account_id memo amount")

NOT_CENTS = []


def cents(text):
    """Integer cents from an exact decimal string (None counts as 0).
    Sub-cent values are rounded half-even and recorded in NOT_CENTS."""
    if text is None:
        return 0
    d = decimal.Decimal(str(text)) * 100
    c = d.to_integral_value(rounding=decimal.ROUND_HALF_EVEN)
    if c != d:
        NOT_CENTS.append(text)
    return int(c)


def fmt(c):
    """Cents as a plain decimal string, e.g. -1234.50."""
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
    """SQL to leave out memorized (template) and no-post lines."""
    cols = set(columns(db, table))
    return "".join(f" AND coalesce({c}, 0) = 0" for c in ("is_memorized_transaction_bool", "is_no_post_bool")
                   if c in cols)


def txn_type(table):
    stem = table.split("_", 1)[1].rsplit("_lineitem", 1)[0]
    return TXN_TYPES.get(stem, stem.replace("_", " ").title())


def _col(cols, name):
    return f'"{name}"' if name in cols else "NULL"


def entries(db):
    """All posting entries: lines first, then inventory records (COGS)."""
    out, lines = [], {}
    fields = ("transaction_id", "target_id", "transaction_date", "doc_num", "customer_id", "vendor_id",
              "employee_id", "other_id", "item_id", "account_id", "memo", "amount_amt")
    for t in posting_tables(db):
        cols = set(columns(db, t))
        select = ", ".join(_col(cols, f) for f in fields)
        for row in db.execute(f'SELECT {select} FROM "{t}" WHERE account_id IS NOT NULL{skip_flags(db, t)}'):
            txn, target, date, num, cust, vend, emp, oth, item, acct, memo, amt = row
            e = Entry(t, txn_type(t), txn, target, (date or "")[:10], num, cust, vend, emp, oth, item,
                      acct, memo, cents(amt))
            out.append(e)
            lines[target] = e
    for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                           "AND name LIKE '%inventory_lineitem'").fetchall():
        for target, acct, amt in db.execute(f'SELECT target_id, account_id, amount_amt FROM "{t}" '
                                            "WHERE account_id IS NOT NULL"):
            line = lines.get(target)
            if line is None:
                continue
            out.append(line._replace(table=t, account_id=acct, memo="Cost of goods sold", amount=cents(amt)))
    return out


def _tree(db, table, key):
    """id -> full name with parents joined by ':' (Customer:Job, Parent:Sub)."""
    if not has_table(db, table):
        return {}
    cols = set(columns(db, table))
    parent = "parent_id" if "parent_id" in cols else "NULL"
    rows = {i: (n, p) for i, n, p in db.execute(f'SELECT {key}, name, {parent} FROM "{table}"')}
    out = {}
    for i in rows:
        parts, seen, cur = [], set(), i
        while cur in rows and cur not in seen:
            seen.add(cur)
            parts.append(rows[cur][0] or "")
            cur = rows[cur][1]
        out[i] = ":".join(reversed(parts))
    return out


class Names:
    def __init__(self, db):
        self.account = _tree(db, "abmc_account_user", "account_id")
        self.customer = _tree(db, "abmc_customer_user", "customer_id")
        self.vendor = _tree(db, "abmc_vendor_user", "vendor_id")
        self.employee = _tree(db, "abmc_employee", "employee_id")
        self.other = _tree(db, "abmc_other_user", "other_id")
        self.account_type, self.account_num = {}, {}
        if has_table(db, "abmc_account_user"):
            for a, ty, num in db.execute("SELECT account_id, type, account_num FROM abmc_account_user"):
                self.account_type[a] = ty
                self.account_num[a] = num or ""

    def name_of(self, e):
        """(name, kind) for an entry: customer/job, vendor, employee or other."""
        for ident, table, kind in ((e.customer_id, self.customer, "Customer"),
                                   (e.vendor_id, self.vendor, "Vendor"),
                                   (e.employee_id, self.employee, "Employee"),
                                   (e.other_id, self.other, "Other")):
            if ident and ident in table:
                return table[ident], kind
        return "", ""


def tax_agencies(db):
    """item_id -> vendor_id of the agency a sales tax item is owed to."""
    if not has_table(db, "abmc_item_user") or "tax_agency_id" not in columns(db, "abmc_item_user"):
        return {}
    return dict(db.execute("SELECT item_id, tax_agency_id FROM abmc_item_user "
                           "WHERE tax_agency_id IS NOT NULL AND tax_agency_id <> 0"))


def applications(db, entries_):
    """transaction_id -> applied cents per link type, from real line links."""
    real = {e.target_id for e in entries_}
    links = {}
    if not has_table(db, "abmc_transaction_link"):
        return {}
    cols = set(columns(db, "abmc_transaction_link"))
    sec = "is_secondary_bool" if "is_secondary_bool" in cols else "0"
    for txn, ty, amt, t1, t2, secondary in db.execute(
            f"SELECT transaction_id, link_type, amount_amt, target_1_id, target_2_id, {sec} "
            "FROM abmc_transaction_link WHERE amount_amt IS NOT NULL "
            "AND coalesce(is_memorized_transaction_bool, 0) = 0"):
        if t1 in real and t2 in real and not secondary:
            links[(ty, t1, t2)] = (txn, cents(amt))
    out = collections.defaultdict(lambda: collections.defaultdict(int))
    for (ty, _, _), (txn, amt) in links.items():
        out[txn][ty] += amt
    return out


OpenItem = collections.namedtuple("OpenItem", "side transaction_id txn_type date num party_id amount open")


def open_items(db, entries_, names):
    """AR and AP open amounts per transaction.

    A transaction's AR (or AP) amount is the sum of its lines on accounts of
    that class; payments and credits applied to it reduce it in magnitude:
    open = sign(amount) * (|amount| - |applied|). Returns OpenItems with
    open != 0 (and the full list as the second value)."""
    applied = applications(db, entries_)
    groups = {}
    for e in entries_:
        ty = names.account_type.get(e.account_id)
        if ty not in (AR, AP) or e.table.endswith("inventory_lineitem"):
            continue
        side = "AR" if ty == AR else "AP"
        party = e.customer_id if side == "AR" else e.vendor_id
        key = (side, e.transaction_id)
        g = groups.setdefault(key, [e, 0, party])
        g[1] += e.amount
        g[2] = g[2] or party
    items = []
    for (side, txn), (e, amount, party) in groups.items():
        types = AR_LINK_TYPES if side == "AR" else AP_LINK_TYPES
        used = abs(sum(applied.get(txn, {}).get(t, 0) for t in types))
        sign = 1 if amount > 0 else -1
        open_ = sign * (abs(amount) - used) if amount else 0
        items.append(OpenItem(side, txn, e.txn_type, e.date, e.num, party, amount, open_))
    return [i for i in items if i.open], items
