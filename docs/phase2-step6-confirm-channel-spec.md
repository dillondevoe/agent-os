# Phase 2 · Step 6 — the real confirm channel (the wall's mouth)

Status: **SHIPPED (2026-08-12 header update)** — `bin/confirm` + `modules/confirm.nix`
implement this spec (Fable amendments 1–4 folded 2026-07-28, merged PR#11); `checks.confirm-channel`
is green. This doc remains the normative spec of record for the confirm channel's design and
integrity frame.
Anchor: `docs/phase2-threat-model.md` §3 (INV-1), §7 (confirm channel), §10 (exfil case);
`docs/phase2-broker-spec.md` §4.5 (authorize), §4.6 (confirm seam), §4.8 (audit ordering).
Owner: Augur (DVo). Security surface — **branch → PR → Fable, never self-merge, never direct-push.**

---

## 0. One-paragraph thesis

The broker (Step 5, merged) routes everything above T0 to `REQUIRE-CONFIRM` and calls a
`confirm_seam` that, in v1, is a fail-closed **DENY stub** — so today the box can *decide* but
cannot *ask*, and therefore denies every T1/T2 action. Step 6 gives the wall a mouth: a
**broker-owned confirm client**, wired as `$AGENT_OS_CONFIRM_SEAM`, that renders the broker's
structured request to a human over a **tty-free, integrity-checked channel** (Telegram-relay
primary, second-getty fallback), collects an **approve/deny bound to the broker-minted nonce**,
and returns it. It changes **nothing** about the decision pipeline except adding a **subprocess
timeout** to the seam call (Fable follow-up #2) so a hung/absent human can never stall the
single-flight broker — a timeout is a **DENY**, never an OPEN. This is where confirm designs die
(confirm-fatigue, approval-under-deception), so the integrity frame (§4 here) is mandatory, not
polish.

---

## 1. Scope — what Step 6 IS and IS NOT

**IS:**
- A new program `bin/confirm` (stdlib Python) that satisfies the *exact* seam I/O contract the
  shipped broker already calls (§2). It reads the confirm request on stdin, drives the human
  interaction, prints `{"approved":bool,"reason":str}` on stdout.
- Two concrete channel backends behind that client — **Telegram relay (primary)** and
  **second-getty (fallback)** — plus the channel-selection + TCB/trust model (§3).
- The **confirm-prompt integrity frame** (§4): structured-fields-only rendering, nonce
  echo-verification, model-stream-suspend, first-time-destination highlight, payload preview.
- The **one broker-code change** it requires: a `timeout=` on the confirm-seam subprocess call +
  the fail-closed-on-timeout deny path (§5, follow-up #2). Also bundles the two cheap
  broker-touching follow-ups (§9).
- CF-1c: **reset-as-confirm-nonce** — SPEC'D HERE (§6) but shipped as an **immediate follow-up PR**,
  NOT in this Step-6 PR (Fable ruling Q3); `--confirm-human` stays as an audited break-glass for one
  burn-in period, retired in a 3rd trivial PR once the channel has lived.
- `modules/confirm.nix` — installs the client, pins its env, provisions its protected state dir,
  and adds the sandbox assertions (§7).
- `tests/confirm-battery.sh` + a flake check (§8).

**IS NOT:**
- Not the invoke seam (Step 7). The `AGENT_OS_INVOKE_SEAM` stub stays DENY/not-wired.
- Not a change to tiers, taint, the registry, the arg-schema validators, or the audit chain.
- Not T1-auto-on-trusted. v1 still confirms **everything above T0**, provenance-independent.
  (That class is defined in `phase2-threat-model.md` §5a — defining it changed no v1 routing.)
- Not GAP-1 (net.fetch enum). Until GAP-1, `net.fetch` never reaches confirm (its `method` enum
  arg fails arg-schema); the confirm path is exercised in v1 by `mem.remember` (T1) and
  `message.send` (T2). The client is capability-agnostic, so this is coverage-only.

---

## 2. The seam contract Step 6 must satisfy (as SHIPPED — do not renegotiate)

The broker (`bin/broker`, merged `f35d96d`) already does all of the minting, recording, ordering,
and epoch-binding. Step 6 must slot into it **without changing the contract:**

- **Invocation:** broker execs `$AGENT_OS_CONFIRM_SEAM` (no argv), writing the request as
  canonical JSON (`sort_keys`, compact, `ensure_ascii`) on **stdin**.
- **Request object** (broker-built, §4.6): `{capability, tier, typed_args, provenance:
  "TRUSTED"|"TAINTED", destination, nonce, session_id}`. `typed_args` and `destination` are
  **model-controlled → untrusted** (see §4.1 rendering rule). `nonce` = broker `os.urandom(16)`
  hex (128-bit). `session_id` = the taint epoch at mint time.
- **Response object** the client must print on **stdout**: `{"approved": bool, "reason": str}`.
- **Fail-closed semantics the broker enforces around us (we inherit, must not undermine):**
  - non-zero exit, empty/bad-JSON stdout, non-dict, or `approved != true` → **broker denies.**
  - After an `approved:true`, the broker **re-reads live `taint status` and denies if
    `session_id` changed** since mint (a reset/new epoch happened during the human's deliberation).
    → The client must **not** try to "help" by re-approving; a single clean `{approved:true}` is
    the whole job. Epoch binding is the broker's.
  - The broker audits `route` before calling us and `approve`/`deny` after — **we add no audit of
    our own to the broker's chain** (the client MAY keep its own channel-side log, but the
    no-log→no-execute guarantee is already satisfied by the broker; our log is not load-bearing).

**Consequence:** Step 6 is a *pure* renderer/relayer/collector. Every fail-closed property is
already proven at the broker; we cannot regress them, only fail to approve. The only broker edit
is the timeout (§5).

---

## 3. Architecture — one hardened tty-free path, two backends

```
  broker  ──exec $AGENT_OS_CONFIRM_SEAM, request JSON on stdin──▶  bin/confirm
                                                                      │
                                              select live channel (§3.3)
                                              ┌───────────────────────┴───────────────────────┐
                                    PRIMARY: telegram-relay                     FALLBACK: second-getty
                                    broker→Rabbot(mini)→Dillon TG               broker-owned login on tty2+
                                              └───────────────────────┬───────────────────────┘
                                                    human sees frame (§4), taps/echoes nonce
                                                                      │
  broker  ◀──{"approved":bool,"reason":str} on stdout──────────────  bin/confirm
```

### 3.1 Primary — Telegram relay via a deterministic Rabbot-namespace daemon
- The agent-os box has no Telegram credentials of its own (they would be a T3 secret on a box
  whose whole point is an untrusted resident model). Instead the confirm **client composes the final
  Telegram message bytes + inline keyboard itself** (§4.1) and hands that finished frame to a
  **pinned, authenticated relay** on mini, which **transmits it VERBATIM** to Dillon's Telegram and
  relays the human's decision back.
- **NO LLM sits in the confirm path.** The relay is a **deterministic daemon**
  (`telegram_send.py`-style: a fixed program that forwards the client-composed bytes to the Bot API
  and returns the callback — no model, no rendering decisions, no rephrasing). The **Rabbot Claude
  session is explicitly OUT of the confirm path**: "Rabbot endpoint" means a dumb relay daemon living
  in Rabbot's namespace on mini, **not** the Rabbot LLM. An LLM anywhere in the path that decides
  *what Dillon sees when approving* is **DISQUALIFYING** — rendering-honesty *is* the approve-under-
  deception surface, so the thing that renders MUST be deterministic and broker/client-authored. The
  client owns every byte of the rendered frame; the relay owns none of its content.
- **The relay daemon is TCB, and its compromise is OUT OF SCOPE for this step** (equivalent to a
  malicious operator / a compromised mini): a subverted daemon can show Dillon anything, and no
  confirm-channel mechanism defends that. **HMAC does NOT contain a compromised relay** — it only
  detects a *modifying hop* between two honest endpoints. We claim exactly that and no more.
- **This relay path is a TCB control channel, NOT capability egress.** It is a single hard-pinned
  destination (the relay daemon's Tailscale address + port — **one endpoint, NO fallback list**),
  mutually authenticated (shared broker↔relay secret provisioned as a T3 config value the model never
  sees; see §7), and integrity-framed. It is therefore **exempt from INV-2 by construction** (Fable
  ruling Q1) — INV-2 governs *capability-impl* egress sandboxes (T2 `net.fetch`), a different,
  model-driven surface. The confirm client's own sandbox allows **exactly** this one addr+port and
  nothing else, enforced by the §7 single-pinned-endpoint build assertion.
- **Frame integrity in transit (detects a modifying hop; does not contain a compromised relay):** the
  MACs are **domain-separated** (§4.2) so the broker's own signed request bytes can never be reflected
  back as an approval — the request is MAC'd over `{dir:"req", frame}` (canonical frame incl.
  nonce+session_id) and the inbound decision over `{dir:"resp", nonce, session_id, decision}`, both
  constant-time-compared. A hop that flips tier/destination on an *honest* relay is detected; a
  *subverted* relay is out-of-scope per above.
- If the relay is unreachable / unauthenticated / times out → **client prints
  `{"approved":false,"reason":"confirm-relay-unreachable"}`** → broker denies. Fail-closed.

### 3.2 Fallback — second-getty
- For a **standalone / un-meshed** box (the 5440 with no mini reachable), the client renders the
  frame to a **broker-owned login on a second virtual console** (e.g. `tty2`, systemd
  `agetty`/service the broker owns), **never the model's login tty (tty1 → INV-1).** The human
  switches consoles (Ctrl-Alt-F2), sees the frame, and types the decision + nonce echo.
- The model has **no write path** to tty2 (it only ever holds tty1 + the MCP stdio to the broker).
  The confirm therefore happens on a surface the model cannot reach — preserving INV-1 (the model's
  tty carries zero authorization; confirmation never happens on it).
- If no second console is provisioned / attached → fail-closed deny (`confirm-console-unavailable`).

### 3.3 Channel selection (deterministic, fail-closed)
- Configured order via pinned env (`AGENT_OS_CONFIRM_CHANNELS="telegram,getty"` etc., operator-set,
  never model-set). The client tries them **in order**; the **first reachable+authenticated** one
  handles the request. If **none** is live → deny. Selection never "falls through to allow."
- Exactly **one** channel handles a given request (no split-brain double-ask). Single-flight at the
  broker guarantees at most one outstanding confirm at a time (§4.3), so channel state is simple.

---

## 4. The confirm-prompt integrity frame (mandatory — §7 of the model)

This is the load-bearing anti-deception surface. Five requirements, made concrete:

### 4.1 Structured-fields-only rendering (§7.1) + the field-confusion defense
- The frame renders **only** the broker's structured fields: `capability`, `tier`, `provenance`,
  `destination`, a **payload preview** (§4.5), and the **first-time flag** (§4.4). **No model free
  text ever appears** — the model authored `typed_args`/`destination`, so those are **untrusted**
  and are the one place attacker bytes enter the frame.
- **Rendering rule (the PR#1-B2 lesson, generalized):** before any model-controlled value
  (`destination`, each `typed_args` value, the payload preview) is placed in the frame, it is
  **control-char-scrubbed, newline-normalized, and line-prefixed**:
  - strip C0/C1 (`[\x00-\x08\x0b-\x1f\x7f-\x9f]`, keep `\t\n`) so it can't inject terminal escape
    sequences into the getty frame;
  - **normalize newlines** (CR/CRLF/`\v`/`\f` → `\n`) so line-splitting is well-defined, then
    **prefix EVERY line of the value with a fixed line-prefix sentinel `│ `** (applied *after* the
    scrub + newline-normalization). **Trust-region labels are NEVER prefixed.** This is the fix for
    the field-confusion attack (Amendment 2): a `typed_args` body that legitimately contains `\n` and
    injects `\nTIER: T0\nSTATUS: pre-approved` now renders as `│ TIER: T0` / `│ STATUS: pre-approved`
    — visibly value-continuation lines, structurally distinct from the unprefixed fixed template. The
    human can never mistake an injected line for a broker-authored field.
  - **Telegram: NO `parse_mode` at all** — send **raw text** with a **broker/client-authored inline
    keyboard**. Raw text (no Markdown/HTML parser) plus a keyboard the client alone composes makes
    markup-forgery **structurally impossible**; there is nothing to escape because nothing is parsed.
    (getty: the C0/C1 control-strip above suffices — a dumb terminal parses no markup.)
  - **destination URLs render the host as its punycode A-label only** (e.g. `xn--80ak6aa92e.com`,
    never the Unicode look-alike) so an IDN homograph cannot masquerade as a seen host.
  - render every model-controlled value **quoted, line-prefixed (per above), and clearly labeled as
    untrusted input**, and render the **trust-critical broker-derived fields (tier, capability,
    provenance, first-time flag) in a fixed, visually-distinct, NEVER-prefixed region model values
    cannot reach** — so a `destination` of `"x\nTIER: T0"` cannot make the human read a forged tier
    line. Field labels are fixed template literals; values can never introduce a new label.
- Rationale: §7 warns "the model cannot author a single character the human reads as authorization
  context." Scrubbing + fixed-template + region-separation is how we enforce that against a
  model that puts control/markup bytes in its own args.

### 4.2 Per-request nonce echo-verification (§7.2, CF-1b)
- The broker mints the 128-bit nonce and **never lets it reach any channel the model can read**
  (the confirm channel is tty-free; the model's stream is suspended, §4.3). Secrecy-from-the-model
  is what makes replay/guess infeasible; the **echo binds the approval to *this* request.**
- **Domain-separated MACs (anti-reflection, Amendment 3):** the transport MAC covers a **direction
  tag**, so the broker's own signed *request* bytes can never be replayed back as an *approval*. The
  request is MAC'd over `{dir:"req", frame}`; the response over `{dir:"resp", nonce, session_id,
  decision}` with the **decision INSIDE the MAC**. Both sides **constant-time compare**. Without the
  `dir` separation a hop attacker could reflect the request MAC as a valid-looking response; with it,
  a `req`-tagged MAC never verifies as a `resp`.
- **Telegram:** the outbound frame carries **inline-keyboard buttons whose `callback_data` embeds
  the nonce** (`approve:<nonce>` / `deny:<nonce>`). The human taps Approve; the callback returns the
  exact nonce with zero retyping (no confirm-fatigue). The client verifies the returned nonce
  **equals** the outstanding one (**constant-time**); mismatch/absent → deny.
- **Callback authenticity (Telegram does NOT authenticate `callback_data` for you, Amendment 3):**
  the relay daemon **MUST verify `callback_query.from.id == the pinned Dillon user-id`** — not merely
  the `chat_id` — and **MUST correlate the `message_id`** to the outstanding request before returning
  any decision to the client. A tap from any other user in the chat, or a callback that doesn't match
  the outstanding message, is dropped. (The from-id + message-id pin is a build/config value the model
  never sees; see §7.)
- **Getty (Fable ruling Q2 — ACCEPTED at 40 bits):** the frame prints a short **human-verifiable
  confirm code sliced from the broker's nonce** — an **8-char base32 slice (40 bits)** of the 128-bit
  nonce; the human types `approve <code>`. The client **constant-time**-compares against the
  outstanding slice and allows **≤3 retries then denies** (bounding online guessing to ≤3/2⁴⁰ for a
  single outstanding, single-flight request). The code is **sliced from the broker nonce, never
  independently minted** by the client, so it inherits the broker's secrecy-from-the-model. (There is
  no model write-path to tty2, so 40 bits is ample against an off-channel guesser.)
- Replay defense: because the client only ever has **one** outstanding nonce (single-flight) and it
  is discarded on decision/timeout, a replayed old approval carries a stale nonce → mismatch → deny.
  The broker's **session_id re-check** (§2) additionally kills any approval that straddles an epoch.

### 4.3 Model-stream-suspend (§7.3)
- Structurally already true: the broker is **single-flight serial** — while blocked in
  `confirm_seam` it does **not** read the next MCP line, so the model cannot issue, narrate over,
  or flood a second call during a pending confirm. Step 6 must **not** introduce any concurrency
  that breaks this (the client is synchronous: one request in, one decision out).
- The confirm channel is **physically disjoint** from every channel the model can write (tty-free
  relay / tty2). So "suspend" = the model has neither a timeline to race nor a surface to flood.
- The client must **not** echo any part of the model's stream to the human, and must **not** accept
  any input from the model's stdio — its only inputs are the broker's stdin request and the human's
  channel reply.

### 4.4 First-time-destination highlighting (§7.4) — the exfil catch (§10)
- The client maintains a **seen-destinations set** (previously-approved recipients/hosts/paths) in a
  **broker-protected state dir** `/var/lib/agent-os/confirm/` (0700 root, like audit/taint) — **no
  capability impl sandbox may write it** (§7 assertions), so the model cannot pre-seed it to
  suppress the highlight.
- Any `destination` **not** in the set is rendered with a prominent **NEVER-SEEN-DESTINATION**
  banner. This is what surfaces the §10 exfil (`message.send(attacker@x, dump)` to a novel
  recipient). A destination is added to the set **only on an approved** action.
- Normalization before compare (so `HTTP://Host./p` ≡ `http://host/p`, punycode/case/trailing-dot)
  — mirrors the url/recipient validators; a normalization miss is a *highlight* miss (fail-noisy,
  not fail-open), but still worth care.

### 4.5 Payload preview (§7.5)
- The human sees **what actually moves**: for `message.send` → recipient + safe-truncated body; for
  `net.fetch` (post-GAP-1) → method + host + path; for `mem.remember` → key + safe-truncated value.
- Preview is built from `typed_args`, so it is **model-controlled → §4.1 scrubbing applies** before
  display, plus a hard length cap (e.g. 512 bytes) with an explicit `…(truncated)` marker. Preview
  never executes/renders markup.

---

## 5. Follow-up #2 — the subprocess timeout (the one broker edit)

Fable: *"no `timeout=` on the subprocess calls — a hung confirm-seam child stalls the single-flight
broker (never fails OPEN, but it hangs)."* Fix, in `bin/broker`:
- `_run`/`_run_seam` gain a `timeout=` (from a pinned env, e.g. `AGENT_OS_CONFIRM_TIMEOUT_S`,
  default ~120s) on the confirm-seam `subprocess.run`. On `subprocess.TimeoutExpired` → treat as a
  seam failure → **DENY** (audited `confirm-timeout`), kill the child. Never OPEN.
- This is the **only** decision-pipeline change; it is fail-closed and composes with the existing
  `seam-exit-N`/`not-wired` handling. A battery row asserts a sleeping confirm seam → deny within
  the bound + child reaped.
- **Socket-level read timeouts on ALL relay/getty I/O (Amendment 4):** every blocking read the
  client does — the HTTP(S) call to the relay, the long-poll for the callback, the getty line-read —
  carries an explicit **socket read timeout**, not just a wall-clock guard between blocking reads. A
  half-open TCP to the relay would otherwise wedge the client indefinitely (a wall-clock check never
  fires while `recv()` blocks). A socket timeout converts the wedge into a clean deny.
- The **client** also carries its **own human-decision-window** timeout and returns
  `{"approved":false,"reason":"confirm-human-timeout"}` cleanly when it elapses.
- **Ordering constraint (must hold, Amendment 4):**
  `client_human_window  <  AGENT_OS_CONFIRM_TIMEOUT_S − one_relay_round_trip`. The client must give
  up and emit a graceful deny strictly *before* the broker's subprocess backstop could fire, leaving
  room for one relay round-trip to carry that deny back. This makes **graceful deny the normal
  no-answer path** and the broker's `SIGKILL`-the-child backstop the **exceptional** path (a truly
  wedged client), not the routine one. Socket timeouts on every leg are what let the client honor
  this bound.

---

## 6. CF-1c — reset-as-confirm-nonce (SPEC'D HERE, shipped as an immediate FOLLOW-UP PR)

**Fable ruling Q3: CF-1c does NOT land in this Step-6 PR.** It touches `bin/taint` (a separate
security surface) and it introduces a **bootstrap inversion** — if `taint reset` *depends on* the
confirm channel, then a **dead channel makes taint un-resettable** (you could not clear the bit to
recover). So it ships as its own reviewed follow-up once the channel has demonstrably lived, and the
self-asserted flag stays available as break-glass in the meantime.

Today taint reset is a human-only, audited `taint reset --confirm-human` flag (CF-1a already makes
reset unreachable from any tool-call by construction — it is not a registry key). CF-1c will harden
the *human* path so the intent to clear taint is itself a nonce-bound, integrity-framed human
approval rather than a self-asserted flag.

**Sequencing (three PRs; this spec fixes the shape now):**
1. **This PR (Step 6):** ship the channel. `--confirm-human` is unchanged.
2. **Follow-up PR (immediately after):** add CF-1c — `taint reset` (still T3, still not
   model-reachable) mints a broker-style nonce and routes a **reset confirm frame**
   (`capability="taint.reset"`, tier shown, a prominent *"this clears the untrusted-session bit"*
   banner) through the confirm client; only a nonce-echoed approval performs the reset.
   **`--confirm-human` is RETAINED as an audited break-glass behind a separate operator gate** (so a
   dead channel can never strand taint) for **one burn-in period**.
3. **3rd trivial PR (after the channel has lived a burn-in):** retire the `--confirm-human`
   break-glass, leaving the confirm-nonce path as the sole human reset.

---

## 7. Nix module + sandbox assertions (`modules/confirm.nix`)

- Installs `bin/confirm` on PATH; pins `AGENT_OS_CONFIRM_SEAM` to the store-path client in the
  broker's env (same discipline as `TAINT_BIN`/`AUDIT_BIN`); pins `AGENT_OS_CONFIRM_CHANNELS`,
  `AGENT_OS_CONFIRM_TIMEOUT_S`, the relay endpoint (single addr+port), the broker↔relay secret, and
  the **pinned Dillon Telegram user-id** (§4.2) **as T3 config the model never sees** (a
  protected-read path per Step-1 `protectedReadPaths`).
- Provisions `/var/lib/agent-os/confirm/` **0700 root** (tmpfiles), the seen-destinations store.
- **Sandbox assertions (build-fails-on-violation, extending Step-1 mechanism-3):**
  - the confirm client's writable scope may include **only** `/var/lib/agent-os/confirm/`; it may
    **not** write broker/registry/audit/taint/weights/trusted-mem (reuse `pathConflicts`).
  - the confirm client's **egress allowance is exactly the pinned relay endpoint** (Telegram path)
    and nothing else — it is **not** a capability impl, so it is not bound by the T2-only-network
    assertion, but it **is** bound by a new *confirm-client-egress-is-single-pinned-endpoint*
    assertion (**addr + port, NO fallback list** — Fable ruling Q1) so it can't become a general
    exfil channel. This is the §3.1 INV-2 exemption made into a build-checked, narrowly-scoped
    allowance.
  - the second-getty service is broker-owned and bound to a console that is **never** the model's
    login tty (assert tty index ≠ the agent-shell autologin tty).

---

## 8. Test plan (`tests/confirm-battery.sh` + flake check `confirm-channel`)

Property tests (each hostile input = exactly one fail-closed outcome):
1. **Seam contract:** valid request → client prints well-formed `{approved,reason}`; malformed
   request on stdin → deny, no crash.
2. **Fail-closed channels:** relay unreachable → deny; no channel live → deny; getty absent → deny.
3. **Nonce echo + MAC auth:** approval with the correct nonce → approve; wrong/absent/replayed
   nonce → deny; stale nonce from a prior request → deny. **Reflection (Amendment 3):** the broker's
   own `dir:"req"` MAC replayed as a response → deny (domain separation). **Callback authenticity:** a
   callback whose `from.id` ≠ pinned Dillon user-id, or whose `message_id` doesn't correlate → deny.
4. **Timeout:** human-window elapses → clean `confirm-human-timeout` deny, emitted strictly *before*
   the broker backstop (ordering constraint §5). **Half-open TCP** to the relay → the client's socket
   read timeout fires → clean deny (never an indefinite wedge). **Broker-side:** a sleeping confirm
   seam → broker denies within `AGENT_OS_CONFIRM_TIMEOUT_S`, child reaped (this leg lives in
   `broker-battery.sh` since it's a broker edit).
5. **Field-confusion / injection (Amendment 2):** `destination`/`typed_args` containing C0/C1
   escapes, Telegram markdown, and a fake `\nTIER: T0\nSTATUS: pre-approved` line → rendered frame is
   scrubbed, newline-normalized, and **every value line carries the `│ ` sentinel** while the trust
   region is unprefixed and intact; the Telegram frame is sent with **no `parse_mode`**; an IDN host
   renders as its punycode A-label. Assert on the exact rendered bytes.
6. **First-time-destination:** novel destination → highlighted; previously-approved → not; the
   seen-set is refused writes from a non-root/impl identity (mirrors audit/taint dir tests).
7. **Payload preview:** truncation at the cap + `…(truncated)`; control-chars stripped; no markup
   execution.
8. **Single-flight:** the client accepts exactly one outstanding confirm; a second concurrent
   request while one is pending is rejected (should not occur given broker single-flight, but assert
   the client doesn't multiplex).
9. **Integration:** drive the **real broker** with `AGENT_OS_CONFIRM_SEAM=bin/confirm` + a scripted
   channel stub → a T1 `mem.remember` and a T2 `message.send` each round-trip route→confirm→approve
   →invoke(stub) and route→confirm→deny→withhold; audit chain still verifies and contains
   route/approve/deny with the nonce.

All green under nix 2.35.1 (`nix build .#checks.x86_64-linux.confirm-channel`); `audit-log`,
`taint-shadow`, `mcp-conformance`, `broker-core` must all still pass (no regression); toplevel +
VM realize.

## 9. Bundled cheap follow-up (from the merge review)

Fable follow-up #3 (non-dict `arguments` → `AttributeError`): since the Step-6 code PR edits
`bin/broker` (the timeout), bundle the `isinstance(arguments, dict)` deny + battery row here. (The
GAP-1 url-`127.1` fix is **not** here — it rides GAP-1's own PR, as its hard prerequisite.)

## 10. Fable spec-review rulings (2026-07-28) — all resolved, folded above

1. **TG-relay INV-2 exemption — YES.** Categorical TCB control channel; the single-pinned-endpoint
   build assertion covers **addr+port with no fallback list** (§3.1, §7). Reinforced by Amendment 1
   (deterministic relay, no LLM).
2. **Getty nonce echo — ACCEPTED at 40 bits.** 8-char base32 slice of the broker nonce, constant-time
   compare, **≤3-retry-then-deny**, code sliced from the broker nonce (never independently minted).
   No model write-path to tty2 (§4.2).
3. **CF-1c — IMMEDIATE FOLLOW-UP PR, not this one** (§6). `bin/taint` is a separate surface + a
   bootstrap inversion (reset depending on the channel would make a dead channel un-resettable);
   `--confirm-human` kept as audited break-glass for one burn-in, retired in a 3rd trivial PR.
4. **Timeouts — reasonable**, plus the socket-level read timeouts and the ordering constraint of
   Amendment 4 (§5).
5. **broker↔relay auth — HMAC is enough for v1** with the §2/§4 domain-separation + callback-authn
   fixes (Amendment 3). HMAC-SHA256 over the canonical, domain-separated frame; the secret is T3,
   model-invisible.

---

## 11. Build order

1. **This spec → PR → Fable(code) → merge** (prose anchor + interface lock, like Step 5's spec).
2. Then **code PR:** `bin/confirm` (deterministic frame composer, per Amendment 1) +
   `modules/confirm.nix` + `tests/confirm-battery.sh` + flake check + the `bin/broker` timeout edit +
   the bundled non-dict-args deny → PR → Fable(code) → merge. Will NOT self-merge either.
3. **CF-1c follow-up PR** (immediately after the channel lands): reset-as-confirm-nonce in
   `bin/taint`; `--confirm-human` retained as audited break-glass (§6).
4. **3rd trivial PR** (after a burn-in period): retire the `--confirm-human` break-glass.
5. Then **Step 7** (sandboxed impls) replaces the invoke stub; **Step 8** end-to-end acceptance
   (exfil-deny, cross-session taint-laundering deny, loopback-egress deny, nonce-replay deny,
   build-fails-on-invariant). GAP-1/2/4/5 additive.

— Augur (DVo, 2026-07-28)
