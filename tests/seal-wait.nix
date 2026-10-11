# tests/seal-wait.nix — THE AGENT DOES NOT EXIST WHILE THE SETUP WINDOW IS OPEN, on a booted box
# (docs/design/first-login-and-setup-window.md §2.4, slice 3b part 1; the spec's
# setup-window-agent-absent and setup-window-seal-on-model).
#
# The product path with the window on. The model server is a stand-in that answers GET /api/tags from
# a file the test writes, as the fetcher's uid, on the real port: the detector, the unit ordering, the
# login gate and the user-manager gate are the real ones.
#
# A sampler runs on the box for the whole test, five times a second: "a process of the agent's account
# exists, and THEN the set still holds a uid". The set only ever goes from non-empty to empty within a
# boot, so that order has no false alarm. Every "no agent process" below has its control in the same
# boot: the same check finds the agent once the box is sealed.
#
#   D1  window open, no model: the seal unit is still starting; tty1's getty has not started; there is
#       no login token; no process of the agent's account exists; tty1 shows the progress line.
#   D2  the other doors, knocked on while the window is open: a second virtual terminal (logind spawns
#       its getty on the switch), a serial console, a getty started by hand. Each one refuses and says
#       why. None of them produces a process of the agent's account.
#   D3  lingering enabled for the agent's account, then a reboot inside the window: logind tries to
#       start the user manager at boot with no login at all, and the start condition refuses it.
#       The reboot itself must NOT seal the box (systemd stops the wait at shutdown): the window
#       reopens with what was left.
#   D4  the download line is text from the network: tty1 shows it cleaned (an escape sequence planted
#       in it does not clear the screen).
#   D5  the model appears in the fetcher's own list: the box seals itself (reason: complete), the set
#       is empty, the token exists, and only then tty1's getty starts and the agent exists. Controls:
#       the second terminal and the serial console now log the agent in.
#   D6  re-provisioning under a running agent is refused: with the state removed and the window unit
#       restarted in the sealed boot, the set stays empty. After a reboot the same state IS a first
#       boot: the window opens, and the agent is absent again.
#   D7  the wait is killed: nothing seals (only a timeout does), the window is left to the tick, and
#       the agent stays absent: no token, tty1's getty did not follow a wait that failed. Starting
#       tty1's getty runs the wait again, and with the model listed the box seals and the agent starts.
#   D8  no model ever arrives: the budget ends, the box seals (reason: budget), tty1 says so and how to
#       set the machine up again, and then the agent starts without a model.
#   Last: a control for the sampler itself (a planted violation is recorded).
{ pkgs, baseModules }:

let
  # GET /api/tags from a file; everything else 404. Bound to loopback on the model server's port.
  fakeOllama = pkgs.writeText "fake-ollama.py" ''
    import http.server, json, os
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/api/tags":
                self.send_response(404); self.end_headers(); return
            try:
                body = open("/run/fake-ollama/tags.json", "rb").read()
            except OSError:
                body = b'{"models": []}'
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass
    http.server.ThreadingHTTPServer(("127.0.0.1", 11434), H).serve_forever()
  '';
  # pgrep FIRST, the set second: see the header.
  sampler = pkgs.writeShellScript "invariant-sampler" ''
    while :; do
      if ${pkgs.procps}/bin/pgrep -u agent >/dev/null 2>&1; then
        if ${pkgs.nftables}/bin/nft list set inet agentos_egress setup_uid 2>/dev/null | ${pkgs.gnugrep}/bin/grep -q 'elements'; then
          { date +%T; ${pkgs.procps}/bin/pgrep -u agent -a; } >> /var/lib/invariant-violations 2>&1
        fi
      fi
      ${pkgs.coreutils}/bin/sleep 0.2
    done
  '';
