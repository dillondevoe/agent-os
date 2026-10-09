# App manifest v0: what an agent-made app may touch

Status: PROTOTYPE SPEC with a runner (`bin/agos-run`) and a battery
(`tests/agos-run-battery.py`). Owner review required before any app made by the agent runs
on a real machine. The shape matches what `evals/tasks/app-generation.json` already scores, so
a model that passes the eval produces a manifest this runner accepts.

## 1. Why

The vision (owner, 2026-10-05): the agent builds small apps and automations on the fly, and
each one runs in its own sandbox with a phone-style permission manifest the owner approves
once. The safety rule that matters: no single app gets private data, untrusted content and a
path to the network at once without an explicit approval. The manifest is the unit of
approval; the runner is the unit of enforcement.

## 2. Where app code lives (decided here)

Every app lives in `~/.local/share/agent-os/apps/<name>/` and gets read-write access to that
directory implicitly. Nothing else is implicit. This answers the eval baseline's open
question (an app asking `rw` on its own files in `/tmp` was asking for the wrong thing; its own
directory is the right place and costs it nothing). The apps root is itself on the credential
deny list, so one app cannot name another app's directory in `files`.

## 3. The manifest

`manifest.json` in the app directory. Exactly these keys; unknown keys are refused.

```json
{
  "name":     "gym-spending",                 
  "version":  "0.1",
  "entry":    ["python3", "report.py"],
  "files":    [{"path": "~/Documents/bank.csv", "mode": "r"}],
  "network":  [],
  "devices":  [],
  "limits":   {"cpu_pct": 25, "mem_mb": 128, "wall_s": 30},
  "schedule": ""
}
```

| key | rule | meaning |
|---|---|---|
| `name` | R1: `^[a-z0-9][a-z0-9-]{0,39}$`, must equal the directory name (R11) | identity |
| `version` | R2: non-empty string | shown at approval; any change changes the hash |
| `entry` | R3: argv list, non-empty strings | run from the app directory, `$HOME` and `$PATH` only |
| `files[]` | R4: absolute or `~/`, resolves (symlinks followed) under `$HOME`; R5: never a credential location or credential-looking name; R6: `mode` is `r` or `rw` | the only paths visible besides the app dir |
| `network` | R7: list of bare domain names; empty means none | **not enforced per-domain in v0: always denied** (section 5) |
| `devices` | R8: must be empty in v0 | no device access exists yet |
| `limits` | R9: `cpu_pct` 1-100 (default 50), `mem_mb` 16-8192 (default 512), `wall_s` 1-86400 (default 300) | cgroup limits via `systemd-run --scope` |
| `schedule` | R10: systemd `OnCalendar` string or empty, checked with `systemd-analyze calendar` | a timer the owner can see and revoke; v0 records it, nothing installs it yet |

Credential deny list (R5), mirrored from the agos-do prototype: `~/.ssh`, `~/.gnupg`,
`~/.aws`, `~/.kube`, `~/.docker`, `~/.password-store`, `~/.claude`, browser profiles,
keyrings, `~/.config/agent-os/keys`, `~/.local/state/agent-os`, `~/.cache`, `~/.netrc`,
git credentials, plus any path component matching `.env`, `id_*`, `*.pem|key|p12|pfx|kdbx|gpg|asc`,
or `credentials|secrets|token`. Nothing on this list can be requested, in either mode.

Least privilege the eval already checks and the runner does not second-guess: `r` unless
writing is the point, no network unless the request needs it, never credential paths. The
runner enforces the hard rules; the eval scores the judgment.

## 4. Approval

Approval is the manifest's SHA-256 over its canonical JSON (sorted keys, no whitespace),
recorded in `~/.local/state/agent-os/approvals.json`:

```json
{"<sha256>": {"name": "gym-spending", "approved_at": "2026-10-08T01:02:03Z"}}
```

Only the confirm channel (`bin/confirm`, outside the agent's session) writes this file; the
agent and the runner never do. The file sits inside a deny-listed directory, so no app can
bind it. Consequences, all enforced by the runner:

- Not in the file: refused (exit 4).
- Any edit to the manifest after approval: different hash, refused until re-approved.
- Entry recorded under a different `name`: does not count.
- Corrupt file: approves nothing.
- Revoke: delete the entry.

`--approve-for-test` bypasses the lookup for batteries and prints a loud warning; it is not
an approval and must never be used by the agent.

Wiring the confirm channel to write this file is the next slice; today the owner adds the
line by hand after reading the manifest.

## 5. Enforcement (v0, `bin/agos-run`)

Never unsandboxed: no bubblewrap means refusal (exit 5). The argv, in order:

1. `systemd-run --user --scope -p MemoryMax=<mem_mb>M -p CPUQuota=<cpu_pct>% -p RuntimeMaxSec=<wall_s>` when `systemd-run` exists (`AGOS_RUN_NO_SYSTEMD=1` drops only this prefix; the sandbox stays).
2. `bwrap --unshare-all --unshare-net --die-with-parent --new-session`
3. `--ro-bind / /` then `--proc`, `--dev`, `--tmpfs /tmp`, `--tmpfs /run`, `--tmpfs $HOME`: the whole system is visible read-only, the home directory is empty.
4. `--bind <appdir> <appdir>` then one `--ro-bind` (`r`) or `--bind` (`rw`) per `files[]` entry, then `--remount-ro $HOME` so the mount-point directories bwrap created inside the tmpfs are not writable scratch (non-recursive, the rw mounts under it stay rw).
5. `--clearenv --setenv HOME --setenv PATH --setenv AGOS_APP <name> --chdir <appdir> -- <entry>`.

`--dry-run` prints the exact argv as JSON and exits 0; the battery asserts on it.

**Network is denied in v0 regardless of the manifest.** A non-empty `network` list is
validated, kept in the hash, and printed as "not yet enforced per-domain, DENIED in v0".
Per-domain enforcement needs the per-uid egress design (`docs/design/egress-policy.md`) or a
per-app proxy; the runner refuses to pretend until that exists.

## 6. What v0 does not do

- No per-domain network, no devices, no timer installation, no GUI confirm.
- `--ro-bind / /` exposes world-readable system files; a secret stored outside `$HOME` with
  loose permissions is visible. The home directory is the privacy boundary in v0.
- No seccomp filter; no outbound D-Bus or Wayland socket (both live under the tmpfs `/run`),
  so a v0 app has no display. A windowed app needs a deliberate socket bind, a later slice.
- Resource limits need a user systemd; without it the app runs sandboxed but unlimited.

## 7. Next slices

1. `bin/confirm` writes `approvals.json` from a rendered manifest (approve = record the hash).
2. Timer installation from `schedule` as a user unit, listed and revocable.
3. Per-domain network for apps, on top of the egress work.
4. An "apps" surface: list running apps, what each can touch, one-tap revoke.
