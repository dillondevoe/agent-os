#!/usr/bin/env python3
# tests/agos-propose-contract.py — contract battery for modules/agos_propose.py
# (self-improvement loop, phase PROPOSE).
#
# The module sells three guarantees, and each has a control arm, because every one of them
# is trivially satisfiable by a module that does NOTHING AT ALL:
#
#   1. A PROPOSAL IS A DOCUMENT, NEVER AN ACTION.
#      Control: case D emits a real proposal and asserts it EXISTS. "Nothing was applied"
#      is what a broken drafter reports too.
#   2. THE DENY LIST REFUSES, AND THE REFUSAL IS VISIBLE.
#      Control: case B feeds a FORBIDDEN target and watches it go red — Augur's explicit
#      insistence (2026-08-19): "a green security leg needs the run where it goes red;
#      feed it a forbidden-path proposal and watch it refuse, or the filter is untested
#      fiction." B2 then feeds an ALLOWED target and asserts it is NOT refused, so we know
#      the filter discriminates rather than blanket-denying.
#   3. REJECTED IS TERMINAL, KEYED ON CONTENT.
#      Control: case C rejects, re-proposes identically (must stay REJECTED), and then C3
#      re-proposes with the SAME content under a cosmetically different route to prove the
#      id did not move — attrition is the failure mode, not repetition.
#
# (docs/cancelled-boundaries.md: control-arm the instrument, not just the guard.)

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agos_propose as P

FAILURES = []


def check(label, cond):
    print("%s — %s" % (label, "PASS" if cond else "FAIL"))
    if not cond:
        FAILURES.append(label)


def store():
    return P.ProposalStore(os.path.join(tempfile.mkdtemp(), "lessons.db"))


def lesson(typ="TOOL_FAILURE", sig="sig-1", detail="boom", n=2):
    return {"sig_id": sig, "type": typ, "detail": detail, "occurrences": n}


# ── A. STRUCTURAL: the module cannot act, and there is no APPLY here ───────────────
SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "agos_propose.py"), encoding="utf-8").read()
CODE = "\n".join(l for l in SRC.splitlines() if not l.strip().startswith("#"))

for mod in ("subprocess", "shutil", "urllib", "socket", "requests"):
    check("A. does not import %s (a document cannot act)" % mod,
          not re.search(r"^\s*(import|from)\s+%s\b" % mod, CODE, re.M))
check("A. defines no apply entry point",
      not re.search(r"^\s*def\s+(apply|merge|commit|push)\b", CODE, re.M))
check("A. opens no file for writing",
      not re.search(r"open\([^)]*['\"][wa]", CODE))
check("A. render() returns text rather than writing it",
      isinstance(P.render([]), str))

# ── B. THE DENY LIST GOES RED ON A FORBIDDEN PATH (the armed control) ──────────────
s = store()
forbidden = P.Proposal("sig-x", "modules/agos_propose.py", "loosen the deny list")
check("B. the gate definition itself is denied", P.denied(forbidden.target) is not None)
check("B. so is a secret", P.denied("home/.ssh/id_ed25519") is not None)
check("B. so is the genesis-lock contract", P.denied("modules/genesis_lock.nix") is not None)
check("B. so is the PS<->OS antenna layer", P.denied("modules/antennae.py") is not None)
check("B. traversal does not walk around it",
      P.denied("modules/../MODULES/AGOS_PROPOSE.PY") is not None)
check("B. and the refusal names WHICH rule fired, not just 'no'",
      P.denied(forbidden.target) == "agos_propose.py")

# B2 — CONTROL ARM: an allowed target must NOT be refused, or the filter is a blanket deny.
check("B2. CONTROL: an ordinary docs target is allowed",
      P.denied("docs/lessons.md") is None)

