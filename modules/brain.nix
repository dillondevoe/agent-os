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
    OLLAMA_MODEL = "qwen3.5:9b";   # 9B (~6.6GB) agentic tier = v0.1 default brain
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
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      # Publish pull progress where the ROOTLESS agent-shell can READ it: a root-owned tmpfs
      # dir (0755, world-traversable) holding a 0644 status file. Preserve it across the timer's
      # failed→retry restarts so the login always has a line to show; /run is tmpfs so it still
      # clears on reboot. The agent only READS this file — no new privileged surface, no sudo.
      RuntimeDirectory = "agent-os";
      RuntimeDirectoryMode = "0755";
      RuntimeDirectoryPreserve = "yes";
    };
    script = ''
      OLLAMA=${config.services.ollama.package}/bin/ollama
      STATUS=/run/agent-os/pull-status
      # world-readable one-line progress the rootless agent-shell tails (atomic write, 0644).
      say() { printf '%s\n' "$*" > "$STATUS.tmp" 2>/dev/null && chmod 0644 "$STATUS.tmp" 2>/dev/null && mv -f "$STATUS.tmp" "$STATUS" 2>/dev/null || true; }

      # already have it? nothing to do (covers sealed + all later boots).
      if "$OLLAMA" list 2>/dev/null | grep -q 'qwen3.5:9b'; then
        say "model already present"; echo "agent-os: model already present"; exit 0
      fi
      # Wait for REAL connectivity — network-online.target can be reached before wifi has
      # fully associated/DHCP'd, which would fail the pull. Poll up to ~2min.
      # Probe with a tcp/443 connect (dep-free bash /dev/tcp), NOT ping: the clean-room
      # egress wall drops ICMP for every uid (root included), so ping never succeeds here
      # even when online — it would burn the full ~2min every first boot. Probe the MODEL HOST
      # itself (registry.ollama.ai:443) — the exact host+channel `ollama pull` fetches from — so a
      # passing probe means the pull can actually run, not merely that some other site is up.
      say "waiting for network..."
      for _i in $(seq 1 24); do
        timeout 4 ${pkgs.bash}/bin/bash -c ': >/dev/tcp/registry.ollama.ai/443' >/dev/null 2>&1 && break
        echo "agent-os: waiting for network... ($_i/24)"; say "waiting for network... ($_i/24)"; sleep 5
      done
      echo "agent-os: pulling qwen3.5:9b (~6.6GB, first boot only)..."
      say "starting download (~6.6GB, first boot only)..."
      # Stream the pull's progress into the status file: ollama redraws one progress line with
      # carriage returns, so split \r into \n and keep writing the LATEST line. Capture the pull's
      # REAL exit code out-of-band (a file, NOT the pipe) so the tee can't mask a failure:
      # NO `|| exit 0` — a real failure must leave the unit FAILED (retriable by the timer / next
      # reboot), never silently mark done and never pull ("wifi ate the install"). (If ollama emits
      # no interim progress when its stdout is a pipe, the status simply holds on "starting
      # download..." until completion — degraded but still correct; --check + the timer are unaffected.)
      _RCF=/run/agent-os/.pull-rc
      # `set +e` FIRST inside the subshell: it inherits the script's errexit, so a FAILING pull would
      # kill the subshell before `echo $?` runs and lose the real rc (_RCF missing → cat falls back to 1
      # — still FAILED so the invariant holds, but the "rc=$_rc" log would always lie "1"). With +e the
      # echo always records the true rc.
      ( set +e; "$OLLAMA" pull qwen3.5:9b 2>&1; echo $? > "$_RCF" ) | tr '\r' '\n' | while IFS= read -r _l; do
        # `|| true`: a trailing-empty tr'd line makes the [ -n ] test false → the && chain returns 1 → the
        # while (this pipeline's tail element) returns 1 → the script's `set -e` would spuriously mark a
        # GOOD pull FAILED. Force the body's rc to 0.
        [ -n "$_l" ] && say "$_l" || true
      done
      _rc="$(cat "$_RCF" 2>/dev/null || echo 1)"; rm -f "$_RCF"
      if [ "$_rc" = 0 ]; then
        say "download complete — starting the brain"; echo "agent-os: pull complete"
      else
        say "download failed — retrying automatically"; echo "agent-os: pull failed (rc=$_rc)"
      fi
      exit "$_rc"
    '';
  };

  # Self-healing retry for the first-boot brain pull (Rec A). On a wifi-late boot the
  # oneshot above gives up its ~2min tcp/443 wait before the network associates and ends
  # FAILED, so the model never lands. PR #21 made the agent user rootless, so agent-shell
  # can NO LONGER re-kick the pull (that dead `sudo systemctl restart` is removed there) —
  # the retry HAS to live root-side. This ROOT timer simply re-fires the ROOT oneshot above
  # until the model is present. It adds NO new privileged surface: it only retries a service
  # that already runs at boot, so nothing new becomes root-reachable and the agent stays
  # powerless (posture ratified: geist A-timer ruling, 2026-07-30).
  #
  # Self-quiescent, no explicit stop needed: on a successful pull the oneshot stays
  # `active (exited)` (RemainAfterExit = true above) and never deactivates, so
  # OnUnitInactiveSec below never elapses again and the timer goes idle on its own. It only
  # keeps firing while the oneshot sits FAILED (the wifi-late window). Cadence is
  # intentionally coarse — that window is minutes, not seconds, and nothing about the
  # posture depends on the number.
  #
  # CI blind spot: this lives on the boot/activation path, which `nix flake check` only
  # EVALUATES and `nix build .#vm` can't exercise (mkVMOverride swaps the boot surface).
  # Eval-green here means the unit text is well-formed, NOT that the wifi-late self-heal
  # fires — that is proven by a real first-boot smoke, not CI.
  systemd.timers.agent-os-pull-model = {
    description = "Agent OS — retry the first-boot brain pull while online, until the model lands";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      # Guaranteed floor: fire at least once after the boot oneshot's ~2min wait resolves,
      # even in the edge where the unit never reports an inactive timestamp.
      OnBootSec = "180s";
      # Workhorse: retry 60s after each FAILED pull. Goes idle once the pull succeeds (the
      # unit stays active-exited and never re-deactivates). Coarse on purpose.
      OnUnitInactiveSec = "60s";
    };
  };
}
