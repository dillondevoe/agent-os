# modules/setup-window.nix — the setup window (docs/design/first-login-and-setup-window.md §2, slice 3a).
# Options only, and INERT, unless `agentos.cleanRoom.setupWindow.enable = true`.
#
# Today the box ships with DNS + 80/443 open to EVERY uid until a human rebuilds it sealed. With the
# window on, the loaded ruleset is always the sealed one; setup is one uid (the model fetcher's) in one
# named set for a budget of open seconds, and the box closes it by itself. modules/clean-room.nix
# renders the set and its two rules; modules/agos_window.py is the only thing that writes the set.
#
# THIS SLICE is the mechanism: the set, the fetcher's fixed uid, the state machine, the per-boot /
# per-reload re-derivation, the once-a-minute budget tick that closes and seals. NOT in this slice
# (3b): the agent's session waiting for the seal, the "model is in, seal now" path, the tty1 progress
# line, and the seal-check assertions. Until 3b lands the option must stay off on a real box: with it
# on, the agent's uid still gets no accept, but the agent can run while the fetcher has egress.
#
# SECURITY SURFACE (egress wall): branch -> PR -> Fable, never direct-push, never self-merge.
{ config, pkgs, lib, ... }:

let
  cr = config.agentos.cleanRoom;
  cfg = cr.setupWindow;
  table = "inet agentos_egress";

  # One transaction each: the set is flushed and (for "open") refilled in the same `nft -f`, so it is
  # never anything but empty or exactly { fetch uid }. Rendered here, never built in a shell.
  openFile = pkgs.writeText "agentos-setup-window-open.nft" ''
    flush set ${table} setup_uid
    add element ${table} setup_uid { ${toString cfg.fetchUid} }
  '';
  closeFile = pkgs.writeText "agentos-setup-window-close.nft" ''
    flush set ${table} setup_uid
  '';
  # What the `sealed` marker's hash names: the rendered table.
  rulesetFile = pkgs.writeText "agentos-egress-table.nft" config.networking.nftables.tables.agentos_egress.content;

  windowConfig = {
    state_dir = "/var/lib/agent-os/seal";
    length = cfg.seconds;
    build_epoch = cfg.buildEpoch;
    fetch_uid = cfg.fetchUid;
    nft = "${pkgs.nftables}/bin/nft";
    systemctl = "${config.systemd.package}/bin/systemctl";
    open_file = "${openFile}";
    close_file = "${closeFile}";
    ruleset_file = "${rulesetFile}";
    restart_units = [ "ollama.service" ];                 # the fetcher: ends a download in flight
    stop_units = [ "agent-os-pull-model.timer" "agent-os-pull-model.service" ];   # stopped now; conditioned below
    sealed_by_build = cr.sealed;
  };

  agos-window = pkgs.runCommand "agos-window" { nativeBuildInputs = [ pkgs.makeBinaryWrapper ]; } ''
    install -Dm755 ${./agos_window.py} $out/libexec/agos_window.py
    substituteInPlace $out/libexec/agos_window.py \
      --replace-fail '"@AGOS_WINDOW_CONFIG@"' ${lib.escapeShellArg (builtins.toJSON (builtins.toJSON windowConfig))}
    makeBinaryWrapper ${pkgs.python3}/bin/python3 $out/libexec/agentos-setup-window \
      --add-flags "-I $out/libexec/agos_window.py"
  '';
  run = "${agos-window}/libexec/agentos-setup-window";

  sandbox = {
    PrivateTmp = true;
    ProtectHome = true;
    ProtectSystem = "strict";
    ReadWritePaths = [ "/var/lib/agent-os/seal" ];
    NoNewPrivileges = true;
    ProtectKernelModules = true;
    ProtectControlGroups = true;
    RestrictAddressFamilies = [ "AF_UNIX" "AF_NETLINK" ];   # nft speaks netlink; systemctl a unix socket
    CapabilityBoundingSet = [ "CAP_NET_ADMIN" ];           # nft. Everything else is root on root's own files.
    SystemCallArchitectures = "native";
  };
