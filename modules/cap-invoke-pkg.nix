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
  wrapper = pkgs.writeShellScriptBin "cap-invoke" ''
    exec ${pkgs.python3}/bin/python3 ${../bin/cap-invoke} "$@"
  '';

  capBinDir = pkgs.runCommand "agent-os-cap-bin" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    mkdir -p $out/bin
    cp ${../bin/cap-capabilities-list} $out/bin/cap-capabilities-list
    chmod +x $out/bin/cap-capabilities-list
    patchShebangs $out/bin
  '';
in
{
  inherit wrapper capBinDir;
}
