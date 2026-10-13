# First login and the setup window (spec, v3)

Status: SPEC, APPROVED by the Fable spec review (four passes, 2026-10-10 to 2026-10-11; the last:
APPROVE, no contradictions). Fable spec review 2026-10-10: approve with required changes (twelve, folded into v1);
re-review 2026-10-11: all twelve confirmed, seven further required changes from the rewrite itself,
folded into v2; final pass 2026-10-11: all seven confirmed, three more required (R1-R3) plus
implementation notes, folded into this v3. Rulings in section 8. No code in this PR. It
turns `docs/design/surfaces-and-first-login.md` §4 into buildable pieces now that the owner has
answered open question 1 there (2026-10-10: "setup window i think"): one system that opens a short,
logged window to fetch the local model and then locks itself, not two systems and a switch.

Security surface (the egress wall's provisioning exception, a root helper fed from the console, the
choice of what the box boots): branch → spec PR → Fable spec review → implementation PRs → Fable
code review for the wall pieces → merge.

## 0. Thesis

Two things have to become true, and they are separable:

1. **Sealing stops being a rebuild.** Today the seal is `agentos.cleanRoom.sealed = true` plus
   `nixos-rebuild switch` into a second closure (`agentos-sealed`). The owner's ruling asks for one
   closure. The sealed ruleset becomes the *only* ruleset the image ever loads; the setup window is
   one element in one named set that the ruleset already refers to. A box that fails to open the
   window, or forgets it was open, is sealed.
2. **First login picks a surface without building anything.** Desktop, terminal and shell are
   chosen from what is already in the store. The desktop is a pre-built specialisation selected
   offline; terminal and shell are a runtime indirection that needs no switch at all.

## 1. What is true today (measured)

- Product path: `nixosConfigurations.agentos` (unsealed) and `agentos-sealed` (the switch target).
  `modules/clean-room.nix` renders ONE nftables table, `inet agentos_egress`, `policy drop`;
  `cfg.sealed = false` adds a provisioning allowance at build time (DNS + 80/443, v4-pinned, not
  scoped to a uid). `modules/seal-check.nix` verifies at boot only that the table exists and that
  `nftables.service` is not failed; it fails loud and recoverable, and its repair text says
  `systemctl restart nftables`.
- nixpkgs `nftables.nix` (pin `624af66`): `reloadIfChanged = true`; `ExecStart` and `ExecReload`
  both run the rules script, which deletes and recreates each declared table. Anything a later
  unit added to that table is gone after any reload, restart, or `nixos-rebuild switch`.
- The product path is tty-only: `configuration.nix` sets `services.xserver.enable = false`, has one
  account (`agent`, autologin on tty1), `users.mutableUsers = false`, plymouth quiet boot
  (`boot-branding.nix`), and imports no desktop module.
- Every surface piece lives in the developer image only (`configuration-open.nix`, which shares no
  module with the product path): `agentos.desktop`, `agentos.desktopShell`, `agentos.terminal`,
  `agentos.shell`, the adapter (`modules/agos_desktop.py`). Each is a build-time option.
- The developer image has no wall, so it has no window either.
- There is no choice store, no first-login program, no `specialisation` anywhere in the repo.
- The model is fetched by the ollama daemon itself (`ollama pull` only asks it to). nixpkgs
  `ollama.nix` always sets `DynamicUser = true`; with `services.ollama.user` set it adds `User=`
  and an `isSystemUser` account with **no `uid`**, so the number is allocated at install (the Dell
  got 992), not known at evaluation. `ids.nix` has no ollama entry.
- `modules/brain.nix` has a root oneshot, `agent-os-pull-model`, with a retry timer; it probes
  `registry.ollama.ai:443` as root.
- nixpkgs specialisations: systemd-boot gets one entry per specialisation per generation;
  `…/specialisation/<name>/bin/switch-to-configuration boot` makes the specialisation a *new
  top-level generation* (whose own `specialisation/` is empty). Nested specialisations are ignored.
- `modules/agos_surface.py` already exists and is unrelated (the self-improvement digest). The
  first-login code must not reuse that name (section 4).

## 2. The setup window

### 2.1 Mechanism

- The base ruleset is the sealed one, always. `clean-room.nix` gains one named set and two rules
  that refer to it; the rule text lives entirely in the reviewed closure and `nft -c` covers it:

  ```
  set setup_uid { typeof meta skuid; }
  meta nfproto ipv4 meta skuid @setup_uid udp dport 53 accept
  meta nfproto ipv4 meta skuid @setup_uid tcp dport { 53, 443 } accept
  ```

  Empty set = no extra accepts = sealed. The two rules sit after the `ct state` and
  `oifname "lo"` rules and before any uid-0 accept. No port 80.
- **The guard (added in slice 3a).** One more rule, *before* the conntrack accept:

  ```
  oifname != "lo" meta skuid <fetch uid> meta skuid != @setup_uid drop
  ```

  The fetcher's uid sends nothing off-box unless it is in the set, on a connection it already has
  as much as on a new one. Without it, flushing the set stops new connections while a download in
  flight carries on, because its packets match `ct state established` first. The slice 3a VM test
  shows exactly that with the rule removed. It is not v4-pinned: a drop scoped narrowly fails
  open. With the guard, "sealed" is true at the wall the instant the set is flushed; restarting
  the fetcher afterwards is tidying, and the seal does not depend on it succeeding.
  tcp/53 is included because the resolver falls back to TCP on truncation, as the existing
  fetch-uid rule already allows.
- Every accept that can be active during the window carries the v4 pin and names exactly one uid
  through the set. There is no uid-blind accept anywhere in the table.
- The only writer of the set is one root unit, `agentos-setup-window.service`. Opening the window
  is one `nft -f` of a Nix-rendered file that adds one element, the model fetcher's uid; closing
  is one `nft -f` that flushes the set. The unit never builds rule text in a shell.
- **The set's content is not durable across nftables reloads; the window unit is the only writer
  and re-derives it from state.** `agentos-setup-window.service` is a oneshot with
  `RemainAfterExit=yes` whose `ExecStart` and `ExecReload` are the same idempotent "recompute from
  state" entry point. It is `After=nftables.service`, `PartOf=nftables.service` (a restart re-runs
  it) and `ReloadPropagatedFrom=nftables.service` (a reload re-runs it). Every run reads the state
  (2.2), returns in well under a second, and ends in exactly one of: set empty, or set = { fetch
  uid }, applied as one `nft -f` ("flush set; add element" is one transaction). A run that cannot
  decide leaves it empty.
- It does no timekeeping while it runs. A separate `agentos-setup-window-tick.timer`
  (`OnUnitActiveSec=1min`) runs the budget write (2.2). The stamp at shutdown is the *window
  oneshot's* `ExecStop`: with `RemainAfterExit=yes` it stays active, so its `ExecStop` runs at
  shutdown (and on a `PartOf` restart, which is the same boot and charges nothing). The tick
  service is inactive between runs, so an `ExecStop` there would never fire at shutdown.
