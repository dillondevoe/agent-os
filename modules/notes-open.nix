# modules/notes-open.nix — Phase 2: notes (OPEN variant).
#
# The roadmap's "notes" ambient app. Mirrors calendar-open exactly: a REAL local store the
# agent reads AND writes, plus a JSON-emitting CLI hand + a human GUI. Fully local and
# credential-free (unlike email) — so the GUI+hand symmetry is back.
#
# WHAT THIS SHIPS:
#   * a plain-markdown note store at /var/lib/agos-notes (one <slug>.md per note — a real
#     store the agent-brain and the human's editor both see, not an opaque DB).
#   * `agos-notes`, a stable JSON-emitting CLI: list / new / read / append. This is the
#     surface the future agent-brain `notes.*` hand wraps 1:1 (grammar-wiring is Rabbot's
#     lane; the hand itself is this credential-free local CLI, same as agos-cal).
#   * Apostrophe — a distraction-free markdown editor for the human. It ships a .desktop, so
#     `wofi --show drun` ($mod+R, from desktop-open.nix) finds it and Hyprland tiles it. The
#     human opens notes from /var/lib/agos-notes; agent + human share one plain-md store.
#
# Slugs are sanitized to [a-z0-9-] so note filenames are always tame (no spaces/tabs/newlines
# in a filename we generate) — which is why list/read use simple field separators safely,
# unlike files-open (which must survive arbitrary externally-named files).
#
# ISOLATION: OPEN-only, self-contained, imported solely from configuration-open.nix. Shares
# nothing with the sealed path — fold into a shared substrate module at seal-time.
{ pkgs, ... }:
let
  notesDir = "/var/lib/agos-notes";
  agos-notes = import ./pkgs/agos-notes.nix { inherit pkgs notesDir; };
in {
  # Store dir must exist + be agent-writable before first use. setgid (2) so new *.md inherit
  # group `users` even when root (Rabbot over SSH) creates them — same as calendar's vdir.
  systemd.tmpfiles.rules = [
    "d ${notesDir} 2775 agent users -"
  ];

  environment.systemPackages = with pkgs; [
    agos-notes       # the agent's notes hand (JSON: list/new/read/append)
    apostrophe       # human-facing markdown notes editor (appears in wofi drun automatically)
  ];
}
