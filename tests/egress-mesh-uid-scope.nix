# tests/egress-mesh-uid-scope.nix — WP-S1 increment 2: behavioural coverage of the WireGuard
# mesh accepts in the agentos_egress output chain, over a REAL tunnel.
#
# WHY THIS EXISTS. `tests/egress-uid-scope.nix` (increment 1) proved the uid scope holds on the
# ordinary egress path, but it composes baseModules WITHOUT meshWireguard, so it says nothing
# about the two accepts the mesh adds — and those are the accepts that had the bug:
#
#     2fb94c6 fixed `oifname "wg-mesh" accept`  ->  `meta skuid 0 oifname "wg-mesh" accept`
#
# The uid-blind version handed the untrusted agent an all-port path to every mesh peer, which is
# to say to the machines holding the creds and the DBs. That fix has been ratified (Geist R1,
# Fable gate PR #86) but until this file it was only ever PARSE-verified: `nft --check` proves a
# ruleset is well-formed, never that a packet dies. **Leg 2 below is the direct behavioural
# regression test for that fix** — run it against the pre-2fb94c6 rule and it goes green-to-red.
#
# THE CONTROL IS THE TEST. A tunnel that never came up denies the agent perfectly, and would also
# deny root. Leg 1 sends the SAME request over the SAME tunnel to the SAME port as leg 2, seconds
# apart, differing only in uid. That is what makes leg 2 attributable to the uid predicate rather
# than to a broken fixture — the failure mode this suite has already been bitten by twice.
#
# WHAT IS AND IS NOT COVERED:
#   - COVERED: accept (a), the INNER rule — `meta skuid 0 oifname wg-mesh accept`. Root reaches a
#     peer across the tunnel; the agent cannot, on the same path.
#   - COVERED INCIDENTALLY: accept (b), the per-peer OUTER UDP pin, in its POSITIVE half only.
#     Nothing handshakes without it, so leg 1 succeeding proves it permits the pinned endpoint.
#   - NOT COVERED: the NEGATIVE half of (b) — that UDP to an UNconfigured endpoint is refused. I
#     tried and withdrew it. A `drop` in this chain is silent (leg 2's TCP probe times out rather
#     than getting EPERM), so an unanswered UDP send exits 0 whether it was accepted or dropped:
#     the sender cannot tell. Asserting `nc -u ... ` fails would therefore be unfalsifiable — it
#     would pass on a correctly pinned wall and on no wall at all. Doing it properly needs a UDP
#     request/response pair with a positive control on the same shape (an echo responder on the
#     pinned port), which the wg port cannot provide since it answers only wg. Left undone rather
#     than done vacuously; belongs to WP-S4's battery, which can afford the fixture.
#   - NOT COVERED: that (b) is uid-blind by necessity (kernel-generated encapsulated packets carry
#     no skuid, so `meta skuid` cannot match them). Geist's review advisory 1 names the residual:
#     any uid may emit UDP to a configured peer endpoint:port, with no cooperating receiver, since
#     the peer's kernel discards non-wg traffic on that port. That is a covert-channel residual,
#     not an exfil path, and it is a property of nftables' matching model — there is no ruleset
#     change that would make it testable-and-passing.
#   - NOT COVERED: hostname allowlisting (nft cannot name hosts; WP-S5's fetch-proxy) — same
#     residual as increment 1.
{ pkgs, baseModules }:
let
  # THROWAWAY FIXTURE KEYPAIRS, generated for this file and used nowhere else, ever. This does not
  # breach the "NO CREDENTIALS IN THE REPO" constraint: that rule protects the real mesh map and
  # the real box key, both of which stay out-of-band (privateKeyFile, S6 runbook). A test needs a
  # working handshake, a handshake needs matching keys, and keys that exist only to make two
  # ephemeral VMs talk to each other are fixture data, not credentials. Treat any appearance of
  # these strings outside this file as a bug.
  sealedPriv = "KILgLiCry9YbWvEteeaCY8pfG2uaSWcewIblckPYVnA=";
  sealedPub = "6Q35IEWag6KH8WaNB+c4j9Rw1ldJiVXz1dMi7cBd0hg=";
  meshPriv = "eIpfUpV3os3oHk4/EYXORo9sMYd4X07TqDHHvY6mmXQ=";
  meshPub = "1y5D6eB9jr8B+QviSA6JXqMNIchzdpdJHu7FwgFfSVg=";

  # The peer's endpoint has to be a build-time literal, because that is the entire point of the
  # per-peer OUTER pin — clean-room renders `ip daddr <this> udp dport <this>` into the chain. But
  # the value is a RUNTIME fact owned by the test framework (it hands out 192.168.1.N on the
  # inter-node VLAN, ordered by node name, so `meshpeer` < `sealed`). A build-time constant that
  # must equal a runtime fact is exactly the shape that rots silently: reorder the nodes, rename
  # one, or have the framework change its allocation, and the pin stops matching the peer. The
  # handshake would then fail and every deny leg would pass for the wrong reason. testScript
  # asserts this equality before asserting anything else.
  meshPeerVlanIp = "192.168.1.1";
  wgPort = 51820;
  sealedTunIp = "10.100.0.2";
  meshTunIp = "10.100.0.1";
