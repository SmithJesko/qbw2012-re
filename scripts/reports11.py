"""Accounting reports from an export (qbw2012.py / export11.py), as CSV.

Usage: reports11.py BOOKS.sqlite [--out DIR] [--as-of YYYY-MM-DD]
                    [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                    [--period month|quarter|year|total] [--fiscal-year-start MONTH]

Writes to DIR (default reports/):
  trial_balance.csv     as of --as-of
  balance_sheet.csv     as of --as-of
  profit_and_loss.csv   --from to --to, one column per period plus a total
  general_ledger.csv    --from to --to, every entry with names, running balance
  ar_open_items.csv, ar_aging.csv, ap_open_items.csv, ap_aging.csv

Defaults: --as-of is the latest transaction date in the file, --from is the
start of the fiscal year containing --as-of, --to is --as-of. The fiscal
year start month isn't stored in a decodable place, so it is an option
(default 1, January). Income and expense accounts reset at each fiscal year
start: earlier years' net income is shown in Retained Earnings, as in a
closed set of books.

Open items are the ones open in the file as it is (applications carry no
date of their own); --as-of only sets the date that aging counts from.

Amounts are positive in the debit and credit columns. Balance sheet and P&L
show each section in its natural sign (assets, expenses: debit; liabilities,
equity, income: credit). Every report ends with a tie-out check printed to
the console; exit status is 1 if any fails. Derived from the file only, not
compared with QuickBooks (see NOTES F19).
"""
import argparse
import collections
import csv
import datetime
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger11 as L  # noqa: E402
from check11 import open_export  # noqa: E402

SECTIONS = [  # (heading, account types, natural side)
    ("Income", (10,), "credit"), ("Cost of Goods Sold", (11,), "debit"), ("Expense", (12,), "debit"),
    ("Other Income", (13,), "credit"), ("Other Expense", (14,), "debit"),
]
BS_SECTIONS = [
    ("Current Assets", (0, 1, 2), "debit"), ("Fixed Assets", (3,), "debit"), ("Other Assets", (4,), "debit"),
    ("Current Liabilities", (5, 6, 7), "credit"), ("Long Term Liabilities", (8,), "credit"),
    ("Equity", (9,), "credit"),
]
BUCKETS = ("current", "1-30", "31-60", "61-90", "91+")


def parse_date(s):
    return datetime.date.fromisoformat(s)


def fy_start(d, month):
    return datetime.date(d.year if d.month >= month else d.year - 1, month, 1)


def money(c):
    return L.fmt(c) if c else ""


def side(c, natural):
    """Cents in the section's natural sign (stored: debit negative)."""
    return -c if natural == "debit" else c


def debit_credit(c):
    return (money(-c), "") if c < 0 else ("", money(c))


class Book:
    def __init__(self, db, fiscal_start):
        self.db = db
        self.names = L.Names(db)
        self.entries = [e for e in L.entries(db) if e.date]
        self.fiscal_start = fiscal_start
        self.retained = next((a for a, n in self.names.account.items()
                              if self.names.account_type.get(a) == 9 and n.split(":")[-1] == "Retained Earnings"),
                             None)

    def kind(self, acct):
        return self.names.account_type.get(acct)

    def is_pl(self, acct):
        return self.kind(acct) in L.PL_TYPES

    def order(self, acct):
        return (self.kind(acct) if self.kind(acct) is not None else 99, self.names.account_num.get(acct, ""),
                self.names.account.get(acct, ""))

    def balances(self, as_of):
        """Account balances as of a date, closing earlier fiscal years' income
        and expense into Retained Earnings. Returns (balances, prior_net)."""
        start = fy_start(as_of, self.fiscal_start).isoformat()
        end = as_of.isoformat()
        bal, prior = collections.defaultdict(int), 0
        for e in self.entries:
            if e.date > end:
                continue
            if self.is_pl(e.account_id) and e.date < start:
                prior += e.amount
            else:
                bal[e.account_id] += e.amount
        if prior:
            bal[self.retained] += prior
        return bal, prior

    def account_label(self, acct):
        if acct is None:
            return "", "Retained Earnings (prior years' net income)"
        return self.names.account_num.get(acct, ""), self.names.account.get(acct, f"account {acct}")


