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
  #   (b) filesystem confinement: ReadWritePaths/ReadOnlyPaths systemd enforcement is a
  #       separate Step-7 unit not built here. The mem.* impls self-enforce their hardcoded
  #       $MEM_ROOT, so fs-unconfined-at-systemd is defense-in-depth-missing, not fail-open.
  # Fable to rule whether A2 must also wire systemd fs-confinement / add the `spawns` gate.
  reg = (import ./capability-registry.nix { inherit lib; }).registry;

  # The caps whose impls are direct-exec'd through the seam in A2. Both the source file
  # AND the $out/bin filename are DERIVED from reg.<name>.impl, so the header's
  # "$out/bin filename == registry `impl` field" invariant holds structurally, and a
  # shipped cap whose impl file is missing fails the build (copy of a nonexistent path
  # throws). To ship a new cap: declare it in the registry, drop its impl at bin/<impl>,
  # add its name here.
  shippedCaps = [ "capabilities.list" "mem.recall" "mem.remember" ];
  offenders   = lib.filter (name: reg.${name}.sandbox.network) shippedCaps;

  wrapper = pkgs.writeShellScriptBin "cap-invoke" ''
    # PATH the impls see is PINNED here to a store-only, minimal set — NOT inherited from the
    # broker's (or any parent's) ambient PATH. cap-invoke builds the impl env from
    # AGENT_OS_CAP_PATH (defaulting to EMPTY when unset), so this export is the ONE authority on
    # what PATH an impl gets. Impls are absolute-shebang'd (patchShebangs, below) and currently
    # spawn-free, so this is inert defense-in-depth today; it exists so the FIRST impl that spawns
    # a helper by bare name resolves it from the store, never from an attacker-controlled dir.
    export AGENT_OS_CAP_PATH=${pkgs.coreutils}/bin
    exec ${pkgs.python3}/bin/python3 ${../bin/cap-invoke} "$@"
  '';

  cpLines = lib.concatMapStrings (name:
    let impl = reg.${name}.impl; in ''
      cp ${(../bin) + ("/" + impl)} $out/bin/${impl}
      chmod +x $out/bin/${impl}
    '') shippedCaps;

  capBinDir =
    assert lib.assertMsg (offenders == [ ])
      "cap-invoke-pkg: shipped cap(s) [${lib.concatStringsSep " " offenders}] declare sandbox.network=true, but capBinDir direct-execs impls with NO systemd net-confinement (PrivateNetwork/IPAddressDeny) built yet — a network-capable cap must not reach the seam until Step-7 cgroup confinement lands. Remove it from shippedCaps or build the confinement first.";
    pkgs.runCommand "agent-os-cap-bin" { nativeBuildInputs = [ pkgs.python3 ]; } ''
      mkdir -p $out/bin
      ${cpLines}
      patchShebangs $out/bin
    '';
in
{
  inherit wrapper capBinDir;
}
