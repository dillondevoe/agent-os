# agos-notes — the agent's hand, extracted from modules/notes-open.nix so something other than a
# NixOS module can name it.
#
# WHY THIS FILE EXISTS: a `let` binding inside a NixOS module is unreachable from
# outside that module. Nothing in the repo could put this CLI on a PATH, so
# tests/agos-notes-battery.py could only ever run against a machine that had already
# booted the whole desktop image — which is why it sat on KNOWN_UNWIRED_DEBT.
# Extracted, it is an ordinary derivation a cheap runCommand check can depend on.
#
# The GUI stays in the module. The hand and the GUI are separate things and only
# one of them is a contract.

{ pkgs, notesDir }:


pkgs.writeShellApplication {
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
}
