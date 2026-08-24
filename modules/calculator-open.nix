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
  # The agent's calculator hand, now a standalone package so a cheap runCommand can put it on
  # PATH for tests/agos-calc-battery.py. writeShellApplication still runs shellcheck at build
  # time and still pins `qalc` into runtimeInputs — moving the file changed neither.
  agos-calc = import ./pkgs/agos-calc.nix { inherit pkgs; };
in {
  environment.systemPackages = with pkgs; [
    agos-calc        # the agent's calculator hand (JSON: {input,result,ok,messages})
    qalculate-gtk    # human-facing Qalculate! GUI (appears in wofi drun automatically)
  ];
}
