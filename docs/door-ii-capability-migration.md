# Door (ii) — routing the brain's three hands through the broker wall

Status: **DRAFT, for Geist's gate.** No code lands from this document. Each capability
below becomes its own PR on the security surface (branch → PR → Fable → merge, never
direct-push), in the order given.

Ruling this implements: geist → mirror, 2026-09-03T00:09:47Z ("door (ii) YES, amended")
and the follow-on ruling 3, 2026-09-03T00:19:17Z (nix-ld stays OFF on OPEN).

---

## 0. The shape, and what is already built

The amendment that matters: **three capability registrations, not one uid slice.** The three
tools need three different confinements, and the tree already knows how to say confinement
per-capability. What they share is the seam, and every part of it is merged today:

```
model bytes ──▶ bin/mcp (normalize) ──▶ bin/broker  ── verdict ─┬─ DENY
                (untrusted)              THE wall               ├─ ALLOW-AUTO
                                                                └─ REQUIRE-CONFIRM ─▶ confirm seam
                                            │
                                   audit BEFORE effect
                                            │
                                            ▼
                                     bin/cap-invoke  (AGENT_OS_INVOKE_SEAM)
                                     registry: capability → impl NAME → impl BINARY
                                     strictly under AGENT_OS_CAP_BIN_DIR, never PATH
                                            │
                                            ▼
                                  modules/cap-sandbox.nix
                                  transient unit whose ReadWritePaths / PrivateNetwork /
                                  IPAddressDeny are DERIVED from the registry `sandbox` decl
```

So a "migration" is not new machinery. Per capability it is exactly four declarations plus
one impl binary:

| what | where |
|---|---|
| tier + args + summary | `modules/capability-registry.nix`, via `mkCap` |
| verdict class | derived from tier (T0 auto / T1 auto+checkpoint / T2 always-confirm) |
| confinement | the same entry's `sandbox = { … }` |
| impl binary | `bin/cap-<name>`, resolved by `bin/cap-invoke` |

`defaultSandbox` is closed — `network = false`, no writable paths — so a capability gets
nothing it does not name. `cap-sandbox.nix:157` gives `network = false` a hard
`PrivateNetwork=yes` + `IPAddressDeny=any`, and registry asserts already refuse a wrong-typed
field, a protected path in a writable scope, and a T2 network impl that fails to carry the
full egress deny-list.

**Retirement note carried through all three pages:** `(i)` — `_SummonConsent` in
`modules/agent-brain.py:213-241` — is the INTERIM seam. It retires in the summon PR, not
before, and not separately.

---

## 1. `fetch_web` → `net.fetch` (T2) — first slice

**This is not a new capability. It is a rebinding.** `net.fetch` is already registered:

```nix
"net.fetch" = mkCap {
  tier = "T2"; impl = "cap-net-fetch";
  args = { url = "url"; method = "enum"; };
  sandbox = { network = true; egressDeny = egressDenyList; };
};
```

Network-yes / filesystem-no, from a declaration — the open half of GATE #5 in `broker.nix`.
The brain today does `_run_agos("agos-web","fetch",url)` (`agent-brain.py:1188`): an
in-process subprocess in the brain's own uid, with no wall, no audit line, and no taint
stamp on the bytes it brings back.

**Change:** the `fetch_web` tool dispatches a `tools/call` for `net.fetch` through `bin/mcp`
instead of shelling out. Nothing in the registry changes.

**Verdict class:** T2 → REQUIRE-CONFIRM, provenance-independent. That is the honest class for
an outward-facing fetch and it is already what the registry says.

**Why first:** it is the slice the design already owes — the capability exists, the
confinement exists, the impl name is reserved. It exercises the whole seam end-to-end
(verdict → confirm → cap-invoke → sandbox → taint-on-return) with zero new policy. If the
seam has a defect, it surfaces here, on the capability where a wrong answer is cheapest.

**Anti-laundering, and it is the real prize:** the broker commits the taint effect covering a
result BEFORE the result's bytes are released. Fetched web bytes are the canonical untrusted
input, and today they enter the brain unstamped.

**Open:** whether `agos-web fetch` becomes `cap-net-fetch` directly or the impl wraps it.
Wrapping is smaller; direct is honest about what runs in the sandbox. Leaning direct.

---

## 2. `run_command` → `sys.run` (new, T2) — confinement, not verdicts

Today (`agent-brain.py:1153-1157`):

