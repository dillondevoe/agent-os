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

def _raised(fn):
    """Return the exception TEXT, so a check can assert on what an error says (and what it must
    not say) rather than only that one occurred."""
    try: fn(); return ""
    except Exception as e: return str(e)

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
# The name is a path component; without charset confinement "../x" escapes the identity root
# entirely (the WP-S2 caller-supplied-path class). Confined by construction, mem.* precedent.
check("C. path-traversal participant name is REJECTED at mint",
      raises(lambda: identity.mint("../evil", "x"), identity.IdentityError))
check("C. separator in participant name is REJECTED on the read path too",
      raises(lambda: identity.pubkey_of("a/b"), identity.IdentityError))
check("C. leading-dot participant name is REJECTED",
      raises(lambda: identity.mint(".hidden", "x"), identity.IdentityError))
check("C. control arm: the validator actually admits a normal name",
      identity.mint("carol-2", "os-agent").startswith("npub1"))
check("C. traversal mint left NOTHING outside keys/ (keys/../evil.key resolves here)",
      not os.path.exists(os.path.join(_tmp, "evil.key")))

# -- C2. adversarial review of the gate-authored fix dc0c823 (Geist's lines, my review) --
# The gate does not get to be its own gate. Two defects found in dc0c823 and fixed 2026-08-19;
# both are pinned here so the review leaves evidence rather than an opinion.
check("C2. trailing-newline name REJECTED (Python `$` matches before a final \\n; `\\Z` does not)",
      raises(lambda: identity._key_path("alice\n"), identity.IdentityError))
check("C2. embedded-newline name rejected",
      raises(lambda: identity._key_path("al\nice"), identity.IdentityError))
# Case collision: mint() resolves idempotency by FILE EXISTENCE, which means different things on
# a case-insensitive dev box and a case-sensitive deployment target. Assert we fail loud rather
# than silently handing back another participant's key. On a case-SENSITIVE filesystem "Alice" is
# simply a new participant and mints cleanly — both outcomes are correct, silence is not.
_alice_secret = open(os.path.join(_tmp, "keys", "alice.key")).read().strip()
try:
    _other = identity.mint("Alice", "os-agent")
    check("C2. case-variant name minted a DISTINCT identity (case-sensitive fs)", _other != npub)
except identity.IdentityError as _e:
    check("C2. case-variant name FAILS LOUD rather than returning another participant's key "
          "(case-insensitive fs)", "collision" in str(_e))
    # An error message is a leak surface like any other. Asserting `True` here would have been
    # decoration — the check has to be able to fail.
    check("C2. the collision error names both participants but NEVER key material",
          "Alice" in str(_e) and "alice" in str(_e) and _alice_secret not in str(_e))

# -- C3. the SAME class on the READ paths (found gating PR #123 — mint was one path of three) --
# pubkey_of("Alice") and sign_as("Alice") resolve the key file by filesystem semantics too; on a
# case-insensitive fs the unguarded versions returned/signed-with ALICE's key, and silently
# signing as another participant is strictly worse than handing back her npub. Branch-armed the
# same way as C2: on a case-sensitive fs "Alice" is her own participant and both calls succeed
# against her OWN key; on a case-insensitive fs both must fail loud.
_c3_msg = bip340.tagged_hash("AgentOS/test-c3", b"\x04" * 32)
try:
    _pk_case = identity.pubkey_of("Alice")
    check("C3. read path resolves case-variant to its OWN distinct key (case-sensitive fs)",
          _pk_case != identity.pubkey_of("alice"))
    _sig_case = identity.sign_as("Alice", _c3_msg)
    check("C3. sign path: case-variant signature verifies ONLY against its own npub",
          identity.verify(bech32.npub_encode(_pk_case), _c3_msg, _sig_case)
          and not identity.verify(identity.npub_of("alice"), _c3_msg, _sig_case))
except identity.IdentityError as _e3:
    check("C3. read path (pubkey_of) fails loud on case-collision, never returns another's key",
          "collision" in str(_e3))
    check("C3. sign path (sign_as) fails loud on case-collision, never signs with another's key",
          raises(lambda: identity.sign_as("Alice", _c3_msg), identity.IdentityError))

# -- C4. review of the gate-authored completion f21ea5e: the guard's OWN precondition --
# Geist's extracted lesson ("what OTHER path resolves this same input?") applied to his own fix.
# The call sites are genuinely complete — an AST audit confirms all six name->key resolvers reach
# _assert_recorded_name. The hole was one level in: the guard returned early when no registry
# entry existed, which is a FAIL-OPEN on its own precondition, reachable by an interrupted mint()
# (key written with O_EXCL, registry written after). Measured before the fix: sign_as("Alice")
# with alice.md removed produced a signature that verified against ALICE's pubkey.
import shutil as _shutil
_c4msg = bip340.tagged_hash("AgentOS/test", b"\x09" * 32)  # local: section D's `msg` is defined below
_orphan = os.path.join(_tmp, "participants", "alice.md")
_backup = _orphan + ".bak"
_shutil.copy(_orphan, _backup)
os.remove(_orphan)
try:
    check("C4. orphan key (no registry entry) REFUSES to sign — fail closed, not open",
          raises(lambda: identity.sign_as("alice", _c4msg), identity.IdentityError))
    check("C4. orphan key refuses on the read path too",
          raises(lambda: identity.pubkey_of("alice"), identity.IdentityError))
    check("C4. the refusal names the missing entry, never key material",
          _alice_secret not in _raised(lambda: identity.sign_as("alice", _c4msg)))
finally:
    _shutil.move(_backup, _orphan)
check("C4. guard passes again once the registry entry is restored",
      identity.pubkey_of("alice") is not None)
# A name with neither key nor entry is simply not a participant — the guard must NOT swallow that
# into a collision error, or "unknown participant" becomes unreportable.
check("C4. wholly unknown name still raises 'no such participant', not a collision error",
      "no such participant" in str(_raised(lambda: identity.pubkey_of("nobody-at-all"))))

# -- C5. round 5 (found gating PR #124): "precondition absent" includes "present but UNREADABLE" --
# The C4 fix failed closed on a MISSING entry but the parse loop still left recorded=None on an
# entry with no readable name field — a zero-length/truncated alice.md, produced by the SAME
# mint() crash window (registry write interrupted mid-file). Measured before the fix:
# sign_as("Alice") signed with alice's key straight through the parse loop's None.
_c5_backup = open(_orphan).read()
open(_orphan, "w").close()  # zero-length: exists, carries nothing
try:
    check("C5. truncated registry entry REFUSES to sign — unreadable attribution is no attribution",
          raises(lambda: identity.sign_as("alice", _c4msg), identity.IdentityError))
    check("C5. truncated entry refuses on the read path too",
          raises(lambda: identity.pubkey_of("alice"), identity.IdentityError))
    check("C5. the refusal names the entry, never key material",
          "no readable name field" in _raised(lambda: identity.pubkey_of("alice"))
          and _alice_secret not in _raised(lambda: identity.pubkey_of("alice")))
finally:
    open(_orphan, "w").write(_c5_backup)
check("C5. guard passes again once the entry is restored", identity.pubkey_of("alice") is not None)

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
      raises(lambda: identity.sign_as("alice", _c4msg), identity.IdentityError))
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
