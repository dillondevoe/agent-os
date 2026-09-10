# Convergence Vision — Where Agent OS Starts to Feel Like Something

*Status: vision page for founder review. Nothing here is committed to build. 2026-08-29.*

The critique on the table: **"it feels sparse."** The OS boots, deploys, and is correct — Phase 0 and Phase 1 are done, the broker and confirm seams are green, and Phase 2 (the Djinn as an MCP server) is in flight — and it does not yet feel like anything. This page names the convergence point between user delight and the tooling we already have, and proposes the first three small deliverables that would make the Dell feel *inhabited* rather than merely running.

---

## (a) Delight, concretely: the first 60 seconds after boot

What happens today: quiet splash, then tty1 with a smear of Hyprland/session chatter above a text prompt. It reads as a machine that hasn't finished getting dressed.

What the first 60 seconds should be:

**0–8 seconds.** The lid opens, the splash holds — one mark, no scroll. No Hyprland chatter, no session lines. Anything the machine wants to say that a person can't act on has been *relocated*, not suppressed — it's findable later, in plain words. The screen the person sees carries only what concerns them.

**8–15 seconds.** The prompt doesn't arrive empty. It arrives *already knowing*. One or two short lines, in the machine's own voice, drawn from `mem` and last session's tail: "Back. Last time you were mid-way through the calendar module — want me to pick that up?" or, if nothing is pending, something true and small: "Quiet night. Nothing broke while you were gone." This is not a MOTD. It's the difference between walking into an empty office and walking into one where someone looked up.

**15–30 seconds.** The person types something — anything. The first token comes back *fast*, because the local floor model was warmed during boot idle, its KV cache already primed with the session preamble and recent memory. The person never sees a loading state on the first exchange. (We have not measured current time-to-first-token; that measurement is the first act of this work, not a claim.)

**30–60 seconds.** The person asks for something that requires a capability — read a file, remember a fact — and instead of a log line, they see a single legible sentence of the harness thinking: "Reading your notes from Tuesday… found it." When the broker asks for confirmation, the ask is one human sentence with a clear yes/no, not a JSON blob. By the end of the first minute, the person has been greeted by name, answered instantly, and watched the machine do one visibly smart thing on their behalf.

That's the whole minute. Nothing in it requires a compositor, a GUI, or a mascot. It's a text screen — but one designed by someone with taste, on a machine that was awake before you were.

## (b) The convergence point: **the Attentive Prompt**

The four asks — camelid runtime ingenuity, harness magic, skilled UI/UX, at-will apps — are not four features. They compose into one thing:

> **The prompt is never cold, never blank, and never dumb. Everything the person meets is already warm, already aware, and already fit to them.**

Call it the **Attentive Prompt**. It's the single experience the four phrases describe from different angles:

