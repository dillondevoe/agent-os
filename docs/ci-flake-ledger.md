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

**Same-SHA control: `gh run rerun 32770548874 --failed` was triggered at 20:2xZ and had not
reached a verdict when this entry was written.** Stated as pending rather than assumed green —
that is the exact failure mode the first entry in this file had to retract.

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
