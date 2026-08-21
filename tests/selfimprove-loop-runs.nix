# tests/selfimprove-loop-runs.nix — the runtime witness that the self-improvement loop
# ACTUALLY RUNS, on a booted machine, and produces a digest with real observations in it.
#
# WHY THIS EXISTS (2026-08-20). `modules/selfimprove-open.nix` installs the eight-module
# engine into the open image and arms a 6h timer, and its own header says, in as many words:
#
#     "...and the package builds — NOT that the timer fires or the digest lands. That needs
#      a real [boot]."
#
# For a day that sentence was the whole coverage story. Every gate in this repo was green on
# the engine — a module manifest checked off the built artifact, an import guard that fails
# the build if a module goes missing, eight contract batteries, 74 checks — and across three
# machines and one Dell the loop had never executed a single time. `agos_cycle.main()` had
# never been called by anything except a battery calling it directly.
#
# That is the caller defect one level up, which this repo has now hit three times: the parts
# are green, the thing the parts exist to do has never happened. A build gate cannot catch it
# by construction — "the unit file parses" and "the unit ran" are different facts, and only
# one of them needs a kernel.
#
# WHAT THIS ASSERTS, in the order the evidence strengthens:
#   1. the timer is ARMED — active, and with a real next-elapse, not merely present
#   2. the digest does NOT exist before the service runs        (the control arm)
#   3. the service runs to completion and the digest LANDS
#   4. the digest is NOT the blind one — the loop observed real signals   (the leg that matters)
#   5. APPLY did not happen: no proposal target was written
#
# Leg 4 is the reason this test is not just leg 3 with extra steps. `agos_cycle` leads its
# digest with CANNOT-ASSESS when it could read no sources, which is correct behaviour and is
# exactly what a VM produces by default (there is no turn-log on a fresh box). A test that
# only asserted "a digest appeared" would pass on a machine where the loop observed NOTHING —
# a green that says the plumbing is connected and nothing about whether anything flows through
# it. So the test SEEDS a turn-log with two real cost-cap records and then asserts the digest
# stopped saying CANNOT-ASSESS. That is the difference between "the loop ran" and "the loop
# ran and saw something", and this repo's own ledger (docs/cancelled-boundaries.md) names the
# former class as indistinguishable from a tool that never ran.
#
# Leg 2 is a control arm and not a formality. `systemd.tmpfiles` creates the state DIRECTORY
# at boot; if it (or a future refactor) ever created the digest FILE too, leg 3 would pass
# without the service having done anything at all. Asserting the absence first is what makes
# the later presence evidence.
#
# CONTROL-ARMED 2026-08-20 — this is a guard, not just a green. A passing test proves nothing
# about a test until you have watched it fail. Two arms, each reverted after:
#   ARM A — delete `wantedBy = [ "timers.target" ];` from modules/selfimprove-open.nix.
#           RED at line 21, `systemctl is-active agos-selfimprove.timer` exit 3. That is the
#           failure a careless refactor of the module would actually cause, and leg 1 catches
#           it by assertion — not by driver timeout. (First attempt at this arm DID die by
#           900s timeout, because the script waited on the unit under test; see the note at
#           the wait line for why the wait now sits on timers.target instead.)
#   ARM C — add `"f ${stateDir}/digest.md 0644 root root -"` to systemd.tmpfiles.rules, so the
#           digest exists before the service ever runs. RED at line 33, `test -e .../digest.md`
#           unexpectedly succeeded. This is the arm on the CONTROL: it proves leg 2 can fire,
#           and therefore that leg 3's "the digest landed" is evidence of the loop running
#           rather than of tmpfiles having made the file.
#   BASELINE — green, unmodified tree, in the same sweep as both arms.
#
# NOT covered here, stated so no coverage is claimed silently:
#   - that the timer FIRES on its own, ON EVERY RUN. This test starts the service explicitly
#     rather than idling a VM for ten minutes; leg 1 asserts the arming, which is the fact a
#     refactor would silently destroy, and the firing itself is systemd's behaviour, not ours.
#     Recorded here because it is evidence and it should not be lost: the autonomous fire WAS
#     observed once, during this test's own development (2026-08-20). The first run timed out
#     waiting for multi-user.target, and its boot log contains the loop running unattended at
#     OnBootSec, with nothing having invoked it:
#         [600.24] systemd[1]: Starting Agent OS — self-improvement loop: ...
#         [615.42] agos-selfimprove[3316]: CANNOT-ASSESS: 0 of 1 sources readable ...
#         [615.49] agos-selfimprove[3316]: digest: /var/lib/agos-selfimprove/digest.md
#         [616.00] systemd[1]: agos-selfimprove.service: Deactivated successfully.
#     That is the first time the loop executed anywhere, on any machine, and it did so without
#     being asked. The CANNOT-ASSESS is correct: nothing had seeded a turn-log at that point,
#     and the engine refused to report an empty read as a quiet system. Re-testing systemd on
#     every CI run to reproduce it would cost ten minutes of wall clock for a fact already in
#     evidence.
#   - APPLY. There is nothing to cover — APPLY is unbuilt pending Q1. Leg 5 asserts the
#     engine does not write a proposal target, which is the property that must survive until
#     that question is answered, not a stand-in for testing an APPLY that does not exist.
#   - anything about the SEALED lane. This engine ships in the open image only; every other
#     VM test in this repo composes `baseModules`, and this is the first to compose
#     `openModules`, so the open lane had zero behavioural VM coverage before it.
{ pkgs, openModules }:
pkgs.testers.runNixOSTest {
  name = "agentos-selfimprove-loop-runs";
  # `configuration-open.nix` pulls in gaming-open.nix, which sets
  # `nixpkgs.config.allowUnfreePredicate`. runNixOSTest hands each node a read-only pkgs by
  # default, which itself defines `nixpkgs.config` — two definitions of a unique option, so
  # evaluation fails before a VM is ever built. Letting the node instantiate its own nixpkgs
  # is the documented escape, and it is the RIGHT one here: the open image's unfree predicate
  # is part of the configuration under test, so forcing it away would test a machine the open
  # lane never ships.
  node.pkgsReadOnly = false;
  nodes.machine = {
    imports = openModules;
    virtualisation.memorySize = 4096;
    virtualisation.cores = 4;
    # Tailscale's autoconnect oneshot is turned OFF for the test, and this is the one
    # deviation from the shipped open configuration, so it is stated rather than buried.
    # `tailscaled-autoconnect` runs `tailscale up --auth-key file:/var/lib/tailscale/authkey`;
    # that key is a runtime secret placed by install.sh and is deliberately NOT in the repo,
    # so in a sandboxed VM the unit sits there failing to reach controlplane.tailscale.com
    # for its full five-minute timeout before giving up. It is orthogonal to the engine under
    # test — the loop reads a local file and writes a local digest, and touches no network —
    # so the only thing that unit contributes here is five minutes of wall clock and a
    # screenful of DNS failures. The DAEMON stays enabled; only the auto-join is stubbed.
    systemd.services.tailscaled-autoconnect.enable = false;

    # DEVIATION 2, and the reason this test could not run in CI at all until it was made.
    # The open image bundles three model-seed units. `agos-seed-model-3b` pulls in a
    # HASH-PINNED FIXED-OUTPUT GGUF that has no fetcher: its builder's only job is to print
    # `nix-store --add-fixed sha256 ...` and exit 1. The blob is staged by hand, and it is
    # staged in exactly one Nix store on earth — DVo's. So the open image is UNBUILDABLE on
    # any machine that does not already have it, GitHub runners included; the first CI run of
    # this test died there in 2m25s, having never booted anything. `agos-seed-model` is
    # buildable but fetches a multi-gigabyte 9B from HuggingFace on every cold runner.
    #
    # Disabling the units drops the store paths from the closure, because the weights are
    # referenced only through the unit scripts. That is the honest scope of this test: the
    # subject is the SELF-IMPROVEMENT LOOP, which neither loads a model nor talks to ollama.
    # It is NOT a claim that the open image as shipped builds in CI — it does not, and that
    # is a real gap in the open lane rather than a property of this file. Filed separately.
    systemd.services.agos-seed-model-3b.enable = false;
    systemd.services.agos-seed-model.enable = false;
    systemd.services.agos-seed-lora.enable = false;
  };

  testScript = ''
    machine.start()
    # Deliberately NOT multi-user.target. The open image is a full desktop (hyprland, waybar,
    # xdg portals, flatpak) on top of the agent stack, and on a sandboxed VM it takes well over
    # fifteen minutes to settle — the first run of this test died on exactly that, at
    # `wait_for_unit("multi-user.target")`, having already proven the thing it was written to
    # prove. Waiting on the unit under test instead of on the whole machine is both faster and
    # a tighter assertion: a green here cannot be bought by some unrelated desktop service
    # finally coming up.
    #
    # We wait on timers.target, NOT on agos-selfimprove.timer itself. timers.target is a
    # systemd built-in that is reached early and is reached REGARDLESS of whether our timer
    # is wanted by it — it is the same fixed point in a healthy tree and in a broken one.
    # Waiting on the unit under test looks tighter but is worse: control arm A (drop
    # `wantedBy = [ "timers.target" ]`) then hangs the full 900s driver timeout and dies of
    # a timeout instead of dying on the leg-1 assertion. A red is only evidence if it is a
    # red for the RIGHT reason, so the wait goes on the fixed point and the assertion goes
    # on the timer.
    machine.wait_for_unit("timers.target")

    # ── Leg 1: the timer is ARMED, not merely installed ──────────────────────────────
    machine.succeed("systemctl is-active agos-selfimprove.timer")
    # A timer can be active and still be scheduled for nothing. NextElapseUSecMonotonic is
    # systemd's own answer to "when will this actually fire"; 0 means never, which is what a
    # dropped OnBootSec/OnUnitActiveSec would leave behind while `is-active` stayed green.
    nxt = machine.succeed(
        "systemctl show agos-selfimprove.timer -p NextElapseUSecMonotonic --value"
    ).strip()
    assert nxt not in ("", "0", "infinity"), \
        f"timer is active but scheduled for nothing: NextElapseUSecMonotonic={nxt!r}"

    # ── Leg 2 (control arm): the digest does not exist yet ───────────────────────────
    machine.succeed("test -d /var/lib/agos-selfimprove")
    machine.fail("test -e /var/lib/agos-selfimprove/digest.md")

    # Seed the source the loop reads. Two cost_cap_breaker records so COMPARE has something
    # that can recur; the third line is deliberately not one, to confirm the parser filters
    # on `event` rather than counting lines.
    machine.succeed("mkdir -p /root/memory")
    machine.succeed(
        "printf '%s\\n' "
        "'{\"event\":\"cost_cap_breaker\",\"kind\":\"hops\",\"hops\":40,\"ts\":\"2026-08-20T00:00:00Z\"}' "
        "'{\"event\":\"cost_cap_breaker\",\"kind\":\"hops\",\"hops\":41,\"ts\":\"2026-08-20T01:00:00Z\"}' "
        "'{\"event\":\"something_else\",\"ts\":\"2026-08-20T02:00:00Z\"}' "
        "> /root/memory/turn-log.jsonl"
    )

    # ── Leg 3: the service runs to completion and the digest lands ───────────────────
    machine.succeed("systemctl start agos-selfimprove.service")
    machine.wait_until_succeeds(
        "systemctl show agos-selfimprove.service -p SubState --value | grep -qx dead", timeout=120
    )
    rc = machine.succeed(
        "systemctl show agos-selfimprove.service -p ExecMainStatus --value"
    ).strip()
    assert rc == "0", f"the loop exited {rc} — a non-zero here means it could not surface"
    machine.succeed("test -s /var/lib/agos-selfimprove/digest.md")
    # The stores are the loop's memory across runs; a digest without them is a loop that
    # cannot dedup and will re-report the same signal forever.
    machine.succeed("test -s /var/lib/agos-selfimprove/lessons.db")
    machine.succeed("test -s /var/lib/agos-selfimprove/proposals.db")

    digest = machine.succeed("cat /var/lib/agos-selfimprove/digest.md")
    print(digest)

    # ── Leg 4: the digest is not the blind one ───────────────────────────────────────
    assert "CANNOT-ASSESS" not in digest, (
        "the loop ran but observed nothing — it read 0 sources despite the seeded turn-log. "
        "A digest that leads with CANNOT-ASSESS is the engine correctly refusing to call an "
        "empty result a quiet system, and it means this test proved only that a file appeared."
    )
    assert "self-improvement digest" in digest, \
        "digest does not look like the engine's own output"

    # ── Leg 5: no APPLY ─────────────────────────────────────────────────────────────
    # The engine reports proposals; it changes nothing it proposed. Until Q1 is answered
    # that is the load-bearing property of the whole loop.
    assert "APPLY is not implemented" in digest, \
        "the digest dropped its own no-APPLY disclaimer"
    machine.fail("test -e /var/lib/agos-selfimprove/lessons.md")
  '';
}