- The tick is also the enforcer of the budget: when it computes `remaining ≤ 0` it applies the
  empty set itself, in the same run, and then starts the seal. Closing the window never depends
  on the seal unit succeeding.
- The tick is not conditioned on the state directory being writable. If it cannot write the
  state (a read-only remount in the middle of the window), it flushes the set first and then
  fails: an enforcer that cannot keep its books closes the window.
- What re-runs it on `switch-to-configuration`: `nftables.service` embeds the path of the rules
  file, so a changed ruleset changes the unit text, `reloadIfChanged` reloads it, and the reload
  propagates. If the window unit's own text changed in the same switch it is restarted instead.
  Both are a same-boot re-derivation and charge nothing (2.2).
- The fetch uid: the ollama daemon gets a static account with an explicit number,
  `users.users.ollama.uid = <N>` (outside the `ids.nix` range), with eval assertions that
  `services.ollama.user == "ollama"` and that the uid is set. The nft file renders from
  `config.users.users.ollama.uid`.
- The `agent-os-pull-model` probe stops running as root: it runs as the ollama uid (or is dropped),
  so the window's subject is the only uid that ever needs the registry.
- Sealing is `agentos-seal.service` (2.4). Its sequence is fixed: (1) flush the set, (2) restart
  the ollama daemon so it comes up with the set already empty, (3) stop the `agent-os-pull-model`
  service and its retry timer, (4) write `sealed`. Both pull units carry
  `ConditionPathExists=!/var/lib/agent-os/seal/sealed`, so they cannot start again while the
  marker exists. (v3 said "stop and mask". A runtime mask is gone after a reboot and the timer
  would be back; the condition holds across reboots. Changed in slice 3a.) Flushing an empty set is a no-op, so
  sealing is idempotent. It also carries `ExecStopPost=` flush-and-write-`sealed` (reason:
  timeout), so a seal that fails or times out still leaves the set empty and the box marked.
- `nixos-rebuild` is not involved. `agentos.cleanRoom.sealed` stays for one release with a changed
  meaning (`true` = never open the window on this box: the window unit is masked, not merely
  skipped) so `agentos-sealed` keeps evaluating until the product path moves over (slice 4).

### 2.2 State

One root-owned directory, `/var/lib/agent-os/seal/` (0700 root). The window oneshot carries
`RequiresMountsFor=/var/lib/agent-os` and `ConditionPathIsReadWrite=/var/lib/agent-os/seal` (the
tick and the seal unit carry only the mount requirement, see 2.1 and 2.4): if
the state cannot be written (read-only filesystem, no space), the window does not open. Writes
are temp file, `fsync`, `rename`, then `fsync` of the directory.

| File | Content |
|---|---|
| `window.json` | `schema`, `remaining_seconds` (the budget), `opened_at` and `wall_deadline` (advisory), `boots` (count). One file, one atomic write. |
| `sealed` | The store path of the running toplevel, the sha256 of the rendered ruleset file, UTC time, the reason, and the boot id of the boot that sealed. Never removed by any unit. |
| `recorded.json` | A pointer: where in the audit chain the tick found this marker's seal (offset and hash of the line; 2.7). Checked against the chain on every use; losing it costs one scan. |

The window is measured as a **budget of open seconds**, not as a wall-clock deadline. A box with
no RTC battery boots near 1970 and time sync is itself behind the wall, so a deadline written from
that clock would be wrong for the life of the box.

- The budget starts at the window length (2.3). `window.json` also holds the kernel boot id
  (`/proc/sys/kernel/random/boot_id`) of the boot that last opened the window, and
  `budget_at_boot`, the budget as it stood when that boot opened it.
- Remaining is always *recomputed*, never successively subtracted:
  `remaining = budget_at_boot − max(60, open_seconds_this_boot)`, where `open_seconds_this_boot`
  is `CLOCK_MONOTONIC` time since the window opened in this boot (not since the boot started). The tick writes that value once a minute and at shutdown; writing
  it twice gives the same number.
- A run under the same boot id is a re-derivation (an nftables reload, a rebuild) and charges
  nothing. A run under a new boot id first settles the previous boot at its last written value,
  then starts a new `budget_at_boot`.
- The 60-second floor means every boot that opens the window costs at least a minute, so a reboot
  loop cannot hold it open on sub-minute boots. A power loss loses at most the 59 seconds since
  the last tick, which the next boot's floor bounds.
