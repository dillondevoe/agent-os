# The broker as a service (spec, v0)

Status: SPEC, APPROVED by the Fable spec review (2026-10-10: first pass approve with required
changes, all folded in; re-review approve; rulings in section 7). No code in this PR. It closes the gap named in
`docs/design/app-approval-confirm.md` §6: today the wall decides nothing in production.

Security surface (the wall's launch path, its privilege, its environment):
branch → spec PR → Fable spec review → implementation PRs → Fable code review → merge.

## 0. Thesis

The threat model (§4) puts all real privilege in one small trusted component, the broker, with
the model on the untrusted side and the capability impls sandboxed below it. That only holds if
the broker runs somewhere the agent account cannot reach into. Today it does not run anywhere:
`bin/agent-loop` spawns `mcp parse | broker run` as siblings of its own file, which on the image
do not exist, so every tool call dies in the wall. The fix is to run the broker as a system
service on the other side of a uid boundary, started by systemd with a clean environment, and
have the agent talk to it over a unix socket that carries one JSON-RPC request per connection.

## 1. What is true today (measured, not assumed)

- `bin/agent-loop` `_wall()` runs `[sys.executable, MCP, "parse"] | [sys.executable, BROKER, "run"]`
  with `MCP`/`BROKER` defaulting to siblings of `agent-loop`. On the image `agent-loop` is a lone
  store file (`modules/agent-shell.nix`), the siblings are absent, and `AGENT_OS_MCP` /
  `AGENT_OS_BROKER` are set nowhere in `modules/`. Every call is "deny by absence".
- Even pointed at the installed wrappers, the broker would run as uid `agent`, which cannot append
  the audit log or write taint state (`/var/lib/agent-os/{audit,taint,broker}` are root 0700), and
  cannot start the capability impls' transient units on the system manager (`bin/cap-invoke`).
- The VM tests run the broker as root: `cap-composed-path` from the test's root shell (environment
  scrubbed), `app-approve-confirm` under `systemd-run` with a clean environment. That path works
  end to end; this spec makes it the production path.
- One wall run handles one request (`_wall` writes one line, reads one verdict). Audit and taint
  serialize their own state with `flock`. The broker is single-flight within one process.
- `bin/mcp` does not flush per record: its verdict reaches the broker when `mcp` exits, i.e. on
  stdin EOF. The launcher (2.2 step 2) therefore feeds `mcp` exactly one line and then EOF itself.

## 2. Design

### 2.1 Units

```
agent-os-wall.socket      ListenStream=/run/agent-os/wall.sock
                          SocketUser=root  SocketGroup=agent-os-wall  SocketMode=0660
                          Accept=yes  MaxConnections=1
agent-os-wall@.service    one instance per connection; StandardInput=socket, StandardOutput=socket,
                          StandardError=journal; runs the wall launcher (2.2);
                          RuntimeMaxSec = derived (2.3)
```

- **One connection, one request, one instance.** Matches `_wall` exactly; nothing long-lived holds
  state between requests except audit, taint and the broker anchor, which live on disk.
- **`MaxConnections=1` refuses, it does not queue** (systemd.socket(5): further connections "will be
  refused until at least one existing connection is terminated"). A concurrent connection is
  accepted and closed with no verdict, which the client reads as a deny. That keeps the threat
  model's single-flight property (taint consult → decision → taint effect, TOCTOU-free) across
  processes. `agent-loop` is itself single-flight, so only the agent's own side processes ever see
  the refusal.
- **Only the agent can connect.** `agent-os-wall` is a dedicated group whose only member is `agent`
  (2.6). Other local users cannot open the socket (mode 0660). `/run/agent-os` is created root 0755
  by systemd when it sets up `ListenStream` (no tmpfiles rule needed; the VM test asserts the mode).

### 2.2 The wall launcher (inside the instance)

`agent-os-wall@.service` runs one fixed store script, `agent-os-wall-launch` (`python3 -I`):

1. **Peer check.** Read `SO_PEERCRED` on fd 0 (kernel-set at `connect()`; the uid is not spoofable
   from the agent side). Resolve the agent's uid at runtime with `pwd.getpwnam("agent")` (root-owned
   `/etc/passwd`; the uid is allocated at install, not known at evaluation time, and pinning it would
   force a uid migration on installed boxes). Refuse, closing without a verdict and logging one
   journal line, if the lookup fails or the peer uid differs. The group gate and this check are
   redundant on purpose: the group stops other users; the uid check stops root tooling (root bypasses
   0660) from producing audit records attributed to no agent, and survives a group-membership mistake.
   A lookup that raises, or returns uid 0, is a refusal.
   Export `AGENT_OS_PEER_UID` and `AGENT_OS_PEER_PID` (the pid is informational only: pids are
   reused and a connection fd can be passed to another process of the same uid).
2. **Exactly one request.** Read bytes from fd 0 up to the first newline, at most 1 MiB; anything
   after it is ignored and the read side is closed. A missing newline before EOF, or more than 1 MiB,
   is refused without a verdict. One instance therefore never serves more than one request.
