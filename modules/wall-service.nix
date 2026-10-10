# The wall as a socket-activated system service (docs/design/broker-service.md, the merged,
# Fable-reviewed spec this file implements; build-order PR 2).
#
# Security surface (the wall's launch path, privilege, environment): branch -> PR -> Fable(code)
# -> merge. Never direct-push.
#
#   agent-os-wall.socket     /run/agent-os/wall.sock, root:agent-os-wall 0660, Accept=yes,
#                            MaxConnections=1 (a concurrent connection is REFUSED: single-flight
#                            system-wide, spec §2.1)
#   agent-os-wall@.service   one instance per connection: bin/agent-os-wall-launch (peer check bound
#                            to the agent uid, exactly one request line) -> mcp parse | broker run,
#                            as root under a tight unit with a clean systemd environment (§2.3)
#
# The agent reaches it through the installed agent-loop, which has the socket path compiled in
# (modules/agent-shell.nix). Every privileged effect (audit, taint, confirm, capability units) is
# now produced by a wall instance only.
{ config, pkgs, lib, ... }:

let
  cfg = config.agentos.wall;
  socketPath = "/run/agent-os/wall.sock";
  group = "agent-os-wall";

  confirmPkg = import ./confirm-pkg.nix { inherit pkgs; };
  capInvoke = import ./cap-invoke-pkg.nix { inherit pkgs; };

  # Timing, derived (spec §2.3): confirm backstop + capability timeout + unit reap + margin.
  reapS = 10;
  marginS = 20;
  runtimeMaxS = confirmPkg.brokerTimeout + capInvoke.capTimeoutS + reapS + marginS;
  clientTimeoutS = runtimeMaxS + 10;

  # The installed wrappers the VM tests drive (python3 -I, seams pinned). Same derivations as
  # modules/mcp.nix and modules/broker.nix install, taken from the system profile's packages.
  mcpBin = "/run/current-system/sw/bin/mcp";
  brokerBin = "/run/current-system/sw/bin/broker";

  launcher = pkgs.runCommand "agent-os-wall-launch" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    mkdir -p $out/bin
    cp ${../bin/agent-os-wall-launch} $out/bin/agent-os-wall-launch
    substituteInPlace $out/bin/agent-os-wall-launch \
      --replace-fail 'WALL_AGENT_USER = ""' 'WALL_AGENT_USER = "agent"' \
      --replace-fail 'WALL_MCP = ""' 'WALL_MCP = "${mcpBin}"' \
      --replace-fail 'WALL_BROKER = ""' 'WALL_BROKER = "${brokerBin}"'
    chmod 0555 $out/bin/agent-os-wall-launch
    patchShebangs $out/bin
  '';

  relayIsIp = builtins.match "[0-9]{1,3}(\\.[0-9]{1,3}){3}|[0-9a-fA-F:]+" confirmPkg.relayAddr != null;
  wallMembers = lib.attrNames (lib.filterAttrs (_: u: lib.elem group (u.extraGroups or [ ]) || (u.group or "") == group)
    config.users.users);
in
{
  options.agentos.wallInternal = lib.mkOption {
    type = lib.types.attrs;
    internal = true;
    readOnly = true;
    description = "Derived wall constants for other modules (socket path, client/runtime timeouts).";
  };

  options.agentos.wall = {
    auditSigner = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = ''
        Participant the wall signs its audit records as (AGENT_OS_AUDIT_SIGNER). The wall unit is the
        ONLY place this is set: a login-shell environment never reaches a systemd unit, so setting it
        elsewhere silently leaves signing off (spec §2.3). null = unsigned (today's default).
      '';
    };
    auditRequireSigned = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = cfg.auditSigner;
      defaultText = lib.literalExpression "config.agentos.wall.auditSigner";
      description = "AGENT_OS_AUDIT_REQUIRE_SIGNED for the wall (defaults to the signer).";
    };
  };

  config = {
    assertions = [
      { assertion = wallMembers == [ "agent" ]
                    && lib.all (m: m == "agent") (config.users.groups.${group}.members or [ ]);
        message = "wall-service: the ${group} group must contain exactly the agent account (found: ${lib.concatStringsSep "," wallMembers}). Anyone in it can ask the wall for privileged effects."; }
      { assertion = confirmPkg.humanWindow < confirmPkg.brokerTimeout
                    && confirmPkg.brokerTimeout < runtimeMaxS && runtimeMaxS < clientTimeoutS;
        message = "wall-service: timing must order human window < confirm backstop < RuntimeMaxSec < client timeout (spec §2.3)."; }
      { assertion = relayIsIp;
        message = "wall-service: confirm relayAddr '${confirmPkg.relayAddr}' must be an IP literal (the wall has no DNS path and IPAddressAllow= cannot express a name; spec §2.3)."; }
    ];

    users.groups.${group} = { };
    users.users.agent.extraGroups = [ group ];

    systemd.sockets.agent-os-wall = {
      description = "Agent OS wall (the capability broker), one request per connection";
      wantedBy = [ "sockets.target" ];
      listenStreams = [ socketPath ];
      socketConfig = {
        Accept = true;
        MaxConnections = 1;
        SocketUser = "root";
        SocketGroup = group;
        SocketMode = "0660";
      };
    };

    systemd.services."agent-os-wall@" = {
      description = "Agent OS wall instance (mcp parse | broker run for one request)";
      after = [ "systemd-tmpfiles-setup.service" "agent-os-identity-boot.service" ];
      requires = [ "systemd-tmpfiles-setup.service" "agent-os-identity-boot.service" ];
      environment = lib.filterAttrs (_: v: v != null) {
        AGENT_OS_AUDIT_SIGNER = cfg.auditSigner;
        AGENT_OS_AUDIT_REQUIRE_SIGNED = cfg.auditRequireSigned;
      };
      serviceConfig = {
        ExecStart = "${launcher}/bin/agent-os-wall-launch";
        StandardInput = "socket";
        StandardOutput = "socket";
        StandardError = "journal";
        RuntimeMaxSec = runtimeMaxS;
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ReadWritePaths = [ "/var/lib/agent-os/audit" "/var/lib/agent-os/taint"
                           "/var/lib/agent-os/broker" "/var/lib/agent-os/confirm" ];
        ProtectHome = true;
        PrivateTmp = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectKernelLogs = true;
        ProtectControlGroups = true;
        RestrictNamespaces = true;
        RestrictRealtime = true;
        LockPersonality = true;
        RestrictSUIDSGID = true;
        SystemCallArchitectures = "native";
        DevicePolicy = "closed";
        DeviceAllow = [ "/dev/${confirmPkg.gettyTty} rw" ];
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
        IPAddressDeny = "any";
        IPAddressAllow = map (e: e.addr) confirmPkg.relayEndpoints;
      };
    };

    # For modules/agent-shell.nix (the installed agent-loop's compiled socket path and deadline).
    agentos.wallInternal = { inherit socketPath clientTimeoutS runtimeMaxS; };
  };
}
