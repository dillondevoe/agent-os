#!/usr/bin/env python3
# identity-boot-battery.py — task 324 step 1: FIRST-BOOT identity wiring.
#
# tests/identity-battery.py already covers the identity LAYER (keygen, modes, sign/verify,
# preflight control arms). This battery covers the thing that was actually broken, which was not
# the layer: `ensure_boot_identities()` existed, worked, and had ZERO production callers, so no
# box ever minted a participant and $AGENT_OS_AUDIT_SIGNER could only ever fail closed.
#
# THAT IS WHY SECTION C EXISTS AND IS THE POINT OF THIS FILE. A battery that only drove the
# python would have passed at 9540b35 — every function under test was already correct. The defect
# was that nothing CALLED them and nothing agreed on WHERE. So C asserts the wiring itself: the
# module is imported into the build, the boot unit invokes the wrapper, and the minting side and
# the signing side resolve the SAME identity root from ONE constant. A negative result from an
# instrument not wired to the thing under test proves nothing; here the wiring IS the thing.
#
# Run: python3 tests/identity-boot-battery.py
#
# Checks:
#   A. fresh root -> exactly the two spec participants, correct roles, self-test passes
#   B. second run -> identical npubs and UNTOUCHED key files (idempotent, cannot rotate)
#   C. the nix wiring: imported, invoked, and one shared root pin across mint and sign
#   D. modes after a boot mint, and the control arm that they are checkable at all
# MUTATES_SHARED_STATE — Geist's law, 2026-09-05: a box-runnable battery never mutates
# human-shared state. Read as DATA by tests/vm-matrix-contract.py, not as a comment.
MUTATES_SHARED_STATE = False

import os, re, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = 0

def check(name, cond):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond: EX = 1

_tmp = tempfile.mkdtemp(prefix="identity-boot-")
os.environ["AGENT_OS_IDENTITY_ROOT"] = _tmp   # identity.py resolves ROOT at IMPORT time
sys.path.insert(0, os.path.join(ROOT, "modules"))
import identity

KEYS = os.path.join(_tmp, "keys")
PARTS = os.path.join(_tmp, "participants")

# -- A. fresh root --
check("A. fresh root has no keys before the boot call", not os.path.isdir(KEYS))
minted = identity.ensure_boot_identities(owner="dillon", agent="agent")
check("A. mints exactly the two spec participants", sorted(minted) == ["agent", "dillon"])
check("A. both npubs are well-formed NIP-19", all(v.startswith("npub1") for v in minted.values()))
check("A. the two participants are DISTINCT keys",
      minted["agent"] != minted["dillon"])
roles = {}
for n in ("agent", "dillon"):
    txt = open(os.path.join(PARTS, n + ".md")).read()
    m = re.search(r"^role:\s*(\S+)", txt, re.M)
    roles[n] = m.group(1) if m else None
check("A. roles recorded per spec 2.1 (agent=os-agent, dillon=owner-human)",
      roles == {"agent": "os-agent", "dillon": "owner-human"})
# ensure_boot_identities ends in boot_self_test(agent) and RAISES on failure, so reaching here
# is the self-test passing. Asserted explicitly so the guarantee is named, not implied.
check("A. boot self-test passes for the signer participant", identity.boot_self_test("agent") is True)
# The registry legitimately carries `pubkey_hex`, which is 64 hex chars and PUBLIC — so a bare
# "no 64-hex-string" assertion is not this property, it is a false alarm (it fired on the first
# run of this battery and the finding was the battery's, not the code's). The property is that
# the SECRET is absent, so compare against the actual secret.
_secrets = {n: open(os.path.join(KEYS, n + ".key")).read().strip() for n in ("agent", "dillon")}
check("A. the registry entry never contains the SECRET key",
      not any(_secrets[a] in open(os.path.join(PARTS, b + ".md")).read()
              for a in _secrets for b in _secrets))

