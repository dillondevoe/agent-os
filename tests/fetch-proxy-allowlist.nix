# tests/fetch-proxy-allowlist.nix — WP-S5 behavioural coverage: the hostname allowlist, and the
# withdrawal of uid 0's direct fetch path.
#
# WHY A VM CAN SAY ANYTHING HERE AT ALL. Spec §WP-S5 records that S5 "is the one item in Phase S
# that a VM cannot fully validate — DNS/SNI-based enforcement needs a real network path." That is
# true of the ACCEPTANCE (a live `nixos-rebuild switch` through the proxy against the real cache),
# and that acceptance stays an at-the-box session. It is NOT true of the PREDICATE. The question
# "does this proxy refuse a name that is not on the list" can be asked with no internet at all,
# and asked BETTER without one:
#
#     Every hostname in this test resolves to the SAME peer IP.
#
# So the only variable between a permitted fetch and a refused one is the NAME. A test that used
# real hosts would confound the name with the route, the DNS answer, and the peer's liveness —
# four ways to pass for the wrong reason. Here there is one. What this file cannot establish is
# that the real cache is reachable through the proxy, and it does not claim it.
#
# WHAT EACH LEG IS FOR, and specifically which failure it is the ONLY evidence against:
#
#   1. CONTROL — allowlisted name, through the proxy, 200. Without this every deny below passes on
#      a dead proxy or a dead peer. It runs FIRST, on purpose (the mesh test's leg-6 lesson: a
#      control taken after the reading cannot exonerate the reading).
#   2. THE PREDICATE — a non-allowlisted name, SAME IP, refused 403 by the proxy.
#   3. THE ANCHORING — `allowed.example.test.evil.test`, SAME IP, refused. tinyproxy compiles
#      filter entries with `regcomp`; an unanchored `allowed.example.test` is a SUBSTRING regex
#      and matches this host. That is a silent, complete allowlist bypass that reads as a correct
#      config in review, and leg 2 does NOT catch it — leg 2 passes just as happily against an
#      unanchored allowlist. This leg is the only thing standing between the module and that bug.
#      Run it against a `toPattern = h: h` and it goes green-to-red.
#   4. THE PORT PIN — allowlisted name, port 8443. tinyproxy's `check_allowed_connect_ports`
#      returns 1 unconditionally when no ConnectPort options are configured, i.e. ABSENT
#      configuration means EVERY port is tunnellable. Another default-permissive trap that leaves
#      no trace in the config file, so it gets its own leg.
#   5. THE RULE DELTA — uid 0 aiming straight at the peer, no proxy, must time out. This is the
#      half of S5 that lives in nftables rather than in tinyproxy: root's direct 443 accept is
#      GONE, so the allowlist cannot simply be walked around. Addressed by raw IP, never by name,
#      because uid 0 has also lost :53 and a name-based probe would fail at resolution — the
#      right answer for the wrong reason, and indistinguishable from the wall in the exit code.
#   6. The agent uid, same probe. Unchanged from WP-S1 and expected to pass trivially; kept
#      because S5 rewrites the accepts around it and a rewrite is exactly when an unrelated
#      predicate gets clipped.
#
# NOT COVERED, and deliberately: TLS. The proxy blind-tunnels after CONNECT and never sees a
# certificate, so there is nothing here for a TLS test to assert — trust in the fetched bytes
# rests on nix's narHash, not on this proxy. The legs below tunnel plain HTTP through CONNECT via
# `curl --proxytunnel`, which exercises the identical code path in tinyproxy (reqs.c dispatches on
# the CONNECT method, not on what flows afterwards) without standing up a CA in a fixture.
{ pkgs, baseModules }:
let
  # Build-time constant that must equal a RUNTIME fact owned by the test framework (it allocates
  # 192.168.1.N on the inter-node VLAN, ordered by node name: `peer` < `sealed`). Same rot risk as
  # the mesh test's endpoint pin — rename or reorder the nodes and every deny leg starts passing
  # for the wrong reason — so testScript asserts the equality before asserting anything else.
  peerIp = "192.168.1.1";
  proxyPort = 3128;
  proxyUid = 350;

  allowedHost = "allowed.example.test";
  blockedHost = "blocked.example.test";
  # Leg 3's whole point: a SUFFIX of this string is the allowlisted name, so an unanchored regex
  # allows it. It is what an attacker registers.
  suffixHost = "${allowedHost}.evil.test";
