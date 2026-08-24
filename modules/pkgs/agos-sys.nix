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
      vol_raw=$(wpctl get-volume @DEFAULT_AUDIO_SINK@ 2>/dev/null) || vol_raw=""
      vol_pct=$(printf '%s' "$vol_raw" | awk '/Volume/{printf "%d", $2*100}') || vol_pct=""
      if printf '%s' "$vol_raw" | grep -q "MUTED"; then vol_muted="true"; else vol_muted="false"; fi

      # --- display backlight ---
      bri_pct=$(brightnessctl -m 2>/dev/null \
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

    cmd_volume() {
      case "''${1:-}" in
        mute)    wpctl set-mute   @DEFAULT_AUDIO_SINK@ 1 ;;
        unmute)  wpctl set-mute   @DEFAULT_AUDIO_SINK@ 0 ;;
        toggle)  wpctl set-mute   @DEFAULT_AUDIO_SINK@ toggle ;;
        ""|*[!0-9]*) echo "usage: agos-sys volume <0-100|mute|unmute|toggle>" >&2; exit 2 ;;
        *)       wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ "''${1}%" ;;  # -l caps at 100%
      esac
    }

    cmd_brightness() {
      case "''${1:-}" in
        ""|*[!0-9]*) echo "usage: agos-sys brightness <0-100>" >&2; exit 2 ;;
        *)       brightnessctl set "''${1}%" >/dev/null ;;
      esac
    }

    case "''${1:-}" in
      status)     cmd_status ;;
      volume)     cmd_volume "''${2:-}" ;;
      brightness) cmd_brightness "''${2:-}" ;;
      *)          usage ;;
    esac
  '';
}
