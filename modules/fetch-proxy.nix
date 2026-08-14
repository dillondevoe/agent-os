# modules/fetch-proxy.nix — WP-S5: replace the interim skuid-0 port scope with a NAME-scoped
# fetch path, and take uid 0 to default-DROP.
#
# THE PROBLEM THIS SOLVES. WP-S1 narrowed a blanket `meta skuid 0 accept` to
#
#     meta nfproto ipv4 meta skuid 0 udp dport 53 accept
#     meta nfproto ipv4 meta skuid 0 tcp dport { 53, 443 } accept
#
# which is a PORT scope, not a DESTINATION scope. Any root process may still open :443 to any
# address on the internet. That was labelled "interim" in clean-room.nix from the day it landed:
# nftables cannot name hosts, so a hostname allowlist cannot live in the packet filter at all.
# It has to live in something that speaks HTTP. Hence a proxy.
#
# THE SHAPE. nix-daemon (and fwupd, if enabled) get `https_proxy=http://127.0.0.1:<port>`. They
# reach the proxy over loopback, which the egress chain already accepts. The proxy runs as its
# OWN uid, and that uid — not uid 0 — is what the chain permits out to 53/443. So the only path
# off this box for a root process is through a filter that checks the requested HOSTNAME against
# an allowlist. uid 0 goes default-DROP.
#
# WHAT THIS IS AND IS NOT WORTH. Be precise, because the temptation is to call it more:
#
#   - It DOES bound a compromised nix-daemon (or a malicious derivation escaping the build
#     sandbox as root) to the allowlisted names. That is the S1 residual, closed.
#   - It does NOT stop a compromised root from EXFILTRATING to an allowlisted host. `CONNECT
#     cache.nixos.org:443` is permitted, and what flows inside that tunnel is opaque to the proxy
#     by construction — it is TLS. The property is "which peers", never "which bytes".
#   - It does NOT authenticate the name. The proxy trusts the client's CONNECT line and resolves
#     it itself; a client cannot reach evil.example.com by *claiming* it is cache.nixos.org, but
#     neither is there any certificate check here. TLS verification stays where it always was —
#     in nix, which pins by narHash regardless of who served the bytes.
#   - It is the one Phase-S item a VM cannot fully validate (spec §WP-S5). tests/fetch-proxy-
#     allowlist.nix establishes the hostname PREDICATE at a constant destination IP. It cannot
#     establish that a real `nixos-rebuild switch` completes against the real cache. That is an
#     at-the-box acceptance, and it is deliberately not claimed here.
#
# FOUR SEMANTICS OF TINYPROXY, READ OUT OF ITS SOURCE (1.11.3) RATHER THAN ITS PROSE. Two of
# them are default-PERMISSIVE, which is exactly the shape that ships a policy file that does
# nothing while looking like it does everything:
#
#   1. `filter_run()` returns 1 = BLOCK. With FILTER_OPT_DEFAULT_DENY set, a pattern MATCH
#      returns 0 (allow) and falling off the end returns 1 (block). So `FilterDefaultDeny yes`
#      plus patterns is an ALLOWLIST. Without it, the identical file is a BLOCKLIST — the same
#      config, read the opposite way. Non-negotiable here; not exposed as an option.
#   2. CONNECT IS filtered. reqs.c sets connect_method at the CONNECT branch and falls through
#      to `filter_run (fu ? url : request->host)`. With FilterURLs off it matches on the host
#      from the CONNECT line, which is the only thing an HTTPS request exposes to a proxy.
#   3. Entries are compiled with `regcomp` — REGEXES, not literals. An unanchored
#      `cache.nixos.org` therefore matches `cache.nixos.org.evil.com`, and would read as a
#      correct allowlist in every review. This module never accepts a regex: it takes literal
#      hostnames, VALIDATES them against a hostname grammar, and renders `^…$` with the dots
#      escaped itself. A caller cannot write an unanchored entry because a caller cannot write
#      an entry.
#   4. ABSENT ConnectPort options means ALL ports are allowed for CONNECT
#      (`check_allowed_connect_ports`: `if (!connect_ports) return 1;`). Pinned to 443.
#
# The build-time guard that filtering is even COMPILED IN lives in flake.nix as
# `checks.fetch-proxy-filter-compiled` — `#ifdef FILTER_ENABLE` wraps the whole filter block, so
# a tinyproxy built `--disable-filter` accepts `Filter`/`FilterDefaultDeny` in its config and
# silently proxies everything. Today nixpkgs passes no configureFlags and upstream defaults the
# flag to yes; that is an upstream default this security property rests on, so it is asserted
# rather than assumed.
{ config, lib, pkgs, ... }:

