# modules/model-open.nix — Phase 2: the brain, BAKED INTO THE IMAGE (OPEN variant).
#
# FOUNDER DIRECTIVE (Dillon 8988, 2026-07-31, relayed rabbot-to-augur-package-model-at-
# install-not-pull): the OS ships WITH its brain. No first-boot CDN pull, no 4.7GB
# Cloudflare download over flaky wifi (the exact 2-day install pain). Boot Agent OS and
# it is alive.
#
# HOW: the qwen2.5:7b-instruct weights (Q4_K_M GGUF, single file, ~4.68GB) are a
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
  modelTag = "qwen2.5:7b-instruct";

  # --- the weights: fixed-output derivation → in the closure → in the image -----
  gguf = pkgs.fetchurl {
    name = "qwen2.5-7b-instruct-q4_k_m.gguf";
    url = "https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/Qwen2.5-7B-Instruct-Q4_K_M.gguf";
    # HF LFS oid 65b8fcd92af6b4fefa935c625d1ac27ea29dcb6ee14589c55a8f115ceaaa1423
    hash = "sha256-Zbj82Sr2tP76k1xiXRrCfqKdy27hRYnFWo8RXOqqFCM=";
  };

  # --- the Modelfile: ChatML template + qwen2.5 stops --------------------------
  # Explicit template (not GGUF-metadata auto-detect) so the chat wiring is deterministic
  # across Ollama versions. Tool-call template is a follow-up if agent-brain wants native
  # tools; agent-brain drives via its own protocol regardless.
  modelfile = pkgs.writeText "qwen2.5-instruct.modelfile" ''
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
