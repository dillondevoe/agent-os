# modules/notes-open.nix — Phase 2: notes (OPEN variant).
#
# The roadmap's "notes" ambient app. Mirrors calendar-open exactly: a REAL local store the
# agent reads AND writes, plus a JSON-emitting CLI hand + a human GUI. Fully local and
# credential-free (unlike email) — so the GUI+hand symmetry is back.
#
# WHAT THIS SHIPS:
#   * a plain-markdown note store at /var/lib/agos-notes (one <slug>.md per note — a real
#     store the agent-brain and the human's editor both see, not an opaque DB).
#   * `agos-notes`, a stable JSON-emitting CLI: list / new / read / append. This is the
#     surface the future agent-brain `notes.*` hand wraps 1:1 (grammar-wiring is Rabbot's
#     lane; the hand itself is this credential-free local CLI, same as agos-cal).
#   * Apostrophe — a distraction-free markdown editor for the human. It ships a .desktop, so
#     `wofi --show drun` ($mod+R, from desktop-open.nix) finds it and Hyprland tiles it. The
#     human opens notes from /var/lib/agos-notes; agent + human share one plain-md store.
#
# Slugs are sanitized to [a-z0-9-] so note filenames are always tame (no spaces/tabs/newlines
# in a filename we generate) — which is why list/read use simple field separators safely,
# unlike files-open (which must survive arbitrary externally-named files).
#
# ISOLATION: OPEN-only, self-contained, imported solely from configuration-open.nix. Shares
# nothing with the sealed path — fold into a shared substrate module at seal-time.
{ pkgs, ... }:
let
  notesDir = "/var/lib/agos-notes";

  agos-notes = pkgs.writeShellApplication {
    name = "agos-notes";
    runtimeInputs = with pkgs; [ coreutils gnused findutils jq ];
    text = ''
      NOTES="${notesDir}"

      usage() {
        cat >&2 <<'USAGE'
      agos-notes — the agent's notes hand (JSON out). Store: plain-markdown <slug>.md files.

        agos-notes list                 all notes, newest first:
                                        [{slug,title,bytes,mtime_epoch}]
        agos-notes new "<title>"        create a note (slug derived from title):
                                        {ok,slug,title,path}   (ok:false if the slug exists)
        agos-notes read <slug>          {ok,slug,title,body}
        agos-notes append <slug> <text…>  append a line: {ok,slug}
      USAGE
        exit 2
      }

      # Title -> slug: lowercase, non-alnum runs -> '-', trim leading/trailing '-'.
      slugify() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//'; }

      # First line of a note, stripped of a leading "# ", else the slug.
      title_of() {
        local t; t=$(head -n1 "$1" 2>/dev/null | sed -E 's/^#[[:space:]]*//')
        if [ -n "$t" ]; then printf '%s' "$t"; else printf '%s' "$2"; fi
      }

      cmd_list() {
        find "$NOTES" -maxdepth 1 -name '*.md' -printf '%f\t%s\t%T@\n' 2>/dev/null \
          | while IFS="$(printf '\t')" read -r fname bytes mtime; do
              slug="''${fname%.md}"
              title=$(title_of "$NOTES/$fname" "$slug")
              jq -n --arg slug "$slug" --arg title "$title" \
                    --argjson bytes "$bytes" --argjson mtime "''${mtime%.*}" \
                    '{slug:$slug,title:$title,bytes:$bytes,mtime_epoch:$mtime}'
            done \
          | jq -s -c 'sort_by(-.mtime_epoch)'
      }

      cmd_new() {
        title="$*"
        if [ -z "$title" ]; then echo "agos-notes new: need a title" >&2; exit 2; fi
        slug=$(slugify "$title")
        if [ -z "$slug" ]; then echo "agos-notes new: title has no slug-able characters" >&2; exit 2; fi
        path="$NOTES/$slug.md"
        if [ -e "$path" ]; then
          jq -n --arg slug "$slug" --arg path "$path" '{ok:false,error:"note exists",slug:$slug,path:$path}'
          return 0
        fi
        printf '# %s\n' "$title" > "$path"
        jq -n --arg slug "$slug" --arg title "$title" --arg path "$path" '{ok:true,slug:$slug,title:$title,path:$path}'
      }

      cmd_read() {
        slug="''${1:-}"
        if [ -z "$slug" ]; then echo "agos-notes read: need a slug" >&2; exit 2; fi
        path="$NOTES/$slug.md"
        if [ ! -e "$path" ]; then jq -n --arg slug "$slug" '{ok:false,error:"no such note",slug:$slug}'; return 0; fi
        title=$(title_of "$path" "$slug")
        body=$(cat "$path")
        jq -n --arg slug "$slug" --arg title "$title" --arg body "$body" '{ok:true,slug:$slug,title:$title,body:$body}'
      }

      cmd_append() {
        slug="''${1:-}"; if [ "$#" -gt 0 ]; then shift; fi
        text="$*"
        if [ -z "$slug" ]; then echo "agos-notes append: need a slug and text" >&2; exit 2; fi
        path="$NOTES/$slug.md"
        if [ ! -e "$path" ]; then jq -n --arg slug "$slug" '{ok:false,error:"no such note",slug:$slug}'; return 0; fi
        printf '%s\n' "$text" >> "$path"
        jq -n --arg slug "$slug" '{ok:true,slug:$slug}'
      }

      case "''${1:-}" in
        list)   cmd_list ;;
        new)    shift; cmd_new "$@" ;;
        read)   shift; cmd_read "$@" ;;
        append) shift; cmd_append "$@" ;;
        *)      usage ;;
      esac
    '';
  };
in {
  # Store dir must exist + be agent-writable before first use. setgid (2) so new *.md inherit
  # group `users` even when root (Rabbot over SSH) creates them — same as calendar's vdir.
  systemd.tmpfiles.rules = [
    "d ${notesDir} 2775 agent users -"
  ];

  environment.systemPackages = with pkgs; [
    agos-notes       # the agent's notes hand (JSON: list/new/read/append)
    apostrophe       # human-facing markdown notes editor (appears in wofi drun automatically)
  ];
}
