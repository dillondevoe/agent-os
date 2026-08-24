# agos-cal.nix — the agent's calendar hand, extracted from calendar-open.nix 2026-08-24.
#
# Seventh in the extraction that began at e5d5c5c. The reason is the same one: while the hand
# was a `let` binding inside a NixOS module, NOTHING outside that module could name it, so no
# derivation could put it on a PATH and calendar-battery.py ran in no CI lane at all — it sat
# on KNOWN_UNWIRED_DEBT because it was structurally unwireable, not because nobody got to it.
#
# khalConf TRAVELS with the hand (the module body never referenced it — two refs total, its own
# binding and the CONF line below). calName and calPath STAY in the module, which uses both for
# tmpfiles rules and radicale, and are passed in here rather than duplicated. Duplicating a
# shared binding across two files is the scar this extraction route has avoided six times.
{ pkgs, calName, calPath }:

let
  # khal config — READ-only (lives in the immutable /etc store symlink); khal WRITES
  # only into calPath (mutable, tmpfiles-created below). Timezone pinned so "now" and
  # every stored event are unambiguous. Formats fixed so agos-cal's parse is stable.
  khalConf = pkgs.writeText "agos-khal.conf" ''
    [calendars]
    [[${calName}]]
    path = ${calPath}
    type = calendar

    [locale]
    timeformat = %H:%M
    dateformat = %Y-%m-%d
    longdateformat = %Y-%m-%d
    datetimeformat = %Y-%m-%d %H:%M
    longdatetimeformat = %Y-%m-%d %H:%M
    default_timezone = America/Chicago
    local_timezone = America/Chicago

    [default]
    default_calendar = ${calName}
  '';

in
# The agent's calendar hand-surface. Emits JSON on stdout for every read; writes go
# through khal. Invocations below are the ones proven against khal 0.14.0 (nixpkgs
# unstable) before this module was written. The field separator is generated at
# runtime (0x1f) and handed to jq via --arg, so no control char lives in the source.
pkgs.writeShellApplication {
  name = "agos-cal";
  runtimeInputs = [ pkgs.khal pkgs.jq pkgs.coreutils ];
  text = ''
    # AGOS_CAL_CONF overrides the baked config so a sandbox can point the hand at a WRITABLE
    # vdir. The baked value stays the default, so deployed behaviour is unchanged. Without this
    # the contract lane cannot reach `add`/`agenda`/`cals` at all — khal's store lives under
    # /var/lib, which no build sandbox can create — and those arms would be wired but never
    # executed, which this repo has now twice found to be indistinguishable from passing.
    CONF="''${AGOS_CAL_CONF:-${khalConf}}"
    CAL="${calName}"

    usage() {
      cat >&2 <<'USAGE'
    agos-cal — the agent's calendar hand (JSON out).

      agos-cal now                     current instant: {iso,epoch,tz,weekday,date}
      agos-cal agenda [DAYS]           events today..+DAYS (default 7) as a JSON array
      agos-cal add "<YYYY-MM-DD HH:MM>" "<summary>" ["<YYYY-MM-DD HH:MM>"]
                                       create an event (end optional; khal defaults 1h)
      agos-cal cals                    list calendar collections

    Store: a real iCalendar vdir (per-event *.ics). Config is read-only.
    USAGE
    }

    cmd="''${1:-}"
    if [ "$#" -gt 0 ]; then shift; fi
    case "$cmd" in
      now)
        # Nuclear-accurate: the system clock is NTP-synced (systemd-timesyncd).
        printf '{"iso":"%s","epoch":%s,"tz":"%s","weekday":"%s","date":"%s"}\n' \
          "$(date -Iseconds)" "$(date +%s)" "$(date +%Z)" "$(date +%A)" "$(date +%Y-%m-%d)"
        ;;
      agenda)
        days="''${1:-7}"
        sep="$(printf '\037')"   # 0x1f unit separator — never appears in a title
        # CAPTURE, then pipe. `khal | jq` looks natural and is a FALSE-ANSWER generator: jq
        # succeeds on empty input and prints `[]` BEFORE pipefail can set the exit code, so a
        # broken calendar store emitted a perfectly plausible "no events today" on stdout with
        # rc 1 underneath. Proved 2026-08-24 against a khal stub that exits 1 — stdout `[]`,
        # rc 1, on all three khal-backed verbs. An empty agenda is a BELIEVABLE answer, which
        # makes this strictly worse than the `agos-notes list` case it rhymes with: there the
        # caller got a correct value with a failing rc; here the value itself is a lie.
        if ! raw=$(khal -c "$CONF" list --day-format "" \
             --format "{start}''${sep}{end}''${sep}{title}" today "''${days}d" 2>&1); then
          jq -n --arg e "$raw" '{ok:false,error:"khal list failed",detail:$e}'
          exit 0
        fi
        printf '%s\n' "$raw" \
          | jq -R -c --arg sep "''${sep}" 'select(length>0) | split($sep) | {start:.[0],end:.[1],title:.[2]}' \
          | jq -s -c '.'
        ;;
      add)
        start="''${1:?agos-cal add: need a start \"YYYY-MM-DD HH:MM\"}"
        summary="''${2:?agos-cal add: need a summary}"
        end="''${3:-}"
        # khal `new` takes START (and END) as whitespace-split positional tokens.
        read -r -a sarr <<< "$start"
        if [ -n "$end" ]; then read -r -a earr <<< "$end"; else earr=(); fi
        # Same capture rule, different failure: `add` emitted NO JSON AT ALL under set -e, so
        # the one uniform thing every hand promises — a parseable body on stdout — was absent
        # exactly when the caller most needs to be told why.
        if ! raw=$(khal -c "$CONF" new -a "$CAL" "''${sarr[@]}" "''${earr[@]}" "$summary" 2>&1); then
          jq -n --arg e "$raw" '{ok:false,error:"khal new failed",detail:$e}'
          exit 0
        fi
        printf '{"ok":true,"start":"%s","end":"%s","title":"%s"}\n' "$start" "$end" "$summary"
        ;;
      cals)
        if ! raw=$(khal -c "$CONF" printcalendars 2>&1); then
          jq -n --arg e "$raw" '{ok:false,error:"khal printcalendars failed",detail:$e}'
          exit 0
        fi
        printf '%s\n' "$raw" | jq -R -s -c 'split("\n") | map(select(length>0))'
        ;;
      ""|-h|--help|help)
        usage
        if [ "$cmd" = "" ]; then exit 2; fi
        exit 0
        ;;
      *)
        echo "agos-cal: unknown command: $cmd" >&2
        usage
        exit 2
        ;;
    esac
  '';
}
