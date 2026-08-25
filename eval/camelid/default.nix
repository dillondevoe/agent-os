# task 342, claim 1 — does github.com/timtoole02/Camelid BUILD UNDER NIX FROM SOURCE,
# with nixpkgs + its own code only, no curl-pipe installer and no non-nixpkgs runtime?
#
# THIS IS AN EVALUATION ARTEFACT, NOT AN ADOPTION. Nothing here is referenced by
# flake.nix, no agent-os module imports it, and it must not be merged to `main`
# (Rabbot's boundary, verbatim: "Do NOT replace ollama anywhere. Do NOT merge anything
# into agent-os `main`. Branch + report.").
#
# The commit is PINNED. It was captured with `git ls-remote` at 2026-08-25T01:41Z, BEFORE
# anything was fetched, so the thing evaluated is the thing named in the report — a moving
# `HEAD` would make every number below unattributable to any particular tree.
#
# WHY THIS RUNS IN CI AND NOT ON DVo: DVo has no `nix` binary and no rust toolchain
# (measured tick 550, not assumed). CI is already this repo's only .nix verifier.
#
# cargoHash IS DELIBERATELY EMPTY ON THE FIRST RUN. There is no way to compute a vendor
# hash on a box with no nix, and inventing one would be a fabricated measurement. The first
# CI run is EXPECTED to fail with `hash mismatch ... got: sha256-...`; that `got:` value is
# the measurement, and it gets pinned here in a second commit. A red first run is the
# instrument working, not the claim failing — the workflow says so in its own output.
{ pkgs ? import <nixpkgs> { } }:

let
  rev = "bfd5e02698165754fd2491dc45ae8223e6fbba29";
in
pkgs.rustPlatform.buildRustPackage {
  pname = "camelid-eval-342";
  version = "0.6.1-${builtins.substring 0 12 rev}";

  src = pkgs.fetchFromGitHub {
    owner = "timtoole02";
    repo = "Camelid";
    inherit rev;
    # MEASURED by run 1 (32799282259): nix reported
    #   got: sha256-fqLglkSQ5MrXadcsENn01nIV03L24+6pgkYUssQ/ld4=
    # for a 105.4 MB source tarball. Pinned from nix's own output, not computed by hand.
    hash = "sha256-fqLglkSQ5MrXadcsENn01nIV03L24+6pgkYUssQ/ld4=";
  };

  # MEASURED by run 2 (32799807646), transcribed from nix's own `got:` line.
  #
  # NOTE WHAT THE VENDOR STAGE FETCHED: tao, windows-core, windows-future, system-deps.
  # Those are `camelid-desktop`'s (Tauri) dependencies. `default-members = ["."]` scopes
  # what BUILDS, but cargo vendors from the WORKSPACE lockfile, so the desktop member's
  # crates are downloaded regardless. It inflates the vendor derivation; it does not enter
  # the server binary's runtime closure. Recorded rather than worked around — the runtime
  # closure printed by the verdict step is what settles the clean-room question.
  cargoHash = "sha256-Gr9fT4H2TYbqfQlDqv4Lvr93RCnt40S1VlSYGK9xtYM=";

  # Server binary only. `--bin camelid` is upstream's own CI scope; the camelid-desktop
  # workspace member is a Tauri app and is NOT part of the claim being tested.
  cargoBuildFlags = [ "--bin" "camelid" ];

  # Upstream pins `channel = "1.95.0"` in rust-toolchain.toml. Under Nix there is no rustup,
  # so that file is INERT and this builds with whatever rustc nixpkgs carries. That is a
  # finding for the report, not a workaround: their declared toolchain is not the one this
  # closure would ship, and `rust-version = "1.89"` in Cargo.toml is the only floor that
  # actually binds. If nixpkgs' rustc is below 1.89 this fails loudly, which is correct.
  nativeBuildInputs = with pkgs; [
    # rusqlite is `features = ["bundled"]` -> compiles a VENDORED C sqlite via cc.
    # rustls is `default-features = false, features = ["aws-lc-rs"]` -> aws-lc needs cmake
    # (and a C toolchain) at build time. Both are build-time only and both are VENDORED C
    # rather than nixpkgs' sqlite/openssl — record that against the clean-room list rather
    # than silently satisfying it.
    cmake
    perl
    pkg-config
  ];

  # No network in the sandbox; upstream's tests are not the claim.
  doCheck = false;

  meta = {
    description = "task-342 claim-1 probe: Camelid built from source under Nix, pinned";
    homepage = "https://github.com/timtoole02/Camelid";
    license = pkgs.lib.licenses.mit;
  };
}
