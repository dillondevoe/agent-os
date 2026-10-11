# First login and the setup window (spec, v0)

Status: SPEC, awaiting Fable spec review. No code in this PR. It turns
`docs/design/surfaces-and-first-login.md` §4 into buildable pieces now that the owner has
answered open question 1 there (2026-10-10: "setup window i think"): one system that opens a
short, logged window to fetch the local model and then locks itself, not two systems and a switch.

Security surface (the egress wall's provisioning exception, a root program on the console, the
choice of what the box boots): branch → spec PR → Fable spec review → implementation PRs → Fable
code review for the wall pieces → merge.

## 0. Thesis

Two things have to become true, and they are separable:

1. **Sealing stops being a rebuild.** Today the seal is `agentos.cleanRoom.sealed = true` plus
   `nixos-rebuild switch` into a second closure (`agentos-sealed`). The owner's ruling asks for one
   closure. The proposal: the sealed ruleset is the *only* base ruleset the image ever loads, and
   the setup window is a small, additive, expiring exception on top of it. A box that fails to
   open the window, or forgets it was open, is sealed.
2. **First login picks a surface without building anything.** Desktop, terminal and shell are
   chosen from what is already in the store. The desktop is a pre-built specialisation selected
   offline; terminal and shell are a runtime indirection that needs no switch at all.

## 1. What is true today (measured)

- Product path: `nixosConfigurations.agentos` (unsealed) and `agentos-sealed` (the switch target).
  `modules/clean-room.nix` renders ONE nftables table; `cfg.sealed = false` adds a provisioning
  allowance (DNS + 80/443, not scoped to a uid) at build time. `modules/seal-check.nix` verifies at
  boot that the table exists and fails loud and recoverable if not.
- The product path is tty-only: `configuration.nix` sets `services.xserver.enable = false`, has one
  account (`agent`, autologin on tty1), `users.mutableUsers = false`, and imports no desktop module.
- Every surface piece lives in the developer image only (`configuration-open.nix`, which shares no
  module with the product path): `agentos.desktop` (none | hyprland | sway | plasma | gnome |
  cosmic), `agentos.desktopShell`, `agentos.terminal`, `agentos.shell`, the adapter
  (`modules/agos_desktop.py`). Each is a build-time option: changing one is a rebuild.
- The developer image has no wall, so it has no window either.
- There is no choice store, no first-login program, no specialisation anywhere in the repo.
- The model is fetched by the ollama daemon itself (`ollama pull` only asks it to), and the daemon
  runs as a systemd `DynamicUser` (`services.ollama.user = null`), so it has no stable uid an
  nftables `skuid` rule can name. `clean-room.nix` already records that only numeric, static uids
  are reliable there (its timesyncd rule).

## 2. The setup window

### 2.1 Mechanism

- The base ruleset is the sealed one, always. `clean-room.nix` keeps its `output` chain with
  `policy drop` and gains one empty regular chain, `setup_window`, that the output chain jumps to
  after the loopback and established rules. Empty chain = no extra accepts = sealed.
- A root oneshot, `agentos-setup-window.service`, is the only thing that may put rules into that
  chain. It adds exactly: DNS (53) and TCP 443 for the uid that fetches the model — not root, not
  `agent`, not port 80. That uid is the ollama daemon's, so the daemon gets a static system user
  (`services.ollama.user = "ollama"`, a fixed uid asserted at eval) in place of today's
  DynamicUser; the rule names the number. It runs only when the state machine in 2.2 says the
  window may open.
- A second oneshot, `agentos-seal.service`, flushes the chain and writes the sealed state. It is
  started by whichever comes first: the model is present and first login has finished, or the
  deadline timer fires. Flushing an already-empty chain is a no-op, so sealing is idempotent.
- `nixos-rebuild` is not involved. `agentos.cleanRoom.sealed` stays as an option for one release
  with a changed meaning (`true` = never open the window on this box) so `agentos-sealed` keeps
  evaluating until the product path moves over (slice 4).

### 2.2 State

One root-owned directory, `/var/lib/agent-os/seal/` (0700 root), next to the audit and taint state
the broker already keeps there. Two files, both written atomically (write temp, fsync, rename):

| File | Meaning |
|---|---|
| `window-opened` | First line: the boot-clock-independent deadline as a UTC timestamp. Written once, when the window first opens. |
| `sealed` | Present = the box has sealed. Never removed by any unit. |

Decision table for `agentos-setup-window.service` at boot:

