# tests/cap-composed-path.nix — WP-S2 / GATE #5(a), the COMPOSED half.
#
# tests/cap-sandbox-confinement.nix ends with an explicit statement of what it does not cover:
#
#     "this drives the DISPATCHER directly, with the store pins supplied as env — the same
#      affordance the battery uses at a box. It therefore says nothing about the broker's own
#      wrapper handing the dispatcher those pins. [...] The fully-composed path — real broker,
#      real wrapper, confined, in a VM — is not yet asserted anywhere, and that is the next
#      increment after this one."
#
# This is that increment. It is the ONLY runtime witness for the wiring, and the reason it exists
# is a measured hole, not tidiness: the per-cap fs confinement reaches production through exactly
# two `export` lines in mkWrapper's `lib.optionalString confined` block (modules/cap-invoke-pkg.nix).
# Delete them and cap-invoke reads an unset AGENT_OS_CAP_SANDBOX as "unconfined by config" and
# direct-execs the impl. Before `checks.cap-wrapper-pinned` (flake.nix) nothing noticed at all;
# that check now notices at BUILD time by grepping the wrapper text. What it cannot do is prove
# systemd honours the pins it found. This test closes the other half at RUNTIME:
#
#   cap-wrapper-pinned          proves production is WIRED to confine   (build, wrapper text)
#   cap-sandbox-confinement     proves the confinement WORKS            (runtime, pins as env)
#   THIS TEST                   proves the wiring DELIVERS it           (runtime, pins from wrapper)
#
# THE LOAD-BEARING DIFFERENCE FROM ITS SIBLING: this test supplies NEITHER AGENT_OS_CAP_SANDBOX
# NOR AGENT_OS_SYSTEMD_RUN. It actively scrubs both from the environment and asserts they are
# absent before invoking. Everything the confinement needs must arrive from the broker's own
# wrapper (modules/broker.nix pins AGENT_OS_INVOKE_SEAM to capInvoke.wrapper — the CONFINED
# build) or the escape leg goes red. That inversion is the whole point: its sibling proves the
# kernel stops the escape when correctly configured; this proves production configures it.
#
# WHY file.read IS THE VEHICLE: it is T0 (modules/capability-registry.nix), so the broker
# ALLOW-AUTOs it with no confirm seam — a headless VM has no tty for modules/confirm.nix to drive.
# It also carries a real `sandbox` decl and is in `shippedCaps`, so it exercises the derived
# confinement rather than a synthetic stand-in. A T1 cap (file.write) would need the confirm seam
# and would be testing two things at once.
#
# WHAT THIS STILL DOES NOT COVER, stated rather than implied: it asserts the READ escape. The
# write half shares ONE residual escape with it (cap-sandbox-battery.sh's own analysis: both are
# the symlinked PARENT, and one mount namespace closes both), and file.write is T1, so covering
# it here would require driving the confirm seam. The battery covers write under confinement
# directly. This test's claim is precisely: the broker's wrapper delivers a REAL confinement to a
# REAL impl for at least one shipped cap, and the two exports are therefore load-bearing at
# runtime and not merely present in a string.
{ pkgs, baseModules }:

let
  # The canary lives OUTSIDE every declared root, exactly as in cap-sandbox-battery.sh. Same bytes
  # on purpose: if these two tests ever disagree about what a leak looks like, that is a bug in the
  # tests, not a finding.
  canaryBytes = "CAP-SANDBOX-CANARY-8f3a-must-never-be-read";
  outside = "/var/lib/agent-os-composed-outside";
  safe = "/var/lib/agent-os/safe-read";
