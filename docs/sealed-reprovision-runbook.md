# Agent OS — clean re-provision runbook (`agentos-sealed`)

**WP-S6.2.** Turns "seal = clean re-provision" from tribal knowledge into a procedure someone
else can follow. Written 2026-08-23 by Mirror against `spec-agentos-phase-s-execution-2026-08-13`
§0/§1/§5/§6 and `SEAL-CHECKLIST-agentos.md`.

---

## STATUS — READ THIS BEFORE FOLLOWING ANYTHING BELOW

**This runbook is WRITTEN but NOT ACCEPTED, and the two are different things.**

Its acceptance criterion (spec WP-S6) is: *"followed literally against a throwaway VM, produces a
system passing S1–S5's acceptance criteria from a cold start."* As measured on `origin/main` @
`f864c2c` (spec §6, Geist, 2026-08-23):

| Dep | State | Consequence for this document |
|---|---|---|
| S1 | DONE (#86 #87) | §5.1 checks are runnable |
| S2a | DONE (#92, #94–#97, #99) | §5.2 partially runnable |
| **S2b** | **NOT STARTED** — `cap-net-fetch` / `cap-message-send` absent | §5.2 cannot fully pass |
| **S3** | **NOT STARTED** — `system-set.nix` still SCAFFOLD | §5.3 cannot pass |
| **S4** | **NOT GRADUATED** — `bin/broker:123`, taint still shadow | §5.4 cannot pass |
| **S5** | **BUILT-NOT-VERIFIED** — `agentos-sealed-s5` candidate, HW verify behind S4 | §5.5 cannot pass |

So **the verification section of this runbook currently fails by construction**, and it is supposed
to. Spec §1 sequences S6 *after* S4 and S5 are green; §6 ruled S6.2 writable now because it needs no
hardware. Both are true — needing no hardware is not the same as having a runnable acceptance — and
this block exists so nobody mistakes "the runbook exists" for "the re-provision is proven."

**A green run of §5 is the acceptance event.** Until that has happened against a throwaway VM and
been recorded below, this document is a plan, not a verified procedure.

**Acceptance log** (append one line per literal cold-start run; empty is the honest state):

| Date | Runner | Target | S1 | S2 | S3 | S4 | S5 | Notes |
|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | *no accepted run yet* |

**This runbook does not authorize wiping any physical box.** WP-S7 is gated on spec §3
(seal target (a) re-provision the Dell vs (b) image + runbook until second hardware), which has
**no Dillon answer on record**. Running §2 against hardware before that answer exists is out of
scope for every agent.

---

## 0. What this procedure is

`agentos-sealed` is a **separate flake output**, not a patch applied to a running `agentos-open`
box. Dev and build stay on `agentos-open` forever — unsealed, full-power, Tailscale-reachable.
Sealing means **a fresh system closure applied to wiped storage**. A live open box mutated in place
is not a sealed box and must never be recorded as one; the whole point of the clean-room posture is
that the sealed system has no history you have to trust.

Two constraints bind every step below regardless of sequencing:
- **No credentials in the public repo, ever.**
- **The image/release lane ships hardened-only** — it must refuse to emit an image for
  `agentos-open` or for unsealed `#agentos`. §1.4 verifies this rather than assuming it.

---

## 1. Pre-flight, on the build host (nothing destructive happens in this section)

### 1.1 Confirm the dependency gate is actually green

Do not skip this because the STATUS table above says it is red today — re-measure, because the
table is a snapshot and this runbook outlives it.

```sh
# S4 graduated: taint must be a GATE, not a shadow log
grep -n 'taint' bin/broker | sed -n '1,40p'      # expect no shadow-only path at the gate
nix build .#test-taint-gate-battery              # S4's Step-8 battery, every §10 deny case
# S5 verified on real hardware (see its own acceptance line — this cannot be met in a VM)
```

**If S4's battery does not exist or does not pass, stop.** Sealing a system whose capability gate
is still advisory produces a box that looks hardened and is not — the exact false-success shape
this project keeps paying for.

### 1.2 Budget RAM before anything else (spec §5, Dillon-approved 2026-08-17)

**The model is the payload, and it is sized in FIRST.** OS and services fit in the remainder, never
the other way around.

1. Reserve the 14B judgment lane's RAM explicitly.
2. Size OS + services against what is left, not against the box total.
3. Refuse any Electron-class dependency (anything shipping a browser runtime) **at the nixpkgs
   gate** — not at review, not at runtime.
4. Judge every choice by **RAM-per-capability, not RAM total**. On a local-inference box, wasted
   RAM is intelligence loss: it forces a smaller model or a shorter context.

Record the reservation and the remainder in the acceptance log row. A re-provision that quietly
shrinks the model lane has failed even if every other check is green.

### 1.3 Build the sealed closure

```sh
nix flake check .#agentos-sealed          # S1's asserts ride here
nix build .#agentos-sealed
```

Composition must be `mkSystem`, **not** `mkOpenSystem`. Confirm by inspection of the flake output,
not by the build succeeding — an open composition builds perfectly well.

### 1.4 Prove the release lane still refuses the open variants

This is a **negative control** and it is load-bearing: an image lane that has never been shown to
refuse is not known to refuse.

```sh
nix build .#image-agentos-open    # MUST FAIL
nix build .#image-agentos         # MUST FAIL (unsealed)
nix build .#image-agentos-sealed  # must succeed
```

If either of the first two succeeds, stop and fix the lane. Do not proceed and "remember not to
use it."

### 1.5 Answer the seal target before touching storage

Spec §3, Dillon's call, currently unanswered:
- **(a)** re-provision the Dell as the sealed target, or
- **(b)** ship sealed as image + runbook until second hardware exists.

Under **(b)**, the only legitimate target for §2–§4 is a throwaway VM. Under **(a)**, a physical
wipe requires WP-S7 to be scheduled, which this spec explicitly does not do.

---

## 2. Wipe target storage

Destructive. Nothing here is reversible; §6 is the abort path and it is "start over", not "undo".

1. Confirm the target identity **out of band** — serial, MAC, or console — not by hostname. A
   hostname is a claim made by the machine you are about to erase.
2. Confirm nothing on the target is the only copy of anything. Sealed provisioning assumes the
   disk is worthless.
3. Wipe partitions and any prior LUKS headers. Do not preserve `/var/lib`, `/home`, or an old
   `/nix/store` "to save time" — a carried store is carried history, and it defeats the clean room.

**Fresh means fresh.** Every reuse you allow here is a thing a future audit cannot rule out.

---

## 3. Fresh weights, fresh state

1. **Model weights: re-download, do not copy from the open box.** Weights carried off a
   development machine have the development machine's provenance.
2. **`/var/lib` is created empty.** No carried audit log, no carried registry, no carried identity
   material.
3. **Identity is minted on first boot, not transplanted.** The participant-minting oneshot
   (`identity.nix`, task 324) runs before anything that signs. A copied key means the sealed box's
   identity is the old box's identity, which makes the audit chain a lie about which machine acted.

---

## 4. Apply the sealed config

```sh
nixos-rebuild switch --flake .#agentos-sealed
```

Set the audit-signing pair **where the broker process actually inherits it** (§5.6 explains why
this is not the same as declaring it in the config).

Reboot once before verifying. Verification against a system that has not completed a real boot
proves less than it appears to — S1–S5's acceptance criteria are about a running sealed box, not
about a successful switch.

---

## 5. Verify — re-run the acceptance criteria AGAINST THE PROVISIONED BOX

**Do not carry any of these forward from earlier VM runs.** The runbook's whole value is that the
provisioned system is checked as provisioned. A check inherited from CI is a check this procedure
did not perform.

### 5.1 S1 — sudo lockdown, skuid scope, tty3 break-glass
- `sudo curl <arbitrary-host>` **fails** post-seal.
- The agent user is not in `wheel` and has no sudoers entry.
- tty3 break-glass login works. Verify it **before** you need it.

### 5.2 S2 — capability impls
- Each impl's sandbox unit matches its tier's Step-1 build-time assertion.
- The hostile-input battery passes per capability.
- `cap-invoke` no longer fails closed on any of the four.

### 5.3 S3 — `system.set`
- A live `timezone` change works end to end; the confirm frame shows the correct delta.
- An out-of-enum option value is rejected **at the broker**, not at the root unit.

### 5.4 S4 — taint gate
- Every deny case in threat-model §10's narrative **actually denies**, verified by running the
  battery on this box — not by reading the code and not by trusting CI.

### 5.5 S5 — egress seal
- A non-allowlisted HTTPS destination from uid 0 **fails**.
- Allowlisted destinations (nixos cache, pinned flake hosts) still succeed. This is the positive
  arm: without it, a total-failure network reads as a working seal.
- A live `nixos-rebuild switch` completes with the proxy in place.

### 5.6 Audit signing — the sovereign switch (`SEAL-CHECKLIST-agentos.md`, task 324, PRs #140 #144 #145)

Verbatim from the checklist, because this is the one line whose failure mode is a *clean-looking
success*:

> **Dell sovereign switch:** host config sets `AGENT_OS_AUDIT_SIGNER=agent` **and**
> `AGENT_OS_AUDIT_REQUIRE_SIGNED=agent` — both, never one (audit-pkg.nix's deploy-coupling rule,
> finding G). After `nixos-rebuild switch`, run `audit verify` once under the pin and confirm it
> reports `chain intact` with a **non-zero signed count**. A `0 signed` result means the vars did
> not reach the broker's environment: the log will verify clean and the signer will be silently off.

Two things that are easy to get wrong and are the reason this section is long:

**Read the env from inside the broker, not from the config.** The broker is not a systemd unit — it
runs from the tty1 agent-shell session. `environment.variables` reaches login shells via
`/etc/profile`, which is the path the VM test exercised; the tty1 path on a real box is a different
path and must be confirmed on the box:

```sh
tr '\0' '\n' < /proc/<broker-pid>/environ | grep AGENT_OS_AUDIT
```

**Then read the count.** `chain intact` alone is not a pass — an empty or unsigned chain is intact.
The non-zero **signed count** is the assertion. CI cannot see this: `tests/identity-boot.nix` leg 6
asserts the env reaches the invocation *in the VM*, and nothing asserts it on real hardware except
a person reading the number. Shown firing, not merely green.

Pre-seal invariants already proven in CI — recorded so they are not re-derived, and **not** to be
re-checked here: a real reboot does not rotate keys (leg 5); the minting oneshot runs before
anything that signs, with signing on from boot (legs 1–5); a forged-but-registered signer is
rejected by the pin and accepted with the pin unset (legs 8/8b).

### 5.7 Record the run

Append a row to the acceptance log in the STATUS block — including the failures. A runbook with
only successful rows is a runbook whose failures were deleted, which tells a future reader nothing
about where it is fragile.

---

## 6. Abort and rollback

There is no rollback. Storage was wiped in §2 and identity was minted fresh in §3; the sealed box
has no prior generation to switch back to.

If verification fails: **do not patch the sealed box into compliance.** A sealed system repaired by
hand is an open system with a sealed system's name, and every §5 check it subsequently passes is
about a machine that was mutated in place. Fix the cause in the flake, then re-run this procedure
from §2 against wiped storage.

Recovery of the *target hardware* to a usable state, if the seal is abandoned, is a re-provision of
`agentos-open` and is outside this runbook.

---

## 7. What this runbook is not

- Not authorization to wipe hardware (WP-S7, gated on spec §3).
- Not a patch procedure for a running box — see §0.
- Not a substitute for S4's battery or S5's hardware verification. It **runs** their acceptance
  criteria; it does not replace them.
