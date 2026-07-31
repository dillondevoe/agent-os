# modules/files-open.nix — Phase 2: the file manager (OPEN variant).
#
# The roadmap's "files" ambient app, built to its acceptance bar ("launches, the agent
# can read+drive it, tiles cleanly, no red config errors"): the Thunar GUI (xfce.thunar)
# for the human at the Waybar/Hyprland surface, plus a thin agent-facing CLI hand —
# `agos-files` — that returns a directory listing / a path's metadata as JSON, so the
# agent-brain can wrap it 1:1 over the box's real filesystem.
#
# READ-ONLY BY DESIGN: agos-files only ever inspects (list/stat) — it never creates,
# moves, or deletes. The agent already has a full shell for mutation; this hand is the
# STRUCTURED, contract-stable read surface (predictable JSON the brain parses), not a
# second destructive path baked into the image. Mutation stays in the GUI (human) or bash.
#
# Same shape as calendar-open (agos-cal), settings-open (agos-sys), calculator-open
# (agos-calc): a JSON-contract shell hand + a human GUI, OPEN-only, self-contained,
# imported solely from configuration-open.nix. It shares nothing with the sealed path —
# fold into a shared substrate module at seal-time (same follow-up as the others).
#
# The GUI needs no hyprland.conf edit: xfce.thunar ships a .desktop file, so
# `wofi --show drun` ($mod+R, from desktop-open.nix) finds it and Hyprland tiles it like
# any other window — the "tiles cleanly" half of the acceptance bar.
{ pkgs, ... }:
let
  # The agent's files hand. writeShellApplication runs shellcheck at build time (real
  # acceptance) and pins `find`/`jq` into runtimeInputs — no runtime PATH dependence.
  # Records are NUL-separated and fields 0x1f-separated (generated at runtime, handed to
  # jq via --arg) so a filename containing spaces, tabs, or newlines can never corrupt
  # the JSON — files are the domain, so odd names are expected, not an edge case.
  agos-files = pkgs.writeShellApplication {
    name = "agos-files";
    runtimeInputs = with pkgs; [ coreutils findutils jq ];
    text = ''
      usage() {
        cat >&2 <<'USAGE'
      agos-files — the agent's read-only files hand (JSON out).

        agos-files list <dir>    entries in <dir> (non-recursive):
                                 {ok,dir,count,entries:[{name,type,size,mtime_epoch}]}
        agos-files stat <path>   one path's metadata:
                                 {ok,exists,path,type,size,mtime_epoch}

      Read-only: it never creates, moves, or deletes. type ∈ file|dir|link (raw
      find type letter for anything else). size in bytes; mtime_epoch is integer UTC.
      USAGE
        exit 2
      }

      # find %y gives a one-letter type; map the common three to stable words, pass the
      # rest through verbatim so nothing is silently mislabelled.
      TYPEMAP='{f:"file",d:"dir",l:"link"}'

      cmd_list() {
        dir="''${1:-}"
        if [ -z "$dir" ]; then echo "agos-files list: need a directory" >&2; exit 2; fi
        if [ ! -d "$dir" ]; then
          jq -n --arg dir "$dir" '{ok:false, error:"not a directory", dir:$dir, count:0, entries:[]}'
          return 0
        fi
        sep="$(printf '\037')"   # 0x1f unit separator between fields; NUL between records
        find "$dir" -mindepth 1 -maxdepth 1 -printf "%y''${sep}%s''${sep}%T@''${sep}%f\0" \
          | jq -R -s -c --arg sep "$sep" --arg dir "$dir" '
              split("\u0000")
              | map(select(length>0) | split($sep)
                    | { name:.[3],
                        type:('"$TYPEMAP"'[.[0]] // .[0]),
                        size:(.[1]|tonumber),
                        mtime_epoch:(.[2]|tonumber|floor) })
              | { ok:true, dir:$dir, count:length, entries:. }'
      }

      cmd_stat() {
        path="''${1:-}"
        if [ -z "$path" ]; then echo "agos-files stat: need a path" >&2; exit 2; fi
        if [ ! -e "$path" ] && [ ! -L "$path" ]; then
          jq -n --arg p "$path" '{ok:true, exists:false, path:$p}'
          return 0
        fi
        sep="$(printf '\037')"
        find "$path" -maxdepth 0 -printf "%y''${sep}%s''${sep}%T@\n" \
          | jq -R -c --arg sep "$sep" --arg p "$path" '
              split($sep)
              | { ok:true, exists:true, path:$p,
                  type:('"$TYPEMAP"'[.[0]] // .[0]),
                  size:(.[1]|tonumber),
                  mtime_epoch:(.[2]|tonumber|floor) }'
      }

      case "''${1:-}" in
        list) shift; cmd_list "$@" ;;
        stat) shift; cmd_stat "$@" ;;
        *)    usage ;;
      esac
    '';
  };
in {
  environment.systemPackages = with pkgs; [
    agos-files       # the agent's files hand (JSON: list/stat, read-only)
    xfce.thunar      # human-facing Thunar file manager (appears in wofi drun automatically)
  ];
}
