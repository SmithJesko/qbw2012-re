"""Export a QuickBooks 2012 (SQL Anywhere 11) .qbw to SQLite in one step.

Usage: qbw2012.py COPY.qbw OUT.sqlite

Work on a copy of the company file. Steps: find the fill key K and step
table (fill11.py), repair steps that hide a page anchor, remove the fill,
learn sector-7 rows (exact rows from this file, then the bundled table
data/c7_sa11.json learned from the 2012 sample companies, then rows voted
from this file), export (export11.py) and run the self-checks (check11.py).
Exit status is 1 if a self-check fails.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check11  # noqa: E402
import export11  # noqa: E402
import fill11  # noqa: E402
import sector7  # noqa: E402

BUNDLED_C7 = os.path.join(HERE, "..", "data", "c7_sa11.json")


def load_bundled():
    if not os.path.exists(BUNDLED_C7):
        return {}
    j = json.load(open(BUNDLED_C7))
    return {int(k): v for part in ("voted", "exact") for k, v in j[part].items()}


def main(src, out):
    t0 = time.time()
    raw = open(src, "rb").read()
    if raw[0x2C4:0x2C4 + 28] != b"Sybase Inc., Copyright (c)20":
        sys.exit("not a SQL Anywhere 11 .qbw (no SA11 fingerprint at 0x2C4)")
    k = fill11.find_k(raw)
    steps = fill11.find_steps(raw, k)
    fixed = fill11.repair_steps(raw, k, steps)
    data = fill11.decode(raw, k, steps)
    print(f"fill: K={k}, {len(steps)} step slots, {len(fixed)} repaired ({time.time() - t0:.0f}s)")
    exact, voted, _ = sector7.learn([data])
    c7 = {r7: row for r7, row in voted.items() if None not in row}
    c7.update(load_bundled())
    c7.update(exact)
    print(f"sector 7: {len(exact)} exact and {len(voted)} voted rows from this file, "
          f"{len(c7)} in use ({time.time() - t0:.0f}s)")
    export11.export(data, c7, out)
    print(f"exported in {time.time() - t0:.0f}s; self-checks:")
    return check11.main(out)


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
