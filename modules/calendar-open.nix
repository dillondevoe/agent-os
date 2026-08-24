# Agent OS — OPEN variant Phase 2 substrate: the nuclear-accurate calendar
# (Dillon's flagged highest-value Phase 2 item; roadmap Phase 2, owner Augur).
#
# WHY (roadmap 2026-07-31, Phase 2 "ambient substrate"): "nuclear-accurate calendar
# the agent reads AND writes — grounds the brain in the real now, past training cutoff.
# A real cal app/store the agent-brain can query + mutate, not a text file."
#
# WHAT THIS INCREMENT SHIPS:
#   (headless substrate — the agent's half)
#   * a REAL iCalendar store: khal on a local vdir at /var/lib/agos-calendar/agent
#     (per-event *.ics files with VTIMEZONE — a real store, not a flat text file).
#   * `agos-cal`, a stable JSON-emitting CLI the agent drives: now / agenda / add / cals.
#     This is the exact surface the future agent-brain `calendar.*` hand wraps (grammar
#     proposed to Rabbot — agent-brain tool-grammar is a cross-brain escalation, so the
#     hand-wiring lands there, not here).
#   (visual half — Dillon go 9047, rabbot-to-augur-calendar-GUI-bump-2026-07-31; unblocked
#    now that desktop-open.nix makes Hyprland reproducible in-tree)
#   * `gnome-calendar`, a GTK window in systemPackages → auto-listed by `wofi --show drun`
#     ($mod+R) and tiled by Hyprland, exactly like thunderbird. "show me the calendar" opens
#     a real window, not just agos-cal's text list.
#   * `services.radicale`, a loopback (127.0.0.1:5232) CalDAV server whose agent/agent
#     collection is SYMLINKED onto THIS SAME vdir — so the GUI (over CalDAV) and the agent
#     (over khal) read/write ONE physical set of *.ics files. No bridge, no vdirsyncer, no
#     divergence: a GUI-created event IS an agos-cal event and vice-versa. Sandbox-clean
#     because filesystem_folder = calRoot puts both the symlink and its target inside
#     radicale's ProtectSystem=strict ReadWritePaths; radicale runs as the agent user so
#     *.ics ownership matches what agos-cal/khal write.
# DEFERRED — runtime CalDAV validation, a Dell (real-HW) acceptance gate, NOT a compile
# concern (this headless bandwidth-capped node cannot run Hyprland/gnome-calendar/CalDAV):
#   (1) round-trip smoke: GUI add → `agos-cal agenda` shows it, and `agos-cal add` → GUI
#       shows it (proves the one-store symlink + rights + discovery actually resolve live);
#   (2) auto-provisioning gnome-calendar's CalDAV account (evolution-data-server source)
#       vs the one-time manual "add account http://localhost:5232" — runtime profile config,
#       same mutable-profile pattern as thunderbird's accounts.
#
# ISOLATION: OPEN-only, self-contained, shares ZERO modules with the sovereign path —
# imported solely from configuration-open.nix. Nothing here can perturb the sealed
# surface. SEAL-TIME FOLLOW-UP: at the sovereign repackage, the calendar capability
# folds into a shared substrate module (same pattern as the planned hardware-base.nix).
{ config, pkgs, lib, ... }:

let
  calRoot = "/var/lib/agos-calendar";
  calName = "agent";
  calPath = "${calRoot}/${calName}";

  # ── visual half: loopback CalDAV over the SAME vdir ─────────────────────────────────
  radicalePort = 5232;
  # radicale 3 lays collections out at <filesystem_folder>/collection-root/<url-path>.
  # We point filesystem_folder at calRoot and symlink the agent/agent collection onto the
  # existing vdir; BOTH the link and its target live under calRoot, so radicale's
  # ProtectSystem=strict ReadWritePaths (= filesystem_folder) permit the writes and radicale
  # + khal share one physical store. Client URL: http://127.0.0.1:5232/agent/agent/
  radicaleCollLink = "${calRoot}/collection-root/${calName}/${calName}";
  # marks the shared vdir as a VCALENDAR collection so radicale exposes the existing events
  # on discovery. Copied in only if absent (tmpfiles `C`) — never clobbers radicale's props.
  radicaleProps = pkgs.writeText "agos-radicale-props.json" ''{"tag": "VCALENDAR"}'';

  # khalConf and the agos-cal hand now live in ./pkgs/agos-cal.nix. A `let` binding inside a
  # NixOS module cannot be named from anywhere else, so while the hand lived here no derivation
  # could put `agos-cal` on a PATH and calendar-battery.py was structurally unwireable.
  # calName and calPath are passed IN rather than duplicated — the module body needs both.
  agosCal = import ./pkgs/agos-cal.nix { inherit pkgs calName calPath; };

in {
  # The store dir must exist + be agent-writable before first use. setgid (2) so new
  # *.ics inherit group `users` even when root (Rabbot over SSH) creates them.
  systemd.tmpfiles.rules = [
    "d ${calRoot} 0755 ${calName} users -"
    "d ${calPath} 2775 ${calName} users -"
    # visual half: pre-create radicale's collection-root and symlink its agent/agent
    # collection onto the shared vdir, then seed the VCALENDAR marker (only if absent).
    "d ${calRoot}/collection-root 0755 ${calName} users -"
    "d ${calRoot}/collection-root/${calName} 0755 ${calName} users -"
    "L+ ${radicaleCollLink} - - - - ${calPath}"
    "C ${calPath}/.Radicale.props 0664 ${calName} users - ${radicaleProps}"
  ];

  # Loopback CalDAV over the shared vdir. Runs as the agent user so *.ics ownership matches
  # agos-cal/khal; no auth + anonymous full-rights because 127.0.0.1 single-tenant is the
  # only principal. filesystem_folder = calRoot keeps the symlinked collection inside the
  # sandbox's ReadWritePaths (see radicaleCollLink above).
  services.radicale = {
    enable = true;
    user = calName;
    group = "users";
    settings = {
      server.hosts = [ "127.0.0.1:${toString radicalePort}" ];
      auth.type = "none";
      storage.filesystem_folder = calRoot;
    };
    rights = {
      allow-all = { user = ".*"; collection = ".*"; permissions = "RrWw"; };
    };
  };

  # khal + agos-cal = the agent's half; gnome-calendar = the human window (auto-appears in
  # `wofi --show drun`, tiled by Hyprland — same delivery as thunderbird in email-open.nix).
  environment.systemPackages = [ pkgs.khal agosCal pkgs.gnome-calendar ];
}
