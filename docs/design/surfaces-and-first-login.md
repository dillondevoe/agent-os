# Surfaces and first login

Status: DESIGN PROPOSAL. No code in this change. Owner review required before any
implementation PR. Decision 4 (GUI confirm) is still open; this document writes the default.

## 1. Goal

Agent OS ships one product image for everyone. The agent core (broker, brain, capabilities,
confirm channel, egress wall) is identical on every machine, and each desktop, terminal and
interactive shell is a thin "surface" on top of it that the user picks at first login and that
can never widen what the agent is allowed to do.

Today only one surface works (a Hyprland session in the open variant; the sealed variant is
tty-only). This design makes the surface a choice instead of a variant, and keeps every
security property independent of that choice.

## 2. Decisions, as design constraints

These come from the owner's decisions of 2026-10-05. Each is stated as a constraint the
implementation must satisfy.

| # | Decision | Constraint on the design |
|---|---|---|
| 1 | Sealed gets a GUI ("guis are good"). | A graphical session is in scope for the product image, not only tty. The tty path stays as the floor. |
| 2 | A display manager in the trusted base is acceptable. | Allowed, but it must be greetd-class and minimal (section 5). No full-featured login manager running as root with plugins. |
| 3 | Quick-ask may call the local model directly, read-only, no tools. | Quick-ask is a separate, narrow path (section 7). It gets no capabilities, no memory write, no network beyond loopback. |
| 4 | GUI confirm inside the agent's session: OPEN. | Default until decided: confirmation runs outside the agent's session. See 2.1. |
| 5 | Support more desktops over time. | The agent core must not import any desktop. Desktops plug in through one adapter interface (section 6). |
| 6 | Ship a few of everything and ask at first login. | Closures for every offered choice are present before the egress wall goes up (section 4). |
| 7 | home-manager is acceptable as a flake input. | Approved. It is used for per-user dotfiles only (section 4.4), never for anything the agent's security depends on. |
| 8 | XWayland on. | On, but it must not enable any network listener (section 5). |
| - | "One real version for everyone, esp. since public." | No sealed/open product split. One image that seals itself after first-run provisioning; a developer profile lives outside the product path. This direction still needs the owner's OK on the seal model (open question 1). |

### 2.1 The open decision: GUI confirm (default written here)

Default: the confirmation prompt runs outside the agent's session, in a separate trusted channel
(the existing second-getty and relay channels today; a separate seat or session for a graphical
prompt later). It is never a window in the session the agent can see and control.

Reasoning in plain words. The confirm prompt is the one place a human says "yes, let it do that."
If the prompt is a window on the same screen the agent is using, then an agent that can see the
screen and send input (a screenshot tool, a virtual keyboard, a pointer) could find the "Allow"
button and click it itself. Even if we never give the agent those tools on purpose, any program
running as the same user on a desktop can usually read the screen and fake input, so a
compromised browser tab or a prompt-injected app gets the same power. Putting the prompt in a
different session means there is nothing for the agent to see or click, and the approval can only
come from a person at the keyboard. This keeps the existing rule that the model's own terminal
carries zero authorization.

What the owner may still choose: allow an in-session GUI confirm later, if it can be shown that
the agent has no screen-read or input path at all. Until then, the default stands.

## 3. Fixed regardless of choice

These do not vary with `agentos.desktop`, `agentos.terminal` or `agentos.shell`, and each gets a
test (section 8).

- The agent user is unprivileged and not in `wheel`; it has no sudo rule. The existing eval-time
  assertions apply to every desktop. A desktop module must not add `wheel`, and must not add
  `input` or `video` beyond what the compositor strictly needs.
- The agent's login shell is bash, and the tty1 respawn loop stays. `agent-shell` is a bash script
  and the loop is POSIX sh in `environment.loginShellInit`; neither works under fish. Interactive
  shell choice therefore applies to the terminal the human opens, not to the agent account.
- The egress wall is nftables, system-wide and per-uid. It does not depend on the session, and a
  desktop must not open a hole in it. GUI apps that try to reach the network (update checks,
  location, weather) fail closed once sealed; the sealed profile disables those services.
