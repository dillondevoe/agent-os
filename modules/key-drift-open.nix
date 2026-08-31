# agos-key-drift — a standing answer to "is declared state the whole of SSH access?"
#
# THE INCIDENT. 2026-08-31: the Dell was carrying a hand-written
# /root/.ssh/authorized_keys holding an undeclared ed25519 key (mtime Aug 16). sshd reads
# BOTH %h/.ssh/authorized_keys and /etc/ssh/authorized_keys.d/%u; only the second is
# Nix-managed. So every gate we had was green — the module was faithful, the build was
# reproducible, the switch succeeded — while root access on the running box was wider than
# anything anyone had declared. The key was removed the same day; this module exists so the
# NEXT one is found by the machine instead of by a brain that happened to go looking.
#
# WHY A UNIT AND NOT A FLAKE CHECK. This is a property of a RUNNING MACHINE. Nothing about
# an evaluation can see a file someone typed into a console last month. `nix flake check`
# gets the battery (does the scanner discriminate?); the box gets the scan (is THIS box
# clean?). Those are different questions and neither substitutes for the other.
#
# IT DOES NOT REPAIR. Deleting a key you do not understand is how you lose the only way
# back into a box at the moment you most need it — the 2026-08-11 outage is in this tree's
# comments for exactly that reason. It reports; a person or a brain decides.
{ config, lib, pkgs, ... }:
let
  agos-key-drift = pkgs.writeShellApplication {
    name = "agos-key-drift";
    runtimeInputs = with pkgs; [ coreutils gnugrep gnused gawk openssh ];
    # NOT `-e`: this script's whole output is its exit code, and it distinguishes
    # 1 (drift) from 2 (could not assess). `set -e` would abort mid-scan on the first
    # non-zero grep and report a PARTIAL scan under whatever code happened to escape.
    bashOptions = [ "nounset" "pipefail" ];
    text = builtins.readFile ./agos-key-drift/agos-key-drift.sh;
  };
in {
  environment.systemPackages = [ agos-key-drift ];

  systemd.services.agos-key-drift = {
    description = "Agent OS — scan for SSH keys with access that no declared state grants";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${agos-key-drift}/bin/agos-key-drift";
      StateDirectory = "agos-key-drift";
      # Reads /root/.ssh — root is required and is not incidental.
      User = "root";
      # A FAILED unit is the whole alarm surface: `systemctl is-failed agos-key-drift`
      # answers the question over ssh in one line, with no report parsing. Exit 1 (drift)
      # and exit 2 (cannot assess) both land there, deliberately — an incomplete scan is
      # not a clean one, and collapsing them would recreate the bug this module is about.
      SuccessExitStatus = [ ];
    };
  };

  systemd.timers.agos-key-drift = {
    description = "Agent OS — periodic SSH key drift scan";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      # Drift is introduced by a human at a console, so the cadence that matters is
      # "since someone last touched it", not seconds. Daily plus every boot.
      OnBootSec = "5min";
      OnUnitActiveSec = "1d";
      Persistent = true;
    };
  };
}
