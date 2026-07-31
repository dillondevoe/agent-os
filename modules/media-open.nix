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
  # `ffmpeg-headless` (hyphen) must be quoted string-access — a bareword inside
  # `with pkgs; [ … ]` would parse as the subtraction `ffmpeg - headless`. writeShellApplication
  # resolves its `bin` output for PATH via lib.getBin, so ffprobe lands correctly.
  ffmpegHeadless = pkgs."ffmpeg-headless";

  agos-media = pkgs.writeShellApplication {
    name = "agos-media";
    runtimeInputs = [ ffmpegHeadless pkgs.coreutils pkgs.jq ];
    text = ''
      usage() {
        cat >&2 <<'USAGE'
      agos-media — the agent's read-only media hand (JSON out). Images/video/audio via ffprobe.

        agos-media info <path>   probe a media file:
          {ok,path,bytes,media_type,format_name,duration_s,width,height,streams}
          media_type ∈ image|video|audio|other; streams:[{type,codec,width,height}]
      USAGE
        exit 2
      }

      cmd_info() {
        path="''${1:-}"
        if [ -z "$path" ]; then echo "agos-media info: need a path" >&2; exit 2; fi
        if [ ! -f "$path" ]; then jq -n --arg p "$path" '{ok:false,error:"no such file",path:$p}'; return 0; fi
        if ! probe=$(ffprobe -v quiet -print_format json -show_format -show_streams "$path" 2>/dev/null) \
             || [ -z "$probe" ]; then
          jq -n --arg p "$path" '{ok:false,error:"not a readable media file",path:$p}'; return 0
        fi
        bytes=$(stat -c '%s' "$path")
        # Reshape ffprobe's schema into the stable contract. An image is a single-frame "video"
        # stream, so a *_pipe / image2 container name classifies as image before the video test.
        printf '%s' "$probe" | jq --arg p "$path" --argjson bytes "$bytes" '
          (.streams // []) as $st
          | ($st | map(select(.codec_type=="video")) | .[0]) as $v
          | ($st | any(.codec_type=="video")) as $hasv
          | ($st | any(.codec_type=="audio")) as $hasa
          | (.format.format_name // "") as $fmt
          | (if ($fmt|test("_pipe$")) or ($fmt=="image2") then "image"
             elif $hasv then "video"
             elif $hasa then "audio"
             else "other" end) as $mt
          | { ok:true, path:$p, bytes:$bytes, media_type:$mt,
              format_name:(.format.format_name // null),
              duration_s:((.format.duration // null) | if .==null then null else (tonumber? // null) end),
              width:($v.width // null), height:($v.height // null),
              streams:($st | map({ type:.codec_type, codec:.codec_name,
                                   width:(.width // null), height:(.height // null) })) }'
      }

      case "''${1:-}" in
        info) shift; cmd_info "$@" ;;
        *)    usage ;;
      esac
    '';
  };
in {
  environment.systemPackages = [
    agos-media      # the agent's read-only media hand (JSON: info)
    pkgs.imv        # human-facing Wayland image viewer (appears in wofi drun)
    pkgs.mpv        # human-facing video/audio player (appears in wofi drun)
  ];
}
