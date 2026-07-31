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
  # trafilatura's CLI lives in the python package's bin output; writeShellApplication puts it on
  # PATH via lib.makeBinPath. It is the readability/extract pass — it strips nav/boilerplate and
  # emits JSON with title/text (and, with --with-metadata, author/date).
  trafilatura = pkgs.python3Packages.trafilatura;

  agos-web = pkgs.writeShellApplication {
    name = "agos-web";
    runtimeInputs = [ pkgs.curl trafilatura pkgs.coreutils pkgs.jq ];
    text = ''
      usage() {
        cat >&2 <<'USAGE'
      agos-web — the agent's read-only web-content hand (JSON out). Fetch a public URL, extract readable text.

        agos-web fetch <url>   fetch an http(s) URL, strip nav/boilerplate:
          {ok,url,title,text,chars,author,date}
      USAGE
        exit 2
      }

      cmd_fetch() {
        url="''${1:-}"
        if [ -z "$url" ]; then echo "agos-web fetch: need a url" >&2; exit 2; fi
        # Read-only WEB hand: only http(s). Refuse file://, ftp://, etc. up front.
        case "$url" in
          http://*|https://*) : ;;
          *) jq -n --arg u "$url" '{ok:false,error:"url must be http(s)://",url:$u}'; return 0 ;;
        esac
        # Fetch with strict caps: bounded time, download size, and redirect count; identify
        # politely; and constrain the protocol to http(s) even ACROSS redirects (no file:// jump).
        if ! html=$(curl -sSL \
              --proto '=http,https' --proto-redir '=http,https' \
              --max-time 20 --max-filesize 5000000 --max-redirs 5 \
              -A "agos-web/1 (+read-only agent web hand)" \
              "$url" 2>/dev/null) || [ -z "$html" ]; then
          jq -n --arg u "$url" '{ok:false,error:"fetch failed or empty",url:$u}'; return 0
        fi
        # Readability/extract pass over the fetched HTML (from stdin). --with-metadata surfaces
        # title/author/date; --no-comments drops comment noise. Empty output => nothing readable.
        if ! extracted=$(printf '%s' "$html" | trafilatura --json --with-metadata --no-comments 2>/dev/null) \
             || [ -z "$extracted" ]; then
          jq -n --arg u "$url" '{ok:false,error:"no readable content",url:$u}'; return 0
        fi
        # Reshape trafilatura's schema into the stable contract. Absent fields collapse to null.
        printf '%s' "$extracted" | jq --arg u "$url" '
          (.text // "") as $t
          | { ok:true, url:$u,
              title:(.title // null),
              text:$t,
              chars:($t | length),
              author:(.author // null),
              date:(.date // null) }'
      }

      case "''${1:-}" in
        fetch) shift; cmd_fetch "$@" ;;
        *)     usage ;;
      esac
    '';
  };
in {
  environment.systemPackages = [
    agos-web        # the agent's read-only web-content hand (JSON: fetch)
    pkgs.firefox    # human-facing web browser (appears in wofi drun / Hyprland tile)
  ];
}
