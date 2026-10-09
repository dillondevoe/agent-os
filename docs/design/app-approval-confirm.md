# App approval through the confirm channel (spec, v0)

Status: SPEC, APPROVED by the Fable spec review (2026-10-09: first pass approve with required
changes, all folded in; re-review approve, its four should-fix notes folded in; rulings in section 7). No code in this PR. It
covers §7 item 1 of `docs/design/app-manifest.md`: approving an agent-built app through broker
→ `bin/confirm` → Telegram/getty, for the sealed image where the human is not sitting at the
agent's terminal.

Security surface (registry, a new capability, the approval store the runner trusts):
branch → spec PR → Fable spec review → implementation PRs → Fable code review → merge.

## 0. Thesis

On a dev box the owner approves an app by running `agos-approve` on their own tty. On the
sealed image the agent works alone and the owner may only be reachable by phone. The agent
should be able to ask "may app X have exactly these permissions?" and get an answer that
only the human can give, through the channel that already exists for that: the broker's
REQUIRE-CONFIRM path. The answer must bind to the exact manifest the human read, and the
agent's tool loop must have no way to record an approval itself.

## 1. Scope

IS:
- a T2 capability `app.approve` whose impl records one approval after the broker has a human yes;
- moving the image's approval store to a path the agent account cannot write, compiled into
  the image's copies of the tools (no environment fallback);
- the frame rule: the human sees every manifest field, untruncated, in a frame that fits one
  Telegram message, or the capability refuses;
- one line in `bin/broker`: `ORIGIN_BY_CAP["app.approve"] = TRUSTED` (section 3.3, step 8).

IS NOT:
- any other change to `bin/broker`, and no change to `bin/confirm` (the frame-size guard for all
  capabilities is a separate PR, section 7 ruling 4);
- approving app CODE. Like v0, approval is of the permission manifest. The sandbox is what
  makes running unreviewed code acceptable;
- revocation through the agent (3.7), GUI confirm (decision 4 stays open), or a new tier;
- running the app runner outside the agent account (section 2, residual).

## 2. Why now, and what this does and does not guarantee

`bin/agos-run` reads approvals from `$HOME/.local/state/agent-os/approvals.json` of whoever
runs it (`approved()`, around line 170). On a dev box that is the human, and only `agos-approve`
(which refuses the `agent` account, the agent session and a non-tty) writes it. On the sealed
image the agent session is the `agent` account, so `$HOME` would be the agent's own home and the
agent could write that file directly. A second path already exists in the tools: `agos-schedule`
writes `ExecStart=<bin/agos-run> <appdir>` with only `Environment=PATH=`, so a scheduled run
would never see a wrapper's environment and would fall back to `$HOME`.

The agos tools are not packaged into the image today (no module references them), so neither
path is live. Both must be closed before the tools ship. Section 3.1 closes them.

**Residual, stated plainly.** While `agos-run` and `agos-net` run as uid `agent`, the approval
gate constrains the model's TOOL LOOP (what the agent does through the broker and the packaged
tools), not the ACCOUNT: an account that can run `bwrap` can sandbox and run its own code without
asking anyone. The account-level guarantee arrives only when the runner and the network hand
run outside the agent uid. That is a later, separate design. This spec does not claim it.

`agent` has no sudo (only the break-glass account does, `modules/break-glass.nix`). On the
image `sudo agos-approve` is therefore a human-only, break-glass path. Note that sudo's
`env_reset` drops `AGENT_OS_ACTIVE`, so the session check in `agos-approve` is not what stops
the agent there; the absence of sudo for `agent` is.

## 3. Design

### 3.1 The approval store moves to a path the agent cannot write

- Image path: `/var/lib/agent-os/app-approvals/approvals.json`. Directory `root:root 0755`
  (systemd-tmpfiles), file `root:root 0644` (3.3 step 7). The agent can read it (the runner
  needs that) and cannot write it.
- **No fallback on the image.** The image packages `agos-run`, `agos-approve`, `agos-schedule`
  and `agos-build` with the store path and the apps root substituted in as constants at build
  time (`IMAGE_APPROVALS`, `IMAGE_APPS`). When the constants are set, the tools ignore the
  environment entirely and never read `$HOME/.local/state/agent-os/approvals.json`; a missing or
  unreadable store approves nothing. On dev boxes the constants are empty and `AGOS_APPROVALS` /
  `AGOS_APPS` (optional env) or the current `$HOME` defaults apply, unchanged.
- **Scheduled runs use the same copy.** `agos-schedule` computes `RUNNER` beside itself, so on
  the image the unit's `ExecStart` is the packaged runner with the constants baked in. It also
  writes `Environment=AGOS_APPROVALS=` and `AGOS_APPS=` into the unit on dev boxes where they are
  set, so a scheduled run reads the same store as a run by hand. A schedule-battery arm covers it.