in
pkgs.testers.runNixOSTest {
  name = "agentos-fetch-proxy-allowlist";

  nodes.sealed = { ... }: {
    imports = baseModules ++ [{
      agentos.cleanRoom.sealed = true;
      agentos.fetchProxy = {
        enable = true;
        port = proxyPort;
        uid = proxyUid;
        allowedHosts = [ allowedHost ];
      };
    }];

    # THE CONTROL, expressed as configuration: all three names point at one address. Nothing in
    # this test can distinguish hosts by route, because there is only one route.
    networking.hosts."${peerIp}" = [ allowedHost blockedHost suffixHost ];

    virtualisation.memorySize = 2048;
    virtualisation.cores = 2;
    environment.systemPackages = [ pkgs.curl ];
    # See tests/egress-uid-scope.nix: NetworkManager otherwise claims eth1 and flushes the
    # framework's static VLAN address, leaving no route to the peer and turning every deny leg
    # into a false GREEN.
    networking.networkmanager.unmanaged = [ "interface-name:eth1" ];
  };

  # A plain box. No egress wall — every refusal in this test must come from the sealed node's own
  # proxy or its own output chain, or the result is unattributable.
  nodes.peer = { ... }: {
    networking.firewall.enable = false;
    # BIND 0.0.0.0, NOT ${peerIp}. `after = network.target` does NOT mean the VLAN address is
    # configured yet, so a listener that binds the literal dies at boot with EADDRNOTAVAIL — which
    # is exactly what happened here on the first run. The tempting fix, and the one carried over
    # from the mesh test, is `Restart = always`: retry until the address shows up. It works, and it
    # is a TRAP, because `wait_for_unit` is satisfied by a unit that is merely between crashes. On
    # the first run of this file `wait_for_unit("listen-443.service")` returned after 182s having
    # observed a crash-looping unit, the script proceeded, and leg 1 — the control — failed with
    # `CONNECT tunnel failed, response 500`, which reads as "the allowlist is broken" when the true
    # cause was "nothing was listening yet". Binding the wildcard removes the ordering dependency
    # entirely; Restart stays only as a belt. The real gate is wait_for_open_port below.
    systemd.services.listen-443 = {
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 -m http.server 443 --bind 0.0.0.0";
        Restart = "always";
        RestartSec = 1;
      };
    };
    # Leg 4 aims here. It must be LISTENING, so that a refusal on :8443 is attributable to the
    # ConnectPort pin and not to nothing being there — the same reason leg 2 needs leg 1.
    systemd.services.listen-8443 = {
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 -m http.server 8443 --bind 0.0.0.0";
        Restart = "always";
        RestartSec = 1;
      };
    };
  };

  testScript = ''
    start_all()
    # Wait on the PORT, at the address the probes actually use — not on the unit. An active unit is
    # not a live instrument: see the comment on listen-443 above. wait_for_open_port polls
    # `nc -z ${peerIp} <port>` on the peer, so it cannot be satisfied by a process that is starting,
    # crashing, or bound somewhere else.
    peer.wait_for_open_port(443, "${peerIp}")
    peer.wait_for_open_port(8443, "${peerIp}")
    sealed.wait_for_unit("tinyproxy.service")
    sealed.wait_for_unit("nftables.service")

    # ---- fixture integrity, before any reading -------------------------------------------------
    # The framework's VLAN allocation is a runtime fact this file hard-codes in two places (the
    # /etc/hosts map and every probe). If it ever stops being ${peerIp}, the deny legs would all
    # pass because nothing is reachable, and the suite would read as a stronger wall than it is.
    addrs = peer.succeed("ip -4 -o addr show dev eth1")
    assert "${peerIp}/" in addrs, (
        f"fixture drift: peer's VLAN address is not ${peerIp}, so every probe below is aimed at "
        f"nothing and every deny leg would pass for the wrong reason: {addrs!r}"
    )

    # The proxy must actually be running AS the uid the egress chain permits. If the module and
    # the ruleset disagreed about the number, the proxy would be walled in and leg 1 would fail —
    # but it would fail as "allowlist broken", which is the wrong diagnosis for a uid mismatch.
    uid = sealed.succeed("id -u tinyproxy").strip()
    assert uid == "${toString proxyUid}", (
        f"the proxy user's uid is {uid}, but the egress chain grants ${toString proxyUid}"
    )

    def via_proxy(host, port=443):
        """CONNECT <host>:<port> through the proxy. --proxytunnel forces the CONNECT method for an
        http:// URL, which is the code path an https:// fetch would take, minus the TLS."""
        return sealed.succeed(
            f"rc=0; curl -sS --max-time 15 -o /dev/null -x http://127.0.0.1:${toString proxyPort} "
            f"--proxytunnel http://{host}:{port}/ 2>&1 || rc=$?; echo EXIT=$rc"
        )

    # ---- leg 1: CONTROL — the allowlisted name works, end to end ------------------------------
    with subtest("leg 1: allowlisted host is proxied"):
        out = via_proxy("${allowedHost}")
        assert "EXIT=0" in out, (
            f"the CONTROL failed: ${allowedHost} could not be fetched through the proxy, so every "
            f"denial below is unattributable — a dead proxy denies perfectly: {out!r}"
        )

    # ---- leg 2: THE PREDICATE — same IP, unlisted name, refused -------------------------------
    with subtest("leg 2: non-allowlisted host is refused by the proxy"):
        out = via_proxy("${blockedHost}")
        assert "EXIT=0" not in out, (
            f"${blockedHost} was PROXIED. The allowlist is not being enforced — check that "
            f"FilterDefaultDeny is set (without it the same file is a BLOCKlist): {out!r}"
        )
        assert "403" in out, (
            f"${blockedHost} failed, but not by the filter (want a 403 from the proxy). A refusal "
            f"for any other reason would also fail this leg on a proxy that filters nothing: {out!r}"
        )

    # ---- leg 3: THE ANCHORING — the substring bypass --------------------------------------------
    with subtest("leg 3: a host with the allowlisted name as a SUFFIX is refused"):
        out = via_proxy("${suffixHost}")
        assert "EXIT=0" not in out, (
            f"${suffixHost} was PROXIED. This is the unanchored-regex bypass: tinyproxy compiles "
            f"allowlist entries with regcomp, so a bare '${allowedHost}' matches any host that "
            f"CONTAINS it. Leg 2 does not catch this. Check toPattern in modules/fetch-proxy.nix "
            f"still renders ^…$: {out!r}"
        )
        assert "403" in out, (
            f"${suffixHost} failed, but not by the filter: {out!r}"
        )

    # ---- leg 4: THE PORT PIN -------------------------------------------------------------------
    with subtest("leg 4: CONNECT to a non-443 port is refused even on an allowlisted host"):
        out = via_proxy("${allowedHost}", 8443)
        assert "EXIT=0" not in out, (
            f"CONNECT ${allowedHost}:8443 succeeded. Absent ConnectPort configuration means ALL "
            f"ports are tunnellable, so this leg failing means the pin is gone, not merely "
            f"loosened: {out!r}"
        )
        assert "403" in out, f"refused, but not by the port check: {out!r}"

    # ---- leg 5: THE RULE DELTA — uid 0 has no direct path --------------------------------------
    # Addressed by IP, never by name: uid 0 lost :53 in the same exchange, so a name-based probe
    # would die at resolution and produce an exit code indistinguishable from the wall.
    with subtest("leg 5: uid 0 cannot reach the peer directly, bypassing the proxy"):
        out = sealed.succeed(
            "rc=0; curl -sS --max-time 10 -o /dev/null http://${peerIp}:443/ || rc=$?; echo EXIT=$rc"
        )
        assert "EXIT=28" in out, (
            f"root's direct probe to the peer did not TIME OUT (want EXIT=28, the signature of a "
            f"silent drop). EXIT=0 means the S5 exchange did not withdraw root's 443 accept and "
            f"the allowlist can simply be walked around; EXIT=7 means the fixture has no route "
            f"and leg 5 proves nothing: {out!r}"
        )

    # ---- leg 6: the agent uid, unchanged from WP-S1 --------------------------------------------
    with subtest("leg 6: the agent uid still cannot reach the peer"):
        out = sealed.succeed(
            "rc=0; runuser -u agent -- curl -sS --max-time 10 -o /dev/null "
            "http://${peerIp}:443/ || rc=$?; echo EXIT=$rc"
        )
        assert "EXIT=28" in out, (
            f"the agent's direct probe did not time out (want EXIT=28): {out!r}"
        )

    # ---- leg 7: the agent cannot use the proxy either -------------------------------------------
    # The proxy listens on loopback and `oifname "lo" accept` is uid-BLIND, so the agent can reach
    # it. That is a path from the untrusted agent to the internet, bounded by the allowlist. It is
    # not a hole in S5 — the agent is confined to the same names root is — but it is a fact worth
    # asserting rather than discovering, so the behaviour is recorded either way.
    with subtest("leg 7: record whether the agent can reach the proxy over loopback"):
        out = sealed.succeed(
            "rc=0; runuser -u agent -- curl -sS --max-time 15 -o /dev/null "
            "-x http://127.0.0.1:${toString proxyPort} --proxytunnel http://${blockedHost}:443/ "
            "2>&1 || rc=$?; echo EXIT=$rc"
        )
        assert "EXIT=0" not in out, (
            f"the agent tunnelled to a NON-allowlisted host through the proxy. Whatever the "
            f"loopback story, the filter must apply to every client: {out!r}"
        )
  '';
}