- The wall-clock deadline is a second, closing-only limit. It is recorded only if the clock was
  believable (later than the image's build date, compiled in) when the window first opened, and
  honoured only if it is believable now. A clock earlier than the build date is "unknown": the
  budget alone decides, so a box with no RTC battery still gets its window. (v3 also said a clock
  far in the future "cannot shorten a window". That was wrong as written: if the window opened
  under a believable clock and the clock later jumps past the deadline, the window closes early.
  That is the safe direction, and it is what slice 3a implements. The budget is immune to clocks
  in both directions.)

Decision table, evaluated on every run of `agentos-setup-window.service`:

| `sealed` | `window.json` | Action |
|---|---|---|
| valid | any | Set empty. Nothing else; nothing is deleted. |
| absent | absent | First boot: write `window.json` with the full budget and this boot id, record, open. |
| absent | valid or absent, and the record or the open is refused | Seal at once, reason `refused` (2.7). |
| absent | valid, same boot id, remaining > 0 | Re-derivation: open, charge nothing. |
| absent | valid, new boot id, remaining > 0, wall deadline not passed (or clock unknown) | Settle the previous boot, open for the remainder. |
| absent | valid, remaining ≤ 0 or wall deadline passed | Seal. |
| present but malformed | — | Wall DOWN (seal-check's response); do not open. |
| absent | unreadable or malformed | Seal, and say so on the console. |
| absent | any, but the state directory is not writable | The unit is skipped by its condition; the set stays empty; seal-check (e) treats the box as sealed-or-down until a human fixes the filesystem. Intended. |

**A reader never raises, and a role that raises anyway fails closed** (added in slice 3c, from the
commit security review). Every role reads its state before it closes or opens anything, so an
exception in a reader is a role that died with the set as it was: an open window with no
enforcer. The first version could be made to raise by a number too large to become a float
(`10**400` is valid JSON) and by a pointer offset too large to seek to; the wait's unit can
write these files. (Nesting deeper than the parser allows is the third way a parser raises
something that is not "bad JSON". On the Python this image ships the files' size cap keeps the
depth far below that limit; the catch is general so that it does not depend on that.) Now every loader returns a verdict
whatever it is given, every number is bounded before it is used, and each role is wrapped so
that any exception at all ends with the set flushed and the role failed.

"Valid" for `sealed` means: the schema parses, every field is well formed, and the toplevel is a
store path. It does not mean the ruleset hash matches: a rebuild legitimately changes it, there is
no ledger of past rulesets, and garbage collection removes old generations. A mismatch is logged
(`sealed-generation-changed`) and is not a failure. A well-formed forged marker therefore cannot be
told from a real one; all it can do is seal the box, which is the safe direction.

Every transition appends one line to the audit chain: opened (budget), reopened after reboot
(remaining), re-derived after an nftables reload, sealed (reason: complete, budget, deadline,
invalid state), plus the set's element count.

### 2.3 Window length; no reopening in v0

- Length: `agentos.cleanRoom.setupWindowSeconds`, default 7200 (2 hours; owner question A), with an
  eval assertion that it is at most 4 hours.
- A box that seals on the budget without a model still boots and says, on tty1, that it has no
  model.
- **There is no reopen command and no reopen capability in v0** (ruling 2). Re-provisioning is a
  reinstall, or a human with root on the break-glass console (tty3) removing the two state files (a leftover `recorded.json` names the old seal and is ignored)
  and rebooting, which the table treats as a first boot. That path is root-only, needs no new
  code, and is what the console message points to.

### 2.4 The agent does not exist while the window is open

The fetcher is the model server (ruling 3): while the window is open, the daemon that will serve
the agent can reach DNS and 443. The agent can talk to that daemon over loopback, and
`/api/pull <name>` sends attacker-chosen bytes to an attacker-chosen host. So the two must never
overlap:

- `agentos-seal.service` is in every boot (`WantedBy=multi-user.target`), and the agent's session
  (`getty@tty1` autologin, and any desktop session) carries `Requires=` and `After=` on it. Unit
  ordering is static, so the seal unit is always scheduled: on a sealed box it exits at once.
- **tty1 is not the only door (found while building slice 3b; v3 named only tty1).** nixpkgs bakes
  `--autologin agent` into every getty template, so a second virtual terminal (logind spawns its
  getty on a switch), a serial console (the normal console of a VM) and a container console all log
  the agent in. And logind starts the agent's user manager at boot, with no login at all, if
  lingering was ever enabled for the account, which an account may do for itself; the agent's
  scheduled apps are systemd user timers in that manager. Every one of these runs code the agent
  may have written in an earlier life (a shell profile, a timer), and the window can be open again
  after a re-provisioning. So:
  - one positive token, `/run/agentos-setup/login-ok`. Only the window program writes it, under
    the state lock, after a valid `sealed` marker and a flush nft confirmed. `/run` is a tmpfs:
    every boot starts without it. Absent means closed;
  - every agetty's login program (`services.getty.loginProgram`, read by all the templates) is a
    gate that execs `login` only if the token is there. The break-glass console (tty3) spells out
    its own login program and is not gated: it is root's, and it must work while a window is stuck;
  - `user@.service` carries `ExecCondition=+<gate> %i`: uid 0 passes, every other uid needs the
    token. A skipped user manager is not a failure; logind starts it again at the login;
  - **once the token exists the window cannot open again in that boot.** `recompute` refuses to
    open, and the seal itself refuses to write a new marker, whoever asks (the tick on a spent
    budget, the wait a respawning getty runs again, a timeout). A human who removes the state
    re-provisions the box at the next boot, never under a running agent, and nothing undoes it
    in between.
- tty1's dependency is declared from the seal unit's side (`RequiredBy=` and `Before=`), not as a
  drop-in on `getty@tty1`: nixpkgs writes every drop-in as `overrides.conf`, and an instance
  drop-in of that name masks the template's, which holds the getty's whole command line. The VM
  test caught the first version doing that.
- The seal unit carries **no** `Condition*=` (2.2 said it would carry `ConditionPathIsReadWrite`;
  that was wrong): a unit skipped by a condition counts as started for everything that `Requires=`
  it, and the agent's getty would follow a wait that never ran. A state directory that cannot be
  written makes the wait fail, because it cannot write the marker.
- A window that is not open in this boot (the window unit failed, was skipped, or refused) is
  sealed by the wait at once, reason `not open`: nothing can fetch a model, so there is nothing to
  wait for.
- The wait ends at the first seal it attempts. A seal that fails leaves the unit failed and the
  agent withheld; it does not loop, which would restart the fetcher every few seconds.
- `ExecStopPost` seals in one case only: the wait timed out (2.1 said "fails or times out"). systemd
  stops the wait at every shutdown, and a seal there would turn every reboot inside the window into
  a sealed box with no model; the slice 3b VM test caught exactly that. A wait that is killed or
  crashes leaves the window to the tick. The agent is absent in every one of those cases, because
  only a seal writes the token.
- On a boot where the window is open it *blocks* until one of two things: the model is present
  and first login has finished, or the budget is gone. Its `TimeoutStartSec` is the window length
  plus a margin. "Model present" is decided by one detector: the daemon's own `GET /api/tags` on
  loopback lists the configured model.
- It waits on first login by ordering: `After=agentos-surface-apply.service`. (On the product
  path that unit does not exist until slice 4 brings first login there; until then the ordering
  is inert.)
- "Sealed" means no egress and not "no new egress": the guard rule (2.1) drops the fetcher's
  off-box packets the moment the set is flushed, including on a connection opened inside the
  window. After the flush the seal also restarts the fetcher (`systemctl restart ollama.service`,
  step 2 of the sequence in 2.1) so that it does not sit retrying, but the seal's truth does not
  rest on that restart. A
  half-pulled model is absent from `/api/tags`, so the detector cannot mistake it for a model.
- tty1 between the first-login questions and the agent's getty is owned by one small unit,
  `agentos-setup-progress.service` (`Type=oneshot`, `TTYPath=/dev/tty1`,
  `After=agentos-firstlogin.service`, `Before=getty@tty1.service`, unprivileged, output only). It
  cannot read the state directory, so the window program writes one display-only line for it,
  `/run/agentos-setup/status`; nothing decides anything from that file. The download's progress
  text comes from the fetcher, so only printable ASCII of it reaches the terminal. It
  exits when the status says sealed, so tty1 never has two owners. (Known limit: the status write
  is best effort. If that one write failed while the seal succeeded, tty1's getty would wait for
  the progress unit's own limit, the window length plus eleven minutes.) It prints the download progress, and if
  the budget ends without a model it prints that the box has no model and how a human re-provisions
  it (2.3). It reads no input.
- There is no cycle: the first-login unit is before the getty and not after the seal; the seal
  waits on first login; the getty waits on the seal.
- This is an invariant with a test: no process of the `agent` uid exists while `setup_uid` is
  non-empty.

### 2.5 What seal-check asserts

(As built in slice 3b part 2. The first paragraph of each item is v3's; the differences are marked.)

`seal-check.nix` gets a third variant for a box that seals itself, and it is of the **sealed
class**: a wall that is DOWN gets the sealed response (links down, NetworkManager masked, the
agent withheld), not the developer image's banner. It reads the **kernel's** ruleset itself and
asserts:

- (a) nftables did not fail and the table is present;
- (b) the loaded ruleset is the reviewed one: the set `setup_uid` is declared once and exactly
  three rules refer to it, the two accepts **to the letter as nft prints them** (v4 only, DNS and
  443 only) and the guard that names the fetcher's uid. (v3 said "its two rules"; the guard came
  with slice 3a. It is a check of those four lines, not of the whole table.) Anything else: wall DOWN;
- (c) `sealed` valid ⇒ the set is empty; otherwise DOWN;
- (d) `sealed` absent and the set holds the fetcher's uid ⇒ the window unit is running and
  `window.json` is valid (the program writes its books before it opens); otherwise DOWN. (v3 said
  "otherwise seal and re-check". seal-check is not a writer of the set or of the marker, and must
  not become one: the tick and the wait seal.)
- (e) `sealed` absent and the window unit failed or never ran ⇒ the set must be empty. No token:
  the box is sealed-or-down, never "open";
- in every case the set holds nothing but the fetcher's uid, and a malformed marker is DOWN.

The state is read **before** the set: the marker is written after the flush, so "marker valid",
read first, can never be followed by a legitimately non-empty set.

**Two keys.** `/run/agentos-sealed-ok` is written only when (c) holds. The window program writes
`/run/agentos-setup/login-ok` when *it* has sealed (2.4). Every door to the agent's account
needs both, so a bug in the program's own bookkeeping cannot certify itself. The tty1 guard in
the login shell is the sealed one as well.

**Two runs per boot.** Early, as today (after nftables and the window unit, before the network),
so a wall that is down is answered before anything is online; on a boot that begins with the
window open this run passes and writes no token. And again in `agentos-seal-check-final`, after
the wait and before tty1's getty, which is when (c) can first hold. That unit is required by
tty1's getty (declared from its own side, 2.4) and is not `RemainAfterExit`, so it runs again
whenever that getty starts.

The token is not removed at the start of a run: the check runs again at every start of tty1's
getty, and a healthy sealed box must not lose its second key for the length of a run. Instead
every way out of the check that did not prove (c) removes it, in an exit trap: a DOWN, the "not
sealed" exit, an error, a kill by the start timeout. What a trap cannot see (SIGKILL, a script
that cannot start) leaves the unit failed, and the backstop removes the token. In this variant
the backstop is not `RemainAfterExit`: a unit that stays active runs nothing when it is started
again, and a backstop that fires once per boot is not one here. (The second review pass caught
exactly that.)

Two consequences worth knowing. After a re-provisioning (root removes the marker under a running
agent) the next start of tty1's getty finds no marker, so the second key goes and the agent's
account cannot log in again in that boot; the re-provisioning wants a reboot anyway. And a DOWN
found while an operator restarts nftables by hand (the stop deletes the table before the start
loads it) is a false one: the key comes back at the next check, but NetworkManager stays masked
until root unmasks it. A reload, which is what a rebuild does, is atomic.

**The repair path.** On a DOWN, tty1 itself shows nothing (its getty requires the check, and the
progress line has stopped); the line on the live console and the banner on every other console
point at tty3. On the build-sealed variant a DOWN wall leaves a bare shell on tty1 as the
agent's account. Here that account does not log in without both keys, so the repair path is the
break-glass console (tty3), and every other console prints the banner, which says so. A box with
no break-glass password provisioned and a DOWN wall is repaired from installer media. That is a
real cost of this variant and belongs in the product decision of slice 4.

A box built with `agentos.cleanRoom.sealed = true` has none of the window's units (v3 said
"masked"; slice 3a made them absent) and never writes a `sealed` file. seal-check treats that as
sealed-by-build: the build flag stands in for the marker, and if the image was built with the
window's ruleset the set must be empty for the token to be written.

When the marker's ruleset hash differs from the running generation's, seal-check logs
`sealed-generation-changed` (not a failure: a rebuild legitimately changes it) and rewrites
nothing. Its banner says that restarting nftables also re-runs the window unit, and how to run
the check again.

### 2.6 What this changes about trust

- Before: the unsealed closure allowed DNS + 80/443 to every uid, including the agent's, until a
  human rebuilt. After: the agent's uid never gets an accept rule, and never runs while one exists.
- Before: forgetting to rebuild left the box open forever. After: forgetting leaves it sealed.
- The cost: the seal is now a runtime fact held in `/var/lib`, not a property of the closure hash
  alone. A verifier reads the store path (which contains the wall and the set's two rules) plus
  the `sealed` marker, which names the ruleset it sealed under. The README wording must say so.
- Not defended: root deleting the state. Root already owns the box.
- Accepted: a wiped `/var/lib` looks like a first boot and opens one window of the configured
  length, for the fetch uid only, with no agent running.

### 2.7 The record (slice 3c)

2.2 says every transition of the window appends one line to the audit chain. This section says
how: the chain had one writing unit until now (the wall), and it may be signed. The shape below
is the proposal as changed by the spec ruling of 2026-10-13 (the changes are marked "ruling").

**What is recorded.** Four transitions, one record each:

| `event` | when | fields beyond the common ones |
|---|---|---|
| `window-opened` | first boot: the window is about to open with the full budget | `remaining`, `budget_at_boot`, `boots` |
| `window-reopened` | a later boot is about to open it with what was left | `remaining`, `budget_at_boot`, `boots` |
| `window-rederived` | same boot: the set is about to be put back after an nftables reload or restart | `remaining`, `budget_at_boot`, `boots` |
| `window-sealed` | the box sealed itself | `marker`: what the marker says (`reason`, `sealed_at`, `boot_id`, `toplevel`, `ruleset_sha256`) |

Common to all four (ruling): `"src": "setup-window"`, `schema`, `boot_id` (what the state is keyed
on, and what tells reopened from re-derived), `fetch_uid` (the subject that had egress, as a
number), `toplevel` and `ruleset_sha256` (the generation and the ruleset the record speaks for),
and `elements`: how many uids the **kernel's** set held when the record was written, read back
from nft, never assumed. These are always what the **writing role measured itself** at the
moment it wrote. (`remaining`, `budget_at_boot` and `boots` on the opens are not measurements:
they are what the program decided from its books, `window.json`, bounded by the window length.
A forged book can make them wrong about history; it cannot put anything but bounded numbers
there.) For the seal that role is the tick, possibly in a later boot and from a later
generation than the seal; what the *marker* says about the seal is quoted under `marker`, as the
marker's. (Found by the commit security review after the code review: the first version copied
the marker's fields to the top of a signed record. The marker can be written by the wait, the
one role that reads bytes from a process that has been on the network, so a file's claims came
out as the signer's own.) The marker is validated field by field before any of it is quoted: a
finite, non-negative time (a NaN equals nothing, itself included, and would have been recorded
again every minute), a boot id of letters, digits and dashes, a toplevel of printable
characters, each with a length limit. For the three opens that is the state before the open: the record says
the program authorised the open and is about to attempt it. (So on a run with no reload and no
reboot no record ever says 1: the opens say 0 and the seal says 0. The chain witnesses that the
open was authorised and that the box sealed. A re-derive after an nftables reload says 0 too:
the reload is what emptied the set. A non-zero count appears only when the window unit is run
again with the set still full, for instance restarted by hand.) The marker's `reason` is one of exactly seven: `complete`, `budget`, `deadline`,
`invalid state`, `timeout`, `not open`, `refused`. On a box with no clock battery `sealed_at`
and the chain's own `ts` may be near 1970; the order of events is `boot_id` and the chain's
`seq`, not the times. Nothing in a record comes from the model server's answers, except that a marker's
`reason: complete` is the wait's conclusion from one, quoted as the marker's.

Refusals and closes that are not a seal (a re-provisioning refused until the reboot, a tick that
closes because it cannot keep its books, a wall that seal-check finds DOWN) stay in the journal.
One of them, "the state was removed under a running agent", is security-relevant rather than
operational and is a candidate for the chain in slice 4. So is this one: the login token
appearing while the window is open (only something already root in the wait's unit could do
that). It closes the window at the next reload and no marker is ever written, so the chain shows
an open with no seal and only the journal says why; logins stay shut all the same, because the
second key is seal-check's.

**Who writes.** The window program, through the same pinned `audit` wrapper the wall uses (one
derivation, `modules/audit-pkg.nix`: the same ledger, modules and identity root). The chain
already has more than one writing program (the broker and the taint tracker), and `audit append`
serialises on its own lock. The window program takes nothing from the agent or the network for
what it records, which makes it a less exposed writer than the wall. Ruling: **exactly two
units append**, the window oneshot (the opens) and the tick (the seal). The wait
(`agentos-seal`) never appends and gets no access to the audit directory: it is the one unit
that parses bytes from a process that has been on the network, and it must always be able to
run. Rejected: a wall verb (the launcher refuses a root peer by design), a ledger of its own
(not "the chain"), journal only (the one tamper-evident witness that egress existed on a box
that now says it is sealed would be missing).

**Who signs.** The same participant as the wall, or nobody if the wall signs nothing. The
window's signer and pin are read from `agentos.wall.auditSigner` / `auditRequireSigned`, compiled
into the program's configuration, left out when null, and handed to the `audit` child in its
environment explicitly (ruling: the program runs its children with an empty environment, so
without this every window record on a signed box would be unsigned and `audit verify` would
fail the chain from there on). One signer keeps `AGENT_OS_AUDIT_REQUIRE_SIGNED=<participant>`
true of every record. Reading the key is not a new grant: these units are root, and root reads
its own files. The two appending units `Require=` and follow the identity boot unit, as the wall
does: a box whose identity failed its self-test gets **no window** (the set stays empty; the
wait seals `not open`), never a record signed with whatever key is on disk. A window record may
be the first record of the chain.

**What a failed write means.**

- *Opening*: no record, no window. The order is books, record, set, under the state lock.
  Ruling: if the chain refuses the record, or nft then refuses the open, the box is **sealed at
  once**, reason `refused`. Before this slice an open that nft refused left the books saying
  "open" over an empty set, and the box waited out its whole budget (two hours) before it
  sealed with no model; a refused record would have inherited that. **Any** refused record does
  this, a transient one as much as a corrupt chain: an append that times out, a lock file that
  cannot be opened, a set that cannot be measured. The box is then sealed with no model until a
  human re-provisions it. That is the price of "no record, no window", and the ruling accepted
  it: the way back is the one 2.3 names (root on the repair console repairs the log if it needs
  it, removes the state, reboots). A refused boot is charged one floor of budget. One case still
  waits out the budget: nft refusing the open *and* the flush, where no marker can be written
  (the marker never lies); that is a wall seal-check calls DOWN.
- *Sealing*: never waits for the record. Whoever seals (the wait, its timeout backstop, the
  tick, the window oneshot, a `seal` typed by root) writes the marker, as before. Ruling: **the tick reconciles**. It runs once a minute on a
  sealed box too, and appends `window-sealed` built from the marker, so the record is at most a
  minute behind the seal. Whether that has been done is read from the **chain** (a read-only
  scan for a `window-sealed` whose `marker` has this marker's `boot_id`: a box seals at most
  once per boot, so that is the honest count, and a marker rewritten with a new time does not
  get a second record).
  `seal/recorded.json` is a **pointer**, not a statement: the offset and the hash of that line,
  checked against the chain every time it is used. (The first version stored only "recorded",
  which let a side file decide whether a security record is written: a chain that later lost
  the record would have stayed without it. Found by the commit security review.) The tick that
  appends writes no pointer; the next tick finds the line and writes it. So the record can be
  late (the chain is broken: the tick keeps trying, quietly), never lost (the debt is the
  default state of "valid marker, no such record in the chain") and never written twice (the
  scan comes before every append, under the state lock). The marker
  gains `boot_id` for this. Nothing about logins waits for the record.

The file-as-queue of the first draft (`audit-owed.json`) is gone: it could be unwritable, it
could duplicate after a crash, and it could grow.

**Time under the lock.** One append is limited to 10 seconds (`AUDIT_LIMIT`), and so is the
scan of the log for a seal's record (one scan per tick at most: the tick that appends does not
look for what it wrote) (`SCAN_LIMIT`; a log too long to read in that time is also
too long for `audit append`, which reads all of it, and the record then stays owed). While the
window is open nothing else writes the chain, so longer is a failure and not a queue. With every
tool at its limit the longest holders of the state lock are: a seal, 20 + 45 + 2 x 10 = 85
seconds (the review of this slice found the earlier limits let a refused open reach 170, past
the 150 the others wait; the restart and stop limits were shortened, and killing `systemctl` at
its limit does not cancel the job it queued); a recompute whose open is refused, 20 + 10 + 20
+ 85 = 135; a tick that seals and then records it, 20 + 85 + 10 + 20 + 10 = 145. The battery
pins the table to the constants, and the program computes the same bound from the units it is
configured to restart and stop and refuses a configuration that would not fit.

**The broker's anchor.** The broker writes the head hash of the chain outside the audit
directory after each of its own appends. Records appended by the window (as by the taint
tracker) move the head without moving the anchor; the anchor remains a hash that must be found
*in* the chain, which is what it is for.

**Step 7 of the audit plan** (a dedicated owner for the audit directory, `chattr +a` on the log):
append-only is compatible with every writer. A dedicated owner is not, as planned: the two
appending units are root with one capability and write only as the owner. `bin/audit` now names
the writers and says Step 7 must give the directory a group they are in.

**The surface choice.** Slice 1 owes the same treatment to its "store written" line. That store
is written by a root helper on the developer image, which has no audit chain; the line moves to
the chain when the surfaces move to the product path (slice 4), through the same wrapper. The
readers' fallback lines are written by processes of the agent's account, which cannot and must
not write the chain; they stay in the journal.

**After this slice** the flake assertion that kept the window off shipped images is replaced by
assertions on the structure above, which hold for any configuration that enables it. Turning it
on for a shipped image is slice 4's decision.

## 3. The surface choice

### 3.1 Store

`/var/lib/agent-os/surface.toml`, root 0644, written atomically:

```toml
schema = 1
desktop = "hyprland"
terminal = "kitty"
shell = "bash"
```

One reader (python, stdlib `tomllib`) used by everything else. It opens the file with
`O_NOFOLLOW`, requires a regular root-owned file on the root filesystem (`st_dev` check) of at
most 4 KiB, and validates against closed lists compiled in at build time. Anything else: the
defaults, plus one audit line. It never returns a string that did not come from its own closed
list, and validation happens before any value reaches an `exec`.

### 3.2 Desktop: specialisations, selected without a new generation

- The image defines one NixOS `specialisation` per offered desktop other than the default. All are
  in the closure when the image is built, so all are in the store before the wall exists.
- Applying a choice does **not** run a specialisation's `switch-to-configuration boot`: that turns
  the specialisation into a new top-level generation, grows the boot menu by generations × (1 +
  desktops), and leaves the default desktop reachable only through a parent that garbage
  collection may delete.
- Instead a root unit, `agentos-boot-default.service`, selects the boot entry that already exists
  for the chosen desktop's specialisation: `bootctl set-default <entry>` (or clears it for the
  image's own desktop).
- **Corrections from the slice 2 proof (2026-10-11).** Two things in v0-v3 of this spec were wrong.
  - *Entry names.* The spec assumed `nixos-generation-<N>-specialisation-<name>.conf`. On this pin
    an entry is named by a hash of its own content (`nixos-<sha256>.conf`, title `NixOS (<name>)`),
    so the name cannot be derived from the desktop. The entry is found by what it boots: the one
    whose `options` line has the exact word `init=<specialisation toplevel>/init`. The desktop
    name comes from the validated store and a closed list. The hash is a function of the
    generation, so **an old generation's entry keeps its name and stays in the menu** (until
    `configurationLimit` prunes it): a default left pointing at one goes on booting that old
    generation. It does not go stale by itself.
  - *Which generation.* The spec said "the booted generation". That pins the box: after a rebuild
    the machine is still running the old generation, the unit would re-select the old
    generation's entry, the next boot would be the old generation again, and no update (security
    updates included) would ever land. The generation is the one **the system profile points
    to**, which is what `loader.conf`'s own default tracks: the chosen desktop follows the box's
    updates exactly as an unmodified NixOS follows them. An old entry picked by hand in the boot
    menu is a one-off, as it is on any NixOS.