# ── C. REJECTED IS TERMINAL AND CONTENT-KEYED ─────────────────────────────────────
s = store()
r1 = P.propose(s, [lesson()])
check("C. a fresh lesson is emitted", r1["emitted"] == 1 and not r1["refused"])
pid = s.by_status(P.EMITTED)[0]["prop_id"]
check("C. rejecting it takes effect", s.reject(pid) is True)
check("C. it leaves EMITTED", s.by_status(P.EMITTED) == [])
r2 = P.propose(s, [lesson()])
check("C. re-proposing the SAME content does NOT resurrect it",
      r2["emitted"] == 0 and r2["suppressed"] == 1)
check("C. it is still REJECTED after the re-run", s.status(pid) == P.REJECTED)
check("C. and REJECTED is terminal — a second reject is a no-op",
      s.reject(pid) is False)
# C3 — the id is content-derived, so cosmetic route changes cannot mint a new one.
check("C3. content hash ignores path spelling, so rewording the route cannot resurrect",
      P.content_hash("s", "docs/x.md", "b") == P.content_hash("s", "./docs//x.md", "b "))
check("C3. but genuinely different content IS a different proposal",
      P.content_hash("s", "docs/x.md", "b") != P.content_hash("s", "docs/x.md", "different"))

# ── D. CONTROL: the drafter actually drafts (else every check above is vacuous) ────
s = store()
rD = P.propose(s, [lesson(sig="d1"), lesson("COST_SPIKE", "d2"), lesson("STALLED_WORK", "d3")])
check("D. CONTROL: all three known lesson types draft a proposal", rD["drafted"] == 3)
check("D. CONTROL: and they are emitted, not silently zero", rD["emitted"] == 3)
check("D. CONTROL: rendered output contains the proposals",
      "proposal" in P.render(s.by_status(P.EMITTED)))
check("D. an UNKNOWN lesson type is counted as undraftable, not dropped silently",
      P.propose(s, [lesson("SOMETHING_NEW", "d4")])["undraftable"] == 1)

# ── E. THE DENY LIST FIRES THROUGH THE REAL propose() PATH, AND RECORDS ───────────
# Not via denied() in isolation: this drives a FORBIDDEN target through propose() itself,
# which is the only version of this check that proves the wiring rather than the predicate.
# (With the shipped _DRAFTS table every target is docs/lessons.md, so the deny list is
# unreachable from propose() today — hence the injectable table. Testing only denied()
# would have let a disconnected filter pass as a green security leg.)
s = store()
BAD_DRAFTS = {"TOOL_FAILURE": ("modules/agos_propose.py", "loosen the deny list")}
rE = P.propose(s, [lesson(sig="e1")], drafts=BAD_DRAFTS)
check("E. propose() REFUSES a forbidden target end-to-end", len(rE["refused"]) == 1)
check("E. it emits nothing", rE["emitted"] == 0)
check("E. the report names the rule that fired",
      rE["refused"][0]["token"] == "agos_propose.py")
refused = s.by_status(P.REFUSED)
check("E. the refused proposal persists for audit", len(refused) == 1)
check("E. with the reason attached", "deny-path" in (refused[0]["reason"] or ""))
check("E. and it is NOT in the emitted set", s.by_status(P.EMITTED) == [])

# E2 — CONTROL ARM: the same path with an ALLOWED target must go GREEN, or "refused"
# is just what this module does to everything.
s = store()
OK_DRAFTS = {"TOOL_FAILURE": ("docs/lessons.md", "add a guardrail")}
rE2 = P.propose(s, [lesson(sig="e2")], drafts=OK_DRAFTS)
check("E2. CONTROL: an allowed target passes the same path and emits",
      rE2["emitted"] == 1 and not rE2["refused"])

# ── F. IDEMPOTENCE: a cadence re-run does not multiply proposals ──────────────────
s = store()
P.propose(s, [lesson(sig="f1")])
again = P.propose(s, [lesson(sig="f1")])
check("F. re-proposing an unchanged lesson emits nothing new",
      again["emitted"] == 0 and again["suppressed"] == 1)
check("F. and there is still exactly one proposal on file",
      len(s.by_status(P.EMITTED)) == 1)

print("\n%d failure(s)" % len(FAILURES))
for f in FAILURES:
    print("  FAILED: %s" % f)
sys.exit(1 if FAILURES else 0)