in
pkgs.testers.runNixOSTest {
  name = "agentos-egress-mesh-uid-scope";

  nodes.sealed = { ... }: {
    imports = baseModules ++ [{
      agentos.cleanRoom.sealed = true;
      agentos.meshWireguard = {
        enable = true;
        address = [ "${sealedTunIp}/24" ];
        listenPort = wgPort;
        # FIXTURE-ONLY and deliberately not the deployment shape. `environment.etc` with an
        # explicit mode writes a real 0600 file at activation rather than a symlink into the
        # store, which matters: the module asserts `!hasPrefix storeDir privateKeyFile`, and that
        # assertion inspects the PATH STRING. A store-SYMLINKED /etc entry would satisfy it while
        # putting the key in world-readable /nix/store anyway. Not exploited here; reported to
        # Geist as a low advisory against the module.
        privateKeyFile = "/etc/wireguard-fixture.key";
        peers = [{
          publicKey = meshPub;
          endpoint = "${meshPeerVlanIp}:${toString wgPort}";
          allowedIPs = [ "${meshTunIp}/32" ];
        }];
      };
    }];
    environment.etc."wireguard-fixture.key" = { text = sealedPriv; mode = "0600"; };
    virtualisation.memorySize = 2048;
    virtualisation.cores = 2;
    environment.systemPackages = [ pkgs.curl ];
    # See the long note in tests/egress-uid-scope.nix: NetworkManager otherwise claims eth1 and
    # flushes the framework's static VLAN address, leaving no route to the peer and turning every
    # deny leg into a false GREEN. Fixture-only; the chain keys on uid and port.
    networking.networkmanager.unmanaged = [ "interface-name:eth1" ];
  };

  # An ordinary box on the mesh — NOT sealed, no egress wall. Every denial in this test must come
  # from the sealed node's own output chain; a wall on both ends would make the result
  # unattributable. Listener is on the TUNNEL address only, so nothing can reach it except across
  # the tunnel (a VLAN-routed hit would otherwise pass leg 1 without the mesh working at all).
  nodes.meshpeer = { ... }: {
    networking.firewall.enable = false;
    networking.wireguard.enable = true;
    networking.wireguard.interfaces.wg-mesh = {
      ips = [ "${meshTunIp}/24" ];
      listenPort = wgPort;
      privateKeyFile = "/etc/wireguard-fixture.key";
      peers = [{
        publicKey = sealedPub;
        allowedIPs = [ "${sealedTunIp}/32" ];
      }];
    };
    environment.etc."wireguard-fixture.key" = { text = meshPriv; mode = "0600"; };
    systemd.services.listen-tun-443 = {
      wantedBy = [ "multi-user.target" ];
      after = [ "wireguard-wg-mesh.service" ];
      serviceConfig.ExecStart =
        "${pkgs.python3}/bin/python3 -m http.server 443 --bind ${meshTunIp}";
    };
  };

  testScript = ''
    start_all()
    sealed.wait_for_unit("multi-user.target")
    meshpeer.wait_for_unit("multi-user.target")

    # The build-time pin must equal the runtime address, or accept (b) points at nothing and the
    # handshake below fails for a reason that has nothing to do with the wall. Assert before
    # asserting.
    meshpeer.succeed("ip -4 -o addr show scope global | grep -q ' ${meshPeerVlanIp}/'")

    sealed.succeed("test -e /run/agentos-sealed-ok")
    sealed.succeed("nft list table inet agentos_egress >/dev/null")
    # The rule under test is present in the rendered chain, scoped. Structural, not behavioural,
    # but it localises a failure if the legs go red. NOTE the ordering consequence, learned the
    # hard way while verifying the regression claim below: on a genuine uid-blind regression THIS
    # line fails first and the run never reaches leg 2, so a red here does not tell you whether
    # the behavioural half still works. To exercise leg 2 against the old rule you must disable
    # this assertion as well. Kept anyway — a fast structural localiser is worth more than the
    # cost of that one manual step during a deliberate counterfactual.
    sealed.succeed("nft list table inet agentos_egress | grep -q 'skuid 0 oifname \"wg-mesh\"'")

    # Tunnel up, with a real handshake. `wg show latest-handshakes` returns 0 until one completes,
    # so this distinguishes "interface exists" from "peers are actually talking" — the former is
    # what a broken fixture gives you, and it denies everything.
    sealed.wait_for_unit("wireguard-wg-mesh.service")
    meshpeer.wait_for_unit("wireguard-wg-mesh.service")
    sealed.succeed("ping -c1 -W2 ${meshTunIp} >/dev/null")
    sealed.wait_until_succeeds(
        "test $(wg show wg-mesh latest-handshakes | awk '{print $2}') -ne 0", timeout=30
    )
    meshpeer.wait_until_succeeds(
        "curl -sS --max-time 2 -o /dev/null http://${meshTunIp}:443/", timeout=30
    )
    print(sealed.succeed("ip -4 -o addr show; ip -4 route; wg show"))

    with subtest("1. CONTROL — uid 0 reaches the peer ACROSS THE TUNNEL"):
        # Proves three things at once: accept (a) permits uid 0 onto wg-mesh, accept (b) permits
        # the outer UDP to the pinned endpoint, and the tunnel carries traffic. Without this, leg
        # 2 is satisfied by any broken link and the test is theatre.
        sealed.succeed("curl -sS --max-time 10 -o /dev/null http://${meshTunIp}:443/")

    with subtest("2. the agent is DENIED the same peer, same port, same tunnel — accept (a)"):
        # THE ASSERTION. Identical request to leg 1, differing only in uid. Against the
        # pre-2fb94c6 uid-blind `oifname "wg-mesh" accept` this SUCCEEDS; that is the regression
        # this file exists to catch. A DROP times out (curl 28); an instant exit means the route
        # or the tunnel died between the legs, not that the wall held.
        sealed.fail(
            "runuser -u agent -- curl -sS --max-time 10 -o /dev/null http://${meshTunIp}:443/"
        )

    with subtest("3. CONTROL — the agent's stack is alive (it is the WALL denying, not the user)"):
        sealed.succeed(
            "systemd-run --unit=lo-probe ${pkgs.python3}/bin/python3 -m http.server 8081 "
            "--bind 127.0.0.1"
        )
        sealed.wait_until_succeeds(
            "curl -sS --max-time 2 -o /dev/null http://127.0.0.1:8081/", timeout=30
        )
        sealed.succeed(
            "runuser -u agent -- curl -sS --max-time 10 -o /dev/null http://127.0.0.1:8081/"
        )

    with subtest("4. the agent cannot reach the peer off-tunnel either"):
        # Closes the obvious way around leg 2: if the agent were denied the tunnel but allowed the
        # VLAN, exfil-to-a-peer would still be open. Same denial, different path.
        sealed.fail(
            "runuser -u agent -- curl -sS --max-time 10 -o /dev/null "
            "http://${meshPeerVlanIp}:443/"
        )
  '';
}