- When the chosen desktop has no entry in the profile's generation (a rebuild that no longer
  offers it), the default is **cleared**, so the box boots the new generation's own desktop. It is
  never left on an older generation's entry.
- **A later generation with first login turned off** installs none of this, and the default it
  inherits still names the older generation's entry, which still exists. So the module, when
  first login is off, makes the boot-loader install clear a default that names a NixOS entry.
  (A generation built without the module at all cannot do that; there the older generation boots
  once more, its own per-boot unit finds the chosen desktop missing from the profile's generation
  and clears the default, and the following boot is the new generation.)
- The variable is written only when it would change: no NVRAM write on every boot, and an install
  onto a fresh machine does not delete a default this box never set.
- The re-select runs in two places: the per-boot unit, and
  `boot.loader.systemd-boot.extraInstallCommands`, which both `nixos-rebuild switch` and
  `nixos-rebuild boot` run after the profile points at the new generation and its entries exist.
  That closes the `boot` case this spec previously accepted as "one boot on the wrong default".
- A desktop chosen on this boot that differs from the one running is entered by rebooting, once,
  before the agent's getty: the root helper leaves a marker in `/run`, and the per-boot unit
  (`--at-boot`) removes it and reboots. Only that unit honours the marker: the same program
  running during a rebuild never reboots the box.