def write(path, header, rows):
    # utf-8-sig: the byte-order mark makes Excel read the file as UTF-8
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def report_trial_balance(book, as_of, out):
    bal, _ = book.balances(as_of)
    rows, dr, cr = [], 0, 0
    for acct in sorted((a for a, c in bal.items() if c), key=book.order):
        num, name = book.account_label(acct)
        d, c = debit_credit(bal[acct])
        dr += -bal[acct] if bal[acct] < 0 else 0
        cr += bal[acct] if bal[acct] > 0 else 0
        rows.append([num, name, L.ACCOUNT_TYPES.get(book.kind(acct), ""), d, c])
    rows.append(["", "TOTAL", "", L.fmt(dr), L.fmt(cr)])
    write(os.path.join(out, "trial_balance.csv"), ["number", "account", "type", "debit", "credit"], rows)
    return [("trial balance: debits equal credits", dr == cr, f"{L.fmt(dr)} / {L.fmt(cr)}")]


def net_income(book, start, end):
    return sum(e.amount for e in book.entries if book.is_pl(e.account_id) and start <= e.date <= end)


def report_balance_sheet(book, as_of, out):
    bal, _ = book.balances(as_of)
    ni = net_income(book, fy_start(as_of, book.fiscal_start).isoformat(), as_of.isoformat())
    rows, totals = [], {}
    for heading, types, natural in BS_SECTIONS:
        accts = sorted((a for a, c in bal.items() if c and book.kind(a) in types
                        or (a is None and 9 in types and c)), key=lambda a: book.order(a) if a else (9, "", "~"))
        total = 0
        for acct in accts:
            num, name = book.account_label(acct)
            v = side(bal[acct], natural)
            total += v
            rows.append([heading, num, name, L.fmt(v)])
        if heading == "Equity" and ni:
            rows.append([heading, "", "Net Income", L.fmt(ni)])
            total += ni
        rows.append([heading, "", f"Total {heading}", L.fmt(total)])
        totals[heading] = total
    assets = totals["Current Assets"] + totals["Fixed Assets"] + totals["Other Assets"]
    liab = totals["Current Liabilities"] + totals["Long Term Liabilities"]
    rows.append(["", "", "TOTAL ASSETS", L.fmt(assets)])
    rows.append(["", "", "TOTAL LIABILITIES & EQUITY", L.fmt(liab + totals["Equity"])])
    write(os.path.join(out, "balance_sheet.csv"), ["section", "number", "account", "amount"], rows)
    return [("balance sheet: assets equal liabilities + equity", assets == liab + totals["Equity"],
             f"{L.fmt(assets)} / {L.fmt(liab + totals['Equity'])}")], ni


def fy_label(base, fiscal_start):
    """2016 for a calendar year, FY2015-16 for one starting mid-year."""
    return str(base.year) if fiscal_start == 1 else f"FY{base.year}-{(base.year + 1) % 100:02d}"