| `sealed` | `window-opened` | Action |
|---|---|---|
| present | any | Do nothing. The chain stays empty. |
| absent | absent | First boot: write `window-opened` with now + the window length, open the window, start the deadline timer. |
| absent | present, deadline in the future | Reopen until that same deadline (a reboot during setup must not strand the pull, and must not extend the window). |
| absent | present, deadline passed | Seal now (run `agentos-seal.service`). |
| unreadable, malformed, clock before the build date | — | Seal now, and say so on the console. |

Every transition appends one line to the audit chain: opened (with the deadline), reopened after
reboot, sealed (with the reason: complete, deadline, or invalid state).

### 2.3 Window length and reopening

- Length: 2 hours from first boot (owner question A below). Long enough for a slow link to pull a
  multi-gigabyte model, short enough that a box left on a shelf does not sit open.
- A box that seals on the deadline without a model still boots and says, on tty1, that it has no
  model and how to reopen.
- Reopening is a human act on the trusted console, never the agent's: `agentos setup-window open`
  run from the break-glass console (tty3) or approved on the confirm channel (tty2) as a T2 broker
  capability. It removes nothing; it writes a new `window-opened` deadline and a `reopened` audit
  line, and `sealed` is renamed to `sealed.prev` so the table above treats the box as mid-window.
  The agent has no capability that reaches it without the confirm prompt.

### 2.4 What this changes about trust

- Before: the unsealed closure allowed DNS + 80/443 to every uid, including the agent's, until a
  human rebuilt. After: the agent's uid never gets an accept rule, at any point in the box's life.
- Before: forgetting to rebuild left the box open forever. After: forgetting leaves it sealed.
- The cost: the seal is now a runtime fact held in `/var/lib`, not a property of the closure hash.
  `seal-check.nix` is extended to assert, on every boot, that `sealed` present ⇒ `setup_window`
  is empty, and to shout and drop links if not (its existing sealed response). A verifier can no
  longer read "sealed" off the store path alone; it reads the store path (which contains the wall)
  plus the `sealed` marker. This is the trade the owner's ruling makes and it is stated here so
  the review can weigh it.
- Not defended: root deleting the state. Root already owns the box.
- Accepted: a wiped `/var/lib` looks like a first boot and opens one window of the configured
  length, for the `ollama` uid only.

## 3. The surface choice

### 3.1 Store

`/var/lib/agent-os/surface.toml`, root 0644, written atomically:

```toml
schema = 1
desktop = "hyprland"   # one of the closure's offered desktops
terminal = "kitty"
shell = "bash"
```

One reader, `agentos-surface read` (python, stdlib `tomllib`), used by everything else. It
validates against closed lists compiled in at build time. Unknown key, unknown value, wrong type,
missing file, oversize file (> 4 KiB): the defaults, plus one audit line. It never returns a string
that did not come from its own closed list, so no caller can be handed a path or a command.

### 3.2 Desktop: specialisations, selected offline

- The image defines one NixOS `specialisation` per offered desktop other than the default. All of
  them are in the closure when the image is built, so all of them are in the store before the wall
  exists.
- Applying a choice is `…/specialisation/<name>/bin/switch-to-configuration boot` (or the parent's,
  for the default), then a reboot. No evaluation, no build, no network.
- That is done by one root oneshot, `agentos-surface-apply.service`, which reads the store through
  the validator and maps the enum to a specialisation name through a table compiled in at build
  time. The agent cannot start it (no polkit rule, not a broker capability in v0).
