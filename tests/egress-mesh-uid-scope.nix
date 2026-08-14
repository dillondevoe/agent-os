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
#   - COVERED: accept (b), the per-peer OUTER UDP pin, BOTH halves. Positive: nothing handshakes
#     without it, so leg 1 proves it permits the pinned endpoint. Negative: legs 4 and 5 prove an
#     agent-sourced datagram to that same endpoint never leaves the box, which is the SPORT PIN
#     added 2026-08-14 for the Fable gate's MEDIUM on PR #86.
#
#     I withdrew an earlier version of this on the belief that a drop here is SILENT for UDP —
#     that `sendto()` returns 0 whether the datagram was accepted or dropped, making any
#     sender-side assertion pass on a pinned wall and on no wall at all. That belief was WRONG,
#     and the test is what corrected it: udp_sendmsg returns the netfilter verdict up the
#     syscall, so the agent's probe gets EPERM. TCP behaves the other way (tcp_connect ignores
#     the transmit error and retransmits), which is why leg 2 sees a timeout and leg 5 sees an
#     error — the mechanism is written at leg 5. Leg 5 therefore asserts BOTH: the EPERM at the
#     sender, with a same-shape control and a check of WHICH errno, and the absence of the marker
#     at the RECEIVER (tcpdump on the peer, with a live-instrument control so "marker absent"
#     cannot mean "nothing was watching").
#   - COVERED, contrary to what I told the gate: the negative half of the outer pin's DADDR
#     scoping — uid 0 aiming UDP at an unconfigured address is refused. I scoped this to S4 as
#     "untestable by construction" on the same wrong premise above. It is a two-line assertion.
#   - RESIDUAL, not a gap in this test: the sport pin rests on wg's kernel socket OWNING
#     listenPort, which leg 4 asserts directly rather than assumes. With the interface DOWN that
#     port is free and the accepts still render, so an unprivileged process could bind it and
#     source-match. Named in clean-room.nix at the rule; closing it wants accept-teardown tied to
#     interface state (S4/S6), not a rule change.
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
  # single source of truth for the two things leg 7 manipulates on the box
  keyPath = "/etc/wireguard-fixture.key";
  ifName = "wg-mesh";  # meshWireguard.interfaceName default
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
        # putting the key in world-readable /nix/store anyway. Reported to the Fable gate and
        # RULED 2026-08-14: the string check stays as a typo net, the guarantee moved to a
        # resolution-time preStart guard. Leg 7 drives that guard to red on all three branches —
        # including this exact symlink bypass, which is why the fixture keeps the honest 0600 file.
        privateKeyFile = keyPath;
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
      privateKeyFile = keyPath;
      peers = [{
        publicKey = sealedPub;
        allowedIPs = [ "${sealedTunIp}/32" ];
      }];
    };
    environment.etc."wireguard-fixture.key" = { text = meshPriv; mode = "0600"; };
    # Receiver-side observation for the sport-pin legs. NOTE (corrected 2026-08-14): this comment
    # used to justify itself with "the SENDER cannot tell an accepted UDP datagram from a dropped
    # one" — the same wrong premise the header now retracts. The sender CAN tell: udp_sendmsg
    # returns the netfilter verdict, so leg 5 gets EPERM. The capture is kept anyway, because two
    # instruments on opposite ends of the path beat one, and marker-absent-at-the-receiver is what
    # rules out "refused for some sender-local reason." Kept for the right reason now, not the
    # wrong one. (Carried lines are never re-verified — this one survived the header's correction
    # by one cycle and is exactly the scar it names.)
    environment.systemPackages = [ pkgs.tcpdump ];
    systemd.services.listen-tun-443 = {
      wantedBy = [ "multi-user.target" ];
      after = [ "wireguard-wg-mesh.service" ];
      serviceConfig.ExecStart =
        "${pkgs.python3}/bin/python3 -m http.server 443 --bind ${meshTunIp}";
    };
    # Leg 6's instrument (Fable LOW, PR #87 gate). Without a listener on the VLAN address, a
    # wall-PERMITTED probe to ${meshPeerVlanIp}:443 gets an instant RST and leg 6's bare fail()
    # is satisfied by connection-refused — i.e. it would pass just as well with no wall at all.
    # With this bound, refusal is off the table: the only way the agent gets nothing back is the
    # drop. Leg 6 now asserts curl 28 against it.
    systemd.services.listen-vlan-443 = {
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      serviceConfig = {
        ExecStart =
          "${pkgs.python3}/bin/python3 -m http.server 443 --bind ${meshPeerVlanIp}";
        # The VLAN address is configured by the test harness on eth1, and this unit can reach
        # ExecStart before that happens — `--bind` then dies with EADDRNOTAVAIL and stays dead,
        # which cost a VM run. Retry instead of guessing the ordering: `network.target` does not
        # mean "addresses are up," and the sibling tunnel listener only works because it orders on
        # wireguard-wg-mesh.service, which genuinely does imply its address exists.
        Restart = "always";
        RestartSec = 1;
      };
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

    with subtest("4. the agent cannot BIND the wg listen port while the interface is up"):
        # First half of the sport pin's defence (Fable gate MEDIUM, PR #86). The pin is only
        # worth anything because wg's kernel socket OWNS listenPort, so an unprivileged process
        # cannot source-match it. That is an assumption about socket ownership, not about
        # nftables, so assert it directly rather than reasoning about it.
        sealed.fail(
            "runuser -u agent -- ${pkgs.python3}/bin/python3 -c "
            "'import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
            "s.bind((\"0.0.0.0\", 51820))'"
        )
        # CONTROL: the same bind on a free port succeeds, so leg 4 is the PORT being held and
        # not the agent being unable to bind anything at all (a sandbox, a missing capability, a
        # broken interpreter would all produce the same red).
        sealed.succeed(
            "runuser -u agent -- ${pkgs.python3}/bin/python3 -c "
            "'import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
            "s.bind((\"0.0.0.0\", 51999))'"
        )

    with subtest("5. the agent's UDP to the peer endpoint is REFUSED — the sport pin"):
        # Second half of the pin's defence, and the leg that corrected a premise of mine. I wrote
        # it expecting the drop to be SILENT — sendto() returning 0 whether the datagram was
        # accepted or dropped — because that is what leg 2 shows for TCP, which times out rather
        # than erroring. That generalisation was wrong, and the mechanism is worth recording here
        # since it is exactly why these two legs use different instruments:
        #   UDP: udp_sendmsg -> ip_send_skb returns the netfilter verdict straight up the syscall,
        #        so a policy drop surfaces to userspace as EPERM. Falsifiable at the sender.
        #   TCP: tcp_connect() ignores that same error from the SYN transmit and lets the
        #        retransmit timer run, so connect() blocks and you observe only a timeout.
        # So the sender-side assertion is available AND sharper — provided it checks WHICH error
        # and has a control of the same shape. Both are below. The receiver-side capture is kept
        # as well: it is the only evidence that survives the kernel reporting an error it did not
        # act on.
        #
        # CONTROL, same shape: same user, same socket type, same syscall, allowed destination.
        # Without it, an EPERM from a sandbox, a dropped capability or a broken interpreter would
        # read as "the wall denied it".
        sealed.succeed(
            "runuser -u agent -- ${pkgs.python3}/bin/python3 -c "
            "'import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
            "s.sendto(b\"CONTROL\", (\"127.0.0.1\", 9))'"
        )
        meshpeer.succeed(
            "systemd-run --unit=cap ${pkgs.tcpdump}/bin/tcpdump -i eth1 -n -w /tmp/cap.pcap "
            "udp port 51820"
        )
        meshpeer.sleep(2)
        # The agent emits a marked datagram straight at the peer's wg endpoint. Its source port
        # is whatever the kernel hands out — never 51820, per leg 4 — so the pinned rule does not
        # match it and the chain's policy drop takes it.
        err = sealed.fail(
            "runuser -u agent -- ${pkgs.python3}/bin/python3 -c "
            "'import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
            "s.sendto(b\"AUGURPROBEAUGURPROBE\", (\"${meshPeerVlanIp}\", 51820))' 2>&1"
        )
        # WHICH failure. A fixture with no route to the VLAN fails this same command with
        # ENETUNREACH and would otherwise be indistinguishable from a wall doing its job — the
        # trap that made an earlier version of this file's deny legs pass on a dead network.
        assert "Errno 1" in err or "PermissionError" in err, (
            f"the agent's UDP was refused, but not by the wall: {err!r}"
        )
        # CONTROL for the instrument: real encapsulated wg traffic, generated by pinging across
        # the tunnel, MUST show up in the same capture. Without this, a tcpdump on the wrong
        # interface — or one that never started — reports "no marker found" and the leg passes
        # while observing nothing.
        sealed.succeed("ping -c3 -W2 ${meshTunIp} >/dev/null")
        meshpeer.sleep(2)
        meshpeer.succeed("systemctl stop cap.service || true")
        captured = meshpeer.succeed(
            "${pkgs.tcpdump}/bin/tcpdump -r /tmp/cap.pcap -n 2>/dev/null | wc -l"
        ).strip()
        print(f"packets captured on the peer's wg port: {captured}")
        assert int(captured) > 0, (
            "capture is empty — the instrument, not the wall, is what this leg measured"
        )
        meshpeer.fail(
            "${pkgs.tcpdump}/bin/tcpdump -r /tmp/cap.pcap -A -n 2>/dev/null | grep -q AUGURPROBE"
        )
        # ADVISORY 1's negative half, restored. I told the gate this was untestable by
        # construction and it is not: the outer accepts are pinned per-peer to a daddr, so uid 0
        # aiming the same UDP at an UNCONFIGURED address must be refused, and the same EPERM that
        # falsified the leg above falsifies this. It is uid 0 deliberately — the point is that
        # the outer pin is narrow, not that the agent is contained.
        unconf = sealed.fail(
            "${pkgs.python3}/bin/python3 -c "
            "'import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
            "s.sendto(b\"AUGURPROBEAUGURPROBE\", (\"192.168.1.99\", 51820))' 2>&1"
        )
        assert "Errno 1" in unconf or "PermissionError" in unconf, (
            f"uid 0's UDP to an unconfigured peer was refused, but not by the wall: {unconf!r}"
        )

    with subtest("6. the agent cannot reach the peer off-tunnel either"):
        # Closes the obvious way around leg 2: if the agent were denied the tunnel but allowed the
        # VLAN, exfil-to-a-peer would still be open. Same denial, different path.
        #
        # This leg used to be a bare fail() (Fable LOW, PR #87 gate — correct catch). A bare
        # fail() here does not discriminate its failure mode: the peer's only listener bound the
        # TUNNEL address, so a wall-PERMITTED probe to the VLAN address drew an instant RST, and
        # connection-refused satisfies fail() exactly as well as a drop does. The property was
        # structurally true, but the leg was not the thing proving it — the trap this file's own
        # leg 5 note teaches, reproduced two subtests later.
        #
        # Fixed on both ends: meshpeer now binds ${meshPeerVlanIp}:443 (see listen-vlan-443, so
        # refusal is impossible), and the exit code is asserted rather than merely nonzero.
        # 28 = timed out with nothing back = the SYN died in the wall. 7 would mean refused or
        # unreachable; a live listener rules out refused, and the wg handshake above — which
        # crosses this same VLAN as outer UDP — rules out "no L3 path." So a 7 here is a broken
        # fixture and MUST NOT read as a pass. Were the wall to permit this probe, the listener
        # would answer and the exit code would be 0, which also fails the assertion: the leg now
        # discriminates in both directions rather than accepting any nonzero.
        #
        # RESIDUAL, precisely: the handshake proves the VLAN carries UDP:51820, not TCP:443.
        # A path broken for TCP-only, non-wall reasons would still read as 28. Closing that
        # needs a permitted-TCP control, which cannot exist from `sealed` while the wall stands
        # — the same shape as the wg-down residual named at the rules themselves.
        # CONTROL FIRST — establish the instrument is live BEFORE taking the reading. If
        # listen-vlan-443 were dead, the agent's timeout below would be over-attributed to the
        # wall, which is the very error this leg is being fixed for. Run from meshpeer itself,
        # a vantage point the wall does not touch.
        meshpeer.wait_until_succeeds(
            "curl -sS --max-time 5 -o /dev/null http://${meshPeerVlanIp}:443/", timeout=30
        )
        # NOTE on the shell form: the driver executes with `set -e`, so a bare
        # `cmd; echo EXIT=$?` never reaches the echo — the non-zero curl aborts first and
        # succeed() reports the raw 28. Capturing via `|| rc=$?` keeps the failure inside a
        # `||` list, where set -e does not fire. (Cost one VM run; leaving the reason here.)
        off = sealed.succeed(
            "rc=0; runuser -u agent -- curl -sS --max-time 10 -o /dev/null "
            "http://${meshPeerVlanIp}:443/ || rc=$?; echo EXIT=$rc"
        )
        assert "EXIT=28" in off, (
            f"the agent's off-tunnel probe failed, but not by the wall (want EXIT=28): {off!r}"
        )

    # LAST — this leg takes the tunnel down and back up, so nothing may follow it.
    with subtest("7. the key guard actually fails closed, on all three of its branches"):
        # mesh-wireguard-sealed.nix grew a preStart guard (Fable gate ruling, 2026-08-14) because
        # the eval-time store-path assertion inspects a STRING and is bypassed by a store-symlinked
        # /etc entry. Every leg above ran with that guard PASSING, which proves only that it does
        # not break a healthy box — a guard wired to nothing would look identical. So drive each
        # branch to red deliberately. Same reason leg 2 got a counterfactual run.
        sealed.succeed("cp -a ${keyPath} /root/key.bak")

        # (i) absent — the first-hardware-boot case the module header describes.
        sealed.succeed("rm -f ${keyPath}")
        sealed.fail("systemctl restart wireguard-${ifName}.service")
        sealed.succeed(
            "journalctl -u wireguard-${ifName}.service -n 40 --no-pager | grep -q 'is absent'"
        )

        # (ii) resolves into the store — the exact bypass the eval assertion cannot see. A symlink
        # to any store-resident file reproduces it; the guard exits before wg ever reads the file.
        sealed.succeed("ln -sfT /run/current-system/sw/bin/sh ${keyPath}")
        sealed.fail("systemctl restart wireguard-${ifName}.service")
        sealed.succeed(
            "journalctl -u wireguard-${ifName}.service -n 40 --no-pager "
            "| grep -q 'Store contents are world-readable'"
        )

        # (iii) group/other-readable.
        sealed.succeed("rm -f ${keyPath} && install -m0644 /root/key.bak ${keyPath}")
        sealed.fail("systemctl restart wireguard-${ifName}.service")
        sealed.succeed(
            "journalctl -u wireguard-${ifName}.service -n 40 --no-pager "
            "| grep -q 'group or other bits are set'"
        )

        # CONTROL: restore and the mesh comes back. Without this the three reds above could all be
        # a unit that stopped being startable for some unrelated reason partway through the leg.
        sealed.succeed("rm -f ${keyPath} && install -m0600 /root/key.bak ${keyPath}")
        sealed.succeed("systemctl restart wireguard-${ifName}.service")
        # The interface unit alone is NOT enough, and this is a DEPLOYMENT finding that fell out of
        # writing the control rather than a detail of the test. The per-peer units are `wantedBy`
        # the interface unit, so a restart does not re-run one that is already active — and after a
        # failed start they sit in `failed` (observed here: "Job ...peer-<key>.service/start failed
        # with result 'dependency'") and are not retried. The interface therefore comes back with
        # NO PEERS while `systemctl restart` exits 0 and `is-active` reports active: a dead mesh
        # that passes every unit-level check. That is exactly the S6 runbook's recovery path —
        # provision the key after the expected first-boot failure, then restart — so the runbook
        # step must restart the peer units too, not just the interface.
        # NB: peer unit names carry systemd escapes for the base64 key (`\x2b` for `+`, `\x3d` for
        # `=`). Do not pipe them through xargs, which strips the backslashes and then reports the
        # mangled name as "Unit not found" — costs a full VM run to diagnose. `set --` from a
        # command substitution preserves them.
        sealed.succeed("systemctl reset-failed 'wireguard-${ifName}-peer-*' 2>/dev/null || true")
        sealed.succeed(
            "set -- $(systemctl list-unit-files --plain --no-legend "
            "'wireguard-${ifName}-peer-*' | cut -d' ' -f1); "
            "test $# -gt 0 && systemctl restart \"$@\""
        )
        sealed.succeed("wg show ${ifName} | grep -q '${meshPub}'")
        # ...and END TO END, because "peer configured" is the claim `wg show` supports, not
        # "the tunnel carries traffic again".
        sealed.wait_until_succeeds(
            "curl -sS --max-time 2 -o /dev/null http://${meshTunIp}:443/", timeout=30
        )
  '';
}
