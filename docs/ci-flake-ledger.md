# CI flake ledger

Intermittent CI failures, recorded at the moment they happen.

**Why this file exists.** A flaky job that is re-run green leaves no trace. The failure scrolls
out of the Actions list, the PR goes green, and the next person to see the same red concludes
"probably just flaky" from memory rather than from evidence — or, worse, concludes it is a real
regression and goes looking in the diff. Both readings are guesses, and both are avoidable for
the cost of writing the observation down BEFORE the re-run lands.

**The rule: a re-run does not erase an entry.** Record the failure first, then re-run. An entry
is only removed when the underlying cause is fixed, and then the fix is named here.

**Second rule, learned by breaking it on the first entry below: TRIGGERING A RE-RUN IS NOT
OBSERVING ONE.** Push to the branch and GitHub cancels the in-flight run — the re-run you were
counting on dies without a verdict, and the next green you see is on a DIFFERENT tree. Before
writing that a job was re-run, check `gh run list --json headSha,conclusion` and record the SHA
the conclusion belongs to. A green on a later commit is worth recording, but it is a weaker
observation and must be labelled as one, because the reader a month from now cannot tell the two
apart from the word "re-run."

**What this file is not.** It is not a place to file real failures for later. A red that is
reproducible, or that a diff can explain, is a bug and belongs in a fix or an issue — not here.
The entry criterion is: *the failing job cannot be explained by the change under test.*

---

## "Shell did not start in time" — first seen in test-identity-boot, NOT specific to it

