# App approval through the confirm channel (spec, v0)

Status: SPEC for review. No code in this PR. It covers §7 item 1 of `docs/design/app-manifest.md`:
approving an agent-built app through broker → `bin/confirm` → Telegram/getty, for the sealed
image where the human is not sitting at the agent's terminal.

Security surface (registry, a new capability, the approval store the runner trusts):
branch → spec PR → Fable spec review → implementation PRs → Fable code review → merge.

## 0. Thesis

On a dev box the owner approves an app by running `agos-approve` on their own tty. On the
sealed image the agent works alone and the owner may only be reachable by phone. The agent
should be able to ask "may app X have exactly these permissions?" and get an answer that
only the human can give, through the channel that already exists for that: the broker's
REQUIRE-CONFIRM path. The answer must bind to the exact manifest the human read, and nothing
the agent controls may be able to write an approval.

## 1. Scope

IS:
- a T2 capability `app.approve` whose impl records one approval after the broker has a human yes;
- moving the image's approval store out of any account the agent controls, with the runner,
  `agos-approve` and `agos-schedule` reading it from a pinned path;
- the frame rule: the human sees every manifest field, untruncated, or the capability refuses.

IS NOT:
- any change to `bin/confirm` or `bin/broker` (the seam contract is used as shipped);
- approving app CODE. Like v0, approval is of the permission manifest. The sandbox is what
  makes running unreviewed code acceptable;
- revocation through the agent (section 3.7), GUI confirm (decision 4 stays open), or a new tier.

## 2. Why now: the store the runner trusts is agent-writable on the image

`bin/agos-run` reads approvals from `$HOME/.local/state/agent-os/approvals.json` of whoever
runs it. On a dev box that is the human, and only `agos-approve` (which refuses the `agent`
account, the agent session and a non-tty) writes it. On the sealed image the agent session
is the `agent` account. If the agent runs apps, `$HOME` is the agent's own home, and the
agent can write that file directly. Every approval gate would then be decorative.

The agos-* tools are not packaged into the image today (no module references them), so this
is a latent hole, not a live one. It must be closed before they ship. Section 3.1 closes it.

## 3. Design

### 3.1 The approval store moves to a path the agent cannot write

- Image path: `/var/lib/agent-os/app-approvals/approvals.json`. Directory `root:root 0755`,
  file `root:root 0644`. The agent can read it (the runner needs that) and cannot write it.
- `agos-run`, `agos-approve` and `agos-schedule` read the store from `AGOS_APPROVALS` when it
  is set, and then ONLY from there: no fallback to `$HOME`. The image pins `AGOS_APPROVALS`
  in the wrappers it installs, the way `modules/broker.nix` pins its environment. Unset
  (dev boxes), behaviour is unchanged.
- The runner already deny-lists the approvals path from app binds. The new path is under
  `/var/lib/agent-os`, which apps cannot bind (outside `$HOME` the sandbox root is read-only,
  and `--ro-bind / /` makes this file readable to apps. That is acceptable: the store holds
  names, hashes and timestamps, no secrets).
- On the image, `agos-approve approve` keeps working for an owner at a local tty through
  `sudo` (it already refuses the agent session and the `agent` account). That is the
  break-glass path when the confirm channel is down.

### 3.2 The capability

```nix
"app.approve" = mkCap {
  tier = "T2"; impl = "cap-app-approve";
  summary = "Ask the owner to approve an app's exact permission manifest.";
  args = { app = "path"; sha256 = "string";
           name = "string"; version = "string"; entry = "string"; files = "string";
           network = "string"; schedule = "string"; limits = "string"; devices = "string"; };
  sandbox = { readOnlyPaths  = [ "/var/lib/agent-os/apps" ];
              readWritePaths = [ "/var/lib/agent-os/app-approvals" ]; };
};
```

- **T2**: granting an app new authority is outward-facing in effect (an approved app may
  reach listed domains, read listed files, run on a timer). T2 is always confirmed,
  provenance-independent (threat model §5), so a tainted session can still ask and the
  human still decides.