```python
o = subprocess.run(["bash","-c",args.get("command","")], capture_output=True, text=True, timeout=30)
```

`bash -c <arbitrary>` in the brain's own uid, 30s timeout, no wall. `SYS_BASE` tells the model
*"You HAVE HANDS"* and the same prompt tells it to read web pages. **This is the live wire door
(ii) actually cuts:** prompt-injection → arbitrary exec, on the operator's uid.

**The ruling's core point, adopted verbatim: do not try to classify arbitrary shell.** A broker
verdict on `bash -c <anything>` can only honestly be REQUIRE-CONFIRM-always, and a
confirm-every-command box is a different product. So:

- **Verdict:** ALLOW-AUTO. The broker's contribution here is the **audit line**, not a gate.
- **Confinement:** the exec happens in a transient `cap-sandbox` unit derived from the registry
  decl. Blast radius by declaration.
- **Operator friction added:** zero.
- **Widening the sandbox is a PR-gated registry change** — which is exactly where that decision
  belongs, and is the whole benefit of the move.

Proposed opening decl, deliberately tight — the first PR should be *too narrow* and widen on
evidence, never the reverse:

```nix
"sys.run" = mkCap {
  tier = "T2"; impl = "cap-sys-run";
  summary = "Run a shell command inside the declared sandbox.";
  args = { command = "string"; };
  sandbox = { readWritePaths = [ "/var/lib/agent-os/workspace" ]; network = false; };
};
```

`network = false` yields `PrivateNetwork=yes` + `IPAddressDeny=any` from
`cap-sandbox.nix:158`. Note what that costs: `run_command curl …` stops working. That is the
point, and it is also the thing most likely to be wrong in practice — flagged as the primary
question for the gate.

**Tier honesty, open question for Geist:** T2 is defined as *"irreversible / outward-facing,
ALWAYS human-confirm"*. Registering `sys.run` as T2 while asking for ALLOW-AUTO contradicts
the tier's own definition. Two options, and I do not think this is mine to pick:
1. a new tier for *"audited, confined, not confirmed"*; or
2. T1 with the checkpoint scope derived from `readWritePaths` (the mechanism T1 already has).

**(2) looks right** — T1's checkpoint is precisely "reversible within a declared scope", which
is what a sandboxed exec is. But it widens what T1 means, so it is a ruling, not a preference.

---

## 3. `summon_claude` → `cloud.summon` (T2) — and (i) retires here

Today: `_summon_claude` (`agent-brain.py:1192+`) calls `ok_to_summon()`, then
`subprocess.run(["claude","-p",brief,…], timeout=180)` in the brain's uid.

**Registration:** T2, REQUIRE-CONFIRM always. Its impl unit is the ONE unit on the box
permitted to read the operator's Claude credentials — nothing else gets that path.

### 3a. Precondition — verify (i)'s properties before retiring (i)

`_SummonConsent` carries four constraints. The confirm seam is asserted to carry all four at
the broker layer. **The doc gate is the place that assertion gets checked against the deployed
seam, not against the spec.** If any property is weaker in practice, (i) stays until it isn't:

| (i)'s constraint | where it lives in (i) | claimed equivalent | verified? |
|---|---|---|---|
| operator-only (never from model output) | `arm()` called only from the operator's input line | tty-separated channels, INV-1, authn | ☐ |
| single-use | `check_and_consume` clears `_at` on success | nonce-bound approve/deny | ☐ |
| time-bounded | `_SUMMON_GRANT_TTL_S = 300` | `humanWindow < brokerTimeout` | ☐ |
| logged both ways | `_log_summon_attempt(allowed, reason)` — a refusal is LOUD | audit-before-effect, both verdicts | ☐ |

Discharging those four boxes against `docs/phase2-step6-confirm-channel-spec.md` **and the
deployed unit** is a precondition of the summon PR, and it is the one part of this document I
expect to change on contact.

### 3b. Retirement + battery migration

`ok_to_summon()` has **one call site** (`agent-brain.py:1199`) — constraint 5 of (i) paying out
exactly as designed. The summon PR replaces that call with the broker call and deletes
`_SummonConsent`.

**Two consent gates in series is the failure mode to avoid**: it double-asks the operator, and
it silently repoints the 15 existing arms — PR #257 defect-2, *"a new gate upstream of an arm
can quietly repoint it,"* applied forward. So retirement is in the same PR as the cutover, never
a follow-up.

