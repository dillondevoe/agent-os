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
  `oifname "lo"` rules and before any uid-0 accept, and nothing else refers to the set. No port 80.
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

One root-owned directory, `/var/lib/agent-os/seal/` (0700 root). The window oneshot and the seal
unit carry
`RequiresMountsFor=/var/lib/agent-os` and `ConditionPathIsReadWrite=/var/lib/agent-os/seal` (the
tick carries only the mount requirement, see 2.1): if
the state cannot be written (read-only filesystem, no space), the window does not open. Writes
are temp file, `fsync`, `rename`, then `fsync` of the directory.

| File | Content |
|---|---|
| `window.json` | `schema`, `remaining_seconds` (the budget), `opened_at` and `wall_deadline` (advisory), `boots` (count). One file, one atomic write. |
| `sealed` | The store path of the running toplevel, the sha256 of the rendered ruleset file, UTC time, and the reason. Never removed by any unit. |

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
| absent | absent | First boot: write `window.json` with the full budget and this boot id, open. |
| absent | valid, same boot id, remaining > 0 | Re-derivation: open, charge nothing. |
| absent | valid, new boot id, remaining > 0, wall deadline not passed (or clock unknown) | Settle the previous boot, open for the remainder. |
| absent | valid, remaining ≤ 0 or wall deadline passed | Seal. |
| present but malformed | — | Wall DOWN (seal-check's response); do not open. |
| absent | unreadable or malformed | Seal, and say so on the console. |
| absent | any, but the state directory is not writable | The unit is skipped by its condition; the set stays empty; seal-check (e) treats the box as sealed-or-down until a human fixes the filesystem. Intended. |

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
  reinstall, or a human with root on the break-glass console (tty3) removing the two state files
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
- On a boot where the window is open it *blocks* until one of two things: the model is present
  and first login has finished, or the budget is gone. Its `TimeoutStartSec` is the window length
  plus a margin. "Model present" is decided by one detector: the daemon's own `GET /api/tags` on
  loopback lists the configured model.
- It waits on first login by ordering: `After=agentos-surface-apply.service`.
- After the flush and before it writes `sealed` it ends the fetch: `systemctl restart
  ollama.service` (step 2 of the sequence in 2.1). Flushing the set does not end a download
  already in flight, because the `ct state established` rule above the set rules keeps an open
  connection alive. The restart ends it, so "sealed" means no egress and not "no new egress". A
  half-pulled model is absent from `/api/tags`, so the detector cannot mistake it for a model.
- tty1 between the first-login questions and the agent's getty is owned by one small unit,
  `agentos-setup-progress.service` (`Type=oneshot`, `TTYPath=/dev/tty1`,
  `After=agentos-firstlogin.service`, `Before=getty@tty1.service`, unprivileged, output only). It
  exits when `sealed` appears, so tty1 never has two owners. It prints the download progress, and if
  the budget ends without a model it prints that the box has no model and how a human re-provisions
  it (2.3). It reads no input.
- There is no cycle: the first-login unit is before the getty and not after the seal; the seal
  waits on first login; the getty waits on the seal.
- This is an invariant with a test: no process of the `agent` uid exists while `setup_uid` is
  non-empty.

### 2.5 What seal-check asserts

`seal-check.nix` is ordered after `agentos-setup-window.service` and before the agent's getty, and
on every boot asserts:

- (a) the table is present;
- (b) the set `setup_uid` and its two rules are present — their absence means the loaded ruleset is
  not the reviewed one: wall DOWN;
- (c) `sealed` valid ⇒ the set is empty; otherwise the sealed response (links down, agent withheld);
- (d) `sealed` absent ⇒ `window.json` valid with budget left; otherwise seal and re-check;
- (e) `sealed` absent and the window unit failed or inactive ⇒ the set must be empty and the box is
  treated as sealed-or-down, never as "open" — unless the box is sealed-by-build (below), where an
  inactive, masked window unit is the expected state and (c) applies.

A box built with `agentos.cleanRoom.sealed = true` has the window unit masked and never writes a
`sealed` file. seal-check treats that as sealed-by-build: (c) applies with the build flag standing
in for the marker, so the set must be empty and the token is written. An eval assertion ties the
two together: `cleanRoom.sealed = true` implies the window unit is masked.

`/run/agentos-sealed-ok` is written only when (c) holds. When the sealed marker's ruleset hash
differs from the running generation's, seal-check logs `sealed-generation-changed` (not a failure:
a rebuild legitimately changes it) and rewrites nothing. Its repair text stops telling the operator
to restart nftables without saying that the window unit follows.

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
| 3b | The setup window: around it | wall; Fable code review | The agent's session waiting on the seal (2.4), the "model is in" seal and its detector, the tty1 progress unit, the seal-check assertions (2.5), the audit-chain lines (window and surface choice). **Until 3b lands the option must stay off on a real box**: the agent's uid gets no accept, but the agent can run while the fetcher has egress |
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
| `seal-check-sealed-by-build` (VM) | `cleanRoom.sealed = true`: window unit masked, set empty, token written, no `sealed` file needed |
| `setup-window-seal-fails` (VM) | The seal unit made to fail or time out: the set is still empty afterwards and `sealed` is written; with the seal unit broken outright, the tick alone empties the set at `remaining ≤ 0` |
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
