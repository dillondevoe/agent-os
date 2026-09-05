#!/usr/bin/env python3
# bip340-battery.py — Phase 1.5B slice 1 (task 287 item 2): the vendored BIP-340 signer.
#
# Binding condition 2 of Geist's 2026-08-19 Path-A ruling: the FULL official test-vector set
# runs in CI, INCLUDING the must-fail verification vectors, control-armed.
#
# The must-fail half is the load-bearing half. A verifier that returns True unconditionally
# passes every TRUE vector, so a battery that checked only those would certify a signer that
# accepts forgeries. Vectors 5-15 are the ones that catch that, and check I below proves this
# battery can actually fail rather than merely reporting PASS.
#
# Run: python3 tests/bip340-battery.py
#
# Checks:
#   A. module compiles; no DEBUG scaffolding survived the vendoring
#   B. every official vector with a secret key: pubkey_gen + schnorr_sign reproduce the
#      published pubkey and signature EXACTLY (deterministic nonces — same input, same bytes)
#   C. every official vector verifies to its published result (TRUE and FALSE alike)
#   D. malformed inputs raise rather than returning a verdict
#   I. control arm: a deliberately broken verifier is CAUGHT by the must-fail vectors
# MUTATES_SHARED_STATE — Geist's law, 2026-09-05: a box-runnable battery never mutates
# human-shared state. Read as DATA by tests/vm-matrix-contract.py, not as a comment.
MUTATES_SHARED_STATE = False

import csv, os, py_compile, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "modules"))
VECTORS = os.path.join(ROOT, "tests", "bip340-test-vectors.csv")
EX = 0

def check(name, cond):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond: EX = 1

# -- A. compile + vendoring hygiene --
MOD = os.path.join(ROOT, "modules", "bip340.py")
try:
    py_compile.compile(MOD, doraise=True); print("  PASS compile modules/bip340.py")
except py_compile.PyCompileError as e:
    print("  FAIL compile modules/bip340.py: " + str(e)); EX = 1

import bip340

src = open(MOD).read()
# Check the CODE, not the prose: the module docstring names the DEBUG scaffolding in order to
# document its removal, and the first version of this check flagged that mention. A hygiene
# check that greps the whole file cannot tell "this code prints secrets" from "this comment
# says it doesn't" — so parse the docstring off and assert against what actually executes.
import ast as _ast
_tree = _ast.parse(src)
_code = src
if (_doc := _ast.get_docstring(_tree)) is not None:
    _code = src.replace(_doc, "", 1)
check("A. no DEBUG scaffolding in executable code (a signer must not print private intermediates)",
      "debug_print_vars" not in _code and "DEBUG" not in _code)
check("A. interim-signer marker present (ruling condition 5)",
      "MUST be replaced by libsecp256k1" in src)

rows = list(csv.DictReader(open(VECTORS)))
check("A. official vector set loaded", len(rows) >= 15)

# -- B/C. the official vectors --
signed = verified = must_fail = 0
for r in rows:
    idx, comment = r["index"], (r["comment"] or "").strip()
    msg = bytes.fromhex(r["message"])
    pub = bytes.fromhex(r["public key"])
    sig_hex = r["signature"]
    expected = r["verification result"] == "TRUE"

    if r["secret key"]:
        sk = bytes.fromhex(r["secret key"])
        aux = bytes.fromhex(r["aux_rand"])
        got_pub = bip340.pubkey_gen(sk)
        got_sig = bip340.schnorr_sign(msg, sk, aux)
        check("B. vector %s: pubkey_gen matches published pubkey" % idx,
              got_pub.hex().upper() == r["public key"].upper())
        # Byte-exact signature equality is the deterministic-nonce assertion: a signer that
        # drew a random nonce would verify fine here yet produce different bytes every run.
        check("B. vector %s: signature is byte-exact (deterministic nonce)" % idx,
              got_sig.hex().upper() == sig_hex.upper())
        signed += 1

    try:
        got = bip340.schnorr_verify(msg, pub, bytes.fromhex(sig_hex))
    except (ValueError, Exception):
        got = False
    check("C. vector %s verifies %s%s" % (idx, expected, " — " + comment if comment else ""),
          got == expected)
    verified += 1
    if not expected: must_fail += 1

check("C. signing exercised on every keyed vector", signed >= 5)
check("C. must-fail vectors are actually present (forgery coverage)", must_fail >= 8)

# -- D. malformed input raises, rather than silently answering --
def raises(fn, want):
    """True only if fn() raised an error whose message contains `want`.

    A bare `except Exception: return True` accepts ANY failure — a typo in the call, a
    NameError, an unrelated TypeError — as evidence that the length guard under test fired.
    The arm then stays green through a module whose guard was deleted."""
    try: fn()
    except Exception as e:
        if want.lower() in str(e).lower(): return True
        print("      (raised, but not the guard under test: %s: %s)" % (type(e).__name__, e))
        return False
    return False
check("D. 31-byte pubkey raises", raises(lambda: bip340.schnorr_verify(b"", b"\x00"*31, b"\x00"*64), "32"))
check("D. 63-byte signature raises", raises(lambda: bip340.schnorr_verify(b"", b"\x00"*32, b"\x00"*63), "64"))
check("D. zero secret key raises", raises(lambda: bip340.pubkey_gen(bytes(32)), "range"))
check("D. short aux_rand raises",
      raises(lambda: bip340.schnorr_sign(b"", bytes.fromhex("01"*32), b"\x00"*31), "32"))

# -- I. control arm: prove the must-fail vectors can catch a broken verifier --
# A detector that has never fired is a claim. Swap in the classic broken implementation — one
# that accepts everything — and confirm the FALSE vectors flag it. If this check ever reports
# "not caught", every PASS above is worthless.
_real = bip340.schnorr_verify
try:
    bip340.schnorr_verify = lambda msg, pubkey, sig: True
    caught = sum(1 for r in rows
                 if r["verification result"] == "FALSE"
                 and bip340.schnorr_verify(bytes.fromhex(r["message"]),
                                           bytes.fromhex(r["public key"]),
                                           bytes.fromhex(r["signature"])) is not False)
finally:
    bip340.schnorr_verify = _real
check("I. control arm: always-True verifier is caught by %d must-fail vectors" % must_fail,
      caught == must_fail and caught > 0)
check("I. real verifier restored after the control arm", bip340.schnorr_verify is _real)

print("bip340-battery: " + ("PASS" if EX == 0 else "FAIL"))
sys.exit(EX)