- The display manager is greetd-class: one small autologin-capable greeter, no theming engine, no
  user switcher, autologin only to the agent account, never to a human administrator account.
  Desktops that insist on their own manager (Plasma, GNOME) are admitted only after a separate
  review of that manager as trusted-base code.
- XWayland is enabled (owner decision) but must not enable any network listener: no TCP X
  socket (`-listen tcp` off, `-nolisten tcp` on), only the local abstract/unix sockets. A VM test
  asserts no non-loopback listener from the display stack.
- The broker and the confirm channel stay outside the session (2.1). The adapter (section 6) runs
  as the agent user, never as root, and never holds the confirm socket.
- Terminal and shell choice cannot change which brain binary launches. The brain starts from the
  pinned store path, not from the user's `PATH`.

Residual risk to state plainly: on a wlroots compositor, every process of the agent's user is one
trust domain for screen capture and input. X clients under XWayland are weaker still, since X has
no client isolation. The fixed rules above do not remove this; they keep it from becoming
authority (confirm is elsewhere, the wall is elsewhere, no root).

## 4. First-login flow

### 4.1 What the user is asked

A short guided screen on first boot (graphical if a GPU session is available, otherwise on tty1),
three questions, each with a default and a "skip, use defaults" path:

1. Desktop: Hyprland, sway, Plasma, or none (terminal only).
2. Terminal: kitty, ghostty, foot, alacritty.
3. Shell: bash, fish, zsh.

Defaults: the choice offered first is the one with the least code and closure: desktop `none`
until a graphical adapter is accepted for the default image (open question 2); terminal `kitty`;
shell `bash`. Skipping gives a working box. The choice can be changed later from the same screen
(`agentos surface`, name provisional) and is reversible.

### 4.2 Where the choice is stored

A single small file owned by root, for example `/var/lib/agentos/surface.toml`, containing three
enum values and a schema version. The agent user cannot write it. It is read at boot by a
trusted unit that selects a pre-built system generation (4.3). It holds no secrets and no paths.
The file is validated against a closed list of values; anything else falls back to defaults and
logs a line to the audit chain.

### 4.3 Mapping to NixOS options, and the provisioning window

The choices map one to one onto options:

```
agentos.desktop  = "none" | "hyprland" | "sway" | "plasma";
agentos.terminal = "kitty" | "ghostty" | "foot" | "alacritty";
agentos.shell    = "bash" | "fish" | "zsh";      # the human's interactive shell
```

The central constraint: once the egress wall is up, the box can reach only the nixpkgs binary
cache, and the agent can reach nothing. A rebuild at that point would need the cache and would
change the system the user is supposed to be able to verify. So the design must not depend on
building anything after the seal.

Design: the image contains, in its closure, every desktop, terminal and shell the user may pick.
First login only selects among them; it does not download or compile anything.

- Option A (recommended): one system closure with all choices present, with the selected
  surface enabled through specialisations (NixOS `specialisation`), one per supported
  desktop. The selected specialisation becomes the boot default via a local, offline
  `switch-to-configuration boot`. No network, no evaluation, no build: the closures are already
  in the store. Terminal and shell are per-user data (4.4) and need no switch at all.
- Option B: pick at install time (ISO installer asks), build exactly one desktop into the image.
  Smaller closure, but changing the desktop later needs a rebuild and the cache, which is
  possible while sealed (the cache is allowed) but is a larger, slower, verifiable event.

Provisioning window, stated explicitly. The model pull needs open egress, so the box starts
unsealed, runs first-run provisioning, and then seals (this is the seal model under review, open
question 1). The surface choice interacts with that window as follows:

1. Everything any choice needs must be in the store before the wall goes up. That includes all
   offered desktops, terminals, shells, fonts and the adapter. Image size grows accordingly; the
   cost per desktop is tracked (open question 4).
2. The choice itself is made inside the window, while the box can still fetch a missing piece if
   something was left out. This is a safety margin, not the plan.
3. After the seal, changing the choice is an offline generation switch (Option A) or a sealed
   rebuild through the cache (Option B), never a download from an arbitrary host.
4. A seal check treats "chosen surface present in store" as a precondition. If it is not, the
   seal step refuses and says why; it does not seal a box whose chosen desktop cannot start.

