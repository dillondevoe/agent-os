# modules/model-open.nix — Phase 2: the brain, BAKED INTO THE IMAGE (OPEN variant).
#
# FOUNDER DIRECTIVE (Dillon 8988, 2026-07-31, relayed rabbot-to-augur-package-model-at-
# install-not-pull): the OS ships WITH its brain. No first-boot CDN pull, no 4.7GB
# Cloudflare download over flaky wifi (the exact 2-day install pain). Boot Agent OS and
# it is alive.
#
# HOW: the qwen3.5:9b weights (UD-Q4_K_XL GGUF, single file, ~5.97GB) are a
# fixed-output derivation — content-hashed, in the Nix closure, therefore IN THE IMAGE
# (Rabbot's "lean reproducible" pick over an installer-staged tarball). At first boot a
# local oneshot imports them into Ollama over the loopback API (`ollama create`) — a
# LOCAL file import, zero network. Idempotent: skips if the tag already exists.
#
# HASH PROVENANCE (why this is pinned without DVo fetching 4.68GB): the SRI hash below is
# the HuggingFace LFS object sha256 for bartowski/Qwen2.5-7B-Instruct-GGUF ::
# Qwen2.5-7B-Instruct-Q4_K_M.gguf (size 4683074240 B, oid 65b8fcd9…1423), read from the
# repo tree metadata (a few KB) and converted with `nix hash convert`. fetchurl verifies
# the flat sha256 of the downloaded file == that oid, so the pin is exact. The 4.68GB
# realization happens on the FETCH-CAPABLE BUILD BOX (mini/Dell), never on the bandwidth-
# capped DVo research node.
#
# OPEN-only-first (same discipline as calendar/desktop): self-contained, imported solely
# from configuration-open.nix, perturbs nothing sealed. SEAL-TIME (Geist, Phase 4): the
# SAME mechanism gives the sovereign variant its Phase-S "no first-boot network" posture —
# fold this in there; do not re-solve it.
{ config, pkgs, lib, ... }:
let
  modelTag = "qwen3.5:9b";

  # --- the weights: fixed-output derivation → in the closure → in the image -----
  # Qwen3.5-9B (Mar 2026) replaces qwen2.5:7b-instruct (two generations stale).
  # BFCL-V4 66.1 vs Qwen3-30B-A3B-Thinking's 42.4 — a 56% relative jump in agentic
  # tool-calling at a third the parameters. TAU2-Bench 79.1. Most intelligent model
  # under 10B as of Aug 2026.
  # Unsloth UD-Q4_K_XL ("Unsloth Dynamic") quantises layers non-uniformly and beats a
  # flat Q4_K_M at ~the same size. Q4_K_M is the FLOOR for reliable tool-calling —
  # do not go below it; sub-Q4 breaks structured tool output.
  gguf = pkgs.fetchurl {
    name = "qwen3.5-9b-ud-q4_k_xl.gguf";
    url = "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-UD-Q4_K_XL.gguf";
    # HF LFS oid 6f5d30666c2d8ae16a306e616d95341dcf3cc46810df84d7e6f5a7d1e4c1b293 (5.97 GB)
    hash = "sha256-b10wZmwtiuFqMG5hbZU0Hc88xGgQ34TX5vWn0eTBspM=";
  };

  # --- the Modelfile: ChatML template + qwen3.5 stops --------------------------
  # Explicit template (not GGUF-metadata auto-detect) so the chat wiring is deterministic
  # across Ollama versions. Qwen3.5 keeps the ChatML <|im_start|>/<|im_end|> framing.
  # NOTE: Qwen3.5 defaults to THINKING mode and is a token hog if left unconstrained —
  # an interactive OS shell must cap reasoning. See PARAMETER num_predict below.
  modelfile = pkgs.writeText "qwen3.5-instruct.modelfile" ''
    FROM ${gguf}
    TEMPLATE """{{ if .System }}<|im_start|>system
    {{ .System }}<|im_end|>
    {{ end }}{{ if .Prompt }}<|im_start|>user
    {{ .Prompt }}<|im_end|>
    {{ end }}<|im_start|>assistant
    {{ .Response }}<|im_end|>
    """
    PARAMETER stop "<|im_start|>"
    PARAMETER stop "<|im_end|>"
    PARAMETER temperature 0.7
    PARAMETER num_predict 2048
  '';
