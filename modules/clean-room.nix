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
          # could still reach an arbitrary :443. PR-K closes this with a dedicated-uid local
          # HTTPS fetch-proxy enforcing a hostname allowlist (cache.nixos.org + pinned flake
          # hosts — which nftables cannot express) and drops uid 0 to default-DROP.
          meta skuid 0 udp dport 53 accept
          meta skuid 0 tcp dport { 53, 443 } accept

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
          meta skuid ${toString config.ids.uids.systemd-timesync} udp dport { 53, 123 } accept
        ${lib.optionalString (!cfg.sealed) ''
          # PROVISIONING (unsealed) — removed by `sealed = true` + rebuild. Lets
          # setup-brain.sh resolve + fetch the model weights (and, transiently, lets the
          # ollama daemon's update-ping out; sealing kills it).
          udp dport 53 accept
          tcp dport 53 accept
          tcp dport { 80, 443 } accept
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
