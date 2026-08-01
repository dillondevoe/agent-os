# modules/gaming-open.nix — Agent OS gaming lane (OPEN variant ONLY).
#
# WHY THIS IS A CONFIG CHANGE, NOT AN INSTALL: on NixOS Steam is a declarative service,
# not `apt install steam` — /usr is immutable and Steam needs an FHS runtime + udev
# rules that only `programs.steam.enable` wires up. Dillon repeatedly asked the
# agent-brain to "install steam" and it kept failing (msgs incl. 9113) because no brain
# can shortcut a missing config declaration — the OS itself has to enable it (Rabbot
# gaming-lane ask, 2026-08-01). Gaming is explicit in the OS thesis (the Linux/speed/
# gaming/ops specialist), so make it actually work.
#
# OPEN-only + self-contained (same isolation doctrine as calendar/desktop/model): imported
# solely from configuration-open.nix, shares nothing with the sealed sovereign config —
# the sovereign box has no business running Steam. Folds into a shared substrate at
# seal-time only if a sealed variant ever wants it (it won't).
#
# After deploy, the agent-brain's correct answer to "install steam" becomes "it's already
# enabled — run `steam`" (the OBSERVED-FAILURE-MODES training example encodes the
# pre-enable behavior; this module is what makes the post-enable answer true).

{ lib, ... }:

{
  # FHS runtime + controller udev rules + the steam/steamcmd wrappers. remotePlay/dedicated
  # firewall openings and the gamescope session are deliberately NOT enabled here — no
  # overreach; add them behind a real ask if Dillon wants Remote Play / a big-picture TTY.
  programs.steam.enable = true;

  # Steam and Proton render through 32-bit GL; the 64-bit iGPU stack already enabled in
  # configuration-open.nix (hardware.graphics.enable = true, Iris Xe) is not sufficient on
  # its own. This adds the 32-bit driver path Proton titles need.
  hardware.graphics.enable32Bit = true;

  # Steam is unfree. Whitelist ONLY the Steam package set via a predicate (tighter than a
  # blanket nixpkgs.config.allowUnfree = true) so the unfree surface stays minimal — the
  # explicit Rabbot preference. getName strips the version; the set covers the wrapper, the
  # unwrapped app, the FHS run shim, and steamcmd across nixpkgs renames.
  nixpkgs.config.allowUnfreePredicate = pkg: builtins.elem (lib.getName pkg) [
    "steam"
    "steam-original"
    "steam-unwrapped"
    "steam-run"
    "steamcmd"
  ];
}
