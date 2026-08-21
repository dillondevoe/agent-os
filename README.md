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

**No path to root:** the agent runs as an unprivileged user with **no `sudo`, not in `wheel`, no
password** — so it cannot climb to uid 0 and cannot `sudo` around the egress seal. Root is reachable
only by a human at a narrow interactive break-glass door (a password login on a separate tty).
Together with the fail-loud seal above, that's the sovereignty invariant made real, not just declared.

**Also works (2026-08-12):** the capability *broker* — the fine-grained layer that gates each thing
the agent can *do* through a tool — is shipped: `bin/broker` (decision pipeline), `bin/confirm` +
`modules/confirm.nix` (the human-confirm channel), and the invoke seam are wired together in the
sovereign module set, with green `broker-core` / `confirm-channel` / `seam-live` checks. Of the
7 registry-declared capabilities, 3 have real implementations (`mem.recall`, `mem.remember`,
`capabilities.list`); `file.read`, `file.write`, `net.fetch`, and `message.send` are declared in
the registry but still stubbed pending their impls. Separately, the ambient `agos-*` hands
(calendar, email, files, notes, web, and more) are fully live on the open variant. Also coming: a
GUI-guest for the rare things that need a screen, polished onboarding. It's a foundation, not a
finished product.

Tested on a Dell Latitude 5440 (13th-gen Intel, 32GB, CPU inference). Modern Dells hide the NVMe
behind Intel VMD — if boot times out looking for the disk, set **BIOS → SATA Operation → AHCI**
(the initrd also carries the `vmd` driver, so leaving VMD on works too).

---

## Where this goes
Hardware-tiered local models (8GB potato → 32GB beast), a sysops-trained model of our own
built rung by rung, and an inference mesh of cooperating machines. The full arc:
**[ROADMAP.md → The North Star](ROADMAP.md#the-north-star--where-years-of-this-go)**.

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
tests/run-local.sh        run every nix-free property battery in one command
```

> Flakes only see git-tracked files — `git add` after editing, or `nix build` silently uses the old
> version. `nix flake check` validates the whole config without hardware.

### Tests

Every property battery runs in CI under a **sandboxed** `nix flake check` (see
`.github/workflows/flake-check.yml`) — that is the merge gate, and `sandbox = true` is
load-bearing: a permissive local nix can pass where the clean-room build fails.

```
nix flake check --option sandbox true -L     # the real gate: all batteries + nix eval
bash tests/run-local.sh                      # the nix-free subset, one table, seconds
bash tests/run-local.sh -v                   # ...with output from any failure
```

`tests/run-local.sh` exists because each battery takes its own positional arguments
(between one and seven), so running one by hand means reconstructing its signature. It
wires up the nine that need no Nix store — front-door kick, agos-events, comms
shadow/live contracts, agent-loop, audit, taint, mem-cap, mcp — and is meant for a fast
local loop, **not** as merge evidence. The registry- and store-dependent batteries
(broker, cap, confirm, seam-live, nft-ruleset, open-imports, seal-faildown) only run
under the flake check.

---

## The open variant does not build on your machine

Stated plainly because it is not otherwise discoverable, and someone will otherwise lose a CI
run finding out (2026-08-20, PR #136, dead at 2m25s having never booted a VM).

`nixosConfigurations.agentos-open` — and therefore any VM test that composes `openModules` —
**cannot be built on a machine that has not staged a particular GGUF by hand.**
`modules/model-3b-open.nix` pins the 3B Augur switchboard weights as a fixed-output derivation
with **no fetcher**: its builder's whole job is to print the staging command and exit 1.

```
nix-store --add-fixed sha256 /path/to/qwen2.5-3b-augur-q4_k_m.gguf
```

`configuration-open.nix` imports that module unconditionally, so the blob is in the closure of
the whole open system. Evaluation succeeds; the **build** fails. `modules/model-open.nix` is a
milder version of the same thing — buildable, but it pulls a multi-gigabyte 9B from HuggingFace
on any store that lacks it.

**The consequence, which matters more than the inconvenience.** The sealed lane has seven
behavioural VM tests in the slow lane. The open lane — which is where the *entire Agent OS
engine* ships, since `modules/selfimprove-open.nix` and the `agos_*` engine are open-lane only —
had none, and structurally could not have any, because no CI runner can build the image. That
asymmetry looked like a gap in effort. It was a gap in *possibility*, and nothing in this repo
said so.

**Working around it, if you are writing an open-lane test.** Disable the seed units in your test
node, which drops the weights from the closure because they are referenced only through the unit
scripts (`tests/selfimprove-loop-runs.nix` does this; qwen paths in its derivation closure go
6 → 0):

```nix
systemd.services.agos-seed-model-3b.enable = false;
systemd.services.agos-seed-model.enable    = false;
systemd.services.agos-seed-lora.enable     = false;
```

That makes *your test* runnable anywhere. It does **not** mean the open image as shipped is
covered by CI — it is not, and a green from such a test must not be read as saying otherwise.
Say so in the test file, as that one does.

Whether to fix this properly — publish the 3B GGUF as a release asset and use `fetchurl` (the
precedent exists in-repo: `modules/model-lora-open.nix` already does exactly that for the LoRA
adapter), or gate the model modules behind an option — is an open decision, tracked as task 317.

---

## License

MIT — see [LICENSE](LICENSE). Take it, fork it, build your own. That's the point.
