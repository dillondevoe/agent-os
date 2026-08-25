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
