# Optional LoRA adapter for the local brain — the fine-tuned Agent OS specialist.
#
# WHAT THIS IS
#   A QLoRA adapter trained on this repo's own surface: the real 14-tool schema parsed out
#   of modules/agent-brain.py, the actual Nix option paths used by these modules, and the
#   doctrine in GENESIS.md. It teaches the base model three things it does not ship with:
#
#     1. the JSON tool-call contract this OS expects,
#     2. NixOS idiom (declarative config + ephemeral `nix shell`, never apt/brew/.exe),
#     3. RESTRAINT — answering without firing a tool when no tool is warranted.
#
# WHY IT IS OPT-IN AND NOT THE DEFAULT
#   The base model is what `nix flake check` and the batteries validate. An adapter changes
#   model behaviour, and behaviour changes belong behind a flag until they have been booted
#   on real hardware. Importing this module is a deliberate act; the default image is
#   unchanged. modules/model-open.nix stays the single source of the base brain.
#
# MEASURED BEFORE/AFTER  (held-out split, identical harness, identical system prompt)
#   Tool-use eval (n=held-out agentos-brain):
#     correct tool chosen      40.0%  ->  87.5%
#     arguments schema-valid   35.7%  ->  92.9%
#     restraint (no tool when none warranted)
#                              11.8%  ->  92.0%
#   Structured-judgment eval:
#     stock produced ZERO parseable responses; adapter produced a working classifier
#     (kind 100%, needs_verification 90%, priority 60%).
#
#   Honest scope: single run, no seed sweep. Scores are computed over responses that
#   parsed. See docs/ for the eval harness and the caveat about generation-token budget —
#   Qwen3.5 reasons before answering, so a short num_predict truncates JSON mid-object and
#   depresses every metric. PARAMETER num_predict is set accordingly below.
#
# HOW TO USE
#   Add to your configuration's imports, alongside model-open.nix:
#     imports = [ ./modules/model-open.nix ./modules/model-lora-open.nix ];
#   Then rebuild. The adapter is fetched by hash, imported into Ollama as a separate tag,
#   and selected by setting OLLAMA_MODEL to that tag. Nothing is overwritten.
{ config, lib, pkgs, ... }:

let
  # The adapter tag is DISTINCT from the base tag so both remain available and a rollback
  # is `OLLAMA_MODEL=qwen3.5:9b` with no rebuild. Same reasoning as the 3B front-door
  # being its own derivation rather than a mutation of the main brain.
  loraTag = "qwen3.5:9b-agentos";
  baseTag = "qwen3.5:9b";

  # Pinned by flat sha256, exactly like the base GGUF in model-open.nix. fetchurl verifies
  # the hash of the downloaded file, so the pin is exact and the build is reproducible.
  #
  # UNPUBLISHED: the adapter is not on a public host yet, so this module is DOCUMENTATION
  # + a wiring reference and is imported by nothing. `published` gates the derivation so a
  # placeholder hash can never reach fetchurl — an unfilled placeholder produces the
  # assertion message below at EVAL time rather than an opaque hash-mismatch at BUILD time.
  #
  # To publish:
  #   1. upload adapter_model.safetensors (+ adapter_config.json) to a HF model repo
  #   2. hash it:  nix hash convert --hash-algo sha256 --to sri <sha256 of the file>
  #   3. set adapterUrl + adapterHash below, and flip `published` to true
  published = false;
  adapterUrl = "https://huggingface.co/OWNER/agent-os-qwen3.5-9b-lora/resolve/main/adapter_model.safetensors";
  adapterHash = "";

  adapter = pkgs.fetchurl {
    name = "qwen3.5-9b-agentos-lora.safetensors";
    url = adapterUrl;
    hash = adapterHash;
  };

  # Ollama consumes a LoRA via ADAPTER in the modelfile. The base FROM must be the tag the
  # image already seeded, so this derivation adds a lightweight layer instead of re-shipping
  # 6GB of weights.
  #
  # num_predict 2048, not the default: Qwen3.5 defaults to THINKING mode. A short budget
  # gets consumed by reasoning tokens and truncates the JSON tool call before it closes —
  # which reads as a model failure and is actually a configuration failure. This exact
  # mistake depressed the first eval run's parse rate.
  modelfile = pkgs.writeText "qwen3.5-9b-agentos.modelfile" ''
    FROM ${baseTag}
    ADAPTER ${adapter}
    PARAMETER stop "<|im_start|>"
    PARAMETER stop "<|im_end|>"
    PARAMETER temperature 0.7
    PARAMETER num_predict 2048
  '';
