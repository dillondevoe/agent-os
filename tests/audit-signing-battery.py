#!/usr/bin/env python3
"""audit-signing-battery.py — properties for SIGNED audit records (task 287, slice 3).

Usage: audit-signing-battery.py [<path-to-bin/audit>] [<scratch-dir>]

bin/audit's own battery (audit-battery.sh) covers the unsigned chain. This one covers
what signing adds and — more to the point — every way it could be silently dropped.

The chain has no secret in it, so an actor who can write the log can drop the tail and
re-fabricate a valid canonical chained suffix. Signatures close that. But signatures only
close it if a record cannot QUIETLY become unsigned, so the arms here are mostly about
downgrade: signing turned off mid-log, a `sig` stripped, a signer's registry entry
deleted to make the signature uncheckable. Every one of those must fail verify, and each
is control-armed (asserted to pass before the tamper, fail after) — a must-fail check that
was never armed proves nothing about the guard, only about the fixture.
"""
import json, os, secrets, shutil, subprocess, sys, tempfile

SIG_TAG = "AgentOS/audit-record-v1"          # must equal bin/audit's SIG_TAG

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "bin", "audit")
SCRATCH = sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp(prefix="audit-sig-")
MODULES = os.path.join(REPO, "modules")
sys.path.insert(0, MODULES)
import identity, bip340  # noqa: E402

EX = 0
def check(label, cond):
    global EX
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        EX = 1

AUDIT_DIR = os.path.join(SCRATCH, "audit")
ID_ROOT = os.path.join(SCRATCH, "identity")
LOG = os.path.join(AUDIT_DIR, "audit.log")
os.makedirs(AUDIT_DIR, exist_ok=True)
identity.ROOT = ID_ROOT
identity.KEYS_DIR = os.path.join(ID_ROOT, "keys")
identity.PARTICIPANTS_DIR = os.path.join(ID_ROOT, "participants")
os.environ["AGENT_OS_IDENTITY_ROOT"] = ID_ROOT
NPUB = identity.mint("broker", "os-agent")


