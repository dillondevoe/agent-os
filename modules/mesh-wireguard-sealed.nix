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
# FIRST-BOOT EXPECTATION (Fable gate, PR #86 advisory 4, 2026-08-14): because the sealed
# composition enables this module with an EMPTY map and no key, `wireguard-<iface>.service`
# FAILS on a freshly imaged box until the S6 runbook provisions the key. That failure is the
# design working — a mesh unit that came up without a key would mean a key came from the
# image. Read a red wg unit on first hardware boot as "runbook step not run yet", not as a
# regression. The preStart guard below says exactly that on the console.
#
# EGRESS-WALL INTERACTION (why clean-room.nix renders the accept lines, not this file):
# WireGuard's encapsulated outer UDP packets are generated in-kernel with no owning socket,
# so `meta skuid` scoping cannot match them — they need their own accept lines. Those lines
# MUST live inside the `agentos_egress` output chain itself: nftables traverses every
# table's hook chains, so an `accept` verdict in a second additive table would NOT save a
# packet from clean-room's `policy drop` (accept only ends processing within its own
# table). clean-room.nix therefore renders the mesh accepts from this module's config.
# There are TWO of them and this header used to describe only the second, which read as
# "the whole mesh diff is pinned" when half of it was not:
#   (a) INNER traffic onto the wg interface — `meta skuid 0 oifname <iface> accept`. Scoped
#       to uid 0 because the chain's invariant is that the agent/model/capabilities stay
#       ZERO-egress, not merely off-the-internet; a uid-blind version of this line hands the
#       untrusted agent an all-port path to every mesh peer. See the long note at that rule.
#   (b) OUTER encapsulated UDP — scoped per-peer to `ip daddr <endpoint-ip> udp dport
#       <endpoint-port>`, no blanket UDP, because kernel-generated packets have no skuid.
#
# SECURITY SURFACE (egress wall + mesh reachability): routed branch -> PR -> Fable, never
# direct-push, never self-merge.
{ config, pkgs, lib, ... }:
let
  cfg = config.agentos.meshWireguard;
  # endpoint literals are enforced as "<ipv4>:<port>" so the wall lines they generate are
  # exact daddr/dport pairs — a DNS-name endpoint would need an open resolver path at
  # handshake time and an unpinnable daddr, both of which the seal exists to refuse.
  # SHAPE GATE, NOT VALIDATION (Fable gate, PR #86 advisory 3, 2026-08-14): this matches
  # 999.999.999.999:99999 quite happily. That is deliberate and sufficient — its job is to
  # refuse the CLASS of value that would make the egress accept unpinnable (a hostname), and
  # an out-of-range literal is caught at wg setup, loudly, on the box. Do not read it as
  # "endpoints are validated".
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

    # RESOLUTION-TIME key guard (Fable gate, PR #86, 2026-08-14 — ruled: harden, don't replace).
    # The eval-time assertion below inspects the path STRING; this inspects what that string
    # resolves to on the running box, which is the only place the guarantee can actually live.
    # It runs before wg touches the key and fails the unit closed, so a mis-provisioned key is a
    # dead mesh rather than a leaked one.
    systemd.services."wireguard-${cfg.interfaceName}".preStart = ''
      key=${lib.escapeShellArg cfg.privateKeyFile}
      real=$(${pkgs.coreutils}/bin/readlink -f "$key" 2>/dev/null || true)
      if [ -z "$real" ] || [ ! -f "$real" ]; then
        echo "meshWireguard: private key $key is absent. Provision it out-of-band (S6 runbook);" >&2
        echo "  on a freshly imaged box this failure is EXPECTED, not a regression." >&2
        exit 1
      fi
      case "$real" in
        ${builtins.storeDir}/*)
          echo "meshWireguard: private key $key resolves to $real, under ${builtins.storeDir}." >&2
          echo "  Store contents are world-readable and repo-derived. Refusing to start." >&2
          exit 1 ;;
      esac
      mode=$(${pkgs.coreutils}/bin/stat -L -c '%a' "$key")
      if [ "$(( 0$mode & 077 ))" -ne 0 ]; then
        echo "meshWireguard: private key $key has mode $mode — group or other bits are set." >&2
        echo "  Refusing to start; chmod 0600 and chown root." >&2
        exit 1
      fi
    '';

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
        # WHAT THIS LAYER ACTUALLY GUARANTEES (corrected 2026-08-14 after the Fable gate): it is
        # a TYPO NET, not the guarantee. It compares a string prefix, so it passes on
        # `privateKeyFile = "/etc/k"` even when `environment.etc."k"` (declared without an
        # explicit `mode`) makes /etc/k a symlink INTO the store — the exact outcome this
        # assertion names, reached without lying to it. The guarantee is the preStart guard
        # above, which resolves the path. Keep both: this one fails the build at eval, cheaply.
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