3. **The pipeline.** Start the installed, pinned `mcp parse` with that one line on its stdin
   (then EOF), pipe its stdout into the installed `broker run`, whose stdout is fd 1 (the socket).
   No shell, no interpolation of request bytes. `mcp` and `broker` stderr go to the journal; neither
   writes request bytes to stderr today (`bin/mcp`, `bin/broker`), and no future stage may.

### 2.3 Privilege, confinement and timing of the instance (v1: root, hardened)

The broker needs: append the audit log, read and write taint and broker state, write the confirm
seen-destinations file, start transient system units for capability impls (D-Bus to PID 1, which
polkit allows for uid 0), write the confirm console (`/dev/tty2`), read the confirm relay secret,
and reach the confirm relay.

**v1 runs it as root with a tight unit.** Target properties, each asserted in the VM test with
`systemctl show` (and the escape attempt where one exists):

- `NoNewPrivileges=yes`, `ProtectSystem=strict`,
  `ReadWritePaths=/var/lib/agent-os/audit /var/lib/agent-os/taint /var/lib/agent-os/broker /var/lib/agent-os/confirm`
  (complete: every write the wall makes is under these; the identity directory is read only and
  needs no entry), `ProtectHome=yes`, `PrivateTmp=yes`, `ProtectKernelTunables/Modules/Logs=yes`,
  `ProtectControlGroups=yes`, `RestrictNamespaces=yes`, `RestrictRealtime=yes`, `LockPersonality=yes`,
  `RestrictSUIDSGID=yes`, `SystemCallArchitectures=native`. Connecting to
  `/run/dbus/system_bus_socket` under the read-only `/run` is unaffected.
- `DevicePolicy=closed` with `DeviceAllow=/dev/tty2 rw` (the confirm console only).
- `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`. `IPAddressDeny=any` plus `IPAddressAllow=` the
  confirm relay endpoint from `modules/confirm-pkg.nix` (`relayEndpoints`). `relayAddr` must be an IP
  literal (a hostname needs DNS the wall has no path for, and `IPAddressAllow=` cannot express it);
  `confirm-pkg.nix` gains that check. The sealed nftables ruleset (`egress-policy.md`) also decides
  root's egress: the relay is reachable only if it is a mesh peer or an explicit root accept for
  `relayAddr:relayPort` exists. Otherwise telegram reports `confirm-relay-unreachable` and getty is
  the only channel. Stated, not assumed.
- **Timing, derived not literal.** `RuntimeMaxSec` = confirm backstop (`brokerTimeout`, 120) +
  capability timeout (`AGENT_OS_CAP_TIMEOUT_S`, 30) + unit reap (10) + margin (20) = **180 s**, computed
  in Nix from the pinned values, with an evaluation-time assertion of the ordering
  `human window (90) < confirm backstop < RuntimeMaxSec < client timeout`. A shorter cap would kill
  an approved T2 call after `approve` is audited but before `invoke` is, leaving the audit
  incomplete. The taint flock held across a human `taint reset` confirm (tty3) can also block a wall
  instance's `taint set` for up to the confirm window; the wall then denies (`taint-set-failed`),
  bounded by the same cap.
- **Environment:** systemd gives the instance a clean environment; nothing the agent sets reaches
  the broker, the seams or the impls. The unit is the single place the audit signer pair is set:
  `AGENT_OS_AUDIT_SIGNER` / `AGENT_OS_AUDIT_REQUIRE_SIGNED` from one module option (login-shell
  `environment.variables` never reaches a unit, so without this, signing silently turns off). The
  peer variables ride `wall_env()` by prefix to the seams (harmless); `bin/cap-invoke` builds the
  impl environment explicitly, so they never reach a capability impl (asserted in PR 2).
- **Ordering:** `After=` and `Requires=` on `systemd-tmpfiles-setup.service` and the identity
  minting unit, so a signing wall never starts before its identity and state directories exist (it
  would deny every call with `audit-failed`). An identity failure therefore stops the wall entirely:
  deny by absence, the fail-closed direction.

### 2.4 Audit

The broker's `route` record gains `peer_uid` and `peer_pid` when the variables are set. `peer_pid`
is advisory, never a key. A refused connection (2.2 steps 1-2) writes nothing to the audit log (the
broker never ran) and logs one journal line. systemd's instance name for an `Accept=yes`
unix connection includes the peer's pid and uid, so the journal carries the uid independently.

### 2.5 The client (`bin/agent-loop`)

- The installed `agent-loop` has the socket path compiled in (a substituted constant, the
  `IMAGE_APPROVALS` pattern; `AGENT_OS_WALL_SOCKET` is read only where the constant is empty, for
  batteries and dev boxes). When a socket path is set, `_wall` connects, writes the one request line,
  **must** half-close its write side (its half of the one-request contract: the launcher reads up to
the first newline, and a client that never ends its line fails fast rather than holding the slot), and reads one
  line under an overall deadline of `RuntimeMaxSec + 10` = **190 s**, with a 4 MiB reply cap. Any
  failure (no socket, refused, closed without a line, garbled, oversize, timeout) is the existing
  fail-closed deny. It never falls back to spawning a broker, even when the socket is absent.
