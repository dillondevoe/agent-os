# tests/cap-sandbox-confinement.nix — WP-S2 / GATE #5(a), the ENFORCED half.
#
# tests/cap-sandbox-battery.sh is the behavioural evidence that the per-cap systemd fs-confinement
# actually stops the escape. It needs root and a live SYSTEM systemd, so it cannot be a check
# derivation — and for one commit it was therefore a test NOTHING SCHEDULED EVER RAN. This repo has
# already written down what that costs, in .github/workflows/vm-tests.yml:
#
#     "A regression test that does not execute does not prevent the regression; it documents that
#      someone once could have caught it."
#
# So this file gives the battery the one environment it needs: a booted NixOS VM, where root, PID 1,
# D-Bus and a real /var all exist. The test does not re-implement the assertions — re-stating them
# in a testScript would create two definitions of "confined" that can drift, and the shell version
# is the one a human runs at a box. It runs the SAME script, unmodified, and inherits every leg.
#
# WHAT THIS ADDS OVER THE DVo RUN recorded in the WP-S2 PR:
#   * It is SCHEDULED (vm-tests.yml matrix), so the confinement cannot be silently weakened later.
#   * It runs on the REAL target platform. The DVo run was WSL2 Ubuntu with distro systemd and a
#     distro python; here the impls are the patchShebangs'd store binaries, the interpreter is the
#     store python, /nix/store is the only runtime bound in, and systemd is whatever nixpkgs pins.
#     That last part is load-bearing rather than incidental: the ProtectSystem=strict finding in
#     modules/cap-sandbox.nix was measured on systemd 255.4, and a property that holds on exactly
#     one systemd version is a coincidence until a second version agrees. This is the second: the
#     first green run of this test, 2026-08-15, booted systemd 261 and all seven legs held.
#   * It boots `baseModules`, so /var/lib/agent-os/{safe-read,workspace} come from broker.nix's own
#     tmpfiles rules rather than from the battery's mkdir. A tmpfiles regression that removed those
#     roots would leave the battery green at a dev box (it creates them itself) and take the seam
#     down in production; here the VM's roots are the production ones.
#
# WHAT IT STILL DOES NOT COVER, stated rather than implied: this drives the DISPATCHER directly,
# with the store pins supplied as env — the same affordance the battery uses at a box. It therefore
# says nothing about the broker's own wrapper handing the dispatcher those pins. That link is
# covered from the other side by `checks.seam-live` (broker -> seam, unconfined) and by the build
# gate in modules/cap-invoke-pkg.nix, which fails if a shipped cap has no derived confinement. The
# fully-composed path — real broker, real wrapper, confined, in a VM — is not yet asserted anywhere,
# and that is the next increment after this one.
{ pkgs, baseModules }:

let
  capInvoke = import ../modules/cap-invoke-pkg.nix { inherit pkgs; };
  capSandbox = import ../modules/cap-sandbox.nix { lib = pkgs.lib; };
  policyJson = pkgs.writeText "agent-os-cap-sandbox.json" capSandbox.policyJson;
  registryJson = pkgs.writeText "agent-os-registry.json"
    (builtins.toJSON (import ../modules/capability-registry.nix { lib = pkgs.lib; }).registry);
  battery = ../tests/cap-sandbox-battery.sh;
in
pkgs.testers.runNixOSTest {
  name = "agentos-cap-sandbox-confinement";

  nodes.box = { ... }: {
    imports = baseModules;
    # The battery is bash, and it shells out to systemctl/systemd-run and a python3 for its JSON
    # helpers. Everything the confined IMPLS need is bound in by the policy itself (/nix/store);
    # these are for the harness that drives them, which runs unconfined.
    environment.systemPackages = with pkgs; [ bash coreutils python3 systemd ];
  };

  testScript = ''
    box.wait_for_unit("multi-user.target")

    # The roots must come from broker.nix's tmpfiles, NOT from the battery's own mkdir -p. Asserted
    # BEFORE the battery runs, because the battery would create them and mask their absence — and a
    # missing bind source is exactly the condition that turns every file cap into a hard deny in
    # production. (`test -d` on each, so the failure names which one.)
    box.succeed("test -d /var/lib/agent-os/safe-read")
    box.succeed("test -d /var/lib/agent-os/workspace")

    # Sanity the environment the battery ASSUMES, so a red leg means "the confinement moved", not
    # "the VM was not ready". is-system-running may legitimately report `degraded` here (the sealed
    # variants leave units masked), which the battery accepts too.
    # `is-system-running` exits 1 on `degraded`, so its status must NOT be the assertion — the
    # STRING is. (It is also why this cannot be a pipeline: succeed() runs under pipefail, so
    # `is-system-running | grep` fails on the producer's exit code even when grep matches. That
    # bit once, at first run.)
    box.succeed(
        "state=$(systemctl is-system-running --wait || true); echo \"systemd state: $state\"; "
        "case \"$state\" in running|degraded) exit 0 ;; *) exit 1 ;; esac"
    )

    # Record the systemd version in the log. The ProtectSystem=strict composition failure documented
    # in modules/cap-sandbox.nix is version-sensitive by nature, so when this test goes red after a
    # nixpkgs bump, this line is the first thing worth reading.
    print(box.succeed("systemctl --version | head -1"))

    # The battery IS the assertion set. `-L`/print-build-logs in the slow lane streams this, and the
    # per-leg "cap-sandbox N OK" lines are the report — each leg names the property it proves.
    out = box.succeed(
        "${pkgs.bash}/bin/bash ${battery} "
        + "${../bin/cap-invoke} "
        + "${capInvoke.capBinDir}/bin "
        + "${registryJson} "
        + "${policyJson} "
        + "${pkgs.systemd}/bin/systemd-run 2>&1"
    )
    print(out)

    # Belt-and-braces on the harness itself: the battery exits non-zero on any failed leg, so
    # succeed() above is the real gate. This catches the one thing it cannot — a future edit that
    # deletes legs and still exits 0. If you add a leg, add it here.
    for leg in ["cap-sandbox 0 OK", "cap-sandbox 1 OK", "cap-sandbox 2 OK", "cap-sandbox 3 OK",
                "cap-sandbox 4 OK", "cap-sandbox 5 OK", "cap-sandbox 6 OK",
                "cap-sandbox 7 OK",
                "cap-sandbox: ALL PROPERTIES HOLD"]:
        assert leg in out, f"battery exited 0 but never reported {leg!r} — legs were removed"
  '';
}
