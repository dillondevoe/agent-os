# Security policy

## Supported versions

Only `main` is supported. There are no maintained release branches; fixes land on `main`
and nowhere else.

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** for this repository: open the
**Security** tab, then **Report a vulnerability**. Do not open a public issue or pull request
for a suspected vulnerability, and do not post exploit details publicly until a fix has landed.

This is a small, one-owner project run on a best-effort basis; there is no response-time
guarantee.

## What is in scope

Agent OS makes a few claims it calls the *sovereignty invariants*. A way to break any of them,
on the variant where it is claimed, is a vulnerability:

- **Egress.** On the sealed variants the agent, its local model and every capability have no
  path to move bytes off the machine: the nftables output chain is default-DROP, the only
  exceptions being the nixpkgs binary cache and time sync (`modules/clean-room.nix`).
- **Fail-loud seal.** If the wall fails to load on a sealed box, the machine drops the network
  and refuses to hand over the agent rather than running unsealed.
- **No path to root.** The agent runs as an unprivileged user with no `sudo`, not in `wheel`,
  no password. Root is reachable only through the interactive break-glass login.
- **The capability wall.** Every action the agent takes through a tool goes through the broker
  (`bin/broker`) and its registry (`modules/capability-registry.nix`); the broker, registry,
  audit log, taint state and model weights are protected paths no capability may write, and the
  forbidden T3 operations are not expressible as requests.
- **Provenance.** The audit log and taint tracking record what they claim to record.

Also in scope: secrets or personal data committed to this public repository.

## What is out of scope

- The **open variant** (`agentos-open`) is a deliberately permissive dev box: no egress wall,
  Tailscale and SSH enabled, a human `operator` account with passwordless sudo. Those are
  design choices, not vulnerabilities. Flaws in how it is built are still welcome.
- Anything that requires physical access to an unlocked machine, or that assumes root has
  already been obtained by a human.
- Third-party software and model weights; report those upstream.

## Use at your own risk

This software is MIT-licensed and, as the license says, provided "as is", without warranty or
liability. Please read that plainly: an agent that can rewrite its own system configuration
(`/etc/nixos`) and rebuild the machine can break that machine, lose data or weaken its own
protections, through its own mistakes or through a model that has been manipulated. You run it
at your own risk, on hardware you can afford to wipe, and you are responsible for what you
permit it to do.
