"""identity.py — participant keypairs, registry, and the boot self-test (Phase 1.5B, task 287).

INTERIM STORAGE — at-rest encryption pending the LUKS/TPM prerequisite; these keys MUST NOT
guard real value until it lands.

That marker is a binding condition of Geist's 2026-08-19 condition-(b) ruling, and the battery
asserts it so it cannot quietly disappear. The honest description of what this module provides
is: keys held out-of-tree, 0600 (dir 0700), fail-loud preflight, never in config/logs/Nix store.
It is NOT "encrypted at rest" — that phrase names a powered-off/stolen-disk property, and nothing
here defends against that adversary. The spec's original wording was amended for exactly this
reason. File-level encryption was rejected on the merits, not merely on the dependency wall: a
headless box has no one to type a passphrase and no TPM story today, so the wrapping secret would
sit on the same disk as the ciphertext and defend nothing. LUKS (or equivalent) is the real
mechanism and is filed as a standing prerequisite, bound to the same gate boundary as the
libsecp256k1 swap in modules/bip340.py — one wall, two obligations, crossed once.

Signing is BIP-340 Schnorr (modules/bip340.py, vendored reference); npub is NIP-19 bech32
(modules/bech32.py, vendored BIP-173 reference). Neither the curve math nor the codec is ours.

Layout, per spec §2.2 (path-is-meaning — the registry is memory a human can read):
    $AGENT_OS_IDENTITY_ROOT (default ~/identity)
      keys/<name>.key          0600, 64 hex chars, THE SECRET — never read by anything that
                               logs, never rendered, never copied into the registry
      participants/<name>.md   0644, frontmatter: npub + role + created_at
"""
import os, re, secrets, stat, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bip340, bech32

ROOT = os.environ.get("AGENT_OS_IDENTITY_ROOT") or os.path.join(os.path.expanduser("~"), "identity")
KEYS_DIR = os.path.join(ROOT, "keys")
PARTICIPANTS_DIR = os.path.join(ROOT, "participants")

DIR_MODE = 0o700
KEY_MODE = 0o600


class IdentityError(RuntimeError):
    """Fail loud. Never carries key material in its message — only paths and modes."""


# A participant name is a PATH COMPONENT, so it gets the mem.* namespace treatment: confined by
# construction (charset, no separators, no leading dot — "../x" or "a/b" can never form). The
# 2026-08-14 WP-S2 ruling is the boundary law here: self-enforced-root only holds when the impl
# controls the whole path, and a caller-supplied name is caller-supplied path bytes without this.
# `\Z`, NOT `$`: in Python `$` also matches immediately BEFORE a trailing newline, so the first
# version of this gate accepted "alice\n" and produced a distinct key file `alice\n.key`. Not
# traversal — but on a module whose job is attributing actions to distinct keys, a name that
# RENDERS identically to another while resolving to a different key is the worse failure mode.
# (Found reviewing the gate's own fix, 2026-08-19.)
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")

def _validated(name):
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise IdentityError("participant name %r is not a valid namespace "
                            "(need ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ — the name becomes a path "
                            "component, so separators and a leading dot are rejected)" % (name,))
    return name

def _key_path(name): return os.path.join(KEYS_DIR, _validated(name) + ".key")
def _participant_path(name): return os.path.join(PARTICIPANTS_DIR, _validated(name) + ".md")


def preflight():
    """Enforce the out-of-tree secret contract. Never reads key CONTENTS — only stats them.

    Mirrors modules/mail-proton-bridge-open.nix's preflight, which is the house precedent for a
    secret that lives outside the repo and the Nix store. Loud failure is the point: a silent
    downgrade to world-readable keys is precisely the outcome a quiet check would produce.
    """
    if not os.path.isdir(KEYS_DIR):
        return  # nothing minted yet — mint() creates the dir with the right mode
    mode = stat.S_IMODE(os.stat(KEYS_DIR).st_mode)
    if mode != DIR_MODE:
        raise IdentityError("key dir %s is mode %o, must be %o (an out-of-tree secret dir must "
                            "not be group/world-accessible)" % (KEYS_DIR, mode, DIR_MODE))
    for entry in sorted(os.listdir(KEYS_DIR)):
        if not entry.endswith(".key"):
            continue
        p = os.path.join(KEYS_DIR, entry)
        m = stat.S_IMODE(os.stat(p).st_mode)
        if m != KEY_MODE:
            raise IdentityError("key file %s is mode %o, must be %o" % (p, m, KEY_MODE))