- **Runtime ingenuity** is what makes attention *possible* — a local model that's pre-warmed and cache-primed is a runtime that can afford to speculate, to have the greeting composed before the person sits down, to answer without a spinner. Cold inference can't be attentive; warm inference can.
- **Harness magic** is attention *acting* — the harness composing `mem recall` + capabilities + broker into moves the person watches happen in one sentence each, rather than reading about in logs afterward.
- **Skilled UI/UX** is attention *legible* — every surface (the boot screen, the confirm channel, the shell's narration) written and laid out like a person with taste made it, because the most cutting-edge runtime in the world reads as junk if its first screen is session chatter.
- **Bespoke apps** are attention *fulfilled* — "I need a thing for this" answered now, from the `*-open.nix` surface we already have eighteen of, conjured by the same brain that greeted you.

The test for any future work: *does it make the prompt more attentive, or is it just another feature?* A vision that ships four parallel workstreams has failed; a vision where every workstream cashes out at the same prompt has converged.

## (c) The first three deliverables

Ordered by delight-per-effort. Each is ≤1 work package and demonstrable to Dillon on the Dell in one sitting. Each is a facet of the Attentive Prompt, not a standalone feature.

### 1. The Greeting — the prompt arrives knowing you

**What it is.** On login, before the person types anything, `agent-shell` composes one or two lines from local state: `mem` (recent entries, open threads), the tail of the last session, and anything the orchestration layer (`agos_observe.py` / `agos_events.py`) flagged overnight. Composed by the local floor model so it works offline and costs nothing. If there's genuinely nothing, it says something short and true rather than faking warmth.

**The demo.** Reboot the Dell with Dillon watching. Splash, then tty1, and the first thing on screen is the machine acknowledging *them* and *what was in flight*. They type a reply and the conversation just continues from there. One reboot, one goosebump.

**Why it earns its place.** Highest delight-per-line-of-work available. It converts the exact moment that currently feels sparse — the empty prompt — into the moment the OS proves it's alive. It exercises three existing systems (`mem`, agent-brain, the local floor) and builds nothing new except the composition.

**What it touches.** `bin/agent-shell`, `modules/agent-brain.py`, reads from `home/memory` via `mem recall`, optionally `agos_observe.py` output. No new modules, no deferred territory.

### 2. The Warm Floor — first token before the person finishes their thought

**What it is.** During boot idle (after the splash, before login completes), the local ollama floor model is loaded and its KV cache primed with the static session preamble plus the top of recent memory — so the first exchange, and every offline exchange, starts hot. This is the camelid move: the runtime spending idle cycles so the person never spends waiting ones. First step is honest: **instrument time-to-first-token cold vs. warm on the Dell's 32GB** — we have no current numbers and will not invent any. If prewarm doesn't move the needle perceptibly, this deliverable reports that and stops.

**The demo.** Two boots side by side (or a before/after on the same box): cold prompt vs. warm prompt, stopwatch honest, then Dillon types a first message and feels the difference — or sees the measurement showing there isn't one, which is also a real result.

**Why it earns its place.** It's the load-bearing substrate under deliverable 1 — the greeting only feels magic if the follow-up reply is instant. And it's the founder's "camelid-esque runtime ingenuity" made literal and small: speculation, caching, always-warm, on hardware we own.

**What it touches.** A systemd unit or boot-hook in the flake to preload/prime the ollama floor, `modules/agent-brain.py` for the preamble contract, a measurement script. 32GB is enough headroom for a ~14B quant resident; memory pressure with everything else running is part of what gets measured.

### 3. The Visible Hand — the harness narrates its moves in one human sentence each

**What it is.** When the brain uses a capability — `cap-mem-recall`, `cap-file-read`, broker confirmations — the shell shows one short, well-written line per move, present tense, in the machine's voice: "Checking what you told me about the Becker runs… got it." And the confirm channel's asks get the same craft pass: one plain sentence stating what will happen and why, then yes/no. Not a log level, not a spinner, not JSON. This is harness magic and skilled UI/UX in the same stroke: the smart thing *and* the taste to show it well.

**The demo.** Dillon asks the shell something that requires two or three capability hops and one broker confirmation. They watch the machine think out loud in three legible lines, approve one clean ask, and get their answer — and it reads like watching a good assistant work, not like tailing a daemon.

**Why it earns its place.** The broker/confirm seam is the most architecturally distinctive thing this OS has — the authorization gate is the *soul* of a sovereign agent OS — and today it presents as plumbing. Making the seam beautiful is where "someone with taste designed it" pays off hardest per hour, because the mechanism already works; only the surface changes.

**What it touches.** `bin/agent-shell` presentation layer, `bin/confirm` / `modules/confirm.nix` message formatting, possibly a thin narration hook in `modules/agent-brain.py`. Zero changes to broker semantics, sandboxing, or the capability registry.

---

## On WP-L1, the log console — and where it lands

The log console (`docs/log-console-spec.md`) is the *premise* under the first eight seconds of section (a) — relocate the noise, keep the boot screen human — and its own spec's measured correction matters here: the ACPI lines are already console-suppressed, and the remaining tty1 noise is Hyprland/session output. So the delight-critical slice is small: **silence the session chatter on tty1** so the splash-to-greeting sequence is clean. That slice rides inside deliverable 1 as a prerequisite polish, not a fourth work package. The full findable-console (the browsable "place for the machine's noise") is good and honest work, but on pure delight-per-effort it loses to all three above: nobody falls in love with a machine because its logs are well-filed. Build it after the greeting exists, when "where did that message go?" becomes a real question someone asks.

## Deliberately excluded

- **Anything needing the GUI-guest compositor** (a window the Djinn summons) — explicitly deferred, and none of the three need it. When bespoke apps go visual, that's a decision for Dillon, not a dependency smuggled in here.
- **Browsing-as-dispatch, security-kernel hardening, brand/mascot** — all deferred per the roadmap; nothing above touches them.
- **A "bespoke app" deliverable in the first three.** The `*-open.nix` surface is the right substrate and conjuring an app on request is the fourth facet of the Attentive Prompt — but a genuinely delightful version wants either richer TUI craft or the deferred compositor, and a rushed text-only version would demo as a script launcher. It's the natural deliverable 4, scoped after Dillon rules on the compositor question.
- **Any invented numbers.** No timings, benchmarks, or user claims appear above; deliverable 2 begins by measuring the one number that matters and is allowed to conclude "no perceptible win."

The bet, stated plainly: sparse isn't a missing feature, it's a cold prompt. Warm the prompt — knowing, fast, and legible — and the same OS that boots correctly today starts to feel like it was waiting for you.
