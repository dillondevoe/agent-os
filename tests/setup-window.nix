# tests/setup-window.nix — the setup window on a booted box, verified by PACKET FATE
# (docs/design/first-login-and-setup-window.md §2, slice 3a; the spec's setup-window-scope,
# -default-sealed, -reload, -reboot, -budget, -invalid-state, -clock-1970 and seal-marker-forged).
#
# The product path with `agentos.cleanRoom.setupWindow.enable = true` and a four-minute budget, and a
# peer that listens on 443, 80 and UDP 53. Every "cannot reach" has a positive control in the same
# state and pins curl's exit code 28 (a timeout: the packet was dropped), so a dead network or a
# refused connection cannot pass for the wall.
#
#   W1  first boot: the set holds exactly the fetcher's uid; the state is on disk, root-only.
#   W2  scope while OPEN: the fetcher's uid reaches 443 and DNS; it does NOT reach 80; the agent's uid
#       reaches nothing; root gains nothing (its own 443 accept is unchanged, 80 stays closed).
#   W3  an nftables reload, and a restart, empty the set and the window unit puts the uid back; neither
#       charges the budget.
#   W4  with the window unit stopped, a reloaded ruleset is the sealed one: the set is empty and the
#       fetcher's uid cannot reach 443. Starting the unit reopens without a new charge.
#   W5  a reboot inside the window reopens it with what was left, less one floor: not a fresh budget.
#   W6  the budget (or the wall deadline, whichever a look catches first) runs out: the box seals itself, the
#       fetcher is restarted, the pull and its retry timer are stopped and cannot start again, and the
#       fetcher's uid cannot reach 443 any more. A CONNECTION OPENED INSIDE THE WINDOW AND HELD ACROSS
#       THE SEAL IS DEAD (the spec's setup-window-seal-ends-flow): sealed means no egress, not "no new
#       connections". Control in W2: the same held connection works while the window is open.
#   W7  sealed stays sealed: across an nftables reload and restart, and across a reboot.
#   W8  re-provisioning is root removing the state: a garbage window.json seals ("invalid state").
#   W9  an empty `sealed` file is not a seal and not a first boot: the unit fails, the set stays empty.
#   W10 THE UPGRADE DOOR (spec 5.1): with the agent logged in, and no marker, no books and no login
#       token (what `nixos-rebuild switch` from an image without the window leaves), the window does
#       NOT open: the box is sealed `refused` and the chain has one `window-anomaly`, foreign-process.
#   W11 a clock with no battery (1970) on a first boot: the window opens on the budget alone. (The
#       agent's sessions are ended first: until slice 4a this leg removed the token under a logged-in
#       agent, and the window opened next to it.)
{ pkgs, baseModules }:

let
  # A UDP echo on 53 for the peer, and a one-datagram probe for the box (exit 0 only if the echo came back).
  echo53 = pkgs.writeText "echo53.py" ''
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 53))
    while True:
        data, addr = s.recvfrom(512)
        s.sendto(data, addr)
  '';
  probe53 = pkgs.writeText "probe53.py" ''
    import socket, sys
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(4)
    try:
        s.sendto(b"q", (sys.argv[1], 53))
    except PermissionError:
        sys.exit(27)          # the output hook dropped it: the kernel refuses the send (EPERM)
    try:
        sys.exit(0 if s.recvfrom(64)[0] == b"q" else 1)
    except OSError:
        sys.exit(28)          # sent, never answered
  '';
  # Hold one TCP connection to the peer's 443 open; when the trigger file appears, try to USE it.
  # "alive" = a request sent on the old connection was answered; "dead-..." = it was not.
  hold443 = pkgs.writeText "hold443.py" ''
    import os, socket, sys, time
    peer, ready, trigger, result = sys.argv[1:5]
    s = socket.create_connection((peer, 443), timeout=5)
    open(ready, "w").write("ready")
    while not os.path.exists(trigger):
        time.sleep(0.2)
    try:
        s.settimeout(6)
        s.sendall(b"GET / HTTP/1.0\r\n\r\n")
        res = "alive" if s.recv(64).startswith(b"HTTP/") else "dead-no-reply"
    except OSError as e:
        res = "dead-" + e.__class__.__name__
    open(result, "w").write(res)
  '';
