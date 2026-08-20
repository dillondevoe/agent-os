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
| 4 | The Dell watcher's dead tailscale leg | A monitoring leg that could no longer fire, while continuing to read as coverage. | operational (jarvis-sync) |
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
