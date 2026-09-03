# Phase 2 · Step 7 / Phase S · WP-S2 — per-capability systemd fs-confinement, DERIVED from the
# Step-1 registry `sandbox` declaration. This is GATE #5(a).
#
# Security surface (this file IS a security boundary — for `file.read` it is the ONLY symlink
# boundary that exists): branch -> PR -> Fable(code) -> merge. Never direct-push.
#
# ── Why this file exists ────────────────────────────────────────────────────────────────────
# `cap-invoke` direct-execs a capability impl. Two shipped impls take a CALLER-SUPPLIED path:
#
#   * `file.read`  validates the path STRING only. Its own comment delegates symlink-escape to
#     "that layer" — this layer. Without it, a symlink planted in SAFE_ROOT passes the textual
#     check and `open()` follows it: arbitrary-file read. FAIL-OPEN.
#   * `file.write` refuses to overwrite a non-regular TARGET, but the check is target-only: a
#     symlinked PARENT component still redirects the write, and lstat->open is a TOCTOU window.
#
# Neither is closable in the impl (a userspace path check can never win a TOCTOU race against
# the filesystem). Both are closed HERE, by the kernel, via a mount namespace.
#
# ── Why `TemporaryFileSystem=/:ro` and not `ProtectSystem=strict` alone ─────────────────────
# `ProtectSystem=strict` makes the filesystem READ-ONLY. It does not make it UNREADABLE — under
# it alone, `SAFE_ROOT/evil -> /etc/shadow` still reads /etc/shadow. `ReadOnlyPaths=` likewise
# only removes write permission; it grants nothing and denies no reads elsewhere. The only shape
# that makes "everything else" genuinely absent is to start from an EMPTY root
# (`TemporaryFileSystem=/:ro`) and bind back exactly the declared roots plus the runtime paths
# the interpreter needs. A symlink out of the sandbox then resolves to a path that does not
# exist in the namespace -> ENOENT, for reads and writes alike, with no race to lose.
# ── MEASURED, and it contradicts the brief: `ProtectSystem=strict` is DELIBERATELY ABSENT ──
# The WP-S2 routing asked for `ProtectSystem=strict` alongside the empty root. It is not here,
# because on real systemd (255.4, the DVo test host) adding it does not harden the namespace —
# it CANCELS it. Bisected, both orderings, with and without the other hardening options:
#
#   TemporaryFileSystem=/:ro                          -> /etc/passwd absent   (boundary holds)
#   TemporaryFileSystem=/:ro + ProtectSystem=strict   -> /etc/passwd READABLE (boundary GONE)
#   ProtectSystem=strict + TemporaryFileSystem=/:ro   -> /etc/passwd READABLE (order irrelevant)
#   TemporaryFileSystem=/:ro + all other base props   -> /etc/passwd absent   (boundary holds)
#
# ProtectSystem=strict works by remounting the HOST's /usr, /boot, /efi and /etc read-only, which
# reintroduces the host tree the empty root had removed. Read-only is not unreadable, and for
# `file.read` the threat is a READ. So the option that reads like extra defence is, in this exact
# combination, the one that reopens the hole — which is why the battery's negative control and its
# escape leg are load-bearing rather than ceremonial: this composition failure is invisible to any
# check that only asserts "the hardening property is present". Reported, not papered over.
#
# CORROBORATED on a second systemd: tests/cap-sandbox-confinement.nix boots a NixOS VM running
# systemd 261 and every leg of the battery holds there too (first green run 2026-08-15). One
# version agreeing with itself is a coincidence; 255 and 261 agreeing is the property.
#
# ── Derivation, not duplication ────────────────────────────────────────────────────────────
# Every path below comes from `capability-registry.nix`: `sandbox.readOnlyPaths` become
# BindReadOnlyPaths, `sandbox.readWritePaths` become BindPaths, and the registry's own
# `protectedPaths`/`protectedReadPaths` become InaccessiblePaths. Nothing is hand-listed, so a
# registry edit moves the confinement with it and the registry's `assert ok` gates this file too
# (reading `.registry` forces every mechanism-3 invariant).
{ lib
, # The paths bound back read-only into EVERY cap namespace, because the impl's interpreter
  # lives there. On NixOS this is exactly the store: impls are patchShebangs'd to an absolute
  # store-path python3, so the store is the whole runtime. Overridable so a non-NixOS host
  # (the WP-S2 battery runs on a WSL2 Ubuntu box) can bind its distro libdirs instead.
  runtimePaths ? [ "/nix/store" ]
}:

