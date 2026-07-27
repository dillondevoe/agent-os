# Agent OS — v0.1 scaffold

A computer whose shell is an agent, not a desktop. Boot → autologin → you're
talking to it. Files are markdown memories (path = meaning). The agent can
rewrite the machine's own config and rebuild.

Vision + spec: `~/jarvis-sync/VISION-agent-os-2026-07-27.md`.

## What's here (spec-independent scaffold — started 2026-07-27)
```
flake.nix                 the whole machine + a `vm` output to test before hardware
configuration.nix         boring base: user, network, toolbox, flakes on, no desktop
modules/agent-shell.nix   THE module — autologin tty1 → exec the agent, no bash prompt
bin/agent-shell           login program: seeds memory tree, boot banner, hands off to brain
home/memory/              seed of the markdown-memory home tree
```

## Prove it boots-and-talks (in a VM, no Dell needed yet)
```sh
cd ~/agent-os
nix build .#vm
./result/bin/run-agent-os-vm      # boots into the agent shell in a window
```
The VM comes up, autologins tty1, seeds `~/memory`, and drops into the brain
(or the placeholder memory-REPL if no brain is installed yet).

## Brain
- **v0.1 (now):** cloud — Claude Code as the login program. Fastest path to
  "burn it, boot it, talk to it." `BRAIN=claude` (default).
- **Phase 1.5:** local-model floor (ollama / llama.cpp) so it thinks offline —
  ties into the grid-down resilient-mesh goal. `setup-brain.sh` will install it.

## Then, onto the Dell Latitude
1. Boot NixOS installer USB on the Latitude.
2. `nixos-generate-config` → commit its `hardware-configuration.nix` here.
3. `nixos-install --flake .#agentos` → reboot → it comes up talking.

## Honest status
This is a **v0 skeleton**, not flash-ready. It needs: the Dell's
hardware-configuration.nix, a VM smoke-test pass, and the brain installer.
Built spec-independent first so the moment Dillon reports the model + RAM we
finalize and test. The security-kernel hardening (autonomous agent + root =
the real work) is roadmap, not v0.1 — v0.1 is single-user demo box.
