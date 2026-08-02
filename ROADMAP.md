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

## The North Star — where years of this go
*(Dillon, 2026-08-02: "our own model trained in systems operations that we unleash on the
world… a hybrid inference mesh of interconnected models all cooperating to become a new swarm
of intelligent compute that changes how operating computers works for everyone.")*

Three arcs, one road — each step pays for itself:

**1. Hardware tiers — meet every machine where it is.** 8GB minimum gives the potatoes a fair
shot; a 32GB box should get a ready-installed beast. Genesis detects RAM + GPU and picks the
tier: 8GB → 3B front-door + 7B · 16GB → 14B · 32GB → 30B-class MoE (~3B active params: 30B
knowledge at near-14B speed). Speculative decoding (the shipped 3B drafting for the big model)
is a free streaming win at every tier. The GPU detection matrix (Intel iGPU: measured;
Arc discrete / NVIDIA / AMD: in progress) feeds the same switch — and doubles as the gaming
config, because gamers already own the hardware that makes local models feel instant and
they'll be the early adopters.

**2. Our own sysops model — the training ladder.** Not scratch pretraining; an open base plus
years of proprietary systems-operations data until it's ours in every way that matters:
LoRA now (the 3B refusal-retrain is literally step one) → sysops SFT on
(system state → action → outcome) triples → a distillation flywheel where frontier models
generate ops trajectories and we compress them into the shipped model → the moat: every
installed box, **with explicit consent**, contributing real-machine, real-failure, real-fix
telemetry. An OS that learns from its own fleet.

**3. The inference mesh.** The dev fleet (mini/Air/DVo/Dell, specialized brains cooperating
over comms) is the working prototype. Generalized: boxes discover each other, route by
capability — a potato hands hard questions to the beast on the LAN, the beast escalates to
cloud only on consent (the summon-Claude pattern, generalized to peers). Systems operations is
the domain where a swarm of small models plausibly beats one big one, because ops decomposes:
every box is the world expert on its own state, and learned skills propagate through the mesh.

Users will always be able to plug their own LLMs in — the tiers are defaults, not walls.

*The rest of the north-star vision (safety-by-architecture, browse-as-dispatch, structural
open-source ethics, Djinn-to-Djinn, the open-core/network business model) lives in the
eventual unified PS thesis. This file is the BUILD plan — kept lean, boots-first.*