def run(payload, signer=None, require=None):
    env = dict(os.environ)
    env["AGENT_OS_AUDIT_DIR"] = AUDIT_DIR
    env["AGENT_OS_MODULES"] = MODULES
    env["AGENT_OS_IDENTITY_ROOT"] = ID_ROOT
    env.pop("AGENT_OS_AUDIT_SIGNER", None)
    if signer is not None:
        env["AGENT_OS_AUDIT_SIGNER"] = signer
    if require is not None:
        env["AGENT_OS_AUDIT_REQUIRE_SIGNED"] = require
    return subprocess.run([sys.executable, AUDIT, "append"], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


def _record_digest(rec):
    """bin/audit's record_digest, reproduced over the SAME unsigned projection it signs."""
    unsigned = {k: v for k, v in rec.items() if k not in ("signer", "sig")}
    canon = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return bip340.tagged_hash(SIG_TAG, canon.encode("utf-8"))


def verify(require=None, id_root=None):
    env = dict(os.environ)
    env["AGENT_OS_AUDIT_DIR"] = AUDIT_DIR
    env["AGENT_OS_MODULES"] = MODULES
    env["AGENT_OS_IDENTITY_ROOT"] = id_root or ID_ROOT
    env.pop("AGENT_OS_AUDIT_SIGNER", None)
    if require is not None:
        env["AGENT_OS_AUDIT_REQUIRE_SIGNED"] = require
    return subprocess.run([sys.executable, AUDIT, "verify"], capture_output=True, text=True,
                          env=env)


def lines():
    with open(LOG) as f:
        return [ln.rstrip("\n") for ln in f if ln.strip()]


def canonical(rec):
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def rewrite(recs):
    """Rewrite the whole log from records, re-chaining so the CHAIN is valid.

    Without this the signature arms would be meaningless: any edit also breaks the chain,
    so verify would fail for the wrong reason and the test would pass while proving
    nothing about signatures. Each arm below re-chains, then asserts the failure names the
    signature.
    """
    import hashlib
    prev = "0" * 64
    out = []
    for i, rec in enumerate(recs):
        rec = dict(rec)
        rec["seq"] = i
        rec["prev"] = prev
        line = canonical(rec)
        prev = hashlib.sha256(line.encode()).hexdigest()
        out.append(line)
    with open(LOG, "w") as f:
        f.write("".join(ln + "\n" for ln in out))


def reset():
    if os.path.exists(LOG):
        os.remove(LOG)


print("audit-signing-battery")

# -- A. no regression: unsigned still works exactly as before --
reset()
check("A. unsigned append exits 0", run({"cap": "file.read"}).returncode == 0)
check("A. unsigned record carries neither signer nor sig",
      "signer" not in json.loads(lines()[0]) and "sig" not in json.loads(lines()[0]))
check("A. unsigned log verifies", verify().returncode == 0)

# -- B. a signed record is really signed --
reset()
r = run({"cap": "file.write", "path": "/tmp/x"}, signer="broker")
check("B. signed append exits 0 (%s)" % r.stderr.strip()[:80], r.returncode == 0)
rec0 = json.loads(lines()[0])
check("B. record carries signer + sig", rec0.get("signer") == "broker" and "sig" in rec0)
check("B. signed log verifies", verify().returncode == 0)
check("B. verify reports the signed count", ", 1 signed" in verify().stdout)
# Recompute the signature INDEPENDENTLY of bin/audit — a battery that only re-runs the
# implementation's own helper cannot tell a wrong digest domain from a right one.
unsigned = {k: v for k, v in rec0.items() if k not in ("signer", "sig")}
digest = bip340.tagged_hash("AgentOS/audit-record-v1", canonical(unsigned).encode())
check("B. signature verifies against the participant's registered npub",
      identity.verify(NPUB, digest, bytes.fromhex(rec0["sig"])))
check("B. the log never contains key material",
      open(os.path.join(ID_ROOT, "keys", "broker.key")).read().strip() not in open(LOG).read())

# -- C. control arms: a tampered signed record must FAIL, with the chain kept valid --
good = json.loads(lines()[0])
rewrite([good])
check("C. control arm: the re-chained good record still verifies", verify().returncode == 0)

bad = dict(good); bad["sig"] = ("f" if bad["sig"][0] != "f" else "0") + bad["sig"][1:]
rewrite([bad])
out = verify()
check("C. a flipped signature byte is caught",
      out.returncode != 0 and "BAD SIGNATURE" in out.stderr)

bad = dict(good); bad["path"] = "/etc/shadow"
rewrite([bad])
out = verify()
check("C. an edited PAYLOAD under a valid chain is caught by the signature",
      out.returncode != 0 and "BAD SIGNATURE" in out.stderr)

bad = dict(good); bad["ts"] = "2000-01-01T00:00:00Z"
rewrite([bad])
check("C. the signature covers ts/seq/prev, not just the payload — an edited ts is caught",
      verify().returncode != 0)

bad = dict(good); bad["sig"] = "zz" + bad["sig"][2:]
rewrite([bad])
check("C. a non-hex sig fails closed rather than raising", verify().returncode == 1)

bad = dict(good); bad["signer"] = 17
rewrite([bad])
check("C. a non-string signer fails closed", verify().returncode == 1)

bad = dict(good); del bad["sig"]
rewrite([bad])
out = verify()
check("C. a half-signed record (signer, no sig) is refused",
      out.returncode != 0 and "half-signed" in out.stderr)

# -- D. downgrade: the whole point --
reset()
run({"cap": "a"}, signer="broker")
check("D. control arm: signed log verifies before the downgrade", verify().returncode == 0)
r = run({"cap": "b"})                      # signer unset => unsigned append succeeds...
check("D. an unsigned append after a signed one still exits 0 (append is stateless)",
      r.returncode == 0)
out = verify()
check("D. ...but VERIFY refuses it — signing cannot be dropped mid-chain",
      out.returncode != 0 and "UNSIGNED record" in out.stderr)

# The no-downgrade rule is FORWARD-only (once signed, stays signed), so on its own it says
# nothing about an attacker stripping the signature off an EARLIER record. That gap is closed by
# composition rather than by the rule, and this arm is what proves the composition actually
# holds: stripping a prefix signature changes that line, so either the chain breaks at the next
# record, or the attacker re-chains — and `prev` is itself signed, so the FOLLOWING record's
# signature then fails. Both directions are checked; an unchecked "it composes" is an assumption.
reset()
run({"cap": "a"}, signer="broker")
run({"cap": "b"}, signer="broker")
r0, r1 = json.loads(lines()[0]), json.loads(lines()[1])
check("D. control arm: two signed records verify", verify().returncode == 0)
stripped = {k: v for k, v in r0.items() if k not in ("sig", "signer")}
with open(LOG, "w") as f:                      # strip WITHOUT re-chaining
    f.write(canonical(stripped) + "\n" + canonical(r1) + "\n")
out = verify()
check("D. stripping a prefix signature without re-chaining breaks the chain",
      out.returncode != 0 and "BROKEN CHAIN" in out.stderr)
rewrite([stripped, r1])                        # ...and WITH re-chaining
out = verify()
check("D. re-chaining after the strip is caught by the NEXT record's signature "
      "(prev is signed, so a prefix cannot be laundered)",
      out.returncode != 0 and "BAD SIGNATURE" in out.stderr)

# -- E. require-signed from genesis --
reset()
run({"cap": "a"})
check("E. control arm: an all-unsigned log verifies by default", verify().returncode == 0)
check("E. AGENT_OS_AUDIT_REQUIRE_SIGNED=1 rejects it", verify(require="1").returncode != 0)
check("E. =0 is not 'set' — it stays permissive", verify(require="0").returncode == 0)

# -- F. append-side fail-closed --
reset()
r = run({"cap": "a"}, signer="")
check("F. a SET-but-empty signer is refused, not read as 'unsigned'", r.returncode == 5)
check("F. ...and nothing was appended", not os.path.exists(LOG) or lines() == [])
r = run({"cap": "a"}, signer="nosuchparticipant")
check("F. an unknown participant fails closed", r.returncode == 5)
check("F. ...and nothing was appended", not os.path.exists(LOG) or lines() == [])
r = run({"cap": "a"}, signer="../../etc/passwd")
check("F. a traversal-shaped signer name is refused", r.returncode == 5)

# unattributable key: identity's own round-4 guard, reached THROUGH audit
entry = os.path.join(ID_ROOT, "participants", "broker.md")
saved = open(entry).read()
os.remove(entry)
r = run({"cap": "a"}, signer="broker")
check("F. an orphan key (registry entry gone) refuses to sign — identity's fail-closed "
      "guard holds through this caller", r.returncode == 5)
open(entry, "w").write(saved)
check("F. control arm: signing works again once the entry is restored",
      run({"cap": "a"}, signer="broker").returncode == 0)

# -- G. an UNCHECKABLE signature is a failed one --
reset()
run({"cap": "a"}, signer="broker")
check("G. control arm: verifies with the registry intact", verify().returncode == 0)
empty_root = os.path.join(SCRATCH, "empty-identity")
os.makedirs(empty_root, exist_ok=True)
out = verify(id_root=empty_root)
check("G. verify FAILS when the signer cannot be resolved — deleting a registry entry "
      "must not be a way to pass verify", out.returncode != 0)

# -- H. a caller cannot forge attribution --
reset()
r = run({"cap": "a", "signer": "dillon", "sig": "00" * 64})
rec = json.loads(lines()[0])
check("H. caller-supplied signer/sig are stripped (RESERVED)",
      r.returncode == 0 and "signer" not in rec and "sig" not in rec)
reset()
run({"cap": "a", "signer": "dillon"}, signer="broker")
rec = json.loads(lines()[0])
check("H. a caller cannot override WHO signed", rec["signer"] == "broker")
check("H. the forged-attribution log still verifies", verify().returncode == 0)

# -- I. signer PIN: AGENT_OS_AUDIT_REQUIRE_SIGNED=<participant> (PR #126 finding A) --
# Unpinned verify accepts a signature from ANY registered participant, so an actor holding
# a low-trust participant's key AND log write can drop the tail and re-sign a fabricated
# suffix as themselves. This arm REPRODUCES that attack (it must still pass under `=1`,
# which is the finding) and then shows the pin rejecting it. No arm covered this before.
identity.mint("lowtrust", "os-agent")


def truncate_to(n):
    keep = lines()[:n]
    with open(LOG, "w") as f:
        f.write("".join(ln + "\n" for ln in keep))


reset()
for c in ("a", "b", "c"):
    run({"cap": c}, signer="broker")
check("I. control arm: the honest 3-record broker log verifies under the pin",
      verify(require="broker").returncode == 0 and len(lines()) == 3)

truncate_to(1)                                   # drop the tail...
r = run({"cap": "forged"}, signer="lowtrust")    # ...and re-sign a suffix as ANOTHER participant
check("I. the attacker's suffix appends cleanly (chain is re-derived, not secret)",
      r.returncode == 0 and len(lines()) == 2)
check("I. finding A reproduced: the rewritten log passes UNPINNED verify",
      verify().returncode == 0)
check("I. finding A reproduced: ...and still passes with =1 ('signed by anyone')",
      verify(require="1").returncode == 0)
out = verify(require="broker")
check("I. THE FIX: =broker rejects the suffix, naming the wrong signer",
      out.returncode != 0 and "WRONG SIGNER" in out.stderr and "lowtrust" in out.stderr)

truncate_to(1)                                   # same tamper shape, SAME signer
run({"cap": "honest-suffix"}, signer="broker")
check("I. control arm: a same-signer suffix still passes the pin — the pin tests WHO "
      "signed, not that the tail was rewritten",
      verify(require="broker").returncode == 0)

reset()                                          # an unsigned record under a pin
run({"cap": "a"})
check("I. a pin also demands signatures from genesis (unsigned log rejected)",
      verify(require="broker").returncode != 0)
check("I. control arm: that same log passes with the pin unset", verify().returncode == 0)

# -- J. KEY SUBSTITUTION: verify must anchor to the REGISTRY, not to the key on disk --
# `npub_of()` DERIVES the pubkey from the private key currently at rest, so it answers
# "who is this key?" — a reference point that moves with the thing it is checking. An
# actor who can write the key file AND the log can therefore mint a fresh key, re-sign a
# wholly fabricated log as `broker`, and pass verify: every signature checks out against
# a pubkey derived from the attacker's own key. `recorded_npub()` answers "who is this
# participant?", which does not move. identity.boot_self_test was anchored to the registry
# on 2026-08-27 for exactly this reason; bin/audit verify was not.
reset()
for c in ("a", "b"):
    run({"cap": c}, signer="broker")
check("J. control arm: the honest broker log verifies before any substitution",
      verify().returncode == 0)

honest_key = open(identity._key_path("broker")).read()
recorded = identity.recorded_npub("broker")
with open(identity._key_path("broker"), "w") as f:      # substitute the key at rest
    f.write(secrets.token_bytes(32).hex())
check("J. the substituted key derives a DIFFERENT npub than the registry records "
      "(the fixture actually substitutes something)", identity.npub_of("broker") != recorded)

reset()                                                 # attacker re-signs a whole log as broker
for c in ("forged-1", "forged-2"):
    r = run({"cap": c}, signer="broker")
    check("J. the forged log appends cleanly under the substituted key", r.returncode == 0)

_rec = json.loads(lines()[0])
check("J. PRE-FIX ARM: the derived anchor ACCEPTS the forgery — this is the defect, "
      "shown firing rather than described",
      identity.verify(identity.npub_of(_rec["signer"]), _record_digest(_rec),
                      bytes.fromhex(_rec["sig"])))
check("J. ...while the REGISTRY anchor rejects the same record — the two anchors "
      "disagree, and only one of them is an identity",
      not identity.verify(recorded, _record_digest(_rec), bytes.fromhex(_rec["sig"])))

out = verify()
check("J. THE FIX: verify rejects a log signed by a substituted key",
      out.returncode != 0 and "broker" in out.stderr)

with open(identity._key_path("broker"), "w") as f:      # restore the real key
    f.write(honest_key)
reset()
run({"cap": "honest-again"}, signer="broker")
check("J. control arm: with the real key restored an honest log verifies again — the "
      "fix rejects substitution, not signatures",
      verify().returncode == 0)

if os.environ.get("AUDIT_SIG_KEEP_SCRATCH") != "1" and len(sys.argv) <= 2:
    shutil.rmtree(SCRATCH, ignore_errors=True)
print("audit-signing-battery: " + ("PASS" if EX == 0 else "FAIL"))
sys.exit(EX)
