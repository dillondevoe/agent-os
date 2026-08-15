# Phase 2 · Step 7 (go-live) — the shared `cap-invoke` seam wrapper + pinned cap-impl bin dir.
#
# bin/cap-invoke is the AGENT_OS_INVOKE_SEAM DISPATCHER: the broker (Step 5) names it once via
# AGENT_OS_INVOKE_SEAM and reaches EVERY capability impl through it. Going live means broker.nix
# must pin BOTH halves to store paths, the same discipline audit/taint/confirm already use — so no
# stray env in the broker's environment can redirect the seam or its impl-resolution:
#
#   * wrapper   — a writeShellScriptBin that execs bin/cap-invoke under a store-pinned python3.
#                 The shell execs the interpreter EXPLICITLY, bypassing cap-invoke's own
#                 `#!/usr/bin/env python3` source shebang (identical to the taint/audit/confirm
#                 wrappers). broker.nix pins AGENT_OS_INVOKE_SEAM to `${wrapper}/bin/cap-invoke`.
#   * capBinDir — the directory cap-invoke resolves AGENT_OS_CAP_BIN_DIR to. cap-invoke DIRECT-EXECs
#                 each impl by absolute path (subprocess.run([impl_path]) — NEVER via PATH), so each
#                 impl's OWN shebang must resolve without /usr/bin/env. patchShebangs rewrites
#                 `#!/usr/bin/env python3` -> the absolute store-path python3. This is gate #3's
#                 purity fix AND the production-real form of the sandbox-ENOEXEC trap the battery
#                 models with its shebang-pin loop (the nix build sandbox has NO /usr/bin/env; here
#                 nix makes the pin real instead of test-only). The impl FILENAME in $out/bin MUST
#                 equal the registry `impl` field ("cap-capabilities-list") — cap-invoke execs
#                 `<AGENT_OS_CAP_BIN_DIR>/<impl_name>`.
#
# Importing this file twice with the same `pkgs` yields the same derivations (Nix deduplicates),
# so there is no collision — same lesson/shape as modules/taint-pkg.nix / modules/confirm-pkg.nix.
#
# Security surface (the injection contract goes LIVE here): branch -> PR -> Fable(code) -> merge.
# Never direct-push.
{ pkgs }:

