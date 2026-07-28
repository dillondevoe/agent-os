# Phase 2 · Step 1 — the capability registry + tier schema + build-time invariants.
#
# This is the IMMUTABLE, operator-authored declaration of every capability the Djinn
# broker ("the wall") will ever offer. It is pure data + assertions: no runtime logic
# lives here. See docs/phase2-threat-model.md for the model this encodes.
#
# The point of this file is that a configuration which VIOLATES a security invariant
# does not EVALUATE — the assertions below throw at eval time, so `nix flake check`
# (which forces `ok`) fails. That build failure IS the mechanism-3 test.
#
# Security surface (registry + tiers + sandbox asserts are load-bearing):
#   branch -> PR -> Fable(code) -> merge. Never direct-push.
#
# Consumed as a pure function of `lib` so it can be evaluated standalone (the flake
# check imports it with nixpkgs.lib) and, in Step 7, so each capability impl's systemd
# sandbox (ReadWritePaths / PrivateNetwork / IPAddressDeny) is DERIVED from these exact,
# already-validated declarations — the assertion guards the single source of truth.
{ lib }:

let
  inherit (lib) hasPrefix all mapAttrsToList concatStringsSep length;

  # ── Tiers ──────────────────────────────────────────────────────────────────
  # T0/T1/T2 are the only RUNTIME tiers. T3 is deliberately NOT a tier a capability
  # can declare — it is the "not expressible as a request" forbidden set (below).
  runtimeTiers = [ "T0" "T1" "T2" ];

  # ── T3 — the forbidden set (non-expressible; operator-config-only to change) ──
  # These operations must never appear as a capability name. They are changeable
  # only by an operator commit through the reviewed Nix pipeline, never by a model
  # request. Listed so the assertion can prove none leaked into the registry.
  forbiddenT3 = [
    "broker.modify" "registry.modify" "audit.modify" "taint.modify"
    "tiers.modify" "credentials.read" "weights.write"
  ];

  # ── Protected paths — no capability impl may hold ANY of these in its writable
  #    scope (mechanism 3). Poisoned weights / a rewritten audit log / a mutated
  #    registry are persistence-across-reset bypasses, so writability here is a
  #    build-time failure, not a review-time note.
  protectedPaths = [
    "/etc/agent-os/broker"          # broker binary + config
    "/etc/agent-os/registry"        # this registry, materialized
    "/var/lib/agent-os/audit"       # append-only audit log
    "/var/lib/agent-os/taint"       # taint-tracker state
    "/var/lib/agent-os/models"      # model weights
    "/var/lib/agent-os/mem/trusted" # trusted-origin mem namespaces
  ];

  # ── INV-2 — egress deny-list. Any network-capable (T2) impl must deny at least
  #    these; a specific host is reachable only via an operator-config allowlist
  #    entry (which is itself T3 to change). Loopback is on the list on purpose:
  #    a confirmed fetch to 127.0.0.1:11434 could drive the in-guest model / pull
  #    weights, so loopback egress is effectively T3.
  egressDenyList = [
    "127.0.0.0/8" "10.0.0.0/8" "172.16.0.0/12" "192.168.0.0/16"
    "169.254.0.0/16" "::1/128" "fc00::/7" "fe80::/10"
  ];

  # Allowed arg types (the parser in Step 4 validates values against these; here we
  # only assert the registry never declares an unknown type).
  argTypes = [ "string" "path" "namespace" "url" "recipient" "bytes" "enum" ];

  # sandbox defaults — everything closed unless a capability opts in.
  defaultSandbox = {
    readWritePaths = [ ];
    readOnlyPaths  = [ ];
    network        = false;
    egressDeny     = [ ];   # only meaningful when network = true
    egressAllow    = [ ];   # operator-config exceptions (config-only to change)
  };

  mkCap = { tier, summary, args ? { }, impl, sandbox ? { } }: {
    inherit tier summary args impl;
    sandbox = defaultSandbox // sandbox;
  };

  # ── The v1 registry (matches docs/phase2-threat-model.md §5) ─────────────────
  registry = {
    # T0 — read-only, side-effect-free, reversible, local. Runs even under taint.
    "mem.recall" = mkCap {
      tier = "T0"; impl = "cap-mem-recall";
      summary = "Read memory within an allowlisted namespace.";
      args = { namespace = "namespace"; query = "string"; };
      sandbox = { readOnlyPaths = [ "/var/lib/agent-os/mem" ]; };
    };
    "capabilities.list" = mkCap {
      tier = "T0"; impl = "cap-capabilities-list";
      summary = "Enumerate the offered capabilities and their tiers.";
    };
    "file.read" = mkCap {
      tier = "T0"; impl = "cap-file-read";
      summary = "Read a file under a declared-safe path.";
      args = { path = "path"; };
      sandbox = { readOnlyPaths = [ "/var/lib/agent-os/safe-read" ]; };
    };

    # T1 — reversible local side effects. Confirmed-in-v1 (T1-auto deferred).
    "mem.remember" = mkCap {
      tier = "T1"; impl = "cap-mem-remember";
      summary = "Write a memory entry (origin-stamped; taint rides the storage).";
      args = { namespace = "namespace"; content = "string"; };
      # writes ONLY the session namespace — never the trusted-origin namespace.
      sandbox = { readWritePaths = [ "/var/lib/agent-os/mem/session" ]; };
    };
    "file.write" = mkCap {
      tier = "T1"; impl = "cap-file-write";
      summary = "Write a file within the sandboxed workspace.";
      args = { path = "path"; content = "bytes"; };
      sandbox = { readWritePaths = [ "/var/lib/agent-os/workspace" ]; };
    };

    # T2 — irreversible / outward-facing. ALWAYS human-confirm, provenance-independent.
    "net.fetch" = mkCap {
      tier = "T2"; impl = "cap-net-fetch";
      summary = "Fetch a URL (egress; obeys INV-2 deny-list).";
      args = { url = "url"; method = "enum"; };
      sandbox = { network = true; egressDeny = egressDenyList; };
    };
    "message.send" = mkCap {
      tier = "T2"; impl = "cap-message-send";
      summary = "Send a message to a human or peer (outward-facing).";
      args = { recipient = "recipient"; body = "string"; };
      sandbox = { network = true; egressDeny = egressDenyList; };
    };
  };

  # ── Invariant checks. Each is {cond, msg}; `ok` forces them all and throws the
  #    first violated message. A violation therefore fails evaluation → fails the
  #    flake check. This is the executable form of the threat model.
  caps = mapAttrsToList (name: c: { inherit name; inherit (c) tier impl sandbox args; }) registry;

  # writable path w conflicts with protected path p if either contains the other.
  pathConflicts = w: p: (w == p) || (hasPrefix (w + "/") p) || (hasPrefix (p + "/") w);
  capTouchesProtected = c: lib.any (w: lib.any (p: pathConflicts w p) protectedPaths) c.sandbox.readWritePaths;

  denyCovers = c: all (cidr: lib.elem cidr c.sandbox.egressDeny) egressDenyList;

  checks =
    # (1) every capability declares a runtime tier — T3 is non-expressible.
    (map (c: {
      cond = lib.elem c.tier runtimeTiers;
      msg  = "capability-registry: '${c.name}' declares tier '${c.tier}' — only ${concatStringsSep "/" runtimeTiers} are expressible (T3 is config-only, non-expressible).";
    }) caps)
    ++
    # (2) no capability name is a forbidden T3 operation.
    (map (c: {
      cond = !(lib.elem c.name forbiddenT3);
      msg  = "capability-registry: '${c.name}' is a forbidden T3 operation and must never be a capability.";
    }) caps)
    ++
    # (3) mechanism 3 — no impl's writable scope includes a protected path.
    (map (c: {
      cond = !(capTouchesProtected c);
      msg  = "capability-registry: '${c.name}' has a writable path overlapping a protected path (broker/registry/audit/taint/weights/trusted-mem). Poisoned-state persistence — build denied.";
    }) caps)
    ++
    # (4) mechanism 3 — only T2 impls may have network. A T0/T1 with network is an
    #     exfil channel bypassing the T2 always-confirm floor.
    (map (c: {
      cond = (!c.sandbox.network) || (c.tier == "T2");
      msg  = "capability-registry: '${c.name}' (tier ${c.tier}) has network in its sandbox — only T2 impls may have ANY network (else it bypasses the off-box T2 floor).";
    }) caps)
    ++
    # (5) INV-2 — every network-capable impl carries the full egress deny-list.
    (map (c: {
      cond = (!c.sandbox.network) || (denyCovers c);
      msg  = "capability-registry: '${c.name}' has network but its egress deny-list does not cover the full INV-2 set (loopback/RFC1918/link-local/ULA).";
    }) caps)
    ++
    # (6) impls are non-empty and unique.
    (map (c: { cond = c.impl != ""; msg = "capability-registry: '${c.name}' has an empty impl."; }) caps)
    ++
    [ { cond = length (lib.unique (map (c: c.impl) caps)) == length caps;
        msg = "capability-registry: duplicate impl name — every capability maps to a distinct impl."; } ]
    ++
    # (7) schema — every declared arg type is known.
    (lib.concatMap (c:
      mapAttrsToList (an: at: {
        cond = lib.elem at argTypes;
        msg  = "capability-registry: '${c.name}' arg '${an}' has unknown type '${at}' (allowed: ${concatStringsSep "," argTypes}).";
      }) c.args
    ) caps);

  ok = all (c: lib.asserts.assertMsg c.cond c.msg) checks;

in {
  inherit registry runtimeTiers forbiddenT3 protectedPaths egressDenyList argTypes;
  # `ok` is true only if every invariant holds; forcing it (the flake check does)
  # throws the first violation's message. Downstream (Step 7) reads `registry`
  # only after this module has evaluated, i.e. only after `ok` is proven.
  ok = assert ok; true;
  capabilityNames = map (c: c.name) caps;
}