- **Apps live in `/var/lib/agent-os/apps/<name>/` on the image.** Capability sandboxes run
  with `ProtectHome=yes` and an empty root (`modules/cap-sandbox.nix`), so the impl cannot read
  `/home`. `agos-build`, `agos-run` and `agos-schedule` take the apps root from `AGOS_APPS`
  (pinned on the image; default `~/.local/share/agent-os/apps` on dev boxes). The agent
  account owns `/var/lib/agent-os/apps`.
- `app` is a `path` arg, so the broker already refuses a non-canonical path or one outside the
  declared read root before anything is shown to the human. A symlink to elsewhere resolves to
  nothing inside the sandbox's empty root (the existing symlink boundary).
- **The manifest fields travel as arguments on purpose.** `bin/confirm` renders exactly the
  typed arguments (the generic sorted `key=value` fallback, every value scrubbed and line-
  prefixed). Passing each field means the human reads the real permissions, not a name. The
  agent cannot lie in them: the impl refuses unless each one equals the manifest on disk
  (3.3). List and object fields are canonical JSON strings (`sort_keys`, compact), because the
  registry has no list type and adding one is a wall change this does not need.

### 3.3 What the impl does (after the broker has a human yes)

The invoke seam passes `{capability, arguments}`; the impl never sees the nonce and does not
need it. The broker only reaches the impl after an approve with an unchanged taint epoch.

1. Parse arguments; every declared key present (the broker already enforces exact keys).
2. `app` must equal `/var/lib/agent-os/apps/<name>` with `name` matching the runner's app-name
   rule. Open `manifest.json` with `O_NOFOLLOW`; refuse a non-regular file or more than 64 KiB.
3. Validate with the runner's own `validate()`, imported by path (the same rule set `agos-build`
   and `agos-approve` use), against the app directory as the runner will see it.
4. Compute the canonical manifest SHA-256 the runner uses. Refuse unless it equals `sha256`.
5. Refuse unless each field argument equals the canonical JSON (or the plain string for
   `name`, `version`, `schedule`) of the same manifest key, and the manifest has exactly the
   eight keys. This is what binds "what the human read" to "what is approved".
6. **Render budget.** Refuse if any one argument, after `bin/confirm`'s own `scrub()`, is longer
   than its `MAXPREVIEW` (512), because the frame would truncate it and the human would approve
   unseen bytes. Refuse if the whole argument block exceeds 3000 characters, so the frame fits
   one Telegram message (4096) with the fixed template. `scrub` and `MAXPREVIEW` are imported
   from `bin/confirm` by path, not copied, so the impl measures what the frame actually shows.
   A refused manifest can still be approved at a local tty with `agos-approve` (3.1).
7. Write the store atomically (temp file in the same directory, `fsync`, `rename`): add
   `{"<sha>": {"name": ..., "approved_at": ..., "via": "confirm"}}`. A corrupt or non-object
   store is refused, never overwritten (the `agos-approve` rule).
8. Return `{ok: true, content: "approved <name> <sha prefix>"}`; every refusal is
   `{ok: false, content: "<reason>"}` with exit 0 (the `cap-invoke` contract: impl error bodies
   flow through the taint fence).

**Time of check vs time of use.** The agent owns the app directory and can edit the manifest
while the human reads the prompt. Step 4 re-hashes after the human's yes. If the manifest
changed, the hash differs from the approved `sha256` argument and nothing is written. If it
changes after the write, the runner re-hashes at every run and refuses (existing behaviour).
The approval is for a hash, never for a name.

### 3.4 What the human sees

The existing frame, unchanged: `CAPABILITY: app.approve`, `TIER: T2`, `PROVENANCE`, then
under "untrusted model input" an `ARGS:` block with `app`, `devices`, `entry`, `files`,
`limits`, `name`, `network`, `schedule`, `sha256`, `version`, one per line, each prefixed `│ `.
A named render shape for `app.approve` in `bin/confirm` (plain-word labels such as "can read",
"can reach") would be friendlier. It is deliberately left out: it is a `bin/confirm` change, so a
separate Fable-reviewed PR if wanted (open question 2).

### 3.5 How the agent asks

`agos-build` gains a final line on the image: instead of "run `agos-approve approve <dir>`",
it prints the `app.approve` call it would make, with the arguments it computed. The agent's
tool loop makes the call through the broker like any other capability. No new agent tool.

