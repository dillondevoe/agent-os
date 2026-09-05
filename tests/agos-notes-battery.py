#!/usr/bin/env python3
# agos-notes-battery.py — Phase 2 notes (agos-notes) acceptance harness.
# Verifies list/new/read/append over the plain-md store. agos-notes hardcodes
# /var/lib/agos-notes; on a host without a writable store the write path degrades
# gracefully (ok:false) — we assert the JSON contract either way, and SKIP the
# write-path detail if the store isn't writable (Dell gate owns the real store).
# Run: PYTHONPATH=modules python3 tests/agos-notes-battery.py
# SIDE_EFFECTS — Geist's law as amended 2026-09-05T13:05Z: a box-runnable battery declares
# every effect that leaves the machine or outlives the run. Read as DATA by
# tests/vm-matrix-contract.py, not as a comment.
SIDE_EFFECTS = []

import subprocess, json, shutil, sys

EX = 0
def check(name, cond, detail=""):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))
    if not cond: EX = 1

def run(cmd):
    try:
        o = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return o.returncode, o.stdout.strip(), o.stderr.strip()
    except FileNotFoundError:
        return 127, "", "not found: " + " ".join(cmd)
    except Exception as e:
        return 1, "", str(e)

ncli = shutil.which("agos-notes")
if not ncli:
    print("  SKIP agos-notes-battery: agos-notes not on PATH (image not built).")
    sys.exit(0)
print("  using REAL agos-notes CLI: " + ncli)

# list -> valid JSON array (empty store is fine)
rc, out, err = run([ncli, "list"])
try:
    arr = json.loads(out)
    check("list -> JSON array", isinstance(arr, list), out[:60])
except Exception as e:
    check("list parses", False, str(e))

# The OTHER half of the product's hand, covered without mutating anything. `read` on a slug
# that does not exist returns {ok:false,error:"no such note",slug} and creates NOTHING
# (notes-open.nix cmd_read: it stats the path and returns before any write). So the second of
# the brain's two notes verbs keeps real coverage here — the write arms were removed because
# they mutated, not because coverage was expendable.
MISSING = "battery-slug-that-does-not-exist"
rc, out, err = run([ncli, "read", MISSING])
try:
    d = json.loads(out)
    check("read <missing> -> ok:false + no-such-note", d.get("ok") is False and "no such note" in str(d.get("error","")), out[:80])
    check("read <missing> -> echoes the slug", d.get("slug") == MISSING, str(d.get("slug")))
except Exception as e:
    check("read parses", False, str(e) + " | out=" + out[:60])

# THE WRITE ARMS ARE GONE, AND THEY WERE NEVER COVERING THE PRODUCT.
# This battery used to run `agos-notes new` and `agos-notes append` against the hard-coded
# /var/lib/agos-notes — the store notes-open.nix documents as SHARED WITH THE HUMAN — with no
# delete verb in the CLI to undo it. Every run left a note behind, permanently, in the
# operator's real notes. Found in the absent-binary sweep (Mirror, 2026-09-05); the store was
# still empty on the Dell, so it was armed but had not yet fired.
#
# Geist ruled (2026-09-05, P2 ACTION): remove the arms, and NO env override on the store — a
# caller-supplied store path on a shipped module is the 08-14 fs-confinement class and Phase S
# would pay for it forever. The arms cost nothing to lose: the brain's `notes` hand is
# list|read ONLY (agent-brain.py:1306-1310, which answers "use list|read" to anything else),
# so `new`/`append` were testing verbs the product does not advertise to the agent. Verified
# by reading that dispatch, not by taking the ruling's word for it.
#
# Mutating arms belong in the VM arm of tests/verb-battery.nix, where the store is disposable.
# That is now law rather than preference, and it is enforced as DATA below, not as this comment.

print("agos-notes-battery: " + ("ALL PASS" if EX == 0 else "FAILURES"))
sys.exit(EX)
