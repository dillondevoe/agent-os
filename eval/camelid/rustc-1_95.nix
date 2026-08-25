# task 342, claim 1 — THE DISCRIMINATING RUN.
#
# The default build failed inside rustc's own core_arch on nixpkgs' rustc 1.97.1
# (run 32801527610). I reported that as a FAIL with a cause, and reported the version
# hypothesis — "it would build on the 1.95.0 their rust-toolchain.toml pins" — explicitly
# as an INFERENCE, because no build against a ~1.95 compiler had been run.
#
# Page then built the same rev green on the mini with rustc 1.95.0 and, correctly, refused
# to let that count: the mini is aarch64-darwin, and the failing function is gated
# `target_os = "linux", target_arch = "x86_64"`, so `_mm512_dpbusd_epi32` was never
# codegen'd there at all. Their green and my red were never compiling the same code. Two
# arms that were not measuring the same thing cannot agree.
#
# This file is the pair done properly: SAME expression, SAME rev, SAME x86_64-linux, and
# exactly one variable moved — the compiler. nixpkgs is pinned to the nixos-25.11 channel
# revision, which carries `rustPackages_1_95` at rustcVersion "1.95.0", which is upstream's
# declared channel to the patch.
#
# It discriminates in both directions, which is the only reason it is worth running:
#   green -> the failure is the toolchain, and Camelid builds under Nix once pinned.
#   red   -> the version hypothesis is WRONG and I must retract it, not soften it.
let
  # nixos-25.11 channel revision, resolved 2026-08-25 from channels.nixos.org/nixos-25.11.
  rev = "b6018f87da91d19d0ab4cf979885689b469cdd41";
  pkgs = import (builtins.fetchTarball {
    url = "https://github.com/NixOS/nixpkgs/archive/${rev}.tar.gz";
  }) { };
  rustPlatform = pkgs.makeRustPlatform {
    inherit (pkgs.rustPackages_1_95) cargo rustc;
  };
in
# cargoHash left at default.nix's value for now. If THIS nixpkgs vendors differently, run 1
# of this file reports its own `got:` and it gets pinned here — a hash mismatch under a
# different nixpkgs is a fact about nixpkgs, not a finding about Camelid.
import ./default.nix { inherit pkgs rustPlatform; }
