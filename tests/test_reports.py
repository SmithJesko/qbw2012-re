"""Unit tests for ledger11 and reports11 on a tiny synthetic export.

The export is built in memory: one customer, a 2015 sale paid in part, a
2016 sale, an expense and a refund pair. No company files.

Run from the repo root: python3 -I -m unittest discover -s tests
"""
import csv
import datetime
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import ledger11 as L  # noqa: E402
import reports11 as R  # noqa: E402

LINE_COLS = ("target_id", "transaction_id", "transaction_date", "doc_num", "customer_id", "vendor_id",
             "item_id", "account_id", "memo", "amount_amt", "is_memorized_transaction_bool")


def build(db):
    ex = db.execute
    ex("CREATE TABLE abmc_account_user (account_id, name, parent_id, type, account_num)")
    ex("CREATE TABLE abmc_account_internal (account_id, ending_balance_amt)")
    accounts = [(1, "Checking", 0, 0, "1000"), (4, "Accounts Receivable", 0, 1, "1100"),
                (22, "Retained Earnings", 0, 9, "3900"), (40, "Sales", 0, 10, "4000"),
                (60, "Rent", 0, 12, "6000")]
    ex("CREATE TABLE abmc_customer_user (customer_id, name, parent_id)")
    ex("CREATE TABLE abmc_customer_internal (customer_id, end_balance_amt)")
    db.executemany("INSERT INTO abmc_account_user VALUES (?, ?, ?, ?, ?)", accounts)
    db.executemany("INSERT INTO abmc_customer_user VALUES (?, ?, ?)", [(7, "Smith", 0), (8, "Kitchen", 7)])
    cols = ", ".join(LINE_COLS)
    for t in ("abmc_invoice_lineitem", "abmc_received_payment_lineitem", "abmc_check_lineitem",
              "abmc_estimate_lineitem"):
        ex(f"CREATE TABLE {t} ({cols})")
    ins = lambda t, *r: ex(f"INSERT INTO {t} VALUES ({', '.join('?' * len(LINE_COLS))})", r)  # noqa: E731
    # invoice 100 (2015): 300.00 to Smith:Kitchen; debit AR, credit Sales (debits stored negative)
    ins("abmc_invoice_lineitem", 1001, 100, "2015-06-01 00:00:00", "1", 8, None, None, 4, None, "-300.00", 0)
    ins("abmc_invoice_lineitem", 1002, 100, "2015-06-01 00:00:00", "1", 8, None, None, 40, "job", "300.00", 0)
    # payment 200 (2016): 120.00 applied to invoice 100
    ins("abmc_received_payment_lineitem", 2001, 200, "2016-02-01 00:00:00", None, 8, None, None, 1, None,
        "-120.00", 0)
    ins("abmc_received_payment_lineitem", 2002, 200, "2016-02-01 00:00:00", None, 8, None, None, 4, None,
        "120.00", 0)
    # invoice 101 (2016): 50.00
    ins("abmc_invoice_lineitem", 1011, 101, "2016-03-01 00:00:00", "2", 8, None, None, 4, None, "-50.00", 0)
    ins("abmc_invoice_lineitem", 1012, 101, "2016-03-01 00:00:00", "2", 8, None, None, 40, None, "50.00", 0)
    # check 300 (2016): rent 80.00
    ins("abmc_check_lineitem", 3001, 300, "2016-03-05 00:00:00", "77", None, None, None, 60, None, "-80.00", 0)
    ins("abmc_check_lineitem", 3002, 300, "2016-03-05 00:00:00", "77", None, None, None, 1, None, "80.00", 0)
    # a memorized (template) invoice and an estimate never post
    ins("abmc_invoice_lineitem", 1021, 102, "2016-04-01 00:00:00", "3", 8, None, None, 4, None, "-999.00", 1)
    ins("abmc_estimate_lineitem", 4001, 400, "2016-04-01 00:00:00", "E1", 8, None, None, 4, None, "-5.00", 0)
    ex("CREATE TABLE abmc_transaction_link (link_id, transaction_id, transaction_2_id, target_1_id, "
       "target_2_id, link_type, amount_amt, is_secondary_bool, is_memorized_transaction_bool)")
    # the application, its sibling, and a repeat of it (an older row version)
    db.executemany("INSERT INTO abmc_transaction_link VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        (1, 100, 200, 1002, 2002, 4, "120.00", 0, 0),
        (2, 200, 100, 2002, 1002, 4, "120.00", 0, 0),
        (3, 100, 200, 1002, 2002, 4, "120.00", 0, 0)])
    ex("CREATE TABLE abmc_invoice_header (transaction_id, due_date, is_paid_bool)")
    db.executemany("INSERT INTO abmc_invoice_header VALUES (?, ?, ?)",
                   [(100, "2015-07-01 00:00:00", 0), (101, "2016-04-01 00:00:00", 0)])
    db.executemany("INSERT INTO abmc_account_internal VALUES (?, ?)",
                   [(1, "-40.00"), (4, "-230.00"), (22, "0"), (40, "350.00"), (60, "-80.00")])
    db.executemany("INSERT INTO abmc_customer_internal VALUES (?, ?)", [(7, "0"), (8, "-230.00")])


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        build(self.db)
        self.entries = L.entries(self.db)
        self.names = L.Names(self.db)

    def test_posting_rules(self):
        self.assertEqual(len(self.entries), 8)  # no memorized invoice, no estimate
        self.assertEqual(sum(e.amount for e in self.entries), 0)

    def test_cents(self):
        self.assertEqual(L.cents("-1241.22"), -124122)
        self.assertEqual(L.cents("0"), 0)
        self.assertEqual(L.cents(None), 0)
        self.assertEqual(L.fmt(-5), "-0.05")

    def test_names(self):
        self.assertEqual(self.names.customer[8], "Smith:Kitchen")
        e = next(e for e in self.entries if e.transaction_id == 100)
        self.assertEqual(self.names.name_of(e), ("Smith:Kitchen", "Customer"))

    def test_open_items_partial_payment(self):
        open_, _ = L.open_items(self.db, self.entries, self.names)
        by_txn = {i.transaction_id: i.open for i in open_}
        self.assertEqual(by_txn, {100: -18000, 101: -5000})  # 180.00 and 50.00 still owed

    def test_balances_match_stored(self):
        computed = {}
        for e in self.entries:
            computed[e.account_id] = computed.get(e.account_id, 0) + e.amount
        stored = {a: L.cents(b) for a, b in self.db.execute("SELECT * FROM abmc_account_internal")}
        self.assertEqual({a: computed.get(a, 0) for a in stored}, stored)


class ReportsTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        build(self.db)
        self.book = R.Book(self.db, 1)
        self.out = tempfile.mkdtemp()

    def test_year_end_close(self):
        bal, prior = self.book.balances(datetime.date(2016, 12, 31))
        self.assertEqual(prior, 30000)  # 2015 net income
        self.assertEqual(bal[22], 30000)  # closed into Retained Earnings
        self.assertEqual(bal[40], 5000)  # 2016 sales only

    def test_trial_balance_and_balance_sheet(self):
        as_of = datetime.date(2016, 12, 31)
        for label, ok, _ in R.report_trial_balance(self.book, as_of, self.out):
            self.assertTrue(ok, label)
        checks, ni = R.report_balance_sheet(self.book, as_of, self.out)
        self.assertTrue(all(ok for _, ok, _ in checks))
        self.assertEqual(ni, -3000)  # 2016: 50.00 sales - 80.00 rent
        rows = read(os.path.join(self.out, "balance_sheet.csv"))
        self.assertIn(["", "", "TOTAL ASSETS", "270.00"], rows)  # 40.00 cash + 230.00 receivable

    def test_profit_and_loss(self):
        checks, net = R.report_profit_and_loss(self.book, datetime.date(2015, 1, 1), datetime.date(2016, 12, 31),
                                               "year", self.out)
        self.assertTrue(all(ok for _, ok, _ in checks))
        rows = read(os.path.join(self.out, "profit_and_loss.csv"))
        self.assertEqual(rows[0][3:], ["2015", "2016", "Total"])
        self.assertEqual(rows[-1], ["", "", "Net Income", "300.00", "-30.00", "270.00"])
        self.assertEqual(net, 27000)

    def test_general_ledger_ties_to_trial_balance(self):
        checks = R.report_general_ledger(self.book, datetime.date(2016, 1, 1), datetime.date(2016, 12, 31),
                                         self.out)
        self.assertTrue(all(ok for _, ok, _ in checks), checks)

    def test_open_items_and_aging(self):
        checks = R.report_open_items(self.book, datetime.date(2016, 4, 15), self.out)
        self.assertTrue(all(ok for _, ok, _ in checks), checks)
        aging = read(os.path.join(self.out, "ar_aging.csv"))
        # invoice 101 is 14 days past due, invoice 100 (180.00 left) 289 days
        self.assertEqual(aging[1], ["Smith:Kitchen", "", "50.00", "", "", "180.00", "230.00"])

    def test_period_labels(self):
        q = R.periods(datetime.date(2015, 7, 1), datetime.date(2015, 12, 31), "quarter", 7)
        self.assertEqual([p[2] for p in q], ["FY2015-16 Q1", "FY2015-16 Q2"])
        m = R.periods(datetime.date(2016, 1, 15), datetime.date(2016, 3, 2), "month", 1)
        self.assertEqual([(p[0].isoformat(), p[1].isoformat(), p[2]) for p in m],
                         [("2016-01-15", "2016-01-31", "2016-01"), ("2016-02-01", "2016-02-29", "2016-02"),
                          ("2016-03-01", "2016-03-02", "2016-03")])


if __name__ == "__main__":
    unittest.main()
