# tests/surface-choice.nix — first login on a booted machine (docs/design/first-login-and-setup-window.md
# §3-§4, slice 1; the spec's `surface-choice-store` and `first-login-choice-survives`).
#
# A small node, not the full developer image (which CI cannot build): the two surface modules, an
# `agent` account that autologins on tty1, and an `operator` whose login shell is the choice wrapper.
#
#   S1  the questions are on tty1 BEFORE the agent's getty; no agent process exists while they are.
#   S2  non-default answers typed on the console reach the store: root-owned, 0644, exact text
#       (the runtime directory is still there when the root helper reads it).
#   S3  the tokens file is gone; the front end ran as agentos-setup, which has no shell, home or groups.
#   S4  the agent cannot write, replace or remove the store.
#   S5  the wrappers follow the store: `agentos-terminal` is the chosen terminal, the operator's login
#       shell is the chosen shell, and SHELL / PATH in the environment change neither.
#   S6  a malformed store means the defaults, with a journal line; the wrappers still work.
#   S7  the questions are not asked again once the store exists.
{ pkgs }:

pkgs.testers.runNixOSTest {
  name = "agentos-surface-choice";

  nodes.box = { config, ... }: {
    imports = [ ../modules/surface-options.nix ../modules/surface-choice.nix ];
    agentos.desktop = "none";
    agentos.firstLogin.enable = true;
    users.users.agent = { isNormalUser = true; };
    users.users.operator = { isNormalUser = true; shell = config.agentos.surfaceInternal.shellPkg; };
    services.getty.autologinUser = "agent";
    environment.systemPackages = with pkgs; [ coreutils util-linux procps ];
    virtualisation.memorySize = 2048;
  };

  testScript = ''
    STORE = "/var/lib/agent-os/surface.toml"

    box.start()
    # S1 — the questions own tty1 before the agent's getty; the agent has no process yet.
    box.wait_until_tty_matches("1", "Terminal:")
    state = box.succeed("systemctl show -p ActiveState --value agentos-firstlogin.service").strip()
    assert state == "activating", "S1: the front end is " + state
    getty = box.succeed("systemctl show -p ActiveState --value getty@tty1.service").strip()
    assert getty == "inactive", "S1: the agent's getty is already " + getty
    box.fail("pgrep -u agent")
    box.fail("test -e " + STORE)
    screen = box.get_tty_text("1")
    assert "Desktop:" not in screen, "S1: a desktop question with one desktop offered"
    assert "kitty   (default)" in screen, "S1: the default terminal is not marked: " + screen
    front = box.succeed("systemctl show -p MainPID --value agentos-firstlogin.service").strip()
    owner = box.succeed(f"ps -o user= -p {front}").strip()
    assert owner == "agentos-setup", "S3: the front end runs as " + owner

    # Terminals are offered in name order: alacritty, foot, ghostty, kitty. A bad line first.
    box.send_chars("9\n")
    box.wait_until_tty_matches("1", "Please type a number")
    box.send_chars("2\n")
    box.wait_until_tty_matches("1", "Shell:")
    box.send_chars("3\n")                                  # bash, fish, zsh

    # S2 — the choice reaches the store.
    box.wait_until_succeeds("test -e " + STORE, timeout=60)   # a lost choice fails here, fast
    box.wait_for_unit("getty@tty1.service")
    text = box.succeed("cat " + STORE)
    assert text == 'schema = 1\ndesktop = "none"\nterminal = "foot"\nshell = "zsh"\n', "S2: store text: " + repr(text)
    assert box.succeed("stat -c '%U:%G %a' " + STORE).strip() == "root:root 644", "S2: store owner/mode"
    assert box.succeed("agentos-surface").split() == ["desktop=none", "terminal=foot", "shell=zsh"], "S2: reader"

    # S3 — the tokens file is gone; the account has nothing.
    box.fail("test -e /run/agentos-firstlogin/choice")
    assert box.succeed("id -Gn agentos-setup").split() == ["agentos-setup"], "S3: groups"
    passwd = box.succeed("getent passwd agentos-setup").strip().split(":")
    assert passwd[6].endswith("nologin") and passwd[5] == "/var/empty", "S3: shell/home: " + repr(passwd)

    # S4 — the agent cannot change the store.
    box.wait_until_succeeds("pgrep -u agent")
    box.fail("runuser -u agent -- sh -c 'echo x > " + STORE + "'")
    box.fail("runuser -u agent -- rm -f " + STORE)
    box.fail("runuser -u agent -- sh -c 'cp " + STORE + " /tmp/s && mv -f /tmp/s " + STORE + " </dev/null'")
    box.fail("runuser -u agent -- touch /var/lib/agent-os/x")
    assert box.succeed("cat " + STORE) == text, "S4: the store changed"

    # S5 — the wrappers follow the store, whatever the environment says.
    assert "foot" in box.succeed("runuser -u agent -- agentos-terminal --version"), "S5: terminal"
    poisoned = box.succeed("runuser -u agent -- env SHELL=/bin/evil TERMINAL=kitty PATH=/tmp/evil:$PATH agentos-terminal --version")
    assert "foot" in poisoned, "S5: the environment steered the terminal: " + poisoned
    assert box.succeed("runuser -l operator -c 'echo zsh=$ZSH_VERSION'").strip() != "zsh=", "S5: the operator's login shell is not zsh"
    assert box.succeed("getent passwd operator").strip().endswith("/bin/agentos-shell"), "S5: operator shell field"
    assert box.succeed("runuser -l operator -c 'env SHELL=/bin/evil agentos-shell -c \"echo z=$ZSH_VERSION\"'").strip() != "z=", "S5: SHELL steered the shell"

    # S6 — a malformed store: the defaults, a journal line, wrappers still work.
    box.succeed("cp " + STORE + " /root/good && printf 'schema = 1\\ndesktop = \"none\"\\nterminal = \"/bin/sh\"\\nshell = \"zsh\"\\n' > " + STORE)
    assert box.succeed("agentos-surface 2>/dev/null").split() == ["desktop=none", "terminal=kitty", "shell=bash"], "S6: defaults"
    assert "kitty" in box.succeed("runuser -u agent -- agentos-terminal --version"), "S6: terminal default"
    box.succeed("journalctl -t agos-choice --no-pager | grep -q 'terminal: store terminal is not an offered value; using the default'")
    assert box.succeed("runuser -l operator -c 'echo bash=$BASH_VERSION'").strip() != "bash=", "S6: shell default"
    box.succeed("ln -sf /root/good " + STORE + ".link && mv -T " + STORE + ".link " + STORE)   # a symlink to a valid store
    assert box.succeed("agentos-surface 2>/dev/null").split()[1] == "terminal=kitty", "S6: a symlinked store was followed"
    box.succeed("rm " + STORE + " && install -m 644 /root/good " + STORE)
    assert box.succeed("agentos-surface").split()[1] == "terminal=foot", "S6: restored"

    # S7 — not asked again.
    box.succeed("systemctl start agentos-firstlogin.service agentos-surface-apply.service")
    assert box.succeed("systemctl show -p ConditionResult --value agentos-firstlogin.service").strip() == "no", "S7: asked again"
    assert box.succeed("cat " + STORE) == text, "S7: the store changed"
  '';
}