- Only systemd-boot has the entries this selects among. With any other boot loader (GRUB on UEFI
  has EFI variables but no `/boot/loader/entries`) only the image's own desktop is offered and no
  specialisation is built.
- `bootctl set-default` writes the EFI variable `LoaderEntryDefault`, which takes precedence over
  the `default` line in `loader.conf`. nixpkgs rewrites `loader.conf` on every boot-loader install
  and does not touch the variable, so after a rebuild the variable still names the OLD
  generation's entry, and that entry still exists: the re-select at install time is required, not
  belt and braces.
- Only when the variable names an entry that no longer exists (its generation was pruned) does
  systemd-boot fall back to the `loader.conf` default. That is the un-specialised entry of the
  newest generation: the image's own desktop. The fallback is safe because of what nixpkgs writes
  there. It is the rare case, not the mechanism: see the bullets above for the common one.
- UEFI only in v0: the unit carries `ConditionPathExists=/sys/firmware/efi/efivars`. On a box
  without EFI variables (or with GRUB) the desktop choice is not offered and the box runs the
  default.
- `boot.loader.systemd-boot.configurationLimit` is set, and stated.
- `boot.loader.systemd-boot.configurationLimit` may remove the generation the variable names; the
  per-boot re-assert covers it.
- The agent cannot influence what boots: `/boot`, the EFI variables and
  `/nix/var/nix/profiles` are not agent-writable (asserted, tested), and the unit is not startable
  by the agent (no polkit rule, not a broker capability in v0).