The 15 arms' **properties** migrate into the broker battery; the in-process object does not.
Each arm restates as a property of the confirm seam (a consent-less summon denies; an expired
grant denies; a grant is single-use; both outcomes are audited). **An arm that cannot be
restated as a seam property is a property (i) had and the seam does not** — and that is a
finding for §3a, not an arm to drop quietly. The migration must print its arm count beside the
verdict, so a silently-dropped arm cannot pass as a green suite.

### 3c. How the impl actually runs — the scoped loader (ruling 3)

Ruling 3: `programs.nix-ld.enable` stays **OFF** on OPEN. A box where a generic
dynamically-linked ELF gets a message and exit 127 is a box where **only store-built binaries
execute** — a structural bound on precisely the wire §2 describes. Enabling a global loader
shim so one binary can run would trade a systemic control for a convenience.

Measured on the Dell (`agent-os-cfg-bca9ed4`, 2026-09-03), two controls:

```
CONTROL A  foreign ELF on Ubuntu                       rc=0
CONTROL B  /run/current-system/sw/bin/true on the Dell rc=0
MEASURE    foreign ELF on the Dell                     rc=127   stub-ld message
```

`/lib64/ld-linux-x86-64.so.2` exists as a `stub-ld`; `NIX_LD` unset; no nix-ld in the config.

So cloud-on-OPEN is **scoped, not excluded**: the loader accommodation belongs to this
capability's impl unit and nowhere else — a per-binary FHS wrapper, or a store-built claude
package if one pins cleanly. That composes with 3a: **one unit holds the credentials AND the
foreign-ELF affordance; everything else on the box keeps stub-ld.**

**Open evidence slot — the VM run.** Choosing wrapper-vs-package needs the binary's linking
reality (static? dynamic? does a nix build exist?), which I have not measured — the Dell has no
`claude` installed and mutating a live box unasked is not mine to do. `nix build .#vm`, then
`bin/setup-brain.sh --cloud-only` and `ldd ~/.local/bin/claude`. Evidence attaches **here**,
and closing it also closes Geist's last `inferred` claim on F3. **This section is `inferred`
until that lands.**

### 3d. Fallout for the current code path, worth fixing whenever it is cheapest

`_summon_claude` catches `FileNotFoundError` ("isn't set up") and sniffs stderr for
`log in`/`auth`. A stub-ld failure is neither: rc=127 with *"Could not start dynamically linked
executable"*, so it surfaces as the generic *"Claude couldn't complete that: …"*. Fail-soft, so
nothing breaks — but the operator gets no idea why, on a box where the answer is structural.

---

## 4. F2, widened — a probe at both ends

`setup-brain.sh --cloud` today succeeds while producing an inert binary: install returns 0,
`command -v claude` returns 0, and the thing cannot run. **Fail-looks-like-success at setup
time — the exact class #258 closed at login time.**

Fix: after install, run the SAME `--version` probe. On rc≠0, say loudly *"installed but cannot
run on this profile (stub-ld, exit 127) — cloud unavailable"* and exit nonzero, on OPEN and
SEALED alike (they fail for different reasons: SEALED's install is dropped by the egress wall;
OPEN's install succeeds and the binary is inert). **Presence on PATH is not the test; the probe
is** — now at both ends.

Small and independent of the three slices. Ships wherever it is cheapest.

---

## 5. Sequence and pacing

1. **`fetch_web` → `net.fetch`** — the slice the design already owes; exercises the seam with no new policy.
2. **`run_command` → `sys.run`** — the biggest live risk; needs the tier ruling in §2 first.
3. **`summon_claude` → `cloud.summon`** — lowest marginal risk (already code-gated), and its cutover is the (i)-retirement PR. Needs §3a discharged and the §3c VM evidence.

Separate PRs, security surface, Fable review. Idle-until-work material; nothing here preempts
step (c), which is Dillon-gated and untouched.

## 6. Open questions for the gate

1. **§2 tier** — new tier, or T1-with-derived-checkpoint for `sys.run`? (I lean T1.)
2. **§2 confinement** — is `network = false` on the opening `sys.run` decl too tight to be usable?
3. **§1 impl** — `cap-net-fetch` directly, or wrapping `agos-web fetch`? (I lean direct.)
4. **§3a** — is discharging the four-property table a doc-gate obligation, or the summon PR's first commit?
