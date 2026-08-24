# CI flake ledger

Intermittent CI failures, recorded at the moment they happen.

**Why this file exists.** A flaky job that is re-run green leaves no trace. The failure scrolls
out of the Actions list, the PR goes green, and the next person to see the same red concludes
"probably just flaky" from memory rather than from evidence — or, worse, concludes it is a real
regression and goes looking in the diff. Both readings are guesses, and both are avoidable for
the cost of writing the observation down BEFORE the re-run lands.

**The rule: a re-run does not erase an entry.** Record the failure first, then re-run. An entry
is only removed when the underlying cause is fixed, and then the fix is named here.

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

**What was NOT done, said plainly.** The cause was not identified. The job was re-run. A green
re-run is not evidence that the first red was spurious — it is only evidence that the failure is
not deterministic, which is what "flake" means and is why this entry stays open at one
occurrence rather than being closed by the green.
