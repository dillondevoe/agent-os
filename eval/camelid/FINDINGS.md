# task 342 — Camelid eval, claim 1: **FAIL**, with a precise cause

Pinned rev `bfd5e02698165754fd2491dc45ae8223e6fbba29` (captured with `git ls-remote`
before anything was fetched). Three CI runs, each answering one thing.

| run | id | what it settled |
|---|---|---|
| 1 | 32799282259 | source hash `sha256-fqLglkSQ5MrXadcsENn01nIV03L24+6pgkYUssQ/ld4=`, 105.4 MB archive; fetches and unpacks **inside the sandbox** |
| 2 | 32799807646 | vendor hash `sha256-Gr9fT4H2TYbqfQlDqv4Lvr93RCnt40S1VlSYGK9xtYM=`; whole workspace vendors, desktop member included |
| 3 | 32801527610 | **compile failure**, below |

## The failure

```
error: intrinsic signature mismatch for `llvm.x86.avx512.vpdpbusd.512`:
       expected signature `<16 x i32> (<16 x i32>, <16 x i32>, <16 x i32>)`,
       found `<16 x i32> (<16 x i32>, <64 x i8>, <64 x i8>)`
  --> library/core/src/../../stdarch/crates/core_arch/src/x86/avx512vnni.rs:892:4
error: could not compile `camelid` (lib) due to 1 previous error
```

**Read the path before assigning blame.** The mismatch is inside **rustc's own
`core_arch`**, not in Camelid's source. nixpkgs' `rustc 1.97.1` ships a stdarch whose
declaration of `_mm512_dpbusd_epi32` disagrees with the LLVM it is built against. The
error fires when the intrinsic is *codegen'd* — and Camelid codegens it:

`src/inference.rs:15615` and `:15842`, inside

```rust
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
#[target_feature(enable = "avx512f,avx512bw,avx512vnni")]
unsafe fn q8_0_vnni_tile16_i32_avx512(...)
```

That gate is `target_os`/`target_arch` only. It is **not** behind a cargo feature, so
every Linux x86_64 build compiles it — there is no supported way to opt out.

## Why this is the toolchain finding, not a footnote

`rust-toolchain.toml` pins `channel = "1.95.0"`. Under Nix there is no rustup, so that
file is **inert** and nixpkgs' rustc is used instead. Upstream CI, using rustup, never
sees the compiler this fails on. The pin that looked like a footnote in the first reading
of the tree is the thing standing between their green CI and this red one.

## What is NOT established

- **That the failure is version-specific.** It is strongly attributable to rustc 1.97.1's
  stdarch/LLVM pairing, and `#[allow(clippy::incompatible_msrv)]` sits directly above the
  function, so upstream knows this code is MSRV-sensitive. But no build against a rustc
  ≈1.95 was run here, so "it would build on their pinned toolchain" is an inference, not
  a measurement. The one experiment that settles it: pin a nixpkgs rev carrying rustc
  1.95.x and re-run this same expression, changing nothing else.
- **Anything about the clean-room list.** No closure was produced, so the question
  Rabbot actually asked — does any *runtime* dep violate it — has no answer. What is
  known is build-time only: `rusqlite` `bundled` and `rustls`/`aws-lc-rs` compile vendored
  C, and the web UI needs npm at build time (`build.rs` writes a placeholder otherwise).
- **Claims 2-5.** Untouched. Per Rabbot: "If claim 1 fails, stop there and report."

## Verdict as stated for the report

Claim 1 **FAILS as literally asked** — it does not build under Nix from source with
nixpkgs' toolchain. It does **not** fail for the reason the claim was designed to catch
("needs a non-nixpkgs runtime"). Those are different findings and collapsing them into
one word would mislead whoever decides next.

---

# UPDATE — run 32804388753, the controlled pair. The version hypothesis is CONFIRMED,
# and the clean-room question finally has an answer.

Both arms in ONE run, ONE nix expression, exactly one variable moved (`rustPlatform`;
`cargoHash` was left free to move too and did not need to). Page's rule applied:
two arms that were never measuring the same thing cannot agree — so these two were made
to measure the same thing.

| arm | rustc | nixpkgs | result |
|---|---|---|---|
| `default.nix` | **1.97.1** (nixpkgs default) | flake registry default | **RED** — `core_arch/src/x86/avx512vnni.rs:892:4`, builder exit 101 |
| `rustc-1_95.nix` | **1.95.0** (`rustPackages_1_95`, nixpkgs `b6018f87`) | pinned 25.11 | **GREEN** — `camelid 0.6.1` runs |

`cargoHash` did not change between arms — the vendor derivation is identical across both
nixpkgs revisions, which is itself a small fact worth having: the two arms vendored the
same tree.

## Finding 5 — the failure is TOOLCHAIN-SPECIFIC, not a Camelid defect

Retracting nothing; the earlier reading holds and is now measured rather than inferred.
The intrinsic import at `src/inference.rs:15601/15823` compiles cleanly on the toolchain
upstream declares in `rust-toolchain.toml` (`channel = "1.95.0"`). The breakage is in
rustc 1.97.1's own `stdarch`, on a path Camelid merely calls. `#[allow(clippy::incompatible_msrv)]`
above the function shows upstream is aware the code is MSRV-sensitive.

**Consequence for claim 1:** the failure is a *pinning* problem, not a *portability* one.
Nix is the tool that fixes exactly this — `makeRustPlatform { inherit (pkgs.rustPackages_1_95) cargo rustc; }`
is four lines and it is already written in this directory.

## Finding 6 — the runtime closure. This is the clean-room evidence.

`nix path-info -Sh ./result-1_95` -> **72.9 MiB total closure.** Seven paths, in full:

    camelid-eval-342-0.6.1-bfd5e0269816
    gcc-14.3.0-lib
    gcc-14.3.0-libgcc
    glibc-2.40-224
    libidn2-2.3.8
    libunistring-1.4.1
    xgcc-14.3.0-libgcc

**No Python. No Node. No Docker. No non-nixpkgs runtime anything.** Every path is stock
nixpkgs stdenv. The engine's "single binary, no runtime deps" claim is TRUE as measured,
not as pitched. `./result-1_95/bin/camelid --version` printed `camelid 0.6.1` inside CI.

Page's narrowing stands and composes: on aarch64-darwin the same rev builds from source
with the stock toolchain and zero npm, because `build.rs::ensure_web_ui_placeholder()`
exists so the Rust build never needs the frontend. For a **headless/API-only** deployment
npm is not a build dep either. The npm requirement is scoped to the real web UI alone.

## Revised verdict for the report

Claim 1 = **PASS, with a pin.** It builds under Nix from source, sandboxed, no installer,
closure 72.9 MiB, and **no runtime dep violates the clean-room list**. The condition is
that the toolchain must be pinned to upstream's declared 1.95.0 rather than taken from
nixpkgs default — which is a normal Nix packaging line, not a clean-room violation.

The earlier "FAILS as literally asked" line is **superseded, not softened**: it was correct
about the default-toolchain arm and was reported with its inference labelled as an inference.
The experiment named there as the thing that would settle it has now been run, and it settled
it in the other direction.

## What is STILL not established

- **Claims 2-5 by Mirror.** Claim 5 is BLOCKED on DVo by box facts (no CUDA device, no
  ollama, 7 GB RAM). Page owns 2-5 on mini and their verdicts are theirs to report.
- **Anything about runtime behaviour of the binary beyond `--version`.** No model was
  loaded here. Closure size is a packaging fact, not a performance one.
