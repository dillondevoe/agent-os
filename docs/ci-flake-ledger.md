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

## test-identity-boot — "Shell did not start in time"

| | |
|---|---|
| **First observed** | 2026-08-24T01:11Z |
| **Occurrences** | 1 here; superseded by the four-job entry below — see 2026-08-25 |
| **Commit** | `3c99650` (branch `mirror/wire-frontdoor-kick-battery`, PR #161) |
| **Job** | `vm-test (test-identity-boot)` |
| **Status** | open — folded into the harness entry below, which answers this entry's own "what would change this verdict" |

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

---

## The harness, not the tests — `backdoor.service` unreachable across four different jobs

| | |
|---|---|
| **First observed** | 2026-08-24T01:11Z (the entry above) |
| **Occurrences** | 4 visible, across 4 **different** jobs; more are hidden — see below |
| **Jobs** | `test-identity-boot`, `test-seal-faildown`, `test-selfimprove-loop-runs`, `test-egress-uid-scope` |
| **Status** | open — root cause NOT established |

**This entry answers the question the one above asked.** That entry said a second occurrence
would weaken "runner load" and point at "our VM is slow to reach a usable shell." Three more
arrived — and the discriminating fact is that they are on *different jobs*, with a signature that
does not vary:

| run | job | date (UTC) |
|---|---|---|
| `32792191355` | `vm-test (test-selfimprove-loop-runs)` | 2026-08-25T00:05Z |
| `32784416040` | `vm-test (test-identity-boot)` | 2026-08-24T22:23Z |
| `32779702396` | `vm-test (test-seal-faildown)` | 2026-08-24T21:28Z |
| `32808436764` attempt 1 | `vm-test (test-egress-uid-scope)` | 2026-08-25T04:18Z |

Every one: the driver cannot reach `backdoor.service`, retries 20 times, and gives up after
~7m07s with `RuntimeError: Shell did not start in time.`

The guest does **not** report a problem — no unit failure, no OOM, no panic. The last console
line is an ordinary one,

```
[    9.221984] systemd[1]: etc-machine\x2did.mount: Deactivated successfully.
```

— and then the console is silent for the rest of the run.

**So this is one harness defect, not four flaky tests.** The four job names are the four places
the harness happened to be standing. Filing them as four separate test problems would be the
wrong unit of work, and — per this file's entry criterion — none of the four can be explained by
the change under test.

### The rate is a lower bound, and the mechanism is worth knowing

Over the last 60 `vm-tests.yml` runs (2026-08-23T23:13Z → 2026-08-25T04:18Z): 53 success,
4 failure, 3 cancelled — 4 of 57 concluded, ~7.0%. An earlier count over a 40-run window gave
4 of 38, ~10.5%. Same underlying data, sliding window. **Neither figure is precise and neither
should be quoted as one.**

Both also undercount *by construction*, which sharpens this file's opening rule:

- `gh run list` reports only the **latest attempt**. Re-running failed jobs rewrites the run's
  conclusion in place, so a failure re-run to green **leaves the population entirely**.
- Confirmed directly: run `32808436764` lists `conclusion: success` and is `attempt: 2`; its
  attempt-1 `test-egress-uid-scope` failure appears nowhere in the listing.
- Five runs in this window are `attempt > 1` (`32808436764`, `32770548874` at attempt 3,
  `32751833582`, `32708993068`, `32678457976`). One is a confirmed instance of this defect. The
  other four are **not** claimed as such — a re-attempt can follow a cancel or a real fix, and
  that has not been checked one by one.

"A flaky job that is re-run green leaves no trace" is stated at the top of this file as a reason
to write things down. It is also literally true of the GitHub API, and that is why the ledger
cannot be reconstructed later from run history.

### What is NOT established

- **Root cause.** Two shapes fit every observation equally: (a) the console/backdoor channel is
  lost while the VM keeps running, or (b) the VM wedges outright. Nothing measured here separates
  them. Separating them needs a nix-capable box that can hold a wedged guest open for inspection;
  DVo has none. Raised as a resourcing question, not answered here.
- **Whether this is new.** No run data exists before 2026-08-24T07:41Z.
- **Whether the rate is stable.** Two windows, two numbers, roughly one day of data.

### Why it matters at 7%

An ambiguous gate gets re-run until green regardless of whether the red was noise or a
regression. A real regression landing during a flaky period is indistinguishable from the flake,
and the standard response — re-run — is the one that hides it.
