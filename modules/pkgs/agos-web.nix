# agos-web — the agent's hand, extracted from modules/web-open.nix so something other than a
# NixOS module can name it.
#
# WHY THIS FILE EXISTS: a `let` binding inside a NixOS module is unreachable from
# outside that module. Nothing in the repo could put this CLI on a PATH, so
# tests/agos-web-battery.py could only ever run against a machine that had already
# booted the whole desktop image — which is why it sat on KNOWN_UNWIRED_DEBT.
# Extracted, it is an ordinary derivation a cheap runCommand check can depend on.
#
# The GUI stays in the module. The hand and the GUI are separate things and only
# one of them is a contract.

{ pkgs }:

let

  # trafilatura's CLI lives in the python package's bin output; writeShellApplication puts it on
  # PATH via lib.makeBinPath. It is the readability/extract pass — it strips nav/boilerplate and
  # emits JSON with title/text (and, with --with-metadata, author/date).
  trafilatura = pkgs.python3Packages.trafilatura;
in
pkgs.writeShellApplication {
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

    # Same class again. With curl absent this hand reports "fetch failed or empty" — a claim about
    # the NETWORK — and with trafilatura absent it reports "no readable content", a claim about the
    # PAGE. Both are confident diagnoses of a subject the hand never reached. The web hand is the
    # worst place in the family for this, because "fetch failed" and "no readable content" are both
    # things that genuinely happen all the time, so the false version is indistinguishable from the
    # true one and a caller has no way to tell it is looking at a broken install.
    CURL="''${AGOS_WEB_CURL:-curl}"
    TRAFILATURA="''${AGOS_WEB_TRAFILATURA:-trafilatura}"
    require_backend() {  # $1 binary, $2 subject url
      command -v "$1" >/dev/null 2>&1 && return 0
      jq -n --arg u "$2" --arg b "$1" \
        '{ok:false, error:"web backend absent", detail:("not on PATH: " + $b), url:$u}'
      return 1
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
      require_backend "$CURL" "$url" || return 0
      require_backend "$TRAFILATURA" "$url" || return 0
      if ! html=$("$CURL" -sSL \
            --proto '=http,https' --proto-redir '=http,https' \
            --max-time 20 --max-filesize 5000000 --max-redirs 5 \
            -A "agos-web/1 (+read-only agent web hand)" \
            "$url" 2>/dev/null) || [ -z "$html" ]; then
        jq -n --arg u "$url" '{ok:false,error:"fetch failed or empty",url:$u}'; return 0
      fi
      # Readability/extract pass over the fetched HTML (from stdin). --with-metadata surfaces
      # title/author/date; --no-comments drops comment noise. Empty output => nothing readable.
      if ! extracted=$(printf '%s' "$html" | "$TRAFILATURA" --json --with-metadata --no-comments 2>/dev/null) \
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
}