def periods(start, end, kind, fiscal_start):
    if kind == "total":
        return [(start, end, f"{start.isoformat()} to {end.isoformat()}")]
    out, cur = [], start
    step = {"month": 1, "quarter": 3, "year": 12}[kind]
    while cur <= end:
        if kind == "month":
            nxt = datetime.date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
            label = cur.strftime("%Y-%m")
        else:
            base = fy_start(cur, fiscal_start)
            months = (cur.year - base.year) * 12 + cur.month - base.month
            first = months // step * step
            pstart = datetime.date(base.year + (base.month - 1 + first) // 12, (base.month - 1 + first) % 12 + 1, 1)
            total = base.month - 1 + first + step
            nxt = datetime.date(base.year + total // 12, total % 12 + 1, 1)
            year = fy_label(base, fiscal_start)
            label = f"{year} Q{first // 3 + 1}" if kind == "quarter" else year
        p_end = min(nxt - datetime.timedelta(days=1), end)
        out.append((cur, p_end, label))
        cur = nxt
    return out


def report_profit_and_loss(book, start, end, kind, out):
    cols = periods(start, end, kind, book.fiscal_start)
    if len(cols) > 1:
        cols.append((start, end, "Total"))
    s, e = start.isoformat(), end.isoformat()
    act = collections.defaultdict(lambda: [0] * len(cols))
    for x in book.entries:
        if not book.is_pl(x.account_id) or not s <= x.date <= e:
            continue
        for i, (ps, pe, _) in enumerate(cols):
            if ps.isoformat() <= x.date <= pe.isoformat():
                act[x.account_id][i] += x.amount
    rows, sums = [], {}
    for heading, types, natural in SECTIONS:
        total = [0] * len(cols)
        for acct in sorted((a for a in act if book.kind(a) in types and any(act[a])), key=book.order):
            v = [side(c, natural) for c in act[acct]]
            total = [t + x for t, x in zip(total, v)]
            num, name = book.account_label(acct)
            rows.append([heading, num, name] + [money(x) for x in v])
        rows.append([heading, "", f"Total {heading}"] + [L.fmt(x) for x in total])
        sums[heading] = total
        if heading == "Cost of Goods Sold":
            rows.append(["", "", "Gross Profit"] + [L.fmt(a - b) for a, b in zip(sums["Income"], total)])
        if heading == "Expense":
            rows.append(["", "", "Net Ordinary Income"] + [
                L.fmt(a - b - c) for a, b, c in zip(sums["Income"], sums["Cost of Goods Sold"], total)])
    net = [a - b - c + d - f for a, b, c, d, f in zip(*(sums[h] for h, _, _ in SECTIONS))]
    rows.append(["", "", "Net Income"] + [L.fmt(x) for x in net])
    write(os.path.join(out, "profit_and_loss.csv"), ["section", "number", "account"] + [c[2] for c in cols], rows)
    direct = net_income(book, s, e)
    return [("P&L: net income equals the income and expense entries for the period", net[-1] == direct,
             L.fmt(net[-1]))], net[-1]


def report_general_ledger(book, start, end, out):
    s, e = start.isoformat(), end.isoformat()
    opening, inside = collections.defaultdict(int), collections.defaultdict(list)
    fy = fy_start(start, book.fiscal_start).isoformat()
    for x in book.entries:
        if x.date > e:
            continue
        if x.date >= s:
            inside[x.account_id].append(x)
        elif not book.is_pl(x.account_id) or x.date >= fy:
            opening[x.account_id] += x.amount
        else:
            opening[book.retained] += x.amount  # earlier fiscal years close to Retained Earnings
    rows, closing, dr, cr = [], {}, 0, 0
    for acct in sorted(set(opening) | set(inside), key=book.order):
        num, name = book.account_label(acct)
        run = opening.get(acct, 0)
        rows.append([num, name, s, "", "", "Opening balance", "", "", "", "", L.fmt(-run)])
        for x in sorted(inside[acct], key=lambda x: (x.date, x.transaction_id or 0, x.target_id or 0)):
            run += x.amount
            d, c = debit_credit(x.amount)
            dr += -x.amount if x.amount < 0 else 0
            cr += x.amount if x.amount > 0 else 0
            who, kind = book.names.name_of(x)
            rows.append([num, name, x.date, x.txn_type, x.num or "", who, kind, x.memo or "", d, c, L.fmt(-run)])
        closing[acct] = run
    write(os.path.join(out, "general_ledger.csv"),
          ["number", "account", "date", "type", "num", "name", "name_type", "memo", "debit", "credit",
           "balance (debit +)"], rows)
    checks = [("general ledger: debits equal credits in the period", dr == cr, f"{L.fmt(dr)} / {L.fmt(cr)}")]
    if fy_start(start, book.fiscal_start) == fy_start(end, book.fiscal_start):
        tb, _ = book.balances(end)
        ok = all(closing.get(a, 0) == tb.get(a, 0) for a in set(closing) | {a for a in tb if a is not None})
        checks.append(("general ledger: closing balances equal the trial balance", ok, ""))
    return checks


def aging(days):
    if days <= 0:
        return "current"
    return "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "91+"


def report_open_items(book, as_of, out):
    open_, _ = L.open_items(book.db, book.entries, book.names)
    due = {}
    for header in ("abmc_invoice_header", "abmc_bill_header"):
        if L.has_table(book.db, header):
            due.update({t: (d or "")[:10] for t, d in book.db.execute(
                f'SELECT transaction_id, due_date FROM "{header}"')})
    checks = []
    for side_, acct_type, party_names, label in (("AR", L.AR, book.names.customer, "customer"),
                                                ("AP", L.AP, book.names.vendor, "vendor")):
        sign = -1 if side_ == "AR" else 1  # AR shown as owed to the company, AP as owed by it
        rows, summary = [], collections.defaultdict(lambda: [0] * len(BUCKETS))
        for i in sorted((i for i in open_ if i.side == side_),
                        key=lambda i: (party_names.get(i.party_id, ""), i.date, i.transaction_id)):
            d = due.get(i.transaction_id) or i.date
            days = (as_of - parse_date(d)).days if d else 0
            b = aging(days)
            summary[i.party_id][BUCKETS.index(b)] += sign * i.open
            rows.append([party_names.get(i.party_id, ""), i.txn_type, i.date, i.num or "", d, max(days, 0), b,
                         L.fmt(sign * i.amount), L.fmt(sign * i.open)])
        write(os.path.join(out, f"{side_.lower()}_open_items.csv"),
              [label, "type", "date", "num", "due_date", "days_past_due", "aging", "amount", "open"], rows)
        srows = [[party_names.get(p, "")] + [money(x) for x in v] + [L.fmt(sum(v))]
                 for p, v in sorted(summary.items(), key=lambda kv: party_names.get(kv[0], ""))]
        totals = [sum(v[k] for v in summary.values()) for k in range(len(BUCKETS))]
        srows.append(["TOTAL"] + [L.fmt(x) for x in totals] + [L.fmt(sum(totals))])
        write(os.path.join(out, f"{side_.lower()}_aging.csv"), [label] + list(BUCKETS) + ["total"], srows)
        ledger = sign * sum(e.amount for e in book.entries if book.kind(e.account_id) == acct_type)
        checks.append((f"{side_} open items: total equals the {L.ACCOUNT_TYPES[acct_type]} balance",
                       sum(totals) == ledger, f"{L.fmt(sum(totals))} / {L.fmt(ledger)}"))
    return checks


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("books")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--as-of")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--period", choices=("month", "quarter", "year", "total"), default="month")
    ap.add_argument("--fiscal-year-start", type=int, default=1, choices=range(1, 13), metavar="MONTH")
    args = ap.parse_args(argv)

    book = Book(open_export(args.books), args.fiscal_year_start)
    if not book.entries:
        sys.exit("no dated ledger entries in this export")
    as_of = parse_date(args.as_of) if args.as_of else parse_date(max(e.date for e in book.entries))
    end = parse_date(args.end) if args.end else as_of
    start = parse_date(args.start) if args.start else fy_start(end, args.fiscal_year_start)
    os.makedirs(args.out, exist_ok=True)

    checks = report_trial_balance(book, as_of, args.out)
    bs_checks, bs_ni = report_balance_sheet(book, as_of, args.out)
    checks += bs_checks
    pl_checks, pl_ni = report_profit_and_loss(book, start, end, args.period, args.out)
    checks += pl_checks
    if start == fy_start(as_of, args.fiscal_year_start) and end == as_of:
        checks.append(("P&L net income equals Net Income on the balance sheet", pl_ni == bs_ni, L.fmt(pl_ni)))
    checks += report_general_ledger(book, start, end, args.out)
    checks += report_open_items(book, as_of, args.out)

    print(f"reports as of {as_of}, P&L and ledger {start} to {end}, fiscal year starts in month "
          f"{args.fiscal_year_start} -> {args.out}/")
    failed = False
    for label, ok, detail in checks:
        failed |= not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}{'  (' + detail + ')' if detail else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
