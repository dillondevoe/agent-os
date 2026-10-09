#!/usr/bin/env python3
"""gym-spending: sum the gym lines in one bank CSV. Reads ONE file read-only, no network."""
import csv, os
total = 0.0
with open(os.path.expanduser("~/Documents/bank.csv"), newline="") as fh:
    for row in csv.DictReader(fh):
        if "gym" in (row.get("description") or "").lower():
            total += float(row.get("amount") or 0)
print("gym spending: %.2f" % total)
