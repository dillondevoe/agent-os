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
| **Occurrences** | 1 |
| **Commit** | `3c99650` (branch `mirror/wire-frontdoor-kick-battery`, PR #161) |
| **Job** | `vm-test (test-identity-boot)` |
| **Status** | open — infrastructure-suspected, not reproduced locally |

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