- Apps cannot bind the store: outside `$HOME` the sandbox root is a read-only bind. Apps can
  read it (`--ro-bind / /`), which is acceptable: it holds names, hashes and timestamps, no secrets.
- `agos-approve approve` remains the break-glass local path on the image (section 2).

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

- **T2, and never-auto by name.** Granting an app authority is outward-facing in effect (an
  approved app may reach listed domains, read listed files, run on a timer). T2 is always
  confirmed, provenance-independent (threat model §5). In addition, `app.approve` is excluded
  by name from any future auto-approval class (`T1-auto-on-trusted` or any T2 relaxation).
- **Apps live in `/var/lib/agent-os/apps/<name>/` on the image.** Capability sandboxes run with
  `ProtectHome=yes` and an empty root (`modules/cap-sandbox.nix`), so the impl cannot read
  `/home`. The agent account owns `/var/lib/agent-os/apps`.
- `app` is a `path` arg. The broker refuses a non-canonical path or one outside the cap's
  declared roots, but those roots are the READ and WRITE roots together (`bin/broker`, path
  validation), so `app=/var/lib/agent-os/app-approvals` passes the broker. Step 2 below is
  therefore load-bearing, not a second check. A symlink to elsewhere resolves to nothing inside
  the sandbox's empty root (the existing symlink boundary).
- **The manifest fields travel as arguments on purpose.** `bin/confirm` renders the typed
  arguments (the generic sorted `key=value` block, every value scrubbed and line-prefixed).
  Passing each field means the human reads the real permissions, not a name. The agent cannot
  lie in them: the impl refuses unless each one equals the manifest on disk (3.3). List and
  object fields are JSON strings, because the registry has no list type and adding one is a wall
  change this does not need. **Two encoders, kept apart:** the manifest hash is the runner's
  `sha()` over its `canonical()` (`ensure_ascii=False`, unchanged). The per-FIELD encoder is
  `json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`, defined once in
  `agos-run` as `field_json()` and used both by `agos-build` when it prints the call and by the
  impl in step 5, so the two can never disagree on a non-ASCII path.
- **Registry home for invariant A1:** `exclusivePaths = { "/var/lib/agent-os/app-approvals" =
  "app.approve"; }` in `modules/capability-registry.nix`, with a check that fails evaluation if
  any other capability declares a path that conflicts with it (same containment rule as the
  protected-path checks). Neither new path conflicts with an existing protected path, and both
  are canonical, so the entry as written evaluates.

### 3.3 What the impl does (after the broker has a human yes)

The invoke seam passes `{capability, arguments}`; the impl never sees the nonce and does not
need it. The broker reaches the impl only after an approve with an unchanged taint epoch
(`bin/broker` decision flow: nonce, epoch re-check, approve audit, then invoke).

The impl's environment is exactly what `bin/cap-invoke` gives every impl: `PATH` (store-only)
and `AGENT_OS_REGISTRY`. It has no `HOME`. It imports `canonical()`, `sha()`, `NAME_RE` and
`ALLOWED_KEYS` from `agos-run`, and `scrub()`, `MAXPREVIEW` and `render_frame()` from `confirm`,
by path, from copies that `modules/cap-invoke-pkg.nix` places beside `cap-app-approve` (the
`SourceFileLoader` pattern `bin/agos-approve` already uses). Importing `bin/confirm` performs no
I/O (its `main` is guarded).

1. **Uid guard.** Refuse unless `os.geteuid() == 0`. The store's ownership model assumes the
   impl runs as root (capability units run under the system manager with no `User=`). If a later
   change adds `User=agent` to the unit, the store would become agent-writable; this trips first.
2. **Path.** `app` must equal `/var/lib/agent-os/apps/<name>` exactly, with `name` matching
   `NAME_RE` and equal to the `name` argument. Open `manifest.json` with `O_NOFOLLOW`; refuse a
   non-regular file or more than 64 KiB.
3. **Structure only.** The manifest is a JSON object with exactly the eight `ALLOWED_KEYS`. The
   runner's own `validate()` is NOT run here: it is filesystem-bound to the agent's `$HOME`
   (R0 without `HOME`, R11 apps root, R13 every read path must exist), none of which is visible in
   the capability sandbox. The approval binds a hash, not a filesystem state; the runner
   re-enforces every filesystem rule (R4, R5, R11, R13, R14 and the rest) at every run. So an
   approved app can still be refused at run time, which is the safe direction.
   Hand-written manifests that omit optional keys (`devices`, `limits`, `schedule` default
   silently in the runner) are refused here, because a missing `limits` would hide the defaults
   from the human. `agos-build` always writes all eight; anything else is approved locally with
   `agos-approve`.
