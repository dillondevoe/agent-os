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

  # Make the whole stack agree on ONE brain + ONE model WITHOUT editing the security-
  # reviewed shim. v0.1 (sovereign, CPU-only 5440) defaults to the snappy 7B-instruct
  # tier; the 14.8B stays PULLABLE as the judgment lane (`setup-brain.sh --model
  # qwen2.5:14b`). BRAIN is pinned to the local floor so the login shell NEVER selects a
  # cloud brain even if a `claude` CLI were ever present (belt-and-suspenders on the
  # no-cloud-path-in-v0.1 rule; none is installed on the image). setup-brain.sh pulls
  # exactly OLLAMA_MODEL, so `brain-ollama --check` (>=1 model) AND the /api/chat model
  # line agree — otherwise --check could pass on one tag while the shim defaults to an
  # un-pulled other.
  environment.variables = {
    BRAIN        = "brain-ollama";          # local floor IS the brain in v0.1 — no cloud path
    OLLAMA_HOST  = "http://127.0.0.1:11434";
    OLLAMA_MODEL = "qwen2.5:7b-instruct";   # 7B (~4.7GB) fast tier = v0.1 default brain
  };

  # First-boot brain bootstrap. The mem-REPL floor (agent-shell with no model) has no
  # shell escape to run `ollama pull` by hand — and a sharable image can't ask every
  # user to find a shell. So pull the weights automatically, once, on first boot while
  # egress is still open (the UNSEALED #agentos provisioning window). Idempotent: skips
  # if the model is already present (so it's a no-op on #agentos-sealed and every reboot
  # after). After it completes, one reboot (or re-login) and agent-shell finds a live
  # model → boots into the real brain instead of the memory floor.
  systemd.services.agent-os-pull-model = {
    description = "Agent OS — pull the local brain model on first boot (provisioning)";
    after    = [ "ollama.service" "network-online.target" ];
    wants    = [ "ollama.service" "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    environment.OLLAMA_HOST = "http://127.0.0.1:11434";
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
    script = ''
      OLLAMA=${config.services.ollama.package}/bin/ollama
      # already have it? nothing to do (covers sealed + all later boots).
      if "$OLLAMA" list 2>/dev/null | grep -q 'qwen2.5:7b-instruct'; then
        echo "agent-os: model already present"; exit 0
      fi
      echo "agent-os: pulling qwen2.5:7b-instruct (~4.7GB, first boot only)..."
      "$OLLAMA" pull qwen2.5:7b-instruct || {
        echo "agent-os: model pull failed (no egress? sealed too early?) — retry after network."; exit 0
      }
    '';
  };
}
