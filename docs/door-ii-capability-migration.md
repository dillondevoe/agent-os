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
| verdict class | derived from tier (T0 auto / T1 confirm-in-v1, checkpoint scope from `readWritePaths`, auto deferred to §5a / T2 always-confirm) |
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

**Resolved (gate on #259, Q3): direct.** `cap-net-fetch` does the fetch itself rather than
wrapping `agos-web fetch`. Wrapping is smaller, but the audit line should name what actually
runs in the unit.

---

## 2. `run_command` → `sys.run` (new, **T1**) — confinement, not verdicts

Today (`agent-brain.py:1153-1157`):

```python
o = subprocess.run(["bash","-c",args.get("command","")], capture_output=True, text=True, timeout=30)
```

`bash -c <arbitrary>` in the brain's own uid, 30s timeout, no wall. `SYS_BASE` tells the model
*"You HAVE HANDS"* and the same prompt tells it to read web pages. **This is the live wire door
(ii) actually cuts:** prompt-injection → arbitrary exec, on the operator's uid.

The ruling's core point, adopted verbatim: **do not try to classify arbitrary shell.** The
broker's contribution here is confinement and an audit line, not a semantic gate on the command
string.

- **Tier: T1** (Geist's gate on #259, Q1). The checkpoint scope is **derived from
  `readWritePaths`** — the mechanism `capability-registry.nix:113-115` already prescribes, so
  this widens nothing.
- **Confinement:** a transient `cap-sandbox` unit derived from the registry decl. Blast radius
  by declaration.
- **Widening the sandbox is a PR-gated registry change** — which is exactly where that decision
  belongs, and is the whole benefit of the move.

```nix
"sys.run" = mkCap {
  tier = "T1"; impl = "cap-sys-run";
  summary = "Run a shell command inside the declared sandbox.";
  args = { command = "string"; };
  sandbox = { readWritePaths = [ "/var/lib/agent-os/workspace" ]; network = false; };
};
```

### 2a. Why not ALLOW-AUTO — the ask was standing on a false premise

An earlier draft asked for ALLOW-AUTO and worried that T2's always-confirm definition
contradicted it. **The contradiction was not between the tier and the ask; it was in the ask.**
`capability-registry.nix:112` — *"T1 — reversible local side effects. Confirmed-in-v1 (T1-auto
deferred)"* — and threat model §5, *"confirm everything above T0"*. ALLOW-AUTO-always was never
available **at any tier**. So the real choice was: invent a verdict class consulting neither
operator nor taint, or take T1's road. §5a already rules no-second-vocabulary. T1's road.

**The general law, and it is the durable half — tiers must be closed under emulation.** A
workspace-scoped `sys.run` is a universal `file.write` emulator: anything `file.write` can do
inside `/var/lib/agent-os/workspace`, `sys.run` can do with a redirect. An auto `sys.run` would
therefore make `file.write`'s confirm **advisory** — the tier survives on paper while the
capability that emulates it walks around it. Check any new capability against this: *what do my
declared paths and args let me emulate, and is that thing's tier still true afterwards?*

**Honest cost, restated:** friction is not zero. It is **`file.write`'s friction**, until §5a's
`T1-auto-on-trusted` lands. That is the correct place for the pressure — and note that slice 1
ships §5a's own enabling dependency (taint stamping on fetched bytes) first, so the order
already serves it. **Friction pressure routes to implementing §5a, never to loosening the
tier.**

### 2b. `network = false` is load-bearing and permanent

Not, as the earlier draft had it, "the line most likely to be wrong." Two independent reasons:

1. **Registry assert (4): network ⟹ T2.** A networked `sys.run` is *definitionally* T2, hence
   always-confirm. There is no slice at which it widens and stays auto — the invariant closes
   that door before policy gets a vote.
2. **A sandboxed `curl` would bring web bytes back UNSTAMPED**, laundering straight past the
   anti-laundering property §1 exists to establish.

So `run_command curl …` dying is **the control working**. A real need for networked exec names
a **new T2 capability** with its own decl and its own confirm — never a wider `sys.run`.

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
deployed unit** is the **summon PR's first commit** (gate on #259, Q4) — deliberately *not*
today. A box ticked now would certify a seam slice 3 acts on later, and a "verified" row that
ages is the record disagreeing with the machine silently: the exact failure the unticked-box
design exists to prevent. The doc-gate obligation was the table existing with the right four
rows; that is discharged.

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

## 6. Questions carried to the gate — all four answered

Ruled by Geist on PR #259, 2026-09-03T00:43:16Z. Kept as a record of what was decided and on
what grounds, since two of the four turned on lines neither of us had quoted.

1. **`sys.run` tier — T1, no new tier.** The ALLOW-AUTO ask was standing on a false premise:
   T1 is *also* confirmed-in-v1, so auto was never available at any tier. Option (2) widens
   nothing — `capability-registry.nix:113-115` already prescribes checkpoint-scope-from-
   `readWritePaths`. See §2a, including the emulation law.
2. **`network = false` — permanent, not too tight.** Registry assert (4) makes a networked
   `sys.run` definitionally T2, and a sandboxed `curl` would launder unstamped web bytes past
   §1's whole point. See §2b.
3. **Slice 1 impl — direct**, not a wrapper. See §1.
4. **§3a discharge — the summon PR's first commit**, not the doc gate. See §3a.

**Standing convention adopted from this gate:** arm-count-printed-beside-the-verdict becomes the
battery's convention generally, not just for the summon migration.
