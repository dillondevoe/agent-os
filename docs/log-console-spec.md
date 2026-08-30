# WP-L1 — the Agent OS log console: a findable place for the machine's noise

Status: **SPEC — not built.** Dillon reviews before build (directive tg 11462, 2026-08-29).
Owner: Mirror (DVo). Boot/console surface — **branch → PR → review, never self-merge.**
Origin: Dillon, verbatim — *"what are they telling the user? we could have a log console or
distinguish a place for stuff like that."*

---

## 0. Thesis

The boot screen should carry only what a person can act on. Everything else the machine says —
firmware quirks, benign service chatter, a long-but-normal warm-up — is not *hidden*, it is
**relocated to a place a human can find and read in plain words.** Today Agent OS has the first
half (a quiet splash) and none of the second: the noise is merely suppressed, and the one surface
a worried operator would reach for is `journalctl`, which answers in kernel dialect.

The failure this prevents is not cosmetic. It is an operator power-cycling a healthy box because
the only signal available to them was ambiguous.

## 1. The measurement that motivated the spec, and corrected its premise

Measured on the Dell (generation 31 / `4891bb9`, 2026-08-29 ~17:0xZ):

| Fact | Value |
|---|---|
| `/proc/sys/kernel/printk` | `3 4 1 7` → `console_loglevel = 3` |
| Kernel cmdline | `quiet splash rd.udev.log_level=3 … splash loglevel=3` |
| ACPI `CNVW` lines, journal `PRIORITY` | **3** (`KERN_ERR`), 3 lines, byte-identical across 4 boots |
| `systemctl --failed` | **0 units** |
| `systemctl --user --failed` (user `agent`) | **0 units** |
| Login session | user `agent`, seat0, **tty1** |

The kernel prints a message to the console only when `level < console_loglevel`. The ACPI lines
are level 3 and the console loglevel is 3, so **they are already suppressed from the console.**
They live in the journal, which is where they belong.

**Consequence for the directive's item 1:** lowering to `loglevel=2` cannot remove console text
that is not being printed, and it would additionally suppress `KERN_CRIT` (level 2) — the class
`modules/boot-branding.nix` explicitly preserves because it diagnosed the 2026-07-29 ahci/VMD boot
brick. See §6.

**What is still unmeasured:** the actual glyphs on Dillon's screen. No photograph has captured the
text region; the one image on record shows a tiling-WM status bar (`vol 15% · enp0s31f6 · bat
100%`). Every claim that the ACPI lines *are* what he sees is an inference from "there are errors
in the journal," not an observation of the screen. This spec does not rest on that inference, and
§7 says how to close it.

## 2. Surface

`agos-log` — a first-class, named view. Two entry points, one renderer:

- **`agos-log`** from any shell (the primary; discoverable, greppable, works over SSH).
- **tty2**, a getty running the same renderer as its login shell, reachable with `Ctrl-Alt-F2`
  from the splash or the WM. This is the one that matters when the box looks stuck and the
  operator cannot get a shell.

The splash (tty1) stays exactly as it is. The log console is a *destination*, not a second stream.

## 3. What feeds it

| Source | Command | Purpose |
|---|---|---|
| Kernel, this boot | `journalctl -b -k -o json` | firmware + hardware lines, with `PRIORITY` |
| All units, this boot | `journalctl -b -p err -o json` | userspace errors |
| Unit state | `systemctl --failed`, `systemctl list-jobs` | **real** failures and **waits** |
| User session | `systemctl --user --failed` (session user) | the half a system-only check misses |

`list-jobs` is not optional. It is the only source that distinguishes *waiting* from *broken*, and
it is what the WP-C1 acceptance line lacked.

## 4. Rendering: three bands, visually distinct

The renderer's whole job is that a real failure **cannot** be mistaken for noise.

**Band 1 — ACTION NEEDED** (red, top, never collapsed). A unit in `failed`; a device that did not
appear; a filesystem mounted read-only. Each entry carries *what it does*, *what broke*, *what to
try*. Empty state is a printed line, not an absence: `No failures. 0 units failed.`

