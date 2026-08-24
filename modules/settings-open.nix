# modules/settings-open.nix — Phase 2: the settings surface (OPEN variant).
#
# The roadmap's "settings" app, built to its acceptance bar ("agent can read+drive it"):
# a small agent-facing CLI hand — `agos-sys` — that reports live system state as JSON and
# drives the two settings a headless agent actually changes (audio volume, display
# brightness), plus a compact set of human-facing GUI tools (audio mixer, display
# arrangement, NetworkManager editor) for the console/Waybar path.
#
# This mirrors the calendar substrate's shape (modules/calendar-open.nix → `agos-cal`):
# a thin, JSON-contract shell hand the agent-brain can wrap 1:1, over the box's real
# backends (NetworkManager for net, PipeWire/wireplumber for audio, brightnessctl for
# backlight). OPEN-only, self-contained, imported solely from configuration-open.nix —
# it shares nothing with the sealed sovereign path. Fold into a shared substrate module
# at seal-time (same follow-up as calendar + desktop).
#
# Runtime deps that live in OTHER open increments (build-independent, noted): the audio
# reads/sets need PipeWire running (modules/desktop-open.nix); brightness needs a real
# backlight (bare-metal Dell). Absent either, `agos-sys status` degrades gracefully to
# null fields — it never errors — so the CLI builds and runs anywhere.
{ pkgs, ... }:
let
  agos-sys = import ./pkgs/agos-sys.nix { inherit pkgs; };
in {
  environment.systemPackages = with pkgs; [
    agos-sys              # the agent's system-settings hand (JSON status + volume/brightness)
    # Human-facing settings GUIs (console / Waybar path) — the "surface" a person clicks:
    pavucontrol           # audio mixer / device routing
    wdisplays             # Wayland display arrangement (wlroots/Hyprland)
    networkmanagerapplet  # nm-applet + nm-connection-editor (NM is the backend)
  ];
}
