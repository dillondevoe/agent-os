# First login and the setup window (spec, v1)

Status: SPEC. Fable spec review 2026-10-10: approve with required changes; all twelve are folded in
below and the four rulings are recorded in section 8. Awaiting the re-review. No code in this PR. It
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
  and re-derives it from state.** `agentos-setup-window.service` is `After=nftables.service`,
  `PartOf=nftables.service` (a restart re-runs it) and `ReloadPropagatedFrom=nftables.service`
  with an `ExecReload` that is the same idempotent "recompute from state" entry point (a reload
  re-runs it). Every run reads the state (2.2) and ends in exactly one of: set empty, or set =
  { fetch uid }. A run that cannot decide leaves it empty.
- The fetch uid: the ollama daemon gets a static account with an explicit number,
  `users.users.ollama.uid = <N>` (outside the `ids.nix` range), with eval assertions that
  `services.ollama.user == "ollama"` and that the uid is set. The nft file renders from
  `config.users.users.ollama.uid`.
- The `agent-os-pull-model` probe stops running as root: it runs as the ollama uid (or is dropped),
  so the window's subject is the only uid that ever needs the registry.
- Sealing is `agentos-seal.service`: flush the set, write the sealed state. It is started by
  whichever comes first: the model is present and first login has finished, or the budget runs
  out. Flushing an empty set is a no-op, so sealing is idempotent.
- `nixos-rebuild` is not involved. `agentos.cleanRoom.sealed` stays for one release with a changed
  meaning (`true` = never open the window on this box: the window unit is masked, not merely
  skipped) so `agentos-sealed` keeps evaluating until the product path moves over (slice 4).

### 2.2 State

One root-owned directory, `/var/lib/agent-os/seal/` (0700 root). Both units carry
`RequiresMountsFor=/var/lib/agent-os` and `ConditionPathIsReadWrite=/var/lib/agent-os/seal`: if
the state cannot be written (read-only filesystem, no space), the window does not open. Writes
are temp file, `fsync`, `rename`, then `fsync` of the directory.

| File | Content |
|---|---|
| `window.json` | `schema`, `remaining_seconds` (the budget), `opened_at` and `wall_deadline` (advisory), `boots` (count). One file, one atomic write. |
| `sealed` | The store path of the running toplevel, the sha256 of the rendered ruleset file, UTC time, and the reason. Never removed by any unit. |

The window is measured as a **budget of open seconds**, not as a wall-clock deadline. A box with
no RTC battery boots near 1970 and time sync is itself behind the wall, so a deadline written from
that clock would be wrong for the life of the box.

- The budget starts at the window length (2.3). While the window is open the unit rewrites
  `remaining_seconds` once a minute and on `ExecStop`.
- Each boot that opens the window subtracts at least a floor of 60 seconds, whatever its uptime,
  so a reboot loop cannot hold the window open on sub-minute boots.
- The wall-clock deadline is honoured only when the clock is later than the image's build date
  (compiled in). A clock earlier than that is "unknown": the budget alone decides. A clock far in
  the future cannot shorten a window either, for the same reason.

Decision table, evaluated on every run of `agentos-setup-window.service`:

| `sealed` | `window.json` | Action |
|---|---|---|
| valid | any | Set empty. Nothing else. |
| absent | absent | First boot: write `window.json` with the full budget, open. |
| absent | valid, budget > 0, wall deadline not passed (or clock unknown) | Subtract the boot floor, open for the remainder. |
| absent | valid, budget ≤ 0 or wall deadline passed | Seal. |
| present but malformed, or its ruleset hash names no ruleset this box has ever rendered | — | Treat as wall DOWN (seal-check's response); do not open. |
| absent | unreadable or malformed | Seal, and say so on the console. |

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

- On any boot where the window opens, the agent's session (`getty@tty1` autologin, and any desktop
  session) is ordered `After=agentos-seal.service`. The agent account has no process until the set
  is empty.