No choice may keep the window open longer or add allowed destinations.

### 4.4 Terminal, shell, and home-manager

Terminal and shell are user-level. home-manager (approved as a flake input) renders per-user
configuration for the human account: the terminal's config, the shell's init, the launcher.
Rules:

- home-manager manages dotfiles for the human account only. It does not manage the agent
  account's security-relevant files, the broker, the confirm channel, or the wall.
- Config is copied once (not force-symlinked each boot) so user edits survive. Files the brain
  depends on, such as window class rules for the brain window, stay owned by the system.
- The flake input is pinned and goes through the existing flake-input provenance checks.

## 5. What the first login does not change

See section 3. In short: no wheel for the agent, bash and the tty1 loop for the agent account,
the wall, a minimal greetd-class display manager, XWayland on with no network listener, confirm
outside the session.

## 6. The adapter interface

Each desktop implements one small interface, run as a systemd user unit of the agent user. The
core never imports a desktop; it calls the adapter.

| Verb | Meaning | Portable layer | Required |
|---|---|---|---|
| `launch(app)` | Start an application by desktop entry id | XDG desktop entries (`gtk-launch`, `gio launch`) | yes |
| `windows()` | List windows (id, class, title, workspace) | per-desktop IPC | optional |
| `focus(id)` / `move(id, target)` | Focus or move a window | per-desktop IPC | optional |
| `screenshot()` | Capture the screen or a window | portal Screenshot, or `grim` on wlroots | optional |
| `notify(msg)` | Show a notification to the human | `org.freedesktop.Notifications` | yes |
| `clipboard(get/set)` | Read or write the clipboard | `wl-clipboard` where available | optional |

Rules:

- Optional verbs return an explicit "unsupported on this desktop" (a third state), never an empty
  result that reads as "saw nothing". The same discipline applies to the brain's window context.
- Every verb the agent can reach is a capability behind the broker, with a tier. `screenshot`,
  `clipboard` read, and any input injection are not enabled by default, and input injection is
  not part of this interface at all.
- The adapter never gets root, `wheel`, or the confirm socket.

First adapters, in order:

1. Hyprland: exists. The work is to extract the existing `hyprctl` calls behind the interface and
   preserve the window class the brain window relies on.
2. sway: the same protocol family, a small port (`swaymsg` for windows). Its purpose is to prove
   the interface is not shaped around one compositor.
3. Plasma 6: highest demand for non-experts, and it forces the no-wlroots case (D-Bus and
   portals only), which validates the least-common-denominator layer. It also brings its own
   display manager question (section 3).

GNOME and COSMIC follow only if their window-control story matures.

## 7. Quick-ask

A direct, read-only call to the local model, for a fast question from a hotkey or a terminal.

- Path: a dmenu-style prompt, then one HTTP call to the local model on loopback, then the answer
  as a notification and on the clipboard. From a terminal it prints the answer instead.
- No tools, no capabilities, no memory read or write, no file access, no network except the
  loopback model endpoint. It does not pass through the broker because it has nothing for the
  broker to gate. It does not use the agent's memory or its conversation.
- Optional context: the current text selection, capped at a fixed size, added to the prompt
  when the user invoked it from the graphical prompt.
- Because it bypasses the broker's audit path, it writes its own minimal audit line (timestamp
  and byte counts only, never the text) so that its use is visible (open question 5).

Where it lives. Port the two small scripts from the project owner's personal setup into the
repo as generic packaged scripts in a new surface module, stripped of personal paths, hostnames
and model choices:

- `agos-ask`: the quick-ask above. The model name comes from the product's configured local
  model, not a hardcoded tag. The launcher is configurable (fuzzel, wofi, rofi).
- `agos-scratch`: toggles the agent's shell as a floating scratch window. The compositor-specific
  part (a special workspace in Hyprland, the scratchpad in sway) moves behind the adapter
  rather than being a `hyprctl` call in the script. It launches the agent shell through the
  pinned path, with no extra privilege.

Dictation (local speech to text) is out of scope here and would ship behind its own option
because of the model size.

## 8. Test plan

Principle: only claims with a test are shipped; a surface without its test is marked
experimental.

### 8.1 VM-test matrix to add

