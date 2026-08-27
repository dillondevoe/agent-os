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

Nineteen observed failures, six **different** jobs, one **byte-identical** signature:

| run | job | date (UTC) |
|---|---|---|
| `32792191355` | `vm-test (test-selfimprove-loop-runs)` | 2026-08-25T00:05Z |
| `32784416040` | `vm-test (test-identity-boot)` | 2026-08-24T22:23Z |
| `32779702396` | `vm-test (test-seal-faildown)` | 2026-08-24T21:28Z |
| `32808436764` attempt 1 | `vm-test (test-egress-uid-scope)` | 2026-08-25T04:18Z |
| `33037585674` attempt 1 | `vm-test (test-fetch-proxy-allowlist)` | 2026-08-27T03:58Z |
| `33037585674` attempt 1 | `vm-test (test-selfimprove-loop-runs)` | 2026-08-27T03:59Z |
| `32654129939` | `vm-test (test-egress-uid-scope)` | 2026-08-23T17:13Z |
| `32622321722` | `vm-test (test-egress-mesh-uid-scope)` | 2026-08-23T06:12Z |
| `32574669452` | `vm-test (test-egress-uid-scope)` | 2026-08-22T13:03Z |
| `32442249749` | `vm-test (test-egress-mesh-uid-scope)` | 2026-08-21T03:07Z |
| `32770548874` attempt 1 | `vm-test (test-fetch-proxy-allowlist)` | 2026-08-24T19:51Z |
| `32751833582` attempt 1 | `vm-test (test-identity-boot)` | 2026-08-24T16:36Z |
| `32708993068` attempt 1 | `vm-test (test-identity-boot)` | 2026-08-24T08:58Z |
| `32678457976` attempt 1 | `vm-test (test-identity-boot)` | 2026-08-24T01:01Z |
| `32655978802` attempt 1 | `vm-test (test-egress-uid-scope)` | 2026-08-23T17:47Z |
| `32652029344` attempt 1 | `vm-test (test-identity-boot)` | 2026-08-23T16:33Z |
| `32615318593` attempt 1 | `vm-test (test-egress-mesh-uid-scope)` | 2026-08-23T03:25Z |
| `32592787891` attempt 1 | `vm-test (test-egress-uid-scope)` | 2026-08-22T19:07Z |
| `32534142304` attempt 1 | `vm-test (test-egress-uid-scope)` | 2026-08-21T22:43Z |

Rows 7–19 were added by the 2026-08-27 census below, not by anyone watching CI (rows 15–19 by
its second, deeper pass — see the superseded block). Four of them
(`32654129939`, `32622321722`, `32574669452`, `32442249749`) were plainly visible failures this
file simply never recorded; the four `attempt 1` rows were recovered from the attempts API. Every
one is `Shell did not start in time`, checked in its own attempt log.

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

Hence the `attempt 1` qualifier on their rows, matching `32808436764`.

**CORRECTED 2026-08-27, same day, by Geist — and the correction is the second instance of a class
this file caught itself in an hour earlier.** The paragraph above used to end: *"record the
instance BEFORE you re-run it, because the re-run is what destroys the evidence."* **A rerun does
not destroy anything. It hides.** `gh run list` shows only the latest attempt; the prior attempt
stays fully addressable at `/actions/runs/<id>/attempts/<n>`, with `/jobs` and `/logs`, for as
long as GitHub retains logs (90 days by default). Verified here, not taken on report: attempt 1 of
`33037585674` returns `conclusion: failure` with exactly the two job names in the rows above, and
attempt 1 of `32808436764` — the specimen this file thought it had lost — returns its
`test-egress-uid-scope` failure and the `Shell did not start in time` lines intact.

The ordering advice survives, demoted to what it actually is: recording before you rerun is
*cheaper* than reconstructing afterwards, not the only way the instance continues to exist.

**The class, named because it is now twice in one day.** "DVo has no `nix`" was a `PATH`-scoped
observation generalised to the machine. "The rerun erased it" was a `gh run list`-scoped
observation generalised to GitHub. Both were **true of the instrument and false of the world**,
and both were promoted to ledger law off a single instrument without a second one being tried.
The rule that falls out: **before writing "X is gone" or "X cannot", reach for X one more way.**
The prospective observation itself still stands and is still worth the space — it is the exhibit
proving `gh run list` is the wrong census tool, which is exactly what the next section now does
something about.

