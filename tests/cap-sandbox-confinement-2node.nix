# tests/cap-sandbox-confinement-2node.nix — the TWO-NODE harness, Geist's ruled next evidence step.
#
# WHAT QUESTION THIS EXISTS TO ANSWER, and it is one question, not a general "more coverage":
# does systemd's IPAddressDeny actually filter anything on this platform?
#
# The single-node test cannot say. Leg 8b probes an address in a denied CIDR, but on one node
# every candidate address is one of the host's OWN, so Linux routes the connection over `lo` — and
# systemd's IP filtering is documented not to apply to the loopback device. The arm therefore
# CANNOT go green whatever the deny list does, which is why it reports NOT-DEMONSTRATED rather
# than passing or failing. An arm that cannot fail is as uninformative as one that cannot pass;
# this file is the environment where it can do both.
#
# THE CONSEQUENCES ARE RULED IN ADVANCE (Geist, 2026-09-03), so this harness cannot be read to
# favour whichever answer it returns:
#   branch (a) the deny list ENFORCES  -> 8b goes green, the layer-1 design stands, and a future
#              lift-PR for `offenders` cites this run.
#   branch (b) the deny list is INERT, or is loopback-exempt by design -> that is a real finding:
#              slice 1 switches to the netns+proxy shape (tests/fetch-proxy-allowlist.nix already
#              has it), which answers the loopback threat structurally rather than by filter — in
#              a private namespace, 127.0.0.1 is the namespace's own loopback and the host's
#              :11434 is unreachable by construction.
# Either way `offenders` in modules/cap-invoke-pkg.nix STAYS CLOSED until someone reads the result.
# This test does not lift a gate; it produces the evidence a lift would have to cite.
#
# WHAT BUILDING IT FOUND, before it ever ran. The battery already carried a seam for this,
# AGENT_OS_BATTERY_REMOTE_DENIED_ADDR, with a comment explaining that it existed so the remote
# branch would be REACHABLE — "a branch no harness can enter is untested code that reads as
# coverage." The seam could not be entered. The battery bound its own 8a/8b listener to the target
# address, and you cannot bind to an address belonging to another machine: supplying a real remote
# target failed with EADDRNOTAVAIL and died as "harness broken", never reaching a verdict. The
# hook built to keep a branch reachable was itself unreachable, for the length of time nobody
# tried to use it. Splitting the listener from the target, and adding the companion
# AGENT_OS_BATTERY_REMOTE_DENIED_PORT, is part of this change.
#
# WHY THE ADDRESS IS DECISIVE HERE. runNixOSTest puts every node on one shared VLAN, whose
# RFC1918 /24 lies inside 192.168.0.0/16 — a CIDR net.fetch's policy denies. So the address is  # gate-allow
# (a) genuinely off-host from `box`'s point of view, reached over a real ethernet route rather than
# `lo`, and (b) inside a CIDR the policy already denies for its own reasons. No special-casing, no
# address invented for the test: the deny entry under test is the shipping one.
{ pkgs, baseModules }:

let
  capInvoke = import ../modules/cap-invoke-pkg.nix { inherit pkgs; };
  capSandbox = import ../modules/cap-sandbox.nix { lib = pkgs.lib; };
  policyJson = pkgs.writeText "agent-os-cap-sandbox.json" capSandbox.policyJson;
  registryJson = pkgs.writeText "agent-os-registry.json"
    (builtins.toJSON (import ../modules/capability-registry.nix { lib = pkgs.lib; }).registry);
  battery = ../tests/cap-sandbox-battery.sh;
  # A fixed port, because the battery must be TOLD the remote port and a negotiated one would need
  # a channel between the nodes that this harness has no reason to build.
  targetPort = 18443;
  # A FILE, not an ExecStart one-liner. A multi-line python program squeezed into a quoted Nix
  # string is exactly the kind of thing that survives review and dies at boot, and this repo has
  # no way to parse-check it before CI. `writeScript` keeps the program readable and makes the
  # shebang the store python's.
  listener = pkgs.writeScript "probe-listener.py" ''
    #!${pkgs.python3}/bin/python3
    import socket, sys
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", ${toString targetPort}))
    s.listen(16)
    sys.stderr.write("listening on ${toString targetPort}\n")
    sys.stderr.flush()
    while True:
        conn, _ = s.accept()
        conn.close()
  '';