4. **Hash.** Compute the canonical manifest SHA-256 with the runner's `sha()`. Refuse unless it
   equals `sha256`.
5. **Fields.** Refuse unless each field argument equals `field_json()` (or the plain string for
   `name`, `version`, `schedule`) of the same manifest key. This binds "what the human
   read" to "what is approved".
6. **What the human reads.**
   - Refuse unless `scrub(v) == v` for every argument: the human reads scrubbed text, so a value
     carrying control or bidi characters would display as something other than what is approved.
     A manifest has no legitimate use for those characters.
   - Refuse unless `v.isascii()` for the plain-string arguments (`app`, `name`, `version`,
     `schedule`, `sha256`); the JSON fields are ASCII by construction (`field_json()`).
   - Refuse if any argument is longer than `MAXPREVIEW` (512): the frame would truncate it.
   - Build the exact frame with `render_frame(req, first_time=True, for_getty=True,
     confirm_code="XXXXXXXX")` where `req = {"capability": "app.approve", "tier": "T2",
     "provenance": "TAINTED", "typed_args": args, "destination": None}` (the longest shape the
     human can see), and refuse if it is longer than 4096 characters, one Telegram message. The
     frame renders every argument twice (PAYLOAD and ARGS), which is why the whole frame is
     measured, not the argument block. Telegram counts UTF-16 units; every argument is ASCII
     after the previous check, so `len()` is exact.
   - A refused manifest can still be approved at a local tty with `agos-approve` (break-glass).
7. **Write.** Temp file in the store directory, `os.fchmod(fd, 0o644)` (the unit's `UMask=0077`
   would otherwise leave it 0600 and unreadable to the agent-uid runner, so every approval would
   be invisible), `fsync`, `os.replace`. Entry: `{"<sha>": {"name": ..., "approved_at": ...,
   "via": "confirm"}}`. A corrupt or non-object store is refused, never overwritten (the
   `agos-approve` rule).
8. **Return.** `{ok: true, content: "approved <name> <sha prefix>"}`; every refusal is `{ok:
   false, content: "<reason>"}` with exit 0 (the `cap-invoke` contract). A refusal's content is
   a fixed literal per reason code and never echoes an argument or a manifest byte: because the
   capability is mapped TRUSTED (next sentence), echoed bytes would bypass the taint fence. The content is composed
   by the impl from validated, ASCII-only fields, so the broker maps the capability TRUSTED
   (`ORIGIN_BY_CAP["app.approve"] = TRUSTED`); unmapped, every approval would taint the session.

**Time of check vs time of use.** The agent owns the app directory and can edit the manifest
while the human reads the prompt. Step 4 re-hashes after the human's yes; if the manifest
changed, the hash differs from the approved `sha256` argument and nothing is written. If it
changes after the write, the runner re-hashes at every run and refuses (existing behaviour).
The approval is for a hash, never for a name.

### 3.4 What the human sees

The existing frame, unchanged: `CAPABILITY: app.approve`, `TIER: T2`, `PROVENANCE`, the
`*** NEVER-SEEN-DESTINATION ***` banner, then under "untrusted model input" a `PAYLOAD:` and an
`ARGS:` block, each listing `app`, `devices`, `entry`, `files`, `limits`, `name`, `network`,
`schedule`, `sha256`, `version` one per line, prefixed `│ `.

The never-seen banner appears on every `app.approve` (it has no recipient or URL, so the
destination is empty, and `bin/confirm` treats an empty destination as never seen; `mem.remember`
behaves the same today). The owner's documentation must say so, so the banner is not trained into
noise. A named render shape for `app.approve` in `bin/confirm` (plain-word labels, no duplicate
block, no banner) would be friendlier; it is a separate, later `bin/confirm` PR.

### 3.5 How the agent asks

`agos-build` gains a final line on the image: instead of "run `agos-approve approve <dir>`",
it prints the `app.approve` call with the arguments it computed. The agent's tool loop makes
the call through the broker like any other capability. No new agent tool.

### 3.6 Schedules

`agos-schedule install` requires an approved manifest and reads the same store (3.1). A schedule
is inside the hashed manifest, so approving the manifest approves its schedule. Installing the
timer stays a separate step; whether the agent may install user timers for approved apps on the
image is a follow-up. The timer can only run `agos-run`, which re-checks the approval.

### 3.7 Revocation

Out of scope for the capability. The owner revokes with `sudo agos-approve revoke <name|sha>`
(break-glass). An agent-callable `app.revoke` would only ever reduce authority and could be T1;
it is a follow-up, not designed here.

## 4. Invariants and build assertions

