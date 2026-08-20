# modules/selfimprove-open.nix — install the orchestration engine and RUN the loop.
#
# ═══ WHY THIS FILE EXISTS ═══
# Every `agos_*` module — events, subagents, advisor, lcm, observe, propose, cycle,
# surface — existed only as a CI FIXTURE. Each is referenced exactly once in flake.nix,
# inside a `runCommand` test gate that copies it into a temp dir, runs its battery and
# throws the copy away. Nothing installed any of them. Slices 3-6 of the HARNESS-MAP
# build order were merged, gated, green, and ABSENT FROM EVERY RUNNING MACHINE.
#
# The give-away phrasing was "Dell deploy-verify pending", which implies something is on
# the box waiting to be verified. Nothing was. A green battery proves a module WORKS; it
# says nothing about whether the module is INSTALLED, and those two facts had never been
# checked by the same thing.
#
# So this module does the two jobs no battery could:
#   (1) puts the engine on the system, and
#   (2) invokes it on a timer, because a package nothing runs is the same defect one step
#       later — which is precisely how the cycle runner sat un-called for two days.
#
# It is also registered in flake.nix's `agentos-open-imports` guard, so an unimported
# selfimprove-open.nix FAILS THE BUILD. That guard is the repo's existing answer to
# exactly this class of bug and it is the reason this file cannot silently detach again.
#
# ═══ WHAT IT DOES NOT DO ═══
# APPLY. `agos-selfimprove` observes, compares, proposes, refuses and writes a digest a
# human reads at /var/lib/agos-selfimprove/digest.md. It changes no file it proposed
# against; agos_surface.emit() enforces that boundary in code, not by this unit being
# careful. Whether APPLY ever auto-merges is Q1, open with Dillon. Until Q1 is answered
# this unit is deliberately the whole loop.
#
# ═══ CI BLIND SPOT (stated, per the pattern in brain.nix) ═══
# `nix flake check` EVALUATES this module. Eval-green means the unit text is well-formed
# and the package builds — NOT that the timer fires or the digest lands. That needs a real
# machine, and every Agent OS box has been unreachable this week. This file makes the loop
# installed-and-scheduled; it does not make it OBSERVED RUNNING. Do not upgrade that claim
# without a boot.
{ config, lib, pkgs, ... }:

let
  stateDir = "/var/lib/agos-selfimprove";

  # The engine, as an importable package. Kept as a directory rather than flattened into
  # bin/ because the modules import each other by name (agos_cycle imports agos_observe,
  # agos_propose, agos_surface) — a flat bin/ would break those imports in a way no test
  # would catch, since the batteries copy the modules into one directory themselves.
  agos-engine = pkgs.runCommand "agos-engine" { } ''
    mkdir -p "$out/lib/agos"
    cp ${../modules/agos_events.py}    "$out/lib/agos/agos_events.py"
    cp ${../modules/agos_observe.py}   "$out/lib/agos/agos_observe.py"
    cp ${../modules/agos_propose.py}   "$out/lib/agos/agos_propose.py"
    cp ${../modules/agos_surface.py}   "$out/lib/agos/agos_surface.py"
    cp ${../modules/agos_cycle.py}     "$out/lib/agos/agos_cycle.py"
    cp ${../modules/agos_lcm.py}       "$out/lib/agos/agos_lcm.py"
    cp ${../modules/agos_advisor.py}   "$out/lib/agos/agos_advisor.py"
    cp ${../modules/agos_subagents.py} "$out/lib/agos/agos_subagents.py"
  '';

  # Stdlib only, by construction — every agos_* module is stdlib-only on purpose, and if
  # that ever stops being true this bare python3 is what fails loudly and immediately.
  agos-selfimprove = pkgs.writeShellApplication {
    name = "agos-selfimprove";
    runtimeInputs = [ pkgs.python3 ];
    text = ''
      set -euo pipefail
      export PYTHONPATH="${agos-engine}/lib/agos''${PYTHONPATH:+:$PYTHONPATH}"
      exec python3 -c 'import agos_cycle, sys; sys.exit(agos_cycle.main(sys.argv[1:]))' \
        "${stateDir}/lessons.db" "${stateDir}/proposals.db" "${stateDir}/digest.md"
    '';
  };
in {
  environment.systemPackages = [ agos-selfimprove ];

  systemd.tmpfiles.rules = [ "d ${stateDir} 0755 root root -" ];

  systemd.services.agos-selfimprove = {
    description = "Agent OS — self-improvement loop: observe, compare, propose, surface (no APPLY)";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${agos-selfimprove}/bin/agos-selfimprove";
      # rc=1 means "could not surface", which agos_cycle.run() reports rather than hides.
      # It is a real failure and systemd should record it as one — a loop that cannot
      # publish its findings is not a loop that found nothing.
      StateDirectory = "agos-selfimprove";
    };
  };

  systemd.timers.agos-selfimprove = {
    description = "Agent OS — run the self-improvement loop periodically";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "10m";
      OnUnitActiveSec = "6h";
      # The loop is idempotent by construction (content-hash dedup on occurrences, INSERT
      # OR IGNORE on signals), so a missed run costs nothing and a doubled run costs
      # nothing. Cadence is therefore chosen for signal, not safety: hourly would surface
      # the same digest six times and train whoever reads it to stop reading it.
      Persistent = true;
    };
  };
}