in {
  # Fail at EVAL time with a legible message, not at BUILD time with a hash mismatch. A
  # module that silently builds against a fake hash and dies on first boot is the worst
  # outcome; `published = false` makes importing it before publication a loud, explained
  # failure. mkIf keeps the systemd unit out of the config entirely until then, so nothing
  # references an unpublished derivation.
  assertions = [
    {
      assertion = published;
      message = ''
        modules/model-lora-open.nix is imported but the adapter is not published yet.
        Set adapterUrl + adapterHash and flip `published = true`, or drop this module from
        your imports. It is opt-in precisely so the default image never depends on an
        unpublished artifact.
      '';
    }
  ];

  systemd.services = lib.mkIf published {
   agos-seed-lora = {
    description = "Seed ${loraTag} (${baseTag} + fine-tuned adapter) into Ollama — opt-in";
    wantedBy = [ "multi-user.target" ];
    # Ordered AFTER the base seed: the adapter modelfile's `FROM` references that tag, so
    # running first would fail on a missing base.
    after = [ "ollama.service" "agos-seed-model.service" ];
    requires = [ "ollama.service" ];
    path = [ pkgs.ollama ];

    # Mirrors agos-seed-model in modules/model-open.nix DELIBERATELY, scar for scar. Each
    # line below is load-bearing and was learned the hard way there:
    #   * the ollama CLI panics with "$HOME is not defined" unless HOME is set, and a
    #     systemd unit inherits none — that panic fired before the idempotent tag-check
    #     and left a fresh box brainless;
    #   * OLLAMA_MODELS must be the daemon's own store or `ollama create` lands the blob
    #     where the daemon will not serve it;
    #   * both are read from the services.ollama options rather than hardcoded so they
    #     cannot drift from whatever the daemon actually uses.
    environment = {
      OLLAMA_HOST = "127.0.0.1:11434";
      HOME = config.services.ollama.home;                # /var/lib/ollama
      OLLAMA_MODELS = config.services.ollama.modelsDir;  # daemon's store
    };

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      # Same-user is NOT sufficient: the NixOS ollama module always sets DynamicUser=true,
      # so the store sits behind /var/lib/private/ollama (0700 root:root) and a bare
      # static-user unit cannot traverse it (EACCES on `ollama create`). Join the daemon's
      # namespace via DynamicUser + StateDirectory while KEEPING User/Group pinned, so the
      # shared StateDirectory ownership stays the daemon's uid instead of a per-unit one.
      User = config.services.ollama.user;
      Group = config.services.ollama.group;
      DynamicUser = true;
      StateDirectory = "ollama";
    };

    script = ''
      # Wait for the loopback API (the daemon may have only just started).
      for _ in $(seq 1 60); do
        if ${pkgs.curl}/bin/curl -sf "http://$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then
          break
        fi
        sleep 2
      done

      # Idempotent: a rebuild must be cheap and must not re-import over a good tag.
      if ollama list 2>/dev/null | ${pkgs.gnugrep}/bin/grep -q '^${loraTag}'; then
        echo "${loraTag} already present — nothing to do"
        exit 0
      fi

      # The base tag must exist first — the adapter is a layer on top, not a full model.
      if ! ollama list 2>/dev/null | ${pkgs.gnugrep}/bin/grep -q '^${baseTag}'; then
        echo "base tag ${baseTag} is absent; agos-seed-model must run first" >&2
        exit 1
      fi

      echo "creating ${loraTag} from ${baseTag} + adapter"
      ollama create ${loraTag} -f ${modelfile}
    '';
   };
  };

  # NOT set as OLLAMA_MODEL. Selecting the adapter is an explicit operator decision:
  #   OLLAMA_MODEL=qwen3.5:9b-agentos
  # Leaving the default alone means importing this module cannot change the behaviour the
  # batteries validated.
}
