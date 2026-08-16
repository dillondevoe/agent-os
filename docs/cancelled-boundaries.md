# Cancelled boundaries — a ledger of one recurring bug class

> **Status: draft, pending Geist's correction.** The class is Geist's formulation; this file
> only collects the instances into one place. Anything here that misstates the taxonomy is
> mine to fix, not the taxonomy's fault. Ownership of the wording stays with Geist.

## The class

> **A boundary cancelled by something that reads like it belongs.**

Stated the other way round, which is the form that catches things:

> **The dangerous absence is the one that resembles a configuration.**

These are not bugs where something looks wrong. Every instance below looked *right* — it
looked like a considered choice made by someone who understood the system. That is the whole
difficulty. A boundary that is obviously missing gets noticed in review. A boundary cancelled
by a plausible neighbouring line does not, because the reviewer's eye stops at "someone
thought about this here."

The failures share a shape:

1. A protective mechanism exists and is correctly written.
2. Something adjacent — a stricter-sounding option, an unset variable, a narrower path list,
   a deprecated alias — quietly removes its effect.
3. Every existing gate stays green, because the gates assert the mechanism's *content*, not
   that anything still consumes it.

Step 3 is what makes this class expensive. The tests do not fail. They cannot: they were
never wired to notice.

## The canonical catch

> **A guard that permits everything is indistinguishable from a tool that never ran — check
> the control arm.**

If a check has only ever been observed to pass, you do not know it is a check. You know it is
a thing that passes. Before trusting any guard, make it fail on purpose: feed it the input it
is supposed to reject and confirm it rejects. A guard whose failing arm has never been
exercised is a comment with a CI badge.

## The ledger

| # | Instance | What cancelled the boundary | Where |
|---|----------|------------------------------|-------|
| 1 | `ProtectSystem=strict` vs. the cap sandbox | `strict` makes the filesystem read-only, not *unreadable*, and composes with the namespace in a way that undoes it. It reads *stricter* than what it replaced. | `modules/cap-sandbox.nix` (deliberately absent, with the reasoning kept inline) |
| 2 | `runtimePaths` unvalidated argument | The guard's assertions all described what the *default* `runtimePaths` produced, so deleting the guard outright kept them green. | `flake.nix` (negative control added) |
| 3 | The wrapper pins — an **absence** | `cap-invoke` treats an unset `AGENT_OS_CAP_SANDBOX` as "unconfined by config". Deleting two `export` lines silently ships an unconfined seam and errors nowhere. | `modules/cap-invoke-pkg.nix`, guarded in `flake.nix` |
| 4 | The Dell watcher's dead tailscale leg | A monitoring leg that could no longer fire, while continuing to read as coverage. | operational (jarvis-sync) |
| 5 | `nc -z` / bash `/dev/tcp` as a readiness probe | A TCP connect is satisfied by a process that is merely *starting*, so the probe answers a weaker question than the one it appears to ask. | `modules/brain.nix`, `tests/fetch-proxy-allowlist.nix` |
| 6 | Leg 7 v1 of the cap-sandbox battery | The leg passed against a confinement that had already been cancelled — it proved the probe ran, not that the wall held. Fixed with a run-marker and a control-arm-first ordering. | `tests/cap-sandbox-battery.sh` *(in flight — PR #101)* |
| 7 | `systemd.watchdog.*` rename-shim alias | A deprecated alias that still evaluates today and could silently stop applying later, while continuing to read as a correct setting. Avoided by writing the modern option name. | `configuration.nix`, `configuration-open.nix` *(in flight — PR #102)* |
| 8 | The `vm-tests` path filter | A coverage claim **narrower than the thing it guards**: the filter omitted the two files every VM in the lane boots, so a boot-affecting change ran only `flake-check`. | `.github/workflows/vm-tests.yml` *(in flight — PR #103)* |

Members 6–8 are recorded here as in-flight rather than merged. If those PRs change shape in
review, this table is wrong until someone fixes it — which is itself an instance of the class,
so it is worth being pedantic about.

### Member 8 is the clearest specimen we have

It is worth singling out because it caught itself in public. PR #103 (which widens the filter)
and PR #102 (which edits exactly the files the filter was missing) sat in the queue at the same
time:

```
#102  touches configuration*.nix  ->  flake-check ONLY          (57s)
#103  touches vm-tests.yml        ->  flake-check + 5 vm-tests
```

Same repository, same day, same green checkmark in the PR UI. The only difference was whether
the changed file happened to appear in a list. Nothing was broken, nothing was misconfigured,
and no one had made a mistake — the filter was a thoughtful, scoped list that simply did not
mention two files. That is the class exactly.

## Working against the class

When adding or reviewing anything protective:

- **Exercise the failing arm.** Feed the guard what it must reject; watch it reject. If you
  have never seen it red, you have not seen it work.
- **Ask what consumes it.** A test that validates a policy's *content* does not establish that
  any code path still reads that policy. Those are different claims.
- **Distrust adjacency.** The line that cancels a boundary usually sits next to it and sounds
  stronger. `strict` undoing a namespace is the archetype.
- **Check that the guard's scope matches the guarded thing.** Member 8 is a filter that was
  narrower than its subject. Coverage lists age badly, and they age silently.
- **Prefer absence that fails loudly.** Where an unset variable currently means "skip the
  protection", consider making it mean "deny". Note the asymmetry recorded in `flake.nix`: a
  missing `AGENT_OS_SYSTEMD_RUN` is a runtime deny, while a missing policy was a silent
  unconfined exec. Same absence, opposite consequence.
- **Verify on the running system, not in the diff.** Several members were only visible by
  asking the live machine. A diff cannot tell you that the interface a config protects has no
  cable in it.

## Why this file is in the repo

The class had accumulated roughly eight members across commit messages, inline comments, and
brain-comms, with exactly one reference anywhere in the tree (`flake.nix`). Scattered like
that it is not a checklist anyone can apply — it is a thing you have to have been present for.
The instances are product knowledge; the comms that carried them are not shipped, and should
not be. This file is the durable half.
