#!/usr/bin/env python3
"""gym-spending-battery — arms for examples/apps/gym-spending/report.py.

WHY AN EXAMPLE APP GETS A BATTERY. This file is not only a demo: it is the reference output
for the `ag-gym-spending` app-generation eval (evals/tasks/app-generation.json), i.e. the
shape the OS's build-app path is scored against. A defect here is not one wrong total; it is
the pattern being taught, and then graded as correct.

The two defects below are code-qa run 1 findings, both confirmed:
  report.py:5  the CSV is opened with the PLATFORM DEFAULT ENCODING and the header names are
               matched CASE-SENSITIVELY, so a UTF-8 BOM (every Excel "Save as CSV") or a
               capitalised `Description` makes row.get() return None for every row -- and the
               script then prints `gym spending: 0.00` and exits 0. A wrong answer that looks
               exactly like a right one: the user's real conclusion is "I spend nothing on the
               gym". This is the worst available direction, and it is the common export shape.
  report.py:8  float() is handed the raw cell, so a formatted amount -- `$45.00`, `1,234.50`,
               or a parenthesised debit -- raises an uncaught ValueError and the run dies with
               a traceback and no total.

The fix must therefore do two things that pull in opposite directions: be TOLERANT about the
shapes banks really emit, and be LOUD when it cannot find the columns at all. A script that
only got tolerant would still answer 0.00 for a file it did not understand.
"""
import csv
import io
import os
import subprocess
import sys
import shutil
import tempfile

SIDE_EFFECTS = []  # scratch HOME under mktemp, removed at exit; the script under test is run as a subprocess, nothing leaves the box

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("GYM_REPORT", os.path.join(
    os.path.dirname(HERE), "examples", "apps", "gym-spending", "report.py"))

passed = []
failed = []


def run(csv_text, encoding="utf-8"):
    """Run the real script against a bank.csv fixture. Returns (rc, stdout+stderr)."""
    d = tempfile.mkdtemp()
    docs = os.path.join(d, "Documents")
    os.makedirs(docs)
    with open(os.path.join(docs, "bank.csv"), "w", encoding=encoding, newline="") as fh:
        fh.write(csv_text)
    env = dict(os.environ, HOME=d)
    # LC_ALL=C pins the environment for determinism, NOT because it creates the defect.
    # CORRECTION (2026-11-01, Geist on PR 361): the original header said C locale was "the
    # condition under which the default-encoding defect actually bites". That is false --
    # Python 3.7+ enables UTF-8 mode under the C locale (measured here: LC_ALL=C gives
    # sys.flags.utf8_mode 1, preferredencoding utf-8), so the encoding is utf-8 either way.
    # The BOM arm bites through the HEADER KEY instead: any utf-8 read that is not utf-8-sig
    # leaves '\ufeff' glued to the first field name, so row.get("description") misses. Measured
    # against the pre-fix script under both LC_ALL=C and en_US.UTF-8: 0.00 in both.
    # The arm is sound; the reason I gave for it was not, and a wrong reason is what makes a
    # future reader "fix" the right test.
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env.pop("PYTHONIOENCODING", None)
    try:
        p = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, env=env)
    finally:
        shutil.rmtree(d, ignore_errors=True)       # removed at exit: the declaration above is a promise
    return p.returncode, (p.stdout + p.stderr)


def check(cond, msg):
    (passed if cond else failed).append(msg)
    print(("  ok   " if cond else "  FAIL ") + msg)


PLAIN = "description,amount\nCity Gym monthly,45.00\nCoffee,3.50\nGYM towel,5.00\n"

# --- CONTROL FIRST, so a script that answered nothing could not pass the rest ---------------
rc, out = run(PLAIN)
check(rc == 0 and "50.00" in out, "CONTROL plain lowercase utf-8 headers total 50.00 (got: %r)" % out.strip())

# --- A. the BOM. Excel and most bank exports write one; it lands in the first header key. ----
rc, out = run("﻿" + PLAIN)
check(rc == 0 and "50.00" in out, "A a UTF-8 BOM must not zero the total (got: %r)" % out.strip())
check("0.00" not in out or "50.00" in out, "A the BOM case must not silently print 0.00")

# --- B. capitalised headers, the other half of the same finding. -----------------------------
rc, out = run("Description,Amount\nCity Gym monthly,45.00\nCoffee,3.50\nGYM towel,5.00\n")
check(rc == 0 and "50.00" in out, "B capitalised headers must still match (got: %r)" % out.strip())

# --- C. formatted amounts. These are what a real export contains. ----------------------------
rc, out = run('description,amount\nCity Gym monthly,"$45.00"\nGym annual,"1,005.00"\n')
check(rc == 0, "C a $ and a thousands separator must not abort the run (rc=%d, %r)" % (rc, out.strip()))
check("1050.00" in out, "C formatted amounts parse to 1050.00 (got: %r)" % out.strip())

# --- D. THE LOUD HALF. An unparseable file must NOT read as zero spending. -------------------
# This is the arm that stops the tolerant fixes above from being the whole answer: a script
# that merely lowercased headers would still print 0.00 for a file with no amount column.
rc, out = run("date,merchant,value\n2026-01-01,City Gym,45.00\n")
check(rc != 0, "D a CSV with no description/amount column must FAIL, not print a total (rc=%d)" % rc)
check("0.00" not in out, "D the no-columns case must not print a total at all (got: %r)" % out.strip())
check("amount" in out.lower() or "column" in out.lower(),
      "D the failure must name what was missing (got: %r)" % out.strip())

# --- E. a genuinely empty-but-valid file is zero, and that is NOT an error. ------------------
# E is the permitting twin for D: without it, a script that failed on everything it could not
# total would satisfy D while refusing every honest zero -- and "you spent nothing at the gym
# this month" is a real and correct answer.
rc, out = run("description,amount\nCoffee,3.50\n")
check(rc == 0 and "0.00" in out, "E CONTROL a valid file with no gym rows is 0.00 and rc 0 (got: %r)" % out.strip())

# --- F. a single unparseable amount must not be silently dropped to 0. ----------------------
rc, out = run('description,amount\nCity Gym monthly,"banana"\n')
check(rc != 0 or "banana" in out,
      "F an amount that cannot be read must be named, not counted as 0 (rc=%d, %r)" % (rc, out.strip()))

# --- G. a BLANK gym amount (a pending row / auth hold) must not vanish as 0. ---------------
rc, out = run("description,amount\nCity Gym monthly,\nGym annual,100.00\n")
check(rc != 0 and "100.00" not in out, "G a blank gym amount must fail, not total 100.00 (rc=%d, %r)" % (rc, out.strip()))

# --- H. `amount` and `Amount` both present: ambiguous, not resolved by dict order. ----------
rc, out = run("description,amount,Amount\nCity Gym monthly,45.00,999.00\n")
check(rc != 0 and "999.00" not in out, "H colliding headers must fail, not pick one (rc=%d, %r)" % (rc, out.strip()))

# --- I. the error names the PHYSICAL line, so a multi-line quoted field cannot misdirect. ---
rc, out = run('description,amount\n"City Gym\nmonthly",banana\n')
check("line 3" in out, "I a bad amount after a 2-line quoted field is reported at line 3 (%r)" % out.strip())

print("gym-spending-battery: %s (%d ok, %d failed)" % (
    "PASS" if not failed else "FAIL", len(passed), len(failed)))
sys.exit(1 if failed else 0)
