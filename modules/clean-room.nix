# modules/clean-room.nix — the v0.1 "clean room": a default-DENY egress wall + a
# telemetry-off posture that make "sovereign, talks with NO internet" a STRUCTURAL fact,
# not a promise.
#
# Threat model (Phase-2 INV-2, egress): the agent, its local model, and every capability
# it can invoke must have NO path to move bytes off-box. Inference is loopback-only
# (services.ollama @ 127.0.0.1, see modules/brain.nix), so a default-DROP output chain
# costs the agent nothing and denies exfiltration by construction.
#
# Two channels survive once sealed: the nixpkgs binary cache (root / nix-daemon), so the
# box can still rebuild ITSELF from nixpkgs — "nixpkgs-only" — and system time sync scoped
# to systemd-timesyncd ALONE (uid 154, its DNS + NTP, advisory B) so the clock can't drift
# the cache's TLS validity window shut. Everything else is dropped.
#
# Ordering ("seal AFTER the model pull"): `ollama pull` needs the network at provision
# time, so the wall ships UNSEALED (cfg.sealed = false) — a provisioning window that also
# permits DNS + 80/443 so setup-brain.sh can fetch the weights. After the pull, flip
# `agentos.cleanRoom.sealed = true` and `nixos-rebuild switch`; that rebuild removes the
# provisioning allowance — the ollama daemon's own update-ping goes with it — and the box
# is sealed to nixpkgs-only.
#
# SECURITY SURFACE (boot/login + egress wall): routed branch -> PR -> Fable, never
# direct-push, never self-merge.
{ config, pkgs, lib, ... }:
let
  cfg = config.agentos.cleanRoom;
  mw = config.agentos.meshWireguard or { enable = false; };