- Offered in the product image in v0: `none` (default) and `hyprland` (with Noctalia, the developer
  image's default since #339). sway, Plasma, GNOME and COSMIC stay developer-image choices until
  the closure budget is set (owner question B).
- The seal step refuses if the chosen desktop's specialisation is not in the running closure
  (surfaces-and-first-login.md §4.3 point 4). By construction it always is; the check exists so a
  future change that drops one fails at seal time and says why.

### 3.3 Terminal and shell: no switch at all

- `agentos-terminal`: a tiny wrapper that asks the validator for the terminal and `exec`s it from a
  compiled-in table of store paths. The desktop configs bind "open a terminal" to this wrapper
  instead of to a terminal name. All offered terminals are in the closure.
- `agentos-shell`: the same shape. It is the login shell of the human account (the developer
  image's `operator`; the product path gains a human account in slice 4) and `exec`s the chosen
  shell as a login shell. If the store is unreadable it runs bash.
- The agent account is untouched: bash, the tty1 loop, kitty for the brain window.
- home-manager is not needed for this and stays out of v0. It returns when per-user dotfiles do.

## 4. The first-login program

- One program, `agentos-surface`, text only, stdlib python. Three numbered questions (desktop,
  terminal, shell), each showing its default; Enter takes the default; `s` skips the rest. It
  writes the store and exits. This answers open question 8: the text program is the only program
  in v0; a graphical front end, if one is built, calls the same writer.
- First boot: a root oneshot bound to tty1 (`StandardInput=tty`, `TTYPath=/dev/tty1`,
  `ConditionPathExists=!/var/lib/agent-os/surface.toml`), ordered before the autologin getty, so
  the human answers before the agent's session exists. Then `agentos-surface-apply`, and a reboot
  only if the desktop differs from the running one.
- Later: `agentos surface` from the break-glass console re-runs it. Changing terminal or shell
  takes effect at the next terminal or login; changing desktop reboots.
- Input handling: a line is accepted only if it is empty, `s`, or a digit within the menu's range.
  Nothing typed is stored, logged, or passed to a command. A 5-minute idle timeout takes the
  defaults so an unattended first boot still finishes and the setup window still closes.
- It runs as root on the console, so it is trusted-base code. It stays small (one file, no
  imports beyond the stdlib), and it does not start the network, a shell, or an editor.

## 5. Slices

| # | PR | Lane | Contents |
|---|---|---|---|
| 1 | Choice store, validator, `agentos-surface`, `agentos-terminal`, `agentos-shell` | developer image; Fable code review (root console program) | The program and wrappers, wired into the developer image's desktops; battery for the validator and the input handling; `surface-choice-store` VM test |
| 2 | Desktop specialisations and `agentos-surface-apply` | developer image | hyprland / sway / none specialisations (the heavy desktops stay build-time choices); first-boot oneshot; `surface-offline-switch` VM test with the network cut |
| 3 | The setup window | wall; Fable code review | `setup_window` chain, the two oneshots, the deadline timer, the state table, audit lines, `seal-check` extension; VM tests in §6 |
| 4 | Product path | wall; Fable code review | Surfaces and first login in `agentos`; a human account; `agentos-sealed` retired once the window has acceptance evidence |

Slices 1 and 2 touch no wall code and can land while 3 is in review.

## 6. Tests

| Test | Asserts |
|---|---|
| validator battery | Every malformed store (unknown value, wrong type, extra key, oversize, symlink, not root-owned) yields the defaults and an audit line; no output string outside the closed lists |
| input battery | Only empty / `s` / in-range digits are accepted; nothing typed reaches a file or an argv; idle timeout takes defaults |
| `surface-choice-store` (VM) | Root-owned; the agent uid cannot write or replace it; fallback logged |
| `surface-offline-switch` (VM) | With the network interface down, choosing another desktop completes and the box boots into it |
| `setup-window-default-sealed` (VM) | With `agentos-setup-window.service` masked or failing, the chain is empty and the `ollama` uid cannot reach off-box |
| `setup-window-scope` (VM) | Window open: the `ollama` uid reaches 443; the `agent` uid and root do not gain anything the sealed ruleset denies; port 80 stays closed |
| `setup-window-deadline` (VM) | Past the deadline (clock stepped), the chain is empty and `sealed` exists; a reboot does not reopen |
| `setup-window-reboot` (VM) | A reboot inside the window reopens it with the original deadline, not a new one |
| `setup-window-invalid-state` (VM) | Malformed `window-opened`, or a clock before the build date: sealed, console says why |
| `seal-check-window` (VM) | `sealed` present with a non-empty chain: seal-check's sealed response (links down, loud) |

Each battery arm is mutation-tested before merge, as elsewhere in this repo.

## 7. Open questions

For the Fable review:

1. Is a runtime `sealed` marker an acceptable replacement for "sealed is the closure", given §2.4?
   The alternative that keeps the closure property is a `sealed` specialisation and an offline
   `switch-to-configuration boot` at seal time; it composes badly with desktop specialisations
   (they cannot nest, so every desktop needs a sealed twin) and still needs a deadline unit.
2. Should reopening the window exist at all in v0, or is "reinstall to re-provision" the honest
   answer until there is acceptance evidence?
3. The window's subject is the ollama daemon's uid, because the daemon is the fetcher. That means
   the process that serves the model to the agent can also reach the internet while the window is
   open (today every uid can). Is that acceptable for v0, or must the fetch be a separate one-shot
   uid that downloads the blobs and hands them to a daemon that never has egress? The second is
   cleaner and is more work (ollama's registry protocol, or a pre-fetched model store import).
4. First-boot ordering: the program runs as root on tty1 before the agent's getty. Is there a
   smaller trusted footprint (for example, running it as an unprivileged user with a root helper
   that only accepts three enum values)?

For the owner (none blocks slices 1 and 2):

- A. Window length: 2 hours proposed.
- B. Which desktops ship in the product image: `none` and Hyprland + Noctalia proposed; each
  further desktop adds its whole closure to the image.
