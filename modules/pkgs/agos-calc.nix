# modules/pkgs/agos-calc.nix — the agent's calculator hand, as a PACKAGE.
#
# EXTRACTED FROM calculator-open.nix 2026-08-24, and the reason is a testing one.
#
# This derivation was a `let` binding inside the NixOS module. A let binding in a module is
# unreachable from outside it: there is no expression anywhere else in the repo that can name
# `agos-calc`, so nothing could put it on a PATH except by evaluating the whole module. That is
# why tests/agos-calc-battery.py sat on KNOWN_UNWIRED_DEBT self-disarming — it SKIPs rc=0 when
# `agos-calc` is not on PATH, and no lane could ever supply it.
#
# The alternative was a nixosTest importing calculator-open.nix, and that is the expensive
# answer for a bad reason: the module also installs qalculate-gtk for the human. Every one of
# the eight ambient modules welds a ~50-line agent CLI to a GUI (firefox, gnome-calendar,
# thunar, zathura, imv+mpv, apostrophe, qalculate-gtk), so testing the CLI that way means
# building a desktop image to exercise a shell script.
#
# THE HAND AND THE GUI ARE SEPARATE THINGS AND ONLY ONE OF THEM IS A CONTRACT. Splitting the
# package out is what makes the battery cheap to run — a runCommand with this in
# nativeBuildInputs, no VM — and the module below is unchanged in what it installs.
{ pkgs }:
pkgs.writeShellApplication {
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
        # An EMPTY expression is "no expression given" in substance, and until 2026-08-24 it
        # took a different path: `agos-calc eval ""` is one argument, so the $# guard above
        # never fired, the empty string went to qalc, and the hand answered rc=0. Found the
        # first time tests/agos-calc-battery.py ever executed anywhere — the battery had been
        # asserting exit 2 here since it was written, and nothing had ever run the assertion.
        if [ -z "''${expr//[[:space:]]/}" ]; then
          echo "agos-calc eval: empty expression" >&2
          exit 2
        fi
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
}
