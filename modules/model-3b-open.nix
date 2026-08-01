# modules/model-3b-open.nix — SECOND brain, BAKED INTO THE IMAGE (OPEN variant).
#
# Companion to modules/model-open.nix. That module bakes the DEFAULT 7B
# (qwen2.5:7b-instruct) the agent serves. THIS module bakes an ADDITIONAL, NON-DEFAULT
# 3B — the Augur "switchboard" run-6 fine-tune — under a DISTINCT Ollama tag so both
# models are resident and selectable. It does NOT change agent-brain's default model:
# a default-swap is a separate, Dillon-gated change (roadmap: "no default-swap till nod").
#
# WHY A HAND-PINNED FOD (not fetchurl like the 7B, not pkgs.requireFile): the 7B is a public
# HuggingFace artifact, so model-open.nix pins it by URL+hash and the fetch-capable build box
# (mini/Dell) downloads it at build. The 3B is a LOCAL fine-tune (merged run-6 weights,
# converted+quantized on the DVo research node) — it lives on no CDN, so there is nothing to
# fetch; it must be STAGED out-of-band. `pkgs.requireFile` is the usual tool for that, but it
# stamps its derivation `meta.license = unfree` by conservative default, which then trips
# checkMeta and would demand this artifact be added to the gaming lane's allowUnfreePredicate
# allowlist — a broader, shared surface than this baked model warrants. Instead we pin the file
# as a PLAIN fixed-output derivation: its output path is fully determined by (name, flat sha256),
# byte-for-byte what `nix-store --add-fixed sha256 qwen2.5-3b-augur-q4_k_m.gguf` produces, so a
# matching staged file makes the path already-valid and nix never runs the builder (exactly
# requireFile's short-circuit). If it is NOT staged, the builder runs and fails loud with the
# staging command. This carries NO license meta — the SAME treatment the 7B's fetchurl FOD gets
# (fetchurl FODs aren't unfree-gated either), keeping the two baked models consistent and this
# module SELF-CONTAINED. Provisioning step (build box, before rebuild):
#     nix-store --add-fixed sha256 qwen2.5-3b-augur-q4_k_m.gguf
# (the store path is derived from name+sha256, so a matching file added anywhere satisfies it).
#
# OPEN-only-first (same discipline as model-open.nix): self-contained, imported solely from
# configuration-open.nix, perturbs nothing sealed. Additive + non-default → no seal/wall/
# egress/genesis/default surface touched.
{ config, pkgs, lib, ... }:
let
  modelTag3b = "qwen2.5:3b-augur";

  # --- the weights: hash-pinned FOD, staged out-of-band into the store (see header) -----
  # sha256 is the flat sha256 of qwen2.5-3b-augur-q4_k_m.gguf (llama-quantize Q4_K_M of the
  # f16 GGUF converted from the merged run-6 safetensors). The output path is identical to
  # `nix-store --add-fixed sha256 qwen2.5-3b-augur-q4_k_m.gguf`, so staging that exact file
  # short-circuits the build; if unstaged, the builder prints the staging command and fails.
  gguf3b = pkgs.stdenvNoCC.mkDerivation {
    name = "qwen2.5-3b-augur-q4_k_m.gguf";
    outputHashMode = "flat";
    outputHashAlgo = "sha256";
    outputHash = "4f43d129caa48c4e2c590465ed232fc2b777aedb79bd431518a47ac8c1e15bf0";
    preferLocalBuild = true;
    builder = pkgs.writeShellScript "require-3b-gguf-message" ''
      echo "The 3B Augur switchboard GGUF is not staged in this Nix store. On the build box run:" >&2
      echo "  nix-store --add-fixed sha256 /path/to/qwen2.5-3b-augur-q4_k_m.gguf" >&2
      echo "(source: DVo ~/dvo-local-log/3b-gguf/qwen2.5-3b-augur-q4_k_m.gguf; mini can scp it from dlux.)" >&2
      exit 1
    '';
  };

  # --- the Modelfile: same ChatML template as the 7B (Qwen2.5 family) --------------
  modelfile3b = pkgs.writeText "qwen2.5-3b-augur.modelfile" ''
    FROM ${gguf3b}
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
  # First-boot seed: import the bundled 3B into Ollama LOCALLY (no network). Mirrors
  # agos-seed-model exactly (same env fix for the ollama-CLI $HOME panic, same user +
  # DynamicUser/StateDirectory namespace so it can WRITE the daemon's store). Ordered AFTER
  # agos-seed-model so the DEFAULT 7B seeds
  # first and the two `ollama create`s never race on a fresh boot. Idempotent + RemainAfterExit.
  systemd.services.agos-seed-model-3b = {
    description = "Seed ${modelTag3b} (non-default) into Ollama from the in-image GGUF (local, no network)";
    wantedBy = [ "multi-user.target" ];
    after = [ "ollama.service" "agos-seed-model.service" ];
    requires = [ "ollama.service" ];
    path = [ pkgs.ollama ];
    environment = {
      OLLAMA_HOST = "127.0.0.1:11434";
      HOME = config.services.ollama.home;               # /var/lib/ollama
      OLLAMA_MODELS = config.services.ollama.modelsDir;  # daemon's store
    };
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      # The daemon's store is NOT writable by a bare static-user unit. The NixOS ollama
      # module ALWAYS sets DynamicUser=true (even with services.ollama.user pinned), so the
      # real state lives at /var/lib/private/ollama and /var/lib/private is 0700 root:root —
      # a unit without the DynamicUser sandbox gets no bind-mount and uid 992 can't traverse
      # it (EACCES on `ollama create`; verified live on the Dell, Rabbot 2026-08-01). Enter
      # the SAME namespace: DynamicUser + StateDirectory=ollama gives this unit the
      # /var/lib/private/ollama → /var/lib/ollama bind-mount the daemon has. User/Group are
      # KEPT pinned to services.ollama.user ("ollama", the static uid-992 name) — WITHOUT it,
      # DynamicUser would allocate a fresh uid for THIS unit's name and chown the shared
      # StateDirectory away from the daemon (re-poisoning ownership). Name pinned + sandbox
      # joined = writable AND consistent.
      User = config.services.ollama.user;
      Group = config.services.ollama.group;
      DynamicUser = true;
      StateDirectory = "ollama";
    };
    script = ''
      for _ in $(seq 1 60); do
        if ollama list >/dev/null 2>&1; then break; fi
        sleep 1
      done
      if ollama list 2>/dev/null | grep -q '${modelTag3b}'; then
        echo "agos-seed-model-3b: ${modelTag3b} already present — nothing to do."
        exit 0
      fi
      echo "agos-seed-model-3b: importing ${modelTag3b} from the in-image GGUF (local, no network)…"
      ollama create ${modelTag3b} -f ${modelfile3b}
      echo "agos-seed-model-3b: done — second brain resident (non-default)."
    '';
  };
}