| | |
|---|---|
| **First observed** | 2026-08-24T01:11Z |
| **Last observed** | 2026-08-24T16:36:45Z |
| **Occurrences** | 3 — see the third-occurrence section below; NOT identity-boot-only |
| **Commits** | `3c99650` (PR #161); `6fbd328` (PR #162) |
| **Job** | `vm-test (test-identity-boot)` |
| **Status** | open — non-determinism now DEMONSTRATED (same-SHA green re-run); cause still unidentified |

```
RuntimeError: Shell did not start in time
  self.connect()
  return self.execute(f"systemctl {q}")
```

**Why it is recorded as a flake rather than investigated as a regression.** The commit under
test changed exactly one file, `tests/vm-matrix-contract.py` — a stdlib contract script that the
identity-boot VM test neither imports nor executes. The eight other VM legs on the same commit
passed. The immediately preceding seven runs on this branch all passed, `test-identity-boot`
included. The failure is in the nixos test harness's initial shell connect, before any assertion
in the test body runs.

**What would change this verdict.** A second occurrence, especially on a commit that touches
`modules/identity.py`, `tests/identity-boot-battery.py`, or the identity module wiring in
`flake.nix`. Two occurrences make "runner load" a weaker explanation than "our VM is slow to
reach a usable shell," and the next step would be to look at the unit ordering the test waits on
rather than at the harness.

**What was NOT done, said plainly, and it is weaker than what this entry first claimed.** The
cause was not identified. I wrote here that the job "was re-run" — it was re-triggered, and it
never reached a verdict: `gh run list` shows `3c99650 cancelled`, superseded by the next push to
the branch. So there is NO second observation of `test-identity-boot` on the failing SHA. What
exists instead is a green full run on `20ade69`, the next commit. That is weaker evidence, and
the difference matters: a completed green re-run on the SAME tree isolates non-determinism, while
a green on a LATER tree cannot distinguish "flaky" from "the intervening commit changed it" —
even when, as here, the intervening commit only adds a markdown file and could not plausibly have
changed it. Plausibility is the argument I would be resting on, and this ledger exists because
plausible reconstruction from memory is what it replaces.

A green run is not evidence the first red was spurious in either case — it is at most evidence
the failure is not deterministic, which is what "flake" means. This entry stays open at one
occurrence, now with the added note that the confirming observation was never actually taken.

### Second occurrence, 2026-08-24 — and it supplies the observation the first one lacked

`6fbd328`, run `32751833582`, job `vm-test (test-identity-boot)`. Same terminal error,
`RuntimeError: Shell did not start in time`. This time the failure is located precisely: it is
**subtest 5, "a REAL reboot does not rotate the keys — the leg a battery cannot show"** — subtests
1-4 passed and finished cleanly first. So the shell that did not start is the one after
`machine.shutdown(); machine.start()`, i.e. the VM's SECOND cold boot, not its first. That is new
information the first entry did not have, and it narrows the suspect from "our VM is slow to reach
a usable shell" to "our VM is slow to reach a usable shell *the second time*."

**The same-SHA control was taken this time.** `gh run rerun 32751833582 --failed` completed:
`attempt=2 completed success 6fbd328`. Same tree, same test, green. That is the observation the
first entry explicitly records as never taken, and it is the strong form: it isolates
non-determinism without resting on any argument about what an intervening commit could plausibly
have changed. **`flake` is now demonstrated rather than inferred.** The cause is still not
identified, and this entry stays open.

**The change under test still cannot explain it**, by the same criterion as before, checked rather
than assumed: `6fbd328` touches `modules/pkgs/agos-sys.nix` and `tests/agos-sys-battery.py`, and
`grep -c 'agos-sys' tests/identity-boot-battery.py` -> 0. The eleven vm-tests runs immediately
preceding this one were all green.

**What the second occurrence changes about the next step.** The first entry said two occurrences
would make "runner load" weaker than "our VM is slow to reach a usable shell." It has happened, and
the subtest-5 localisation points somewhere more specific than either: the reboot leg calls
`machine.start()` and then waits, with no timeout of its own, on the test driver's default backdoor
connect. A third occurrence should not be re-triaged from scratch — it should go straight to that
wait.

**What was deliberately NOT done.** No retry loop was wrapped around `machine.start()`. A retry
there would make this ledger entry stop accruing occurrences while changing nothing about the
underlying slowness, and — worse — it would silently absorb a REAL failure of identity-boot to come
back from a reboot, which is the exact thing subtest 5 exists to detect. Masking the only detector
of a defect in order to quiet its false positives costs more than the false positives do.

### Third occurrence, 2026-08-24T19:58Z — and it FALSIFIES this entry's title and its next step

`03b210a`, run `32770548874`, job **`vm-test (test-fetch-proxy-allowlist)`**. Same terminal
error, `RuntimeError: Shell did not start in time`.

**It is not test-identity-boot.** It is a different VM test, on a different machine (`sealed`),
reaching the error through a different call — `wait_for_unit` -> `systemctl` -> `execute` ->
`connect`, on the machine's **FIRST** boot, not through `machine.start()` on a second one. The
second occurrence's localisation ("slow to reach a usable shell *the second time*") does not
survive this, and neither does the prescribed next step. **The previous entry said a third
occurrence "should not be re-triaged from scratch — it should go straight to that wait." Going
straight to that wait would have been going straight to code this occurrence never executed.**
Recording that plainly: the narrowing was real evidence at the time and it was still wrong, because
two occurrences of a symptom in one test is also what a fleet-wide symptom looks like early.

**The new information, and it is about the failure's SHAPE rather than its location.** The guest's
serial log stops dead at guest-time **9.05s**, mid-boot, on `Starting Virtual Console Setup...`
(wall clock 19:53:37Z). The next line in the log is the driver's traceback at **19:58:27Z** —
**4 minutes 50 seconds of total silence from a guest that was emitting several lines per second.**

That distinguishes two hypotheses this ledger has so far treated as one. "Slow to reach a usable
shell" predicts boot messages continuing, just late. What actually happened is that the guest
**stopped emitting entirely** — a wedge, not a slowness. **No timeout increase fixes a wedge**, and
every remedy considered across the first two occurrences (raise the limit, add a wait, retry the
start) assumed slowness. The retry loop deliberately NOT written at occurrence 2 is, on this
evidence, even less likely to have helped than the entry gave it credit for.

`Starting Virtual Console Setup...` appears three times in the final seconds (19:53:37.127,
.218, .302). Recorded as an observation, not a diagnosis — systemd restarting a unit is one
reading among several and this entry does not have enough to pick.

**The change under test still cannot explain it, checked rather than assumed — but the check is
weaker this time and that is worth saying.** `03b210a` touches `modules/pkgs/agos-sys.nix`, and
`agos-sys` IS in `systemPackages` via `settings-open.nix`, so it is in the image every one of
these VMs boots. That is a real path, unlike the previous two occurrences' subjects. Against it:
`agos-sys` is a `writeShellApplication` that nothing runs at boot, the wedge is in
`systemd-vconsole-setup` which has no relation to it, the other VM legs on the same commit passed,
and **occurrence 1 (`3c99650`) touched only `tests/vm-matrix-contract.py`** — a file no VM image
contains — which refutes any strict dependency on agos-sys. Noted rather than dismissed: **2 of 3
occurrences are on commits touching `agos-sys.nix`**, occurrence 1 is the disconfirming case, and
a fourth occurrence on a commit that touches neither would settle it.

**Same-SHA control, attempt 1: TRIGGERED, THEN CANCELLED BY MY OWN NEXT PUSH — the identical
mistake this file's first entry had to retract, repeated one tick later, in the file that
documents it.** `gh run rerun 32770548874 --failed` was started, and then the commit carrying
this very section was pushed to the same branch, superseding it: `03b210a cancelled`. The first
entry above says, verbatim, *"it was re-triggered, and it never reached a verdict ... superseded
by the next push to the branch."* I read that sentence while writing this section and still did it.

**Why the prose did not prevent the repeat, which is the part worth generalising.** The lesson was
recorded as a thing to remember, and the actual cause is structural: on this repo a branch rerun
and a push to that branch are *in conflict by construction*, and nothing in the sequence
"trigger control -> finish work -> push" knows that. A rule phrased as vigilance loses to an
ordering defect every time. **The control must be started and RESOLVED before the tick pushes
anything, or started from a commit the tick will not move past.**

**What is NOT evidence here.** `93558da` — the markdown-only commit that cancelled the control —
ran vm-tests green. That is a green on a LATER tree, which this file already ruled the weak form:
it cannot separate "flaky" from "the intervening commit changed it," and the fact that a
docs-only commit could not plausibly have changed it is exactly the plausibility argument this
ledger exists to replace. Recorded as not-a-control.

**Same-SHA control, attempt 2: TAKEN, and it is GREEN.** Re-triggered on `03b210a` with the tick's
push deliberately withheld until it reached a verdict — the ordering fix above, applied to itself.
`attempt=2 completed success 03b210a`, and verified by the job list rather than the run's
conclusion: all nine legs succeeded, **`vm-test (test-fetch-proxy-allowlist)` included**. Same
tree, same test, green. Occurrence 3 is therefore non-determinism DEMONSTRATED, not inferred —
the strong form, on the third occurrence and for the second time in this file.

Note in passing: this rerun also put `vm-test (test-identity-boot)` green on the same tree.

**What the control does NOT settle.** It shows the wedge is not deterministic. It says nothing
about what wedged. The next step below is unchanged by it.

| | |
|---|---|
| **Occurrences** | 3 |
| **Jobs** | `vm-test (test-identity-boot)` ×2; `vm-test (test-fetch-proxy-allowlist)` ×1 |
| **Status** | open — NOT identity-boot-specific; symptom is a mid-boot wedge, not slowness |

**Next step, replacing the one this occurrence falsified.** Stop looking at which test waits and
start looking at what the guest was doing when it went quiet. Concretely: capture the guest's
last serial line on every future occurrence (this entry now has one data point: `9.05s, Virtual
Console Setup`), and compare it against the two identity-boot occurrences' logs, which were never
read for this. If the wedge point is the same across tests, it is a boot-path defect in our image
and belongs to the image, not to any test. **Do not raise a timeout in the meantime** — on this
evidence it would convert a 5-minute red into a longer red, or worse, into a green that means
nothing.

### The wedge point is the SAME in both tests — first application of the new next step

The previous section's next step was: *"capture the guest's last serial line on every occurrence
and compare it against the two identity-boot occurrences' logs, which were never read for this."*
Done, and it produced the first cross-test evidence this entry has ever had.

**Occurrence 3** — `test-fetch-proxy-allowlist`, `03b210a`, machine `sealed`. Last guest line,
guest-time **9.05s**:

```
[    9.053528] systemd[1]: Starting Virtual Console Setup...
        <4m50s of total silence>
RuntimeError: Shell did not start in time
```

**Occurrence 2** — `test-identity-boot`, `6fbd328`, run `32751833582` **attempt 1** (note: `gh run
view --log` returns the LATEST attempt, which for this run is the green rerun; `--attempt 1` is
required to see the failing one, and reading the wrong attempt would have shown a clean 278s pass).
Last guest lines, guest-time **13.0-13.8s**:

```
[   13.011831] systemd[1]: Starting Virtual Console Setup...
[   13.127372] systemd[1]: systemd-vconsole-setup.service: Deactivated successfully.
[   13.130212] systemd[1]: Stopped Virtual Console Setup.
[   13.169598] systemd[1]: Starting Virtual Console Setup...
[   13.227441] systemd[1]: systemd-vconsole-setup.service: Deactivated successfully.
[   13.231150] systemd[1]: Stopped Virtual Console Setup.
[   13.236991] systemd[1]: Starting Virtual Console Setup...
[   13.832705] systemd[1]: Finished Virtual Console Setup.
        <silence>
RuntimeError: Shell did not start in time
```

**Two different tests, two different machines, two different commits — and the guest goes silent at
the same boot stage, with `systemd-vconsole-setup` cycling repeatedly immediately before it, in
both.** That is the first evidence in this entry that is about the IMAGE rather than about any
test, and it is what finally justifies the retitle: this was never an identity-boot bug.

**Hypothesis, stated as a hypothesis.** `systemd-vconsole-setup` and the nixos test driver's serial
backdoor both want the console device. A race between them would produce exactly this silhouette:
the guest is alive and healthy (occurrence 2 shows vconsole-setup *Finished* cleanly), but the
backdoor shell never becomes reachable, so the driver waits out its full timeout and reports the
only thing it can see — "Shell did not start in time." **Not confirmed.** Two data points at the
same stage is suggestive and is also what you would get from any stage that happens to be where the
boot log naturally thins out.

**What would confirm or kill it, chosen so that a null result is informative either way.** On a
GREEN run of the same tests, find where the backdoor shell comes up relative to
`systemd-vconsole-setup`. If the backdoor consistently connects BEFORE vconsole-setup runs on green
boots and the two failures are the cases where the ordering inverted, the race is real and the fix
is an ordering constraint, not a timeout. If the backdoor comes up after vconsole-setup on green
boots too, then vconsole-setup is merely the last chatty unit before a quiet stretch, this
correlation is an artifact of where the log thins, and the next place to look is what the driver
does between spawning qemu and its first successful connect.

**Occurrence 1 (`3c99650`) could not be read**: it has aged out of `gh run list --limit 60` on this
repo. Recorded as unavailable rather than omitted — the third data point that would have made this
a pattern instead of a pair is simply gone, which is an argument for capturing the last serial line
into this file AT THE TIME rather than planning to go back for it.

| | |
|---|---|
| **Occurrences** | 3 (logs read: 2 of 3; occurrence 1 aged out of the run list) |
| **Status** | open — image-level, not test-level; wedge stage identified, cause still hypothesis |

### The confirming observation was taken immediately, and it WEAKENS the hypothesis it tested

Taken in the same tick rather than deferred, from `32773211071` (green `93558da`,
`test-identity-boot`):

```
[    3.384650] systemd[1]: Starting Virtual Console Setup...
[    3.519639] systemd[1]: Finished Virtual Console Setup.
[    3.837610] systemd[1]: systemd-vconsole-setup.service: Deactivated successfully.
[    3.853525] systemd[1]: Stopping Virtual Console Setup...
[    3.856360] systemd[1]: Starting Virtual Console Setup...
         OK   Finished Virtual Console Setup.
```

**The repeated start/stop cycling is NORMAL.** It happens on green boots too, in the same shape.
The previous section flagged the cycling as "an observation, not a diagnosis" and was right to; it
is not distinctive and carries no signal. The vconsole-contention hypothesis is not confirmed, and
the null result is the informative one this entry asked for.

**But the timing is a real signal, and it corrects something I asserted two sections ago.** The
green boot reaches vconsole-setup at **3.4-3.9s**. The two failures reach the same stage at
**9.05s** and **13.0-13.8s** — three to four times later, at an identical point in the boot
sequence. Those guests were already far behind before they went quiet.

**Correction, and it changes the recommended action, which is why it is stated rather than
quietly folded in.** The third-occurrence section says *"That is a WEDGE, not a slowness"* and
*"Do not raise a timeout in the meantime."* The first half is over-strong on this evidence: the
failing boots were demonstrably slow *as well as* eventually silent, and "wedge" was inferred from
the silence alone without ever comparing against a green boot's clock. What the evidence now
supports is **a guest running 3-4x slow that then stops emitting** — which is compatible with a
wedge, and equally compatible with a guest so starved of CPU that it makes no visible progress
inside the driver's window.

The **operational** advice does not change, for a different reason than the one first given. Do not
raise the timeout — not because a timeout cannot help a wedge, but because a 3-4x slowdown against
a shared CI runner points at resource contention, and raising the limit would convert a fast red
into a slow one while removing the only signal that the runner is oversubscribed.

**Next step, revised again.** Stop looking at the boot log's content and start looking at its
clock. On every future occurrence record the guest timestamp of a fixed early landmark (e.g.
`Starting Virtual Console Setup`) alongside the same landmark from a green run of the same test.
If the ratio is consistently >2x, this is runner contention and belongs to the workflow's
concurrency/resource settings, not to the image and not to any test. Two data points say 3-4x;
that is a hypothesis with a cheap test and a clear owner.
