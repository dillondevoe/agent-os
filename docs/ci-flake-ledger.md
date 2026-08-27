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

Four observed failures, four **different** jobs, one **byte-identical** signature:

| run | job | date (UTC) |
|---|---|---|
| `32792191355` | `vm-test (test-selfimprove-loop-runs)` | 2026-08-25T00:05Z |
| `32784416040` | `vm-test (test-identity-boot)` | 2026-08-24T22:23Z |
| `32779702396` | `vm-test (test-seal-faildown)` | 2026-08-24T21:28Z |
| `32808436764` attempt 1 | `vm-test (test-egress-uid-scope)` | 2026-08-25T04:18Z |

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

**This is one harness defect, not four flaky tests.** The four job names are the four places the
harness happened to be standing when it failed; treating them as four separate test problems is
the wrong unit of work.

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
  inspection; DVo has no `nix` binary and cannot do it. Flagged as a resourcing question.
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
