# modules/surface-choice.nix — the surface choice at first login (docs/design/first-login-and-setup-window.md
# §3-§4, slice 1). Inert unless `agentos.firstLogin.enable = true`.
#
# What it adds when enabled:
#   - the choice store, /var/lib/agent-os/surface.toml (root 0644), and its one reader;
#   - `agentos-firstlogin` on tty1 before the agent's getty, as the unprivileged, sandboxed
#     `agentos-setup` account: three questions, Enter = default, a 5-minute timeout to the defaults.
#     It runs only while the store does not exist;
#   - `agentos-surface-apply`, the root helper: three tokens in, the store out, nothing else;
#   - `agentos-terminal` and `agentos-shell`: "open a terminal" and the human's login shell go through
#     these, so changing either is a file edit by root, not a rebuild. Every offered terminal and
#     shell is installed, which is the point: the choice selects among what is already there.
#
# SLICE 1 SCOPE. The desktop is asked only when the image offers more than one, and this slice offers
# exactly the build's own (`agentos.desktop`): selecting a desktop needs the specialisations of
# slice 2. The audit-chain line the spec asks for on a fallback is owed with the wall slice (3):
# the chain has one signed writer today, and a second root writer is that review's question. Until
# then the reasons go to the journal (`journalctl -t agos-choice`).
{ config, pkgs, lib, ... }:

let
  cfg = config.agentos;
  s = cfg.surfaceInternal;
  choiceConfig = {
    offered = {
      desktop = [ cfg.desktop ];
      terminal = lib.attrNames s.terminals;
      shell = lib.attrNames s.shells;
    };
    defaults = { desktop = cfg.desktop; terminal = cfg.terminal; shell = cfg.shell; };
    exec = {
      terminal = lib.mapAttrs (_: t: "${t.pkg}/bin/${t.cmd}") s.terminals;
      shell = lib.mapAttrs (n: p: "${p}/bin/${n}") s.shells;
    };
    store = "/var/lib/agent-os/surface.toml";
    tokens = "/run/agentos-firstlogin/choice";
    tokens_user = "agentos-setup";
  };

  # One python file, five commands, each with its role fixed in the wrapper. `-I`: no user site, no
  # PYTHON* environment, no cwd on sys.path. The JSON is double-encoded: a JSON string literal is a
  # valid Python string literal for this (ASCII) content.
  agos-choice = pkgs.runCommand "agos-choice" { nativeBuildInputs = [ pkgs.makeBinaryWrapper ]; } ''
    install -Dm755 ${./agos_choice.py} $out/libexec/agos_choice.py
    substituteInPlace $out/libexec/agos_choice.py \
      --replace-fail '"@AGOS_CHOICE_CONFIG@"' ${lib.escapeShellArg (builtins.toJSON (builtins.toJSON choiceConfig))}
    for pair in agentos-firstlogin:firstlogin agentos-surface-apply:apply agentos-terminal:terminal \
                agentos-shell:shell agentos-surface:read; do
      makeBinaryWrapper ${pkgs.python3}/bin/python3 $out/bin/''${pair%%:*} \
        --add-flags "-I $out/libexec/agos_choice.py ''${pair##*:}"
    done
  '';
in
{
  config = lib.mkMerge [
    { agentos.firstLogin.package = agos-choice; }

    (lib.mkIf cfg.firstLogin.enable {
      system.build.agosChoice = agos-choice;
      environment.systemPackages = [ agos-choice ] ++ lib.mapAttrsToList (_: t: t.pkg) s.terminals;
      # A login shell has to be listed; fish and zsh also need their system-wide setup.
      environment.shells = [ "${agos-choice}/bin/agentos-shell" ];
      programs.fish.enable = true;
      programs.zsh.enable = true;

      # No shell, no home, no groups, no sudo or polkit rule: it exists to hold a keyboard for five
      # minutes and write one 0600 file.
      users.groups.agentos-setup = { };
      users.users.agentos-setup = {
        isSystemUser = true;
        group = "agentos-setup";
        description = "Agent OS first-login questions (no privilege)";
      };

      systemd.tmpfiles.rules = [ "d /var/lib/agent-os 0755 root root - -" ];

      systemd.services.agentos-firstlogin = {
        description = "Agent OS first login: desktop, terminal, shell";
        wantedBy = [ "multi-user.target" ];
        after = [ "plymouth-quit-wait.service" "systemd-user-sessions.service" ];
        before = [ "getty@tty1.service" "agentos-surface-apply.service" ];
        unitConfig.ConditionPathExists = "!/var/lib/agent-os/surface.toml";
        serviceConfig = {
          Type = "oneshot";
          ExecStart = "${agos-choice}/bin/agentos-firstlogin";
          User = "agentos-setup";
          Group = "agentos-setup";
          # systemd opens the terminal as root before it drops privileges, so the account needs no
          # `tty` group. Hangup and deallocate run after it exits: the tokens file is the only thing
          # that crosses to the helper.
          StandardInput = "tty";
          StandardOutput = "tty";
          StandardError = "journal";
          TTYPath = "/dev/tty1";
          TTYReset = true;
          TTYVHangup = true;
          TTYVTDisallocate = true;
          # A oneshot stops the moment it exits, and systemd removes a runtime directory when its
          # unit stops: preserved, or the helper would find nothing and every choice would be the
          # default. The helper unlinks the file after its one read.
          RuntimeDirectory = "agentos-firstlogin";
          RuntimeDirectoryMode = "0700";
          RuntimeDirectoryPreserve = "yes";
          TimeoutStartSec = 360;          # the program's own limit is 300 s; this is the backstop
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

      systemd.services.agentos-surface-apply = {
        description = "Agent OS first login: write the choice store (root helper)";
        wantedBy = [ "multi-user.target" ];
        requires = [ "agentos-firstlogin.service" ];
        after = [ "agentos-firstlogin.service" ];
        before = [ "getty@tty1.service" ];
        unitConfig.ConditionPathExists = "!/var/lib/agent-os/surface.toml";
        serviceConfig = {
          Type = "oneshot";
          ExecStart = "${agos-choice}/bin/agentos-surface-apply";
          PrivateNetwork = true;
          ProtectSystem = "strict";
          ProtectHome = true;
          PrivateTmp = true;
          NoNewPrivileges = true;
          ReadWritePaths = [ "/var/lib/agent-os" "/run/agentos-firstlogin" ];
          CapabilityBoundingSet = "CAP_DAC_OVERRIDE";   # read a 0600 file owned by agentos-setup; nothing else
          SystemCallArchitectures = "native";
        };
      };
    })
  ];
}
