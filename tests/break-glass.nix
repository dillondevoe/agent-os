# tests/break-glass.nix — THE REPAIR CONSOLE IS REACHABLE, on a booted box (modules/break-glass.nix).
#
# tty3 is the one interactive root door: a plain login prompt, root's password, nowhere else. A human
# gets to tty3 with Ctrl+Alt+F3, and what starts a console on a VT switch is logind asking for
# `autovt@tty3.service`. Until 2026-10-12 the override for tty3 was a unit file of its own named
# `getty@tty3.service` (nixpkgs writes a template INSTANCE as a whole unit unless told
# `overrideStrategy = "asDropin"`), so `autovt@tty3` stayed a different unit and ran the template:
# an autologin as the agent. The root prompt never appeared. Unit text could not show that; this does.
#
#   B1  a VT switch to tty3 shows a login PROMPT: no automatic login, no process of the agent's
#       account on that console; `autovt@tty3` and `getty@tty3` are ONE unit, running break-glass's
#       own command line.
#   B2  a wrong password is refused.
#   B3  root with its password gets a root shell on tty3.
#   B4  after logout the prompt comes back (the console is a getty, not a one-shot).
#   B5  CONTROL: tty4 is unchanged: the same VT switch there logs the agent in automatically.
#   B6  `nohash`: with no break-glass password provisioned, tty3 still shows the prompt and lets
#       NOBODY in (file absent => login disabled; no passwordless root).
# The image here is the unsealed product image with the setup window off. The build-sealed images
# are covered by the flake assertion (the unit text is the same drop-in); the window-open case has
# its own leg in tests/seal-wait.nix.
{ pkgs, baseModules }:

let
  common = { lib, ... }: {
    imports = baseModules;
    # Not under test, and it would sit two minutes waiting for a registry this VM cannot reach.
    systemd.services.agent-os-pull-model.wantedBy = lib.mkForce [ ];
    systemd.timers.agent-os-pull-model.wantedBy = lib.mkForce [ ];
    virtualisation.memorySize = 2048;
    virtualisation.cores = 2;
    environment.systemPackages = [ pkgs.kbd ];
  };