- The human sees the first-login questions (section 4), then a plain progress line for the model
  download on tty1, then the normal session.
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
  treated as sealed-or-down, never as "open".

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
- Instead the root unit `agentos-surface-apply.service` selects the boot entry that already
  exists for the running generation's specialisation: `bootctl set-default
  nixos-generation-<N>-specialisation-<name>.conf` (or clears it for the default), where `<N>` is
  read from the system profile link and `<name>` comes from a table compiled in at build time. It
  runs at every boot and after every `switch-to-configuration` (an activation snippet), so a
  rebuild, which changes `<N>`, is followed by a re-assert. A stale default falls back to the
  parent, which is desktop `none`: the tty floor.
- `boot.loader.systemd-boot.configurationLimit` is set, and stated.
- The agent cannot influence what boots: `/boot`, the EFI variables and
  `/nix/var/nix/profiles` are not agent-writable (asserted, tested), and the unit is not startable
  by the agent (no polkit rule, not a broker capability in v0).
- Offered in the product image in v0: `none` (default) and `hyprland` (with Noctalia, the developer
  image's default since #339). sway, Plasma, GNOME and COSMIC stay developer-image choices until
  the closure budget is set (owner question B).
- The seal step refuses if the chosen desktop's specialisation is not in the running closure.
- This selection mechanism is the part of the spec least proven on paper. Slice 2 must demonstrate
  it in a UEFI VM (including a rebuild in the middle) before anything depends on it; if it does
  not hold, the fallback is the generation switch with a GC root for the parent, recorded here so
  the choice is not relitigated from scratch.

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

- **Front end**, `agentos-firstlogin`, runs as a dedicated unprivileged user (`agentos-setup`, not
  `agent`) on tty1. Three numbered questions (desktop, terminal, shell), each showing its default;
  Enter takes the default; `s` skips the rest. It writes three tokens to one 0600 file in its own
  runtime directory and exits. It has no `exec`.
- **Root helper**, `agentos-surface-apply.service`, reads only that file, accepts only three
  tokens each present in its compiled-in list, and writes the store. It parses nothing else.
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
| 1 | Choice store, validator, front end, root helper, `agentos-terminal`, `agentos-shell` | developer image; Fable code review (root helper, console input) | Wired into the developer image's desktops; validator and input batteries; `surface-choice-store` VM test |
| 2 | Desktop specialisations and boot-entry selection | developer image | hyprland / sway / none specialisations (the heavy desktops stay build-time choices); the UEFI VM proof of 3.2; `surface-offline-switch` |
| 3 | The setup window | wall; Fable code review | The set and its rules, the window and seal units, the state table, the budget, agent ordering, audit lines, the seal-check extension, the static ollama uid; the VM tests in section 6 |
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
| `surface-offline-switch` (VM, UEFI) | With the network down, choosing another desktop boots into it; switching back to `none` works; a rebuild in between is followed by a re-assert; `/boot`, EFI variables and the profile are not agent-writable |
| ruleset text invariants (build) | The two set rules sit after the `ct state` and loopback rules and before any uid-0 accept; nothing else refers to `setup_uid`; every accept reachable in the window is v4-pinned |
| `nft-ruleset-window-open` (build) | The base ruleset plus the exact rendered "open" file passes `nft -c` |
| `setup-window-default-sealed` (VM) | Window unit masked or failing: set empty, the fetch uid cannot reach off-box; positive control: with the window open it can |
| `setup-window-scope` (VM) | Window open: the fetch uid reaches 443; port 80 stays closed; root gains nothing; no process of the `agent` uid exists while the set is non-empty |
| `setup-window-reload` (VM) | `systemctl reload nftables` and `restart nftables` mid-window: the set is re-populated, the budget is unchanged, one audit line; after the seal the set stays empty across both |
| `setup-window-unit-dead` (VM) | Window unit killed with `sealed` absent: seal-check treats the box as sealed-or-down |
| `setup-window-budget` (VM) | Budget exhausted: set empty, `sealed` written; a reboot does not reopen |
| `setup-window-reboot` (VM) | A reboot inside the window reopens with the remaining budget, not a new one |
| `setup-window-reboot-loop` (VM) | Five boots of under a minute each consume at least five floors |
| `setup-window-clock-1970` (VM) | Clock at the epoch: the window opens on the budget; it is not sealed |
| `setup-window-invalid-state` (VM) | Malformed `window.json`: sealed, console says why |
| `seal-marker-forged` (VM) | An empty or wrong-content `sealed`: rejected, wall DOWN response |
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

Adopted from the review's optional list: one state file with a schema; the window length as an
option with a cap; the element count in the audit line; a named set instead of a chain the unit
fills; `sealed = true` masks the window unit.

Not verified by the review, and therefore owed by the implementation slices: the exact behaviour
of `switch-to-configuration` for the boot action on this pin; `DynamicUser=` together with a
static `User=` on the pinned systemd (relied on the Dell observation in `model-3b-open.nix`); the
resolver's TCP fallback in the pinned ollama; whether `getty@tty1` ordering conflicts with a
`TTYPath=/dev/tty1` unit placed before it (VMs are blind to this; a real-hardware item).