let
  lib = pkgs.lib;

  # ── Build-time confinement gate (Fable mechanism-3, Step-7 obligation — PARTIAL) ──
  # cap-invoke DIRECT-EXECs each impl in capBinDir (subprocess.run([abs_path]) — the
  # untrusted-model -> broker -> seam -> impl path goes LIVE here). Fable's requirement:
  # an impl with a dangerous sandbox must not be reachable through the seam until its
  # systemd confinement (cgroup) is built. The ONE danger the REGISTRY can prove at eval
  # time is `sandbox.network` — a network-capable (T2) cap needs PrivateNetwork/
  # IPAddressDeny net-confinement that Step-7 has NOT built yet, so shipping one into
  # capBinDir would hand the model un-confined egress. So: FAIL THE BUILD if any cap we
  # ship declares network=true. Reading `reg` also forces the registry's own mechanism-3
  # asserts (its output gates rawRegistry behind `assert ok`), so capBinDir cannot build
  # on an invalid registry either.
  #
  # SCOPE — two dangers this predicate does NOT cover (FLAGGED FOR FABLE AT PR, not
  # silently assumed safe):
  #   (a) child-spawn: the registry has NO `spawns` field, so "impl forks a subprocess"
  #       is not eval-detectable here. The 3 shipped impls are spawn-free by inspection;
  #       the FIRST capability that spawns must land killpg/start_new_session AND ideally
  #       a `spawns` schema field so this gate can catch it. Documented, not enforced.
  #   (b) filesystem confinement: BUILT as of WP-S2 — see modules/cap-sandbox.nix and the
  #       `unconfined` gate below. It is no longer a deferral. (It arrived because file.read
  #       made it fail-OPEN rather than merely defence-in-depth-missing: unlike mem.*, which
  #       take a namespace and are confined by construction, file.read takes a caller-supplied
  #       path and delegates symlink-escape to this layer.) The `spawns` schema field is still
  #       not added — but the sandbox now runs every impl in its own transient-unit cgroup, so a
  #       spawning impl's descendants are reaped with the unit rather than orphaned.
  reg = (import ./capability-registry.nix { inherit lib; }).registry;

  # The caps whose impls are direct-exec'd through the seam in A2. Both the source file
  # AND the $out/bin filename are DERIVED from reg.<name>.impl, so the header's
  # "$out/bin filename == registry `impl` field" invariant holds structurally, and a
  # shipped cap whose impl file is missing fails the build (copy of a nonexistent path
  # throws). To ship a new cap: declare it in the registry, drop its impl at bin/<impl>,
  # add its name here.
  #
  # WP-S2 (GATE #5(a)): `file.read` and `file.write` join HERE, in the SAME change that builds
  # their systemd fs-confinement (modules/cap-sandbox.nix, pinned into the wrapper below) — never
  # before it, never separately. Geist's 2026-08-14 RULING 1 is the governing decision: the file
  # caps take a caller-supplied path, `file.read` has ZERO impl-layer symlink defense by design,
  # and until the mount-namespace confinement exists a planted symlink under SAFE_ROOT is an
  # arbitrary-file read. That is fail-OPEN, which is what GATE #5 forbids wiring in. With the
  # confinement built, the escape is closed by the kernel — see tests/cap-sandbox-battery.sh,
  # which demonstrates it on real systemd with a negative control first.
  #
  # MEASURED refinement of the ruling, carried here because it changes what to test, not whether:
  # the open hole is the symlinked PARENT (`safe-read/dir -> /etc`, then `safe-read/dir/shadow`),
  # not a symlink at the final component — cap-file-read's own lstat DOES reject the latter. That
  # makes file.read's residual escape the SAME shape as file.write's target-only-lstat gap, which
  # is why one mount namespace closes both.
  shippedCaps = [ "capabilities.list" "mem.recall" "mem.remember" "file.read" "file.write" ];
  offenders   = lib.filter (name: reg.${name}.sandbox.network) shippedCaps;

  # The registry-DERIVED confinement policy, materialized. Reading it forces the registry asserts
  # a second time and, more importantly, makes the policy a store path the wrapper can pin — so
  # no ambient env can point the dispatcher at a weaker policy.
  capSandbox = import ./cap-sandbox.nix { inherit lib; };
  sandboxPolicy = pkgs.writeText "agent-os-cap-sandbox.json" capSandbox.policyJson;

  # Every shipped cap MUST have a derived confinement entry with an actual fs boundary. The
  # dispatcher already denies a cap missing from the policy at runtime; this makes the same
  # condition a BUILD failure, so the fail-closed path is a backstop rather than the plan.
  unconfined = lib.filter
    (name: !(lib.elem "TemporaryFileSystem=/:ro" (capSandbox.policy.${name} or [ ])))
    shippedCaps;

  # `mkWrapper confined` — ONE definition, two builds. The production `wrapper` is confined; the
  # `unconfinedWrapper` exists for exactly one consumer, `checks.seam-live`, and is never
  # referenced by broker.nix or any NixOS module.
  #
  # WHY IT HAS TO EXIST, stated plainly rather than hidden: the confined wrapper cannot run inside
  # a nix build sandbox at all. There is no D-Bus and no PID 1 there, so systemd-run cannot create
  # a transient unit, and cap-invoke then DENIES — which is the correct production behaviour and
  # exactly what makes seam-live (a check derivation driving the real broker end-to-end) go red for
  # an infrastructure reason that has nothing to do with what it tests. seam-live's question is
  # "does the broker<->seam wiring return blessed, DATA-fenced content without spuriously tainting
  # the session"; the confinement's question is "does the kernel stop the escape". Splitting them
  # keeps each answerable. The confined path's evidence is tests/cap-sandbox-battery.sh on real
  # systemd — NOT this check, and the two must not be read as covering each other.
  #
  # The delta between the two builds is EXACTLY the two exports below and nothing else, so the
  # test wrapper cannot drift into testing a different dispatcher.
  mkWrapper = confined: pkgs.writeShellScriptBin "cap-invoke" ''
    # PATH the impls see is PINNED here to a store-only, minimal set — NOT inherited from the
    # broker's (or any parent's) ambient PATH. cap-invoke builds the impl env from
    # AGENT_OS_CAP_PATH (defaulting to EMPTY when unset), so this export is the ONE authority on
    # what PATH an impl gets. Impls are absolute-shebang'd (patchShebangs, below) and currently
    # spawn-free, so this is inert defense-in-depth today; it exists so the FIRST impl that spawns
    # a helper by bare name resolves it from the store, never from an attacker-controlled dir.
    export AGENT_OS_CAP_PATH=${pkgs.coreutils}/bin
    # GATE #5(a): the per-cap systemd fs-confinement, DERIVED from the registry sandbox decl, and
    # the launcher that applies it. Both are pinned HERE — in the seam's own wrapper, the same
    # discipline as AGENT_OS_CAP_PATH — so no ambient env can point the dispatcher at a weaker
    # policy or at a "systemd-run" of the caller's choosing. cap-invoke DENIES rather than running
    # an impl bare if either is missing; leaving them unset is a dev/battery affordance only.
    ${lib.optionalString confined ''
      export AGENT_OS_CAP_SANDBOX=${sandboxPolicy}
      export AGENT_OS_SYSTEMD_RUN=${pkgs.systemd}/bin/systemd-run
    ''}
    exec ${pkgs.python3}/bin/python3 ${../bin/cap-invoke} "$@"
  '';

  cpLines = lib.concatMapStrings (name:
    let impl = reg.${name}.impl; in ''
      cp ${(../bin) + ("/" + impl)} $out/bin/${impl}
      chmod +x $out/bin/${impl}
    '') shippedCaps;

  capBinDir =
    assert lib.assertMsg (unconfined == [ ])
      "cap-invoke-pkg: shipped cap(s) [${lib.concatStringsSep " " unconfined}] have no derived fs-confinement in modules/cap-sandbox.nix — a cap must not reach the seam before its per-cap systemd sandbox exists (GATE #5(a)).";
    assert lib.assertMsg (offenders == [ ])
      "cap-invoke-pkg: shipped cap(s) [${lib.concatStringsSep " " offenders}] declare sandbox.network=true, but capBinDir direct-execs impls with NO systemd net-confinement (PrivateNetwork/IPAddressDeny) built yet — a network-capable cap must not reach the seam until Step-7 cgroup confinement lands. Remove it from shippedCaps or build the confinement first.";
    pkgs.runCommand "agent-os-cap-bin" { nativeBuildInputs = [ pkgs.python3 ]; } ''
      mkdir -p $out/bin
      ${cpLines}
      patchShebangs $out/bin
    '';
  wrapper = mkWrapper true;
  unconfinedWrapper = mkWrapper false;
in
{
  inherit wrapper unconfinedWrapper capBinDir;
}