- `AGENT_OS_MCP` / `AGENT_OS_BROKER` are inert when a socket path is set.
- When unset (batteries, dev boxes), the current subprocess pipeline is unchanged.
- **The client cannot abort the wall.** Today a timed-out `agent-loop` kills its broker; after this
  it can only close its socket, and an approved effect completes after the client gave up (audited,
  bounded by `RuntimeMaxSec`).

### 2.6 The group

`modules/wall-service.nix` declares `agent-os-wall` and adds it to `users.users.agent.extraGroups`,
with an evaluation-time assertion that the group's members are exactly `[ "agent" ]`, next to the
existing no-agent-root assertions. The agent's other group, `networkmanager`, grants no path to the
wall.

## 3. What this does not change

- The wall's decisions, the registry, tiers, taint, the confirm frame, the capability sandboxes.
- Human break-glass (`taint reset` on tty3 as root) and owner-side `agos-approve` (sudo).
- The agent account keeps its bash (agent-shell.nix). What changes is that privileged effects now
  require the broker's yes, because only a wall instance can produce them.

## 4. Invariants and tests

- **B1** On the image, the only process that can append audit, write taint, or start a capability
  unit is a wall instance (root, systemd-started). The agent cannot do any of them directly (VM:
  writes and `systemd-run` as agent fail).
- **B2** A wall instance's environment is systemd's, not the caller's (VM: plant `AGENT_OS_*` and
  `PYTHONPATH` in the agent's environment; read `/proc/<pid>/environ` of the instance and the confirm
  child; none present; the impl's environment carries no `AGENT_OS_PEER_*`).
- **B3** Only the agent can connect (VM: `nobody` and a fresh user get EACCES; root connecting gets
  no verdict and no audit record).
- **B4** One instance at a time: with a confirm pending, a second connection gets EOF with no
  verdict, no second instance starts, and the audit log has exactly one `route` record for the pair.
- **B5** End to end from the agent's own shell: a T0 `file.read` through `agent-loop`'s socket path
  returns content; a T2 `app.approve` reaches the tty2 confirm and, approved, writes the store.
- **B6** The audit `route` record carries the agent's uid as `peer_uid`, and `signer`/`sig` when the
  deploy sets the signer option.
- **B7** Fail-closed client: socket absent (no fallback), instance killed mid-request, garbage reply,
  oversize reply, timeout, and a server that never sees EOF (client omitted the half-close) — each a
  deny (agent-loop battery, fake server).
- **B8** One request per instance: a connection carrying two lines gets exactly one verdict and the
  audit log one `route` record; a connection with no newline before EOF gets no verdict.

Fast-lane checks: a flake check that the unit text carries every 2.3 property, the derived
`RuntimeMaxSec`, the signer environment and the ordering, plus the socket's mode/group; that the
installed `agent-loop` carries the compiled socket path (the `cap-wrapper-pinned` pattern); the
group-membership assertion; the timing-order assertion; the `relayAddr` IP-literal check. VM test
`test-wall-service` for B1-B6 and B8.

## 5. Build order

1. **Client**: `agent-loop` socket path (compiled-constant hook, half-close, 190 s deadline, no
   fallback) + battery arms (fake server). Not wall code; normal review.
2. **Service**: `modules/wall-service.nix` (socket, template unit, group + assertion, the launcher
   with the peer check and one-line read), derived timing + assertion, signer env, ordering, broker
   `peer_uid`/`peer_pid` fields, the `relayAddr` check, the installed `agent-loop` with the socket
   compiled in, unit-text flake checks, VM test `test-wall-service`. Wall code: Fable code review.
3. **Re-point** `tests/app-approve-confirm.nix` to drive from the agent through the socket instead of
   root `systemd-run`, so the end-to-end test is the production path in every step.

## 6. Residuals, accepted

- **Self-DoS:** an agent process can hold the single slot with a connection that never sends a
  newline, for up to `RuntimeMaxSec`; only the agent is affected.
- The agent authors the typed arguments the confirm frame shows; that is by design (the human
  judges them), not influence over the verdict.
- `/var/lib/agent-os/apps` is agent-owned and `app.approve` reads it; covered by the
  edit-during-prompt leg.

## 7. Spec-review rulings (Fable, 2026-10-10)

1. **Root now, dedicated uid later: agreed.** For the follow-up: `/dev/tty2` is `root:tty 0620` and
   confirm opens it read-only too, so a non-root wall needs a udev rule (or mode 0660), not only the
   `tty` group; polkit receives `unit` and `verb` for `manage-units`, so an `agent-os-cap-*` rule is
   feasible, still to be measured on the target systemd.
2. **`MaxConnections=1`: keep, with refusal semantics** (2.1). Concurrent instances relying on the
   flocks would void the single-flight argument. Revisit only if a second wall client appears.
3. **Session binding: no.** Every agent-uid process is the model, and the tty1 autologin respawn
   means the session effectively never ends. Closed as a non-goal.
4. **`mcp parse` server-side: agreed**, with the one-line, 1 MiB bound (2.2) so the parser's input
   per connection is bounded.
