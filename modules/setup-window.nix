# modules/setup-window.nix — the setup window (docs/design/first-login-and-setup-window.md §2, slice 3a).
# Options only, and INERT, unless `agentos.cleanRoom.setupWindow.enable = true`.
#
# Today the box ships with DNS + 80/443 open to EVERY uid until a human rebuilds it sealed. With the
# window on, the loaded ruleset is always the sealed one; setup is one uid (the model fetcher's) in one
# named set for a budget of open seconds, and the box closes it by itself. modules/clean-room.nix
# renders the set and its two rules; modules/agos_window.py is the only thing that writes the set.
#
# SLICE 3a is the mechanism: the set, the fetcher's fixed uid, the state machine, the per-boot /
# per-reload re-derivation, the once-a-minute budget tick that closes and seals.
#
# SLICE 3b, part 1 (spec 2.4): THE AGENT DOES NOT EXIST WHILE THE WINDOW IS OPEN. The fetcher is the
# model server; while it has egress, anything that can reach it on loopback can make it send chosen
# bytes to a chosen host. So nothing may start a process for the agent's account until the box is
# sealed. `agentos-seal.service` blocks until then (the model is in, or the budget is gone), and
# EVERY door to the agent's account waits for it:
#   1. tty1, where the agent autologs in: `getty@tty1` requires the seal unit and starts after it.
#   2. every other console agetty serves (the other virtual terminals logind spawns on a switch, a
#      serial console, a container console): nixpkgs bakes `--autologin agent` into ALL of them, so
#      their login program is a gate that execs `login` only when /run/agentos-setup/login-ok exists.
#      The break-glass console (tty3) has its own ExecStart and its own login program: it is root's,
#      and it is not gated.
#   3. the agent's user manager, which logind starts at BOOT, with no login at all, if lingering was
#      ever enabled for the account (an account may enable it for itself): `user@.service` gets a
#      start condition on the same token. uid 0 is exempt. This is where the agent's scheduled apps
#      live (systemd user timers), so it is the door that needs no human to open it.
# The token is positive: absent means closed. Only agos_window.py writes it, under the state lock,
# after a valid marker and a confirmed flush. And once it exists the window cannot open again in that
# boot, so removing the state re-provisions the box at the next boot, never under a running agent.
#
# NOT YET (3b, part 2): the seal-check assertions of spec 2.5, the audit-chain lines, moving the
# pull's registry probe off uid 0. Until that lands the option stays off on every shipped image (a
# flake assertion holds it there).
#
# SECURITY SURFACE (egress wall): branch -> PR -> Fable, never direct-push, never self-merge.
{ config, options, pkgs, lib, ... }:

