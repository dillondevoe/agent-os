#!/usr/bin/env python3
"""gym-spending: sum the gym lines in one bank CSV. Reads ONE file read-only, no network.

Kept short on purpose: it is a worked example of an Agent OS app, so its shape gets copied.
Two things in it are not incidental:

  utf-8-sig and a case-folded header map. Real exports carry a UTF-8 BOM (Excel writes one)
  and capitalise their headers. With a plain open() and exact keys, every row misses and the
  script prints `0.00` and exits 0 -- a wrong answer shaped exactly like a right one, whose
  reading is "I spend nothing on the gym". A read that cannot find its columns must never be
  reported as a total of zero.

  A missing column is an ERROR, an empty match is a ZERO. Being tolerant about input shapes
  is only half the job; the other half is staying loud when the file is not the file we think.
"""
import csv
import os
import sys

WANT = ("description", "amount")


def cell(row, name):
    return (row.get(name) or "").strip()


def money(text):
    """'$1,005.00' -> 1005.0, '(45.00)' -> -45.0. Raises ValueError, never guesses zero."""
    t = text.replace(",", "").replace("$", "").strip()
    if t.startswith("(") and t.endswith(")"):      # accounting-style debit
        t = "-" + t[1:-1]
    return float(t)                                # blank raises too: a pending row is not 0


# utf-8-sig strips the BOM if one is there and is a plain utf-8 read if it is not.
with open(os.path.expanduser("~/Documents/bank.csv"), newline="", encoding="utf-8-sig") as fh:
    reader = csv.DictReader(fh)
    headers = {}
    for h in reader.fieldnames or []:
        k = (h or "").strip().lower()
        if k in headers:                           # `amount` AND `Amount`: ambiguous, say so
            sys.exit("bank.csv: two columns read as %r (%r, %r)" % (k, headers[k], h))
        headers[k] = h
    missing = [w for w in WANT if w not in headers]
    if missing:
        sys.exit("bank.csv: no %s column (found: %s)"
                 % (", ".join(missing), ", ".join(reader.fieldnames or []) or "no header row"))
    total = 0.0
    for row in reader:
        if "gym" not in cell(row, headers["description"]).lower():
            continue
        raw = cell(row, headers["amount"])
        try:
            total += money(raw)
        except ValueError:
            sys.exit("bank.csv line %d: cannot read amount %r" % (reader.line_num, raw))

print("gym spending: %.2f" % total)
