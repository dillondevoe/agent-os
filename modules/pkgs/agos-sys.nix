# agos-sys — the agent's hand, extracted from modules/settings-open.nix so something other than a
# NixOS module can name it.
#
# WHY THIS FILE EXISTS: a `let` binding inside a NixOS module is unreachable from
# outside that module. Nothing in the repo could put this CLI on a PATH, so
# tests/agos-sys-battery.py could only ever run against a machine that had already
# booted the whole desktop image — which is why it sat on KNOWN_UNWIRED_DEBT.
# Extracted, it is an ordinary derivation a cheap runCommand check can depend on.
#
# The GUI stays in the module. The hand and the GUI are separate things and only
# one of them is a contract.

{ pkgs }:

# The agent's system-settings hand. writeShellApplication runs shellcheck at build
# time (real acceptance) and pins every backend binary into runtimeInputs.
pkgs.writeShellApplication {
  name = "agos-sys";
  runtimeInputs = with pkgs; [
    coreutils gnugrep gawk jq
    networkmanager      # nmcli — network state (NM is the box's backend)
    wireplumber         # wpctl — PipeWire volume/mute
    brightnessctl       # backlight
  ];
  text = ''
    # Strict mode is on (writeShellApplication). Every probe below tolerates a missing
    # backend with `|| default` so `status` degrades to nulls instead of aborting.

    usage() {
      echo "usage: agos-sys {status | volume <0-100|mute|unmute|toggle> | brightness <0-100>}" >&2
      exit 2
    }

    cmd_status() {
      # --- network (NetworkManager) ---
      net_state=$(nmcli -t -f STATE general status 2>/dev/null) || net_state="unknown"
      net_dev=$(nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null \
        | awk -F: '$3=="connected" && $2!="loopback"{print $1; exit}') || net_dev=""
      net_type=$(nmcli -t -f DEVICE,TYPE device status 2>/dev/null \
        | awk -F: -v d="$net_dev" '$1==d{print $2; exit}') || net_type=""
      net_conn=$(nmcli -t -f DEVICE,CONNECTION device status 2>/dev/null \
        | awk -F: -v d="$net_dev" '$1==d{print $2; exit}') || net_conn=""
      wifi_signal=$(nmcli -t -f ACTIVE,SIGNAL device wifi 2>/dev/null \
        | awk -F: '$1=="yes"{print $2; exit}') || wifi_signal=""

      # --- audio (PipeWire via wireplumber) ---
      vol_raw=$("$WPCTL" get-volume @DEFAULT_AUDIO_SINK@ 2>/dev/null) || vol_raw=""
      vol_pct=$(printf '%s' "$vol_raw" | awk '/Volume/{printf "%d", $2*100}') || vol_pct=""
      if printf '%s' "$vol_raw" | grep -q "MUTED"; then vol_muted="true"; else vol_muted="false"; fi

      # --- display backlight ---
      bri_pct=$("$BRIGHTNESSCTL" -m 2>/dev/null \
        | awk -F, 'NR==1{gsub("%","",$4); print $4}') || bri_pct=""

      # --- battery (sysfs — no upower dep) ---
      bat_cap=""; bat_stat=""
      bat_dir=$(find /sys/class/power_supply -maxdepth 1 -name 'BAT*' 2>/dev/null | head -1) || bat_dir=""
      if [ -n "$bat_dir" ]; then
        bat_cap=$(cat "$bat_dir/capacity" 2>/dev/null) || bat_cap=""
        bat_stat=$(cat "$bat_dir/status" 2>/dev/null) || bat_stat=""
      fi

      jq -n \
        --arg net_state "$net_state" \
        --arg net_dev "$net_dev" \
        --arg net_type "$net_type" \
        --arg net_conn "$net_conn" \
        --argjson wifi_signal "''${wifi_signal:-null}" \
        --argjson volume "''${vol_pct:-null}" \
        --arg muted "$vol_muted" \
        --argjson brightness "''${bri_pct:-null}" \
        --argjson battery "''${bat_cap:-null}" \
        --arg battery_status "$bat_stat" \
        '{
           network: {
             state: $net_state,
             device: (if $net_dev == "" then null else $net_dev end),
             type: (if $net_type == "" then null else $net_type end),
             connection: (if $net_conn == "" then null else $net_conn end),
             wifi_signal: $wifi_signal
           },
           audio:   { volume: $volume, muted: ($muted == "true") },
           display: { brightness: $brightness },
           power:   {
             battery: $battery,
             status: (if $battery_status == "" then null else $battery_status end)
           }
         }'
    }

    # THE WRITE VERBS SAID NOTHING AT ALL, in either direction, until 2026-08-24. This is a
    # JSON hand: `status` returns a body, every other agos-* verb returns a body, and these two
    # printed EMPTY STDOUT on success — so a caller could not tell "the volume was set" from
    # "the call did nothing". On failure it was worse and it is a shape this repo has now fixed
    # three times: under `set -euo pipefail` a missing PipeWire or backlight aborted the script
    # with rc 1, no JSON, and wpctl's raw text on stderr — the `agos-notes new` defect exactly,
    # in the hand whose battery had ZERO ok:false arms and so could never have noticed.
    #
    # Off the Dell there IS no PipeWire and no backlight, which makes the degrade path the
    # NORMAL path everywhere except one machine. Contract: exit 2 stays for usage errors; a
    # backend that is absent or refuses is rc 0 with {ok:false} and the tool's own words in
    # `detail`, because a caller that cannot see stderr otherwise gets a silent no-op.
    #
    # NOTE FOR THE NEXT SWEEP: tests/hand-degrade-contract.py could not have found this, and
    # says so in its own header — no pipeline, no producer, nothing structural to match. It was
    # found by asking which battery asserted the contract LEAST (agos-sys: zero ok:false arms,
    # one rc==0 arm) and then reading that hand. The static check and that question are
    # different instruments; this hand is the evidence that neither one covers the other.
    # ABSENT vs REFUSED, and this hand was the LAST rung of a ladder the repo climbed on
    # 2026-08-24. `emit_write` below SAID `error:"backend refused or is absent"` — one string for
    # two states a caller must treat differently. "wpctl said no" is a fact about the machine's
    # audio; "wpctl is not installed" is a fact about the BUILD, and no amount of retrying fixes
    # it. agos-cal got this same treatment while being the honest one of its family, on the
    # reasoning that nearly-right is the state in which a rule quietly stops applying to you.
    # This hand is nearly-right in exactly the same way.
    #
    # The overrides exist because a PATH strip cannot reach these: writeShellApplication prepends
    # runtimeInputs INSIDE the wrapper, so a probe would take the ordinary path while a battery
    # printed green about a branch it never entered.
    #
    # `cmd_status` is deliberately NOT guarded. Its contract is to degrade every probe to null —
    # an absent backend there is a MISSING FIELD, not an error — and a guard would convert a
    # working degrade into a refusal. The guard belongs where absence is reported AS a failure.
    WPCTL="''${AGOS_SYS_WPCTL:-wpctl}"
    BRIGHTNESSCTL="''${AGOS_SYS_BRIGHTNESSCTL:-brightnessctl}"

    require_backend() {  # $1 binary, $2 verb, $3 requested
      command -v "$1" >/dev/null 2>&1 && return 0
      jq -n --arg v "$2" --arg r "$3" --arg b "$1" \
        '{ok:false, verb:$v, requested:$r, error:"system backend absent",
           detail:("not on PATH: " + $b)}'
      return 1
    }

    # `error` was "backend refused or is absent" until the guard above existed, and that string
    # was accurate then and is a LIE now: absence is caught before this emitter is reached, so
    # every message this branch prints is a genuine refusal. A disjunction left standing after
    # one of its arms is made unreachable reads as caution and is actually staleness.
    emit_write() {
      # $1 verb, $2 requested value, $3 rc, $4 captured output
      if [ "$3" -eq 0 ]; then
        jq -n --arg v "$1" --arg r "$2" '{ok:true, verb:$v, requested:$r}'
      else
        jq -n --arg v "$1" --arg r "$2" --arg e "$4" \
          '{ok:false, verb:$v, requested:$r, error:"backend refused", detail:$e}'
      fi
    }

    cmd_volume() {
      # ORDER MATTERS, and it is not the order the guard was first written in. A usage error is
      # a fact about the CALL and is true whether or not the backend exists; an absent backend
      # is a fact about the machine. Guarding first made `agos-sys volume bogus` answer rc 0
      # {ok:false,"system backend absent"} on every host without PipeWire -- which is every host
      # but the Dell -- silently retiring the exit-2 usage contract for this verb.
      case "''${1:-}" in
        mute|unmute|toggle) : ;;
        ""|*[!0-9]*) echo "usage: agos-sys volume <0-100|mute|unmute|toggle>" >&2; exit 2 ;;
      esac
      require_backend "$WPCTL" volume "''${1:-}" || return 0
      case "''${1:-}" in
        mute)    if out=$("$WPCTL" set-mute @DEFAULT_AUDIO_SINK@ 1 2>&1); then rc=0; else rc=$?; fi ;;
        unmute)  if out=$("$WPCTL" set-mute @DEFAULT_AUDIO_SINK@ 0 2>&1); then rc=0; else rc=$?; fi ;;
        toggle)  if out=$("$WPCTL" set-mute @DEFAULT_AUDIO_SINK@ toggle 2>&1); then rc=0; else rc=$?; fi ;;
        ""|*[!0-9]*) echo "usage: agos-sys volume <0-100|mute|unmute|toggle>" >&2; exit 2 ;;
        # -l caps at 100% so a caller cannot drive the sink above unity.
        *)       if out=$("$WPCTL" set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ "''${1}%" 2>&1); then rc=0; else rc=$?; fi ;;
      esac
      emit_write volume "''${1}" "$rc" "$out"
    }

    cmd_brightness() {
      case "''${1:-}" in
        ""|*[!0-9]*) echo "usage: agos-sys brightness <0-100>" >&2; exit 2 ;;
      esac
      require_backend "$BRIGHTNESSCTL" brightness "''${1:-}" || return 0
      case "''${1:-}" in
        ""|*[!0-9]*) echo "usage: agos-sys brightness <0-100>" >&2; exit 2 ;;
        *)       if out=$("$BRIGHTNESSCTL" set "''${1}%" 2>&1); then rc=0; else rc=$?; fi ;;
      esac
      emit_write brightness "''${1}" "$rc" "$out"
    }

    case "''${1:-}" in
      status)     cmd_status ;;
      volume)     cmd_volume "''${2:-}" ;;
      brightness) cmd_brightness "''${2:-}" ;;
      *)          usage ;;
    esac
  '';
}
