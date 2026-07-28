# Phase 2 · Step 5 — The broker core ("the wall"): design spec

Status: **SPEC — for Fable(code) review before any broker code is written.** Its own
branch → PR → Fable; the broker implementation is a *separate* later branch. This doc
consolidates every carry-forward accrued through Steps 1–4 and fixes the interfaces the
broker wires so the implementation review has nothing to re-litigate.

Anchor: `docs/phase2-threat-model.md` §5 (tiers), §6 (taint + DATA fence), §7 (confirm),
§8 (build-enforced invariants / no-log→no-execute), §9 (MCP surface), §12 step 5.

---

## 1. What Step 5 is — and the one-sitting decomposition decision

The broker is the **one trusted decision point**. Everything upstream is untrusted (the
model's bytes) or a pure primitive (Steps 1–4); everything an action actually *does* is
downstream (Steps 6–7). The broker is the pipeline in between: it turns a **validated MCP
verdict** into exactly one of `{DENY, ALLOW-AUTO, REQUIRE-CONFIRM}`, audits that decision,
and only then touches an effect seam. It is the §12 "one-sitting-bar" file.

**Decomposition decision (Rabbot asked me to make this call before writing):** the broker
core ships with its two effect points as **narrow, injected seams that default to
fail-closed stubs** — it does NOT contain the confirm channel (Step 6) or the capability
impls (Step 7). Concretely:

- `confirm_seam(request) -> APPROVE | DENY` — Step-5 default stub returns **DENY**
  ("confirm channel not wired"). Step 6 replaces it with the Telegram/second-getty client.
- `invoke_seam(cap, args) -> result` — Step-5 default stub returns **not-implemented**
  (a fail-closed error, never a fabricated success). Step 7 replaces it with the sandboxed
  impls.

This keeps Step 5 to *decision + audit + dispatch*, fully testable now, with two well-typed
seams. If review finds even this too large, the next cut is to lift the **arg-schema
validator** (§4.3) into `bin/broker-argcheck` as its own reviewed unit; the pipeline stays.
Nothing else in the pipeline is separable without splitting a fail-closed decision across
a trust boundary, which would *add* surface. I do not think that further cut is needed —
flagging it as the pre-identified relief valve.

**Step 5 executes nothing on its own** (impls are Step 7, confirm is Step 6). Its testable
surface is the *routing decision + the audit trail + the fail-closed behavior of both
seams*. That is the whole point: prove the wall's logic in isolation before wiring effects.

---

## 2. Inputs, outputs, trust boundary

```
 model (UNTRUSTED)                      broker core (TCB)                    effects
 ─────────────────      ┌───────────────────────────────────────────┐   ┌──────────────┐
  JSON-RPC bytes ─▶ bin/mcp parse ─▶ verdict ─▶ [decision pipeline] ─▶│──▶ confirm_seam │ (Step 6)
                    (Step 4)          (dict)         │    every        │   └──────────────┘
                                                     │    junction ─▶ audit append (Step 2)
                                                     │    fail-closed  │──▶ invoke_seam  │ (Step 7)
                                                     ▼                 │   └──────────────┘
                                          registry (Step 1) · taint (Step 3)
```

- **Input:** the broker consumes the **normalized verdict object** emitted by `bin/mcp
  parse` — one dict per line — and **never re-parses the raw wire** (carry-forward CF-6).
  A verdict with `ok:false` is a **hard deny**: audit it, return the parser's error to the
  model, and **never fall through to the raw bytes** for a "second opinion." One parser,
  one meaning (this is exactly why Step 4's verdict is provably standard-JSON).
- Wiring: in production the broker runs `bin/mcp parse` as a child over the stdio stream
  and reads verdict lines; test harnesses feed verdict lines directly. Either way the
  broker's input contract is *the verdict dict*, not bytes.
- **Output to the model:** a typed MCP result or a typed error — always structured, never
  free bytes that could be re-read as instructions (§4.9 DATA fence).

Verdict shapes the broker must handle (from `bin/mcp`, verbatim):
- `initialize`  → `{ok:true, method:"initialize", id, protocolVersion}` — handshake only.
- `tools/list`  → `{ok:true, method:"tools/list", id, cursor}` — enumerate registry (T0).
- `tools/call`  → `{ok:true, method:"tools/call", id, name, arguments}` — the real path.
- deny          → `{ok:false, id, error:{code,message}}` — pass through, audit, stop.

Only `tools/call` enters the authorization pipeline. `initialize`/`tools/list` are
broker-internal metadata (no tier, no taint gate, no impl) but are still audited.

---

## 3. The decision pipeline (order is load-bearing; fail-closed at every step)

For a `tools/call` verdict, in this exact order — the first failing stage denies:

1. **Consume verdict** — reject `ok:false` (CF-6); accept only the three known methods.
2. **Registry lookup** — `cap = registry[name]` or **deny `unknown-capability`**. Because
   the registry (Step 1) can only contain T0/T1/T2 keys and the assert forbids any T3 name,
   an incoming `name` of `reset`, `taint.modify`, `registry.modify`, … is simply **not a
   key** → denied here. **T3 non-expressibility is automatic and needs no special case**
   (CF-1a): the broker only ever dispatches registry keys, and reset is not one.
3. **Arg-schema check** (§4.3) — every provided arg validates against the cap's declared
   type; unknown/missing args deny (CF-7).
4. **Tier classify** — `tier = cap.tier` (pure registry read; the model cannot set it).
5. **Taint consult** (§4.4) — read the **structured** tainted bit (CF-2), and *in parallel*
   fire the shadow `taint gate <tier>` for the evidence log. The live decision uses the bit,
   **never the shadow exit code.**
6. **Authorize** (§4.5) — map `(tier, tainted)` → `ALLOW-AUTO | REQUIRE-CONFIRM | DENY`.
7. **Audit the decision** (§4.8) — `audit append` BEFORE any effect. If the log write
   fails, **deny** (no-log → no-execute).
8. **Effect** — `ALLOW-AUTO` → `invoke_seam`; `REQUIRE-CONFIRM` → `confirm_seam`, and only
   on `APPROVE` → `invoke_seam`. Any seam failure/denial → deny, audited.
9. **Return** — wrap the result/error in the typed envelope (§4.9); taint-set on untrusted
   ingest.

Every `deny` is `{ok:false, error:{code,message}}` back to the model and an audit line.
Unknown-anything, uncertain-anything → deny. The safe default is "do nothing."

---

## 4. Per-stage detail

### 4.1 Verdict consumption (CF-6)
- Accept only `method ∈ {initialize, tools/list, tools/call}` with `ok:true`; anything
  else denies. `ok:false` is passed through as the model's error and audited — the broker
  does not re-examine, re-parse, or "recover" it.
- The broker holds **no parser of its own.** If it ever needs a field the verdict doesn't
  carry, that is a Step-4 change (new reviewed parser field), not an ad-hoc broker parse.

### 4.2 Registry lookup & tier
- `registry` and `capabilityNames` come from `modules/capability-registry.nix` **after**
  `assert ok` — the broker consumes the already-invariant-checked data (materialized to a
  read-only JSON the broker reads from a Step-1 **protected path**; the model can't write
  it). Tier is `cap.tier`, one of `T0|T1|T2`.
- Unknown name → `deny(unknown-capability)`. This is the whole of T3 enforcement (CF-1a).

### 4.3 Arg-schema validation (CF-7)
The parser guarantees `arguments` is a structurally-valid, capped, dup-free,
control-scrubbed object. The broker checks **semantics** against `cap.args` (a
`{argname: type}` map, types ∈ `string|path|namespace|url|recipient|bytes|enum`):

- **Exact key match:** every key in `arguments` must be declared in `cap.args`; every
  declared arg is required unless the registry marks it optional (registry has no optional
  marker today → **all declared args required** in v1; see GAP-2). Unknown or missing → deny.
- **Per-type value validators** (all fail-closed; a validator that can't decide denies):
  - `string` — `str`, length ≤ the parser's per-string cap (already enforced); no further.
  - `bytes`  — `str`, byte-length ≤ a broker payload cap (declare `MAX_ARG_BYTES`).
  - `enum`   — `str` ∈ the cap's declared allowed set — **which the registry does not yet
    carry** (GAP-1). Until GAP-1 lands, an `enum` arg **denies** (fail-closed), so `net.fetch`
    `method` cannot be exercised — acceptable in v1 (net.fetch is T2/confirm-gated anyway).
  - `path`   — `str`, must be **canonical-absolute** (reuse the registry's `pathIsCanonical`
    rule: leading `/`, no empty/`.`/`..` segment, no trailing `/`) **and confined** under
    one of the cap's own `sandbox.readWritePaths`/`readOnlyPaths` roots. This is
    defense-in-depth over the Step-7 systemd sandbox — the broker refuses an out-of-root
    path before the impl ever runs. Symlink-escape is still Step-7's OS job (noted in §6).
  - `namespace` — `str` matching `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` and confined to the
    cap's allowlisted mem roots; no `/`, no `..`.
  - `recipient` — `str` matching a conservative recipient charset (no control, no
    whitespace, length-bounded); the **value** is never trusted for authorization — it is a
    T2 "destination" surfaced verbatim in the confirm frame (§7 of the model, first-time
    highlight). The broker does not resolve/act on it at this stage.
  - `url` — parse with `urllib.parse`; require `scheme ∈ {http,https}` and a non-empty host.
    **INV-2 belt-and-suspenders:** if the host is an IP literal in the egress deny-list
    (loopback/RFC1918/CGNAT/link-local/ULA), **deny at the broker** rather than relying only
    on the Step-7 `IPAddressDeny`. A hostname that *resolves* to a denied range is caught by
    the systemd layer at fetch time (the broker does not do DNS in v1 — flagged, not hidden).
- All validators are total and fail-closed: unknown type → deny (should be impossible; the
  registry assert already restricts types, but the broker does not assume it).

### 4.4 Taint consult (CF-2) — structured bit, shadow gate stays parallel
- The **live** input is the structured tainted bit, obtained from **one** owner of the
  fail-closed read semantics: `taint` (Step 3). The broker calls a **new additive**
  `taint status --json` → `{"tainted":bool,"session_id":int}` (GAP-3; additive taint PR,
  its own branch→PR→Fable). The broker MUST NOT parse taint's human `status` line, MUST NOT
  reimplement `load_session` (avoids a two-reader differential), and MUST NOT use `taint
  gate`'s **exit code** (always 0 in shadow) as a signal.
- The broker STILL invokes `taint gate <tier>` (shadow) so the evidence log keeps
  accruing the `would_auto_authorize` record — but purely for evidence, discarding its exit
  status. Live and shadow paths are separate by construction.
- Fail-closed: if `taint status --json` errors, is unreachable, or reports corrupt/uninit →
  **treat as tainted** (the taint tool already returns `tainted:true` for missing/corrupt;
  the broker treats any consult failure the same way).

### 4.5 Authorization decision (v1 policy — §5 tiers, §6 shadow)
| tier | clean session | tainted session |
|---|---|---|
| **T0** | ALLOW-AUTO | ALLOW-AUTO (T0 can't move bytes off-box) |
| **T1** | REQUIRE-CONFIRM | REQUIRE-CONFIRM |
| **T2** | REQUIRE-CONFIRM | REQUIRE-CONFIRM |

v1 confirms **everything above T0** regardless of provenance (§5: T1-auto-on-trusted is
deferred until the shadow taint evidence earns it). So taint does not change the *live*
routing in v1 — it is logged (shadow) and it **will** change routing in v2. The broker
still consults it now so the wiring, the fail-closed path, and the evidence are all real.
T2 is confirm **regardless of provenance, always** — never auto, ever, even clean.

### 4.6 Confirm seam (→ Step 6; Step-5 stub = DENY)
- Interface: `confirm_seam(confirm_request) -> {approved: bool, reason: str}` where
  `confirm_request = {capability, tier, typed_args, provenance: TRUSTED|TAINTED,
  destination, nonce}` — **structured fields only, no model free text** (§7.1). The broker
  builds this record; Step 6 renders/relays it.
- Step-5 default stub returns `{approved:false, reason:"confirm-channel-not-wired"}` →
  every >T0 request denies. This is correct v1 fail-closed behavior until Step 6 lands.
- **Nonce (CF-1b):** the broker mints a per-request nonce the model can't know; approval
  must echo it; replay/guess → deny. In Step 5 the broker *mints and records* the nonce
  (so the audit trail and the interface are real); Step 6 owns the echo-verification and
  the channel integrity. Reset-of-taint should eventually be a **confirm-nonce action, not
  a self-asserted `--confirm-human` flag** (CF-1c) — a Step-6 follow-up, flagged not built.

### 4.7 Invoke seam (→ Step 7; Step-5 stub = not-implemented)
- Interface: `invoke_seam(cap, validated_args) -> {ok:bool, content, origin:
  TRUSTED|UNTRUSTED, meta}`. `origin` tells the broker whether the *result* is untrusted
  (a `net.fetch` page is UNTRUSTED; a `file.read` of a safe path is TRUSTED) — this drives
  §4.9.
- Step-5 default stub returns `{ok:false, reason:"impl-not-wired"}` — a fail-closed error,
  **never a fabricated success**. Step 7 supplies the real sandboxed impls whose sandboxes
  are *derived from the same registry declarations* the broker validated against.

### 4.8 Audit at every junction (no-log → no-execute, §8)
- The broker audits via `audit append` (Step 2): stdin = one JSON record, exit 0 = logged,
  **non-zero = fail-closed deny**. Records: `{src:"broker", event, id, capability, tier,
  provenance, decision, nonce?, reason?}`; the broker uses the same canonical serialization
  discipline as `bin/audit`/`bin/mcp`.
- Ordering rule: **audit the decision BEFORE the effect.** ALLOW-AUTO audits then invokes;
  REQUIRE-CONFIRM audits the routing, then (on approve) audits the approval, then invokes.
  An executed or routed action is therefore always already on the log. If any of these
  `audit append` calls fails, the broker denies and does not proceed to the effect.

### 4.9 The DATA-channel fence on the return path (CF-8, §6)
The one bypass the model itself can attempt is **laundering data into instructions**. The
broker owns the separation:
- Every capability **result** returns to the model as a typed MCP result with an explicit
  **`content_type: "data"`** envelope — never concatenated into the instruction stream,
  never returned as bare text the model could read as a new directive.
- **Untrusted ingest sets taint.** If `invoke_seam` reports `origin: UNTRUSTED` (e.g. a
  `net.fetch` body, or a `mem.recall` the taint tracker re-taints), the broker calls
  `taint set "<why>"` so the monotonic bit rises for the rest of the session. Any
  subsequent `mem.remember` of that content is therefore stamped **UNTRUSTED-origin** by
  the taint tool (storage-persistent, §6) — closing cross-session laundering.
- `mem.recall` (T0) routes through `taint recall <key>` so an UNTRUSTED-origin entry
  re-taints the session on read (already Step-3 behavior; the broker must call it, not skip
  it because the tier is T0). T0 "runs even under taint" refers to *authorization*, not to
  *skipping the provenance side effects*.

---

## 5. Consolidated carry-forward disposition (all 8 + gaps)

| # | carry-forward | source | owner | disposition in Step 5 |
|---|---|---|---|---|
| CF-1a | reset unreachable from any tool-call (T3) | Step 3 | broker | **Done by construction** — reset is not a registry key → unknown-capability deny (§4.2). |
| CF-1b | reset/confirm needs a broker-minted nonce, not a self-asserted flag | Step 3 | broker+Step6 | Broker mints+records nonce now (§4.6); echo-verify is Step 6. |
| CF-1c | reset should demand a Step-6 confirm-nonce eventually | Step 3 | Step 6 | Flagged as Step-6 follow-up; v1 keeps `--confirm-human` (human-only, audited). |
| CF-2 | consume structured `would_auto_authorize`, never shadow exit code | Step 3 | broker | §4.4 — live bit via `taint status --json`; shadow gate parallel, exit ignored. |
| CF-3 | boot `--mem-root` mandatory | Step 3 | boot wiring | Recorded: the boot path (agent-shell) must pass `--mem-root`; broker asserts a boot-taint consult ran. Boot-wiring PR, flagged. |
| CF-4 | origin tags → content-hash + atomic stamp-with-write | Step 3 | taint-additive | GAP-4: name-keyed tags let a rewritten-same-key entry keep a stale tag; add content-hash defense-in-depth. Additive taint PR, not Step 5. |
| CF-5 | corrupt-session reset reuses session_id=0 → bump-from-max-seen or refuse | Step 3 | taint-additive | GAP-5: `cmd_reset` over a corrupt/uninit session sets id 0, colliding with a prior epoch. Bump from max-seen or refuse. Additive taint PR. |
| CF-6 | consume normalized verdict only; ok:false = deny, no byte fall-through | Step 4 | broker | §2, §4.1 — core input contract. |
| CF-7 | registry arg-schema check per tool | Step 4 | broker | §4.3 — full per-type validator set. |
| CF-8 | DATA-channel fence for untrusted content | Step 4 | broker | §4.9 — typed data envelope + taint-set-on-untrusted-ingest. |

### New gaps found this pass (each its own additive PR, flagged not silently absorbed)
- **GAP-1 — registry enum/allowed-values.** `net.fetch.method` is typed `enum` but the
  registry declares no member set, so the broker cannot validate it. Until fixed, `enum`
  args deny (fail-closed, §4.3). Fix = additive Step-1 registry field (`argEnums`), own PR.
- **GAP-2 — required-vs-optional args.** The registry has no optional-arg marker; v1 treats
  all declared args as required. If any real cap needs an optional arg, add the marker
  (additive Step-1), don't loosen the broker.
- **GAP-3 — `taint status --json`.** Additive structured-output mode on Step-3's `taint`
  so the broker consults a structured bit, not a parsed human line (CF-2). Own branch→PR→Fable.
- **GAP-4 / GAP-5** — the two taint hardenings above (CF-4/CF-5).

**Prerequisite ordering:** GAP-3 (taint `--json`) is the only hard prerequisite for the
broker to *function*; GAP-1/2/4/5 can land in parallel or after, since their absence is
handled fail-closed. The broker PR should depend on the GAP-3 PR merging first.

---

## 6. Fail-closed junction table (the review checklist)

| junction | uncertain / failure → |
|---|---|
| verdict `ok:false` | pass error through, audit, stop — no byte re-parse |
| unknown capability name | deny `unknown-capability` (this is T3 enforcement) |
| arg unknown/missing/ill-typed | deny `arg-schema` |
| `enum` arg pre-GAP-1 | deny (no member set to check against) |
| `path`/`namespace` outside cap root | deny `arg-confinement` |
| `url` host in INV-2 deny-list | deny `egress-denied` |
| taint consult errors/corrupt/uninit | treat as **tainted** |
| audit append non-zero | deny (no-log→no-execute) |
| confirm seam down (Step-5 stub) | deny (`confirm-channel-not-wired`) |
| confirm nonce mismatch/replay | deny |
| invoke seam not wired (Step-5 stub) | deny (`impl-not-wired`), never fake success |
| impl reports UNTRUSTED result | `taint set`; return under `data` envelope |

Symlink-escape of a validated `path` is **out of broker scope** — it is a Step-7 OS
obligation (`ProtectSystem`, protected paths as `InaccessiblePaths`). The broker's path
check is textual-confinement defense-in-depth, stated as such so review doesn't mistake it
for the whole guard.

---

## 7. Test plan — `tests/broker-battery.sh` (ships with the broker code, not this doc)
Property + hostile battery, same bar as Steps 1–4, driven by a flake `broker-core` check:
- **Routing matrix:** every (tier × clean/tainted) cell → the §4.5 decision, asserted.
- **T3 non-expressibility:** `tools/call name=reset|taint.modify|registry.modify|…` → deny
  `unknown-capability` (never routed, never confirmed).
- **Arg-schema:** unknown key, missing key, wrong type per arg-type; `path`/`namespace`
  escape (`..`, non-canonical, out-of-root) → deny; INV-2 `url` literal (127.0.0.1, 10./
  172.16./192.168./100.64./169.254./::1/fc00::/fe80::) → deny; `enum` pre-GAP-1 → deny.
- **Verdict passthrough:** a `bin/mcp` deny line (each error code) → broker denies, audits,
  does not re-parse.
- **no-log→no-execute:** point `AUDIT_BIN` at a failing stub → every route/effect denies
  and the seam is never called (assert seam-not-invoked, mirroring taint's Step-3 test).
- **Fail-closed seams:** confirm stub → all >T0 deny; invoke stub → T0 returns
  `impl-not-wired`, never a fabricated success.
- **DATA fence:** an UNTRUSTED invoke result → broker emits `taint set` (assert the audit
  event) and wraps the return under `content_type:"data"`.
- **Taint consult:** with `taint status --json` reporting tainted/clean/corrupt →
  correct live decision; corrupt/unreachable → tainted; shadow `gate` exit ignored.
- **Golden verdicts** for the routing decisions (canonical serialization, deterministic).

Green under `nix build .#checks.x86_64-linux.broker-core`, plus no regression on
`capability-registry` / `audit-log` / `taint-shadow` / `mcp-conformance`, and `toplevel`
realizes with the new `modules/broker.nix`.

---

## 8. Explicitly deferred (owned by later steps — not scope creep into Step 5)
- **Step 6:** the real confirm channel + integrity frame (Telegram/second-getty), nonce
  echo-verification, stream-suspend, first-time-destination highlight, payload preview;
  reset-as-confirm-nonce (CF-1c).
- **Step 7:** the sandboxed capability impls; systemd sandbox derived from the registry;
  symlink-escape OS guard; DNS-time egress enforcement.
- **Step 8:** the §10 exfil-deny end-to-end, taint-laundering-across-sessions deny,
  loopback-egress deny, confirm-nonce-replay deny, build-fails-on-invariant-violation.
- **v2:** T1-auto-on-trusted (once the shadow evidence earns it); peer exposure; disclosure
  classification.

---

## 9. Summary for review
The broker is a **fail-closed decision pipeline** with two stubbed effect seams. It consumes
only Step-4 verdicts, classifies against the Step-1 registry (T3 unexpressible for free),
validates args by type, consults the Step-3 taint bit through one structured owner, confirms
everything above T0 in v1, audits before every effect (no-log→no-execute), and fences
untrusted results into a DATA channel that re-taints the session. All eight carry-forwards
are dispositioned above; five small additive PRs (GAP-1..5, of which only GAP-3 is a hard
prerequisite) are flagged rather than folded silently. The one-sitting bar is met by keeping
confirm (Step 6) and impls (Step 7) behind narrow seams, with the arg-checker as the
pre-identified further cut if review wants it.
