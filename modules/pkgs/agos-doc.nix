# agos-doc — the agent's hand, extracted from modules/docs-open.nix so something other than a
# NixOS module can name it.
#
# WHY THIS FILE EXISTS: a `let` binding inside a NixOS module is unreachable from
# outside that module. Nothing in the repo could put this CLI on a PATH, so
# tests/agos-doc-battery.py could only ever run against a machine that had already
# booted the whole desktop image — which is why it sat on KNOWN_UNWIRED_DEBT.
# Extracted, it is an ordinary derivation a cheap runCommand check can depend on.
#
# The GUI stays in the module. The hand and the GUI are separate things and only
# one of them is a contract.

{ pkgs }:

let

  # `poppler-utils` is the live attribute (the underscore `poppler_utils` is a removed alias
  # that throws on eval). Hyphen ⇒ must be quoted string-access and bound here — a bareword
  # `poppler-utils` inside `with pkgs; [ … ]` would parse as the subtraction `poppler - utils`.
  popplerUtils = pkgs."poppler-utils";
in
pkgs.writeShellApplication {
  name = "agos-doc";
  runtimeInputs = [ popplerUtils pkgs.coreutils pkgs.gnused pkgs.jq ];
  text = ''
    usage() {
      cat >&2 <<'USAGE'
    agos-doc — the agent's read-only document hand (JSON out). PDF via poppler.

      agos-doc info <path>          {ok,path,pages,title,author,pdf_version,page_size,bytes}
      agos-doc text <path> [page]   extract text — whole doc, or one 1-indexed page:
                                    {ok,path,page,text}
    USAGE
      exit 2
    }

    # A MISSING BACKEND IS NOT A DEFECTIVE SUBJECT, and until 2026-08-24 this hand said it was.
    # Every backend call below is guarded with `if ! out=$(...)`, which is right for capturing a real
    # failure — but "the tool is not installed" and "the file is broken" arrive through that same
    # branch, and the branch names the FILE. Probed with poppler stripped from PATH against a PDF that
    # is demonstrably fine: `{ok:false,error:"not a readable PDF"}`. That is not a MISSING reason, it
    # is a WRONG one. The caller is misinformed rather than uninformed, and one that believes it may
    # tell someone their document is corrupt, or fall back, or delete it, on the strength of a claim
    # this hand had no evidence for. Same severity ordering as the swallowed-producer sweep earlier
    # today: agos-notes/agos-cal left the caller uninformed, agos-files stamped ok:true on a lie, and
    # this stamps a specific, confident, false diagnosis of the subject.
    #
    # The backends are named through variables so the branch is REACHABLE FROM A TEST. Stripping PATH
    # cannot reach it — writeShellApplication prepends `runtimeInputs` INSIDE the wrapper, so a probe
    # would take the ordinary success path while the battery printed green about a branch it never
    # entered. Same override pattern as AGOS_CAL_CONF and AGOS_CALC_QALC, and the same reason.
    PDFINFO="''${AGOS_DOC_PDFINFO:-pdfinfo}"
    PDFTOTEXT="''${AGOS_DOC_PDFTOTEXT:-pdftotext}"
    require_backend() {  # $1 binary, $2 subject path
      command -v "$1" >/dev/null 2>&1 && return 0
      jq -n --arg p "$2" --arg b "$1" \
        '{ok:false, error:"document backend absent", detail:("not on PATH: " + $b), path:$p}'
      return 1
    }

    cmd_info() {
      path="''${1:-}"
      if [ -z "$path" ]; then echo "agos-doc info: need a path" >&2; exit 2; fi
      if [ ! -f "$path" ]; then jq -n --arg p "$path" '{ok:false,error:"no such file",path:$p}'; return 0; fi
      require_backend "$PDFINFO" "$path" || return 0
      if ! info=$("$PDFINFO" "$path" 2>/dev/null); then
        jq -n --arg p "$path" '{ok:false,error:"not a readable PDF",path:$p}'; return 0
      fi
      # Each pdfinfo field is a single line "Name:   value" — strip the label + leading space.
      field() { printf '%s\n' "$info" | sed -n "s/^$1:[[:space:]]*//p" | head -n1; }
      pages=$(field Pages | tr -dc '0-9'); [ -z "$pages" ] && pages=0
      title=$(field Title)
      author=$(field Author)
      pdfver=$(field "PDF version")
      psize=$(field "Page size")
      bytes=$(stat -c '%s' "$path")
      jq -n --arg p "$path" --argjson pages "$pages" --arg title "$title" \
            --arg author "$author" --arg pdfver "$pdfver" --arg psize "$psize" \
            --argjson bytes "$bytes" \
        '{ok:true, path:$p, pages:$pages, title:$title, author:$author,
          pdf_version:$pdfver, page_size:$psize, bytes:$bytes}'
    }

    cmd_text() {
      path="''${1:-}"; page="''${2:-}"
      if [ -z "$path" ]; then echo "agos-doc text: need a path" >&2; exit 2; fi
      if [ ! -f "$path" ]; then jq -n --arg p "$path" '{ok:false,error:"no such file",path:$p}'; return 0; fi
      if [ -n "$page" ]; then
        case "$page" in *[!0-9]*) echo "agos-doc text: page must be a number" >&2; exit 2 ;; esac
        require_backend "$PDFTOTEXT" "$path" || return 0
        if ! body=$("$PDFTOTEXT" -f "$page" -l "$page" "$path" - 2>/dev/null); then
          jq -n --arg p "$path" '{ok:false,error:"not a readable PDF",path:$p}'; return 0
        fi
        jq -n --arg p "$path" --argjson page "$page" --arg body "$body" '{ok:true,path:$p,page:$page,text:$body}'
      else
        require_backend "$PDFTOTEXT" "$path" || return 0
        if ! body=$("$PDFTOTEXT" "$path" - 2>/dev/null); then
          jq -n --arg p "$path" '{ok:false,error:"not a readable PDF",path:$p}'; return 0
        fi
        jq -n --arg p "$path" --arg body "$body" '{ok:true,path:$p,page:null,text:$body}'
      fi
    }

    case "''${1:-}" in
      info) shift; cmd_info "$@" ;;
      text) shift; cmd_text "$@" ;;
      *)    usage ;;
    esac
  '';
}