let
  cfg = config.agentos.fetchProxy;

  # A literal hostname, validated then escaped then anchored. See semantic (3) above: the
  # validation is the real defence — escaping alone would still let `*` or `|` through if the
  # grammar were loose, so the grammar is tight and escaping is the belt to its suspenders.
  hostnameRe = "[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*";
  badHosts = lib.filter (h: builtins.match hostnameRe h == null) cfg.allowedHosts;
  # `.` is the only regex metacharacter that survives the grammar above; escape it anyway by
  # construction rather than by that argument, so a future grammar widening does not silently
  # become a pattern-injection.
  toPattern = h: "^" + (lib.replaceStrings [ "." ] [ "\\." ] h) + "$";
  filterFile = pkgs.writeText "fetch-proxy-allowlist" (
    lib.concatMapStrings (h: toPattern h + "\n") cfg.allowedHosts
  );

  proxyUrl = "http://127.0.0.1:${toString cfg.port}";
in
{
  options.agentos.fetchProxy = {
    enable = lib.mkEnableOption ''
      the WP-S5 fetch proxy: root's egress to 53/443 is withdrawn and re-granted to a dedicated
      proxy uid that enforces a hostname allowlist
    '';

    allowedHosts = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "cache.nixos.org" "channels.nixos.org" "github.com" ];
      description = ''
        LITERAL hostnames the box may fetch from. Not regexes, not globs, not suffixes —
        `nixos.org` does NOT admit `cache.nixos.org`. Each entry is anchored by this module.

        An empty list is a working configuration and means "no outbound HTTPS at all", which is
        the correct posture for a box that has finished provisioning. It is the DEFAULT because
        the failure direction matters: a forgotten allowlist should strand a fetch, never permit
        an unreviewed one.
      '';
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 3128;
      description = "Loopback port the proxy listens on. Never bound off-host.";
    };

    uid = lib.mkOption {
      type = lib.types.int;
      default = 350;
      description = ''
        STATIC uid for the proxy user. Static because the egress chain has to name it NUMERICALLY
        at build time: `meta skuid <n>` is rendered into the ruleset by clean-room.nix, and the
        sandboxed `nft --check` gate that parses that ruleset has no user database to resolve a
        NAME against. An `isSystemUser` with an auto-allocated uid would be resolvable only on
        the box, which is precisely where we no longer get to check it.

        350 sits in the gap between the highest static allocation in nixpkgs' ids.nix (327) and
        the floor of its auto-allocation range for system users (400..999, allocated downward
        from 999). The collision assertion below is what actually enforces that, since both of
        those numbers are upstream facts that can move.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = badHosts == [ ];
        message =
          "agentos.fetchProxy.allowedHosts takes literal hostnames, not patterns. Rejected: "
          + lib.concatStringsSep ", " (map (h: ''"${h}"'') badHosts);
      }
      {
        # A silent uid collision would hand a SECOND user the proxy's egress grant, which is the
        # whole permission this module exists to keep scarce.
        assertion =
          lib.length (lib.filter (u: u.uid == cfg.uid) (lib.attrValues config.users.users)) == 1;
        message =
          "agentos.fetchProxy.uid = ${toString cfg.uid} is claimed by more than one user: "
          + lib.concatStringsSep ", "
            (map (u: u.name) (lib.filter (u: u.uid == cfg.uid) (lib.attrValues config.users.users)));
      }
    ];

    services.tinyproxy = {
      enable = true;
      settings = {
        Listen = "127.0.0.1";
        Port = cfg.port;
        # Semantic (1): this pair is the allowlist. Neither half is optional and neither is
        # exposed as an option — `FilterDefaultDeny no` would silently invert the file's meaning.
        Filter = filterFile;
        FilterDefaultDeny = true;
        # Match on the host, not the URL — an HTTPS CONNECT has no URL to match (semantic 2).
        FilterURLs = false;
        # Declare the regex dialect rather than inheriting it. The patterns this module renders
        # are anchored literals and parse identically as BRE or ERE, but a dialect that is
        # assumed is a dialect that can change under you.
        #
        # `FilterExtended yes` is the OLD spelling of this and still works, but tinyproxy 1.11.3
        # answers it with `WARNING line N: deprecated option FilterExtended, use FilterType`
        # (conf.c handle_filterextended). A deprecated option is one release from being a
        # REMOVED option, and the removal direction here is silent: drop FilterExtended and the
        # dialect falls back to BRE, where `^…$` still parses and the file still reads correct.
        # conf.c's STDCONF grammar for this key is a bare `(bre|ere|fnmatch)`, unquoted.
        FilterType = "ere";
        FilterCaseSensitive = false;
        # Semantic (4): without this line, CONNECT to ANY port is permitted.
        ConnectPort = [ 443 ];
        # No inbound-facing surface: loopback only, and clients are the box itself.
        Allow = [ "127.0.0.1" ];
        Timeout = 600;
        LogLevel = "Connect";
      };
    };

    # Static uid — see the option's description for why a name will not do here.
    users.users.tinyproxy.uid = cfg.uid;

    # The proxy is a network-facing HTTP parser running on the sealed box (three CVEs patched in
    # the 1.11.3 nixpkgs derivation as of this writing). It is bound to loopback and its clients
    # are root daemons, so the exposure is inward, not inward-from-the-internet — but it parses
    # attacker-influenced RESPONSE bytes from every host it is allowed to reach, so it gets the
    # hardening a leaf service should have had anyway.
    systemd.services.tinyproxy.serviceConfig = {
      NoNewPrivileges = true;
      PrivateTmp = true;
      PrivateDevices = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      RestrictNamespaces = true;
      RestrictRealtime = true;
      RestrictSUIDSGID = true;
      LockPersonality = true;
      MemoryDenyWriteExecute = true;
      SystemCallArchitectures = "native";
      RestrictAddressFamilies = [ "AF_INET" "AF_UNIX" ];
      # Restart=on-failure comes from the upstream module. Keep it, and make the gap short: while
      # the proxy is down the box cannot fetch AT ALL (uid 0 has no direct path any more), so a
      # crash is a hard outage of nixos-rebuild rather than a silent fallback to open egress.
      # Fail-CLOSED is the intended direction; fail-closed-for-ten-minutes is not.
      RestartSec = "1s";
    };

    # Route the fetchers. These are the two root-side things on this box that talk to the
    # internet; anything else that grows a need must be added HERE and to the allowlist, and will
    # otherwise simply fail to connect — which is the point.
    systemd.services.nix-daemon.environment = {
      https_proxy = proxyUrl;
      http_proxy = proxyUrl;
      # Loopback must never be proxied, or the daemon would ask the proxy to reach the proxy.
      no_proxy = "127.0.0.1,localhost";
    };

    systemd.services.fwupd.environment = lib.mkIf config.services.fwupd.enable {
      https_proxy = proxyUrl;
      http_proxy = proxyUrl;
      no_proxy = "127.0.0.1,localhost";
    };
  };
}
