# CI flake ledger — `vm-tests` harness

**Status: measured, root cause NOT established.** This file exists because a re-run that turns
green is exactly how this defect stays invisible. It records what was observed so the next person
does not start from zero.

Measured 2026-08-25 by Mirror. Window and method are stated below so the numbers can be
re-derived, disagreed with, or superseded.

## TWO SHAPES, NOT ONE — read this before filing a failure below

Everything under "The signature" describes **shape A**: the driver cannot reach `backdoor.service`,
retries 20 times, dies at ~7m07s with `RuntimeError: Shell did not start in time`.

**Shape B is different and was first recorded 2026-08-27** (run `33031952158`, job
`vm-test (test-selfimprove-loop-runs)`, PR #170). It is a substituter failure, not a guest
failure. No VM is ever booted.

```
warning: unable to download '.../*.nar.zst': HTTP error 200
         (curl error: Failed sending data to the peer); retrying from offset 78331904
error:   unable to download '.../*.nar.zst': HTTP error 416
error:   path '/nix/store/...-libqmi-1.38.0' is required, but there is no substituter that can build it
error:   some substitutes for the outputs of derivation '...-fwupd-2.1.6.drv' failed
         (usually happens due to networking issues); try '--fallback' to build derivation from source
```

The mechanism is visible in those two lines together: a large NAR download is interrupted, nix
retries it as a **ranged** request from a byte offset, and cache.nixos.org answers **416 Range Not
Satisfiable**. Nix then treats the path as unsubstitutable rather than falling back to building it,
and the whole `nixos-system` closure cascades to `Reason: 1 dependency failed`.

**How to tell them apart in one glance — use the DURATION.**

| | shape A | shape B |
|---|---|---|
| dies at | ~7m07s | ~2m |
| last useful line | a normal guest console line, then silence | `HTTP error 416` / `no substituter` |
| VM booted | yes | **no** |
| plausible remedy | unknown, root cause NOT established | `--fallback`, or a retry |

**Do not fold B into A's count.** A's rate figures above were derived from A's signature, and
adding B's instances to them would inflate a number whose whole point is that it is already a
floor. They are separate populations until someone shows otherwise.

**Why B is worth a section rather than a re-run.** B is *more* invisible than A, not less: it is
transient infra, a re-run clears it, and it never produces a test failure anyone would investigate.
The instance above was caught only because it landed on a PR whose diff was a flake check and one
comment — nothing in that change can touch a VM, so the failure could not be mine, and the only
remaining question was which shape it was. On a PR with a real diff it would have been read as
"my change broke the VM tests", re-run, gone green, and left no trace.

Unresolved and stated rather than guessed: whether `--fallback` belongs in the workflow's
`nix build` invocation. It would convert B from a hard failure into a long build, which is a
tradeoff with a real cost, and nobody has measured how often B occurs. **One instance is not a
rate.** Recorded so the second instance has something to be the second of.

## The signature

Six observed failures, five **different** jobs, one **byte-identical** signature:

| run | job | date (UTC) |
|---|---|---|
| `32792191355` | `vm-test (test-selfimprove-loop-runs)` | 2026-08-25T00:05Z |
| `32784416040` | `vm-test (test-identity-boot)` | 2026-08-24T22:23Z |
| `32779702396` | `vm-test (test-seal-faildown)` | 2026-08-24T21:28Z |
| `32808436764` attempt 1 | `vm-test (test-egress-uid-scope)` | 2026-08-25T04:18Z |
| `33037585674` attempt 1 | `vm-test (test-fetch-proxy-allowlist)` | 2026-08-27T03:58Z |
| `33037585674` attempt 1 | `vm-test (test-selfimprove-loop-runs)` | 2026-08-27T03:59Z |

Every one fails the same way: the driver cannot reach `backdoor.service` in the guest, retries
20 times, and gives up after ~7m07s with

```
RuntimeError: Shell did not start in time.
```

The guest does **not** report a problem. No unit failure, no OOM, no panic. The last console line
is a normal one —

```
[    9.221984] systemd[1]: etc-machine\x2did.mount: Deactivated successfully.
```

— and then the console is silent for the rest of the run.

**Instances five and six landed in the SAME run, and that is the first co-occurrence recorded
here.** Both on main at `18ca0b6`, both shape A, both the byte-identical
`RuntimeError: Shell did not start in time` out of `test_driver/machine/__init__.py:1075`. The
other seven jobs in that run — including `test-identity-boot`, which had itself been an instance
two days earlier — were green.

Be careful what this licenses. GitHub-hosted runners are per-job VMs, so two failures in one run
are **two independent draws, not one machine wedging twice**. This is not evidence of a run-level
or runner-level cause and must not be written up as one.

What it does say is about *dispersion*, not mechanism: the instantaneous rate can be 2 of 9 in a
single run, which the ~7% figure below does not prepare a reader for. A rate quoted as a scalar
invites the reading "roughly one job every fourteen runs", and that reading would have made this
run look anomalous when it is an ordinary draw from a distribution nobody has characterised. The
rate stays a lower bound; it is not recomputed here, because the window that produced it has not
been re-derived and splicing two new points onto an old window is the arithmetic this file already
warns about.

The diff on that sha touched `tests/vm-matrix-contract.py` only — a host-side contract check that
runs in `flake-check`, boots no VM, and is imported by no VM test — and the previous main sha
`45ebb4e` was green on all nine. The exclusion rests on that reachability argument, **not** on
"it's a known flake", which is the reasoning this file exists to refuse.

**Rerun on the same sha: BOTH jobs green (attempt 2, 2026-08-27T04:50Z).** That is the cheap
discriminator, and it was Geist's, not mine — `gh run rerun <id> --failed` re-runs only the failed
jobs on the identical commit. A pass confirms the flake; a second failure on the same two jobs
would have falsified the diff argument above and been a stop-and-summon, not a re-rerun. It also
restores main's latest `vm-tests` to green, which every future "the previous sha was green"
argument depends on — the exclusion reasoning used here is only available to the next person if
somebody keeps that column true.

**And then this file's own undercount mechanism ran on its own newest rows, in front of the
author.** The section below asserts that `gh run list` reports only the latest attempt, so a
failure re-run to green "leaves the population entirely" — an assertion previously resting on one
reconstructed specimen (`32808436764`). This time it was watched prospectively: `33037585674` was
read as `completed/failure` at 03:58Z with two named failed jobs, and after the rerun the same
query returns `conclusion: success` with nine green jobs and no trace of attempt 1. **The two
instances above are now unreachable from any run listing; they survive only because they were
written down before the rerun.**

Hence the `attempt 1` qualifier on their rows, matching `32808436764`. The ordering is the
transferable part: **record the instance BEFORE you re-run it, because the re-run is what destroys
the evidence** — and the re-run is also the correct thing to do, so there is no version of this
where waiting helps.

**This is one harness defect, not five flaky tests.** The job names are just the places the
harness happened to be standing when it failed; treating them as separate test problems is the
wrong unit of work. `test-selfimprove-loop-runs` appearing twice, two days apart, is the same
non-fact as any of the others — it is where the harness was standing, not what was wrong.

## Rate — and why the rate is a lower bound, not a measurement

Over the last 60 `vm-tests.yml` runs (2026-08-23T23:13Z → 2026-08-25T04:18Z): **53 success,
4 failure, 3 cancelled** — 4 of 57 concluded, ~7.0%.

An earlier count on a 40-run window gave 4 of 38, ~10.5%. Both are the same underlying data seen
through a sliding window. **Neither number is precise and neither should be quoted as one.**

More importantly, both **undercount by construction**:

- `gh run list` reports only the **latest attempt** of a run. Re-running failed jobs rewrites the
  run's conclusion in place, so a failure that was re-run to green **leaves the population
  entirely**. Confirmed directly: run `32808436764` lists as `conclusion: success`, and it is
  `attempt: 2`; its attempt-1 `test-egress-uid-scope` failure appears nowhere in the listing.
- Five runs in this window are `attempt > 1` (`32808436764`, `32770548874` at attempt 3,
  `32751833582`, `32708993068`, `32678457976`). At least one of those hidden prior attempts is a
  confirmed instance of this defect. The others are **not** claimed as such — a re-attempt can
  follow a cancel or a real fix, and that has not been checked one by one.

So: the visible rate is a floor. The same shape as the wake-fire floor — an instrument that can
only ever see part of what it is counting should say "lower bound" out loud.

## What is NOT established

- **Root cause.** Two shapes fit every observation equally well: (a) the console/backdoor channel
  is lost while the VM keeps running, or (b) the VM wedges outright. Nothing measured here
  separates them. Separating them needs a nix-capable box that can hold a wedged guest open for
  inspection.

  **RETRACTED 2026-08-27 — this line used to read "DVo has no `nix` binary and cannot do it.
  Flagged as a resourcing question." That is false, and the author of this file is the one who
  wrote it.** Measured on DVo the same day: `nix (Determinate Nix 3.21.9) 2.34.8` at
  `/nix/var/nix/profiles/default/bin/nix`, `/dev/kvm` present, 6 cores, 7 GB. What was true is
  that `nix` is not on the *default* `PATH` here, and that fact got generalised into a property of
  the machine. The repo's own capability probe already draws exactly this distinction in the
  opposite direction — an rc=127 there means "I could not run the instrument", never "the
  capability is down" — and this line is that rule broken by the person who relies on it.

  The cost is the part worth recording: a root-cause investigation sat parked as a *resourcing
  question* for two days, and the whole blocker was one `export PATH`. **A claim that a capability
  is absent is a claim about the machine, and it needs the same control arm as a claim that one is
  present** — run the thing on the box before writing down that the box cannot.

  **And the retraction is not merely "the binary exists" — the whole harness was run here to
  check.** `nix build .#test-selfimprove-loop-runs` on DVo, 2026-08-27, at drv
  `b1vli8yv…-vm-test-run-agentos-selfimprove-loop-runs` — the *same* derivation that failed as
  instance six in CI. The guest booted, the driver reached `backdoor.service`, the assertions ran,
  and the script finished in **100.12s**, green. That is the claim's control arm, run after the
  fact: a statement that this box cannot host the investigation had to survive actually hosting it,
  and it did not.

  So the root cause is still NOT established, but it is no longer blocked on hardware. What it is
  actually blocked on is narrower and should be stated as such: reproducing shape A *locally*,
  which a ~7% flake does not oblige to happen on demand. **The local green above is worth exactly
  nothing as evidence about the flake** — one pass against a ~7% failure rate is the expected
  outcome and would look identical if the defect were universal-but-rare or absent-here-entirely.
  It is evidence about the *machine* and nothing else. Only a local FAILURE, held open for
  inspection, separates (a) from (b).
- **Whether this is new.** No data exists before 2026-08-24T07:41Z. It may be long-standing.
- **Whether the rate is stable.** Two windows, two different numbers, one day of data.
- **Whether all five re-attempts in the window were this defect.** One is confirmed; four are not
  checked.

## Why it matters even at 7%

An ambiguous gate gets re-run until it is green, regardless of whether the red was noise or a
regression. That is the wired-batteries class from the other direction: there, a check that could
not fail; here, a check whose failure carries no information, so its failure gets discarded. A
real regression landing during a flaky period is indistinguishable from the flake, and the
standard response — re-run — is the one that hides it.

Recording it beats losing it to another green re-run.
