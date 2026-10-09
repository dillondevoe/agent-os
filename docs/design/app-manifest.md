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
| `files[]` | R4: absolute or `~/`, resolves (symlinks followed) strictly under `$HOME` (never `$HOME` itself); R5: never a credential location, a credential-looking name, **or an ancestor of one** (`~/.local`, `~/.config` are refused because `~/.config/gh` and the approvals file live below them); R6: `mode` is `r` or `rw`; R13: the path must exist, except a missing `rw` path, which the runner creates as a 0700 directory the app owns; R14: entries must not nest (one under another, or over the app dir), so no bind can shadow another | the only paths visible besides the app dir |
| `network` | R7: list of bare domain names; empty means none | **not enforced per-domain in v0: always denied** (section 5) |
| `devices` | R8: must be empty in v0 | no device access exists yet |
| `limits` | R9: `cpu_pct` 1-100 (default 50), `mem_mb` 16-8192 (default 512), `wall_s` 1-86400 (default 300) | cgroup limits via `systemd-run --scope` |
| `schedule` | R10: systemd `OnCalendar` string or empty, checked with `systemd-analyze calendar` | a timer the owner can see and revoke; installed by `agos-schedule` after approval (section 4) |

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

Only `bin/agos-approve`, run by the owner outside the agent's session, writes this file; the
agent and the runner never do. The file sits inside a deny-listed directory, so no app can
bind it. Consequences, all enforced by the runner:

- Not in the file: refused (exit 4).
- Any edit to the manifest after approval: different hash, refused until re-approved.
- Entry recorded under a different `name`: does not count.
- Corrupt file: approves nothing.
- Revoke: delete the entry.

`--approve-for-test` bypasses the lookup for batteries and prints a loud warning; it is not
an approval and must never be used by the agent.

### How the owner approves (v0, `bin/agos-approve`)

    agos-approve show    <appdir>     # render: name, version, entry, files + modes, network (v0: denied anyway), limits, schedule, sha256
    agos-approve approve <appdir>     # same render, then type the first 8 hex of the sha to record it
    agos-approve revoke  <name|sha>   # delete the entry; the runner refuses from then on
    agos-approve list

`approve` refuses (exit 3) unless it is a human outside the agent session: stdin must be a tty,
`AGENT_OS_ACTIVE` (the marker `agent-shell.nix` exports when the agent session starts) must be
unset, and the invoking account must not be `agent`. The answer is the sha prefix, not y/N, so
what is approved is bound to what was rendered: a different manifest shows a different sha.
Validation is the runner's own, imported by path, so nothing the runner would refuse is ever
offered for approval. The file is written atomically, 0600, in a 0700 directory.

### Schedules (v0, `bin/agos-schedule`)

    agos-schedule install <appdir>    # agos-app-<name>.{service,timer} in ~/.config/systemd/user, enable --now
    agos-schedule remove  <name>
    agos-schedule list                # schedule, sha prefix, and ok | STALE (manifest changed / approval revoked)

The timer's service runs `agos-run <appdir>` and nothing else, so a scheduled fire passes the
same sandbox and the same approval check as a run by hand: the clock cannot widen the app.
`install` refuses an empty `schedule`, an unapproved manifest (the owner approves the schedule by
approving the manifest that carries it), and a host without `systemctl`. Because `schedule` is
inside the hashed manifest, editing it changes the sha and the runner refuses every fire until
the owner re-approves; `list` shows that timer as STALE. Units carry `X-AgentOS-Sha` so `remove`
and `list` only ever touch units this tool wrote.

This is not the confirm channel. `bin/confirm` is a pure relayer the broker drives with a nonce
and never writes state; routing app approval through broker → confirm → Telegram/getty inside
the sealed image is a later slice (§7).

## 5. Enforcement (v0, `bin/agos-run`)

Never unsandboxed: no bubblewrap means refusal (exit 5). The argv, in order:

1. `systemd-run --user --scope -p MemoryMax=<mem_mb>M -p CPUQuota=<cpu_pct>% -p RuntimeMaxSec=<wall_s>` when a user manager answers a probe (`systemd-run --user --scope -- true`). A binary without a reachable user manager (container, nix sandbox, non-lingering ssh) or `AGOS_RUN_NO_SYSTEMD=1` drops only this prefix and prints "limits: not enforced"; the sandbox stays.
2. `bwrap --unshare-all --unshare-net --die-with-parent --new-session`
3. `--ro-bind / /` then `--proc`, `--dev`, `--tmpfs /tmp`, `--tmpfs /run`, `--tmpfs $HOME`: the whole system is visible read-only, the home directory is empty.
4. Missing `rw` paths are created on the host only now, after validation and approval, never on `--dry-run`: each missing component is created 0700 in turn, nothing is followed through a file or symlink, and any failure (a regular file in the way, permissions) is a named refusal, R15, before anything runs. Then `--bind <appdir> <appdir>` and one `--ro-bind` (`r`) or `--bind` (`rw`) per `files[]` entry (siblings only, R14, so order cannot shadow), then `--remount-ro $HOME` so the mount-point directories bwrap created inside the tmpfs are not writable scratch (non-recursive, the rw mounts under it stay rw).
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

1. App approval through the confirm channel (broker → `bin/confirm` → Telegram/getty) for the sealed image; v0 is the owner-side `bin/agos-approve` on the human's own login.
2. ~~Timer installation from `schedule` as a user unit, listed and revocable.~~ Done: `bin/agos-schedule`.
3. Per-domain network for apps, on top of the egress work.
4. An "apps" surface: list running apps, what each can touch, one-tap revoke.
