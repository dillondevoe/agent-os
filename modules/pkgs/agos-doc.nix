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

    cmd_info() {
      path="''${1:-}"
      if [ -z "$path" ]; then echo "agos-doc info: need a path" >&2; exit 2; fi
      if [ ! -f "$path" ]; then jq -n --arg p "$path" '{ok:false,error:"no such file",path:$p}'; return 0; fi
      if ! info=$(pdfinfo "$path" 2>/dev/null); then
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
        if ! body=$(pdftotext -f "$page" -l "$page" "$path" - 2>/dev/null); then
          jq -n --arg p "$path" '{ok:false,error:"not a readable PDF",path:$p}'; return 0
        fi
        jq -n --arg p "$path" --argjson page "$page" --arg body "$body" '{ok:true,path:$p,page:$page,text:$body}'
      else
        if ! body=$(pdftotext "$path" - 2>/dev/null); then
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
