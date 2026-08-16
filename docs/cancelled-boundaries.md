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
| 8 | The `vm-tests` path filter | A coverage claim **narrower than the thing it guards**: the filter omitted the two files every VM in the lane boots, so a boot-affecting change ran only `flake-check`. | `.github/workflows/vm-tests.yml` *(merged — PR #103)* |
| 9 | Draft status as a merge-gate mitigation | A draft PR reports `mergeStateStatus=CLEAN`, **not** `DRAFT`, and the auto-merge watcher never requested `isDraft` at all — so draft was not merely unchecked, it was *invisible*. The mitigation whose only job was to be an independent second barrier was a silent no-op. | `agentos_merge_gate_watcher.py` (operational; fixed by Page) |
| 10 | `GUARDED_LABELS` naming labels that did not exist | The label guard named six labels; **five of them did not exist in the repository**, so those five could never be *applied* and the guard could never be invoked through them — correct code keyed on values nobody could supply. Inert for three of the four original names since the watcher shipped. | same file + repo label set (all labels created; the watcher now verifies them at runtime) |
| 11 | A `seal-` prefix in a guarded-path list | The guarded-prefix list named `seal-`, which reads as "the seal surface is covered" — but the file is `mesh-wireguard-**sealed**.nix`, and the matcher is component-based, so it never matched. Unlike member 8, the entry was **present**: an auditor reading the list ticks the box. A prefix that covers a concept in one spelling of it. Same file: `secret` did not match `mail-secret-open.nix` for the same reason. | `agentos_merge_gate_watcher.py` (operational; found and fixed by Page) |
| 12 | `flake.lock` guarded, `flake.nix` open | The same list guarded the **pinned inputs** but not the **build definition**. The lock file is derived from the nix file; guarding it while leaving its source open protects the manifest and not the thing that writes it. The list read as "the build is covered" because a build-related path was in it. | `agentos_merge_gate_watcher.py` (operational; found and fixed by Page) |

Members 6–7 are recorded here as in-flight rather than merged. If those PRs change shape in
review, this table is wrong until someone fixes it — which is itself an instance of the class,
so it is worth being pedantic about.

### Members 9 and 10 are the class biting its own cataloguers

Both were found *while building the mitigation for a must-ask PR in this very repo* — which is
worth recording plainly, because it is the strongest available evidence that reading the class
does not confer immunity to it.

Member 9 is the purer specimen. Draft status is a real GitHub feature, it was deliberately
applied, it was visible in the UI, and everyone involved — the author of the mitigation and the
author of the watcher — independently believed it held. It enforced nothing. The reasoning that
produced it ("draft can only prevent a merge, never cause one") is true of draft in general and
false of draft *against this consumer*, which is exactly step 2 of the shape: the boundary was
cancelled not by an error but by an adjacent mechanism that did not happen to read it.

Member 10 is the follow-on, and it shows the class survives its own fix. The replacement guard
was written correctly and tested correctly at the function level — `must-ask` really was in
`GUARDED_LABELS` — but the label did not exist in the repository, so nothing could ever carry
it. A guard keyed on an unavailable input is a guard that never fires, and it looks identical in
source review to one that works.

**And member 10 turned out to be older and larger than the two labels that exposed it.** The fix
for it was not to create the two missing labels but to make the watcher *ask the supplier* on
every run whether each guarded label resolves. The first live run of that check reported three
more — `breaking`, `human-gate`, `wip` — none of which had ever existed. Of the four original
guarded labels, only `hold` was real. That guard had been roughly 75% inert **for its entire
life**, and nothing surfaced it, because an inert guard and a working guard emit identical output
on every PR that does not need them. The two we added were the newest instances, not the first;
we walked into a standing condition rather than creating one.

That is the most useful thing in this file about detection cost. Members 9 and 10 were both found
in under a day *because someone was actively testing a mitigation*. The three older labels sat
unnoticed from the day the watcher shipped, because nobody was.

The general lesson, stated to be reusable: **verifying that a guard contains the right rule is
not verifying that the guard can ever be reached.** Member 3 is the same asymmetry in a variable,
member 8 in a path list, member 10 in a label. Ask what supplies the input, not just what the
rule says about it.

Sharpened into a rule worth applying beyond this repo:

> **A guard that names an external identifier should verify, at runtime, that the identifier
> resolves.**

Labels here; elsewhere a webhook URL, a queue name, a systemd unit, a file path, a message
recipient. The 2026-08-12 recipient-namespace gap was this same shape — a sender addressing a name
that routed to nobody. The check belongs in the guard itself, because that is the only place that
knows the full list, and it should **warn rather than halt**: an unresolvable identifier makes the
mechanism unavailable, but it does not make any particular subject unsafe, and failing closed on a
transient lookup error would take down the very thing the guard exists to provide.

### Members 11 and 12: a coverage list can be wrong while looking complete

Member 8 was a list that was visibly short — the files simply were not in it, and anyone who
diffed the list against the tree would see the gap. Members 11 and 12 are worse, because the
entries are *present*. An auditor reading the list sees `seal-` and concludes the seal surface is
covered; sees a `flake.*` path and concludes the build is covered. Both conclusions are wrong, and
neither is visible by reading the list — only by matching it against the actual tree.

Two mechanisms, worth separating:

* **11 is a matching failure.** `seal-` never matches `mesh-wireguard-sealed.nix`; the matcher is
  component-based, so `secret` never matches `mail-secret-open.nix` either. The entry covers one
  spelling of the concept it appears to name. Note the fix chosen: name the files explicitly rather
  than loosen the matcher to substring, which would over-match far more than it repairs.
* **12 is a scoping failure.** Both `flake.lock` and `flake.nix` were available to guard and only
  the derived one was chosen. Guarding an artifact while leaving open the source it is generated
  from is a boundary that a single regeneration walks around.

The practice that finds both is the same, and it is not review: **diff the coverage list against
the tree it claims to cover, and confirm each entry matches something.** An entry matching nothing
is member 10 in a different costume — a rule keyed on a value nothing supplies.

The other half of this pair is the throughput check, which is easy to skip because it feels like
the opposite of safety. A guard widened until it refuses everything is exactly as useless as one
that guards nothing, and it fails in a way people route around rather than report. After widening,
Page confirmed both arms on live PRs: `flake.nix` and `cap-sandbox` changes now refuse, while
`docs/`, `README.md` and `tests/run-local.sh` still pass. **Widening a guard is a change that needs
its control arm re-run, not just its failing arm.**

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
- **Match the coverage list against the tree, do not read it.** An entry that matches nothing
  reads exactly like an entry that matches the file you had in mind. Members 11 and 12.
- **Re-run the control arm after widening a guard, not just the failing arm.** A guard that
  refuses everything is as useless as one that guards nothing, and it gets routed around instead
  of reported.
- **Prefer absence that fails loudly.** Where an unset variable currently means "skip the
  protection", consider making it mean "deny". Note the asymmetry recorded in `flake.nix`: a
  missing `AGENT_OS_SYSTEMD_RUN` is a runtime deny, while a missing policy was a silent
  unconfined exec. Same absence, opposite consequence.
- **Verify on the running system, not in the diff.** Several members were only visible by
  asking the live machine. A diff cannot tell you that the interface a config protects has no
  cable in it.
- **Ask what supplies the guard's input.** Members 3, 8, and 10 are all correct rules that could
  not be reached — an unset variable, an omitted path, a label that did not exist. Reviewing the
  rule tells you nothing about whether anything can ever hand it the value that trips it.
- **Make the guard check its own identifiers at runtime, and warn.** Where a guard names something
  external — a label, a unit, a path, a recipient — have it verify on each run that the name
  resolves, and say which ones do not. This is the only member of the class that a *running system*
  can report about itself, which is why it is worth wiring rather than remembering. Warn rather
  than halt: an unresolvable name disables the mechanism, it does not make the subject unsafe.
- **Never retire a barrier on the strength of its replacement being written.** Retire it once you
  have watched the replacement refuse the real thing. Member 9 was trusted for a full day on the
  strength of it being plausible.

## Why this file is in the repo

The class had accumulated roughly twelve members across commit messages, inline comments, and
brain-comms, with exactly one reference anywhere in the tree (`flake.nix`). Scattered like
that it is not a checklist anyone can apply — it is a thing you have to have been present for.
The instances are product knowledge; the comms that carried them are not shipped, and should
not be. This file is the durable half.
