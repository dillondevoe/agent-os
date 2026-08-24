# modules/media-open.nix — Phase 2: media viewer/player (OPEN variant).
#
# The roadmap's "media" ambient app. Completes the read-hand trio over a human's files:
# files-open (#7) = directory/metadata, docs-open (#10) = PDF content, this = image/AV probe.
# Now the agent can inspect ANY media a human drops in — dimensions, codec, duration — and the
# human has a viewer for it.
#
# WHAT THIS SHIPS:
#   * `agos-media`, a stable JSON-emitting, READ-ONLY CLI. It shells `ffprobe` (which already
#     emits JSON) and RESHAPES it into a compact, stable contract so the agent never has to
#     parse ffprobe's sprawling schema:
#       info <path> -> {ok,path,bytes,media_type,format_name,duration_s,width,height,streams}
#     media_type is derived (image|video|audio|other); an image reads as a 1-frame "video"
#     stream in ffprobe, so a "*_pipe"/image2 container name is classified image first.
#     Read-only by construction — ffprobe only inspects; it never mutates a file. This is the
#     surface a future agent-brain `media.*` hand wraps 1:1 (wiring is Rabbot's lane).
#   * imv — a keyboard-driven Wayland image viewer (ships a .desktop).
#   * mpv — the canonical keyboard-driven video/audio player (ships a .desktop). Together
#     they are the human half; both appear in `wofi --show drun` and Hyprland tiles them.
#
# The hand is backed by `ffmpeg-headless` (ships ffprobe without X11/SDL — a lighter closure
# than the full ffmpeg mpv pulls). Missing / non-media input returns {ok:false,error:…}.
#
# ISOLATION: OPEN-only, self-contained, imported solely from configuration-open.nix. Shares
# nothing with the sealed path — fold into a shared substrate module at seal-time.
{ pkgs, ... }:
let
  agos-media = import ./pkgs/agos-media.nix { inherit pkgs; };
in {
  environment.systemPackages = [
    agos-media      # the agent's read-only media hand (JSON: info)
    pkgs.imv        # human-facing Wayland image viewer (appears in wofi drun)
    pkgs.mpv        # human-facing video/audio player (appears in wofi drun)
  ];
}
