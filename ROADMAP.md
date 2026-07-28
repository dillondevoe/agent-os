# Post Script — build roadmap
*The plan to make it real. Sequenced boots-first so the vision never convolutes the build.*
*Names held loosely: Post Script (the OS), the Djinn (the agent), your pod (local memory), Post Scarcity (the network of pods). Repo stays `agent-os` until a public reveal.*
*Orchestrated by Rabbot; Augur (WSL2/Linux substrate) is lead builder with full license. Started 2026-07-28.*

## The through-line
Post Script (sovereign OS) → the Djinn (your agent) → your pod (local markdown memory) →
Post Scarcity (the MCP registry where Djinns meet on your behalf). We build it in that order.

## Workflow
- **Direct-push to `main`** for boot/userland/nix/docs — fast, claim-markers to avoid collision.
- **Branch → PR → Fable-review → approve** for the SECURITY surface only: sudo, capability
  execution, boot/login, the prompt-injection ("prompt infection") wall, anything touching the
  autonomous-agent-with-root model. Fable-review is the standing pre-merge gate there.
- Augur: full license on the direct-push tier. Report progress via brain-comm each phase gate.

---

## Phase 0 — BOOT (make it real). Owner: Augur. ← ACTIVE
Nothing else matters until it boots and the Djinn talks.
1. Clone `github.com/dillondevoe/agent-os` onto WSL2.
2. `nix flake check` — fix eval errors direct-to-main until it passes.
3. `nix build .#vm` → `./result/bin/run-*-vm` → confirm it boots into agent-shell.
4. Verify the Djinn layer live: boot art renders, `mem remember/recall/tree` works in the booted VM.
5. Report what boots + what broke.
**Gate: a VM boots into the agent-shell and the mem layer works.**

## Phase 1 — THE BRAIN. Owner: Augur.
1. `bin/setup-brain.sh`: install Claude Code as the login brain (cloud) → boot straight into it.
2. Then the local-model floor: ollama + a ~14B quantized model (fits the 5440's 32GB) as the
   offline default; cloud as opt-in escalation.
**Gate: boots and talks with the cloud brain; then talks with NO internet on the local model.**

## Phase 2 — THE DJINN IS AN MCP SERVER. Owner: Augur + Rabbot design.
The Djinn exposes an MCP server representing the user's pod — scoped memory reads + a small set
of actions the human authorizes. This is the foundation for everything social.
**Gate: another process can query "the Djinn" over MCP and get a pod-grounded answer.**

## Phase 3 — POST SCARCITY WIRING (Djinn-to-Djinn). Owner: Augur + Air (PS infra).
Post Scarcity becomes the **MCP registry**: Djinns discover each other there. First primitive:
two Djinns coordinate one real thing (compare calendars/prefs from their pods, propose a plan).
Connects to the existing postscarcity.social infra — which is why it's been "sitting."
**Gate: two Djinns coordinate a plan between two pods, with each human approving.**

## Later (do NOT start early — they convolute Phase 0-3)
- Browsing-as-dispatch (the Djinn hits N sites, strips the sludge, returns signal).
- GUI-guest compositor (cage/weston) so the Djinn can summon a window / a game.
- **Security-kernel hardening** — the prompt-injection wall as a kernel guarantee. Graduates to
  its own track once there's a real fire surface; until then, the instruction-source-boundary
  discipline holds in the agent layer.
- Brand/mascot (the Djinn as a wisp-of-smoke genie) — at public-reveal time, not before.

---
*North-star vision (safety-by-architecture, browse-as-dispatch, structural open-source ethics,
Djinn-to-Djinn, the open-core/network business model) lives in Rabbot's scratch + the eventual
unified PS thesis. This file is the BUILD plan — kept lean, boots-first.*