let
  cr = config.agentos.cleanRoom;
  cfg = cr.setupWindow;
  table = "inet agentos_egress";
  runDir = "/run/agentos-setup";
  agentUser = config.services.getty.autologinUser;      # the account whose processes must not exist yet
  loginToken = "${runDir}/login-ok";

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
    run_dir = runDir;                                     # login-ok (the token) and status (the console's line)
    model = config.environment.variables.OLLAMA_MODEL;    # the one the detector looks for in /api/tags
    tags_port = 11434;
  };

  agos-progress = pkgs.runCommand "agos-setup-progress" { nativeBuildInputs = [ pkgs.makeBinaryWrapper ]; } ''
    install -Dm755 ${./agos_setup_progress.py} $out/libexec/agos_setup_progress.py
    substituteInPlace $out/libexec/agos_setup_progress.py --replace-fail '"@AGOS_PROGRESS_INSTALLED@"' '"yes"'
    makeBinaryWrapper ${pkgs.python3}/bin/python3 $out/libexec/agentos-setup-progress \
      --add-flags "-I $out/libexec/agos_setup_progress.py"
  '';

  # Door 2. agetty runs this as root in place of `login`, for every console it serves. The token is a
  # plain file in a root-only-writable tmpfs directory; absent, a symlink or anything else: no login.
  realLogin = options.services.getty.loginProgram.default;
  loginGate = pkgs.writeShellScript "agentos-login-gate" ''
    if [ -f ${loginToken} ] && [ ! -L ${loginToken} ]; then
      exec ${realLogin} "$@"
    fi
    printf '\r\n%s\r\n%s\r\n' \
      'Agent OS is still setting up this machine.' \
      'This console opens when setup has finished.'
    ${pkgs.coreutils}/bin/sleep 10
    exit 1
  '';

  # Door 3. ExecCondition of user@.service, run with full privileges BEFORE anything runs as the
  # user: exit 0 = start the manager, 1 = skip it (not a failure; logind starts it again at login).
  userGate = pkgs.writeShellScript "agentos-user-gate" ''
    [ "$1" = 0 ] && exit 0
    [ -f ${loginToken} ] && [ ! -L ${loginToken} ] && exit 0
    exit 1
  '';

  agos-window = pkgs.runCommand "agos-window" { nativeBuildInputs = [ pkgs.makeBinaryWrapper ]; } ''
    install -Dm755 ${./agos_window.py} $out/libexec/agos_window.py
    substituteInPlace $out/libexec/agos_window.py \
      --replace-fail '"@AGOS_WINDOW_CONFIG@"' ${lib.escapeShellArg (builtins.toJSON (builtins.toJSON windowConfig))}
    makeBinaryWrapper ${pkgs.python3}/bin/python3 $out/libexec/agentos-setup-window \
      --add-flags "-I $out/libexec/agos_window.py"
  '';
  run = "${agos-window}/libexec/agentos-setup-window";

  doorAssertions = [
    # THE DOORS THIS MODULE GATES ARE ALL THE DOORS THERE ARE. agetty's consoles and the user manager
    # wait for the token. Anything else that starts a process for the agent's account would run while
    # the window is open, so it may not be configured next to the window until it has a gate too.
    {
      assertion = cr.sealed || lib.all (n: (config.systemd.services.${n}.serviceConfig.User or null) != agentUser)
                                       (lib.attrNames config.systemd.services);
      message = "setup-window: a system unit runs as the agent's account (User=${agentUser}); it would run while the setup window is open.";
    }
    {
      # A list of the known ones, not a proof: a unit shipped through `systemd.packages`, or a root
      # unit that drops to the account itself (runuser, setpriv), cannot be seen from here. The
      # product path has neither; a module that adds one adds its gate.
      assertion = cr.sealed || !(config.services.openssh.enable || config.services.cron.enable || config.services.fcron.enable
                                 || config.services.atd.enable || config.services.tailscale.enable
                                 || config.services.displayManager.enable || config.services.greetd.enable);
      message = "setup-window: sshd, Tailscale (its ssh), cron, fcron, atd and display managers can start a session for the agent's account and are not gated on the seal; they cannot be enabled together with the setup window.";
    }
    # nixpkgs writes every drop-in as `overrides.conf`, and an instance drop-in of that name MASKS the
    # template's. So an instance unit of a getty template silently drops the login gate for that
    # console (and the getty's whole command line), and an instance of user@ drops the start
    # condition. The one kind allowed is a console with its own complete, non-autologin command line:
    # the break-glass console. (A disabled instance is a mask, and a masked console is closed.)
    {
      assertion = cr.sealed || (
        let
          names = lib.attrNames config.systemd.services;
          isInstanceOf = t: n: lib.hasPrefix t n && n != t;
          gettys = lib.filter (n: lib.any (t: isInstanceOf t n) [ "getty@" "autovt@" "serial-getty@" "container-getty@" ]) names;
          cmd = u: builtins.unsafeDiscardStringContext (lib.concatStringsSep " " (lib.toList (u.serviceConfig.ExecStart or "")));
          own = u: !u.enable || ((u.serviceConfig ? ExecStart) && lib.hasInfix "agetty" (cmd u) && !(lib.hasInfix "--autologin" (cmd u)));
        in lib.all (n: own config.systemd.services.${n}) gettys && !(lib.any (isInstanceOf "user@") names));
      message = "setup-window: an instance unit of a getty template or of user@ masks the template's drop-in (the login gate, the start condition). Only a disabled one, or a console with its own non-autologin agetty command line (break-glass), is allowed next to the setup window.";
    }
    {
      assertion = cr.sealed || agentUser != null;
      message = "setup-window: the gated account is the one agetty autologs in; services.getty.autologinUser must be set.";
    }
  ];

  sandbox = {
    PrivateTmp = true;
    ProtectHome = true;
    ProtectSystem = "strict";
    ReadWritePaths = [ "/var/lib/agent-os/seal" ];
    # The token and the console's status line. systemd makes the directory before every start of any
    # of the units below, root-owned, and never removes it (it is a tmpfs: a boot does).
    RuntimeDirectory = "agentos-setup";
    RuntimeDirectoryMode = "0755";
    RuntimeDirectoryPreserve = "yes";
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
    ] ++ doorAssertions;

    # The fetcher's uid, fixed. (nixpkgs' module would create the user with an auto-allocated uid.)
    services.ollama.user = "ollama";
    services.ollama.group = "ollama";
    users.users.ollama = { uid = cfg.fetchUid; isSystemUser = true; group = "ollama"; };
    users.groups.ollama.gid = cfg.fetchUid;

    system.build.agosWindow = agos-window;
    system.build.agosWindowConfig = windowConfig;
    system.build.agosWindowOpenFile = openFile;
    system.build.agosSetupProgress = agos-progress;
    system.build.agosLoginGate = loginGate;
    system.build.agosUserGate = userGate;
    system.build.agosWindowDoorAssertions = doorAssertions;

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
        # The stamp may wait for the state lock (150 s) behind a tick that is restarting the fetcher
        # (120 s). The default 90 s would let systemd kill it, and the next boot would inherit a
        # budget up to a minute too generous.
        TimeoutStopSec = "180s";
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

    # ── slice 3b, part 1: the agent does not exist while the window is open ─────────────────────

    # The wait. In every boot; on a sealed box it confirms the flush, writes the token and exits.
    # NOT RemainAfterExit: each start of something that Requires= it (a respawned getty) runs it
    # again, which is one `nft -f` and a file.
    systemd.services.agentos-seal = lib.mkIf (!cr.sealed) {
      description = "Agent OS setup: wait for the model, then seal (the agent's logins start after this)";
      wantedBy = [ "multi-user.target" ];
      # DOOR 1: tty1. Declared from THIS side (a `.requires/` link plus Before=), never as a drop-in on
      # `getty@tty1`: nixpkgs names every drop-in `overrides.conf`, and an instance drop-in of that name
      # MASKS the template's, which is where the getty's whole command line lives (autologin, and the
      # login gate). The first version of this slice did exactly that; tty1's getty fell back to
      # upstream's /usr/bin/agetty, which does not exist here, and failed. tests/seal-wait.nix D5.
      requiredBy = [ "getty@tty1.service" ];
      before = [ "getty@tty1.service" ];
      # After the window unit (so "not open in this boot" is a fact, not a race) and after the
      # first-login questions and the desktop's boot entry (the wait must not seal under them).
      after = [ "agentos-setup-window.service" "agentos-surface-apply.service" "agentos-boot-default.service" "ollama.service" ];
      # NO Condition*= on this unit, ever. A unit skipped by a condition counts as STARTED for every
      # unit that Requires= it, and the agent's getty would follow a wait that never ran. A state
      # directory that cannot be written makes the wait FAIL (it cannot write the marker).
      unitConfig.RequiresMountsFor = [ "/var/lib/agent-os" ];
      # A rebuild must not restart this unit: an explicit restart is passed on to everything that
      # Requires= it, and that is the agent's getty (the running session would be killed).
      restartIfChanged = false;
      stopIfChanged = false;
      serviceConfig = sandbox // {
        Type = "oneshot";
        ExecStart = "${run} wait";
        # The tick ends the wait when the budget is spent. If the wait is still there this long after
        # it, the tick did not: the timeout fires and ExecStopPost seals (and only then: a shutdown
        # stops the wait as well, and a reboot inside the window must reopen it).
        ExecStopPost = "${run} ended";
        TimeoutStartSec = toString (cfg.seconds + 600);
        TimeoutStopSec = "300s";           # `ended` may wait for the state lock behind a seal in progress
        # The detector speaks HTTP to the fetcher on loopback, and to nothing else.
        RestrictAddressFamilies = [ "AF_UNIX" "AF_NETLINK" "AF_INET" ];
        IPAddressDeny = "any";
        IPAddressAllow = "localhost";
      };
    };

    # Door 2: every console agetty serves. One option, read by getty@, serial-getty@, container-getty@
    # and console-getty alike. break-glass.nix spells out its own login program for tty3.
    services.getty.loginProgram = lib.mkIf (!cr.sealed) "${loginGate}";

    # Door 3: the user manager (lingering). A template drop-in: every uid but 0 waits for the token.
    systemd.services."user@" = lib.mkIf (!cr.sealed) {
      overrideStrategy = "asDropin";
      serviceConfig.ExecCondition = "+${userGate} %i";
    };

    # tty1 between the first-login questions and the agent's login. Output only, a throwaway account,
    # no network. It exits when the status says sealed (or failed), so tty1 never has two owners.
    # Skipped on a box that is already sealed: there is nothing to show.
    systemd.services.agentos-setup-progress = lib.mkIf (!cr.sealed) {
      description = "Agent OS setup: progress on the console";
      wantedBy = [ "multi-user.target" ];
      after = [ "agentos-setup-window.service" "agentos-firstlogin.service" "agentos-surface-apply.service"
                "agentos-boot-default.service" "plymouth-quit-wait.service" ];
      before = [ "getty@tty1.service" ];
      unitConfig.ConditionPathExists = "!/var/lib/agent-os/seal/sealed";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${agos-progress}/libexec/agentos-setup-progress ${toString (cfg.seconds + 660)}";
        TimeoutStartSec = toString (cfg.seconds + 720);
        DynamicUser = true;
        StandardInput = "null";
        StandardOutput = "tty";
        StandardError = "journal";
        TTYPath = "/dev/tty1";
        PrivateNetwork = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        RestrictAddressFamilies = "AF_UNIX";
        SystemCallArchitectures = "native";
      };
    };
  };
}