Each row is a nixosTest in the existing style (compare the uid-scoped egress test).

| Test | For each | Asserts |
|---|---|---|
| `surface-boot-<desktop>` | none, hyprland, sway, plasma | Compositor/session starts; brain window exists, found through the adapter; the machine screenshot is non-blank |
| `surface-terminal-<term>` | kitty, ghostty, foot, alacritty | Process runs with the expected class or app id; ghostty needs GL, so run it under the virtual GPU or software-GL path |
| `surface-shell-<shell>` | bash, fish, zsh | Human account gets that shell; the agent account still has bash; the tty1 loop still starts |
| `surface-no-root-<desktop>` | each desktop | Agent is not in `wheel`, has no sudo rule, no extra groups (eval-time assertions plus an in-VM `id` check) |
| `surface-egress-<desktop>` | each desktop, sealed | From the agent uid, off-box connections fail; display stack, portals and D-Bus units bind no non-loopback listener; XWayland has no TCP socket |
| `surface-confirm-isolated-<desktop>` | each desktop | The confirm prompt is not reachable from the agent session: no screenshot or input path from the agent uid to it |
| `surface-offline-switch` | each desktop | With the wall up and the network cut, choosing a different surface completes from the local store |
| `surface-choice-store` | - | The choice file is root-owned, rejects unknown values, falls back to defaults, logs to the audit chain |
| `quick-ask-readonly` | - | The call reaches only loopback, writes no memory, exposes no tools, logs its audit line |
| config parse | hyprland, sway | Compositor config validates (`hyprland-config-parses` exists; add the sway equivalent) |

### 8.2 What CI can and cannot build

CAN: all eval-time checks (every combination evaluates, assertions hold), config parse checks,
the choice-store test, the quick-ask test, and VM tests that need only software rendering:
sway and Hyprland under software GL, the terminals that start without a real GPU, the
no-root and egress tests (these need no real display content).

CANNOT, or should not be required: anything needing a real GPU or a real display (ghostty
acceleration, real screen capture fidelity); the full Plasma closure on a hosted runner may be too
large or too slow, so it runs on the slow lane (nightly) rather than per PR; and the open-variant
image cannot be built in CI at all today because of an unfetchable model asset, so no CI test
boots the shipped developer image. The product image must not carry that limitation. Real
hardware passes stay manual and are recorded as such. Fast lane: evals and parses on every
PR. Slow lane: VM matrix, nightly.

## 9. Order of work (for later PRs)

1. Fix stale comments in the open configuration that contradict the code.
2. Add `agentos.terminal` and `agentos.shell` with current defaults (no behaviour change), then
   the other terminals and shells and their tests.
3. Extract the adapter from the Hyprland module; add `agos-ask` and `notify`.
4. Add `agentos.desktop` (none and hyprland as a refactor, then sway, then Plasma).
5. First-login screen and choice store, after the seal model (question 1) is decided.
6. A trusted graphical confirm in a separate seat, last, because it touches the trusted base.

## 10. Open questions

1. Seal model: one closure with a time-boxed, audited provisioning window (this document
   assumes it), or two closures and a switch? This gates section 4.3.
2. Which desktop is the default in the product image: `none` (smallest) or Hyprland (what exists)?
3. GUI confirm in the agent's own session: still open (2.1). Default stands until decided.
4. Closure budget: how large may the single image be with all desktops included, and is Plasma
   worth its size on a sealed box? Option A versus Option B depends on the answer.
5. Does quick-ask's own minimal audit line satisfy the owner, or should it be unlogged, or
   routed through the broker as a read-only no-tool call?
6. Plasma and GNOME bring their own display managers. Accept them as trusted-base code, or
   require greetd with a session command for every desktop?
7. Is ghostty the default terminal, or does it stay kitty?
8. Does the first-login screen need a text-only twin for headless machines, and is it the same
   program?
9. XWayland on wlroots weakens isolation between the agent's apps. Is a per-app restriction
   (for example, only listed apps may use X) worth building in v1?
10. Which of these package versions exist at the repository's pinned nixpkgs revision? Several
    desktop and terminal modules were checked only on a newer revision and must be re-checked
    against the lock file before implementation.