let
  regModule = import ./capability-registry.nix { inherit lib; };
  reg = regModule.registry;   # forces `assert ok` — no confinement is derived from an invalid registry
  inherit (regModule) protectedPaths protectedReadPaths;

  # Same containment relation the registry uses for its protected-path invariant: two paths
  # conflict if either contains the other.
  pathConflicts = a: b:
    (a == "/") || (b == "/")
    || (a == b) || (lib.hasPrefix (a + "/") b) || (lib.hasPrefix (b + "/") a);

  # ── runtimePaths is an UNVALIDATED SECURITY INPUT. Validate it. ──────────────────────────
  # Everything in `runtimePaths` is emitted as `BindReadOnlyPaths=` into EVERY cap's namespace,
  # so this argument can hand back exactly what `TemporaryFileSystem=/:ro` took away. Two ways,
  # both of which look reasonable at the call site:
  #
  #   runtimePaths = [ "/" ]      -> binds the whole host read-only over the empty root. This is
  #                                  the ProtectSystem=strict failure IDENTICALLY, arrived at
  #                                  through a legitimate-looking parameter instead of a
  #                                  hardening option. Read-only is not unreadable; file.read's
  #                                  threat is a READ, and this makes /etc/shadow readable again.
  #   runtimePaths = [ "/etc" ]   -> narrower, same class: it re-binds a tree that CONTAINS
  #                                  /etc/agent-os/broker, a protected-READ path. The bind is
  #                                  applied to every cap, so it would grant credentials.read
  #                                  (a T3 forbidden op) to caps the registry proved cannot have it.
  #
  # Neither is hypothetical: the header above explicitly invites a non-NixOS host to override this
  # with its distro libdirs, so a human WILL edit this argument, and the failure is silent — the
  # derived properties all look correct and `checks.cap-sandbox` (which evaluates the DEFAULT) is
  # blind to it. So the guard belongs HERE, at eval, where every caller passes through it.
  #
  # Rejected: non-canonical paths (same shape the registry's check 3c enforces, and for the same
  # reason — systemd canonicalizes at unit-load, so a textual guard that skips it is bypassable),
  # and any path conflicting with a protected or protected-read path. The legitimate overrides all
  # pass: /nix/store, /usr, /lib, /lib64, /usr/lib — none of them contain /etc/agent-os or
  # /var/lib/agent-os.
  pathIsCanonical = p:
    let parts = lib.splitString "/" p;
    in (builtins.head parts == "")
       && (lib.length parts >= 2)
       && (lib.all (seg: seg != "" && seg != "." && seg != "..") (builtins.tail parts));

  runtimeBad = lib.filter (p: !(pathIsCanonical p)) runtimePaths;
  runtimeCollides = lib.filter
    (p: lib.any (q: pathConflicts p q) (protectedPaths ++ protectedReadPaths))
    runtimePaths;

  runtimeOk =
    assert lib.assertMsg (runtimeBad == [ ]) ''
      cap-sandbox: runtimePaths contains a non-canonical path [${lib.concatStringsSep " " runtimeBad}].
      Each must be absolute with no empty, '.' or '..' segment and no trailing '/' — and never "/",
      which would bind the entire host back over TemporaryFileSystem=/:ro and undo this boundary.
    '';
    assert lib.assertMsg (runtimeCollides == [ ]) ''
      cap-sandbox: runtimePaths [${lib.concatStringsSep " " runtimeCollides}] overlap a protected or
      protected-read path. These are bound read-only into EVERY capability namespace, so the overlap
      would hand every cap a readable broker config / credentials store / audit log — the exact reads
      the registry's build-time invariants deny. Bind a narrower runtime path.
    '';
    true;

  # ── Base hardening applied to EVERY capability, regardless of declaration ──────────────
  # The fs half is the load-bearing part; the rest is the standard systemd hardening set, kept
  # uniform so a new cap cannot be born less confined than its siblings.
  baseProps = [
    "TemporaryFileSystem=/:ro"   # empty root — see header. THE symlink boundary.
    # NO ProtectSystem= here. See the header: on systemd 255 it re-exposes the host /etc and /usr
    # over the empty root. Do not "restore" it without re-running tests/cap-sandbox-battery.sh.
    "ProtectHome=yes"
    "PrivateTmp=yes"
    "PrivateDevices=yes"
    "ProtectProc=invisible"
    "ProcSubset=pid"
    "ProtectKernelTunables=yes"
    "ProtectKernelModules=yes"
    "ProtectKernelLogs=yes"
    "ProtectControlGroups=yes"
    "ProtectClock=yes"
    "ProtectHostname=yes"
    "NoNewPrivileges=yes"
    "RestrictSUIDSGID=yes"
    "RestrictRealtime=yes"
    "RestrictNamespaces=yes"
    "LockPersonality=yes"
    "SystemCallArchitectures=native"
    "UMask=0077"
  ];

  # ── Network confinement, derived ───────────────────────────────────────────────────────
  # network=false (every cap shipped today) gets a hard PrivateNetwork — an impl that is not
  # declared network-capable gets no stack at all, so an exfil attempt cannot even resolve.
  # network=true is NOT shipped yet (cap-invoke-pkg's build gate refuses it); the deny-list
  # translation is written here so the T2 slice starts from the registry, not from scratch.
  #
  # DENY-ONLY, and the omission of `IPAddressAllow=any` is the whole point. This rendering
  # previously LED with it, which made the entire deny list dead by construction. From
  # systemd.resource-control(5), verbatim: "Access is granted when the checked IP address
  # matches an entry in IPAddressAllow=. Otherwise, access is denied when it matches
  # IPAddressDeny=. Otherwise, access is granted." `any` matches every address at rule 1, so
  # rule 2 — the registry's egressDenyList, the executable form of INV-2 — never ran. Measured,
  # not reasoned: battery leg 8c reached 127.0.0.1 under a policy that named 127.0.0.0/8, in the
  # same VM where leg 8m refused that exact probe under IPAddressDeny=any (PR #263, CI job
  # 100655923315). Nothing was dropped and no kernel support was missing; the rule order did it.
  #
  # With no Allow entry, rule 1 never fires, denied CIDRs stop at rule 2, and everything else is
  # granted at rule 3 — which is the "public internet only" shape the T2 slice actually wants.
  # An operator exception (`sandbox.egressAllow`, config-only to change) renders as a SPECIFIC
  # Allow CIDR: it wins at rule 1 for that range alone, and can never be `any`.
  # An operator exception that spelled itself `any` (or the CIDRs `any` expands to) would
  # reinstate the dead-deny-list bug exactly, from config instead of from this file. The comment
  # above is not load-bearing on its own: this refuses to evaluate. Registry invariant (5b)
  # already forbids a non-empty egressAllow in-tree, so this covers the operator-config path
  # that invariant deliberately leaves open.
  blanketAllow = [ "any" "0.0.0.0/0" "::/0" ];
  checkAllow = c:
    let bad = lib.filter (cidr: lib.elem cidr blanketAllow) c.sandbox.egressAllow; in
    if bad == [ ] then c.sandbox.egressAllow
    else throw ("cap-sandbox: capability egressAllow contains a blanket entry ${toString bad}. "
                + "A blanket IPAddressAllow matches every address at rule 1 of "
                + "systemd.resource-control(5), which makes the egressDeny list dead by "
                + "construction — the defect measured in PR #263. Name specific CIDRs.");

  netProps = c:
    if c.sandbox.network
    then map (cidr: "IPAddressDeny=${cidr}") c.sandbox.egressDeny
         ++ map (cidr: "IPAddressAllow=${cidr}") (checkAllow c)
    else [ "PrivateNetwork=yes" "IPAddressDeny=any" "RestrictAddressFamilies=AF_UNIX" ];

  # ── Filesystem confinement, derived ────────────────────────────────────────────────────
  declared = c: c.sandbox.readOnlyPaths ++ c.sandbox.readWritePaths;

  # A protected path is made explicitly inaccessible UNLESS the cap's own declaration overlaps
  # it. The overlap case is not a hole: the registry's build-time invariants already prove no
  # cap holds a protected path in WRITABLE scope and none holds a protected-READ path at all.
  # The one legitimate overlap is `mem.recall`'s readOnlyPaths=/var/lib/agent-os/mem containing
  # the write-protected /var/lib/agent-os/mem/trusted — recall-of-trusted is by design (the
  # registry says so explicitly), and it stays READ-only here because the bind is
  # BindReadOnlyPaths. Blanket-InaccessiblePaths would break that by-design read.
  inaccessible = c:
    map (p: "InaccessiblePaths=-${p}")
      (lib.filter (p: !(lib.any (d: pathConflicts d p) (declared c)))
        (lib.unique (protectedPaths ++ protectedReadPaths)));

  # `runtimeOk` is FORCED here, on the one code path that consumes runtimePaths — an assertion
  # bound in a `let` and never referenced is not an assertion, it is a comment that costs eval time.
  fsProps = c:
    lib.optionals runtimeOk
      (map (p: "BindReadOnlyPaths=${p}") (runtimePaths ++ c.sandbox.readOnlyPaths))
    ++ map (p: "BindPaths=${p}") c.sandbox.readWritePaths
    ++ inaccessible c;

  propsFor = c: baseProps ++ fsProps c ++ netProps c;

  # cap name -> ordered list of `systemd-run --property=` arguments.
  policy = lib.mapAttrs (_name: c: propsFor c) reg;

in {
  inherit policy propsFor baseProps;

  # The materialized policy, as the dispatcher consumes it. cap-invoke looks a capability up by
  # name and refuses to run an impl whose name is ABSENT (fail-closed): a cap that reaches the
  # seam with no derived confinement is exactly the state GATE #5 forbids.
  policyJson = builtins.toJSON policy;
}
