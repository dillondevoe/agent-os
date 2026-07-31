# modules/settings-open.nix — Phase 2: the settings surface (OPEN variant).
#
# The roadmap's "settings" app, built to its acceptance bar ("agent can read+drive it"):
# a small agent-facing CLI hand — `agos-sys` — that reports live system state as JSON and
# drives the two settings a headless agent actually changes (audio volume, display
# brightness), plus a compact set of human-facing GUI tools (audio mixer, display
# arrangement, NetworkManager editor) for the console/Waybar path.
#
# This mirrors the calendar substrate's shape (modules/calendar-open.nix → `agos-cal`):
# a thin, JSON-contract shell hand the agent-brain can wrap 1:1, over the box's real
# backends (NetworkManager for net, PipeWire/wireplumber for audio, brightnessctl for
# backlight). OPEN-only, self-contained, imported solely from configuration-open.nix —
# it shares nothing with the sealed sovereign path. Fold into a shared substrate module
# at seal-time (same follow-up as calendar + desktop).
#
# Runtime deps that live in OTHER open increments (build-independent, noted): the audio
# reads/sets need PipeWire running (modules/desktop-open.nix); brightness needs a real
# backlight (bare-metal Dell). Absent either, `agos-sys status` degrades gracefully to
# null fields — it never errors — so the CLI builds and runs anywhere.
{ pkgs, ... }:
let
  # The agent's system-settings hand. writeShellApplication runs shellcheck at build
  # time (real acceptance) and pins every backend binary into runtimeInputs.
  agos-sys = pkgs.writeShellApplication {
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
  };
in {
  environment.systemPackages = with pkgs; [
    agos-sys              # the agent's system-settings hand (JSON status + volume/brightness)
    # Human-facing settings GUIs (console / Waybar path) — the "surface" a person clicks:
    pavucontrol           # audio mixer / device routing
    wdisplays             # Wayland display arrangement (wlroots/Hyprland)
    networkmanagerapplet  # nm-applet + nm-connection-editor (NM is the backend)
  ];
}
