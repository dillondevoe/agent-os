# tests/egress-uid-scope.nix — nixosTest for WP-S1's acceptance criterion: the scoped skuid-0
# egress rules are verified by PACKET FATE, not by reading the ruleset.
#
# WHY THIS EXISTS. Before this test, every piece of evidence for the egress wall was structural:
#   - `nft-ruleset-{sealed,unsealed}` run the nftables module's own `nft --check` checkPhase.
#     That is a PARSE gate. A ruleset that parses perfectly and accepts everything passes it.
#   - `tests/seal-faildown.nix` asserts the table EXISTS (`nft list table inet agentos_egress`)
#     and is gone after teardown. It never sends a packet and never checks a packet's fate.
#   - `grep -rln skuid --include=*.nix .` matched two MODULES and zero tests.
# So the spec's WP-S1 acceptance line — "a scoped-skuid integration test confirms `sudo curl
# <arbitrary-host>` fails post-seal" — was unmet, and the wall's load-bearing property (uid
# scoping) had never been observed holding. This closes that.
#
# THE CONTROL MATTERS AS MUCH AS THE DENIALS. A wall that drops EVERYTHING passes every deny
# assertion while being broken (no nix-daemon fetches, no fwupd, a box that cannot rebuild out).
# Leg 1 is therefore a positive: uid 0 to :443 must SUCCEED. Without it, a totally-dead network
# is indistinguishable from a correctly-scoped one, and this test would be theatre.
#
# WHAT IS AND IS NOT COVERED (stated so no coverage is claimed silently):
#   - COVERED: uid scoping (agent vs root) and port scoping (443 vs arbitrary), against a real
#     off-box destination, on the SEALED variant, plus a loopback control proving the agent's
#     networking is alive and it is the WALL denying it.
#   - NOT COVERED — hostname allowlisting. nftables cannot name hosts, so uid 0 reaching :443 on
#     ANY host is the documented HONEST RESIDUAL in clean-room.nix, closed later by the PR-K
#     fetch-proxy (WP-S5). "Arbitrary host" in the spec's acceptance line is therefore tested as
#     arbitrary PORT — the strongest form the current rule can actually enforce. Do not read leg
#     1 as "the box may only reach the substituter": it may reach anything on 443.
#   - NOT COVERED — the WireGuard mesh accepts (`meta skuid 0 oifname wg-mesh accept` and the
#     per-peer outer-UDP pins). Those need a wg keypair + peer topology in the fixture, which the
#     "no credentials in the repo" constraint shapes; increment 2, tracked separately. This node
#     composes baseModules WITHOUT meshWireguard, matching tests/seal-faildown.nix's shape.
#   - NOT COVERED — DHCP and timesync accepts (uid 0 :67, uid 154 :53/:123). Same harness would
#     reach them; not in WP-S1's acceptance line.
{ pkgs, baseModules }:
pkgs.testers.runNixOSTest {
  name = "agentos-egress-uid-scope";

  nodes.sealed = { ... }: {
    imports = baseModules ++ [ { agentos.cleanRoom.sealed = true; } ];
    virtualisation.memorySize = 2048;
    virtualisation.cores = 2;
    # curl is the probe; pin it into the image rather than depending on the base closure
    # happening to carry it (a missing curl would fail every leg and read as a passing wall).
    environment.systemPackages = [ pkgs.curl ];
    # FIXTURE-ONLY, and load-bearing for this test being meaningful at all. The test framework
    # statically addresses the inter-node VLAN (eth1 = 192.168.1.x) at boot, but this variant
    # runs NetworkManager, which then claims eth1, fails to activate it ("Activation: failed for
    # connection 'Wired connection 1'") and FLUSHES the address. The sealed node is then left
    # with no route to the peer at all, and every probe below fails instantly with curl exit 7 —
    # which is indistinguishable from a wall denial unless you read the L3 dump. That would make
    # legs 2/4/5 pass for the wrong reason: a false GREEN on the exact assertions this test
    # exists to make. Handing eth1 to the framework does not weaken the system under test — the
    # egress chain keys on uid and port, and on no interface except `lo` (and `wg-mesh`, which
    # this increment does not cover). NM stays enabled, so the sealed posture is unchanged.
    networking.networkmanager.unmanaged = [ "interface-name:eth1" ];
  };

  # The off-box destination. Two listeners: :443 (inside the scoped allow) and :8080 (outside
  # it). Firewall off — every denial in this test must come from the SEALED node's own output
  # chain, never from the peer refusing the connection, or the test would pass with the wall
  # switched off entirely.
  nodes.peer = { ... }: {
    networking.firewall.enable = false;
    systemd.services.listen-443 = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 -m http.server 443 --bind 0.0.0.0";
    };
    systemd.services.listen-8080 = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 -m http.server 8080 --bind 0.0.0.0";
    };
  };

  testScript = ''
    start_all()
    sealed.wait_for_unit("multi-user.target")
    peer.wait_for_unit("listen-443.service")
    peer.wait_for_unit("listen-8080.service")

    # Resolve the peer by ADDRESS, not by name: a name would route the probe through glibc
    # resolution, and DNS egress is itself uid-scoped here — a failed lookup and a dropped
    # packet are the same curl exit code, so a hostname would make every deny leg ambiguous.
    # Exclude 10.0.2.0/24: that is QEMU's user-mode NAT range, and EVERY node in a nixosTest
    # holds the identical address 10.0.2.15 on it. Picking it makes the sealed node probe
    # ITSELF, so leg 1 fails with "connection refused" and the whole test reads as a wall that
    # blocks root — a false PASS on legs 2/4/5 for a fixture bug. The inter-node VLAN address
    # is the only one that is actually off-box.
    peer_ip = peer.succeed(
        "ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 "
        "| grep -v '^10\\.0\\.2\\.' | head -1"
    ).strip()
    print(f"peer address under test: {peer_ip}")
    assert peer_ip, "no off-box peer address found; the fixture, not the wall, is broken"
    # ...and prove it is genuinely off-box: if the sealed node owns this address too, every
    # denial below would be self-directed and unattributable to the egress chain.
    sealed.fail(f"ip -4 -o addr show scope global | grep -q ' {peer_ip}/'")

    # Both listeners are reachable from the peer's own side, so a later failure on the sealed
    # node is attributable to the sealed node. Without this, a dead listener reads as a wall.
    peer.succeed(f"curl -sS --max-time 5 -o /dev/null http://{peer_ip}:443/")
    peer.succeed(f"curl -sS --max-time 5 -o /dev/null http://{peer_ip}:8080/")

    sealed.succeed("test -e /run/agentos-sealed-ok")
    sealed.succeed("nft list table inet agentos_egress >/dev/null")

    # Diagnostic, not an assertion: a DROP shows up as a curl TIMEOUT, while "no address on the
    # test VLAN" shows up as an instant exit 7. Those two are trivially confused and only one of
    # them is about the wall, so dump the sealed node's actual L3 state into the log.
    print(sealed.succeed("ip -4 -o addr show; ip -4 route; systemctl is-active NetworkManager || true"))

    with subtest("1. CONTROL — uid 0 reaches :443 (the wall is scoped, not merely dead)"):
        sealed.succeed(f"curl -sS --max-time 10 -o /dev/null http://{peer_ip}:443/")

    with subtest("2. uid 0 is DENIED an arbitrary port (the acceptance line: root cannot egress anywhere)"):
        sealed.fail(f"curl -sS --max-time 10 -o /dev/null http://{peer_ip}:8080/")

    with subtest("3. CONTROL — the AGENT can reach loopback (its stack works; the wall is what denies)"):
        # Proves legs 4/5 are the WALL denying, not a broken user, a missing curl, or a dead
        # network stack. Must be a real listener the agent actually reaches — an `|| true` here
        # would make the control vacuous and legs 4/5 unfalsifiable.
        sealed.succeed(
            "systemd-run --unit=lo-probe ${pkgs.python3}/bin/python3 -m http.server 8081 --bind 127.0.0.1"
        )
        sealed.wait_until_succeeds(
            "curl -sS --max-time 2 -o /dev/null http://127.0.0.1:8081/", timeout=30
        )
        sealed.succeed("runuser -u agent -- curl -sS --max-time 10 -o /dev/null http://127.0.0.1:8081/")

    with subtest("4. the agent is DENIED :443 — the UID scope, not just the port scope"):
        # The load-bearing assertion. :443 is open to uid 0 (leg 1), so if this succeeded the
        # rule would be a PORT allow wearing a uid predicate, and every unprivileged process on
        # the box — the agent, the model, any capability — would have an HTTPS path off it.
        sealed.fail(f"runuser -u agent -- curl -sS --max-time 10 -o /dev/null http://{peer_ip}:443/")

    with subtest("5. the agent is DENIED an arbitrary port"):
        sealed.fail(f"runuser -u agent -- curl -sS --max-time 10 -o /dev/null http://{peer_ip}:8080/")
  '';
}