- Offered in the product image in v0: `none` (default) and `hyprland` (with Noctalia, the developer
  image's default since #339). sway, Plasma, GNOME and COSMIC stay developer-image choices until
  the closure budget is set (owner question B).
- The seal step refuses if the chosen desktop's specialisation is not in the running closure.
- This selection mechanism was the part of the spec least proven on paper. Slice 2 demonstrates it
  in a UEFI VM with a real systemd-boot (`tests/surface-boot-default.nix`, eight legs): a
  first-login choice boots the chosen variant with no new generation and before the agent has a
  session; a later change takes effect at the next boot; the image's own desktop clears the
  default; a stale default falls back to the image's own desktop and is re-selected on that boot;
  the agent cannot change any of it; **a rebuild in the middle** (a second generation installed
  with `switch-to-configuration boot`) moves the default to the new generation's entry at install
  time and the next boot runs the new generation; and running the old generation by hand does not
  pin the box to it; and **a later generation with first login turned off** clears the default
  at install and is what boots next. NOT demonstrated: `nixos-rebuild switch --rollback` (the profile moving
  backwards; the same code path as a rebuild, in the other direction), and real desktops (in the
  test node a desktop is a variable). The fallback if the mechanism ever fails to hold is the
  generation switch with a GC root for the parent, recorded here so the choice is not relitigated
  from scratch.

### 3.3 Terminal and shell: no switch at all

- `agentos-terminal`: a wrapper that asks the validator for the terminal and `exec`s it from a
  compiled-in table of store paths. The desktop configs bind "open a terminal" to this wrapper by
  absolute store path, never by a name on `PATH`.
- `agentos-shell`: the same shape. It is the login shell of the human account and `exec`s the
  chosen shell as a login shell. If the store is unreadable it runs bash.
- Both ignore the environment for the *decision*: `SHELL`, `PATH`, `ENV`, `BASH_ENV`, `ZDOTDIR`,
  `XDG_CONFIG_HOME` are passed through to the program they start but never consulted.
- The agent account is untouched: bash, the tty1 loop, kitty for the brain window.
- home-manager is not needed for this and stays out of v0.

## 4. The first-login program

Split in two (ruling 4), so that no root process reads keyboard bytes:

- **Front end**, `agentos-firstlogin.service`, runs as a dedicated unprivileged user
  (`agentos-setup`, not `agent`) on tty1. Three numbered questions (desktop, terminal, shell), each
  showing its default; Enter takes the default; `s` skips the rest. It writes three tokens to one
  0600 file and exits. It has no `exec`. On the idle timeout it writes the default tokens, so the
  helper always has a file to read.
- The file lives in the unit's `RuntimeDirectory=` (root creates it, owned by `agentos-setup`,
  0700) with `RuntimeDirectoryPreserve=yes`: systemd removes a runtime directory when its unit
  stops, and a oneshot front end stops the moment it exits, which is before the helper reads.
  The helper unlinks the file after its one read.
