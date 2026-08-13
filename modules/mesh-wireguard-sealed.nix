# modules/mesh-wireguard-sealed.nix — WP-S1: WireGuard point-to-point mesh access for the
# SEALED variant (spec-agentos-phase-s-execution-2026-08-13 §WP-S1). The sealed box gets
# WireGuard, NOT Tailscale — the re-baseline revives that ruling for the sealed lane
# specifically; agentos-open keeps Tailscale and never imports this module's enablement.
#
# NO CREDENTIALS IN THE REPO (Phase-S constraint, binding): the private key lives at
# `privateKeyFile` on the box, provisioned out-of-band (S6 runbook step). Peer topology
# (public keys + endpoints) is injected at deployment time via these options; the defaults
# ship EMPTY so the public repo carries structure, never a live mesh map.
#
# EGRESS-WALL INTERACTION (why clean-room.nix renders the accept lines, not this file):
# WireGuard's encapsulated outer UDP packets are generated in-kernel with no owning socket,
# so `meta skuid` scoping cannot match them — they need their own accept lines. Those lines
# MUST live inside the `agentos_egress` output chain itself: nftables traverses every
# table's hook chains, so an `accept` verdict in a second additive table would NOT save a
# packet from clean-room's `policy drop` (accept only ends processing within its own
# table). clean-room.nix therefore renders the mesh accepts from this module's config,
# scoped per-peer to `ip daddr <endpoint-ip> udp dport <endpoint-port>` — no blanket UDP.
#
# SECURITY SURFACE (egress wall + mesh reachability): routed branch -> PR -> Fable, never
# direct-push, never self-merge.
{ config, pkgs, lib, ... }:
let
  cfg = config.agentos.meshWireguard;
  # endpoint literals are enforced as "<ipv4>:<port>" so the wall lines they generate are
  # exact daddr/dport pairs — a DNS-name endpoint would need an open resolver path at
  # handshake time and an unpinnable daddr, both of which the seal exists to refuse.
  endpointRe = "^([0-9]{1,3}\\.){3}[0-9]{1,3}:[0-9]{1,5}$";
in {
  options.agentos.meshWireguard = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "WireGuard point-to-point mesh access for the sealed variant (WP-S1).";
    };
    interfaceName = lib.mkOption {
      type = lib.types.str;
      default = "wg-mesh";
      description = "WireGuard interface name (also used by clean-room's oifname accept).";
    };
    listenPort = lib.mkOption {
      type = lib.types.port;
      default = 51820;
      description = "UDP listen port for inbound handshakes (opened in the input firewall).";
    };
    address = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "10.100.0.2/24" ];
      description = "Interface addresses. Deployment-time value; empty in the public repo.";
    };
    privateKeyFile = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/wireguard/wg-mesh.key";
      description = ''
        Path to the private key ON THE BOX (0600, root). Provisioned out-of-band by the S6
        runbook — never a store path, never checked in.
      '';
    };
    peers = lib.mkOption {
      default = [ ];
      description = ''
        Mesh peers. Deployment-time values; ships empty. Each endpoint must be a literal
        "<ipv4>:<port>" — clean-room.nix pins an egress accept to exactly that pair.
      '';
      type = lib.types.listOf (lib.types.submodule {
        options = {
          publicKey = lib.mkOption { type = lib.types.str; };
          endpoint = lib.mkOption {
            type = lib.types.nullOr (lib.types.strMatching endpointRe);
            default = null;
            description = "Literal ipv4:port, or null for a listen-only peer.";
          };
          allowedIPs = lib.mkOption {
            type = lib.types.listOf lib.types.str;
            example = [ "10.100.0.1/32" ];
          };
          persistentKeepalive = lib.mkOption {
            type = lib.types.nullOr lib.types.int;
            default = 25;
          };
        };
      });
    };
  };

  config = lib.mkIf cfg.enable {
    networking.wireguard.enable = true;
    networking.wireguard.interfaces.${cfg.interfaceName} = {
      ips = cfg.address;
      listenPort = cfg.listenPort;
      privateKeyFile = cfg.privateKeyFile;
      peers = map (p: {
        publicKey = p.publicKey;
        allowedIPs = p.allowedIPs;
        persistentKeepalive = p.persistentKeepalive;
      } // lib.optionalAttrs (p.endpoint != null) { endpoint = p.endpoint; }) cfg.peers;
    };

    # Inbound handshake path (input chain — the ordinary firewall, not the egress wall).
    networking.firewall.allowedUDPPorts = [ cfg.listenPort ];

    assertions = [
      {
        # The whole point of this module is sealed-lane mesh access behind the wall; enabling
        # it without the wall would silently mean "WireGuard plus unrestricted egress".
        assertion = config.agentos.cleanRoom.enable;
        message = "meshWireguard (WP-S1) requires the clean-room egress wall; it renders its "
          + "accept lines inside clean-room's output chain and is meaningless without it.";
      }
      {
        # No credentials in the public repo, ever (Phase-S binding constraint): a store-path
        # key would be world-readable in /nix/store and likely committed.
        assertion = !(lib.hasPrefix builtins.storeDir cfg.privateKeyFile);
        message = "meshWireguard: privateKeyFile must be an on-box path (e.g. /var/lib/"
          + "wireguard/...), never a /nix/store path — store paths are world-readable and "
          + "end up in the public repo.";
      }
      {
        # Tailscale is the OPEN lane's mesh. One box, one mesh daemon: if both are ever
        # enabled the sealed box grows a second, unpinned egress path.
        assertion = !(config.services.tailscale.enable or false);
        message = "meshWireguard (WP-S1): the sealed variant meshes over WireGuard ONLY; "
          + "services.tailscale.enable must stay false here (Tailscale belongs to agentos-open).";
      }
    ];
  };
}
