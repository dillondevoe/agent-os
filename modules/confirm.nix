# Phase 2 · Step 6 — the confirm channel client's sandbox + install (modules/confirm.nix).
#
# bin/confirm is the wall's MOUTH: the broker-owned seam that renders a REQUIRE-CONFIRM request
# to a human over a tty-free, integrity-checked channel and returns a nonce-bound approve/deny.
# This module (a) installs it as `confirm`, (b) provisions the 0700-root seen-destinations store,
# and (c) carries the build-fails-on-violation sandbox assertions (§7) that keep the confirm
# client from ever becoming a general exfil channel.
#
# The wrapper itself + all its pinned operator-config live in modules/confirm-pkg.nix (the single
# source of truth), so the broker (modules/broker.nix) pins AGENT_OS_CONFIRM_SEAM to the EXACT
# same store-path binary in its OWN wrapper. The SEAM + AGENT_OS_CONFIRM_TIMEOUT_S are therefore
# pinned by the broker wrapper (authoritative regardless of launch context) — NOT via this
# module's environment.variables, which the broker would merely inherit (Fable Step-6 ruling; see
# the confirm-pkg.nix header).
#
# Security surface (the confirm channel + injection wall): branch -> PR -> Fable(code) -> merge.
# Never direct-push, never self-merge.
{ config, pkgs, lib, ... }:

let
  # Reuse the registry's protected-path sets as the single source of truth (reading these lists
  # does NOT force the registry's `assert ok` — Nix is lazy — we only want the data).
  reg = import ./capability-registry.nix { inherit lib; };

  # The confirm wrapper + its pinned operator-config, defined ONCE in confirm-pkg.nix so the
  # broker pins AGENT_OS_CONFIRM_SEAM to the EXACT same store-path binary. The assertions below
  # gate the SAME facts the wrapper embeds — a violating pin does not evaluate, so the build fails.
  cpkg = import ./confirm-pkg.nix { inherit pkgs; };

  confirmWritable = [ cpkg.confirmDir ];    # the client's ENTIRE writable scope — nothing else

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
    [ { cond = confirmWritable == [ cpkg.confirmDir ];
        msg  = "confirm: the client's writable scope must be EXACTLY [\"${cpkg.confirmDir}\"] and nothing else."; } ]
    ++ lib.concatMap (w: map (p: {
         cond = !(pathConflicts w p);
         msg  = "confirm: writable path '${w}' overlaps protected path '${p}' — the confirm client must not write broker/registry/audit/taint/weights/trusted-mem.";
       }) reg.protectedPaths) confirmWritable
    ++ [
      { cond = builtins.length cpkg.relayEndpoints == 1;
        msg  = "confirm: egress allowance must be EXACTLY one pinned endpoint (addr+port, NO fallback list) — Fable ruling Q1; more than one and it can become a general exfil channel."; }
      { cond = lib.all (e: builtins.isString e.addr && e.addr != ""
                          && builtins.isInt e.port && e.port > 0 && e.port < 65536) cpkg.relayEndpoints;
        msg  = "confirm: the pinned relay endpoint must be a non-empty addr + a 1..65535 int port."; }
      { cond = cpkg.gettyTty != cpkg.modelTty;
        msg  = "confirm: the second-getty console '${cpkg.gettyTty}' must NEVER be the model's login tty '${cpkg.modelTty}' (INV-1: the model's tty carries zero authorization)."; }
      { cond = lib.any (rp: pathConflicts rp cpkg.relaySecretFile) reg.protectedReadPaths;
        msg  = "confirm: the broker<->relay secret '${cpkg.relaySecretFile}' must live under a protected-READ path so the model can never open it (it is T3 config, §7)."; }
      { cond = cpkg.humanWindow + cpkg.roundTrip <= cpkg.brokerTimeout;
        msg  = "confirm: human window (${toString cpkg.humanWindow}s) + one relay round-trip (${toString cpkg.roundTrip}s) must be <= the broker backstop (${toString cpkg.brokerTimeout}s) so the client's graceful deny precedes the SIGKILL backstop (§5 ordering constraint)."; }
    ];
  confirmSandboxOk = lib.all (c: lib.asserts.assertMsg c.cond c.msg) checks;
in
{
  # Gate the installed package behind the sandbox assertions: a violating pin does not evaluate,
  # so the VM/toplevel realize (§8) fails. Same guarantee as the registry's `assert ok`.
  environment.systemPackages = lib.mkIf confirmSandboxOk [ cpkg.wrapper ];

  # The seen-destinations store (§4.4): 0700 root, a protected path no capability impl may write
  # (mirrors audit/taint). The model cannot pre-seed it to suppress the first-time banner.
  systemd.tmpfiles.rules = [
    "d /var/lib/agent-os 0755 root root - -"
    "d ${cpkg.confirmDir} 0700 root root - -"
  ];

  # NOTE: the live second-getty agetty unit on ${cpkg.gettyTty} is operator/Step-7 deployment
  # detail — spawning a login on a second console is environment-specific and must not destabilize
  # VM boot. The load-bearing invariant (that console is NEVER ${cpkg.modelTty}) is the build
  # assertion above; the getty backend opens ${cpkg.gettyTty} directly when a console is attached,
  # else fails closed.
}
