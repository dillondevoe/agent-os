#!/usr/bin/env python3
# identity-battery.py — Phase 1.5B slice 2 (task 287 item 2): keypairs, registry, boot self-test.
#
# Pins Geist's 2026-08-19 condition-(b) ruling. The load-bearing checks are the PERMISSION ones
# and the two MARKER assertions: B2 is only honest if the fail-loud preflight actually fails, and
# the two known gaps (no libsecp256k1, no at-rest encryption) are only "held" if something
# mechanical refuses to let their markers disappear.
#
# Run: python3 tests/identity-battery.py
#
# Checks:
#   A. modules compile; both interim markers present and asserted
#   B. bech32/NIP-19: official npub vector, roundtrip, bech32m rejection, malformed rejection
#   C. mint: key 0600, dir 0700, registry legible, secret NEVER in the registry, idempotent
#   D. sign/verify as a participant; two participants verify only against their OWN npub
#   E. boot self-test passes, and FAILS LOUD when the signer is broken (control arm)
#   F. preflight control arm: loosened perms are actually CAUGHT, on both file and dir
import os, py_compile, stat, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "modules"))
EX = 0

def check(name, cond):
    global EX
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond: EX = 1

def raises(fn, exc=Exception):
    try: fn(); return False
    except exc: return True

# -- A. compile + markers --
for f in ("modules/identity.py", "modules/bech32.py"):
    try:
        py_compile.compile(os.path.join(ROOT, f), doraise=True); print("  PASS compile " + f)
    except py_compile.PyCompileError as e:
        print("  FAIL compile " + f + ": " + str(e)); EX = 1

_tmp = tempfile.mkdtemp()
os.environ["AGENT_OS_IDENTITY_ROOT"] = _tmp
import identity, bech32, bip340

id_src = open(os.path.join(ROOT, "modules", "identity.py")).read()
# Normalize whitespace before matching: the marker is prose in a docstring and WILL be re-wrapped
# by the next person who edits the paragraph. Matching the raw text made this assertion fail on a
# line break — i.e. it was testing the line width, not the presence of the marker.
id_flat = " ".join(id_src.split())
check("A. at-rest interim marker present (ruling condition b3)",
      "at-rest encryption pending" in id_flat and "MUST NOT guard real value" in id_flat)
check("A. the false phrase is NOT claimed as a property of this module",
      'It is NOT "encrypted at rest"' in id_flat)
check("A. bech32 module cites its exact upstream source URL (Geist AST-diffs it)",
      "github.com/sipa/bech32" in open(os.path.join(ROOT, "modules", "bech32.py")).read())

# -- B. NIP-19 --
# Official NIP-19 vector: this exact pubkey encodes to this exact npub. A codec that silently
# used bech32m instead of bech32 still produces a plausible string — only a pinned vector catches it.
VEC_HEX = "3bf0c63fcb93463407af97a5e5ee64fa883d107ef9e558472c4eb9aaaefa459d"
VEC_NPUB = "npub180cvv07tjdrrgpa0j7j7tmnyl2yr6yr7l8j4s3evf6u64th6gkwsyjh6w6"
check("B. official NIP-19 vector encodes exactly",
      bech32.npub_encode(bytes.fromhex(VEC_HEX)) == VEC_NPUB)
check("B. official NIP-19 vector decodes exactly",
      bech32.npub_decode(VEC_NPUB).hex() == VEC_HEX)
check("B. bech32m string is REJECTED (silent-mismatch class, not a crash class)",
      raises(lambda: bech32.npub_decode(
          bech32.bech32_encode("npub", bech32.convertbits(bytes.fromhex(VEC_HEX), 8, 5),
                               bech32.Encoding.BECH32M))))
check("B. corrupted checksum rejected", raises(lambda: bech32.npub_decode(VEC_NPUB[:-1] + "q")))
check("B. wrong hrp rejected", raises(lambda: bech32.npub_decode(
      bech32.bech32_encode("nsec", bech32.convertbits(bytes.fromhex(VEC_HEX), 8, 5),
                           bech32.Encoding.BECH32))))
check("B. 31-byte pubkey rejected", raises(lambda: bech32.npub_encode(b"\x01" * 31)))

