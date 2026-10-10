# The broker as a service (spec, v0)

Status: SPEC for review. No code in this PR. It closes the gap named in
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
  So it would deny everything, now "by failure".
- Every VM test that exercises the wall (`cap-composed-path`, `app-approve-confirm`) runs the
  broker as root under `systemd-run`. That path works end to end; this spec makes it the
  production path.
- One wall run handles one request (`_wall` writes one line, reads one verdict). Audit and taint
  serialize their own state with `flock`. The broker is single-flight within one process.

## 2. Design

### 2.1 Units

```
agent-os-wall.socket      ListenStream=/run/agent-os/wall.sock
                          SocketUser=root  SocketGroup=agent-os-wall  SocketMode=0660
                          Accept=yes  MaxConnections=1  Backlog=16
agent-os-wall@.service    one instance per connection; StandardInput=socket, StandardOutput=socket,
                          StandardError=journal; runs the wall pipeline (2.2); RuntimeMaxSec=150
```

- **One connection, one request, one instance.** Matches `_wall` exactly; nothing long-lived holds
  state between requests except audit, taint and the broker anchor, which already live on disk.
- **`MaxConnections=1`**: one wall instance at a time system-wide. This keeps the threat model's
  single-flight property (taint consult → decision → taint effect, TOCTOU-free) across processes,
  not just within one. Further connections wait in the backlog. A confirm that waits on a human
  therefore blocks other tool calls for up to the confirm window; that is the v1 behaviour, and the
  same as today's single-flight broker.
- **Only the agent can connect.** `agent-os-wall` is a dedicated group whose only member is `agent`.
  Other local users cannot open the socket (mode 0660). The socket directory `/run/agent-os` is
  root 0755.

### 2.2 The pipeline inside the instance

`agent-os-wall@.service` runs one fixed store script:

1. Read the peer's credentials from the socket on fd 0 (`SO_PEERCRED`: uid, gid, pid). Refuse
   (close without a verdict, which the client reads as a deny) unless the uid is the `agent`
   account's uid, compiled into the script. Export the uid/pid as `AGENT_OS_PEER_UID` /
   `AGENT_OS_PEER_PID` for the audit record (2.4).
2. Exec `mcp parse | broker run` using the installed, pinned wrappers (`python3 -I`, clean env, seams
   pinned), the same artefacts the VM tests drive. No shell interpolates request bytes: the socket
   is the pipeline's stdin.

### 2.3 Privilege and confinement of the broker instance (v1: root, hardened)

The broker needs: append the audit log, read and write taint and broker state, start transient
system units for capability impls (D-Bus to PID 1, which polkit allows for uid 0), write the
confirm console (`/dev/tty2`), read the confirm relay secret, and reach the confirm relay.

**v1 runs it as root with a tight unit**, because every one of those is already exercised as root
in the VM tests, and splitting it across a dedicated uid plus polkit rules is a second, separate
change (section 6, question 1). Target unit properties, each asserted in the VM test with
`systemctl show` and the escape attempt where one exists:

- `NoNewPrivileges=yes`, `ProtectSystem=strict`,
  `ReadWritePaths=/var/lib/agent-os/audit /var/lib/agent-os/taint /var/lib/agent-os/broker /var/lib/agent-os/confirm`,
  `ProtectHome=yes`, `PrivateTmp=yes`, `ProtectKernelTunables/Modules/Logs=yes`,
  `ProtectControlGroups=yes`, `RestrictNamespaces=yes`, `RestrictRealtime=yes`, `LockPersonality=yes`,
  `RestrictSUIDSGID=yes`, `SystemCallArchitectures=native`.
- `DevicePolicy=closed` with `DeviceAllow=/dev/tty2 rw` (the confirm console only; `PrivateDevices`
  would hide it).
- `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` (D-Bus, the socket, the relay).
  `IPAddressDeny=any` plus `IPAddressAllow=` exactly the confirm relay endpoint from
  `modules/confirm-pkg.nix` (`relayEndpoints`), so the wall itself has no other egress.
- The capability impls are NOT confined by these properties: they run in their own transient units
  with the registry-derived sandbox (`modules/cap-sandbox.nix`), unchanged.
- **Environment:** systemd gives the instance a clean environment. Nothing the agent sets reaches
  the broker, the seams or the impls. This, not the wrapper hardening of #317, is the boundary
  against the agent account; #317 remains defence against propagation.

### 2.4 Audit