def _assert_recorded_name(name):
    """Fail loud when the filesystem's answer to `name` is another participant's files.

    File paths resolve by FILESYSTEM semantics, not name equality: on macOS (case-insensitive,
    the dev box) "Alice" hits alice's key file; on the case-sensitive deployment target it does
    not. PR #123 closed this for mint(); the read paths (pubkey_of, sign_as) had the same defect
    — a case-variant name silently SIGNED WITH another participant's key, which is strictly worse
    than handing back her npub. So the recorded-name assertion guards every path that resolves a
    name to a key, not just the minting one. (Found gating PR #123, 2026-08-19 — the same class
    the PR itself names, fixed on one path of three.)
    """
    pp = _participant_path(name)
    if not os.path.exists(pp):
        return  # no registry entry — an orphan key is preflight's problem, not a collision
    recorded = None
    for line in open(pp):
        if line.startswith("name: "):
            recorded = line[len("name: "):].strip(); break
    if recorded is not None and recorded != name:
        raise IdentityError(
            "participant name collision: %r resolves to the existing key for %r "
            "(case-insensitive filesystem). Refusing to return another participant's "
            "key — pick a distinct name." % (name, recorded))


def mint(name, role):
    """Mint a participant keypair + registry entry. Idempotent: an existing key is NEVER replaced.

    Re-minting would silently orphan every signature the old key produced, so an existing key is
    returned as-is. Recovery is a NEW key plus re-attestation (spec §2.1) — deliberately not a
    thing this function can do by accident.
    """
    os.makedirs(KEYS_DIR, mode=DIR_MODE, exist_ok=True)
    os.makedirs(PARTICIPANTS_DIR, mode=0o755, exist_ok=True)
    os.chmod(KEYS_DIR, DIR_MODE)  # makedirs respects umask; an existing dir keeps its old mode
    kp = _key_path(name)
    if os.path.exists(kp):
        preflight()
        _assert_recorded_name(name)
        return npub_of(name)

    # secrets.token_bytes is the CSPRNG; BIP-340 requires 1 <= d <= n-1, so reject the
    # (negligible-probability) out-of-range draw rather than clamping it — clamping would bias
    # the key space, and "negligible" is not "impossible".
    while True:
        sk = secrets.token_bytes(32)
        if 1 <= int.from_bytes(sk, "big") <= bip340.n - 1:
            break
    pub = bip340.pubkey_gen(sk)

    fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, KEY_MODE)  # mode at CREATE time,
    try:                                                              # never a chmod afterwards
        os.write(fd, sk.hex().encode())
    finally:
        os.close(fd)

    npub = bech32.npub_encode(pub)
    with open(_participant_path(name), "w") as f:
        f.write("---\nname: %s\nrole: %s\nnpub: %s\npubkey_hex: %s\ncreated_at: %s\n"
                "key_type: secp256k1-schnorr-bip340\n---\n\n"
                "Participant `%s` (%s). Public identity only — the secret key lives outside this\n"
                "registry, in the agent-only key dir, and is never rendered here.\n"
                % (name, role, npub, pub.hex(),
                   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), name, role))
    preflight()
    return npub


def npub_of(name):
    return bech32.npub_encode(pubkey_of(name))


def pubkey_of(name):
    kp = _key_path(name)
    if not os.path.exists(kp):
        raise IdentityError("no such participant: %s (expected %s)" % (name, kp))
    preflight()
    _assert_recorded_name(name)
    return bip340.pubkey_gen(bytes.fromhex(open(kp).read().strip()))


def sign_as(name, msg):
    """Sign 32 bytes as `name`. The acting participant signs its OWN work (spec §2.3)."""
    if len(msg) != 32:
        raise IdentityError("BIP-340 signs a 32-byte digest, got %d bytes" % len(msg))
    preflight()
    _assert_recorded_name(name)
    sk = bytes.fromhex(open(_key_path(name)).read().strip())
    return bip340.schnorr_sign(msg, sk, secrets.token_bytes(32))


def verify(npub, msg, sig):
    """Verify against an npub. Returns False on a bad signature; raises only on malformed input."""
    return bip340.schnorr_verify(msg, bech32.npub_decode(npub), sig)


def boot_self_test(name="agent"):
    """Sign/verify roundtrip on every boot — ruling condition 3. Fails LOUD, never silently.

    A signer that has degraded to producing unverifiable signatures is worse than one that is
    plainly absent: the audit trail keeps accumulating records that nothing can check later.
    """
    msg = bip340.tagged_hash("AgentOS/boot-self-test", secrets.token_bytes(32))
    sig = sign_as(name, msg)
    if not verify(npub_of(name), msg, sig):
        raise IdentityError("identity boot self-test FAILED for %r — signer produced a signature "
                            "that does not verify against its own pubkey" % name)
    return True


def ensure_boot_identities(owner="dillon", agent="agent"):
    """First-boot keygen for the owner-human and the OS agent (spec §2.1), then the self-test."""
    minted = {owner: mint(owner, "owner-human"), agent: mint(agent, "os-agent")}
    boot_self_test(agent)
    return minted