in
{
  options.agentos.cleanRoom.setupWindow = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Replace the build-time provisioning allowance (DNS + 80/443 for every uid until a rebuild) with
        the setup window: the sealed ruleset always, plus DNS + 443 for the model fetcher's uid only,
        for `seconds` of open time, closed and sealed by the box itself.
      '';
    };
    seconds = lib.mkOption {
      type = lib.types.ints.between 120 14400;
      default = 7200;
      description = "The window's budget of OPEN seconds (not a wall-clock deadline). At most 4 hours.";
    };
    fetchUid = lib.mkOption {
      type = lib.types.ints.between 328 399;
      default = 351;
      description = ''
        STATIC uid of the model fetcher (the ollama daemon: `ollama pull` only asks it to). The set
        holds a NUMBER, and nixpkgs' ollama module allocates its user's uid at install, so the uid is
        pinned here. 328..399 is the gap between nixpkgs' highest static id and its auto-allocation
        range, the same gap fetch-proxy's 350 lives in.
      '';
    };
    buildEpoch = lib.mkOption {
      type = lib.types.ints.positive;
      default = 1767225600;       # 2026-01-01; flake.nix sets the repository's own commit time
      description = "A clock earlier than this (epoch seconds) is treated as unknown (no RTC battery).";
    };
  };

  config = lib.mkIf (cr.enable && cfg.enable) {
    assertions = [
      {
        assertion = config.services.ollama.user == "ollama" && config.users.users.ollama.uid == cfg.fetchUid;
        message = "setup-window: the ollama daemon must run as the static user `ollama` with uid agentos.cleanRoom.setupWindow.fetchUid.";
      }
      {
        assertion = lib.length (lib.filter (u: (u.uid or null) == cfg.fetchUid) (lib.attrValues config.users.users)) == 1
                    && !(config.agentos.fetchProxy.enable or false && config.agentos.fetchProxy.uid == cfg.fetchUid);
        message = "setup-window: fetchUid ${toString cfg.fetchUid} is claimed by more than one user.";
      }
    ];

    # The fetcher's uid, fixed. (nixpkgs' module would create the user with an auto-allocated uid.)
    services.ollama.user = "ollama";
    services.ollama.group = "ollama";
    users.users.ollama = { uid = cfg.fetchUid; isSystemUser = true; group = "ollama"; };
    users.groups.ollama.gid = cfg.fetchUid;

    system.build.agosWindow = agos-window;
    system.build.agosWindowConfig = windowConfig;
    system.build.agosWindowOpenFile = openFile;

    # Once the box has sealed, nothing may go on asking the fetcher to pull: the first-boot pull and
    # its retry timer refuse to start while the marker exists. A condition, not a mask: a runtime mask
    # is gone after a reboot, and the timer would be back.
    systemd.services.agent-os-pull-model.unitConfig.ConditionPathExists = "!/var/lib/agent-os/seal/sealed";
    systemd.timers.agent-os-pull-model.unitConfig.ConditionPathExists = "!/var/lib/agent-os/seal/sealed";

    systemd.tmpfiles.rules = [
      "d /var/lib/agent-os 0755 root root - -"
      "d /var/lib/agent-os/seal 0700 root root - -"
    ];

    # Sealed-by-build (`cleanRoom.sealed = true`): the window never opens on this box. The units do
    # not exist to be started; the program itself also refuses (sealed_by_build is compiled in).
    systemd.services.agentos-setup-window = lib.mkIf (!cr.sealed) {
      description = "Agent OS setup window: re-derive the egress set from its state";
      wantedBy = [ "multi-user.target" ];
      # nftables recreates the table (and so the set, EMPTY) on every start and reload. This unit is
      # the set's only writer, so it follows both: PartOf re-runs it on a restart,
      # ReloadPropagatedFrom on a reload (which is what a rebuild that changes the ruleset does).
      after = [ "nftables.service" "systemd-tmpfiles-setup.service" ];
      partOf = [ "nftables.service" ];
      unitConfig = {
        ReloadPropagatedFrom = [ "nftables.service" ];
        RequiresMountsFor = [ "/var/lib/agent-os" ];
        ConditionPathIsReadWrite = "/var/lib/agent-os/seal";     # no books, no window
      };
      serviceConfig = sandbox // {
        Type = "oneshot";
        RemainAfterExit = true;            # so ExecReload exists and ExecStop runs at shutdown
        ExecStart = "${run} recompute";
        ExecReload = "${run} recompute";
        ExecStop = "${run} stop";          # the shutdown stamp of the budget
      };
    };

    # The budget's clock, and its enforcer: when the budget is spent the tick flushes the set itself and
    # then seals. NOT conditioned on the state directory being writable: a tick that cannot write
    # flushes the set and fails (an enforcer that cannot keep its books closes the window).
    systemd.services.agentos-setup-window-tick = lib.mkIf (!cr.sealed) {
      description = "Agent OS setup window: budget tick";
      after = [ "agentos-setup-window.service" ];
      unitConfig.RequiresMountsFor = [ "/var/lib/agent-os" ];
      serviceConfig = sandbox // {
        Type = "oneshot";
        ExecStart = "${run} tick";
      };
    };
    systemd.timers.agentos-setup-window-tick = lib.mkIf (!cr.sealed) {
      description = "Agent OS setup window: budget tick, once a minute";
      wantedBy = [ "timers.target" ];
      timerConfig = { OnActiveSec = "1min"; OnUnitActiveSec = "1min"; AccuracySec = "5s"; };
    };
  };
}
