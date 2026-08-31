{ config, lib, pkgs, ... }:
let
  agos-user-drift = pkgs.writeShellApplication {
    name = "agos-user-drift";
    runtimeInputs = [ pkgs.python3 ];
    # NOT `-e`: the wrapper must pass the scanner's THREE-VALUED exit code through
    # untouched. 1 (drift) and 2 (cannot-assess) are both non-zero and mean different
    # things; a wrapper that aborted on the first would collapse them.
    bashOptions = [ "nounset" "pipefail" ];
    text = ''
      exec python3 ${./agos-user-drift/agos-user-drift.py} "$@"
    '';
  };
in {
  environment.systemPackages = [ agos-user-drift ];

  systemd.services.agos-user-drift = {
    description = "Agent OS — scan for users, groups and memberships no declared state grants";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${agos-user-drift}/bin/agos-user-drift";
      StateDirectory = "agos-user-drift";
      User = "root";
      # Deliberately empty: EVERY non-zero code must land the unit in `failed`, because
      # `systemctl is-failed` is the whole of the alarm surface. Listing 1 or 2 here as
      # "success" would make a drifted box look identical to a clean one.
      SuccessExitStatus = [ ];
    };
  };

  systemd.timers.agos-user-drift = {
    description = "Agent OS — periodic user/group drift scan";
    wantedBy = [ "timers.target" ];
    timerConfig = { OnBootSec = "6min"; OnUnitActiveSec = "1d"; Persistent = true; };
  };
}