The broker's `route` record gains `peer_uid` and `peer_pid` when `AGENT_OS_PEER_UID` is set (they
ride `wall_env()` by prefix). A wall instance refused at step 2.2.1 writes nothing to the audit log
(the broker never ran) and logs one line to the journal.

### 2.5 The client (`bin/agent-loop`)

- `AGENT_OS_WALL_SOCKET` (pinned by `modules/agent-shell.nix` to `/run/agent-os/wall.sock`): when set,
  `_wall` connects, writes the one request line, shuts down its write side, and reads one line with
  the existing `WALL_TIMEOUT_S` (raised to 160 s so a confirm that runs its full window still
  returns; the instance's `RuntimeMaxSec=150` and the confirm backstop of 120 s sit under it).
  Any failure (no socket, refused, closed without a line, garbled, timeout) is the existing
  fail-closed deny.
- When `AGENT_OS_WALL_SOCKET` is set, `_wall` never falls back to spawning a broker.
- When unset (batteries, dev boxes), the current subprocess pipeline is unchanged.

## 3. What this does not change

- The wall's decisions, the registry, tiers, taint, the confirm frame, the capability sandboxes.
- Human break-glass (`taint reset` on tty3 as root) and owner-side `agos-approve` (sudo).
- The model never gets a shell from this: the agent account still has its bash (agent-shell.nix);
  what changes is that privileged effects now require the broker's yes, because only the broker
  can produce them.

## 4. Invariants and tests

- **B1** On the image, the only process that can append audit, write taint, or start a capability
  unit is a wall instance (root, systemd-started). The agent cannot do any of them directly (VM:
  writes and `systemd-run` as agent fail).
- **B2** A wall instance's environment is systemd's, not the caller's (VM: plant `AGENT_OS_*` and
  `PYTHONPATH` in the agent's environment, read `/proc/<pid>/environ` of the instance and the confirm
  child; none present).
- **B3** Only the agent account can connect (VM: `nobody` and a fresh user get EACCES; a connection
  whose peer uid is not the agent's is closed without a verdict).
- **B4** One instance at a time (VM: two concurrent connections, the second's instance starts only
  after the first ends).
- **B5** End to end from the agent's own shell: a T0 `file.read` through `agent-loop`'s socket path
  returns content; a T2 `app.approve` reaches the tty2 confirm and, approved, writes the store
  (reuses `tests/app-approve-confirm.nix` legs, now driven from the agent side, not root).
- **B6** The audit `route` record carries the agent's uid as `peer_uid`.
- **B7** Fail-closed client: socket absent, instance killed mid-request, garbage reply, timeout —
  each a deny (agent-loop battery, fake socket server).

Fast-lane checks: a flake check that the unit text carries every 2.3 property and the socket's
mode/group (string level, the `cap-wrapper-pinned` pattern), plus the agent-loop battery arms for
the socket client. VM test `test-wall-service` for B1-B6.

## 5. Build order

1. **Client**: `agent-loop` socket path + battery arms (fake server). Not wall code; normal review.
2. **Service**: `modules/wall-service.nix` (socket + template unit + group + the pipeline script with
   the peer check), broker `peer_uid`/`peer_pid` fields, `agent-shell.nix` pins
   `AGENT_OS_WALL_SOCKET`, unit-text flake check, VM test `test-wall-service`. Wall code: Fable code
   review.
3. **Re-point** `tests/app-approve-confirm.nix` to drive from the agent through the socket instead of
   root `systemd-run`, so the end-to-end test is the production path in every step.

## 6. Open questions for the spec review

1. Root now, dedicated uid later? A dedicated `agent-os-broker` uid needs: ownership of the four
   state directories, `tty` group (or a udev rule) for `/dev/tty2`, the relay secret via
   `LoadCredential=`, and a polkit rule allowing `org.freedesktop.systemd1.manage-units` only for
   transient units named `agent-os-cap-*`. Whether polkit sees the unit name for
   `StartTransientUnit` must be measured on the target systemd before relying on it. Proposed: v1
   root as above; the dedicated uid as a follow-up once the polkit detail is measured.
2. Is `MaxConnections=1` acceptable while a confirm waits on a human (other tool calls queue for up
   to the confirm window)? The alternative is concurrent instances relying on the taint/audit
   `flock`s for correctness, which changes the single-flight argument the threat model makes.
3. Should the peer check also bind the agent's login session (logind session of tty1), so a process
   the agent leaves running after logout cannot use the wall? v1 checks the uid only.
4. Should `mcp parse` move into the client (cheap early rejection) or stay server-side? This spec
   keeps it server-side: it is part of the wall, and the client is untrusted.
