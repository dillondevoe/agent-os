# Phase 2 · Step 6 — the confirm channel client + its sandbox (modules/confirm.nix).
#
# bin/confirm is the wall's MOUTH: the broker-owned seam that renders a REQUIRE-CONFIRM request
# to a human over a tty-free, integrity-checked channel and returns a nonce-bound approve/deny.
# This module (a) installs it as `confirm`, (b) pins the SEAM + the broker's subprocess backstop
# so the broker calls THIS client with a hard timeout, (c) provisions the 0700-root seen-
# destinations store, and (d) carries the build-fails-on-violation sandbox assertions (§7) that
# keep the confirm client from ever becoming a general exfil channel.
#
# Security surface (the confirm channel + injection wall): branch -> PR -> Fable(code) -> merge.
# Never direct-push, never self-merge.
#
# Pinning discipline (same lesson as audit/taint/broker): the confirm wrapper pins EVERY value
# the client trusts (state dir, channel order, human window, the single relay endpoint, the T3
# secret FILE path, the pinned Dillon user-id, the getty tty) as store-path/literal env, so no
# stray env in the client's environment can redirect it. The overrides survive only as TEST
# affordances via a DIRECT `python3 bin/confirm` call — exactly how tests/confirm-battery.sh
# drives it.
{ config, pkgs, lib, ... }:

