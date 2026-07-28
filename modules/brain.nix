# The local-model floor's daemon — what makes "talks with NO internet" real.
#
# bin/brain-ollama (the login shell's offline brain) talks HTTP to a local Ollama
# server. On NixOS the *daemon* must be declarative: /usr is immutable, so the
# upstream `curl | sh` ollama installer cannot work — it lives here instead. Model
# *weights* are runtime state (not a nix derivation), so they're pulled imperatively
# at first boot by bin/setup-brain.sh (`ollama pull`), per the roadmap's division.
{ config, pkgs, lib, ... }:

{
  services.ollama = {
    enable = true;
    # Loopback only — the brain is for THIS machine; nothing off-box should reach it.
    # (127.0.0.1:11434 is also the default brain-ollama / ollama-CLI endpoint.)
    host = "127.0.0.1";
    port = 11434;
    # No GPU accel set: the 5440's Intel Iris Xe isn't a stock ollama accel target, so
    # it runs on CPU — a 14B Q4 is comfortable in the 5440's 32GB. Set "cuda"/"rocm"
    # only on hardware that has it.
  };

  # Make the whole stack agree on ONE model WITHOUT editing the security-reviewed
  # shim: brain-ollama's built-in default is the 7B fast tier, but the 5440 runs the
  # 14.8B. Set it here (session env) and have setup-brain.sh pull exactly this tag, so
  # `brain-ollama --check` (>=1 model) AND the /api/chat model line agree — otherwise
  # --check could pass on the 14B while the shim silently defaults to an un-pulled 7B.
  environment.variables = {
    OLLAMA_HOST  = "http://127.0.0.1:11434";
    OLLAMA_MODEL = "qwen2.5:14b";   # 14.8B Q4 (~9GB) = DVo's `gwen:latest` weights
  };
}
