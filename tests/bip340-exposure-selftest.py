#!/usr/bin/env python3
"""Control battery for tests/bip340-exposure-contract.py — the condition-3 tripwire.

WHY A BATTERY FOR A CHECK. The checked-in state of this repo is GREEN for the tripwire: the
only importers of `modules/bip340.py` today are `modules/identity.py` and `bin/audit`, and
neither touches the network. A check whose only observed behaviour is "passes on a clean tree"
is indistinguishable from a check that passes on everything, and the second one is worthless on
the single day it matters. Every arm below therefore names the world it is exercising and
whether that world must be RED or GREEN.

Arms A2, A5 and A6 are CONTROL arms and are load-bearing:
  * without A2 (healthy tree is GREEN) a checker that fails unconditionally passes A1;
  * without A5 (`urllib.parse` is NOT network I/O) a checker that flags any `urllib` string
    passes A1 and A3 while being red on a tree that is fine — noise, and noise gets uninstalled;
  * without A6 the importlib arm (A3) proves nothing: A6 runs the NAIVE static-only extractor
    against A3's input and asserts it MISSES the edge, so A3 is shown catching something a
    plausible implementation does not.
A4 is the vacuity arm: a checker handed no sources at all must FAIL, not pass. That is this
repo's own recurring defect (docs/cancelled-boundaries.md members 3, 8, 10) and the reason
`bip340-battery.py` sits in KNOWN_UNWIRED_DEBT rather than being assumed to run.

Run: python3 tests/bip340-exposure-selftest.py
"""
import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import importlib.util

