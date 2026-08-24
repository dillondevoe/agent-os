# modules/web-open.nix — Phase 2: web browser + read-only web-content hand (OPEN variant).
#
# The roadmap's "browser" ambient app — the TWELFTH and final app of the ambient dozen.
# Rabbot's RULING (rabbot-to-augur-browser-12-RULING-readonly-web-hand-2026-07-31) split the
# browser into two things and ruled only the READ-ONLY half in-lane as #12:
#
#   * THE HUMAN HALF — Firefox, the canonical web browser (ships a .desktop → wofi drun /
#     Hyprland tiles it). This is the "browse" half: it opens a URL for the HUMAN to see.
#
#   * THE AGENT HALF — `agos-web`, a stable JSON-emitting, READ-ONLY web-content hand. It
#     completes the read-hand family (files#7=dir/meta, docs#10=PDF content, media#11=A/V probe,
#     → web=page content): the agent can now READ a public web page to answer a question.
#       fetch <url> -> {ok,url,title,text,chars,author,date}
#     It fetches a public http(s) URL with curl (strictly capped — bounded time/size/redirects,
#     http(s)-only even across redirects) and runs the readability/extract pass with trafilatura
#     (--with-metadata --no-comments), which strips nav/boilerplate and returns the main content
#     plus title/author/date. jq reshapes that into the compact, stable contract so the agent
#     never parses trafilatura's schema. This is the "inference" half of browse-or-inference.
#
# EXPLICITLY OUT OF SCOPE (Rabbot's ruling): browser AUTOMATION — the agent DRIVING a real
# browser (click/scroll/forms, logged-in sessions). That is connector-shaped exactly like email
# (#8): it carries sessions + auth, so its auth bits escalate to Dillon. It is parked as its own
# LATER increment for the orchestration/integration phase — NOT a leaf built here.
#
# NO auth, NO credentials, NO connector, NO logged-in state → not auth-sensitive → in-lane.
# The `web.*` agent-brain grammar wraps `agos-web` 1:1 (wiring is Rabbot's lane, batched with the
# calc/sys/files/notes/doc/media hands).
#
# ISOLATION: OPEN-only, self-contained, imported solely from configuration-open.nix. Shares
# nothing with the sealed path — fold into a shared substrate module at seal-time.
{ pkgs, ... }:
let
  agos-web = import ./pkgs/agos-web.nix { inherit pkgs; };
in {
  environment.systemPackages = [
    agos-web        # the agent's read-only web-content hand (JSON: fetch)
    pkgs.firefox    # human-facing web browser (appears in wofi drun / Hyprland tile)
  ];
}