in {
  options.agentos.cleanRoom = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Enable the Agent OS clean-room egress wall + telemetry-off posture.";
    };
    sealed = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        false = PROVISIONING window: the box may reach DNS + 80/443 so `setup-brain.sh`
        can `ollama pull` the local weights. true = SEALED: the only off-box channel that
        survives is the nixpkgs binary cache (root / nix-daemon), for rebuilds; the agent,
        the local model, and every capability are cut off from the network. Flip to true
        and `nixos-rebuild switch` AFTER the model pull — that rebuild IS the seal.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    # --- telemetry / phone-home OFF (belt; the egress wall below is the real enforcer) ---
    environment.variables.DO_NOT_TRACK = "1";

    # --- the egress wall: default-DROP output, in its OWN additive table so the inbound
    #     firewall's table is left untouched -----------------------------------------------
    # NOTE: this relies on in-process (glibc) name resolution — configuration.nix uses
    # NetworkManager's default DNS (no systemd-resolved), so a root-initiated lookup
    # egresses as skuid 0 and is accepted. If systemd-resolved is ever enabled, DNS would
    # originate from the systemd-resolve user and need its own accept line — flag at review.
    networking.nftables.enable = true;
    networking.nftables.tables.agentos_egress = {
      family = "inet";
      content = ''
        chain output {
          type filter hook output priority 0; policy drop;

          # return traffic for connections we already allowed
          ct state established,related accept
          ct state invalid drop

          # on-box IPC: the local brain (127.0.0.1:11434), mem, everything loopback
          oifname "lo" accept

          # nixpkgs-only survivor: nix-daemon substituter fetches + fwupd, both root-initiated.
          # SCOPED (PR-A, Fix 1 interim) — was a blanket `meta skuid 0 accept`, which let ANY
          # skuid-0 process egress anywhere and so defeated the seal from ONE compromised root
          # process (Advisory A / geist v0.2 ruling — the exact class modules/system-set.nix
          # exists to bound). nix-daemon needs glibc DNS (udp/tcp 53, per the NetworkManager-DNS
          # note above) + HTTPS (443) to the substituter; fwupd is the same shape. Ports only —
          # nftables cannot name hostnames.
          # HONEST RESIDUAL: HTTPS+DNS root egress remains open, so a compromised root process
          # could still exfiltrate TWO ways — (a) an arbitrary :443, HTTPS to any host; and
          # (b) DNS tunnelling over the open udp/tcp 53 to an attacker-run resolver (payload
          # smuggled in QNAMEs — no :443 needed at all). nftables can gate neither (it cannot
          # name hostnames). PR-K closes BOTH with a dedicated-uid local HTTPS fetch-proxy
          # enforcing a hostname allowlist (cache.nixos.org + pinned flake hosts) and dropping
          # uid 0 to default-DROP. (Fable LOW-#5, 2026-07-29 — comment previously named only :443.)
          #
          # nfproto ipv4 PARITY (PR-A residual): every scoped skuid accept below carries
          # `meta nfproto ipv4` so it is EXPLICITLY v4-only, matching the v4-only doctrine in the
          # IPv6 note at the bottom of this chain. Today v6 egress is already dead (NDP is
          # default-dropped), so this changes no live behaviour — but WITHOUT it, the day someone
          # scopes in a link-local NDP allow per that note, these bare `skuid 0 … accept` rules
          # would silently start matching v6 too and widen root egress to 443/53/123 over IPv6.
          # The predicate keeps the accepts pinned to the family they were reasoned about.
          meta nfproto ipv4 meta skuid 0 udp dport 53 accept
          meta nfproto ipv4 meta skuid 0 tcp dport { 53, 443 } accept
          # DHCPv4 client (NetworkManager's, uid 0): DISCOVER/REQUEST egress as udp sport 68 →
          # dport 67 (broadcast). The scoped skuid-0 rules above do NOT cover it, so under the
          # default-DROP policy it would be dropped — and on the SEALED box on wifi (the Dell,
          # always DHCP) a boot or lease-renewal would then lose the lease entirely: no address,
          # no route, no nixpkgs rebuild channel, and no way to rebuild out except the tty3
          # break-glass. (Fable HIGH, 2026-07-29 — regression from scoping the old blanket root
          # accept.) v4-only: v6 SLAAC/DHCPv6 is dead when sealed per the IPv6 note below, and
          # the DHCP *reply* is inbound (input chain), not this egress table.
          # LIVE-VERIFY on HW: sealed box must hold its address across a real lease renewal.
          # NB — the `udp` keyword is DELIBERATELY repeated (`udp sport 68 udp dport 67`, not
          # `udp sport 68 dport 67`). nft 1.0.9 (the sandbox builder's version) rejects a single
          # `udp` shared across a sport+dport pair with "No symbol type information" at `dport`, in
          # EITHER order — a parser bug, not a syntax error. Repeating `udp` is the standard
          # workaround and is semantically identical. Do NOT "simplify" this back to one `udp`: the
          # nftables `checkRuleset` (`nft -c`) that catches it runs ONLY when the sealed toplevel /
          # VM is built, NEVER in `nix flake check` (VM/toplevel are out of `checks`) — so a
          # re-break passes CI green and only surfaces at `nixos-rebuild` on the box.
          meta nfproto ipv4 meta skuid 0 udp sport 68 udp dport 67 accept

          # time sync — advisory B (PR#19/#20 Fable). systemd-timesyncd runs as the STATIC
          # user systemd-timesync (uid ${toString config.ids.uids.systemd-timesync}, pinned
          # in nixpkgs ids.nix), NOT a DynamicUser and NOT root — so `skuid 0` above does not
          # cover it, but a numeric skuid-scope IS reliable (the numeric uid sidesteps
          # passwd/ruleset-load ordering). It must egress BOTH to resolve the pool hostnames
          # (there is no systemd-resolved — NetworkManager DNS — so timesyncd resolves via
          # glibc → udp/53) AND to speak NTP (udp/123); a udp/123-only allow silently fails
          # sealed (DNS dropped → resolution fails → NTP never happens → clock STILL drifts).
          # Scoping BOTH ports to uid 154 keeps the clock honest — so the nixpkgs cache's TLS
          # validity window can't drift shut — while every other process (the agent, the
          # model, any capability) stays zero-egress. Permanent survivor: kept even sealed,
          # since drift accrues most on a long-sealed box.
          meta nfproto ipv4 meta skuid ${toString config.ids.uids.systemd-timesync} udp dport { 53, 123 } accept
        ${lib.optionalString (mw.enable or false) ''
          # WP-S1 mesh (mesh-wireguard-sealed.nix). Two accept classes, both REQUIRED here in
          # THIS chain: an accept in a second additive table cannot save a packet from this
          # chain's `policy drop` (nftables traverses all tables; drop anywhere wins).
          # (a) INNER traffic onto the wg interface — mesh-only by construction: what egresses
          #     via ${mw.interfaceName} can only reach the peers' allowedIPs (wg cryptokey
          #     routing), so this is not an internet path.
          oifname "${mw.interfaceName}" accept
          # (b) OUTER encapsulated UDP to each peer endpoint. Kernel-generated (no owning
          #     socket), so the skuid scoping above can never match it — pinned instead to the
          #     exact daddr+dport of each configured peer, rendered from the module's
          #     literal-ipv4 endpoints. No peers with endpoints => no lines => no outer egress.
        ${lib.concatMapStringsSep "\n" (p:
            let ep = lib.splitString ":" p.endpoint; in
            "          meta nfproto ipv4 ip daddr ${lib.elemAt ep 0} udp dport ${lib.elemAt ep 1} accept"
          ) (lib.filter (p: p.endpoint != null) mw.peers)}
        ''}
        ${lib.optionalString (!cfg.sealed) ''
          # PROVISIONING (unsealed) — removed by `sealed = true` + rebuild. Lets
          # setup-brain.sh resolve + fetch the model weights (and, transiently, lets the
          # ollama daemon's update-ping out; sealing kills it).
          # nfproto ipv4 PARITY (Fable LOW, 2026-07-30): these carry `meta nfproto ipv4` for the
          # same reason as the scoped skuid accepts above — no live gap today (v6 NDP is
          # default-dropped, and these are removed by sealing anyway), but if a link-local NDP
          # allow is ever scoped in per the IPv6 note below, an unprefixed `udp/tcp dport …` would
          # silently start matching v6 too and widen the provisioning window over IPv6. Pin them
          # to the family they were reasoned about.
          meta nfproto ipv4 udp dport 53 accept
          meta nfproto ipv4 tcp dport 53 accept
          meta nfproto ipv4 tcp dport { 80, 443 } accept
        ''}
          # IPv6 note — advisory C (PR#19 Fable). This default-drop also drops outbound
          # ICMPv6 NDP (neighbor/router solicitation), so IPv6 neighbor discovery — and
          # thus all IPv6 egress — is dead once sealed. INTENDED: v0.1 is IPv4 (the skuid-0
          # nixpkgs fetches + udp/123 NTP both work over v4). A future "why is v6 dead?" is
          # THIS line, not a reason to loosen the wall — if v6 is ever needed, scope a
          # link-local-only NDP allow (icmpv6 type { nd-neighbor-solicit,
          # nd-neighbor-advert } accept), never a blanket v6 accept.

          # the agent, the local model, and any capability => dropped (INV-2 egress)
        }
      '';
    };

    # --- structural invariants: eval FAILS if the sovereign posture is broken ------------
    assertions = [
      {
        assertion = config.services.ollama.host == "127.0.0.1";
        message = "clean-room: the local brain (services.ollama) must stay loopback-only "
          + "(127.0.0.1); a routable bind would defeat the INV-2 egress wall.";
      }
    ];

    warnings = lib.optional (!cfg.sealed)
      ("agentos.cleanRoom.sealed = false (PROVISIONING): egress to DNS + 80/443 is open so "
      + "the model can be pulled. Set sealed = true and rebuild once the weights are present.");
  };
}
