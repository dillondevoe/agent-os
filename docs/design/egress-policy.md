# Egress policy: sealed agent, free human

Status: DESIGN PROPOSAL. No code in this change. Owner review required before any
implementation PR. Line references are to `origin/main` at the time of writing (the merge of
"surfaces and first login", #299) and will drift; the rendered ruleset
(`nix build .#nft-ruleset-sealed`) is the source of truth, not this document.

## 1. Goal

The owner's intent, in plain words: the person using the machine should be able to use it
normally (download things, browse the internet), while the agent, the OS services and the
"guts" of the system are sealed from the network.

Today that is not possible on the sealed variant. The wall is machine-wide: a human's browser
is dropped by the same rule that drops the agent. This document proposes moving from a
machine-wide wall to a per-user one, keeps the agent's guarantee exactly as strong as it is
now, and states honestly what the new arrangement does not protect against.

Non-goals: no change to the inbound firewall, no change to the broker or capability registry,
no new network channel for the agent, no content scanning of web traffic.

## 2. Today's policy, exactly

All references are to `modules/clean-room.nix` unless stated.

**Where it lives.** One additive nftables table, `inet agentos_egress`, one chain `output`,
hook `output`, priority 0, `policy drop` (lines 125-129). nftables traverses every table and a
drop anywhere wins, so no other module can accept a packet this chain drops.

**What it accepts, in chain order.**

| # | Rule | Lines | Notes |
|---|---|---|---|
| 1 | `ct state established,related accept`; `ct state invalid drop` | 132-133 | Return traffic of flows already allowed. uid-blind by nature. |
| 2 | `oifname "lo" tcp dport <fetch-proxy port> meta skuid != 0 drop` | 87-93, rendered at 135 | Only when `agentos.fetchProxy.enable`. Carved out BEFORE the loopback accept. Deliberately not pinned to ipv4 (a narrow drop is fail-open, a narrow accept is fail-closed; see the comment at 58-86). |
| 3 | `oifname "lo" accept` | 136 | **uid-blind.** Any local uid may connect to any loopback port: the model on `127.0.0.1:11434`, mem, and anything else that listens. |
| 4 | `meta nfproto ipv4 meta skuid <fetchUid> udp dport 53 accept` and `... tcp dport { 53, 443 } accept` | 51-55, rendered at 184 | `fetchUid` is `0` (interim) or the fetch-proxy's static uid (default 350) when the proxy is enabled (`fetchUid` at line 51). This is root's DNS and HTTPS channel for the nixpkgs cache. |
| 5 | `meta nfproto ipv4 meta skuid 0 udp sport 68 udp dport 67 accept` | 202 | NetworkManager's DHCPv4 client (uid 0). |
| 6 | `meta nfproto ipv4 meta skuid <systemd-timesync uid> udp dport { 53, 123 } accept` | 216 | Time sync. Static uid 154 from nixpkgs `ids.nix`. |
| 7 | Mesh accepts (only if `agentos.meshWireguard.enable`): `meta nfproto ipv4 meta skuid 0 oifname <iface> accept` plus per-peer outer-UDP pins | 247 and following | Admin reachability. |
| 8 | Provisioning accepts (only if `sealed = false`): `meta nfproto ipv4 udp dport 53`, `tcp dport 53`, `tcp dport { 80, 443 }` | 273-285 | **uid-blind.** Anyone on the box, including the agent, may reach any host on 53/80/443 until the seal. |

Everything else is dropped by the policy, including all IPv6 (see the note at lines 287-293:
NDP is dropped, so v6 is dead by construction, and every scoped accept carries
`meta nfproto ipv4` so that it cannot widen to v6 if NDP is ever allowed).

**Consequence for a person at the keyboard.** On the sealed variant there is no human account
at all. `configuration.nix:87` defines one interactive user, `agent`, and
`modules/agent-shell.nix:31` autologins it on tty1 (the open variant adds a separate
`operator`, `configuration-open.nix:297`). Whoever sits at the machine is, as far as the
network is concerned, the agent's uid, and that uid has no path out. A browser run there would
fail every connection. The only people-facing network paths are the mesh (admin SSH inbound)
and the tty3 break-glass root shell, which is root and therefore bound to the cache-only
channel.

**Related facts the proposal builds on.**

- Root's egress can be exchanged for the fetch-proxy's (`modules/fetch-proxy.nix`): uid 0
  loses 53/443 and the proxy uid gets them, with a hostname allowlist the packet filter cannot
  express.
- DNS is in-process glibc through NetworkManager, with no `systemd-resolved` (comment at
  lines 120-123 of the source), so a lookup egresses as the uid that made it.
- The model is bound to `127.0.0.1` and an eval assertion keeps it there
  (`config.services.ollama.host == "127.0.0.1"`, line ~303; `modules/brain.nix:15`).
- The capability layer separately denies loopback, RFC1918 and link-local targets for the
  agent's confirmed fetches (`docs/phase2-threat-model.md`, INV-2).

## 3. Proposed policy

### 3.1 Principle

Egress is decided by WHO owns the socket, not by which machine it is. Three classes:

| Class | Who | Egress |
|---|---|---|
| Sealed | the agent uid, every system service uid | default DROP, unchanged |
| Narrow channels | root (cache, via the fetch-proxy uid when enabled), timesyncd, DHCP, mesh | exactly today's rules, unchanged |
| Open | accounts in the group `agentos-humans` | ALLOW |

The delta is one new accept class. Nothing the agent can do today becomes possible, and the
agent's rules are not edited, because the agent never had any.

### 3.2 The rule shape

Written in the existing table, in the existing chain, placed after the loopback guards (3.5)
and before the provisioning block. Illustrative, not final syntax:

```
# humans: any destination, v4 now (3.4 covers v6)
meta nfproto ipv4 meta skgid 360 accept
```

Design points:

- **Numeric id, static.** The gid is rendered from `config.users.groups.agentos-humans.gid`,
  pinned like the fetch-proxy uid and for the same reason: the sandboxed `nft --check` gate has
  no user database, so a name would fail there and only surface at `nixos-rebuild` on the box.
  A proposed value is 360 (adjacent to the proxy's 350, outside the nixpkgs static range); to
  be confirmed against `ids.nix` when implemented.
- **`skgid` matches the process's primary (filesystem) group, not its supplementary groups.**
  This is the kernel's behaviour for `meta skgid` (it reads the socket owner's file credentials),
  and it is easy to get wrong: a human whose primary group is `users` and who is merely a
  member of `agentos-humans` would NOT match. Two ways to handle it, to be chosen at
  implementation: make `agentos-humans` the primary group of every human account (simplest, and
  what the VM test must prove), or match on a uid set (`meta skuid { 1000, 1001 }`) rendered
  from the declared human accounts. The test plan (section 6) includes the supplementary-only
  negative case so this cannot regress silently.
- **Eval-time assertions** (the existing style in `configuration.nix`): the agent user and every
  `isSystemUser` account must not be in `agentos-humans`; `agentos-humans` must not be the
  agent's primary or supplementary group; the group has a fixed gid. A drifting membership is
  the one way this policy fails open, so it is asserted, not documented.
- **The agent needs no new line.** It stays default DROP by falling through. Do not add
  `meta skuid != <agent> accept`: that is a deny-list shape, and a new uid would be open by
  default. The accept is an allow-list on the human group.
- **Root and service uids are untouched.** Root keeps only the channels above. Note that a
  human who is also an administrator can `sudo`; that is the human's own authority and is not an
  agent path (the agent has no sudo and is not in `wheel`; `configuration.nix` assertions).

### 3.3 DNS and DoH

- A human's lookups egress as the human uid and are covered by the group accept. No DNS-specific
  rule is needed for humans, including a browser's own DNS-over-HTTPS (it is just 443 as that
  uid).
