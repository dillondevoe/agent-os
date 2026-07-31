# modules/calculator-open.nix — Phase 2: the calculator (OPEN variant).
#
# The roadmap's "calculator" ambient app, built to its acceptance bar ("launches, the
# agent can read+drive it, tiles cleanly, no red config errors"): the Qalculate! GUI
# (qalculate-gtk) for the human at the Waybar/Hyprland surface, plus a thin agent-facing
# CLI hand — `agos-calc` — that evaluates an expression and returns JSON, so the
# agent-brain can wrap it 1:1 over the box's real math backend (libqalculate's `qalc`).
#
# Same shape as calendar-open (agos-cal) and settings-open (agos-sys): a JSON-contract
# shell hand + a human GUI, OPEN-only, self-contained, imported solely from
# configuration-open.nix. It shares nothing with the sealed sovereign path — fold into a
# shared substrate module at seal-time (same follow-up as calendar/desktop/settings).
#
# The GUI needs no hyprland.conf edit to be launchable: qalculate-gtk ships a .desktop
# file, so `wofi --show drun` ($mod+R, from desktop-open.nix) finds it and Hyprland tiles
# it like any other window — the "tiles cleanly" half of the acceptance bar.
{ pkgs, ... }:
let
  # The agent's calculator hand. writeShellApplication runs shellcheck at build time
  # (real acceptance) and pins `qalc` into runtimeInputs — no runtime PATH dependence.
  agos-calc = pkgs.writeShellApplication {
    name = "agos-calc";
    runtimeInputs = with pkgs; [ coreutils gnugrep gnused jq libqalculate ]; # libqalculate → `qalc`
    text = ''
      # Strict mode is on (writeShellApplication). `qalc` is libqalculate's CLI; -t is
      # terse (value only, no "expr = " echo). The expression is passed as ARGS (never on
      # stdin) so qalc evaluates one-shot and can never block on an interactive prompt.

      usage() {
        echo "usage: agos-calc eval <expression…>" >&2
        echo "   e.g. agos-calc eval '(2+3)*4'   |   agos-calc eval 'sqrt(2)'   |   agos-calc eval '200 * 15%'" >&2
        exit 2
      }

      cmd_eval() {
        if [ "$#" -eq 0 ]; then
          echo "agos-calc eval: no expression given" >&2
          exit 2
        fi
        expr="$*"
        # qalc is permissive and its exit code alone can't separate a clean eval from a
        # unit/variable coercion (e.g. "zzz" -> z³). It reports validity via diagnostics
        # that behave differently by mode: TERSE (-t) prints ONLY the value — clean, and it
        # picks "=" vs "≈" for us — but SUPPRESSES every message; NON-terse prints any
        # "error:"/"warning:" line (to stdout) ahead of the echoed result. So run BOTH:
        # terse for the result value, non-terse for the messages + the error signal. Always
        # emit JSON — a qalc non-zero exit is DATA (ok:false), never a reason to bail.
        result=$(qalc -t "$expr" 2>/dev/null) || true
        # Capture qalc's non-terse exit INSIDE `if` — a bare `full=$(...)` assignment would
        # trip `set -e` and abort (no JSON) whenever qalc exits non-zero (e.g. a partial
        # parse), which is exactly the ok:false case we must still REPORT, not die on.
        if full=$(qalc "$expr" 2>/dev/null); then frc=0; else frc=$?; fi
        messages=$(printf '%s\n' "$full" | grep -iE '^(error|warning):' | tr '\n' ' ' | sed 's/  */ /g; s/ *$//') || true
        # ok iff qalc exited clean, returned a value, AND raised no ERROR-level diagnostic.
        # A warning (division by zero, empty-parens→0) is surfaced in `messages` but leaves
        # ok=true — it is qalc's evaluated answer, not a parse failure.
        if [ "$frc" -eq 0 ] && [ -n "$result" ] && ! printf '%s\n' "$full" | grep -qiE '^error:'; then
          ok=true
        else
          ok=false
        fi
        jq -n \
          --arg input "$expr" \
          --arg result "$result" \
          --arg messages "$messages" \
          --argjson ok "$ok" \
          '{ input: $input, result: $result, ok: $ok,
             messages: (if $messages == "" then null else $messages end) }'
      }

      case "''${1:-}" in
        eval) shift; cmd_eval "$@" ;;
        *)    usage ;;
      esac
    '';
  };
in {
  environment.systemPackages = with pkgs; [
    agos-calc        # the agent's calculator hand (JSON: {input,result,ok,messages})
    qalculate-gtk    # human-facing Qalculate! GUI (appears in wofi drun automatically)
  ];
}
