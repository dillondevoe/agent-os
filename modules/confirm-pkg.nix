# Phase 2 · Step 6 — the shared `confirm` wrapper package (single source of truth).
#
# bin/confirm is the wall's MOUTH (the broker-owned confirm seam). TWO callers need the
# IDENTICAL production wrapper + the SAME pinned operator-config:
#   - modules/confirm.nix installs it system-wide as `confirm`, provisions its 0700-root seen-
#     store, and carries the build-fails-on-violation sandbox assertions (§7) over these facts.
#   - modules/broker.nix (Step 5) must pin AGENT_OS_CONFIRM_SEAM to the EXACT store-path of this
#     wrapper (and AGENT_OS_CONFIRM_TIMEOUT_S to brokerTimeout) in the broker's OWN wrapper's
#     `export`s — alongside its TAINT_BIN / AUDIT_BIN / AGENT_OS_REGISTRY pins.
#
# Why the seam is pinned in the broker wrapper and NOT via environment.variables (Fable Step-6
# ruling): environment.variables is INHERITED from the broker's launch context. If anything the
# model can influence sets AGENT_OS_CONFIRM_SEAM in that context, it redirects the seam to a
# malicious auto-approver -> universal fail-OPEN. A wrapper `export` runs immediately before
# `exec` and is AUTHORITATIVE regardless of launch context, removing that dependency entirely —
# exactly the pinning discipline audit/taint/registry already use.
#
# Defining the wrapper + facts once here removes drift: every pinned value changes in exactly one
# place and both callers inherit it. Importing this file twice with the same `pkgs` yields the
# same derivation (Nix deduplicates), so there is no collision — same lesson/shape as
# modules/taint-pkg.nix / modules/audit-pkg.nix.
{ pkgs }:

let
  # ── Pinned facts (operator-config; the model never sets any of these) ────────
  confirmDir      = "/var/lib/agent-os/confirm";
  gettyTty        = "tty2";                 # the second console the getty backend renders to
  modelTty        = "tty1";                 # agent-shell autologin tty (INV-1) — MUST differ
  channels        = "telegram,getty";       # deterministic order; first reachable+authn wins
  brokerTimeout   = 120;                    # AGENT_OS_CONFIRM_TIMEOUT_S — the broker's SIGKILL backstop
  humanWindow     = 90;                     # the client's OWN graceful window; MUST be < backstop − rtt
  roundTrip       = 15;                     # headroom for one relay round-trip (§5 ordering constraint)

  # Single hard-pinned relay endpoint — addr + port, NO fallback list (Fable ruling Q1, §3.1/§7).
  relayAddr       = "100.71.238.38";        # mini's Tailscale addr (operator-config placeholder)
  relayPort       = 8443;

  # T3 secrets, model-invisible by construction (asserted under a protected-READ path in
  # confirm.nix). Both default empty/unprovisioned -> telegram stays fail-closed-unreachable and
  # confirm falls through to getty (or, if neither is live, denies). Fail-closed is correct.
  relaySecretFile = "/etc/agent-os/credentials/confirm-relay-secret";
  dillonUserId    = "";                     # operator-set; empty => telegram fail-closed-unreachable

  wrapper = pkgs.writeShellScriptBin "confirm" ''
    export AGENT_OS_CONFIRM_DIR=${confirmDir}
    export AGENT_OS_CONFIRM_CHANNELS=${channels}
    export AGENT_OS_CONFIRM_HUMAN_WINDOW_S=${toString humanWindow}
    export AGENT_OS_CONFIRM_RELAY_ADDR=${relayAddr}
    export AGENT_OS_CONFIRM_RELAY_PORT=${toString relayPort}
    export AGENT_OS_CONFIRM_RELAY_SECRET_FILE=${relaySecretFile}
    export AGENT_OS_CONFIRM_DILLON_USER_ID=${dillonUserId}
    export AGENT_OS_CONFIRM_GETTY_TTY=/dev/${gettyTty}
    exec ${pkgs.python3}/bin/python3 ${../bin/confirm} "$@"
  '';

  # The egress allowance the confirm.nix sandbox asserts is EXACTLY one pinned endpoint.
  relayEndpoints = [ { addr = relayAddr; port = relayPort; } ];

  # ── Intrinsic seam invariants (spec §7) ──────────────────────────────────────
  # Defined HERE, alongside the wrapper, so every consumer that PINS this store-path — confirm.nix
  # (install), broker.nix + taint-pkg.nix (their AGENT_OS_CONFIRM_SEAM `export`s) — forces the SAME
  # facts the wrapper embeds. The guarantee travels WITH the pin and survives confirm.nix ever being
  # dropped from the system imports (Fable Step-6 follow-up: refactor-proof the by-construction
  # store-path identity across all three consumers). Install-context checks (writable-scope vs
  # protected paths, secret under a protected-READ path) stay in confirm.nix — they only exist when
  # THAT module installs the client + provisions its 0700-root seen-store.
  checks = [
    { cond = builtins.length relayEndpoints == 1;
      msg  = "confirm: egress allowance must be EXACTLY one pinned endpoint (addr+port, NO fallback list) — Fable ruling Q1; more than one and it can become a general exfil channel."; }
    { cond = pkgs.lib.all (e: builtins.isString e.addr && e.addr != ""
                            && builtins.isInt e.port && e.port > 0 && e.port < 65536) relayEndpoints;
      msg  = "confirm: the pinned relay endpoint must be a non-empty addr + a 1..65535 int port."; }
    { cond = gettyTty != modelTty;
      msg  = "confirm: the second-getty console '${gettyTty}' must NEVER be the model's login tty '${modelTty}' (INV-1: the model's tty carries zero authorization)."; }
    { cond = humanWindow + roundTrip <= brokerTimeout;
      msg  = "confirm: human window (${toString humanWindow}s) + one relay round-trip (${toString roundTrip}s) must be <= the broker backstop (${toString brokerTimeout}s) so the client's graceful deny precedes the SIGKILL backstop (§5 ordering constraint)."; }
  ];
  # Forces every check; throws the first violated message. ANY consumer that references checksOk
  # therefore fails the build on a violating pin — same guarantee as the registry's `assert ok`.
  checksOk = pkgs.lib.all (c: pkgs.lib.asserts.assertMsg c.cond c.msg) checks;
in
{
  inherit wrapper
    confirmDir gettyTty modelTty channels brokerTimeout humanWindow roundTrip
    relayAddr relayPort relaySecretFile dillonUserId
    relayEndpoints checksOk;
}