### 3.6 Schedules

Unchanged in principle: `agos-schedule install` requires an approved manifest and reads the
same store through `AGOS_APPROVALS`. A schedule is inside the hashed manifest, so approving
the manifest approves its schedule. Installing the timer stays a separate, local step (on the
image, a follow-up decides whether the agent may install user timers for approved apps; the
timer can only run `agos-run`, which re-checks the approval).

### 3.7 Revocation

Out of scope for the capability. The owner revokes with `sudo agos-approve revoke <name|sha>`.
An agent-callable `app.revoke` would only ever reduce authority and could be T1. It is listed
as a follow-up, not designed here.

## 4. Invariants and build assertions

- **A1** Only `app.approve` may hold `/var/lib/agent-os/app-approvals` in any readable or
  writable scope. A registry assertion (like `protectedPaths`, with a single named exception)
  fails evaluation otherwise.
- **A2** `/var/lib/agent-os/app-approvals` is not writable by the `agent` account: a VM test
  asserts the mode, the owner, and that a write as `agent` fails.
- **A3** With `AGOS_APPROVALS` set, the runner, `agos-approve` and `agos-schedule` never read
  `$HOME/.local/state/agent-os/approvals.json`. Control: an approval planted only there does not
  let `agos-run` start the app.
- **A4** `cap-app-approve` is in `cap-invoke-pkg`'s `shippedCaps` only together with its
  sandbox entry (the existing gate: no derived sandbox, no run).

## 5. Test plan

`tests/app-approve-battery.py` (impl, fake store, no broker), each arm with a control:
- correct arguments → one entry written, `via: confirm`, mode preserved; the control (any one
  field changed by one character) writes nothing;
- `sha256` correct but a field argument differs from disk → refused;
- manifest edited between "confirm" and the impl run → refused, store unchanged;
- `app` outside the apps root, `app` with a name that fails the rule, symlinked `manifest.json`,
  oversize manifest → refused;
- one argument over 512 scrubbed characters, or an argument block over 3000 → refused (and
  the same manifest one character shorter passes);
- corrupt store → refused, file byte-identical afterwards;
- control characters and bidi marks in a field: the comparison uses raw values, and the budget
  is measured after `scrub()` (the frame is what the human reads).

Registry: a `nix flake check` arm proving A1 (a second cap declaring the store path fails
evaluation). Runner: `agos-run-battery` gains the A3 arm. VM test `test-app-approve-confirm`
(after the image wiring): broker + `bin/confirm` with a scripted getty answer. Approve → the app
runs; deny → it is refused; manifest edited during the prompt → refused; `agent` cannot
write the store.

## 6. Build order (each a PR, merged in order)

1. **Dev tools honour `AGOS_APPROVALS` / `AGOS_APPS`** (`agos-run`, `agos-approve`,
   `agos-schedule`, `agos-build`) with the A3 arm. Not wall code; normal review.
2. **`app.approve` registry entry + `bin/cap-app-approve` + battery + A1 assertion +
   `shippedCaps`.** Wall code: Fable code review before merge.
3. **Image wiring**: a module that packages the agos-* tools with pinned env, creates
   `/var/lib/agent-os/apps` (agent-owned) and the store directory (root-owned), plus the VM
   test. Wall-adjacent: Fable code review.

## 7. Open questions for the spec review

1. Is T2 the right tier, or does granting authority deserve a distinct class (for example
   never-auto even if `T1-auto-on-trusted` lands)? T2 is never auto today, so this only matters
   if T2 semantics change.
2. Should `bin/confirm` get a named shape for `app.approve` (plain-word labels), as a separate
   PR, or is the generic `key=value` block acceptable for v0?
3. Should the frame also show a hash of the app's entry source, so the human can tell "same
   permissions, different code"? v0 says no (permissions, not code), but it is one more line.
4. The 3000-character block budget is a guess at "fits one Telegram message with the template".
   Should `bin/confirm` instead refuse any frame over 4096 itself, for every capability? That
   protects all T1/T2 prompts, not just this one, and is a separate `bin/confirm` change.
