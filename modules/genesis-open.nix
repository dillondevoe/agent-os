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
#                  @SH@             -> ${pkgs.bash}/bin/bash          (baked interpreter)
#                so its `LOCKED = not GENESIS_PATH.startswith("@")` flips true: it reads
#                the baked path and refuses anything not hashing to the baked value.
#                @SH@ is the same discipline pointed at the brain's HAND. `run_command` used
#                to reach `bash` by NAME; brain-home.service's explicit PATH carries no shell,
#                so every shell-out on a running deployment died `FileNotFoundError: 'bash'`
#                (measured 2026-09-05) — an errno raised before any process started, not an rc
#                a caller could read. Baking the store path means the built brain resolves no
#                name at all. tests/shell-resolve-battery.py arms both halves, D against a
#                real measured unit PATH.
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

  # The think budget, BAKED IN rather than inherited (2026-08-31). Same derivation discipline
  # as sessionOllamaEnv below: read from environment.variables, never re-typed, so the login
  # env and the script's default cannot say different things.
  #
  # This exists because inheritance FAILED. OLLAMA_THINK=off shipped in environment.variables
  # and never reached the TUI: brain-home.service is a `systemd --user` unit and systemd does
  # not source /etc/set-environment, so the box kept thinking for 81s a turn with the fix
  # deployed and every declaration-level check green. Note the launch context that broke it was
  # introduced by THIS lane, in baac2a3, which moved the TUI off hyprland's exec-once — the
  # comment fifteen lines below already said a systemd unit does not inherit this attrset, and
  # it was applied to the prewarm unit only.
  #
  # A missing value is a build error, not a default: silently omitting the substitution would
  # restore the model's own thinking-ON behaviour, which is invisible at runtime except as a
  # slow box, and "slow" is exactly how this defect hid for a day.
  thinkDefault =
    config.environment.variables.OLLAMA_THINK or (throw
      ("genesis-open: environment.variables.OLLAMA_THINK is unset, so agent-brain would be "
       + "built with no baked think budget and would fall back to the model's default (thinking "
       + "ON). On this CPU box that burns the entire num_predict budget on reasoning and returns "
       + "an EMPTY message. Set it in the variant's configuration."));

  agent-brain = pkgs.runCommand "agent-brain" { } ''
    mkdir -p "$out/bin" "$out/modules"

    # The modules agent-brain.py IMPORTS, installed beside it. Their absence is the defect
    # this block exists to close: the brain shipped `import spend_ceiling` (#243) and
    # `import providers` while the derivation copied exactly one file, so the gate code was
    # on the box and the module it needs was not. Both imports are wrapped in try/except by
    # design, so a missing module is SILENT — for providers it degrades, and for a CONFIGURED
    # spend ceiling it fail-closes to UNAVAILABLE, i.e. the budget cannot be armed at all.
    # Neither failure logs at build time and neither is visible until the day a credential
    # lands, which is exactly when a budget has to work.
    #
    # One copy, in $out/modules, symlinked into $out/bin. The brain resolves a bare import
    # from its own directory (sys.path[0]); bin/agent-os-budget resolves
    # dirname(dirname(__file__))/modules. Both land on the same file, so the two cannot drift.
    cp ${./spend_ceiling.py} "$out/modules/spend_ceiling.py"
    cp ${./providers.py}     "$out/modules/providers.py"
    ln -s ../modules/spend_ceiling.py "$out/bin/spend_ceiling.py"
    ln -s ../modules/providers.py     "$out/bin/providers.py"

    # The counter CLI the ceiling's own error text tells the operator to run
    # ("run `agent-os-budget init`"). Advice that names an absent command is not advice.
    cp ${../bin/agent-os-budget} "$out/bin/agent-os-budget"
    chmod +w "$out/bin/agent-os-budget"
    substituteInPlace "$out/bin/agent-os-budget" \
      --replace-fail '#!/usr/bin/env python3' '#!${brainPython}/bin/python3'
    chmod +x "$out/bin/agent-os-budget"

    cp ${./agent-brain.py} "$out/bin/agent-brain"
    chmod +w "$out/bin/agent-brain"
    substituteInPlace "$out/bin/agent-brain" \
      --replace-fail '#!/usr/bin/env python3' '#!${brainPython}/bin/python3' \
      --replace-fail '@GENESIS_PATH@'   '${genesis}/GENESIS.md' \
      --replace-fail '@GENESIS_SHA256@' '${genesisSha}' \
      --replace-fail '@THINK_DEFAULT@'  '${thinkDefault}' \
      --replace-fail '@SH@'             '${pkgs.bash}/bin/bash'
    chmod +x "$out/bin/agent-brain"
  '';
  # R1 (tier 0) — the SESSION's ollama env, read from the one attrset that defines it.
  #
  # `environment.variables` builds the LOGIN environment. A systemd unit does NOT inherit it.
  # The prewarm unit below therefore ran with OLLAMA_MODEL unset and fell through to
  # agent-brain.py's own `qwen3.5:9b` literal, while the session runs `qwen3.5:9b-agentos`
  # (base + LoRA — a different ollama tag). See the unit's comment for the two costs.
  #
  # Derived, never re-spelled: any OLLAMA_* the open config sets for the session reaches the
  # prewarm automatically, so the pair cannot drift. Re-typing the tag here would put the
  # single rule in two places, which is how it broke the first time.
  sessionOllamaEnv =
    lib.filterAttrs (n: _: lib.hasPrefix "OLLAMA_" n) config.environment.variables;

  # A silent fallback is the ENTIRE defect, so an unset session model is a build error rather
  # than a unit that starts, succeeds, and warms the wrong thing.
  _prewarmModelAsserted =
    if sessionOllamaEnv ? OLLAMA_MODEL then true
    else throw ("genesis-open: environment.variables.OLLAMA_MODEL is unset, so agos-boot-prewarm "
      + "would warm agent-brain's built-in default instead of the model this system actually runs. "
      + "That failure is invisible at runtime (the unit still exits 0), which is why it is refused "
      + "here at build time. Set the session's model tag in the variant's configuration.");

in {
  # The genesis-locked brain on the agent's PATH. Unique fingerprint for the
  # agentos-open-imports guard: `agent-brain` in systemPackages.
  environment.systemPackages = [ agent-brain ];

  # Exposed so a flake check can test the BUILT artifact — the script with @THINK_DEFAULT@
  # actually substituted — rather than the source, or the declaration. The gate this replaces
  # read environment.variables and the parser separately and pronounced them consistent; both
  # halves were green while the running brain had neither. Same reason the hyprland check reads
  # system.build.hyprlandConf instead of re-deriving the text.
  system.build.agentBrain = agent-brain;

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
    # THE MODEL THIS WARMS IS THE MODEL THE SESSION RUNS (R1, tier 0). Derived from
    # environment.variables above — see sessionOllamaEnv. Before this line the unit inherited
    # nothing, warmed agent-brain's `qwen3.5:9b` fallback, and left the session's
    # qwen3.5:9b-agentos slot as cold as if the unit had never run — while keep_alive=-1 kept
    # BOTH ~6.5GB models resident, which on a 21GB memory-bandwidth-bound box is the largest
    # single cause of the slowness this unit was written to fix.
    environment = lib.seq _prewarmModelAsserted sessionOllamaEnv;
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
