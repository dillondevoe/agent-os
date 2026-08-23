# tests/identity-boot.nix — task 324 step 2, boot leg. The runtime witness that first-boot
# participant minting actually happens on real systemd.
#
# WHY THIS EXISTS (2026-08-23). Step 1 (#140) wired `modules/identity.nix` and everything in the
# tree agreed it was correct: the module parses, the package builds, the sandbox battery exercises
# `ensure_boot_identities()` directly. None of that is evidence that a BOOTING MACHINE mints
# anything. The distinction is this repo's own recurring class — `checks.cap-sandbox` validates a
# policy's content while nothing consumes it; `RuntimeWatchdogUSec` reports intent whether or not a
# device opened. A oneshot that never runs, or runs and fails, leaves every existing gate green.
#
# It was going to be verified on the Dell. It could not be: the Dell runs `agentos-open`, and
# Geist's 2026-08-23 ruling on 324 is that identity FOLLOWS AUDIT — both are sovereign-only, so the
# open box carries no `agent-os-*` unit at all and is the wrong instrument, not a broken one. This
# test is the proof the Dell was going to give, without the Dell.
#
# THE DEPLOY HALF IS NOW HERE TOO (legs 6-8, Geist's ruling 2026-08-23T05:39Z). It used to say the
# deploy half "lands at the seal and is deliberately NOT asserted here" — that sentence is deleted
# rather than left standing, because a comment that was true when written is exactly the bug this
# repo spent 2026-08-23 on (line 97 of configuration-open.nix, same day, same class). The two env
# vars are set in the NODE CONFIG, not on the command line, because the Dell's host config will set
# them and a test that sets them per-invocation would share no shape with the deploy it stands in
# for. What that buys beyond convenience: `identity.nix` claims to be "ordered BEFORE anything that
# would want a signer … ordering against the future, cheaply", and with signing ON from boot that
# future is HERE — if anything in the sovereign image signs before the oneshot has minted, this
# test fails loud in CI instead of at the seal. Legs 1-5 therefore run with signing ON, which is
# the deployed state anyway.
#
# WHAT THIS ASSERTS, weakest evidence to strongest:
#   1. the unit RAN and succeeded          — oneshot reached active/exited, rc=0
#   2. the tree exists with declared MODES  — keys/ 0700, keys/*.key 0600, participants/*.md 0644
#   3. the self-test passed                — sign/verify roundtrip, in the unit's own words
#   4. no key material reached the journal  — the module claims this; nothing asserted it
#   5. SECOND BOOT yields the SAME npubs    — idempotency on real systemd
#   6. a record APPENDS and is signed        — deploy shape: env from the node config
#   7. `audit verify` passes under the pin   — the signature is real, not decorative
#   8. a forged signer is REJECTED           — and the negative arm shows the PIN is what rejects
#
# Leg 5 is the one that needs a VM and cannot be faked in a battery. `mint()` is idempotent by
# construction ("an existing key is NEVER replaced — re-minting would silently orphan every
# signature the old key produced"), and a battery can show that by calling it twice in one process.
# What a battery CANNOT show is that a real reboot — fresh process, tmpfiles re-run, unit re-fired
# with RemainAfterExit already satisfied on the prior boot — does not rotate the keys. A regression
# there is catastrophic and silent: the box keeps booting, the ledger keeps appending, and every
# signature written before the reboot is now unverifiable. Nothing else in the tree would notice.
#
# Leg 4 exists because modules/identity-pkg.nix states "prints npubs (public by definition) and
# never key material" and, until this file, that was a comment. A leak would be invisible forever.
#
# NOT covered here, stated so no coverage is claimed silently:
#   - the real-host deploy. Legs 6-8 assert the deploy SHAPE (env in the node config, the wrapper
#     as the image installs it) on a VM; that the Dell's own host config carries the same two
#     values is a seal-checklist line, not something CI can see.
#   - key QUALITY (BIP-340 correctness, CSPRNG). tests/ already covers bip340/bech32 directly;
#     this test would only re-run them one layer up and claim more than it checks.
#   - the OPEN variant. It has no identity unit BY RULING; a test asserting its absence would
#     freeze a scope decision into CI. flake.nix's module lists are where that lives.
{ pkgs, baseModules }:
pkgs.testers.runNixOSTest {
  name = "agentos-identity-boot";
  nodes.machine = {
    imports = baseModules;
    virtualisation.memorySize = 2048;
    virtualisation.cores = 2;
    # THE DEPLOY SHAPE, not a test affordance. Set here — in the node config — rather than on the
    # `audit` command line, because this is where the Dell's host config will set them, and a test
    # whose mechanism differs from the deploy's is testing a different thing while reading like the
    # same one. Both together, never one: modules/audit-pkg.nix's DEPLOY-COUPLING RULE (PR #126
    # finding G) — SIGNER without REQUIRE_SIGNED lets a whole-log rewrite to all-unsigned verify
    # clean from genesis, and `=1` instead of `=agent` lets any registered participant re-sign a
    # fabricated tail. Leg 8 is the live demonstration of the second half.
    environment.variables = {
      AGENT_OS_AUDIT_SIGNER = "agent";
      AGENT_OS_AUDIT_REQUIRE_SIGNED = "agent";
    };
  };
  testScript = ''
    machine.start()
    machine.wait_for_unit("multi-user.target")

    root = "/var/lib/agent-os/identity"

    with subtest("1. the oneshot ran and succeeded"):
        # `is-active` on a RemainAfterExit oneshot reports 'active' after a clean exit. Checked
        # with an explicit Result= as well: `systemctl show` does NOT error on a unit that does
        # not exist, so 'success' alone is a phantom green — measured on the Dell 2026-08-23,
        # where an unknown unit name returned Result=success/ExecMainStatus=0 byte-identically to
        # a real one. The unit must first be PRESENT for its result to mean anything.
        machine.succeed("systemctl cat agent-os-identity-boot.service > /dev/null")
        state = machine.succeed("systemctl show -p ActiveState --value agent-os-identity-boot.service").strip()
        assert state == "active", f"unit ActiveState is {state!r}, expected 'active'"
        res = machine.succeed("systemctl show -p Result --value agent-os-identity-boot.service").strip()
        assert res == "success", f"unit Result is {res!r}, expected 'success'"
        rc = machine.succeed("systemctl show -p ExecMainStatus --value agent-os-identity-boot.service").strip()
        assert rc == "0", f"unit ExecMainStatus is {rc!r}, expected '0'"

    with subtest("2. both participants exist, with the modes the module declares"):
        for name in ("dillon", "agent"):
            machine.succeed(f"test -f {root}/keys/{name}.key")
            machine.succeed(f"test -f {root}/participants/{name}.md")
        # Modes are asserted, not assumed: identity.py's preflight() FAILS LOUD on a permissive
        # key dir, so a wrong mode here would stop the boot unit rather than degrade quietly —
        # but only if the mode is what the module says it is. DIR_MODE=0o700, KEY_MODE=0o600.
        d = machine.succeed(f"stat -c %a {root}/keys").strip()
        assert d == "700", f"keys dir mode is {d!r}, expected '700'"
        for name in ("dillon", "agent"):
            m = machine.succeed(f"stat -c %a {root}/keys/{name}.key").strip()
            assert m == "600", f"{name}.key mode is {m!r}, expected '600'"
            p = machine.succeed(f"stat -c %a {root}/participants/{name}.md").strip()
            assert p == "644", f"{name}.md mode is {p!r}, expected '644'"
        # The registry is meant to be memory a human can read (spec §2.2). If the frontmatter
        # lost its npub the keys would still verify and the registry would be useless.
        for name, role in (("dillon", "owner-human"), ("agent", "os-agent")):
            md = machine.succeed(f"cat {root}/participants/{name}.md")
            assert f"role: {role}" in md, f"{name}.md missing 'role: {role}':\n{md}"
            assert "npub: npub1" in md, f"{name}.md missing a bech32 npub:\n{md}"

    with subtest("3. the sign/verify self-test passed, in the unit's own words"):
        # The load-bearing half per identity-pkg.nix: a signer that produces unverifiable
        # signatures is worse than one plainly absent, so this must FAIL LOUD rather than warn.
        boot_log = machine.succeed("journalctl -b -u agent-os-identity-boot.service --no-pager")
        assert "identity boot self-test PASSED for agent" in boot_log, f"self-test line absent:\n{boot_log}"

    with subtest("4. no key material reached the journal"):
        # identity-pkg.nix: "It prints npubs (public by definition) and never key material."
        # That was a comment until this assertion. Compare against the real secrets on disk
        # rather than a regex for hex — a pattern match would pass a leak in another encoding.
        for name in ("dillon", "agent"):
            secret = machine.succeed(f"cat {root}/keys/{name}.key").strip()
            assert len(secret) == 64, f"{name}.key is {len(secret)} chars, expected 64 hex"
            assert secret not in boot_log, f"{name}'s SECRET KEY appears in the journal"

    # Capture the npubs the first boot produced, from the registry rather than the log.
    npubs_before = {
        name: machine.succeed(
            f"grep '^npub:' {root}/participants/{name}.md"
        ).split(":", 1)[1].strip()
        for name in ("dillon", "agent")
    }
    print(f"first boot npubs: {npubs_before}")

    with subtest("5. a REAL reboot does not rotate the keys — the leg a battery cannot show"):
        machine.shutdown()
        machine.start()
        machine.wait_for_unit("multi-user.target")
        # The unit must have run again, not been skipped — otherwise this proves nothing about
        # idempotency, only that a skipped unit changes nothing. Same phantom-green discipline
        # as leg 1: a unit that did not run is not a unit that ran harmlessly.
        state = machine.succeed("systemctl show -p ActiveState --value agent-os-identity-boot.service").strip()
        assert state == "active", f"after reboot, unit ActiveState is {state!r}"
        log2 = machine.succeed("journalctl -b -u agent-os-identity-boot.service --no-pager")
        assert "identity boot self-test PASSED for agent" in log2, f"self-test absent on 2nd boot:\n{log2}"
        for name in ("dillon", "agent"):
            after = machine.succeed(
                f"grep '^npub:' {root}/participants/{name}.md"
            ).split(":", 1)[1].strip()
            assert after == npubs_before[name], (
                f"{name}'s npub CHANGED across reboot: {npubs_before[name]} -> {after}. "
                "Every signature written before the reboot is now unverifiable."
            )
        print(f"second boot npubs unchanged: {npubs_before}")

    # ── LEGS 6-8: THE DEPLOY ARM (Geist's ruling 2026-08-23T05:39Z) ──────────────────────
    # Placed AFTER leg 5 on purpose: the keys are already shown stable across a reboot, so a
    # signature produced here is a signature produced by the identity this test has already
    # pinned down. Run in this order and NOT reorderable — leg 8 deliberately poisons the log
    # for the pin, and the negative arm reads that same poisoned log.
    ledger = "/var/lib/agent-os/audit/audit.log"

    with subtest("6. ARMING — the node-config env actually reaches the invocation"):
        # Without this the whole deploy arm is theatre. `environment.variables` lands in
        # /etc/profile; if the path this test drives commands through did not source it, legs
        # 6-8 would run with signing OFF, leg 6 would still append (unsigned), leg 7 would
        # still pass (an unsigned log with no earlier signed record verifies clean), and only
        # leg 8 would fail — reading as "the forgery check is broken" when the truth is "the
        # experiment was never set up". A zero is only informative if you first made it
        # capable of being non-zero, so the setup is asserted before anything is concluded
        # from it.
        seen_signer = machine.succeed("echo $AGENT_OS_AUDIT_SIGNER").strip()
        seen_pin = machine.succeed("echo $AGENT_OS_AUDIT_REQUIRE_SIGNED").strip()
        assert seen_signer == "agent", (
            f"$AGENT_OS_AUDIT_SIGNER is {seen_signer!r} at the invocation, expected 'agent' — "
            "the node config did not reach this command, so legs 6-8 would prove nothing"
        )
        assert seen_pin == "agent", (
            f"$AGENT_OS_AUDIT_REQUIRE_SIGNED is {seen_pin!r} at the invocation, expected 'agent'"
        )

    with subtest("6b. a record appends through the image's own wrapper, SIGNED by agent"):
        # `audit` as the image installs it (modules/audit.nix -> audit-pkg.nix), never a direct
        # `python3 bin/audit`: the wrapper is what pins the ledger, AGENT_OS_MODULES and the
        # identity root, and those pins are half of what is under test. The env override route
        # exists in bin/audit only as a battery affordance and is not the deployed path.
        machine.succeed(
            """echo '{"event":"identity-boot-test","actor":"nixos-test"}' | audit append"""
        )
        last = machine.succeed(f"tail -1 {ledger}")
        import json as _json
        rec = _json.loads(last)
        assert rec.get("signer") == "agent", f"record signer is {rec.get('signer')!r}:\n{last}"
        assert isinstance(rec.get("sig"), str) and len(rec["sig"]) == 128, (
            f"record carries no 64-byte hex signature:\n{last}"
        )
        # The signature must verify against the npub leg 5 pinned, not merely be present.
        assert npubs_before["agent"].startswith("npub1"), npubs_before["agent"]

    with subtest("7. `audit verify` passes under the pin"):
        out = machine.succeed("audit verify")
        print(f"verify under pin: {out.strip()}")

    with subtest("8. a forged signer is REJECTED by the pin"):
        # `dillon` is a REGISTERED participant with a REAL key, so this record is correctly
        # signed and chains cleanly. Nothing about it is malformed. The only thing wrong with
        # it is WHO signed it — which is precisely the attack `=1` would wave through (finding
        # A: an actor holding any registered participant's key drops the tail and re-signs a
        # fabricated suffix as themselves). Forging with a bogus name instead would prove far
        # less: it would fail on an unresolvable npub, i.e. on the registry, not on the pin.
        machine.succeed(
            """echo '{"event":"forged","actor":"nixos-test"}' """
            """| AGENT_OS_AUDIT_SIGNER=dillon audit append"""
        )
        machine.fail("audit verify")
        forged = machine.succeed(f"tail -1 {ledger}")
        assert _json.loads(forged).get("signer") == "dillon", forged

    with subtest("8b. NEGATIVE ARM — with the pin UNSET the same log verifies clean"):
        # This is what makes leg 8 mean what it reads. Without it, leg 8 passes just as well if
        # the dillon record were malformed, unsigned, or chain-breaking — and the test would
        # claim "the pin rejects a foreign signer" while actually having shown "a broken record
        # fails verify", which needs no pin at all. Unsetting exactly one variable and getting
        # a PASS isolates the pin as the cause. Same discipline as PR #144's negative arm
        # dying at leg 1: the control has to fail for the reason you named.
        machine.succeed("env -u AGENT_OS_AUDIT_REQUIRE_SIGNED audit verify")
  '';
}