in
pkgs.testers.runNixOSTest {
  name = "agentos-seal-wait";

  nodes.box = { lib, ... }: {
    imports = baseModules;
    agentos.cleanRoom.setupWindow.enable = true;
    agentos.cleanRoom.setupWindow.seconds = 600;
    # Not under test, and it would sit two minutes waiting for a registry this VM cannot reach.
    systemd.services.agent-os-pull-model.wantedBy = lib.mkForce [ ];
    systemd.timers.agent-os-pull-model.wantedBy = lib.mkForce [ ];
    systemd.services.ollama.serviceConfig.ExecStart = lib.mkForce "${pkgs.python3}/bin/python3 ${fakeOllama}";
    # From the first moment of every boot, before anything could log the agent in.
    systemd.services.invariant-sampler = {
      wantedBy = [ "sysinit.target" ];
      unitConfig.DefaultDependencies = false;
      after = [ "local-fs.target" ];
      serviceConfig.ExecStart = "${sampler}";
    };
    virtualisation.memorySize = 2048;
    virtualisation.cores = 2;
    # A second serial line (ttyS1): the test harness masks the getty on its own console (ttyS0), and
    # a serial console is the normal console of a VM, so that door needs a real port to knock on.
    virtualisation.qemu.options = [ "-serial null" ];
    environment.systemPackages = [ pkgs.python3 pkgs.jq pkgs.kbd ];
  };

  testScript = ''
    import json, time

    def tty1_until(*needles, timeout=240):
        # The text on tty1 once ALL the needles are on it at the same moment.
        for _ in range(timeout):
            text = box.get_tty_text("1")
            if all(n in text for n in needles):
                return text
            time.sleep(1)
        raise Exception(f"tty1 never showed {needles}; last:\n{text}")

    STATE = "/var/lib/agent-os/seal"
    RUN = "/run/agentos-setup"
    UID = "351"

    def elements():
        out = box.succeed("nft -j list set inet agentos_egress setup_uid")
        return sorted(str(e) for e in json.loads(out)["nftables"][-1]["set"].get("elem", []))

    def agent_procs():
        return box.execute("pgrep -u agent -a")[1].strip()

    def no_agent(tag):
        procs = agent_procs()
        assert procs == "", f"{tag}: a process of the agent's account exists while the window is open:\n{procs}"

    def no_violation(tag):
        v = box.execute("cat /var/lib/invariant-violations 2>/dev/null")[1].strip()
        assert v == "", f"{tag}: the sampler saw the agent's account running with the set non-empty:\n{v}"

    def marker():
        return json.loads(box.succeed(f"cat {STATE}/sealed"))

    def boot_open(tag):
        # A boot in which the window opens. multi-user.target is NOT reached while it is open (the
        # agent's getty is part of it), so wait for the window unit instead.
        box.start()
        box.wait_for_unit("agentos-setup-window.service")
        try:
            box.wait_until_succeeds("nft -j list set inet agentos_egress setup_uid | grep -q '\"elem\"'", timeout=30)
        except Exception:
            print(box.execute(f"journalctl -b -1 -t agos-window --no-pager | tail -20; journalctl -b -t agos-window --no-pager; ls -la {STATE}; cat {STATE}/sealed {STATE}/window.json")[1])
            raise
        assert elements() == [UID], f"{tag}: the window is not open: {elements()}"
        box.wait_until_succeeds("systemctl show -p ActiveState --value agentos-seal.service | grep -qx activating", timeout=60)

    def reprovision(tag):
        # The documented path: root removes the two state files, then the machine restarts.
        box.succeed(f"rm -f {STATE}/sealed {STATE}/window.json /run/fake-ollama/tags.json")
        box.shutdown()
        boot_open(tag)
        w = json.loads(box.succeed(f"cat {STATE}/window.json"))
        assert w["boots"] == 1 and w["budget_at_boot"] == 600, f"{tag}: not a first boot: {w}"

    agent_uid = None

    # ── D1 ──────────────────────────────────────────────────────────────────────────────────────
    boot_open("D1")
    agent_uid = box.succeed("id -u agent").strip()
    assert box.succeed("id -u ollama").strip() == UID
    assert box.succeed("systemctl show -p ActiveState --value getty@tty1.service").strip() == "inactive", "D1: tty1's getty started before the seal"
    assert "agentos-seal.service" in box.succeed("systemctl show -p Requires --value getty@tty1.service"), "D1: tty1's getty does not require the seal"
    assert "agentos-seal.service" in box.succeed("systemctl show -p After --value getty@tty1.service"), "D1: tty1's getty is not ordered after the seal"
    assert "agentos-seal.service" in box.succeed("systemctl show -p Requires --value autovt@tty1.service"), "D1: the alias getty.target wants is another unit"
    box.fail(f"test -e {RUN}/login-ok")
    assert json.loads(box.succeed(f"cat {RUN}/status"))["state"] == "open", "D1: the status line"
    assert box.succeed(f"stat -c '%U %a' {RUN}").split() == ["root", "755"], "D1: the run directory"
    no_agent("D1")
    box.wait_until_tty_matches("1", "Agent OS is getting its model")
    assert box.succeed("systemctl show -p User --value agentos-setup-progress.service").strip() != "root" or \
        box.succeed("systemctl show -p DynamicUser --value agentos-setup-progress.service").strip() == "yes", "D1: the progress unit runs as root"
    box.fail("systemctl is-active --quiet multi-user.target")

    # ── D2 ──────────────────────────────────────────────────────────────────────────────────────
    box.succeed("chvt 4")
    box.wait_until_tty_matches("4", "This console opens when setup has finished")
    box.succeed("grep -q agentos-login-gate \"$(systemctl cat getty@.service | sed -n 's/^ExecStart=\\(\\/[^ ]*\\).*/\\1/p' | tail -1)\"")
    box.succeed("systemctl cat serial-getty@.service | grep ExecStart | grep -q agentos-login-gate")
    box.succeed("systemctl cat getty@tty3.service | grep ExecStart | grep -v agentos-login-gate | grep -q bin/login")   # break-glass: root's, not gated
    no_agent("D2 second terminal")
    box.succeed("systemctl start getty@tty6.service")
    box.wait_until_tty_matches("6", "This console opens when setup has finished")
    no_agent("D2 a getty started by hand")
    box.succeed("test -c /dev/ttyS1 && systemctl start serial-getty@ttyS1.service")
    # The gate refuses, waits ten seconds and exits; systemd starts the getty again. One restart seen
    # means one whole refusal has happened on that line.
    box.wait_until_succeeds("test \"$(systemctl show -p NRestarts --value serial-getty@ttyS1.service)\" -ge 1", timeout=60)
    no_agent("D2 serial console")
    box.succeed("chvt 1")
    no_violation("D2")

    # ── D3 ──────────────────────────────────────────────────────────────────────────────────────
    box.succeed("mkdir -p /var/lib/systemd/linger && touch /var/lib/systemd/linger/agent")
    box.shutdown()
    boot_open("D3")
    w3 = json.loads(box.succeed(f"cat {STATE}/window.json"))
    assert w3["boots"] == 2 and w3["budget_at_boot"] < 600, f"D3: the reboot did not reopen the window with what was left: {w3}"
    box.fail(f"test -e {STATE}/sealed")
    # logind knocked (it started the manager at boot, with nobody logged in) and the gate refused.
    box.wait_until_succeeds(f"journalctl -b -u user@{agent_uid}.service --no-pager | grep -q \"Skipped due to 'exec-condition'\"", timeout=60)
    assert box.succeed(f"systemctl show -p ActiveState --value user@{agent_uid}.service").strip() != "active", "D3: the agent's user manager is running"
    assert "agentos-user-gate" in box.succeed(f"systemctl show -p ExecCondition --value user@{agent_uid}.service"), "D3: no start condition on the user manager"
    assert box.execute("$(systemctl cat user@.service | grep -o '/nix/store/[^ ]*agentos-user-gate') 0")[0] == 0, "D3: uid 0 must pass the gate"
    assert box.execute(f"$(systemctl cat user@.service | grep -o '/nix/store/[^ ]*agentos-user-gate') {agent_uid}")[0] == 1, "D3: the agent's uid must not"
    no_agent("D3")
    no_violation("D3")

    # ── D4 ──────────────────────────────────────────────────────────────────────────────────────
    box.wait_until_tty_matches("1", "Agent OS is getting its model")
    box.succeed("install -d -m 755 /run/agent-os && printf 'pulling \\033[2J\\033[Hmanifest 42%%' > /run/agent-os/pull-status && chmod 644 /run/agent-os/pull-status")
    box.wait_until_tty_matches("1", "manifest 42%")
    assert "Agent OS is getting its model" in box.get_tty_text("1"), "D4: the planted escape sequence cleared the screen"

    # ── D5 ──────────────────────────────────────────────────────────────────────────────────────
    half = json.dumps({"models": [{"name": "qwen3.5:9b-partial"}, {"name": "other:1"}]})
    box.succeed(f"install -d -m 755 /run/fake-ollama && echo '{half}' > /run/fake-ollama/tags.json")
    box.succeed("sleep 12")
    box.fail(f"test -e {STATE}/sealed")                    # a look-alike name is not the model
    assert elements() == [UID], "D5: sealed on a model that is not the configured one"
    before = box.succeed("systemctl show -p ActiveEnterTimestampMonotonic --value ollama.service").strip()
    whole = json.dumps({"models": [{"name": "qwen3.5:9b", "model": "qwen3.5:9b"}]})
    box.succeed(f"echo '{whole}' > /run/fake-ollama/tags.json")
    box.wait_until_succeeds(f"test -e {STATE}/sealed", timeout=40)
    m5 = marker()
    assert m5["reason"] == "complete", f"D5: {m5}"
    box.wait_until_succeeds(f"test -f {RUN}/login-ok", timeout=20)
    assert elements() == [], f"D5: sealed with a non-empty set: {elements()}"
    assert box.succeed(f"stat -c '%U %a' {RUN}/login-ok").split() == ["root", "644"], "D5: the token"
    assert json.loads(box.succeed(f"cat {RUN}/status")) == {"state": "sealed", "reason": "complete"}, "D5: the status line"
    try:
        box.wait_for_unit("getty@tty1.service")
    except Exception:       # what it ran is the first thing to know (a masked template drop-in shows here)
        print(box.execute("systemctl status --no-pager -l getty@tty1.service agentos-seal.service 2>&1 | head -50")[1])
        raise
    box.wait_until_succeeds("pgrep -u agent >/dev/null", timeout=60)            # CONTROL for every no_agent above
    box.wait_until_succeeds(f"systemctl is-active --quiet user@{agent_uid}.service", timeout=60)
    assert box.succeed("systemctl show -p ActiveEnterTimestampMonotonic --value ollama.service").strip() != before, "D5: the fetcher was not restarted"
    box.succeed("journalctl -t agos-window --no-pager | grep -q 'sealed reason=complete set_flushed=1'")
    box.wait_for_unit("multi-user.target")
    box.succeed("chvt 5")
    box.wait_until_succeeds("pgrep -u agent -t tty5 >/dev/null", timeout=60)    # CONTROL for D2: the same door, open
    box.succeed("systemctl restart serial-getty@ttyS1.service")
    box.wait_until_succeeds("pgrep -u agent -t ttyS1 >/dev/null", timeout=60)   # CONTROL for D2: the serial door, open
    box.succeed("chvt 1")
    no_violation("D5")

    # ── D6 ──────────────────────────────────────────────────────────────────────────────────────
    box.succeed(f"rm {STATE}/sealed {STATE}/window.json")
    box.fail("systemctl restart agentos-setup-window.service")
    assert elements() == [], f"D6: the window opened under a running agent: {elements()}"
    box.fail(f"test -e {STATE}/window.json")
    box.succeed("journalctl -t agos-window --no-pager | grep -q 'the window stays closed until a reboot'")
    box.succeed("systemctl restart nftables.service; sleep 3")
    assert elements() == [], f"D6: an nftables restart opened it: {elements()}"
    no_violation("D6")
    reprovision("D6")
    no_agent("D6 after the reboot")
    box.fail(f"test -e {RUN}/login-ok")

    # ── D7 ──────────────────────────────────────────────────────────────────────────────────────
    box.succeed("systemctl kill --signal=KILL agentos-seal.service")
    box.wait_until_succeeds("systemctl show -p ActiveState --value agentos-seal.service | grep -qx failed", timeout=30)
    box.succeed("sleep 3")
    box.fail(f"test -e {STATE}/sealed")
    box.fail(f"test -e {RUN}/login-ok")
    assert elements() == [UID], f"D7: a killed wait must leave the window to the tick: {elements()}"
    assert box.succeed("systemctl show -p ActiveState --value getty@tty1.service").strip() != "active", "D7: tty1's getty followed a wait that failed"
    no_agent("D7 after the kill")
    box.succeed("systemctl start --no-block getty@tty1.service")             # anything that starts it runs the wait again
    box.wait_until_succeeds("systemctl show -p ActiveState --value agentos-seal.service | grep -qx activating", timeout=30)
    no_agent("D7 while the second wait runs")
    box.succeed(f"install -d -m 755 /run/fake-ollama && echo '{whole}' > /run/fake-ollama/tags.json")
    box.wait_until_succeeds(f"test -f {RUN}/login-ok", timeout=40)
    assert marker()["reason"] == "complete" and elements() == [], f"D7: {marker()} {elements()}"
    box.wait_for_unit("getty@tty1.service")
    box.wait_until_succeeds("pgrep -u agent >/dev/null", timeout=60)
    no_violation("D7")

    # ── D8 ──────────────────────────────────────────────────────────────────────────────────────
    reprovision("D8")
    no_agent("D8")
    # Ten minutes is the budget; leave 70 seconds of it (the books are root's, and this is root).
    # Under the state lock, so the once-a-minute tick cannot write its own copy back in between.
    box.succeed(f"flock {STATE}/.lock sh -c \"jq -c '.budget_at_boot = 70 | .remaining = 10' {STATE}/window.json > {STATE}/w.tmp && chmod 600 {STATE}/w.tmp && mv {STATE}/w.tmp {STATE}/window.json\"")
    tty1_until("ran out", "closed to the network", "/var/lib/agent-os/seal/sealed")      # why, and how to set up again
    box.wait_until_succeeds(f"test -e {STATE}/sealed", timeout=30)
    assert marker()["reason"] == "budget", f"D8: {marker()}"
    assert elements() == []
    box.wait_for_unit("getty@tty1.service")
    box.wait_until_succeeds("pgrep -u agent >/dev/null", timeout=60)
    no_violation("D8")
    assert box.succeed("systemctl is-active agentos-seal-check.service").strip() == "active", "the existing seal check still passes"

    # CONTROL for the sampler, last: the agent is running now. Put the uid back in the set by hand for
    # two seconds. A sampler that cannot see THAT proved nothing above.
    box.succeed(f"nft add element inet agentos_egress setup_uid '{{ {UID} }}' && sleep 2 && nft flush set inet agentos_egress setup_uid")
    seen = box.succeed("cat /var/lib/invariant-violations")
    assert "agent" in seen or seen.strip(), f"CONTROL: the sampler did not record a planted violation: {seen!r}"
  '';
}