in
pkgs.testers.runNixOSTest {
  name = "agentos-setup-window";

  nodes.box = { lib, ... }: {
    imports = baseModules;
    agentos.cleanRoom.setupWindow.enable = true;
    agentos.cleanRoom.setupWindow.seconds = 240;
    # The product's first-boot pull holds multi-user.target for two minutes waiting for a registry
    # this VM cannot reach, and a four-minute budget is spent before the test can look. It is not
    # what is under test: here it runs only when its timer fires, in the background.
    systemd.services.agent-os-pull-model.wantedBy = lib.mkForce [ ];
    virtualisation.memorySize = 2048;
    virtualisation.cores = 2;
    environment.systemPackages = [ pkgs.curl pkgs.python3 pkgs.jq ];
    networking.networkmanager.unmanaged = [ "interface-name:eth1" ];
  };

  nodes.peer = { ... }: {
    networking.firewall.enable = false;
    systemd.services.listen-443 = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 -m http.server 443 --bind 0.0.0.0";
    };
    systemd.services.listen-80 = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 -m http.server 80 --bind 0.0.0.0";
    };
    systemd.services.echo-53 = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 ${echo53}";
    };
  };

  testScript = ''
    import json

    STATE = "/var/lib/agent-os/seal"
    UID = "351"

    box.start()
    peer.start()
    # Not multi-user.target: with the window open the agent's getty, and so that target, wait for the
    # seal (slice 3b; tests/seal-wait.nix). The window unit is what this test is about.
    box.wait_for_unit("agentos-setup-window.service")
    peer.wait_for_unit("multi-user.target")
    peer.wait_for_open_port(443)
    peer.wait_for_open_port(80)
    peer_ip = peer.succeed("ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | grep -v '^10\\.0\\.2\\.' | head -1").strip()
    assert peer_ip, "no off-box peer address; the fixture, not the wall, is broken"
    assert box.succeed("id -u ollama").strip() == UID, "the fetcher's uid is not the fixed one"
    agent_uid = box.succeed("id -u agent").strip()

    def elements():
        out = box.succeed("nft -j list set inet agentos_egress setup_uid")
        return sorted(str(e) for e in json.loads(out)["nftables"][-1]["set"].get("elem", []))

    def window():
        return json.loads(box.succeed(f"cat {STATE}/window.json"))

    def curl(user, port):
        # (exit code) of a 5-second fetch from the peer as that user. 0 = reached; 28 = dropped.
        return box.execute(f"runuser -u {user} -- curl -s -o /dev/null -m 5 http://{peer_ip}:{port}/")[0]

    def dns(user):
        # exit code of one datagram to the peer's port 53 as that user. 0 = echoed back; 27 = the wall
        # refused the send (EPERM), which is what a dropped UDP packet looks like; 28 = sent, no answer.
        return box.execute(f"runuser -u {user} -- python3 ${probe53} {peer_ip}")[0]

    def hold(tag):
        # A connection to the peer's 443, opened NOW as the fetcher's uid and kept. Returns when it is up.
        box.succeed(f"install -d -m 1777 /tmp/hold && rm -f /tmp/hold/{tag}.*")
        box.succeed(f"systemd-run --quiet --collect --unit=hold-{tag} --uid={UID} python3 ${hold443} {peer_ip} /tmp/hold/{tag}.ready /tmp/hold/{tag}.go /tmp/hold/{tag}.result")
        box.wait_until_succeeds(f"test -e /tmp/hold/{tag}.ready", timeout=20)

    def use(tag):
        # Try to use that old connection; "alive" or "dead-...".
        box.succeed(f"touch /tmp/hold/{tag}.go")
        box.wait_until_succeeds(f"test -e /tmp/hold/{tag}.result", timeout=30)
        return box.succeed(f"cat /tmp/hold/{tag}.result").strip()

    def wait_open():
        box.wait_until_succeeds("nft -j list set inet agentos_egress setup_uid | grep -q '\"elem\"'", timeout=30)

    # W1 — first boot
    box.wait_for_unit("agentos-setup-window.service")
    wait_open()
    assert elements() == [UID], f"W1: the set must hold exactly the fetcher's uid: {elements()}"
    w1 = window()
    assert w1["boots"] == 1 and w1["budget_at_boot"] == 240 and w1["remaining"] <= 180, f"W1: {w1}"
    assert box.succeed(f"stat -c '%U %a' {STATE} {STATE}/window.json").split() == ["root", "700", "root", "600"], "W1: state modes"
    box.fail(f"test -e {STATE}/sealed")
    box.succeed("journalctl -t agos-window --no-pager | grep -q 'window opened remaining=180 boots=1'")

    # The window is open with the console's progress unit alive beside it, and stays open across the
    # ticks of this test: that unit is DynamicUser=, and the program's allowlist takes systemd's range
    # for such units from a constant. Pin the constant to what this systemd actually allocates.
    dyn = int(box.wait_until_succeeds(
        "pid=$(systemctl show -p MainPID --value agentos-setup-progress.service); test \"$pid\" != 0 && stat -c %u /proc/$pid", timeout=60).strip())
    assert 61184 <= dyn <= 65519, f"W1: a DynamicUser= unit got uid {dyn}, outside the range agos_window.py treats as the system's own"

    # W2 — scope while open
    assert curl("ollama", 443) == 0, "W2: POSITIVE CONTROL: the fetcher's uid must reach 443 while the window is open"
    peer.wait_for_unit("echo-53.service")
    assert dns("root") == 0, "W2: control: the UDP echo fixture works (root's own DNS accept)"
    assert dns("ollama") == 0, f"W2: the fetcher's uid must reach DNS while the window is open: {dns('ollama')}"
    assert curl("ollama", 80) == 28, f"W2: port 80 must stay closed for the fetcher: {curl('ollama', 80)}"
    hold("control")
    assert use("control") == "alive", "W2: CONTROL for W6: with the window open, a held connection must still work"
    # The probes below run processes AS THE AGENT'S ACCOUNT while the window is open (root starts them
    # with runuser: the one thing the gates cannot stop). Since slice 4a the tick closes and seals the
    # window when it sees such a process, which is right and would end this test here. The tick's
    # timer is stopped for these few probes and started again after them.
    box.succeed("systemctl stop agentos-setup-window-tick.timer; while systemctl is-active --quiet agentos-setup-window-tick.service; do sleep 1; done")
    assert elements() == [UID], f"W2: the window closed before the agent's probes: {elements()}"
    assert curl("agent", 443) == 28 and curl("agent", 80) == 28, "W2: the agent's uid must reach nothing"
    assert dns("agent") == 27, f"W2: the agent's uid must not reach DNS (27 = dropped at the wall): {dns('agent')}"
    assert curl("root", 80) == 28, "W2: root must gain nothing from the window (80 stays closed)"
    assert curl("root", 443) == 0, "W2: control: root's own 443 accept is unchanged"
    box.fail(f"runuser -u agent -- cat {STATE}/window.json")
    box.fail("runuser -u agent -- nft list ruleset")
    box.fail("runuser -u agent -- systemctl start agentos-setup-window.service")
    box.fail("runuser -u agent -- sh -c 'command -v agentos-setup-window'")
    box.wait_until_fails(f"pgrep -u {agent_uid} >/dev/null", timeout=30)
    box.succeed("systemctl start agentos-setup-window-tick.timer")
    assert elements() == [UID], f"W2: the window did not survive the probes: {elements()}"

    # W3 — nftables reload and restart: emptied, put back, not charged
    for verb in ("reload", "restart"):
        box.succeed(f"systemctl {verb} nftables.service")
        wait_open()
        assert elements() == [UID], f"W3 {verb}: {elements()}"
        w = window()
        assert w["boots"] == 1 and w["budget_at_boot"] == 240, f"W3 {verb}: the budget was charged: {w}"
        assert curl("ollama", 443) == 0, f"W3 {verb}: the window did not come back"
    assert int(box.succeed("journalctl -t agos-window --no-pager | grep -c 're-derived'").strip()) >= 2, "W3: no re-derivation logged"

    # W4 — without the window unit, a loaded ruleset is the sealed one
    box.succeed("systemctl stop agentos-setup-window.service && systemctl restart nftables.service")
    assert elements() == [], f"W4: the reloaded ruleset is not the sealed one: {elements()}"
    assert curl("ollama", 443) == 28, "W4: the fetcher's uid reached 443 with the set empty"
    assert dns("ollama") == 27, f"W4: the fetcher's uid reached DNS with the set empty: {dns('ollama')}"
    assert curl("root", 443) == 0, "W4: control: the network is alive (root's 443)"
    box.succeed("systemctl start agentos-setup-window.service")
    wait_open()
    assert window()["boots"] == 1 and curl("ollama", 443) == 0, "W4: starting the unit must reopen, in the same boot"

    # W5 — a reboot inside the window
    before = window()
    box.shutdown()
    box.start()
    box.wait_for_unit("agentos-setup-window.service")
    wait_open()
    w5 = window()
    stamped = w5["budget_at_boot"]
    assert w5["boots"] == 2 and stamped <= before["remaining"] and stamped < 240, f"W5: not what was left: before={before} after={w5}"
    assert w5["remaining"] <= stamped - 60, f"W5: the new boot was not charged its floor: {w5}"
    assert curl("ollama", 443) == 0, "W5: the window did not reopen"

    # W6 — the budget runs out; the box seals itself. A connection the fetcher's uid opened while the
    # window was open is held across the seal: sealed must mean it is DEAD, not merely that no new one
    # can be made. (It is a separate process, so restarting the fetcher does not close it: only the wall can.)
    hold("inflight")
    box.succeed("systemctl start ollama.service; systemctl start agent-os-pull-model.timer || true")
    ollama_before = box.succeed("systemctl show -p ActiveEnterTimestampMonotonic --value ollama.service").strip()
    box.wait_until_succeeds(f"test -e {STATE}/sealed", timeout=int(w5["remaining"]) + 150)
    marker = json.loads(box.succeed(f"cat {STATE}/sealed"))
    # "budget" or "deadline": the open-seconds budget and the wall-clock deadline both end about now
    # (the deadline is the first opening plus the window length; a reboot's downtime counts against it
    # and not against the budget, the per-boot floor the other way round). Which one a look catches
    # first depends on how long the boots took; since slice 3b the wait looks every few seconds, not
    # only the tick once a minute. Either is the box sealing itself on time. (CI sealed on the deadline.)
    assert marker["reason"] in ("budget", "deadline") and marker["toplevel"].startswith("/nix/store/") and len(marker["ruleset_sha256"]) == 64, f"W6: {marker}"
    assert marker["toplevel"] == box.succeed("readlink -f /run/current-system").strip(), "W6: the marker names another system"
    assert elements() == [], f"W6: sealed with a non-empty set: {elements()}"
    assert curl("ollama", 443) == 28, "W6: the fetcher's uid still reaches 443 after the seal"
    after = use("inflight")
    assert after.startswith("dead-"), f"W6: a connection opened inside the window still works after the seal: {after}"
    assert curl("root", 443) == 0, "W6: control: the network is alive"
    box.wait_until_succeeds("systemctl is-active --quiet ollama.service", timeout=60)
    assert box.succeed("systemctl show -p ActiveEnterTimestampMonotonic --value ollama.service").strip() != ollama_before, "W6: the fetcher was not restarted"
    box.fail("systemctl is-active --quiet agent-os-pull-model.timer")
    box.fail("systemctl start agent-os-pull-model.timer && systemctl is-active --quiet agent-os-pull-model.timer")   # the marker's condition
    assert box.succeed("systemctl show -p ConditionResult --value agent-os-pull-model.timer").strip() == "no", "W6: the retry timer can start on a sealed box"
    box.succeed(f"journalctl -t agos-window --no-pager | grep -q 'sealed reason={marker['reason']} set_flushed=1'")

    # W7 — sealed stays sealed
    for verb in ("reload", "restart"):
        box.succeed(f"systemctl {verb} nftables.service")
        box.succeed("sleep 3")
        assert elements() == [], f"W7 {verb}: reopened after the seal: {elements()}"
    box.shutdown()
    box.start()
    box.wait_for_unit("multi-user.target")
    box.wait_for_unit("agentos-setup-window.service")
    assert elements() == [] and curl("ollama", 443) == 28, "W7: a reboot reopened a sealed box"
    box.fail("systemctl is-active --quiet agent-os-pull-model.timer")       # the condition holds across the reboot
    assert box.succeed("systemctl show -p ConditionResult --value agent-os-pull-model.timer").strip() == "no", "W7: the retry timer is back after a reboot"
    assert json.loads(box.succeed(f"cat {STATE}/sealed")) == marker, "W7: the marker changed"

    # W8 — root removes the state (the re-provisioning path); a garbage window.json seals. In a boot
    # that was already sealed nothing is sealed again (slice 3b: a re-provisioning waits for the
    # reboot), so the test stands in for the new boot by removing the login token, as W10 does.
    box.succeed(f"rm {STATE}/sealed /run/agentos-setup/login-ok && echo garbage > {STATE}/window.json && systemctl restart agentos-setup-window.service")
    assert json.loads(box.succeed(f"cat {STATE}/sealed"))["reason"] == "invalid state" and elements() == [], "W8"

    # W9 — an empty `sealed` is neither a seal nor a first boot
    box.succeed(f"rm {STATE}/window.json && : > {STATE}/sealed")
    box.fail("systemctl restart agentos-setup-window.service")
    assert elements() == [], "W9: a forged marker opened the window"
    box.fail(f"test -e {STATE}/window.json")
    box.succeed("journalctl -t agos-window --no-pager | grep -q 'the sealed marker is malformed'")

    # W10 — the upgrade door. The agent is logged in on tty1 (this boot was sealed). Remove the marker,
    # the books AND the login token: that is the state of a box that was running an image without the
    # window when `nixos-rebuild switch` started the window unit for the first time. It must not open.
    AUDIT = "/var/lib/agent-os/audit/audit.log"
    box.wait_until_succeeds(f"pgrep -u {agent_uid} >/dev/null", timeout=60)       # the control: there IS an agent to refuse for
    before = int(box.succeed(f"grep -c window-opened {AUDIT} || true").strip() or 0)
    box.succeed(f"rm -f {STATE}/sealed {STATE}/window.json /run/agentos-setup/login-ok; systemctl reset-failed agentos-setup-window.service")
    box.succeed("systemctl restart agentos-setup-window.service")
    assert elements() == [], f"W10: the window opened next to a logged-in agent: {elements()}"
    w10 = json.loads(box.succeed(f"cat {STATE}/sealed"))
    assert w10["reason"] == "refused", f"W10: {w10}"
    box.fail(f"test -e {STATE}/window.json")                                       # refused BEFORE the books
    assert int(box.succeed(f"grep -c window-opened {AUDIT} || true").strip() or 0) == before, "W10: a window-opened record was written for a refused open"
    anomalies = [json.loads(l) for l in box.succeed(f"grep window-anomaly {AUDIT}").splitlines()]
    assert len(anomalies) == 1 and anomalies[0]["kind"] == "foreign-process" and anomalies[0]["processes"] >= 1 \
        and anomalies[0]["src"] == "setup-window", f"W10: {anomalies}"
    box.succeed("journalctl -t agos-window --no-pager | grep -q 'the window does not open next to them'")
    assert curl("ollama", 443) == 28, "W10: the fetcher reaches 443 after a refused open"
    # the same again in the same boot: still closed, still ONE record
    box.succeed(f"rm -f {STATE}/sealed /run/agentos-setup/login-ok; systemctl restart agentos-setup-window.service")
    assert elements() == [] and box.succeed(f"grep -c window-anomaly {AUDIT}").strip() == "1", "W10: a second refusal in one boot wrote a second record"

    # W11 — a first boot with no clock battery. A first boot has no login token (/run is new) and no
    # agent. The test stands in for it by ending the agent's sessions and removing the token; with the
    # agent gone the same restart that W10 refused must open.
    # (Keep this leg free of VT switches and key presses: tty1's getty is stopped, not gone. Anything
    # that starts it again runs the wait first, which would seal `not open` before the restart below
    # and fail this leg. It could not log the agent in: the token is removed.)
    box.succeed("systemctl stop getty@tty1.service; loginctl terminate-user agent 2>/dev/null || true")
    box.wait_until_fails(f"pgrep -u {agent_uid} >/dev/null", timeout=60)
    box.succeed(f"rm -f {STATE}/sealed {STATE}/window.json /run/agentos-setup/login-ok; systemctl stop systemd-timesyncd.service 2>/dev/null || true")
    box.succeed("date -s '1970-01-02 00:00:00' && test $(date +%Y) = 1970")
    box.succeed("systemctl reset-failed agentos-setup-window.service; systemctl restart agentos-setup-window.service")
    wait_open()
    w11 = window()
    assert w11["wall_deadline"] is None and w11["opened_at"] is None and w11["budget_at_boot"] == 240, f"W11: {w11}"
    assert elements() == [UID] and curl("ollama", 443) == 0, "W11: a 1970 clock did not open on the budget"
  '';
}