in
pkgs.testers.runNixOSTest {
  name = "agentos-cap-composed-path";

  nodes.box = { ... }: {
    imports = baseModules;
    # For the HARNESS only (bash/python to build the verdict JSON and read the reply). The broker,
    # the seam and the impls are the store-pinned production artefacts that baseModules installs —
    # deliberately NOT re-declared here, because pinning them in the test would be the test
    # supplying what production is supposed to supply, which is the exact bug under examination.
    environment.systemPackages = with pkgs; [ bash coreutils python3 systemd ];
  };

  testScript = ''
    import base64
    import json

    box.wait_for_unit("multi-user.target")

    # Same readiness discipline as cap-sandbox-confinement.nix: `is-system-running` exits 1 on
    # `degraded` (sealed variants leave units masked), so the STRING is the assertion, not the
    # status — and it cannot be a pipeline, because succeed() runs under pipefail.
    box.succeed(
        "state=$(systemctl is-system-running --wait || true); echo \"systemd state: $state\"; "
        "case \"$state\" in running|degraded) exit 0 ;; *) exit 1 ;; esac"
    )
    print(box.succeed("systemctl --version | head -1"))

    # The capability roots must come from broker.nix's tmpfiles. A missing bind source is a hard
    # deny in production, which would make the escape leg below pass for entirely the wrong reason.
    box.succeed("test -d ${safe}")
    box.succeed("test -d /var/lib/agent-os/workspace")

    # `broker` is on PATH because modules/broker.nix puts it in environment.systemPackages. This is
    # the production binary with the production wrapper — the thing whose wiring is under test.
    broker = box.succeed("command -v broker").strip()
    print(f"broker under test: {broker}")

    # ── the scrub ────────────────────────────────────────────────────────────────────────────
    # Everything below runs with BOTH confinement variables removed from the environment, and the
    # removal is ASSERTED rather than trusted. If a future edit leaks either one into the test
    # environment, this test would silently revert to being a second copy of its sibling — proving
    # the kernel works rather than proving production wires it. `env -u` is belt; the assertion is
    # braces. (A guard that permits everything is indistinguishable from a tool that never ran.)
    scrub = "env -u AGENT_OS_CAP_SANDBOX -u AGENT_OS_SYSTEMD_RUN"
    box.succeed(
        f"{scrub} sh -c '"
        "[ -z \"''${AGENT_OS_CAP_SANDBOX-}\" ] || exit 1; "
        "[ -z \"''${AGENT_OS_SYSTEMD_RUN-}\" ] || exit 1'"
    )

    def broker_call(path):
        """One real tools/call file.read through the REAL broker. Returns combined output.

        The verdict goes to a file via create_file rather than being interpolated into a shell
        command: the paths under test contain no metacharacters today, but a quoting bug here
        would corrupt the request and produce a deny that looks exactly like a working
        confinement — the failure mode this whole test exists to rule out.
        """
        verdict = json.dumps({
            "ok": True, "method": "tools/call", "id": 1,
            "name": "file.read", "arguments": {"path": path},
        })
        b64 = base64.b64encode(verdict.encode()).decode()
        box.succeed("mkdir -p /run/composed-test")
        box.succeed(f"printf %s {b64} | base64 -d > /run/composed-test/verdict.json")
        # `|| true` because the deny legs EXPECT a non-zero broker exit; swallowing it inside
        # succeed() would turn an informative red into an opaque one. The assertions below read
        # the OUTPUT, which is what carries the verdict and the (never-permitted) canary bytes.
        return box.succeed(
            f"{scrub} sh -c '{broker} run < /run/composed-test/verdict.json' 2>&1 || true"
        )

    # ── 1. POSITIVE CONTROL, through the composed path ───────────────────────────────────────
    # FIRST, and non-negotiable. A confined in-root read must still SUCCEED end-to-end. Without
    # this leg, leg 2's deny proves nothing: a broker that refused every file.read for an unrelated
    # reason (missing root, seam misconfigured, impl crash) would produce an identical red-free
    # result. This is the control arm, and it is the leg that makes the escape leg meaningful.
    box.succeed("printf 'hello composed world' > ${safe}/plain.txt")
    out = broker_call("${safe}/plain.txt")
    print("positive control output: " + out)
    assert '"ok": true' in out or '"ok":true' in out, (
        "COMPOSED PATH BROKEN: an in-root file.read through the real broker did not succeed. "
        "Every deny below is now meaningless — fix this before reading leg 2. Output: " + out
    )
    assert "hello composed world" in out, (
        "in-root read returned ok but not the content — the impl did not actually run: " + out
    )
    print("composed 1 OK  (real broker -> real wrapper -> confined impl: in-root read works)")

    # ── 2. THE ESCAPE, through the composed path ─────────────────────────────────────────────
    # The symlinked PARENT — the residual escape the impl's lstat cannot close, because every
    # component but the last is resolved by the kernel during open(). Unconfined, this returns the
    # canary (cap-sandbox-battery.sh leg 0 demonstrates exactly that). Confined, the target does
    # not exist in the impl's mount namespace at all.
    box.succeed("mkdir -p ${outside}")
    box.succeed("printf '%s\\n' '${canaryBytes}' > ${outside}/canary.txt")
    box.succeed("chmod 0600 ${outside}/canary.txt")
    box.succeed("ln -sfn ${outside} ${safe}/dir")

    out = broker_call("${safe}/dir/canary.txt")
    print("escape leg output: " + out)

    # The canary check comes FIRST and is the assertion that actually matters. A leaked byte is a
    # breach whatever the exit code says; checking rc first would let a "denied" verdict that still
    # echoed the content read as green.
    assert "${canaryBytes}" not in out, (
        "CANARY LEAKED THROUGH THE COMPOSED PATH. The broker's wrapper is not delivering the "
        "confinement to the impl — production is running caps unconfined. Output: " + out
    )
    assert '"ok": false' in out or '"ok":false' in out or "error" in out.lower(), (
        "symlinked-parent read through the real broker did not report a failure: " + out
    )
    print("composed 2 OK  (symlinked-parent escape CLOSED by confinement the WRAPPER supplied)")

    # ── 3. no transient units leak on the composed path ──────────────────────────────────────
    # The battery asserts this for the direct-dispatcher path; the wrapper path spawns the same
    # transient units, and a --collect regression here would accumulate units under the broker's
    # own invocations, which is where it would actually bite in production.
    # POSITIVE CONTROL FOR THE MATCHER, and it is not optional. `wc -l` always returns a number,
    # so this leg cannot go vacuously EMPTY -- but it can go vacuously ZERO, which is worse because
    # zero is the passing value. The glob `agent-os-cap-*` is a bare string literal spelled in THREE
    # unconnected places: bin/cap-invoke:221 (`"agent-os-cap-%d-%d" % ...`, the only thing that ever
    # creates these units), here, and tests/cap-sandbox-battery.sh:179. Nothing ties them together.
    # Rename the prefix in cap-invoke and BOTH leak checks match nothing, report 0, and stay green
    # forever while the leak they exist to catch runs unobserved. That is the whole inert-control
    # class: ask what this check would print if it were inert, and the answer was "the same thing."
    # So make it print something different. A decoy unit under the same glob must be VISIBLE here
    # before the count of zero is allowed to mean anything.
    def cap_units():
        return box.succeed(
            "systemctl list-units --all --no-legend 'agent-os-cap-*' 2>/dev/null | wc -l"
        ).strip()

    box.succeed(
        "systemd-run --unit=agent-os-cap-matcherprobe --property=RemainAfterExit=yes "
        "/bin/sh -c true"
    )
    seen = cap_units()
    assert seen != "0", (
        "the agent-os-cap-* matcher is INERT: a unit created under that exact prefix was invisible "
        "to it, so the leak assertion below has been passing on an empty match set, not on a clean "
        "one. Check the prefix in bin/cap-invoke against the glob here. Saw: " + seen
    )
    box.succeed("systemctl stop agent-os-cap-matcherprobe.service || true")
    box.succeed("systemctl reset-failed agent-os-cap-matcherprobe.service || true")

    leaked = cap_units()
    assert leaked == "0", leaked + " transient agent-os-cap-* unit(s) leaked on the composed path"
    print("composed 3 OK  (transient units collected; matcher armed -- saw " + seen + " decoy)")

    print("cap-composed-path: THE WIRING DELIVERS THE CONFINEMENT")
  '';
}