- **A1** Only `app.approve` may hold `/var/lib/agent-os/app-approvals` in any readable or
  writable scope (`exclusivePaths`, 3.2). Negative control: a synthetic second capability
  declaring the path fails evaluation.
- **A2** After a VM approve, the store file is `root:root 0644`, the directory `root:root 0755`,
  and a write as `agent` fails.
- **A3** The image copies of the tools never read `$HOME/.local/state/agent-os/approvals.json`.
  Controls: an approval planted only there does not let `agos-run` start the app, with
  `AGOS_APPROVALS` set to anything or unset.
- **A4** `cap-app-approve` is in `cap-invoke-pkg`'s `shippedCaps` only together with its sandbox
  entry (the existing gate), and the build places `agos-run` and `confirm` copies beside it.
- **A5** A scheduled run reads the same store as a run by hand (schedule-battery arm).

## 5. Test plan

`tests/app-approve-battery.py` (impl, temp store, no broker), each arm with a control:
- correct arguments → one entry written, `via: confirm`, file mode 0644; control: any one field
  changed by one character writes nothing;
- `sha256` correct but a field argument differs from disk → refused;
- manifest edited between "confirm" and the impl run → refused, store unchanged;
- `app=/var/lib/agent-os/app-approvals` (inside the rw root) → refused; `app` with a name that
  fails `NAME_RE` or differs from `name`, symlinked `manifest.json`, oversize manifest, a manifest
  missing an optional key → refused;
- a field containing U+202E or a C0 control → refused (`scrub(v) != v`); control: same field
  without it passes;
- one argument over 512 characters → refused; a manifest whose rendered frame is over 4096 →
  refused, and the same manifest one character shorter in one field passes;
- `geteuid() != 0` → refused. The battery runs unprivileged, so the positive arms need a test
  affordance: `AGOS_APPROVE_TEST_SKIP_UID=1` skips the uid guard, in the same class as
  `AGENT_OS_FILE_SAFE_ROOT`; `cap-invoke` never passes it (its impl env is exactly `PATH` and
  `AGENT_OS_REGISTRY`), and the guard arm runs with it unset;
- no refusal's content contains the offending argument value (fixed literals only);
- corrupt store → refused, file byte-identical afterwards.

Registry: the A1 flake-check negative control. Runner and schedule: the A3 and A5 arms in
`agos-run-battery` and `agos-schedule-battery`. VM test `test-app-approve-confirm` (after the
image wiring): broker + `bin/confirm` with a scripted getty answer. Approve → the app runs; deny
→ refused; manifest edited during the prompt → refused; A2 measured.

## 6. Build order (each a PR, merged in order)

1. **Dev tools honour the store/apps constants and env** (`agos-run`, `agos-approve`,
   `agos-schedule`, `agos-build`), including the schedule unit environment, with the A3 and A5
   arms. Not wall code; normal review.
2. **`app.approve`**: registry entry + `exclusivePaths` check (A1) + `bin/cap-app-approve` +
   battery + `shippedCaps` and the copied helpers (A4) + the one-line `ORIGIN_BY_CAP` entry.
   Wall code: Fable code review before merge.
3. **Image wiring** (note: scheduled units name a store path for `ExecStart`, so a rebuild
   leaves old timers pointing at a collectable path; pre-existing `agos-schedule` behaviour that
   this PR must handle or document): a module that packages the agos tools with the constants substituted,
   creates `/var/lib/agent-os/apps` (agent-owned) and the store directory (root-owned, tmpfiles),
   plus the VM test. Wall-adjacent: Fable code review.

Separate, any time: `bin/confirm` denies any frame over 4096 for every capability (ruling 4).

**Pre-existing gap, not closed here.** In production the agent loop starts `broker` as uid
`agent`, while `cap-invoke` runs impls as transient units under the system manager, which needs
privilege `agent` does not have (no polkit or sudo rule exists in `modules/`). This spec assumes
impls run as root, codifies that with the uid guard (3.3 step 1) and measures it in A2; how the
broker reaches the system manager in production is the T2 go-live slice's problem, and it
blocks PR 3's VM test from being an end-to-end production claim until it is solved.

## 7. Spec-review rulings (Fable, 2026-10-09)

1. **Tier:** T2 is right. No new class; `app.approve` is never-auto by name (3.2).
2. **Render shape:** the generic block is acceptable for v0 given the whole-frame measurement and
   the scrub-equality rule (3.3 step 6). A named shape is a later `bin/confirm` PR.
3. **Entry-source hash in the frame:** no. Code is not approved (the sandbox is the control); a
   code hash would invite the belief that code was reviewed.
4. **Frame size:** yes, as a separate `bin/confirm` PR: any frame over 4096 is denied for every
   capability (fail-closed). Until it lands, the impl's whole-frame measurement is the control
   for this capability.