**This is one harness defect, not six flaky tests.** The job names are just the places the
harness happened to be standing when it failed; treating them as separate test problems is the
wrong unit of work. `test-selfimprove-loop-runs` appearing twice, two days apart, is the same
non-fact as any of the others — it is where the harness was standing, not what was wrong.

## Rate — the census, run 2026-08-27

**The earlier numbers in this section were floors quoted off `gh run list`. They have been
replaced by an enumeration, because the attempts API makes one possible.** What the floors said,
kept for the record: 4 of 57 concluded runs (~7.0%) over a 60-run window, and 4 of 38 (~10.5%)
over an earlier 40-run one — the same data through a sliding window, neither precise.

**Census method (reproducible; this is the ledger's counting instrument from here on):**

1. `gh run list --limit N --json databaseId,name,attempt,conclusion,createdAt` — every run, all
   branches. **Not `--branch main`**: the same query scoped to `main` returned *one* rerun where
   the unscoped one returned seven, and most of this harness's failures happen on PR branches.
2. Every row with `attempt > 1` has hidden prior attempts. For each `n < attempt`, fetch
   `/actions/runs/<id>/attempts/<n>` for the conclusion and `.../jobs` for the failed job names.
3. Classify each failure **from its log**, never from its duration:
   `/actions/runs/<id>/attempts/<n>/logs` → `Shell did not start in time` is shape A,
   `HTTP error 416` / `no substituter` is shape B, **neither is neither** — see below.

4. **Since the one-shot retry shipped (2026-08-27), steps 1–3 no longer see every instance —
   a retried instance ends in a green job.** The retry emits a marker for exactly this reason.
   Grep the job logs of *successful* attempts too:

   ```bash
   # per run id, over the same window as step 1
   gh api "repos/dillondevoe/agent-os/actions/runs/$ID/attempts/$N/logs" > /tmp/l.zip
   unzip -p /tmp/l.zip '*' | grep -oE 'FLAKE-A-RETRY test=[a-z0-9-]+ run=[0-9]+' | sort | uniq -c
   ```

   The `run=[0-9]+` tail is load-bearing, not decoration (Geist, gate, 2026-08-27 — verified on
   run `33037585674`): the runner echoes every `run:` script body into the job log with
   `${{ matrix.test }}` already substituted, so EVERY job — healthy or not — carries
   `FLAKE-A-RETRY test=<job> run=${GITHUB_RUN_ID}` twice in the source echo. A grep without the
   numeric `run=` counts those and reads ~200% at baseline: a saturated instrument, the mirror
   image of the silently-dead one below. Only a marker that actually fired has digits there.

   **Each marker is one shape-A instance and MUST be counted in the table below**, even though
   the attempt it belongs to concluded `success`. A mitigation that swallows a failure without
   leaving a countable trace is not a mitigation, it is a delete — Geist's condition, and the
   thing this step exists to prevent. If a window returns zero markers *and* zero shape-A
   failures, verify the marker is still emitted before concluding the flake is gone: an
   instrument that has quietly stopped reporting looks identical to a fixed harness.

**Result over 2026-08-20T18:37Z → 2026-08-27T03:50Z** (138 `vm-tests (slow lane)` runs; window
bounded by listing depth, not by choice):

| | count |
|---|---|
| runs listed | 138 (121 success, 10 failure, 7 cancelled) |
| runs with `attempt > 1` | 7, carrying 8 hidden prior attempts |
| **shape A instances, visible** | **7** |
| **shape A instances, recovered from hidden attempts** | **7** |
| shape B instances | 1 (`33031952158` attempt 1) |
| failures that are **neither** shape | 3 |

**Fourteen shape-A instances, where this file had six** — itself later corrected upward to
nineteen; see the superseding block below, and note that this sentence is left standing rather
than quietly rewritten because the *sequence* of counts is the exhibit. The seven recovered from hidden attempts
are `33037585674` attempt 1 (two jobs), `32808436764`, `32770548874`, `32751833582`,
`32708993068`, `32678457976` — each confirmed by `Shell did not start in time` in its own attempt
log, 6 or 8 occurrences apiece. The seven visible ones include four this ledger never recorded at
all: `32654129939`, `32622321722`, `32574669452`, `32442249749`.

**And that last sentence is the finding, not the reruns.** This file's stated theory of its own
undercount was *reruns hide instances*. Half the missing instances were sitting in plain
`gh run list` output the whole time, `conclusion: failure`, unrecorded — because nobody
enumerated. **The mechanism I could explain was not the mechanism doing most of the damage.** A
tidy causal story for a gap is not a measurement of the gap.

**SUPERSEDED 2026-08-27, ~40 minutes after it was published — by the same person, off the same
method, run deeper.** The paragraph here reported *"7 re-attempted runs … 146 attempts … 14
instances … ~8.9%"*. Those numbers are low. The recipe above said `--limit N` without saying how
large N has to be, and the first census ran it at `--limit 200` **across all workflows**, which
does not reach far enough back into this one workflow's history. Re-run at `--limit 300` filtered
to `vm-tests (slow lane)`, the same window contains **12** re-attempted runs, not 7. The five it
missed — `32655978802`, `32652029344`, `32615318593`, `32592787891`, `32534142304` — each have an
attempt-1 failure carrying six `Shell did not start in time` lines.

**That is the third time in one day, and this time the instrument was one I had just built.**
"DVo has no `nix`" was `PATH`-scoped. "The rerun erased it" was `gh run list`-scoped. This one was
`--limit`-scoped: a census whose depth was a parameter nobody pinned, publishing a count as though
depth were not a variable. **A method is not more trustworthy than a single observation just
because it is a method — an under-specified parameter in a recipe is an unstated assumption with
better handwriting.** The recipe above now pins both the limit and the workflow filter, and the
window below is pinned by run-id range so the numbers are reproducible rather than sliding.

**The census, corrected — window pinned to run ids `32404178598` … `33037585674`
(2026-08-20T18:37Z → 2026-08-27T03:50Z):**

| | count |
|---|---|
| `vm-tests (slow lane)` runs | 137 |
| attempts (sum of each run's `attempt`) | **150** |
| job executions (sum of `.jobs\|length` over all 150 attempts) | **1309** |
| runs with `attempt > 1` | 12 |
| **shape-A instances** | **19** (7 visible, 12 recovered from hidden attempts) |
| attempts carrying ≥1 shape-A instance | 18 |

- **Per attempt: 18 / 150 = 12.0%.**
- **Per job execution: 19 / 1309 = 1.45%.**

(19 instances live in 18 attempts because `33037585674` attempt 1 failed two jobs at once.)

**The per-job denominator was worth refusing to guess.** Job counts per attempt are **7, 8 and
9** — three values, not one (6 attempts at 7, 29 at 8, 115 at 9), because the matrix changed twice
in the window. A `runs × 9` denominator would have given 1350 against a measured 1309. The error
is only 3%, which is the useful part: **the assumption would have been wrong and would have looked
right**, and nothing downstream would ever have flagged it. All 150 job-count fetches returned a
number; zero errors, so the sum is not hiding a silently-skipped attempt. The per-*job* rate is
**not** computed here, and the reason is a live trap: runs in this window do not all have the same
number of jobs — `32426451466` has 8 where `33037585674` has 9, because the matrix changed. Any
`runs × 9` denominator is an assumption wearing a number. Summing `.jobs|length` over every
attempt is the missing step and is left explicitly undone rather than guessed.

**The three neither-shape failures are the census's control arm.** `32685877607`, `32630978544`
and `32426451466` are `vm-tests` failures with zero `Shell did not start in time` lines and zero
substituter lines; their logs carry `RequestedAssertionFailed` on real assertions (a `ping` that
did not answer, a model pull that exited 2) after the guest booted fine. They are **not** this
defect and are not counted as it. That matters more than the instances: a classifier that
returned "flake" for everything would have produced a bigger, more impressive, entirely worthless
number — **the count is only worth something because the method demonstrably declines to make
one.**

**Known limit of the classifier, stated rather than discovered later:** the log grep runs over the
whole attempt's log archive, so it attributes a signature to the *attempt*, not to a specific job.
For the multi-job attempts here that is not load-bearing — a passing job does not emit the shape-A
line — but a future attempt with one shape-A failure and one real failure would be mis-read by
this method, and the fix is a per-job log fetch.

## What the 19 specimens say about the standing hypothesis (2026-08-27)

The three two-VM tests carry a comment, shipped `6c7ff06` on 2026-08-23T09:25Z, that diagnoses
this failure as **intra-job** contention — two QEMU guests booting at once on one 4-vCPU runner —
and staggers their boots as the mitigation. Its argument rested on a distribution: *"all FOUR
landed in the three two-VM tests and ZERO in the six single-VM tests… under a uniform model that
is (3/9)^4 ~ 1%."* It also named its own falsification condition: *"if it returns in a two-VM
test, the next move is the targeted one-shot retry."*

The census gives that comment 19 data points where it had 4. Three findings, in descending order
of how much they survive scrutiny.

**1. The "ZERO in single-VM tests" premise is FALSIFIED. Eight of the nineteen are single-VM.**
By job: `egress-uid-scope` 6, `identity-boot` 5, `egress-mesh-uid-scope` 3,
`fetch-proxy-allowlist` 2, `selfimprove-loop-runs` 2, `seal-faildown` 1 — that is 11 two-VM and
**8 single-VM**. Whatever this defect is, it is not confined to tests that boot two guests, and
the premise the mitigation was reasoned from does not hold.

**2. The two-VM enrichment is real but far weaker than claimed.** 11 of 19 against 6.3 expected
under a uniform 3-of-9 model: **p ≈ 0.024**, not ~1%. Worth keeping as a signal; not worth
treating as a mechanism established.

**3. The mitigation's own falsification condition is met — five times — and yet it looks like it
helped.** Two-VM instances split 6 before the stagger and 5 after, against 40 and 110 attempts:
**15.0% → 4.5% of attempts.** So both of these are true at once and neither cancels the other:
staggering did not stop the failure, and the two-VM rate after it is about a third of what it was.
The pre-authorised next move (a targeted one-shot retry on this exact `RuntimeError`, never a
blanket retry) has had its trigger fire.

**4. And the finding I thought I had, which did not survive its own control check — recorded
because the check is the point.** All eight single-VM instances land *after* the stagger date and
none before, which reads as a regression appearing around 2026-08-23. It is mostly an artifact of
**the matrix changing under the window**: `identity-boot` accounts for 5 of the 8 and *was added*
`9c74fd4` on 2026-08-23T04:40Z — it existed for the last 4.7 hours of a 2.7-day "before" window.
Excluding it leaves 3 instances, expected 1.1 in the before-window at the after-rate, P(0) = 0.33.
Nothing. **A rate compared across a window in which the population itself changed is not a
comparison**, and this file already had the same fact wearing another hat: the 7/8/9 job counts
above *are* the matrix changing twice mid-window. The lesson is cheap and the alternative was
publishing a regression that does not exist.

## The prediction the retry is falsifiable against (written BEFORE it shipped, 2026-08-27)

Recorded here ahead of the mitigation landing, so that it cannot be fitted to the outcome
afterwards. Geist's §3 item 5; the arithmetic is this file's own census.

**Baseline, measured:** 19 shape-A instances across 1309 job executions = **1.45% per job
execution**. A 9-job attempt therefore fails from shape A with probability
`1 - (1 - 0.0145)^9` ≈ **12.3%** — which is the independently-derived 12.0% per-attempt rate in
the census table. The two numbers were computed from different columns and agree; that is the
only reason to trust either.

**Predictions, in the order they falsify:**

1. **Attempt-level red from shape A falls to ≈0.2%.** One shot converts a single-instance attempt
   to green; only an attempt hit *twice on the same job* stays red, at ≈0.0145² per job ≈ 0.02%,
   ~0.2% over nine. If red-from-shape-A stays anywhere near 12%, the retry is not firing —
   check the predicate before believing the rate.
2. **Marker count tracks ≈1.45% of job executions.** This is the load-bearing one: the markers
   are now the census, so the instrument's own calibration is the claim. Materially *below* 1.45%
   means instances are escaping the signature and being recorded as genuine reds. Materially
   *above* means the predicate is catching something `Shell did not start in time` does not
   isolate — **stop and summon, do not widen the retry.**
3. **Roughly 11 of every 19 markers carry a two-VM job name.** The enrichment measured at
   p ≈ 0.024 above. If the marker stream comes back near-uniform across the nine jobs, the
   enrichment was a small-sample artefact and the stagger's rationale weakens further.

**What none of these tests.** The retry is keyed to the *signature*, not to a mechanism, and the
root cause stays OPEN. A green lane after this ships is not evidence the harness was fixed; it is
evidence a known failure is being absorbed and counted. The day the marker count goes to zero on
its own is the day there is something to explain.

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