in
pkgs.testers.runNixOSTest {
  name = "agentos-cap-sandbox-confinement-2node";

  nodes = {
    # The node under test. Identical to the single-node harness — same baseModules, same battery,
    # same artefacts — so any difference in outcome is attributable to the TARGET, not to a second
    # way of building the box.
    box = { ... }: {
      imports = baseModules;
      environment.systemPackages = with pkgs; [ bash coreutils python3 systemd ];
    };

    # A plain listener. Deliberately NOT importing baseModules: this node exists only to accept a
    # TCP connection, and giving it the agent-os stack would invite the reading that something on
    # the target side participated in the result.
    target = { ... }: {
      networking.firewall.allowedTCPPorts = [ targetPort ];
      environment.systemPackages = with pkgs; [ python3 ];
      systemd.services.probe-listener = {
        wantedBy = [ "multi-user.target" ];
        serviceConfig.ExecStart = "${listener}";
      };
    };
  };

  testScript = ''
    start_all()
    box.wait_for_unit("multi-user.target")
    target.wait_for_unit("probe-listener.service")
    target.wait_for_open_port(${toString targetPort})

    # Select the target address BY THE PROPERTY 8b needs, not by position or interface name.
    # The first cut took `head -1` of the target's scope-global addresses and got QEMU's user-net
    # address -- which every node in a nixosTest carries identically, so the "remote" address was
    # box itself and the ping succeeded in 0.03s against loopback. An interface name would have been the same
    # mistake wearing a different spelling, and a hard-coded literal a third. So: take the target's
    # global addresses, subtract box's own, and require the remainder to be exactly one. An address
    # shared by both nodes cannot be chosen by construction, which is the only guarantee worth
    # having here -- the whole point of two nodes is that the route is not `lo`.
    def globals4(node):
        return set(
            node.succeed(
                "ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1"
            ).split()
        )

    own = set(box.succeed("ip -4 -o addr show | awk '{print $4}' | cut -d/ -f1").split())
    candidates = sorted(globals4(target) - own)
    assert len(candidates) == 1, (
        f"expected exactly one target address that box does not also hold, got {candidates} "
        f"(target globals {sorted(globals4(target))}, box own {sorted(own)}). Zero means the nodes "
        "share all addressing and 8b would be a loopback probe again; more than one means the "
        "harness cannot say which route it measured."
    )
    addr = candidates[0]
    print(f"target address on the test VLAN: {addr} (box own: {sorted(own)})")

    # 8b's meaning also depends on the chosen address lying inside a CIDR the shipping policy
    # denies -- otherwise a denial proves nothing about the deny list. Asserted rather than assumed,
    # because the selector above is deliberately free to return whatever the topology gives it.
    import ipaddress

    assert ipaddress.ip_address(addr) in ipaddress.ip_network("192.168.0.0/16"), (  # gate-allow
        f"target address {addr} is not inside the denied CIDR the 8b leg is probing; a connection "
        "result against it would say nothing about the deny list"
    )

    # PRECONDITION, asserted from `box` and not assumed: the address must be genuinely reachable
    # from the node under test BEFORE any deny list is involved. If this fails the run is a broken
    # harness, and saying so here keeps it from later masquerading as an 8a control failure.
    box.succeed(f"ping -c1 -W5 {addr} >/dev/null")

    out = box.succeed(
        f"AGENT_OS_BATTERY_REMOTE_DENIED_ADDR={addr} "
        + "AGENT_OS_BATTERY_REMOTE_DENIED_PORT=${toString targetPort} "
        + "${pkgs.bash}/bin/bash ${battery} "
        + "${../bin/cap-invoke} "
        + "${capInvoke.capBinDir}/bin "
        + "${registryJson} "
        + "${policyJson} "
        + "${pkgs.systemd}/bin/systemd-run "
        + "${../bin/cap-net-fetch} 2>&1"
    )
    print(out)

    # 8b must now be DECISIVE. NOT-DEMONSTRATED is the single-node outcome and its appearance here
    # means the remote target did not take effect — the whole point of this file is that the arm
    # stops being allowed to abstain.
    assert "8b NOT-DEMONSTRATED" not in out, (
        "8b still reported NOT-DEMONSTRATED with a genuinely off-host target supplied. The remote "
        "seam did not take effect, so this run settles nothing."
    )
    assert "cap-sandbox 8b OK" in out, "8b did not report OK and did not abstain — read the log"

    # The arm count, same rule as the single-node harness: the one failure `succeed()` cannot
    # catch is legs being deleted while the script still exits 0.
    for leg in ["cap-sandbox 0 OK", "cap-sandbox 1 OK", "cap-sandbox 2 OK", "cap-sandbox 3 OK",
                "cap-sandbox 4 OK", "cap-sandbox 5 OK", "cap-sandbox 6 OK",
                "cap-sandbox 7 OK", "cap-sandbox 8b OK", "cap-sandbox 9 OK",
                "11 arms"]:
        assert leg in out, f"battery exited 0 but never reported {leg!r} — legs were removed"
  '';
}
