# Agent OS — OPEN variant: THE GENESIS LOCK (the soul, baked into the brain).
#
# WHY (GENESIS.md = the Dillon-approved soul doc, on main fa5c166; Geist's lock ruling
# "bind not bytes", geist-to-rabbot-genesis-spine-check-pass-lock-ruling-2026-07-31):
# store-read-only is NECESSARY-NOT-SUFFICIENT — the real attack is REPOINTING the reader.
# So the binding is BAKED AT BUILD, not runtime: the brain derivation carries the soul's
# content-hashed store PATH and its SHA256 as build-time LITERALS. No env var, no runtime
# symlink, no config key can repoint it. Changing the soul then requires rebuilding the
# brain — the doc's own "deliberate rebuild" carve. (broker.nix seam-pinning, applied to
# the soul.)
#
# HOW — two derivations:
#   genesis      GENESIS.md -> a content-hashed store dir. ${genesis}/GENESIS.md is the
#                immutable, reproducible, tamper-evident-by-construction soul path.
#   agent-brain  Rabbot's genesis-wired brain (Geist item 3: reads the soul FIRST,
#                hash-verifies it, _refuse()s loud on mismatch/missing, leads the system
#                prompt with it). We substitute its two BUILD-TIME placeholders:
#                  @GENESIS_PATH@   -> ${genesis}/GENESIS.md   (baked store path)
#                  @GENESIS_SHA256@ -> sha256 hex of that content (baked hash)
#                so its `LOCKED = not GENESIS_PATH.startswith("@")` flips true: it reads
#                the baked path and refuses anything not hashing to the baked value.
#
# HONEST CLAIM (never "untamperable"): no runtime edit/repoint path; tampering is loud;
# changing the soul is a rebuild-shaped act.
#
# ISOLATION: OPEN-only, same discipline as calendar/desktop/settings/model — self-contained,
# imports nothing sealed, can't perturb the sovereign surface.
#
# THE /etc COPY IS DEV-ALIGNMENT ONLY, NOT THE SECURITY PATH: environment.etc below places
# a read-only, byte-identical copy at the stable human path /etc/agent-os/GENESIS.md so a
# hand-deployed *dev* brain (unlocked, pre-rebuild) and humans can read the same soul. The
# LOCKED (nix-built) brain reads the baked ${genesis} store path and NEVER /etc — so /etc is
# an inert convenience, not a reader the lock depends on (Geist's "don't add a repointable
# reader" holds: the security-bearing reader is the baked literal).
#
# SEALED VARIANT: Phase-S folds the soul into the sovereign box's sealed integrity closure
# (Geist owns that); this module is the OPEN-lane packaging of the same binding.
{ config, pkgs, lib, ... }:

let
  # (1) The soul as a content-hashed store path. `cp` preserves GENESIS.md byte-for-byte,
  # so ${genesis}/GENESIS.md hashes to genesisSha below.
  genesis = pkgs.runCommand "agent-os-genesis" { } ''
    mkdir -p "$out"
    cp ${../GENESIS.md} "$out/GENESIS.md"
  '';

  # (2) The baked hash — sha256 hex of the exact soul bytes. builtins.hashFile hashes the
  # raw file; the brain hashes open(...,"utf-8").read().encode("utf-8"). For the LF-only,
  # BOM-free GENESIS.md on main these are identical (asserted at build by the agent-brain
  # smoke test in the ready-note). base16 lowercase == Python's hexdigest().
  genesisSha = builtins.hashFile "sha256" ../GENESIS.md;

  # (3) The genesis-wired brain with the two literals baked in. --replace-fail makes the
  # build ERROR if any placeholder is ever renamed away (no silent no-op — substitution is
  # guaranteed or the build fails loud). The interpreter is pinned to the store python3 via
  # an explicit shebang substitution (deterministic — no dependence on /usr/bin/env or the
  # runtime PATH). The script's own subprocess calls (hyprctl/agos-cal/firefox/…) still
  # resolve against the ambient system PATH by design — do NOT confine them here.
  # UX v2 slice 1: the brain's interpreter is now a python env carrying prompt_toolkit
  # (PromptSession input lock). Same pinned-shebang mechanism — the env's bin/python3
  # sees its site-packages, so no PYTHONPATH juggling. The brain degrades gracefully
  # (ImportError guard) if run under a bare python3 in dev.
  # Phase 1.5 slice 2 (K6, task 287): providers.py needs pyyaml to parse providers.yaml.
  # Without it here, agent-brain's provider-config import silently fails (caught by its
  # own ImportError guard) and every boot falls back to legacy OLLAMA_MODEL-only behavior
  # — the wiring would never actually activate on this build, just warn to stderr.
  brainPython = pkgs.python3.withPackages (ps: [ ps.prompt-toolkit ps.pyyaml ]);

  agent-brain = pkgs.runCommand "agent-brain" { } ''
    mkdir -p "$out/bin"
    cp ${./agent-brain.py} "$out/bin/agent-brain"
    chmod +w "$out/bin/agent-brain"
    substituteInPlace "$out/bin/agent-brain" \
      --replace-fail '#!/usr/bin/env python3' '#!${brainPython}/bin/python3' \
      --replace-fail '@GENESIS_PATH@'   '${genesis}/GENESIS.md' \
      --replace-fail '@GENESIS_SHA256@' '${genesisSha}'
    chmod +x "$out/bin/agent-brain"
  '';
in {
  # The genesis-locked brain on the agent's PATH. Unique fingerprint for the
  # agentos-open-imports guard: `agent-brain` in systemPackages.
  environment.systemPackages = [ agent-brain ];

  # DEV-ALIGNMENT copy (NOT the security path — see header). Read-only store symlink at a
  # stable human path, byte-identical to the baked soul.
  environment.etc."agent-os/GENESIS.md".source = ../GENESIS.md;

  # Boot-time prewarm (P1, rabbot-to-page-P1-boot-time-prewarm-unit-shave-cold-start-2026-
  # 08-02, Dillon msg 9277: "first thinking taking 3 minutes is too long :("). The in-process
  # warmup (agent-brain.py's warmup_greeting, PR #49/#50) only wins if boot→first-message
  # exceeds the cold prefill (~3min CPU) — Dillon types immediately, so his real turn queues
  # on CHAT_LOCK behind the warmup and still eats the full 3 minutes. Shave it by starting
  # the prefill AT POWER-ON instead: a oneshot that fires the SAME --once path (byte-
  # identical sysmsg() prefix -> same KV-cache slot) as soon as Ollama has the model seeded,
  # so the slot is hot before Hyprland even starts. keep_alive=-1 (agent-brain.py) keeps that
  # slot loaded after this unit exits; the in-process warmup stays as belt — it no-ops fast
  # against an already-hot slot. Not gated on boot (no `before` on the session) — this simply
  # runs as early as it can, in parallel.
  systemd.services.agos-boot-prewarm = {
    description = "Agent OS — prewarm the brain's KV cache at boot (shaves the first-message cold-start wait)";
    wantedBy = [ "multi-user.target" ];
    after = [ "ollama.service" "agos-seed-model.service" ];
    requires = [ "ollama.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      # HTTP-only against the loopback Ollama API — no ollama store access needed, so this
      # unit does NOT need agos-seed-model's DynamicUser+StateDirectory namespace join.
      DynamicUser = true;
    };
    script = ''
      ${agent-brain}/bin/agent-brain --once "boot warmup — reply with one word" >/dev/null 2>&1 || true
    '';
  };
}
