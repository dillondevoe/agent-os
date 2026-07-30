```text
   ▄▀█ █▀▀ █▀▀ █▄░█ ▀█▀   █▀█ █▀
   █▀█ █▄█ ██▄ █░▀█ ░█░   █▄█ ▄█
        the computer you talk to
```

# Agent OS

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
&nbsp;![Built on NixOS](https://img.shields.io/badge/built%20on-NixOS-5277C3?logo=nixos&logoColor=white)
&nbsp;![Status: v0.1](https://img.shields.io/badge/status-v0.1%20%C2%B7%20rough%20on%20purpose-e8a33d)
&nbsp;![Local-first](https://img.shields.io/badge/local--first-no%20telemetry-2ea44f)

**A computer whose shell is an AI, not a desktop.** Boot the machine and you're
talking to it — a model running *locally*, offline-capable, with no telemetry and
no cloud dependency. Your files are markdown memories the agent reads and writes.
You change a setting by asking. By default nothing leaves the machine.

Built by a musician, out loud, one piece a week. This is **v0.1** — rough on purpose.

---

## Install (one line)

On a spare machine, boot the [NixOS minimal installer](https://nixos.org/download#nixos-iso),
connect to the internet (`nmtui`), then:

```sh
curl -sL https://raw.githubusercontent.com/dillondevoe/agent-os/main/install.sh | sudo bash
```

> ⚠️ **This ERASES the target disk.** Use a machine you don't need. The installer shows you
> the disk and makes you type `YES` before it touches anything.

It partitions, installs, and on first boot pulls a local model (Qwen 2.5 7B, ~4.7GB).
One more reboot and you're talking to a fully local AI — unplug the ethernet and it still works.

**Just want to see it first?** Boot it in a VM, no hardware or wipe needed:

```sh
git clone https://github.com/dillondevoe/agent-os && cd agent-os
nix build .#vm && ./result/bin/run-agent-os-vm
```

---

## The idea

Three moves, each a deliberate inversion of how computers work now:

1. **The interface is a colleague, not a desktop.** No icons, no windows. The first thing that
   exists on boot is a conversation. The agent is your file manager, launcher, and settings panel.
2. **Memory is the filesystem.** Facts live as markdown at `~/memory/<domain>/<thing>.md` — *path
   is meaning*. What the agent knows, you can read, edit, and grep. No opaque vector store.
3. **Sovereign by default.** The brain is a model on *your* machine (works with the internet
   unplugged). The firewall drops all outbound traffic except what you explicitly allow — so it
   *can't* phone home. No account, no analytics, no surveillance. Us, the machine, and the OS.

Why: as AI makes surveillance cheaper, "free software for your whole life" gets to be a worse
trade. Agent OS is a bet that you can have an AI that's powerful **and** yours — owned, auditable,
offline. Open source isn't a nice-to-have here; for a sovereignty tool it's the only version
anyone should trust.

---

## Status (v0.1)

**Works:** bootable sovereign NixOS · autologin → agent shell · local Qwen brain (auto-pulled on
first boot) · markdown memory (`remember` / `recall` / `tree`) · clean-room egress wall (nftables
default-DROP outbound, sealable to nixpkgs-only) · **fail-loud egress seal** — if the wall ever fails
to load on a sealed box, the machine drops the network and refuses to hand you the agent rather than
silently running unsealed (fail-*closed*, not fail-silent) · no cloud path, no telemetry · graceful
memory-REPL floor when no model is present (never crash-loops).

**Not yet:** the capability broker — the safety layer that gates what the agent can *do* — is
designed and being integrated, but not fully wired yet, so **treat the agent as trusted-but-unsandboxed
for now**. The last hole here is passwordless `sudo` defeating the egress seal; the sudo-removal posture
(agent out of `wheel`, root only via a narrow interactive break-glass door) is in review and landing
imminently. Also coming: the agent's hands (a tool-use loop through the broker), a GUI-guest for the
rare things that need a screen, polished onboarding. It's a foundation, not a finished product.

Tested on a Dell Latitude 5440 (13th-gen Intel, 32GB, CPU inference). Modern Dells hide the NVMe
behind Intel VMD — if boot times out looking for the disk, set **BIOS → SATA Operation → AHCI**
(the initrd also carries the `vmd` driver, so leaving VMD on works too).

---

## How it's built

Declarative NixOS — the machine is a reproducible expression, and the agent can rewrite its own
`/etc/nixos` and rebuild.

```
flake.nix                 the whole machine (+ a `vm` output to boot before hardware)
configuration.nix         base system: user, network, bootloader, disk layout, no desktop
install.sh                the one-line installer (partition → install → first-boot model pull)
modules/agent-shell.nix   THE module — autologin tty1 → exec the agent (no bash prompt)
modules/brain.nix         local Ollama daemon + first-boot model bootstrap
modules/clean-room.nix    the egress wall (default-DROP outbound; sealed = nixpkgs-only)
bin/agent-shell           login program: seeds the memory tree, boot banner, hands to the brain
bin/mem                   the markdown-memory tool (path = meaning)
bin/brain-ollama          the local-model chat shim (stdlib, no deps)
```

> Flakes only see git-tracked files — `git add` after editing, or `nix build` silently uses the old
> version. `nix flake check` validates the whole config without hardware.

---

## License

MIT — see [LICENSE](LICENSE). Take it, fork it, build your own. That's the point.
