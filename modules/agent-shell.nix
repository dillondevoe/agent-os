# The module that makes this Agent OS instead of "a bare NixOS box."
# Boot -> tty1 -> autologin -> the agent. No display manager, no bash prompt.
{ config, pkgs, lib, ... }:

let
  # The memory-as-filesystem tool. Must be on PATH as `mem` (agent-shell + the brain both
  # call it); give it an explicit python3 shebang so it runs on the target with no deps.
  memBin = pkgs.writeScriptBin "mem"
    ("#!${pkgs.python3}/bin/python3\n" + builtins.readFile ../bin/mem);
  # The offline/sovereign brain — a stdlib Ollama chat shim. agent-shell falls to
  # this (the "local floor") when no cloud brain is installed and a model is
  # reachable. Same python3-shebang wrapping as mem so it needs no runtime deps.
  brainOllamaBin = pkgs.writeScriptBin "brain-ollama"
    ("#!${pkgs.python3}/bin/python3\n" + builtins.readFile ../bin/brain-ollama);
  # The tool-calling brain (v0.2) — a stdlib Ollama function-calling loop. agent-shell
  # PREFERS this over brain-ollama as the local brain because it can ACT (emit tool-calls
  # the loop dispatches), not only chat. Same python3-shebang wrapping so it needs no
  # runtime deps. In v0.2 A1 its only wired tool is an inert echo; A2 routes real caps
  # through the mcp|broker wall. It sits on the UNTRUSTED side and makes no security decisions.
  agentLoopBin = pkgs.writeScriptBin "agent-loop"
    ("#!${pkgs.python3}/bin/python3\n" + builtins.readFile ../bin/agent-loop);
  # The launcher lives in the repo so it's easy to iterate; installed to /run/current-system.
  # It calls `mem`, `agent-loop`, and `brain-ollama` by bare name (PATH), so all must be
  # installed alongside it.
  agentShell = pkgs.writeShellScriptBin "agent-shell"
    (builtins.readFile ../bin/agent-shell);
in {
  environment.systemPackages = [ agentShell memBin brainOllamaBin agentLoopBin ];

  # Autologin the human on tty1...
  services.getty.autologinUser = "agent";

  # ...and make the agent their login program. Setting the user's shell to the
  # launcher means the FIRST thing that exists on boot is the conversation.
  # (We keep bash as a real shell underneath so the agent can spawn subshells to
  #  run commands — the agent is the *interface*, not a jail.)
  users.users.agent.shell = pkgs.bash;
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
