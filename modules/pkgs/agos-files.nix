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
      # CAPTURE, then pipe. `find | jq` under `set -euo pipefail` is a FALSE-ANSWER generator,
      # and this instance is the worst of the three this repo has found: jq wraps whatever find
      # managed to print and STAMPS `ok:true` on it, so an unreadable directory answered
      # {"ok":true,"count":0,"entries":[]} with rc 1 underneath. Measured 2026-08-24 on a
      # chmod-000 dir holding three entries. The `-d` guard above catches "not a directory" and
      # cannot see this: the directory EXISTS. Worse, a PARTIALLY readable tree returns some
      # entries and a `count` that is a confident undercount — a lie with a number attached.
      # agos-notes had this shape, then agos-cal; this is the third hand, and the only one that
      # asserts ok:true over the failure rather than merely returning a bare value.
      # A TEMP FILE, NOT `raw=$(...)`. The obvious capture-then-pipe remedy — the one that
      # fixed agos-notes and agos-cal — SILENTLY BROKE THIS HAND, and the control arm on the
      # SUCCESS path is the only reason I know: a dir with 3 entries came back count=1. Command
      # substitution strips NUL bytes, and NUL is this pipeline's record separator (chosen
      # because a filename may legally contain a newline). So the remedy for a confident
      # undercount manufactured a confident undercount, on the path that was previously correct.
      # The fix cannot borrow the shape; it has to preserve the bytes. THE REMEDY IS NOT THE
      # LESSON — "capture, then pipe" is shorthand for "do not let a failing producer's exit
      # code be swallowed", and $( ) is only one way to hold the output.
      out=$(mktemp) || { jq -n --arg dir "$dir" '{ok:false,error:"no temp space",dir:$dir,count:0,entries:[]}'; return 0; }
      trap 'rm -f "$out"' RETURN
      if ! err=$(find "$dir" -mindepth 1 -maxdepth 1 \
                   -printf "%y''${sep}%s''${sep}%T@''${sep}%f\0" 2>&1 >"$out"); then
        jq -n --arg dir "$dir" --arg e "$err" \
          '{ok:false, error:"cannot list directory", detail:$e, dir:$dir, count:0, entries:[]}'
        return 0
      fi
      jq -R -s -c --arg sep "$sep" --arg dir "$dir" '
            split("\u0000")
            | map(select(length>0) | split($sep)
                  | { name:.[3],
                      type:('"$TYPEMAP"'[.[0]] // .[0]),
                      size:(.[1]|tonumber),
                      mtime_epoch:(.[2]|tonumber|floor) })
            | { ok:true, dir:$dir, count:length, entries:. }' <"$out"
    }

    cmd_stat() {
      path="''${1:-}"
      if [ -z "$path" ]; then echo "agos-files stat: need a path" >&2; exit 2; fi
      if [ ! -e "$path" ] && [ ! -L "$path" ]; then
        jq -n --arg p "$path" '{ok:true, exists:false, path:$p}'
        return 0
      fi
      sep="$(printf '\037')"
      if ! raw=$(find "$path" -maxdepth 0 -printf "%y''${sep}%s''${sep}%T@\n" 2>&1); then
        jq -n --arg p "$path" --arg e "$raw" '{ok:false, error:"cannot stat path", detail:$e, path:$p}'
        return 0
      fi
      printf '%s\n' "$raw" \
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
