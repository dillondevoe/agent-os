# agos-files — the agent's hand, extracted from modules/files-open.nix so something other than a
# NixOS module can name it.
#
# WHY THIS FILE EXISTS: a `let` binding inside a NixOS module is unreachable from
# outside that module. Nothing in the repo could put this CLI on a PATH, so
# tests/agos-files-battery.py could only ever run against a machine that had already
# booted the whole desktop image — which is why it sat on KNOWN_UNWIRED_DEBT.
# Extracted, it is an ordinary derivation a cheap runCommand check can depend on.
#
# The GUI stays in the module. The hand and the GUI are separate things and only
# one of them is a contract.

{ pkgs }:

# The agent's files hand. writeShellApplication runs shellcheck at build time (real
# acceptance) and pins `find`/`jq` into runtimeInputs — no runtime PATH dependence.
# Records are NUL-separated and fields 0x1f-separated (generated at runtime, handed to
# jq via --arg) so a filename containing spaces, tabs, or newlines can never corrupt
# the JSON — files are the domain, so odd names are expected, not an edge case.
pkgs.writeShellApplication {
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
}
