{
  description = "Agent OS — a computer whose shell is an agent, not a desktop";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";  # Dell Latitude = Intel x86_64
      # One module list, two machines: `agentos` ships UNSEALED (provisioning — the model
      # can still be pulled); `agentos-sealed` is the SAME machine with the clean-room
      # egress wall sealed to nixpkgs-only. The on-device seal is therefore a single
      # `nixos-rebuild switch --flake ...#agentos-sealed` AFTER the model pull — no local
      # file editing, no git push.
      baseModules = [
        ./configuration.nix
        ./modules/agent-shell.nix
        ./modules/brain.nix
        ./modules/clean-room.nix
        ./modules/audit.nix
        ./modules/taint.nix
        ./modules/mcp.nix
        ./modules/broker.nix
        ./modules/confirm.nix
        ./modules/seal-check.nix
        ./modules/break-glass.nix   # PR-A: the ONE interactive root door (tty3, password-gated)
        ./modules/system-set.nix    # PR-A: SCAFFOLD for the root-side system.set executor (impl in PR-J)
        ./modules/boot-branding.nix
      ];
      mkSystem = extraModules: nixpkgs.lib.nixosSystem {
        inherit system;
        modules = baseModules ++ extraModules;
      };
      # OPEN / MESHED variant (`agentos-open`, Dillon msg 8926). A deliberately
      # PERMISSIVE dev box — OpenSSH + Tailscale + full-power user + real bash shell
      # + ollama daemon, NO clean-room egress wall, NO agent-shell REPL, NO auto-pull.
      # Built from a SELF-CONTAINED base (configuration-open.nix) that shares ZERO
      # modules with the sovereign path above, so it can never perturb the sealed
      # surface. Rabbot meshes in over Tailscale/SSH and builds on the Dell live; the
      # box is sealed + repackaged for GitHub at the END, not here.
      openModules = [ ./configuration-open.nix ];
      mkOpenSystem = extraModules: nixpkgs.lib.nixosSystem {
        inherit system;
        modules = openModules ++ extraModules;
      };
    in {
      # The whole machine. `agent-shell.nix` is the part that makes it Agent OS;
      # `configuration.nix` is boring base plumbing (bootloader, user, network).
      nixosConfigurations.agentos = mkSystem [ ];
      # Same machine, egress wall sealed — the post-model-pull `switch` target.
      nixosConfigurations.agentos-sealed = mkSystem [ { agentos.cleanRoom.sealed = true; } ];

      # OPEN / MESHED dev variant — intentionally permissive (see `openModules`).
      # Install:  nixos-install --flake github:dillondevoe/agent-os#agentos-open
      # (install.sh VARIANT=agentos-open bakes the mesh authorized_keys + TS_AUTHKEY).
      nixosConfigurations.agentos-open = mkOpenSystem [ ];

      # Prove boot-and-talk in a VM BEFORE it ever touches the Dell:
      #   nix build .#vm && ./result/bin/run-*-vm       (unsealed: can pull a model)
      #   nix build .#vm-sealed                          (sealed: nixpkgs-only egress)
      packages.${system} = {
        vm        = self.nixosConfigurations.agentos.config.system.build.vm;
        vm-sealed = self.nixosConfigurations.agentos-sealed.config.system.build.vm;
        # Boot-sanity the open variant in a VM before it touches the Dell:
        #   nix build .#vm-open && ./result/bin/run-*-vm
        vm-open   = self.nixosConfigurations.agentos-open.config.system.build.vm;

        # seal-failloud fail-down loop, driven headless (nixosTest). Kept OUT of `checks`
        # so routine `nix flake check` stays fast — this boots a VM (minutes) vs the 6
        # property batteries (seconds). Run on demand:  nix build .#test-seal-faildown
        test-seal-faildown = import ./tests/seal-faildown.nix {
          pkgs = nixpkgs.legacyPackages.${system};
          inherit baseModules;
        };
      };

      checks.${system} = {
        # Phase 2 · Step 1 — evaluating the capability registry FORCES its invariant
        # assertions (mechanism 3 + INV-2 + schema). Any violation throws during eval,
        # so `nix flake check` fails to build this. That failure IS the test — a
        # configuration that breaks a security invariant does not evaluate.
        capability-registry =
          let reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
          in nixpkgs.legacyPackages.${system}.runCommand "capability-registry-check" { } ''
            echo "capability registry invariants hold (ok=${builtins.toJSON reg.ok})"
            echo "capabilities: ${nixpkgs.lib.concatStringsSep " " reg.capabilityNames}"
            touch $out
          '';

        # Phase 2 · Step 2 — the audit-log primitive's property battery. Proves
        # append-only NDJSON, SHA-256 chain tamper/truncation evidence, no-log->no-execute
        # fail-closed, hostile-newline single-line safety, and reserved-field forgery
        # stripping. A regression here fails `nix flake check`.
        audit-log =
          nixpkgs.legacyPackages.${system}.runCommand "audit-log-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              bash ${./tests/audit-battery.sh} ${./bin/audit} "$work"
              touch $out
            '';

        # Phase 2 · Step 3 — the taint tracker's property battery (SHADOW mode). Proves
        # the anti-laundering invariants: a monotonic set-only per-session bit, human-only
        # reset, UNTRUSTED-absorbing mem origin tags stored where the model can't write,
        # recall-of-untrusted re-taints across sessions, boot-taint on untrusted mem, and
        # fail-closed audit logging that gates NOTHING in v1. It drives the real audit
        # primitive, so a regression in either fails `nix flake check`.
        taint-shadow =
          nixpkgs.legacyPackages.${system}.runCommand "taint-shadow-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              bash ${./tests/taint-battery.sh} ${./bin/taint} ${./bin/audit} "$work"
              touch $out
            '';

        # Phase 2 · Step 4 — the MCP stdio front door's conformance + hostile-input
        # battery. Proves the parser invariants: one pinned protocol rev, three methods
        # only, and fail-closed on every unknown/malformed/oversized/duplicate-id/
        # duplicate-key/type-coerced/trailing-byte/unicode-trick input, with hard
        # size+depth caps and NO parser differential (one parser, one schema, no
        # coercion). The parser is pure (no privileged state), so this check needs no
        # scratch dirs beyond a workdir. A regression fails `nix flake check`.
        mcp-conformance =
          nixpkgs.legacyPackages.${system}.runCommand "mcp-conformance-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              bash ${./tests/mcp-battery.sh} ${./bin/mcp} "$work"
              touch $out
            '';

        # Phase 2 · Step 5 — the broker's property battery ("the wall"). Proves the 10-stage
        # fail-closed pipeline against the REAL materialized registry + the REAL taint/audit
        # primitives as children: the routing matrix (T0 ALLOW-AUTO, T1/T2 REQUIRE-CONFIRM),
        # T3 non-expressibility, per-type arg validation (path canonical+confined via shared
        # golden vectors, url INV-2 + obfuscated-IP evasion, namespace-under-root, recipient
        # charset, enum-denies-pre-GAP-1), verdict passthrough, single-flight + malformed-line
        # stream-shut, no-log->no-execute, the return-path taint-before-content ordering
        # (origin broker-derived not impl-reported, withhold-on-taint-fail, broker-owns-the-
        # stamp), the DATA fence, confirm epoch binding, and mcp-input-contract compatibility.
        # It materializes the registry with the SAME builtins.toJSON the broker module ships,
        # so a registry change that would break classification fails `nix flake check` here.
        broker-core =
          let
            reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
            pkgs = nixpkgs.legacyPackages.${system};
            registryJson = pkgs.writeText "registry.json" (builtins.toJSON reg.registry);
          in pkgs.runCommand "broker-core-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            work="$(mktemp -d)"
            bash ${./tests/broker-battery.sh} ${./bin/broker} ${./bin/taint} ${./bin/audit} \
              ${./bin/mcp} ${registryJson} "$work"
            touch $out
          '';

        # Phase 2 · Step 6 — the confirm channel ("the wall's mouth") property battery. Proves the
        # broker-owned confirm client (bin/confirm) is a fail-closed renderer/relayer/collector:
        # seam contract on both backends, no-channel/unreachable/unknown-name all deny (never
        # allow), nonce echo + domain-separated-HMAC auth (reflection/badmac/wrong-user/epoch each
        # caught), human-window + EOF timeouts, field-confusion/injection scrubbing with the `│ `
        # sentinel on every model-controlled value line while the trust region stays intact,
        # parse_mode-None transmitted frames, first-time-destination highlighting, preview
        # truncation, single-flight non-multiplexing, and a REAL-broker integration leg
        # (route->confirm->approve->invoke / route->confirm->deny->withhold) whose audit chain
        # still verifies and carries the nonce. It drives the real broker/taint/audit as children,
        # so a regression in any of them fails `nix flake check` here.
        confirm-channel =
          let
            reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
            pkgs = nixpkgs.legacyPackages.${system};
            registryJson = pkgs.writeText "registry.json" (builtins.toJSON reg.registry);
          in pkgs.runCommand "confirm-channel-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            work="$(mktemp -d)"
            bash ${./tests/confirm-battery.sh} ${./bin/confirm} ${./bin/broker} ${./bin/taint} \
              ${./bin/audit} ${registryJson} "$work"
            touch $out
          '';

        # Phase 4 (v0.2 A2) — the tool-calling loop's property battery. Drives the REAL
        # bin/agent-loop against a scripted fake Ollama (tests/ollama-stub.py) over the sandbox
        # loopback AND the REAL bin/mcp piped into a deterministic stub broker
        # (tests/broker-stub.py), and proves the loop MECHANICS: a plain answer skips tools; a
        # valid capability call round-trips THROUGH THE WALL (dispatch -> broker data_result fed
        # back as a role:"tool" message that preserves the content_type:"data" envelope -> final
        # answer), with the tool surface itself discovered through the wall via capabilities.list;
        # three broker denials in one user turn stop tool-calling and the final turn is taken with
        # NO tools offered; tool_calls emitted on that withheld final turn are NEVER dispatched;
        # and model-supplied terminal control bytes are scrubbed before the tty. agent-loop lives
        # on the UNTRUSTED side and makes zero security decisions (the stub replaces the broker's
        # policy decision, not the loop's behavior), so this is loop correctness, not a wall
        # policy test — mcp and broker carry their own batteries. A regression fails `nix flake check`.
        agent-loop =
          nixpkgs.legacyPackages.${system}.runCommand "agent-loop-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              bash ${./tests/agent-loop-battery.sh} ${./bin/agent-loop} ${./tests/ollama-stub.py} \
                ${./bin/mcp} ${./tests/broker-stub.py} "$work"
              touch $out
            '';

        # Phase 2 · Step 7 — the capability seam's property battery. Proves the invoke-seam
        # DISPATCHER (bin/cap-invoke) is a thin, fail-closed resolver against the REAL materialized
        # registry: capability->impl resolution, the {ok,content,meta:{key}} contract, the
        # exit-code bright line (impl exit 0 + valid object forwarded — incl. ok:false error bodies
        # that must reach the taint fence; impl crash / garbage / cap-bin-dir-unset / path-escaping
        # impl name all fail closed with no forwarded bytes), and normalization that strips any
        # impl-reported origin / extra meta down to identity. It also drives the first impl
        # (bin/cap-capabilities-list, T0). A regression in either fails `nix flake check`.
        capabilities =
          let
            reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
            pkgs = nixpkgs.legacyPackages.${system};
            registryJson = pkgs.writeText "registry.json" (builtins.toJSON reg.registry);
          in pkgs.runCommand "capabilities-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            work="$(mktemp -d)"
            bash ${./tests/cap-battery.sh} ${./bin/cap-invoke} ${./bin/cap-capabilities-list} \
              ${registryJson} "$work"
            touch $out
          '';

        # Phase 2 · A2 — the mem.* capability IMPLS' property battery. `capabilities` above proves the
        # DISPATCHER (cap-invoke); this proves the two impls BEHIND mem.remember / mem.recall via
        # DIRECT-invoke with AGENT_OS_MEM_ROOT=<scratch> — the DESIGNED test override (cap-mem-remember:41).
        # The seam strips AGENT_OS_MEM_ROOT (impl env = {PATH, AGENT_OS_REGISTRY}), so a through-seam write
        # lands at the hardcoded /var/lib/agent-os/mem, which a non-root check-derivation cannot create;
        # the direct path is the only in-sandbox home for the write round-trip. Covers PIN-2 byte-identity
        # (no trailing-newline hinge), PIN-A per-entry content-hash binding, content-addressed idempotency,
        # multi-hit score-ordering + MAX_HITS ceiling, the key-grammar fence (nothing written on an illegal
        # namespace), and fail-closed DROP of oversized / non-utf8 / illegal-slug files. The through-the-WALL
        # write->read E2E (real /var, boot-wired seam, root) is a nixosTest-VM forward-obligation on
        # task-279, deliberately NOT built here (a check-derivation cannot write /var). Regression -> RED.
        mem-cap =
          nixpkgs.legacyPackages.${system}.runCommand "mem-cap-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
            work="$(mktemp -d)"
            bash ${./tests/mem-cap-battery.sh} ${./bin/cap-mem-remember} ${./bin/cap-mem-recall} "$work"
            touch $out
          '';

        # Phase 2 · Step 7 (go-live) — the WIRED invoke-seam END-TO-END regression guard. Drives the
        # REAL broker through the REAL store-pinned cap-invoke DISPATCHER + the patchShebangs'd
        # capabilities.list impl (the EXACT artifacts modules/broker.nix now pins into production via
        # cap-invoke-pkg.nix) and asserts a real `tools/call capabilities.list` returns blessed,
        # DATA-fenced content AND leaves the session taint CLEAN — i.e. ORIGIN_BY_CAP[capabilities.
        # list] = TRUSTED holds END-TO-END (go-live gate #1). Neither broker-core (scripted fake seam,
        # UNTRUSTED caps only) nor capabilities (dispatcher driven directly, no broker) exercises this
        # path — this is the ONLY check that proves the wired seam does not spuriously taint a list.
        # A regression fails `nix flake check` (verify under `--option sandbox true`).
        seam-live =
          let
            reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
            pkgs = nixpkgs.legacyPackages.${system};
            registryJson = pkgs.writeText "registry.json" (builtins.toJSON reg.registry);
            capInvoke = import ./modules/cap-invoke-pkg.nix { inherit pkgs; };
          in pkgs.runCommand "seam-live-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            work="$(mktemp -d)"
            bash ${./tests/seam-live-battery.sh} ${./bin/broker} ${./bin/taint} ${./bin/audit} \
              ${capInvoke.wrapper}/bin/cap-invoke ${capInvoke.capBinDir}/bin ${registryJson} "$work"
            touch $out
          '';

        # PR-A follow-up — the CI-toplevel-build gap ("the real fix"). `nix flake check`
        # EVALUATES the nixosConfigurations but never BUILDS their `system.build.toplevel`, so
        # the nftables `checkRuleset` (`nft --check`) — the ONLY thing that catches a bad egress
        # rule — never ran in CI. That is exactly how the clean-room DHCP rule shipped
        # `agentos-sealed` RED from #21 to #27 (nft 1.0.9 rejects `udp sport X dport Y`, a single
        # `udp` shared across a sport+dport pair, with "No symbol type information"; see
        # modules/clean-room.nix). These two checks close the gap CHEAPLY: each forces the nftables
        # module's OWN `rulesScript` derivation to build (it is one element of the nftables service
        # ExecStart), and that drv's checkPhase runs the EXACT lkl-hijacked `nft --check` the box
        # runs at `nixos-rebuild` (nixpkgs nftables.nix checkPhase, LD_PRELOAD liblkl-hijack). We
        # reuse the module's own check verbatim — no reconstruction, no drift. Referencing the
        # ExecStart store paths in the build script pulls in ONLY that drv (nft + lkl + our
        # ruleset), NOT the whole system toplevel — so `nix flake check` stays fast, matching the
        # design that deliberately holds the seal-faildown VM out of `checks` (packages.${system}
        # comment above). We gate BOTH machines: -sealed (the shipped seal) AND -unsealed (whose
        # provisioning accepts differ, so its ruleset is a distinct nft parse). A re-simplification
        # of any egress rule now fails HERE, in CI — not silently at `nixos-rebuild` on the Dell.
        # GUARD-OF-THE-GUARD (Fable APPROVE_WITH_CAVEAT, #28): both checks force the module's
        # rulesScript drv to build, but that drv only actually runs `nft --check` when the module's
        # `checkRuleset` is true (nixpkgs nftables.nix — checkPhase is `lib.optionalString
        # cfg.checkRuleset`). If anyone ever set `networking.nftables.checkRuleset = false` the
        # checkPhase would go EMPTY and both of these would go green-while-unvalidated — the single
        # way this whole PR can be silently defeated. Default is true and there's no override in-repo,
        # but for a guard whose entire job is "the wall can't ship red," a comment is weaker than it
        # deserves — so each check eval-time ASSERTS its config's checkRuleset before building.
        nft-ruleset-sealed =
          assert nixpkgs.lib.assertMsg
            self.nixosConfigurations.agentos-sealed.config.networking.nftables.checkRuleset
            "nft-ruleset-sealed would false-PASS: networking.nftables.checkRuleset is false";
          nixpkgs.legacyPackages.${system}.runCommand "nft-ruleset-sealed-check" { } ''
            echo "agentos-sealed nftables ruleset passed nft --check via: ${
              nixpkgs.lib.concatStringsSep " "
                self.nixosConfigurations.agentos-sealed.config.systemd.services.nftables.serviceConfig.ExecStart
            }"
            touch $out
          '';

        nft-ruleset-unsealed =
          assert nixpkgs.lib.assertMsg
            self.nixosConfigurations.agentos.config.networking.nftables.checkRuleset
            "nft-ruleset-unsealed would false-PASS: networking.nftables.checkRuleset is false";
          nixpkgs.legacyPackages.${system}.runCommand "nft-ruleset-unsealed-check" { } ''
            echo "agentos (unsealed) nftables ruleset passed nft --check via: ${
              nixpkgs.lib.concatStringsSep " "
                self.nixosConfigurations.agentos.config.systemd.services.nftables.serviceConfig.ExecStart
            }"
            touch $out
          '';

        # Phase 2 (OPEN lane) — the DROPPED-IMPORT guard ("imports can't ship green").
        # SCAR (#42 -> fixed #44): a rebase resolved configuration-open.nix's `imports = [ … ]`
        # conflict by DELETING the whole block. Every Phase-2 module is self-contained, so with
        # nothing importing them the agentos-open toplevel still EVALUATED and BUILT clean — it just
        # silently shipped an image with no calendar, no desktop, no settings, and no in-image brain.
        # `nix flake check` never builds the open toplevel (the SAME gap the nft checks above close
        # for the sealed egress rule), so it read as merged+green.
        #
        # WHY A MARKER-ASSERT, NOT A TOPLEVEL BUILD (Rabbot's choice-of-form to me): building
        # `agentos-open.config.system.build.toplevel` is the strongest guard but REALIZES the ~4.68GB
        # qwen2.5 model FOD (modules/model-open.nix) on every CI run — bandwidth-hostile for a public
        # repo. This asserts, at EVAL time (zero realization, DVo-cheap), that each of the fourteen
        # bundled modules left its unique fingerprint in the built config. Drop any import and its
        # marker goes unset -> this throws, naming the module. Same dropped-import class, caught for
        # free. (Upgrade to a full toplevel build later if a fetch-capable CI ever wants belt+braces.)
        #
        # GUARD-OF-THE-GUARD: each marker must stay UNIQUE to exactly one module (a fingerprint no
        # other module sets), or a dropped import could be masked by an unrelated one. Today:
        #   calendar-open -> `agos-cal` CLI in systemPackages   (the agent's read/write calendar hand)
        #   desktop-open  -> programs.hyprland.enable           (the reproducible compositor)
        #   settings-open -> `agos-sys` CLI in systemPackages   (the agent's settings hand)
        #   model-open    -> systemd service `agos-seed-model`  (the in-image brain, Dillon 8988)
        #   genesis-open  -> `agent-brain` in systemPackages    (the genesis-locked soul-reading brain)
        #   calculator-open -> `agos-calc` CLI in systemPackages (the agent's calculator hand)
        #   files-open    -> `agos-files` CLI in systemPackages (the agent's read-only files hand)
        #   email-open    -> `thunderbird` in systemPackages     (human mail GUI; agent hand = MCP, escalated)
        #   mail-secret-open -> systemd service `agos-mail-token-preflight` (the (A) MCP-email token scaffold,
        #                     agent-side of email; NOT a new dozen app — Rabbot RULING-A)
        #   notes-open    -> `agos-notes` CLI in systemPackages (the agent's notes read/write hand)
        #   docs-open     -> `agos-doc` CLI in systemPackages  (the agent's read-only PDF-content hand)
        #   media-open    -> `agos-media` CLI in systemPackages (the agent's read-only image/AV probe hand)
        #   web-open      -> `agos-web` CLI in systemPackages  (the agent's read-only web-content hand;
        #                     human half = firefox. Browser AUTOMATION is a SEPARATE later increment.)
        #   mail-proton-bridge-open -> systemd service `agos-mail-proton-preflight` (Proton-via-Bridge
        #                     cred scaffold, the "both" parallel to Gmail; NOT a new dozen app)
        agentos-open-imports =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            lib  = nixpkgs.lib;
            cfg  = self.nixosConfigurations.agentos-open.config;
            hasPkg = n: lib.any (p: lib.hasInfix n (p.name or "")) cfg.environment.systemPackages;
          in
            assert lib.assertMsg (hasPkg "agos-cal")
              "agentos-open-imports: calendar-open.nix is NOT imported — agos-cal missing from systemPackages (dropped imports block? see #42/#44).";
            assert lib.assertMsg (hasPkg "gnome-calendar" && cfg.services.radicale.enable)
              "agentos-open-imports: calendar-open.nix GUI half is missing — gnome-calendar absent from systemPackages or services.radicale disabled (the loopback CalDAV that shares the agent vdir with the GUI window; see calendar-GUI-bump go 9047).";
            assert lib.assertMsg cfg.programs.hyprland.enable
              "agentos-open-imports: desktop-open.nix is NOT imported — programs.hyprland.enable is false.";
            assert lib.assertMsg (hasPkg "agos-sys")
              "agentos-open-imports: settings-open.nix is NOT imported — agos-sys missing from systemPackages.";
            assert lib.assertMsg (builtins.hasAttr "agos-seed-model" cfg.systemd.services)
              "agentos-open-imports: model-open.nix is NOT imported — the in-image brain (agos-seed-model, Dillon 8988) is absent.";
            assert lib.assertMsg (hasPkg "agent-brain")
              "agentos-open-imports: genesis-open.nix is NOT imported — agent-brain (the genesis-locked soul-reading brain) missing from systemPackages.";
            assert lib.assertMsg (hasPkg "agos-calc")
              "agentos-open-imports: calculator-open.nix is NOT imported — agos-calc missing from systemPackages.";
            assert lib.assertMsg (hasPkg "agos-files")
              "agentos-open-imports: files-open.nix is NOT imported — agos-files missing from systemPackages.";
            assert lib.assertMsg (hasPkg "thunderbird")
              "agentos-open-imports: email-open.nix is NOT imported — thunderbird (the human mail GUI) missing from systemPackages.";
            assert lib.assertMsg (builtins.hasAttr "agos-mail-token-preflight" cfg.systemd.services)
              "agentos-open-imports: mail-secret-open.nix is NOT imported — the (A) MCP-email token scaffold (agos-mail-token-preflight) is absent.";
            assert lib.assertMsg (hasPkg "agos-notes")
              "agentos-open-imports: notes-open.nix is NOT imported — agos-notes missing from systemPackages.";
            assert lib.assertMsg (hasPkg "agos-doc")
              "agentos-open-imports: docs-open.nix is NOT imported — agos-doc missing from systemPackages.";
            assert lib.assertMsg (hasPkg "agos-media")
              "agentos-open-imports: media-open.nix is NOT imported — agos-media missing from systemPackages.";
            assert lib.assertMsg (hasPkg "agos-web")
              "agentos-open-imports: web-open.nix is NOT imported — agos-web missing from systemPackages.";
            assert lib.assertMsg (builtins.hasAttr "agos-mail-proton-preflight" cfg.systemd.services)
              "agentos-open-imports: mail-proton-bridge-open.nix is NOT imported — the Proton-via-Bridge cred scaffold (agos-mail-proton-preflight) is absent.";
            pkgs.runCommand "agentos-open-imports-check" { } ''
              echo "agentos-open imports all fourteen Phase-2 modules: calendar(agos-cal+gnome-calendar/radicale) desktop(hyprland) settings(agos-sys) model(agos-seed-model) genesis(agent-brain) calculator(agos-calc) files(agos-files) email(thunderbird) mail-secret(agos-mail-token-preflight) notes(agos-notes) docs(agos-doc) media(agos-media) web(agos-web) mail-proton(agos-mail-proton-preflight)"
              touch $out
            '';

        # Orchestration engine · Phase 1 — the `agos-events` append-only event-log library's CONTRACT
        # BATTERY. Proves, against the MULTI-WRITER-across-SyncThing reality Geist ruled load-bearing
        # (per-(topic,machine) single-writer files; merge-read ordered by ts→machine→id; per-(consumer,
        # topic,machine) cursors): multi-writer exactly-once (no double/miss), replay-from-0, defer()
        # redelivery with NO done + head-of-line by writer (Geist PIN #1), deterministic ordering
        # (ts dominates, machine breaks ties), first-class done/await_done threaded by corr_id, and
        # routing on (topic, to) — NEVER by parsing payloads (Geist ruling #2). Zero external deps;
        # a regression fails `nix flake check`. (Phase-1 library; the shadow brain-comms migration
        # rides on top and is measured separately — Rabbot owns that before/after readout.)
        agos-events-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agos-events-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              cp ${./modules/agos_events.py} "$work/agos_events.py"
              cp ${./tests/agos-events-contract.py} "$work/contract.py"
              cd "$work"
              python3 contract.py
              touch $out
            '';

        # Orchestration engine · Phase 1 part 2 — the SHADOW brain-comms migration's CONTRACT BATTERY
        # (agos_comms_shadow, atop agos_events). Observes + parallel-emits, changes NO routing (the live
        # `_done/`+dispatch-watchers stay truth). Proves the migration is faithful BEFORE any cutover:
        # PORT PARITY — two INDEPENDENT route derivations agree on every comm (route_from_to(parse) ==
        # file_route(globs)); ROUTE TRUTH over every documented hazard (single/broadcast/the broadcast_gap
        # where to-all misses scout/phoenix/geist/rebound/combined air-<brain>/to-mesh orphan→nobody);
        # EMIT idempotency; DONE corr_id threading; EXACTLY-ONCE cursor'd delivery with the mtime-refire
        # double-fire structurally killed + orphan→no-brain; author-host machine partition in arrival
        # (mtime) order. A regression fails `nix flake check`.
        agos-comms-shadow-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agos-comms-shadow-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              cp ${./modules/agos_events.py} "$work/agos_events.py"
              cp ${./modules/agos_comms_shadow.py} "$work/agos_comms_shadow.py"
              cp ${./tests/agos-comms-shadow-contract.py} "$work/contract.py"
              cd "$work"
              python3 contract.py
              touch $out
            '';

        # Orchestration engine · Phase 1 CUTOVER — the LIVE routing layer's CONTRACT BATTERY
        # (agos_comms_live, atop agos_events + agos_comms_shadow). Where the shadow battery proves the
        # route DERIVATIONS are faithful, this proves the LIVE MECHANICS the cutover turns on: the
        # react-on-delta consumer (wake_pending fires exactly the NEW summonses routed to a brain, and a
        # re-run yields NOTHING — the mtime-refire double-fire structurally killed by the cursor), route
        # fidelity under the live path (broadcast gap, rebound, un-addressed brain), fresh-delta ordering,
        # emit idempotency, and Saga's read-only cohesion sweep (a request with no done reads UNRESOLVED,
        # clears when its done lands, and advances no cursor). A regression fails `nix flake check`.
        agos-comms-live-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agos-comms-live-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              cp ${./modules/agos_events.py} "$work/agos_events.py"
              cp ${./modules/agos_comms_shadow.py} "$work/agos_comms_shadow.py"
              cp ${./modules/agos_comms_live.py} "$work/agos_comms_live.py"
              cp ${./tests/agos-comms-live-contract.py} "$work/contract.py"
              cd "$work"
              python3 contract.py
              touch $out
            '';
      };
    };
}