# -- C. mint --
npub = identity.mint("alice", "owner-human")
kp = os.path.join(_tmp, "keys", "alice.key")
reg = os.path.join(_tmp, "participants", "alice.md")
check("C. key file is 0600", stat.S_IMODE(os.stat(kp).st_mode) == 0o600)
check("C. key dir is 0700", stat.S_IMODE(os.stat(os.path.join(_tmp, "keys")).st_mode) == 0o700)
check("C. registry entry written", os.path.exists(reg))
reg_txt = open(reg).read()
secret = open(kp).read().strip()
# The whole point of splitting key dir from registry: the registry is legible, the secret is not
# in it. A single accidental f-string would put it there and nothing else would notice.
check("C. SECRET NEVER appears in the registry entry", secret not in reg_txt)
check("C. registry carries npub + role + created_at",
      npub in reg_txt and "role: owner-human" in reg_txt and "created_at:" in reg_txt)
check("C. mint is idempotent — an existing key is never replaced",
      identity.mint("alice", "owner-human") == npub and open(kp).read().strip() == secret)

# -- D. sign / verify --
msg = bip340.tagged_hash("AgentOS/test", b"\x02" * 32)
sig = identity.sign_as("alice", msg)
check("D. alice's signature verifies against alice's npub", identity.verify(npub, msg, sig))
bob = identity.mint("bob", "os-agent")
check("D. alice's signature does NOT verify against bob's npub", not identity.verify(bob, msg, sig))
check("D. two participants' actions verify against their OWN keys (spec acceptance 2)",
      identity.verify(bob, msg, identity.sign_as("bob", msg)))
check("D. a tampered message fails verification",
      not identity.verify(npub, bip340.tagged_hash("AgentOS/test", b"\x03" * 32), sig))
check("D. signing a non-32-byte message raises", raises(lambda: identity.sign_as("alice", b"short")))
check("D. unknown participant raises", raises(lambda: identity.pubkey_of("nobody")))

# -- E. boot self-test, control-armed --
check("E. boot self-test passes on a healthy signer", identity.boot_self_test("alice"))
# A signer that produces unverifiable signatures is the failure this test exists for. If the
# self-test cannot detect it, the self-test is decoration. Arm it at the layer the self-test
# actually renders its verdict on — identity.verify.
#
# Arming it one layer lower (stubbing bip340.schnorr_verify) taught me something worth keeping:
# upstream's schnorr_sign VERIFIES ITS OWN OUTPUT before returning, so a broken verifier blows up
# at SIGN time with RuntimeError rather than reaching the self-test's check at all. Both arms are
# kept below: the class fails loud at two independent layers, which is the property worth pinning.
_real_verify = identity.verify
try:
    identity.verify = lambda npub, msg, sig: False
    caught = raises(lambda: identity.boot_self_test("alice"), identity.IdentityError)
finally:
    identity.verify = _real_verify
check("E. control arm: boot self-test FAILS LOUD when verification says no", caught)

_real_low = bip340.schnorr_verify
try:
    bip340.schnorr_verify = lambda msg, pubkey, sig: False
    caught_low = raises(lambda: identity.boot_self_test("alice"), RuntimeError)
finally:
    bip340.schnorr_verify = _real_low
check("E. control arm: a broken verifier is ALSO caught at sign time by upstream's own self-check",
      caught_low)
check("E. real verifiers restored",
      bip340.schnorr_verify is _real_low and identity.verify is _real_verify)

# -- F. preflight, control-armed on both surfaces --
os.chmod(kp, 0o644)
check("F. control arm: world-readable KEY FILE is caught", raises(identity.preflight, identity.IdentityError))
check("F. loose key file also blocks signing (the guard is on the path, not just the check)",
      raises(lambda: identity.sign_as("alice", msg), identity.IdentityError))
os.chmod(kp, 0o600)
os.chmod(os.path.join(_tmp, "keys"), 0o755)
check("F. control arm: group/world-accessible KEY DIR is caught",
      raises(identity.preflight, identity.IdentityError))
os.chmod(os.path.join(_tmp, "keys"), 0o700)
check("F. preflight passes once both modes are correct", identity.preflight() is None)
try:
    identity.preflight_err = None
    os.chmod(kp, 0o644); identity.preflight()
except identity.IdentityError as e:
    check("F. the error names the path and mode but NEVER the key material", secret not in str(e))
finally:
    os.chmod(kp, 0o600)

print("identity-battery: " + ("PASS" if EX == 0 else "FAIL"))
sys.exit(EX)
