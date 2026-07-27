# The module that makes this Agent OS instead of "a bare NixOS box."
# Boot -> tty1 -> autologin -> the agent. No display manager, no bash prompt.
{ config, pkgs, lib, ... }:

let
  # The launcher lives in the repo so it's easy to iterate; installed to /run/current-system.
  agentShell = pkgs.writeShellScriptBin "agent-shell"
    (builtins.readFile ../bin/agent-shell);
in {
  environment.systemPackages = [ agentShell ];

  # Autologin the human on tty1...
  services.getty.autologinUser = "dtd";

  # ...and make the agent their login program. Setting the user's shell to the
  # launcher means the FIRST thing that exists on boot is the conversation.
  # (We keep bash as a real shell underneath so the agent can spawn subshells to
  #  run commands — the agent is the *interface*, not a jail.)
  users.users.dtd.shell = pkgs.bash;
  environment.loginShellInit = ''
    # Only take over the primary console — other ttys stay plain for rescue.
    if [ "$(tty)" = "/dev/tty1" ] && [ -z "$AGENT_OS_ACTIVE" ]; then
      export AGENT_OS_ACTIVE=1
      exec ${agentShell}/bin/agent-shell
    fi
  '';

  # The markdown-memory home tree is seeded on first login by the launcher.
  # (Path = meaning: ~/memory/<domain>/<thing>.md is a fact the agent knows.)
}