**Band 2 — WAITING** (amber). Every job in `list-jobs`, with its elapsed time and its timeout.
This is the band that exists because of the prewarm finding:

```
WAITING   agos-boot-prewarm.service — warming the model KV cache
          elapsed 6m12s · timeout: NONE (TimeoutStartUSec=infinity)
          multi-user.target and graphical.target are queued behind this.
          Normal range on this machine: ~11 min. Longer than ~25 min is not normal.
```

A wait with no timeout is rendered as `timeout: NONE` in the same amber as the wait itself. A boot
that cannot fail loudly should at least be *visibly* unable to fail.

**Band 3 — KNOWN / HARMLESS** (grey, collapsed to one line each, expandable). Matched against a
small table of explained signatures shipped with the OS:

```
KNOWN     firmware ACPI quirk (CNVW / Intel CNVi Wi-Fi) — 3 lines, every boot
          The BIOS's ACPI tables reference a symbol they do not contain. The kernel
          aborts the method and continues. Wi-Fi works. Not caused by Agent OS;
          not fixable from Agent OS. Present since first boot (2026-08-22).
```

Anything unmatched renders in Band 3 as `UNEXPLAINED` in plain grey with its raw text — never
silently dropped. The table explains; it never filters.

## 5. Acceptance — written so it cannot decay

The `agos-calc '23*19'` line decayed because it asserted a **CLI invocation** rather than an
**observable property**, so a subcommand rename silently invalidated it while looking fine. These
are written as properties of output, each with a **negative arm**, and they run in the VM test
harness — not as a runbook a human performs.

1. **Failure is visible.** With a deliberately-failing stub unit masked into the test system,
   `agos-log --format=json` contains an entry with `band: "action"` naming that unit.
   *Negative arm:* on an unmodified boot, no entry has `band: "action"` and the human-readable
   output contains the literal `No failures.`
2. **A wait is not a failure.** With a stub `Type=oneshot` unit that sleeps 30s before
   `multi-user.target`, the unit appears with `band: "waiting"` and **not** `band: "action"`.
   *Negative arm:* once it completes, it appears in neither.
3. **An untimed wait is marked.** For any entry with `band: "waiting"` whose unit has
   `TimeoutStartUSec=infinity`, the record has `timeout: null` and the rendered line contains
   `timeout: NONE`.
4. **Known noise is explained, not dropped.** Every journal line at `PRIORITY<=3` this boot appears
   in the JSON exactly once, in some band. *Negative arm* (the anti-filter): a line whose signature
   is absent from the table appears with `band: "known", explained: false` — asserted by injecting
   a synthetic unmatched `KERN_ERR` via `/dev/kmsg` in the test.
5. **The user session is covered.** A failing `--user` unit for the session user produces a
   `band: "action"` entry. *Negative arm:* removing it clears the entry.
6. **The surface exists where it is claimed.** `agos-log` resolves on `PATH`, and tty2's getty is
   `active`, asserted from unit state — not from documentation.

Criterion 4's negative arm is the one that keeps this honest: without it, a renderer that dropped
everything it did not recognise would pass every other check.

## 6. Explicitly NOT in this spec

- **`loglevel=2`.** Measured as ineffective for the stated symptom and a strict loss of `KERN_CRIT`
  diagnosability (§1). Filed back to Rabbot/Dillon rather than shipped; if it is wanted for another
  reason, that is a separate decision on stated grounds.
- **A finite `TimeoutStartSec` on `agos-boot-prewarm.service`.** A real proposal (25–30 min against
  an ~11 min normal), but it changes boot behaviour and is out of scope here. This spec makes the
  untimed wait *visible*; it does not change it.
- Any change to the tty1 splash.

## 7. The open measurement

Before or alongside build: capture the text Dillon actually sees. A photograph of the text region
during boot settles it in one shot. Until then, "the errors on the screen" names an unidentified
observation, and this spec is deliberately built to be correct either way — it relocates and
explains *whatever* the machine says, rather than targeting one guessed signature.