let
  # Reuse the registry's protected-path sets as the single source of truth (reading these lists
  # does NOT force the registry's `assert ok` — Nix is lazy — we only want the data).
  reg = import ./capability-registry.nix { inherit lib; };

  # ── Pinned facts (operator-config; the model never sets any of these) ────────
  confirmDir      = "/var/lib/agent-os/confirm";
  confirmWritable = [ confirmDir ];        # the client's ENTIRE writable scope — nothing else
  gettyTty        = "tty2";                 # the second console the getty backend renders to
  modelTty        = "tty1";                 # agent-shell autologin tty (INV-1) — MUST differ
  channels        = "telegram,getty";       # deterministic order; first reachable+authn wins
  brokerTimeout   = 120;                    # AGENT_OS_CONFIRM_TIMEOUT_S — the broker's SIGKILL backstop
  humanWindow     = 90;                     # the client's OWN graceful window; MUST be < backstop − rtt
  roundTrip       = 15;                     # headroom for one relay round-trip (§5 ordering constraint)

  # Single hard-pinned relay endpoint — addr + port, NO fallback list (Fable ruling Q1, §3.1/§7).
  relayAddr       = "100.71.238.38";        # mini's Tailscale addr (operator-config placeholder)
  relayPort       = 8443;
  relayEndpoints  = [ { addr = relayAddr; port = relayPort; } ];

  # T3 secrets, model-invisible by construction. The broker<->relay HMAC key is read from a FILE
  # under a protected-READ path (asserted below) so it never rides argv/env dumps; the pinned
  # Dillon Telegram user-id gates callback authenticity. Both default empty/unprovisioned: until
  # the operator provisions them the telegram channel stays fail-closed-unreachable and confirm
  # falls through to getty (or, if neither is live, denies). Fail-closed is the correct default.
  relaySecretFile = "/etc/agent-os/credentials/confirm-relay-secret";
  dillonUserId    = "";                     # operator-set; empty => telegram fail-closed-unreachable

  # Textual path-overlap — the SAME rule as the registry's pathConflicts, reused here so the
  # confirm client's writable scope is checked against protected paths identically.
  pathConflicts = w: p:
    (w == "/") || (p == "/") || (w == p)
    || (lib.hasPrefix (w + "/") p) || (lib.hasPrefix (p + "/") w);

  # ── Build-fails-on-violation sandbox assertions (spec §7; extends mechanism-3) ──
  # Each is {cond, msg}; `confirmSandboxOk` forces them all and throws the first violated
  # message. A violating pin therefore does not evaluate -> `nix build .#vm` fails. That build
  # failure IS the sandbox test, exactly like the registry's `assert ok`.
  checks =
    [ { cond = confirmWritable == [ confirmDir ];
        msg  = "confirm: the client's writable scope must be EXACTLY [\"${confirmDir}\"] and nothing else."; } ]
    ++ lib.concatMap (w: map (p: {
         cond = !(pathConflicts w p);
         msg  = "confirm: writable path '${w}' overlaps protected path '${p}' — the confirm client must not write broker/registry/audit/taint/weights/trusted-mem.";
       }) reg.protectedPaths) confirmWritable
    ++ [
      { cond = builtins.length relayEndpoints == 1;
        msg  = "confirm: egress allowance must be EXACTLY one pinned endpoint (addr+port, NO fallback list) — Fable ruling Q1; more than one and it can become a general exfil channel."; }
      { cond = lib.all (e: builtins.isString e.addr && e.addr != ""
                          && builtins.isInt e.port && e.port > 0 && e.port < 65536) relayEndpoints;
        msg  = "confirm: the pinned relay endpoint must be a non-empty addr + a 1..65535 int port."; }
      { cond = gettyTty != modelTty;
        msg  = "confirm: the second-getty console '${gettyTty}' must NEVER be the model's login tty '${modelTty}' (INV-1: the model's tty carries zero authorization)."; }
      { cond = lib.any (rp: pathConflicts rp relaySecretFile) reg.protectedReadPaths;
        msg  = "confirm: the broker<->relay secret '${relaySecretFile}' must live under a protected-READ path so the model can never open it (it is T3 config, §7)."; }
      { cond = humanWindow + roundTrip <= brokerTimeout;
        msg  = "confirm: human window (${toString humanWindow}s) + one relay round-trip (${toString roundTrip}s) must be <= the broker backstop (${toString brokerTimeout}s) so the client's graceful deny precedes the SIGKILL backstop (§5 ordering constraint)."; }
    ];
  confirmSandboxOk = lib.all (c: lib.asserts.assertMsg c.cond c.msg) checks;

  confirm = pkgs.writeShellScriptBin "confirm" ''
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
in
{
  # Gate the installed package behind the sandbox assertions: a violating pin does not evaluate,
  # so the VM/toplevel realize (§8) fails. Same guarantee as the registry's `assert ok`.
  environment.systemPackages = lib.mkIf confirmSandboxOk [ confirm ];

  # Pin the SEAM + the broker's subprocess backstop into the system environment the broker
  # inherits (the broker is spawned from the agent-shell login, which sources this).
  # NOTE (flagged to Fable): broker.nix pins its OWN trusted paths in its wrapper; the confirm
  # SEAM is contributed here via environment.variables so as NOT to edit the separately-merged
  # broker wrapper beyond the one mandated timeout edit in bin/broker. If you'd rather pin
  # AGENT_OS_CONFIRM_SEAM directly into the broker wrapper (modules/broker.nix), that is a
  # one-line follow-up — calling it out rather than silently touching a merged surface.
  environment.variables = {
    AGENT_OS_CONFIRM_SEAM = "${confirm}/bin/confirm";
    AGENT_OS_CONFIRM_TIMEOUT_S = toString brokerTimeout;
  };

  # The seen-destinations store (§4.4): 0700 root, a protected path no capability impl may write
  # (mirrors audit/taint). The model cannot pre-seed it to suppress the first-time banner.
  systemd.tmpfiles.rules = [
    "d /var/lib/agent-os 0755 root root - -"
    "d ${confirmDir} 0700 root root - -"
  ];

  # NOTE: the live second-getty agetty unit on ${gettyTty} is operator/Step-7 deployment detail —
  # spawning a login on a second console is environment-specific and must not destabilize VM boot.
  # The load-bearing invariant (that console is NEVER ${modelTty}) is the build assertion above;
  # the getty backend opens ${gettyTty} directly when a console is attached, else fails closed.
}