# -- B. idempotence: a re-run must not rotate anything --
before = {n: os.stat(os.path.join(KEYS, n + ".key")).st_mtime_ns for n in ("agent", "dillon")}
again = identity.ensure_boot_identities(owner="dillon", agent="agent")
after = {n: os.stat(os.path.join(KEYS, n + ".key")).st_mtime_ns for n in ("agent", "dillon")}
check("B. second run returns the SAME npubs", again == minted)
check("B. second run does not rewrite the key files", before == after)
# CONTROL ARM. "Same npubs" is only evidence if it is capable of being different — otherwise the
# check would also pass against a stub that returned a constant. Remove one key and confirm the
# comparison DOES go false, then restore the fresh state for D.
os.remove(os.path.join(KEYS, "agent.key"))
os.remove(os.path.join(PARTS, "agent.md"))
rotated = identity.ensure_boot_identities(owner="dillon", agent="agent")
check("B. control arm: a DESTROYED key really does yield a different npub "
      "(so B's equality check can fail, and its passing means something)",
      rotated["agent"] != minted["agent"] and rotated["dillon"] == minted["dillon"])

# -- C. the wiring — the half that was actually missing --
pkg = open(os.path.join(ROOT, "modules/identity-pkg.nix")).read()
mod = open(os.path.join(ROOT, "modules/identity.nix")).read()
audit_pkg = open(os.path.join(ROOT, "modules/audit-pkg.nix")).read()
flake = open(os.path.join(ROOT, "flake.nix")).read()

def code(src):
    """Comment-stripped view. These files carry long rationale comments that legitimately QUOTE
    the very strings the negative checks below look for (`AGENT_OS_AUDIT_SIGNER=agent` appears in
    prose explaining why it is NOT set here). Matching raw text would make documentation fail the
    battery — and the fix a future author would reach for is deleting the explanation."""
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))

check("C. modules/identity.nix is imported into the built system",
      "./modules/identity.nix" in flake)
# str.index RAISES on a missing needle, which under the control arm (import deleted) turned a
# clean FAIL into a traceback that hid every check after it. A battery must stay legible in the
# state it exists to detect, so this is a comparison, not an exception.
_i_ident, _i_audit = flake.find("./modules/identity.nix"), flake.find("./modules/audit.nix")
check("C. identity.nix is ordered BEFORE audit.nix in the module list",
      _i_ident != -1 and _i_audit != -1 and _i_ident < _i_audit)
check("C. a boot unit actually INVOKES the wrapper (the defect was a missing caller)",
      "agent-os-identity-boot" in mod and "ExecStart" in mod and "oneshot" in mod)
check("C. the wrapper calls ensure_boot_identities with the RULED names",
      "ensure_boot_identities" in pkg and 'owner="dillon"' in pkg and 'agent="agent"' in pkg)
m = re.search(r'root\s*=\s*"([^"]+)"', pkg)
check("C. identity-pkg.nix defines exactly one root constant", m is not None)
check("C. the boot wrapper pins AGENT_OS_IDENTITY_ROOT to it",
      "export AGENT_OS_IDENTITY_ROOT=${root}" in pkg)
check("C. the AUDIT wrapper pins the SAME root, DERIVED from that one constant rather than "
      "retyped (a second literal would be free to drift)",
      "identity-pkg.nix" in audit_pkg
      and "export AGENT_OS_IDENTITY_ROOT=${identityRoot}" in audit_pkg
      and (m.group(1) if m else "\0") not in code(audit_pkg))
check("C. the identity tree is declared 0700 root root, like the ledger beside it",
      (m.group(1) if m else "\0") + " 0700 root root" in mod)
check("C. step 2 is NOT baked into the build (the signer name is a DEPLOY input; "
      "audit-pkg's finding-G coupling rule says both env vars are set together, by the operator)",
      "AGENT_OS_AUDIT_SIGNER=" not in code(mod) and "AGENT_OS_AUDIT_SIGNER=" not in code(pkg))

# -- D. modes after a boot mint --
check("D. key dir is 0700", (os.stat(KEYS).st_mode & 0o777) == 0o700)
check("D. every key file is 0600",
      all((os.stat(os.path.join(KEYS, f)).st_mode & 0o777) == 0o600
          for f in os.listdir(KEYS) if f.endswith(".key")))
check("D. preflight passes on the tree the boot unit produced", identity.preflight() is None)
# CONTROL ARM for D: the mode assertions above are only meaningful if a wrong mode is caught.
os.chmod(os.path.join(KEYS, "agent.key"), 0o644)
try:
    identity.preflight(); caught = False
except identity.IdentityError:
    caught = True
finally:
    os.chmod(os.path.join(KEYS, "agent.key"), 0o600)
check("D. control arm: a loosened key file IS caught by preflight", caught)

print("identity-boot-battery: " + ("PASS" if EX == 0 else "FAIL"))
sys.exit(EX)