- **The agent must have no DNS path.** Today that holds because there is no resolver the agent
  can reach: root's 53/443 is by uid and glibc resolves in-process. The proposal must keep it
  that way, and there are two traps:
  1. Do not enable `systemd-resolved` (the source already flags it). Its stub listens on
     loopback (`127.0.0.53`), the loopback accept is uid-blind, and the resolver then asks
     upstream as its own uid. The agent could resolve arbitrary names through it, which is a DNS
     exfiltration channel (payload in the query names) with no `:443` involved. The same applies
     to NetworkManager's dnsmasq mode or any local caching resolver.
  2. A "human" proxy or VPN that listens on loopback (a local SOCKS port, a Tor client, a
     commercial VPN's helper) is a path for ANY uid that can reach that loopback port, and it
     egresses as its owner. See 3.5 and open question 7.
- Timesyncd keeps its own 53/123 line; it does not generalise.

### 3.4 IPv6

Today v6 is dead and doctrine is "v4 only" (lines 287-293), enforced two ways: NDP is dropped,
and every scoped accept is pinned `meta nfproto ipv4`. What that implies for this change:

- The new human accept must carry the same pin, for the same reason: pinning keeps the rule on
  the family it was reasoned about, and the pin means that turning v6 on later is an explicit,
  reviewed act.
- **Phase 1 (this design's default): humans get IPv4 only.** Most networks, including home
  networks and the sites a person browses, work over v4. The cost is that a v6-only or
  NAT64-only network does not work for the human. That is stated, not hidden.
- **Phase 2: v6 for humans, as a separate PR with its own tests.** It needs three things, each
  with a different uid story:
  1. Neighbour/router discovery. The kernel sends NS/RS with no owning socket, so `skuid` and
     `skgid` can never match it. It has to be a type-scoped, link-local-scoped accept
     (`icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit }`, destination
     `fe80::/10` or `ff02::/16`), as the existing note already prescribes. That is uid-blind
     and so a small covert-channel surface (link-local, fixed ICMP types); it must be stated
     as such and kept to those types.
  2. A v6 human accept, `meta nfproto ipv6 meta skgid 360 accept`.
  3. NetworkManager's DHCPv6 client (uid 0, udp 546 to 547) if the network needs it, shaped like
     the DHCPv4 line.
- **v6 loopback is already open to everyone.** `oifname "lo" accept` is family-blind, so
  `::1` has the same uid-blind property as `127.0.0.1`. Anything a human runs that binds
  `[::1]` or dual-stack `localhost` is reachable by the agent. Section 3.5 treats both families
  together.
- Deliberately NOT carrying a family pin on any new DROP rule, per the fail-open argument at
  lines 58-86: pin accepts, never narrow drops.

### 3.5 Loopback: the uid-blind accept becomes a problem the moment a human exists

Line 136 was safe when every local uid was the agent or a service. With a human on the box it
is a two-way hole.

**Direction A: human (and the human's browser) to the agent's services.** The model endpoint
`127.0.0.1:11434` is reachable by any process of the human uid, including a browser tab.
**Direction B: agent to the human's loopback listeners.** The agent can connect to any port a
human process listens on: a dev server, a browser started with a remote-debugging port, a
database, a local web UI that trusts "it came from localhost".

Proposed shape:

1. **Human to the agent's service ports: drop by default.** Before the loopback accept, drop
   `meta skgid 360` to the destination ports of the agent-side services (the model, mem, the
   fetch-proxy is already guarded for non-root). Rendered from the options that define those
   ports, so the list cannot drift from the services. The human talks to the agent through the
   agent shell and the broker, not by hitting the model's HTTP API from a browser. Quick-ask
   (`surfaces-and-first-login.md` section 7) must therefore run as a narrow helper on the agent
   side, not as the human's own curl.
2. **Agent to loopback: allow-list, not blanket.** Replace the uid-blind accept, for the agent
   uid only, with an accept scoped to the agent's known service ports, then drop the rest of the
   agent's loopback traffic. This requires an inventory of the agent's real loopback ports
   (the comment at line 135 says "the local brain, mem, everything loopback"); that inventory
   is step zero of implementation and is an open question (8). If the inventory shows the agent
   needs no loopback listeners beyond the model, the allow-list is one port.
3. **Both families.** Every loopback rule above is written without an `nfproto` pin, so `::1`
   is covered.
4. **Unix sockets are not covered by nftables.** Filesystem permissions protect path sockets.
   Abstract-namespace unix sockets are scoped by network namespace, not by uid, so the agent
   can connect to a human's abstract socket (the X11 abstract socket is the common one). This is
   a residual of sharing one network namespace; see the alternatives in 3.7 and the session
   separation mitigation in section 4.

#### Can web content drive the model through loopback?

The risk: the human visits a page, and script on that page sends requests to
`http://127.0.0.1:11434` to make the local model do something, or to pull weights, or to read
what is there. The browser is a process of the human uid and (without 3.5.1) is allowed by the
loopback accept.

What the model server does, read from source at the time of writing (upstream `main`):

- **CORS allow-list.** `envconfig.AllowedOrigins()` returns `OLLAMA_ORIGINS` if set, plus
  `http(s)://localhost`, `127.0.0.1`, `0.0.0.0` (with and without a port), and the schemes
  `app://*`, `file://*`, `tauri://*`, `vscode-webview://*`, `vscode-file://*`.
  <https://raw.githubusercontent.com/ollama/ollama/main/envconfig/config.go> (function
  `AllowedOrigins`).
- **Router wiring.** `GenerateRoutes` builds the CORS config from that list and installs
  `cors.New(corsConfig)` and then `allowedHostsMiddleware(s.addr)` on every route.
  <https://raw.githubusercontent.com/ollama/ollama/main/server/routes.go> (`GenerateRoutes`).
- **Origin enforcement is server-side, not only advisory.** The CORS middleware used
  (`gin-contrib/cors`) aborts a request that carries an `Origin` header not in the allow-list
  with HTTP 403 (`applyCors`: `if !cors.isOriginValid(c, origin) { c.AbortWithStatus(403) }`).
  A browser attaches `Origin` to cross-origin POSTs, including "no-cors" simple requests, so a
  public web page's blind POST is rejected by the server, not merely unreadable by the page.
  <https://raw.githubusercontent.com/gin-contrib/cors/master/cors.go>.
- **Host-header check against DNS rebinding.** `allowedHostsMiddleware`: when the server is
  bound to loopback, a request whose `Host` is not a loopback/private IP, `localhost`, the
  machine hostname, or a `.localhost`/`.local`/`.internal` name gets 403. A rebinding page
  (attacker name resolving to `127.0.0.1`) sends `Host: <attacker name>` and is refused.
  `routes.go`, functions `allowedHost` and `allowedHostsMiddleware`.

What that does and does not give us:

- It stops the main attacks from an ordinary remote web page: cross-origin read, blind
  cross-origin write, and DNS rebinding.
- It does **not** stop a page served from an allowed origin. Anything the human serves on
  `http://localhost:<any port>` (a dev server, a local web app, a downloaded HTML file opened
  as `file://`, an Electron or Tauri app) is on the allow-list, by design of that server. A
  malicious or compromised local web app, or a malicious `file://` page the human opens, can
  drive the model.
- It is a defence in the server we do not control, by a default that can change between
  releases and that an operator can widen with `OLLAMA_ORIGINS`. It is the second layer, not
  the first.
- Non-browser processes send no `Origin` header and are not subject to CORS at all; only the
  firewall (3.5.1) and the account split stop a human-uid process (malware included) from
  talking to the model.

So the control is the firewall: **3.5.1 drops the human group from the model port.** The
server's CORS and Host checks stay as defence in depth. An implementation PR must also pin the
server settings (`OLLAMA_ORIGINS` not widened, asserted at eval time) and add a VM test that a
request from the human uid to the model port fails (section 6).

### 3.6 What stays unsealed on purpose

The human is not sandboxed by this policy. The human's browser can reach anything the human's
uid can reach, including other machines on the local network and the mesh interface if its
accepts permit (today only uid 0 does). Restricting a person's own browsing is not a goal.

### 3.7 Alternatives considered

- **A separate network namespace for the agent** (an empty netns with only a veth or a unix
  socket to the model). Strongest: it removes the uid-blind loopback problem and the abstract
  socket residual at the root, because the agent has no shared loopback with the human. Cost:
  every agent unit and capability sandbox must be moved into it, and the model endpoint must be
  re-exposed to it. Larger change; a good phase 3 and the likely end state. This proposal does
  not block it: the per-uid rules remain as the second layer.
- **`socket cgroupv2` or `meta cgroup` matches** to scope by systemd unit instead of uid. Useful
  for service uids that share an account, not needed while every service has its own uid.
- **A forward-hook wall.** Not applicable: this table is on the output hook. Traffic that is
  forwarded rather than locally generated (containers with NAT, a libvirt bridge, a hotspot)
  bypasses uid matching entirely. The image must not enable forwarding; a test asserts
  `net.ipv4.ip_forward` and `net.ipv6.conf.all.forwarding` stay 0 (section 6).

## 4. Threat table

Plain words. "The seal" means: the agent and the OS have no network path except the listed
channels, and the agent has no path to root.

### 4.1 What the seal still guarantees under this policy

| Guarantee | Why it holds |
|---|---|
| The agent has no network path off the machine. | Its uid has no accept and falls to `policy drop`; it is not in `agentos-humans` (asserted). |
| OS services and root have no direct path to the internet. | Only today's channels remain: the cache (through the fetch-proxy when enabled), DHCP, time sync, and the mesh for root. |
| The agent cannot get root. | Unchanged: no `sudo`, not in `wheel`, no password. |
| The agent cannot use the human's network by changing its uid. | Changing uid needs root. |
| The seal fails loud. | Unchanged: the wall failing to load drops the network and withholds the agent. |

### 4.2 What it does NOT guarantee, and what we do about it

| Not guaranteed | In plain words | Mitigation (design requirement) |
|---|---|---|
| Malware the human runs | A program the human downloads and runs has the human's network. It can steal the human's files and reach the internet. The seal is about the agent, not about the human's choices. | Out of scope to prevent; stated plainly in the public claim. The human's files are separated from the agent's (below), so such malware does not automatically get the agent's memory, keys or model state; those are in the agent's account, unreadable to the human uid by default. |
| Prompt injection through content the agent reads | A web page or document the human downloads can contain text that tells the agent what to do. The agent has no network, so it cannot send anything out directly, but it can be steered into acting on local files or asking the human to approve something. | Existing taint tracking and confirm tiers (`phase2-threat-model.md`). Content from outside is tainted at the hand-over. Human-always confirms for outward actions stay. This design adds no new channel for the agent, and is not a defence against injection by itself. |
| Files the human downloads reaching the agent | A file moved into the agent's reach is untrusted input to the model, and could be crafted to exploit a parser. | The downloads folder is NOT readable by the agent. Moving a file to the agent is a capability the human confirms (a named, confirmed hand-over, tainted on arrival), never ambient access. |
| A browser in the same desktop session sharing screen and input with agent processes | On one desktop, programs of one user (and on X, any client) can often read the screen and send keystrokes. An agent process in the human's session could watch the browser or type into it. | Separate sessions or seats: the agent and the human are different accounts in different sessions (the same rule the surface design uses for confirmation, `surfaces-and-first-login.md` 2.1). No screen-capture and no input-injection capability is exposed to the agent. A compositor or X server shared between the two is a design smell and is flagged in review. |
| The agent driving the human's browser | If the agent can reach the browser's debugging port, its automation interface, or its profile, it gets the human's network and logged-in sessions by proxy. | The agent cannot read or write the human's browser profile (home directory modes, separate accounts). The browser is not started with a remote-debugging port. The agent's loopback is allow-listed (3.5.2), so a stray local debugging port is unreachable. No capability for desktop or browser control is added. |
| Local web content driving the model | See 3.5. A page from an allowed local origin can talk to the model's HTTP API. | The firewall drops the human group from the model port (3.5.1). The server's CORS and Host checks are the second layer. |
| A human-run proxy, VPN or Tor client on loopback | A listener owned by the human is a path for any process that can connect to it, including the agent. | Agent loopback allow-list (3.5.2); open question 7. |
| Anything that crosses via the human by hand | The human can copy text out of the agent's output into the world. | Out of scope. It is the human's decision. |
| What the broker's confirm shows | Unchanged. | Unchanged. |

The honest summary: this design makes the agent's isolation depend on the account boundary
(files, sessions, loopback) as well as on the packet filter. Before this change the filter
alone was enough, because there was no one else on the box. After it, the account boundary
becomes load-bearing, and so must be tested like the filter is.

## 5. The public claim

### 5.1 Wording that stays true

Use, in the README and SECURITY.md:

> On the sealed variant, the agent, its local model and the operating system's services have no
> network path off the machine, except for the nixpkgs binary cache and time sync. You, the
> person using the computer, can browse and download normally; your own programs run with your
> network access, not the agent's. The agent cannot become you, and cannot become root.

Do NOT use:

- "The machine is safe" / "the machine is secure". Malware the human runs is not stopped.
- "Nothing leaves the machine" / "nothing leaves the machine unless you send it". The human's
  browser sends data; this is also false of software the human installs. The claim is about the
  agent and the OS, not the machine.
- "Offline". The machine is online for the human.
- "No telemetry on the machine". Say "the agent and OS services cannot send telemetry"; a
  browser the human installs is not covered.

### 5.2 Existing sentences that must change when this ships

| File | Lines | Current | Why it becomes false or misleading |
|---|---|---|---|
| `README.md` | 16-18 | "an outbound-default-DROP firewall enforces that nothing leaves the machine" | A human can send data out. Replace with the 5.1 wording. |
| `README.md` | 60-62 | "the nftables firewall drops all outbound traffic except the nixpkgs binary cache and time sync, so the agent and its model have no path off the box" | The first half is false for the human account; the second half stays true. Reword to "for the agent, the model and the OS". |
| `README.md` | 75-78 | "clean-room egress wall (nftables default-DROP outbound; ...)" | Still true of the agent; say "per-user" and name the human exception. |
| `README.md` | 113 | Outbound firewall row: "nftables default-DROP ... `agentos-sealed`: nixpkgs cache + time sync only." | Add the human exception to the sealed column. |
| `README.md` | 117-120 | Telemetry row, and the sentence "treat 'nothing leaves the machine' as a default of the software" | Reword: the wall enforces no telemetry from the agent and OS services; it does not cover programs a human runs. |
| `README.md` | 138 | module index "default-DROP outbound; sealed = nixpkgs-only" | Per-user wording. |
| `SECURITY.md` | 22-24 | Egress invariant: "the agent, its local model and every capability have no path to move bytes off the machine: the nftables output chain is default-DROP, the only exceptions being the nixpkgs binary cache and time sync" | Remains true for the agent, but "the output chain is default-DROP, the only exceptions" is no longer literally accurate once the human group is accepted. Reword as "default-DROP for every uid except the human group". |
| `SECURITY.md` | scope list | none | Add an in-scope invariant: "a way for the agent's uid to gain a network path, including through the human group, a loopback listener, or a local resolver, is a vulnerability", and add an explicit out-of-scope line: malware the human runs, and the human's own browsing. |
| `modules/clean-room.nix` | header 1-23 | "NO path to move bytes off-box" for the agent | Update with the per-user model when implemented. |

## 6. Test plan

The repo already separates a parse gate from packet-fate tests, and its own comments are clear
about the difference: `nft --check` only proves a ruleset parses, and a widened rule parses
(flake.nix comment above `nft-ruleset-sealed`). Both layers are needed.

### 6.1 Ruleset checks (fast lane, `nix flake check`)

- Extend `nft-ruleset-sealed`, `nft-ruleset-sealed-s5` and `nft-ruleset-unsealed` so the new
  accept and the loopback drops are in the parsed ruleset (a distinct nft parse per variant).
- **Textual invariants on the rendered ruleset**, in the style of the existing structural
  checks (these catch what `nft --check` cannot):
  - exactly one rule mentions `skgid` (or the human uid set) in an accept, and it is pinned
    `meta nfproto ipv4` in phase 1;
  - no `accept` rule in the chain is uid-blind and non-loopback except `ct state`;
  - the human accept is positioned after the loopback drops;
  - the agent's uid and the gid number are not both present in any accept;
  - the proxy-port guard and the model-port drop are present on the variants that enable them.
- **Eval-time assertions** (fail at `nix flake check`, before any build): the agent and all
  `isSystemUser` accounts are not in `agentos-humans`; the gid is static; `agentos-humans` is
  not in the agent's groups; the model server's allowed origins are not widened; no forwarding
  sysctl is enabled; `systemd-resolved` and local caching resolvers are not enabled.

### 6.2 VM tests (slow lane, `vm-tests.yml`, KVM)

New file modelled on `tests/egress-uid-scope.nix` (same fixture: a sealed node with
`networkmanager.unmanaged = ["interface-name:eth1"]`, a peer on the test VLAN with listeners on
443 and 8080 and its firewall off so every denial comes from the sealed node). Addresses use
documentation ranges only (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`, and
`2001:db8::/32` for v6). Accounts are `alice` (human) and the agent.

Each leg states what a pass proves; the controls matter as much as the denials.

| # | Leg | Expect | Proves |
|---|---|---|---|
| 1 | `alice` fetches `https://<peer>:443` and `:8080` | success | The human can use the network (positive control; a dead network would pass every denial). |
| 2 | agent uid fetches the same | fail | The seal holds for the agent. |
| 3 | root fetches `:8080`, and `:443` (proxy disabled variant: allowed by today's channel; proxy enabled: denied directly) | as today | Root unchanged; the new accept did not leak to uid 0. |
| 4 | a service uid (the proxy uid, timesyncd) to an unrelated port | fail | Service uids stay sealed. |
| 5 | a process with `alice` as supplementary group only, primary `users` | fail (or pass, per the chosen matching strategy in 3.2) | The `skgid` primary-group semantics are pinned and cannot regress. |
| 6 | agent to its own loopback service | success | Agent IPC alive (control for 7-9). |
| 7 | `alice` to the model port on `127.0.0.1` and `::1` | fail | Human cannot reach the model (3.5.1), both families. |
| 8 | agent to a listener started by `alice` on `127.0.0.1:<port>` and on `[::1]` | fail | Agent cannot reach human loopback services (3.5.2). Skipped if the allow-list is not adopted. |
| 9 | `alice` to a listener started by the agent on a non-service port | success | The human's own loopback use works (e.g. a dev server). |
| 10 | agent to a local resolver port | fail | No DNS path (3.3). |
| 11 | agent to `[2001:db8::<peer>]` and `alice` to the same | both fail in phase 1; in phase 2, `alice` succeeds and the agent fails | v6 parity, in both phases. |
| 12 | Origin and Host behaviour of the model server: `curl` with `Origin: https://web.example.test` and with `Host: attacker.example.test` against the model, run as the agent | 403 | The server-side layer is present in the pinned package (a canary for an upstream default changing). |
| 13 | `sysctl` forwarding keys are 0 | 0 | No forward-path bypass. |
| 14 | unsealed (provisioning) variant: agent and `alice` to `:443` | both succeed; after the seal switch, agent fails and `alice` still succeeds | Section 7 behaviour. |
| 15 | seal-faildown: wall fails to load | network dropped, as `tests/seal-faildown.nix` | Fail-loud applies to the human too (see open question 9). |

Every deny leg should run twice in spirit: a destination the human can reach (leg 1) versus the
same destination as the denied uid, so the denial is attributable to the uid, not to routing.
Mirror the existing test's care that a missing route reads as a false green.

### 6.3 What CI can build

- Fast lane (`flake-check.yml`): the eval assertions, the three `nft-ruleset-*` parse gates, the
  textual ruleset invariants, and the personal-data gate (this document is subject to it).
- Slow lane (`vm-tests.yml`): the new uid-scope test for humans, plus the existing
  `test-egress-uid-scope`, `test-egress-mesh-uid-scope` and `test-fetch-proxy-allowlist`, which
  must stay green and unmodified in what they assert about the agent.
- Not buildable in CI: a real browser against real sites, a real `nixos-rebuild switch` through
  the real cache, v6 on a real network with real router advertisements, and hardware DHCP
  renewal. Those are at-the-box acceptance, listed as such, never claimed as CI-verified.

## 7. First-run provisioning window and the surfaces design

Seal model (as in `surfaces-and-first-login.md` 4.3, still open question 1 there): the box
starts unsealed, pulls the model, then seals.

- **During the window** (`sealed = false`): today the provisioning accepts (8 in section 2) are
  uid-blind, so the agent has DNS and 80/443 too. Under the per-user policy this can be tighter:
  scope the provisioning accepts to root and the model server's uid (whoever performs the pull)
  rather than everyone, and keep the human group open. Human egress is open throughout, so the
  person can already browse during setup (for example to read instructions). The change is
  an improvement to the agent's window as well, but it must not break `setup-brain.sh`, so it
  is a separate, tested step.
- **At the seal:** the agent's provisioning accepts are removed (they are what the rebuild
  removes today); the human accept is permanent and does not change at the seal. Seal check
  gains a precondition: the `agentos-humans` group exists and the agent is not in it, or the
  seal refuses (consistent with the surface design's "refuse to seal an unusable box").
- **Surfaces.** The surface choice (desktop, terminal, shell) is the human's. The wall does not
  depend on the surface, and a surface must not open a hole in it (the surface design's "fixed
  regardless of choice" list). New constraints from this document:
  - the graphical session for the human and the agent's session are separate, as the confirm
    channel already requires; the human's browser runs in the human's session only;
  - no desktop module may add the agent to the human group or add a network listener;
  - a surface that autostarts a service which listens on loopback must declare the port, so it
    appears in the agent's deny set (3.5.2);
  - GUI apps in the agent's session that try the network (update checks) keep failing closed,
    as the surface design already states.
- **First login creates the human.** See open question 1: on a one-account box, first login is
  where the human account is created and added to the group.

## 8. Open questions for the owner

1. **Do human accounts exist at all on a single-user box?** Today the only interactive account
   is `agent`, autologged on tty1 (`modules/agent-shell.nix:31`). Options: (a) first login
   creates a separate human account (this document's assumption; the agent shell becomes a
   tool the human opens, in a separate session); (b) the first login user IS the human, and the
   agent runs as a service account with no login; (c) keep one account and no human egress
   (today). (a) and (b) change the product's feel; this is the central question.
2. **Guest browsing.** Should there be a throwaway, no-persistence guest account that is in the
   human group, for a shared machine? It would need a tmpfs home and no access to anything.
3. **Download scanning.** Should files the human downloads be scanned (antivirus) before the
   agent can be handed them, or is the confirmed hand-over (taint on arrival) enough? A scanner
   is more trusted code and, if it fetches signatures, needs its own egress.
4. **Where does the human's downloads folder live,** and should it be on a separate mount
   (`noexec`, `nosuid`, `nodev`) so that "downloaded, therefore not executable by default"
   becomes a property, not a habit?
5. **Browser choice and shipping.** Does the image ship a browser? A sandboxed browser has its
   own helper processes and its own loopback habits; the policy treats it as just the human
   uid, which is simple but gives it everything.
6. **Local network and mesh for the human.** Should the human accept exclude RFC1918,
   link-local and the mesh interface (so a browser cannot be used to attack the home router or
   mesh peers)? Cheap to add as destination drops before the accept, at the cost of breaking
   printers, NAS and local dev. Default proposed: allow.
7. **Tor and VPN.** A human VPN or Tor client changes where the human's traffic goes and, if it
   listens on loopback, creates a path for the agent unless 3.5.2 holds. A WireGuard-style VPN
   needs the kernel-generated outer-UDP pin like the mesh's. Do we support them in the image,
   document them as unsupported, or leave them to the human?
8. **Loopback inventory.** Which loopback ports does the agent legitimately need beyond the
   model? The allow-list in 3.5.2 depends on the answer, and so does the test in leg 8.
9. **Fail-loud for the human.** If the wall fails to load today, the machine drops the network
   and withholds the agent. Under this policy, is "no network for anyone" the right failure
   for the human too (the current behaviour, and what this document assumes)?
10. **v6 timing.** Is IPv4-only for humans acceptable for the first release, with v6 as the
    separate follow-up in 3.4?
11. **Status of the interim root rules.** The human policy is cleaner if the fetch-proxy is
    enabled on the shipped sealed variant, so root has no direct 53/443. Is that already the
    plan, or does the interim `skuid 0` pair continue to ship?

## 9. Order of work (for later PRs, each reviewed on its own)

1. Inventory of agent loopback ports and the model's listener set (read-only; feeds 3.5).
2. `agentos-humans` group, static gid, eval assertions, no firewall change. Tests: eval only.
3. Human accept (v4), loopback drop of the model port for the group, ruleset invariants, and
   the VM test legs 1-5, 7, 9, 13. Update the README and SECURITY.md in the SAME PR (section
   5.2), never before.
4. Agent loopback allow-list (3.5.2) and legs 6, 8, 10.
5. Provisioning-window tightening (section 7).
6. v6 for humans (3.4), then, if wanted, the agent network namespace (3.7).

No step ships a public claim stronger than the tests behind it.