- The `agentos-setup` account: a system user with no shell (`nologin`), no home, no groups (not
  `tty`, `video` or `input`), no sudo or polkit rule. The unit is sandboxed: `PrivateNetwork=yes`,
  `ProtectSystem=strict`, `NoNewPrivileges=yes`, an empty `CapabilityBoundingSet=`. It needs no
  `tty` group: systemd opens `TTYPath` as root before dropping privileges. `TTYVHangup` and
  `TTYVTDisallocate` run after it exits, so the token file is the only thing that crosses to root.
- **Root helper**, `agentos-surface-apply.service` (`Requires=` and `After=` the front end), reads
  only that file, with the same discipline as the store reader (3.1): `O_NOFOLLOW`, a regular file
  owned by the `agentos-setup` uid, at most 64 bytes, on the `/run` filesystem, read once. It
  accepts only three tokens each present in its compiled-in list and writes the store. It parses
  nothing else. The only principal who can race the file is `agentos-setup` itself.
- The helper does not touch the bootloader. Turning the stored desktop into a boot default is a
  different root unit (`agentos-boot-default.service`, 3.2) that reads only the validated store.
  One unit parses a user-fed file; another writes EFI variables; neither does both.
- Input handling in the front end: raw bytes, at most 16 per line; a line containing ESC or any
  other control byte is invalid; bracketed paste is invalid; the input queue is flushed
  (`tcflush`) before each prompt; a line is accepted only if it is empty, `s`, or a digit within
  the menu's range. Nothing typed is stored or logged.
- A 5-minute idle timeout takes the defaults, so an unattended first boot still finishes and the
  setup window still closes. `TimeoutStartSec` of 6 minutes is the systemd backstop.
- The unit: `After=plymouth-quit-wait.service`, `Before=getty@tty1.service`,
  `ConditionPathExists=!/var/lib/agent-os/surface.toml`, `StandardInput=tty`,
  `StandardOutput=tty`, `TTYPath=/dev/tty1`, `TTYReset=yes`, `TTYVHangup=yes`,
  `TTYVTDisallocate=yes`.
- Later changes: a human at the break-glass console runs the same pair. Terminal and shell take
  effect at the next terminal or login; a desktop change takes effect at the next boot.
- The text program is the only program in v0 (open question 8 of the parent design); a graphical
  front end, if built, writes the same three tokens.

## 5. Slices

| # | PR | Lane | Contents |
|---|---|---|---|
| 1 | Choice store, validator, front end, root helper, `agentos-terminal`, `agentos-shell` | developer image; Fable code review (root helper, console input) | Wired into the developer image's desktops; validator, input and helper batteries; the `agentos-setup` account; `surface-choice-store` VM test |
| 2 | Desktop specialisations and `agentos-boot-default` | developer image | hyprland / sway / none specialisations (the heavy desktops stay build-time choices); the UEFI VM proof of 3.2; `surface-offline-switch` |
| 3a | The setup window: the mechanism | wall; Fable code review | `agentos.cleanRoom.setupWindow.enable` (default OFF, the existing rulesets byte-identical): the set and its rules, the fetcher's fixed uid, the state machine (`modules/agos_window.py`), the window oneshot following nftables' restarts and reloads, the tick that closes and seals on the budget, the pull units conditioned on the marker. VM test `tests/setup-window.nix` (W1-W10) |
| 3b part 1 | The setup window: the agent does not exist while it is open | wall; Fable code review | `agentos-seal.service` (the wait, the detector, `ExecStopPost`), tty1's getty behind it, the login token and the gates on every other console and on the user manager, no reopening in a sealed boot, the tty1 progress unit (2.4). VM test `test-seal-wait`. The option stays off on shipped images. |
| 3b part 2 | The setup window: seal-check and the rest | wall; Fable code review | The seal-check assertions (2.5) and the second key at every door, the registry probe as the fetcher's uid, the tool timeouts under the lock wait, the quiet tick while a re-provisioning waits for its reboot. The flake assertion stays, now pointing at 3c. |
| 3c | The record | wall; Fable spec ruling, then code review | The audit-chain records of the window's four transitions (2.7): two appending units, the wall's signer, seal-at-once on a refused open, the seal's record reconciled by the tick. The flake gate is replaced by structural assertions. The surface-choice line moves with the surfaces in slice 4. |
| 4 | Product path | wall; Fable code review | Surfaces and first login in `agentos`; a human account; `agentos-sealed` retired once the window has acceptance evidence |

Slices 1 and 2 touch no wall code and can land while 3 is in review.

## 6. Tests

Every probe that claims "the agent uid gains nothing" runs as the real `agent` uid, not as root
with a uid flag. Every "cannot reach" arm has a positive control in the same test and pins the
failure mode (curl exit 28, as `egress-uid-scope.nix` does), so a dead network cannot pass it.

