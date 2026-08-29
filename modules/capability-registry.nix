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

  # ── Protected READ paths — no capability impl may hold ANY of these in its
  #    readable scope (read-only OR writable). Reading broker config / a credentials
  #    store is functionally `credentials.read` (a T3 forbidden op), so a cap like
  #    `config.inspect` with readOnlyPaths=["/etc/agent-os/broker"] must fail the
  #    build. NOTE: /var/lib/agent-os/mem/trusted is deliberately NOT here —
  #    recall-of-trusted by `mem.recall` is by design; re-tainting is the broker's job.
  protectedReadPaths = [
    "/etc/agent-os/broker"        # broker binary + config (holds cloud creds per §13)
    "/etc/agent-os/credentials"   # any separate secrets store
  ];

  # ── INV-2 — egress deny-list. Any network-capable (T2) impl must deny at least
  #    these; a specific host is reachable only via an operator-config allowlist
  #    entry (which is itself T3 to change). Loopback is on the list on purpose:
  #    a confirmed fetch to 127.0.0.1:11434 could drive the in-guest model / pull
  #    weights, so loopback egress is effectively T3.
  egressDenyList = [
    "127.0.0.0/8" "10.0.0.0/8" "172.16.0.0/12" "192.168.0.0/16"
    "100.64.0.0/10"   # CGNAT / shared-address space (RFC 6598) — real LAN/router hops
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

  mkCap = { tier, summary, args ? { }, argEnums ? { }, impl, sandbox ? { } }: {
    inherit tier summary args impl argEnums;
    sandbox = defaultSandbox // sandbox;
  };

  # ── The v1 registry (matches docs/phase2-threat-model.md §5) ─────────────────
  # Bound as `rawRegistry`: it is UNvalidated here. No consumer may read it directly —
  # the output `registry` gates it behind `assert ok` so the invariants throw first.
  rawRegistry = {
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
    # T1-auto-on-trusted is defined in docs/phase2-threat-model.md §5a. Note for whoever
    # implements it: the checkpoint scope is DERIVED from the T1 readWritePaths below --
    # "the workspace root" alone drops mem.remember's namespace.
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
      # GAP-1: the member set for the `method` enum. Least-privilege for a *fetch* —
      # read-only-on-remote verbs only (GET body, HEAD metadata probe). State-changing
      # verbs (POST/PUT/DELETE/PATCH) are out of scope for v1 net.fetch.
      argEnums = { method = [ "GET" "HEAD" ]; };
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
  caps = mapAttrsToList (name: c: { inherit name; inherit (c) tier impl sandbox args argEnums; }) rawRegistry;

  # Canonical absolute path: leading "/", and no empty / "." / ".." segment (which
  # rules out "//", "/./", "/../", trailing "/", relative paths, and "/" itself).
  # Non-canonical paths bypass the textual overlap check but systemd canonicalizes
  # them at unit-load, so a declared "/mem/session/../trusted" WOULD grant trusted
  # writes — reject at eval instead. (Symlink aliasing can't be caught here; that is
  # a Step-7 obligation, discharged by modules/cap-sandbox.nix: an EMPTY root
  # (TemporaryFileSystem=/:ro) with only the declared roots bound back, plus protected
  # paths as InaccessiblePaths. This comment used to say "ProtectSystem + ..."; that was
  # wrong and pointed the next reader at the one option that CANCELS the boundary — it
  # remounts the host /etc and /usr read-only over the empty root, and read-only is not
  # unreadable. Measured on systemd 255 and 261; see cap-sandbox.nix's header.)
  pathIsCanonical = p:
    let parts = lib.splitString "/" p;
    in (builtins.head parts == "")            # leading slash present
       && (length parts >= 2)
       && (all (seg: seg != "" && seg != "." && seg != "..") (builtins.tail parts));

  # w conflicts with protected path p if either contains the other. "/" (root) is
  # special-cased to conflict with everything (belt-and-suspenders; canonical check
  # already rejects a bare "/").
  pathConflicts = w: p:
    (w == "/") || (p == "/")
    || (w == p) || (hasPrefix (w + "/") p) || (hasPrefix (p + "/") w);
  capTouchesProtected = c: lib.any (w: lib.any (p: pathConflicts w p) protectedPaths) c.sandbox.readWritePaths;

  # A cap must not READ a protected-read path via readOnly OR writable scope.
  capReadsProtected = c:
    lib.any (rp: lib.any (p: pathConflicts rp p) protectedReadPaths)
      (c.sandbox.readOnlyPaths ++ c.sandbox.readWritePaths);

  allDeclaredPaths = c: c.sandbox.readWritePaths ++ c.sandbox.readOnlyPaths;

  # Merged sandbox must not carry keys outside the schema (a typo like `newtork=true`
  # would silently default network=false — safe here, but unvalidated for Step 7).
  sandboxKeysKnown = c: all (k: lib.elem k (lib.attrNames defaultSandbox)) (lib.attrNames c.sandbox);

  # Sandbox value TYPES: without this, a `network = "true"` string is truthy and would
  # silently pass the T0/T1-no-network guard (check 4); asserting types fails-loud instead.
  isStrList = v: lib.isList v && all lib.isString v;
  sandboxTypesOk = c:
    let s = c.sandbox; in
    lib.isBool s.network
    && isStrList s.readWritePaths && isStrList s.readOnlyPaths
    && isStrList s.egressDeny && isStrList s.egressAllow;

  denyCovers = c: all (cidr: lib.elem cidr c.sandbox.egressDeny) egressDenyList;

  checks =
    # (0) sandbox value TYPES first — a wrong-typed field (e.g. network = "true") must
    #     fail LOUD here, before a later guard silently coerces it. `all` forces this
    #     list left-to-right, so putting it first makes the clean message win.
    (map (c: {
      cond = sandboxTypesOk c;
      msg  = "capability-registry: '${c.name}' sandbox has a wrong-typed field (network must be a bool; readWritePaths/readOnlyPaths/egressDeny/egressAllow must be lists of strings).";
    }) caps)
    ++
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
    # (3b) mechanism 3 — no impl may READ a protected-read path (broker config /
    #      credentials store); reading those is credentials.read, a T3 forbidden op.
    (map (c: {
      cond = !(capReadsProtected c);
      msg  = "capability-registry: '${c.name}' has a readable path overlapping a protected-read path (broker config / credentials) — that is credentials.read (T3). Build denied.";
    }) caps)
    ++
    # (3c) every declared path (writable OR read-only) must be canonical-absolute, so
    #      the textual overlap checks above cannot be bypassed by ../ // or trailing /.
    (lib.concatMap (c:
      map (p: {
        cond = pathIsCanonical p;
        msg  = "capability-registry: '${c.name}' declares non-canonical path '${p}' — must be absolute with no empty, '.' or '..' segment and no trailing '/'.";
      }) (allDeclaredPaths c)
    ) caps)
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
    # (5b) egressAllow is operator-config-only — the registry must never ship a host
    #      allowlist entry (that would be a config change smuggled into declarations).
    (map (c: {
      cond = c.sandbox.egressAllow == [ ];
      msg  = "capability-registry: '${c.name}' declares a non-empty egressAllow — allowlist entries live in operator config, never the registry.";
    }) caps)
    ++
    # (5c) reject unknown sandbox keys (a typo would silently mis-default a control).
    (map (c: {
      cond = sandboxKeysKnown c;
      msg  = "capability-registry: '${c.name}' sandbox has an unknown key (allowed: ${concatStringsSep "," (lib.attrNames defaultSandbox)}). A typo would silently default a security control.";
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
    ) caps)
    ++
    # (7b) GAP-1 — every `enum`-typed arg MUST carry a non-empty string member set in
    #      argEnums. An enum with no members fail-closes in the broker (unusable), so a
    #      forgotten member set is a BUILD error here, not a silent runtime deny.
    (lib.concatMap (c:
      mapAttrsToList (an: at: {
        cond = at != "enum"
               || ((c.argEnums ? ${an})
                   && lib.isList c.argEnums.${an}
                   && c.argEnums.${an} != [ ]
                   && (all lib.isString c.argEnums.${an}));
        msg  = "capability-registry: '${c.name}' arg '${an}' is type 'enum' but argEnums.${an} is missing/empty/non-string — declare argEnums.${an} = [ \"...\" ] (an enum with no member set fail-closes and is unusable).";
      }) c.args
    ) caps)
    ++
    # (7c) GAP-1 — no argEnums entry for an arg that is not typed `enum` (dead, never-consulted
    #      config; reject it so declarations stay honest).
    (lib.concatMap (c:
      mapAttrsToList (an: _members: {
        cond = (c.args ? ${an}) && c.args.${an} == "enum";
        msg  = "capability-registry: '${c.name}' declares argEnums.${an} but arg '${an}' is not type 'enum' — a member set on a non-enum arg is dead config.";
      }) c.argEnums
    ) caps);

  ok = all (c: lib.asserts.assertMsg c.cond c.msg) checks;

in {
  inherit runtimeTiers forbiddenT3 protectedPaths protectedReadPaths egressDenyList argTypes;
  # Nix is LAZY: reading `registry` does not force `ok`. So the DATA is gated behind
  # `assert ok` — no consumer (incl. Step 7's sandbox derivation) can obtain a
  # capability without every mechanism-3 invariant throwing first. Same for the name
  # list. `ok` remains available standalone (its own assert forces the checks).
  registry = assert ok; rawRegistry;
  capabilityNames = assert ok; map (c: c.name) caps;
  ok = assert ok; true;
}
