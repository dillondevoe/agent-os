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
# test is the proof the Dell was going to give, without the Dell. The deploy half (signing actually
# ON, via $AGENT_OS_AUDIT_SIGNER + $AGENT_OS_AUDIT_REQUIRE_SIGNED) lands when the Dell is switched
# to a sovereign variant at the seal, and is deliberately NOT asserted here.
#
# WHAT THIS ASSERTS, weakest evidence to strongest:
#   1. the unit RAN and succeeded          — oneshot reached active/exited, rc=0
#   2. the tree exists with declared MODES  — keys/ 0700, keys/*.key 0600, participants/*.md 0644
#   3. the self-test passed                — sign/verify roundtrip, in the unit's own words
#   4. no key material reached the journal  — the module claims this; nothing asserted it
#   5. SECOND BOOT yields the SAME npubs    — idempotency on real systemd
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
#   - that anything SIGNS. After this module a box HAS a signer and still does not sign; that
#     separation is the point of step 1 and asserting otherwise would test a deploy decision the
#     build deliberately does not make.
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
  '';
}
