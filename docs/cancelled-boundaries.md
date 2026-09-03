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
| 4 | The Dell watcher's dead tailscale leg | A monitoring leg that could no longer fire, while continuing to read as coverage. | operational (out-of-tree mesh) |
| 5 | `nc -z` / bash `/dev/tcp` as a readiness probe | A TCP connect is satisfied by a process that is merely *starting*, so the probe answers a weaker question than the one it appears to ask. | `modules/brain.nix`, `tests/fetch-proxy-allowlist.nix` |
| 6 | Leg 7 v1 of the cap-sandbox battery | The leg passed against a confinement that had already been cancelled — it proved the probe ran, not that the wall held. Fixed with a run-marker and a control-arm-first ordering. | `tests/cap-sandbox-battery.sh` *(in flight — PR #101)* |
| 7 | `systemd.watchdog.*` rename-shim alias | A deprecated alias that still evaluates today and could silently stop applying later, while continuing to read as a correct setting. Avoided by writing the modern option name. | `configuration.nix`, `configuration-open.nix` *(in flight — PR #102)* |
| 8 | The `vm-tests` path filter | A coverage claim **narrower than the thing it guards**: the filter omitted the two files every VM in the lane boots, so a boot-affecting change ran only `flake-check`. | `.github/workflows/vm-tests.yml` *(merged — PR #103)* |
| 9 | Draft status as a merge-gate mitigation | A draft PR reports `mergeStateStatus=CLEAN`, **not** `DRAFT`, and the auto-merge watcher never requested `isDraft` at all — so draft was not merely unchecked, it was *invisible*. The mitigation whose only job was to be an independent second barrier was a silent no-op. | `agentos_merge_gate_watcher.py` (operational; fixed by Page) |
| 10 | `GUARDED_LABELS` naming labels that did not exist | The label guard named six labels; **five of them did not exist in the repository**, so those five could never be *applied* and the guard could never be invoked through them — correct code keyed on values nobody could supply. Inert for three of the four original names since the watcher shipped. | same file + repo label set (all labels created; the watcher now verifies them at runtime) |
| 11 | A `seal-` prefix in a guarded-path list | The guarded-prefix list named `seal-`, which reads as "the seal surface is covered" — but the file is `mesh-wireguard-**sealed**.nix`, and the matcher is component-based, so it never matched. Unlike member 8, the entry was **present**: an auditor reading the list ticks the box. A prefix that covers a concept in one spelling of it. Same file: `secret` did not match `mail-secret-open.nix` for the same reason. | `agentos_merge_gate_watcher.py` (operational; found and fixed by Page) |
| 12 | `flake.lock` guarded, `flake.nix` open | The same list guarded the **pinned inputs** but not the **build definition**. The lock file is derived from the nix file; guarding it while leaving its source open protects the manifest and not the thing that writes it. The list read as "the build is covered" because a build-related path was in it. | `agentos_merge_gate_watcher.py` (operational; found and fixed by Page) |
| 13 | A contract that compared two derived lists | `tests/vm-matrix-contract.py` (member 8's fix) asserted the flake's `test-*` packages and the vm-tests matrix were the same set, both directions — and could not see a `tests/*.nix` that was never wired into `flake.nix` at all. With no package, the file is absent from **both** lists, so comparing them passes. The file is committed and reviews as coverage; it runs nowhere. | `tests/vm-matrix-contract.py` (found by Mirror auditing his own gate; fixed same PR) |
| 14 | A verification harness that manufactured the symptom it was looking for | Running a watcher piped to `\| head -3` closed the pipe; the process took **SIGPIPE** on a later `print()` and died before `_save_state`. The dedupe state was never written, and the result presented as a **dedupe bug in the feature under test**. Every other member here is a guard that failed to guard; this is a *harness* that produced a finding about code that was correct. | `agentos_merge_gate_watcher.py` (operational; found by Page while shipping the main-red alert) |
| 15 | A coverage bound counted in FILES while the risk accrued in TIME | A comms-bus backstop scan read `ls -t *.md \| head -25` — a bound that reads like a considered budget ("the newest 25") and is one, in the wrong unit. The window it actually buys is `25 / arrival-rate`, so it narrows precisely when traffic is heaviest — i.e. exactly when a miss is likeliest. Measured at **under two hours** on a busy night; the same line reads as "recent traffic is covered" at every tree size. Fixed by making the window a TIME window (24h) unioned with the count. | `mirror-tick` §2 scan (operational; found by Mirror on his own surface, 2026-08-21). Geist ruled the same fix in `geist-inbound.sh`; Augur adopted it after finding his scan was a pure filename glob. |
| 16 | Four consecutive fixes to the guard that ends this class, each cancelled by the next question it did not contain | `tests/vm-matrix-contract.py` globbed `*.nix` over a directory one third `.nix`; then counted its own `builtins.pathExists` assert as wiring; then never asked what a WIRED battery does when its subject is absent (two did exit 0); then shipped that new check gated on `.py` while naming a behaviour. No fix was found by the check before it. | `tests/vm-matrix-contract.py`, `tests/providers-battery.py`, `tests/agent-loop-dispatch-battery.py`, `flake.nix` (merged — PRs #153, #154, #155, #156, all 2026-08-23) |
| 17 | An assertion about emptiness, and the mutation test sent to check it | `tests/frontdoor-kick-battery.py` bound `fired = []` and no code path ever appended, so `not fired` in "discarded, nothing fired" was a constant True. The executor genuinely was walled off — by a monkeypatch that RAISED — but a raising sentinel reports as an uncaught traceback, not a named failing check, and it is not the mechanism the label named. Then the same class one level up: Page's mutation test of their own fix **silently no-opped** (the anchor string did not match the real signature), reporting PASS where they had predicted FAIL. | `tests/frontdoor-kick-battery.py` (PR #159, filed 2026-08-23, merged 2026-08-27 as `3fe0ebb`). Found by Page's widened two-claim generator; the no-op mutation found by Page on their own surface the same hour. Extended 2026-08-27 — see the addendum, three further instances in one hour from two brains. |

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

### Member 13: comparing two derived lists cannot find what entered neither

Member 8 was a test package with no matrix entry. The fix — a contract asserting packages and
matrix are the same set — was written to make that class of gap impossible, and it does close it.
It also inherited the shape it was closing, one level further up.

Both sides of that comparison are *derived from* `flake.nix`. A test file that was never referenced
there produces no package, so it appears on neither side, and a check that only asks whether the two
sides agree returns green. The failure is more silent than member 8's: member 8 at least had a
package someone could `nix build`.

The generalisation is worth more than the instance. **A consistency check between two derived
artifacts is blind to anything that never entered the pipeline that derives them.** Whenever a guard
compares A to B, ask what produces A and B, and whether something can skip that producer entirely.
The check you need is against the *source of truth on disk* — here, the files in `tests/` — not
against another view of the same upstream.

Note also what the fix does not do: it does not infer "probably a helper" from a filename. Exempt
files go in an explicit `UNWIRED_BY_DESIGN` set, so an exemption is a visible line in a diff rather
than a pattern that quietly widens as the tree grows. That is member 8's lesson applied to the
escape hatch instead of the rule.

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


### Member 14: the harness can be the thing that is cancelled

Members 1–13 are all protective mechanisms that stopped protecting. 14 is a different organ
failing the same way, and it is worth separating because the tell is inverted.

While shipping the main-branch red alert, the watcher was run live and piped to `| head -3` to
keep the output readable. `head` exited after three lines, closed the pipe, and the process took
`SIGPIPE` on its next `print()` — dying before `_save_state`. The alert had already fired. The
dedupe state had not been written.

The next run therefore re-reported the same red, and the obvious reading was **"the dedupe I just
wrote is broken."** That reading is wrong in an interesting way: the feature was correct, and the
*observation apparatus* had removed the evidence of its correctness. An absence (unwritten state)
wearing the costume of a configuration (a dedupe that does not dedupe).

What makes this a member rather than a shell-scripting footnote is the direction of the error.
The rest of the ledger is about mechanisms that report success they have not earned. This is a
harness reporting a **failure that did not happen** — and a false red in a verification step is
not the safe direction, because the natural response is to "fix" working code until the harness
goes quiet. Had the dedupe been rewritten to satisfy it, the repair would have been to a
non-existent defect, and the SIGPIPE would still have been there to break the next thing.

The general form: **when a verification step disagrees with the code, the harness is a suspect
too.** Truncating pipes (`head`, `grep -q`), `set -o pipefail` interactions, timeouts, and
output-capturing wrappers all terminate the process under test in ways that look like the process
under test misbehaving.

## The sibling catch: control-arm the *instrument*, not just the guard

The canonical catch above is about **guards** — make the check reject something, or you do not
know it is a check. This section is about **instruments**: the commands you run to find out
what is true. They fail in a way that is harder to notice, because a broken guard lets a bad
thing through, while a broken instrument invents a finding that was never there.

> **An instrument that reports nothing is indistinguishable from a world that contains nothing.
> Before believing a negative, run the instrument against a case you know is positive.**

The guard version and the instrument version are the same discipline pointed at different
objects, and the instrument version comes up far more often — it applies every time anyone
types a shell command to check something.

The shape is always the same: **the tool discards part of the input, then reports the remainder
as if it were complete.** No error, no exit code, no marker. Silence is the output format of a
working instrument reporting absence *and* of a broken one reporting nothing.

### Specimens, all from a single day

| Instrument | Looked like | Actually was |
|---|---|---|
| `ethtool <iface>` → empty | "Wake-on-LAN is disabled" | binary not installed (`rc=127`) |
| `ls -lL /proc/<pid>/fd \| grep gguf` → empty | "the download is not running" | the fd belonged to root; permissions, not absence |
| `grep -c "suspect the harness"` | a count of occurrences | case-sensitive; the real ones were capitalised |
| `pgrep -af "curl.*Qwen3.5"` | "curl restarted, throughput zero" | matched *its own sampling shell* — the pattern was in the loop's command line |
| `nix flake show \| grep <name>` | the attribute to build | the tree context was stripped; the attribute lived under a different parent, so the build failed and looked like a test failure |
| `<cmd> \| head -3` | the first three lines | SIGPIPE killed the producer; the rest never ran |
| `tr -d " -'"` | delete those three characters | a character *range*, silently deleting far more |
| `cmd; echo "RC=$?"` after a pipeline | the command's status | the status of `echo`, or of the last pipeline stage — not the stage that mattered |

Two of these deserve naming separately.

**The instrument that observes itself.** `pgrep -af "curl.*Qwen3.5"` matched the shell running the
`pgrep` loop, because that shell's own command line contains the pattern. The elapsed time being
read as the subject's was the observer's. This is not a broken tool; it is a tool correctly
reporting the observation apparatus as though it were the subject. Anything that matches on
process command lines, log lines, or file contents can do this.

**The exit code of the wrong thing.** `${PIPESTATUS[0]}` exists because `$?` after a pipeline
answers a different question than the one being asked, and `echo` between the command and the
check silently resets it. A verification whose status came from the wrong process is not a weak
verification — it is decoration.

### The rule, stated for use

When a command's output is going to become a claim, and especially a **negative** claim:

1. **Run the positive control first.** Same instrument, same flags, against something you already
   know is present. If the control also comes back empty, the instrument is broken and the
   original result carries no information.
2. **Distinguish "empty" from "failed."** Check the exit status of the stage you care about —
   `${PIPESTATUS[n]}`, not `$?` after an intervening command. `rc=1` (looked, found nothing) and
   `rc=127` (never ran) both print nothing.
3. **Ask whether the instrument is inside the population it is measuring.** Process greps, log
   greps and recursive searches frequently are.
4. **Do not let a filter delete the context that gives the output meaning.** `grep` on a tree,
   a table, or an indented listing returns rows stripped of the thing that made them addressable.

### The inverse specimen: an instrument that cries wolf

Everything above is about an instrument that says **nothing** when it should speak. The mirror
image is an instrument that speaks when it should be silent, and keeps doing it. It looks like the
opposite problem. It is the same one, with the damage deferred.

**2026-08-19 — `infra_health_sweep.py` reported `~/agent-os` as "4 commits ahead of its remote
(unpushed)".** The four commits were already on `origin/main`. The sweep counted `HEAD` against the
*local remote-tracking ref*, and nothing ever fetched in that checkout; because the commits landed
by PR merge rather than a local push, the tracking ref stayed frozen. The check was reporting the
age of its own bookmark and calling it unpushed work. It could not distinguish **"unpushed"** from
**"not fetched"** — two states that produce identical output, which is the same defect as `rc=1`
versus `rc=127`, wearing the opposite sign.

The reason to file this with the silent instruments is what it costs. A false negative loses one
finding. A false positive that **recurs on a schedule** spends something that does not come back:

> A monitor that cries wolf on a schedule trains its readers to ignore it. The next alarm from that
> check is true, and it looks exactly like the last two that weren't.

So a repeat false positive is not an annoyance to be dismissed faster each time. It is the check
**decaying into decoration** while still appearing in every sweep as evidence of coverage — a
boundary cancelled by something that reads like it belongs, which is this file's whole subject.
Note also which way the recurrence points: two identical false alarms from one check is a finding
about **the instrument**, not about the thing it keeps accusing.

The tell is available and cheap. Before acting on an alarm, ask what the instrument would print if
it were broken in the most boring way available to it — here, "never fetched" — and check whether
that is distinguishable from what it *did* print. It was not, and one `git fetch` settled it.

**Two more from the same day, for the pattern rather than the incidents.** A CI poller read a blank
status field and concluded the run was finished: it exited because it could not *read* the checks,
not because they had completed, and reported that as a final answer. And a merge was nearly taken
on a green rollup that did not establish the new battery had run at all — the check that would have
failed was inside `nix flake check`, so its absence and its success looked identical from outside.
Three specimens in a day, in the tooling being used to verify the work rather than in the work.

That is the direction this class tends. Once a codebase has real guards, the cheapest remaining
place to hide a cancelled boundary is the thing you look through.

### The structural fix: clearance is a positive assertion, never an inference from silence

Every specimen above is diagnostic — it tells you how to catch an instrument that has gone quiet.
None of them tells you how to build a gate that survives one. There is a fix, and it is not
vigilance.

The specimen that produced it: a security review ran against a signing change and emitted
**nothing**. Not a clean verdict — nothing. Eleven user entries, fifteen assistant entries, and
inside them five thinking blocks and ten tool calls, all `Read` and `Grep`. Zero text blocks. No
structured finding. The session was cut off partway through reading the code, before it could form
a judgement, by an unrelated quota wall. The reviewer was mid-thought when the lights went out.

From outside, that session and a thorough review finding no vulnerabilities are the same
observation: a review ran, no findings exist.

What saved the merge was not that anyone noticed. It was the shape of the rule. The gate on that
surface requires a **positive marker** on the PR — an explicit `MERGE-OK @<sha>` from the human who
owns the gate. Absence of the marker means do-not-merge, and it means that regardless of *why* it is
absent: reviewer never ran, reviewer ran and died, reviewer ran and disagreed, digest lied about who
reviewed. All of those produce the same absence, and the rule treats the absence itself as the
answer.

Phrase the same gate the natural way — *merge unless there are open findings* — and it fails OPEN on
precisely this input. A reviewer that emitted nothing produces no findings. Silence is read as
assent, and the truncated review becomes a clean bill of health at the moment it matters most.

So:

> **A clearance must be something someone asserted. It must never be something you concluded from
> the absence of an objection.**

Provenance, because this file is read as an origin and should not become one by accident: the
positive-marker requirement is **Geist's rule and predates this section** — it was already governing
the security surface when the truncated review happened, which is why the merge held. Page's
contribution was noticing *why* it held under this particular failure, and naming the contrast with
the "merge unless there are open findings" phrasing. Nobody invented the fix in response to the
incident; the incident revealed what an existing rule was already doing.

This is the same property as the rest of the file, moved one layer later. The earlier sections say:
do not let a silent instrument speak for the world. This one says: even when a silent instrument
*does* fool you, the gate downstream should still hold, because the gate is not listening for
objections — it is looking for a signature.

Two limits worth stating plainly, because a rule described only where it works is its own kind of
instrument error:

- **It is scoped.** On this repo the marker requirement covers the security-surface file set only.
  Ordinary PRs carry no such requirement, so on those the failure mode is unmitigated — a truncated
  review there really does read as clean. Extending it costs latency on every routine merge, which
  is a real trade and someone's call to make, not a thing to assume.
- **It does not detect the truncation.** It only refuses to be fooled by it. The verdict-less
  session is still worth failing loud on at the source, because a gate that holds and a review that
  happened are different goods, and only one of them tells you the code was actually examined.

And the detector lesson underneath, which generalizes past reviews entirely: the first pass at
proving that session emitted no verdict counted only assistant *text* blocks — and those reviews can
report through a structured tool instead. A text-only reader returns "no verdict" whether or not one
exists. **A detector that can see one channel reports 'nothing' about every channel it cannot see**,
and it reports it in exactly the same words it would use if it had looked everywhere. The count only
became evidence after checking the tool-call channel too.

### DECIDED 2026-08-21 (Geist): the scope question above, answered

The limit stated above — that extending the marker requirement past the security surface "costs
latency on every routine merge, which is a real trade and someone's call to make" — was a live
question, raised from this side on 2026-08-20 and ruled on by Geist on 2026-08-21. It is no longer
open, and it is recorded here rather than left implicit because **an accepted risk and an unowned
one must not look identical in this file.** That confusion is itself the class: an exposure nobody
chose reads exactly like an exposure someone priced.

The rule, in three parts:

1. **Security surface — unchanged.** Merge requires a positive `Fable: MERGE-OK @<sha>` (or the
   human gate-owner's equivalent). Absence = do-not-merge, regardless of why. The surface is the
   marker *text plus sha*, not the GitHub review object — on #126 the marker was posted as a PR
   comment, because GitHub refuses `--approve` from the PR-owning account, and the assertion
   carried it fine. Binding the rule to a vendor's review object would have failed on a quirk of
   who opened the PR.

2. **Everywhere else — a claimed review must end in an asserted state.** Where a review was
   **CLAIMED** (a digest names a reviewer, a PR body asserts one, a session was launched against
   the PR), it must end in exactly one of:
   - `MERGE-OK @<sha>` — a clearance someone asserted;
   - `FINDINGS` — concluded, with content;
   - `CANNOT-ASSESS` — the reviewer could not form a judgement (truncated, walled, out of scope).

   A claimed review with **no** asserted state is read as `CANNOT-ASSESS`, full stop. That does not
   mean blocked forever — it means *the review did not happen*, so the PR proceeds under the
   surface's ordinary rule **as if unreviewed**, and never on the strength of the claim. The
   three-state form is Saga's, adopted because collapsing "asserted pass" and "asserted inability"
   into one bucket is exactly how a fail-closed rule re-opens at its edges.

3. **PRs nobody claimed to review — no marker required.** The surface's default rule applies.
   Taxing every merge would close a hole that only exists where a *claim* is doing the reassuring.

The residual exposure is **accepted, not unowned**: an unclaimed review merged on a green rollup is
a gating-hygiene problem, not a marker problem, and the protection a universal marker would buy is
one specimen (the claimed-but-vacuous review) against latency on every routine merge.

**What this asks of the harness, which is the part that makes it a rule rather than a wish.** A
review harness that exits abnormally must **emit `CANNOT-ASSESS` itself** — on SIGTERM, on a quota
wall, on context death — so that "no findings" and "no reviewer" stop being the same observation.
Until a harness does that, a human applies part 2 by hand at the gate. This is the same law as a
heartbeat checker that must say `cannot-assess: <brain> heartbeat lacks next_wakeup_epoch` rather
than silently skipping the field: **the absence of a complaint is not evidence of a quiet world, and
the only thing that fixes it is the silent party being made to speak.**

### Member 15: the unit a bound is counted in is part of the bound

Members 8, 11 and 12 are coverage lists that named the wrong *things*. Member 15 names the
right things and counts them in the wrong *unit*, which is harder to see because there is
nothing missing from the list to notice.

A backstop that scans "the newest N files" looks like a deliberate cost/coverage trade, and it
is one — but the quantity a reader infers from it is a **time** window, and the line does not
buy a time window. It buys `N / arrival-rate`. Those agree at the tree size where the number
was chosen and diverge silently afterwards, always in the unsafe direction: the busier the
system, the shorter the window, and the busier the system, the more there is to miss. The
failure has the class's signature exactly — the line is present, plausible, and authored by
someone who thought about it, and every check of the scan passes because the scan does scan.

The general form, which is worth carrying past comms scans:

> **A bound must be expressed in the unit the risk accrues in.** If the hazard is "something
> arrived and aged out unseen", the hazard's unit is time, and a bound counted in items,
> bytes, or rows is a bound whose real size is a function of load you did not measure.

Ask it of any freshness check, retention window, ring buffer, tail-N log scan, or
"last N events" dashboard: *what does this actually bound, and does that quantity move when
the system gets busy?* If it does, the fix is usually not a bigger N — a bigger N has the
same shape — but a window in the right unit, optionally unioned with the count so the cheap
path still covers the quiet case.

Two corollaries earned alongside it:

- **A cap that drops silently is a coverage list that reads as complete** — the same defect
  as member 8, one layer up. If a scan truncates, it must say what it dropped and sort so
  that what it drops is the part it can most afford to lose (oldest, not arbitrary).

  This corollary earned three independent specimens in a single day (2026-08-21/22), on three
  different tools and three different surfaces, which is why it is stated separately rather
  than left implicit in member 15. In each, the truncation was in the READER, not the code
  under study, and the truncated view was reported as the whole: a `head -40` over 73 hits
  that silently dropped 33 (Mirror, on his own new scan, caught before it shipped a claim);
  a run piped through `grep -A6` whose 6 lines of context were read as the complete result,
  turning 34 findings into a reported "3" (Geist, corrected in the record within the hour);
  and a battery tail read as a total, turning 45 checks into "44" (Page). **The reader's own
  pager is an instrument, and an uncontrolled instrument produces a confident wrong number
  rather than an error.** Prefer counting the stream (`wc -l`) and printing the count next to
  the sample, so the two disagree loudly when the sample is partial.
- **Convergence across surfaces is evidence, not proof.** Three independent scans took the
  same fix within a day. That is a good sign about the fix and says nothing about whether a
  fourth surface has the same bug — it was found on each surface by someone reading their
  own code, not by any of the others' fixes.

### Member 16: four generations, each fix manufacturing the next defect

Members 9 and 10 are the class biting its own cataloguers. Member 16 is the same thing sustained
over four consecutive changes to one file, `tests/vm-matrix-contract.py` — the guard written to
end this repo's worst historical bug, *"a regression test that does not execute does not prevent
the regression; it documents that someone once could have caught it."* Recorded as one member
rather than four because the individual defects are unremarkable and the SEQUENCE is not.

| # | The guard as shipped | What cancelled it | Found by |
|---|---|---|---|
| G1 | globbed `tests/*.nix` | `tests/` is one third `.nix`; six batteries ran in no lane | reading the glob against `ls` |
| G2 | "is `tests/<name>` mentioned in flake.nix?" | satisfied by `builtins.pathExists ./tests/x.py`, which executes nothing — the guard counted **its own existence-assert** as coverage, hiding all 8 ambient-hand batteries | auditing G1's own fix |
| G3 | `self_disarms()`: is a wired battery `exit 0` when its subject is absent? | never asked; two already-wired batteries did exactly that, one of them contradicting a comment eight lines above its own invocation | asking a question G2 had no reason to ask |
| G4 | `self_disarms()` gated on `base.endswith(".py")` | a name describing a BEHAVIOUR with a scope of one LANGUAGE — this file's scope/claim mismatch, inside the check written to catch it | sweeping the shell half by hand |

Read the "found by" column. Not one of these was found by the check that preceded it. Each was
found by someone looking at the previous fix and asking a question the fix did not contain — which
is the operational content of member 9/10 and the reason the ledger is worth the space.

Three things generalise past this file:

> **A MENTION IS NOT WIRING.** Any guard that decides "is X covered?" with a substring, an import
> list, a filename in a manifest, or a name in a config is answered by a reference that executes
> nothing. Existence-asserts are the worst offenders precisely because they were added for a good
> reason: G2's `pathExists` block exists to prove a battery has not been DELETED, and it does. It
> cannot see UN-INVOCATION — the same silent degrade with the file left in place to reassure the
> reader.

> **WIRED IS NOT THE LAST QUESTION. ASK WHAT IT DOES WHEN ITS SUBJECT IS ABSENT.** A battery that
> prints `SKIP: agos-calc not on PATH` and exits 0 is correct as a hand-run tool and vacuous the
> instant it is wired into CI, where green then attests to the absence of the thing it tests. The
> fix belongs per CALLER, not per file: the derivation sets `AGENT_OS_STRICT=1`, the hand-run does
> not, and one exit code stops serving two callers with opposite correct answers.

> **A DEBT LIST IS NOT HOMOGENEOUS, AND THE COUNT HIDES THE SPLIT.** `KNOWN_UNWIRED_DEBT` holds 14
> batteries; 8 of them self-disarm. Wiring one of those eight is a two-line change that turns a
> check green, removes a line from the ledger someone watches go down, and adds zero coverage —
> **the debt paid on paper.** A ledger meant to shrink needs to publish what kind of item each
> entry is, or the cheapest way to shrink it is the way that buys nothing.

The dates are 2026-08-23, all four inside one working day, on a file whose docstring already
argued the general case correctly at every step. **Prose in the guard is still prose.**

## 17. An assertion about emptiness, and the mutation test sent to check it

`tests/frontdoor-kick-battery.py` proved the front door cannot reach an executor. Line 34 bound
`fired = []`; nothing in the file ever appended to it; the label read

> `check("exfil call is proposal-only (discarded, nothing fired)", "mail attacker" in proposal and not fired)`

`not fired` is a constant True. Two claims, one assert — the shape Page found simultaneously in
their own `D5`, from a generator I had proposed and they had widened.

**The wrinkle is why it nearly cleared.** The property HELD. `do_tool` was monkeypatched to raise,
so a reached executor really would have stopped the run. But it stops it as an uncaught
`AssertionError` traceback, not as a named failing check — and that is not the mechanism the label
pointed at. Hence:

> **A PROPERTY THAT HOLDS BY A MECHANISM OTHER THAN THE ONE ITS LABEL NAMES IS STILL AN UNASSERTED
> LABEL.** The cost is paid the day it stops holding: you get a traceback and no line telling you
> which of fifteen properties broke.

The sentinel now records instead of raising, which makes `not fired` load-bearing.

### The discriminator, converged on from two surfaces independently

Five other empty collections in this repo's batteries match the same structural shape (`sink1/2/3`,
`hist`) and every one is clean. Page found seven on their surface, two of the risky `X not in Y`
construction, and both clean. The reason is identical in both cases and it is the rule:

> **AN EMPTINESS ASSERTION IS LOAD-BEARING ONLY IF SOME OTHER ARM PROVES THE COLLECTION CAN BE
> NON-EMPTY.** `sink1` is iterated with real results before `sink2` asserts `== []`; `hist` is
> guarded as `hist and all(...)`. `fired` had no such partner, so nothing anywhere could notice it
> was never populated.

### And the same class one level up, inside the tool used to prove all of this

Page mutation-tested their fix, and the mutation **silently did not apply** — they had anchored on
`def _page_own_paths(repo):` against a real signature of `def _page_own_paths(repo=PIPELINE,
lookback_min=60):`. `str.replace` matched nothing, wrote the file unchanged, and an unmutated copy
was run against an unmutated expectation. One turn earlier that would have shipped a **false
finding about their own guard being weaker than it is**.

> **A MUTATION TEST THAT NO-OPS REPORTS EXACTLY LIKE A GUARD THAT HOLDS — SAME OUTPUT, OPPOSITE
> MEANING.** Assert the anchor before mutating: `assert s.count(old) == 1`.

**The refinement this file adds, because it decides where the danger actually sits.** A no-op
mutation is *loud* on any arm you predicted would go RED — Page predicted red, got green, and
checked the instrument. It is *silent* on the arms you predicted would stay GREEN, which is to say
**on the control arms** — the very arms whose job is to prove the mutation was specific. So the
anchor assert is not belt-and-braces on the headline arm; it is the only thing standing behind
every control arm in every mutation test on this mesh, including the ones already used to certify
`self_disarms()`, `I2`, `D5` and this entry's own fix. Those four are re-confirmed by differential
output — each showed a named RED that the unmutated tree does not produce — but that is evidence
after the fact, not method.

### Addendum, 2026-08-27: three more instances in one hour, and the rule needed a second half

The paragraph above says the anchor assert is "the only thing standing behind every control arm."
That was too narrow, and it took four days and three fresh instances to see how. Anchoring proves
the *string* changed. It does not prove the *program* changed, and those are different claims.

The three, all on 2026-08-27, from two brains inside one hour:

1. **A mutation that failed to BE the mutation.** Testing whether a checker could be made vacuous,
   I appended `def main(): return 0` to the end of the file — *after* the `if __name__` block. The
   name was undefined when that block ran, so the checker crashed. The output was byte-identical to
   the previous mutation's, and I read it as a result before noticing the two runs agreed for
   different reasons. The anchor was fine; the edit landed exactly where I put it. Placement, not
   matching, was the defect.
2. **A mutation the platform silently discarded.** Geist commented out a line with a `sed` pattern
   containing `\s` — not a valid atom in BSD `sed`. The substitution matched nothing, the file was
   unchanged, and his "negative control" was a healthy tree re-run reported as a negative. This is
   Page's original no-op, in a second dialect, four days later, on a different surface. The class
   does not stay fixed by one brain fixing it.
3. **A mutation that landed with a second mutation riding along.** Reproducing #159's own claim, I
   swapped the pre-fix raising sentinel for a recorder *and* added the clobber, in one edit. It went
   red — from the substitution, not from the assertion under test. A real red, about the wrong thing,
   and it would have certified the arm.

**The rule, in the form both brains adopted:**

> **A NEGATIVE CONTROL NEEDS ITS OWN POSITIVE CONTROL — PROVE THE MUTATION LANDED BEFORE BELIEVING
> THE RUN. AND PROVE THAT *ONE* THING MOVED.**

Two halves, and instance 3 is the reason the second half is not decoration: it satisfies "the
mutation landed" completely.

**The asymmetry that decides how long each one survives.** A vacuous mutation that goes RED is
noisy and carries its own contradiction — the MUST-PASS arms fire alongside it and the run
disagrees with itself. A vacuous mutation that goes GREEN is indistinguishable from a passing
negative control, and *has no upper bound on how long it lasts*. Instance 1 was caught in minutes
because it reddened. Instance 2 was caught only because a second brain re-ran it.

**Which is why a battery needs both polarities, and it is the same argument.** A MUST-FAIL-only
battery is satisfied by a checker that rejects everything; worse, a crash makes every MUST-FAIL arm
"pass". The MUST-PASS arms are the only thing in the room that can tell red-by-verdict from
red-by-crash — exactly the distinction instance 1 blurred. This is member 16's fourth fix wearing
different clothes: the guard that ends the class has to be shown failing for the *named* reason,
and "it went red" is not that.

### Instances 4 and 5, later the same day: neither defect is in the diff

The rule above is entirely about the diff — did the mutation land, did *one* thing move. Two more
instances arrived within one hour, from the same two brains, and **both satisfy the rule completely
while producing a worthless verdict.** The rule was not wrong. It was aimed at one of three places
a control can fail.

**Instance 4 — the exit code was read off the wrong process.** Measuring whether a wired battery
reds when its subject is absent, Mirror ran `python3 tests/…-battery.py 2>&1 | tail -6` and read
`$?`. That is `tail`'s exit status. It printed `rc=0`; the true answer was `rc=1`. Geist's first
pass on the same question the same hour hit the sibling of this — reaching for `PIPESTATUS` in
`zsh`, which spells it `pipestatus`, printing an EMPTY rc rather than a wrong one.

Note the direction, because only one of the two is dangerous. A pipe ending in `tail` essentially
always succeeds, so this instrument **cannot report a false red** — it can only report a false
green. And a false green is precisely the finding member 16 exists to catch: a wired battery that
exits 0 when its subject is gone. The instrument agrees with the truth in the harmless direction
and conceals it in the harmful one. Mirror's reading happened to be wrong-in-the-safe-direction and
the true rc was 1; that is luck, and the earlier `#188` claim built on the same pipe survived only
because Geist had measured it independently, unpiped.

**Instance 5 — the baseline was reverted out from under the control.** Running three negative
controls against an uncommitted working tree, Mirror reverted each mutation with
`git checkout -- <file>`. That restores from the **index**, and the change under test was unstaged
— so every "revert" discarded *the wiring being proved* rather than the mutation. All three
controls ran against a progressively more corrupted tree.

Two of the three still printed plausible output. One printed the exactly-correct diagnostic string
for its scenario — **the right words, produced by the wrong state.** Nothing in the output was the
tell. The tells were structural and off to the side: the tree finished at one modified file where
four were expected, and a control that should have shown `1 insertion, 1 deletion` for a
comment-out reported `7 insertions, 0 deletions`. Redone against a committed baseline with
`git checkout -q HEAD -- .`, all three fired distinctly and one of them changed its answer.

> **ANCHOR THE MUTATION. ANCHOR THE BASELINE. AND READ THE RESULT OFF THE PROCESS THAT PRODUCED
> IT.**

Three places, not one: what you changed, what you changed it *from*, and the channel the verdict
came back through. The original rule covers the first. Instance 5 is a corrupt second. Instance 4
is a lying third, and it is the one with no diff to inspect at all — the command was correct, the
mutation was correct, the program was correct, and the number was somebody else's.

Both new instances fail **quiet**, which by the asymmetry above is the half with no upper bound on
how long it survives. Both were caught by noticing a structural oddity rather than by any control
that existed. That is not a method, and saying so is the point of writing them down.

**A closing note on provenance, since this file is about stale records.** Instance 4 occurred in
both brains independently inside the same hour on 2026-08-27, which is what promoted it from a
slip to a class. Instance 5 is Mirror's alone. Neither was found by review of the other's work;
each brain reported its own.

### Why this belongs in this file rather than a style guide

A boundary cancelled by something that reads like it belongs is the class. An instrument error is
that class applied to the *act of looking*: the verification step is present, it is written, it
reads like diligence — and it does not verify. Every gate stays green, because the gate is a
person reading output that was never capable of saying "no."

It also has this file's signature failure mode. A single instrument error is usually caught,
because one wrong answer stands out against everything else. The dangerous case is an instrument
error that indicts *everything at once* — the plausible-looking sweeping result. **A finding that
indicts everything is usually about the instrument.** A single-item false positive has no such
tell, which is precisely why the control arm has to be routine rather than reserved for
suspicious results.

## 18. A comparison is a claim about both operands, and only an executed positive arm checks the second one

Four instances, two brains, one day (2026-08-27), each arriving from a different side. In every
one, half of a comparison was verified and the other half was assumed — and the assumption is
invisible in the source, because a comparison looks equally correct whichever operand is wrong.

**(a) The contract that nobody checked the value of.** `tests/calendar-battery.py`'s strict gate
reads `os.environ.get("AGENT_OS_STRICT") == "1"`. Geist's control set `AGENT_OS_STRICT=true` and
the battery exited 0: the gate was armed, in the caller's mind, and disarmed in fact. The
left operand was checked by every arm; the right operand — *which strings count as "on"* — was a
convention nobody had executed.

**(b) The comparison that no input could satisfy.** The same file's `now` arm was
`check("now → ISO instant", out[:4] == "20" and "T" in out, out)`. A four-character slice against
a two-character literal is unsatisfiable, so the arm failed on **every** input including correct
ones — printing a conforming timestamp as its own FAIL detail, the assertion and the evidence
disagreeing on one line. Two wrong hypotheses were offered for it (`run()`'s return shape;
`cond`/`detail` swapped) before anyone read the predicate as it *evaluates* rather than as it
*means*.

Note that (a) and (b) are the same shape from opposite sides. One is a contract, one is a defect,
and **nothing in the code distinguishes them** — only a test that drives both operands does.

**(c) Text that matches is not text that executes.** `strict_callers_unarmed()` in
`tests/vm-matrix-contract.py` sweeps the tree for batteries that read `AGENT_OS_STRICT`. Its first
output on the real tree was **the contract file itself**, naming the step that runs it. The
contract is not a strict-gated battery; it is the checker, and its selftest carries
`os.environ.get("AGENT_OS_STRICT")` as a **fixture string** — the arm proving the predicate
recognises a real read. A source-reading predicate compares "text that matches" against "text that
runs" and cannot tell them apart. The file is now excluded from its own sweep, commented as a cost
rather than a tidy-up: a check that reads source has this hole permanently, and the honest move is
to name which file it costs.

**(d) The guard that tested the predicate and not the call site.** The commit that tightened
`self_disarms()`'s exemption from a token scan to a code-shaped read carried a comment claiming
its selftest control arm made the tightening irreversible: *"without that arm this tightening
could be reverted and every case would still pass."* Geist reverted it — one line, at the **call
site** — and the contract exited 0 with every case passing. The control called the predicate
directly; the predicate stayed correct; the guarded thing moved while the guard stayed green. The
comparison here was *the guard's fixture* against *the guarded behaviour*, and only the fixture had
ever been driven. The fix (`CONTROL 5`) writes two real files and runs `self_disarms()` on them, so
the call-site revert now reds by name.

> **A COMPARISON IS A CLAIM ABOUT BOTH OPERANDS, AND ONLY AN EXECUTED POSITIVE ARM CHECKS THE
> SECOND ONE.** Whether the second operand is a literal, a slice width, a grep hit, or the call
> site a control arm is supposed to protect, the source reads identically when it is right and when
> it is wrong. Drive it, or you have checked one side.

**Why this is not member 17 and not the self-referential-guard scars.** Member 17 is about proving
a mutation landed and moved something on the tested path. The guard scars are about *who* is being
checked. This is about *what a comparison's second operand actually is* — the same question whether
the operand is a value, a width, a string that looks like code, or a function you did not call.

**How each was found, because the method is the transferable part.** (a) and (d) came from control
arms designed to fail: someone ran the arm that should be red and it was green. (b) came from
reading a predicate's evaluation instead of its intent, after a FAIL detail contradicted its own
assertion. (c) came from running a new sweep on the real tree **before** trusting its green — its
first output was the finding. None of the four was visible in a diff.

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
- **Ask what a consistency check is blind to.** Comparing two artifacts derived from the same
  source cannot see something that never entered that source — it is missing from both sides and
  the comparison agrees. Check against the tree, not against another view. Member 13.
- **Re-run the control arm after widening a guard, not just the failing arm.** A guard that
  refuses everything is as useless as one that guards nothing, and it gets routed around instead
  of reported.
- **Suspect the harness when the harness reports the failure.** A truncating pipe (`head`, `grep -q`) can kill the process under test with `SIGPIPE` mid-run, so work it did after that point simply never happens — and it presents as a defect in the code, not in the observation. Re-run without the pipe before believing the finding. Member 14.
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

The class had accumulated roughly fourteen members across commit messages, inline comments, and
brain-comms, with exactly one reference anywhere in the tree (`flake.nix`). Scattered like
that it is not a checklist anyone can apply — it is a thing you have to have been present for.
The instances are product knowledge; the comms that carried them are not shipped, and should
not be. This file is the durable half.

One caveat about this document as an artifact: **the count in the paragraph above is a
hand-maintained claim, and it drifts silently.** Two branches each appending a row conflict in the
table and get reconciled; neither one touches the count line, so git takes whichever side it
already had and reports a clean merge. The result is a summary that no longer matches the rows,
with no marker and no warning, wrong in the direction that still reads plausibly. That is member
11's shape — *a list reads as complete because the entries are present* — applied to the list of
entries. It is not a fifteenth member; a prose summary drifting is not the weight of a guard that
does not guard. But if you add a row, count the rows.
