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
  # WP-S5. `or` guards the case where fetch-proxy.nix is not in the module list at all, matching
  # how `mw` is read above — clean-room must still evaluate for a variant that composes neither.
  fp = config.agentos.fetchProxy or { enable = false; };

  # The S5 rule delta, in full. Two lines become two lines; only the uid they name changes, and
  # that is the entire security difference. Written as an EXCHANGE rendered from one option
  # rather than as two independent toggles: a config that withdrew root's accepts without issuing
  # the proxy's would brick nixos-rebuild, and one that issued both would be S1 with extra steps
  # while reading, in the rendered ruleset, exactly like S5.
  #
  # Root does not lose its ability to fetch — it loses its ability to fetch DIRECTLY. The hop to
  # the proxy is loopback, which `oifname "lo" accept` already covers.
  #
  # The proxy uid is NUMERIC and comes from config, so the rule and the user it names cannot
  # disagree — both render from agentos.fetchProxy.uid. A NAME would resolve on the box and FAIL
  # in the sandboxed `nft --check` gate, which has no user database; that gate is the whole reason
  # fetch-proxy.nix pins a static uid instead of taking an auto-allocated one.
  #
  # What this pair does NOT do: the hostname check is not here and cannot be — nftables cannot
  # name hosts. This grant is exactly as wide in DESTINATION as the one it replaces. It is
  # narrower only in WHO HOLDS IT: one hardened daemon whose single job is to refuse a hostname,
  # instead of every process running as root. The evidence that the refusal works is behavioural
  # (tests/fetch-proxy-allowlist.nix), never this line.
  fetchUid = if fp.enable then toString fp.uid else "0";
  fetchAccepts = lib.concatStringsSep "\n          " [
    "meta nfproto ipv4 meta skuid ${fetchUid} udp dport 53 accept"
    "meta nfproto ipv4 meta skuid ${fetchUid} tcp dport { 53, 443 } accept"
  ];

  # WP-S5 follow-up (Geist MEDIUM on PR #89, 2026-08-14). The proxy listens on loopback and
  # `oifname "lo" accept` below is UID-BLIND, tinyproxy's `Allow 127.0.0.1` is uid-blind, and
  # nothing else scoped the proxy port — so the untrusted agent could CONNECT through the proxy
  # and reach every ALLOWLISTED host.
  #
  # I originally wrote that off as "not a hole: the agent is confined to the same names root is."
  # That is a defence on the NAMES axis that silently repeals the invariant on the EGRESS axis.
  # INV-2 for the agent is ZERO egress, not allowlisted egress, so "confined to the allowlist" is
  # a strict WEAKENING of the agent's confinement, not a null change. And it is not hypothetical:
  # fetch-proxy.nix's own option example lists `github.com`, and a CONNECT tunnel to an
  # interactive host that reflects a request body is an exfil channel for agent-chosen bytes.
  #
  # So: deny non-root clients the proxy port, ahead of the general loopback accept. Legitimate
  # clients are nix-daemon and fwupd, both uid 0; the proxy egresses OUTWARD, never to itself; and
  # agent IPC on every other loopback port (the local brain on 11434, mem, all of it) is untouched
  # because this names one dport.
  #
  # Deliberately NOT carrying the `meta nfproto ipv4` pin the accepts carry. Scoping an ACCEPT
  # narrowly is fail-closed; scoping a DROP narrowly is fail-OPEN — a v4-only drop would leave
  # ::1 open the day anything starts listening there. tinyproxy binds 127.0.0.1 only today, which
  # is a reason this is currently unreachable, not a reason to write the rule as if it always will
  # be.
  # Rendered INCLUDING its own comment lines, so that with fetchProxy disabled this contributes
  # literally nothing to the ruleset. PR #89 merged on the argument "agentos-sealed is
  # byte-unchanged"; that argument stays checkable by diffing the two rendered rulesets only if
  # the explanation travels inside the conditional with the rule. A comment emitted
  # unconditionally is inert to nftables and still falsifies the sentence a reviewer is verifying.
  # Joined with an explicit "\n          " the way `fetchAccepts` is, NOT written as a multi-line
  # `''` block: interpolating a multi-line string into the ruleset only indents its FIRST line,
  # and every line after it lands at column 0.
  proxyPortGuard = lib.optionalString fp.enable (lib.concatStringsSep "\n          " [
    "# WP-S5 (Geist MEDIUM on PR #89): the fetch-proxy port is carved OUT of the loopback"
    "# accept below for every non-root uid, BEFORE that accept can match. Rationale at the"
    "# `proxyPortGuard` binding in modules/clean-room.nix."
    "oifname \"lo\" tcp dport ${toString fp.port} meta skuid != 0 drop"
    ""
  ]);
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

          ${proxyPortGuard}# on-box IPC: the local brain (127.0.0.1:11434), mem, everything loopback
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
          # WP-S5, 2026-08-14: PR-K is now `agentos.fetchProxy` (modules/fetch-proxy.nix), and the
          # two accepts below are CONDITIONAL on it. With the proxy enabled they are not rendered
          # at all — uid 0 keeps no direct path to 53 or 443, and the identical grant is re-issued
          # to the proxy's dedicated uid a few lines down. That is the whole of the S5 rule delta:
          # the same two ports, moved from "whoever is root" to "the one process that checks the
          # hostname". Root still reaches the proxy, because that hop is loopback and `oifname
          # "lo" accept` above already covers it.
          #
          # SCOPE, stated rather than implied: this makes the proxy root's only path to the
          # INTERNET, not root's only path off the box. The mesh accept further down this chain
          # (contributed by mesh-wireguard-sealed.nix, `meta skuid 0 oifname <iface> accept`) is
          # untouched by S5, so a compromised root still reaches every mesh PEER directly, on any
          # port, with no hostname check. That is the mesh module's own documented residual and
          # closing it is not this work package — but "S5 landed" must not be read as "root cannot
          # egress without the proxy", because in the rendered ruleset it plainly can.
          #
          # The conditional is deliberately an EXCHANGE rendered from one option rather than two
          # independent toggles. A configuration that withdrew root's accepts without issuing the
          # proxy's would brick nixos-rebuild; one that issued both would be S1 with extra steps
          # and would read, in the rendered ruleset, exactly like S5.
          #
          # nfproto ipv4 PARITY (PR-A residual): every scoped skuid accept below carries
          # `meta nfproto ipv4` so it is EXPLICITLY v4-only, matching the v4-only doctrine in the
          # IPv6 note at the bottom of this chain. Today v6 egress is already dead (NDP is
          # default-dropped), so this changes no live behaviour — but WITHOUT it, the day someone
          # scopes in a link-local NDP allow per that note, these bare `skuid 0 … accept` rules
          # would silently start matching v6 too and widen root egress to 443/53/123 over IPv6.
          # The predicate keeps the accepts pinned to the family they were reasoned about.
          # `fetchAccepts` (let block) renders ONE of two pairs here: the interim skuid-0 pair, or
          # the WP-S5 proxy-uid pair that replaces it. Read the rendered ruleset, not this file,
          # to know which — `nix build .#nft-ruleset-sealed` prints it.
          ${fetchAccepts}
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
          # (a) INNER traffic onto the wg interface, SCOPED TO uid 0.
          #     The first draft of this line was a bare `oifname "${mw.interfaceName}" accept`,
          #     justified as "mesh-only by construction — what egresses via the wg interface can
          #     only reach the peers' allowedIPs (wg cryptokey routing), so this is not an
          #     internet path." That answers the INTERNET question and silently repeals the UID
          #     one. This chain's invariant, stated at the timesync rule above, is that "every
          #     other process (the agent, the model, any capability) stays zero-egress" — not
          #     merely no-internet-egress. A uid-blind accept hands the untrusted agent an
          #     all-port path to every mesh peer the moment mesh is enabled, and the mesh peers
          #     are the machines holding creds and DBs. Exfil to a peer is exfil.
          #     The cryptokey-routing argument is also root-mutable at RUNTIME (`wg set … allowed-ips
          #     0.0.0.0/0` + a route, no rebuild, no ruleset change) — so it rests the wall on a
          #     setting a compromised root rewrites, which is the exact threat the scoped skuid-0
          #     rules above exist to bound.
          #     Mesh access on the sealed box is HUMAN/ADMIN reachability (spec §WP-S1 "dev mesh
          #     access"), and its dominant direction is INBOUND — a peer SSHes in, and the reply
          #     traffic is already accepted by `ct state established` at the top of this chain,
          #     not by this rule. This rule covers only box-INITIATED mesh connections, which is
          #     admin tooling, i.e. uid 0. RULED (Fable gate, PR #86, 2026-08-14 — R1, binding):
          #     this rule stands as scoped; if any non-root sealed-box service is ever meant to
          #     initiate over the mesh it gets its own numeric-skuid line here (the timesync
          #     shape), never a re-widening of this one.
          #     `meta nfproto ipv4` added 2026-08-14 for parity with every other scoped accept in
          #     this chain (Fable advisory 2). It cannot change behaviour here — the uid-0 +
          #     oifname pair holds in both families — but a rule that is the lone exception to the
          #     chain's own doctrine invites the reader to conclude the doctrine is optional.
          meta nfproto ipv4 meta skuid 0 oifname "${mw.interfaceName}" accept
          # (b) OUTER encapsulated UDP to each peer endpoint. Kernel-generated (no owning
          #     socket), so the skuid scoping above can never match it — pinned instead to the
          #     exact daddr+dport of each configured peer, rendered from the module's
          #     literal-ipv4 endpoints. No peers with endpoints => no lines => no outer egress.
          #     SPORT PIN (added 2026-08-14, Fable gate MEDIUM on PR #86). daddr+dport alone is
          #     uid-blind, and "no cooperating receiver" is NOT a defence the threat model
          #     accepts: the datagrams leave the box carrying agent-chosen bytes and are
          #     capturable on-path and at the peer host, so departure is the exfil — the peer's
          #     wg never has to accept anything. Pinning `udp sport <listenPort>` closes it,
          #     because wg's kernel socket OWNS that port while the interface is up, so no
          #     unprivileged process can bind it to source-match. Both halves are asserted
          #     behaviourally in tests/egress-mesh-uid-scope.nix.
          #     RESIDUAL, stated rather than implied: the pin rests on wg holding the port. With
          #     the interface DOWN the port is free, an unprivileged process may bind it, and
          #     these lines then permit its datagrams to the peer endpoint. That window is
          #     narrow (mesh down => the accepts still render, since they key on config, not on
          #     interface state) and it is the honest limit of what a stateless L3/L4 rule can
          #     express about a socket's owner. Closing it properly wants either a cgroup/uid
          #     match nftables cannot apply to kernel-generated packets, or teardown of the
          #     accepts with the interface — S4/S6 territory, not this fix.
        ${lib.concatMapStringsSep "\n" (p:
            let ep = lib.splitString ":" p.endpoint; in
            "          meta nfproto ipv4 ip daddr ${lib.elemAt ep 0} udp sport ${toString mw.listenPort} udp dport ${lib.elemAt ep 1} accept"
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
