# agos-media — the agent's hand, extracted from modules/media-open.nix so something other than a
# NixOS module can name it.
#
# WHY THIS FILE EXISTS: a `let` binding inside a NixOS module is unreachable from
# outside that module. Nothing in the repo could put this CLI on a PATH, so
# tests/agos-media-battery.py could only ever run against a machine that had already
# booted the whole desktop image — which is why it sat on KNOWN_UNWIRED_DEBT.
# Extracted, it is an ordinary derivation a cheap runCommand check can depend on.
#
# The GUI stays in the module. The hand and the GUI are separate things and only
# one of them is a contract.

{ pkgs }:

# `ffmpeg-headless` (hyphen) must be quoted string-access — a bareword inside
# `with pkgs; [ … ]` would parse as the subtraction `ffmpeg - headless`. writeShellApplication
# resolves its `bin` output for PATH via lib.getBin, so ffprobe lands correctly.
ffmpegHeadless = pkgs."ffmpeg-headless";
pkgs.writeShellApplication {
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
}