| Test | Asserts |
|---|---|
| validator battery | Every malformed store (unknown value, wrong type, extra key, oversize, symlink, not root-owned, wrong filesystem) yields the defaults and an audit line; no output string outside the closed lists |
| input battery | Only empty / `s` / in-range digits are accepted; ESC, control bytes, paste and over-length lines are rejected; nothing typed reaches a file or an argv; idle timeout takes defaults |
| helper battery | The root helper accepts only three in-list tokens; anything else writes nothing |
| wrapper battery | `agentos-terminal` / `agentos-shell` ignore `SHELL`, `PATH`, `ENV`, `BASH_ENV`, `ZDOTDIR`, `XDG_CONFIG_HOME` for the decision |
| `surface-choice-store` (VM) | Root-owned; the agent uid cannot write or replace it; fallback logged |
| `surface-offline-switch` (VM, UEFI) | With the network down, choosing another desktop boots into it; switching back to `none` works; a rebuild in between is followed by a re-assert; a rollback boot selects the entry of the booted generation; a stale variable boots desktop `none`; `/boot`, EFI variables and the profile are not agent-writable |
| first-login account (eval + VM) | `agentos-setup` has no shell, home, groups, sudo or polkit rule; the front end unit is sandboxed; the helper refuses a symlink, a file it does not expect the owner of, and an oversize file |
| ruleset text invariants (build) | The two set rules sit after the `ct state` and loopback rules and before any uid-0 accept; nothing else refers to `setup_uid`; every accept reachable in the window is v4-pinned |
| `nft-ruleset-window-open` (build) | The base ruleset plus the exact rendered "open" file passes `nft -c` |
| `setup-window-default-sealed` (VM) | Window unit masked or failing: set empty, the fetch uid cannot reach off-box; positive control: with the window open it can |
| `setup-window-scope` (VM) | Window open: the fetch uid reaches 443; port 80 stays closed; root gains nothing; no process of the `agent` uid exists while the set is non-empty |
| `setup-window-reload` (VM) | `systemctl reload nftables` and `restart nftables` mid-window: the set is re-populated, the same boot is charged nothing extra (remaining equals the recomputed value), one audit line each; after the seal the set stays empty across both |
| `setup-window-seal-ends-flow` (VM) | A download in flight when the seal fires does not survive it: the fetch uid's open connection is gone after `agentos-seal.service` |
| `setup-window-no-model` (VM) | Budget ends with no model: the box seals, the agent's getty starts, tty1 says there is no model |
| `seal-check-sealed-by-build` (VM, slice 3b part 2; the token here is seal-check's `/run/agentos-sealed-ok`) | `cleanRoom.sealed = true`: window unit masked, set empty, token written, no `sealed` file needed |
| `setup-window-seal-fails` (VM) | The seal unit made to fail or time out: the set is still empty afterwards and `sealed` is written; with the seal unit broken outright, the tick alone empties the set at `remaining ≤ 0` |
| `setup-window-agent-absent` (VM, `test-seal-wait`) | Window open: tty1's getty has not started; a second virtual terminal, a getty started by hand and a serial console refuse and produce no process of the agent's account; with lingering enabled the user manager is refused at boot. A sampler on the box watches "agent process, then non-empty set" for the whole test. Controls: each door logs the agent in once the box is sealed |
| `setup-window-seal-on-model` (VM, `test-seal-wait`) | A look-alike model name does not seal; the configured name in `/api/tags` does (reason `complete`), and only then does the agent exist. A killed wait does NOT seal (only a timeout does) and the agent stays absent. State removed in a sealed boot does not reopen the window and is not re-sealed; after a reboot it is a first boot |
| `setup-window-record` (VM, `test-window-record`) | On a signed node the first record of the chain is `window-opened`, signed by the pinned participant; reopened and re-derived follow; the seal is recorded within a tick, once, from the marker; `audit verify` passes under the pin. A torn tail before first boot: sealed `refused`, nothing opened, no agent before the seal; after the repair a reboot is a first boot. The identity unit failing: no window and no record |
| `first-login-choice-survives` (VM) | A non-default choice made in the front end reaches the store (the runtime directory is still there when the helper reads) |
| budget battery | Recompute is idempotent (ticking twice gives one charge); a same-boot run charges nothing; a new boot id settles the previous boot; the floor applies once per boot |
| `setup-window-unit-dead` (VM) | Window unit killed with `sealed` absent: seal-check treats the box as sealed-or-down |
| `setup-window-budget` (VM) | Budget exhausted: set empty, `sealed` written; a reboot does not reopen |
| `setup-window-reboot` (VM) | A reboot inside the window reopens with the remaining budget, not a new one |
| `setup-window-reboot-loop` (VM) | Five boots of under a minute each consume at least five floors |
| `setup-window-clock-1970` (VM) | Clock at the epoch: the window opens on the budget; it is not sealed |
| `setup-window-invalid-state` (VM) | Malformed `window.json`: sealed, console says why |
| `seal-marker-forged` (VM) | An empty or malformed `sealed`: rejected, wall DOWN response. (A well-formed forgery is indistinguishable and only seals the box; not tested as a rejection.) |
| `seal-check-window` (VM) | `sealed` valid with a non-empty set: sealed response. Converse: `sealed` absent with the window unit failed: sealed-or-down |

Each battery arm is mutation-tested before merge, as elsewhere in this repo.

## 7. Open questions for the owner

Neither blocks slices 1 and 2.

- A. Window length: 2 hours proposed (option, capped at 4).
- B. Which desktops ship in the product image: `none` and Hyprland + Noctalia proposed; each
  further desktop adds its whole closure to the image.

## 8. Rulings (Fable spec review, 2026-10-10)

1. **A runtime `sealed` marker is acceptable**, given that it names the ruleset it sealed under,
   means nothing without the set-emptiness check, and fails closed when absent or corrupt. The
   closure-hash property was weaker than advertised (a second closure plus a human remembering to
   switch, failing open). The sealed-specialisation alternative is rejected: it doubles
   generations and still needs the same budget unit.
2. **No reopen in v0.** Reinstall, or root on the break-glass console removing the state.
3. **The model server as fetcher is acceptable for v0** on the condition in 2.4: the agent does
   not run while the window is open. A separate fetch uid is the v1 shape.
4. **Split the first-login program**: an unprivileged front end and a root helper that accepts
   three enum tokens.

Re-review (2026-10-11), all folded in: the window unit split into an idempotent oneshot and a tick;
the budget keyed to the boot id and recomputed; the seal unit always scheduled, blocking, with a
named model detector and a named owner for tty1; the seal ends the in-flight download; marker
validity is schema validity, with the generation mismatch logged; sealed-by-build in seal-check;
the boot-default mechanics (variable precedence, the real stale fallback, the booted generation,
UEFI only, ordering after the bootloader install); the front end's runtime directory, account and
sandbox; the token helper split from the boot-default unit.

Final pass (2026-10-11), folded in: the shutdown stamp belongs to the window oneshot, not the tick
(R1); the tick enforces the budget and the seal has a fixed sequence plus a flush on its failure
path (R2); the front end's runtime directory is preserved until the helper has read it (R3); and
the notes on the progress unit, the seal's ordering, sealed-by-build in (e), which units carry the
mount and read-write conditions, the meaning of open seconds, the two meanings of `<N>`, and the
pull timer.

Adopted from the review's optional list: one state file with a schema; the window length as an
option with a cap; the element count in the audit line; a named set instead of a chain the unit
fills; `sealed = true` masks the window unit.

Not verified by the review, and therefore owed by the implementation slices: the exact behaviour
of `switch-to-configuration` for the boot action on this pin; `DynamicUser=` together with a
static `User=` on the pinned systemd (relied on the Dell observation in `model-3b-open.nix`); the
resolver's TCP fallback in the pinned ollama; whether `getty@tty1` ordering conflicts with a
`TTYPath=/dev/tty1` unit placed before it (VMs are blind to this; a real-hardware item).
