{ config, lib, pkgs, ... }:
let
  # ONE spelling of the pref-reading rule, consumed by the healer AND by the flake
  # check that arms it. The bug this replaced (`.RunSSH // "MISSING"`) survived review
  # and a green deploy; what it could not have survived is a test feeding it the false
  # case. Keeping the program here rather than retyping it in the check is the point —
  # a check that carries its own copy of the expression stops testing the shipped one
  # the moment they drift, and every scar on this surface is that drift.
  runSSHJqProgram = ''if has("RunSSH") then (.RunSSH|tostring) else "MISSING" end'';

  agos-tailscale-ssh-reassert = pkgs.writeShellApplication {
    name = "agos-tailscale-ssh-reassert";
    runtimeInputs = [ config.services.tailscale.package pkgs.jq ];
    # NOT `-e`: the three exit codes below are the whole point and a wrapper that
    # aborted on the first non-zero would collapse healed (0) into cannot-assess (2).
    bashOptions = [ "nounset" "pipefail" ];
    text = ''
      # WHY THIS UNIT EXISTS. NixOS's `tailscaled-autoconnect` runs
      # `tailscale up ... --ssh` ONLY on its `NeedsLogin|NeedsMachineAuth|Stopped`
      # branch; once the node reports `Running` it prints "Tailscale is running" and
      # exits 0. So `--ssh` is asserted ONCE, at first auth, and never re-enforced. A
      # runtime `tailscale set --ssh=false` — a stray hand, a shared account, a
      # rollback — persists silently until someone tries the door and finds it shut.
      # The mesh's own write path onto this box is the door. That is the
      # scanner-with-no-timer shape: a control that ran once and has no schedule.
      #
      # WHY `.RunSSH` IS A LEGITIMATE ORACLE HERE, having argued the opposite about a
      # status field last week. `RunningSSHServer` in `tailscale status --json` is a
      # STATE REPORT and returns absent on this version — unusable. `.RunSSH` in
      # `tailscale debug prefs` is the PREF, i.e. the very field `tailscale set
      # --ssh=true` writes. Reading it is reading the thing itself, not a proxy for it.
      # This unit's job is to keep the PREF true; whether the server is actually
      # SERVING is a different question that CANNOT be answered from this box — see
      # the peer probe below.
      #
      # THE SERVING QUESTION IS NOT ASKED HERE, DELIBERATELY. tailscaled intercepts
      # :22 only for traffic arriving over the tun from a PEER; a connection this box
      # makes to its own tailnet address routes through the kernel to OpenSSH.
      # Measured 2026-08-31T08:00Z, same host / same port / same second:
      #     box  -> its OWN tailnet addr :22   SSH-2.0-OpenSSH_10.4
      #     peer -> that SAME addr      :22   SSH-2.0-Tailscale
      # A self-probe on the banner is therefore CONSTANT with respect to the thing
      # measured — a non-probe that reads like a behavior test. The serving check lives
      # on the mini (`dell-tailscale-ssh-peer-probe.sh`, wired into the heartbeat), which
      # is the only vantage from which the observation carries information.
      #
      # `tailscale set`, NEVER `tailscale up`. A re-`up` carries the FULL flag set and
      # silently resets every pref not named on that command line; `set` touches one.

      prefs="$(tailscale debug prefs 2>&1)" || {
        echo "agos-tailscale-ssh-reassert: CANNOT-ASSESS — 'tailscale debug prefs' failed: $prefs"
        exit 2
      }

      # `has("RunSSH")`, NOT `.RunSSH // "MISSING"`. jq's alternative operator fires on
      # `false` as well as `null`, so the original spelling mapped RunSSH=false — THE ONE
      # STATE THIS UNIT EXISTS TO FIX — onto the MISSING sentinel and out through the
      # CANNOT-ASSESS arm. Proven on the box 2026-08-31: after a hand `tailscale set
      # --ssh=false`, the timer fired on schedule at 03:49:46 CDT and journalled
      # "CANNOT-ASSESS — .RunSSH was 'MISSING'", exit 2, unit `failed`, pref still false.
      # The healer was reachable only in the branch where there was nothing to heal.
      #
      # The sentinel was for an ABSENT field, and absence is what it could no longer
      # detect: `false` and "not there" arrived at the same string, so the code could not
      # tell "the pref is off" from "I cannot read the pref". Same class as the
      # wrong-vantage non-probe — a discriminator that collapses in the failure state.
      runssh="$(printf '%s' "$prefs" | jq -r ${lib.escapeShellArg runSSHJqProgram} 2>/dev/null)"

      case "$runssh" in
        true)
          # Silent by design (guard b). A unit that logs a line every 10 minutes to say
          # nothing happened trains everyone to filter its name out of the journal, and
          # then the ONE line that matters is filtered too.
          exit 0
          ;;
        false)
          # Guard (e): every heal is journalled, so a pref that flaps becomes VISIBLE
          # rather than silently healed forever. A self-healing control with no record
          # is indistinguishable from a control that was never needed.
          echo "agos-tailscale-ssh-reassert: HEAL — .RunSSH was false; asserting --ssh=true"
          if out="$(tailscale set --ssh=true 2>&1)"; then
            echo "agos-tailscale-ssh-reassert: HEALED — tailscale set --ssh=true returned clean"
            exit 0
          else
            echo "agos-tailscale-ssh-reassert: HEAL-FAILED — $out"
            exit 1
          fi
          ;;
        *)
          # MISSING is not false. A key that vanished from the prefs schema means this
          # unit no longer knows what it is looking at, and guessing "off" would make it
          # write a pref on every run against a tailscale that renamed the field.
          echo "agos-tailscale-ssh-reassert: CANNOT-ASSESS — .RunSSH was '$runssh', not true/false"
          exit 2
          ;;
      esac
    '';
  };
in {
  # Exposed so `flake.nix`'s check can run the EXACT shipped binary against a stub
  # `tailscale`, rather than a re-derived copy of its logic.
  system.build.agosTailscaleSshReassert = agos-tailscale-ssh-reassert;

  environment.systemPackages = [ agos-tailscale-ssh-reassert ];

  systemd.services.agos-tailscale-ssh-reassert = {
    description = "Agent OS — keep the Tailscale SSH pref asserted (it is set once at first auth and never re-enforced)";
    # Needs tailscaled up to have prefs to read at all; without this the boot run is a
    # guaranteed CANNOT-ASSESS every reboot, which is noise that teaches people to ignore it.
    after = [ "tailscaled.service" ];
    wants = [ "tailscaled.service" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${agos-tailscale-ssh-reassert}/bin/agos-tailscale-ssh-reassert";
      User = "root";   # `tailscale set` writes prefs through the daemon's local API
      # Deliberately empty: both 1 (heal failed) and 2 (cannot-assess) must land the
      # unit in `failed`, because `systemctl is-failed` is the entire alarm surface.
      SuccessExitStatus = [ ];
    };
  };

  systemd.timers.agos-tailscale-ssh-reassert = {
    description = "Agent OS — periodic Tailscale SSH pref re-assert";
    wantedBy = [ "timers.target" ];
    # 10min, inside Rabbot's <=15min bound. This also sets the width of the control
    # arm's window: the detector on the mini must be able to SEE the off state, so a
    # hand-flipped --ssh=false has at most this long to be observed before the heal.
    timerConfig = { OnBootSec = "3min"; OnUnitActiveSec = "10min"; Persistent = true; };
  };
}