in {
  # First-boot seed: import the bundled weights into Ollama LOCALLY (no network). Runs
  # after ollama.service (declared in configuration-open.nix), waits for the API, then
  # `ollama create ${modelTag}`. Idempotent + RemainAfterExit so it is a one-time boot cost.
  systemd.services.agos-seed-model = {
    description = "Seed ${modelTag} into Ollama from the in-image GGUF (local, no network)";
    wantedBy = [ "multi-user.target" ];
    after = [ "ollama.service" ];
    requires = [ "ollama.service" ];
    path = [ pkgs.ollama ];
    # The ollama CLI evaluates envconfig.AsMap()/Models() at startup and PANICS with
    # "$HOME is not defined" unless HOME (or OLLAMA_MODELS) is set — and a systemd unit
    # inherits neither by default. That panic fired inside the wait-loop AND before the
    # idempotent tag-check reached `ollama create`, so on a FRESH install (weights only
    # in the FOD, seed = the sole import path) the box booted brainless — the founder
    # "boots alive" directive broken (Rabbot P1, 2026-07-31). Mirror the daemon's own env:
    # HOME + the exact model store it reads/writes, so `ollama create` lands the blob where
    # the daemon serves it. Sourced from the services.ollama options (not hardcoded) so it
    # can never drift from whatever the daemon uses.
    environment = {
      OLLAMA_HOST = "127.0.0.1:11434";
      HOME = config.services.ollama.home;               # /var/lib/ollama
      OLLAMA_MODELS = config.services.ollama.modelsDir;  # /var/lib/ollama/models (daemon's store)
    };
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      # Run as the SAME user the ollama daemon runs as (services.ollama.user in
      # configuration-open.nix = "ollama", uid 992), NOT root — a root-run import leaves
      # root-owned blobs the (non-root) daemon can't overwrite on a later `ollama pull`.
      # But same-NAME is not enough: the NixOS ollama module ALWAYS sets DynamicUser=true,
      # so the daemon's store is sandboxed behind /var/lib/private/ollama (0700 root:root)
      # and a bare static-user unit gets no bind-mount → uid 992 can't even traverse it
      # (EACCES on `ollama create`; verified live on the Dell, Rabbot 2026-08-01 — this unit
      # only ever seeded while first-boot uid luck held). Join the daemon's namespace with
      # DynamicUser + StateDirectory=ollama (→ the /var/lib/private/ollama → /var/lib/ollama
      # bind-mount), while KEEPING User/Group pinned so the shared StateDirectory ownership
      # stays uid 992 (a nameless DynamicUser would chown it to a per-unit uid). Writable AND
      # consistent = seed lands + future pulls work.
      User = config.services.ollama.user;
      Group = config.services.ollama.group;
      DynamicUser = true;
      StateDirectory = "ollama";
    };
    script = ''
      # Wait for the loopback Ollama API to answer (daemon just started).
      for _ in $(seq 1 60); do
        if ollama list >/dev/null 2>&1; then break; fi
        sleep 1
      done
      if ollama list 2>/dev/null | grep -q '${modelTag}'; then
        echo "agos-seed-model: ${modelTag} already present — nothing to do."
        exit 0
      fi
      echo "agos-seed-model: importing ${modelTag} from the in-image GGUF (local, no network)…"
      ollama create ${modelTag} -f ${modelfile}
      echo "agos-seed-model: done — the box booted with its brain."
    '';
  };

  # NOTE (disk): the weights exist twice at runtime — once in the Nix store (the image
  # copy, a GC root) and once in Ollama's store after `create` (~+4.68GB). Acceptable for
  # v0 (born-whole > thin). SEAL-TIME optimization: seed Ollama's blob store DIRECTLY from
  # the FOD (symlink/hardlink) to drop the duplicate + the create step entirely.
}
