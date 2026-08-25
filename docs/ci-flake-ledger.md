# CI flake ledger — `vm-tests` harness

**Status: measured, root cause NOT established.** This file exists because a re-run that turns
green is exactly how this defect stays invisible. It records what was observed so the next person
does not start from zero.

Measured 2026-08-25 by Mirror. Window and method are stated below so the numbers can be
re-derived, disagreed with, or superseded.

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
