# modules/seal-check.nix — make the clean-room egress seal FAIL LOUD instead of fail
# silent. Companion to clean-room.nix; closes the HIGH from Fable's PR#22 review.
#
# The hole this closes: clean-room.nix builds the default-DROP egress wall as
# `nftables.service` + the `inet agentos_egress` table, but that seal is NOT a hard
# boot dependency. The build-time `nft -c` check does NOT catch a *runtime* failure —
# the nf_tables module gone after a kernel bump, a conntrack load fail, an ordering
# race. If nftables.service fails at runtime, boot proceeds to multi-user,
# NetworkManager brings the link up, and a `sealed = true` box runs with UNRESTRICTED
# egress — the one invariant it exists for, silently gone. Pre-branding the operator
# got ~1-2s of red `[FAILED] nftables` console scroll; quiet-boot (boot-branding.nix)
# deletes even that. This module restores a LOUD signal and, on the sealed box, a
# targeted fix.
#
# What it does, every boot, after nftables has run and BEFORE the agent login:
#   - verifies the wall is actually up: nftables.service not `failed` AND the
#     `inet agentos_egress` table is present.
#   - seal UP  -> clears the marker, exits 0. (Healthy boot never touches the network.)
#   - seal DOWN -> writes /run/agentos-unsealed (a red banner + repair instructions),
#     shouts a red line to /dev/console, and exits 1 so `systemctl --failed` shows it.
#
# The RESPONSE is variant-aware (the matrix Rabbot + I agreed, endorsed
# rabbot-to-augur-agentos-seal-failloud-recoverable-endorse-2026-07-30):
#   - SEALED (`agentos-sealed`, agentos.cleanRoom.sealed = true): egress is the whole
#     threat, so drop it DIRECTLY — every non-lo link down + NetworkManager runtime-
#     masked — and DO NOT hand tty1 to the agent; drop to a bare repair shell behind
#     the banner. The agent brain never runs unsealed.
#   - NON-SEALED (`agentos`, dev/desktop, operator present): BANNER ONLY. The agent
#     still starts, the network is left alone. Brick-avoidance > hard-close on the box
#     you're sitting at.
#
# Why RECOVERABLE fail-closed, not `systemctl isolate emergency.target` (Fable's literal
# ask): this box has NO usable password anywhere — the `agent` user has no password set
# and there is no root hash (configuration.nix), so emergency/sulogin is either an
# UNRECOVERABLE lockout or, if sulogin honours a no-hash root, an unintended
# passwordless root shell. Both are worse than the leak. Dropping the links stops the
# egress threat directly; the runtime NetworkManager mask lives in /run and evaporates
# on reboot; the operator keeps the tty1 repair shell. Leak stopped, brick avoided,
# recovery = fix nftables + reboot (or unmask NM by hand to rebuild online).
#
# Why tty1 and not "block the login": tty1 autologin is the ONLY working local shell —
# `agent` cannot password-login on any other tty, and tty2 is already the confirm
# channel's getty backend (confirm-pkg.nix). Masking tty1 would be its own brick. So we
# keep the autologin but withhold the AGENT from it (loginShellInit, below).
#
# Fail-closed on AMBIGUITY (Rabbot's pre-answer for Fable): on the sealed variant the
# marker is written FIRST and cleared only on the proven-up path, so a mid-script crash
# leaves the safe (agent-blocked) state — the guard has no fail-silent hole of its own.
# On the non-sealed variant ambiguity fails OPEN (agent runs) — brick-avoidance again.
#
# SECURITY SURFACE (egress seal + login response): routed branch -> PR -> Fable, never
# direct-push, never self-merge. Alternatives considered are laid out for Fable at the
# bottom of this comment.
#
# ---- fail-closed mechanism: three options, this module ships (2) --------------------
#  (1) systemctl isolate emergency.target — Fable's literal ask. REJECTED: no usable
#      password -> lockout or unintended root shell (see above). Also a blunt "something
#      is wrong" hammer that does not even guarantee the network is down.
#  (2) kill-network + block-autologin + loud banner  <== SHIPPED for sealed. Targeted at
#      the actual (egress) threat, recoverable, keeps local repairability.
#  (3) banner-only (agent keeps running) <== SHIPPED for non-sealed. Weakest, but right
#      for a dev box with the operator present.
# -------------------------------------------------------------------------------------
{ config, pkgs, lib, ... }:
let
  cfg    = config.agentos.cleanRoom;
  sealed = cfg.sealed;

  nft  = "${pkgs.nftables}/bin/nft";
  ip   = "${pkgs.iproute2}/bin/ip";
  sctl = "${config.systemd.package}/bin/systemctl";

  # The check. Templated per-variant at eval time (the module knows cfg.sealed), so the
  # sealed box gets the pre-write fail-closed default + the network kill, and the
  # non-sealed box gets neither.
  sealCheck = pkgs.writeShellScript "agentos-seal-check" ''
    set -u
    MARK=/run/agentos-unsealed

    write_marker() {
      cat > "$MARK" <<'MSG'
    ================================================================
      AGENT OS — EGRESS SEAL DOWN (clean-room wall not loaded)
      nftables 'inet agentos_egress' is missing, or nftables.service
      failed. This box may have UNRESTRICTED network egress.
      Repair:  systemctl status nftables
               systemctl restart nftables   (then reboot)
      Sealed box: the network was dropped to stop any leak. To go
      back online for a rebuild:
               systemctl unmask --runtime --now NetworkManager
    ================================================================
    MSG
    }

    console_banner() {
      # a loud red line into the live boot scroll, for an operator watching the console
      printf '\033[1;41;97m %s \033[0m\n' \
        'AGENT OS: EGRESS SEAL DOWN — UNSEALED. See tty1 to repair.' \
        > /dev/console 2>/dev/null || true
    }

    wall_up() {
      # DOWN if nftables.service is failed, OR the egress table is absent. Any doubt
      # (nft missing, table missing, service failed) returns non-zero => treated as down.
      ${sctl} is-failed --quiet nftables.service && return 1
      ${nft} list table inet agentos_egress >/dev/null 2>&1 || return 1
      return 0
    }
    ${lib.optionalString sealed ''
    enforce_offline() {
      # SEALED fail-closed: the ONLY threat an unsealed box poses is egress, so kill it
      # directly. Runtime-mask NetworkManager (mask lives in /run -> gone on reboot ->
      # RECOVERABLE) and drop every non-loopback link. The operator keeps the tty1
      # repair shell; nothing here is a brick.
      ${sctl} mask --runtime --now NetworkManager.service 2>/dev/null || true
      for d in /sys/class/net/*; do
        ifc="$(basename "$d")"
        [ "$ifc" = "lo" ] && continue
        ${ip} link set dev "$ifc" down 2>/dev/null || true
      done
    }

    # fail-CLOSED on ambiguity (sealed only): assume unsealed until the wall is PROVEN
    # up, so any crash below leaves the safe (agent-blocked) state. Cleared only on the
    # proven-up path.
    write_marker
    ''}
    if wall_up; then
      rm -f "$MARK"
      exit 0
    fi

    # --- seal is DOWN ---
    write_marker
    console_banner
    ${lib.optionalString sealed "enforce_offline"}
    exit 1
  '';

  # The tty1 response. lib.mkBefore guarantees this block is concatenated into
  # loginShellInit BEFORE agent-shell.nix's `exec agent-shell` block, so on the sealed
  # variant we can neutralize that block (set AGENT_OS_ACTIVE) and fall through to an
  # interactive repair bash instead of the agent.
  loginGuardSealed = ''
    # seal-failloud (sealed): if the egress seal was found DOWN at boot, do NOT hand
    # tty1 to the agent. Show the red banner and fall through to a bare repair shell.
    if [ "$(tty)" = "/dev/tty1" ] && [ -z "$AGENT_OS_ACTIVE" ] && [ -e /run/agentos-unsealed ]; then
      printf '\033[1;41;97m%s\033[0m\n' ' !! AGENT OS EGRESS SEAL DOWN — UNSEALED !! '
      cat /run/agentos-unsealed 2>/dev/null
      printf '\033[1;33m%s\033[0m\n' 'Agent brain BLOCKED, network dropped. Bare repair shell — fix nftables, reboot.'
      # neutralize agent-shell.nix's exec block below; fall through to interactive bash.
      export AGENT_OS_ACTIVE=1
    fi
  '';
  loginGuardUnsealed = ''
    # seal-failloud (non-sealed dev box): BANNER ONLY. Warn, then let the agent start.
    if [ "$(tty)" = "/dev/tty1" ] && [ -z "$AGENT_OS_ACTIVE" ] && [ -e /run/agentos-unsealed ]; then
      printf '\033[1;41;97m%s\033[0m\n' ' !! AGENT OS EGRESS SEAL DOWN — UNSEALED (agent still starting) !! '
      cat /run/agentos-unsealed 2>/dev/null
      # NOTE: do NOT set AGENT_OS_ACTIVE — the agent still takes over (banner-only on
      # the non-sealed variant). Egress is NOT killed here.
    fi
  '';
in
lib.mkIf cfg.enable {
  systemd.services.agentos-seal-check = {
    description = "Agent OS: verify the clean-room egress seal is up; fail LOUD if it is down";
    wantedBy = [ "multi-user.target" ];
    # after nftables has attempted to load the wall...
    after  = [ "nftables.service" ];
    # ...and BEFORE the agent login sees the marker. On the sealed variant, also before
    # NetworkManager/network-online so the link-drop lands before anything comes online.
    before = [ "getty@tty1.service" "systemd-user-sessions.service" ]
             ++ lib.optionals sealed [ "NetworkManager.service" "network-online.target" ];
    serviceConfig = {
      Type = "oneshot";
      # RemainAfterExit so a DOWN result stays visible as a failed unit post-boot; a
      # `wantedBy` (Wants=, not Requires=) failed oneshot does NOT block the target, so
      # exit 1 signals loudly without bricking the boot.
      RemainAfterExit = true;
      ExecStart = "${sealCheck}";
    };
  };

  # Withhold tty1 from the agent on a down seal (sealed) / warn (non-sealed). mkBefore =
  # runs before agent-shell.nix's exec block; see loginGuard* above.
  environment.loginShellInit =
    lib.mkBefore (if sealed then loginGuardSealed else loginGuardUnsealed);
}
