# Agent OS — OPEN variant Phase 2 substrate: the nuclear-accurate calendar
# (Dillon's flagged highest-value Phase 2 item; roadmap Phase 2, owner Augur).
#
# WHY (roadmap 2026-07-31, Phase 2 "ambient substrate"): "nuclear-accurate calendar
# the agent reads AND writes — grounds the brain in the real now, past training cutoff.
# A real cal app/store the agent-brain can query + mutate, not a text file."
#
# WHAT THIS INCREMENT SHIPS (the substrate — headless, no desktop needed):
#   * a REAL iCalendar store: khal on a local vdir at /var/lib/agos-calendar/agent
#     (per-event *.ics files with VTIMEZONE — a real store, not a flat text file).
#   * `agos-cal`, a stable JSON-emitting CLI the agent drives: now / agenda / add / cals.
#     This is the exact surface the future agent-brain `calendar.*` hand wraps (grammar
#     proposed to Rabbot — agent-brain tool-grammar is a cross-brain escalation, so the
#     hand-wiring lands there, not here).
# DEFERRED to later Phase 2 increments (each its own branch):
#   * the GUI calendar app tiling in Hyprland — BLOCKED on the desktop foundation, which
#     is not yet reproducible in the flake (Phase 0 graphical layer is not in-tree; see
#     the augur-to-rabbot report). When the GUI lands, a localhost radicale + vdirsyncer
#     bridge exposes THIS same vdir over CalDAV so the GUI and the agent share one store.
#
# ISOLATION: OPEN-only, self-contained, shares ZERO modules with the sovereign path —
# imported solely from configuration-open.nix. Nothing here can perturb the sealed
# surface. SEAL-TIME FOLLOW-UP: at the sovereign repackage, the calendar capability
# folds into a shared substrate module (same pattern as the planned hardware-base.nix).
{ config, pkgs, lib, ... }:

let
  calRoot = "/var/lib/agos-calendar";
  calName = "agent";
  calPath = "${calRoot}/${calName}";

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

  # The agent's calendar hand-surface. Emits JSON on stdout for every read; writes go
  # through khal. Invocations below are the ones proven against khal 0.14.0 (nixpkgs
  # unstable) before this module was written. The field separator is generated at
  # runtime (0x1f) and handed to jq via --arg, so no control char lives in the source.
  agosCal = pkgs.writeShellApplication {
    name = "agos-cal";
    runtimeInputs = [ pkgs.khal pkgs.jq pkgs.coreutils ];
    text = ''
      CONF="${khalConf}"
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
          khal -c "$CONF" list --day-format "" \
               --format "{start}''${sep}{end}''${sep}{title}" today "''${days}d" \
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
          khal -c "$CONF" new -a "$CAL" "''${sarr[@]}" "''${earr[@]}" "$summary" >/dev/null
          printf '{"ok":true,"start":"%s","end":"%s","title":"%s"}\n' "$start" "$end" "$summary"
          ;;
        cals)
          khal -c "$CONF" printcalendars | jq -R -s -c 'split("\n") | map(select(length>0))'
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
  };
in {
  # The store dir must exist + be agent-writable before first use. setgid (2) so new
  # *.ics inherit group `users` even when root (Rabbot over SSH) creates them.
  systemd.tmpfiles.rules = [
    "d ${calRoot} 0755 ${calName} users -"
    "d ${calPath} 2775 ${calName} users -"
  ];

  environment.systemPackages = [ pkgs.khal agosCal ];
}