# The repo names contract files with hyphens (vm-matrix-contract.py, agos-cycle-contract.py),
# which are not importable identifiers. Load by path rather than renaming against convention.
_spec = importlib.util.spec_from_file_location(
    "bip340_exposure_contract",
    pathlib.Path(__file__).resolve().parent / "bip340-exposure-contract.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

FAILURES = []
ARMS = 0
CONTROLS = 0


def arm(name, world, expect_red, got_red, detail="", rep=None, reason=None):
    """reason: a substring that MUST appear in the report when the arm is red. An arm that
    asserts only `rc != 0` passes on an implementation that reports every reacher as ALLOWLIST
    DRIFT and never emits EXPOSURE at all — the binding condition would go unreported in CI
    under its own name while the battery stayed green. A7 always checked its reason; the
    exposure arms did not, which is the same defect this file exists to catch."""
    global ARMS, CONTROLS
    ARMS += 1
    if "control arm" in detail or "pre-fix arm" in detail:
        CONTROLS += 1
    ok = (expect_red == got_red)
    verdict = "RED" if got_red else "GREEN"
    want = "RED" if expect_red else "GREEN"
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {world} -> {verdict} (want {want}) {detail}")
    if not ok:
        FAILURES.append(name)
        return
    if got_red and reason is not None:
        if not any(reason.lower() in l.lower() for l in (rep or ())):
            FAILURES.append(name + "-reason")
            print(f"  FAIL {name}-reason: red, but for the wrong reason — no {reason!r} in report")


def run(sources, allowlist=None):
    """sources: {modname: source text}. Returns (rc, report_lines)."""
    return mod.evaluate(sources, allowlist=allowlist if allowlist is not None else mod.ALLOWLIST)


HEALTHY = {
    "bip340": "def schnorr_sign(): pass\n",
    "bech32": "def encode(): pass\n",
    "identity": "import bip340, bech32\n",
    "audit": 'import importlib\nimportlib.import_module("identity")\nimportlib.import_module("bip340")\n',
}

print("bip340-exposure-selftest")

# A1 — the incident shape: a socket-using module imports bip340 directly.
s = dict(HEALTHY); s["relay"] = "import socket\nimport bip340\n"
rc, rep = run(s, allowlist=mod.ALLOWLIST | {"relay"})
arm("A1", "network module imports bip340 (allowlisted, so only the network rule can catch it)",
    True, rc != 0, rep=rep, reason="EXPOSURE")

# A2 — CONTROL: the real shape of the tree today must be GREEN.
rc, rep = run(HEALTHY)
arm("A2", "healthy tree (identity + audit only)", False, rc != 0, "<- control arm")

# A3 — the edge a static grep cannot see: importlib, plus socket.
s = dict(HEALTHY); s["relay"] = 'import socket, importlib\nimportlib.import_module("bip340")\n'
rc, rep = run(s, allowlist=mod.ALLOWLIST | {"relay"})
arm("A3", "network module reaches bip340 via importlib.import_module", True, rc != 0,
    rep=rep, reason="EXPOSURE")

# A4 — vacuity: no sources at all must FAIL, never pass quietly.
rc, rep = run({})
arm("A4", "empty source set (instrument found nothing)", True, rc != 0, "<- vacuity arm")

# A5 — CONTROL: urllib.parse is string surgery, not network I/O.
s = dict(HEALTHY); s["identity"] = "from urllib.parse import urlsplit\nimport bip340, bech32\n"
rc, rep = run(s)
arm("A5", "importer uses urllib.parse only", False, rc != 0, "<- control arm")

# A6 — PRE-FIX arm: the naive static-only extractor must MISS A3's edge.
s = dict(HEALTHY); s["relay"] = 'import socket, importlib\nimportlib.import_module("bip340")\n'
rc_naive, _ = mod.evaluate(s, allowlist=mod.ALLOWLIST | {"relay"}, _imports_of=mod.imports_of_static_only)
arm("A6", "SAME input as A3, naive static-only extractor", False, rc_naive != 0,
    "<- pre-fix arm: proves A3 catches what a plausible impl misses")

# A7 — a new NON-network importer is drift, not exposure, and must say so.
s = dict(HEALTHY); s["notes"] = "import bip340\n"
rc, rep = run(s)
arm("A7", "new non-network importer (allowlist drift)", True, rc != 0)
if rc != 0 and not any("allowlist" in l.lower() for l in rep):
    FAILURES.append("A7-reason")
    print("  FAIL A7-reason: red for the wrong reason — no allowlist line in the report")

# A8 — transitive: network module imports identity, which imports bip340.
s = dict(HEALTHY); s["relay"] = "import socket\nimport identity\n"
rc, rep = run(s, allowlist=mod.ALLOWLIST | {"relay"})
arm("A8", "network module reaches bip340 transitively via identity", True, rc != 0,
    rep=rep, reason="EXPOSURE")

# A9 — REVIEW FINDING (2026-08-27, /code-review medium): network reached through ONE LOCAL HOP.
# The first version filtered `exposed` on the reacher's OWN import list, so a wrapper hid the
# network entirely and the tool reported only ALLOWLIST DRIFT — whose documented remedy is "add
# it to the allowlist", which would have made a genuine condition-3 violation permanently GREEN.
# Not hypothetical: modules/agos_events.py imports socket and is already imported by four
# modules.
s = dict(HEALTHY)
s["netutil"] = "import socket\n"
s["relay"] = "import netutil, bip340\n"
rc, rep = run(s, allowlist=mod.ALLOWLIST | {"relay", "netutil"})
arm("A9", "reacher reaches the network through a local wrapper (one hop)", True, rc != 0,
    rep=rep, reason="EXPOSURE")

# A10 — PRE-FIX arm for A9. The direct-only network test against A9's input must MISS.
rc_direct, _ = mod.evaluate(s, allowlist=mod.ALLOWLIST | {"relay", "netutil"},
                            _net_direct_only=True)
arm("A10", "SAME input as A9, direct-only network test", False, rc_direct != 0,
    "<- pre-fix arm: proves A9 catches what the shipped first version missed")

# A11 — REVIEW FINDING: `from http import client` names the network module in the ALIAS, not in
# node.module, so recording only node.module yields {"http"} — and `http` is deliberately not a
# NETWORK_ROOT (http.client is, urllib.parse is not). Ordinary, non-adversarial Python.
for stmt in ("from http import client", "from urllib import request"):
    s = dict(HEALTHY); s["relay"] = f"{stmt}\nimport bip340\n"
    rc, rep = run(s, allowlist=mod.ALLOWLIST | {"relay"})
    arm(f"A11[{stmt}]", "network module named in the from-import ALIAS", True, rc != 0,
        rep=rep, reason="EXPOSURE")

# A12 — CONTROL for A11: `from urllib import parse` is still string surgery and must stay GREEN.
s = dict(HEALTHY); s["identity"] = "from urllib import parse\nimport bip340, bech32\n"
rc, rep = run(s)
arm("A12", "from urllib import parse (the alias form of A5)", False, rc != 0, "<- control arm")

# A13 — discover() must not silently drop a module when modules/X.py and bin/X collide.
arm("A13", "duplicate module name across modules/ and bin/", True,
    mod.duplicate_names({"modules": ["audit.py"], "bin": ["audit"]}) != [], "<- latent-collision arm")

print()
if FAILURES:
    print(f"SELFTEST FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
# Counted, not asserted: a hardcoded arm count keeps claiming N after the N+1th is added —
# a summary line making a claim nothing verifies (cf. commit 5323203).
print(f"selftest ok — {ARMS} arms, {CONTROLS} of them controls/pre-fix")