in
pkgs.testers.runNixOSTest {
  name = "agentos-break-glass";

  # The product reads root's hash from a file the installer writes. The test gives root one directly
  # ("glass"); `nohash` leaves the product's setting alone, and the file does not exist.
  nodes.box = { lib, ... }: {
    imports = [ common ];
    users.users.root.hashedPasswordFile = lib.mkForce null;
    users.users.root.hashedPassword = "$6$agentostest$ewKMzyEYxD9L/CKzg.LIAt.tkmw2r4LmmpQUQbKOh2ON/RNQ9GKY/LjGcp2m8zAumQpHbcCJ6vGYVGnQmZS5z.";
  };
  nodes.nohash = { ... }: { imports = [ common ]; };

  testScript = ''
    import time

    # The console is never cleared (--noclear, and login retries in place), so "the word is on the
    # screen" says nothing about THIS attempt. Everything below counts occurrences instead.
    def screen(m, tty):
        return m.get_tty_text(str(tty))

    def wait_count(m, tty, needle, n, what, timeout=40):
        for _ in range(timeout):
            if screen(m, tty).count(needle) >= n:
                return
            time.sleep(1)
        raise Exception(f"tty{tty}: {what} (wanted {n} x {needle!r}):\n" + screen(m, tty))

    def prompt(m, tty):
        # Switch to the console the way a human does, and wait for a getty to ask for a name.
        m.succeed(f"chvt {tty}")
        m.wait_until_tty_matches(str(tty), "login:")
        m.succeed("sleep 2")                      # a getty that has just started drops what was typed before it read
        return screen(m, tty)

    def type_login(m, tty, name, password):
        before = screen(m, tty)
        asked = before.count("Password:")
        m.send_chars(name + "\n")
        try:
            wait_count(m, tty, "Password:", asked + 1, "never asked for a password", timeout=15)
        except Exception:
            if screen(m, tty) != before:
                raise
            m.send_chars(name + "\n")            # nothing moved at all: the name was typed before the getty read
            wait_count(m, tty, "Password:", asked + 1, "never asked for a password")
        m.send_chars(password + "\n")

    def failures(m):
        # What the login stack itself recorded. The screen cannot be the witness across attempts: the
        # getty respawns after a few failures and the console is wiped with it.
        #
        # Count login's OWN line, not pam_unix's. A refused password produces two journal lines in
        # order: `pam_unix(login:auth): authentication failure` the moment the password is checked, then
        # a randomised fail delay (pam_fail_delay, ~2-3 s), THEN `FAILED LOGIN (n) on '/dev/tty3' FOR
        # 'root'` as login re-prompts (or exits and the getty respawns). Between those two lines the tty
        # is still owned by the delay: anything typed is echoed and then flushed by the new prompt. Until
        # 2026-10-14 this counted both lines, so `refused` was satisfied by the FIRST one and the next
        # attempt's name landed in that window whenever the delay drew long (red on main at bcbb93a:
        # delay 2.52 s against the harness's 2.2 s; the same tree passed at 2.07 s). Only the second line
        # proves the console is ready for another name.
        return int(m.succeed("journalctl -b --no-pager | grep -c 'FAILED LOGIN' || true").strip() or 0)

    def refused(m, tty, name, password, tag):
        before = failures(m)
        type_login(m, tty, name, password)
        m.wait_until_succeeds(f"test \"$(journalctl -b --no-pager | grep -c 'FAILED LOGIN')\" -gt {before}", timeout=60)
        assert m.execute(f"pgrep -t tty{tty} -x bash")[0] != 0, f"{tag}: a shell is running on the console"
        prompt(m, tty)                           # the console asks again (same login, or a new getty)

    box.start()                              # one machine at a time: nothing here needs both
    box.wait_for_unit("multi-user.target")

    # ── B1 ──────────────────────────────────────────────────────────────────────────────────────
    text = prompt(box, 3)
    assert "automatic login" not in text, f"B1: tty3 logged someone in automatically:\n{text}"
    assert box.execute("pgrep -t tty3 -u agent")[0] != 0, "B1: a process of the agent's account is on the repair console"
    names = box.succeed("systemctl show -p Names --value autovt@tty3.service")
    assert "getty@tty3.service" in names and "autovt@tty3.service" in names, f"B1: autovt@tty3 and getty@tty3 are different units: {names}"
    cmd = box.succeed("systemctl show -p ExecStart --value autovt@tty3.service")
    assert "bin/login" in cmd and "--autologin" not in cmd and "agetty" in cmd, f"B1: the console's command line: {cmd}"
    assert box.succeed("systemctl show -p TTYPath --value autovt@tty3.service").strip() == "/dev/tty3", "B1: the console is not the template's getty"

    # ── B2 ──────────────────────────────────────────────────────────────────────────────────────
    refused(box, 3, "root", "not-the-password", "B2")
    # The agent's account cannot read or write the repair console.
    box.fail("runuser -u agent -- sh -c 'cat /dev/vcs3 >/dev/null'")
    box.fail("runuser -u agent -- sh -c ': >/dev/tty3'")
    box.succeed("sh -c 'cat /dev/vcs3 >/dev/null'")                 # CONTROL: root can

    # ── B3 ──────────────────────────────────────────────────────────────────────────────────────
    type_login(box, 3, "root", "glass")
    box.wait_until_succeeds("pgrep -t tty3 -u root -x bash >/dev/null", timeout=60)

    # ── B4 ──────────────────────────────────────────────────────────────────────────────────────
    box.send_chars("exit\n")
    box.wait_until_fails("pgrep -t tty3 -x bash >/dev/null", timeout=30)
    text = prompt(box, 3)
    assert "automatic login" not in text, f"B4: after logout tty3 logged someone in automatically:\n{text}"

    # ── B5 ──────────────────────────────────────────────────────────────────────────────────────
    box.succeed("chvt 4")
    box.wait_until_succeeds("pgrep -t tty4 -u agent >/dev/null", timeout=60)
    assert "automatic login" in box.get_tty_text("4"), "B5: CONTROL: tty4 did not log the agent in automatically"
    box.succeed("chvt 1")

    # ── B6 ──────────────────────────────────────────────────────────────────────────────────────
    box.shutdown()
    nohash.start()
    nohash.wait_for_unit("multi-user.target")
    nohash.fail("test -e /etc/agent-os/break-glass.hash")
    text = prompt(nohash, 3)
    assert "automatic login" not in text, f"B6: tty3 logged someone in automatically:\n{text}"
    for password in ("", "root", "glass"):
        refused(nohash, 3, "root", password, f"B6 ({password!r})")
    assert nohash.execute("pgrep -t tty3 -u agent")[0] != 0, "B6: a process of the agent's account is on the repair console"
  '';
}
