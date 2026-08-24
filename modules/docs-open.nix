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
  agos-doc = import ./pkgs/agos-doc.nix { inherit pkgs; };
in {
  environment.systemPackages = [
    agos-doc        # the agent's read-only document hand (JSON: info/text)
    pkgs.zathura    # human-facing keyboard-driven document viewer (appears in wofi drun)
  ];
}
