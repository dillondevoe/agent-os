# modules/docs-open.nix — Phase 2: document reader (OPEN variant).
#
# The roadmap's "document reader" ambient app. Pairs with files-open (#7) and notes (#9):
# files-open gives the agent a directory/metadata hand (list/stat, NO content); this gives it
# a read-only CONTENT hand for the one rich format a human most often drops in — PDF. Now the
# agent can actually READ a document a human saved, not just see that it exists.
#
# WHAT THIS SHIPS:
#   * `agos-doc`, a stable JSON-emitting, READ-ONLY CLI over poppler (pdfinfo/pdftotext):
#       info <path>          -> {ok,path,pages,title,author,pdf_version,page_size,bytes}
#       text <path> [page]   -> {ok,path,page,text}   (whole doc, or one 1-indexed page)
#     Read-only by construction — poppler here only extracts; it never writes a document.
#     This is the surface a future agent-brain `doc.*` hand wraps 1:1 (wiring is Rabbot's lane).
#   * Zathura — a keyboard-driven document viewer for the human (ships a .desktop, so
#     `wofi --show drun` / $mod+R finds it and Hyprland tiles it).
#
# poppler is PDF-only, so agos-doc is scoped to PDF today; non-PDF / unreadable input returns
# {ok:false,error:…} rather than throwing. Paths come straight from the caller (often via
# agos-files), so info/text tolerate arbitrary paths and never assume an extension.
#
# ISOLATION: OPEN-only, self-contained, imported solely from configuration-open.nix. Shares
# nothing with the sealed path — fold into a shared substrate module at seal-time.
{ pkgs, ... }:
let
  # `poppler-utils` is the live attribute (the underscore `poppler_utils` is a removed alias
  # that throws on eval). Hyphen ⇒ must be quoted string-access and bound here — a bareword
  # `poppler-utils` inside `with pkgs; [ … ]` would parse as the subtraction `poppler - utils`.
  popplerUtils = pkgs."poppler-utils";

  agos-doc = pkgs.writeShellApplication {
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
  };
in {
  environment.systemPackages = [
    agos-doc        # the agent's read-only document hand (JSON: info/text)
    pkgs.zathura    # human-facing keyboard-driven document viewer (appears in wofi drun)
  ];
}
